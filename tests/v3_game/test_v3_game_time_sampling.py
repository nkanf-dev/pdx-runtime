"""Directed CPU evidence for cooperative normal-time UI sampling."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_data as data, v3_objects as objects
from pdx_runtime._sampling import SamplingStopped
from pdx_runtime.v3_game.time import _SamplingGate
from tests.v3_game import test_v3_game_time as fixtures


def copied(value, count=1):
    raw=value.encode() if isinstance(value,str) else b""
    return N(error=0,value=value,source_count=count,source_produced=True,
        result=N(kind=data.Kind.STRING if isinstance(value,str) else data.Kind.BOOL,
                 snapshot=raw,snapshot_bytes=len(raw),total=len(raw)))


class CountryWars:
    """Stand in for the country producer; admission still uses the real gate."""
    def __init__(self, ctx):
        self._ctx = ctx

    async def wars(self, *, start=0, limit=64):
        return await self._ctx.query("military.country_wars", lambda: N(start=start, limit=limit))


def war_directory(*, complete=True):
    return {"wars": [], "country_id": 0, "source": "Country.AccessWars",
        "scope": "selected_country_wars", "world": 77, "status": "observed" if complete else "partial",
        "start": 0, "next": 0, "has_more": not complete, "source_count": 0 if complete else 65,
        "enumeration_complete": complete, "atomic_snapshot": False, "details": []}


class Tests(IsolatedAsyncioTestCase):
    def setUp(self):
        fixture=fixtures.Tests();fixture.addCleanup=self.addCleanup;fixture.setUp()
        self.ctx,self.api=fixture.ctx,fixture.api
        self.ctx.advance=False;self.ctx.adapters.update(data="data",objects="objects")
        self.ctx.country_id=AsyncMock(return_value=0)
        self.ctx.domain=lambda name: CountryWars(self.ctx) if name == "military" else None
        del self.api._perception  # Real NotificationsAPI + real native UI source adapter.
        self.native_labels=[];self.field_labels=[]
        self.original_query=self.ctx.query

    async def asyncTearDown(self):
        if self.api._run is not None and not self.api._run._task.done():
            await self.api._run.interrupt()

    def install_fields(self, mode):
        started,release=asyncio.Event(),asyncio.Event()
        async def query(label,factory,**kwargs):
            if not label.startswith("ui."):
                return await self.original_query(label,factory)
            self.native_labels.append(label)
            if label.endswith(".count"):
                return N(error=0,source_produced=True,count_valid=True,source_count=0 if self.ctx.paused else 1)
            self.field_labels.append(label)
            if len(self.field_labels)==1:
                if mode=="interrupt":
                    owner=object();self.ctx.pending_queries.append(owner);started.set()
                    await release.wait();self.ctx.pending_queries.remove(owner)
                elif mode=="date":self.ctx.current=datetime(1836,1,8)
                elif mode=="paused":self.ctx.paused=True
                elif mode=="world_interrupt":
                    self.ctx.world=88;self.api._capture_gate.signal.set()
            return copied("Original title" if label.endswith("title") else False)
        self.ctx.query=query
        return started,release

    async def test_interrupt_during_first_pending_field_settles_it_and_submits_no_second_field(self):
        started,release=self.install_fields("interrupt")
        handle=await self.api.run_for(days=7,notification_sources=("message_feed",))
        await started.wait();task=handle._task
        interrupted=asyncio.create_task(handle.interrupt());await asyncio.sleep(0)
        self.assertEqual(len(self.ctx.pending_queries),1)
        self.assertFalse(interrupted.done());self.assertNotIn("time.pause",self.ctx.queries)
        refreshes=self.ctx.refreshes
        release.set();notice=await interrupted
        self.assertEqual(notice["reason"],"interrupted")
        self.assertEqual(len(self.field_labels),1)
        self.assertIs(handle._task,task)
        self.assertEqual(self.ctx.pending_queries,[])
        self.assertEqual(self.ctx.queries.count("time.pause"),1)
        # Only the pause acknowledgement refresh follows the settled field.
        self.assertEqual(self.ctx.refreshes,refreshes+1)
        self.assertEqual(notice["perception"]["sources"][0]["status"],"partial")
        self.assertEqual(notice["perception"]["receipts"][0]["raw_text"]["title"],"Original title")

    async def test_target_reached_in_first_field_stops_before_second_field(self):
        self.install_fields("date")
        handle=await self.api.run_for(days=7,notification_sources=("message_feed",))
        notice=await handle.wait()
        self.assertEqual(notice["reason"],"date")
        self.assertEqual(len(self.field_labels),1)
        self.assertEqual(notice["date"],"1836-01-08")
        self.assertEqual(self.ctx.queries.count("time.pause"),1)
        self.assertEqual(notice["perception"]["sources"][0]["status"],"partial")

    async def test_native_pause_during_first_field_stops_before_second_without_repause(self):
        self.install_fields("paused")
        handle=await self.api.run_for(days=7,notification_sources=("message_feed",))
        notice=await handle.wait()
        self.assertEqual(notice["reason"],"paused")
        self.assertEqual(len(self.field_labels),1)
        self.assertNotIn("time.pause",self.ctx.queries)
        self.assertTrue(notice["paused"])

    async def test_already_reached_target_does_not_dispatch_any_ui_source(self):
        self.install_fields("date")
        notice=await self.api.run_for(days=0,notification_sources=("message_feed",))
        self.assertEqual(notice["reason"],"date")
        self.assertEqual(self.native_labels,[])
        source=notice["perception"]["sources"][0]
        self.assertEqual(source["status"],"partial")
        self.assertEqual(source["sampling_status"],"not_sampled")
        self.assertIsNone(source["count"])
        self.assertNotIn("time.resume",self.ctx.queries)

    async def test_known_native_pause_before_sampling_dispatches_no_running_ui(self):
        self.install_fields("paused")
        async def resume(speed):
            self.ctx.paused=True
            return self.api._public(await self.ctx.refresh())
        self.api._resume_native=resume
        handle=await self.api.run_for(days=7,notification_sources=("message_feed",))
        baseline_count=len(self.native_labels)
        notice=await handle.wait()
        self.assertEqual(notice["reason"],"paused")
        self.assertEqual(len(self.native_labels),baseline_count)
        self.assertEqual(notice["perception"]["sources"][0]["sampling_status"],"not_sampled")

    async def test_security_pages_precede_high_volume_feed_and_unknown_baseline_never_resumes(self):
        state_starts=[]
        async def query(label,factory,**kwargs):
            if label == "military.country_wars":
                request=factory();state_starts.append((label,request.start))
                self.assertEqual(request.limit,64)
                return war_directory(complete=False)
            if label == "notifications.state.plays":
                request=factory();state_starts.append((label,request.start))
                return N(error=0,world=77,domain=objects.Domain.DIPLOMATIC_PLAY,truncated=False,
                         next_index=0,records=())
            if label.startswith("ui."):
                self.native_labels.append(label)
                if label.endswith(".count"):
                    return N(error=0,source_produced=True,count_valid=True,source_count=64)
                return copied("Title" if label.endswith("title") else False,count=64)
            return await self.original_query(label,factory)
        self.ctx.query=query
        def guard(snapshot):return False
        guard.required_ui_sources=()
        guard.required_state_sources=("wars","plays")
        guard.unknown_policy="stop"
        with patch("pdx_runtime.v3_game.notifications.objects.enumerate_objects",
                   side_effect=lambda *a,**k:N(start=k["start_index"])):
            first=await self.api.run_for(days=7,notification_sources=("message_feed",),
                state_sources=("wars","plays"),stop_when=guard)
            second=await self.api.run_for(days=7,notification_sources=("message_feed",),
                state_sources=("wars","plays"),stop_when=guard)
        self.assertEqual(first["reason"],"sampling_incomplete")
        self.assertEqual(second["reason"],"sampling_incomplete")
        self.assertNotIn("time.resume",self.ctx.queries)
        self.assertEqual(state_starts,[("military.country_wars",0),("notifications.state.plays",0)] * 2)
        self.assertLessEqual(first["sampling"]["data_queries"],12)
        self.assertLessEqual(first["sampling"]["clock_queries"],16)
        sources={row["source"]:row for row in first["perception"]["sources"]}
        self.assertFalse(sources["wars"]["enumeration_complete"])
        self.assertEqual(sources["wars"]["page_scope"],"current_country_source_page_only")
        self.assertEqual(sources["wars"]["directory"]["source"],"Country.AccessWars")
        self.assertEqual(sources["wars"]["directory"]["source_count"],65)
        self.assertTrue(sources["plays"]["enumeration_complete"])
        self.assertEqual(sources["message_feed"]["status"],"partial")

    async def test_cold_event_metadata_continues_next_page_instead_of_restarting(self):
        self.ctx._types={};pages=[]
        async def query(label,factory,**kwargs):
            request=factory();pages.append(request.start)
            return N(error=0,rows=[N(name="EventWindow" if request.start==14 else "Other"+str(request.start),
                                    type_id=918 if request.start==14 else request.start)],
                     next=request.start+1,total=50)
        self.ctx.query=query
        observed=await self.api._observe()
        first=_SamplingGate(self.api,asyncio.Event(),datetime(1837,1,1),observed,allow_paused=True)
        with patch("pdx_runtime.v3_data.enumerate_types",side_effect=lambda *a,**k:N(start=k["start"])):
            with self.assertRaises(SamplingStopped):await first.context.type_id("EventWindow")
            second=_SamplingGate(self.api,asyncio.Event(),datetime(1837,1,1),observed,allow_paused=True)
            self.assertEqual(await second.context.type_id("EventWindow"),918)
        self.assertEqual(pages,list(range(15)))
        self.assertEqual(first.data_queries,12)
        self.assertEqual(second.data_queries,3)

    def install_partial_feed(self):
        async def query(label,factory,**kwargs):
            if label.startswith("ui."):
                self.native_labels.append(label)
                if label.endswith(".count"):
                    return N(error=0,source_produced=True,count_valid=True,source_count=64)
                return copied("Native text" if label.endswith("title") else False,count=64)
            return await self.original_query(label,factory)
        self.ctx.query=query

    async def test_bare_false_callback_on_partial_ui_has_no_implicit_all_ui_dependency(self):
        self.install_partial_feed()
        handle=await self.api.run_for(days=7,notification_sources=("message_feed",),
            stop_when=lambda snapshot:False)
        self.assertIsInstance(handle,fixtures.TimeRun)
        baseline=handle.perception
        self.assertEqual(baseline["sources"][0]["status"],"partial")
        self.assertFalse(baseline["guard_dependencies"]["declared"])
        self.assertEqual(baseline["guard_dependencies"]["missing"],[])
        self.assertFalse(baseline["guard_dependencies"]["safety_certified"])
        await handle.interrupt()

    async def test_declared_missing_dependency_stops_incomplete_without_auto_read_or_resume(self):
        self.install_fields("paused")
        def guard(snapshot):return False
        guard.required_ui_sources=("toast",)
        guard.required_state_sources=()
        guard.unknown_policy="stop"
        notice=await self.api.run_for(days=7,notification_sources=("message_feed",),stop_when=guard)
        self.assertEqual(notice["reason"],"sampling_incomplete")
        self.assertNotIn("time.resume",self.ctx.queries)
        missing=notice["perception"]["guard_dependencies"]["missing"]
        self.assertEqual(missing[0]["source"],"toast")
        self.assertEqual(missing[0]["status"],"not_requested")
        self.assertFalse(any("toast" in label for label in self.native_labels))

    async def test_state_observation_option_does_not_implicitly_declare_guard_dependencies(self):
        self.api._perception=AsyncMock(return_value={"cursor":None,"changes":[],"receipts":[],
            "sources":[{"source":"wars","status":"partial","sampled":True,"enumeration_complete":False,
                        "reason":"Only a current copied page was observed."}]})
        handle=await self.api.run_for(days=7,state_sources=("wars",),stop_when=lambda snapshot:False)
        self.assertIsInstance(handle,fixtures.TimeRun)
        self.assertFalse(handle.perception["guard_dependencies"]["declared"])
        await handle.interrupt()

    async def test_small_declared_modal_dependency_can_run_with_unrelated_partial_feed(self):
        self.install_partial_feed()
        def guard(snapshot):return False
        guard.required_ui_sources=("event_windows",)
        guard.required_state_sources=()
        guard.unknown_policy="stop"
        handle=await self.api.run_for(days=7,notification_sources=("event_windows","message_feed"),stop_when=guard)
        self.assertIsInstance(handle,fixtures.TimeRun)
        sources={row["source"]:row for row in handle.perception["sources"]}
        self.assertEqual(sources["event_windows"]["status"],"available")
        self.assertEqual(sources["event_windows"]["count"],0)
        self.assertEqual(sources["message_feed"]["status"],"partial")
        self.assertTrue(handle.perception["guard_dependencies"]["sampled"])
        self.assertFalse(handle.perception["guard_dependencies"]["safety_certified"])
        await handle.interrupt()

    async def test_known_world_change_plus_interrupt_never_pauses_replacement_world(self):
        self.install_fields("world_interrupt")
        handle=await self.api.run_for(days=7,notification_sources=("message_feed",))
        notice=await handle.wait()
        self.assertEqual(notice["reason"],"world_changed")
        self.assertEqual(len(self.field_labels),1)
        self.assertNotIn("time.pause",self.ctx.queries)

    async def test_declared_safe_bundle_warms_cold_popup_metadata_across_explicit_paused_attempts(self):
        self.ctx._types={};del self.api._events
        pages=[]
        async def query(label,factory,**kwargs):
            if label in ("metadata.types","ui.metadata.types"):
                request=factory();pages.append((label,request.start))
                rows=[N(name="EventWindow",type_id=918)] if request.start==0 else []
                if request.start==10:rows.append(N(name="PlayerMessageItem",type_id=1000))
                return N(error=0,rows=rows,next=request.start+1,total=100)
            if label=="events.pending.count":return N(error=0,source_stages=3,root_count=0)
            if label=="ui.message_popups.count":return N(error=0,source_stages=3,root_count=0)
            if label == "military.country_wars":
                request=factory()
                self.assertEqual((request.start,request.limit),(0,64))
                return war_directory()
            if label == "notifications.state.plays":
                return N(error=0,world=77,domain=objects.Domain.DIPLOMATIC_PLAY,truncated=False,next_index=0,records=())
            return await self.original_query(label,factory)
        self.ctx.query=query
        def guard(snapshot):return False
        guard.required_ui_sources=("event_windows","message_popups")
        guard.required_state_sources=("wars","plays")
        guard.unknown_policy="stop"
        with patch("pdx_runtime.v3_data.enumerate_types",side_effect=lambda *a,**k:N(start=k["start"])), \
             patch("pdx_runtime.v3_ui._popup_profile",return_value={"supported":True,"reason":None}):
            first=await self.api.run_for(days=7,notification_sources=guard.required_ui_sources,
                state_sources=guard.required_state_sources,stop_when=guard)
            self.assertEqual(first["reason"],"sampling_incomplete")
            self.assertTrue(self.ctx.paused)
            self.assertNotIn("time.resume",self.ctx.queries)
            first_page_count=len([row for row in pages if row[0]=="ui.metadata.types"])
            second=await self.api.run_for(days=7,notification_sources=guard.required_ui_sources,
                state_sources=guard.required_state_sources,stop_when=guard)
        self.assertIsInstance(second,fixtures.TimeRun)
        self.assertTrue(second.perception["guard_dependencies"]["sampled"])
        metadata=[start for label,start in pages if label=="ui.metadata.types"]
        self.assertEqual(metadata,list(range(11)))
        self.assertGreater(len(metadata),first_page_count)
        self.assertLessEqual(first["sampling"]["data_queries"],12)
        await second.interrupt()
