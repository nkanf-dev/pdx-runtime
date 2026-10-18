from __future__ import annotations
import struct
import unittest
from unittest.mock import Mock, patch

from pdx_runtime import v3_ai as ai
from pdx_runtime.v3 import CallbackResult, QueryExecutionError
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES


class AiCacheTests(unittest.TestCase):
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
        self.process = Mock(pid=123)
        self.interface_callback.return_value = {"status": "ok", "error": 0, "request_id": 9}
        self.process.resolve_export.return_value = 0x10000
        self.process.allocate.return_value = 0x20000

    def row(self, index=0, **overrides):
        values = dict(cache_index=index, valid_fields=31, actor_full_id=0x03000007,
            owner_country_full_id=0x02000004, actor_error=0, owner_error=0,
            link_error=0, role_error=0, actor_enabled_raw=2, actor_141_raw=99,
            role_index=4, role_raw=3, observed_predicate=1,
            reserved0=0, reserved1=0, reserved2=0)
        values.update(overrides)
        return values

    def snapshot(self, rows=None, *, capacity=None, native_total=None, error=0, flags=None, **overrides):
        if rows is None:
            rows = [self.row()]
        if capacity is None:
            capacity = len(rows)
        if native_total is None:
            native_total = len(rows)
        row_errors = sum(any(row[key] for key in ("actor_error", "owner_error", "link_error", "role_error"))
                         for row in rows)
        if flags is None:
            flags = ai.AiCacheFlags.CONTEXT_VALID | ai.AiCacheFlags.HEADER_VALID | ai.AiCacheFlags.TOTAL_KNOWN
            if error == 0:
                flags |= ai.AiCacheFlags.COMPLETE
            elif error == 234:
                flags |= ai.AiCacheFlags.TRUNCATED
            elif error in (998, 574):
                flags |= ai.AiCacheFlags.PREFIX_FAULT
            if row_errors:
                flags |= ai.AiCacheFlags.ROW_ERRORS
        values = dict(size=96 + capacity * 64, error=error, row_capacity=capacity,
            row_count=len(rows), native_total=native_total, flags=flags,
            required_bytes=96 + native_total * 64 if flags & ai.AiCacheFlags.HEADER_VALID else 0,
            returned_bytes=96 + len(rows) * 64, row_bytes=64, manager_override_raw=3,
            manager_error=0, role_count=8, paused=0, simulation_started=1,
            execution_thread_id=7372, row_error_count=row_errors, reserved=0,
            expected_world=0, world=0x20000, application=0x30000)
        values.update(overrides)
        header = ai.CACHE_SNAPSHOT.pack(*values.values())
        return header + b"".join(ai.CACHE_ROW.pack(*row.values()) for row in rows) + bytes(64 * (capacity - len(rows)))

    def complete(self, query, data):
        self.world_status.return_value = {"status": "ok", "completed_request_id": 9}
        self.process.read.return_value = data
        return query.read()

    def test_exact_abi_and_initial_request(self):
        query = ai.observe_cached_ai(self.client, capacity=3, expected_world=0xFEDCBA9876543210,
                                     process=self.process)
        self.assertIs(type(query), CallbackResult)
        self.assertEqual((ai.CACHE_SNAPSHOT.size, ai.CACHE_ROW.size, ai.STATE.size), (96, 64, 144))
        self.process.resolve_export.assert_called_once_with("pdx_v3_console.dll", "PdxV3ConsoleAiCacheInterface", aliases=V3_EXPORT_ALIASES)
        self.process.allocate.assert_called_once_with(288)
        initial = self.process.write.call_args.args[1]
        expected = [0] * 20
        expected[:3] = [288, 997, 3]
        expected[8] = 64
        expected[17] = 0xFEDCBA9876543210
        self.assertEqual(ai.CACHE_SNAPSHOT.unpack_from(initial), tuple(expected))
        self.assertEqual(initial[96:], bytes(192))
        self.assertEqual(struct.unpack_from("<Q", initial, 72)[0], 0xFEDCBA9876543210)
        self.assertEqual(query.result_size, 288)
        self.interface_callback.assert_called_once_with(0x10000, 0x20000)
        self.world_status.assert_not_called()

    def test_default_capacity_and_no_command_or_read_gate(self):
        with patch.object(ai.v3_console, "execute_arguments") as command:
            query = ai.observe_cached_ai(self.client, process=self.process)
        self.assertEqual(ai.CACHE_SNAPSHOT.unpack_from(self.process.write.call_args.args[1])[2], 256)
        self.assertEqual(query.result_size, 96 + 256 * 64)
        command.assert_not_called()
        self.process.read.assert_not_called()
        self.world_status.assert_not_called()
        self.client.assert_has_calls([unittest.mock.call.request("interface-callback 0x10000 0x20000")])
        self.assertEqual(self.client.method_calls, [unittest.mock.call.request("interface-callback 0x10000 0x20000")])

    def test_capacity_strict_integer_and_whole_uint32_limit(self):
        for value in (True, False, "3", 3.0, None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                ai.observe_cached_ai(self.client, capacity=value, process=self.process)
        limit = (0xFFFFFFFF - 96) // 64
        for value in (-1, limit + 1, 0xFFFFFFFF, 1 << 100):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ai.observe_cached_ai(self.client, capacity=value, process=self.process)
        self.assertEqual(ai._cache_allocation_size(limit), 0xFFFFFFE0)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.interface_callback.assert_not_called()

    def test_no_game_count_cap(self):
        with patch.object(ai, "submit_callback", return_value=object()) as submit:
            ai.observe_cached_ai(self.client, capacity=100000, process=self.process)
        self.assertEqual(len(submit.call_args.args[1]), 96 + 100000 * 64)
        self.assertEqual(ai.CACHE_SNAPSHOT.unpack_from(submit.call_args.args[1])[2], 100000)

    def test_expected_world_strict_uint64(self):
        for value in (True, False, None, "0", 0.0):
            with self.subTest(value=value), self.assertRaises(TypeError):
                ai.observe_cached_ai(self.client, expected_world=value, process=self.process)
        for value in (-1, 1 << 64):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ai.observe_cached_ai(self.client, expected_world=value, process=self.process)
        self.process.allocate.assert_not_called()
        ai.observe_cached_ai(self.client, capacity=0, expected_world=0xFFFFFFFFFFFFFFFF, process=self.process)
        self.assertEqual(ai.CACHE_SNAPSHOT.unpack_from(self.process.write.call_args.args[1])[17], 0xFFFFFFFFFFFFFFFF)

    def test_zero_capacity_nonempty_discovery_is_234_without_retry(self):
        query = ai.observe_cached_ai(self.client, capacity=0, process=self.process)
        result = self.complete(query, self.snapshot([], capacity=0, native_total=7, error=234))
        self.assertEqual((result.error, result.row_count, result.native_total), (234, 0, 7))
        self.assertEqual((result.required_bytes, result.returned_bytes), (544, 96))
        self.assertEqual(result.rows, ())
        self.assertTrue(result.truncated)
        self.assertFalse(result.complete)
        self.interface_callback.assert_called_once()
        self.process.allocate.assert_called_once_with(96)
        self.process.read.assert_called_once_with(0x20000, 96)

    def test_real_empty_cache_complete_for_zero_or_spare_capacity(self):
        for capacity in (0, 4):
            result = ai.AiCacheSnapshot.from_bytes(self.snapshot([], capacity=capacity))
            self.assertTrue(result.context_valid)
            self.assertTrue(result.header_valid)
            self.assertTrue(result.total_known)
            self.assertTrue(result.complete)
            self.assertFalse(result.truncated)
            self.assertEqual((result.native_total, result.row_count, result.rows), (0, 0, ()))
            self.assertEqual((result.required_bytes, result.returned_bytes), (96, 96))

    def test_order_and_duplicates_are_not_deduplicated(self):
        rows = [self.row(i, actor_full_id=actor) for i, actor in enumerate((0x3000009, 0x3000007, 0x3000009))]
        result = ai.AiCacheSnapshot.from_bytes(self.snapshot(rows, capacity=5))
        self.assertEqual([row.cache_index for row in result.rows], [0, 1, 2])
        self.assertEqual([row.actor_full_id for row in result.rows], [0x3000009, 0x3000007, 0x3000009])
        self.assertEqual(result.row_count, 3)
        self.assertTrue(result.complete)

    def test_raw_bytes_are_not_booleans_and_known_fields_are_exposed(self):
        result = ai.AiCacheSnapshot.from_bytes(self.snapshot([self.row(actor_enabled_raw=255, actor_141_raw=2, role_raw=99)]))
        row = result.rows[0]
        self.assertEqual((row.actor_enabled_raw, row.actor_141_raw, row.role_raw, result.manager_override_raw), (255, 2, 99, 3))
        self.assertEqual(row.known_fields, ai.AiCacheValidFields(31))
        self.assertTrue(all((row.actor_fields_known, row.actor_identity_known, row.owner_identity_known,
                             row.role_value_known, row.observed_predicate_known)))
        self.assertFalse(row.has_errors)
        self.assertEqual((result.paused, result.simulation_started), (0, 1))

    def test_error_rows_keep_independent_fields_and_complete_coverage(self):
        rows = [self.row(0, valid_fields=1, actor_error=1168, owner_error=21, link_error=13,
                         role_error=1168, role_raw=0xFFFFFFFF, observed_predicate=0xFFFFFFFF),
                self.row(1, valid_fields=31, link_error=1168),
                self.row(2, valid_fields=23, role_index=100, role_raw=0xFFFFFFFF, role_error=1168)]
        result = ai.AiCacheSnapshot.from_bytes(self.snapshot(rows))
        self.assertEqual(result.row_count, 3)
        self.assertEqual(result.row_error_count, 3)  # rows, not number of failed fields
        self.assertTrue(result.complete)
        self.assertTrue(result.has_row_errors)
        first = result.rows[0]
        self.assertTrue(first.actor_fields_known)
        self.assertFalse(first.actor_identity_known)
        self.assertEqual((first.actor_error, first.owner_error, first.link_error, first.role_error), (1168, 21, 13, 1168))
        self.assertEqual(first.role_raw, 0xFFFFFFFF)
        self.assertTrue(result.rows[1].owner_identity_known)
        self.assertEqual(result.rows[1].link_error, 1168)
        self.assertFalse(result.rows[2].role_value_known)
        self.assertTrue(result.rows[2].observed_predicate_known)

    def test_unknown_field_payload_is_not_fabricated_or_range_checked(self):
        row = self.row(valid_fields=0, actor_error=13, owner_error=21, role_error=21,
                       actor_enabled_raw=0xFFFFFFFF, actor_141_raw=0xFFFFFFFE,
                       role_raw=0xFFFFFFFD, observed_predicate=0xFFFFFFFC)
        result = ai.AiCacheSnapshot.from_bytes(self.snapshot([row]))
        self.assertEqual(result.rows[0].known_fields, 0)
        self.assertEqual((result.rows[0].actor_enabled_raw, result.rows[0].actor_141_raw,
                          result.rows[0].role_raw, result.rows[0].observed_predicate),
                         (0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC))

    def test_capacity_short_keeps_only_whole_prefix(self):
        result = ai.AiCacheSnapshot.from_bytes(self.snapshot([self.row(0), self.row(1)],
            capacity=2, native_total=5, error=234))
        self.assertEqual((result.error, result.row_count, result.native_total), (234, 2, 5))
        self.assertEqual((result.required_bytes, result.returned_bytes, result.size), (416, 224, 224))
        self.assertTrue(result.truncated)
        self.assertFalse(result.complete)
        self.assertEqual([row.cache_index for row in result.rows], [0, 1])

    def test_huge_known_total_uses_uint64_required_bytes_no_count_cap(self):
        result = ai.AiCacheSnapshot.from_bytes(self.snapshot([], capacity=0, native_total=0x7FFFFFFF, error=234))
        self.assertEqual(result.native_total, 0x7FFFFFFF)
        self.assertEqual(result.required_bytes, 96 + 0x7FFFFFFF * 64)
        self.assertGreater(result.required_bytes, 0xFFFFFFFF)

    def test_negative_native_total_is_preserved_on_native_error(self):
        result = ai.AiCacheSnapshot.from_bytes(self.snapshot([], capacity=2, native_total=-7,
            error=13, flags=ai.AiCacheFlags.CONTEXT_VALID | ai.AiCacheFlags.TOTAL_KNOWN))
        self.assertEqual(result.native_total, -7)
        self.assertEqual(result.error, 13)
        self.assertTrue(result.total_known)
        self.assertFalse(result.header_valid)
        self.assertFalse(result.complete)
        self.assertFalse(result.truncated)
        self.assertEqual((result.required_bytes, result.returned_bytes, result.rows), (0, 96, ()))

    def test_native_context_errors_are_not_successful_empty(self):
        for error in (21, 13, 1168):
            result = ai.AiCacheSnapshot.from_bytes(self.snapshot([], capacity=3, native_total=0,
                error=error, flags=0, manager_error=error, world=0))
            self.assertEqual(result.error, error)
            self.assertFalse(result.context_valid)
            self.assertFalse(result.total_known)
            self.assertFalse(result.complete)
            self.assertEqual(result.rows, ())

    def test_fault_prefix_is_not_ordinary_truncation(self):
        for error in (998, 574):
            result = ai.AiCacheSnapshot.from_bytes(self.snapshot([self.row()], capacity=4,
                native_total=3, error=error))
            self.assertEqual(result.error, error)
            self.assertTrue(result.prefix_fault)
            self.assertFalse(result.truncated)
            self.assertFalse(result.complete)
            self.assertEqual((result.row_count, result.native_total, result.required_bytes, result.returned_bytes), (1, 3, 288, 160))
            self.assertEqual(len(result.rows), 1)

    def test_early_fault_without_known_total_retains_status(self):
        for error in (998, 574):
            result = ai.AiCacheSnapshot.from_bytes(self.snapshot([], capacity=3, native_total=-1,
                error=error, flags=ai.AiCacheFlags.PREFIX_FAULT, manager_error=21))
            self.assertTrue(result.prefix_fault)
            self.assertFalse(result.total_known)
            self.assertEqual((result.error, result.native_total, result.row_count), (error, -1, 0))

    def test_unused_allocation_tail_is_not_decoded_as_rows(self):
        data = bytearray(self.snapshot(capacity=3))
        data[160:] = b"\xff" * 128
        result = ai.AiCacheSnapshot.from_bytes(data)
        self.assertEqual(result.row_count, 1)
        self.assertEqual(len(result.rows), 1)
        self.assertTrue(result.complete)

    def test_invalid_allocation_or_request_fields_are_rejected(self):
        good = self.snapshot(capacity=3)
        for data in (b"", good[:95], good[:-1], good + b"\0", self.snapshot(capacity=3, size=96),
                     self.snapshot(capacity=3, row_capacity=2), self.snapshot(row_bytes=63), self.snapshot(reserved=1)):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                ai.AiCacheSnapshot.from_bytes(data)

    def test_request_echo_is_checked_by_submission_decoder(self):
        query = ai.observe_cached_ai(self.client, capacity=2, expected_world=0xA000, process=self.process)
        decode = query.decode_result
        with self.assertRaises(ValueError):
            decode(self.snapshot(capacity=2, expected_world=0xB000))
        with self.assertRaises(ValueError):
            decode(self.snapshot(capacity=1, expected_world=0xA000))
        with self.assertRaises(ValueError):
            decode(self.snapshot(capacity=2, expected_world=0xA000, world=0xB000))
        good = decode(self.snapshot(capacity=2, expected_world=0xA000, world=0xA000))
        self.assertEqual(good.expected_world, 0xA000)

    def test_expected_world_mismatch_retains_native_error_and_diagnostics(self):
        query = ai.observe_cached_ai(self.client, capacity=2, expected_world=0xA000, process=self.process)
        result = self.complete(query, self.snapshot([], capacity=2, native_total=-1,
            error=21, flags=0, manager_error=21, expected_world=0xA000, world=0xB000))
        self.assertEqual((result.error, result.expected_world, result.world), (21, 0xA000, 0xB000))
        self.assertFalse(result.context_valid)
        self.assertFalse(result.complete)
        self.interface_callback.assert_called_once()

    def test_counts_and_byte_lengths_are_strict(self):
        cases = [dict(row_count=2), dict(returned_bytes=159), dict(returned_bytes=161),
                 dict(required_bytes=159), dict(required_bytes=0), dict(row_error_count=1),
                 dict(row_count=0), dict(native_total=-1), dict(flags=15 | 128)]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ai.AiCacheSnapshot.from_bytes(self.snapshot(**changes))
        with self.assertRaises(ValueError):
            ai.AiCacheSnapshot.from_bytes(self.snapshot([], error=21, flags=0, required_bytes=96))
        with self.assertRaises(ValueError):
            ai.AiCacheSnapshot.from_bytes(self.snapshot(native_total=0))

    def test_known_flag_dependencies_and_status_are_strict(self):
        cases = [dict(flags=2), dict(flags=5), dict(flags=7), dict(error=234, flags=15),
                 dict(error=21, flags=15), dict(error=998, flags=7), dict(error=574, flags=7),
                 dict(error=21, flags=71), dict(flags=31), dict(flags=79),
                 dict(error=234, flags=23), dict(flags=47), dict(error=997, flags=15)]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ai.AiCacheSnapshot.from_bytes(self.snapshot(**changes))
        with self.assertRaises(ValueError):
            ai.AiCacheSnapshot.from_bytes(self.snapshot([], native_total=-1, error=13, flags=7, required_bytes=0))

    def test_row_prefix_validity_reserved_and_known_raw_bounds_are_strict(self):
        cases = [dict(cache_index=1), dict(valid_fields=63), dict(reserved0=1), dict(reserved1=1),
                 dict(reserved2=1), dict(actor_enabled_raw=256), dict(actor_141_raw=256),
                 dict(role_index=8), dict(observed_predicate=2),
                 dict(actor_error=1168), dict(owner_error=1168), dict(role_error=1168)]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ai.AiCacheSnapshot.from_bytes(self.snapshot([self.row(**changes)]))
        with self.assertRaises(ValueError):
            ai.AiCacheSnapshot.from_bytes(self.snapshot(manager_override_raw=256))

    def test_row_error_flag_counts_rows_not_fields(self):
        row = self.row(valid_fields=1, actor_error=1168, owner_error=21, role_error=13)
        for changes in (dict(row_error_count=3), dict(flags=15), dict(row_error_count=0)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ai.AiCacheSnapshot.from_bytes(self.snapshot([row], **changes))

    def test_pending_storage_lifetime_and_no_replay(self):
        query = ai.observe_cached_ai(self.client, capacity=2, process=self.process)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 8}
        self.assertIsNone(query.read())
        with self.assertRaises(RuntimeError):
            query.close()
        self.process.read.assert_not_called()
        self.process.free.assert_not_called()
        result = self.complete(query, self.snapshot(capacity=2))
        self.assertIs(query.read(), result)
        query.close()
        self.process.read.assert_called_once_with(0x20000, 224)
        self.process.free.assert_called_once_with(0x20000)
        self.process.close.assert_not_called()
        self.interface_callback.assert_called_once()

    def test_completed_unexecuted_callback_uses_existing_execution_error(self):
        query = ai.observe_cached_ai(self.client, capacity=2, process=self.process)
        initial = self.process.write.call_args.args[1]
        self.world_status.return_value = {"status": "ok", "request_id": 9,
            "completed_request_id": 9, "world_error": 21}
        self.process.read.return_value = initial
        with self.assertRaises(QueryExecutionError):
            query.read()
        self.process.free.assert_called_once_with(0x20000)
        self.interface_callback.assert_called_once()

    def test_unknown_delivery_keeps_shared_handle_without_retry(self):
        self.interface_callback.side_effect = OSError("delivery unknown")
        with self.assertRaises(OSError) as raised:
            ai.observe_cached_ai(self.client, capacity=1, process=self.process)
        query = raised.exception.query
        self.assertIs(type(query), CallbackResult)
        self.assertIsNone(query.request_id)
        self.process.free.assert_not_called()
        with self.assertRaises(RuntimeError):
            query.read()
        query.recover(9)
        result = self.complete(query, self.snapshot())
        self.assertTrue(result.complete)
        self.interface_callback.assert_called_once()
        self.process.free.assert_called_once_with(0x20000)

    def test_native_errors_use_shared_result_not_automatic_exception_or_retry(self):
        query = ai.observe_cached_ai(self.client, capacity=2, process=self.process)
        result = self.complete(query, self.snapshot([], capacity=2, native_total=-9, error=13,
            flags=ai.AiCacheFlags.CONTEXT_VALID | ai.AiCacheFlags.TOTAL_KNOWN))
        self.assertEqual(result.error, 13)
        self.assertEqual(result.native_total, -9)
        self.assertFalse(result.complete)
        self.process.free.assert_called_once_with(0x20000)
        self.interface_callback.assert_called_once()

    def test_known_raw_total_can_have_invalid_array_metadata(self):
        for total in (-0x80000000, -1, 0, 7, 0x7FFFFFFF):
            result = ai.AiCacheSnapshot.from_bytes(self.snapshot([], capacity=2, native_total=total,
                error=13, flags=ai.AiCacheFlags.CONTEXT_VALID | ai.AiCacheFlags.TOTAL_KNOWN))
            self.assertEqual((result.error, result.native_total), (13, total))
            self.assertTrue(result.total_known)
            self.assertFalse(result.header_valid)
            self.assertFalse(result.complete)
            self.assertEqual((result.required_bytes, result.returned_bytes, result.rows), (0, 96, ()))

    def test_valid_array_header_survives_later_pool_error(self):
        result = ai.AiCacheSnapshot.from_bytes(self.snapshot([], capacity=4, native_total=3,
            error=13, flags=7))
        self.assertTrue(result.header_valid)
        self.assertTrue(result.total_known)
        self.assertEqual((result.error, result.required_bytes, result.returned_bytes), (13, 288, 96))
        self.assertFalse(result.complete)
        self.assertFalse(result.truncated)
        self.assertFalse(result.prefix_fault)

    def test_role_raw_is_full_uint32_not_an_actor_byte(self):
        for role_raw in (256, 0x80000000, 0xFFFFFFFF):
            result = ai.AiCacheSnapshot.from_bytes(self.snapshot([self.row(role_raw=role_raw)]))
            self.assertEqual(result.rows[0].role_raw, role_raw)
            self.assertTrue(result.rows[0].role_value_known)

    def test_faulted_row_can_be_committed_with_partial_known_fields(self):
        for error in (998, 574):
            row = self.row(1, valid_fields=3, owner_error=error, link_error=21, role_error=21,
                           role_raw=0xFFFFFFFF, observed_predicate=0)
            result = ai.AiCacheSnapshot.from_bytes(self.snapshot([self.row(), row], capacity=2,
                native_total=2, error=error))
            self.assertEqual(result.row_count, result.native_total)
            self.assertTrue(result.prefix_fault)
            self.assertFalse(result.complete)  # A full row count cannot hide the traversal fault.
            self.assertFalse(result.truncated)
            self.assertEqual(result.row_error_count, 1)
            self.assertEqual(result.rows[1].owner_error, error)
            self.assertTrue(result.rows[1].actor_fields_known)
            self.assertTrue(result.rows[1].actor_identity_known)
            self.assertFalse(result.rows[1].owner_identity_known)
            self.assertFalse(result.rows[1].observed_predicate_known)

    def test_canonical_native_bad_request_error_is_not_fake_empty(self):
        result = ai.AiCacheSnapshot.from_bytes(self.snapshot([], capacity=2, native_total=-1,
            error=87, flags=0, manager_error=21))
        self.assertEqual((result.error, result.native_total, result.returned_bytes), (87, -1, 96))
        self.assertFalse(result.complete)
        self.assertFalse(result.total_known)
        self.assertEqual(result.rows, ())

    def test_supplied_adapter_and_shared_transport_are_forwarded_once(self):
        token = object()
        with patch.object(ai, "submit_callback", return_value=token) as submit:
            result = ai.observe_cached_ai(self.client, capacity=0, adapter=0x12340000, process=self.process)
        self.assertIs(result, token)
        submit.assert_called_once()
        self.assertIs(submit.call_args.args[0], self.client)
        self.assertEqual(submit.call_args.kwargs["adapter"], 0x12340000)
        self.assertEqual(submit.call_args.kwargs["callback_symbol"], "PdxV3ConsoleAiCacheInterface")
        self.assertIs(submit.call_args.kwargs["process"], self.process)
        self.assertNotIn("handle_factory", submit.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
