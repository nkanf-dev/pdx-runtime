"""Original NativeSource wire/copied-owner CPU fixtures. No VM or game calls.
Only the preset API example is contract-backed. Other expression literals are
WIRE-ONLY fixtures and do not prove VM registration or source compatibility.
SPDX-License-Identifier: MPL-2.0
"""
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import Mock

from pdx_runtime import v3_data as data


SINGLE_SOURCE_WIRE = "f0000000e5030000000000000000000070000000010000007800000001000000be00000010000000000000004000000000000000000000000000000000000000000000000000000000000000000000000000000021000000800000000100000000000000a80000000000000000000000a800000016000000f000000000000000ce0000001b00000000000000ffffffff00000000000000000000000000000000ffffffffffffffff47616d6552756c6553657474696e672e4765744b65790100000000000000000000000000000047657447616d6552756c65732e47657452756c655072657365747300000000000000"


class NativeSourceValueTests(unittest.TestCase):
    def test_strict_kind_frozen_dataclass_and_no_layout_or_pointer_fields(self):
        self.assertEqual(int(data.NativeSourceKind.PRESET_STORED_SETTINGS), 1)
        source = data.NativeSource(data.NativeSourceKind.PRESET_STORED_SETTINGS)
        self.assertIs(source.kind, data.NativeSourceKind.PRESET_STORED_SETTINGS)
        with self.assertRaises(FrozenInstanceError):
            source.kind = 0

    def test_invalid_bool_int_other_enum_and_unknown_kind_are_rejected(self):
        for kind in (0, 1, 2, -1, True, False, 1.0, "1", None,
                     data.NativeFieldKind.DECREE_TYPE_KEY, data.Kind.EMPTY):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                data.NativeSource(kind)

    def test_native_source_is_not_a_prefix_expression_or_native_field(self):
        source = data.NativeSource(data.NativeSourceKind.PRESET_STORED_SETTINGS)
        with self.assertRaises(ValueError):
            data.CreationStep(source, 0)
        with self.assertRaises(ValueError):
            data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY, source)


class NativeSourcePageTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=1)
        self.client.request.side_effect = lambda command: (
            {"status": "ok", "request_id": 7, "error": 0}
            if command.startswith("interface-callback ")
            else {"status": "ok", "completed_request_id": 7})
        self.process = Mock(pid=1)
        self.process.resolve_export.return_value = 0xA100
        self.process.export_owner.return_value = 0xA000
        self.process.allocate.return_value = 0x2000
        self.native = data.NativeSource(data.NativeSourceKind.PRESET_STORED_SETTINGS)
        self.prefix = data.CreationStep("GetGameRules.GetRulePresets", 0)
        self.query = None

    def submit(self, fields=("GameRuleSetting.GetKey",), **kwargs):
        if "sources" not in kwargs:
            kwargs.setdefault("source_expression", self.native)
        kwargs.setdefault("steps", (self.prefix,))
        self.query = data.project_page(self.client, fields, process=self.process, **kwargs)
        return self.process.write.call_args.args[1]

    def callback_count(self):
        return sum(call.args[0].startswith("interface-callback ")
                   for call in self.client.request.call_args_list)

    def grouped_table(self, raw):
        offset, count, cursor, reserved = data.PAGE_GROUP_INPUT.unpack_from(raw, 112)
        self.assertEqual(reserved, 0)
        records = [data.PAGE_SOURCE.unpack_from(raw, offset + index * 16) for index in range(count)]
        return records, cursor

    def returned(self, cells, *, error=0, count_valid=1, source_count=1, grouped=False):
        raw = bytearray(self.process.write.call_args.args[1])
        header = list(data.PAGE_REQUEST.unpack_from(raw))
        prefix = b""
        if grouped:
            prefix_bytes = data.PAGE_GROUP_OUTPUT.size + data.SOURCE_OBSERVATION.size
            prefix = (data.PAGE_GROUP_OUTPUT.pack(prefix_bytes, 1, 0, 1, 0, 1)
                      + data.SOURCE_OBSERVATION.pack(0, error, 1, count_valid, source_count,
                                                     0, len(cells) // header[4], 1, 0, 1, 1, 0))
        table, payload = bytearray(), bytearray()
        base = len(prefix) + len(cells) * data.PAGE_CELL.size
        for row, field, cell_error, kind, type_id, bits, text, total in cells:
            table.extend(data.PAGE_CELL.pack(row, field, cell_error, kind, type_id,
                0xffffffff, bits, base + len(payload), len(text), len(text) if total is None else total,
                0xffffffff, 0, 0, 0))
            payload.extend(text)
        copied = prefix + bytes(table + payload)
        header[1], header[11], header[12], header[13] = error, 91, source_count, count_valid
        header[14], header[15], header[16], header[17] = len(cells) // header[4], 0x3000, len(copied), len(cells)
        data.PAGE_REQUEST.pack_into(raw, 0, *header)
        def read(address, count):
            if address == 0x2000:
                return bytes(raw)
            offset = address - 0x3000
            return copied[offset:offset + count]
        self.process.read.side_effect = read
        return bytes(raw), copied, read

    def test_single_native_source_exact_wire_scope24_step40_and_span8_fields(self):
        raw = self.submit()
        self.assertEqual(raw, bytes.fromhex(SINGLE_SOURCE_WIRE))
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((data.PAGE_SOURCE.size, data.CREATION_STEP.size), (16, 40))
        self.assertEqual((header[3], header[4], header[5], header[8], header[18]), (112, 1, 120, 16, 32 | 1))
        self.assertEqual(data.PAGE_SOURCE.unpack_from(raw, header[7]), (1, 0, 0, 0))
        offset, length = data.PAGE_SPAN.unpack_from(raw, header[3])
        self.assertEqual(raw[offset:offset + length], b"GameRuleSetting.GetKey")
        steps_offset, count, completed, _, final_count, step_captures = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, 88)
        self.assertEqual((count, completed, final_count, step_captures), (1, 0, 0, 0))
        step = data.CREATION_STEP.unpack_from(raw, steps_offset)
        self.assertEqual(raw[step[0]:step[0] + step[1]], b"GetGameRules.GetRulePresets")
        self.assertEqual(self.process.resolve_export.call_args.args,
                         ("pdx_v3_data.dll", "PdxV3DataProjectOriginalSourcesInterface"))

    def test_single_descriptor_follows_utf8_without_extra_alignment_rule(self):
        raw = self.submit(("'x'",))  # wire-only literal field
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual(header[7], 168 + 3)
        self.assertEqual(header[7] % 4, 3)
        self.assertEqual(data.PAGE_SOURCE.unpack_from(raw, header[7]), (1, 0, 0, 0))
        self.assertNotIn(b"PRESET_STORED_SETTINGS", raw)

    def test_grouped_mixed_sources_all_use_source16_and_preserve_utf8_text(self):
        raw = self.submit(sources=("Owner.Settings", self.native, "Owner.设置"), source_index=1, start=4)
        header = data.PAGE_REQUEST.unpack_from(raw)
        records, cursor = self.grouped_table(raw)
        self.assertEqual((header[3], header[8], header[9], header[18]), (128, 0, 4, 32 | 4 | 1))
        self.assertEqual(cursor, 1)
        self.assertEqual([record[0] for record in records], [0, 1, 0])
        self.assertEqual(records[1], (1, 0, 0, 0))
        self.assertEqual([reserved for _, _, _, reserved in records], [0, 0, 0])
        self.assertEqual([raw[offset:offset + length] for _, offset, length, _ in records],
                         [b"Owner.Settings", b"", "Owner.设置".encode("utf-8")])

    def test_grouped_native_only_and_terminal_cursor_keep_canonical_descriptors(self):
        raw = self.submit(sources=(self.native, self.native), source_index=2)
        records, cursor = self.grouped_table(raw)
        self.assertEqual((records, cursor), ([(1, 0, 0, 0), (1, 0, 0, 0)], 2))
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((header[8], header[18]), (0, 37))
        self.assertEqual(self.callback_count(), 1)

    def test_prefix_captures_keep_separate_original_slot_tables(self):
        steps = (data.CreationStep("Root.Presets(A.Self,B.Self)", 2, 5,
                                   source_arguments=("B.Self", "A.Self")),)
        roots = ((data.ObjectBinding(91, 1001, retained=True), data.ObjectBinding(92, 1002, retained=True)),)
        raw = self.submit(steps=steps, binding_rows=roots)
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual(header[18], 32 | 1 | 8)
        step_offset, count, _, _, final_count, captures = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, 88)
        self.assertEqual((count, final_count), (1, 0))
        step = data.CREATION_STEP.unpack_from(raw, step_offset)
        self.assertEqual(step[2:4], (2, 5))
        offset, count = data.PAGE_SPAN.unpack_from(raw, captures)
        self.assertEqual(count, 2)
        values = []
        for slot in range(count):
            text_offset, length = data.PAGE_SPAN.unpack_from(raw, offset + slot * 8)
            values.append(raw[text_offset:text_offset + length])
        self.assertEqual(values, [b"B.Self", b"A.Self"])
        offset, count = data.PAGE_SPAN.unpack_from(raw, header[5])
        self.assertEqual(count, 2)
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, offset), (91, 1, 1001))
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, offset + 16), (92, 1, 1002))
        self.assertEqual((self.callback_count(), self.client.request.call_count), (1, 1))
        self.process.read.assert_not_called()

    def test_native_source_alias_wins_over_native_fields_group_and_typed_prefix(self):
        # NativeField combination here proves only independent field/source wire flags,
        # not that a decree native field is valid on an actual GameRuleSetting row.
        fields = (data.NativeField(data.NativeFieldKind.DECREE_ID), "GameRuleSetting.GetKey")
        for grouped in (False, True):
            for typed in (False, True):
                with self.subTest(grouped=grouped, typed=typed):
                    options = {"steps": (data.CreationStep("Root.Rows(Root.Self)" if typed else "Root.Rows", 0,
                                                          source_arguments=("Root.Self",) if typed else ()),)}
                    if grouped:
                        options["sources"] = ("Owner.Settings", self.native)
                    raw = self.submit(fields, **options)
                    header = data.PAGE_REQUEST.unpack_from(raw)
                    self.assertEqual(header[18], 32 | 16 | 1 | (4 if grouped else 0) | (8 if typed else 0))
                    self.assertEqual(data.PAGE_FIELD.unpack_from(raw, header[3]), (2, 0, 0, 0))
                    self.assertEqual(header[5] - header[3], 2 * 16)
                    self.assertEqual(self.process.resolve_export.call_args.args[1],
                                     "PdxV3DataProjectOriginalSourcesInterface")
        self.assertEqual(self.callback_count(), 4)


    def test_native_source_requires_fresh_prefix_even_with_retained_root(self):
        roots = ((data.ObjectBinding(91, 1001, retained=True),),)
        for options in ({"steps": ()}, {"steps": (), "binding_rows": roots},
                        {"steps": (), "sources": ("Owner.Settings", self.native)}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "fresh CreationStep"):
                self.submit(**options)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_native_source_rejects_final_captures_but_not_prefix_captures(self):
        for options in ({"source_arguments": ("Root.Self",)},
                        {"source_arguments": ("Root.Self",), "sources": (self.native,)},
                        {"source_arguments": ("Root.Self",), "sources": ("Owner.Settings", self.native)}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.submit(**options)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_invalid_source_shapes_expressions_and_prefixes_fail_before_allocation(self):
        cases = [dict(sources=()), dict(sources=("", self.native)), dict(sources=("A.\0Rows", self.native)),
                 dict(sources=("\ud800", self.native)), dict(sources=(True, self.native)),
                 dict(sources=(b"A.Rows", self.native)), dict(source_expression=True),
                 dict(sources=(self.native,), source_expression=self.native),
                 dict(sources=(self.native,), source_index=True), dict(sources=(self.native,), source_index=2),
                 dict(steps=(self.native,)), dict(steps=(self.prefix,) * 9), dict(binding_rows=((), ())),
                 dict(sources=(self.native,), binding_rows=((), ()))]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.submit(**options)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_native_source_group_generator_is_bounded_at_32(self):
        raw = self.submit(sources=(self.native if index == 31 else "Owner.Settings" for index in range(32)))
        records, _ = self.grouped_table(raw)
        self.assertEqual(len(records), 32)
        self.assertEqual(records[-1], (1, 0, 0, 0))
        with self.assertRaisesRegex(ValueError, "32 sources"):
            self.submit(sources=(self.native for _ in range(33)))
        self.assertEqual(self.callback_count(), 1)

    def test_copied_cells_keep_bytes_errors_zero_false_empty_and_partial_without_receipt_gate(self):
        fields = ("GameRuleSetting.GetKey", "A.Zero", "A.False", "A.Empty", "A.Error", "A.Partial")
        self.submit(fields)
        text = b"setting_key\0\xff"
        self.returned([(0, 0, 0, data.Kind.STRING, 77, 0, text, None),
                       (0, 1, 0, data.Kind.U32, 78, 0, bytes(4), None),
                       (0, 2, 0, data.Kind.BOOL, 79, 0, b"\0", None),
                       (0, 3, 0, data.Kind.STRING, 77, 0, b"", None),
                       (0, 4, 1168, data.Kind.EMPTY, -1, 0, b"", None),
                       (0, 5, 234, data.Kind.STRING, 77, 0, b"ab", 8)], error=8, count_valid=0)
        page = self.query.read()
        self.assertEqual((page.error, page.count_valid, page.completed_steps), (8, False, 0))
        self.assertEqual(page.cells[0].snapshot, text)
        self.assertEqual([cell.value for cell in page.cells[:5]], [text.decode("utf-8", "replace"), 0, False, "", None])
        self.assertIs(page.cells[2].value, False)
        self.assertEqual([cell.error for cell in page.cells], [0, 0, 0, 0, 1168, 234])
        self.assertEqual((page.cells[5].snapshot, page.cells[5].total, page.cells[5].complete), (b"ab", 8, False))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            _ = page.cells[5].value
        self.assertEqual(page.value_handle, 0)
        self.assertIsNone(page.cells[0].binding_native_type_name)
        with self.assertRaisesRegex(RuntimeError, "no retained native value"):
            self.query.release_value()
        self.assertEqual(self.callback_count(), 1)

    def test_grouped_unknown_prefix_and_copied_observations_use_unchanged_decoder(self):
        self.submit(sources=(self.native, "Owner.Settings"))
        header, copied, _ = self.returned([(0, 0, 0, data.Kind.STRING, 77, 0, b"setting", None)], grouped=True)
        unknown = data.ProjectionPage.from_bytes(header, copied[:12])
        self.assertIsNone(unknown.next_cursor)
        self.assertIsNone(unknown.has_more)
        self.assertEqual(unknown.sources, ())
        page = self.query.read()
        self.assertEqual((page.next_cursor, page.has_more, page.range_complete), ((1, 0), False, True))
        self.assertEqual((page.sources[0].source_index, page.sources[0].row_count), (0, 1))
        self.assertEqual(page.cells[0].value, "setting")

    def test_interrupted_copy_keeps_original_query_and_resumes_without_getter_replay(self):
        self.submit()
        text = b"s" * 150000
        _, _, read = self.returned([(0, 0, 0, data.Kind.STRING, 77, 0, text, None)])
        failure = OSError("owned native-source bytes interrupted")
        failed = False
        def interrupted(address, count):
            nonlocal failed
            if address == 0x3000 + 65536 and not failed:
                failed = True
                raise failure
            return read(address, count)
        self.process.read.side_effect = interrupted
        with self.assertRaises(OSError) as caught:
            self.query.read()
        self.assertIs(caught.exception, failure)
        self.assertIs(caught.exception.query, self.query)
        self.assertFalse(self.query.partial_result.cells[0].complete)
        self.process.free.assert_not_called()
        self.assertEqual(self.query.read().cells[0].snapshot, text)
        self.assertEqual(sum(call.args[0] == 0x3000 for call in self.process.read.call_args_list), 1)
        self.assertEqual(self.callback_count(), 1)

    def test_pending_keeps_original_callback_storage_without_replay_or_free(self):
        self.submit()
        self.client.request.side_effect = lambda _: {"status": "ok", "completed_request_id": 6}
        self.assertIsNone(self.query.read())
        with self.assertRaisesRegex(RuntimeError, "pending"):
            self.query.close()
        self.process.free.assert_not_called()
        self.assertEqual(self.callback_count(), 1)

    def test_unknown_delivery_attaches_same_exception_query_and_recovers_without_replay(self):
        failure = TimeoutError("unknown original-source delivery")
        self.client.request.side_effect = failure
        with self.assertRaises(TimeoutError) as caught:
            self.submit()
        self.assertIs(caught.exception, failure)
        self.query = caught.exception.query
        self.process.free.assert_not_called()
        self.query.recover(7)
        self.client.request.side_effect = lambda _: {"status": "ok", "completed_request_id": 7}
        self.returned([(0, 0, 0, data.Kind.STRING, 77, 0, b"same owned result", None)])
        self.assertEqual(self.query.read().cells[0].value, "same owned result")
        self.assertEqual(self.callback_count(), 1)

    def test_cleanup_warning_does_not_hide_completed_native_source_result(self):
        self.submit()
        self.returned([(0, 0, 0, data.Kind.STRING, 77, 0, b"setting", None)])
        failure = OSError("owned free failed")
        self.process.free.side_effect = [failure, None, None]
        page = self.query.read()
        self.assertEqual(page.cells[0].value, "setting")
        self.assertIs(self.query.cleanup_error, failure)
        reads = self.process.read.call_count
        self.assertIs(self.query.read(), page)
        self.assertIsNone(self.query.cleanup_error)
        self.assertEqual(self.process.read.call_count, reads)
        self.assertEqual(self.callback_count(), 1)

    def test_missing_original_sources_alias_never_falls_back_allocates_or_submits(self):
        failure = LookupError("original sources capability absent")
        self.process.resolve_export.side_effect = failure
        with self.assertRaises(LookupError) as caught:
            self.submit()
        self.assertIs(caught.exception, failure)
        self.process.resolve_export.assert_called_once()
        self.assertEqual(self.process.resolve_export.call_args.args[1], "PdxV3DataProjectOriginalSourcesInterface")
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
