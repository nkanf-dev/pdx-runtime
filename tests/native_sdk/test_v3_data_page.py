"""Owned CPU projection fixtures. Never attach to a game. SPDX-License-Identifier: MPL-2.0"""
import struct
import unittest
from unittest.mock import Mock

from pdx_runtime import v3_data as data


class PageTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=1)
        self.client.request.side_effect = lambda command: ({"status": "ok", "request_id": 7, "error": 0}
            if command.startswith("interface-callback ") else {"status": "ok", "completed_request_id": 7})
        self.process = Mock(pid=1)
        self.process.resolve_export.return_value = 0x1000
        self.process.allocate.return_value = 0x2000
        self.query = None

    def submit(self, fields=("Building.GetNameNoFormatting",), **kwargs):
        self.query = data.project_page(self.client, fields, process=self.process, **kwargs)
        return self.process.write.call_args.args[1]

    def result(self, cells, *, start=0, total_rows=1, error=0, binding_mode=0xffffffff,
               binding_kind=0xffffffff, binding_type=b"", binding_error=0):
        raw = bytearray(self.process.write.call_args.args[1])
        h = list(data.PAGE_REQUEST.unpack_from(raw))
        table, payload = bytearray(), bytearray(binding_type)
        table_size = len(cells) * data.PAGE_CELL.size
        for row, field, err, kind, bits, text, total in cells:
            table.extend(data.PAGE_CELL.pack(row, field, err, kind, 4, binding_mode, bits,
                table_size + len(payload), len(text), len(text) if total is None else total,
                binding_kind, binding_error, table_size, len(binding_type)))
            payload.extend(text)
        copied = bytes(table + payload)
        h[1] = error; h[9] = start; h[11] = 123; h[12] = total_rows; h[13] = 1
        h[14] = len(cells) // h[4]; h[15] = 0x3000 if copied else 0; h[16] = len(copied); h[17] = len(cells)
        data.PAGE_REQUEST.pack_into(raw, 0, *h)
        def read(address, count):
            if address == 0x2000:
                return bytes(raw)
            offset = address - 0x3000
            return copied[offset:offset + count]
        self.process.read.side_effect = read
        return bytes(raw), copied, read

    def test_wire_root_binding_source_and_fields(self):
        raw = self.submit(("Building.GetBuildingType.GetKey", "Building.GetTotalLevels"),
            binding_rows=((data.ObjectBinding.full_id("State", 17, 0x81000043),),),
            source_expression="State.GetPotentialUrbanBuildings", start=64, limit=64)
        h = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual(data.PAGE_REQUEST.size, 88)
        self.assertEqual(h[2], 0)  # world is observed by native callback, not an input gate
        self.assertEqual(data.PAGE_CELL.size, 80)
        self.assertEqual((h[4], h[6], h[9], h[10]), (2, 1, 64, 64))
        self.assertEqual(raw[h[7]:h[7]+h[8]], b"State.GetPotentialUrbanBuildings")
        offset, count = data.PAGE_SPAN.unpack_from(raw, h[5])
        self.assertEqual((offset % 8, count), (0, 1))
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, offset), (17, 3, 0x81000043))
        self.process.resolve_export.assert_called_once()
        self.assertEqual(self.process.resolve_export.call_args.args, ("pdx_v3_data.dll", "PdxV3DataProjectPageInterface"))

    def test_many_full_id_rows_one_callback(self):
        raw = self.submit(binding_rows=tuple((data.ObjectBinding.full_id("Building", 31, 0xA0000000 + i),) for i in range(64)))
        h = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual(h[6], 64)
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in self.client.request.call_args_list), 1)
        for i in range(64):
            offset, count = data.PAGE_SPAN.unpack_from(raw, h[5] + i * data.PAGE_SPAN.size)
            self.assertEqual(data.BINDING_INPUT.unpack_from(raw, offset), (31, 6, 0xA0000000 + i))

    def test_cells_keep_errors_false_zero_and_complete_large_text(self):
        self.submit(("A.False", "A.Zero", "A.Error", "A.Long"))
        text = ("本地文本" * 30000).encode()
        self.result([(0, 0, 0, data.Kind.BOOL, 0, b"\0", None),
                     (0, 1, 0, data.Kind.U32, 0, bytes(4), None),
                     (0, 2, 1168, data.Kind.EMPTY, 0, b"", None),
                     (0, 3, 0, data.Kind.STRING, 0, text, None)])
        result = self.query.read()
        self.assertIs(result.cells[0].value, False)
        self.assertEqual(result.cells[1].value, 0)
        self.assertEqual(result.cells[2].error, 1168)
        self.assertEqual(result.cells[3].snapshot, text)
        self.assertEqual(result.cells[3].value.encode(), text)
        self.assertTrue(result.cells[3].complete)
        self.assertIs(self.query.read(), result)
        self.assertEqual([c.args[0] for c in self.process.free.call_args_list], [0x3000, 0x2000])
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in self.client.request.call_args_list), 1)

    def test_read_failure_retains_prefix_and_resumes_without_getter(self):
        self.submit()
        text = b"z" * 150000
        header, copied, read = self.result([(0, 0, 0, data.Kind.STRING, 0, text, None)])
        failed = False
        def partial(address, count):
            nonlocal failed
            if address == 0x3000 + 65536 and not failed:
                failed = True
                raise OSError("owned byte read failed")
            return read(address, count)
        self.process.read.side_effect = partial
        with self.assertRaises(OSError) as caught:
            self.query.read()
        self.assertIs(caught.exception.query, self.query)
        self.assertEqual(self.query.copied_address, 0x3000)
        self.assertEqual(len(self.query.partial_result.cells), 1)
        self.assertFalse(self.query.partial_result.cells[0].complete)
        self.process.free.assert_not_called()
        result = self.query.read()
        self.assertEqual(result.cells[0].snapshot, text)
        first_chunk = [c for c in self.process.read.call_args_list if c.args[0] == 0x3000]
        self.assertEqual(len(first_chunk), 1)
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in self.client.request.call_args_list), 1)

    def test_free_error_does_not_hide_result_and_retries_cleanup_only(self):
        self.submit()
        self.result([(0, 0, 0, data.Kind.STRING, 0, b"native", None)])
        self.process.free.side_effect = [OSError("free failed"), None, None]
        result = self.query.read()
        self.assertEqual(result.cells[0].value, "native")
        self.assertIsInstance(self.query.cleanup_error, OSError)
        reads = self.process.read.call_count
        self.assertIs(self.query.read(), result)
        self.assertIsNone(self.query.cleanup_error)
        self.assertEqual(self.process.read.call_count, reads)
        self.assertEqual([c.args[0] for c in self.process.free.call_args_list], [0x3000, 0x3000, 0x2000])

    def test_provider_binding_mode_is_preserved_without_retagging_or_validation(self):
        for mode in (0, 1, 2, 3, 0xffffffff):
            with self.subTest(mode=mode):
                self.submit(source_expression="State.GetPotentialUrbanBuildings")
                self.result([(0, 0, 13, data.Kind.STRING, 0, b"observed", None)], binding_mode=mode)
                cell = self.query.read().cells[0]
                self.assertEqual((cell.binding_mode, cell.error, cell.value), (mode, 13, "observed"))

    def test_original_any_descriptor_keeps_pointer_and_owned_representations_distinct(self):
        for type_name in (b".?AU?$SAnyType@PEBVCDataBuilding@@@PdxAnyImpl@@",
                          b".?AU?$SAnyType@V?$CPdxPtr@VCDataBuilding@@@@@PdxAnyImpl@@"):
            self.submit(source_expression="State.GetPotentialUrbanBuildings")
            self.result([(0, 0, 0, data.Kind.STRING, 0, b"name", None)],
                binding_mode=2, binding_kind=data.Kind.OBJECT, binding_type=type_name)
            cell = self.query.read().cells[0]
            self.assertEqual((cell.binding_kind, cell.binding_native_type_name, cell.binding_error),
                             (data.Kind.OBJECT, type_name.decode(), 0))
            self.assertEqual(cell.value, "name")

    def test_descriptor_error_does_not_erase_fields_or_become_field_error(self):
        self.submit(source_expression="State.GetPotentialUrbanBuildings")
        self.result([(0, 0, 0, data.Kind.BOOL, 0, b"\0", None)], binding_mode=2, binding_error=8)
        cell = self.query.read().cells[0]
        self.assertEqual((cell.error, cell.binding_error, cell.binding_native_type_name), (0, 8, None))
        self.assertIs(cell.value, False)


    def test_nested_source_keeps_typed_root_bindings_and_original_step_wire(self):
        roots = ((data.ObjectBinding(91, 1001, retained=True), data.ObjectBinding(92, 1002, retained=True)),)
        raw = self.submit(("CultureCasualtyStatistics.GetDead",), binding_rows=roots,
            steps=(data.CreationStep("War.GetActiveBattles", 1),),
            source_expression="Battle.GetAttackerStatistics")
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((header[2], header[3], header[18]), (0, 112, data.PAGE_SCOPED))
        offset, depth, completed, _, arguments_count, reserved = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, data.PAGE_REQUEST.size)
        self.assertEqual(arguments_count, 0)
        self.assertEqual((depth, completed, reserved), (1, 0, 0))
        step = data.CREATION_STEP.unpack_from(raw, offset)
        self.assertEqual(raw[step[0]:step[0] + step[1]], b"War.GetActiveBattles")
        self.assertEqual(step[2:4], (1, 0xffffffff))
        binding_offset, binding_count = data.PAGE_SPAN.unpack_from(raw, header[5])
        self.assertEqual(binding_count, 2)
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, binding_offset), (91, 1, 1001))
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, binding_offset + data.BINDING_INPUT.size), (92, 1, 1002))

    def test_nested_prefix_observations_survive_later_failure_and_copied_cells(self):
        self.submit(("CultureCasualtyStatistics.GetDead",),
            steps=(data.CreationStep("War.GetActiveBattles", 1),),
            source_expression="Battle.GetAttackerStatistics")
        header, copied, read = self.result([(0, 0, 13, data.Kind.I64, 0, bytes(8), None)], error=13)
        raw = bytearray(header)
        offset, depth, _, arguments_offset, arguments_count, reserved = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, data.PAGE_REQUEST.size)
        step = list(data.CREATION_STEP.unpack_from(raw, offset))
        step[4:10] = (1, 1, 3, 1, 99, 0)
        data.CREATION_STEP.pack_into(raw, offset, *step)
        data.PAGE_SCOPED_SUFFIX.pack_into(raw, data.PAGE_REQUEST.size, offset, depth, 1,
                                         arguments_offset, arguments_count, reserved)
        self.process.read.side_effect = lambda address, count: bytes(raw) if address == 0x2000 else read(address, count)
        result = self.query.read()
        self.assertEqual((result.error, result.completed_steps, result.steps[0].source_count), (13, 1, 3))
        self.assertEqual((result.steps[0].element_type_id, result.steps[0].binding_mode), (99, 0))
        self.assertEqual((result.cells[0].error, result.cells[0].value), (13, 0))

    def test_nested_depth_and_source_shape_bound_before_submission(self):
        for kwargs in (dict(steps=(data.CreationStep("War.GetActiveBattles", 0),)),
                       dict(steps=(data.CreationStep("War.GetActiveBattles", 0),) * 9, source_expression="Battle.GetAttackerStatistics"),
                       dict(steps=("not a step",), source_expression="Battle.GetAttackerStatistics")):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.submit(**kwargs)
        self.client.request.assert_not_called()


    def test_captured_source_argument_is_native_expression_in_explicit_slot_table(self):
        raw = self.submit(("BattleUnitType.GetAverageMorale",),
            binding_rows=((data.ObjectBinding(91, 1001, retained=True), data.ObjectBinding(92, 1002, retained=True)),),
            steps=(data.CreationStep("War.GetActiveBattles", 2),),
            source_expression="Battle.GetBattleUnitTypeList(MilitaryFormation.Self)",
            source_arguments=("MilitaryFormation.Self",))
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual(header[18], data.PAGE_SCOPED | data.PAGE_CAPTURE_ARGUMENTS)
        _, depth, _, offset, count, _ = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, data.PAGE_REQUEST.size)
        self.assertEqual((depth, count), (1, 1))
        text_offset, text_bytes = data.PAGE_SPAN.unpack_from(raw, offset)
        self.assertEqual(raw[text_offset:text_offset + text_bytes], b"MilitaryFormation.Self")
        self.assertEqual(raw[header[7]:header[7] + header[8]], b"Battle.GetBattleUnitTypeList(MilitaryFormation.Self)")
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in self.client.request.call_args_list), 1)

    def test_no_optional_flag_keeps_ordinary_header_even_with_large_input(self):
        raw = self.submit(("Building." + "Getter" * 40,))
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertGreater(len(raw), 112)
        self.assertEqual((header[3], header[18]), (88, 0))

    def test_source_arguments_require_source_and_bounded_slot_storage(self):
        for kwargs in (dict(source_arguments=("MilitaryFormation.Self",)),
                       dict(source_expression="Battle.GetBattleUnitTypeList(MilitaryFormation.Self)",
                            source_arguments=("MilitaryFormation.Self",) * 33),
                       dict(source_expression="Battle.GetBattleUnitTypeList(MilitaryFormation.Self)",
                            source_arguments=("MilitaryFormation.\0Self",))):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.submit(**kwargs)
        self.client.request.assert_not_called()

    def test_literal_columns_keep_original_expression_and_each_copied_row(self):
        raw = self.submit(("'literal text'",), source_expression="Owner.GetRows")
        h = data.PAGE_REQUEST.unpack_from(raw)
        offset, count = data.PAGE_SPAN.unpack_from(raw, h[3])
        self.assertEqual(raw[offset:offset + count], b"'literal text'")
        self.result([(0, 0, 0, data.Kind.STRING, 0, b"literal text", None),
                     (1, 0, 0, data.Kind.STRING, 0, b"literal text", None)], total_rows=2)
        self.assertEqual([row[0].value for row in self.query.read().rows], ["literal text", "literal text"])

    def test_page_never_has_an_engine_value_to_release(self):
        self.submit()
        self.result([(0, 0, 0, data.Kind.BOOL, 0, b"\0", None)])
        self.assertEqual(self.query.read().value_handle, 0)
        with self.assertRaisesRegex(RuntimeError, "no retained native value"):
            self.query.release_value()
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in self.client.request.call_args_list), 1)

    def test_partial_native_string_and_error_are_not_erased(self):
        self.submit()
        self.result([(0, 0, 234, data.Kind.STRING, 0, b"ab", 8)])
        cell = self.query.read().cells[0]
        self.assertEqual((cell.error, cell.snapshot, cell.total), (234, b"ab", 8))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            _ = cell.value

    def test_empty_source_and_page_continuation(self):
        self.submit(source_expression="State.AccessCappedRuralBuildings")
        self.result([], total_rows=0)
        result = self.query.read()
        self.assertTrue(result.count_valid)
        self.assertFalse(result.has_more)
        self.assertEqual(result.rows, ())
        self.process.free.assert_called_once_with(0x2000)
        self.submit(source_expression="State.AccessCappedRuralBuildings", start=64)
        self.result([(64, 0, 0, data.Kind.BOOL, 1, b"\1", None)], start=64, total_rows=80)
        result = self.query.read()
        self.assertEqual(result.next, 65)
        self.assertTrue(result.has_more)

    def test_pending_keeps_all_storage(self):
        self.submit()
        self.client.request.return_value = {"status": "ok", "completed_request_id": 6}
        self.client.request.side_effect = None
        self.assertIsNone(self.query.read())
        with self.assertRaisesRegex(RuntimeError, "pending"):
            self.query.close()
        self.process.free.assert_not_called()

    def test_invalid_dimensions_before_submission(self):
        cases = [dict(fields=()), dict(fields=("X",) * 33), dict(binding_rows=((),) * 65),
                 dict(limit=0), dict(start=-1), dict(start=1),
                 dict(source_expression="X", binding_rows=((), ())), dict(fields=("X\0Y",))]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                data.project_page(self.client, kwargs.pop("fields", ("X",)), process=self.process, **kwargs)
        self.process.allocate.assert_not_called()

    def test_request_free_error_keeps_primary_and_does_not_refree_copy(self):
        self.submit()
        self.result([(0, 0, 0, data.Kind.U64, 0, bytes(8), None)])
        self.process.free.side_effect = [None, OSError("request free failed"), None]
        result = self.query.read()
        self.assertEqual(result.cells[0].value, 0)
        self.assertIsInstance(self.query.cleanup_error, OSError)
        self.query.close()
        self.assertIsNone(self.query.cleanup_error)
        self.assertEqual([c.args[0] for c in self.process.free.call_args_list], [0x3000, 0x2000, 0x2000])

    def test_unknown_delivery_retains_exact_query_and_recovers_without_replay(self):
        self.client.request.side_effect = TimeoutError("unknown delivery")
        with self.assertRaises(TimeoutError) as caught:
            self.submit()
        self.query = caught.exception.query
        self.process.free.assert_not_called()
        self.query.recover(7)
        self.client.request.side_effect = lambda _: {"status": "ok", "completed_request_id": 7}
        self.result([(0, 0, 0, data.Kind.STRING, 0, b"same source", None)])
        self.assertEqual(self.query.read().cells[0].value, "same source")
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in self.client.request.call_args_list), 1)

    def test_rejected_cleanup_failure_keeps_shared_query_owner(self):
        self.client.request.side_effect = lambda _: {"status": "error", "error": 5}
        failure = OSError("request free failed")
        self.process.free.side_effect = failure
        from pdx_runtime.native_bridge import BridgeError
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

    def test_failed_page_keeps_successful_prefix_cells(self):
        self.submit()
        self.result([(0, 0, 0, data.Kind.STRING, 0, b"observed", None)], error=8)
        result = self.query.read()
        self.assertEqual(result.error, 8)
        self.assertEqual(result.cells[0].value, "observed")

    def test_native_error_and_all_numeric_bits_are_preserved(self):
        self.submit(("A.I32", "A.I64", "A.Double", "A.Fixed"))
        bits = struct.unpack("<Q", struct.pack("<d", -1.25))[0]
        self.result([(0, 0, 0, data.Kind.I32, 0xffffffff, bytes(4), None),
                     (0, 1, 0, data.Kind.I64, 0xffffffffffffffff, bytes(8), None),
                     (0, 2, 0, data.Kind.DOUBLE, bits, struct.pack("<d", -1.25), None),
                     (0, 3, 13, data.Kind.FIXED, 0, bytes(8), None)])
        cells = self.query.read().cells
        self.assertEqual([cell.value for cell in cells[:3]], [-1, -1, -1.25])
        self.assertEqual((cells[3].error, cells[3].raw_bits), (13, 0))

    def test_old_runtime_missing_export_never_submits(self):
        self.process.resolve_export.side_effect = LookupError("export not found")
        with self.assertRaises(LookupError):
            self.submit()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()


    def grouped_result(self, cells, observations, *, cursor=(3, 0), more=False, complete=True, error=0):
        raw = bytearray(self.process.write.call_args.args[1])
        h = list(data.PAGE_REQUEST.unpack_from(raw))
        prefix = data.PAGE_GROUP_OUTPUT.size + len(observations) * data.SOURCE_OBSERVATION.size
        table, payload = bytearray(), bytearray()
        payload_offset = prefix + len(cells) * data.PAGE_CELL.size
        for row, field, err, kind, bits, text, total in cells:
            table.extend(data.PAGE_CELL.pack(row, field, err, kind, 4, 0, bits,
                payload_offset + len(payload), len(text), len(text) if total is None else total,
                0xffffffff, 0, 0, 0))
            payload.extend(text)
        copied = (data.PAGE_GROUP_OUTPUT.pack(prefix, *cursor, len(observations), more, complete)
                  + b"".join(data.SOURCE_OBSERVATION.pack(*o) for o in observations) + table + payload)
        h[1] = error; h[11] = 123; h[14] = len(cells) // h[4]
        h[15] = 0x3000; h[16] = len(copied); h[17] = len(cells)
        data.PAGE_REQUEST.pack_into(raw, 0, *h)
        def read(address, count):
            if address == 0x2000:
                return bytes(raw)
            offset = address - 0x3000
            return copied[offset:offset + count]
        self.process.read.side_effect = read
        return bytes(raw), bytes(copied), read

    def test_group_input_alias_and_prefix_wire(self):
        raw = self.submit(("Building.GetName", "Building.GetLevels"), sources=("State.A", "State.B"),
            source_index=1, start=20, steps=(data.CreationStep("Country.GetStates", 2),))
        h = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((h[3], h[8], h[9], h[18]), (128, 0, 20, data.PAGE_GROUPED | data.PAGE_SCOPED))
        offset, count, index, reserved = data.PAGE_GROUP_INPUT.unpack_from(raw, 112)
        self.assertEqual((count, index, reserved), (2, 1, 0))
        spans = [data.PAGE_SPAN.unpack_from(raw, offset + i * 8) for i in range(count)]
        self.assertEqual([raw[o:o+n] for o, n in spans], [b"State.A", b"State.B"])
        self.assertEqual(self.process.resolve_export.call_args.args,
                         ("pdx_v3_data.dll", "PdxV3DataProjectSourcesInterface"))

    def test_group_bounds_and_single_source_arguments_unchanged(self):
        cases = [dict(sources=()), dict(sources=("A",) * 33), dict(sources=("A",), source_expression="B"),
                 dict(sources=("A",), source_index=2), dict(sources=("A",), source_index=True),
                 dict(sources=("A",), source_arguments=("A.Self",)),
                 dict(sources=("A",), binding_rows=((), ()))]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.submit(**options)
        self.process.allocate.assert_not_called()

    def test_group_source_errors_and_duplicate_local_rows_do_not_hide_values(self):
        self.submit(("A.N", "A.B"), sources=("S0", "S1", "S2"))
        observations = [(0, 13, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
                        (1, 0, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0),
                        (2, 0, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0)]
        self.grouped_result([(0, 0, 0, data.Kind.I32, 0, bytes(4), None),
                             (0, 1, 0, data.Kind.BOOL, 0, b"\0", None),
                             (0, 0, 13, data.Kind.STRING, 0, b"kept", None),
                             (0, 1, 0, data.Kind.BOOL, 1, b"\1", None)], observations, complete=False)
        page = self.query.read()
        self.assertEqual([c.value for c in page.cells], [0, False, "kept", True])
        self.assertEqual([c.row for c in page.cells], [0, 0, 0, 0])
        self.assertEqual([(s.source_index, s.row_offset, s.row_count) for s in page.sources], [(0, 0, 0), (1, 0, 1), (2, 1, 1)])
        self.assertEqual(page.sources[0].error, 13)
        self.assertFalse(page.sources[0].count_valid)
        self.assertFalse(page.range_complete)
        self.assertFalse(page.has_more)
        self.assertEqual(page.next_cursor, (3, 0))
        self.assertEqual(page.cells[2].error, 13)

    def test_group_range_complete_is_fragment_not_terminal_or_field_success(self):
        self.submit(sources=("S0", "S1"), source_index=1, start=40)
        self.grouped_result([(40, 0, 50, data.Kind.I32, 0, bytes(4), None)],
            [(1, 0, 1, 1, 80, 40, 1, 41, 0, 0, 1, 0)], cursor=(1, 41), more=True)
        page = self.query.read()
        self.assertTrue(page.range_complete)
        self.assertTrue(page.has_more)
        self.assertEqual(page.next_cursor, (1, 41))
        self.assertEqual(page.cells[0].error, 50)

    def test_group_first_copy_chunk_has_source_mapping_and_retry_keeps_query(self):
        self.submit(sources=("S0", "S1"))
        header, copied, read = self.grouped_result([(0, 0, 0, data.Kind.STRING, 0, b"x" * 150000, None)],
            [(0, 0, 1, 1, 0, 0, 0, 0, 0, 1, 1, 0), (1, 0, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0)], cursor=(2, 0))
        def fail(address, count):
            if address == 0x3000 + 65536:
                raise OSError("owned copy interrupted")
            return read(address, count)
        self.process.read.side_effect = fail
        with self.assertRaises(OSError) as caught:
            self.query.read()
        self.assertIs(caught.exception.query, self.query)
        partial = self.query.partial_result
        self.assertEqual([s.source_index for s in partial.sources], [0, 1])
        self.assertEqual(partial.next_cursor, (2, 0))
        self.assertGreater(len(partial.cells[0].snapshot), 0)
        self.assertFalse(partial.cells[0].complete)
        self.process.free.assert_not_called()
        self.process.read.side_effect = read
        self.assertEqual(self.query.read().cells[0].value, "x" * 150000)
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in self.client.request.call_args_list), 1)
        self.assertEqual(self.process.free.call_count, 2)

    def test_group_missing_prefix_remains_unknown_and_fatal_prefix_is_preserved(self):
        self.submit(sources=("S0", "S1"))
        header, copied, _ = self.grouped_result([(0, 0, 0, data.Kind.STRING, 0, b"before failure", None)],
            [(0, 8, 1, 1, 4, 0, 1, 0, 0, 0, 0, 0)], cursor=(0, 0), more=True, complete=False, error=8)
        empty = data.ProjectionPage.from_bytes(header, copied[:12])
        self.assertIsNone(empty.next_cursor)
        self.assertIsNone(empty.has_more)
        self.assertEqual(empty.sources, ())
        page = self.query.read()
        self.assertEqual((page.error, page.cells[0].value), (8, "before failure"))
        self.assertEqual(page.next_cursor, (0, 0))
        self.assertFalse(page.range_complete)

    def test_group_cleanup_warning_preserves_completed_result(self):
        self.submit(sources=("S0",))
        self.grouped_result([], [(0, 0, 1, 1, 0, 0, 0, 0, 0, 1, 1, 0)], cursor=(1, 0))
        self.process.free.side_effect = OSError("owned free failed")
        page = self.query.read()
        self.assertTrue(page.range_complete)
        self.assertIsNotNone(self.query.cleanup_error)
        self.process.free.side_effect = None
        self.assertIs(self.query.read(), page)
        self.assertIsNone(self.query.cleanup_error)

    def test_group_partial_type_name_is_unknown_not_empty_or_false_raw_descriptor(self):
        self.submit(tuple(f"A.F{i}" for i in range(32)), sources=("S0",))
        raw = bytearray(self.process.write.call_args.args[1])
        h = list(data.PAGE_REQUEST.unpack_from(raw))
        prefix = data.PAGE_GROUP_OUTPUT.size + data.SOURCE_OBSERVATION.size
        table_size = 64 * 32 * data.PAGE_CELL.size
        name = b".?AV?$SAnyType@V?$CPdxPtr@VCDataBuilding@@@@@PdxAnyImpl@@"
        table = bytearray()
        for row in range(64):
            for field in range(32):
                table.extend(data.PAGE_CELL.pack(row, field, 0, data.Kind.I32, 4, 2, 0,
                    prefix + table_size, 0, 0, data.Kind.OBJECT, 0, prefix + table_size, len(name)))
        copied = (data.PAGE_GROUP_OUTPUT.pack(prefix, 1, 0, 1, 0, 1)
            + data.SOURCE_OBSERVATION.pack(0, 0, 1, 1, 64, 0, 64, 64, 0, 1, 1, 0) + table + name)
        h[1] = 0; h[14] = 64; h[15] = 0x3000; h[16] = len(copied); h[17] = 2048
        data.PAGE_REQUEST.pack_into(raw, 0, *h)
        partial = data.ProjectionPage.from_bytes(bytes(raw), copied[:65536])
        self.assertGreater(len(partial.cells), 0)
        self.assertIsNone(partial.cells[0].binding_native_type_name)
        self.assertEqual(partial.cells[0].value, 0)
        short = data.ProjectionPage.from_bytes(bytes(raw), copied[:-1])
        self.assertIsNone(short.cells[0].binding_native_type_name)
        full = data.ProjectionPage.from_bytes(bytes(raw), copied)
        self.assertEqual(full.cells[0].binding_native_type_name, name.decode())

    def test_worldless_global_read_chains_use_existing_page_and_observed_zero(self):
        cases = [
            ("GetGameRules.GetRulePresets", ("GuiGameRulePreset.GetName", "GuiGameRulePreset.IsIronmanEnabled")),
            ("GetGameRules.GetSettings", ("GuiGameRule.GetRule.GetName", "GuiGameRule.GetSetting.GetKey", "GuiGameRule.GetSetting.GetName")),
        ]
        for expression, fields in cases:
            raw = self.submit(fields, source_expression=expression)
            h = data.PAGE_REQUEST.unpack_from(raw)
            self.assertEqual((h[2], h[6], h[18]), (0, 1, 0))
            self.assertEqual(raw[h[7]:h[7]+h[8]].decode(), expression)
            self.result([(0, i, 0, data.Kind.STRING, 0, b"native fixture", None) for i in range(len(fields))])
            page = self.query.read()
            self.assertEqual(page.world, 0)
            self.assertEqual(len(page.cells), len(fields))
            self.assertTrue(all(c.value == "native fixture" for c in page.cells))


if __name__ == "__main__":
    unittest.main()
