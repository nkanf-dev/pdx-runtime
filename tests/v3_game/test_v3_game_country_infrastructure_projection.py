"""State infrastructure copied-page fields/errors; post-cut3 CPU-only evidence."""
from dataclasses import replace
from decimal import Decimal, localcontext
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as D, v3_objects as O
from pdx_runtime.v3_game.country import CountryAPI

ACTOR=0x300009c
STATE=0xA3000001
GETTERS=("GetInfrastructure","GetInfrastructureUsage","GetInfrastructureBalance",
    "GetMarketAccess","IsIsolatedFromMarket","GetMarketAccessStatus")
EXPRESSIONS=tuple("State."+getter for getter in GETTERS)
VALUES=(10012345,12054321,-2041976,0,False,"#native Market connected#!")


class Context:
    def __init__(self):
        self.native=object(); self.world=0x7000; self.adapters={"data":7,"objects":8}
        self.lookups=[]; self.process=NS(resolve_export=self.resolve)
        self.calls=[]; self.types=[]; self.diagnostics=[]; self.remembered=[]
        self.refreshes=0; self.country_calls=0
    def resolve(self,adapter,symbol,**kwargs): self.lookups.append((adapter,symbol)); return 0x180010000
    async def blocking(self,function,*args,**kwargs): return function(*args,**kwargs)
    async def refresh(self): self.refreshes+=1
    async def country_id(self): self.country_calls+=1; return ACTOR
    async def type_id(self,name): self.types.append(name); return 83
    async def query(self,label,factory): self.calls.append(label); return factory().result
    async def perform(self,label,work): return await work()
    def _remember(self,query): self.remembered.append(query)


class Native:
    def __init__(self,ctx,state=STATE):
        self.ctx=ctx; self.state=state; self.pages=[]; self.evaluated=[]; self.released=[]
        self.metadata_calls=0; self.object_calls=0; self.owner=ACTOR
        self.changed={}; self.missing=set(); self.page_error=0; self.count_valid=True
        self.cleanup=None; self.failure=None; self.original=None; self.world=ctx.world
    def objects(self,*args,**kwargs):
        self.object_calls+=1
        return NS(result=NS(error=0,world=self.ctx.world,records=(NS(error=0,
            resolved_id=self.state,owner_id=self.owner),)))
    def metadata(self,*args,**kwargs):
        self.metadata_calls+=1
        rows=tuple(NS(type_id=83,name=getter,argument_count=0,callbacks=(1,0,0,0)) for getter in GETTERS)
        return NS(result=NS(error=0,rows=rows,next=len(rows),total=len(rows)))
    def cell(self,index):
        value=VALUES[index]
        kind=D.Kind.STRING if index==5 else D.Kind.BOOL if index==4 else D.Kind.FIXED
        bits=0 if kind==D.Kind.STRING else int(value)&((1<<64)-1)
        raw=value.encode() if kind==D.Kind.STRING else bits.to_bytes(8,"little")
        return D.ProjectionCell(0,index,0,kind,84,bits,raw,len(raw))
    def submit(self,client,initial,**kwargs):
        self.pages.append((initial,kwargs))
        cells=[self.changed.get(i,lambda c:c)(self.cell(i)) for i in range(6) if i not in self.missing]
        page=D.ProjectionPage(self.page_error,self.world,6,0,17,1,self.count_valid,1,tuple(cells))
        self.original=NS(result=page,partial_result=page,cleanup_error=self.cleanup)
        if self.failure is not None:
            self.failure.query=self.original; raise self.failure
        return self.original
    def evaluate(self,client,expression,bindings,**kwargs):
        self.evaluated.append(expression); index=EXPRESSIONS.index(expression); cell=self.cell(index)
        return NS(result=NS(error=0,kind=cell.kind,value=cell.value,
            value_handle=len(self.evaluated),snapshot=cell.snapshot,snapshot_bytes=len(cell.snapshot),total=cell.total))
    def release(self,client,handle,**kwargs):
        self.released.append(handle); return NS(result=NS(error=0,value_handle=0))


class Tests(unittest.IsolatedAsyncioTestCase):
    def fixture(self,state=STATE):
        ctx=Context(); native=Native(ctx,state)
        for obj,name,side in ((D,"submit_callback",native.submit),(D,"evaluate",native.evaluate),
                (D,"release",native.release),(D,"enumerate_functions",native.metadata),
                (O,"read_ids",native.objects)):
            patcher=patch.object(obj,name,side_effect=side); patcher.start(); self.addCleanup(patcher.stop)
        return ctx,native,CountryAPI(ctx)

    async def test_real_page_producer_same_six_fields_one_binding_no_scalar_metadata_or_release(self):
        ctx,n,api=self.fixture()
        with localcontext() as dc:
            dc.prec=2; answer=await api.state_infrastructure(STATE)
        self.assertEqual(len(n.pages),1); raw,options=n.pages[0]; h=D.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((h[4],h[6],h[10],h[18]),(6,1,1,0))
        fields=[D.PAGE_SPAN.unpack_from(raw,h[3]+i*D.PAGE_SPAN.size) for i in range(6)]
        self.assertEqual(tuple(raw[o:o+l].decode() for o,l in fields),EXPRESSIONS)
        offset,count=D.PAGE_SPAN.unpack_from(raw,h[5]); binding=D.BINDING_INPUT.unpack_from(raw,offset)
        self.assertEqual((count,binding[0],binding[2]),(1,83,STATE))
        self.assertEqual(options["callback_symbol"],"PdxV3DataProjectPageInterface")
        self.assertEqual((n.metadata_calls,n.evaluated,n.released),(0,[],[]))
        self.assertEqual((ctx.refreshes,ctx.country_calls,ctx.types,n.object_calls),(1,1,["State"],1))
        self.assertEqual(answer["capacity"],Decimal("100.12345")); self.assertEqual(answer["balance"],Decimal("-20.41976"))
        self.assertEqual(answer["market_access"],Decimal(0)); self.assertIs(answer["isolated_from_market"],False)
        self.assertEqual(answer["market_access_status_text"],VALUES[5]); self.assertTrue(answer["available"])
        self.assertIs(answer["native_pages"][0],n.original.result)

    async def test_legacy_and_page_healthy_values_match_with14_to2_fixture_calls(self):
        oldctx,n,oldapi=self.fixture(); oldctx.process=object()
        old=await oldapi.state_infrastructure(STATE)
        self.assertEqual((n.metadata_calls,len(n.evaluated),len(n.released)),(1,6,6))
        newctx=Context(); n.ctx=newctx; n.pages.clear(); n.evaluated.clear(); n.released.clear(); n.metadata_calls=0
        new=await CountryAPI(newctx).state_infrastructure(STATE)
        self.assertEqual({k:v for k,v in new.items() if k!="native_pages"},old)
        self.assertEqual((len(oldctx.calls),len(newctx.calls)),(14,2))
        self.assertEqual((n.metadata_calls,n.evaluated,n.released),(0,[],[]))

    async def test_partial_status_native_error_keeps_text_and_original_bytes(self):
        _,n,api=self.fixture()
        n.changed[5]=lambda c:replace(c,error=D.ERROR_MORE_DATA,snapshot=b"native prefix",total=100)
        row=await api.state_infrastructure(STATE)
        self.assertEqual(row["market_access_status_text"],"native prefix")
        self.assertIn("incomplete",row["reasons"]["market_access_status_text"])
        self.assertEqual(row["native_pages"][0].cells[-1].snapshot,b"native prefix")
        self.assertEqual(len(n.pages),1); self.assertEqual(n.evaluated,[])


    async def test_invalid_utf8_status_keeps_replacement_text_and_raw_error_reason(self):
        _,n,api=self.fixture(); n.changed[5]=lambda c:replace(c,snapshot=b"A\xff",total=2)
        row=await api.state_infrastructure(STATE)
        self.assertEqual(row["market_access_status_text"],"A\ufffd")
        self.assertIn("invalid UTF-8",row["reasons"]["market_access_status_text"])
        self.assertEqual(row["native_pages"][0].cells[-1].snapshot,b"A\xff")


    async def test_zero_state_id_is_preserved_and_existing_unowned_lookup_still_stops_read(self):
        ctx,n,api=self.fixture(0); row=await api.state_infrastructure(0)
        self.assertEqual(row["state_id"],0); self.assertTrue(row["available"])
        n.pages.clear(); ctx.calls.clear(); ctx.types.clear(); ctx.lookups.clear(); n.owner=ACTOR+1
        row=await api.state_infrastructure(0)
        self.assertFalse(row["available"]); self.assertEqual(n.pages,[])
        self.assertEqual(ctx.types,[]); self.assertEqual(ctx.lookups,[])
        self.assertEqual(ctx.calls,["country.state_infrastructure.state"])


if __name__=="__main__":unittest.main()
