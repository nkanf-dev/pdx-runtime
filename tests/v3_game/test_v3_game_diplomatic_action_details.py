"""Post-cut3 bounded copied action details; no command or selector mutation.

Uses genuine original General choices with real shared owner/executor protocols.
Only new tests run; old selected-action fixtures are imported as transport data.
"""
import asyncio
from contextlib import ExitStack
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.query_executor import PendingOperation
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game.diplomacy import actions as a
from pdx_runtime.v3_game._choices import OwnedChoice, ChoicesBusyError
from tests.v3_game.test_v3_game_diplomatic_selected import Context as BaseContext, Fixture as BaseFixture, Callback, TARGET, EXACT_RTTI


class Context(BaseContext):
    def __init__(self):
        super().__init__()
        self.loop = asyncio.get_running_loop()
        self.details_started = asyncio.Event()
    async def perform(self, label, operation):
        self.performs.append(label)
        return await self.executor.perform(label, operation)


class GateCallback(Callback):
    def read(self):
        if self.gate is not None and not self.gate.is_set():
            return None
        return super().read()


class Fixture(BaseFixture):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.details_calls, self.details_pages = [], []
        self.detail_values, self.detail_cells = {}, {}
        self.detail_error, self.detail_valid, self.detail_empty = 0, True, False
        self.detail_exception, self.detail_warning, self.detail_gate = None, None, None
        self.detail_query = None
    def project(self, client, fields, binding_rows, **kwargs):
        if tuple(fields) != tuple(field.expression for field in a._DIPLOMATIC_ACTION_DETAILS_FIELDS):
            return super().project(client, fields, binding_rows, **kwargs)
        self.details_calls.append((tuple(fields), tuple(binding_rows), dict(kwargs)))
        if self.detail_exception:
            raise self.detail_exception
        assert len(binding_rows) == 1 and len(binding_rows[0]) == 1
        retained = binding_rows[0][0]
        assert retained.retained and retained.object in self.handles
        assert retained.type_id == self.row_type_id
        assert kwargs.get("source_expression") is None and not kwargs.get("steps")
        values = {"native_key": "actual_native_key", "native_action_id": self.identities[retained.object],
            "first_country_id": 0, "second_country_id": TARGET,
            "first_state_id": 0, "first_state_name": "#bold First state#!",
            "second_state_id": 0x80000004, "second_state_name": "Second state",
            "calling_in_obligation": False, "promising_obligation": True,
            "can_potentially_owe_obligation": False, "requires_confirmation": True,
            "target_states_description": "#bold Native target states#!", "ai_will_accept": False,
            "ai_will_accept_description": "#bold Native current answer#!",
            "ai_acceptance_breakdown": "Native current breakdown",
            "type_has_modify_pact": False, "type_has_reverse_pact": True, "type_has_transfer_pact": False}
        values.update(self.detail_values)
        cells = []
        if not self.detail_empty:
            for index, field in enumerate(a._DIPLOMATIC_ACTION_DETAILS_FIELDS):
                if field.name in self.detail_cells:
                    cells.append(self.detail_cells[field.name])
                    continue
                value = values[field.name]
                kind = field.kind if value is not None else data.Kind.EMPTY
                raw = value.encode() if isinstance(value, str) else b""
                bits = int(value) & 0xFFFFFFFFFFFFFFFF if type(value) in (int, bool) else 0
                cells.append(data.ProjectionCell(0, index, 0, kind, self.row_type_id, bits, raw, len(raw)))
        rows = 0 if self.detail_empty else 1
        page = data.ProjectionPage(self.detail_error, 0x9001, len(fields), 0, 17,
            rows, self.detail_valid, rows, tuple(cells))
        self.details_pages.append(page)
        query = GateCallback(page, adapter=kwargs["adapter"])
        query.gate, query.cleanup_error = self.detail_gate, self.detail_warning
        self.detail_query = query
        self.ctx.loop.call_soon_threadsafe(self.ctx.details_started.set)
        return query


class DiplomaticActionDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ctx = Context()
        self.api, self.f = DiplomacyAPI(self.ctx), Fixture(self.ctx)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, method in (("evaluate", self.f.evaluate), ("list_count", self.f.list_count),
                ("list_element", self.f.list_element), ("project_page", self.f.project),
                ("release", self.f.release)):
            self.stack.enter_context(patch.object(data, name, side_effect=method))
        self.forbidden = [self.stack.enter_context(patch.object(module, name)) for module, name in
            ((commands, "create"), (commands, "request"), (data, "command_value"),
             (data, "invoke_action"), (data, "invoke_property_action"))]
    async def catalog(self):
        return await self.api.diplomatic_action_choices(TARGET)
    def no_mutation(self):
        for call in self.forbidden:
            call.assert_not_called()
        self.assertEqual(self.f.command_calls, [])

    async def test_one_explicit_extra_read_same_original_binding_actual_key_parties_and_id(self):
        catalog = await self.catalog()
        selected = catalog[1]
        self.assertEqual(selected.native_result.kind, data.Kind.RAW)
        self.assertEqual(selected.native_result.native_type_name, EXACT_RTTI)
        self.assertEqual(catalog.owner.value_adapters[selected.native_result.value_handle], "original-data")
        self.f.rows = [("owned", 999)]
        self.f.events.clear()
        result = await self.api.diplomatic_action_details(selected)
        row = result["row"]
        self.assertEqual((row["native_key"], row["native_action_id"]), ("actual_native_key", 22))
        self.assertEqual((row["first_country_id"], row["second_country_id"]), (0, TARGET))
        self.assertEqual((row["first_state_id"], row["second_state_id"]), (0, 0x80000004))
        self.assertEqual(row["first_state_name_raw"], "#bold First state#!")
        self.assertEqual(row["first_state_name"], "First state")
        self.assertTrue(result["read_complete"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["status"], "observed")
        self.assertIs(result["source_native_result"], selected.native_result)
        self.assertEqual(len(self.f.details_calls), 1)
        fields, bindings, options = self.f.details_calls[0]
        self.assertEqual(bindings, ((selected._binding,),))
        self.assertEqual(fields, tuple(field.expression for field in a._DIPLOMATIC_ACTION_DETAILS_FIELDS))
        self.assertEqual(options["limit"], 64)
        self.assertLessEqual(len(fields), 32)
        self.assertEqual(self.ctx.country_reads, 0)
        self.assertEqual(self.ctx.performs[-1], "diplomacy.diplomatic_action_details")
        self.assertFalse(any(event[0] in (a._GENERAL_SOURCE, "count", "element") for event in self.f.events))
        self.no_mutation()
        await catalog.close()

    async def test_native_zero_false_and_open_selectors_do_not_block_clean_read(self):
        catalog = await self.catalog()
        self.f.detail_values.update(native_action_id=0, first_country_id=0, second_country_id=0,
            first_state_id=0, second_state_id=0, calling_in_obligation=False,
            promising_obligation=False, can_potentially_owe_obligation=False,
            requires_confirmation=False, ai_will_accept=False, type_has_modify_pact=False,
            type_has_reverse_pact=False, type_has_transfer_pact=False, ai_acceptance_breakdown="")
        result = await self.api.diplomatic_action_details(catalog[0])
        row = result["row"]
        self.assertEqual(row["native_action_id"], 0)
        self.assertEqual(row["first_state_id"], 0)
        self.assertFalse(row["calling_in_obligation"])
        self.assertFalse(row["requires_confirmation"])
        self.assertFalse(row["ai_will_accept"])
        self.assertEqual(row["ai_acceptance_breakdown_raw"], "")
        self.assertTrue(result["complete"])
        self.assertEqual(result["reasons"], [])
        remaining = ["intent", "pact", "proposal_accept_decline", "stop"]
        supported = ["obligation", "first_state", "second_state", "country_only"]
        self.assertEqual(result["unclosed_selectors"], remaining)
        self.assertEqual(result["supported_command_edits"], supported)
        self.assertEqual(catalog.metadata["result"]["unclosed_selectors"], remaining)
        self.assertEqual(catalog.metadata["result"]["supported_command_edits"], supported)
        for report in (result, catalog.metadata["result"]):
            self.assertIn("original General row", report["read_scope"])
            self.assertIn("separate command", report["command_edit_scope"])
            self.assertIn("not in-place source or Window setters", report["command_edit_scope"])
            self.assertIn("not edited-selector readback", report["command_edit_scope"])
        self.assertFalse(result["command_validation_performed"])
        self.assertFalse(result["selector_mutation_performed"])
        self.assertNotIn("intent", result)
        self.assertNotIn("stop_intent", row)
        self.no_mutation()
        await catalog.close()

    async def test_action_uint32_id_is_not_reclassified_as_full_reference_or_pact_id(self):
        catalog = await self.catalog()
        self.f.detail_values["native_action_id"] = 0xFFFFFFFF
        result = await self.api.diplomatic_action_details(catalog[0])
        self.assertEqual(result["row"]["native_action_id"], 0xFFFFFFFF)
        self.assertNotIn("native_action_id", result["row"]["reasons"])
        self.assertTrue(result["read_complete"])
        self.assertIn("not persistent/full-reference/Pact", result["native_action_id_semantics"])
        self.assertNotIn("pact_id", result["row"])
        self.no_mutation()
        await catalog.close()

    async def test_optional_state_null_or_invalid_sentinel_keeps_the_selected_action(self):
        catalog = await self.catalog()
        self.f.detail_values.update(first_state_id=None, first_state_name=None, second_state_id=0xFFFFFFFF)
        result = await self.api.diplomatic_action_details(catalog[0])
        row = result["row"]
        self.assertIsNone(row["first_state_id"])
        self.assertIsNone(row["first_state_name_raw"])
        self.assertEqual(row["second_state_id"], 0xFFFFFFFF)
        self.assertEqual(row["native_action_id"], 11)
        self.assertIn("first_state_id", row["reasons"])
        self.assertIn("second_state_id", row["reasons"])
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["complete"])
        self.assertIs(result["source_native_result"], catalog[0].native_result)
        self.assertEqual(len(catalog), 2)
        self.no_mutation()
        await catalog.close()

    async def test_native_null_flag_and_error_values_are_not_fake_negative_or_absence(self):
        catalog = await self.catalog()
        self.f.detail_values.update(requires_confirmation=None, can_potentially_owe_obligation=None)
        i = next(i for i, f in enumerate(a._DIPLOMATIC_ACTION_DETAILS_FIELDS) if f.name == "ai_will_accept")
        self.f.detail_cells["ai_will_accept"] = data.ProjectionCell(0, i, 87, data.Kind.BOOL, 177, 1, b"", 0)
        result = await self.api.diplomatic_action_details(catalog[0])
        row = result["row"]
        self.assertIsNone(row["requires_confirmation"])
        self.assertIsNone(row["can_potentially_owe_obligation"])
        self.assertTrue(row["ai_will_accept"])
        self.assertIn("ai_will_accept", row["reasons"])
        self.assertFalse(result["read_complete"])
        self.assertEqual(row["native_key"], "actual_native_key")
        self.no_mutation()
        await catalog.close()

    async def test_partial_text_global_error_and_original_bytes_remain_visible(self):
        catalog = await self.catalog()
        i = next(i for i, f in enumerate(a._DIPLOMATIC_ACTION_DETAILS_FIELDS) if f.name == "ai_acceptance_breakdown")
        raw = b"#bold kept\xff#!"
        self.f.detail_cells["ai_acceptance_breakdown"] = data.ProjectionCell(0, i, 122,
            data.Kind.STRING, 177, 0, raw, 100)
        self.f.detail_error = 122
        result = await self.api.diplomatic_action_details(catalog[0])
        self.assertEqual(result["row"]["native_action_id"], 11)
        self.assertIn("kept", result["row"]["ai_acceptance_breakdown"])
        self.assertIn("ai_acceptance_breakdown", result["row"]["reasons"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["native_pages"][0].error, 122)
        self.assertEqual(result["native_pages"][0].cells[i].snapshot, raw)
        self.no_mutation()
        await catalog.close()

    async def test_empty_native_descriptor_copy_is_partial_row_not_no_action(self):
        catalog = await self.catalog()
        self.f.detail_empty = True
        result = await self.api.diplomatic_action_details(catalog[0])
        self.assertIn("row", result)
        self.assertIsNone(result["row"]["native_action_id"])
        self.assertTrue(result["row"]["reasons"])
        self.assertFalse(result["read_complete"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(catalog), 2)
        self.no_mutation()
        await catalog.close()

    async def test_prior_catalog_partial_quality_does_not_add_an_extra_read_gate(self):
        self.f.copy_error = 122
        catalog = await self.catalog()
        self.assertFalse(catalog.metadata["result"]["complete"])
        result = await self.api.diplomatic_action_details(catalog[0])
        self.assertTrue(result["complete"])
        self.assertTrue(result["read_complete"])
        self.assertEqual(result["row"]["native_action_id"], 11)
        self.no_mutation()
        await catalog.close()

    async def test_current_confirmation_ai_and_type_flags_are_only_native_current_values(self):
        catalog = await self.catalog()
        result = await self.api.diplomatic_action_details(catalog[0])
        row = result["row"]
        self.assertTrue(row["requires_confirmation"])
        self.assertFalse(row["ai_will_accept"])
        self.assertTrue(row["promising_obligation"])
        self.assertTrue(row["type_has_reverse_pact"])
        self.assertFalse(row["type_has_modify_pact"])
        self.assertEqual(row["ai_will_accept_description_raw"], "#bold Native current answer#!")
        self.assertTrue(result["complete"])
        self.assertEqual(result["reasons"], [])
        self.assertNotIn("eligible", result)
        self.assertNotIn("accepted", result)
        self.no_mutation()
        await catalog.close()

    async def test_no_auxiliary_type_or_actor_lookup_and_original_adapter_is_kept(self):
        catalog = await self.catalog()
        self.ctx._types.pop("DiplomaticAction")
        self.ctx.adapters["data"] = "later-unrelated-data"
        result = await self.api.diplomatic_action_details(catalog[0])
        self.assertTrue(result["complete"])
        self.assertEqual(self.f.details_calls[0][2]["adapter"], "original-data")
        self.assertEqual(self.ctx.country_reads, 0)
        self.assertNotIn("DiplomaticAction", self.ctx.type_reads)
        self.no_mutation()
        await catalog.close()

    async def test_invalid_unadmitted_closed_and_foreign_choices_fail_before_extra_copy(self):
        for value in (0, "increase_relations", {"native_key": "copied"}):
            with self.assertRaises(TypeError):
                await self.api.diplomatic_action_details(value)
        catalog = await self.catalog()
        selected = catalog[0]
        fake = OwnedChoice(catalog, selected.native_result, "DiplomaticAction", selected._binding, dict(selected))
        with self.assertRaises(ValueError):
            await self.api.diplomatic_action_details(fake)
        with self.assertRaises(ValueError):
            await DiplomacyAPI(Context()).diplomatic_action_details(selected)
        await catalog.close()
        with self.assertRaises(RuntimeError):
            await self.api.diplomatic_action_details(selected)
        self.assertEqual(self.f.details_calls, [])
        self.no_mutation()

    async def test_original_copy_exception_retains_source_graph_without_relookup_or_cleanup(self):
        catalog = await self.catalog()
        error = RuntimeError("original native copied details unknown")
        self.f.detail_exception = error
        with self.assertRaises(RuntimeError) as caught:
            await self.api.diplomatic_action_details(catalog[0])
        self.assertIs(caught.exception, error)
        self.assertIs(error.choices, catalog)
        self.assertEqual(len(self.f.details_calls), 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertFalse(self.f.released)
        self.no_mutation()

    async def test_original_pending_copy_query_is_resumed_once_and_keeps_parent_lease(self):
        catalog = await self.catalog()
        self.f.detail_gate = threading.Event()
        self.ctx.executor.timeout = 0
        pending = await self.api.diplomatic_action_details(catalog[0])
        self.assertIsInstance(pending, PendingOperation)
        await asyncio.wait_for(self.ctx.details_started.wait(), 1)
        original = self.f.detail_query
        try:
            self.assertIn(original, self.ctx.owner_pool.snapshot())
            with self.assertRaises(ChoicesBusyError):
                await catalog.close()
        finally:
            self.f.detail_gate.set()
        result = await pending.resume(timeout=1)
        self.assertTrue(result["complete"])
        self.assertIs(self.f.detail_query, original)
        self.assertEqual(len(self.f.details_calls), 1)
        self.assertEqual(catalog.active_leases, 0)
        self.no_mutation()
        await catalog.close()

    async def test_cleanup_warning_is_separate_from_clean_copied_results(self):
        catalog = await self.catalog()
        warning = OSError("native copied buffer cleanup warning")
        self.f.detail_warning = warning
        result = await self.api.diplomatic_action_details(catalog[0])
        self.assertTrue(result["complete"])
        self.assertEqual(result["row"]["native_action_id"], 11)
        self.assertIs(result["cleanup_warnings"][0], warning)
        self.no_mutation()
        await catalog.close()

    def test_descriptor_actual_signatures_kinds_and_unchanged_default_bound(self):
        fields = {field.name: field for field in a._DIPLOMATIC_ACTION_DETAILS_FIELDS}
        self.assertEqual(len(a.DIPLOMATIC_ACTION_FIELDS), 32)
        self.assertEqual(len(fields), 19)
        self.assertEqual(fields["native_action_id"].expression, "DiplomaticAction.GetID")
        self.assertEqual(fields["native_action_id"].kind, data.Kind.U32)
        self.assertFalse(fields["native_action_id"].full_id)
        self.assertEqual(fields["first_state_id"].expression, "DiplomaticAction.GetFirstState.GetID")
        self.assertTrue(fields["first_state_id"].full_id)
        self.assertEqual(fields["requires_confirmation"].kind, data.Kind.BOOL)
        for name in ("modify", "reverse", "transfer"):
            self.assertEqual(fields["type_has_" + name + "_pact"].expression,
                "DiplomaticAction.GetType.Has" + name.title() + "Pact")
        self.assertNotIn("DiplomaticAction.IsActivePact", [field.expression for field in fields.values()])
        self.no_mutation()


if __name__ == "__main__":
    unittest.main()
