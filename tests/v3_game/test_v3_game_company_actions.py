"""CPU tests of original retained Company actions; no game process or controller."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from pdx_runtime import v3_data as D, v3_commands as C
from pdx_runtime.v3_game.economy import companies as A
from pdx_runtime.v3_game.economy import EconomyAPI

WORLD=0x7000
COUNTRY=0x3000007
KEY="company_steel"
COMPANY_ID=0xA1000402


class Context:
    def __init__(self):
        self.world=WORLD;self.native=object();self.process=object()
        self.adapters={"data":8,"console":9,"commands":10}
        self.diagnostics=[];self.types=[];self.calls=[]
    async def refresh(self):pass
    async def country_id(self):return COUNTRY
    async def type_id(self,name):
        self.types.append(name)
        return {"Country":83,"CompanyType":42,"Company":122}[name]
    async def query(self,label,factory,*,owner=None):
        self.calls.append((label,owner))
        return factory().result


def value_result(value=None,*,kind=D.Kind.EMPTY,handle=0,error=0,snapshot=b"",total=None):
    return NS(result=NS(error=error,kind=kind,value_handle=handle,value=value,
        snapshot=snapshot,total=len(snapshot) if total is None else total,
        native_type_name=getattr(value,"native_type_name","")))


def source(handle,error=0):return value_result(kind=D.Kind.LIST,handle=handle,error=error)
def count(number):return NS(result=NS(error=0,total=number))
def element(handle,kind="CompanyType",error=0):
    obj=D.NativeObject(0x100000+handle,42 if kind=="CompanyType" else 122,
        handle,"CPdxPtr@VCData"+kind)
    return value_result(obj,kind=D.Kind.OBJECT,handle=handle,error=error)
def copied_key(key=KEY,*,handle=14,error=0,total=None):
    return value_result(kind=D.Kind.STRING,handle=handle,error=error,
        snapshot=key.encode(),total=total)
def producer(handle=901):
    real=D.NativeDataCommand(0x11111,0x22222,0x33333,handle,"CDataCommand",WORLD)
    return value_result(real,kind=D.Kind.COMMAND,handle=handle)
def player(*,controlled=COUNTRY):
    return NS(result=NS(error=0,controlled_country_error=0,observer_enabled=False,
        world=WORLD,controlled_country_id=controlled))
def returned(company_ids=(),*,world=WORLD,country=COUNTRY,aux_complete=False):
    rows=[{"company_id":ident,"country_id":country,"type_key":KEY,
        "country_relation_complete":True,
        "observations":{"type_key":{"complete":True},"name":{"complete":aux_complete}}}
        for ident in company_ids]
    return {"country_id":country,"world":world,"companies":rows,"native_count":len(rows),
        "truncated":False,"reasons":[],"contract_complete":aux_complete,
        "complete":aux_complete}


class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_create_potential_original_typed_owner_and_auxiliary_readback_not_gate(self):
        ctx=Context();released=[]
        def evaluate(_native,expression,bindings,**kwargs):
            if expression=="Country.GetPotentialCompanies":return source(11)
            if expression=="CompanyType.GetKey":
                self.assertEqual(bindings[0].object,12)
                self.assertTrue(bindings[0].retained)
                return copied_key()
            if expression=="Country.CreateCompany(CompanyType.Self)":
                self.assertEqual(bindings[0].object,COUNTRY)
                self.assertTrue(bindings[0].country_id)
                self.assertEqual(bindings[1].object,12)
                return producer()
            raise AssertionError(expression)
        def release(_native,handle,**kwargs):
            released.append(handle)
            return NS(result=NS(error=0,value_handle=0))
        async def submit(generated,handles,request):
            self.assertIsInstance(generated.value,D.NativeDataCommand)
            self.assertEqual(handles,[11,12,14,901])
            self.assertEqual(request["country_id"],COUNTRY)
            return {"accepted":True,"native_accepted":True}
        observed=returned([COMPANY_ID])  # Name/date not complete: irrelevant to membership.
        with patch.object(A,"list_companies",return_value=observed) as readback, \
             patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate) as evaluate_calls, \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12)), \
             patch.object(D,"release",side_effect=release), \
             patch.object(D,"enumerate_functions") as metadata:
            answer=await A.change_company(ctx,KEY,"create",submit_generated=submit)
        self.assertTrue(answer["accepted"]);self.assertTrue(answer["effect_observed"])
        self.assertIs(answer["readback"],observed)
        self.assertEqual(answer["selected_source"],"Country.GetPotentialCompanies")
        self.assertEqual(answer["after_company_ids"],[COMPANY_ID])
        self.assertEqual(released,[901,14,12,11])
        self.assertEqual(ctx.types,["Country","CompanyType"])
        self.assertEqual(evaluate_calls.call_count,3)
        readback.assert_awaited_once();metadata.assert_not_called()

    async def test_remove_one_proved_id_even_when_unrelated_element_incomplete(self):
        ctx=Context();calls=[]
        def evaluate(_native,expression,bindings,**kwargs):
            if expression=="Country.GetCompanies":return source(11,error=13)
            if expression=="Company.GetCompanyType.GetKey":
                self.assertEqual(bindings[0].object,12)
                return copied_key()
            if expression=="Country.RemoveCompany(Company.GetCompanyType)":
                self.assertEqual(bindings[0].object,COUNTRY)
                self.assertEqual(bindings[1].object,12)
                return producer()
            raise AssertionError(expression)
        def next_element(_native,handle,index,**kwargs):
            return element(12,"Company") if index==0 else value_result(
                kind=D.Kind.EMPTY,handle=21,error=13)
        async def submit(generated,handles,request):
            calls.append("submit")
            self.assertIsInstance(generated.value,D.NativeDataCommand)
            self.assertEqual(handles,[11,12,14,21,901])
            return {"accepted":True,"native_accepted":True}
        observed=returned([])
        with patch.object(A,"list_companies",return_value=observed), \
             patch.object(A,"_copy_owned_full_id",return_value=COMPANY_ID) as native_id, \
             patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"list_count",return_value=count(2)), \
             patch.object(D,"list_element",side_effect=next_element), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))):
            answer=await A.change_company(ctx,KEY,"remove",submit_generated=submit)
        self.assertEqual(calls,["submit"])
        self.assertTrue(answer["accepted"]);self.assertTrue(answer["effect_observed"])
        self.assertEqual(answer["selected_company_id"],COMPANY_ID)
        self.assertEqual(answer["selected_source"],"Country.GetCompanies")
        self.assertTrue(answer["sources"][0]["source_partial"])
        self.assertEqual(answer["sources"][0]["unverified_elements"][0]["error"],13)
        self.assertEqual(answer["sources"][0]["reason"],"Country.GetCompanies: native source error 13")
        self.assertEqual(ctx.types,["Country","Company"])
        native_id.assert_awaited_once()

    async def test_remove_matching_partial_readback_proves_effect_not_observed(self):
        ctx=Context()
        async def submit(*args):return {"accepted":True,"native_accepted":True}
        def evaluate(_native,expression,bindings,**kwargs):
            return {"Country.GetCompanies":source(11),
                "Company.GetCompanyType.GetKey":copied_key(),
                "Country.RemoveCompany(Company.GetCompanyType)":producer()}[expression]
        partial=returned([COMPANY_ID]);partial["native_count"]=None
        partial["reasons"]=["unrelated page error"]
        with patch.object(A,"list_companies",return_value=partial), \
             patch.object(A,"_copy_owned_full_id",return_value=COMPANY_ID), \
             patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12,"Company")), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))):
            answer=await A.change_company(ctx,KEY,"remove",submit_generated=submit)
        self.assertTrue(answer["accepted"])
        self.assertTrue(answer["membership_observed"])
        self.assertFalse(answer["effect_observed"])
        self.assertEqual(answer["after_company_ids"],[COMPANY_ID])
        self.assertIs(answer["readback"],partial)

    async def test_actual_duplicate_live_type_blocks_ambiguous_type_based_removal(self):
        ctx=Context()
        def evaluate(_native,expression,bindings,**kwargs):
            return source(11) if expression=="Country.GetCompanies" else copied_key()
        with patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(A,"_copy_owned_full_id",side_effect=[COMPANY_ID,COMPANY_ID+1]), \
             patch.object(D,"evaluate",side_effect=evaluate) as getters, \
             patch.object(D,"list_count",return_value=count(2)), \
             patch.object(D,"list_element",side_effect=[element(12,"Company"),element(22,"Company")]), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))), \
             patch.object(A,"list_companies") as readback:
            answer=await A.change_company(ctx,KEY,"remove",submit_generated=lambda *args:None)
        self.assertFalse(answer["accepted"])
        self.assertIn("ambiguous",answer["sources"][0]["reason"])
        self.assertEqual(getters.call_count,3)
        readback.assert_not_called()

    async def test_existing_type_fallback_uses_original_owned_promote_not_forged_success(self):
        ctx=Context();native_commands=[]
        def evaluate(_native,expression,bindings,**kwargs):
            if expression in A._CREATE_SOURCES:return source({A._CREATE_SOURCES[0]:11,
                A._CREATE_SOURCES[1]:21,A._CREATE_SOURCES[2]:31}[expression])
            if expression=="Country.GetCompanies":return source(41)
            if expression=="Company.GetCompanyType.GetKey":return copied_key(handle=44)
            if expression=="Country.CreateCompany(Company.GetCompanyType)":
                native_commands.append(expression)
                self.assertEqual(bindings[1].object,42)
                return producer()
            raise AssertionError(expression)
        def list_count(_native,handle,**kwargs):return count(1 if handle==41 else 0)
        async def submit(generated,handles,request):
            self.assertIsInstance(generated.value,D.NativeDataCommand)
            return {"accepted":False,"allowed":False,"native_validation_error":0,
                "reason":"native rules reject another company"}
        with patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(A,"_copy_owned_full_id") as copy_id, \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"list_count",side_effect=list_count), \
             patch.object(D,"list_element",return_value=element(42,"Company")), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))):
            answer=await A.change_company(ctx,KEY,"create",submit_generated=submit)
        self.assertEqual(native_commands,["Country.CreateCompany(Company.GetCompanyType)"])
        copy_id.assert_not_called()
        self.assertNotIn("existing_company_id",answer)
        self.assertFalse(answer["accepted"])
        self.assertFalse(answer["allowed"])
        self.assertIsNone(answer["effect_observed"])

    async def test_absent_key_is_source_gap_not_native_validation_failure(self):
        ctx=Context()
        with patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=[source(11),source(12),source(13),source(14)]) as getters, \
             patch.object(D,"list_count",return_value=count(0)), \
             patch.object(D,"list_element") as elements, \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))), \
             patch.object(A,"list_companies") as readback:
            answer=await A.change_company(ctx,KEY,"create",submit_generated=lambda *args:None)
        self.assertEqual(len(answer["sources"]),4)
        self.assertIn("absence or permanent unavailability is not proved",answer["reason"])
        self.assertNotIn("allowed",answer)
        self.assertFalse(answer["accepted"])
        self.assertIsNone(answer["effect_observed"])
        self.assertEqual(getters.call_count,4)
        elements.assert_not_called();readback.assert_not_called()

    async def test_wrong_potential_type_skipped_until_real_available_type(self):
        ctx=Context()
        def evaluate(_native,expression,bindings,**kwargs):
            return {A._CREATE_SOURCES[0]:source(11),A._CREATE_SOURCES[1]:source(21),
                A._CREATE_SOURCES[2]:source(31),"CompanyType.GetKey":copied_key(handle=34),
                "Country.CreateCompany(CompanyType.Self)":producer()}[expression]
        def native_count(_native,handle,**kwargs):return count(1 if handle!=21 else 0)
        def native_element(_native,handle,index,**kwargs):
            return element(12,"Company") if handle==11 else element(32)
        async def submit(generated,handles,request):
            self.assertEqual(handles,[11,12,21,31,32,34,901])
            return {"accepted":False,"allowed":False,"native_validation_error":0}
        with patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"list_count",side_effect=native_count), \
             patch.object(D,"list_element",side_effect=native_element), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))):
            answer=await A.change_company(ctx,KEY,"create",submit_generated=submit)
        self.assertEqual(answer["selected_source"],A._CREATE_SOURCES[2])
        self.assertEqual(answer["sources"][0]["unverified_elements"][0]["index"],0)

    async def test_source_error_with_retained_list_still_generates_from_typed_owner(self):
        ctx=Context()
        def evaluate(_native,expression,bindings,**kwargs):
            return {A._CREATE_SOURCES[0]:source(11,error=13),
                "CompanyType.GetKey":copied_key(),
                "Country.CreateCompany(CompanyType.Self)":producer()}[expression]
        async def submit(generated,handles,request):return {"accepted":False,"allowed":False}
        with patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12)), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))):
            answer=await A.change_company(ctx,KEY,"create",submit_generated=submit)
        self.assertEqual(answer["selected_source"],A._CREATE_SOURCES[0])
        self.assertIn("native source error 13",answer["sources"][0]["reason"])
        self.assertTrue(answer["source_partial"])
        self.assertFalse(answer["accepted"])

    async def test_long_key_snapshots_original_any_once_without_default_total_cap(self):
        ctx=Context();key="company_"+"a"*1_050_000
        pending=copied_key(key[:200],error=D.ERROR_MORE_DATA,total=len(key.encode()))
        complete=copied_key(key)
        def evaluate(_native,expression,bindings,**kwargs):
            return {A._CREATE_SOURCES[0]:source(11),"CompanyType.GetKey":pending,
                "Country.CreateCompany(CompanyType.Self)":producer()}[expression]
        async def submit(*args):return {"accepted":False,"allowed":False}
        with patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate) as getter, \
             patch.object(D,"snapshot",return_value=complete) as snapshot, \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12)), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))):
            answer=await A.change_company(ctx,key,"create",submit_generated=submit)
        self.assertEqual(answer["selected_source"],A._CREATE_SOURCES[0])
        self.assertEqual(getter.call_count,3)
        snapshot.assert_called_once()
        self.assertEqual(snapshot.call_args.args[1],14)

    async def test_native_source_not_cut_at_old_4096_default_cap(self):
        ctx=Context();visited=[]
        def evaluate(_native,expression,bindings,**kwargs):
            return {A._CREATE_SOURCES[0]:source(11),"CompanyType.GetKey":copied_key(),
                "Country.CreateCompany(CompanyType.Self)":producer()}[expression]
        def element_at(_native,handle,index,**kwargs):
            visited.append(index)
            return element(12) if index==4096 else value_result(
                kind=D.Kind.EMPTY,error=13)
        async def submit(*args):return {"accepted":False,"allowed":False}
        with patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"list_count",return_value=count(4097)), \
             patch.object(D,"list_element",side_effect=element_at), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))):
            answer=await A.change_company(ctx,KEY,"create",submit_generated=submit)
        self.assertEqual(visited[-1],4096)
        self.assertEqual(len(visited),4097)
        self.assertEqual(answer["sources"][0]["matched_index"],4096)
        self.assertTrue(answer["source_partial"])

    async def test_key_errors_preserve_raw_native_snapshot_and_indexes(self):
        ctx=Context()
        def evaluate(_native,expression,bindings,**kwargs):
            if expression in A._CREATE_SOURCES or expression=="Country.GetCompanies":
                return source({A._CREATE_SOURCES[0]:11,A._CREATE_SOURCES[1]:21,
                    A._CREATE_SOURCES[2]:31,"Country.GetCompanies":41}[expression])
            if expression=="CompanyType.GetKey":return copied_key(KEY[:4],error=13,total=50)
            if expression=="Company.GetCompanyType.GetKey":return copied_key(KEY[:4],error=13,total=50)
            raise AssertionError(expression)
        with patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12)), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))):
            answer=await A.change_company(ctx,KEY,"create",submit_generated=lambda *args:None)
        issue=answer["sources"][0]["unverified_keys"][0]
        self.assertEqual(issue["index"],0)
        self.assertEqual(issue["observations"][0]["error"],13)
        self.assertEqual(issue["observations"][0]["snapshot"],b"comp")
        self.assertEqual(issue["observations"][0]["total"],50)

    async def test_snapshot_interruption_preserves_original_key_prefix_and_owner(self):
        ctx=Context()
        prefix=copied_key(KEY[:4],error=D.ERROR_MORE_DATA,total=len(KEY.encode()))
        original=OSError("snapshot request unresolved");original.query=object()
        def evaluate(_native,expression,bindings,**kwargs):
            return {A._CREATE_SOURCES[0]:source(11),"CompanyType.GetKey":prefix}[expression]
        with patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate) as getter, \
             patch.object(D,"snapshot",side_effect=original) as snapshot, \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12)), \
             patch.object(D,"release") as release:
            with self.assertRaises(OSError) as caught:
                await A.change_company(ctx,KEY,"create",submit_generated=lambda *args:None)
        self.assertIs(caught.exception,original)
        self.assertIs(caught.exception.query,original.query)
        partial=original.company_action_result
        self.assertEqual(partial["country_id"],COUNTRY)
        self.assertEqual(partial["sources"][0]["source"],A._CREATE_SOURCES[0])
        evidence=partial["sources"][0]["unverified_keys"][0]
        self.assertEqual(evidence["index"],0)
        self.assertEqual(evidence["observations"][0]["snapshot"],b"comp")
        self.assertEqual(evidence["observations"][0]["error"],D.ERROR_MORE_DATA)
        self.assertEqual(evidence["observations"][0]["total"],len(KEY.encode()))
        self.assertEqual(getter.call_count,2)
        snapshot.assert_called_once();release.assert_not_called()
        self.assertEqual(ctx._economy_retained[0]["company_action_read"].owners,[11,12,14])

    async def test_unknown_original_generation_keeps_owner_without_replaying(self):
        class FailingContext(Context):
            async def query(self,label,factory,*,owner=None):
                if label=="economy.company.action.generate":
                    raise OSError("original query unresolved")
                return await super().query(label,factory,owner=owner)
        ctx=FailingContext()
        def evaluate(_native,expression,bindings,**kwargs):
            return {A._CREATE_SOURCES[0]:source(11),"CompanyType.GetKey":copied_key()}[expression]
        with patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate) as getter, \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12)), \
             patch.object(D,"release") as release:
            with self.assertRaisesRegex(OSError,"original query unresolved"):
                await A.change_company(ctx,KEY,"create",submit_generated=lambda *args:None)
        self.assertEqual(getter.call_count,2)
        release.assert_not_called()
        self.assertEqual(ctx._economy_retained[0]["company_action_read"].owners,[11,12,14])

    async def test_pending_unique_command_retains_same_source_any_owner(self):
        ctx=Context()
        async def submit(generated,handles,request):
            return {"accepted":None,"native_error":574,"_retain_inputs":True}
        def evaluate(_native,expression,bindings,**kwargs):
            return {A._CREATE_SOURCES[0]:source(11),"CompanyType.GetKey":copied_key(),
                "Country.CreateCompany(CompanyType.Self)":producer()}[expression]
        with patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12)), \
             patch.object(D,"release") as release:
            answer=await A.change_company(ctx,KEY,"create",submit_generated=submit)
        self.assertIsNone(answer["accepted"])
        self.assertTrue(answer["ownership_pending"])
        release.assert_not_called()
        retained=ctx._economy_retained[0]["company_action_read"]
        self.assertEqual(retained.owners,[11,12,14,901])
        self.assertTrue(retained.uncertain)

    async def test_full_partial_and_wrong_world_readback_preserve_primary_acceptance(self):
        ctx=Context()
        async def submit(*args):return {"accepted":True,"native_accepted":True}
        def evaluate(_native,expression,bindings,**kwargs):
            return {A._CREATE_SOURCES[0]:source(11),"CompanyType.GetKey":copied_key(),
                "Country.CreateCompany(CompanyType.Self)":producer()}[expression]
        broken=returned([COMPANY_ID],world=WORLD+1)
        broken["companies"][0]["observations"]["type_key"]={"complete":False,"error":13}
        with patch.object(A,"list_companies",return_value=broken), \
             patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate) as getter, \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12)), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))):
            answer=await A.change_company(ctx,KEY,"create",submit_generated=submit)
        self.assertTrue(answer["accepted"])
        self.assertIs(answer["readback"],broken)
        self.assertIsNone(answer["effect_observed"])
        self.assertIn("country/world changed",answer["readback_reason"])
        self.assertEqual(getter.call_count,3)

    async def test_matching_readback_row_proves_presence_despite_partial_list(self):
        ctx=Context()
        async def submit(*args):return {"accepted":True,"native_accepted":True}
        def evaluate(_native,expression,bindings,**kwargs):
            return {A._CREATE_SOURCES[0]:source(11),"CompanyType.GetKey":copied_key(),
                "Country.CreateCompany(CompanyType.Self)":producer()}[expression]
        partial=returned([COMPANY_ID]);partial["native_count"]=None
        partial["truncated"]=True;partial["reasons"]=["other Company page incomplete"]
        with patch.object(A,"list_companies",return_value=partial), \
             patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate) as getter, \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12)), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))):
            answer=await A.change_company(ctx,KEY,"create",submit_generated=submit)
        self.assertTrue(answer["accepted"])
        self.assertTrue(answer["membership_observed"])
        self.assertTrue(answer["effect_observed"])
        self.assertEqual(answer["after_company_ids"],[COMPANY_ID])
        self.assertIs(answer["readback"],partial)
        self.assertEqual(getter.call_count,3)
        present,ids,reason=A._readback_membership(partial,COUNTRY,WORLD,WORLD,KEY)
        self.assertIs(present,True);self.assertEqual(ids,[COMPANY_ID]);self.assertIsNone(reason)
        empty=returned([]);empty["native_count"]=None
        absent,ids,reason=A._readback_membership(empty,COUNTRY,WORLD,WORLD,KEY)
        self.assertIsNone(absent);self.assertEqual(ids,[])
        self.assertIn("absence not proved",reason)

    async def test_interrupted_readback_retains_original_partial_result(self):
        ctx=Context();partial=returned([COMPANY_ID])
        failure=OSError("readback transport failed");failure.company_observations=partial
        async def interrupted(_ctx):raise failure
        async def submit(*args):return {"accepted":True,"native_accepted":True}
        def evaluate(_native,expression,bindings,**kwargs):
            return {A._CREATE_SOURCES[0]:source(11),"CompanyType.GetKey":copied_key(),
                "Country.CreateCompany(CompanyType.Self)":producer()}[expression]
        with patch.object(A,"list_companies",side_effect=interrupted), \
             patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate) as getter, \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12)), \
             patch.object(D,"release",return_value=NS(result=NS(error=0,value_handle=0))):
            answer=await A.change_company(ctx,KEY,"create",submit_generated=submit)
        self.assertTrue(answer["accepted"])
        self.assertIsNone(answer["effect_observed"])
        self.assertIs(answer["readback"],partial)
        self.assertEqual(getter.call_count,3)

    async def test_real_native_command_owner_bound_submit_validates_once(self):
        ctx=Context();actions=[]
        def evaluate(_native,expression,bindings,**kwargs):
            return {A._CREATE_SOURCES[0]:source(11),"CompanyType.GetKey":copied_key(),
                "Country.CreateCompany(CompanyType.Self)":producer()}[expression]
        def receipt(action,*,accepted=0,validated=0,cloned=0,command=0x222):
            return C.CommandResult(size=C.RESULT.size,error=0,action=action,type_token=23684,
                command=command,buffer=0,buffer_capacity=0,buffer_size=0,count=0,
                submit_flags=4,validated=validated,accepted=accepted,
                execution_thread_id=17,reserved=0,application=0,world=WORLD,session=1,
                data=b"original native wire" if action==C.Action.SERIALIZE else b"",
                cloned_command=cloned)
        def wrap(value):return NS(result=value)
        def clone(_native,action,**kwargs):
            actions.append("clone")
            self.assertEqual(action,C.Action.CLONE)
            self.assertEqual(kwargs["command"],0x11111)
            return wrap(receipt(action,cloned=0x222))
        def serialize(owner,capacity=65536):
            self.assertIsInstance(owner,C.NativeCommand)
            actions.append("serialize");return wrap(receipt(C.Action.SERIALIZE))
        def validate(owner,capacity=4096):
            self.assertEqual(capacity,65536)
            actions.append("validate");return wrap(receipt(C.Action.VALIDATE,validated=1))
        def submit(owner,flags=4):
            self.assertEqual(flags,4)
            actions.append("submit");return wrap(receipt(C.Action.SUBMIT,accepted=1))
        def destroy(owner):
            actions.append("destroy");return wrap(receipt(C.Action.DESTROY,command=0))
        original=D.NativeDataCommand(0x11111,0x22222,0x33333,901,"CDataCommand",WORLD)
        with patch.object(A,"list_companies",return_value=returned([COMPANY_ID])), \
             patch.object(A.player,"observe_player",return_value=player()), \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"list_count",return_value=count(1)), \
             patch.object(D,"list_element",return_value=element(12)), \
             patch.object(D,"command_value",return_value=wrap(NS(error=0,value=original))), \
             patch.object(D,"release",return_value=wrap(NS(error=0,value_handle=0))), \
             patch.object(C,"request",side_effect=clone), \
             patch.object(C.NativeCommand,"serialize",serialize), \
             patch.object(C.NativeCommand,"validate",validate), \
             patch.object(C.NativeCommand,"submit",submit), \
             patch.object(C.NativeCommand,"destroy",destroy):
            answer=await A.change_company(ctx,KEY,"create",
                submit_generated=EconomyAPI(ctx)._submit_generated)
        self.assertTrue(answer["accepted"]);self.assertTrue(answer["effect_observed"])
        self.assertEqual(actions,["clone","serialize","validate","submit","destroy"])

    async def test_invalid_key_or_operation_never_queries_native(self):
        for key,operation in [("","create"),("x\x00","remove"),(23,"remove"),(KEY,"erase")]:
            with self.subTest(key=key,operation=operation):
                with self.assertRaises(ValueError):
                    await A.change_company(Context(),key,operation,submit_generated=lambda *args:None)


class RenameThroughSharedOwner(unittest.IsolatedAsyncioTestCase):
    class Owner:
        def __init__(self, *, validated=True, validation_error=0, submit_error=0,
                     accepted=True, unknown=False, failure=None):
            self.command=901; self.world=WORLD; self.type_token=24072
            self.pending=None; self.attempt=NS(outcome="owned"); self.calls=[]
            self.validated=validated; self.validation_error=validation_error
            self.submit_error=submit_error; self.accepted=accepted; self.unknown=unknown
            self.failure=failure
            self.validation=NS(error=validation_error,validated=validated,reason="native rules",command=901)
            self.receipt=NS(error=submit_error,accepted=accepted,command=0)
        def validate(self, *, capacity):
            self.calls.append(("validate",capacity))
            return NS(result=self.validation)
        def submit(self, *, flags):
            self.calls.append(("submit",flags))
            if self.failure is not None:
                self.pending=self.failure.query; self.attempt=NS(outcome="unknown")
                raise self.failure
            self.attempt=NS(outcome="unknown" if self.unknown else "consumed")
            if not self.unknown:self.command=0
            return NS(result=self.receipt)
        def destroy(self):
            self.calls.append(("destroy",))
            self.command=0; self.attempt=NS(outcome="consumed")
            return NS(result=NS(error=0,command=0))
        def serialize(self): raise AssertionError("no auxiliary serialize gate")

    def observed(self, name="新名字", *, complete=True, world=WORLD):
        return {"world":world,"complete":False,"reasons":["unrelated date/field incomplete"],
            "companies":[{"company_id":COMPANY_ID,"complete":False,
                "observations":{"name_no_formatting":{"value":name,"complete":complete,
                    "snapshot":name.encode(),"error":0 if complete else 234}}},
                {"company_id":COMPANY_ID+1,"complete":False,"observations":{}}]}

    async def invoke(self, *, owner=None, created_error=0, created_command=901,
                     observation=None, readback_error=None, new_name="新名字", cleanup_error=None):
        from pdx_runtime.v3_game._run import Run as _Run
        from unittest.mock import AsyncMock
        ctx=Context(); ctx.refreshes=0; events=[]
        async def refresh():ctx.refreshes+=1
        ctx.refresh=refresh
        owner=self.Owner() if owner is None else owner
        created=NS(error=created_error,command=created_command,world=WORLD,
            type_token=24072,action=C.Action.DESERIALIZE)
        def create(*args,**kwargs):events.append("factory");return NS(result=created)
        async def readback(_ctx):
            events.append("readback")
            self.assertIn(("submit",4),owner.calls)
            if readback_error is not None:raise readback_error
            return self.observed(new_name) if observation is None else observation
        with patch.object(C,"create",side_effect=create) as factory, \
             patch.object(C.NativeCommand,"from_result",return_value=owner) as adopted, \
             patch.object(A,"list_companies",side_effect=readback) as read:
            if cleanup_error is None:
                result=await A.rename_company(ctx,COMPANY_ID,new_name,run_factory=_Run)
            else:
                with patch.object(_Run,"cleanup",new=AsyncMock(side_effect=cleanup_error)):
                    result=await A.rename_company(ctx,COMPANY_ID,new_name,run_factory=_Run)
        factory.assert_called_once_with(ctx.native,24072,actor_id=COUNTRY,world=WORLD,
            parameters={23675:C.UInt32(COMPANY_ID),27:new_name},adapter=10,process=ctx.process)
        self.assertEqual(ctx.refreshes,1)
        self.assertEqual(ctx.types,[])
        return ctx,result,owner,created,adopted.call_count,read.await_count,events

    async def test_original_factory_validate_submit_once_then_name_without_auxiliary_gate(self):
        ctx,result,owner,created,adopted,reads,events=await self.invoke()
        self.assertTrue(result["accepted"]);self.assertTrue(result["effect_observed"])
        self.assertEqual(result["name_no_formatting"],"新名字")
        self.assertIs(result["native_result"],owner.receipt)
        self.assertIs(result["creation"],created)
        self.assertEqual(owner.calls,[("validate",65536),("submit",4)])
        self.assertEqual((adopted,reads),(1,1));self.assertEqual(events,["factory","readback"])
        self.assertEqual(ctx.owner_pool.snapshot(),())

    async def test_empty_name_is_original_native_request_not_host_trimmed_or_fake_noop(self):
        _,result,owner,_,_,_,_=await self.invoke(new_name="")
        self.assertTrue(result["accepted"]);self.assertTrue(result["effect_observed"])
        self.assertEqual(result["name_no_formatting"],"")
        self.assertEqual(owner.calls,[("validate",65536),("submit",4)])

    async def test_native_factory_error_and_partial_command_keep_original_without_adoption(self):
        for pointer in (0,901):
            with self.subTest(command=pointer):
                ctx,result,owner,created,adopted,reads,_=await self.invoke(
                    created_error=13,created_command=pointer)
                self.assertIs(result["native_result"],created)
                self.assertEqual(result["native_error"],13)
                self.assertIsNone(result["accepted"])
                self.assertEqual((adopted,reads),(0,0));self.assertEqual(owner.calls,[])
                if pointer:
                    retained=ctx.owner_pool.snapshot()
                    self.assertEqual(len(retained),1)
                    self.assertIs(retained[0].unadopted_command,created)
                    self.assertEqual(result["cleanup"]["status"],"retained_pending_or_unknown")
                else:self.assertEqual(ctx.owner_pool.snapshot(),())

    async def test_native_validator_rejects_once_without_submit_or_readback(self):
        _,result,owner,_,_,reads,_=await self.invoke(owner=self.Owner(validated=False))
        self.assertFalse(result["accepted"]);self.assertEqual(result["status"],"rejected")
        self.assertIs(result["validation"],owner.validation)
        self.assertEqual(owner.calls,[("validate",65536),("destroy",)])
        self.assertEqual(reads,0)

    async def test_submit_unknown_keeps_original_owner_and_native_receipt_no_cleanup_replay(self):
        owner=self.Owner(submit_error=574,accepted=False,unknown=True)
        ctx,result,_,_,_,reads,_=await self.invoke(owner=owner)
        self.assertIs(result["native_result"],owner.receipt)
        self.assertEqual(result["native_error"],574);self.assertIsNone(result["accepted"])
        self.assertEqual(result["cleanup"]["status"],"retained_pending_or_unknown")
        self.assertEqual(owner.calls,[("validate",65536),("submit",4)])
        self.assertEqual(reads,0)
        self.assertIs(ctx.owner_pool.snapshot()[0].owners[0],owner)

    async def test_readback_error_partial_or_other_world_never_erases_acceptance(self):
        failure=OSError("independent native name copy failed")
        failure.query=object();failure.company_observations={"companies":[{"copied_prefix":b"partial"}]}
        _,result,owner,_,_,_,_=await self.invoke(readback_error=failure)
        self.assertTrue(result["accepted"]);self.assertIs(result["native_result"],owner.receipt)
        self.assertIs(result["readback_error"],failure)
        self.assertIs(result["readback"],failure.company_observations)
        self.assertIsNone(result["effect_observed"])
        for observed in (self.observed("前缀",complete=False),self.observed("新名字",world=WORLD+1)):
            _,result,owner,_,_,_,_=await self.invoke(observation=observed)
            self.assertTrue(result["accepted"]);self.assertIsNone(result["effect_observed"])
            self.assertIs(result["native_result"],owner.receipt)
            self.assertEqual(result["name_no_formatting"],observed["companies"][0]["observations"]["name_no_formatting"]["value"])

    async def test_cleanup_warning_does_not_replace_original_accepted_result(self):
        _,result,owner,_,_,_,_=await self.invoke(cleanup_error=OSError("cleanup only"))
        self.assertTrue(result["accepted"]);self.assertTrue(result["effect_observed"])
        self.assertIs(result["native_result"],owner.receipt)
        self.assertEqual(result["cleanup"]["status"],"cleanup_unknown_retained")

    async def test_pending_submit_keeps_same_exception_query_and_single_owner(self):
        from pdx_runtime.v3_game._run import Run as _Run
        ctx=Context();failure=OSError("original submit pending");failure.query=object()
        owner=self.Owner(failure=failure)
        created=NS(error=0,command=901,world=WORLD,type_token=24072,action=C.Action.DESERIALIZE)
        with patch.object(C,"create",return_value=NS(result=created)) as factory, \
             patch.object(C.NativeCommand,"from_result",return_value=owner), \
             patch.object(A,"list_companies") as read:
            with self.assertRaises(OSError) as caught:
                await A.rename_company(ctx,COMPANY_ID,"name",run_factory=_Run)
        self.assertIs(caught.exception,failure)
        self.assertIs(owner.pending,failure.query)
        self.assertEqual(factory.call_count,1);self.assertEqual(owner.calls,[("validate",65536),("submit",4)])
        read.assert_not_called()
        self.assertIs(ctx.owner_pool.snapshot()[0].owners[0],owner)
        self.assertIsNone(failure.company_action_result["accepted"])

    async def test_final_cleanup_cancellation_keeps_same_error_and_public_prefix_once(self):
        import asyncio
        from unittest.mock import AsyncMock
        from pdx_runtime.v3_game._run import Run as _Run
        ctx=Context();failure=asyncio.CancelledError("owning task cancelled during cleanup")
        owner=self.Owner(validated=False)
        created=NS(error=0,command=901,world=WORLD,type_token=24072,action=C.Action.DESERIALIZE)
        with patch.object(C,"create",return_value=NS(result=created)) as factory, \
             patch.object(C.NativeCommand,"from_result",return_value=owner), \
             patch.object(A,"list_companies") as read, \
             patch.object(_Run,"cleanup",new=AsyncMock(side_effect=failure)) as cleanup:
            with self.assertRaises(asyncio.CancelledError) as caught:
                await A.rename_company(ctx,COMPANY_ID,"name",run_factory=_Run)
        self.assertIs(caught.exception,failure)
        answer=failure.company_action_result
        self.assertFalse(answer["accepted"])
        self.assertIs(answer["validation"],owner.validation)
        self.assertIs(answer["creation"],created)
        self.assertEqual(owner.calls,[("validate",65536)])
        cleanup.assert_awaited_once();factory.assert_called_once();read.assert_not_called()
        retained=ctx.owner_pool.snapshot()
        self.assertEqual(len(retained),1)
        self.assertIs(retained[0].public_result,answer)
        self.assertIs(retained[0].owners[0],owner)



if __name__=="__main__":unittest.main()
