"""Native-field wire and copied-page CPU tests. No game or native calls.
SPDX-License-Identifier: MPL-2.0
"""
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import Mock

from pdx_runtime import v3_data as data
from pdx_runtime.native_bridge import BridgeError


DIRECT_NATIVE_WIRE = "98000000e5030000000000000000000070000000010000008000000001000000880000000e00000000000000400000000000000000000000000000000000000000000000000000000000000000000000000000001000000088000000000000000000000088000000000000000000000001000000000000000000000000000000980000000000000047657444656372656554797065730000"


class NativeFieldValueTests(unittest.TestCase):
    def test_kind_and_frozen_default_receiver(self):
        self.assertEqual(int(data.NativeFieldKind.DECREE_TYPE_KEY), 1)
        self.assertEqual(int(data.NativeFieldKind.DECREE_ID), 2)
        value = data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY)
        self.assertEqual(value.receiver_expression, "")
        self.assertIs(value.kind, data.NativeFieldKind.DECREE_TYPE_KEY)
        for name, replacement in (("kind", 0), ("receiver_expression", "Decree.GetType")):
            with self.subTest(name=name), self.assertRaises(FrozenInstanceError):
                setattr(value, name, replacement)

    def test_explicit_registered_receiver_is_preserved_without_rewriting(self):
        for expression in ("", "Decree.GetType", "Decree.GetType.GetName", "Decree.本来の受信者"):
            with self.subTest(expression=expression):
                value = data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY, expression)
                self.assertEqual(value.receiver_expression, expression)

    def test_bad_kind_and_bool_are_rejected(self):
        for kind in (0, 1, 2, -1, True, False, 1.0, "1", None, data.Kind.EMPTY):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                data.NativeField(kind)

    def test_non_string_receiver_is_rejected_even_when_empty(self):
        for receiver in (b"", b"Decree.GetType", bytearray(), None, 0, False, []):
            with self.subTest(receiver=receiver), self.assertRaises(ValueError):
                data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY, receiver)

    def test_nul_and_non_utf8_receiver_are_rejected(self):
        for receiver in ("\0", "Decree.Get\0Type", "\ud800", "Decree.\udfff"):
            with self.subTest(receiver=receiver), self.assertRaises(ValueError):
                data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY, receiver)

    def test_receiver_utf8_length_must_fit_signed32_without_huge_allocation(self):
        class OversizedBytes(bytes):
            def __len__(self):
                return 0x80000000
        class OversizedExpression(str):
            def encode(self, *args, **kwargs):
                return OversizedBytes(b"receiver")
        with self.assertRaisesRegex(ValueError, "signed32"):
            data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY, OversizedExpression("receiver"))


class NativeFieldPageTests(unittest.TestCase):
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
        self.query = None
        self.direct = data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY)
        self.active = data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY, "Decree.GetType")

    def submit(self, fields=None, **kwargs):
        if fields is None:
            fields = (self.direct,)
        if "sources" not in kwargs:
            kwargs.setdefault("source_expression", "GetDecreeTypes")
        self.query = data.project_page(self.client, fields, process=self.process, **kwargs)
        return self.process.write.call_args.args[1]

    def callback_count(self):
        return sum(call.args[0].startswith("interface-callback ")
                   for call in self.client.request.call_args_list)

    def returned(self, cells, *, error=0, source_count=1, count_valid=1, grouped=False):
        raw = bytearray(self.process.write.call_args.args[1])
        header = list(data.PAGE_REQUEST.unpack_from(raw))
        prefix = b""
        if grouped:
            size = data.PAGE_GROUP_OUTPUT.size + data.SOURCE_OBSERVATION.size
            prefix = (data.PAGE_GROUP_OUTPUT.pack(size, 1, 0, 1, 0, 1)
                      + data.SOURCE_OBSERVATION.pack(0, 0, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0))
        table, payload = bytearray(), bytearray()
        base = len(prefix) + len(cells) * data.PAGE_CELL.size
        for row, field, cell_error, kind, type_id, bits, text, total in cells:
            table.extend(data.PAGE_CELL.pack(row, field, cell_error, kind, type_id,
                2, bits, base + len(payload), len(text), len(text) if total is None else total,
                data.Kind.OBJECT, 0, 0, 0))
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

    def native_table(self, raw):
        header = data.PAGE_REQUEST.unpack_from(raw)
        return [data.PAGE_FIELD.unpack_from(raw, header[3] + index * data.PAGE_FIELD.size)
                for index in range(header[4])]

    def test_native_only_wire_is_exact_and_has_scope24_without_steps(self):
        raw = self.submit()
        self.assertEqual(raw, bytes.fromhex(DIRECT_NATIVE_WIRE))
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((data.PAGE_REQUEST.size, data.PAGE_FIELD.size, data.PAGE_SCOPED_SUFFIX.size),
                         (88, 16, 24))
        self.assertEqual((header[3], header[5], header[18]), (112, 128, 16))
        scope = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, 88)
        self.assertEqual(scope, (136, 0, 0, 136, 0, 0))
        self.assertEqual(self.native_table(raw), [(1, 0, 0, 0)])
        self.assertEqual(self.native_table(raw)[0][1:3], (0, 0))  # canonical direct receiver span
        self.assertEqual(raw[header[7]:header[7] + header[8]], b"GetDecreeTypes")
        self.assertEqual(self.process.resolve_export.call_args.args,
                         ("pdx_v3_data.dll", "PdxV3DataProjectNativeFieldsInterface"))

    def test_mixed_registered_direct_and_actual_gettype_receiver_use_field16(self):
        fields = ("Decree.GetName", self.direct, self.active, "'literal text'")
        raw = self.submit(fields, source_expression="Country.GetDecrees")
        header = data.PAGE_REQUEST.unpack_from(raw)
        table = self.native_table(raw)
        self.assertEqual((header[4], header[5], header[18]), (4, 112 + 4 * 16, 16))
        self.assertEqual([record[0] for record in table], [0, 1, 1, 0])
        self.assertEqual([record[3] for record in table], [0, 0, 0, 0])
        self.assertEqual([raw[offset:offset + length] for _, offset, length, _ in table],
                         [b"Decree.GetName", b"", b"Decree.GetType", b"'literal text'"])
        self.assertEqual(table[1][1:3], (0, 0))  # canonical direct receiver span in a mixed table
        self.assertGreater(table[2][1], 0)  # actual GetType receiver still has its original text span
        self.assertNotIn(b"Self", raw)
        self.assertNotIn(b"GetKey", raw)
        self.assertEqual((self.callback_count(), self.client.request.call_count), (1, 1))

    def test_unicode_receiver_byte_length_and_text_are_preserved(self):
        expression = "Decree.本来の受信者"
        raw = self.submit((data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY, expression),))
        kind, offset, length, reserved = self.native_table(raw)[0]
        self.assertEqual((kind, length, reserved), (1, len(expression.encode("utf-8")), 0))
        self.assertEqual(raw[offset:offset + length], expression.encode("utf-8"))

    def test_mixed_generator_is_bounded_at_32_fields(self):
        raw = self.submit((self.direct if index % 2 else "Decree.GetName" for index in range(32)))
        self.assertEqual(data.PAGE_REQUEST.unpack_from(raw)[4], 32)
        self.assertEqual([kind for kind, _, _, _ in self.native_table(raw)], [0, 1] * 16)
        with self.assertRaisesRegex(ValueError, "32 fields"):
            self.submit((self.direct for _ in range(33)))
        self.assertEqual(self.callback_count(), 1)

    def test_native_alias_wins_for_all_page_group_and_capture_combinations(self):
        for grouped in (False, True):
            for typed in (False, True):
                for final_capture in (False, True):
                    if grouped and final_capture:
                        continue
                    with self.subTest(grouped=grouped, typed=typed, final_capture=final_capture):
                        step = data.CreationStep("Root.Rows(Root.Self)" if typed else "Root.Rows", 0,
                                                source_arguments=("Root.Self",) if typed else ())
                        options = dict(steps=(step,))
                        if grouped:
                            options["sources"] = ("Owner.Decrees", "Owner.OtherDecrees")
                        else:
                            options["source_expression"] = "Owner.Decrees(Root.Self)" if final_capture else "Owner.Decrees"
                            options["source_arguments"] = ("Root.Self",) if final_capture else ()
                        raw = self.submit(("Decree.GetName", self.active), **options)
                        header = data.PAGE_REQUEST.unpack_from(raw)
                        expected_flags = 16 | 1 | (4 if grouped else 0) | (8 if typed else 0) | (2 if final_capture else 0)
                        self.assertEqual(header[18], expected_flags)
                        self.assertEqual(header[3], 128 if grouped else 112)
                        self.assertEqual(self.process.resolve_export.call_args.args[1],
                                         "PdxV3DataProjectNativeFieldsInterface")
                        scope = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, 88)
                        self.assertEqual((scope[1], scope[4]), (1, int(final_capture)))
                        self.assertEqual(bool(scope[5]), typed)
                        if grouped:
                            _, count, source_index, reserved = data.PAGE_GROUP_INPUT.unpack_from(raw, 112)
                            self.assertEqual((count, source_index, reserved), (2, 0, 0))
        self.assertEqual(self.callback_count(), 6)

    def test_grouped_native_only_still_has_scope24_and_group_suffix(self):
        raw = self.submit(sources=("GetDecreeTypes", "GetOtherDecreeTypes"), source_index=1)
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((header[3], header[18]), (128, 16 | 4))
        scope = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, 88)
        self.assertEqual(scope[1:3], (0, 0))
        offset, count, cursor, reserved = data.PAGE_GROUP_INPUT.unpack_from(raw, 112)
        values = []
        for index in range(count):
            start, length = data.PAGE_SPAN.unpack_from(raw, offset + index * 8)
            values.append(raw[start:start + length])
        self.assertEqual((values, cursor, reserved), ([b"GetDecreeTypes", b"GetOtherDecreeTypes"], 1, 0))


    def test_native_keeps_step40_and_separate_final_and_prefix_slot_tables(self):
        step = data.CreationStep("Root.Rows(A.Self,B.Self)", 2, 5,
                                 source_arguments=("B.Self", "A.Self"))
        raw = self.submit(steps=(step,), source_expression="Owner.Decrees(C.Self,D.Self)",
                          source_arguments=("D.Self", "C.Self"))
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual(header[18], 16 | 1 | 2 | 8)
        steps_offset, depth, completed, final_offset, final_count, prefix_offset = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, 88)
        self.assertEqual((data.CREATION_STEP.size, depth, completed, final_count), (40, 1, 0, 2))
        record = data.CREATION_STEP.unpack_from(raw, steps_offset)
        self.assertEqual(record[2:], (2, 5, 0, 0, 0, 0, -1, 0xffffffff))
        self.assertEqual(raw[record[0]:record[0] + record[1]], b"Root.Rows(A.Self,B.Self)")
        prefix_slots, prefix_count = data.PAGE_SPAN.unpack_from(raw, prefix_offset)
        self.assertEqual(prefix_count, 2)
        for offset, expected in ((final_offset, [b"D.Self", b"C.Self"]),
                                 (prefix_slots, [b"B.Self", b"A.Self"])):
            actual = []
            for index in range(2):
                start, length = data.PAGE_SPAN.unpack_from(raw, offset + index * 8)
                actual.append(raw[start:start + length])
            self.assertEqual(actual, expected)
        self.assertEqual(self.native_table(raw)[0][0], 1)

    def test_fresh_source_can_keep_original_retained_root_bindings(self):
        roots = ((data.ObjectBinding(91, 1001, retained=True), data.ObjectBinding(92, 1002, retained=True)),)
        raw = self.submit(binding_rows=roots, source_expression="Country.GetDecrees")
        header = data.PAGE_REQUEST.unpack_from(raw)
        offset, count = data.PAGE_SPAN.unpack_from(raw, header[5])
        self.assertEqual((header[2], count, offset % 8), (0, 2, 0))
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, offset), (91, 1, 1001))
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, offset + 16), (92, 1, 1002))
        self.assertEqual(self.client.request.call_count, 1)
        self.process.read.assert_not_called()

    def test_native_fields_reject_plain_binding_and_retained_matrices(self):
        for roots in (((),), ((data.ObjectBinding(91, 1001, retained=True),),),
                      ((data.ObjectBinding.country(91, 0x81000043),),)):
            with self.subTest(roots=roots), self.assertRaisesRegex(ValueError, "fresh Creation"):
                self.submit(binding_rows=roots, source_expression=None)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_bad_sources_fail_before_capability_resolution_or_allocation(self):
        cases = [dict(source_expression=value) for value in ("", "A.\0Rows", "\ud800", b"Rows", False)]
        cases += [dict(sources=()), dict(sources=("",)), dict(sources=("A.\0Rows",)),
                  dict(sources=("\ud800",)), dict(sources=("A.Rows",), source_expression="B.Rows"),
                  dict(sources=("A.Rows",), source_index=True),
                  dict(source_expression="A.Rows", binding_rows=((), ())),
                  dict(sources=("A.Rows",), binding_rows=((), ()))]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.submit(**options)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_mixed_fields_keep_ordinary_validation_and_dimension_limits(self):
        cases = [(), (self.direct, ""), (self.direct, "A.\0Value"), (self.direct, "\ud800"),
                 (self.direct, b"A.Value"), (self.direct, None), (self.direct, True), (self.direct,) * 33]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.submit(values)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_unchanged_cells_keep_native_minus_one_and_ordinary_zero_false_empty_errors(self):
        self.submit((self.direct, "A.Zero", "A.False", "A.Empty", "A.Error", "A.Partial"))
        self.returned([(0, 0, 0, data.Kind.STRING, -1, 0, b"decree_key", None),
                       (0, 1, 0, data.Kind.U32, 77, 0, bytes(4), None),
                       (0, 2, 0, data.Kind.BOOL, 77, 0, b"\0", None),
                       (0, 3, 0, data.Kind.STRING, 77, 0, b"", None),
                       (0, 4, 1168, data.Kind.EMPTY, 77, 0, b"", None),
                       (0, 5, 234, data.Kind.STRING, 77, 0, b"ab", 8)])
        page = self.query.read()
        self.assertIsInstance(page.cells[0], data.ProjectionCell)
        self.assertEqual([cell.type_id for cell in page.cells], [-1, 77, 77, 77, 77, 77])
        self.assertEqual([cell.value for cell in page.cells[:5]], ["decree_key", 0, False, "", None])
        self.assertIs(page.cells[2].value, False)
        self.assertEqual([cell.error for cell in page.cells], [0, 0, 0, 0, 1168, 234])
        self.assertEqual((page.cells[5].snapshot, page.cells[5].total, page.cells[5].complete), (b"ab", 8, False))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            _ = page.cells[5].value
        self.assertEqual(page.value_handle, 0)
        self.assertEqual(page.rows, (page.cells,))
        with self.assertRaisesRegex(RuntimeError, "no retained native value"):
            self.query.release_value()
        self.assertEqual(self.callback_count(), 1)

    def test_native_bytes_and_cell_error_use_existing_decoder_without_type_fabrication(self):
        self.submit()
        text = b"native\0bytes\xff"
        self.returned([(0, 0, 13, data.Kind.STRING, -1, 0, text, None)], error=8, count_valid=0)
        page = self.query.read()
        cell = page.cells[0]
        self.assertEqual((page.error, page.count_valid, cell.error, cell.type_id), (8, False, 13, -1))
        self.assertEqual(cell.snapshot, text)
        self.assertEqual(cell.value, text.decode("utf-8", "replace"))
        self.assertEqual((cell.binding_mode, cell.binding_kind), (2, data.Kind.OBJECT))
        self.assertIsNone(cell.binding_native_type_name)
        self.assertIs(self.query.read(), page)

    def test_grouped_native_output_uses_unchanged_observation_decoder(self):
        self.submit(sources=("GetDecreeTypes",))
        self.returned([(0, 0, 0, data.Kind.STRING, -1, 0, b"key", None)], grouped=True)
        page = self.query.read()
        self.assertEqual((page.grouped, page.next_cursor, page.range_complete, page.has_more),
                         (True, (1, 0), True, False))
        self.assertEqual((page.sources[0].source_index, page.sources[0].row_count), (0, 1))
        self.assertEqual((page.cells[0].type_id, page.cells[0].value), (-1, "key"))

    def test_copy_failure_keeps_prefix_and_original_query_then_resumes_without_replay(self):
        self.submit()
        text = b"k" * 150000
        _, _, read = self.returned([(0, 0, 0, data.Kind.STRING, -1, 0, text, None)])
        failure = OSError("owned bytes interrupted")
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
        self.assertEqual(self.query.partial_result.cells[0].type_id, -1)
        self.assertGreater(len(self.query.partial_result.cells[0].snapshot), 0)
        self.assertFalse(self.query.partial_result.cells[0].complete)
        self.process.free.assert_not_called()
        page = self.query.read()
        self.assertEqual(page.cells[0].snapshot, text)
        self.assertEqual(self.callback_count(), 1)
        self.assertEqual(sum(call.args[0] == 0x3000 for call in self.process.read.call_args_list), 1)
        self.assertEqual([call.args[0] for call in self.process.free.call_args_list], [0x3000, 0x2000])

    def test_cleanup_warning_keeps_primary_result_and_retries_only_cleanup(self):
        self.submit()
        self.returned([(0, 0, 0, data.Kind.STRING, -1, 0, b"observed", None)])
        failure = OSError("free failed")
        self.process.free.side_effect = [failure, None, None]
        page = self.query.read()
        self.assertEqual(page.cells[0].value, "observed")
        self.assertIs(self.query.cleanup_error, failure)
        reads = self.process.read.call_count
        self.assertIs(self.query.read(), page)
        self.assertIsNone(self.query.cleanup_error)
        self.assertEqual(self.process.read.call_count, reads)
        self.assertEqual(self.callback_count(), 1)
        self.assertEqual([call.args[0] for call in self.process.free.call_args_list], [0x3000, 0x3000, 0x2000])

    def test_pending_keeps_original_request_without_replay_or_free(self):
        self.submit()
        self.client.request.side_effect = lambda _: {"status": "ok", "completed_request_id": 6}
        self.assertIsNone(self.query.read())
        with self.assertRaisesRegex(RuntimeError, "pending"):
            self.query.close()
        self.process.free.assert_not_called()
        self.assertEqual(self.callback_count(), 1)

    def test_unknown_delivery_preserves_original_exception_query_and_recovers_once(self):
        failure = TimeoutError("unknown delivery")
        self.client.request.side_effect = failure
        with self.assertRaises(TimeoutError) as caught:
            self.submit()
        self.assertIs(caught.exception, failure)
        self.query = caught.exception.query
        self.process.free.assert_not_called()
        self.query.recover(7)
        self.client.request.side_effect = lambda _: {"status": "ok", "completed_request_id": 7}
        self.returned([(0, 0, 0, data.Kind.STRING, -1, 0, b"same source", None)])
        self.assertEqual(self.query.read().cells[0].value, "same source")
        self.assertEqual(self.callback_count(), 1)

    def test_missing_native_alias_never_falls_back_allocates_or_submits(self):
        failure = LookupError("native fields export not found")
        self.process.resolve_export.side_effect = failure
        with self.assertRaises(LookupError) as caught:
            self.submit(sources=("Owner.Decrees",),
                        steps=(data.CreationStep("Root.Rows(Root.Self)", 0, source_arguments=("Root.Self",)),))
        self.assertIs(caught.exception, failure)
        self.process.resolve_export.assert_called_once()
        self.assertEqual(self.process.resolve_export.call_args.args,
                         ("pdx_v3_data.dll", "PdxV3DataProjectNativeFieldsInterface"))
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_decree_id_direct_field_is_kind2_with_canonical_empty_span(self):
        identity = data.NativeField(data.NativeFieldKind.DECREE_ID)
        self.assertEqual(identity.receiver_expression, "")
        raw = self.submit((identity,), source_expression="Country.GetDecrees")
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((header[3], header[4], header[18]), (112, 1, 16))
        self.assertEqual(self.native_table(raw), [(2, 0, 0, 0)])
        self.assertEqual(data.PAGE_SCOPED_SUFFIX.unpack_from(raw, 88)[1:3], (0, 0))
        self.assertEqual(raw[header[7]:header[7] + header[8]], b"Country.GetDecrees")
        self.assertEqual(self.process.resolve_export.call_args.args,
                         ("pdx_v3_data.dll", "PdxV3DataProjectNativeFieldsInterface"))
        self.assertEqual((self.callback_count(), self.client.request.call_count), (1, 1))

    def test_decree_id_rejects_nonempty_receiver_before_allocation(self):
        for receiver in ("Decree.GetType", "Decree.Self", " ", "\0", "Decree.\ud800"):
            with self.subTest(receiver=receiver), self.assertRaisesRegex(ValueError, "DECREE_ID requires an empty"):
                self.submit((data.NativeField(data.NativeFieldKind.DECREE_ID, receiver),),
                            source_expression="Country.GetDecrees")
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_decree_id_rejects_nonstring_receiver_before_allocation(self):
        for receiver in (b"", b"Decree.GetType", None, 0, False, []):
            with self.subTest(receiver=receiver), self.assertRaises(ValueError):
                self.submit((data.NativeField(data.NativeFieldKind.DECREE_ID, receiver),),
                            source_expression="Country.GetDecrees")
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_decree_id_and_active_key_copy_same_current_rows_in_one_page(self):
        identity = data.NativeField(data.NativeFieldKind.DECREE_ID)
        raw = self.submit((identity, self.active, "Decree.GetName"), source_expression="Country.GetDecrees")
        table = self.native_table(raw)
        self.assertEqual([value[0] for value in table], [2, 1, 0])
        self.assertEqual(table[0], (2, 0, 0, 0))
        self.assertEqual(raw[table[1][1]:table[1][1] + table[1][2]], b"Decree.GetType")
        self.assertEqual(raw[table[2][1]:table[2][1] + table[2][2]], b"Decree.GetName")
        self.assertEqual(self.client.request.call_count, 1)
        self.returned([(0, 0, 0, data.Kind.U32, -1, 0, bytes(4), 4),
                       (0, 1, 0, data.Kind.STRING, -1, 0, b"decree_zero", None),
                       (0, 2, 0, data.Kind.STRING, 77, 0, b"first", None),
                       (1, 0, 0, data.Kind.U32, -1, 0xffffffff, bytes([255]) * 4, 4),
                       (1, 1, 0, data.Kind.STRING, -1, 0, b"decree_uintmax", None),
                       (1, 2, 0, data.Kind.STRING, 77, 0, b"second", None)], source_count=2)
        page = self.query.read()
        self.assertEqual([[cell.value for cell in row] for row in page.rows],
                         [[0, "decree_zero", "first"], [0xffffffff, "decree_uintmax", "second"]])
        self.assertEqual([[cell.row for cell in row] for row in page.rows], [[0, 0, 0], [1, 1, 1]])
        self.assertEqual([[cell.type_id for cell in row] for row in page.rows], [[-1, -1, 77], [-1, -1, 77]])
        self.assertEqual(self.callback_count(), 1)
        self.assertEqual(page.value_handle, 0)

    def test_decree_id_u32_zero_high_bits_and_uintmax_are_not_masked(self):
        identity = data.NativeField(data.NativeFieldKind.DECREE_ID)
        for exact_id in (0, 1, 0x80000000, 0x81000043, 0xffffffff):
            with self.subTest(exact_id=exact_id):
                self.submit((identity,), source_expression="Country.GetDecrees")
                snapshot = exact_id.to_bytes(4, "little")
                self.returned([(0, 0, 0, data.Kind.U32, -1, exact_id, snapshot, 4)])
                cell = self.query.read().cells[0]
                self.assertIsInstance(cell, data.ProjectionCell)
                self.assertEqual((cell.kind, cell.type_id, cell.raw_bits, cell.total),
                                 (data.Kind.U32, -1, exact_id, 4))
                self.assertEqual(cell.snapshot, snapshot)
                self.assertEqual(cell.value, exact_id)
                self.assertTrue(cell.complete)
        self.assertEqual(self.callback_count(), 5)

    def test_decree_id_error_and_partial_bytes_keep_existing_cell_decoder(self):
        identity = data.NativeField(data.NativeFieldKind.DECREE_ID)
        self.submit((identity, identity), source_expression="Country.GetDecrees")
        self.returned([(0, 0, 234, data.Kind.U32, -1, 0xffffffff, b"\xff\xff", 4),
                       (0, 1, 1168, data.Kind.EMPTY, -1, 0, b"", 0)], error=8, count_valid=0)
        page = self.query.read()
        self.assertEqual((page.error, page.count_valid), (8, False))
        partial, failed = page.cells
        self.assertEqual((partial.error, partial.kind, partial.type_id, partial.raw_bits, partial.value),
                         (234, data.Kind.U32, -1, 0xffffffff, 0xffffffff))
        self.assertEqual((partial.snapshot, partial.total, partial.complete), (b"\xff\xff", 4, False))
        self.assertEqual((failed.error, failed.kind, failed.type_id, failed.value),
                         (1168, data.Kind.EMPTY, -1, None))
        self.assertEqual(self.callback_count(), 1)

    def test_decree_id_plain_retained_matrix_still_requires_fresh_source(self):
        roots = ((data.ObjectBinding(91, 1001, retained=True),),)
        with self.assertRaisesRegex(ValueError, "fresh Creation"):
            self.submit((data.NativeField(data.NativeFieldKind.DECREE_ID),),
                        source_expression=None, binding_rows=roots)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_decree_id_grouped_typed_prefix_keeps_existing_native_alias(self):
        raw = self.submit((data.NativeField(data.NativeFieldKind.DECREE_ID), self.active),
                          sources=("Owner.Decrees", "Owner.OtherDecrees"),
                          steps=(data.CreationStep("Root.Rows(Root.Self)", 0, source_arguments=("Root.Self",)),))
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((header[3], header[18]), (128, 16 | 1 | 4 | 8))
        self.assertEqual(self.native_table(raw)[0], (2, 0, 0, 0))
        self.assertEqual(self.process.resolve_export.call_args.args[1], "PdxV3DataProjectNativeFieldsInterface")
        self.assertEqual(self.callback_count(), 1)

    def test_rejected_submission_cleanup_keeps_original_error_and_query_owner(self):
        self.client.request.side_effect = lambda _: {"status": "error", "error": 5}
        failure = OSError("request free failed")
        self.process.free.side_effect = failure
        with self.assertRaises(BridgeError) as caught:
            self.submit()
        self.query = caught.exception.query
        self.assertIs(caught.exception, self.query.submission_error)
        self.assertEqual(caught.exception.winerror, 5)
        self.assertIs(self.query.cleanup_error, failure)
        self.assertEqual(self.query.result_address, 0x2000)
        self.process.free.side_effect = None
        self.query.close()
        self.assertEqual(self.process.free.call_count, 2)
        self.assertEqual(self.callback_count(), 1)


if __name__ == "__main__":
    unittest.main()
