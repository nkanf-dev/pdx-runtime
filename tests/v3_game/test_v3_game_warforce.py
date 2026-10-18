"""CPU-only tests for warforce owned sources and one-shot producers."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game.military import forces as warforce


class API:
    def __init__(self, ctx=None):
        self._ctx = ctx

    def _native_full_id(self, value):
        return value if type(value) is int and 0 <= value <= 0xFFFFFFFF else None


class Run:
    def __init__(self):
        self.api = API()
        self.actor = 42
        self.providers = []
        self.field_calls = []
        self.element_calls = []
        self.elements_by_expression = {}
        self.values = {}
        self.expressions = []

    async def country(self):
        return "country"

    async def provider(self, receiver, method, arguments=0):
        self.providers.append((receiver, method, arguments))
        return object()

    async def functions(self, receiver):
        if receiver == "Character":
            return {"GetFullNameNoFormatting": object()}
        return {}

    async def elements(self, expression, bindings, target):
        self.element_calls.append((expression, bindings, target))
        for index, binding in enumerate(self.elements_by_expression.get(expression, ())):
            yield index, object(), binding

    async def field(self, receiver, method, bindings, kinds, *, arguments=0, expression=None):
        key = expression or receiver + "." + method
        self.field_calls.append((key, bindings, arguments))
        value = self.values.get((key, bindings), self.values.get(key, (None, "unavailable: " + key)))
        return value() if callable(value) else value

    async def evaluate(self, expression, bindings):
        self.expressions.append((expression, bindings))
        value, reason = self.values.get((expression, bindings), self.values.get(expression, (None, None)))
        if reason:
            raise AssertionError(reason)
        kind = data.Kind.BOOL if type(value) is bool else data.Kind.STRING
        raw = value.encode() if isinstance(value, str) else b""
        return SimpleNamespace(kind=kind, value=value, snapshot_bytes=len(raw), total=len(raw))


class ChoiceContext:
    def __init__(self):
        self.native, self.process = object(), object()
        self.adapters = {"data": "fake-data", "commands": "fake-commands"}
        self.perform_calls = []

    async def perform(self, label, operation):
        self.perform_calls.append(label)
        return await operation()


class ChoiceRun(Run):
    def __init__(self, ctx, api, handles=()):
        super().__init__()
        self.ctx, self.api = ctx, api
        self.native, self.process = ctx.native, ctx.process
        self.adapters = dict(ctx.adapters)
        self.value_adapters = {handle: self.adapters["data"] for handle in handles}
        self.field_values = {}
        self.values = list(handles)
        self.operations = []
        self.status = "working"
        self.retain_unknown = False

    async def type_id(self, name):
        return {"MilitaryFormationUnitEntry": 71, "CombatUnit": 72,
                "MobilizationOption": 73}[name]

    async def field(self, receiver, method, bindings, kinds, *, arguments=0, expression=None):
        key = expression or receiver + "." + method
        self.field_calls.append((key, bindings, arguments))
        value = self.field_values.get((key, bindings),
            self.field_values.get(key, (None, "unavailable: " + key)))
        return value() if callable(value) else value

    def wrapper(self, result, named_type):
        expected = (".?AU?$SAnyType@V?$CPdxPtr@USDataCombatUnitEntry@@@@@PdxAnyImpl@@"
                    if named_type == "MilitaryFormationUnitEntry" else
                    ".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@")
        if (not isinstance(result.value, data.NativeObject)
                or result.value.native_type_name != expected
                or not result.value.pointer or result.value_handle not in self.values):
            raise RuntimeError("wrapper ownership is not established")
        return result.value

    async def finish(self, result):
        if self.retain_unknown:
            self.status = "retained_pending_or_unknown"
            result["cleanup"] = {"status": self.status}
        else:
            self.values.clear()
            self.status = "complete"
        return result


def owned_result(named_type, type_id, handle, *, strong=True):
    if named_type == "MilitaryFormationUnitEntry":
        rtti = (".?AU?$SAnyType@V?$CPdxPtr@USDataCombatUnitEntry@@@@@PdxAnyImpl@@" if strong
                else ".?AU?$SAnyType@PEAUSDataCombatUnitEntry@@@PdxAnyImpl@@")
    else:
        rtti = (".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@" if strong
                else ".?AU?$SAnyType@PEAVCData" + named_type + "@@@PdxAnyImpl@@")
    value = data.NativeObject(0x1000 + handle, type_id, handle, rtti)
    return SimpleNamespace(value=value, value_handle=handle, error=0,
                           type_id=type_id, native_type_name=rtti)


class WarforceTests(unittest.IsolatedAsyncioTestCase):
    def immediate(self, run):
        async def owned_read(api, label, operation):
            run.api = api
            return await operation(run)
        return owned_read

    async def test_disband_uses_owned_formation_producer_once_and_reads_post_state(self):
        run = Run()
        formations = iter((("army", "army-17"), (None, None)))
        produced = []
        async def formation(current, wanted, **kwargs):
            self.assertEqual(wanted, 17)
            return next(formations)
        async def produced_owner(current, expression, bindings):
            produced.append((expression, bindings))
            return "owner"
        async def validate(current, owner, result, *, validate_only=False):
            self.assertEqual(owner, "owner")
            result.update(status="accepted", accepted=True)
            return True
        with patch.multiple(warforce, owned_read=self.immediate(run), _formation=formation,
                _produced_owner=produced_owner, _validate_submit=validate):
            result = await warforce.formation_disband(API(), 17)
        self.assertEqual(produced, [("MilitaryFormation.Disband", ("army-17",))])
        self.assertTrue(result["post_state"]["disband_observed"])
        self.assertEqual(result["status"], "accepted")

    async def test_composition_keeps_partial_raw_text_and_per_field_errors(self):
        run = Run()
        run.elements_by_expression.update({
            "MilitaryFormation.GetCombatUnitEntries": ["entry"],
            "MilitaryFormationUnitEntry.GetCombatUnits": ["unit"],
        })
        run.values.update({
            ("MilitaryFormationUnitEntry.GetUnitType.GetNameNoFormatting", ("entry",)):
                ("#bold Line Infantry#!", "partial native text"),
            ("MilitaryFormationUnitEntry.GetIDString", ("entry",)): ("line_infantry", None),
            ("MilitaryFormationUnitEntry.GetCurrentNumRegularUnits", ("entry",)): (3, None),
            ("MilitaryFormationUnitEntry.GetExpectedNumRegularUnits", ("entry",)): (5, None),
            ("CombatUnit.GetName", ("unit",)): ("#italic 1st Battalion#!", None),
            ("CombatUnit.GetManpowerRaw", ("unit",)): (812, None),
            ("CombatUnit.GetMoraleRaw", ("unit",)): (None, "native error 21"),
            ("CombatUnit.GetBuilding.GetID", ("unit",)): (99, None),
        })
        async def formation(current, wanted, **kwargs):
            return "army", "army"
        with patch.multiple(warforce, owned_read=self.immediate(run), _formation=formation):
            result = await warforce.formation_unit_composition(API(), 8)
        entry = result["entries"][0]
        self.assertEqual(entry["name_raw"], "#bold Line Infantry#!")
        self.assertEqual(entry["name"], "Line Infantry")
        self.assertEqual(entry["unavailable"]["name"], "partial native text")
        self.assertIn("type_tooltip_tag", entry)
        self.assertNotIn("type_key", entry)
        self.assertEqual(entry["current_regular"], 3)
        unit = entry["units"][0]
        self.assertEqual(unit["manpower"], 812)
        self.assertIsNone(unit["morale"])
        self.assertEqual(unit["unavailable"]["morale"], "native error 21")
        self.assertEqual(unit["building_id"], 99)

    async def test_unit_action_re_resolves_native_entry_id_not_previous_ordinal(self):
        run = Run()
        run.elements_by_expression["MilitaryFormation.GetCombatUnitEntries"] = ["new-first", "selected-entry"]
        run.values.update({
            ("MilitaryFormationUnitEntry.GetIDString", ("new-first",)): ("artillery", None),
            ("MilitaryFormationUnitEntry.GetIDString", ("selected-entry",)): ("line_infantry", None),
        })
        produced = []
        async def formation(current, wanted, **kwargs):
            return "army", "army"
        async def produced_owner(current, expression, bindings):
            produced.append((expression, bindings))
            return "owner"
        async def validate(current, owner, result, *, validate_only=False):
            result.update(status="available", accepted=None)
            return False
        with patch.multiple(warforce, owned_read=self.immediate(run), _formation=formation,
                _produced_owner=produced_owner, _validate_submit=validate):
            result = await warforce.formation_unit_action(API(), 8, "set_default_type",
                entry_id_string="line_infantry", validate_only=True)
        self.assertEqual(produced, [("MilitaryFormation.GetSetDefaultUnitTypeCommand("
                                     "MilitaryFormationUnitEntry.GetUnitType.Self)",
                                     ("army", "selected-entry"))])
        self.assertEqual(result["entry_id_string"], "line_infantry")
        self.assertNotIn("entry_index", result)

    async def test_recruit_options_are_reads_and_distinguish_real_queue_from_conscript_opener(self):
        run = Run()
        run.elements_by_expression.update({
            "MilitaryFormation.GetEligibleGroups": ["infantry"],
            "MilitaryFormation.GetEligibleTypesByGroup(CombatUnitGroup.Self)": ["line"],
        })
        run.values.update({
            ("CombatUnitGroup.GetIDString", ("infantry",)): ("infantry", None),
            ("CombatUnitType.GetNameNoFormatting", ("line",)): ("Line Infantry", None),
            ("MilitaryFormation.CanRecruitRegularUnit(CombatUnitType.Self)", ("army", "line")):
                (True, None),
            ("MilitaryFormation.CanRecruitConscriptUnit(CombatUnitType.Self)", ("army", "line")):
                (False, "insufficient conscription capacity"),
        })
        async def formation(current, wanted, **kwargs):
            return "army", "army"
        with patch.multiple(warforce, owned_read=self.immediate(run), _formation=formation):
            result = await warforce.formation_recruit_options(API(), 8)
        option = result["groups"][0]["unit_types"][0]
        self.assertTrue(option["can_recruit_regular"])
        self.assertFalse(option["can_recruit_conscript"])
        self.assertIn("insufficient", option["unavailable"]["can_recruit_conscript"])
        self.assertTrue(result["regular_recruit_action_available_via_owned_choice"])
        self.assertIn("Building.QueueMilitaryConstruction", result["regular_recruit_action_note"])
        self.assertTrue(result["conscript_map_opener_available_via_owned_choice"])
        self.assertFalse(result["conscript_recruit_action_available"])
        self.assertIn("owned current MapListPanel", result["conscript_recruit_action_unavailable_reason"])
        self.assertFalse(any("QueueMilitaryConstruction" in call[0] or "OnClick" in call[0]
                             for call in run.field_calls))

    async def test_commander_candidate_catalog_does_not_create_or_validate_commands(self):
        run = Run()
        run.elements_by_expression["Country.AccessGeneralsOffer"] = ["candidate"]
        run.elements_by_expression["Country.AccessAdmiralsOffer"] = []
        run.elements_by_expression["Character.AccessTraits"] = ["bold"]
        run.values.update({
            ("Character.GetID", ("candidate",)): (73, None),
            ("Character.GetFullNameNoFormatting", ("candidate",)): ("#bold Ada#!", None),
            ("Character.GetRankName", ("candidate",)): ("General", None),
            ("Character.GetCommandLimitRaw", ("candidate",)): (40, None),
            ("CharacterTrait.GetNameNoFormatting", ("bold",)): ("Offensive Planner", None),
        })
        with patch.object(warforce, "owned_read", self.immediate(run)):
            result = await warforce.commander_candidates(API(), role="general")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["id"], 73)
        self.assertEqual(candidate["name"], "Ada")
        self.assertIsNone(candidate["eligible"])
        self.assertIn("selected candidate", candidate["unavailable"]["eligible"])
        self.assertEqual(candidate["traits"][0]["name"], "Offensive Planner")
        self.assertIsNone(candidate["cost"])
        self.assertFalse(run.expressions)

    async def test_recruit_commander_materializes_only_selected_offer_command(self):
        run = Run()
        produced = []
        async def candidate(current, wanted, role=None):
            return "general", "candidate"
        async def formation(current, wanted, **kwargs):
            return "army", "army"
        async def produced_owner(current, expression, bindings):
            produced.append((expression, bindings))
            return "owner"
        async def validate(current, owner, result, *, validate_only=False):
            result.update(status="available", accepted=None)
            return False
        with patch.multiple(warforce, owned_read=self.immediate(run), _candidate=candidate,
                _formation=formation, _produced_owner=produced_owner, _validate_submit=validate):
            result = await warforce.recruit_commander(API(), 73, formation_id=8, validate_only=True)
        self.assertEqual(produced, [("Character.GetRecruitCommand(MilitaryFormation.Self)",
                                     ("candidate", "army"))])

    async def test_selected_commander_recruit_options_validate_one_candidate_once(self):
        run = Run()
        produced = []
        validations = []
        async def candidate(current, wanted, role=None):
            return "general", "candidate"
        async def produced_owner(current, expression, bindings):
            produced.append((expression, bindings))
            return "owner"
        async def validate(current, owner, result, *, validate_only=False):
            validations.append(validate_only)
            result.update(status="available", accepted=None, eligible=True)
            return False
        with patch.multiple(warforce, owned_read=self.immediate(run), _candidate=candidate,
                _produced_owner=produced_owner, _validate_submit=validate):
            result = await warforce.commander_recruit_options(API(), 73)
        self.assertEqual(produced, [("Character.GetRecruitCommandNoFormation", ("candidate",))])
        self.assertEqual(validations, [True])
        self.assertTrue(result["eligible"])

    async def test_promotion_reads_transition_and_uses_producer_once(self):
        run = Run()
        run.values.update({
            ("Character.GetRankName", ("commander",)): ("Brigadier General", None),
            ("Character.GetNextCommanderRankBenefits", ("commander",)): ("+20 command limit", None),
            ("Character.GetNextCommanderRank.GetTooltipTag", ("commander",)): ("commander_rank_2", None),
        })
        produced = []
        async def character(current, wanted):
            return "commander"
        async def produced_owner(current, expression, bindings):
            produced.append((expression, bindings))
            return "owner"
        async def validate(current, owner, result, *, validate_only=False):
            result.update(status="available", accepted=None)
            return False
        with patch.multiple(warforce, owned_read=self.immediate(run), _character=character,
                _produced_owner=produced_owner, _validate_submit=validate):
            result = await warforce.commander_promotion(API(), 9, validate_only=True)
        self.assertEqual(produced, [("Character.GetPromoteCommand", ("commander",))])
        self.assertEqual(result["before"]["current_rank"], "Brigadier General")
        self.assertEqual(result["before"]["next_rank_tooltip_tag"], "commander_rank_2")

    async def test_interactions_preserve_options_without_list_wide_validation(self):
        run = Run()
        run.elements_by_expression["Character.GetInteractions"] = ["retire"]
        run.values.update({
            ("CharacterInteraction.GetName", ("retire",)): ("Retire Commander", None),
            ("CharacterInteraction.GetTooltipTag", ("retire",)): ("retire_commander", None),
            ("CharacterInteraction.IsCommandValid(Character.Self)", ("retire", "commander")):
                (True, None),
        })
        async def character(current, wanted):
            return "commander"
        with patch.multiple(warforce, owned_read=self.immediate(run), _character=character):
            result = await warforce.commander_interactions(API(), 9)
        option = result["interactions"][0]
        self.assertEqual(option["interaction_tooltip_tag"], "retire_commander")
        self.assertNotIn("interaction_key", option)
        self.assertIsNone(option["eligible"])
        self.assertFalse(option["validity_checked"])
        self.assertTrue(option["action_available_via_owned_choice"])
        self.assertIn("invoke_commander_interaction", option["action_note"])
        self.assertFalse(any("IsCommandValid" in call[0] or "ExecuteCommand" in call[0]
                             for call in run.field_calls))


    async def test_disband_post_observation_failure_keeps_native_acceptance(self):
        run = Run()
        calls = 0
        async def formation(current, wanted, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return "army", "army"
            raise RuntimeError("post-state outcome unknown")
        async def produced_owner(current, expression, bindings):
            return "owner"
        async def validate(current, owner, result, *, validate_only=False):
            result.update(status="accepted", accepted=True, native_result="original")
            return True
        with patch.multiple(warforce, owned_read=self.immediate(run), _formation=formation,
                _produced_owner=produced_owner, _validate_submit=validate):
            result = await warforce.formation_disband(API(), 17)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["native_result"], "original")
        self.assertIn("outcome unknown", result["observation_error"])
        self.assertTrue(run.retain_unknown)

    async def test_mobilization_catalog_exposes_native_effect_and_upkeep_text(self):
        run = Run()
        run.elements_by_expression["MilitaryFormation.GetMobilizationOptions"] = ["rail"]
        run.values.update({
            ("MobilizationOption.GetNameNoFormatting", ("rail",)): ("Railways", None),
            ("MobilizationOption.GetUnitModifier.GetDesc", ("rail",)): ("+10% movement", None),
            ("MobilizationOption.GetUpkeepModifier.GetDesc", ("rail",)): ("Consumes transportation", None),
            ("MobilizationOption.IsActivated(MilitaryFormation.AccessSelf)", ("rail", "army")): (True, None),
            ("MobilizationOption.IsPossible(MilitaryFormation.AccessSelf)", ("rail", "army")): (True, None),
            ("MobilizationOption.CanBeTurnedOff(MilitaryFormation.AccessSelf)", ("rail", "army")): (False, None),
        })
        async def formation(current, wanted, **kwargs):
            return "army", "army"
        with patch.multiple(warforce, owned_read=self.immediate(run), _formation=formation):
            result = await warforce.mobilization_options(API(), 8)
        option = result["options"][0]
        self.assertEqual(option["effects"], "+10% movement")
        self.assertIn("option_tooltip_tag", option)
        self.assertNotIn("option_key", option)
        self.assertTrue(result["mutation_available_via_owned_choice"])
        self.assertEqual(option["goods_cost"], "Consumes transportation")
        self.assertTrue(option["activated"])
        self.assertFalse(option["can_be_turned_off"])

    async def test_owned_mobilization_choice_uses_same_retained_binding_once(self):
        ctx = ChoiceContext()
        api = API(ctx)
        source_run = ChoiceRun(ctx, api, handles=(30,))
        action_run = ChoiceRun(ctx, api)
        result = owned_result("MobilizationOption", 73, 30)
        binding = data.ObjectBinding(73, 30, retained=True)
        async def elements(run, catalog, expression, bindings, named_type, **kwargs):
            self.assertIs(run, source_run)
            yield 0, result, binding
        async def formation(run, wanted, **kwargs):
            return "army", "formation"
        produced, validations = [], []
        async def produced_owner(run, expression, bindings):
            produced.append((run, expression, bindings))
            return "owner"
        async def validate(run, owner, answer, *, validate_only=False):
            validations.append(validate_only)
            answer.update(status="accepted", accepted=True)
            return True
        with patch("pdx_runtime.v3_game.military._warfare.WarfareRead",
                   side_effect=[source_run, action_run]), patch.multiple(warforce,
                   _owned_choice_elements=elements, _formation=formation,
                   _produced_owner=produced_owner, _validate_submit=validate):
            catalog = await warforce.mobilization_option_choices(api, 8, perform=False)
            self.assertEqual(len(catalog), 1)
            choice = catalog[0]
            answer = await warforce.toggle_mobilization_option(api, choice, perform=False)
        self.assertEqual(ctx.perform_calls, [])
        self.assertTrue(answer["accepted"])
        self.assertEqual(validations, [False])
        self.assertEqual(len(produced), 1)
        self.assertEqual(produced[0][1], "MobilizationOption.Toggle(MilitaryFormation.AccessSelf)")
        self.assertIs(produced[0][2][0], choice._binding)
        self.assertEqual(produced[0][2][0].object, 30)
        self.assertEqual(catalog.active_leases, 0)
        await catalog.close()
        self.assertTrue(catalog.closed)

    async def test_set_mobilization_option_changes_once_to_desired_state(self):
        ctx = ChoiceContext()
        api = API(ctx)
        source_run = ChoiceRun(ctx, api, handles=(30,))
        action_run = ChoiceRun(ctx, api)
        result = owned_result("MobilizationOption", 73, 30)
        binding = data.ObjectBinding(73, 30, retained=True)
        states = iter(((False, None), (True, None)))
        action_run.field_values[("MobilizationOption.IsActivated(MilitaryFormation.AccessSelf)",
                                 (binding, "formation"))] = lambda: next(states)
        async def elements(run, catalog, expression, bindings, named_type, **kwargs):
            yield 0, result, binding
        async def formation(run, wanted, **kwargs):
            return "army", "formation"
        produced, validations = [], []
        async def produced_owner(run, expression, bindings):
            produced.append((expression, bindings))
            return "owner"
        async def validate(run, owner, answer, *, validate_only=False):
            validations.append(validate_only)
            answer.update(status="accepted", accepted=True)
            return True
        with patch("pdx_runtime.v3_game.military._warfare.WarfareRead",
                   side_effect=[source_run, action_run]), patch.multiple(warforce,
                   _owned_choice_elements=elements, _formation=formation,
                   _produced_owner=produced_owner, _validate_submit=validate):
            catalog = await warforce.mobilization_option_choices(api, 8, perform=False)
            answer = await warforce.set_mobilization_option(api, catalog[0], True,
                perform=False)
        self.assertEqual(ctx.perform_calls, [])
        self.assertEqual(validations, [False])
        self.assertEqual(len(produced), 1)
        self.assertFalse(answer["activated_before"])
        self.assertTrue(answer["activated_after"])
        self.assertEqual(answer["action"], "set_mobilization_option")
        await catalog.close()

    async def test_set_mobilization_option_unchanged_creates_no_command(self):
        ctx = ChoiceContext()
        api = API(ctx)
        source_run = ChoiceRun(ctx, api, handles=(30,))
        action_run = ChoiceRun(ctx, api)
        result = owned_result("MobilizationOption", 73, 30)
        binding = data.ObjectBinding(73, 30, retained=True)
        action_run.field_values[("MobilizationOption.IsActivated(MilitaryFormation.AccessSelf)",
                                 (binding, "formation"))] = (True, None)
        async def elements(run, catalog, expression, bindings, named_type, **kwargs):
            yield 0, result, binding
        async def formation(run, wanted, **kwargs):
            return "army", "formation"
        produced, validations = [], []
        async def produced_owner(*args):
            produced.append(args)
        async def validate(*args, **kwargs):
            validations.append((args, kwargs))
        with patch("pdx_runtime.v3_game.military._warfare.WarfareRead",
                   side_effect=[source_run, action_run]), patch.multiple(warforce,
                   _owned_choice_elements=elements, _formation=formation,
                   _produced_owner=produced_owner, _validate_submit=validate):
            catalog = await warforce.mobilization_option_choices(api, 8)
            answer = await warforce.set_mobilization_option(api, catalog[0], True)
        self.assertEqual(answer["status"], "unchanged")
        self.assertIsNone(answer["accepted"])
        self.assertTrue(answer["activated_after"])
        self.assertFalse(produced)
        self.assertFalse(validations)
        await catalog.close()

    async def test_set_mobilization_option_unknown_current_never_blind_toggles(self):
        ctx = ChoiceContext()
        api = API(ctx)
        source_run = ChoiceRun(ctx, api, handles=(30,))
        action_run = ChoiceRun(ctx, api)
        result = owned_result("MobilizationOption", 73, 30)
        binding = data.ObjectBinding(73, 30, retained=True)
        async def elements(run, catalog, expression, bindings, named_type, **kwargs):
            yield 0, result, binding
        async def formation(run, wanted, **kwargs):
            return "army", "formation"
        produced, validations = [], []
        async def produced_owner(*args):
            produced.append(args)
        async def validate(*args, **kwargs):
            validations.append((args, kwargs))
        with patch("pdx_runtime.v3_game.military._warfare.WarfareRead",
                   side_effect=[source_run, action_run]), patch.multiple(warforce,
                   _owned_choice_elements=elements, _formation=formation,
                   _produced_owner=produced_owner, _validate_submit=validate):
            catalog = await warforce.mobilization_option_choices(api, 8)
            answer = await warforce.set_mobilization_option(api, catalog[0], False)
        self.assertEqual(answer["status"], "unavailable")
        self.assertIsNone(answer["accepted"])
        self.assertFalse(produced)
        self.assertFalse(validations)
        await catalog.close()

    async def test_unknown_selected_choice_action_keeps_parent_graph_and_never_replays(self):
        from pdx_runtime.v3_game._choices import ChoicesBusyError
        ctx = ChoiceContext()
        api = API(ctx)
        source_run = ChoiceRun(ctx, api, handles=(30,))
        action_run = ChoiceRun(ctx, api)
        result = owned_result("MobilizationOption", 73, 30)
        binding = data.ObjectBinding(73, 30, retained=True)
        async def elements(run, catalog, expression, bindings, named_type, **kwargs):
            yield 0, result, binding
        async def formation(run, wanted, **kwargs):
            return "army", "formation"
        attempts = []
        async def produced_owner(run, expression, bindings):
            attempts.append((expression, bindings))
            raise RuntimeError("original producer outcome unknown")
        with patch("pdx_runtime.v3_game.military._warfare.WarfareRead",
                   side_effect=[source_run, action_run]), patch.multiple(warforce,
                   _owned_choice_elements=elements, _formation=formation,
                   _produced_owner=produced_owner):
            catalog = await warforce.mobilization_option_choices(api, 8)
            with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
                await warforce.toggle_mobilization_option(api, catalog[0])
        self.assertEqual(len(attempts), 1)
        self.assertEqual(catalog.active_leases, 1)
        with self.assertRaises(ChoicesBusyError):
            await catalog.close()
        action_run.status = "complete"
        lease, = tuple(catalog._leases)
        self.assertTrue(lease.settle())
        await catalog.close()
        self.assertTrue(catalog.closed)
        self.assertEqual(len(attempts), 1)

    async def test_owned_combat_unit_choice_disbands_exact_retained_unit(self):
        ctx = ChoiceContext()
        api = API(ctx)
        source_run = ChoiceRun(ctx, api, handles=(20, 31))
        action_run = ChoiceRun(ctx, api)
        entry_result = owned_result("MilitaryFormationUnitEntry", 71, 20)
        unit_result = owned_result("CombatUnit", 72, 31)
        entry = data.ObjectBinding(71, 20, retained=True)
        unit = data.ObjectBinding(72, 31, retained=True)
        source_run.field_values[("MilitaryFormationUnitEntry.GetIDString", (entry,))] = ("line_infantry", None)
        async def elements(run, catalog, expression, bindings, named_type, **kwargs):
            if named_type == "MilitaryFormationUnitEntry":
                yield 0, entry_result, entry
            else:
                self.assertEqual(bindings, (entry,))
                yield 4, unit_result, unit
        async def formation(run, wanted, **kwargs):
            return "army", "formation"
        produced, validations = [], []
        async def produced_owner(run, expression, bindings):
            produced.append((expression, bindings))
            return "owner"
        async def validate(run, owner, answer, *, validate_only=False):
            validations.append(validate_only)
            answer.update(status="available", accepted=None, eligible=True)
            return False
        with patch("pdx_runtime.v3_game.military._warfare.WarfareRead",
                   side_effect=[source_run, action_run]), patch.multiple(warforce,
                   _owned_choice_elements=elements, _formation=formation,
                   _produced_owner=produced_owner, _validate_submit=validate):
            catalog = await warforce.combat_unit_choices(api, 8,
                entry_id_string="line_infantry", perform=False)
            self.assertEqual(len(catalog), 1)
            choice = catalog[0]
            answer = await warforce.disband_combat_unit(api, choice,
                validate_only=True, perform=False)
        self.assertEqual(ctx.perform_calls, [])
        self.assertEqual(validations, [True])
        self.assertTrue(answer["eligible"])
        self.assertEqual(produced, [("MilitaryFormation.GetDisbandUnitCommand(CombatUnit.Self)",
                                     ("formation", unit))])
        self.assertIs(produced[0][1][1], choice._binding)
        await catalog.close()

    async def test_choice_catalog_rejects_weak_wrapper_instead_of_retagging(self):
        ctx = ChoiceContext()
        api = API(ctx)
        source_run = ChoiceRun(ctx, api, handles=(30,))
        weak = owned_result("MobilizationOption", 73, 30, strong=False)
        binding = data.ObjectBinding(73, 30, retained=True)
        async def elements(run, catalog, expression, bindings, named_type, **kwargs):
            yield 0, weak, binding
        async def formation(run, wanted, **kwargs):
            return "army", "formation"
        with patch("pdx_runtime.v3_game.military._warfare.WarfareRead", return_value=source_run),                 patch.multiple(warforce, _owned_choice_elements=elements, _formation=formation):
            with self.assertRaises(ValueError) as caught:
                await warforce.mobilization_option_choices(api, 8)
        catalog = caught.exception.choices
        self.assertEqual(len(catalog), 0)
        await catalog.close()

    async def test_mutations_reject_ordinals_and_missing_native_id_strings(self):
        for bad in (None, "", True, 7):
            with self.assertRaises(ValueError):
                await warforce.formation_unit_action(API(), 8, "set_default_type",
                    entry_id_string=bad)
        with self.assertRaises(ValueError):
            await warforce.formation_unit_action(API(), 8, "disband_unit",
                entry_id_string="line_infantry")
        self.assertTrue(hasattr(warforce, "set_mobilization_option"))
        api = API(ChoiceContext())
        with self.assertRaises(TypeError):
            await warforce.toggle_mobilization_option(api, 0)
        with self.assertRaises(TypeError):
            await warforce.set_mobilization_option(api, 0, True)
        for bad in (0, 1, None, "true"):
            with self.assertRaises(TypeError):
                await warforce.set_mobilization_option(api, object(), bad)
        with self.assertRaises(TypeError):
            await warforce.disband_combat_unit(api, "unit name")

    async def test_canonical_unit_entry_struct_alias_is_admitted_without_cdata_gate(self):
        from pdx_runtime.v3_game._choices import OwnedChoices
        from pdx_runtime.v3_game._run import Run as _Run
        ctx = ChoiceContext(); api = API(ctx)
        run = ChoiceRun(ctx, api, handles=(20,))
        catalog = OwnedChoices(run)
        result = owned_result("MilitaryFormationUnitEntry", 71, 20)
        binding = data.ObjectBinding(71, 20, retained=True)
        self.assertIs(_Run.wrapper(run, result, "MilitaryFormationUnitEntry"), result.value)
        choice = catalog.add(result, "MilitaryFormationUnitEntry", binding, {"complete": True})
        self.assertIs(choice.native_result, result)
        for spelling in (
                ".?AU?$SAnyType@V?$CPdxPtr@VCDataMilitaryFormationUnitEntry@@@@@PdxAnyImpl@@",
                ".?AU?$SAnyType@PEAUSDataCombatUnitEntry@@@PdxAnyImpl@@"):
            wrong = SimpleNamespace(value=data.NativeObject(0x1014, 71, 20, spelling),
                                    value_handle=20, error=0, type_id=71,
                                    native_type_name=spelling)
            with self.assertRaises(RuntimeError):
                _Run.wrapper(run, wrong, "MilitaryFormationUnitEntry")
            with self.assertRaises(ValueError):
                catalog.add(wrong, "MilitaryFormationUnitEntry", binding, {})
        await catalog.close()

    async def test_owned_entry_source_uses_exact_alias_and_actual_getcombatunits_expression(self):
        from pdx_runtime.v3_game._run import Run as _Run
        ctx = ChoiceContext(); api = API(ctx)
        run = ChoiceRun(ctx, api, handles=(10, 20, 30, 31))
        run.wrapper = lambda result, named_type: _Run.wrapper(run, result, named_type)
        entry_result = owned_result("MilitaryFormationUnitEntry", 71, 20)
        unit_result = owned_result("CombatUnit", 72, 31)
        entry = data.ObjectBinding(71, 20, retained=True)
        run.field_values[("MilitaryFormationUnitEntry.GetIDString", (entry,))] = ("line_infantry", None)
        expressions, lists = [], []
        async def evaluate(expression, bindings):
            expressions.append((expression, bindings))
            handle = 10 if expression == "MilitaryFormation.GetCombatUnitEntries" else 30
            return SimpleNamespace(kind=data.Kind.LIST,
                value=data.NativeList(handle, 0x1000, b"", "native-owned-list"), value_handle=handle)
        async def call(label, factory): return factory()
        def count(native, handle, **kwargs):
            lists.append(("count", handle)); return SimpleNamespace(total=1, error=0)
        def element(native, handle, index, **kwargs):
            lists.append(("element", handle, index))
            return entry_result if handle == 10 else unit_result
        run.evaluate, run.call = evaluate, call
        async def formation(current, wanted, **kwargs): return "army", "formation"
        with patch("pdx_runtime.v3_game.military._warfare.WarfareRead", return_value=run), \
                patch.object(warforce, "_formation", formation), \
                patch.multiple(data, list_count=count, list_element=element):
            catalog = await warforce.combat_unit_choices(api, 8, perform=False)
        self.assertEqual(len(catalog), 1)
        self.assertIs(catalog[0].native_result, unit_result)
        self.assertEqual(expressions, [
            ("MilitaryFormation.GetCombatUnitEntries", ("formation",)),
            ("MilitaryFormationUnitEntry.GetCombatUnits", (entry,))])
        self.assertEqual(lists, [("count", 10), ("element", 10, 0),
                                 ("count", 30), ("element", 30, 0)])
        self.assertEqual(catalog.metadata["result"]["rejected_rows"], [])
        await catalog.close()
