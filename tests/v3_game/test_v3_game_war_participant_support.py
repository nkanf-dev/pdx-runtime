"""Genuine-source participant Pages. CPU mocks only, never game/VM/native calls.

081 supplies two independent single-leaf observations, not this five-field Page
acceptance. These tests cover the current consumer/wire and retention behavior.
"""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pdx_runtime import v3_data as data, v3_objects as objects
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_game.military import _warfare as warfare
from pdx_runtime.v3_game.military import MilitaryAPI
from tests.v3_game.test_v3_game_warfare_reads import WarfareReadFixture, TYPES


FIELDS = (
    "WarParticipant.GetCountry.GetID",
    "WarParticipant.GetWarSupportDesc",
    "WarParticipant.GetWarSupportDescDelayed",
    "WarParticipant.GetExhaustionDesc",
    "WarParticipant.GetCanCapitulateDesc",
)
DESC_KEYS = ("war_support_description", "war_support_description_delayed",
             "exhaustion_description", "capitulation_description")
_ORIGINAL_PROJECT_PAGE = data.project_page


class ParticipantPageFixture(WarfareReadFixture):
    def __init__(self, *, dynamic_type=17713, wire=False):
        super().__init__()
        self.participant_dynamic_type = dynamic_type  # Synthetic VM enumeration, never a version ID.
        self.process = Mock(pid=1)
        self.process.resolve_export.return_value = 0xA100
        self.process.export_owner.return_value = 0xA000
        self.process.allocate.return_value = 0x2000
        self.native = Mock(pid=1)
        self.status = {"status": "ok", "request_id": 7, "completed_request_id": 7}
        self.native.request.side_effect = lambda command: (
            {"status": "ok", "request_id": 7, "error": 0}
            if command.startswith("interface-callback ") else self.status)
        self.page_calls, self.pages, self.events = [], [], []
        self.participant_rows = {
            "War.GetAttackers": [self.row(self.ATTACKER_ID, "Attacker")],
            "War.GetDefenders": [self.row(self.DEFENDER_ID, "Defender")],
        }
        self.field_kinds, self.field_errors, self.field_totals = {}, {}, {}
        self.page_errors, self.count_valid = {}, {}
        self.row_limits = {}
        self.wire = wire
        self.copy_fault = None
        self.pending = False
        self.pending_error = None
        self.wire_query = None

    @staticmethod
    def row(country, label):
        return (country, "#bold " + label + " support#!", label + " delayed",
                label + " exhaustion", label + " capitulation")

    async def blocking(self, function, *args):
        return function(*args)

    async def type_id(self, name):
        if name == "WarParticipant":
            return self.participant_dynamic_type
        return await super().type_id(name)

    def evaluate(self, native, expression, bindings, **kwargs):
        self.events.append(("evaluate", expression))
        if "AccessParticipant" in expression:
            raise AssertionError("fresh Country -> AccessParticipant is not this source route")
        return super().evaluate(native, expression, bindings, **kwargs)

    async def query(self, label, factory, **kwargs):
        self.queries.append(label)
        result = factory()
        if isinstance(result, data.DataQuery):
            value = result.read()
            if value is None:
                self.pending_error = TimeoutError("original participant Page is pending")
                self.pending_error.query = result
                raise self.pending_error
            return value
        return result

    def project_page(self, native, fields, binding_rows, *, source_expression=None,
                     start=0, limit=64, steps=(), source_arguments=(), **kwargs):
        fields, binding_rows = tuple(fields), tuple(tuple(row) for row in binding_rows)
        if fields != FIELDS or source_expression not in self.participant_rows:
            raise AssertionError((fields, source_expression))
        if len(binding_rows) != 1 or len(binding_rows[0]) != 1:
            raise AssertionError("participant Page has one War root, no Country or Participant input")
        war = binding_rows[0][0]
        if not war.retained or war.type_id != TYPES["War"]:
            raise AssertionError("Page needs the original owned War")
        self._war_id(war)
        if steps or source_arguments:
            raise AssertionError("genuine source needs no fabricated typed arguments")
        self.page_calls.append({"fields": fields, "bindings": binding_rows[0],
            "source": source_expression, "start": start, "limit": limit,
            "dynamic_source_type": self.participant_dynamic_type})
        self.events.append(("page", source_expression, start))
        if self.wire:
            return self.wire_page(native, fields, binding_rows, source_expression, start, limit, kwargs)
        values = self.participant_rows[source_expression]
        amount = min(limit, self.row_limits.get(source_expression, limit), max(0, len(values) - start))
        cells = []
        for row_index in range(start, start + amount):
            for column, value in enumerate(values[row_index]):
                key = (source_expression, row_index, column)
                kind = self.field_kinds.get(key, data.Kind.I32 if column == 0 else data.Kind.STRING)
                raw = value.encode() if isinstance(value, str) else b""
                bits = int(value) & ((1 << 64) - 1) if type(value) in (int, bool) else 0
                cells.append(data.ProjectionCell(row_index, column, self.field_errors.get(key, 0),
                    kind, TYPES["int32"] if column == 0 else TYPES["CString"], bits,
                    raw, self.field_totals.get(key, len(raw)), binding_mode=1,
                    binding_kind=data.Kind.RAW))
        page = data.ProjectionPage(self.page_errors.get(source_expression, 0), self.world,
            5, start, 4321, len(values), self.count_valid.get(source_expression, True), amount, tuple(cells))
        self.pages.append(page)
        return page

    def wire_page(self, native, fields, bindings, source, start, limit, kwargs):
        query = _ORIGINAL_PROJECT_PAGE(native, fields, bindings, source_expression=source,
                                       start=start, limit=limit, **kwargs)
        self.wire_query = query
        self.wire_raw = bytearray(self.process.write.call_args.args[1])
        header = list(data.PAGE_REQUEST.unpack_from(self.wire_raw))
        values = self.participant_rows[source][0]
        table, payload = bytearray(5 * data.PAGE_CELL.size), bytearray()
        for column, value in enumerate(values):
            snapshot = value.encode() if isinstance(value, str) else b""
            data.PAGE_CELL.pack_into(table, column * data.PAGE_CELL.size, 0, column, 0,
                data.Kind.I32 if column == 0 else data.Kind.STRING,
                TYPES["int32"] if column == 0 else TYPES["CString"], 1,
                value & 0xffffffff if column == 0 else 0, len(table) + len(payload),
                len(snapshot), len(snapshot), data.Kind.RAW, 0, 0, 0)
            payload.extend(snapshot)
        copied = bytes(table + payload)
        header[1], header[11], header[12], header[13], header[14] = 0, 4321, 1, 1, 1
        header[15], header[16], header[17] = 0x3000, len(copied), 5
        data.PAGE_REQUEST.pack_into(self.wire_raw, 0, *header)
        def read(address, count):
            if address == 0x2000:
                return bytes(self.wire_raw)
            if self.copy_fault is not None and address == 0x3000 + 65536:
                raise self.copy_fault
            offset = address - 0x3000
            return copied[offset:offset + count]
        self.process.read.side_effect = read
        if self.pending:
            self.status["completed_request_id"] = 6
        return query

    def patches(self):
        stack = super().patches()
        stack.enter_context(patch.object(data, "project_page", self.project_page))
        return stack


class GenuineParticipantSupportTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_details_use_exact_five_leaves_and_actual_source_row_country(self):
        ctx = ParticipantPageFixture()
        # Scalar legacy source would report Attacker; the actual Page row is Defender.
        ctx.participant_rows["War.GetAttackers"] = [ctx.row(ctx.DEFENDER_ID, "Page row")]
        with ctx.patches():
            result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        participant = result["participants"][0]
        self.assertEqual(participant["country_id"], ctx.DEFENDER_ID)
        self.assertEqual(participant["country_id_raw"], ctx.DEFENDER_ID - (1 << 32))
        self.assertEqual(participant["country_id_bytes"], ctx.DEFENDER_ID.to_bytes(4, "little"))
        self.assertEqual(participant["name"], "Defender")
        self.assertEqual(participant["support_details"]["war_support_description_raw"], "#bold Page row support#!")
        self.assertEqual(participant["support_details"]["war_support_description"], "Page row support")
        self.assertIs(participant["country_id_cell"], ctx.pages[0].cells[0])
        self.assertIs(participant["support_details"]["native_cells"]["war_support_description"], ctx.pages[0].cells[1])
        self.assertFalse(participant["atomic_snapshot"])
        self.assertEqual(len(ctx.page_calls), 2)
        for call in ctx.page_calls:
            self.assertEqual(call["fields"], FIELDS)
            self.assertEqual(len(call["bindings"]), 1)
            self.assertTrue(call["bindings"][0].retained)
            self.assertEqual(call["limit"], 64)
        self.assertFalse(any("Participant" in call[3] for call in ctx.projection_calls))
        self.assertEqual(result["id"], ctx.WAR_ID)
        self.assertFalse(ctx.handles)

    async def test_multirow_65_pages_finish_side_copy_before_legacy_enrichment(self):
        ctx = ParticipantPageFixture()
        rows = [ctx.row(10000 + index, "Row" + str(index)) for index in range(65)]
        ctx.participant_rows["War.GetAttackers"] = rows
        for country, *_ in rows:
            ctx.country_names[country] = "Country " + str(country)
            ctx.war_support[(ctx.WAR_ID, country)] = data.FixedPoint(700000)
            ctx.casualties[(ctx.WAR_ID, country)] = data.FixedPoint(0)
        with ctx.patches():
            result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        attackers = [row for row in result["participants"] if row["side"] == "attacker"]
        self.assertEqual([row["country_id"] for row in attackers], list(range(10000, 10065)))
        self.assertEqual([row["source_index"] for row in attackers], list(range(65)))
        self.assertEqual(attackers[64]["support_details"]["war_support_description"], "Row64 support")
        pages = result["participant_support_sources"]["attacker"]["native_pages"]
        self.assertEqual([(page.start, page.row_count) for page in pages], [(0, 64), (64, 1)])
        first = ctx.events.index(("page", "War.GetAttackers", 0))
        second = ctx.events.index(("page", "War.GetAttackers", 64))
        self.assertEqual(second, first + 1)
        self.assertEqual(len(ctx.page_calls), 3)

    async def test_64_rows_use_one_source_page_and_dynamic_type_is_not_hardcoded(self):
        for dynamic in (17713, 23007):
            ctx = ParticipantPageFixture(dynamic_type=dynamic)
            ctx.participant_rows["War.GetAttackers"] = [ctx.row(-1, "Invalid") for _ in range(64)]
            with ctx.patches():
                result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
            self.assertEqual(len([call for call in ctx.page_calls if call["source"] == "War.GetAttackers"]), 1)
            self.assertEqual(ctx.page_calls[0]["dynamic_source_type"], dynamic)
            self.assertEqual(len(result["participants"]), 65)
            self.assertEqual(ctx.pages[0].row_count, 64)

    async def test_country_sentinels_raw_signed_bits_errors_and_incomplete_are_not_usable_ids(self):
        ctx = ParticipantPageFixture()
        ctx.participant_rows["War.GetAttackers"] = [ctx.row(-1, "Signed"), ctx.row(0xffffffff, "Unsigned"),
            ctx.row(156, "Error"), ctx.row(156, "Incomplete"), ctx.row("156", "Wrong kind")]
        ctx.field_kinds[("War.GetAttackers", 1, 0)] = data.Kind.U32
        ctx.field_errors[("War.GetAttackers", 2, 0)] = 21
        ctx.field_totals[("War.GetAttackers", 3, 0)] = 4
        ctx.field_kinds[("War.GetAttackers", 4, 0)] = data.Kind.STRING
        with ctx.patches():
            result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        rows = result["participants"][:5]
        self.assertEqual([row["country_id_raw"] for row in rows], [-1, 0xffffffff, 156, 156, "156"])
        self.assertTrue(all(row["country_id"] is None for row in rows))
        self.assertTrue(all("country_id" in row["unavailable"] for row in rows))
        self.assertEqual(rows[0]["country_id_bytes"], b"\xff" * 4)
        self.assertEqual(rows[2]["country_id_error"], 21)
        self.assertEqual(rows[3]["country_id_cell"].total, 4)
        self.assertEqual(rows[4]["country_id_bytes"], b"156")
        self.assertTrue(all("war_support" not in row for row in rows))
        self.assertEqual(rows[2]["support_details"]["war_support_description"], "Error support")

    async def test_partial_string_empty_and_zero_false_none_mismatches_survive(self):
        ctx = ParticipantPageFixture()
        values = list(ctx.participant_rows["War.GetAttackers"][0])
        values[1:] = ["partial", "", False, None]
        ctx.participant_rows["War.GetAttackers"] = [tuple(values), ctx.row(-1, "Zero")]
        ctx.field_totals[("War.GetAttackers", 0, 1)] = 30
        ctx.field_errors[("War.GetAttackers", 0, 1)] = 122
        ctx.field_kinds[("War.GetAttackers", 0, 3)] = data.Kind.BOOL
        ctx.field_kinds[("War.GetAttackers", 0, 4)] = data.Kind.EMPTY
        zero = list(ctx.participant_rows["War.GetAttackers"][1]); zero[1] = 0
        ctx.participant_rows["War.GetAttackers"][1] = tuple(zero)
        ctx.field_kinds[("War.GetAttackers", 1, 1)] = data.Kind.I32
        with ctx.patches():
            result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        support = result["participants"][0]["support_details"]
        self.assertEqual(support["war_support_description_raw"], "partial")
        self.assertIn("122", support["unavailable"]["war_support_description"])
        self.assertEqual(support["war_support_description_delayed_raw"], "")
        self.assertNotIn("war_support_description_delayed", support["unavailable"])
        self.assertIs(support["exhaustion_description_raw"], False)
        self.assertIsNone(support["capitulation_description_raw"])
        self.assertEqual(result["participants"][1]["support_details"]["war_support_description_raw"], 0)

    async def test_source_error_preserves_prefix_and_does_not_continue_has_more(self):
        ctx = ParticipantPageFixture()
        ctx.participant_rows["War.GetAttackers"] = [ctx.row(-1, "Prefix") for _ in range(65)]
        ctx.page_errors["War.GetAttackers"] = 21
        with ctx.patches():
            result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        source = result["participant_support_sources"]["attacker"]
        self.assertEqual(source["status"], "partial")
        self.assertFalse(source["membership_complete"])
        self.assertEqual(source["native_pages"][0].error, 21)
        self.assertTrue(source["native_pages"][0].has_more)
        self.assertEqual(len([row for row in result["participants"] if row["side"] == "attacker"]), 64)
        self.assertEqual(len([call for call in ctx.page_calls if call["source"] == "War.GetAttackers"]), 1)
        self.assertEqual(result["participant_coverage"], "partial_native_side_lists")
        self.assertFalse(ctx.source_count_calls and any("GetAttackers" == call[1] for call in ctx.source_count_calls))

    async def test_empty_and_unobserved_counts_are_not_conflated(self):
        for failure in (False, True):
            ctx = ParticipantPageFixture()
            ctx.participant_rows["War.GetAttackers"] = []
            ctx.participant_rows["War.GetDefenders"] = []
            if failure:
                ctx.count_valid = {source: False for source in ctx.participant_rows}
            with ctx.patches():
                result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
            self.assertEqual(result["participants"], None if failure else [])
            self.assertEqual(result["participant_coverage"], "partial_native_side_lists" if failure else "native_side_lists")
            self.assertEqual(result["participant_support_sources"]["attacker"]["status"], "partial" if failure else "observed")

    async def test_nonadvancing_source_returns_partial_once(self):
        ctx = ParticipantPageFixture()
        ctx.row_limits["War.GetAttackers"] = 0
        with ctx.patches():
            result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        source = result["participant_support_sources"]["attacker"]
        self.assertFalse(source["membership_complete"])
        self.assertIn("did not advance", source["details"][0]["reason"])
        self.assertEqual(len([call for call in ctx.page_calls if call["source"] == "War.GetAttackers"]), 1)

    async def test_known_no_page_has_only_legacy_facts_and_explicit_unavailable_support(self):
        ctx = WarfareReadFixture()
        with ctx.patches(), patch.object(data, "project_page") as page:
            result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        page.assert_not_called()
        self.assertEqual(result["participants"][0]["country_id"], ctx.ATTACKER_ID)
        support = result["participants"][0]["support_details"]
        self.assertEqual(support["status"], "unavailable")
        self.assertIsNone(support["war_support_description_raw"])
        self.assertFalse(any("GetWarSupportDesc" in expression for expression in ctx.expressions))
        self.assertFalse(ctx.handles)


    def process_no_detail_probe(self, ctx):
        ctx.process.resolve_export.assert_not_called()

    async def test_explicit_opt_in_uses_genuine_source_without_country_or_participant_root(self):
        ctx = ParticipantPageFixture()
        with ctx.patches():
            result = await warfare.country_wars(MilitaryAPI(ctx), ctx.WAR_ID, include_support_details=True)
        self.assertEqual(result["wars"][0]["participants"][0]["support_details"]["status"], "observed")
        self.assertEqual(ctx.list_element_sources, ["War"])
        self.assertTrue(all(len(call["bindings"]) == 1 for call in ctx.page_calls))

    async def test_actual_wire_copyfault_retains_graph_and_resumes_same_query_bytes_only(self):
        ctx = ParticipantPageFixture(wire=True)
        values = list(ctx.participant_rows["War.GetAttackers"][0]); values[1] = "x" * 150000
        ctx.participant_rows["War.GetAttackers"][0] = tuple(values)
        ctx.copy_fault = OSError("original participant copied-tail fault")
        api = MilitaryAPI(ctx)
        with ctx.patches(), self.assertRaises(OSError) as caught:
            await api.war(ctx.WAR_ID).read()
        self.assertIs(caught.exception, ctx.copy_fault)
        self.assertIs(caught.exception.query, ctx.wire_query)
        self.assertEqual(caught.exception.projection_context["fields"], FIELDS)
        self.assertEqual(len(ctx.page_calls), 1)
        self.assertTrue(ctx.handles)
        self.assertFalse(ctx.releases)
        self.assertFalse(ctx.wire_query.partial_result.cells[1].complete)
        self.assertEqual(len(api._warfare_runs), 1)
        ctx.copy_fault = None
        completed = caught.exception.query.read()
        self.assertEqual(len(completed.cells[1].snapshot), 150000)
        self.assertEqual(completed.value_handle, 0)
        self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                             for call in ctx.native.request.call_args_list), 1)
        raw = ctx.process.write.call_args.args[1]
        header = data.PAGE_REQUEST.unpack_from(raw)
        self.assertEqual((header[4], header[6], header[10]), (5, 1, 64))
        self.assertEqual(raw[header[7]:header[7] + header[8]], b"War.GetAttackers")
        self.assertEqual(ctx.process.resolve_export.call_args.args[1], "PdxV3DataProjectPageInterface")

    async def test_actual_wire_pending_keeps_original_query_and_stops_other_side(self):
        ctx = ParticipantPageFixture(wire=True)
        ctx.pending = True
        api = MilitaryAPI(ctx)
        with ctx.patches(), self.assertRaises(TimeoutError) as caught:
            await api.war(ctx.WAR_ID).read()
        self.assertIs(caught.exception, ctx.pending_error)
        self.assertIs(caught.exception.query, ctx.wire_query)
        ctx.process.read.assert_not_called()
        ctx.process.free.assert_not_called()
        self.assertEqual(len(ctx.page_calls), 1)
        self.assertFalse(ctx.releases)
        self.assertTrue(ctx.handles)
        self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                             for call in ctx.native.request.call_args_list), 1)

    async def test_cancelled_page_propagates_original_query_without_legacy_or_other_source(self):
        ctx = ParticipantPageFixture()
        error = asyncio.CancelledError("original participant source cancellation")
        query = SimpleNamespace(adapter="fake", partial_result=None)
        error.query = query
        original = ctx.project_page
        def cancel(*args, **kwargs):
            original(*args, **kwargs)
            raise error
        with ctx.patches(), patch.object(data, "project_page", cancel), self.assertRaises(asyncio.CancelledError) as caught:
            await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        self.assertIs(caught.exception, error)
        self.assertIs(error.query, query)
        self.assertEqual(len(ctx.page_calls), 1)
        self.assertFalse(ctx.releases)
        self.assertTrue(ctx.handles)
        self.assertFalse(any(expression == "War.GetWarSupport(Country.Self)" for expression in ctx.expressions))

    async def test_copied_country_zero_is_valid_without_truthiness_replacement(self):
        ctx = ParticipantPageFixture()
        ctx.participant_rows["War.GetAttackers"] = [ctx.row(0, "Zero Country")]
        ctx.war_support[(ctx.WAR_ID, 0)] = data.FixedPoint(0)
        ctx.casualties[(ctx.WAR_ID, 0)] = data.FixedPoint(0)
        with ctx.patches():
            result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        row = result["participants"][0]
        self.assertEqual(row["country_id"], 0)
        self.assertEqual(row["country_id_raw"], 0)
        self.assertEqual(row["country_id_bytes"], b"\x00" * 4)
        self.assertEqual(row["war_support"], 0)
        self.assertEqual(row["support_details"]["war_support_description"], "Zero Country support")

    async def test_known_missing_page_export_uses_no_detail_submission(self):
        ctx = ParticipantPageFixture()
        ctx.process.resolve_export.side_effect = BridgeError("resolve_export", 127, reason="export not found")
        with ctx.patches():
            result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        self.assertEqual(ctx.page_calls, [])
        self.assertEqual(result["participants"][0]["support_details"]["status"], "unavailable")
        self.assertEqual(result["participants"][0]["country_id"], ctx.ATTACKER_ID)
        ctx.process.resolve_export.assert_called_once()

    async def test_preflight_access_error_is_not_optional_absence_or_legacy_fallback(self):
        ctx = ParticipantPageFixture()
        error = BridgeError("resolve_export", 5, reason="access denied")
        ctx.process.resolve_export.side_effect = error
        with ctx.patches(), self.assertRaises(BridgeError) as caught:
            await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        self.assertIs(caught.exception, error)
        self.assertEqual(ctx.page_calls, [])
        self.assertEqual(ctx.source_count_calls, [])
        self.assertFalse(any("GetWarSupport(Country.Self)" in expression for expression in ctx.expressions))

    async def test_unknown_wire_submission_keeps_original_query_without_second_source(self):
        ctx = ParticipantPageFixture(wire=True)
        error = TimeoutError("original source submission delivery unknown")
        ctx.native.request.side_effect = error
        api = MilitaryAPI(ctx)
        with ctx.patches(), self.assertRaises(TimeoutError) as caught:
            await api.war(ctx.WAR_ID).read()
        self.assertIs(caught.exception, error)
        self.assertIsInstance(error.query, data.ProjectionPageQuery)
        self.assertEqual(error.projection_context["fields"], FIELDS)
        self.assertEqual(len(ctx.page_calls), 1)
        self.assertEqual(ctx.native.request.call_count, 1)
        ctx.process.free.assert_not_called()
        self.assertTrue(ctx.handles)
        self.assertFalse(ctx.releases)
        self.assertEqual(len(api._warfare_runs), 1)

    async def test_cleanup_warning_does_not_hide_copied_descriptions(self):
        ctx = ParticipantPageFixture()
        def release_error(native, handle, **kwargs):
            return SimpleNamespace(error=21, value_handle=handle)
        with ctx.patches(), patch.object(data, "release", release_error):
            result = await MilitaryAPI(ctx).war(ctx.WAR_ID).read()
        self.assertEqual(result["participants"][0]["support_details"]["war_support_description"], "Attacker support")
        self.assertEqual(result["cleanup"]["status"], "native_error_retained")
        self.assertEqual(result["cleanup"]["native_result"].error, 21)


if __name__ == "__main__":
    unittest.main()
