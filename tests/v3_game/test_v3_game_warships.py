"""CPU-only contract tests for the independent warships source module.

No game, PID, controller, DLL, runfile, attach, load, or deployment is used.
The tests stop at original DataFactory/source and command-producer boundaries.
"""
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime.v3_game._run import SourceUnavailable, command_field
from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.v3_game.military import ships as warships
from pdx_runtime.v3_game.military._warfare import WarfareRead


FEATURE_IDS = {
    "war.ship-stats",
    "war.ship-decommission",
    "war.ship-rename",
    "war.ship-build-options",
    "war.ship-build",
    "war.ship-queue",
    "war.ship-queue-cancel",
    "war.ship-queue-reorder",
    "war.ship-queue-pause",
    "war.ship-default-fleet",
    "war.ship-retrofit",
    "war.ship-repair",
    "war.ship-operational-state",
    "war.template-read",
    "war.template-edit",
    "war.template-delete",
    "war.template-default",
    "war.flagship",
    "war.flagship-style",
    "war.flagship-read",
    "war.sea-region-read",
    "war.fleet-deploy",
    "war.transport-options",
    "war.transport-order",
    "war.marines-attach",
    "war.invasion-options",
    "war.invasion-start",
    "war.invasion-read",
    "war.invasion-cancel",
    "war.invasion-defend",
    "war.strait-read",
    "war.strait-tolls",
    "war.strait-access",
    "war.strait-permissions",
}


class FakeAPI:
    @staticmethod
    def _native_full_id(value):
        if isinstance(value, str) and value.isdigit():
            return int(value)
        if type(value) is int:
            return value & 0xFFFFFFFF
        return None


class FakeRun:
    def __init__(self):
        self.world = 77
        self.actor = 9
        self.api = FakeAPI()
        self.providers = []
        self.element_calls = []
        self.field_calls = []
        self.fields_calls = []
        self.origin_calls = []
        self.element_rows = {}
        self.origins = {}
        self.values = {}
        self.evaluations = []
        self.wrappers = []
        self.destroyed = []
        self.operations = []
        self.owners = []
        self.adopt_calls = []
        self.unadopted_command = None
        self.ctx_queries = []
        self.ctx = SimpleNamespace(
            native=object(), adapters={"commands": "commands"}, process=object(),
            query=self._ctx_query,
        )

    async def _ctx_query(self, label, factory, **kwargs):
        self.ctx_queries.append(label)
        return factory()

    def adopt(self, result, *, expected_world=None):
        self.adopt_calls.append((result, expected_world))
        owner = commands.NativeCommand.from_result(
            self.ctx.native, result, adapter=self.ctx.adapters["commands"],
            process=self.ctx.process)
        self.owners.append(owner)
        expected = self.world if expected_world is None else expected_world
        if owner.world != expected:
            raise RuntimeError("Native command belongs to another source world")
        return owner

    async def destroy(self, owner):
        self.destroyed.append(owner)

    async def country(self):
        return "country"

    async def type_id(self, name):
        return {"ShipList": 501}.get(name, 999)

    async def evaluate(self, expression, bindings):
        self.evaluations.append((expression, tuple(bindings)))
        if expression == "Country.GetShipList":
            return SimpleNamespace(value_handle=700, value=object())
        raise AssertionError("unexpected evaluate: " + expression)

    def wrapper(self, result, named_type, **kwargs):
        self.wrappers.append((result, named_type))
        return result.value

    async def provider(self, named_type, method, arguments=0):
        self.providers.append((named_type, method, arguments))
        return object()

    async def elements(self, expression, bindings, named_type):
        self.element_calls.append((expression, tuple(bindings), named_type))
        for row in self.element_rows.get(expression, ()):
            yield row

    async def origin(self, element, named_type, *args, **kwargs):
        self.origin_calls.append((element, named_type))
        return self.origins[(element, named_type)]

    async def field(self, named_type, method, bindings, kinds, *, arguments=0, expression=None):
        expression = expression or named_type + "." + method
        self.field_calls.append((named_type, method, tuple(bindings), tuple(kinds), arguments, expression))
        key = (expression, tuple(bindings))
        value = self.values.get(key, self.values.get(expression, ...))
        if value is not ...:
            if isinstance(value, tuple) and len(value) == 2 and value[0] == "error":
                return None, value[1]
            return value, None
        if data.Kind.STRING in kinds:
            return "raw:" + expression, None
        if data.Kind.BOOL in kinds:
            return False, None
        if data.Kind.FIXED in kinds:
            return Decimal("1.25"), None
        if data.Kind.I64 in kinds:
            return 64, None
        return 3, None

    async def fields(self, named_type, bindings, specs, *, expression_prefix=None):
        specs = tuple(specs)
        self.fields_calls.append((named_type, tuple(bindings), specs, expression_prefix))
        row = {"unavailable": {}}
        prefix = expression_prefix or named_type
        for method, key, kinds in specs:
            kinds = kinds if isinstance(kinds, tuple) else (kinds,)
            row[key], reason = await self.field(
                named_type, method, bindings, kinds,
                expression=prefix + "." + method)
            if reason:
                row["unavailable"][key] = reason
        return row

    async def functions(self, named_type):
        return {
            "IsValid": object(),
            "GetID": object(),
            "GetIDString": object(),
            "GetNameNoFormatting": object(),
            "GetName": object(),
            "GetTooltipTag": object(),
        }


async def direct_owned(run, api, label, operation):
    return await operation(run)


class WarshipsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.run = FakeRun()
        async def owned(api, label, operation):
            return await direct_owned(self.run, api, label, operation)
        self.owned = patch.object(warships, "owned_read", side_effect=owned)
        self.owned.start()
        self.addCleanup(self.owned.stop)


    def test_row_merge_keeps_existing_unavailable_and_native_pages(self):
        page_a = object()
        page_b = object()
        row = {
            "id": 7,
            "unavailable": {"relation": "not retained"},
            "native_pages": [page_a],
        }
        merged = warships._merge_fields(row, {
            "name": "raw name",
            "unavailable": {"field": "partial cell"},
            "native_pages": [page_b],
        })
        self.assertIs(merged, row)
        self.assertEqual(row["name"], "raw name")
        self.assertEqual(row["unavailable"], {
            "relation": "not retained", "field": "partial cell"})
        self.assertEqual(row["native_pages"], [page_a, page_b])

    async def test_shared_fields_batches_45_cells_into_two_pages_and_preserves_errors(self):
        run = object.__new__(WarfareRead)
        run.page_available = AsyncMock(return_value=True)
        first_cells = [
            SimpleNamespace(
                row=0, field=index, kind=data.Kind.STRING,
                value=f"value-{index}", reason="partial cell" if index == 5 else None)
            for index in range(32)
        ]
        second_cells = [
            SimpleNamespace(
                row=0, field=index, kind=data.Kind.STRING,
                value=f"value-{index + 32}", reason=None)
            for index in range(13)
        ]
        first_page = SimpleNamespace(error=0, cells=first_cells)
        second_page = SimpleNamespace(error=123, cells=second_cells)
        run.page = AsyncMock(side_effect=[first_page, second_page])
        run.cell_value = lambda cell: (cell.value, cell.reason)
        specs = tuple(
            (f"GetField{index}", f"field_{index}", (data.Kind.STRING,))
            for index in range(45)
        )

        result = await WarfareRead.fields(
            run, "Ship", ("ship",), specs, expression_prefix="Ship.GetTemplate")

        self.assertEqual(run.page.await_count, 2)
        first = run.page.await_args_list[0]
        second = run.page.await_args_list[1]
        self.assertEqual(len(first.args[0]), 32)
        self.assertEqual(len(second.args[0]), 13)
        self.assertEqual(first.args[0][0], "Ship.GetTemplate.GetField0")
        self.assertEqual(second.args[0][0], "Ship.GetTemplate.GetField32")
        self.assertEqual(result["native_pages"], [first_page, second_page])
        self.assertEqual(result["field_0"], "value-0")
        self.assertEqual(result["unavailable"]["field_5"], "partial cell")
        self.assertIn("native page error 123", result["unavailable"]["field_32"])

    async def test_ship_state_uses_owned_ship_source_and_preserves_display_raw(self):
        self.run.element_rows["ShipList.GetShips"] = [
            (0, "ship-element", "ship-binding"),
        ]
        self.run.origins[("ship-element", "Ship")] = 0x81000007
        self.run.values["Ship.GetNameNoFormatting"] = "#bold HMS Resolute#!"
        relation = AsyncMock(return_value=({"id": 22, "name": "Fleet"}, None))
        chains = AsyncMock(side_effect=[
            {"name": "Template", "unavailable": {}},
            {"name": "Destroyer", "unavailable": {}},
            {"name": "Veteran", "unavailable": {}},
        ])
        modifications = AsyncMock(return_value=([], []))
        with (
            patch.object(warships, "_formation_relation", relation),
            patch.object(warships, "_chain_fields", chains),
            patch.object(warships, "_modifications", modifications),
            patch.object(data, "create_ship_name_context",
                         side_effect=AssertionError("ship_state must not create a rename context")) as context,
            patch.object(data, "invoke_action",
                         side_effect=AssertionError("ship_state must not edit a rename draft")) as edit_name,
            patch.object(warships, "_produced_owner", AsyncMock(
                side_effect=AssertionError("ship_state must not produce a rename command"))) as produce,
            patch.object(warships, "_validate_submit", AsyncMock(
                side_effect=AssertionError("ship_state must not validate or submit"))) as validate,
            patch.object(commands, "request",
                         side_effect=AssertionError("ship_state must not query command availability")) as command,
        ):
            result = await warships.ship_state(self.api, 0x81000007)

        self.assertEqual(result["id"], 0x81000007)
        self.assertEqual(result["name"], "HMS Resolute")
        self.assertEqual(result["name_raw"], "#bold HMS Resolute#!")
        self.assertEqual(result["source_index"], 0)
        self.assertIsNone(result["rename_command_available"])
        self.assertIsNone(result["rename_command_unavailable_reason"])
        self.assertEqual(result["rename_command_status"], "not_checked")
        self.assertEqual(result["rename_command_entrypoint"], "military.rename_ship")
        self.assertEqual(result["rename_command_source"], "ShipChangeNamePopup.Confirm")
        self.assertFalse(result["flagship_signal_flags"])
        self.assertEqual(result["crew_current"], 3)
        context.assert_not_called()
        edit_name.assert_not_called()
        produce.assert_not_awaited()
        validate.assert_not_awaited()
        command.assert_not_called()
        self.assertEqual(self.run.evaluations, [("Country.GetShipList", ("country",))])
        self.assertEqual(self.run.wrappers[0][1], "ShipList")
        self.assertEqual(self.run.element_calls[0][0], "ShipList.GetShips")
        self.assertIn(("Country", "GetShipList", 0), self.run.providers)
        self.assertIn(("ShipList", "GetShips", 0), self.run.providers)
        self.assertEqual(self.run.origin_calls, [("ship-element", "Ship")])
        self.assertIn("hull_current", result)
        self.assertIn("crew_current", result)
        self.assertIn("retrofit_progress", result)
        self.assertIn("flagship_signal_flags", result)
        ship_batches = [call for call in self.run.fields_calls
                        if call[0] == "Ship" and call[3] is None]
        self.assertEqual(len(ship_batches), 1)
        self.assertEqual(len(ship_batches[0][2]), 45)

    async def test_ship_selection_rejects_bool_invalid_id_and_preserves_zero(self):
        self.run.element_rows["ShipList.GetShips"] = [(0, "zero-element", "zero-ship")]
        self.run.origins[("zero-element", "Ship")] = 0
        for wanted in (True, False, -1, 0xffffffff, 0x100000000, "0"):
            with self.subTest(wanted=wanted):
                with self.assertRaisesRegex(ValueError, "complete uint32 Ship ID"):
                    await warships._ship(self.run, wanted)
        self.assertEqual(self.run.element_calls, [])
        self.assertEqual(await warships._ship(self.run, 0), (0, "zero-ship"))

    async def test_template_read_uses_native_full_id_and_real_modification_lists(self):
        self.run.element_rows["ShipList.GetShipTemplates"] = [
            (2, "template-element", "template-binding"),
        ]
        self.run.origins[("template-element", "ShipTemplate")] = 0x8200002A
        self.run.element_rows["ShipTemplate.GetModifications"] = [
            (0, "mod-element", "mod-binding"),
        ]
        self.run.element_rows["ShipTemplate.GetUtilityModifications"] = []
        self.run.values["ShipTemplate.GetNameNoFormatting"] = "#bold Coastal Raider#!"
        self.run.values["ShipTemplate.GetTooltipTag"] = "ship_template_coastal_raider"
        self.run.values["ShipModificationType.GetNameNoFormatting"] = "#v Reinforced Hull#!"
        self.run.values["ShipModificationType.GetID"] = 41

        result = await warships.ship_templates(self.api)
        row = result["templates"][0]
        self.assertEqual(row["id"], 0x8200002A)
        self.assertEqual(row["source_index"], 2)
        self.assertEqual(row["tooltip_tag"], "ship_template_coastal_raider")
        self.assertEqual(row["name"], "Coastal Raider")
        self.assertEqual(row["name_raw"], "#bold Coastal Raider#!")
        self.assertEqual(row["modifications"][0]["id"], 41)
        self.assertEqual(row["modifications"][0]["name_raw"], "#v Reinforced Hull#!")
        self.assertIn(
            ("ShipTemplate.GetModifications", ("template-binding",), "ShipModificationType"),
            self.run.element_calls,
        )
        self.assertEqual(result["identity_scope"], "native_full_ship_template_id")
        self.assertIn(("template-element", "ShipTemplate"), self.run.origin_calls)
        template_batches = [call for call in self.run.fields_calls
                            if call[0] == "ShipTemplate" and call[3] is None]
        self.assertEqual(len(template_batches), 1)
        self.assertEqual(len(template_batches[0][2]), 28)
        self.assertIn(
            ("ShipType", ("template-binding",)),
            [(call[0], call[1]) for call in self.run.fields_calls
             if call[3] == "ShipTemplate.GetType"],
        )


    async def test_ship_template_selection_uses_native_full_id_not_ordinal_or_name(self):
        self.run.element_rows["ShipList.GetShipTemplates"] = [
            (4, "template-element", "template"),
        ]
        self.run.origins[("template-element", "ShipTemplate")] = 0x8200002A
        self.assertEqual(await warships._template(self.run, 0x8200002A), "template")
        self.assertEqual(self.run.origin_calls, [("template-element", "ShipTemplate")])
        self.assertFalse(any(call[1] in ("GetTooltipTag", "GetNameNoFormatting")
                             for call in self.run.field_calls))
        with self.assertRaisesRegex(SourceUnavailable, "was not present"):
            await warships._template(self.run, 0x8200002B)

    async def test_ship_change_template_clones_one_selected_original_producer(self):
        produced = AsyncMock(return_value="owner")
        validate = AsyncMock(return_value=False)
        with (
            patch.object(warships, "_ship", AsyncMock(return_value=(0, "ship"))),
            patch.object(warships, "_template", AsyncMock(return_value="template")),
            patch.object(warships, "_produced_owner", produced),
            patch.object(warships, "_validate_submit", validate),
        ):
            result = await warships.ship_action(
                self.api, 88, "change_template", template_id=0x8200002A,
                validate_only=True)

        produced.assert_awaited_once_with(
            self.run,
            "Ship.ChangeTemplate(ShipTemplate.AccessSelf)",
            ("ship", "template"),
        )
        validate.assert_awaited_once_with(self.run, "owner", result, validate_only=True)
        self.assertIsNone(result["accepted"])
        self.assertEqual(result["template_id"], 0x8200002A)

    def test_queue_tree_replaces_only_canonical_count_field(self):
        body = (
            commands.Assignment(commands.Token(10304), commands.UInt32(9)),
            commands.Assignment(commands.Token(65), (
                commands.Assignment(commands.Token(717), commands.UInt32(0x8200002A)),
                commands.Assignment(commands.Token(23168), commands.UInt32(44)),
                commands.Assignment(commands.Token(22805), 2),
                commands.Assignment(commands.Token(22308), commands.Token(603)),
            )),
        )
        tree = commands.decode_command(commands.encode_command(24653, body))
        replaced = warships._replace_queue_quantity(tree, 3)
        self.assertEqual(command_field(replaced, 22805), 3)
        self.assertEqual(command_field(replaced, 10304), commands.UInt32(9))
        self.assertEqual(command_field(replaced, 717), commands.UInt32(0x8200002A))
        self.assertEqual(command_field(replaced, 23168), commands.UInt32(44))
        self.assertEqual(command_field(replaced, 22308), commands.Token(603))
        self.assertEqual(replaced.key, tree.key)
        self.assertEqual(commands.token_ids(replaced), commands.token_ids(tree))
        self.assertEqual(warships._QUEUE_QUANTITY_NAME, "construction_increment_level")

    async def test_queue_count_rebuilds_one_fresh_owner_and_submits_once(self):
        owner = SimpleNamespace(pending=None, attempt=None, name="rebuilt-owner")
        rewrite = {
            "original_native_quantity": 2,
            "requested_native_quantity": 3,
            "original_serialization_result": {"wire": "original"},
            "deserialize_result": {"command": "fresh"},
        }
        rebuild = AsyncMock(return_value=(owner, rewrite))

        async def validate(run, selected, attempt, *, validate_only=False):
            self.assertIs(selected, owner)
            attempt.update(
                status="accepted", accepted=True,
                native_result={"owner": selected.name, "accepted": True},
            )
            return True

        with (
            patch.object(warships, "_queue_owner_for_count", rebuild),
            patch.object(warships, "_validate_submit", side_effect=validate),
            patch.object(warships, "ship_templates", AsyncMock(return_value={"status": "observed"})),
            patch.object(warships, "ship_constructions", AsyncMock(return_value={"status": "observed"})),
        ):
            result = await warships.ship_template_action(
                self.api, 0x8200002A, "queue_for_fleet", fleet_id=44, count=3)

        rebuild.assert_awaited_once_with(
            self.run, 0x8200002A, "queue_for_fleet", 44, 3)
        self.assertEqual(result["requested_count"], 3)
        self.assertEqual(result["accepted_count"], 3)
        self.assertEqual(result["remaining_count"], 0)
        self.assertEqual(result["native_attempt_count"], 1)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["attempts"][0]["native_result"]["owner"], "rebuilt-owner")
        self.assertEqual(result["attempts"][0]["original_native_quantity"], 2)

    async def test_queue_rejection_returns_zero_accepted_count_without_replay(self):
        owner = SimpleNamespace(pending=None, attempt=None, name="rebuilt-owner")
        rebuild = AsyncMock(return_value=(owner, {
            "original_native_quantity": 10,
            "requested_native_quantity": 4,
        }))

        async def validate(run, selected, attempt, *, validate_only=False):
            attempt.update(
                status="rejected", accepted=False,
                native_result={"owner": selected.name, "accepted": False},
            )
            return False

        with (
            patch.object(warships, "_queue_owner_for_count", rebuild),
            patch.object(warships, "_validate_submit", side_effect=validate),
        ):
            result = await warships.ship_template_action(
                self.api, 0x8200002A, "queue", count=4)
        self.assertEqual(rebuild.await_count, 1)
        self.assertEqual(result["native_attempt_count"], 1)
        self.assertEqual(result["accepted_count"], 0)
        self.assertEqual(result["remaining_count"], 4)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "rejected")

    async def test_queue_validate_only_leaves_cleanup_to_shared_finish(self):
        owner = SimpleNamespace(pending=None, attempt=None)

        async def validate(run, selected, attempt, *, validate_only=False):
            attempt.update(status="available", eligible=True, accepted=None)
            return False

        with (
            patch.object(warships, "_queue_owner_for_count", AsyncMock(return_value=(
                owner, {"original_native_quantity": 1, "requested_native_quantity": 5}))),
            patch.object(warships, "_validate_submit", side_effect=validate),
        ):
            result = await warships.ship_template_action(
                self.api, 0x8200002A, "queue", count=5, validate_only=True)
        self.assertEqual(self.run.destroyed, [])
        self.assertEqual(result["validated_count"], 5)
        self.assertEqual(result["remaining_count"], 0)
        self.assertEqual(result["status"], "available")

    async def test_queue_acceptance_counts_preserve_native_tri_state_without_replay(self):
        for accepted, error, expected_count, expected_remaining in (
                (True, 5, 4, 0), (False, 0, 0, 4), (None, 5, None, None)):
            with self.subTest(accepted=accepted, error=error):
                receipt = SimpleNamespace(accepted=accepted, error=error)
                owner = SimpleNamespace(pending=object(), attempt=None)
                rebuild = AsyncMock(return_value=(owner, {}))

                async def validate(run, selected, attempt, *, validate_only=False):
                    attempt.update(status="native_result", accepted=accepted,
                                   native_error=error, native_result=receipt)
                    return False

                observe = AsyncMock(side_effect=AssertionError("unsettled readback"))
                with (
                    patch.object(warships, "_queue_owner_for_count", rebuild),
                    patch.object(warships, "_validate_submit", side_effect=validate),
                    patch.object(warships, "ship_templates", observe),
                ):
                    result = await warships.ship_template_action(
                        self.api, 0x8200002A, "queue", count=4)
                self.assertIs(result["accepted"], accepted)
                self.assertEqual(result["accepted_count"], expected_count)
                self.assertEqual(result["remaining_count"], expected_remaining)
                self.assertIs(result["attempts"][0]["native_result"], receipt)
                self.assertEqual(result["attempts"][0]["native_error"], error)
                rebuild.assert_awaited_once()
                observe.assert_not_awaited()

    async def test_queue_unknown_validation_does_not_invent_validated_count(self):
        receipt = SimpleNamespace(error=5, validated=0)

        async def validate(run, selected, attempt, *, validate_only=False):
            attempt.update(status="native_result", accepted=None, eligible=None,
                           validation=receipt, native_error=5)
            return False

        with (
            patch.object(warships, "_queue_owner_for_count", AsyncMock(return_value=(object(), {}))),
            patch.object(warships, "_validate_submit", side_effect=validate),
        ):
            result = await warships.ship_template_action(
                self.api, 0x8200002A, "queue", count=5, validate_only=True)
        self.assertEqual(result["accepted_count"], 0)
        self.assertIsNone(result["validated_count"])
        self.assertIsNone(result["remaining_count"])
        self.assertIs(result["attempts"][0]["validation"], receipt)
        self.assertEqual(self.run.destroyed, [])

    async def test_queue_primary_acceptance_and_validation_survive_cleanup_failure(self):
        from pdx_runtime.v3_game._run import Run as _Run

        for validate_only in (False, True):
            with self.subTest(validate_only=validate_only):
                receipt = SimpleNamespace(accepted=1, error=5, validated=1)
                self.run.cleanup = AsyncMock(side_effect=RuntimeError("cleanup failure"))
                self.run.retain_unknown = False

                async def owned(api, label, operation):
                    result = await operation(self.run)
                    return await _Run.finish(self.run, result)

                async def validate(run, selected, attempt, *, validate_only=False):
                    attempt.update(
                        status="available" if validate_only else "accepted",
                        accepted=None if validate_only else True,
                        validation=receipt, native_result=receipt, native_error=5)
                    return not validate_only

                with (
                    patch.object(warships, "owned_read", side_effect=owned),
                    patch.object(warships, "_queue_owner_for_count", AsyncMock(return_value=(object(), {}))),
                    patch.object(warships, "_validate_submit", side_effect=validate),
                    patch.object(warships, "ship_templates", AsyncMock(return_value={})),
                    patch.object(warships, "ship_constructions", AsyncMock(return_value={})),
                ):
                    result = await warships.ship_template_action(
                        self.api, 0x8200002A, "queue", count=5, validate_only=validate_only)
                self.assertEqual(result["status"], "available" if validate_only else "accepted")
                self.assertIs(result["accepted"], None if validate_only else True)
                self.assertEqual(result["validated_count"] if validate_only else result["accepted_count"], 5)
                self.assertIs(result["attempts"][0]["native_result"], receipt)
                self.assertEqual(result["attempts"][0]["native_error"], 5)
                self.assertEqual(result["cleanup"]["status"], "cleanup_unknown_retained")
                self.assertEqual(result["cleanup"]["reason"], "cleanup failure")
                self.assertEqual(self.run.destroyed, [])

    async def test_queue_quantity_reads_native_serialized_field(self):
        owner = SimpleNamespace(
            pending=None, attempt=None, type_token=24653, serialize=AsyncMock())
        wire = commands.encode_command(24653, {22805: 7})
        serialized = SimpleNamespace(error=0, data=wire)
        with patch.object(warships, "_command_result", AsyncMock(return_value=serialized)):
            quantity, tree, result = await warships._queue_quantity(self.run, owner)
        self.assertEqual(quantity, 7)
        self.assertEqual(tree.key, commands.Token(24653))
        self.assertIs(result, serialized)

    async def test_queue_rebuild_uses_shared_adopt_and_preserves_edited_full_tree(self):
        tree = commands.decode_command(commands.encode_command(24653, {
            10304: commands.UInt32(9),
            65: {
                717: commands.UInt32(0x8200002A),
                23168: commands.UInt32(44),
                22805: 2,
                22308: commands.Token(603),
            },
        }))
        original_serialized = SimpleNamespace(
            error=0, data=commands.encode_tree(tree))
        original = SimpleNamespace(
            pending=None, attempt=None, type_token=24653, world=77,
            serialize=AsyncMock())
        rebuilt = SimpleNamespace(
            pending=None, attempt=None, type_token=24653, world=77,
            serialize=AsyncMock())
        receipt = SimpleNamespace(
            error=0, command=123, action=commands.Action.DESERIALIZE,
            type_token=24653, world=77)
        captured = {}

        def deserialize(*args, **kwargs):
            captured["data"] = kwargs["data"]
            captured["world"] = kwargs["world"]
            return receipt

        with (
            patch.object(warships, "_template_action_producer", AsyncMock(return_value=(
                "ShipTemplate.QueueConstructionForFleet(MilitaryFormation.Self)",
                ("template", "fleet")))),
            patch.object(warships, "_produced_owner", AsyncMock(return_value=original)),
            patch.object(warships, "_queue_quantity", AsyncMock(return_value=(
                2, tree, original_serialized))),
            patch.object(warships, "_token", AsyncMock(return_value=22805)),
            patch.object(warships.commands, "request", side_effect=deserialize),
            patch.object(warships.commands.NativeCommand, "from_result", return_value=rebuilt),
        ):
            owner, evidence = await warships._queue_owner_for_count(
                self.run, 0x8200002A, "queue_for_fleet", 44, 3)

        self.assertIs(owner, rebuilt)
        self.assertEqual(self.run.destroyed, [original])
        self.assertEqual(self.run.owners, [rebuilt])
        self.assertEqual(self.run.adopt_calls, [(receipt, 77)])
        self.assertEqual(captured["world"], 77)
        edited = commands.decode_command(captured["data"])
        self.assertEqual(command_field(edited, 22805), 3)
        self.assertEqual(command_field(edited, 10304), commands.UInt32(9))
        self.assertEqual(command_field(edited, 717), commands.UInt32(0x8200002A))
        self.assertEqual(command_field(edited, 23168), commands.UInt32(44))
        self.assertEqual(command_field(edited, 22308), commands.Token(603))
        self.assertIs(evidence["deserialize_result"], receipt)

    async def test_queue_full_id_is_distinct_from_position_and_generation_slot(self):
        self.run.element_rows["ShipList.GetShipConstructions"] = [
            (5, "construction-element", "construction"),
        ]
        construction_id = 0xF200002A
        self.run.origins[("construction-element", "ShipConstruction")] = construction_id
        self.run.values["ShipConstruction.GetIndex"] = 12
        relation = AsyncMock(return_value=({"id": 44}, None))
        chains = AsyncMock(side_effect=[
            {"name": "Ship", "unavailable": {}},
            {"name": "Template", "unavailable": {}},
            {"name": "Type", "unavailable": {}},
        ])
        with (
            patch.object(warships, "_formation_relation", relation),
            patch.object(warships, "_chain_fields", chains),
        ):
            readback = await warships.ship_constructions(self.api)
        row = readback["constructions"][0]
        self.assertEqual(row["id"], construction_id)
        self.assertEqual(row["queue_index"], 12)
        self.assertEqual(row["source_index"], 5)
        self.assertEqual(row["identity_scope"], "native_full_ship_construction_id")
        self.assertTrue(readback["mutation_available"])

        # Same slot bits and duplicate display/queue values do not alias a new generation.
        self.run.element_rows["ShipList.GetShipConstructions"] = [
            (0, "old-element", "old-construction"),
            (1, "new-element", "new-construction"),
        ]
        self.run.origins[("old-element", "ShipConstruction")] = 0x1200002A
        self.run.origins[("new-element", "ShipConstruction")] = construction_id
        self.run.values[("ShipConstruction.GetIndex", ("old-construction",))] = 12
        self.run.values[("ShipConstruction.GetIndex", ("new-construction",))] = 12
        self.run.values[("ShipConstruction.GetTooltipTag", ("old-construction",))] = "duplicate"
        self.run.values[("ShipConstruction.GetTooltipTag", ("new-construction",))] = "duplicate"
        self.run.field_calls.clear()
        self.assertEqual(await warships._construction(self.run, construction_id), "new-construction")
        self.assertEqual(self.run.field_calls, [])
        with self.assertRaisesRegex(SourceUnavailable, "was not present"):
            await warships._construction(self.run, 0xE200002A)

        produced = AsyncMock(return_value="owner")
        validate = AsyncMock(return_value=False)
        with (
            patch.object(warships, "_construction", AsyncMock(return_value="new-construction")),
            patch.object(warships, "_produced_owner", produced),
            patch.object(warships, "_validate_submit", validate),
        ):
            result = await warships.ship_construction_action(
                self.api, construction_id, "move", new_position=2, validate_only=True)
        produced.assert_awaited_once_with(
            self.run, "ShipConstruction.MoveToIndex('(int32)2')", ("new-construction",))
        validate.assert_awaited_once_with(self.run, "owner", result, validate_only=True)
        self.assertEqual(result["construction_id"], construction_id)

    async def test_pause_and_default_fleet_are_separate_original_country_intents(self):
        produced = AsyncMock(return_value="owner")
        validate = AsyncMock(return_value=False)
        related = AsyncMock(return_value=({"id": 4}, None))
        with (
            patch.object(warships, "_related", related),
            patch.object(warships, "_formation", AsyncMock(return_value=("fleet", "fleet"))),
            patch.object(warships, "_produced_owner", produced),
            patch.object(warships, "_validate_submit", validate),
        ):
            result = await warships.ship_construction_control(
                self.api, default_fleet_id=4, validate_only=True)
        produced.assert_awaited_once_with(
            self.run,
            "Country.SetDefaultConstructionFleet(MilitaryFormation.Self)",
            ("country", "fleet"),
        )
        self.assertEqual(result["default_fleet_id"], 4)
        with self.assertRaisesRegex(ValueError, "separate explicit intents"):
            await warships.ship_construction_control(self.api, paused=True, default_fleet_id=4)


    async def test_fleet_repair_has_separate_read_and_single_original_action(self):
        with patch.object(warships, "_formation", AsyncMock(return_value=("fleet", "fleet"))):
            status = await warships.fleet_repair_status(self.api, 44)
        self.assertEqual(status["fleet_id"], 44)
        self.assertIn("repair_progress", status)

        produced = AsyncMock(return_value="owner")
        validate = AsyncMock(return_value=False)
        with (
            patch.object(warships, "_formation", AsyncMock(return_value=("fleet", "fleet"))),
            patch.object(warships, "_produced_owner", produced),
            patch.object(warships, "_validate_submit", validate),
        ):
            result = await warships.fleet_repair(self.api, 44, validate_only=True)
        produced.assert_awaited_once_with(
            self.run, "MilitaryFormation.RecallForRepairs", ("fleet",))
        validate.assert_awaited_once_with(self.run, "owner", result, validate_only=True)

    async def test_flagship_finds_full_ship_id_in_owned_ship_source(self):
        self.run.element_rows["ShipList.GetShips"] = [
            (0, "e1", "s1"), (1, "e2", "s2"),
        ]
        self.run.origins[("e1", "Ship")] = 100
        self.run.origins[("e2", "Ship")] = 200
        self.run.values[("Ship.IsFlagship", ("s1",))] = False
        self.run.values[("Ship.IsFlagship", ("s2",))] = True
        with patch.object(warships, "_ship_row", AsyncMock(return_value={"id": 200, "name_raw": "Flag"})):
            result = await warships.flagship_state(self.api)
        self.assertEqual(result["flagship"]["id"], 200)
        self.assertEqual(result["invariant"], "one")

    async def test_naval_area_reads_state_region_view_without_flattening_province_set(self):
        self.run.element_rows["MilitaryFormation.GetNavalMissionSeaRegions"] = [
            (0, "region-element", "region"),
        ]
        self.run.values[("StateRegion.GetID", ("region",))] = 71
        self.run.values[("StateRegion.GetNameNoFormatting", ("region",))] = "#bold North Sea#!"
        with patch.object(warships, "_formation", AsyncMock(return_value=("fleet", "fleet"))):
            result = await warships.naval_area_sources(self.api, 44)
        self.assertEqual(result["assigned_sea_regions"][0]["id"], 71)
        self.assertEqual(result["assigned_sea_regions"][0]["name_raw"], "#bold North Sea#!")
        self.assertIsNone(result["candidate_scope"])
        self.assertFalse(result["fleet_deploy_command_available"])
        self.assertFalse(result["area_edit_command_available"])
        self.assertIn("selected-formation state", result["fleet_deploy_command_unavailable_reason"])
        self.assertIn("CProvinceSet", result["area_wire"])
        self.assertIn(
            ("MilitaryFormation.GetNavalMissionSeaRegions", ("fleet",), "StateRegion"),
            self.run.element_calls,
        )

    async def test_transport_reports_real_capacity_without_inventing_pair_eligibility(self):
        self.run.element_rows["Country.GetMilitaryFormationsFleet"] = [
            (0, "fleet-element", "fleet"),
        ]
        self.run.origins[("fleet-element", "MilitaryFormation")] = 44
        self.run.values[("MilitaryFormation.GetAvailableCarryingCapacity", ("fleet",))] = 17
        with (
            patch.object(warships, "_formation", AsyncMock(return_value=("army", "army"))),
            patch.object(warships, "_formation_source_rows", AsyncMock(return_value=[])),
        ):
            result = await warships.transport_options(self.api, 33)
        self.assertEqual(result["fleets"][0]["id"], 44)
        self.assertEqual(result["fleets"][0]["available_carrying_capacity"], 17)
        self.assertEqual(result["fleets"][0]["transported_formations"], [])
        self.assertEqual(result["fleets"][0]["attached_formations"], [])
        self.assertIsNone(result["fleets"][0]["eligible_for_army"])
        self.assertFalse(result["command_available"])
        self.assertFalse(result["marine_attach_command_available"])
        self.assertIn("TransportPlannerPopup.StartTransport", result["command_unavailable_reason"])
        self.assertIn("MilitaryFormationWrapped.EmbarkFormation",
                      result["marine_attach_command_unavailable_reason"])

    async def test_invasion_actions_use_full_id_and_do_not_alias_stale_generation(self):
        invasion_id = 0xF3000044
        produced = AsyncMock(return_value="owner")
        validate = AsyncMock(return_value=False)
        with (
            patch.object(warships, "_invasion", AsyncMock(return_value="invasion")),
            patch.object(warships, "_formation", AsyncMock(return_value=("army", "army"))),
            patch.object(warships, "_produced_owner", produced),
            patch.object(warships, "_validate_submit", validate),
        ):
            defend = await warships.invasion_action(
                self.api, 55, invasion_id, "defend_army",
                formation_id=33, validate_only=True)
        produced.assert_awaited_once_with(
            self.run,
            "Invasion.DeployArmyToDefend(MilitaryFormation.Self)",
            ("invasion", "army"),
        )
        validate.assert_awaited_once_with(self.run, "owner", defend, validate_only=True)
        self.assertEqual(defend["invasion_id"], invasion_id)

        produced.reset_mock()
        validate.reset_mock()
        with (
            patch.object(warships, "_invasion", AsyncMock(return_value="invasion")),
            patch.object(warships, "_produced_owner", produced),
            patch.object(warships, "_validate_submit", validate),
        ):
            cancel = await warships.invasion_action(
                self.api, 55, invasion_id, "cancel", validate_only=True)
        produced.assert_awaited_once_with(self.run, "Invasion.Cancel", ("invasion",))
        validate.assert_awaited_once_with(self.run, "owner", cancel, validate_only=True)

        # Resolve by all generation bits inside the selected War source.
        self.run.element_rows["War.GetInvasions"] = [
            (0, "old-invasion-element", "old-invasion"),
            (1, "new-invasion-element", "new-invasion"),
        ]
        self.run.origins[("old-invasion-element", "Invasion")] = 0x13000044
        self.run.origins[("new-invasion-element", "Invasion")] = invasion_id
        self.run.values[("Invasion.GetName", ("old-invasion",))] = "Duplicate"
        self.run.values[("Invasion.GetName", ("new-invasion",))] = "Duplicate"
        self.run.values[("Invasion.GetTooltipTag", ("old-invasion",))] = "duplicate"
        self.run.values[("Invasion.GetTooltipTag", ("new-invasion",))] = "duplicate"
        self.run.values[("Invasion.IsValid", ("new-invasion",))] = True
        self.run.field_calls.clear()
        with patch.object(warships, "_war", AsyncMock(return_value=("country", "war"))):
            selected = await warships._invasion(self.run, 55, invasion_id)
            self.assertEqual(selected, "new-invasion")
            self.assertEqual(self.run.field_calls, [])
            with self.assertRaisesRegex(SourceUnavailable, "was not present"):
                await warships._invasion(self.run, 55, 0xE3000044)


    async def test_invasion_read_exposes_full_native_identity(self):
        self.run.element_rows["Country.AccessWars"] = [(0, "war-element", "war")]
        self.run.origins[("war-element", "War")] = 55
        self.run.element_rows["War.GetInvasions"] = [(3, "invasion-element", "invasion")]
        self.run.origins[("invasion-element", "Invasion")] = 0xF3000044
        with patch.object(
                warships, "_invasion_row",
                AsyncMock(return_value={"id": 0xF3000044, "war_id": 55,
                                        "source_index": 3})):
            result = await warships.invasions(self.api)
        self.assertEqual(result["invasions"], [
            {"id": 0xF3000044, "war_id": 55, "source_index": 3}])
        self.assertEqual(result["identity_scope"], "native_full_invasion_id_with_war_source")
        self.assertTrue(result["mutation_available"])
        self.assertIn(("invasion-element", "Invasion"), self.run.origin_calls)


    async def test_transfer_uses_observed_source_and_exact_typed_native_fields_once(self):
        from pdx_runtime.v3_game.military import MilitaryAPI
        api = MilitaryAPI(SimpleNamespace())
        self.run.native = self.run.ctx.native
        self.run.process = self.run.ctx.process
        self.run.adapters = self.run.ctx.adapters
        receipt = SimpleNamespace(error=0, command=0xC123, world=77)
        owner = SimpleNamespace(command=0xC123, world=77)
        self.run.adopt = lambda result, **kwargs: owner
        native = SimpleNamespace(accepted=1, error=0)
        async def validate(run, selected, result, *, validate_only=False):
            self.assertIs(selected, owner)
            result.update(accepted=True, status="accepted", native_result=native)
            return True
        with (
            patch.object(warships, "_ship", AsyncMock(return_value=(0, "ship"))),
            patch.object(warships, "_formation_relation", AsyncMock(side_effect=[
                ({"id": 0}, None), ({"id": 0x82000003}, None)])) as relation,
            patch.object(commands, "create", return_value=receipt) as create,
            patch.object(warships, "_validate_submit", AsyncMock(side_effect=validate)) as submit,
        ):
            result = await warships.transfer_ship(api, 0x81000007, 0x82000003)
        create.assert_called_once_with(self.run.native, 24660, actor_id=9, world=77,
            parameters={12911: commands.UInt32(0), 107: commands.UInt32(0x82000003),
                        24647: (commands.UInt32(0x81000007),)},
            adapter="commands", process=self.run.process)
        self.assertEqual(submit.await_count, 1)
        self.assertEqual(relation.await_count, 2)
        self.assertTrue(result["assigned_to_requested_fleet"])
        self.assertIs(result["native_result"], native)
        self.assertIs(result["creation_result"], receipt)

    async def test_transfer_unknown_source_keeps_original_relation_without_native_command(self):
        from pdx_runtime.v3_game.military import MilitaryAPI
        api = MilitaryAPI(SimpleNamespace())
        relation = {"id": None, "name_raw": "native copied fleet", "unavailable": {"id": "partial ID"}}
        with (
            patch.object(warships, "_ship", AsyncMock(return_value=(0, "ship"))),
            patch.object(warships, "_formation_relation", AsyncMock(return_value=(relation, "partial ID"))),
            patch.object(commands, "create") as create,
        ):
            result = await warships.transfer_ship(api, 8, 9)
        self.assertIs(result["source_fleet"], relation)
        self.assertIsNone(result["accepted"])
        self.assertEqual(result["reason"], "partial ID")
        create.assert_not_called()

    async def test_transfer_native_rejection_is_not_overridden_by_client_gates(self):
        from pdx_runtime.v3_game.military import MilitaryAPI
        api = MilitaryAPI(SimpleNamespace())
        self.run.native = self.run.ctx.native
        self.run.process = self.run.ctx.process
        self.run.adapters = self.run.ctx.adapters
        self.run.adopt = lambda result, **kwargs: "owner"
        original = SimpleNamespace(error=0, validated=0, reason="native domain rule")
        async def reject(run, owner, result, *, validate_only=False):
            result.update(accepted=False, status="rejected", reason=original.reason, validation=original)
            return False
        with (
            patch.object(warships, "_ship", AsyncMock(return_value=(0, "ship"))),
            patch.object(warships, "_formation_relation", AsyncMock(return_value=({"id": 4}, None))) as relation,
            patch.object(commands, "create", return_value=SimpleNamespace(error=0, command=0xC123)),
            patch.object(warships, "_validate_submit", AsyncMock(side_effect=reject)) as validate,
        ):
            result = await warships.transfer_ship(api, 8, 9)
        self.assertFalse(result["accepted"])
        self.assertIs(result["validation"], original)
        self.assertEqual(result["reason"], "native domain rule")
        self.assertEqual(validate.await_count, 1)
        self.assertEqual(relation.await_count, 1)

    async def test_transfer_partial_readback_preserves_acceptance_and_unknown_assignment(self):
        from pdx_runtime.v3_game.military import MilitaryAPI
        api = MilitaryAPI(SimpleNamespace())
        self.run.native = self.run.ctx.native
        self.run.process = self.run.ctx.process
        self.run.adapters = self.run.ctx.adapters
        self.run.adopt = lambda result, **kwargs: "owner"
        native = SimpleNamespace(error=0, accepted=1)
        async def accept(run, owner, result, *, validate_only=False):
            result.update(accepted=True, status="accepted", native_result=native)
            return True
        observed = {"id": None, "unavailable": {"id": "native partial"}}
        with (
            patch.object(warships, "_ship", AsyncMock(return_value=(0, "ship"))),
            patch.object(warships, "_formation_relation", AsyncMock(side_effect=[
                ({"id": 4}, None), (observed, "native partial")])) as relation,
            patch.object(commands, "create", return_value=SimpleNamespace(error=0, command=0xC123)),
            patch.object(warships, "_validate_submit", AsyncMock(side_effect=accept)) as submit,
        ):
            result = await warships.transfer_ship(api, 8, 9)
        self.assertTrue(result["accepted"])
        self.assertIs(result["native_result"], native)
        self.assertIs(result["observed_fleet"], observed)
        self.assertIsNone(result["assigned_to_requested_fleet"])
        self.assertEqual(submit.await_count, 1)
        self.assertEqual(relation.await_count, 2)

if __name__ == "__main__":
    unittest.main()
