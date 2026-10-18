"""CPU original-row diplomacy ownership and selected-command integration.

Uses real _Run, OwnedChoices, NativeCommand and SerializedQueryExecutor.
Only native transport functions return fixture callback handles. No game runs.
"""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3, v3_data as data, v3_commands as commands, v3_session
from pdx_runtime.query_executor import SerializedQueryExecutor
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.diplomacy import actions as a
from pdx_runtime.v3_game._choices import ChoicesBusyError
from tests.v3_game.test_v3_game_diplomatic_actions import make_page

TARGET = 0x80000002
ACTOR = 0
EXACT_RTTI = ".?AU?$SAnyType@V?$CPdxPtr@VCDataDiplomaticAction@@@@@PdxAnyImpl@@"


class Callback(v3.CallbackResult):
    """CPU callback transport; actual NativeCommand receipt checks remain live."""
    next_id = 1
    def __init__(self, result, *, adapter=None, address=0x400000):
        self.result = result
        self.adapter = adapter
        self.request_id = Callback.next_id
        Callback.next_id += 1
        self.result_address = address
        self.opaque_address = address
        self._released = False
        self._result = None
        self.cleanup_error = None
        self.submission_error = None
    def read(self):
        self._result = self.result
        self._released = True
        return self.result


class SessionCallback(v3_session.SessionCallbackResult):
    def __init__(self, result, **kwargs):
        Callback.__init__(self, result, **kwargs)
        self.session_request_id = self.request_id + 1000
        self.session_status = NS(completed_request_id=self.session_request_id)
    read = Callback.read


class Context:
    def __init__(self):
        self.native, self.process = object(), object()
        self.adapters = {"data": "original-data", "commands": "original-commands", "console": "console"}
        self._types = {"Country": 71, "DiplomaticAction": 177}
        self.type_reads, self.country_reads, self.performs, self.queries = [], 0, [], []
        self.world = 0  # Deliberately cold: producer world must be authoritative.
        self.executor = SerializedQueryExecutor(timeout=2)
        self.owner_pool = self.executor.owner_pool
        self.diagnostics = self.executor.diagnostics
    async def type_id(self, name):
        self.type_reads.append(name)
        return self._types[name]
    async def country_id(self):
        self.country_reads += 1
        raise AssertionError("the source target must not become or gate the native actor")
    async def query(self, label, factory, *, owner=None):
        self.queries.append(label)
        return await self.executor.query(label, factory, owner=owner)
    async def perform(self, label, operation):
        self.performs.append(label)
        return await self.executor.perform(label, operation, context=self)
    def _remember(self, query):
        self.executor._remember(query)


class Fixture:
    def __init__(self, ctx):
        self.ctx = ctx
        self.rows = [("owned", 11), ("owned", 22)]
        self.handles, self.identities, self.lists = {}, {}, {}
        self.next_handle = 100
        self.events, self.pages, self.released, self.command_calls = [], [], [], []
        self.source_error, self.count_error, self.copy_error = 0, 0, 0
        self.source_null, self.descriptor_failure, self.producer_empty = False, None, False
        self.element_error = 0
        self.row_type_id = ctx._types["DiplomaticAction"]
        self.carrier_null, self.producer_error, self.clone_error = False, 0, 0
        self.clone_pointer = 0xD000
        self.validated, self.validation_error = 1, 0
        self.accepted, self.submit_error = 1, 0
        self.submit_exception = None
        self.command_world = 0x9001
        self.last_selected = None
        self.source_adapter = "original-data"
        self.validation_reason = "native reason"
        self.warning = None

    def value(self, value, kind, action=data.Action.EVALUATE, *, adapter=None, error=0, handle=True):
        adapter = self.source_adapter if adapter is None else adapter
        held = self.next_handle if handle else 0
        if handle:
            self.next_handle += 1
            self.handles[held] = value
        result = NS(error=error, action=action, value_handle=held, value=value, kind=kind,
            type_id=self.ctx._types["DiplomaticAction"])
        return Callback(result, adapter=adapter)

    def evaluate(self, client, expression, bindings, **kwargs):
        self.events.append((expression, tuple(bindings), kwargs["adapter"]))
        if expression == a._GENERAL_SOURCE:
            assert len(bindings) == 1 and bindings[0].domain == "Country"
            assert bindings[0].object == TARGET
            if self.source_null:
                return self.value(None, data.Kind.EMPTY, error=self.source_error)
            q = self.value(data.NativeList(0, 0, b"", "original-general-source"), data.Kind.LIST,
                error=self.source_error)
            self.lists[q.result.value_handle] = list(self.rows)
            return q
        assert expression == "DiplomaticAction.ExecuteAction"
        assert len(bindings) == 1 and bindings[0].retained
        selected = bindings[0].object
        assert selected in self.handles
        self.last_selected = self.identities[selected]
        if self.producer_empty:
            return self.value(None, data.Kind.EMPTY, adapter=kwargs["adapter"], error=self.producer_error)
        held = self.next_handle
        carrier = data.NativeDataCommand(0xC000, 0xC100, 0xC018, held,
            "original-command-carrier", self.command_world)
        return self.value(carrier, data.Kind.COMMAND, adapter=kwargs["adapter"], error=self.producer_error)

    def list_count(self, client, handle, **kwargs):
        assert handle in self.handles
        self.events.append(("count", handle, kwargs["adapter"]))
        return Callback(NS(error=self.count_error, total=len(self.lists[handle])), adapter=kwargs["adapter"])

    def list_element(self, client, handle, index, **kwargs):
        self.events.append(("element", index, kwargs["adapter"]))
        mode, identity = self.lists[handle][index]
        held = self.next_handle
        spelling = {"owned": EXACT_RTTI,
            "raw": ".?AU?$SAnyType@PEAVCDataDiplomaticAction@@@PdxAnyImpl@@",
            "inline": ".?AU?$SAnyType@VCDataDiplomaticAction@@@PdxAnyImpl@@"}[mode]
        # Real decoded DataResult, including Kind.RAW0 with original bytes.
        # No host NativeObject or fabricated CPdx conversion is introduced.
        snapshot = (0x90000 + held * 0x100).to_bytes(8, "little") if identity is not None else b""
        header = list(data.REQUEST.unpack(bytes(data.REQUEST.size)))
        header[0] = data.REQUEST.size + len(snapshot)
        header[1], header[2] = self.element_error, data.Action.LIST_ELEMENT
        header[4], header[5] = self.row_type_id, index
        header[9], header[10] = len(snapshot), 17
        header[11] = data.Kind.RAW if identity is not None else data.Kind.EMPTY
        header[13], header[14] = 0, held
        header[15], header[16], header[17] = 0x1000, 0x2000, 0x3000
        header[18], header[19] = len(snapshot), len(snapshot)
        header[24], header[25] = data.REQUEST.size, len(snapshot)
        header[28] = (spelling if identity is not None else "").encode().ljust(192, b"\0")
        result = data.DataResult.from_bytes(data.REQUEST.pack(*header) + snapshot)
        self.next_handle += 1
        self.handles[held] = result.value
        self.identities[held] = identity
        return Callback(result, adapter=kwargs["adapter"])

    def project(self, client, fields, binding_rows, **kwargs):
        self.events.append(("project", binding_rows, kwargs["adapter"]))
        if self.descriptor_failure:
            raise self.descriptor_failure
        assert 1 <= len(fields) <= 32 and len(binding_rows) <= 64
        assert kwargs.get("source_expression") is None
        records = []
        for bindings in binding_rows:
            action, = [b for b in bindings if b.retained]
            assert action.type_id == self.row_type_id
            assert action.object in self.handles
            identity = self.identities[action.object]
            records.append({"native_key": "increase_relations", "name": "#bold Duplicate name#!",
                "first_country_id": ACTOR, "second_country_id": TARGET,
                "acceptance_value_no_obligation": identity})
        page = make_page(a.DIPLOMATIC_ACTION_FIELDS, records, error=self.copy_error)
        self.pages.append(page)
        query = Callback(page, adapter=kwargs["adapter"])
        query.cleanup_error = self.warning
        return query

    def command_value(self, client, handle, **kwargs):
        self.events.append(("carrier", handle, kwargs["adapter"]))
        assert handle in self.handles
        value = self.handles[handle]
        if self.carrier_null:
            value = data.NativeDataCommand(0, 0, 0, handle, "original-null-carrier", self.command_world)
        return Callback(NS(error=0, value=value, value_handle=0), adapter=kwargs["adapter"])

    def command_request(self, client, action, **kwargs):
        action = commands.Action(action)
        self.command_calls.append((action, dict(kwargs)))
        assert kwargs["adapter"] == "original-commands"
        assert kwargs["world"] == self.command_world
        self.events.append((action.name, kwargs.get("command"), kwargs["adapter"]))
        address = 0x500000 + len(self.command_calls) * 0x100000
        capacity = kwargs.get("capacity", 0)
        command, error, validated, accepted = kwargs["command"], 0, 0, 0
        payload, count = b"", 0
        if action == commands.Action.CLONE:
            error = self.clone_error
            payload = int(self.clone_pointer).to_bytes(8, "little")
        elif action == commands.Action.VALIDATE:
            error, validated = self.validation_error, self.validated
            payload = self.validation_reason.encode()
        elif action == commands.Action.SUBMIT:
            if self.submit_exception:
                raise self.submit_exception
            error, accepted, command = self.submit_error, self.accepted, 0
        elif action == commands.Action.DESTROY:
            command = 0
        else:
            raise AssertionError("No candidate create, setter or serialization is allowed")
        full_bytes = len(payload)
        if error == 122 and action == commands.Action.VALIDATE:
            full_bytes = capacity + 1
            payload = (payload + bytes(capacity))[:capacity]
        token = 42 if not error and command else 0
        raw = commands.RESULT.pack(commands.RESULT.size, error, action, token, command,
            address + commands.RESULT.size if capacity else 0, capacity,
            full_bytes, count, kwargs.get("submit_flags", 4), validated, accepted,
            17, 0, 0x1000, self.command_world, 0) + payload
        result = commands.CommandResult.from_bytes(raw)
        query_cls = SessionCallback if action == commands.Action.SUBMIT else Callback
        return query_cls(result, adapter=kwargs["adapter"], address=address)

    def release(self, client, handle, **kwargs):
        assert handle in self.handles
        self.events.append(("release", handle, kwargs["adapter"]))
        self.released.append(handle)
        del self.handles[handle]
        return Callback(NS(error=0, action=data.Action.RELEASE, value_handle=0), adapter=kwargs["adapter"])


class DiplomaticSelectedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = Context()
        self.api = DiplomacyAPI(self.ctx)
        self.fixture = Fixture(self.ctx)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("evaluate", "list_count", "list_element", "project_page", "command_value", "release"):
            handler = self.fixture.project if name == "project_page" else getattr(self.fixture, name)
            self.stack.enter_context(patch.object(data, name, side_effect=handler))
        self.stack.enter_context(patch.object(commands, "request", side_effect=self.fixture.command_request))
        self.create = self.stack.enter_context(patch.object(commands, "create"))

    async def catalog(self):
        return await self.api.diplomatic_action_choices(TARGET)

    def action_counts(self):
        return [action for action, _ in self.fixture.command_calls]

    async def test_catalog_keeps_original_general_rows_with_actual_parties_and_dynamic_type(self):
        catalog = await self.catalog()
        self.assertEqual(len(catalog), 2)
        self.assertTrue(catalog.metadata["result"]["complete"])
        self.assertEqual(catalog[0]["first_country_id"], ACTOR)
        self.assertEqual(catalog[0]["second_country_id"], TARGET)
        self.assertEqual(catalog[0]["name_raw"], "#bold Duplicate name#!")
        self.assertEqual(catalog[0]._binding.type_id, 177)
        self.assertIsInstance(catalog[0].native_result, data.DataResult)
        self.assertEqual(catalog[0].native_result.kind, data.Kind.RAW)
        self.assertEqual(catalog[0].native_result.native_type_name, EXACT_RTTI)
        self.assertIsInstance(catalog[0].native_result.value, bytes)
        self.assertEqual(catalog[0].native_result.object, 0, "owned RAW must not need a host pointer decoder")
        self.assertEqual(self.ctx.country_reads, 0)
        self.assertEqual(self.ctx.performs, ["diplomacy.diplomatic_action_choices"])
        self.assertEqual(self.fixture.command_calls, [])
        self.create.assert_not_called()
        self.assertIsInstance(catalog.owner, run_mod.Run)
        self.assertIn(catalog.owner, self.ctx.owner_pool.snapshot())
        await catalog.close()
        self.assertEqual(self.fixture.handles, {})
        self.assertEqual(self.ctx.owner_pool.snapshot(), ())

    async def test_catalog_rejects_borrowed_inline_and_null_without_retagging(self):
        self.fixture.rows = [("raw", 11), ("inline", 22), ("owned", None)]
        catalog = await self.catalog()
        self.assertEqual(len(catalog), 0)
        rejected = catalog.metadata["result"]["rejected_rows"]
        self.assertEqual(len(rejected), 3)
        self.assertFalse(catalog.metadata["result"]["complete"])
        self.assertIn("PEAVCDataDiplomaticAction", rejected[0]["native_result"].native_type_name)
        self.assertEqual(rejected[0]["native_result"].kind, data.Kind.RAW)
        self.assertIsInstance(rejected[0]["native_result"].value, bytes)
        self.assertEqual(rejected[2]["native_result"].kind, data.Kind.EMPTY)
        self.assertEqual(len(self.fixture.pages), 0)
        await catalog.close()

    async def test_catalog_binding_uses_actual_original_header_type_not_a_forced_hint(self):
        self.fixture.row_type_id = 199
        catalog = await self.catalog()
        self.assertEqual(self.ctx._types["DiplomaticAction"], 177)
        self.assertEqual(catalog[0].native_result.type_id, 199)
        self.assertEqual(catalog[0]._binding.type_id, 199)
        self.assertEqual(catalog[0]._binding.object, catalog[0].native_result.value_handle)
        self.assertTrue(catalog[0]._binding.retained)
        await catalog.close()

    async def test_catalog_missing_auxiliary_type_lookup_keeps_actual_header_actionable(self):
        original_type_id = self.ctx.type_id
        async def type_id(name):
            if name == "DiplomaticAction":
                raise LookupError("auxiliary type metadata unavailable")
            return await original_type_id(name)
        self.ctx.type_id = type_id
        self.fixture.row_type_id = 199
        catalog = await self.catalog()
        self.assertEqual(catalog[0].native_result.type_id, 199)
        self.assertEqual(catalog[0]._binding.type_id, 199)
        self.assertEqual(catalog[0].native_result.kind, data.Kind.RAW)
        self.assertEqual(catalog[0].native_result.native_type_name, EXACT_RTTI)
        result = await self.api.execute_diplomatic_action(catalog[0], readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.fixture.last_selected, 11)
        self.assertEqual(self.action_counts(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.SUBMIT])
        self.assertEqual(self.ctx.type_reads, [], "cached Country metadata needs no auxiliary query")
        await catalog.close()

    async def test_catalog65_owned_rows_use_two_fixed_binding_pages(self):
        self.fixture.rows = [("owned", i) for i in range(65)]
        catalog = await self.catalog()
        self.assertEqual(len(catalog), 65)
        self.assertEqual([p.row_count for p in self.fixture.pages], [64, 1])
        self.assertTrue(all(p.fields_count == 32 for p in self.fixture.pages))
        self.assertEqual(self.fixture.command_calls, [])
        await catalog.close()

    async def test_catalog_source_null_and_count_failure_preserve_native_evidence(self):
        self.fixture.source_null = True
        self.fixture.source_error = 87
        catalog = await self.catalog()
        self.assertFalse(catalog.metadata["result"]["complete"])
        self.assertEqual(len(catalog), 0)
        self.assertEqual(catalog.metadata["result"]["reasons"][0]["native_result"].error, 87)
        await catalog.close()
        self.fixture.source_null = False
        self.fixture.source_error = 0
        self.fixture.count_error = 87
        catalog = await self.catalog()
        self.assertEqual(catalog.metadata["result"]["sources"][0]["count_result"].error, 87)
        self.assertEqual(len(catalog), 0)
        await catalog.close()

    async def test_catalog_partial_descriptor_keeps_original_choices_and_pages(self):
        self.fixture.copy_error = 122
        catalog = await self.catalog()
        self.assertEqual(len(catalog), 2)
        self.assertFalse(catalog.metadata["result"]["complete"])
        self.assertEqual(catalog[0]["native_key"], "increase_relations")
        self.assertIs(catalog.metadata["result"]["native_pages"][0], self.fixture.pages[0])
        await catalog.close()

    async def test_catalog_element_error_preserves_original_owned_partial_result(self):
        self.fixture.element_error = 122
        catalog = await self.catalog()
        self.assertEqual(len(catalog), 2)
        self.assertFalse(catalog.metadata["result"]["complete"])
        self.assertEqual(catalog[0].native_result.error, 122)
        await catalog.close()

    async def test_selected_original_binding_no_key_or_index_relookup_and_one_native_validate_submit(self):
        catalog = await self.catalog()
        selected = catalog[1]
        self.fixture.rows = [("owned", 999)]  # Later source contents must not select the action.
        before_events = len(self.fixture.events)
        result = await self.api.execute_diplomatic_action(selected)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertEqual(self.fixture.last_selected, 22)
        self.assertEqual(self.action_counts(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.SUBMIT])
        later = self.fixture.events[before_events:]
        self.assertFalse(any(event[0] in (a._GENERAL_SOURCE, "count", "element") for event in later))
        execute = [e for e in later if e[0] == "DiplomaticAction.ExecuteAction"]
        self.assertEqual(execute[0][1], (selected._binding,))
        self.assertEqual(result["after"]["row"]["acceptance_value_no_obligation"], 22)
        self.assertEqual(catalog.active_leases, 0)
        self.assertEqual(self.ctx.country_reads, 0)
        self.assertEqual(self.ctx.performs[-1], "diplomacy.execute_diplomatic_action")
        self.assertTrue(all(action != commands.Action.SERIALIZE for action in self.action_counts()))
        await catalog.close()

    async def test_selected_readback_false_does_not_copy_or_claim_effect(self):
        catalog = await self.catalog()
        count = len(self.fixture.pages)
        result = await self.api.execute_diplomatic_action(catalog[0], readback=False)
        self.assertTrue(result["accepted"])
        self.assertNotIn("after", result)
        self.assertEqual(len(self.fixture.pages), count)
        self.assertIsNone(result["effect_observed"])
        await catalog.close()

    async def test_selected_native_rejection_validates_once_never_submits_and_cleans_clone(self):
        catalog = await self.catalog()
        self.fixture.validated = 0
        result = await self.api.execute_diplomatic_action(catalog[0])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["validation"].reason, "native reason")
        self.assertEqual(self.action_counts(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.DESTROY])
        self.assertNotIn("after", result)
        await catalog.close()

    async def test_selected_truncated_validation_reason_is_not_repeated(self):
        catalog = await self.catalog()
        self.fixture.validation_error = 122
        result = await self.api.execute_diplomatic_action(catalog[0], readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["validation"].error, 122)
        self.assertTrue(result["validation"].reason_truncated)
        self.assertEqual(self.action_counts().count(commands.Action.VALIDATE), 1)
        await catalog.close()

    async def test_selected_producer_error_preserves_native_result_without_clone(self):
        catalog = await self.catalog()
        self.fixture.producer_error = 87
        result = await self.api.execute_diplomatic_action(catalog[0])
        self.assertEqual(result["native_result"].error, 87)
        self.assertEqual(result["status"], "native_error")
        self.assertEqual(self.fixture.command_calls, [])
        self.assertIsNone(result["accepted"])
        await catalog.close()

    async def test_selected_null_original_producer_or_carrier_is_not_a_synthetic_command(self):
        catalog = await self.catalog()
        self.fixture.producer_empty = True
        result = await self.api.execute_diplomatic_action(catalog[0])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(self.fixture.command_calls, [])
        self.assertTrue(result["native_results"])
        self.fixture.producer_empty, self.fixture.carrier_null = False, True
        result = await self.api.execute_diplomatic_action(catalog[0])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(self.fixture.command_calls, [])
        await catalog.close()

    async def test_selected_error_clone_with_pointer_keeps_parent_catalog_busy(self):
        catalog = await self.catalog()
        self.fixture.clone_error = 8
        result = await self.api.execute_diplomatic_action(catalog[0])
        self.assertEqual(result["native_result"].error, 8)
        self.assertEqual(result["native_result"].cloned_command, self.fixture.clone_pointer)
        self.assertFalse(result["ownership_settled"])
        self.assertEqual(self.action_counts(), [commands.Action.CLONE])
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertFalse(catalog.closed)
        self.assertEqual(catalog.active_leases, 1)
        self.assertFalse(self.fixture.released)

    async def test_selected_accepted_partial_submit_keeps_raw_acceptance_and_owner_no_readback(self):
        catalog = await self.catalog()
        self.fixture.submit_error = 122
        result = await self.api.execute_diplomatic_action(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["submission"].error, 122)
        self.assertFalse(result["ownership_settled"])
        self.assertNotIn("after", result)
        self.assertIn("readback_skipped", result)
        self.assertEqual(self.action_counts().count(commands.Action.SUBMIT), 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        retained = [r for r in self.ctx.owner_pool.snapshot() if isinstance(r, run_mod.Run) and r is not catalog.owner]
        self.assertEqual(len(retained), 1)
        owner = retained[0].owners[0]
        self.assertEqual(owner.attempt.outcome, "unknown")
        self.assertIs(owner.pending, owner.attempt.query)
        self.assertIs(owner.attempt.result, result["submission"])

    async def test_selected_submit_exception_keeps_original_attempt_without_replay(self):
        catalog = await self.catalog()
        original = RuntimeError("native submit unknown")
        self.fixture.submit_exception = original
        with self.assertRaises(RuntimeError) as caught:
            await self.api.execute_diplomatic_action(catalog[0])
        self.assertIs(caught.exception, original)
        self.assertEqual(self.action_counts().count(commands.Action.SUBMIT), 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertFalse(self.fixture.released)

    async def test_selected_readback_exception_does_not_erase_native_acceptance(self):
        catalog = await self.catalog()
        error = OSError("original copied readback failed")
        self.fixture.descriptor_failure = error
        result = await self.api.execute_diplomatic_action(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertIs(result["readback_error"], error)
        self.assertFalse(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertEqual(self.action_counts().count(commands.Action.SUBMIT), 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_selected_adapters_follow_original_lease_not_later_context_data_role(self):
        catalog = await self.catalog()
        self.ctx.adapters["data"] = "later-unrelated-data"
        result = await self.api.execute_diplomatic_action(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertTrue(all(e[2] == "original-data" for e in self.fixture.events
            if e[0] in ("DiplomaticAction.ExecuteAction", "carrier", "project")))
        await catalog.close()
        self.assertTrue(all(e[2] == "original-data" for e in self.fixture.events if e[0] == "release"))

    async def test_selected_rejects_key_id_copied_row_wrong_context_and_closed_catalog(self):
        for value in ("increase_relations", 0, {"native_key": "increase_relations"}):
            with self.assertRaises(TypeError):
                await self.api.execute_diplomatic_action(value)
        catalog = await self.catalog()
        with self.assertRaises(ValueError):
            await DiplomacyAPI(Context()).execute_diplomatic_action(catalog[0])
        with self.assertRaises(TypeError):
            await self.api.execute_diplomatic_action(catalog[0], readback=1)
        selected = catalog[0]
        await catalog.close()
        with self.assertRaises(RuntimeError):
            await self.api.execute_diplomatic_action(selected)
        self.assertEqual(self.fixture.command_calls, [])

    async def test_catalog_bad_target_rejected_before_native_source(self):
        for target in (None, -1, 0xFFFFFFFF, True):
            with self.assertRaises(ValueError):
                await self.api.diplomatic_action_choices(target)
        self.assertEqual(self.fixture.events, [])
        self.assertEqual(self.fixture.command_calls, [])

    async def test_cleanup_warning_keeps_native_copied_catalog_values(self):
        self.fixture.warning = OSError("descriptor copied-buffer cleanup warning")
        catalog = await self.catalog()
        self.assertEqual(catalog[0]["native_key"], "increase_relations")
        self.assertIs(catalog.metadata["result"]["cleanup_warnings"][0], self.fixture.warning)
        await catalog.close()


if __name__ == "__main__":
    unittest.main()
