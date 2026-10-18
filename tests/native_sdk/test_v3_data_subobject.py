"""CPU-only additive embedded-subobject wire tests; not native game success."""
import unittest
from unittest.mock import Mock
from pdx_runtime import v3_data as data


class SubobjectTests(unittest.TestCase):
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

    def source(self, **kwargs):
        return data.PointerArraySource("PopupManager.Self", 0x40, 991, 88,
            element_subobject_offset=0x88, expected_element_vtable=0x100000 + 0x45c54e8, **kwargs)

    def packet(self):
        return self.process.write.call_args.args[1]

    def test_additive_suffix_and_real_original_type_not_pointer_deref(self):
        data.evaluate_pointer_array(self.client, self.source(index=2, expected_count=3),
            "PlayerMessageItem.GetDescription", world=77, process=self.process)
        raw = self.packet(); fields = data.REQUEST.unpack_from(raw)
        array = data.POINTER_ARRAY_SUFFIX.unpack_from(raw, data.ELEMENT_HEADER_SIZE)
        self.assertEqual(array[3], 991)
        self.assertEqual(array[7], data.ARRAY_DIRECT_CONTEXT | data.ARRAY_ELEMENT_SUBOBJECT)
        self.assertEqual(data.SUBOBJECT_SOURCE_SUFFIX.unpack_from(raw, data.ARRAY_ELEMENT_HEADER_SIZE),
                         (0x88, 0, 0x100000 + 0x45c54e8))
        self.assertEqual(fields[20], data.ARRAY_ELEMENT_HEADER_SIZE + 16)
        self.assertEqual(raw[fields[20]:fields[20]+fields[21]], b"PlayerMessageItem.GetDescription")

    def test_count_uses_old_pointer_array_wire_without_borrowing_subobject(self):
        data.pointer_array_count(self.client, self.source(), world=77, process=self.process)
        raw = self.packet(); fields = data.REQUEST.unpack_from(raw)
        array = data.POINTER_ARRAY_SUFFIX.unpack_from(raw, data.ELEMENT_HEADER_SIZE)
        self.assertEqual(array[7], data.ARRAY_COUNT_ONLY)
        self.assertEqual(fields[20], data.ARRAY_ELEMENT_HEADER_SIZE)

    def test_optional_guard_follows_subobject_suffix(self):
        data.evaluate_pointer_array(self.client, self.source(guard_expression="PlayerMessageItem.GetMessageGroupTag",
            guard_input=data.TypedInput.string("not-an-instance-id")), "PlayerMessageItem.GetName", world=77, process=self.process)
        raw = self.packet(); fields = data.REQUEST.unpack_from(raw)
        guard = data.SOURCE_GUARD_SUFFIX.unpack_from(raw, data.ARRAY_ELEMENT_HEADER_SIZE + 16)
        self.assertEqual(fields[20], data.GUARDED_ARRAY_HEADER_SIZE + 16)
        self.assertEqual(raw[guard[0]:guard[0]+guard[1]], b"PlayerMessageItem.GetMessageGroupTag")
        # This only encodes exact caller intent, never promotes group tag to stable identity.

    def test_old_dll_error_keeps_no_result_owner(self):
        q = data.evaluate_pointer_array(self.client, self.source(), "PlayerMessageItem.GetName", world=77, process=self.process)
        raw = bytearray(self.packet()); fields = list(data.REQUEST.unpack_from(raw)); fields[1] = 87
        raw[:data.REQUEST.size] = data.REQUEST.pack(*fields)
        self.process.read.return_value = bytes(raw)
        result = q.read()
        self.assertEqual(result.error, 87)
        self.assertEqual(result.result.value_handle, 0)
        self.assertEqual(result.subobject_source.byte_offset, 0x88)
        self.assertIs(q.read(), result)
        self.assertEqual(self.enqueue.call_count, 1)

    def test_subobject_requires_proven_original_vtable_and_alignment(self):
        for offset, vtable in [(0x88, 0), (0, 9), (7, 9), (True, 9), (0x88, True)]:
            with self.assertRaises(ValueError):
                data.PointerArraySource("Root.Self", 0x40, 991, 88,
                    element_subobject_offset=offset, expected_element_vtable=vtable)
        self.enqueue.assert_not_called()
