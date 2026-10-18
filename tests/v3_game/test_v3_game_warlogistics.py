"""CPU-only tests for original V3 military logistics reads and allocation."""
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from contextlib import ExitStack

from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.v3_game.military import logistics as warlogistics
from pdx_runtime.v3_game.military._warfare import WarfareRead
from pdx_runtime.v3_game.military import MilitaryAPI


TYPES = {
    "Country": 83,
    "MilitaryFormation": 37,
    "bool": 5,
    "uint32": 6,
    "int32": 4,
    "CString": 0,
    "CFixedPoint": 82,
}

EXPECTED_LEVELS = {
    "very_low": "VeryLow",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "very_high": "VeryHigh",
}
EXPECTED_NETWORK_METHODS = (
    "GetNumSupplyShips",
    "GetTotalSupplyShipCount",
    "GetNumAvailableSupplyShips",
    "GetNumAssignedSupplyShips",
    "GetNumRequiredSupplyShips",
    "GetNumAssignedSupplyShipsToArmies",
    "GetNumAssignedSupplyShipsToFleets",
    "GetNumRequiredSupplyShipsToArmies",
    "GetNumRequiredSupplyShipsToFleets",
    "GetSupplyShipsArmiesBreakdown",
    "GetSupplyShipsFleetsBreakdown",
    "GetSupplyNetworkStrength",
    "GetSupplyNetworkStrengthTarget",
    "GetSupplyNetworkStrengthWeeklyChange",
    "GetSupplyNetworkNumSeaNodes",
    "GetSupplyNetworkNumSeaNodesDesc",
    "GetDeficitSupplyShipRatioForProgressBar",
)
EXPECTED_COST_METHODS = (
    "GetSupplyConstructionCapacity",
    "GetFreeSupplyConstructionCapacity",
    "GetSupplyConstructionQueueUsage",
    "GetSupplyConstructionUsageTotal",
    "GetSupplyConstructionQueueCount",
    "GetMilitaryConstructionCapacity",
    "GetMilitaryConstructionUsageTotal",
    "GetMilitaryConstructionQueueUsage",
    "GetSupplyMaintenanceUsage",
    "GetSupplyMaintenanceFulfillment",
    "GetSupplyMaintenanceDemand",
    "GetCurrentSailors",
    "GetNeededSailors",
    "HasInsufficientMaxSailors",
    "GetSupplyShipConstructionGoodsExpenses",
    "GetSupplyShipMaintenanceExpenses",
    "GetMilitaryGoodsExpenses",
    "GetMilitaryGoodsExpensesByGoodsDesc",
)
EXPECTED_CAPACITY_METHODS = EXPECTED_COST_METHODS[:8]
EXPECTED_ATTRITION_METHODS = (
    "GetAttritionRaw",
    "GetAverageAttritionPerWeek",
    "GetAttritionRiskDesc",
    "HasLogisticsCenterGoodsShortage",
    "GetSupplyShortageRatio",
    "GetEffectiveSupplyDistance",
    "HasSupplyShortageOrganizationPenalty",
    "GetGoodsConsumptionCostRaw",
    "GetGoodsConsumptionDesc",
)


def function(name, result_type, arguments=0, *, action=False):
    return SimpleNamespace(
        name=name,
        argument_count=arguments,
        return_type_id=result_type,
        callbacks=(0, 0, 0, 123) if action else (123, 0, 0, 0),
    )


def type_for_kind(kind):
    return {
        data.Kind.BOOL: TYPES["bool"],
        data.Kind.I32: TYPES["int32"],
        data.Kind.U32: TYPES["uint32"],
        data.Kind.STRING: TYPES["CString"],
        data.Kind.FIXED: TYPES["CFixedPoint"],
    }[kind]


class FakeCommand:
    def __init__(self):
        self.command = 0x9000
        self.world = 77
        self.pending = None
        self.attempt = None
        self.calls = []

    def validate(self, capacity):
        self.calls.append(("validate", capacity))
        self.attempt = SimpleNamespace(outcome="owned")
        return SimpleNamespace(error=0, validated=1, reason="", command=self.command)

    def submit(self, flags):
        self.calls.append(("submit", flags))
        self.command = 0
        self.attempt = SimpleNamespace(outcome="consumed")
        return SimpleNamespace(error=0, accepted=1, command=0)

    def destroy(self):
        self.calls.append(("destroy", None))
        self.command = 0
        self.attempt = SimpleNamespace(outcome="consumed")
        return SimpleNamespace(error=0, command=0)


class LogisticsFixture:
    FORMATION_ID = 0x81000002

    def __init__(self):
        self.native = object()
        self.process = object()
        self.world = 77
        self.adapters = {"data": "fake", "commands": "fake"}
        self.diagnostics = []
        self.queries = []
        self.expressions = []
        self.releases = []
        self.handles = {}
        self.next_handle = 100
        self.refresh_count = 0
        self.native_errors = {}
        self.native_partial_errors = {}
        self.unknown_expression = None
        self.producer_expressions = []
        self.command_value_calls = []
        self.clone_calls = []
        self.owner = FakeCommand()
        self.armies = [self.FORMATION_ID]
        self.fleets = []

        self.fields = {"Country": {}, "MilitaryFormation": {}}
        self.values = {"Country": {}, "MilitaryFormation": {}}
        self.kinds = {"Country": {}, "MilitaryFormation": {}}
        self._install_country_fields()
        self._install_formation_fields()

    def _add(self, named_type, method, kind, value, *, arguments=0, action=False):
        self.fields[named_type][method] = function(
            method, type_for_kind(kind), arguments, action=action
        )
        self.values[named_type][method] = value
        self.kinds[named_type][method] = kind

    def _install_country_fields(self):
        for method, key, kind in (*warlogistics._NETWORK, *warlogistics._COSTS):
            if method in self.fields["Country"]:
                continue
            if kind == data.Kind.I32:
                value = 0 if method in ("GetNumSupplyShips", "GetCurrentSailors") else 7
            elif kind == data.Kind.BOOL:
                value = False
            elif kind == data.Kind.STRING:
                value = "Native " + key.replace("_", " ")
            elif kind == data.Kind.FIXED:
                fixed = {
                    "GetSupplyNetworkStrength": 75000,
                    "GetSupplyNetworkStrengthTarget": 100000,
                    "GetSupplyNetworkStrengthWeeklyChange": 0,
                    "GetDeficitSupplyShipRatioForProgressBar": 25000,
                    "GetSupplyConstructionCapacity": 800000,
                    "GetFreeSupplyConstructionCapacity": 300000,
                    "GetSupplyConstructionQueueUsage": 500000,
                    "GetSupplyConstructionUsageTotal": 500000,
                    "GetMilitaryConstructionCapacity": 400000,
                    "GetMilitaryConstructionUsageTotal": 200000,
                    "GetMilitaryConstructionQueueUsage": 200000,
                    "GetMilitaryGoodsExpenses": 0,
                }.get(method, 125000)
                value = data.FixedPoint(fixed)
            else:
                raise AssertionError(kind)
            self._add("Country", method, kind, value)
        for suffix in EXPECTED_LEVELS.values():
            method = "GetSupplyShipConstructionLevelDesc" + suffix
            self._add("Country", method, data.Kind.STRING, "Allocation " + suffix)
            producer = "SetSupplyShipConstructionLevel" + suffix
            self.fields["Country"][producer] = function(producer, 0, action=True)
        self.fields["Country"]["GetMilitaryFormationsArmy"] = function(
            "GetMilitaryFormationsArmy", 70
        )
        self.fields["Country"]["GetMilitaryFormationsFleet"] = function(
            "GetMilitaryFormationsFleet", 70
        )

    def _install_formation_fields(self):
        self._add("MilitaryFormation", "GetID", data.Kind.U32, self.FORMATION_ID)
        values = {
            "GetAttritionRaw": (data.Kind.FIXED, data.FixedPoint(0)),
            "GetAverageAttritionPerWeek": (data.Kind.FIXED, data.FixedPoint(2500)),
            "GetAttritionRiskDesc": (data.Kind.STRING, "Low attrition risk"),
            "HasLogisticsCenterGoodsShortage": (data.Kind.BOOL, False),
            "GetSupplyShortageRatio": (data.Kind.FIXED, data.FixedPoint(0)),
            "GetEffectiveSupplyDistance": (data.Kind.FIXED, data.FixedPoint(450000)),
            "HasSupplyShortageOrganizationPenalty": (data.Kind.BOOL, False),
            "GetGoodsConsumptionCostRaw": (data.Kind.FIXED, data.FixedPoint(175000)),
            "GetGoodsConsumptionDesc": (data.Kind.STRING, "Goods consumption"),
        }
        for method, (kind, value) in values.items():
            self._add("MilitaryFormation", method, kind, value)

    async def query(self, label, factory, **kwargs):
        self.queries.append(label)
        return factory()

    async def country_id(self):
        return 156

    async def type_id(self, name):
        return TYPES[name]

    async def refresh(self):
        self.refresh_count += 1
        return SimpleNamespace(paused=True)

    async def perform(self, label, operation):
        return await operation()

    def result(self, action, *, error=0, value=None, kind=data.Kind.EMPTY,
               type_id=0, handle=0, total=None, native_type_name=""):
        raw = value.encode("utf-8") if isinstance(value, str) else b""
        return SimpleNamespace(
            action=action,
            error=error,
            value=value,
            kind=kind,
            type_id=type_id,
            native_type_name=native_type_name,
            value_handle=handle,
            total=len(raw) if total is None else total,
            snapshot_bytes=len(raw),
            snapshot=raw,
        )

    def retain(self, content):
        self.next_handle += 1
        self.handles[self.next_handle] = content
        return self.next_handle

    def _scalar(self, named_type, method):
        return self.kinds[named_type][method], self.values[named_type][method]

    def evaluate(self, native, expression, bindings, **kwargs):
        self.expressions.append(expression)
        if expression == self.unknown_expression:
            raise RuntimeError("original post-read outcome unknown")
        if expression in self.native_partial_errors:
            prefix, total = self.native_partial_errors[expression]
            return self.result(
                data.Action.EVALUATE,
                error=122,
                value=prefix,
                kind=data.Kind.STRING,
                type_id=TYPES["CString"],
                total=total,
            )
        if expression in self.native_errors:
            return self.result(data.Action.EVALUATE, error=self.native_errors[expression])
        if expression in (
            "Country.GetMilitaryFormationsArmy",
            "Country.GetMilitaryFormationsFleet",
        ):
            entries = self.armies if expression.endswith("Army") else self.fleets
            handle = self.retain(("list", "MilitaryFormation", list(entries)))
            return self.result(
                data.Action.EVALUATE,
                value=data.NativeList(handle, 0x3000, b"", "native list"),
                kind=data.Kind.LIST,
                handle=handle,
            )
        if expression.startswith("Country.SetSupplyShipConstructionLevel"):
            self.producer_expressions.append(expression)
            handle = self.retain(("command_source", expression))
            carrier = data.NativeDataCommand(
                0x4000,
                0x5000,
                0x4018,
                handle,
                ".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@",
                self.world,
            )
            return self.result(
                data.Action.EVALUATE,
                value=carrier,
                kind=data.Kind.OBJECT,
                handle=handle,
            )
        if expression.startswith("Country."):
            named_type = "Country"
            method = expression.removeprefix("Country.").split("(")[0]
        elif expression.startswith("MilitaryFormation."):
            named_type = "MilitaryFormation"
            method = expression.removeprefix("MilitaryFormation.").split("(")[0]
            if not bindings[0].retained:
                raise AssertionError("formation scalar must use the retained owned wrapper")
        else:
            raise AssertionError(f"unexpected expression: {expression}")
        kind, value = self._scalar(named_type, method)
        return self.result(
            data.Action.EVALUATE,
            value=value,
            kind=kind,
            type_id=type_for_kind(kind),
        )

    def list_count(self, native, handle, **kwargs):
        return SimpleNamespace(error=0, total=len(self.handles[handle][2]))

    def list_element(self, native, handle, index, **kwargs):
        _, name, entries = self.handles[handle]
        if name != "MilitaryFormation":
            raise AssertionError("only the owned formation list may use list_element")
        child = self.retain(("object", name, entries[index]))
        native_name = ".?AU?$SAnyType@V?$CPdxPtr@VCDataMilitaryFormation@@@@@PdxAnyImpl@@"
        return self.result(
            data.Action.LIST_ELEMENT,
            value=data.NativeObject(0x1000 + child, TYPES[name], child, native_name),
            kind=data.Kind.OBJECT,
            type_id=TYPES[name],
            handle=child,
            native_type_name=native_name,
        )

    def functions(self, native, type_id, **kwargs):
        name = next(name for name in ("Country", "MilitaryFormation") if TYPES[name] == type_id)
        rows = list(self.fields[name].values())
        return SimpleNamespace(error=0, rows=rows, total=len(rows), next=len(rows))

    def release(self, native, handle, **kwargs):
        self.releases.append(handle)
        del self.handles[handle]
        return SimpleNamespace(error=0, value_handle=0)

    def command_value(self, native, handle, **kwargs):
        self.command_value_calls.append(handle)
        content = self.handles[handle]
        if content[0] != "command_source":
            raise AssertionError("command carrier must come from the original retained producer")
        carrier = data.NativeDataCommand(
            0x4000,
            0x5000,
            0x4018,
            handle,
            ".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@",
            self.world,
        )
        return self.result(data.Action.COMMAND_VALUE, value=carrier, kind=data.Kind.OBJECT)

    def command_request(self, native, action, **kwargs):
        self.clone_calls.append((action, kwargs))
        if action != commands.Action.CLONE:
            raise AssertionError("only the original producer command may be cloned")
        if kwargs.get("command") != 0x4000 or kwargs.get("world") != self.world:
            raise AssertionError("clone must use the original carrier pointer and world")
        return SimpleNamespace(
            error=0,
            action=commands.Action.CLONE,
            command=0,
            cloned_command=self.owner.command,
            type_token=901,
            world=self.world,
        )

    def patches(self):
        stack = ExitStack()
        stack.enter_context(patch.multiple(
            data,
            evaluate=self.evaluate,
            list_count=self.list_count,
            list_element=self.list_element,
            enumerate_functions=self.functions,
            release=self.release,
            command_value=self.command_value,
        ))
        stack.enter_context(patch.object(commands, "request", self.command_request))
        stack.enter_context(patch.object(
            commands.NativeCommand,
            "from_result",
            return_value=self.owner,
        ))
        return stack


class LogisticsReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_network_and_costs_preserve_fixed_zero_false_and_native_partials(self):
        ctx = LogisticsFixture()
        api = MilitaryAPI(ctx)
        ctx.native_errors["Country.GetSupplyNetworkNumSeaNodes"] = 21
        ctx.native_partial_errors["Country.GetSupplyShipsFleetsBreakdown"] = (
            "Fleet su",
            len("Fleet supply breakdown"),
        )
        ctx.native_partial_errors["Country.GetMilitaryGoodsExpensesByGoodsDesc"] = (
            "Military go",
            len("Military goods cost description"),
        )
        with ctx.patches():
            observed = await api.logistics.read()
            network = observed["network"]
            costs = observed["costs"]

        self.assertEqual(network["status"], "observed")
        self.assertEqual(network["supply_ships"], 0)
        self.assertEqual(network["network_strength"], Decimal("0.75000"))
        self.assertEqual(network["network_strength_weekly_change"], Decimal("0.00000"))
        self.assertIsNone(network["sea_node_count"])
        self.assertIn("native error 21", network["unavailable"]["sea_node_count"])
        self.assertEqual(network["fleet_supply_description"], "Fleet su")
        self.assertIn("native error 122", network["unavailable"]["fleet_supply_description"])
        self.assertIn("partial", network["unavailable"]["fleet_supply_description"])

        self.assertEqual(costs["status"], "observed")
        self.assertEqual(costs["current_sailors"], 0)
        self.assertFalse(costs["insufficient_max_sailors"])
        self.assertEqual(costs["military_goods_expenses"], Decimal("0.00000"))
        self.assertEqual(costs["military_goods_cost_description"], "Military go")
        self.assertIn("partial", costs["unavailable"]["military_goods_cost_description"])
        self.assertEqual(ctx.refresh_count, 2)
        self.assertFalse(ctx.handles)
        reads = [entry for entry in ctx.diagnostics if isinstance(entry, WarfareRead)]
        self.assertEqual(len(reads), 2)
        partial_receipts = [value for run in reads for _, value in run.operations
                            if getattr(value, "error", 0) == 122]
        self.assertEqual([receipt.value for receipt in partial_receipts], ["Fleet su", "Military go"])

    async def test_fixed_specs_use_current_metadata_and_missing_field_is_explicit(self):
        ctx = LogisticsFixture()
        api = MilitaryAPI(ctx)
        del ctx.fields["Country"]["GetNumAvailableSupplyShips"]
        with ctx.patches():
            result = (await api.logistics.read())["network"]

        self.assertNotIn("Country.GetNumAvailableSupplyShips", ctx.expressions)
        self.assertIn("Country.GetSupplyNetworkStrength", ctx.expressions)
        self.assertIsNone(result["available_supply_ships"])
        self.assertIn("current metadata has no pure", result["unavailable"]["available_supply_ships"])
        self.assertFalse(ctx.handles)

    async def test_allocation_options_are_five_original_unvalidated_levels(self):
        ctx = LogisticsFixture()
        api = MilitaryAPI(ctx)
        with ctx.patches():
            result = await api.logistics.allocation()

        self.assertEqual(result["status"], "observed")
        self.assertEqual([row["level"] for row in result["levels"]], list(EXPECTED_LEVELS))
        self.assertEqual(
            [row["description"] for row in result["levels"]],
            ["Allocation " + suffix for suffix in EXPECTED_LEVELS.values()],
        )
        self.assertTrue(all(row["selected"] is None for row in result["levels"]))
        self.assertTrue(all(row["eligible"] is None for row in result["levels"]))
        self.assertIsNone(result["selected_level"])
        self.assertIn("no dedicated selected", result["selected_level_unavailable_reason"])
        self.assertEqual(
            result["capacity_observation"]["supply_construction_capacity"],
            Decimal("8.00000"),
        )
        self.assertEqual(
            result["capacity_observation"]["free_supply_construction_capacity"],
            Decimal("3.00000"),
        )
        expected = [
            "Country.GetSupplyShipConstructionLevelDesc" + suffix
            for suffix in EXPECTED_LEVELS.values()
        ] + ["Country." + method for method in EXPECTED_CAPACITY_METHODS]
        self.assertEqual(ctx.expressions, expected)
        self.assertFalse(ctx.producer_expressions)
        self.assertFalse(ctx.command_value_calls)
        self.assertFalse(ctx.clone_calls)
        self.assertFalse(ctx.owner.calls)
        self.assertFalse(ctx.handles)


class AllocationCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_level_uses_only_its_original_producer_once_and_reads_capacity(self):
        for level, suffix in EXPECTED_LEVELS.items():
            with self.subTest(level=level):
                ctx = LogisticsFixture()
                api = MilitaryAPI(ctx)
                api._pause_for_action = AsyncMock()
                with ctx.patches():
                    result = await api.logistics.set_allocation(level)

                self.assertEqual(
                    ctx.producer_expressions,
                    ["Country.SetSupplyShipConstructionLevel" + suffix],
                )
                self.assertEqual(ctx.owner.calls, [("validate", 65536), ("submit", 4)])
                self.assertEqual(len(ctx.command_value_calls), 1)
                self.assertEqual(len(ctx.clone_calls), 1)
                self.assertTrue(result["accepted"])
                self.assertEqual(result["status"], "accepted")
                self.assertTrue(result["paused"])
                self.assertEqual(result["level"], level)
                self.assertEqual(
                    result["observation"]["supply_construction_capacity"],
                    Decimal("8.00000"),
                )
                self.assertEqual(
                    result["observation"]["military_queue_usage"],
                    Decimal("2.00000"),
                )
                self.assertIsNone(result["observed_selected_level"])
                self.assertIn("no dedicated selected-level getter", result["selected_level_unavailable_reason"])
                api._pause_for_action.assert_awaited_once()
                self.assertFalse(ctx.handles)
                self.assertFalse(api._warfare_runs)

    async def test_unknown_postread_keeps_acceptance_and_original_producer_graph(self):
        ctx = LogisticsFixture()
        api = MilitaryAPI(ctx)
        api._pause_for_action = AsyncMock()
        ctx.unknown_expression = "Country.GetSupplyConstructionCapacity"
        with ctx.patches():
            result = await api.logistics.set_allocation("high")

        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["paused"])
        self.assertIn("post-read outcome unknown", result["observation_error"])
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(ctx.producer_expressions, ["Country.SetSupplyShipConstructionLevelHigh"])
        self.assertEqual(ctx.owner.calls, [("validate", 65536), ("submit", 4)])
        self.assertFalse(ctx.releases)
        self.assertEqual(len(ctx.handles), 1)
        self.assertEqual(next(iter(ctx.handles.values())), (
            "command_source", "Country.SetSupplyShipConstructionLevelHigh"
        ))
        self.assertEqual(len(api._warfare_runs), 1)
        self.assertIs(api._warfare_runs[0].public_result, result)

    async def test_invalid_level_rejects_before_pause_or_native_work(self):
        ctx = LogisticsFixture()
        api = MilitaryAPI(ctx)
        api._pause_for_action = AsyncMock()
        with self.assertRaisesRegex(ValueError, "allocation"):
            await api.logistics.set_allocation("automatic")
        api._pause_for_action.assert_not_awaited()
        self.assertFalse(ctx.queries)
        self.assertFalse(ctx.expressions)


class AttritionTests(unittest.IsolatedAsyncioTestCase):
    async def test_attrition_preserves_zero_false_fixed_and_partial_native_results(self):
        ctx = LogisticsFixture()
        api = MilitaryAPI(ctx)
        ctx.native_errors["MilitaryFormation.GetAverageAttritionPerWeek"] = 21
        ctx.native_partial_errors["MilitaryFormation.GetAttritionRiskDesc"] = (
            "Low attr",
            len("Low attrition risk"),
        )
        with ctx.patches():
            result = (await api.formation(ctx.FORMATION_ID).logistics())["attrition"]

        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["formation_id"], ctx.FORMATION_ID)
        self.assertEqual(result["attrition"], Decimal("0.00000"))
        self.assertIsNone(result["average_weekly_attrition"])
        self.assertIn("native error 21", result["unavailable"]["average_weekly_attrition"])
        self.assertEqual(result["attrition_risk"], "Low attr")
        self.assertIn("native error 122", result["unavailable"]["attrition_risk"])
        self.assertFalse(result["logistics_goods_shortage"])
        self.assertEqual(result["supply_shortage_ratio"], Decimal("0.00000"))
        self.assertFalse(result["supply_shortage_organization_penalty"])
        self.assertEqual(result["effective_supply_distance"], Decimal("4.50000"))
        self.assertEqual(result["goods_consumption_cost"], Decimal("1.75000"))
        self.assertEqual(ctx.refresh_count, 2)
        self.assertFalse(ctx.handles)
        read = next(entry for entry in ctx.diagnostics if isinstance(entry, WarfareRead))
        receipts = {label: value for label, value in read.operations}
        self.assertEqual(receipts["MilitaryFormation.GetAverageAttritionPerWeek"].error, 21)
        self.assertEqual(receipts["MilitaryFormation.GetAttritionRiskDesc"].value, "Low attr")



if __name__ == "__main__":
    unittest.main()
