"""CPU tariff read -> set roundtrips over the real high-level command path.

Only native boundary outputs are fixtures. No game/PID/DLL calls. The actual
policy table, normalization, observation, clone/validate/submit/cleanup methods
run here; fixtures do not prove game legality or native gameplay acceptance.
"""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.v3_game import economy


class Query:
    def __init__(self,result):self.result=result
    def read(self):return self.result


class Context:
    def __init__(self,fixture):
        self.fixture=fixture;self.native=NS(pid=123);self.process=NS(pid=123)
        self.world=1234;self.diagnostics={};self.adapters={n:n for n in ("data","commands")}
    async def refresh(self):return None
    async def country_id(self):return 156
    async def type_id(self,name):return {"Country":1,"Goods":2}[name]
    async def perform(self,label,work):return await work()
    async def query(self,label,factory,*,owner=None):
        query=factory()
        return owner.complete() if owner else query.read()


class Owner:
    def __init__(self,fixture):
        self.fixture=fixture;self.command=0x7000;self.pending=None;self.attempt=NS(outcome="owned")
    def queued(self,result,*,consume=False):
        assert self.pending is None
        self.consume=consume;self.pending=Query(result)
        return self.pending
    def complete(self):
        value=self.pending.read();self.pending=None
        if self.consume:self.command=0;self.attempt.outcome="consumed"
        return value
    def serialize(self):return self.queued(NS(error=0,data=b"fixture-native-wire"))
    def validate(self,capacity):
        self.fixture.validations+=1
        return self.queued(NS(error=0,validated=self.fixture.allowed,reason="" if self.fixture.allowed else "Tariffs unavailable under current law"))
    def submit(self,flags):
        assert flags==4
        self.fixture.submissions+=1
        if self.fixture.accepted:
            self.fixture.active[self.fixture.selected_direction]=self.fixture.selected_policy
        return self.queued(NS(error=0,accepted=self.fixture.accepted,reason=""),consume=True)
    def destroy(self):
        self.fixture.destructions+=1
        return self.queued(NS(error=0,command=0),consume=True)


class Fixture:
    def __init__(self,import_policy="tariffs_high",export_policy="tariffs_none"):
        self.ctx=Context(self);self.api=economy.EconomyAPI(self.ctx)
        self.active={"import":import_policy,"export":export_policy}
        self.allowed=True;self.accepted=True;self.validations=0;self.submissions=0;self.destructions=0
        self.next_handle=10;self.handles=set();self.queries=[];self.owners=[]
        self.selected_direction=None;self.selected_policy=None;self.bad_query=False
        self.api._market_labels=self.labels;self.api._controlled=self.controlled
    async def controlled(self,country):return country==156
    async def market(self,*args,**kwargs):return {"market_id":7}
    async def labels(self,*args,**kwargs):
        return {"key":"iron","name":"Iron","binding":data.ObjectBinding(2,4,retained=True),"handles":[]},"Market"
    def result(self,kind,value):
        self.next_handle+=1;handle=self.next_handle;self.handles.add(handle)
        return NS(error=0,kind=kind,value=value,value_handle=handle)
    def evaluate(self,native,expression,bindings,**kwargs):
        self.queries.append(expression)
        for direction in economy._TARIFF_DIRECTIONS:
            for policy in economy._TARIFF_POLICIES:
                if expression==economy._tariff_expression(direction,policy,query=True):
                    value=self.result(data.Kind.BOOL,self.active[direction]==policy)
                    if self.bad_query:value.error=1168
                    return Query(value)
                if expression==economy._tariff_expression(direction,policy):
                    self.selected_direction=direction;self.selected_policy=policy
                    carrier=data.NativeDataCommand(0x5000,0x6000,0x5018,0,"CDataCommand",self.ctx.world)
                    return Query(self.result(data.Kind.COMMAND,carrier))
        raise AssertionError(expression)
    def release(self,native,handle,**kwargs):
        self.handles.remove(handle)
        return Query(NS(error=0,value_handle=0))
    def command_value(self,native,handle,**kwargs):
        return Query(NS(error=0,value=data.NativeDataCommand(0x5000,0x6000,0x5018,handle,"CDataCommand",self.ctx.world)))
    def clone(self,*args,**kwargs):return Query(NS(error=0,cloned_command=0x7000))
    def owner(self,*args,**kwargs):
        owner=Owner(self);self.owners.append(owner);return owner
    def patches(self):
        from contextlib import ExitStack
        stack=ExitStack()
        stack.enter_context(patch.object(economy.market_relation, "observe_country_market", side_effect=self.market))
        for module,name,value in ((economy.data,"evaluate",self.evaluate),(economy.data,"release",self.release),
            (economy.data,"command_value",self.command_value),(economy.commands,"request",self.clone),
            (economy.commands.NativeCommand,"from_result",self.owner)):
            stack.enter_context(patch.object(module,name,value))
        return stack


class TariffRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_native_read_words_and_canonical_fields_roundtrip_both_directions(self):
        expected={"tariffs_none":"none","tariffs_low":"low","tariffs_high":"high","tariffs_max":"max",
            "subventions_low":"subsidy_low","subventions_high":"subsidy_high","subventions_max":"subsidy_max"}
        self.assertEqual(set(expected),set(economy._TARIFF_POLICIES))
        for canonical,word in expected.items():
            for direction in economy._TARIFF_DIRECTIONS:
                with self.subTest(canonical=canonical,direction=direction):
                    fixture=Fixture(canonical,canonical)
                    with fixture.patches():
                        before=await fixture.api.tariffs("iron")
                        self.assertEqual(before[direction],word)
                        self.assertEqual(before[direction+"_policy"],canonical)
                        result=await fixture.api.set_tariff("iron",direction,before[direction])
                        again=await fixture.api.set_tariff("iron",direction,before[direction+"_policy"])
                        after=await fixture.api.tariffs("iron")
                    self.assertEqual(fixture.active[direction],canonical)
                    self.assertEqual(after[direction],word)
                    self.assertEqual(result["policy"],canonical)
                    self.assertTrue(result["accepted"]);self.assertTrue(result["effect_observed"])
                    self.assertTrue(again["accepted"])
                    self.assertEqual(fixture.validations,2);self.assertEqual(fixture.submissions,2)
                    self.assertFalse(fixture.handles)
                    self.assertTrue(all(not owner.command for owner in fixture.owners))


    async def test_same_effective_policy_still_obeys_real_native_validator_rejection(self):
        fixture=Fixture("subventions_high");fixture.allowed=False
        with fixture.patches():
            before=await fixture.api.tariffs("iron")
            result=await fixture.api.set_tariff("iron","import",before["import"])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"],"Tariffs unavailable under current law")
        self.assertEqual(fixture.validations,1);self.assertEqual(fixture.submissions,0)
        self.assertEqual(fixture.destructions,1);self.assertFalse(fixture.handles)
        self.assertEqual(fixture.active["import"],"subventions_high")

    async def test_receiver_rejection_is_not_reported_as_noop_or_effect(self):
        fixture=Fixture("tariffs_low");fixture.accepted=False
        with fixture.patches():result=await fixture.api.set_tariff("iron","import","low")
        self.assertFalse(result["accepted"])
        self.assertNotIn("effect_observed",result)
        self.assertEqual(fixture.validations,1);self.assertEqual(fixture.submissions,1)
        self.assertFalse(fixture.handles)

    async def test_incomplete_policy_read_keeps_both_fields_unknown(self):
        fixture=Fixture();fixture.bad_query=True
        with fixture.patches():result=await fixture.api.tariffs("iron")
        for direction in economy._TARIFF_DIRECTIONS:
            self.assertIsNone(result[direction]);self.assertIsNone(result[direction+"_policy"])
        self.assertEqual(result["reason"],"Effective policy observation incomplete")
        self.assertEqual(fixture.submissions,0);self.assertFalse(fixture.handles)

    async def test_unknown_or_numeric_policies_never_reach_native(self):
        fixture=Fixture()
        for policy in (None,True,3,"tariffs_medium","subsidy_none","invented_policy"):
            with self.subTest(policy=policy),self.assertRaises(ValueError):
                await fixture.api.set_tariff("iron","import",policy)
        self.assertFalse(fixture.queries);self.assertFalse(fixture.owners)


if __name__=="__main__":unittest.main()
