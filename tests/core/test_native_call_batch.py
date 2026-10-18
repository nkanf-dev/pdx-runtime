"""Focused ordinary Win64 batch wire/lifetime tests; no native target is executed."""
from __future__ import annotations

import struct
import unittest
from unittest.mock import Mock

from pdx_runtime import native_call as native, native_callback
from pdx_runtime.native_bridge import BridgeError

ADDRESS = 0x200000000


class NativeCallBatchTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=123)
        self.dispatch = Mock(return_value={"status": "ok", "request_id": 7})
        self.status = Mock()
        self.process = Mock(pid=123)
        self.context = native_callback.CallbackContext(self.client, self.dispatch,
            self.status, Mock(return_value=None), lambda process: process is self.process,
            lambda response: native_callback.CallbackDisposition("accepted", response["request_id"]))
        self.process.resolve_export.return_value = 0x10000
        self.process.allocate.return_value = ADDRESS

    def test_contiguous_header_requests_and_argument_arrays_use_exact_dynamic_pointers(self):
        calls = ((0x10, (1, native.float64_bits(2.5), 3)),
                 (0x20, ()), (0x30, (native.float32_bits(-1.25), 9)))
        blob = native.encode_batch(calls, request_address=ADDRESS)
        self.assertEqual(native.BATCH_REQUEST.size, 32)
        self.assertEqual(native.REQUEST.size, 56)
        self.assertEqual(native.BATCH_REQUEST.unpack_from(blob), (32, 997, 3, ADDRESS + 32, 0))
        args_offset = 32 + 3 * 56
        expected_pointers = (ADDRESS + args_offset, 0, ADDRESS + args_offset + 24)
        for index, ((target, arguments), pointer) in enumerate(zip(calls, expected_pointers)):
            request = native.REQUEST.unpack_from(blob, 32 + index * 56)
            self.assertEqual(request[:5], (56, 997, target, len(arguments), pointer))
        self.assertEqual(struct.unpack_from("<5Q", blob, args_offset), calls[0][1] + calls[2][1])
        template = native.encode_batch(calls)
        self.assertEqual(native.BATCH_REQUEST.unpack_from(template)[3], 0)
        for index in range(3):
            self.assertEqual(native.REQUEST.unpack_from(template, 32 + index * 56)[4], 0)
        empty = native.encode_batch((), request_address=ADDRESS)
        self.assertEqual(empty, native.BATCH_REQUEST.pack(32, 997, 0, 0, 0))
        self.assertEqual(native.NativeCallBatchResult.from_bytes(empty).calls, ())

    def test_one_queue_pending_retains_entire_allocation_and_completed_prefix_raw_results(self):
        calls = ((0x10, (1, 2)), (0, ()), (0x30, (3,)))
        query = native.submit_batch(self.client, calls, context=self.context, process=self.process)
        expected = native.encode_batch(calls, request_address=ADDRESS)
        self.process.allocate.assert_called_once_with(len(expected))
        self.assertEqual([call.args for call in self.process.write.call_args_list],
                         [(ADDRESS, native.encode_batch(calls)), (ADDRESS, expected)])
        self.dispatch.assert_called_once_with(0x10000, ADDRESS)
        self.process.resolve_export.assert_called_once_with("pdx_runtime.dll", "PdxNativeCallBatchInterface")
        self.status.return_value = {"status": "ok", "completed_request_id": 6}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()

        result_blob = bytearray(expected)
        native.BATCH_REQUEST.pack_into(result_blob, 0, 32, 87, 3, ADDRESS + 32, 2)
        xmm = struct.pack("<QQ", native.float64_bits(-2.5), 0xFEDCBA9876543210)
        native.REQUEST.pack_into(result_blob, 32, 56, 0, 0x10, 2, ADDRESS + 200,
                                 0xFEDCBA9876543210, xmm)
        native.REQUEST.pack_into(result_blob, 88, 56, 87, 0, 0, 0, 0, bytes(16))
        self.process.read.return_value = bytes(result_blob)
        self.status.return_value = {"status": "ok", "completed_request_id": 7}
        result = query.read()
        self.assertEqual((result.error, result.completed, result.count), (87, 2, 3))
        self.assertEqual(result.calls[0].rax, 0xFEDCBA9876543210)
        self.assertEqual(result.calls[0].xmm0, xmm)
        self.assertEqual(result.calls[0].xmm0_low64, native.float64_bits(-2.5))
        self.assertEqual(result.calls[0].xmm0_high64, 0xFEDCBA9876543210)
        self.assertEqual(result.calls[1].error, 87)
        self.assertEqual(result.calls[2].error, 997)
        self.assertEqual(result.calls[2].target, 0x30)
        self.assertIs(query.read(), result)
        self.process.free.assert_called_once_with(ADDRESS)
        self.process.close.assert_not_called()
        self.dispatch.assert_called_once()

    def test_unknown_batch_delivery_keeps_all_requests_and_arguments_without_replay(self):
        calls = ((0x10, (1, 2, 3, 4, 5)), (0x20, (6,)))
        self.dispatch.side_effect = BridgeError("ReadFile(pipe)", 109)
        with self.assertRaises(BridgeError) as caught:
            native.submit_batch(self.client, calls, context=self.context, process=self.process)
        query = caught.exception.query
        self.assertIs(query.client, self.client)
        self.assertIs(query.process, self.process)
        self.assertEqual(query.result_address, ADDRESS)
        self.process.allocate.assert_called_once_with(len(native.encode_batch(calls)))
        self.assertEqual(self.process.write.call_args.args, (ADDRESS, native.encode_batch(calls, request_address=ADDRESS)))
        self.process.free.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            query.read()
        self.dispatch.assert_called_once()
        self.process.free.assert_not_called()


if __name__ == "__main__":
    unittest.main()
