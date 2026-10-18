"""CPU-only executor lifetime and adapter-alias checks; no native game calls."""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
import unittest

from pdx_runtime import query_executor as shared
from pdx_runtime.v3_game._run import ACTIVE_CONTEXT
from pdx_runtime.v3_game._core import GameContext


class Query:
    def __init__(self, result, gate=None):
        self.result, self.gate = result, gate
        self._result, self._released, self.reads = None, False, 0
        self.started = threading.Event()

    def read(self):
        self.started.set()
        self.reads += 1
        if self.gate is not None and not self.gate.is_set():
            return None
        self._result, self._released = self.result, True
        return self.result


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    def executor(self, **kwargs):
        return shared.SerializedQueryExecutor(timeout=.001, poll_interval=.001, **kwargs)

    async def test_generic_timeout_resume_keeps_exact_task_owner_and_query(self):
        executor, ready = self.executor(), asyncio.Event()
        query, factories, completions = Query(0, ready), [], []
        self.addCleanup(ready.set)
        graph = object()
        class Owner:
            owned_graph = graph
            def complete(self):
                completions.append(self.owned_graph)
                return query.read()
        owner = Owner()
        def submit():
            factories.append(query)
            return query
        pending = await executor.perform("owned", lambda: executor.query("read", submit, owner=owner))
        self.assertIsInstance(pending, shared.PendingResult)
        self.assertIs(pending.context, executor)
        task = pending.task
        self.assertIs(await pending.resume(timeout=0), pending)
        self.assertIs(pending.task, task)
        self.assertTrue(await asyncio.to_thread(query.started.wait, 1))
        self.assertEqual(pending.pending_queries, (query,))
        ready.set()
        self.assertEqual(await pending.resume(timeout=1), 0)
        self.assertEqual(factories, [query])
        self.assertTrue(completions)
        self.assertTrue(all(item is graph for item in completions))
        self.assertEqual(pending.pending_queries, ())
        self.assertEqual(json.loads(json.dumps(pending)), {"status": "pending", "operation": "owned"})

    async def test_caller_cancel_keeps_original_task_and_unknown_query(self):
        executor, ready, submitted = self.executor(), asyncio.Event(), asyncio.Event()
        executor.timeout = 1
        query, calls = Query(False, ready), []
        loop = asyncio.get_running_loop()
        def submit():
            calls.append(query)
            loop.call_soon_threadsafe(submitted.set)
            error = OSError("response lost")
            error.query = query
            raise error
        caller = asyncio.create_task(executor.perform("unknown", lambda: executor.query("read", submit)))
        await submitted.wait()
        task, = executor._operations
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertFalse(task.cancelled())
        self.assertIs(executor._locked_query, query)
        self.assertEqual(executor.pending_queries, [query])
        ready.set()
        self.assertIs(await asyncio.wait_for(task, 1), False)
        self.assertEqual(calls, [query])

    async def test_one_query_slot_without_workflow_lock(self):
        executor, ready, waiting = self.executor(), asyncio.Event(), asyncio.Event()
        query, calls = Query(1, ready), []
        self.addCleanup(ready.set)
        first = await executor.perform("first", lambda: executor.query("first", lambda: query))
        self.assertTrue(await asyncio.to_thread(query.started.wait, 1))
        async def second():
            waiting.set()
            return await executor.query("second", lambda: (calls.append(1), Query(2))[1])
        next_operation = await executor.perform("second", second)
        await waiting.wait()
        self.assertEqual(calls, [])
        self.assertIs(executor._locked_query, query)
        ready.set()
        self.assertEqual(await first.resume(timeout=1), 1)
        self.assertEqual(await next_operation.resume(timeout=1), 2)
        self.assertEqual(calls, [1])
        workflow_gate = asyncio.Event()
        slow = await executor.perform("workflow", workflow_gate.wait)
        self.assertEqual(await executor.query("independent", lambda: Query(3)), 3)
        workflow_gate.set()
        self.assertTrue(await slow.resume(timeout=1))

    async def test_unproved_busy_pipe_error_does_not_replay_factory_or_drop_existing_graph(self):
        from unittest.mock import Mock
        from pdx_runtime.native_bridge import BridgeError, NativeBridge
        api = Mock()
        original = BridgeError("WriteFile(pipe)", 170)
        api.write_file.return_value = False
        api.failure.return_value = original
        bridge = NativeBridge(42, api=api)
        bridge.handle = 123
        executor = self.executor(busy_submit_error=lambda error: error.winerror == 170)
        graph, calls = object(), []
        executor.owner_pool.retain(graph)
        def submit():
            calls.append(1)
            if len(calls) == 1:
                bridge.request("one-native-action")
            return Query("a second factory call must not happen")
        with self.assertRaises(BridgeError) as caught:
            await executor.query("uncertain", submit)
        self.assertIs(caught.exception, original)
        self.assertNotIn("delivery", original.details)
        self.assertEqual(calls, [1])
        api.write_file.assert_called_once()
        api.read_file.assert_not_called()
        api.close_handle.assert_not_called()
        self.assertEqual(executor.owner_pool.snapshot(), (graph,))
        self.assertEqual(executor.pending_queries, [])
        self.assertFalse(executor._query_lock.locked())

    async def test_actual_not_sent_transport_proof_allows_only_injected_retry(self):
        from unittest.mock import Mock
        from pdx_runtime.native_bridge import BridgeError, NativeBridge
        api = Mock()
        bridge = NativeBridge(42, api=api)  # Closed: request fails before WriteFile.
        executor = self.executor(busy_submit_error=lambda error: error.operation == "request")
        query, calls, errors = Query(False), [], []
        def submit():
            calls.append(1)
            if len(calls) == 1:
                try:
                    bridge.request("one-native-action")
                except BridgeError as error:
                    errors.append(error)
                    raise
            return query
        self.assertIs(await executor.query("not_sent", submit), False)
        self.assertEqual(calls, [1, 1])
        self.assertEqual(errors[0].details["delivery"], "not_sent")
        api.write_file.assert_not_called()
        api.read_file.assert_not_called()
        self.assertEqual(query.reads, 1)
        self.assertTrue(query._released)
        self.assertEqual(executor.pending_queries, [])
        self.assertFalse(executor._query_lock.locked())

    async def test_unknown_submission_beats_even_injected_busy_retry(self):
        from pdx_runtime.native_bridge import BridgeError
        executor, query, calls = self.executor(busy_submit_error=lambda error: True), Query(4), []
        original = BridgeError("original factory failure", 170, delivery="not_sent")
        original.query = query
        graph = executor.owner_pool.retain(object())
        def submit():
            calls.append(1)
            raise original
        self.assertEqual(await executor.query("unknown", submit), 4)
        self.assertEqual(calls, [1])
        self.assertIs(executor.diagnostics["operations"][0]["query"], query)
        self.assertIs(executor.diagnostics["operations"][0]["submission_error"], original)
        self.assertEqual(executor.owner_pool.snapshot(), (graph,))

    async def test_injected_transport_retry_and_diagnostics(self):
        class Transient(Exception):
            pass
        diagnostics = []
        executor = self.executor(diagnostics=diagnostics,
                                 retryable_read_error=lambda error: isinstance(error, Transient),
                                 busy_submit_error=lambda error: isinstance(error, Transient))
        query, submits = Query(5), []
        original_read = query.read
        reads = []
        def read():
            reads.append(1)
            if len(reads) == 1:
                raise Transient("read")
            return original_read()
        query.read = read
        def submit():
            submits.append(1)
            if len(submits) == 1:
                error = Transient("transport proved no submission")
                error.details = {"delivery": "not_sent"}
                raise error
            return query
        self.assertEqual(await executor.query("transport", submit), 5)
        self.assertEqual(len(reads), 2)
        self.assertEqual(len(submits), 2)
        self.assertIs(executor.diagnostics, diagnostics)
        self.assertEqual(diagnostics[0]["read_failures"], 1)

    async def test_cached_terminal_owner_failure_never_replays_completion(self):
        executor, ready, calls = self.executor(), asyncio.Event(), []
        query = Query(6)
        failure = OSError("release failed after cached completion")
        class Owner:
            def complete(self):
                calls.append(1)
                if not ready.is_set():
                    return None
                query._result = 6
                raise failure
        pending = await executor.perform("owner", lambda: executor.query("owner", lambda: query, owner=Owner()))
        ready.set()
        for _ in range(2):
            with self.assertRaises(OSError) as caught:
                await pending.resume(timeout=1)
            self.assertIs(caught.exception, failure)
            if _ == 0:
                completed_calls = len(calls)
        self.assertEqual(len(calls), completed_calls)
        self.assertEqual(executor.pending_queries, [query])
        self.assertFalse(executor._query_lock.locked())
        self.assertEqual(await executor.query("next", lambda: Query(7)), 7)
        self.assertEqual(len(calls), completed_calls)

    async def test_cached_false_completion_reconciles_without_native_read(self):
        executor, query = self.executor(), Query(False)
        await executor.submit(lambda: query)
        self.assertIs(query.read(), False)
        self.assertTrue(executor._query_lock.locked())
        self.assertEqual(await executor.query("next", lambda: Query(8)), 8)
        self.assertEqual(query.reads, 1)
        self.assertEqual(executor.pending_queries, [])

    async def test_rejected_callback_cleanup_retry_is_terminal_without_completion(self):
        from pdx_runtime.native_bridge import BridgeError
        from pdx_runtime.native_callback import (
            CallbackContext, CallbackDisposition, submit_callback,
        )
        calls = {"dispatch": 0, "complete": 0, "decode": 0, "free": 0}
        class Process:
            def resolve_export(self, adapter, symbol):
                return 100
            def allocate(self, size):
                return 200
            def write(self, address, initial_bytes):
                pass
            def free(self, address):
                calls["free"] += 1
                if calls["free"] == 1:
                    raise BridgeError("free rejected callback", 5)
        process, client, queries = Process(), object(), []
        def dispatch(callback, address):
            calls["dispatch"] += 1
            return {"rejected": True}
        def complete():
            calls["complete"] += 1
            raise AssertionError("a proved-rejected callback never completes")
        def decode(raw):
            calls["decode"] += 1
            raise AssertionError("a proved-rejected callback has no result")
        context = CallbackContext(
            client, dispatch, complete, lambda: process,
            lambda candidate: candidate is process,
            lambda raw: CallbackDisposition("rejected", error=170),
        )
        def submit():
            try:
                return submit_callback(client, b"initial", context=context,
                    adapter="generic", callback_symbol="callback", decode_result=decode,
                    process=process)
            except Exception as error:
                queries.append(error.query)
                raise
        executor = self.executor(
            retryable_read_error=lambda error: isinstance(error, BridgeError),
            busy_submit_error=lambda error: getattr(error, "winerror", None) == 170,
        )
        with self.assertRaises(BridgeError) as caught:
            await asyncio.wait_for(executor.query("reject", submit), 1)
        query, = queries
        self.assertIs(caught.exception, query.submission_error)
        self.assertEqual(caught.exception.winerror, 170)
        self.assertIsNone(query._result)
        self.assertTrue(query._released)
        self.assertEqual(calls, {"dispatch": 1, "complete": 0, "decode": 0, "free": 2})
        self.assertEqual(executor.pending_queries, [])
        self.assertFalse(executor._query_lock.locked())
        terminal = executor.diagnostics["operations"][-1]
        self.assertTrue(terminal["query_released"])
        self.assertNotIn("callback_completed", terminal)
        self.assertEqual(await executor.query("next", lambda: Query(0)), 0)
        self.assertEqual(calls["dispatch"], 1)

    async def test_unclassified_error_propagates_without_resubmit_or_release(self):
        executor, query, calls = self.executor(), Query(9), []
        def read():
            calls.append(1)
            raise ValueError("decode failure")
        query.read = read
        with self.assertRaisesRegex(ValueError, "decode failure"):
            await executor.query("bad", lambda: query)
        self.assertEqual(calls, [1])
        self.assertEqual(executor.pending_queries, [query])
        self.assertFalse(query._released)
        self.assertFalse(executor._query_lock.locked())


class AdapterAliasTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return GameContext(SimpleNamespace(native=object(), namespace={}), object(),
                           timeout=.001, poll_interval=.001)


    async def test_adapter_scope_and_pending_context_still_use_original_game_context(self):
        ctx, gate, observed = self.context(), asyncio.Event(), []
        async def work():
            observed.append(ACTIVE_CONTEXT.get())
            await gate.wait()
            observed.append(ACTIVE_CONTEXT.get())
            return await ctx.query("zero", lambda: Query(0))
        pending = await ctx.perform("scope", work)
        self.assertIs(pending.context, ctx)
        self.assertEqual(pending.task.get_name(), "v3-game:scope")
        self.assertIs(ACTIVE_CONTEXT.get(), None)
        self.assertIs(ctx._operations, ctx._executor._operations)
        ctx.timeout = 0
        self.assertIs(await pending, pending)
        gate.set()
        self.assertEqual(await pending.resume(timeout=1), 0)
        self.assertEqual(observed, [ctx, ctx])
        self.assertIs(ACTIVE_CONTEXT.get(), None)


if __name__ == "__main__":
    unittest.main()
