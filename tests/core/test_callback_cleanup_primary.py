"""CPU primary-result/cleanup ownership tests using real shared/V3 callback code."""
import asyncio
import gc
import struct
import threading
import unittest
import weakref
from types import SimpleNamespace as N
from unittest.mock import Mock, patch

from pdx_runtime import native_call, native_callback, v3, v3_access
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.query_executor import RetainedOwners, SerializedQueryExecutor
from pdx_runtime.session import RuntimeSession
from pdx_runtime.v3_game._core import GameContext
from tests.native_sdk.test_v3_query import bind_fixture_wire


class OwnerPoolTests(unittest.TestCase):
    def test_identity_store_accepts_equal_unhashable_owners_and_has_no_cleanup_policy(self):
        pool = RetainedOwners()
        first, second = {"value": 1}, {"value": 1}
        self.assertIs(pool.retain(first), first)
        pool.retain(first)
        pool.retain(second)
        self.assertEqual(len(pool.snapshot()), 2)
        self.assertIs(pool.snapshot()[0], first)
        self.assertIs(pool.snapshot()[1], second)
        pool.discard(first)
        self.assertEqual(len(pool.snapshot()), 1)
        self.assertIs(pool.snapshot()[0], second)
        pool.discard(first)
        self.assertEqual(len(pool.snapshot()), 1)

    def test_owner_is_strongly_held_until_explicit_discard(self):
        class Owner:
            pass
        pool, owner = RetainedOwners(), Owner()
        ref = weakref.ref(owner)
        pool.retain(owner)
        del owner
        gc.collect()
        self.assertIsNotNone(ref())
        pool.discard(ref())
        gc.collect()
        self.assertIsNone(ref())



class CleanupPrimaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()

    def access(self, *, owned=False, scope_error=0, call_error=0):
        client = Mock(pid=123)
        bind_fixture_wire(client)
        client.queue_v3_interface_callback.return_value = {"status": "ok", "error": 0, "request_id": 7}
        client.v3_world_status.return_value = {"status": "ok", "request_id": 7,
            "completed_request_id": 7, "world_error": 574}
        process = Mock(pid=123)
        exports = {"PdxNativeCallInterface": 0x10000, "PdxV3AccessCallback": 0x11000}
        process.resolve_export.side_effect = lambda module, symbol, **kwargs: exports[symbol]
        process.allocate.return_value = 0x20000
        inner = native_call.REQUEST.pack(56, call_error, 0xABCDEF, 3, 0x20090, 0, bytes(16)) + struct.pack("<3Q", 11, 13, 17)
        outer = v3_access.REQUEST.pack(88, scope_error, 1, 0, 0x10000, 0x20058,
            0x20058 + len(inner), 0, 0, 0xABC, 0xCDE, 1234, 1, 1, 0)
        blob = outer + inner + b"host\0"
        process.read.return_value = blob
        self.enterContext(patch("pdx_runtime.v3_access.RemoteProcess", return_value=process))
        queries = []
        def factory():
            try:
                query = v3_access.submit_call(client, 0xABCDEF, (11, 13, 17),
                    mode=1, flag=0, dispatch="interface", source="host",
                    process=None if owned else process)
            except Exception as error:
                if hasattr(error, "query"):
                    queries.append(error.query)
                raise
            queries.append(query)
            return query
        session = RuntimeSession(client)
        ctx = GameContext(session, process, timeout=.01, poll_interval=.001)
        return ctx, client, process, queries, factory, blob

    def assert_one_callback_copy(self, client, process):
        client.queue_v3_interface_callback.assert_called_once()
        client.v3_world_status.assert_called_once()
        process.read.assert_called_once()

    async def test_access_primary_returns_while_failed_cleanup_query_remains_owned(self):
        ctx, client, process, queries, factory, _ = self.access()
        failure = OSError("first free failed")
        process.free.side_effect = [failure, None]
        result = await ctx.query("access", factory)
        query = queries[0]
        self.assertIs(ctx.owner_pool, ctx.session.owner_pool)
        self.assertIs(ctx._executor.owner_pool, ctx.session.owner_pool)
        self.assertIsInstance(result, v3_access.AccessResult)
        self.assertIs(result, query._result)
        self.assertEqual(result.callee_result.rax, 0)
        self.assertIs(query.cleanup_error, failure)
        self.assertFalse(query._released)
        self.assertIs(query.process, process)
        self.assertIn(query, ctx.pending_queries)
        self.assertIn(query, ctx._executor.owner_pool.snapshot())
        self.assertFalse(ctx._query_lock.locked())
        process.close.assert_not_called()
        # A generic finished Query has no native release attributes. It must not
        # be pinned forever, or blocked behind an unrelated cleanup warning.
        simple = N(read=lambda: 4)
        self.assertEqual(await ctx.query("independent", lambda: simple), 4)
        self.assertNotIn(simple, ctx.pending_queries)
        self.assertNotIn(simple, ctx._executor.owner_pool.snapshot())
        self.assertIs(await ctx.wait(query), result)
        self.assertTrue(query._released)
        self.assertIsNone(query.cleanup_error)
        self.assertEqual(ctx.pending_queries, [])
        self.assertNotIn(query, ctx._executor.owner_pool.snapshot())
        self.assertEqual(process.free.call_count, 2)
        self.assert_one_callback_copy(client, process)

    async def test_raw_close_is_retired_by_pending_index_maintenance_without_replay(self):
        ctx, client, process, queries, factory, _ = self.access()
        process.free.side_effect = [OSError("first free failed"), None]
        result = await ctx.query("access", factory)
        query = queries[0]
        query.close()
        self.assertTrue(query._released)
        before = (process.free.call_count, process.read.call_count, client.v3_world_status.call_count)
        self.assertEqual(ctx.pending_queries, [])
        self.assertNotIn(query, ctx.owner_pool.snapshot())
        self.assertIs(query._result, result)
        self.assertEqual((process.free.call_count, process.read.call_count,
                          client.v3_world_status.call_count), before)
        client.queue_v3_interface_callback.assert_called_once()


    async def test_owned_process_close_failure_preserves_primary_and_retries_close_only(self):
        ctx, client, process, queries, factory, _ = self.access(owned=True)
        failure = OSError("owned process close failed")
        process.close.side_effect = [failure, None]
        result = await ctx.query("access", factory)
        query = queries[0]
        self.assertIs(query._result, result)
        self.assertTrue(query._owns_process)
        self.assertTrue(query._freed)
        self.assertFalse(query._released)
        self.assertIs(query.cleanup_error, failure)
        self.assertIn(query, ctx._executor.owner_pool.snapshot())
        self.assertIs(await ctx.wait(query), result)
        process.free.assert_called_once()
        self.assertEqual(process.close.call_count, 2)
        self.assertTrue(query._released)
        self.assertIsNone(query.cleanup_error)
        self.assert_one_callback_copy(client, process)

    async def test_native_scope_and_callee_errors_are_primary_not_cleanup_errors(self):
        ctx, client, process, queries, factory, _ = self.access(scope_error=13, call_error=87)
        failure = OSError("free failed")
        process.free.side_effect = [failure, None]
        result = await ctx.query("native-errors", factory)
        self.assertEqual((result.error, result.callee_result.error, result.callee_result.rax), (13, 87, 0))
        self.assertIs(queries[0].cleanup_error, failure)
        self.assertIs(await ctx.wait(queries[0]), result)
        self.assert_one_callback_copy(client, process)

    async def test_execution_error_keeps_original_identity_when_cleanup_also_fails(self):
        ctx, client, process, queries, factory, _ = self.access(scope_error=997)
        failure = OSError("free failed")
        process.free.side_effect = [failure, None]
        with self.assertRaises(v3.QueryExecutionError) as caught:
            await ctx.query("unwritten", factory)
        query = queries[0]
        self.assertIs(caught.exception, query._execution_error)
        self.assertIs(caught.exception.result, query._result)
        self.assertEqual(caught.exception.winerror, 574)
        self.assertIs(query.cleanup_error, failure)
        self.assertIn(query, ctx.pending_queries)
        with self.assertRaises(v3.QueryExecutionError) as resumed:
            await ctx.wait(query)
        self.assertIs(resumed.exception, caught.exception)
        self.assertTrue(query._released)
        self.assertEqual(ctx.pending_queries, [])
        self.assert_one_callback_copy(client, process)

    async def test_undecoded_payload_error_retains_storage_and_never_invents_primary(self):
        ctx, client, process, queries, factory, full = self.access()
        process.read.return_value = full[:90]
        with self.assertRaises(struct.error):
            await ctx.query("short", factory)
        query = queries[0]
        self.assertIsNone(query._result)
        self.assertIsNone(query.cleanup_error)
        self.assertFalse(query._released)
        process.free.assert_not_called()
        self.assertIn(query, ctx.pending_queries)
        process.read.return_value = full
        result = await ctx.wait(query)
        self.assertIsInstance(result, v3_access.AccessResult)
        self.assertEqual(result.callee_result.rax, 0)
        client.queue_v3_interface_callback.assert_called_once()
        self.assertEqual(process.read.call_count, 2)
        process.free.assert_called_once()

    async def test_rejected_primary_is_not_replaced_by_repeated_cleanup_failure(self):
        ctx, client, process, queries, factory, _ = self.access(owned=True)
        client.queue_v3_interface_callback.return_value = {"status": "error", "error": 5}
        failure = OSError("rejected allocation free failed")
        process.free.side_effect = failure
        with self.assertRaises(BridgeError) as caught:
            await ctx.query("rejected", factory)
        query = queries[0]
        self.assertIs(caught.exception, query.submission_error)
        self.assertIs(query.cleanup_error, failure)
        self.assertIsNone(query._result)
        self.assertIn(query, ctx.pending_queries)
        self.assertFalse(ctx._query_lock.locked())
        process.read.assert_not_called()
        process.close.assert_not_called()
        client.v3_world_status.assert_not_called()
        process.free.side_effect = None
        with self.assertRaises(BridgeError) as resumed:
            await ctx.wait(query)
        self.assertIs(resumed.exception, caught.exception)
        self.assertTrue(query._released)
        self.assertEqual(ctx.pending_queries, [])
        process.close.assert_called_once()
        client.queue_v3_interface_callback.assert_called_once()

    async def test_session_interface_rejection_keeps_primary_before_session_request_exists(self):
        from tests.core import test_v3_session as fixtures
        fixture = fixtures.SessionTests()
        fixture.setUp()
        fixture.client.queue_v3_interface_callback.return_value = {"status": "error", "error": 5}
        failure = OSError("interface rejection cleanup failed")
        ctx = GameContext(RuntimeSession(fixture.client), fixture.process)
        with patch.object(fixture.process, "free", side_effect=failure):
            with self.assertRaises(BridgeError) as caught:
                await ctx.query("interface.reject", fixture.submit)
        query = caught.exception.query
        self.assertIs(caught.exception, query.submission_error)
        self.assertEqual(caught.exception.operation, "V3 query submit")
        self.assertEqual(caught.exception.winerror, 5)
        self.assertIsNone(query.request_id)
        self.assertIsNone(query.session_request_id)
        self.assertIsNone(query._enqueue_error)
        self.assertIsNone(query._result)
        self.assertIs(query.cleanup_error, failure)
        self.assertIn(query, ctx.pending_queries)
        self.assertFalse(ctx._query_lock.locked())
        fixture.client.v3_world_status.assert_not_called()
        with self.assertRaises(BridgeError) as resumed:
            await ctx.wait(query)
        self.assertIs(resumed.exception, caught.exception)
        self.assertEqual(fixture.process.freed, [query.result_address])
        self.assertEqual(ctx.pending_queries, [])
        fixture.client.queue_v3_interface_callback.assert_called_once()
        fixture.client.v3_world_status.assert_not_called()

    async def test_session_known_rejection_is_terminal_while_original_cleanup_is_retained(self):
        from tests.core import test_v3_session as fixtures
        fixture = fixtures.SessionTests()
        fixture.setUp()
        query = fixture.submit()
        fixture.enqueue_done(170)
        query._owns_process = True
        original_free = fixture.process.free
        failure = OSError("session enqueue rejection cleanup failed")
        ctx = GameContext(RuntimeSession(fixture.client), fixture.process)
        with patch.object(fixture.process, "free", side_effect=failure):
            with self.assertRaises(BridgeError) as caught:
                await ctx.query("session.reject", lambda: query)
        self.assertIs(caught.exception, query._enqueue_error)
        self.assertIs(caught.exception, query.submission_error)
        self.assertIs(caught.exception.query, query)
        self.assertEqual(caught.exception.winerror, 170)
        self.assertIs(query.cleanup_error, failure)
        self.assertIsNone(query._result)
        self.assertIsNone(query.session_request_id)
        self.assertIn(query, ctx.pending_queries)
        self.assertIn(query, ctx.owner_pool.snapshot())
        self.assertFalse(ctx._query_lock.locked())
        before = fixture.client.v3_world_status.call_count
        with self.assertRaises(BridgeError) as resumed:
            await ctx.wait(query)
        self.assertIs(resumed.exception, caught.exception)
        self.assertEqual(fixture.client.v3_world_status.call_count, before)
        self.assertEqual(fixture.process.freed, [query.result_address])
        self.assertTrue(fixture.process.closed)
        self.assertEqual(ctx.pending_queries, [])
        self.assertNotIn(query, ctx.owner_pool.snapshot())
        fixture.client.queue_v3_interface_callback.assert_called_once()

    async def test_partial_page_free_keeps_primary_and_does_not_free_prefix_twice(self):
        from tests.native_sdk import test_v3_data_page as fixtures
        from pdx_runtime import v3_data as data
        fixture = fixtures.PageTests()
        fixture.setUp()
        fixture.submit()
        fixture.result([(0, 0, 0, data.Kind.U64, 0, bytes(8), None)])
        query = fixture.query
        failure = OSError("request allocation free failed after copy free")
        fixture.process.free.side_effect = [None, failure, None]
        ctx = GameContext(RuntimeSession(fixture.client), fixture.process)
        result = await ctx.query("page", lambda: query)
        self.assertEqual(result.cells[0].value, 0)
        self.assertTrue(query._copied_freed)
        self.assertFalse(query._freed)
        self.assertIs(query.cleanup_error, failure)
        self.assertIn(query, ctx.pending_queries)
        self.assertIn(query, ctx._executor.owner_pool.snapshot())
        reads = fixture.process.read.call_count
        self.assertIs(await ctx.wait(query), result)
        self.assertEqual(fixture.process.read.call_count, reads)
        self.assertEqual([call.args[0] for call in fixture.process.free.call_args_list],
                         [0x3000, 0x2000, 0x2000])
        self.assertIsNone(query.cleanup_error)
        self.assertEqual(ctx.pending_queries, [])


    async def test_public_cancellation_keeps_decoded_primary_and_inflight_cleanup_once(self):
        ctx, client, process, queries, factory, _ = self.access(owned=True)
        ctx.timeout = 10
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        self.gates.append(release)
        failure = OSError("first free failed after original caller cancelled")
        attempts = []
        def free(address):
            attempts.append(address)
            if len(attempts) == 1:
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(2):
                    raise AssertionError("fake cleanup was not released")
                raise failure
        process.free.side_effect = free
        caller = asyncio.create_task(ctx.perform("access", lambda: ctx.query("access", factory)))
        await asyncio.wait_for(entered.wait(), 1)
        original_task, = ctx._operations
        query = queries[0]
        self.assertIsInstance(query._result, v3_access.AccessResult)
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertFalse(original_task.done())
        self.assertFalse(query._released)
        process.close.assert_not_called()
        self.assertIn(query, ctx._executor.owner_pool.snapshot())
        release.set()
        result = await asyncio.wait_for(original_task, 1)
        self.assertIs(result, query._result)
        self.assertIs(query.cleanup_error, failure)
        self.assertEqual(len(attempts), 1)
        self.assertIn(query, ctx.pending_queries)
        self.assertIs(await ctx.wait(query), result)
        self.assertEqual(len(attempts), 2)
        process.close.assert_called_once()
        self.assert_one_callback_copy(client, process)

    async def test_raw_native_call_uses_same_shared_primary_cleanup_policy(self):
        client, process = object(), Mock()
        process.resolve_export.return_value = 0x1000
        process.allocate.return_value = 0x2000
        process.read.return_value = native_call.REQUEST.pack(56, 0, 0xABCD, 0, 0, 0, bytes(16))
        dispatch = Mock(return_value={"status": "ok", "request_id": 3})
        status = Mock(return_value={"status": "ok", "completed_request_id": 3})
        context = native_callback.CallbackContext(client, dispatch, status, lambda: process,
            lambda candidate: candidate is process,
            lambda response: native_callback.CallbackDisposition("accepted", request_id=response["request_id"]))
        executor = SerializedQueryExecutor()
        failure = OSError("raw call free failed")
        process.free.side_effect = [failure, None]
        queries = []
        def factory():
            query = native_call.submit(client, 0xABCD, context=context)
            queries.append(query)
            return query
        result = await executor.query("raw", factory)
        query = queries[0]
        self.assertIsInstance(result, native_call.NativeCallResult)
        self.assertEqual(result.rax, 0)
        self.assertIs(query.cleanup_error, failure)
        self.assertTrue(query._owns_process)
        self.assertIn(query, executor.pending_queries)
        self.assertIs(await executor.wait(query), result)
        process.read.assert_called_once()
        process.close.assert_called_once()
        dispatch.assert_called_once()
        status.assert_called_once()
        self.assertEqual(executor.owner_pool.snapshot(), ())


if __name__ == "__main__":
    unittest.main()
