"""Shared copied-page values and retained queries on owned CPU fixtures."""
import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data
from pdx_runtime.query_executor import PendingOperation, SerializedQueryExecutor
from pdx_runtime.v3_game import _projection as projection


def page(*, error=0, cells=None, **options):
    if cells is None:
        cells = (data.ProjectionCell(0, 0, 0, data.Kind.U32, 0, 0, b"\0" * 4, 4),)
    grouped = options.get("grouped", False)
    return data.ProjectionPage(error, 77, 1, 0, len(cells), 0 if grouped else len(cells),
                               not grouped, len(cells), cells, **options)


class Context:
    def __init__(self):
        self.native, self.process = object(), NS(resolve_export=lambda *a, **k: 1234)
        self.adapters, self.diagnostics = {"data": 7}, []
    async def blocking(self, function, *args, **kwargs):
        return function(*args, **kwargs)
    async def query(self, label, factory):
        return factory().result


class ProjectionPageTests(unittest.IsolatedAsyncioTestCase):
    def test_native_zero_false_and_partial_text_keep_their_errors(self):
        zero = data.ProjectionCell(0, 0, 13, data.Kind.U32, 0, 0, b"\0" * 4, 4)
        false = data.ProjectionCell(0, 1, 5, data.Kind.BOOL, 0, 0, b"\0", 1)
        text = data.ProjectionCell(0, 2, 299, data.Kind.STRING, 0, 0, b"prefix", 100)
        value, reason = projection._projection_value(zero)
        self.assertEqual(value, 0)
        self.assertIn("13", reason)
        value, reason = projection._projection_value(false)
        self.assertIs(value, False)
        self.assertIn("5", reason)
        value, reason = projection._projection_value(text)
        self.assertEqual(value, "prefix")
        self.assertIn("299", reason)
        self.assertIn("6/100", reason)
        self.assertEqual(text.snapshot, b"prefix")

    async def test_pending_copy_resumes_the_same_query_without_resubmission(self):
        ctx = Context()
        executor = SerializedQueryExecutor(timeout=.001, poll_interval=.001)
        ctx.query, ctx._remember, ctx.diagnostics = executor.query, executor._remember, executor.diagnostics
        result = page()
        class CopyQuery:
            ready = False
            _released = False
            partial_result = result
            def read(self):
                if not self.ready:
                    return None
                self._released = True
                return result
        original, submitted = CopyQuery(), []
        created, loop = asyncio.Event(), asyncio.get_running_loop()
        def submit(*args, **kwargs):
            submitted.append(original)
            loop.call_soon_threadsafe(created.set)
            return original
        with patch.object(data, "project_page", submit):
            pending = await executor.perform("projection", lambda: projection._projection_page(
                ctx, "page", ("Row.Value",), ((),)))
            await asyncio.wait_for(created.wait(), 1)
            self.assertIsInstance(pending, PendingOperation)
            self.assertEqual(pending.pending_queries, (original,))
            self.assertIs(original.partial_result, result)
            original.ready = True
            self.assertIs(await pending.resume(timeout=1), result)
        self.assertEqual(submitted, [original])
        self.assertEqual(executor.pending_queries, [])
        self.assertTrue(original._released)

    async def test_fatal_group_keeps_copied_values_and_never_replays_its_cursor(self):
        ctx = Context()
        cells = (data.ProjectionCell(0, 0, 13, data.Kind.U32, 0, 0, b"\0" * 4, 4),
                 data.ProjectionCell(1, 0, 0, data.Kind.BOOL, 0, 0, b"\0", 1))
        observation = data.SourceObservation(0, 0, True, True, 3, 0, 2, 2, 0, False, False)
        original = page(error=8, cells=cells, sources=(observation,), next_cursor=(0, 2),
                        range_complete=False, grouped=True, _group_has_more=True)
        with patch.object(data, "project_page", return_value=NS(result=original)) as submit:
            rows = [row async for row in projection._projection_source_pages(
                ctx, "group", ("Row.Value",), ((),), ("Root.A", "Root.B"))]
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0][1], observation)
        self.assertIs(rows[0][2], original)
        self.assertEqual(rows[0][3], cells)
        self.assertEqual(projection._projection_value(cells[0])[0], 0)
        self.assertIs(projection._projection_value(cells[1])[0], False)
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(original.next_cursor, (0, 2))


if __name__ == "__main__":
    unittest.main()
