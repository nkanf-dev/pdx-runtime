"""CPU wire/ownership tests. No native host or game is opened by this suite."""
import unittest
from unittest.mock import Mock

from pdx_runtime import v3_data as data


class ProjectionTests(unittest.TestCase):
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
        self.enqueue.return_value = {"status": "ok", "error": 0, "request_id": 7}
        self.completion.return_value = {"status": "ok", "completed_request_id": 7}
        self.process = Mock(pid=1)
        self.process.resolve_export.return_value = 0x1000
        self.process.allocate.return_value = 0x2000

    def packet(self):
        return self.process.write.call_args.args[1]

    def completed(self, *, error=0, kind=data.Kind.BOOL, bits=0, produced=1,
                  count_valid=0, count=0, text=b"", total=None, pointer=0):
        raw = bytearray(self.packet())
        fields = list(data.REQUEST.unpack_from(raw))
        fields[1] = error; fields[11] = kind; fields[13] = pointer; fields[26] = bits
        fields[9] = len(text) if total is None else total
        fields[19] = len(text)
        raw[fields[24]:fields[24] + len(text)] = text
        raw[:data.REQUEST.size] = data.REQUEST.pack(*fields)
        suffix = list(data.PROJECTION_SUFFIX.unpack_from(raw, data.REQUEST.size))
        suffix[1:4] = [produced, count_valid, count]
        raw[data.REQUEST.size:data.PROJECTION_HEADER_SIZE] = data.PROJECTION_SUFFIX.pack(*suffix)
        return bytes(raw)

    def test_root_wire_world_and_original_expression(self):
        data.evaluate_copied(self.client, "ToastMessageHandler.HasMessage", world=0x8888,
            output_bytes=128, process=self.process)
        raw = self.packet(); fields = data.REQUEST.unpack_from(raw)
        self.assertEqual(data.PROJECTION_HEADER_SIZE, 392)
        self.assertEqual(fields[2], 17)
        self.assertEqual(data.PROJECTION_SUFFIX.unpack_from(raw, data.REQUEST.size), (0x8888, 0, 0, 0, 0))
        self.assertEqual(raw[fields[20]:fields[20]+fields[21]], b"ToastMessageHandler.HasMessage")
        self.assertEqual(fields[22] % 8, 0)
        self.assertEqual(fields[24] % 8, 0)

    def test_source_count_zero_has_positive_native_production_flags(self):
        q = data.source_count(self.client, "MessageFeedHandler.GetItems", world=0x88, process=self.process)
        self.assertEqual(data.REQUEST.unpack_from(self.packet())[2], 18)
        self.process.read.return_value = self.completed(kind=data.Kind.EMPTY, count_valid=1)
        result = q.read()
        self.assertEqual((result.error, result.source_produced, result.count_valid, result.source_count), (0, 1, 1, 0))
        self.assertEqual(result.result.value_handle, 0)
        self.assertIs(q.read(), result)
        self.process.free.assert_called_once_with(0x2000)
        self.assertEqual(self.enqueue.call_count, 1)

    def test_zero_and_false_are_not_empty(self):
        for kind, bits, expected in [(data.Kind.BOOL, 0, False), (data.Kind.U64, 0, 0),
                                     (data.Kind.U32, 0xffffffff, 0xffffffff)]:
            q = data.evaluate_copied(self.client, "Global.GetValue", world=0x88, process=self.process)
            self.process.read.return_value = self.completed(kind=kind, bits=bits)
            self.assertEqual(q.read().value, expected)

    def test_partial_utf8_retains_exact_bytes_and_no_handle(self):
        q = data.evaluate_copied(self.client, "Global.GetText", world=0x88, output_bytes=3, process=self.process)
        self.process.read.return_value = self.completed(error=234, kind=data.Kind.STRING,
            text=b"abc", total=9)
        result = q.read()
        self.assertEqual(result.result.snapshot, b"abc")
        self.assertEqual(result.result.value_handle, 0)
        self.assertEqual(result.source_produced, 1)
        with self.assertRaises(ValueError):
            _ = result.value

    def test_older_dll_error_is_not_empty_source_success(self):
        q = data.source_count(self.client, "MessageFeedHandler.GetItems", world=0x88, process=self.process)
        self.process.read.return_value = self.completed(error=87, kind=data.Kind.RAW, produced=0)
        result = q.read()
        self.assertEqual((result.error, result.source_produced, result.count_valid), (87, 0, 0))

    def test_pointer_or_unproven_success_is_rejected(self):
        data.evaluate_copied(self.client, "Global.GetValue", world=0x88, process=self.process)
        for options in [{"pointer": 9}, {"produced": 0}, {"kind": data.Kind.OBJECT}, {"count_valid": 1, "produced": 0}]:
            with self.assertRaises(ValueError):
                data.ProjectionResult.from_bytes(self.completed(**options))
        data.source_count(self.client, "Global.GetList", world=0x88, process=self.process)
        with self.assertRaises(ValueError):
            data.ProjectionResult.from_bytes(self.completed(kind=data.Kind.EMPTY))

    def test_pending_query_does_not_free_or_replay(self):
        q = data.evaluate_copied(self.client, "Global.GetValue", world=0x88, process=self.process)
        self.completion.return_value = {"status": "ok", "completed_request_id": 6}
        self.assertIsNone(q.read()); self.assertIsNone(q.read())
        self.process.free.assert_not_called()
        self.assertEqual(self.enqueue.call_count, 1)

    def test_validation_precedes_queueing(self):
        for world in (0, -1, True, 1 << 64):
            with self.assertRaises(ValueError):
                data.evaluate_copied(self.client, "Global.GetValue", world=world, process=self.process)
        for expression in ("", "Global.Get\0Value"):
            with self.assertRaises(ValueError):
                data.source_count(self.client, expression, world=1, process=self.process)
        with self.assertRaises(ValueError):
            data.evaluate_copied(self.client, "Global.GetValue", world=1, output_bytes=-1, process=self.process)
        self.enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
