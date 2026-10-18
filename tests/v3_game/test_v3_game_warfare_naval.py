"""CPU-only tests for native-owned naval mission modes and assigned areas.

The native ownership proof covered here is specific to NavalMissionType. These
fixtures do not infer or transfer that proof to CommanderOrderType.
"""
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
    "MilitaryFormation": 37,
    "NavalMissionType": 177,
    "NavalMission": 178,
    "bool": 5,
    "int32": 4,
    "CString": 0,
}


def function(name, result_type=0, arguments=0):
    return NS(name=name, argument_count=arguments, return_type_id=result_type,
              callbacks=(123, 0, 0, 0))


@dataclass(frozen=True)
class Mission:
    key: str
    name: str
    description: str = "fixture mission"
    selected: bool = False


class NavalOwner:
    def __init__(self, fixture, mission, command):
        self.fixture = fixture
        self.mission = mission
        self.command = command
        self.world = fixture.world
        self.pending = None
        self.attempt = None
        self.calls = []

    @property
    def behavior(self):
        return self.fixture.behaviors.get(self.mission.key, {})

    def serialize(self):
        self.calls.append("serialize")
        self.fixture.events.append(("serialize", self.mission.key, self.command))
        self.attempt = NS(outcome="unknown" if self.behavior.get("unknown_serialize") else "owned")
        token = (self.fixture.unrelated_token if self.behavior.get("malformed_wire")
                 else self.fixture.naval_field_token)
        wire = commands.encode_command(self.fixture.command_type_token, {token: self.mission.key})
        return NS(error=self.behavior.get("serialize_error", 0), action=commands.Action.SERIALIZE,
                  data=wire, command=self.command)

    def validate(self, capacity):
        self.calls.append("validate")
        self.fixture.events.append(("validate", self.mission.key, capacity))
        self.attempt = NS(outcome="owned")
        eligible = self.behavior.get("eligible", True)
        return NS(error=0, action=commands.Action.VALIDATE, validated=int(eligible),
                  reason="" if eligible else self.behavior.get("reason", "Mission unavailable"))

    def submit(self, flags):
        self.calls.append("submit")
        self.fixture.events.append(("submit", self.mission.key, flags))
        accepted = self.behavior.get("accepted", True)
        unknown = self.behavior.get("unknown_submit", False)
        self.attempt = NS(outcome="unknown" if unknown else "consumed")
        if not unknown:
            self.command = 0
        return NS(error=0, action=commands.Action.SUBMIT, accepted=int(accepted), command=self.command)

    def destroy(self):
        self.calls.append("destroy")
        self.fixture.events.append(("destroy", self.mission.key, self.command))
        self.command = 0
        self.attempt = NS(outcome="consumed")
        return NS(error=0, action=commands.Action.DESTROY, command=0)


class NavalFixture:
    def __init__(self):
        self.native = object()
        self.process = object()
        self.world = 77
        self.actor = 156
        self.fleet_id = 0x82000003
        self.adapters = {"data": "fake-data", "commands": "fake-commands"}
        self.diagnostics = []
        self.refresh_count = 0
        self.query_labels = []
        self.expressions = []
        self.expression_bindings = []
        self.events = []
        self.releases = []
        self.handles = {}
        self.next_handle = 100
        self.next_pointer = 0x20000
        self.next_clone = 0xA0000
        self.source_handles = []
        self.source_history = []
        self.missions = []
        self.behaviors = {}
        self.produced = {}
        self.owners = []
        self.clone_requests = []
        self.resolve_names = []
        self.command_type_token = 0x601
        self.naval_field_token = 0x779
        self.unrelated_token = 0x780
        self.unknown_expression = None
        self.has_mission = True
        self.region_failure = None
        self.assigned_regions = [
            {"id": 17, "tooltip_tag": "state_region,baltic",
             "name": "#bold Baltic Sea#!"},
            {"id": 23, "tooltip_tag": "state_region,north",
             "name": "North Sea"},
        ]
        self.status_values = {
            "MilitaryFormation.GetNavalMission.GetNameNoFormatting": "#bold Patrol Baltic#!",
            "MilitaryFormation.GetNavalMission.GetNumSeaRegions": 2,
            "MilitaryFormation.GetNavalMission.GetMaxSeaRegions": 3,
            "MilitaryFormation.GetNavalMission.GetSeaRegionBreakdown": "2 / 3 regions",
            "MilitaryFormation.GetNavalMission.GetType.GetNameNoFormatting": "#italic Patrol#!",
        }
        self.fields = {
            "MilitaryFormation": {
                "HasNavalMission": function("HasNavalMission", TYPES["bool"]),
                "HasNavalMissionType": function("HasNavalMissionType", TYPES["bool"], 1),
            },
            "NavalMissionType": {
                "GetNameNoFormatting": function("GetNameNoFormatting", TYPES["CString"]),
                "GetDesc": function("GetDesc", TYPES["CString"]),
            },
            "NavalMission": {
                "GetNameNoFormatting": function("GetNameNoFormatting", TYPES["CString"]),
                "GetNumSeaRegions": function("GetNumSeaRegions", TYPES["int32"]),
                "GetMaxSeaRegions": function("GetMaxSeaRegions", TYPES["int32"]),
                "GetSeaRegionBreakdown": function("GetSeaRegionBreakdown", TYPES["CString"]),
            },
        }
        self.fields["NavalMissionType"].setdefault(
            "GetNameNoFormatting", function("GetNameNoFormatting", TYPES["CString"]))

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
    def result(action, *, error=0, value=None, kind=data.Kind.EMPTY, type_id=0, handle=0,
               native_type_name=""):
        snapshot = value.encode("utf-8") if isinstance(value, str) else b""
        return NS(action=action, error=error, value=value, kind=kind, type_id=type_id,
                  value_handle=handle, native_type_name=native_type_name,
                  total=len(snapshot), snapshot_bytes=len(snapshot), snapshot=snapshot)

    async def fleet_source(self, run, fleet_id, *, kind=None):
        assert kind == "fleet"
        handle = self.retain(("source", "MilitaryFormation", fleet_id), source=True)
        native_name = ".?AU?$SAnyType@V?$CPdxPtr@VCDataMilitaryFormation@@@@@PdxAnyImpl@@"
        source = self.result(data.Action.EVALUATE,
            value=data.NativeObject(0x5000 + handle, TYPES["MilitaryFormation"], handle, native_name),
            kind=data.Kind.OBJECT, type_id=TYPES["MilitaryFormation"], handle=handle,
            native_type_name=native_name)
        await run.capture("fixture.fleet_source", lambda: source)
        self.events.append(("fleet_source", fleet_id, handle))
        return "fleet", data.ObjectBinding(TYPES["MilitaryFormation"], handle, retained=True)

    def evaluate(self, native, expression, bindings, **kwargs):
        self.expressions.append(expression)
        self.expression_bindings.append((expression, tuple(bindings)))
        if expression == self.unknown_expression:
            raise RuntimeError("original naval observation outcome unknown")
        if expression == "MilitaryFormation.GetPotentialMissionsTypes":
            handle = self.retain(("list", "NavalMissionType", list(self.missions)), source=True)
            value = data.NativeList(handle, 0x3000, b"", "owned mode3 native list")
            return self.result(data.Action.EVALUATE, value=value, kind=data.Kind.LIST, handle=handle)
        if expression in (
                "MilitaryFormation.CreateNavalMission(NavalMissionType.AccessSelf)",
                "MilitaryFormation.CreateNavalMissionWithPreviousArea(NavalMissionType.AccessSelf)"):
            _, named_type, mission = self.handles[bindings[1].object]
            assert named_type == "NavalMissionType"
            return self.producer(expression, mission)
        if "NavalMissionType.Self" in expression and "AccessSelf" not in expression:
            raise AssertionError("mutable NavalMissionType producer must use AccessSelf, not Self")
        if expression == "MilitaryFormation.HasNavalMissionType(NavalMissionType.AccessSelf)":
            _, named_type, mission = self.handles[bindings[1].object]
            assert named_type == "NavalMissionType"
            return self.result(data.Action.EVALUATE, value=mission.selected, kind=data.Kind.BOOL,
                               type_id=TYPES["bool"])
        if expression.startswith("NavalMissionType."):
            _, named_type, mission = self.handles[bindings[0].object]
            assert named_type == "NavalMissionType"
            method = expression.removeprefix("NavalMissionType.")
            value = mission.name if method == "GetNameNoFormatting" else mission.description
            return self.result(data.Action.EVALUATE, value=value, kind=data.Kind.STRING,
                               type_id=TYPES["CString"])
        if expression == "MilitaryFormation.HasNavalMission":
            return self.result(data.Action.EVALUATE, value=self.has_mission, kind=data.Kind.BOOL,
                               type_id=TYPES["bool"])
        if expression in self.status_values:
            value = self.status_values[expression]
            kind = data.Kind.I32 if type(value) is int else data.Kind.STRING
            return self.result(data.Action.EVALUATE, value=value, kind=kind,
                               type_id=TYPES["int32"] if kind == data.Kind.I32 else TYPES["CString"])
        raise AssertionError("unexpected or default/empty naval factory expression: " + expression)

    def producer(self, expression, mission):
        self.next_pointer += 0x100
        pointer = self.next_pointer
        handle = self.retain(("producer", expression, mission, pointer))
        carrier = data.NativeDataCommand(pointer, pointer + 0x10, pointer + 0x18, handle,
                                         "CDataCommand", self.world)
        self.produced[pointer] = (expression, mission, handle, carrier)
        self.events.append(("producer", mission.key, expression, pointer))
        return self.result(data.Action.EVALUATE, value=carrier, kind=data.Kind.COMMAND, handle=handle)

    def list_count(self, native, handle, **kwargs):
        return NS(error=0, total=len(self.handles[handle][2]))

    def list_element(self, native, handle, index, **kwargs):
        _, named_type, entries = self.handles[handle]
        mission = entries[index]
        child = self.retain(("object", named_type, mission), source=True)
        native_name = ".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@"
        value = data.NativeObject(0x5000 + child, TYPES[named_type], child, native_name)
        self.events.append(("owned_mode3_element", mission.key, child))
        return self.result(data.Action.LIST_ELEMENT, value=value, kind=data.Kind.OBJECT,
                           type_id=TYPES[named_type], handle=child,
                           native_type_name=native_name)

    def command_value(self, native, handle, **kwargs):
        kind, expression, mission, pointer = self.handles[handle]
        assert kind == "producer"
        self.events.append(("command_value", mission.key, pointer))
        return NS(error=0, action=data.Action.COMMAND_VALUE, value=self.produced[pointer][3])

    def request(self, native, action, *, command, world, capacity, **kwargs):
        assert action == commands.Action.CLONE
        assert capacity == 8
        expression, mission, producer_handle, carrier = self.produced[command]
        assert producer_handle in self.handles
        assert all(handle in self.handles for handle in self.source_handles)
        assert world == carrier.generation_world == self.world
        behavior = self.behaviors.get(mission.key, {})
        if behavior.get("clone_error"):
            return NS(error=behavior["clone_error"], action=action, cloned_command=0)
        self.next_clone += 0x100
        owner = NavalOwner(self, mission, self.next_clone)
        self.owners.append(owner)
        self.clone_requests.append((action, command, world, expression))
        self.events.append(("clone", mission.key, command))
        return NS(error=0, action=action, cloned_command=owner.command, owner=owner)

    def resolve_token(self, native, name, **kwargs):
        self.resolve_names.append(name)
        assert name == "naval_mission_type"
        return NS(error=0, type_token=self.naval_field_token)

    def create_forbidden(self, *args, **kwargs):
        raise AssertionError("naval mission producers must not use an empty generic Create or encode sea_nodes")

    def enumerate_functions(self, native, type_id, **kwargs):
        named_type = next(name for name, value in TYPES.items() if value == type_id)
        rows = list(self.fields.get(named_type, {}).values())
        return NS(error=0, rows=rows, total=len(rows), next=len(rows))

    def source_count(self, native, source, bindings, **kwargs):
        assert source == "MilitaryFormation.GetNavalMissionSeaRegions"
        self.events.append(("assigned_region_source", source, len(self.assigned_regions)))
        if self.region_failure:
            return NS(error=self.region_failure, count_valid=False, source_count=0)
        return NS(error=0, count_valid=True, source_count=len(self.assigned_regions))

    def evaluate_element(self, native, source, index, expression, bindings, **kwargs):
        assert source == "MilitaryFormation.GetNavalMissionSeaRegions"
        assert kwargs["expected_count"] == len(self.assigned_regions)
        region = self.assigned_regions[index]
        values = {
            "StateRegion.GetID": (data.Kind.I32, region["id"]),
            "StateRegion.GetTooltipTag": (data.Kind.STRING, region["tooltip_tag"]),
            "StateRegion.GetNameNoFormatting": (data.Kind.STRING, region["name"]),
        }
        kind, value = values[expression]
        native_result = self.result(data.Action.EVALUATE_ELEMENT, value=value, kind=kind,
                                    type_id=TYPES["int32"] if kind == data.Kind.I32 else TYPES["CString"])
        return NS(error=0, action=data.Action.EVALUATE_ELEMENT, result=native_result, value=value)

    def release(self, native, handle, **kwargs):
        self.events.append(("release", handle))
        self.releases.append(handle)
        del self.handles[handle]
        if handle in self.source_handles:
            self.source_handles.remove(handle)
        return NS(error=0, value_handle=0)

    def patches(self):
        stack = ExitStack()
        for name in ("evaluate", "list_count", "list_element", "command_value",
                     "enumerate_functions", "source_count", "evaluate_element", "release"):
            stack.enter_context(patch.object(warfare.data, name, getattr(self, name)))
        stack.enter_context(patch.object(warfare.commands, "request", self.request))
        stack.enter_context(patch.object(warfare.commands, "resolve_token", self.resolve_token))
        stack.enter_context(patch.object(warfare.commands, "create", self.create_forbidden))
        stack.enter_context(patch.object(warfare.commands.NativeCommand, "from_result",
                                         lambda native, result, **kwargs: result.owner))
        stack.enter_context(patch.object(warfare, "_formation", self.fleet_source))
        return stack


def api_for(fixture):
    api = MilitaryAPI(fixture)
    api._pause_for_action = AsyncMock()
    return api


class NavalMissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_options_use_original_owned_potential_source_and_accessself_producers(self):
        fixture = NavalFixture()
        fixture.missions = [
            Mission("naval_mission_patrol", "#bold Patrol#!", selected=True),
            Mission("naval_mission_escort", "Escort Convoys"),
        ]
        api = api_for(fixture)
        with fixture.patches():
            result = await api.formation(fixture.fleet_id).naval.missions()
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["coverage"], "native_potential_missions")
        self.assertIsNone(result["eligible"])
        self.assertEqual([row["key"] for row in result["missions"]],
                         ["naval_mission_patrol", "naval_mission_escort"])
        self.assertEqual([row["selected"] for row in result["missions"]], [True, False])
        self.assertTrue(all(row["eligible"] is None for row in result["missions"]))
        self.assertNotIn("available_targets", result)
        self.assertIn("MilitaryFormation.GetPotentialMissionsTypes", fixture.expressions)
        self.assertEqual([expression for expression in fixture.expressions
                          if "CreateNavalMission" in expression],
                         ["MilitaryFormation.CreateNavalMission(NavalMissionType.AccessSelf)"] * 2)
        self.assertIn("MilitaryFormation.HasNavalMissionType(NavalMissionType.AccessSelf)",
                      fixture.expressions)
        self.assertFalse(any("NavalMissionType.Self" in expression and "AccessSelf" not in expression
                             for expression in fixture.expressions))
        self.assertEqual(fixture.resolve_names, ["naval_mission_type"])
        self.assertEqual(len([event for event in fixture.events if event[0] == "owned_mode3_element"]), 2)
        self.assertTrue(all(owner.calls == ["serialize", "destroy"] for owner in fixture.owners))
        self.assertFalse(any(call in ("validate", "submit") for owner in fixture.owners
                             for call in owner.calls))
        self.assertFalse(fixture.handles)
        self.assertFalse(api._warfare_runs)
        api._pause_for_action.assert_not_awaited()

    async def test_plain_create_and_default_previous_area_validate_only_selected_clone(self):
        cases = (
            (False, False, "MilitaryFormation.CreateNavalMission(NavalMissionType.AccessSelf)"),
            (None, True, "MilitaryFormation.CreateNavalMissionWithPreviousArea(NavalMissionType.AccessSelf)"),
        )
        for argument, expected_preserve, expression in cases:
            with self.subTest(preserve_area=argument):
                fixture = NavalFixture()
                fixture.missions = [Mission("naval_mission_patrol", "Patrol"),
                                    Mission("naval_mission_escort", "Escort")]
                api = api_for(fixture)
                status = AsyncMock(return_value={"has_mission": True, "sea_regions": []})
                with fixture.patches(), patch.object(warfare, "_naval_status", status):
                    if argument is None:
                        result = await api.formation(fixture.fleet_id).naval.set_mission('naval_mission_escort')
                    else:
                        result = await api.formation(fixture.fleet_id).naval.set_mission('naval_mission_escort', preserve_area=argument)
                producers = [seen for seen in fixture.expressions if "CreateNavalMission" in seen]
                self.assertEqual(producers, [expression, expression])
                self.assertEqual(fixture.owners[0].calls, ["serialize", "destroy"])
                self.assertEqual(fixture.owners[1].calls, ["serialize", "validate", "submit"])
                self.assertEqual(sum(owner.calls.count("validate") for owner in fixture.owners), 1)
                self.assertEqual(sum(owner.calls.count("submit") for owner in fixture.owners), 1)
                self.assertTrue(result["accepted"])
                self.assertIsNone(result["mission_effective"])
                self.assertEqual(result["preserve_area"], expected_preserve)
                self.assertNotIn("sea_nodes", result)
                self.assertFalse(fixture.handles)
                self.assertFalse(api._warfare_runs)

    async def test_rejected_selected_mission_never_submits_and_cleans_known_graph(self):
        fixture = NavalFixture()
        key = "naval_mission_blockade"
        fixture.missions = [Mission(key, "Blockade")]
        fixture.behaviors[key] = {"eligible": False, "reason": "No valid assigned sea area"}
        api = api_for(fixture)
        with fixture.patches():
            result = await api.formation(fixture.fleet_id).naval.set_mission(key)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "No valid assigned sea area")
        self.assertEqual(fixture.owners[0].calls, ["serialize", "validate", "destroy"])
        self.assertFalse(fixture.handles)
        self.assertFalse(api._warfare_runs)

    async def test_status_projects_only_assigned_regions_without_retyping_index_or_id(self):
        fixture = NavalFixture()
        api = api_for(fixture)
        with fixture.patches():
            result = await api.formation(fixture.fleet_id).naval.mission()
        self.assertEqual(result["status"], "observed")
        self.assertTrue(result["has_mission"])
        self.assertEqual(result["area_coverage"], "current_mission_area_only")
        self.assertEqual(result["name"], "Patrol Baltic")
        self.assertEqual(result["name_raw"], "#bold Patrol Baltic#!")
        self.assertEqual(result["type_name"], "Patrol")
        self.assertEqual(result["type_name_raw"], "#italic Patrol#!")
        self.assertEqual(result["sea_region_count"], 2)
        self.assertEqual(result["max_sea_regions"], 3)
        self.assertEqual(result["sea_regions"], [
            {"id": 17, "tooltip_tag": "state_region,baltic", "name": "Baltic Sea",
             "name_raw": "#bold Baltic Sea#!", "unavailable": {}},
            {"id": 23, "tooltip_tag": "state_region,north", "name": "North Sea",
             "name_raw": "North Sea", "unavailable": {}},
        ])
        self.assertNotIn("available_targets", result)
        self.assertNotIn("sea_nodes", result)
        self.assertFalse(fixture.owners)
        self.assertFalse(any("GetPotentialMissionsTypes" in expression for expression in fixture.expressions))
        self.assertFalse(fixture.handles)
        self.assertFalse(api._warfare_runs)

    async def test_known_option_failure_keeps_observed_rows_as_partial_and_cleans_graph(self):
        fixture = NavalFixture()
        fixture.missions = [Mission("naval_mission_patrol", "Patrol"),
                            Mission("naval_mission_broken", "Broken")]
        fixture.behaviors["naval_mission_broken"] = {"serialize_error": 21}
        api = api_for(fixture)
        with fixture.patches():
            result = await api.formation(fixture.fleet_id).naval.missions()
        self.assertEqual(result["status"], "partial")
        self.assertEqual([row["key"] for row in result["missions"]], ["naval_mission_patrol"])
        self.assertIn("native error 21", result["details"][0])
        self.assertEqual(fixture.owners[1].calls, ["serialize", "destroy"])
        self.assertFalse(fixture.handles)
        self.assertFalse(api._warfare_runs)

    async def test_unknown_option_work_retains_complete_graph_without_replay(self):
        fixture = NavalFixture()
        key = "naval_mission_patrol"
        fixture.missions = [Mission(key, "Patrol")]
        fixture.behaviors[key] = {"unknown_serialize": True}
        api = api_for(fixture)
        with fixture.patches():
            result = await api.formation(fixture.fleet_id).naval.missions()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["missions"], [])
        self.assertIn("ownership is unresolved", result["details"][0])
        self.assertEqual(fixture.owners[0].calls, ["serialize"])
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertFalse(fixture.releases)
        self.assertTrue(fixture.handles)
        self.assertEqual(len(api._warfare_runs), 1)

        query_fixture = NavalFixture()
        query_fixture.missions = [Mission(key, "Patrol")]
        query_fixture.unknown_expression = (
            "MilitaryFormation.CreateNavalMission(NavalMissionType.AccessSelf)")
        query_api = api_for(query_fixture)
        with query_fixture.patches(), self.assertRaisesRegex(
                RuntimeError, "original naval observation outcome unknown"):
            await query_api.formation(query_fixture.fleet_id).naval.missions()
        self.assertFalse(query_fixture.owners)
        self.assertFalse(query_fixture.releases)
        self.assertTrue(query_fixture.handles)
        self.assertEqual(len(query_api._warfare_runs), 1)

    async def test_unknown_post_observation_preserves_acceptance_and_parent_graph(self):
        fixture = NavalFixture()
        key = "naval_mission_patrol"
        fixture.missions = [Mission(key, "Patrol")]
        api = api_for(fixture)
        status = AsyncMock(side_effect=RuntimeError("original naval status outcome unknown"))
        with fixture.patches(), patch.object(warfare, "_naval_status", status):
            result = await api.formation(fixture.fleet_id).naval.set_mission(key)
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["mission_effective"])
        self.assertIn("outcome unknown", result["observation_error"])
        self.assertEqual(fixture.owners[0].calls, ["serialize", "validate", "submit"])
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertFalse(fixture.releases)
        self.assertTrue(fixture.handles)
        self.assertEqual(len(api._warfare_runs), 1)
        self.assertIs(api._warfare_runs[0].public_result, result)


if __name__ == "__main__":
    unittest.main()
