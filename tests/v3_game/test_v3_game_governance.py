"""CPU-only governance contracts through real copied-page composition.

The native pages and command results are fixtures. No game, DLL, SDK reload
or native command is opened. These tests prove composition and result retention,
not native gameplay acceptance or exact-version getter availability.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, localcontext
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.query_executor import PendingOperation, SerializedQueryExecutor
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.politics import governance as g


def _retained_runs(context):
    return [owner for owner in context.owner_pool.snapshot()
            if isinstance(owner, run_mod.Run) and owner.ctx is context]


class Fixture:
    def __init__(self):
        self.kinds = {}
        for value in vars(g).values():
            if isinstance(value, tuple):
                self.kinds.update((field.expression, field.kind) for field in value if isinstance(field, g.Field))
        for role in ("GetRuler", "GetHeir"):
            self.kinds.update((field.expression, field.kind) for field in g._character_fields("Country." + role))
        self.summary = {}
        self.sources = {}
        self.nested = {}
        self.field_errors = {}
        self.page_errors = {}
        self.count_invalid = set()
        self.calls = []
        self.pages = []
        self.warning = None

    def cell(self, expression, value, field, row, source):
        kind = self.kinds[expression]
        total = None
        error = self.field_errors.get((source, expression), 0)
        if isinstance(value, dict):
            kind = value.get("kind", kind)
            total = value.get("total")
            error = value.get("error", error)
            value = value.get("value")
        if value is None:
            value = "" if kind == data.Kind.STRING else False if kind == data.Kind.BOOL else 0
        snapshot = value.encode() if isinstance(value, str) else b""
        bits = value.bits if isinstance(value, data.FixedPoint) else int(value) if not isinstance(value, str) else 0
        return data.ProjectionCell(row, field, error, kind, 97, bits & 0xffffffffffffffff,
            snapshot, len(snapshot) if total is None else total,
            binding_mode=2, binding_kind=data.Kind.OBJECT,
            binding_native_type_name="original-provider-fixture")

    def project(self, native, fields, binding_rows, *, source_expression=None,
                start=0, limit=64, steps=(), source_arguments=(), **kwargs):
        assert 1 <= len(fields) <= 32
        assert len(binding_rows) == 1 and len(binding_rows[0]) == 1
        assert binding_rows[0][0].domain == "Country"
        assert not binding_rows[0][0].retained
        self.calls.append(NS(fields=fields, roots=binding_rows, source=source_expression,
            start=start, limit=limit, steps=steps, source_arguments=source_arguments))
        if steps:
            assert len(steps) == 1
            records = self.nested.get((source_expression, steps[0].index), [])
        else:
            records = [self.summary] if source_expression is None else self.sources.get(source_expression, [])
        selected = records[start:start + limit]
        cells = tuple(self.cell(expression, record.get(expression), field, row, source_expression)
            for row, record in enumerate(selected) for field, expression in enumerate(fields))
        page = data.ProjectionPage(self.page_errors.get(source_expression, 0), 0x5000, len(fields),
            start, 11, len(records), source_expression not in self.count_invalid, len(selected), cells)
        self.pages.append(page)
        return NS(result=page, cleanup_error=self.warning)


class Context:
    def __init__(self):
        self._types = {"Country": 7}
        self.adapters = {"data": "fixture-data", "console": "fixture-console"}
        self.native, self.process = object(), object()
        self.country_reads = 0
        self.performs = []
        self.queries = []
        self.remembered = []
        self.diagnostics = []
        self.executor = SerializedQueryExecutor(timeout=1)
        self.owner_pool = self.executor.owner_pool
        self.gate = None
        self.entered = asyncio.Event()

    async def country_id(self):
        self.country_reads += 1
        # A composite must pin the first selection, not follow changes.
        return 0 if self.country_reads == 1 else 0x80000002

    async def type_id(self, name):
        raise AssertionError("Cached Country type must not cause an extra world/type probe")

    async def query(self, label, factory):
        query = factory()
        self.queries.append((label, query))
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        return query.result

    async def perform(self, label, operation):
        self.performs.append(label)
        return await self.executor.perform(label, operation)

    def _remember(self, query):
        self.remembered.append(query)


class API(g.GovernanceMixin):
    def __init__(self, context):
        self._ctx = context


class NegotiationContext(Context):
    def __init__(self):
        super().__init__()
        self.world = 0x5000
        self._types["InterestGroup"] = 8
        self.adapters["commands"] = "fixture-commands"

    async def type_id(self, name):
        return self._types[name]

    async def query(self, label, factory, *, owner=None):
        return await super().query(label, factory)


class NegotiationOwner:
    def __init__(self, fixture, pointer):
        self.fixture = fixture
        self.world, self.command = fixture.generation_world, pointer
        self.pending, self.attempt = None, None

    def validate(self, *, capacity):
        self.fixture.validations += 1
        self.attempt = NS(outcome="unknown" if self.fixture.unknown_validation else "owned")
        return NS(result=self.fixture.validation)

    def submit(self, *, flags):
        assert flags == 4
        self.fixture.submissions += 1
        if self.fixture.dispatch_error:
            raise OSError("native dispatch uncertain")
        self.command = 0
        self.attempt = NS(outcome="unknown" if self.fixture.unknown_submit else "consumed")
        if self.fixture.update_state_on_submit and self.fixture.submission.accepted:
            self.fixture.pages.summary.update({"Country.IsNegotiating": True,
                "Country.GetNegotiatingInterestGroup.GetID": self.fixture.selected_id,
                "Country.GetNegotiatingInterestGroup.GetNameNoFormatting": "Observed IG"})
        return NS(result=self.fixture.submission)

    def destroy(self):
        self.fixture.destructions += 1
        self.command = 0
        self.attempt = NS(outcome="destroyed")
        return NS(result=NS(error=0, command=0))


class NegotiationFixture:
    generation_world = 0x5000

    def __init__(self, pages):
        self.pages = pages
        self.ids = [0, 0x80000002, 9]
        self.handles, self.bound = {}, {}
        self.next_handle = 1
        self.events = []
        self.producers = self.clones = self.validations = self.submissions = self.destructions = 0
        self.validation = NS(error=0, validated=True, reason="original native reason")
        self.submission = NS(error=0, accepted=True)
        self.unknown_validation = self.unknown_submit = self.dispatch_error = False
        self.update_state_on_submit = True
        self.identity_kind = data.Kind.U32
        self.selected_id = None
        self.command_bindings = None
        self.released = []
        self.owner = None

    def value(self, value, kind, *, action=data.Action.EVALUATE):
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = value
        return NS(result=NS(error=0, action=action, value_handle=handle, value=value, kind=kind))

    def evaluate(self, native, expression, bindings, **kwargs):
        self.events.append(expression)
        if expression == "Country.AccessAllInterestGroups":
            result = self.value(data.NativeList(0, 0, b"", "fixture"), data.Kind.LIST)
            self.source_handle = result.result.value_handle
            return result
        if expression == "InterestGroup.GetID":
            assert bindings[0].retained and bindings[0].object in self.handles
            return self.value(self.bound[bindings[0].object], self.identity_kind)
        if expression == "Country.StartNegotiationWith(InterestGroup.Self)":
            self.producers += 1
            self.command_bindings = bindings
            assert len(bindings) == 2 and bindings[0].country_id and bindings[1].retained
            assert self.source_handle in self.handles and bindings[1].object in self.handles
            self.selected_id = self.bound[bindings[1].object]
            return self.value(data.NativeDataCommand(0x8000, 0x8010, 0x8018,
                0, "CDataCommand", self.generation_world), data.Kind.COMMAND)
        raise AssertionError("unexpected native expression " + expression)

    def list_count(self, native, handle, **kwargs):
        assert handle == self.source_handle and handle in self.handles
        return NS(result=NS(error=0, total=len(self.ids)))

    def list_element(self, native, handle, index, **kwargs):
        assert handle == self.source_handle and handle in self.handles
        # Same owned native wrapper form required by the real _Run.elements.
        wrapper = data.NativeObject(0x9000 + index * 0x100, 0, 0,
            ".?AU?$SAnyType@V?$CPdxPtr@VCDataInterestGroup@@@@@PdxAnyImpl@@")
        result = self.value(wrapper, data.Kind.OBJECT, action=data.Action.LIST_ELEMENT)
        result.result.native_type_name = ".?AU?$SAnyType@V?$CPdxPtr@VCDataInterestGroup@@@@@PdxAnyImpl@@"
        result.result.type_id = 8
        self.bound[result.result.value_handle] = self.ids[index]
        return result

    def command_value(self, native, handle, **kwargs):
        return NS(result=NS(error=0, action=data.Action.COMMAND_VALUE, value=self.handles[handle]))

    def clone(self, native, action, **kwargs):
        assert action == commands.Action.CLONE
        assert kwargs["world"] == self.generation_world
        self.clones += 1
        self.owner = NegotiationOwner(self, 0xA000)
        return NS(result=NS(error=0, cloned_command=0xA000, owner=self.owner))

    def release(self, native, handle, **kwargs):
        self.released.append(handle)
        del self.handles[handle]
        return NS(result=NS(error=0, action=data.Action.RELEASE, value_handle=0))


class GovernanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture, self.context = Fixture(), Context()
        self.api = API(self.context)
        self.project = patch.object(data, "project_page", side_effect=self.fixture.project).start()
        self.addCleanup(patch.stopall)

    def assert_country_pinned(self, expected=0):
        self.assertEqual(self.context.country_reads, 1)
        self.assertTrue(all(call.roots[0][0].object == expected for call in self.fixture.calls))
        self.assertTrue(all(call.limit == 64 for call in self.fixture.calls))

    async def test_government_reads_coalition_ruler_and_proven_absent_heir(self):
        f = self.fixture
        f.summary.update({"Country.GetGovernment.GetName": "#bold Republic#!",
            "Country.GetGovernment.GetTransferOfPowerName": "Elected",
            "Country.HasRuler": True, "Country.HasHeir": False,
            "Country.GetRuler.GetID": -2147483646,
            "Country.GetRuler.GetFullNameNoFormatting": "原生领袖",
            "Country.GetTotalGovernmentClout": data.FixedPoint(37500),
            "Country.GetRemainingDaysForFreeGovernmentReformRaw": 0})
        f.sources["Country.AccessInterestGroupsInGovernment"] = [{"InterestGroup.GetID": 0}]
        f.sources["Country.GetPartiesInGovernment"] = [{"Party.GetNameNoFormatting": "Party"}]
        result = await self.api.government()
        self.assertTrue(result["complete"])
        self.assertEqual(result["summary"]["name"], "Republic")
        self.assertEqual(result["summary"]["name_raw"], "#bold Republic#!")
        self.assertEqual(result["summary"]["total_government_clout"], Decimal("0.375"))
        self.assertEqual(result["summary"]["free_reform_days_remaining"], 0)
        self.assertEqual(result["ruler"]["id"], 0x80000002)
        self.assertEqual(result["ruler"]["name"], "原生领袖")
        self.assertEqual(result["heir"]["status"], "absent")
        self.assertIs(result["heir"]["present"], False)
        self.assertNotIn("id", result["heir"])
        self.assertEqual(result["interest_groups"]["rows"][0]["id"], 0)
        self.assertEqual(self.context.performs, ["politics.government"])
        self.assert_country_pinned()
        self.assertFalse(any("GetHeir" in expr for call in f.calls for expr in call.fields))
        self.assertEqual(result["native_pages"], f.pages)
        self.assertIs(result["summary"]["native_pages"][0], f.pages[0])

    async def test_government_missing_presence_getter_does_not_hide_observed_character(self):
        self.fixture.summary.update({"Country.HasRuler": {"value": False, "error": 87},
                                     "Country.HasHeir": True, "Country.GetHeir.GetID": 0})
        result = await self.api.government()
        self.assertFalse(result["complete"])
        self.assertEqual(result["heir"]["id"], 0)
        self.assertEqual(result["ruler"]["id"], 0)
        self.assertNotEqual(result["ruler"]["status"], "absent")
        self.assertIn("87", result["summary"]["reasons"]["fields"]["has_ruler"])
        self.assertEqual(result["native_pages"], self.fixture.pages)

    async def test_legitimacy_keeps_native_bounds_zero_and_signed_int64(self):
        self.fixture.summary.update({"Country.GetGovernmentLegitimacy": 0,
            "Country.GetGovernmentLegitimacyLevel.GetLowerBound": -2,
            "Country.GetGovernmentLegitimacyLevel.GetUpperBound": 117,
            "Country.GetGovernmentLegitimacyDesc": "#bold 原因#!",
            "Country.GetLoyalistsOrRadicalsGainedFromLegitimacy": -5000000001})
        result = await self.api.legitimacy()
        self.assertEqual((result["legitimacy"], result["level_lower_bound"], result["level_upper_bound"]), (0, -2, 117))
        self.assertEqual(result["loyalists_or_radicals_gained"], -5000000001)
        self.assertEqual(result["breakdown"], "原因")
        self.assertEqual(result["breakdown_raw"], "#bold 原因#!")
        self.assertEqual(self.context.performs, ["politics.legitimacy"])
        self.assertEqual(len(self.fixture.calls), 1)

    async def test_partial_text_wrong_kind_and_native_errors_retain_original_values(self):
        self.fixture.summary.update({"Country.GetGovernmentLegitimacyDesc": {
            "value": "#bold copied#!", "total": 1000, "error": 234},
            "Country.GetGovernmentLegitimacy": {"value": "native text", "kind": data.Kind.STRING}})
        self.fixture.page_errors[None] = 234
        result = await self.api.legitimacy()
        self.assertFalse(result["complete"])
        self.assertEqual(result["legitimacy"], "native text")
        self.assertEqual(result["breakdown_raw"], "#bold copied#!")
        self.assertIn("differs", result["reasons"]["fields"]["legitimacy"])
        self.assertIn("incomplete", result["reasons"]["fields"]["breakdown"])
        self.assertEqual(result["native_pages"][0].error, 234)
        self.assertEqual(len(self.fixture.calls), 1)

    async def test_parties_keep_duplicate_labels_and_native_member_context_without_fake_ids(self):
        f = self.fixture
        f.sources["Country.AccessActiveParties"] = [
            {"Party.GetNameNoFormatting": "Duplicate", "Party.GetMomentum": data.FixedPoint(12345)},
            {"Party.GetNameNoFormatting": "Duplicate", "Party.GetCurrentVotingPowerShare": data.FixedPoint(0)}]
        f.nested[("Party.GetMembers", 0)] = [{"Party.GetNameNoFormatting": "Changed parent",
            "Party.GetLeader.GetID": 91, "InterestGroup.GetID": 0}]
        f.nested[("Party.GetMembers", 1)] = [{"Party.GetNameNoFormatting": "Duplicate",
            "Party.GetLeader.GetID": 92, "InterestGroup.GetID": 0x80000002}]
        result = await self.api.parties()
        rows = result["parties"]["rows"]
        self.assertEqual([row["name"] for row in rows], ["Duplicate", "Duplicate"])
        self.assertEqual(rows[0]["momentum"], Decimal("0.12345"))
        self.assertTrue(all("id" not in row and "key" not in row for row in rows))
        self.assertFalse(result["party_selector_identity"]["stable_id_available"])
        self.assertEqual(result["memberships"][0]["rows"][0]["party_name"], "Changed parent")
        self.assertEqual(result["memberships"][0]["rows"][0]["id"], 0)
        self.assertEqual(result["memberships"][1]["rows"][0]["id"], 0x80000002)
        for index, call in enumerate(f.calls[1:]):
            self.assertEqual(call.steps, (data.CreationStep("Country.AccessActiveParties", index, 2),))
            self.assertIn("Party.GetNameNoFormatting", call.fields)
            self.assertIn("InterestGroup.GetID", call.fields)
        self.assertEqual(self.context.performs, ["politics.parties"])
        self.assert_country_pinned()

    async def test_empty_party_source_is_proven_empty_not_missing(self):
        result = await self.api.parties()
        self.assertTrue(result["complete"])
        self.assertEqual(result["parties"]["source_count"], 0)
        self.assertEqual(result["parties"]["rows"], [])
        self.assertEqual(result["memberships"], [])
        self.assertEqual(len(self.fixture.calls), 1)

    async def test_nested_source_failure_keeps_parent_and_other_members(self):
        self.fixture.sources["Country.AccessActiveParties"] = [{"Party.GetNameNoFormatting": "Observed"}]
        self.fixture.page_errors["Party.GetMembers"] = 1237
        self.fixture.count_invalid.add("Party.GetMembers")
        result = await self.api.parties()
        self.assertFalse(result["complete"])
        self.assertEqual(result["parties"]["rows"][0]["name"], "Observed")
        self.assertEqual(result["memberships"][0]["native_pages"][0].error, 1237)
        self.assertIn("memberships[0]", result["reasons"])
        self.assertEqual(len(self.fixture.calls), 2)

    async def test_elections_reads_timetable_confidence_votes_and_history_without_manual_action(self):
        self.fixture.summary.update({"Country.HasElections": False,
            "Country.GetElectoralConfidence": data.FixedPoint(123456789),
            "Country.GetElection.GetEligibleVoters": 5000000001,
            "Country.GetElection.GetTotalVoters": 0,
            "Country.GetElection.GetVoterTurnout": data.FixedPoint(0),
            "Country.GetNextElectionDate": "1840.1.2",
            "Country.GetElection.GetLastElectionYearRaw": 1836,
            "Country.GetElection.HasPreviousElection": True})
        self.fixture.sources["Country.GetElection.GetParties"] = [{"Party.GetNumVotersLastElection": 3000000001}]
        self.fixture.sources["Country.GetElection.GetIndependentIGs"] = [{"InterestGroup.GetID": 0}]
        self.fixture.sources["Country.AccessLastElectionParties"] = [{"Party.GetNameNoFormatting": "Historical"}]
        with localcontext() as context:
            context.prec = 2
            result = await self.api.elections()
        summary = result["summary"]
        self.assertIs(summary["has_elections"], False)
        self.assertEqual(summary["electoral_confidence"], Decimal("1234.56789"))
        self.assertEqual(summary["eligible_voters"], 5000000001)
        self.assertEqual(summary["voter_turnout"], Decimal(0))
        self.assertEqual(result["parties"]["rows"][0]["last_election_voters"], 3000000001)
        self.assertEqual(result["last_election_parties"]["rows"][0]["name"], "Historical")
        self.assertEqual([call.source for call in self.fixture.calls], [None,
            "Country.GetElection.GetParties", "Country.GetElection.GetIndependentIGs", "Country.AccessLastElectionParties"])
        self.assertEqual(self.context.performs, ["politics.elections"])
        self.assert_country_pinned()

    async def test_negotiation_preserves_all_igs_and_does_not_claim_eligibility(self):
        self.fixture.summary.update({"Country.IsNegotiating": False,
            "Country.GetNumAvailableNegotiations": 0,
            "Country.GetLawBeingEnacted.GetNameNoFormatting": "Law",
            "Country.GetLawBeingEnacted.GetIGsApproveList": "Approving native list"})
        self.fixture.sources["Country.AccessAllInterestGroups"] = [
            {"InterestGroup.GetID": 0, "InterestGroup.GetAmenability": data.FixedPoint(0), "InterestGroup.HasNegotiated": True},
            {"InterestGroup.GetID": 9, "InterestGroup.GetAmenability": data.FixedPoint(9900000), "InterestGroup.HasNegotiated": False}]
        result = await self.api.negotiation_options()
        self.assertFalse(result["complete"])
        self.assertTrue(result["read_complete"])
        self.assertFalse(result["eligibility_checked"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["summary"]["available_negotiations"], 0)
        self.assertEqual(result["summary"]["approving_interest_groups"], "Approving native list")
        self.assertEqual([row["id"] for row in result["candidates"]["rows"]], [0, 9])
        self.assertIs(result["candidates"]["rows"][0]["has_negotiated"], True)
        self.assertFalse(any("StartNegotiationWith" in expr or "IsValid(" in expr
            for call in self.fixture.calls for expr in call.fields))
        self.assertEqual(self.context.performs, ["politics.negotiation_options"])
        self.assert_country_pinned()

    async def test_invalid_negotiating_id_is_preserved_not_replaced_with_zero(self):
        self.fixture.summary["Country.GetNegotiatingInterestGroup.GetID"] = 0xffffffff
        result = await self.api.negotiation_options()
        self.assertEqual(result["summary"]["negotiating_interest_group_id"], 0xffffffff)
        self.assertFalse(result["read_complete"])
        self.assertIn("sentinel", result["summary"]["reasons"]["fields"]["negotiating_interest_group_id"])

    async def test_ig_nested_ideologies_traits_leadership_keep_original_parent_id(self):
        f = self.fixture
        f.sources["Country.AccessAllInterestGroups"] = [{"InterestGroup.GetID": 1,
            "InterestGroup.GetLeader.GetID": -2147483646,
            "InterestGroup.GetPoliticalStrength": 6000000001}]
        # Simulate a changed parent at the same occurrence. Rows expose the
        # actual parent seen in that callback, rather than being joined to ID1.
        f.nested[("InterestGroup.AccessAllIdeologies", 0)] = [{
            "InterestGroup.GetID": 3, "Ideology.GetNameNoFormatting": "Liberal",
            "Ideology.GetLawStanceDesc": "Exact stance"}]
        f.nested[("InterestGroup.AccessTraits", 0)] = [{
            "InterestGroup.GetID": 3, "InterestGroupTrait.GetNameNoFormatting": "Trait",
            "InterestGroupTrait.IsEnabled(InterestGroup.Self)": False}]
        f.nested[("InterestGroup.GetPossibleLeaderIdeologies", 0)] = [{
            "InterestGroup.GetID": 3, "Ideology.GetNameNoFormatting": "Candidate ideology"}]
        result = await self.api.interest_group_politics()
        root = result["interest_groups"]["rows"][0]
        self.assertEqual((root["id"], root["leader_id"], root["political_strength"]), (1, 0x80000002, 6000000001))
        ideology = result["ideologies"][0]["rows"][0]
        trait = result["traits"][0]["rows"][0]
        self.assertEqual(ideology["interest_group_id"], 3)
        self.assertEqual(ideology["law_stances"], "Exact stance")
        self.assertNotIn("id", ideology)
        self.assertEqual(trait["interest_group_id"], 3)
        self.assertIs(trait["enabled"], False)
        self.assertNotIn("id", trait)
        self.assertEqual(len(f.calls[0].fields), 32)
        self.assertEqual(f.calls[2].source, "InterestGroup.AccessTraits")
        self.assertIn("InterestGroupTrait.IsEnabled(InterestGroup.Self)", f.calls[2].fields)
        self.assertTrue(all(call.steps == (data.CreationStep("Country.AccessAllInterestGroups", 0, 1),)
            for call in f.calls[1:]))
        self.assert_country_pinned()
        self.assertEqual(self.context.performs, ["politics.interest_group_politics"])

    async def test_ig_129_rows_use_three_bounded_pages_with_details_disabled(self):
        self.fixture.sources["Country.AccessAllInterestGroups"] = [
            {"InterestGroup.GetID": index} for index in range(129)]
        result = await self.api.interest_group_politics(details=False)
        self.assertEqual([call.start for call in self.fixture.calls], [0, 64, 128])
        self.assertEqual(len(result["interest_groups"]["rows"]), 129)
        self.assertEqual(result["interest_groups"]["rows"][-1]["id"], 128)
        self.assertEqual(result["interest_groups"]["rows"][-1]["source_index"], 128)
        self.assertFalse(result["details_included"])
        self.assertNotIn("traits", result)
        self.assertTrue(result["complete"])
        self.assertEqual(result["native_pages"], self.fixture.pages)

    async def test_cleanup_warning_cannot_hide_observed_government(self):
        self.fixture.warning = OSError("copied storage cleanup warning")
        result = await self.api.government()
        self.assertTrue(result["complete"])
        warnings = [item for item in self.context.diagnostics if "cleanup_error" in item]
        self.assertEqual(len(warnings), len(self.fixture.calls))
        self.assertTrue(all(item["cleanup_error"] is self.fixture.warning for item in warnings))
        self.assertEqual([item["result"] for item in warnings], self.fixture.pages)
        self.assertEqual(len(self.context.remembered), len(self.fixture.calls))
        self.assertEqual(result["native_pages"], self.fixture.pages)

    async def test_pending_composite_keeps_one_owner_and_resumes_without_replay(self):
        self.context.executor.timeout = 0
        self.context.gate = asyncio.Event()
        pending = await self.api.government()
        self.assertIsInstance(pending, PendingOperation)
        await self.context.entered.wait()
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertEqual(self.context.performs, ["politics.government"])
        self.context.gate.set()
        result = await pending.resume(timeout=1)
        self.assertTrue(result["complete"])
        self.assertEqual(len([call for call in self.fixture.calls if call.source is None]), 1)
        self.assertEqual(len(self.fixture.calls), 3)
        self.assertTrue(pending.done)
        self.assert_country_pinned()

    async def test_explicit_country_id_never_reads_selected_player(self):
        result = await self.api.elections(country_id=0x80000002)
        self.assertEqual(result["country_id"], 0x80000002)
        self.assertEqual(self.context.country_reads, 0)
        self.assertTrue(all(call.roots[0][0].object == 0x80000002 for call in self.fixture.calls))


class NegotiationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.pages, self.context = Fixture(), NegotiationContext()
        self.fixture = NegotiationFixture(self.pages)
        self.api = politics.PoliticsAPI(self.context)
        self.patchers = [patch.object(data, name, getattr(self.fixture, name)) for name in
            ("evaluate", "list_count", "list_element", "command_value", "release")]
        self.patchers.extend((patch.object(data, "project_page", side_effect=self.pages.project),
            patch.object(commands, "request", side_effect=self.fixture.clone),
            patch.object(commands.NativeCommand, "from_result", side_effect=lambda native, result, **kw: result.owner)))
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_cleaned(self):
        self.assertEqual(self.fixture.handles, {})
        self.assertEqual(_retained_runs(self.context), [])
        self.assertFalse(self.api._mutation_lock.locked())

    async def test_selects_real_full_id_in_retained_graph_and_submits_once(self):
        result = await self.api.negotiate(0x80000002)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertTrue(result["effect_observed"])
        self.assertEqual(result["interest_group_id"], 0x80000002)
        self.assertEqual(result["country_id"], 0)
        self.assertIs(result["validation"], self.fixture.validation)
        self.assertIs(result["submission"], self.fixture.submission)
        self.assertEqual((self.fixture.producers, self.fixture.clones, self.fixture.validations, self.fixture.submissions), (1, 1, 1, 1))
        self.assertEqual(self.fixture.events.count("InterestGroup.GetID"), 2)
        self.assertEqual(self.fixture.command_bindings[0].object, 0)
        self.assertEqual(self.fixture.bound[self.fixture.command_bindings[1].object], 0x80000002)
        self.assertEqual(self.context.country_reads, 1)
        self.assertEqual(self.context.performs, ["politics.negotiate"])
        self.assertEqual(len(self.pages.calls), 1)
        self.assert_cleaned()

    async def test_carrier_generation_world_not_stale_context_controls_clone_adoption(self):
        self.context.world = 0  # First action before a cached world is populated.
        result = await self.api.negotiate(0, readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.fixture.owner.world, self.fixture.generation_world)
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (1, 1, 1))
        self.assertEqual(self.context.world, 0)  # No refresh/extra world receipt.
        self.assert_cleaned()

    async def test_zero_full_id_is_valid_and_readback_can_be_disabled(self):
        result = await self.api.negotiate(0, readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.fixture.selected_id, 0)
        self.assertEqual(self.fixture.events.count("InterestGroup.GetID"), 1)
        self.assertEqual(self.pages.calls, [])
        self.assertIsNone(result["effect_observed"])
        self.assert_cleaned()

    async def test_source_index_is_never_treated_as_an_ig_id(self):
        result = await self.api.negotiate(1)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["accepted"])
        self.assertIn("not in Country.AccessAllInterestGroups", result["reason"])
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (0, 0, 0))
        self.assertEqual(self.pages.calls, [])
        self.assert_cleaned()

    async def test_native_rejection_is_returned_without_submission_or_candidate_probes(self):
        self.fixture.validation.validated = False
        self.fixture.validation.reason = "#bold Native negotiation rule#!"
        result = await self.api.negotiate(9)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "#bold Native negotiation rule#!")
        self.assertIs(result["validation"], self.fixture.validation)
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (1, 1, 0))
        self.assertEqual(self.fixture.destructions, 1)
        self.assert_cleaned()

    async def test_validation_partial_reason_is_not_retried(self):
        self.fixture.validation.error = 122
        result = await self.api.negotiate(0)
        self.assertTrue(result["accepted"])
        self.assertIs(result["validation"], self.fixture.validation)
        self.assertEqual(result["validation"].error, 122)
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (1, 1, 1))
        self.assert_cleaned()

    async def test_unknown_validation_keeps_original_graph_without_submit(self):
        self.fixture.unknown_validation = True
        result = await self.api.negotiate(0)
        self.assertFalse(result["ownership_settled"])
        self.assertIs(result["validation"], self.fixture.validation)
        self.assertEqual(self.fixture.submissions, 0)
        self.assertNotEqual(self.fixture.handles, {})
        self.assertEqual(len(_retained_runs(self.context)), 1)
        self.assertEqual(self.fixture.released, [])
        self.assertEqual(self.pages.calls, [])

    async def test_accepted_with_unknown_ownership_keeps_acceptance_and_skips_readback(self):
        self.fixture.unknown_submit = True
        result = await self.api.negotiate(0)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["ownership_settled"])
        self.assertIs(result["submission"], self.fixture.submission)
        self.assertEqual(self.fixture.submissions, 1)
        self.assertEqual(self.fixture.released, [])
        self.assertIn("unsettled", result["readback_skipped"])
        self.assertEqual(self.pages.calls, [])

    async def test_readback_false_does_not_erase_accepted_delivery_or_claim_completion(self):
        self.fixture.update_state_on_submit = False
        result = await self.api.negotiate(0)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["effect_observed"])
        self.assertFalse(result["completed"])
        self.assertEqual(self.fixture.submissions, 1)
        self.assert_cleaned()

    async def test_readback_native_error_preserves_raw_result_and_acceptance(self):
        self.pages.field_errors[(None, "Country.GetNegotiatingInterestGroup.GetID")] = 13
        result = await self.api.negotiate(0)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["readback_complete"])
        self.assertIsNone(result["effect_observed"])
        self.assertIn("13", result["after"]["reasons"]["fields"]["interest_group_id"])
        self.assertEqual(result["after"]["interest_group_id"], 0)
        self.assertIs(result["submission"], self.fixture.submission)
        self.assertEqual(self.fixture.submissions, 1)
        self.assert_cleaned()

    async def test_readback_exception_does_not_erase_selected_command_result(self):
        failure = OSError("readback unavailable")
        async def fail(*args, **kwargs):
            raise failure
        with patch.object(g, "country_snapshot", fail):
            result = await self.api.negotiate(0)
        self.assertTrue(result["accepted"])
        self.assertIs(result["readback_error"], failure)
        self.assertIs(result["submission"], self.fixture.submission)
        self.assertEqual(self.fixture.submissions, 1)
        self.assert_cleaned()

    async def test_wrong_native_identity_kind_never_coerces_or_generates(self):
        self.fixture.identity_kind = data.Kind.I32
        result = await self.api.negotiate(0)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(self.fixture.producers, 0)
        ids = [value for label, value in result["native_results"] if label == "InterestGroup.GetID"]
        self.assertEqual(len(ids), len(self.fixture.ids))
        self.assertEqual(ids[0].kind, data.Kind.I32)
        self.assertEqual(ids[0].value, 0)
        self.assertTrue(all(value.kind == data.Kind.I32 for value in ids))
        self.assertEqual([value.value for value in ids], self.fixture.ids)
        issues = result["selection_issues"]
        queries = [query for label, query in self.context.queries if label.endswith(".InterestGroup.GetID")]
        elements = [value for label, value in result["native_results"]
                    if label == "Country.AccessAllInterestGroups.element"]
        self.assertEqual(len(issues), len(ids))
        self.assertEqual(len(queries), len(ids))
        self.assertEqual(len(elements), len(ids))
        for index, issue in enumerate(issues):
            self.assertEqual(issue["source_index"], index)
            self.assertIs(issue["native_result"], ids[index])
            self.assertIs(issue["query"], queries[index])
            self.assertIs(issue["query"].result, ids[index])
            self.assertIs(issue["source_result"], elements[index])
            self.assertEqual(issue["binding"].object, elements[index].value_handle)
            self.assertTrue(issue["binding"].retained)
            self.assertEqual(issue["binding"].type_id, elements[index].type_id)
            self.assertEqual(self.fixture.bound[issue["binding"].object], self.fixture.ids[index])
            self.assertIsNone(issue["error"])
            self.assertIsNone(issue["partial_result"])
            self.assertEqual(issue["reason"], "InterestGroup.GetID did not return a valid native uint32 reference")
        self.assertIsNone(result["accepted"])
        self.assertIsNone(result["validation"])
        self.assertIsNone(result["submission"])
        self.assertIsNone(result["effect_observed"])
        self.assertEqual((self.fixture.clones, self.fixture.validations, self.fixture.submissions), (0, 0, 0))
        self.assertIsNone(self.fixture.command_bindings)
        self.assertEqual(self.pages.calls, [])
        self.assert_cleaned()

    async def test_pending_action_holds_mutation_lock_and_original_selection(self):
        self.context.executor.timeout = 0
        self.context.gate = asyncio.Event()
        pending = await self.api.negotiate(0)
        self.assertIsInstance(pending, PendingOperation)
        await self.context.entered.wait()
        self.assertTrue(self.api._mutation_lock.locked())
        self.assertEqual(self.fixture.events, ["Country.AccessAllInterestGroups"])
        self.context.gate.set()
        result = await pending.resume(timeout=1)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.fixture.events.count("Country.AccessAllInterestGroups"), 1)
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (1, 1, 1))
        self.assertEqual(self.context.performs, ["politics.negotiate"])
        self.assert_cleaned()

    async def test_dispatch_exception_keeps_graph_and_never_replays(self):
        self.fixture.dispatch_error = True
        with self.assertRaisesRegex(OSError, "dispatch uncertain"):
            await self.api.negotiate(0)
        self.assertEqual((self.fixture.producers, self.fixture.validations, self.fixture.submissions), (1, 1, 1))
        self.assertEqual(self.fixture.released, [])
        self.assertEqual(len(_retained_runs(self.context)), 1)
        self.assertEqual(self.pages.calls, [])

    async def test_invalid_input_has_no_native_side_effects(self):
        for value in (True, False, -1, 0xffffffff, 0x100000000, "1", {"source_index": 0}, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.api.negotiate(value)
        self.assertEqual(self.context.performs, [])
        self.assertEqual(self.context.queries, [])
        self.assertEqual(self.fixture.events, [])


if __name__ == "__main__":
    unittest.main()
