"""CPU-only original native state-name composition and ownership."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game.country.names import EDIT_NAMES
from pdx_runtime.query_executor import Diagnostics
from pdx_runtime.v3_game._run import Run as _Run
from pdx_runtime.v3_game.state_management import StateManagementAPI, StateNames, _STATE_NAME_FIELDS
from tests.v3_game import test_v3_game_state_management as state_fixture
from tests.v3_game.test_v3_game_state_management import command_result


def copied(raw=b"", *, error=0, total=None, kind=data.Kind.STRING):
    return N(action=data.Action.STATE_NAME_OVERRIDE, kind=kind, error=error,
        value_handle=0, snapshot=raw, snapshot_bytes=len(raw), raw_bits=0,
        total=len(raw) if total is None else total)


def edited(index, *, error=0, invoked=1, accepted=1):
    native = N(action=data.Action.INVOKE_ACTION, error=error, value_handle=301 + index,
        snapshot=b"original native snapshot", raw_bits=0, value=None)
    return data.ActionResult(native, 0, 0, 4, invoked, accepted, data.UTF8_INPUT)


class StateNamesTests(IsolatedAsyncioTestCase):
    def prepare(self, *, raw=None, **options):
        f = state_fixture.StateIncorporationActionTests.prepare(self, **options)
        f.ctx.country_id = AsyncMock(side_effect=AssertionError("do not replace native actor"))
        f.ctx.explicit_country_id = 999
        f.before = [copied(value) for value in (raw or (b"", b"old city", b"", b"farm", b"", b"wood"))]
        f.created = N(action=data.Action.CREATE_STATE_NAME_CONTEXT, error=0, value_handle=201,
            value=data.NativeObject(0x12345678, 73, 201, "native state-name context"))
        f.created_call = patch.object(data, "create_state_name_context", return_value=N(result=f.created)).start()
        f.read = patch.object(data, "state_name_override",
            side_effect=lambda client, identity, *, index, **kw: N(result=f.before[index])).start()
        f.edits = [edited(index) for index in range(6)]
        f.edit = patch.object(data, "invoke_action", side_effect=lambda client, expression, *a, **kw:
            N(result=f.edits[EDIT_NAMES.index(expression.split(".")[-1])])).start()
        f.draft = patch.object(data, "state_name_draft", side_effect=AssertionError("no readback audits")).start()
        return f

    async def test_read_six_raw_fields_preserves_empty_override_not_localized_fallback(self):
        f = self.prepare(raw=(b"", "城市".encode(), b"", b"farm", b"mine", b"wood"))
        result = await StateManagementAPI(f.ctx).names(7)
        self.assertIsInstance(result, StateNames)
        self.assertTrue(result.complete)
        self.assertEqual(result.values, dict(zip(_STATE_NAME_FIELDS, ("", "城市", "", "farm", "mine", "wood"))))
        self.assertEqual([call.kwargs["index"] for call in f.read.call_args_list], list(range(6)))
        self.assertIs(result.fields["city"], f.before[1])
        f.created_call.assert_not_called()
        f.edit.assert_not_called()
        self.assertFalse(any(isinstance(entry, _Run) for entry in f.ctx.owner_pool.snapshot()))

    async def test_partial_error_and_unexpected_kind_keep_original_fields(self):
        f = self.prepare()
        f.before[1] = copied(b"partial", error=5, total=20)
        f.before[2] = copied(kind=data.Kind.U32)
        result = await StateManagementAPI(f.ctx).names(7)
        self.assertFalse(result.complete)
        self.assertEqual(result.values["city"], "partial")
        self.assertIsNone(result.values["port"])
        self.assertIs(result.fields["city"], f.before[1])
        self.assertEqual(result.fields["port"].raw_bits, 0)

    async def test_complete_more_data_read_grows_to_exact_native_size_and_keeps_attempts(self):
        f = self.prepare()
        partial = copied(b"x" * 4096, error=234, total=5000)
        complete = copied(b"x" * 5000)
        attempts = iter((partial, complete))
        f.read.side_effect = lambda client, identity, *, index, **kw: N(
            result=next(attempts) if index == 1 else f.before[index])
        result = await StateManagementAPI(f.ctx).names(7)
        self.assertTrue(result.complete)
        self.assertEqual(result.attempts["city"], (partial, complete))
        self.assertEqual([c.kwargs["output_bytes"] for c in f.read.call_args_list if c.kwargs["index"] == 1], [4096, 5000])

    async def test_more_data_without_larger_requirement_does_not_spin(self):
        f = self.prepare()
        f.before[1] = copied(b"prefix", error=234, total=200)
        result = await StateManagementAPI(f.ctx).names(7)
        self.assertFalse(result.complete)
        self.assertEqual(f.read.call_count, 6)

    async def test_one_name_reads_only_five_preserved_raw_fields_and_restores_all_six(self):
        f = self.prepare()
        result = await StateManagementAPI(f.ctx).rename(7, city="新的城市")
        self.assertEqual(f.ctx.queries[0], "state_management.names.create_context")
        self.assertEqual([c.kwargs["index"] for c in f.read.call_args_list], [0, 2, 3, 4, 5])
        self.assertEqual([c.args[1] for c in f.edit.call_args_list], ["StateNameChangePopup." + h for h in EDIT_NAMES])
        self.assertEqual([c.args[2].text for c in f.edit.call_args_list], ["", "新的城市", "", "farm", "", "wood"])
        self.assertTrue(all(c.args[2].kind == data.UTF8_INPUT for c in f.edit.call_args_list))
        self.assertTrue(all(c.args[3] == (data.ObjectBinding(73, 201, retained=True),) for c in f.edit.call_args_list))
        self.assertEqual(result["desired"]["state"], b"")
        self.assertEqual(result["before"].requested_fields, ("state", "port", "farm", "mine", "wood"))
        f.evaluate.assert_called_once_with(f.ctx.native, "StateNameChangePopup.Confirm",
            (data.ObjectBinding(73, 201, retained=True),), output_bytes=65536,
            adapter=0x1234, process=f.ctx.process)
        self.assertEqual((f.owner.validations, f.owner.submissions), (1, 1))
        self.assertEqual(result["world"], 91)
        self.assertIs(result["native_result"], f.submission)
        self.assertTrue(result["accepted"])
        self.assertEqual(set(c.args[1] for c in f.release_call.call_args_list), {101, 201, *range(301, 307)})
        f.draft.assert_not_called()
        f.ctx.country_id.assert_not_called()
        self.assertEqual(f.ctx.types, [])
        self.assertFalse(any("serialize" in label for label in f.ctx.queries))
        self.assertFalse(any(isinstance(entry, _Run) for entry in f.ctx.owner_pool.snapshot()))

    async def test_all_six_explicit_names_have_zero_old_name_reads(self):
        f = self.prepare()
        names = dict(zip(_STATE_NAME_FIELDS, ("S", "C", "P", "F", "M", "W")))
        result = await StateManagementAPI(f.ctx).rename(7, **names)
        f.read.assert_not_called()
        self.assertEqual(result["before"].requested_fields, ())
        self.assertEqual([c.args[2].text for c in f.edit.call_args_list], list(names.values()))
        self.assertEqual(result["desired"], {k: v.encode() for k, v in names.items()})
        self.assertEqual((f.owner.validations, f.owner.submissions), (1, 1))

    async def test_no_requested_changes_is_not_an_accepted_command(self):
        f = self.prepare()
        result = await StateManagementAPI(f.ctx).rename(7)
        self.assertEqual(result["status"], "no_request")
        self.assertIsNone(result["accepted"])
        self.assertEqual(f.ctx.queries, [])
        f.created_call.assert_not_called()
        f.read.assert_not_called()

    async def test_empty_name_is_a_native_request_not_a_guarantee_of_permission(self):
        f = self.prepare(validation=command_result(validated=False, reason="native name condition"))
        result = await StateManagementAPI(f.ctx).rename(7, city="")
        self.assertEqual(f.edit.call_args_list[1].args[2], data.TypedInput.utf8_string(""))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "native name condition")
        self.assertEqual((f.owner.validations, f.owner.submissions), (1, 0))

    async def test_all_input_shapes_validated_before_every_native_call(self):
        f = self.prepare()
        api = StateManagementAPI(f.ctx)
        for kwargs, error in (({"wood": 1}, TypeError), ({"wood": "bad\0name"}, ValueError),
                              ({"wood": "bad\ud800"}, UnicodeEncodeError), ({"validate_only": 1}, TypeError)):
            with self.subTest(kwargs=kwargs), self.assertRaises(error):
                await api.rename(7, city="valid", **kwargs)
        for value in (True, -1, 2**32, None, "7"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await api.rename(value, city="valid")
        self.assertEqual(f.ctx.performs, [])
        f.created_call.assert_not_called()

    async def test_text_is_original_typed_input_not_expression_syntax(self):
        f = self.prepare()
        text = "(converter)城市'\\line\nend"
        await StateManagementAPI(f.ctx).rename(7, state=text)
        self.assertEqual(f.edit.call_args_list[0].args[2], data.TypedInput.utf8_string(text))
        self.assertNotIn(text, f.edit.call_args_list[0].args[1])

    async def test_original_creation_error_and_its_real_owner_are_preserved(self):
        f = self.prepare()
        f.created.error = 5
        result = await StateManagementAPI(f.ctx).rename(7, city="new")
        self.assertIs(result["native_result"], f.created)
        self.assertEqual(result["native_error"], 5)
        f.read.assert_not_called()
        f.edit.assert_not_called()
        f.release_call.assert_called_once_with(f.ctx.native, 201, adapter=0x1234, process=f.ctx.process)

    async def test_incomplete_preserved_name_does_not_submit_or_replace_with_display_text(self):
        f = self.prepare()
        f.before[2] = copied(b"native partial", error=5, total=20)
        result = await StateManagementAPI(f.ctx).rename(7, city="new")
        self.assertEqual(result["status"], "incomplete_original_overrides")
        self.assertIs(result["before"].fields["port"], f.before[2])
        f.edit.assert_not_called()
        f.evaluate.assert_not_called()
        self.assertEqual(f.owner.submissions, 0)
        f.release_call.assert_called_once()

    async def test_unselected_invalid_utf8_is_exposed_without_a_lossy_edit(self):
        f = self.prepare(raw=(b"", b"old", b"\xff", b"", b"", b""))
        result = await StateManagementAPI(f.ctx).rename(7, city="new")
        self.assertEqual(result["status"], "unrepresentable_original_override")
        self.assertEqual(result["field"], "port")
        self.assertEqual(result["before"].fields["port"].snapshot, b"\xff")
        f.edit.assert_not_called()

    async def test_unselected_nul_is_not_silently_truncated(self):
        f = self.prepare(raw=(b"", b"old", b"a\0b", b"", b"", b""))
        result = await StateManagementAPI(f.ctx).rename(7, city="new")
        self.assertEqual(result["status"], "unrepresentable_original_override")
        self.assertEqual(result["field"], "port")
        f.edit.assert_not_called()

    async def test_selected_replacement_does_not_require_old_invalid_bytes(self):
        f = self.prepare(raw=(b"", b"\xff", b"", b"", b"", b""))
        result = await StateManagementAPI(f.ctx).rename(7, city="valid")
        self.assertTrue(result["accepted"])
        self.assertNotIn(1, [call.kwargs["index"] for call in f.read.call_args_list])

    async def test_rejected_original_setter_stops_before_confirm_and_keeps_prior_edits(self):
        f = self.prepare()
        f.edits[2] = edited(2, error=50, invoked=0, accepted=0)
        result = await StateManagementAPI(f.ctx).rename(7, city="new")
        self.assertEqual(result["status"], "draft_edit_rejected")
        self.assertIs(result["native_result"], f.edits[2])
        self.assertEqual(tuple(result["edits"]), ("state", "city", "port"))
        self.assertEqual(f.edit.call_count, 3)
        f.evaluate.assert_not_called()
        self.assertEqual(f.owner.submissions, 0)
        self.assertEqual(set(c.args[1] for c in f.release_call.call_args_list), {201, 301, 302, 303})

    async def test_accepted_setter_with_copy_error_is_preserved_not_replayed(self):
        f = self.prepare()
        f.edits[1] = edited(1, error=234, invoked=1, accepted=1)
        result = await StateManagementAPI(f.ctx).rename(7, city="new")
        self.assertIs(result["edits"]["city"], f.edits[1])
        self.assertEqual(f.edit.call_count, 6)
        self.assertTrue(result["accepted"])
        self.assertEqual((f.owner.validations, f.owner.submissions), (1, 1))

    async def test_validate_only_validates_once_and_cleans_the_same_generated_owner(self):
        f = self.prepare()
        result = await StateManagementAPI(f.ctx).rename(7, city="new", validate_only=True)
        self.assertEqual((f.owner.validations, f.owner.submissions, f.owner.destructions), (1, 0, 1))
        self.assertIs(result["validation"], f.validation)
        self.assertIsNone(result["accepted"])
        self.assertEqual(f.release_call.call_count, 8)

    async def test_unknown_read_keeps_completed_fields_context_and_original_query(self):
        f = self.prepare()
        failure = RuntimeError("original read unavailable")
        failure.query = object()
        f.before[3] = failure
        with self.assertRaises(RuntimeError) as caught:
            await StateManagementAPI(f.ctx).rename(7, city="new")
        self.assertIs(caught.exception, failure)
        self.assertEqual(set(failure.state_names.fields), {"state", "port"})
        self.assertIs(failure.state_management_result["context"], f.created)
        original = next(entry for entry in f.ctx.owner_pool.snapshot() if isinstance(entry, _Run))
        self.assertIs(original.uncertain_query, failure.query)
        self.assertEqual(original.values, [201])
        f.release_call.assert_not_called()
        f.edit.assert_not_called()

    async def test_unknown_edit_keeps_same_context_and_completed_original_any_values(self):
        f = self.prepare()
        failure = RuntimeError("original edit pending")
        failure.query = object()
        f.edits[2] = failure
        with self.assertRaises(RuntimeError) as caught:
            await StateManagementAPI(f.ctx).rename(7, city="new")
        self.assertIs(caught.exception, failure)
        original = next(entry for entry in f.ctx.owner_pool.snapshot() if isinstance(entry, _Run))
        self.assertEqual(original.values, [201, 301, 302])
        self.assertIs(original.uncertain_query, failure.query)
        self.assertEqual(tuple(failure.state_management_result["edits"]), ("state", "city"))
        self.assertIn(("names.edit.city", f.edits[1]), failure.state_management_result["operations"])
        f.release_call.assert_not_called()
        f.evaluate.assert_not_called()

    async def test_unknown_create_survives_finished_task_and_diagnostics_eviction(self):
        f = self.prepare()
        f.ctx.diagnostics = Diagnostics()
        failure = RuntimeError("original create delivery unknown")
        failure.query = object()
        f.created_call.return_value = N(result=failure)
        task = asyncio.create_task(StateManagementAPI(f.ctx).rename(7, city="new"))
        with self.assertRaises(RuntimeError) as caught:
            await task
        self.assertIs(caught.exception, failure)
        drained = asyncio.Event()
        task.add_done_callback(lambda _: drained.set())
        await drained.wait()
        original = next(entry for entry in f.ctx.owner_pool.snapshot() if isinstance(entry, _Run))
        for index in range(100):
            f.ctx.diagnostics.append({"later": index})
        self.assertNotIn(original, f.ctx.diagnostics["operations"])
        self.assertIs(original.uncertain_query, failure.query)
        self.assertTrue(original.retain_unknown)
        self.assertEqual(original.values, [])
        f.created_call.assert_called_once()
        f.release_call.assert_not_called()

    async def test_unknown_submission_is_not_replayed_or_cleaned(self):
        failure = RuntimeError("original submission unknown")
        failure.query = object()
        f = self.prepare(submission=failure)
        with self.assertRaises(RuntimeError) as caught:
            await StateManagementAPI(f.ctx).rename(7, city="new")
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.state_management_result["validation"], f.validation)
        self.assertEqual(f.owner.submissions, 1)
        f.release_call.assert_not_called()

    async def test_cleanup_failure_keeps_original_acceptance_and_the_shared_pool_scope(self):
        f = self.prepare(release_error=5)
        result = await StateManagementAPI(f.ctx).rename(7, city="new")
        self.assertTrue(result["accepted"])
        self.assertIs(result["native_result"], f.submission)
        self.assertIs(result["cleanup"]["native_result"], f.release)
        original = next(entry for entry in f.ctx.owner_pool.snapshot() if isinstance(entry, _Run))
        self.assertEqual(original.values, [201, 301, 302, 303, 304, 305, 306, 101])
        self.assertFalse(hasattr(f.ctx, "_state_management_runs"))
        self.assertFalse(hasattr(f.ctx, "_retained_native_runs"))

    async def test_all_name_operations_and_release_use_the_original_process_snapshot(self):
        f = self.prepare()
        native, process, adapter = f.ctx.native, f.ctx.process, f.ctx.adapters["data"]
        query = f.ctx.query
        switched = False
        async def switch(label, factory, *, owner=None):
            nonlocal switched
            if not switched:
                switched = True
                f.ctx.native, f.ctx.process, f.ctx.adapters["data"] = object(), object(), 0x9999
            return await query(label, factory, owner=owner)
        f.ctx.query = switch
        await StateManagementAPI(f.ctx).rename(7, city="new")
        for operation in (f.created_call, f.read, f.edit, f.evaluate, f.extract, f.release_call):
            for call in operation.call_args_list:
                self.assertIs(call.args[0], native)
                self.assertIs(call.kwargs["process"], process)
                self.assertEqual(call.kwargs["adapter"], adapter)

    async def test_original_command_acceptance_survives_nonzero_native_copy_error(self):
        submission = command_result(error=5, accepted=True)
        f = self.prepare(submission=submission)
        result = await StateManagementAPI(f.ctx).rename(7, city="new")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["native_error"], 5)
        self.assertIs(result["native_result"], submission)
        self.assertEqual((f.owner.validations, f.owner.submissions), (1, 1))
        self.assertEqual(f.edit.call_count, 6)
