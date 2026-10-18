"""Full Company source wire and existing copied-query CPU fixtures.
These mocks do not prove a native provider, VM field, real binding ID or runtime
acceptance. Required source count and actual copied/error positions stay distinct.
SPDX-License-Identifier: MPL-2.0
"""
import unittest
from unittest.mock import Mock

from pdx_runtime import v3_data as data
from pdx_runtime.v3 import QueryExecutionError


COMPANY_SINGLE_WIRE = "a8000000e503000000000000000000007000000001000000780000000100000092000000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000200000008000000000000000000000008000000000000000000000008000000012000000a800000000000000436f6d70616e79547970652e4765744b657902000000000000000000000000000000000000000000"
OLD_ORDINARY_WIRE = "70000000e50300000000000000000000580000000100000060000000010000006f0000000000000000000000400000000000000000000000000000000000000000000000000000000000000000000000000000000000000068000000070000007000000000000000412e56616c756500"


class CompanySourceValueTests(unittest.TestCase):

    def test_raw_int2_bool_and_unknown_kinds_are_not_silently_converted(self):
        for kind in (2, True, False, 0, -1, 3, 2.0, "FULL_COMPANY_TYPES", None, data.NativeFieldKind.DECREE_ID):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                data.NativeSource(kind)


class CompanySourcePageTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=1)
        self.status = {"status": "ok", "request_id": 7, "completed_request_id": 7}
        self.client.request.side_effect = lambda command: (
            {"status": "ok", "request_id": 7, "error": 0}
            if command.startswith("interface-callback ") else self.status)
        self.process = Mock(pid=1)
        self.process.resolve_export.return_value = 0xA100
        self.process.export_owner.return_value = 0xA000
        self.process.allocate.return_value = 0x2000
        self.company = data.NativeSource(data.NativeSourceKind.FULL_COMPANY_TYPES)
        self.query = None

    def submit_full(self, fields=("CompanyType.GetKey",), **kwargs):
        # This test-only helper supplies the required explicit public limit=0.
        kwargs.setdefault("limit", 0)
        if "sources" not in kwargs:
            kwargs.setdefault("source_expression", self.company)
        self.query = data.project_page(self.client, fields, process=self.process, **kwargs)
        return self.process.write.call_args.args[1]

    def callbacks(self):
        return sum(call.args[0].startswith("interface-callback ") for call in self.client.request.call_args_list)

    def returned(self, cells, *, source_count, count_valid=1, error=0, row_count=None):
        raw = bytearray(self.process.write.call_args.args[1])
        header = list(data.PAGE_REQUEST.unpack_from(raw))
        table, payload = bytearray(), bytearray()
        base = len(cells) * data.PAGE_CELL.size
        for row, field, cell_error, kind, bits, text, total in cells:
            table.extend(data.PAGE_CELL.pack(row, field, cell_error, kind, 77,
                0xffffffff, bits, base + len(payload), len(text), len(text) if total is None else total,
                0xffffffff, 0, 0, 0))
            payload.extend(text)
        copied = bytes(table + payload)
        header[1], header[11], header[12], header[13] = error, 91, source_count, count_valid
        header[14] = len(cells) // header[4] if row_count is None else row_count
        header[15], header[16], header[17] = 0x3000 if copied else 0, len(copied), len(cells)
        data.PAGE_REQUEST.pack_into(raw, 0, *header)
        def read(address, count):
            if address == 0x2000:
                return bytes(raw)
            offset = address - 0x3000
            return copied[offset:offset + count]
        self.process.read.side_effect = read
        return bytes(raw), copied, read

    def key_cell(self, row, text, *, error=0, total=None):
        return (row, 0, error, data.Kind.STRING, 0, text, total)

    def test_explicit_all_n_wire_is_exact_source2_zero_window_and_scope24(self):
        raw = self.submit_full(limit=0, start=0)
        self.assertEqual(raw, bytes.fromhex(COMPANY_SINGLE_WIRE))
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((header[3], header[4], header[6], header[8], header[9], header[10], header[18]),
                         (112, 1, 1, 16, 0, 0, 32))
        self.assertEqual(data.PAGE_SOURCE.unpack_from(raw, header[7]), (2, 0, 0, 0))
        self.assertEqual(data.PAGE_SCOPED_SUFFIX.unpack_from(raw, 88), (128, 0, 0, 128, 0, 0))
        offset, length = data.PAGE_SPAN.unpack_from(raw, header[3])
        self.assertEqual(raw[offset:offset + length], b"CompanyType.GetKey")
        self.assertNotIn(b"FULL_COMPANY_TYPES", raw)
        self.assertEqual(self.process.resolve_export.call_args.args,
                         ("pdx_v3_data.dll", "PdxV3DataProjectCompanyTypesInterface"))
        self.assertEqual((self.callbacks(), self.client.request.call_count), (1, 1))

    def test_omitted_and_explicit_legacy64_are_rejected_not_mapped_to_unlimited(self):
        for options in ({}, {"limit": 64}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "explicit limit=0"):
                data.project_page(self.client, ("CompanyType.GetKey",), source_expression=self.company,
                                  process=self.process, **options)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_other_windows_and_bool_are_clear_implementation_limit_errors(self):
        cases = [dict(limit=value) for value in (-1, 1, 63, 65, 128, False, True, 0.0, "0", None)]
        cases += [dict(start=value) for value in (-1, 1, 64, False, True, 0.0, "0", None)]
        for options in cases:
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "FULL_COMPANY_TYPES currently requires"):
                self.submit_full(**options)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_prefix_group_and_final_arguments_are_not_implemented_in_first_capability(self):
        cases = [dict(steps=(data.CreationStep("Owner.Rows", 0),)),
                 dict(steps=(data.CreationStep("Owner.Rows(Owner.Self)", 0, source_arguments=("Owner.Self",)),)),
                 dict(source_arguments=("Owner.Self",)), dict(sources=(self.company,)),
                 dict(sources=("Owner.Rows", self.company)),
                 dict(sources=(data.NativeSource(data.NativeSourceKind.PRESET_STORED_SETTINGS), self.company),
                      steps=(data.CreationStep("GetGameRules.GetRulePresets", 0),))]
        for options in cases:
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "not implemented"):
                self.submit_full(**options)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_one_empty_or_original_typed_root_row_keeps_binding_bytes(self):
        roots = ((data.ObjectBinding(91, 1001, retained=True), data.ObjectBinding.state(92, 0x81000043)),)
        raw = self.submit_full(binding_rows=roots)
        header = data.PAGE_REQUEST.unpack_from(raw)
        offset, count = data.PAGE_SPAN.unpack_from(raw, header[5])
        self.assertEqual((header[6], count, offset % 8), (1, 2, 0))
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, offset), (91, 1, 1001))
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, offset + 16), (92, 3, 0x81000043))
        self.assertEqual(self.callbacks(), 1)  # mock IDs prove wire only, not live binding validity

    def test_field_count_and_single_root_row_limits_still_validate_before_allocation(self):
        for options in (dict(fields=()), dict(fields=("CompanyType.GetKey",) * 33),
                        dict(binding_rows=()), dict(binding_rows=((), ()))):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.submit_full(**options)
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_native_field_format_is_independent_but_company_capability_wins(self):
        # Decree NativeField compatibility with CompanyType is NOT claimed; this is a wire combination fixture.
        fields = ("CompanyType.GetKey", data.NativeField(data.NativeFieldKind.DECREE_ID))
        raw = self.submit_full(fields)
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual(header[18], 32 | 16)
        self.assertEqual(header[5] - header[3], 2 * 16)
        self.assertEqual(data.PAGE_FIELD.unpack_from(raw, header[3] + 16), (2, 0, 0, 0))
        self.assertEqual(data.PAGE_SOURCE.unpack_from(raw, header[7]), (2, 0, 0, 0))
        self.assertEqual(self.process.resolve_export.call_args.args[1], "PdxV3DataProjectCompanyTypesInterface")

    def test_ordinary_and_preset_budgets_keep_old_default64_and_reject_zero(self):
        for source, steps in (("Owner.Rows", ()),
                              (data.NativeSource(data.NativeSourceKind.PRESET_STORED_SETTINGS),
                               (data.CreationStep("GetGameRules.GetRulePresets", 0),))):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "1..64"):
                    self.submit_full(source_expression=source, steps=steps, limit=0)
                raw = self.submit_full(source_expression=source, steps=steps, limit=64)
                self.assertEqual(data.PAGE_REQUEST.unpack_from(raw)[10], 64)
        with self.assertRaisesRegex(ValueError, "fresh CreationStep"):
            self.submit_full(source_expression=data.NativeSource(data.NativeSourceKind.PRESET_STORED_SETTINGS), limit=64)
        raw = data._page_request(("A.Value",), ((),), source_expression=None, start=0, limit=64)
        self.assertEqual(raw, bytes.fromhex(OLD_ORDINARY_WIRE))

    def test_all_native_n_above64_are_copied_without_budget_clip(self):
        self.submit_full()
        count = 130
        keys = [b"" if row == 2 else ("company_" + str(row)).encode() for row in range(count)]
        self.returned([self.key_cell(row, key) for row, key in enumerate(keys)], source_count=count)
        page = self.query.read()
        self.assertEqual((page.source_count, page.count_valid, page.row_count, len(page.rows)), (count, True, count, count))
        self.assertEqual([cell.snapshot for cell in page.cells], keys)
        self.assertEqual([cell.row for cell in page.cells], list(range(count)))
        self.assertEqual((page.next, page.has_more, page.value_handle), (count, False, 0))
        self.assertEqual(page.world, 0)  # actual header observation is not replaced by a host world
        self.assertTrue(all(cell.complete for cell in page.cells))
        self.assertEqual(self.callbacks(), 1)

    def test_real_valid_empty_count_is_distinct_from_missing_provider(self):
        self.submit_full()
        self.returned([], source_count=0, count_valid=1)
        page = self.query.read()
        self.assertEqual((page.error, page.source_count, page.count_valid, page.row_count, page.cells), (0, 0, True, 0, ()))
        self.assertFalse(page.has_more)
        self.process.free.assert_called_once_with(0x2000)

    def test_null_provider_error_is_not_converted_to_valid_empty_catalog(self):
        self.submit_full()
        self.returned([], source_count=0, count_valid=0, error=1168)
        page = self.query.read()
        self.assertEqual((page.error, page.count_valid, page.source_count, page.cells), (1168, False, 0, ()))
        self.assertIs(self.query.read(), page)
        self.assertEqual(self.callbacks(), 1)

    def test_invalid_observed_count_bits_are_preserved_without_allocation_or_index_loop(self):
        self.submit_full()
        self.returned([], source_count=0xffffffff, count_valid=0, error=87)
        page = self.query.read()
        self.assertEqual((page.error, page.source_count, page.count_valid, page.row_count), (87, 0xffffffff, False, 0))
        self.assertEqual(page.cells, ())
        self.assertEqual([call.args[0] for call in self.process.free.call_args_list], [0x2000])

    def test_null_bad_and_key_error_positions_are_not_filtered_from_original_rows(self):
        self.submit_full()
        cells = [self.key_cell(0, b"first"), (1, 0, 1168, data.Kind.EMPTY, 0, b"", 0),
                 self.key_cell(2, b""), (3, 0, 13, data.Kind.EMPTY, 0, b"", 0),
                 self.key_cell(4, b"kept", error=234, total=12)]
        self.returned(cells, source_count=5, error=13)
        page = self.query.read()
        self.assertEqual([cell.row for cell in page.cells], [0, 1, 2, 3, 4])
        self.assertEqual([cell.error for cell in page.cells], [0, 1168, 0, 13, 234])
        self.assertEqual([cell.value for cell in page.cells[:4]], ["first", None, "", None])
        self.assertEqual((page.row_count, page.source_count, page.has_more), (5, 5, False))
        self.assertEqual(page.error, 13)  # terminal enumeration is not a successful/completed key claim
        self.assertFalse(page.cells[-1].complete)

    def test_required_n_and_total_remain_distinct_from_actual_native_prefix(self):
        self.submit_full()
        self.returned([self.key_cell(0, b"one"), self.key_cell(1, b"two"),
                       self.key_cell(2, b"ab", error=234, total=100)], source_count=130, error=8)
        page = self.query.read()
        self.assertEqual((page.source_count, page.row_count, len(page.cells), page.error), (130, 3, 3, 8))
        self.assertEqual((page.cells[2].snapshot, page.cells[2].total, page.cells[2].complete), (b"ab", 100, False))
        self.assertTrue(page.has_more)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            _ = page.cells[2].value
        with self.assertRaisesRegex(ValueError, "FULL_COMPANY_TYPES currently requires"):
            self.submit_full(start=page.next)
        self.assertEqual(self.callbacks(), 1)

    def test_failed_output_allocation_does_not_fabricate_available_prefix_bytes(self):
        self.submit_full()
        self.returned([], source_count=130, count_valid=1, error=8, row_count=0)
        page = self.query.read()
        self.assertEqual((page.error, page.source_count, page.count_valid, page.row_count, page.cells), (8, 130, True, 0, ()))
        self.assertEqual(self.query.copied_address, 0)
        self.process.free.assert_called_once_with(0x2000)

    def test_short_read_interruption_resumes_same_buffer_without_native_getter_replay(self):
        self.submit_full()
        count = 130
        cells = [self.key_cell(row, bytes([65 + row % 26]) * 1024) for row in range(count)]
        _, copied, read = self.returned(cells, source_count=count)
        failure = OSError("same copied output interrupted")
        failed = False
        def short_then_fail(address, length):
            nonlocal failed
            if address == 0x3000:
                return copied[:13000]  # legitimate positive short read
            if address == 0x3000 + 13000 and not failed:
                failed = True
                raise failure
            return read(address, length)
        self.process.read.side_effect = short_then_fail
        with self.assertRaises(OSError) as caught:
            self.query.read()
        self.assertIs(caught.exception, failure)
        self.assertIs(caught.exception.query, self.query)
        partial = self.query.partial_result
        self.assertEqual((partial.source_count, partial.row_count, len(partial.cells)), (count, count, count))
        self.assertFalse(all(cell.complete for cell in partial.cells))
        self.assertEqual(self.query.copied_address, 0x3000)
        self.process.free.assert_not_called()
        page = self.query.read()
        self.assertEqual([cell.snapshot for cell in page.cells], [cell[5] for cell in cells])
        self.assertTrue(all(cell.complete for cell in page.cells))
        self.assertEqual(sum(call.args[0] == 0x3000 for call in self.process.read.call_args_list), 1)
        self.assertEqual((self.callbacks(), self.process.allocate.call_count), (1, 1))
        self.assertEqual([call.args[0] for call in self.process.free.call_args_list], [0x3000, 0x2000])

    def test_cleanup_error_keeps_primary_keys_and_retries_cleanup_only(self):
        self.submit_full()
        self.returned([self.key_cell(0, b"company")], source_count=1)
        failure = OSError("copied output free failed")
        self.process.free.side_effect = [failure, None, None]
        page = self.query.read()
        self.assertEqual(page.cells[0].value, "company")
        self.assertIs(self.query.cleanup_error, failure)
        reads = self.process.read.call_count
        self.assertIs(self.query.read(), page)
        self.assertIsNone(self.query.cleanup_error)
        self.assertEqual(self.process.read.call_count, reads)
        self.assertEqual(self.callbacks(), 1)

    def test_pending_retains_original_request_without_read_free_or_replay(self):
        self.submit_full()
        self.status = {"status": "ok", "request_id": 7, "completed_request_id": 6}
        self.assertIsNone(self.query.read())
        with self.assertRaisesRegex(RuntimeError, "pending"):
            self.query.close()
        self.process.read.assert_not_called()
        self.process.free.assert_not_called()
        self.assertEqual(self.callbacks(), 1)

    def test_unknown_delivery_recovers_exact_original_query_without_replay(self):
        failure = TimeoutError("unknown company callback delivery")
        self.client.request.side_effect = failure
        with self.assertRaises(TimeoutError) as caught:
            self.submit_full()
        self.assertIs(caught.exception, failure)
        self.query = caught.exception.query
        self.process.free.assert_not_called()
        self.query.recover(7)
        self.client.request.side_effect = lambda _: self.status
        self.returned([self.key_cell(0, b"original")], source_count=1)
        self.assertEqual(self.query.read().cells[0].value, "original")
        self.assertEqual(self.callbacks(), 1)

    def test_required_company_export_is_not_replaced_by_original_sources_alias(self):
        failure = LookupError("CompanyTypes capability absent")
        self.process.resolve_export.side_effect = failure
        with self.assertRaises(LookupError) as caught:
            self.submit_full()
        self.assertIs(caught.exception, failure)
        self.process.resolve_export.assert_called_once()
        self.assertEqual(self.process.resolve_export.call_args.args[1], "PdxV3DataProjectCompanyTypesInterface")
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_old_native_error87_is_exposed_without_fallback_or_empty_success(self):
        self.submit_full()
        self.returned([], source_count=0, count_valid=0, error=87)
        page = self.query.read()
        self.assertEqual((page.error, page.count_valid, page.cells), (87, False, ()))
        self.assertIs(self.query.read(), page)
        self.assertEqual((self.callbacks(), self.process.resolve_export.call_count), (1, 1))

    def test_completed_callback574_keeps_distinct_original_pending_header_and_partial_query(self):
        self.submit_full()
        self.status = {"status": "ok", "request_id": 7, "completed_request_id": 7, "world_error": 574}
        self.returned([self.key_cell(0, b"prefix", error=234, total=20)], source_count=130,
                      error=data.ERROR_IO_PENDING)
        with self.assertRaises(QueryExecutionError) as caught:
            self.query.read()
        original_error = caught.exception
        self.assertEqual(original_error.winerror, 574)
        self.assertIs(original_error.result, self.query.partial_result)
        self.assertEqual(original_error.result.error, data.ERROR_IO_PENDING)
        self.assertEqual(original_error.result.cells[0].snapshot, b"prefix")
        self.assertEqual(self.query.completion_status["callback_error"], 574)
        with self.assertRaises(QueryExecutionError) as repeated:
            self.query.read()
        self.assertIs(repeated.exception, original_error)
        self.assertEqual(self.callbacks(), 1)

    def test_completed_callback574_does_not_replace_real_page_error13_or_prefix(self):
        self.submit_full()
        self.status = {"status": "ok", "request_id": 7, "completed_request_id": 7, "world_error": 574}
        self.returned([self.key_cell(0, b"kept")], source_count=130, error=13)
        page = self.query.read()
        self.assertEqual((page.error, page.cells[0].snapshot), (13, b"kept"))
        self.assertEqual(self.query.completion_status["callback_error"], 574)
        self.assertEqual(self.callbacks(), 1)

    def test_later_callback574_is_not_attributed_to_this_queries_opaque997(self):
        self.submit_full()
        self.status = {"status": "ok", "request_id": 8, "completed_request_id": 8, "world_error": 574}
        self.returned([], source_count=0, count_valid=0, error=data.ERROR_IO_PENDING)
        with self.assertRaises(QueryExecutionError) as caught:
            self.query.read()
        self.assertEqual(caught.exception.winerror, 0)  # unknown historical callback error, not fabricated574
        self.assertEqual(caught.exception.result.error, data.ERROR_IO_PENDING)
        self.assertEqual(self.query.completion_status["callback_error"], 574)
        self.assertEqual(self.callbacks(), 1)


if __name__ == "__main__":
    unittest.main()
