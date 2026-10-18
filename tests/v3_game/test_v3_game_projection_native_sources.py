"""Canonical opaque SourceA wire/discovery. No native VM/game execution.
Only GetGameRules.GetRulePresets -> PRESET_STORED_SETTINGS is contract-backed.
Other expression/NativeField combinations test wire formats only, not row-type
compatibility, provider registration or native formal arguments.
"""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as D
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_game import _projection as P

ORIGINAL="PdxV3DataProjectOriginalSourcesInterface"
FIELDS="PdxV3DataProjectNativeFieldsInterface"

class Process:
    def __init__(self): self.lookups=[]; self.error=None
    def resolve_export(self,adapter,symbol,**kwargs):
        self.lookups.append((adapter,symbol))
        if self.error is not None: raise self.error
        return 0x180010000

class Context:
    def __init__(self):
        self.native=object(); self.process=Process(); self.adapters={"data":7}
        self.diagnostics=[]; self.queries=[]
    async def blocking(self,function,*args,**kwargs): return function(*args,**kwargs)
    async def query(self,label,factory): self.queries.append(label); return factory().result

def native_source(): return D.NativeSource(D.NativeSourceKind.PRESET_STORED_SETTINGS)
def prefix(): return D.CreationStep("GetGameRules.GetRulePresets",0)
def page():
    text=b"setting_key"
    return D.ProjectionPage(0,0,1,0,1,1,True,1,
        (D.ProjectionCell(0,0,0,D.Kind.STRING,-1,0,text,len(text)),))
def group_page():
    return D.ProjectionPage(0,0,1,0,1,0,False,1,page().cells,
        sources=(D.SourceObservation(0,0,True,True,1,0,1,1,0,True,True),
                 D.SourceObservation(1,0,True,True,0,0,0,0,1,True,True)),
        next_cursor=(2,0),range_complete=True,grouped=True,_group_has_more=False)

class Tests(unittest.IsolatedAsyncioTestCase):

    async def test_native_source_discovery_keeps_original_transport_error(self):
        ctx=Context(); original=BridgeError("resolve_export",5,reason="access denied")
        ctx.process.error=original
        with self.assertRaises(BridgeError) as caught:
            await P._projection_supported(ctx,native_sources=True)
        self.assertIs(caught.exception,original)
        self.assertNotIn((7,ORIGINAL),ctx._projection_capabilities)
        ctx.process.error=None
        self.assertTrue(await P._projection_supported(ctx,native_sources=True))
        self.assertEqual(ctx.process.lookups,[(7,ORIGINAL),(7,ORIGINAL)])
        self.assertEqual(ctx.queries,[])

    async def test_real_producer_keeps_single_opaque_source_and_fresh_preset_step(self):
        ctx=Context(); source=native_source(); step=prefix(); submitted=[]; passed=[]
        real=D.project_page
        def actual(*args,**kwargs):
            passed.append(kwargs); return real(*args,**kwargs)
        def submit(client,initial,**kwargs):
            submitted.append((initial,kwargs)); return NS(result=page())
        with patch.object(D,"project_page",side_effect=actual),patch.object(D,"submit_callback",side_effect=submit):
            answer=await P._projection_page(ctx,"preset.settings",("GameRuleSetting.GetKey",),
                ((),),source_expression=source,steps=(step,))
        self.assertIs(passed[0]["source_expression"],source); self.assertIs(passed[0]["steps"][0],step)
        self.assertEqual(len(submitted),1); raw,options=submitted[0]; h=D.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual(h[18],D.PAGE_SCOPED|D.PAGE_ORIGINAL_SOURCES)
        self.assertEqual(D.PAGE_SOURCE.unpack_from(raw,h[7]),(1,0,0,0))
        scope=D.PAGE_SCOPED_SUFFIX.unpack_from(raw,D.PAGE_REQUEST.size)
        record=D.CREATION_STEP.unpack_from(raw,scope[0])
        self.assertEqual(raw[record[0]:record[0]+record[1]],b"GetGameRules.GetRulePresets")
        self.assertEqual(options["callback_symbol"],ORIGINAL)
        self.assertNotIn(b"NativeSource",raw); self.assertNotIn(b"PRESET_STORED_SETTINGS",raw)
        self.assertEqual(P._projection_value(answer.cells[0]),("setting_key",None))
        self.assertEqual(ctx.process.lookups,[]); self.assertEqual(ctx.queries,["preset.settings"])

    async def test_group_iterator_keeps_original_source_observations(self):
        ctx=Context(); submitted=[]
        def submit(client,initial,**kwargs):
            submitted.append((initial,kwargs)); return NS(result=group_page())
        sources=(native_source(),native_source())
        with patch.object(D,"submit_callback",side_effect=submit):
            rows=[row async for row in P._projection_source_pages(ctx,"preset",("GameRuleSetting.GetKey",),
                ((),),sources,steps=(prefix(),))]
        self.assertEqual([row[0] for row in rows],[0,1]); self.assertEqual(len(submitted),1)
        self.assertEqual(ctx.process.lookups,[(7,ORIGINAL)])
        self.assertEqual(submitted[0][1]["callback_symbol"],ORIGINAL)

    async def test_group_iterator_routes_typed_native_fields_to_its_callback(self):
        ctx=Context(); submitted=[]
        def submit(client,initial,**kwargs):
            submitted.append((initial,kwargs)); return NS(result=group_page())
        step=D.CreationStep("Root.Rows(Root.Self)",0,source_arguments=("Root.Self",))
        with patch.object(D,"submit_callback",side_effect=submit):
            rows=[row async for row in P._projection_source_pages(ctx,"format-only",(D.NativeField(D.NativeFieldKind.DECREE_TYPE_KEY),),
                ((),),("Owner.Decrees","Owner.OtherDecrees"),steps=(step,))]
        self.assertEqual(len(rows),2); self.assertEqual(ctx.process.lookups,[(7,FIELDS)])
        self.assertEqual(submitted[0][1]["callback_symbol"],FIELDS)
        self.assertFalse(D.PAGE_REQUEST.unpack_from(submitted[0][0])[18]&D.PAGE_ORIGINAL_SOURCES)

    async def test_original_copy_exception_keeps_same_source_object_steps_and_query(self):
        ctx=Context(); source=native_source(); step=prefix(); original=NS(partial_result=page())
        failure=OSError("copy interrupted"); failure.query=original
        with patch.object(D,"project_page",side_effect=failure) as send:
            with self.assertRaises(OSError) as caught:
                await P._projection_page(ctx,"partial",("GameRuleSetting.GetKey",),((),),source_expression=source,steps=(step,))
        self.assertIs(caught.exception,failure); self.assertIs(failure.query,original)
        self.assertIs(failure.projection_context["source_expression"],source)
        self.assertIs(failure.projection_context["steps"][0],step); self.assertEqual(send.call_count,1)

if __name__=="__main__":unittest.main()
