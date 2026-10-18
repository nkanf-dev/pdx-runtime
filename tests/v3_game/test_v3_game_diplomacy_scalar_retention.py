"""Focused copied-result retention. CPU queries only, no game/native calls."""
import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pdx_runtime import v3_data as data
from pdx_runtime.query_executor import Diagnostics, RetainedOwners, SerializedQueryExecutor
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.v3_game._run import Run as _Run


class Query(data.DataQuery):
    def __init__(self, result, *, error=None, cached=False, adapter="original-data"):
        self.result, self.error, self.adapter = result, error, adapter
        self._result = result if cached else None
        self._released, self.reads = False, 0
    def read(self):
        self.reads += 1
        if self.error is not None:
            raise self.error
        self._result, self._released = self.result, True
        return self.result


class Context:
    def __init__(self):
        self.native, self.process = object(), object()
        self.world = 7
        self.adapters = {"data": "context-data", "commands": "unused-commands"}
        self.diagnostics, self.owner_pool = Diagnostics(), RetainedOwners()
        self.executor = SerializedQueryExecutor(diagnostics=self.diagnostics)
    async def query(self, label, factory, *, owner=None):
        return await self.executor.query(label, factory, owner=owner)
    async def type_id(self, name): return {"Country": 10, "int32": 11}[name]


def scalar(value, kind=data.Kind.I32, *, error=0, handle=19):
    return NS(action=data.Action.EVALUATE, error=error, kind=kind,
        type_id=11, value=value, value_handle=handle)


class DiplomacyReadRetentionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx, self.binding = Context(), data.ObjectBinding.country(10, 0)
        self.api = DiplomacyAPI(self.ctx)
    def retained(self):
        return [r for r in self.ctx.owner_pool.snapshot() if isinstance(r, _Run)]

    async def test_scalar_falsy_and_raw_bytes_survive_native_release_error_with_original_adapter(self):
        for value, kind in ((0, data.Kind.I32), (False, data.Kind.BOOL),
                            (None, data.Kind.EMPTY), (b"\x00\xff", data.Kind.RAW)):
            with self.subTest(value=value):
                self.setUp()
                native = scalar(value, kind)
                release_result = NS(action=data.Action.RELEASE, error=5, value_handle=19)
                query = Query(native)
                with patch.object(data, "evaluate", return_value=query) as make, \
                     patch.object(data, "release", return_value=Query(release_result)) as release:
                    observed = await self.api._scalar("Country.GetName", self.binding)
                self.assertIs(observed, value)
                run = self.retained()[0]
                self.assertIs(run.public_result["native_result"], native)
                self.assertIs(run.public_result["value"], value)
                self.assertIs(run.public_result["cleanup"]["native_result"], release_result)
                self.assertEqual(run.values, [19])
                self.assertEqual(release.call_args.kwargs["adapter"], "original-data")
                make.assert_called_once()
                release.assert_called_once()

    async def test_native_getter_error_and_cleanup_error_remain_distinct_not_zero(self):
        native = scalar(0, error=1168)
        cleanup = NS(action=data.Action.RELEASE, error=5, value_handle=19)
        with patch.object(data, "evaluate", return_value=Query(native)), \
             patch.object(data, "release", return_value=Query(cleanup)):
            result = await self.api._scalar("Country.GetName", self.binding)
        self.assertIsNone(result)
        report = self.retained()[0].public_result
        self.assertEqual(report["native_error"], 1168)
        self.assertIs(report["native_result"], native)
        self.assertIs(report["cleanup"]["native_result"], cleanup)

    async def test_relation_zero_survives_release_error_without_changing_original_scope(self):
        player = NS(error=0, controlled_country_error=0, world=7,
                    observer_enabled=False, controlled_country_id=0)
        function = NS(name="GetRelationsWith", type_id=10, argument_count=1,
                      return_type_id=11, callbacks=(1, 0, 0, 0))
        self.api._country_functions = AsyncMock(return_value=(function,))
        native = scalar(0)
        with patch("pdx_runtime.v3_game.diplomacy.player.observe_player", return_value=Query(player)), \
             patch.object(data, "evaluate", return_value=Query(native)) as make, \
             patch.object(data, "release", return_value=Query(NS(error=5, value_handle=19))):
            result = await self.api._relation_points(0, 0x80000002)
        self.assertEqual(result, (0, None))
        self.assertEqual(make.call_args.args[1], "Country.GetRelationsWith(GetPlayer)")
        self.assertEqual(make.call_args.args[2][0].object, 0x80000002)
        self.assertIs(self.retained()[0].public_result["native_result"], native)

    async def test_cleanup_unknown_returns_primary_and_keeps_same_query_without_replay(self):
        native, error = scalar(0), RuntimeError("original release delivery unknown")
        release_query = Query(None, error=error)
        with patch.object(data, "evaluate", return_value=Query(native)) as make, \
             patch.object(data, "release", return_value=release_query) as release:
            result = await self.api._scalar("Country.GetName", self.binding)
        self.assertEqual(result, 0)
        run = self.retained()[0]
        self.assertIs(run.uncertain_query, release_query)
        self.assertEqual(run.values, [19])
        self.assertIs(run.public_result["value"], native.value)
        make.assert_called_once()
        release.assert_called_once()
        self.assertEqual(release_query.reads, 1)

    async def test_getter_cached_partial_keeps_original_error_value_query_and_any(self):
        native, error = scalar(False, data.Kind.BOOL), RuntimeError("cached getter observation error")
        query = Query(native, error=error, cached=True)
        with patch.object(data, "evaluate", return_value=query) as make, \
             patch.object(data, "release") as release:
            with self.assertRaises(RuntimeError) as caught:
                await self.api._scalar("Country.GetName", self.binding)
        self.assertIs(caught.exception, error)
        self.assertIs(error.diplomacy_read_result["value"], False)
        self.assertIs(error.diplomacy_read_result["native_result"], native)
        self.assertIs(error.diplomacy_read_scope.uncertain_query, query)
        self.assertEqual(error.diplomacy_read_scope.values, [19])
        self.assertEqual(error.diplomacy_read_scope.value_adapters[19], "original-data")
        make.assert_called_once()
        release.assert_not_called()

    async def test_real_cancellation_keeps_same_error_and_known_primary_without_overwriting_context(self):
        native, error = scalar(None, data.Kind.EMPTY), asyncio.CancelledError("original cleanup cancel")
        error.observed_result = None
        query = Query(None, error=error)
        with patch.object(data, "evaluate", return_value=Query(native)), \
             patch.object(data, "release", return_value=query) as release:
            with self.assertRaises(asyncio.CancelledError) as caught:
                await self.api._scalar("Country.GetName", self.binding)
        self.assertIs(caught.exception, error)
        self.assertIsNone(error.observed_result)
        self.assertIn("value", error.diplomacy_read_result)
        self.assertIsNone(error.diplomacy_read_result["value"])
        self.assertIs(error.diplomacy_read_result["native_result"], native)
        self.assertIs(error.diplomacy_read_scope.uncertain_query, query)
        self.assertTrue(any(item is error.diplomacy_read_scope for item in self.ctx.owner_pool.snapshot()))
        release.assert_called_once()

    async def test_borrowed_object_is_still_rejected_without_cleanup_replacing_error(self):
        native = scalar(object(), data.Kind.OBJECT)
        with patch.object(data, "evaluate", return_value=Query(native)), \
             patch.object(data, "release", return_value=Query(NS(error=5, value_handle=19))):
            with self.assertRaisesRegex(RuntimeError, "borrowed object") as caught:
                await self.api._scalar("Country.GetName", self.binding)
        self.assertIs(caught.exception.diplomacy_read_result["native_result"], native)
        self.assertEqual(caught.exception.diplomacy_read_result["cleanup"]["native_result"].error, 5)


if __name__ == "__main__":
    unittest.main()
