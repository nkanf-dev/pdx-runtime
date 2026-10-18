"""CPU-only national-management page contracts. No process, DLL, or game calls.

The production mixin and shared projection composition execute against copied
native-page fixtures. These tests are not live native eligibility evidence.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
import struct
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.query_executor import PendingOperation, RetainedOwners, SerializedQueryExecutor
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.politics import national


class Query:
    serial = 0
    def __init__(self, result):
        Query.serial += 1
        self.request_id = Query.serial
        self.result = result
    def read(self):
        return self.result


def cell(value, kind, row, field):
    bits, text = 0, b""
    if kind == data.Kind.STRING:
        text = value.encode("utf-8")
    elif kind == data.Kind.FIXED:
        bits = int(Decimal(value) * 100000) & ((1 << 64) - 1)
    elif kind == data.Kind.FLOAT:
        bits = struct.unpack("<I", struct.pack("<f", value))[0]
    elif kind == data.Kind.DOUBLE:
        bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    else:
        bits = int(value) & ((1 << 64) - 1)
    return data.ProjectionCell(row, field, 0, kind, 0, bits, text, len(text))


class API(national.NationalManagementMixin):
    def __init__(self, ctx):
        self._ctx = ctx
        self._mutation_lock = asyncio.Lock()


class Fixture:
    country_id = 0x81000001
    state_id = 0x82000002
    world = 700

    def __init__(self):
        self.calls, self.performs, self.country_resolutions = [], [], 0
        self.query_factory, self.transform = Query, lambda page: page
        self.retained, self.type_calls = [], []
        self.retained_formations = {}
        self.ctx = NS(native=object(), process=object(), adapters={"data": "fake"},
                      world=self.world, diagnostics=[], owner_pool=RetainedOwners(),
                      _types={"Country": 20, "State": 30})
        self.ctx.country_id = self.current_country
        self.ctx.perform = self.perform
        self.ctx.query = self.query
        self.ctx.type_id = self.type_id
        self.ctx._remember = self.retained.append
        self.api = API(self.ctx)
        specs = (national._IDENTITY, national._MODIFIER_SUMMARY, national._MODIFIER_ENTRIES,
                 national._TIMED_MODIFIERS, national._COLONY_FIELDS,
                 national._FORMATION_FIELDS, national._FORMATION_COUNTRIES,
                 national._FORMATION_REGIONS, national._UNIFICATION_SUPPORT, national._RELEASE_FIELDS,
                 national._COLONIZATION_OPTIONS, national._PLAYER_COLONY_STATES)
        self.kinds = {field.expression: field.kind for fields in specs for field in fields}
        self.defaults = {expression: "native text" if kind == data.Kind.STRING else
                         False if kind == data.Kind.BOOL else 0.0 if kind == data.Kind.FLOAT else 0
                         for expression, kind in self.kinds.items()}
        self.defaults.update({
            "Country.GetName": "#bold Test Republic#!", "Country.GetTagName": "TST",
            "Country.GetFlagTextIcon": "@TST!", "GetString_CVector4f(Country.GetMapColor)": "(0, 0.5, 1, 1)",
            "Country.GetPrimaryCulturesDesc": "#v Culture A#!, Culture B",
            "Country.GetStateReligion.GetName": "Religion", "Country.GetGovernment.GetName": "Republic",
            "State.GetID": self.state_id, "State.GetName": "Colony",
            "State.IsValid": True, "State.IsBeingColonized": True,
            "State.IsGrowingColony": True, "State.GetOwner.GetID": self.country_id,
            "State.GetColonyProgress": "0.12345", "State.GetDailyColonyProgress": "0",
            "State.GetNextColonyProvince.GetState.GetOwner.GetID": 0x83000003,
            "State.GetColonizers('STATE_REGION_COLONIZER_ITEM')": "#v Competitor A#!, Competitor B",
            "Country.GetTensionWith(State.GetNextColonyProvince.GetState.GetOwner)": 0,
            "ModifierEntry.GetName": "#v Authority#!", "ModifierEntry.GetValue": "-0.25000",
            "ModifierEntry.GetBreakdown": "#bold Source A#!: -25%",
            "TimedModifier.GetNumRemainingDays": 0, "TimedModifier.GetPercentageRemainingDays": 0.0,
            "TimedModifier.GetStartDate.GetString": "1836.1.1", "TimedModifier.GetEndDate.GetString": "1836.1.2",
            "CountryFormation.GetName": "Union", "CountryCreation.GetName": "Released Nation",
            "Country.GetID": 0x84000004, "StateRegion.GetID": 0x85000005,
            "StateRegion.GetName": "Formation Region", "StateRegion.GetLargestStateOwner.GetID": self.country_id,
            "GetDataModelSize(CountryFormation.AccessFormationCandidates)": 1,
            "GetDataModelSize(CountryFormation.AccessFormationStateRegions)": 1,
            "GetDataModelSize(CountryFormation.AccessFormationCultureCountries)": 1,
            "GetPlayer.GetID": 0x99000009, "GetPlayer.GetName": "Actual Player",
            "CountryFormation.GetSupportedCandidateCountry(GetPlayer).GetID": 0xffffffff,
            "CountryFormation.GetSupportedCandidateCountry(GetPlayer).GetName": "",
            "State.GetStateRegion.GetID": 55,
            "State.GetStateRegion.GetColonizeTooltip": "#bold Original conditions#!: claim, interest, malaria",
        })
        self.sources = {
            "Country.GetModifier.GetEntries": [{}], "Country.GetTimedModifiers": [{}],
            "Country.AccessStates": [{}], "Country.AccessFormableCountries": [{}],
            "Country.AccessReleasableCountries": [{}],
            "CountryFormation.AccessFormationCandidates": [{}],
            "CountryFormation.AccessFormationStateRegions": [{}],
            "CountryFormation.AccessFormationCultureCountries": [{}],
            "GetPlayer.AccessStates": [{"State.GetID": 11, "State.GetOwner.GetID": 0x99000009,
                                         "State.IsBeingColonized": False, "State.IsGrowingColony": False}],
        }
        self.states = {}
        self.nested = {}
        self.nested_parents = None
        self.nested_parent_by_page = {}

    async def current_country(self):
        self.country_resolutions += 1
        return self.country_id

    async def type_id(self, name):
        self.type_calls.append(name)
        return {"Country": 20, "State": 30, "CountryFormation": 40, "CountryCreation": 41}[name]

    async def perform(self, label, work):
        self.performs.append(label)
        return await work()

    async def query(self, label, factory, **kwargs):
        return factory().read()

    def project(self, native, fields, binding_rows, **kwargs):
        self.calls.append((tuple(fields), tuple(binding_rows), kwargs))
        source = kwargs.get("source_expression")
        start, limit = kwargs.get("start", 0), kwargs.get("limit", 64)
        if source is not None:
            steps = kwargs.get("steps", ())
            parent_payload = {}
            if steps:
                assert len(steps) == 1 and steps[0].source_expression == "Country.AccessFormableCountries"
                step = steps[0]
                parents = self.sources[step.source_expression] if self.nested_parents is None else self.nested_parents
                if step.expected_count is not None and step.expected_count != len(parents):
                    page = data.ProjectionPage(1237, self.world, len(fields), start, 45, 0, False, 0, ())
                    return self.query_factory(self.transform(page))
                parent_payload = {**parents[step.index], **self.nested_parent_by_page.get((source, start), {})}
                all_rows = self.nested.get(source, {}).get(step.index, self.sources[source])
            else:
                all_rows = self.sources[source]
            payloads = [{**parent_payload, **payload} for payload in all_rows[start:start + limit]]
            total = len(all_rows)
            if source == "GetPlayer.AccessStates":
                assert tuple(binding_rows) == ((),)
            else:
                self.assert_country_bindings(binding_rows)
        else:
            total = len(binding_rows)
            payloads = []
            for row in binding_rows:
                if len(row) == 2 and row[1].retained:
                    country, formation = row
                    assert country.domain == "Country"
                    assert formation.type_id in (40, 41) and formation.object in self.retained_formations
                    payload = dict(self.retained_formations[formation.object])
                    if "GetPlayer.GetID" in fields:
                        payload.update({"Country.GetID": country.object, "Country.GetName": f"Candidate {country.object}"})
                    else:
                        assert country.object == self.country_id
                    payloads.append(payload)
                elif len(row) == 2:
                    country, state = row
                    assert country.domain == "Country" and country.object == self.country_id
                    assert state.domain == "State" and not state.retained
                    payloads.append({"State.GetID": state.object, **self.states.get(state.object, {})})
                elif len(row) == 1 and row[0].domain == "State":
                    state = row[0]
                    assert not state.retained
                    payloads.append({"State.GetID": state.object, **self.states.get(state.object, {})})
                else:
                    self.assert_country_bindings((row,))
                    payloads.append({})
        cells = []
        for row_index, payload in enumerate(payloads):
            for field_index, expression in enumerate(fields):
                value = payload.get(expression, self.defaults[expression])
                cells.append(cell(value, self.kinds[expression], row_index, field_index))
        page = data.ProjectionPage(0, self.world, len(fields), start, 45, total, True,
                                   len(payloads), tuple(cells))
        return self.query_factory(self.transform(page))

    def assert_country_bindings(self, rows):
        for row in rows:
            assert len(row) == 1 and row[0].domain == "Country" and not row[0].retained


class NationalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fx = Fixture()
        self.patch = patch.object(data, "project_page", self.fx.project)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        # A read may not generate, validate, or submit a political command.
        self.forbid = patch.object(data, "evaluate", side_effect=AssertionError("read used retained evaluation"))
        self.forbid.start()
        self.addCleanup(self.forbid.stop)

    def formation_native_source(self, records=None, *, raw_element=False, raw_root=False,
                                fail_element=None, unknown_error=None, named_type="CountryFormation"):
        """Fixture native results only; production _Run.elements/OwnedChoices run."""
        records = [{}] if records is None else records
        source_expression = {"CountryFormation": "Country.AccessFormableCountries",
                             "CountryCreation": "Country.AccessReleasableCountries"}[named_type]
        type_id = {"CountryFormation": 40, "CountryCreation": 41}[named_type]
        backend = NS(calls=[], released=[], live=set(), records=records)
        self.fx.retained_formations = {200 + i: dict(row) for i, row in enumerate(records)}
        def result(action, handle, value, *, native_type_id=-1, native_type_name=""):
            return NS(error=0, action=action, value_handle=handle, value=value,
                      type_id=native_type_id, native_type_name=native_type_name)
        def evaluate(native, expression, bindings, **kwargs):
            self.assertEqual(expression, source_expression)
            self.assertEqual(len(bindings), 1)
            self.assertEqual(bindings[0].object, self.fx.country_id)
            backend.calls.append(("source", expression, tuple(bindings)))
            backend.live.add(100)
            source_rtti = ".?AU?$SAnyType@PEAVCData" + named_type + "@@@PdxAnyImpl@@" if raw_root else "fixture list"
            value = (data.NativeObject(0x900, type_id, 100, source_rtti)
                     if raw_root else data.NativeList(100, 0, b"", source_rtti))
            return Query(result(data.Action.EVALUATE, 100, value,
                native_type_id=type_id if raw_root else -1, native_type_name=source_rtti))
        def count(native, handle, **kwargs):
            self.assertEqual(handle, 100)
            backend.calls.append(("count", handle))
            return Query(NS(error=0, action=data.Action.LIST_COUNT, total=len(records), value_handle=0))
        def element(native, handle, index, **kwargs):
            self.assertEqual(handle, 100)
            backend.calls.append(("element", index))
            if index == fail_element:
                raise unknown_error if unknown_error is not None else OSError("native element dispatch unknown")
            value_handle = 200 + index
            backend.live.add(value_handle)
            rtti = (".?AU?$SAnyType@PEAVCData" + named_type + "@@@PdxAnyImpl@@" if raw_element else
                    ".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@")
            value = data.NativeObject(0x1000 + index * 0x100, type_id, value_handle, rtti)
            return Query(result(data.Action.LIST_ELEMENT, value_handle, value,
                native_type_id=type_id, native_type_name=rtti))
        def release(native, handle, **kwargs):
            self.assertIn(handle, backend.live)
            backend.live.remove(handle)
            backend.released.append((handle, kwargs))
            return Query(result(data.Action.RELEASE, 0, None))
        for patcher in (patch.object(data, "evaluate", evaluate), patch.object(data, "list_count", count),
                        patch.object(data, "list_element", element), patch.object(data, "release", release)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return backend

    async def test_identity_copies_native_flag_color_and_chained_flavor(self):
        result = await self.fx.api.national_identity()
        self.assertTrue(result["complete"])
        self.assertEqual(result["name"], "Test Republic")
        self.assertEqual(result["name_raw"], "#bold Test Republic#!")
        self.assertEqual(result["tag"], "TST")
        self.assertEqual(result["flag_text_icon"], "@TST!")
        self.assertEqual(result["map_color"], "(0, 0.5, 1, 1)")
        self.assertEqual(result["government"], "Republic")
        self.assertEqual(len(result["native_pages"]), 1)
        self.assertEqual(self.fx.performs, ["politics.national_identity"])
        self.assertEqual(self.fx.type_calls, [])
        fields = self.fx.calls[0][0]
        self.assertIn("GetString_CVector4f(Country.GetMapColor)", fields)
        self.assertNotIn("Country.GetFlag", fields)
        self.assertNotIn("Country.GetMapColor", fields)

    async def test_modifiers_keep_aggregate_sources_dates_zero_and_negative_fixed(self):
        result = await self.fx.api.national_modifiers()
        self.assertTrue(result["complete"])
        self.assertEqual(result["entries"]["rows"][0]["value"], Decimal("-0.25"))
        self.assertEqual(result["entries"]["rows"][0]["breakdown_raw"], "#bold Source A#!: -25%")
        timed = result["timed_modifiers"]["rows"][0]
        self.assertEqual(timed["remaining_days"], 0)
        self.assertIs(timed["is_decaying"], False)
        self.assertEqual(timed["start_date"], "1836.1.1")
        self.assertEqual(timed["end_date"], "1836.1.2")
        self.assertEqual(len(result["native_pages"]), 3)
        self.assertEqual([call[2]["source_expression"] for call in self.fx.calls],
                         [None, "Country.GetModifier.GetEntries", "Country.GetTimedModifiers"])
        self.assertEqual(self.fx.country_resolutions, 1)
        self.assertEqual(self.fx.performs, ["politics.national_modifiers"])

    async def test_empty_modifier_sources_are_complete_not_missing(self):
        self.fx.sources["Country.GetModifier.GetEntries"] = []
        self.fx.sources["Country.GetTimedModifiers"] = []
        result = await self.fx.api.national_modifiers()
        self.assertTrue(result["complete"])
        self.assertEqual(result["entries"]["rows"], [])
        self.assertEqual(result["timed_modifiers"]["source_count"], 0)

    async def test_native_error_and_partial_text_survive_composite(self):
        def partial(page):
            if page.fields_count == len(national._MODIFIER_ENTRIES):
                first, *rest = page.cells
                return replace(page, error=13, cells=(replace(first, error=50, total=first.total + 99), *rest))
            return page
        self.fx.transform = partial
        result = await self.fx.api.national_modifiers()
        self.assertFalse(result["complete"])
        self.assertEqual(result["entries"]["rows"][0]["name"], "Authority")
        reasons = str(result["entries"]["rows"][0]["reasons"])
        self.assertIn("50", reasons)
        self.assertIn("incomplete", reasons)
        self.assertEqual(result["native_pages"][1].error, 13)
        self.assertEqual(result["timed_modifiers"]["rows"][0]["remaining_days"], 0)

    async def test_colonies_filter_index_and_only_project_selected_full_ids(self):
        self.fx.sources["Country.AccessStates"] = [
            {"State.GetID": 12, "State.IsBeingColonized": False, "State.IsGrowingColony": False},
            {"State.GetID": 0, "State.IsBeingColonized": True, "State.IsGrowingColony": False},
            {"State.GetID": self.fx.state_id},
        ]
        self.fx.states[0] = {"State.IsColonizationPaused": True}
        result = await self.fx.api.colonies()
        self.assertTrue(result["complete"])
        self.assertEqual([row["id"] for row in result["rows"]], [0, self.fx.state_id])
        self.assertEqual(result["observed_state_count"], 3)
        self.assertEqual(result["colony_count"], 2)
        self.assertIs(result["rows"][0]["paused"], True)
        self.assertEqual(result["rows"][0]["daily_progress"], Decimal("0"))
        self.assertEqual(result["rows"][1]["progress"], Decimal("0.12345"))
        self.assertEqual(result["rows"][1]["tension"], 0)
        self.assertEqual(result["rows"][1]["target_country_id"], 0x83000003)
        self.assertEqual(len(self.fx.calls), 2)
        fields, bindings, kwargs = self.fx.calls[1]
        self.assertEqual([row[1].object for row in bindings], [0, self.fx.state_id])
        self.assertIn("State.GetColonizers('STATE_REGION_COLONIZER_ITEM')", fields)
        self.assertIn("Country.GetTensionWith(State.GetNextColonyProvince.GetState.GetOwner)", fields)
        self.assertEqual(self.fx.country_resolutions, 1)
        self.assertEqual(self.fx.performs, ["politics.colonies"])

    async def test_no_colonies_stops_after_index(self):
        self.fx.sources["Country.AccessStates"] = [{
            "State.IsBeingColonized": False, "State.IsGrowingColony": False}]
        result = await self.fx.api.colonies()
        self.assertTrue(result["complete"])
        self.assertEqual(result["rows"], [])
        self.assertEqual(len(self.fx.calls), 1)

    async def test_unknown_classification_and_invalid_identity_are_preserved(self):
        self.fx.sources["Country.AccessStates"] = [{"State.GetID": 0xffffffff}]
        result = await self.fx.api.colonies()
        self.assertFalse(result["complete"])
        self.assertEqual(result["rows"], [])
        self.assertEqual(len(result["unclassified_rows"]), 1)
        self.assertEqual(result["unclassified_rows"][0]["id"], 0xffffffff)
        self.assertEqual(len(self.fx.calls), 1)

    async def test_false_classification_with_native_error_is_not_asserted_absent(self):
        self.fx.sources["Country.AccessStates"] = [{
            "State.IsBeingColonized": False, "State.IsGrowingColony": False}]
        self.fx.transform = lambda page: replace(page, cells=tuple(
            replace(value, error=50) if value.field == 2 else value for value in page.cells))
        result = await self.fx.api.colonies()
        self.assertFalse(result["complete"])
        self.assertEqual(result["rows"], [])
        self.assertEqual(len(result["unclassified_rows"]), 1)
        self.assertIs(result["unclassified_rows"][0]["is_being_colonized"], False)
        self.assertIn("50", result["unclassified_rows"][0]["reasons"]["is_being_colonized"])

    async def test_long_native_text_and_page_errors_keep_colony_values(self):
        long_text = "competitor " * 10000
        self.fx.defaults["State.GetColonizers('STATE_REGION_COLONIZER_ITEM')"] = long_text
        self.fx.transform = lambda page: replace(page, error=13)
        result = await self.fx.api.colony_details(self.fx.state_id)
        self.assertFalse(result["complete"])
        self.assertEqual(result["colonizers"], long_text.rstrip())
        self.assertEqual(result["progress"], Decimal("0.12345"))
        self.assertEqual(result["native_pages"][0].error, 13)
        self.assertEqual(len(self.fx.calls), 1)

    async def test_selected_country_id_does_not_resolve_current_country(self):
        result = await self.fx.api.national_identity(country_id=0)
        self.assertEqual(result["country_id"], 0)
        self.assertEqual(self.fx.calls[0][1][0][0].object, 0)
        self.assertEqual(self.fx.country_resolutions, 0)

    async def test_state_details_accept_zero_id_without_name_scan(self):
        result = await self.fx.api.colony_details(0)
        self.assertEqual(result["id"], 0)
        self.assertEqual(result["requested_state_id"], 0)
        self.assertEqual(result["country_id"], self.fx.country_id)
        self.assertTrue(result["complete"])
        self.assertNotIn("source_index", result)
        self.assertEqual(len(self.fx.calls), 1)
        self.assertIsNone(self.fx.calls[0][2]["source_expression"])

    async def test_name_ambiguity_does_not_choose_list_position(self):
        self.fx.sources["Country.AccessStates"] = [{"State.GetID": 0}, {"State.GetID": self.fx.state_id}]
        result = await self.fx.api.colony_details("Colony")
        self.assertEqual(result["status"], "ambiguous")
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(len(self.fx.calls), 1)

    async def test_exact_name_resolves_native_id(self):
        result = await self.fx.api.colony_details("Colony")
        self.assertTrue(result["complete"])
        self.assertEqual(result["id"], self.fx.state_id)
        self.assertEqual(len(self.fx.calls), 2)

    async def test_missing_state_row_is_not_erased(self):
        self.fx.transform = lambda page: replace(page, cells=(), row_count=0)
        result = await self.fx.api.colony_details(self.fx.state_id)
        self.assertFalse(result["complete"])
        self.assertIsNone(result["id"])
        self.assertEqual(result["requested_state_id"], self.fx.state_id)
        self.assertIn("did not return", result["reasons"]["fields"]["row"])

    async def test_invalid_state_selectors_fail_before_native_submission(self):
        for target in (True, -1, 0xffffffff, "", {}, {"source_index": 0}):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    await self.fx.api.colony_details(target)
        self.assertEqual(self.fx.calls, [])

    async def test_catalogs_keep_native_eligibility_and_explicit_selector_limits(self):
        formations = await self.fx.api.formation_candidates(details=False)
        releases = await self.fx.api.release_candidates()
        self.assertTrue(formations["catalog_complete"])
        self.assertTrue(releases["catalog_complete"])
        self.assertFalse(formations["complete"])
        self.assertFalse(releases["complete"])
        self.assertFalse(formations["selector_identity"]["available"])
        self.assertFalse(releases["release_options"]["complete"])
        for result in (formations, releases):
            row = result["rows"][0]
            self.assertEqual(row["source_index"], 0)
            self.assertNotIn("id", row)
            self.assertNotIn("key", row)
            self.assertEqual(row["controlled_states"], 0)
        self.assertIs(formations["rows"][0]["can_form"], False)
        self.assertIs(releases["rows"][0]["can_release"], False)
        self.assertEqual([call[2]["source_expression"] for call in self.fx.calls],
                         ["Country.AccessFormableCountries", "Country.AccessReleasableCountries"])
        self.assertFalse(any("CanReleaseCountryAs" in field for call in self.fx.calls for field in call[0]))

    async def test_formation_details_copy_actual_parent_and_native_country_region_ids(self):
        result = await self.fx.api.formation_candidates()
        self.assertTrue(result["complete"])
        self.assertTrue(result["catalog_complete"])
        self.assertFalse(result["selector_identity"]["available"])
        candidate = result["unification_candidates"][0]["rows"][0]
        region = result["formation_state_regions"][0]["rows"][0]
        self.assertEqual(candidate["country_id"], 0x84000004)
        self.assertEqual(region["state_region_id"], 0x85000005)
        self.assertEqual(candidate["formation_name"], "Union")
        self.assertEqual(region["formation_name"], "Union")
        self.assertEqual(candidate["supporter_count"], 0)
        self.assertIs(region["controlled"], False)
        self.assertEqual(len(self.fx.calls), 4)
        self.assertEqual(self.fx.country_resolutions, 1)
        self.assertEqual(self.fx.performs, ["politics.formation_candidates"])
        for fields, bindings, kwargs in self.fx.calls[1:]:
            step, = kwargs["steps"]
            self.assertEqual(step, data.CreationStep("Country.AccessFormableCountries", 0, 1))
            self.assertIn("CountryFormation.GetName", fields)
            self.assertIn("CountryFormation.GetDefinition.GetName", fields)
            self.assertEqual(len(bindings), 1)
            self.assertEqual(bindings[0][0].object, self.fx.country_id)
        self.assertIn("CountryFormation.ControlsStateRegion(Country.Self,StateRegion.Self)", self.fx.calls[2][0])
        self.assertNotIn("id", result["rows"][0])
        self.assertNotIn("unification_candidates", result["rows"][0])

    async def test_reordered_formation_source_is_not_joined_to_old_parent_label(self):
        self.fx.sources["Country.AccessFormableCountries"] = [{"CountryFormation.GetName": "Earlier label"}]
        self.fx.nested_parents = [{"CountryFormation.GetName": "Current label"}]
        result = await self.fx.api.formation_candidates()
        self.assertEqual(result["rows"][0]["name"], "Earlier label")
        self.assertEqual(result["unification_candidates"][0]["rows"][0]["formation_name"], "Current label")
        self.assertEqual(result["formation_state_regions"][0]["rows"][0]["formation_name"], "Current label")
        self.assertNotIn("formation_id", result["unification_candidates"][0]["rows"][0])
        self.assertFalse(result["atomic_snapshot"])

    async def test_changed_formation_source_count_preserves_native_retry_without_replay(self):
        self.fx.nested_parents = [{}, {}]
        result = await self.fx.api.formation_candidates()
        self.assertFalse(result["complete"])
        self.assertTrue(result["catalog_complete"])
        self.assertEqual(len(self.fx.calls), 4)
        for name in ("unification_candidates", "formation_state_regions", "culture_countries"):
            observed, = result[name]
            self.assertEqual(observed["rows"], [])
            self.assertEqual(observed["native_pages"][0].error, 1237)
            self.assertFalse(observed["parent_label_available"])
            self.assertEqual(observed["requested_parent_occurrence"], 0)

    async def test_empty_nested_sources_do_not_invent_a_parent_label(self):
        self.fx.sources["CountryFormation.AccessFormationCandidates"] = []
        self.fx.sources["CountryFormation.AccessFormationStateRegions"] = []
        self.fx.sources["CountryFormation.AccessFormationCultureCountries"] = []
        result = await self.fx.api.formation_candidates()
        self.assertTrue(result["complete"])
        for name in ("unification_candidates", "formation_state_regions", "culture_countries"):
            observed, = result[name]
            self.assertEqual(observed["source_count"], 0)
            self.assertEqual(observed["rows"], [])
            self.assertFalse(observed["parent_label_available"])
            self.assertNotIn("formation_name", observed)
            self.assertNotIn("formation_id", observed)

    async def test_nested_formation_pages_keep_each_current_parent_label(self):
        source = "CountryFormation.AccessFormationCandidates"
        self.fx.sources[source] = [{"Country.GetID": index} for index in range(65)]
        self.fx.nested_parent_by_page[(source, 0)] = {"CountryFormation.GetName": "Parent at page 0"}
        self.fx.nested_parent_by_page[(source, 64)] = {"CountryFormation.GetName": "Parent at page 64"}
        result = await self.fx.api.formation_candidates()
        observed, = result["unification_candidates"]
        self.assertEqual([row["country_id"] for row in observed["rows"]], list(range(65)))
        self.assertEqual(observed["rows"][0]["formation_name"], "Parent at page 0")
        self.assertEqual(observed["rows"][-1]["formation_name"], "Parent at page 64")
        self.assertEqual(len(observed["native_pages"]), 2)
        calls = [call for call in self.fx.calls if call[2]["source_expression"] == source]
        self.assertEqual([call[2]["start"] for call in calls], [0, 64])
        self.assertTrue(all(len(call[0]) <= 32 for call in calls))

    async def test_formation_child_field_errors_keep_ids_and_native_rows(self):
        def error(page):
            if page.fields_count == len(national._FORMATION_REGIONS):
                return replace(page, cells=tuple(replace(value, error=50) if value.field == 7 else value for value in page.cells))
            return page
        self.fx.transform = error
        result = await self.fx.api.formation_candidates()
        self.assertFalse(result["complete"])
        region = result["formation_state_regions"][0]["rows"][0]
        self.assertEqual(region["state_region_id"], 0x85000005)
        self.assertIs(region["controlled"], False)
        self.assertIn("50", region["reasons"]["controlled"])
        self.assertEqual(len(result["native_pages"]), 4)

    async def test_duplicate_formation_labels_do_not_collapse_distinct_occurrences(self):
        self.fx.sources["Country.AccessFormableCountries"] = [{}, {}]
        self.fx.nested["CountryFormation.AccessFormationCandidates"] = {
            0: [{"Country.GetID": 0}], 1: [{"Country.GetID": 0x84000004}]}
        result = await self.fx.api.formation_candidates()
        observations = result["unification_candidates"]
        self.assertEqual(len(observations), 2)
        self.assertEqual([row["rows"][0]["country_id"] for row in observations], [0, 0x84000004])
        self.assertEqual([row["rows"][0]["formation_name"] for row in observations], ["Union", "Union"])
        self.assertEqual([row["requested_parent_occurrence"] for row in observations], [0, 1])
        self.assertEqual(len(result["native_pages"]), 7)

    async def test_empty_formation_catalog_does_not_issue_nested_calls(self):
        self.fx.sources["Country.AccessFormableCountries"] = []
        result = await self.fx.api.formation_candidates()
        self.assertTrue(result["complete"])
        self.assertEqual(result["unification_candidates"], [])
        self.assertEqual(result["formation_state_regions"], [])
        self.assertEqual(len(self.fx.calls), 1)

    async def test_pending_nested_formation_page_resumes_original_scoped_request(self):
        executor = SerializedQueryExecutor(timeout=.01, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query, self.fx.ctx.perform = executor.query, executor.perform
        self.fx.ctx._remember = executor._remember
        captured, started, loop = [], asyncio.Event(), asyncio.get_running_loop()
        class Delayed(Query):
            def __init__(self, page):
                super().__init__(page)
                self.ready = False
                self.partial_result = replace(page, cells=page.cells[:1])
                captured.append(self)
                loop.call_soon_threadsafe(started.set)
            def read(self):
                return self.result if self.ready else None
        self.fx.query_factory = lambda page: Delayed(page) if page.fields_count == len(national._FORMATION_COUNTRIES) else Query(page)
        task = asyncio.create_task(self.fx.api.formation_candidates())
        await asyncio.wait_for(started.wait(), 1)
        pending = await task
        self.assertIsInstance(pending, PendingOperation)
        self.assertIs(pending.pending_queries[0], captured[0])
        self.assertEqual(len(self.fx.calls), 2)
        captured[0].ready = True
        self.fx.query_factory = Query
        result = await pending.resume(1)
        self.assertTrue(result["complete"])
        self.assertEqual(len(self.fx.calls), 4)
        self.assertEqual(result["unification_candidates"][0]["rows"][0]["formation_name"], "Union")
        self.assertEqual(executor.pending_queries, [])

    async def test_invalid_formation_details_flag_does_not_submit_native_read(self):
        for details in (None, 0, 1, "yes"):
            with self.assertRaises(ValueError):
                await self.fx.api.formation_candidates(details=details)
        self.assertEqual(self.fx.calls, [])

    async def test_source_and_selected_state_batches_use_64_row_pages(self):
        self.fx.sources["Country.AccessStates"] = [{"State.GetID": value} for value in range(130)]
        result = await self.fx.api.colonies()
        self.assertTrue(result["complete"])
        self.assertEqual([row["id"] for row in result["rows"]], list(range(130)))
        self.assertEqual([call[2]["start"] for call in self.fx.calls[:3]], [0, 64, 128])
        self.assertEqual([len(call[1]) for call in self.fx.calls[3:]], [64, 64, 2])
        self.assertTrue(all(len(call[0]) <= 32 and call[2]["limit"] == 64 for call in self.fx.calls))

    async def test_cleanup_warning_keeps_primary_result_and_original_query(self):
        class Cleanup(Query):
            cleanup_error = OSError("copy release failed")
        self.fx.query_factory = Cleanup
        result = await self.fx.api.national_modifiers()
        self.assertTrue(result["complete"])
        warnings = [item for item in self.fx.ctx.diagnostics if "cleanup_error" in item]
        self.assertEqual(len(warnings), 3)
        self.assertEqual(len(self.fx.retained), 3)
        self.assertTrue(all(item["result"] in result["native_pages"] for item in warnings))
        self.assertEqual(result["entries"]["rows"][0]["value"], Decimal("-0.25"))

    async def test_pause_idempotent_false_does_not_generate_a_command(self):
        command = AsyncMock()
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            result = await self.fx.api.set_colonization_paused(0, False)
        self.assertEqual(result["status"], "unchanged")
        self.assertFalse(result["submitted"])
        self.assertIsNone(result["accepted"])
        self.assertTrue(result["effect_observed"])
        command.assert_not_awaited()
        self.assertEqual(self.fx.calls[0][0], ("State.IsColonizationPaused",))
        self.assertEqual(len(self.fx.calls), 1)

    async def test_pause_and_resume_use_one_selected_producer_and_actual_readback(self):
        receipts = (object(),)
        async def execute(api, label, expression, bindings, **kwargs):
            self.assertIs(api, self.fx.api)
            self.assertEqual(label, "set_colonization_paused")
            self.assertEqual(expression, "State.TogglePauseColonization")
            self.assertEqual([value.domain for value in bindings], ["Country", "State"])
            self.assertEqual(bindings[1].object, 0)
            self.assertEqual(kwargs, {"actor_id": self.fx.country_id, "perform": False})
            self.fx.defaults["State.IsColonizationPaused"] = not self.fx.defaults["State.IsColonizationPaused"]
            return {"status": "accepted", "accepted": True, "native_results": receipts}
        command = AsyncMock(side_effect=execute)
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            paused = await self.fx.api.set_colonization_paused({"id": 0}, True)
            resumed = await self.fx.api.set_colonization_paused(0, False)
        self.assertEqual(command.await_count, 2)
        self.assertIs(paused["after"]["paused"], True)
        self.assertIs(resumed["after"]["paused"], False)
        self.assertTrue(paused["effect_observed"])
        self.assertTrue(resumed["effect_observed"])
        self.assertIs(paused["native_results"], receipts)
        self.assertEqual(len(self.fx.calls), 4)
        self.assertTrue(all(call[0] == ("State.IsColonizationPaused",) for call in self.fx.calls))
        self.assertEqual(self.fx.performs, ["politics.set_colonization_paused"] * 2)

    async def test_unknown_initial_paused_state_does_not_authorize_a_toggle(self):
        self.fx.transform = lambda page: replace(page, error=50)
        command = AsyncMock()
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            result = await self.fx.api.set_colonization_paused(0, True)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["submitted"])
        self.assertIsNone(result["accepted"])
        self.assertEqual(result["before"]["native_pages"][0].error, 50)
        command.assert_not_awaited()

    async def test_native_rejection_retains_validation_without_toggle_or_readback(self):
        validation = NS(error=0, validated=False, reason="Native colonization rule")
        command = AsyncMock(return_value={"status": "rejected", "accepted": False,
                            "validation": validation, "reason": validation.reason})
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            result = await self.fx.api.set_colonization_paused(0, True)
        self.assertFalse(result["accepted"])
        self.assertIs(result["validation"], validation)
        self.assertEqual(result["reason"], validation.reason)
        self.assertIsNone(result["after"])
        self.assertEqual(len(self.fx.calls), 1)
        command.assert_awaited_once()

    async def test_acceptance_without_effect_does_not_replay_command(self):
        command = AsyncMock(return_value={"status": "accepted", "accepted": True})
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            result = await self.fx.api.set_colonization_paused(0, True)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["effect_observed"])
        self.assertIs(result["after"]["paused"], False)
        command.assert_awaited_once()

    async def test_after_readback_failure_does_not_suppress_native_acceptance(self):
        error, receipts = OSError("native readback transport failure"), (object(),)
        async def execute(*args, **kwargs):
            def fail(page):
                raise error
            self.fx.transform = fail
            return {"accepted": True, "status": "accepted", "native_results": receipts}
        command = AsyncMock(side_effect=execute)
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            result = await self.fx.api.set_colonization_paused(0, True)
        self.assertTrue(result["accepted"])
        self.assertIs(result["native_results"], receipts)
        self.assertIs(result["readback_error"], error)
        self.assertIsNone(result["effect_observed"])
        command.assert_awaited_once()

    async def test_abandon_uses_original_producer_and_observes_actual_state(self):
        async def execute(api, label, expression, bindings, **kwargs):
            self.assertEqual(label, "abandon_colony")
            self.assertEqual(expression, "State.AbandonColony")
            self.assertEqual(bindings[1].object, self.fx.state_id)
            self.fx.defaults["State.IsBeingColonized"] = False
            self.fx.defaults["State.IsGrowingColony"] = False
            return {"accepted": True, "status": "accepted"}
        command = AsyncMock(side_effect=execute)
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            result = await self.fx.api.abandon_colony(self.fx.state_id)
        self.assertTrue(result["before"]["is_being_colonized"])
        self.assertFalse(result["after"]["is_being_colonized"])
        self.assertTrue(result["effect_observed"])
        self.assertEqual(len(self.fx.calls), 2)
        command.assert_awaited_once()

    async def test_vanished_state_observation_survives_missing_owner_getter(self):
        async def execute(*args, **kwargs):
            self.fx.defaults["State.IsValid"] = False
            self.fx.transform = lambda page: replace(page, cells=tuple(
                replace(value, error=50) if value.field == 3 else value for value in page.cells))
            return {"accepted": True, "status": "accepted"}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)):
            result = await self.fx.api.abandon_colony(self.fx.state_id)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["effect_observed"])
        self.assertFalse(result["readback_complete"])
        self.assertIs(result["after"]["is_valid"], False)
        self.assertIn("50", result["after"]["reasons"]["fields"]["owner_id"])

    async def test_native_abandon_acceptance_without_observed_loss_stays_false(self):
        command = AsyncMock(return_value={"accepted": True, "status": "accepted"})
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            result = await self.fx.api.abandon_colony(self.fx.state_id)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["effect_observed"])
        self.assertEqual(result["after"]["owner_id"], self.fx.country_id)
        command.assert_awaited_once()

    async def test_invalid_action_selector_and_desired_state_fail_before_native_calls(self):
        for target in (True, -1, 0xffffffff, "Colony", {}, {"source_index": 0}):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    await self.fx.api.set_colonization_paused(target)
                with self.assertRaises(ValueError):
                    await self.fx.api.abandon_colony(target)
        for desired in (0, 1, None, "yes"):
            with self.assertRaises(ValueError):
                await self.fx.api.set_colonization_paused(0, desired)
        self.assertEqual(self.fx.calls, [])

    async def test_concurrent_identical_pause_intents_submit_only_one_toggle(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def execute(*args, **kwargs):
            entered.set()
            await release.wait()
            self.fx.defaults["State.IsColonizationPaused"] = True
            return {"accepted": True, "status": "accepted"}
        command = AsyncMock(side_effect=execute)
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            first = asyncio.create_task(self.fx.api.set_colonization_paused(0, True))
            second = asyncio.create_task(self.fx.api.set_colonization_paused(0, True))
            await asyncio.wait_for(entered.wait(), 1)
            release.set()
            outcomes = await asyncio.gather(first, second)
        self.assertEqual([result["status"] for result in outcomes], ["accepted", "unchanged"])
        command.assert_awaited_once()

    async def test_pending_after_readback_retains_once_only_command(self):
        executor = SerializedQueryExecutor(timeout=.01, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query, self.fx.ctx.perform = executor.query, executor.perform
        self.fx.ctx._remember = executor._remember
        captured, started, loop = [], asyncio.Event(), asyncio.get_running_loop()
        class Delayed(Query):
            def __init__(self, result):
                super().__init__(result)
                self.ready = False
                captured.append(self)
                loop.call_soon_threadsafe(started.set)
            def read(self):
                return self.result if self.ready else None
        async def execute(*args, **kwargs):
            self.fx.defaults["State.IsColonizationPaused"] = True
            self.fx.query_factory = Delayed
            return {"accepted": True, "status": "accepted"}
        command = AsyncMock(side_effect=execute)
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            task = asyncio.create_task(self.fx.api.set_colonization_paused(0, True))
            await asyncio.wait_for(started.wait(), 1)
            pending = await task
            self.assertIsInstance(pending, PendingOperation)
            self.assertTrue(self.fx.api._mutation_lock.locked())
            captured[0].ready = True
            result = await pending.resume(1)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["effect_observed"])
        self.assertFalse(self.fx.api._mutation_lock.locked())
        self.assertEqual(len(self.fx.calls), 2)
        command.assert_awaited_once()

    async def test_formation_choices_retain_original_cpdx_values_and_batch_descriptions(self):
        backend = self.formation_native_source([{"CountryFormation.GetName": "Choice A"},
                                                {"CountryFormation.GetName": "Choice B"}])
        with patch.object(commands, "request", side_effect=AssertionError("read generated a command")):
            catalog = await self.fx.api.formation_choices()
        self.assertIsInstance(catalog, national.OwnedChoices)
        self.assertEqual([choice["name"] for choice in catalog], ["Choice A", "Choice B"])
        self.assertEqual([choice["source_index"] for choice in catalog], [0, 1])
        self.assertTrue(all(choice.native_result.type_id == 40 and
            choice.native_result.native_type_name == ".?AU?$SAnyType@V?$CPdxPtr@VCDataCountryFormation@@@@@PdxAnyImpl@@"
            for choice in catalog))
        self.assertTrue(catalog.metadata["read"]["complete"])
        self.assertEqual(catalog.metadata["read"]["country_id"], self.fx.country_id)
        self.assertEqual(catalog.owner.values, [100, 200, 201])
        self.assertIn(catalog.owner, self.fx.ctx.owner_pool.snapshot())
        self.assertEqual(backend.released, [])
        self.assertEqual(len(self.fx.calls), 1)
        fields, rows, options = self.fx.calls[0]
        self.assertIsNone(options["source_expression"])
        self.assertEqual([row[1].object for row in rows], [200, 201])
        self.assertTrue(all(row[1].retained for row in rows))
        self.assertTrue(all("Command" not in expression and ".FormCountry(" not in expression for expression in fields))
        self.assertEqual(self.fx.performs, ["politics.formation_choices"])
        closed = await catalog.close()
        self.assertTrue(closed["closed"])
        self.assertNotIn(catalog.owner, self.fx.ctx.owner_pool.snapshot())
        self.assertEqual([handle for handle, _ in backend.released], [201, 200, 100])
        self.assertEqual(backend.live, set())
        await catalog.close()
        self.assertEqual(len(backend.released), 3)

    async def test_formation_choices_description_batches_do_not_reselect_source(self):
        backend = self.formation_native_source([{"CountryFormation.GetName": str(index)} for index in range(130)])
        catalog = await self.fx.api.formation_choices()
        self.assertEqual(len(catalog), 130)
        self.assertEqual([len(call[1]) for call in self.fx.calls], [64, 64, 2])
        self.assertEqual(sum(call[0] == "source" for call in backend.calls), 1)
        self.assertEqual(sum(call[0] == "count" for call in backend.calls), 1)
        self.assertEqual(sum(call[0] == "element" for call in backend.calls), 130)
        self.assertEqual(catalog[-1]["source_index"], 129)
        self.assertEqual(catalog[-1]._binding.object, 329)
        await catalog.close()
        self.assertEqual(len(backend.released), 131)

    async def test_empty_owned_formation_source_still_has_explicit_close(self):
        backend = self.formation_native_source([])
        catalog = await self.fx.api.formation_choices()
        self.assertEqual(len(catalog), 0)
        self.assertTrue(catalog.metadata["read"]["complete"])
        self.assertEqual(self.fx.calls, [])
        self.assertEqual(backend.live, {100})
        await catalog.close()
        self.assertEqual([handle for handle, _ in backend.released], [100])

    async def test_known_raw_element_failure_exposes_catalog_without_retagging_or_cleanup(self):
        backend = self.formation_native_source(raw_element=True)
        with self.assertRaisesRegex(RuntimeError, "ownership is not established") as raised:
            await self.fx.api.formation_choices()
        catalog = raised.exception.choices
        self.assertIs(catalog.metadata["read"]["failure"], raised.exception)
        self.assertEqual(len(catalog), 0)
        self.assertEqual(catalog.owner.values, [100, 200])
        original = next(native for _, native in catalog.owner.operations
                        if getattr(native, "value_handle", 0) == 200)
        self.assertEqual(original.value.native_type_name, ".?AU?$SAnyType@PEAVCDataCountryFormation@@@PdxAnyImpl@@")
        self.assertIs(catalog.owner.operations[-1][1], original)
        self.assertFalse(getattr(catalog.owner, "retain_unknown", False))
        self.assertEqual(backend.released, [])
        self.assertEqual(self.fx.calls, [])
        self.assertNotIn("unsupported", str(raised.exception).lower())
        await catalog.close()
        self.assertTrue(catalog.closed)
        self.assertEqual([handle for handle, _ in backend.released], [200, 100])

    async def test_known_non_list_root_failure_preserves_actual_native_result(self):
        backend = self.formation_native_source(raw_root=True)
        with self.assertRaisesRegex(RuntimeError, "did not return an owned list") as raised:
            await self.fx.api.formation_choices()
        catalog = raised.exception.choices
        self.assertEqual(len(catalog), 0)
        self.assertIsInstance(catalog.owner.operations[0][1].value, data.NativeObject)
        self.assertFalse(getattr(catalog.owner, "retain_unknown", False))
        self.assertEqual(backend.released, [])
        self.assertEqual(self.fx.calls, [])
        await catalog.close()
        self.assertTrue(catalog.closed)
        self.assertEqual([handle for handle, _ in backend.released], [100])

    async def test_unknown_source_read_failure_pins_original_catalog_graph(self):
        error = OSError("element dispatch outcome unknown")
        backend = self.formation_native_source([{}, {}], fail_element=1, unknown_error=error)
        with self.assertRaises(OSError) as raised:
            await self.fx.api.formation_choices()
        self.assertIs(raised.exception, error)
        catalog = error.choices
        self.assertTrue(catalog.owner.retain_unknown)
        result = await catalog.close()
        self.assertFalse(result["closed"])
        self.assertEqual(catalog.owner.values, [100, 200])
        self.assertEqual(backend.released, [])

    async def test_unknown_description_copy_failure_pins_choices_until_original_owner_settles(self):
        error = OSError("copied description read unknown")
        backend = self.formation_native_source()
        def fail(page):
            raise error
        self.fx.transform = fail
        with self.assertRaises(OSError) as raised:
            await self.fx.api.formation_choices()
        self.assertIs(raised.exception, error)
        catalog = error.choices
        self.assertTrue(catalog.owner.retain_unknown)
        self.assertEqual(catalog.owner.values, [100, 200])
        self.assertFalse((await catalog.close())["closed"])
        self.assertIn(catalog.owner, self.fx.ctx.owner_pool.snapshot())
        self.assertEqual(backend.released, [])

    async def test_partial_choice_descriptions_keep_original_selectors_without_fake_ids(self):
        backend = self.formation_native_source([{}, {}])
        self.fx.transform = lambda page: replace(page, cells=page.cells[:page.fields_count], row_count=1)
        catalog = await self.fx.api.formation_choices()
        self.assertEqual(len(catalog), 2)
        self.assertFalse(catalog.metadata["read"]["complete"])
        self.assertIsNone(catalog[1]["name"])
        self.assertIn("did not copy", catalog[1]["reasons"]["row"])
        self.assertNotIn("id", catalog[1])
        self.assertEqual(catalog[1]._binding.object, 201)
        self.assertTrue(catalog[1]._binding.retained)
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_known_native_source_error_is_not_marked_unknown_or_emptied(self):
        backend = self.formation_native_source()
        native_error = NS(error=50, action=data.Action.LIST_COUNT, total=7, value_handle=0)
        with patch.object(data, "list_count", return_value=Query(native_error)):
            with self.assertRaises(run_mod.KnownResultError) as raised:
                await self.fx.api.formation_choices()
        catalog = raised.exception.choices
        self.assertIs(raised.exception.result, native_error)
        self.assertFalse(getattr(catalog.owner, "retain_unknown", False))
        self.assertIs(catalog.owner.operations[-1][1], native_error)
        self.assertEqual(backend.released, [])
        await catalog.close()
        self.assertTrue(catalog.closed)
        self.assertEqual([handle for handle, _ in backend.released], [100])

    async def test_known_description_native_error_keeps_usable_original_choice_binding(self):
        backend = self.formation_native_source([{"CountryFormation.GetName": "Copied beside error"}])
        self.fx.transform = lambda page: replace(page, error=13)
        catalog = await self.fx.api.formation_choices()
        self.assertFalse(catalog.metadata["read"]["complete"])
        self.assertEqual(catalog.metadata["read"]["native_pages"][0].error, 13)
        self.assertEqual(catalog[0]["name"], "Copied beside error")
        self.assertEqual(catalog[0]._binding.object, 200)
        self.assertFalse(getattr(catalog.owner, "retain_unknown", False))
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_pending_choice_description_resumes_original_owner_without_source_replay(self):
        backend = self.formation_native_source()
        executor = SerializedQueryExecutor(timeout=.01, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query, self.fx.ctx.perform = executor.query, executor.perform
        self.fx.ctx._remember = executor._remember
        captured, started, loop = [], asyncio.Event(), asyncio.get_running_loop()
        class Delayed(Query):
            def __init__(self, page):
                super().__init__(page)
                self.ready = False
                self.partial_result = replace(page, cells=page.cells[:1])
                captured.append(self)
                loop.call_soon_threadsafe(started.set)
            def read(self):
                return self.result if self.ready else None
        self.fx.query_factory = Delayed
        task = asyncio.create_task(self.fx.api.formation_choices())
        await asyncio.wait_for(started.wait(), 1)
        pending = await task
        self.assertIsInstance(pending, PendingOperation)
        self.assertIs(pending.pending_queries[0], captured[0])
        self.assertEqual(backend.live, {100, 200})
        captured[0].ready = True
        catalog = await pending.resume(1)
        self.assertEqual(len(catalog), 1)
        self.assertTrue(catalog.metadata["read"]["complete"])
        self.assertEqual(sum(call[0] == "source" for call in backend.calls), 1)
        self.assertEqual(len(self.fx.calls), 1)
        self.assertEqual(backend.released, [])
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_pending_form_country_keeps_choice_lease_and_single_selected_intent(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        executor = SerializedQueryExecutor(timeout=.01, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query, self.fx.ctx.perform = executor.query, executor.perform
        self.fx.ctx._remember = executor._remember
        started, ready = asyncio.Event(), asyncio.Event()
        async def execute(api, label, expression, selector, **kwargs):
            operation = NS(ctx=self.fx.ctx, status="working")
            await selector(operation)
            started.set()
            await ready.wait()
            operation.status = "complete"
            return {"accepted": True, "status": "accepted", "completed": False}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            task = asyncio.create_task(self.fx.api.form_country(catalog[0]))
            await asyncio.wait_for(started.wait(), 1)
            pending = await task
            self.assertIsInstance(pending, PendingOperation)
            self.assertEqual(catalog.active_leases, 1)
            with self.assertRaises(ChoicesBusyError):
                await catalog.close()
            self.assertEqual(backend.released, [])
            ready.set()
            result = await pending.resume(1)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(catalog.active_leases, 0)
        self.assertEqual(sum(call[0] == "source" for call in backend.calls), 1)
        command.assert_awaited_once()
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_form_country_borrows_original_choice_not_ordinal_or_later_source(self):
        backend = self.formation_native_source([{"CountryFormation.GetName": "Chosen original"}])
        catalog = await self.fx.api.formation_choices()
        original_queries = list(backend.calls)
        backend.records[:] = [{"CountryFormation.GetName": "A different current source row"}]
        captured, receipts = [], (object(),)
        async def execute(api, label, expression, selector, **kwargs):
            operation = NS(ctx=self.fx.ctx, status="working")
            bindings = await selector(operation)
            captured.append((operation, bindings))
            self.assertEqual(label, "form_country")
            self.assertEqual(expression, "CountryFormation.FormCountry(Country.Self)")
            self.assertEqual(kwargs, {"actor_id": self.fx.country_id, "perform": False})
            self.assertEqual(bindings[0].object, self.fx.country_id)
            self.assertEqual(bindings[0].domain, "Country")
            self.assertEqual(bindings[1].object, 200)
            self.assertTrue(bindings[1].retained)
            operation.status = "complete"
            self.fx.defaults["Country.GetName"] = "Actual resulting identity"
            return {"accepted": True, "status": "accepted", "native_results": receipts, "completed": False}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            result = await self.fx.api.form_country(catalog[0])
        command.assert_awaited_once()
        self.assertEqual(backend.calls, original_queries)
        self.assertEqual(result["formation"]["name"], "Chosen original")
        self.assertEqual(result["after_identity"]["name"], "Actual resulting identity")
        self.assertIs(result["native_results"], receipts)
        self.assertFalse(result["completed"])
        self.assertEqual(catalog.active_leases, 0)
        self.assertFalse(catalog.closed)
        self.assertEqual(backend.released, [])
        self.assertEqual(self.fx.performs, ["politics.formation_choices", "politics.form_country"])
        await catalog.close()

    async def test_form_country_rejection_keeps_native_reason_and_catalog_open(self):
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        validation = NS(error=0, validated=False, reason="native formation condition")
        async def execute(api, label, expression, selector, **kwargs):
            operation = NS(ctx=self.fx.ctx, status="working")
            await selector(operation)
            operation.status = "complete"
            return {"accepted": False, "status": "rejected", "validation": validation, "reason": validation.reason}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)):
            result = await self.fx.api.form_country(catalog[0])
        self.assertIs(result["validation"], validation)
        self.assertEqual(result["reason"], validation.reason)
        self.assertIsNone(result["after_identity"])
        self.assertEqual(catalog.active_leases, 0)
        self.assertEqual(len(self.fx.calls), 1)
        self.assertEqual(backend.released, [])
        await catalog.close()

    async def test_form_country_unknown_command_keeps_source_lease_and_primary_exception(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        operation = NS(ctx=self.fx.ctx, status="working")
        error = OSError("original form command dispatch unknown")
        async def execute(api, label, expression, selector, **kwargs):
            await selector(operation)
            raise error
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            with self.assertRaises(OSError) as raised:
                await self.fx.api.form_country(catalog[0])
        self.assertIs(raised.exception, error)
        self.assertEqual(catalog.active_leases, 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertEqual(backend.released, [])
        command.assert_awaited_once()
        operation.status = "complete"  # fixture: original operation settled, not a replay
        await catalog.close()
        self.assertTrue(catalog.closed)

    async def test_form_country_closed_foreign_or_plain_choices_fail_without_command(self):
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock()) as command:
            for choice in (0, "Union", {"source_index": 0}, dict(catalog[0])):
                with self.assertRaises(TypeError):
                    await self.fx.api.form_country(choice)
            other_api = API(NS(perform=self.fx.perform))
            with self.assertRaises(ValueError):
                await other_api.form_country(catalog[0])
            await catalog.close()
            with self.assertRaises(RuntimeError):
                await self.fx.api.form_country(catalog[0])
            command.assert_not_awaited()
        self.assertEqual(len(backend.released), 2)

    async def test_form_country_readback_error_keeps_accepted_native_result(self):
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        error, receipts = OSError("identity readback unknown"), (object(),)
        async def execute(api, label, expression, selector, **kwargs):
            operation = NS(ctx=self.fx.ctx, status="working")
            await selector(operation)
            operation.status = "complete"
            def fail(page):
                raise error
            self.fx.transform = fail
            return {"accepted": True, "status": "accepted", "native_results": receipts}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            result = await self.fx.api.form_country(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertIs(result["native_results"], receipts)
        self.assertIs(result["readback_error"], error)
        self.assertIsNone(result["after_identity"])
        self.assertEqual(catalog.active_leases, 0)
        command.assert_awaited_once()
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_release_choices_use_exact_owned_country_creation_source_without_opening_ui(self):
        backend = self.formation_native_source([{"CountryCreation.GetName": "Released Nation"}],
                                                named_type="CountryCreation")
        with patch.object(commands, "request", side_effect=AssertionError("read generated or submitted a command")):
            catalog = await self.fx.api.release_choices()
        self.assertEqual(catalog.metadata["source_expression"], "Country.AccessReleasableCountries")
        self.assertEqual(catalog.metadata["selection"], {
            "actor_country_id": self.fx.country_id, "source_country_id": self.fx.country_id})
        self.assertEqual(catalog.metadata["filtering"], {
            "native_source_is_country_scoped": True, "python_row_filter_applied": False,
            "command_eligibility_filter_applied": False})
        self.assertTrue(catalog.metadata["read"]["complete"])
        self.assertEqual(catalog[0].native_type, "CountryCreation")
        self.assertEqual(catalog[0].native_result.type_id, 41)
        self.assertEqual(catalog[0].native_result.native_type_name,
                         ".?AU?$SAnyType@V?$CPdxPtr@VCDataCountryCreation@@@@@PdxAnyImpl@@")
        self.assertEqual(catalog[0]["name"], "Released Nation")
        self.assertIs(catalog[0]["can_release"], False)
        self.assertEqual(catalog[0]._binding.type_id, 41)
        self.assertEqual(catalog[0]._binding.object, 200)
        self.assertTrue(catalog[0]._binding.retained)
        self.assertNotIn("id", catalog[0])
        self.assertEqual(self.fx.performs, ["politics.release_choices"])
        self.assertEqual([call[1] for call in backend.calls if call[0] == "source"], ["Country.AccessReleasableCountries"])
        self.assertIn("CountryCreation.CanReleaseCountry(Country.Self)", self.fx.calls[0][0])
        self.assertTrue(all("ReleaseCountryWindow" not in expression and "PopupManager" not in expression
                            for expression in self.fx.calls[0][0]))
        self.assertEqual(backend.released, [])
        await catalog.close()
        self.assertTrue(catalog.closed)
        self.assertNotIn(catalog.owner, self.fx.ctx.owner_pool.snapshot())

    async def test_release_choices_duplicate_names_keep_distinct_original_bindings(self):
        backend = self.formation_native_source([{}, {}], named_type="CountryCreation")
        catalog = await self.fx.api.release_choices()
        self.assertEqual([choice["name"] for choice in catalog], ["Released Nation", "Released Nation"])
        self.assertEqual([choice._binding.object for choice in catalog], [200, 201])
        self.assertEqual([choice["source_index"] for choice in catalog], [0, 1])
        self.assertTrue(all("id" not in choice and "key" not in choice for choice in catalog))
        await catalog.close()
        self.assertEqual([handle for handle, _ in backend.released], [201, 200, 100])

    async def test_raw_release_choice_wrapper_stays_an_explicit_gap_not_retagged(self):
        backend = self.formation_native_source(named_type="CountryCreation", raw_element=True)
        with self.assertRaisesRegex(RuntimeError, "ownership is not established") as raised:
            await self.fx.api.release_choices()
        catalog = raised.exception.choices
        self.assertEqual(len(catalog), 0)
        self.assertEqual(catalog.owner.values, [100, 200])
        original = next(native for _, native in catalog.owner.operations
                        if getattr(native, "value_handle", 0) == 200)
        self.assertEqual(original.value.native_type_name, ".?AU?$SAnyType@PEAVCDataCountryCreation@@@PdxAnyImpl@@")
        self.assertIs(catalog.owner.operations[-1][1], original)
        self.assertFalse(getattr(catalog.owner, "retain_unknown", False))
        self.assertEqual(backend.released, [])
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_release_choice_partial_copy_does_not_change_actor_or_filter_rows(self):
        backend = self.formation_native_source([{}, {}], named_type="CountryCreation")
        self.fx.transform = lambda page: replace(page, error=13)
        catalog = await self.fx.api.release_choices()
        self.assertEqual(len(catalog), 2)
        self.assertFalse(catalog.metadata["read"]["complete"])
        self.assertEqual(catalog.metadata["read"]["native_pages"][0].error, 13)
        self.assertEqual(catalog.metadata["selection"]["actor_country_id"], self.fx.country_id)
        self.assertEqual(catalog.owner.actor, self.fx.country_id)
        self.assertFalse(catalog.metadata["filtering"]["python_row_filter_applied"])
        self.assertTrue(all(choice["can_release"] is False for choice in catalog))
        self.assertEqual([choice._binding.object for choice in catalog], [200, 201])
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_release_choice_cannot_be_sent_to_formation_producer(self):
        backend = self.formation_native_source(named_type="CountryCreation")
        catalog = await self.fx.api.release_choices()
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock()) as command:
            with self.assertRaises(TypeError):
                await self.fx.api.form_country(catalog[0])
            command.assert_not_awaited()
        await catalog.close()
        self.assertEqual(backend.live, set())


    async def test_colonization_options_copy_original_tooltip_without_candidate_commands(self):
        with patch.object(commands, "request", side_effect=AssertionError("options generated a command")):
            result = await self.fx.api.colonization_options(state_id=0)
        row, = result["rows"]
        self.assertEqual(row["id"], 0)
        self.assertEqual(row["requested_state_id"], 0)
        self.assertEqual(row["region_id"], 55)
        self.assertEqual(row["actor_country_id"], 0x99000009)
        self.assertEqual(row["colonize_tooltip"], "Original conditions: claim, interest, malaria")
        self.assertEqual(row["colonize_tooltip_raw"], "#bold Original conditions#!: claim, interest, malaria")
        self.assertFalse(result["command_validation_performed"])
        self.assertEqual(result["actor_source"], "GetPlayer")
        self.assertTrue(result["complete"])
        self.assertEqual(self.fx.country_resolutions, 0)
        self.assertEqual(self.fx.performs, ["politics.colonization_options"])
        fields, bindings, kwargs = self.fx.calls[0]
        self.assertIsNone(kwargs["source_expression"])
        self.assertEqual(len(bindings[0]), 1)
        self.assertEqual(bindings[0][0].domain, "State")
        self.assertEqual(bindings[0][0].object, 0)
        self.assertIn("State.GetStateRegion.GetColonizeTooltip", fields)
        self.assertNotIn("State.GetStateRegion.Colonize", fields)
        self.assertFalse(any(expression.startswith("IsValid(") for expression in fields))
        self.assertNotIn("source_index", row)

    async def test_colonization_options_batch_only_explicit_state_ids(self):
        ids = [1000 + index * 7 for index in range(130)]
        result = await self.fx.api.colonization_options(states=ids)
        self.assertEqual([row["id"] for row in result["rows"]], ids)
        self.assertEqual(result["requested_state_ids"], ids)
        self.assertEqual(result["requested_state_count"], 130)
        self.assertEqual([len(call[1]) for call in self.fx.calls], [64, 64, 2])
        self.assertTrue(all(call[2]["source_expression"] is None for call in self.fx.calls))
        self.assertTrue(all(len(row) == 1 and row[0].domain == "State" for call in self.fx.calls for row in call[1]))
        self.assertTrue(all(row["region_id"] == 55 for row in result["rows"]))
        self.assertEqual(self.fx.country_resolutions, 0)

    async def test_colonization_options_retain_partial_native_tooltip_and_page_error(self):
        tooltip_index = next(index for index, field in enumerate(national._COLONIZATION_OPTIONS)
                             if field.name == "colonize_tooltip")
        self.fx.transform = lambda page: replace(page, error=13, cells=tuple(
            replace(value, error=50, total=value.total + 5) if value.field == tooltip_index else value
            for value in page.cells))
        result = await self.fx.api.colonization_options(0)
        self.assertFalse(result["complete"])
        row, = result["rows"]
        self.assertEqual(row["id"], 0)
        self.assertIn("Original conditions", row["colonize_tooltip"])
        self.assertIn("50", row["reasons"]["colonize_tooltip"])
        self.assertIn("incomplete", row["reasons"]["colonize_tooltip"])
        self.assertEqual(result["native_pages"][0].error, 13)

    async def test_colonization_options_missing_row_keeps_request_not_fake_native_id(self):
        self.fx.transform = lambda page: replace(page, cells=(), row_count=0)
        result = await self.fx.api.colonization_options(0)
        self.assertFalse(result["complete"])
        row, = result["rows"]
        self.assertEqual(row["requested_state_id"], 0)
        self.assertIsNone(row["id"])
        self.assertIsNone(row["region_id"])
        self.assertIn("did not copy", row["reasons"]["row"])

    async def test_colonization_options_empty_or_invalid_explicit_selection_is_not_world_search(self):
        result = await self.fx.api.colonization_options(states=[])
        self.assertEqual(result["status"], "empty_selection")
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["native_pages"], [])
        for kwargs in ({}, {"state_id": 0, "states": [1]}, {"states": "ABC"}, {"states": 5},
                       {"state_id": True}, {"states": [{"source_index": 0}]}, {"states": [1, -1]}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    await self.fx.api.colonization_options(**kwargs)
        self.assertEqual(self.fx.calls, [])

    async def test_colonize_uses_same_callback_chained_producer_and_original_player_states(self):
        async def execute(api, label, expression, bindings, **kwargs):
            self.assertEqual(label, "colonize")
            self.assertEqual(expression, "State.GetStateRegion.Colonize")
            self.assertEqual(kwargs, {"perform": False})
            state, = bindings
            self.assertEqual(state.domain, "State")
            self.assertEqual(state.object, self.fx.state_id)
            self.assertFalse(state.retained)
            self.fx.sources["GetPlayer.AccessStates"].append({
                "State.GetID": 0, "State.GetOwner.GetID": 0x99000009,
                "State.IsBeingColonized": True, "State.IsGrowingColony": True})
            return {"accepted": True, "status": "accepted", "completed": False}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            result = await self.fx.api.colonize(self.fx.state_id)
        command.assert_awaited_once()
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["actor_source"], "GetPlayer")
        self.assertEqual(result["requested_state_id"], self.fx.state_id)
        self.assertEqual(result["after"]["target_state"]["id"], self.fx.state_id)
        self.assertEqual([row["id"] for row in result["before"]["player_states"]["rows"]], [11])
        self.assertEqual([row["id"] for row in result["after"]["player_states"]["rows"]], [11, 0])
        self.assertTrue(result["after"]["player_states"]["rows"][1]["is_growing_colony"])
        self.assertEqual([call[2]["source_expression"] for call in self.fx.calls],
                         [None, "GetPlayer.AccessStates", None, "GetPlayer.AccessStates"])
        self.assertEqual(self.fx.calls[1][1], ((),))
        self.assertEqual(self.fx.calls[3][1], ((),))
        self.assertEqual(self.fx.country_resolutions, 0)
        self.assertEqual(self.fx.performs, ["politics.colonize"])

    async def test_colonize_acceptance_without_observed_change_is_not_completed(self):
        command = AsyncMock(return_value={"accepted": True, "status": "accepted", "completed": False})
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            result = await self.fx.api.colonize(0)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["before"]["player_states"]["rows"], result["after"]["player_states"]["rows"])
        self.assertNotIn("colonization_completed", result)
        self.assertNotIn("created_state_id", result)
        command.assert_awaited_once()

    async def test_colonize_native_rejection_is_preserved_without_readback_or_scan(self):
        validation = NS(error=0, validated=False, reason="native colonial claim restriction")
        command = AsyncMock(return_value={"accepted": False, "status": "rejected",
                                          "validation": validation, "reason": validation.reason})
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            result = await self.fx.api.colonize(0)
        self.assertFalse(result["accepted"])
        self.assertIs(result["validation"], validation)
        self.assertEqual(result["reason"], validation.reason)
        self.assertIsNone(result["after"])
        self.assertEqual(len(self.fx.calls), 2)
        command.assert_awaited_once()

    async def test_colonize_after_player_read_failure_keeps_target_copy_and_native_acceptance(self):
        error, receipts = OSError("player-state readback unknown"), (object(),)
        async def execute(*args, **kwargs):
            def fail_player(page):
                if page.fields_count == len(national._PLAYER_COLONY_STATES):
                    raise error
                return page
            self.fx.transform = fail_player
            return {"accepted": True, "status": "accepted", "completed": False, "native_results": receipts}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            result = await self.fx.api.colonize(0)
        self.assertTrue(result["accepted"])
        self.assertIs(result["native_results"], receipts)
        self.assertIs(result["readback_error"], error)
        self.assertEqual(result["after"]["target_state"]["id"], 0)
        self.assertIsNone(result["after"]["player_states"])
        command.assert_awaited_once()

    async def test_colonize_before_transport_error_preserves_partial_observation_without_submit(self):
        error = OSError("original target read unknown")
        def fail(page):
            raise error
        self.fx.transform = fail
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock()) as command:
            with self.assertRaises(OSError) as raised:
                await self.fx.api.colonize(0)
            command.assert_not_awaited()
        self.assertIs(raised.exception, error)
        self.assertEqual(error.colonization_observation, {"target_state": None, "player_states": None})

    async def test_colonize_full_state_id_validation_precedes_native_queries(self):
        for selected in (True, -1, 0xffffffff, "State name", {}, {"source_index": 0}):
            with self.assertRaises(ValueError):
                await self.fx.api.colonize(selected)
        self.assertEqual(self.fx.calls, [])

    async def test_colonize_known_partial_before_read_does_not_replace_native_eligibility(self):
        self.fx.transform = lambda page: replace(page, error=13)
        validation = NS(error=0, validated=False, reason="native command decides eligibility")
        command = AsyncMock(return_value={"accepted": False, "status": "rejected",
                                          "validation": validation, "reason": validation.reason})
        with patch("pdx_runtime.v3_game._political_actions.selected_command", command):
            result = await self.fx.api.colonize(0)
        self.assertFalse(result["before"]["target_state"]["complete"])
        self.assertEqual(result["before"]["target_state"]["id"], 0)
        self.assertIs(result["validation"], validation)
        command.assert_awaited_once()

    async def test_pending_colonize_player_readback_keeps_original_copies_without_replay(self):
        executor = SerializedQueryExecutor(timeout=.01, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query, self.fx.ctx.perform = executor.query, executor.perform
        self.fx.ctx._remember = executor._remember
        captured, started, loop = [], asyncio.Event(), asyncio.get_running_loop()
        # Only the after-player source page is delayed; before data is already copied.
        class Delayed(Query):
            def __init__(self, page):
                super().__init__(page)
                self.ready = False
                self.partial_result = replace(page, cells=page.cells[:1])
                captured.append(self)
                loop.call_soon_threadsafe(started.set)
            def read(self):
                return self.result if self.ready else None
        async def execute(*args, **kwargs):
            self.fx.sources["GetPlayer.AccessStates"].append({"State.GetID": 0, "State.IsGrowingColony": True})
            self.fx.query_factory = lambda page: Delayed(page) if page.fields_count == len(national._PLAYER_COLONY_STATES) else Query(page)
            return {"accepted": True, "status": "accepted", "completed": False}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            task = asyncio.create_task(self.fx.api.colonize(0))
            await asyncio.wait_for(started.wait(), 1)
            pending = await task
            self.assertIsInstance(pending, PendingOperation)
            self.assertIs(pending.pending_queries[0], captured[0])
            self.fx.sources["GetPlayer.AccessStates"] = [{"State.GetID": 999}]
            captured[0].ready = True
            result = await pending.resume(1)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual([row["id"] for row in result["after"]["player_states"]["rows"]], [11, 0])
        self.assertEqual(len(self.fx.calls), 4)
        command.assert_awaited_once()
        self.assertEqual(executor.pending_queries, [])

    async def test_colonize_real_selected_helper_one_native_chain_validate_submit(self):
        self.fx.ctx.adapters["commands"] = "fake-commands"
        validation = NS(error=0, validated=True, reason="")
        submission = NS(error=0, accepted=True, command=0)
        events, released = [], []
        fx = self.fx
        class Owner:
            world, command, pending, attempt = fx.world, 0x3000, None, None
            def validate(self, capacity):
                events.append(("validate", capacity))
                self.attempt = NS(outcome="owned")
                return Query(validation)
            def submit(self, flags=4):
                events.append(("submit", flags))
                self.command, self.attempt = 0, NS(outcome="consumed")
                fx.sources["GetPlayer.AccessStates"].append({"State.GetID": 0, "State.IsGrowingColony": True})
                return Query(submission)
            def serialize(self, *args, **kwargs):
                raise AssertionError("selected colonization must not serialize")
        owner = Owner()
        carrier = data.NativeDataCommand(0x1000, 0x1100, 0x1018, 900, "fixture", fx.world)
        def evaluate(native, expression, bindings, **kwargs):
            self.assertEqual(expression, "State.GetStateRegion.Colonize")
            state, = bindings
            self.assertEqual(state.domain, "State")
            self.assertEqual(state.object, fx.state_id)
            self.assertFalse(state.retained)
            events.append(("produce", expression))
            return Query(NS(error=0, action=data.Action.EVALUATE, value_handle=900, value=carrier))
        def release(native, handle, **kwargs):
            released.append(handle)
            return Query(NS(error=0, action=data.Action.RELEASE, value_handle=0))
        copied = NS(error=0, action=data.Action.COMMAND_VALUE, value_handle=900, value=carrier)
        cloned = NS(error=0, action=commands.Action.CLONE, cloned_command=0x3000, type_token=12345, world=fx.world)
        with patch.object(data, "evaluate", evaluate), \
             patch.object(data, "command_value", return_value=Query(copied)), \
             patch.object(data, "release", release), \
             patch.object(commands, "request", return_value=Query(cloned)) as request, \
             patch.object(commands.NativeCommand, "from_result", return_value=owner):
            result = await fx.api.colonize(fx.state_id)
        self.assertEqual([event[0] for event in events], ["produce", "validate", "submit"])
        self.assertEqual(released, [900])
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertIs(result["validation"], validation)
        self.assertIs(result["submission"], submission)
        self.assertEqual(result["after"]["target_state"]["id"], fx.state_id)
        self.assertEqual([row["id"] for row in result["after"]["player_states"]["rows"]], [11, 0])
        self.assertEqual(fx.country_resolutions, 0)
        self.assertEqual(fx.type_calls, [])
        request.assert_called_once()
        self.assertEqual(request.call_args.args[1], commands.Action.CLONE)
        action_runs = [value for value in fx.ctx.diagnostics if isinstance(value, run_mod.Run) and value.label == "colonize"]
        self.assertEqual(len(action_runs), 1)
        self.assertIsNone(action_runs[0].actor)
        self.assertNotIn(action_runs[0], fx.ctx.owner_pool.snapshot())

    async def test_support_snapshot_uses_original_choice_data_adapter_after_default_changes(self):
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        self.fx.ctx.adapters["data"] = "later-other-data.dll"
        async def execute(api, label, expression, selector, **kwargs):
            operation = NS(ctx=self.fx.ctx, status="working", adapters=dict(self.fx.ctx.adapters))
            await selector(operation)
            self.assertEqual(operation.adapters["data"], "fake")
            operation.status = "complete"
            return {"accepted": True, "status": "accepted"}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)):
            result = await self.fx.api.support_unification(catalog[0], 0)
        self.assertTrue(result["accepted"])
        self.assertTrue(all(options["adapter"] == "fake" for _, _, options in self.fx.calls))
        read_runs = [run for run in self.fx.ctx.diagnostics if isinstance(run, run_mod.Run)
                     and run.label.startswith("politics.support_unification.")]
        self.assertEqual(len(read_runs), 2)
        self.assertTrue(all(run.status == "complete" for run in read_runs))
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_support_unification_binds_only_candidate_and_uses_actual_global_player(self):
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        original_source_calls = list(backend.calls)
        receipts = (object(),)
        async def execute(api, label, expression, selector, **kwargs):
            operation = NS(ctx=self.fx.ctx, status="working")
            bindings = await selector(operation)
            self.assertEqual(label, "support_unification")
            self.assertEqual(expression, "CountryFormation.SupportCandidate(GetPlayer, Country.Self)")
            self.assertEqual(kwargs, {"perform": False})
            self.assertEqual(len(bindings), 2)
            self.assertEqual(bindings[0].domain, "Country")
            self.assertEqual(bindings[0].object, 0)
            self.assertTrue(bindings[1].retained)
            self.assertEqual(bindings[1].object, 200)
            self.fx.defaults["CountryFormation.IsSupportingCandidate(GetPlayer, Country.Self)"] = True
            self.fx.defaults["CountryFormation.IsSupportingAnyCandidate(GetPlayer)"] = True
            self.fx.defaults["CountryFormation.GetSupportedCandidateCountry(GetPlayer).GetID"] = 0
            self.fx.defaults["CountryFormation.GetSupportedCandidateCountry(GetPlayer).GetName"] = "Zero candidate"
            operation.status = "complete"
            return {"accepted": True, "status": "accepted", "completed": False, "native_results": receipts}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            result = await self.fx.api.support_unification(catalog[0], 0)
        command.assert_awaited_once()
        self.assertEqual(result["actor_source"], "GetPlayer")
        self.assertEqual(result["catalog_country_id"], self.fx.country_id)
        self.assertEqual(result["before"]["actor_country_id"], 0x99000009)
        self.assertEqual(result["before"]["candidate_country_id"], 0)
        self.assertFalse(result["before"]["supports_candidate"])
        self.assertTrue(result["after"]["supports_candidate"])
        self.assertEqual(result["after"]["supported_candidate_id"], 0)
        self.assertIs(result["native_results"], receipts)
        self.assertFalse(result["completed"])
        self.assertEqual(backend.calls, original_source_calls)
        self.assertEqual(catalog.active_leases, 0)
        self.assertEqual(self.fx.performs, ["politics.formation_choices", "politics.support_unification"])
        for fields, bindings, _ in self.fx.calls[1:]:
            self.assertEqual(sum(binding.domain == "Country" for binding in bindings[0]), 1)
            self.assertIn("GetPlayer.GetID", fields)
            self.assertIn("CountryFormation.GetSupportedCandidateCountry(GetPlayer).GetID", fields)
        await catalog.close()

    async def test_support_unification_native_repeat_can_withdraw_without_fake_completion(self):
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        candidate_id = 0x84000004
        self.fx.defaults["CountryFormation.IsSupportingCandidate(GetPlayer, Country.Self)"] = True
        self.fx.defaults["CountryFormation.IsSupportingAnyCandidate(GetPlayer)"] = True
        self.fx.defaults["CountryFormation.GetSupportedCandidateCountry(GetPlayer).GetID"] = candidate_id
        async def execute(api, label, expression, selector, **kwargs):
            operation = NS(ctx=self.fx.ctx, status="working")
            await selector(operation)
            self.fx.defaults["CountryFormation.IsSupportingCandidate(GetPlayer, Country.Self)"] = False
            self.fx.defaults["CountryFormation.IsSupportingAnyCandidate(GetPlayer)"] = False
            self.fx.defaults["CountryFormation.GetSupportedCandidateCountry(GetPlayer).GetID"] = 0xffffffff
            operation.status = "complete"
            return {"accepted": True, "status": "accepted", "completed": False}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            result = await self.fx.api.support_unification(catalog[0], candidate_id)
        self.assertTrue(result["before"]["supports_candidate"])
        self.assertFalse(result["after"]["supports_candidate"])
        self.assertFalse(result["after"]["supports_any_candidate"])
        self.assertEqual(result["after"]["supported_candidate_id"], 0xffffffff)
        self.assertIn("sentinel", result["after"]["reasons"]["fields"]["supported_candidate_id"])
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        command.assert_awaited_once()
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_support_rejection_preserves_before_and_native_validation_without_readback(self):
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        validation = NS(error=0, validated=False, reason="native support rule")
        async def execute(api, label, expression, selector, **kwargs):
            operation = NS(ctx=self.fx.ctx, status="working")
            await selector(operation)
            operation.status = "complete"
            return {"accepted": False, "status": "rejected", "validation": validation, "reason": validation.reason}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)):
            result = await self.fx.api.support_unification(catalog[0], 0)
        self.assertFalse(result["accepted"])
        self.assertIs(result["validation"], validation)
        self.assertIsNone(result["after"])
        self.assertEqual(len(self.fx.calls), 2)
        self.assertFalse(result["before"]["supports_candidate"])
        self.assertEqual(catalog.active_leases, 0)
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_support_local_snapshot_validation_error_settles_known_empty_read_owner(self):
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        with patch.object(national, "_UNIFICATION_SUPPORT", ()), \
             patch.object(data, "project_page") as project, \
             patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock()) as command:
            with self.assertRaisesRegex(ValueError, "choose1..32Field") as raised:
                await self.fx.api.support_unification(catalog[0], 0)
            project.assert_not_called()
            command.assert_not_awaited()
        error = raised.exception
        self.assertIs(error.choices, catalog)
        owner = error.read_owner
        self.assertEqual(owner.status, "complete")
        self.assertFalse(getattr(owner, "retain_unknown", False))
        self.assertIsNone(getattr(owner, "uncertain_query", None))
        self.assertEqual(owner.operations, [])
        self.assertNotIn(owner, self.fx.ctx.owner_pool.snapshot())
        self.assertEqual(catalog.active_leases, 0)
        self.assertEqual(backend.live, {100, 200})
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_support_predispatch_value_and_type_errors_use_canonical_known_local_semantics(self):
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        for error in (ValueError("local projection arguments"), TypeError("local projection input type")):
            with self.subTest(error=type(error).__name__):
                with patch.object(data, "project_page", side_effect=error) as project, \
                     patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock()) as command:
                    with self.assertRaises(type(error)) as raised:
                        await self.fx.api.support_unification(catalog[0], 0)
                    project.assert_called_once()
                    command.assert_not_awaited()
                self.assertIs(raised.exception, error)
                self.assertIs(error.choices, catalog)
                owner = error.read_owner
                self.assertEqual(owner.status, "complete")
                self.assertFalse(getattr(owner, "retain_unknown", False))
                self.assertIsNone(getattr(owner, "uncertain_query", None))
                self.assertEqual(owner.values, [])
                self.assertEqual(owner.operations, [])
                self.assertNotIn(owner, self.fx.ctx.owner_pool.snapshot())
                self.assertEqual(catalog.active_leases, 0)
                self.assertEqual(backend.released, [])
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_support_actual_query_failure_keeps_canonical_query_and_partial_result(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        for error in (OSError("original snapshot read uncertain"), ValueError("original copied page decode failed")):
            with self.subTest(error=type(error).__name__):
                captured = []
                class FailedQuery(Query):
                    def __init__(self, page):
                        super().__init__(page)
                        self.partial_result = replace(page, error=122, cells=page.cells[:2])
                        captured.append(self)
                    def read(self): raise error
                self.fx.query_factory = FailedQuery
                with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock()) as command:
                    with self.assertRaises(type(error)) as raised:
                        await self.fx.api.support_unification(catalog[0], 0)
                    command.assert_not_awaited()
                self.assertIs(raised.exception, error)
                self.assertIs(error.choices, catalog)
                owner = error.read_owner
                self.assertTrue(owner.retain_unknown)
                self.assertIs(owner.uncertain_query, captured[0])
                self.assertIn(owner, self.fx.ctx.owner_pool.snapshot())
                self.assertEqual(owner.values, [])
                self.assertIs(owner.operations[0][1], captured[0].partial_result)
                self.assertEqual(owner.operations[0][1].error, 122)
                with self.assertRaises(ChoicesBusyError): await catalog.close()
                self.assertEqual(backend.released, [])
                # Fixture-only settlement of the same original failed copy;
                # this does not resubmit or alter production owner decisions.
                owner.retain_unknown, owner.uncertain_query = False, None
                await owner.finish({"status": "original_fixture_copy_settled"})
        self.fx.query_factory = Query
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_support_unknown_before_read_keeps_source_lease_without_submitting(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        error = OSError("support observation unknown")
        def fail(page):
            raise error
        self.fx.transform = fail
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock()) as command:
            with self.assertRaises(OSError) as raised:
                await self.fx.api.support_unification(catalog[0], 0)
        self.assertIs(raised.exception, error)
        self.assertIs(error.choices, catalog)
        self.assertTrue(error.read_owner.retain_unknown)
        self.assertIn(error.read_owner, self.fx.ctx.owner_pool.snapshot())
        self.assertEqual(catalog.active_leases, 1)
        command.assert_not_awaited()
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertEqual(backend.released, [])

    async def test_support_readback_error_preserves_acceptance_and_keeps_borrowed_source(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        error, receipts = OSError("support readback unknown"), (object(),)
        async def execute(api, label, expression, selector, **kwargs):
            operation = NS(ctx=self.fx.ctx, status="working")
            await selector(operation)
            operation.status = "complete"
            def fail(page):
                raise error
            self.fx.transform = fail
            return {"accepted": True, "status": "accepted", "completed": False, "native_results": receipts}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            result = await self.fx.api.support_unification(catalog[0], 0)
        self.assertTrue(result["accepted"])
        self.assertIs(result["native_results"], receipts)
        self.assertIs(result["readback_error"], error)
        self.assertIsNone(result["after"])
        self.assertTrue(error.read_owner.retain_unknown)
        self.assertIn(error.read_owner, self.fx.ctx.owner_pool.snapshot())
        self.assertEqual(catalog.active_leases, 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        command.assert_awaited_once()
        self.assertEqual(backend.released, [])

    async def test_support_unknown_empty_read_owner_survives_completed_operation_task(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        executor = SerializedQueryExecutor(timeout=1, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query, self.fx.ctx.perform = executor.query, executor.perform
        self.fx.ctx._remember = executor._remember
        error = OSError("unknown borrowed read with no new Any handles")
        async def execute(api, label, expression, selector, **kwargs):
            operation = NS(ctx=self.fx.ctx, status="working")
            await selector(operation)
            operation.status = "complete"
            def fail(page):
                raise error
            self.fx.transform = fail
            return {"accepted": True, "status": "accepted", "completed": False}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)):
            result = await self.fx.api.support_unification(catalog[0], 0)
        # Let already-queued task-done callbacks run; no polling or time delay.
        callbacks_done = asyncio.Event()
        asyncio.get_running_loop().call_soon(callbacks_done.set)
        await callbacks_done.wait()
        self.assertTrue(result["accepted"])
        self.assertIs(result["readback_error"], error)
        self.assertEqual(error.read_owner.values, [])
        self.assertEqual(error.read_owner.owners, [])
        self.assertTrue(error.read_owner.retain_unknown)
        self.assertIn(error.read_owner, self.fx.ctx.owner_pool.snapshot())
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertEqual(backend.released, [])

    async def test_support_unknown_command_keeps_original_formation_binding(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        error = OSError("support dispatch unknown")
        operation = NS(ctx=self.fx.ctx, status="working")
        async def execute(api, label, expression, selector, **kwargs):
            bindings = await selector(operation)
            self.assertEqual(bindings[1].object, 200)
            raise error
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            with self.assertRaises(OSError) as raised:
                await self.fx.api.support_unification(catalog[0], 0)
        self.assertIs(raised.exception, error)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        command.assert_awaited_once()
        self.assertEqual(backend.released, [])
        operation.status = "complete"
        await catalog.close()

    async def test_support_requires_actual_choice_and_explicit_full_candidate_id(self):
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        original_calls = len(self.fx.calls)
        for candidate in (True, -1, 0xffffffff, "ABC", {"id": 0}, None):
            with self.assertRaises(ValueError):
                await self.fx.api.support_unification(catalog[0], candidate)
        for choice in (0, "Union", {"source_index": 0}, dict(catalog[0])):
            with self.assertRaises(TypeError):
                await self.fx.api.support_unification(choice, 0)
        self.assertEqual(len(self.fx.calls), original_calls)
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_pending_support_readback_holds_choice_and_resumes_without_replay(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        executor = SerializedQueryExecutor(timeout=.01, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query, self.fx.ctx.perform = executor.query, executor.perform
        self.fx.ctx._remember = executor._remember
        captured, started, loop = [], asyncio.Event(), asyncio.get_running_loop()
        class Delayed(Query):
            def __init__(self, page):
                super().__init__(page)
                self.ready = False
                self.partial_result = replace(page, cells=page.cells[:1])
                captured.append(self)
                loop.call_soon_threadsafe(started.set)
            def read(self):
                return self.result if self.ready else None
        async def execute(api, label, expression, selector, **kwargs):
            operation = NS(ctx=self.fx.ctx, status="working")
            await selector(operation)
            operation.status = "complete"
            self.fx.defaults["CountryFormation.IsSupportingCandidate(GetPlayer, Country.Self)"] = True
            self.fx.defaults["CountryFormation.GetSupportedCandidateCountry(GetPlayer).GetID"] = 0
            self.fx.query_factory = Delayed
            return {"accepted": True, "status": "accepted", "completed": False}
        with patch("pdx_runtime.v3_game._political_actions.selected_command", AsyncMock(side_effect=execute)) as command:
            task = asyncio.create_task(self.fx.api.support_unification(catalog[0], 0))
            await asyncio.wait_for(started.wait(), 1)
            pending = await task
            self.assertIsInstance(pending, PendingOperation)
            self.assertIs(pending.pending_queries[0], captured[0])
            self.assertEqual(catalog.active_leases, 1)
            with self.assertRaises(ChoicesBusyError):
                await catalog.close()
            captured[0].ready = True
            result = await pending.resume(1)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["after"]["supports_candidate"])
        self.assertFalse(result["completed"])
        self.assertEqual(catalog.active_leases, 0)
        self.assertEqual(len(self.fx.calls), 3)
        self.assertEqual(sum(call[0] == "source" for call in backend.calls), 1)
        command.assert_awaited_once()
        scopes = [owner for owner in self.fx.ctx.owner_pool.snapshot() if isinstance(owner, run_mod.Run)]
        self.assertEqual(scopes, [catalog.owner])
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_support_runs_real_selected_helper_one_validate_one_submit_no_serialization(self):
        backend = self.formation_native_source()
        catalog = await self.fx.api.formation_choices()
        self.fx.ctx.adapters["commands"] = "fake-commands"
        validation = NS(error=0, validated=True, reason="")
        submission = NS(error=0, accepted=True, command=0)
        events = []
        fx = self.fx
        class Owner:
            world, command, pending, attempt = fx.world, 0x3000, None, None
            def validate(self, capacity):
                events.append(("validate", capacity))
                self.attempt = NS(outcome="owned")
                return Query(validation)
            def submit(self, flags=4):
                events.append(("submit", flags))
                self.command = 0
                self.attempt = NS(outcome="consumed")
                fx.defaults["CountryFormation.IsSupportingCandidate(GetPlayer, Country.Self)"] = True
                fx.defaults["CountryFormation.IsSupportingAnyCandidate(GetPlayer)"] = True
                fx.defaults["CountryFormation.GetSupportedCandidateCountry(GetPlayer).GetID"] = 0
                return Query(submission)
            def serialize(self, *args, **kwargs):
                raise AssertionError("selected support must not serialize")
        owner = Owner()
        carrier = data.NativeDataCommand(0x1000, 0x1100, 0x1018, 900, "fixture", fx.world)
        def evaluate(native, expression, bindings, **kwargs):
            self.assertEqual(expression, "CountryFormation.SupportCandidate(GetPlayer, Country.Self)")
            self.assertEqual(len(bindings), 2)
            self.assertEqual(bindings[0].object, 0)
            self.assertEqual(bindings[0].domain, "Country")
            self.assertTrue(bindings[1].retained)
            self.assertEqual(bindings[1].object, 200)
            backend.live.add(900)
            events.append(("produce", expression))
            return Query(NS(error=0, action=data.Action.EVALUATE, value_handle=900, value=carrier))
        copied = NS(error=0, action=data.Action.COMMAND_VALUE, value_handle=900, value=carrier)
        cloned = NS(error=0, action=commands.Action.CLONE, cloned_command=0x3000, type_token=12345, world=fx.world)
        with patch.object(data, "evaluate", evaluate), \
             patch.object(data, "command_value", return_value=Query(copied)), \
             patch.object(commands, "request", return_value=Query(cloned)) as request, \
             patch.object(commands.NativeCommand, "from_result", return_value=owner):
            result = await fx.api.support_unification(catalog[0], 0)
        self.assertEqual([event[0] for event in events], ["produce", "validate", "submit"])
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertIs(result["validation"], validation)
        self.assertIs(result["submission"], submission)
        self.assertTrue(result["after"]["supports_candidate"])
        self.assertEqual(result["after"]["actor_country_id"], 0x99000009)
        self.assertEqual([handle for handle, _ in backend.released], [900])
        request.assert_called_once()
        self.assertEqual(request.call_args.args[1], commands.Action.CLONE)
        action_runs = [value for value in fx.ctx.diagnostics if isinstance(value, run_mod.Run)
                       and value.label == "support_unification"]
        self.assertEqual(len(action_runs), 1)
        self.assertIsNone(action_runs[0].actor)
        self.assertEqual(catalog.active_leases, 0)
        await catalog.close()
        self.assertEqual(backend.live, set())

    async def test_real_generated_owner_uses_carrier_world_not_stale_context(self):
        # Production generated_owner/adopt and NativeCommand.from_result run;
        # only original native query results are fixtures. No helper mock here.
        for stale_world in (0, self.fx.world + 99):
            with self.subTest(stale_world=stale_world):
                self.fx.ctx.world = stale_world
                self.fx.ctx.adapters["commands"] = "fake-commands"
                run = run_mod.Run(self.fx.ctx, "pause-boundary")
                initial = data.NativeDataCommand(0x1000, 0x1100, 0x1018, 17, "fixture")
                carrier = replace(initial, generation_world=self.fx.world)
                evaluated = NS(error=0, action=data.Action.EVALUATE, value_handle=17, value=initial)
                copied = NS(error=0, action=data.Action.COMMAND_VALUE, value_handle=17, value=carrier)
                cloned = NS(error=0, action=commands.Action.CLONE, cloned_command=0x2000,
                            type_token=12345, world=self.fx.world)
                bindings = (data.ObjectBinding.full_id("Country", 20, self.fx.country_id),
                            data.ObjectBinding.full_id("State", 30, self.fx.state_id))
                with patch.object(data, "evaluate", return_value=Query(evaluated)) as evaluate, \
                     patch.object(data, "command_value", return_value=Query(copied)) as command_value, \
                     patch.object(commands, "request", return_value=Query(cloned)) as request:
                    owner = await run.generated_owner("State.TogglePauseColonization", bindings)
                self.assertEqual(owner.world, carrier.generation_world)
                self.assertEqual(owner.command, 0x2000)
                self.assertEqual(run.world, stale_world)
                self.assertEqual(run.values, [17])
                self.assertEqual(run.owners, [owner])
                evaluate.assert_called_once()
                command_value.assert_called_once()
                request.assert_called_once()
                self.assertEqual(request.call_args.args[1], commands.Action.CLONE)
                self.assertEqual(request.call_args.kwargs["world"], carrier.generation_world)
                self.assertEqual(request.call_args.kwargs["command"], carrier.primary_pointer)
                self.assertEqual(self.fx.country_resolutions, 0)
                self.assertEqual(self.fx.type_calls, [])

    async def test_real_generated_owner_retains_clone_with_different_actual_source_world(self):
        self.fx.ctx.world = 0
        self.fx.ctx.adapters["commands"] = "fake-commands"
        run = run_mod.Run(self.fx.ctx, "abandon-boundary")
        carrier = data.NativeDataCommand(0x1000, 0x1100, 0x1018, 17, "fixture", self.fx.world)
        evaluated = NS(error=0, action=data.Action.EVALUATE, value_handle=17, value=carrier)
        copied = NS(error=0, action=data.Action.COMMAND_VALUE, value_handle=17, value=carrier)
        cloned = NS(error=0, action=commands.Action.CLONE, cloned_command=0x2000,
                    type_token=12345, world=self.fx.world + 1)
        with patch.object(data, "evaluate", return_value=Query(evaluated)), \
             patch.object(data, "command_value", return_value=Query(copied)), \
             patch.object(commands, "request", return_value=Query(cloned)) as request:
            with self.assertRaisesRegex(RuntimeError, "another source world"):
                await run.generated_owner("State.AbandonColony", ())
        self.assertEqual(run.values, [17])
        self.assertEqual(len(run.owners), 1)
        self.assertEqual(run.owners[0].world, cloned.world)
        self.assertEqual(run.owners[0].command, 0x2000)
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs["world"], carrier.generation_world)

    async def test_pending_composite_resumes_original_query_once(self):
        executor = SerializedQueryExecutor(timeout=.01, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query = executor.query
        self.fx.ctx.perform = executor.perform
        self.fx.ctx._remember = executor._remember
        captured, started = [], asyncio.Event()
        loop = asyncio.get_running_loop()
        class Delayed(Query):
            def __init__(self, result):
                super().__init__(result)
                self.ready = False
                self.partial_result = replace(result, cells=result.cells[:1])
                captured.append(self)
                loop.call_soon_threadsafe(started.set)
            def read(self):
                return self.result if self.ready else None
        self.fx.query_factory = Delayed
        task = asyncio.create_task(self.fx.api.national_modifiers())
        await asyncio.wait_for(started.wait(), 1)
        pending = await task
        self.assertIsInstance(pending, PendingOperation)
        self.assertIs(pending.pending_queries[0], captured[0])
        captured[0].ready = True
        self.fx.query_factory = Query
        result = await pending.resume(1)
        self.assertTrue(result["complete"])
        self.assertEqual(len(self.fx.calls), 3)
        self.assertEqual(executor.pending_queries, [])


if __name__ == "__main__":
    unittest.main()
