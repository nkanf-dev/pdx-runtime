"""CPU-only political page composition. No game/native connection."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game import _political_data as p


def cell(value, field=0, row=0, error=0, *, total=None, kind=None):
    snapshot=b""; bits=0
    if isinstance(value,str): k=data.Kind.STRING; snapshot=value.encode()
    elif type(value) is bool:k=data.Kind.BOOL;bits=int(value)
    elif type(value) is int:k=data.Kind.I32;bits=value&0xffffffff
    elif isinstance(value,data.FixedPoint):k=data.Kind.FIXED;bits=value.bits
    else:k=data.Kind.EMPTY
    return data.ProjectionCell(row,field,error,k if kind is None else kind,9,bits,snapshot,
                               len(snapshot) if total is None else total)


def page(values, *, start=0, total=None, error=0, valid=True):
    count=len(values)
    width=len(values[0]) if values else 1
    cells=tuple(cell(value,j,i) if not isinstance(value,data.ProjectionCell) else value
                for i,row in enumerate(values) for j,value in enumerate(row))
    return data.ProjectionPage(error,0x1000,width,start,7,count if total is None else total,
                               valid,count,cells)


class Context:
    def __init__(self,pages):
        self.pages=list(pages);self._types={"Country":5,"State":6}
        self.adapters={"data":"data.dll","console":"console.dll"};self.native=object();self.process=object()
        self.calls=[];self.performs=[];self.remembered=[];self.selected_reads=0;self.diagnostics=[]
        self.world=NS(marker="original world")
    async def country_id(self):self.selected_reads+=1;return 0
    async def type_id(self,name):raise AssertionError("cached type must not trigger world/metadata call")
    async def query(self,label,factory,*,owner=None):
        q=factory();self.calls.append((label,q));return self.pages.pop(0)
    async def perform(self,label,fn):self.performs.append(label);return await fn()
    def _remember(self,q):self.remembered.append(q)


class PoliticalProjectionTests(unittest.IsolatedAsyncioTestCase):
    def api(self,pages):return NS(_ctx=Context(pages))

    async def test_scalar_preserves_zero_false_and_exact_fixed(self):
        api=self.api([page([[0,False,data.FixedPoint(123456)]])])
        fields=(p.Field("count","Country.GetCount",data.Kind.I32),
                p.Field("flag","Country.IsThing",data.Kind.BOOL),
                p.Field("points","Country.GetPoints",data.Kind.FIXED))
        with patch.object(p.data,"project_page",return_value=NS(cleanup_error=None)) as call:
            result=await p.country_snapshot(api,"test",fields)
        self.assertEqual((result["count"],result["flag"],result["points"]),(0,False,Decimal("1.23456")))
        self.assertTrue(result["complete"]);self.assertEqual(len(api._ctx.calls),1)
        self.assertEqual(call.call_args.args[1],tuple(f.expression for f in fields))
        self.assertEqual(len(api._ctx.performs),1)

    async def test_signed_full_id_preserves_reference_bits(self):
        api=self.api([page([[-2147483646]])])
        with patch.object(p.data,"project_page",return_value=NS(cleanup_error=None)):
            result=await p.country_snapshot(api,"id",(p.Field("id","Country.GetID",data.Kind.I32,full_id=True),))
        self.assertEqual(result["id"],0x80000002)

    async def test_invalid_sentinel_remains_visible_with_reason(self):
        api=self.api([page([[-1]])])
        with patch.object(p.data,"project_page",return_value=NS(cleanup_error=None)):
            result=await p.country_snapshot(api,"id",(p.Field("id","Country.GetID",data.Kind.I32,full_id=True),))
        self.assertEqual(result["id"],0xffffffff)
        self.assertIn("sentinel",result["reasons"]["fields"]["id"])

    async def test_error_partial_text_not_lost(self):
        native=cell("#bold kept#!",error=122,total=30)
        api=self.api([page([[native]],error=122)])
        with patch.object(p.data,"project_page",return_value=NS(cleanup_error=None)):
            result=await p.country_snapshot(api,"text",(p.Field("name","Country.GetName",data.Kind.STRING,text=True),))
        self.assertEqual(result["name"],"kept");self.assertEqual(result["name_raw"],"#bold kept#!")
        self.assertFalse(result["complete"]);self.assertEqual(result["native_pages"][0].error,122)

    async def test_wrong_native_kind_keeps_value_not_fake_none(self):
        api=self.api([page([["not int"]])])
        with patch.object(p.data,"project_page",return_value=NS(cleanup_error=None)):
            result=await p.country_snapshot(api,"kind",(p.Field("amount","Country.GetAmount",data.Kind.I32),))
        self.assertEqual(result["amount"],"not int")
        self.assertIn("differs",result["reasons"]["fields"]["amount"])

    async def test129rows_use3pages_not_fields_times_rows(self):
        api=self.api([page([[i] for i in range(64)],total=129),
                      page([[i] for i in range(64,128)],start=64,total=129),
                      page([[128]],start=128,total=129)])
        with patch.object(p.data,"project_page",return_value=NS(cleanup_error=None)) as call:
            result=await p.country_source(api,"list","Country.AccessStates",(p.Field("id","State.GetID"),))
        self.assertEqual(len(result["rows"]),129);self.assertEqual(call.call_count,3)
        self.assertEqual([c.kwargs["start"] for c in call.call_args_list],[0,64,128])
        self.assertEqual(result["rows"][-1]["source_index"],128)
        self.assertEqual(api._ctx.selected_reads,1)

    async def test_empty_native_list_is_observed(self):
        api=self.api([page([],total=0)])
        with patch.object(p.data,"project_page",return_value=NS(cleanup_error=None)):
            result=await p.country_source(api,"empty","Country.GetSubjects",(p.Field("id","Country.GetID"),))
        self.assertTrue(result["complete"]);self.assertEqual(result["rows"],[])

    async def test_failed_source_not_empty_success(self):
        api=self.api([page([],error=87,valid=False)])
        with patch.object(p.data,"project_page",return_value=NS(cleanup_error=None)):
            result=await p.country_source(api,"empty","Country.GetSubjects",(p.Field("id","Country.GetID"),))
        self.assertFalse(result["complete"]);self.assertIsNone(result["source_count"])

    async def test_cleanup_failure_does_not_mask_native_values(self):
        warning=OSError("cleanup");q=NS(cleanup_error=warning);api=self.api([page([[7]])])
        with patch.object(p.data,"project_page",return_value=q):
            result=await p.country_snapshot(api,"cleanup",(p.Field("value","Country.GetValue"),))
        self.assertEqual(result["value"],7);self.assertTrue(result["complete"])
        self.assertIs(api._ctx.diagnostics[0]["cleanup_error"],warning);self.assertEqual(api._ctx.remembered,[q])

    async def test_nested_steps_forwarded_without_extra_binding_or_probes(self):
        api=self.api([page([[2]])]);steps=(data.CreationStep("Country.AccessAllInterestGroups",3),)
        with patch.object(p.data,"project_page",return_value=NS(cleanup_error=None)) as call:
            await p.country_source(api,"nested","InterestGroup.AccessTraits",(p.Field("x","InterestGroupTrait.GetName"),),
                                   country_id=0,steps=steps,source_arguments=("Country.Self",),perform=False)
        self.assertEqual(call.call_args.kwargs["steps"],steps)
        self.assertEqual(call.call_args.kwargs["source_arguments"],("Country.Self",))
        self.assertEqual(api._ctx.performs,[]);self.assertEqual(api._ctx.selected_reads,0)

    async def test_nonadvancing_source_stops_without_replay(self):
        api=self.api([page([],total=1)])
        with patch.object(p.data,"project_page",return_value=NS(cleanup_error=None)) as call:
            result=await p.country_source(api,"stuck","Country.GetSubjects",(p.Field("id","Country.GetID"),))
        self.assertEqual(call.call_count,1);self.assertFalse(result["complete"])

    async def test_state_and_country_bindings_not_duplicate_country_alias(self):
        api=self.api([page([[1]])])
        with patch.object(p.data,"project_page",return_value=NS(cleanup_error=None)) as call:
            result=await p.state_snapshot(api,"state",(p.Field("value","State.GetID"),),state_id=1,country_id=0)
        bindings=call.call_args.args[2][0]
        self.assertEqual([b.domain for b in bindings],["Country","State"])
        self.assertEqual(result["state_id"],1)

    async def test_snapshot_uses_original_run_projection_protocol(self):
        from pdx_runtime.v3_game._run import Run as _Run
        original = page([[0, False, None]])
        api = self.api([original])
        run = _Run(api._ctx, "original")
        # The scope pins its source adapter. A later context change must not
        # redirect the retained descriptor read to a different adapter.
        api._ctx.adapters["data"] = "later.dll"
        fields = (p.Field("id", "Country.GetID", data.Kind.I32, full_id=True),
                  p.Field("flag", "Country.IsThing", data.Kind.BOOL),
                  p.Field("empty", "Country.GetEmpty", data.Kind.EMPTY))
        with patch.object(p.data, "project_page", return_value=NS(cleanup_error=None)) as call:
            result = await p.snapshot(api, "retained", fields, ((),), owner=run, perform=False)
        self.assertEqual((result["id"], result["flag"], result["empty"]), (0, False, None))
        self.assertTrue(result["complete"])
        self.assertEqual(call.call_args.kwargs["adapter"], "data.dll")
        self.assertIs(run.operations[0][1], original)
        self.assertEqual(api._ctx.performs, [])

    async def test_owner_projection_preserves_native_error_and_cleanup_separately(self):
        from pdx_runtime.v3_game._run import Run as _Run
        original = page([[cell(0, error=122)]], error=122)
        api = self.api([original])
        run = _Run(api._ctx, "original")
        warning = OSError("cleanup")
        query = NS(cleanup_error=warning)
        with patch.object(p.data, "project_page", return_value=query):
            result = await p.snapshot(api, "retained", (p.Field("value", "Country.GetID"),),
                                      ((),), owner=run)
        self.assertEqual(result["value"], 0)
        self.assertFalse(result["complete"])
        self.assertIs(result["native_pages"][0], original)
        self.assertIn("122", result["reasons"]["fields"]["value"])
        self.assertIs(run.operations[0][1], original)
        self.assertEqual(api._ctx.remembered, [query])
        self.assertTrue(any(isinstance(item, dict) and item.get("cleanup_error") is warning
                            for item in api._ctx.diagnostics))
        self.assertEqual(api._ctx.performs, ["retained"])

    async def test_input_fields_bounded_without_query(self):
        api=self.api([])
        with self.assertRaises(ValueError):
            await p.project(api,"large",[p.Field(str(i),"Country.GetID") for i in range(33)],((),))
        self.assertEqual(api._ctx.calls,[])


if __name__=="__main__":unittest.main()
