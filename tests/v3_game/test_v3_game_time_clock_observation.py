"""Focused raw clock reporting tests; no game/native process or new clock policy."""
import asyncio
from dataclasses import fields
from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_lifecycle as life
from pdx_runtime.query_executor import SerializedQueryExecutor
from pdx_runtime.v3_game.time import TimeAPI, TimeRun
from tests.v3_game.test_v3_game_time import LegacyPlayer


def observed(text, *, raw=0, error=0, paused=True, speed=4):
    values = {field.name: 0 for field in fields(life.LifecycleResult)}
    values.update(size=life.LIFECYCLE_RESULT.size, error=error, world=77,
                  paused=int(paused), speed=speed, date_raw=raw,
                  path="", resolved_path="", date_text=text)
    return life.LifecycleResult(**values)


class PublicClockTests(unittest.TestCase):
    def test_raw_clock_keeps_original_result_zero_error_text_and_json_protocol(self):
        api = TimeAPI(object())
        original = observed("1854.6.16.18", raw=0, error=574)
        api._last_observed = observed("1900.1.1.0", raw=999)
        result = api._public(original)
        clock = result["clock"]
        self.assertIs(clock.native_result, original)
        expanded = {"reason": "date", **result, "target": "1854-06-15 18:00"}
        self.assertIs(expanded["clock"], clock)
        self.assertIs(expanded["clock"].native_result, original)
        self.assertEqual(result["date"], "1854-06-16 18:00")
        self.assertEqual((result["paused"], result["speed"]), (True, 0))
        self.assertEqual(clock["date_raw"], 0)
        self.assertEqual(clock["error"], 574)
        self.assertEqual(clock["date_text"], "1854.6.16.18")
        self.assertEqual(clock["date_parts"], (1854, 6, 16, 18))
        self.assertEqual(clock["text_precision"], "hour")
        self.assertEqual(clock["hour_source"], "native_text")
        encoded = json.loads(json.dumps(result))
        self.assertEqual(encoded["clock"]["date_raw"], 0)
        self.assertEqual(encoded["clock"]["error"], 574)
        self.assertNotIn("native_result", encoded["clock"])
        api._public(api._last_observed)
        self.assertIs(clock.native_result, original)

    def test_three_part_native_text_does_not_claim_native_midnight_precision(self):
        original = observed("1854.6.17")
        result = TimeAPI(object())._public(original)
        self.assertEqual(result["date"], "1854-06-17")
        self.assertEqual(result["clock"]["date_parts"], (1854, 6, 17, 0))
        self.assertEqual(result["clock"]["date_text"], "1854.6.17")
        self.assertEqual(result["clock"]["text_precision"], "day")
        self.assertEqual(result["clock"]["hour_source"], "parser_default_zero")
        self.assertIs(result["clock"].native_result, original)

    def test_four_part_midnight_keeps_explicit_native_hour_and_legacy_date(self):
        original = observed("1854.6.17.0")
        result = TimeAPI(object())._public(original)
        self.assertEqual(result["date"], "1854-06-17")
        self.assertEqual(result["clock"]["text_precision"], "hour")
        self.assertEqual(result["clock"]["hour_source"], "native_text")
        self.assertEqual(result["clock"]["date_parts"], (1854, 6, 17, 0))
        self.assertIs(result["clock"].native_result, original)


class Query:
    def __init__(self, result):
        self.result = result
        self._result = None
        self._released = False
        self.reads = 0

    def read(self):
        self.reads += 1
        self._result = self.result
        self._released = True
        return self.result


class ClockContext:
    """Existing executor and original query ownership, fixture clock bytes only."""
    def __init__(self):
        self.world = 77
        self.native = object()
        self.process = object()
        self.adapters = {"lifecycle": "fixture"}
        self.session = N(namespace={})
        self.executor = SerializedQueryExecutor(timeout=1, poll_interval=0)
        self.diagnostics = self.executor.diagnostics
        self.current = datetime(1854, 6, 15, 18)
        self.paused = True
        self.speed = 4
        self.unpaused_observations = 0
        self.labels = []
        self.queries = []
        self.observations = []
        self.controls = []

    @property
    def pending_queries(self):
        return self.executor.pending_queries

    async def perform(self, label, body):
        return await self.executor.perform(label, body, context=self)

    async def query(self, label, factory):
        self.labels.append(label)
        return await self.executor.query(label, factory)

    def make_query(self, result):
        query = Query(result)
        self.queries.append(query)
        return query

    def snapshot(self):
        return observed(self.current.strftime("%Y.%m.%d.%H"),
                        raw=0, paused=self.paused, speed=self.speed)

    async def refresh(self):
        if not self.paused:
            # Already-running game crosses target between two original reads.
            self.unpaused_observations += 1
            self.current = (datetime(1854, 6, 16, 18)
                            if self.unpaused_observations == 1 else datetime(1854, 6, 17))
        original = self.snapshot()
        self.observations.append(original)
        return await self.query("world.observe", lambda: self.make_query(original))

    def set_speed(self, native, speed, **kwargs):
        self.speed = speed
        result = self.snapshot()
        self.controls.append(("speed", speed, result))
        return self.make_query(result)

    def set_paused(self, native, paused, **kwargs):
        self.paused = paused
        result = self.snapshot()
        self.controls.append(("paused", paused, result))
        return self.make_query(result)


class ClockOperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_keeps_requested_target_actual_pause_result_and_query_cleanup(self):
        ctx = ClockContext()
        api = TimeAPI(ctx)
        api._events = AsyncMock(return_value=[])
        api._perception = AsyncMock(return_value={"cursor": None, "changes": [], "receipts": [], "sources": []})
        with tempfile.TemporaryDirectory() as folder:
            player = LegacyPlayer(folder)
            ctx.session.namespace["player"] = player
            with patch.object(life, "set_speed", side_effect=ctx.set_speed), patch.object(life, "set_paused", side_effect=ctx.set_paused):
                handle = await api.run_for(days=1, speed=5)
                self.assertIsInstance(handle, TimeRun)
                try:
                    notice = await handle.wait()
                finally:
                    if not handle._task.done():
                        await handle.interrupt()
            self.assertEqual(handle["target"], "1854-06-16 18:00")
            self.assertEqual(notice["target"], "1854-06-16 18:00")
            self.assertEqual(notice["date"], "1854-06-17")
            self.assertEqual((notice["reason"], notice["paused"], notice["speed"]), ("date", True, 0))
            self.assertIs(notice["clock"].native_result, ctx.observations[-1])
            self.assertIsNot(notice["clock"].native_result, ctx.controls[-1][2])
            self.assertEqual(notice["clock"]["date_text"], "1854.06.17.00")
            self.assertEqual(notice["clock"]["date_raw"], 0)
            self.assertEqual(notice["clock"]["text_precision"], "hour")
            self.assertEqual(ctx.labels, ["world.observe"] * 3 + ["time.speed", "time.resume", "world.observe", "world.observe", "time.pause", "world.observe"])
            self.assertEqual([(kind, value) for kind, value, result in ctx.controls], [("speed", 4), ("paused", False), ("paused", True)])
            self.assertEqual(len(ctx.queries), 9)
            self.assertTrue(all(query._released and query.reads == 1 for query in ctx.queries))
            self.assertEqual(ctx.pending_queries, [])
            self.assertFalse(ctx.executor._query_lock.locked())
            self.assertEqual(ctx.executor.owner_pool.snapshot(), ())
            recorded = json.loads((Path(folder) / "notices.json").read_text(encoding="utf-8"))
            self.assertEqual(recorded[str(handle["watch"])]["clock"]["date_text"], "1854.06.17.00")
            before = len(ctx.queries)
            self.assertIs(await handle.wait(), notice)
            self.assertEqual(len(ctx.queries), before)
            api._last_observed = observed("1900.1.1.0", raw=999)
            self.assertIs(notice["clock"].native_result, ctx.observations[-1])

    async def test_observe_exposes_the_same_single_original_query_result(self):
        ctx = ClockContext()
        api = TimeAPI(ctx)
        result = await api.observe()
        self.assertIs(result["clock"].native_result, ctx.observations[0])
        self.assertEqual(ctx.labels, ["world.observe"])
        self.assertEqual(len(ctx.queries), 1)
        self.assertEqual(ctx.queries[0].reads, 1)
        self.assertTrue(ctx.queries[0]._released)
        self.assertEqual(ctx.pending_queries, [])
        self.assertFalse(ctx.executor._query_lock.locked())


if __name__ == "__main__":
    unittest.main()
