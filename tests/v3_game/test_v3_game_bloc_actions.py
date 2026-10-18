"""CPU fixtures for original power-bloc choices and selected command ownership."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.diplomacy import bloc_actions as bloc
from pdx_runtime.v3_game._choices import OwnedChoice, OwnedChoices, ChoicesBusyError


KINDS = {f.expression: f.kind for fields in (bloc._BLOCS, bloc._PRINCIPLES, bloc._PRINCIPLE_AFTER,
    bloc._membership_fields("Country"), bloc._membership_fields("GetPlayer")) for f in fields}
KINDS.update({"Country.IsInPowerBloc": data.Kind.BOOL, "Country.IsPowerBlocLeader": data.Kind.BOOL})


def native_object(handle, named_type="PowerBloc", *, raw=False, error=0):
    spelling = (".?AU?$SAnyType@PEAVCData" + named_type + "@@@PdxAnyImpl@@" if raw else
                ".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@")
    native_type_id = 6 if named_type == "PowerBloc" else 7
    return NS(action=data.Action.LIST_ELEMENT, value_handle=handle, error=error,
        native_type_name=spelling, type_id=native_type_id,
        value=data.NativeObject(0x1000 + handle, native_type_id, handle, spelling))


def copied(value, *, error=0, total=None, kind=None):
    return NS(value=value, error=error, total=total, kind=kind)


def native_page(query, rows, *, error=0, count_valid=True):
    cells = []
    for index, row in enumerate(rows):
        for field, expression in enumerate(query.fields):
            kind = KINDS[expression]
            default = "same display name" if kind == data.Kind.STRING else False if kind == data.Kind.BOOL else data.FixedPoint(0) if kind == data.Kind.FIXED else 0
            value = row.get(expression, default)
            spec = value if isinstance(value, NS) else copied(value)
            value = spec.value
            actual_kind = kind if spec.kind is None else spec.kind
            snapshot = value.encode() if actual_kind == data.Kind.STRING else b""
            bits = value.bits if isinstance(value, data.FixedPoint) else (int(value) & 0xffffffffffffffff if actual_kind != data.Kind.STRING else 0)
            cells.append(data.ProjectionCell(index, field, spec.error, actual_kind, 7, bits, snapshot,
                len(snapshot) if spec.total is None else spec.total))
    return data.ProjectionPage(error, 17, len(query.fields), 0, 3, len(rows), count_valid, len(rows), tuple(cells))


class Owner:
    def __init__(self, ctx):
        self.ctx, self.pending, self.attempt, self.command = ctx, None, None, 55
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


class Run:
    blocking = run_mod.Run.blocking
    diagnostics = run_mod.Run.diagnostics
    _remember = run_mod.Run._remember
    query = run_mod.Run.query
    _capture_values = run_mod.Run._capture_values
    capture = run_mod.Run.capture
    call = run_mod.Run.call
    evaluate = run_mod.Run.evaluate
    country = run_mod.Run.country
    wrapper = run_mod.Run.wrapper
    owner_unsettled = staticmethod(run_mod.Run.owner_unsettled)
    command_result = run_mod.Run.command_result
    validate_submit = run_mod.Run.validate_submit
    finish = run_mod.Run.finish
    def __init__(self, ctx, label, namespace="politics"):
        self.ctx, self.label, self.namespace = ctx, label, namespace
        self.native, self.process, self.adapters = ctx.native, ctx.process, dict(ctx.adapters)
        self.actor = None
        self.values, self.value_adapters, self.owners, self.operations = [], {}, [], []
        self.status, self.cleaned = "working", 0
        self.retained_scope = run_mod.scope_owner_pool(ctx)
        self.retained_scope.retain(self)
        ctx.runs.append(self)
    async def generated_owner(self, expression, bindings):
        self.ctx.generated.append((expression, tuple(bindings), self))
        owner = Owner(self.ctx)
        self.owners.append(owner)
        return owner
    async def cleanup(self):
        self.cleaned += 1
        if self.ctx.cleanup_error:
            raise self.ctx.cleanup_error
        self.values.clear()
        self.value_adapters.clear()
        self.status = "complete"
        self.retained_scope.discard(self)


class Context:
    def __init__(self, count=2):
        self.native, self.process, self.world = object(), object(), 17
        self.adapters = {"data": "original-data", "commands": "commands"}
        self._types = {"Country": 5, "PowerBloc": 6, "PowerBlocPrinciple": 7}
        self.objects = [native_object(i + 10) for i in range(count)]
        self.principles = [native_object(100, "PowerBlocPrinciple"), native_object(101, "PowerBlocPrinciple")]
        self.calls, self.performs, self.runs, self.generated = [], [], [], []
        self.diagnostics, self.remembered = [], []
        self.country_reads = 0
        self.validation = NS(error=0, validated=1, reason="native condition")
        self.submission = NS(error=0, accepted=1)
        self.unknown_validation = self.unknown_submission = False
        self.dispatch_error = self.cleanup_error = self.readback_error = None
        self.fail_element = None
        self.element_errors = {}
        self.source_error, self.count_error = 0, 0
        self.copy_error = 0
        self.row_fields = {}
        self.copy_hook = None
        self.hold_submit = None
    async def country_id(self):
        self.country_reads += 1
        return 0
    async def type_id(self, name):
        return self._types[name]
    async def query(self, label, factory, owner=None):
        q = factory()
        self.calls.append((label, q))
        if label.endswith("command.submit") and self.hold_submit:
            entered, released = self.hold_submit
            entered.set()
            await released.wait()
        if not hasattr(q, "operation"):
            return q
        if q.operation == "evaluate":
            if q.expression == "Country.GetPowerBloc":
                answer = self.objects[0]
                return NS(**{**vars(answer), "action": data.Action.EVALUATE, "error": self.source_error})
            self.assert_source(q.expression)
            handle = 2000 if q.expression == "PowerBloc.GetPrinciples" else 1000
            return NS(action=data.Action.EVALUATE, error=self.source_error, value_handle=handle,
                value=data.NativeList(handle, 0x2000, b"", "original native list"))
        if q.operation == "count":
            rows = self.principles if q.handle == 2000 else self.objects
            return NS(action=data.Action.LIST_COUNT, error=self.count_error, total=len(rows), value_handle=0)
        if q.operation == "element":
            if q.index == self.fail_element:
                raise OSError("original source read uncertain")
            rows = self.principles if q.handle == 2000 else self.objects
            result = rows[q.index]
            if q.index in self.element_errors:
                result = NS(**{**vars(result), "error": self.element_errors[q.index]})
            return result
        if q.operation == "page":
            if self.generated and self.readback_error:
                raise self.readback_error
            if self.copy_hook:
                result = self.copy_hook(q)
                return await result if asyncio.iscoroutine(result) else result
            return native_page(q, [self.row_fields for _ in q.bindings], error=self.copy_error)
        raise AssertionError(q.operation)
    def assert_source(self, expression):
        if expression not in ("GetAllPowerBlocs", "Country.GetPowerBlocsWithLeverage", "PowerBloc.GetPrinciples"):
            raise AssertionError("No invented source or candidate command: " + expression)
    async def perform(self, label, work):
        self.performs.append(label)
        return await work()
    def _remember(self, query):
        self.remembered.append(query)


class API(bloc.BlocActionsMixin):
    def __init__(self, context):
        self._ctx = context


def evaluate(native, expression, bindings=(), **kwargs):
    return NS(operation="evaluate", expression=expression, bindings=tuple(bindings), adapter=kwargs["adapter"])


def count(native, handle, **kwargs):
    return NS(operation="count", handle=handle, adapter=kwargs["adapter"])


def element(native, handle, index, **kwargs):
    return NS(operation="element", handle=handle, index=index, adapter=kwargs["adapter"])


def project(native, fields, binding_rows=((),), **kwargs):
    return NS(operation="page", fields=tuple(fields), bindings=tuple(binding_rows),
        adapter=kwargs["adapter"], limit=kwargs["limit"], cleanup_error=None)


class BlocActionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for target, name, replacement in ((run_mod, "Run", Run), (data, "evaluate", evaluate),
                (data, "list_count", count), (data, "list_element", element), (data, "project_page", project)):
            patcher = patch.object(target, name, side_effect=replacement) if target is data else patch.object(target, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def catalog(self, count=2, source="all"):
        api = API(Context(count))
        return api, await api.bloc_choices(source)

    def pages(self, ctx):
        return [q for _, q in ctx.calls if getattr(q, "operation", None) == "page"]

    async def test_all_catalog_129choices_three_bulk_pages_no_candidate_commands(self):
        api, catalog = await self.catalog(129)
        self.assertIsInstance(catalog, OwnedChoices)
        self.assertEqual(len(catalog), 129)
        self.assertEqual([len(q.bindings) for q in self.pages(api._ctx)], [64, 64, 1])
        self.assertTrue(all(q.limit == 64 and len(q.fields) <= 32 for q in self.pages(api._ctx)))
        self.assertTrue(all(len(row) == 1 and row[0].retained for q in self.pages(api._ctx) for row in q.bindings))
        self.assertEqual(api._ctx.generated, [])
        self.assertEqual(api._ctx.country_reads, 0)
        self.assertEqual(api._ctx.performs, ["diplomacy.bloc_choices"])
        self.assertEqual(catalog[128]._binding.object, 138)
        self.assertIn(1000, catalog.owner.values)
        self.assertIn(catalog.owner, [owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, Run)])
        await catalog.close()
        self.assertEqual([owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, Run)], [])

    async def test_leverage_and_current_sources_use_one_full_actor_country_root(self):
        for source, expected in (("leverage", "Country.GetPowerBlocsWithLeverage"), ("current", "Country.GetPowerBloc")):
            api, catalog = await self.catalog(source=source)
            produced = [q for _, q in api._ctx.calls if getattr(q, "operation", None) == "evaluate"]
            self.assertEqual(produced[0].expression, expected)
            self.assertEqual(len(produced[0].bindings), 1)
            self.assertTrue(produced[0].bindings[0].country_id)
            self.assertEqual(produced[0].bindings[0].object, 0)
            self.assertEqual(api._ctx.country_reads, 1)
            self.assertEqual(len(catalog), 1 if source == "current" else 2)
            await catalog.close()

    async def test_raw_native_rows_are_reported_not_retagged_or_made_actionable(self):
        ctx = Context()
        ctx.objects[0] = native_object(10, raw=True)
        api = API(ctx)
        catalog = await api.bloc_choices()
        self.assertEqual(len(catalog), 1)
        report = catalog.metadata["result"]
        self.assertFalse(report["complete"])
        self.assertIs(report["rejected_rows"][0]["native_result"], ctx.objects[0])
        self.assertEqual(catalog[0]._binding.object, 11)
        self.assertEqual(ctx.generated, [])
        await catalog.close()

    async def test_native_error_owned_row_and_zero_false_partial_text_survive(self):
        ctx = Context(1)
        ctx.element_errors[0] = 87
        ctx.copy_error = 122
        ctx.row_fields = {"PowerBloc.GetNameNoFormatting": copied("#bold kept#!", error=122, total=100),
            "PowerBloc.GetNumMembers": 0, "PowerBloc.IsActive": False,
            "PowerBloc.GetLeader.GetID": -2147483647}
        catalog = await API(ctx).bloc_choices()
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["name"], "kept")
        self.assertEqual(catalog[0]["name_raw"], "#bold kept#!")
        self.assertEqual(catalog[0]["member_count"], 0)
        self.assertIs(catalog[0]["active"], False)
        self.assertEqual(catalog[0]["leader_id"], 0x80000001)
        self.assertIn("incomplete", catalog[0]["reasons"]["name"])
        self.assertEqual(catalog.metadata["result"]["native_pages"][0].error, 122)
        self.assertTrue(catalog.metadata["result"]["reasons"])
        await catalog.close()

    async def test_observed_empty_and_failed_count_are_distinct_actual_catalogs(self):
        for count_error in (0, 87):
            ctx = Context(0)
            ctx.count_error = count_error
            catalog = await API(ctx).bloc_choices()
            self.assertIsInstance(catalog, OwnedChoices)
            self.assertEqual(len(catalog), 0)
            self.assertEqual(catalog.metadata["result"]["complete"], not bool(count_error))
            self.assertEqual(catalog.metadata["result"]["status"], "observed" if not count_error else "unavailable")
            await catalog.close()

    async def test_transport_error_exposes_actual_partial_catalog_and_retains_source(self):
        ctx = Context(66)
        ctx.fail_element = 65
        with self.assertRaises(OSError) as caught:
            await API(ctx).bloc_choices()
        catalog = caught.exception.choices
        self.assertIsInstance(catalog, OwnedChoices)
        self.assertEqual(len(catalog), 64)
        self.assertTrue(catalog.owner.retain_unknown)
        self.assertIn(catalog.owner, [owner for owner in ctx.owner_pool.snapshot() if isinstance(owner, Run)])
        self.assertIn(1000, catalog.owner.values)
        self.assertEqual(catalog.owner.cleaned, 0)
        self.assertEqual(ctx.generated, [])

    async def test_request_join_uses_selected_original_handle_not_equal_display_name(self):
        api, catalog = await self.catalog()
        chosen = catalog[1]
        self.assertEqual(chosen["name"], catalog[0]["name"])
        api._ctx.objects.reverse()
        result = await api.request_join_bloc(chosen, readback=False)
        expression, bindings, run = api._ctx.generated[0]
        self.assertEqual(expression, "PowerBloc.RequestToJoin")
        self.assertEqual([b.object for b in bindings], [11])
        self.assertEqual((run.owners[0].validations, run.owners[0].submissions), (1, 1))
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertIsNone(result["membership_observed"])
        self.assertEqual(api._ctx.performs, ["diplomacy.bloc_choices", "diplomacy.request_join_bloc"])
        self.assertEqual(api._ctx.country_reads, 0)
        await catalog.close()

    async def test_invite_and_kick_bind_one_target_country_without_actor_alias(self):
        for method, command in (("invite_to_bloc", "Invite"), ("kick_from_bloc", "Kick")):
            api, catalog = await self.catalog()
            result = await getattr(api, method)(catalog[0], 0x80000001, readback=False)
            expression, bindings, _ = api._ctx.generated[0]
            self.assertEqual(expression, "PowerBloc." + command + "(Country.Self)")
            self.assertEqual(len(bindings), 2)
            country_bindings = [b for b in bindings if b.domain == "Country" or b.country_id]
            self.assertEqual(len(country_bindings), 1)
            self.assertEqual(country_bindings[0].object, 0x80000001)
            self.assertEqual(api._ctx.country_reads, 0)
            self.assertTrue(result["accepted"])
            self.assertFalse(result["completed"])
            await catalog.close()

    async def test_zero_target_id_is_not_missing_or_actor_lookup(self):
        api, catalog = await self.catalog()
        result = await api.invite_to_bloc(catalog[0], 0, readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(api._ctx.generated[0][1][-1].object, 0)
        self.assertEqual(api._ctx.country_reads, 0)
        await catalog.close()

    async def test_native_rejection_does_not_submit_or_readback(self):
        api, catalog = await self.catalog()
        api._ctx.validation.validated = 0
        before = len(self.pages(api._ctx))
        result = await api.request_join_bloc(catalog[0])
        owner = api._ctx.generated[0][2].owners[0]
        self.assertEqual((owner.validations, owner.submissions), (1, 0))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "native condition")
        self.assertEqual(len(self.pages(api._ctx)), before)
        await catalog.close()

    async def test_membership_readback_uses_native_predicate_and_pending_request_not_completion(self):
        api, catalog = await self.catalog()
        api._ctx.row_fields = {"PowerBloc.IsMember(GetPlayer.Self)": False,
            "PowerBloc.HasPendingRequestToJoinFromCountry(GetPlayer.Self)": True}
        result = await api.request_join_bloc(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertIs(result["membership_observed"], False)
        self.assertIsNone(result["effect_observed"])
        self.assertTrue(result["after"]["membership"]["pending_request"])
        self.assertEqual(len(api._ctx.performs), 2)
        self.assertEqual(len(self.pages(api._ctx)[-1].bindings[0]), 1)
        await catalog.close()

    async def test_kick_readback_copied_false_reports_current_nonmembership(self):
        api, catalog = await self.catalog()
        result = await api.kick_from_bloc(catalog[0], 0)
        self.assertTrue(result["accepted"])
        self.assertIs(result["membership_observed"], False)
        self.assertTrue(result["effect_observed"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["after"]["membership"]["country_id"], 0)
        await catalog.close()

    async def test_readback_field_error_keeps_false_but_does_not_claim_observed_membership(self):
        api, catalog = await self.catalog()
        api._ctx.row_fields = {"PowerBloc.IsMember(Country.Self)": copied(False, error=87)}
        result = await api.kick_from_bloc(catalog[0], 0)
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["membership_observed"])
        self.assertIsNone(result["effect_observed"])
        self.assertIs(result["after"]["membership"]["member"], False)
        await catalog.close()

    async def test_readback_transport_error_preserves_acceptance_and_original_borrow(self):
        api, catalog = await self.catalog()
        api._ctx.readback_error = OSError("copy uncertain")
        result = await api.invite_to_bloc(catalog[0], 5)
        self.assertTrue(result["accepted"])
        self.assertIs(result["readback_error"], api._ctx.readback_error)
        self.assertEqual(api._ctx.generated[0][2].owners[0].submissions, 1)
        self.assertEqual(catalog.active_leases, 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertTrue(api._ctx.runs[-1].retain_unknown)

    async def test_unsettled_validation_or_submission_retains_catalog_and_skips_readback(self):
        for stage in ("validation", "submission"):
            api, catalog = await self.catalog()
            setattr(api._ctx, "unknown_" + stage, True)
            before = len(self.pages(api._ctx))
            result = await api.request_join_bloc(catalog[0])
            run = api._ctx.generated[0][2]
            self.assertFalse(result["ownership_settled"])
            self.assertEqual(run.cleaned, 0)
            self.assertEqual(run.owners[0].submissions, int(stage == "submission"))
            self.assertEqual(len(self.pages(api._ctx)), before)
            self.assertIn(run, [owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, Run)])
            if stage == "submission":
                self.assertTrue(result["accepted"])
                self.assertIn("readback_skipped", result)
            with self.assertRaises(ChoicesBusyError):
                await catalog.close()

    async def test_dispatch_exception_never_replays_or_releases_the_original_choice(self):
        api, catalog = await self.catalog()
        api._ctx.dispatch_error = OSError("submit uncertain")
        with self.assertRaises(OSError):
            await api.kick_from_bloc(catalog[0], 7)
        run = api._ctx.generated[0][2]
        self.assertEqual(run.owners[0].submissions, 1)
        self.assertEqual(run.cleaned, 0)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_pending_submit_blocks_catalog_close_without_extra_whole_workflow_lock(self):
        api, catalog = await self.catalog()
        entered, released = asyncio.Event(), asyncio.Event()
        api._ctx.hold_submit = (entered, released)
        action = asyncio.create_task(api.request_join_bloc(catalog[0], readback=False))
        await entered.wait()
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertEqual(len(api._ctx.generated), 1)
        released.set()
        result = await action
        self.assertTrue(result["accepted"])
        await catalog.close()
        self.assertEqual([owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, Run)], [])

    async def test_leave_uses_one_actor_root_and_optional_false_readback(self):
        api = API(Context())
        result = await api.leave_bloc()
        expression, bindings, run = api._ctx.generated[0]
        self.assertEqual(expression, "Country.LeavePowerBloc")
        self.assertEqual(len(bindings), 1)
        self.assertTrue(bindings[0].country_id)
        self.assertEqual(bindings[0].object, 0)
        self.assertTrue(result["accepted"])
        self.assertIs(result["membership_observed"], False)
        self.assertTrue(result["effect_observed"])
        self.assertFalse(result["completed"])
        self.assertEqual(api._ctx.performs, ["diplomacy.leave_bloc"])
        self.assertEqual(api._ctx.country_reads, 1)
        self.assertEqual([owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, Run)], [])

    async def test_principle_choices_keep_actual_parent_lease_effects_and_cooldown_without_validate(self):
        api, catalog = await self.catalog()
        api._ctx.row_fields = {"PowerBloc.GetNameNoFormatting": "native parent now",
            "PowerBlocPrinciple.GetNameNoFormatting": "#bold Principle#!",
            "PowerBloc.IsPrincipleOnCooldown(PowerBlocPrinciple.Self)": False}
        principles = await api.principle_choices(catalog[1])
        self.assertEqual(len(principles), 2)
        self.assertEqual(principles[0]["bloc_name"], "native parent now")
        self.assertEqual(principles[0]["name"], "Principle")
        self.assertIs(principles[0]["on_cooldown"], False)
        self.assertEqual(principles[0]._binding.object, 100)
        self.assertIs(principles.metadata["bloc_choice"], catalog[1])
        self.assertEqual(api._ctx.generated, [])
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        await principles.close()
        await catalog.close()
        self.assertEqual([owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, Run)], [])

    async def test_upgrade_one_original_principle_with_parent_and_once_validation_submission(self):
        api, catalog = await self.catalog()
        principles = await api.principle_choices(catalog[0])
        result = await api.upgrade_bloc_principle(principles[1])
        expression, bindings, run = api._ctx.generated[0]
        self.assertEqual(expression, "PowerBlocPrinciple.UpgradeToNextLevel")
        self.assertEqual([b.object for b in bindings], [101])
        self.assertEqual((run.owners[0].validations, run.owners[0].submissions), (1, 1))
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertIn("selected_principle", result["after"])
        self.assertEqual(result["target_scope"], "native_player_country_current_bloc_at_execution")
        self.assertEqual(result["after"]["observation_scope"],
            "GetPlayer.GetPowerBloc at observation time; not command-actor readback if the player Country changed")
        self.assertFalse(result["after"]["completion_claimed"])
        self.assertEqual(api._ctx.performs, ["diplomacy.bloc_choices", "diplomacy.principle_choices", "diplomacy.upgrade_bloc_principle"])
        await principles.close()
        await catalog.close()

    async def test_upgrade_reads_observation_time_player_bloc_not_source_or_assumed_command_actor(self):
        api, catalog = await self.catalog()
        api._ctx.row_fields = {"PowerBloc.GetNameNoFormatting": "foreign source bloc"}
        principles = await api.principle_choices(catalog[1])
        self.assertEqual(principles[0]["bloc_name"], "foreign source bloc")
        api._ctx.row_fields = {
            "PowerBloc.GetNameNoFormatting": "do not read source bloc",
            "GetPlayer.GetPowerBloc.GetNameNoFormatting": "observation-time player's bloc",
            "GetPlayer.GetID": -2147483646,
            "GetPlayer.GetPowerBloc.HasPrinciple(PowerBlocPrinciple.Self)": False,
        }
        result = await api.upgrade_bloc_principle(principles[0])
        row = result["after"]["selected_principle"]
        self.assertEqual(row["bloc_name"], "observation-time player's bloc")
        self.assertEqual(row["observed_player_country_id"], 0x80000002)
        self.assertIs(row["currently_present"], False)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertIsNone(result["effect_observed"])
        self.assertIn("player Country changed", result["after"]["observation_scope"])
        self.assertEqual(len(api._ctx.generated[0][1]), 1)
        page = self.pages(api._ctx)[-1]
        self.assertEqual([b.object for b in page.bindings[0]], [100])
        self.assertFalse(any(expression.startswith("PowerBloc.") for expression in page.fields))
        await principles.close()
        await catalog.close()

    async def test_unchosen_identity_forms_forged_and_foreign_choices_rejected_before_commands(self):
        api, catalog = await self.catalog()
        for value in ("same display name", 0, 11, dict(catalog[0]), None):
            with self.assertRaises(TypeError):
                await api.request_join_bloc(value)
        forged = OwnedChoice(catalog, catalog[0].native_result, "PowerBloc", catalog[0]._binding, {})
        with self.assertRaises(ValueError):
            await api.request_join_bloc(forged)
        with self.assertRaises(ValueError):
            await API(Context()).request_join_bloc(catalog[0])
        self.assertEqual(api._ctx.generated, [])
        await catalog.close()
        with self.assertRaises(RuntimeError):
            await api.request_join_bloc(catalog[0])

    async def test_primitive_options_reject_bool_ids_invalid_sentinel_and_nonbool_readback(self):
        api, catalog = await self.catalog()
        for value in (True, -1, 0xffffffff, "AAA", None):
            with self.assertRaises(ValueError):
                await api.invite_to_bloc(catalog[0], value)
        with self.assertRaises(ValueError):
            await api.leave_bloc(readback=1)
        with self.assertRaises(ValueError):
            await api.request_join_bloc(catalog[0], readback=None)
        with self.assertRaises(ValueError):
            await api.bloc_choices("invented")
        self.assertEqual(api._ctx.generated, [])
        await catalog.close()

    async def test_partial_validation_reason_keeps_one_submit_and_native_acceptance(self):
        api, catalog = await self.catalog()
        api._ctx.validation.error = 122
        result = await api.request_join_bloc(catalog[0], readback=False)
        run = api._ctx.generated[0][2]
        self.assertTrue(result["accepted"])
        self.assertIs(result["validation"], api._ctx.validation)
        self.assertEqual((run.owners[0].validations, run.owners[0].submissions), (1, 1))
        await catalog.close()

    async def test_cleanup_warning_keeps_acceptance_and_catalog_lease(self):
        api, catalog = await self.catalog()
        api._ctx.cleanup_error = OSError("native cleanup uncertain")
        result = await api.request_join_bloc(catalog[0], readback=False)
        run = api._ctx.generated[0][2]
        self.assertTrue(result["accepted"])
        self.assertEqual(result["cleanup"]["status"], "cleanup_unknown_retained")
        self.assertIn(run, [owner for owner in api._ctx.owner_pool.snapshot() if isinstance(owner, Run)])
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_catalog_adapter_is_reused_after_context_adapter_change(self):
        api, catalog = await self.catalog()
        api._ctx.adapters["data"] = "different-data"
        await api.request_join_bloc(catalog[0])
        action_run = api._ctx.generated[0][2]
        self.assertEqual(action_run.adapters["data"], "original-data")
        self.assertEqual(self.pages(api._ctx)[-1].adapter, "original-data")
        await catalog.close()


# Customization uses the real _Run, including Action22/input ownership and cleanup.
KINDS.update({field.expression: field.kind for field in bloc._CUSTOMIZATION_PREVIEW})


def customization_context(native, handle, **kwargs):
    return NS(operation="customization_context", handle=handle, native=native,
        adapter=kwargs["adapter"], process=kwargs["process"])


def customization_action(native, expression, input, bindings=(), **kwargs):
    return NS(operation="customization_action", expression=expression, input=input,
        bindings=tuple(bindings), native=native, adapter=kwargs["adapter"], process=kwargs["process"])


def customization_release(native, handle, **kwargs):
    return NS(operation="release", handle=handle, native=native,
        adapter=kwargs["adapter"], process=kwargs["process"])


class CustomizationContext(Context):
    def __init__(self):
        super().__init__(1)
        # Native bundle fixture: no public encoder for non-name fields is used.
        self.bundle = {"name": "old native name", "coa": (4, 7, 9), "texture": "native texture",
                       "color": 0x88442200, "statue": (1, 3, 5)}
        self.draft_bundle = None
        self.preview_values = {
            "PowerBlocCustomizationPopup.ConfirmGetDesc": "native confirmation description",
            "PowerBlocCustomizationPopup.GetPowerBlocMapTextureSelector.GetSelectedItem.GetKey": "native_map_texture",
            "PowerBlocCustomizationPopup.GetPowerBlocMapTextureSelector.GetSelectedItem.GetName": "#bold Original texture#!",
            "PowerBlocCustomizationPopup.GetPowerBlocMapTextureSelector.GetSelectedItem.GetIndex": 0,
            "PowerBlocCustomizationPopup.GetStatueHeroType.GetName": "Original hero",
            "PowerBlocCustomizationPopup.GetStatueHeroType.GetEntity": "original_hero_entity",
            "PowerBlocCustomizationPopup.GetStatuePedestalType.GetName": "Original pedestal",
            "PowerBlocCustomizationPopup.GetStatuePedestalType.GetEntity": "original_pedestal_entity",
            "PowerBlocCustomizationPopup.GetCustomCoaPreview.GetSmallPowerBlocCoaFrame": 0,
            "PowerBlocCustomizationPopup.GetCustomCoaPreview.GetMediumPowerBlocCoaFrame": 4,
            "PowerBlocCustomizationPopup.GetCustomCoaPreview.GetLargePowerBlocCoaFrame": 7,
        }
        self.context_result = None
        self.context_type_id = 319
        self.context_error = 0
        self.context_raw = False
        self.context_wrong_owned_type = False
        self.context_calls, self.action_calls, self.releases = [], [], []
        self.edits, self.confirms = [], []
        self.edit_error = self.confirm_error = self.condition_error = 0
        self.edit_invoked = self.edit_accepted = 1
        self.confirm_invoked = self.confirm_accepted = 1
        self.condition_value, self.condition_kind = True, data.Kind.BOOL
        self.apply_confirm = True
        self.draft_error = self.after_error = self.release_error = 0
        self.draft_failure = self.after_failure = self.action_failure = None
        self.action_failure_expression = None
        self.wait_action = None
        self.action_results = []
    async def query(self, label, factory, owner=None):
        q = factory()
        if getattr(q, "operation", None) == "customization_context":
            self.calls.append((label, q))
            self.context_calls.append(q)
            self.draft_bundle = dict(self.bundle)
            spelling = ".?AU?$SAnyType@V?$CPdxPtr@VCPowerBlocCustomizationPopup@@@@@PdxAnyImpl@@"
            if self.context_raw:
                spelling = ".?AU?$SAnyType@PEAVCPowerBlocCustomizationPopup@@@PdxAnyImpl@@"
            if self.context_wrong_owned_type:
                spelling = ".?AU?$SAnyType@V?$CPdxPtr@VCDataPowerBlocCustomizationPopup@@@@@PdxAnyImpl@@"
            self.context_result = NS(action=data.Action.CREATE_OWNED_CONTEXT, value_handle=5000,
                error=self.context_error, type_id=self.context_type_id, native_type_name=spelling,
                value=data.NativeObject(0x12345000, self.context_type_id, 5000, spelling))
            return self.context_result
        if getattr(q, "operation", None) == "customization_action":
            self.calls.append((label, q))
            self.action_calls.append(q)
            if self.wait_action and self.wait_action[0] == q.expression:
                _, entered, released = self.wait_action
                entered.set()
                await released.wait()
            if (self.action_failure and (self.action_failure_expression is None
                    or self.action_failure_expression == q.expression)):
                raise self.action_failure
            if q.expression == "PowerBlocCustomizationPopup.OnEditName":
                if q.input.kind != data.UTF8_INPUT:
                    raise AssertionError("OnEditName needs exact CUTF8String input, not CString/formal text")
                self.edits.append(q.input.text)
                if self.edit_invoked and self.edit_accepted:
                    # Fixture models only the original leaf's proved semantics.
                    self.draft_bundle["name"] = q.input.text.split("\0", 1)[0].strip(" \t\r\n")
                error, invoked, accepted = self.edit_error, self.edit_invoked, self.edit_accepted
            elif q.expression == "PowerBlocCustomizationPopup.ConfirmClicked":
                if q.input != data.TypedInput.empty():
                    raise AssertionError("ConfirmClicked is an immediate empty-input property action")
                self.confirms.append(dict(self.draft_bundle))
                if self.apply_confirm and self.confirm_invoked and self.confirm_accepted:
                    self.bundle = dict(self.draft_bundle)
                error, invoked, accepted = self.confirm_error, self.confirm_invoked, self.confirm_accepted
            else:
                raise AssertionError("No guessed arbitrary customization setter/Show: " + q.expression)
            handle = 6000 + len(self.action_results)
            raw = NS(action=data.Action.INVOKE_ACTION, value_handle=handle, error=error,
                snapshot=b"copied-prefix" if error == 122 else b"complete", snapshot_bytes=13 if error == 122 else 8,
                total=100 if error == 122 else 8)
            result = NS(result=raw, value_handle=handle, error=error, invoked=invoked, accepted=accepted)
            self.action_results.append(result)
            return result
        if getattr(q, "operation", None) == "evaluate" and q.expression == "PowerBlocCustomizationPopup.ConfirmIsValid":
            self.calls.append((label, q))
            return NS(action=data.Action.EVALUATE, value_handle=0, error=self.condition_error,
                kind=self.condition_kind, value=self.condition_value)
        if getattr(q, "operation", None) == "page" and self.draft_bundle is not None:
            if q.fields == tuple(field.expression for field in bloc._CUSTOMIZATION_PREVIEW):
                self.calls.append((label, q))
                if self.draft_failure:
                    raise self.draft_failure
                return native_page(q, [{**self.preview_values,
                    "PowerBlocCustomizationPopup.GetNewCustomName": copied(self.draft_bundle["name"], error=self.draft_error)}])
            if q.fields == ("PowerBloc.GetNameNoFormatting",):
                self.calls.append((label, q))
                if self.after_failure:
                    raise self.after_failure
                return native_page(q, [{q.fields[0]: copied(self.bundle["name"], error=self.after_error)}])
        if getattr(q, "operation", None) == "release":
            self.calls.append((label, q))
            self.releases.append(q)
            return NS(action=data.Action.RELEASE, value_handle=0, error=self.release_error)
        return await super().query(label, lambda: q, owner=owner)


class RawCustomizationContext(CustomizationContext):
    """A real RAW DataResult header; no host NativeObject is manufactured."""
    def __init__(self, spelling, *, early_failure=False):
        super().__init__()
        self.raw_spelling = spelling
        self.early_failure = early_failure

    async def query(self, label, factory, owner=None):
        query = factory()
        if getattr(query, "operation", None) != "customization_context":
            return await super().query(label, lambda: query, owner=owner)
        from dataclasses import fields
        self.calls.append((label, query))
        self.context_calls.append(query)
        self.draft_bundle = dict(self.bundle)
        values = {field.name: 0 for field in fields(data.DataResult) if field.name != "rows"}
        payload = b"original raw context bytes"
        values.update(size=data.REQUEST.size, error=0, action=data.Action.CREATE_OWNED_CONTEXT,
            kind=data.Kind.RAW, type_id=self.context_type_id, value_handle=5000,
            object=0, raw_any=bytes(32), native_type_name=self.raw_spelling,
            snapshot=payload, snapshot_bytes=len(payload), total=len(payload))
        if self.early_failure:
            # Native revision2 clears output fields before its app-null/busy
            # error21 return. The input source handle is not a published Any.
            values.update(error=21, type_id=-1, value_handle=0, any_type=0,
                any_vtable=0, object=0, raw_any=bytes(32), native_type_name="",
                snapshot=b"", snapshot_bytes=0, total=0)
        self.context_result = data.DataResult(**values)
        return self.context_result


class BlocCustomizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name, replacement in (("evaluate", evaluate), ("list_count", count),
                ("list_element", element), ("project_page", project),
                ("create_power_bloc_context", customization_context),
                ("invoke_action", customization_action), ("release", customization_release)):
            patcher = patch.object(data, name, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def catalog(self, context=None):
        api = API(context or CustomizationContext())
        return api, await api.bloc_choices()

    def action_run(self, ctx):
        return next(run for run in reversed(ctx.diagnostics)
            if isinstance(run, run_mod.Run) and run.label == "customize_bloc")

    async def test_early_context_failure_does_not_adopt_or_release_catalog_input_handle(self):
        ctx = RawCustomizationContext("", early_failure=True)
        api, catalog = await self.catalog(ctx)
        original_handle = catalog[0]._binding.object
        result = await api.customize_bloc(catalog[0], name="not invoked")
        self.assertEqual(ctx.context_calls[0].handle, original_handle)
        self.assertEqual((result["native_error"], result["status"]), (21, "native_result"))
        self.assertIs(result["context_result"], ctx.context_result)
        self.assertEqual(result["context_result"].value_handle, 0)
        self.assertEqual(result["context_result"].type_id, -1)
        self.assertEqual(result["context_result"].native_type_name, "")
        self.assertIsNone(result["accepted"])
        self.assertEqual(ctx.action_calls, [])
        operation_run = self.action_run(ctx)
        self.assertEqual(operation_run.values, [])
        self.assertEqual(operation_run.status, "complete")
        self.assertIn(original_handle, catalog.owner.values)
        self.assertEqual(ctx.releases, [])
        self.assertEqual(catalog.active_leases, 0)
        await catalog.close()
        self.assertEqual([query.handle for query in ctx.releases].count(original_handle), 1)

    async def test_owned_raw_header_context_uses_canonical_binding_without_object_retag(self):
        spelling = ".?AU?$SAnyType@V?$CPdxPtr@VCPowerBlocCustomizationPopup@@@@@PdxAnyImpl@@"
        ctx = RawCustomizationContext(spelling)
        api, catalog = await self.catalog(ctx)
        result = await api.customize_bloc(catalog[0], name="new raw context name")
        self.assertIsInstance(result["context_result"], data.DataResult)
        self.assertEqual(result["context_result"].kind, data.Kind.RAW)
        self.assertEqual(result["context_result"].object, 0)
        self.assertEqual(result["context_result"].value, b"original raw context bytes")
        self.assertEqual(ctx.action_calls[0].bindings, (data.ObjectBinding(319, 5000, retained=True),))
        self.assertTrue(result["accepted"])
        self.assertEqual(len(ctx.confirms), 1)
        self.assertEqual(ctx.generated, [])
        self.assertEqual(self.action_run(ctx).status, "complete")
        await catalog.close()

    async def test_borrowed_raw_header_context_is_preserved_but_not_retagged(self):
        spelling = ".?AU?$SAnyType@PEAVCPowerBlocCustomizationPopup@@@PdxAnyImpl@@"
        ctx = RawCustomizationContext(spelling)
        api, catalog = await self.catalog(ctx)
        result = await api.customize_bloc(catalog[0], name="must not be invoked")
        self.assertIs(result["context_result"], ctx.context_result)
        self.assertEqual(result["context_result"].value, b"original raw context bytes")
        self.assertEqual(result["status"], "native_result")
        self.assertIsNone(result["accepted"])
        self.assertEqual(ctx.action_calls, [])
        self.assertEqual(ctx.generated, [])
        await catalog.close()

    async def test_name_uses_actual_context_type_retained_source_and_one_original_confirm(self):
        api, catalog = await self.catalog()
        original = dict(api._ctx.bundle)
        result = await api.customize_bloc(catalog[0], name="  New Native Name\t")
        ctx = api._ctx
        self.assertEqual(ctx.context_calls[0].handle, catalog[0]._binding.object)
        self.assertEqual(len(ctx.context_calls), 1)
        self.assertEqual([q.expression for q in ctx.action_calls], [
            "PowerBlocCustomizationPopup.OnEditName", "PowerBlocCustomizationPopup.ConfirmClicked"])
        self.assertEqual(ctx.action_calls[0].input, data.TypedInput.utf8_string("  New Native Name\t"))
        self.assertEqual(ctx.action_calls[0].bindings, (data.ObjectBinding(319, 5000, retained=True),))
        self.assertEqual(ctx.action_calls[1].bindings, ctx.action_calls[0].bindings)
        self.assertEqual(ctx.edits, ["  New Native Name\t"])
        self.assertEqual(len(ctx.confirms), 1)
        self.assertEqual(ctx.bundle, {**original, "name": "New Native Name"})
        self.assertTrue(result["accepted"])
        self.assertTrue(result["effect_observed"])
        self.assertEqual(result["draft"]["name"], "New Native Name")
        self.assertFalse(result["completed"])
        self.assertEqual(ctx.generated, [])
        self.assertEqual(ctx.country_reads, 0)
        self.assertEqual(ctx.performs, ["diplomacy.bloc_choices", "diplomacy.customize_bloc"])
        self.assertEqual([q.handle for q in ctx.releases], [6001, 6000, 5000])
        self.assertEqual(self.action_run(ctx).status, "complete")
        await catalog.close()
        self.assertEqual([q.handle for q in ctx.releases], [6001, 6000, 5000, 10, 1000])

    async def test_default_none_is_read_only_preview_even_when_readback_false(self):
        for options in ({}, {"name": None, "readback": False}):
            api, catalog = await self.catalog()
            result = await api.customize_bloc(catalog[0], **options)
            self.assertEqual(result["status"], "preview")
            self.assertIsNone(result["accepted"])
            self.assertEqual(result["draft"]["name"], "old native name")
            self.assertEqual(api._ctx.action_calls, [])
            self.assertFalse(any(getattr(q, "expression", None) == "PowerBlocCustomizationPopup.ConfirmIsValid"
                for _, q in api._ctx.calls))
            await catalog.close()

    async def test_preview_copies_original_texture_statue_and_coa_scalar_descriptors_without_mutation(self):
        api, catalog = await self.catalog()
        before = dict(api._ctx.bundle)
        result = await api.customize_bloc(catalog[0])
        draft = result["draft"]
        self.assertEqual(draft["map_texture_key"], "native_map_texture")
        self.assertEqual(draft["map_texture_name"], "Original texture")
        self.assertEqual(draft["map_texture_name_raw"], "#bold Original texture#!")
        self.assertEqual(draft["map_texture_index"], 0)
        self.assertEqual(draft["statue_hero_name"], "Original hero")
        self.assertEqual(draft["statue_hero_entity"], "original_hero_entity")
        self.assertEqual(draft["statue_pedestal_name"], "Original pedestal")
        self.assertEqual(draft["statue_pedestal_entity"], "original_pedestal_entity")
        self.assertEqual((draft["coa_small_frame"], draft["coa_medium_frame"], draft["coa_large_frame"]), (0, 4, 7))
        self.assertIn("not input identities", draft["descriptor_scope"])
        self.assertEqual(api._ctx.bundle, before)
        self.assertEqual(api._ctx.action_calls, [])
        pages = [q for _, q in api._ctx.calls if getattr(q, "operation", None) == "page"]
        self.assertEqual(pages[-1].fields, tuple(field.expression for field in bloc._CUSTOMIZATION_PREVIEW))
        self.assertEqual(pages[-1].bindings, ((data.ObjectBinding(319, 5000, retained=True),),))
        self.assertLessEqual(len(pages[-1].fields), 32)
        self.assertEqual(len(pages[-1].bindings), 1)
        await catalog.close()

    async def test_preview_descriptor_errors_preserve_values_without_blocking_name_confirmation(self):
        api, catalog = await self.catalog()
        expression = "PowerBlocCustomizationPopup.GetStatuePedestalType.GetName"
        api._ctx.preview_values[expression] = copied("#bold kept pedestal#!", error=122, total=100)
        result = await api.customize_bloc(catalog[0], name="new")
        self.assertTrue(result["accepted"])
        self.assertTrue(result["effect_observed"])
        self.assertEqual(result["draft"]["statue_pedestal_name_raw"], "#bold kept pedestal#!")
        self.assertIn("native getter error 122", result["draft"]["reasons"]["fields"]["statue_pedestal_name"])
        self.assertFalse(result["draft"]["complete"])
        self.assertEqual(len(api._ctx.confirms), 1)
        await catalog.close()

    async def test_exact_utf8_input_keeps_nul_unicode_and_native_ascii_only_normalization(self):
        api, catalog = await self.catalog()
        name = " \t\r\n\u00a0命名\u00a0 \t\r\n\0suffix ignored by native leaf"
        result = await api.customize_bloc(catalog[0], name=name)
        self.assertEqual(api._ctx.action_calls[0].input.text, name)
        self.assertEqual(result["requested_name"], name)
        self.assertEqual(result["draft"]["name_raw"], "\u00a0命名\u00a0")
        self.assertEqual(result["after"]["name_raw"], "\u00a0命名\u00a0")
        self.assertEqual(result["draft"]["name"], "命名")
        self.assertEqual(result["after"]["name"], "命名")
        self.assertTrue(result["effect_observed"])
        await catalog.close()

    async def test_empty_name_is_explicit_input_native_predicate_decides(self):
        api, catalog = await self.catalog()
        api._ctx.condition_value = False
        result = await api.customize_bloc(catalog[0], name="")
        self.assertEqual(api._ctx.edits, [""])
        self.assertEqual(result["draft"]["name"], "")
        self.assertFalse(result["accepted"])
        self.assertFalse(result["eligible"])
        self.assertEqual(api._ctx.confirms, [])
        self.assertEqual(api._ctx.bundle["name"], "old native name")
        await catalog.close()

    async def test_partial_input_snapshot_keeps_accepted_setter_and_confirms_without_replay(self):
        api, catalog = await self.catalog()
        api._ctx.edit_error = 122
        result = await api.customize_bloc(catalog[0], name="new")
        self.assertIs(result["name_input"], api._ctx.action_results[0])
        self.assertEqual(result["name_input"].error, 122)
        self.assertLess(len(result["name_input"].result.snapshot), result["name_input"].result.total)
        self.assertTrue(result["name_input_accepted"])
        self.assertTrue(result["accepted"])
        self.assertEqual(api._ctx.edits, ["new"])
        self.assertEqual(len(api._ctx.confirms), 1)
        self.assertTrue(result["effect_observed"])
        await catalog.close()

    async def test_confirm_callback_flags_survive_native_error_and_readback_failure(self):
        api, catalog = await self.catalog()
        api._ctx.confirm_error = 87
        api._ctx.after_failure = OSError("world readback uncertain")
        result = await api.customize_bloc(catalog[0], name="new")
        self.assertTrue(result["invoked"])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["invocation"].error, 87)
        self.assertIs(result["readback_error"], api._ctx.after_failure)
        self.assertIsNone(result["effect_observed"])
        self.assertFalse(result["completed"])
        self.assertEqual(len(api._ctx.confirms), 1)
        self.assertEqual(self.action_run(api._ctx).status, "retained_pending_or_unknown")
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_readback_false_avoids_optional_getters_but_still_uses_native_predicate(self):
        api, catalog = await self.catalog()
        before = len(api._ctx.calls)
        result = await api.customize_bloc(catalog[0], name="new", readback=False)
        self.assertTrue(result["accepted"])
        self.assertNotIn("draft", result)
        self.assertNotIn("after", result)
        calls = api._ctx.calls[before:]
        self.assertFalse(any(getattr(q, "operation", None) == "page" for _, q in calls))
        self.assertEqual(sum(getattr(q, "expression", None) == "PowerBlocCustomizationPopup.ConfirmIsValid"
            for _, q in calls), 1)
        await catalog.close()

    async def test_adapter_is_original_choice_adapter_not_later_context_default(self):
        api, catalog = await self.catalog()
        api._ctx.adapters["data"] = "later unrelated data"
        result = await api.customize_bloc(catalog[0], name="new")
        self.assertTrue(result["accepted"])
        self.assertTrue(all(q.adapter == "original-data"
            for q in (*api._ctx.context_calls, *api._ctx.action_calls, *api._ctx.releases)))
        self.assertTrue(all(q.native is api._ctx.native and q.process is api._ctx.process
            for q in (*api._ctx.context_calls, *api._ctx.action_calls, *api._ctx.releases)))
        await catalog.close()

    async def test_pending_confirm_keeps_source_popup_and_input_any_in_one_original_run(self):
        api, catalog = await self.catalog()
        entered, released = asyncio.Event(), asyncio.Event()
        api._ctx.wait_action = ("PowerBlocCustomizationPopup.ConfirmClicked", entered, released)
        task = asyncio.create_task(api.customize_bloc(catalog[0], name="new"))
        await entered.wait()
        run = self.action_run(api._ctx)
        self.assertEqual(run.values, [5000, 6000])
        self.assertIn(run, api._ctx.owner_pool.snapshot())
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        self.assertEqual(api._ctx.releases, [])
        released.set()
        result = await task
        self.assertTrue(result["accepted"])
        self.assertEqual(len(api._ctx.context_calls), 1)
        self.assertEqual(len(api._ctx.confirms), 1)
        await catalog.close()

    async def test_unknown_setter_keeps_original_graph_and_query_without_confirm_or_replay(self):
        api, catalog = await self.catalog()
        api._ctx.action_failure = OSError("setter original unknown")
        with self.assertRaises(OSError):
            await api.customize_bloc(catalog[0], name="new")
        run = self.action_run(api._ctx)
        self.assertTrue(run.retain_unknown)
        self.assertIs(run.uncertain_query, api._ctx.action_calls[0])
        self.assertEqual(run.values, [5000])
        self.assertEqual(len(api._ctx.action_calls), 1)
        self.assertEqual(api._ctx.releases, [])
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_unknown_confirm_keeps_accepted_name_input_and_original_query_without_replay(self):
        api, catalog = await self.catalog()
        api._ctx.action_failure = OSError("confirm original unknown")
        api._ctx.action_failure_expression = "PowerBlocCustomizationPopup.ConfirmClicked"
        with self.assertRaises(OSError):
            await api.customize_bloc(catalog[0], name="new", readback=False)
        run = self.action_run(api._ctx)
        self.assertTrue(run.public_result["name_input_accepted"])
        self.assertIsNone(run.public_result["accepted"])
        self.assertIs(run.uncertain_query, api._ctx.action_calls[1])
        self.assertEqual(run.values, [5000, 6000])
        self.assertEqual(api._ctx.edits, ["new"])
        self.assertEqual(len(api._ctx.action_calls), 2)
        self.assertEqual(api._ctx.releases, [])
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_cleanup_warning_is_separate_from_actual_accepted_callback(self):
        api, catalog = await self.catalog()
        api._ctx.release_error = 87
        result = await api.customize_bloc(catalog[0], name="new", readback=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["invocation"].error, 0)
        self.assertEqual(result["cleanup"]["status"], "native_error_retained")
        self.assertEqual(result["cleanup"]["native_result"].error, 87)
        self.assertEqual(result["native_error"], 0)
        self.assertEqual(len(api._ctx.confirms), 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()

    async def test_wrong_raw_or_cdata_context_spelling_is_never_retagged(self):
        for attribute in ("context_raw", "context_wrong_owned_type"):
            api, catalog = await self.catalog()
            setattr(api._ctx, attribute, True)
            result = await api.customize_bloc(catalog[0], name="new")
            self.assertIs(result["context_result"], api._ctx.context_result)
            self.assertIsNone(result["accepted"])
            self.assertEqual(api._ctx.action_calls, [])
            self.assertIn("ownership", result["reason"])
            await catalog.close()

    async def test_factory_native_failure_preserves_original_result_and_does_not_edit(self):
        api, catalog = await self.catalog()
        api._ctx.context_error = 87
        result = await api.customize_bloc(catalog[0], name="new")
        self.assertIs(result["context_result"], api._ctx.context_result)
        self.assertEqual(result["native_error"], 87)
        self.assertIsNone(result["accepted"])
        self.assertEqual(api._ctx.action_calls, [])
        await catalog.close()

    async def test_complete_owned_context_with_partial_copy_error_can_continue(self):
        for error in (122, data.ERROR_MORE_DATA):
            api, catalog = await self.catalog()
            api._ctx.context_error = error
            result = await api.customize_bloc(catalog[0], name="new")
            self.assertEqual(result["context_result"].error, error)
            self.assertTrue(result["accepted"])
            self.assertEqual(len(api._ctx.context_calls), 1)
            self.assertEqual(len(api._ctx.confirms), 1)
            await catalog.close()

    async def test_failed_or_nonboolean_native_predicate_never_confirms(self):
        for error, kind, value in ((87, data.Kind.BOOL, True), (0, data.Kind.I32, 1)):
            api, catalog = await self.catalog()
            api._ctx.condition_error, api._ctx.condition_kind, api._ctx.condition_value = error, kind, value
            result = await api.customize_bloc(catalog[0], name="new")
            self.assertTrue(result["name_input_accepted"])
            self.assertIsNone(result["accepted"])
            self.assertEqual(result["validation"].error, error)
            self.assertEqual(result["draft"]["name_raw"], "new")
            self.assertEqual(api._ctx.confirms, [])
            await catalog.close()

    async def test_setter_rejection_or_native_failure_stays_separate_from_confirm_acceptance(self):
        for invoked, accepted, error in ((1, 0, 0), (0, 1, 0), (1, 1, 87)):
            api, catalog = await self.catalog()
            api._ctx.edit_invoked, api._ctx.edit_accepted, api._ctx.edit_error = invoked, accepted, error
            result = await api.customize_bloc(catalog[0], name="new")
            self.assertEqual(result["name_input_invoked"], bool(invoked))
            self.assertEqual(result["name_input_accepted"], bool(accepted))
            self.assertEqual(result["name_input"].error, error)
            self.assertIsNone(result["accepted"])
            self.assertIsNone(result["validation"])
            self.assertEqual(api._ctx.confirms, [])
            await catalog.close()

    async def test_optional_getter_native_errors_do_not_hide_confirm_callback(self):
        api, catalog = await self.catalog()
        api._ctx.draft_error = api._ctx.after_error = 122
        result = await api.customize_bloc(catalog[0], name="new")
        self.assertTrue(result["accepted"])
        self.assertTrue(result["invoked"])
        self.assertEqual(result["draft"]["name_raw"], "new")
        self.assertEqual(result["after"]["name_raw"], "new")
        self.assertIn("native getter error 122", result["draft"]["reasons"]["fields"]["name"])
        self.assertIsNone(result["effect_observed"])
        self.assertEqual(len(api._ctx.confirms), 1)
        await catalog.close()

    async def test_raw_name_equality_not_cleaned_display_view_decides_observation(self):
        ctx = CustomizationContext()
        ctx.bundle["name"] = "new"
        ctx.apply_confirm = False
        api, catalog = await self.catalog(ctx)
        result = await api.customize_bloc(catalog[0], name="\u00a0new\u00a0")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["draft"]["name"], result["after"]["name"])
        self.assertNotEqual(result["draft"]["name_raw"], result["after"]["name_raw"])
        self.assertFalse(result["effect_observed"])
        await catalog.close()

    async def test_dynamic_context_type_must_be_actual_nonnegative_integer(self):
        for type_id in (-1, None, True):
            api, catalog = await self.catalog()
            api._ctx.context_type_id = type_id
            result = await api.customize_bloc(catalog[0], name="new")
            self.assertIsNone(result["accepted"])
            self.assertEqual(api._ctx.action_calls, [])
            self.assertEqual(result["reason"],
                "Native PowerBlocCustomizationPopup wrapper ownership is not established")
            self.assertEqual(result["status"], "native_result")
            self.assertIs(result["context_result"], api._ctx.context_result)
            self.assertIs(result["native_result"], api._ctx.context_result)
            self.assertEqual(result["context_result"].type_id, type_id)
            self.assertIs(type(result["context_result"].type_id), type(type_id))
            await catalog.close()

    async def test_foreign_unadmitted_and_closed_choices_do_not_create_context(self):
        api, catalog = await self.catalog()
        other, foreign = await self.catalog()
        with self.assertRaises(ValueError):
            await api.customize_bloc(foreign[0], name="new")
        original = catalog[0]
        forged = OwnedChoice(catalog, original.native_result, "PowerBloc", original._binding, dict(original))
        with self.assertRaises(ValueError):
            await api.customize_bloc(forged, name="new")
        await catalog.close()
        with self.assertRaises(RuntimeError):
            await api.customize_bloc(original, name="new")
        self.assertEqual(api._ctx.context_calls, [])
        await foreign.close()

    async def test_invalid_inputs_fail_before_context_creation(self):
        api, catalog = await self.catalog()
        for name in (False, 0, {}, b"new"):
            with self.assertRaises(TypeError):
                await api.customize_bloc(catalog[0], name=name)
        with self.assertRaises(ValueError):
            await api.customize_bloc(catalog[0], name="new", readback=1)
        for choice in (0, "name", {"name": "old native name"}):
            with self.assertRaises(TypeError):
                await api.customize_bloc(choice, name="new")
        self.assertEqual(api._ctx.context_calls, [])
        await catalog.close()


if __name__ == "__main__":
    unittest.main()
