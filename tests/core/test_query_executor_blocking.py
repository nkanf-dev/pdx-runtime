"""CPU-only blocking-I/O scheduling and ownership regression tests.

Real executor, GameContext and NativeBridge; fake Win32 API only. No process,
game, pipe connection, native binary or controller is opened.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pdx_runtime.native_bridge import NativeBridge
from pdx_runtime.query_executor import PendingOperation, SerializedQueryExecutor
from pdx_runtime.v3_game._core import GameContext
from pdx_runtime.v3_game._run import ACTIVE_CONTEXT


class Block:
    def __init__(self, loop):
        self.loop = loop
        self.entered = asyncio.Event()
        self.release = threading.Event()
        self.worker_threads = []

    def wait(self):
        self.worker_threads.append(threading.get_ident())
        self.loop.call_soon_threadsafe(self.entered.set)
        if not self.release.wait(2):
            raise AssertionError("fake I/O was not released by the test")


class PipeAPI:
    """The real bridge lock must pair each fake write with its matching read."""
    def __init__(self, blocked_command, block):
        self.blocked_command, self.block = blocked_command, block
        self.current = None
        self.commands = []
        self.events = []
        self.threads = []

    def write_file(self, handle, data, size, count, overlapped):
        if self.current is not None:
            raise AssertionError("overlapping untagged pipe exchanges")
        self.current = data.decode("ascii")
        self.commands.append(self.current)
        self.events.append(("write", self.current))
        self.threads.append(threading.get_ident())
        count._obj.value = size
        return True

    def read_file(self, handle, buffer, size, count, overlapped):
        command = self.current
        if command == self.blocked_command:
            self.block.wait()
        raw = json.dumps({"status": "ok", "value": command, "world": "0x100",
            "interface_thread_id": 9, "main_module_base": "0x200"}).encode()
        ctypes.memmove(buffer, raw, len(raw))
        count._obj.value = len(raw)
        self.events.append(("read", command))
        self.current = None
        return True


class Query:
    def __init__(self, result, reader=None):
        self.result, self.reader = result, reader
        self._result, self._released = None, False
        self.reads = 0

    def read(self):
        self.reads += 1
        if self.reader is not None:
            self.reader()
        self._result, self._released = self.result, True
        return self.result


class BlockingExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.blocks = []
        self.loop_thread = threading.get_ident()
        self.futures = []
        loop = asyncio.get_running_loop()
        original = loop.run_in_executor
        def record(*args, **kwargs):
            future = original(*args, **kwargs)
            self.futures.append(future)
            return future
        self.enterContext(patch.object(loop, "run_in_executor", side_effect=record))

    def active_calls(self):
        return tuple(future for future in self.futures if not future.done())

    def tearDown(self):
        for block in self.blocks:
            block.release.set()

    def block(self):
        block = Block(asyncio.get_running_loop())
        self.blocks.append(block)
        return block

    def bridge(self, command, block):
        api = PipeAPI(command, block)
        bridge = NativeBridge(123, api=api)
        bridge.handle = object()  # Not an OS handle.
        return bridge, api

    def executor(self):
        return SerializedQueryExecutor(timeout=.01, poll_interval=.001)

    async def heartbeat(self):
        beat = asyncio.Event()
        asyncio.get_running_loop().call_soon(beat.set)
        await asyncio.wait_for(beat.wait(), .5)

    async def probe_exchange(self, blocked_command, *, cancel_owned=False):
        executor, block = self.executor(), self.block()
        bridge, api = self.bridge(blocked_command, block)
        value, calls = object(), []
        query = Query(value, lambda: bridge.request("complete"))
        def factory():
            calls.append("first")
            bridge.request("enqueue")
            return query
        pending = await executor.perform("first", lambda: executor.query("first", factory))
        await asyncio.wait_for(block.entered.wait(), .5)
        self.assertIsInstance(pending, PendingOperation)
        original_task = pending.task
        original_calls = self.active_calls()
        self.assertEqual(len(original_calls), 1)
        self.assertIs(await pending.resume(timeout=0), pending)
        await self.heartbeat()
        self.assertFalse(block.release.is_set())
        self.assertFalse(pending.done)
        self.assertFalse(query._released)
        self.assertTrue(executor._query_lock.locked())
        if blocked_command == "enqueue":
            self.assertEqual(executor.pending_queries, [])
        else:
            self.assertEqual(executor.pending_queries, [query])
        def next_factory():
            calls.append("second")
            bridge.request("next-enqueue")
            return Query(2, lambda: bridge.request("next-complete"))
        next_pending = await executor.perform("next", lambda: executor.query("next", next_factory))
        self.assertIsInstance(next_pending, PendingOperation)
        self.assertEqual(calls, ["first"])
        if cancel_owned:
            original_task.cancel()
            await self.heartbeat()
            self.assertFalse(original_task.done())
            self.assertEqual(self.active_calls(), original_calls)
            self.assertTrue(executor._query_lock.locked())
        block.release.set()
        self.assertIs(await pending.resume(timeout=1), value)
        self.assertIs(pending.task, original_task)
        self.assertEqual(await next_pending.resume(timeout=1), 2)
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(query.reads, 1)
        self.assertEqual(api.commands, ["enqueue", "complete", "next-enqueue", "next-complete"])
        self.assertEqual(api.events, [(stage, command) for command in api.commands for stage in ("write", "read")])
        self.assertTrue(all(thread != self.loop_thread for thread in api.threads))
        self.assertEqual(executor.pending_queries, [])
        self.assertFalse(self.active_calls())
        self.assertFalse(executor._query_lock.locked())

    async def test_blocked_factory_deadline_keeps_future_before_query_exists(self):
        await self.probe_exchange("enqueue")

    async def test_blocked_reader_deadline_keeps_exact_query_and_storage(self):
        await self.probe_exchange("complete")

    async def test_direct_owned_task_cancel_cannot_abandon_factory(self):
        await self.probe_exchange("enqueue", cancel_owned=True)

    async def test_direct_owned_task_cancel_cannot_abandon_reader(self):
        await self.probe_exchange("complete", cancel_owned=True)

    async def test_cancel_public_caller_is_prompt_but_original_factory_finishes(self):
        executor, block = self.executor(), self.block()
        executor.timeout = 10
        value, calls = object(), []
        query = Query(value)
        def factory():
            calls.append(1)
            block.wait()
            return query
        caller = asyncio.create_task(executor.perform("cancel-caller", lambda: executor.query("read", factory)))
        await asyncio.wait_for(block.entered.wait(), .5)
        original_task, = executor._operations
        future, = self.active_calls()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertFalse(original_task.done())
        self.assertFalse(future.cancelled())
        self.assertTrue(executor._query_lock.locked())
        await self.heartbeat()
        block.release.set()
        self.assertIs(await asyncio.wait_for(original_task, 1), value)
        self.assertEqual(calls, [1])
        self.assertEqual(query.reads, 1)

    async def test_existing_bridge_lock_contention_does_not_block_loop(self):
        executor, block = self.executor(), self.block()
        bridge, api = self.bridge("outside", block)
        external = asyncio.create_task(asyncio.to_thread(bridge.request, "outside"))
        await asyncio.wait_for(block.entered.wait(), .5)
        query = Query(7)
        calls = []
        def factory():
            calls.append(1)
            bridge.request("owned")
            return query
        pending = await executor.perform("lock", lambda: executor.query("lock", factory))
        self.assertIsInstance(pending, PendingOperation)
        self.assertEqual(api.commands, ["outside"])
        self.assertTrue(bridge._lock.locked())
        await self.heartbeat()
        self.assertFalse(pending.done)
        block.release.set()
        await asyncio.wait_for(external, 1)
        self.assertEqual(await pending.resume(timeout=1), 7)
        self.assertEqual(api.commands, ["outside", "owned"])
        self.assertEqual(calls, [1])
        self.assertEqual(query.reads, 1)

    async def test_ambiguous_factory_reply_is_owned_once_after_cancel(self):
        executor, block = self.executor(), self.block()
        query, calls = Query(object()), []
        def factory():
            calls.append(1)
            block.wait()
            error = OSError("reply lost after dispatch")
            error.query = query
            raise error
        pending = await executor.perform("unknown", lambda: executor.query("unknown", factory))
        await asyncio.wait_for(block.entered.wait(), .5)
        self.assertIsInstance(pending, PendingOperation)
        pending.task.cancel()
        await self.heartbeat()
        self.assertTrue(executor._query_lock.locked())
        block.release.set()
        self.assertIs(await pending.resume(timeout=1), query.result)
        self.assertEqual(calls, [1])
        self.assertEqual(query.reads, 1)
        self.assertIs(executor.diagnostics["operations"][0]["query"], query)

    async def test_original_completion_is_shared_and_not_reconciled_while_running(self):
        executor, block = self.executor(), self.block()
        query, calls, factories = Query(object()), [], []
        class Owner:
            def complete(self):
                calls.append(1)
                # Simulate query.read copying/freeing, then owner bookkeeping
                # still running. The slot cannot be externally reconciled yet.
                query._result, query._released = query.result, True
                block.wait()
                return query.result
        owner = Owner()
        pending = await executor.perform("owner", lambda: executor.query("owner", lambda: query, owner=owner))
        await asyncio.wait_for(block.entered.wait(), .5)
        second_observer = asyncio.create_task(executor.wait(query, owner=owner))
        next_pending = await executor.perform("next", lambda: executor.query("next",
            lambda: (factories.append(1), Query(8))[1]))
        self.assertIsInstance(next_pending, PendingOperation)
        self.assertEqual(factories, [])
        self.assertEqual(calls, [1])
        self.assertIs(executor._locked_query, query)
        with self.assertRaises(ValueError):
            await executor.wait(query, owner=object())
        block.release.set()
        self.assertIs(await pending.resume(timeout=1), query.result)
        self.assertIs(await second_observer, query.result)
        self.assertEqual(await next_pending.resume(timeout=1), 8)
        self.assertEqual(calls, [1])
        self.assertEqual(factories, [1])

    async def test_pool_is_reused_context_propagates_and_independent_io_can_run(self):
        executor, block = self.executor(), self.block()
        seen = []
        token = ACTIVE_CONTEXT.set("original-context")
        try:
            first = asyncio.create_task(executor.blocking(block.wait))
            await asyncio.wait_for(block.entered.wait(), .5)
            pool = asyncio.get_running_loop()._default_executor
            def inspect_context():
                seen.append((ACTIVE_CONTEXT.get(), threading.get_ident()))
                return 4
            self.assertEqual(await asyncio.wait_for(executor.blocking(inspect_context), .5), 4)
            self.assertFalse(block.release.is_set())
            for _ in range(4):
                self.assertEqual(await executor.blocking(inspect_context), 4)
                self.assertIs(asyncio.get_running_loop()._default_executor, pool)
            block.release.set()
            await first
        finally:
            ACTIVE_CONTEXT.reset(token)
        self.assertTrue(all(context == "original-context" and thread != self.loop_thread for context, thread in seen))
        self.assertLess(len({thread for _, thread in seen}), len(seen))
        self.assertFalse(self.active_calls())

    async def test_core_direct_status_boundaries_are_off_loop(self):
        for command in ("world-status", "status"):
            with self.subTest(command=command):
                block = self.block()
                bridge, api = self.bridge(command, block)
                ctx = GameContext(SimpleNamespace(native=bridge, namespace={}), object(),
                    timeout=.01, poll_interval=.001)
                ctx.world = 0x100
                observed = SimpleNamespace(error=0, world=ctx.world)
                with patch("pdx_runtime.v3_game._core.lifecycle.observe", return_value=Query(observed)):
                    pending = await ctx.perform("refresh", ctx.refresh)
                    await asyncio.wait_for(block.entered.wait(), .5)
                    self.assertIsInstance(pending, PendingOperation)
                    await self.heartbeat()
                    self.assertFalse(pending.done)
                    block.release.set()
                    result = await pending.resume(timeout=1)
                self.assertIs(result, observed)
                self.assertEqual(ctx.image_base, 0x200)
                self.assertEqual(ctx.interface_thread_id, 9)
                self.assertTrue(all(thread != self.loop_thread for thread in api.threads))



if __name__ == "__main__":
    unittest.main()
