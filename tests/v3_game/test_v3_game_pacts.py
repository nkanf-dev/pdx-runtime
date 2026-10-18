"""CPU copied native pact scopes, not owning Pact choices or live effects."""
from contextlib import ExitStack
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands, v3_script as script
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.v3_game.diplomacy import country_details as p
from tests.v3_game.test_v3_game_political_data import Context, cell

TARGET = 0x80000002
HIGH_PACT = 0xF0000003


def native_page(fields, records, *, start=0, total=None, error=0, valid=True):
    cells = []
    for row, record in enumerate(records):
        for column, field in enumerate(fields):
            if field.name in record:
                value = record[field.name]
            elif field.full_id:
                value = 0
            elif field.kind == data.Kind.BOOL:
                value = False
            elif field.kind == data.Kind.FIXED:
                value = data.FixedPoint(0)
            elif field.kind in (data.Kind.I32, data.Kind.U32):
                value = 0
            else:
                value = "native text"
            cells.append(value if isinstance(value, data.ProjectionCell)
                else cell(value, column, row, kind=field.kind))
    return data.ProjectionPage(error, 0x5000, len(fields), start, 17,
        len(records) if total is None else total, valid, len(records), tuple(cells))


def summary(*, subject=False, include_subject=True, **values):
    fields = p._PACT_SUMMARY if include_subject else p._PACT_SUMMARY[:2]
    return native_page(fields, [{"is_subject": subject, **values}])


def group(key="increase_relations", country=0):
    return {"group_country_id": country, "group_type_key": key}


def pact(key="increase_relations", country=0, **values):
    return {"group_country_id": country, "group_type_key": key,
        "pact_type_key": key, "context_country_id": country,
        "other_country_id": TARGET, "first_country_id": TARGET,
        "second_country_id": country, **values}


def empty_groups():
    return native_page(p._PACT_GROUPS, [])


class PactReadTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, pages, **kwargs):
        ctx = Context(pages)
        api = DiplomacyAPI(ctx)
        with ExitStack() as stack:
            native = stack.enter_context(patch.object(data, "project_page", return_value=NS(cleanup_error=None)))
            forbidden = [stack.enter_context(patch.object(module, name)) for module, name in
                ((commands, "create"), (commands, "request"), (data, "evaluate"), (script, "trigger"))]
            result = await api.pacts(**kwargs)
        for call in forbidden:
            call.assert_not_called()
        self.assertEqual(ctx.performs, ["diplomacy.pacts"])
        self.assertEqual(ctx.pages, [])
        for call in native.call_args_list:
            self.assertLessEqual(len(call.args[1]), 32)
            self.assertEqual(call.kwargs["limit"], 64)
            roots = call.args[2][0]
            self.assertEqual(len(roots), 1)
            self.assertEqual(roots[0].domain, "Country")
            self.assertFalse(roots[0].retained)
            expressions = " ".join(call.args[1])
            self.assertNotIn("GetAllGeneralDiploActions", expressions)
            self.assertNotIn("GetStartCommand", expressions)
            self.assertNotIn("IsValid(Command", expressions)
        return result, ctx, native

    async def test_true_pact_source_contextual_type_argument_and_same_callback_actual_parties(self):
        pages = [summary(), native_page(p._PACT_GROUPS, [group()]),
            native_page(p._PACT_ROWS, [pact(pact_id=HIGH_PACT, name="#bold Actual pact#!",
                ongoing_for_context_country=False, ongoing_for_other_country=True)])]
        result, ctx, native = await self.invoke(pages)
        self.assertTrue(result["read_complete"])
        row, = result["pacts"]
        self.assertEqual(row["pact_id"], HIGH_PACT)
        self.assertEqual((row["first_country_id"], row["second_country_id"]), (TARGET, 0))
        self.assertEqual((row["context_country_id"], row["other_country_id"]), (0, TARGET))
        self.assertFalse(row["ongoing_for_context_country"])
        self.assertTrue(row["ongoing_for_other_country"])
        self.assertEqual((row["name"], row["name_raw"]), ("Actual pact", "#bold Actual pact#!"))
        self.assertEqual(row["scope_reasons"], [])
        self.assertEqual(native.call_args_list[1].kwargs["source_expression"], p._PACT_GROUP_SOURCE)
        self.assertEqual(native.call_args_list[2].kwargs["source_expression"], p._PACT_SOURCE)
        self.assertIn("AccessActiveDiplomaticPacts(ContextualDiplomaticActionType.Self)", p._PACT_SOURCE)
        self.assertNotIn("AccessActiveDiplomaticPacts(DiplomaticActionType.Self)", p._PACT_SOURCE)
        self.assertIn(p._PACT + ".GetID", native.call_args_list[2].args[1])
        self.assertEqual(ctx.selected_reads, 1)
        self.assertFalse(result["owning_pact_lookup_mapped"])
        self.assertFalse(result["command_validation_performed"])
        self.assertFalse(result["subject_pact"]["present"])


    async def test_zero_false_fixed_and_empty_break_reason_are_not_coerced(self):
        pages = [summary(), native_page(p._PACT_GROUPS, [group()]),
            native_page(p._PACT_ROWS, [pact(pact_id=0, subject_relation=False,
                has_daily_relations_progress=False, daily_relations_progress=data.FixedPoint(0),
                linked_action_maintenance_cost=data.FixedPoint(123456),
                linked_action_base_maintenance_cost=data.FixedPoint(0),
                about_to_break_reason="", auto_break_reason="", linked_action_first_country_id=TARGET)])]
        result, _, _ = await self.invoke(pages)
        row, = result["pacts"]
        self.assertEqual(row["pact_id"], 0)
        self.assertFalse(row["subject_relation"])
        self.assertFalse(row["has_daily_relations_progress"])
        self.assertEqual(row["daily_relations_progress"], Decimal(0))
        self.assertEqual(row["linked_action_maintenance_cost"], Decimal("1.23456"))
        self.assertEqual(row["linked_action_base_maintenance_cost"], Decimal(0))
        self.assertEqual(row["about_to_break_reason"], "")
        self.assertEqual(row["auto_break_reason_raw"], "")
        self.assertNotIn("about_to_break", row)
        self.assertEqual(row["linked_action_first_country_id"], TARGET)

    async def test_duplicate_actual_pact_ids_and_group_keys_are_not_deduplicated(self):
        pages = [summary(), native_page(p._PACT_GROUPS, [group(), group()]),
            native_page(p._PACT_ROWS, [pact(pact_id=0)]), native_page(p._PACT_ROWS, [pact(pact_id=0)])]
        result, _, native = await self.invoke(pages)
        self.assertEqual([row["pact_id"] for row in result["pacts"]], [0, 0])
        self.assertEqual(len(result["active_pact_sources"]), 2)
        self.assertEqual([call.kwargs["steps"][0].index for call in native.call_args_list[2:]], [0, 1])
        self.assertTrue(result["read_complete"])
        self.assertFalse(result["atomic_snapshot"])

    async def test_same_count_group_occurrence_drift_keeps_fresh_actual_rows_and_scope_reason(self):
        pages = [summary(), native_page(p._PACT_GROUPS, [group("original", 0)]),
            native_page(p._PACT_ROWS, [pact("fresh", TARGET, pact_id=HIGH_PACT)])]
        result, _, native = await self.invoke(pages)
        row, = result["pacts"]
        section, = result["active_pact_sources"]
        self.assertEqual((row["group_country_id"], row["group_type_key"], row["pact_type_key"]), (TARGET, "fresh", "fresh"))
        self.assertEqual(row["pact_id"], HIGH_PACT)
        self.assertEqual(section["requested_group_type_key"], "original")
        self.assertEqual(section["requested_group_country_id"], 0)
        self.assertTrue(row["scope_reasons"])
        self.assertIn("changed", row["scope_reasons"][0]["reason"])
        self.assertFalse(result["read_complete"])
        self.assertEqual(native.call_count, 3, "drift must not trigger an ordinal retry")
        step, = native.call_args_list[2].kwargs["steps"]
        self.assertEqual((step.index, step.expected_count), (0, 1))
        self.assertEqual(result["active_pact_groups"]["rows"][0]["group_type_key"], "original")

    async def test_actual_pact_type_mismatch_preserves_original_identity_not_group_label(self):
        pages = [summary(), native_page(p._PACT_GROUPS, [group()]),
            native_page(p._PACT_ROWS, [pact(pact_type_key="actual_other_type", pact_id=7)])]
        result, _, _ = await self.invoke(pages)
        row, = result["pacts"]
        self.assertEqual(row["pact_type_key"], "actual_other_type")
        self.assertEqual(row["group_type_key"], "increase_relations")
        self.assertIn("differs", row["scope_reasons"][0]["reason"])
        self.assertFalse(result["read_complete"])
        self.assertIn("active_pact_sources[0]", result["reasons"])

    async def test_unproven_group_identity_is_not_guessed_from_localized_names(self):
        bad = cell("increase_relations", 1, error=122, total=100)
        pages = [summary(), native_page(p._PACT_GROUPS, [group()]),
            native_page(p._PACT_ROWS, [pact(group_type_key=bad, pact_type_name="increase_relations", pact_id=9)])]
        result, _, _ = await self.invoke(pages)
        row, = result["pacts"]
        self.assertEqual(row["group_type_key"], "increase_relations")
        self.assertEqual(row["pact_id"], 9)
        self.assertIn("group_type_key", row["reasons"])
        self.assertTrue(any("unproven" in reason["reason"] for reason in row["scope_reasons"]))
        self.assertFalse(result["read_complete"])

    async def test_failed_group_source_is_not_successful_empty_pact_catalog(self):
        pages = [summary(), native_page(p._PACT_GROUPS, [], error=87, valid=False)]
        result, _, native = await self.invoke(pages)
        self.assertFalse(result["read_complete"])
        self.assertEqual(result["pacts"], [])
        self.assertIsNone(result["active_pact_groups"]["source_count"])
        self.assertEqual(result["active_pact_groups"]["native_pages"][0].error, 87)
        self.assertEqual(native.call_count, 2)


    async def test_partial_native_description_and_break_reasons_remain_with_raw_pages(self):
        column = next(i for i, field in enumerate(p._PACT_ROWS) if field.name == "about_to_break_reason")
        partial = cell("#bold kept reason#!", column, error=122, total=100)
        pages = [summary(), native_page(p._PACT_GROUPS, [group()]),
            native_page(p._PACT_ROWS, [pact(pact_id=3, about_to_break_reason=partial)], error=122)]
        result, _, _ = await self.invoke(pages)
        row, = result["pacts"]
        self.assertEqual(row["about_to_break_reason"], "kept reason")
        self.assertEqual(row["about_to_break_reason_raw"], "#bold kept reason#!")
        self.assertIn("about_to_break_reason", row["reasons"])
        self.assertEqual(result["active_pact_sources"][0]["native_pages"][0].error, 122)
        self.assertEqual(row["pact_id"], 3)
        self.assertFalse(result["read_complete"])

    async def test_native_empty_child_is_not_requeried_to_build_an_identity_join(self):
        pages = [summary(), native_page(p._PACT_GROUPS, [group()]), native_page(p._PACT_ROWS, [])]
        result, _, native = await self.invoke(pages)
        self.assertTrue(result["read_complete"])
        self.assertEqual(result["pacts"], [])
        self.assertEqual(result["active_pact_sources"][0]["source_count"], 0)
        self.assertEqual(native.call_count, 3)
        self.assertFalse(result["atomic_snapshot"])

    async def test_failed_child_count_retains_native_error_not_claimed_empty_absence(self):
        pages = [summary(), native_page(p._PACT_GROUPS, [group()]),
            native_page(p._PACT_ROWS, [], error=1237, valid=False)]
        result, _, _ = await self.invoke(pages)
        source, = result["active_pact_sources"]
        self.assertIsNone(source["source_count"])
        self.assertEqual(source["native_pages"][0].error, 1237)
        self.assertFalse(result["read_complete"])

    async def test65groups_page_and_traverse_absolute_occurrences_with_native_count_guard(self):
        pages = [summary(), native_page(p._PACT_GROUPS, [group() for _ in range(64)], total=65),
            native_page(p._PACT_GROUPS, [group()], start=64, total=65)]
        pages.extend(native_page(p._PACT_ROWS, []) for _ in range(65))
        result, _, native = await self.invoke(pages)
        self.assertTrue(result["read_complete"])
        self.assertEqual(len(result["active_pact_sources"]), 65)
        self.assertEqual(native.call_count, 68)
        self.assertEqual(native.call_args_list[2].kwargs["start"], 64)
        final_step, = native.call_args.kwargs["steps"]
        self.assertEqual((final_step.index, final_step.expected_count), (64, 65))

    async def test129actual_pact_rows_are_three_pages_without_hidden_field_index_merge(self):
        pages = [summary(), native_page(p._PACT_GROUPS, [group()])]
        for start in (0, 64, 128):
            pages.append(native_page(p._PACT_ROWS, [pact(pact_id=i)
                for i in range(start, min(start + 64, 129))], start=start, total=129))
        result, _, native = await self.invoke(pages)
        self.assertEqual([row["pact_id"] for row in result["pacts"]], list(range(129)))
        self.assertEqual(native.call_count, 5)
        self.assertEqual([call.kwargs["start"] for call in native.call_args_list[2:]], [0, 64, 128])
        self.assertTrue(all(call.args[1] == tuple(field.expression for field in p._PACT_ROWS)
            for call in native.call_args_list[2:]))
        self.assertEqual(len(result["active_pact_sources"][0]["native_pages"]), 3)

    async def test_subject_supplement_uses_actual_native_pact_chain_without_deduplication(self):
        pages = [summary(subject=True), native_page(p._PACT_GROUPS, [group()]),
            native_page(p._PACT_ROWS, [pact(pact_id=0, subject_relation=True)]),
            native_page(p._SUBJECT_PACT, [{"pact_id": 0, "pact_type_key": "subject_type_vassal",
                "subject_relation": True, "observed_is_subject": True}])]
        result, _, native = await self.invoke(pages)
        self.assertTrue(result["read_complete"])
        self.assertEqual(result["pacts"][0]["pact_id"], 0)
        self.assertEqual(result["subject_pact"]["pact_id"], 0)
        self.assertTrue(result["subject_pact"]["subject_relation"])
        self.assertEqual(result["subject_pact"]["pact_type_key"], "subject_type_vassal")
        self.assertIn("Country.GetSubjectPact.GetID", native.call_args.args[1])
        self.assertNotIn("group_type_key", result["subject_pact"])
        self.assertNotIn("IsValid", " ".join(native.call_args.args[1]))

    async def test_subject_none_predicate_is_unknown_not_false_and_supplement_is_preserved(self):
        unknown = cell(None, 2, kind=data.Kind.EMPTY)
        pages = [summary(subject=unknown), empty_groups(),
            native_page(p._SUBJECT_PACT, [{"pact_id": 12, "observed_is_subject": True}])]
        result, _, native = await self.invoke(pages)
        self.assertIsNone(result["summary"]["is_subject"])
        self.assertEqual(result["subject_pact"]["pact_id"], 12)
        self.assertNotIn("present", result["subject_pact"])
        self.assertFalse(result["read_complete"])
        self.assertEqual(native.call_count, 3)

    async def test_subject_false_with_native_error_is_not_clean_absence(self):
        failed = cell(False, 2, error=87)
        pages = [summary(subject=failed), empty_groups(), native_page(p._SUBJECT_PACT, [{"pact_id": 13}])]
        result, _, native = await self.invoke(pages)
        self.assertFalse(result["summary"]["is_subject"])
        self.assertIn("is_subject", result["summary"]["reasons"]["fields"])
        self.assertEqual(result["subject_pact"]["pact_id"], 13)
        self.assertNotIn("present", result["subject_pact"])
        self.assertEqual(native.call_count, 3)

    async def test_subject_native_sentinel_and_partial_result_are_not_changed_into_absence(self):
        pages = [summary(subject=True), empty_groups(),
            native_page(p._SUBJECT_PACT, [{"pact_id": 0xFFFFFFFF, "name": "#bold kept subject#!"}], error=87)]
        result, _, _ = await self.invoke(pages)
        self.assertEqual(result["subject_pact"]["pact_id"], 0xFFFFFFFF)
        self.assertEqual(result["subject_pact"]["name_raw"], "#bold kept subject#!")
        self.assertIn("pact_id", result["subject_pact"]["reasons"]["fields"])
        self.assertFalse(result["read_complete"])
        self.assertNotIn("present", result["subject_pact"])

    async def test_subject_not_requested_excludes_predicate_and_supplement_reads(self):
        pages = [summary(include_subject=False), empty_groups()]
        result, _, native = await self.invoke(pages, include_subject=False)
        self.assertTrue(result["read_complete"])
        self.assertFalse(result["subject_pact_requested"])
        self.assertNotIn("subject_pact", result)
        self.assertNotIn("is_subject", result["summary"])
        self.assertEqual(len(native.call_args_list[0].args[1]), 2)
        self.assertEqual(native.call_count, 2)

    async def test_explicit_selected_country_is_pinned_and_does_not_replace_native_getter_parties(self):
        pages = [summary(observed_country_id=TARGET), native_page(p._PACT_GROUPS, [group(country=TARGET)]),
            native_page(p._PACT_ROWS, [pact(country=TARGET, first_country_id=0, second_country_id=TARGET)])]
        result, ctx, native = await self.invoke(pages, country_id=TARGET)
        self.assertEqual(ctx.selected_reads, 0)
        self.assertTrue(all(call.args[2][0][0].object == TARGET for call in native.call_args_list))
        self.assertEqual(result["pacts"][0]["first_country_id"], 0)
        self.assertEqual(result["pacts"][0]["second_country_id"], TARGET)

    async def test_invalid_input_rejected_before_native_query(self):
        for kwargs in ({"country_id": -1}, {"country_id": 0xFFFFFFFF}, {"country_id": True},
                {"include_subject": 0}, {"include_subject": None}):
            ctx = Context([])
            with patch.object(data, "project_page") as native:
                with self.assertRaises((TypeError, ValueError)):
                    await DiplomacyAPI(ctx).pacts(**kwargs)
            native.assert_not_called()
            self.assertEqual(ctx.calls, [])

    async def test_original_projection_exception_keeps_query_partial_and_never_replays(self):
        original = RuntimeError("original pending pact projection")
        partial = native_page(p._PACT_GROUPS, [], error=122, valid=False)
        original.partial_result = partial
        query = NS(cleanup_error=None)
        class PendingContext(Context):
            async def query(self, label, factory):
                factory()
                raise original
        ctx = PendingContext([])
        with patch.object(data, "project_page", return_value=query) as native:
            with self.assertRaises(RuntimeError) as caught:
                await DiplomacyAPI(ctx).pacts(country_id=TARGET)
        self.assertIs(caught.exception, original)
        self.assertIs(original.query, query)
        self.assertIs(original.partial_result, partial)
        self.assertEqual(native.call_count, 1)
        self.assertEqual(original.projection_context["binding_rows"][0][0].object, TARGET)


if __name__ == "__main__":
    unittest.main()
