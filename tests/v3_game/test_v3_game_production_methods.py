"""CPU PM composition tests. No game, process, DLL or native call is made."""
from __future__ import annotations

import copy
import struct
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pdx_runtime import v3_commands as commands, native_call
from pdx_runtime.v3_game.economy import production_methods as pm
from pdx_runtime.v3_game import _text as localizer

BUILDING=0xAB000007
ACTOR=156
OLD="pm_scholastic"; TARGET="pm_philosophy"


def snapshot(selected=OLD, world=70000):
    choices=[{"key":key,"name":key,"selected":key==selected} for key in (OLD,TARGET)]
    return {"building_id":BUILDING,"building_key":"building_university","state_id":23,
        "country_id":ACTOR,"world":world,"groups":[{"key":"pmg_education","name":"教育",
        "current_keys":[selected],"current_observed":True,"candidates":choices}],
        "display":{"groups":[{"key":"pmg_education","name":"教育","current":{"key":selected,"name":selected},
            "candidates":copy.deepcopy(choices)}],"effects":{}}}


class Query:
    def __init__(self,result,finish=None):self.result=result;self.finish=finish
    def read(self):
        if self.finish:
            finish,self.finish=self.finish,None;finish()
        return self.result


class Context:
    def __init__(self):
        self.native,self.process,self.session=object(),object(),object()
        self.adapters={name:name+"-adapter" for name in ("data","objects","commands","access","call","runtime")}
        self.world,self.image_base=70000,0x140000000
        self.diagnostics,self.queries=[],[]
        self.unknown_submit=False
    async def refresh(self):return NS(world=self.world)
    async def country_id(self):return ACTOR
    async def type_id(self,name):return 902
    async def query(self,label,factory,owner=None):
        query=factory();self.queries.append((label,query,owner))
        if label=="production_methods.submit" and self.unknown_submit:
            owner.attempt.outcome="unknown";raise RuntimeError("original handoff retained")
        return owner.complete() if owner else query.read()


class Owner:
    def __init__(self,ctx,actor,parameters,*,valid=True,accepted=True):
        self.world,self.type_token,self.command=ctx.world,13208,0x12300
        self.actor,self.parameters=actor,parameters
        self.valid,self.accepted=valid,accepted
        self.pending,self.attempt=None,None
        self.calls,self.capacities=[],[]
    def request(self,label,result,consumed=False):
        self.calls.append(label)
        def finish():
            self.pending=None;self.attempt=NS(outcome="consumed" if consumed else "owned")
            if consumed:self.command=0
        self.pending=Query(result,finish);self.attempt=NS(outcome="pending")
        return self.pending
    def complete(self):return self.pending.read()
    def serialize(self):return self.request("serialize",NS(error=0,data=commands.encode_command(13208,
        {10304:commands.UInt32(self.actor),65:self.parameters})))
    def validate(self,capacity):
        self.capacities.append(capacity)
        return self.request("validate",NS(error=0,validated=int(self.valid),reason=""))
    def submit(self,flags):
        self.flags=flags
        return self.request("submit",NS(error=0,accepted=int(self.accepted),command=0),True)
    def destroy(self):return self.request("destroy",NS(error=0,command=0),True)


class ProductionMethodsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context();self.creates=[];self.owners=[];self.valid=True;self.accepted=True
        self.valid_by_key={}
        def create(native,token,*,actor_id,parameters,adapter,process):
            self.assertIs(native,self.ctx.native);self.assertIs(process,self.ctx.process)
            self.assertEqual(token,13208);self.assertEqual(adapter,"commands-adapter")
            self.creates.append((actor_id,parameters))
            owner=Owner(self.ctx,actor_id,parameters,valid=self.valid_by_key.get(parameters[13209],self.valid),accepted=self.accepted)
            self.owners.append(owner)
            return Query(NS(error=0,owner=owner))
        self.patchers=[patch.object(pm.commands,"create",side_effect=create),
            patch.object(pm.commands.NativeCommand,"from_result",side_effect=lambda native,result,**kw:result.owner)]
        for patcher in self.patchers:patcher.start();self.addCleanup(patcher.stop)

    async def test_read_uses_native_allowed_not_presence_and_cleans_without_submit(self):
        self.valid_by_key[TARGET]=False
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot())):
            result=await pm.read_production_methods(self.ctx,BUILDING,details=False)
        choices=result["groups"][0]["candidates"]
        self.assertEqual([row["key"] for row in choices],[OLD,TARGET])
        self.assertTrue(choices[0]["selected"])
        self.assertFalse(choices[1]["allowed"])
        self.assertFalse(choices[1]["reason_complete"])
        self.assertTrue(all(owner.calls==["serialize","validate","destroy"] for owner in self.owners))
        self.assertTrue(all(owner.capacities==[0] for owner in self.owners))
        self.assertTrue(all(owner.command==0 for owner in self.owners))

    async def test_set_exact_key_native_wire_once_and_fresh_current_readback(self):
        with patch.object(pm,"_snapshot",AsyncMock(side_effect=[snapshot(),snapshot(),snapshot(TARGET)])) as read:
            result=await pm.set_production_method(self.ctx,BUILDING,{"key":TARGET})
        self.assertTrue(result["accepted"]);self.assertTrue(result["effect_observed"])
        self.assertEqual(result["status"],"selected")
        actor,parameters=self.creates[0]
        self.assertEqual(actor,ACTOR)
        self.assertEqual(list(parameters),[10232,21409,13209])
        self.assertEqual(parameters[10232],[commands.UInt32(BUILDING)])
        self.assertEqual(parameters[21409],"building_university")
        self.assertEqual(parameters[13209],TARGET)
        self.assertEqual(self.owners[0].calls.count("submit"),1)
        self.assertEqual(self.owners[0].capacities,[0,0])
        self.assertEqual(self.owners[0].flags,4)
        self.assertEqual(read.await_count,3)
        self.assertIn("not a claim",result["effect_scope"])

    async def test_rejected_native_rule_never_submits_or_selects_an_alternative(self):
        self.valid=False
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot())):
            result=await pm.set_production_method(self.ctx,BUILDING,TARGET)
        self.assertFalse(result["allowed"]);self.assertFalse(result["submitted"])
        self.assertEqual(self.owners[0].calls,["serialize","validate","destroy"])
        self.assertEqual(len(self.creates),1)
        self.assertEqual(self.creates[0][1][13209],TARGET)

    async def test_receiver_rejection_is_consumed_not_replayed(self):
        self.accepted=False
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot())):
            result=await pm.set_production_method(self.ctx,BUILDING,TARGET)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"],"receiver_rejected")
        self.assertEqual(self.owners[0].command,0)
        self.assertEqual(self.owners[0].calls.count("submit"),1)
        self.assertNotIn("destroy",self.owners[0].calls)

    async def test_already_current_is_observed_without_fabricated_acceptance(self):
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot(TARGET))):
            result=await pm.set_production_method(self.ctx,BUILDING,TARGET)
        self.assertEqual(result["status"],"already_selected")
        self.assertTrue(result["effect_observed"])
        self.assertFalse(result["submitted"])
        self.assertIsNone(result["accepted"])
        self.assertFalse(self.creates)

    async def test_known_accepted_pending_effect_repeated_call_only_observes(self):
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot())):
            first=await pm.set_production_method(self.ctx,BUILDING,TARGET)
            second=await pm.set_production_method(self.ctx,BUILDING,TARGET)
        self.assertTrue(first["accepted"]);self.assertFalse(first["effect_observed"])
        self.assertFalse(second["effect_observed"])
        self.assertEqual(len(self.creates),1)
        self.assertEqual(self.owners[0].calls.count("submit"),1)
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot(TARGET))):
            third=await pm.set_production_method(self.ctx,BUILDING,TARGET)
        self.assertTrue(third["effect_observed"])
        self.assertEqual(len(self.creates),1)

    async def test_unknown_handoff_keeps_original_owner_query_and_never_reissues(self):
        self.ctx.unknown_submit=True
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot())):
            with self.assertRaisesRegex(RuntimeError,"original handoff"):
                await pm.set_production_method(self.ctx,BUILDING,TARGET)
            original=self.owners[0].pending
            result=await pm.set_production_method(self.ctx,BUILDING,TARGET)
        self.assertEqual(result["status"],"original_handoff_retained")
        self.assertIsNone(result["accepted"])
        self.assertIs(self.owners[0].pending,original)
        self.assertEqual(len(self.creates),1)
        self.assertEqual(self.owners[0].calls.count("submit"),1)
        self.assertNotIn("destroy",self.owners[0].calls)

    async def test_invalid_or_unobserved_key_does_not_change_first_available_method(self):
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot())):
            result=await pm.set_production_method(self.ctx,BUILDING,"pm_unrelated")
        self.assertFalse(result["submitted"])
        self.assertFalse(self.creates)
        for bad in (True,0xffffffff,-1):
            with self.assertRaises(ValueError):await pm.set_production_method(self.ctx,bad,TARGET)

    async def test_observed_numeric_id_resolves_by_native_key_not_list_position(self):
        observed={"groups":[],"effects":{TARGET:{"id":98}}}
        with patch.object(pm,"_snapshot",AsyncMock(side_effect=[snapshot(),snapshot(),snapshot(TARGET)])), \
             patch.object(pm,"_display",AsyncMock(return_value=observed)):
            result=await pm.set_production_method(self.ctx,BUILDING,98)
        self.assertTrue(result["effect_observed"])
        self.assertEqual(self.creates[0][1][13209],TARGET)
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot())), \
             patch.object(pm,"_display",AsyncMock(return_value=observed)):
            result=await pm.set_production_method(self.ctx,BUILDING,0)
        self.assertFalse(result["submitted"])
        self.assertEqual(len(self.creates),1)


    async def test_unsupported_provider_uses_single_callback_fallback_not_a_pointer_cast(self):
        fallback=snapshot()
        with patch.object(pm,"_identity",AsyncMock(return_value=(NS(state_id=23),ACTOR))), \
             patch.object(pm.CountryAPI,"_building_key",AsyncMock(return_value=("building_university",None))), \
             patch.object(pm.building_status,"_production_methods",AsyncMock(return_value={"groups":None,"reasons":{"groups":"borrowed"}})), \
             patch.object(pm,"_snapshot_fallback",AsyncMock(return_value=fallback)) as copied:
            result=await pm._snapshot(self.ctx,BUILDING)
        self.assertEqual(result["groups"][0]["current_keys"],[OLD])
        copied.assert_awaited_once_with(self.ctx,BUILDING)

    async def test_details_include_only_genuine_compact_summaries_and_numeric_id(self):
        observed={"groups":snapshot()["display"]["groups"],"effects":{TARGET:{"id":98,
            "goods_effects":{"text":"Paper +5","raw_text":"native goods","complete":True},
            "employment_effects":{"text":"Academics +500","raw_text":"native jobs","complete":True}}}}
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot())), \
             patch.object(pm,"_display",AsyncMock(return_value=observed)):
            result=await pm.read_production_methods(self.ctx,BUILDING,details=True)
        choice=result["groups"][0]["candidates"][1]
        self.assertEqual(choice["id"],98)
        self.assertEqual(choice["goods_effects"]["text"],"Paper +5")
        self.assertEqual(choice["employment_effects"]["text"],"Academics +500")
        self.assertIsNone(choice["qualification_warning"])
        self.assertIn("both Building",choice["qualification_warning_reason"])
        self.assertFalse(any("submit" in owner.calls for owner in self.owners))

    async def test_real_context_timeout_resumes_same_original_submission(self):
        import asyncio
        from pdx_runtime.v3_game._core import GameContext, PendingOperation
        real=GameContext(NS(native=self.ctx.native),self.ctx.process,timeout=.001,poll_interval=.001)
        real.world=self.ctx.world;real.refresh=AsyncMock();real.country_id=AsyncMock(return_value=ACTOR)
        real.adapters.update(self.ctx.adapters)
        gate={"ready":False}
        submit_observed=asyncio.Event()
        loop=asyncio.get_running_loop()
        def adopt(native,result,**kw):
            owner=result.owner; submit=owner.submit
            def delayed(flags):
                query=submit(flags);read=query.read
                def held_read():
                    loop.call_soon_threadsafe(submit_observed.set)
                    return read() if gate["ready"] else None
                query.read=held_read
                return query
            owner.submit=delayed
            return owner
        with patch.object(pm.commands.NativeCommand,"from_result",side_effect=adopt), \
             patch.object(pm,"_snapshot",AsyncMock(side_effect=[snapshot(),snapshot(),snapshot(TARGET)])):
            pending=await real.perform("economy.set_production_method",lambda:pm.set_production_method(real,BUILDING,TARGET))
            self.assertIsInstance(pending,PendingOperation)
            await asyncio.wait_for(submit_observed.wait(),timeout=2)
            original=self.owners[0].pending
            self.assertIs(await pending.resume(timeout=.001),pending)
            self.assertIs(self.owners[0].pending,original)
            gate["ready"]=True
            result=await pending.resume(timeout=2)
        self.assertTrue(result["effect_observed"])
        self.assertEqual(len(self.creates),1)
        self.assertEqual(self.owners[0].calls.count("submit"),1)
        self.assertEqual(real.pending_queries,[])

    async def test_snapshot_owned_buffer_cleanup_after_known_copy_error(self):
        self.ctx.process=NS(allocate=Mock(return_value=0x9000),write=Mock(),read=Mock(side_effect=OSError("host copy")),
            free=Mock(),resolve_export=Mock(return_value=0x1000))
        recipe=NS(storage_size=16,ops=[1,2],initial_storage=lambda full_id:bytes(16),
            compose=lambda **kwargs:(b"initial",lambda address:b"prepared"))
        batch=NS(error=0,completed=2,calls=[NS(error=0),NS(error=0)])
        scope=NS(error=0,entered=1,left=1,callee_result=batch)
        with patch.object(pm,"_PMSnapshotRecipe",return_value=recipe), \
             patch.object(pm.v3_access,"submit_callback",return_value=Query(scope)):
            with self.assertRaisesRegex(OSError,"host copy"):
                await pm._snapshot_fallback(self.ctx,BUILDING)
        self.ctx.process.free.assert_called_once_with(0x9000)
        self.assertIsNone(self.ctx._production_method_operations[-1]["storage"])

    async def test_unknown_snapshot_prefix_retains_owned_storage(self):
        self.ctx.process=NS(allocate=Mock(return_value=0x9000),write=Mock(),read=Mock(),free=Mock(),
            resolve_export=Mock(return_value=0x1000))
        recipe=NS(storage_size=16,ops=[1,2],initial_storage=lambda full_id:bytes(16),
            compose=lambda **kwargs:(b"initial",lambda address:b"prepared"))
        scope=NS(error=0,entered=1,left=0,callee_result=NS(error=0,completed=1,calls=[NS(error=0)]))
        with patch.object(pm,"_PMSnapshotRecipe",return_value=recipe), \
             patch.object(pm.v3_access,"submit_callback",return_value=Query(scope)):
            with self.assertRaisesRegex(RuntimeError,"prefix retained"):
                await pm._snapshot_fallback(self.ctx,BUILDING)
        self.ctx.process.free.assert_not_called()
        self.ctx.process.read.assert_not_called()
        self.assertEqual(self.ctx._production_method_operations[-1]["storage"],0x9000)

    def test_recipe_patches_owned_dynamic_arguments_inside_one_native_batch(self):
        recipe=pm._PMSnapshotRecipe(group_capacity=2,methods_per_group=2,active_capacity=2)
        initial,prepare=recipe.compose(image_base=0x140000000,resolver_address=0x1111,runtime_read_address=0x2222,
            storage=0x300000,full_building_id=BUILDING)
        payload=prepare(0x500000)
        first_args=struct.unpack_from("<Q",payload,32+24)[0]
        self.assertEqual(struct.unpack_from("<Q",payload,first_args-0x500000)[0],BUILDING)
        for copy_index,target_index,arg in recipe.links:
            copy_args=struct.unpack_from("<Q",payload,32+copy_index*56+24)[0]
            target_args=struct.unpack_from("<Q",payload,32+target_index*56+24)[0]
            self.assertEqual(struct.unpack_from("<Q",payload,copy_args-0x500000+8)[0],target_args+arg*8)
        self.assertEqual(recipe.storage_size,len(recipe.initial_storage(BUILDING)))


    async def test_world_change_after_acceptance_is_not_claimed_as_old_world_effect(self):
        newer=snapshot(TARGET,world=self.ctx.world+1)
        with patch.object(pm,"_snapshot",AsyncMock(side_effect=[snapshot(),snapshot(),newer])):
            result=await pm.set_production_method(self.ctx,BUILDING,TARGET)
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["effect_observed"])
        self.assertEqual(result["status"],"accepted_world_changed")
        self.assertEqual(self.owners[0].calls.count("submit"),1)

    async def test_compact_effects_use_true_retained_method_context_and_preserve_source_graph(self):
        from pdx_runtime.v3_game.country import CountryAPI, _Read
        from pdx_runtime import v3_data as data
        helper=NS(read_retained_text=AsyncMock(side_effect=lambda ctx,kind,handle,expression:{
            "text":expression,"raw_text":"native "+expression,"text_complete":True,
            "context_supplied":True,"native_temporaries_destroyed":True,"decode_error":None,
            "world":ctx.world,"localization_success":None}))
        api=CountryAPI(self.ctx);read=_Read(api)
        group_source=NS(error=0,value_handle=100,value=data.NativeList(100,0,b"","model"))
        method_source=NS(error=0,value_handle=102,value=data.NativeList(102,0,b"","model"))
        group=NS(error=0,value_handle=200,value=data.NativeObject(0x2000,902,200,"CPdxPtr@VCDataProductionMethodGroup@@"))
        method=NS(error=0,value_handle=201,value=data.NativeObject(0x3000,902,201,"CPdxPtr@VCDataProductionMethod@@"))
        released=[]
        with patch.object(localizer,"read_retained_text",helper.read_retained_text), \
             patch.object(api,"_function",AsyncMock(return_value=(NS(),None))), \
             patch.object(pm.data,"evaluate",side_effect=[Query(group_source),Query(method_source)]), \
             patch.object(pm.data,"list_count",side_effect=[Query(NS(error=0,total=1)),Query(NS(error=0,total=1))]), \
             patch.object(pm.data,"list_element",side_effect=[Query(group),Query(method)]), \
             patch.object(pm.building_status,"_string",AsyncMock(return_value=(TARGET,None))), \
             patch.object(pm.building_status,"_value",AsyncMock(return_value=(98,None))), \
             patch.object(pm.data,"release",side_effect=lambda native,h,**kw:(released.append(h) or Query(NS(error=0,value_handle=0)))):
            values,_=await pm._data_effects(self.ctx,api,read,data.ObjectBinding.building(902,BUILDING))
            self.assertEqual(released,[])
            self.assertEqual(values[TARGET]["id"],98)
            self.assertTrue(values[TARGET]["goods_effects"]["complete"])
            self.assertTrue(values[TARGET]["employment_effects"]["complete"])
            self.assertIsNone(values[TARGET]["goods_effects"]["localization_success"])
            for call in helper.read_retained_text.await_args_list:
                self.assertEqual(call.args[1:3],("ProductionMethod",201))
            await read.close()
        self.assertEqual(released,[201,102,200,100])

    async def test_borrowed_group_never_becomes_an_owned_effects_context(self):
        from pdx_runtime.v3_game.country import CountryAPI, _Read
        from pdx_runtime import v3_data as data
        helper=NS(read_retained_text=AsyncMock())
        api=CountryAPI(self.ctx);read=_Read(api)
        source=NS(error=0,value_handle=100,value=data.NativeList(100,0,b"","model"))
        borrowed=NS(error=50,value_handle=0,value=None)
        with patch.object(localizer,"read_retained_text",helper.read_retained_text), \
             patch.object(api,"_function",AsyncMock(return_value=(NS(),None))), \
             patch.object(pm.data,"evaluate",return_value=Query(source)) as evaluate, \
             patch.object(pm.data,"list_count",return_value=Query(NS(error=0,total=1))), \
             patch.object(pm.data,"list_element",return_value=Query(borrowed)), \
             patch.object(pm.data,"release",return_value=Query(NS(error=0,value_handle=0))):
            values,gap=await pm._data_effects(self.ctx,api,read,data.ObjectBinding.building(902,BUILDING))
            await read.close()
        self.assertEqual(values,{})
        self.assertIn("not available",gap)
        self.assertEqual(evaluate.call_count,1)
        helper.read_retained_text.assert_not_awaited()

    async def test_full_effects_are_not_inferred_from_blank_goods_summary(self):
        full={"text":"Bureaucracy +10","raw_text":"native bureaucracy +10","complete":True,
            "scope":"production_method_for_player_country","building_specific":False,
            "source_expression":"ProductionMethod.GetTooltip(GetPlayer.Self)","reason":None}
        observed={"groups":snapshot()["display"]["groups"],"effects":{TARGET:{
            "goods_effects":{"text":"","raw_text":"","complete":True},
            "employment_effects":{"text":"","raw_text":"","complete":True},"full_effects":full}}}
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot())), \
             patch.object(pm,"_display",AsyncMock(return_value=observed)):
            result=await pm.read_production_methods(self.ctx,BUILDING,details=True)
        choice=result["groups"][0]["candidates"][1]
        self.assertEqual(choice["goods_effects"]["text"],"")
        self.assertEqual(choice["full_effects"]["text"],"Bureaucracy +10")
        self.assertFalse(choice["full_effects"]["building_specific"])
        self.assertIsNone(choice["full_effects_reason"])
        self.assertFalse(any("submit" in owner.calls for owner in self.owners))

    async def test_missing_full_effects_do_not_mean_no_non_goods_modifiers(self):
        observed={"groups":snapshot()["display"]["groups"],"effects":{TARGET:{
            "goods_effects":{"text":"","raw_text":"","complete":True}}}}
        with patch.object(pm,"_snapshot",AsyncMock(return_value=snapshot())), \
             patch.object(pm,"_display",AsyncMock(return_value=observed)):
            result=await pm.read_production_methods(self.ctx,BUILDING,details=True)
        choice=result["groups"][0]["candidates"][1]
        self.assertIsNone(choice["full_effects"])
        self.assertIn("not returned",choice["full_effects_reason"])

    async def test_confirmed_full_tooltip_uses_single_country_argument_and_same_retained_pm(self):
        read=NS(world=self.ctx.world,uncertain=False)
        api=NS(_function=AsyncMock(return_value=(NS(),None)))
        helper=NS(read_retained_text=AsyncMock(return_value={"text":"Bureaucracy +10",
            "raw_text":"native full tooltip","text_complete":True,"context_supplied":True,
            "native_temporaries_destroyed":True,"world":self.ctx.world}))
        with patch.object(pm,"_FULL_EFFECTS_EXPRESSION","GetTooltip(GetPlayer.Self)"):
            result=await pm._full_effects(self.ctx,api,read,helper,254,201)
        api._function.assert_awaited_once_with(read,"ProductionMethod",254,"GetTooltip",argument_count=1)
        helper.read_retained_text.assert_awaited_once_with(self.ctx,"ProductionMethod",201,
            "ProductionMethod.GetTooltip(GetPlayer.Self)")
        self.assertTrue(result["complete"])
        self.assertEqual(result["scope"],"production_method_for_player_country")
        self.assertFalse(result["building_specific"])

    async def test_full_tooltip_missing_native_signature_is_explicit_and_not_called(self):
        read=NS(world=self.ctx.world,uncertain=False)
        api=NS(_function=AsyncMock(return_value=(None,"GetTooltip has no supported readonly one-argument signature")))
        helper=NS(read_retained_text=AsyncMock())
        with patch.object(pm,"_FULL_EFFECTS_EXPRESSION","GetTooltip(GetPlayer.Self)"):
            result=await pm._full_effects(self.ctx,api,read,helper,254,201)
        self.assertFalse(result["complete"])
        self.assertIsNone(result["text"])
        self.assertIn("one-argument",result["reason"])
        helper.read_retained_text.assert_not_awaited()

if __name__=="__main__":unittest.main()
