"""CPU-only original Setup source/action composition; no game/native execution."""
from __future__ import annotations

from collections import deque
from dataclasses import fields, replace
from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch

from pdx_runtime import v3_access as access, v3_data as data
from pdx_runtime.v3_game._run import Run as _Run
from pdx_runtime.v3_game.setup import SetupAPI, SetupPage, SetupRow


class Context:
    def __init__(self):
        self.native = object()
        self.process = object()
        self.world = 0
        self.adapters = {"data": 0x1234}
        self.diagnostics = deque()
        self.queries = []
        self.performs = []
        self.remembered = []

    async def query(self, label, factory, *, owner=None):
        self.queries.append(label)
        query = factory()
        if isinstance(query.result, BaseException):
            raise query.result
        return query.result

    async def perform(self, label, operation):
        self.performs.append(label)
        return await operation()

    def _remember(self, query):
        self.remembered.append(query)


def action_result(*, error=0, invoked=1, accepted=1, handle=101):
    native = N(action=data.Action.INVOKE_ACTION, error=error, value_handle=handle,
               snapshot=b"native bytes", snapshot_bytes=len(b"native bytes"),
               total=len(b"native bytes"), raw_bits=0, value=None)
    return data.ActionResult(native, 0, 0, 4, invoked, accepted, data.UTF8_INPUT)


def preset_page(*, start=0, count=2, total=2, error=0, world=0):
    cells = []
    for row in range(start, start + count):
        raw = ("原名" + str(row)).encode()
        cells.append(data.ProjectionCell(row, 0, 0, data.Kind.STRING, 1, 0, raw, len(raw)))
        cells.append(data.ProjectionCell(row, 1, 0, data.Kind.BOOL, 1, 0, b"", 0))
    return data.ProjectionPage(error, world, 2, start, 77, total, True, count, tuple(cells))


class SetupCopiedPageTests(TestCase):
    def test_unknown_group_continuation_is_not_false_or_complete(self):
        native = replace(preset_page(count=0, total=0), grouped=True, _group_has_more=None)
        view = SetupPage("presets", ("name", "ironman"), native)
        self.assertIsNone(view.has_more)
        self.assertFalse(view.enumeration_complete)

    def test_native_source_ordinals_and_false_ironman_are_not_missing_or_ids(self):
        page = preset_page(start=64, count=2, total=100)
        view = SetupPage("presets", ("name", "ironman"), page)
        self.assertEqual([row.source_index for row in view.rows], [64, 65])
        self.assertEqual(view.rows[0].values["name"], "原名64")
        self.assertIs(view.rows[0].values["ironman"], False)
        self.assertEqual(view.next, 66)
        self.assertTrue(view.has_more)
        self.assertFalse(view.enumeration_complete)

    def test_current_empty_source_can_be_fully_enumerated_without_claiming_absence(self):
        page = preset_page(count=0, total=0)
        view = SetupPage("presets", ("name", "ironman"), page)
        self.assertEqual(view.rows, ())
        self.assertTrue(view.enumeration_complete)
        self.assertIs(view.native_page, page)

    def test_partial_native_text_and_getter_errors_keep_values_and_raw_bytes(self):
        page = preset_page(count=1, total=1, error=234)
        raw = "原名".encode()[:4]
        cells = (replace(page.cells[0], snapshot=raw, total=20, error=234), page.cells[1])
        page = replace(page, cells=cells)
        view = SetupPage("presets", ("name", "ironman"), page)
        self.assertTrue(view.rows[0].values["name"].startswith("原"))
        self.assertEqual(view.rows[0].cells["name"].snapshot, raw)
        self.assertIn("234", view.rows[0].field_issues["name"])
        self.assertFalse(view.enumeration_complete)

    def test_copied_cells_survive_even_if_native_counts_are_partial(self):
        page = replace(preset_page(count=1, total=1), row_count=0, count_valid=False, error=5)
        view = SetupPage("presets", ("name", "ironman"), page)
        self.assertEqual(len(view.rows), 1)
        self.assertEqual(view.rows[0].values["name"], "原名0")
        self.assertFalse(view.enumeration_complete)


class SetupAPITests(IsolatedAsyncioTestCase):
    async def test_presets_borrow_and_copy_original_provider_in_one_shared_callback(self):
        ctx = Context()
        page = preset_page()
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)) as native:
            view = await SetupAPI(ctx).presets()
        native.assert_called_once_with(ctx.native,
            ("GuiGameRulePreset.GetName", "GuiGameRulePreset.IsIronmanEnabled"), ((),),
            source_expression="GetGameRules.GetRulePresets", start=0, limit=64,
            steps=(), source_arguments=(), adapter=0x1234, process=ctx.process)
        self.assertIs(view.native_page, page)
        self.assertEqual(ctx.queries, ["setup.presets"])
        self.assertEqual(ctx.performs, ["setup.presets"])


    async def test_world0_native_not_ready_is_returned_without_unsafe_list_fallback(self):
        ctx = Context()
        page = data.ProjectionPage(21, 0, 2, 0, 77, 0, False, 0, ())
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)),                 patch.object(data, "evaluate") as evaluate, patch.object(data, "list_element") as element:
            view = await SetupAPI(ctx).presets()
        self.assertEqual(view.native_page.error, 21)
        self.assertFalse(view.enumeration_complete)
        evaluate.assert_not_called()
        element.assert_not_called()
        self.assertEqual(ctx.diagnostics[-1]["projection_page"], page)

    async def test_draft_uses_one_global_field_page_without_country_type_lookup(self):
        ctx = Context()
        page = replace(preset_page(count=0, total=0), fields_count=6)
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)) as native:
            await SetupAPI(ctx).draft()
        self.assertEqual(native.call_args.kwargs["source_expression"], None)
        self.assertIn("GetGameRules.GetPresetSaveName", native.call_args.args[1])
        self.assertIn("GetGameRules.GetSelectedPresetIndex", native.call_args.args[1])
        self.assertEqual(native.call_args.kwargs["limit"], 1)

    async def test_original_cutf8string_edit_handler_receives_name_as_data_not_syntax(self):
        names = ("O'Brien", "A\\B", "(converter)payload", "中é文", "line1\nline2", "")
        for name in names:
            with self.subTest(name=name):
                ctx = Context()
                source = action_result()
                release_result = N(action=data.Action.RELEASE, error=0, value_handle=0)
                with patch.object(data, "invoke_action", return_value=N(result=source)) as invoke,                         patch.object(data, "invoke_property_action") as property_action,                         patch.object(data, "release", return_value=N(result=release_result)) as release:
                    receipt = await SetupAPI(ctx).set_preset_name(name)
                invoke.assert_called_once_with(ctx.native, "GetGameRules.OnPresetSaveNameEdit",
                    data.TypedInput.utf8_string(name), adapter=0x1234, process=ctx.process)
                property_action.assert_not_called()
                release.assert_called_once_with(ctx.native, 101, adapter=0x1234, process=ctx.process)
                self.assertEqual(receipt["requested_name"], name)
                self.assertIs(receipt["native_result"], source)

    async def test_name_nul_or_invalid_utf8_rejected_before_every_native_call(self):
        ctx = Context()
        with patch.object(data, "invoke_action") as invoke:
            with self.assertRaises(ValueError):
                await SetupAPI(ctx).set_preset_name("prefix\0suffix")
            with self.assertRaises(UnicodeEncodeError):
                await SetupAPI(ctx).set_preset_name("bad\ud800")
        invoke.assert_not_called()
        self.assertEqual(ctx.performs, [])

    async def test_save_current_draft_is_one_original_action_not_implicit_rename(self):
        ctx = Context()
        source = action_result(handle=0)
        with patch.object(data, "invoke_property_action", return_value=N(result=source)) as invoke,                 patch.object(data, "invoke_action") as edit:
            receipt = await SetupAPI(ctx).save_preset()
        invoke.assert_called_once_with(ctx.native, "GetGameRules.SavePreset",
            adapter=0x1234, process=ctx.process)
        edit.assert_not_called()
        self.assertIs(receipt["native_result"], source)
        self.assertNotIn("persistence_complete", receipt)
        self.assertNotIn("requested_name", receipt)

    async def test_delete_current_selection_reads_index_in_same_original_expression(self):
        ctx = Context()
        source = action_result(handle=0)
        with patch.object(data, "invoke_property_action", return_value=N(result=source)) as invoke,                 patch.object(data, "invoke_action") as select:
            receipt = await SetupAPI(ctx).delete_selected_preset()
        invoke.assert_called_once_with(ctx.native,
            "GetGameRules.DeletePresetAtIndex(GetGameRules.GetSelectedPresetIndex)",
            adapter=0x1234, process=ctx.process)
        select.assert_not_called()
        self.assertIs(receipt["native_result"], source)

    async def test_delete_current_draft_name_does_not_implicitly_stage_or_apply_a_preset(self):
        ctx = Context()
        source = action_result(handle=0)
        with patch.object(data, "invoke_property_action", return_value=N(result=source)) as invoke,                 patch.object(data, "invoke_action") as edit_or_select:
            await SetupAPI(ctx).delete_draft_named_preset()
        invoke.assert_called_once_with(ctx.native,
            "GetGameRules.DeletePresetWithName(GetGameRules.GetPresetSaveName)",
            adapter=0x1234, process=ctx.process)
        edit_or_select.assert_not_called()

    async def test_action_error_and_native_acceptance_are_both_preserved_without_replay(self):
        ctx = Context()
        source = action_result(error=234, invoked=1, accepted=1)
        release_result = N(action=data.Action.RELEASE, error=0, value_handle=0)
        copied = N(action=data.Action.SNAPSHOT, error=0, value_handle=101,
            snapshot=b"native bytes", snapshot_bytes=len(b"native bytes"), total=len(b"native bytes"))
        with patch.object(data, "invoke_property_action", return_value=N(result=source)) as invoke, \
                patch.object(data, "snapshot", return_value=N(result=copied)) as snapshot, \
                patch.object(data, "release", return_value=N(result=release_result)) as release:
            receipt = await SetupAPI(ctx).save_preset()
        self.assertIs(receipt["native_result"], source)
        self.assertEqual((source.error, source.invoked, source.accepted), (234, 1, 1))
        self.assertEqual(source.result.snapshot, b"native bytes")
        self.assertIs(receipt["snapshot_attempts"][0]["native_result"], copied)
        invoke.assert_called_once()
        snapshot.assert_called_once()
        release.assert_called_once()

    async def test_original_declined_or_not_invoked_zero_fields_are_not_filtered(self):
        ctx = Context()
        source = action_result(error=0, invoked=0, accepted=0, handle=0)
        with patch.object(data, "invoke_property_action", return_value=N(result=source)):
            receipt = await SetupAPI(ctx).save_preset()
        self.assertIs(receipt["native_result"], source)
        self.assertEqual((source.error, source.invoked, source.accepted), (0, 0, 0))

    async def test_cleanup_error_keeps_original_action_result(self):
        ctx = Context()
        source = action_result()
        failure = N(action=data.Action.RELEASE, error=5, value_handle=101)
        with patch.object(data, "invoke_property_action", return_value=N(result=source)),                 patch.object(data, "release", return_value=N(result=failure)):
            receipt = await SetupAPI(ctx).save_preset()
        self.assertIs(receipt["native_result"], source)
        self.assertIs(receipt["cleanup"]["native_result"], failure)

    async def test_unknown_action_query_keeps_original_exception_without_any_cleanup_or_retry(self):
        ctx = Context()
        failure = RuntimeError("original action copy unknown")
        failure.query = object()
        original = failure.query
        with patch.object(data, "invoke_action", return_value=N(result=failure)) as invoke,                 patch.object(data, "release") as release:
            with self.assertRaises(RuntimeError) as caught:
                await SetupAPI(ctx).set_preset_name("选名")
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.query, original)
        self.assertEqual(failure.setup_result["requested_name"], "选名")
        invoke.assert_called_once()
        release.assert_not_called()

    async def test_invalid_page_bounds_do_not_submit_any_query(self):
        ctx = Context()
        with patch.object(data, "project_page") as native:
            for start, limit in ((-1, 1), (0, 0), (0, 65), (True, 1), (0, True)):
                with self.subTest(start=start, limit=limit), self.assertRaises(ValueError):
                    await SetupAPI(ctx).presets(start=start, limit=limit)
        native.assert_not_called()

    async def test_cleanup_unknown_scope_survives_diagnostic_eviction_without_domain_registry(self):
        from pdx_runtime.query_executor import Diagnostics
        ctx = Context()
        ctx.diagnostics = Diagnostics()
        source = action_result()
        failure = N(action=data.Action.RELEASE, error=5, value_handle=101)
        with patch.object(data, "invoke_property_action", return_value=N(result=source)),                 patch.object(data, "release", return_value=N(result=failure)):
            result = await SetupAPI(ctx).save_preset()
        self.assertIs(result["native_result"], source)
        self.assertEqual(len([entry for entry in ctx.owner_pool.snapshot() if isinstance(entry, _Run)]), 1)
        original = next(entry for entry in ctx.owner_pool.snapshot() if isinstance(entry, _Run))
        for index in range(100):
            ctx.diagnostics.append({"later": index})
        self.assertNotIn(original, ctx.diagnostics["operations"])
        self.assertEqual([entry for entry in ctx.owner_pool.snapshot() if isinstance(entry, _Run)], [original])
        self.assertEqual(original.values, [101])
        self.assertFalse(hasattr(ctx, "_setup_runs"))

    async def test_successful_action_scope_is_removed_from_shared_context_storage(self):
        ctx = Context()
        source = action_result()
        released = N(action=data.Action.RELEASE, error=0, value_handle=0)
        with patch.object(data, "invoke_property_action", return_value=N(result=source)),                 patch.object(data, "release", return_value=N(result=released)):
            await SetupAPI(ctx).save_preset()
        self.assertEqual([entry for entry in ctx.owner_pool.snapshot() if isinstance(entry, _Run)], [])

    async def test_action_and_value_release_use_same_original_run_process_and_adapter(self):
        ctx = Context()
        original_native, original_process, original_adapter = ctx.native, ctx.process, ctx.adapters["data"]
        source = action_result()
        released = N(action=data.Action.RELEASE, error=0, value_handle=0)
        original_query = ctx.query
        switched = False
        async def switching_query(label, factory, *, owner=None):
            nonlocal switched
            if not switched:
                switched = True
                ctx.native, ctx.process = object(), object()
                ctx.adapters["data"] = 0x9999
            return await original_query(label, factory, owner=owner)
        ctx.query = switching_query
        with patch.object(data, "invoke_action", return_value=N(result=source)) as invoke,                 patch.object(data, "release", return_value=N(result=released)) as release:
            await SetupAPI(ctx).set_preset_name("original")
        invoke.assert_called_once_with(original_native, "GetGameRules.OnPresetSaveNameEdit",
            data.TypedInput.utf8_string("original"), adapter=original_adapter, process=original_process)
        release.assert_called_once_with(original_native, 101,
            adapter=original_adapter, process=original_process)


def preset_application(*, index=0, error=0, invoked=1, accepted=1, count=3, world=0):
    native = N(action=data.Action.INVOKE_SOURCE_INDEX_ACTION, error=error, kind=data.Kind.I32,
        value_handle=0, snapshot=b"native copied prefix", raw_bits=index, value=index)
    action = data.ActionResult(native, 0, 0, 4, invoked, accepted, data.Kind.I32)
    return data.ElementResult(action, world, 0, 0, 0, 0, 77, 0, 0, count)


class SetupPresetApplyTests(IsolatedAsyncioTestCase):
    async def test_one_original_source_and_action_share_source_index_callback(self):
        ctx = Context()
        native = preset_application(index=2)
        with patch.object(data, "invoke_source_index_action", return_value=N(result=native)) as action, \
                patch.object(data, "list_count") as count, patch.object(data, "list_element") as element, \
                patch.object(data, "invoke_action") as direct, patch.object(data, "release") as release:
            result = await SetupAPI(ctx).apply_preset(2)
        self.assertIs(result["native_result"], native)
        self.assertEqual(result["source_index"], 2)
        action.assert_called_once_with(ctx.native, "GetGameRules.GetRulePresets", 2,
            "GetGameRules.OnSelectPreset", adapter=0x1234, process=ctx.process)
        count.assert_not_called()
        element.assert_not_called()
        direct.assert_not_called()
        release.assert_not_called()
        self.assertEqual(ctx.queries, ["setup.apply_preset"])
        self.assertFalse(any(isinstance(entry, _Run) for entry in ctx.owner_pool.snapshot()))

    async def test_native_zero_ordinal_world0_and_copied_value_zero_are_kept(self):
        ctx = Context()
        native = preset_application(index=0, world=0)
        with patch.object(data, "invoke_source_index_action", return_value=N(result=native)):
            result = await SetupAPI(ctx).apply_preset(0)
        self.assertEqual(result["source_index"], 0)
        self.assertEqual(result["native_result"].source_world, 0)
        self.assertEqual(result["native_result"].value, 0)

    async def test_native_range_refusal_is_returned_without_python_count_or_retry(self):
        ctx = Context()
        native = preset_application(index=9, count=2, error=1168, invoked=0, accepted=0)
        with patch.object(data, "invoke_source_index_action", return_value=N(result=native)) as action:
            result = await SetupAPI(ctx).apply_preset(9)
        self.assertIs(result["native_result"], native)
        self.assertEqual(native.source_count, 2)
        self.assertEqual((native.invoked, native.accepted), (0, 0))
        action.assert_called_once()

    async def test_native_error_does_not_erase_accepted_invocation_or_partial_bytes(self):
        ctx = Context()
        native = preset_application(error=234, invoked=1, accepted=1)
        with patch.object(data, "invoke_source_index_action", return_value=N(result=native)) as action:
            result = await SetupAPI(ctx).apply_preset(0)
        self.assertIs(result["native_result"], native)
        self.assertEqual((native.error, native.invoked, native.accepted), (234, 1, 1))
        self.assertEqual(native.result.snapshot, b"native copied prefix")
        action.assert_called_once()

    async def test_native_not_ready_count_zero_does_not_become_missing_presets(self):
        ctx = Context()
        native = preset_application(error=21, invoked=0, accepted=0, count=0)
        with patch.object(data, "invoke_source_index_action", return_value=N(result=native)):
            result = await SetupAPI(ctx).apply_preset(0)
        self.assertIs(result["native_result"], native)
        self.assertNotIn("empty", result)
        self.assertNotIn("missing", result)

    async def test_bad_ordinal_does_not_submit_or_resolve_metadata(self):
        ctx = Context()
        with patch.object(data, "invoke_source_index_action") as action:
            for index in (True, -1, 2**31, 1.5, "1", None):
                with self.subTest(index=index), self.assertRaises(ValueError):
                    await SetupAPI(ctx).apply_preset(index)
        action.assert_not_called()
        self.assertEqual(ctx.performs, [])

    async def test_unknown_query_is_kept_and_never_replayed_or_released(self):
        ctx = Context()
        failure = RuntimeError("original source-index action result unknown")
        failure.query = object()
        with patch.object(data, "invoke_source_index_action", return_value=N(result=failure)) as action, \
                patch.object(data, "release") as release:
            with self.assertRaises(RuntimeError) as caught:
                await SetupAPI(ctx).apply_preset(1)
        self.assertIs(caught.exception, failure)
        self.assertEqual(failure.setup_result["source_index"], 1)
        original = next(entry for entry in ctx.owner_pool.snapshot() if isinstance(entry, _Run))
        self.assertIs(original.uncertain_query, failure.query)
        self.assertTrue(original.retain_unknown)
        action.assert_called_once()
        release.assert_not_called()

    async def test_missing_new_capability_has_no_unsafe_old_action_fallback(self):
        ctx = Context()
        failure = OSError("PdxV3DataInvokeSourceIndexInterface not exported")
        with patch.object(data, "invoke_source_index_action", side_effect=failure) as action, \
                patch.object(data, "invoke_action") as direct:
            with self.assertRaises(OSError) as caught:
                await SetupAPI(ctx).apply_preset(1)
        self.assertIs(caught.exception, failure)
        action.assert_called_once()
        direct.assert_not_called()

    async def test_action_uses_the_original_scope_native_process_and_adapter(self):
        ctx = Context()
        native, process, adapter = ctx.native, ctx.process, ctx.adapters["data"]
        old_query = ctx.query
        async def switch(label, factory, *, owner=None):
            ctx.native, ctx.process, ctx.adapters["data"] = object(), object(), 0x9999
            return await old_query(label, factory, owner=owner)
        ctx.query = switch
        with patch.object(data, "invoke_source_index_action", return_value=N(result=preset_application())) as action:
            await SetupAPI(ctx).apply_preset(0)
        self.assertIs(action.call_args.args[0], native)
        self.assertIs(action.call_args.kwargs["process"], process)
        self.assertEqual(action.call_args.kwargs["adapter"], adapter)

    async def test_facade_consumes_real_shared_wire_encoder_and_element_decoder(self):
        ctx = Context()
        observed = {}
        def submit(client, initial, **options):
            request = data.DataResult.from_bytes(initial)
            source = data.ElementResult.from_bytes(initial)
            self.assertEqual(request.action, data.Action.INVOKE_SOURCE_INDEX_ACTION)
            self.assertEqual(request.start, 1)
            self.assertEqual(request.capacity, 0xffffffff)  # No caller expected_count.
            self.assertEqual(request.bindings_count, 0)
            self.assertEqual(source.source_world, 0)
            self.assertEqual(initial[source.source_text_offset:source.source_text_offset+source.source_text_bytes],
                b"GetGameRules.GetRulePresets")
            self.assertEqual(initial[request.text_offset:request.text_offset+request.text_bytes],
                b"GetGameRules.OnSelectPreset")
            self.assertEqual(options["callback_symbol"], "PdxV3DataInvokeSourceIndexInterface")
            self.assertIs(options["decode"].__func__, data.ElementResult.from_bytes.__func__)
            # Decode a completed copied-only native reply through that same decoder.
            reply = bytearray(initial)
            header = list(data.REQUEST.unpack_from(reply))
            header[1], header[11], header[26] = 0, data.Kind.I32, 1
            data.REQUEST.pack_into(reply, 0, *header)
            action = list(data.ACTION_SUFFIX.unpack_from(reply, data.REQUEST.size))
            action[2:5] = [4, 1, 1]
            data.ACTION_SUFFIX.pack_into(reply, data.REQUEST.size, *action)
            element = list(data.ELEMENT_SUFFIX.unpack_from(reply, data.ACTION_HEADER_SIZE))
            element[5], element[8] = 77, 3
            data.ELEMENT_SUFFIX.pack_into(reply, data.ACTION_HEADER_SIZE, *element)
            observed["result"] = options["decode"](bytes(reply))
            return N(result=observed["result"])
        with patch.object(data, "_submit", side_effect=submit) as native_submit:
            result = await SetupAPI(ctx).apply_preset(1)
        native_submit.assert_called_once()
        self.assertIs(result["native_result"], observed["result"])
        self.assertEqual(result["native_result"].source_count, 3)
        self.assertEqual((result["native_result"].invoked, result["native_result"].accepted), (1, 1))
        self.assertEqual(result["native_result"].value, 1)


# Build actual decoder results, not simplified action mocks, at the sequence seam.
def sequence_step(*, handle=101, error=0, invoked=1, accepted=1,
                  snapshot=b"name", total=None, declared=None):
    raw = bytearray(data.encode_action_request("GetGameRules.OnPresetSaveNameEdit",
        data.TypedInput.utf8_string("input"), output_bytes=max(16, len(snapshot))))
    header = list(data.REQUEST.unpack_from(raw))
    header[1], header[11], header[14] = error, data.Kind.STRING, handle
    header[9] = len(snapshot) if total is None else total
    header[19] = len(snapshot) if declared is None else declared
    raw[header[24]:header[24] + len(snapshot)] = snapshot
    data.REQUEST.pack_into(raw, 0, *header)
    suffix = list(data.ACTION_SUFFIX.unpack_from(raw, data.REQUEST.size))
    suffix[2:5] = [4, invoked, accepted]
    data.ACTION_SUFFIX.pack_into(raw, data.REQUEST.size, *suffix)
    result = data.ActionResult.from_bytes(bytes(raw))
    if len(snapshot) < header[19]:
        result = replace(result, result=replace(result.result, snapshot=snapshot))
    return result


def sequence_receipt(*steps, error=0, completed=None, stopped=False, scope_error=0):
    payload = data.ActionSequenceResult(32, error, len(steps), 32,
        len(steps) if completed is None else completed, stopped, tuple(steps), 32)
    raw = bytearray(access.encode_request(0xA100, 0, 0, mode=1, flag=0))
    header = list(access.REQUEST.unpack_from(raw))
    header[1], header[12], header[13] = scope_error, 1, 1
    access.REQUEST.pack_into(raw, 0, *header)
    return replace(access.AccessResult.from_bytes(bytes(raw)), callee_result=payload)


def sequence_context():
    ctx = Context()
    ctx.adapters["access"] = 0xB000
    return ctx


def sequence_query(result):
    return N(result=result, adapter=0xB000, data_adapter=0xA000,
        completion_status={"status": "ok", "completed_request_id": 7, "callback_error": 0})


def released_value():
    return N(result=N(action=data.Action.RELEASE, error=0, value_handle=0))


class SetupNamedPresetTests(IsolatedAsyncioTestCase):
    async def test_save_name_uses_original_utf8_edit_and_gated_consumer_in_one_callback(self):
        ctx = sequence_context()
        name = "(converter) O'Brien\\中\n"
        original = sequence_receipt(sequence_step(), sequence_step(handle=102, snapshot=b""))
        with patch.object(data, "invoke_action_sequence", return_value=sequence_query(original)) as sequence, \
                patch.object(data, "invoke_action") as direct, \
                patch.object(data, "invoke_property_action") as single, \
                patch.object(data, "release", return_value=released_value()) as release:
            result = await SetupAPI(ctx).save_preset_as(name)
        sequence.assert_called_once_with(ctx.native, (
            data.ActionStep("GetGameRules.OnPresetSaveNameEdit", data.TypedInput.utf8_string(name),
                data.ActionContinuation.INVOKED_AND_ACCEPTED),
            data.ActionStep("GetGameRules.SavePreset", data.TypedInput.empty(), data.ActionContinuation.ALWAYS),
        ), adapter=0x1234, access_adapter=0xB000, process=ctx.process)
        self.assertIs(result["native_result"], original)
        self.assertEqual(result["requested_name"], name)
        self.assertEqual(result["snapshot_attempts"], [])
        self.assertTrue(result["complete"])
        self.assertNotIn("persistence_complete", result)
        self.assertEqual(ctx.performs, ["setup.save_preset_as"])
        self.assertEqual([c.args[1] for c in release.call_args_list], [102, 101])
        self.assertTrue(all(c.kwargs["adapter"] == 0xA000 for c in release.call_args_list))
        direct.assert_not_called()
        single.assert_not_called()
        self.assertFalse(any(isinstance(x, _Run) for x in ctx.owner_pool.snapshot()))

    async def test_named_delete_consumes_original_draft_getter_without_apply_or_selection(self):
        ctx = sequence_context()
        original = sequence_receipt(sequence_step(handle=0), sequence_step(handle=0, snapshot=b""))
        with patch.object(data, "invoke_action_sequence", return_value=sequence_query(original)) as sequence, \
                patch.object(data, "invoke_source_index_action") as apply:
            result = await SetupAPI(ctx).delete_preset_named("")
        steps = sequence.call_args.args[1]
        self.assertEqual(steps[0].input, data.TypedInput.utf8_string(""))
        self.assertEqual(steps[1].expression,
            "GetGameRules.DeletePresetWithName(GetGameRules.GetPresetSaveName)")
        self.assertEqual(steps[1].input, data.TypedInput.empty())
        self.assertIs(result["native_result"], original)
        self.assertEqual(result["action"], "delete_preset_named")
        apply.assert_not_called()

    async def test_rejected_first_edit_keeps_original_unexecuted_pending_suffix(self):
        ctx = sequence_context()
        rejected = sequence_step(invoked=0, accepted=0, handle=0)
        unexecuted = sequence_step(error=997, invoked=0, accepted=0, handle=0, snapshot=b"")
        original = sequence_receipt(rejected, unexecuted, completed=1, stopped=True)
        with patch.object(data, "invoke_action_sequence", return_value=sequence_query(original)) as sequence, \
                patch.object(data, "snapshot") as snapshot, patch.object(data, "release") as release:
            result = await SetupAPI(ctx).save_preset_as("declined")
        self.assertIs(result["native_result"], original)
        self.assertIs(original.callee_result.native_results[1], unexecuted)
        self.assertEqual((original.callee_result.completed, original.callee_result.stopped), (1, True))
        self.assertEqual((rejected.invoked, rejected.accepted, unexecuted.error), (0, 0, 997))
        sequence.assert_called_once()
        snapshot.assert_not_called()
        release.assert_not_called()

    async def test_accepted_234_keeps_edit_receipt_and_snapshots_same_handle_before_release(self):
        ctx = sequence_context()
        first = sequence_step(error=234, snapshot=b"pre", total=8)
        consumer = sequence_step(handle=102, snapshot=b"")
        original = sequence_receipt(first, consumer, error=234)
        copied = replace(first.result, action=data.Action.SNAPSHOT, error=0,
            snapshot=b"complete", snapshot_bytes=8)
        order = []
        def snapshot(*args, **options):
            order.append(("snapshot", args[1]))
            return N(result=copied)
        def release(*args, **options):
            order.append(("release", args[1]))
            return released_value()
        with patch.object(data, "invoke_action_sequence", return_value=sequence_query(original)) as sequence, \
                patch.object(data, "snapshot", side_effect=snapshot) as snap, \
                patch.object(data, "release", side_effect=release):
            result = await SetupAPI(ctx).save_preset_as("new draft")
        self.assertIs(result["native_result"], original)
        self.assertEqual((first.error, first.invoked, first.accepted, first.result.snapshot), (234, 1, 1, b"pre"))
        attempt = result["snapshot_attempts"][0]
        self.assertIs(attempt["original"], first)
        self.assertIs(attempt["native_result"], copied)
        self.assertTrue(result["complete"])
        self.assertEqual(order, [("snapshot", 101), ("release", 102), ("release", 101)])
        snap.assert_called_once_with(ctx.native, 101, output_bytes=4096, adapter=0xA000, process=ctx.process)
        sequence.assert_called_once()

    async def test_zero_native_error_with_short_actual_copy_still_reads_original_handle(self):
        ctx = sequence_context()
        first = sequence_step(error=0, snapshot=b"a", total=4, declared=4)
        original = sequence_receipt(first, sequence_step(handle=0, snapshot=b""))
        copied = replace(first.result, action=data.Action.SNAPSHOT, snapshot=b"name")
        with patch.object(data, "invoke_action_sequence", return_value=sequence_query(original)) as sequence, \
                patch.object(data, "snapshot", return_value=N(result=copied)) as snapshot, \
                patch.object(data, "release", return_value=released_value()):
            result = await SetupAPI(ctx).delete_preset_named("name")
        self.assertEqual((first.error, first.result.snapshot_bytes, first.result.snapshot), (0, 4, b"a"))
        self.assertTrue(result["complete"])
        snapshot.assert_called_once()
        sequence.assert_called_once()

    async def test_callback_fault_adopts_current_handle_beyond_completed_then_raises_original(self):
        ctx = sequence_context()
        original = sequence_receipt(sequence_step(), sequence_step(handle=102),
            error=997, completed=1, scope_error=574)
        query = sequence_query(None)
        failure = data.ActionSequenceReadError(query, original, b"original copied fault bytes")
        query.result = failure
        with patch.object(data, "invoke_action_sequence", return_value=query) as sequence, \
                patch.object(data, "snapshot") as snapshot, patch.object(data, "release") as release:
            with self.assertRaises(data.ActionSequenceReadError) as caught:
                await SetupAPI(ctx).save_preset_as("already edited")
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.setup_result["native_result"], original)
        self.assertEqual(failure.copied_bytes, b"original copied fault bytes")
        retained = next(x for x in ctx.owner_pool.snapshot() if isinstance(x, _Run))
        self.assertEqual(retained.values, [101, 102])
        self.assertEqual(retained.value_adapters, {101: 0xA000, 102: 0xA000})
        self.assertIs(retained.uncertain_query, query)
        sequence.assert_called_once()
        snapshot.assert_not_called()
        release.assert_not_called()

    async def test_unknown_sequence_keeps_query_and_does_not_edit_again_or_release(self):
        ctx = sequence_context()
        query = sequence_query(None)
        failure = RuntimeError("original sequence completion unknown")
        failure.query = query
        query.result = failure
        with patch.object(data, "invoke_action_sequence", return_value=query) as sequence, \
                patch.object(data, "release") as release:
            with self.assertRaises(RuntimeError) as caught:
                await SetupAPI(ctx).delete_preset_named("draft may already differ")
        self.assertIs(caught.exception, failure)
        self.assertEqual(failure.setup_result["requested_name"], "draft may already differ")
        self.assertNotIn("native_result", failure.setup_result)
        retained = next(x for x in ctx.owner_pool.snapshot() if isinstance(x, _Run))
        self.assertIs(retained.uncertain_query, query)
        self.assertTrue(retained.retain_unknown)
        sequence.assert_called_once()
        release.assert_not_called()

    async def test_unknown_snapshot_keeps_original_receipts_and_supplement_query(self):
        ctx = sequence_context()
        first = sequence_step(error=234, snapshot=b"p", total=10000)
        original = sequence_receipt(first, sequence_step(handle=0, snapshot=b""), error=234)
        failure = RuntimeError("snapshot read unknown")
        supplement = N(result=failure)
        failure.query = supplement
        with patch.object(data, "invoke_action_sequence", return_value=sequence_query(original)) as sequence, \
                patch.object(data, "snapshot", return_value=supplement) as snapshot, \
                patch.object(data, "release") as release:
            with self.assertRaises(RuntimeError) as caught:
                await SetupAPI(ctx).save_preset_as("new name")
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.setup_result["native_result"], original)
        self.assertFalse(failure.setup_result["complete"])
        attempt = failure.setup_result["snapshot_attempts"][0]
        self.assertIs(attempt["query"], supplement)
        self.assertIs(attempt["original"], first)
        self.assertIs(attempt["error"], failure)
        snapshot.assert_called_once_with(ctx.native, 101, output_bytes=10000, adapter=0xA000, process=ctx.process)
        sequence.assert_called_once()
        release.assert_not_called()

    async def test_known_incomplete_supplement_returns_original_and_retains_scope(self):
        ctx = sequence_context()
        first = sequence_step(error=234, snapshot=b"p", total=8)
        original = sequence_receipt(first, sequence_step(handle=0, snapshot=b""))
        copied = replace(first.result, action=data.Action.SNAPSHOT, snapshot=b"part", snapshot_bytes=4)
        with patch.object(data, "invoke_action_sequence", return_value=sequence_query(original)) as sequence, \
                patch.object(data, "snapshot", return_value=N(result=copied)) as snapshot, \
                patch.object(data, "release") as release:
            result = await SetupAPI(ctx).save_preset_as("name")
        self.assertIs(result["native_result"], original)
        self.assertFalse(result["complete"])
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        retained = next(x for x in ctx.owner_pool.snapshot() if isinstance(x, _Run))
        self.assertEqual(retained.values, [101])
        snapshot.assert_called_once()
        sequence.assert_called_once()
        release.assert_not_called()

    async def test_cleanup_error_never_replaces_original_ordered_native_results(self):
        ctx = sequence_context()
        original = sequence_receipt(sequence_step(), sequence_step(handle=0, snapshot=b""))
        failed_release = N(action=data.Action.RELEASE, error=5, value_handle=101)
        with patch.object(data, "invoke_action_sequence", return_value=sequence_query(original)), \
                patch.object(data, "release", return_value=N(result=failed_release)):
            result = await SetupAPI(ctx).delete_preset_named("name")
        self.assertIs(result["native_result"], original)
        self.assertIs(result["cleanup"]["native_result"], failed_release)
        self.assertTrue(result["complete"])

    async def test_named_calls_reject_unrepresentable_names_before_perform_or_native(self):
        ctx = sequence_context()
        with patch.object(data, "invoke_action_sequence") as sequence:
            for method in (SetupAPI(ctx).save_preset_as, SetupAPI(ctx).delete_preset_named):
                for name, error in ((123, TypeError), ("a\0b", ValueError), ("bad\ud800", UnicodeEncodeError)):
                    with self.subTest(method=method.__name__, name=repr(name)), self.assertRaises(error):
                        await method(name)
        sequence.assert_not_called()
        self.assertEqual(ctx.performs, [])

    async def test_missing_sequence_capability_does_not_fallback_to_separate_callbacks(self):
        ctx = sequence_context()
        failure = OSError("PdxV3DataInvokeActionSequenceInterface not exported")
        with patch.object(data, "invoke_action_sequence", side_effect=failure) as sequence, \
                patch.object(data, "invoke_action") as single:
            with self.assertRaises(OSError) as caught:
                await SetupAPI(ctx).save_preset_as("name")
        self.assertIs(caught.exception, failure)
        sequence.assert_called_once()
        single.assert_not_called()

    async def test_original_run_process_and_both_adapter_inputs_survive_context_switch(self):
        ctx = sequence_context()
        native, process = ctx.native, ctx.process
        original = sequence_receipt(sequence_step(), sequence_step(handle=0, snapshot=b""))
        query = ctx.query
        async def switch(label, factory, *, owner=None):
            ctx.native, ctx.process = object(), object()
            ctx.adapters.update(data=0x9999, access=0x8888)
            return await query(label, factory, owner=owner)
        ctx.query = switch
        with patch.object(data, "invoke_action_sequence", return_value=sequence_query(original)) as sequence, \
                patch.object(data, "release", return_value=released_value()) as release:
            await SetupAPI(ctx).save_preset_as("name")
        self.assertIs(sequence.call_args.args[0], native)
        self.assertIs(sequence.call_args.kwargs["process"], process)
        self.assertEqual((sequence.call_args.kwargs["adapter"], sequence.call_args.kwargs["access_adapter"]),
            (0x1234, 0xB000))
        release.assert_called_once_with(native, 101, adapter=0xA000, process=process)

    async def test_real_sequence_encoder_access_carrier_and_original_decoders(self):
        ctx = sequence_context()
        client = Mock(pid=1)
        client.request.side_effect = lambda command: ({"status": "ok", "request_id": 7, "error": 0}
            if command.startswith("interface-callback ") else {"status": "ok", "completed_request_id": 7})
        process = Mock(pid=1)
        process.resolve_export.side_effect = lambda adapter, symbol, **options: (
            0xA100 if symbol == "PdxV3DataInvokeActionSequenceInterface" else 0xB100)
        process.export_owner.return_value = 0xA000
        process.allocate.return_value = 0x2000
        ctx.native, ctx.process = client, process
        observed = {}
        async def original_query(label, factory, *, owner=None):
            query = factory()
            raw = bytearray(process.write.call_args.args[1])
            scope = list(access.REQUEST.unpack_from(raw))
            scope[1], scope[12], scope[13] = 0, 1, 1
            access.REQUEST.pack_into(raw, 0, *scope)
            base = access.PAYLOAD_OFFSET
            sequence = list(data.ACTION_SEQUENCE.unpack_from(raw, base))
            self.assertEqual(sequence[2], 2)
            sequence[1], sequence[4] = 0, 2
            data.ACTION_SEQUENCE.pack_into(raw, base, *sequence)
            expressions = []
            for index in range(2):
                offset, policy = data.ACTION_SEQUENCE_STEP.unpack_from(raw, base + sequence[3] + index * 8)
                self.assertEqual(policy, 1 if index == 0 else 0)
                at = base + offset
                header = list(data.REQUEST.unpack_from(raw, at))
                expressions.append(bytes(raw[at + header[20]:at + header[20] + header[21]]))
                before = data.ActionResult.from_bytes(bytes(raw[at:at + header[0]]))
                self.assertEqual(before.input_kind, data.UTF8_INPUT if index == 0 else data.Kind.EMPTY)
                header[1], header[14], header[9], header[19] = 0, 0, 0, 0
                data.REQUEST.pack_into(raw, at, *header)
                action = list(data.ACTION_SUFFIX.unpack_from(raw, at + data.REQUEST.size))
                action[2:5] = [4, 1, 1]
                data.ACTION_SUFFIX.pack_into(raw, at + data.REQUEST.size, *action)
            self.assertEqual(expressions, [b"GetGameRules.OnPresetSaveNameEdit", b"GetGameRules.SavePreset"])
            process.read.return_value = bytes(raw)
            observed["query"], observed["result"] = query, query.read()
            return observed["result"]
        ctx.query = original_query
        with patch.object(data, "snapshot") as snapshot, patch.object(data, "release") as release:
            result = await SetupAPI(ctx).save_preset_as("(not syntax) 中'O\\B")
        self.assertIs(result["native_result"], observed["result"])
        self.assertIsInstance(result["native_result"], access.AccessResult)
        self.assertEqual(observed["query"].data_adapter, 0xA000)
        self.assertEqual(process.allocate.call_count, 1)
        self.assertEqual(process.free.call_count, 1)
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in client.request.call_args_list), 1)
        snapshot.assert_not_called()
        release.assert_not_called()

    async def test_real_context_pending_resumes_original_sequence_without_replay(self):
        import asyncio
        from pdx_runtime.query_executor import PendingOperation
        from pdx_runtime.v3_game._core import GameContext
        loop = asyncio.get_running_loop()
        submitted = asyncio.Event()
        original = sequence_receipt(sequence_step(handle=0), sequence_step(handle=0, snapshot=b""))
        class Query:
            data_adapter = 0xA000
            adapter = 0xB000
            ready = False
            _released = False
            def read(self):
                if not self.ready:
                    return None
                self._released = True
                return original
        query = Query()
        def submit(*args, **options):
            loop.call_soon_threadsafe(submitted.set)
            return query
        session = N(native=object(), namespace={})
        ctx = GameContext(session, process=object(), timeout=0, poll_interval=.001)
        with patch.object(data, "invoke_action_sequence", side_effect=submit) as sequence, \
                patch.object(data, "release") as release:
            pending = await SetupAPI(ctx).save_preset_as("pending name")
            self.assertIsInstance(pending, PendingOperation)
            await asyncio.wait_for(submitted.wait(), 1)
            self.assertIs(await pending.resume(timeout=0), pending)
            self.assertIn(query, pending.pending_queries)
            self.assertTrue(any(isinstance(x, _Run) for x in ctx.owner_pool.snapshot()))
            query.ready = True
            result = await pending.resume(timeout=1)
        self.assertIs(result["native_result"], original)
        self.assertTrue(result["complete"])
        self.assertIs(ctx.owner_pool, session.owner_pool)
        self.assertEqual(ctx.owner_pool.snapshot(), ())
        sequence.assert_called_once()
        release.assert_not_called()

    async def test_original_postinput_snapshot_fault_with_zero_lengths_is_not_complete(self):
        ctx = sequence_context()
        first = sequence_step(error=574, snapshot=b"", total=0)
        original = sequence_receipt(first, sequence_step(handle=0, snapshot=b""), error=574)
        copied = replace(first.result, action=data.Action.SNAPSHOT, error=0,
            snapshot=b"recovered", snapshot_bytes=9, total=9)
        with patch.object(data, "invoke_action_sequence", return_value=sequence_query(original)) as sequence, \
                patch.object(data, "snapshot", return_value=N(result=copied)) as snapshot, \
                patch.object(data, "release", return_value=released_value()):
            result = await SetupAPI(ctx).save_preset_as("draft already changed")
        snapshot.assert_called_once_with(ctx.native, 101, output_bytes=4096,
            adapter=0xA000, process=ctx.process)
        self.assertEqual(first.error, 574)
        self.assertIs(result["native_result"], original)
        self.assertTrue(result["complete"])
        self.assertIs(result["snapshot_attempts"][0]["native_result"], copied)
        sequence.assert_called_once()

    async def test_supplement_error_with_zero_lengths_keeps_owned_any_incomplete(self):
        ctx = sequence_context()
        first = sequence_step(error=234, snapshot=b"p", total=9)
        original = sequence_receipt(first, sequence_step(handle=0, snapshot=b""), error=234)
        failed = replace(first.result, action=data.Action.SNAPSHOT, error=21,
            snapshot=b"", snapshot_bytes=0, total=0)
        with patch.object(data, "invoke_action_sequence", return_value=sequence_query(original)) as sequence, \
                patch.object(data, "snapshot", return_value=N(result=failed)) as snapshot, \
                patch.object(data, "release") as release:
            result = await SetupAPI(ctx).save_preset_as("draft already changed")
        self.assertIs(result["native_result"], original)
        self.assertIs(result["snapshot_attempts"][0]["native_result"], failed)
        self.assertFalse(result["complete"])
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        retained = next(x for x in ctx.owner_pool.snapshot() if isinstance(x, _Run))
        self.assertEqual(retained.values, [101])
        release.assert_not_called()
        snapshot.assert_called_once()
        sequence.assert_called_once()


class SetupRuleChoicesTests(IsolatedAsyncioTestCase):

    async def test_native_empty_choices_remain_complete_empty_not_rule_absence(self):
        ctx = Context()
        page = replace(preset_page(count=0, total=0), fields_count=6)
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)):
            result = await SetupAPI(ctx).rule_choices(0)
        self.assertEqual(result.rows, ())
        self.assertEqual(result.requested_rule_index, 0)
        self.assertTrue(result.enumeration_complete)
        self.assertEqual(result.native_page.source_count, 0)

    async def test_native_parent_lookup_error_and_partial_cell_bytes_are_kept(self):
        ctx = Context()
        cell = data.ProjectionCell(0, 1, 234, data.Kind.STRING, 0, 0, b"prefix", 20)
        page = data.ProjectionPage(1168, 0, 6, 0, 77, 0, False, 0, (cell,))
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)) as project:
            result = await SetupAPI(ctx).rule_choices(999)
        self.assertIs(result.native_page, page)
        self.assertEqual(result.native_page.error, 1168)
        self.assertEqual(result.rows[0].cells["setting_key"].snapshot, b"prefix")
        self.assertEqual(result.rows[0].values["setting_key"], "prefix")
        self.assertIn("234", result.rows[0].field_issues["setting_key"])
        self.assertFalse(result.enumeration_complete)
        project.assert_called_once()

    async def test_invalid_rule_ordinal_or_choice_page_bounds_do_not_submit(self):
        ctx = Context()
        with patch.object(data, "project_page") as project:
            for index in (True, -1, 2**31, 1.5, "1", None):
                with self.subTest(index=index), self.assertRaises(ValueError):
                    await SetupAPI(ctx).rule_choices(index)
            for start, limit in ((-1, 1), (0, 0), (0, 65), (True, 1), (0, True)):
                with self.subTest(start=start, limit=limit), self.assertRaises(ValueError):
                    await SetupAPI(ctx).rule_choices(0, start=start, limit=limit)
        project.assert_not_called()
        self.assertEqual(ctx.performs, [])

    async def test_unknown_choice_query_keeps_original_without_raw_pointer_fallback(self):
        ctx = Context()
        failure = RuntimeError("original scoped choices unknown")
        query = N(result=failure)
        with patch.object(data, "project_page", return_value=query) as project, \
                patch.object(data, "list_element") as raw:
            with self.assertRaises(RuntimeError) as caught:
                await SetupAPI(ctx).rule_choices(2)
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.query, query)
        self.assertEqual(failure.projection_context["steps"],
            (data.CreationStep("GetGameRules.GetSettings", 2),))
        project.assert_called_once()
        raw.assert_not_called()

    async def test_real_shared_page_encoder_carries_scoped_rule_and_choice_expressions(self):
        ctx = Context()
        page = replace(preset_page(count=0, total=0), fields_count=6)
        def submit(client, initial, **options):
            header = data.PAGE_REQUEST.unpack_from(initial)
            self.assertEqual(header[18], data.PAGE_SCOPED | data.PAGE_NATIVE_FIELDS)
            self.assertEqual((header[9], header[10]), (4, 6))
            self.assertEqual(initial[header[7]:header[7] + header[8]], b"GuiGameRule.GetRule.GetSettings")
            steps_offset, count, completed, _, arguments, _ = data.PAGE_SCOPED_SUFFIX.unpack_from(
                initial, data.PAGE_REQUEST.size)
            self.assertEqual((count, completed, arguments), (1, 0, 0))
            step = data.CREATION_STEP.unpack_from(initial, steps_offset)
            self.assertEqual((step[2], step[3]), (3, 0xffffffff))
            self.assertEqual(initial[step[0]:step[0] + step[1]], b"GetGameRules.GetSettings")
            self.assertEqual(header[4], 6)
            fields = []
            for index in range(5):
                kind, offset, size, reserved = data.PAGE_FIELD.unpack_from(
                    initial, header[3] + index * data.PAGE_FIELD.size)
                self.assertEqual((kind, reserved), (0, 0))
                fields.append(initial[offset:offset + size])
            self.assertEqual(data.PAGE_FIELD.unpack_from(initial,
                header[3] + 5 * data.PAGE_FIELD.size), (3, 0, 0, 0))
            self.assertIn(b"GameRuleSetting.GetKey", fields)
            self.assertNotIn(b"GameRule.GetKey", fields)
            self.assertEqual(options["callback_symbol"], "PdxV3DataProjectRuleFieldsInterface")
            return N(result=page, cleanup_error=None)
        with patch.object(data, "submit_callback", side_effect=submit) as submit:
            result = await SetupAPI(ctx).rule_choices(3, start=4, limit=6)
        self.assertIs(result.native_page, page)
        self.assertEqual(result.requested_rule_index, 3)
        submit.assert_called_once()


class SetupNamedSequenceShortCopyTests(IsolatedAsyncioTestCase):
    async def test_real_decoder_short_native_error0_copy_keeps_only_observed_originals(self):
        ctx = sequence_context()
        client = Mock(pid=1)
        status = {"status": "ok", "completed_request_id": 7, "callback_error": 0}
        client.request.side_effect = lambda command: ({"status": "ok", "request_id": 7, "error": 0}
            if command.startswith("interface-callback ") else status)
        process = Mock(pid=1)
        process.resolve_export.side_effect = lambda adapter, symbol, **options: (
            0xA100 if symbol == "PdxV3DataInvokeActionSequenceInterface" else 0xB100)
        process.export_owner.return_value = 0xA000
        process.allocate.return_value = 0x2000
        ctx.native, ctx.process = client, process
        observed = {}
        async def read_original(label, factory, *, owner=None):
            query = factory()
            observed["query"] = query
            raw = bytearray(process.write.call_args.args[1])
            scope = list(access.REQUEST.unpack_from(raw))
            scope[1], scope[12], scope[13] = 0, 1, 1
            access.REQUEST.pack_into(raw, 0, *scope)
            base = access.PAYLOAD_OFFSET
            sequence = list(data.ACTION_SEQUENCE.unpack_from(raw, base))
            sequence[1], sequence[4] = 0, 2
            data.ACTION_SEQUENCE.pack_into(raw, base, *sequence)
            stop = None
            for index in range(2):
                offset, _ = data.ACTION_SEQUENCE_STEP.unpack_from(raw, base + sequence[3] + index * 8)
                at = base + offset
                header = list(data.REQUEST.unpack_from(raw, at))
                header[1], header[11], header[14], header[9], header[19] = 0, data.Kind.STRING, 101 + index, 4, 4
                data.REQUEST.pack_into(raw, at, *header)
                raw[at + header[24]:at + header[24] + 4] = b"name"
                action = list(data.ACTION_SUFFIX.unpack_from(raw, at + data.REQUEST.size))
                action[2:5] = [4, 1, 1]
                data.ACTION_SUFFIX.pack_into(raw, at + data.REQUEST.size, *action)
                if index == 0:
                    stop = at + header[24] + 1
            observed["copied_bytes"] = bytes(raw[:stop])
            process.read.return_value = observed["copied_bytes"]
            return query.read()
        ctx.query = read_original
        with patch.object(data, "snapshot") as snapshot, patch.object(data, "release") as release:
            with self.assertRaises(data.ActionSequenceReadError) as caught:
                await SetupAPI(ctx).save_preset_as("name")
        error = caught.exception
        self.assertIs(error.query, observed["query"])
        self.assertEqual(error.copied_bytes, observed["copied_bytes"])
        self.assertEqual(error.completion_status, status)
        self.assertIs(error.setup_result["native_result"], error.partial_result)
        sequence = error.partial_result.callee_result
        self.assertFalse(sequence.complete)
        self.assertEqual((sequence.count, sequence.completed, len(sequence.native_results)), (2, 2, 1))
        first = sequence.native_results[0]
        self.assertEqual((first.error, first.invoked, first.accepted, first.value_handle), (0, 1, 1, 101))
        self.assertEqual((first.result.snapshot_bytes, first.result.total, first.result.snapshot), (4, 4, b"n"))
        retained = next(x for x in ctx.owner_pool.snapshot() if isinstance(x, _Run))
        self.assertEqual(retained.values, [101])
        self.assertEqual(retained.value_adapters, {101: 0xA000})
        self.assertIs(retained.uncertain_query, observed["query"])
        self.assertEqual(process.allocate.call_count, 1)
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in client.request.call_args_list), 1)
        process.free.assert_not_called()
        snapshot.assert_not_called()
        release.assert_not_called()


class SetupApplyRulesTests(IsolatedAsyncioTestCase):
    async def test_explicit_apply_is_one_original_action_not_draft_edit_or_preset_save(self):
        ctx = Context()
        original = action_result(handle=0)
        with patch.object(data, "invoke_property_action", return_value=N(result=original)) as apply, \
                patch.object(data, "invoke_action") as edit, \
                patch.object(data, "invoke_action_sequence") as sequence, \
                patch.object(data, "project_page") as read:
            result = await SetupAPI(ctx).apply_rules()
        apply.assert_called_once_with(ctx.native, "GetGameRules.ApplySettings",
            adapter=0x1234, process=ctx.process)
        self.assertEqual(result["action"], "apply_rules")
        self.assertIs(result["native_result"], original)
        self.assertEqual(ctx.performs, ["setup.apply_rules"])
        self.assertEqual(ctx.queries, ["setup.apply_rules"])
        self.assertNotIn("persistence_complete", result)
        edit.assert_not_called()
        sequence.assert_not_called()
        read.assert_not_called()

    async def test_apply_native_error_and_acceptance_are_both_visible_without_replay(self):
        ctx = Context()
        original = action_result(handle=0, error=5, invoked=1, accepted=1)
        with patch.object(data, "invoke_property_action", return_value=N(result=original)) as apply:
            result = await SetupAPI(ctx).apply_rules()
        self.assertIs(result["native_result"], original)
        self.assertEqual((original.error, original.invoked, original.accepted), (5, 1, 1))
        apply.assert_called_once()


_RULE_HEADER_NAMES = tuple(field.name for field in fields(data.GameRuleSelectionResult))[:40]


def rule_selection_payload(*, keys=(b"old", b"new"), initial=b"old", final=b"new", receipts=(),
                           error=0, outcome=data.GameRuleSelectionOutcome.SELECTED, **observations):
    raw = bytearray(data.encode_game_rule_selection("request rule", "request setting", output_bytes=65536))
    header = dict(zip(_RULE_HEADER_NAMES, data.GAME_RULE_SELECTION.unpack_from(raw)))
    cursor = header["output_offset"]
    keys_offset = cursor if keys else 0
    cursor += len(keys) * data.PAGE_SPAN.size
    for index, key in enumerate(keys):
        data.PAGE_SPAN.pack_into(raw, keys_offset + index * data.PAGE_SPAN.size, cursor, len(key))
        raw[cursor:cursor + len(key)] = key
        cursor += len(key)
    initial_offset = cursor if initial is not None else 0
    if initial is not None:
        raw[cursor:cursor + len(initial)] = initial
        cursor += len(initial)
    final_offset = cursor if final is not None else 0
    if final is not None:
        raw[cursor:cursor + len(final)] = final
        cursor += len(final)
    cursor = (cursor + 7) & ~7
    receipts_offset = cursor if receipts else 0
    for receipt in receipts:
        raw[cursor:cursor + len(receipt)] = receipt
        cursor += len(receipt)
    header.update(error=error, stage=data.GameRuleSelectionStage.COMPLETE, outcome=outcome,
        root_observed=1, root_found=1, row_observed=1, row_found=1, count_valid=1,
        choice_count=len(keys), current_found=1, target_found=1, current_index=0, target_index=1,
        direction=int(bool(receipts)), planned=len(receipts), completed=len(receipts), stopped=0,
        receipts_offset=receipts_offset, receipts_count=len(receipts),
        keys_offset=keys_offset, keys_count=len(keys), final_observed=int(final is not None),
        final_selected=int(final is not None), initial_key_valid=int(initial is not None),
        initial_key_offset=initial_offset, initial_key_bytes=len(initial or b""),
        final_key_valid=int(final is not None), final_key_offset=final_offset,
        final_key_bytes=len(final or b""), receipts_bytes=sum(map(len, receipts)),
        copied_output_bytes=cursor - header["output_offset"], execution_thread_id=91)
    header.update(observations)
    data.GAME_RULE_SELECTION.pack_into(raw, 0, *(header[name] for name in _RULE_HEADER_NAMES))
    return data.GameRuleSelectionResult.from_bytes(bytes(raw))


def rule_action_packet(*, error=0, invoked=1, accepted=1):
    raw = bytearray(data.encode_action_request("", data.TypedInput.empty(), output_bytes=0))
    header = list(data.REQUEST.unpack_from(raw))
    header[1], header[11] = error, data.Kind.EMPTY
    data.REQUEST.pack_into(raw, 0, *header)
    suffix = list(data.ACTION_SUFFIX.unpack_from(raw, data.REQUEST.size))
    suffix[2:5] = [4, invoked, accepted]
    data.ACTION_SUFFIX.pack_into(raw, data.REQUEST.size, *suffix)
    return bytes(raw)


def rule_selection_access(payload, *, error=0):
    raw = bytearray(access.encode_request(0xA100, 0, 0, mode=1, flag=0))
    header = list(access.REQUEST.unpack_from(raw))
    header[1], header[12], header[13] = error, 1, 1
    access.REQUEST.pack_into(raw, 0, *header)
    return replace(access.AccessResult.from_bytes(bytes(raw)), callee_result=payload)


class SetupSetRuleTests(IsolatedAsyncioTestCase):
    async def test_actual_rule_api_gets_keys_as_data_and_does_not_apply_or_gate(self):
        ctx = sequence_context()
        payload = rule_selection_payload(receipts=(rule_action_packet(),))
        original = rule_selection_access(payload)
        with patch.object(data, "select_game_rule", return_value=sequence_query(original)) as select, \
                patch.object(data, "invoke_property_action") as apply, \
                patch.object(data, "project_page") as read, \
                patch.object(data, "snapshot") as snapshot, patch.object(data, "release") as release:
            result = await SetupAPI(ctx).set_rule("(converter) rule'\\中\0tail", "", output_bytes=8192)
        select.assert_called_once_with(ctx.native, "(converter) rule'\\中\0tail", "", output_bytes=8192,
            adapter=0x1234, access_adapter=0xB000, process=ctx.process)
        self.assertIs(result["native_result"], original)
        self.assertEqual(result["action"], "set_rule")
        self.assertEqual(result["requested_setting_key"], "")
        self.assertEqual(ctx.performs, ["setup.set_rule"])
        self.assertEqual(ctx.queries, ["setup.set_rule"])
        self.assertNotIn("accepted", result)
        self.assertNotIn("complete", result)
        self.assertNotIn("persistence_complete", result)
        self.assertFalse(any(isinstance(x, _Run) for x in ctx.owner_pool.snapshot()))
        apply.assert_not_called()
        read.assert_not_called()
        snapshot.assert_not_called()
        release.assert_not_called()

    async def test_no_action_observations_preserve_native_flags_and_do_not_fabricate_success(self):
        cases = (
            (data.GameRuleSelectionOutcome.NATIVE_NULL, dict(error=13, observation_error=13,
                row_observed=1, row_found=0, count_valid=0, target_found=0, current_found=0)),
            (data.GameRuleSelectionOutcome.EMPTY, dict(choice_count=0, current_found=0, target_found=0)),
            (data.GameRuleSelectionOutcome.TARGET_ABSENT, dict(target_found=0)),
            (data.GameRuleSelectionOutcome.CURRENT_NOT_MEMBER, dict(current_found=0)),
            (data.GameRuleSelectionOutcome.ALREADY_SELECTED, dict(current_index=1, target_index=1)),
        )
        for outcome, observations in cases:
            with self.subTest(outcome=outcome):
                ctx = sequence_context()
                payload = rule_selection_payload(outcome=outcome, receipts=(), **observations)
                original = rule_selection_access(payload)
                with patch.object(data, "select_game_rule", return_value=sequence_query(original)) as select:
                    result = await SetupAPI(ctx).set_rule("rule", "setting")
                self.assertIs(result["native_result"].callee_result, payload)
                self.assertEqual(payload.outcome, outcome)
                self.assertEqual(payload.native_results, ())
                self.assertNotIn("accepted", result)
                self.assertNotIn("missing", result)
                self.assertNotIn("success", result)
                select.assert_called_once()

    async def test_current_keys_zero_flags_empty_key_and_error_receipts_stay_exact(self):
        ctx = sequence_context()
        payload = rule_selection_payload(keys=(b"", b"actual\0new"), initial=b"", final=b"actual\0new",
            receipts=(rule_action_packet(error=234), rule_action_packet(error=5, accepted=0)),
            error=234, observation_error=13, outcome=data.GameRuleSelectionOutcome.ACTION_STOPPED,
            stopped=1, completed=1)
        original = rule_selection_access(payload)
        with patch.object(data, "select_game_rule", return_value=sequence_query(original)) as select:
            result = await SetupAPI(ctx).set_rule("requested", "different from copied")
        self.assertIs(result["native_result"], original)
        self.assertEqual((payload.error, payload.observation_error, payload.completed, len(payload.native_results)),
            (234, 13, 1, 2))
        self.assertEqual((payload.initial_key, payload.final_key), ("", "actual\0new"))
        self.assertEqual((payload.native_results[0].invoked, payload.native_results[0].accepted,
            payload.native_results[0].error), (1, 1, 234))
        self.assertEqual((payload.native_results[1].invoked, payload.native_results[1].accepted,
            payload.native_results[1].error), (1, 0, 5))
        select.assert_called_once()

    async def test_completed_output_partial_keeps_real_prefix_without_capacity_replay(self):
        ctx = sequence_context()
        payload = rule_selection_payload(keys=(b"one",), initial=None, final=None,
            receipts=(rule_action_packet(),), error=234, outcome=data.GameRuleSelectionOutcome.OUTPUT_PARTIAL,
            choice_count=100, planned=49, completed=1)
        original = rule_selection_access(payload)
        with patch.object(data, "select_game_rule", return_value=sequence_query(original)) as select:
            result = await SetupAPI(ctx).set_rule("rule", "target", output_bytes=1024)
        self.assertIs(result["native_result"], original)
        self.assertEqual((payload.choice_count, payload.keys_count, payload.planned, payload.completed), (100, 1, 49, 1))
        self.assertTrue(payload.complete)  # Copied record integrity, not selected/effect completion.
        self.assertIsNone(payload.final_key)
        self.assertNotIn("accepted", result)
        select.assert_called_once()

    async def test_original_read_fault_keeps_partial_receipts_query_status_and_scope(self):
        ctx = sequence_context()
        payload = rule_selection_payload(receipts=(rule_action_packet(), rule_action_packet(error=997)),
            error=997, completed=1, stage=data.GameRuleSelectionStage.ACTIONS)
        original = rule_selection_access(payload, error=574)
        query = sequence_query(None)
        failure = data.GameRuleSelectionReadError(query, original, b"actual copied prefix")
        query.result = failure
        with patch.object(data, "select_game_rule", return_value=query) as select, \
                patch.object(data, "release") as release:
            with self.assertRaises(data.GameRuleSelectionReadError) as caught:
                await SetupAPI(ctx).set_rule("rule", "target")
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.setup_result["native_result"], original)
        self.assertIs(failure.query, query)
        self.assertEqual(failure.copied_bytes, b"actual copied prefix")
        self.assertEqual(len(payload.native_results), 2)
        retained = next(x for x in ctx.owner_pool.snapshot() if isinstance(x, _Run))
        self.assertIs(retained.uncertain_query, query)
        self.assertEqual(retained.values, [])
        self.assertTrue(retained.retain_unknown)
        select.assert_called_once()
        release.assert_not_called()

    async def test_missing_capability_and_unknown_query_never_fall_back_to_pointer_or_next_loop(self):
        for failure in (OSError("PdxV3DataSelectGameRuleInterface missing"), RuntimeError("original query unknown")):
            with self.subTest(failure=failure):
                ctx = sequence_context()
                query = sequence_query(failure)
                failure.query = query
                with patch.object(data, "select_game_rule", return_value=query) as select, \
                        patch.object(data, "invoke_action") as action, \
                        patch.object(data, "invoke_element_action") as element:
                    with self.assertRaises(type(failure)) as caught:
                        await SetupAPI(ctx).set_rule("rule", "setting")
                self.assertIs(caught.exception, failure)
                self.assertIs(failure.query, query)
                self.assertNotIn("native_result", failure.setup_result)
                select.assert_called_once()
                action.assert_not_called()
                element.assert_not_called()

    async def test_validation_is_wire_representability_not_preset_nul_policy(self):
        ctx = sequence_context()
        with patch.object(data, "select_game_rule") as select:
            for rule, setting in ((None, "s"), ("r", 1)):
                with self.subTest(rule=rule, setting=setting), self.assertRaises(TypeError):
                    await SetupAPI(ctx).set_rule(rule, setting)
            for rule, setting in (("bad\ud800", "s"), ("r", "bad\ud800")):
                with self.assertRaises(UnicodeEncodeError):
                    await SetupAPI(ctx).set_rule(rule, setting)
            for size in (True, -1, 2**32, 1.5):
                with self.subTest(size=size), self.assertRaises(ValueError):
                    await SetupAPI(ctx).set_rule("rule", "setting", output_bytes=size)
        select.assert_not_called()
        self.assertEqual(ctx.performs, [])
        original = rule_selection_access(rule_selection_payload())
        with patch.object(data, "select_game_rule", return_value=sequence_query(original)) as select:
            await SetupAPI(ctx).set_rule("", "prefix\0tail", output_bytes=0)
        self.assertEqual(select.call_args.args[1:3], ("", "prefix\0tail"))
        self.assertEqual(select.call_args.kwargs["output_bytes"], 0)

    async def test_original_scope_client_process_adapters_pinned_across_context_change(self):
        ctx = sequence_context()
        native, process = ctx.native, ctx.process
        query = ctx.query
        async def switch(label, factory, *, owner=None):
            ctx.native, ctx.process = object(), object()
            ctx.adapters.update(data=0x9999, access=0x8888)
            return await query(label, factory, owner=owner)
        ctx.query = switch
        original = rule_selection_access(rule_selection_payload())
        with patch.object(data, "select_game_rule", return_value=sequence_query(original)) as select:
            await SetupAPI(ctx).set_rule("rule", "setting")
        select.assert_called_once_with(native, "rule", "setting", output_bytes=65536,
            adapter=0x1234, access_adapter=0xB000, process=process)

    async def test_real_encoder_access_carrier_decoder_keeps_partial_native_outcome(self):
        ctx = sequence_context()
        client = Mock(pid=1)
        status = {"status": "ok", "completed_request_id": 7, "callback_error": 0}
        client.request.side_effect = lambda command: ({"status": "ok", "request_id": 7, "error": 0}
            if command.startswith("interface-callback ") else status)
        process = Mock(pid=1)
        process.resolve_export.side_effect = lambda adapter, symbol, **options: (
            0xA100 if symbol == "PdxV3DataSelectGameRuleInterface" else 0xB100)
        process.export_owner.return_value = 0xA000
        process.allocate.return_value = 0x2000
        ctx.native, ctx.process = client, process
        observed = {}
        async def read_original(label, factory, *, owner=None):
            query = factory()
            raw = bytearray(process.write.call_args.args[1])
            scope = list(access.REQUEST.unpack_from(raw))
            scope[1], scope[12], scope[13] = 0, 1, 1
            access.REQUEST.pack_into(raw, 0, *scope)
            base = access.PAYLOAD_OFFSET
            header = dict(zip(_RULE_HEADER_NAMES, data.GAME_RULE_SELECTION.unpack_from(raw, base)))
            for key, prefix in (("rule'\0中", "rule"), ("(converter)setting", "setting")):
                offset, size = header[prefix + "_input_offset"], header[prefix + "_input_bytes"]
                self.assertEqual(raw[base + offset:base + offset + size], data.TypedInput.string(key).pack())
            self.assertEqual(header["output_bytes"], 2048)
            packet = rule_action_packet(error=234)
            at = base + header["output_offset"]
            raw[at:at + len(packet)] = packet
            header.update(error=234, stage=data.GameRuleSelectionStage.ACTIONS,
                outcome=data.GameRuleSelectionOutcome.OUTPUT_PARTIAL, planned=40, completed=1,
                choice_count=100, count_valid=1, receipts_offset=header["output_offset"],
                receipts_count=1, receipts_bytes=len(packet), copied_output_bytes=len(packet))
            data.GAME_RULE_SELECTION.pack_into(raw, base, *(header[name] for name in _RULE_HEADER_NAMES))
            process.read.return_value = bytes(raw)
            observed["query"] = query
            observed["result"] = query.read()
            return observed["result"]
        ctx.query = read_original
        with patch.object(data, "invoke_property_action") as apply, patch.object(data, "release") as release:
            result = await SetupAPI(ctx).set_rule("rule'\0中", "(converter)setting", output_bytes=2048)
        self.assertIs(result["native_result"], observed["result"])
        payload = result["native_result"].callee_result
        self.assertIsInstance(payload, data.GameRuleSelectionResult)
        self.assertEqual((payload.error, payload.planned, payload.completed, len(payload.native_results)), (234, 40, 1, 1))
        self.assertEqual((payload.native_results[0].error, payload.native_results[0].accepted), (234, 1))
        self.assertTrue(payload.complete)
        self.assertEqual(process.allocate.call_count, 1)
        self.assertEqual(process.free.call_count, 1)
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in client.request.call_args_list), 1)
        apply.assert_not_called()
        release.assert_not_called()

    async def test_real_context_pending_resumes_same_rule_callback_without_replay(self):
        import asyncio
        from pdx_runtime.query_executor import PendingOperation
        from pdx_runtime.v3_game._core import GameContext
        loop = asyncio.get_running_loop()
        submitted = asyncio.Event()
        original = rule_selection_access(rule_selection_payload(receipts=(rule_action_packet(),)))
        class Query:
            data_adapter = 0xA000
            adapter = 0xB000
            ready = False
            _released = False
            def read(self):
                if not self.ready:
                    return None
                self._released = True
                return original
        query = Query()
        def submit(*args, **options):
            loop.call_soon_threadsafe(submitted.set)
            return query
        session = N(native=object(), namespace={})
        ctx = GameContext(session, process=object(), timeout=0, poll_interval=.001)
        with patch.object(data, "select_game_rule", side_effect=submit) as select:
            pending = await SetupAPI(ctx).set_rule("rule", "target")
            self.assertIsInstance(pending, PendingOperation)
            await asyncio.wait_for(submitted.wait(), 1)
            self.assertIs(await pending.resume(timeout=0), pending)
            self.assertIn(query, pending.pending_queries)
            query.ready = True
            result = await pending.resume(timeout=1)
        self.assertIs(result["native_result"], original)
        self.assertEqual(ctx.owner_pool.snapshot(), ())
        select.assert_called_once()


def _rule_key_wire_context(case, *, source="preset_settings", fail_copy=False,
                           partial_field=0, rule_key=b"canonical_rule_key", page_error=0):
    ctx = Context()
    client = Mock(pid=1)
    client.request.side_effect = lambda command: (
        {"status": "ok", "request_id": 7, "error": 0}
        if command.startswith("interface-callback ")
        else {"status": "ok", "completed_request_id": 7})
    process = Mock(pid=1)
    process.resolve_export.return_value = 0xA100
    process.export_owner.return_value = 0xA000
    process.allocate.return_value = 0x2000
    ctx.native, ctx.process = client, process
    observed = {"fail_copy": fail_copy}
    async def read_original(label, factory, *, owner=None):
        query = factory()
        observed["query"] = query
        raw = bytearray(process.write.call_args.args[1])
        observed["submitted"] = bytes(raw)
        header = list(data.PAGE_REQUEST.unpack_from(raw))
        flags = data.PAGE_NATIVE_FIELDS
        if source == "preset_settings":
            flags |= data.PAGE_ORIGINAL_SOURCES | data.PAGE_SCOPED
            case.assertEqual(data.PAGE_SOURCE.unpack_from(raw, header[7]), (1, 0, 0, 0))
        else:
            expression = (b"GetGameRules.GetSettings" if source == "rules"
                          else b"GuiGameRule.GetRule.GetSettings")
            case.assertEqual(raw[header[7]:header[7] + header[8]], expression)
            if source == "rule_choices":
                flags |= data.PAGE_SCOPED
        case.assertEqual(header[18], flags)
        case.assertEqual(header[4], 6)
        fields = []
        for index in range(5):
            kind, offset, size, reserved = data.PAGE_FIELD.unpack_from(
                raw, header[3] + index * data.PAGE_FIELD.size)
            case.assertEqual((kind, reserved), (0, 0))
            fields.append(bytes(raw[offset:offset + size]))
        prefix = b"GuiGameRule.GetSetting" if source == "rules" else b"GameRuleSetting"
        parent = (b"GuiGameRule.GetRule.GetName" if source == "rules"
                  else b"GameRuleSetting.GetGameRule.GetName")
        case.assertEqual(fields, [parent, prefix + b".GetKey", prefix + b".GetName",
            prefix + b".GetDescription", prefix + b".GetDescriptionWithObjectiveRecommendation"])
        case.assertEqual(data.PAGE_FIELD.unpack_from(raw,
            header[3] + 5 * data.PAGE_FIELD.size), (3, 0, 0, 0))
        if source != "rules":
            scope = list(data.PAGE_SCOPED_SUFFIX.unpack_from(raw, data.PAGE_REQUEST.size))
            case.assertEqual(scope[1], 1)
            scope[2] = 1
            data.PAGE_SCOPED_SUFFIX.pack_into(raw, data.PAGE_REQUEST.size, *scope)
            step = list(data.CREATION_STEP.unpack_from(raw, scope[0]))
            expression = (b"GetGameRules.GetRulePresets" if source == "preset_settings"
                          else b"GetGameRules.GetSettings")
            case.assertEqual(raw[step[0]:step[0] + step[1]], expression)
            step[4:] = [1, 1, 9, 1, 77, 0]
            data.CREATION_STEP.pack_into(raw, scope[0], *step)
        texts = ("原规则".encode(), b"stored'\0key", b"", "原说明".encode(),
                 "原建议".encode(), rule_key)
        payload, table = bytearray(), bytearray()
        table_bytes = len(texts) * data.PAGE_CELL.size
        for field, value in enumerate(texts):
            table.extend(data.PAGE_CELL.pack(header[9], field, 0, data.Kind.STRING,
                -1, 0xffffffff, 0, table_bytes + len(payload), len(value), len(value),
                0xffffffff, 0, 0, 0))
            payload.extend(value)
        copied = bytes(table + payload)
        cut = table_bytes + sum(map(len, texts[:partial_field])) + max(0, len(texts[partial_field]) - 1)
        header[1], header[2], header[11], header[12], header[13] = page_error, 0, 91, 8, int(page_error == 0)
        header[14], header[15], header[16], header[17] = 1, 0x3000, len(copied), 6
        data.PAGE_REQUEST.pack_into(raw, 0, *header)
        def memory(address, count):
            if address == 0x2000:
                return bytes(raw)
            offset = address - 0x3000
            if observed["fail_copy"]:
                return copied[offset:min(offset + count, cut)] if offset < cut else b""
            return copied[offset:offset + count]
        process.read.side_effect = memory
        try:
            return query.read()
        except Exception as error:
            observed["original_error"] = error
            raise
    ctx.query = read_original
    return ctx, client, process, observed


class SetupStoredPresetTests(IsolatedAsyncioTestCase):
    def stored_page(self, *, start=0, count=1, total=1, error=0, count_valid=True):
        texts = ("原规则", "key'\0中", "", "原说明", "原建议", "canonical_rule_key")
        cells = tuple(data.ProjectionCell(start + row, field, 0, data.Kind.STRING,
            -1, 0, text.encode(), len(text.encode()))
            for row in range(count) for field, text in enumerate(texts))
        return data.ProjectionPage(error, 0, 6, start, 77, total, count_valid, count, cells)


    async def test_invalid_index_or_page_bounds_do_not_submit(self):
        ctx = Context()
        with patch.object(data, "project_page") as project:
            for index in (True, False, -1, 1 << 31, 1.0, None):
                with self.subTest(index=index), self.assertRaises(ValueError):
                    await SetupAPI(ctx).preset_settings(index)
            for options in ({"start": -1}, {"start": True}, {"start": 1 << 31},
                            {"limit": 0}, {"limit": 65}, {"limit": True}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    await SetupAPI(ctx).preset_settings(0, **options)
        project.assert_not_called()
        self.assertEqual(ctx.performs, [])

    async def test_zero_ordinal_empty_names_and_original_setting_bytes_are_preserved(self):
        ctx = Context()
        page = self.stored_page()
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)):
            view = await SetupAPI(ctx).preset_settings(0)
        self.assertEqual(view.requested_preset_index, 0)
        self.assertEqual(view.rows[0].source_index, 0)
        self.assertEqual(view.rows[0].values["rule_name"], "原规则")
        self.assertEqual(view.rows[0].values["setting_key"], "key'\0中")
        self.assertEqual(view.rows[0].cells["setting_key"].snapshot, "key'\0中".encode())
        self.assertEqual(view.rows[0].values["setting_name"], "")
        self.assertEqual(view.rows[0].values["rule_key"], "canonical_rule_key")
        self.assertNotEqual(view.rows[0].values["rule_key"], view.rows[0].values["setting_key"])
        self.assertNotIn("default", view.rows[0].values)
        self.assertNotIn("locked", view.rows[0].values)

    async def test_proven_empty_stored_array_is_not_an_absent_preset(self):
        ctx = Context()
        page = self.stored_page(count=0, total=0)
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)) as project:
            view = await SetupAPI(ctx).preset_settings(5)
        self.assertEqual(view.rows, ())
        self.assertTrue(view.enumeration_complete)
        self.assertEqual(view.requested_preset_index, 5)
        self.assertEqual(view.native_page.source_count, 0)
        self.assertTrue(view.native_page.count_valid)
        project.assert_called_once()

    async def test_source_error_and_unobserved_count_stay_distinct_from_empty(self):
        ctx = Context()
        page = self.stored_page(count=0, total=0, error=21, count_valid=False)
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)) as project, \
                patch.object(data, "evaluate") as fallback:
            view = await SetupAPI(ctx).preset_settings(0)
        self.assertIs(view.native_page, page)
        self.assertEqual((view.native_page.error, view.native_page.world), (21, 0))
        self.assertFalse(view.native_page.count_valid)
        self.assertFalse(view.enumeration_complete)
        self.assertIs(ctx.diagnostics[-1]["projection_page"], page)
        project.assert_called_once()
        fallback.assert_not_called()

    async def test_partial_bytes_and_observed_cells_survive_partial_row_counts(self):
        ctx = Context()
        page = self.stored_page(start=4, total=8, error=234, count_valid=False)
        cell = replace(page.cells[1], snapshot=b"p", total=100, error=234)
        page = replace(page, cells=(cell,), row_count=0)
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)):
            view = await SetupAPI(ctx).preset_settings(2, start=4)
        self.assertIs(view.native_page, page)
        self.assertEqual(len(view.rows), 1)
        self.assertEqual(view.rows[0].source_index, 4)
        self.assertEqual(view.rows[0].values["setting_key"], "p")
        self.assertEqual(view.rows[0].cells["setting_key"].snapshot, b"p")
        self.assertIn("234", view.rows[0].field_issues["setting_key"])
        self.assertFalse(view.enumeration_complete)

    async def test_cleanup_warning_does_not_replace_original_page(self):
        ctx = Context()
        page = self.stored_page()
        warning = RuntimeError("original copied allocation cleanup unknown")
        query = N(result=page, cleanup_error=warning)
        with patch.object(data, "project_page", return_value=query) as project:
            view = await SetupAPI(ctx).preset_settings(0)
        self.assertIs(view.native_page, page)
        self.assertEqual(ctx.remembered, [query])
        self.assertIs(ctx.diagnostics[-1]["cleanup_error"], warning)
        self.assertIs(ctx.diagnostics[-1]["result"], page)
        project.assert_called_once()

    async def test_original_copy_exception_exposes_same_partial_page_and_query(self):
        ctx = Context()
        page = self.stored_page(error=234)
        failure = RuntimeError("original partial byte read")
        query = N(result=failure, partial_result=page)
        with patch.object(data, "project_page", return_value=query) as project:
            with self.assertRaises(RuntimeError) as caught:
                await SetupAPI(ctx).preset_settings(0)
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.query, query)
        self.assertIs(failure.setup_page.native_page, page)
        self.assertEqual(failure.setup_page.requested_preset_index, 0)
        self.assertIsNone(failure.setup_page.requested_rule_index)
        self.assertIsInstance(failure.projection_context["source_expression"], data.NativeSource)
        project.assert_called_once()

    def wire_context(self, *, fail_copy=False):
        return _rule_key_wire_context(self, fail_copy=fail_copy)

    async def test_real_source16_encoder_original_alias_and_copied_page_decoder(self):
        ctx, client, process, observed = self.wire_context()
        view = await SetupAPI(ctx).preset_settings(4, start=2, limit=1)
        process.resolve_export.assert_called_once()
        self.assertEqual(process.resolve_export.call_args.args,
                         (0x1234, "PdxV3DataProjectRuleFieldsInterface"))
        self.assertEqual(process.resolve_export.call_args.kwargs["aliases"]["pdx_v3_data.dll"],
                         "pdx_runtime.dll")
        self.assertEqual(view.requested_preset_index, 4)
        self.assertEqual(view.rows[0].source_index, 2)
        self.assertEqual(view.rows[0].values["setting_key"], "stored'\0key")
        self.assertEqual(view.native_page.world, 0)
        self.assertEqual(view.native_page.completed_steps, 1)
        self.assertEqual(view.native_page.steps[0].index, 4)
        self.assertEqual(view.native_page.source_count, 8)
        self.assertTrue(view.has_more)
        self.assertEqual(process.allocate.call_count, 1)
        self.assertEqual(process.free.call_count, 2)
        self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                             for call in client.request.call_args_list), 1)

    async def test_real_short_copy_keeps_partial_view_and_continues_same_allocation(self):
        ctx, client, process, observed = self.wire_context(fail_copy=True)
        with self.assertRaises(Exception) as caught:
            await SetupAPI(ctx).preset_settings(4, start=2, limit=1)
        error, query = caught.exception, observed["query"]
        self.assertIs(error, observed["original_error"])
        self.assertIs(error.query, query)
        self.assertIs(error.setup_page.native_page, query.partial_result)
        self.assertEqual(error.setup_page.requested_preset_index, 4)
        self.assertIn("rule_name", error.setup_page.rows[0].field_issues)
        process.free.assert_not_called()
        observed["fail_copy"] = False
        copied = query.read()
        self.assertEqual(copied.cells[1].snapshot, b"stored'\0key")
        self.assertEqual(process.allocate.call_count, 1)
        self.assertEqual(process.free.call_count, 2)
        self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                             for call in client.request.call_args_list), 1)

    async def test_missing_actual_rule_fields_alias_preserves_error_before_allocation(self):
        ctx, client, process, observed = self.wire_context()
        failure = LookupError("original source export missing")
        process.resolve_export.side_effect = failure
        with self.assertRaises(LookupError) as caught:
            await SetupAPI(ctx).preset_settings(0)
        self.assertIs(caught.exception, failure)
        process.allocate.assert_not_called()
        process.write.assert_not_called()
        self.assertFalse(any(call.args[0].startswith("interface-callback ")
                             for call in client.request.call_args_list))
        self.assertFalse(hasattr(failure, "setup_page"))

    async def test_real_context_pending_resumes_same_stored_source_query_without_replay(self):
        import asyncio
        from pdx_runtime.query_executor import PendingOperation
        from pdx_runtime.v3_game._core import GameContext
        loop = asyncio.get_running_loop()
        submitted = asyncio.Event()
        original = self.stored_page()
        class Query:
            adapter = 0xA000
            ready = False
            _released = False
            def read(self):
                if not self.ready:
                    return None
                self._released = True
                return original
        query = Query()
        def submit(*args, **options):
            loop.call_soon_threadsafe(submitted.set)
            return query
        ctx = GameContext(N(native=object(), namespace={}), process=object(), timeout=0, poll_interval=.001)
        with patch.object(data, "project_page", side_effect=submit) as project:
            pending = await SetupAPI(ctx).preset_settings(0)
            self.assertIsInstance(pending, PendingOperation)
            await asyncio.wait_for(submitted.wait(), 1)
            self.assertIs(await pending.resume(timeout=0), pending)
            self.assertIn(query, pending.pending_queries)
            query.ready = True
            view = await pending.resume(timeout=1)
        self.assertIs(view.native_page, original)
        self.assertEqual(view.requested_preset_index, 0)
        self.assertEqual(ctx.owner_pool.snapshot(), ())
        project.assert_called_once()


class SetupRuleKeyTests(IsolatedAsyncioTestCase):
    async def read(self, ctx, source):
        api = SetupAPI(ctx)
        if source == "rules":
            return await api.rules(start=2, limit=1)
        return await getattr(api, source)(4, start=2, limit=1)

    async def test_rules_real_field16_appends_canonical_key_after_old_five_columns(self):
        key = "canonical'\0规则".encode()
        ctx, client, process, observed = _rule_key_wire_context(self, source="rules", rule_key=key)
        view = await self.read(ctx, "rules")
        self.assertEqual(view.field_names, ("rule_name", "setting_key", "setting_name",
            "description", "recommendation", "rule_key"))
        self.assertEqual(view.rows[0].values["rule_key"], key.decode())
        self.assertEqual(view.rows[0].cells["rule_key"].snapshot, key)
        self.assertNotEqual(view.rows[0].values["rule_key"], view.rows[0].values["setting_key"])
        self.assertNotEqual(view.rows[0].values["rule_key"], view.rows[0].values["rule_name"])
        self.assertIsNone(view.requested_rule_index)
        self.assertIsNone(view.requested_preset_index)
        self.assertEqual(process.resolve_export.call_args.args,
            (0x1234, "PdxV3DataProjectRuleFieldsInterface"))
        self.assertEqual(process.allocate.call_count, 1)
        self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                             for call in client.request.call_args_list), 1)
        self.assertNotIn("default", view.rows[0].values)
        self.assertNotIn("locked", view.rows[0].values)

    async def test_choices_real_key_keeps_scoped_canonical_source_and_rule_ordinal(self):
        ctx, client, process, observed = _rule_key_wire_context(self, source="rule_choices")
        view = await self.read(ctx, "rule_choices")
        self.assertEqual(view.rows[0].source_index, 2)
        self.assertEqual(view.rows[0].values["rule_key"], "canonical_rule_key")
        self.assertEqual(view.requested_rule_index, 4)
        self.assertIsNone(view.requested_preset_index)
        self.assertEqual(view.native_page.steps[0].index, 4)
        self.assertEqual(view.native_page.completed_steps, 1)
        self.assertEqual(process.resolve_export.call_args.args,
            (0x1234, "PdxV3DataProjectRuleFieldsInterface"))
        self.assertEqual(process.allocate.call_count, 1)

    async def test_missing_rule_fields_alias_never_uses_old_alias_or_allocates(self):
        for source in ("rules", "rule_choices", "preset_settings"):
            with self.subTest(source=source):
                ctx, client, process, observed = _rule_key_wire_context(self, source=source)
                failure = LookupError("original RuleFields marker unavailable")
                process.resolve_export.side_effect = failure
                with self.assertRaises(LookupError) as caught:
                    await self.read(ctx, source)
                self.assertIs(caught.exception, failure)
                process.resolve_export.assert_called_once()
                self.assertEqual(process.resolve_export.call_args.args,
                    (0x1234, "PdxV3DataProjectRuleFieldsInterface"))
                process.allocate.assert_not_called()
                process.write.assert_not_called()
                self.assertFalse(any(call.args[0].startswith("interface-callback ")
                    for call in client.request.call_args_list))
                self.assertFalse(hasattr(failure, "setup_page"))

    async def test_native_error87_keeps_original_page_and_observed_bytes_without_fallback(self):
        for source in ("rules", "rule_choices", "preset_settings"):
            with self.subTest(source=source):
                ctx, client, process, observed = _rule_key_wire_context(
                    self, source=source, page_error=87)
                view = await self.read(ctx, source)
                self.assertEqual(view.native_page.error, 87)
                self.assertFalse(view.native_page.count_valid)
                self.assertFalse(view.enumeration_complete)
                self.assertEqual(view.rows[0].cells["rule_key"].snapshot, b"canonical_rule_key")
                self.assertEqual(view.rows[0].cells["setting_key"].snapshot, b"stored'\0key")
                process.resolve_export.assert_called_once()
                self.assertEqual(process.allocate.call_count, 1)
                self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                    for call in client.request.call_args_list), 1)

    async def test_empty_and_nul_rule_keys_are_original_values_not_missing(self):
        for key in (b"", b"\0", "规则'\0尾".encode()):
            with self.subTest(key=key):
                ctx, client, process, observed = _rule_key_wire_context(
                    self, source="preset_settings", rule_key=key)
                view = await self.read(ctx, "preset_settings")
                self.assertEqual(view.rows[0].values["rule_key"], key.decode())
                self.assertEqual(view.rows[0].cells["rule_key"].snapshot, key)
                self.assertEqual(view.rows[0].cells["rule_key"].total, len(key))
                self.assertNotIn("rule_key", view.rows[0].field_issues)

    async def test_key_getter_error_is_not_replaced_by_setting_key_or_false(self):
        cell = data.ProjectionCell(2, 5, 50, data.Kind.EMPTY, -1, 0, b"", 0)
        other = data.ProjectionCell(2, 1, 0, data.Kind.STRING, -1, 0, b"setting_key", 11)
        page = data.ProjectionPage(0, 0, 6, 2, 77, 8, True, 1, (other, cell))
        for source in ("rules", "rule_choices", "preset_settings"):
            with self.subTest(source=source):
                ctx = Context()
                with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)) as project:
                    view = await self.read(ctx, source)
                self.assertIs(view.native_page, page)
                self.assertIs(view.rows[0].cells["rule_key"], cell)
                self.assertIsNone(view.rows[0].values["rule_key"])
                self.assertEqual(view.rows[0].values["setting_key"], "setting_key")
                self.assertIn("50", view.rows[0].field_issues["rule_key"])
                project.assert_called_once()

    async def test_partial_key_bytes_keep_original_query_and_resume_same_allocation(self):
        for source in ("rules", "rule_choices", "preset_settings"):
            with self.subTest(source=source):
                ctx, client, process, observed = _rule_key_wire_context(
                    self, source=source, fail_copy=True, partial_field=5)
                with self.assertRaises(Exception) as caught:
                    await self.read(ctx, source)
                error, query = caught.exception, observed["query"]
                self.assertIs(error, observed["original_error"])
                self.assertIs(error.query, query)
                self.assertIs(error.setup_page.native_page, query.partial_result)
                self.assertEqual(error.setup_page.rows[0].cells["rule_key"].snapshot,
                    b"canonical_rule_ke")
                self.assertIn("rule_key", error.setup_page.rows[0].field_issues)
                self.assertEqual(error.setup_page.rows[0].values["setting_key"], "stored'\0key")
                process.free.assert_not_called()
                observed["fail_copy"] = False
                copied = query.read()
                self.assertEqual(copied.cells[5].snapshot, b"canonical_rule_key")
                self.assertEqual(process.allocate.call_count, 1)
                self.assertEqual(process.free.call_count, 2)
                self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                    for call in client.request.call_args_list), 1)

    async def test_rules_and_choices_pending_resume_same_query_without_replay(self):
        import asyncio
        from pdx_runtime.query_executor import PendingOperation
        from pdx_runtime.v3_game._core import GameContext
        for source in ("rules", "rule_choices"):
            with self.subTest(source=source):
                loop = asyncio.get_running_loop()
                submitted = asyncio.Event()
                cell = data.ProjectionCell(2, 5, 0, data.Kind.STRING, -1, 0, b"canonical_rule_key", 18)
                original = data.ProjectionPage(0, 0, 6, 2, 77, 8, True, 1, (cell,))
                class Query:
                    adapter = 0xA000
                    ready = False
                    _released = False
                    def read(self):
                        if not self.ready:
                            return None
                        self._released = True
                        return original
                query = Query()
                def submit(*args, **options):
                    loop.call_soon_threadsafe(submitted.set)
                    return query
                ctx = GameContext(N(native=object(), namespace={}), process=object(),
                    timeout=0, poll_interval=.001)
                with patch.object(data, "project_page", side_effect=submit) as project:
                    pending = await self.read(ctx, source)
                    self.assertIsInstance(pending, PendingOperation)
                    await asyncio.wait_for(submitted.wait(), 1)
                    self.assertIs(await pending.resume(timeout=0), pending)
                    self.assertIn(query, pending.pending_queries)
                    query.ready = True
                    view = await pending.resume(timeout=1)
                self.assertIs(view.native_page, original)
                self.assertEqual(view.rows[0].values["rule_key"], "canonical_rule_key")
                self.assertEqual(ctx.owner_pool.snapshot(), ())
                project.assert_called_once()


class SetupGlobalIronmanTests(IsolatedAsyncioTestCase):
    def page(self, *, enabled=False, error=0, count_valid=True, include_cell=True):
        cell = data.ProjectionCell(0, 0, 0, data.Kind.BOOL, -1, int(enabled), b"", 0)
        return data.ProjectionPage(error, 0, 1, 0, 77, 1, count_valid,
            int(include_cell), (cell,) if include_cell else ())


    async def test_real_encoder_uses_existing_page_alias_and_original_global_getter(self):
        ctx = Context()
        page = self.page(enabled=False)
        def submit(client, initial, **options):
            header = data.PAGE_REQUEST.unpack_from(initial)
            self.assertEqual((header[4], header[8], header[9], header[10], header[18]),
                (1, 0, 0, 1, 0))
            self.assertLessEqual(header[7], len(initial))  # Empty span has no source bytes.
            offset, size = data.PAGE_SPAN.unpack_from(initial, header[3])
            self.assertEqual(initial[offset:offset + size], b"IsIronmanEnabled")
            self.assertEqual(options["callback_symbol"], "PdxV3DataProjectPageInterface")
            return N(result=page, cleanup_error=None)
        with patch.object(data, "submit_callback", side_effect=submit) as callback:
            view = await SetupAPI(ctx).ironman()
        self.assertIs(view.native_page, page)
        self.assertIs(view.rows[0].values["enabled"], False)
        self.assertEqual(view.source, "ironman")
        callback.assert_called_once()

    async def test_unknown_global_flag_stays_native_error_not_draft_or_false_fallback(self):
        ctx = Context()
        page = self.page(error=21, count_valid=False, include_cell=False)
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)) as project, \
                patch.object(data, "evaluate") as evaluate, \
                patch.object(data, "invoke_property_action") as action:
            view = await SetupAPI(ctx).ironman()
        self.assertIs(view.native_page, page)
        self.assertEqual(view.native_page.error, 21)
        self.assertFalse(view.native_page.count_valid)
        self.assertEqual(view.rows, ())
        self.assertFalse(view.enumeration_complete)
        project.assert_called_once()
        evaluate.assert_not_called()
        action.assert_not_called()

    async def test_read_copy_exception_keeps_same_partial_global_page_and_query(self):
        ctx = Context()
        page = self.page(error=234)
        failure = RuntimeError("original global-flag copy unknown")
        query = N(result=failure, partial_result=page)
        with patch.object(data, "project_page", return_value=query) as project:
            with self.assertRaises(RuntimeError) as caught:
                await SetupAPI(ctx).ironman()
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.query, query)
        self.assertIs(failure.setup_page.native_page, page)
        self.assertIs(failure.setup_page.rows[0].values["enabled"], False)
        self.assertEqual(failure.setup_page.source, "ironman")
        project.assert_called_once()

    async def test_global_true_false_use_stock_fixed_bool_formal_not_draft_or_input_any(self):
        for enabled, token in ((True, "yes"), (False, "no")):
            with self.subTest(enabled=enabled):
                ctx = Context()
                original = action_result(handle=0)
                with patch.object(data, "invoke_property_action", return_value=N(result=original)) as invoke, \
                        patch.object(data, "invoke_action") as input_action, \
                        patch.object(data, "project_page") as read:
                    result = await SetupAPI(ctx).set_ironman(enabled)
                invoke.assert_called_once_with(ctx.native,
                    "SetIronmanEnabledStatus('(bool)" + token + "')",
                    adapter=0x1234, process=ctx.process)
                input_action.assert_not_called()
                read.assert_not_called()
                self.assertIs(result["native_result"], original)
                self.assertIs(result["requested_enabled"], enabled)
                self.assertEqual(result["action"], "set_ironman")
                self.assertEqual(ctx.performs, ["setup.set_ironman"])
                self.assertEqual(ctx.queries, ["setup.set_ironman"])
                self.assertNotIn("observed_enabled", result)
                self.assertNotIn("achievement_eligible", result)

    async def test_non_bool_global_intent_rejected_before_any_native_submission(self):
        ctx = Context()
        with patch.object(data, "invoke_property_action") as action, \
                patch.object(data, "invoke_action") as input_action:
            for enabled in (0, 1, None, "yes", [], object()):
                with self.subTest(enabled=enabled), self.assertRaises(TypeError):
                    await SetupAPI(ctx).set_ironman(enabled)
        action.assert_not_called()
        input_action.assert_not_called()
        self.assertEqual(ctx.performs, [])

    async def test_declined_global_receipt_error_and_false_intent_are_not_rewritten(self):
        ctx = Context()
        original = action_result(error=87, invoked=1, accepted=0, handle=0)
        with patch.object(data, "invoke_property_action", return_value=N(result=original)) as invoke:
            result = await SetupAPI(ctx).set_ironman(False)
        self.assertIs(result["native_result"], original)
        self.assertIs(result["requested_enabled"], False)
        self.assertEqual((original.error, original.invoked, original.accepted), (87, 1, 0))
        invoke.assert_called_once()

    async def test_original_global_action_exception_stays_same_without_retry_or_draft_fallback(self):
        ctx = Context()
        failure = RuntimeError("original global action unknown")
        query = N(result=failure)
        failure.query = query  # Preserve the witness already attached by the original query.
        with patch.object(data, "invoke_property_action", return_value=query) as invoke, \
                patch.object(data, "invoke_action") as input_action, \
                patch.object(data, "release") as release:
            with self.assertRaises(RuntimeError) as caught:
                await SetupAPI(ctx).set_ironman(True)
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.query, query)
        self.assertIs(failure.setup_result["requested_enabled"], True)
        invoke.assert_called_once()
        input_action.assert_not_called()
        release.assert_not_called()


class SetupDraftDefaultsIronmanTests(IsolatedAsyncioTestCase):
    async def test_reset_dispatches_one_original_draft_operation_without_read_or_apply(self):
        ctx = Context()
        original = action_result(handle=0)
        with patch.object(data, "invoke_property_action", return_value=N(result=original)) as invoke, \
                patch.object(data, "invoke_action") as setter, \
                patch.object(data, "project_page") as read, \
                patch.object(data, "select_game_rule") as select:
            result = await SetupAPI(ctx).reset_rules_to_defaults()
        invoke.assert_called_once_with(ctx.native, "GetGameRules.ResetAllToDefaults",
            adapter=0x1234, process=ctx.process)
        setter.assert_not_called()
        read.assert_not_called()
        select.assert_not_called()
        self.assertIs(result["native_result"], original)
        self.assertEqual(result["action"], "reset_rules_to_defaults")
        self.assertNotIn("requested_enabled", result)
        self.assertEqual(ctx.performs, ["setup.reset_rules_to_defaults"])
        self.assertEqual(ctx.queries, ["setup.reset_rules_to_defaults"])


    async def test_ironman_requires_a_boolean_before_perform_or_native_submission(self):
        ctx = Context()
        with patch.object(data, "invoke_property_action") as invoke, \
                patch.object(data, "invoke_action") as setter:
            for invalid in (None, 0, 1, "no", "'(bool)yes'", {}, object()):
                with self.subTest(invalid=invalid), self.assertRaises(TypeError):
                    await SetupAPI(ctx).set_draft_ironman(invalid)
        invoke.assert_not_called()
        setter.assert_not_called()
        self.assertEqual(ctx.performs, [])

    async def test_original_error_zero_flags_and_false_intent_are_not_rewritten(self):
        for method in ("reset_rules_to_defaults", "set_draft_ironman"):
            with self.subTest(method=method):
                ctx = Context()
                original = action_result(error=234, invoked=0, accepted=0, handle=0)
                with patch.object(data, "invoke_property_action", return_value=N(result=original)) as invoke:
                    api = SetupAPI(ctx)
                    result = (await api.reset_rules_to_defaults() if method == "reset_rules_to_defaults"
                              else await api.set_draft_ironman(False))
                self.assertIs(result["native_result"], original)
                self.assertEqual((original.error, original.invoked, original.accepted), (234, 0, 0))
                self.assertEqual(original.result.snapshot, b"native bytes")
                self.assertNotIn("effect_complete", result)
                self.assertNotIn("persistence_complete", result)
                if method == "set_draft_ironman":
                    self.assertIs(result["requested_enabled"], False)
                invoke.assert_called_once()

    async def test_original_submission_exception_keeps_false_request_metadata(self):
        ctx = Context()
        original = RuntimeError("original submission state unknown")
        with patch.object(data, "invoke_property_action", return_value=N(result=original)) as invoke:
            with self.assertRaises(RuntimeError) as caught:
                await SetupAPI(ctx).set_draft_ironman(False)
        self.assertIs(caught.exception, original)
        self.assertEqual(original.setup_result["action"], "set_draft_ironman")
        self.assertIs(original.setup_result["requested_enabled"], False)
        invoke.assert_called_once()

    async def test_supplement_failure_preserves_false_intent_and_original_receipt(self):
        ctx = Context()
        original = sequence_step(error=234, snapshot=b"p", total=10000)
        failure = RuntimeError("same original snapshot read failed")
        query = N(result=failure)
        failure.query = query
        with patch.object(data, "invoke_property_action", return_value=N(result=original, adapter=0xA000)) as action, \
                patch.object(data, "snapshot", return_value=query) as snapshot, \
                patch.object(data, "release") as release:
            result = await SetupAPI(ctx).set_draft_ironman(False)
        self.assertIs(result["requested_enabled"], False)
        self.assertIs(result["native_result"], original)
        self.assertIs(result["snapshot_error"], failure)
        self.assertFalse(result["complete"])
        self.assertIs(result["snapshot_attempts"][0]["query"], query)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        action.assert_called_once()
        snapshot.assert_called_once()
        release.assert_not_called()

    async def test_real_property_encoder_keeps_boolean_in_expression_and_input_empty(self):
        calls = (("reset_rules_to_defaults", None, "GetGameRules.ResetAllToDefaults"),
                 ("set_draft_ironman", True, "GetGameRules.SetIronman('(bool)yes')"),
                 ("set_draft_ironman", False, "GetGameRules.SetIronman('(bool)no')"))
        for method, enabled, expression in calls:
            with self.subTest(method=method, enabled=enabled):
                ctx = Context()
                original = data.ActionResult.from_bytes(rule_action_packet(accepted=0))
                with patch.object(data, "submit_callback", return_value=N(result=original)) as submit:
                    api = SetupAPI(ctx)
                    result = (await api.reset_rules_to_defaults() if method == "reset_rules_to_defaults"
                              else await api.set_draft_ironman(enabled))
                submit.assert_called_once()
                initial = submit.call_args.args[1]
                header = data.REQUEST.unpack_from(initial)
                suffix = data.ACTION_SUFFIX.unpack_from(initial, data.REQUEST.size)
                self.assertEqual(initial[header[20]:header[20] + header[21]], expression.encode())
                self.assertEqual(header[2], data.Action.INVOKE_ACTION)
                self.assertEqual(suffix[5], data.Kind.EMPTY)
                self.assertEqual(initial[suffix[0]:suffix[0] + suffix[1]], data.TypedInput.empty().pack())
                self.assertEqual(submit.call_args.kwargs["callback_symbol"], "PdxV3DataInterface")
                self.assertIs(result["native_result"], original)
                self.assertEqual(original.accepted, 0)

    async def test_pending_ironman_formal_action_resumes_same_query_once(self):
        import asyncio
        from pdx_runtime.query_executor import PendingOperation
        from pdx_runtime.v3_game._core import GameContext
        loop = asyncio.get_running_loop()
        submitted = asyncio.Event()
        original = data.ActionResult.from_bytes(rule_action_packet())
        class Query:
            adapter = 0xA000
            ready = False
            _released = False
            def read(self):
                if not self.ready:
                    return None
                self._released = True
                return original
        query = Query()
        def invoke(*args, **options):
            loop.call_soon_threadsafe(submitted.set)
            return query
        ctx = GameContext(N(native=object(), namespace={}), process=object(), timeout=0, poll_interval=.001)
        with patch.object(data, "invoke_property_action", side_effect=invoke) as action:
            pending = await SetupAPI(ctx).set_draft_ironman(False)
            self.assertIsInstance(pending, PendingOperation)
            await asyncio.wait_for(submitted.wait(), 1)
            self.assertIs(await pending.resume(timeout=0), pending)
            self.assertIn(query, pending.pending_queries)
            query.ready = True
            result = await pending.resume(timeout=1)
        self.assertIs(result["native_result"], original)
        self.assertIs(result["requested_enabled"], False)
        self.assertEqual(ctx.owner_pool.snapshot(), ())
        action.assert_called_once()


class SetupActionSnapshotTests(IsolatedAsyncioTestCase):
    async def test_partial_accepted_currentdraft_action_copies_same_owned_any_before_release(self):
        ctx = Context()
        original = sequence_step(error=234, snapshot=b"pre", total=8)
        copied = replace(original.result, action=data.Action.SNAPSHOT, error=0,
            snapshot=b"complete", snapshot_bytes=8)
        order = []
        def snapshot(*args, **options):
            order.append(("snapshot", args[1]))
            return N(result=copied)
        def release(*args, **options):
            order.append(("release", args[1]))
            return released_value()
        with patch.object(data, "invoke_action", return_value=N(result=original, adapter=0xA000)) as action, \
                patch.object(data, "snapshot", side_effect=snapshot) as read, \
                patch.object(data, "release", side_effect=release):
            result = await SetupAPI(ctx).set_preset_name("new name")
        self.assertIs(result["native_result"], original)
        self.assertEqual((original.error, original.invoked, original.accepted), (234, 1, 1))
        self.assertEqual(original.result.snapshot, b"pre")
        self.assertIs(result["snapshot_attempts"][0]["original"], original)
        self.assertIs(result["snapshot_attempts"][0]["native_result"], copied)
        self.assertTrue(result["complete"])
        self.assertNotIn("snapshot_error", result)
        self.assertEqual(order, [("snapshot", 101), ("release", 101)])
        read.assert_called_once_with(ctx.native, 101, output_bytes=4096, adapter=0xA000, process=ctx.process)
        action.assert_called_once()

    async def test_short_actual_bytes_with_native_error0_are_supplemented_not_reinvoked(self):
        ctx = Context()
        original = sequence_step(error=0, snapshot=b"a", total=4, declared=4)
        copied = replace(original.result, action=data.Action.SNAPSHOT, snapshot=b"name")
        with patch.object(data, "invoke_property_action", return_value=N(result=original, adapter=0xA000)) as action, \
                patch.object(data, "snapshot", return_value=N(result=copied)) as snapshot, \
                patch.object(data, "release", return_value=released_value()):
            result = await SetupAPI(ctx).delete_draft_named_preset()
        self.assertIs(result["native_result"], original)
        self.assertTrue(result["complete"])
        self.assertEqual((original.error, original.result.snapshot_bytes, original.result.snapshot), (0, 4, b"a"))
        snapshot.assert_called_once()
        action.assert_called_once()

    async def test_supplement_exception_is_additive_and_keeps_primary_and_original_query(self):
        ctx = Context()
        original = sequence_step(error=234, snapshot=b"p", total=10000)
        failure = RuntimeError("supplement original read unknown")
        query = N(result=failure)
        failure.query = query
        with patch.object(data, "invoke_property_action", return_value=N(result=original, adapter=0xA000)) as action, \
                patch.object(data, "snapshot", return_value=query) as snapshot, \
                patch.object(data, "release") as release:
            result = await SetupAPI(ctx).save_preset()
        self.assertIs(result["native_result"], original)
        self.assertEqual((original.error, original.invoked, original.accepted), (234, 1, 1))
        self.assertIs(result["snapshot_error"], failure)
        self.assertFalse(result["complete"])
        attempt = result["snapshot_attempts"][0]
        self.assertIs(attempt["query"], query)
        self.assertIs(attempt["error"], failure)
        self.assertIs(attempt["original"], original)
        retained = next(x for x in ctx.owner_pool.snapshot() if isinstance(x, _Run))
        self.assertIs(retained.uncertain_query, query)
        self.assertEqual(retained.values, [101])
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        diagnostic = next(entry for entry in ctx.diagnostics if isinstance(entry, dict)
            and entry.get("label") == "setup.save_preset.post_input_snapshot")
        self.assertIs(diagnostic["native_result"], original)
        self.assertIs(diagnostic["error"], failure)
        snapshot.assert_called_once_with(ctx.native, 101, output_bytes=10000, adapter=0xA000, process=ctx.process)
        action.assert_called_once()
        release.assert_not_called()

    async def test_supplement_native_failure_remains_incomplete_and_retains_original_any(self):
        ctx = Context()
        original = sequence_step(error=574, snapshot=b"", total=0)
        failed = replace(original.result, action=data.Action.SNAPSHOT, error=21)
        with patch.object(data, "invoke_property_action", return_value=N(result=original, adapter=0xA000)) as action, \
                patch.object(data, "snapshot", return_value=N(result=failed)) as snapshot, \
                patch.object(data, "release") as release:
            result = await SetupAPI(ctx).delete_selected_preset()
        self.assertIs(result["native_result"], original)
        self.assertFalse(result["complete"])
        self.assertIs(result["snapshot_attempts"][0]["native_result"], failed)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        action.assert_called_once()
        snapshot.assert_called_once()
        release.assert_not_called()

    async def test_complete_empty_original_any_needs_no_read_and_no_acceptance_rewrite(self):
        ctx = Context()
        original = sequence_step(error=0, snapshot=b"", invoked=1, accepted=0)
        with patch.object(data, "invoke_property_action", return_value=N(result=original, adapter=0xA000)) as action, \
                patch.object(data, "snapshot") as snapshot, \
                patch.object(data, "release", return_value=released_value()):
            result = await SetupAPI(ctx).apply_rules()
        self.assertIs(result["native_result"], original)
        self.assertEqual(original.accepted, 0)
        self.assertTrue(result["complete"])
        self.assertEqual(result["snapshot_attempts"], [])
        action.assert_called_once()
        snapshot.assert_not_called()

    async def test_real_context_pending_snapshot_resumes_supplement_not_original_action(self):
        import asyncio
        from pdx_runtime.query_executor import PendingOperation
        from pdx_runtime.v3_game._core import GameContext
        loop = asyncio.get_running_loop()
        submitted = asyncio.Event()
        original = sequence_step(error=234, snapshot=b"p", total=8)
        copied = replace(original.result, action=data.Action.SNAPSHOT, error=0,
            snapshot=b"complete", snapshot_bytes=8)
        class Query:
            adapter = 0xA000
            ready = False
            _released = False
            def read(self):
                if not self.ready:
                    return None
                self._released = True
                return copied
        supplement = Query()
        class Completed:
            adapter = 0xA000
            _released = True
            def __init__(self, result):
                self.result = result
            def read(self):
                return self.result
        def snapshot(*args, **options):
            loop.call_soon_threadsafe(submitted.set)
            return supplement
        ctx = GameContext(N(native=object(), namespace={}), process=object(), timeout=0, poll_interval=.001)
        with patch.object(data, "invoke_property_action", return_value=Completed(original)) as action, \
                patch.object(data, "snapshot", side_effect=snapshot) as read, \
                patch.object(data, "release", return_value=Completed(
                    N(action=data.Action.RELEASE, error=0, value_handle=0))) as release:
            pending = await SetupAPI(ctx).save_preset()
            self.assertIsInstance(pending, PendingOperation)
            await asyncio.wait_for(submitted.wait(), 1)
            self.assertIs(await pending.resume(timeout=0), pending)
            self.assertIn(supplement, pending.pending_queries)
            retained = next(x for x in ctx.owner_pool.snapshot() if isinstance(x, _Run))
            self.assertEqual(retained.values, [101])
            action.assert_called_once()
            release.assert_not_called()
            supplement.ready = True
            result = await pending.resume(timeout=1)
        self.assertIs(result["native_result"], original)
        self.assertIs(result["snapshot_attempts"][0]["query"], supplement)
        self.assertTrue(result["complete"])
        self.assertEqual(ctx.owner_pool.snapshot(), ())
        action.assert_called_once()
        read.assert_called_once()
        release.assert_called_once()


class SetupRuleUIStateTests(IsolatedAsyncioTestCase):
    expressions = ("IsHost", "IsInGame", "And( IsHost, Not( IsInGame ) )",
        "GameHasMultiplePlayers", "Not( GameHasMultiplePlayers )",
        "GetGameRules.IsPresetWindowShown")
    names = ("is_host", "is_in_game", "rule_arrows_condition", "multiplayer",
        "preset_strip_condition", "preset_popup_shown")

    def page(self, bits=(0, 1, 0, 1, 0, 0), *, error=0, count_valid=True):
        cells = tuple(data.ProjectionCell(0, index, 0, data.Kind.BOOL, -1, bit, b"", 0)
            for index, bit in enumerate(bits))
        return data.ProjectionPage(error, 0, 6, 0, 77, 1, count_valid, 1, cells)


    async def test_real_encoder_keeps_exact_six_expressions_and_existing_page_alias(self):
        ctx = Context()
        page = self.page()
        def submit(client, initial, **options):
            header = data.PAGE_REQUEST.unpack_from(initial)
            self.assertEqual((header[4], header[8], header[9], header[10], header[18]),
                (6, 0, 0, 1, 0))
            expressions = []
            for index in range(6):
                offset, size = data.PAGE_SPAN.unpack_from(initial,
                    header[3] + index * data.PAGE_SPAN.size)
                expressions.append(initial[offset:offset + size].decode())
            self.assertEqual(tuple(expressions), self.expressions)
            self.assertEqual(options["callback_symbol"], "PdxV3DataProjectPageInterface")
            return N(result=page, cleanup_error=None)
        with patch.object(data, "submit_callback", side_effect=submit) as callback:
            view = await SetupAPI(ctx).rule_ui_state()
        self.assertIs(view.native_page, page)
        self.assertEqual(view.field_names, self.names)
        self.assertEqual(view.source, "rule_ui_state")
        self.assertIs(view.rows[0].values["rule_arrows_condition"], False)
        callback.assert_called_once()

    async def test_native_conditions_are_not_recomputed_from_separate_input_observations(self):
        ctx = Context()
        # Native leaves are separate observations. Do not replace a copied
        # condition with Python logic over flags that may have changed in between.
        page = self.page((0, 1, 1, 1, 1, 1))
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)):
            view = await SetupAPI(ctx).rule_ui_state()
        values = view.rows[0].values
        self.assertIs(values["is_host"], False)
        self.assertIs(values["is_in_game"], True)
        self.assertIs(values["rule_arrows_condition"], True)
        self.assertIs(values["multiplayer"], True)
        self.assertIs(values["preset_strip_condition"], True)
        self.assertIs(values["preset_popup_shown"], True)
        self.assertNotIn("editable", values)
        self.assertNotIn("authorized", values)
        self.assertNotIn("rendered", values)

    async def test_false_none_original_getter_error_and_zero_bits_remain_distinct(self):
        ctx = Context()
        original = self.page()
        missing = data.ProjectionCell(0, 0, 50, data.Kind.EMPTY, -1, 0, b"", 0)
        page = replace(original, cells=(missing,) + original.cells[1:])
        with patch.object(data, "project_page", return_value=N(result=page, cleanup_error=None)):
            view = await SetupAPI(ctx).rule_ui_state()
        row = view.rows[0]
        self.assertIs(row.cells["is_host"], missing)
        self.assertIsNone(row.values["is_host"])
        self.assertIn("50", row.field_issues["is_host"])
        self.assertIs(row.values["rule_arrows_condition"], False)
        self.assertEqual(row.cells["rule_arrows_condition"].raw_bits, 0)
        self.assertIs(view.native_page, page)


    async def test_copy_failure_keeps_original_query_and_same_partial_condition_page(self):
        ctx = Context()
        page = replace(self.page(error=234), cells=self.page().cells[:3])
        failure = RuntimeError("original UI condition copy failed")
        query = N(result=failure, partial_result=page)
        with patch.object(data, "project_page", return_value=query) as project:
            with self.assertRaises(RuntimeError) as caught:
                await SetupAPI(ctx).rule_ui_state()
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.query, query)
        self.assertIs(failure.setup_page.native_page, page)
        self.assertEqual(failure.setup_page.source, "rule_ui_state")
        self.assertIs(failure.setup_page.rows[0].values["rule_arrows_condition"], False)
        self.assertNotIn("preset_popup_shown", failure.setup_page.rows[0].cells)
        project.assert_called_once()


    async def test_pending_resumes_original_condition_query_without_replay(self):
        import asyncio
        from pdx_runtime.query_executor import PendingOperation
        from pdx_runtime.v3_game._core import GameContext
        loop = asyncio.get_running_loop()
        submitted = asyncio.Event()
        original = self.page()
        class Query:
            adapter = 0xA000
            ready = False
            _released = False
            def read(self):
                if not self.ready:
                    return None
                self._released = True
                return original
        query = Query()
        def submit(*args, **options):
            loop.call_soon_threadsafe(submitted.set)
            return query
        ctx = GameContext(N(native=object(), namespace={}), process=object(),
            timeout=0, poll_interval=.001)
        with patch.object(data, "project_page", side_effect=submit) as project:
            pending = await SetupAPI(ctx).rule_ui_state()
            self.assertIsInstance(pending, PendingOperation)
            await asyncio.wait_for(submitted.wait(), 1)
            self.assertIs(await pending.resume(timeout=0), pending)
            self.assertIn(query, pending.pending_queries)
            query.ready = True
            view = await pending.resume(timeout=1)
        self.assertIs(view.native_page, original)
        self.assertEqual(ctx.owner_pool.snapshot(), ())
        project.assert_called_once()


class SetupAchievementFlagTests(IsolatedAsyncioTestCase):
    async def test_original_setting_flag_uses_real_fields_and_keeps_false_true_and_unknown(self):
        cases = (("rules", data.Kind.BOOL, 0, 0, False),
                 ("rule_choices", data.Kind.BOOL, 1, 0, True),
                 ("preset_settings", data.Kind.EMPTY, 0, 50, None))
        for source, kind, bits, error, expected in cases:
            with self.subTest(source=source):
                ctx = Context()
                original_cell = data.ProjectionCell(0, 6, error, kind, -1, bits, b"", 0)
                original = data.ProjectionPage(0, 0, 7, 0, 77, 1, True, 1, (original_cell,))
                def submit(client, initial, **options):
                    header = data.PAGE_REQUEST.unpack_from(initial)
                    self.assertEqual(header[4], 7)
                    self.assertEqual(data.PAGE_FIELD.unpack_from(initial,
                        header[3] + 5 * data.PAGE_FIELD.size), (3, 0, 0, 0))
                    flag_kind, offset, size, reserved = data.PAGE_FIELD.unpack_from(
                        initial, header[3] + 6 * data.PAGE_FIELD.size)
                    self.assertEqual((flag_kind, reserved), (0, 0))
                    expression = ("GuiGameRule.GetSetting.HasFlag( 'blocks_achievements' )"
                        if source == "rules" else "GameRuleSetting.HasFlag( 'blocks_achievements' )")
                    self.assertEqual(initial[offset:offset + size], expression.encode())
                    self.assertEqual(options["callback_symbol"], "PdxV3DataProjectRuleFieldsInterface")
                    return N(result=original, cleanup_error=None)
                with patch.object(data, "submit_callback", side_effect=submit) as callback:
                    api = SetupAPI(ctx)
                    view = (await api.rules(include_achievement_flags=True) if source == "rules"
                        else await getattr(api, source)(0, include_achievement_flags=True))
                self.assertIs(view.native_page, original)
                self.assertEqual(view.field_names[-1], "blocks_achievements")
                self.assertEqual(view.field_names[5], "rule_key")
                self.assertIs(view.rows[0].cells["blocks_achievements"], original_cell)
                self.assertIs(view.rows[0].values["blocks_achievements"], expected)
                if error:
                    self.assertIn("50", view.rows[0].field_issues["blocks_achievements"])
                callback.assert_called_once()

    async def test_non_boolean_include_option_is_rejected_before_submission(self):
        ctx = Context()
        with patch.object(data, "project_page") as project:
            for invalid in (0, None, "yes"):
                with self.subTest(invalid=invalid), self.assertRaises(TypeError):
                    await SetupAPI(ctx).rules(include_achievement_flags=invalid)
        self.assertEqual(ctx.performs, [])
        project.assert_not_called()
