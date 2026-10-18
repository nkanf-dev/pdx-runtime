"""CPU copied-catalog contracts. No native runtime or game execution."""
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.v3_game.diplomacy import actions as a
from tests.v3_game.test_v3_game_political_data import Context,cell

TARGET=0x80000002

def make_page(fields,records,*,start=0,total=None,error=0,valid=True):
    cells=[]
    for row,values in enumerate(records):
        for column,field in enumerate(fields):
            value=values.get(field.name)
            if field.name not in values:
                if field.full_id:value=0 if field.name in ("first_country_id","observed_player_country_id") else TARGET
                elif field.kind==data.Kind.BOOL:value=False
                elif field.kind==data.Kind.FIXED:value=data.FixedPoint(123456)
                elif field.kind==data.Kind.I32:value=0
                else:value="native text"
            cells.append(value if isinstance(value,data.ProjectionCell) else cell(value,column,row))
    return data.ProjectionPage(error,0x1000,len(fields),start,7,
        len(records) if total is None else total,valid,len(records),tuple(cells))

def context_page():return make_page(a._CONTEXT,[{"target_name":"Native target"}])

class DiplomaticCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self,pages,**kwargs):
        ctx=Context(pages);api=DiplomacyAPI(ctx)
        with patch.object(data,"project_page",return_value=NS(cleanup_error=None)) as native,\
             patch.object(commands,"create") as create,patch.object(data,"evaluate") as evaluate:
            result=await api.diplomatic_options(TARGET,**kwargs)
        create.assert_not_called();evaluate.assert_not_called()
        return result,ctx,native

    async def test_default_five_original_sources_no_candidate_command(self):
        pages=[context_page()]+[make_page(a.DIPLOMATIC_ACTION_FIELDS,[{"native_key":"same_native_key","name":"#bold Native#!"}]) for _ in range(5)]
        result,ctx,native=await self.invoke(pages)
        self.assertTrue(result["complete"]);self.assertFalse(result["command_validation_performed"])
        self.assertEqual(len(result["actions"]),5)
        self.assertEqual([r["category"] for r in result["actions"]],list(a._ACTION_GROUPS))
        self.assertTrue(all(r["available"] is None for r in result["actions"]))
        self.assertEqual(native.call_count,6);self.assertEqual(len(ctx.performs),1);self.assertEqual(ctx.selected_reads,0)
        self.assertEqual([call.kwargs["source_expression"] for call in native.call_args_list[1:]],
                         ["Country.GetAll"+name+"DiploActions" for name in a._ACTION_GROUPS.values()])
        self.assertTrue(all(len(call.args[1])<=32 and call.kwargs["limit"]<=64 for call in native.call_args_list))

    async def test_full_id_zero_false_fixed_and_raw_key_preserved(self):
        key="native_日本:#bold_key#!"
        records=[{"native_key":key,"first_country_id":0,"second_country_id":-2147483646,
                  "name":"#bold Native name#!","maintenance_cost":data.FixedPoint(0),"active_pact":False}]
        result,_,_=await self.invoke([context_page(),make_page(a.DIPLOMATIC_ACTION_FIELDS,records)],groups=("general",))
        row=result["actions"][0]
        self.assertEqual((row["action"],row["native_key"]),(key,key))
        self.assertEqual((row["first_country_id"],row["second_country_id"]),(0,TARGET))
        self.assertFalse(row["active"]);self.assertEqual(row["cost"],Decimal(0))
        self.assertEqual(row["name"],"Native name");self.assertEqual(row["name_raw"],"#bold Native name#!")

    async def test_available_membership_is_native_source_not_host_validation(self):
        result,_,native=await self.invoke([context_page(),make_page(a.DIPLOMATIC_ACTION_FIELDS,[{"native_key":"x"}])],
                                          groups=("subject",),scope="available")
        self.assertTrue(result["actions"][0]["available"])
        self.assertFalse(result["command_validation_performed"])
        self.assertEqual(native.call_args.kwargs["source_expression"],"Country.GetAvailableSubjectDiploActions")

    async def test129rows_are_three_bounded_pages_not_scalar_calls(self):
        pages=[context_page()]+[make_page(a.DIPLOMATIC_ACTION_FIELDS,[{"native_key":str(i)} for i in range(start,min(start+64,129))],
                                 start=start,total=129) for start in (0,64,128)]
        result,_,native=await self.invoke(pages,groups=("general",))
        self.assertEqual(len(result["actions"]),129);self.assertEqual(native.call_count,4)
        self.assertEqual([call.kwargs["start"] for call in native.call_args_list[1:]],[0,64,128])

    async def test_error_keeps_partial_text_and_source(self):
        bad=cell("#bold kept#!",1,0,error=122,total=100)
        result,_,_=await self.invoke([context_page(),make_page(a.DIPLOMATIC_ACTION_FIELDS,[{"native_key":"x","name":bad}],error=122)],groups=("general",))
        self.assertFalse(result["complete"]);self.assertEqual(result["actions"][0]["name"],"kept")
        self.assertEqual(result["sources"]["general"]["native_pages"][0].error,122)
        self.assertTrue(result["actions"][0]["reasons"])

    async def test_failed_source_not_observed_empty_catalog(self):
        result,_,_=await self.invoke([context_page(),make_page(a.DIPLOMATIC_ACTION_FIELDS,[],error=87,valid=False)],groups=("general",))
        self.assertFalse(result["complete"]);self.assertEqual(result["actions"],[])
        self.assertIsNone(result["sources"]["general"]["source_count"])

    async def test_empty_source_is_real_empty_observation(self):
        result,_,_=await self.invoke([context_page(),make_page(a.DIPLOMATIC_ACTION_FIELDS,[])],groups=("general",))
        self.assertTrue(result["complete"]);self.assertEqual(result["actions"],[])
        self.assertFalse(result["complete_native_action_catalog"])

    async def test_bad_selection_does_not_query(self):
        for kwargs in ({"scope":"fake"},{"groups":("fake",)},{"groups":()}):
            ctx=Context([]);api=DiplomacyAPI(ctx)
            with self.assertRaises(ValueError):await api.diplomatic_options(TARGET,**kwargs)
            self.assertEqual(ctx.calls,[])


if __name__=="__main__":unittest.main()
