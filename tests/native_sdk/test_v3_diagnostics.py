from __future__ import annotations
import unittest
from unittest.mock import Mock
from pdx_runtime import v3_diagnostics as diag

class DiagnosticsTests(unittest.TestCase):
    def _wire_request_fixture(self, command):
        """CPU transport: preserve raw responses through exact V3 wire commands."""
        if command == "world-status":
            return self.world_status()
        words = command.split()
        if len(words) == 3 and words[0] == "interface-callback":
            callback, address = int(words[1], 0), int(words[2], 0)
            self.assertEqual(command, f"interface-callback {callback:#x} {address:#x}")
            return self.interface_callback(callback, address)
        raise AssertionError(f"unexpected V3 fixture wire command: {command!r}")

    def setUp(self):
        self.client=Mock(spec_set=["pid", "request", "status"], pid=123)
        self.world_status = Mock()
        self.interface_callback = Mock()
        self.client.request.side_effect = self._wire_request_fixture
        self.interface_callback.return_value={"status":"ok", "error": 0,"request_id":9}
        self.process=Mock(pid=123);self.process.allocate.return_value=0x20000
        self.process.resolve_export.return_value=0x10000

    def header(self,capacity,*,operation=2,error=0,flags=0,after=4,next_id=5,output_size=0,
               required=0,count=0,eligible=0,store_count=3,more=0,available=1):
        return diag.HEADER.pack(104+capacity,error,operation,flags,after,5,7,next_id,
            2,capacity,output_size,required,count,eligible,store_count,more,
            1,available,0,7372,0,0x50000,100000,1000)

    def test_status_absence_is_not_empty_success(self):
        query=diag.status(self.client,process=self.process)
        self.assertEqual(diag.HEADER.size,104)
        self.world_status.return_value={"status":"ok","completed_request_id":9}
        self.process.read.return_value=self.header(0,operation=1,error=21,available=0)
        result=query.read()
        self.assertEqual(result.error,21);self.assertFalse(result.viewer_available)
        self.assertFalse(result.subscription_coverage_known)
        self.assertEqual(result.records,())

    def test_pending_storage_and_copied_native_record(self):
        query=diag.records(self.client,after_sequence=4,max_records=2,output_capacity=256,process=self.process)
        self.world_status.return_value={"status":"ok","completed_request_id":8}
        self.assertIsNone(query.read());self.process.free.assert_not_called()
        texts=(b"script",b"native.cpp",b"unknown effect")
        size=diag.RECORD.size+sum(map(len,texts))
        record=diag.RECORD.pack(size,5,65536,247,3,4,123456,*map(len,texts),0)+b"".join(texts)
        self.world_status.return_value={"status":"ok","completed_request_id":9}
        self.process.read.return_value=self.header(256,flags=4,output_size=size,required=size,count=1,eligible=3,more=1)+record+bytes(256-size)
        result=query.read();self.assertEqual(result.records[0].message,"unknown effect")
        self.assertEqual(result.records[0].native_time_raw,123456)
        self.assertEqual(result.records[0].group_raw,3)
        self.assertEqual(result.next_sequence,5);self.assertTrue(result.has_more)
        self.assertIs(query.read(),result);self.process.free.assert_called_once_with(0x20000)
        self.interface_callback.assert_called_once()

    def test_truncation_gap_reset_are_stream_flags_not_parser_status(self):
        result=diag.DiagnosticsResult.from_bytes(self.header(0,error=234,flags=15,
            output_size=0,required=100,count=0,eligible=3,more=1))
        self.assertTrue(result.gap);self.assertTrue(result.reset);self.assertTrue(result.output_truncated)
        self.assertEqual(result.error,234);self.assertEqual(result.records,())
        self.assertEqual(result.output_required,100)

    def test_empty_bounded_page_and_input_validation(self):
        diag.records(self.client,max_records=0,output_capacity=0,process=self.process)
        blob=self.process.write.call_args.args[1];values=diag.HEADER.unpack(blob)
        self.assertEqual((values[8],values[9]),(0,0))
        with self.assertRaises(ValueError):diag.records(self.client,after_sequence=-2,process=self.process)
        with self.assertRaises(ValueError):diag.records(self.client,max_records=-1,process=self.process)
        with self.assertRaises(ValueError):diag.records(self.client,expected_viewer=-1,process=self.process)

if __name__=="__main__":unittest.main()
