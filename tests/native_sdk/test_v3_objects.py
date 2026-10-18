"""Bulk result layout, caller-sized pages and shared callback ownership."""
from __future__ import annotations

import struct
import unittest
from unittest.mock import Mock

from pdx_runtime import v3_objects as objects
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES


class V3ObjectsTests(unittest.TestCase):
    def _wire_request_fixture(self, command):
        """CPU transport: preserve raw responses through exact V3 wire commands."""
        if command == "world-status":
            return self.world_status()
        words = command.split()
        if len(words) == 3 and words[0] == "interface-callback":
            callback, address = int(words[1], 0), int(words[2], 0)
            self.assertEqual(command, f"interface-callback {callback:#x} {address:#x}")
            return self.interface_callback(callback, address)
        raise AssertionError(f"unexpected V3 fixture wire command: {command!r}")

    def setUp(self):
        self.client = Mock(spec_set=["pid", "request", "status"], pid=123)
        self.world_status = Mock()
        self.interface_callback = Mock()
        self.client.request.side_effect = self._wire_request_fixture
        self.interface_callback.return_value = {
            "status": "ok", "error": 0, "request_id": 9, "completed_request_id": 8}
        self.process = Mock(pid=123)
        self.process.resolve_export.return_value = 0x10000
        self.process.allocate.return_value = 0x20000

    def completed_buffer(self, record: bytes, *, error=0, checked=1, scanned=3,
                         next_index=4, bound=1024, truncated=1):
        initial = bytearray(self.process.write.call_args.args[1])
        header = list(objects.HEADER.unpack_from(initial))
        header[1] = error
        header[11:17] = [int(bool(record)), checked, scanned, next_index, bound, truncated]
        header[18:] = [0x30000, 0x40000, 0x50000]
        objects.HEADER.pack_into(initial, 0, *header)
        initial[header[9]:header[9]+len(record)] = record
        self.process.read.return_value = bytes(initial)
        self.world_status.return_value = {
            "status": "ok", "completed_request_id": 9}

    def test_caller_selected_capacity_and_pending_use_shared_handle(self):
        query = objects.enumerate_objects(self.client, objects.Domain.COUNTRY,
            start_index=17, max_items=5001, capacity=2, process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual(header[4:7], (17, 5001, 2))
        self.assertEqual(len(self.process.write.call_args.args[1]), 96 + 2 * 32)
        self.process.resolve_export.assert_called_once_with("pdx_v3_objects.dll", "PdxV3ObjectsInterface", aliases=V3_EXPORT_ALIASES)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 8}
        self.assertIsNone(query.read())
        self.process.read.assert_not_called()
        self.process.free.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "pending"):
            query.close()

    def test_country_decode_keeps_native_bits_and_truthful_page_metadata(self):
        query = objects.enumerate_objects(self.client, objects.Domain.COUNTRY,
            start_index=1, max_items=1, process=self.process)
        self.completed_buffer(objects.COUNTRY_RECORD.pack(0x1000001, 0x1000001, 0, 1,
                                                         0x60000, 2478004656764))
        result = query.read()
        self.assertTrue(result.valid)
        self.assertEqual(result.records[0].gdp_raw64, 2478004656764)
        self.assertEqual((result.checked_count, result.scanned_count), (1, 3))
        self.assertEqual(result.slot_bound, 1024)
        self.assertEqual(result.next_index, 4)
        self.assertTrue(result.truncated)
        self.assertIs(query.read(), result)
        self.process.free.assert_called_once_with(0x20000)

    def test_state_ids_input_full_generation_and_per_id_error(self):
        query = objects.read_ids(self.client, objects.Domain.STATE,
            [0x1000005, 0xFFFFFFFF], start_index=1, max_items=1, process=self.process)
        initial = self.process.write.call_args.args[1]
        header = objects.HEADER.unpack_from(initial)
        self.assertEqual(header[7], 2)
        self.assertEqual(struct.unpack_from("<2I", initial, header[8]), (0x1000005, 0xFFFFFFFF))
        self.assertEqual(header[9] % 8, 0)
        self.completed_buffer(objects.STATE_RECORD.pack(0xFFFFFFFF, 0, 1168, 0xFFFFFF,
                                                       0x40000, 0, 0), next_index=2, truncated=0)
        result = query.read()
        self.assertTrue(result.valid)
        self.assertFalse(result.records[0].valid)
        self.assertEqual(result.records[0].pointer, result.fallback)
        self.assertEqual(result.records[0].error, 1168)

    def test_building_decode_signed_levels_and_dynamic_type_pointer(self):
        query = objects.read_ids(self.client, objects.Domain.BUILDING, [87], process=self.process,
                                 adapter=0xA0000)
        self.completed_buffer(objects.BUILDING_RECORD.pack(87, 87, 0, 87, 0x60000,
                                                          0x1000005, -2, 0x70000))
        record = query.read().records[0]
        self.assertIsInstance(record, objects.Building)
        self.assertEqual((record.state_id, record.levels, record.type_pointer),
                         (0x1000005, -2, 0x70000))
        self.process.resolve_export.assert_called_once_with(0xA0000, "PdxV3ObjectsInterface", aliases=V3_EXPORT_ALIASES)

    def test_pop_bulk_full_id_and_dynamic_type_are_typed_not_building_layout(self):
        query = objects.read_ids(self.client, objects.Domain.POP, [0x3000064],
                                 process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[2], header[10], header[17]), (4, 32, 136))
        self.completed_buffer(objects.POP_RECORD.pack(0x3000064, 0x3000064, 0, 100,
                                                     0x60000, 0x70000))
        result = query.read()
        self.assertIsInstance(result.records[0], objects.Pop)
        self.assertEqual(result.records[0].type_pointer, 0x70000)
        self.assertEqual(result.records[0].resolved_id, 0x3000064)
        self.process.free.assert_called_once_with(0x20000)

    def test_market_borrowed_origin_and_full_owner_id(self):
        query = objects.enumerate_objects(self.client, objects.Domain.MARKET,
            start_index=0, max_items=2, capacity=1, process=self.process)
        self.completed_buffer(objects.MARKET_RECORD.pack(0x2000003, 0x2000003, 0, 3,
                                                        0x60000, 0x1000001, 0),
                              checked=1, scanned=4, next_index=4, bound=128)
        result = query.read()
        record = result.records[0]
        self.assertIsInstance(record, objects.Market)
        self.assertEqual((record.pointer, record.owner_id), (0x60000, 0x1000001))
        self.assertEqual((result.capacity, result.next_index, result.slot_bound), (1, 4, 128))
        self.assertTrue(result.truncated)

    def test_pop_and_market_preserve_invalid_record_error(self):
        for domain, layout, fields in [
            (objects.Domain.POP, objects.POP_RECORD, (0x40000, 0)),
            (objects.Domain.MARKET, objects.MARKET_RECORD, (0x40000, 0, 0)),
        ]:
            query = objects.read_ids(self.client, domain, [0xFFFFFFFF], process=self.process)
            self.completed_buffer(layout.pack(0xFFFFFFFF, 0, 1168, 0xFFFFFF, *fields))
            result = query.read()
            self.assertTrue(result.valid)
            self.assertFalse(result.records[0].valid)
            self.assertEqual(result.records[0].pointer, result.fallback)

    def test_pop_details_explicit_96byte_layout_keeps_independent_errors(self):
        query = objects.read_pop_details(self.client, [0x2000000], process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[2], header[3], header[10]), (4, 4, 96))
        native = objects.POP_DETAILS_RECORD.pack(0x2000000, 0x2000000, 0, 0,
            0x60000, 0x70000, 30, 10, 20, 5, 0xFFFFFFFF, 0, 0, 1168, 0, 0x80000, 0x40000)
        self.completed_buffer(native)
        record = query.read().records[0]
        self.assertIsInstance(record, objects.PopDetails)
        self.assertEqual((record.total_size, record.workforce, record.dependents), (30, 10, 20))
        self.assertTrue(record.valid)
        self.assertEqual(record.country_error, 1168)
        self.assertEqual(record.country_id, 0xFFFFFFFF)

    def test_legacy_pop32_and_explicit_details96_do_not_change_each_other(self):
        legacy = objects._buffer(objects.Domain.POP, objects.READ_IDS, 0, 1, 1, (0,))
        details = objects._buffer(objects.Domain.POP, objects.READ_POP_DETAILS, 0, 1, 1, (0,))
        self.assertEqual((objects.HEADER.unpack_from(legacy)[10], objects.HEADER.unpack_from(details)[10]), (32, 96))
        objects.enumerate_pop_details(self.client, start_index=9, max_items=10000,
                                      capacity=2, process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[3], header[4], header[5], header[6], header[10]), (3, 9, 10000, 2, 96))

    def test_market_goods_actual_pointer_input_and_fixedpoint_rawbits(self):
        query = objects.read_market_goods(self.client, [(0x1000000, 0x70000)], process=self.process)
        initial = self.process.write.call_args.args[1]
        header = objects.HEADER.unpack_from(initial)
        self.assertEqual((header[2], header[3], header[10]), (5, 5, 56))
        self.assertEqual(objects.MARKET_GOODS_INPUT.unpack_from(initial, header[8]), (0x1000000, 0, 0x70000))
        native = objects.MARKET_GOODS_RECORD.pack(0x1000000, 0x1000000, 0, 0,
            0x60000, 0x70000, 0xFFFFFFFFFFFE7960, 7, 0xFFFFFFFF, 0, 0)
        self.completed_buffer(native)
        record = query.read().records[0]
        self.assertTrue(record.valid)
        self.assertEqual(record.price_raw64_signed, -100000)
        self.assertEqual((record.goods_index, record.definition_index), (7, 0xFFFFFFFF))

    def test_market_goods_definition_page_is_not_refdb_fullid_enumeration(self):
        query = objects.enumerate_market_goods(self.client, 0, start_index=3, max_items=4,
                                               capacity=1, process=self.process)
        initial = self.process.write.call_args.args[1]
        header = objects.HEADER.unpack_from(initial)
        self.assertEqual((header[3], header[7], header[10]), (6, 1, 56))
        self.assertEqual(struct.unpack_from("<I", initial, header[8])[0], 0)
        native = objects.MARKET_GOODS_RECORD.pack(0, 0, 0, 0, 0x60000,
                                                0x70000, 0, 3, 3, 0, 0)
        self.completed_buffer(native, next_index=4, bound=52, truncated=1)
        result = query.read()
        self.assertTrue(result.records[0].valid)
        self.assertEqual((result.records[0].price_raw64, result.slot_bound), (0, 52))

    def test_wrong_new_domain_operation_and_input_fail_before_queue(self):
        with self.assertRaises(ValueError):
            objects._buffer(objects.Domain.STATE, objects.READ_POP_DETAILS, 0, 1, 1, (0,))
        with self.assertRaises(ValueError):
            objects.read_market_goods(self.client, [(0, -1)], process=self.process)
        self.interface_callback.assert_not_called()

    def test_market_details_preserve_price_prefix_and_native_order_raw64(self):
        query = objects.read_market_goods(self.client, [(0, 0x70000)], details=True,
                                          process=self.process)
        initial = self.process.write.call_args.args[1]
        header = objects.HEADER.unpack_from(initial)
        self.assertEqual((header[3], header[10]), (7, 104))
        native = objects.MARKET_GOODS_DETAILS_RECORD.pack(0, 0, 0, 0,
            0x60000, 0x70000, 5459525, 3, 0xFFFFFFFF, 0, 0,
            10000001, 20000002, 30000003, 40000004, 0, 0, 0, 0)
        self.completed_buffer(native)
        record = query.read().records[0]
        self.assertIsInstance(record, objects.MarketGoodsDetails)
        self.assertIsInstance(record, objects.MarketGoodsValue)
        self.assertTrue(record.valid)
        self.assertEqual(record.price_raw64, 5459525)
        self.assertEqual((record.buy_orders_raw64, record.sell_orders_raw64,
                          record.imports_raw64, record.exports_raw64),
                         (10000001, 20000002, 30000003, 40000004))
        self.process.free.assert_called_once_with(0x20000)

    def test_market_details_enum_uses_actual_definition_page_operation(self):
        query = objects.enumerate_market_goods(self.client, 0, start_index=50,
            max_items=53, capacity=2, details=True, process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[3], header[4], header[5], header[6], header[10]), (8, 50, 53, 2, 104))
        self.world_status.return_value = {"status": "ok", "completed_request_id": 8}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()

    def test_market_details_field_errors_are_not_valid_zero_values(self):
        query = objects.read_market_goods(self.client, [(0xFFFFFFFF, 0x70000)],
                                          details=True, process=self.process)
        native = objects.MARKET_GOODS_DETAILS_RECORD.pack(0xFFFFFFFF, 0, 1168, 0,
            0x40000, 0x70000, 0, 3, 0xFFFFFFFF, 1168, 0,
            0, 0, 0, 0, 1168, 1168, 1168, 1168)
        self.completed_buffer(native)
        record = query.read().records[0]
        self.assertFalse(record.valid)
        self.assertEqual((record.buy_orders_error, record.sell_orders_error,
                          record.imports_error, record.exports_error), (1168,) * 4)

    def test_military_formation_full_generation_id_owner_native_record(self):
        query = objects.read_ids(self.client, objects.Domain.MILITARY_FORMATION,
                                 [0x2000000], process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[2], header[3], header[10]), (6, 2, 40))
        self.completed_buffer(objects.MILITARY_FORMATION_RECORD.pack(
            0x2000000, 0x2000000, 0, 0, 0x60000, 0x1000001, 0, 0x70000))
        record = query.read().records[0]
        self.assertIsInstance(record, objects.MilitaryFormation)
        self.assertTrue(record.valid)
        self.assertEqual((record.resolved_id, record.owner_country_id,
                          record.owner_country_pointer), (0x2000000, 0x1000001, 0x70000))
        self.process.free.assert_called_once_with(0x20000)

    def test_formation_owner_failure_does_not_invalidate_real_formation(self):
        query = objects.enumerate_objects(self.client, objects.Domain.MILITARY_FORMATION,
            start_index=0, max_items=1, process=self.process)
        self.completed_buffer(objects.MILITARY_FORMATION_RECORD.pack(
            0, 0, 0, 0, 0x60000, 0xFFFFFFFF, 1168, 0x40000))
        record = query.read().records[0]
        self.assertTrue(record.valid)
        self.assertEqual(record.owner_country_error, 1168)
        self.assertEqual(record.owner_country_id, 0xFFFFFFFF)

    def test_formation_invalid_id_record_preserves_native_fallback(self):
        query = objects.read_ids(self.client, objects.Domain.MILITARY_FORMATION,
                                 [0xFFFFFFFF], process=self.process)
        self.completed_buffer(objects.MILITARY_FORMATION_RECORD.pack(
            0xFFFFFFFF, 0, 1168, 0xFFFFFF, 0x40000, 0xFFFFFFFF, 1168, 0))
        record = query.read().records[0]
        self.assertFalse(record.valid)
        self.assertEqual(record.pointer, 0x40000)
        self.assertEqual(record.owner_country_pointer, 0)

    def test_war_native_dp_country_leader_join_record(self):
        query = objects.read_ids(self.client, objects.Domain.WAR, [0x3000000], process=self.process)
        self.assertEqual(objects.HEADER.unpack_from(self.process.write.call_args.args[1])[10], 72)
        self.completed_buffer(objects.WAR_RECORD.pack(0x3000000, 0x3000000, 0, 0,
            0x60000, 0x2000005, 0, 0x70000, 0x1000001, 3, 0, 0, 0x80000, 0x90000))
        record = query.read().records[0]
        self.assertIsInstance(record, objects.War)
        self.assertTrue(record.valid)
        self.assertEqual((record.diplomatic_play_id, record.attacker_leader_id,
                          record.defender_leader_id), (0x2000005, 0x1000001, 3))

    def test_valid_war_invalid_dp_preserves_separate_relation_failure(self):
        query = objects.enumerate_objects(self.client, objects.Domain.WAR,
                                         start_index=0, max_items=1, process=self.process)
        self.completed_buffer(objects.WAR_RECORD.pack(0, 0, 0, 0, 0x60000,
            0xFFFFFFFF, 1168, 0x40000, 0xFFFFFFFF, 0xFFFFFFFF, 1168, 1168, 0, 0))
        record = query.read().records[0]
        self.assertTrue(record.valid)
        self.assertEqual(record.diplomatic_play_error, 1168)
        self.assertEqual((record.attacker_leader_id, record.defender_leader_id), (0xFFFFFFFF,) * 2)
        self.assertEqual((record.attacker_leader_pointer, record.defender_leader_pointer), (0, 0))

    def test_war_one_invalid_leader_does_not_replace_other(self):
        query = objects.read_ids(self.client, objects.Domain.WAR, [1], process=self.process)
        self.completed_buffer(objects.WAR_RECORD.pack(1, 1, 0, 1, 0x60000,
            5, 0, 0x70000, 0xFFFFFFFF, 3, 1168, 0, 0x40000, 0x90000))
        record = query.read().records[0]
        self.assertTrue(record.valid)
        self.assertEqual((record.attacker_leader_error, record.defender_leader_error), (1168, 0))
        self.assertEqual(record.defender_leader_pointer, 0x90000)

    def test_diplomatic_play_direct_domain_full_id_and_two_leaders(self):
        query = objects.read_ids(self.client, objects.Domain.DIPLOMATIC_PLAY,
                                 [0x3000005], process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[2], header[3], header[10]), (8, 2, 56))
        self.completed_buffer(objects.DIPLOMATIC_PLAY_RECORD.pack(0x3000005, 0x3000005, 0, 5,
            0x60000, 0x1000001, 3, 0, 0, 0x70000, 0x80000))
        record = query.read().records[0]
        self.assertIsInstance(record, objects.DiplomaticPlay)
        self.assertTrue(record.valid)
        self.assertEqual((record.attacker_leader_id, record.defender_leader_id), (0x1000001, 3))

    def test_diplomatic_play_invalid_one_leader_keeps_native_play_valid(self):
        query = objects.enumerate_objects(self.client, objects.Domain.DIPLOMATIC_PLAY,
            start_index=0, max_items=1, process=self.process)
        self.completed_buffer(objects.DIPLOMATIC_PLAY_RECORD.pack(0, 0, 0, 0,
            0x60000, 1, 0xFFFFFFFF, 0, 1168, 0x70000, 0x40000))
        record = query.read().records[0]
        self.assertTrue(record.valid)
        self.assertEqual((record.attacker_leader_error, record.defender_leader_error), (0, 1168))
        self.assertEqual(record.attacker_leader_pointer, 0x70000)

    def test_character_native_leaf_value_full_generation_and_domain_layout(self):
        query = objects.enumerate_objects(self.client, objects.Domain.CHARACTER,
            start_index=7, max_items=1, process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[2], header[3], header[4], header[10]), (9, 1, 7, 32))
        self.completed_buffer(objects.CHARACTER_RECORD.pack(
            0x2000007, 0x2000007, 0, 7, 0x60000, 1, 0), next_index=8)
        result = query.read()
        record = result.records[0]
        self.assertIsInstance(record, objects.Character)
        self.assertTrue(record.valid)
        self.assertTrue(record.is_female_valid)
        self.assertEqual((record.resolved_id, record.is_female), (0x2000007, 1))
        self.assertIs(query.read(), result)
        self.process.free.assert_called_once_with(0x20000)

    def test_character_zero_false_and_independent_field_error_are_distinct(self):
        for field_error in (0, 574):
            query = objects.read_ids(self.client, objects.Domain.CHARACTER, [0],
                                     process=self.process)
            self.completed_buffer(objects.CHARACTER_RECORD.pack(
                0, 0, 0, 0, 0x60000, 0, field_error))
            record = query.read().records[0]
            self.assertTrue(record.valid)
            self.assertEqual(record.is_female, 0)
            self.assertEqual(record.is_female_valid, field_error == 0)

    def test_character_invalid_and_stale_full_id_keep_field_failure_not_valid_false(self):
        for full_id in (0xFFFFFFFF, 0x01000000):
            query = objects.read_ids(self.client, objects.Domain.CHARACTER, [full_id],
                                     process=self.process)
            initial = self.process.write.call_args.args[1]
            header = objects.HEADER.unpack_from(initial)
            self.assertEqual(struct.unpack_from("<I", initial, header[8])[0], full_id)
            self.completed_buffer(objects.CHARACTER_RECORD.pack(
                full_id, 0, 1168, full_id & 0xFFFFFF, 0x40000, 0, 1168))
            record = query.read().records[0]
            self.assertFalse(record.valid)
            self.assertFalse(record.is_female_valid)
            self.assertEqual((record.pointer, record.error, record.is_female_error),
                             (0x40000, 1168, 1168))

    def definition_native_result(self, records, *, error=0, next_index=1,
                                 definition_count=42, required_text=None, scanned=None):
        data = bytearray(self.process.write.call_args.args[1])
        header = list(objects.HEADER.unpack_from(data))
        options = list(objects.DEFINITION_OPTIONS.unpack_from(data, header[8]))
        used = 0
        for i, (requested, resolved, object_error, group_error, key, group_key) in enumerate(records):
            key_error = object_error
            group_key_error = object_error or group_error
            key_at = options[0] + used if not key_error else 0
            if not key_error:
                data[key_at:key_at + len(key)] = key
                used += len(key)
            group_at = options[0] + used if not group_key_error else 0
            if not group_key_error:
                data[group_at:group_at + len(group_key)] = group_key
                used += len(group_key)
            objects.LAW_DEFINITION_RECORD.pack_into(data, header[9] + i * header[10],
                requested, resolved, object_error, key_error, 0x60000 if not object_error else 0x40000,
                2 if not group_error and not object_error else -1, group_error or object_error,
                0x70000 if not object_error else 0, key_at, len(key) if not key_error else 0,
                len(key) if not key_error else 0, group_at, len(group_key) if not group_key_error else 0,
                len(group_key) if not group_key_error else 0, group_key_error, 0)
        options[2] = used
        options[4] = used if required_text is None else required_text
        options[5] = options[0] + options[4]
        objects.DEFINITION_OPTIONS.pack_into(data, header[8], *options)
        header[1] = error
        header[11:17] = [len(records), len(records) if scanned is None else scanned,
            len(records) if scanned is None else scanned, next_index, definition_count,
            int(error == 234 or next_index < definition_count)]
        objects.HEADER.pack_into(data, 0, *header)
        self.process.read.return_value = bytes(data)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 9}

    def test_law_definition_explicit_ordinal_native_key_and_group_utf8(self):
        query = objects.enumerate_law_definitions(self.client, start_index=0, max_items=2,
            text_capacity=512, process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[0], header[2], header[3], header[7], header[10]), (96, 10, 9, 0, 72))
        self.definition_native_result([(0, 0, 0, 0, "law_自由".encode(), b"lawgroup_power_structure")])
        result = query.read()
        record = result.records[0]
        self.assertIsInstance(record, objects.LawDefinition)
        self.assertTrue(record.valid)
        self.assertEqual((record.key, record.group_key), ("law_自由", "lawgroup_power_structure"))
        self.assertEqual((record.group_index, record.group_error, record.group_pointer), (2, 0, 0x70000))
        self.assertEqual(result.slot_bound, 42)  # Definition count in explicit operation9.
        self.assertIs(query.read(), result)
        self.process.free.assert_called_once_with(0x20000)

    def test_law_definition_negative_index_is_not_refdb_sentinel_mask(self):
        query = objects.read_law_definition_indices(self.client, [0, -1, 0x01000000],
            start_index=1, max_items=1, text_capacity=0, process=self.process)
        initial = self.process.write.call_args.args[1]
        header = objects.HEADER.unpack_from(initial)
        self.assertEqual(struct.unpack_from("<3i", initial, header[8] + 32), (0, -1, 0x01000000))
        self.definition_native_result([(-1, -1, 1168, 1168, b"", b"")], next_index=2)
        record = query.read().records[0]
        self.assertFalse(record.valid)
        self.assertEqual((record.pointer, record.key_error, record.group_key_error), (0x40000, 1168, 1168))
        self.assertEqual(record.key_bytes, b"")

    def test_law_short_text_preserves_complete_prefix_and_unwritten_next_index(self):
        query = objects.enumerate_law_definitions(self.client, start_index=0, max_items=2,
            text_capacity=8, process=self.process)
        self.definition_native_result([(0, 0, 0, 0, b"abc", b"def")], error=234,
            next_index=1, required_text=20, scanned=2)
        result = query.read()
        self.assertEqual((result.error, result.returned_count, result.next_index), (234, 1, 1))
        self.assertEqual((result.records[0].key_bytes, result.records[0].group_key_bytes), (b"abc", b"def"))
        self.assertTrue(result.truncated)
        self.assertGreater(result.definition_options.required_buffer_size, result.buffer_size)
        self.assertEqual(result.definition_options.text_bytes_required, 20)
        self.process.free.assert_called_once_with(0x20000)

    def test_law_definition_pending_keeps_arena_until_callback_completion(self):
        query = objects.enumerate_law_definitions(self.client, start_index=5, max_items=2,
            text_capacity=10000, process=self.process)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 8}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        self.process.read.assert_not_called()

    def test_law_definition_decoder_rejects_partial_key_bytes(self):
        query = objects.enumerate_law_definitions(self.client, start_index=0, max_items=1,
            text_capacity=128, process=self.process)
        self.definition_native_result([(0, 0, 0, 0, b"law_a", b"group")])
        data = bytearray(self.process.read.return_value)
        header = objects.HEADER.unpack_from(data)
        struct.pack_into("<I", data, header[9] + 48, 6)  # required key size != copied5B.
        self.process.read.return_value = bytes(data)
        with self.assertRaisesRegex(ValueError, "partial"):
            query.read()

    def test_law_definitions_reject_full_uint32_id_and_refdb_operations(self):
        with self.assertRaises(ValueError):
            objects.read_law_definition_indices(self.client, [0xFFFFFFFF], process=self.process)
        with self.assertRaises(ValueError):
            objects.enumerate_objects(self.client, objects.Domain.LAW_DEFINITION,
                start_index=0, max_items=1, process=self.process)
        self.interface_callback.assert_not_called()

    def test_law_wrong_typed_database_result_is_not_a_successful_catalog(self):
        query = objects.enumerate_law_definitions(self.client, start_index=0, max_items=5,
            text_capacity=1024, process=self.process)
        self.definition_native_result([], error=5023, next_index=0, definition_count=0)
        result = query.read()
        self.assertFalse(result.valid)
        self.assertEqual((result.error, result.returned_count, result.records), (5023, 0, ()))
        self.process.free.assert_called_once_with(0x20000)

    def technology_native_result(self, requested=0, resolved=0, object_error=0,
                                 key=b"mechanized_tools", category_index=1,
                                 category_error=0, category_token=501, category_token_error=0,
                                 category_is_default=0, error=0, next_index=1, definition_count=100,
                                 required_text=None):
        data = bytearray(self.process.write.call_args.args[1])
        header = list(objects.HEADER.unpack_from(data))
        options = list(objects.DEFINITION_OPTIONS.unpack_from(data, header[8]))
        copied = len(key) if not object_error else 0
        at = options[0] if not object_error else 0
        if copied:
            data[at:at + copied] = key
        objects.TECHNOLOGY_DEFINITION_RECORD.pack_into(data, header[9], requested, resolved,
            object_error, object_error, 0x60000 if not object_error else 0x40000,
            category_index, category_error, 0x70000 if not object_error else 0,
            category_token, category_token_error, category_is_default, at, copied, copied)
        options[2] = copied
        options[4] = copied if required_text is None else required_text
        options[5] = options[0] + options[4]
        objects.DEFINITION_OPTIONS.pack_into(data, header[8], *options)
        header[1] = error
        header[11:17] = [1, 1, 1, next_index, definition_count, int(next_index < definition_count)]
        objects.HEADER.pack_into(data, 0, *header)
        self.process.read.return_value = bytes(data)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 9}

    def test_technology_definition_native_key_category_and_separate_64byte_layout(self):
        query = objects.enumerate_technology_definitions(self.client, start_index=0, max_items=2,
            text_capacity=100, process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[0], header[2], header[3], header[10]), (96, 11, 9, 64))
        self.technology_native_result()
        result = query.read()
        record = result.records[0]
        self.assertIsInstance(record, objects.TechnologyDefinition)
        self.assertTrue(record.valid)
        self.assertTrue(record.has_category)
        self.assertEqual((record.key, record.category_index, record.category_token), ("mechanized_tools", 1, 501))
        self.assertEqual(record.category_pointer, 0x70000)
        self.assertIs(query.read(), result)
        self.process.free.assert_called_once_with(0x20000)

    def test_technology_default_category_is_not_a_real_category_token(self):
        query = objects.read_technology_definition_indices(self.client, [0], process=self.process)
        self.technology_native_result(category_index=-1, category_error=1168,
            category_token=0, category_token_error=1168, category_is_default=1)
        record = query.read().records[0]
        self.assertTrue(record.valid)
        self.assertFalse(record.has_category)
        self.assertEqual((record.category_is_default, record.category_error, record.category_token_error), (1, 1168, 1168))
        self.assertEqual(record.key, "mechanized_tools")

    def test_technology_invalid_signed_ordinal_retains_native_fallback_errors(self):
        query = objects.read_technology_definition_indices(self.client, [-1, 0x01000000],
            max_items=1, text_capacity=0, process=self.process)
        initial = self.process.write.call_args.args[1]
        header = objects.HEADER.unpack_from(initial)
        self.assertEqual(struct.unpack_from("<2i", initial, header[8] + 32), (-1, 0x01000000))
        self.technology_native_result(requested=-1, resolved=-1, object_error=1168,
            key=b"", category_index=-1, category_error=1168, category_token=0, category_token_error=1168)
        record = query.read().records[0]
        self.assertFalse(record.valid)
        self.assertEqual((record.pointer, record.key_error, record.category_error), (0x40000, 1168, 1168))
        self.assertEqual(record.key_bytes, b"")

    def test_technology_whole_text_short_prefix_does_not_skip_definition(self):
        query = objects.enumerate_technology_definitions(self.client, start_index=0, max_items=2,
            text_capacity=8, process=self.process)
        self.technology_native_result(key=b"tech_a", error=234, next_index=1, required_text=40)
        result = query.read()
        self.assertEqual((result.error, result.returned_count, result.next_index), (234, 1, 1))
        self.assertEqual(result.records[0].key, "tech_a")
        self.assertGreater(result.definition_options.required_buffer_size, result.buffer_size)
        self.assertEqual(result.definition_options.text_bytes_required, 40)

    def test_technology_refdb_and_uint32_generation_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            objects.read_ids(self.client, objects.Domain.TECHNOLOGY_DEFINITION, [0], process=self.process)
        with self.assertRaises(ValueError):
            objects.read_technology_definition_indices(self.client, [0xFFFFFFFF], process=self.process)
        self.interface_callback.assert_not_called()

    def test_empty_and_zero_capacity_are_explicit_requests(self):
        objects.read_ids(self.client, objects.Domain.COUNTRY, [], process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[5], header[6], header[7], header[17]), (0, 0, 0, 96))
        objects.enumerate_objects(self.client, objects.Domain.BUILDING, start_index=0,
                                 max_items=10, capacity=0, process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[5], header[6], header[17]), (10, 0, 96))

    def test_invalid_inputs_fail_before_queue(self):
        for call in [
            lambda: objects.enumerate_objects(self.client, 99, start_index=0, max_items=1),
            lambda: objects.enumerate_objects(self.client, objects.Domain.STATE, start_index=-1, max_items=1),
            lambda: objects.enumerate_objects(self.client, objects.Domain.STATE, start_index=0, max_items=-1),
            lambda: objects.read_ids(self.client, objects.Domain.BUILDING, [0x100000000]),
        ]:
            with self.assertRaises(ValueError):
                call()
        self.interface_callback.assert_not_called()

    def test_invalid_returned_size_rejected_without_overread(self):
        query = objects.enumerate_objects(self.client, objects.Domain.STATE,
                                         start_index=0, max_items=1, process=self.process)
        self.completed_buffer(b"")
        buffer = bytearray(self.process.read.return_value)
        struct.pack_into("<I", buffer, 11 * 4, 2)  # More records than allocated capacity.
        self.process.read.return_value = bytes(buffer)
        with self.assertRaisesRegex(ValueError, "layout"):
            query.read()
        self.process.free.assert_not_called()

    def test_unknown_delivery_keeps_input_and_recover_does_not_resubmit(self):
        self.interface_callback.side_effect = BridgeError("ReadFile", 109)
        with self.assertRaises(BridgeError) as captured:
            objects.read_ids(self.client, objects.Domain.COUNTRY, [1], process=self.process)
        query = captured.exception.query
        self.process.free.assert_not_called()
        self.completed_buffer(objects.COUNTRY_RECORD.pack(1, 1, 0, 1, 0x60000, 7))
        self.assertTrue(query.recover(9).read().valid)
        self.interface_callback.assert_called_once()


    def test_interest_group_full_generation_and_independent_country40(self):
        query = objects.read_ids(self.client, objects.Domain.INTEREST_GROUP,
            [0x3A000321], process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[2], header[3], header[10]), (12, 2, 40))
        self.completed_buffer(objects.INTEREST_GROUP_RECORD.pack(
            0x3A000321, 0x3A000321, 0, 0x321, 0x60000,
            0x07000009, 0, 0x70000))
        record = query.read().records[0]
        self.assertIsInstance(record, objects.InterestGroup)
        self.assertTrue(record.valid and record.has_country)
        self.assertEqual((record.requested_id, record.country_id), (0x3A000321, 0x07000009))

    def test_interest_group_country_failure_does_not_invalidate_real_ig(self):
        query = objects.enumerate_objects(self.client, objects.Domain.INTEREST_GROUP,
            start_index=0, max_items=1, process=self.process)
        self.completed_buffer(objects.INTEREST_GROUP_RECORD.pack(
            5, 5, 0, 5, 0x60000, 0xFFFFFFFF, 1168, 0x40000))
        record = query.read().records[0]
        self.assertTrue(record.valid)
        self.assertFalse(record.has_country)
        self.assertEqual((record.country_error, record.country_pointer), (1168, 0x40000))

    def completed_ig_text_buffer(self, fields, name=b"", *, error=0,
                                 next_index=1, required=None, count=1):
        data = bytearray(self.process.write.call_args.args[1])
        header = list(objects.HEADER.unpack_from(data))
        options = list(objects.DEFINITION_OPTIONS.unpack_from(data, header[8]))
        required = len(name) if required is None else required
        options[2:] = [len(name), 0, required, options[0] + required]
        header[1], header[11] = error, count
        header[12:17] = [count, 1, next_index, 2048, int(bool(error))]
        header[18:] = [0x30000, 0x40000, 0x50000]
        objects.HEADER.pack_into(data, 0, *header)
        objects.DEFINITION_OPTIONS.pack_into(data, header[8], *options)
        data[options[0]:options[0] + len(name)] = name
        if count:
            fields = list(fields)
            fields[9] = options[0] if fields[8] == 0 else 0
            fields[10] = len(name)
            objects.INTEREST_GROUP_DETAILS_RECORD.pack_into(data, header[9], *fields)
        self.process.read.return_value = bytes(data)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 9}
        return data

    def test_interest_group_native_display_name56_not_a_definition_key(self):
        query = objects.read_interest_group_details(self.client, [0xFE000008],
            text_capacity=256, process=self.process)
        initial = self.process.write.call_args.args[1]
        header = objects.HEADER.unpack_from(initial)
        self.assertEqual((header[2], header[3], header[10]), (12, 12, 56))
        self.assertEqual(struct.unpack_from("<I", initial, header[8] + 32)[0], 0xFE000008)
        name = "#v £ig_icon 农村民众#!".encode("utf-8")
        self.completed_ig_text_buffer((0xFE000008, 0xFE000008, 0, 8, 0x60000,
            1, 0, 0x70000, 0, 0, 0, 0), name)
        result = query.read();record = result.records[0]
        self.assertIsInstance(record, objects.InterestGroupDetails)
        self.assertTrue(record.has_name and record.has_country)
        self.assertEqual((record.name_utf8, record.name), (name, name.decode("utf-8")))
        self.assertEqual(result.interest_group_options.text_bytes_written, len(name))
        self.assertIsNone(result.definition_options)
        self.process.free.assert_called_once_with(0x20000)

    def test_interest_group_invalid_and_stale_ids_keep_native_fallback_errors(self):
        for full_id in (0xFFFFFFFF, 0x9E000005):
            self.setUp()
            query = objects.read_interest_group_details(self.client, [full_id], process=self.process)
            self.completed_ig_text_buffer((full_id, 0, 1168, full_id & 0xFFFFFF,
                0x40000, 0xFFFFFFFF, 1168, 0, 1168, 0, 0, 0))
            record = query.read().records[0]
            self.assertFalse(record.valid or record.has_name or record.has_country)
            self.assertEqual((record.pointer, record.name, record.name_error), (0x40000, None, 1168))

    def test_interest_group_empty_native_name_is_valid_not_missing(self):
        query = objects.read_interest_group_details(self.client, [8], text_capacity=0, process=self.process)
        self.completed_ig_text_buffer((8, 8, 0, 8, 0x60000, 1, 0, 0x70000, 0, 0, 0, 0))
        record = query.read().records[0]
        self.assertTrue(record.has_name)
        self.assertEqual(record.name, "")

    def test_interest_group_name_failure_remains_independent(self):
        query = objects.read_interest_group_details(self.client, [8], process=self.process)
        self.completed_ig_text_buffer((8, 8, 0, 8, 0x60000, 1, 0, 0x70000, 574, 0, 0, 0))
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_country)
        self.assertFalse(record.has_name)
        self.assertEqual(record.name_error, 574)

    def test_interest_group_whole_name234_does_not_skip_unwritten_input(self):
        query = objects.read_interest_group_details(self.client, [0x1000008],
            text_capacity=2, process=self.process)
        self.completed_ig_text_buffer((), error=234, next_index=0, required=25, count=0)
        result = query.read()
        self.assertEqual((result.error, result.returned_count, result.next_index), (234, 0, 0))
        self.assertTrue(result.truncated)
        self.assertEqual((result.interest_group_options.text_bytes_required,
                         result.interest_group_options.text_bytes_written), (25, 0))
        self.assertIs(query.read(), result)
        self.interface_callback.assert_called_once()

    def test_interest_group_malformed_name_arena_is_rejected(self):
        query = objects.read_interest_group_details(self.client, [8], process=self.process)
        data = self.completed_ig_text_buffer((8, 8, 0, 8, 0x60000, 1, 0, 0x70000, 0, 0, 0, 0), b"name")
        header = objects.HEADER.unpack_from(data)
        struct.pack_into("<I", data, header[9] + 48, 5) # name_bytes past complete written arena.
        with self.assertRaisesRegex(ValueError, "complete text arena"):
            objects.ObjectsResult.from_bytes(bytes(data))
        # The shared handle still sees the original, valid known-complete mock
        # snapshot. This decoder test does not assume decode failures auto-free.
        query.close()
        self.process.free.assert_called_once_with(0x20000)

    def test_interest_group_detail_pending_keeps_native_owned_request_buffer(self):
        query = objects.enumerate_interest_group_details(self.client, start_index=7,
            max_items=10, capacity=1, text_capacity=0, process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[3], header[4:7]), (11, (7, 10, 1)))
        self.world_status.return_value = {"status": "ok", "completed_request_id": 8}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "pending"):
            query.close()

    def test_interest_group_rejects_wrong_operations_and_non_uint32_ids(self):
        for bad in (-1, 1 << 32, "8"):
            with self.assertRaises(ValueError):
                objects.read_interest_group_details(self.client, [bad], process=self.process)
        with self.assertRaises(ValueError):
            objects._definition_buffer(objects.ENUMERATE_DEFINITIONS, (), 0, 1, 1, 64,
                domain=objects.Domain.INTEREST_GROUP)
        with self.assertRaises(ValueError):
            objects._layout(objects.Domain.TECHNOLOGY_DEFINITION, objects.READ_INTEREST_GROUP_DETAILS)
        self.interface_callback.assert_not_called()

    def test_shipping_lane_current_domain_full_generation_and128_layout(self):
        query = objects.read_ids(self.client, objects.Domain.SHIPPING_LANE,
            [0xC1000011], process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[2], header[3], header[10]), (13, 2, 128))
        fields = (0xC1000011, 0xC1000011, 0, 17, 0x60000,
            0x02000001, 0, 0x70000, 0x03000005, 0, 0x80000,
            0x04000006, 0, 0x90000, 0x01000003, 0, 0xA0000,
            0x01000004, 0, 0xB0000, 230001, 85000, 0, 0)
        self.completed_buffer(objects.SHIPPING_LANE_RECORD.pack(*fields))
        record = query.read().records[0]
        self.assertIsInstance(record, objects.ShippingLane)
        self.assertTrue(record.valid and record.has_country and record.has_begin_market and record.has_end_market)
        self.assertEqual((record.requested_id, record.country_id), (0xC1000011, 0x02000001))
        self.assertEqual((record.convoy_cost_raw64, record.effectiveness_raw64), (230001, 85000))

    def test_shipping_lane_zero_full_id_is_valid_not_missing(self):
        query = objects.read_ids(self.client, objects.Domain.SHIPPING_LANE, [0], process=self.process)
        self.completed_buffer(objects.SHIPPING_LANE_RECORD.pack(
            0, 0, 0, 0, 0x60000, 1, 0, 0x70000,
            5, 0, 0x80000, 6, 0, 0x90000, 3, 0, 0xA0000,
            4, 0, 0xB0000, 0, 0, 0, 0))
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_country)
        self.assertEqual((record.requested_id, record.convoy_cost_error, record.effectiveness_error), (0, 0, 0))

    def test_shipping_lane_invalid_and_stale_keep_native_fallback(self):
        for full_id in (0xFFFFFFFF, 0xFA000011):
            self.setUp()
            query = objects.read_ids(self.client, objects.Domain.SHIPPING_LANE, [full_id], process=self.process)
            self.completed_buffer(objects.SHIPPING_LANE_RECORD.pack(
                full_id, 0, 1168, full_id & 0xFFFFFF, 0x40000,
                *([0xFFFFFFFF, 1168, 0] * 5), 0, 0, 1168, 1168))
            record = query.read().records[0]
            self.assertFalse(record.valid or record.has_country or record.has_begin_market or record.has_end_market)
            self.assertEqual((record.pointer, record.effectiveness_error), (0x40000, 1168))

    def test_shipping_lane_relationship_failure_is_not_native_zero_quantity(self):
        query = objects.enumerate_objects(self.client, objects.Domain.SHIPPING_LANE,
            start_index=0, max_items=1, process=self.process)
        self.completed_buffer(objects.SHIPPING_LANE_RECORD.pack(
            17, 17, 0, 17, 0x60000, 1, 0, 0x70000,
            0xFFFFFFFF, 1168, 0x40000, 6, 0, 0x90000,
            0xFFFFFFFF, 1168, 0, 4, 0, 0xB0000, 0, 0, 0, 1168))
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_country and record.has_end_market)
        self.assertFalse(record.has_begin_market)
        self.assertEqual((record.convoy_cost_error, record.effectiveness_error), (0, 1168))

    def test_shipping_lane_preserves_negative_native_scalar_bits_not_float(self):
        query = objects.read_ids(self.client, objects.Domain.SHIPPING_LANE, [17], process=self.process)
        rawbits = (1 << 64) - 100001
        self.completed_buffer(objects.SHIPPING_LANE_RECORD.pack(
            17, 17, 0, 17, 0x60000, 1, 0, 0x70000,
            5, 0, 0x80000, 6, 0, 0x90000, 3, 0, 0xA0000,
            4, 0, 0xB0000, rawbits, 85000, 0, 0))
        self.assertEqual(query.read().records[0].convoy_cost_raw64, rawbits)

    def test_shipping_lane_effectiveness_failure_and_market_failure_are_separate(self):
        query = objects.read_ids(self.client, objects.Domain.SHIPPING_LANE, [17], process=self.process)
        self.completed_buffer(objects.SHIPPING_LANE_RECORD.pack(
            17, 17, 0, 17, 0x60000, 1, 0, 0x70000,
            5, 0, 0x80000, 6, 0, 0x90000, 3, 1168, 0x40000,
            4, 0, 0xB0000, 100000, 0, 0, 574))
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_end_market)
        self.assertFalse(record.has_begin_market)
        self.assertEqual((record.begin_market_error, record.effectiveness_error), (1168, 574))

    def test_shipping_lane_ref_domain_cannot_use_definition_or_ig_name_operations(self):
        for operation in (objects.ENUMERATE_DEFINITIONS, objects.READ_DEFINITION_INDICES,
                          objects.ENUMERATE_INTEREST_GROUP_DETAILS, objects.READ_INTEREST_GROUP_DETAILS):
            with self.assertRaises(ValueError):
                objects._layout(objects.Domain.SHIPPING_LANE, operation)
        self.assertFalse(hasattr(objects.Domain, "TRADE_ROUTE"))
        self.assertFalse(hasattr(objects.Domain, "TRADE_CENTER"))
        with self.assertRaises(ValueError):
            objects.read_ids(self.client, objects.Domain.SHIPPING_LANE, [-1], process=self.process)
        self.interface_callback.assert_not_called()

    def test_company_live_full_generation56_and_static_definition_pointer(self):
        query = objects.read_ids(self.client, objects.Domain.COMPANY, [0xFC000021], process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[2], header[3], header[10]), (14, 2, 56))
        self.completed_buffer(objects.COMPANY_RECORD.pack(0xFC000021, 0xFC000021, 0, 33,
            0x60000, 0x01000001, 0, 0x70000, 0x80000, 0, 0))
        record = query.read().records[0]
        self.assertIsInstance(record, objects.Company)
        self.assertTrue(record.valid and record.has_country and record.has_company_type)
        self.assertEqual((record.requested_id, record.company_type_pointer), (0xFC000021, 0x80000))
        self.assertFalse(hasattr(record, "company_type_id"))
        self.assertFalse(hasattr(record, "headquarters_state_id"))

    def completed_company_text_buffer(self, fields, name=b"", *, error=0,
                                      next_index=1, required=None, count=1):
        data = bytearray(self.process.write.call_args.args[1])
        header = list(objects.HEADER.unpack_from(data))
        options = list(objects.DEFINITION_OPTIONS.unpack_from(data, header[8]))
        required = len(name) if required is None else required
        options[2:] = [len(name), 0, required, options[0] + required]
        header[1], header[11] = error, count
        header[12:17] = [count, 1, next_index, 2048, int(bool(error))]
        header[18:] = [0x30000, 0x40000, 0x50000]
        objects.HEADER.pack_into(data, 0, *header)
        objects.DEFINITION_OPTIONS.pack_into(data, header[8], *options)
        data[options[0]:options[0] + len(name)] = name
        if count:
            fields = list(fields)
            fields[12] = options[0] if fields[11] == 0 else 0
            fields[13] = len(name)
            objects.COMPANY_DETAILS_RECORD.pack_into(data, header[9], *fields)
        self.process.read.return_value = bytes(data)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 9}
        return data

    def test_company_details72_copied_owned_native_name_not_definition_key(self):
        query = objects.read_company_details(self.client, [0xF9000021], text_capacity=128, process=self.process)
        initial = self.process.write.call_args.args[1]
        header = objects.HEADER.unpack_from(initial)
        self.assertEqual((header[2], header[3], header[10]), (14, 14, 72))
        self.assertEqual(struct.unpack_from("<I", initial, header[8] + 32)[0], 0xF9000021)
        name = "#v 公司名称\x15icon\x16#!".encode("utf-8")
        self.completed_company_text_buffer((0xF9000021, 0xF9000021, 0, 33, 0x60000,
            1, 0, 0x70000, 0x80000, 0, 0, 0, 0, 0, 0), name)
        result = query.read();record = result.records[0]
        self.assertTrue(record.has_name and record.has_country and record.has_company_type)
        self.assertEqual((record.name_utf8, record.name), (name, name.decode("utf-8")))
        self.assertIsNone(result.definition_options)
        self.assertIsNone(result.interest_group_options)
        self.assertEqual(result.company_options.text_bytes_written, len(name))
        self.process.free.assert_called_once_with(0x20000)

    def test_company_country_failure_does_not_gate_valid_name_or_definition(self):
        query = objects.read_company_details(self.client, [33], process=self.process)
        self.completed_company_text_buffer((33, 33, 0, 33, 0x60000,
            0xFFFFFFFF, 1168, 0x40000, 0x80000, 0, 0, 0, 0, 0, 0), b"native-name")
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_company_type and record.has_name)
        self.assertFalse(record.has_country)
        self.assertEqual(record.country_error, 1168)

    def test_company_definition_failure_is_not_live_object_failure(self):
        query = objects.read_company_details(self.client, [33], process=self.process)
        self.completed_company_text_buffer((33, 33, 0, 33, 0x60000,
            1, 0, 0x70000, 0x90000, 1168, 0, 1168, 0, 0, 0))
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_country)
        self.assertFalse(record.has_company_type or record.has_name)
        self.assertEqual((record.company_type_pointer, record.name_error), (0x90000, 1168))

    def test_company_invalid_and_stale_full_ids_preserve_original_fallback(self):
        for full_id in (0xFFFFFFFF, 0xF8000021):
            self.setUp()
            query = objects.read_company_details(self.client, [full_id], process=self.process)
            self.completed_company_text_buffer((full_id, 0, 1168, full_id & 0xFFFFFF,
                0x40000, 0xFFFFFFFF, 1168, 0, 0, 1168, 0, 1168, 0, 0, 0))
            record = query.read().records[0]
            self.assertFalse(record.valid or record.has_country or record.has_company_type or record.has_name)
            self.assertEqual((record.pointer, record.name), (0x40000, None))

    def test_company_zero_id_and_empty_native_name_are_not_missing(self):
        query = objects.read_company_details(self.client, [0], text_capacity=0, process=self.process)
        self.completed_company_text_buffer((0, 0, 0, 0, 0x60000,
            1, 0, 0x70000, 0x80000, 0, 0, 0, 0, 0, 0))
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_name)
        self.assertEqual(record.name, "")

    def test_company_whole_name_truncation_retains_unwritten_input_index(self):
        query = objects.read_company_details(self.client, [33], text_capacity=0, process=self.process)
        self.completed_company_text_buffer((), error=234, next_index=0, required=43, count=0)
        result = query.read()
        self.assertEqual((result.error, result.next_index, result.returned_count), (234, 0, 0))
        self.assertEqual((result.company_options.text_bytes_written, result.company_options.text_bytes_required), (0, 43))
        self.assertTrue(result.truncated)
        self.interface_callback.assert_called_once()

    def test_company_name_field_failure_preserves_real_company_and_country(self):
        query = objects.read_company_details(self.client, [33], process=self.process)
        self.completed_company_text_buffer((33, 33, 0, 33, 0x60000,
            1, 0, 0x70000, 0x80000, 0, 0, 574, 0, 0, 0))
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_country and record.has_company_type)
        self.assertFalse(record.has_name)
        self.assertEqual(record.name_error, 574)

    def test_company_bad_name_arena_decoder_is_rejected_without_transport_changes(self):
        query = objects.read_company_details(self.client, [33], process=self.process)
        data = self.completed_company_text_buffer((33, 33, 0, 33, 0x60000,
            1, 0, 0x70000, 0x80000, 0, 0, 0, 0, 0, 0), b"name")
        header = objects.HEADER.unpack_from(data)
        struct.pack_into("<I", data, header[9] + 64, 5)
        with self.assertRaisesRegex(ValueError, "complete text arena"):
            objects.ObjectsResult.from_bytes(bytes(data))
        query.close()
        self.process.free.assert_called_once_with(0x20000)

    def test_company_pending_keeps_arena_and_no_implicit_replay(self):
        query = objects.enumerate_company_details(self.client, start_index=31,
            max_items=99, capacity=2, text_capacity=1, process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[3], header[4:7]), (13, (31, 99, 2)))
        self.world_status.return_value = {"status": "ok", "completed_request_id": 8}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "pending"):
            query.close()
        self.interface_callback.assert_called_once()

    def test_company_rejects_definition_domain_wrongops_and_invalid_uint32_inputs(self):
        for invalid in (-1, 1 << 32, "33"):
            with self.assertRaises(ValueError):
                objects.read_company_details(self.client, [invalid], process=self.process)
        for wrong_op in (objects.ENUMERATE_DEFINITIONS, objects.READ_DEFINITION_INDICES,
                         objects.READ_INTEREST_GROUP_DETAILS):
            with self.assertRaises(ValueError):objects._layout(objects.Domain.COMPANY, wrong_op)
        with self.assertRaises(ValueError):
            objects._layout(objects.Domain.SHIPPING_LANE, objects.READ_COMPANY_DETAILS)
        self.interface_callback.assert_not_called()

    def test_ship_full_generation88_four_real_ref_domains(self):
        query = objects.read_ids(self.client, objects.Domain.SHIP, [0xAD000017], process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[2], header[3], header[10]), (15, 2, 88))
        self.completed_buffer(objects.SHIP_RECORD.pack(0xAD000017, 0xAD000017, 0, 23, 0x60000,
            0xF000001, 0, 0x70000, 0x3000002, 0, 0x80000,
            0x2000003, 0, 0x90000, 0x4000004, 0, 0xA0000))
        record = query.read().records[0]
        self.assertIsInstance(record, objects.Ship)
        self.assertTrue(record.valid and record.has_country and record.has_fleet and record.has_template)
        self.assertEqual((record.requested_id, record.template_version_id, record.template_id),
                         (0xAD000017, 0x2000003, 0x4000004))
        self.assertFalse(hasattr(record, "ship_type_id"))
        self.assertFalse(hasattr(record, "target_fleet_id"))

    def completed_ship_text_buffer(self, fields, name=b"", *, error=0,
                                   next_index=1, required=None, count=1):
        data = bytearray(self.process.write.call_args.args[1])
        header = list(objects.HEADER.unpack_from(data))
        options = list(objects.DEFINITION_OPTIONS.unpack_from(data, header[8]))
        required = len(name) if required is None else required
        options[2:] = [len(name), 0, required, options[0] + required]
        header[1], header[11] = error, count
        header[12:17] = [count, 1, next_index, 2048, int(bool(error))]
        header[18:] = [0x30000, 0x40000, 0x50000]
        objects.HEADER.pack_into(data, 0, *header)
        objects.DEFINITION_OPTIONS.pack_into(data, header[8], *options)
        data[options[0]:options[0] + len(name)] = name
        if count:
            fields = list(fields)
            fields[18] = options[0] if fields[17] == 0 else 0
            fields[19] = len(name)
            objects.SHIP_DETAILS_RECORD.pack_into(data, header[9], *fields)
        self.process.read.return_value = bytes(data)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 9}
        return data

    def test_ship_details104_original_name_no_formatting_is_not_formatted_name(self):
        query = objects.read_ship_details(self.client, [0xAC000017], text_capacity=128, process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[2], header[3], header[10]), (15, 16, 104))
        name = "HMS 原生舰名".encode("utf-8")
        self.completed_ship_text_buffer((0xAC000017, 0xAC000017, 0, 23, 0x60000,
            1, 0, 0x70000, 2, 0, 0x80000, 3, 0, 0x90000, 4, 0, 0xA0000, 0, 0, 0, 0), name)
        result = query.read();record = result.records[0]
        self.assertTrue(record.has_name_no_formatting)
        self.assertEqual((record.name_no_formatting_utf8, record.name_no_formatting), (name, name.decode("utf-8")))
        self.assertFalse(hasattr(record, "name"))
        self.assertIsNone(result.company_options)
        self.assertEqual(result.ship_options.text_bytes_written, len(name))
        self.process.free.assert_called_once_with(0x20000)

    def test_ship_zero_id_and_zero_relations_are_valid_not_missing(self):
        query = objects.read_ids(self.client, objects.Domain.SHIP, [0], process=self.process)
        self.completed_buffer(objects.SHIP_RECORD.pack(0, 0, 0, 0, 0x60000,
            0, 0, 0x70000, 0, 0, 0x80000, 0, 0, 0x90000, 0, 0, 0xA0000))
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_country and record.has_fleet and record.has_template)

    def test_ship_invalid_and_stale_full_id_preserves_original_fallback(self):
        for full_id in (0xFFFFFFFF, 0xAB000017):
            self.setUp()
            query = objects.read_ship_details(self.client, [full_id], process=self.process)
            self.completed_ship_text_buffer((full_id, 0, 1168, full_id & 0xFFFFFF, 0x40000,
                *([0xFFFFFFFF, 1168, 0] * 4), 1168, 0, 0, 0))
            record = query.read().records[0]
            self.assertFalse(record.valid or record.has_country or record.has_fleet or record.has_template or record.has_name_no_formatting)
            self.assertEqual((record.pointer, record.name_no_formatting), (0x40000, None))

    def test_ship_country_failure_keeps_real_fleet_template_and_native_name(self):
        query = objects.read_ship_details(self.client, [23], process=self.process)
        self.completed_ship_text_buffer((23, 23, 0, 23, 0x60000,
            0xFFFFFFFF, 1168, 0x40000, 2, 0, 0x80000, 3, 0, 0x90000, 4, 0, 0xA0000, 0, 0, 0, 0), b"original")
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_fleet and record.has_template and record.has_name_no_formatting)
        self.assertFalse(record.has_country)

    def test_ship_assigned_fleet_failure_is_not_target_fleet_substitution(self):
        query = objects.read_ids(self.client, objects.Domain.SHIP, [23], process=self.process)
        self.completed_buffer(objects.SHIP_RECORD.pack(23, 23, 0, 23, 0x60000,
            1, 0, 0x70000, 0xFFFFFFFF, 1168, 0x40000, 3, 0, 0x90000, 4, 0, 0xA0000))
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_country and record.has_template)
        self.assertFalse(record.has_fleet)
        self.assertEqual(record.fleet_id, 0xFFFFFFFF)

    def test_ship_version_error_blocks_template_deref_not_live_ship(self):
        query = objects.read_ids(self.client, objects.Domain.SHIP, [23], process=self.process)
        self.completed_buffer(objects.SHIP_RECORD.pack(23, 23, 0, 23, 0x60000,
            1, 0, 0x70000, 2, 0, 0x80000, 0xAA000003, 1168, 0x40000,
            0xFFFFFFFF, 1168, 0))
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_fleet)
        self.assertFalse(record.has_template)
        self.assertEqual(record.template_id, 0xFFFFFFFF)

    def test_ship_template_error_does_not_hide_valid_version(self):
        query = objects.read_ids(self.client, objects.Domain.SHIP, [23], process=self.process)
        self.completed_buffer(objects.SHIP_RECORD.pack(23, 23, 0, 23, 0x60000,
            1, 0, 0x70000, 2, 0, 0x80000, 3, 0, 0x90000, 0xA9000004, 1168, 0x40000))
        record = query.read().records[0]
        self.assertTrue(record.valid)
        self.assertEqual((record.template_version_error, record.template_error), (0, 1168))
        self.assertFalse(record.has_template)

    def test_ship_name_no_formatting_failure_is_separate_and_empty_is_valid(self):
        query = objects.read_ship_details(self.client, [23], process=self.process)
        self.completed_ship_text_buffer((23, 23, 0, 23, 0x60000,
            1, 0, 0x70000, 2, 0, 0x80000, 3, 0, 0x90000, 4, 0, 0xA0000, 574, 0, 0, 0))
        record = query.read().records[0]
        self.assertTrue(record.valid and record.has_template)
        self.assertFalse(record.has_name_no_formatting)
        self.setUp()
        query = objects.read_ship_details(self.client, [23], text_capacity=0, process=self.process)
        self.completed_ship_text_buffer((23, 23, 0, 23, 0x60000,
            1, 0, 0x70000, 2, 0, 0x80000, 3, 0, 0x90000, 4, 0, 0xA0000, 0, 0, 0, 0))
        record = query.read().records[0]
        self.assertTrue(record.has_name_no_formatting)
        self.assertEqual(record.name_no_formatting, "")

    def test_ship_whole_name_truncation234_no_skipped_record(self):
        query = objects.read_ship_details(self.client, [23], text_capacity=0, process=self.process)
        self.completed_ship_text_buffer((), error=234, next_index=0, required=37, count=0)
        result = query.read()
        self.assertEqual((result.error, result.next_index, result.returned_count), (234, 0, 0))
        self.assertEqual((result.ship_options.text_bytes_written, result.ship_options.text_bytes_required), (0, 37))
        self.interface_callback.assert_called_once()

    def test_ship_decoder_rejects_name_outside_written_arena(self):
        query = objects.read_ship_details(self.client, [23], process=self.process)
        data = self.completed_ship_text_buffer((23, 23, 0, 23, 0x60000,
            1, 0, 0x70000, 2, 0, 0x80000, 3, 0, 0x90000, 4, 0, 0xA0000, 0, 0, 0, 0), b"name")
        header = objects.HEADER.unpack_from(data)
        struct.pack_into("<I", data, header[9] + 96, 5)
        with self.assertRaisesRegex(ValueError, "complete text arena"):
            objects.ObjectsResult.from_bytes(bytes(data))
        query.close()
        self.process.free.assert_called_once_with(0x20000)

    def test_ship_pending_request_keeps_owner_and_operation_domains_are_explicit(self):
        query = objects.enumerate_ship_details(self.client, start_index=11, max_items=100,
            capacity=1, text_capacity=0, process=self.process)
        header = objects.HEADER.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual((header[3], header[4:7]), (15, (11, 100, 1)))
        self.world_status.return_value = {"status": "ok", "completed_request_id": 8}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        for wrong_op in (objects.ENUMERATE_DEFINITIONS, objects.READ_COMPANY_DETAILS,
                         objects.READ_INTEREST_GROUP_DETAILS):
            with self.assertRaises(ValueError):objects._layout(objects.Domain.SHIP, wrong_op)
        with self.assertRaises(ValueError):objects._layout(objects.Domain.COMPANY, objects.READ_SHIP_DETAILS)
        self.interface_callback.assert_called_once()

if __name__ == "__main__":
    unittest.main()
