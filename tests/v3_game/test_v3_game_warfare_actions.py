"""CPU-only tests for warfare reference, producer, and peace actions."""
from contextlib import ExitStack
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.v3_game.military import _warfare as warfare
from pdx_runtime.v3_game.military import MilitaryAPI
from tests.v3_game.test_v3_game_warfare import FakeCommand


TYPES = {
    "Country": 83,
    "War": 236,
    "MilitaryFormation": 37,
    "bool": 5,
    "CString": 0,
}


def function(name, result_type=0, arguments=0):
    return NS(name=name, argument_count=arguments, return_type_id=result_type,
              callbacks=(123, 0, 0, 0))


class ActionFixture:
    def __init__(self, *, eligible=True, accepted=True):
        self.native = object()
        self.process = object()
        self.world = 77
        self.actor = 156
        self.adapters = {"data": "fake-data", "commands": "fake-commands"}
        self.diagnostics = []
        self.refresh_count = 0
        self.query_labels = []
        self.expressions = []
        self.expression_bindings = []
        self.events = []
        self.releases = []
        self.handles = {}
        self.source_handles = []
        self.source_history = []
        self.next_handle = 100
        self.next_pointer = 0x10000
        self.next_clone = 0x80000
        self.default_eligible = eligible
        self.default_accepted = accepted
        self.rejection_reason = "Native rules reject this action"
        self.owners = []
        self.produced = {}
        self.clone_requests = []
        self.resolve_names = []
        self.create_records = []
        self.unknown_expression = None
        self.supply_selected = True
        self.tokens = {
            "assign_commander_to_formation": 1001,
            "unassign_commander_from_formation": 1002,
            "reset_character_order_command": 1003,
            "character": 2001,
            "formation": 2002,
        }
        self.peace_values = {
            "War.AllowsNegotiatedPeace": True,
            "War.HasActivePeaceDeal": True,
            "War.HasActiveWhitePeaceDeal": False,
            "War.IsPeaceDealValid": True,
            "War.GetPeaceDealValidationDesc": "Valid terms",
            "War.IsPeaceNegotiator(Country.Self)": True,
            "War.MustRatifyPeaceDeal(Country.Self)": False,
            "War.WouldAcceptActivePeaceDeal(Country.Self)": True,
            "War.WouldWhitePeaceDealBeAccepted": True,
            "War.GetWhitePeaceDealAcceptanceTooltip(Country.Self)": "Would accept",
            "War.GetCapitulationDesireDesc(Country.Self)": "Low desire",
        }
        self.fields = {
            "War": {
                "AllowsNegotiatedPeace": function("AllowsNegotiatedPeace", TYPES["bool"]),
                "HasActivePeaceDeal": function("HasActivePeaceDeal", TYPES["bool"]),
                "HasActiveWhitePeaceDeal": function("HasActiveWhitePeaceDeal", TYPES["bool"]),
                "IsPeaceDealValid": function("IsPeaceDealValid", TYPES["bool"]),
                "GetPeaceDealValidationDesc": function("GetPeaceDealValidationDesc", TYPES["CString"]),
                "IsPeaceNegotiator": function("IsPeaceNegotiator", TYPES["bool"], 1),
                "MustRatifyPeaceDeal": function("MustRatifyPeaceDeal", TYPES["bool"], 1),
                "WouldAcceptActivePeaceDeal": function("WouldAcceptActivePeaceDeal", TYPES["bool"], 1),
                "WouldWhitePeaceDealBeAccepted": function("WouldWhitePeaceDealBeAccepted", TYPES["bool"]),
                "GetWhitePeaceDealAcceptanceTooltip": function(
                    "GetWhitePeaceDealAcceptanceTooltip", TYPES["CString"], 1),
                "GetCapitulationDesireDesc": function("GetCapitulationDesireDesc", TYPES["CString"], 1),
            },
            "MilitaryFormation": {
                "IsSupplyPrioritySelected": function("IsSupplyPrioritySelected", TYPES["bool"], 1),
            },
        }

    async def query(self, label, factory, **kwargs):
        self.query_labels.append(label)
        return factory()

    async def perform(self, label, operation):
        return await operation()

    async def refresh(self):
        self.refresh_count += 1

    async def country_id(self):
        return self.actor

    async def type_id(self, name):
        return TYPES[name]

    def retain(self, content, *, source=False):
        self.next_handle += 1
        self.handles[self.next_handle] = content
        if source:
            self.source_handles.append(self.next_handle)
            self.source_history.append(self.next_handle)
        return self.next_handle

    @staticmethod
    def result(action, *, error=0, value=None, kind=data.Kind.EMPTY, type_id=0, handle=0):
        snapshot = value.encode("utf-8") if isinstance(value, str) else b""
        return NS(action=action, error=error, value=value, kind=kind, type_id=type_id,
                  value_handle=handle, total=len(snapshot), snapshot_bytes=len(snapshot),
                  snapshot=snapshot)

    async def formation_source(self, run, formation_id):
        handle = self.retain(("source", "MilitaryFormation", formation_id), source=True)
        run.values.append(handle)
        self.events.append(("formation_source", formation_id, handle))
        return "army", data.ObjectBinding(TYPES["MilitaryFormation"], handle, retained=True)

    async def war_source(self, run, war_id):
        handle = self.retain(("source", "War", war_id), source=True)
        run.values.append(handle)
        self.events.append(("war_source", war_id, handle))
        country = data.ObjectBinding.country(TYPES["Country"], self.actor)
        war = data.ObjectBinding(TYPES["War"], handle, retained=True)
        return country, war

    def evaluate(self, native, expression, bindings, **kwargs):
        self.expressions.append(expression)
        self.expression_bindings.append((expression, tuple(bindings)))
        if expression == self.unknown_expression:
            raise RuntimeError("original post-observation query outcome unknown")
        producer_prefixes = (
            "MilitaryFormation.SelectSupplyPriority(",
            "MilitaryFormation.RemoveNavalMission",
            "MilitaryFormation.DisembarkFormation",
            "War.ProposePeaceDeal(Country.Self)",
            "War.SetWhitePeace(Country.Self)",
            "War.ToggleAcceptPeace(Country.Self)",
            "War.Capitulate(Country.Self)",
        )
        if expression.startswith(producer_prefixes):
            return self.producer(expression)
        if expression.startswith("MilitaryFormation.IsSupplyPrioritySelected("):
            return self.result(data.Action.EVALUATE, value=self.supply_selected,
                               kind=data.Kind.BOOL, type_id=TYPES["bool"])
        if expression in self.peace_values:
            value = self.peace_values[expression]
            kind = data.Kind.BOOL if type(value) is bool else data.Kind.STRING
            return self.result(data.Action.EVALUATE, value=value, kind=kind,
                               type_id=TYPES["bool"] if kind == data.Kind.BOOL else TYPES["CString"])
        raise AssertionError("unexpected or empty/default producer expression: " + expression)

    def producer(self, expression):
        self.next_pointer += 0x100
        pointer = self.next_pointer
        handle = self.retain(("producer", expression, pointer))
        carrier = data.NativeDataCommand(pointer, pointer + 0x10, pointer + 0x18, handle,
                                         "CDataCommand", self.world)
        self.produced[pointer] = (expression, handle, carrier)
        self.events.append(("producer", expression, pointer))
        return self.result(data.Action.EVALUATE, value=carrier, kind=data.Kind.COMMAND, handle=handle)

    def command_value(self, native, handle, **kwargs):
        kind, expression, pointer = self.handles[handle]
        assert kind == "producer"
        self.events.append(("command_value", expression, pointer))
        return NS(error=0, action=data.Action.COMMAND_VALUE, value=self.produced[pointer][2])

    def request(self, native, action, *, command, world, capacity, **kwargs):
        assert action == commands.Action.CLONE
        assert capacity == 8
        expression, producer_handle, carrier = self.produced[command]
        assert producer_handle in self.handles
        assert all(handle in self.handles for handle in self.source_handles)
        assert world == carrier.generation_world == self.world
        self.next_clone += 0x100
        owner = FakeCommand(eligible=self.default_eligible, accepted=self.default_accepted)
        owner.command = self.next_clone
        if not owner.eligible:
            original_validate = owner.validate
            def validate(capacity):
                result = original_validate(capacity)
                result.reason = self.rejection_reason
                return result
            owner.validate = validate
        self.owners.append(owner)
        self.clone_requests.append((action, command, world, expression))
        self.events.append(("clone", expression, command))
        return NS(error=0, action=action, cloned_command=owner.command, owner=owner)

    def resolve_token(self, native, name, **kwargs):
        self.resolve_names.append(name)
        return NS(error=0, type_token=self.tokens[name])

    def create(self, native, token, *, actor_id, world, parameters, **kwargs):
        owner = FakeCommand(eligible=self.default_eligible, accepted=self.default_accepted)
        owner.command = self.next_clone = self.next_clone + 0x100
        if not owner.eligible:
            original_validate = owner.validate
            def validate(capacity):
                result = original_validate(capacity)
                result.reason = self.rejection_reason
                return result
            owner.validate = validate
        self.owners.append(owner)
        self.create_records.append((token, actor_id, world, parameters))
        return NS(error=0, action=commands.Action.CREATE, command=owner.command, owner=owner)

    def enumerate_functions(self, native, type_id, **kwargs):
        named_type = next(name for name, value in TYPES.items() if value == type_id)
        rows = list(self.fields.get(named_type, {}).values())
        return NS(error=0, rows=rows, total=len(rows), next=len(rows))

    def release(self, native, handle, **kwargs):
        self.events.append(("release", handle))
        self.releases.append(handle)
        del self.handles[handle]
        if handle in self.source_handles:
            self.source_handles.remove(handle)
        return NS(error=0, value_handle=0)

    def patches(self):
        stack = ExitStack()
        stack.enter_context(patch.object(warfare.data, "evaluate", self.evaluate))
        stack.enter_context(patch.object(warfare.data, "command_value", self.command_value))
        stack.enter_context(patch.object(warfare.data, "enumerate_functions", self.enumerate_functions))
        stack.enter_context(patch.object(warfare.data, "release", self.release))
        stack.enter_context(patch.object(warfare.commands, "request", self.request))
        stack.enter_context(patch.object(warfare.commands, "resolve_token", self.resolve_token))
        stack.enter_context(patch.object(warfare.commands, "create", self.create))
        stack.enter_context(patch.object(warfare.commands.NativeCommand, "from_result",
                                         lambda native, result, **kwargs: result.owner))
        return stack


def api_for(fixture):
    api = MilitaryAPI(fixture)
    api._pause_for_action = AsyncMock()
    return api


class CommanderReferenceActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_reference_actions_use_only_canonical_native_fields(self):
        cases = (
            ("assign_commander", (0x09000002, 0x81000003),
             "assign_commander_to_formation", ("character", "formation"),
             {"status": "observed", "formation": {"id": 0x81000003}}),
            ("unassign_commander", (0x09000002,),
             "unassign_commander_from_formation", ("character",),
             {"status": "observed", "has_formation": False}),
            ("reset_commander_order", (0x09000002,),
             "reset_character_order_command", ("character",),
             {"status": "observed"}),
        )
        for public_name, arguments, command_name, fields, observation in cases:
            with self.subTest(action=public_name):
                fixture = ActionFixture()
                api = api_for(fixture)
                status = AsyncMock(return_value=observation)
                with fixture.patches(), patch.object(warfare, "commander_status", status):
                    selected = api.commander(arguments[0])
                    method = {"assign_commander": "assign", "unassign_commander": "unassign",
                              "reset_commander_order": "reset_order"}[public_name]
                    result = await getattr(selected, method)(*arguments[1:])
                self.assertEqual(fixture.resolve_names, [command_name, *fields])
                token, actor, world, parameters = fixture.create_records[0]
                self.assertEqual(token, fixture.tokens[command_name])
                self.assertEqual((actor, world), (fixture.actor, fixture.world))
                expected = {fixture.tokens[name]: commands.UInt32(value)
                            for name, value in zip(fields, arguments)}
                self.assertEqual(parameters, expected)
                self.assertEqual(fixture.owners[0].calls, ["validate", "submit"])
                self.assertTrue(result["accepted"])
                self.assertTrue(result["paused"])
                self.assertFalse(fixture.expressions)
                self.assertFalse(api._warfare_runs)
                api._pause_for_action.assert_awaited_once_with()

    async def test_reference_rejection_never_submits_and_known_owner_is_destroyed(self):
        fixture = ActionFixture(eligible=False)
        api = api_for(fixture)
        status = AsyncMock()
        with fixture.patches(), patch.object(warfare, "commander_status", status):
            result = await api.commander(150994946).unassign()
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], fixture.rejection_reason)
        self.assertEqual(fixture.owners[0].calls, ["validate", "destroy"])
        status.assert_not_awaited()
        self.assertFalse(api._warfare_runs)

    async def test_reference_post_observation_failure_keeps_native_acceptance(self):
        fixture = ActionFixture()
        api = api_for(fixture)
        status = AsyncMock(side_effect=RuntimeError("unknown status source retained by its reader"))
        with fixture.patches(), patch.object(warfare, "commander_status", status):
            result = await api.commander(150994946).assign(2164260867)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertIn("unknown status source", result["observation_error"])
        self.assertEqual(fixture.owners[0].calls, ["validate", "submit"])
        self.assertFalse(api._warfare_runs)


class FormationProducerActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_actions_use_exact_populated_original_producers(self):
        cases = (
            ("set_supply_priority", (0x81000003, "high"),
             "MilitaryFormation.SelectSupplyPriority('high')"),
            ("remove_naval_mission", (0x81000003,), "MilitaryFormation.RemoveNavalMission"),
            ("disembark", (0x81000003,), "MilitaryFormation.DisembarkFormation"),
        )
        for public_name, arguments, expression in cases:
            with self.subTest(action=public_name):
                fixture = ActionFixture()
                api = api_for(fixture)
                details = AsyncMock(return_value={"status": "observed"})
                with fixture.patches(), patch.object(warfare, "_formation", fixture.formation_source),                         patch.object(warfare, "formation_details", details):
                    selected = api.formation(arguments[0])
                    if public_name == "set_supply_priority":
                        result = await selected.set_supply_priority(*arguments[1:])
                    else:
                        result = await getattr(selected.naval, {"remove_naval_mission": "remove_mission",
                                                               "disembark": "disembark"}[public_name])()
                producer_expressions = [item for item in fixture.expressions
                                        if not item.startswith("MilitaryFormation.IsSupplyPrioritySelected")]
                self.assertEqual(producer_expressions, [expression])
                producer_binding = next(bindings for seen, bindings in fixture.expression_bindings
                                        if seen == expression)
                self.assertEqual(len(producer_binding), 1)
                self.assertEqual(producer_binding[0].object, fixture.source_history[0])
                self.assertEqual(fixture.owners[0].calls, ["validate", "submit"])
                self.assertEqual(len(fixture.clone_requests), 1)
                self.assertFalse(fixture.create_records)
                self.assertTrue(result["accepted"])
                self.assertTrue(result["paused"])
                if public_name == "set_supply_priority":
                    self.assertTrue(result["observed_selected"])
                    details.assert_not_awaited()
                else:
                    self.assertEqual(details.await_count, 1)
                    self.assertIs(details.await_args.args[0]._ctx, api._ctx)
                    self.assertEqual(details.await_args.args[1], 0x81000003)
                self.assertFalse(fixture.handles)
                self.assertFalse(api._warfare_runs)

    async def test_rejected_original_producer_is_not_submitted_and_cleans_known_graph(self):
        fixture = ActionFixture(eligible=False)
        api = api_for(fixture)
        details = AsyncMock()
        with fixture.patches(), patch.object(warfare, "_formation", fixture.formation_source),                 patch.object(warfare, "formation_details", details):
            result = await api.formation(2164260867).naval.remove_mission()
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], fixture.rejection_reason)
        self.assertEqual(fixture.owners[0].calls, ["validate", "destroy"])
        details.assert_not_awaited()
        self.assertFalse(fixture.handles)
        self.assertFalse(api._warfare_runs)

    async def test_unknown_supply_postread_preserves_acceptance_and_parent_source_graph(self):
        fixture = ActionFixture()
        api = api_for(fixture)
        fixture.unknown_expression = "MilitaryFormation.IsSupplyPrioritySelected('high')"
        with fixture.patches(), patch.object(warfare, "_formation", fixture.formation_source):
            result = await api.formation(2164260867).set_supply_priority('high')
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertIn("outcome unknown", result["observation_error"])
        self.assertEqual(fixture.owners[0].calls, ["validate", "submit"])
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertFalse(fixture.releases)
        self.assertTrue(fixture.handles)
        self.assertEqual(len(api._warfare_runs), 1)
        self.assertIs(api._warfare_runs[0].public_result, result)


class PeaceActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_peace_options_reads_source_facts_without_creating_or_cloning_commands(self):
        fixture = ActionFixture()
        api = api_for(fixture)
        with fixture.patches(), patch.object(warfare, "_war", fixture.war_source):
            result = await api._call(warfare, "peace_options", 42)
        self.assertEqual(result["status"], "observed")
        self.assertTrue(result["allows_negotiated_peace"])
        self.assertTrue(result["country_is_negotiator"])
        self.assertEqual(result["validation_description"], "Valid terms")
        self.assertFalse(fixture.owners)
        self.assertFalse(fixture.clone_requests)
        self.assertFalse(fixture.create_records)
        self.assertFalse(any("ProposePeaceDeal" in expression or "SetWhitePeace" in expression
                             or "ToggleAcceptPeace" in expression or "Capitulate" in expression
                             for expression in fixture.expressions))
        self.assertFalse(fixture.handles)
        self.assertFalse(api._warfare_runs)
        self.assertEqual(fixture.refresh_count, 1)
        api._pause_for_action.assert_not_awaited()

    async def test_peace_actions_use_exact_original_producer_and_never_claim_settlement(self):
        cases = (
            ("propose_peace", "War.ProposePeaceDeal(Country.Self)"),
            ("set_white_peace", "War.SetWhitePeace(Country.Self)"),
            ("toggle_peace_acceptance", "War.ToggleAcceptPeace(Country.Self)"),
            ("capitulate", "War.Capitulate(Country.Self)"),
        )
        for public_name, expression in cases:
            with self.subTest(action=public_name):
                fixture = ActionFixture()
                api = api_for(fixture)
                observed = {"allows_negotiated_peace": True}
                status = AsyncMock(return_value=observed)
                with fixture.patches(), patch.object(warfare, "_war", fixture.war_source),                         patch.object(warfare, "_peace_status", status):
                    selected = api.war(42).peace
                    result = await getattr(selected, {"propose_peace": "propose", "set_white_peace": "white_peace",
                        "toggle_peace_acceptance": "toggle_acceptance", "capitulate": "capitulate"}[public_name])()
                self.assertEqual(fixture.expressions, [expression])
                bindings = fixture.expression_bindings[0][1]
                self.assertEqual(len(bindings), 2)
                self.assertEqual(bindings[0].object, fixture.source_history[0])
                self.assertEqual(fixture.owners[0].calls, ["validate", "submit"])
                self.assertTrue(result["accepted"])
                self.assertIsNone(result["peace_settled"])
                self.assertEqual(result["observation"], observed)
                if public_name == "set_white_peace":
                    self.assertEqual(result["action"], "set_white_peace")
                    self.assertNotEqual(result.get("status"), "settled")
                self.assertFalse(fixture.handles)
                self.assertFalse(api._warfare_runs)

    async def test_toggle_acceptance_is_not_idempotent_and_each_explicit_call_submits(self):
        fixture = ActionFixture()
        api = api_for(fixture)
        status = AsyncMock(return_value={"country_would_accept_active": True})
        with fixture.patches(), patch.object(warfare, "_war", fixture.war_source),                 patch.object(warfare, "_peace_status", status):
            first = await api.war(42).peace.toggle_acceptance()
            second = await api.war(42).peace.toggle_acceptance()
        self.assertTrue(first["accepted"])
        self.assertTrue(second["accepted"])
        self.assertEqual(len(fixture.owners), 2)
        self.assertTrue(all(owner.calls == ["validate", "submit"] for owner in fixture.owners))
        self.assertEqual(fixture.expressions,
                         ["War.ToggleAcceptPeace(Country.Self)"] * 2)
        self.assertNotEqual(first.get("status"), "unchanged")
        self.assertNotEqual(second.get("status"), "unchanged")
        self.assertFalse(fixture.handles)
        self.assertFalse(api._warfare_runs)

    async def test_rejected_peace_producer_never_submits_and_cleans_known_graph(self):
        fixture = ActionFixture(eligible=False)
        api = api_for(fixture)
        status = AsyncMock()
        with fixture.patches(), patch.object(warfare, "_war", fixture.war_source),                 patch.object(warfare, "_peace_status", status):
            result = await api.war(42).peace.capitulate()
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], fixture.rejection_reason)
        self.assertEqual(fixture.owners[0].calls, ["validate", "destroy"])
        status.assert_not_awaited()
        self.assertFalse(fixture.handles)
        self.assertFalse(api._warfare_runs)

    async def test_unknown_peace_postread_preserves_acceptance_and_parent_source_graph(self):
        fixture = ActionFixture()
        api = api_for(fixture)
        status = AsyncMock(side_effect=RuntimeError("original peace observation outcome unknown"))
        with fixture.patches(), patch.object(warfare, "_war", fixture.war_source),                 patch.object(warfare, "_peace_status", status):
            result = await api.war(42).peace.propose()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertIn("outcome unknown", result["observation_error"])
        self.assertIsNone(result["peace_settled"])
        self.assertEqual(fixture.owners[0].calls, ["validate", "submit"])
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertFalse(fixture.releases)
        self.assertTrue(fixture.handles)
        self.assertEqual(len(api._warfare_runs), 1)
        self.assertIs(api._warfare_runs[0].public_result, result)


if __name__ == "__main__":
    unittest.main()
