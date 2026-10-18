"""CPU-only native diplomatic-play candidate/producer contracts; no game or DLL.

These fixtures prove source, binding, ownership and once-only composition.
They do not prove the producer's demand/forced-play mode or live eligibility.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.query_executor import PendingOperation, SerializedQueryExecutor
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.diplomacy import play_options as p
from pdx_runtime.v3_game._choices import OwnedChoices, ChoicesBusyError


class Context:
    def __init__(self):
        self.native, self.process = object(), object()
        self.world = 0  # Producer provenance, not this cache, controls adoption.
        self._types = {name: index for index, name in enumerate(("Country", "State", "DiplomaticPlayType",
            "CountryDefinition", "WarGoalDraft", "DiplomaticDemand", "Sway", "SwayOffer"), 1)}
        self.adapters = {"data": "data-A", "commands": "commands-A", "console": "console-A"}
        self.diagnostics, self.queries, self.performs, self.remembered = [], [], [], []
        self.country_reads = 0
        self.executor = SerializedQueryExecutor(timeout=1)
        self.owner_pool = self.executor.owner_pool
        self.gate = None
        self.entered = asyncio.Event()

    async def country_id(self):
        self.country_reads += 1
        return 99

    async def type_id(self, name):
        return self._types[name]

    async def perform(self, label, operation):
        self.performs.append(label)
        return await self.executor.perform(label, operation)

    async def query(self, label, factory, *, owner=None):
        query = factory()
        self.queries.append((label, query))
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        return query.result

    def _remember(self, query):
        self.remembered.append(query)


class API(p.PlayOptionsMixin):
    def __init__(self, context):
        self._ctx = context


class CommandOwner:
    def __init__(self, fixture):
        self.fixture = fixture
        self.world = fixture.generation_world
        self.command = 0xB000
        self.pending, self.attempt = None, None

    def validate(self, *, capacity):
        self.fixture.validations += 1
        self.attempt = NS(outcome="unknown" if self.fixture.unknown_validation else "owned")
        return NS(result=self.fixture.validation)

    def submit(self, *, flags):
        assert flags == 4
        self.fixture.submissions += 1
        if self.fixture.submit_error:
            raise self.fixture.submit_error
        self.command = 0
        self.attempt = NS(outcome="unknown" if self.fixture.unknown_submit else "consumed")
        return NS(result=self.fixture.submission)

    def destroy(self):
        self.fixture.destructions += 1
        self.command = 0
        self.attempt = NS(outcome="destroyed")
        return NS(result=NS(error=0, command=0))


class Fixture:
    generation_world = 0x5000

    def __init__(self, context):
        self.context = context
        fields = [*p._DEFINITION, *p._DRAFT, *p._OFFER]
        for kind in ("Country", "State", "CountryWithDefinition"):
            fields.extend(p._type_fields(kind))
        self.kinds = {field.expression: field.kind for field in fields}
        self.next_handle = 100
        self.handles, self.bound, self.lists = {}, {}, {}
        self.potential = [("DiplomaticPlayType", "first", "owned"), ("DiplomaticPlayType", "second", "owned")]
        self.nonpotential = [("DiplomaticPlayType", "other", "owned")]
        self.definition_mode = self.promoted_mode = "owned"
        self.lookup_mode = "owned"
        self.native_lookup_error = 0
        self.events, self.pages, self.projections, self.released, self.adapters_used = [], [], [], [], []
        self.producers = self.clones = self.validations = self.submissions = self.destructions = 0
        self.validation = NS(error=0, validated=True, reason="original native rules")
        self.submission = NS(error=0, accepted=True)
        self.unknown_validation = self.unknown_submit = False
        self.submit_error = None
        self.copy_error = None
        self.copy_native_error = 0
        self.warning = None
        self.selected_bindings = self.selected_expression = None
        self.selected_type_identity = None

    def value(self, value, kind, *, adapter="data-A", action=data.Action.EVALUATE, error=0):
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = value
        return NS(result=NS(error=error, action=action, value_handle=handle, value=value, kind=kind), adapter=adapter)

    def wrapper(self, named_type, identity, mode="owned", *, adapter="data-A", action=data.Action.LIST_ELEMENT):
        handle = self.next_handle
        rtti = (".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@" if mode == "owned" else
                ".?AU?$SAnyType@PEAVCData" + named_type + "@@@PdxAnyImpl@@")
        obj = data.NativeObject(0x10000 + handle * 0x100, self.context._types[named_type], handle, rtti)
        query = self.value(obj, data.Kind.OBJECT, adapter=adapter, action=action)
        query.result.native_type_name = rtti
        query.result.type_id = self.context._types[named_type]
        self.bound[handle] = named_type, identity
        return query

    def source(self, rows, adapter):
        query = self.value(data.NativeList(0, 0, b"", "fixture"), data.Kind.LIST, adapter=adapter)
        self.lists[query.result.value_handle] = list(rows)
        return query

    def evaluate(self, native, expression, bindings, **kwargs):
        adapter = kwargs["adapter"]
        self.events.append((expression, tuple(bindings)))
        self.adapters_used.append((expression, adapter))
        if expression in ("Country.GetPotentialDiploPlays", "State.GetPotentialDiploPlays"):
            return self.source(self.potential, adapter)
        if expression in ("Country.GetNonPotentialDiploPlays", "State.GetNonPotentialDiploPlays"):
            return self.source(self.nonpotential, adapter)
        if expression == "Country.GetDefinition":
            country, = bindings
            assert country.domain == "Country"
            return self.wrapper("CountryDefinition", country.object, self.definition_mode, adapter=adapter, action=data.Action.EVALUATE)
        if expression in ("DiplomaticDemand.GetDemandedWarGoal", "SwayOffer.GetOfferedWarGoal"):
            assert bindings[0].object in self.handles
            return self.wrapper("WarGoalDraft", 0, self.promoted_mode, adapter=adapter, action=data.Action.EVALUATE)
        if expression == "Sway.GetSwayOffer":
            assert bindings[0].object in self.handles
            return self.wrapper("SwayOffer", 0, self.promoted_mode, adapter=adapter, action=data.Action.EVALUATE)
        if ".GetStartCommand" in expression:
            self.producers += 1
            self.selected_expression, self.selected_bindings = expression, tuple(bindings)
            for binding in bindings:
                if binding.retained:
                    assert binding.object in self.handles
                    if self.bound[binding.object][0] == "DiplomaticPlayType":
                        self.selected_type_identity = self.bound[binding.object][1]
            if self.native_lookup_error:
                return self.value(None, data.Kind.EMPTY, adapter=adapter, error=self.native_lookup_error)
            return self.value(data.NativeDataCommand(0x8000, 0x8010, 0x8018, 0, "CDataCommand", self.generation_world), data.Kind.COMMAND, adapter=adapter)
        if expression.startswith("GetDiplomaticPlayType('"):
            if self.native_lookup_error:
                return self.value(None, data.Kind.EMPTY, adapter=adapter, error=self.native_lookup_error)
            key = expression.split("'")[1]
            return self.wrapper("DiplomaticPlayType", key, self.lookup_mode, adapter=adapter, action=data.Action.EVALUATE)
        raise AssertionError("unexpected expression " + expression)

    def list_count(self, native, handle, **kwargs):
        assert handle in self.handles
        return NS(result=NS(error=0, total=len(self.lists[handle])), adapter=kwargs["adapter"])

    def list_element(self, native, handle, index, **kwargs):
        assert handle in self.handles
        return self.wrapper(*self.lists[handle][index], adapter=kwargs["adapter"])

    def project(self, native, fields, binding_rows, **kwargs):
        self.adapters_used.append(("project", kwargs["adapter"]))
        if self.copy_error:
            raise self.copy_error
        assert len(fields) <= 32 and len(binding_rows) <= 64
        self.projections.append(NS(fields=fields, bindings=binding_rows, adapter=kwargs["adapter"]))
        cells = []
        for row, bindings in enumerate(binding_rows):
            for binding in bindings:
                if binding.retained:
                    assert binding.object in self.handles
            for index, expression in enumerate(fields):
                kind = self.kinds[expression]
                value = "" if kind == data.Kind.STRING else False if kind == data.Kind.BOOL else 0
                if expression == "DiplomaticPlayType.GetName":
                    value = "#bold Duplicate#!"
                elif "GetStartDesc" in expression:
                    value = "#bold Original start description#!"
                elif "GetStartTooltip" in expression:
                    value = "Original target tooltip"
                elif expression == "CountryDefinition.GetName":
                    value = "Original definition"
                elif expression == "CountryDefinition.IsValid":
                    value = True
                elif expression == "WarGoalDraft.GetInfamyAmount":
                    value = 123456
                elif expression == "WarGoalDraft.GetTarget.GetID":
                    value = -2147483646
                elif expression == "SwayOffer.RequiresAnyInput":
                    value = True
                snapshot = value.encode() if isinstance(value, str) else b""
                bits = 0 if isinstance(value, str) else int(value) & 0xffffffffffffffff
                cells.append(data.ProjectionCell(row, index, 0, kind, 8, bits, snapshot, len(snapshot)))
        page = data.ProjectionPage(self.copy_native_error, self.generation_world, len(fields), 0, 11,
            len(binding_rows), True, len(binding_rows), tuple(cells))
        self.pages.append(page)
        return NS(result=page, cleanup_error=self.warning, adapter=kwargs["adapter"])

    def command_value(self, native, handle, **kwargs):
        return NS(result=NS(error=0, action=data.Action.COMMAND_VALUE, value=self.handles[handle]), adapter=kwargs["adapter"])

    def clone(self, native, action, **kwargs):
        assert action == commands.Action.CLONE
        assert kwargs["world"] == self.generation_world
        self.clones += 1
        owner = CommandOwner(self)
        return NS(result=NS(error=0, cloned_command=owner.command, owner=owner))

    def release(self, native, handle, **kwargs):
        self.released.append(handle)
        self.adapters_used.append(("release", kwargs["adapter"]))
        del self.handles[handle]
        return NS(result=NS(error=0, action=data.Action.RELEASE, value_handle=0), adapter=kwargs["adapter"])


class PlayOptionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = Context()
        self.fixture = Fixture(self.context)
        self.api = API(self.context)
        self.patchers = [patch.object(data, name, getattr(self.fixture, name)) for name in
            ("evaluate", "list_count", "list_element", "command_value", "release")]
        self.patchers.extend((patch.object(data, "project_page", self.fixture.project),
            patch.object(commands, "request", self.fixture.clone),
            patch.object(commands.NativeCommand, "from_result", lambda native, result, **kwargs: result.owner)))
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def runs(self):
        return [owner for owner in self.context.owner_pool.snapshot() if isinstance(owner, run_mod.Run)]

    async def parent_choice(self, named_type):
        run = run_mod.Run(self.context, "fixture_parent", namespace="diplomacy")
        value = await run.capture("fixture.parent", lambda: self.fixture.wrapper(named_type, 0, action=data.Action.EVALUATE))
        catalog = OwnedChoices(run)
        choice = catalog.add(value, named_type, data.ObjectBinding(self.context._types[named_type], value.value_handle, retained=True), {})
        return catalog, choice

    async def test_country_candidates_use_both_original_sources_and_one_target_country(self):
        catalog = await self.api.play_options(0)
        self.assertEqual(len(catalog), 3)
        self.assertEqual([expression for expression, _ in self.fixture.events],
            ["Country.GetPotentialDiploPlays", "Country.GetNonPotentialDiploPlays"])
        self.assertTrue(all(choice["name"] == "Duplicate" for choice in catalog))
        self.assertEqual(catalog[0]["name_raw"], "#bold Duplicate#!")
        self.assertFalse(catalog[0]["epic"])
        self.assertFalse(catalog[0]["eligibility_checked"])
        self.assertNotIn("id", catalog[0]); self.assertNotIn("key", catalog[0])
        self.assertTrue(catalog.metadata["result"]["read_complete"])
        self.assertFalse(catalog.metadata["result"]["complete"])
        self.assertEqual(catalog.metadata["target_id"], 0)
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (0, 0, 0))
        for call in self.fixture.projections:
            self.assertIn("DiplomaticPlayType.GetStartDescCountry(GetPlayer.Self,Country.Self)", call.fields)
            for row in call.bindings:
                country = [binding for binding in row if binding.domain == "Country"]
                self.assertEqual(len(country), 1)
                self.assertEqual(country[0].object, 0)
        self.assertEqual(self.context.country_reads, 0)
        await catalog.close(); self.assertEqual(self.fixture.handles, {})

    async def test_state_variant_uses_state_source_and_original_getplayer_expression(self):
        catalog = await self.api.play_options(target_state_id=0, include_nonpotential=False)
        self.assertEqual([expression for expression, _ in self.fixture.events], ["State.GetPotentialDiploPlays"])
        call = self.fixture.projections[0]
        self.assertIn("DiplomaticPlayType.GetStartTooltipState(GetPlayer,State.Self)", call.fields)
        self.assertTrue(all(any(binding.domain == "State" and binding.object == 0 for binding in row) for row in call.bindings))
        self.assertFalse(any(binding.domain == "Country" for row in call.bindings for binding in row))
        result = await self.api.start_play_native(catalog[0])
        self.assertEqual(result["expression"], "DiplomaticPlayType.GetStartCommandState(GetPlayer,State.Self)")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["target_kind"], "State")
        await catalog.close(); self.assertEqual(self.fixture.handles, {})

    async def test_real_definition_key_lookup_does_not_parse_localized_name_or_ordinal(self):
        catalog = await self.api.play_type_choices("dp_independence", 0)
        self.assertEqual(len(catalog), 1)
        self.assertEqual(self.fixture.events[0][0], "GetDiplomaticPlayType('dp_independence')")
        self.assertEqual(catalog.metadata["requested_key"], "dp_independence")
        self.assertNotIn("key", catalog[0])
        self.assertEqual(self.fixture.producers, 0)
        await catalog.close()
        for key in (0, "dp_x').Execute()", "bad\\key", "bad\nkey", ""):
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.api.play_type_choices(key, 0)

    async def test_definition_key_is_passed_verbatim_without_display_name_fallback(self):
        catalog = await self.api.play_type_choices("mod-自定义.key", 0)
        self.assertEqual(self.fixture.events[0][0], "GetDiplomaticPlayType('mod-自定义.key')")
        await catalog.close()
        self.fixture.events.clear()
        self.fixture.native_lookup_error = 1168
        missing = await self.api.play_type_choices("Localized looking name", 0)
        self.assertEqual([expression for expression, _ in self.fixture.events],
            ["GetDiplomaticPlayType('Localized looking name')"])
        self.assertEqual(len(missing), 0)
        self.assertEqual(missing.metadata["result"]["reasons"][0]["native_result"].error, 1168)
        await missing.close()
        self.assertEqual(self.fixture.handles, {})

    async def test_country_definition_variant_uses_actual_definition_and_pins_parent(self):
        definitions = await self.api.country_definition_choices(9)
        options = await self.api.play_options(2, definition_choice=definitions[0])
        with self.assertRaises(ChoicesBusyError):
            await definitions.close()
        fields = self.fixture.projections[-1].fields
        self.assertIn("DiplomaticPlayType.GetStartDescCountryWithDefinition(GetPlayer.Self,Country.Self,CountryDefinition.Self)", fields)
        result = await self.api.start_play_native(options[0])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["target_kind"], "CountryWithDefinition")
        self.assertEqual(result["expression"], "DiplomaticPlayType.GetStartCommandCountryWithDefinition(GetPlayer.Self,Country.Self,CountryDefinition.Self)")
        countries = [binding for binding in self.fixture.selected_bindings if binding.domain == "Country"]
        self.assertEqual([binding.object for binding in countries], [2])
        definition = next(binding for binding in self.fixture.selected_bindings if binding.type_id == self.context._types["CountryDefinition"])
        self.assertEqual(definition.object, definitions[0]._binding.object)
        await options.close(); await definitions.close()
        self.assertEqual(self.fixture.handles, {})

    async def test_raw_type_or_definition_is_reported_without_fabricating_choice(self):
        self.fixture.potential = [("DiplomaticPlayType", "raw", "raw")]
        self.fixture.nonpotential = []
        options = await self.api.play_options(0)
        self.assertEqual(len(options), 0)
        self.assertEqual(len(options.metadata["result"]["rejected_rows"]), 1)
        self.assertEqual(options.metadata["result"]["status"], "unavailable")
        await options.close()
        self.fixture.definition_mode = "raw"
        definitions = await self.api.country_definition_choices(0)
        self.assertEqual(len(definitions), 0)
        self.assertEqual(len(definitions.metadata["result"]["rejected_rows"]), 1)
        self.assertEqual(self.fixture.producers, 0)
        await definitions.close(); self.assertEqual(self.fixture.handles, {})

    async def test_empty_sources_have_real_zero_counts_but_semantic_gaps_remain_explicit(self):
        self.fixture.potential = self.fixture.nonpotential = []
        options = await self.api.play_options(0)
        self.assertEqual(len(options), 0)
        self.assertTrue(options.metadata["result"]["read_complete"])
        self.assertFalse(options.metadata["result"]["complete"])
        self.assertEqual([source["count_result"].total for source in options.metadata["result"]["sources"]], [0, 0])
        self.assertTrue(options.metadata["result"]["limitations"])
        await options.close()

    async def test_selected_owned_type_uses_exact_binding_and_one_validation_submission(self):
        options = await self.api.play_options(0)
        options[1]["source"]["source_index"] = 99
        result = await self.api.start_play_native(options[1])
        self.assertTrue(result["accepted"])
        self.assertEqual(self.fixture.selected_type_identity, "second")
        self.assertEqual((self.fixture.producers, self.fixture.clones, self.fixture.validations, self.fixture.submissions), (1, 1, 1, 1))
        self.assertIs(result["validation"], self.fixture.validation)
        self.assertIs(result["submission"], self.fixture.submission)
        self.assertFalse(result["mode_known"])
        self.assertIsNone(result["start_mode"])
        self.assertFalse(result["confirmation_used"])
        self.assertFalse(result["completed"])
        self.assertFalse(result["contract_complete"])
        self.assertIsNone(result["effect_observed"])
        self.assertIn("not proven", result["mode_reason"])
        self.assertEqual(len(self.runs()), 1)
        await options.close(); self.assertEqual(self.fixture.handles, {}); self.assertEqual(self.runs(), [])

    async def test_native_key_command_is_chained_and_does_not_require_fabricated_type_wrapper(self):
        self.fixture.lookup_mode = "raw"
        result = await self.api.start_play_native("dp_independence", target_country_id=0)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["expression"], "GetDiplomaticPlayType('dp_independence').GetStartCommandCountry(GetPlayer.Self,Country.Self)")
        self.assertEqual(len(self.fixture.selected_bindings), 1)
        self.assertEqual(self.fixture.selected_bindings[0].domain, "Country")
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (1, 1, 1))
        self.assertEqual(self.fixture.handles, {})
        self.assertEqual(self.runs(), [])

    async def test_missing_native_key_retains_native_error_without_fallback(self):
        self.fixture.native_lookup_error = 1168
        result = await self.api.start_play_native("dp_missing", target_country_id=0)
        self.assertEqual(result["native_error"], 1168)
        self.assertEqual(result["status"], "native_error")
        self.assertIsNone(result["accepted"])
        self.assertEqual((self.fixture.validations, self.fixture.submissions), (0, 0))
        self.assertEqual(self.fixture.handles, {})

    async def test_native_rejection_keeps_original_reason_and_never_submits(self):
        options = await self.api.play_options(0)
        self.fixture.validation.validated = False
        self.fixture.validation.reason = "#bold Original native rejection#!"
        result = await self.api.start_play_native(options[0])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "#bold Original native rejection#!")
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (1, 1, 0))
        await options.close(); self.assertEqual(self.fixture.handles, {})

    async def test_partial_native_validation_reason_is_not_repeated(self):
        self.fixture.validation.error = 122
        result = await self.api.start_play_native("dp_independence", target_country_id=0)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["validation"].error, 122)
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (1, 1, 1))

    async def test_unknown_submission_keeps_original_choice_borrowed(self):
        options = await self.api.play_options(0)
        self.fixture.unknown_submit = True
        result = await self.api.start_play_native(options[0])
        self.assertTrue(result["accepted"])
        self.assertFalse(result["ownership_settled"])
        self.assertIs(result["submission"], self.fixture.submission)
        with self.assertRaises(ChoicesBusyError):
            await options.close()
        self.assertEqual(self.fixture.submissions, 1)

    async def test_uncertain_dispatch_is_not_replayed_or_cleaned(self):
        options = await self.api.play_options(0)
        self.fixture.submit_error = OSError("unknown dispatch")
        with self.assertRaisesRegex(OSError, "unknown dispatch"):
            await self.api.start_play_native(options[0])
        with self.assertRaises(ChoicesBusyError):
            await options.close()
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (1, 1, 1))
        self.assertTrue(self.fixture.handles)

    async def test_pending_action_is_one_owned_operation_and_never_reselects_by_index(self):
        options = await self.api.play_options(0)
        self.context.entered.clear(); self.context.executor.timeout = 0; self.context.gate = asyncio.Event()
        pending = await self.api.start_play_native(options[1])
        self.assertIsInstance(pending, PendingOperation)
        await self.context.entered.wait()
        with self.assertRaises(ChoicesBusyError):
            await options.close()
        self.context.gate.set()
        result = await pending.resume(timeout=1)
        self.assertTrue(result["accepted"])
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (1, 1, 1))
        self.assertEqual([name for name in self.context.performs if "start_play_native" in name], ["diplomacy.start_play_native"])
        await options.close()

    async def test_original_data_adapter_survives_context_adapter_change(self):
        options = await self.api.play_options(0)
        self.context.adapters["data"] = "new-wrong-registry"
        result = await self.api.start_play_native(options[0])
        self.assertTrue(result["accepted"])
        await options.close()
        self.assertTrue(all(adapter == "data-A" for _, adapter in self.fixture.adapters_used))

    async def test_copy_error_is_retained_beside_actual_owned_candidate(self):
        self.fixture.copy_native_error = 13
        options = await self.api.play_options(0)
        self.assertEqual(len(options), 3)
        self.assertEqual(options[0]["name"], "Duplicate")
        self.assertFalse(options.metadata["result"]["read_complete"])
        self.assertEqual(options.metadata["result"]["native_pages"][0].error, 13)
        self.assertFalse(getattr(options.owner, "retain_unknown", False))
        await options.close(); self.assertEqual(self.fixture.handles, {})

    async def test_descriptor_cleanup_warning_does_not_hide_options(self):
        self.fixture.warning = OSError("copied buffer cleanup")
        options = await self.api.play_options(0)
        self.assertEqual(len(options), 3)
        self.assertTrue(options.metadata["result"]["read_complete"])
        self.assertEqual(options.metadata["result"]["cleanup_warnings"], [self.fixture.warning])
        self.assertEqual(len(self.context.remembered), 1)
        await options.close()

    async def test_existing_demand_draft_keeps_parent_and_reports_exact_costs(self):
        parents, parent = await self.parent_choice("DiplomaticDemand")
        drafts = await self.api.war_goal_draft_choices(parent)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["infamy"], Decimal("1.23456"))
        self.assertEqual(drafts[0]["maneuvers_cost"], 0)
        self.assertIs(drafts[0]["incomplete_selection"], False)
        self.assertEqual(drafts[0]["target_country_id"], 0x80000002)
        self.assertFalse(drafts.metadata["result"]["complete"])
        with self.assertRaises(ChoicesBusyError):
            await parents.close()
        await drafts.close(); await parents.close(); self.assertEqual(self.fixture.handles, {})

    async def test_existing_sway_offer_and_offered_draft_chain_has_no_fabricated_panel(self):
        parents, sway = await self.parent_choice("Sway")
        offers = await self.api.sway_offer_choices(sway)
        self.assertTrue(offers[0]["requires_input"])
        self.assertFalse(offers[0]["takes_effect_immediately"])
        drafts = await self.api.war_goal_draft_choices(offers[0])
        self.assertEqual(len(drafts), 1)
        with self.assertRaises(ChoicesBusyError):
            await offers.close()
        with self.assertRaises(ChoicesBusyError):
            await parents.close()
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (0, 0, 0))
        await drafts.close(); await offers.close(); await parents.close()
        self.assertEqual(self.fixture.handles, {})

    async def test_raw_existing_draft_is_not_retagged_as_owned(self):
        parents, parent = await self.parent_choice("DiplomaticDemand")
        self.fixture.promoted_mode = "raw"
        drafts = await self.api.war_goal_draft_choices(parent)
        self.assertEqual(len(drafts), 0)
        self.assertEqual(len(drafts.metadata["result"]["rejected_rows"]), 1)
        self.assertFalse(getattr(drafts.owner, "retain_unknown", False))
        await drafts.close(); await parents.close()

    async def test_native_start_does_not_call_confirmation_popup_or_claim_force_mode(self):
        result = await self.api.start_play_native("dp_independence", target_country_id=0)
        expressions = [expression for expression, _ in self.fixture.events]
        self.assertFalse(any("ShowConfirmation" in expression or "DiplomaticPlayConfirmation" in expression for expression in expressions))
        self.assertIsNone(result["start_mode"])
        self.assertFalse(result["mode_known"])
        with self.assertRaises(TypeError):
            await self.api.start_play_native("dp_independence", target_country_id=0, force=True)

    async def test_invalid_target_overrides_and_definition_types_fail_before_native_work(self):
        for kwargs in ({}, {"target_country_id": 0, "target_state_id": 0}, {"target_state_id": True},
                {"target_state_id": -1}, {"target_state_id": 0xffffffff}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await self.api.play_options(**kwargs)
        with self.assertRaises(TypeError):
            await self.api.play_options(0, definition_choice={"id": 0})
        self.assertEqual(self.context.queries, [])
        options = await self.api.play_options(0)
        before = len(self.context.queries)
        with self.assertRaises(ValueError):
            await self.api.start_play_native(options[0], target_country_id=1)
        self.assertEqual(len(self.context.queries), before)
        await options.close()

    async def test_65_actual_type_bindings_use_two_bounded_copy_pages(self):
        self.fixture.potential = [("DiplomaticPlayType", str(index), "owned") for index in range(65)]
        self.fixture.nonpotential = []
        options = await self.api.play_options(0)
        self.assertEqual(len(options), 65)
        self.assertEqual([len(call.bindings) for call in self.fixture.projections], [64, 1])
        self.assertEqual((self.fixture.producers, self.fixture.validations), (0, 0))
        await options.close(); self.assertEqual(self.fixture.handles, {})


if __name__ == "__main__":
    unittest.main()
