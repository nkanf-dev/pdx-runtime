"""Technology country-metric CPU contract: no engine or native process is opened."""
from __future__ import annotations

from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime.v3_game.research import ResearchAPI
from pdx_runtime.v3_game import _read as support


class Context:
    def __init__(self):
        self.performed = []

    async def perform(self, label, work):
        self.performed.append(label)
        return await work()


class ResearchCountryMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ctx = Context()
        self.api = ResearchAPI(self.ctx)
        self.read_fields = AsyncMock()
        self.patch = patch.object(support, "read_fields", new=self.read_fields)
        self.patch.start()
        self.addCleanup(self.patch.stop)


    async def test_spread_never_mistakes_localized_description_for_tech_keys(self):
        self.read_fields.return_value = {
            "country_id": 3,
            "values": {"currently_spreading_description": "<native>Machine parts</native>",
                       "base_weekly_spread": Decimal("12.50000"),
                       "min_weekly_spread": Decimal("10.00000"),
                       "max_weekly_spread": Decimal("25.00000"),
                       "weekly_spread_description": "<native>bonus</native>",
                       "weekly_spread_breakdown": "<native>breakdown</native>"},
            "observations": {"currently_spreading_description": {"complete": True}},
            "reasons": {}, "complete": True, "snapshot": "single native projection page",
        }
        result = await self.api.spread()
        self.assertEqual(self.ctx.performed, ["research.spread"])
        ctx, fields = self.read_fields.await_args.args
        self.assertIs(ctx, self.ctx)
        self.assertEqual(self.read_fields.await_args.kwargs, {"label": "research.spread"})
        self.assertEqual([(f.name, f.expression, f.kind) for f in fields], [
            ("currently_spreading_description", "Country.GetCurrentlySpreadingTechsDesc", "text"),
            ("base_weekly_spread", "Country.GetBaseWeeklyTechnologySpread", "fixed"),
            ("min_weekly_spread", "Country.GetMinWeeklyTechnologySpread", "fixed"),
            ("max_weekly_spread", "Country.GetMaxWeeklyTechnologySpread", "fixed"),
            ("weekly_spread_description", "Country.GetWeeklyTechnologySpreadDesc", "text"),
            ("weekly_spread_breakdown", "Country.GetWeeklyTechnologySpreadBreakdown", "text"),
        ])
        self.assertEqual(result["currently_spreading_description"], "<native>Machine parts</native>")
        self.assertEqual(result["base_weekly_spread"], Decimal("12.50000"))
        self.assertTrue(result["observation_complete"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["tech_identification_complete"])
        self.assertIn("Technology argument", result["tech_identification_reason"])
        self.assertNotIn("technology_keys", result)
        self.assertNotIn("category", result)


class ResearchProjectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Real shared helper, with the native projection callback replaced by a page."""

    async def test_innovation_uses_one_page_and_one_country_lookup(self):
        from pdx_runtime import v3_data as data
        from pdx_runtime.v3_game import _read as support
        ctx = Context()
        ctx.world = 0x70000
        ctx.adapters = {"data": "data-adapter"}
        ctx.refresh = AsyncMock()
        ctx.country_id = AsyncMock(return_value=0x1000007)
        ctx.type_id = AsyncMock(return_value=83)
        page = data.ProjectionPage(error=0, world=ctx.world, fields_count=7,
            start=0, execution_thread_id=24, source_count=1, count_valid=True,
            row_count=1, cells=tuple(
                data.ProjectionCell(0, idx, 0, data.Kind.FIXED, 82, bits, b"", 0)
                for idx, bits in enumerate((10000000, 8000000, 9000000, 8500000))) + tuple(
                data.ProjectionCell(0, idx, 0, data.Kind.STRING, 0, 0, text, len(text))
                for idx, text in enumerate((b"rich innovation", b"rich cap", b"rich speed"), 4)))
        with patch.object(support, "_projection_supported", new=AsyncMock(return_value=True)), \
             patch.object(support, "_projection_page", new=AsyncMock(return_value=page)) as projected:
            observed = await ResearchAPI(ctx).innovation()
        ctx.refresh.assert_awaited_once()
        ctx.country_id.assert_awaited_once()
        ctx.type_id.assert_awaited_once_with("Country")
        projected.assert_awaited_once()
        args = projected.await_args.args
        self.assertIs(args[0], ctx)
        self.assertEqual(len(args[2]), 7)
        self.assertEqual(args[3][0][0], data.ObjectBinding.country(83, 0x1000007))
        self.assertEqual(observed["raw_innovation"], Decimal("100.00000"))
        self.assertEqual(observed["innovation"], Decimal("80.00000"))
        self.assertEqual(observed["max_innovation"], Decimal("90.00000"))
        self.assertEqual(observed["research_speed"], Decimal("85.00000"))
        self.assertEqual(observed["innovation_description"], "rich innovation")
        self.assertEqual(observed["observations"]["raw_innovation"]["raw_bits"], 10000000)
        self.assertTrue(observed["complete"])


class ResearchPrerequisiteEnqueueTests(unittest.IsolatedAsyncioTestCase):
    """Post-cut3 intent wiring; native execution is replaced by actual helper seams."""

    def context(self):
        from types import SimpleNamespace as NS
        from pdx_runtime.query_executor import Diagnostics
        ctx=Context();ctx.native=NS(pid=123);ctx.process=NS(pid=123)
        ctx.diagnostics=Diagnostics()
        ctx.adapters={name:name+"-adapter" for name in ("data","commands","objects","access","call")}
        return ctx

    def action(self):
        from types import SimpleNamespace as NS
        ctx=self.context();api=ResearchAPI(ctx)
        helper=NS(prepare_enqueue=AsyncMock(),prepare_dequeue=AsyncMock(),
            submit_prepared=AsyncMock(return_value=NS(error=0,accepted=True)),
            observe_result=AsyncMock(return_value={"keys":("native_prerequisite","locked_target"),
                "entries":({"key":"native_prerequisite"},{"key":"locked_target"})}))
        from pdx_runtime.v3_game.research import _queue
        ctx.world, ctx.image_base = 0x7000, 0x140000000
        scan = _queue.new_plan(ctx, 0xAF000007)
        plan = _queue.new_plan(ctx, 0xAF000007)
        plan.update(before={"current_after":{"present":False}}, validation=NS(validated=True,reason=""),
            owner=NS(attempt=NS(outcome="consumed")), target_is_current_after=False,
            queue_changed=True, target_selection_changed=True)
        async def prepare(actual, **kwargs):
            self.assertIs(actual, plan)
            return actual
        helper.prepare_enqueue.side_effect = prepare
        api._new_plan=AsyncMock(side_effect=[(helper,scan),(helper,plan)]);api._cleanup=AsyncMock()
        api._identity=AsyncMock(side_effect=AssertionError("no start/current gate for enqueue"))
        api._candidates=AsyncMock(return_value=[{"public":{"key":"locked_target","researchable":False,
            "can_start":False,"is_current":False},"expression":"Country.AccessResearchableTechnologiesProduction","index":9}])
        return ctx,api,helper,scan,plan

    async def test_public_explicit_native_intent_key_validation_and_original_return(self):
        ctx=self.context();api=ResearchAPI(ctx);original={"accepted":None,"status":"pending-or-native"}
        api._change=AsyncMock(return_value=original)
        result=await api.enqueue_prerequisites("locked_target")
        self.assertIs(result,original)
        self.assertEqual(ctx.performed,["research.enqueue_prerequisites"])
        api._change.assert_awaited_once_with("enqueue","locked_target",with_unlocks=True)
        for invalid in ("", " key", "key\0", None):
            with self.assertRaises(ValueError):await api.enqueue_prerequisites(invalid)
        self.assertEqual(api._change.await_count,1)

    async def test_locked_target_one_existing_prepare_submit_no_host_prerequisite_graph(self):
        ctx,api,helper,scan,plan=self.action()
        result=await api.enqueue_prerequisites("locked_target")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["queue"],[{"key":"native_prerequisite","position":1},{"key":"locked_target","position":2}])
        self.assertFalse(result["is_current"])
        api._identity.assert_not_awaited();api._candidates.assert_awaited_once()
        helper.prepare_enqueue.assert_awaited_once()
        kwargs=helper.prepare_enqueue.await_args.kwargs
        self.assertIs(kwargs["with_unlocks"],True)
        self.assertIs(helper.prepare_enqueue.await_args.args[0], plan)
        self.assertEqual((plan["actor_id"],kwargs["index"],kwargs["expected_key"]),(0xAF000007,9,"locked_target"))
        helper.prepare_dequeue.assert_not_awaited()
        helper.submit_prepared.assert_awaited_once_with(plan,enqueue=True)
        self.assertIs(result["native_result"],helper.submit_prepared.return_value)
        self.assertEqual(api._cleanup.await_count,2)

    async def test_ordinary_enqueue_uses_native_default_without_unlock_keyword(self):
        ctx,api,helper,scan,plan=self.action()
        await api.enqueue("locked_target")
        self.assertNotIn("with_unlocks",helper.prepare_enqueue.await_args.kwargs)
        self.assertEqual(ctx.performed,["research.enqueue"])

    async def test_native_rejection_no_submit_and_unknown_receiver_no_readback_or_retry(self):
        from types import SimpleNamespace as NS
        ctx,api,helper,scan,plan=self.action();plan["validation"]=NS(validated=False,reason="native prerequisite rule")
        with self.assertRaisesRegex(ValueError,"native prerequisite rule"):
            await api.enqueue_prerequisites("locked_target")
        helper.submit_prepared.assert_not_awaited()
        ctx,api,helper,scan,plan=self.action();native=NS(error=31,accepted=True)
        helper.submit_prepared.return_value=native;plan["owner"].attempt.outcome="unknown"
        result=await api.enqueue_prerequisites("locked_target")
        self.assertIs(result["native_result"],native);self.assertIsNone(result["accepted"])
        helper.observe_result.assert_not_awaited();helper.submit_prepared.assert_awaited_once()
        helper.prepare_enqueue.assert_awaited_once()

    async def test_accepted_receiver_readback_failure_does_not_erase_native_acceptance(self):
        ctx,api,helper,scan,plan=self.action();failure=OSError("native queue readback interrupted")
        helper.observe_result.side_effect=failure
        result=await api.enqueue_prerequisites("locked_target")
        self.assertTrue(result["accepted"]);self.assertIs(result["observation_error"],failure)
        self.assertIs(result["native_result"],helper.submit_prepared.return_value)
        helper.submit_prepared.assert_awaited_once();helper.prepare_enqueue.assert_awaited_once()
