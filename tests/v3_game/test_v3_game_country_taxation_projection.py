"""Taxation shares genuine State name pages; CPU-only affected cases."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as D, v3_objects as O
from pdx_runtime.v3_game.country import CountryAPI, _projection_supported

ACTOR = 0x300009c
GETTERS = ("CalcTaxCapacity", "CalcTaxCapacityUsage", "CalcTaxCapacityBalance")
TAX_FIELDS = tuple("State." + getter for getter in GETTERS)


class Context:
    def __init__(self):
        self.native = object(); self.world = 0x7000
        self.adapters = {"data": 7, "objects": 8}; self.resolves = []
        self.process = NS(resolve_export=self.resolve); self.calls = []; self.diagnostics = []
        self.refreshes = 0; self.country_calls = 0; self.types = []; self.remembered = []
    def resolve(self, module, symbol, **kwargs):
        self.resolves.append((module, symbol)); return 0x180010000
    async def blocking(self, function, *args, **kwargs): return function(*args, **kwargs)
    async def refresh(self): self.refreshes += 1
    async def country_id(self): self.country_calls += 1; return ACTOR
    async def type_id(self, name): self.types.append(name); return 83
    async def query(self, label, factory): self.calls.append(label); return factory().result
    async def perform(self, label, work): return await work()
    def _remember(self, query): self.remembered.append(query)


class Native:
    def __init__(self, ctx, count=43):
        self.ctx = ctx; self.ids = [0xA3000000+i for i in range(count)]
        self.pages = []; self.evaluated = []; self.released = []; self.names = True
        self.changed = {}; self.missing = set(); self.page_error = 0; self.count_valid = True
        self.cleanup = None; self.fail_at = None; self.failure = None; self.failed_query = None
    def objects(self, *args, **kwargs):
        rows = tuple(NS(error=0, resolved_id=identity, owner_id=ACTOR) for identity in self.ids)
        return NS(result=NS(error=0, records=rows+(NS(error=0,resolved_id=99,owner_id=ACTOR+1),),
            truncated=False, next_index=len(rows)+1))
    def metadata(self, *args, **kwargs):
        getters = (("GetNameNoFormatting", "GetName") if self.names else ()) + GETTERS
        rows = tuple(NS(type_id=83, name=getter, argument_count=0, callbacks=(1,0,0,0))
            for getter in getters)
        return NS(result=NS(error=0, rows=rows, next=len(rows), total=len(rows)))
    def bits(self, expression, identity):
        return {TAX_FIELDS[0]:10012345, TAX_FIELDS[1]:12054321,
            TAX_FIELDS[2]:-2041976}[expression] if identity != self.ids[-1] else 0
    def cell(self, row, column, expression, identity):
        if expression in TAX_FIELDS:
            bits = self.bits(expression,identity)&((1<<64)-1)
            return D.ProjectionCell(row,column,0,D.Kind.FIXED,84,bits,bits.to_bytes(8,"little"),8)
        text = ("State " + str(identity)).encode()
        return D.ProjectionCell(row,column,0,D.Kind.STRING,85,0,text,len(text))
    def page(self, client, fields, binding_rows, **kwargs):
        fields, binding_rows = tuple(fields), tuple(binding_rows)
        self.pages.append((fields,binding_rows))
        cells = []
        for row, bindings in enumerate(binding_rows):
            identity = bindings[0].object
            for col, expression in enumerate(fields):
                if (identity, expression) in self.missing: continue
                original = self.cell(row,col,expression,identity)
                cells.append(self.changed.get((identity,expression), lambda x:x)(original))
        page = D.ProjectionPage(self.page_error,self.ctx.world,len(fields),0,17,
            len(binding_rows),self.count_valid,len(binding_rows),tuple(cells))
        query = NS(result=page, cleanup_error=self.cleanup)
        if self.fail_at == len(self.pages):
            self.failed_query = query; query.partial_result = page
            self.failure.query = query
            raise self.failure
        return query
    def evaluate(self, client, expression, bindings, **kwargs):
        self.evaluated.append((expression,bindings[0].object))
        identity=bindings[0].object
        bits=self.bits(expression,identity)&((1<<64)-1)
        return NS(result=NS(error=0, kind=D.Kind.FIXED, value=D.FixedPoint(bits),
            value_handle=len(self.evaluated), snapshot=bits.to_bytes(8,"little"),snapshot_bytes=8,total=8))
    def release(self, client, handle, **kwargs):
        self.released.append(handle); return NS(result=NS(error=0,value_handle=0))


class BeforeTaxBatch(CountryAPI):
    """Prior affected traversal, retained only as a comparison oracle."""
    async def _state_rows(self, read, country_id, *, details=False):
        records = await self._records(read, O.Domain.STATE)
        domestic = {r.resolved_id:r for r in records if r.owner_id == country_id}
        if not domestic: return []
        state_type = await read.type_id("State")
        projected = (await self._project_records(read,"State",state_type,list(domestic))
                     if await _projection_supported(self._ctx) else None)
        rows=[]
        for state_id in domestic:
            if projected is None:
                name, reason = await self._name(read,"State",D.ObjectBinding.state(state_type,state_id))
            else:
                name, reason = projected[state_id]["name"], projected[state_id]["name_reason"]
            row={"id":state_id,"name":name,"country_id":country_id,"reasons":{"name":reason} if reason else {}}
            if details: row["taxation"]=await self._state_taxation(read,D.ObjectBinding.state(state_type,state_id))
            rows.append(row); await read.close()
        return rows


class Tests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, count=43, api_type=CountryAPI):
        ctx=Context(); native=Native(ctx,count)
        for method, side in (("project_page",native.page),("enumerate_functions",native.metadata),
                ("evaluate",native.evaluate),("release",native.release)):
            patcher=patch.object(D,method,side_effect=side); patcher.start(); self.addCleanup(patcher.stop)
        patcher=patch.object(O,"enumerate_objects",side_effect=native.objects)
        patcher.start(); self.addCleanup(patcher.stop)
        return ctx,native,api_type(ctx)

    async def test43states_taxation_in_same_name_page_no_scalar_evaluate_or_release(self):
        ctx,n,api=self.fixture(); rows=await api.states(details=True)
        self.assertEqual(len(rows),43); self.assertEqual(len(n.pages),1)
        self.assertEqual(n.pages[0][0],("State.GetNameNoFormatting",)+TAX_FIELDS)
        self.assertEqual([r[0].object for r in n.pages[0][1]],n.ids)
        self.assertTrue(all(r[0].domain=="State" and not r[0].retained for r in n.pages[0][1]))
        self.assertEqual(n.evaluated,[]); self.assertEqual(n.released,[])
        self.assertEqual((ctx.refreshes,ctx.country_calls,ctx.types),(1,1,["State"]))
        self.assertEqual(rows[0]["taxation"]["capacity"],Decimal("100.12345"))
        self.assertTrue(rows[0]["taxation"]["insufficient"])
        self.assertEqual(rows[-1]["taxation"]["capacity"],Decimal(0))
        self.assertIs(rows[-1]["taxation"]["insufficient"],False)
        self.assertIs(rows[0]["taxation"]["native_pages"][0],rows[-1]["taxation"]["native_pages"][0])

    async def test65states_continue_fullids_in64plus1_pages_without_totalcap(self):
        _,n,api=self.fixture(65); rows=await api.states(details=True)
        self.assertEqual([len(bindings) for _,bindings in n.pages],[64,1])
        self.assertEqual([r["id"] for r in rows],n.ids)
        self.assertEqual(rows[-1]["taxation"]["native_row"],0)
        self.assertEqual(rows[-2]["taxation"]["native_row"],63)

    async def test_default_states_keeps_name_only_no_tax_fields(self):
        _,n,api=self.fixture(); rows=await api.states()
        self.assertEqual(n.pages[0][0],("State.GetNameNoFormatting",))
        self.assertTrue(all("taxation" not in row for row in rows)); self.assertEqual(n.evaluated,[])

    async def test_tax_fields_still_copy_when_no_name_getter_is_registered(self):
        _,n,api=self.fixture(); n.names=False; rows=await api.states(details=True)
        self.assertEqual(n.pages[0][0],TAX_FIELDS)
        self.assertTrue(all(row["name"] is None for row in rows))
        self.assertEqual(rows[0]["taxation"]["balance"],Decimal("-20.41976"))

    async def test_cell_error_keeps_zero_and_derived_false_with_reason(self):
        _,n,api=self.fixture(); last=n.ids[-1]
        n.changed[last,TAX_FIELDS[0]]=lambda c:replace(c,error=13)
        row=(await api.states(details=True))[-1]["taxation"]
        self.assertEqual(row["capacity"],Decimal(0)); self.assertIs(row["insufficient"],False)
        self.assertIn("13",row["reasons"]["capacity"]); self.assertIn("13",row["reasons"]["insufficient"])
        self.assertEqual(row["native_pages"][0].cells[-3].error,13)

    async def test_missing_usage_stays_none_not_false_and_other_fields_survive(self):
        _,n,api=self.fixture(); n.missing.add((n.ids[0],TAX_FIELDS[1]))
        row=(await api.states(details=True))[0]["taxation"]
        self.assertIsNone(row["usage"]); self.assertIsNone(row["insufficient"])
        self.assertEqual(row["capacity"],Decimal("100.12345"))
        self.assertIn("did not copy",row["reasons"]["usage"])


    async def test_second_page_interrupt_keeps_completed_rows_and_original_partial_query(self):
        _,n,api=self.fixture(65); n.fail_at=2; n.failure=OSError("copy interrupted")
        with self.assertRaises(OSError) as caught: await api.states(details=True)
        self.assertIs(caught.exception,n.failure); self.assertIs(n.failure.query,n.failed_query)
        prefix=n.failure.country_state_projection
        self.assertEqual(len(prefix["native_pages"]),1)
        self.assertEqual(prefix["records"][n.ids[0]]["taxation"]["capacity"],Decimal("100.12345"))
        self.assertIs(prefix["pending_query"],n.failed_query); self.assertEqual(len(n.pages),2)
        self.assertEqual(n.evaluated,[]); self.assertEqual(n.released,[])

    async def test_name_fallback_interrupt_keeps_already_copied_taxation_without_requery(self):
        _,n,api=self.fixture(1); n.fail_at=2; n.failure=OSError("name copy interrupted")
        n.changed[n.ids[0],"State.GetNameNoFormatting"]=lambda c:replace(c,kind=D.Kind.EMPTY,error=13)
        with self.assertRaises(OSError) as caught: await api.states(details=True)
        self.assertIs(caught.exception,n.failure)
        prefix=n.failure.country_state_projection
        self.assertEqual(prefix["records"][n.ids[0]]["taxation"]["capacity"],Decimal(0))
        self.assertEqual(len(prefix["native_pages"]),1)
        self.assertEqual(n.pages[1][0],("State.GetName",))
        self.assertIs(prefix["pending_query"],n.failed_query)
        self.assertEqual(n.evaluated,[]); self.assertEqual(n.released,[])

    async def test_healthy_old_traversal_matches_all_public_values_and_saves258_scalar_queries(self):
        oldctx,n,oldapi=self.fixture(api_type=BeforeTaxBatch)
        old=await oldapi.states(details=True)
        self.assertEqual((len(n.evaluated),len(n.released)),(129,129))
        newctx=Context(); n.ctx=newctx
        n.pages.clear(); n.evaluated.clear(); n.released.clear()
        new=await CountryAPI(newctx).states(details=True)
        for row in new:
            row["taxation"].pop("native_pages"); row["taxation"].pop("native_row")
        self.assertEqual(new,old); self.assertEqual(len(oldctx.calls)-len(newctx.calls),258)
        self.assertEqual(n.evaluated,[]); self.assertEqual(n.released,[])


if __name__=="__main__": unittest.main()
