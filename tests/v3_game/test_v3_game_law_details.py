"""CPU-only law-details pages and one explicitly selected institution command.

No game process, DLL, GUI, absolute reference files or live connection is used.
The fixture seam copies the real ProjectionPage/Cell representation.
"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game import _political_data, politics
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.politics import law_details as law


KINDS = {}
for value in vars(law).values():
    if isinstance(value, tuple):
        for field in value:
            if isinstance(field, law.Field):
                KINDS[field.expression] = field.kind
for field in law._prefix(law._LAW_EFFECTS, law._LAW_ROOT):
    KINDS[field.expression] = field.kind
for prefix in ("Law", law._LAW_ROOT):
    for intent in ("enactment", "cancellation"):
        for field in law._affected_movement_fields(intent, prefix):
            KINDS[field.expression] = field.kind


def copied(value, *, error=0, total=None, kind=None, binding_error=0):
    return NS(value=value, error=error, total=total, kind=kind, binding_error=binding_error)


def cell(value, kind, row, field):
    spec = value if isinstance(value, NS) else copied(value)
    value = spec.value
    kind = spec.kind if spec.kind is not None else kind
    snapshot = value.encode() if kind == data.Kind.STRING else b""
    bits = value.bits if isinstance(value, data.FixedPoint) else (int(value) & 0xffffffffffffffff if kind != data.Kind.STRING else 0)
    return data.ProjectionCell(row, field, spec.error, kind, 7, bits, snapshot,
        len(snapshot) if spec.total is None else spec.total, binding_error=spec.binding_error)


def native_page(query, records=(), *, total=None, error=0, count_valid=True):
    cells = []
    for row_index, record in enumerate(records):
        for field_index, expression in enumerate(query.fields):
            kind = KINDS[expression]
            default = "native label" if kind == data.Kind.STRING else False if kind == data.Kind.BOOL else data.FixedPoint(0) if kind == data.Kind.FIXED else 0
            cells.append(cell(record.get(expression, default), kind, row_index, field_index))
    return data.ProjectionPage(error, 0x1000, len(query.fields), query.start, 7,
        len(records) if total is None else total, count_valid, len(records), tuple(cells))


class Context:
    def __init__(self, handler=None):
        self.handler = handler or (lambda query: native_page(query, [{}] if query.source is None else []))
        self._types = {"Country": 5}
        self.native = object()
        self.process = object()
        self.adapters = {"data": "data.dll"}
        self.calls = []
        self.performs = []
        self.diagnostics = []
        self.remembered = []
        self.country_reads = 0
        self.country = 0
    async def country_id(self):
        self.country_reads += 1
        return self.country
    async def type_id(self, name):
        raise AssertionError("No extra metadata/world call for cached Country")
    async def query(self, label, factory):
        query = factory()
        self.calls.append((label, query))
        result = self.handler(query)
        return await result if asyncio.iscoroutine(result) else result
    async def perform(self, label, read):
        self.performs.append(label)
        return await read()
    def _remember(self, query):
        self.remembered.append(query)


class API(law.LawDetailsMixin):
    def __init__(self, context):
        self._ctx = context
        self._mutation_lock = asyncio.Lock()


def projection(native, fields, bindings, **kwargs):
    return NS(fields=tuple(fields), bindings=tuple(bindings), source=kwargs["source_expression"],
        start=kwargs["start"], limit=kwargs["limit"], steps=kwargs["steps"],
        source_arguments=kwargs["source_arguments"], cleanup_error=None)


class LawDetailsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.project_patch = patch.object(data, "project_page", side_effect=projection)
        self.project = self.project_patch.start()
        self.addCleanup(self.project_patch.stop)
        # Reads have no fallback through old retained getter/command discovery.
        self.evaluate_patch = patch.object(data, "evaluate", side_effect=AssertionError("Read evaluated a retained command/object"))
        self.evaluate_patch.start()
        self.addCleanup(self.evaluate_patch.stop)

    def api(self, handler=None):
        return API(Context(handler))

    def assert_bounded(self, api):
        for _, query in api._ctx.calls:
            self.assertLessEqual(len(query.fields), 32)
            self.assertEqual(query.limit, 64)
            self.assertEqual(len(query.bindings), 1)
            self.assertEqual(len(query.bindings[0]), 1)
            country = query.bindings[0][0]
            self.assertEqual(country.domain, "Country")
            self.assertFalse(country.retained)
            self.assertFalse(any(token in expr for expr in query.fields for token in ("StopEnacting", "StartEnacting", "Validate", "GetIDString")))

    async def test_enactment_one_page_preserves_native_zero_false_chances_and_full_country(self):
        def answer(q):
            return native_page(q, [{"Country.IsEnactingLaw": True,
                "Country.GetLawBeingEnacted.GetNameNoFormatting": "#bold Public Schools#!",
                "Country.GetLawBeingEnacted.GetCheckpointSuccessChance": data.FixedPoint(12500),
                "Country.GetLawBeingEnacted.GetEnactmentPhase": 0,
                "Country.GetLawBeingEnacted.GetEnactmentSetbackCount": 0,
                "Country.GetLawBeingEnacted.HasEnactmentLastCheckpoint": False,
                "Country.GetLawBeingEnacted.GetCheckpointEndDateString": "1 January 1840"}])
        api = self.api(answer)
        result = await api.enactment_details(country_id=0x80000001)
        self.assertEqual(result["law_name"], "Public Schools")
        self.assertEqual(result["law_name_raw"], "#bold Public Schools#!")
        self.assertEqual(result["success_chance"], Decimal("0.125"))
        self.assertEqual((result["phase"], result["setbacks"]), (0, 0))
        self.assertIs(result["has_last_checkpoint"], False)
        self.assertEqual(result["checkpoint_date"], "1 January 1840")
        self.assertTrue(result["complete"])
        self.assertIn("Law", result["identity_limitations"])
        self.assertNotIn("law_id", result)
        self.assertEqual(api._ctx.country_reads, 0)
        self.assertEqual(api._ctx.calls[0][1].bindings[0][0].object, 0x80000001)
        self.assertEqual(len(api._ctx.calls[0][1].fields), 31)
        self.assertEqual(api._ctx.performs, ["politics.enactment_details"])
        self.assert_bounded(api)

    async def test_law_effects_current_enact_and_cancel_movements_same_callback_parent(self):
        def answer(q):
            if q.source is None:
                return native_page(q, [{law._LAW_ROOT + ".IsBeingEnacted": True,
                    law._LAW_ROOT + ".GetNameNoFormatting": "earlier law"}])
            return native_page(q, [{law._LAW_ROOT + ".GetNameNoFormatting": "actual law now",
                law._LAW_ROOT + ".GetCountry.GetID": -2147483647,
                "PoliticalMovement.GetNameNoFormatting": "Native movement"}])
        api = self.api(answer)
        result = await api.law_effects()
        self.assertEqual(len(api._ctx.calls), 3)
        self.assertEqual(result["laws"][0]["law_name"], "earlier law")
        affected = result["affected_movements"]
        self.assertEqual([p["intent"] for p in affected], ["enactment", "cancellation"])
        for page in affected:
            self.assertEqual(page["rows"][0]["law_name"], "actual law now")
            self.assertEqual(page["rows"][0]["law_country_id"], 0x80000001)
            self.assertNotIn("law_id", page["rows"][0])
        self.assertIn("Country.GetLawBeingEnacted.GetRadicalismEffectOnMovementFromCancelEnact(PoliticalMovement.Self)", api._ctx.calls[-1][1].fields)
        self.assertFalse(result["eligibility_checked"])
        self.assertEqual(api._ctx.country_reads, 1)
        self.assertEqual(api._ctx.performs, ["politics.law_effects"])
        self.assert_bounded(api)

    async def test_law_candidates_original_sources_and_creation_steps_not_id_join(self):
        def answer(q):
            if q.source == law._CANDIDATE_LAW_SOURCES[0]:
                return native_page(q, [{"Law.GetNameNoFormatting": "earlier candidate"}])
            if q.steps:
                return native_page(q, [{"Law.GetNameNoFormatting": "current provider candidate"}])
            return native_page(q)
        api = self.api(answer)
        result = await api.law_effects(scope="candidates")
        self.assertEqual([entry["source_expression"] for entry in result["laws"]], list(law._CANDIDATE_LAW_SOURCES))
        self.assertEqual(len(result["affected_movements"]), 2)
        nested_calls = [q for _, q in api._ctx.calls if q.steps]
        self.assertEqual(len(nested_calls), 2)
        for q in nested_calls:
            self.assertEqual(q.steps, (data.CreationStep(law._CANDIDATE_LAW_SOURCES[0], 0, 1),))
        self.assertEqual(result["affected_movements"][0]["rows"][0]["law_name"], "current provider candidate")
        self.assertNotIn("affected_movements", result["laws"][0]["rows"][0])
        self.assert_bounded(api)

    async def test_only_error_free_false_skips_current_affected_movements(self):
        for error, expected_calls in ((0, 1), (87, 3)):
            def answer(q):
                return native_page(q, [{law._LAW_ROOT + ".IsBeingEnacted": copied(False, error=error)}] if q.source is None else [])
            api = self.api(answer)
            result = await api.law_effects()
            self.assertEqual(len(api._ctx.calls), expected_calls)
            self.assertIs(result["laws"][0]["being_enacted"], False)
            self.assertEqual(result["complete"], not bool(error))

    async def test_amendments_all_sources_preserve_proposed_sponsor_timers_and_type(self):
        def answer(q):
            if q.source.endswith("GetAllAmendments"):
                return native_page(q, [{"Amendment.GetAttachedLaw.GetNameNoFormatting": "actual attached law",
                    "Amendment.GetNameNoFormatting": "#bold Amendment A#!",
                    "Amendment.GetSponsor.GetID": 0x80000002,
                    "Amendment.IsActive": False,
                    "Amendment.HasTimeout": True,
                    "Amendment.GetTimeoutDate": "31 December 1840",
                    "Amendment.GetNumEndorsingInterestGroups": 0}])
            return native_page(q, [{}])
        api = self.api(answer)
        result = await api.amendments()
        self.assertEqual(len(result["laws"]), 6)
        self.assertEqual(len(result["amendments"]), 7)
        self.assertEqual(len(api._ctx.calls), 13)
        row = result["amendments"][1]["rows"][0]
        self.assertEqual(row["law_name"], "actual attached law")
        self.assertEqual(row["name"], "Amendment A")
        self.assertEqual(row["sponsor_id"], 0x80000002)
        self.assertIs(row["active"], False)
        self.assertEqual(row["endorsing_interest_groups_count"], 0)
        self.assertEqual(row["timeout_date"], "31 December 1840")
        self.assertNotIn("id", row)
        self.assertNotIn("law_id", row)
        self.assertIn("Amendment", result["identity_limitations"])
        self.assertEqual(api._ctx.performs, ["politics.amendments"])
        self.assert_bounded(api)

    async def test_active_only_uses_original_active_provider_and_empty_is_observed(self):
        api = self.api()
        result = await api.amendments(scope="enacting", active_only=True)
        self.assertEqual(api._ctx.calls[0][1].source, "Country.GetLawBeingEnacted.GetActiveAmendments")
        self.assertEqual(result["amendments"][0]["rows"], [])
        self.assertTrue(result["complete"])
        self.assertEqual(len(api._ctx.calls), 1)

    async def test_changed_parent_count_native_error_is_not_silently_empty_or_retried(self):
        def answer(q):
            if q.steps:
                return native_page(q, error=1237, count_valid=False)
            return native_page(q, [{}])
        api = self.api(answer)
        result = await api.amendments(scope="candidates")
        self.assertEqual(len(api._ctx.calls), 4)
        self.assertFalse(result["complete"])
        self.assertEqual(result["amendments"][0]["native_pages"][0].error, 1237)
        self.assertIsNone(result["amendments"][0]["source_count"])
        self.assertEqual(len(result["native_pages"]), 4)
        self.assertIn("amendments[0]", result["reasons"])

    async def test_institution_levels_129rows_four_queries_actual_parent_and_conditions(self):
        def answer(q):
            if not q.steps:
                return native_page(q, [{"Institution.GetNameNoFormatting": "old institution"}])
            rows = [{"Institution.GetNameNoFormatting": "new native parent",
                "InstitutionInvestmentLevel.GetLevel": i} for i in range(q.start, min(q.start + 64, 129))]
            return native_page(q, rows, total=129)
        api = self.api(answer)
        result = await api.institution_effects()
        self.assertEqual(len(api._ctx.calls), 4)
        self.assertEqual([q.start for _, q in api._ctx.calls[1:]], [0, 64, 128])
        level_rows = result["levels"][0]["rows"]
        self.assertEqual(len(level_rows), 129)
        self.assertEqual(level_rows[-1]["level"], 128)
        self.assertEqual(level_rows[0]["institution_name"], "new native parent")
        self.assertIs(level_rows[0]["can_change_to_level"], False)
        self.assertEqual(result["institutions"]["rows"][0]["investment_cost"], Decimal(0))
        self.assertIn("Institution.GetChangeLevelDesc(InstitutionInvestmentLevel.GetLevel)", api._ctx.calls[1][1].fields)
        self.assertEqual(api._ctx.performs, ["politics.institution_effects"])
        self.assert_bounded(api)

    async def test_movement_pressure_and_all_states_use_same_callback_parent_without_filter(self):
        def answer(q):
            if not q.steps:
                return native_page(q, [{"PoliticalMovement.GetTargetSupport": data.FixedPoint(23000),
                    "PoliticalMovement.GetTargetRadicalism": data.FixedPoint(150000),
                    "PoliticalMovement.GetNumOfObstinateStates": 0}])
            return native_page(q, [{"PoliticalMovement.GetNameNoFormatting": "current movement",
                "State.GetID": -2147483646,
                "State.GetObstinanceFromMovement(PoliticalMovement.Self)": data.FixedPoint(0)}])
        api = self.api(answer)
        result = await api.movement_pressure()
        movement = result["movements"]["rows"][0]
        self.assertEqual(movement["target_support"], Decimal("0.23"))
        self.assertEqual(movement["target_activism"], Decimal("1.5"))
        self.assertEqual(movement["obstinate_states_count"], 0)
        state = result["state_pressure"][0]["rows"][0]
        self.assertEqual(state["state_id"], 0x80000002)
        self.assertEqual(state["obstinance_from_movement"], Decimal(0))
        self.assertEqual(state["movement_name"], "current movement")
        self.assertEqual(api._ctx.calls[1][1].source, "PoliticalMovement.GetCountry.AccessStates")
        self.assertEqual(api._ctx.performs, ["politics.movement_pressure"])
        self.assert_bounded(api)

    async def test_129movements_three_pages_not_per_row_field_calls(self):
        api = self.api(lambda q: native_page(q, [{} for _ in range(q.start, min(q.start + 64, 129))], total=129))
        result = await api.movement_pressure(states=False)
        self.assertEqual(len(api._ctx.calls), 3)
        self.assertEqual(len(result["movements"]["rows"]), 129)
        self.assertEqual([q.start for _, q in api._ctx.calls], [0, 64, 128])
        self.assertEqual(result["state_pressure"], [])
        self.assert_bounded(api)

    async def test_civil_wars_and_secessions_keep_overlapping_native_sources_and_igs(self):
        def answer(q):
            if q.steps:
                return native_page(q, [{"CivilWar.GetName": "actual current civil war", "InterestGroup.GetID": 0x80000001}])
            return native_page(q, [{"CivilWar.GetName": "overlapping name", "CivilWar.IsRevolution": False,
                "CivilWar.GetNumUprisingBattalions": 0, "CivilWar.GetClockCheckpointRemainingWeeks": 0}])
        api = self.api(answer)
        result = await api.civil_wars(country_id=0)
        self.assertEqual(len(api._ctx.calls), 4)
        self.assertEqual(result["civil_wars"]["rows"][0]["civil_war_name"], "overlapping name")
        self.assertEqual(result["secessions"]["rows"][0]["civil_war_name"], "overlapping name")
        self.assertIs(result["civil_wars"]["rows"][0]["revolution"], False)
        self.assertEqual(result["civil_wars"]["rows"][0]["uprising_battalions"], 0)
        for part in result["insurrectionary_interest_groups"]:
            self.assertEqual(part["rows"][0]["interest_group_id"], 0x80000001)
            self.assertEqual(part["rows"][0]["civil_war_name"], "actual current civil war")
        for _, q in api._ctx.calls:
            if q.steps:
                self.assertEqual(q.source, "CivilWar.AccessInsurrectionaryIGs(Country.Self)")
                self.assertEqual(q.source_arguments, ("Country.Self",))
        self.assertEqual(api._ctx.performs, ["politics.civil_wars"])
        self.assert_bounded(api)

    async def test_copied_error_zero_false_and_partial_text_are_preserved(self):
        def answer(q):
            return native_page(q, [{"PoliticalMovement.GetNameNoFormatting": copied("#bold kept#!", error=122, total=60),
                "PoliticalMovement.IsCausingObstinance": copied(False, error=87),
                "PoliticalMovement.GetNumOfObstinateStates": copied(0, binding_error=5)}], error=122)
        api = self.api(answer)
        result = await api.movement_pressure(states=False)
        row = result["movements"]["rows"][0]
        self.assertEqual(row["name"], "kept")
        self.assertEqual(row["name_raw"], "#bold kept#!")
        self.assertIs(row["causing_obstinance"], False)
        self.assertEqual(row["obstinate_states_count"], 0)
        self.assertIn("incomplete", row["reasons"]["name"])
        self.assertIn("binding error", row["reasons"]["obstinate_states_count"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["native_pages"][0].error, 122)

    async def test_unavailable_source_differs_from_observed_empty(self):
        for error, valid in ((0, True), (87, False)):
            api = self.api(lambda q: native_page(q, error=error, count_valid=valid))
            result = await api.civil_wars(interest_groups=False)
            self.assertEqual(result["complete"], valid)
            self.assertEqual(result["civil_wars"]["rows"], [])
            self.assertEqual(result["civil_wars"]["status"], "observed" if valid else "unavailable")
            self.assertEqual(len(api._ctx.calls), 2)

    async def test_cleanup_warning_keeps_primary_fields_and_query_owner(self):
        warning = OSError("cleanup failed")
        def factory(*args, **kwargs):
            q = projection(*args, **kwargs)
            q.cleanup_error = warning
            return q
        api = self.api()
        with patch.object(data, "project_page", side_effect=factory):
            result = await api.enactment_details()
        self.assertTrue(result["complete"])
        self.assertEqual(result["phase"], 0)
        self.assertEqual(len(api._ctx.remembered), 1)
        self.assertIs(api._ctx.diagnostics[0]["cleanup_error"], warning)

    async def test_waiting_leaf_retains_original_work_without_second_perform_or_page(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def answer(q):
            if q.steps:
                entered.set()
                await release.wait()
            return native_page(q, [{}])
        api = self.api(answer)
        task = asyncio.create_task(api.institution_effects())
        await entered.wait()
        self.assertEqual(len(api._ctx.calls), 2)
        self.assertEqual(api._ctx.performs, ["politics.institution_effects"])
        release.set()
        result = await task
        self.assertTrue(result["complete"])
        self.assertEqual(len(api._ctx.calls), 2)

    async def test_controls_and_scope_validation_do_not_enter_native_work(self):
        for method, kwargs in (("law_effects", {"scope": "invented"}),
                ("amendments", {"scope": None}), ("amendments", {"active_only": 1}),
                ("law_effects", {"movements": 0}), ("institution_effects", {"levels": "yes"}),
                ("movement_pressure", {"states": 0}), ("civil_wars", {"interest_groups": None})):
            api = self.api()
            with self.assertRaises(ValueError):
                await getattr(api, method)(**kwargs)
            self.assertEqual(api._ctx.calls, [])
            self.assertEqual(api._ctx.performs, [])

    async def test_every_public_read_enters_perform_once_with_bounded_native_pages(self):
        for method in ("enactment_details", "law_effects", "amendments", "institution_effects", "movement_pressure", "civil_wars"):
            api = self.api()
            result = await getattr(api, method)()
            self.assertEqual(api._ctx.performs, ["politics." + method])
            self.assertEqual(api._ctx.country_reads, 1)
            self.assertTrue(result["native_pages"])
            self.assert_bounded(api)


class ActionOwner:
    def __init__(self, ctx):
        self.ctx, self.pending, self.attempt, self.command = ctx, None, None, 17
        self.validations = self.submissions = 0
    def validate(self, capacity=0):
        self.validations += 1
        self.attempt = NS(outcome="unknown" if self.ctx.unknown_validation else "owned")
        return self.ctx.validation
    def submit(self, flags=4):
        self.submissions += 1
        if self.ctx.dispatch_error:
            raise self.ctx.dispatch_error
        self.attempt = NS(outcome="unknown" if self.ctx.unknown_submission else "consumed")
        self.command = 0
        return self.ctx.submission


class ActionRun:
    made = []
    owner_unsettled = staticmethod(run_mod.Run.owner_unsettled)
    command_result = run_mod.Run.command_result
    validate_submit = run_mod.Run.validate_submit
    finish = run_mod.Run.finish
    def __init__(self, ctx, label, namespace="politics"):
        self.ctx, self.namespace, self.actor = ctx, namespace, None
        self.owners, self.operations, self.generated, self.origins = [], [], [], []
        self.cleaned = 0
        self.status = "working"
        self.retained_scope = run_mod.scope_owner_pool(ctx)
        self.retained_scope.retain(self)
        self.made.append(self)
    async def country(self):
        self.actor = 0
        return data.ObjectBinding.country(5, self.actor)
    async def elements(self, expression, bindings, named_type):
        self.source, self.root, self.named_type = expression, bindings, named_type
        for index, full_id in enumerate(self.ctx.ids):
            yield index, NS(full_id=full_id), data.ObjectBinding(6, index + 100, retained=True)
    async def origin(self, element, named_type):
        self.origins.append((element.full_id, named_type))
        return element.full_id
    async def generated_owner(self, expression, bindings):
        self.generated.append((expression, bindings))
        owner = ActionOwner(self.ctx)
        self.owners.append(owner)
        return owner
    async def cleanup(self):
        self.cleaned += 1
        if self.ctx.cleanup_error:
            raise self.ctx.cleanup_error
        self.status = "complete"
        self.retained_scope.discard(self)


class ActionContext:
    def __init__(self):
        self.ids = [0, 0x80000001]
        self.validation = NS(error=0, validated=1, reason="native reason")
        self.submission = NS(error=0, accepted=1)
        self.performs, self.calls = [], []
        self.unknown_validation = self.unknown_submission = False
        self.dispatch_error = self.cleanup_error = None
    async def query(self, label, factory, owner=None):
        self.calls.append(label)
        return factory()
    async def perform(self, label, callback):
        self.performs.append(label)
        return await callback()


class StopInstitutionTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, identity=0x80000001, context=None):
        ctx = context or ActionContext()
        api = API(ctx)
        with patch.object(run_mod, "Run", ActionRun):
            result = await api.stop_institution(identity)
        return api, result, ActionRun.made[-1]

    async def test_full_id_selection_retained_original_parent_one_validate_one_submit(self):
        api, result, run = await self.invoke()
        self.assertEqual(run.origins, [(0, "Institution"), (0x80000001, "Institution")])
        self.assertEqual(run.source, "Country.AccessInstitutions")
        self.assertEqual(run.generated[0][0], "Institution.StopSizeChange")
        self.assertEqual(run.generated[0][1][1].object, 101)
        self.assertTrue(run.generated[0][1][1].retained)
        self.assertEqual(len(run.generated), 1)
        self.assertEqual((run.owners[0].validations, run.owners[0].submissions), (1, 1))
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertIs(result["validation"], api._ctx.validation)
        self.assertIs(result["submission"], api._ctx.submission)
        self.assertEqual(api._ctx.performs, ["politics.stop_institution"])
        self.assertEqual(run.cleaned, 1)
        self.assertEqual([owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, ActionRun)], [])

    async def test_full_zero_id_is_selected_not_missing_or_default_candidate(self):
        _, result, run = await self.invoke(0)
        self.assertTrue(result["accepted"])
        self.assertEqual(run.origins, [(0, "Institution")])
        self.assertEqual(run.generated[0][1][1].object, 100)

    async def test_missing_full_id_does_not_create_any_candidate_command(self):
        _, result, run = await self.invoke(24)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["accepted"])
        self.assertEqual(run.generated, [])
        self.assertEqual(run.cleaned, 1)

    async def test_native_rejection_preserves_reason_without_submit(self):
        ctx = ActionContext()
        ctx.validation.validated = 0
        _, result, run = await self.invoke(context=ctx)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "native reason")
        self.assertEqual((run.owners[0].validations, run.owners[0].submissions), (1, 0))

    async def test_partial_validation_and_cleanup_warning_do_not_hide_acceptance(self):
        ctx = ActionContext()
        ctx.validation.error = 122
        ctx.cleanup_error = OSError("cleanup")
        api, result, run = await self.invoke(context=ctx)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["cleanup"]["status"], "cleanup_unknown_retained")
        self.assertEqual(result["cleanup"]["reason"], "cleanup")
        self.assertEqual(run.owners[0].validations, 1)
        self.assertEqual([owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, ActionRun)], [run])

    async def test_unknown_validation_retains_graph_and_never_submits(self):
        ctx = ActionContext()
        ctx.unknown_validation = True
        api, result, run = await self.invoke(context=ctx)
        self.assertIs(result["validation"], ctx.validation)
        self.assertFalse(result["ownership_settled"])
        self.assertEqual(run.cleaned, 0)
        self.assertEqual(run.owners[0].submissions, 0)
        self.assertEqual([owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, ActionRun)], [run])

    async def test_unknown_submit_acceptance_remains_visible_and_no_cleanup(self):
        ctx = ActionContext()
        ctx.unknown_submission = True
        api, result, run = await self.invoke(context=ctx)
        self.assertTrue(result["accepted"])
        self.assertIs(result["submission"], ctx.submission)
        self.assertEqual(run.owners[0].submissions, 1)
        self.assertEqual(run.cleaned, 0)
        self.assertEqual([owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, ActionRun)], [run])

    async def test_unknown_dispatch_retains_source_graph_without_replay(self):
        ctx = ActionContext()
        ctx.dispatch_error = OSError("uncertain dispatch")
        api = API(ctx)
        with patch.object(run_mod, "Run", ActionRun), self.assertRaises(OSError):
            await api.stop_institution(0)
        run = ActionRun.made[-1]
        self.assertEqual(run.owners[0].submissions, 1)
        self.assertEqual(run.cleaned, 0)
        self.assertEqual([owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, ActionRun)], [run])
        self.assertFalse(api._mutation_lock.locked())

    async def test_action_rejects_names_occurrences_bools_and_invalid_sentinel(self):
        for value in ("Schools", True, -1, 0xffffffff, {"source_index": 0}, None):
            api = API(ActionContext())
            with self.assertRaises(ValueError):
                await api.stop_institution(value)
            self.assertEqual(api._ctx.performs, [])
            self.assertEqual(api._ctx.calls, [])


if __name__ == "__main__":
    unittest.main()
