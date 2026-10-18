"""CPU-only copied state-management observations; no game is contacted."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game._run import Run as _Run
from pdx_runtime.v3_game.state_management import (
    StateIncorporation, StateManagementAPI, _INCORPORATION_FIELDS,
)


FIELDS = tuple(expression for _, expression in _INCORPORATION_FIELDS)


def page_for(ids, *, world=9, error=0, missing=(), overrides=None):
    cells = []
    overrides = overrides or {}
    for row, identity in enumerate(ids):
        for field, (name, expression) in enumerate(_INCORPORATION_FIELDS):
            if name in missing:
                continue
            if name in ("incorporated", "incorporating", "unincorporated"):
                cell = data.ProjectionCell(row, field, 0, data.Kind.BOOL, 1, 0, b"", 0)
            elif name == "id":
                cell = data.ProjectionCell(row, field, 0, data.Kind.U32, 1, identity, b"", 0)
            elif name == "owner_country_id":
                cell = data.ProjectionCell(row, field, 0, data.Kind.U32, 1, 0, b"", 0)
            elif any(part in name for part in ("description", "effects", "time_left", "name")):
                raw = "原生文字".encode("utf-8")
                cell = data.ProjectionCell(row, field, 0, data.Kind.STRING, 1, 0, raw, len(raw))
            else:
                cell = data.ProjectionCell(row, field, 0, data.Kind.FIXED, 1, 0, b"", 0)
            if name in overrides:
                cell = replace(cell, **overrides[name])
            cells.append(cell)
    return data.ProjectionPage(error, world, len(FIELDS), 0, 77, len(ids), True, len(ids), tuple(cells))


class Context:
    def __init__(self, pages):
        self.native = object()
        self.process = object()
        self.adapters = {"data": 0x1234}
        self.pages = iter(pages)
        self.types = []
        self.queries = []
        self.performs = []
        self.diagnostics = deque()
        self.remembered = []

    async def type_id(self, name):
        self.types.append(name)
        return 34

    async def query(self, label, factory):
        self.queries.append(label)
        factory()
        result = next(self.pages)
        if isinstance(result, BaseException):
            raise result
        return result

    async def perform(self, label, work):
        self.performs.append(label)
        return await work()

    def _remember(self, query):
        self.remembered.append(query)


class StateIncorporationTests(TestCase):
    def test_native_zero_false_and_exact_fixed_bits_are_values_not_missing(self):
        page = page_for((0,))
        report = StateIncorporation(0, 0, page)
        self.assertEqual(report.values["id"], 0)
        self.assertIs(report.values["incorporated"], False)
        self.assertEqual(report.values["owner_country_id"], 0)
        self.assertIsInstance(report.values["progress"], data.FixedPoint)
        self.assertEqual(report.values["progress"].bits, 0)
        self.assertEqual(report.values["name"], "原生文字")
        self.assertTrue(report.complete)
        self.assertEqual(report.missing_fields, ())
        self.assertEqual(report.field_issues, {})

    def test_native_errors_do_not_discard_an_observed_value(self):
        page = page_for((7,), error=5, overrides={"incorporated": {"error": 234, "raw_bits": 0}})
        report = StateIncorporation(7, 0, page)
        self.assertIs(report.values["incorporated"], False)
        self.assertEqual(report.cells["incorporated"].error, 234)
        self.assertIn("234", report.field_issues["incorporated"])
        self.assertIs(report.native_page, page)
        self.assertFalse(report.complete)

    def test_partial_native_text_is_displayed_and_original_bytes_stay_available(self):
        raw = "原生文字".encode()[:4]
        page = page_for((7,), overrides={"name": {"snapshot": raw, "total": 20}})
        report = StateIncorporation(7, 0, page)
        self.assertTrue(report.values["name"].startswith("原"))
        self.assertEqual(report.cells["name"].snapshot, raw)
        self.assertEqual(report.cells["name"].total, 20)
        self.assertIn("incomplete", report.field_issues["name"])
        self.assertFalse(report.complete)

    def test_missing_fields_are_separate_from_observed_native_empty_values(self):
        page = page_for((7,), missing=("name",), overrides={"time_left": {
            "kind": data.Kind.EMPTY, "snapshot": b"", "total": 0}})
        report = StateIncorporation(7, 0, page)
        self.assertNotIn("name", report.values)
        self.assertIn("name", report.missing_fields)
        self.assertIn("time_left", report.values)
        self.assertIsNone(report.values["time_left"])
        self.assertFalse(report.complete)

    def test_requested_identity_does_not_replace_native_observed_identity(self):
        report = StateIncorporation(7, 0, page_for((9,)))
        self.assertEqual(report.requested_state_id, 7)
        self.assertEqual(report.values["id"], 9)

    def test_empty_page_is_not_proof_that_state_or_mechanic_does_not_exist(self):
        page = data.ProjectionPage(21, 0, len(FIELDS), 0, 0, 0, False, 0, ())
        report = StateIncorporation(7, 0, page)
        self.assertEqual(report.values, {})
        self.assertEqual(len(report.missing_fields), len(FIELDS))
        self.assertEqual(report.native_page.error, 21)
        self.assertFalse(report.complete)

    def test_binding_diagnostic_is_retained_even_when_getter_copied_values(self):
        page = page_for((7,), overrides={"id": {"binding_error": 5}})
        report = StateIncorporation(7, 0, page)
        self.assertEqual(report.values["id"], 7)
        self.assertEqual(report.cells["id"].binding_error, 5)
        self.assertFalse(report.complete)


class StateManagementAPITests(IsolatedAsyncioTestCase):
    async def test_one_state_uses_one_shared_page_and_proven_state_full_id_binding(self):
        page = page_for((7,))
        ctx = Context((page,))
        with patch.object(data, "project_page", return_value=N(cleanup_error=None)) as native:
            report = await StateManagementAPI(ctx).incorporation(7)
        native.assert_called_once_with(ctx.native, FIELDS, ((data.ObjectBinding.state(34, 7),),),
            source_expression=None, start=0, limit=1, steps=(), source_arguments=(),
            adapter=0x1234, process=ctx.process)
        self.assertIs(report.native_page, page)
        self.assertEqual(ctx.types, ["State"])
        self.assertEqual(ctx.queries, ["state_management.incorporation"])
        self.assertEqual(ctx.performs, ["state_management.incorporation"])

    async def test_batch_uses_one_page_per_64_states_and_preserves_per_page_world(self):
        ids = tuple(range(65))
        first, second = page_for(ids[:64], world=1), page_for(ids[64:], world=2)
        ctx = Context((first, second))
        with patch.object(data, "project_page", return_value=N(cleanup_error=None)) as native:
            reports = await StateManagementAPI(ctx).incorporation_many(ids)
        self.assertEqual(native.call_count, 2)
        self.assertEqual([c.kwargs["limit"] for c in native.call_args_list], [64, 1])
        self.assertEqual([r.requested_state_id for r in reports], list(ids))
        self.assertEqual([r.values["id"] for r in reports], list(ids))
        self.assertEqual((reports[0].native_page.world, reports[-1].native_page.world), (1, 2))
        self.assertEqual(ctx.types, ["State"])

    async def test_native_page_error_preserves_all_original_cells_and_diagnostics(self):
        page = page_for((7,), error=5)
        ctx = Context((page,))
        with patch.object(data, "project_page", return_value=N(cleanup_error=None)):
            report = await StateManagementAPI(ctx).incorporation(7)
        self.assertEqual(report.values["id"], 7)
        self.assertEqual(ctx.diagnostics[-1]["projection_page"], page)

    async def test_later_unknown_page_keeps_first_page_and_original_pending_query(self):
        ids = tuple(range(65))
        first = page_for(ids[:64])
        pending_query = N(cleanup_error=None)
        failure = RuntimeError("original copied page is pending")
        ctx = Context((first, failure))
        with patch.object(data, "project_page", side_effect=[N(cleanup_error=None), pending_query]) as native:
            with self.assertRaises(RuntimeError) as caught:
                await StateManagementAPI(ctx).incorporation_many(ids)
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.query, pending_query)
        self.assertEqual(len(failure.completed_state_incorporations), 64)
        self.assertIs(failure.completed_state_incorporations[0].native_page, first)
        self.assertEqual(native.call_count, 2)

    async def test_cleanup_warning_does_not_replace_copied_state_values(self):
        page = page_for((7,))
        warning = RuntimeError("copy release delayed")
        query = N(cleanup_error=warning)
        ctx = Context((page,))
        with patch.object(data, "project_page", return_value=query):
            report = await StateManagementAPI(ctx).incorporation(7)
        self.assertIs(report.native_page, page)
        self.assertEqual(report.values["id"], 7)
        self.assertEqual(ctx.remembered, [query])
        self.assertIs(ctx.diagnostics[-1]["cleanup_error"], warning)

    async def test_empty_batch_needs_no_metadata_or_callback(self):
        ctx = Context(())
        with patch.object(data, "project_page") as native:
            reports = await StateManagementAPI(ctx).incorporation_many(())
        self.assertEqual(reports, ())
        self.assertEqual(ctx.types, [])
        native.assert_not_called()

    async def test_bad_late_id_is_rejected_before_every_native_query(self):
        ctx = Context(())
        api = StateManagementAPI(ctx)
        with patch.object(data, "project_page") as native:
            for value in (True, -1, 2**32, "7", None):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    await api.incorporation_many((7, value))
        native.assert_not_called()
        self.assertEqual(ctx.types, [])
        self.assertEqual(ctx.performs, [])

    async def test_repeated_explicit_ids_keep_separate_input_rows(self):
        ctx = Context((page_for((7, 7)),))
        with patch.object(data, "project_page", return_value=N(cleanup_error=None)):
            reports = await StateManagementAPI(ctx).incorporation_many((7, 7))
        self.assertEqual(len(reports), 2)
        self.assertEqual([r.page_row for r in reports], [0, 1])
        self.assertEqual([r.values["id"] for r in reports], [7, 7])


class CommandOwner:
    def __init__(self, validation, submission, *, world=91, unknown=False):
        self.world, self.command = world, 0x400
        self.pending = None
        self.attempt = None
        self.validation, self.submission = validation, submission
        self.unknown = unknown
        self.validations = self.submissions = self.destructions = 0

    def validate(self, *, capacity):
        self.validations += 1
        self.pending = "validate"
        return N(result=self.validation, stage="validate")

    def submit(self, *, flags):
        self.submissions += 1
        self.pending = "submit"
        return N(result=self.submission, stage="submit")

    def destroy(self):
        self.destructions += 1
        self.command = 0
        return N(result=N(error=0, command=0), stage="destroy")

    def complete(self, query):
        if query.stage == "submit":
            self.attempt = N(outcome="unknown" if self.unknown else "accepted")
            if not self.unknown:
                self.command = 0
                self.pending = None
        else:
            self.pending = None


class CommandContext(Context):
    def __init__(self):
        super().__init__(())
        self.world = 0  # Metadata context can legitimately have no cached world.
        self.adapters["commands"] = 0x5678

    async def query(self, label, factory, *, owner=None):
        self.queries.append(label)
        query = factory()
        if isinstance(query.result, BaseException):
            raise query.result
        if owner is not None:
            owner.complete(query)
        return query.result


def command_result(*, error=0, validated=True, accepted=True, reason=""):
    return N(error=error, validated=validated, accepted=accepted, reason=reason,
             cloned_command=0x400, command=0x400, world=91)


class StateIncorporationActionTests(IsolatedAsyncioTestCase):
    def prepare(self, *, validation=None, submission=None, unknown=False,
                source_error=0, clone_error=0, clone_pointer=0x400, release_error=0):
        from pdx_runtime import v3_commands as commands
        ctx = CommandContext()
        validation = command_result() if validation is None else validation
        submission = command_result() if submission is None else submission
        owner = CommandOwner(validation, submission, unknown=unknown)
        carrier = data.NativeDataCommand(0x100, 0x200, 0x118, 101, "CDataCommand", 91)
        source = N(action=data.Action.EVALUATE, error=source_error, value_handle=101, value=carrier)
        extracted = N(action=data.Action.COMMAND_VALUE, error=0, value_handle=101, value=carrier)
        clone = command_result(error=clone_error)
        clone.cloned_command = clone_pointer
        release = N(action=data.Action.RELEASE, error=release_error,
                    value_handle=101 if release_error else 0)
        self.addCleanup(patch.stopall)
        evaluate = patch.object(data, "evaluate", return_value=N(result=source)).start()
        extract = patch.object(data, "command_value", return_value=N(result=extracted)).start()
        clone_call = patch.object(commands, "request", return_value=N(result=clone)).start()
        release_call = patch.object(data, "release", return_value=N(result=release)).start()
        adopted = patch.object(commands.NativeCommand, "from_result", return_value=owner).start()
        return N(ctx=ctx, owner=owner, source=source, extracted=extracted, clone=clone,
                 release=release, evaluate=evaluate, extract=extract, clone_call=clone_call,
                 release_call=release_call, adopted=adopted, validation=validation, submission=submission)

    async def test_original_producer_clone_validate_submit_once_without_serialization(self):
        f = self.prepare()
        result = await StateManagementAPI(f.ctx).incorporate(7)
        f.evaluate.assert_called_once_with(f.ctx.native, "State.IncorporateState",
            (data.ObjectBinding.state(34, 7),), output_bytes=65536,
            adapter=0x1234, process=f.ctx.process)
        self.assertEqual(f.clone_call.call_args.kwargs["world"], 91)
        self.assertEqual(f.clone_call.call_args.kwargs["command"], 0x100)
        self.assertEqual((f.owner.validations, f.owner.submissions), (1, 1))
        self.assertEqual(f.owner.destructions, 0)
        self.assertEqual((result["state_id"], result["world"]), (7, 91))
        self.assertIs(result["validation"], f.validation)
        self.assertIs(result["native_result"], f.submission)
        self.assertTrue(result["accepted"])
        f.release_call.assert_called_once_with(f.ctx.native, 101,
            adapter=0x1234, process=f.ctx.process)
        self.assertFalse(any("serialize" in label for label in f.ctx.queries))
        self.assertTrue(all(label.startswith("state_management.") for label in f.ctx.queries))

    async def test_validation_only_has_no_submit_and_destroys_generated_clone(self):
        f = self.prepare()
        result = await StateManagementAPI(f.ctx).incorporate(7, validate_only=True)
        self.assertEqual(result["status"], "available")
        self.assertIsNone(result["accepted"])
        self.assertEqual((f.owner.validations, f.owner.submissions, f.owner.destructions), (1, 0, 1))
        f.release_call.assert_called_once()

    async def test_native_refusal_reason_and_original_validation_are_returned(self):
        refusal = command_result(validated=False, reason="native condition")
        f = self.prepare(validation=refusal)
        result = await StateManagementAPI(f.ctx).incorporate(7)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "native condition")
        self.assertIs(result["validation"], refusal)
        self.assertEqual(f.owner.submissions, 0)
        self.assertEqual(f.owner.destructions, 1)

    async def test_validation_native_error_preserves_raw_and_does_not_submit(self):
        failure = command_result(error=5, validated=False, reason="")
        f = self.prepare(validation=failure)
        result = await StateManagementAPI(f.ctx).incorporate(7)
        self.assertEqual(result["native_error"], 5)
        self.assertIs(result["native_result"], failure)
        self.assertEqual(f.owner.submissions, 0)
        self.assertIsNone(result["accepted"])

    async def test_known_native_acceptance_survives_unknown_owned_command_state(self):
        f = self.prepare(unknown=True)
        result = await StateManagementAPI(f.ctx).incorporate(7)
        self.assertTrue(result["accepted"])
        self.assertIs(result["native_result"], f.submission)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual((f.owner.validations, f.owner.submissions, f.owner.destructions), (1, 1, 0))
        f.release_call.assert_not_called()

    async def test_cleanup_error_keeps_the_original_successful_submission(self):
        f = self.prepare(release_error=5)
        result = await StateManagementAPI(f.ctx).incorporate(7)
        self.assertTrue(result["accepted"])
        self.assertIs(result["native_result"], f.submission)
        self.assertIs(result["cleanup"]["native_result"], f.release)
        self.assertEqual(result["cleanup"]["status"], "native_error_retained")
        f.release_call.assert_called_once()

    async def test_producer_partial_error_retains_raw_source_and_releases_its_real_any(self):
        f = self.prepare(source_error=234)
        result = await StateManagementAPI(f.ctx).incorporate(7)
        self.assertIs(result["native_result"], f.source)
        self.assertEqual(result["native_error"], 234)
        f.clone_call.assert_not_called()
        self.assertEqual(f.owner.submissions, 0)
        f.release_call.assert_called_once()

    async def test_failed_clone_with_real_pointer_is_retained_not_destroyed_or_replayed(self):
        f = self.prepare(clone_error=5)
        result = await StateManagementAPI(f.ctx).incorporate(7)
        self.assertIs(result["native_result"], f.clone)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(f.owner.validations, 0)
        f.release_call.assert_not_called()
        f.adopted.assert_not_called()
        f.clone_call.assert_called_once()

    async def test_unknown_submission_raises_original_exception_without_cleanup(self):
        failure = RuntimeError("original query copy unavailable")
        failure.query = object()
        f = self.prepare(submission=failure)
        with self.assertRaises(RuntimeError) as caught:
            await StateManagementAPI(f.ctx).incorporate(7)
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.state_management_result["validation"], f.validation)
        self.assertEqual(f.owner.submissions, 1)
        self.assertEqual(f.owner.destructions, 0)
        f.release_call.assert_not_called()

    async def test_invalid_action_inputs_do_not_start_query_or_resolve_metadata(self):
        f = self.prepare()
        api = StateManagementAPI(f.ctx)
        with self.assertRaises(ValueError):
            await api.incorporate(-1)
        with self.assertRaises(TypeError):
            await api.incorporate(7, validate_only=1)
        self.assertEqual(f.ctx.types, [])
        f.evaluate.assert_not_called()

    async def test_unknown_scope_survives_diagnostic_eviction_in_shared_context_storage(self):
        from pdx_runtime.query_executor import Diagnostics
        f = self.prepare(unknown=True)
        f.ctx.diagnostics = Diagnostics()
        result = await StateManagementAPI(f.ctx).incorporate(7)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(len([entry for entry in f.ctx.owner_pool.snapshot() if isinstance(entry, _Run)]), 1)
        original = next(entry for entry in f.ctx.owner_pool.snapshot() if isinstance(entry, _Run))
        for index in range(100):
            f.ctx.diagnostics.append({"later": index})
        self.assertEqual([entry for entry in f.ctx.owner_pool.snapshot() if isinstance(entry, _Run)], [original])
        self.assertNotIn(original, f.ctx.diagnostics["operations"])
        self.assertEqual(original.values, [101])
        self.assertIs(original.owners[0], f.owner)
        f.release_call.assert_not_called()



class StateBureaucracyAvailabilityTests(IsolatedAsyncioTestCase):
    async def test_owner_bureaucracy_balance_uses_one_proven_promoted_page(self):
        negative = (-1234567) & 0xffffffffffffffff
        page = page_for((7,), overrides={"owner_bureaucracy_balance": {"raw_bits": negative}})
        ctx = Context((page,))
        with patch.object(data, "project_page", return_value=N(cleanup_error=None)) as project:
            result = await StateManagementAPI(ctx).incorporation(7)
        self.assertEqual(len(ctx.queries), 1)
        self.assertIn("State.GetOwner.GetPowerDelta('bureaucracy')", project.call_args.args[1])
        self.assertEqual(result.values["owner_bureaucracy_balance"], data.FixedPoint(negative))
        self.assertIsInstance(result.values["owner_bureaucracy_balance"], data.FixedPoint)
        self.assertEqual(result.values["owner_bureaucracy_balance"].signed, -1234567)
        self.assertTrue(result.complete)
        self.assertNotIn("affordable", result.values)
        self.assertNotIn("can_incorporate", result.values)

    async def test_budget_field_error_keeps_its_native_zero_and_other_state_fields(self):
        page = page_for((7,), overrides={"owner_bureaucracy_balance": {"error": 5}})
        ctx = Context((page,))
        with patch.object(data, "project_page", return_value=N(cleanup_error=None)):
            result = await StateManagementAPI(ctx).incorporation(7)
        self.assertFalse(result.complete)
        self.assertEqual(result.values["owner_bureaucracy_balance"], data.FixedPoint(0))
        self.assertEqual(result.cells["owner_bureaucracy_balance"].error, 5)
        self.assertEqual(result.values["id"], 7)
