"""Portable CPU regressions for the current selected-Goods A0 path.

Run real EconomyAPI composition with mocked lower interfaces. No game, process,
immutable snapshot, external source loader, environment setting or old algorithm
is required. Count selection plus final releases only, not total action latency.
"""
import asyncio
from collections import Counter
from contextlib import ExitStack
import struct
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as D, v3_commands as C
from pdx_runtime.query_executor import Diagnostics
from pdx_runtime.v3_game import economy as economy_module
from pdx_runtime.v3_game.economy import EconomyAPI

SELECTION_LABELS={"Market.GetNameNoFormatting","Market.GetName","Market.GetGoodsSortedByBalance",
    "economy.market_goods_count","economy.market_good","Goods.GetKey","Goods.GetNameNoFormatting",
    "Goods.GetName","economy.goods_definition"}

def q(result): return NS(result=result,request_id=1)

class Context:
    def __init__(self,f):
        self.f=f; self.native=NS(pid=7); self.process=NS(pid=7,resolve_export=self.resolve)
        self.world=0x7000; self.image_base=0x140000000; self.interface_thread_id=77
        self.adapters={key:0x180000000 for key in ("data","objects","commands","access","call","runtime","console","script")}
        self.diagnostics=Diagnostics(); self.calls=[]; self.type_calls=[]; self.refreshes=0; self.pending_queries=[]
    def resolve(self,module,name,*,aliases=None):
        self.f.exports.append(name); return 0x180010000 if name=="PdxRuntimeRead" else 0x180020000
    async def refresh(self): self.refreshes+=1
    async def country_id(self): return self.f.actor
    async def type_id(self,name): self.type_calls.append(name); return {"Country":83,"Market":92,"Goods":99}[name]
    async def blocking(self,function,*args,**kwargs): return function(*args,**kwargs)
    async def perform(self,label,work): return await work()
    async def wait(self,query): return query.result
    async def query(self,label,factory,**kwargs):
        self.calls.append(label)
        try:
            query=factory()
            if self.f.block_label==label and not self.f.did_block:
                self.f.did_block=True; self.pending_queries.append(query); self.f.blocked.set()
                await self.f.proceed.wait(); self.pending_queries.remove(query)
            if self.f.fail_label==label:
                self.pending_queries.append(query); self.f.failure.query=query; query.partial_result=query.result
                raise self.f.failure
            result=query.result
        except BaseException:
            raise
        self.diagnostics.append({"label":label,"query":query,"result":result})
        return result

class Fixture:
    def __init__(self,*,count=25,target=20,actor=156,market=2):
        self.actor=actor; self.market=market
        self.rows=[{"key":"small_arms" if index==target-1 else "good_"+str(index),
            "name":"Name "+str(index),"definition_index":(index*7)%997,
            "element_error":0,"key_error":0,"key_partial":False,"name_kind":D.Kind.STRING}
            for index in range(count)]
        self.ctx=Context(self); self.api=EconomyAPI(self.ctx); self.next_handle=100
        self.handles={}; self.live=set(); self.released=[]; self.evaluated=[]; self.elements=[]; self.exports=[]
        self.metadata=[]; self.batches=[]; self.command_requests=[]; self.owners=[]; self.generations=[]
        self.source_error=0; self.count_error=0; self.source_total=None; self.source_handle=None
        self.allowed=True; self.submit_accepted=1; self.submit_error=0; self.readback=True; self.taxed=False
        self.submit_failure=None; self.release_failure=None; self.release_failed=False; self.world_delta=0; self.at_submit_live=None
        self.fail_label=None; self.failure=OSError("original copy interrupted"); self.block_label=None
        self.blocked=asyncio.Event(); self.proceed=asyncio.Event(); self.did_block=False
        self.block_nonselected_names=False; self.block_index=False; self.last_good=None
    def own(self,kind,**extra):
        handle=self.next_handle; self.next_handle+=1
        self.handles[handle]={"kind":kind,**extra}; self.live.add(handle); return handle
    def string(self,text,kind="string",**extra):
        handle=self.own(kind,**extra); raw=text.encode()
        return NS(error=0,kind=D.Kind.STRING,value_handle=handle,value=text,snapshot=raw,
            snapshot_bytes=len(raw),total=len(raw),native_type_name="CString",raw_bits=0)
    def boolean(self,value):
        handle=self.own("boolean")
        return NS(error=0,kind=D.Kind.BOOL,value_handle=handle,value=value,snapshot=bytes((bool(value),)),
            snapshot_bytes=1,total=1,native_type_name="bool",raw_bits=int(bool(value)))
    def row_for_binding(self,bindings):
        binding=next(binding for binding in bindings if binding.type_id==99)
        item=self.handles[binding.object]
        assert item["kind"]=="element" and binding.retained
        assert binding.object in self.live and item["source"]==self.source_handle and item["source"] in self.live
        self.last_good=item["row"]; return item["row"]
    def evaluate(self,client,expression,bindings=(),**kwargs):
        self.evaluated.append((expression,tuple(bindings)))
        if expression.startswith("Market.GetName"):
            return q(self.string("Market "+str(self.market),"market_name"))
        if expression=="Market.GetGoodsSortedByBalance":
            self.source_handle=self.own("source")
            return q(NS(error=self.source_error,kind=D.Kind.LIST,value_handle=self.source_handle))
        if expression=="Goods.GetKey":
            index=self.row_for_binding(bindings); row=self.rows[index]; result=self.string(row["key"],"key",row=index)
            result.error=row["key_error"]
            if row["key_partial"]: result.total+=20
            return q(result)
        if expression.startswith("Goods.GetName"):
            index=self.row_for_binding(bindings); row=self.rows[index]
            if self.block_nonselected_names and row["key"]!="small_arms": raise AssertionError("nonselected name read")
            result=self.string(row["name"],"name",row=index)
            if row["name_kind"]!=D.Kind.STRING:
                result.kind=row["name_kind"]; result.value=False
            return q(result)
        if expression.startswith("Country.Set") or expression=="Goods.ToggleTaxation(Country.Self)":
            index=self.row_for_binding(bindings); self.generations.append((expression,index,tuple(bindings)))
            handle=self.own("generated",row=index)
            value=D.NativeDataCommand(0x5000,0x6000,0x5018,handle,"CDataCommand",self.ctx.world)
            return q(NS(error=0,kind=D.Kind.COMMAND,value=value,value_handle=handle))
        if expression=="Goods.IsTaxed(Country.Self)":
            self.row_for_binding(bindings); return q(self.boolean(self.taxed))
        if expression.startswith("Country.Has"):
            self.row_for_binding(bindings); return q(self.boolean(self.readback))
        raise AssertionError(expression)
    def functions(self,client,type_id,**kwargs):
        self.metadata.append(type_id)
        rows=(NS(name="GetNameNoFormatting",argument_count=0,callbacks=(1,0,0,0)),) if type_id==92 else ()
        return q(NS(error=0,rows=rows,next=len(rows),total=len(rows)))
    def count(self,client,handle,**kwargs):
        assert handle==self.source_handle and handle in self.live
        return q(NS(error=self.count_error,total=len(self.rows) if self.source_total is None else self.source_total))
    def element(self,client,source,index,**kwargs):
        assert source==self.source_handle and source in self.live
        self.elements.append(index); row=self.rows[index]
        handle=self.own("element",row=index,source=source)
        return q(NS(error=row["element_error"],kind=D.Kind.OBJECT,value_handle=handle,
            object=0x900000+index*0x100,native_type_name="CPdxPtr@VCDataGoods"))
    def batch(self,client,callback,initial,**kwargs):
        if self.block_index: raise AssertionError("unused goods_index called")
        address=0x100000; prepared=kwargs["prepare_payload"](address)
        header=economy_module.native_call.BATCH_REQUEST.unpack_from(prepared)
        self.batches.append(header[2]); assert header[2]==3
        rec=economy_module.native_call.REQUEST.unpack_from(prepared,32)
        args=struct.unpack_from("<4Q",prepared,rec[4]-address); index=(args[0]-0x900000)//0x100
        offset=32+3*56+3*32; payload=bytearray(offset+96)
        struct.pack_into("<I",payload,offset+40,self.rows[index]["definition_index"])
        struct.pack_into("<3I",payload,offset+64,24,8,24)
        return q(NS(error=0,entered=True,left=True,payload=bytes(payload),
            callee_result=NS(error=0,completed=3,calls=(NS(error=0,rax=0),)*3)))
    def release(self,client,handle,**kwargs):
        if self.release_failure is not None and not self.release_failed:
            self.release_failed=True; raise self.release_failure
        assert handle in self.live,(handle,self.handles.get(handle))
        self.released.append(handle); self.live.remove(handle)
        return q(NS(error=0,value_handle=0))
    async def relation(self,*args,**kwargs): return {"market_id":self.market}
    def player(self,*args,**kwargs):
        return q(NS(error=0,controlled_country_error=0,observer_enabled=False,
            controlled_country_id=self.actor,world=self.ctx.world+self.world_delta))
    def command_value(self,client,handle,**kwargs):
        assert self.handles[handle]["kind"]=="generated" and handle in self.live
        return q(NS(error=0,value=D.NativeDataCommand(0x5000,0x6000,0x5018,handle,"CDataCommand",self.ctx.world)))
    def clone(self,client,action,**kwargs):
        self.command_requests.append(action); assert action==C.Action.CLONE
        return q(NS(error=0,cloned_command=0x8000))
    def owner(self,*args,**kwargs):
        owner=Owner(self); self.owners.append(owner); return owner
    def patches(self):
        stack=ExitStack()
        stack.enter_context(patch.object(economy_module.market_relation, "observe_country_market", side_effect=self.relation))
        for obj,name,side in ((D,"evaluate",self.evaluate),(D,"enumerate_functions",self.functions),
            (D,"list_count",self.count),(D,"list_element",self.element),(D,"release",self.release),
            (D,"command_value",self.command_value),(C,"request",self.clone),
            (C.NativeCommand,"from_result",self.owner),(economy_module.player,"observe_player",self.player),
            (economy_module.v3_access,"submit_callback",self.batch)):
            stack.enter_context(patch.object(obj,name,side_effect=side))
        return stack
    def selection_queries(self): return sum(label in SELECTION_LABELS for label in self.ctx.calls)
    def tail_count(self): return sum(label=="economy.release" for label in self.ctx.calls)

class Owner:
    def __init__(self,f):
        self.f=f; self.command=0x8000; self.pending=None; self.attempt=NS(outcome="owned")
        self.serializes=0; self.validates=0; self.submits=0; self.destroys=0; self.submit_result=None
    def serialize(self): self.serializes+=1; return q(NS(error=0,data=b"native"))
    def validate(self,**kwargs):
        self.validates+=1; return q(NS(error=0,validated=self.f.allowed,reason="" if self.f.allowed else "native rejection"))
    def submit(self,flags):
        self.submits+=1; self.f.at_submit_live=set(self.f.live)
        if self.f.submit_failure is not None:
            self.pending=object(); self.attempt.outcome="unknown"; raise self.f.submit_failure
        self.command=0; self.attempt.outcome="consumed"
        self.submit_result=NS(error=self.f.submit_error,accepted=self.f.submit_accepted,reason="")
        return q(self.submit_result)
    def destroy(self):
        self.destroys+=1; self.command=0; self.attempt.outcome="consumed"; return q(NS(error=0))

class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_goods_control_k20_uses43_selection44_release_and_original_selected_owner_once(self):
        f=Fixture()
        with f.patches(): result=await f.api.set_tariff("small_arms","import","tariffs_none")
        self.assertEqual((f.selection_queries(),f.tail_count()),(43,44))
        self.assertEqual(f.selection_queries()+f.tail_count(),87)
        self.assertEqual(f.batches,[]); self.assertEqual(f.metadata,[99])
        self.assertTrue(result["accepted"]); self.assertTrue(result["effect_observed"])
        self.assertEqual(len(f.generations),1); self.assertEqual(f.generations[0][1],19)
        self.assertEqual(result["good"],"small_arms"); self.assertEqual(f.elements,list(range(20)))
        self.assertEqual(len(f.owners),1); owner=f.owners[0]
        self.assertEqual((owner.serializes,owner.validates,owner.submits,owner.destroys),(1,1,1,0))
        self.assertEqual(f.command_requests,[C.Action.CLONE])
        expressions=Counter(expression for expression,_ in f.evaluated)
        self.assertEqual(expressions["Market.GetGoodsSortedByBalance"],1)
        self.assertEqual(expressions["Goods.GetKey"],20); self.assertEqual(expressions["Goods.GetName"],1)
        generated_bindings=f.generations[0][2]
        selected=next(binding for binding in generated_bindings if binding.type_id==99)
        self.assertTrue(selected.retained); self.assertEqual(f.handles[selected.object]["row"],19)
        self.assertEqual(f.handles[selected.object]["source"],f.source_handle)
        readback_bindings=next(bindings for expression,bindings in f.evaluated if expression==result["readback_expression"])
        self.assertEqual(readback_bindings,generated_bindings)
        self.assertIn(selected.object,f.at_submit_live); self.assertIn(f.source_handle,f.at_submit_live)
        self.assertEqual(len(f.at_submit_live),43)
        self.assertFalse(f.live); self.assertEqual(len(f.released),len(set(f.released)))
        self.assertIs(result["native_results"]["submit"],owner.submit_result)
        self.assertEqual(result["native_results"]["generated"].value.value_handle,
            next(handle for handle,item in f.handles.items() if item["kind"]=="generated"))

    async def test_current_count_formula_k1_and_k64_keeps_source_and_single_action(self):
        for k in (1,64):
            with self.subTest(k=k):
                f=Fixture(count=k,target=k)
                with f.patches(): result=await f.api.set_tariff("small_arms","import","tariffs_none")
                self.assertEqual(f.selection_queries(),3+2*k)
                self.assertEqual(f.tail_count(),2*k+4)
                self.assertEqual(f.selection_queries()+f.tail_count(),4*k+7)
                self.assertEqual(len(f.generations),1); self.assertEqual(f.generations[0][1],k-1)
                self.assertEqual(f.owners[0].submits,1); self.assertEqual(f.batches,[])
                self.assertIs(result["native_results"]["submit"],f.owners[0].submit_result)
                self.assertFalse(f.live)

    async def test_full_market_joins_actual_definition_index_zero_not_source_order(self):
        f=Fixture(count=3,target=2)
        f.rows[0]["definition_index"]=23; f.rows[1]["definition_index"]=0
        with f.patches(): labels,name=await f.api._market_labels(f.market)
        self.assertEqual(labels,{23:{"key":"good_0","name":"Name 0"},
            0:{"key":"small_arms","name":"Name 1"},14:{"key":"good_2","name":"Name 2"}})
        self.assertEqual(name,"Market 2"); self.assertEqual(f.batches,[3,3,3])
        self.assertEqual(f.metadata,[92,99]); self.assertEqual(f.elements,[0,1,2])
        self.assertEqual(f.ctx.calls[:5],["economy.name_metadata","Market.GetNameNoFormatting",
            "economy.name_metadata","Market.GetGoodsSortedByBalance","economy.market_goods_count"])
        row_queries=["economy.market_good","Goods.GetKey","Goods.GetName","economy.goods_definition"]
        self.assertEqual(f.ctx.calls[5:17],row_queries*3)
        self.assertEqual(f.tail_count(),11); self.assertEqual(f.released,sorted(f.handles,reverse=True))
        self.assertFalse(f.live)
        self.assertEqual(set(f.ctx.diagnostics["economy_names"]),{"Market:2","Goods:good_0","Goods:small_arms","Goods:good_2"})

    async def test_full_market_error_partial_false_empty_and_unknown_matrix(self):
        for setting in ("empty","source_error","count_error","key_error","key_partial","element_error","name_false","unknown_name"):
            with self.subTest(setting=setting):
                f=Fixture(count=0 if setting=="empty" else 3,target=2)
                if setting=="source_error": f.source_error=13
                if setting=="count_error": f.count_error=5
                if setting in ("key_error","key_partial","element_error"):
                    f.rows[0][setting]=True if setting=="key_partial" else 13
                if setting=="name_false": f.rows[0]["name_kind"]=D.Kind.BOOL
                if setting=="unknown_name": f.fail_label="Goods.GetName"
                with f.patches():
                    if setting=="unknown_name":
                        with self.assertRaises(OSError) as caught: await f.api._market_labels(f.market)
                        self.assertIs(caught.exception,f.failure)
                        self.assertIs(f.failure.query,f.ctx.pending_queries[0])
                        self.assertIs(f.failure.query.partial_result,f.failure.query.result)
                        self.assertEqual(f.failure.query.result.snapshot,b"Name 0")
                        self.assertEqual(f.released,[]); self.assertEqual(f.batches,[])
                        retained=f.ctx._economy_retained[0]["retained_handles"]
                        self.assertEqual(retained,[100,101,102,103])
                        self.assertIn(f.source_handle,retained)
                        self.assertEqual(f.live,set(f.handles))
                        continue
                    labels,name=await f.api._market_labels(f.market)
                self.assertEqual(name,"Market 2"); self.assertFalse(f.live)
                self.assertEqual(f.released,sorted(f.handles,reverse=True))
                expected={0:{"key":"good_0","name":"Name 0"},
                    7:{"key":"small_arms","name":"Name 1"},14:{"key":"good_2","name":"Name 2"}}
                if setting in ("empty","source_error","count_error"):
                    expected={}; self.assertEqual(f.elements,[]); self.assertEqual(f.batches,[])
                elif setting in ("key_error","key_partial"):
                    expected[0]["key"]=None
                elif setting=="element_error":
                    del expected[0]
                elif setting=="name_false":
                    expected[0]["name"]=None
                self.assertEqual(labels,expected)
                self.assertEqual(len(f.batches),len(expected))
                self.assertFalse(f.generations); self.assertFalse(f.owners)

    async def test_selected_name_kept_and_prefix_handles_transferred_without_early_release(self):
        f=Fixture(); f.block_nonselected_names=True; f.block_index=True
        with f.patches(): selected,market_name=await f.api._market_labels(f.market,keep_key="small_arms")
        self.assertEqual((selected["key"],selected["name"]),("small_arms","Name 19"))
        self.assertIsNone(market_name); self.assertEqual(selected["market_id"],f.market)
        self.assertTrue(selected["binding"].retained)
        self.assertEqual(selected["goods_handle"],selected["binding"].object)
        self.assertEqual(f.handles[selected["binding"].object]["row"],19)
        names=[expression for expression,_ in f.evaluated if expression.startswith("Goods.GetName")]
        self.assertEqual(names,["Goods.GetName"])
        self.assertFalse(any(expression.startswith("Market.GetName") for expression,_ in f.evaluated))
        self.assertEqual(f.released,[]); self.assertEqual(len(selected["handles"]),42)
        self.assertEqual(set(selected["handles"]),f.live); self.assertIn(f.source_handle,selected["handles"])
        self.assertEqual(set(f.ctx.diagnostics["economy_names"]),{"Goods:small_arms"})
        # Test caller consumes the transferred handles using the real cleanup path.
        with f.patches(): await f.api._release(selected["handles"])
        self.assertFalse(f.live); self.assertEqual(len(f.released),42)

    async def test_target_missing_returns_rejection_without_generating_or_replaying(self):
        f=Fixture(count=3,target=9)
        with f.patches(): result=await f.api.set_tariff("small_arms","import","tariffs_none")
        self.assertIs(result["accepted"],False); self.assertEqual(result["good"],"small_arms")
        self.assertEqual(f.generations,[]); self.assertEqual(f.owners,[]); self.assertFalse(f.live)
        self.assertEqual(f.elements,[0,1,2]); self.assertEqual(f.batches,[])
        self.assertEqual(f.metadata,[])

    async def test_selected_empty_source_and_known_source_count_errors_do_not_succeed(self):
        for setting in ("empty","source_error","count_error"):
            with self.subTest(setting=setting):
                f=Fixture(count=0 if setting=="empty" else 3,target=2)
                if setting=="source_error": f.source_error=13
                if setting=="count_error": f.count_error=5
                with f.patches(): result=await f.api.set_tariff("small_arms","import","tariffs_none")
                self.assertIs(result["accepted"],False); self.assertEqual(f.generations,[])
                self.assertFalse(f.live); self.assertEqual(f.released.count(f.source_handle),1)
                self.assertEqual(f.metadata,[])

    async def test_selected_key_error_partial_key_and_bad_element_never_select_wrong_row(self):
        for setting in ("key_error","key_partial","element_error"):
            with self.subTest(setting=setting):
                f=Fixture(count=3,target=2)
                f.rows[1][setting]=True if setting=="key_partial" else 13
                with f.patches(): result=await f.api.set_tariff("small_arms","import","tariffs_none")
                self.assertIs(result["accepted"],False); self.assertEqual(f.generations,[])
                self.assertFalse(f.live); self.assertEqual(f.batches,[])

    async def test_zero_actor_market_and_false_tax_membership_are_preserved(self):
        f=Fixture(count=1,target=1,actor=0,market=0)
        with f.patches(): result=await f.api.consumption_tax("small_arms")
        self.assertEqual(result["country_id"],0); self.assertIs(result["taxed"],False)
        self.assertEqual(result["name"],"Name 0"); self.assertFalse(f.live); self.assertEqual(f.generations,[])
        market_bindings=[bindings[0] for expression,bindings in f.evaluated if expression=="Market.GetGoodsSortedByBalance"]
        self.assertEqual(len(market_bindings),1); self.assertEqual(market_bindings[0].object,0)

    async def test_accepted_command_false_readback_keeps_exact_native_receipt(self):
        f=Fixture(); f.readback=False
        with f.patches(): result=await f.api.set_tariff("small_arms","import","tariffs_none")
        self.assertTrue(result["accepted"]); self.assertIs(result["observed"],False)
        self.assertIs(result["effect_observed"],False)
        self.assertIs(result["native_results"]["submit"],f.owners[0].submit_result)
        self.assertEqual(f.owners[0].submits,1); self.assertFalse(f.live)

    async def test_raw_native_rejection_stays_rejected_without_readback_or_retry(self):
        f=Fixture(); f.submit_error=13; f.submit_accepted=0
        with f.patches(): result=await f.api.set_tariff("small_arms","import","tariffs_none")
        self.assertIs(result["accepted"],False); self.assertEqual(result["native_error"],13)
        self.assertEqual(result["native_accepted"],0); self.assertEqual(f.owners[0].submits,1)
        self.assertNotIn("readback_result",result); self.assertFalse(f.live)
        self.assertIs(result["native_results"]["submit"],f.owners[0].submit_result)

    async def test_wrong_kind_selected_name_stays_none_not_false_text(self):
        f=Fixture(); f.rows[19]["name_kind"]=D.Kind.BOOL
        with f.patches(): result=await f.api.consumption_tax("small_arms")
        self.assertIsNone(result["name"]); self.assertIs(result["taxed"],False)
        self.assertFalse(f.live)

    async def test_partial_copy_exception_keeps_original_query_and_prefix_owners(self):
        f=Fixture(); f.fail_label="Goods.GetKey"
        with f.patches():
            with self.assertRaises(OSError) as caught: await f.api.set_tariff("small_arms","import","tariffs_none")
        self.assertIs(caught.exception,f.failure)
        self.assertIs(f.failure.query,f.ctx.pending_queries[0])
        self.assertIs(f.failure.query.partial_result,f.failure.query.result)
        retained=f.ctx._economy_retained[0]["retained_handles"]
        self.assertIn(f.source_handle,retained)
        self.assertTrue(all(handle in f.live for handle in retained))
        self.assertEqual(f.released,[]); self.assertEqual(f.generations,[])
        self.assertEqual(Counter(expression for expression,_ in f.evaluated)["Goods.GetKey"],1)

    async def test_unknown_submit_keeps_same_owner_inputs_without_destroy_release_or_replay(self):
        f=Fixture(); f.submit_failure=OSError("original submit unknown")
        with f.patches():
            with self.assertRaises(OSError) as caught: await f.api.set_tariff("small_arms","import","tariffs_none")
        self.assertIs(caught.exception,f.submit_failure); owner=f.owners[0]
        self.assertEqual((owner.serializes,owner.validates,owner.submits,owner.destroys),(1,1,1,0))
        self.assertEqual(f.released,[]); self.assertIsNotNone(owner.pending)
        owner_record=next(record for record in f.ctx._economy_retained if record.get("retained_native_owner") is owner)
        self.assertEqual(set(owner_record["handles"]),f.live)
        self.assertIn(f.source_handle,owner_record["handles"])
        self.assertIn("generated",caught.exception.economy_native_results)
        self.assertEqual(f.command_requests,[C.Action.CLONE]); self.assertEqual(len(f.generations),1)

    async def test_pending_key_copy_keeps_inputs_and_resumes_same_query_without_replay(self):
        f=Fixture(count=3,target=2); f.block_label="Goods.GetKey"
        with f.patches():
            task=asyncio.create_task(f.api.set_tariff("small_arms","import","tariffs_none"))
            try:
                await asyncio.wait_for(f.blocked.wait(),1)
                self.assertFalse(task.done()); self.assertEqual(f.released,[]); self.assertEqual(f.generations,[])
                self.assertIn(f.source_handle,f.live); self.assertEqual(len(f.ctx.pending_queries),1)
                pending_query=f.ctx.pending_queries[0]
                self.assertEqual(pending_query.result.snapshot,b"good_0")
            finally:
                f.proceed.set()
                result=await asyncio.wait_for(task,1)
        self.assertTrue(result["accepted"]); self.assertTrue(f.did_block)
        self.assertEqual(f.ctx.pending_queries,[])
        self.assertEqual(Counter(expression for expression,_ in f.evaluated)["Market.GetGoodsSortedByBalance"],1)
        self.assertEqual(Counter(expression for expression,_ in f.evaluated)["Goods.GetKey"],2)
        self.assertEqual(len(f.generations),1); self.assertFalse(f.live)

    async def test_cleanup_fault_keeps_primary_receipt_and_remaining_retained_handles(self):
        f=Fixture(); f.release_failure=OSError("cleanup interrupted")
        with f.patches(): result=await f.api.set_tariff("small_arms","import","tariffs_none")
        self.assertTrue(result["accepted"]); self.assertIs(result["native_results"]["submit"],f.owners[0].submit_result)
        self.assertIs(result["cleanup_errors"][0],f.release_failure)
        retained=next(record for record in f.ctx._economy_retained if "retained_goods_control_values" in record)
        self.assertEqual(set(retained["retained_goods_control_values"]),f.live)
        self.assertTrue(f.live); self.assertEqual(f.released,[]); self.assertEqual(f.owners[0].submits,1)

if __name__=="__main__": unittest.main()
