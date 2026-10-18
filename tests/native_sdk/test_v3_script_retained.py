"""Explicit retained native-owner composition; no native game inference."""
import struct
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from pdx_runtime import v3_script as script

class Memory:
    def __init__(self):
        self.next=0x20000;self.buffers={};self.freed=[];self.closed=0
    def allocate(self,size):
        address=self.next;self.next+=0x1000
        self.buffers[address]=bytearray(size)
        return address
    def locate(self,address,size):
        for base,data in self.buffers.items():
            if base<=address and address+size<=base+len(data):return data,address-base
        raise OSError('unmapped')
    def write(self,address,data):
        buffer,off=self.locate(address,len(data));buffer[off:off+len(data)]=data
    def read(self,address,size):
        buffer,off=self.locate(address,size);return bytes(buffer[off:off+size])
    def free(self,address):
        self.freed.append(address);del self.buffers[address]
    def close(self):self.closed+=1
    def resolve_export(self,adapter,symbol,*,aliases=None):return symbol

class Query:
    def __init__(self,args):
        self.args=args;self.result=None
    def read(self):return self.result
    def complete(self,rax=0,error=0):
        self.result=SimpleNamespace(error=error,callee_result=SimpleNamespace(error=0,rax=rax))

class RetainedTests(unittest.TestCase):
    def setUp(self):
        self.process=Memory();self.client=Mock(pid=123);self.queries=[]
        def submit(client,target,args,**kwargs):
            self.assertEqual(kwargs['mode'],1);self.assertEqual(kwargs['flag'],0)
            self.assertEqual(kwargs['dispatch'],'interface');self.assertIs(kwargs['process'],self.process)
            q=Query((target,tuple(args)));self.queries.append(q);return q
        self.patch=patch.object(script.v3_access,'submit_call',side_effect=submit);self.patch.start()
    def tearDown(self):self.patch.stop()
    def context(self):
        op=script.create_context(self.client,script.Scope.raw(5,1),process=self.process)
        self.assertEqual(op.query.args,('PdxV3ScriptContextRoot',(op.owner.address+320,op.owner.address)))
        op.query.complete();self.assertIs(op.read().value,op.owner)
        return op.owner
    def parsed(self,kind=script.ScriptKind.VALUE,error=0):
        op=script.parse(self.client,'gdp',script.Scope.raw(5,1),kind=kind,process=self.process)
        self.process.write(op.owner.address,script.OWNER.pack(544,error,kind,5,
            0x90000 if not error else 0,0,0,bytes(512)))
        op.query.complete(error);op.read();return op.owner
    def test_create_native_once_pending_and_explicit_release(self):
        op=script.create_context(self.client,script.Scope.raw(5,1),process=self.process)
        ctx=op.owner
        self.assertIsNone(op.read());self.assertFalse(self.process.freed)
        with self.assertRaises(RuntimeError):ctx.release()
        op.query.complete();self.assertIs(op.read().value,ctx)
        self.assertTrue(ctx.constructed)
        drop=ctx.release();self.assertIsNone(drop.read());self.assertFalse(self.process.freed)
        drop.query.complete();drop.read()
        self.assertTrue(ctx.released);self.assertEqual(self.process.freed,[ctx.address])
        self.assertEqual(len(self.queries),2)
    def test_parse_once_two_evaluations_both_borrowers_block_release(self):
        ctx=self.context();parsed=self.parsed()
        first=parsed.evaluate(ctx);self.assertIsNone(first.read())
        with self.assertRaises(RuntimeError):parsed.release()
        with self.assertRaises(RuntimeError):ctx.release()
        out=first.query.args[1][-1];self.process.write(out,struct.pack('<Q',2485661593480))
        first.query.complete();result=first.read()
        self.assertEqual(result.value.bits,2485661593480)
        second=parsed.evaluate(ctx)
        out2=second.query.args[1][-1];self.process.write(out2,struct.pack('<Q',2485661593480))
        second.query.complete();self.assertEqual(second.read().value.bits,2485661593480)
        self.assertEqual([q.args[0] for q in self.queries].count('PdxV3ScriptParse'),1)
        drop=parsed.release()
        self.process.write(parsed.address,script.OWNER.pack(544,0,3,5,0,0,0,bytes(512)))
        drop.query.complete();drop.read()
        self.assertTrue(parsed.released);self.assertIn(parsed.address,self.process.freed)
        self.assertFalse(ctx.released)
    def test_retained_trigger_uses_public_u32_evaluator_without_reparse(self):
        ctx=self.context();parsed=self.parsed(kind=script.ScriptKind.TRIGGER)
        for raw in (1,0):
            call=parsed.evaluate(ctx)
            self.assertEqual(call.query.args[0],'PdxV3ScriptEvaluate')
            out=call.query.args[1][2];self.process.write(out,struct.pack('<I',raw))
            call.query.complete();result=call.read()
            self.assertEqual(result.value.raw_boolean,raw)
            self.assertEqual(result.value.boolean,bool(raw))
        self.assertEqual([q.args[0] for q in self.queries].count('PdxV3ScriptParse'),1)
        self.assertFalse(parsed.released)

    def test_completed_release_error_keeps_native_owner_wire(self):
        parsed=self.parsed();drop=parsed.release();drop.query.complete(170)
        result=drop.read()
        self.assertEqual(result.error,170);self.assertFalse(parsed.released)
        self.assertIn(parsed.address,self.process.buffers);self.assertNotIn(parsed.address,self.process.freed)
        self.assertIsNone(parsed.pending)
    def test_semantic_parse_failure_is_empty_owner_not_good_zero(self):
        parsed=self.parsed(error=11)
        self.assertEqual(parsed.info.error,11)
        ctx=self.context()
        with self.assertRaises(RuntimeError):parsed.evaluate(ctx)
        drop=parsed.release();drop.query.complete();drop.read()
        self.assertTrue(parsed.released)
    def test_unknown_delivery_retains_context_and_recoverable_operation(self):
        q=Query(None)
        def unknown(*args,**kwargs):
            err=OSError('transport');err.query=q;raise err
        with patch.object(script.v3_access,'submit_call',side_effect=unknown):
            with self.assertRaises(OSError) as caught:
                script.create_context(self.client,script.Scope.raw(5,1),process=self.process)
        op=caught.exception.native_operation;ctx=op.owner
        self.assertIs(ctx.pending,op);self.assertFalse(self.process.freed)
        with self.assertRaises(RuntimeError):ctx.release()
        q.complete();self.assertIs(op.read().value,ctx)
    def test_named_mutation_uses_native_saved_api_and_temp_scope_lifetime(self):
        ctx=self.context();saved=script.SavedScope(14410,script.Scope.raw(5,1))
        op=ctx.set_saved(saved);symbol,args=op.query.args
        self.assertEqual(symbol,'PdxV3ScriptContextSetSaved');self.assertEqual(args[:2],(ctx.address,14410))
        self.assertEqual(self.process.read(args[2],16),saved.value.to_bytes())
        self.assertIsNone(op.read());self.assertIn(args[2],self.process.buffers)
        op.query.complete();op.read();self.assertNotIn(args[2],self.process.buffers)
        self.assertTrue(ctx.constructed)
    def test_intern_owns_input_until_completed_and_copies_native_id(self):
        op=script.intern_name(self.client,'pdx_probe_actor',process=self.process)
        symbol,args=op.query.args
        self.assertEqual(symbol,'PdxV3ScriptInternScopeName')
        self.assertIsNone(op.read());self.assertIn(args[0],self.process.buffers)
        self.process.write(args[2],struct.pack('<I',14410));op.query.complete()
        self.assertEqual(op.read().value,14410);self.assertNotIn(args[0],self.process.buffers)
    def test_native_parse_exception_after_owner_write_retains_owner_and_input(self):
        op=script.parse(self.client,'gdp',script.Scope.raw(5,1),process=self.process)
        owner=op.owner
        self.process.write(owner.address,script.OWNER.pack(544,0,3,5,0x90000,0,0,bytes(512)))
        op.query.complete(error=574)
        with self.assertRaises(script.BridgeError) as caught:op.read()
        self.assertIs(caught.exception.native_operation,op)
        self.assertIs(owner.pending,op)
        self.assertIn(owner.address,self.process.buffers)
        self.assertNotIn(owner.address,self.process.freed)
        with self.assertRaises(RuntimeError):owner.release()
        self.assertEqual([q.args[0] for q in self.queries].count('PdxV3ScriptParse'),1)

    def test_release_output_read_failure_retries_locally_without_native_call(self):
        owner=self.parsed();drop=owner.release()
        self.process.write(owner.address,script.OWNER.pack(544,0,3,5,0,0,0,bytes(512)))
        drop.query.complete()
        original_read=self.process.read;attempts=[]
        def fail_once(address,size):
            attempts.append(None)
            if len(attempts)==1:raise OSError('readback')
            return original_read(address,size)
        self.process.read=fail_once
        with self.assertRaises(OSError):drop.read()
        self.assertIs(owner.pending,drop);self.assertFalse(owner.released)
        drop.read()
        self.assertTrue(owner.released)
        self.assertEqual([q.args[0] for q in self.queries].count('PdxV3ScriptRelease'),1)
        self.assertEqual(self.process.freed,[owner.address])

    def test_release_storage_free_failure_retries_without_second_destructor(self):
        ctx=self.context();drop=ctx.release();drop.query.complete()
        original_free=self.process.free;attempts=[]
        def fail_once(address):
            attempts.append(address)
            if len(attempts)==1:raise OSError('free')
            original_free(address)
        self.process.free=fail_once
        with self.assertRaises(OSError):drop.read()
        self.assertTrue(ctx.released);self.assertIsNone(ctx.pending)
        result=drop.read()
        self.assertEqual(result.error,0)
        self.assertEqual([q.args[0] for q in self.queries].count('PdxV3ScriptContextRelease'),1)
        self.assertEqual(self.process.freed,[ctx.address])
        self.assertEqual(attempts,[ctx.address,ctx.address])

    def test_release_prequeue_failure_never_frees_live_native_owners(self):
        ctx=self.context();parsed=self.parsed()
        def fail(*args,**kwargs):raise ValueError('beforequeue')
        with patch.object(script.v3_access,'submit_call',side_effect=fail):
            with self.assertRaises(ValueError):ctx.release()
            with self.assertRaises(ValueError):parsed.release()
        self.assertIn(ctx.address,self.process.buffers)
        self.assertIn(parsed.address,self.process.buffers)
        self.assertFalse(ctx.released);self.assertFalse(parsed.released)
        self.assertIsNone(ctx.pending);self.assertIsNone(parsed.pending)

    def test_prequeue_failure_cleans_fresh_owner_and_process_once(self):
        def fail(*args,**kwargs):raise ValueError('beforequeue')
        with patch.object(script.v3_access,'submit_call',side_effect=fail):
            with self.assertRaises(ValueError):
                script.create_context(self.client,script.Scope.raw(5,1),process=self.process)
        self.assertEqual(len(self.process.freed),1);self.assertEqual(self.process.closed,0)

if __name__=='__main__':unittest.main()
