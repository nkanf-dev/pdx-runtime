"""Current-query cleanup warning handoff. Real Page encoder, mocked submit."""
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as D
from pdx_runtime.v3_game import _projection as P

FIELDS=("State.GetInfrastructure",)
BINDINGS=((D.ObjectBinding.state(83,0xA3000001),),)

def page(bits=100000,*,error=0):
    return D.ProjectionPage(0,0x7000,1,0,17,1,True,1,
        (D.ProjectionCell(0,0,error,D.Kind.FIXED,84,bits,bits.to_bytes(8,"little"),8),))

class Context:
    def __init__(self):
        self.native=object(); self.process=object(); self.adapters={"data":7}
        self.diagnostics=[]; self.remembered=[]
    async def query(self,label,factory): return factory().result
    def _remember(self,query): self.remembered.append(query)

class Tests(unittest.IsolatedAsyncioTestCase):

    async def test_caller_sink_gets_same_record_and_native_error_zero_remains_primary(self):
        ctx=Context(); sink=[]; warning=OSError("free failed")
        original=NS(result=page(0,error=13),cleanup_error=warning)
        with patch.object(D,"submit_callback",return_value=original):
            result=await P._projection_page(ctx,"scalar",FIELDS,BINDINGS,cleanup_warnings=sink)
        self.assertEqual(len(sink),1); self.assertIs(sink[0],ctx.diagnostics[0])
        self.assertIs(sink[0]["query"],original); self.assertIs(sink[0]["result"],result)
        value,reason=P._projection_value(result.cells[0])
        self.assertEqual(value.signed,0); self.assertIn("13",reason)
        self.assertIs(sink[0]["cleanup_error"],warning)

    async def test_collector_failure_after_append_is_not_retried_or_mixed_with_next_page(self):
        ctx=Context(); delivery_error=RuntimeError("collector stopped after append")
        class StopAfterAppend(list):
            attempts=0
            def append(self,record):
                self.attempts+=1; super().append(record); raise delivery_error
        failed_sink=StopAfterAppend(); next_sink=[]
        qa=NS(result=page(1),cleanup_error=OSError("A"))
        qb=NS(result=page(2),cleanup_error=OSError("B"))
        with patch.object(D,"submit_callback",side_effect=(qa,qb)) as submit:
            ra=await P._projection_page(ctx,"same",FIELDS,BINDINGS,cleanup_warnings=failed_sink)
            rb=await P._projection_page(ctx,"same",FIELDS,BINDINGS,cleanup_warnings=next_sink)
        self.assertIs(ra,qa.result); self.assertIs(rb,qb.result)
        self.assertEqual((failed_sink.attempts,len(failed_sink),len(next_sink),submit.call_count),(1,1,1,2))
        self.assertIs(failed_sink[0],ctx.diagnostics[0])
        self.assertIs(failed_sink[0]["cleanup_warning_delivery_error"],delivery_error)
        self.assertIs(next_sink[0]["query"],qb)
        self.assertNotIn("cleanup_warning_delivery_error",next_sink[0])
        self.assertEqual(ctx.remembered,[qa,qb])

    async def test_closed_collector_preserves_original_partial_page_and_raw_bytes(self):
        ctx=Context(); delivery_error=RuntimeError("partial collector closed")
        class ClosedCollector(list):
            def append(self,record): raise delivery_error
        partial=replace(page(),cells=(D.ProjectionCell(0,0,13,D.Kind.STRING,-1,0,b"prefix",30),))
        original=NS(result=partial,cleanup_error=OSError("partial cleanup"))
        with patch.object(D,"submit_callback",return_value=original) as submit:
            result=await P._projection_page(ctx,"partial",FIELDS,BINDINGS,cleanup_warnings=ClosedCollector())
        self.assertIs(result,partial); self.assertIs(ctx.diagnostics[0]["result"],partial)
        self.assertIs(ctx.diagnostics[0]["query"],original)
        self.assertIs(ctx.diagnostics[0]["cleanup_error"],original.cleanup_error)
        self.assertIs(ctx.diagnostics[0]["cleanup_warning_delivery_error"],delivery_error)
        self.assertEqual(result.cells[0].snapshot,b"prefix"); self.assertFalse(result.cells[0].complete)
        value,reason=P._projection_value(result.cells[0])
        self.assertEqual(value,"prefix"); self.assertIn("13",reason); self.assertIn("incomplete",reason)
        self.assertEqual(submit.call_count,1); self.assertEqual(ctx.remembered,[original])

if __name__=="__main__":unittest.main()
