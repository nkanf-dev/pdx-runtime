"""CPU-only real result classes for shared Run ownership and snapshot completion."""
from dataclasses import replace
from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pdx_runtime import v3_access as access, v3_data as data
from pdx_runtime.query_executor import Diagnostics, RetainedOwners
from pdx_runtime.v3_game._run import Run as _Run


def action_result(*, handle=101, error=0, snapshot=b"value", total=None,
                  declared=None, invoked=1, accepted=1):
    raw = bytearray(data.encode_action_request("Type.OnEdit", data.TypedInput.utf8_string("input"),
        output_bytes=max(32, len(snapshot))))
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
    return replace(result, result=replace(result.result, snapshot=snapshot))


def sequence_result(*steps, error=0, completed=None, stopped=False, copied_bytes=32):
    payload = data.ActionSequenceResult(32, error, len(steps), 32,
        len(steps) if completed is None else completed, stopped, tuple(steps), copied_bytes)
    raw = bytearray(access.encode_request(0xA100, 0, 0, mode=1, flag=0))
    header = list(access.REQUEST.unpack_from(raw))
    header[1], header[12], header[13] = 0, 1, 1
    access.REQUEST.pack_into(raw, 0, *header)
    return replace(access.AccessResult.from_bytes(bytes(raw)), callee_result=payload)


def data_query(result, adapter=0xA000):
    query = object.__new__(data.DataQuery)
    query.adapter, query.test_result = adapter, result
    return query


class Context:
    def __init__(self):
        self.native, self.process = object(), object()
        self.adapters = {"data": "original-data", "access": 0xB000}
        self.world = 77
        self.owner_pool, self.diagnostics = RetainedOwners(), Diagnostics()
        self.queries = []

    async def query(self, label, factory, **options):
        query = factory()
        self.queries.append((label, query))
        error = getattr(query, "test_error", None)
        if error is not None:
            raise error
        return query.test_result


def access_query(result, *, data_adapter=0xA000):
    return N(test_result=result, adapter=0xB000, data_adapter=data_adapter,
        completion_status={"state": "done", "callback_error": 574})


class RunSequenceTests(IsolatedAsyncioTestCase):
    async def test_adopts_real_action_handles_even_beyond_completed_and_native_error(self):
        ctx = Context()
        run = _Run(ctx, "sequence")
        original = sequence_result(action_result(handle=11, error=234),
            action_result(handle=12, error=21, invoked=0, accepted=0),
            action_result(handle=0, error=997, invoked=0, accepted=0),
            error=21, completed=1, stopped=True)
        query = access_query(original)
        observed = await run.capture("actions", lambda: query)
        self.assertIs(observed, original)
        self.assertEqual(run.values, [11, 12])
        self.assertEqual(run.value_adapters, {11: 0xA000, 12: 0xA000})
        self.assertEqual(run.adapters["data"], 0xA000)
        released = []
        def release(native, handle, **options):
            released.append((handle, options["adapter"]))
            return data_query(replace(original.callee_result.native_results[0].result,
                action=data.Action.RELEASE, value_handle=0, error=0))
        with patch.object(data, "release", side_effect=release):
            await run.finish({"native_result": original})
        self.assertEqual(released, [(12, 0xA000), (11, 0xA000)])
        self.assertEqual(ctx.owner_pool.snapshot(), ())

    async def test_action22_actual_dataquery_adopts_and_pins_new_owner(self):
        ctx = Context()
        run = _Run(ctx, "owned-context")
        original = replace(action_result(handle=22).result, action=data.Action.CREATE_OWNED_CONTEXT)
        query = data_query(original, 0xC000)
        self.assertIs(await run.capture("context", lambda: query), original)
        self.assertEqual(run.values, [22])
        self.assertEqual(run.value_adapters, {22: 0xC000})
        self.assertEqual(run.adapters["data"], 0xC000)

    async def test_partial_read_adopts_all_known_prefix_and_retains_original_query(self):
        ctx = Context()
        run = _Run(ctx, "partial")
        original = sequence_result(action_result(handle=31), action_result(handle=32, error=234),
            error=997, completed=0, copied_bytes=24)
        query = access_query(original)
        error = data.ActionSequenceReadError(query, original, b"copied prefix")
        query.test_error = error
        with self.assertRaises(data.ActionSequenceReadError) as caught:
            await run.capture("actions", lambda: query)
        self.assertIs(caught.exception, error)
        self.assertIs(run.uncertain_query, query)
        self.assertTrue(run.retain_unknown)
        self.assertEqual(run.values, [31, 32])
        self.assertIs(run.operations[0][1], original)
        result = await run.finish({"native_result": original})
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertIn(run, ctx.owner_pool.snapshot())
        self.assertNotEqual(run.status, "complete")

    async def test_real_late_parser_valueerror_adopts_prefix_and_preserves_same_error(self):
        ctx = Context()
        ctx.process = N(export_owner=lambda handler: 0xA000)
        run = _Run(ctx, "late-parse")
        steps = (data.ActionStep("Type.First", data.TypedInput.empty(), data.ActionContinuation.ALWAYS),
                 data.ActionStep("Type.Second", data.TypedInput.empty(), data.ActionContinuation.ALWAYS))
        packet = bytearray(data.encode_action_sequence(steps))
        first_offset = data.ACTION_SEQUENCE_STEP.unpack_from(packet, data.ACTION_SEQUENCE.size)[0]
        header = list(data.REQUEST.unpack_from(packet, first_offset))
        header[1], header[11], header[14] = 0, data.Kind.EMPTY, 91
        data.REQUEST.pack_into(packet, first_offset, *header)
        data.ACTION_SEQUENCE_STEP.pack_into(packet,
            data.ACTION_SEQUENCE.size + data.ACTION_SEQUENCE_STEP.size, len(packet) + 8, 0)
        raw = access.encode_request(0xA100, 0, 0, mode=1, flag=0) + bytes(packet)
        original_errors = []
        def decode(value):
            try:
                return access.AccessResult.from_bytes(value, payload_size=len(packet),
                    decode_payload=data.ActionSequenceResult.from_bytes)
            except ValueError as error:
                original_errors.append(error)
                raise
        query = N(adapter=0xB000, completion_status={"callback_error": 574}, decode_result=decode)
        async def execute(label, factory, **options):
            original_query = factory()
            return original_query.decode_result(raw)
        ctx.query = execute
        with patch("pdx_runtime.v3_wire.resolve_v3_export", return_value=0xA100), \
                patch.object(access, "submit_callback", return_value=query):
            with self.assertRaises(ValueError) as caught:
                await run.capture("actions", lambda: data.invoke_action_sequence(
                    ctx.native, steps, adapter="data", access_adapter=0xB000, process=ctx.process))
        self.assertIs(caught.exception, original_errors[0])
        self.assertIs(caught.exception.query, query)
        self.assertIs(caught.exception.partial_result, query.partial_result)
        self.assertIs(caught.exception.partial_sequence, query.partial_result.callee_result)
        self.assertEqual(run.values, [91])
        self.assertEqual(run.value_adapters, {91: 0xA000})
        self.assertIs(run.uncertain_query, query)
        self.assertTrue(run.retain_unknown)
        self.assertNotEqual(run.status, "complete")

    async def test_missing_sequence_data_adapter_never_uses_access_adapter(self):
        ctx = Context()
        run = _Run(ctx, "missing-adapter")
        original = sequence_result(action_result(handle=41))
        query = access_query(original, data_adapter=None)
        self.assertIs(await run.capture("actions", lambda: query), original)
        self.assertEqual(run.value_adapters, {41: None})
        self.assertTrue(run.retain_unknown)
        self.assertIs(run.uncertain_query, query)
        with patch.object(data, "release") as release:
            await run.finish({"native_result": original})
        release.assert_not_called()
        self.assertIn(run, ctx.owner_pool.snapshot())

    async def test_one_snapshot_per_duplicate_handle_keeps_each_original_step_receipt(self):
        ctx = Context()
        run = _Run(ctx, "duplicate")
        first = action_result(handle=51, error=234, snapshot=b"p", total=7)
        second = action_result(handle=51, error=234, snapshot=b"q", total=9000)
        original = sequence_result(first, second, error=234)
        await run.capture("actions", lambda: access_query(original))
        copied = replace(first.result, action=data.Action.SNAPSHOT, error=0,
            snapshot=b"recovered", snapshot_bytes=9, total=9)
        with patch.object(data, "snapshot", return_value=data_query(copied)) as snapshot, \
                patch.object(data, "invoke_action") as action, \
                patch.object(data, "invoke_action_sequence") as sequence:
            result = await run.complete_action_inputs(original)
        self.assertIs(result["native_result"], original)
        self.assertTrue(result["complete"])
        attempt, = result["snapshot_attempts"]
        self.assertEqual(attempt["step_indices"], [0, 1])
        self.assertEqual(attempt["originals"], [first, second])
        self.assertIs(attempt["native_result"], copied)
        snapshot.assert_called_once_with(ctx.native, 51, output_bytes=9000,
            adapter=0xA000, process=ctx.process)
        action.assert_not_called()
        sequence.assert_not_called()
        self.assertEqual(run.values, [51])
        self.assertEqual(first.error, 234)

    async def test_error0_short_actual_copy_still_snapshots_original_any(self):
        ctx = Context()
        run = _Run(ctx, "short-copy")
        first = action_result(handle=61, snapshot=b"p", total=4, declared=4)
        await run.capture("action", lambda: data_query(first))
        copied = replace(first.result, action=data.Action.SNAPSHOT,
            snapshot=b"full", snapshot_bytes=4, total=4)
        with patch.object(data, "snapshot", return_value=data_query(copied)) as snapshot:
            result = await run.complete_action_inputs(first)
        self.assertTrue(result["complete"])
        snapshot.assert_called_once()
        self.assertIs(result["native_result"], first)

    async def test_error0_empty_is_complete_but_failed_zero_length_supplement_is_not(self):
        ctx = Context()
        run = _Run(ctx, "empty-and-error")
        empty = action_result(handle=71, snapshot=b"")
        failed = action_result(handle=72, error=574, snapshot=b"", total=0)
        original = sequence_result(empty, failed, error=574)
        await run.capture("actions", lambda: access_query(original))
        copied = replace(failed.result, action=data.Action.SNAPSHOT, error=21)
        with patch.object(data, "snapshot", return_value=data_query(copied)) as snapshot, \
                patch.object(data, "release") as release:
            completion = await run.complete_action_inputs(original)
            result = await run.finish(completion)
        self.assertFalse(result["complete"])
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(run.values, [71, 72])
        self.assertTrue(run.retain_unknown)
        snapshot.assert_called_once_with(ctx.native, 72, output_bytes=4096,
            adapter=0xA000, process=ctx.process)
        release.assert_not_called()

    async def test_pending_supplement_attaches_completion_and_retains_same_query(self):
        ctx = Context()
        run = _Run(ctx, "pending-copy")
        original = action_result(handle=81, error=234, snapshot=b"p", total=9)
        await run.capture("action", lambda: data_query(original))
        query = data_query(None)
        error = RuntimeError("snapshot outcome unknown")
        error.query = query
        query.test_error = error
        with patch.object(data, "snapshot", return_value=query) as snapshot:
            with self.assertRaises(RuntimeError) as caught:
                await run.complete_action_inputs(original)
        self.assertIs(caught.exception, error)
        self.assertIs(error.snapshot_completion["native_result"], original)
        self.assertFalse(error.snapshot_completion["complete"])
        self.assertIs(error.snapshot_completion["snapshot_attempts"][0]["query"], query)
        self.assertIs(run.uncertain_query, query)
        self.assertTrue(run.retain_unknown)
        self.assertEqual(run.values, [81])
        snapshot.assert_called_once()
