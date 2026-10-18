"""Scope request packing/shared lifetime; native own-host supplies execution proof."""
from __future__ import annotations

import struct
import unittest
from unittest.mock import Mock
from tests.native_sdk.test_v3_query import bind_fixture_wire

from pdx_runtime import native_call, v3, v3_access, v3_session
from pdx_runtime.native_bridge import BridgeError


class V3AccessTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=123)
        bind_fixture_wire(self.client)
        self.client.queue_v3_interface_callback.return_value = {"error": 0, "status": "ok", "request_id": 7}
        self.process = Mock(pid=123)
        exports = {"PdxNativeCallInterface": 0x10000, "PdxV3AccessCallback": 0x11000,
            "PdxV3QueueSessionCallback": 0x12000, "PdxV3SessionCallbackState": 0x13000}
        self.process.resolve_export.side_effect = lambda module, symbol, *, aliases=None: exports[symbol]
        self.process.allocate.return_value = 0x20000

    def completed_scope(self, callback=0x50000, payload=b"", *, address=0x20000):
        return v3_access.REQUEST.pack(88, 0, 1, 0, callback, address + 88,
            address + 88 + len(payload), 0, 0, 0xABC, 0xCDE, 1234, 1, 1, 0) + payload + b"host\0"

    def test_raw_mode_flag_and_exact_public_layout(self):
        blob = v3_access.encode_request(0x50000, 0x60000, 0x70000, mode=0xFEDCBA98,
            flag=-17, enter=0x80000, leave=0x90000)
        self.assertEqual(len(blob), 88)
        result = v3_access.AccessResult.from_bytes(blob)
        self.assertEqual((result.error, result.mode, result.flag), (997, 0xFEDCBA98, -17))
        self.assertEqual((result.callbackfn, result.opaque, result.source, result.enter, result.leave),
                         (0x50000, 0x60000, 0x70000, 0x80000, 0x90000))

    def test_interface_one_allocation_pending_and_callee_opaque_address(self):
        handle = v3_access.submit_callback(self.client, 0x50000, bytes(8), mode=1, flag=0,
            dispatch="interface", source="host", process=self.process,
            prepare_payload=lambda address: struct.pack("<Q", address))
        self.assertIsInstance(handle, v3.CallbackResult)
        self.assertEqual((handle.scope_address, handle.callee_address, handle.source_address),
                         (0x20000, 0x20058, 0x20060))
        self.process.allocate.assert_called_once_with(101)
        self.client.queue_v3_interface_callback.assert_called_once_with(0x11000, 0x20000)
        blob = self.process.write.call_args_list[-1].args[1]
        self.assertEqual(struct.unpack_from("<Q", blob, 24)[0], 0x20058)
        self.assertEqual(struct.unpack_from("<Q", blob, 32)[0], 0x20060)
        self.assertEqual(struct.unpack_from("<Q", blob, 88)[0], 0x20058)
        self.assertEqual(blob[-5:], b"host\0")
        self.client.v3_world_status.return_value = {"status": "ok", "completed_request_id": 6}
        self.assertIsNone(handle.read())
        self.process.free.assert_not_called()

    def test_completed_scope_decodes_owned_payload_and_frees_once(self):
        handle = v3_access.submit_callback(self.client, 0x50000, bytes(8), mode=1, flag=0,
            dispatch="interface", source="host", process=self.process,
            decode_payload=lambda data: struct.unpack("<Q", data)[0])
        self.client.v3_world_status.return_value = {"status": "ok", "completed_request_id": 7}
        self.process.read.return_value = self.completed_scope(payload=struct.pack("<Q", 42))
        result = handle.read()
        self.assertEqual((result.context, result.execution_thread_id, result.entered, result.left),
                         (0xABC, 1234, 1, 1))
        self.assertEqual(result.callee_result, 42)
        self.assertIs(handle.read(), result)
        self.process.free.assert_called_once_with(0x20000)
        self.process.close.assert_not_called()

    def test_raw_call_composition_uses_existing_positional_packer(self):
        args = (11, native_call.float64_bits(1.25), 17, 19, 23)
        handle = v3_access.submit_call(self.client, 0xABCDEF, args, mode=1, flag=0,
            dispatch="interface", source="host", process=self.process)
        self.assertEqual(handle.handler_address, 0x10000)
        self.assertEqual(handle.arguments_address, 0x20058 + 56)
        blob = self.process.write.call_args_list[-1].args[1]
        inner = blob[88:88 + 56 + 8 * len(args)]
        self.assertEqual(inner, native_call.encode_request(0xABCDEF, args, request_address=0x20058))

    def test_session_completion_not_interface_enqueue_owns_scope_lifetime(self):
        handle = v3_access.submit_callback(self.client, 0x50000, bytes(8), mode=3, flag=1,
            dispatch="session", source="host", process=self.process)
        self.assertIsInstance(handle, v3_session.SessionCallbackResult)
        scope_address = 0x20000 + v3_session.OPAQUE_OFFSET
        self.assertEqual(handle.scope_address, scope_address)
        blob = self.process.write.call_args_list[-1].args[1]
        enqueue = native_call.NativeCallResult.from_bytes(blob)
        self.assertEqual(enqueue.target, 0x12000)
        self.assertEqual(struct.unpack_from("<3Q", blob, native_call.REQUEST.size),
                         (0x11000, scope_address, handle.session_request_address))
        self.client.v3_world_status.return_value = {"status": "ok", "completed_request_id": 7}
        enqueue_completed = native_call.REQUEST.pack(56, 0, 0x12000, 3, 0x20038, 0, bytes(16))
        self.process.read.side_effect = [enqueue_completed, struct.pack("<Q", 9),
            v3_session.CALLBACK_STATUS.pack(56, 1, 0, 0, 9, 8, 0x11000, scope_address, 0)]
        self.assertIsNone(handle.read())
        self.process.free.assert_not_called()
        completed = self.completed_scope(payload=bytes(8), address=scope_address)
        self.process.read.side_effect = [
            v3_session.CALLBACK_STATUS.pack(56, 3, 0, 18100, 9, 9, 0x11000, scope_address, 0xAAA),
            bytes(v3_session.OPAQUE_OFFSET) + completed]
        self.assertEqual(handle.read().left, 1)
        self.process.free.assert_called_once_with(0x20000)

    def test_prequeue_patch_failure_and_unknown_delivery_retain_rules(self):
        self.process.write.side_effect = [None, BridgeError("WriteProcessMemory", 299)]
        with self.assertRaises(BridgeError):
            v3_access.submit_callback(self.client, 0x50000, mode=1, flag=0,
                dispatch="interface", process=self.process)
        self.process.free.assert_called_once_with(0x20000)
        self.client.queue_v3_interface_callback.assert_not_called()
        self.process.reset_mock()
        self.process.write.side_effect = None
        self.client.queue_v3_interface_callback.side_effect = BridgeError("ReadFile", 109)
        with self.assertRaises(BridgeError) as captured:
            v3_access.submit_callback(self.client, 0x50000, mode=1, flag=0,
                dispatch="interface", process=self.process)
        self.assertEqual(captured.exception.query.result_address, 0x20000)
        self.assertEqual(captured.exception.query.scope_address, 0x20000)
        self.assertEqual(captured.exception.query.callee_address, 0x20058)
        self.assertEqual(captured.exception.query.handler_address, 0x50000)
        self.process.free.assert_not_called()


if __name__ == "__main__":
    unittest.main()
