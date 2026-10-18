"""Behavior of the original VFS rename entry; no files or native calls run."""
from __future__ import annotations

from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pdx_runtime import v3_lifecycle as life
from pdx_runtime.v3_game.storage import StorageAPI


class Context:
    def __init__(self):
        self.native = object()
        self.process = object()
        self.adapters = {"lifecycle": 0xABCD}
        self.queries = []
        self.performs = []

    async def query(self, label, factory):
        self.queries.append(label)
        query = factory()
        if isinstance(query.result, BaseException):
            raise query.result
        return query.result

    async def perform(self, label, operation):
        self.performs.append(label)
        return await operation()


def rename_result(*, error=0, success=0, tag=17):
    return life.SaveManagementResult(64, error, life.SaveManagementAction.RENAME_NATIVE,
        0, 0, 0, 0, 0, 0, success, tag, 77, 0x1000, 0x2000, b"")


class StorageRenameTests(IsolatedAsyncioTestCase):
    async def test_real_wire_and_decoder_preserve_paths_raw_message_and_success_with_copy_error(self):
        ctx = Context()
        path = "native:/save games/../literal/原.v3"
        new_name = "新'é.v3"
        observed = {}
        def submit(client, initial, **options):
            header = list(life.SAVE_MANAGEMENT_HEADER.unpack_from(initial))
            self.assertEqual((header[2], header[3], header[6]),
                (life.SaveManagementAction.RENAME_NATIVE, 0, 1))
            start = life.SAVE_MANAGEMENT_HEADER.size
            self.assertEqual(initial[start:start + header[4]], path.encode())
            self.assertEqual(initial[start + header[4]:start + header[4] + header[5]], new_name.encode())
            self.assertEqual(options["callback_symbol"], "PdxV3SaveManagementInterface")
            header[1], header[7], header[8], header[9], header[10] = 234, 1, 20, 1, 19
            packet = life.SAVE_MANAGEMENT_HEADER.pack(*header)
            packet += path.encode() + new_name.encode() + b"\xff"
            observed["result"] = options["decode_result"](packet)
            return N(result=observed["result"])
        with patch.object(life, "submit_callback", side_effect=submit) as submit_callback:
            value = await StorageAPI(ctx).rename(path, new_name, message_capacity=1)
        self.assertEqual((value.source_path, value.new_name), (path, new_name))
        self.assertIs(value.result, observed["result"])
        self.assertEqual((value.result.error, value.result.native_success, value.result.native_result_tag),
            (234, 1, 19))
        self.assertEqual(value.result.message_raw, b"\xff")
        self.assertEqual(value.result.message_required, 20)
        self.assertEqual(ctx.queries, ["storage.rename"])
        submit_callback.assert_called_once()

    async def test_native_refusal_with_callback_error_zero_is_not_success_or_exception(self):
        ctx = Context()
        original = rename_result(error=0, success=0, tag=23)
        with patch.object(life, "rename_save", return_value=N(result=original)) as rename:
            value = await StorageAPI(ctx).rename("save games/one.v3", "two.v3")
        self.assertIs(value.result, original)
        self.assertEqual((value.result.error, value.result.native_success, value.result.native_result_tag),
            (0, 0, 23))
        rename.assert_called_once()

    async def test_invalid_leaf_or_text_fails_before_query_or_mutation(self):
        ctx = Context()
        with patch.object(life, "rename_save") as rename:
            for path, name, error in (("", "new.v3", ValueError),
                    ("old.v3", "../new.v3", ValueError),
                    ("old.v3", "new/leaf.v3", ValueError),
                    ("old.v3", "new\\leaf.v3", ValueError),
                    ("old.v3", "..", ValueError),
                    ("old.v3", "bad\0name", ValueError),
                    ("old.v3", "bad\ud800", UnicodeEncodeError),
                    ("old.v3", None, TypeError)):
                with self.subTest(path=path, name=name), self.assertRaises(error):
                    await StorageAPI(ctx).rename(path, name)
        self.assertEqual(ctx.queries, [])
        self.assertEqual(ctx.performs, [])
        rename.assert_not_called()

    async def test_unknown_completion_keeps_original_error_query_and_request_without_replay(self):
        ctx = Context()
        failure = RuntimeError("rename completion unknown")
        query = N(result=failure)
        failure.query = query
        with patch.object(life, "rename_save", return_value=query) as rename:
            with self.assertRaises(RuntimeError) as caught:
                await StorageAPI(ctx).rename("save games/one.v3", "two.v3")
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.query, query)
        self.assertEqual((failure.save_rename_source, failure.save_rename_new_name),
            ("save games/one.v3", "two.v3"))
        rename.assert_called_once()

    async def test_pending_resumes_the_original_rename_query_at_most_once(self):
        import asyncio
        from pdx_runtime.query_executor import PendingOperation
        from pdx_runtime.v3_game._core import GameContext
        loop = asyncio.get_running_loop()
        submitted = asyncio.Event()
        original = rename_result(success=1)
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
        with patch.object(life, "rename_save", side_effect=submit) as rename:
            pending = await StorageAPI(ctx).rename("save games/one.v3", "two.v3")
            self.assertIsInstance(pending, PendingOperation)
            await asyncio.wait_for(submitted.wait(), 1)
            self.assertIs(await pending.resume(timeout=0), pending)
            self.assertIn(query, pending.pending_queries)
            query.ready = True
            value = await pending.resume(timeout=1)
        self.assertIs(value.result, original)
        self.assertEqual(ctx.owner_pool.snapshot(), ())
        rename.assert_called_once()
