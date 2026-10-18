"""CPU-only tests for owned war, participant, goal, and battle reads.

No process, game, controller, DLL, or runfile is opened.  The fixture patches the
native data calls and the proven CDataWar+0x10 origin copy at the WarfareRead
boundary.
"""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_data as data, v3_objects as objects
from pdx_runtime.v3_game.military import MilitaryAPI
from pdx_runtime.v3_game.military._warfare import WarfareRead, battles, country_wars


TYPES = {
    "Country": 83,
    "War": 236,
    "bool": 5,
    "uint32": 6,
    "int32": 4,
    "int64": 115,
    "CString": 0,
    "CFixedPoint": 82,
}


def function(name, result_type, arguments=0):
    return SimpleNamespace(
        name=name,
        argument_count=arguments,
        return_type_id=result_type,
        callbacks=(123, 0, 0, 0),
    )


PROJECTION_KINDS = {
    "WarParticipant.GetCountry.GetID": (data.Kind.U32, TYPES["uint32"]),
    "WarGoal.GetNameNoFormatting": (data.Kind.STRING, TYPES["CString"]),
    "WarGoal.GetDesc": (data.Kind.STRING, TYPES["CString"]),
    "WarGoal.GetStatusString": (data.Kind.STRING, TYPES["CString"]),
    "WarGoal.GetHolder.GetID": (data.Kind.U32, TYPES["uint32"]),
    "WarGoal.GetTarget.GetID": (data.Kind.U32, TYPES["uint32"]),
    "WarGoal.IsContested": (data.Kind.BOOL, TYPES["bool"]),
    "WarGoal.IsExecuted": (data.Kind.BOOL, TYPES["bool"]),
    "WarGoal.IsPressedInCurrentPeaceDeal": (data.Kind.BOOL, TYPES["bool"]),
    "Battle.GetName": (data.Kind.STRING, TYPES["CString"]),
    "Battle.GetTooltipTag": (data.Kind.STRING, TYPES["CString"]),
    "Battle.GetStatusString": (data.Kind.STRING, TYPES["CString"]),
    "Battle.IsActive": (data.Kind.BOOL, TYPES["bool"]),
    "Battle.HasEnded": (data.Kind.BOOL, TYPES["bool"]),
    "Battle.GetAttacker.GetID": (data.Kind.U32, TYPES["uint32"]),
    "Battle.GetDefender.GetID": (data.Kind.U32, TYPES["uint32"]),
    "Battle.GetFront.GetID": (data.Kind.U32, TYPES["uint32"]),
    "Battle.GetDurationInDaysRaw": (data.Kind.I32, TYPES["int32"]),
    "Battle.CalcCurrentAttackerManpower": (data.Kind.I64, TYPES["int64"]),
    "Battle.CalcCurrentDefenderManpower": (data.Kind.I64, TYPES["int64"]),
    "Battle.CalcCurrentAttackerMorale": (data.Kind.FIXED, TYPES["CFixedPoint"]),
    "Battle.CalcCurrentDefenderMorale": (data.Kind.FIXED, TYPES["CFixedPoint"]),
    "Battle.GetAttackerTotalDead": (data.Kind.FIXED, TYPES["CFixedPoint"]),
    "Battle.GetDefenderTotalDead": (data.Kind.FIXED, TYPES["CFixedPoint"]),
    "Battle.GetOccupationWon": (data.Kind.STRING, TYPES["CString"]),
}


class WarfareReadFixture:
    WAR_ID = 0x8100002A
    ATTACKER_ID = 0x81000011
    DEFENDER_ID = 0x81000022
    FRONT_ID = 0x81000033

    def __init__(self):
        self.native = object()
        self.process = object()
        self.world = 77
        self.diagnostics = []
        self.adapters = {"data": "fake"}
        self.queries = []
        self.expressions = []
        self.releases = []
        self.handles = {}
        self.next_handle = 100
        self.refresh_count = 0
        self.war_ids = [self.WAR_ID]
        self.origin_calls = []
        self.list_element_sources = []
        self.source_count_calls = []
        self.projection_calls = []
        self.native_errors = {}
        self.native_partial_errors = {}
        self.source_errors = {}
        self.invalid_counts = set()
        self.projection_errors = {}
        self.projection_partial_errors = {}
        self.projection_truncations = {}
        self.projection_kind_overrides = {}
        self.unknown_projection = None

        self.fields = {
            "Country": {
                "AccessWars": function("AccessWars", 70),
                "GetNameNoFormatting": function("GetNameNoFormatting", TYPES["CString"]),
            },
            "War": {},
        }
        for name in (
            "GetAttackers",
            "GetDefenders",
            "GetAttackersWarGoals",
            "GetDefendersWarGoals",
            "GetActiveBattles",
        ):
            self.fields["War"][name] = function(name, 70)
        for name in (
            "IsParticipant",
            "IsAttacker",
            "IsDefender",
            "IsWarLeader",
        ):
            self.fields["War"][name] = function(name, TYPES["bool"], 1)
        for name in (
            "AllowsNegotiatedPeace",
            "HasActivePeaceDeal",
            "HasActiveWhitePeaceDeal",
            "IsPeaceDealValid",
        ):
            self.fields["War"][name] = function(name, TYPES["bool"])
        for name in (
            "GetPeaceDealValidationDesc",
            "GetPressedWarGoalsString",
            "GetNameNoFormatting",
        ):
            self.fields["War"][name] = function(name, TYPES["CString"])
        for name in ("GetWarSupport", "GetCasualties"):
            self.fields["War"][name] = function(name, TYPES["CFixedPoint"], 1)

        self.country_names = {
            self.ATTACKER_ID: "#bold Attacker#!",
            self.DEFENDER_ID: "#bold Defender#!",
            156: "#bold Player#!",
        }
        self.war_values = {
            self.WAR_ID: {
                "GetNameNoFormatting": "#bold Himalayan War#!",
                "IsParticipant": True,
                "IsAttacker": True,
                "IsDefender": False,
                "IsWarLeader": True,
                "AllowsNegotiatedPeace": True,
                "HasActivePeaceDeal": False,
                "HasActiveWhitePeaceDeal": False,
                "IsPeaceDealValid": True,
                "GetPeaceDealValidationDesc": "Valid peace deal",
                "GetPressedWarGoalsString": "Conquer State",
            }
        }
        self.war_support = {
            (self.WAR_ID, self.ATTACKER_ID): data.FixedPoint(62500),
            (self.WAR_ID, self.DEFENDER_ID): data.FixedPoint((1 << 64) - 12500),
        }
        self.casualties = {
            (self.WAR_ID, self.ATTACKER_ID): data.FixedPoint(120000),
            (self.WAR_ID, self.DEFENDER_ID): data.FixedPoint(80000),
        }
        self.source_rows = {
            (self.WAR_ID, "War.GetAttackers"): [self.ATTACKER_ID],
            (self.WAR_ID, "War.GetDefenders"): [self.DEFENDER_ID],
            (self.WAR_ID, "War.GetAttackersWarGoals"): [{
                "WarGoal.GetNameNoFormatting": "#bold Conquer Tibet#!",
                "WarGoal.GetDesc": "Take the state",
                "WarGoal.GetStatusString": "Pressed",
                "WarGoal.GetHolder.GetID": self.ATTACKER_ID,
                "WarGoal.GetTarget.GetID": self.DEFENDER_ID,
                "WarGoal.IsContested": True,
                "WarGoal.IsExecuted": False,
                "WarGoal.IsPressedInCurrentPeaceDeal": True,
            }],
            (self.WAR_ID, "War.GetDefendersWarGoals"): [],
            (self.WAR_ID, "War.GetActiveBattles"): [{
                "Battle.GetName": "#bold Battle of Lhasa#!",
                "Battle.GetTooltipTag": "BATTLE_LHASA",
                "Battle.GetStatusString": "In progress",
                "Battle.IsActive": True,
                "Battle.HasEnded": False,
                "Battle.GetAttacker.GetID": self.ATTACKER_ID,
                "Battle.GetDefender.GetID": self.DEFENDER_ID,
                "Battle.GetFront.GetID": self.FRONT_ID,
                "Battle.GetDurationInDaysRaw": 9,
                "Battle.CalcCurrentAttackerManpower": 12000,
                "Battle.CalcCurrentDefenderManpower": 9000,
                "Battle.CalcCurrentAttackerMorale": data.FixedPoint(75000),
                "Battle.CalcCurrentDefenderMorale": data.FixedPoint(50000),
                "Battle.GetAttackerTotalDead": data.FixedPoint(125000),
                "Battle.GetDefenderTotalDead": data.FixedPoint(200000),
                "Battle.GetOccupationWon": "+2.5%",
            }],
        }

    async def query(self, label, factory, **kwargs):
        self.queries.append(label)
        return factory()

    async def country_id(self):
        return 156

    async def type_id(self, name):
        return TYPES[name]

    async def refresh(self):
        self.refresh_count += 1

    async def perform(self, label, operation):
        return await operation()

    def result(self, action, *, error=0, value=None, kind=data.Kind.EMPTY, type_id=0,
               handle=0, total=None):
        raw = value.encode("utf-8") if isinstance(value, str) else b""
        return SimpleNamespace(
            action=action,
            error=error,
            value=value,
            kind=kind,
            type_id=type_id,
            value_handle=handle,
            total=len(raw) if total is None else total,
            snapshot_bytes=len(raw),
            snapshot=raw,
        )

    def retain(self, content):
        self.next_handle += 1
        self.handles[self.next_handle] = content
        return self.next_handle

    def _war_id(self, binding):
        retained = self.handles[binding.object]
        if retained[:2] != ("object", "War"):
            raise AssertionError(f"unexpected retained binding: {retained!r}")
        return retained[2]

    def evaluate(self, native, expression, bindings, **kwargs):
        self.expressions.append(expression)
        if expression in self.native_partial_errors:
            prefix, total = self.native_partial_errors[expression]
            return self.result(
                data.Action.EVALUATE,
                error=122,
                value=prefix,
                kind=data.Kind.STRING,
                type_id=TYPES["CString"],
                total=total,
            )
        if expression in self.native_errors:
            return self.result(data.Action.EVALUATE, error=self.native_errors[expression])
        if expression == "Country.AccessWars":
            handle = self.retain(("list", "War", list(self.war_ids)))
            return self.result(
                data.Action.EVALUATE,
                value=data.NativeList(handle, 0x3000, b"", "native list"),
                kind=data.Kind.LIST,
                handle=handle,
            )
        if expression.startswith("Country."):
            method = expression.removeprefix("Country.").split("(")[0]
            if method != "GetNameNoFormatting":
                raise AssertionError(f"unexpected Country expression: {expression}")
            value = self.country_names.get(bindings[0].object, f"Country {bindings[0].object}")
            return self.result(
                data.Action.EVALUATE,
                value=value,
                kind=data.Kind.STRING,
                type_id=TYPES["CString"],
            )
        if expression in {
            "War.GetAttackers",
            "War.GetDefenders",
            "War.GetAttackersWarGoals",
            "War.GetDefendersWarGoals",
            "War.GetActiveBattles",
        }:
            raise AssertionError("inline Creation sources must use source_count/evaluate_element")
        if expression.startswith("War."):
            method = expression.removeprefix("War.").split("(")[0]
            war_id = self._war_id(bindings[0])
            if method == "GetWarSupport":
                value = self.war_support[(war_id, bindings[1].object)]
                kind, type_id = data.Kind.FIXED, TYPES["CFixedPoint"]
            elif method == "GetCasualties":
                value = self.casualties[(war_id, bindings[1].object)]
                kind, type_id = data.Kind.FIXED, TYPES["CFixedPoint"]
            else:
                value = self.war_values[war_id][method]
                if isinstance(value, bool):
                    kind, type_id = data.Kind.BOOL, TYPES["bool"]
                elif isinstance(value, str):
                    kind, type_id = data.Kind.STRING, TYPES["CString"]
                else:
                    raise AssertionError(f"unexpected War scalar: {method}={value!r}")
            return self.result(data.Action.EVALUATE, value=value, kind=kind, type_id=type_id)
        raise AssertionError(f"unexpected evaluate expression: {expression}")

    def list_count(self, native, handle, **kwargs):
        return SimpleNamespace(error=0, total=len(self.handles[handle][2]))

    def list_element(self, native, handle, index, **kwargs):
        _, name, entries = self.handles[handle]
        self.list_element_sources.append(name)
        if name != "War":
            raise AssertionError("inline participant/goal/battle rows must never use list_element")
        child_handle = self.retain(("object", name, entries[index]))
        native_name = ".?AU?$SAnyType@V?$CPdxPtr@VCDataWar@@@@@PdxAnyImpl@@"
        result = self.result(
            data.Action.LIST_ELEMENT,
            value=data.NativeObject(0x1000 + child_handle, TYPES["War"], child_handle, native_name),
            kind=data.Kind.OBJECT,
            type_id=TYPES["War"],
            handle=child_handle,
        )
        result.native_type_name = native_name
        return result

    def functions(self, native, type_id, **kwargs):
        name = next(name for name, number in TYPES.items() if number == type_id)
        rows = list(self.fields[name].values())
        return SimpleNamespace(error=0, rows=rows, total=len(rows), next=len(rows))

    def release(self, native, handle, **kwargs):
        self.releases.append(handle)
        del self.handles[handle]
        return SimpleNamespace(error=0, value_handle=0)

    def source_count(self, native, source, bindings, **kwargs):
        if kwargs.get("world") != self.world or len(bindings) != 1 or not bindings[0].retained:
            raise AssertionError("source_count must use the current world and retained War binding")
        war_id = self._war_id(bindings[0])
        self.source_count_calls.append((war_id, source, tuple(bindings), kwargs))
        error = self.source_errors.get((war_id, source), self.source_errors.get(source, 0))
        if error:
            return SimpleNamespace(
                action=data.Action.SOURCE_COUNT,
                error=error,
                count_valid=0,
                source_count=0,
            )
        rows = self.source_rows[(war_id, source)]
        return SimpleNamespace(
            action=data.Action.SOURCE_COUNT,
            error=0,
            count_valid=0 if (war_id, source) in self.invalid_counts or source in self.invalid_counts else 1,
            source_count=len(rows),
        )

    def evaluate_element(self, native, source, index, expression, bindings, **kwargs):
        if kwargs.get("world") != self.world or len(bindings) != 1 or not bindings[0].retained:
            raise AssertionError("evaluate_element must use the current world and retained War binding")
        war_id = self._war_id(bindings[0])
        call = (war_id, source, index, expression, tuple(bindings), kwargs)
        self.projection_calls.append(call)
        if self.unknown_projection in ((source, index, expression), expression):
            raise RuntimeError("original child projection outcome unknown")
        specific = (war_id, source, index, expression)
        general = (source, index, expression)
        partial = self.projection_partial_errors.get(
            specific, self.projection_partial_errors.get(general)
        )
        if partial is not None:
            prefix, total = partial
            native_result = self.result(
                data.Action.EVALUATE_ELEMENT,
                error=122,
                value=prefix,
                kind=data.Kind.STRING,
                type_id=TYPES["CString"],
                total=total,
            )
            return SimpleNamespace(error=122, result=native_result, value=prefix)
        error = self.projection_errors.get(specific, self.projection_errors.get(general, 0))
        if error:
            native_result = self.result(data.Action.EVALUATE_ELEMENT, error=error)
            return SimpleNamespace(error=error, result=native_result, value=None)
        row = self.source_rows[(war_id, source)][index]
        value = row if source in ("War.GetAttackers", "War.GetDefenders") else row[expression]
        total = None
        truncated = self.projection_truncations.get(
            specific, self.projection_truncations.get(general)
        )
        if truncated is not None:
            value, total = truncated
        kind, type_id = self.projection_kind_overrides.get(
            specific,
            self.projection_kind_overrides.get(general, PROJECTION_KINDS[expression]),
        )
        native_result = self.result(
            data.Action.EVALUATE_ELEMENT,
            value=value,
            kind=kind,
            type_id=type_id,
            total=total,
        )
        return SimpleNamespace(error=0, result=native_result, value=value)

    def patches(self):
        stack = ExitStack()
        stack.enter_context(patch.multiple(
            data,
            evaluate=self.evaluate,
            list_count=self.list_count,
            list_element=self.list_element,
            enumerate_functions=self.functions,
            release=self.release,
            source_count=self.source_count,
            evaluate_element=self.evaluate_element,
        ))
        stack.enter_context(patch.object(WarfareRead, "origin", _fixture_war_origin))
        return stack


async def _fixture_war_origin(run, result, named_type, size=4, *, borrowed=False):
    """CPU proof boundary for the established retained CDataWar+0x10 ABI."""
    value = run.wrapper(result, named_type, borrowed=borrowed)
    if named_type != "War" or size != 4:
        raise AssertionError("the warfare fixture only patches the proven four-byte War origin")
    retained = run.ctx.handles[result.value_handle]
    if retained[:2] != ("object", "War"):
        raise AssertionError("origin was not requested from an owned CPdxPtr<CDataWar>")
    run.ctx.origin_calls.append({
        "pointer": value.pointer,
        "origin_address": value.pointer + 0x10,
        "war_id": retained[2],
    })
    return retained[2]


class CountryWarReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = WarfareReadFixture()
        self.api = MilitaryAPI(self.ctx)

    async def test_public_war_details_uses_full_origin_and_inline_scalar_projections(self):
        with self.ctx.patches():
            result = await self.api.war(self.ctx.WAR_ID).read()

        self.assertEqual(result["id"], self.ctx.WAR_ID)
        self.assertEqual(result["name"], "Himalayan War")
        self.assertEqual(result["name_raw"], "#bold Himalayan War#!")
        self.assertEqual(
            [(row["country_id"], row["side"], row["name"]) for row in result["participants"]],
            [
                (self.ctx.ATTACKER_ID, "attacker", "Attacker"),
                (self.ctx.DEFENDER_ID, "defender", "Defender"),
            ],
        )
        self.assertEqual(result["participants"][0]["war_support"], Decimal("0.62500"))
        self.assertEqual(result["participants"][1]["casualties"], Decimal("0.80000"))
        goal = result["war_goals"]["attacker"][0]
        self.assertEqual(goal["source_index"], 0)
        self.assertEqual(goal["name"], "Conquer Tibet")
        self.assertEqual(goal["holder_country_id"], self.ctx.ATTACKER_ID)
        self.assertEqual(goal["target_country_id"], self.ctx.DEFENDER_ID)
        self.assertEqual(result["war_goals"]["defender"], [])
        self.assertEqual(self.ctx.refresh_count, 1)
        self.assertEqual([call["war_id"] for call in self.ctx.origin_calls], [self.ctx.WAR_ID])
        self.assertEqual(
            self.ctx.origin_calls[0]["origin_address"],
            self.ctx.origin_calls[0]["pointer"] + 0x10,
        )
        self.assertEqual(self.ctx.list_element_sources, ["War"])
        self.assertEqual(
            [source for _, source, _, _ in self.ctx.source_count_calls],
            [
                "War.GetAttackers",
                "War.GetAttackersWarGoals",
                "War.GetDefenders",
                "War.GetDefendersWarGoals",
            ],
        )
        participant_calls = [call for call in self.ctx.projection_calls if "Participant" in call[3]]
        self.assertEqual(
            [(call[1], call[2], call[3]) for call in participant_calls],
            [
                ("War.GetAttackers", 0, "WarParticipant.GetCountry.GetID"),
                ("War.GetDefenders", 0, "WarParticipant.GetCountry.GetID"),
            ],
        )
        self.assertTrue(all(call[5]["expected_count"] == 1 for call in participant_calls))
        self.assertFalse(self.ctx.handles)
        self.assertFalse(self.api._warfare_runs)

    async def test_two_wars_project_all_four_inline_participant_lists(self):
        second_war = 0x8100002B
        self.ctx.war_ids.append(second_war)
        self.ctx.war_values[second_war] = dict(self.ctx.war_values[self.ctx.WAR_ID])
        self.ctx.war_values[second_war]["GetNameNoFormatting"] = "#bold Second War#!"
        for source in (
            "War.GetAttackers",
            "War.GetDefenders",
            "War.GetAttackersWarGoals",
            "War.GetDefendersWarGoals",
            "War.GetActiveBattles",
        ):
            self.ctx.source_rows[(second_war, source)] = deepcopy(
                self.ctx.source_rows[(self.ctx.WAR_ID, source)]
            )
        for country_id in (self.ctx.ATTACKER_ID, self.ctx.DEFENDER_ID):
            self.ctx.war_support[(second_war, country_id)] = self.ctx.war_support[(self.ctx.WAR_ID, country_id)]
            self.ctx.casualties[(second_war, country_id)] = self.ctx.casualties[(self.ctx.WAR_ID, country_id)]

        with self.ctx.patches():
            result = await country_wars(self.api)

        self.assertEqual([war["id"] for war in result["wars"]], [self.ctx.WAR_ID, second_war])
        participant_calls = [call for call in self.ctx.projection_calls if "Participant" in call[3]]
        self.assertEqual(len(participant_calls), 4)
        self.assertEqual(
            [(call[0], call[1], call[3]) for call in participant_calls],
            [
                (self.ctx.WAR_ID, "War.GetAttackers", "WarParticipant.GetCountry.GetID"),
                (self.ctx.WAR_ID, "War.GetDefenders", "WarParticipant.GetCountry.GetID"),
                (second_war, "War.GetAttackers", "WarParticipant.GetCountry.GetID"),
                (second_war, "War.GetDefenders", "WarParticipant.GetCountry.GetID"),
            ],
        )
        self.assertEqual(self.ctx.list_element_sources, ["War", "War"])
        self.assertFalse(self.ctx.handles)

    async def test_country_wars_distinguishes_genuine_empty_root_from_native_error(self):
        self.ctx.war_ids = []
        with self.ctx.patches():
            empty = await country_wars(self.api)
        self.assertEqual(empty["status"], "observed")
        self.assertEqual(empty["wars"], [])
        self.assertFalse(self.ctx.handles)

        failed_ctx = WarfareReadFixture()
        failed_ctx.native_errors["Country.AccessWars"] = 13
        failed_api = MilitaryAPI(failed_ctx)
        with failed_ctx.patches():
            failed = await country_wars(failed_api)
        self.assertEqual(failed["status"], "unavailable")
        self.assertIn("Country.AccessWars: native error 13", failed["reason"])
        self.assertNotIn("wars", failed)
        self.assertFalse(failed_ctx.handles)

    async def test_empty_inline_sources_are_observed_empty_not_unavailable(self):
        for source in (
            "War.GetAttackers",
            "War.GetDefenders",
            "War.GetAttackersWarGoals",
            "War.GetDefendersWarGoals",
        ):
            self.ctx.source_rows[(self.ctx.WAR_ID, source)] = []
        with self.ctx.patches():
            result = await country_wars(self.api)
        war = result["wars"][0]
        self.assertEqual(result["status"], "observed")
        self.assertEqual(war["participants"], [])
        self.assertEqual(war["participant_coverage"], "native_side_lists")
        self.assertIsNone(war["participants_unavailable_reason"])
        self.assertEqual(war["war_goals"], {"attacker": [], "defender": []})
        self.assertEqual(self.ctx.list_element_sources, ["War"])
        self.assertFalse(self.ctx.projection_calls)
        self.assertFalse(self.ctx.handles)

    async def test_participant_source_error_is_partial_and_does_not_become_empty(self):
        self.ctx.source_errors["War.GetAttackers"] = 21
        with self.ctx.patches():
            result = await country_wars(self.api)
        war = result["wars"][0]
        self.assertEqual(result["status"], "observed")
        self.assertEqual(
            [(row["country_id"], row["side"]) for row in war["participants"]],
            [(self.ctx.DEFENDER_ID, "defender")],
        )
        self.assertEqual(war["participant_coverage"], "partial_native_side_lists")
        self.assertIn("native error 21", war["unavailable"]["attacker_participants"])
        self.assertEqual(
            war["participants_unavailable_reason"],
            "Some original participant rows could not be read",
        )
        self.assertFalse(self.ctx.handles)

    async def test_optional_participant_and_goal_failures_keep_other_native_facts(self):
        self.ctx.native_errors["Country.GetNameNoFormatting"] = 17
        self.ctx.projection_errors[(
            "War.GetAttackersWarGoals", 0, "WarGoal.GetDesc"
        )] = 21
        with self.ctx.patches():
            result = await country_wars(self.api)
        war = result["wars"][0]
        attacker = war["participants"][0]
        goal = war["war_goals"]["attacker"][0]
        self.assertEqual(attacker["country_id"], self.ctx.ATTACKER_ID)
        self.assertIsNone(attacker["name"])
        self.assertIn("native error 17", attacker["unavailable"]["name"])
        self.assertEqual(goal["name"], "Conquer Tibet")
        self.assertIsNone(goal["description"])
        self.assertTrue(goal["contested"])
        self.assertIn("native error 21", goal["unavailable"]["description"])
        self.assertEqual(result["status"], "observed")
        self.assertFalse(self.ctx.handles)

    async def test_partial_native_text_keeps_real_prefix_and_partial_reason(self):
        self.ctx.native_partial_errors["War.GetPeaceDealValidationDesc"] = (
            "Valid pe",
            len("Valid peace deal"),
        )
        self.ctx.projection_partial_errors[(
            "War.GetAttackersWarGoals", 0, "WarGoal.GetDesc"
        )] = ("Take th", len("Take the state"))

        with self.ctx.patches():
            result = await country_wars(self.api)

        war = result["wars"][0]
        goal = war["war_goals"]["attacker"][0]
        self.assertEqual(war["peace_validation_description"], "Valid pe")
        self.assertNotEqual(war["peace_validation_description"], "Valid peace deal")
        self.assertIn("native error 122", war["unavailable"]["peace_validation_description"])
        self.assertIn("partial", war["unavailable"]["peace_validation_description"])
        self.assertEqual(goal["description"], "Take th")
        self.assertNotEqual(goal["description"], "Take the state")
        self.assertIn("native error 122", goal["unavailable"]["description"])
        self.assertIn("partial", goal["unavailable"]["description"])
        read = next(entry for entry in self.ctx.diagnostics if isinstance(entry, WarfareRead))
        field_receipt = next(value for label, value in read.operations
                             if label == "War.GetPeaceDealValidationDesc")
        copied_receipt = next(value for label, value in read.operations
                              if label == "WarGoal.GetDesc")
        self.assertEqual(field_receipt.error, 122)
        self.assertEqual(field_receipt.value, "Valid pe")
        self.assertEqual(copied_receipt.error, 122)
        self.assertEqual(copied_receipt.value, "Take th")
        self.assertFalse(self.ctx.handles)

    async def test_war_goal_ids_keep_raw_signed_bits_and_reject_invalid_sentinel(self):
        source = (self.ctx.WAR_ID, "War.GetAttackersWarGoals")
        signed_attacker = self.ctx.ATTACKER_ID - (1 << 32)
        goal = self.ctx.source_rows[source][0]
        goal["WarGoal.GetHolder.GetID"] = signed_attacker
        goal["WarGoal.GetTarget.GetID"] = objects.UINT32_MAX
        self.ctx.projection_kind_overrides[(
            "War.GetAttackersWarGoals", 0, "WarGoal.GetHolder.GetID"
        )] = (data.Kind.I32, TYPES["int32"])

        with self.ctx.patches():
            result = await country_wars(self.api)

        observed = result["wars"][0]["war_goals"]["attacker"][0]
        self.assertEqual(observed["holder_country_id_raw"], signed_attacker)
        self.assertEqual(observed["holder_country_id"], self.ctx.ATTACKER_ID)
        self.assertEqual(observed["target_country_id_raw"], objects.UINT32_MAX)
        self.assertIsNone(observed["target_country_id"])
        self.assertIn("complete ID", observed["unavailable"]["target_country_id"])
        self.assertFalse(self.ctx.handles)

    async def test_unknown_inline_child_retains_the_complete_original_war_graph(self):
        self.ctx.unknown_projection = "WarParticipant.GetCountry.GetID"
        with self.ctx.patches(), self.assertRaisesRegex(RuntimeError, "outcome unknown"):
            await country_wars(self.api)
        self.assertTrue(self.ctx.handles)
        self.assertFalse(self.ctx.releases)
        self.assertEqual(len(self.api._warfare_runs), 1)
        run = self.api._warfare_runs[0]
        self.assertEqual(len(run.values), 2)
        self.assertIn(("list", "War", [self.ctx.WAR_ID]), self.ctx.handles.values())
        self.assertIn(("object", "War", self.ctx.WAR_ID), self.ctx.handles.values())

    async def test_public_war_details_not_found_uses_full_war_id_not_list_index(self):
        with self.ctx.patches():
            result = await self.api.war(0).read()
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["wars"], [])
        self.assertEqual(self.ctx.origin_calls[0]["war_id"], self.ctx.WAR_ID)
        self.assertFalse(self.ctx.handles)


class BattleReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = WarfareReadFixture()
        self.api = MilitaryAPI(self.ctx)

    async def test_public_battles_projects_scalars_without_fabricating_battle_id(self):
        with self.ctx.patches():
            result = await self.api.war(self.ctx.WAR_ID).battles()
        self.assertEqual(result["status"], "observed")
        self.assertEqual(len(result["battles"]), 1)
        battle = result["battles"][0]
        self.assertEqual(battle["war_id"], self.ctx.WAR_ID)
        self.assertEqual(battle["source_index"], 0)
        self.assertIsNone(battle["id"])
        self.assertEqual(
            battle["id_unavailable_reason"],
            "Current Battle API has no native ID getter",
        )
        self.assertEqual(battle["name"], "Battle of Lhasa")
        self.assertEqual(battle["front_id"], self.ctx.FRONT_ID)
        self.assertEqual(battle["attacker_manpower"], 12000)
        self.assertEqual(battle["attacker_morale"], Decimal("0.75000"))
        self.assertEqual(self.ctx.list_element_sources, ["War"])
        self.assertEqual(
            [source for _, source, _, _ in self.ctx.source_count_calls],
            ["War.GetActiveBattles"],
        )
        self.assertTrue(all(call[1] == "War.GetActiveBattles" for call in self.ctx.projection_calls))
        self.assertTrue(all(call[5]["expected_count"] == 1 for call in self.ctx.projection_calls))
        self.assertFalse(any(call[3] in ("Battle.GetID", "Battle.GetIDString") for call in self.ctx.projection_calls))
        self.assertFalse(self.ctx.handles)
        self.assertFalse(self.api._warfare_runs)

    async def test_battle_partial_text_keeps_prefix_and_numeric_failure_stays_none(self):
        self.ctx.projection_truncations[(
            "War.GetActiveBattles", 0, "Battle.GetStatusString"
        )] = ("In pro", len("In progress"))
        self.ctx.projection_errors[(
            "War.GetActiveBattles", 0, "Battle.CalcCurrentAttackerMorale"
        )] = 21
        with self.ctx.patches():
            result = await battles(self.api)
        battle = result["battles"][0]
        self.assertEqual(result["status"], "observed")
        self.assertEqual(battle["battle_status"], "In pro")
        self.assertNotEqual(battle["battle_status"], "In progress")
        self.assertIn("partial", battle["unavailable"]["battle_status"])
        self.assertIsNone(battle["attacker_morale"])
        self.assertEqual(battle["defender_morale"], Decimal("0.50000"))
        self.assertIn("native error 21", battle["unavailable"]["attacker_morale"])
        read = next(entry for entry in self.ctx.diagnostics if isinstance(entry, WarfareRead))
        status_receipt = next(value for label, value in read.operations
                              if label == "Battle.GetStatusString")
        morale_receipt = next(value for label, value in read.operations
                              if label == "Battle.CalcCurrentAttackerMorale")
        self.assertEqual(status_receipt.error, 0)
        self.assertEqual(status_receipt.value, "In pro")
        self.assertEqual(status_receipt.result.snapshot_bytes, len("In pro"))
        self.assertEqual(status_receipt.result.total, len("In progress"))
        self.assertEqual(morale_receipt.error, 21)
        self.assertFalse(self.ctx.handles)

    async def test_battle_relation_ids_keep_raw_signed_bits_and_reject_invalid_sentinel(self):
        source = (self.ctx.WAR_ID, "War.GetActiveBattles")
        signed_attacker = self.ctx.ATTACKER_ID - (1 << 32)
        battle = self.ctx.source_rows[source][0]
        battle["Battle.GetAttacker.GetID"] = signed_attacker
        battle["Battle.GetDefender.GetID"] = objects.UINT32_MAX
        self.ctx.projection_kind_overrides[(
            "War.GetActiveBattles", 0, "Battle.GetAttacker.GetID"
        )] = (data.Kind.I32, TYPES["int32"])

        with self.ctx.patches():
            result = await battles(self.api)

        observed = result["battles"][0]
        self.assertEqual(observed["attacker_country_id_raw"], signed_attacker)
        self.assertEqual(observed["attacker_country_id"], self.ctx.ATTACKER_ID)
        self.assertEqual(observed["defender_country_id_raw"], objects.UINT32_MAX)
        self.assertIsNone(observed["defender_country_id"])
        self.assertIn("complete ID", observed["unavailable"]["defender_country_id"])
        self.assertEqual(observed["front_id_raw"], self.ctx.FRONT_ID)
        self.assertEqual(observed["front_id"], self.ctx.FRONT_ID)
        self.assertFalse(self.ctx.handles)

    async def test_war_filter_distinguishes_missing_war_from_matched_empty_source(self):
        self.ctx.source_rows[(self.ctx.WAR_ID, "War.GetActiveBattles")] = []
        with self.ctx.patches():
            matched = await self.api.war(self.ctx.WAR_ID).battles()
            missing = await self.api.war(self.ctx.WAR_ID + 1).battles()

        self.assertEqual(matched["status"], "observed")
        self.assertTrue(matched["war_found"])
        self.assertEqual(matched["battles"], [])
        self.assertEqual(missing["status"], "not_found")
        self.assertFalse(missing["war_found"])
        self.assertEqual(missing["battles"], [])
        self.assertEqual(
            [source for _, source, _, _ in self.ctx.source_count_calls],
            ["War.GetActiveBattles"],
        )
        self.assertEqual(self.ctx.refresh_count, 2)
        self.assertFalse(self.ctx.handles)

    async def test_empty_and_failed_battle_sources_are_not_conflated(self):
        self.ctx.source_rows[(self.ctx.WAR_ID, "War.GetActiveBattles")] = []
        with self.ctx.patches():
            empty = await battles(self.api)
        self.assertEqual(empty["status"], "observed")
        self.assertEqual(empty["battles"], [])
        self.assertFalse(self.ctx.handles)

        failed_ctx = WarfareReadFixture()
        failed_ctx.source_errors["War.GetActiveBattles"] = 13
        failed_api = MilitaryAPI(failed_ctx)
        with failed_ctx.patches():
            failed = await battles(failed_api)
        self.assertEqual(failed["status"], "unavailable")
        self.assertIsNone(failed["battles"])
        self.assertIn("native error 13", failed["details"][0])
        self.assertFalse(failed_ctx.handles)

    async def test_unobserved_zero_error_count_is_not_reported_as_empty(self):
        self.ctx.invalid_counts.add("War.GetActiveBattles")
        with self.ctx.patches():
            result = await battles(self.api)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["battles"])
        self.assertIn("original source count was not observed", result["details"][0])
        self.assertFalse(self.ctx.handles)




if __name__ == "__main__":
    unittest.main()
