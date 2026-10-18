"""Thin Session callback composition: native storage spans both completions."""
from __future__ import annotations
import struct
import unittest
from unittest.mock import Mock
from tests.native_sdk.test_v3_query import bind_fixture_wire

from pdx_runtime import native_call, v3_session, v3
from pdx_runtime.native_bridge import BridgeError

class MemoryProcess:
    def __init__(self):
        self.pid = 123
        self.memory = bytearray(2048)
        self.base = 0x20000
        self.state_address = 0x30000
        self.state = bytes(v3_session.CALLBACK_STATUS.size)
        self.freed = []
        self.closed = False
        self.writes = []
    def resolve_export(self, module, symbol, *, aliases=None):
        return {"PdxNativeCallInterface":0x10000,"PdxV3QueueSessionCallback":0x11000,
                "PdxV3SessionCallbackState":self.state_address}[symbol]
    def allocate(self, size):
        self.size = size
        return self.base
    def write(self, address, data):
        offset=address-self.base
        self.memory[offset:offset+len(data)]=data
        self.writes.append((address,data))
        return len(data)
    def read(self, address, size):
        if address==self.state_address: return self.state
        offset=address-self.base
        return bytes(self.memory[offset:offset+size])
    def free(self,address): self.freed.append(address)
    def close(self): self.closed=True

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.process=MemoryProcess()
        self.client=Mock(pid=123)
        bind_fixture_wire(self.client)
        self.client.queue_v3_interface_callback.return_value={"error": 0, "status":"ok","request_id":7}
        self.client.v3_world_status.return_value={"status":"ok","request_id":7,"completed_request_id":6}
    def submit(self):
        return v3_session.submit(self.client,0x44444,(1,2),process=self.process)
    def enqueue_done(self,error=0):
        self.client.v3_world_status.return_value={"status":"ok","request_id":7,"completed_request_id":7}
        self.process.write(self.process.base+4,struct.pack("<I",0))
        self.process.write(self.process.base+32,struct.pack("<Q",error))
        self.process.write(self.process.base+v3_session.SESSION_REQUEST_OFFSET,struct.pack("<Q",11))
    def session_done(self,phase=3,error=0):
        self.process.state=v3_session.CALLBACK_STATUS.pack(56,phase,error,18100,11,11,
            0x10000,self.process.base+v3_session.OPAQUE_OFFSET,0x50000)
        if not error:
            self.process.write(self.process.base+v3_session.OPAQUE_OFFSET+4,struct.pack("<I",0))
            self.process.write(self.process.base+v3_session.OPAQUE_OFFSET+32,struct.pack("<Q",18100))
    def test_actual_packing_and_two_completion_lifetime(self):
        query=self.submit()
        self.assertIs(query.client,self.client)
        outer=native_call.NativeCallResult.from_bytes(self.process.read(self.process.base,56))
        self.assertEqual(outer.target,0x11000)
        self.assertEqual(outer.argument_count,3)
        self.assertEqual(struct.unpack("<3Q",self.process.read(self.process.base+56,24)),
            (0x10000,query.opaque_address,query.session_request_address))
        inner=native_call.NativeCallResult.from_bytes(self.process.read(query.opaque_address,56))
        self.assertEqual(inner.arguments_address,query.opaque_address+56)
        self.assertIsNone(query.read()); self.assertFalse(self.process.freed)
        self.enqueue_done()
        self.process.state=v3_session.CALLBACK_STATUS.pack(56,1,0,0,11,10,0x10000,query.opaque_address,0)
        self.assertIsNone(query.read())
        self.assertEqual(query.request_id,7); self.assertEqual(query.session_request_id,11)
        self.assertFalse(self.process.freed)
        self.session_done()
        result=query.read()
        self.assertEqual(result.rax,18100)
        self.assertEqual(query.session_status.execution_thread_id,18100)
        self.assertEqual(self.process.freed,[self.process.base])
        self.assertIs(query.read(),result)
    def test_enqueue_rejection_releases_without_session_execution(self):
        query=self.submit(); self.enqueue_done(170)
        with self.assertRaises(BridgeError) as raised: query.read()
        self.assertEqual(raised.exception.winerror,170)
        self.assertEqual(self.process.freed,[self.process.base])
        self.assertIsNone(query.session_request_id)
    def test_rejected_enqueue_cleanup_can_retry_close_without_double_free(self):
        query=self.submit(); self.enqueue_done(170)
        query._owns_process=True
        close_calls=[]
        def close():
            close_calls.append(None)
            if len(close_calls)==1: raise OSError("close failed")
            self.process.closed=True
        self.process.close=close
        with self.assertRaises(BridgeError) as first: query.read()
        self.assertEqual(first.exception.winerror,170)
        self.assertIs(first.exception,query.submission_error)
        self.assertIs(first.exception.query,query)
        self.assertIsInstance(query.cleanup_error,OSError)
        self.assertEqual(self.process.freed,[self.process.base])
        with self.assertRaises(BridgeError) as raised: query.read()
        self.assertIs(raised.exception,first.exception)
        self.assertEqual(raised.exception.winerror,170)
        self.assertIsNone(query.cleanup_error)
        self.assertEqual(self.process.freed,[self.process.base])
        self.assertEqual(len(close_calls),2)
        self.assertTrue(self.process.closed)

    def test_remove_cancellation_is_error_not_target_result(self):
        query=self.submit(); self.enqueue_done(); self.session_done(4,1223)
        with self.assertRaises(v3.QueryExecutionError) as raised: query.read()
        self.assertEqual(raised.exception.winerror,1223)
        self.assertEqual(self.process.freed,[self.process.base])
    def test_unknown_delivery_retains_then_explicit_interface_recovery(self):
        self.client.queue_v3_interface_callback.side_effect=OSError("transport")
        with self.assertRaises(OSError) as raised: self.submit()
        query=raised.exception.query
        self.assertFalse(self.process.freed)
        with self.assertRaises(RuntimeError): query.read()
        query.recover(7)
        self.enqueue_done(); self.session_done()
        self.assertEqual(query.read().rax,18100)
    def test_lowlevel_enqueue_uses_shared_native_call_request(self):
        request=v3_session.enqueue(self.client,0xaaa,0xbbb,0xccc,process=self.process)
        self.assertEqual(request.target,0x11000)
        self.assertEqual(request.argument_count,3)
        self.assertEqual(struct.unpack("<3Q",self.process.read(self.process.base+56,24)),(0xaaa,0xbbb,0xccc))

if __name__=="__main__": unittest.main()
