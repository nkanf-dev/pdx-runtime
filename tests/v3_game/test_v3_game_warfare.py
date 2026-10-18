"""CPU-only warfare provider tests; no process, game or DLL is opened."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game.military import MilitaryAPI
from pdx_runtime.v3_game.military._warfare import country_war_fronts


TYPES = {"Country": 83, "War": 236, "Front": 214, "bool": 5,
         "uint32": 6, "int32": 4, "int64": 115, "CString": 0, "CFixedPoint": 82}


def function(name, result_type, arguments=0, *, action=False):
    return SimpleNamespace(name=name, argument_count=arguments, return_type_id=result_type,
                           callbacks=(0, 0, 0, 123) if action else (123, 0, 0, 0))


class Fixture:
    def __init__(self):
        self.native = object()
        self.process = object()
        self.world = 77
        self.diagnostics = []
        self.adapters = {"data": "fake"}
        self.queries, self.expressions, self.releases = [], [], []
        self.handles, self.next_handle = {}, 100
        self.wars = [[11, 12], [12, 13]]
        self.fields = {
            "Country": {"AccessWars": function("AccessWars", 70)},
            "War": {"AccessFronts": function("AccessFronts", 70)},
            "Front": {name: function(name, TYPES[kind], argc) for name, kind, argc in (
                ("IsValid", "bool", 0), ("GetID", "uint32", 0),
                ("GetNameNoFormatting", "CString", 0), ("GetPlayerAdvantageRaw", "int32", 0),
                ("GetPlayerAdvantageDesc", "CString", 0), ("GetOccupationProgressDesc", "CString", 0),
                ("GetPlayerSideNumUnitsAtFront", "int32", 0), ("GetPlayerSideNumUnitsTraveling", "int32", 0),
                ("HasCountry", "bool", 1), ("IsVulnerable", "bool", 1))}}
        self.native_errors = {}
        self.unknown_expression = None
        self.borrowed_front = False
        self.release_error = None
        self.refresh_count = 0

    async def query(self, label, factory, **kwargs):
        self.queries.append(label)
        return factory()

    async def country_id(self):
        return 156

    async def type_id(self, name):
        return TYPES[name]

    async def refresh(self):
        self.refresh_count += 1

    async def perform(self, label, operation):
        return await operation()

    def result(self, action, *, error=0, value=None, kind=data.Kind.EMPTY, type_id=0, handle=0):
        raw = value.encode("utf-8") if isinstance(value, str) else b""
        return SimpleNamespace(action=action, error=error, value=value, kind=kind,
            type_id=type_id, value_handle=handle, total=len(raw), snapshot_bytes=len(raw), snapshot=raw,
            native_type_name=value.native_type_name if isinstance(value, data.NativeObject) else "")

    def retain(self, content):
        self.next_handle += 1
        self.handles[self.next_handle] = content
        return self.next_handle

    def evaluate(self, native, expression, bindings, **kwargs):
        self.expressions.append(expression)
        if expression == self.unknown_expression:
            raise RuntimeError("original callback outcome unknown")
        if expression in self.native_errors:
            return self.result(data.Action.EVALUATE, error=self.native_errors[expression])
        if expression == "Country.AccessWars":
            handle = self.retain(("list", "War", list(range(len(self.wars)))))
            return self.result(data.Action.EVALUATE, value=data.NativeList(handle, 0x3000, b"", "native list"),
                kind=data.Kind.LIST, handle=handle)
        if expression == "War.AccessFronts":
            _, _, war_index = self.handles[bindings[0].object]
            handle = self.retain(("list", "Front", self.wars[war_index]))
            return self.result(data.Action.EVALUATE, value=data.NativeList(handle, 0x3000, b"", "native list"),
                kind=data.Kind.LIST, handle=handle)
        _, _, front_id = self.handles[bindings[0].object]
        method = expression.removeprefix("Front.").split("(")[0]
        values = {"IsValid": True, "GetID": front_id,
            "GetNameNoFormatting": f"#bold Front {front_id}#!", "GetPlayerAdvantageRaw": 7,
            "GetPlayerAdvantageDesc": "Advantage", "GetOccupationProgressDesc": "Occupation",
            "GetPlayerSideNumUnitsAtFront": 0, "GetPlayerSideNumUnitsTraveling": 0,
            "HasCountry": front_id != 13, "IsVulnerable": True}
        type_id = self.fields["Front"][method].return_type_id
        kind = {5:data.Kind.BOOL, 6:data.Kind.U32, 4:data.Kind.I32, 0:data.Kind.STRING}[type_id]
        return self.result(data.Action.EVALUATE, value=values[method], kind=kind, type_id=type_id)

    def list_count(self, native, handle, **kwargs):
        return SimpleNamespace(error=0, total=len(self.handles[handle][2]))

    def list_element(self, native, handle, index, **kwargs):
        _, name, entries = self.handles[handle]
        child_handle = self.retain(("object", name, entries[index]))
        native_name = ".?AU?$SAnyType@V?$CPdxPtr@VCData" + name + "@@@@@PdxAnyImpl@@"
        if name == "Front" and self.borrowed_front:
            native_name = ".?AU?$SAnyType@PEAVCDataFront@@@PdxAnyImpl@@"
        return self.result(data.Action.LIST_ELEMENT, value=data.NativeObject(
            0x1000+child_handle, TYPES[name], child_handle, native_name),
            kind=data.Kind.OBJECT, type_id=TYPES[name], handle=child_handle)

    def functions(self, native, type_id, **kwargs):
        name = next(name for name, number in TYPES.items() if number == type_id)
        rows = list(self.fields[name].values())
        return SimpleNamespace(error=0, rows=rows, total=len(rows), next=len(rows))

    def release(self, native, handle, **kwargs):
        self.releases.append(handle)
        if self.release_error:
            return SimpleNamespace(error=self.release_error, value_handle=handle)
        del self.handles[handle]
        return SimpleNamespace(error=0, value_handle=0)

    def patches(self):
        return patch.multiple(data, evaluate=self.evaluate, list_count=self.list_count,
            list_element=self.list_element, enumerate_functions=self.functions, release=self.release)


class WarfareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = Fixture()
        self.api = MilitaryAPI(self.ctx)

    async def test_war_provider_finds_unoccupied_fronts_and_keeps_ally_fronts(self):
        with self.ctx.patches():
            result = await self.api._call(__import__("pdx_runtime.v3_game.military._warfare", fromlist=["country_war_fronts"]), "country_war_fronts")
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["coverage"], "country_war_fronts")
        self.assertEqual([row["id"] for row in result["fronts"]], [11, 12, 13])
        self.assertEqual([row["player_units_at_front"] for row in result["fronts"]], [0, 0, 0])
        self.assertFalse(result["fronts"][-1]["country_in_front"])
        self.assertTrue(all(row["deployment_eligible"] is None for row in result["fronts"]))
        self.assertEqual(result["fronts"][0]["name"], "Front 11")
        self.assertEqual(result["fronts"][0]["name_raw"], "#bold Front 11#!")
        self.assertFalse(self.ctx.handles)
        self.assertEqual(self.ctx.refresh_count, 1)
        self.assertFalse(self.api._warfare_runs)
        self.assertFalse(any("Command" in expression or "Clicked" in expression for expression in self.ctx.expressions))

    async def test_no_wars_is_observed_empty_not_unavailable(self):
        self.ctx.wars = []
        with self.ctx.patches():
            result = await self.api._call(__import__("pdx_runtime.v3_game.military._warfare", fromlist=["country_war_fronts"]), "country_war_fronts")
        self.assertEqual(result["fronts"], [])
        self.assertEqual(result["status"], "observed")
        self.assertFalse(self.ctx.handles)

    async def test_actual_source_failure_is_not_empty_and_known_values_are_released(self):
        self.ctx.native_errors["War.AccessFronts"] = 13
        with self.ctx.patches():
            result = await self.api._call(__import__("pdx_runtime.v3_game.military._warfare", fromlist=["country_war_fronts"]), "country_war_fronts")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["fronts"])
        self.assertIn("native error 13", str(result["details"]))
        self.assertFalse(self.ctx.handles)

    async def test_missing_provider_and_action_metadata_are_not_executed(self):
        self.ctx.fields["War"]["AccessFronts"] = function("AccessFronts",70,action=True)
        with self.ctx.patches():
            result = await self.api._call(__import__("pdx_runtime.v3_game.military._warfare", fromlist=["country_war_fronts"]), "country_war_fronts")
        self.assertIsNone(result["fronts"])
        self.assertFalse(self.ctx.expressions)

    async def test_optional_native_field_error_keeps_real_fronts(self):
        self.ctx.native_errors["Front.GetPlayerAdvantageRaw"] = 21
        with self.ctx.patches():
            result = await self.api._call(__import__("pdx_runtime.v3_game.military._warfare", fromlist=["country_war_fronts"]), "country_war_fronts")
        self.assertEqual(len(result["fronts"]), 3)
        self.assertIsNone(result["fronts"][0]["player_advantage"])
        self.assertIn("native error 21", result["fronts"][0]["unavailable"]["player_advantage"])

    async def test_unknown_child_outcome_retains_complete_original_graph(self):
        self.ctx.unknown_expression = "Front.GetID"
        with self.ctx.patches(), self.assertRaisesRegex(RuntimeError, "outcome unknown"):
            await self.api._call(__import__("pdx_runtime.v3_game.military._warfare", fromlist=["country_war_fronts"]), "country_war_fronts")
        self.assertTrue(self.ctx.handles)
        self.assertFalse(self.ctx.releases)
        self.assertEqual(len(self.api._warfare_runs), 1)

    async def test_borrowed_child_without_ownership_evidence_is_not_used(self):
        self.ctx.borrowed_front = True
        with self.ctx.patches():
            result = await self.api._call(__import__("pdx_runtime.v3_game.military._warfare", fromlist=["country_war_fronts"]), "country_war_fronts")
        self.assertIsNone(result["fronts"])
        self.assertIn("ownership", str(result["details"]))
        self.assertNotIn("Front.GetID", self.ctx.expressions)
        self.assertFalse(self.ctx.handles)

    async def test_cleanup_native_error_keeps_observed_result_and_retained_owner(self):
        self.ctx.release_error = 21
        with self.ctx.patches():
            result = await self.api._call(__import__("pdx_runtime.v3_game.military._warfare", fromlist=["country_war_fronts"]), "country_war_fronts")
        self.assertEqual(len(result["fronts"]), 3)
        self.assertEqual(result["cleanup"]["status"], "native_error_retained")
        self.assertEqual(len(self.ctx.releases), 1)
        self.assertTrue(self.ctx.handles)
        self.assertIs(self.api._warfare_runs[0].public_result, result)




class FormationFixture(Fixture):
    def __init__(self):
        super().__init__()
        TYPES["MilitaryFormation"] = 37
        self.fields["Country"].update({name: function(name,70) for name in
            ("GetMilitaryFormationsArmy", "GetMilitaryFormationsFleet")})
        self.formation_values = {
            "GetIDString": (data.Kind.STRING, "2"),
            "GetNameNoFormatting": (data.Kind.STRING, "#bold Army#!"),
            "GetLocationString": (data.Kind.STRING, "Tibet Front"),
            "GetFormationStatusDesc": (data.Kind.STRING, "Defending"),
            "IsMobilized": (data.Kind.BOOL, True), "IsFullyMobilized": (data.Kind.BOOL, False),
            "GetMobilizationPercentage": (data.Kind.FIXED, data.FixedPoint(75000)),
            "HasRaisedConscripts": (data.Kind.BOOL, False), "IsTraveling": (data.Kind.BOOL, True),
            "GetTravelDaysLeft": (data.Kind.I32, 3), "IsInBattle": (data.Kind.BOOL, False),
            "IsAdvancingInBattle": (data.Kind.BOOL, False), "IsDefendingInBattle": (data.Kind.BOOL, False),
            "GetNumCombatUnits": (data.Kind.I32, 8), "GetNumConscripts": (data.Kind.I32, 0),
            "GetCurrentManpower": (data.Kind.I64, 6200), "GetMaxManpower": (data.Kind.I64, 8000),
            "GetOrganizationPercentage": (data.Kind.FIXED, data.FixedPoint(80000)),
            "GetCurrentSupplyAmount": (data.Kind.FIXED, data.FixedPoint(400000)),
            "GetSupplyRatio": (data.Kind.FIXED, data.FixedPoint(50000)),
            "GetSupplyConsumption": (data.Kind.FIXED, data.FixedPoint(100000)),
            "GetSupplyCapacity": (data.Kind.FIXED, data.FixedPoint(800000)),
            "HasSupplyRoute": (data.Kind.BOOL, True), "HasFullSupplyPath": (data.Kind.BOOL, False),
            "CanDeployToFront": (data.Kind.BOOL, True),
        }
        self.fields["MilitaryFormation"] = {name:function(name,0) for name in self.formation_values}
        self.fields["MilitaryFormation"]["GetCurrentFront"] = function("GetCurrentFront",TYPES["Front"])

    def evaluate(self, native, expression, bindings, **kwargs):
        if expression in ("Country.GetMilitaryFormationsArmy", "Country.GetMilitaryFormationsFleet"):
            self.expressions.append(expression)
            handle = self.retain(("list", "MilitaryFormation", [2] if expression.endswith("Army") else []))
            return self.result(data.Action.EVALUATE, value=data.NativeList(handle,0x3000,b"","native list"),
                kind=data.Kind.LIST, handle=handle)
        if expression.startswith("MilitaryFormation.GetCurrentFront."):
            expression = expression.replace("MilitaryFormation.GetCurrentFront.", "Front.")
        elif expression.startswith("MilitaryFormation."):
            self.expressions.append(expression)
            method = expression.split(".")[1]
            if expression in self.native_errors:
                return self.result(data.Action.EVALUATE,error=self.native_errors[expression])
            if method not in self.formation_values:
                return self.result(data.Action.EVALUATE, error=1168)
            kind,value = self.formation_values[method]
            return self.result(data.Action.EVALUATE,value=value,kind=kind)
        return super().evaluate(native,expression,bindings,**kwargs)


class FormationDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def test_formation_supply_and_location_are_real_native_values(self):
        ctx = FormationFixture()
        api = MilitaryAPI(ctx)
        with ctx.patches():
            result = await api.formation(2).read()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["name"], "Army")
        self.assertEqual(result["name_raw"], "#bold Army#!")
        self.assertEqual(result["manpower"], 6200)
        self.assertEqual(str(result["supply_ratio"]), "0.50000")
        self.assertFalse(result["fully_mobilized"])
        self.assertTrue(result["travelling"])
        self.assertIsNone(result["target_front"])
        self.assertIn("GetTargetFront", result["unavailable"]["target_front"])
        self.assertEqual(result["current_front"]["id"], 2)
        self.assertFalse(ctx.handles)
        self.assertEqual(ctx.refresh_count, 1)
        self.assertNotIn("Country.GetMilitaryFormationsFleet", ctx.expressions)

    async def test_native_failure_keeps_other_supply_fields_and_not_owned_is_explicit(self):
        ctx = FormationFixture()
        api = MilitaryAPI(ctx)
        ctx.native_errors["MilitaryFormation.GetCurrentSupplyAmount"] = 21
        with ctx.patches():
            result = await api.formation(2).read()
            absent = await api.formation(99).read()
        self.assertIsNone(result["supply"])
        self.assertEqual(str(result["supply_capacity"]), "8.00000")
        self.assertIn("native error 21", result["unavailable"]["supply"])
        self.assertEqual(absent["status"], "not_found")
        self.assertFalse(ctx.handles)




class FakeCommand:
    def __init__(self, *, eligible=True, accepted=True, unknown_submit=False):
        self.command = 0x1000
        self.world = 77
        self.pending = None
        self.attempt = None
        self.eligible, self.accepted, self.unknown_submit = eligible, accepted, unknown_submit
        self.calls = []

    def validate(self, capacity):
        self.calls.append("validate")
        self.attempt = SimpleNamespace(outcome="owned")
        return SimpleNamespace(error=0, validated=int(self.eligible), reason="" if self.eligible else "No path to this front")

    def submit(self, flags):
        self.calls.append("submit")
        self.attempt = SimpleNamespace(outcome="unknown" if self.unknown_submit else "consumed")
        if not self.unknown_submit:
            self.command = 0
        return SimpleNamespace(error=0, accepted=int(self.accepted), command=self.command)

    def destroy(self):
        self.calls.append("destroy")
        self.command = 0
        self.attempt = SimpleNamespace(outcome="consumed")
        return SimpleNamespace(error=0, command=0)


class FormationCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from pdx_runtime import v3_commands as commands
        self.commands = commands
        self.ctx = Fixture()
        self.ctx.adapters["commands"] = "fake"
        self.api = MilitaryAPI(self.ctx)
        self.api._pause_for_action = unittest.mock.AsyncMock()
        self.owner = FakeCommand()
        self.tokens = {"deploy_military_formation_to_front":99, "station_military_formation":100,
                       "formation":101, "front":102, "state":103}
        self.resolved, self.created = [], []

    def resolve(self, native, name, **kwargs):
        self.resolved.append(name)
        return SimpleNamespace(error=0,type_token=self.tokens[name])

    def create(self, native, token, **kwargs):
        self.created.append((token,kwargs))
        return SimpleNamespace(error=0, command=self.owner.command)

    def patches(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(self.commands, "resolve_token", self.resolve))
        stack.enter_context(patch.object(self.commands, "create", self.create))
        return stack

    async def test_deploy_uses_actual_canonical_fields_and_observes_without_claiming_arrival(self):
        observation = {"status":"observed", "current_front":{"id":7}, "travelling":False}
        with self.patches(), patch.object(self.commands.NativeCommand,"from_result",return_value=self.owner), \
                patch("pdx_runtime.v3_game.military._warfare.formation_details",unittest.mock.AsyncMock(return_value=observation)):
            result = await self.api.formation(2164260866).deploy(7)
        self.assertEqual(self.resolved,["deploy_military_formation_to_front","formation","front"])
        token, arguments = self.created[0]
        self.assertEqual(arguments["parameters"],{101:self.commands.UInt32(0x81000002),102:self.commands.UInt32(7)})
        self.assertEqual(arguments["actor_id"],156)
        self.assertEqual(arguments["world"],77)
        self.assertEqual(self.owner.calls,["validate","submit"])
        self.assertTrue(result["accepted"])
        self.assertTrue(result["observed_on_requested_front"])
        self.assertIsNone(result["arrived"])
        self.assertFalse(self.api._warfare_runs)

    async def test_targeted_options_do_not_submit_or_pause_and_keep_native_rejection(self):
        self.owner.eligible = False
        with self.patches(), patch.object(self.commands.NativeCommand,"from_result",return_value=self.owner):
            result = await self.api.formation(2).deployment(7)
        self.assertEqual(self.owner.calls,["validate","destroy"])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"],"No path to this front")
        self.api._pause_for_action.assert_not_awaited()
        self.assertIsNone(result["accepted"])

    async def test_rejected_deploy_is_not_submitted(self):
        self.owner.eligible = False
        with self.patches(), patch.object(self.commands.NativeCommand,"from_result",return_value=self.owner):
            result = await self.api.formation(2).deploy(7)
        self.assertFalse(result["accepted"])
        self.assertEqual(self.owner.calls,["validate","destroy"])

    async def test_station_uses_state_not_hq_field(self):
        with self.patches(), patch.object(self.commands.NativeCommand,"from_result",return_value=self.owner), \
                patch("pdx_runtime.v3_game.military._warfare.formation_details",unittest.mock.AsyncMock(return_value={"status":"observed"})):
            result = await self.api.formation(2).station(87)
        self.assertEqual(self.resolved,["station_military_formation","formation","state"])
        self.assertEqual(self.created[0][1]["parameters"],{101:self.commands.UInt32(2),103:self.commands.UInt32(87)})
        self.assertEqual(result["state_id"],87)
        self.assertNotIn("hq_id",result)

    async def test_unknown_submit_preserves_original_result_and_does_not_destroy_or_replay(self):
        self.owner.unknown_submit = True
        with self.patches(), patch.object(self.commands.NativeCommand,"from_result",return_value=self.owner):
            result = await self.api.formation(2).deploy(7)
        self.assertEqual(self.owner.calls,["validate","submit"])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["native_outcome"],"unknown")
        self.assertEqual(result["cleanup"]["status"],"retained_pending_or_unknown")
        self.assertEqual(len(self.api._warfare_runs),1)

    async def test_post_observation_failure_does_not_erase_native_acceptance(self):
        with self.patches(), patch.object(self.commands.NativeCommand,"from_result",return_value=self.owner), \
                patch("pdx_runtime.v3_game.military._warfare.formation_details",unittest.mock.AsyncMock(side_effect=RuntimeError("original read pending"))):
            result = await self.api.formation(2).deploy(7)
        self.assertTrue(result["accepted"])
        self.assertIn("original read pending",result["observation_error"])
        self.assertEqual(self.owner.calls,["validate","submit"])

    async def test_public_ids_reject_bool_or_truncated_range_before_native_work(self):
        with self.assertRaises(ValueError):
            await self.api.formation(True).deploy(7)
        with self.assertRaises(ValueError):
            await self.api.formation(2).deploy(4294967295)
        self.assertFalse(self.created)


if __name__ == "__main__":
    unittest.main()
