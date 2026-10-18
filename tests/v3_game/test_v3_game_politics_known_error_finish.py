"""CPU-only legacy _run known-result completion boundary.

Real PoliticsAPI._run, canonical _Run.capture/call/finish and the original query
executor execute against native-shaped State.AccessDecrees result fixtures.
No game process, DLL or native effect is created.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data
from pdx_runtime.query_executor import RetainedOwners, SerializedQueryExecutor
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod


class Query:
    serial = 0
    def __init__(self, result, *, adapter=0xA000):
        Query.serial += 1
        self.request_id, self.result, self.adapter = Query.serial, result, adapter
    def read(self): return self.result


class KnownErrorFinishTests(unittest.IsolatedAsyncioTestCase):
    source_handle, source_adapter = 901, 0xA000

    def fixture(self):
        fx = NS(releases=[], runs=[], errors=[], source_queries=[])
        fx.ctx = NS(native=object(), process=object(), world=700,
                    adapters={"data": 0xB000, "commands": "unused-commands"},
                    diagnostics=[], owner_pool=RetainedOwners())
        fx.executor = SerializedQueryExecutor(timeout=1, poll_interval=.001,
            diagnostics=fx.ctx.diagnostics, owners=fx.ctx.owner_pool)
        fx.ctx.query, fx.ctx.perform = fx.executor.query, fx.executor.perform
        fx.ctx._remember = fx.executor._remember
        async def refresh(): return None
        fx.ctx.refresh = refresh
        fx.api = politics.PoliticsAPI(fx.ctx)
        fx.source = NS(error=0, action=data.Action.EVALUATE,
            value_handle=self.source_handle, type_id=-1, native_type_name="original Creation24",
            value=data.NativeList(self.source_handle, 0, b"original source bytes", "original Creation24"))
        fx.native_error = NS(error=50, action=data.Action.LIST_COUNT,
                             value_handle=0, total=7)
        fx.partial = {"native_count_prefix": object()}
        def evaluate(native, expression, bindings, **kwargs):
            self.assertIs(native, fx.ctx.native)
            self.assertEqual(expression, "State.AccessDecrees")
            state, = bindings
            self.assertEqual((state.domain, state.object), ("State", 0x82000002))
            query = Query(fx.source, adapter=self.source_adapter)
            fx.source_queries.append(query)
            return query
        def count(native, handle, **kwargs):
            self.assertEqual(handle, self.source_handle)
            return Query(fx.native_error, adapter=self.source_adapter)
        def release(native, handle, **kwargs):
            fx.releases.append((handle, kwargs["adapter"], kwargs["process"]))
            return Query(NS(error=0, action=data.Action.RELEASE, value_handle=0), adapter=self.source_adapter)
        for patcher in (patch.object(data, "evaluate", evaluate),
                        patch.object(data, "list_count", count),
                        patch.object(data, "release", release)):
            patcher.start()
            self.addCleanup(patcher.stop)
        async def acquire(run):
            fx.runs.append(run)
            state = data.ObjectBinding.state(30, 0x82000002)
            result = await run.call("State.AccessDecrees", lambda: data.evaluate(
                run.native, "State.AccessDecrees", (state,),
                adapter=run.adapters["data"], process=run.process))
            self.assertIs(result, fx.source)
            self.assertEqual(run.values, [self.source_handle])
            self.assertEqual(run.value_adapters[self.source_handle], self.source_adapter)
        async def fail_count(run):
            try:
                await run.call("State.AccessDecrees.count", lambda: data.list_count(
                    run.native, self.source_handle, adapter=self.source_adapter, process=run.process))
            except run_mod.KnownResultError as error:
                error.partial_result = fx.partial
                fx.errors.append(error)
                raise
        fx.acquire, fx.fail_count = acquire, fail_count
        return fx

    async def test_known_native_count_error_finishes_original_any_once_and_preserves_same_error(self):
        fx = self.fixture()
        async def operation(run):
            await fx.acquire(run)
            await fx.fail_count(run)
        with self.assertRaises(run_mod.KnownResultError) as raised:
            await fx.api._run("known_decree_count", operation)
        error, = fx.errors
        run, = fx.runs
        self.assertIs(raised.exception, error)
        self.assertIs(error.result, fx.native_error)
        self.assertIs(error.partial_result, fx.partial)
        self.assertEqual(str(error), "State.AccessDecrees.count: native error 50")
        self.assertIs(error.observed_result, run.public_result)
        self.assertIs(error.observed_result["native_result"], fx.native_error)
        self.assertIs(error.observed_result["partial_result"], fx.partial)
        self.assertEqual(error.cleanup["status"], "complete")
        self.assertEqual(run.status, "complete")
        self.assertEqual(run.values, [])
        self.assertEqual(run.value_adapters, {})
        self.assertNotIn(run, fx.ctx.owner_pool.snapshot())
        self.assertEqual(fx.releases, [(self.source_handle, self.source_adapter, fx.ctx.process)])
        self.assertEqual(sum(stage == "release" for stage, _ in run.operations), 1)
        self.assertIs(run.operations[0][1], fx.source)
        self.assertIs(run.operations[1][1], fx.native_error)
        self.assertEqual(fx.executor.pending_queries, [])

    async def test_known_error_with_unknown_or_unsettled_owner_retains_original_graph(self):
        for mode in ("retain_unknown", "unsettled_owner"):
            with self.subTest(mode=mode):
                fx = self.fixture()
                uncertain = Query(None, adapter=self.source_adapter)
                owner = NS(command=0x3000, pending=uncertain, attempt=NS(outcome="pending"))
                async def operation(run):
                    await fx.acquire(run)
                    if mode == "retain_unknown":
                        run.retain_unknown, run.uncertain_query = True, uncertain
                    else:
                        run.owners.append(owner)
                    await fx.fail_count(run)
                with self.assertRaises(run_mod.KnownResultError) as raised:
                    await fx.api._run("known_decree_count_retained", operation)
                error, = fx.errors
                run, = fx.runs
                self.assertIs(raised.exception, error)
                self.assertIs(error.result, fx.native_error)
                self.assertIs(error.partial_result, fx.partial)
                self.assertIs(error.observed_result, run.public_result)
                self.assertEqual(run.status, "retained_pending_or_unknown")
                self.assertIs(error.cleanup, run.public_result["cleanup"])
                self.assertEqual(error.cleanup["status"], run.status)
                self.assertEqual(fx.releases, [])
                self.assertEqual(run.values, [self.source_handle])
                self.assertEqual(run.value_adapters[self.source_handle], self.source_adapter)
                self.assertIn(run, fx.ctx.owner_pool.snapshot())
                if mode == "retain_unknown":
                    self.assertIs(run.uncertain_query, uncertain)
                else:
                    self.assertIs(run.owners[0], owner)
                    self.assertIs(owner.pending, uncertain)
                    self.assertEqual(owner.command, 0x3000)

    async def test_existing_accepted_primary_dict_is_finished_in_place_not_replaced_by_error(self):
        fx = self.fixture()
        submission = NS(error=0, accepted=True, command=0)
        primary = {"status": "accepted", "accepted": True,
                   "native_result": submission, "submission": submission, "completed": False}
        async def operation(run):
            await fx.acquire(run)
            run.public_result = primary
            await fx.fail_count(run)
        with self.assertRaises(run_mod.KnownResultError) as raised:
            await fx.api._run("known_decree_count_after_acceptance", operation)
        error, = fx.errors
        run, = fx.runs
        self.assertIs(raised.exception, error)
        self.assertIs(error.result, fx.native_error)
        self.assertIs(error.partial_result, fx.partial)
        self.assertIs(error.observed_result, primary)
        self.assertIs(run.public_result, primary)
        self.assertTrue(primary["accepted"])
        self.assertEqual(primary["status"], "accepted")
        self.assertIs(primary["native_result"], submission)
        self.assertIs(primary["submission"], submission)
        self.assertFalse(primary["completed"])
        self.assertNotIn("partial_result", primary)
        self.assertEqual(error.cleanup["status"], "complete")
        self.assertEqual(run.status, "complete")
        self.assertEqual(fx.releases, [(self.source_handle, self.source_adapter, fx.ctx.process)])

    async def test_other_runtime_error_and_cancellation_do_not_trigger_known_result_finisher(self):
        for failure in (RuntimeError("arbitrary operation failure"), asyncio.CancelledError("original cancellation")):
            with self.subTest(error=type(failure).__name__):
                fx = self.fixture()
                async def operation(run):
                    await fx.acquire(run)
                    raise failure
                with self.assertRaises(type(failure)) as raised:
                    await fx.api._run("other_decree_count_failure", operation)
                self.assertIs(raised.exception, failure)
                run, = fx.runs
                self.assertEqual(fx.releases, [])
                self.assertEqual(run.values, [self.source_handle])
                self.assertIn(run, fx.ctx.owner_pool.snapshot())
                self.assertNotEqual(run.status, "complete")
                self.assertFalse(hasattr(failure, "cleanup"))
                self.assertFalse(hasattr(failure, "observed_result"))


if __name__ == "__main__": unittest.main()
