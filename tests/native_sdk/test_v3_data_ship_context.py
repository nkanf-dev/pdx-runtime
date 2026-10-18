"""Ship context typed wire and copied DataResult tests. No native/game calls.
SPDX-License-Identifier: MPL-2.0
"""
import struct
import unittest
from unittest.mock import Mock

from pdx_runtime import v3_data as data


SHIP_POPUP_ANY = b".?AU?$SAnyType@V?$CPdxPtr@VCShipChangeNamePopup@@@@@PdxAnyImpl@@"


class ShipContextTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=1)
        self.status = {"status": "ok", "request_id": 7, "completed_request_id": 7}
        self.client.request.side_effect = lambda command: (
            {"status": "ok", "request_id": 7, "error": 0}
            if command.startswith("interface-callback ") else self.status)
        self.process = Mock(pid=1)
        self.process.allocate.return_value = 0x2000
        self.process.resolve_export.return_value = 0xA100
        self.process.export_owner.return_value = 0xA000

    def submit(self, **kwargs):
        query = data.create_ship_name_context(self.client, 901, process=self.process, **kwargs)
        self.raw = bytearray(self.process.write.call_args.args[1])
        return query

    def returned(self, *, error=0, value_handle=902, type_id=147, kind=data.Kind.OBJECT,
                 pointer=0xABC0, type_name=SHIP_POPUP_ANY):
        h = list(data.REQUEST.unpack_from(self.raw))
        h[1], h[4], h[10], h[11] = error, type_id, 91, kind
        h[13], h[14] = pointer, value_handle
        h[28] = type_name.ljust(192, b"\0")
        data.REQUEST.pack_into(self.raw, 0, *h)
        self.process.read.return_value = bytes(self.raw)

    def test_actual_action22_flags5_source_handle_and_existing_owned_context_callback(self):
        query = self.submit(output_bytes=24, adapter=0xA000)
        h = data.REQUEST.unpack_from(self.raw)
        self.assertEqual(data.OwnedContextKind.SHIP_NAME, 5)
        self.assertEqual((h[0], h[1], h[2], h[3], h[14], h[25]), (len(self.raw), 997, 22, 5, 901, 24))
        self.assertEqual((h[21], h[23]), (0, 0))
        self.assertEqual(self.raw, data._request(data.Action.CREATE_OWNED_CONTEXT, flags=5,
                                                value_handle=901, output_bytes=24))
        self.assertIsInstance(query, data.DataQuery)
        self.assertEqual(query.adapter, 0xA000)
        self.process.export_owner.assert_called_once_with(0xA100)
        self.assertEqual(self.process.resolve_export.call_args.args,
                         (0xA000, "PdxV3DataCreateOwnedContextInterface"))
        self.assertEqual((self.process.allocate.call_count, self.client.request.call_count), (1, 1))

    def test_copied_native_dynamic_type_and_exact_cpp_name_are_not_guessed(self):
        query = self.submit()
        self.returned(type_id=283)
        result = query.read()
        self.assertIsInstance(result, data.DataResult)
        self.assertEqual((result.action, result.flags, result.error, result.type_id), (22, 5, 0, 283))
        native = result.value
        self.assertIsInstance(native, data.NativeObject)
        self.assertEqual((native.pointer, native.type_id, native.value_handle), (0xABC0, 283, 902))
        self.assertEqual(native.native_type_name, SHIP_POPUP_ANY.decode())
        self.assertIn("VCShipChangeNamePopup", native.native_type_name)
        self.assertNotIn("CShipNameChangePopup", native.native_type_name)
        binding = native.binding()
        self.assertEqual((binding.type_id, binding.object, binding.retained), (283, 902, True))
        self.assertEqual((self.process.read.call_count, self.client.request.call_count), (1, 2))
        self.process.free.assert_called_once_with(query.result_address)

    def test_original_partial_error_publishes_new_owner_not_input_handle(self):
        query = self.submit()
        self.returned(error=234, value_handle=902)
        result = query.read()
        self.assertEqual((result.error, result.value_handle, result.value.value_handle), (234, 902, 902))
        self.assertNotEqual(result.value_handle, 901)
        self.assertEqual(result.value.native_type_name, SHIP_POPUP_ANY.decode())
        self.assertEqual(query.adapter, 0xA000)
        self.process.free.assert_called_once_with(0x2000)  # byte storage only; Any remains owned
        self.assertEqual(self.process.allocate.call_count, 1)
        self.assertIs(query.read(), result)
        self.assertEqual(self.process.read.call_count, 1)

    def test_failed_native_initialization_keeps_real_error_no_fabricated_new_owner(self):
        query = self.submit()
        self.returned(error=13, value_handle=0, type_id=-1, kind=data.Kind.EMPTY, pointer=0, type_name=b"")
        result = query.read()
        self.assertEqual((result.error, result.value_handle, result.kind), (13, 0, data.Kind.EMPTY))
        self.assertIsNone(result.value)
        self.assertEqual(self.client.request.call_count, 2)
        self.process.free.assert_called_once()

    def test_explicit_release_uses_actual_data_dll_and_new_handle_only(self):
        query = self.submit()
        self.returned(error=234)
        result = query.read()
        self.process.allocate.return_value = 0x4000
        release_query = query.release_value(process=self.process)
        release_packet = self.process.write.call_args.args[1]
        h = data.REQUEST.unpack_from(release_packet)
        self.assertEqual((h[2], h[14]), (data.Action.RELEASE, result.value_handle))
        self.assertEqual(h[14], 902)
        self.assertEqual(self.process.resolve_export.call_args.args, (0xA000, "PdxV3DataInterface"))
        self.assertIsInstance(release_query, data.DataQuery)
        self.assertEqual(self.process.allocate.call_count, 2)

    def test_pending_retains_same_ship_request_and_does_not_export_output(self):
        query = self.submit()
        self.status["completed_request_id"] = 6
        self.assertIsNone(query.read())
        self.process.read.assert_not_called()
        self.process.free.assert_not_called()
        self.assertEqual((self.process.allocate.call_count, self.client.request.call_count), (1, 2))
        self.assertEqual(data.REQUEST.unpack_from(self.raw)[14], 901)

    def test_missing_owned_context_capability_is_original_error_no_fallback(self):
        fault = LookupError("owned context capability unavailable")
        self.process.resolve_export.side_effect = fault
        with self.assertRaises(LookupError) as caught:
            self.submit()
        self.assertIs(caught.exception, fault)
        self.assertEqual(self.process.resolve_export.call_count, 1)
        self.assertEqual(self.process.resolve_export.call_args.args[1], "PdxV3DataCreateOwnedContextInterface")
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_unknown_delivery_recovers_same_existing_dataquery_without_resubmit(self):
        fault = RuntimeError("original unknown ship context delivery")
        self.client.request.side_effect = fault
        with self.assertRaises(RuntimeError) as caught:
            self.submit()
        self.assertIs(caught.exception, fault)
        query = fault.query
        self.assertIsInstance(query, data.DataQuery)
        self.assertEqual(query.adapter, 0xA000)
        self.assertIsNone(query.request_id)
        self.process.free.assert_not_called()
        self.raw = bytearray(self.process.write.call_args.args[1])
        self.returned(error=234)
        self.client.request.side_effect = lambda _: self.status
        self.assertIs(query.recover(7), query)
        result = query.read()
        self.assertEqual((result.error, result.value_handle), (234, 902))
        self.assertEqual((self.process.allocate.call_count, self.client.request.call_count), (1, 2))
        self.process.free.assert_called_once()

    def test_uint64_source_and_uint32_allocation_representability_before_queue(self):
        for handle in (-1, 1 << 64):
            with self.subTest(handle=handle), self.assertRaises(struct.error):
                data.create_ship_name_context(self.client, handle, process=self.process)
        for size in (-1, 0xffffffff, 1 << 32):
            with self.subTest(size=size), self.assertRaises((ValueError, struct.error)):
                self.submit(output_bytes=size)
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_default_capacity_and_zero_capacity_use_ordinary_encoder(self):
        self.submit()
        self.assertEqual(data.REQUEST.unpack_from(self.raw)[25], 4096)
        self.submit(output_bytes=0)
        self.assertEqual(data.REQUEST.unpack_from(self.raw)[25], 0)


if __name__ == "__main__":
    unittest.main()
