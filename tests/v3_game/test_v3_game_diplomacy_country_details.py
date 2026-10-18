"""CPU copied-read contracts. No live game, native injection or command execution."""
from contextlib import ExitStack
from decimal import Decimal
import struct
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands, v3_script as script
from pdx_runtime.v3_game.diplomacy import country_details as d
from pdx_runtime.v3_game.diplomacy.actions import DIPLOMATIC_ACTION_FIELDS
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from tests.v3_game.test_v3_game_political_data import Context

TARGET = 0x80000002
OTHER = 0xF0000003


def copied_cell(value, field=0, row=0, *, kind=None, error=0, total=None,
                binding_error=0):
    raw, snapshot = 0, b""
    if kind is None:
        if type(value) is bool:
            kind = data.Kind.BOOL
        elif isinstance(value, str):
            kind = data.Kind.STRING
        elif isinstance(value, data.FixedPoint):
            kind = data.Kind.FIXED
        elif type(value) is float:
            kind = data.Kind.FLOAT
        elif type(value) is int:
            kind = data.Kind.I32
        else:
            kind = data.Kind.EMPTY
    if kind == data.Kind.STRING:
        snapshot = value.encode("utf-8")
    elif kind == data.Kind.FIXED:
        raw = value.bits
    elif kind == data.Kind.FLOAT:
        raw = int.from_bytes(struct.pack("<f", value), "little")
    elif kind != data.Kind.EMPTY:
        raw = int(value) & 0xFFFFFFFFFFFFFFFF
    return data.ProjectionCell(row, field, error, kind, 9, raw & 0xFFFFFFFFFFFFFFFF, snapshot,
        len(snapshot) if total is None else total, binding_error=binding_error)


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
            elif field.kind == data.Kind.FLOAT:
                value = 0.0
            elif field.kind in (data.Kind.I32, data.Kind.U32):
                value = 0
            else:
                value = "native text"
            cells.append(value if isinstance(value, data.ProjectionCell)
                else copied_cell(value, column, row, kind=field.kind))
    return data.ProjectionPage(error, 0x1000, len(fields), start, 7,
        len(records) if total is None else total, valid, len(records), tuple(cells))


def script_result(*, country_id=TARGET, scope_type=script.ScopeType.COUNTRY,
                  error=0, valid=1, parsed=1, executed=1, raw_boolean=0,
                  kind=script.ScriptKind.TRIGGER, diagnostic=b"", truncated=0):
    """Decode the real576-byte wire result, rather than a fake boolean model."""
    return script.ScriptResult.from_bytes(script.RESULT.pack(script.RESULT.size,
        error, kind, 42, scope_type, 0, 0, 0, country_id,
        valid, parsed, executed, raw_boolean, 7372, len(diagnostic), truncated,
        0, diagnostic.ljust(512, b"\0")))


def summary(fields=d._OBLIGATION_SUMMARY, **values):
    return native_page(fields, [values])


def relation(direction, **values):
    return native_page(d._obligation_fields(direction), [values])


def subject_pages(*, types=True, summary_values=None, action_records=None):
    pages = [summary(d._SUBJECT_SUMMARY, **(summary_values or {}))]
    if types:
        pages.append(native_page(d._subject_type_fields(), []))
    pages.extend(native_page(DIPLOMATIC_ACTION_FIELDS, action_records or []) for _ in range(6))
    pages.append(summary(d._SUBJECT_PLAYS))
    return pages


class CountryDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, method, pages, *, query=None, context_type_id=5, **kwargs):
        ctx = Context(pages)
        ctx._types["Country"] = context_type_id
        ctx.adapters["script"] = "script.dll"
        api = DiplomacyAPI(ctx)
        query = NS(cleanup_error=None) if query is None else query
        with ExitStack() as stack:
            native = stack.enter_context(patch.object(data, "project_page", return_value=query))
            create = stack.enter_context(patch.object(commands, "create"))
            request = stack.enter_context(patch.object(commands, "request"))
            evaluate = stack.enter_context(patch.object(data, "evaluate"))
            trigger = stack.enter_context(patch.object(script, "trigger", return_value=NS(cleanup_error=None)))
            effect = stack.enter_context(patch.object(script, "effect"))
            parse = stack.enter_context(patch.object(script, "parse"))
            result = await getattr(api, method)(**kwargs)
        effect.assert_not_called()
        parse.assert_not_called()
        if not kwargs.get("country_types"):
            trigger.assert_not_called()
        ctx.script_trigger_calls = list(trigger.call_args_list)
        create.assert_not_called()
        request.assert_not_called()
        evaluate.assert_not_called()
        self.assertEqual(ctx.pages, [], "all requested copied pages must be consumed")
        self.assertEqual(ctx.performs, ["diplomacy." + method])
        for call in native.call_args_list:
            self.assertLessEqual(len(call.args[1]), 32)
            self.assertLessEqual(call.kwargs["limit"], 64)
            self.assertEqual([root.domain for root in call.args[2][0]], ["Country"])
            for expression in call.args[1]:
                for forbidden in ("GetStartCommand", "ShowConfirmation", "ConfirmAction", "IsValid(Command", "Serialize", "Validate"):
                    self.assertNotIn(forbidden, expression)
        return result, ctx, native

    async def test_recognition_rank_only_has_explicit_unknown_native_predicate(self):
        result, ctx, native = await self.invoke("recognition", [summary(d._RANK,
            rank="#bold Recognized power#!", rank_number=1)], journal=False)
        self.assertTrue(result["read_complete"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["recognized"])
        self.assertFalse(result["recognition_predicate_mapped"])
        self.assertFalse(result["command_validation_performed"])
        self.assertEqual(result["rank"]["rank"], "Recognized power")
        self.assertEqual(result["rank"]["rank_raw"], "#bold Recognized power#!")
        self.assertEqual(result["recognition_entries"], [])
        self.assertEqual(ctx.selected_reads, 1)
        self.assertEqual(native.call_count, 1)
        self.assertEqual(result["country_type_predicates_requested"], [])
        self.assertNotIn("country_type_observations", result)
        self.assertEqual(ctx.script_trigger_calls, [])

    async def test_recognition_empty_journal_is_not_recognized(self):
        result, _, native = await self.invoke("recognition", [summary(d._RANK),
            native_page(d._JOURNAL, []), native_page(d._JOURNAL, [])], country_id=TARGET)
        self.assertTrue(result["read_complete"])
        self.assertIsNone(result["recognized"])
        self.assertEqual(result["recognition_entries"], [])
        self.assertEqual(native.call_count, 3)
        sources = [call.kwargs["source_expression"] for call in native.call_args_list[1:]]
        self.assertEqual(sources, [d._RECOGNITION_GROUP + "." + getter + "(Country.Self)"
            for getter in ("GetActiveJournalEntryTypes", "GetInactiveJournalEntryTypes")])

    async def test_recognition_uses_real_entry_type_and_original_bar_source(self):
        entry = {"type_key": d._RECOGNITION_KEY, "entry_type_key": d._RECOGNITION_KEY,
            "complete_entry": True, "active": True}
        pages = [summary(d._RANK), native_page(d._JOURNAL, [
            {"type_key": "another_entry"}, entry]),
            native_page(d._PROGRESS, [{"entry_type_key": d._RECOGNITION_KEY,
                "current_value": data.FixedPoint(123456), "current_progress": 0.25,
                "min_value": data.FixedPoint(-500000), "max_value": data.FixedPoint(70000000)}]),
            native_page(d._JOURNAL, [])]
        result, _, native = await self.invoke("recognition", pages, country_id=TARGET)
        self.assertTrue(result["read_complete"])
        self.assertIsNone(result["recognized"], "JE completion is not recognition")
        self.assertTrue(result["recognition_entries"][0]["complete_entry"])
        bar = result["progress_bars"][0]["rows"][0]
        self.assertEqual((bar["current_value"], bar["current_progress"]), (Decimal("1.23456"), 0.25))
        self.assertEqual((bar["min_value"], bar["max_value"]), (Decimal(-5), Decimal(700)))
        call = native.call_args_list[2]
        self.assertEqual(call.kwargs["source_expression"], d._ENTRY + ".GetScriptedProgressBars")
        step, = call.kwargs["steps"]
        self.assertEqual((step.source_expression, step.index, step.expected_count),
            (d._RECOGNITION_GROUP + ".GetActiveJournalEntryTypes(Country.Self)", 1, 2))
        expressions = " ".join(expression for call in native.call_args_list for expression in call.args[1])
        self.assertIn("Country.GetJournalEntry(JournalEntryType.Self)", expressions)
        self.assertNotIn("GetJournalEntry('je_earn_recognition')", expressions)

    async def test_recognition_same_count_occurrence_drift_retains_actual_different_entry(self):
        pages = [summary(d._RANK), native_page(d._JOURNAL, [{
            "type_key": d._RECOGNITION_KEY, "entry_type_key": d._RECOGNITION_KEY}]),
            native_page(d._PROGRESS, [{"entry_type_key": "je_actual_replacement",
                "entry_name": "#bold Actual replacement#!", "current_value": data.FixedPoint(7654321)}]),
            native_page(d._JOURNAL, [])]
        result, _, native = await self.invoke("recognition", pages, country_id=TARGET)
        bars = result["progress_bars"][0]
        actual, = bars["rows"]
        self.assertEqual(actual["entry_type_key"], "je_actual_replacement")
        self.assertEqual(actual["entry_name_raw"], "#bold Actual replacement#!")
        self.assertEqual(actual["current_value"], Decimal("76.54321"))
        self.assertFalse(actual["requested_type_key_matches"])
        self.assertIn("different journal entry", actual["identity_scope_reason"])
        self.assertEqual(bars["identity_scope_reasons"][0]["actual_type_key"], "je_actual_replacement")
        self.assertFalse(bars["complete"])
        self.assertFalse(result["read_complete"])
        self.assertIn("progress_bars[0]", result["reasons"])
        self.assertEqual(result["recognition_entries"][0]["entry_type_key"], d._RECOGNITION_KEY,
            "the earlier native observation must not be overwritten by a refreshed occurrence")
        self.assertEqual(len(bars["rows"]), 1, "mismatched native observations must not be dropped")
        step, = native.call_args_list[2].kwargs["steps"]
        self.assertEqual((step.index, step.expected_count), (0, 1))
        self.assertEqual(native.call_count, 4, "drift must not trigger a hidden ordinal retry")

    async def test_recognition_optional_exact_country_types_use_original_trigger_and_id_scope(self):
        keys = ("recognized", "colonial", "unrecognized", "decentralized", "company")
        natives = [script_result(raw_boolean=value) for value in (0, 0, 2, 0, 0)]
        result, ctx, native = await self.invoke("recognition", [summary(d._RANK)] + natives,
            country_id=TARGET, journal=False, country_types=keys, context_type_id=177)
        section = result["country_type_observations"]
        self.assertTrue(section["complete"])
        self.assertTrue(result["read_complete"])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["recognized"])
        self.assertFalse(result["recognition_predicate_mapped"])
        self.assertEqual(result["country_type_predicates_requested"], list(keys))
        self.assertEqual([row["matches"] for row in section["rows"]], [False, False, True, False, False])
        self.assertEqual(section["rows"][2]["raw_boolean"], 2)
        self.assertTrue(section["rows"][2]["boolean"])
        self.assertIs(section["rows"][2]["native_result"], natives[2])
        self.assertEqual(native.call_count, 1)
        self.assertEqual(native.call_args.args[2][0][0].type_id, 177)
        self.assertEqual(len(ctx.script_trigger_calls), 5)
        for key, call in zip(keys, ctx.script_trigger_calls):
            self.assertIs(call.args[0], ctx.native)
            self.assertEqual(call.args[1], "is_country_type = " + key)
            self.assertEqual(call.args[2], script.Scope.id(script.ScopeType.COUNTRY, TARGET))
            self.assertEqual(call.args[2].type, 5, "script scope is not the DataFactory's dynamic type177")
            self.assertEqual(call.kwargs, {"adapter": "script.dll", "process": ctx.process})
        self.assertEqual(ctx.selected_reads, 0)
        self.assertIn("not the general recognition flag", section["scope"])

    async def test_recognition_exact_recognized_type_true_is_not_universal_recognition(self):
        native = script_result(raw_boolean=1)
        result, _, _ = await self.invoke("recognition", [summary(d._RANK), native],
            country_id=TARGET, journal=False, country_types=("recognized",))
        self.assertTrue(result["country_type_observations"]["rows"][0]["matches"])
        self.assertIsNone(result["recognized"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["read_complete"])

    async def test_recognition_exact_unrecognized_false_is_not_inverted_and_zero_id_is_valid(self):
        result, ctx, _ = await self.invoke("recognition", [summary(d._RANK),
            script_result(country_id=0, raw_boolean=0)],
            country_id=0, journal=False, country_types=("unrecognized",))
        row, = result["country_type_observations"]["rows"]
        self.assertFalse(row["matches"])
        self.assertEqual(row["raw_boolean"], 0)
        self.assertIsNone(result["recognized"])
        self.assertEqual(ctx.script_trigger_calls[0].args[2].payload, 0)
        self.assertTrue(result["read_complete"])

    async def test_recognition_native_trigger_error_keeps_full_result_and_continues_requested_reads(self):
        failed = script_result(error=11, parsed=0, executed=0, raw_boolean=1,
            diagnostic=b"Unknown type\xff", truncated=1)
        later = script_result(raw_boolean=0)
        result, ctx, _ = await self.invoke("recognition", [summary(d._RANK), failed, later],
            country_id=TARGET, journal=False, country_types=("recognized", "company"))
        first, second = result["country_type_observations"]["rows"]
        self.assertIs(first["native_result"], failed)
        self.assertEqual((first["error"], first["scope_valid"], first["parsed"], first["executed"]), (11, 1, 0, 0))
        self.assertEqual(first["raw_boolean"], 1)
        self.assertIsNone(first["boolean"])
        self.assertIsNone(first["matches"])
        self.assertEqual(first["diagnostic_raw"], b"Unknown type\xff")
        self.assertEqual(first["diagnostic_truncated"], 1)
        self.assertEqual(first["scope"], failed.scope)
        self.assertFalse(first["complete"])
        self.assertTrue(second["complete"])
        self.assertFalse(second["matches"])
        self.assertEqual(len(ctx.script_trigger_calls), 2, "native errors must not discard later independent requested reads")
        self.assertFalse(result["read_complete"])
        self.assertIn("country_type_observations", result["reasons"])
        self.assertIsNone(result["recognized"])

    async def test_recognition_trigger_scope_parse_execution_or_kind_gap_stays_unknown(self):
        cases = ({"valid": 0}, {"parsed": 0}, {"executed": 0},
            {"kind": script.ScriptKind.EFFECT}, {"country_id": 0},
            {"scope_type": script.ScopeType.STATE})
        for flags in cases:
            with self.subTest(flags=flags):
                actual = script_result(raw_boolean=1, **flags)
                result, _, _ = await self.invoke("recognition", [summary(d._RANK), actual],
                    country_id=TARGET, journal=False, country_types=("decentralized",))
                row, = result["country_type_observations"]["rows"]
                self.assertIs(row["native_result"], actual)
                self.assertEqual(row["boolean"], actual.boolean, "the original SDK boolean must remain visible")
                self.assertEqual(row["raw_boolean"], 1)
                self.assertIsNone(row["matches"], "unproven scope/parse/execution is not a negative equality")
                self.assertTrue(row["reasons"])
                self.assertFalse(result["read_complete"])
                self.assertIsNone(result["recognized"])

    async def test_recognition_country_type_selection_is_bounded_and_not_script_injection(self):
        for keys in ("recognized", ("recognized", "recognized"), ("fake",),
                ("recognized } always = yes",), d._COUNTRY_TYPE_KEYS + ("recognized",)):
            with self.subTest(keys=keys):
                ctx = Context([])
                with patch.object(data, "project_page") as project, patch.object(script, "trigger") as trigger:
                    with self.assertRaises(ValueError):
                        await DiplomacyAPI(ctx).recognition(country_types=keys)
                project.assert_not_called()
                trigger.assert_not_called()
                self.assertEqual(ctx.calls, [])
                self.assertEqual(ctx.selected_reads, 0)

    async def test_recognition_pending_original_trigger_query_not_replayed_or_replaced(self):
        ctx = Context([summary(d._RANK)])
        ctx.adapters["script"] = "script.dll"
        query = NS(cleanup_error=None)
        pending = RuntimeError("original native trigger is pending")
        pending.query = query
        partial = script_result(executed=0)
        pending.partial_result = partial
        with patch.object(data, "project_page", return_value=NS(cleanup_error=None)), \
             patch.object(script, "trigger", side_effect=pending) as trigger:
            with self.assertRaises(RuntimeError) as caught:
                await DiplomacyAPI(ctx).recognition(country_id=TARGET, journal=False,
                    country_types=("unrecognized", "company"))
        self.assertIs(caught.exception, pending)
        self.assertIs(pending.query, query)
        self.assertIs(pending.partial_result, partial)
        trigger.assert_called_once()
        self.assertEqual(ctx.performs, ["diplomacy.recognition"])

    async def test_recognition_inactive_source_keeps_actual_state(self):
        pages = [summary(d._RANK), native_page(d._JOURNAL, []),
            native_page(d._JOURNAL, [{"type_key": d._RECOGNITION_KEY,
                "entry_type_key": d._RECOGNITION_KEY, "active": False}]),
            native_page(d._PROGRESS, [])]
        result, _, native = await self.invoke("recognition", pages, country_id=TARGET)
        self.assertEqual(result["recognition_entries"][0]["source_state"], "inactive")
        self.assertFalse(result["recognition_entries"][0]["active"])
        self.assertIn("GetInactiveJournalEntryTypes", native.call_args.kwargs["steps"][0].source_expression)
        self.assertTrue(result["read_complete"])

    async def test_recognition_source_key_is_not_display_text_or_a_synthetic_id(self):
        pages = [summary(d._RANK), native_page(d._JOURNAL, [{
            "type_key": "#bold je_earn_recognition#!", "entry_type_key": d._RECOGNITION_KEY}]),
            native_page(d._JOURNAL, [])]
        result, _, native = await self.invoke("recognition", pages)
        self.assertEqual(result["recognition_entries"], [])
        self.assertEqual(native.call_count, 3)
        self.assertEqual(result["journal_sources"][0]["rows"][0]["type_key"], "#bold je_earn_recognition#!")

    async def test_recognition_native_source_failure_not_empty_success(self):
        pages = [summary(d._RANK), native_page(d._JOURNAL, [], error=87, valid=False),
            native_page(d._JOURNAL, [])]
        result, _, _ = await self.invoke("recognition", pages)
        self.assertFalse(result["read_complete"])
        self.assertIsNone(result["journal_sources"][0]["source_count"])
        self.assertEqual(result["journal_sources"][0]["native_pages"][0].error, 87)
        self.assertIn("journal_sources[0]", result["reasons"])
        self.assertIsNone(result["recognized"])

    async def test_recognition_partial_bar_text_and_zero_values_stay_visible(self):
        bad = copied_cell("#bold kept#!", 3, error=122, total=100)
        pages = [summary(d._RANK), native_page(d._JOURNAL, [{
            "type_key": d._RECOGNITION_KEY, "entry_type_key": d._RECOGNITION_KEY}]),
            native_page(d._PROGRESS, [{"entry_type_key": d._RECOGNITION_KEY,
                "description": bad, "current_value": data.FixedPoint(0), "current_progress": 0.0}], error=122),
            native_page(d._JOURNAL, [])]
        result, _, _ = await self.invoke("recognition", pages)
        bar = result["progress_bars"][0]["rows"][0]
        self.assertEqual((bar["description"], bar["description_raw"]), ("kept", "#bold kept#!"))
        self.assertEqual((bar["current_value"], bar["current_progress"]), (Decimal(0), 0.0))
        self.assertIn("description", bar["reasons"])
        self.assertFalse(result["read_complete"])

    async def test_recognition_pagination_uses_absolute_occurrence_not_rank_or_key_id(self):
        rows = [{"type_key": "other"} for _ in range(64)]
        pages = [summary(d._RANK), native_page(d._JOURNAL, rows, total=65),
            native_page(d._JOURNAL, [{"type_key": d._RECOGNITION_KEY,
                "entry_type_key": d._RECOGNITION_KEY}], start=64, total=65),
            native_page(d._PROGRESS, []), native_page(d._JOURNAL, [])]
        result, _, native = await self.invoke("recognition", pages)
        self.assertEqual(result["recognition_entries"][0]["source_index"], 64)
        self.assertEqual(native.call_args_list[3].kwargs["steps"][0].index, 64)
        self.assertEqual(native.call_args_list[3].kwargs["steps"][0].expected_count, 65)
        self.assertFalse(result["atomic_snapshot"])

    async def test_obligations_empty_native_relations_are_complete_not_a_permission(self):
        pages = [summary(), native_page(d._obligation_fields("from"), []),
            native_page(d._obligation_fields("to"), [])]
        result, _, native = await self.invoke("obligations", pages)
        self.assertTrue(result["complete"])
        self.assertEqual(result["action_contexts"], [])
        self.assertNotIn("can_call_in_obligation", result)
        self.assertNotIn("can_owe_obligation", result)
        self.assertEqual(native.call_count, 3)

    async def test_obligations_requested_relations_complete_when_contexts_not_requested(self):
        pages = [summary(), relation("from", relation_id=0xF0000001,
            first_country_id=TARGET, second_country_id=0, debtor_country_id=0,
            creditor_country_id=TARGET, debtor_owes_creditor=True,
            expiration_date="native FROM date", expiration_description="#bold From#!", days_left_for_creditor=0),
            relation("to", relation_id=0xF0000002, first_country_id=0,
            second_country_id=TARGET, debtor_country_id=TARGET, creditor_country_id=0,
            debtor_owes_creditor=True, expiration_date="native TO date")]
        result, _, native = await self.invoke("obligations", pages,
            country_id=TARGET, action_contexts=False)
        self.assertTrue(result["complete"])
        self.assertTrue(result["read_complete"])
        self.assertEqual(result["action_contexts_status"], "not_requested")
        source_from, source_to = result["owed_from"]["rows"][0], result["owed_to"]["rows"][0]
        self.assertEqual((source_from["first_country_id"], source_from["debtor_country_id"]), (TARGET, 0))
        self.assertEqual((source_to["first_country_id"], source_to["debtor_country_id"]), (0, TARGET))
        self.assertEqual(source_from["relation_id"], 0xF0000001)
        self.assertEqual(source_from["days_left_for_creditor"], 0)
        self.assertEqual(source_from["expiration_description_raw"], "#bold From#!")
        self.assertEqual(source_to["expiration_date"], "native TO date")
        self.assertEqual(native.call_count, 3)

    async def test_obligations_expiration_owners_match_original_gui_not_selected_country(self):
        pages = [summary(), relation("from"), relation("to")]
        _, _, native = await self.invoke("obligations", pages, action_contexts=False)
        expressions_from = native.call_args_list[1].args[1]
        expressions_to = native.call_args_list[2].args[1]
        for suffix in ("Date", "Desc"):
            self.assertIn(d._DEBTOR + ".GetObligationFromExpiration" + suffix + "(" + d._CREDITOR + ")", expressions_from)
            self.assertIn(d._CREDITOR + ".GetObligationToExpiration" + suffix + "(" + d._DEBTOR + ")", expressions_to)
        self.assertIn("DiplomaticRelations.CalcOwedObligationDaysLeft(" + d._CREDITOR + ")", expressions_from)
        self.assertNotIn("Country.GetObligationFromExpirationDate(Country.Self)", expressions_from)

    async def test_obligations_capabilities_are_per_native_action_all_five_families(self):
        pages = [summary(), relation("from", relation_id=1)]
        for family in d._ACTION_FAMILIES:
            pages.append(native_page(d._OBLIGATION_ACTIONS, [{
                "relation_id": 2, "debtor_country_id": 0, "creditor_country_id": TARGET,
                "action_id": 0xF0000004, "action_key": "native_" + family,
                "action_first_country_id": OTHER, "action_second_country_id": 0,
                "can_call_in_obligation": family == "General", "can_owe_obligation": False}]))
        pages.append(relation("to", relation_id=3))
        pages.extend(native_page(d._OBLIGATION_ACTIONS, []) for _ in d._ACTION_FAMILIES)
        result, ctx, native = await self.invoke("obligations", pages, country_id=TARGET)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["action_contexts"]), 10)
        first = result["action_contexts"][0]["rows"][0]
        self.assertEqual((first["relation_id"], first["action_id"]), (2, 0xF0000004))
        self.assertEqual((first["action_first_country_id"], first["action_second_country_id"]), (OTHER, 0))
        self.assertTrue(first["can_call_in_obligation"])
        self.assertFalse(first["can_owe_obligation"])
        self.assertFalse(result["action_contexts"][1]["rows"][0]["can_call_in_obligation"])
        self.assertEqual(result["owed_from"]["rows"][0]["relation_id"], 1,
            "a fresh callback's relation ID must not be overwritten by an ordinal join")
        for index, family in enumerate(d._ACTION_FAMILIES):
            self.assertEqual(native.call_args_list[2 + index].kwargs["source_expression"],
                d._DEBTOR + ".GetAll" + family + "DiploActions")
            self.assertEqual(native.call_args_list[8 + index].kwargs["source_expression"],
                d._CREDITOR + ".GetAll" + family + "DiploActions")
        for call in native.call_args_list:
            self.assertEqual(call.args[2][0][0].object, TARGET)
        self.assertEqual(ctx.selected_reads, 0)
        self.assertIn("original native player", result["action_actor_scope"])
        self.assertFalse(result["command_validation_performed"])

    async def test_obligations_action_page_keeps_native_errors_and_partial_rows(self):
        bad = copied_cell(False, 8, error=87)
        pages = [summary(), relation("from"), native_page(d._OBLIGATION_ACTIONS, [{
            "can_call_in_obligation": bad, "name": "#bold kept#!"}], error=87)]
        pages.extend(native_page(d._OBLIGATION_ACTIONS, []) for _ in range(4))
        pages.append(native_page(d._obligation_fields("to"), []))
        result, _, _ = await self.invoke("obligations", pages)
        row = result["action_contexts"][0]["rows"][0]
        self.assertFalse(row["can_call_in_obligation"])
        self.assertIn("can_call_in_obligation", row["reasons"])
        self.assertEqual(row["name_raw"], "#bold kept#!")
        self.assertFalse(result["read_complete"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["action_contexts_status"], "partial")

    async def test_obligations_failed_relation_source_does_not_make_empty_success(self):
        pages = [summary(), native_page(d._obligation_fields("from"), [], error=1237, valid=False),
            native_page(d._obligation_fields("to"), [])]
        result, _, native = await self.invoke("obligations", pages)
        self.assertFalse(result["complete"])
        self.assertIsNone(result["owed_from"]["source_count"])
        self.assertEqual(result["owed_from"]["native_pages"][0].error, 1237)
        self.assertEqual(native.call_count, 3)

    async def test_obligations_nested_source_count_and_actual_relation_id_are_retained(self):
        pages = [summary(), relation("from", relation_id=123)]
        pages.extend(native_page(d._OBLIGATION_ACTIONS, []) for _ in d._ACTION_FAMILIES)
        pages.append(native_page(d._obligation_fields("to"), []))
        _, _, native = await self.invoke("obligations", pages)
        for call in native.call_args_list[2:7]:
            step, = call.kwargs["steps"]
            self.assertEqual((step.source_expression, step.index, step.expected_count),
                ("Country.GetObligationsOwedFrom", 0, 1))
            self.assertIn("DiplomaticRelations.GetID", call.args[1])
            self.assertIn(d._DEBTOR + ".GetID", call.args[1])
            self.assertIn(d._CREDITOR + ".GetID", call.args[1])

    async def test_subject_six_original_sources_are_separate_complete_copied_reads(self):
        result, ctx, native = await self.invoke("subject_options", subject_pages(), country_id=TARGET)
        self.assertTrue(result["complete"])
        self.assertTrue(result["read_complete"])
        self.assertEqual(result["status"], "observed")
        expected = ["Country.Get" + scope + family + "DiploActions"
            for family in ("Subject", "Overlord") for scope in ("All", "Available", "Potential")]
        self.assertEqual([section["source_expression"] for section in result["actions"]], expected)
        self.assertEqual([call.kwargs["source_expression"] for call in native.call_args_list[2:-1]], expected)
        self.assertEqual([section["native_source_scope"] for section in result["actions"]],
            ["All", "Available", "Potential"] * 2)
        self.assertEqual(ctx.selected_reads, 0)
        self.assertFalse(result["command_validation_performed"])
        self.assertEqual(result["selected_action_eligibility"]["status"], "not_checked")
        self.assertIsNone(result["selected_action_eligibility"]["valid"])
        self.assertEqual(result["play_mode"]["status"], "not_selected")
        self.assertIsNone(result["play_mode"]["mode"])
        self.assertEqual(result["reasons"], {})

    async def test_subject_non_subject_native_false_skips_invalid_current_type_reads(self):
        result, _, native = await self.invoke("subject_options", subject_pages())
        self.assertEqual(result["target_subject_type"]["status"], "absent")
        self.assertFalse(result["target_subject_type"]["present"])
        self.assertTrue(result["target_subject_type"]["complete"])
        self.assertFalse(result["player_subject_type"]["present"])
        self.assertEqual(native.call_count, 9)
        self.assertFalse(any("GetOverlord.GetID" in expression
            for call in native.call_args_list for expression in call.args[1]))

    async def test_subject_include_types_false_completes_only_requested_scope(self):
        result, _, native = await self.invoke("subject_options", subject_pages(types=False), include_types=False)
        self.assertTrue(result["complete"])
        self.assertFalse(result["subject_types_requested"])
        self.assertNotIn("subject_types", result)
        self.assertEqual(native.call_count, 8)
        self.assertFalse(any(call.kwargs["source_expression"] == "GetSubjectTypes" for call in native.call_args_list))

    async def test_subject_native_keys_fixed_transfer_and_false_flags_are_not_invented(self):
        pages = subject_pages()
        key = "custom_subject_日本:#bold_key#!"
        pages[1] = native_page(d._subject_type_fields(), [{"key": key,
            "name": "#bold Native type#!", "income_transfer": data.FixedPoint(0),
            "accepts_only_recognized_overlord": True, "join_overlord_wars": False}])
        result, _, native = await self.invoke("subject_options", pages)
        row = result["subject_types"]["rows"][0]
        self.assertEqual(row["key"], key)
        self.assertEqual(row["name"], "Native type")
        self.assertEqual(row["name_raw"], "#bold Native type#!")
        self.assertEqual(row["income_transfer"], Decimal(0))
        self.assertFalse(row["join_overlord_wars"])
        self.assertTrue(row["accepts_only_recognized_overlord"])
        self.assertNotIn("recognized", result, "a SubjectType restriction is not country recognition")
        self.assertEqual(native.call_args_list[1].kwargs["source_expression"], "GetSubjectTypes")

    async def test_subject_target_and_player_use_actual_current_and_neighbor_types(self):
        pages = [summary(d._SUBJECT_SUMMARY, target_is_subject=True, player_is_subject=True)]
        for role, country in (("target", "Country"), ("player", "GetPlayer")):
            pages.append(summary(d._subject_current_fields(country), key=role + "_actual",
                overlord_country_id=OTHER, higher_type_valid=True, lower_type_valid=True))
            for direction, getter in (("higher", "GetHigherAutonomySubjectType"), ("lower", "GetLowerAutonomySubjectType")):
                prefix = country + ".GetSubjectType." + getter + "(" + country + ".Self)"
                pages.append(summary(d._subject_type_fields(prefix), key=role + "_" + direction))
        pages.extend(subject_pages()[1:])
        result, _, native = await self.invoke("subject_options", pages, country_id=TARGET)
        self.assertTrue(result["complete"])
        for role in ("target", "player"):
            self.assertEqual(result[role + "_subject_type"]["key"], role + "_actual")
            self.assertEqual(result[role + "_subject_type"]["overlord_country_id"], OTHER)
            for direction in ("higher", "lower"):
                self.assertEqual(result[role + "_" + direction + "_subject_type"]["key"], role + "_" + direction)
        self.assertIn("Country.GetSubjectType.GetHigherAutonomySubjectType(Country.Self).GetKey", native.call_args_list[2].args[1])
        self.assertIn("GetPlayer.GetSubjectType.GetHigherAutonomySubjectType(GetPlayer.Self).GetKey", native.call_args_list[5].args[1])
        self.assertIn("original native GetPlayer", result["actor_scope"])

    async def test_subject_invalid_adjacent_type_is_absent_not_guessed_neighbor(self):
        pages = [summary(d._SUBJECT_SUMMARY, target_is_subject=True),
            summary(d._subject_current_fields("Country"), key="actual_subject",
                higher_type_valid=False, lower_type_valid=False)] + subject_pages()[1:]
        result, _, native = await self.invoke("subject_options", pages)
        self.assertTrue(result["complete"])
        self.assertFalse(result["target_higher_subject_type"]["present"])
        self.assertFalse(result["target_lower_subject_type"]["present"])
        self.assertNotIn("key", result["target_higher_subject_type"])
        self.assertEqual(native.call_count, 10)

    async def test_subject_error_false_is_not_clean_absence(self):
        bad = copied_cell(False, 3, error=87)
        pages = [summary(d._SUBJECT_SUMMARY, target_is_subject=bad),
            summary(d._subject_current_fields("Country"), key="copied_despite_predicate_error")] + subject_pages()[1:]
        result, _, _ = await self.invoke("subject_options", pages)
        self.assertEqual(result["target_subject_type"]["key"], "copied_despite_predicate_error")
        self.assertNotIn("present", result["target_subject_type"])
        self.assertFalse(result["complete"])
        self.assertIn("target_is_subject", result["summary"]["reasons"]["fields"])

    async def test_subject_duplicate_actions_stay_in_all_six_native_sources(self):
        result, _, _ = await self.invoke("subject_options", subject_pages(
            action_records=[{"native_key": "same_native_key", "first_country_id": 0, "second_country_id": TARGET}]))
        self.assertEqual(len(result["actions"]), 6)
        for section in result["actions"]:
            self.assertEqual(section["rows"][0]["native_key"], "same_native_key")
            self.assertEqual(section["rows"][0]["first_country_id"], 0)
            self.assertEqual(section["rows"][0]["second_country_id"], TARGET)
        self.assertIsNone(result["selected_action_eligibility"]["valid"])

    async def test_subject_type_pagination129rows_has_bounded_pages(self):
        pages = [summary(d._SUBJECT_SUMMARY)]
        for start in (0, 64, 128):
            pages.append(native_page(d._subject_type_fields(), [{"key": str(i)}
                for i in range(start, min(start + 64, 129))], start=start, total=129))
        pages.extend(subject_pages()[2:])
        result, _, native = await self.invoke("subject_options", pages)
        self.assertEqual(len(result["subject_types"]["rows"]), 129)
        self.assertEqual([call.kwargs["start"] for call in native.call_args_list[1:4]], [0, 64, 128])
        self.assertEqual(native.call_count, 11)
        self.assertFalse(result["atomic_snapshot"])

    async def test_subject_source_error_preserves_rows_and_separate_action_scope_metadata(self):
        pages = subject_pages()
        pages[2] = native_page(DIPLOMATIC_ACTION_FIELDS, [{"native_key": "kept_action"}], error=87)
        result, _, _ = await self.invoke("subject_options", pages)
        self.assertFalse(result["complete"])
        self.assertFalse(result["read_complete"])
        self.assertEqual(result["actions"][0]["rows"][0]["native_key"], "kept_action")
        self.assertIn("actions[0]", result["reasons"])
        self.assertNotIn("selected_eligibility", result["reasons"])
        self.assertNotIn("play_mode", result["reasons"])
        self.assertIsNone(result["selected_action_eligibility"]["valid"])

    async def test_subject_play_previews_only_use_original_type_names_and_tooltips(self):
        result, _, native = await self.invoke("subject_options", subject_pages())
        expected = []
        for key in ("dp_annex_subject", "dp_independence"):
            prefix = "GetDiplomaticPlayType('" + key + "')"
            expected.extend((prefix + ".GetName", prefix + ".GetStartDescCountry(GetPlayer.Self,Country.Self)",
                prefix + ".GetStartTooltipCountry(GetPlayer.Self,Country.Self)"))
        self.assertEqual(native.call_args.args[1], tuple(expected))
        self.assertIsNone(result["play_mode"]["mode"])
        self.assertTrue(result["complete"])

    async def test_selected_country_is_resolved_once_then_pinned_for_every_read(self):
        cases = (("recognition", [summary(d._RANK), native_page(d._JOURNAL, []), native_page(d._JOURNAL, [])]),
            ("obligations", [summary(), native_page(d._obligation_fields("from"), []), native_page(d._obligation_fields("to"), [])]),
            ("subject_options", subject_pages()))
        for method, pages in cases:
            with self.subTest(method=method):
                _, ctx, native = await self.invoke(method, pages)
                self.assertEqual(ctx.selected_reads, 1)
                self.assertTrue(all(call.args[2][0][0].object == 0 for call in native.call_args_list))

    async def test_invalid_country_id_fails_before_native_query(self):
        for method in ("recognition", "obligations", "subject_options"):
            for country_id in (-1, 0xFFFFFFFF, True):
                with self.subTest(method=method, country_id=country_id):
                    ctx = Context([])
                    with patch.object(data, "project_page") as native:
                        with self.assertRaises(ValueError):
                            await getattr(DiplomacyAPI(ctx), method)(country_id=country_id)
                    native.assert_not_called()
                    self.assertEqual(ctx.calls, [])

    async def test_original_query_exception_and_partial_evidence_not_swallowed(self):
        class PendingContext(Context):
            async def query(self, label, factory):
                factory()
                raise pending
        pending = RuntimeError("original pending copied operation")
        pending.partial_result = object()
        query = NS(cleanup_error=None)
        ctx = PendingContext([])
        with patch.object(data, "project_page", return_value=query):
            with self.assertRaises(RuntimeError) as caught:
                await DiplomacyAPI(ctx).recognition(country_id=TARGET, journal=False)
        self.assertIs(caught.exception, pending)
        self.assertIs(pending.query, query)
        self.assertEqual(pending.projection_context["binding_rows"][0][0].object, TARGET)
        self.assertEqual(pending.projection_context["fields"], tuple(field.expression for field in d._RANK))

    async def test_cleanup_warning_is_separate_and_does_not_mask_copied_result(self):
        warning = OSError("native cleanup warning")
        query = NS(cleanup_error=warning)
        pages = [summary(), native_page(d._obligation_fields("from"), []), native_page(d._obligation_fields("to"), [])]
        result, ctx, _ = await self.invoke("obligations", pages, query=query)
        self.assertTrue(result["complete"])
        self.assertIs(ctx.diagnostics[0]["cleanup_error"], warning)
        self.assertIs(ctx.remembered[0], query)


if __name__ == "__main__":
    unittest.main()
