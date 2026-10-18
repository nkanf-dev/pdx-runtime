"""Prewar original sources/actions through real shared owners; CPU fixtures only."""
import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.v3_game.diplomacy import plays
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.v3_game._choices import ChoicesBusyError
from pdx_runtime.query_executor import RetainedOwners
from tests.v3_game.test_v3_game_political_data import cell

class Context:
    def __init__(self):
        self.native=object();self.process=object();self.adapters={x:x+".dll" for x in ("data","commands","access","call")}
        self.world=0;self.diagnostics=[];self.owner_pool=RetainedOwners();self._types={"Country":1,"DiplomaticPlay":2,"WarGoal":3,"Sway":4,"WarGoalDraft":5,"SwayOffer":6,"ArticleDraft":7}
        self.performs=[];self.queries=[];self.hold=None;self.started=asyncio.Event();self.resume=asyncio.Event()
    async def type_id(self,name):return self._types[name]
    async def country_id(self):return 0
    async def perform(self,label,fn):self.performs.append(label);return await fn()
    async def query(self,label,factory,owner=None):
        self.queries.append(label);query=factory()
        if self.hold and self.hold in label:self.started.set();await self.resume.wait()
        return query.result
    def _remember(self,query):pass

class Owner:
    def __init__(self,f):self.f=f;self.world=0x10000;self.command=0x9000;self.pending=None;self.attempt=None
    def validate(self,capacity):self.f.validations+=1;self.attempt=NS(outcome="owned");return NS(result=self.f.validation)
    def submit(self,flags=4):
        self.f.submissions+=1;self.attempt=NS(outcome="consumed");self.command=0
        if self.f.apply:
            if "LeanTowardsInitiator" in self.f.expression:self.f.selected.values["DiplomaticPlay.IsInitiatorLeaning(Country.Self)"]=True
            if "MakeWarGoalPrimaryDemand" in self.f.expression:self.f.selected.values["WarGoal.IsPrimaryDemand"]=True
        return NS(result=NS(error=0,accepted=True))
    def destroy(self):self.command=0;self.attempt=NS(outcome="consumed");return NS(result=NS(error=0,command=0))

class Fixture:
    def __init__(self,ctx):
        self.ctx=ctx;self.handles={};self.lists={};self.next=1;self.raw=False;self.fail_readback=False;self.apply=True
        self.producers=self.clones=self.validations=self.submissions=0;self.calls=[];self.pages=[]
        self.validation=NS(error=0,validated=True,reason="native selected condition")
        self.play=NS(type="DiplomaticPlay",values={"DiplomaticPlay.GetName":"#bold First play#!","DiplomaticPlay.GetInitiator.GetID":0x80000002})
        self.goal=NS(type="WarGoal",values={"WarGoal.IsPrimaryDemand":False,"WarGoal.GetName":"Original goal"})
        self.sway=NS(type="Sway",values={"Sway.IsValid":True,"Sway.GetProposalDesc":"Original incoming offer"})
        self.play_rows=[self.play];self.expression=None;self.selected=None
        self.fields={f.expression:f for group in (plays._PLAY,plays._GOAL,plays._SWAY,plays._COUNTRY) for f in group}
    def retain(self,value,kind,action,model=None):
        h=self.next;self.next+=1;self.handles[h]=(value,model)
        if isinstance(value,data.NativeObject):value=data.NativeObject(value.pointer,value.type_id,h,value.native_type_name);self.handles[h]=(value,model)
        return NS(result=NS(error=0,action=action,value_handle=h,value=value,kind=kind),adapter=0x50000)
    def obj(self,model,action=data.Action.EVALUATE):
        prefix=".?AU?$SAnyType@PEAVCData" if self.raw else ".?AU?$SAnyType@V?$CPdxPtr@VCData"
        suffix="@@@PdxAnyImpl@@" if self.raw else "@@@@@PdxAnyImpl@@"
        query=self.retain(data.NativeObject(0x1000+id(model)%10000,self.ctx._types[model.type],0,prefix+model.type+suffix),data.Kind.OBJECT,action,model)
        query.result.native_type_name=prefix+model.type+suffix
        query.result.type_id=self.ctx._types[model.type]
        return query
    def model(self,bindings,named_type=None):
        for b in reversed(bindings):
            if b.retained:
                model=self.handles[b.object][1]
                if model is not None and (named_type is None or model.type==named_type):return model
        return None
    def evaluate(self,native,expression,bindings,**kwargs):
        self.calls.append(expression)
        if expression=="Country.AccessActiveDiplomaticPlays":rows=self.play_rows
        elif expression in ("DiplomaticPlay.GetInitiatorWarGoals","DiplomaticPlay.GetTargetWarGoals"):rows=[self.goal] if "Initiator" in expression else []
        elif "GetIncomingSway" in expression or "GetAcceptedSway" in expression:return self.obj(self.sway)
        else:
            assert any(x in expression for x in ("LeanTowardsInitiator","LeanTowardsTarget","BeOnTheFence","DeclareNeutrality","SupportInitiator","SupportTarget","SwitchSide","Yield","MakeWarGoalPrimaryDemand","AcceptSway","RejectSway","AddWarGoal","AddTreatyWarGoal","CreateSway")),expression
            self.producers+=1;self.expression=expression;self.selected=self.model(bindings)
            return self.retain(data.NativeDataCommand(0x2000,0x2010,0x2018,0,"CDataCommand",0x10000),data.Kind.COMMAND,data.Action.EVALUATE)
        query=self.retain(data.NativeList(0,0,b"","fixture"),data.Kind.LIST,data.Action.EVALUATE)
        self.lists[query.result.value_handle]=tuple(rows);return query
    def count(self,native,h,**kwargs):return NS(result=NS(error=0,total=len(self.lists[h])))
    def element(self,native,h,index,**kwargs):return self.obj(self.lists[h][index],data.Action.LIST_ELEMENT)
    def carrier(self,native,h,**kwargs):return NS(result=NS(error=0,action=data.Action.COMMAND_VALUE,value=self.handles[h][0]))
    def clone(self,native,action,**kwargs):
        assert action==commands.Action.CLONE;assert kwargs["world"]==0x10000
        self.clones+=1;return NS(result=NS(error=0,cloned_command=0x9000,owner=Owner(self)))
    def release(self,native,h,**kwargs):
        self.handles.pop(h);return NS(result=NS(error=0,action=data.Action.RELEASE,value_handle=0))
    def field(self,expression,model,participant=None):
        f=self.fields[expression]
        if participant is not None and expression=="Country.GetID":return participant
        if expression in model.values:return model.values[expression]
        if f.text:return "native original text"
        if f.kind==data.Kind.BOOL:return expression.endswith("IsValid")
        if f.kind==data.Kind.FIXED:return data.FixedPoint(0)
        return 0
    def project(self,native,fields,binding_rows,**kwargs):
        if self.fail_readback and self.submissions:raise OSError("original readback unknown")
        source=kwargs.get("source_expression");start=kwargs.get("start",0);limit=kwargs.get("limit",64)
        self.pages.append((tuple(fields),tuple(binding_rows),dict(kwargs)))
        assert len(fields)<=32 and limit<=64
        if source and "WarGoals" in source:
            models=[(self.goal,None)] if "Initiator" in source else []
        elif source:models=[(self.play,0),(self.play,0x80000002)]
        else:models=[(self.model(row),None) for row in binding_rows]
        total=len(models);models=models[start:start+limit]
        cells=tuple(cell(self.field(expr,model,part),f,r) for r,(model,part) in enumerate(models) for f,expr in enumerate(fields))
        return NS(result=data.ProjectionPage(0,0x10000,len(fields),start,17,total,True,len(models),cells),adapter=0x50000,cleanup_error=None)

class PlayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context();self.f=Fixture(self.ctx);self.api=DiplomacyAPI(self.ctx)
        for module,name,method in ((data,"evaluate",self.f.evaluate),(data,"list_count",self.f.count),(data,"list_element",self.f.element),
            (data,"command_value",self.f.carrier),(data,"release",self.f.release),(data,"project_page",self.f.project),
            (commands,"request",self.f.clone)):
            patch.object(module,name,side_effect=method).start()
        patch.object(commands.NativeCommand,"from_result",side_effect=lambda native,result,**kw:result.owner).start()
        self.addCleanup(patch.stopall)

    async def test_actual_play_choices_no_candidate_command_and_original_fields(self):
        catalog=await self.api.play_choices()
        self.assertEqual(len(catalog),1);self.assertEqual(catalog[0]["name"],"First play")
        self.assertEqual(catalog[0]["initiator_id"],0x80000002)
        self.assertEqual((self.f.producers,self.f.validations,self.f.submissions),(0,0,0))
        self.assertIn(catalog.owner,self.ctx.owner_pool.snapshot());await catalog.close()
        self.assertNotIn(catalog.owner,self.ctx.owner_pool.snapshot());self.assertEqual(self.f.handles,{})

    async def test_raw_list_rows_not_retagged_as_strong_choice(self):
        self.f.raw=True;catalog=await self.api.play_choices()
        self.assertEqual(len(catalog),0);self.assertEqual(len(catalog.metadata["result"]["rejected_rows"]),1)
        await catalog.close();self.assertEqual(self.f.handles,{})

    async def test_129_owned_rows_copy_descriptors_in_three_pages(self):
        self.f.play_rows=[self.f.play]*129;catalog=await self.api.play_choices()
        self.assertEqual(len(catalog),129);self.assertEqual(len(self.f.pages),3)
        self.assertEqual([len(p[1]) for p in self.f.pages],[64,64,1]);await catalog.close()

    async def test_selected_lean_once_uses_retained_choice_not_provider_lookup(self):
        catalog=await self.api.play_choices();self.f.calls.clear()
        result=await self.api.set_play_stance(catalog[0],"lean_initiator")
        self.assertEqual((self.f.producers,self.f.clones,self.f.validations,self.f.submissions),(1,1,1,1))
        self.assertTrue(result["accepted"]);self.assertTrue(result["completed"])
        self.assertEqual(self.f.calls,["DiplomaticPlay.LeanTowardsInitiator(Country.Self)"])
        self.assertEqual(len(self.ctx.performs),2);await catalog.close()

    async def test_accepted_stance_without_observed_change_not_complete(self):
        catalog=await self.api.play_choices();self.f.apply=False
        result=await self.api.set_play_stance(catalog[0],"lean_initiator")
        self.assertTrue(result["accepted"]);self.assertFalse(result["completed"]);await catalog.close()

    async def test_validation_rejection_has_no_submit(self):
        catalog=await self.api.play_choices();self.f.validation.validated=False
        result=await self.api.yield_play(catalog[0])
        self.assertFalse(result["accepted"]);self.assertEqual(self.f.submissions,0);await catalog.close()

    async def test_yield_acceptance_not_claimed_as_termination(self):
        catalog=await self.api.play_choices();result=await self.api.yield_play(catalog[0])
        self.assertTrue(result["accepted"]);self.assertFalse(result["completed"]);await catalog.close()

    async def test_real_participant_sources_preserve_full_ids_no_commands(self):
        catalog=await self.api.play_choices();result=await self.api.play_details(catalog[0])
        self.assertEqual(set(result["participants"]),set(plays._PARTICIPANTS))
        self.assertEqual(result["participants"]["undecided"]["rows"][1]["id"],0x80000002)
        self.assertEqual(self.f.producers,0);await catalog.close()

    async def test_goal_choice_keeps_parent_until_child_close_then_original_primary(self):
        catalog=await self.api.play_choices();goals=await self.api.play_goal_choices(catalog[0])
        with self.assertRaises(ChoicesBusyError):await catalog.close()
        result=await self.api.make_play_goal_primary(goals[0]);self.assertTrue(result["completed"])
        self.assertEqual(self.f.expression,"WarGoal.MakeWarGoalPrimaryDemand(Country.Self)")
        await goals.close();await catalog.close();self.assertEqual(self.f.handles,{})

    async def test_sway_response_original_proposal_no_outgoing_argument_guess(self):
        catalog=await self.api.play_choices();sways=await self.api.play_sway_choices(catalog[0],7)
        self.assertEqual(len(sways),2);self.assertFalse(any("GetOutgoingSway" in c for c in self.f.calls))
        result=await self.api.respond_to_sway(sways[0],accept=False)
        self.assertTrue(result["accepted"]);self.assertFalse(result["completed"])
        self.assertEqual(self.f.expression,"Sway.RejectSway");await sways.close();await catalog.close()

    async def test_action_continuation_keeps_parent_until_original_submit_finishes(self):
        catalog=await self.api.play_choices();self.ctx.hold="command.submit"
        action=asyncio.create_task(self.api.yield_play(catalog[0],readback=False));await self.ctx.started.wait()
        with self.assertRaises(ChoicesBusyError):await catalog.close()
        self.ctx.resume.set();self.assertTrue((await action)["accepted"])
        await catalog.close();self.assertEqual(self.f.submissions,1)

    async def test_unknown_after_read_preserves_accepted_and_catalog_graph(self):
        catalog=await self.api.play_choices();self.f.fail_readback=True
        result=await self.api.yield_play(catalog[0])
        self.assertTrue(result["accepted"]);self.assertIn("readback_error",result);self.assertEqual(self.f.submissions,1)
        with self.assertRaises(ChoicesBusyError):await catalog.close()

    async def test_local_controls_fail_without_native_submission(self):
        with self.assertRaises(TypeError):await self.api.yield_play(0)
        with self.assertRaises(ValueError):
            catalog=await self.api.play_choices();await self.api.set_play_stance(catalog[0],"invented_side")
        self.assertEqual(self.f.producers,0);await catalog.close()

    async def actual_draft(self,named_type):
        from pdx_runtime.v3_game._run import Run as _Run
        from pdx_runtime.v3_game._choices import OwnedChoices
        run=_Run(self.ctx,"draft_fixture",namespace="diplomacy")
        result=await run.capture("native.original.draft",lambda:self.f.obj(NS(type=named_type,values={})))
        catalog=OwnedChoices(run)
        catalog.add(result,named_type,data.ObjectBinding(self.ctx._types[named_type],result.value_handle,retained=True),{})
        return catalog

    async def test_add_original_populated_draft_once_no_goal_reconstruction(self):
        plays_catalog=await self.api.play_choices();drafts=await self.actual_draft("WarGoalDraft")
        result=await self.api.add_play_goal(plays_catalog[0],drafts[0])
        self.assertEqual(self.f.expression,"DiplomaticPlay.AddWarGoal(WarGoalDraft.Self)")
        self.assertEqual((self.f.producers,self.f.validations,self.f.submissions),(1,1,1))
        self.assertTrue(result["accepted"]);self.assertFalse(result["completed"])
        self.assertIn("goals",result["after"]);await drafts.close();await plays_catalog.close()

    async def test_actual_article_draft_uses_treaty_goal_original_producer(self):
        play_catalog=await self.api.play_choices();drafts=await self.actual_draft("WarGoalDraft");articles=await self.actual_draft("ArticleDraft")
        result=await self.api.add_play_goal(play_catalog[0],drafts[0],article_choice=articles[0],readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.f.expression,"DiplomaticPlay.AddTreatyWarGoal(WarGoalDraft.Self,ArticleDraft.Self)")
        await articles.close();await drafts.close();await play_catalog.close()

    async def test_sway_sender_is_original_getplayer_only_target_country_bound(self):
        play_catalog=await self.api.play_choices();offers=await self.actual_draft("SwayOffer")
        result=await self.api.send_sway(play_catalog[0],0x80000002,offers[0],readback=False)
        self.assertTrue(result["accepted"]);self.assertEqual(result["target_country_id"],0x80000002)
        self.assertEqual(self.f.expression,"DiplomaticPlay.CreateSway(GetPlayer.Self,Country.Self,SwayOffer.Self)")
        self.assertEqual(self.f.submissions,1);await offers.close();await play_catalog.close()

if __name__=="__main__":unittest.main()
