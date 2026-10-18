"""CPU-only country population-culture awakening and homeland projections.

Production composition/helpers run against copied native-page fixtures. No
process, native DLL, world-pop scan, command or game session is created.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_objects as objects
from pdx_runtime.query_executor import PendingOperation, RetainedOwners, SerializedQueryExecutor
from pdx_runtime.v3_game.politics import cultures


class Query:
    serial = 0
    def __init__(self, result):
        Query.serial += 1
        self.request_id, self.result = Query.serial, result
    def read(self):
        return self.result


def cell(value, kind, row, field):
    text, bits = b"", 0
    if kind == data.Kind.STRING:
        text = value.encode("utf-8")
    elif kind == data.Kind.FIXED:
        bits = int(Decimal(value) * 100000) & ((1 << 64) - 1)
    else:
        bits = int(value) & ((1 << 64) - 1)
    return data.ProjectionCell(row, field, 0, kind, 0, bits, text, len(text))


class API(cultures.CulturePoliticsMixin):
    def __init__(self, ctx):
        self._ctx = ctx


class Fixture:
    country = 0x81000001
    world = 700
    def __init__(self):
        self.calls, self.performs, self.type_calls, self.retained = [], [], [], []
        self.country_resolutions = 0
        self.ctx = NS(native=object(), process=object(), world=self.world,
                      adapters={"data": "fake"}, diagnostics=[], owner_pool=RetainedOwners(),
                      _types={"Country": 20})
        self.ctx.country_id, self.ctx.type_id = self.country_id, self.type_id
        self.ctx.perform, self.ctx.query = self.perform, self.query
        self.ctx.refresh = self.forbid_refresh
        self.ctx._remember = self.remember
        self.api = API(self.ctx)
        self.query_factory = Query
        self.transform = lambda source, page: page
        self.records = [{}]
        self.homelands = {0: [{"State.GetID": 0x82000003}]}
        self.current_parents = None
        self.parent_by_page = {}
        fields = (*cultures._AWAKENING_FIELDS, *cultures._HOMELAND_FIELDS)
        self.kinds = {field.expression: field.kind for field in fields}
        self.defaults = {expression: "native text" if kind == data.Kind.STRING else False if kind == data.Kind.BOOL else 0
                         for expression, kind in self.kinds.items()}
        self.defaults.update({
            "Culture.GetID": 0, "Culture.GetNameNoFormatting": "#bold Culture A#!",
            "Culture.IsValid": True, "Culture.HasNationalAwakening": False,
            "Culture.GetCurrentFervor": "0", "Culture.GetTargetFervor": "0",
            "Culture.GetMonthlyChangeFervor": "-0.12500",
            "Culture.GetTargetFervorBreakdown": "#v Original awakening conditions#!",
            "Culture.GetTargetFervorFromCountryModifiers": "0",
            "Culture.GetTargetFervorFromCountryModifiersBreakdown": "Original country modifiers",
            "Culture.IsPrimaryCultureIn(Country.Self)": True,
            "Culture.HasSecessionMovement(Country.Self)": False,
            "State.GetID": 0x82000003, "State.GetName": "#v Homeland State#!",
            "State.GetOwner.GetID": 0x83000004, "State.GetOwner.GetName": "Other country",
            "State.GetStateRegion.GetID": 55, "State.GetStateRegion.GetName": "Homeland Region",
        })

    async def country_id(self):
        self.country_resolutions += 1
        return self.country
    async def type_id(self, name):
        self.type_calls.append(name)
        if name != "Country":
            raise AssertionError("No invented Culture/PopCulture/State binding metadata lookup")
        return 20
    async def forbid_refresh(self):
        raise AssertionError("copied read added a world check")
    async def perform(self, label, work):
        self.performs.append(label)
        return await work()
    async def query(self, label, factory, **kwargs):
        return factory().read()
    def remember(self, query):
        self.retained.append(query)
        self.ctx.owner_pool.retain(query)

    def project(self, native, fields, binding_rows, **kwargs):
        fields, binding_rows = tuple(fields), tuple(binding_rows)
        source, start, limit = kwargs["source_expression"], kwargs["start"], kwargs["limit"]
        self.calls.append((fields, binding_rows, kwargs))
        assert len(binding_rows) == 1 and len(binding_rows[0]) == 1
        root = binding_rows[0][0]
        assert root.domain == "Country" and not root.retained
        assert limit == 64 and len(fields) <= 32
        if source == "Country.AccessPopCulturesSortedByPopulation":
            assert not kwargs["steps"]
            payloads = []
            for record in self.records:
                culture_id = record.get("Culture.GetID", self.defaults["Culture.GetID"])
                payloads.append({"GetDataModelSize(Culture.AccessHomelands)": len(self.homelands.get(culture_id, [])), **record})
        elif source == "Culture.AccessHomelands":
            step, = kwargs["steps"]
            assert step.source_expression == "Country.AccessPopCulturesSortedByPopulation"
            parents = self.records if self.current_parents is None else self.current_parents
            if step.expected_count is not None and step.expected_count != len(parents):
                page = data.ProjectionPage(1237, self.world, len(fields), start, 45, 0, False, 0, ())
                return self.query_factory(self.transform(source, page))
            parent = {**parents[step.index], **self.parent_by_page.get((step.index, start), {})}
            culture_id = parent.get("Culture.GetID", self.defaults["Culture.GetID"])
            payloads = [{**parent, **state} for state in self.homelands.get(culture_id, [])]
        else:
            raise AssertionError("Unexpected source or world scan: " + str(source))
        count = len(payloads)
        payloads = payloads[start:start + limit]
        cells = tuple(cell(payload.get(expression, self.defaults[expression]), self.kinds[expression], row, field)
                      for row, payload in enumerate(payloads) for field, expression in enumerate(fields))
        page = data.ProjectionPage(0, self.world, len(fields), start, 45, count, True, len(payloads), cells)
        return self.query_factory(self.transform(source, page))


class CulturePoliticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fx = Fixture()
        for patcher in (
                patch.object(data, "project_page", self.fx.project),
                patch.object(data, "evaluate", side_effect=AssertionError("no per-cell evaluation or command")),
                patch.object(data, "list_count", side_effect=AssertionError("no borrowed list rebinding")),
                patch.object(data, "list_element", side_effect=AssertionError("no borrowed Culture pointer")),
                patch.object(objects, "enumerate_objects", side_effect=AssertionError("no world-pop scan"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_native_culture_rows_keep_zero_false_and_exact_signed_fervor(self):
        result = await self.fx.api.national_awakenings()
        self.assertTrue(result["complete"])
        row, = result["rows"]
        self.assertEqual(row["id"], 0)
        self.assertEqual(row["name"], "Culture A")
        self.assertEqual(row["name_raw"], "#bold Culture A#!")
        self.assertIs(row["has_national_awakening"], False)
        self.assertEqual(row["current_fervor"], Decimal("0"))
        self.assertEqual(row["target_fervor"], Decimal("0"))
        self.assertEqual(row["monthly_fervor_change"], Decimal("-0.125"))
        self.assertEqual(row["target_fervor_country_modifiers"], Decimal("0"))
        self.assertEqual(row["target_fervor_breakdown_raw"], "#v Original awakening conditions#!")
        self.assertIs(row["has_secession_movement"], False)
        self.assertEqual(self.fx.country_resolutions, 1)
        self.assertEqual(self.fx.type_calls, [])
        self.assertEqual(self.fx.performs, ["politics.national_awakenings"])
        self.assertEqual(result["source_expression"], "Country.AccessPopCulturesSortedByPopulation")
        self.assertFalse(any("PopCulture." in expression for call in self.fx.calls for expression in call[0]))

    async def test_country_population_scope_is_not_all_world_or_only_awakened(self):
        self.fx.records = [{"Culture.GetID": 0}, {"Culture.GetID": 0x80000002,
            "Culture.HasNationalAwakening": True, "Culture.GetCurrentFervor": "12.34567"}]
        result = await self.fx.api.national_awakenings(country_id=0)
        self.assertEqual(result["country_id"], 0)
        self.assertEqual(result["collection_scope"]["country_id"], 0)
        self.assertFalse(result["collection_scope"]["all_world_cultures"])
        self.assertFalse(result["collection_scope"]["only_awakened_filter_applied"])
        self.assertEqual([row["has_national_awakening"] for row in result["rows"]], [False, True])
        self.assertEqual([row["id"] for row in result["rows"]], [0, 0x80000002])
        self.assertEqual(result["rows"][1]["current_fervor"], Decimal("12.34567"))
        self.assertEqual(result["rows"][1]["target_fervor"], Decimal("0"))
        self.assertTrue(all(call[1][0][0].object == 0 for call in self.fx.calls))
        self.assertEqual(self.fx.country_resolutions, 0)

    async def test_no_unregistered_key_is_invented_from_name_or_occurrence(self):
        result = await self.fx.api.national_awakenings()
        self.assertEqual(result["identity"]["id_source"], "Culture.GetID")
        self.assertFalse(result["identity"]["definition_key_available"])
        self.assertNotIn("key", result["rows"][0])
        self.assertFalse(any("GetKey" in expression for call in self.fx.calls for expression in call[0]))

    async def test_homelands_are_original_state_rows_with_same_callback_culture_identity(self):
        result = await self.fx.api.national_awakenings()
        observed, = result["homelands"]
        row, = observed["rows"]
        self.assertEqual(row["culture_id"], 0)
        self.assertEqual(row["culture_name"], "Culture A")
        self.assertEqual(row["state_id"], 0x82000003)
        self.assertEqual(row["owner_id"], 0x83000004)
        self.assertNotEqual(row["owner_id"], result["country_id"])
        self.assertEqual(row["region_id"], 55)
        self.assertEqual(row["state_name_raw"], "#v Homeland State#!")
        self.assertNotIn("homelands", result["rows"][0])
        fields, roots, options = self.fx.calls[1]
        self.assertIn("State.GetID", fields)
        self.assertIn("State.GetName", fields)
        self.assertIn("Culture.GetID", fields)
        self.assertFalse(any(expression.startswith("StateRegion.") for expression in fields))
        self.assertEqual(options["steps"], (data.CreationStep("Country.AccessPopCulturesSortedByPopulation", 0, 1),))
        self.assertEqual(len(roots[0]), 1)

    async def test_selected_country_is_the_original_primary_secession_argument(self):
        result = await self.fx.api.national_awakenings(0)
        fields = self.fx.calls[0][0]
        self.assertIn("Culture.IsPrimaryCultureIn(Country.Self)", fields)
        self.assertIn("Culture.HasSecessionMovement(Country.Self)", fields)
        self.assertNotIn("Culture.HasSecessionMovement(GetPlayer)", fields)
        self.assertIs(result["rows"][0]["is_primary_culture"], True)
        self.assertIs(result["rows"][0]["has_secession_movement"], False)

    async def test_complete_empty_country_source_does_not_scan_elsewhere(self):
        self.fx.records = []
        result = await self.fx.api.national_awakenings()
        self.assertTrue(result["complete"])
        self.assertEqual(result["source_count"], 0)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["homelands"], [])
        self.assertEqual(len(self.fx.calls), 1)
        self.assertFalse(result["collection_scope"]["all_world_cultures"])

    async def test_empty_homeland_source_does_not_invent_parent_identity(self):
        self.fx.homelands = {}
        result = await self.fx.api.national_awakenings()
        self.assertTrue(result["complete"])
        self.assertEqual(result["rows"][0]["homeland_count"], 0)
        observed, = result["homelands"]
        self.assertEqual(observed["source_count"], 0)
        self.assertEqual(observed["rows"], [])
        self.assertFalse(observed["parent_identity_copied"])
        self.assertNotIn("culture_id", observed)

    async def test_native_page_and_field_errors_preserve_copied_false_and_values(self):
        def transform(source, page):
            if source == cultures._CULTURE_SOURCE:
                return replace(page, error=13, cells=tuple(replace(value, error=50) if value.field == 3 else value for value in page.cells))
            return page
        self.fx.transform = transform
        result = await self.fx.api.national_awakenings()
        self.assertFalse(result["complete"])
        self.assertIs(result["rows"][0]["has_national_awakening"], False)
        self.assertEqual(result["rows"][0]["current_fervor"], Decimal("0"))
        self.assertIn("50", result["rows"][0]["reasons"]["has_national_awakening"])
        self.assertIn("50", str(result["reasons"]))
        self.assertEqual(result["native_pages"][0].error, 13)
        self.assertEqual(result["homelands"][0]["rows"][0]["state_id"], 0x82000003)

    async def test_homeland_field_error_keeps_real_state_and_culture_ids(self):
        def transform(source, page):
            if source == "Culture.AccessHomelands":
                return replace(page, cells=tuple(replace(value, error=50) if value.field == 4 else value for value in page.cells))
            return page
        self.fx.transform = transform
        result = await self.fx.api.national_awakenings()
        self.assertFalse(result["complete"])
        row = result["homelands"][0]["rows"][0]
        self.assertEqual(row["culture_id"], 0)
        self.assertEqual(row["state_id"], 0x82000003)
        self.assertEqual(row["owner_id"], 0x83000004)
        self.assertIn("50", row["reasons"]["owner_id"])
        self.assertEqual(result["reasons"][0]["scope"], "homelands")
        self.assertIn("owner_id", str(result["reasons"]))

    async def test_missing_homeland_count_does_not_fabricate_zero_or_skip_original_source(self):
        self.fx.transform = lambda source, page: replace(page, cells=page.cells[:-1]) if source == cultures._CULTURE_SOURCE else page
        result = await self.fx.api.national_awakenings()
        self.assertFalse(result["complete"])
        self.assertIsNone(result["rows"][0]["homeland_count"])
        self.assertIn("did not copy", result["rows"][0]["reasons"]["homeland_count"])
        self.assertEqual(result["homelands"][0]["rows"][0]["state_id"], 0x82000003)
        self.assertEqual(len(self.fx.calls), 2)

    async def test_partial_text_and_invalid_utf8_remain_visible_with_original_pages(self):
        def transform(source, page):
            if source == cultures._CULTURE_SOURCE:
                cells = tuple(replace(value, snapshot=b"native\xff", total=30) if value.field == 7 else value for value in page.cells)
                return replace(page, cells=cells)
            return page
        self.fx.transform = transform
        result = await self.fx.api.national_awakenings()
        row = result["rows"][0]
        self.assertFalse(result["complete"])
        self.assertIn("native", row["target_fervor_breakdown"])
        self.assertIn("invalid UTF-8", row["reasons"]["target_fervor_breakdown"])
        self.assertIn("incomplete", row["reasons"]["target_fervor_breakdown"])
        self.assertEqual(result["native_pages"][0].cells[7].snapshot, b"native\xff")

    async def test_long_breakdown_is_copied_without_evaluation_or_source_replay(self):
        text = "原生民族觉醒条件" * 20000
        self.fx.defaults["Culture.GetTargetFervorBreakdown"] = text
        result = await self.fx.api.national_awakenings()
        self.assertEqual(result["rows"][0]["target_fervor_breakdown"], text)
        self.assertEqual(len(self.fx.calls), 2)

    async def test_kind_disagreement_does_not_turn_unknown_into_false(self):
        def transform(source, page):
            if source == cultures._CULTURE_SOURCE:
                return replace(page, cells=tuple(replace(value, kind=data.Kind.EMPTY, raw_bits=0) if value.field == 3 else value for value in page.cells))
            return page
        self.fx.transform = transform
        result = await self.fx.api.national_awakenings()
        self.assertIsNone(result["rows"][0]["has_national_awakening"])
        self.assertFalse(result["complete"])
        self.assertIn("native kind", result["rows"][0]["reasons"]["has_national_awakening"])

    async def test_unknown_source_count_is_not_a_known_empty_country(self):
        self.fx.transform = lambda source, page: replace(page, error=50, count_valid=False, cells=(), row_count=0)
        result = await self.fx.api.national_awakenings()
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["source_count"])
        self.assertEqual(result["rows"], [])
        self.assertIn("unavailable", str(result["reasons"]))
        self.assertEqual(len(self.fx.calls), 1)

    async def test_source_and_homeland_pagination_are_native64_rows_by_bounded_fields(self):
        self.fx.records = [{"Culture.GetID": index} for index in range(130)]
        self.fx.homelands = {}
        result = await self.fx.api.national_awakenings()
        source_calls = [call for call in self.fx.calls if call[2]["source_expression"] == cultures._CULTURE_SOURCE]
        self.assertEqual([call[2]["start"] for call in source_calls], [0, 64, 128])
        self.assertEqual([row["id"] for row in result["rows"]], list(range(130)))
        self.assertEqual(len(result["homelands"]), 130)
        self.assertTrue(all(len(call[0]) <= 32 and call[2]["limit"] == 64 for call in self.fx.calls))

    async def test_reordered_parent_does_not_join_homelands_to_stale_culture_row(self):
        self.fx.records = [{"Culture.GetID": 11, "Culture.GetNameNoFormatting": "Earlier culture"}]
        self.fx.current_parents = [{"Culture.GetID": 22, "Culture.GetNameNoFormatting": "Current culture"}]
        self.fx.homelands = {11: [{"State.GetID": 100}], 22: [{"State.GetID": 200}]}
        result = await self.fx.api.national_awakenings()
        self.assertEqual(result["rows"][0]["id"], 11)
        homeland = result["homelands"][0]["rows"][0]
        self.assertEqual(homeland["culture_id"], 22)
        self.assertEqual(homeland["culture_name"], "Current culture")
        self.assertEqual(homeland["state_id"], 200)
        self.assertFalse(result["atomic_snapshot"])

    async def test_changed_parent_count_retains_original_retry_error_without_replay(self):
        self.fx.current_parents = [{}, {}]
        result = await self.fx.api.national_awakenings()
        self.assertFalse(result["complete"])
        self.assertEqual(result["rows"][0]["id"], 0)
        observed, = result["homelands"]
        self.assertEqual(observed["native_pages"][0].error, 1237)
        self.assertEqual(observed["rows"], [])
        self.assertFalse(observed["parent_identity_copied"])
        self.assertEqual(len(self.fx.calls), 2)

    async def test_homeland_pages_repeat_actual_culture_identity_in_each_callback(self):
        self.fx.homelands = {0: [{"State.GetID": index} for index in range(65)]}
        self.fx.parent_by_page[(0, 0)] = {"Culture.GetNameNoFormatting": "First page culture"}
        self.fx.parent_by_page[(0, 64)] = {"Culture.GetNameNoFormatting": "Later page culture"}
        result = await self.fx.api.national_awakenings()
        observed, = result["homelands"]
        self.assertEqual([row["state_id"] for row in observed["rows"]], list(range(65)))
        self.assertEqual(observed["rows"][0]["culture_name"], "First page culture")
        self.assertEqual(observed["rows"][-1]["culture_name"], "Later page culture")
        self.assertEqual(len(observed["native_pages"]), 2)

    async def test_cleanup_warning_keeps_primary_rows_and_original_query_owner(self):
        class Cleanup(Query):
            cleanup_error = OSError("copied storage cleanup warning")
        self.fx.query_factory = Cleanup
        result = await self.fx.api.national_awakenings()
        self.assertTrue(result["complete"])
        self.assertEqual(result["rows"][0]["id"], 0)
        self.assertEqual(len(self.fx.retained), 2)
        warnings = [value for value in self.fx.ctx.diagnostics if "cleanup_error" in value]
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all(value["result"] in result["native_pages"] for value in warnings))

    async def test_pending_nested_page_resumes_original_copy_without_source_replay(self):
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
        self.fx.query_factory = lambda page: Delayed(page) if page.fields_count == len(cultures._HOMELAND_FIELDS) else Query(page)
        task = asyncio.create_task(self.fx.api.national_awakenings())
        await asyncio.wait_for(started.wait(), 1)
        pending = await task
        self.assertIsInstance(pending, PendingOperation)
        self.assertIs(pending.pending_queries[0], captured[0])
        self.assertEqual(captured[0].partial_result.cells[0].value, 0)
        captured[0].ready = True
        self.fx.query_factory = Query
        result = await pending.resume(1)
        self.assertTrue(result["complete"])
        self.assertEqual(len(self.fx.calls), 2)
        self.assertEqual(result["homelands"][0]["rows"][0]["state_id"], 0x82000003)
        self.assertEqual(executor.pending_queries, [])


if __name__ == "__main__":
    unittest.main()
