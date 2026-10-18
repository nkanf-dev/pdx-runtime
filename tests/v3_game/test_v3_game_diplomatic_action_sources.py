"""Five native source families; focused ownership/execution contract integration.

Real retained RAW DataResult headers, leases, command owners and execution code.
Only native transports are CPU fixtures; imported legacy test methods are not run.
"""
import asyncio
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game.diplomacy import actions as a
from pdx_runtime.v3_game._choices import OwnedChoices
from tests.v3_game.test_v3_game_diplomatic_selected import Fixture as BaseFixture, TARGET, EXACT_RTTI
from tests.v3_game.test_v3_game_diplomatic_action_details import Context as DetailContext, Fixture as DetailFixture
from tests.v3_game.test_v3_game_diplomatic_command_edit import Fixture as EditFixture, native_wire, A


class Context(DetailContext):
    def __init__(self):
        super().__init__()
        self.stage = asyncio.Event()


class Fixture(DetailFixture, EditFixture):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.edit_transport = False

    def evaluate(self, client, expression, bindings, **options):
        if expression not in a._DIPLOMATIC_SOURCES:
            return BaseFixture.evaluate(self, client, expression, bindings, **options)
        self.events.append((expression, tuple(bindings), options["adapter"]))
        assert len(bindings) == 1 and bindings[0].domain == "Country"
        assert bindings[0].object == TARGET
        query = self.value(data.NativeList(0, 0, b"", expression), data.Kind.LIST)
        self.lists[query.result.value_handle] = list(self.rows)
        return query

    def command_request(self, client, action, **options):
        if self.edit_transport:
            return EditFixture.command_request(self, client, action, **options)
        return BaseFixture.command_request(self, client, action, **options)


class DiplomaticSourceFamilyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ctx = Context()
        self.api, self.f = DiplomacyAPI(self.ctx), Fixture(self.ctx)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("evaluate", "list_count", "list_element", "project_page", "command_value", "release"):
            method = self.f.project if name == "project_page" else getattr(self.f, name)
            self.stack.enter_context(patch.object(data, name, side_effect=method))
        self.stack.enter_context(patch.object(commands, "request", side_effect=self.f.command_request))
        self.create = self.stack.enter_context(patch.object(commands, "create", side_effect=AssertionError("no actor reconstruction")))

    def calls(self):
        return [action for action, _ in self.f.command_calls]

    async def close(self, catalog):
        await catalog.close()
        self.assertFalse(self.f.handles)
        self.assertFalse(self.ctx.owner_pool.snapshot())
        self.assertEqual(self.ctx.country_reads, 0)
        self.create.assert_not_called()

    async def test_four_additional_families_execute_exact_owned_raw_row_and_adapter(self):
        for group, scope in (("overlord", "available"), ("subject", "potential"),
                ("bloc_leader", "all"), ("bloc_member", "available")):
            with self.subTest(group=group, scope=scope):
                self.f.rows = [("owned", 11), ("owned", 22)]
                self.f.row_type_id = 199
                self.f.source_adapter = "actual-" + group + "-adapter"
                self.f.command_calls.clear()
                catalog = await self.api.diplomatic_action_choices(TARGET, group=group, scope=scope)
                choice = catalog[1]
                source = "Country.Get" + a._SOURCE_SCOPES[scope] + a._ACTION_GROUPS[group] + "DiploActions"
                self.assertEqual(catalog.metadata["source"], source)
                self.assertEqual(choice["source"]["expression"], source)
                self.assertEqual(choice.native_result.kind, data.Kind.RAW)
                self.assertEqual(choice.native_result.native_type_name, EXACT_RTTI)
                self.assertEqual(choice._binding.type_id, 199)
                self.assertEqual(choice._adapter, self.f.source_adapter)
                self.assertEqual((choice["first_country_id"], choice["second_country_id"]), (0, TARGET))
                self.f.rows = [("owned", 999)]  # changed source cannot reselect the saved row
                self.f.events.clear()
                result = await self.api.execute_diplomatic_action(choice, readback=False)
                self.assertTrue(result["accepted"])
                self.assertEqual(self.f.last_selected, 22)
                self.assertEqual((result["source_group"], result["source_scope"]), (group, scope))
                self.assertEqual(result["source"], source)
                self.assertEqual(self.calls(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.SUBMIT])
                self.assertFalse(any(event[0] in a._DIPLOMATIC_SOURCES for event in self.f.events))
                self.assertTrue(all(event[2] == self.f.source_adapter for event in self.f.events
                    if event[0] in ("DiplomaticAction.ExecuteAction", "carrier")))
                await self.close(catalog)

    async def test_general_default_and_explicit_all_use_the_same_original_native_route(self):
        for options in ({}, {"group": "general", "scope": "all"}):
            with self.subTest(options=options):
                self.f.events.clear()
                self.f.command_calls.clear()
                catalog = await self.api.diplomatic_action_choices(TARGET, **options)
                self.assertEqual(self.f.events[0][0], a._GENERAL_SOURCE)
                self.assertEqual((catalog.metadata["source_group"], catalog.metadata["source_scope"]), ("general", "all"))
                self.assertEqual(self.calls(), [])
                result = await self.api.execute_diplomatic_action(catalog[0], readback=False)
                self.assertTrue(result["accepted"])
                self.assertEqual(self.calls(), [commands.Action.CLONE, commands.Action.VALIDATE, commands.Action.SUBMIT])
                await self.close(catalog)

    async def test_available_and_potential_details_keep_real_source_membership_not_validation(self):
        for group, scope in (("overlord", "available"), ("bloc_member", "potential")):
            with self.subTest(group=group, scope=scope):
                catalog = await self.api.diplomatic_action_choices(TARGET, group=group, scope=scope)
                choice = catalog[0]
                result = await self.api.diplomatic_action_details(choice)
                self.assertEqual((result["source_group"], result["source_scope"]), (group, scope))
                self.assertEqual(result["source"], catalog.metadata["source"])
                self.assertIs(result["source_native_result"], choice.native_result)
                self.assertFalse(result["command_validation_performed"])
                self.assertEqual(self.f.details_calls[-1][1], ((choice._binding,),))
                self.assertEqual(catalog.metadata["result"]["available"], True if scope == "available" else None)
                self.assertEqual(self.calls(), [])
                await self.close(catalog)

    async def test_illegal_group_scope_and_nonfamily_catalog_fail_before_native_execution(self):
        for options in ({"group": "Country.GetAllGeneralDiploActions"}, {"group": "unknown"},
                {"scope": "All"}, {"scope": []}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    await self.api.diplomatic_action_choices(TARGET, **options)
        self.assertEqual(self.f.events, [])
        catalog = await self.api.diplomatic_action_choices(TARGET, group="subject")
        other = OwnedChoices(catalog.owner, metadata={"source": "Country.GetAllSubjectDiploActions.extra"})
        choice = catalog[0]
        rejected = other.add(choice.native_result, "DiplomaticAction", choice._binding, dict(choice))
        with self.assertRaises(ValueError):
            await self.api.execute_diplomatic_action(rejected, readback=False)
        self.assertEqual(self.calls(), [])
        await self.close(catalog)

    async def test_other_family_non21256_retains_original_result_and_reports_edit_unsupported(self):
        self.f.edit_transport = True
        self.f.original_type = 42
        self.f.original_wire = commands.encode_command(42, (A(10304, commands.UInt32(0xf1000005)),))
        catalog = await self.api.diplomatic_action_choices(TARGET, group="bloc_leader")
        result = await self.api.execute_diplomatic_action(catalog[0], country_only=False, readback=False)
        self.assertEqual(result["status"], "edit_unimplemented")
        self.assertEqual(result["original_wire"], self.f.original_wire)
        self.assertEqual(result["original_serialization"].type_token, 42)
        self.assertFalse(result["wire_edit_performed"])
        self.assertEqual(result["source_group"], "bloc_leader")
        self.assertNotIn(commands.Action.DESERIALIZE, self.calls())
        self.assertNotIn(commands.Action.VALIDATE, self.calls())
        self.assertNotIn(commands.Action.SUBMIT, self.calls())
        await self.close(catalog)

    async def test_other_family_21256_edits_preserve_native_actor_parties_mode_and_other_state(self):
        self.f.edit_transport = True
        self.f.original_wire = native_wire(actor=0xf1000005, mode=24431, targets=(
            A(10374, commands.UInt32(0xa0000043)), A(10375, commands.UInt32(TARGET)),
            A(24084, commands.UInt32(0xab000007)), A(24085, commands.UInt32(0xcd000008)), A(24086, True)))
        catalog = await self.api.diplomatic_action_choices(TARGET, group="subject", scope="potential")
        result = await self.api.execute_diplomatic_action(catalog[1], obligation="call_in_obligation",
            second_state=0, readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.f.parsed["actor"], 0xf1000005)
        self.assertEqual(self.f.parsed[10374], 0xa0000043)
        self.assertEqual(self.f.parsed[10375], TARGET)
        self.assertEqual(self.f.parsed[24084], 0xab000007)
        self.assertEqual(self.f.parsed[24085], 0)
        self.assertIs(self.f.parsed[24086], True)
        self.assertEqual(self.f.parsed[24152], commands.Token(24431))
        self.assertEqual(self.f.parsed[21268], commands.Token(21619))
        self.assertEqual(self.f.serializations, [self.f.clone_pointer])
        self.assertEqual((result["source_group"], result["source_scope"]), ("subject", "potential"))
        await self.close(catalog)
