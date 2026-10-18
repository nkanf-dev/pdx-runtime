"""CPU-only ownership tests for the shared run_mod.Run native scope seam."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.query_executor import Diagnostics, RetainedOwners
from pdx_runtime.session import RuntimeSession
from pdx_runtime.v3_game._core import GameContext
from pdx_runtime.v3_game._run import Run as _Run, retained_runs


class FakeQuery:
    def __init__(self, result, *, adapter="query-adapter"):
        self.result = result
        self.adapter = adapter


class FakeNative:
    def __init__(self):
        self.close_count = 0

    def close(self):
        self.close_count += 1


def actual_data_query(result, adapter):
    query = object.__new__(data.DataQuery)
    query.adapter = adapter
    query._test_result = result
    return query


class ScopeContext:
    def __init__(self, *, world=77):
        self.native = object()
        self.process = object()
        self.adapters = {
            "data": "ctx-data",
            "commands": "ctx-commands",
            "access": "ctx-access",
            "call": "ctx-call",
        }
        self.world = world
        self.owner_pool = RetainedOwners()
        self.diagnostics = Diagnostics()
        self.queries = []
        self.submitted_queries = []

    async def query(self, label, factory, **kwargs):
        self.queries.append((label, kwargs))
        query = factory()
        self.submitted_queries.append(query)
        if isinstance(query, FakeQuery):
            return query.result
        return getattr(query, "_test_result", query)


def native_result(action, *, error=0, handle=0, value=None, accepted=0):
    return SimpleNamespace(
        action=action,
        error=error,
        value_handle=handle,
        value=value,
        accepted=accepted,
    )


class FakeOwner:
    def __init__(self, *, world=77, validation=None, submission=None,
                 validation_outcome="owned", submission_outcome="consumed"):
        self.command = 0x9000
        self.world = world
        self.type_token = 901
        self.pending = None
        self.attempt = None
        self.calls = []
        self.validation = validation or SimpleNamespace(
            error=0, validated=1, reason="", command=self.command
        )
        self.submission = submission or SimpleNamespace(
            error=0, accepted=1, command=0
        )
        self.validation_outcome = validation_outcome
        self.submission_outcome = submission_outcome

    def validate(self, capacity):
        self.calls.append(("validate", capacity))
        self.attempt = SimpleNamespace(outcome=self.validation_outcome)
        return self.validation

    def submit(self, flags):
        self.calls.append(("submit", flags))
        self.attempt = SimpleNamespace(outcome=self.submission_outcome)
        if self.submission_outcome == "consumed":
            self.command = 0
        return self.submission

    def destroy(self):
        self.calls.append(("destroy", None))
        self.command = 0
        self.attempt = SimpleNamespace(outcome="consumed")
        return SimpleNamespace(error=0, command=0)


class OwnerPoolWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_session_context_executor_and_run_share_one_pool(self):
        native = FakeNative()
        session = RuntimeSession(native)
        process = object()
        ctx = GameContext(session, process=process)

        self.assertIs(ctx.owner_pool, session.owner_pool)
        self.assertIs(ctx._executor.owner_pool, session.owner_pool)
        run = _Run(ctx, "registered")
        self.assertIs(run.retained_scope, session.owner_pool)
        self.assertIn(run, session.owner_pool.snapshot())
        self.assertFalse(hasattr(ctx, "_retained_native_runs"))
        self.assertEqual(retained_runs(ctx), [run])

        await run.cleanup()
        self.assertNotIn(run, session.owner_pool.snapshot())
        session.close()
        self.assertEqual(native.close_count, 1)


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_raw_action_retains_new_any_even_on_error_and_acceptance(self):
        ctx = ScopeContext()
        run = _Run(ctx, "capture", namespace="military")
        nested = native_result(data.Action.INVOKE_ACTION, error=122, handle=41)
        receipt = SimpleNamespace(error=122, accepted=1, result=nested)

        observed = await run.capture(
            "invoke",
            lambda: FakeQuery(receipt, adapter=0x7FF60000),
        )

        self.assertIs(observed, receipt)
        self.assertEqual(run.operations, [("invoke", receipt)])
        self.assertEqual(run.values, [41])
        self.assertEqual(run.value_adapters, {41: 0x7FF60000})
        self.assertIn(run, ctx.owner_pool.snapshot())

    async def test_snapshot_release_and_command_value_echoes_are_never_adopted(self):
        ctx = ScopeContext()
        run = _Run(ctx, "echoes")
        for action in (data.Action.SNAPSHOT, data.Action.RELEASE, data.Action.COMMAND_VALUE):
            receipt = SimpleNamespace(
                error=0,
                result=native_result(action, handle=1000 + int(action)),
            )
            await run.capture(action.name.lower(), lambda receipt=receipt: FakeQuery(receipt))

        self.assertFalse(run.values)
        self.assertFalse(run.value_adapters)
        self.assertEqual(len(run.operations), 3)
        await run.cleanup()
        self.assertEqual(run.status, "complete")


class AffinityTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_uses_captured_query_adapter_and_pinned_native_process(self):
        ctx = ScopeContext()
        original_native, original_process = ctx.native, ctx.process
        run = _Run(ctx, "affinity")
        receipt = native_result(data.Action.EVALUATE, handle=55, value="owned")
        await run.capture(
            "source",
            lambda: FakeQuery(receipt, adapter=0x7FF612340000),
        )

        ctx.native = object()
        ctx.process = object()
        ctx.adapters["data"] = "new-context-adapter"
        released = []

        def release(native, handle, *, adapter, process):
            released.append((native, handle, adapter, process))
            return FakeQuery(native_result(data.Action.RELEASE, handle=0), adapter="release-query")

        with patch.object(data, "release", release):
            await run.cleanup()

        self.assertEqual(
            released,
            [(original_native, 55, 0x7FF612340000, original_process)],
        )
        self.assertEqual(run.status, "complete")
        self.assertFalse(run.values)
        self.assertNotIn(run, ctx.owner_pool.snapshot())

    async def test_actual_data_query_adapter_pins_default_and_each_value_releases_to_its_producer(self):
        ctx = ScopeContext()
        run = _Run(ctx, "actual-adapters")
        first = native_result(data.Action.EVALUATE, handle=61, value="first")
        second = native_result(data.Action.EVALUATE, handle=62, value="second")

        await run.capture("first", lambda: actual_data_query(first, 0x7FF610000000))
        self.assertEqual(run.adapters["data"], 0x7FF610000000)
        await run.capture("second", lambda: actual_data_query(second, 0x7FF620000000))
        self.assertEqual(run.adapters["data"], 0x7FF620000000)
        self.assertEqual(run.value_adapters, {
            61: 0x7FF610000000,
            62: 0x7FF620000000,
        })
        released = []

        def release(native, handle, *, adapter, process):
            released.append((handle, adapter))
            return FakeQuery(native_result(data.Action.RELEASE))

        with patch.object(data, "release", release):
            await run.cleanup()

        self.assertEqual(released, [
            (62, 0x7FF620000000),
            (61, 0x7FF610000000),
        ])
        self.assertFalse(run.values)
        self.assertNotIn(run, ctx.owner_pool.snapshot())

    async def test_scope_stays_strong_after_diagnostic_rotation_then_complete_removes_it(self):
        ctx = ScopeContext()
        run = _Run(ctx, "rotation")
        for index in range(150):
            ctx.diagnostics.append({"index": index})

        self.assertNotIn(run, ctx.diagnostics["operations"])
        self.assertIn(run, ctx.owner_pool.snapshot())
        await run.cleanup()
        self.assertEqual(run.status, "complete")
        self.assertNotIn(run, ctx.owner_pool.snapshot())


class UnknownCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_unknown_capture_with_no_values_survives_task_done_and_diagnostic_rotation(self):
        ctx = ScopeContext()
        holder = {}
        uncertain_query = FakeQuery(SimpleNamespace(error=997), adapter="uncertain-module")

        async def failing_query(label, factory, **kwargs):
            query = factory()
            error = RuntimeError("first capture outcome unknown")
            error.query = query
            raise error

        ctx.query = failing_query

        async def fail_first_capture():
            run = holder["run"] = _Run(ctx, "first-unknown")
            await run.capture("unknown", lambda: uncertain_query)

        task = asyncio.create_task(fail_first_capture())
        with self.assertRaisesRegex(RuntimeError, "first capture outcome unknown"):
            await task
        await asyncio.sleep(0)

        run = holder["run"]
        self.assertFalse(run.values)
        self.assertTrue(run.retain_unknown)
        self.assertIs(run.uncertain_query, uncertain_query)
        for index in range(150):
            ctx.diagnostics.append({"index": index})
        self.assertNotIn(run, ctx.diagnostics["operations"])
        self.assertIn(run, ctx.owner_pool.snapshot())
        self.assertNotEqual(run.status, "complete")
        with self.assertRaisesRegex(RuntimeError, "unresolved work"):
            await run.cleanup()
        self.assertIn(run, ctx.owner_pool.snapshot())

    async def test_known_prequery_value_and_type_errors_complete_and_discard_empty_scope(self):
        for error_type in (ValueError, TypeError):
            with self.subTest(error_type=error_type.__name__):
                ctx = ScopeContext()
                holder = {}

                def fail_before_query():
                    raise error_type("known local input error")

                async def fail_known_capture():
                    run = holder["run"] = _Run(ctx, "known-prequery")
                    await run.capture("known", fail_before_query)

                task = asyncio.create_task(fail_known_capture())
                with self.assertRaisesRegex(error_type, "known local input error"):
                    await task
                await asyncio.sleep(0)

                run = holder["run"]
                self.assertFalse(getattr(run, "retain_unknown", False))
                self.assertIsNone(getattr(run, "uncertain_query", None))
                self.assertFalse(run.values)
                self.assertFalse(run.owners)
                self.assertFalse(run.buffers)
                self.assertFalse(ctx.submitted_queries)
                self.assertEqual(run.status, "complete")
                self.assertNotIn(run, ctx.owner_pool.snapshot())

    async def test_unknown_capture_retains_query_graph_across_diagnostics_and_session_close(self):
        native = FakeNative()
        session = RuntimeSession(native)
        ctx = ScopeContext()
        ctx.native = native
        ctx.session = session
        ctx.owner_pool = session.owner_pool
        run = _Run(ctx, "unknown-capture")
        source = native_result(data.Action.EVALUATE, handle=91, value="source")
        await run.capture("source", lambda: FakeQuery(source, adapter="source-module"))
        uncertain_query = FakeQuery(SimpleNamespace(error=997), adapter="uncertain-module")

        async def failing_query(label, factory, **kwargs):
            query = factory()
            error = RuntimeError("capture outcome unknown")
            error.query = query
            raise error

        ctx.query = failing_query
        with self.assertRaisesRegex(RuntimeError, "capture outcome unknown"):
            await run.capture("unknown", lambda: uncertain_query)

        self.assertTrue(run.retain_unknown)
        self.assertIs(run.uncertain_query, uncertain_query)
        for index in range(150):
            ctx.diagnostics.append({"index": index})
        self.assertNotIn(run, ctx.diagnostics["operations"])
        self.assertIn(run, session.owner_pool.snapshot())
        self.assertEqual(run.values, [91])

        with patch.object(data, "release", side_effect=AssertionError("must not release")):
            with self.assertRaisesRegex(RuntimeError, "unresolved work"):
                await run.cleanup()
            main = {"status": "observed", "value": 9}
            finished = await run.finish(main)

        self.assertIs(finished, main)
        self.assertEqual(main["value"], 9)
        self.assertEqual(main["cleanup"]["status"], "retained_pending_or_unknown")
        session.close()
        self.assertEqual(native.close_count, 1)
        self.assertIn(run, session.owner_pool.snapshot())
        self.assertEqual(run.values, [91])

    async def test_task_done_discards_completely_empty_scope_without_dead_record(self):
        ctx = ScopeContext()
        holder = {}

        async def create_empty_scope():
            holder["run"] = _Run(ctx, "empty-task")

        task = asyncio.create_task(create_empty_scope())
        await task
        await asyncio.sleep(0)

        run = holder["run"]
        self.assertFalse(run.values)
        self.assertFalse(run.owners)
        self.assertFalse(run.buffers)
        self.assertNotIn(run, ctx.owner_pool.snapshot())


class GeneratedOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_carrier_generation_world_wins_over_zero_or_stale_context_without_serialize(self):
        for context_world in (0, 12):
            with self.subTest(context_world=context_world):
                ctx = ScopeContext(world=context_world)
                original_native, original_process = ctx.native, ctx.process
                run = _Run(ctx, "generated", namespace="military")
                producer_handle = 71
                generated = data.NativeDataCommand(
                    0x4000, 0x5000, 0x4018, producer_handle,
                    ".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@", 0,
                )
                carrier = data.NativeDataCommand(
                    0x4000, 0x5000, 0x4018, producer_handle,
                    ".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@", 77,
                )
                made = native_result(
                    data.Action.EVALUATE,
                    handle=producer_handle,
                    value=generated,
                )
                carried = native_result(data.Action.COMMAND_VALUE, value=carrier)
                clone = SimpleNamespace(
                    error=0,
                    action=commands.Action.CLONE,
                    command=0,
                    cloned_command=0x9000,
                    type_token=901,
                    world=77,
                )
                request_calls = []
                release_calls = []
                owner = FakeOwner(world=77)
                owner.command = 0

                def evaluate(native, expression, bindings, **kwargs):
                    self.assertIs(native, original_native)
                    self.assertIs(kwargs["process"], original_process)
                    return FakeQuery(made, adapter="producer-module-base")

                def command_value(native, handle, **kwargs):
                    self.assertIs(native, original_native)
                    self.assertEqual(handle, producer_handle)
                    self.assertIs(kwargs["process"], original_process)
                    return FakeQuery(carried, adapter="data-module-base")

                def request(native, action, **kwargs):
                    request_calls.append((native, action, kwargs))
                    return FakeQuery(clone, adapter="commands-module-base")

                def release(native, handle, **kwargs):
                    release_calls.append((native, handle, kwargs))
                    return FakeQuery(native_result(data.Action.RELEASE), adapter="release-module")

                with patch.multiple(data, evaluate=evaluate, command_value=command_value, release=release), \
                        patch.object(commands, "request", request), \
                        patch.object(commands.NativeCommand, "from_result", return_value=owner), \
                        patch.object(commands, "decode_command", side_effect=AssertionError("serialize forbidden")):
                    observed_owner = await run.generated_owner("Country.OriginalProducer", ())
                    await run.cleanup()

                self.assertIs(observed_owner, owner)
                self.assertEqual(len(request_calls), 1)
                native, action, kwargs = request_calls[0]
                self.assertIs(native, original_native)
                self.assertEqual(action, commands.Action.CLONE)
                self.assertEqual(kwargs["world"], 77)
                self.assertIs(kwargs["process"], original_process)
                self.assertEqual(release_calls[0][1], producer_handle)
                self.assertEqual(release_calls[0][2]["adapter"], "producer-module-base")


class ValidateSubmitTests(unittest.IsolatedAsyncioTestCase):
    async def test_validation_122_and_accepted_submission_keep_original_receipts_once(self):
        ctx = ScopeContext()
        run = _Run(ctx, "submit", namespace="military")
        validation = SimpleNamespace(error=122, validated=1, reason="partial rea", command=0x9000)
        submission = SimpleNamespace(error=0, accepted=1, command=0)
        owner = FakeOwner(validation=validation, submission=submission)
        run.owners.append(owner)
        public = {"accepted": None}

        accepted = await run.validate_submit(owner, public)

        self.assertTrue(accepted)
        self.assertEqual(owner.calls, [("validate", 65536), ("submit", 4)])
        self.assertIs(public["validation"], validation)
        self.assertIs(public["native_result"], submission)
        self.assertEqual(public["native_error"], 0)
        self.assertEqual(public["reason"], "partial rea")
        self.assertTrue(public["eligible"])
        self.assertTrue(public["accepted"])
        self.assertEqual(public["status"], "accepted")
        self.assertEqual(run.operations, [
            ("command.validate", validation),
            ("command.submit", submission),
        ])
        await run.finish(public)
        self.assertEqual(run.status, "complete")

    async def test_validation_error_with_validated_receipt_keeps_eligible_but_does_not_submit(self):
        ctx = ScopeContext()
        run = _Run(ctx, "validation-error", namespace="military")
        validation = SimpleNamespace(error=5, validated=1, reason="raw validation", command=0x9000)
        owner = FakeOwner(validation=validation)
        run.owners.append(owner)
        public = {"accepted": None}

        accepted = await run.validate_submit(owner, public)

        self.assertFalse(accepted)
        self.assertEqual(owner.calls, [("validate", 65536)])
        self.assertTrue(public["eligible"])
        self.assertIs(public["validation"], validation)
        self.assertIs(public["native_result"], validation)
        self.assertEqual(public["native_error"], 5)
        self.assertEqual(public["reason"], "raw validation")
        self.assertIsNone(public["accepted"])
        self.assertEqual(public["status"], "native_result")
        owner.command = 0
        await run.finish(public)

    async def test_submit_native_error_and_unknown_outcome_preserve_raw_result(self):
        ctx = ScopeContext()
        error_run = _Run(ctx, "submit-error", namespace="military")
        validation = SimpleNamespace(error=0, validated=1, reason="", command=0x9000)
        native_error = SimpleNamespace(error=5, accepted=1, command=0x9000)
        error_owner = FakeOwner(validation=validation, submission=native_error,
                                submission_outcome="owned")
        error_run.owners.append(error_owner)
        error_public = {"accepted": None}

        accepted = await error_run.validate_submit(error_owner, error_public)

        self.assertTrue(accepted)
        self.assertIs(error_public["native_result"], native_error)
        self.assertEqual(error_public["native_error"], 5)
        self.assertTrue(error_public["accepted"])
        self.assertEqual(error_public["status"], "accepted")
        error_owner.command = 0
        await error_run.finish(error_public)

        unconfirmed_ctx = ScopeContext()
        unconfirmed_run = _Run(unconfirmed_ctx, "submit-error-unconfirmed", namespace="military")
        unconfirmed_result = SimpleNamespace(error=5, accepted=0, command=0x9000)
        unconfirmed_owner = FakeOwner(validation=validation, submission=unconfirmed_result,
                                      submission_outcome="owned")
        unconfirmed_run.owners.append(unconfirmed_owner)
        unconfirmed_public = {"accepted": None}

        unconfirmed = await unconfirmed_run.validate_submit(unconfirmed_owner, unconfirmed_public)

        self.assertFalse(unconfirmed)
        self.assertIs(unconfirmed_public["native_result"], unconfirmed_result)
        self.assertEqual(unconfirmed_public["native_error"], 5)
        self.assertIsNone(unconfirmed_public["accepted"])
        self.assertEqual(unconfirmed_public["status"], "native_result")
        unconfirmed_owner.command = 0
        await unconfirmed_run.finish(unconfirmed_public)

        unknown_ctx = ScopeContext()
        unknown_run = _Run(unknown_ctx, "submit-unknown", namespace="military")
        unknown_submission = SimpleNamespace(error=0, accepted=1, command=0x9000)
        unknown_owner = FakeOwner(validation=validation, submission=unknown_submission,
                                  submission_outcome="unknown")
        unknown_run.owners.append(unknown_owner)
        unknown_public = {"accepted": None}

        settled = await unknown_run.validate_submit(unknown_owner, unknown_public)
        finished = await unknown_run.finish(unknown_public)

        self.assertFalse(settled)
        self.assertIs(finished, unknown_public)
        self.assertTrue(unknown_public["accepted"])
        self.assertIs(unknown_public["native_result"], unknown_submission)
        self.assertEqual(unknown_public["native_outcome"], "unknown")
        self.assertEqual(unknown_public["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertIn(unknown_run, unknown_ctx.owner_pool.snapshot())


class FinishTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_and_unknown_cleanup_errors_do_not_replace_main_result(self):
        known_ctx = ScopeContext()
        known_run = _Run(known_ctx, "known-cleanup")
        await known_run.capture("source", lambda: FakeQuery(
            native_result(data.Action.EVALUATE, handle=81), adapter="source-adapter"
        ))
        native_release_error = native_result(data.Action.RELEASE, error=21, handle=81)

        with patch.object(data, "release", lambda *args, **kwargs: FakeQuery(native_release_error)):
            main = {"status": "observed", "value": 7}
            finished = await known_run.finish(main)

        self.assertIs(finished, main)
        self.assertEqual(main["value"], 7)
        self.assertEqual(main["cleanup"]["status"], "native_error_retained")
        self.assertIs(main["cleanup"]["native_result"], native_release_error)
        self.assertEqual(known_run.values, [81])
        self.assertIn(known_run, known_ctx.owner_pool.snapshot())

        unknown_ctx = ScopeContext()
        unknown_run = _Run(unknown_ctx, "unknown-cleanup")
        await unknown_run.capture("source", lambda: FakeQuery(
            native_result(data.Action.EVALUATE, handle=82), adapter="source-adapter"
        ))
        pending_query = object()

        def unknown_release(*args, **kwargs):
            error = RuntimeError("release outcome unknown")
            error.query = pending_query
            raise error

        with patch.object(data, "release", unknown_release):
            main = {"status": "observed", "value": 8}
            finished = await unknown_run.finish(main)

        self.assertIs(finished, main)
        self.assertEqual(main["value"], 8)
        self.assertEqual(main["cleanup"]["status"], "cleanup_unknown_retained")
        self.assertIs(main["cleanup"]["query"], pending_query)
        self.assertTrue(unknown_run.retain_unknown)
        self.assertEqual(unknown_run.values, [82])
        self.assertIn(unknown_run, unknown_ctx.owner_pool.snapshot())


if __name__ == "__main__":
    unittest.main()
