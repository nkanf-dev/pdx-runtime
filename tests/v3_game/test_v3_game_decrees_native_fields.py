"""CPU-only decree copied fields and selected original command ownership.

These fixtures do not load the game or a DLL. They exercise the actual domain
composition, canonical copied-page projection, retained owner, and executor.
Native field layout/callback and gameplay acceptance are separate evidence.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game._core import PendingOperation
from tests.v3_game.test_v3_game_politics import Command, Context, Fixture, Query


NATIVE_EXPORT = "PdxV3DataProjectNativeFieldsInterface"
POTENTIAL = "State.GetPotentialDecrees"
ACTIVE = "State.AccessDecrees"


SET_LITERAL_CASES = (
    ('decree_new', "GetDecreeType('decree_new').GetSetCommand(State.Self)"),
    ('decree_social_mobility', "GetDecreeType('decree_social_mobility').GetSetCommand(State.Self)"),
    ('decree_road_maintenance', "GetDecreeType('decree_road_maintenance').GetSetCommand(State.Self)"),
    ("decree_owner's_rule", "GetDecreeType('decree_owner\\'s_rule').GetSetCommand(State.Self)"),
    ('decree_path\\folder', "GetDecreeType('decree_path\\\\folder').GetSetCommand(State.Self)"),
    ('decree_"double"', 'GetDecreeType(\'decree_"double"\').GetSetCommand(State.Self)'),
    ('decree_raw\r\nline', "GetDecreeType('decree_raw\r\nline').GetSetCommand(State.Self)"),
    ('decree_教育_é', "GetDecreeType('decree_教育_é').GetSetCommand(State.Self)"),
    (' decree_new ', "GetDecreeType(' decree_new ').GetSetCommand(State.Self)"),
    (' \t ', "GetDecreeType(' \t ').GetSetCommand(State.Self)"),
    ('()decree_new', "GetDecreeType('()decree_new').GetSetCommand(State.Self)"),
    ('(unfinished', "GetDecreeType('(unfinished').GetSetCommand(State.Self)"),
    (' (Country)decree_new', "GetDecreeType(' (Country)decree_new').GetSetCommand(State.Self)"),
    ('decree_café', "GetDecreeType('decree_café').GetSetCommand(State.Self)"),
    ('decree_café', "GetDecreeType('decree_café').GetSetCommand(State.Self)"),
    ('', "GetDecreeType('').GetSetCommand(State.Self)"),
    ('not_installed_native_key', "GetDecreeType('not_installed_native_key').GetSetCommand(State.Self)"),
)

class DecreeCommand(Command):
    def serialize(self):
        self.f.serializations += 1
        raise AssertionError("Copied decree discovery and selected commands must not serialize")

    def validate(self, capacity):
        self.f.validations += 1
        query = super().validate(capacity)
        query.result.error = self.f.validation_error
        query.result.reason = self.f.validation_reason or query.result.reason
        return query

    def submit(self, flags):
        self.f.submit_calls += 1
        query = super().submit(flags)
        query.result.error = self.f.submit_error
        query.result.accepted = self.f.submit_accepted
        self.f.submission_query = query
        return query

    def complete(self):
        command, operation = self.command, self.operation
        value = super().complete()
        if value is not None and operation == "validate" and self.f.unknown_validation:
            self.attempt = NS(outcome="unknown")
        if value is not None and operation == "submit" and self.f.keep_submit_owner:
            self.command = command
            self.attempt = NS(outcome="pending")
        return value


class DecreeFixture(Fixture):
    def __init__(self):
        super().__init__()
        self.potential_rows = [dict(key="decree_road_maintenance", name="Road Maintenance"),
                               dict(key="decree_social_mobility", name="Social Mobility")]
        self.active_rows = [dict(id=0x08000007, key="decree_road_maintenance")]
        self.fresh_rows = None
        self.cell_overrides, self.page_overrides = {}, {}
        self.page_calls, self.export_calls, self.release_adapters = [], [], []
        self.remove_bindings = []
        self.native_fields_available = True
        self.raw_owned_decree = self.bad_decree_header = False
        self.release_error = self.validation_error = self.submit_error = 0
        self.validation_reason = ""
        self.submit_accepted = True
        self.unknown_validation = self.keep_submit_owner = False
        self.serializations = self.validations = self.submit_calls = 0
        self.clone_calls = 0
        self.expected_set_expressions = {expression: key for key, expression in SET_LITERAL_CASES}
        self.native_set_definition_keys = {key: key for key, _ in SET_LITERAL_CASES}
        self.set_requests = []
        self.set_producer_error = self.clone_error = 0
        self.set_producer_none = False
        original_resolve = self.memory.resolve_export
        def resolve(module, name, **kwargs):
            self.export_calls.append((module, name))
            if name == NATIVE_EXPORT and not self.native_fields_available:
                raise BridgeError("resolve_export", 127, reason="export not found")
            return original_resolve(module, name, **kwargs)
        self.memory.resolve_export = resolve

    def evaluate(self, native, expression, bindings, **kwargs):
        if expression in self.expected_set_expressions:
            self.events.append(expression)
            assert len(bindings) == 1 and bindings[0].domain == "State"
            assert not bindings[0].retained and not bindings[0].country_id
            self.set_requests.append((expression, tuple(bindings), dict(kwargs)))
            key = self.expected_set_expressions[expression]
            query = (Query(self.result(None)) if self.set_producer_none else self.producer(22673,
                {439: commands.UInt32(bindings[0].object), 22671: self.native_set_definition_keys[key]}))
            query.result.error = self.set_producer_error
            return query
        if expression == ACTIVE:
            self.events.append(expression)
            rows = self.active_rows if self.fresh_rows is None else self.fresh_rows
            return self.list_result([("Decree", row["id"]) for row in rows])
        if expression == "Decree.GetRemoveCommand":
            self.remove_bindings.append(bindings[0])
        if expression in ("DecreeType.GetSetCommand(State.Self)", "Decree.GetType"):
            raise AssertionError("Unselected candidate command or GetType fallback")
        return super().evaluate(native, expression, bindings, **kwargs)

    def wrapper(self, name, origin, **kwargs):
        result = super().wrapper(name, origin, **kwargs)
        if name == "Decree" and self.raw_owned_decree:
            result.kind, result.value = data.Kind.RAW, b"copied undecoded payload"
        if name == "Decree" and self.bad_decree_header:
            result.native_type_name, result.type_id = None, -1
        return result

    def created(self, token, params, command=0xD0000):
        owner = DecreeCommand(self, token, params, command)
        self.owners.append(owner)
        return Query(NS(error=0, owner=owner, action=commands.Action.DESERIALIZE))

    def request(self, *args, **kwargs):
        self.clone_calls += 1
        query = super().request(*args, **kwargs)
        query.result.error = self.clone_error
        return query

    def release(self, native, handle, **kwargs):
        self.release_adapters.append(kwargs["adapter"])
        if self.release_error:
            return Query(NS(error=self.release_error, value_handle=handle, action=data.Action.RELEASE))
        return super().release(native, handle, **kwargs)

    def project_page(self, native, fields, binding_rows, **kwargs):
        fields, binding_rows = tuple(fields), tuple(tuple(row) for row in binding_rows)
        source, start, limit = kwargs["source_expression"], kwargs["start"], kwargs["limit"]
        assert source in (POTENTIAL, ACTIVE)
        assert limit == 64 and len(binding_rows) == 1 and len(binding_rows[0]) == 1
        assert binding_rows[0][0].domain == "State" and not binding_rows[0][0].retained
        assert kwargs["adapter"] == self.ctx.adapters["data"]
        self.page_calls.append((source, fields, binding_rows, dict(kwargs)))
        rows = self.potential_rows if source == POTENTIAL else self.active_rows
        selected, cells = rows[start:start+limit], []
        for offset, row in enumerate(selected):
            for column, field in enumerate(fields):
                if isinstance(field, data.NativeField):
                    if field.kind == data.NativeFieldKind.DECREE_ID:
                        assert source == ACTIVE and field.receiver_expression == ""
                        value, kind = row["id"], data.Kind.U32
                    else:
                        assert field.kind == data.NativeFieldKind.DECREE_TYPE_KEY
                        assert field.receiver_expression == ("Decree.GetType" if source == ACTIVE else "")
                        value, kind = row["key"], data.Kind.STRING
                else:
                    assert source == POTENTIAL and field == "DecreeType.GetName"
                    value, kind = row["name"], data.Kind.STRING
                snapshot = value.encode("utf-8") if kind == data.Kind.STRING else b""
                cell = data.ProjectionCell(offset, column, 0, kind,
                    -1 if isinstance(field, data.NativeField) else 12,
                    value if kind == data.Kind.U32 else 0, snapshot, len(snapshot))
                override = self.cell_overrides.get((source, start+offset, column))
                cells.append(replace(override, row=offset, field=column) if override is not None else cell)
        page = data.ProjectionPage(0, self.world, len(fields), start, 0x1000,
                                  len(rows), True, len(selected), tuple(cells))
        page = replace(page, **self.page_overrides.get((source, start), {}))
        query = Query(page)
        query.cleanup_error = None
        return query


class DecreeContext(Context):
    def __init__(self, fixture):
        super().__init__(fixture)
        self.perform_labels, self.query_owners = [], []
        self.selected_actor = fixture.actor

    async def country_id(self):
        return self.selected_actor

    async def perform(self, label, operation):
        self.perform_labels.append(label)
        return await super().perform(label, operation)

    async def query(self, label, factory, *, owner=None):
        self.query_owners.append((label, owner))
        return await super().query(label, factory, owner=owner)


class DecreeNativeFieldTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = DecreeFixture()
        self.ctx = DecreeContext(self.fixture)
        self.api = politics.PoliticsAPI(self.ctx)
        self.patchers = self.fixture.patches()
        for patcher in self.patchers:
            patcher.start()

    async def asyncTearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()

    def no_commands(self):
        self.assertEqual((self.fixture.clone_calls, self.fixture.serializations,
                          self.fixture.validations, self.fixture.submit_calls), (0, 0, 0, 0))
        self.assertFalse(self.fixture.owners)
        self.assertFalse(self.fixture.remove_bindings)
        self.assertFalse(self.fixture.set_requests)

    def cleaned(self):
        self.assertFalse(self.fixture.handles)
        self.assertFalse(self.fixture.memory.blocks)
        self.assertFalse(self.api._retained)
        self.assertTrue(all(not owner.command for owner in self.fixture.owners))

    def observation(self, label="decrees.observation"):
        runs = [item for item in self.ctx.diagnostics["operations"] if isinstance(item, run_mod.Run)]
        return next(value for name, value in reversed(runs[-1].operations) if name == label)

    async def test_options_copy_native_key_name_without_candidate_commands(self):
        rows = await self.api.decree_options(8)
        self.assertEqual([row["key"] for row in rows], ["decree_road_maintenance", "decree_social_mobility"])
        self.assertEqual(rows[0]["name"], "Road Maintenance")
        self.assertIsNone(rows[0]["eligible"])
        self.assertFalse(rows[0]["eligibility_checked"])
        self.assertIn("not been validated", rows[0]["reason"])
        self.assertEqual(self.ctx.perform_labels, ["politics.decree_options"])
        field = self.fixture.page_calls[0][1][0]
        self.assertEqual(field, data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY))
        self.no_commands()
        self.cleaned()

    async def test_active_id_and_type_key_are_copied_from_same_original_page_row(self):
        self.fixture.potential_rows = []
        self.fixture.active_rows = [dict(id=0, key="not_in_potential"),
                                    dict(id=0xF8000007, key="decree_second"),
                                    dict(id=0xffffffff, key="native_sentinel")]
        rows = await self.api.decrees(8)
        self.assertEqual([(row["id"], row["key"]) for row in rows],
                         [(0, "not_in_potential"), (0xF8000007, "decree_second"), (0xffffffff, "native_sentinel")])
        self.assertIn("invalid-reference sentinel", rows[2]["reasons"]["id"])
        source, fields, _, _ = self.fixture.page_calls[0]
        self.assertEqual(source, ACTIVE)
        self.assertEqual(fields, (data.NativeField(data.NativeFieldKind.DECREE_ID),
            data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY, "Decree.GetType")))
        self.assertFalse(self.fixture.events)
        self.no_commands()
        self.cleaned()

    async def test_key_bytes_and_rich_name_are_not_used_as_host_identity(self):
        raw = "\x15B Road Maintenance\x15!"
        key = "decree_原样'\\\x00tail"
        self.fixture.potential_rows = [dict(key=key, name=raw)]
        row, = await self.api.decree_options(0)
        self.assertEqual(row["key"], key)
        self.assertEqual(row["name_raw"], raw)
        self.assertEqual(row["name"], "Road Maintenance")
        self.assertEqual(row["state_id"], 0)
        self.no_commands()
        self.cleaned()

    async def test_optional_name_error_and_partial_keep_original_key_and_text(self):
        self.fixture.cell_overrides[(POTENTIAL, 0, 1)] = data.ProjectionCell(
            0, 1, 5, data.Kind.STRING, 12, 0, b"Original prefix", 25)
        row = (await self.api.decree_options(8))[0]
        self.assertEqual(row["key"], "decree_road_maintenance")
        self.assertEqual(row["name_raw"], "Original prefix")
        self.assertIn("getter error 5", row["reasons"]["name"])
        self.assertIn("incomplete", row["reasons"]["name"])
        self.no_commands()
        self.cleaned()

    async def test_native_page_error_keeps_copied_instance_id_and_key(self):
        self.fixture.page_overrides[(ACTIVE, 0)] = dict(error=1237, count_valid=False)
        row, = await self.api.decrees(8)
        self.assertEqual(row["id"], 0x08000007)
        self.assertEqual(row["key"], "decree_road_maintenance")
        observed = self.observation()
        self.assertFalse(observed["complete"])
        self.assertEqual(observed["native_pages"][0].error, 1237)
        self.assertEqual(observed["reasons"][0]["native_error"], 1237)
        self.no_commands()
        self.cleaned()

    async def test_public_partial_rows_expose_source_error_and_original_pages(self):
        self.fixture.page_overrides[(ACTIVE, 0)] = dict(error=1237, count_valid=False)
        row, = await self.api.decrees(8)
        self.assertFalse(row["read_complete"])
        self.assertEqual(row["source_reasons"][0]["native_error"], 1237)
        self.assertIs(row["native_pages"][0], self.ctx.created_queries[0].result)
        self.assertEqual((row["id"], row["key"]), (0x08000007, "decree_road_maintenance"))
        self.no_commands()
        self.cleaned()

    async def test_empty_failed_active_read_raises_original_result_with_partial_observation(self):
        self.fixture.active_rows = []
        self.fixture.page_overrides[(ACTIVE, 0)] = dict(error=1168, count_valid=False)
        with self.assertRaises(run_mod.KnownResultError) as caught:
            await self.api.decrees(8)
        error = caught.exception
        self.assertEqual(error.result.error, 1168)
        self.assertEqual(error.partial_result["rows"], [])
        self.assertIs(error.partial_result["native_pages"][0], error.result)
        self.no_commands()

    async def test_empty_failed_potential_read_does_not_return_false_empty_options(self):
        self.fixture.potential_rows = []
        self.fixture.page_overrides[(POTENTIAL, 0)] = dict(error=5)
        with self.assertRaises(run_mod.KnownResultError) as caught:
            await self.api.decree_options(8)
        self.assertEqual(caught.exception.result.error, 5)
        self.assertEqual(caught.exception.partial_result["rows"], [])
        self.no_commands()

    async def test_empty_count_invalid_read_preserves_native_error_zero(self):
        self.fixture.active_rows = []
        self.fixture.page_overrides[(ACTIVE, 0)] = dict(count_valid=False)
        with self.assertRaises(run_mod.KnownResultError) as caught:
            await self.api.decrees(8)
        self.assertEqual(caught.exception.result.error, 0)
        self.assertFalse(caught.exception.result.count_valid)
        self.assertIn("incomplete", str(caught.exception))
        self.no_commands()

    async def test_missing_export_with_true_empty_native_source_still_returns_empty_list(self):
        self.fixture.native_fields_available = False
        self.fixture.active_rows = self.fixture.potential_rows = []
        self.assertEqual(await self.api.decrees(8), [])
        self.assertEqual(await self.api.decree_options(8), [])
        self.no_commands()
        self.cleaned()

    async def test_paging_retains_same_row_pairs_without_joining_potential(self):
        self.fixture.active_rows = [dict(id=0xA0000000+i, key="native_"+str(i)) for i in range(65)]
        rows = await self.api.decrees(8)
        self.assertEqual([(row["id"], row["key"]) for row in rows],
                         [(row["id"], row["key"]) for row in self.fixture.active_rows])
        self.assertEqual([call[3]["start"] for call in self.fixture.page_calls], [0, 64])
        self.no_commands()
        self.cleaned()

    async def test_capability_is_cached_on_connected_context_not_each_run(self):
        await self.api.decree_options(8)
        await self.api.decrees(8)
        self.assertEqual([name for _, name in self.fixture.export_calls], [NATIVE_EXPORT])
        self.no_commands()
        self.cleaned()

    async def test_missing_export_keeps_potential_names_without_command_fallback(self):
        self.fixture.native_fields_available = False
        rows = await self.api.decree_options(8)
        self.assertEqual(rows[0]["name"], "Road Maintenance")
        self.assertIsNone(rows[0]["key"])
        self.assertIsNone(rows[0]["eligible"])
        self.assertIn("export is unavailable", rows[0]["key_reason"])
        self.assertEqual(self.fixture.page_calls[0][1], ("DecreeType.GetName",))
        self.no_commands()
        self.cleaned()

    async def test_missing_export_keeps_original_owned_active_full_ids(self):
        self.fixture.native_fields_available = False
        self.fixture.active_rows = [dict(id=0, key="zero"), dict(id=0xFC000007, key="high")]
        rows = await self.api.decrees(8)
        self.assertEqual([row["id"] for row in rows], [0, 0xFC000007])
        self.assertTrue(all(row["key"] is None for row in rows))
        self.assertTrue(all("NativeFields export unavailable" in row["key_reason"] for row in rows))
        self.assertFalse(self.fixture.page_calls)
        self.assertNotIn("Decree.GetType", self.fixture.events)
        self.no_commands()
        self.cleaned()

    async def test_missing_export_raw_header_does_not_create_a_nativeobject_decoder(self):
        self.fixture.native_fields_available = False
        self.fixture.raw_owned_decree = True
        row, = await self.api.decrees(8)
        self.assertIsNone(row["id"])
        self.assertEqual(row["native_result"].value, b"copied undecoded payload")
        self.assertIn("ownership is not established", row["reasons"]["id"])
        self.no_commands()
        self.cleaned()

    async def test_missing_export_wrong_original_header_retains_unknown_row(self):
        self.fixture.native_fields_available = False
        self.fixture.bad_decree_header = True
        row, = await self.api.decrees(8)
        self.assertIsNone(row["id"])
        self.assertIsNone(row["native_result"].native_type_name)
        self.assertIn("ownership is not established", row["reasons"]["id"])
        self.no_commands()
        self.cleaned()

    async def test_remove_by_full_id_ignores_optional_key_error(self):
        self.fixture.cell_overrides[(ACTIVE, 0, 1)] = data.ProjectionCell(
            0, 1, 1168, data.Kind.STRING, -1, 0, b"native prefix", 30)
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["decree"]["key"], "native prefix")
        self.assertEqual((self.fixture.clone_calls, self.fixture.validations, self.fixture.submit_calls), (1, 1, 1))
        self.assertEqual(self.fixture.serializations, 0)
        self.assertEqual(self.fixture.submitted, [(22672, {22671: commands.UInt32(0x08000007)})])
        self.assertTrue(self.fixture.remove_bindings[0].retained)
        self.assertEqual(self.ctx.perform_labels, ["politics.remove_decree"])
        self.assertTrue(all(owner is not None for label, owner in self.ctx.query_owners if label in
            ("politics.command.validate", "politics.command.submit")))
        self.cleaned()

    async def test_remove_full_id_identity_join_survives_fresh_source_reordering(self):
        self.fixture.active_rows = [dict(id=0x08000001, key="first"), dict(id=0xF8000007, key="chosen")]
        self.fixture.fresh_rows = list(reversed(self.fixture.active_rows))
        receipt = await self.api.remove_decree(8, "chosen")
        self.assertTrue(receipt["accepted"])
        self.assertEqual(self.fixture.submitted[0][1][22671], commands.UInt32(0xF8000007))
        self.assertEqual((self.fixture.validations, self.fixture.submit_calls), (1, 1))
        self.cleaned()

    async def test_remove_copied_id_error_is_preserved_but_not_used_as_clean_identity(self):
        self.fixture.cell_overrides[(ACTIVE, 0, 0)] = data.ProjectionCell(
            0, 0, 5, data.Kind.U32, -1, 0x08000007, b"", 0, binding_error=1237)
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertEqual(receipt["status"], "unavailable")
        row = receipt["observation"]["rows"][0]
        self.assertEqual(row["id"], 0x08000007)
        self.assertIn("getter error 5", row["reasons"]["id"])
        self.assertIn("binding error 1237", row["reasons"]["id"])
        self.no_commands()
        self.cleaned()

    async def test_native_uintmax_id_remains_visible_but_cannot_be_selected_by_key(self):
        self.fixture.active_rows = [dict(id=0xffffffff, key="sentinel_type")]
        receipt = await self.api.remove_decree(8, "sentinel_type")
        self.assertEqual(receipt["status"], "unavailable")
        self.assertEqual(receipt["observation"]["rows"][0]["id"], 0xffffffff)
        self.no_commands()
        self.cleaned()

    async def test_invalid_utf8_native_key_keeps_text_but_is_not_a_clean_selector(self):
        self.fixture.cell_overrides[(ACTIVE, 0, 1)] = data.ProjectionCell(
            0, 1, 0, data.Kind.STRING, -1, 0, b"decree_\xff", 8)
        receipt = await self.api.remove_decree(8, "decree_\ufffd")
        self.assertEqual(receipt["status"], "unavailable")
        row = receipt["observation"]["rows"][0]
        self.assertEqual(row["key"], "decree_\ufffd")
        self.assertNotIn("key", row["reasons"])
        self.assertFalse(row["key_text_lossless"])
        self.assertIsNone(row["key_native_text"])
        self.assertIn("no lossless UTF-8 text selector", row["key_encoding_reason"])
        self.assertTrue(receipt["observation"]["complete"])
        self.assertEqual(row["key_raw"], b"decree_\xff")
        self.assertEqual(row["key_total"], 8)
        self.no_commands()
        self.cleaned()

    async def test_remove_does_not_treat_a_display_name_as_native_key(self):
        receipt = await self.api.remove_decree(8, "Road Maintenance")
        self.assertIsNone(receipt["accepted"])
        self.no_commands()
        self.cleaned()

    async def test_clean_native_key_match_is_not_blocked_by_other_unknown_row_or_page_error(self):
        self.fixture.active_rows.append(dict(id=0x08000008, key="unrelated"))
        self.fixture.cell_overrides[(ACTIVE, 1, 1)] = data.ProjectionCell(
            1, 1, 5, data.Kind.STRING, -1, 0, b"unknown prefix", 30)
        self.fixture.page_overrides[(ACTIVE, 0)] = dict(error=1237, count_valid=False)
        receipt = await self.api.remove_decree(8, "decree_road_maintenance")
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["decree"]["id"], 0x08000007)
        self.assertEqual(receipt["observation"]["reasons"][0]["native_error"], 1237)
        self.assertEqual(self.fixture.submit_calls, 1)
        self.cleaned()

    async def test_clean_full_id_match_survives_other_unknown_id_and_global_error(self):
        self.fixture.active_rows.append(dict(id=0x08000008, key="unrelated"))
        self.fixture.cell_overrides[(ACTIVE, 1, 0)] = data.ProjectionCell(
            1, 0, 5, data.Kind.U32, -1, 0x08000008, b"", 0)
        self.fixture.page_overrides[(ACTIVE, 0)] = dict(error=1237)
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["decree"]["id"], 0x08000007)
        self.assertEqual(self.fixture.submit_calls, 1)
        self.cleaned()

    async def test_no_match_in_incomplete_source_is_unknown_not_native_rejection(self):
        self.fixture.page_overrides[(ACTIVE, 0)] = dict(error=1237)
        receipt = await self.api.remove_decree(8, "not_observed")
        self.assertEqual(receipt["status"], "unavailable")
        self.assertIsNone(receipt["accepted"])
        self.assertEqual(receipt["observation"]["native_pages"][0].error, 1237)
        self.no_commands()
        self.cleaned()

    async def test_legal_replacement_character_key_uses_original_utf8_bytes(self):
        key = "decree_\ufffd"
        self.fixture.active_rows = [dict(id=0x08000007, key=key)]
        receipt = await self.api.remove_decree(8, key)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["decree"]["key_raw"], key.encode("utf-8"))
        self.assertEqual(receipt["decree"]["key_total"], len(key.encode("utf-8")))
        self.assertIs(receipt["decree"]["key_native_cell"], receipt["observation"]["native_pages"][0].rows[0][1])
        self.assertNotIn("key", receipt["decree"]["reasons"])
        self.assertTrue(receipt["decree"]["key_text_lossless"])
        self.assertIsNone(receipt["decree"]["key_encoding_reason"])
        self.cleaned()

    async def test_remove_zero_id_is_not_treated_as_missing(self):
        self.fixture.active_rows = [dict(id=0, key="native_zero")]
        receipt = await self.api.remove_decree(8, 0)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(self.fixture.submitted[0][1][22671], commands.UInt32(0))
        self.cleaned()

    async def test_remove_unknown_key_does_not_create_any_command(self):
        receipt = await self.api.remove_decree(8, "not_a_native_key")
        self.assertIsNone(receipt["accepted"])
        self.assertEqual(receipt["status"], "rejected")
        self.no_commands()
        self.cleaned()

    async def test_remove_partial_key_never_selects_a_plausible_prefix(self):
        self.fixture.cell_overrides[(ACTIVE, 0, 1)] = data.ProjectionCell(
            0, 1, 0, data.Kind.STRING, -1, 0, b"decree_road", 30)
        receipt = await self.api.remove_decree(8, "decree_road")
        self.assertEqual(receipt["status"], "unavailable")
        self.assertEqual(receipt["observation"]["rows"][0]["key"], "decree_road")
        self.no_commands()
        self.cleaned()

    async def test_remove_ambiguous_native_key_requires_full_instance_id(self):
        self.fixture.active_rows.append(dict(id=0x08000008, key="decree_road_maintenance"))
        receipt = await self.api.remove_decree(8, "decree_road_maintenance")
        self.assertIn("ambiguous", receipt["reason"])
        self.assertEqual([row["id"] for row in receipt["observation"]["rows"]], [0x08000007, 0x08000008])
        self.no_commands()
        self.cleaned()

    async def test_remove_does_not_select_a_replacement_at_old_ordinal(self):
        self.fixture.fresh_rows = [dict(id=0x09000007, key="decree_road_maintenance")]
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertEqual(receipt["status"], "unavailable")
        self.assertIn("absent from the fresh", receipt["reason"])
        self.no_commands()
        self.cleaned()

    async def test_remove_strict_original_header_cannot_be_inferred_from_value(self):
        self.fixture.bad_decree_header = True
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertEqual(receipt["status"], "unavailable")
        self.assertIn("ownership is not established", receipt["reason"])
        self.assertTrue(any(isinstance(result, NS) and getattr(result, "native_type_name", "missing") is None
            for _, result in receipt["native_results"]))
        self.no_commands()
        self.cleaned()

    async def test_remove_by_id_still_works_when_nativefields_export_is_missing(self):
        self.fixture.native_fields_available = False
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertTrue(receipt["accepted"])
        self.assertIsNone(receipt["decree"]["key"])
        self.assertEqual((self.fixture.validations, self.fixture.submit_calls), (1, 1))
        self.assertEqual(self.fixture.serializations, 0)
        self.cleaned()

    async def test_remove_by_key_missing_export_keeps_readable_ids_and_no_command(self):
        self.fixture.native_fields_available = False
        receipt = await self.api.remove_decree(8, "decree_road_maintenance")
        self.assertEqual(receipt["status"], "unavailable")
        self.assertEqual(receipt["observation"]["rows"][0]["id"], 0x08000007)
        self.assertIn("export unavailable", receipt["observation"]["rows"][0]["key_reason"])
        self.no_commands()
        self.cleaned()

    async def test_native_permissions_not_a_context_actor_receipt_gate(self):
        self.ctx.selected_actor = self.fixture.actor + 1
        self.ctx.world = 0
        self.fixture.reject.add(22672)
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertFalse(receipt["accepted"])
        self.assertEqual(receipt["reason"], "Not enough bureaucracy")
        self.assertEqual((self.fixture.validations, self.fixture.submit_calls), (1, 0))
        self.assertEqual(self.fixture.serializations, 0)
        self.cleaned()

    async def test_native_validation_error_keeps_original_result_without_submit(self):
        self.fixture.validation_error = 5
        self.fixture.validation_reason = "Original native validation error"
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertEqual(receipt["native_error"], 5)
        self.assertEqual(receipt["reason"], "Original native validation error")
        self.assertIs(receipt["native_result"], receipt["validation"])
        self.assertEqual((self.fixture.validations, self.fixture.submit_calls), (1, 0))
        self.cleaned()

    async def test_native_accepted_plus_error_is_not_rewritten_as_rejected(self):
        self.fixture.submit_error = 87
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(receipt["native_error"], 87)
        self.assertIs(receipt["native_result"], self.fixture.submission_query.result)
        self.assertEqual(self.fixture.submit_calls, 1)
        self.cleaned()

    async def test_unknown_validation_retains_original_owner_and_does_not_submit(self):
        self.fixture.unknown_validation = True
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertEqual(receipt["native_outcome"], "unknown")
        self.assertEqual(receipt["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertTrue(self.api._retained[0].owners[0].command)
        self.assertTrue(self.fixture.handles)
        self.assertEqual((self.fixture.validations, self.fixture.submit_calls), (1, 0))

    async def test_accepted_unsettled_submit_keeps_original_handle_and_acceptance(self):
        self.fixture.keep_submit_owner = True
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["native_outcome"], "pending")
        self.assertEqual(receipt["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertTrue(self.api._retained[0].owners[0].command)
        self.assertEqual(self.fixture.submit_calls, 1)
        self.assertNotIn("destroy:22672", self.fixture.events)

    async def test_cleanup_warning_does_not_erase_accepted_native_result(self):
        self.fixture.release_error = 5
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["native_result"].error, 0)
        self.assertEqual(receipt["cleanup"]["native_result"].error, 5)
        self.assertEqual(self.fixture.submit_calls, 1)
        self.assertTrue(self.api._retained)

    async def test_real_executor_pending_resume_never_recreates_remove_command(self):
        self.ctx.timeout = 0
        self.fixture.delay_submit = 5
        pending = await self.api.remove_decree(8, 0x08000007)
        self.assertIsInstance(pending, PendingOperation)
        receipt = await pending.resume(timeout=1)
        self.assertTrue(receipt["accepted"])
        self.assertEqual((self.fixture.clone_calls, self.fixture.validations, self.fixture.submit_calls), (1, 1, 1))
        self.assertTrue(all(self.ctx.created_queries.count(query) == 1 for query in self.ctx.created_queries))
        self.cleaned()

    async def test_selected_source_keeps_original_data_adapter_for_query_and_release(self):
        self.ctx.adapters["data"] = "selected-original-data.dll"
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(self.fixture.page_calls[0][3]["adapter"], "selected-original-data.dll")
        self.assertTrue(self.fixture.release_adapters)
        self.assertTrue(all(adapter == "selected-original-data.dll" for adapter in self.fixture.release_adapters))
        self.assertEqual(self.ctx.perform_labels, ["politics.remove_decree"])
        self.cleaned()


    async def test_set_uses_explicit_native_expression_fixtures_for_plain_literal_subset(self):
        self.fixture.active_rows = []
        cases = [(key, expression) for key, expression in SET_LITERAL_CASES
                 if key not in ("", "not_installed_native_key")]
        for index, (key, expression) in enumerate(cases, 1):
            with self.subTest(key=key):
                receipt = await self.api.set_decree(8, key)
                self.assertTrue(receipt["accepted"])
                self.assertEqual(receipt["requested_decree_key"], key)
                self.assertEqual(receipt["key_scope"], "request")
                self.assertIsNone(receipt["decree"])
                self.assertEqual(self.fixture.set_requests[-1][0], expression)
                self.assertEqual(self.fixture.submitted[-1],
                    (22673, {439: commands.UInt32(8), 22671: key}))
                self.assertEqual((self.fixture.clone_calls, self.fixture.validations,
                                  self.fixture.submit_calls), (index, index, index))
                self.assertEqual(self.fixture.serializations, 0)
                self.cleaned()
        self.assertEqual(len(self.fixture.set_requests), len(cases))
        self.assertTrue(all(call[0] == ACTIVE for call in self.fixture.page_calls))
        self.assertNotIn("Decree.GetType", self.fixture.events)
        self.assertNotIn(POTENTIAL, self.fixture.events)

    async def test_set_calls_actual_codec_with_unmodified_whitespace_key(self):
        key = " decree_new "
        actual_codec = data.plain_cstring_literal
        with patch.object(data, "plain_cstring_literal", wraps=actual_codec) as codec:
            receipt = await self.api.set_decree(8, key)
        codec.assert_called_once_with(key)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["requested_decree_key"], key)
        self.assertEqual(self.fixture.set_requests[0][0],
                         "GetDecreeType(' decree_new ').GetSetCommand(State.Self)")
        self.cleaned()

    async def test_set_unsafe_literal_requests_keep_key_and_original_codec_error(self):
        for key in ("native\x00tail", "(Country)native", "(x)native", "( )native", "\ud800"):
            with self.subTest(key=repr(key)):
                receipt = await self.api.set_decree(8, key)
                self.assertEqual(receipt["status"], "unsupported")
                self.assertIsNone(receipt["accepted"])
                self.assertEqual(receipt["requested_decree_key"], key)
                self.assertEqual(receipt["key_scope"], "request")
                self.assertIsInstance(receipt["literal_error"], (ValueError, UnicodeError))
                self.assertNotIn("keyinvalid", receipt["reason"].lower().replace(" ", ""))
        self.assertFalse(self.fixture.page_calls)
        self.no_commands()
        self.cleaned()

    async def test_set_literal_error_is_the_original_object_not_a_native_false(self):
        original_error = ValueError("Original literal-subset boundary")
        with patch.object(data, "plain_cstring_literal", side_effect=original_error):
            receipt = await self.api.set_decree(8, "decree_new")
        self.assertIs(receipt["literal_error"], original_error)
        self.assertIsNone(receipt["accepted"])
        self.no_commands()
        self.cleaned()

    async def test_set_definition_id_and_nonstring_are_explicitly_unsupported(self):
        for selected in (0x08000007, 0, True, None):
            with self.subTest(selected=selected):
                receipt = await self.api.set_decree(8, selected)
                self.assertEqual(receipt["status"], "unsupported")
                self.assertIsNone(receipt["accepted"])
                self.assertIs(receipt["requested_decree_key"], selected)
                self.assertIn("native key string", receipt["reason"])
        self.no_commands()
        self.cleaned()

    async def test_set_unknown_key_uses_original_null_definition_command_validation(self):
        key = "not_installed_native_key"
        self.fixture.native_set_definition_keys[key] = ""
        self.fixture.reject.add(22673)
        self.fixture.validation_reason = "Original native null-definition command rejection"
        receipt = await self.api.set_decree(8, key)
        self.assertFalse(receipt["accepted"])
        self.assertFalse(receipt["validation"].validated)
        self.assertEqual(receipt["reason"], self.fixture.validation_reason)
        self.assertEqual(receipt["requested_decree_key"], key)
        self.assertEqual(receipt["key_scope"], "request")
        self.assertIsNone(receipt["decree"])
        self.assertEqual(self.fixture.owners[0].params[22671], "")
        self.assertEqual((len(self.fixture.set_requests), self.fixture.clone_calls,
                          self.fixture.validations, self.fixture.submit_calls), (1, 1, 1, 0))
        self.cleaned()

    async def test_set_empty_string_reaches_native_validation_without_host_normalization(self):
        self.fixture.reject.add(22673)
        receipt = await self.api.set_decree(8, "")
        self.assertEqual(receipt["requested_decree_key"], "")
        self.assertEqual(self.fixture.set_requests[0][0], "GetDecreeType('').GetSetCommand(State.Self)")
        self.assertFalse(receipt["accepted"])
        self.assertEqual(self.fixture.validations, 1)
        self.cleaned()

    async def test_set_already_active_outside_potential_is_positive_observation_not_submission(self):
        self.fixture.potential_rows = []
        receipt = await self.api.set_decree(8, "decree_road_maintenance")
        self.assertEqual(receipt["status"], "unchanged")
        self.assertTrue(receipt["active_decree_observed"])
        self.assertIsNone(receipt["accepted"])
        self.assertEqual(receipt["decree"]["id"], 0x08000007)
        self.assertEqual(receipt["decree"]["key_native_text"], "decree_road_maintenance")
        self.assertIn("no command was submitted", receipt["reason"])
        self.no_commands()
        self.cleaned()

    async def test_set_positive_active_key_is_not_vetoed_by_global_or_other_row_error(self):
        self.fixture.active_rows.append(dict(id=0x08000008, key="unrelated"))
        self.fixture.cell_overrides[(ACTIVE, 1, 1)] = data.ProjectionCell(
            1, 1, 5, data.Kind.STRING, -1, 0, b"partial", 30)
        self.fixture.page_overrides[(ACTIVE, 0)] = dict(error=1237, count_valid=False)
        receipt = await self.api.set_decree(8, "decree_road_maintenance")
        self.assertEqual(receipt["status"], "unchanged")
        self.assertTrue(receipt["active_decree_observed"])
        self.assertIsNone(receipt["accepted"])
        self.assertEqual(receipt["observation"]["native_pages"][0].error, 1237)
        self.no_commands()
        self.cleaned()

    async def test_set_unknown_active_read_does_not_block_original_native_command(self):
        self.fixture.active_rows = []
        self.fixture.page_overrides[(ACTIVE, 0)] = dict(error=1237, count_valid=False)
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertTrue(receipt["accepted"])
        self.assertIsNone(receipt["active_decree_observed"])
        self.assertFalse(receipt["observation"]["complete"])
        self.assertEqual((self.fixture.clone_calls, self.fixture.validations, self.fixture.submit_calls), (1, 1, 1))
        self.cleaned()

    async def test_set_partial_active_key_view_is_not_an_idempotence_receipt(self):
        self.fixture.cell_overrides[(ACTIVE, 0, 1)] = data.ProjectionCell(
            0, 1, 0, data.Kind.STRING, -1, 0, b"decree_road_maintenance", 40)
        receipt = await self.api.set_decree(8, "decree_road_maintenance")
        self.assertTrue(receipt["accepted"])
        self.assertIsNone(receipt["active_decree_observed"])
        self.assertFalse(receipt["observation"]["rows"][0]["key_complete"])
        self.assertEqual((len(self.fixture.set_requests), self.fixture.validations, self.fixture.submit_calls), (1, 1, 1))
        self.cleaned()

    async def test_set_foreign_context_actor_and_world_do_not_retarget_or_gate(self):
        self.ctx.world = 0
        self.ctx.selected_actor = self.fixture.actor + 1
        async def no_country_receipt():
            raise AssertionError("A selected native producer does not need a context actor receipt")
        self.ctx.country_id = no_country_receipt
        state = 0xF8000008
        receipt = await self.api.set_decree(state, "decree_new")
        self.assertTrue(receipt["accepted"])
        binding, = self.fixture.set_requests[0][1]
        self.assertEqual((binding.domain, binding.object, binding.country_id), ("State", state, False))
        self.assertEqual(self.fixture.submitted[0][1][439], commands.UInt32(state))
        self.assertEqual(self.fixture.serializations, 0)
        self.assertEqual(self.ctx.perform_labels, ["politics.set_decree"])
        self.cleaned()

    async def test_set_native_validation_partial_error_keeps_result_and_no_submit(self):
        self.fixture.validation_error = 5
        self.fixture.validation_reason = "Original native partial validation reason"
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertIsNone(receipt["accepted"])
        self.assertEqual(receipt["native_error"], 5)
        self.assertIs(receipt["native_result"], receipt["validation"])
        self.assertEqual(receipt["reason"], self.fixture.validation_reason)
        self.assertEqual((self.fixture.validations, self.fixture.submit_calls), (1, 0))
        self.cleaned()

    async def test_set_native_accepted_plus_error_preserves_acceptance_and_original_result(self):
        self.fixture.submit_error = 87
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["native_error"], 87)
        self.assertEqual(receipt["status"], "accepted")
        self.assertIs(receipt["native_result"], self.fixture.submission_query.result)
        self.assertIsNone(receipt["decree"])
        self.assertNotIn("applied", receipt)
        self.assertEqual(self.fixture.submit_calls, 1)
        self.cleaned()

    async def test_set_native_receiver_rejection_is_original_false_not_replayed(self):
        self.fixture.submit_accepted = False
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertFalse(receipt["accepted"])
        self.assertEqual(receipt["native_result"].error, 0)
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(self.fixture.submit_calls, 1)
        self.cleaned()

    async def test_set_native_completed_error_has_unknown_acceptance_not_applied_false(self):
        self.fixture.submit_accepted = False
        self.fixture.submit_error = 5
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertIsNone(receipt["accepted"])
        self.assertEqual(receipt["native_result"].error, 5)
        self.assertEqual(receipt["status"], "native_result")
        self.assertNotIn("applied", receipt)
        self.assertEqual(self.fixture.submit_calls, 1)
        self.cleaned()

    async def test_set_unknown_validation_retains_original_owner_without_submit(self):
        self.fixture.unknown_validation = True
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertIsNone(receipt["accepted"])
        self.assertEqual(receipt["native_outcome"], "unknown")
        self.assertEqual(receipt["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertTrue(self.api._retained[0].owners[0].command)
        self.assertEqual((len(self.fixture.set_requests), self.fixture.validations, self.fixture.submit_calls), (1, 1, 0))

    async def test_set_accepted_pending_owner_keeps_original_handle_without_cleanup_replay(self):
        self.fixture.keep_submit_owner = True
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["native_outcome"], "pending")
        self.assertEqual(receipt["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertTrue(self.api._retained[0].owners[0].command)
        self.assertEqual(self.fixture.submit_calls, 1)
        self.assertNotIn("destroy:22673", self.fixture.events)

    async def test_set_cleanup_warning_does_not_erase_native_acceptance(self):
        self.fixture.release_error = 5
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["native_result"].error, 0)
        self.assertEqual(receipt["cleanup"]["native_result"].error, 5)
        self.assertEqual(self.fixture.submit_calls, 1)
        self.assertTrue(self.api._retained)

    async def test_set_real_pending_resume_reuses_original_producer_clone_and_submit(self):
        self.ctx.timeout = 0
        self.fixture.delay_submit = 5
        pending = await self.api.set_decree(8, "decree_new")
        self.assertIsInstance(pending, PendingOperation)
        receipt = await pending.resume(timeout=1)
        self.assertTrue(receipt["accepted"])
        self.assertEqual((len(self.fixture.set_requests), self.fixture.clone_calls,
                          self.fixture.validations, self.fixture.submit_calls), (1, 1, 1, 1))
        self.assertTrue(all(self.ctx.created_queries.count(query) == 1 for query in self.ctx.created_queries))
        self.cleaned()

    async def test_set_native_producer_error_keeps_original_partial_carrier_result(self):
        self.fixture.set_producer_error = 1168
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertEqual(receipt["native_error"], 1168)
        self.assertIsNone(receipt["accepted"])
        self.assertIsInstance(receipt["native_result"].value, data.NativeDataCommand)
        self.assertEqual((len(self.fixture.set_requests), self.fixture.clone_calls,
                          self.fixture.validations, self.fixture.submit_calls), (1, 0, 0, 0))
        self.cleaned()

    async def test_set_completed_none_producer_is_unknown_not_fabricated_false(self):
        self.fixture.set_producer_none = True
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertEqual(receipt["status"], "unavailable")
        self.assertIsNone(receipt["accepted"])
        original = next(value for name, value in receipt["native_results"] if "GetSetCommand" in name)
        self.assertIsNone(original.value)
        self.assertEqual((len(self.fixture.set_requests), self.fixture.clone_calls,
                          self.fixture.validations, self.fixture.submit_calls), (1, 0, 0, 0))
        self.cleaned()

    async def test_set_partial_clone_error_retains_unadopted_original_pointer(self):
        self.fixture.clone_error = 5
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertEqual(receipt["native_error"], 5)
        self.assertIsNone(receipt["accepted"])
        self.assertTrue(receipt["native_result"].cloned_command)
        self.assertIs(self.api._retained[0].unadopted_command, receipt["native_result"])
        self.assertEqual((self.fixture.clone_calls, self.fixture.validations, self.fixture.submit_calls), (1, 0, 0))
        self.assertNotIn("destroy:22673", self.fixture.events)

    async def test_set_preserves_original_data_adapter_and_one_outer_perform(self):
        self.ctx.adapters["data"] = "selected-original-set-data.dll"
        receipt = await self.api.set_decree(8, "decree_new")
        self.assertTrue(receipt["accepted"])
        self.assertEqual(self.fixture.set_requests[0][2]["adapter"], "selected-original-set-data.dll")
        self.assertTrue(all(adapter == "selected-original-set-data.dll" for adapter in self.fixture.release_adapters))
        self.assertEqual(self.ctx.perform_labels, ["politics.set_decree"])
        self.cleaned()

    async def test_complete_non_utf8_native_key_is_read_complete_with_independent_view_limit(self):
        self.fixture.cell_overrides[(ACTIVE, 0, 1)] = data.ProjectionCell(
            0, 1, 0, data.Kind.STRING, -1, 0, b"decree_\xff", 8)
        row, = await self.api.decrees(8)
        self.assertTrue(row["read_complete"])
        self.assertTrue(row["key_complete"])
        self.assertEqual(row["key_raw"], b"decree_\xff")
        self.assertEqual(row["key"], "decree_\ufffd")
        self.assertFalse(row["key_text_lossless"])
        self.assertIsNone(row["key_native_text"])
        self.assertNotIn("key", row["reasons"])
        self.assertIn("no lossless UTF-8 text selector", row["key_encoding_reason"])
        self.no_commands()
        self.cleaned()

    async def test_native_nul_tail_remains_full_read_text_and_full_id_can_remove(self):
        key = "native_prefix\x00tail"
        self.fixture.active_rows = [dict(id=0x08000007, key=key)]
        row, = await self.api.decrees(8)
        self.assertEqual(row["key_raw"], key.encode("utf-8"))
        self.assertEqual(row["key_native_text"], key)
        self.assertTrue(row["key_text_lossless"])
        self.assertTrue(row["read_complete"])
        receipt = await self.api.remove_decree(8, 0x08000007)
        self.assertTrue(receipt["accepted"])
        self.cleaned()

    async def test_shortened_key_view_warning_is_not_a_native_error_or_remove_gate(self):
        key = "native_prefix\x00tail"
        self.fixture.active_rows = [dict(id=0x08000007, key=key)]
        original = self.api._decree_key_bytes
        def shortened_view(observed, column):
            for row in observed["rows"]:
                row["key"] = "native_prefix"  # Explicit test-only display view.
            original(observed, column)
        with patch.object(self.api, "_decree_key_bytes", side_effect=shortened_view):
            receipt = await self.api.remove_decree(8, key)
        self.assertTrue(receipt["accepted"])
        row = receipt["decree"]
        self.assertEqual(row["key"], "native_prefix")
        self.assertEqual(row["key_native_text"], key)
        self.assertFalse(row["key_text_lossless"])
        self.assertIn("view differs", row["key_encoding_reason"])
        self.assertNotIn("key", row["reasons"])
        self.assertTrue(receipt["observation"]["complete"])
        self.cleaned()

    async def test_native_empty_key_bytes_stay_a_complete_native_observation(self):
        self.fixture.potential_rows = [dict(key="", name="Native empty-key observation")]
        row, = await self.api.decree_options(8)
        self.assertEqual(row["key"], "")
        self.assertEqual(row["key_raw"], b"")
        self.assertEqual(row["key_native_text"], "")
        self.assertTrue(row["key_text_lossless"])
        self.assertTrue(row["read_complete"])
        self.assertNotIn("key", row["reasons"])
        self.no_commands()
        self.cleaned()


    async def test_set_does_not_translate_display_name_to_a_catalog_key(self):
        requested = "Road Maintenance"
        expression = "GetDecreeType('Road Maintenance').GetSetCommand(State.Self)"
        self.fixture.expected_set_expressions[expression] = requested
        self.fixture.native_set_definition_keys[requested] = ""
        self.fixture.reject.add(22673)
        receipt = await self.api.set_decree(8, requested)
        self.assertFalse(receipt["accepted"])
        self.assertEqual(receipt["requested_decree_key"], requested)
        self.assertEqual(self.fixture.set_requests[0][0], expression)
        self.assertIsNone(receipt["active_decree_observed"])
        self.assertEqual((self.fixture.validations, self.fixture.submit_calls), (1, 0))
        self.cleaned()

    async def test_set_positive_raw_key_is_not_blocked_by_a_different_text_view(self):
        original = self.api._decree_key_bytes
        def display_view(observed, column):
            for row in observed["rows"]:
                row["key"] = "Road Maintenance"  # Explicit view, not native identity.
            original(observed, column)
        with patch.object(self.api, "_decree_key_bytes", side_effect=display_view):
            receipt = await self.api.set_decree(8, "decree_road_maintenance")
        self.assertEqual(receipt["status"], "unchanged")
        self.assertIsNone(receipt["accepted"])
        self.assertTrue(receipt["active_decree_observed"])
        self.assertFalse(receipt["decree"]["key_text_lossless"])
        self.assertEqual(receipt["decree"]["key_native_text"], "decree_road_maintenance")
        self.assertNotIn("key", receipt["decree"]["reasons"])
        self.no_commands()
        self.cleaned()


if __name__ == "__main__":
    unittest.main()
