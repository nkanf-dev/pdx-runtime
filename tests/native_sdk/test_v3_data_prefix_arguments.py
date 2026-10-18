"""Typed prefix capture wire and copied-page tests. No native or game calls.
SPDX-License-Identifier: MPL-2.0
"""
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import Mock

from pdx_runtime import v3_data as data


class PrefixArgumentTests(unittest.TestCase):
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
        self.query = None
        self.roots = ((data.ObjectBinding(91, 1001, retained=True),
                       data.ObjectBinding(92, 1002, retained=True)),)
        self.steps = (data.CreationStep("War.GetActiveBattles", 1, 4),
                      data.CreationStep("Battle.GetBattleUnitTypeList(MilitaryFormation.Self)", 2,
                                        source_arguments=("MilitaryFormation.Self",)))

    def submit(self, **kwargs):
        kwargs.setdefault("steps", self.steps)
        kwargs.setdefault("binding_rows", self.roots)
        if "sources" not in kwargs:
            kwargs.setdefault("source_expression", "BattleUnitType.GetCultureStatistics")
        self.query = data.project_page(self.client, ("CultureCasualtyStatistics.GetDead",),
                                       process=self.process, **kwargs)
        self.raw = bytearray(self.process.write.call_args.args[1])
        return bytes(self.raw)

    def argument_tables(self, raw):
        suffix = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, data.PAGE_REQUEST.size)
        self.assertNotEqual(suffix[5], 0)
        values = []
        for index in range(suffix[1]):
            offset, count = data.PAGE_SPAN.unpack_from(raw, suffix[5] + index * data.PAGE_SPAN.size)
            arguments = []
            if count:
                self.assertEqual(offset % 8, 0)
            for slot in range(count):
                start, length = data.PAGE_SPAN.unpack_from(raw, offset + slot * data.PAGE_SPAN.size)
                self.assertLessEqual(start + length, len(raw))
                arguments.append(raw[start:start + length])
            values.append(tuple(arguments))
        return suffix, tuple(values)

    def returned(self, *, text=b"native copied", error=0, cell_error=0, completed=2,
                 source_produced=1, count_valid=1, source_count=3, grouped=False):
        h = list(data.PAGE_REQUEST.unpack_from(self.raw))
        suffix = list(data.PAGE_SCOPED_SUFFIX.unpack_from(self.raw, data.PAGE_REQUEST.size))
        suffix[2] = completed
        data.PAGE_SCOPED_SUFFIX.pack_into(self.raw, data.PAGE_REQUEST.size, *suffix)
        for index in range(suffix[1]):
            at = suffix[0] + index * data.CREATION_STEP.size
            step = list(data.CREATION_STEP.unpack_from(self.raw, at))
            if index < completed:
                step[4:] = 1, 1, 4, 1, 99 + index, index % 2
            data.CREATION_STEP.pack_into(self.raw, at, *step)
        prefix = b""
        if grouped:
            prefix_size = data.PAGE_GROUP_OUTPUT.size + data.SOURCE_OBSERVATION.size
            prefix = (data.PAGE_GROUP_OUTPUT.pack(prefix_size, 1, 0, 1, 0, 1)
                      + data.SOURCE_OBSERVATION.pack(0, 0, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0))
        payload_offset = len(prefix) + data.PAGE_CELL.size
        copied = prefix + data.PAGE_CELL.pack(0, 0, cell_error, data.Kind.STRING, 77,
                                             0, 0, payload_offset, len(text), len(text),
                                             0xffffffff, 0, 0, 0) + text
        h[1], h[11], h[12], h[13], h[14] = error, 91, source_count, count_valid, 1
        h[15], h[16], h[17] = 0x3000, len(copied), 1
        data.PAGE_REQUEST.pack_into(self.raw, 0, *h)
        def read(address, count):
            if address == 0x2000:
                return bytes(self.raw)
            offset = address - 0x3000
            return copied[offset:offset + count]
        self.process.read.side_effect = read
        return copied, read

    def test_creation_step_keyword_only_old_three_positional_and_frozen_tuple(self):
        old = data.CreationStep("Root.Rows", 1, 3)
        self.assertEqual(old.source_arguments, ())
        with self.assertRaises(TypeError):
            data.CreationStep("Root.Rows", 1, 3, ("Root.Self",))
        captures = ["Root.Self"]
        step = data.CreationStep("Root.Rows(Root.Self)", 0, source_arguments=captures)
        captures.append("Other.Self")
        self.assertEqual(step.source_arguments, ("Root.Self",))
        with self.assertRaises(FrozenInstanceError):
            step.source_arguments = ()

    def test_prefix_formation_capture_has_parallel_step_table_in_suffix_index_five(self):
        raw = self.submit()
        h = data.PAGE_REQUEST.unpack_from(raw)
        suffix, arguments = self.argument_tables(raw)
        self.assertEqual(data.CREATION_STEP.size, 40)
        self.assertEqual(h[18], data.PAGE_SCOPED | data.PAGE_CAPTURE_STEP_ARGUMENTS)
        self.assertEqual((suffix[1], suffix[2], suffix[4]), (2, 0, 0))
        self.assertEqual(arguments, ((), (b"MilitaryFormation.Self",)))
        self.assertEqual(suffix[5] % 8, 0)
        self.assertEqual(data.PAGE_SPAN.unpack_from(raw, suffix[5]), (0, 0))
        for index, original in enumerate(self.steps):
            step = data.CREATION_STEP.unpack_from(raw, suffix[0] + index * 40)
            self.assertEqual(raw[step[0]:step[0] + step[1]], original.source_expression.encode())
            self.assertEqual(step[2:4], (original.index, 0xffffffff if original.expected_count is None
                                        else original.expected_count))
            self.assertEqual(step[4:], (0, 0, 0, 0, -1, 0xffffffff))
        self.assertEqual(self.process.resolve_export.call_args.args,
                         ("pdx_v3_data.dll", "PdxV3DataProjectTypedStepsInterface"))
        self.assertEqual(self.query.adapter, 0xA000)
        self.assertEqual((self.process.allocate.call_count, self.client.request.call_count), (1, 1))

    def test_prefix_and_final_source_slots_keep_separate_exact_ordered_tables(self):
        steps = (data.CreationStep("Root.Pairs(A.Self,B.Self)", 0,
                                  source_arguments=("B.Self", "A.Self")),)
        raw = self.submit(steps=steps, source_expression="Pair.Values(C.Self,D.Self)",
                          source_arguments=("D.Self", "C.Self"))
        h = data.PAGE_REQUEST.unpack_from(raw)
        suffix, arguments = self.argument_tables(raw)
        self.assertEqual(h[18], 1 | 2 | 8)
        self.assertEqual(arguments, ((b"B.Self", b"A.Self"),))
        self.assertEqual(suffix[4], 2)
        final = []
        for slot in range(suffix[4]):
            offset, count = data.PAGE_SPAN.unpack_from(raw, suffix[3] + slot * 8)
            final.append(raw[offset:offset + count])
        self.assertEqual(final, [b"D.Self", b"C.Self"])
        self.assertNotEqual(suffix[3], suffix[5])
        self.assertEqual(raw[h[7]:h[7] + h[8]], b"Pair.Values(C.Self,D.Self)")

    def test_root_bindings_remain_original_typed_inputs_not_exported_capture_handles(self):
        raw = self.submit()
        h = data.PAGE_REQUEST.unpack_from(raw)
        offset, count = data.PAGE_SPAN.unpack_from(raw, h[5])
        self.assertEqual((h[2], h[6], offset % 8, count), (0, 1, 0, 2))
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, offset), (91, 1, 1001))
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, offset + data.BINDING_INPUT.size), (92, 1, 1002))
        self.assertEqual(self.client.request.call_count, 1)
        self.process.read.assert_not_called()

    def test_grouped_prefix_captures_select_typed_alias_not_grouped_old_symbol(self):
        raw = self.submit(sources=("BattleUnitType.A", "BattleUnitType.B"), source_index=1)
        h = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual(h[18], 1 | 4 | 8)
        self.assertEqual(h[3], 128)
        self.assertEqual(self.argument_tables(raw)[1], ((), (b"MilitaryFormation.Self",)))
        offset, count, index, reserved = data.PAGE_GROUP_INPUT.unpack_from(raw, 112)
        values = []
        for i in range(count):
            at, length = data.PAGE_SPAN.unpack_from(raw, offset + i * 8)
            values.append(raw[at:at + length])
        self.assertEqual((values, index, reserved), ([b"BattleUnitType.A", b"BattleUnitType.B"], 1, 0))
        self.assertEqual(self.process.resolve_export.call_args.args[1], "PdxV3DataProjectTypedStepsInterface")


    def test_ordinary_parameterized_prefix_is_not_automatically_captured_or_retagged(self):
        raw = self.submit(steps=(data.CreationStep("Root.Rows(MilitaryFormation.Self)", 0),))
        h = data.PAGE_REQUEST.unpack_from(raw)
        suffix = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, data.PAGE_REQUEST.size)
        self.assertEqual((h[18], suffix[4], suffix[5]), (data.PAGE_SCOPED, 0, 0))
        step = data.CREATION_STEP.unpack_from(raw, suffix[0])
        self.assertEqual(raw[step[0]:step[0] + step[1]], b"Root.Rows(MilitaryFormation.Self)")
        self.assertEqual(self.process.resolve_export.call_args.args[1], "PdxV3DataProjectPageInterface")

    def test_grouped_without_prefix_captures_keeps_original_group_alias(self):
        self.submit(steps=(data.CreationStep("Root.Rows", 0),), sources=("B.Rows",))
        self.assertEqual(self.process.resolve_export.call_args.args[1], "PdxV3DataProjectSourcesInterface")

    def test_32_slots_per_source_not_an_aggregate_chain_limit(self):
        slots = tuple(f"Root.Argument{i}" for i in range(32))
        steps = tuple(data.CreationStep("Root.Rows", i, source_arguments=slots) for i in range(8))
        raw = self.submit(steps=steps, source_arguments=slots)
        suffix, arguments = self.argument_tables(raw)
        self.assertEqual((suffix[1], suffix[4]), (8, 32))
        self.assertEqual(tuple(map(len, arguments)), (32,) * 8)
        self.assertEqual(arguments[7], tuple(expression.encode() for expression in slots))

    def test_invalid_capture_expressions_and_slot_bounds_before_allocation(self):
        for value in ("", "Bad\0Name", "Bad\ud800Name", None, 1):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                data.CreationStep("Root.Rows", 0, source_arguments=(value,))
        for value in ("Root.Self", b"Root.Self", None, ("Root.Self",) * 33):
            with self.subTest(arguments=repr(value)), self.assertRaises(ValueError):
                data.CreationStep("Root.Rows", 0, source_arguments=value)
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_bounded_generator_arguments_are_copied_into_immutable_slot_tuple(self):
        step = data.CreationStep("Root.Rows", 0, source_arguments=(f"Root.A{i}" for i in range(32)))
        self.assertEqual(len(step.source_arguments), 32)
        self.assertIsInstance(step.source_arguments, tuple)
        with self.assertRaises(ValueError):
            data.CreationStep("Root.Rows", 0, source_arguments=("Root.Self" for _ in range(33)))

    def test_invalid_step_indices_depth_and_source_shape_never_allocate(self):
        for index in (-1, True, 1 << 31, 1.5):
            with self.assertRaises(ValueError):
                data.CreationStep("Root.Rows", index, source_arguments=("Root.Self",))
        for expected in (-1, True, 1 << 31, 1.5):
            with self.assertRaises(ValueError):
                data.CreationStep("Root.Rows", 0, expected, source_arguments=("Root.Self",))
        for kwargs in (dict(steps=self.steps * 5), dict(steps=self.steps, source_expression=None),
                       dict(steps=self.steps, binding_rows=((), ()))):
            with self.assertRaises(ValueError):
                self.submit(**kwargs)
        self.process.allocate.assert_not_called()

    def test_legacy_scoped_entrypoints_reject_captures_instead_of_silent_drop(self):
        step = data.CreationStep("Root.Rows(Root.Self)", 0, source_arguments=("Root.Self",))
        for call in (data.evaluate_scoped, data.scoped_source_count):
            with self.subTest(call=call.__name__), self.assertRaisesRegex(ValueError, "project_page"):
                call(self.client, (step,), "Row.Value", world=1, process=self.process)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_copied_page_output_keeps_original_steps_and_no_engine_any_handle(self):
        self.submit()
        self.returned(text="实际复制".encode())
        result = self.query.read()
        self.assertIsInstance(result, data.ProjectionPage)
        self.assertEqual((result.value_handle, result.completed_steps), (0, 2))
        self.assertEqual(result.cells[0].value, "实际复制")
        self.assertEqual(tuple(step.binding_mode for step in result.steps), (0, 1))
        self.assertEqual(tuple(step.element_type_id for step in result.steps), (99, 100))
        self.assertEqual([call.args[0] for call in self.process.free.call_args_list], [0x3000, 0x2000])
        self.assertEqual(self.client.request.call_count, 2)
        self.assertEqual(self.process.read.call_count, 2)
        self.assertIs(self.query.read(), result)

    def test_grouped_copied_page_uses_unchanged_observation_decoder(self):
        self.submit(sources=("BattleUnitType.A",))
        self.returned(grouped=True)
        result = self.query.read()
        self.assertTrue(result.grouped)
        self.assertEqual(result.next_cursor, (1, 0))
        self.assertEqual(result.sources[0].source_count, 1)
        self.assertEqual(result.cells[0].value, "native copied")
        self.assertEqual(result.completed_steps, 2)
        self.assertEqual(result.value_handle, 0)

    def test_capture_failure_preserves_native_error_and_unobserved_counts(self):
        self.submit()
        h = list(data.PAGE_REQUEST.unpack_from(self.raw))
        h[1], h[12], h[13], h[14], h[15], h[16], h[17] = 13, 0, 0, 0, 0, 0, 0
        data.PAGE_REQUEST.pack_into(self.raw, 0, *h)
        self.process.read.return_value = bytes(self.raw)
        result = self.query.read()
        self.assertEqual((result.error, result.count_valid, result.source_count, result.completed_steps),
                         (13, False, 0, 0))
        self.assertEqual(tuple(step.count_valid for step in result.steps), (0, 0))
        self.assertEqual(result.cells, ())
        self.assertEqual(self.process.read.call_count, 1)
        self.assertEqual(self.client.request.call_count, 2)

    def test_pending_typed_page_retains_original_allocation_without_capture_replay(self):
        self.submit()
        self.status["completed_request_id"] = 6
        self.assertIsNone(self.query.read())
        self.process.read.assert_not_called()
        self.process.free.assert_not_called()
        self.assertEqual((self.process.allocate.call_count, self.client.request.call_count), (1, 2))

    def test_owned_copy_failure_preserves_prefix_and_resumes_same_page(self):
        self.submit()
        text = b"x" * 150000
        copied, original_read = self.returned(text=text)
        fault = OSError("original copied-page tail read fault")
        def read(address, count):
            if address == 0x3000 + 65536:
                raise fault
            return original_read(address, count)
        self.process.read.side_effect = read
        with self.assertRaises(OSError) as caught:
            self.query.read()
        self.assertIs(caught.exception, fault)
        self.assertIs(fault.query, self.query)
        self.assertEqual(self.query.partial_result.completed_steps, 2)
        self.assertFalse(self.query.partial_result.cells[0].complete)
        self.assertGreater(len(self.query.partial_result.cells[0].snapshot), 0)
        self.process.free.assert_not_called()
        self.process.read.side_effect = original_read
        result = self.query.read()
        self.assertEqual(result.cells[0].snapshot, text)
        self.assertEqual(self.process.allocate.call_count, 1)
        self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                             for call in self.client.request.call_args_list), 1)
        self.assertEqual(sum(call.args[0] == 0x3000 for call in self.process.read.call_args_list), 1)

    def test_worldless_fresh_global_root_keeps_zero_world_without_extra_status_rpc(self):
        step = data.CreationStep("GetGlobalRows(GetGlobalScope)", 0,
                                 source_arguments=("GetGlobalScope",))
        raw = self.submit(steps=(step,), binding_rows=((),), source_expression="Row.GetChildren")
        h = data.PAGE_REQUEST.unpack_from(raw)
        offset, count = data.PAGE_SPAN.unpack_from(raw, h[5])
        self.assertEqual((h[2], h[6], count), (0, 1, 0))
        self.assertEqual(offset % 8, 0)
        self.assertEqual(self.argument_tables(raw)[1], ((b"GetGlobalScope",),))
        self.assertEqual(self.client.request.call_count, 1)
        self.assertTrue(self.client.request.call_args.args[0].startswith("interface-callback "))

    def test_unknown_typed_prefix_submission_recovers_original_page_without_replay(self):
        fault = TimeoutError("original unknown typed-prefix delivery")
        self.client.request.side_effect = fault
        with self.assertRaises(TimeoutError) as caught:
            self.submit()
        self.assertIs(caught.exception, fault)
        self.query = fault.query
        self.raw = bytearray(self.process.write.call_args.args[1])
        self.assertIsInstance(self.query, data.ProjectionPageQuery)
        self.assertEqual(self.query.adapter, 0xA000)
        self.process.free.assert_not_called()
        self.returned()
        self.client.request.side_effect = lambda _: self.status
        self.assertIs(self.query.recover(7), self.query)
        result = self.query.read()
        self.assertEqual((result.completed_steps, result.value_handle), (2, 0))
        self.assertEqual(result.cells[0].value, "native copied")
        self.assertEqual((self.process.allocate.call_count, self.client.request.call_count), (1, 2))
        self.assertEqual(self.process.resolve_export.call_count, 1)
        self.assertEqual(self.process.resolve_export.call_args.args[1], "PdxV3DataProjectTypedStepsInterface")

    def test_missing_typed_capability_never_falls_back_allocates_or_submits(self):
        fault = LookupError("typed-prefix capability missing")
        self.process.resolve_export.side_effect = fault
        with self.assertRaises(LookupError) as caught:
            self.submit(sources=("BattleUnitType.A",))
        self.assertIs(caught.exception, fault)
        self.assertEqual(self.process.resolve_export.call_count, 1)
        self.assertEqual(self.process.resolve_export.call_args.args[1], "PdxV3DataProjectTypedStepsInterface")
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
