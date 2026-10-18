"""CPU-only original-provider character/exile read contracts; no game or DLL.

The real page composition runs against copied native fixtures. Tests do not
claim exact-version native invocation or interaction command acceptance.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, localcontext
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data
from pdx_runtime.query_executor import PendingOperation, SerializedQueryExecutor
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.politics import characters as c


def _retained_runs(context):
    return [owner for owner in context.owner_pool.snapshot()
            if isinstance(owner, run_mod.Run) and owner.ctx is context]


class Fixture:
    def __init__(self):
        fields = [*c._EXILE_SUMMARY, *c._EXILE_FILTERS, *c._ROSTER_SUMMARY, *c._MOVEMENTS]
        for prefix in ("Character", "Country.GetRuler", "Country.GetHeir", "InterestGroup.GetLeader", "Company.GetExecutive"):
            fields.extend(c._character_fields(prefix, exile=prefix == "Character"))
            fields.extend(c._trait_fields(prefix))
            fields.extend(c._interaction_fields(prefix))
        fields.extend((c._text("company_name", "Company.GetNameNoFormatting"),))
        self.kinds = {field.expression: field.kind for field in fields}
        self.summary, self.sources, self.errors, self.page_errors = {}, {}, {}, {}
        self.count_invalid = set()
        self.calls, self.pages = [], []
        self.warning = None

    @staticmethod
    def key(source, steps=()):
        return source, tuple((step.source_expression, step.index) for step in steps)

    def rows(self, source, rows, steps=()):
        self.sources[self.key(source, steps)] = rows

    def project(self, native, fields, binding_rows, *, source_expression=None,
                start=0, limit=64, steps=(), source_arguments=(), **kwargs):
        assert 1 <= len(fields) <= 32 and limit == 64
        assert len(binding_rows) == 1 and len(binding_rows[0]) == 1
        assert binding_rows[0][0].domain == "Country" and not binding_rows[0][0].retained
        self.calls.append(NS(fields=fields, source=source_expression, steps=steps,
            bindings=binding_rows, start=start, limit=limit, source_arguments=source_arguments))
        key = self.key(source_expression, steps)
        records = [self.summary] if source_expression is None else self.sources.get(key, [])
        selected = records[start:start + limit]
        cells = []
        for row, record in enumerate(selected):
            for index, expression in enumerate(fields):
                kind = self.kinds[expression]
                value = record.get(expression)
                total, error = None, self.errors.get((source_expression, expression), 0)
                if isinstance(value, dict):
                    kind, total, error = value.get("kind", kind), value.get("total"), value.get("error", error)
                    value = value.get("value")
                if value is None:
                    value = "" if kind == data.Kind.STRING else False if kind == data.Kind.BOOL else 0
                snapshot = value.encode() if isinstance(value, str) else b""
                bits = value.bits if isinstance(value, data.FixedPoint) else 0 if isinstance(value, str) else int(value)
                cells.append(data.ProjectionCell(row, index, error, kind, 9,
                    bits & 0xffffffffffffffff, snapshot, len(snapshot) if total is None else total,
                    binding_mode=2, binding_kind=data.Kind.OBJECT,
                    binding_native_type_name="original-character-provider-fixture"))
        page = data.ProjectionPage(self.page_errors.get(source_expression, 0), 0x5000, len(fields), start,
            11, len(records), source_expression not in self.count_invalid, len(selected), tuple(cells))
        self.pages.append(page)
        return NS(result=page, cleanup_error=self.warning)


class Context:
    def __init__(self):
        self._types = {"Country": 7}
        self.native, self.process = object(), object()
        self.adapters = {"data": "fixture-data", "console": "fixture-console"}
        self.performs, self.queries, self.diagnostics, self.remembered = [], [], [], []
        self.country_reads = 0
        self.executor = SerializedQueryExecutor(timeout=1)
        self.owner_pool = self.executor.owner_pool
        self.gate = None
        self.entered = asyncio.Event()

    async def country_id(self):
        self.country_reads += 1
        return 0 if self.country_reads == 1 else 0x80000002

    async def type_id(self, name):
        raise AssertionError("No extra type/world probe is needed for the cached Country type")

    async def perform(self, label, operation):
        self.performs.append(label)
        return await self.executor.perform(label, operation)

    async def query(self, label, factory):
        query = factory()
        self.queries.append((label, query))
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        return query.result

    def _remember(self, query):
        self.remembered.append(query)


class API(c.CharacterMixin):
    def __init__(self, context):
        self._ctx = context


class ChoiceContext(Context):
    def __init__(self):
        super().__init__()
        self.world = 0
        self._types.update(Character=8, CharacterInteraction=9, InterestGroup=10, PoliticalMovement=11, Company=12)

    async def type_id(self, name):
        return self._types[name]

    async def query(self, label, factory, *, owner=None):
        return await super().query(label, factory)


class ChoiceFixture(Fixture):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.next_handle = 100
        self.handles, self.bound, self.lists = {}, {}, {}
        self.character_rows = [("Character", 0x80000002, "owned"), ("Character", 0, "owned")]
        self.interaction_rows = [("CharacterInteraction", 51, "owned"), ("CharacterInteraction", 52, "owned")]
        self.events, self.released, self.adapters_used = [], [], []
        self.conditions, self.invocations = 0, 0
        self.valid, self.condition_error = True, 0
        self.invocation_error, self.invoked, self.accepted = 0, True, True
        self.transport_error = None
        self.copy_error = None
        self.copy_native_error = 0
        self.last_invoked = None
        self.popularity = 0

    def value(self, value, kind, *, action=data.Action.EVALUATE, adapter="fixture-data", error=0):
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = value
        return NS(result=NS(error=error, action=action, value_handle=handle, value=value, kind=kind), adapter=adapter)

    def wrapper(self, named_type, identity, mode, *, adapter, action=data.Action.LIST_ELEMENT):
        handle = self.next_handle
        spelling = (".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@" if mode == "owned" else
                    ".?AU?$SAnyType@PEAVCData" + named_type + "@@@PdxAnyImpl@@" if mode == "raw" else
                    ".?AU?$SAnyType@VCData" + named_type + "@@@PdxAnyImpl@@")
        value = data.NativeObject(0x9000 + handle * 0x100, self.context._types[named_type], handle, spelling)
        query = self.value(value, data.Kind.OBJECT, adapter=adapter, action=action)
        query.result.native_type_name = spelling
        query.result.type_id = self.context._types[named_type]
        self.bound[handle] = named_type, identity
        return query

    def source(self, rows, *, adapter):
        query = self.value(data.NativeList(0, 0, b"", "fixture"), data.Kind.LIST, adapter=adapter)
        self.lists[query.result.value_handle] = list(rows)
        return query

    def evaluate(self, native, expression, bindings, **kwargs):
        adapter = kwargs["adapter"]
        self.adapters_used.append((expression, adapter))
        self.events.append((expression, tuple(bindings)))
        if expression == "GetExilePool.GetExiles":
            return self.source(self.character_rows, adapter=adapter)
        if expression in ("Country.GetRuler", "Country.GetHeir"):
            return self.wrapper("Character", 0x80000002, "owned", adapter=adapter, action=data.Action.EVALUATE)
        for source, kind in (("Country.AccessAllInterestGroups", "InterestGroup"),
                             ("Country.GetCompanies", "Company"), ("Country.AccessPoliticalMovements", "PoliticalMovement")):
            if expression == source:
                return self.source([(kind, 5, "owned")], adapter=adapter)
        if expression in ("InterestGroup.GetLeader", "Company.GetExecutive"):
            assert bindings[0].object in self.handles and bindings[0].retained
            return self.wrapper("Character", 0x80000002, "owned", adapter=adapter, action=data.Action.EVALUATE)
        if expression == "PoliticalMovement.GetSupportingCharacters":
            assert bindings[0].object in self.handles
            return self.source(self.character_rows, adapter=adapter)
        if expression == "Character.GetInteractions":
            assert bindings[0].object in self.handles and self.bound[bindings[0].object][0] == "Character"
            return self.source(self.interaction_rows, adapter=adapter)
        if expression == "CharacterInteraction.IsCommandValid(Character.Self)":
            self.conditions += 1
            assert all(binding.object in self.handles for binding in bindings)
            return self.value(self.valid, data.Kind.BOOL, adapter=adapter, error=self.condition_error)
        raise AssertionError("Unexpected expression " + expression)

    def list_count(self, native, handle, **kwargs):
        assert handle in self.handles
        self.adapters_used.append(("count", kwargs["adapter"]))
        return NS(result=NS(error=0, total=len(self.lists[handle])), adapter=kwargs["adapter"])

    def list_element(self, native, handle, index, **kwargs):
        assert handle in self.handles
        self.adapters_used.append(("element", kwargs["adapter"]))
        return self.wrapper(*self.lists[handle][index], adapter=kwargs["adapter"])

    def release(self, native, handle, **kwargs):
        self.released.append(handle)
        self.adapters_used.append(("release", kwargs["adapter"]))
        del self.handles[handle]
        return NS(result=NS(error=0, action=data.Action.RELEASE, value_handle=0), adapter=kwargs["adapter"])

    def invoke_property_action(self, native, expression, bindings, **kwargs):
        self.invocations += 1
        self.adapters_used.append((expression, kwargs["adapter"]))
        assert expression == "CharacterInteraction.ExecuteCommand(Character.Self)"
        assert all(binding.object in self.handles for binding in bindings)
        self.last_invoked = tuple(self.bound[binding.object] for binding in bindings)
        if self.transport_error:
            raise self.transport_error
        self.popularity += 100000
        return NS(result=NS(error=self.invocation_error, action=data.Action.INVOKE_ACTION,
            value_handle=0, invoked=self.invoked, accepted=self.accepted), adapter=kwargs["adapter"])

    def project(self, native, fields, binding_rows, **kwargs):
        self.adapters_used.append(("project", kwargs["adapter"]))
        if self.copy_error:
            raise self.copy_error
        assert 1 <= len(fields) <= 32 and len(binding_rows) <= 64
        self.calls.append(NS(fields=fields, bindings=binding_rows, adapter=kwargs["adapter"]))
        cells = []
        for row, bindings in enumerate(binding_rows):
            by_type = {binding.type_id: binding for binding in bindings}
            char = by_type[self.context._types["Character"]]
            assert char.retained and char.object in self.handles
            identity = self.bound[char.object][1]
            for field, expression in enumerate(fields):
                kind = self.kinds[expression]
                value = "" if kind == data.Kind.STRING else False if kind == data.Kind.BOOL else 0
                if expression == "Character.GetID":
                    value = identity if identity < 0x80000000 else identity - 0x100000000
                elif expression == "Character.GetFullNameNoFormatting":
                    value = "Duplicate character"
                elif expression == "Character.GetPopularity":
                    value = self.popularity
                elif expression.startswith("CharacterInteraction."):
                    interaction = by_type[self.context._types["CharacterInteraction"]]
                    assert interaction.retained and interaction.object in self.handles
                    if kind == data.Kind.STRING:
                        value = "Duplicate interaction" if expression.endswith("GetName") else "Original descriptor"
                snapshot = value.encode() if isinstance(value, str) else b""
                bits = 0 if isinstance(value, str) else int(value) & 0xffffffffffffffff
                cells.append(data.ProjectionCell(row, field, 0, kind, 99, bits, snapshot, len(snapshot)))
        page = data.ProjectionPage(self.copy_native_error, 0x5000, len(fields), 0, 11,
            len(binding_rows), True, len(binding_rows), tuple(cells))
        self.pages.append(page)
        return NS(result=page, cleanup_error=self.warning, adapter=kwargs["adapter"])


class CharacterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture, self.context = Fixture(), Context()
        self.api = API(self.context)
        self.patcher = patch.object(data, "project_page", side_effect=self.fixture.project)
        self.project = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def assert_native_pages_preserved(self, result):
        self.assertCountEqual([id(page) for page in result["native_pages"]], [id(page) for page in self.fixture.pages])

    def assert_pinned(self, country_id=0):
        self.assertTrue(all(call.bindings[0][0].object == country_id for call in self.fixture.calls))
        self.assertTrue(all(call.bindings[0][0].domain == "Country" for call in self.fixture.calls))

    async def test_exiles_use_original_global_source_and_exact_country_acceptance(self):
        self.fixture.summary.update({"GetExilePool.GetNumAllExiles": 1,
            "GetExilePool.GetNumInviteableExiles": 0,
            "GetVariableSystem.Exists('politics_overview_exile_pool_show_available')": True,
            "Country.GetEmptyAgitatorSlots": 0})
        self.fixture.rows("GetExilePool.GetExiles", [{"Character.GetID": -2147483646,
            "Character.GetFullNameNoFormatting": "#bold 原生流亡者#!", "Character.GetAge": 0,
            "Character.GetHomeCountry.GetID": -2147483645,
            "Character.GetPopularity": data.FixedPoint(123456789), "Character.GetProminence": data.FixedPoint(0),
            "Character.GetAcceptanceValueIn(Country.Self)": 0,
            "Character.WillJoinMovementIfInvited(Country.Self)": False,
            "Character.WillStartMovementIfInvited(Country.Self)": True}])
        with localcontext() as precision:
            precision.prec = 2
            result = await self.api.exiles()
        row, = result["exiles"]["rows"]
        self.assertEqual((row["id"], row["home_country_id"]), (0x80000002, 0x80000003))
        self.assertEqual((row["name"], row["name_raw"]), ("原生流亡者", "#bold 原生流亡者#!"))
        self.assertEqual(row["popularity"], Decimal("1234.56789"))
        self.assertEqual(row["prominence"], Decimal(0))
        self.assertEqual(row["acceptance"], 0)
        self.assertIs(row["will_join_movement_if_invited"], False)
        self.assertIs(row["will_start_movement_if_invited"], True)
        self.assertTrue(result["summary"]["ui_only_show_available"])
        self.assertTrue(result["all_count_matches_read_count"])
        self.assertTrue(result["complete"])
        self.assertEqual(len(self.fixture.calls[1].fields), 32)
        self.assertEqual(self.fixture.calls[1].source, "GetExilePool.GetExiles")
        self.assertEqual(self.context.performs, ["politics.exiles"])
        self.assertEqual(self.context.country_reads, 1)
        self.assert_pinned()
        self.assert_native_pages_preserved(result)

    async def test_actual_ig_filters_and_count_mismatch_do_not_claim_all_exiles(self):
        self.fixture.summary["GetExilePool.GetNumAllExiles"] = 10
        self.fixture.rows("GetExilePool.GetExiles", [{"Character.GetID": 0}])
        self.fixture.rows("Country.AccessAllInterestGroups", [{"InterestGroup.GetID": 0,
            "GetExilePool.IsInterestGroupFiltered(InterestGroup.Self)": True}])
        result = await self.api.exiles()
        self.assertEqual(result["exiles"]["source_count"], 1)
        self.assertFalse(result["all_count_matches_read_count"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["read_complete"])
        self.assertTrue(result["interest_group_filters"]["rows"][0]["filtered"])
        self.assertIn("filtered or changed", result["reasons"]["pool_scope"])
        self.assertEqual(result["exiles"]["rows"][0]["id"], 0)

    async def test_empty_pool_is_observed_when_native_all_count_is_zero(self):
        result = await self.api.exiles()
        self.assertTrue(result["complete"])
        self.assertTrue(result["all_count_matches_read_count"])
        self.assertEqual(result["exiles"]["rows"], [])
        self.assertEqual(result["traits"], [])
        self.assertEqual(result["interactions"], [])
        self.assertEqual(len(self.fixture.calls), 3)

    async def test_missing_all_count_remains_unknown_not_zero(self):
        self.fixture.summary["GetExilePool.GetNumAllExiles"] = {"value": 0, "error": 1168}
        result = await self.api.exiles()
        self.assertIsNone(result["all_count_matches_read_count"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["summary"]["all_exiles_count"], 0)
        self.assertIn("1168", result["summary"]["reasons"]["fields"]["all_exiles_count"])

    async def test_original_nested_traits_and_interaction_descriptors_without_validation(self):
        self.fixture.summary["GetExilePool.GetNumAllExiles"] = 1
        self.fixture.rows("GetExilePool.GetExiles", [{"Character.GetID": 4}])
        step = (data.CreationStep("GetExilePool.GetExiles", 0, 1),)
        self.fixture.rows("Character.AccessTraits", [{"Character.GetID": 8,
            "CharacterTrait.GetNameNoFormatting": "Trait", "CharacterTrait.IsDisabled": False}], step)
        self.fixture.rows("Character.GetInteractions", [{"Character.GetID": 8,
            "CharacterInteraction.GetName": "Invite", "CharacterInteraction.GetRequirements(Character.Self)": "Original restrictions",
            "CharacterInteraction.GetEffects(Character.Self)": "#bold Original effects#!",
            "CharacterInteraction.IsOnCooldown(Character.Self)": True,
            "CharacterInteraction.GetCooldownRemaining(Character.Self)": "0 days"}], step)
        result = await self.api.exiles()
        self.assertEqual(result["exiles"]["rows"][0]["id"], 4)
        self.assertEqual(result["traits"][0]["rows"][0]["character_id"], 8)
        self.assertIs(result["traits"][0]["rows"][0]["disabled"], False)
        interaction = result["interactions"][0]["rows"][0]
        self.assertEqual(interaction["character_id"], 8)
        self.assertEqual(interaction["requirements"], "Original restrictions")
        self.assertEqual(interaction["effects_raw"], "#bold Original effects#!")
        self.assertEqual(interaction["cooldown_remaining"], "0 days")
        self.assertTrue(interaction["on_cooldown"])
        self.assertNotIn("id", interaction)
        self.assertNotIn("valid", interaction)
        self.assertFalse(result["interaction_validity_checked"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["read_complete"])
        self.assertIn("interaction_validity", result["reasons"])
        self.assertTrue(all(call.steps == step for call in self.fixture.calls[3:]))

    async def test_partial_nested_text_and_native_error_keep_character_and_page(self):
        self.fixture.summary["GetExilePool.GetNumAllExiles"] = 1
        self.fixture.rows("GetExilePool.GetExiles", [{"Character.GetID": 0}])
        step = (data.CreationStep("GetExilePool.GetExiles", 0, 1),)
        self.fixture.rows("Character.GetInteractions", [{"Character.GetID": 0,
            "CharacterInteraction.GetEffects(Character.Self)": {"value": "#bold Kept#!", "total": 900, "error": 234}}], step)
        result = await self.api.exiles()
        self.assertFalse(result["read_complete"])
        self.assertEqual(result["exiles"]["rows"][0]["id"], 0)
        interaction = result["interactions"][0]["rows"][0]
        self.assertEqual(interaction["effects"], "Kept")
        self.assertEqual(interaction["effects_raw"], "#bold Kept#!")
        self.assertIn("234", interaction["reasons"]["effects"])
        self.assert_native_pages_preserved(result)

    async def test_source_error_is_not_reported_as_empty_pool_success(self):
        self.fixture.page_errors["GetExilePool.GetExiles"] = 87
        self.fixture.count_invalid.add("GetExilePool.GetExiles")
        result = await self.api.exiles()
        self.assertFalse(result["read_complete"])
        self.assertIsNone(result["exiles"]["source_count"])
        self.assertIsNone(result["all_count_matches_read_count"])
        self.assertEqual(result["exiles"]["native_pages"][0].error, 87)

    async def test_129_exiles_use_three_pages_and_preserve_duplicate_labels(self):
        self.fixture.summary["GetExilePool.GetNumAllExiles"] = 129
        self.fixture.rows("GetExilePool.GetExiles", [{"Character.GetID": index,
            "Character.GetFullNameNoFormatting": "Duplicate"} for index in range(129)])
        result = await self.api.exiles(details=False)
        source_calls = [call for call in self.fixture.calls if call.source == "GetExilePool.GetExiles"]
        self.assertEqual([call.start for call in source_calls], [0, 64, 128])
        self.assertEqual(len(result["exiles"]["rows"]), 129)
        self.assertEqual(result["exiles"]["rows"][-1]["id"], 128)
        self.assertEqual(result["exiles"]["rows"][-1]["source_index"], 128)
        self.assertTrue(result["all_count_matches_read_count"])
        self.assertFalse(result["details_included"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["read_complete"])

    async def test_explicit_acceptance_country_does_not_relabel_global_pool_owner(self):
        result = await self.api.exiles(country_id=0x80000002)
        self.assertEqual(self.context.country_reads, 0)
        self.assert_pinned(0x80000002)
        self.assertIn("original global", result["pool_counts_scope"])
        self.assertEqual(self.fixture.calls[1].source, "GetExilePool.GetExiles")

    async def test_political_roster_uses_exact_named_sources_and_is_not_exhaustive(self):
        self.fixture.summary.update({"Country.HasRuler": True, "Country.HasHeir": False,
            "Country.GetRuler.GetID": -2147483646, "Country.GetRuler.GetAllRoleNames": "Ruler"})
        self.fixture.rows("Country.AccessAllInterestGroups", [{"InterestGroup.GetID": 0,
            "InterestGroup.GetLeader.GetID": 3, "InterestGroup.GetLeader.GetProminence": data.FixedPoint(12345)}])
        self.fixture.rows("Country.GetCompanies", [{"Company.GetNameNoFormatting": "Company", "Company.GetExecutive.GetID": 4}])
        self.fixture.rows("Country.AccessPoliticalMovements", [{"PoliticalMovement.GetNameNoFormatting": "Movement"}])
        step = (data.CreationStep("Country.AccessPoliticalMovements", 0, 1),)
        self.fixture.rows("PoliticalMovement.GetSupportingCharacters", [{"Character.GetID": 3,
            "Character.IsAgitator": True, "PoliticalMovement.GetNameNoFormatting": "Movement"}], step)
        result = await self.api.political_characters(details=False)
        self.assertEqual(result["ruler"]["id"], 0x80000002)
        self.assertEqual(result["heir"]["status"], "absent")
        self.assertNotIn("id", result["heir"])
        self.assertEqual(result["interest_group_leaders"]["rows"][0]["id"], 3)
        self.assertEqual(result["company_executives"]["rows"][0]["id"], 4)
        self.assertEqual(result["movement_supporters"][0]["rows"][0]["id"], 3)
        self.assertFalse(result["roster_complete"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["read_complete"])
        self.assertIn("unattached agitators", result["reasons"]["roster_scope"])
        self.assertFalse(any("GetHeir" in expression for call in self.fixture.calls for expression in call.fields))
        self.assertEqual(self.context.performs, ["politics.political_characters"])
        self.assertEqual(self.context.country_reads, 1)
        self.assert_pinned()
        self.assert_native_pages_preserved(result)

    async def test_roster_promoted_character_details_keep_chained_parent_arguments(self):
        self.fixture.rows("Country.AccessAllInterestGroups", [{"InterestGroup.GetID": 0, "InterestGroup.GetLeader.GetID": 1}])
        step = (data.CreationStep("Country.AccessAllInterestGroups", 0, 1),)
        self.fixture.rows("InterestGroup.GetLeader.GetInteractions", [{"InterestGroup.GetLeader.GetID": 2,
            "CharacterInteraction.GetName": "Promote",
            "CharacterInteraction.GetRequirements(InterestGroup.GetLeader.Self)": "Current requirements"}], step)
        result = await self.api.political_characters()
        interaction = next(value for value in result["interactions"] if value["rows"])
        self.assertEqual(interaction["rows"][0]["character_id"], 2)
        self.assertEqual(result["interest_group_leaders"]["rows"][0]["id"], 1)
        self.assertEqual(interaction["rows"][0]["requirements"], "Current requirements")
        call = next(call for call in self.fixture.calls if call.source == "InterestGroup.GetLeader.GetInteractions")
        self.assertEqual(call.steps, step)
        self.assertIn("CharacterInteraction.GetEffects(InterestGroup.GetLeader.Self)", call.fields)
        self.assertNotIn("CharacterInteraction.GetEffects(Character.Self)", call.fields)

    async def test_movement_supporter_trait_details_preserve_two_original_creation_steps(self):
        self.fixture.rows("Country.AccessPoliticalMovements", [{"PoliticalMovement.GetNameNoFormatting": "A"}])
        movement_path = (data.CreationStep("Country.AccessPoliticalMovements", 0, 1),)
        self.fixture.rows("PoliticalMovement.GetSupportingCharacters", [{"Character.GetID": 1}], movement_path)
        path = (*movement_path, data.CreationStep("PoliticalMovement.GetSupportingCharacters", 0, 1))
        self.fixture.rows("Character.AccessTraits", [{"Character.GetID": 2,
            "CharacterTrait.GetNameNoFormatting": "Actual current trait"}], path)
        result = await self.api.political_characters()
        trait = next(value for value in result["traits"] if value["rows"])
        self.assertEqual(trait["rows"][0]["character_id"], 2)
        self.assertEqual(result["movement_supporters"][0]["rows"][0]["id"], 1)
        call = next(call for call in self.fixture.calls if call.source == "Character.AccessTraits")
        self.assertEqual(call.steps, path)
        self.assertEqual(trait["requested_occurrences"], [0, 0])
        self.assert_native_pages_preserved(result)

    async def test_character_missing_predicate_is_not_proven_absence(self):
        self.fixture.summary.update({"Country.HasHeir": {"value": False, "error": 87}, "Country.GetHeir.GetID": 0})
        result = await self.api.political_characters(details=False)
        self.assertEqual(result["heir"]["id"], 0)
        self.assertNotEqual(result["heir"]["status"], "absent")
        self.assertFalse(result["read_complete"])
        self.assertIn("87", result["summary"]["reasons"]["fields"]["has_heir"])

    async def test_cleanup_warnings_are_separate_from_observed_values(self):
        self.fixture.warning = OSError("copied storage cleanup")
        result = await self.api.exiles()
        self.assertTrue(result["complete"])
        warnings = [item for item in self.context.diagnostics if "cleanup_error" in item]
        self.assertEqual(len(warnings), len(self.fixture.calls))
        self.assertTrue(all(item["cleanup_error"] is self.fixture.warning for item in warnings))
        self.assert_native_pages_preserved(result)

    async def test_pending_roster_is_one_owned_workflow_without_replaying(self):
        self.context.executor.timeout = 0
        self.context.gate = asyncio.Event()
        pending = await self.api.political_characters()
        self.assertIsInstance(pending, PendingOperation)
        await self.context.entered.wait()
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertEqual(self.context.performs, ["politics.political_characters"])
        self.context.gate.set()
        result = await pending.resume(timeout=1)
        self.assertTrue(result["read_complete"])
        self.assertEqual(len([call for call in self.fixture.calls if call.source is None]), 1)
        self.assertEqual(self.context.country_reads, 1)

    async def test_no_candidate_command_validation_execution_filter_changes_or_character_binding(self):
        self.fixture.summary["GetExilePool.GetNumAllExiles"] = 1
        self.fixture.rows("GetExilePool.GetExiles", [{"Character.GetID": 0}])
        await self.api.exiles()
        forbidden = ("IsCommandValid", "ExecuteCommand", "SetInterestGroupFilter", "ToggleInterestGroupFilter", "GetRecruitCommand")
        self.assertFalse(any(word in expression for call in self.fixture.calls for expression in call.fields for word in forbidden))
        self.assertTrue(all(len(call.fields) <= 32 for call in self.fixture.calls))
        self.assert_pinned()


class CharacterChoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from pdx_runtime.v3_game import politics
        self.context = ChoiceContext()
        self.fixture = ChoiceFixture(self.context)
        self.api = politics.PoliticsAPI(self.context)
        self.patchers = [patch.object(data, name, getattr(self.fixture, name)) for name in
            ("evaluate", "list_count", "list_element", "release", "project_page", "invoke_property_action")
            if name != "project_page"]
        self.patchers.append(patch.object(data, "project_page", self.fixture.project))
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def pair(self):
        characters = await self.api.character_choices()
        interactions = await self.api.interaction_choices(characters[0])
        return characters, interactions

    async def close_pair(self, characters, interactions):
        await interactions.close()
        await characters.close()
        self.assertEqual(self.fixture.handles, {})
        self.assertEqual(_retained_runs(self.context), [])

    async def test_character_choices_are_actual_owned_values_not_ids_or_ordinals(self):
        self.fixture.character_rows[1] = ("Character", 0, "raw")
        choices = await self.api.character_choices()
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0]["id"], 0x80000002)
        self.assertNotEqual(choices[0]._binding.object, 0)
        self.assertNotEqual(choices[0]._binding.object, choices[0]["id"])
        report = choices.metadata["result"]
        self.assertEqual(report["status"], "partial")
        self.assertEqual(len(report["rejected_rows"]), 1)
        self.assertIn("PEAVCDataCharacter", report["rejected_rows"][0]["native_result"].value.native_type_name)
        self.assertFalse(getattr(choices.owner, "retain_unknown", False))
        self.assertEqual(self.fixture.conditions, 0)
        self.assertEqual(self.fixture.invocations, 0)
        await choices.close()
        self.assertEqual(self.fixture.handles, {})

    async def test_interaction_catalog_keeps_parent_borrowed_until_child_close(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        characters, interactions = await self.pair()
        self.assertEqual(len(interactions), 2)
        self.assertEqual(characters.active_leases, 1)
        self.assertEqual(self.fixture.conditions, 0)
        self.assertEqual(self.fixture.invocations, 0)
        self.assertFalse(interactions[0]["validity_checked"])
        self.assertIs(interactions.metadata["character_choice"], characters[0])
        with self.assertRaises(ChoicesBusyError):
            await characters.close()
        await self.close_pair(characters, interactions)

    async def test_raw_and_inline_interaction_results_are_rejected_without_converter(self):
        self.fixture.interaction_rows = [("CharacterInteraction", 51, "raw"), ("CharacterInteraction", 52, "inline")]
        characters, interactions = await self.pair()
        self.assertEqual(len(interactions), 0)
        self.assertEqual(len(interactions.metadata["result"]["rejected_rows"]), 2)
        self.assertEqual(interactions.metadata["result"]["status"], "unavailable")
        self.assertFalse(getattr(interactions.owner, "retain_unknown", False))
        self.assertEqual(self.fixture.conditions, 0)
        self.assertEqual(self.fixture.invocations, 0)
        await self.close_pair(characters, interactions)

    async def test_selected_exact_interaction_validated_once_and_invoked_once(self):
        characters, interactions = await self.pair()
        self.assertEqual(interactions[0]["name"], interactions[1]["name"])
        # Even a changed visible occurrence cannot retarget the actual binding.
        interactions[1]["source"]["source_index"] = 999
        result = await self.api.character_interact(characters[0], interactions[1])
        self.assertEqual(self.fixture.last_invoked, (("Character", 0x80000002), ("CharacterInteraction", 52)))
        self.assertEqual((self.fixture.conditions, self.fixture.invocations), (1, 1))
        self.assertTrue(result["accepted"])
        self.assertTrue(result["invoked"])
        self.assertFalse(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertIn("property action callback", result["acceptance_scope"])
        self.assertEqual(result["after"]["character"]["popularity"], Decimal(1))
        self.assertEqual(self.context.performs, ["politics.character_choices", "politics.interaction_choices", "politics.character_interact"])
        await self.close_pair(characters, interactions)

    async def test_closed_interaction_catalog_fails_locally_without_leaking_parent_borrow(self):
        characters, interactions = await self.pair()
        await interactions.close()
        before = len(self.context.queries)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            await self.api.character_interact(characters[0], interactions[0])
        self.assertEqual(len(self.context.queries), before)
        await characters.close()
        self.assertEqual(self.fixture.handles, {})
        self.assertEqual(_retained_runs(self.context), [])

    async def test_after_copy_failure_preserves_invocation_and_original_borrowed_graph(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        characters, interactions = await self.pair()
        self.fixture.copy_error = OSError("after copy transport unknown")
        result = await self.api.character_interact(characters[0], interactions[0])
        self.assertTrue(result["accepted"])
        self.assertTrue(result["invoked"])
        self.assertIs(result["readback_error"], self.fixture.copy_error)
        self.assertIs(result["readback_error"].choices, characters)
        self.assertEqual((self.fixture.conditions, self.fixture.invocations), (1, 1))
        with self.assertRaises(ChoicesBusyError):
            await interactions.close()
        with self.assertRaises(ChoicesBusyError):
            await characters.close()
        self.assertEqual({run.label for run in _retained_runs(self.context)},
            {"character_choices", "interaction_choices", "character_interact.after"})

    async def test_native_false_validation_never_invokes(self):
        characters, interactions = await self.pair()
        self.fixture.valid = False
        result = await self.api.character_interact(characters[0], interactions[0])
        self.assertIs(result["validation"].value, False)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual((self.fixture.conditions, self.fixture.invocations), (1, 0))
        self.assertNotIn("after", result)
        await self.close_pair(characters, interactions)

    async def test_native_validation_error_preserves_observed_true_without_invocation(self):
        characters, interactions = await self.pair()
        self.fixture.condition_error = 13
        result = await self.api.character_interact(characters[0], interactions[0])
        self.assertIs(result["validation"].value, True)
        self.assertEqual(result["validation"].error, 13)
        self.assertIsNone(result["accepted"])
        self.assertEqual((self.fixture.conditions, self.fixture.invocations), (1, 0))
        await self.close_pair(characters, interactions)

    async def test_native_invoked_and_accepted_flags_survive_original_action_error(self):
        characters, interactions = await self.pair()
        self.fixture.invocation_error = 87
        result = await self.api.character_interact(characters[0], interactions[0], readback=False)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["invoked"])
        self.assertEqual(result["native_error"], 87)
        self.assertIs(result["native_result"], result["invocation"])
        self.assertFalse(result["completed"])
        self.assertEqual((self.fixture.conditions, self.fixture.invocations), (1, 1))
        await self.close_pair(characters, interactions)

    async def test_interaction_from_different_character_choice_is_not_rebound(self):
        characters, interactions = await self.pair()
        before = len(self.context.queries)
        with self.assertRaisesRegex(ValueError, "exact Character"):
            await self.api.character_interact(characters[1], interactions[0])
        self.assertEqual(len(self.context.queries), before)
        self.assertEqual((self.fixture.conditions, self.fixture.invocations), (0, 0))
        await self.close_pair(characters, interactions)

    async def test_ids_names_indices_and_copied_dictionaries_are_not_owned_choices(self):
        characters, interactions = await self.pair()
        before = len(self.context.queries)
        for value in (0, 0x80000002, "Duplicate character", {"source_index": 0}, dict(characters[0])):
            with self.subTest(value=value), self.assertRaises(TypeError):
                await self.api.interaction_choices(value)
        self.assertEqual(len(self.context.queries), before)
        await self.close_pair(characters, interactions)

    async def test_mutated_context_adapter_does_not_retarget_retained_values(self):
        characters = await self.api.character_choices()
        self.context.adapters["data"] = "wrong-new-registry"
        interactions = await self.api.interaction_choices(characters[0])
        result = await self.api.character_interact(characters[0], interactions[0])
        self.assertTrue(result["accepted"])
        await self.close_pair(characters, interactions)
        self.assertTrue(all(adapter == "fixture-data" for _, adapter in self.fixture.adapters_used))

    async def test_copied_native_page_error_is_partial_not_unknown_ownership(self):
        self.fixture.copy_native_error = 13
        characters = await self.api.character_choices()
        self.assertEqual(len(characters), 2)
        self.assertEqual(characters[0]["id"], 0x80000002)
        self.assertFalse(characters.metadata["result"]["complete"])
        self.assertEqual(characters.metadata["result"]["native_pages"][0].error, 13)
        self.assertFalse(getattr(characters.owner, "retain_unknown", False))
        await characters.close()
        self.assertEqual(self.fixture.handles, {})

    async def test_copy_transport_failure_exposes_catalog_and_retains_original_graph(self):
        self.fixture.copy_error = OSError("copy transport unknown")
        with self.assertRaisesRegex(OSError, "copy transport unknown") as caught:
            await self.api.character_choices()
        catalog = caught.exception.choices
        self.assertTrue(catalog.owner.retain_unknown)
        self.assertTrue(self.fixture.handles)
        result = await catalog.close()
        self.assertFalse(result["closed"])
        self.assertEqual(self.fixture.released, [])

    async def test_known_local_projection_failure_does_not_become_unknown_transport(self):
        self.fixture.copy_error = ValueError("known local projection input")
        with self.assertRaisesRegex(ValueError, "known local") as caught:
            await self.api.character_choices()
        catalog = caught.exception.choices
        self.assertFalse(getattr(catalog.owner, "retain_unknown", False))
        await catalog.close()
        self.assertEqual(self.fixture.handles, {})

    async def test_pending_selected_action_holds_both_sources_and_resumes_original(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        characters, interactions = await self.pair()
        self.context.entered.clear()
        self.context.executor.timeout = 0
        self.context.gate = asyncio.Event()
        pending = await self.api.character_interact(characters[0], interactions[0], readback=False)
        self.assertIsInstance(pending, PendingOperation)
        await self.context.entered.wait()
        self.assertTrue(self.api._mutation_lock.locked())
        with self.assertRaises(ChoicesBusyError):
            await interactions.close()
        with self.assertRaises(ChoicesBusyError):
            await characters.close()
        self.context.gate.set()
        result = await pending.resume(timeout=1)
        self.assertTrue(result["accepted"])
        self.assertEqual((self.fixture.conditions, self.fixture.invocations), (1, 1))
        await self.close_pair(characters, interactions)

    async def test_uncertain_property_dispatch_keeps_both_catalogs_without_replay(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        characters, interactions = await self.pair()
        self.fixture.transport_error = OSError("property dispatch uncertain")
        with self.assertRaisesRegex(OSError, "property dispatch uncertain"):
            await self.api.character_interact(characters[0], interactions[0])
        self.assertEqual((self.fixture.conditions, self.fixture.invocations), (1, 1))
        with self.assertRaises(ChoicesBusyError):
            await interactions.close()
        with self.assertRaises(ChoicesBusyError):
            await characters.close()
        self.assertEqual({run.label for run in _retained_runs(self.context)},
            {"character_choices", "interaction_choices", "character_interact"})

    async def test_supported_sources_use_original_parents_and_promotions(self):
        for source, expected in (("ruler", "Country.GetRuler"), ("heir", "Country.GetHeir"),
                ("interest_group_leaders", "InterestGroup.GetLeader"),
                ("company_executives", "Company.GetExecutive"),
                ("movement_supporters", "PoliticalMovement.GetSupportingCharacters")):
            with self.subTest(source=source):
                characters = await self.api.character_choices(source)
                self.assertTrue(characters)
                self.assertIn(expected, [expression for expression, _ in self.fixture.events])
                self.assertTrue(all(choice.native_type == "Character" for choice in characters))
                await characters.close()
                self.assertEqual(self.fixture.handles, {})

    async def test_65_retained_character_records_copy_in_two_bounded_pages(self):
        self.fixture.character_rows = [("Character", index, "owned") for index in range(65)]
        characters = await self.api.character_choices()
        self.assertEqual(len(characters), 65)
        self.assertEqual([len(call.bindings) for call in self.fixture.calls], [64, 1])
        self.assertTrue(all(len(call.fields) == 32 for call in self.fixture.calls))
        self.assertEqual(characters[0]["id"], 0)
        self.assertEqual(characters[64]["id"], 64)
        self.assertEqual(self.fixture.conditions, 0)
        await characters.close()
        self.assertEqual(self.fixture.handles, {})

    async def test_descriptor_cleanup_warning_preserves_owned_choices_and_copies(self):
        self.fixture.warning = OSError("copied buffer cleanup")
        characters = await self.api.character_choices()
        self.assertEqual(characters[0]["id"], 0x80000002)
        self.assertTrue(characters.metadata["result"]["complete"])
        self.assertEqual(characters.metadata["result"]["cleanup_warnings"], [self.fixture.warning])
        self.assertEqual(len(self.context.remembered), 1)
        await characters.close()
        self.assertEqual(self.fixture.handles, {})


if __name__ == "__main__":
    unittest.main()
