"""CPU-only dialog wire, exact-profile gate and callback ownership tests.

No game/process/native calls. Mock acknowledgements exercise delivery mechanics;
these tests do not prove original VM getters or historical dialog display.
"""
import unittest
from unittest.mock import Mock, patch
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES

from pdx_runtime import v3_ui_dialogs as ui
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.target_descriptor import UnsupportedBuild


class DialogWireTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=101)
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
        self.process = Mock(pid=101)
        self.process.resolve_export.return_value = 0x1000
        self.process.allocate.return_value = 0x2000
        self.gate = patch.object(ui, "verify_profile", return_value=object())
        self.verify = self.gate.start()
        self.addCleanup(self.gate.stop)

    def submit(self, source="jomini_notifications", **kwargs):
        return ui.snapshot(self.client, source=source, process=self.process,
                           image_base=0x140000000, world=88, interface_thread_id=77, **kwargs)

    def packet(self):
        return self.process.write.call_args.args[1]

    def response(self, *, count=0, records=(), error=0, produced=1, valid=1):
        raw = bytearray(self.packet())
        header = list(ui.HEADER.unpack_from(raw))
        source, text_start = header[4], header[10]
        header[1] = error
        header[12] = count
        header[13] = len(records)
        header[18] = produced
        header[19] = valid
        header[20] = 77
        header[17] = int(bool(header[6] or len(records) < count or error == 234))
        field_index = 0
        text_position = text_start
        required_total = 0
        for index, record_values in enumerate(records):
            row_error = 0
            field_offset = header[8] + field_index * ui.FIELD.size
            for field_id, _expression in ui.GETTERS[source]:
                value = record_values.get(field_id, False if field_id in (5, 6) else b"")
                field_error = 0
                needed = None
                if isinstance(value, tuple):
                    value, field_error, needed = value
                scalar = field_id in (5, 6)
                copied = b"" if scalar else value
                needed = 0 if scalar else len(copied) if needed is None else needed
                field = (field_id, field_error, ui.Kind.BOOL if scalar else ui.Kind.STRING,
                         text_position, len(copied), needed, int(not field_error and len(copied) == needed),
                         0, int(value) if scalar else 0)
                offset = header[8] + field_index * ui.FIELD.size
                raw[offset:offset + ui.FIELD.size] = ui.FIELD.pack(*field)
                raw[text_position:text_position + len(copied)] = copied
                text_position += len(copied)
                required_total += needed
                field_index += 1
                row_error = row_error or field_error
            metadata = 7 if source == 1 else 6
            row = (header[6] + index, row_error, field_offset, len(ui.GETTERS[source]), metadata, 0, 1, 0)
            offset = header[7] + index * ui.RECORD.size
            raw[offset:offset + ui.RECORD.size] = ui.RECORD.pack(*row)
        header[14] = field_index
        header[15] = text_position - text_start
        header[16] = required_total
        raw[:ui.HEADER.size] = ui.HEADER.pack(*header)
        return bytes(raw)

    def test_wire_sizes_profile_gate_and_exact_export(self):
        self.submit(max_items=3, text_capacity=128, start_index=2)
        self.assertEqual((ui.HEADER.size, ui.RECORD.size, ui.FIELD.size), (96, 32, 40))
        header = ui.HEADER.unpack_from(self.packet())
        self.assertEqual((header[3], header[4], header[5], header[6], header[-1]), (77, 1, 3, 2, 88))
        self.assertEqual(header[8], 96 + 3 * 32)
        self.assertEqual(header[10], header[8] + 18 * 40)
        self.verify.assert_called_once_with(self.process, 0x140000000)
        self.process.resolve_export.assert_called_once_with("pdx_runtime.dll", "PdxV3UiDialogsInterface", aliases=V3_EXPORT_ALIASES)
        self.assertIs(self.process.resolve_export.call_args.kwargs["aliases"], V3_EXPORT_ALIASES)
        self.assertEqual(self.enqueue.call_count, 1)

    def test_three_sources_and_explicit_catalog_alias(self):
        for name, expected in (("jomini_notifications", 1), ("general_information", 2),
                               ("general_information_dialogs", 2), ("confirmations", 3)):
            with self.subTest(source=name):
                self.submit(name)
                self.assertEqual(ui.HEADER.unpack_from(self.packet())[4], expected)

    def test_genuine_empty_requires_production_and_valid_count(self):
        query = self.submit()
        self.process.read.return_value = self.response()
        result = query.read()
        self.assertEqual((result.error, result.source_count, result.source_produced, result.count_valid), (0, 0, 1, 1))
        self.assertEqual(result.records, ())
        self.assertIs(query.read(), result)
        self.assertEqual(self.enqueue.call_count, 1)

    def test_unavailable_is_not_an_empty_observation(self):
        query = self.submit()
        self.process.read.return_value = self.response(error=50, produced=0, valid=0)
        result = query.read()
        self.assertEqual((result.error, result.source_produced, result.count_valid), (50, 0, 0))
        self.assertEqual(result.records, ())

    def test_false_empty_text_and_metadata_are_preserved(self):
        query = self.submit()
        self.process.read.return_value = self.response(count=1, records=({1: b"", 2: b"body", 5: False, 6: True},))
        result = query.read()
        row = result.records[0]
        values = {field.name: field.value for field in row.fields}
        self.assertEqual(values["title"], "")
        self.assertIs(values["has_accept"], False)
        self.assertIs(values["has_decline"], True)
        self.assertEqual(row.fields[1].raw_bytes, b"body")
        self.assertTrue(row.consumed_valid)
        self.assertTrue(row.dialog_valid)
        self.assertTrue(row.widget_valid)
        self.assertEqual((row.consumed, row.has_dialog, row.has_widget), (0, 1, 0))
        self.assertEqual(row.fields[0].expression, "JominiNotification.GetTitle")
        self.assertFalse(hasattr(row, "object"))
        self.assertFalse(hasattr(row.fields[0], "value_handle"))

    def test_partial_and_invalid_utf8_preserve_original_bytes(self):
        query = self.submit()
        self.process.read.return_value = self.response(count=1, error=234,
            records=({2: (b"\xe4\xb8", 234, 3)},))
        result = query.read()
        value = result.records[0].fields[1]
        self.assertEqual((value.raw_bytes, value.text_bytes_required, value.complete), (b"\xe4\xb8", 3, 0))
        self.assertIsNone(value.raw_text)
        self.assertIsNone(value.value)
        self.assertEqual((result.text_bytes_copied, result.text_bytes_required, result.truncated), (2, 3, 1))

    def test_full_invalid_utf8_is_copied_not_rewritten(self):
        self.submit("general_information")
        result = ui.DialogSnapshot.from_bytes(self.response(count=1, records=({1: b"\xff"},)))
        field = result.records[0].fields[0]
        self.assertEqual(field.raw_bytes, b"\xff")
        self.assertEqual(field.complete, 1)
        self.assertIsNone(field.raw_text)
        self.assertIsNone(field.value)


    def test_pending_query_keeps_original_storage_and_does_not_replay(self):
        query = self.submit()
        self.completion.return_value = {"status": "ok", "completed_request_id": 6}
        self.assertIsNone(query.read())
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        self.assertEqual(self.enqueue.call_count, 1)

    def test_error_zero_is_not_a_rejected_submission(self):
        query = self.submit()
        self.assertEqual(query.request_id, 7)
        self.process.free.assert_not_called()
        self.enqueue.return_value = {"status": "error", "error": 0, "request_id": 8}
        with self.assertRaises(BridgeError) as caught:
            self.submit()
        self.assertIs(caught.exception.query.process, self.process)
        self.process.free.assert_not_called()

    def test_known_rejection_releases_only_exact_request(self):
        self.enqueue.return_value = {"status": "error", "error": 5}
        with self.assertRaises(BridgeError):
            self.submit()
        self.process.free.assert_called_once_with(0x2000)
        self.assertEqual(self.enqueue.call_count, 1)

    def test_wrong_pid_stops_before_fingerprint_export_allocation_or_dispatch(self):
        self.process.pid = 102
        with self.assertRaises(ValueError):
            self.submit()
        self.verify.assert_not_called()
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.enqueue.assert_not_called()

    def test_unknown_profile_and_old_dll_fail_without_fallback(self):
        self.verify.side_effect = UnsupportedBuild("unknown original file")
        with self.assertRaises(UnsupportedBuild):
            self.submit()
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.enqueue.assert_not_called()
        self.verify.side_effect = None
        self.process.resolve_export.side_effect = BridgeError("resolve_export", reason="export not found")
        with self.assertRaises(BridgeError):
            self.submit()
        self.process.allocate.assert_not_called()
        self.enqueue.assert_not_called()

    def test_validation_precedes_all_external_operations(self):
        invalid = ({"world": 0}, {"interface_thread_id": False}, {"max_items": 0},
                   {"max_items": 257}, {"text_capacity": ui.MAX_TEXT + 1},
                   {"start_index": -1}, {"start_index": 0x80000000}, {"image_base": 0})
        base = dict(source="jomini_notifications", process=self.process,
                    image_base=0x140000000, world=88, interface_thread_id=77)
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                ui.snapshot(self.client, **(base | change))
        with self.assertRaises(ValueError):
            self.submit("settings_windows")
        self.verify.assert_not_called()
        self.process.resolve_export.assert_not_called()
        self.enqueue.assert_not_called()

    def test_decoder_rejects_fabricated_empty_and_invalid_field_spans(self):
        self.submit()
        raw = bytearray(self.response())
        header = list(ui.HEADER.unpack_from(raw))
        header[18] = 0
        raw[:ui.HEADER.size] = ui.HEADER.pack(*header)
        with self.assertRaises(ValueError):
            ui.DialogSnapshot.from_bytes(bytes(raw))
        raw = bytearray(self.response(count=1, records=({1: b"actual"},)))
        header = ui.HEADER.unpack_from(raw)
        field = list(ui.FIELD.unpack_from(raw, header[8]))
        field[3] = 0
        raw[header[8]:header[8] + ui.FIELD.size] = ui.FIELD.pack(*field)
        with self.assertRaises(ValueError):
            ui.DialogSnapshot.from_bytes(bytes(raw))

    def test_decoder_rejects_retagged_fields_and_false_truthiness_bugs(self):
        self.submit()
        raw = bytearray(self.response(count=1, records=({},)))
        header = ui.HEADER.unpack_from(raw)
        offset = header[8] + 4 * ui.FIELD.size
        field = list(ui.FIELD.unpack_from(raw, offset))
        field[-1] = 2
        raw[offset:offset + ui.FIELD.size] = ui.FIELD.pack(*field)
        with self.assertRaises(ValueError):
            ui.DialogSnapshot.from_bytes(bytes(raw))
        raw = bytearray(self.response(count=1, records=({},)))
        field = list(ui.FIELD.unpack_from(raw, header[8]))
        field[0] = 7
        raw[header[8]:header[8] + ui.FIELD.size] = ui.FIELD.pack(*field)
        with self.assertRaises(ValueError):
            ui.DialogSnapshot.from_bytes(bytes(raw))

    def test_profile_uses_shared_exact_file_and_runtime_verifier(self):
        self.gate.stop()
        with patch.object(ui, "verify_loaded_target", return_value="verified") as verifier:
            self.assertEqual(ui.verify_profile(self.process, 0x140000000), "verified")
            verifier.assert_called_once_with(self.process, 0x140000000, definitions=(ui._PROFILE,))
        self.assertEqual(ui._PROFILE.layout["wire_header_bytes"], ui.HEADER.size)


if __name__ == "__main__":
    unittest.main()
