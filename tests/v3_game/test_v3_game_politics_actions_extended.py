"""CPU-only original Law/Amendment choice and selected-action contracts.

The generic retained owner/query fixture is shared with bloc action tests. All
Law/Amendment source models, copied fields and assertions below are independent.
"""
from types import SimpleNamespace as NS
from dataclasses import replace
import asyncio
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.politics import actions as political
from pdx_runtime.v3_game._choices import OwnedChoice, OwnedChoices, ChoicesBusyError
from tests.v3_game.test_v3_game_bloc_actions import Run, Owner, copied, evaluate, count, element, project as owned_project


KINDS = {f.expression: f.kind for fields in (political._LAWS, political._AMENDMENT_CHOICES,
    political._LAW_AFTER, political._AMENDMENT_AFTER, political._GOVERNMENT_IGS,
    political._GOVERNMENT_PARTIES, political._GOVERNMENT_IDS) for f in fields}


def native_object(handle, named_type="Law", *, raw=False, error=0):
    spelling = (".?AU?$SAnyType@PEAVCData" + named_type + "@@@PdxAnyImpl@@" if raw else
                ".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@")
    native_type_id = {"Law": 6, "Amendment": 7, "InterestGroup": 8, "Party": 9}[named_type]
    return NS(action=data.Action.LIST_ELEMENT, error=error, value_handle=handle,
        native_type_name=spelling, type_id=native_type_id,
        value=data.NativeObject(0x1000 + handle, native_type_id, handle, spelling))


def project(*args, **kwargs):
    q = owned_project(*args, **kwargs)
    q.source = kwargs.get("source_expression")
    q.start = kwargs.get("start", 0)
    return q


def native_page(query, rows, *, error=0, total=None):
    cells = []
    for index, row in enumerate(rows):
        for position, expression in enumerate(query.fields):
            kind = KINDS[expression]
            default = "same native name" if kind == data.Kind.STRING else False if kind == data.Kind.BOOL else data.FixedPoint(0) if kind == data.Kind.FIXED else 0
            value = row.get(expression, default)
            spec = value if isinstance(value, NS) else copied(value)
            value = spec.value
            kind = kind if spec.kind is None else spec.kind
            snapshot = value.encode() if kind == data.Kind.STRING else b""
            bits = value.bits if isinstance(value, data.FixedPoint) else (int(value) & 0xffffffffffffffff if kind != data.Kind.STRING else 0)
            cells.append(data.ProjectionCell(index, position, spec.error, kind, 9, bits, snapshot,
                len(snapshot) if spec.total is None else spec.total))
    return data.ProjectionPage(error, 17, len(query.fields), query.start, 3,
        len(rows) if total is None else total, True, len(rows), tuple(cells))


class Context:
    def __init__(self):
        self.native, self.process, self.world = object(), object(), 17
        self.adapters = {"data": "original-data", "commands": "commands"}
        self._types = {"Country": 5, "Law": 6, "Amendment": 7, "InterestGroup": 8, "Party": 9}
        self.sources = {source: [] for source in (*political._ACTIVE_LAW_SOURCES, *political._CANDIDATE_LAW_SOURCES)}
        self.sources[political._ACTIVE_LAW_SOURCES[0]] = [native_object(10)]
        self.sources[political._CANDIDATE_LAW_SOURCES[0]] = [native_object(30), native_object(31)]
        self.enacting = native_object(50)
        self.alternatives = {10: [native_object(60), native_object(61)]}
        self.amendments = [native_object(200, "Amendment"), native_object(201, "Amendment")]
        self.active_amendments = [self.amendments[1]]
        groups = [native_object(300 + i, "InterestGroup") for i in range(5)]
        self.sources["Country.AccessAllInterestGroups"] = groups
        self.sources["Country.AccessInterestGroupsInGovernment"] = [groups[i] for i in (0, 1, 3)]
        self.sources["Country.AccessActiveParties"] = [native_object(400, "Party"), native_object(401, "Party")]
        self.party_members = {400: [groups[2], groups[4]], 401: [groups[1], groups[4]]}
        self.collections = {}
        self.next_source = 1000
        self.calls, self.performs, self.runs, self.generated = [], [], [], []
        self.diagnostics, self.remembered = [], []
        self.country_reads = 0
        self.validation = NS(error=0, validated=1, reason="native eligibility reason")
        self.submission = NS(error=0, accepted=1)
        self.unknown_validation = self.unknown_submission = False
        self.dispatch_error = self.cleanup_error = self.readback_error = None
        self.source_error = self.count_error = self.copy_error = 0
        self.fail_element = None
        self.row_fields = {}
        self.fields_by_handle = {300 + i: {"InterestGroup.GetID": full_id}
            for i, full_id in enumerate((0, 10, 0x80000001, 20, 30))}
    async def country_id(self):
        self.country_reads += 1
        return 0
    async def type_id(self, name):
        return self._types[name]
    async def perform(self, label, work):
        self.performs.append(label)
        return await work()
    async def query(self, label, factory, owner=None):
        q = factory()
        self.calls.append((label, q))
        if not hasattr(q, "operation"):
            return q
        if q.operation == "evaluate":
            if q.expression == "Country.GetLawBeingEnacted":
                return NS(**{**vars(self.enacting), "action": data.Action.EVALUATE, "error": self.source_error})
            if q.expression in self.sources:
                rows = self.sources[q.expression]
            elif q.expression == "Law.GetLawAlternatives":
                rows = self.alternatives[q.bindings[0].object]
            elif q.expression == "Law.GetAllAmendments":
                rows = self.amendments
            elif q.expression == "Law.GetActiveAmendments":
                rows = self.active_amendments
            elif q.expression == "Party.GetMembers":
                rows = self.party_members[q.bindings[0].object]
            else:
                raise AssertionError("No invented source/candidate command: " + q.expression)
            handle = self.next_source
            self.next_source += 1
            self.collections[handle] = (q.expression, rows)
            return NS(action=data.Action.EVALUATE, value_handle=handle, error=self.source_error,
                value=data.NativeList(handle, 0x2000, b"", "original retained provider"))
        if q.operation == "count":
            return NS(action=data.Action.LIST_COUNT, value_handle=0, error=self.count_error,
                total=len(self.collections[q.handle][1]))
        if q.operation == "element":
            source, rows = self.collections[q.handle]
            if self.fail_element == (source, q.index):
                raise OSError("original source uncertain")
            return rows[q.index]
        if q.operation == "page":
            if self.generated and self.readback_error:
                raise self.readback_error
            if q.source is not None:
                source = self.party_members[q.bindings[0][0].object] if q.source == "Party.GetMembers" else self.sources[q.source]
                rows = [{**self.row_fields, **self.fields_by_handle.get(value.value_handle, {})}
                    for value in source[q.start:q.start + q.limit]]
                return native_page(q, rows, error=self.copy_error, total=len(source))
            rows = [{**self.row_fields, **self.fields_by_handle.get(bindings[-1].object, {})} for bindings in q.bindings]
            return native_page(q, rows, error=self.copy_error)
        raise AssertionError(q.operation)
    def _remember(self, query):
        self.remembered.append(query)


class API(political.PoliticalActionsMixin):
    def __init__(self, context):
        self._ctx = context
        self._mutation_lock = asyncio.Lock()


class PoliticalActionsExtendedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for target, name, replacement in ((run_mod, "Run", Run), (data, "evaluate", evaluate),
                (data, "list_count", count), (data, "list_element", element), (data, "project_page", project)):
            patcher = patch.object(target, name, side_effect=replacement) if target is data else patch.object(target, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def laws(self, *, context=None, country_id=0, scope="imposition"):
        api = API(context or Context())
        return api, await api.law_choices(country_id, scope=scope)

    def pages(self, ctx):
        return [q for _, q in ctx.calls if getattr(q, "operation", None) == "page"]

    def evaluations(self, ctx):
        return [q for _, q in ctx.calls if getattr(q, "operation", None) == "evaluate"]

    def assert_bounds(self, ctx):
        for q in self.pages(ctx):
            self.assertLessEqual(len(q.fields), 32)
            self.assertLessEqual(len(q.bindings), 64)
            self.assertEqual(q.limit, 64)
            for bindings in q.bindings:
                self.assertLessEqual(sum(b.domain == "Country" or b.country_id for b in bindings), 1)
                self.assertLessEqual(sum(b.type_id == 6 for b in bindings), 1)

    async def test_imposition_uses_exact_ui_active_law_then_alternatives_no_duplicate_law_binding(self):
        api, catalog = await self.laws(country_id=0x80000001)
        expressions = [q.expression for q in self.evaluations(api._ctx)]
        self.assertEqual(expressions, [political._ACTIVE_LAW_SOURCES[0], "Law.GetLawAlternatives", *political._ACTIVE_LAW_SOURCES[1:]])
        self.assertEqual(len(catalog), 2)
        self.assertEqual(catalog[0]._binding.object, 60)
        self.assertEqual(catalog[1]._binding.object, 61)
        self.assertEqual(self.evaluations(api._ctx)[1].bindings[0].object, 10)
        self.assertEqual([b.object for b in self.pages(api._ctx)[0].bindings[0]], [0x80000001, 60])
        self.assertEqual(api._ctx.country_reads, 0)
        self.assertEqual(api._ctx.generated, [])
        self.assertEqual(api._ctx.performs, ["politics.law_choices"])
        self.assert_bounds(api._ctx)
        await catalog.close()

    async def test_law_owner_and_effects_come_from_leaf_not_requested_country_or_parent_index(self):
        ctx = Context()
        ctx.row_fields = {"Law.GetCountry.GetID": -2147483646,
            "Law.GetCountry.GetName": "actual native law country",
            "Law.GetNameNoFormatting": "#bold native law#!", "Law.GetOnImposeDesc": "native imposition effects",
            "Law.IsActive": False, "Law.IsVisible": True}
        api, catalog = await self.laws(context=ctx, country_id=5)
        self.assertEqual(catalog.metadata["result"]["source_country_id"], 5)
        self.assertEqual(catalog[0]["law_country_id"], 0x80000002)
        self.assertEqual(catalog[0]["law_country_name"], "actual native law country")
        self.assertEqual(catalog[0]["law_name"], "native law")
        self.assertEqual(catalog[0]["on_impose"], "native imposition effects")
        self.assertIs(catalog[0]["active"], False)
        self.assertTrue(catalog[0]["visible"])
        self.assertNotIn("id", catalog[0])
        self.assertNotIn("law_id", catalog[0])
        await catalog.close()

    async def test129_alternatives_three_bulk_pages_no_per_field_queries(self):
        ctx = Context()
        ctx.alternatives[10] = [native_object(100 + i) for i in range(129)]
        api, catalog = await self.laws(context=ctx)
        self.assertEqual(len(catalog), 129)
        self.assertEqual([len(q.bindings) for q in self.pages(ctx)], [64, 64, 1])
        self.assertEqual([q.expression for q in self.evaluations(ctx)].count("Law.GetLawAlternatives"), 1)
        self.assertEqual(ctx.generated, [])
        self.assert_bounds(ctx)
        await catalog.close()

    async def test_active_candidates_enacting_all_sources_are_explicit_and_keep_overlaps(self):
        for scope, expected_count in (("active", 1), ("candidates", 2), ("enacting", 1), ("all", 4)):
            api, catalog = await self.laws(scope=scope)
            self.assertEqual(len(catalog), expected_count)
            self.assertFalse(any(q.expression == "Law.GetLawAlternatives" for q in self.evaluations(api._ctx)))
            self.assertEqual(api._ctx.generated, [])
            self.assertEqual(api._ctx.performs, ["politics.law_choices"])
            await catalog.close()


    async def test_raw_parent_and_raw_leaf_are_diagnostics_never_retagged(self):
        for raw_parent in (True, False):
            ctx = Context()
            if raw_parent:
                ctx.sources[political._ACTIVE_LAW_SOURCES[0]] = [native_object(10, raw=True)]
            else:
                ctx.alternatives[10][0] = native_object(60, raw=True)
            api, catalog = await self.laws(context=ctx)
            self.assertEqual(len(catalog), 0 if raw_parent else 1)
            self.assertEqual(len(catalog.metadata["result"]["rejected_rows"]), 1)
            self.assertFalse(catalog.metadata["result"]["complete"])
            if raw_parent:
                self.assertFalse(any(q.expression == "Law.GetLawAlternatives" for q in self.evaluations(ctx)))
            await catalog.close()

    async def test_amendments_are_genuine_child_choices_with_native_parent_sponsor_timers(self):
        api, laws = await self.laws()
        api._ctx.row_fields = {"Amendment.GetAttachedLaw.GetNameNoFormatting": "actual attached parent",
            "Law.GetNameNoFormatting": "current provider parent", "Amendment.GetSponsor.GetID": 0x80000002,
            "Amendment.IsActive": False, "Amendment.GetNumEndorsingInterestGroups": 0,
            "Amendment.GetTimeoutDate": "1 January 1841"}
        amendments = await api.amendment_choices(laws[1])
        self.assertIs(amendments.metadata["law_choice"], laws[1])
        self.assertEqual(len(amendments), 2)
        self.assertEqual(amendments[0]["law_name"], "actual attached parent")
        self.assertEqual(amendments[0]["provider_law_name"], "current provider parent")
        self.assertEqual(amendments[0]["sponsor_id"], 0x80000002)
        self.assertIs(amendments[0]["active"], False)
        self.assertEqual(amendments[0]["endorsing_interest_groups_count"], 0)
        self.assertEqual(amendments[0]["timeout_date"], "1 January 1841")
        self.assertEqual([b.object for b in self.pages(api._ctx)[-1].bindings[0]], [61, 200])
        self.assertEqual(api._ctx.generated, [])
        self.assert_bounds(api._ctx)
        with self.assertRaises(ChoicesBusyError):
            await laws.close()
        await amendments.close()
        await laws.close()

    async def test_active_only_selects_original_GetActiveAmendments_provider_not_local_filter(self):
        api, laws = await self.laws()
        amendments = await api.amendment_choices(laws[0], active_only=True)
        self.assertEqual(len(amendments), 1)
        self.assertEqual(amendments[0]._binding.object, 201)
        self.assertEqual(self.evaluations(api._ctx)[-1].expression, "Law.GetActiveAmendments")
        self.assertIs(amendments[0]["active"], False)  # keep even a contradictory copied native value
        await amendments.close()
        await laws.close()

    async def test_impose_uses_exact_chosen_leaf_only_once_despite_names_and_provider_reorder(self):
        api, laws = await self.laws(country_id=0x80000001)
        api._ctx.alternatives[10].reverse()
        result = await api.impose_law(laws[1], readback=False)
        expression, bindings, run = api._ctx.generated[0]
        self.assertEqual(expression, "Law.StartImposing")
        self.assertEqual([b.object for b in bindings], [61])
        self.assertEqual([b.type_id for b in bindings], [6])
        self.assertEqual((run.owners[0].validations, run.owners[0].submissions), (1, 1))
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertIn("Law.GetCountry", result["target_scope"])
        self.assertEqual(api._ctx.country_reads, 0)
        self.assertEqual(api._ctx.performs, ["politics.law_choices", "politics.impose_law"])
        await laws.close()

    async def test_repeal_selected_original_amendment_one_validation_submission_without_country_alias(self):
        api, laws = await self.laws(scope="active")
        amendments = await api.amendment_choices(laws[0])
        result = await api.repeal_amendment(amendments[1], readback=False)
        expression, bindings, run = api._ctx.generated[0]
        self.assertEqual(expression, "Amendment.Repeal")
        self.assertEqual([b.object for b in bindings], [201])
        self.assertEqual((run.owners[0].validations, run.owners[0].submissions), (1, 1))
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        with self.assertRaises(ChoicesBusyError):
            await laws.close()
        await amendments.close()
        await laws.close()

    async def test_native_rejection_and_validation_error_do_not_submit(self):
        for error, validated in ((0, 0), (87, 1)):
            api, laws = await self.laws()
            api._ctx.validation.error, api._ctx.validation.validated = error, validated
            result = await api.impose_law(laws[0])
            owner = api._ctx.generated[0][2].owners[0]
            self.assertEqual((owner.validations, owner.submissions), (1, 0))
            self.assertEqual(result["accepted"], None if error else False)
            self.assertEqual(result["reason"], "native eligibility reason")
            self.assertNotIn("after", result)
            await laws.close()

    async def test_partial_validation_reason_keeps_accepted_result_without_second_validation(self):
        api, laws = await self.laws()
        api._ctx.validation.error = 122
        result = await api.impose_law(laws[0], readback=False)
        owner = api._ctx.generated[0][2].owners[0]
        self.assertTrue(result["accepted"])
        self.assertEqual((owner.validations, owner.submissions), (1, 1))
        self.assertIs(result["validation"], api._ctx.validation)
        await laws.close()

    async def test_impose_readback_is_law_observation_not_journal_entry_or_passage_receipt(self):
        api, laws = await self.laws()
        api._ctx.row_fields = {"Law.IsActive": True, "Law.IsBeingEnacted": False}
        result = await api.impose_law(laws[0])
        self.assertTrue(result["accepted"])
        self.assertTrue(result["after"]["selected"]["active"])
        self.assertIs(result["after"]["selected"]["being_enacted"], False)
        self.assertFalse(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertFalse(result["after"]["completion_claimed"])
        self.assertIn("not a legal-imposition journal-entry receipt", result["after"]["observation_scope"])
        self.assertEqual(len(api._ctx.generated), 1)
        self.assertEqual(len(api._ctx.performs), 2)
        await laws.close()

    async def test_repeal_readback_preserves_inactive_error_and_does_not_claim_completion(self):
        api, laws = await self.laws()
        amendments = await api.amendment_choices(laws[0])
        api._ctx.row_fields = {"Amendment.IsActive": copied(False, error=87), "Amendment.HasCooledDown": False}
        result = await api.repeal_amendment(amendments[0])
        self.assertTrue(result["accepted"])
        row = result["after"]["selected"]
        self.assertIs(row["active"], False)
        self.assertIn("active", row["reasons"])
        self.assertFalse(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertEqual([b.object for b in self.pages(api._ctx)[-1].bindings[0]], [200])
        await amendments.close()
        await laws.close()

    async def test_readback_failure_preserves_acceptance_and_source_graph(self):
        api, laws = await self.laws()
        api._ctx.readback_error = OSError("read uncertain")
        result = await api.impose_law(laws[0])
        self.assertTrue(result["accepted"])
        self.assertIs(result["readback_error"], api._ctx.readback_error)
        self.assertTrue(api._ctx.runs[-1].retain_unknown)
        self.assertEqual(api._ctx.generated[0][2].owners[0].submissions, 1)
        with self.assertRaises(ChoicesBusyError):
            await laws.close()

    async def test_unsettled_amendment_submission_keeps_child_and_parent_sources(self):
        api, laws = await self.laws()
        amendments = await api.amendment_choices(laws[0])
        api._ctx.unknown_submission = True
        result = await api.repeal_amendment(amendments[0])
        self.assertTrue(result["accepted"])
        self.assertFalse(result["ownership_settled"])
        self.assertIn("readback_skipped", result)
        self.assertEqual(api._ctx.generated[0][2].cleaned, 0)
        with self.assertRaises(ChoicesBusyError):
            await amendments.close()
        with self.assertRaises(ChoicesBusyError):
            await laws.close()

    async def test_unknown_dispatch_retains_exact_selected_original_without_replay(self):
        api, laws = await self.laws()
        api._ctx.dispatch_error = OSError("unknown submit")
        with self.assertRaises(OSError):
            await api.impose_law(laws[0])
        run = api._ctx.generated[0][2]
        self.assertEqual(run.owners[0].submissions, 1)
        self.assertEqual(run.cleaned, 0)
        self.assertIn(run, api._ctx.owner_pool.snapshot())
        with self.assertRaises(ChoicesBusyError):
            await laws.close()

    async def test_partial_copy_text_zero_false_and_native_pages_remain_visible(self):
        ctx = Context()
        ctx.copy_error = 122
        ctx.row_fields = {"Law.GetNameNoFormatting": copied("#bold kept#!", error=122, total=100),
            "Law.IsSupported": copied(False, error=87), "Law.PredictLegitimacyChangeToEnact": 0}
        api, laws = await self.laws(context=ctx)
        self.assertEqual(laws[0]["law_name"], "kept")
        self.assertEqual(laws[0]["law_name_raw"], "#bold kept#!")
        self.assertIs(laws[0]["supported"], False)
        self.assertEqual(laws[0]["legitimacy_change"], 0)
        self.assertFalse(laws.metadata["result"]["complete"])
        self.assertEqual(laws.metadata["result"]["native_pages"][0].error, 122)
        await laws.close()

    async def test_source_transport_error_exposes_actual_partial_catalog_and_original_owners(self):
        ctx = Context()
        ctx.alternatives[10] = [native_object(100 + i) for i in range(66)]
        ctx.fail_element = ("Law.GetLawAlternatives", 65)
        with self.assertRaises(OSError) as caught:
            await API(ctx).law_choices(0)
        catalog = caught.exception.choices
        self.assertIsInstance(catalog, OwnedChoices)
        self.assertEqual(len(catalog), 64)
        self.assertTrue(catalog.owner.retain_unknown)
        self.assertIn(catalog.owner, ctx.owner_pool.snapshot())
        self.assertIn(10, catalog.owner.values)
        self.assertEqual(ctx.generated, [])

    async def test_empty_source_and_count_error_are_not_conflated(self):
        for error in (0, 87):
            ctx = Context()
            ctx.sources = {source: [] for source in ctx.sources}
            ctx.count_error = error
            api, laws = await self.laws(context=ctx)
            self.assertEqual(len(laws), 0)
            self.assertEqual(laws.metadata["result"]["complete"], not bool(error))
            self.assertEqual(laws.metadata["result"]["status"], "observed" if not error else "unavailable")
            await laws.close()

    async def test_unadmitted_names_dicts_ids_wrong_types_foreign_and_closed_choices_rejected(self):
        api, laws = await self.laws()
        for value in ("same native name", 0, 61, None, dict(laws[0])):
            with self.assertRaises(TypeError):
                await api.impose_law(value)
        with self.assertRaises(TypeError):
            await api.repeal_amendment(laws[0])
        forged = OwnedChoice(laws, laws[0].native_result, "Law", laws[0]._binding, {})
        with self.assertRaises(ValueError):
            await api.impose_law(forged)
        with self.assertRaises(ValueError):
            await API(Context()).impose_law(laws[0])
        self.assertEqual(api._ctx.generated, [])
        await laws.close()
        with self.assertRaises(RuntimeError):
            await api.impose_law(laws[0])

    async def test_invalid_scope_boolean_and_country_inputs_do_not_dispatch(self):
        api, laws = await self.laws()
        before = len(api._ctx.calls)
        for call in (lambda: api.law_choices(scope="invented"), lambda: api.law_choices(True),
                lambda: api.amendment_choices(laws[0], active_only=1),
                lambda: api.impose_law(laws[0], readback=None)):
            with self.assertRaises(ValueError):
                await call()
        self.assertEqual(len(api._ctx.calls), before)
        self.assertEqual(api._ctx.generated, [])
        await laws.close()

    async def test_native_source_and_owned_element_errors_keep_actual_partial_choices(self):
        ctx = Context()
        ctx.source_error = 87
        api, laws = await self.laws(context=ctx)
        self.assertEqual(len(laws), 2)
        self.assertFalse(laws.metadata["result"]["complete"])
        self.assertTrue(laws.metadata["result"]["reasons"])
        ctx.source_error = 0
        ctx.amendments[0] = native_object(200, "Amendment", error=87)
        amendments = await api.amendment_choices(laws[0])
        self.assertEqual(len(amendments), 2)
        self.assertEqual(amendments[0].native_result.error, 87)
        self.assertFalse(amendments.metadata["result"]["complete"])
        self.assertEqual(ctx.generated, [])
        await amendments.close()
        await laws.close()

    async def test_cleanup_warning_preserves_accepted_repeal_and_actual_parent_graph(self):
        api, laws = await self.laws()
        amendments = await api.amendment_choices(laws[0])
        api._ctx.cleanup_error = OSError("cleanup uncertain")
        result = await api.repeal_amendment(amendments[0], readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["cleanup"]["status"], "cleanup_unknown_retained")
        self.assertEqual(api._ctx.generated[0][2].owners[0].submissions, 1)
        with self.assertRaises(ChoicesBusyError):
            await amendments.close()
        with self.assertRaises(ChoicesBusyError):
            await laws.close()

    async def test_original_choice_adapter_survives_context_adapter_edit(self):
        api, laws = await self.laws()
        api._ctx.adapters["data"] = "different-data"
        result = await api.impose_law(laws[0])
        self.assertTrue(result["accepted"])
        self.assertEqual(api._ctx.generated[0][2].adapters["data"], "original-data")
        self.assertEqual(self.pages(api._ctx)[-1].adapter, "original-data")
        await laws.close()


class ReformOwner(Owner):
    def submit(self, flags=4):
        result = super().submit(flags=flags)
        if not result.error and result.accepted and not self.ctx.unknown_submission and self.ctx.apply_factory:
            self.ctx.apply_final_government()
        return result


class ReformRun(Run):
    def adopt(self, result, *, expected_world=None):
        if result.error or not result.command:
            raise AssertionError("only the original successful factory result is adopted")
        if expected_world != result.world:
            raise AssertionError("adopt the factory callback's actual world, not a cached-world receipt")
        owner = ReformOwner(self.ctx)
        owner.command, owner.type_token, owner.world = result.command, result.type_token, result.world
        self.owners.append(owner)
        self.ctx.adopted.append((result, expected_world, self, owner))
        return owner


def factory_request(client, action, **kwargs):
    if action != commands.Action.DESERIALIZE or kwargs.get("command", 0):
        raise AssertionError("one fresh original factory/materialization only; no serialize/re-deserialize")
    return NS(operation="factory", action=action, data=kwargs["data"], options=kwargs)


class ReformContext(Context):
    def __init__(self):
        super().__init__()
        self.factories, self.adopted = [], []
        self.factory_error = 0
        self.factory_partial_pointer = False
        self.factory_dispatch_error = None
        self.factory_world = 77
        self.apply_factory = True
        self.factory_wait = None
        self.after_wait = None
        self.after_page_hook = self.source_page_hook = None
        self.after_cleanup_warning = None
        self.after_copy_error = 0
    def apply_final_government(self):
        by_id = {}
        for values in (*self.sources.values(), *self.party_members.values()):
            for native in values:
                raw = self.fields_by_handle.get(native.value_handle, {}).get("InterestGroup.GetID")
                if type(raw) is int:
                    by_id[raw] = native
        ids = self.factories[-1].ids
        self.sources["Country.AccessInterestGroupsInGovernment"] = [by_id[value] for value in ids]
    async def query(self, label, factory, owner=None):
        q = factory()
        if getattr(q, "operation", None) == "factory":
            self.calls.append((label, q))
            if self.factory_dispatch_error:
                raise self.factory_dispatch_error
            if self.factory_wait:
                entered, released = self.factory_wait
                entered.set()
                await released.wait()
            tree = commands.decode_command(q.data)
            actor = run_mod.command_field(tree, 0x2840)
            vector = run_mod.command_field(tree, 0x30a6)
            if not isinstance(actor, commands.UInt32) or not isinstance(vector, tuple):
                raise AssertionError("original actor UInt32 and flat native IG block required")
            if not all(isinstance(value, commands.UInt32) for value in vector):
                raise AssertionError("native IG vector requires marker20 UInt32 elements")
            q.tree, q.actor, q.ids = tree, actor.value, [value.value for value in vector]
            self.factories.append(q)
            return NS(action=commands.Action.DESERIALIZE, error=self.factory_error,
                command=900 + len(self.factories) if not self.factory_error or self.factory_partial_pointer else 0,
                type_token=tree.key.value, world=self.factory_world)
        if getattr(q, "operation", None) == "page":
            if self.factories:
                if self.readback_error:
                    self.calls.append((label, q))
                    raise self.readback_error
                if self.after_wait:
                    entered, released = self.after_wait
                    entered.set()
                    await released.wait()
                q.cleanup_error = self.after_cleanup_warning
                result = await super().query(label, lambda: q, owner=owner)
                result = data.ProjectionPage(self.after_copy_error, result.world, result.fields_count,
                    result.start, result.execution_thread_id, result.source_count,
                    result.count_valid, result.row_count, result.cells)
                return self.after_page_hook(q, result) if self.after_page_hook else result
            if self.source_page_hook and q.source is not None:
                result = await super().query(label, lambda: q, owner=owner)
                return self.source_page_hook(q, result)
        return await super().query(label, lambda: q, owner=owner)


class GovernmentReformTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for target, name, replacement in ((run_mod, "Run", ReformRun), (data, "evaluate", evaluate),
                (data, "list_count", count), (data, "list_element", element), (data, "project_page", project),
                (commands, "request", factory_request)):
            patcher = patch.object(target, name, side_effect=replacement) if target is not politics else patch.object(target, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def inputs(self, *, context=None, source="interest_groups", country_id=0):
        api = API(context or ReformContext())
        catalog = await api.government_choices(country_id, source=source)
        return api, catalog

    async def test_government_choices_are_actual_igs_and_parties_without_party_ids_or_commands(self):
        for source, count_expected, named_type in (("interest_groups", 5, "InterestGroup"),
                ("current", 3, "InterestGroup"), ("parties", 2, "Party")):
            api, catalog = await self.inputs(source=source)
            self.assertEqual(len(catalog), count_expected)
            self.assertTrue(all(choice.native_type == named_type for choice in catalog))
            if named_type == "Party":
                self.assertTrue(all("id" not in choice for choice in catalog))
            else:
                self.assertEqual(catalog[0]["id"], 0)
            self.assertEqual(api._ctx.factories, [])
            self.assertEqual(api._ctx.generated, [])
            self.assertEqual(api._ctx.performs, ["politics.government_choices"])
            await catalog.close()

    async def test_delta_keeps_all_unchanged_members_and_fresh_native_selected_id(self):
        api, catalog = await self.inputs()
        self.assertEqual(catalog[2]["id"], 0x80000001)
        # The immutable source record is display data; schema IDs are read from
        # the retained native object in the actual selected operation.
        api._ctx.fields_by_handle[302]["InterestGroup.GetID"] = 0x80000002
        result = await api.reform_government(0, add=catalog[2], remove=catalog[1])
        self.assertEqual(result["selection"]["current_ids"], [0, 10, 20])
        self.assertEqual(result["selection"]["final_ids"], [0, 20, 0x80000002])
        self.assertEqual(api._ctx.factories[0].ids, [0, 20, 0x80000002])
        self.assertTrue(result["accepted"])
        self.assertTrue(result["effect_observed"])
        self.assertEqual(set(result["observed_interest_group_ids"]), {0, 20, 0x80000002})
        self.assertFalse(result["completed"])
        self.assertFalse(result["preview_performed"])
        self.assertFalse(result["selection"]["atomic_snapshot"])
        self.assertEqual(api._ctx.performs, ["politics.government_choices", "politics.reform_government"])
        await catalog.close()

    async def test_delta_bulk_roster_preserves129_members_with_bounded_copied_pages(self):
        ctx = ReformContext()
        groups = [native_object(500 + i, "InterestGroup") for i in range(130)]
        ctx.sources["Country.AccessAllInterestGroups"] = groups
        ctx.sources["Country.AccessInterestGroupsInGovernment"] = groups[:129]
        ctx.fields_by_handle.update({500 + i: {"InterestGroup.GetID": i} for i in range(130)})
        api, catalog = await self.inputs(context=ctx)
        before = len(ctx.calls)
        result = await api.reform_government(0, add=catalog[129])
        self.assertEqual(result["selection"]["current_ids"], list(range(129)))
        self.assertEqual(result["selection"]["final_ids"], list(range(130)))
        self.assertTrue(result["effect_observed"])
        pages = [q for _, q in ctx.calls[before:] if getattr(q, "operation", None) == "page"]
        self.assertTrue(all(q.limit == 64 and len(q.fields) <= 32 and len(q.bindings) <= 64 for q in pages))
        self.assertEqual(len(ctx.factories), 1)
        await catalog.close()

    async def test_native_wire_uses_flat_uint32_vector_root_actor_and_no_secondary_deserialize(self):
        api, catalog = await self.inputs()
        result = await api.reform_government(0x80000003, replace=(catalog[0], catalog[2]), readback=False)
        created = api._ctx.factories[0]
        expected = commands.encode_command(0x5a23, {
            0x2840: commands.UInt32(0x80000003),
            65: {0x30a6: (commands.UInt32(0), commands.UInt32(0x80000001))},
        })
        self.assertEqual(created.data, expected)
        self.assertEqual(created.tree.key, commands.Token(0x5a23))
        self.assertEqual(created.actor, 0x80000003)
        self.assertEqual(result["actor_country_id"], 0x80000003)
        self.assertEqual(api._ctx.country_reads, 0)
        self.assertEqual(len(api._ctx.factories), 1)
        self.assertEqual(api._ctx.generated, [])
        owner = api._ctx.adopted[0][3]
        self.assertEqual((owner.validations, owner.submissions), (1, 1))
        await catalog.close()

    async def test_explicit_replacement_does_not_load_or_merge_current_government(self):
        api, catalog = await self.inputs()
        before = len(api._ctx.calls)
        result = await api.reform_government(5, replace=(catalog[4], catalog[2], catalog[4]), readback=False)
        self.assertEqual(result["selection"]["mode"], "replacement")
        self.assertIsNone(result["selection"]["current_ids"])
        self.assertEqual(result["selection"]["final_ids"], [30, 0x80000001])
        self.assertFalse(any(getattr(q, "expression", None) == "Country.AccessInterestGroupsInGovernment"
            for _, q in api._ctx.calls[before:]))
        await catalog.close()

    async def test_empty_replacement_is_native_validated_not_python_policy_rejected(self):
        api = API(ReformContext())
        result = await api.reform_government(0, replace=())
        self.assertEqual(api._ctx.factories[0].ids, [])
        self.assertEqual(run_mod.command_field(api._ctx.factories[0].tree, 0x30a6), ())
        self.assertTrue(result["accepted"])
        self.assertTrue(result["effect_observed"])
        self.assertEqual(result["observed_interest_group_ids"], [])
        self.assertEqual(api._ctx.performs, ["politics.reform_government"])

    async def test_party_inputs_expand_actual_member_igs_and_do_not_serialize_party_identity(self):
        api, parties = await self.inputs(source="parties")
        result = await api.reform_government(0, add=parties[0], remove=parties[1], readback=False)
        # Before 0,10,20; remove party members10,30; add party membersHIGH,30.
        self.assertEqual(result["selection"]["final_ids"], [0, 20, 0x80000001, 30])
        self.assertEqual(api._ctx.factories[0].ids, [0, 20, 0x80000001, 30])
        member_calls = [q for _, q in api._ctx.calls if getattr(q, "source", None) == "Party.GetMembers"]
        self.assertEqual([q.bindings[0][0].object for q in member_calls], [401, 400])
        self.assertTrue(all(len(q.bindings[0]) == 1 for q in member_calls))
        self.assertNotIn(commands.NamedToken("party"), api._ctx.factories[0].tree.value)
        await parties.close()

    async def test_raw_party_members_copy_full_ids_inside_original_party_callback(self):
        api, parties = await self.inputs(source="parties")
        api._ctx.party_members[400] = [
            native_object(302, "InterestGroup", raw=True),
            native_object(304, "InterestGroup", raw=True),
        ]
        before = len(api._ctx.calls)
        result = await api.reform_government(0, replace=parties[0], readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(api._ctx.factories[0].ids, [0x80000001, 30])
        self.assertEqual(len(api._ctx.factories), 1)
        calls = api._ctx.calls[before:]
        pages = [q for _, q in calls if getattr(q, "source", None) == "Party.GetMembers"]
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].fields, ("InterestGroup.GetID",))
        self.assertEqual(pages[0].bindings, ((parties[0]._binding,),))
        self.assertFalse(any(getattr(q, "operation", None) in ("evaluate", "element") for _, q in calls))
        observation = result["selection"]["observations"][0]
        self.assertTrue(observation["required_ids_complete"])
        self.assertEqual(observation["source_count"], 2)
        self.assertEqual([row["id"] for row in observation["rows"]], [0x80000001, 30])
        run = api._ctx.adopted[0][2]
        self.assertTrue(any(label == "reform_government.ids" and native is observation["native_pages"][0]
            for label, native in run.operations))
        await parties.close()

    async def test_optional_descriptor_errors_do_not_block_complete_government_ids(self):
        ctx = ReformContext()
        ctx.row_fields = {
            "InterestGroup.GetNameNoFormatting": copied("partial name", error=122, total=100),
            "InterestGroup.GetClout": copied(data.FixedPoint(0), error=87),
        }
        api, catalog = await self.inputs(context=ctx)
        self.assertFalse(catalog.metadata["result"]["complete"])
        self.assertIn("name", catalog[2]["reasons"])
        result = await api.reform_government(0, add=catalog[2], readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(ctx.factories[0].ids, [0, 10, 20, 0x80000001])
        self.assertTrue(all(observation["required_ids_complete"]
            for observation in result["selection"]["observations"]))
        self.assertIn("native getter error 122", catalog[2]["reasons"]["name"])
        await catalog.close()

    async def test_delta_overlap_is_explicit_remove_then_add_with_original_order(self):
        api, catalog = await self.inputs()
        result = await api.reform_government(0, add=(catalog[1], catalog[1]), remove=catalog[1], readback=False)
        self.assertEqual(result["selection"]["final_ids"], [0, 10, 20])
        self.assertIn("add wins", result["selection"]["delta_order"])
        await catalog.close()

    async def test_raw_baseline_rows_supply_full_ids_in_original_callback_without_strong_catalog_gate(self):
        api, catalog = await self.inputs()
        api._ctx.sources["Country.AccessInterestGroupsInGovernment"][1] = native_object(301, "InterestGroup", raw=True)
        result = await api.reform_government(0, add=catalog[2], readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["selection"]["current_ids"], [0, 10, 20])
        self.assertEqual(api._ctx.factories[0].ids, [0, 10, 20, 0x80000001])
        observation = result["selection"]["observations"][0]
        self.assertTrue(observation["required_ids_complete"])
        self.assertEqual(observation["source_expression"], "Country.AccessInterestGroupsInGovernment")
        self.assertEqual([owner.object for owner in
            [q for _, q in api._ctx.calls if getattr(q, "source", None) == "Country.AccessInterestGroupsInGovernment"][0].bindings[0]], [0])
        await catalog.close()

    async def test_missing_baseline_id_blocks_only_whole_roster_materialization_and_preserves_partial_rows(self):
        api, catalog = await self.inputs()
        api._ctx.fields_by_handle[301]["InterestGroup.GetID"] = copied(10, error=87)
        result = await api.reform_government(0, add=catalog[2], readback=False)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["accepted"])
        self.assertEqual(api._ctx.factories, [])
        observation = result["selection"]["observations"][0]
        self.assertFalse(observation["required_ids_complete"])
        self.assertEqual(observation["rows"][1]["id"], 10)
        self.assertIn("id", observation["rows"][1]["reasons"])
        await catalog.close()

    async def test_complete_id_cells_survive_optional_global_page_diagnostic(self):
        api, catalog = await self.inputs()
        api._ctx.copy_error = 122
        result = await api.reform_government(0, add=catalog[2], readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(api._ctx.factories[0].ids, [0, 10, 20, 0x80000001])
        for observation in result["selection"]["observations"]:
            self.assertTrue(observation["required_ids_complete"])
            self.assertFalse(observation["complete"])
            self.assertEqual(observation["native_pages"][0].error, 122)
        await catalog.close()

    async def test_missing_or_invalid_selected_ig_id_keeps_observation_without_materializing(self):
        for value in (copied(0, error=87), 0xffffffff):
            api, catalog = await self.inputs()
            api._ctx.fields_by_handle[302]["InterestGroup.GetID"] = value
            result = await api.reform_government(0, replace=catalog[2], readback=False)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(api._ctx.factories, [])
            self.assertIsNone(result["selection"]["final_ids"])
            self.assertTrue(result["selection"]["observations"][0]["rows"][0]["reasons"])
            await catalog.close()

    async def test_same_native_actor_field_accepts_foreign_explicit_country_without_player_gate(self):
        api, catalog = await self.inputs(country_id=5)
        # Copied source ownership is deliberately different from requested actor.
        api._ctx.row_fields["InterestGroup.GetCountry.GetID"] = 123
        result = await api.reform_government(0x80000003, replace=catalog[4], readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(api._ctx.factories[0].actor, 0x80000003)
        self.assertEqual(api._ctx.factories[0].ids, [30])
        self.assertEqual(api._ctx.country_reads, 0)
        await catalog.close()

    async def test_default_actor_resolves_once_and_native_creation_world_not_cached_world_is_adopted(self):
        ctx = ReformContext()
        ctx.world = 0
        api = API(ctx)
        result = await api.reform_government(replace=(), readback=False)
        self.assertEqual(ctx.country_reads, 1)
        self.assertEqual(ctx.adopted[0][1], ctx.factory_world)
        self.assertIs(result["creation"], ctx.adopted[0][0])
        self.assertEqual(result["actor_country_id"], 0)

    async def test_native_rejection_preserves_creation_validation_and_does_not_submit(self):
        api, catalog = await self.inputs()
        api._ctx.validation.validated = 0
        result = await api.reform_government(0, add=catalog[2])
        self.assertFalse(result["accepted"])
        self.assertIs(result["validation"], api._ctx.validation)
        self.assertEqual(result["reason"], "native eligibility reason")
        self.assertEqual(api._ctx.adopted[0][3].submissions, 0)
        self.assertNotIn("after", result)
        await catalog.close()

    async def test_partial_factory_result_nonnull_command_is_retained_without_validate_or_submit(self):
        api, catalog = await self.inputs()
        api._ctx.factory_error = 87
        api._ctx.factory_partial_pointer = True
        result = await api.reform_government(0, add=catalog[2])
        self.assertEqual(result["creation"].error, 87)
        self.assertTrue(result["creation"].command)
        self.assertIsNone(result["validation"])
        self.assertEqual(api._ctx.adopted, [])
        self.assertFalse(result["ownership_settled"])
        run = api._ctx.runs[-1]
        self.assertIs(run.unadopted_command, result["creation"])
        self.assertIn(run, api._ctx.owner_pool.snapshot())
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_unknown_factory_dispatch_keeps_sources_and_does_not_replay(self):
        api, catalog = await self.inputs()
        api._ctx.factory_dispatch_error = OSError("factory uncertain")
        with self.assertRaises(OSError):
            await api.reform_government(0, add=catalog[2])
        run = api._ctx.runs[-1]
        self.assertTrue(run.retain_unknown)
        self.assertIn(run, api._ctx.owner_pool.snapshot())
        self.assertEqual(sum(getattr(q, "operation", None) == "factory" for _, q in api._ctx.calls), 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_unknown_accepted_submission_keeps_sources_and_skips_readback(self):
        api, catalog = await self.inputs()
        api._ctx.unknown_submission = True
        result = await api.reform_government(0, add=catalog[2])
        self.assertTrue(result["accepted"])
        self.assertIs(result["submission"], api._ctx.submission)
        self.assertFalse(result["ownership_settled"])
        self.assertIn("readback_skipped", result)
        self.assertEqual(api._ctx.adopted[0][3].submissions, 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_pending_factory_holds_original_choice_without_duplicate_creation(self):
        api, catalog = await self.inputs()
        entered, released = asyncio.Event(), asyncio.Event()
        api._ctx.factory_wait = (entered, released)
        task = asyncio.create_task(api.reform_government(0, add=catalog[2], readback=False))
        await entered.wait()
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertTrue(api._mutation_lock.locked())
        released.set()
        result = await task
        self.assertTrue(result["accepted"])
        self.assertEqual(len(api._ctx.factories), 1)
        self.assertFalse(api._mutation_lock.locked())
        await catalog.close()

    async def test_readback_mismatch_or_failure_never_erases_native_acceptance(self):
        for failure in (None, OSError("after-copy uncertain")):
            api, catalog = await self.inputs()
            api._ctx.apply_factory = False
            api._ctx.readback_error = failure
            result = await api.reform_government(0, add=catalog[2])
            self.assertTrue(result["accepted"])
            self.assertFalse(result["completed"])
            if failure:
                self.assertIs(result["readback_error"], failure)
                self.assertIsNone(result["effect_observed"])
            else:
                self.assertIs(result["effect_observed"], False)
            self.assertEqual(api._ctx.adopted[0][3].submissions, 1)
            await catalog.close()

    async def test_independent_readback_complete_ids_survive_optional_page_error_and_copy_cleanup_warning(self):
        api, catalog = await self.inputs()
        ctx = api._ctx
        ctx.after_copy_error = 122
        ctx.after_cleanup_warning = OSError("independent projection cleanup warning")
        result = await api.reform_government(0, add=catalog[2])
        self.assertTrue(result["accepted"])
        self.assertTrue(result["effect_observed"])
        self.assertEqual(result["observed_interest_group_ids"], [0, 10, 20, 0x80000001])
        self.assertFalse(result["after"]["complete"])
        self.assertTrue(result["after"]["required_ids_complete"])
        self.assertEqual(result["after"]["native_pages"][0].error, 122)
        self.assertIn("independent", result["observation_scope"])
        self.assertEqual(ctx.adopted[0][3].submissions, 1)
        query = ctx.remembered[-1]
        self.assertIs(query.cleanup_error, ctx.after_cleanup_warning)
        self.assertTrue(any(isinstance(item, dict) and item.get("query") is query
            and item.get("cleanup_error") is ctx.after_cleanup_warning for item in ctx.diagnostics))
        await catalog.close()

    async def test_independent_readback_missing_id_or_count_keeps_acceptance_and_all_copied_rows(self):
        for missing in ("id", "count"):
            api, catalog = await self.inputs()
            def partial_page(query, page):
                if missing == "count":
                    return replace(page, count_valid=False)
                return replace(page, cells=(replace(page.cells[0], error=87), *page.cells[1:]))
            api._ctx.after_page_hook = partial_page
            result = await api.reform_government(0, add=catalog[2])
            self.assertTrue(result["accepted"])
            self.assertIs(result["submission"], api._ctx.submission)
            self.assertFalse(result["after"]["required_ids_complete"])
            self.assertEqual(len(result["after"]["rows"]), 4)
            self.assertEqual(result["after"]["rows"][0]["id"], 0)
            self.assertIsNone(result["effect_observed"])
            self.assertNotIn("observed_interest_group_ids", result)
            self.assertEqual(api._ctx.adopted[0][3].submissions, 1)
            await catalog.close()

    async def test_pending_independent_readback_does_not_replay_or_keep_settled_action_run(self):
        api, catalog = await self.inputs()
        entered, released = asyncio.Event(), asyncio.Event()
        api._ctx.after_wait = (entered, released)
        task = asyncio.create_task(api.reform_government(0, add=catalog[2]))
        await entered.wait()
        run = api._ctx.adopted[0][2]
        self.assertEqual(run.status, "complete")
        self.assertNotIn(run, api._ctx.owner_pool.snapshot())
        self.assertEqual(run.public_result["accepted"], True)
        self.assertEqual(len(api._ctx.factories), 1)
        self.assertEqual(api._ctx.adopted[0][3].submissions, 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        released.set()
        result = await task
        self.assertTrue(result["accepted"])
        self.assertTrue(result["effect_observed"])
        self.assertEqual(len(api._ctx.factories), 1)
        await catalog.close()

    async def test_action_cleanup_warning_keeps_acceptance_sources_and_skips_independent_readback(self):
        api, catalog = await self.inputs()
        api._ctx.cleanup_error = OSError("original action cleanup uncertain")
        result = await api.reform_government(0, add=catalog[2])
        self.assertTrue(result["accepted"])
        self.assertIs(result["submission"], api._ctx.submission)
        self.assertEqual(result["cleanup"]["status"], "cleanup_unknown_retained")
        self.assertIn("readback_skipped", result)
        self.assertNotIn("after", result)
        self.assertEqual(api._ctx.adopted[0][3].submissions, 1)
        self.assertIn(api._ctx.adopted[0][2], api._ctx.owner_pool.snapshot())
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_baseline_count_drift_cannot_claim_whole_membership_from_last_page_only(self):
        ctx = ReformContext()
        groups = [native_object(500 + i, "InterestGroup") for i in range(129)]
        ctx.sources["Country.AccessAllInterestGroups"] = groups
        ctx.sources["Country.AccessInterestGroupsInGovernment"] = groups
        ctx.fields_by_handle.update({500 + i: {"InterestGroup.GetID": i} for i in range(129)})
        ctx.source_page_hook = lambda query, page: replace(page, source_count=130) if query.start == 0 else page
        api, catalog = await self.inputs(context=ctx)
        result = await api.reform_government(0, add=catalog[0], readback=False)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["accepted"])
        self.assertEqual(ctx.factories, [])
        observation = result["selection"]["observations"][0]
        self.assertEqual(len(observation["rows"]), 129)
        self.assertEqual([page.source_count for page in observation["native_pages"]], [130, 129, 129])
        self.assertFalse(observation["required_ids_complete"])
        await catalog.close()

    async def test_accepted_submission_beside_native_error_still_exposes_independent_membership(self):
        api, catalog = await self.inputs()
        api._ctx.submission.error = 87
        result = await api.reform_government(0, add=catalog[2])
        self.assertTrue(result["accepted"])
        self.assertIs(result["submission"], api._ctx.submission)
        self.assertEqual(result["submission"].error, 87)
        self.assertEqual(result["native_error"], 87)
        self.assertEqual(result["observed_interest_group_ids"], [0, 10, 20])
        self.assertFalse(result["effect_observed"])
        self.assertFalse(result["completed"])
        self.assertEqual(api._ctx.adopted[0][3].submissions, 1)
        await catalog.close()


    async def test_intent_validation_rejects_names_ids_and_ambiguous_whole_replacement_without_dispatch(self):
        api, catalog = await self.inputs()
        for options in ({"add": (0,)}, {"remove": "same name"}, {"replace": ({"id": 10},)},
                {"add": catalog[0], "replace": ()}, {"remove": catalog[0], "replace": ()},
                {"readback": 1}):
            with self.assertRaises((TypeError, ValueError)):
                await api.reform_government(0, **options)
        self.assertEqual(api._ctx.factories, [])
        self.assertEqual(api._ctx.performs, ["politics.government_choices"])
        await catalog.close()


if __name__ == "__main__":
    unittest.main()
