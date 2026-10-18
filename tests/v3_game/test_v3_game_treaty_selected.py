"""Selected treaty native-owner compositions on CPU fixtures only."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands, v3_access as access
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from tests.v3_game.test_v3_game_political_data import cell


class Process:
    def __init__(self):self.memory={};self.next=0x20000
    def allocate(self,size):self.next+=0x100;self.memory[self.next]=bytes(size);return self.next
    def write(self,p,b):self.memory[p]=b
    def read(self,p,size):return self.memory[p][:size]
    def free(self,p):self.memory.pop(p,None)
    def resolve_export(self,*args):return 0x30000


class Context:
    def __init__(self):
        self.world=0 # stale local state must not reject genuine producer world
        self.native=object();self.process=Process();self._types={"Country":5,"Treaty":6}
        self.adapters={x:x+".dll" for x in ("data","commands","access","call")}
        self.diagnostics=[];self.performs=[];self.labels=[]
    async def country_id(self):return 0
    async def type_id(self,name):return self._types[name]
    async def perform(self,label,fn):self.performs.append(label);return await fn()
    async def query(self,label,factory,**kwargs):self.labels.append(label);return factory().result


class Owner:
    def __init__(self,f):self.f=f;self.world=0x5000;self.command=0x9000;self.pending=None;self.attempt=None
    def validate(self,capacity):self.f.validations+=1;self.attempt=NS(outcome="owned");return NS(result=self.f.validation)
    def submit(self,flags=4):
        self.f.submissions+=1;self.attempt=NS(outcome="consumed");self.command=0
        if self.f.accepted and self.f.apply:self.f.ids.remove(self.f.selected)
        return NS(result=NS(error=0,accepted=self.f.accepted))
    def destroy(self):self.command=0;self.attempt=NS(outcome="consumed");return NS(result=NS(error=0,command=0))


class Fixture:
    def __init__(self,ctx):
        self.ctx=ctx;self.ids=[0,0x80000002];self.handles={};self.lists={};self.refs={};self.counter=1
        self.producers=self.clones=self.validations=self.submissions=0
        self.validation=NS(error=0,validated=True,reason="native penalty reason")
        self.accepted=True;self.apply=True;self.selected=None;self.readback_error=False
    def value(self,value,kind,action=data.Action.EVALUATE):
        h=self.counter;self.counter+=1;self.handles[h]=value
        return NS(result=NS(error=0,action=action,value_handle=h,value=value,kind=kind))
    def evaluate(self,native,expr,bindings,**kwargs):
        if expr in ("Country.GetInForceTreaties","Country.GetDraftTreaties"):
            if self.readback_error and self.submissions:raise OSError("readback copy failed")
            q=self.value(data.NativeList(0,0,b"","fixture"),data.Kind.LIST)
            self.lists[q.result.value_handle]=tuple(self.ids);return q
        if expr=="Treaty.WithdrawFromTreaty":
            self.producers+=1;self.selected=self.refs[bindings[0].object]
            return self.value(data.NativeDataCommand(0x8000,0x8010,0x8018,0,"CDataCommand",0x5000),data.Kind.COMMAND)
        raise AssertionError(expr)
    def count(self,native,h,**kwargs):return NS(result=NS(error=0,total=len(self.lists[h])))
    def element(self,native,h,index,**kwargs):
        ref=self.lists[h][index];pointer=0x10000+index*0x100
        self.ctx.process.memory[pointer+0x10]=ref.to_bytes(4,"little")
        q=self.value(data.NativeObject(pointer,6,0,".?AU?$SAnyType@V?$CPdxPtr@VCDataTreaty@@@@@PdxAnyImpl@@"),data.Kind.OBJECT,data.Action.LIST_ELEMENT)
        q.result.native_type_name=".?AU?$SAnyType@V?$CPdxPtr@VCDataTreaty@@@@@PdxAnyImpl@@"
        q.result.type_id=self.ctx._types["Treaty"]
        self.refs[q.result.value_handle]=ref;return q
    def copy(self,native,function,args,**kwargs):
        dst,src,size=args;self.ctx.process.write(dst,self.ctx.process.read(src,size))
        return NS(result=NS(error=0,callee_result=NS(error=0,rax=dst)))
    def carrier(self,native,h,**kwargs):return NS(result=NS(error=0,action=data.Action.COMMAND_VALUE,value=self.handles[h]))
    def clone(self,native,action,**kwargs):
        assert action==commands.Action.CLONE;assert kwargs["world"]==0x5000
        self.clones+=1;return NS(result=NS(error=0,cloned_command=0x9000,owner=Owner(self)))
    def release(self,native,h,**kwargs):self.handles.pop(h);return NS(result=NS(error=0,action=data.Action.RELEASE,value_handle=0))
    def project(self,native,fields,roots,**kwargs):
        assert roots[0][0].retained and roots[0][0].object in self.refs
        values={"Treaty.GetName":"#bold Treaty#!","Treaty.GetFirstCountry.GetID":0,
            "Treaty.GetSecondCountry.GetID":7,"Treaty.GetBindingDays":3650,"Treaty.GetBindingDaysLeft":0,
            "Treaty.GetBindingPeriodEndGameDate.GetString":"1846.1.1","Treaty.GetBindingPeriodRemainingFormatted":"0 days",
            "Treaty.IsFrozenForBothCountries":False}
        cells=tuple(cell(values.get(e,"native original explanation"),i) for i,e in enumerate(fields))
        return NS(result=data.ProjectionPage(0,0x5000,len(fields),0,1,1,True,1,cells),cleanup_error=None)


class TreatySelectedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context();self.f=Fixture(self.ctx);self.api=DiplomacyAPI(self.ctx)
        for module,name,fn in ((data,"evaluate",self.f.evaluate),(data,"list_count",self.f.count),
            (data,"list_element",self.f.element),(data,"command_value",self.f.carrier),
            (data,"release",self.f.release),(data,"project_page",self.f.project),
            (commands,"request",self.f.clone),(access,"submit_call",self.f.copy)):
            patch.object(module,name,side_effect=fn).start()
        patch.object(commands.NativeCommand,"from_result",side_effect=lambda client,result,**kw:result.owner).start()
        self.addCleanup(patch.stopall)

    async def test_full_id_withdraw_once_and_actual_absence(self):
        result=await self.api.withdraw_treaty(0x80000002)
        self.assertEqual((self.f.producers,self.f.clones,self.f.validations,self.f.submissions),(1,1,1,1))
        self.assertTrue(result["accepted"]);self.assertTrue(result["completed"])
        self.assertEqual(result["status"],"withdrawn");self.assertEqual(self.f.handles,{})
        self.assertEqual(len(self.ctx.performs),1)

    async def test_zero_id_is_selected_not_treated_absent(self):
        result=await self.api.withdraw_treaty(0)
        self.assertEqual(self.f.selected,0);self.assertTrue(result["completed"])

    async def test_native_rejection_no_submit(self):
        self.f.validation.validated=False
        result=await self.api.withdraw_treaty(0)
        self.assertFalse(result["accepted"]);self.assertEqual(self.f.submissions,0)
        self.assertIs(result["validation"],self.f.validation);self.assertEqual(self.f.handles,{})

    async def test_acceptance_not_promoted_to_effect(self):
        self.f.apply=False
        result=await self.api.withdraw_treaty(0)
        self.assertTrue(result["accepted"]);self.assertFalse(result["completed"])
        self.assertTrue(result["after"]["current_country_membership"])

    async def test_readback_error_does_not_hide_accepted_or_replay(self):
        self.f.readback_error=True
        result=await self.api.withdraw_treaty(0)
        self.assertTrue(result["accepted"]);self.assertIn("readback_error",result)
        self.assertEqual(self.f.submissions,1);self.assertTrue(self.ctx.owner_pool.snapshot())

    async def test_unknown_id_not_first_row_fallback(self):
        result=await self.api.withdraw_treaty(3)
        self.assertEqual(result["status"],"unavailable");self.assertEqual(self.f.producers,0)
        self.assertEqual(self.f.handles,{})

    async def test_commitment_reads_real_selected_treaty_and_zero_days(self):
        result=await self.api.treaty_commitment(0x80000002)
        self.assertEqual(result["id"],0x80000002);self.assertEqual(result["binding_days_left"],0)
        self.assertFalse(result["frozen_for_both"]);self.assertEqual(result["name"],"Treaty")
        self.assertEqual((self.f.producers,self.f.clones,self.f.validations,self.f.submissions),(0,0,0,0))
        self.assertEqual(self.f.handles,{})

if __name__=="__main__":unittest.main()
