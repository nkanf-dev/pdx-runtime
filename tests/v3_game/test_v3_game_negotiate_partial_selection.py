"""POST-CUT3 CPU-only negotiation selection over partial original IG sources.

Real GovernanceMixin.negotiate and canonical selected_command/_Run ownership run.
Original source/row/query/command results are fixtures, not game eligibility proof.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.query_executor import RetainedOwners, SerializedQueryExecutor, PendingOperation
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.politics import governance


class Query:
    serial = 0
    def __init__(self, result, *, partial=None, failure=None, pending=False, loop=None, entered=None):
        Query.serial += 1
        self.request_id, self.result = Query.serial, result
        self.adapter = "original-data"
        self.partial_result, self.failure = partial, failure
        self.ready, self.loop, self.entered = not pending, loop, entered
    def read(self):
        if self.entered is not None: self.loop.call_soon_threadsafe(self.entered.set)
        if self.failure is not None: raise self.failure
        return self.result if self.ready else None


class API(governance.GovernanceMixin):
    def __init__(self, context):
        self._ctx, self._mutation_lock = context, asyncio.Lock()


class Owner:
    def __init__(self, fixture):
        self.f, self.command, self.world = fixture, 0xA000, fixture.world
        self.pending, self.attempt = None, None
    def validate(self, *, capacity):
        self.f.validations += 1
        self.attempt = NS(outcome="owned")
        self.pending = Query(self.f.validation)
        return self.pending
    def submit(self, *, flags):
        assert flags == 4
        self.f.submissions += 1
        self.pending = Query(self.f.submission)
        return self.pending
    def complete(self):
        result = self.pending.read()
        if result is not None:
            self.pending = None
            if result is self.f.submission:
                self.command, self.attempt = 0, NS(outcome="consumed")
        return result
    def destroy(self):
        self.f.destructions += 1
        self.pending = Query(NS(error=0, command=0))
        self.command, self.attempt = 0, NS(outcome="destroyed")
        return self.pending
    def serialize(self): raise AssertionError("selected negotiation must not serialize")


class Fixture:
    world = 0x5000
    def __init__(self, rows):
        self.rows, self.live, self.bound = rows, set(), {}
        self.events, self.releases, self.identity_queries, self.identity_results = [], [], [], []
        self.producers = self.clones = self.validations = self.submissions = self.destructions = 0
        self.validation = NS(error=0, validated=True, reason="original native negotiation rule")
        self.submission = NS(error=0, command=0, accepted=True)
        self.loop, self.entered = asyncio.get_running_loop(), asyncio.Event()
        self.ctx = NS(native=object(), process=object(), world=self.world,
            adapters={"data": "original-data", "commands": "original-command"}, diagnostics=[], owner_pool=RetainedOwners())
        self.executor = SerializedQueryExecutor(timeout=1, poll_interval=.001,
            diagnostics=self.ctx.diagnostics, owners=self.ctx.owner_pool)
        self.ctx.query, self.ctx.perform, self.ctx._remember = self.executor.query, self.executor.perform, self.executor._remember
        async def country(): return 0x81000001
        async def type_id(name): return {"Country": 7, "InterestGroup": 8}[name]
        self.ctx.country_id, self.ctx.type_id = country, type_id
        self.api, self.owner = API(self.ctx), None
    def native(self, action, handle, value, kind, *, error=0, type_id=-1, name=""):
        if handle: self.live.add(handle)
        return NS(error=error, action=action, value_handle=handle, value=value,
                  kind=kind, type_id=type_id, native_type_name=name)
    def evaluate(self, native, expression, bindings, **kwargs):
        self.events.append((expression, tuple(bindings)))
        assert kwargs["adapter"] == "original-data"
        if expression == "Country.AccessAllInterestGroups":
            return Query(self.native(data.Action.EVALUATE, 100, data.NativeList(100, 0, b"", "original Creation"), data.Kind.LIST))
        if expression == "InterestGroup.GetID":
            group, = bindings
            assert group.retained and group.type_id == 8 and group.object in self.live
            index = self.bound[group.object]
            row = self.rows[index]
            result = self.native(data.Action.EVALUATE, 300 + index, row.get("value"), row.get("kind", data.Kind.U32), error=row.get("error", 0))
            failure = row.get("failure")
            if row.get("uncertain_known_error"):
                failure = run_mod.KnownResultError("InterestGroup.GetID", result)
                failure.query = Query(result, partial=row.get("partial"))
            query = Query(result, partial=None if row.get("uncertain_known_error") else row.get("partial"), failure=failure,
                pending=row.get("pending", False), loop=self.loop,
                entered=self.entered if row.get("pending") else None)
            self.identity_queries.append(query)
            self.identity_results.append(result)
            return query
        assert expression == "Country.StartNegotiationWith(InterestGroup.Self)"
        self.producers += 1
        country, group = bindings
        assert country.country_id and country.object == 0x81000001
        assert group.retained and group.type_id == 8 and group.object in self.live
        self.selected_binding, self.selected_index = group, self.bound[group.object]
        self.carrier = data.NativeDataCommand(0x8000, 0x8010, 0x8018, 900, "original command", self.world)
        return Query(self.native(data.Action.EVALUATE, 900, self.carrier, data.Kind.COMMAND))
    def count(self, native, handle, **kwargs):
        assert handle == 100 and handle in self.live
        return Query(NS(error=0, action=data.Action.LIST_COUNT, total=len(self.rows), value_handle=0))
    def element(self, native, handle, index, **kwargs):
        assert handle == 100 and handle in self.live
        name = self.rows[index].get("header", ".?AU?$SAnyType@V?$CPdxPtr@VCDataInterestGroup@@@@@PdxAnyImpl@@")
        handle = 200 + index
        self.bound[handle] = index
        value = data.NativeObject(0x9000 + index * 0x100, 8, handle, name)
        return Query(self.native(data.Action.LIST_ELEMENT, handle, value, data.Kind.OBJECT, type_id=8, name=name))
    def carrier_value(self, native, handle, **kwargs):
        assert handle == 900 and handle in self.live
        return Query(NS(error=0, action=data.Action.COMMAND_VALUE, value_handle=handle, value=self.carrier))
    def clone(self, native, action, **kwargs):
        assert action == commands.Action.CLONE and kwargs["command"] == 0x8000
        assert kwargs["world"] == self.world and kwargs["adapter"] == "original-command"
        self.clones += 1
        self.owner = Owner(self)
        return Query(NS(error=0, cloned_command=0xA000, owner=self.owner, world=self.world))
    def release(self, native, handle, **kwargs):
        assert handle in self.live and kwargs["adapter"] == "original-data"
        self.releases.append(handle)
        self.live.remove(handle)
        return Query(NS(error=0, action=data.Action.RELEASE, value_handle=0))


class NegotiatePartialSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self): self.patchers = []
    def tearDown(self):
        for patcher in reversed(self.patchers): patcher.stop()
    def fixture(self, rows):
        fx = Fixture(rows)
        for patcher in (patch.object(data, "evaluate", fx.evaluate), patch.object(data, "list_count", fx.count),
                patch.object(data, "list_element", fx.element), patch.object(data, "command_value", fx.carrier_value),
                patch.object(data, "release", fx.release), patch.object(commands, "request", fx.clone),
                patch.object(commands.NativeCommand, "from_result", side_effect=lambda native, result, **kw: result.owner)):
            patcher.start(); self.patchers.append(patcher)
        return fx
    def cleaned(self, fx):
        self.assertEqual(fx.live, set())
        self.assertEqual(len(fx.releases), len(set(fx.releases)))
        self.assertFalse(any(isinstance(owner, run_mod.Run) for owner in fx.ctx.owner_pool.snapshot()))
    def runs(self, fx): return [owner for owner in fx.ctx.owner_pool.snapshot() if isinstance(owner, run_mod.Run)]

    async def test_healthy_explicit_zero_and_full_id_select_original_binding_once(self):
        for target in (0, 0xF1000004):
            with self.subTest(target=target):
                fx = self.fixture([{"value": 9}, {"value": target}])
                result = await fx.api.negotiate(target, readback=False)
                self.assertTrue(result["accepted"])
                self.assertEqual(result["selection_issues"], [])
                self.assertEqual(fx.selected_binding.object, 201)
                self.assertEqual((fx.producers, fx.clones, fx.validations, fx.submissions), (1, 1, 1, 1))
                self.assertIs(result["submission"], fx.submission)
                self.cleaned(fx)

    async def test_unselected_malformed_identity_rows_do_not_block_later_clean_target(self):
        rows = [{"value": None}, {"value": False, "kind": data.Kind.BOOL},
                {"value": 0, "kind": data.Kind.I32}, {"value": 0xffffffff}, {"value": 0}]
        fx = self.fixture(rows)
        result = await fx.api.negotiate(0, readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(fx.selected_binding.object, 204)
        self.assertEqual(len(result["selection_issues"]), 4)
        self.assertEqual([issue["native_result"].value for issue in result["selection_issues"]], [None, False, 0, 0xffffffff])
        self.assertEqual([issue["query"] for issue in result["selection_issues"]], fx.identity_queries[:4])
        self.assertTrue(all(issue["error"] is None for issue in result["selection_issues"]))
        self.assertEqual((fx.producers, fx.validations, fx.submissions), (1, 1, 1))
        self.cleaned(fx)

    async def test_completed_known_identity_error_keeps_original_partial_and_skips_errored_match(self):
        partial = NS(error=122, value=0, kind=data.Kind.U32, snapshot=b"original-prefix")
        fx = self.fixture([{"value": 0, "error": 50, "partial": partial}, {"value": 0}])
        result = await fx.api.negotiate(0, readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(fx.selected_binding.object, 201)
        issue, = result["selection_issues"]
        self.assertIsInstance(issue["error"], run_mod.KnownResultError)
        self.assertIs(issue["native_result"], fx.identity_results[0])
        self.assertIs(issue["error"].result, fx.identity_results[0])
        self.assertEqual(issue["native_result"].error, 50)
        self.assertEqual(issue["native_result"].value, 0)
        self.assertIs(issue["query"], fx.identity_queries[0])
        self.assertIs(issue["partial_result"], partial)
        self.assertEqual(issue["binding"].object, 200)
        self.assertEqual(issue["source_result"].value_handle, 200)
        self.assertTrue(any(native is fx.identity_results[0] for _, native in result["native_results"]))
        self.assertEqual((fx.producers, fx.validations, fx.submissions), (1, 1, 1))
        self.cleaned(fx)

    async def test_no_clean_match_returns_unavailable_with_all_issues_and_no_candidate_commands(self):
        fx = self.fixture([{"value": None}, {"value": False, "kind": data.Kind.BOOL},
            {"value": 0xffffffff}, {"value": 0, "error": 122}, {"value": 9}])
        result = await fx.api.negotiate(0, readback=False)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["accepted"])
        self.assertEqual(len(result["selection_issues"]), 4)
        self.assertEqual(len(fx.identity_queries), 5)
        self.assertIs(result["selection_issues"][3]["native_result"], fx.identity_results[3])
        self.assertEqual(result["selection_issues"][3]["native_result"].error, 122)
        self.assertEqual((fx.producers, fx.clones, fx.validations, fx.submissions), (0, 0, 0, 0))
        self.cleaned(fx)

    async def test_native_validation_false_and_original_pending_identity_resume_exactly_once(self):
        rejected = self.fixture([{"value": None}, {"value": 0}])
        rejected.validation.validated = False
        result = await rejected.api.negotiate(0, readback=False)
        self.assertFalse(result["accepted"])
        self.assertIs(result["validation"], rejected.validation)
        self.assertEqual(result["reason"], rejected.validation.reason)
        self.assertEqual((rejected.producers, rejected.clones, rejected.validations, rejected.submissions), (1, 1, 1, 0))
        self.cleaned(rejected)
        fx = self.fixture([{"value": None}, {"value": 0, "pending": True}])
        fx.executor.timeout = .01
        task = asyncio.create_task(fx.api.negotiate(0, readback=False))
        await asyncio.wait_for(fx.entered.wait(), 1)
        pending = await task
        self.assertIsInstance(pending, PendingOperation)
        original_task = pending.task
        query = fx.identity_queries[-1]
        self.assertIs(pending.pending_queries[0], query)
        self.assertEqual(fx.producers, 0)
        self.assertTrue(fx.api._mutation_lock.locked())
        query.ready = True
        result = await pending.resume(1)
        self.assertIs(pending.task, original_task)
        self.assertTrue(original_task.done())
        self.assertTrue(result["accepted"])
        self.assertEqual(len(fx.identity_queries), 2)
        self.assertEqual((fx.producers, fx.clones, fx.validations, fx.submissions), (1, 1, 1, 1))
        self.assertEqual(len([event for event in fx.events if event[0] == "Country.AccessAllInterestGroups"]), 1)
        self.cleaned(fx)

    async def test_uncertain_runtime_cancel_or_borrowed_header_never_continues_into_new_action(self):
        for mode in ("runtime", "cancel", "uncertain_known", "borrowed_header"):
            with self.subTest(mode=mode):
                partial = NS(error=122, value=None, kind=data.Kind.EMPTY, snapshot=b"original partial")
                row = {"value": 9, "partial": partial}
                if mode == "runtime": row["failure"] = RuntimeError("identity outcome uncertain")
                elif mode == "cancel": row["failure"] = asyncio.CancelledError("original identity canceled")
                elif mode == "uncertain_known": row["uncertain_known_error"] = True
                else: row["header"] = ".?AU?$SAnyType@PEAVCDataInterestGroup@@@PdxAnyImpl@@"
                fx = self.fixture([row, {"value": 0}])
                if mode == "uncertain_known":
                    result = await fx.api.negotiate(0, readback=False)
                    self.assertEqual(result["status"], "native_error")
                    self.assertIsNone(result["accepted"])
                    self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
                else:
                    expected = asyncio.CancelledError if mode == "cancel" else RuntimeError
                    with self.assertRaises(expected): await fx.api.negotiate(0, readback=False)
                self.assertEqual((fx.producers, fx.clones, fx.validations, fx.submissions), (0, 0, 0, 0))
                self.assertEqual(fx.releases, [])
                run, = self.runs(fx)
                self.assertTrue(run.values)
                if mode != "borrowed_header":
                    original_query = (fx.identity_queries[0].failure.query
                        if mode == "uncertain_known" else fx.identity_queries[0])
                    self.assertIs(run.uncertain_query, original_query)
                    self.assertTrue(run.retain_unknown)
                    if mode == "uncertain_known":
                        issue, = result["selection_issues"]
                        self.assertIs(issue["query"], original_query)
                        self.assertIs(issue["partial_result"], partial)
                        self.assertIs(issue["error"], fx.identity_queries[0].failure)
                self.assertLessEqual(len(fx.identity_queries), 1)
        for invalid in (True, -1, 0xffffffff, "0", None):
            fx = self.fixture([{"value": 0}])
            with self.assertRaises(ValueError): await fx.api.negotiate(invalid, readback=False)
            self.assertEqual(fx.events, [])


if __name__ == "__main__": unittest.main()
