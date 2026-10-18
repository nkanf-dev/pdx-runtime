"""CPU-only nested wire/receipt tests. No native adapter or game is opened."""
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import Mock
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES

from pdx_runtime import v3_data as data
from pdx_runtime.native_bridge import BridgeError


class NestedProjectionTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=321)
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
        self.completion.return_value = {"status": "ok", "error": 0,
            "request_id": 7, "completed_request_id": 7, "callback_error": 0}
        self.process = Mock(pid=321)
        self.process.resolve_export.return_value = 0x1000
        self.process.allocate.return_value = 0x2000
        self.steps = (data.CreationStep("TopFrontend.AccessImportantActions", 2, 4),
                      data.CreationStep("ImportantAlert.GetAlerts", 1, 3))

    def packet(self):
        return self.process.write.call_args.args[1]

    def query(self, steps=None, **kwargs):
        return data.evaluate_scoped(self.client, self.steps if steps is None else steps,
            "Alert.GetName", world=0x1234, process=self.process, **kwargs)

    def completed(self, *, error=0, produced=1, count_valid=0, count=0,
                  kind=data.Kind.STRING, bits=0, text=b"alert", total=None,
                  completed=None, step_receipts=None, pointer_field=None):
        raw = bytearray(self.packet())
        fields = list(data.REQUEST.unpack_from(raw))
        fields[1] = error; fields[11] = kind; fields[26] = bits
        fields[9] = len(text) if total is None else total; fields[19] = len(text)
        if pointer_field is not None:
            fields[pointer_field] = bytes([1]) + bytes(39) if pointer_field == 27 else 0x9999
        raw[fields[24]:fields[24] + len(text)] = text
        raw[:data.REQUEST.size] = data.REQUEST.pack(*fields)
        prefix = list(data.PROJECTION_SUFFIX.unpack_from(raw, data.REQUEST.size))
        prefix[1:4] = [produced, count_valid, count]
        raw[data.REQUEST.size:data.PROJECTION_HEADER_SIZE] = data.PROJECTION_SUFFIX.pack(*prefix)
        suffix = list(data.SCOPED_PROJECTION_SUFFIX.unpack_from(raw, data.PROJECTION_HEADER_SIZE))
        suffix[2] = suffix[1] if completed is None else completed
        raw[data.PROJECTION_HEADER_SIZE:data.SCOPED_PROJECTION_HEADER_SIZE] = data.SCOPED_PROJECTION_SUFFIX.pack(*suffix)
        for i in range(suffix[1]):
            offset = suffix[0] + i * data.CREATION_STEP.size
            step = list(data.CREATION_STEP.unpack_from(raw, offset))
            step[4:] = ([1, 1, step[3] if step[3] != 0xffffffff else 5, 1, 500+i, 0]
                         if step_receipts is None else step_receipts[i])
            raw[offset:offset + data.CREATION_STEP.size] = data.CREATION_STEP.pack(*step)
        return bytes(raw)

    def test_exact_additive_wire_and_original_vm_names(self):
        q = self.query(output_bytes=128)
        raw = self.packet(); fields = data.REQUEST.unpack_from(raw)
        self.assertEqual((data.REQUEST.size, data.PROJECTION_HEADER_SIZE,
            data.SCOPED_PROJECTION_HEADER_SIZE, data.CREATION_STEP.size), (368, 392, 408, 40))
        self.assertEqual(fields[0], len(raw)); self.assertEqual(fields[2:5], (19, 0, -1))
        self.assertEqual(data.PROJECTION_SUFFIX.unpack_from(raw, data.REQUEST.size), (0x1234, 0, 0, 0, 0))
        self.assertEqual(data.SCOPED_PROJECTION_SUFFIX.unpack_from(raw, 392), (408, 2, 0, 0))
        cursor = 488
        for i, source in enumerate(self.steps):
            step = data.CREATION_STEP.unpack_from(raw, 408 + i * 40)
            self.assertEqual(step[:4], (cursor, len(source.source_expression), source.index, source.expected_count))
            self.assertEqual(step[4:], (0, 0, 0, 0, -1, 0xffffffff))
            self.assertEqual(raw[step[0]:step[0]+step[1]], source.source_expression.encode())
            cursor += step[1]
        self.assertEqual(fields[20], cursor)
        self.assertEqual(raw[fields[20]:fields[20]+fields[21]], b"Alert.GetName")
        self.assertEqual(fields[23], 0); self.assertEqual(fields[22], fields[24])
        self.assertEqual(fields[24] % 8, 0); self.assertEqual(fields[25], 128)
        self.assertEqual(q.request_id, 7)
        self.process.resolve_export.assert_called_once_with("pdx_v3_data.dll", "PdxV3DataInterface", aliases=V3_EXPORT_ALIASES)
        self.assertIs(self.process.resolve_export.call_args.kwargs["aliases"], V3_EXPORT_ALIASES)
        self.enqueue.assert_called_once_with(0x1000, 0x2000)

    def test_typed_leaf_receipts_and_no_handle(self):
        q = self.query()
        self.process.read.return_value = self.completed()
        receipt = q.read()
        self.assertEqual(receipt.value, "alert")
        self.assertEqual(receipt.completed_steps, 2)
        self.assertEqual(receipt.steps[0].source_count, 4)
        self.assertEqual(receipt.steps[1].expected_count, 3)
        self.assertEqual(receipt.steps[1].element_type_id, 501)
        self.assertEqual(receipt.value_handle, 0)
        self.assertIs(q.read(), receipt)
        self.process.free.assert_called_once_with(0x2000)
        with self.assertRaises(RuntimeError): q.release_value()
        self.assertEqual(self.enqueue.call_count, 1)

    def test_inner_count_uses_only_parent_step(self):
        q = data.scoped_source_count(self.client, self.steps[:1], "ImportantAlert.GetAlerts",
            world=0x1234, process=self.process)
        fields = data.REQUEST.unpack_from(self.packet())
        self.assertEqual(fields[2], 20); self.assertEqual(fields[25], 0)
        self.assertEqual(data.SCOPED_PROJECTION_SUFFIX.unpack_from(self.packet(), 392)[1], 1)
        self.process.read.return_value = self.completed(kind=data.Kind.EMPTY, text=b"",
            count_valid=1, count=0)
        receipt = q.read()
        self.assertEqual((receipt.source_produced, receipt.count_valid, receipt.source_count), (1, 1, 0))
        self.assertEqual(receipt.steps[0].source_count, 4)

    def test_empty_parent_keeps_proven_zero_on_not_found(self):
        q = self.query()
        self.process.read.return_value = self.completed(error=1168, produced=0,
            kind=data.Kind.EMPTY, text=b"", completed=0,
            step_receipts=[[1, 1, 0, 0, -1, 0xffffffff], [0, 0, 0, 0, -1, 0xffffffff]])
        receipt = q.read()
        self.assertEqual((receipt.error, receipt.completed_steps), (1168, 0))
        self.assertEqual((receipt.steps[0].source_produced, receipt.steps[0].count_valid,
                          receipt.steps[0].source_count), (1, 1, 0))
        self.assertEqual(receipt.steps[1].source_produced, 0)

    def test_count_mismatch_keeps_current_receipt_without_element(self):
        q = self.query()
        self.process.read.return_value = self.completed(error=1237, produced=0,
            kind=data.Kind.EMPTY, text=b"", completed=1,
            step_receipts=[[1, 1, 4, 1, 500, 0], [1, 1, 7, 0, -1, 0xffffffff]])
        receipt = q.read()
        self.assertEqual((receipt.error, receipt.steps[1].source_count), (1237, 7))
        self.assertEqual(receipt.steps[1].element_obtained, 0)
        self.assertEqual(self.enqueue.call_count, 1)

    def test_missing_inner_is_not_empty(self):
        q = self.query()
        self.process.read.return_value = self.completed(error=1168, produced=0,
            kind=data.Kind.EMPTY, text=b"", completed=1,
            step_receipts=[[1, 1, 4, 1, 500, 0], [0, 0, 0, 0, -1, 0xffffffff]])
        receipt = q.read()
        self.assertEqual(receipt.steps[1].count_valid, 0)
        self.assertEqual(receipt.source_produced, 0)

    def test_false_zero_empty_are_distinct(self):
        for kind, bits, text, value in ((data.Kind.BOOL, 0, b"", False),
                (data.Kind.U64, 0, b"", 0), (data.Kind.BOOL, 1, b"", True),
                (data.Kind.STRING, 0, b"", ""), (data.Kind.EMPTY, 0, b"", None)):
            with self.subTest(kind=kind, bits=bits):
                q = self.query()
                self.process.read.return_value = self.completed(kind=kind, bits=bits, text=text)
                receipt = q.read()
                self.assertEqual(receipt.value, value)
                self.assertEqual(receipt.source_produced, 1)

    def test_partial_text_has_complete_source_receipts_no_replay(self):
        q = self.query(output_bytes=3)
        self.process.read.return_value = self.completed(error=234, text=b"abc", total=20)
        receipt = q.read()
        self.assertEqual((receipt.error, receipt.source_produced, receipt.completed_steps), (234, 1, 2))
        self.assertEqual(receipt.result.snapshot, b"abc")
        with self.assertRaises(ValueError): _ = receipt.value
        self.assertIs(q.read(), receipt)
        self.assertEqual(self.enqueue.call_count, 1)

    def test_old_dll_rejects_without_fallback(self):
        q = self.query()
        self.process.read.return_value = self.completed(error=87, produced=0,
            kind=data.Kind.RAW, text=b"", completed=0,
            step_receipts=[[0, 0, 0, 0, -1, 0xffffffff]]*2)
        receipt = q.read()
        self.assertEqual((receipt.error, receipt.source_produced, receipt.count_valid), (87, 0, 0))
        self.assertEqual(self.enqueue.call_count, 1)

    def test_zero_and_eight_step_bounds(self):
        for depth in (0, 8):
            q = self.query(steps=(data.CreationStep("Global.GetItems", 0) for _ in range(depth)))
            self.assertEqual(data.SCOPED_PROJECTION_SUFFIX.unpack_from(self.packet(), 392)[1], depth)
            self.process.read.return_value = self.completed()
            self.assertEqual(q.read().completed_steps, depth)
        with self.assertRaises(ValueError):
            self.query(steps=(data.CreationStep("Global.GetItems", 0) for _ in range(9)))

    def test_frozen_strict_creation_step_validation(self):
        step = data.CreationStep("Global.GetItems", 0)
        with self.assertRaises(FrozenInstanceError): step.index = 1
        for source in ("", "Bad\0Name", "Bad\ud800Name", None, 1):
            with self.subTest(source=repr(source)), self.assertRaises(ValueError): data.CreationStep(source, 0)
        for index in (-1, True, 1.5, 1 << 31):
            with self.assertRaises(ValueError): data.CreationStep("Global.GetItems", index)
        for expected in (-1, True, 1.5, 1 << 31):
            with self.assertRaises(ValueError): data.CreationStep("Global.GetItems", 0, expected)
        self.assertEqual(data.CreationStep("Global.GetItems", 0, 0).expected_count, 0)

    def test_request_validation_precedes_allocation(self):
        for world in (0, -1, True, 1 << 64):
            with self.assertRaises(ValueError):
                data.evaluate_scoped(self.client, self.steps, "Alert.GetName", world=world, process=self.process)
        for capacity in (-1, True, 1.5, 1 << 32, 0xffffffff):
            with self.assertRaises(ValueError): self.query(output_bytes=capacity)
        for expression in ("", "Bad\0Name", "Bad\ud800Name"):
            with self.assertRaises(ValueError):
                data.scoped_source_count(self.client, self.steps, expression, world=1, process=self.process)
        with self.assertRaises(ValueError): self.query(steps=("not a step",))
        self.process.allocate.assert_not_called()
        self.enqueue.assert_not_called()

    def test_shared_process_identity_precedes_lookup(self):
        self.process.pid = 322
        with self.assertRaises(ValueError): self.query()
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()

    def test_pending_status_does_not_replay_or_free(self):
        q = self.query()
        self.completion.return_value = {"status": "ok", "error": 0, "completed_request_id": 6}
        self.assertIsNone(q.read()); self.assertIsNone(q.read())
        self.process.read.assert_not_called(); self.process.free.assert_not_called()
        self.assertEqual(self.enqueue.call_count, 1)

    def test_unknown_submission_keeps_same_allocation_for_recovery(self):
        self.enqueue.return_value = {"status": "ok", "error": 0}
        with self.assertRaises(BridgeError) as caught: self.query()
        q = caught.exception.query
        self.process.free.assert_not_called()
        self.process.read.return_value = self.completed()
        q.recover(7)
        self.assertEqual(q.read().value, "alert")
        self.assertEqual(self.enqueue.call_count, 1)

    def test_decoder_rejects_pointer_fields_and_raw_success(self):
        self.query()
        for field in (12, 13, 14, 15, 16, 17, 27):
            with self.subTest(field=field), self.assertRaises(ValueError):
                data.ProjectionResult.from_bytes(self.completed(pointer_field=field))
        for kind in (data.Kind.RAW, data.Kind.OBJECT, data.Kind.LIST, data.Kind.COMMAND):
            with self.assertRaises(ValueError): data.ProjectionResult.from_bytes(self.completed(kind=kind))

    def test_decoder_rejects_inconsistent_receipts_and_wire_ranges(self):
        self.query()
        invalid = [self.completed(produced=0), self.completed(completed=1),
            self.completed(step_receipts=[[0, 1, 4, 1, 500, 0], [1, 1, 3, 1, 501, 0]]),
            self.completed(step_receipts=[[1, 1, 4, 1, 500, 0], [1, 1, 1, 1, 501, 0]])]
        raw = self.completed()
        for index, value in ((0, 1), (1, 9), (2, 3), (3, 1)):
            broken = bytearray(raw)
            suffix = list(data.SCOPED_PROJECTION_SUFFIX.unpack_from(raw, 392)); suffix[index] = value
            broken[392:408] = data.SCOPED_PROJECTION_SUFFIX.pack(*suffix)
            invalid.append(bytes(broken))
        for broken in invalid:
            with self.assertRaises(ValueError): data.ProjectionResult.from_bytes(broken)

    def test_root_actions_remain_exact_and_empty_steps(self):
        for call, action in ((data.evaluate_copied, 17), (data.source_count, 18)):
            q = call(self.client, "Global.GetItems", world=1, process=self.process)
            raw = self.packet(); fields = list(data.REQUEST.unpack_from(raw))
            self.assertEqual(fields[2], action); self.assertEqual(fields[20], 392)
            fields[1] = 0; fields[11] = data.Kind.EMPTY
            raw = bytearray(raw); raw[:368] = data.REQUEST.pack(*fields)
            raw[368:392] = data.PROJECTION_SUFFIX.pack(1, 1, int(action == 18), 0, 0)
            self.process.read.return_value = bytes(raw)
            self.assertEqual(q.read().steps, ())


if __name__ == "__main__":
    unittest.main()
