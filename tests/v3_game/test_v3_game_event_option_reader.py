"""CPU event-option reader behavior. No game/native I/O or actions."""
from dataclasses import dataclass
from enum import IntEnum
from types import SimpleNamespace as N
import unittest

from pdx_runtime.v3_game.events import _read as reader
class Kind(IntEnum):
    RAW=0;EMPTY=1;BOOL=2;I32=3;STRING=10
@dataclass
class Source:
    root_expression:str;byte_offset:int;type_id:int;interface_thread_id:int
    index:int=0;expected_count:int|None=None;guard_expression:str="";guard_input:object=None
class Input:
    @staticmethod
    def string(text):return N(text=text)
class Receipt:
    def __init__(self,kind=Kind.STRING,raw=b"",*,error=0,bits=0,guarded=False,total=None):
        self.error=error;self.source_count=2;self.guard_checked=int(guarded);self.guard_passed=int(guarded)
        self.result=N(kind=kind,snapshot=raw,snapshot_bytes=len(raw),total=len(raw) if total is None else total,
            native_type_name="CString" if kind==Kind.STRING else "native",raw_bits=bits,
            factory=0,object=0,value_handle=0,any_type=0,any_vtable=0,payload_address=0,raw_any=bytes(40))
    @property
    def value(self):
        return self.result.snapshot.decode("utf-8",errors="replace") if self.result.kind==Kind.STRING else bool(self.result.raw_bits)
class Query:
    def __init__(self,number,expression,source):
        self.request_id=number;self.expression=expression;self.source=source
        self._result=None;self._freed=False;self._released=False
class Data:
    Kind=Kind;PointerArraySource=Source;TypedInput=Input
    def __init__(self):
        self.calls=[];self.replies={};self.submit_error=None;self.submit_unknown=None
        self.options=[("原选项甲",True,True),("原选项乙",False,False)]
        self.tag="opaque-original-tag";self.unknown_query=None
    def submit(self,expression,source):
        q=Query(len(self.calls)+1,expression,source);self.calls.append(q)
        if expression==self.submit_unknown:
            error=RuntimeError("unknown submit");error.query=q;self.unknown_query=q;raise error
        if expression==self.submit_error:raise ValueError("known prequeue validation failure")
        return q
    def evaluate_pointer_array(self,native,source,expression,**kw):
        assert kw["world"]==77 and source.index==0 and source.expected_count==1
        return self.submit(expression,source)
    def evaluate_element(self,native,source_expression,index,expression,**kw):
        assert source_expression=="EventWindow.GetOptions"
        if "expected_count" in kw:assert kw["expected_count"]==len(self.options)
        q=self.submit(expression,kw["pointer_array"]);q.index=index;return q
    def invoke_element_action(self,*args,**kw):raise AssertionError("reader must never invoke an action")
    def response(self,q):
        exp=q.expression;guarded=bool(q.source.guard_expression)
        if guarded:
            assert q.source.guard_expression=="EventWindow.GetEvent.GetTooltipTag"
            assert q.source.guard_input.text==self.tag
        if exp in self.replies:
            r=self.replies[exp];r.guard_checked=r.guard_passed=int(guarded);return r
        strings={"EventWindow.GetEvent.GetKey":"natural.player.event", "EventWindow.GetEvent.GetTooltipTag":self.tag,
            "EventWindow.GetTitle":"原事件标题", "EventWindow.GetDescription":"原正文\n第二行 §Y原标记§!",
            "EventWindow.GetFlavor":"原风味文本", "EventWindow.GetTitleTooltip":"原标题提示"}
        if exp in strings:return Receipt(raw=strings[exp].encode(),guarded=guarded)
        if exp=="EventOption.GetText":return Receipt(raw=self.options[q.index][0].encode(),guarded=guarded)
        if exp=="EventOption.IsEnabled":return Receipt(Kind.BOOL,bits=int(self.options[q.index][1]),guarded=guarded)
        if exp=="EventOption.IsDefaultOption":return Receipt(Kind.BOOL,bits=int(self.options[q.index][2]),guarded=guarded)
        if exp=="EventOption.GetDesc":return Receipt(raw=("原说明"+str(q.index)).encode(),guarded=guarded)
        if exp=="EventOption.IsHighlightedOption":return Receipt(Kind.BOOL,bits=int(q.index==0),guarded=guarded)
        raise AssertionError(exp)
class Fixture:
    def __init__(self):self.data=Data();self.record={};self.wait_unknown=None;self.pending=None
    async def wait(self,label,q):
        if q.expression==self.wait_unknown:
            self.pending=q;raise TimeoutError("unknown completion; retain original query")
        result=self.data.response(q);q._result=result;q._freed=q._released=True;return result
    async def run(self):
        return await reader.read_pending_options(self.data,object(),object(),123,self.wait,
            77,8,498,0,1,self.record)
class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_original_text_and_bool_no_actions(self):
        f=Fixture();record=await f.run()
        self.assertEqual(record["window_fields"]["title"].result.snapshot,"原事件标题".encode())
        self.assertEqual(record["window_fields"]["description"].result.snapshot,"原正文\n第二行 §Y原标记§!".encode())
        self.assertEqual([r["desc"].value for r in record["options"]],["原说明0","原说明1"])
        self.assertEqual([r["highlighted"].value for r in record["options"]],[True,False])
        self.assertTrue(record["optional_identity_verified"])
        self.assertTrue(all(d["ok"] for d in record["field_diagnostics"].values()))
        self.assertFalse(record["atomic_snapshot"])
        self.assertTrue(all(q._freed for q in f.data.calls))
        self.assertEqual(len(record["optional_reads"]),9)
        self.assertTrue(all(r["query"].source.guard_expression for r in record["optional_reads"]))
    async def test_optional_native_error_preserves_options_and_does_not_fake_desc(self):
        f=Fixture();f.data.replies["EventWindow.GetTitle"]=Receipt(Kind.RAW,error=50)
        f.data.replies["EventOption.GetDesc"]=Receipt(Kind.RAW,error=50)
        record=await f.run()
        self.assertEqual(len(record["options"]),2)
        self.assertEqual(record["options"][0]["text"].value,"原选项甲")
        self.assertEqual(record["options"][0]["desc"].error,50)
        self.assertEqual(record["options"][0]["desc"].result.snapshot,b"")
        self.assertEqual(record["field_diagnostics"]["window.title"]["status"],"native_error")
        self.assertEqual(record["field_diagnostics"]["option.0.desc"]["native_error"],50)
        self.assertTrue(record["field_diagnostics"]["window.description"]["ok"])
        self.assertTrue(record["field_diagnostics"]["option.1.highlighted"]["ok"])
        self.assertTrue(record["optional_identity_verified"])
    async def test_optional_wrong_kind_incomplete_and_raw_bytes(self):
        f=Fixture();f.data.replies["EventWindow.GetTitle"]=Receipt(Kind.I32,bits=9)
        f.data.replies["EventWindow.GetFlavor"]=Receipt(raw=b"cut",total=8)
        f.data.replies["EventWindow.GetDescription"]=Receipt(raw=b"\xff\x00original")
        record=await f.run();diags=record["field_diagnostics"]
        self.assertEqual(diags["window.title"]["status"],"unexpected_native_kind")
        self.assertEqual(diags["window.flavor"]["status"],"incomplete_native_bytes")
        self.assertEqual(diags["window.description"]["status"],"ok_raw_bytes")
        self.assertEqual(record["window_fields"]["description"].result.snapshot,b"\xff\x00original")
        self.assertEqual(len(record["options"]),2)
    async def test_optional_known_prequeue_error_is_per_field(self):
        f=Fixture();f.data.submit_error="EventWindow.GetTitle";record=await f.run()
        self.assertIsNone(record["window_fields"]["title"])
        self.assertEqual(record["field_diagnostics"]["window.title"]["status"],"submit_failed")
        self.assertTrue(record["field_diagnostics"]["window.description"]["ok"])
        self.assertNotIn("unknown_query",record)
    async def test_optional_unknown_wait_preserves_exact_query_and_prevents_reentry(self):
        f=Fixture();f.wait_unknown="EventWindow.GetTitle"
        with self.assertRaises(TimeoutError):await f.run()
        self.assertIs(f.record["pending"],f.pending)
        self.assertIs(f.record["unknown_query"]["query"],f.pending)
        before=len(f.data.calls)
        with self.assertRaises(RuntimeError):await f.run()
        self.assertEqual(len(f.data.calls),before)
        self.assertFalse(f.pending._freed)
        self.assertFalse(any(q.expression=="EventWindow.GetDescription" for q in f.data.calls))
    async def test_optional_unknown_submit_preserves_exact_query(self):
        f=Fixture();f.data.submit_unknown="EventOption.GetDesc"
        with self.assertRaises(RuntimeError):await f.run()
        self.assertIs(f.record["pending"],f.data.unknown_query)
        self.assertIs(f.record["unknown_query"]["query"],f.data.unknown_query)
        self.assertEqual(len(f.record["options"]),2)
    async def test_core_unknown_is_not_replayed(self):
        f=Fixture();f.wait_unknown="EventWindow.GetEvent.GetKey"
        with self.assertRaises(TimeoutError):await f.run()
        before=len(f.data.calls)
        with self.assertRaises(RuntimeError):await f.run()
        self.assertEqual(len(f.data.calls),before)
        self.assertIs(f.record["pending"],f.pending)
    async def test_optional_final_identity_error_keeps_original_options(self):
        f=Fixture();original=f.wait
        async def wait(label,q):
            if label=="events.optional.tag.after-fields":
                q._result=Receipt(Kind.RAW,error=1237,guarded=True);q._result.guard_passed=0
                q._freed=q._released=True;return q._result
            return await original(label,q)
        f.wait=wait;record=await f.run()
        self.assertTrue(record["core_identity_verified"])
        self.assertFalse(record["optional_identity_verified"])
        self.assertEqual(len(record["options"]),2)
        self.assertEqual(record["field_diagnostics"]["identity.after_optional"]["native_error"],1237)
if __name__=="__main__":unittest.main(verbosity=2)
