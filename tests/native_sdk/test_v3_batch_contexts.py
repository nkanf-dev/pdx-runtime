"""Focused batch composition; reuse the existing Session test memory fixture, no native execution."""
from __future__ import annotations

import struct
import unittest
from unittest.mock import Mock, patch
from tests.native_sdk.test_v3_query import bind_fixture_wire

from pdx_runtime import native_call as native, v3_access as access, v3_session as session
from pdx_runtime.native_bridge import BridgeError
from tests.core.test_v3_session import MemoryProcess


class BatchMemoryProcess(MemoryProcess):
    def resolve_export(self, module, symbol, *, aliases=None):
        return {"PdxNativeCallInterface": 0x10000,
                "PdxNativeCallBatchInterface": 0x10040,
                "PdxV3AccessCallback": 0x10080,
                "PdxV3QueueSessionCallback": 0x11000,
                "PdxV3SessionCallbackState": self.state_address}[symbol]


def completed_batch(calls, address, *, error=0, completed=None):
    output = bytearray(native.encode_batch(calls, request_address=address))
    completed = len(calls) if completed is None else completed
    native.BATCH_REQUEST.pack_into(output, 0, 32, error, len(calls), address + 32, completed)
    for index in range(completed):
        offset = 32 + index * 56
        struct.pack_into("<I", output, offset + 4, error if index == completed - 1 else 0)
        struct.pack_into("<Q", output, offset + 32, 0xFEDCBA9876543210 + index)
        output[offset + 40:offset + 56] = struct.pack("<QQ", native.float64_bits(-2.5), index)
    return bytes(output)


class V3BatchContextTests(unittest.TestCase):
    def setUp(self):
        self.process = BatchMemoryProcess()
        self.client = Mock(pid=123)
        bind_fixture_wire(self.client)
        self.client.queue_v3_interface_callback.return_value = {"error": 0, "status": "ok", "request_id": 7}
        self.client.v3_world_status.return_value = {"status": "ok", "completed_request_id": 6}

    def test_access_interface_is_one_scope_and_preserves_callee_batch_error_separately(self):
        calls = ((0x10, (1, 2)), (0, ()), (0x30, (3,)))
        query = access.submit_batch(self.client, calls, mode=1, flag=0,
                                    dispatch="interface", source="host", process=self.process)
        self.assertEqual(query.scope_address, self.process.base)
        self.assertEqual(query.callee_address, self.process.base + 88)
        payload_size = len(native.encode_batch(calls))
        self.assertEqual(self.process.read(query.callee_address, payload_size),
                         native.encode_batch(calls, request_address=query.callee_address))
        self.client.queue_v3_interface_callback.assert_called_once_with(0x10080, self.process.base)
        self.assertIsNone(query.read())
        self.assertEqual(self.process.freed, [])
        payload = completed_batch(calls, query.callee_address, error=87, completed=2)
        scope = access.REQUEST.pack(88, 0, 1, 0, 0x10040, query.callee_address,
            query.source_address, 0, 0, 0xABC, 0xCDE, 1234, 1, 1, 0)
        self.process.write(query.scope_address, scope + payload + b"host\0")
        self.client.v3_world_status.return_value = {"status": "ok", "completed_request_id": 7}
        result = query.read()
        self.assertEqual((result.error, result.entered, result.left), (0, 1, 1))
        self.assertEqual(result.callee_result.error, 87)
        self.assertEqual(result.callee_result.completed, 2)
        self.assertEqual(result.callee_result.calls[2].error, 997)
        self.assertEqual(result.callee_result.calls[0].rax, 0xFEDCBA9876543210)
        self.assertEqual(result.callee_result.calls[0].xmm0_low64, native.float64_bits(-2.5))
        self.assertIs(query.read(), result)
        self.assertEqual(self.process.freed, [self.process.base])
        self.assertFalse(self.process.closed)
        self.client.queue_v3_interface_callback.assert_called_once()

    def test_raw_session_batch_keeps_two_stage_layout_and_lifetime(self):
        calls = ((0x10, (1, 2, 3, 4, 5)), (0x20, (native.float32_bits(1.25),)))
        query = session.submit_batch(self.client, calls, process=self.process)
        outer = native.NativeCallResult.from_bytes(self.process.read(self.process.base, 56))
        self.assertEqual(outer.target, 0x11000)
        self.assertEqual(struct.unpack("<3Q", self.process.read(self.process.base + 56, 24)),
                         (0x10040, query.opaque_address, query.session_request_address))
        self.assertEqual(self.process.read(query.opaque_address, len(native.encode_batch(calls))),
                         native.encode_batch(calls, request_address=query.opaque_address))
        self.assertIsNone(query.read())
        self.client.v3_world_status.return_value = {"status": "ok", "completed_request_id": 7}
        self.process.write(self.process.base + 4, struct.pack("<I", 0))
        self.process.write(self.process.base + 32, struct.pack("<Q", 0))
        self.process.write(query.session_request_address, struct.pack("<Q", 11))
        self.process.state = session.CALLBACK_STATUS.pack(56, 1, 0, 0, 11, 10,
            0x10040, query.opaque_address, 0x50000)
        self.assertIsNone(query.read())
        self.assertEqual(query.session_request_id, 11)
        self.assertEqual(self.process.freed, [])
        self.process.write(query.opaque_address, completed_batch(calls, query.opaque_address))
        self.process.state = session.CALLBACK_STATUS.pack(56, 3, 0, 18100, 11, 11,
            0x10040, query.opaque_address, 0x50000)
        result = query.read()
        self.assertEqual((result.error, result.completed), (0, 2))
        self.assertEqual(query.session_status.execution_thread_id, 18100)
        self.assertIs(query.read(), result)
        self.assertEqual(self.process.freed, [self.process.base])
        self.assertFalse(self.process.closed)
        self.client.queue_v3_interface_callback.assert_called_once()

    def test_unknown_batch_delivery_retains_internal_process_and_whole_storage_without_replay(self):
        calls = ((0x10, (1, 2, 3)), (0x20, (4,)))
        for submit, module in ((lambda: access.submit_batch(self.client, calls, mode=1,
                                   flag=0, dispatch="interface", source="host"), access),
                               (lambda: session.submit_batch(self.client, calls), session)):
            with self.subTest(context=module.__name__):
                self.client.reset_mock()
                self.client.queue_v3_interface_callback.side_effect = BridgeError("ReadFile(pipe)", 109)
                self.process = BatchMemoryProcess()
                with patch.object(module, "RemoteProcess", return_value=self.process):
                    with self.assertRaises(BridgeError) as caught:
                        submit()
                query = caught.exception.query
                self.assertIs(query.process, self.process)
                self.assertFalse(self.process.closed)
                self.assertEqual(self.process.freed, [])
                payload_address = (query.callee_address if module is access else query.opaque_address)
                self.assertEqual(self.process.read(payload_address, len(native.encode_batch(calls))),
                                 native.encode_batch(calls, request_address=payload_address))
                with self.assertRaisesRegex(RuntimeError, "unknown"):
                    query.read()
                self.client.queue_v3_interface_callback.assert_called_once()
                self.assertEqual(self.process.freed, [])
                self.assertFalse(self.process.closed)
                query.recover(7)  # Explicitly known request; never enqueue it again.
                self.client.v3_world_status.return_value = {"status": "ok", "completed_request_id": 7}
                payload = completed_batch(calls, payload_address)
                if module is access:
                    scope = access.REQUEST.pack(88, 0, 1, 0, 0x10040, query.callee_address,
                        query.source_address, 0, 0, 0xABC, 0xCDE, 1234, 1, 1, 0)
                    self.process.write(query.scope_address, scope + payload + b"host\0")
                else:
                    self.process.write(self.process.base + 4, struct.pack("<I", 0))
                    self.process.write(self.process.base + 32, struct.pack("<Q", 0))
                    self.process.write(query.session_request_address, struct.pack("<Q", 11))
                    self.process.write(query.opaque_address, payload)
                    self.process.state = session.CALLBACK_STATUS.pack(56, 3, 0, 18100,
                        11, 11, 0x10040, query.opaque_address, 0x50000)
                self.assertIsNotNone(query.read())
                self.assertEqual(self.process.freed, [self.process.base])
                self.assertTrue(self.process.closed)
                self.client.queue_v3_interface_callback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
