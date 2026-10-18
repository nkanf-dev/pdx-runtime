"""CPU map wire/decoder tests only; no native/game source is opened."""
import unittest
from unittest.mock import Mock
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES
from pdx_runtime import v3_ui_map as ui


class MapWireTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=1)
        self.enqueue = Mock()
        self.completion = Mock()
        def request(command):
            if command == "world-status":
                return self.completion()
            if command.startswith("interface-callback "):
                parts = command.split()
                return self.enqueue(int(parts[1], 0), int(parts[2], 0))
            raise AssertionError("unexpected generic transport command: " + command)
        self.client.request.side_effect = request
        self.enqueue.return_value = {"status":"ok", "error":0, "request_id":7}
        self.completion.return_value = {"status":"ok", "completed_request_id":7}
        self.process = Mock(pid=1)
        self.process.resolve_export.return_value = 0x1000
        self.process.allocate.return_value = 0x2000

    def packet(self):
        return self.process.write.call_args.args[1]

    def complete(self, *, error=0, count=0, text=None, required=None, complete=1):
        raw = bytearray(self.packet()); fields = list(ui.HEADER.unpack_from(raw))
        fields[1] = error; fields[8] = count; fields[13] = fields[14] = 1
        if text is not None:
            fields[9] = 1; fields[10] = len(text); fields[11] = len(text) if required is None else required
            fields[12] = not complete
            row = (fields[16], error, fields[6], len(text), fields[11], complete)
            raw[fields[5]:fields[5]+ui.RECORD.size] = ui.RECORD.pack(*row)
            raw[fields[6]:fields[6]+len(text)] = text
        raw[:ui.HEADER.size] = ui.HEADER.pack(*fields)
        return bytes(raw)

    def test_profiled_map_callback_and_no_raw_pointer_wire(self):
        ui.snapshot(self.client, world=88, interface_thread_id=77, max_items=3, start_index=9, text_capacity=128, process=self.process)
        fields = ui.HEADER.unpack_from(self.packet())
        self.assertEqual(ui.HEADER.size, 80); self.assertEqual(ui.RECORD.size, 24)
        self.assertEqual((fields[3],fields[4],fields[16],fields[18]),(77,3,9,88))
        self.process.resolve_export.assert_called_with("pdx_runtime.dll", "PdxV3UiMapNotificationsInterface", aliases=V3_EXPORT_ALIASES)
        self.assertIs(self.process.resolve_export.call_args.kwargs["aliases"], V3_EXPORT_ALIASES)

    def test_genuine_empty_is_produced_valid_not_missing(self):
        q = ui.snapshot(self.client, world=88, interface_thread_id=77, process=self.process)
        self.process.read.return_value = self.complete()
        result = q.read()
        self.assertEqual((result.error,result.source_count,result.source_produced,result.collection_valid),(0,0,1,1))
        self.assertEqual(result.records, ())
        self.assertIs(q.read(), result)
        self.assertEqual(self.enqueue.call_count, 1)

    def test_partial_bytes_required_size_and_ordinal_not_identity(self):
        q = ui.snapshot(self.client, world=88, interface_thread_id=77, start_index=3, process=self.process)
        self.process.read.return_value = self.complete(error=234,count=5,text=b"a\xff",required=100,complete=0)
        result = q.read(); row = result.records[0]
        self.assertEqual(row.source_index,3); self.assertEqual(row.raw_bytes,b"a\xff")
        self.assertIsNone(row.raw_text); self.assertEqual(row.text_bytes_required,100)
        self.assertFalse(row.complete)

    def test_decoder_rejects_unproven_success_and_record_bounds(self):
        ui.snapshot(self.client, world=88, interface_thread_id=77, process=self.process)
        raw = bytearray(self.complete()); fields = list(ui.HEADER.unpack_from(raw)); fields[13] = 0
        raw[:ui.HEADER.size] = ui.HEADER.pack(*fields)
        with self.assertRaises(ValueError): ui.MapSnapshot.from_bytes(bytes(raw))
        raw = bytearray(self.complete(count=1,text=b"native")); fields = ui.HEADER.unpack_from(raw)
        row = list(ui.RECORD.unpack_from(raw,fields[5]));row[2] = 0
        raw[fields[5]:fields[5]+ui.RECORD.size] = ui.RECORD.pack(*row)
        with self.assertRaises(ValueError): ui.MapSnapshot.from_bytes(bytes(raw))

    def test_pending_original_query_does_not_replay(self):
        q = ui.snapshot(self.client, world=88, interface_thread_id=77, process=self.process)
        self.completion.return_value = {"status":"ok", "completed_request_id":6}
        self.assertIsNone(q.read());self.assertIsNone(q.read())
        self.process.free.assert_not_called()
        self.assertEqual(self.enqueue.call_count,1)

    def test_validation_is_before_any_submission(self):
        for kwargs in ({"world":0,"interface_thread_id":77},{"world":88,"interface_thread_id":0},
                       {"world":88,"interface_thread_id":77,"max_items":False}):
            with self.assertRaises(ValueError): ui.snapshot(self.client,process=self.process,**kwargs)
        self.enqueue.assert_not_called()
