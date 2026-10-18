"""Postcut5 General21256 wire edits. CPU native transports; no game calls."""
import asyncio
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import struct
import sys
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime import query_executor
from pdx_runtime.query_executor import PendingOperation
from pdx_runtime.v3_game import politics, _political_actions, _choices
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.diplomacy import actions as a
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.v3_game._choices import ChoicesBusyError
from tests.v3_game.test_v3_game_diplomatic_selected import Context as BaseContext, Fixture as BaseFixture, Callback, SessionCallback, TARGET


def setUpModule():
    modules = (commands, data, a, politics, _political_actions, _choices, query_executor)
    print("IMPORT_CUT " + json.dumps({m.__name__: {"path": str(Path(m.__file__).resolve()),
        "sha256": hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()} for m in modules}, sort_keys=True))
    print("INTERPRETER " + json.dumps({"executable": sys.executable, "version": sys.version}))


def A(token, value):
    return commands.Assignment(commands.Token(token), value)


def native_wire(actor=0, *, actor_present=True, targets=None, obligation=357, mode=24150):
    if targets is None:
        targets = (A(10374, commands.UInt32(0)), A(10375, commands.UInt32(TARGET)), A(24086, True))
    root = [A(499, (A(700, commands.UInt64(0x123456789abcdef0)),)),
            A(65, (A(12795, targets), A(11760, "increase_relations"),
                A(21268, commands.Token(obligation)), A(24152, commands.Token(mode)),
                A(61001, (A(commands.Token(61002).value, commands.Int64(-7)),))))]
    if actor_present:
        root.insert(1, A(10304, commands.UInt32(actor)))
    return commands.encode_command(21256, tuple(root))


def domains(tree):
    return [item.value for item in tree.value if a._command_key(item, 65)]


def target_blocks(tree):
    return [item.value for domain in domains(tree) for item in domain if a._command_key(item, 12795)]


def values(items, token):
    return [item.value for item in items if a._command_key(item, token)]


class Context(BaseContext):
    def __init__(self):
        super().__init__()
        self.loop = asyncio.get_running_loop()
        self.stage = asyncio.Event()
    async def perform(self, label, operation):
        self.performs.append(label)
        return await self.executor.perform(label, operation)


class GateCallback(Callback):
    def read(self):
        if not self.gate.is_set():
            return None
        return super().read()


class GateSessionCallback(SessionCallback):
    read = GateCallback.read


class ErrorCallback(Callback):
    def read(self):
        self._result = self.result
        self.partial_result = self.result
        self.error.query = self
        self.error.partial_result = self.result
        raise self.error


class Fixture(BaseFixture):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.original_wire = native_wire()
        self.original_type = 21256
        self.rebuilt_pointer = 0xE000
        self.serialize_error = self.deserialize_error = self.destroy_error = 0
        self.deserialize_pointer = self.rebuilt_pointer
        self.deserialize_type = 21256
        self.deserialize_exception = self.deserialize_read_error = None
        self.gates, self.queries_by_action = {}, {}
        self.wires, self.parsed = {}, None
        self.serializations = []
        self.deserializations = []
        self.destroyed = []
        self.validation_commands = []
        self.submit_commands = []

    def command_request(self, client, action, **kwargs):
        action = commands.Action(action)
        self.command_calls.append((action, dict(kwargs)))
        assert kwargs["adapter"] == "original-commands"
        assert kwargs["world"] == self.command_world
        address = 0x500000 + len(self.command_calls) * 0x100000
        capacity = max(kwargs.get("capacity", 0), len(kwargs.get("data", b"")))
        pointer = kwargs.get("command", 0)
        error = validated = accepted = 0
        token = self.original_type if pointer == self.clone_pointer or action == commands.Action.CLONE else 21256
        payload = b""
        full = 0
        if action == commands.Action.CLONE:
            self.wires[self.clone_pointer] = self.original_wire
            payload = self.clone_pointer.to_bytes(8, "little")
        elif action == commands.Action.SERIALIZE:
            self.serializations.append(pointer)
            assert len(self.serializations) == 1 and pointer == self.clone_pointer
            error = self.serialize_error
            full = capacity + 1 if error == 122 else len(self.original_wire)
            payload = bytes(capacity) if error else self.original_wire
        elif action == commands.Action.DESERIALIZE:
            self.deserializations.append(bytes(kwargs["data"]))
            if self.deserialize_exception:
                raise self.deserialize_exception
            tree = commands.decode_command(kwargs["data"])
            assert tree.key == commands.Token(21256)
            self.parsed = self.read_native(tree)
            pointer, error, token = self.deserialize_pointer, self.deserialize_error, self.deserialize_type
            if pointer:
                self.wires[pointer] = kwargs["data"]
            payload = kwargs["data"]
        elif action == commands.Action.VALIDATE:
            self.validation_commands.append(pointer)
            error, validated = self.validation_error, self.validated
            payload = self.validation_reason.encode()
        elif action == commands.Action.SUBMIT:
            self.submit_commands.append(pointer)
            error, accepted = self.submit_error, self.accepted
            pointer = 0
        elif action == commands.Action.DESTROY:
            self.destroyed.append(pointer)
            error, pointer = self.destroy_error, 0
        else:
            raise AssertionError("No Create or other command action is allowed")
        if not full:
            full = len(payload)
        if error or not pointer:
            token = 0
        if action == commands.Action.VALIDATE and error == 122:
            full = capacity + 1
            payload = (payload + bytes(capacity))[:capacity]
        raw = commands.RESULT.pack(commands.RESULT.size, error, action, token, pointer,
            address + commands.RESULT.size if capacity else 0, capacity, full, 0,
            kwargs.get("submit_flags", 4), validated, accepted, 17, 0, 0x1000, self.command_world, 0) + payload
        receipt = commands.CommandResult.from_bytes(raw)
        cls = SessionCallback if action == commands.Action.SUBMIT else Callback
        if action in self.gates:
            cls = GateSessionCallback if action == commands.Action.SUBMIT else GateCallback
        if action == commands.Action.DESERIALIZE and self.deserialize_read_error:
            cls = ErrorCallback
        query = cls(receipt, adapter=kwargs["adapter"], address=address)
        if action in self.gates:
            query.gate = self.gates[action]
        if isinstance(query, ErrorCallback):
            query.error = self.deserialize_read_error
        self.queries_by_action[action] = query
        self.ctx.loop.call_soon_threadsafe(self.ctx.stage.set)
        return query

    @staticmethod
    def read_native(tree):
        # Deliberately different fallback: materialization must preserve native
        # original omissions, not silently inherit these values. Repeated blocks
        # accumulate; missing fields in a later block do not reset earlier refs.
        result = {"actor": 99, 10374: 88, 10375: 77, 24084: 66, 24085: 55, 24086: True}
        for item in tree.value:
            if a._command_key(item, 10304):
                result["actor"] = item.value.value
            if a._command_key(item, 65):
                for field in item.value:
                    if a._command_key(field, 12795):
                        for leaf in field.value:
                            if isinstance(leaf, commands.Assignment) and isinstance(leaf.key, commands.Token) and leaf.key.value in result:
                                result[leaf.key.value] = leaf.value.value if isinstance(leaf.value, commands.UInt32) else leaf.value
                    elif isinstance(field, commands.Assignment) and isinstance(field.key, commands.Token) and field.key.value in (11760, 21268, 24152):
                        result[field.key.value] = field.value
        return result


class DiplomaticCommandEditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ctx = Context()
        self.api, self.f = DiplomacyAPI(self.ctx), Fixture(self.ctx)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("evaluate", "list_count", "list_element", "project_page", "command_value", "release"):
            method = self.f.project if name == "project_page" else getattr(self.f, name)
            self.stack.enter_context(patch.object(data, name, side_effect=method))
        self.stack.enter_context(patch.object(commands, "request", side_effect=self.f.command_request))
        self.create = self.stack.enter_context(patch.object(commands, "create", side_effect=AssertionError("No context create")))
        self.old_create = self.stack.enter_context(patch.object(run_mod.Run, "create", side_effect=AssertionError("No old create")))
        self.old_serialize = self.stack.enter_context(patch.object(run_mod.Run, "serialize", side_effect=AssertionError("No context actor serialize")))
        self.catalog = await self.api.diplomatic_action_choices(TARGET)
        self.choice = self.catalog[1]

    def calls(self):
        return [action for action, _ in self.f.command_calls]

    def no_context_actor(self):
        self.assertEqual(self.ctx.country_reads, 0)
        for forbidden in (self.create, self.old_create, self.old_serialize):
            forbidden.assert_not_called()

    async def clean(self):
        await self.catalog.close()
        self.assertFalse(self.f.handles)
        self.assertFalse(self.ctx.owner_pool.snapshot())
        self.no_context_actor()

    async def test_explicit_four_fields_one_original_clone_one_serialize_one_rebuild_then_given_owner(self):
        result = await self.api.execute_diplomatic_action(self.choice, obligation="owe_obligation",
            first_state=0, second_state=0xffffffff, country_only=False, readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.f.last_selected, 22)
        self.assertEqual(self.calls(), [commands.Action.CLONE, commands.Action.SERIALIZE,
            commands.Action.DESERIALIZE, commands.Action.VALIDATE, commands.Action.SUBMIT, commands.Action.DESTROY])
        self.assertEqual(self.f.validation_commands, [self.f.rebuilt_pointer])
        self.assertEqual(self.f.submit_commands, [self.f.rebuilt_pointer])
        self.assertEqual(self.f.destroyed, [self.f.clone_pointer])
        self.assertEqual(self.f.parsed[24084], 0)
        self.assertEqual(self.f.parsed[24085], 0xffffffff)
        self.assertIs(self.f.parsed[24086], False)
        self.assertEqual(self.f.parsed[21268], commands.Token(21620))
        self.assertEqual(result["original_wire"], self.f.original_wire)
        self.assertTrue(result["wire_edit_performed"])
        self.assertIsNone(result["native_parameter_edit_observed"])
        self.assertEqual(result["unclosed_selectors"], ["intent", "pact", "proposal_accept_decline", "stop"])
        self.assertEqual(self.ctx.performs[-1], "diplomacy.execute_diplomatic_action")
        await self.clean()

    async def test_none_parameters_keep_original_native_route_without_serialize_or_rebuild(self):
        result = await self.api.execute_diplomatic_action(self.choice, obligation=None, first_state=None,
            second_state=None, country_only=None, readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.calls(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.SUBMIT])
        self.assertEqual(self.f.validation_commands, [self.f.clone_pointer])
        self.assertEqual(result["supported_command_edits"], ["obligation", "first_state", "second_state", "country_only"])
        self.assertEqual(result["unclosed_selectors"], ["intent", "pact", "proposal_accept_decline", "stop"])
        self.assertIn("separate command", result["command_edit_scope"])
        self.assertIn("not in-place source or Window setters", result["action_scope"])
        self.assertIn("not edited-selector readback", result["command_edit_scope"])
        await self.clean()

    async def test_obligation_none_string_is_an_edit_and_native_callin_is_distinct(self):
        self.f.original_wire = native_wire(obligation=21620)
        first = await self.api.execute_diplomatic_action(self.choice, obligation="none", readback=False)
        self.assertTrue(first["accepted"])
        self.assertEqual(self.f.parsed[21268], commands.Token(357))
        self.f.serializations.clear()
        second = await self.api.execute_diplomatic_action(self.choice, obligation="call_in_obligation", readback=False)
        self.assertTrue(second["accepted"])
        self.assertEqual(self.f.parsed[21268], commands.Token(21619))
        await self.clean()

    async def test_state_edit_keeps_original_country_only_true_and_validation_mode(self):
        self.f.original_wire = native_wire(mode=24431)
        result = await self.api.execute_diplomatic_action(self.choice, first_state=0xf0000004, readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.f.parsed[24084], 0xf0000004)
        self.assertIs(self.f.parsed[24086], True)
        self.assertEqual(self.f.parsed[24152], commands.Token(24431))
        await self.clean()

    async def test_root_actor_zero_uintmax_and_foreign_context_are_never_filled_or_retargeted(self):
        for actor in (0, 0xffffffff, 0xf1000005):
            with self.subTest(actor=actor):
                self.f.original_wire = native_wire(actor=actor)
                self.f.serializations.clear()
                result = await self.api.execute_diplomatic_action(self.choice, country_only=False, readback=False)
                self.assertTrue(result["accepted"])
                self.assertEqual(self.f.parsed["actor"], actor)
                self.assertEqual(self.f.parsed[10374], 0)
                self.assertEqual(self.f.parsed[10375], TARGET)
        await self.clean()

    async def test_global_omissions_materialize_native_originals_actor_independent_from_targets_first(self):
        self.f.original_wire = native_wire(actor_present=False, targets=())
        result = await self.api.execute_diplomatic_action(self.choice, obligation="none", readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.f.parsed["actor"], 0xffffffff)
        for key in (10374, 10375, 24084, 24085):
            self.assertEqual(self.f.parsed[key], 0xffffffff)
        self.assertIs(self.f.parsed[24086], False)
        self.assertEqual(len(result["original_writer_omissions"]), 6)
        await self.clean()

    async def test_duplicate_targets_accumulate_no_per_block_defaults_and_all_matching_edits(self):
        first = (A(10374, commands.UInt32(0)), A(24084, commands.UInt32(0x81000007)), A(61003, "keep-first"))
        second = (A(10375, commands.UInt32(TARGET)), A(24085, commands.UInt32(0x82000008)), A(24086, True),
                  A(24084, commands.UInt32(0x83000009)), A(61003, "keep-second"))
        root = commands.decode_command(native_wire(targets=first))
        d = domains(root)[0]
        d = (d[0], A(12795, second), *d[1:], A(21268, commands.Token(21619)))
        body = tuple(A(65, d) if a._command_key(x, 65) else x for x in root.value)
        self.f.original_wire = commands.encode_tree(commands.Assignment(root.key, body))
        result = await self.api.execute_diplomatic_action(self.choice, first_state=0, obligation="owe_obligation", readback=False)
        edited = commands.decode_command(result["edited_wire"])
        blocks = target_blocks(edited)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(values(blocks[0], 10375), [])
        self.assertEqual(values(blocks[1], 10374), [])
        self.assertEqual(values(blocks[0], 24084), [commands.UInt32(0)])
        self.assertEqual(values(blocks[1], 24084), [commands.UInt32(0)])
        self.assertEqual(values(domains(edited)[0], 21268), [commands.Token(21620), commands.Token(21620)])
        self.assertEqual(self.f.parsed[10374], 0)
        self.assertEqual(self.f.parsed[24085], 0x82000008)
        self.assertFalse(result["original_writer_omissions"])
        self.assertEqual(values(blocks[0], 61003), ["keep-first"])
        self.assertEqual(values(blocks[1], 61003), ["keep-second"])
        await self.clean()

    async def test_global_missing_fields_append_only_to_last_of_repeated_parameters_blocks(self):
        root = commands.decode_command(native_wire(targets=(A(10374, commands.UInt32(0)),)))
        body = root.value + (A(65, (A(12795, (A(10375, commands.UInt32(TARGET)),)),)),)
        self.f.original_wire = commands.encode_tree(commands.Assignment(root.key, body))
        result = await self.api.execute_diplomatic_action(self.choice, second_state=0, readback=False)
        blocks = target_blocks(commands.decode_command(result["edited_wire"]))
        self.assertEqual(len(blocks), 2)
        self.assertEqual(values(blocks[0], 24084), [])
        self.assertEqual(values(blocks[1], 24084), [commands.UInt32(0xffffffff)])
        self.assertEqual(values(blocks[1], 24085), [commands.UInt32(0)])
        self.assertEqual(values(blocks[1], 24086), [False])
        self.assertEqual(self.f.parsed[10374], 0)
        await self.clean()

    async def test_unknown_supported_fields_order_duplicates_headers_and_widths_are_preserved(self):
        root = commands.decode_command(native_wire())
        extra = (A(61010, commands.Float32(1.25)), A(61010, commands.UInt64(0xffffffffffffffff)),
            commands.Assignment(commands.NamedToken("native_future_field"),
                (commands.NamedToken("native_future_enum"), commands.Int64(-0x100000001))))
        self.f.original_wire = commands.encode_tree(commands.Assignment(root.key, root.value + extra))
        result = await self.api.execute_diplomatic_action(self.choice, first_state=0, readback=False)
        edited = commands.decode_command(result["edited_wire"])
        self.assertEqual(edited.value[-3:], extra)
        self.assertEqual(edited.value[0], root.value[0])
        self.assertEqual(values(domains(edited)[0], 11760), ["increase_relations"])
        self.assertEqual(values(domains(edited)[0], 24152), [commands.Token(24150)])
        await self.clean()

    async def test_unsupported_opaque_stream_keeps_original_bytes_and_does_not_rebuild(self):
        self.f.original_wire = b"opaque unsupported original"
        result = await self.api.execute_diplomatic_action(self.choice, first_state=0, readback=False)
        self.assertEqual(result["status"], "edit_unimplemented")
        self.assertEqual(result["original_wire"], self.f.original_wire)
        self.assertIsNone(result["accepted"])
        self.assertEqual(self.calls(), [commands.Action.CLONE, commands.Action.SERIALIZE, commands.Action.DESTROY])
        await self.clean()

    async def test_noncanonical_enum_and_unsupported_ref_form_are_not_silently_normalized(self):
        for wire in (native_wire(obligation=1114), native_wire(mode=357),
                     native_wire(targets=(A(24084, 0),))):
            self.f.original_wire = wire
            self.f.serializations.clear()
            before = len(self.f.deserializations)
            result = await self.api.execute_diplomatic_action(self.choice, first_state=0, readback=False)
            self.assertEqual(result["status"], "edit_unimplemented")
            self.assertEqual(len(self.f.deserializations), before)
            self.assertEqual(result["original_wire"], wire)
        await self.clean()

    async def test_truncated122_has_no_valid_partial_tree_or_auto_serialize_retry(self):
        self.f.serialize_error = 122
        result = await self.api.execute_diplomatic_action(self.choice, first_state=0, readback=False)
        self.assertEqual(result["native_error"], 122)
        self.assertEqual(result["original_serialization"].buffer_size, 65537)
        self.assertFalse(result["wire_edit_performed"])
        self.assertFalse(self.f.deserializations)
        self.assertEqual(len(self.f.serializations), 1)
        self.assertEqual(self.f.destroyed, [self.f.clone_pointer])
        await self.clean()

    async def test_deserialize_error_with_nonzero_pointer_keeps_unadopted_owner_and_original(self):
        self.f.deserialize_error = 87
        result = await self.api.execute_diplomatic_action(self.choice, first_state=0, readback=False)
        self.assertIsNone(result["accepted"])
        self.assertEqual(result["deserialize_result"].command, self.f.rebuilt_pointer)
        self.assertEqual(result["native_error"], 87)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertFalse(result["ownership_settled"])
        self.assertFalse(self.f.destroyed)
        self.assertFalse(self.f.validation_commands)
        with self.assertRaises(ChoicesBusyError):
            await self.catalog.close()
        self.no_context_actor()

    async def test_deserialize_completed_error_without_pointer_keeps_raw_primary_and_cleans_known_original(self):
        self.f.deserialize_error, self.f.deserialize_pointer = 87, 0
        result = await self.api.execute_diplomatic_action(self.choice, first_state=0, readback=False)
        self.assertEqual(result["native_result"].error, 87)
        self.assertEqual(result["native_result"].command, 0)
        self.assertIsNone(result["accepted"])
        self.assertEqual(self.f.destroyed, [self.f.clone_pointer])
        await self.clean()

    async def test_deserialize_read_exception_preserves_same_query_partial_pointer_and_source_graph(self):
        error = OSError("Original deserialize read failed after partial owner")
        self.f.deserialize_read_error = error
        with self.assertRaises(OSError) as caught:
            await self.api.execute_diplomatic_action(self.choice, first_state=0, readback=False)
        self.assertIs(caught.exception, error)
        query = self.f.queries_by_action[commands.Action.DESERIALIZE]
        self.assertIs(error.query, query)
        runs = [r for r in self.ctx.owner_pool.snapshot() if isinstance(r, run_mod.Run) and getattr(r, "unadopted_command", None)]
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertIs(run.unadopted_command, query.result)
        self.assertIs(run.public_result["deserialize_query"], query)
        self.assertEqual(run.owners[0].command, self.f.clone_pointer)
        self.assertFalse(self.f.destroyed)
        with self.assertRaises(ChoicesBusyError):
            await self.catalog.close()
        self.no_context_actor()

    async def test_deserialize_dispatch_unknown_is_not_replayed_or_destroyed(self):
        error = RuntimeError("Original deserialize dispatch outcome unknown")
        self.f.deserialize_exception = error
        with self.assertRaises(RuntimeError) as caught:
            await self.api.execute_diplomatic_action(self.choice, first_state=0, readback=False)
        self.assertIs(caught.exception, error)
        self.assertEqual(len(self.f.deserializations), 1)
        self.assertFalse(self.f.destroyed)
        self.assertFalse(self.f.validation_commands)
        with self.assertRaises(ChoicesBusyError):
            await self.catalog.close()
        self.no_context_actor()

    async def test_deserialize_cancellation_retains_original_operation_graph(self):
        self.f.deserialize_exception = asyncio.CancelledError("Original cancelled dispatch")
        with self.assertRaises(asyncio.CancelledError):
            await self.api.execute_diplomatic_action(self.choice, first_state=0, readback=False)
        self.assertEqual(len(self.f.deserializations), 1)
        self.assertFalse(self.f.destroyed)
        with self.assertRaises(ChoicesBusyError):
            await self.catalog.close()
        self.no_context_actor()

    async def test_real_pending_deserialize_resumes_same_query_once_and_holds_original_clone(self):
        gate = threading.Event()
        self.f.gates[commands.Action.DESERIALIZE] = gate
        self.ctx.executor.timeout = 0
        pending = await self.api.execute_diplomatic_action(self.choice, first_state=0, readback=False)
        self.assertIsInstance(pending, PendingOperation)
        # Use the actual stage event, never sleep/poll a running process.
        while commands.Action.DESERIALIZE not in self.f.queries_by_action:
            self.ctx.stage.clear()
            await asyncio.wait_for(self.ctx.stage.wait(), 2)
        query = self.f.queries_by_action[commands.Action.DESERIALIZE]
        try:
            with self.assertRaises(ChoicesBusyError):
                await self.catalog.close()
            self.assertFalse(self.f.destroyed)
            self.assertIn(query, self.ctx.owner_pool.snapshot())
        finally:
            gate.set()
        result = await pending.resume(timeout=2)
        self.assertTrue(result["accepted"])
        self.assertIs(self.f.queries_by_action[commands.Action.DESERIALIZE], query)
        self.assertEqual(len(self.f.deserializations), 1)
        self.assertEqual(len(self.f.serializations), 1)
        self.assertEqual(self.calls().count(commands.Action.CLONE), 1)
        await self.clean()

    async def test_validation_false_is_native_rejection_no_submit_and_both_owners_clean(self):
        self.f.validated = 0
        result = await self.api.execute_diplomatic_action(self.choice, country_only=False, readback=False)
        self.assertIs(result["accepted"], False)
        self.assertEqual(result["reason"], self.f.validation_reason)
        self.assertFalse(self.f.submit_commands)
        self.assertEqual(self.f.destroyed, [self.f.rebuilt_pointer, self.f.clone_pointer])
        await self.clean()

    async def test_native_true_acceptance_with_error_keeps_both_primary_values_and_skips_readback(self):
        self.f.submit_error, self.f.accepted = 87, 1
        result = await self.api.execute_diplomatic_action(self.choice, first_state=0)
        self.assertIs(result["accepted"], True)
        self.assertEqual(result["native_error"], 87)
        self.assertEqual(result["native_result"].accepted, 1)
        self.assertIn("readback_skipped", result)
        self.assertNotIn("after", result)
        self.assertFalse(self.f.destroyed)
        self.no_context_actor()

    async def test_cleanup_unknown_does_not_erase_acceptance_or_start_readback(self):
        self.f.destroy_error = 574
        result = await self.api.execute_diplomatic_action(self.choice, first_state=0)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["submission"].error, 0)
        self.assertEqual(result["cleanup"]["native_result"].error, 574)
        self.assertIn("readback_skipped", result)
        self.assertNotIn("after", result)
        self.assertEqual(self.f.submit_commands, [self.f.rebuilt_pointer])
        self.no_context_actor()

    async def test_readback_error_is_independent_of_the_selected_submission(self):
        original_project = self.f.project
        def project(*args, **kwargs):
            if self.f.submit_commands:
                raise RuntimeError("Original row after read failed")
            return original_project(*args, **kwargs)
        with patch.object(data, "project_page", side_effect=project):
            result = await self.api.execute_diplomatic_action(self.choice, first_state=0)
        self.assertTrue(result["accepted"])
        self.assertIsInstance(result["readback_error"], RuntimeError)
        self.assertEqual(len(self.f.submit_commands), 1)
        self.no_context_actor()

    async def test_after_cancellation_keeps_same_exception_and_completed_primary_without_overwriting_context(self):
        error = asyncio.CancelledError("Original row after cancelled after accepted submission")
        preexisting_query_state = {"native_query": "preexisting context"}
        error.query_state = preexisting_query_state
        error.observed_result = None
        original_project = self.f.project
        def project(*args, **kwargs):
            if self.f.submit_commands:
                raise error
            return original_project(*args, **kwargs)
        with patch.object(data, "project_page", side_effect=project):
            with self.assertRaises(asyncio.CancelledError) as caught:
                await self.api.execute_diplomatic_action(self.choice, first_state=0)
        self.assertIs(caught.exception, error)
        self.assertIs(error.query_state, preexisting_query_state)
        self.assertIsNone(error.observed_result)
        context = getattr(error, "diplomatic_action_context", None)
        submission = self.f.queries_by_action[commands.Action.SUBMIT].result
        retained = [owner for lease in self.choice._catalog._leases for owner in lease._owners
            if getattr(owner, "status", None) == "complete"
            and isinstance(getattr(owner, "public_result", None), dict)
            and owner.public_result.get("submission") is submission]
        print("CANONICAL_AFTER_CANCEL " + json.dumps({
            "path": "choice._catalog._leases -> lease._owners -> completed actionRun.public_result",
            "matching_primary_owners": len(retained), "active_leases": self.catalog.active_leases,
            "same_submission": bool(retained), "exception_has_choices": hasattr(error, "choices")}))
        self.assertEqual(len(retained), 1)
        self.assertIs(retained[0].public_result["accepted"], True)
        self.assertIsInstance(context, dict)
        primary = context["completed_primary_result"]
        self.assertIs(primary, retained[0].public_result)
        self.assertIs(primary["submission"], submission)
        self.assertIs(primary["native_result"], submission)
        self.assertIs(primary["accepted"], True)
        self.assertIs(context["submission"], submission)
        self.assertEqual(self.f.submit_commands, [self.f.rebuilt_pointer])
        self.assertEqual(self.calls().count(commands.Action.SUBMIT), 1)
        self.assertEqual(self.calls().count(commands.Action.CLONE), 1)
        self.assertTrue(primary["ownership_settled"])
        self.no_context_actor()

    async def test_bad_argument_types_and_non_general_choices_fail_before_producer(self):
        for kwargs in ({"obligation": False}, {"obligation": "owe"}, {"first_state": True},
                {"first_state": -1}, {"second_state": 1 << 32}, {"country_only": 0}, {"readback": 0}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises((TypeError, ValueError)):
                    await self.api.execute_diplomatic_action(self.choice, **kwargs)
        self.assertEqual(self.f.command_calls, [])
        with self.assertRaises(TypeError):
            await self.api.execute_diplomatic_action(22, first_state=0)
        await self.clean()


if __name__ == "__main__":
    unittest.main()
