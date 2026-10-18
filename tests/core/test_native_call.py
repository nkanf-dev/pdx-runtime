"""Focused packing/composition tests; native/tests supplies actual ASM evidence."""
from __future__ import annotations

import struct
import unittest
from unittest.mock import Mock

from pdx_runtime import native_call, native_callback
from pdx_runtime.native_bridge import BridgeError


class NativeCallTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=123)
        self.dispatch = Mock(return_value={"status": "ok", "request_id": 7})
        self.status = Mock()
        self.process = Mock(pid=123)
        self.context = native_callback.CallbackContext(self.client, self.dispatch,
            self.status, Mock(return_value=None), lambda process: process is self.process,
            lambda response: native_callback.CallbackDisposition("accepted", response["request_id"]))
        self.process.resolve_export.return_value = 0x10000
        self.process.allocate.return_value = 0x20000

    def test_exact_request_layout_and_positional_float_bits(self):
        bits = (11, native_call.float64_bits(1.25), 17, native_call.float64_bits(2.5),
                native_call.float32_bits(3.75))
        encoded = native_call.encode_request(0xFFFFFFFFABCDEF01, bits, request_address=0x20000)
        self.assertEqual(native_call.REQUEST.size, 56)
        fields = native_call.REQUEST.unpack_from(encoded)
        self.assertEqual(fields[:6], (56, 997, 0xFFFFFFFFABCDEF01, 5, 0x20038, 0))
        self.assertEqual(struct.unpack_from("<5Q", encoded, 56), bits)
        self.assertEqual(struct.unpack("<d", struct.pack("<Q", bits[1]))[0], 1.25)
        self.assertEqual(struct.unpack("<f", struct.pack("<I", bits[4]))[0], 3.75)

    def test_one_contiguous_allocation_and_shared_pending_lifetime(self):
        request = native_call.submit(self.client, 0xABC, (11, 22, 33, 44, 55), context=self.context, process=self.process)
        self.assertIsInstance(request, native_callback.CallbackResult)
        self.assertEqual(request.arguments_address, 0x20038)
        self.process.allocate.assert_called_once_with(96)
        self.assertEqual(self.process.write.call_args_list[1].args,
                         (0x20018, struct.pack("<Q", 0x20038)))
        self.dispatch.assert_called_once_with(0x10000, 0x20000)
        self.status.return_value = {"status": "ok", "completed_request_id": 6}
        self.assertIsNone(request.read())
        self.process.free.assert_not_called()

    def test_completed_raw128_result_releases_request_and_args_together(self):
        request = native_call.submit(self.client, 0xABC, (1, 2), context=self.context, process=self.process)
        words = struct.pack("<QQ", 0x1122334455667788, 0xFEDCBA9876543210)
        self.process.read.return_value = native_call.REQUEST.pack(56, 0, 0xABC, 2,
            0x20038, 0xFFFFFFFFABCDEF01, words) + struct.pack("<QQ", 1, 2)
        self.status.return_value = {"status": "ok", "completed_request_id": 7}
        result = request.read()
        self.assertEqual(result.rax, 0xFFFFFFFFABCDEF01)
        self.assertEqual(result.xmm0, words)
        self.assertEqual(result.xmm0_low64, 0x1122334455667788)
        self.assertEqual(result.xmm0_high64, 0xFEDCBA9876543210)
        self.assertIs(request.read(), result)
        self.process.free.assert_called_once_with(0x20000)
        self.process.close.assert_not_called()

    def test_empty_arguments_need_no_address_patch(self):
        request = native_call.submit(self.client, 0xABC, context=self.context, process=self.process)
        self.process.write.assert_called_once_with(0x20000,
            native_call.encode_request(0xABC, request_address=0))
        self.assertEqual(request.arguments_address, 0)
        self.process.resolve_export.assert_called_once_with("pdx_runtime.dll", "PdxNativeCallInterface")

    def test_prequeue_argument_pointer_patch_failure_cleans_once(self):
        self.process.write.side_effect = [None, BridgeError("WriteProcessMemory", 299)]
        with self.assertRaises(BridgeError):
            native_call.submit(self.client, 0xABC, (1,), context=self.context, process=self.process)
        self.process.free.assert_called_once_with(0x20000)
        self.dispatch.assert_not_called()

    def test_unknown_delivery_keeps_whole_buffer_without_replay(self):
        self.dispatch.side_effect = BridgeError("ReadFile", 109)
        with self.assertRaises(BridgeError) as captured:
            native_call.submit(self.client, 0xABC, (1,), context=self.context, process=self.process)
        request = captured.exception.query
        self.assertEqual(request.arguments_address, request.result_address + 56)
        self.assertIsNone(request.request_id)
        self.process.free.assert_not_called()
        self.dispatch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
