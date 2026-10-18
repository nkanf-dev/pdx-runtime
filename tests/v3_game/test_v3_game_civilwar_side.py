"""CPU original civil-war human route; no game/native VM or controller effect proof.

Real shared _Run/OwnedChoices/NativeCommand/executor; native transport alone is
fixture-backed. Retained DP rows use actual decoded native headers and RAW bytes.
"""
import asyncio
from contextlib import ExitStack
import struct
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.query_executor import PendingOperation, SerializedQueryExecutor
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.diplomacy import plays
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.v3_game._choices import OwnedChoice, ChoicesBusyError
from tests.v3_game.test_v3_game_diplomatic_selected import Callback, SessionCallback

FOREIGN = 0xF0000007
INITIATOR = 0x80000002
PLAY_ID = 0x8100000B
DP_RTTI = ".?AU?$SAnyType@V?$CPdxPtr@VCDataDiplomaticPlay@@@@@PdxAnyImpl@@"


class GateSessionCallback(SessionCallback):
    def read(self):
        if self.gate is not None and not self.gate.is_set():
            return None
        return super().read()


class Context:
    def __init__(self):
        self.native, self.process = object(), object()
        self.adapters = {"data": "civil-data", "commands": "civil-commands"}
        self._types = {"Country": 71, "DiplomaticPlay": 188}
        self.world = 0
        self.country_reads, self.performs, self.queries = 0, [], []
        self.executor = SerializedQueryExecutor(timeout=2)
        self.owner_pool, self.diagnostics = self.executor.owner_pool, self.executor.diagnostics
        self.loop = asyncio.get_running_loop()
        self.submit_started = asyncio.Event()
    async def type_id(self, name):
        return self._types[name]
    async def country_id(self):
        self.country_reads += 1
        return FOREIGN
    async def query(self, label, factory, *, owner=None):
        self.queries.append(label)
        return await self.executor.query(label, factory, owner=owner)
    async def perform(self, label, operation):
        self.performs.append(label)
        return await self.executor.perform(label, operation)
    def _remember(self, query):
        self.executor._remember(query)


class Fixture:
    def __init__(self, ctx):
        self.ctx = ctx
        self.handles, self.rows, self.next_handle = {}, {}, 100
        self.native_player, self.initiator, self.target = 0, INITIATOR, 0
        self.player_name = "#bold Native player#!"
        self.after_player, self.after_initiator = INITIATOR, INITIATOR
        self.enable_switch, self.apply = False, True
        self.events, self.pages, self.command_calls, self.released = [], [], [], []
        self.producers = 0
        self.captured_actor = self.captured_destination = self.captured_play = None
        self.generation_world = 0x9001
        self.validated, self.validation_error = 1, 0
        self.accepted, self.submit_error = 1, 0
        self.producer_error, self.clone_error, self.clone_pointer = 0, 0, 0xD000
        self.empty_producer, self.null_carrier, self.raw_row = False, False, False
        self.after_error, self.after_native_error, self.after_cells = None, 0, {}
        self.after_warning = None
        self.submit_gate, self.submit_exception = None, None
        self.reason = "#bold native selected rejection#!"

    def result(self, kind, action, *, snapshot=b"", name="", type_id=-1, error=0, held=True):
        handle = self.next_handle if held else 0
        if held:
            self.next_handle += 1
        header = list(data.REQUEST.unpack(bytes(data.REQUEST.size)))
        header[0] = data.REQUEST.size + len(snapshot)
        header[1], header[2], header[4] = error, action, type_id
        header[9], header[10], header[11] = len(snapshot), 17, kind
        header[13], header[14] = 0, handle
        header[15], header[16], header[17] = 0x1000, 0x2000, 0x3000
        header[18], header[19] = len(snapshot), len(snapshot)
        header[24], header[25] = data.REQUEST.size, len(snapshot)
        header[28] = name.encode().ljust(192, b"\0")
        native = data.DataResult.from_bytes(data.REQUEST.pack(*header) + snapshot)
        if held:
            self.handles[handle] = native
        return Callback(native, adapter="civil-data")

    def evaluate(self, client, expression, bindings, **kwargs):
        self.events.append((expression, tuple(bindings), kwargs["adapter"]))
        if expression == "Country.AccessActiveDiplomaticPlays":
            self.source_country = bindings[0].object
            return self.result(data.Kind.LIST, data.Action.EVALUATE, snapshot=bytes(8), name="original-play-source")
        assert expression == "DiplomaticPlay.SwitchSide(GetPlayer)", expression
        assert len(bindings) == 1 and bindings[0].retained
        assert bindings[0].object in self.handles
        assert self.handles[bindings[0].object].native_type_name == DP_RTTI
        self.producers += 1
        self.captured_actor, self.captured_destination = self.native_player, self.initiator
        self.captured_play = self.rows[bindings[0].object]
        if self.empty_producer:
            return self.result(data.Kind.EMPTY, data.Action.EVALUATE, error=self.producer_error)
        return self.result(data.Kind.COMMAND, data.Action.EVALUATE,
            snapshot=struct.pack("<3Q", 0xC000, 0xC100, 0xC018), name="CDataCommand", error=self.producer_error)

    def count(self, client, handle, **kwargs):
        assert handle in self.handles
        return Callback(NS(error=0, total=1), adapter="civil-data")

    def element(self, client, handle, index, **kwargs):
        assert handle in self.handles and index == 0
        spelling = ".?AU?$SAnyType@PEAVCDataDiplomaticPlay@@@PdxAnyImpl@@" if self.raw_row else DP_RTTI
        query = self.result(data.Kind.RAW, data.Action.LIST_ELEMENT,
            snapshot=(0x91000).to_bytes(8, "little"), name=spelling, type_id=188)
        self.rows[query.result.value_handle] = PLAY_ID
        return query

    def carrier(self, client, handle, **kwargs):
        assert handle in self.handles
        self.events.append(("carrier", handle, kwargs["adapter"]))
        values = (0, 0, 0, self.generation_world) if self.null_carrier else (0xC000, 0xC100, 0xC018, self.generation_world)
        return self.result(data.Kind.RAW, data.Action.COMMAND_VALUE,
            snapshot=struct.pack("<4Q", *values), held=False)

    def project(self, client, fields, binding_rows, **kwargs):
        is_after = tuple(fields) == tuple(f.expression for f in plays._CIVILWAR_CONTROL)
        self.events.append(("after" if is_after else "catalog", tuple(binding_rows), kwargs["adapter"]))
        if is_after and self.after_error:
            raise self.after_error
        if is_after:
            assert len(fields) == 6 and len(binding_rows) == 1
            assert len(binding_rows[0]) == 1 and binding_rows[0][0].retained
            assert all("Country.Self" not in field for field in fields)
            definitions = plays._CIVILWAR_CONTROL
        else:
            definitions = plays._PLAY
        cells = []
        actual = {"GetPlayer.GetID": self.native_player,
            "GetPlayer.GetNameNoFormatting": self.player_name,
            "DiplomaticPlay.GetInitiator.GetID": self.initiator,
            "DiplomaticPlay.GetTarget.GetID": self.target,
            "DiplomaticPlay.GetSwitchSideBreakdown": "#bold Native switch explanation#!",
            "DiplomaticPlay.GetDiplomaticPlayType.HasEnableSwitchSides": self.enable_switch}
        for row, bindings in enumerate(binding_rows):
            assert any(b.retained and b.object in self.handles for b in bindings)
            for index, field in enumerate(definitions):
                if is_after and field.name in self.after_cells:
                    cells.append(self.after_cells[field.name])
                    continue
                kind = field.kind if field.kind is not None else data.Kind.STRING if field.text else data.Kind.I32
                value = actual.get(field.expression)
                if field.expression not in actual:
                    value = "native descriptor" if kind == data.Kind.STRING else False if kind == data.Kind.BOOL else 0
                if value is None:
                    kind, bits, raw = data.Kind.EMPTY, 0, b""
                elif kind == data.Kind.STRING:
                    bits, raw = 0, value.encode()
                else:
                    bits, raw = int(value) & 0xFFFFFFFFFFFFFFFF, b""
                cells.append(data.ProjectionCell(row, index, 0, kind, 188, bits, raw, len(raw)))
        page = data.ProjectionPage(self.after_native_error if is_after else 0, self.generation_world,
            len(fields), 0, 17, len(binding_rows), True, len(binding_rows), tuple(cells))
        self.pages.append(page)
        query = Callback(page, adapter=kwargs["adapter"])
        query.cleanup_error = self.after_warning if is_after else None
        return query

    def command(self, client, action, **kwargs):
        action = commands.Action(action)
        assert kwargs["adapter"] == "civil-commands"
        assert kwargs["world"] == self.generation_world
        self.command_calls.append((action, kwargs))
        address = 0x500000 + len(self.command_calls) * 0x100000
        capacity, command = kwargs.get("capacity", 0), kwargs["command"]
        error = validated = accepted = 0
        payload = b""
        if action == commands.Action.CLONE:
            error, payload = self.clone_error, self.clone_pointer.to_bytes(8, "little")
        elif action == commands.Action.VALIDATE:
            error, validated, payload = self.validation_error, self.validated, self.reason.encode()
        elif action == commands.Action.SUBMIT:
            if self.submit_exception:
                raise self.submit_exception
            command, error, accepted = 0, self.submit_error, self.accepted
            if self.apply:
                self.native_player, self.initiator = self.after_player, self.after_initiator
        elif action == commands.Action.DESTROY:
            command = 0
        else:
            raise AssertionError("no factory, setters, serialization or generic Execute route")
        result = commands.CommandResult.from_bytes(commands.RESULT.pack(commands.RESULT.size,
            error, action, 0x438C if not error and command else 0, command,
            address + commands.RESULT.size if capacity else 0, capacity, len(payload),
            0, kwargs.get("submit_flags", 4), validated, accepted, 17, 0, 0x1000, self.generation_world, 0) + payload)
        if action == commands.Action.SUBMIT:
            query = GateSessionCallback(result, adapter="civil-commands", address=address)
            query.gate = self.submit_gate
            self.ctx.loop.call_soon_threadsafe(self.ctx.submit_started.set)
            return query
        return Callback(result, adapter="civil-commands", address=address)

    def release(self, client, handle, **kwargs):
        assert handle in self.handles
        self.released.append(handle)
        self.events.append(("release", handle, kwargs["adapter"]))
        del self.handles[handle]
        return self.result(data.Kind.EMPTY, data.Action.RELEASE, held=False)


class CivilWarSideTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ctx = Context()
        self.api = DiplomacyAPI(self.ctx)
        self.f = Fixture(self.ctx)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, method in (("evaluate", self.f.evaluate), ("list_count", self.f.count),
                ("list_element", self.f.element), ("command_value", self.f.carrier),
                ("project_page", self.f.project), ("release", self.f.release)):
            self.stack.enter_context(patch.object(data, name, side_effect=method))
        self.stack.enter_context(patch.object(commands, "request", side_effect=self.f.command))
        self.create = self.stack.enter_context(patch.object(commands, "create"))
        self.invoke = self.stack.enter_context(patch.object(data, "invoke_property_action"))
    async def catalog(self):
        return await self.api.play_choices(country_id=FOREIGN)
    def actions(self):
        return [action for action, _ in self.f.command_calls]

    async def test_original_getplayer_argument_not_foreign_context_actor_and_exact_selected_once(self):
        catalog = await self.catalog()
        selected = catalog[0]
        self.assertIsInstance(selected.native_result, data.DataResult)
        self.assertEqual(selected.native_result.kind, data.Kind.RAW)
        self.assertEqual(selected.native_result.native_type_name, DP_RTTI)
        self.assertIsInstance(selected.native_result.value, bytes)
        self.assertEqual(catalog.owner.value_adapters[selected.native_result.value_handle], "civil-data")
        result = await self.api.play_civil_war_initiator(selected)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["expression"], "DiplomaticPlay.SwitchSide(GetPlayer)")
        self.assertEqual((self.f.captured_actor, self.f.captured_destination, self.f.captured_play), (0, INITIATOR, PLAY_ID))
        self.assertEqual(self.f.source_country, FOREIGN)
        self.assertEqual(self.ctx.country_reads, 0)
        self.assertNotIn("actor_country_id", result)
        self.assertEqual(self.actions(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.SUBMIT])
        self.assertEqual(self.f.producers, 1)
        self.assertTrue(result["after_initiator_control_observed"])
        self.assertIsNone(result["completed"])
        expression = [event for event in self.f.events if event[0].startswith("DiplomaticPlay.SwitchSide")]
        self.assertEqual(expression[0][1], (selected._binding,))
        self.assertEqual(self.ctx.performs[-1], "diplomacy.play_civil_war_initiator")
        self.create.assert_not_called()
        self.invoke.assert_not_called()
        await catalog.close()

    async def test_after_player_equal_to_current_initiator_is_not_captured_effect_completion(self):
        catalog = await self.catalog()
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertEqual(result["after"]["controlled_country_id"], INITIATOR)
        self.assertEqual(result["after"]["initiator_id"], INITIATOR)
        self.assertTrue(result["after_initiator_control_observed"])
        self.assertIsNone(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertEqual(result["after"]["controlled_country_name_raw"], "#bold Native player#!")
        await catalog.close()

    async def test_after_initiator_drift_is_independent_observation_not_retargeted_destination(self):
        catalog = await self.catalog()
        self.f.after_initiator = 0xE0000009
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertEqual(self.f.captured_destination, INITIATOR)
        self.assertEqual(result["after"]["initiator_id"], 0xE0000009)
        self.assertFalse(result["after_initiator_control_observed"])
        self.assertEqual(result["after"]["controlled_country_id"], INITIATOR)
        self.assertIsNone(result["completed"])
        self.assertIsNone(result["effect_observed"])
        await catalog.close()

    async def test_native_zero_false_after_fields_are_preserved_without_enable_policy_gate(self):
        catalog = await self.catalog()
        self.f.apply = False
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["after"]["controlled_country_id"], 0)
        self.assertFalse(result["after"]["has_enable_switch_sides"])
        self.assertFalse(result["after_initiator_control_observed"])
        self.assertIsNone(result["completed"])
        self.assertEqual(self.f.producers, 1)
        self.assertEqual(self.actions().count(commands.Action.VALIDATE), 1)
        await catalog.close()

    async def test_readback_false_produces_no_after_copy_or_actor_probe(self):
        catalog = await self.catalog()
        result = await self.api.play_civil_war_initiator(catalog[0], readback=False)
        self.assertTrue(result["accepted"])
        self.assertNotIn("after", result)
        self.assertIsNone(result["completed"])
        self.assertIsNone(result["after_initiator_control_observed"])
        self.assertFalse(any(e[0] == "after" for e in self.f.events))
        self.assertEqual(self.ctx.country_reads, 0)
        await catalog.close()

    async def test_native_validation_false_preserves_reason_no_submit(self):
        catalog = await self.catalog()
        self.f.validated = 0
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "#bold native selected rejection#!")
        self.assertEqual(self.actions(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.DESTROY])
        self.assertNotIn("after", result)
        self.assertIsNone(result["completed"])
        await catalog.close()

    async def test_native_validation_error_preserves_result_and_no_submit(self):
        catalog = await self.catalog()
        self.f.validation_error, self.f.validated = 87, 0
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertEqual(result["validation"].error, 87)
        self.assertIsNone(result["accepted"])
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 0)
        await catalog.close()

    async def test_native_producer_error_has_no_clone_or_context_country_fallback(self):
        catalog = await self.catalog()
        self.f.producer_error = 87
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertEqual(result["native_result"].error, 87)
        self.assertEqual(result["status"], "native_error")
        self.assertEqual(self.actions(), [])
        self.assertEqual(self.f.producers, 1)
        self.assertEqual(self.ctx.country_reads, 0)
        await catalog.close()

    async def test_null_command_or_carrier_remains_unavailable_not_default_factory(self):
        catalog = await self.catalog()
        self.f.empty_producer = True
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertEqual(result["status"], "unavailable")
        self.f.empty_producer, self.f.null_carrier = False, True
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(self.actions(), [])
        self.create.assert_not_called()
        await catalog.close()

    async def test_clone_error_nonzero_pointer_retains_original_catalog_graph(self):
        catalog = await self.catalog()
        self.f.clone_error = 8
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertEqual(result["native_result"].cloned_command, self.f.clone_pointer)
        self.assertFalse(result["ownership_settled"])
        self.assertEqual(self.actions(), [commands.Action.CLONE])
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertFalse(self.f.released)

    async def test_native_submit_rejection_is_not_builtin_dispatch_acceptance(self):
        catalog = await self.catalog()
        self.f.accepted = 0
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["submission"].accepted, 0)
        self.assertNotIn("after", result)
        self.assertIsNone(result["completed"])
        self.invoke.assert_not_called()
        await catalog.close()

    async def test_accepted_with_native_error_keeps_original_unknown_query_and_no_after(self):
        catalog = await self.catalog()
        self.f.submit_error = 122
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["submission"].error, 122)
        self.assertFalse(result["ownership_settled"])
        self.assertIsNone(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertNotIn("after", result)
        self.assertIsNone(result["after_initiator_control_observed"])
        runs = [owner for owner in self.ctx.owner_pool.snapshot()
            if isinstance(owner, run_mod.Run) and owner is not catalog.owner]
        owner = runs[0].owners[0]
        self.assertIs(owner.pending, owner.attempt.query)
        self.assertIs(owner.attempt.result, result["submission"])
        self.assertEqual(owner.attempt.outcome, "unknown")
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_unknown_submit_exception_is_not_replayed_or_cleaned(self):
        catalog = await self.catalog()
        error = RuntimeError("native submit transport unknown")
        self.f.submit_exception = error
        with self.assertRaises(RuntimeError) as caught:
            await self.api.play_civil_war_initiator(catalog[0])
        self.assertIs(caught.exception, error)
        self.assertEqual(self.f.producers, 1)
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertFalse(self.f.released)

    async def test_pending_original_submit_keeps_same_handle_and_resumes_once(self):
        catalog = await self.catalog()
        self.f.submit_gate = threading.Event()
        self.ctx.executor.timeout = 0
        pending = await self.api.play_civil_war_initiator(catalog[0])
        self.assertIsInstance(pending, PendingOperation)
        await asyncio.wait_for(self.ctx.submit_started.wait(), 1)
        try:
            with self.assertRaises(ChoicesBusyError):
                await catalog.close()
            runs = [owner for owner in self.ctx.owner_pool.snapshot()
                if isinstance(owner, run_mod.Run) and owner is not catalog.owner]
            native_owner = runs[0].owners[0]
            original_query = native_owner.pending
            self.assertIsNone(runs[0].public_result["completed"])
            self.assertIs(original_query, native_owner.attempt.query)
        finally:
            self.f.submit_gate.set()
        result = await pending.resume(timeout=1)
        self.assertTrue(result["accepted"])
        self.assertIs(native_owner.attempt.query, original_query)
        self.assertEqual(self.f.producers, 1)
        self.assertEqual(self.actions(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.SUBMIT])
        self.assertIsNone(result["completed"])
        await catalog.close()

    async def test_accepted_after_copy_failure_keeps_native_result_and_original_borrower(self):
        catalog = await self.catalog()
        error = OSError("original after copy unknown")
        self.f.after_error = error
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertIs(result["readback_error"], error)
        self.assertIsNone(result["after_initiator_control_observed"])
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 1)
        self.assertIsNone(result["completed"])
        self.assertIsNone(result["effect_observed"])
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_after_native_partial_null_and_raw_string_are_not_dropped(self):
        catalog = await self.catalog()
        self.f.after_player = None
        raw = b"#bold kept explanation#!"
        self.f.after_cells["switch_side_breakdown"] = data.ProjectionCell(0, 4, 122,
            data.Kind.STRING, 188, 0, raw, 100)
        self.f.after_native_error = 122
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertTrue(result["accepted"])
        after = result["after"]
        self.assertIsNone(after["controlled_country_id"])
        self.assertIsNone(result["after_initiator_control_observed"])
        self.assertEqual(after["switch_side_breakdown_raw"], raw.decode())
        self.assertEqual(after["switch_side_breakdown"], "kept explanation")
        self.assertFalse(after["complete"])
        self.assertIn("controlled_country_id", after["reasons"])
        self.assertIn("switch_side_breakdown", after["reasons"])
        self.assertEqual(after["native_pages"][0].error, 122)
        self.assertIsNone(result["completed"])
        await catalog.close()

    async def test_cleanup_warning_does_not_replace_native_acceptance_or_after_values(self):
        catalog = await self.catalog()
        warning = OSError("copied after cleanup warning")
        self.f.after_warning = warning
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["after"]["controlled_country_id"], INITIATOR)
        self.assertIs(result["after"]["cleanup_warnings"][0], warning)
        self.assertTrue(result["after_initiator_control_observed"])
        self.assertIsNone(result["completed"])
        await catalog.close()

    async def test_invalid_unadmitted_closed_and_foreign_choices_make_zero_producers(self):
        for value in (0, "civil_war", {"name": "copied play"}):
            with self.assertRaises(TypeError):
                await self.api.play_civil_war_initiator(value)
        catalog = await self.catalog()
        selected = catalog[0]
        fake = OwnedChoice(catalog, selected.native_result, "DiplomaticPlay", selected._binding, dict(selected))
        with self.assertRaises(ValueError):
            await self.api.play_civil_war_initiator(fake)
        with self.assertRaises(ValueError):
            await DiplomacyAPI(Context()).play_civil_war_initiator(selected)
        with self.assertRaises(TypeError):
            await self.api.play_civil_war_initiator(selected, readback=1)
        await catalog.close()
        with self.assertRaises(RuntimeError):
            await self.api.play_civil_war_initiator(selected)
        self.assertEqual(self.f.producers, 0)
        self.assertEqual(self.actions(), [])

    async def test_borrowed_native_row_is_not_retagged_as_original_owning_play(self):
        self.f.raw_row = True
        catalog = await self.catalog()
        self.assertEqual(len(catalog), 0)
        self.assertEqual(len(catalog.metadata["result"]["rejected_rows"]), 1)
        self.assertEqual(self.f.producers, 0)
        await catalog.close()

    async def test_original_data_adapter_is_kept_after_context_role_change(self):
        catalog = await self.catalog()
        self.ctx.adapters["data"] = "later-unrelated-data"
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertTrue(all(event[2] == "civil-data" for event in self.f.events))
        await catalog.close()
        self.assertTrue(all(event[2] == "civil-data" for event in self.f.events))

    async def test_rev2_clean_current_ids_observe_true_despite_optional_and_global_copy_errors(self):
        catalog = await self.catalog()
        text = b"#bold retained optional reason#!"
        self.f.after_cells["switch_side_breakdown"] = data.ProjectionCell(0, 4, 122,
            data.Kind.STRING, 188, 0, text, 100)
        self.f.after_native_error = 122
        result = await self.api.play_civil_war_initiator(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertTrue(result["after_initiator_control_observed"])
        self.assertFalse(result["after"]["complete"])
        self.assertIn("switch_side_breakdown", result["after"]["reasons"])
        self.assertEqual(result["after"]["native_pages"][0].error, 122)
        self.assertEqual(result["after"]["switch_side_breakdown_raw"], text.decode())
        self.assertIsNone(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertEqual(self.f.producers, 1)
        await catalog.close()

    async def test_rev2_identity_getter_reason_makes_role_unknown_without_dropping_raw_id(self):
        catalog = await self.catalog()
        for name, index in (("controlled_country_id", 0), ("initiator_id", 2)):
            with self.subTest(field=name):
                self.f.after_cells = {name: data.ProjectionCell(0, index, 87,
                    data.Kind.I32, 188, INITIATOR, b"", 0)}
                result = await self.api.play_civil_war_initiator(catalog[0])
                self.assertTrue(result["accepted"])
                self.assertEqual(result["after"][name], INITIATOR)
                self.assertIn(name, result["after"]["reasons"])
                self.assertIsNone(result["after_initiator_control_observed"])
                self.assertIsNone(result["completed"])
                self.assertIsNone(result["effect_observed"])
        self.assertEqual(self.f.producers, 2)
        self.assertEqual(self.actions().count(commands.Action.VALIDATE), 2)
        self.assertEqual(self.actions().count(commands.Action.SUBMIT), 2)
        await catalog.close()

    async def test_rev2_pure_options_current_role_is_true_false_or_unknown_without_command(self):
        catalog = await self.catalog()
        self.f.events.clear()
        for controlled, expected in ((INITIATOR, True), (0, False), (None, None)):
            with self.subTest(controlled=controlled):
                self.f.native_player = controlled
                result = await self.api.civil_war_side_options(catalog[0])
                self.assertIs(result["initiator_control_observed"], expected)
                self.assertEqual(result["controlled_country_id"], controlled)
                self.assertFalse(result["has_enable_switch_sides"])
                self.assertIn("current copied", result["observation_scope"])
        self.assertEqual(self.f.producers, 0)
        self.assertEqual(self.actions(), [])
        self.assertEqual(self.ctx.country_reads, 0)
        self.assertTrue(all(event[0] == "after" for event in self.f.events))
        self.create.assert_not_called()
        self.invoke.assert_not_called()
        await catalog.close()

    async def test_rev2_options_clean_ids_ignore_unrelated_errors_but_identity_error_is_unknown(self):
        catalog = await self.catalog()
        self.f.native_player = INITIATOR
        self.f.after_native_error = 87
        text = b"native optional partial"
        self.f.after_cells["switch_side_breakdown"] = data.ProjectionCell(0, 4, 122,
            data.Kind.STRING, 188, 0, text, 100)
        result = await self.api.civil_war_side_options(catalog[0])
        self.assertTrue(result["initiator_control_observed"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["native_pages"][0].error, 87)
        self.assertEqual(result["switch_side_breakdown_raw"], text.decode())
        self.f.after_cells["initiator_id"] = data.ProjectionCell(0, 2, 87,
            data.Kind.I32, 188, INITIATOR, b"", 0)
        result = await self.api.civil_war_side_options(catalog[0])
        self.assertIsNone(result["initiator_control_observed"])
        self.assertEqual(result["initiator_id"], INITIATOR)
        self.assertIn("initiator_id", result["reasons"])
        self.assertEqual(self.f.producers, 0)
        self.assertEqual(self.actions(), [])
        await catalog.close()

    async def test_rev2_options_not_an_execution_gate_or_default_extra_preread(self):
        catalog = await self.catalog()
        options = await self.api.civil_war_side_options(catalog[0])
        self.assertFalse(options["initiator_control_observed"])
        self.assertFalse(options["has_enable_switch_sides"])
        copies = len(self.f.pages)
        self.f.events.clear()
        result = await self.api.play_civil_war_initiator(catalog[0], readback=False)
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["completed"])
        self.assertIsNone(result["after_initiator_control_observed"])
        self.assertEqual(len(self.f.pages), copies)
        self.assertFalse(any(event[0] == "after" for event in self.f.events))
        self.assertEqual(self.f.producers, 1)
        self.assertEqual(self.actions(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.SUBMIT])
        await catalog.close()

    async def test_rev2_options_reject_invalid_unadmitted_closed_and_foreign_without_native_copy(self):
        for value in (0, "civil_war", {"name": "copied"}):
            with self.assertRaises(TypeError):
                await self.api.civil_war_side_options(value)
        catalog = await self.catalog()
        selected = catalog[0]
        copies = len(self.f.pages)
        fake = OwnedChoice(catalog, selected.native_result, "DiplomaticPlay", selected._binding, dict(selected))
        with self.assertRaises(ValueError):
            await self.api.civil_war_side_options(fake)
        with self.assertRaises(ValueError):
            await DiplomacyAPI(Context()).civil_war_side_options(selected)
        await catalog.close()
        with self.assertRaises(RuntimeError):
            await self.api.civil_war_side_options(selected)
        self.assertEqual(len(self.f.pages), copies)
        self.assertEqual(self.f.producers, 0)
        self.assertEqual(self.actions(), [])


if __name__ == "__main__":
    unittest.main()
