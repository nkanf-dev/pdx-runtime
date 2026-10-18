"""CPU-only tests for held unit types and real regular recruitment producer."""
from __future__ import annotations

from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_commands as commands, v3_data as data, v3_objects as objects
from pdx_runtime.v3_game.military import recruitment as warrecruit
from pdx_runtime.v3_game._choices import OwnedChoices
from tests.v3_game.test_v3_game_warcareer import Context, API, Run, native_result


class RecruitRun(Run):
    def __init__(self, api, handles=()):
        super().__init__(api, handles)
        self.projection = None
        self.adapters["objects"] = "objects"
    async def type_id(self, name):
        return {"CombatUnitGroup": 13, "CombatUnitType": 14,
                "Building": 15, "State": 16}[name]
    async def capture(self, label, factory, **kwargs):
        if label == "recruit.buildings.project":
            self.operations.append((label, self.projection))
            return self.projection
        value = factory(); self.operations.append((label, value)); return value


def type_choice(api, *, mode="regular", handle=41):
    run = RecruitRun(api, (handle,))
    result = native_result("CombatUnitType", 14, handle)
    binding = data.ObjectBinding(14, handle, retained=True)
    catalog = OwnedChoices(run, metadata={"source_kind": "recruit_unit_types",
        "formation_id": 8, "mode": mode, "result": {}})
    choice = catalog.add(result, "CombatUnitType", binding,
        {"name": "Line Infantry", "mode": mode, "complete": True})
    return run, catalog, choice, binding


def cell(row, field, kind, value, *, error=0):
    raw = value.encode() if isinstance(value, str) else b""
    return NS(row=row, field=field, kind=kind, value=value, snapshot=raw,
              total=len(raw), complete=True, error=error)


class RecruitTests(unittest.IsolatedAsyncioTestCase):
    async def test_type_catalog_holds_real_type_and_only_reads_pure_predicate(self):
        ctx = Context(); ctx.adapters["objects"] = "objects"; api = API(ctx)
        run = RecruitRun(api, (51, 52))
        group_result = native_result("CombatUnitGroup", 13, 51)
        type_result = native_result("CombatUnitType", 14, 52)
        group = data.ObjectBinding(13, 51, retained=True)
        unit_type = data.ObjectBinding(14, 52, retained=True)
        run.field_values.update({
            ("CombatUnitGroup.GetIDString", (group,)): ("infantry", None),
            ("CombatUnitType.GetNameNoFormatting", (unit_type,)): ("Line Infantry", None),
            ("MilitaryFormation.CanRecruitRegularUnit(CombatUnitType.Self)",
             ("formation", unit_type)): (True, None),
        })
        async def elements(current, catalog, expression, bindings):
            if expression == "MilitaryFormation.GetEligibleGroups": yield 0, group_result
            else: yield 0, type_result
        async def formation(current, wanted, **kwargs): return "army", "formation"
        with patch.object(warrecruit, "WarfareRead", return_value=run),                 patch.multiple(warrecruit, _choice_elements=elements, _formation=formation):
            catalog = await warrecruit.combat_unit_type_choices(api, 8, perform=False)
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0].native_result.value_handle, 52)
        self.assertTrue(catalog[0]["available"])
        self.assertFalse(any("Command" in call or "OnClick" in call for call in run.field_calls))
        await catalog.close()

    async def test_regular_options_copy_full_building_ids_without_validating_commands(self):
        ctx, api = Context(), None
        ctx.adapters["objects"] = "objects"
        api = API(ctx)
        _, catalog, choice, _ = type_choice(api)
        run = RecruitRun(api)
        run.projection = NS(error=0, count_valid=True, fields_count=7, row_count=1, cells=(
            cell(0, 0, data.Kind.BOOL, True),
            cell(0, 1, data.Kind.STRING, "#bold Barracks#!"),
            cell(0, 2, data.Kind.STRING, "Punjab"),
            cell(0, 3, data.Kind.STRING, "Barracks"),
            cell(0, 4, data.Kind.I32, 10),
            cell(0, 5, data.Kind.I32, 3),
            cell(0, 6, data.Kind.STRING, "Queue one battalion"),
        ))
        states = [NS(resolved_id=5, owner_id=42, error=0),
                  NS(resolved_id=6, owner_id=42, error=0)]
        buildings = [NS(resolved_id=101, state_id=5, levels=7, error=0),
                     NS(resolved_id=102, state_id=6, levels=4, error=0)]
        async def records(current, domain, *, report):
            return states if domain == objects.Domain.STATE else buildings
        async def formation(current, wanted, **kwargs): return "army", "formation"
        with patch.object(warrecruit, "WarfareRead", return_value=run),                 patch.multiple(warrecruit, _object_records=records, _formation=formation):
            result = await warrecruit.regular_recruitment_options(api, choice,
                state_id=5, perform=False)
        self.assertEqual(ctx.perform_calls, [])
        self.assertEqual([row["building_id"] for row in result["options"]], [101])
        self.assertEqual(result["options"][0]["state_id"], 5)
        self.assertFalse(result["options"][0]["native_eligibility_checked"])
        self.assertFalse(any("validate" in name for name, _ in run.operations))
        await catalog.close()

    async def test_regular_recruit_uses_complete_building_binding_and_one_producer(self):
        ctx = Context(); ctx.adapters["objects"] = "objects"; api = API(ctx)
        _, catalog, choice, type_binding = type_choice(api)
        run = RecruitRun(api)
        expected = iter(((2, None), (3, None)))
        produced, validations = [], []
        async def formation(current, wanted, **kwargs): return "army", "formation"
        async def count(*args): return next(expected)
        async def produced_owner(current, expression, bindings):
            produced.append((expression, bindings)); return "owner"
        async def validate(current, owner, result, *, validate_only=False):
            validations.append(validate_only); result.update(status="accepted", accepted=True); return True
        with patch.object(warrecruit, "WarfareRead", return_value=run),                 patch.multiple(warrecruit, _formation=formation, _expected_regular=count,
                    _produced_owner=produced_owner, _validate_submit=validate):
            result = await warrecruit.recruit_regular_unit(api, choice, 101, perform=False)
        self.assertEqual(ctx.perform_calls, [])
        self.assertEqual(validations, [False])
        self.assertEqual(len(produced), 1)
        self.assertEqual(produced[0][0],
            "Building.QueueMilitaryConstruction(MilitaryFormation.Self, CombatUnitType.Self)")
        building, formation_binding, selected_type = produced[0][1]
        self.assertEqual(building.object, 101)
        self.assertEqual(building.domain, "Building")
        self.assertEqual(formation_binding, "formation")
        self.assertIs(selected_type, type_binding)
        self.assertEqual(result["expected_regular_before"], 2)
        self.assertEqual(result["expected_regular_after"], 3)
        self.assertTrue(result["queue_submission_accepted"])
        self.assertTrue(result["construction_queued_observed"])
        await catalog.close()

    async def test_building_id_requires_exact_uint32_not_bool(self):
        ctx = Context(); api = API(ctx)
        _, catalog, choice, _ = type_choice(api)
        for bad in (True, -1, 0xffffffff, 1.5, "101"):
            with self.assertRaises(ValueError):
                await warrecruit.recruit_regular_unit(api, choice, bad, perform=False)
        await catalog.close()

    async def test_conscript_callback_is_truthfully_only_map_opener(self):
        ctx = Context(); api = API(ctx)
        _, catalog, choice, type_binding = type_choice(api, mode="conscript")
        captured = []
        states = iter(({"active": False}, {"active": True}))
        async def map_state(*args): return next(states)
        async def formation(run, wanted, **kwargs): return "army", "formation"
        async def selected(api_arg, label, expression, bindings, *, condition, namespace, perform):
            run = RecruitRun(api_arg)
            actual = await bindings(run)
            run.status = "complete"
            captured.append((expression, condition, perform, actual))
            return {"status": "invoked", "invoked": True, "accepted": True}
        with patch.object(warrecruit, "_map_interaction_active", map_state),                 patch.object(warrecruit, "_formation", formation),                 patch("pdx_runtime.v3_game._political_actions.selected_property_action", selected):
            result = await warrecruit.open_conscript_recruitment_map(api, choice, perform=False)
        self.assertEqual(captured[0][0],
            "MilitaryFormation.OnClickRecruitConscript(CombatUnitType.Self)")
        self.assertEqual(captured[0][1],
            "MilitaryFormation.CanRecruitConscriptUnit(CombatUnitType.Self)")
        self.assertFalse(captured[0][2])
        self.assertEqual(captured[0][3], ("formation", type_binding))
        self.assertTrue(result["invoked"])
        self.assertFalse(result["recruitment_completed"])
        self.assertIn("MapListOption.OnClick is not invoked", result["completion_scope"])
        await catalog.close()

    async def test_options_keep_unknown_capability_and_partial_text_with_native_pages(self):
        ctx = Context(); ctx.adapters["objects"] = "objects"; api = API(ctx)
        _, catalog, choice, _ = type_choice(api)
        run = RecruitRun(api)
        partial_name = cell(0, 1, data.Kind.STRING, "#bold Barr", error=122)
        partial_name.complete, partial_name.total = False, 40
        page = run.projection = NS(error=0, count_valid=True, fields_count=7, row_count=2, cells=(
            cell(0, 0, data.Kind.BOOL, True, error=21), partial_name,
            cell(0, 2, data.Kind.STRING, "Punjab"),
            cell(0, 3, data.Kind.STRING, "Barracks"),
            cell(0, 4, data.Kind.I32, 10), cell(0, 5, data.Kind.I32, 3),
            cell(0, 6, data.Kind.STRING, "Queue one battalion"),
            cell(1, 0, data.Kind.EMPTY, None, error=1168),
            cell(1, 1, data.Kind.STRING, "Unknown building type"),
        ))
        async def records(current, domain, *, report):
            if domain == objects.Domain.STATE:
                return [NS(resolved_id=5, owner_id=42)]
            return [NS(resolved_id=101, state_id=5, levels=7),
                    NS(resolved_id=102, state_id=5, levels=4)]
        async def formation(*args): return "army", "formation"
        with patch.object(warrecruit, "WarfareRead", return_value=run), \
                patch.multiple(warrecruit, _object_records=records, _formation=formation):
            result = await warrecruit.regular_recruitment_options(api, choice, perform=False)
        self.assertEqual([row["building_id"] for row in result["options"]], [101, 102])
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["complete"])
        self.assertEqual(result["options"][0]["name_raw"], "#bold Barr")
        self.assertIn("incomplete", result["options"][0]["reasons"]["name_raw"])
        self.assertIn("recruits_combat_units", result["options"][1]["reasons"])
        self.assertIs(result["native_pages"][0], page)
        self.assertIs(result["native_results"][0][1], page)
        await catalog.close()

    async def test_native_object_page_error_keeps_actual_records_and_original_page(self):
        ctx = Context(); ctx.adapters["objects"] = "objects"; api = API(ctx)
        run = RecruitRun(api)
        good = NS(resolved_id=101, error=0)
        bad = NS(resolved_id=102, error=21)
        page = NS(error=122, records=(good, bad), truncated=True, next_index=1024)
        report = {"reasons": []}
        with patch.object(objects, "enumerate_objects", return_value=page) as enumerate_objects:
            rows = await warrecruit._object_records(run, objects.Domain.BUILDING, report=report)
        self.assertEqual(rows, [good])
        self.assertEqual(enumerate_objects.call_count, 1)
        self.assertIs(report["native_object_pages"][0], page)
        self.assertIs(report["reasons"][0]["native_record"], bad)
        self.assertIs(report["reasons"][1]["native_result"], page)

    async def test_native_object_bad_pagination_stops_without_retry_or_silent_success(self):
        ctx = Context(); ctx.adapters["objects"] = "objects"; api = API(ctx)
        run = RecruitRun(api)
        good = NS(resolved_id=101, error=0)
        page = NS(error=0, records=(good,), truncated=True, next_index=0)
        report = {"reasons": []}
        with patch.object(objects, "enumerate_objects", return_value=page) as enumerate_objects:
            rows = await warrecruit._object_records(run, objects.Domain.BUILDING, report=report)
        self.assertEqual(rows, [good])
        self.assertEqual(enumerate_objects.call_count, 1)
        self.assertIn("did not advance", report["reasons"][0]["reason"])

    async def test_native_regular_recruit_pipeline_one_producer_clone_validate_submit(self):
        for validate_only, valid in ((False, True), (True, True), (False, False)):
            with self.subTest(validate_only=validate_only, valid=valid):
                ctx = Context(); api = API(ctx)
                _, catalog, choice, binding = type_choice(api)
                handle = 71
                carrier = data.NativeDataCommand(0x4000, 0x5000, 0x4018, handle,
                    ".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@", 77)
                produced = NS(action=data.Action.EVALUATE, error=0, value_handle=handle,
                              value=carrier)
                carried = NS(action=data.Action.COMMAND_VALUE, error=0,
                             value_handle=handle, value=carrier)
                clone = NS(action=commands.Action.CLONE, error=0, command=0,
                           cloned_command=0x9000, type_token=901, world=77)
                owner = NS(command=0x9000, world=77, type_token=901, pending=None,
                           attempt=None, calls=[])
                validation = NS(error=0, validated=int(valid), reason="", command=0x9000)
                submission = NS(error=0, accepted=1, command=0)
                def validate(capacity):
                    owner.calls.append(("validate", capacity))
                    owner.attempt = NS(outcome="owned")
                    return validation
                def submit(flags):
                    owner.calls.append(("submit", flags))
                    owner.command, owner.attempt = 0, NS(outcome="consumed")
                    return submission
                def destroy():
                    owner.calls.append(("destroy", None))
                    owner.command, owner.attempt = 0, NS(outcome="consumed")
                    return NS(error=0, command=0)
                owner.validate, owner.submit, owner.destroy = validate, submit, destroy
                def data_query(result):
                    query = object.__new__(data.DataQuery)
                    query.adapter, query._cpu_result = "data", result
                    return query
                async def formation(*args): return "army", "formation"
                expected = iter(((2, None), (3, None)))
                async def count(*args): return next(expected)
                released = NS(action=data.Action.RELEASE, error=0, value_handle=0)
                with patch.multiple(warrecruit, _formation=formation, _expected_regular=count), \
                        patch.object(warrecruit, "_object_records", side_effect=AssertionError("catalog gate forbidden")), \
                        patch.object(data, "evaluate", return_value=data_query(produced)) as evaluate, \
                        patch.object(data, "command_value", return_value=data_query(carried)) as command_value, \
                        patch.object(commands, "request", return_value=clone) as request, \
                        patch.object(commands.NativeCommand, "from_result", return_value=owner), \
                        patch.object(data, "release", return_value=data_query(released)) as release, \
                        patch.object(commands, "decode_command", side_effect=AssertionError("serialize forbidden")):
                    result = await warrecruit.recruit_regular_unit(api, choice, 0,
                        validate_only=validate_only, perform=False)
                self.assertEqual(evaluate.call_count, 1)
                self.assertEqual(evaluate.call_args.args[1],
                    "Building.QueueMilitaryConstruction(MilitaryFormation.Self, CombatUnitType.Self)")
                building, formation_binding, selected_type = evaluate.call_args.args[2]
                self.assertEqual((building.domain, building.object), ("Building", 0))
                self.assertEqual(formation_binding, "formation")
                self.assertIs(selected_type, binding)
                self.assertEqual(command_value.call_count, 1)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(request.call_args.args[1], commands.Action.CLONE)
                self.assertEqual(request.call_args.kwargs["world"], 77)
                self.assertEqual(sum(name == "validate" for name, _ in owner.calls), 1)
                submitted = valid and not validate_only
                self.assertEqual(sum(name == "submit" for name, _ in owner.calls), int(submitted))
                self.assertEqual(sum(name == "destroy" for name, _ in owner.calls), int(not submitted))
                self.assertEqual(release.call_count, 1)
                self.assertEqual(release.call_args.args[1], handle)
                if submitted:
                    self.assertIs(result["native_result"], submission)
                    self.assertTrue(result["queue_submission_accepted"])
                    self.assertTrue(result["construction_queued_observed"])
                else:
                    self.assertIsNone(result["queue_submission_accepted"])
                    self.assertIsNone(result["construction_queued_observed"])
                self.assertEqual(ctx.perform_calls, [])
                self.assertEqual(catalog.active_leases, 0)
                self.assertEqual(ctx.owner_pool.snapshot(), ())
                await catalog.close()

    async def test_regular_readback_failure_preserves_acceptance_without_replay(self):
        ctx = Context(); api = API(ctx)
        _, catalog, choice, _ = type_choice(api)
        run = RecruitRun(api)
        counts = []
        async def count(*args):
            counts.append(None)
            if len(counts) == 2: raise RuntimeError("readback failed")
            return 2, None
        async def formation(*args): return "army", "formation"
        async def produced_owner(*args): return "owner"
        async def validate(current, owner, result, *, validate_only=False):
            result.update(status="accepted", accepted=True, native_error=0)
            return True
        with patch.object(warrecruit, "WarfareRead", return_value=run), \
                patch.multiple(warrecruit, _formation=formation, _expected_regular=count,
                    _produced_owner=produced_owner, _validate_submit=validate):
            result = await warrecruit.recruit_regular_unit(api, choice, 101, perform=False)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["queue_submission_accepted"])
        self.assertIsNone(result["construction_queued_observed"])
        self.assertIn("readback failed", result["readback_error"])
        self.assertEqual(run.status, "retained_pending_or_unknown")
        self.assertEqual(catalog.active_leases, 1)
        run.status = "complete"
        await catalog.close()

    async def test_shared_conscript_opener_is_one_callback_and_not_final_map_click(self):
        ctx = Context(); api = API(ctx)
        _, catalog, choice, binding = type_choice(api, mode="conscript")
        condition = NS(error=0, kind=data.Kind.BOOL, value=True,
                       action=data.Action.EVALUATE, value_handle=0)
        invocation = NS(error=0, invoked=1, accepted=1,
                        result=NS(action=data.Action.INVOKE_ACTION, value_handle=0))
        async def map_state(*args): return {"active": True}
        async def formation(*args): return "army", "formation"
        with patch.multiple(warrecruit, _map_interaction_active=map_state, _formation=formation), \
                patch.object(data, "evaluate", return_value=condition) as evaluate, \
                patch.object(data, "invoke_property_action", return_value=invocation) as invoke:
            result = await warrecruit.open_conscript_recruitment_map(api, choice, perform=False)
        self.assertEqual((evaluate.call_count, invoke.call_count), (1, 1))
        self.assertEqual(evaluate.call_args.args[1],
            "MilitaryFormation.CanRecruitConscriptUnit(CombatUnitType.Self)")
        self.assertEqual(invoke.call_args.args[1],
            "MilitaryFormation.OnClickRecruitConscript(CombatUnitType.Self)")
        self.assertEqual(invoke.call_args.args[2], ("formation", binding))
        self.assertTrue(result["accepted"])
        self.assertFalse(result["recruitment_completed"])
        self.assertEqual(catalog.active_leases, 0)
        await catalog.close()
