"""Owned-State infrastructure reads. CPU fixtures only; no process/native call."""
from __future__ import annotations

import asyncio
from threading import Event
from dataclasses import replace
from decimal import Decimal
import struct
from types import SimpleNamespace as R
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_data as data, v3_objects as objects
from pdx_runtime.v3_game.country import CountryAPI
from pdx_runtime.v3_game._core import GameContext, PendingOperation

COUNTRY, STATE, WORLD = 0xAB000001, 0xCD000003, 0x12340000
TYPES = {"State": 71, "CFixedPoint": 83, "bool": 97}
GETTERS = ("GetInfrastructure", "GetInfrastructureUsage", "GetInfrastructureBalance", "GetMarketAccess", "IsIsolatedFromMarket", "GetMarketAccessStatus")


class Query:
    def __init__(self, result):
        self.result, self._result, self._released, self.request_id = result, None, False, 17
        self.reads = 0
    def read(self):
        self.reads += 1
        if self.result is not None:
            self._result, self._released = self.result, True
        return self.result


class Context:
    def __init__(self):
        self.native, self.process = R(pid=17), R(pid=17)
        self.session = R(native=self.native, namespace={})
        self.world, self.image_base = WORLD, 0x140000000
        self.adapters = {"objects": "custom-objects", "data": "custom-data"}
        self.diagnostics, self.operations, self.queries = [], [], []
        self.fail_label = None
    async def refresh(self):
        return R(world=self.world)
    async def country_id(self):
        return COUNTRY
    async def type_id(self, name):
        return TYPES[name]
    async def query(self, label, factory):
        query = factory()
        self.queries.append((label, query))
        if label == self.fail_label:
            raise TimeoutError("original read completion is unknown")
        return query.read()
    async def perform(self, label, operation):
        self.operations.append(label)
        return await operation()


class Fixture:
    def __init__(self, ctx):
        self.ctx = ctx
        self.page_world, self.page_error, self.owner = WORLD, 0, COUNTRY
        self.record_error, self.missing, self.resolved = 0, False, None
        self.raw = dict(zip(GETTERS, (12345678, 20000000, -7654322, 0, True)))
        self.status_text = "Native market status"
        self.rows = [R(name=name, type_id=TYPES["State"], argument_count=0,
            callbacks=(1, 0, 0, 0), return_type_id=(TYPES["bool"] if name.startswith("Is")
                else 0 if name == "GetMarketAccessStatus" else TYPES["CFixedPoint"]))
            for name in GETTERS]
        self.evaluated, self.released, self.lookups, self.created = [], [], [], []
        self.handle, self.release_error = 100, 0
        self.result_overrides = {}
        self.after_evaluate = None
    def install(self, case):
        for target, name, replacement in ((objects, "read_ids", self.read_ids),
                (data, "enumerate_functions", self.functions), (data, "evaluate", self.evaluate),
                (data, "release", self.release)):
            item = patch.object(target, name, replacement)
            item.start()
            case.addCleanup(item.stop)
    def read_ids(self, client, domain, ids, **options):
        ids = tuple(ids)
        self.lookups.append((client, domain, ids, options))
        full_id, = ids
        record = objects.State(full_id, full_id if self.resolved is None else self.resolved,
            self.record_error, 3, 0xDEADBEEF, self.owner, 0)
        return Query(R(error=self.page_error, world=self.page_world,
            records=() if self.missing else (record,)))
    def functions(self, client, type_id, *, start, capacity, **options):
        if type_id != TYPES["State"]:
            raise AssertionError("unexpected receiver type")
        return Query(R(error=0, rows=tuple(self.rows), next=len(self.rows), total=len(self.rows)))
    def scalar(self, getter):
        self.handle += 1
        result = data.DataResult.from_bytes(data._request(data.Action.EVALUATE))
        raw = self.raw.get(getter)
        if getter == "GetMarketAccessStatus":
            snapshot, kind, type_id, native_name = self.status_text.encode("utf-8"), data.Kind.STRING, 0, ""
            bits = 0
        elif getter.startswith("Is"):
            snapshot, kind, type_id, native_name = bytes((int(raw),)), data.Kind.BOOL, TYPES["bool"], ".?AU?$SAnyType@_N@PdxAnyImpl@@"
            bits = int(raw)
        else:
            snapshot, kind, type_id = struct.pack("<q", raw), data.Kind.FIXED, TYPES["CFixedPoint"]
            native_name = "CFixedPoint"
            bits = raw & 0xFFFFFFFFFFFFFFFF
        result = replace(result, error=0, value_handle=self.handle, kind=kind, type_id=type_id,
            snapshot=snapshot, snapshot_bytes=len(snapshot), total=len(snapshot), payload_size=len(snapshot),
            raw_bits=bits, any_type=0x1000, any_vtable=0x2000, native_type_name=native_name)
        if getter in self.result_overrides:
            result = replace(result, **self.result_overrides[getter])
        self.created.append(result.value_handle)
        return result
    def evaluate(self, client, expression, bindings, **options):
        self.evaluated.append((expression, bindings, options))
        getter = expression.removeprefix("State.")
        if getter not in GETTERS:
            raise AssertionError("unexpected getter: " + expression)
        result = self.scalar(getter)
        if self.after_evaluate is not None:
            self.after_evaluate(expression)
        return Query(result)
    def release(self, client, handle, **options):
        self.released.append(handle)
        return Query(R(error=self.release_error, value_handle=handle if self.release_error else 0))


class StateInfrastructureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = Context()
        self.api = CountryAPI(self.ctx)
        self.fixture = Fixture(self.ctx)
        self.fixture.install(self)
    def assert_clean(self):
        self.assertEqual(set(self.fixture.released), set(self.fixture.created))
        self.assertEqual(len(self.fixture.released), len(set(self.fixture.released)))
        self.assertEqual(self.api._retained_reads, [])
    async def test_real_signed_points_zero_access_and_true_isolation_have_units(self):
        result = await self.api.state_infrastructure(STATE)
        self.assertTrue(result["available"])
        self.assertEqual((result["state_id"], result["country_id"], result["scope"]), (STATE, COUNTRY, "state"))
        self.assertEqual(result["capacity"], Decimal("123.45678"))
        self.assertEqual(result["usage"], Decimal("200.00000"))
        self.assertEqual(result["balance"], Decimal("-76.54322"))
        self.assertEqual(result["market_access"], Decimal("0.00000"))
        self.assertIs(result["isolated_from_market"], True)
        self.assertEqual(result["units"], {"capacity":"infrastructure points", "usage":"infrastructure points",
            "balance":"infrastructure points", "market_access":"fraction"})
        self.assertFalse(result["atomic_snapshot"])
        self.assertEqual(result["reasons"], {})
        self.assertEqual(self.ctx.operations, ["country.state_infrastructure"])
        for expression, bindings, options in self.fixture.evaluated:
            binding, = bindings
            self.assertEqual(binding, data.ObjectBinding.state(TYPES["State"], STATE))
            self.assertNotEqual(binding.object, 0xDEADBEEF)
            self.assertIs(options["process"], self.ctx.process)
            self.assertEqual(options["adapter"], "custom-data")
        self.assert_clean()
    async def test_zero_and_false_are_not_missing_and_fullid_zero_is_accepted(self):
        self.fixture.raw = {name:False if name.startswith("Is") else 0 for name in GETTERS}
        result = await self.api.state_infrastructure(0)
        self.assertTrue(result["available"])
        self.assertEqual(result["state_id"], 0)
        for field in ("capacity", "usage", "balance", "market_access"):
            self.assertEqual(result[field], Decimal("0.00000"))
        self.assertIs(result["isolated_from_market"], False)
        self.assertEqual(result["reasons"], {})
        self.assertEqual(self.fixture.lookups[0][2], (0,))
        self.assert_clean()
    async def test_status_text_preserves_native_markup_and_empty_string(self):
        for text in ("\x15tooltip 原生隔绝状态：无陆路或港口连接\x15!", ""):
            with self.subTest(text=text):
                self.fixture.status_text = text
                result = await self.api.state_infrastructure(STATE)
                self.assertEqual(result["market_access_status_text"], text)
                self.assertNotIn("market_access_status_text", result["reasons"])
        self.assert_clean()
    async def test_unknown_or_invalid_status_text_is_none_with_reason(self):
        for override in ({"kind":data.Kind.FIXED}, {"total":1000}, {"snapshot":b"\xff", "snapshot_bytes":1, "total":1}):
            with self.subTest(override=override):
                self.fixture.result_overrides["GetMarketAccessStatus"] = override
                result = await self.api.state_infrastructure(STATE)
                self.assertIsNone(result["market_access_status_text"])
                self.assertTrue(result["reasons"]["market_access_status_text"])
        self.fixture.rows = [row for row in self.fixture.rows if row.name != "GetMarketAccessStatus"]
        result = await self.api.state_infrastructure(STATE)
        self.assertIsNone(result["market_access_status_text"])
        self.assertIn("missing", result["reasons"]["market_access_status_text"])
        self.assert_clean()
    async def test_invalid_fullids_are_rejected_before_work(self):
        for full_id in (None, True, False, -1, 1.5, "930", {}, 0xFFFFFFFF, 0x100000000):
            with self.subTest(full_id=full_id), self.assertRaises(ValueError):
                await self.api.state_infrastructure(full_id)
        self.assertEqual(self.ctx.operations, [])
        self.assertEqual(self.fixture.lookups, [])
    async def test_missing_or_foreign_state_returns_unknown_values_without_getters(self):
        for failure in ("missing", "record_error", "resolved", "foreign", "page_error"):
            with self.subTest(failure=failure):
                self.fixture.missing = failure == "missing"
                self.fixture.record_error = 1168 if failure == "record_error" else 0
                self.fixture.resolved = STATE + (1 << 24) if failure == "resolved" else None
                self.fixture.owner = COUNTRY + 1 if failure == "foreign" else COUNTRY
                self.fixture.page_error = 50 if failure == "page_error" else 0
                result = await self.api.state_infrastructure(STATE)
                self.assertFalse(result["available"])
                for field in ("capacity", "usage", "balance", "market_access", "isolated_from_market", "market_access_status_text"):
                    self.assertIsNone(result[field])
                    self.assertTrue(result["reasons"][field])
                self.assertTrue(result["reasons"]["state"])
        self.assertEqual(self.fixture.evaluated, [])
        self.assert_clean()
    async def test_lookup_world_change_preserves_actual_native_error(self):
        self.fixture.page_world = WORLD + 1
        self.fixture.page_error = 50
        result = await self.api.state_infrastructure(STATE)
        self.assertFalse(result["available"])
        self.assertIn("50", result["reasons"]["state"])
        for field in ("capacity", "usage", "balance", "market_access",
                "isolated_from_market", "market_access_status_text"):
            self.assertIsNone(result[field])
            self.assertIn("50", result["reasons"][field])
        evidence, = self.ctx.diagnostics
        self.assertEqual(evidence["event"], "world_changed")
        self.assertEqual((evidence["previous_world"], evidence["world"]), (WORLD, WORLD + 1))
        self.assertEqual(self.fixture.lookups[0][3]["adapter"], "custom-objects")
        self.assertEqual(self.fixture.evaluated, [])
        self.assert_clean()
    async def test_missing_or_action_getters_return_none_with_reason(self):
        self.fixture.rows = [row for row in self.fixture.rows if row.name != "GetInfrastructureUsage"]
        isolation = next(row for row in self.fixture.rows if row.name == "IsIsolatedFromMarket")
        isolation.callbacks = (0, 0, 1, 0)
        result = await self.api.state_infrastructure(STATE)
        self.assertIsNone(result["usage"])
        self.assertIn("missing", result["reasons"]["usage"])
        self.assertIsNone(result["isolated_from_market"])
        self.assertIn("read getter", result["reasons"]["isolated_from_market"])
        expressions = [row[0] for row in self.fixture.evaluated]
        self.assertNotIn("State.GetInfrastructureUsage", expressions)
        self.assertNotIn("State.IsIsolatedFromMarket", expressions)
        self.assert_clean()
    async def test_alias_type_keeps_actual_scalar_while_wrong_kinds_and_errors_stay_unknown(self):
        self.fixture.result_overrides = {"GetInfrastructure":{"type_id":TYPES["bool"]},
            "GetInfrastructureUsage":{"kind":data.Kind.I64},
            "GetInfrastructureBalance":{"error":13},
            "IsIsolatedFromMarket":{"kind":data.Kind.FIXED}}
        result = await self.api.state_infrastructure(STATE)
        self.assertEqual(result["capacity"], Decimal("123.45678"))
        self.assertNotIn("capacity", result["reasons"])
        for field in ("usage", "balance", "isolated_from_market"):
            self.assertIsNone(result[field])
            self.assertTrue(result["reasons"][field])
        self.assertIn("13", result["reasons"]["balance"])
        self.assertEqual(result["market_access"], 0)
        self.assert_clean()
    async def test_world_change_during_getter_keeps_separate_observations_and_releases_values(self):
        def change(expression):
            if expression == "State.GetInfrastructureUsage":
                self.ctx.world += 1
        self.fixture.after_evaluate = change
        result = await self.api.state_infrastructure(STATE)
        self.assertTrue(result["available"])
        self.assertEqual(result["capacity"], Decimal("123.45678"))
        self.assertEqual(result["usage"], Decimal("200.00000"))
        self.assertEqual(result["balance"], Decimal("-76.54322"))
        self.assertEqual(result["market_access"], 0)
        self.assertIs(result["isolated_from_market"], True)
        self.assertFalse(result["atomic_snapshot"])
        self.assertEqual(result["reasons"], {})
        evidence, = self.ctx.diagnostics
        self.assertEqual(evidence["event"], "world_changed")
        self.assertEqual(evidence["world"], WORLD + 1)
        self.assertEqual(len(self.fixture.evaluated), len(GETTERS))
        self.assert_clean()
    async def test_actual_boolean_needs_no_redundant_bool_type_lookup(self):
        original = self.ctx.type_id
        looked_up = []
        async def type_id(name):
            looked_up.append(name)
            if name == "bool":
                raise AssertionError("copied boolean must not need another type lookup")
            return await original(name)
        self.ctx.type_id = type_id
        result = await self.api.state_infrastructure(STATE)
        self.assertIs(result["isolated_from_market"], True)
        self.assertEqual(looked_up, ["State"])
        self.assertEqual(result["reasons"], {})
        self.assert_clean()
    async def test_original_unknown_read_keeps_prior_owners_and_query_graph(self):
        self.ctx.fail_label = "State.GetInfrastructureUsage"
        with self.assertRaisesRegex(TimeoutError, "original read"):
            await self.api.state_infrastructure(STATE)
        self.assertEqual(self.fixture.released, [])
        self.assertEqual(len(self.api._retained_reads), 1)
        retained = self.api._retained_reads[0]
        self.assertTrue(retained.uncertain)
        self.assertEqual(retained.owners, self.fixture.created[:1])
        self.assertEqual(self.ctx.queries[-1][0], "State.GetInfrastructureUsage")
        await retained.close()  # Unknown ownership is not a retry/free invitation.
        self.assertEqual(self.fixture.released, [])
    async def test_failed_cleanup_is_retained_and_never_replayed(self):
        self.fixture.release_error = 574
        with self.assertRaisesRegex(RuntimeError, "cleanup"):
            await self.api.state_infrastructure(STATE)
        self.assertEqual(len(self.fixture.released), 1)
        self.assertEqual(len(self.api._retained_reads), 1)
        retained = self.api._retained_reads[0]
        self.assertEqual(len(retained.owners), 6)
        await retained.close()
        self.assertEqual(len(self.fixture.released), 1)
    async def test_real_context_pending_resumes_original_query_then_cleans_once(self):
        ctx = GameContext(self.ctx.session, self.ctx.process, adapters=self.ctx.adapters,
            timeout=.001, poll_interval=.001)
        ctx.world, ctx.image_base = WORLD, self.ctx.image_base
        ctx.refresh = AsyncMock()
        ctx.country_id = AsyncMock(return_value=COUNTRY)
        ctx.type_id = AsyncMock(side_effect=lambda name:TYPES[name])
        api = CountryAPI(ctx)
        original_evaluate = self.fixture.evaluate
        held = []
        loop = asyncio.get_running_loop()
        started, proceed = asyncio.Event(), Event()
        def evaluate(client, expression, bindings, **options):
            query = original_evaluate(client, expression, bindings, **options)
            if expression == "State.GetMarketAccess":
                held.append(query)
                original_read = query.read
                def read():
                    if not proceed.is_set():
                        loop.call_soon_threadsafe(started.set)
                        return None
                    return original_read()
                query.read = read
            return query
        pending = None
        with patch.object(data, "evaluate", evaluate):
            try:
                pending = await api.state_infrastructure(STATE)
                self.assertIsInstance(pending, PendingOperation)
                await asyncio.wait_for(started.wait(), 1)
                self.assertEqual(len(held), 1)
                self.assertIn(held[0], ctx.pending_queries)
                self.assertEqual(self.fixture.released, [])
                proceed.set()
                observed = await pending.resume(timeout=1)
            finally:
                proceed.set()
                if isinstance(pending, PendingOperation):
                    await pending.task
        self.assertEqual(observed["market_access"], 0)
        self.assertEqual(sum(row[0] == "State.GetMarketAccess" for row in self.fixture.evaluated), 1)
        self.assertEqual(ctx.pending_queries, [])
        self.assertFalse(ctx._query_lock.locked())
        self.assertEqual(api._retained_reads, [])
        self.assertEqual(set(self.fixture.released), set(self.fixture.created))
        self.assertEqual(len(self.fixture.released), len(set(self.fixture.released)))


if __name__ == "__main__":
    unittest.main()
