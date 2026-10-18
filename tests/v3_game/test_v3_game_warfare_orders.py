"""CPU-only commander-order ownership and command-flow tests."""
from contextlib import ExitStack
from dataclasses import dataclass
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.v3_game.military import _warfare as warfare
from pdx_runtime.v3_game.military import MilitaryAPI


TYPES = {
    "Country": 83,
    "Character": 174,
    "CommanderOrderType": 175,
    "bool": 5,
    "uint32": 6,
    "CString": 0,
}


def function(name, result_type=0, arguments=0):
    return NS(name=name, argument_count=arguments, return_type_id=result_type,
              callbacks=(123, 0, 0, 0))


@dataclass(frozen=True)
class Option:
    key: str
    name: str
    description: str = "fixture description"
    advances: bool = False
    standby: bool = False


class OrderOwner:
    def __init__(self, fixture, key, command):
        self.fixture = fixture
        self.key = key
        self.command = command
        self.world = fixture.world
        self.pending = None
        self.attempt = None
        self.calls = []

    @property
    def behavior(self):
        return self.fixture.behaviors.get(self.key, {})

    def serialize(self):
        self.calls.append("serialize")
        self.fixture.events.append(("serialize", self.key, self.command))
        self.attempt = NS(outcome="unknown" if self.behavior.get("unknown_serialize") else "owned")
        if self.behavior.get("malformed_wire"):
            wire = commands.encode_command(self.fixture.command_type_token,
                                           {self.fixture.unrelated_token: self.key})
        else:
            wire = commands.encode_command(self.fixture.command_type_token,
                                           {self.fixture.order_field_token: self.key})
        return NS(error=self.behavior.get("serialize_error", 0), action=commands.Action.SERIALIZE,
                  data=wire, command=self.command)

    def validate(self, capacity):
        self.calls.append("validate")
        self.fixture.events.append(("validate", self.key, capacity))
        self.attempt = NS(outcome="unknown" if self.behavior.get("unknown_validate") else "owned")
        eligible = self.behavior.get("eligible", True)
        return NS(error=self.behavior.get("validate_error", 0), action=commands.Action.VALIDATE,
                  validated=int(eligible), reason="" if eligible else self.behavior.get(
                      "reason", "Native order is unavailable"))

    def submit(self, flags):
        self.calls.append("submit")
        self.fixture.events.append(("submit", self.key, flags))
        unknown = self.behavior.get("unknown_submit", False)
        accepted = self.behavior.get("accepted", True)
        self.attempt = NS(outcome="unknown" if unknown else "consumed")
        if not unknown:
            self.command = 0
            if accepted and not self.fixture.preserve_current_after_submit:
                self.fixture.current_key = self.key
        return NS(error=self.behavior.get("submit_error", 0), action=commands.Action.SUBMIT,
                  accepted=int(accepted), command=self.command)

    def destroy(self):
        self.calls.append("destroy")
        self.fixture.events.append(("destroy", self.key, self.command))
        self.command = 0
        self.attempt = NS(outcome="consumed")
        return NS(error=0, action=commands.Action.DESTROY, command=0)


class OrderFixture:
    def __init__(self):
        self.native = object()
        self.process = object()
        self.world = 77
        self.actor = 156
        self.character_id = 0x09000002
        self.adapters = {"data": "fake-data", "commands": "fake-commands"}
        self.diagnostics = []
        self.refresh_count = 0
        self.query_labels = []
        self.expressions = []
        self.events = []
        self.releases = []
        self.handles = {}
        self.next_handle = 100
        self.next_pointer = 0x10000
        self.next_clone = 0x90000
        self.produced = {}
        self.owners = []
        self.clone_requests = []
        self.resolve_names = []
        self.command_type_token = 0x501
        self.order_field_token = 0x777
        self.unrelated_token = 0x778
        self.behaviors = {}
        self.current_key = "order_defend"
        self.current_valid = True
        self.preserve_current_after_submit = False
        self.unknown_expression = None
        self.sources = {
            "AccessOrders": [],
            "AccessUnavailableOrders": [],
            "AccessPossibleSpecialOrders": [],
        }
        self.character_fields = {
            "GetID": function("GetID", TYPES["uint32"]),
            "SelectOrder": function("SelectOrder", arguments=1),
            "AccessOrders": function("AccessOrders"),
            "AccessUnavailableOrders": function("AccessUnavailableOrders"),
            "AccessPossibleSpecialOrders": function("AccessPossibleSpecialOrders"),
        }
        self.fields = {
            "Country": {"AccessGenerals": function("AccessGenerals")},
            "Character": self.character_fields,
            "CommanderOrderType": {
                "IsValid": function("IsValid", TYPES["bool"]),
                "GetNameNoFormatting": function("GetNameNoFormatting", TYPES["CString"]),
                "GetDesc": function("GetDesc", TYPES["CString"]),
                "HasAdvanceBehavior": function("HasAdvanceBehavior", TYPES["bool"]),
                "IsStandBy": function("IsStandBy", TYPES["bool"]),
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

    def retain(self, content):
        self.next_handle += 1
        self.handles[self.next_handle] = content
        return self.next_handle

    @staticmethod
    def result(action, *, error=0, value=None, kind=data.Kind.EMPTY, type_id=0, handle=0,
               native_type_name=""):
        snapshot = value.encode("utf-8") if isinstance(value, str) else b""
        return NS(action=action, error=error, value=value, kind=kind, type_id=type_id,
                  native_type_name=native_type_name, value_handle=handle,
                  total=len(snapshot), snapshot_bytes=len(snapshot), snapshot=snapshot)

    def evaluate(self, native, expression, bindings, **kwargs):
        self.expressions.append(expression)
        if "SetOrder" in expression:
            raise AssertionError("the empty default SetOrder factory must never be evaluated")
        if expression == self.unknown_expression:
            raise RuntimeError("original native query outcome unknown")
        if expression == "Country.AccessGenerals":
            handle = self.retain(("list", "Character", [self.character_id]))
            value = data.NativeList(handle, 0x3000, b"", "owned native list")
            return self.result(data.Action.EVALUATE, value=value, kind=data.Kind.LIST, handle=handle)
        if expression == "Character.GetID":
            _, name, identity = self.handles[bindings[0].object]
            assert name == "Character"
            return self.result(data.Action.EVALUATE, value=identity, kind=data.Kind.U32,
                               type_id=TYPES["uint32"])
        if expression.startswith("Character.") and expression.removeprefix("Character.") in self.sources:
            method = expression.removeprefix("Character.")
            handle = self.retain(("list", "CommanderOrderType", list(self.sources[method])))
            value = data.NativeList(handle, 0x3000, b"", "owned native list")
            return self.result(data.Action.EVALUATE, value=value, kind=data.Kind.LIST, handle=handle)
        if expression == "Character.SelectOrder(CommanderOrderType.Self)":
            _, name, option = self.handles[bindings[1].object]
            assert name == "CommanderOrderType"
            return self.producer(option.key)
        if expression == "Character.SelectOrder(Character.GetOrderType)":
            return self.producer(self.current_key)
        if expression == "Character.GetOrderType.IsValid":
            return self.result(data.Action.EVALUATE, value=self.current_valid, kind=data.Kind.BOOL,
                               type_id=TYPES["bool"])
        if expression.startswith("CommanderOrderType."):
            _, name, option = self.handles[bindings[0].object]
            assert name == "CommanderOrderType"
            method = expression.removeprefix("CommanderOrderType.")
            values = {
                "GetNameNoFormatting": (data.Kind.STRING, option.name),
                "GetDesc": (data.Kind.STRING, option.description),
                "HasAdvanceBehavior": (data.Kind.BOOL, option.advances),
                "IsStandBy": (data.Kind.BOOL, option.standby),
            }
            kind, value = values[method]
            return self.result(data.Action.EVALUATE, value=value, kind=kind,
                               type_id=TYPES["CString"] if kind == data.Kind.STRING else TYPES["bool"])
        raise AssertionError("unexpected expression: " + expression)

    def producer(self, key):
        self.next_pointer += 0x100
        pointer = self.next_pointer
        handle = self.retain(("command", key, pointer))
        carrier = data.NativeDataCommand(pointer, pointer + 0x10, pointer + 0x18, handle,
                                         "CDataCommand", self.world)
        self.produced[pointer] = (key, handle, carrier)
        self.events.append(("producer", key, pointer))
        return self.result(data.Action.EVALUATE, value=carrier, kind=data.Kind.COMMAND, handle=handle)

    def list_count(self, native, handle, **kwargs):
        return NS(error=0, total=len(self.handles[handle][2]))

    def list_element(self, native, handle, index, **kwargs):
        _, named_type, entries = self.handles[handle]
        child = self.retain(("object", named_type, entries[index]))
        native_name = ".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@"
        value = data.NativeObject(0x4000 + child, TYPES[named_type], child, native_name)
        return self.result(data.Action.LIST_ELEMENT, value=value, kind=data.Kind.OBJECT,
                           type_id=TYPES[named_type], handle=child, native_type_name=native_name)

    def enumerate_functions(self, native, type_id, **kwargs):
        named_type = next(name for name, value in TYPES.items() if value == type_id)
        rows = list(self.fields.get(named_type, {}).values())
        return NS(error=0, rows=rows, total=len(rows), next=len(rows))

    def command_value(self, native, handle, **kwargs):
        kind, key, pointer = self.handles[handle]
        assert kind == "command"
        carrier = self.produced[pointer][2]
        self.events.append(("command_value", key, pointer))
        return NS(error=0, action=data.Action.COMMAND_VALUE, value=carrier)

    def request(self, native, action, *, command, world, capacity, **kwargs):
        assert action == commands.Action.CLONE
        assert capacity == 8
        key, source_handle, carrier = self.produced[command]
        assert source_handle in self.handles
        assert world == carrier.generation_world == self.world
        self.clone_requests.append((action, command, world))
        self.events.append(("clone", key, command))
        behavior = self.behaviors.get(key, {})
        if behavior.get("clone_error"):
            return NS(error=behavior["clone_error"], action=action, cloned_command=0)
        self.next_clone += 0x100
        owner = OrderOwner(self, key, self.next_clone)
        self.owners.append(owner)
        return NS(error=0, action=action, cloned_command=owner.command, owner=owner)

    def resolve_token(self, native, name, **kwargs):
        self.resolve_names.append(name)
        return NS(error=0, type_token=self.order_field_token)

    def release(self, native, handle, **kwargs):
        self.events.append(("release", handle))
        self.releases.append(handle)
        del self.handles[handle]
        return NS(error=0, value_handle=0)

    def patches(self):
        stack = ExitStack()
        for name in ("evaluate", "list_count", "list_element", "enumerate_functions",
                     "command_value", "release"):
            stack.enter_context(patch.object(warfare.data, name, getattr(self, name)))
        stack.enter_context(patch.object(warfare.commands, "request", self.request))
        stack.enter_context(patch.object(warfare.commands, "resolve_token", self.resolve_token))
        stack.enter_context(patch.object(warfare.commands.NativeCommand, "from_result",
                                         lambda native, result, **kwargs: result.owner))
        return stack


class CommanderOrderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = OrderFixture()
        self.api = MilitaryAPI(self.fixture)
        self.api._pause_for_action = AsyncMock()

    async def test_options_use_owned_native_sources_and_serialized_canonical_keys_only(self):
        self.fixture.sources["AccessOrders"] = [
            Option("order_advance", "Same display name", advances=True),
        ]
        self.fixture.sources["AccessUnavailableOrders"] = [
            Option("order_defend", "Same display name", standby=True),
        ]
        self.fixture.sources["AccessPossibleSpecialOrders"] = [
            Option("order_rapid_advance", "Not the serialized key", advances=True),
        ]

        with self.fixture.patches():
            result = await self.api.commander(self.fixture.character_id).orders()

        self.assertEqual(result["status"], "observed")
        self.assertEqual([row["key"] for row in result["orders"]],
                         ["order_advance", "order_defend", "order_rapid_advance"])
        self.assertEqual([row["available"] for row in result["orders"]], [True, False, None])
        self.assertEqual([row["source"] for row in result["orders"]], [
            "Character.AccessOrders", "Character.AccessUnavailableOrders",
            "Character.AccessPossibleSpecialOrders"])
        self.assertEqual(self.fixture.resolve_names, ["commander_order_type"])
        self.assertEqual(len(self.fixture.clone_requests), 3)
        self.assertTrue(all(owner.calls == ["serialize", "destroy"]
                            for owner in self.fixture.owners))
        self.assertFalse(any(call in ("validate", "submit") for owner in self.fixture.owners
                             for call in owner.calls))
        self.assertFalse(any("SetOrder" in expression or "Tooltip" in expression
                             for expression in self.fixture.expressions))
        for key in ("order_advance", "order_defend", "order_rapid_advance"):
            labels = [event[0] for event in self.fixture.events if len(event) > 1 and event[1] == key]
            self.assertLess(labels.index("producer"), labels.index("command_value"))
            self.assertLess(labels.index("command_value"), labels.index("clone"))
            self.assertLess(labels.index("clone"), labels.index("serialize"))
        self.assertFalse(self.fixture.handles)
        self.assertFalse(self.api._warfare_runs)
        self.api._pause_for_action.assert_not_awaited()

    async def test_unavailable_option_uses_one_native_validation_and_never_submits(self):
        key = "order_unavailable_advance"
        self.fixture.sources["AccessUnavailableOrders"] = [Option(key, "Advance")]
        self.fixture.behaviors[key] = {"eligible": False, "reason": "General cannot advance"}

        with self.fixture.patches():
            result = await self.api.commander(self.fixture.character_id).set_order(key)

        owner = self.fixture.owners[0]
        self.assertEqual(owner.calls, ["serialize", "validate", "destroy"])
        self.assertFalse(result["eligible"])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "General cannot advance")
        self.assertEqual(sum(one.calls.count("validate") for one in self.fixture.owners), 1)
        self.assertEqual(sum(one.calls.count("submit") for one in self.fixture.owners), 0)
        self.assertIn("Character.AccessUnavailableOrders", self.fixture.expressions)
        self.assertFalse(self.fixture.handles)
        self.assertFalse(self.api._warfare_runs)
        self.api._pause_for_action.assert_awaited_once_with()

    async def test_accepted_selected_clone_submits_once_and_mismatch_stays_observation(self):
        target = "order_advance"
        self.fixture.sources["AccessOrders"] = [
            Option("order_defend", "Defend"), Option(target, "Advance")]
        self.fixture.current_key = "order_defend"
        self.fixture.preserve_current_after_submit = True

        with self.fixture.patches():
            result = await self.api.commander(self.fixture.character_id).set_order(target)

        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["observation"], {"key": "order_defend"})
        self.assertFalse(result["observed_order_matches"])
        self.assertEqual(sum(owner.calls.count("validate") for owner in self.fixture.owners), 1)
        self.assertEqual(sum(owner.calls.count("submit") for owner in self.fixture.owners), 1)
        selected = next(owner for owner in self.fixture.owners
                        if owner.key == target and "validate" in owner.calls)
        self.assertEqual(selected.calls, ["serialize", "validate", "submit"])
        self.assertEqual([event for event in self.fixture.events if event[:2] == ("submit", target)],
                         [("submit", target, 4)])
        self.assertFalse(self.fixture.handles)
        self.assertFalse(self.api._warfare_runs)

    async def test_known_postread_clone_failure_keeps_acceptance_and_completes_cleanup(self):
        target = "order_advance"
        self.fixture.sources["AccessOrders"] = [Option(target, "Advance")]
        self.fixture.current_key = "order_broken_current"
        self.fixture.preserve_current_after_submit = True
        self.fixture.behaviors[self.fixture.current_key] = {"clone_error": 21}

        with self.fixture.patches():
            result = await self.api.commander(self.fixture.character_id).set_order(target)

        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertIn("native error 21", result["observation_error"])
        self.assertEqual(sum(owner.calls.count("submit") for owner in self.fixture.owners), 1)
        self.assertFalse(self.fixture.handles)
        self.assertFalse(self.api._warfare_runs)

    async def test_unknown_postread_query_keeps_accepted_result_and_original_source_graph(self):
        target = "order_advance"
        self.fixture.sources["AccessOrders"] = [Option(target, "Advance")]
        self.fixture.preserve_current_after_submit = True
        self.fixture.unknown_expression = "Character.SelectOrder(Character.GetOrderType)"

        with self.fixture.patches():
            result = await self.api.commander(self.fixture.character_id).set_order(target)

        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertIn("outcome unknown", result["observation_error"])
        self.assertEqual(sum(owner.calls.count("submit") for owner in self.fixture.owners), 1)
        self.assertFalse(self.fixture.releases)
        self.assertTrue(self.fixture.handles)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(len(self.api._warfare_runs), 1)
        self.assertIs(self.api._warfare_runs[0].public_result, result)

    async def test_unknown_serialization_owner_retains_clone_and_complete_source_graph(self):
        key = "order_advance"
        self.fixture.sources["AccessOrders"] = [Option(key, "Advance")]
        self.fixture.behaviors[key] = {"unknown_serialize": True}

        with self.fixture.patches():
            result = await self.api.commander(self.fixture.character_id).orders()

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["orders"], [])
        self.assertIn("ownership is unresolved", result["details"][0])
        self.assertEqual(self.fixture.owners[0].calls, ["serialize"])
        self.assertFalse(self.fixture.releases)
        self.assertTrue(self.fixture.handles)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(len(self.api._warfare_runs), 1)

    async def test_unknown_submit_is_returned_once_without_postread_replay_or_cleanup(self):
        key = "order_advance"
        self.fixture.sources["AccessOrders"] = [Option(key, "Advance")]
        self.fixture.behaviors[key] = {"unknown_submit": True}

        with self.fixture.patches():
            result = await self.api.commander(self.fixture.character_id).set_order(key)

        owner = self.fixture.owners[0]
        self.assertEqual(owner.calls, ["serialize", "validate", "submit"])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["native_outcome"], "unknown")
        self.assertNotIn("observation", result)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(sum(one.calls.count("submit") for one in self.fixture.owners), 1)
        self.assertFalse(self.fixture.releases)
        self.assertTrue(self.fixture.handles)
        self.assertEqual(len(self.api._warfare_runs), 1)

    async def test_missing_serialized_definition_key_is_known_failure_with_full_cleanup(self):
        key = "display_name_must_not_be_used_as_key"
        self.fixture.sources["AccessOrders"] = [Option(key, key)]
        self.fixture.behaviors[key] = {"malformed_wire": True}

        with self.fixture.patches():
            result = await self.api.commander(self.fixture.character_id).orders()

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["orders"], [])
        self.assertIn("could not be decoded", result["details"][0])
        self.assertEqual(self.fixture.owners[0].calls, ["serialize", "destroy"])
        self.assertFalse(any(call in ("validate", "submit") for owner in self.fixture.owners
                             for call in owner.calls))
        self.assertFalse(self.fixture.handles)
        self.assertFalse(self.api._warfare_runs)


if __name__ == "__main__":
    unittest.main()
