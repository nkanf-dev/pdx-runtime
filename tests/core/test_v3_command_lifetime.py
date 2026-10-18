"""Python-boundary fixtures for the real SDK; no native/game success claim."""
from __future__ import annotations

import struct
import unittest

from pdx_runtime import native_call, v3, v3_commands as commands, v3_session
from tests.native_sdk.test_v3_query import fixture_wire_request


COMMAND = 0x55555
WORLD = 0x88888
TYPE = 12490


class FixtureProcess:
    """Allocator/transport memory only, not an implementation of native commands."""
    def __init__(self):
        self.pid = 123
        self.next_address = 0x20000
        self.blocks = {}
        self.freed = []
        self.closed = False
        self.export_error = None
        self.before_export = None
        self.free_error = None
        self.exports = {"PdxV3CommandInterface": 0x10000,
            "PdxV3CommandSession": 0x11000, "PdxNativeCallInterface": 0x12000,
            "PdxV3QueueSessionCallback": 0x13000, "PdxV3SessionCallbackState": 0x14000}
        self.blocks[0x14000] = bytearray(v3_session.CALLBACK_STATUS.size)

    def resolve_export(self, module, symbol, *, aliases=None):
        if self.before_export is not None:
            self.before_export()
        if self.export_error is not None:
            raise self.export_error
        return self.exports[symbol]

    def allocate(self, size):
        address = self.next_address
        self.next_address += size + 0x100
        self.blocks[address] = bytearray(size)
        return address

    def _block(self, address, size):
        for base, block in self.blocks.items():
            if base <= address and address + size <= base + len(block):
                return block, address - base
        raise AssertionError(f"fixture memory range not allocated: {address:#x}+{size}")

    def read(self, address, size):
        block, offset = self._block(address, size)
        return bytes(block[offset:offset + size])

    def write(self, address, data):
        block, offset = self._block(address, len(data))
        block[offset:offset + len(data)] = data
        return len(data)

    def free(self, address):
        if self.free_error is not None:
            raise self.free_error
        self.freed.append(address)
        del self.blocks[address]

    def close(self):
        self.closed = True


class FixtureClient:
    pid = 123

    def __init__(self):
        self.calls = []
        self.status_reads = 0
        self.queue_error = None
        self.status = {"status": "ok", "request_id": 7, "completed_request_id": 6}

    def request(self, command):
        return fixture_wire_request(self, command)

    def queue_v3_interface_callback(self, callback, address):
        self.calls.append((callback, address))
        if self.queue_error is not None:
            raise self.queue_error
        return {"error": 0, "status": "ok", "request_id": 7}

    def v3_world_status(self):
        self.status_reads += 1
        return self.status


class CommandLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.process = FixtureProcess()
        self.client = FixtureClient()
        self.owner = commands.NativeCommand(self.client, COMMAND, TYPE, WORLD,
            process=self.process)

    def assert_blocked(self, owner=None):
        owner = owner or self.owner
        calls = len(self.client.calls)
        for operation in (owner.clone, owner.serialize, owner.validate, owner.submit, owner.destroy):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(RuntimeError):
                    operation()
        self.assertEqual(len(self.client.calls), calls)

    def finish(self, query, *, error=0, command=COMMAND, world=WORLD,
               payload=b"", buffer_size=0, validated=0, accepted=0, changes=None):
        """Write an explicit fixture receipt into the real handle's original buffer."""
        address = getattr(query, "opaque_address", query.result_address)
        fields = list(commands.RESULT.unpack(self.process.read(address, commands.RESULT.size)))
        fields[1], fields[4], fields[7] = error, command, buffer_size
        fields[10], fields[11], fields[12], fields[15] = validated, accepted, 3, world
        fields[3] = TYPE if not error and command else 0
        for index, value in (changes or {}).items():
            fields[index] = value
        self.process.write(address, commands.RESULT.pack(*fields) + payload)
        self.client.status = {"status": "ok", "request_id": 7, "completed_request_id": 7}
        if isinstance(query, v3_session.SessionCallbackResult):
            raw = list(native_call.REQUEST.unpack(self.process.read(query.result_address,
                native_call.REQUEST.size)))
            raw[1], raw[5] = 0, 0  # adapter error, raw enqueue RAX; keep original target
            self.process.write(query.result_address, native_call.REQUEST.pack(*raw))
            self.process.write(query.session_request_address, struct.pack("<Q", 9))
            status = v3_session.CALLBACK_STATUS.pack(v3_session.CALLBACK_STATUS.size,
                2, 0, 4, 9, 9, query.callbackfn, query.opaque_address, 0x99999)
            self.process.write(query.session_state_address, status)


    def test_pending_none_then_completion_uses_same_query_and_retains_receipt(self):
        query = self.owner.validate(capacity=5)
        attempt = self.owner.attempt
        self.assertIs(self.owner.pending, query)
        self.assertIs(attempt.query, query)
        self.assertEqual((attempt.action, attempt.command, attempt.type_token, attempt.world),
            (commands.Action.VALIDATE, COMMAND, TYPE, WORLD))
        self.assertIsNone(self.owner.complete())
        self.assertIs(self.owner.attempt, attempt)
        self.assertEqual(attempt.outcome, "pending")
        self.assert_blocked()
        self.finish(query, payload=b"no", buffer_size=2, validated=0)
        result = self.owner.complete()
        self.assertIs(attempt.result, result)
        self.assertIs(query._result, result)
        self.assertEqual(result.reason, "no")
        self.assertEqual(result.validated, 0)  # native bool is not business success
        self.assertEqual(attempt.outcome, "owned")
        self.assertEqual(self.owner.command, COMMAND)
        self.assertIsNone(self.owner.pending)
        self.assertEqual(self.process.freed, [query.result_address])
        self.assertEqual(len(self.client.calls), 1)
        self.owner.destroy()  # a new explicit operation is allowed, never automatic
        self.assertEqual(len(self.client.calls), 2)

    def test_negative_capacity_records_the_same_zero_capacity_as_request(self):
        for operation in ("serialize", "validate"):
            with self.subTest(operation=operation):
                self.setUp()
                query = getattr(self.owner, operation)(capacity=-1)
                fields = commands.RESULT.unpack(self.process.read(query.result_address,
                    commands.RESULT.size))
                self.assertEqual((fields[5], fields[6], self.owner.attempt.capacity), (0, 0, 0))
                if operation == "serialize":
                    self.finish(query, error=122, buffer_size=17)
                else:
                    self.finish(query, validated=1)
                result = self.owner.complete()
                self.assertEqual(self.owner.attempt.outcome, "owned")
                self.assertEqual(self.owner.command, COMMAND)
                self.assertIsNone(self.owner.pending)
                self.assertIs(self.owner.attempt.result, result)

    def test_unchecked_world0_destroy_keeps_source_world0_and_separate_callback_context(self):
        for callback_world in (0, WORLD):
            with self.subTest(callback_world=callback_world):
                self.setUp()
                self.owner.world = 0
                query = self.owner.destroy()
                self.finish(query, command=0, world=callback_world)
                result = self.owner.complete()
                self.assertEqual(self.owner.attempt.outcome, "consumed")
                self.assertEqual((self.owner.command, self.owner.world), (0, 0))
                self.assertEqual(self.owner.attempt.world, 0)
                self.assertEqual(result.world, callback_world)
                self.assertIsNone(self.owner.pending)
                self.assert_blocked()
        self.setUp()
        self.owner.world = 0
        query = self.owner.destroy()
        self.finish(query, error=574, command=0, world=WORLD)
        result = self.owner.complete()
        self.assertEqual(self.owner.attempt.outcome, "unknown")
        self.assertEqual((self.owner.command, self.owner.world), (COMMAND, 0))
        self.assertEqual((self.owner.attempt.world, result.world), (0, WORLD))
        self.assertIs(self.owner.pending, query)
        self.assert_blocked()

    def test_nonzero_source_world_mismatch_blocks_even_normal_consuming_receipt(self):
        for operation in ("destroy", "submit"):
            with self.subTest(operation=operation):
                self.setUp()
                query = getattr(self.owner, operation)()
                self.finish(query, command=0, world=WORLD + 1)
                self.owner.complete()
                self.assertEqual(self.owner.attempt.outcome, "unknown")
                self.assertEqual((self.owner.command, self.owner.world), (COMMAND, WORLD))
                self.assertIs(self.owner.pending, query)
                self.assert_blocked()

    def test_dispatch_exception_with_none_query_keeps_original_intent_and_blocks(self):
        for explicit_none in (False, True):
            with self.subTest(explicit_none=explicit_none):
                self.setUp()
                error = RuntimeError("fixture export failure")
                if explicit_none:
                    error.query = None
                self.process.export_error = error
                def inspect_attempt():
                    self.assertIsNotNone(self.owner.attempt)
                    self.assertIsNone(self.owner.attempt.query)
                    self.assertEqual(self.owner.attempt.action, commands.Action.SUBMIT)
                self.process.before_export = inspect_attempt
                with self.assertRaises(RuntimeError) as caught:
                    self.owner.submit(flags=13)
                self.assertIs(caught.exception, error)
                attempt = self.owner.attempt
                self.assertIs(attempt.submission_error, error)
                self.assertIsNone(attempt.query)
                self.assertIsNone(self.owner.pending)
                self.assertEqual((attempt.command, attempt.world, attempt.submit_flags),
                    (COMMAND, WORLD, 13))
                self.assertEqual(attempt.outcome, "unknown")
                self.assert_blocked()
                with self.assertRaises(RuntimeError):
                    self.owner.complete()
                self.assertEqual(self.owner.command, COMMAND)
                self.assertFalse(self.client.calls)

    def test_transport_exception_keeps_exact_session_query_and_explicit_recovery_only(self):
        error = RuntimeError("fixture reply lost after dispatch")
        self.client.queue_error = error
        with self.assertRaises(RuntimeError) as caught:
            self.owner.submit()
        self.assertIs(caught.exception, error)
        attempt, query = self.owner.attempt, error.query
        self.assertIsInstance(query, v3_session.SessionCallbackResult)
        self.assertIs(attempt.query, query)
        self.assertIs(self.owner.pending, query)
        self.assertIs(attempt.submission_error, error)
        self.assert_blocked()
        with self.assertRaises(RuntimeError) as read_error:
            self.owner.complete()
        self.assertIs(attempt.completion_error, read_error.exception)
        self.assertFalse(self.process.freed)
        query.recover(7)  # explicit fixture identity; the SDK does not infer it
        self.finish(query, command=0, accepted=0)
        result = self.owner.complete()
        self.assertEqual(attempt.outcome, "consumed")
        self.assertIs(attempt.result, result)
        self.assertIs(attempt.submission_error, error)
        self.assertEqual(len(self.client.calls), 1)

    def test_error574_zero_pointer_and_possible_executed_prefix_stay_unknown(self):
        for operation, pointer in (("submit", 0), ("destroy", 0), ("validate", COMMAND)):
            with self.subTest(operation=operation, pointer=pointer):
                self.setUp()
                query = getattr(self.owner, operation)()
                self.finish(query, error=574, command=pointer)
                result = self.owner.complete()
                attempt = self.owner.attempt
                self.assertEqual(result.error, 574)
                self.assertEqual(result.command, pointer)
                self.assertIs(attempt.result, result)
                self.assertIs(attempt.query, query)
                self.assertIs(self.owner.pending, query)
                self.assertEqual(attempt.outcome, "unknown")
                self.assertEqual(self.owner.command, COMMAND)
                self.assertEqual((attempt.command, attempt.world), (COMMAND, WORLD))
                self.assertEqual(self.process.freed, [query.result_address])
                self.assert_blocked()

    def test_malformed_receipt_retains_exact_query_exception_and_allocation(self):
        query = self.owner.validate()
        self.finish(query, changes={0: commands.RESULT.size + 1})
        with self.assertRaises(ValueError) as caught:
            self.owner.complete()
        attempt = self.owner.attempt
        self.assertIs(attempt.completion_error, caught.exception)
        self.assertIs(attempt.query, query)
        self.assertIsNone(attempt.result)
        self.assertFalse(self.process.freed)
        self.assertEqual(attempt.outcome, "unknown")
        self.assert_blocked()

    def test_mismatched_decoded_receipt_does_not_replace_original_owner(self):
        mutations = {"action": {2: commands.Action.DESTROY}, "world": {15: WORLD + 1},
            "command": {4: COMMAND + 1}, "zero_pointer": {4: 0},
            "type": {3: TYPE + 1}, "buffer": {5: 0}, "capacity": {6: 4},
            "flags": {9: 99}, "bool": {10: 2}, "unexpected_acceptance": {11: 1},
            "count": {8: 1}, "reserved": {13: 1}, "wrong_truncation_status": {7: 6}}
        for label, changes in mutations.items():
            with self.subTest(mismatch=label):
                self.setUp()
                query = self.owner.validate(capacity=5)
                self.finish(query, changes=changes)
                result = self.owner.complete()
                self.assertIs(self.owner.attempt.result, result)
                self.assertEqual(self.owner.attempt.outcome, "unknown")
                self.assertIs(self.owner.pending, query)
                self.assertEqual((self.owner.command, self.owner.world), (COMMAND, WORLD))
                self.assert_blocked()

    def test_query_replacement_stays_explicit_unknown(self):
        query = self.owner.validate()
        self.owner.pending = object()
        with self.assertRaises(RuntimeError):
            self.owner.complete()
        self.assertIs(self.owner.attempt.query, query)
        self.assertEqual(self.owner.attempt.outcome, "unknown")
        self.assert_blocked()

    def test_known_native_preconditions_preserve_owned_pointer_not_receiver_rejection(self):
        cases = (("validate", 21), ("destroy", 87), ("destroy", 5023),
            ("submit", 21), ("submit", 1444), ("submit", 87), ("submit", 5023))
        for operation, error in cases:
            with self.subTest(operation=operation, error=error):
                self.setUp()
                query = getattr(self.owner, operation)()
                self.finish(query, error=error)
                result = self.owner.complete()
                self.assertEqual(result.error, error)
                self.assertEqual(self.owner.attempt.outcome, "owned")
                self.assertEqual(self.owner.command, COMMAND)
                self.assertIsNone(self.owner.pending)
                self.owner.validate()
                self.assertEqual(len(self.client.calls), 2)

    def test_only_native_proved_truncation_is_owned(self):
        for operation in ("validate", "serialize"):
            with self.subTest(operation=operation):
                self.setUp()
                query = getattr(self.owner, operation)(capacity=5)
                data = "不能建".encode("utf-8")[:5]
                self.finish(query, error=122, payload=data, buffer_size=9, validated=0)
                result = self.owner.complete()
                self.assertEqual(self.owner.attempt.outcome, "owned")
                self.assertEqual(self.owner.command, COMMAND)
                if operation == "validate":
                    self.assertTrue(result.reason_truncated)
                    self.assertEqual(result.reason, "不�")
                self.assertEqual(len(self.client.calls), 1)
        self.setUp()
        query = self.owner.validate(capacity=5)
        self.finish(query, error=122, buffer_size=5)
        self.owner.complete()
        self.assertEqual(self.owner.attempt.outcome, "unknown")
        self.assert_blocked()

    def test_clone_retains_source_and_exposes_separate_result_owner(self):
        query = self.owner.clone()
        self.finish(query, payload=struct.pack("<Q", COMMAND + 1), buffer_size=8)
        result = self.owner.complete()
        self.assertEqual(self.owner.attempt.outcome, "owned")
        self.assertEqual(self.owner.command, COMMAND)
        clone = commands.NativeCommand.from_result(self.client, result, process=self.process)
        self.assertEqual((clone.command, clone.world, clone.type_token), (COMMAND + 1, WORLD, TYPE))
        self.setUp()
        query = self.owner.clone()
        self.finish(query, error=8, payload=bytes(8), buffer_size=8)
        self.owner.complete()
        self.assertEqual(self.owner.attempt.outcome, "owned")
        self.assertEqual(self.owner.command, COMMAND)
        self.setUp()
        query = self.owner.clone()
        payload = struct.pack("<Q", COMMAND + 1)
        self.finish(query, error=8, payload=payload, buffer_size=0)
        result = self.owner.complete()
        self.assertEqual(result.cloned_command, 0)  # decoder cannot prove the hidden output
        self.assertEqual(result.data, payload)
        self.assertEqual(self.owner.attempt.outcome, "unknown")
        self.assertIs(self.owner.pending, query)
        self.assertEqual(self.owner.command, COMMAND)
        self.assert_blocked()

    def test_normal_destroy_and_both_receiver_bools_are_consumed_not_effect_proof(self):
        for operation, accepted in (("destroy", 0), ("submit", 0), ("submit", 1)):
            with self.subTest(operation=operation, accepted=accepted):
                self.setUp()
                query = getattr(self.owner, operation)()
                self.finish(query, command=0, accepted=accepted)
                result = self.owner.complete()
                self.assertEqual(result.accepted, accepted)
                self.assertEqual(self.owner.attempt.outcome, "consumed")
                self.assertEqual(self.owner.command, 0)
                self.assertIsNone(self.owner.pending)
                self.assertIs(self.owner.attempt.result, result)
                self.assert_blocked()
                if operation == "submit":
                    self.assertEqual(query.enqueue_result.rax, 0)  # raw result stays public
                    self.assertEqual(query.enqueue_result.target,
                        self.process.exports["PdxV3QueueSessionCallback"])
                    self.assertEqual(query.session_request_id, 9)

    def test_inconsistent_submit_acceptance_cannot_claim_known_ownership(self):
        for error, command, accepted in ((0, 0, 2), (21, COMMAND, 1), (574, 0, 1)):
            with self.subTest(error=error, accepted=accepted):
                self.setUp()
                query = self.owner.submit()
                self.finish(query, error=error, command=command, accepted=accepted)
                self.owner.complete()
                self.assertEqual(self.owner.attempt.outcome, "unknown")
                self.assertEqual(self.owner.command, COMMAND)
                self.assertIs(self.owner.pending, query)
                self.assert_blocked()

    def test_cached_receipt_is_retained_when_callback_storage_release_raises(self):
        query = self.owner.destroy()
        self.finish(query, command=0)
        error = RuntimeError("fixture free failure")
        self.process.free_error = error
        result = self.owner.complete()
        attempt = self.owner.attempt
        self.assertIsNone(attempt.completion_error)
        self.assertIs(attempt.result, result)
        self.assertIs(result, query._result)
        self.assertIs(query.cleanup_error, error)
        self.assertFalse(query._released)
        self.assertEqual(result.command, 0)
        self.assertEqual(attempt.command, COMMAND)
        self.assertEqual(self.owner.command, 0)
        self.assertEqual(attempt.outcome, "consumed")
        self.assertIsNone(self.owner.pending)
        with self.assertRaisesRegex(RuntimeError, "no native command operation pending"):
            self.owner.complete()
        self.assertFalse(self.process.freed)
        self.process.free_error = None
        query.close()  # Retry only original callback storage, not the command.
        self.assertTrue(query._released)
        self.assertIsNone(query.cleanup_error)
        self.assertIs(attempt.result, result)
        self.assertEqual(attempt.outcome, "consumed")

    def test_unchanged_pending_sentinel_retains_decoded_exception_receipt(self):
        query = self.owner.destroy()
        self.client.status = {"status": "ok", "request_id": 7,
            "completed_request_id": 7, "world_error": 31}
        with self.assertRaises(v3.QueryExecutionError) as caught:
            self.owner.complete()
        attempt = self.owner.attempt
        self.assertIs(attempt.completion_error, caught.exception)
        self.assertIs(attempt.result, caught.exception.result)
        self.assertEqual(attempt.result.error, v3.ERROR_IO_PENDING)
        self.assertEqual(attempt.command, COMMAND)
        self.assertIs(self.owner.pending, query)
        self.assertEqual(attempt.outcome, "unknown")
        self.assert_blocked()

    def test_create_forwards_source_world_for_factory_parameters_and_actor_only(self):
        for options, action in (({}, commands.Action.CREATE),
                ({"parameters": {}}, commands.Action.DESERIALIZE),
                ({"actor_id": 17}, commands.Action.DESERIALIZE),
                ({"parameters": {439: commands.UInt32(5)}, "actor_id": 17},
                 commands.Action.DESERIALIZE)):
            for world in (0, WORLD):
                with self.subTest(options=options, world=world):
                    self.setUp()
                    query = commands.create(self.client, TYPE, world=world,
                        process=self.process, **options)
                    fields = commands.RESULT.unpack(self.process.read(query.result_address,
                        commands.RESULT.size))
                    self.assertEqual((fields[2], fields[3], fields[15]), (action, TYPE, world))
                    self.assertEqual(self.client.status_reads, 0)  # no late lookup/inference
                    if action == commands.Action.DESERIALIZE:
                        tree = commands.decode_command(self.process.read(fields[5], fields[7]))
                        self.assertEqual(tree.key, commands.Token(TYPE))
                        if "actor_id" in options:
                            self.assertEqual(tree.value[0], commands.Assignment(
                                commands.Token(10304), commands.UInt32(17)))
        self.setUp()
        query = commands.request(self.client, commands.Action.CREATE, type_token=TYPE,
            process=self.process)  # raw world0 request remains callable, unchanged
        fields = commands.RESULT.unpack(self.process.read(query.result_address, commands.RESULT.size))
        self.assertEqual(fields[15], 0)
        self.assertEqual(self.client.status_reads, 0)


if __name__ == "__main__":
    unittest.main()
