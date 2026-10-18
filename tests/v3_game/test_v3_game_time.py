"""CPU normal-clock and original direct-player duck-protocol tests."""
import asyncio
from datetime import datetime,timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock,patch
from pdx_runtime.v3_game.time import TimeAPI,TimeRun


def atomic(path,value):
    path.write_text(json.dumps(value),encoding="utf-8")


class LegacyPlayer:
    # Exact mature stop_watch/execute boundary, not a second executor.
    def __init__(self,folder):
        self.interrupt=asyncio.Event();self.watch=None;self.watch_sequence=0
        self.notices={};self.folder=Path(folder)
    async def stop_watch(self):
        if self.watch is not None and not self.watch.done():
            self.interrupt.set();await self.watch
        self.interrupt.clear()
    async def execute(self,call):
        await self.stop_watch();return await call()


class Context:
    def __init__(self):
        self.world=77;self.interface_thread_id=8;self.image_base=1000;self.native=object();self.process=object()
        self.adapters={"lifecycle":"life"};self.diagnostics=[];self.session=N(namespace={})
        self.paused=True;self.speed=2;self.current=datetime(1836,1,1);self.advance=True
        self.queries=[];self.pending_queries=[];self.refreshes=0;self.block_next=False
        self.started=asyncio.Event();self.release=asyncio.Event();self.blocked_completions=0
    async def perform(self,label,body):return await body()
    async def refresh(self):
        self.refreshes+=1
        if self.block_next:
            self.block_next=False;q=object();self.pending_queries.append(q);self.started.set()
            await self.release.wait();self.pending_queries.remove(q);self.blocked_completions+=1
        if not self.paused and self.advance:self.current+=timedelta(hours=6)
        self.world=self.world
        return N(error=0,world=self.world,transition_pending=False,paused=self.paused,speed=self.speed,
            displayed_speed=0 if self.paused else self.speed+1,
            date_parts=(self.current.year,self.current.month,self.current.day,self.current.hour))
    async def query(self,label,factory):
        self.queries.append(label);operation,value=factory()
        if operation=="pause":self.paused=value
        elif operation=="speed":self.speed=value
        return N(error=0)


class Tests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context();self.api=TimeAPI(self.ctx);self.api._events=AsyncMock(return_value=[])
        self.api._perception=AsyncMock(return_value={"cursor":None,"changes":[],"receipts":[],"sources":[]})
        self.pauser=patch("pdx_runtime.v3_game.time.life.set_paused",side_effect=lambda *a,**k:("pause",a[1]))
        self.speeder=patch("pdx_runtime.v3_game.time.life.set_speed",side_effect=lambda *a,**k:("speed",a[1]))
        self.forbid_date=patch("pdx_runtime.v3_game.time.life.set_date",side_effect=AssertionError("No SET_DATE"))
        self.forbid_step=patch("pdx_runtime.v3_time_step.submit",side_effect=AssertionError("No ordinary Step"))
        for p in (self.pauser,self.speeder,self.forbid_date,self.forbid_step):p.start();self.addCleanup(p.stop)
    async def asyncTearDown(self):
        if self.api._run is not None and not self.api._run._task.done():await self.api._run.interrupt()
    async def test_pause_resume_and_observe_use_displayed_speed(self):
        observed=await self.api.observe();self.assertTrue(observed["paused"])
        observed=await self.api.resume(speed=3);self.assertFalse(observed["paused"]);self.assertEqual(observed["speed"],3)
        self.assertEqual(self.ctx.speed,2)
        observed=await self.api.pause();self.assertTrue(observed["paused"])
        with self.assertRaises(ValueError):await self.api.resume(speed=6)
    async def test_normal_run_reaches_date_and_pauses_without_step_or_date_write(self):
        handle=await self.api.run_for(days=1)
        self.assertIsInstance(handle,TimeRun);self.assertNotIn("watch",handle)
        notice=await handle.wait();self.assertEqual(notice["reason"],"date");self.assertTrue(notice["paused"])
        self.assertGreaterEqual(self.ctx.current,datetime(1836,1,2))
    async def test_event_aware_wait_returns_readable_event_without_choosing(self):
        calls=0
        async def events():
            nonlocal calls
            calls+=1
            return [] if calls==1 else [{"id":"event-1","title":"Actual event","options":[]}]
        self.api._events=events
        handle=await self.api.run_for(days=90);notice=await handle.wait()
        self.assertEqual(notice["reason"],"event");self.assertEqual(notice["events"][0]["title"],"Actual event")
        self.assertTrue(notice["paused"])
    async def test_existing_event_does_not_resume_or_choose(self):
        self.api._events=AsyncMock(return_value=[{"id":"event-1","title":"Existing"}])
        notice=await self.api.run_for(days=7)
        self.assertEqual(notice["reason"],"event");self.assertNotIn("time.resume",self.ctx.queries)
    async def test_interrupt_retains_original_background_task(self):
        self.ctx.advance=False;handle=await self.api.run_for(days=7)
        task=handle._task;notice=await handle.interrupt()
        self.assertIs(task,handle._task);self.assertEqual(notice["reason"],"interrupted");self.assertTrue(notice["paused"])
    async def test_cancel_waiter_does_not_cancel_native_clock_task(self):
        self.ctx.advance=False;handle=await self.api.run_for(days=7)
        waiter=asyncio.create_task(handle.wait());await asyncio.sleep(0);waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):await waiter
        self.assertFalse(handle._task.cancelled())
        await handle.interrupt()
    async def test_run_until_accepts_date_and_already_reached_does_not_unpause(self):
        result=await self.api.run_until("1836-01-01")
        self.assertEqual(result["reason"],"date");self.assertNotIn("time.resume",self.ctx.queries)
        with self.assertRaises(ValueError):await self.api.run_for(days=-1)
    async def test_legacy_cli_follows_existing_notice_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            player=LegacyPlayer(folder);self.ctx.session.namespace["player"]=player
            handle=await self.api.run_for(days=1)
            # The mature CLI uses json.dumps and checks background/watch.
            reply=json.loads(json.dumps(handle))
            self.assertTrue(reply["background"]);self.assertEqual(reply["watch"],1)
            self.assertIs(player.watch,handle._task)
            notice=await handle.wait()
            recorded=json.loads((Path(folder)/"notices.json").read_text())
            self.assertEqual(recorded[str(reply["watch"])]["reason"],"date")
            self.assertEqual(player.notices["1"]["date"],notice["date"])
    async def test_new_execute_interrupts_and_original_native_read_completes_once(self):
        with tempfile.TemporaryDirectory() as folder:
            player=LegacyPlayer(folder);self.ctx.session.namespace["player"]=player;self.ctx.advance=False
            handle=await self.api.run_for(days=7)
            self.ctx.block_next=True
            await self.ctx.started.wait()
            next_execute=asyncio.create_task(player.execute(self.api.observe))
            await asyncio.sleep(0)
            self.assertTrue(player.interrupt.is_set());self.assertFalse(next_execute.done())
            self.assertEqual(len(self.ctx.pending_queries),1)
            self.ctx.release.set()
            result=await next_execute
            self.assertEqual(self.ctx.blocked_completions,1)
            self.assertEqual(handle.notice["reason"],"interrupted")
            self.assertTrue(result["paused"]);self.assertEqual(self.ctx.pending_queries,[])
    async def test_known_monitor_error_pauses_and_returns_notice(self):
        calls=0
        async def events():
            nonlocal calls
            calls+=1
            if calls>1:raise ValueError("event read unavailable")
            return []
        self.api._events=events
        handle=await self.api.run_for(days=7);notice=await handle.wait()
        self.assertEqual(notice["reason"],"error");self.assertTrue(self.ctx.paused)

    async def test_default_capture_keeps_nonmodal_reminders_without_pausing_early(self):
        samples=0
        async def perceive(*args):
            nonlocal samples
            samples+=1
            return {"cursor":{"world":77,"sequence":samples},"changes":[{"lifecycle":"observed",
                "receipt":{"source":"message_feed","provenance":"native_ui_data","raw_text":{"title":"Unrelated global reminder"}}}],
                "receipts":[],"sources":[]}
        self.api._perception=perceive
        handle=await self.api.run_for(days=1)
        notice=await handle.wait()
        self.assertEqual(notice["reason"],"date")
        self.assertGreaterEqual(len(notice["captured_changes"]),2)
        self.assertIs(handle.perception,notice["perception"])
        self.assertFalse(notice["capture_truncated"])

    async def test_explicit_state_predicate_evaluates_existing_baseline_before_resume(self):
        state={"cursor":{"world":77,"sequence":1},"changes":[],"sources":[{"source":"wars","status":"partial"}],
            "receipts":[{"source":"wars","provenance":"state_observation","native_identity":90,"active":None,
                "fields":{"country_is_leader":True},"active_reason":"not exposed"}]}
        self.api._perception=AsyncMock(return_value=state)
        notice=await self.api.run_for(days=90,state_sources=("wars",),
            stop_when=lambda snapshot:any(row["fields"]["country_is_leader"] is True for row in snapshot["receipts"]))
        self.assertEqual(notice["reason"],"condition")
        self.assertIsNone(notice["perception"]["receipts"][0]["active"])
        self.assertNotIn("time.resume",self.ctx.queries)
        self.assertEqual(self.api._perception.await_args.args[3],("wars",))

    async def test_predicate_stops_during_original_wait_and_terminal_run_does_not_restart(self):
        calls=0
        async def perceive(*args):
            nonlocal calls
            calls+=1
            return {"cursor":{"world":77,"sequence":calls},"changes":[],"sources":[],
                "receipts":[] if calls==1 else [{"source":"message_popups","provenance":"native_ui_data","native_identity":None}]}
        self.api._perception=perceive
        handle=await self.api.run_until("1837-01-01",stop_when=lambda snapshot:bool(snapshot["receipts"]))
        task=handle._task;notice=await handle.wait()
        self.assertEqual(notice["reason"],"condition")
        self.assertTrue(notice["paused"])
        self.assertEqual(notice["date"],self.api._public(await self.ctx.refresh())["date"])
        settled_calls=calls
        self.assertIs(notice,await handle.wait())
        self.assertIs(handle._task,task)
        self.assertEqual(calls,settled_calls)
        self.assertEqual(handle["status"],"complete")

    async def test_interrupt_settles_original_perception_then_wins_over_user_policy(self):
        self.ctx.advance=False
        started=asyncio.Event();release=asyncio.Event();calls=0;policy=[]
        async def perceive(*args):
            nonlocal calls
            calls+=1
            if calls>1:
                owner=object();self.ctx.pending_queries.append(owner);started.set()
                await release.wait();self.ctx.pending_queries.remove(owner)
            return {"cursor":None,"changes":[],"receipts":[],"sources":[]}
        self.api._perception=perceive
        def predicate(snapshot):policy.append(snapshot);return False
        handle=await self.api.run_for(days=7,stop_when=predicate)
        await started.wait()
        interrupted=asyncio.create_task(handle.interrupt());await asyncio.sleep(0)
        self.assertFalse(interrupted.done());self.assertEqual(len(self.ctx.pending_queries),1)
        self.assertNotIn("time.pause",self.ctx.queries)
        release.set();notice=await interrupted
        self.assertEqual(notice["reason"],"interrupted")
        self.assertEqual(calls,2);self.assertEqual(len(policy),1)
        self.assertEqual(self.ctx.pending_queries,[])

    async def test_native_pause_after_resume_is_not_overridden_or_waited_forever(self):
        with patch("pdx_runtime.v3_game.time.life.set_paused",side_effect=lambda *a,**k:("pause",True)):
            handle=await self.api.run_for(days=7)
            notice=await handle.wait()
        self.assertEqual(notice["reason"],"paused")
        self.assertEqual(self.ctx.queries.count("time.resume"),1)
        self.assertNotIn("time.pause",self.ctx.queries)

    async def test_world_change_during_perception_stops_without_pausing_new_world(self):
        calls=0
        async def perceive(*args):
            nonlocal calls
            calls+=1
            if calls>1:self.ctx.world=88
            return {"cursor":None,"world_changed":calls>1,"changes":[],"receipts":[],"sources":[]}
        self.api._perception=perceive
        handle=await self.api.run_for(days=7);notice=await handle.wait()
        self.assertEqual(notice["reason"],"world_changed")
        self.assertNotIn("time.pause",self.ctx.queries)

    async def test_unknown_original_perception_does_not_submit_replacement_pause(self):
        calls=0
        async def perceive(*args):
            nonlocal calls
            calls+=1
            if calls>1:
                self.ctx.pending_queries.append(object())
                raise RuntimeError("original operation unknown")
            return {"cursor":None,"changes":[],"receipts":[],"sources":[]}
        self.api._perception=perceive
        handle=await self.api.run_for(days=7);notice=await handle.wait()
        self.assertEqual(notice["reason"],"error")
        self.assertTrue(notice["original_operation_retained"])
        self.assertEqual(calls,2);self.assertEqual(len(self.ctx.pending_queries),1)
        self.assertNotIn("time.pause",self.ctx.queries)

    async def test_capture_history_is_bounded_and_eviction_explicit(self):
        self.api._perception=AsyncMock(return_value={"cursor":None,"sources":[],"receipts":[],
            "changes":[{"lifecycle":"observed","receipt":{"source":"toast","ordinal":i}} for i in range(150)]})
        notice=await self.api.run_for(days=0)
        self.assertEqual(len(notice["captured_changes"]),128)
        self.assertTrue(notice["capture_truncated"])
        self.assertEqual(notice["captured_changes"][0]["receipt"]["ordinal"],22)

    async def test_predicate_requires_explicit_bool_not_truthy_number_or_coroutine(self):
        with self.assertRaisesRegex(TypeError,"return bool"):
            await self.api.run_for(days=1,stop_when=lambda snapshot:1)
        async def async_predicate(snapshot):return False
        with self.assertRaisesRegex(TypeError,"synchronous"):
            await self.api.run_for(days=1,stop_when=async_predicate)
        self.assertNotIn("time.resume",self.ctx.queries)

    async def test_time_uses_real_highlevel_event_adapter_with_native_source_metadata(self):
        # Exercise NotificationsAPI, not an injected fake-only notification bus.
        del self.api._perception
        self.ctx.country_id=AsyncMock(return_value=0)
        provider=N(observe=AsyncMock(return_value={"sources":[{"source":"message_popups","status":"unavailable",
            "reason":"no copied popup root","coverage":None}],"receipts":[]}))
        self.api._events=AsyncMock(return_value=[{"id":"event-1","title":"Actual event","options":[
            {"index":0,"enabled":False,"default":False}],"text":{"title":{"raw_text":"Raw title","raw_bytes":b"Raw title","complete":True}}}])
        with patch("pdx_runtime.v3_game.notifications.importlib.import_module",return_value=provider):
            notice=await self.api.run_for(days=1,notification_sources=("event_windows","message_popups"))
        self.assertEqual(notice["reason"],"event")
        receipt=notice["perception"]["receipts"][0]
        self.assertEqual(receipt["provenance"],"native_ui_data")
        self.assertEqual(receipt["raw_text"]["title"],"Raw title")
        self.assertIsNone(receipt["actor_country_id"])  # No extra actor query after a known stop.
        self.assertFalse(receipt["actions"][0]["enabled"])
        self.assertFalse(notice["perception"]["selected_sources_complete"])
        self.assertNotIn("time.resume",self.ctx.queries)

    async def test_existing_event_remains_priority_without_invoking_new_policy(self):
        self.api._events=AsyncMock(return_value=[{"id":"event-1","title":"Existing"}])
        def forbidden(snapshot):raise AssertionError("An existing modal already stops the run")
        notice=await self.api.run_for(days=1,stop_when=forbidden)
        self.assertEqual(notice["reason"],"event")
        self.assertNotIn("time.resume",self.ctx.queries)


class _ClockQuery:
    def __init__(self, read):
        self.reader = read
        self.reads = 0
        self._result = None
        self._released = False

    def read(self):
        self.reads += 1
        self._result = self.reader()
        self._released = True
        return self._result


class _RetainedClock:
    """Synchronous fake native callbacks with explicitly held startup/drive stages."""
    pid = 123

    def __init__(self, loop):
        import threading
        self.loop = loop
        self.start_entered, self.drive_entered = asyncio.Event(), asyncio.Event()
        self.start_release, self.drive_release = threading.Event(), threading.Event()
        self.current = datetime(1836, 1, 1)
        self.paused, self.speed = True, 2
        self.observations, self.running_observations = 0, 0
        self.queries, self.controls = [], []

    def request(self, command):
        if command != "world-status":
            raise AssertionError(command)
        return {"status": "ok", "world": "0x4d", "interface_thread_id": 8}

    def status(self):
        return {"status": "ok", "main_module_base": "0x1000"}

    def query(self, reader):
        query = _ClockQuery(reader)
        self.queries.append(query)
        return query

    def observe(self, *args, **kwargs):
        def read():
            self.observations += 1
            if self.observations == 1:
                self.loop.call_soon_threadsafe(self.start_entered.set)
                if not self.start_release.wait(2):
                    raise AssertionError("startup fake callback was not released")
            if not self.paused:
                self.running_observations += 1
                # First is resume's observation; second is the original drive.
                if self.running_observations == 2:
                    self.loop.call_soon_threadsafe(self.drive_entered.set)
                    if not self.drive_release.wait(2):
                        raise AssertionError("drive fake callback was not released")
                    self.current = datetime(1836, 1, 2)
            return N(error=0, world=77, transition_pending=False,
                paused=self.paused, speed=self.speed,
                displayed_speed=0 if self.paused else self.speed + 1,
                date_parts=(self.current.year, self.current.month, self.current.day, self.current.hour))
        return self.query(read)

    def set_speed(self, client, speed, **kwargs):
        def read():
            self.controls.append(("speed", speed))
            self.speed = speed
            return N(error=0)
        return self.query(read)

    def set_paused(self, client, paused, **kwargs):
        def read():
            self.controls.append(("paused", paused))
            self.paused = paused
            return N(error=0)
        return self.query(read)


class ContinuationTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from pdx_runtime.v3_game._core import GameContext
        self.clock = _RetainedClock(asyncio.get_running_loop())
        session = N(native=self.clock, namespace={})
        self.ctx = GameContext(session, process=object(), timeout=.01, poll_interval=.001)
        self.api = TimeAPI(self.ctx)
        self.api._events = AsyncMock(return_value=[])
        self.api._perception = AsyncMock(return_value={
            "cursor": None, "changes": [], "receipts": [], "sources": []})
        self.enterContext(patch("pdx_runtime.v3_game.time.life.observe", side_effect=self.clock.observe))
        self.enterContext(patch("pdx_runtime.v3_game.time.life.set_speed", side_effect=self.clock.set_speed))
        self.enterContext(patch("pdx_runtime.v3_game.time.life.set_paused", side_effect=self.clock.set_paused))

    async def asyncTearDown(self):
        # Keep the original task and native fake callbacks alive through cleanup,
        # including the intentionally failing pre-fix regression run.
        self.clock.start_release.set()
        self.clock.drive_release.set()
        tasks = tuple(self.ctx._operations)
        if tasks:
            await asyncio.wait(tasks, timeout=1)
        if self.api._run is not None and not self.api._run._task.done():
            await asyncio.wait({self.api._run._task}, timeout=1)

    def start(self, method, *, wait, reached=False):
        if method == "for":
            return self.api.run_for(days=0 if reached else 1, wait=wait)
        return self.api.run_until("1836-01-01" if reached else "1836-01-02", wait=wait)

    async def pending_start(self, method, *, wait):
        from pdx_runtime.query_executor import PendingOperation
        pending = await self.start(method, wait=wait)
        await asyncio.wait_for(self.clock.start_entered.wait(), 1)
        self.assertIsInstance(pending, PendingOperation)
        self.assertIs(await pending.resume(timeout=0), pending)
        self.assertFalse(pending.done)
        self.assertIsNone(self.api._run)
        self.clock.start_release.set()
        await asyncio.wait_for(self.clock.drive_entered.wait(), 1)
        self.assertIsInstance(self.api._run, TimeRun)
        return pending

    async def check_terminal_continuation(self, method):
        pending = await self.pending_start(method, wait=True)
        original_task = pending.task
        handle = self.api._run
        drive = handle._task
        # Startup has finished, but the original drive is still held. A caller
        # waiting for the terminal notice must not receive the intermediate run.
        self.assertIs(await pending.resume(timeout=0), pending)
        self.assertFalse(pending.done)
        self.clock.drive_release.set()
        notice = await pending.resume(timeout=1)
        self.assertNotIsInstance(notice, TimeRun)
        self.assertIs(notice, handle.notice)
        self.assertEqual(notice["reason"], "date")
        self.assertTrue(notice["paused"])
        self.assertIs(pending.task, original_task)
        self.assertIs(handle._task, drive)
        self.assertEqual(self.api._serial, 1)
        self.assertEqual(self.clock.controls, [("speed", 2), ("paused", False), ("paused", True)])
        self.assertTrue(all(query.reads == 1 for query in self.clock.queries))
        self.assertEqual(self.ctx.pending_queries, [])

    async def test_run_for_wait_true_retains_terminal_continuation_after_start_pending(self):
        await self.check_terminal_continuation("for")

    async def test_run_until_wait_true_retains_terminal_continuation_after_start_pending(self):
        await self.check_terminal_continuation("until")

    async def check_background(self, method):
        pending = await self.pending_start(method, wait=False)
        handle = await pending.resume(timeout=1)
        self.assertIs(handle, self.api._run)
        self.assertIsInstance(handle, TimeRun)
        self.assertFalse(handle._task.done())
        self.assertTrue(handle["background"])
        self.clock.drive_release.set()
        notice = await handle.wait()
        self.assertEqual(notice["reason"], "date")
        self.assertEqual(self.api._serial, 1)
        self.assertEqual(self.clock.controls.count(("paused", False)), 1)

    async def test_run_for_wait_false_keeps_background_run_after_start_pending(self):
        await self.check_background("for")

    async def test_run_until_wait_false_keeps_background_run_after_start_pending(self):
        await self.check_background("until")

    async def check_reached(self, method):
        from pdx_runtime.query_executor import PendingOperation
        pending = await self.start(method, wait=True, reached=True)
        self.assertIsInstance(pending, PendingOperation)
        self.clock.start_release.set()
        notice = await pending.resume(timeout=1)
        self.assertEqual(notice["reason"], "date")
        self.assertIsNone(self.api._run)
        self.assertEqual(self.clock.controls, [])

    async def test_run_for_wait_true_preserves_immediate_terminal_notice(self):
        await self.check_reached("for")

    async def test_run_until_wait_true_preserves_immediate_terminal_notice(self):
        await self.check_reached("until")

    async def test_cancelled_public_wait_keeps_original_terminal_continuation(self):
        self.ctx.timeout = 10
        caller = asyncio.create_task(self.start("for", wait=True))
        await asyncio.wait_for(self.clock.start_entered.wait(), 1)
        original_task, = self.ctx._operations
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertFalse(original_task.cancelled())
        self.clock.start_release.set()
        await asyncio.wait_for(self.clock.drive_entered.wait(), 1)
        self.assertFalse(original_task.done())
        self.clock.drive_release.set()
        notice = await asyncio.wait_for(original_task, 1)
        self.assertIs(notice, self.api._run.notice)
        self.assertEqual(notice["reason"], "date")
        self.assertEqual(self.api._serial, 1)
        self.assertEqual(self.clock.controls.count(("paused", False)), 1)
