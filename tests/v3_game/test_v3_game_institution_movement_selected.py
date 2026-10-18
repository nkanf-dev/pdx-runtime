"""Post-cut3 original Institution/PoliticalMovement selected-command consumers.

Real _Run readers, exact Any headers, origin-copy path, NativeCommand and executor.
Only native transports are CPU fixtures. No live process or synthetic commands.
"""
import asyncio
from contextlib import ExitStack
import struct
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.query_executor import PendingOperation, SerializedQueryExecutor
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from tests.v3_game.test_v3_game_diplomatic_selected import Callback, SessionCallback, Fixture as CommandTransport


class Memory:
    def __init__(self):
        self.next, self.blocks = 0x10000, {}
    def allocate(self, size):
        address = self.next
        self.next += 0x100
        self.blocks[address] = bytearray(size)
        return address
    def write(self, address, value):
        self.blocks[address][:len(value)] = value
    def read(self, address, size):
        return bytes(self.blocks[address][:size])
    def free(self, address):
        del self.blocks[address]
    def resolve_export(self, module, name):
        assert (module, name) == ("ntdll.dll", "memcpy")
        return 0x44


class Context:
    def __init__(self):
        self.native, self.process = object(), Memory()
        self.adapters = {name: "original-" + name for name in ("data", "commands", "access", "call")}
        self.world, self.actor = 0, 0x80000002
        self.types = {"Country": 71, "Institution": 777, "PoliticalMovement": 778}
        self.executor = SerializedQueryExecutor(timeout=2)
        self.owner_pool, self.diagnostics = self.executor.owner_pool, self.executor.diagnostics
        self.loop = asyncio.get_running_loop()
        self.submit_started = asyncio.Event()
        self.queries, self.performs, self.country_reads, self.refreshes = [], [], 0, 0
    async def refresh(self):
        self.refreshes += 1
    async def country_id(self):
        self.country_reads += 1
        return self.actor
    async def type_id(self, name):
        return self.types[name]
    async def query(self, label, factory, *, owner=None):
        self.queries.append(label)
        return await self.executor.query(label, factory, owner=owner)
    async def perform(self, label, operation):
        self.performs.append(label)
        return await self.executor.perform(label, operation)
    def support(self, name):
        raise AssertionError("No token factory, identity tree or retargeting support is allowed")


class GateSessionCallback(SessionCallback):
    def read(self):
        if not self.gate.is_set():
            return None
        return super().read()


class Fixture:
    def __init__(self, ctx):
        self.ctx, self.handles, self.origins = ctx, {}, {}
        self.next_handle, self.next_pointer = 100, 0x80000
        self.events, self.command_calls, self.released, self.producers = [], [], [], []
        self.elements, self.last_producer, self.last_carrier = {}, None, None
        self.institution_id, self.movement_id = 0, 0x86000004
        self.institution_level, self.institution_target, self.institution_changing = 1, 0, False
        self.institution_key, self.optional_fields = "institution_education", True
        self.bolstered, self.suppressed = False, False
        self.command_world, self.clone_pointer = 0x9001, 0xD000
        self.clone_error, self.validation_error, self.validated = 0, 0, 1
        self.accepted, self.submit_error, self.submit_exception = 1, 0, None
        self.validation_reason = "Native current rule reason"
        self.producer_empty, self.carrier_null = False, False
        self.producer_error, self.release_error = 0, 0
        self.producer_exception, self.after_exception = None, None
        self.after_error, self.after_override = 0, {}
        self.apply_effect, self.submitted, self.bad_ack = True, False, False
        self.submit_gate, self.submit_query = None, None
        self.row_spelling = "owned"

    def result(self, kind=data.Kind.EMPTY, value=None, *, action=data.Action.EVALUATE,
            type_id=0, native_name="", pointer=0, held=True, error=0, snapshot=None, total=None):
        handle = self.next_handle if held else 0
        if held:
            self.next_handle += 1
        if snapshot is None:
            snapshot = value.encode() if isinstance(value, str) else b""
        bits = value.bits if isinstance(value, data.FixedPoint) else int(value) if type(value) in (int, bool) else 0
        header = list(data.REQUEST.unpack(bytes(data.REQUEST.size)))
        header[0] = data.REQUEST.size + len(snapshot)
        header[1], header[2], header[4] = error, action, type_id
        header[9], header[10], header[11] = len(snapshot) if total is None else total, 17, kind
        header[12], header[13], header[14] = 0x1000, pointer, handle
        header[15], header[16], header[17] = 0x2000, 0x3000, 0x4000
        header[18], header[19] = len(snapshot), len(snapshot)
        header[24], header[25], header[26] = data.REQUEST.size, len(snapshot), bits & 0xFFFFFFFFFFFFFFFF
        header[28] = native_name.encode().ljust(192, b"\0")
        result = data.DataResult.from_bytes(data.REQUEST.pack(*header) + snapshot)
        if held:
            self.handles[handle] = result
        return result

    def query(self, result, **kwargs):
        return Callback(result, adapter=kwargs.get("adapter", "original-data"))

    def scalar(self, value, **kwargs):
        kind = (data.Kind.BOOL if type(value) is bool else data.Kind.I32 if type(value) is int
            else data.Kind.FIXED if isinstance(value, data.FixedPoint) else data.Kind.STRING if isinstance(value, str)
            else data.Kind.EMPTY)
        return self.result(kind, value, **kwargs)

    def evaluate(self, client, expression, bindings, **kwargs):
        self.events.append((expression, tuple(bindings), kwargs["adapter"]))
        assert kwargs["adapter"] == "original-data"
        if expression in ("Country.AccessInstitutions", "Country.AccessPoliticalMovements"):
            name = "Institution" if expression.endswith("Institutions") else "PoliticalMovement"
            assert len(bindings) == 1 and not bindings[0].retained
            return self.query(self.result(data.Kind.LIST, native_name=name), **kwargs)
        if expression.startswith("Institution.StartChangingLevelTo(") or expression in (
                "PoliticalMovement.ToggleBolstering", "PoliticalMovement.ToggleSuppression"):
            assert len(bindings) == 1
            binding = bindings[0]
            native = self.handles[binding.object]
            name = "Institution" if expression.startswith("Institution.") else "PoliticalMovement"
            assert binding.retained and binding.type_id == native.type_id
            assert native.native_type_name == run_mod.owned_type_name(name)
            self.producers.append((expression, binding, native))
            if self.producer_exception:
                raise self.producer_exception
            payload = struct.pack("<3Q", 0xB000, 0xC000, 0xA000)
            result = self.result(data.Kind.EMPTY if self.producer_empty else data.Kind.COMMAND,
                native_name="CDataCommand", error=self.producer_error,
                snapshot=b"" if self.producer_empty else payload)
            self.last_producer = result
            return self.query(result, **kwargs)
        name, getter = expression.split(".", 1)
        binding = bindings[0]
        assert binding.retained and binding.object in self.handles
        native = self.handles[binding.object]
        assert binding.type_id == native.type_id
        values = {"Institution": {"GetKey": self.institution_key, "GetName": "#bold Education#!",
                "GetInvestmentLevel": self.institution_level, "GetTargetInvestmentLevel": self.institution_target,
                "IsChangingLevel": self.institution_changing, "GetMaxLevel": 5,
                "GetInvestmentCost": data.FixedPoint(2000000), "GetTimeToReachTargetLevelDesc": "Native duration"},
            "PoliticalMovement": {"GetName": "#bold Liberal Movement#!",
                "IsBolstered": self.bolstered, "IsSuppressed": self.suppressed}}
        value = values[name][getter]
        if self.submitted:
            if self.after_exception:
                raise self.after_exception
            value = self.after_override.get(expression, value)
        return self.query(self.scalar(value, error=self.after_error if self.submitted else 0), **kwargs)

    def list_count(self, client, handle, **kwargs):
        assert self.handles[handle].kind == data.Kind.LIST
        return self.query(self.result(action=data.Action.LIST_COUNT, total=1, held=False), **kwargs)

    def list_element(self, client, handle, index, **kwargs):
        name = self.handles[handle].native_type_name
        type_id = 188 if name == "Institution" else 219
        pointer = self.next_pointer
        self.next_pointer += 0x100
        spelling = run_mod.owned_type_name(name) if self.row_spelling == "owned" else \
            ".?AU?$SAnyType@PEAVCData" + name + "@@@PdxAnyImpl@@"
        result = self.result(data.Kind.OBJECT, action=data.Action.LIST_ELEMENT,
            type_id=type_id, pointer=pointer, native_name=spelling)
        self.origins[pointer + 0x10] = self.institution_id if name == "Institution" else self.movement_id
        self.elements[result.value_handle] = result
        return self.query(result, **kwargs)

    def enumerate_functions(self, client, type_id, **kwargs):
        assert type_id == self.ctx.types["Institution"]
        names = (["GetKey"] if self.institution_key is not None else [])
        if self.optional_fields:
            names += ["GetMaxLevel", "GetInvestmentCost"]
        rows = tuple(NS(name=name, argument_count=0, callbacks=(1, 0, 0, 0)) for name in names)
        return self.query(NS(error=0, rows=rows, total=len(rows), next=len(rows)), **kwargs)

    def submit_call(self, client, target, args, **kwargs):
        assert target == 0x44 and kwargs["adapter"] == "original-access"
        assert kwargs["call_adapter"] == "original-call"
        output, source, size = args
        self.ctx.process.write(output, self.origins[source].to_bytes(size, "little"))
        return self.query(NS(error=0, callee_result=NS(error=0, rax=output)), **kwargs)

    def command_value(self, client, handle, **kwargs):
        assert self.handles[handle] is self.last_producer
        payload = struct.pack("<4Q", 0 if self.carrier_null else 0xB000,
            0 if self.carrier_null else 0xC000, 0xA000, self.command_world)
        result = self.result(data.Kind.COMMAND, action=data.Action.COMMAND_VALUE,
            native_name="CDataCommand", snapshot=payload, total=32, held=False)
        self.last_carrier = result
        return self.query(result, **kwargs)

    def command_request(self, client, action, **kwargs):
        query = CommandTransport.command_request(self, client, action, **kwargs)
        if action == commands.Action.SUBMIT:
            self.submitted = True
            expression = self.producers[-1][0]
            if self.apply_effect and self.accepted and not self.submit_error:
                if expression.startswith("Institution."):
                    self.institution_target = int(expression.removeprefix("Institution.StartChangingLevelTo(").removesuffix(")"))
                    self.institution_changing = True
                elif expression.endswith("ToggleBolstering"):
                    self.bolstered = not self.bolstered
                else:
                    self.suppressed = not self.suppressed
            if self.bad_ack:
                query.session_status.completed_request_id = query.session_request_id - 1
            if self.submit_gate is not None:
                replacement = GateSessionCallback(query.result, adapter=query.adapter, address=query.result_address)
                replacement.gate = self.submit_gate
                query = replacement
            self.submit_query = query
            self.ctx.loop.call_soon_threadsafe(self.ctx.submit_started.set)
        return query

    def release(self, client, handle, **kwargs):
        assert kwargs["adapter"] == "original-data" and handle in self.handles
        self.released.append(handle)
        if not self.release_error:
            del self.handles[handle]
        return self.query(self.result(action=data.Action.RELEASE, held=False, error=self.release_error), **kwargs)


class InstitutionMovementSelectedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ctx = Context()
        self.api, self.f = politics.PoliticsAPI(self.ctx), Fixture(self.ctx)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("evaluate", "list_count", "list_element", "enumerate_functions", "command_value", "release"):
            self.stack.enter_context(patch.object(data, name, side_effect=getattr(self.f, name)))
        self.stack.enter_context(patch.object(politics.access, "submit_call", side_effect=self.f.submit_call))
        self.stack.enter_context(patch.object(commands, "request", side_effect=self.f.command_request))
        self.create = self.stack.enter_context(patch.object(commands, "create", side_effect=AssertionError("No Create")))
        self.serialize = self.stack.enter_context(patch.object(commands.NativeCommand, "serialize", side_effect=AssertionError("No Serialize")))
        self.old_create = self.stack.enter_context(patch.object(run_mod.Run, "create", side_effect=AssertionError("No old factory")))

    def actions(self):
        return [action for action, _ in self.f.command_calls]

    def no_synthesis(self):
        for mock in (self.create, self.serialize, self.old_create):
            mock.assert_not_called()

    def clean(self):
        self.assertFalse(self.f.handles)
        self.assertFalse(self.ctx.process.blocks)
        self.assertFalse(self.ctx.owner_pool.snapshot())
        self.no_synthesis()

    async def test_institution_uses_actual_owned_receiver_integer_native_argument_and_producer_world(self):
        receipt = await self.api.set_institution(0, 3)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["observed_target_level"], 3)
        self.assertEqual(receipt["observed_level"], 1)
        self.assertIsNone(receipt["completed"])
        self.assertEqual(self.ctx.world, 0)
        self.assertEqual(self.ctx.actor, 0x80000002)
        expression, binding, native = self.f.producers[0]
        self.assertEqual(expression, "Institution.StartChangingLevelTo(3)")
        self.assertIsInstance(native, data.DataResult)
        self.assertIsInstance(native.value, data.NativeObject)
        self.assertEqual(native.type_id, 188)
        self.assertNotEqual(binding.type_id, self.ctx.types["Institution"])
        self.assertEqual(binding.object, native.value_handle)
        self.assertEqual(self.actions(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.SUBMIT])
        self.assertEqual(self.f.command_calls[0][1]["command"], 0xB000)
        self.assertTrue(all(options["world"] == 0x9001 for _, options in self.f.command_calls))
        self.assertEqual(self.ctx.country_reads, 1)
        self.assertEqual(self.ctx.performs, ["politics.set_institution"])
        self.clean()

    async def test_existing_institution_id_key_display_name_and_raw_name_selection(self):
        for selector in (0, "institution_education", "Education", "#bold Education#!"):
            with self.subTest(selector=selector):
                self.f.institution_target, self.f.institution_changing = 0, False
                receipt = await self.api.set_institution(selector, 3)
                self.assertTrue(receipt["accepted"])
                self.assertEqual(receipt["institution"]["id"], 0)
        self.assertEqual(len(self.f.producers), 4)
        self.clean()

    async def test_institution_noop_current_level_is_observation_not_native_acceptance(self):
        receipt = await self.api.set_institution(0, 1)
        self.assertIsNone(receipt["accepted"])
        self.assertTrue(receipt["target_satisfied_observed"])
        self.assertFalse(receipt["observed_changing"])
        self.assertEqual(receipt["status"], "unchanged")
        self.assertEqual(self.actions(), [])
        self.clean()

    async def test_institution_noop_changing_to_the_known_requested_target(self):
        self.f.institution_target, self.f.institution_changing = 3, True
        receipt = await self.api.set_institution("Education", 3)
        self.assertIsNone(receipt["accepted"])
        self.assertTrue(receipt["target_satisfied_observed"])
        self.assertTrue(receipt["observed_changing"])
        self.assertEqual(self.f.producers, [])
        self.clean()

    async def test_institution_same_target_without_changing_does_not_fake_noop(self):
        self.f.institution_target, self.f.institution_changing = 3, False
        receipt = await self.api.set_institution(0, 3)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(len(self.f.producers), 1)
        self.clean()

    async def test_institution_nonboolean_changing_and_boolean_target_do_not_fake_noop(self):
        self.f.institution_target, self.f.institution_changing = 3, 0
        self.f.validated = 0
        receipt = await self.api.set_institution(0, 3)
        self.assertFalse(receipt["accepted"])
        self.assertEqual(len(self.f.producers), 1)
        self.f.institution_target, self.f.institution_changing = False, True
        receipt = await self.api.set_institution(0, 0)
        self.assertFalse(receipt["accepted"])
        self.assertEqual(self.f.producers[-1][0], "Institution.StartChangingLevelTo(0)")
        self.clean()

    async def test_required_unknown_institution_target_and_changing_keep_reader_exceptions(self):
        self.f.institution_target = None
        with self.assertRaisesRegex(RuntimeError, "GetTargetInvestmentLevel is unavailable"):
            await self.api.set_institution(0, 0)
        self.f.institution_target, self.f.institution_changing = 3, None
        with self.assertRaisesRegex(RuntimeError, "IsChangingLevel is unavailable"):
            await self.api.set_institution(0, 3)
        self.assertEqual(self.f.producers, [])
        self.no_synthesis()

    async def test_missing_optional_institution_fields_are_not_an_extra_row_gate(self):
        self.f.institution_key, self.f.optional_fields = None, False
        receipt = await self.api.set_institution("Education", 3)
        self.assertTrue(receipt["accepted"])
        self.assertIsNone(receipt["institution"]["key"])
        self.assertIsNone(receipt["institution"]["max_level"])
        self.assertIsNone(receipt["institution"]["cost"])
        self.clean()

    async def test_movement_bolster_uses_zero_argument_original_toggle_on_selected_native_receiver(self):
        receipt = await self.api.bolster("Liberal Movement")
        self.assertTrue(receipt["accepted"])
        self.assertTrue(receipt["observed"])
        self.assertTrue(receipt["desired_state_observed"])
        expression, binding, native = self.f.producers[0]
        self.assertEqual(expression, "PoliticalMovement.ToggleBolstering")
        self.assertEqual(binding.type_id, 219)
        self.assertEqual(binding.object, native.value_handle)
        self.assertEqual(receipt["movement"]["id"], 0x86000004)
        self.assertEqual(self.actions(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.SUBMIT])
        self.clean()

    async def test_movement_suppression_release_preserves_observed_false_and_zero_id(self):
        self.f.movement_id, self.f.suppressed = 0, True
        receipt = await self.api.suppress(0, False)
        self.assertTrue(receipt["accepted"])
        self.assertIs(receipt["observed"], False)
        self.assertTrue(receipt["desired_state_observed"])
        self.assertEqual(self.f.producers[0][0], "PoliticalMovement.ToggleSuppression")
        self.clean()

    async def test_movement_known_noop_false_is_positive_goal_observation_not_acceptance(self):
        receipt = await self.api.bolster("#bold Liberal Movement#!", False)
        self.assertIsNone(receipt["accepted"])
        self.assertIs(receipt["observed"], False)
        self.assertTrue(receipt["desired_state_observed"])
        self.assertEqual(receipt["status"], "unchanged")
        self.assertFalse(self.f.producers)
        self.clean()

    async def test_movement_nonboolean_current_state_cannot_be_compared_as_false_or_toggled(self):
        self.f.bolstered = 0
        receipt = await self.api.bolster(self.f.movement_id, False)
        self.assertIsNone(receipt["accepted"])
        self.assertIsNone(receipt["desired_state_observed"])
        self.assertEqual(receipt["status"], "unavailable")
        self.assertEqual(receipt["observed"], 0)
        self.assertEqual(self.f.producers, [])
        self.clean()

    async def test_movement_required_none_retains_reader_exception_instead_of_toggling(self):
        self.f.bolstered = None
        with self.assertRaisesRegex(RuntimeError, "IsBolstered is unavailable"):
            await self.api.bolster(self.f.movement_id, False)
        self.assertEqual(self.f.producers, [])
        self.no_synthesis()

    async def test_native_validation_rejection_is_false_not_unavailable_or_synthetic_acceptance(self):
        self.f.validated = 0
        receipt = await self.api.set_institution(0, 3)
        self.assertIs(receipt["accepted"], False)
        self.assertFalse(receipt["validation"].validated)
        self.assertEqual(receipt["reason"], self.f.validation_reason)
        self.assertEqual(self.actions(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.DESTROY])
        self.assertIsNone(receipt["submission"])
        self.clean()

    async def test_native_reason_truncation_is_kept_without_second_validation(self):
        self.f.validation_error = 122
        receipt = await self.api.bolster(self.f.movement_id)
        self.assertEqual(receipt["validation"].error, 122)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(self.actions().count(commands.Action.VALIDATE), 1)
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 1)
        self.clean()

    async def test_both_completed_missing_original_producers_use_same_run_finisher(self):
        self.f.producer_empty = True
        first = await self.api.set_institution(0, 3)
        second = await self.api.bolster(self.f.movement_id)
        for receipt in (first, second):
            self.assertEqual(receipt["status"], "unavailable")
            self.assertIsNone(receipt["accepted"])
            self.assertIsInstance(receipt["source_unavailable"], run_mod.SourceUnavailable)
            self.assertEqual(receipt["native_result"].kind, data.Kind.EMPTY)
            self.assertTrue(receipt["native_results"])
        self.assertEqual(len(self.f.producers), 2)
        self.assertEqual(self.actions(), [])
        self.clean()

    async def test_completed_empty_carrier_keeps_raw_result_and_finishes_without_clone(self):
        self.f.carrier_null = True
        receipt = await self.api.suppress(self.f.movement_id)
        self.assertEqual(receipt["status"], "unavailable")
        self.assertIs(receipt["native_result"], self.f.last_carrier)
        self.assertEqual(receipt["native_result"].value.primary_pointer, 0)
        self.assertEqual(self.actions(), [])
        self.clean()

    async def test_native_producer_error_keeps_original_result_and_existing_known_error_finisher(self):
        self.f.producer_error = 122
        with self.assertRaises(run_mod.KnownResultError) as caught:
            await self.api.set_institution(0, 3)
        self.assertIs(caught.exception.result, self.f.last_producer)
        self.assertEqual(caught.exception.result.error, 122)
        self.assertIn(("Institution.StartChangingLevelTo(3)", self.f.last_producer), caught.exception.observed_result["native_results"])
        self.assertEqual(self.actions(), [])
        self.clean()

    async def test_original_runtime_error_is_not_caught_as_source_unavailable_or_replayed(self):
        error = RuntimeError("Original producer dispatch unknown")
        self.f.producer_exception = error
        with self.assertRaises(RuntimeError) as caught:
            await self.api.bolster(self.f.movement_id)
        self.assertIs(caught.exception, error)
        self.assertEqual(len(self.f.producers), 1)
        self.assertFalse(self.f.released)
        self.assertTrue(self.f.handles)
        self.no_synthesis()

    async def test_original_cancellation_is_not_caught_as_source_unavailable(self):
        self.f.producer_exception = asyncio.CancelledError("Original producer cancelled")
        with self.assertRaises(asyncio.CancelledError):
            await self.api.set_institution(0, 3)
        self.assertEqual(len(self.f.producers), 1)
        self.assertFalse(self.f.released)
        self.no_synthesis()

    async def test_accepted_unsettled_original_receipt_is_kept_and_readback_is_skipped(self):
        self.f.bad_ack = True
        receipt = await self.api.bolster(self.f.movement_id)
        self.assertIs(receipt["accepted"], True)
        self.assertIs(receipt["submission"], self.f.submit_query.result)
        self.assertEqual(receipt["native_outcome"], "unknown")
        self.assertIn("readback_skipped", receipt)
        self.assertIsNone(receipt["observed"])
        self.assertEqual(receipt["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(sum(event[0] == "PoliticalMovement.IsBolstered" for event in self.f.events), 1)
        self.assertEqual(self.actions(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.SUBMIT])
        self.assertFalse(self.f.released)
        self.no_synthesis()

    async def test_native_submission_error_preserves_raw_receipt_and_does_not_read_or_resubmit(self):
        self.f.submit_error, self.f.accepted = 87, 0
        receipt = await self.api.set_institution(0, 3)
        self.assertIsNone(receipt["accepted"])
        self.assertIs(receipt["native_result"], self.f.submit_query.result)
        self.assertEqual(receipt["native_error"], 87)
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 1)
        self.assertNotIn("observed_target_level", receipt)
        self.no_synthesis()

    async def test_native_error_with_positive_acceptance_keeps_both_without_readback(self):
        self.f.submit_error, self.f.accepted = 87, 1
        receipt = await self.api.set_institution(0, 3)
        self.assertIs(receipt["accepted"], True)
        self.assertEqual(receipt["native_error"], 87)
        self.assertIs(receipt["submission"], self.f.submit_query.result)
        self.assertEqual(receipt["native_outcome"], "unknown")
        self.assertIn("readback_skipped", receipt)
        self.assertNotIn("observed_target_level", receipt)
        self.assertEqual(receipt["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 1)
        self.assertFalse(self.f.released)
        self.no_synthesis()

    async def test_after_read_error_keeps_native_acceptance_and_original_error(self):
        error = RuntimeError("Original accepted movement observer failure")
        self.f.after_exception = error
        receipt = await self.api.bolster(self.f.movement_id)
        self.assertTrue(receipt["accepted"])
        self.assertIs(receipt["readback_error"], error)
        self.assertIs(receipt["submission"], self.f.submit_query.result)
        self.assertIsNone(receipt["observed"])
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 1)
        self.assertEqual(receipt["cleanup"]["status"], "retained_pending_or_unknown")
        self.no_synthesis()

    async def test_after_native_partial_error_keeps_acceptance_and_original_partial_result(self):
        self.f.after_error = 122
        receipt = await self.api.set_institution(0, 3)
        self.assertTrue(receipt["accepted"])
        self.assertIsInstance(receipt["readback_error"], run_mod.KnownResultError)
        native = receipt["readback_error"].result
        self.assertEqual(native.error, 122)
        self.assertEqual(native.value, 3)
        self.assertIn(("Institution.GetTargetInvestmentLevel", native), receipt["native_results"])
        self.clean()

    async def test_after_nonboolean_movement_value_is_preserved_without_claimed_goal(self):
        self.f.after_override["PoliticalMovement.IsBolstered"] = 1
        receipt = await self.api.bolster(self.f.movement_id)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["observed"], 1)
        self.assertIsNone(receipt["desired_state_observed"])
        self.assertEqual(receipt["status"], "accepted")
        self.clean()

    async def test_pending_original_submit_resumes_once_under_same_intent_lock(self):
        self.f.submit_gate = threading.Event()
        self.ctx.executor.timeout = 0
        pending = await self.api.bolster(self.f.movement_id)
        self.assertIsInstance(pending, PendingOperation)
        await asyncio.wait_for(self.ctx.submit_started.wait(), 1)
        original = self.f.submit_query
        try:
            self.assertIn(original, self.ctx.owner_pool.snapshot())
            self.assertTrue(self.api._mutation_lock.locked())
            self.assertEqual(len(self.f.producers), 1)
        finally:
            self.f.submit_gate.set()
        receipt = await pending.resume(timeout=1)
        self.assertTrue(receipt["accepted"])
        self.assertTrue(receipt["observed"])
        self.assertIs(self.f.submit_query, original)
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 1)
        self.assertEqual(len(self.f.producers), 1)
        self.assertFalse(self.api._mutation_lock.locked())
        self.clean()

    async def test_serialized_movement_setters_do_not_double_toggle(self):
        first, second = await asyncio.gather(self.api.bolster(self.f.movement_id), self.api.bolster(self.f.movement_id))
        self.assertTrue(first["accepted"])
        self.assertIsNone(second["accepted"])
        self.assertTrue(second["desired_state_observed"])
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 1)
        self.assertEqual(len(self.f.producers), 1)
        self.clean()

    async def test_cleanup_warning_does_not_erase_accepted_command_or_native_observation(self):
        self.f.release_error = 122
        receipt = await self.api.set_institution(0, 3)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["observed_target_level"], 3)
        self.assertEqual(receipt["cleanup"]["native_result"].error, 122)
        self.assertIs(receipt["submission"], self.f.submit_query.result)
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 1)
        self.no_synthesis()

    async def test_borrowed_source_header_is_not_promoted_to_an_owned_receiver(self):
        self.f.row_spelling = "borrowed"
        with self.assertRaisesRegex(RuntimeError, "ownership is not established"):
            await self.api.suppress(self.f.movement_id)
        self.assertEqual(self.f.producers, [])
        self.no_synthesis()

    async def test_input_validation_and_absent_target_preserve_original_selection_rules(self):
        for level in (True, -1, 1 << 31, 1.5):
            with self.assertRaises(ValueError):
                await self.api.set_institution(0, level)
        with self.assertRaises(ValueError):
            await self.api.bolster(self.f.movement_id, 0)
        first = await self.api.set_institution("Absent institution", 3)
        second = await self.api.suppress("Landowners")
        self.assertIs(first["accepted"], False)
        self.assertIn("do not target interest groups", second["reason"])
        self.assertEqual(self.f.producers, [])
        self.clean()


if __name__ == "__main__":
    unittest.main()
