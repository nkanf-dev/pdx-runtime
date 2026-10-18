"""CPU-only tests for owned WarGoal reads and peace goal selection."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.v3_game.military import peace as warpeace
from pdx_runtime.v3_game.military._warfare import WarfareRead
from pdx_runtime.v3_game.military import MilitaryAPI


TYPES = {
    "Country": 83,
    "War": 236,
    "WarGoal": 237,
    "WarGoalType": 238,
    "State": 15,
    "ArticleDraft": 239,
    "ArticleType": 240,
    "bool": 5,
    "uint32": 6,
    "int32": 4,
    "CString": 0,
}


def function(name, result_type, arguments=0, *, action=False):
    return SimpleNamespace(
        name=name,
        argument_count=arguments,
        return_type_id=result_type,
        callbacks=(0, 0, 0, 123) if action else (123, 0, 0, 0),
    )


class FakeCommand:
    def __init__(self, on_submit):
        self.command = 0x9000
        self.world = 77
        self.pending = None
        self.attempt = None
        self.calls = []
        self.on_submit = on_submit

    def validate(self, capacity):
        self.calls.append(("validate", capacity))
        self.attempt = SimpleNamespace(outcome="owned")
        return SimpleNamespace(error=0, validated=1, reason="", command=self.command)

    def submit(self, flags):
        self.calls.append(("submit", flags))
        self.on_submit()
        self.command = 0
        self.attempt = SimpleNamespace(outcome="consumed")
        return SimpleNamespace(error=0, accepted=1, command=0)

    def destroy(self):
        self.calls.append(("destroy", None))
        self.command = 0
        self.attempt = SimpleNamespace(outcome="consumed")
        return SimpleNamespace(error=0, command=0)


class PeaceFixture:
    WAR_ID = 0x8100002A
    STATE_GOAL_ID = 0x81000101
    ARTICLE_GOAL_ID = 0x81000102
    DEFENDER_GOAL_ID = 0x81000103
    ATTACKER_ID = 0x81000011
    DEFENDER_ID = 0x81000022
    STATE_ID = 0x81000033

    def __init__(self):
        self.native = object()
        self.process = object()
        self.world = 77
        self.adapters = {"data": "fake", "commands": "fake"}
        self.diagnostics = []
        self.queries = []
        self.expressions = []
        self.evaluate_calls = []
        self.source_count_calls = []
        self.projection_calls = []
        self.list_element_sources = []
        self.releases = []
        self.handles = {}
        self.next_handle = 100
        self.native_errors = {}
        self.native_partial_errors = {}
        self.source_errors = {}
        self.projection_errors = {}
        self.unknown_projection = None
        self.unknown_after = {}
        self.expression_counts = {}
        self.origin_calls = []
        self.producer_expressions = []
        self.command_value_calls = []
        self.clone_calls = []
        self.selected_goal_for_command = None
        self.war_ids = [self.WAR_ID]
        self.goal_sources = {
            "War.GetAttackersWarGoals": [self.STATE_GOAL_ID, self.ARTICLE_GOAL_ID],
            "War.GetDefendersWarGoals": [self.DEFENDER_GOAL_ID],
        }
        self.participant_sources = {
            "War.GetAttackers": [self.ATTACKER_ID],
            "War.GetDefenders": [self.DEFENDER_ID],
        }
        self.potential_pressed = {
            self.STATE_GOAL_ID: True,
            self.ARTICLE_GOAL_ID: False,
            self.DEFENDER_GOAL_ID: False,
        }
        self.country_names = {
            156: "#bold Player#!",
            self.ATTACKER_ID: "#bold Attacker#!",
            self.DEFENDER_ID: "#bold Defender#!",
        }
        self.goal_rows = self._goals()
        self.fields = {name: {} for name in (
            "Country", "War", "WarGoal", "WarGoalType", "State", "ArticleDraft", "ArticleType"
        )}
        self._install_fields()
        self.zero_arg_values = {
            "AllowsNegotiatedPeace": True,
            "HasActivePeaceDeal": True,
            "HasActiveWhitePeaceDeal": False,
            "IsPeaceDealValid": True,
            "GetPeaceDealValidationDesc": "Valid active peace deal",
            "WouldWhitePeaceDealBeAccepted": False,
            "GetPressedWarGoalsString": "Conquer Tibet; Treaty Article 3",
        }
        self.country_values = self._country_values()
        self.owner = FakeCommand(self._apply_toggle)

    def _goals(self):
        return {
            self.STATE_GOAL_ID: {
                "GetNameNoFormatting": "#bold Conquer Tibet#!",
                "GetDesc": "Take the state",
                "GetStatusString": "Pressed",
                "GetHolder.GetID": self.ATTACKER_ID,
                "GetTarget.GetID": self.DEFENDER_ID,
                "IsContested": True,
                "IsExecuted": False,
                "IsPressedInCurrentPeaceDeal": True,
                "HoldsArticle": False,
                "GetType.GetNameNoFormatting": "#bold Conquer State#!",
                "GetType.GetTooltipTag": "WAR_GOAL_CONQUER_STATE",
                "GetTargetState.IsValid": True,
                "GetTargetState.GetID": self.STATE_ID,
                "GetTargetState.GetNameNoFormatting": "#bold Tibet#!",
            },
            self.ARTICLE_GOAL_ID: {
                "GetNameNoFormatting": "#bold Enforce Article#!",
                "GetDesc": "Enforce treaty article",
                "GetStatusString": "Unpressed",
                "GetHolder.GetID": self.ATTACKER_ID,
                "GetTarget.GetID": self.DEFENDER_ID,
                "IsContested": False,
                "IsExecuted": False,
                "IsPressedInCurrentPeaceDeal": False,
                "HoldsArticle": True,
                "GetType.GetNameNoFormatting": "#bold Enforce Treaty Article#!",
                "GetType.GetTooltipTag": "WAR_GOAL_ENFORCE_ARTICLE",
                "GetTargetState.IsValid": False,
                "GetArticleDraft.GetArticleNumber": 3,
                "GetArticleDraft.GetNameNoFormatting": "#bold Article 3#!",
                "GetArticleDraft.GetArticleType.GetNameNoFormatting": "#bold Mutual Defense#!",
                "GetArticleDraft.GetArticleType.GetTooltipTag": "ARTICLE_MUTUAL_DEFENSE",
            },
            self.DEFENDER_GOAL_ID: {
                "GetNameNoFormatting": "#bold Reparations#!",
                "GetDesc": "Demand reparations",
                "GetStatusString": "Pressed",
                "GetHolder.GetID": self.DEFENDER_ID,
                "GetTarget.GetID": self.ATTACKER_ID,
                "IsContested": True,
                "IsExecuted": False,
                "IsPressedInCurrentPeaceDeal": True,
                "HoldsArticle": False,
                "GetType.GetNameNoFormatting": "#bold War Reparations#!",
                "GetType.GetTooltipTag": "WAR_GOAL_REPARATIONS",
                "GetTargetState.IsValid": False,
            },
        }

    def _country_values(self):
        result = {}
        for country_id, side in ((156, "player"), (self.ATTACKER_ID, "attacker"),
                                 (self.DEFENDER_ID, "defender")):
            result[country_id] = {
                "IsPeaceNegotiator": side != "defender",
                "MustRatifyPeaceDeal": side == "defender",
                "WouldAcceptActivePeaceDeal": side != "defender",
                "GetWhitePeaceDealAcceptanceTooltip": "White peace for " + side,
                "GetCapitulationDesire": 0 if side == "attacker" else 37,
                "GetCapitulationDesireDesc": "Capitulation desire for " + side,
                "GetActivePeaceDealAcceptanceScore": "+12" if side != "defender" else "-8",
                "GetActivePeaceDealAcceptanceTooltip": "Active acceptance for " + side,
                "WouldAcceptProposedPeaceDeal": side == "attacker",
                "GetProposedPeaceDealAcceptanceScore": "+4" if side == "attacker" else "-16",
                "GetProposedPeaceDealAcceptanceTooltip": "Proposed acceptance for " + side,
            }
        return result

    def _install_fields(self):
        self.fields["Country"].update({
            "AccessWars": function("AccessWars", 70),
            "GetNameNoFormatting": function("GetNameNoFormatting", TYPES["CString"]),
            "GetID": function("GetID", TYPES["uint32"]),
        })
        for name in (*self.goal_sources, *self.participant_sources):
            self.fields["War"][name.removeprefix("War.")] = function(name.removeprefix("War."), 70)
        for name in ("AllowsNegotiatedPeace", "HasActivePeaceDeal", "HasActiveWhitePeaceDeal",
                     "IsPeaceDealValid", "WouldWhitePeaceDealBeAccepted"):
            self.fields["War"][name] = function(name, TYPES["bool"])
        for name in ("GetPeaceDealValidationDesc", "GetPressedWarGoalsString"):
            self.fields["War"][name] = function(name, TYPES["CString"])
        for name in ("IsPeaceNegotiator", "MustRatifyPeaceDeal", "WouldAcceptActivePeaceDeal"):
            self.fields["War"][name] = function(name, TYPES["bool"], 1)
        for name in ("GetWhitePeaceDealAcceptanceTooltip", "GetCapitulationDesireDesc",
                     "GetActivePeaceDealAcceptanceScore", "GetActivePeaceDealAcceptanceTooltip"):
            self.fields["War"][name] = function(name, TYPES["CString"], 1)
        self.fields["War"]["GetCapitulationDesire"] = function(
            "GetCapitulationDesire", TYPES["int32"], 1
        )
        self.fields["War"]["WouldAcceptProposedPeaceDeal"] = function(
            "WouldAcceptProposedPeaceDeal", TYPES["bool"], 2
        )
        for name in ("GetProposedPeaceDealAcceptanceScore", "GetProposedPeaceDealAcceptanceTooltip"):
            self.fields["War"][name] = function(name, TYPES["CString"], 2)
        self.fields["War"]["HasProposedWhitePeaceDeal"] = function(
            "HasProposedWhitePeaceDeal", TYPES["bool"], 1
        )

        for name, type_name, args in (
            ("GetNameNoFormatting", "CString", 0), ("GetDesc", "CString", 0),
            ("GetStatusString", "CString", 0), ("IsContested", "bool", 0),
            ("IsExecuted", "bool", 0), ("IsPressedInCurrentPeaceDeal", "bool", 0),
            ("HoldsArticle", "bool", 0), ("IsPressedInPotentialPeaceDeal", "bool", 1),
        ):
            self.fields["WarGoal"][name] = function(name, TYPES.get(type_name, 70), args)
        self.fields["WarGoal"]["ToggleIsPressedInPotentialPeaceDeal"] = function(
            "ToggleIsPressedInPotentialPeaceDeal", 0, 1, action=True
        )
        self.fields["WarGoalType"].update({
            "GetNameNoFormatting": function("GetNameNoFormatting", TYPES["CString"]),
            "GetTooltipTag": function("GetTooltipTag", TYPES["CString"]),
        })
        self.fields["State"].update({
            "IsValid": function("IsValid", TYPES["bool"]),
            "GetID": function("GetID", TYPES["uint32"]),
            "GetNameNoFormatting": function("GetNameNoFormatting", TYPES["CString"]),
        })
        self.fields["ArticleDraft"].update({
            "GetArticleNumber": function("GetArticleNumber", TYPES["int32"]),
            "GetNameNoFormatting": function("GetNameNoFormatting", TYPES["CString"]),
        })
        self.fields["ArticleType"].update({
            "GetNameNoFormatting": function("GetNameNoFormatting", TYPES["CString"]),
            "GetTooltipTag": function("GetTooltipTag", TYPES["CString"]),
        })

    async def query(self, label, factory, **kwargs):
        self.queries.append(label)
        return factory()

    async def country_id(self):
        return 156

    async def type_id(self, name):
        return TYPES[name]

    async def perform(self, label, operation):
        return await operation()

    def result(self, action, *, error=0, value=None, kind=data.Kind.EMPTY,
               type_id=0, handle=0, total=None, native_type_name=""):
        raw = value.encode("utf-8") if isinstance(value, str) else b""
        return SimpleNamespace(
            action=action, error=error, value=value, kind=kind, type_id=type_id,
            native_type_name=native_type_name,
            value_handle=handle, total=len(raw) if total is None else total,
            snapshot_bytes=len(raw), snapshot=raw,
        )

    def retain(self, content):
        self.next_handle += 1
        self.handles[self.next_handle] = content
        return self.next_handle

    def _retained(self, binding, name):
        content = self.handles[binding.object]
        if content[:2] != ("object", name):
            raise AssertionError(content)
        return content[2]

    def _apply_toggle(self):
        goal_id = self.selected_goal_for_command
        self.potential_pressed[goal_id] = not self.potential_pressed[goal_id]

    def evaluate(self, native, expression, bindings, **kwargs):
        self.expressions.append(expression)
        self.evaluate_calls.append((expression, tuple(bindings)))
        count = self.expression_counts.get(expression, 0)
        self.expression_counts[expression] = count + 1
        if expression in self.unknown_after and count >= self.unknown_after[expression]:
            raise RuntimeError("original post-read outcome unknown")
        if expression in self.native_partial_errors:
            prefix, total = self.native_partial_errors[expression]
            return self.result(data.Action.EVALUATE, error=122, value=prefix,
                kind=data.Kind.STRING, type_id=TYPES["CString"], total=total)
        if expression in self.native_errors:
            return self.result(data.Action.EVALUATE, error=self.native_errors[expression])
        if expression == "Country.AccessWars":
            handle = self.retain(("list", "War", list(self.war_ids)))
            return self.result(data.Action.EVALUATE,
                value=data.NativeList(handle, 0x3000, b"", "native list"),
                kind=data.Kind.LIST, handle=handle)
        if expression in self.goal_sources:
            self._retained(bindings[0], "War")
            handle = self.retain(("list", "WarGoal", list(self.goal_sources[expression])))
            return self.result(data.Action.EVALUATE,
                value=data.NativeList(handle, 0x3000, b"", "native list"),
                kind=data.Kind.LIST, handle=handle)
        if expression.startswith("Country."):
            method = expression.removeprefix("Country.").split("(")[0]
            if method == "GetNameNoFormatting":
                value, kind, type_id = self.country_names[bindings[0].object], data.Kind.STRING, TYPES["CString"]
            elif method == "GetID":
                value, kind, type_id = bindings[0].object, data.Kind.U32, TYPES["uint32"]
            else:
                raise AssertionError(expression)
            return self.result(data.Action.EVALUATE, value=value, kind=kind, type_id=type_id)
        if expression.startswith("WarGoal."):
            goal_id = self._retained(bindings[0], "WarGoal")
            if expression == "WarGoal.IsPressedInPotentialPeaceDeal(Country.Self)":
                value = self.potential_pressed[goal_id]
            elif expression == "WarGoal.ToggleIsPressedInPotentialPeaceDeal(Country.Self)":
                self.producer_expressions.append(expression)
                self.selected_goal_for_command = goal_id
                handle = self.retain(("command_source", goal_id))
                carrier = data.NativeDataCommand(0x4000, 0x5000, 0x4018, handle,
                    ".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@", self.world)
                return self.result(data.Action.EVALUATE, value=carrier,
                    kind=data.Kind.OBJECT, handle=handle)
            else:
                key = expression.removeprefix("WarGoal.")
                value = self.goal_rows[goal_id][key]
            if isinstance(value, bool):
                kind, type_id = data.Kind.BOOL, TYPES["bool"]
            elif isinstance(value, int):
                kind, type_id = data.Kind.U32 if value > 0x7fffffff else data.Kind.I32, (
                    TYPES["uint32"] if value > 0x7fffffff else TYPES["int32"])
            elif isinstance(value, str):
                kind, type_id = data.Kind.STRING, TYPES["CString"]
            else:
                raise AssertionError(value)
            return self.result(data.Action.EVALUATE, value=value, kind=kind, type_id=type_id)
        if expression.startswith("War."):
            self._retained(bindings[0], "War")
            method = expression.removeprefix("War.").split("(")[0]
            if method in self.zero_arg_values:
                value = self.zero_arg_values[method]
            elif method == "HasProposedWhitePeaceDeal":
                value = True
            else:
                if len(bindings) != 2 or not bindings[1].country_id or bindings[1].retained:
                    raise AssertionError("participant fact requires one independent full Country binding")
                value = self.country_values[bindings[1].object][method]
            if isinstance(value, bool): kind, type_id = data.Kind.BOOL, TYPES["bool"]
            elif isinstance(value, int): kind, type_id = data.Kind.I32, TYPES["int32"]
            else: kind, type_id = data.Kind.STRING, TYPES["CString"]
            return self.result(data.Action.EVALUATE, value=value, kind=kind, type_id=type_id)
        raise AssertionError(expression)

    def list_count(self, native, handle, **kwargs):
        return SimpleNamespace(error=0, total=len(self.handles[handle][2]))

    def list_element(self, native, handle, index, **kwargs):
        _, name, entries = self.handles[handle]
        self.list_element_sources.append(name)
        child = self.retain(("object", name, entries[index]))
        native_name = ".?AU?$SAnyType@V?$CPdxPtr@VCData" + name + "@@@@@PdxAnyImpl@@"
        return self.result(data.Action.LIST_ELEMENT,
            value=data.NativeObject(0x1000 + child, TYPES[name], child, native_name),
            kind=data.Kind.OBJECT, type_id=TYPES[name], handle=child,
            native_type_name=native_name)

    def functions(self, native, type_id, **kwargs):
        name = next(name for name, number in TYPES.items()
                    if number == type_id and name in self.fields)
        rows = list(self.fields[name].values())
        return SimpleNamespace(error=0, rows=rows, total=len(rows), next=len(rows))

    def release(self, native, handle, **kwargs):
        self.releases.append(handle)
        del self.handles[handle]
        return SimpleNamespace(error=0, value_handle=0)

    def source_count(self, native, source, bindings, **kwargs):
        war_id = self._retained(bindings[0], "War")
        self.source_count_calls.append((war_id, source, tuple(bindings), kwargs))
        return SimpleNamespace(action=data.Action.SOURCE_COUNT, error=0, count_valid=1,
            source_count=len(self.participant_sources[source]))

    def evaluate_element(self, native, source, index, expression, bindings, **kwargs):
        war_id = self._retained(bindings[0], "War")
        self.projection_calls.append((war_id, source, index, expression, tuple(bindings), kwargs))
        if expression == self.unknown_projection:
            raise RuntimeError("original inline child outcome unknown")
        if expression != "WarParticipant.GetCountry.GetID":
            raise AssertionError(expression)
        value = self.participant_sources[source][index]
        native_result = self.result(data.Action.EVALUATE_ELEMENT, value=value,
            kind=data.Kind.U32, type_id=TYPES["uint32"])
        return SimpleNamespace(error=0, result=native_result, value=value)

    def command_value(self, native, handle, **kwargs):
        self.command_value_calls.append(handle)
        goal_id = self.handles[handle][1]
        carrier = data.NativeDataCommand(0x4000, 0x5000, 0x4018, handle,
            ".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@", self.world)
        return self.result(data.Action.COMMAND_VALUE, value=carrier, kind=data.Kind.OBJECT)

    def command_request(self, native, action, **kwargs):
        self.clone_calls.append((action, kwargs))
        if action != commands.Action.CLONE or kwargs.get("command") != 0x4000:
            raise AssertionError("only the selected original toggle carrier may be cloned")
        return SimpleNamespace(error=0, action=commands.Action.CLONE, command=0,
            cloned_command=self.owner.command, type_token=901, world=self.world)

    def patches(self):
        stack = ExitStack()
        stack.enter_context(patch.multiple(data, evaluate=self.evaluate,
            list_count=self.list_count, list_element=self.list_element,
            enumerate_functions=self.functions, release=self.release,
            source_count=self.source_count, evaluate_element=self.evaluate_element,
            command_value=self.command_value))
        stack.enter_context(patch.object(WarfareRead, "origin", _fixture_origin))
        stack.enter_context(patch.object(commands, "request", self.command_request))
        stack.enter_context(patch.object(commands.NativeCommand, "from_result", return_value=self.owner))
        return stack


async def _fixture_origin(run, result, named_type, size=4, *, borrowed=False):
    value = run.wrapper(result, named_type, borrowed=borrowed)
    if named_type not in ("War", "WarGoal") or size != 4:
        raise AssertionError("only proved retained War/WarGoal full references are patched")
    content = run.ctx.handles[result.value_handle]
    run.ctx.origin_calls.append((named_type, value.pointer + 0x10, content[2]))
    return content[2]


class WarGoalReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_goals_have_full_ids_real_type_state_article_and_proposed_state(self):
        ctx = PeaceFixture(); api = MilitaryAPI(ctx)
        with ctx.patches():
            result = await warpeace.war_goals(api, ctx.WAR_ID)
        self.assertEqual(result["status"], "observed")
        state_goal, article_goal = result["goals"]["attacker"]
        self.assertEqual(state_goal["id"], ctx.STATE_GOAL_ID)
        self.assertEqual(state_goal["mutation_selector"], {"war_goal_id": ctx.STATE_GOAL_ID})
        self.assertEqual(state_goal["source_index"], 0)
        self.assertEqual(state_goal["goal_type"]["name"], "Conquer State")
        self.assertEqual(state_goal["goal_type"]["tooltip_tag"], "WAR_GOAL_CONQUER_STATE")
        self.assertIsNone(state_goal["goal_type"]["definition_key"])
        self.assertEqual(state_goal["target_state"]["id"], ctx.STATE_ID)
        self.assertEqual(state_goal["target_kind"], "state")
        self.assertTrue(state_goal["pressed_in_current_peace"])
        self.assertTrue(state_goal["pressed_in_proposed_peace"])
        self.assertEqual(article_goal["id"], ctx.ARTICLE_GOAL_ID)
        self.assertEqual(article_goal["target_kind"], "article")
        self.assertEqual(article_goal["article"]["number"], 3)
        self.assertEqual(article_goal["article"]["type_name"], "Mutual Defense")
        self.assertFalse(article_goal["pressed_in_proposed_peace"])
        self.assertEqual(ctx.list_element_sources, ["War", "WarGoal", "WarGoal", "WarGoal"])
        self.assertEqual([call[0] for call in ctx.origin_calls], ["War", "WarGoal", "WarGoal", "WarGoal"])
        self.assertFalse(ctx.projection_calls)
        self.assertFalse(ctx.handles)


class PeaceReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_and_proposed_terms_and_participant_acceptance_are_distinct(self):
        ctx = PeaceFixture(); api = MilitaryAPI(ctx)
        with ctx.patches():
            result = await warpeace.peace_read(api, ctx.WAR_ID)
        self.assertEqual(result["status"], "observed")
        self.assertEqual([g["id"] for g in result["active_terms"]],
                         [ctx.STATE_GOAL_ID, ctx.DEFENDER_GOAL_ID])
        self.assertEqual([g["id"] for g in result["proposed_terms"]], [ctx.STATE_GOAL_ID])
        self.assertTrue(result["active_terms_complete"])
        self.assertTrue(result["proposed_terms_complete"])
        attacker, defender = result["participants"]
        self.assertEqual(attacker["active_acceptance_score"], "+12")
        self.assertEqual(attacker["proposed_acceptance_score"], "+4")
        self.assertEqual(attacker["capitulation_desire"], 0)
        self.assertFalse(attacker["must_ratify"])
        self.assertTrue(defender["must_ratify"])
        proposed_calls = [(e,b) for e,b in ctx.evaluate_calls if "ProposedPeaceDeal" in e]
        self.assertTrue(proposed_calls)
        for expression, bindings in proposed_calls:
            self.assertIn("(Country.Self, GetPlayer)", expression)
            self.assertEqual(len(bindings), 2)
            self.assertTrue(bindings[0].retained)
            self.assertTrue(bindings[1].country_id)
        participant_calls = [c for c in ctx.projection_calls
                             if c[3] == "WarParticipant.GetCountry.GetID"]
        self.assertEqual([(c[1],c[2]) for c in participant_calls],
                         [("War.GetAttackers",0),("War.GetDefenders",0)])
        self.assertFalse(ctx.handles)

    async def test_partial_acceptance_reason_keeps_prefix_with_false_and_zero_facts(self):
        ctx = PeaceFixture(); api = MilitaryAPI(ctx)
        expression = "War.GetProposedPeaceDealAcceptanceTooltip(Country.Self, GetPlayer)"
        ctx.native_partial_errors[expression] = (
            "Proposed acc",
            len("Proposed acceptance for attacker"),
        )
        with ctx.patches():
            result = await warpeace.peace_read(api, ctx.WAR_ID)
        attacker = result["participants"][0]
        self.assertEqual(attacker["proposed_acceptance_reason"], "Proposed acc")
        self.assertIn("native error 122", attacker["unavailable"]["proposed_acceptance_reason"])
        self.assertIn("partial", attacker["unavailable"]["proposed_acceptance_reason"])
        self.assertEqual(attacker["capitulation_desire"], 0)
        self.assertFalse(attacker["must_ratify"])
        read = next(entry for entry in ctx.diagnostics if isinstance(entry, WarfareRead))
        receipts = [value for label, value in read.operations if label == expression]
        self.assertEqual(len(receipts), 2)
        self.assertTrue(all(receipt.error == 122 for receipt in receipts))
        self.assertFalse(ctx.handles)

    async def test_unknown_participant_projection_retains_owned_war_and_goal_graph(self):
        ctx = PeaceFixture(); api = MilitaryAPI(ctx)
        ctx.unknown_projection = "WarParticipant.GetCountry.GetID"
        with ctx.patches(), self.assertRaisesRegex(RuntimeError, "outcome unknown"):
            await warpeace.peace_read(api, ctx.WAR_ID)
        self.assertTrue(ctx.handles)
        self.assertFalse(ctx.releases)
        self.assertEqual(len(api._warfare_runs), 1)
        self.assertIn(("object", "War", ctx.WAR_ID), ctx.handles.values())
        self.assertIn(("object", "WarGoal", ctx.STATE_GOAL_ID), ctx.handles.values())


class GoalSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_desired_state_reads_only_and_does_not_select_producer(self):
        ctx = PeaceFixture(); api = MilitaryAPI(ctx)
        with ctx.patches():
            result = await warpeace.set_goal_pressed(api, ctx.WAR_ID, ctx.STATE_GOAL_ID, True)
        self.assertEqual(result["status"], "unchanged")
        self.assertFalse(result["submitted"])
        self.assertFalse(result["changed"])
        self.assertTrue(result["observed_matches_desired"])
        self.assertFalse(ctx.producer_expressions)
        self.assertFalse(ctx.owner.calls)
        self.assertFalse(ctx.handles)

    async def test_selected_full_goal_id_toggles_once_validates_submits_and_reads_back(self):
        ctx = PeaceFixture(); api = MilitaryAPI(ctx)
        with ctx.patches():
            result = await warpeace.set_goal_pressed(api, ctx.WAR_ID, ctx.ARTICLE_GOAL_ID, True)
        self.assertEqual(result["war_goal_id"], ctx.ARTICLE_GOAL_ID)
        self.assertEqual(result["source_index"], 1)
        self.assertIn("selection used the complete", result["source_index_scope"])
        self.assertEqual(ctx.producer_expressions,
            ["WarGoal.ToggleIsPressedInPotentialPeaceDeal(Country.Self)"])
        self.assertEqual(ctx.owner.calls, [("validate",65536),("submit",4)])
        self.assertEqual(len(ctx.command_value_calls), 1)
        self.assertEqual(len(ctx.clone_calls), 1)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["submitted"])
        self.assertTrue(result["observed_pressed"])
        self.assertTrue(result["observed_matches_desired"])
        self.assertTrue(result["changed"])
        self.assertFalse(ctx.handles)

    async def test_missing_full_goal_id_is_not_selected_by_source_index_or_name(self):
        ctx = PeaceFixture(); api = MilitaryAPI(ctx)
        with ctx.patches():
            result = await warpeace.set_goal_pressed(api, ctx.WAR_ID, 1, True)
        self.assertEqual(result["status"], "not_found")
        self.assertFalse(result["submitted"])
        self.assertFalse(ctx.producer_expressions)
        self.assertFalse(ctx.owner.calls)
        self.assertFalse(ctx.handles)

    async def test_unknown_postread_keeps_acceptance_and_original_goal_source_graph(self):
        ctx = PeaceFixture(); api = MilitaryAPI(ctx)
        expression = "WarGoal.IsPressedInPotentialPeaceDeal(Country.Self)"
        ctx.unknown_after[expression] = 1
        with ctx.patches():
            result = await warpeace.set_goal_pressed(api, ctx.WAR_ID, ctx.ARTICLE_GOAL_ID, True)
        self.assertTrue(result["accepted"])
        self.assertIn("outcome unknown", result["observation_error"])
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(ctx.producer_expressions,
            ["WarGoal.ToggleIsPressedInPotentialPeaceDeal(Country.Self)"])
        self.assertFalse(ctx.releases)
        self.assertTrue(ctx.handles)
        self.assertEqual(len(api._warfare_runs), 1)
        self.assertIs(api._warfare_runs[0].public_result, result)

    async def test_desired_state_requires_real_bool_before_native_work(self):
        ctx = PeaceFixture(); api = MilitaryAPI(ctx)
        with self.assertRaisesRegex(ValueError, "bool"):
            await warpeace.set_goal_pressed(api, ctx.WAR_ID, ctx.STATE_GOAL_ID, 1)
        self.assertFalse(ctx.queries)


if __name__ == "__main__":
    unittest.main()
