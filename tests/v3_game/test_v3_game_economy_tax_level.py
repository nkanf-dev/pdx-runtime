"""CPU-only current tax-band observations and option annotations.

The real NativeCall batch encoder/decoder and GameContext pending continuation
run over fixture boundary results. No game/PID/DLL access or tax mutation.
"""
import struct
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import native_call, v3_data as data
from pdx_runtime.v3_game import economy
from pdx_runtime.v3_game._core import GameContext, PendingOperation


class Query:
    request_id=77
    def __init__(self,result,delay=0):
        self.result=result;self.delay=delay;self.reads=0;self._released=False;self._result=None
    def read(self):
        self.reads+=1
        if self.delay:
            self.delay-=1
            return None
        self._result=self.result;self._released=True
        return self.result


class Process:
    pid=123
    def resolve_export(self,module,name,*,aliases=None):
        return {"PdxV3ScriptScopeValid":0x180001000,"PdxNativeCallBatchInterface":0x180002000}[name]


class Fixture:
    country=0x0200009C
    def __init__(self):
        self.ctx=GameContext(NS(native=NS(pid=123),namespace={}),Process(),timeout=1,poll_interval=0)
        self.ctx.world=0x700000;self.ctx.image_base=0x140000000
        self.ctx.refresh=AsyncMock(return_value=NS(world=self.ctx.world))
        self.ctx.country_id=AsyncMock(return_value=self.country)
        self.ctx.type_id=AsyncMock(return_value=83)
        self.api=economy.EconomyAPI(self.ctx)
        self.enum=2;self.scope_valid=1;self.scope_error=0;self.call_error=0
        self.access_error=0;self.completed=2;self.delay=0;self.queries=[];self.layouts=[]
    def submit(self,native,callback,initial,**kwargs):
        address=0x200000000
        raw=bytearray(kwargs["prepare_payload"](address))
        header=native_call.BATCH_REQUEST.unpack_from(raw)
        records=[native_call.REQUEST.unpack_from(raw,32+i*56) for i in range(2)]
        args=[struct.unpack_from("<2Q",raw,row[4]-address) for row in records]
        local=32+2*56+4*8
        self.layouts.append((header,records,args,struct.unpack_from("<QH3HQ",raw,local),kwargs))
        # Fixture native completion writes only its own prepared request bytes.
        struct.pack_into("<I",raw,local+24,self.scope_valid)
        native_call.BATCH_REQUEST.pack_into(raw,0,32,0,2,address+32,self.completed)
        for index,rax in enumerate((self.scope_error,self.enum)):
            row=records[index]
            native_call.REQUEST.pack_into(raw,32+index*56,row[0],self.call_error if index==1 else 0,
                row[2],row[3],row[4],rax,bytes(16))
        batch=kwargs["decode_payload"](bytes(raw))
        query=Query(NS(error=self.access_error,entered=1,left=1,payload=bytes(raw),callee_result=batch),self.delay)
        self.queries.append(query)
        return query
    def patch(self):return patch.object(economy.v3_access,"submit_callback",side_effect=self.submit)


class TaxLevelTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_native_enums_return_original_names_without_commands(self):
        fixture=Fixture()
        with fixture.patch(),patch.object(economy.commands,"request") as commands_request,patch.object(economy.commands,"create") as commands_create:
            for index,name in enumerate(("very_low","low","medium","high","very_high")):
                fixture.enum=index
                result=await fixture.api.tax_level()
                self.assertEqual(result,{"country_id":fixture.country,"level":name,"reason":None})
            commands_request.assert_not_called();commands_create.assert_not_called()
        self.assertEqual(len(fixture.queries),5)

    async def test_scope_validation_and_real_getter_share_one_inline_callback(self):
        fixture=Fixture()
        with fixture.patch():result=await fixture.api.tax_level()
        header,records,args,scope,kwargs=fixture.layouts[0]
        local=32+2*56+4*8;address=0x200000000
        self.assertEqual(header[2],2)
        self.assertEqual(records[0][2],0x180001000)
        self.assertEqual(records[1][2],fixture.ctx.image_base+0x252A950)
        self.assertEqual(args,[(address+local+8,address+local+24),(0,address+local)])
        self.assertEqual(scope,(address+local+8,5,0,0,0,fixture.country))
        self.assertEqual((kwargs["mode"],kwargs["flag"],kwargs["dispatch"]),(1,0,"interface"))
        self.assertIs(kwargs["process"],fixture.ctx.process)
        self.assertEqual(result["level"],"medium")

    async def test_fallback_and_unknown_values_are_none_not_a_band(self):
        fixture=Fixture()
        with fixture.patch():
            for index in (5,6,123,0xffffffff):
                fixture.enum=index
                result=await fixture.api.tax_level()
                self.assertIsNone(result["level"])
                self.assertIn("fallback" if index==5 else "Unknown",result["reason"])

    async def test_invalid_country_scope_cannot_masquerade_as_very_low(self):
        fixture=Fixture();fixture.enum=0;fixture.scope_valid=0
        with fixture.patch():result=await fixture.api.tax_level()
        self.assertIsNone(result["level"])
        self.assertIn("not a valid native Country scope",result["reason"])

    async def test_scope_error_and_incomplete_native_results_keep_unknown(self):
        for setting,value,fragment in (("scope_error",5,"validation returned an error"),
                ("call_error",1168,"incomplete"),("completed",1,"incomplete"),
                ("access_error",1168,"access did not complete")):
            with self.subTest(setting=setting):
                fixture=Fixture();setattr(fixture,setting,value)
                with fixture.patch():result=await fixture.api.tax_level()
                self.assertIsNone(result["level"]);self.assertIn(fragment,result["reason"])

    async def test_only_low32_of_native_enum_is_consumed(self):
        fixture=Fixture();fixture.enum=0xDEADBEEF00000003
        with fixture.patch():result=await fixture.api.tax_level()
        self.assertEqual(result["level"],"high")

    async def test_original_query_timeout_resumes_without_another_native_read_submission(self):
        fixture=Fixture();fixture.ctx.timeout=0;fixture.delay=3;fixture.enum=1
        with fixture.patch():
            pending=await fixture.api.tax_level()
            self.assertIsInstance(pending,PendingOperation)
            result=await pending.resume(timeout=1)
        self.assertEqual(result["level"],"low")
        self.assertEqual(len(fixture.queries),1)
        self.assertEqual(fixture.queries[0].reads,4)
        self.assertFalse(fixture.ctx.pending_queries)

    async def option_fixture(self,fixture):
        fixture.api._controlled=AsyncMock(return_value=True)
        async def evaluate(expression,bindings,handles,capacity=4096):
            if "GetTaxLevelDesc" in expression:
                raw="原生档位说明".encode("utf-8")
                return NS(error=0,kind=data.Kind.STRING,snapshot_bytes=len(raw),total=len(raw),snapshot=raw)
            return NS(error=0)
        async def constraint(generated,handles,requested,*,submit=True):
            self.assertFalse(submit)
            return {"allowed":requested["level"]!="low","reason":"Native rejection" if requested["level"]=="low" else None}
        fixture.api._evaluate=AsyncMock(side_effect=evaluate)
        fixture.api._submit_generated=AsyncMock(side_effect=constraint)
        fixture.api._release=AsyncMock()

    async def test_options_current_is_independent_of_native_allowed_and_description(self):
        fixture=Fixture();fixture.enum=1
        await self.option_fixture(fixture)
        with fixture.patch():rows=await fixture.api.tax_options()
        self.assertEqual(len(rows),5)
        by_name={row["level"]:row for row in rows}
        self.assertEqual([name for name,row in by_name.items() if row["current"]],["low"])
        self.assertFalse(by_name["low"]["allowed"])
        self.assertTrue(by_name["medium"]["allowed"])
        self.assertFalse(by_name["very_low"]["current"])
        self.assertTrue(all(row["current_reason"] is None for row in rows))
        self.assertEqual(fixture.api._submit_generated.await_count,5)
        self.assertEqual(len(fixture.queries),1)

    async def test_unknown_current_preserves_existing_option_fields_and_constraints(self):
        fixture=Fixture();fixture.enum=5
        await self.option_fixture(fixture)
        with fixture.patch():rows=await fixture.api.tax_options()
        self.assertTrue(all(row["current"] is None for row in rows))
        self.assertTrue(all("fallback" in row["current_reason"] for row in rows))
        self.assertTrue(all(set(("level","description","allowed","reason"))<=set(row) for row in rows))
        self.assertFalse(next(row for row in rows if row["level"]=="low")["allowed"])
        self.assertTrue(next(row for row in rows if row["level"]=="medium")["allowed"])


if __name__=="__main__":unittest.main()
