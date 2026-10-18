import importlib.util
from pathlib import Path
import struct
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from pdx_runtime import native_call

from pdx_runtime.v3_game import _text as m

class Process:
 def __init__(self):self.blocks={};self.next=0x100000000;self.freed=[]
 def allocate(self,size):
  p=self.next;self.next+=((size+4095)//4096+1)*4096;self.blocks[p]=bytearray(size);return p
 def locate(self,p,n):
  for base,raw in self.blocks.items():
   if base<=p and p+n<=base+len(raw):return raw,p-base
  raise ValueError("invalid fake read")
 def read(self,p,n):
  raw,o=self.locate(p,n);return bytes(raw[o:o+n])
 def write(self,p,data):
  raw,o=self.locate(p,len(data));raw[o:o+len(data)]=data
 def free(self,p):del self.blocks[p];self.freed.append(p)
 def resolve_export(self,adapter,name,*,aliases=None):return {"PdxRuntimeRead":0x700,"PdxNativeCallBatchInterface":0x701,"VirtualAlloc":0x702,"ReadProcessMemory":0x703}[name]

class Fixture:
 def __init__(self,body=b"Chinese wood tooltip test",fail=False):
  self.p=Process();self.body=body;self.fail=fail;self.base=0x140000000;self.calls=[];self.dead=[];self.allocated_string=None
  self.second_body=None;self.formats=0;self.batch_counts=[];self.copy_allocations=[]
  self.alloc_fail=False;self.rpm_fail=False;self.rpm_success_rax=1;self.fail_full_at=None
  self.ctx=NS(process=self.p,image_base=self.base,native=object(),world=19,
   adapters={name:name for name in ("runtime","data","call","access")})
  async def query(label,factory):return factory()
  self.ctx.query=query
 def submit(self,client,callback,initial,**kw):
  self.address=self.p.allocate(len(initial));payload=kw["prepare_payload"](self.address);self.p.write(self.address,payload)
  count=native_call.BATCH_REQUEST.unpack_from(payload)[2];self.batch_counts.append(count)
  result=[];batcherror=0
  for i in range(count):
   if count==13 and self.fail_full_at==i:batcherror=574;break
   record=native_call.REQUEST.unpack(self.p.read(self.address+32+i*56,56));fn=record[2];n=record[3];ap=record[4]
   args=struct.unpack("<"+"Q"*n,self.p.read(ap,n*8));self.calls.append((fn,args))
   if fn==self.base+0x7bcf80:
    self.ctx_at=args[0];assert len(self.p.read(self.ctx_at,0x1a8))==0x1a8;rax=self.ctx_at
   elif fn==self.base+0x3a93160:
    assert args[0]==self.ctx_at
    ptr,size,null=struct.unpack("<QiB3x",self.p.read(args[1],16))
    self.name=self.p.read(ptr,size);self.obj=struct.unpack("<Q",self.p.read(args[2],8))[0]
    self.typeid=struct.unpack("<I",self.p.read(args[3],4))[0];rax=self.ctx_at+0x20
   elif fn==self.base+0x3a84710:
    assert args[2:]==(0,0,self.ctx_at,0)
    ptr,size,null=struct.unpack("<QiB3x",self.p.read(args[1],16));self.expr=self.p.read(ptr,size)
    if self.fail:batcherror=574;break
    self.formats+=1;self.string_at=args[0]
    body=self.second_body if self.formats>1 and self.second_body is not None else self.body
    if len(body)<=15:
     self.allocated_string=None;raw=body.ljust(16,b"\0")+struct.pack("<QQ",len(body),15)
    else:
     self.allocated_string=self.p.allocate(len(body)+1);self.p.write(self.allocated_string,body+b"\0")
     raw=struct.pack("<Q8xQQ",self.allocated_string,len(body),len(body))
    self.p.write(self.string_at,raw);rax=self.string_at
   elif fn==0x700:
    source,destination,size,received=args;size &=0xffffffff
    try:
     if not 0<size<=4096:raise ValueError("bounded")
     raw=self.p.read(source,size);self.p.write(destination,raw);self.p.write(received,struct.pack("<I",size));rax=0
    except ValueError:self.p.write(received,bytes(4));rax=87
   elif fn==0x702:
    assert args[0]==0 and args[2:]==(0x3000,4)
    rax=0 if self.alloc_fail or not args[1] else self.p.allocate(args[1])
    if rax:self.copy_allocations.append((rax,args[1]))
   elif fn==0x703:
    handle,source,destination,size,received=args;assert handle==0xffffffffffffffff
    try:
     if self.rpm_fail:raise ValueError("injected RPM failure")
     self.p.write(destination,self.p.read(source,size));self.p.write(received,struct.pack("<Q",size));rax=self.rpm_success_rax
    except ValueError:self.p.write(received,bytes(8));rax=0x100000000
   elif fn==self.base+0x61f810:
    assert args==(self.string_at,)
    if self.allocated_string:self.p.free(self.allocated_string)
    self.dead.append("CString");rax=0
   elif fn==self.base+0x7bd0b0:
    assert args==(self.ctx_at,);assert self.dead[-1]=="CString";self.dead.append("Context");rax=0
   else:raise AssertionError(hex(fn))
   # Actual CPP stores each return RAX before the next call loads its arguments.
   self.p.write(self.address+32+i*56+32,struct.pack("<Q",rax))
   result.append(NS(error=0,rax=rax))
  return NS(error=0,entered=1,left=1,callee_result=NS(error=batcherror,completed=len(result),calls=result))
 async def run(self):
  record={"values":[],"storage":{}}
  with patch.object(m.v3_access,"submit_callback",side_effect=self.submit):
   result=await m._format(self.ctx,record,0x5000000,143,"Building","Building.GetTooltip")
  return result,record

class Tests(unittest.IsolatedAsyncioTestCase):
 async def test_heap_exact_utf8_length_copied_before_destruction(self):
  f=Fixture(("木材\n🪵 "+chr(21)+"G+$").encode()*20);r,record=await f.run()
  self.assertEqual(r["text_bytes"],f.body);self.assertTrue(r["text_complete"])
  self.assertEqual(r["required_bytes"],len(f.body));self.assertIsNone(r["localization_success"])
  self.assertEqual(f.dead,["CString","Context"]);self.assertFalse(record["storage"])
  self.assertEqual(f.name,b"BUILDING");self.assertEqual(f.expr,b"[Building.GetTooltip]")
  self.assertEqual(f.obj,0x5000000);self.assertEqual(f.typeid,143)
 async def test_sso_uses_inline_not_invalid_pointer_probe(self):
  f=Fixture("短文".encode());r,record=await f.run()
  self.assertEqual(r["text"],"短文");self.assertTrue(r["text_complete"])
  self.assertEqual(r["receipts"][7]["native_return"],87)
 async def test_zero_string_is_real_empty_not_unavailable(self):
  f=Fixture(b"");r,record=await f.run();self.assertEqual(r["text"],"");self.assertTrue(r["text_complete"])
 async def test_dynamic_forwarding_uses_real_per_call_args(self):
  f=Fixture(b"x"*111);r,record=await f.run()
  read7=f.calls[7][1]
  self.assertEqual(read7[2],111);self.assertEqual(read7[0],f.allocated_string)
  self.assertEqual(len(f.calls),10);self.assertEqual(f.calls[2][1][2:4],(0,0))
 async def test_long_utf8_automatically_fresh_reads_and_copies_true_size_before_dtors(self):
  body=("木材工具提示\n🪵".encode())*300
  f=Fixture(body);r,record=await f.run()
  self.assertTrue(r["text_complete"]);self.assertEqual(r["text_bytes"],body)
  self.assertEqual(r["required_bytes"],len(body));self.assertEqual(f.batch_counts,[10,13])
  self.assertEqual(f.dead,["CString","Context","CString","Context"]);self.assertFalse(record["storage"])
  self.assertTrue(r["fresh_read_after_capacity"])
  self.assertEqual(f.copy_allocations[0][1],len(body));self.assertNotIn(f.copy_allocations[0][0],f.p.blocks)

 async def test_unknown_prefix_retains_storage_no_blind_dtor_retry(self):
  f=Fixture(fail=True);record={"values":[123],"storage":{}}
  with patch.object(m.v3_access,"submit_callback",side_effect=f.submit):
   with self.assertRaises(RuntimeError):await m._format(f.ctx,record,0x5000000,143,"Building","Building.GetTooltip")
  self.assertFalse(f.dead);self.assertTrue(record["storage"]);self.assertEqual(record["values"],[123])
  self.assertNotIn("native_temporaries_destroyed",record)


 async def test_building_full_id_source_is_held_through_native_destructors(self):
  f=Fixture("木材工具提示".encode()*15)
  async def refresh():pass
  async def typeid(name):self.assertEqual(name,"Building");return 143
  f.ctx.refresh=refresh;f.ctx.type_id=typeid
  result=NS(error=0,object=0x5000000,native_type_name="class CDataBuilding const *",value_handle=50)
  def evaluate(client,expr,bindings,**kw):
   self.assertEqual(expr,"Building.Self");self.assertEqual(len(bindings),1)
   self.assertEqual(bindings[0].object,300);return result
  def release(client,handle,**kw):
   self.assertEqual(handle,50);self.assertEqual(f.dead,["CString","Context"])
   return NS(error=0,value_handle=0)
  with patch.object(m.data,"evaluate",side_effect=evaluate),patch.object(m.data,"release",side_effect=release),patch.object(m.v3_access,"submit_callback",side_effect=f.submit):
   r=await m.read_building_tooltip(f.ctx,300)
  self.assertEqual(r["text_bytes"],f.body)
  self.assertEqual(f.ctx._localization_retained,[])
 async def test_queue_original_owned_then_retained_promotion_no_flag_flip(self):
  f=Fixture("施工队列".encode()*20);released=[]
  async def refresh():pass
  async def country():return 156
  async def typeid(name):return {"Country":5,"ConstructionQueueElement":213}[name]
  f.ctx.refresh=refresh;f.ctx.country_id=country;f.ctx.type_id=typeid
  def evaluate(client,expr,bindings,**kw):
   if expr=="Country.AccessGovernmentConstructionQueue":return NS(error=0,value_handle=70)
   self.assertEqual(expr,"ConstructionQueueElement.Self");self.assertTrue(bindings[0].retained)
   self.assertEqual(bindings[0].object,71)
   return NS(error=0,object=0x5000000,value_handle=72,native_type_name="CDataConstructionQueueElement const *")
  def release(client,handle,**kw):
   self.assertEqual(f.dead,["CString","Context"]);released.append(handle)
   return NS(error=0,value_handle=0)
  element=NS(error=0,value_handle=71,native_type_name="class?$CPdxPtr@VCDataConstructionQueueElement@@")
  with patch.object(m.data,"evaluate",side_effect=evaluate),patch.object(m.data,"release",side_effect=release),patch.object(m.data,"list_count",return_value=NS(error=0,total=1)),patch.object(m.data,"list_element",return_value=element),patch.object(m.v3_access,"submit_callback",side_effect=f.submit):
   r=await m.read_queue_tooltip(f.ctx,0)
  self.assertEqual(f.expr,b"[ConstructionQueueElement.GetTooltip]")
  self.assertEqual(f.typeid,213);self.assertEqual(released,[72,71,70]);self.assertTrue(r["text_complete"])


 async def test_friendly_text_preserves_native_rich_text(self):
  f=Fixture((chr(21)+"G木材工具提示"+chr(21)+"!\n预算 "+"#!").encode());r,record=await f.run()
  self.assertEqual(r["raw_text"],f.body.decode())
  self.assertEqual(r["text"],"木材工具提示\n预算");self.assertTrue(r["context_supplied"])
 async def test_generic_retained_reader_keeps_caller_handle_and_cleans_new_dependency(self):
  f=Fixture(b"native contextual explanation");released=[]
  async def refresh():pass
  async def typeid(name):self.assertEqual(name,"Law");return 55
  f.ctx.refresh=refresh;f.ctx.type_id=typeid
  def evaluate(client,expression,bindings,**kw):
   self.assertEqual(expression,"Law.Self");self.assertEqual(bindings[0].object,90)
   self.assertTrue(bindings[0].retained)
   return NS(error=0,object=0x5000000,value_handle=91)
  def release(client,handle,**kw):released.append(handle);return NS(error=0,value_handle=0)
  with patch.object(m.data,"evaluate",side_effect=evaluate),patch.object(m.data,"release",side_effect=release),patch.object(m.v3_access,"submit_callback",side_effect=f.submit):
   r=await m.read_retained_text(f.ctx,"Law",90,"Law.CannotEnactDesc")
  self.assertEqual(released,[91]);self.assertEqual(f.expr,b"[Law.CannotEnactDesc]")
  self.assertEqual(r["text"],f.body.decode());self.assertEqual(f.ctx._localization_retained,[])


 async def test_long_alloc_zero_returns_incomplete_and_cleans_native_objects(self):
  f=Fixture(b"x"*9001);f.alloc_fail=True;r,record=await f.run()
  self.assertFalse(r["text_complete"]);self.assertIsNone(r["text_bytes"])
  self.assertEqual(f.dead,["CString","Context","CString","Context"]);self.assertFalse(record["storage"])
 async def test_long_rpm_false_uses_low_bool32_not_nonzero_rax64(self):
  f=Fixture(b"x"*9001);f.rpm_fail=True;r,record=await f.run()
  self.assertFalse(r["text_complete"]);self.assertIsNone(r["text_bytes"])
  self.assertEqual(r["receipts"][10]["native_return"],0x100000000)
  self.assertFalse(record["storage"])
 async def test_long_rpm_true_decodes_low_bool32(self):
  f=Fixture(b"x"*9001);f.rpm_success_rax=0xdead00000001;r,record=await f.run()
  self.assertTrue(r["text_complete"]);self.assertEqual(r["text_bytes"],f.body)
 async def test_long_fresh_observation_can_change_length_to_another_heap_value(self):
  f=Fixture(b"x"*9001);f.second_body=("新说明".encode())*1000;r,record=await f.run()
  self.assertEqual(r["text_bytes"],f.second_body);self.assertEqual(r["required_bytes"],len(f.second_body))
  self.assertEqual(f.copy_allocations[0][1],len(f.second_body))
 async def test_long_fresh_observation_can_shrink_to_sso(self):
  f=Fixture(b"x"*9001);f.second_body="短文".encode();r,record=await f.run()
  self.assertTrue(r["text_complete"]);self.assertEqual(r["text"],"短文");self.assertFalse(record["storage"])
 async def test_long_unknown_after_allocation_keeps_exact_prefix_and_copy_buffer(self):
  f=Fixture(b"x"*9001);f.fail_full_at=10;record={"values":[99],"storage":{}}
  with patch.object(m.v3_access,"submit_callback",side_effect=f.submit):
   with self.assertRaises(RuntimeError):await m._format(f.ctx,record,0x5000000,143,"Building","Building.GetTooltip")
  self.assertEqual(f.batch_counts,[10,13]);self.assertEqual(f.dead,["CString","Context"])
  self.assertEqual(len(record["storage"]),2);self.assertEqual(record["values"],[99])
  self.assertIn(f.copy_allocations[-1][0],record["storage"])
  self.assertNotIn("native_temporaries_destroyed",record)


 async def test_building_text_generic_expression_uses_same_full_id_owner(self):
  f=Fixture("不雇佣原因".encode()*8)
  async def refresh():pass
  async def typeid(name):self.assertEqual(name,"Building");return 143
  f.ctx.refresh=refresh;f.ctx.type_id=typeid
  def evaluate(client,expression,bindings,**kw):
   self.assertEqual(expression,"Building.Self");self.assertEqual(bindings[0].object,300)
   self.assertFalse(bindings[0].retained)
   return NS(error=0,object=0x5000000,value_handle=81,native_type_name="CDataBuilding const *")
  def release(client,handle,**kw):
   self.assertEqual(handle,81);self.assertEqual(f.dead,["CString","Context"])
   return NS(error=0,value_handle=0)
  with patch.object(m.data,"evaluate",side_effect=evaluate),patch.object(m.data,"release",side_effect=release),patch.object(m.v3_access,"submit_callback",side_effect=f.submit):
   result=await m.read_building_text(f.ctx,300,"Building.GetNotHiringReasonDesc")
  self.assertEqual(f.expr,b"[Building.GetNotHiringReasonDesc]")
  self.assertEqual(result["text"],f.body.decode());self.assertEqual(f.ctx._localization_retained,[])


 async def _read_building_with_fault(self,f,*,post_batch=None,free_fault=False,pending_fallback=False):
  released=[];free_calls=[];read=f.p.read;free=f.p.free;query=f.ctx.query
  async def refresh():pass
  async def typeid(name):return 143
  f.ctx.refresh=refresh;f.ctx.type_id=typeid
  def read_after(p,n):
   if (post_batch is not None and f.batch_counts and f.batch_counts[-1]==post_batch
       and len(f.dead)==(2 if post_batch==10 else 4) and p==f.ctx_at and n>0x280):
    raise OSError("post-settlement copy failed")
   return read(p,n)
  def free_after(p):
   if free_fault and p==getattr(f,"ctx_at",None) and len(f.dead)==2:
    free_calls.append(p);raise OSError("caller storage release failed")
   return free(p)
  async def uncertain(label,factory):
   if pending_fallback and label=="localizer.original_complete_text":
    raise TimeoutError("new fallback original query pending")
   return await query(label,factory)
  f.p.read=read_after;f.p.free=free_after;f.ctx.query=uncertain
  def evaluate(*args,**kwargs):return NS(error=0,object=0x5000000,native_type_name="CDataBuilding const *",value_handle=81)
  def release(client,handle,**kwargs):released.append(handle);return NS(error=0,value_handle=0)
  with patch.object(m.data,"evaluate",side_effect=evaluate),patch.object(m.data,"release",side_effect=release),patch.object(m.v3_access,"submit_callback",side_effect=f.submit):
   with self.assertRaises((OSError,RuntimeError,TimeoutError)) as caught:
    await m.read_building_text(f.ctx,300,"Building.GetNotHiringReasonDesc")
  return caught.exception,released,free_calls

 async def test_completed_ten_calls_then_local_read_error_cleans_storage_and_self(self):
  f=Fixture(b"short native text")
  error,released,_=await self._read_building_with_fault(f,post_batch=10)
  record=error._localizer_record
  self.assertTrue(m.settled_error(error));self.assertEqual(record["native_phase"]["calls"],10)
  self.assertEqual(f.batch_counts,[10]);self.assertEqual(f.formats,1);self.assertEqual(f.dead,["CString","Context"])
  self.assertEqual(released,[81]);self.assertFalse(record["values"]);self.assertFalse(record["storage"])
  self.assertEqual(f.ctx._localization_retained,[]);self.assertNotIn(f.ctx_at,f.p.blocks)

 async def test_completed_thirteen_calls_then_local_read_error_cleans_both_allocations(self):
  f=Fixture(b"x"*9001)
  error,released,_=await self._read_building_with_fault(f,post_batch=13)
  record=error._localizer_record
  self.assertTrue(m.settled_error(error));self.assertEqual(record["native_phase"]["calls"],13)
  self.assertEqual(f.batch_counts,[10,13]);self.assertEqual(f.formats,2)
  self.assertEqual(f.dead,["CString","Context","CString","Context"])
  self.assertEqual(released,[81]);self.assertFalse(record["storage"]);self.assertFalse(record["values"])
  self.assertNotIn(f.copy_allocations[0][0],f.p.blocks);self.assertEqual(f.ctx._localization_retained,[])

 async def test_completed_free_error_never_retries_free_and_releases_self(self):
  f=Fixture(b"short native text")
  error,released,attempts=await self._read_building_with_fault(f,free_fault=True)
  record=error._localizer_record
  self.assertTrue(m.settled_error(error));self.assertEqual(attempts,[f.ctx_at]);self.assertEqual(released,[81])
  self.assertFalse(record["values"]);self.assertEqual(list(record["storage"]),[f.ctx_at])
  self.assertEqual(f.ctx._localization_retained,[record]);self.assertEqual(f.formats,1)

 async def test_new_fallback_pending_cannot_reuse_previous_completed_phase(self):
  f=Fixture(b"x"*9001)
  error,released,_=await self._read_building_with_fault(f,pending_fallback=True)
  record=error._localizer_record
  self.assertFalse(m.settled_error(error));self.assertEqual(record["native_phase"]["calls"],13)
  self.assertFalse(record["native_phase"]["settled"]);self.assertNotIn("native_temporaries_destroyed",record)
  self.assertEqual(f.batch_counts,[10]);self.assertEqual(f.formats,1)
  self.assertEqual(f.dead,["CString","Context"]);self.assertEqual(released,[])
  self.assertEqual(record["values"],[81]);self.assertTrue(record["storage"])

 async def test_partial_fallback_retains_self_and_exact_new_storage(self):
  f=Fixture(b"x"*9001);f.fail_full_at=10
  error,released,_=await self._read_building_with_fault(f)
  record=error._localizer_record
  self.assertFalse(m.settled_error(error));self.assertEqual(f.batch_counts,[10,13])
  self.assertEqual(f.dead,["CString","Context"]);self.assertEqual(released,[])
  self.assertEqual(record["values"],[81]);self.assertEqual(len(record["storage"]),2)

 async def test_fallback_can_reuse_freed_scratch_address_with_new_free_attempt(self):
  f=Fixture(b"x"*9001);original=f.p.allocate;reused=[]
  def allocate(size):
   address=getattr(f,"ctx_at",None)
   if address is not None and address not in f.p.blocks and size<1000:
    f.p.blocks[address]=bytearray(size);reused.append(address);return address
   return original(size)
  f.p.allocate=allocate
  result,record=await f.run()
  self.assertTrue(result["text_complete"]);self.assertTrue(reused);self.assertFalse(record["storage"])
  self.assertEqual(f.p.freed.count(reused[0]),2)

if __name__=="__main__":unittest.main(verbosity=2)
