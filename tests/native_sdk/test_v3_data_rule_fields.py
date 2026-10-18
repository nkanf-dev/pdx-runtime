"""Canonical Rule KEY3 wire and copied-query CPU tests, not receiver VM proof.

Source1/Company2/decree combinations below test independent wire formats only.
They do not claim semantic receiver compatibility. No game/native getter runs.
SPDX-License-Identifier: MPL-2.0
"""
import unittest
from unittest.mock import Mock

from pdx_runtime import v3_data as data
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3 import QueryExecutionError


RULES = "PdxV3DataProjectRuleFieldsInterface"
KEY = data.NativeFieldKind.GAME_RULE_KEY


class Once:
    def __init__(self, values):
        self.values = values
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations != 1:
            raise AssertionError("caller iterable consumed twice")
        yield from self.values


class RuleFieldValueTests(unittest.TestCase):


    def test_nonempty_receivers_have_exact_key3_error(self):
        for receiver in ("Fixture.Self", " ", "\0", "Fixture.\ud800", "名"):
            with self.subTest(receiver=repr(receiver)), self.assertRaises(ValueError) as caught:
                data.NativeField(KEY, receiver)
            self.assertEqual(str(caught.exception),
                             "GAME_RULE_KEY requires an empty receiver_expression")


    def test_raw_int3_and4_bool_and_foreign_enum_are_not_coerced(self):
        for kind in (3, 4, True, False, 3.0, "3", None,
                     data.NativeSourceKind.PRESET_STORED_SETTINGS, data.Kind.I32):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                data.NativeField(kind)


class RuleFieldPageTests(unittest.TestCase):
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
        self.key = data.NativeField(KEY)
        self.preset = data.NativeSource(data.NativeSourceKind.PRESET_STORED_SETTINGS)
        self.company = data.NativeSource(data.NativeSourceKind.FULL_COMPANY_TYPES)
        self.query = None

    def submit(self, fields=None, **kwargs):
        if fields is None:
            fields = (self.key,)
        if "sources" not in kwargs:
            kwargs.setdefault("source_expression", "Fixture.Rows")
        self.query = data.project_page(self.client, fields, process=self.process, **kwargs)
        return self.process.write.call_args.args[1]

    def callbacks(self):
        return sum(call.args[0].startswith("interface-callback ")
                   for call in self.client.request.call_args_list)

    def field_table(self, raw):
        header = data.PAGE_REQUEST.unpack_from(raw)
        return [data.PAGE_FIELD.unpack_from(raw, header[3] + index * data.PAGE_FIELD.size)
                for index in range(header[4])]

    def returned(self, cells, *, error=0, count_valid=1, source_count=1):
        raw = bytearray(self.process.write.call_args.args[1])
        header = list(data.PAGE_REQUEST.unpack_from(raw))
        table, payload = bytearray(), bytearray()
        base = len(cells) * data.PAGE_CELL.size
        for row, field, cell_error, kind, bits, text, total in cells:
            table.extend(data.PAGE_CELL.pack(row, field, cell_error, kind, -1,
                2, bits, base + len(payload), len(text), len(text) if total is None else total,
                data.Kind.OBJECT, 0, 0, 0))
            payload.extend(text)
        copied = bytes(table + payload)
        # Synthetic execution_thread_id, not a source/receiver type ID.
        header[1], header[11], header[12], header[13] = error, 0xffffffff, source_count, count_valid
        header[14] = max((cell[0] + 1 for cell in cells), default=0)
        header[15], header[16], header[17] = 0x3000 if copied else 0, len(copied), len(cells)
        data.PAGE_REQUEST.pack_into(raw, 0, *header)
        def read(address, count):
            if address == 0x2000:
                return bytes(raw)
            offset = address - 0x3000
            return copied[offset:offset + count]
        self.process.read.side_effect = read
        return bytes(raw), copied, read

    def test_single_key3_is_canonical_field16_and_uses_same_query(self):
        raw = self.submit(source_expression="GetGameRules.GetSettings")
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((header[3], header[4], header[5], header[18]), (112, 1, 128, 16))
        self.assertEqual(self.field_table(raw), [(3, 0, 0, 0)])
        self.assertEqual(data.PAGE_SCOPED_SUFFIX.unpack_from(raw, 88), (136, 0, 0, 136, 0, 0))
        self.assertEqual(raw[header[7]:header[7] + header[8]], b"GetGameRules.GetSettings")
        self.assertIs(type(self.query), data.ProjectionPageQuery)
        self.assertEqual(self.process.resolve_export.call_args.args, ("pdx_v3_data.dll", RULES))
        self.assertEqual((self.callbacks(), self.client.request.call_count), (1, 1))
        self.process.read.assert_not_called()

    def test_mixed_dsl_literal_and_old_fields_preserve_independent_field_bytes(self):
        # WIRE ONLY: no rule/decree receiver compatibility is claimed.
        literal = data.plain_cstring_literal("O'Brien\\名")
        old = data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY, "Decree.GetType")
        raw = self.submit(("Fixture.GetName", self.key, literal, old,
                           data.NativeField(data.NativeFieldKind.DECREE_ID)))
        table = self.field_table(raw)
        self.assertEqual([record[0] for record in table], [0, 3, 0, 1, 2])
        self.assertEqual([record[3] for record in table], [0] * 5)
        self.assertEqual([raw[offset:offset + length] for _, offset, length, _ in table],
                         [b"Fixture.GetName", b"", literal.encode("utf-8"), b"Decree.GetType", b""])
        self.assertEqual(table[1], (3, 0, 0, 0))
        self.assertEqual(table[4], (2, 0, 0, 0))
        self.assertEqual(self.process.resolve_export.call_args.args[1], RULES)
        self.assertNotIn(b"GameRule.GetKey", raw)
        self.assertNotIn(b"GAME_RULE_KEY", raw)
        self.assertNotIn(b".Self", raw)

    def test_key3_wins_at_first_middle_and_last_field_positions(self):
        for position in (0, 15, 31):
            with self.subTest(position=position):
                fields = ["Fixture.Value"] * 32
                fields[position] = self.key
                raw = self.submit(fields)
                self.assertEqual([field[0] for field in self.field_table(raw)].count(3), 1)
                self.assertEqual(self.field_table(raw)[position], (3, 0, 0, 0))
                self.assertEqual(self.process.resolve_export.call_args.args[1], RULES)
        self.assertEqual(self.callbacks(), 3)

    def test_marker_names_inside_plain_dsl_and_literals_do_not_select_rule_alias(self):
        raw = self.submit(("GAME_RULE_KEY", "'PdxV3DataProjectRuleFieldsInterface'"))
        self.assertEqual(data.PAGE_REQUEST.unpack_from(raw)[18], 0)
        self.assertEqual(self.process.resolve_export.call_args.args[1],
                         "PdxV3DataProjectPageInterface")

    def test_group_prefix_and_final_capture_combinations_keep_rule_alias_highest(self):
        count = 0
        for grouped in (False, True):
            for typed in (False, True):
                for final_capture in (False, True):
                    if grouped and final_capture:
                        continue
                    with self.subTest(grouped=grouped, typed=typed, final=final_capture):
                        step = data.CreationStep("Root.Rows(Root.Self)" if typed else "Root.Rows", 0,
                                                source_arguments=("Root.Self",) if typed else ())
                        options = {"steps": (step,)}
                        if grouped:
                            options["sources"] = ("Fixture.Rows", "Fixture.OtherRows")
                        else:
                            options["source_expression"] = "Fixture.Rows(Root.Self)" if final_capture else "Fixture.Rows"
                            options["source_arguments"] = ("Root.Self",) if final_capture else ()
                        raw = self.submit(("Fixture.Name", self.key), **options)
                        expected = 16 | 1 | (4 if grouped else 0) | (8 if typed else 0) | (2 if final_capture else 0)
                        self.assertEqual(data.PAGE_REQUEST.unpack_from(raw)[18], expected)
                        self.assertEqual(self.process.resolve_export.call_args.args[1], RULES)
                        count += 1
        self.assertEqual((count, self.callbacks()), (6, 6))

    def test_source1_and_mixed_group_source16_format_are_independent_of_key3(self):
        # WIRE ONLY: native Setting scope acceptance belongs to native proof.
        for grouped in (False, True):
            for typed in (False, True):
                with self.subTest(grouped=grouped, typed=typed):
                    step = data.CreationStep("Root.Rows(Root.Self)" if typed else "Root.Rows", 0,
                                            source_arguments=("Root.Self",) if typed else ())
                    options = {"steps": (step,)}
                    if grouped:
                        options["sources"] = ("Fixture.Rows", self.preset)
                    else:
                        options["source_expression"] = self.preset
                    raw = self.submit((self.key, "GameRuleSetting.GetName"), **options)
                    h = data.PAGE_REQUEST.unpack_from(raw)
                    self.assertEqual(h[18], 32 | 16 | 1 | (4 if grouped else 0) | (8 if typed else 0))
                    self.assertEqual(self.field_table(raw)[0], (3, 0, 0, 0))
                    if grouped:
                        offset, count, cursor, reserved = data.PAGE_GROUP_INPUT.unpack_from(raw, 112)
                        self.assertEqual((count, cursor, reserved), (2, 0, 0))
                        expression = data.PAGE_SOURCE.unpack_from(raw, offset)
                        self.assertEqual((expression[0], expression[3]), (0, 0))
                        self.assertEqual(raw[expression[1]:expression[1] + expression[2]], b"Fixture.Rows")
                        self.assertEqual(data.PAGE_SOURCE.unpack_from(raw, offset + 16), (1, 0, 0, 0))
                    else:
                        self.assertEqual(data.PAGE_SOURCE.unpack_from(raw, h[7]), (1, 0, 0, 0))
                    self.assertEqual(self.process.resolve_export.call_args.args[1], RULES)
        self.assertEqual(self.callbacks(), 4)

    def test_company2_format_keeps_explicit_unlimited_window_but_rule_marker_wins(self):
        # WIRE ONLY: a CompanyType row is not claimed to be a rule receiver.
        raw = self.submit(("CompanyType.GetKey", self.key), source_expression=self.company, start=0, limit=0)
        h = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((h[9], h[10], h[18]), (0, 0, 32 | 16))
        self.assertEqual(data.PAGE_SOURCE.unpack_from(raw, h[7]), (2, 0, 0, 0))
        self.assertEqual(self.field_table(raw)[1], (3, 0, 0, 0))
        self.assertEqual(self.process.resolve_export.call_args.args[1], RULES)

    def test_native_source_and_plain_source_budgets_and_limits_are_not_relaxed(self):
        cases = [dict(source_expression=self.company),
                 dict(source_expression=self.company, start=1, limit=0),
                 dict(source_expression=self.company, limit=0, steps=(data.CreationStep("Root.Rows", 0),)),
                 dict(sources=(self.company,), limit=0),
                 dict(source_expression=self.company, limit=0, source_arguments=("Root.Self",)),
                 dict(source_expression=self.preset),
                 dict(source_expression=self.preset, steps=(data.CreationStep("Root.Rows", 0),), limit=0),
                 dict(source_expression=self.preset, steps=(data.CreationStep("Root.Rows", 0),),
                      source_arguments=("Root.Self",)),
                 dict(limit=0), dict(limit=65), dict(start=True)]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.submit(**options)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()


    def test_real_generator_is_consumed_once_and_last_key3_is_still_detected(self):
        yielded = []
        def fields():
            for index in range(32):
                yielded.append(index)
                yield self.key if index == 31 else "Fixture.Name"
        raw = self.submit(fields())
        self.assertEqual(yielded, list(range(32)))
        self.assertEqual(self.field_table(raw)[31], (3, 0, 0, 0))
        self.assertEqual(self.process.resolve_export.call_args.args[1], RULES)

    def test_iterable_raising_on_second_iteration_is_not_revisited(self):
        fields = Once(("Fixture.Name", self.key))
        raw = self.submit(fields)
        self.assertEqual(fields.iterations, 1)
        self.assertEqual([record[0] for record in self.field_table(raw)], [0, 3])
        self.assertEqual(self.process.resolve_export.call_args.args[1], RULES)

    def test_other_one_shot_input_iterables_are_not_revisited(self):
        sources = Once(("Fixture.Rows", self.preset))
        steps = Once((data.CreationStep("Root.Rows", 0),))
        root = Once(())
        roots = Once((root,))
        arguments = Once(())
        self.submit(Once((self.key,)), binding_rows=roots, sources=sources,
                    steps=steps, source_arguments=arguments)
        self.assertEqual([item.iterations for item in (sources, steps, root, roots, arguments)], [1] * 5)
        self.assertEqual(self.process.resolve_export.call_args.args[1], RULES)

    def test_33_fields_fail_before_lookup_allocation_or_submission(self):
        fields = Once(self.key if index == 32 else "Fixture.Name" for index in range(33))
        with self.assertRaisesRegex(ValueError, "32 fields"):
            self.submit(fields)
        self.assertEqual(fields.iterations, 1)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_key3_still_requires_fresh_source_not_a_plain_binding_matrix(self):
        with self.assertRaisesRegex(ValueError, "fresh Creation"):
            self.submit(source_expression=None)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_exact_missing_rule_marker_never_falls_back_even_for_source1_or_company2(self):
        failure = LookupError("RuleFields export missing")
        self.process.resolve_export.side_effect = failure
        cases = [{}, {"source_expression": self.preset, "steps": (data.CreationStep("Root.Rows", 0),)},
                 {"source_expression": self.company, "limit": 0},
                 {"sources": ("Fixture.Rows", self.preset),
                  "steps": (data.CreationStep("Root.Rows(Root.Self)", 0, source_arguments=("Root.Self",)),)}]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(LookupError) as caught:
                self.submit(**options)
            self.assertIs(caught.exception, failure)
        self.assertEqual([call.args for call in self.process.resolve_export.call_args_list],
                         [("pdx_v3_data.dll", RULES)] * len(cases))
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_marker_access_error_is_the_original_exception_not_absence_or_fallback(self):
        failure = BridgeError("resolve_export", 5, reason="access denied")
        self.process.resolve_export.side_effect = failure
        with self.assertRaises(BridgeError) as caught:
            self.submit()
        self.assertIs(caught.exception, failure)
        self.process.resolve_export.assert_called_once()
        self.assertEqual(self.process.resolve_export.call_args.args, ("pdx_v3_data.dll", RULES))
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_actual_error87_empty_page_is_not_replaced_or_replayed(self):
        self.submit()
        self.returned([], error=87, count_valid=0, source_count=0)
        page = self.query.read()
        self.assertEqual((page.error, page.count_valid, page.cells), (87, False, ()))
        self.assertIs(self.query.read(), page)
        self.assertEqual((self.callbacks(), self.process.resolve_export.call_count), (1, 1))

    def test_error87_with_copied_partial_bytes_keeps_the_original_page(self):
        self.submit()
        self.returned([(0, 0, 234, data.Kind.STRING, 0, b"prefix", 40)], error=87, count_valid=0)
        page = self.query.read()
        cell = page.cells[0]
        self.assertEqual((page.error, cell.error, cell.type_id, cell.snapshot, cell.total),
                         (87, 234, -1, b"prefix", 40))
        self.assertFalse(cell.complete)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            _ = cell.value
        self.assertEqual((self.callbacks(), self.process.resolve_export.call_count), (1, 1))

    def test_native_string_snapshot_preserves_nul_invalid_utf8_and_exact_empty_bytes(self):
        self.submit((self.key, self.key, self.key))
        exact = b"native\0key\xff"
        self.returned([(0, 0, 0, data.Kind.STRING, 0, exact, None),
                       (0, 1, 0, data.Kind.STRING, 0, b"", None),
                       (0, 2, 1168, data.Kind.EMPTY, 0, b"", 0)])
        page = self.query.read()
        self.assertEqual([cell.snapshot for cell in page.cells], [exact, b"", b""])
        self.assertEqual([cell.type_id for cell in page.cells], [-1, -1, -1])
        self.assertEqual([cell.value for cell in page.cells], [exact.decode("utf-8", "replace"), "", None])
        self.assertEqual(page.cells[2].error, 1168)
        self.assertEqual(page.value_handle, 0)
        with self.assertRaisesRegex(RuntimeError, "no retained native value"):
            self.query.release_value()
        self.assertEqual(self.callbacks(), 1)

    def test_interrupted_copy_preserves_same_query_prefix_and_resumes_without_replay(self):
        self.submit()
        text = b"key" * 50000
        _, _, read = self.returned([(0, 0, 0, data.Kind.STRING, 0, text, None)])
        failure = OSError("owned copy interrupted")
        failed = False
        def interrupted(address, count):
            nonlocal failed
            if address == 0x3000 + 65536 and not failed:
                failed = True
                raise failure
            return read(address, count)
        self.process.read.side_effect = interrupted
        original = self.query
        with self.assertRaises(OSError) as caught:
            original.read()
        self.assertIs(caught.exception, failure)
        self.assertIs(caught.exception.query, original)
        self.assertGreater(len(original.partial_result.cells[0].snapshot), 0)
        self.assertFalse(original.partial_result.cells[0].complete)
        self.process.free.assert_not_called()
        self.assertEqual(original.read().cells[0].snapshot, text)
        self.assertEqual(self.callbacks(), 1)
        self.assertEqual([call.args[0] for call in self.process.free.call_args_list], [0x3000, 0x2000])

    def test_cleanup_warning_preserves_primary_result_and_retries_only_cleanup(self):
        self.submit()
        self.returned([(0, 0, 0, data.Kind.STRING, 0, b"observed", None)])
        failure = OSError("free failed")
        self.process.free.side_effect = [failure, None, None]
        page = self.query.read()
        self.assertEqual(page.cells[0].value, "observed")
        self.assertIs(self.query.cleanup_error, failure)
        reads = self.process.read.call_count
        self.assertIs(self.query.read(), page)
        self.assertIsNone(self.query.cleanup_error)
        self.assertEqual(self.process.read.call_count, reads)
        self.assertEqual(self.callbacks(), 1)
        self.assertEqual([call.args[0] for call in self.process.free.call_args_list], [0x3000, 0x3000, 0x2000])

    def test_pending_retains_original_storage_without_read_free_or_replay(self):
        self.submit()
        self.status = {"status": "ok", "completed_request_id": 6}
        self.assertIsNone(self.query.read())
        with self.assertRaisesRegex(RuntimeError, "pending"):
            self.query.close()
        self.process.read.assert_not_called()
        self.process.free.assert_not_called()
        self.assertEqual(self.callbacks(), 1)

    def test_unknown_delivery_preserves_original_exception_and_query_then_recovers_once(self):
        failure = TimeoutError("unknown delivery")
        self.client.request.side_effect = failure
        with self.assertRaises(TimeoutError) as caught:
            self.submit()
        self.assertIs(caught.exception, failure)
        self.query = caught.exception.query
        original = self.query
        self.process.free.assert_not_called()
        original.recover(7)
        self.client.request.side_effect = lambda _: {"status": "ok", "completed_request_id": 7}
        self.returned([(0, 0, 0, data.Kind.STRING, 0, b"same owned copy", None)])
        self.assertEqual(original.read().cells[0].value, "same owned copy")
        self.assertIs(self.query, original)
        self.assertEqual(self.callbacks(), 1)

    def test_rejected_submission_and_failed_cleanup_keep_both_original_owners(self):
        self.client.request.side_effect = lambda _: {"status": "error", "error": 5}
        failure = OSError("request free failed")
        self.process.free.side_effect = failure
        with self.assertRaises(BridgeError) as caught:
            self.submit()
        original = caught.exception.query
        self.assertIs(caught.exception, original.submission_error)
        self.assertEqual(caught.exception.winerror, 5)
        self.assertIs(original.cleanup_error, failure)
        self.assertEqual(original.result_address, 0x2000)
        self.process.free.side_effect = None
        original.close()
        self.assertEqual(self.process.free.call_count, 2)
        self.assertEqual(self.callbacks(), 1)

    def test_callback574_does_not_overwrite_real_error87_or_copied_prefix(self):
        self.submit()
        self.status = {"status": "ok", "request_id": 7, "completed_request_id": 7, "world_error": 574}
        self.returned([(0, 0, 234, data.Kind.STRING, 0, b"kept", 20)], error=87)
        page = self.query.read()
        self.assertEqual((page.error, page.cells[0].snapshot), (87, b"kept"))
        self.assertEqual(self.query.completion_status["callback_error"], 574)
        self.assertEqual(self.callbacks(), 1)

    def test_callback574_preserves_pending_page_header_and_partial_original_query(self):
        self.submit()
        self.status = {"status": "ok", "request_id": 7, "completed_request_id": 7, "world_error": 574}
        self.returned([(0, 0, 234, data.Kind.STRING, 0, b"kept", 20)], error=data.ERROR_IO_PENDING)
        with self.assertRaises(QueryExecutionError) as caught:
            self.query.read()
        self.assertIs(caught.exception.result, self.query.partial_result)
        self.assertEqual(caught.exception.winerror, 574)
        self.assertEqual(caught.exception.result.error, data.ERROR_IO_PENDING)
        self.assertEqual(caught.exception.result.cells[0].snapshot, b"kept")
        self.assertEqual(self.callbacks(), 1)


if __name__ == "__main__":
    unittest.main()
