import asyncio, json, sys, threading, types, unittest
from unittest.mock import patch,AsyncMock
from pdx_runtime.v3_game import V3Game,PendingOperation
from pdx_runtime.v3_game._core import GameContext, DEFAULT_ADAPTERS
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES
from pdx_runtime import v3_lifecycle as life,v3_player_control as player,v3_data as data

class Query:
    def __init__(self,result,gate=None):self.result=result;self.gate=gate;self.reads=0;self._released=False;self._result=None;self.started=threading.Event()
    def read(self):
        self.started.set()
        self.reads+=1
        if self.gate is not None and not self.gate.is_set():return None
        self._released=True;self._result=self.result;return self.result

class Native:
    def __init__(self):self.world=17;self.base=0x140000000;self.tid=73
    def request(self,command):
        if command!="world-status":raise AssertionError(f"unexpected wire command {command!r}")
        return {"status":"ok","world":hex(self.world),"interface_thread_id":self.tid,"transition_pending":False}
    def status(self):return {"status":"ok","main_module_base":hex(self.base)}

class CoreTests(unittest.IsolatedAsyncioTestCase):
    def context(self,timeout=.02):
        native=Native();process=object();session=types.SimpleNamespace(native=native,namespace={})
        return GameContext(session,process,timeout=timeout,poll_interval=.001)
    def lifecycle(self,ctx):return Query(types.SimpleNamespace(error=0,world=ctx.native.world))
    async def test_exact_session_process_full_raw_and_lazy_domains(self):
        ctx=self.context()
        with patch("pdx_runtime.v3_game._core.RemoteProcess",side_effect=AssertionError("new process")):
            game=V3Game(ctx.session,process=ctx.process)
        self.assertIs(game.raw.session,ctx.session);self.assertIs(game.raw.native,ctx.native);self.assertIs(game.raw.process,ctx.process)
        fake=types.SimpleNamespace(CountryAPI=lambda c:types.SimpleNamespace(ctx=c))
        with patch.dict(sys.modules,{"pdx_runtime.v3_game.country":fake}):
            first=game.country;self.assertIs(first,game.country);self.assertIs(first.ctx,game._ctx)
        self.assertIs(game.raw.v3_data,data);self.assertFalse(game._ctx.owns_process)
        self.assertEqual(DEFAULT_ADAPTERS["carrier"],"pdx_runtime.dll")
    async def test_refresh_and_paged_metadata_cache_invalidates_world(self):
        ctx=self.context();calls=[]
        def types_page(*a,**kw):
            cursor=kw["start"];calls.append((ctx.native.world,cursor))
            rows=([types.SimpleNamespace(name="Other",type_id=1)] if cursor==0 else [types.SimpleNamespace(name="Country",type_id=ctx.native.world+100)])
            return Query(types.SimpleNamespace(error=0,rows=rows,next=128 if cursor==0 else 129,total=129))
        with patch.object(life,"observe",side_effect=lambda *a,**kw:self.lifecycle(ctx)),patch.object(data,"enumerate_types",side_effect=types_page):
            await ctx.refresh()
            self.assertEqual(await ctx.type_id("Country"),117);self.assertEqual(await ctx.type_id("Country"),117)
            self.assertEqual(len(calls),2);self.assertEqual(ctx.image_base,0x140000000);self.assertEqual(ctx.interface_thread_id,73)
            ctx.native.world=29
            self.assertEqual(await ctx.type_id("Country"),117)
            await ctx.refresh()
            self.assertEqual(await ctx.type_id("Country"),129)
            self.assertEqual(len(calls),4)
    async def test_pending_retains_whole_coroutine_and_short_serialization(self):
        ctx=self.context(timeout=.003);gate=asyncio.Event();query=Query({"applied":True},gate);calls=[];steps=[]
        self.addCleanup(gate.set)
        async def operation():
            steps.append("before");value=await ctx.query("build",lambda:(calls.append(1),query)[1]);steps.append("after");return value
        pending=await ctx.perform("build",operation)
        self.assertIsInstance(pending,PendingOperation);self.assertEqual(json.loads(json.dumps(pending)),{"status":"pending","operation":"build"})
        self.assertTrue(await asyncio.to_thread(query.started.wait,1))
        self.assertEqual(steps,["before"]);self.assertEqual(calls,[1]);self.assertIs(pending.pending_queries[0],query)
        gate.set();self.assertEqual(await pending.resume(timeout=.2),{"applied":True});self.assertEqual(steps,["before","after"]);self.assertEqual(calls,[1]);self.assertEqual(ctx.pending_queries,[])
    async def test_timed_out_failure_keeps_original_task_without_shield_diagnostics(self):
        ctx=self.context(timeout=.001);gate=asyncio.Event();calls=[]
        loop=asyncio.get_running_loop();previous=loop.get_exception_handler();diagnostics=[]
        loop.set_exception_handler(lambda current,context:diagnostics.append(context))
        async def operation():
            calls.append(1)
            await gate.wait()
            raise ValueError("original delayed failure")
        try:
            pending=await ctx.perform("delayed failure",operation)
            self.assertIsInstance(pending,PendingOperation);original=pending.task
            gate.set()
            with self.assertRaisesRegex(ValueError,"original delayed failure"):
                await pending.resume(timeout=1)
            await asyncio.sleep(0)  # Let scheduled completion callbacks settle.
            self.assertIs(pending.task,original);self.assertTrue(original.done())
            self.assertEqual(calls,[1]);self.assertEqual(diagnostics,[])
        finally:
            loop.set_exception_handler(previous)

    async def test_owner_complete_is_preserved(self):
        ctx=self.context();query=Query(types.SimpleNamespace(error=0));calls=[]
        class Owner:
            pending=query
            def complete(self):calls.append(1);return self.pending.read()
        owner=Owner();result=await ctx.query("owner",lambda:query,owner=owner)
        self.assertEqual(result.error,0);self.assertEqual(calls,[1])
    async def test_support_bridge_keeps_deadline_frame_and_finished_ledger(self):
        ctx=self.context(timeout=.003);helper=ctx.support('economy.command_plans');gate=asyncio.Event();query=Query(types.SimpleNamespace(error=0),gate);finished=[]
        plan={"pending":{"query":query,"owner":None}}
        def finish(plan,result):finished.append(result);plan["pending"]=None;return {"finished":True}
        with patch.object(helper,"_finished",side_effect=finish):
            pending=await ctx.perform("helper",lambda:helper.settle_pending(plan,timeout_seconds=.001))
            self.assertIsInstance(pending,PendingOperation);await asyncio.sleep(.005);self.assertFalse(pending.done)
            gate.set();self.assertEqual(await pending.resume(timeout=.2),{"finished":True})
        self.assertEqual(len(finished),1);self.assertIsNone(plan["pending"])
    async def test_helper_uncertain_submit_keeps_original_frame_and_factory_once(self):
        ctx=self.context(timeout=.003);helper=ctx.support('economy.command_plans');gate=asyncio.Event();query=Query(types.SimpleNamespace(error=0),gate);calls=[]
        self.addCleanup(gate.set)
        plan={"client":ctx.native,"world":ctx.native.world,"pending":None,"uncertain_operations":[]}
        def factory():
            calls.append(1);error=OSError("queue response lost");error.query=query;raise error
        def finished(plan,result):plan["pending"]=None;return {"completed":True}
        with patch.object(helper,"_finished",side_effect=finished):
            pending=await ctx.perform("helper.unknown",lambda:helper._once(plan,"select",factory))
            self.assertIsInstance(pending,PendingOperation)
            self.assertTrue(await asyncio.to_thread(query.started.wait,1))
            self.assertIs(plan["pending"]["query"],query)
            gate.set();self.assertEqual(await pending.resume(timeout=.2),{"completed":True})
        self.assertEqual(calls,[1]);self.assertEqual(ctx.pending_queries,[])
    async def test_building_metadata_cache_reuses_two_sources_but_never_providers(self):
        from tests.fixtures.building_io import Fixture,BUILDING_ID
        from pdx_runtime.v3_game.economy import building_io
        class Client:
            world=17
            def request(self,command):
                if command!="world-status":raise AssertionError(f"unexpected wire command {command!r}")
                return {"status":"ok","world":self.world}
        class Process:
            owner=0x1000
            def resolve_export(self,adapter,symbol,*,aliases):
                if aliases is not V3_EXPORT_ALIASES:raise AssertionError("expected explicit V3 aliases")
                return self.owner+0x80
            def export_owner(self,address):return address-0x80
        client,process,cache=Client(),Process(),{}
        first=Fixture();second=Fixture()
        async def read(fixture,source):
            with patch.object(data,"_submit",side_effect=fixture.submit):
                return await building_io.read_all_building_io(client,building_full_id=BUILDING_ID,
                    await_result=fixture.complete,source=source,process=process,page_capacity=2,metadata_cache=cache)
        produced=await read(first,"produced");consumed=await read(second,"consumed")
        metadata=(data.Action.TYPES,data.Action.FUNCTIONS)
        self.assertTrue(any(call["action"] in metadata for call in first.calls))
        self.assertFalse(any(call["action"] in metadata for call in second.calls))
        self.assertFalse(produced["metadata_cache_hit"]);self.assertTrue(consumed["metadata_cache_hit"])
        self.assertIn("Building.AccessProducedGoods",first.expressions())
        self.assertIn("Building.AccessConsumedGoods",second.expressions())
        self.assertTrue(produced["rows"]);self.assertTrue(consumed["rows"])
        self.assertIsNot(produced["source_result"],consumed["source_result"])
        self.assertEqual(set(cache[(17,0x1000)]),{"type_rows","function_rows"})
        self.assertIsNot(produced["signatures"],consumed["signatures"])
        client.world=18;third=Fixture();world_changed=await read(third,"produced")
        self.assertFalse(world_changed["metadata_cache_hit"])
        self.assertTrue(any(call["action"] in metadata for call in third.calls))
        process.owner=0x2000;fourth=Fixture();adapter_changed=await read(fourth,"consumed")
        self.assertFalse(adapter_changed["metadata_cache_hit"])
        self.assertTrue(any(call["action"] in metadata for call in fourth.calls))
        self.assertEqual(set(cache),{(17,0x1000),(18,0x1000),(18,0x2000)})
    async def test_building_error_metadata_pages_do_not_pollute_cache(self):
        from dataclasses import replace
        from tests.fixtures.building_io import Fixture,BUILDING_ID
        from pdx_runtime.v3_game.economy import building_io
        def request(command):
            self.assertEqual(command,"world-status")
            return {"status":"ok","world":17}
        def resolve_export(adapter,symbol,*,aliases):
            self.assertIs(aliases,V3_EXPORT_ALIASES)
            return 0x1080
        client=types.SimpleNamespace(request=request)
        process=types.SimpleNamespace(resolve_export=resolve_export,export_owner=lambda a:0x1000)
        for failing_action in (data.Action.TYPES,data.Action.FUNCTIONS):
            cache={};fixture=Fixture();original=fixture.submit
            def broken(*args,**kwargs):
                query=original(*args,**kwargs)
                if query.call["action"]==failing_action and query.call["start"]==2:
                    query.result=replace(query.result,error=50)
                return query
            with patch.object(data,"_submit",side_effect=broken):
                await building_io.read_all_building_io(client,building_full_id=BUILDING_ID,
                    await_result=fixture.complete,process=process,page_capacity=2,metadata_cache=cache)
            self.assertEqual(cache,{})
            good=Fixture()
            with patch.object(data,"_submit",side_effect=good.submit):
                state=await building_io.read_all_building_io(client,building_full_id=BUILDING_ID,
                    await_result=good.complete,process=process,page_capacity=2,metadata_cache=cache)
            self.assertFalse(state["metadata_cache_hit"]);self.assertIn((17,0x1000),cache)
    async def test_shared_cache_world_refresh_replaces_old_suspended_namespace(self):
        ctx=self.context()
        with patch.object(life,"observe",side_effect=lambda *a,**kw:self.lifecycle(ctx)):
            await ctx.refresh();old=ctx.shared_cache("building_io");old["copied"]=17
            self.assertIs(old,ctx.shared_cache("building_io"))
            ctx.native.world=29;await ctx.refresh();new=ctx.shared_cache("building_io")
        self.assertEqual(new,{});self.assertIsNot(old,new);self.assertEqual(old,{"copied":17})
    async def test_diplomatic_timer_helper_two_callbacks_do_not_leave_orphan_lock(self):
        ctx=self.context(timeout=.003);helper=ctx.support('diplomacy._country_commands');calls=[]
        plan={"client":ctx.native,"expected_world":ctx.native.world,"pending":None,"operations":[],"status":"new"}
        async def operation():
            first=await helper._once(plan,"create",lambda:(calls.append("create"),Query(1))[1])
            second=await helper._once(plan,"serialize",lambda:(calls.append("serialize"),Query(2))[1])
            return first+second
        result=await ctx.perform("diplomacy.real-helper",operation)
        if isinstance(result,PendingOperation):result=await result.resume(timeout=.1)
        self.assertEqual(result,3);self.assertEqual(calls,["create","serialize"])
        self.assertEqual(len(plan["operations"]),2);self.assertFalse(ctx._query_lock.locked());self.assertEqual(ctx.pending_queries,[])
    async def test_diplomatic_owner_complete_and_original_pending_continue(self):
        ctx=self.context(timeout=.003);helper=ctx.support('diplomacy._country_commands');gate=asyncio.Event();q=Query(4,gate);calls=[]
        class Owner:
            pending=q
            def complete(self):calls.append("read");return self.pending.read()
        owner=Owner();plan={"client":ctx.native,"expected_world":ctx.native.world,"pending":None,"operations":[],"status":"new"}
        factories=[]
        def factory():factories.append(1);return q
        pending=await ctx.perform("diplomacy.pending",lambda:helper._once(plan,"serialize",factory,owner=owner,timeout_seconds=.001))
        self.assertIsInstance(pending,PendingOperation);await asyncio.sleep(.005);self.assertFalse(pending.done)
        gate.set();self.assertEqual(await pending.resume(timeout=.1),4);self.assertEqual(factories,[1]);self.assertFalse(ctx._query_lock.locked())
    async def test_original_cached_orphan_slot_reconciles_without_native_replay(self):
        ctx=self.context();q=Query(1);await ctx.submit(lambda:q)
        self.assertEqual(q.read(),1)  # Legacy timer's completed raw SDK read.
        self.assertTrue(ctx._query_lock.locked())
        calls=[]
        self.assertEqual(await asyncio.wait_for(ctx.query("next",lambda:(calls.append(1),Query(2))[1]),.1),2)
        self.assertEqual(calls,[1]);self.assertEqual(q.reads,1);self.assertFalse(ctx._query_lock.locked())
    async def test_cancelling_only_caller_wait_keeps_original_operation_alive(self):
        ctx=self.context(timeout=.1);gate=asyncio.Event();q=Query(5,gate);calls=[]
        async def operation():return await ctx.query("native",lambda:(calls.append(1),q)[1])
        caller=asyncio.create_task(ctx.perform("caller-wait",operation))
        while not ctx.pending_queries:await asyncio.sleep(.001)
        tasks=list(ctx._operations);caller.cancel()
        with self.assertRaises(asyncio.CancelledError):await caller
        self.assertFalse(tasks[0].cancelled());self.assertIs(ctx.pending_queries[0],q)
        gate.set();self.assertEqual(await asyncio.wait_for(tasks[0],.1),5);self.assertEqual(calls,[1])
    async def test_query_lock_is_not_a_whole_operation_lock(self):
        ctx=self.context();gate=asyncio.Event()
        async def slow():await gate.wait();return "slow"
        pending=await ctx.perform("slow",slow);self.assertIsInstance(pending,PendingOperation)
        paused=await ctx.perform("pause",lambda:ctx.query("pause",lambda:Query("paused")))
        if isinstance(paused,PendingOperation):paused=await paused.resume(timeout=1)
        self.assertEqual(paused,"paused")
        gate.set();self.assertEqual(await pending.resume(timeout=.2),"slow")
    async def test_treaty_operation_uses_shared_wait_and_original_query(self):
        ctx=self.context(timeout=.003);query_module=ctx.support("diplomacy.treaty.query")
        gate=asyncio.Event();q=Query(types.SimpleNamespace(error=0),gate);calls=[]
        state=query_module.new_state(ctx.native,ctx.process,0,ctx.adapters["data"])
        def factory():calls.append(1);return q
        pending=await ctx.perform("treaty.read",lambda:query_module.operation(state,"read",factory))
        self.assertIsInstance(pending,PendingOperation)
        self.assertFalse(pending.done)
        gate.set();result=await pending.resume(timeout=.2)
        self.assertEqual(result.error,0);self.assertEqual(calls,[1]);self.assertIsNone(state["pending"])
        self.assertIs(state["operations"][0]["query"],q)
        self.assertIs(state["operations"][0]["result"],result)
    async def test_lifecycle_attach_adopts_supplied_resources_without_taking_ownership(self):
        from pdx_runtime.v3_game import lifecycle
        ctx=self.context();ctx.native.pid=67
        session=ctx.session;process=types.SimpleNamespace(pid=67,close=lambda:None)
        with patch.object(lifecycle,"_install",AsyncMock(return_value={"modules":{}})),patch.object(life,"observe",side_effect=lambda *a,**kw:self.lifecycle(ctx)):
            game=await V3Game.attach(session=session,process=process,timeout=.2)
        self.assertIsInstance(game.lifecycle,lifecycle.LifecycleAPI)
        self.assertFalse(game.lifecycle.owns_session);self.assertFalse(game.lifecycle.owns_process)
        self.assertIs(game._ctx.session,session);self.assertIs(game._ctx.process,process)
        async with game:self.assertFalse(game.lifecycle.detached)
        self.assertTrue(game.lifecycle.detached)
    async def test_async_exit_waits_original_pending_detach(self):
        from pdx_runtime.v3_game import lifecycle
        ctx=self.context();game=V3Game(ctx.session,process=ctx.process,timeout=.001)
        gate=asyncio.Event();finished=[]
        async def pending_detach(api):
            async def finish():await gate.wait();finished.append(1);return {"detached":True}
            return await api.ctx.perform("detach",finish)
        with patch.object(lifecycle.LifecycleAPI,"detach",pending_detach):
            closing=asyncio.create_task(game.__aexit__(None,None,None));await asyncio.sleep(.005)
            self.assertFalse(closing.done());gate.set();await asyncio.wait_for(closing,.2)
        self.assertEqual(finished,[1])

if __name__=="__main__":unittest.main(verbosity=2)
