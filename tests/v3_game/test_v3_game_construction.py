"""CPU tests. No process connection, DLL load or native gameplay."""
from __future__ import annotations

from decimal import Decimal
from dataclasses import fields
import asyncio
from types import SimpleNamespace as NS
import struct
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_commands as commands, v3_data as data, v3_objects as objects
from pdx_runtime.v3_game.construction import ConstructionAPI


class Query:
    def __init__(self, result, finish=None):
        self.result, self.finish = result, finish
    def read(self):
        if self.finish:
            finish, self.finish = self.finish, None
            finish()
        return self.result


class Context:
    def __init__(self):
        self.native, self.session = object(), NS()
        self.world, self.image_base, self.interface_thread_id = 70000, 80000, 123
        self.adapters = {name: name + "-adapter" for name in ("data", "objects", "commands")}
        self.diagnostics, self.queries = [], []
        self.process = NS(read=lambda address, size: struct.pack("<Q", 0x55500))
    async def refresh(self):
        return NS(world=self.world)
    async def country_id(self):
        return 156
    async def type_id(self, name):
        return {"Country": 99, "Building": 103, "BuildingType": 207, "State": 88}[name]
    async def query(self, label, factory, owner=None):
        query = factory()
        self.queries.append((label, query, owner))
        return owner.complete() if owner else query.read()
    async def perform(self, label, operation):
        return await operation()
    async def blocking(self, function, *args, **kwargs):
        return function(*args, **kwargs)


class Owner:
    def __init__(self, ctx, token, actor, parameters, valid=True, accepted=True):
        self.ctx, self.type_token, self.world = ctx, token, ctx.world
        self.actor, self.parameters = actor, parameters
        self.command, self.pending, self.attempt = 0x12300, None, None
        self.calls, self.valid, self.accepted = [], valid, accepted
        self.validation_capacities = []
    def _query(self, action, result, consumed=False):
        self.calls.append(action)
        def finish():
            self.pending = None
            self.attempt = NS(outcome="consumed" if consumed else "owned")
            if consumed:
                self.command = 0
        self.attempt = NS(outcome="pending")
        self.pending = Query(result, finish)
        return self.pending
    def complete(self):
        return self.pending.read()
    def serialize(self):
        return self._query("serialize", NS(error=0, data=commands.encode_command(self.type_token,
            {10304: commands.UInt32(self.actor), 65: self.parameters})))
    def validate(self, capacity):
        self.validation_capacities.append(capacity)
        reason = getattr(self.ctx, "validation_reason", "" if self.valid or not capacity else "Not enough available resource levels")
        return self._query("validate", NS(error=0, validated=int(self.valid),
            reason=reason, command=self.command))
    def submit(self, flags):
        self.flags = flags
        return self._query("submit", NS(error=0, accepted=int(self.accepted), command=0), True)
    def destroy(self):
        return self._query("destroy", NS(error=0, command=0), True)


class ConstructionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = Context()
        self.api = ConstructionAPI(self.ctx)
        self.owners, self.creates = [], []
        self.valid, self.accepted = True, True
        def create(native, token, *, parameters, actor_id, adapter, process):
            self.creates.append((native, token, parameters, actor_id, adapter, process))
            owner = Owner(self.ctx, token, actor_id, parameters, self.valid, self.accepted)
            self.owners.append(owner)
            return Query(NS(error=0, owner=owner, command=owner.command,
                world=owner.world, type_token=owner.type_token))
        self.create_patch = patch("pdx_runtime.v3_game.construction.commands.create", side_effect=create)
        self.owner_patch = patch("pdx_runtime.v3_game.construction.commands.NativeCommand.from_result",
                                 side_effect=lambda native, result, **kw: result.owner)
        self.create_patch.start()
        self.owner_patch.start()
        self.addCleanup(self.create_patch.stop)
        self.addCleanup(self.owner_patch.stop)
        self.api._potential_options = AsyncMock(return_value=[])
        self.api._all_type_options = AsyncMock(return_value=[])
        self.api._costs = AsyncMock(return_value={"country_id": 156,
            "scope": "current national building construction"})
        self.api._fresh_candidate_details = AsyncMock(return_value=None)
        self.api._post_build = AsyncMock(return_value={"scope": "bounded native queue count"})
    async def test_build_key_and_levels_are_native_reader_parameters(self):
        self.api._objects = AsyncMock(side_effect=AssertionError("no world join"))
        self.api._post_build = ConstructionAPI._post_build.__get__(self.api)
        count = queue_result(action=data.Action.EVALUATE, kind=data.Kind.I32, raw_bits=13,
            value_handle=300, snapshot=struct.pack("<i", 13), snapshot_bytes=4, total=4)
        with patch.object(data, "evaluate", return_value=Query(count)) as read, \
             patch.object(data, "release", return_value=Query(queue_result(action=data.Action.RELEASE))):
            result = await self.api.build(state=13, building="building_iron_mine", levels=3)
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["post_observation"]["government_queue_count"]["value"], 13)
        native, token, params, actor, adapter, process = self.creates[0]
        self.assertEqual(len(self.creates), 1)
        self.assertEqual((token, actor), (12490, 156))
        self.assertEqual(params, {439: commands.UInt32(13), 10232: "building_iron_mine",
            219: {10304: commands.UInt32(156)}, 22805: 3, 22308: commands.Token(603)})
        self.assertEqual(self.owners[0].calls, ["validate", "submit"])
        self.assertEqual(self.owners[0].flags, 4)
        self.assertEqual(self.owners[0].command, 0)
        self.assertEqual(read.call_args.args[1], "Country.GetNumGovernmentConstructionsInQueue")
        self.api._objects.assert_not_awaited()
        self.api._costs.assert_not_awaited()
        self.api._fresh_candidate_details.assert_not_awaited()

    async def test_acceptance_does_not_claim_observed_queue_or_completion(self):
        result = await self.api.build(13, "building_university")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(self.owners[0].calls, ["validate", "submit"])

    async def test_native_rejection_returns_game_reason_and_releases_owner(self):
        self.valid = False
        self.ctx.validation_reason = ""
        result = await self.api.build(13, "building_iron_mine")
        self.assertIs(result["eligible"], False)
        self.assertIs(result["accepted"], False)
        self.assertEqual(result["reason"], "")
        self.assertEqual(result["reason_raw"], "")
        self.assertEqual(self.owners[0].validation_capacities, [0])
        self.assertEqual(self.owners[0].calls, ["validate", "destroy"])
        self.api._post_build.assert_not_awaited()

    async def test_receiver_rejection_is_not_replayed(self):
        self.accepted = False
        result = await self.api.build(13, "building_iron_mine")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "receiver_rejected")
        self.assertEqual(self.owners[0].calls, ["validate", "submit"])

    async def test_ordinary_arguments_reject_pointer_and_bool_quantity(self):
        for args in ((13, 0x55500, 1), (13, "building_iron_mine", True),
                     (True, "building_iron_mine", 1), (13, "building_iron_mine", 0)):
            with self.assertRaises(ValueError):
                await self.api.build(*args)
        self.assertEqual(self.creates, [])

    async def test_estimate_validates_quantity_without_enqueuing(self):
        self.api._objects = AsyncMock(side_effect=AssertionError("no world Building scan"))
        self.api._fresh_candidate_details = AsyncMock(return_value={"building_key": "building_iron_mine",
            "required_construction": Decimal(200), "existing_levels": 4, "maximum_levels": 40, "name": "Iron Mines"})
        result = await self.api.estimate(13, "building_iron_mine", 2)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["required_construction"], Decimal(400))
        self.assertEqual(result["existing_levels"], 4)
        self.assertEqual(result["maximum_levels"], 40)
        self.assertEqual(len(self.creates), 1)
        self.assertEqual(self.owners[0].calls, ["validate", "destroy"])
        self.api._objects.assert_not_awaited()

    async def test_options_reads_selected_state_menus_without_world_building_join(self):
        self.api._objects = AsyncMock(side_effect=AssertionError("no World scan"))
        self.api._potential_options = AsyncMock(return_value=[{"state_id": 13,
            "building_key": "building_iron_mine", "existing_levels": 0,
            "maximum_levels": 40, "eligible": None}])
        result = await self.api.options(state=13)
        self.assertEqual(result[0]["existing_levels"], 0)
        self.assertEqual(result[0]["maximum_levels"], 40)
        self.api._potential_options.assert_awaited_once()
        self.assertEqual(self.api._potential_options.await_args.args[0], 13)
        self.api._objects.assert_not_awaited()


    async def test_capacity_uses_building_metrics_not_military_capacity(self):
        self.api._value = AsyncMock(side_effect=[(Decimal(50), None), (Decimal(10), None),
            (Decimal(40), None), (Decimal(10), None), (Decimal(0), None), (2, None),
            (None, "native getter error 13"), (False, None)])
        result = await self.api.capacity()
        self.assertEqual(result["base_speed"], Decimal(50))
        self.assertIsNone(result["private_queue_count"])
        self.assertIn("13", result["private_queue_count_reason"])
        self.assertFalse(result["construction_paused"])
        self.assertNotIn("paused", result)
        getters = [call.args[1] for call in self.api._value.await_args_list]
        self.assertNotIn("GetFreeMilitaryConstructionCapacity", getters)
        self.assertIn("CalcUnusedConstruction", getters)



    async def test_getter_error_is_none_not_zero_and_retained_value_is_released(self):
        self.api._functions = AsyncMock(return_value={"CalcUsedConstruction": NS(argument_count=0,
            callbacks=(1, 0, 0, 0))})
        self.api._evaluate = AsyncMock(return_value=NS(error=13, value_handle=900))
        self.api._release = AsyncMock()
        value, reason = await self.api._value("Country", "CalcUsedConstruction", ())
        self.assertIsNone(value)
        self.assertIn("13", reason)
        self.api._release.assert_awaited_once()

    async def test_native_key_rejection_destroys_known_owned_command(self):
        original = self.creates
        owner = Owner(self.ctx, 12490, 156, {439: commands.UInt32(13),
            10232: "building_university", 219: {10304: commands.UInt32(156)},
            22805: 1, 22308: commands.Token(603)})
        with patch("pdx_runtime.v3_game.construction.commands.create", return_value=Query(NS(error=0, owner=owner))):
            with self.assertRaisesRegex(ValueError, "did not resolve"):
                await self.api._prepare(13, "building_invalid_key", 1, 156)
        self.assertEqual(owner.calls, ["serialize", "destroy"])

    async def test_paging_uses_native_cursors_not_object_count_or_file_order(self):
        pages = [NS(error=0, world=self.ctx.world, records=(NS(valid=True, resolved_id=17),),
                    next_index=1000, slot_bound=5000),
                 NS(error=0, world=self.ctx.world, records=(NS(valid=True, resolved_id=65537),),
                    next_index=5000, slot_bound=5000)]
        with patch("pdx_runtime.v3_game.construction.objects.enumerate_objects",
                   side_effect=[Query(page) for page in pages]) as enumerate_objects:
            rows = await self.api._objects(objects.Domain.BUILDING)
        self.assertEqual([row.resolved_id for row in rows], [17, 65537])
        self.assertEqual([call.kwargs["start_index"] for call in enumerate_objects.call_args_list], [0, 1000])


    async def test_type_metadata_uses_single_vm_chains_never_raw_pointer_rebinding(self):
        row = NS(resolved_id=7, state_id=13, type_pointer=0x55500)
        self.api._functions = AsyncMock(return_value={name: NS(argument_count=0, callbacks=(1, 0, 0, 0))
            for name in ("GetKey", "GetNameNoFormatting", "GetRequiredConstruction", "GetConstructionCosts")})
        self.api._evaluate = AsyncMock(return_value=NS(error=13, value_handle=0))
        self.api._release = AsyncMock()
        self.api._generated_key = AsyncMock(return_value="building_iron_mine")
        self.api._value = AsyncMock(return_value=("native description", None))
        result = await self.api._type_details(row)
        self.assertEqual(result["building_key"], "building_iron_mine")
        self.assertIsNone(result["required_construction"])
        self.assertEqual(result["required_construction_reason"], "native getter error 13")
        for call in self.api._evaluate.await_args_list:
            self.assertTrue(call.args[0].startswith("Building.GetBuildingType."))
            self.assertEqual(call.args[1][0].object, 7)
            self.assertEqual(call.args[1][0].domain, "Building")
        self.api._generated_key.assert_awaited_once()

    async def test_generated_key_clones_serializes_and_destroys_without_submit(self):
        row = NS(resolved_id=7, state_id=13)
        binding = data.ObjectBinding.building(103, 7)
        source = NS(error=0, value_handle=80, value=data.NativeDataCommand(0x12000, 0x13000,
            0x12018, 80, "CDataCommand"))
        carrier = NS(error=0, value=data.NativeDataCommand(0x12000, 0x13000,
            0x12018, 80, "CDataCommand", self.ctx.world))
        owner = Owner(self.ctx, 12490, 156, {439: commands.UInt32(13),
            10232: "building_iron_mine", 219: {10304: commands.UInt32(156)},
            22805: 1, 22308: commands.Token(603)})
        self.api._evaluate = AsyncMock(return_value=source)
        async def release(result):
            self.assertEqual(owner.calls[-1], "destroy")
            self.assertIs(result, source)
        self.api._release = AsyncMock(side_effect=release)
        with patch("pdx_runtime.v3_game.construction.data.command_value", return_value=Query(carrier)), \
             patch("pdx_runtime.v3_game.construction.commands.request", return_value=Query(NS(error=0, owner=owner))) as clone:
            key = await self.api._generated_key(row, binding)
        self.assertEqual(key, "building_iron_mine")
        self.assertEqual(owner.calls, ["serialize", "destroy"])
        self.assertEqual(clone.call_args.args[1], commands.Action.CLONE)
        self.assertEqual(clone.call_args.kwargs["world"], self.ctx.world)
        self.api._release.assert_awaited_once()
        self.assertFalse(self.creates)

    async def test_uncertain_clone_keeps_exact_source_and_never_submits_or_reclones(self):
        row = NS(resolved_id=7, state_id=13)
        source = NS(error=0, value_handle=80, value=data.NativeDataCommand(0x12000,
            0x13000, 0x12018, 80, "CDataCommand"))
        carrier = NS(error=0, value=data.NativeDataCommand(0x12000, 0x13000,
            0x12018, 80, "CDataCommand", self.ctx.world))
        self.api._evaluate = AsyncMock(return_value=source)
        self.api._release = AsyncMock()
        with patch("pdx_runtime.v3_game.construction.data.command_value", return_value=Query(carrier)), \
             patch("pdx_runtime.v3_game.construction.commands.request", side_effect=RuntimeError("uncertain clone")) as clone:
            with self.assertRaisesRegex(RuntimeError, "uncertain clone"):
                await self.api._generated_key(row, data.ObjectBinding.building(103, 7))
        clone.assert_called_once()
        self.api._release.assert_not_awaited()
        self.assertIs(self.api._operations[-1]["source"], source)

    async def test_post_acceptance_observer_error_preserves_acceptance_without_replay(self):
        original = RuntimeError("original bounded post read failed")
        self.api._post_build = AsyncMock(side_effect=original)
        result = await self.api.build(13, "building_iron_mine")
        self.assertTrue(result["accepted"])
        self.assertIs(result["post_observation_error"], original)
        self.assertEqual(self.owners[0].calls, ["validate", "submit"])

    async def test_uncertain_owned_submission_is_retained_not_destroyed_or_replayed(self):
        original = RuntimeError("original handoff unresolved")
        original_query = self.ctx.query
        async def query(label, factory, owner=None):
            if label == "construction.submit":
                factory()
                owner.attempt.outcome = "unknown"
                raise original
            return await original_query(label, factory, owner)
        self.ctx.query = query
        with self.assertRaises(RuntimeError) as caught:
            await self.api.build(13, "building_iron_mine")
        self.assertIs(caught.exception, original)
        owner = self.owners[0]
        self.assertEqual(owner.calls, ["validate", "submit"])
        self.assertIsNotNone(owner.pending)
        self.assertNotEqual(owner.command, 0)
        run = self.ctx.owner_pool.snapshot()[0]
        self.assertIs(run.owners[0], owner)
        self.assertIs(original.observed_result, run.public_result)


    async def test_potential_native_candidate_needs_no_live_building_id(self):
        source = NS(error=0, value_handle=100, value=data.NativeList(100, 0x9000, b"", "list"))
        selected = NS(error=0, value_handle=201, value=data.NativeObject(0x5000, 103,
            201, "CPdxPtr@VCDataBuilding@@"))
        self.api._functions = AsyncMock(return_value={"GetPotentialUrbanBuildings": NS(
            argument_count=0, callbacks=(1, 0, 0, 0))})
        self.api._evaluate = AsyncMock(return_value=source)
        self.api._release = AsyncMock()
        self.api._bound_type_details = AsyncMock(return_value={"type_pointer": None,
            "building_key": "building_university", "name": "University", "required_construction": Decimal(400)})
        self.api._value = AsyncMock(side_effect=[(0, None), (100, None)])
        seen = set()
        with patch("pdx_runtime.v3_game.construction.data.list_count", return_value=Query(NS(error=0, total=1))), \
             patch("pdx_runtime.v3_game.construction.data.list_element", return_value=Query(selected)):
            result = await ConstructionAPI._potential_options(self.api, 13, 156, seen, validate=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["building_key"], "building_university")
        self.assertEqual(result[0]["existing_levels"], 0)
        self.assertTrue(result[0]["eligible"])
        self.assertIn((13, "building_university"), seen)
        binding = self.api._bound_type_details.call_args.args[1]
        self.assertTrue(binding.retained)
        self.assertEqual(binding.object, 201)
        self.assertEqual(self.owners[0].calls, ["serialize", "validate", "destroy"])
        self.assertEqual([call.args[0].value_handle for call in self.api._release.await_args_list], [201, 100])

    async def test_potential_borrowed_candidate_is_not_cast_into_owned_building(self):
        source = NS(error=0, value_handle=100, value=data.NativeList(100, 0x9000, b"", "list"))
        selected = NS(error=0, value_handle=201, value=data.NativeObject(0x5000, 999,
            201, "PEBVCDataBuilding@@"))
        self.api._functions = AsyncMock(return_value={"GetPotentialUrbanBuildings": NS(
            argument_count=0, callbacks=(1, 0, 0, 0))})
        self.api._evaluate = AsyncMock(return_value=source)
        self.api._release = AsyncMock()
        self.api._bound_type_details = AsyncMock()
        with patch("pdx_runtime.v3_game.construction.data.list_count", return_value=Query(NS(error=0, total=1))), \
             patch("pdx_runtime.v3_game.construction.data.list_element", return_value=Query(selected)):
            result = await ConstructionAPI._potential_options(self.api, 13, 156, set())
        self.assertEqual(result, [])
        self.api._bound_type_details.assert_not_awaited()
        self.assertFalse(self.creates)
        self.assertEqual(self.api._release.await_count, 2)


    async def test_real_context_timeout_retains_one_original_submit_and_resumes(self):
        from pdx_runtime.v3_game._core import GameContext, PendingOperation
        real = GameContext(NS(native=self.ctx.native), self.ctx.process, timeout=0, poll_interval=.001)
        real.world = self.ctx.world
        real.refresh, real.country_id = AsyncMock(), AsyncMock(return_value=156)
        api = ConstructionAPI(real)
        api._post_build = AsyncMock(return_value={"scope": "bounded count"})
        gate, read_seen = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        original_create = commands.create
        def prepare(*args, **kwargs):
            created = original_create(*args, **kwargs)
            owner = created.result.owner
            original_submit = owner.submit
            def submit(flags):
                query = original_submit(flags)
                original_read = query.read
                def read():
                    loop.call_soon_threadsafe(read_seen.set)
                    return original_read() if gate.is_set() else None
                query.read = read
                return query
            owner.submit = submit
            return created
        with patch.object(commands, "create", side_effect=prepare):
            pending = await api.build(13, "building_iron_mine")
            self.assertIsInstance(pending, PendingOperation)
            try:
                await asyncio.wait_for(read_seen.wait(), timeout=1)
                owner = self.owners[0]
                query = owner.pending
                self.assertIsNotNone(query)
                self.assertEqual(owner.calls, ["validate", "submit"])
            finally:
                gate.set()
                result = await pending.resume(timeout=1)
        self.assertTrue(result["accepted"])
        self.assertEqual(owner.calls, ["validate", "submit"])
        self.assertEqual(len(self.creates), 1)
        self.assertEqual(real.pending_queries, [])

    async def test_game_fixed_point_conversion_uses_shared_exact_units(self):
        from decimal import localcontext, Inexact, Rounded
        from pdx_runtime.v3_game.construction import _number
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            value = _number(data.FixedPoint((1 << 63) - 1))
        self.assertEqual(value, Decimal("92233720368547.75807"))

    async def test_native_state_source_copies_element_key_without_index_as_id(self):
        seen = set()
        self.api._functions = AsyncMock(return_value={"GetPotentialUrbanBuildings": NS(
            argument_count=0, callbacks=(1, 0, 0, 0))})
        values = [NS(error=0, source_count=1, value=0),
            NS(error=0, value="building_university"), NS(error=0, value="University"),
            NS(error=0, value=data.FixedPoint(40000000)), NS(error=0, value=0),
            NS(error=0, value=100), NS(error=0, value="building_university")]
        with patch("pdx_runtime.v3_game.construction.data.evaluate_element",
                   side_effect=[Query(value) for value in values]) as evaluate_element:
            result = await ConstructionAPI._all_type_options(self.api, [13], 156, seen, validate=True)
        self.assertEqual(result[0]["building_key"], "building_university")
        self.assertEqual(result[0]["existing_levels"], 0)
        self.assertEqual(result[0]["required_construction"], Decimal(400))
        self.assertTrue(result[0]["eligible"])
        self.assertEqual(self.creates[0][2][10232], "building_university")
        self.assertEqual(self.creates[0][2][439], commands.UInt32(13))
        for call in evaluate_element.call_args_list:
            self.assertEqual(call.args[1], "State.GetPotentialUrbanBuildings")
            self.assertNotIn("BuildingBrowserPanel", call.args[1])
            self.assertEqual(call.args[4][0].domain, "State")
            self.assertEqual(call.args[4][0].object, 13)
            self.assertEqual(call.kwargs["world"], self.ctx.world)
            self.assertIs(call.kwargs["process"], self.ctx.process)
        self.assertEqual(self.owners[0].calls, ["serialize", "validate", "destroy"])

    async def test_invalid_source_is_negative_cached_but_real_context_changes_recover(self):
        self.api._functions = AsyncMock(return_value={"GetPotentialUrbanBuildings": NS(
            argument_count=0, callbacks=(1, 0, 0, 0))})
        with patch("pdx_runtime.v3_game.construction.data.evaluate_element",
                   return_value=Query(NS(error=13, source_count=0))) as evaluate_element:
            self.assertEqual(await ConstructionAPI._all_type_options(self.api, [13], 156, set()), [])
            self.assertEqual(await ConstructionAPI._all_type_options(self.api, [13], 156, set()), [])
            self.assertEqual(evaluate_element.call_count, 1)
            await ConstructionAPI._all_type_options(self.api, [14], 156, set())
            self.assertEqual(evaluate_element.call_count, 2)
            self.ctx.world += 1
            await ConstructionAPI._all_type_options(self.api, [13], 156, set())
            self.assertEqual(evaluate_element.call_count, 3)
            await ConstructionAPI._all_type_options(self.api, [13], 157, set())
            self.assertEqual(evaluate_element.call_count, 4)
            self.ctx.adapters["data"] = "new-native-data-context"
            await ConstructionAPI._all_type_options(self.api, [13], 157, set())
            self.assertEqual(evaluate_element.call_count, 5)
        self.assertFalse(self.creates)
        self.assertEqual(self.ctx.diagnostics[-1]["native_error"], 13)

    async def test_dynamic_absent_source_is_not_permanently_negative_cached(self):
        binding = data.ObjectBinding.state(88, 13)
        with patch("pdx_runtime.v3_game.construction.data.evaluate_element",
                   side_effect=[Query(NS(error=1168, source_count=0)),
                       Query(NS(error=0, source_count=1, value=4))]) as evaluate_element:
            first = await self.api._source_element("State.GetPotentialUrbanBuildings", 0,
                "Building.GetTotalLevels", (binding,), actor=156, state=13)
            second = await self.api._source_element("State.GetPotentialUrbanBuildings", 0,
                "Building.GetTotalLevels", (binding,), actor=156, state=13)
        self.assertIsNone(first[0])
        self.assertEqual(second[0], 4)
        self.assertEqual(evaluate_element.call_count, 2)

    async def test_costs_are_current_national_expenses_not_per_project_bill(self):
        self.api._value = AsyncMock(side_effect=[(Decimal(90), None), (Decimal(30), None),
            (Decimal(120), None), ("Wood 80, iron 10", None), (None, "native getter error 13")])
        result = await ConstructionAPI._costs(self.api, 156)
        self.assertEqual(result["weekly_government_goods_expense"], Decimal(90))
        self.assertEqual(result["weekly_total_goods_expense"], Decimal(120))
        self.assertEqual(result["units"], "pounds/week")
        self.assertEqual(result["scope"], "current national building construction")
        self.assertIsNone(result["private_goods_description"])
        getters = [call.args[1] for call in self.api._value.await_args_list]
        self.assertIn("GetGovernmentConstructionGoodsExpensesByGoodsDesc", getters)
        self.assertNotIn("GetGovernmentGoodsExpenses", getters)

    async def test_sector_inputs_reuse_shared_economy_io_and_native_shortage_getters(self):
        building = NS(resolved_id=77, state_id=13, levels=3)
        economy = NS(_building_io=AsyncMock(return_value={"inputs": [{"key": "wood",
            "name": "Wood", "consumption": Decimal(40), "consumption_value": Decimal(800),
            "reasons": {}}], "reasons": {}}))
        self.ctx.domain = lambda name: economy
        self.api._value = AsyncMock(side_effect=[(True, None), (Decimal("0.25"), None)])
        result = await self.api._sector_inputs(building)
        economy._building_io.assert_awaited_once_with(77, self.ctx.world)
        self.assertEqual(result["inputs"][0]["quantity"], Decimal(40))
        self.assertEqual(result["inputs"][0]["value"], Decimal(800))
        self.assertTrue(result["has_input_shortage"])
        self.assertEqual(result["input_shortage_penalty"], Decimal("0.25"))
        self.assertTrue(result["complete"])
        self.assertEqual([call.args[1] for call in self.api._value.await_args_list],
                         ["HasAnyInputShortage", "GetInputShortagePenalty"])

    async def test_inputs_join_real_sector_goods_keys_not_target_production_inputs(self):
        domestic = NS(resolved_id=13, owner_id=156)
        sectors = [NS(resolved_id=1, state_id=13, levels=2, type_pointer=0x101),
            NS(resolved_id=2, state_id=13, levels=1, type_pointer=0x101),
            NS(resolved_id=3, state_id=13, levels=10, type_pointer=0x102),
            NS(resolved_id=4, state_id=99, levels=1, type_pointer=0x101)]
        self.api._objects = AsyncMock(side_effect=[[domestic], sectors])
        self.api._known_types = AsyncMock(return_value={0x101: {"building_key": "building_construction_sector"},
            0x102: {"building_key": "building_logging_camp"}})
        def sector_input(building):
            return {"building_id": building.resolved_id, "complete": True,
                "has_input_shortage": True, "input_shortage_penalty": Decimal("0.2"),
                "inputs": [{"key": "wood", "name": "Wood", "quantity": Decimal(40 if building.resolved_id == 1 else 20)}]}
        self.api._sector_inputs = AsyncMock(side_effect=sector_input)
        economy = NS(_market=AsyncMock(return_value={"id": 999, "country_id": 156,
            "goods": [{"key": "tools", "price": Decimal(60), "demand": Decimal(400),
                       "supply": Decimal(1), "shortage": Decimal(399)},
                      {"key": "wood", "price": Decimal(30), "demand": Decimal(100),
                       "supply": Decimal(90), "shortage": Decimal(10)}]}))
        self.ctx.domain = lambda name: economy
        result = await self.api.inputs()
        self.assertEqual([call.args[0].resolved_id for call in self.api._sector_inputs.await_args_list], [1, 2])
        self.assertEqual(result["goods"][0]["key"], "wood")
        self.assertEqual(result["goods"][0]["quantity"], Decimal(60))
        self.assertEqual(result["goods"][0]["market_order_gap"], Decimal(10))
        self.assertEqual([good["key"] for good in result["market_bottlenecks"]], ["wood"])
        self.assertIsNone(result["goods"][0]["native_shortage_penalty"])
        self.assertTrue(result["market_complete"])
        self.assertIn("not per-project", result["scope"])
        self.assertIn("not a native shortage penalty", result["bottleneck_definition"])

    async def test_unknown_sector_identity_cannot_report_no_inputs_or_no_bottlenecks(self):
        self.api._objects = AsyncMock(side_effect=[[NS(resolved_id=13, owner_id=156)],
            [NS(resolved_id=1, state_id=13, levels=2, type_pointer=0x101)]])
        self.api._known_types = AsyncMock(return_value={0x101: {"building_key": None}})
        result = await self.api.inputs()
        self.assertFalse(result["complete"])
        self.assertFalse(result["market_complete"])
        self.assertIn("could not be observed", result["reason"])

    async def test_input_quantities_sum_exactly_under_low_decimal_context(self):
        from decimal import localcontext, Inexact, Rounded
        self.api._objects = AsyncMock(side_effect=[[NS(resolved_id=13, owner_id=156)],
            [NS(resolved_id=i, state_id=13, levels=1, type_pointer=0x101) for i in (1, 2)]])
        self.api._known_types = AsyncMock(return_value={0x101: {"building_key": "building_construction_sector"}})
        self.api._sector_inputs = AsyncMock(side_effect=lambda building: {"complete": True,
            "inputs": [{"key": "wood", "name": "Wood", "quantity": Decimal("92233720368547.75807")}]})
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = context.traps[Rounded] = True
            result = await self.api.inputs(market=False)
        self.assertEqual(result["goods"][0]["quantity"], Decimal("184467440737095.51614"))

    async def test_missing_consumption_stays_unknown_in_aggregate(self):
        self.api._objects = AsyncMock(side_effect=[[NS(resolved_id=13, owner_id=156)],
            [NS(resolved_id=1, state_id=13, levels=1, type_pointer=0x101)]])
        self.api._known_types = AsyncMock(return_value={0x101: {"building_key": "building_construction_sector"}})
        self.api._sector_inputs = AsyncMock(return_value={"complete": False,
            "inputs": [{"key": "wood", "name": "Wood", "quantity": None}]})
        result = await self.api.inputs(market=False)
        self.assertIsNone(result["goods"][0]["quantity"])
        self.assertFalse(result["complete"])

    async def test_owned_candidate_source_failure_is_cached_without_blocking_other_contexts(self):
        self.api._functions = AsyncMock(return_value={"GetPotentialUrbanBuildings": NS(
            argument_count=0, callbacks=(1, 0, 0, 0))})
        self.api._evaluate = AsyncMock(return_value=NS(error=13, value_handle=0))
        self.api._release = AsyncMock()
        self.assertEqual(await ConstructionAPI._potential_options(self.api, 13, 156, set()), [])
        self.assertEqual(await ConstructionAPI._potential_options(self.api, 13, 156, set()), [])
        self.assertEqual(self.api._evaluate.await_count, 1)
        await ConstructionAPI._potential_options(self.api, 14, 156, set())
        self.assertEqual(self.api._evaluate.await_count, 2)
        self.ctx.world += 1
        await ConstructionAPI._potential_options(self.api, 13, 156, set())
        self.assertEqual(self.api._evaluate.await_count, 3)



    async def test_explicit_details_return_original_native_reason_as_incomplete_raw(self):
        self.valid = False
        result = await self.api.build(13, "building_iron_mine", details=True)
        self.assertFalse(result["eligible"])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "Not enough available resource levels")
        self.assertEqual(result["reason_raw"], result["reason"])
        self.assertFalse(result["reason_complete"])
        self.assertEqual(self.owners[0].validation_capacities, [65536])
        self.assertNotIn("submit", self.owners[0].calls)

    async def test_default_estimate_uses_bool_not_null_reason_for_eligibility(self):
        self.valid = False
        self.api._objects = AsyncMock(return_value=[])
        result = await self.api.estimate(13, "building_iron_mine")
        self.assertFalse(result["eligible"])
        self.assertFalse(result["reason_complete"])
        self.assertFalse(result["reason_details"]["eligible"])
        self.assertEqual(result["reason_details"]["levels_requested"], 1)
        self.assertEqual(result["reason_details"]["state_id"], 13)
        self.assertEqual(self.owners[0].validation_capacities, [0])

    async def test_estimate_finds_fresh_potential_cost_without_persistent_building(self):
        self.api._fresh_candidate_details = ConstructionAPI._fresh_candidate_details.__get__(self.api)
        self.api._functions = AsyncMock(return_value={"GetPotentialUrbanBuildings": NS(
            argument_count=0, callbacks=(1, 0, 0, 0))})
        self.api._objects = AsyncMock(side_effect=AssertionError("no world scan"))
        self.api._generated_key = AsyncMock(side_effect=AssertionError("no duplicate construction"))
        current = {"required": 600}
        def evaluate(native, expression, bindings, **options):
            if expression.startswith("State."):
                return Query(queue_result(action=data.Action.EVALUATE, kind=data.Kind.LIST,
                    value_handle=100, snapshot=bytes(24), snapshot_bytes=24, total=24))
            if expression.endswith("GetRequiredConstruction"):
                bits = current["required"] * 100000
                kind, snapshot = data.Kind.FIXED, struct.pack("<q", bits)
            elif expression.endswith("GetTotalLevels") or expression.endswith("GetMaxLevels"):
                bits = 0 if expression.endswith("GetTotalLevels") else 40
                kind, snapshot = data.Kind.I32, struct.pack("<i", bits)
            else:
                bits, kind = 0, data.Kind.STRING
                value = "building_tooling_workshops" if expression.endswith("GetKey") else "native description"
                snapshot = value.encode()
            evaluate.handle += 1
            return Query(queue_result(action=data.Action.EVALUATE, kind=kind, raw_bits=bits,
                value_handle=evaluate.handle, snapshot=snapshot, snapshot_bytes=len(snapshot), total=len(snapshot)))
        evaluate.handle = 1000
        selected = queue_result(action=data.Action.LIST_ELEMENT, kind=data.Kind.OBJECT,
            value_handle=201, type_id=703, object=0x5000, snapshot=struct.pack("<Q", 0x5000),
            snapshot_bytes=8, total=8,
            native_type_name=".?AU?$SAnyType@V?$CPdxPtr@VCDataBuilding@@@@@PdxAnyImpl@@")
        with patch.object(data, "evaluate", side_effect=evaluate), \
             patch.object(data, "list_count", return_value=Query(queue_result(total=1))), \
             patch.object(data, "list_element", return_value=Query(selected)), \
             patch.object(data, "release", return_value=Query(queue_result(action=data.Action.RELEASE))):
            first = await self.api.estimate(23, "building_tooling_workshops", 2)
            current["required"] = 625
            second = await self.api.estimate(23, "building_tooling_workshops", 2)
        self.assertEqual(first["required_construction"], Decimal(1200))
        self.assertEqual(second["required_construction"], Decimal(1250))
        self.assertEqual(first["existing_levels"], 0)
        self.assertEqual(first["maximum_levels"], 40)
        self.assertTrue(all(owner.calls == ["validate", "destroy"] for owner in self.owners))
        self.assertEqual(len(self.creates), 2)  # Two explicit estimates, not enrichment factories.
        self.api._objects.assert_not_awaited()
        self.api._generated_key.assert_not_awaited()

    async def test_estimate_raw_candidate_cost_uses_original_state_binding_scope(self):
        self.api._fresh_candidate_details = ConstructionAPI._fresh_candidate_details.__get__(self.api)
        self.api._functions = AsyncMock(return_value={"GetPotentialUrbanBuildings": NS(
            argument_count=0, callbacks=(1, 0, 0, 0))})
        calls = []
        def scoped(native, source, index, expression, bindings, **options):
            calls.append((source, index, bindings))
            value = {"Building.GetTotalLevels": 0, "Building.GetBuildingType.GetKey": "building_tooling_workshops",
                "Building.GetBuildingType.GetRequiredConstruction": data.FixedPoint(60000000),
                "Building.GetNameNoFormatting": "Tooling Workshops", "Building.GetMaxLevels": 40,
                "Building.GetBuildingType.GetConstructionCosts": None}[expression]
            return Query(NS(error=0, source_count=1, value=value))
        with patch.object(data, "evaluate", return_value=Query(queue_result(error=50))), \
             patch.object(data, "evaluate_element", side_effect=scoped):
            result = await self.api.estimate(23, "building_tooling_workshops")
        self.assertEqual(result["required_construction"], Decimal(600))
        self.assertEqual(result["existing_levels"], 0)
        self.assertTrue(all(source == "State.GetPotentialUrbanBuildings" and bindings[0].object == 23
                            and bindings[0].domain == "State" for source, index, bindings in calls))
        self.assertEqual(len(self.creates), 1)
        self.assertEqual(self.owners[0].calls, ["validate", "destroy"])

    async def test_estimate_enrichment_error_keeps_native_validation_and_single_owner(self):
        original = RuntimeError("original State menu read failed")
        self.api._fresh_candidate_details = AsyncMock(side_effect=original)
        result = await self.api.estimate(23, "building_arms_industry")
        self.assertIs(result["eligible"], True)
        self.assertIs(result["enrichment_error"], original)
        self.assertIsNone(result["required_construction"])
        self.assertIn("construction points", result["required_construction_reason"])
        self.assertEqual(len(self.creates), 1)
        self.assertEqual(self.owners[0].calls, ["validate", "destroy"])

    async def test_estimate_fresh_cost_does_not_erase_observed_existing_levels(self):
        self.api._objects = AsyncMock(side_effect=AssertionError("no broad fallback"))
        self.api._fresh_candidate_details = AsyncMock(return_value={"name": "Native name",
            "building_key": "building_tooling_workshops", "required_construction": Decimal(600),
            "existing_levels": 4, "maximum_levels": 40, "candidate_source": "State.GetPotentialUrbanBuildings"})
        result = await self.api.estimate(23, "building_tooling_workshops")
        self.assertEqual(result["required_construction"], Decimal(600))
        self.assertEqual(result["existing_levels"], 4)
        self.assertEqual(result["name"], "Native name")
        self.assertEqual(result["reason_details"]["existing_levels"], 4)
        self.api._objects.assert_not_awaited()






def queue_result(**values):
    copied = {field.name: 0 for field in fields(data.DataResult)}
    copied.update(raw_any=b"", native_type_name="", snapshot=b"", rows=())
    copied.update(values)
    return data.DataResult(**copied)


class QueueBackend:
    """CPU stand-in only at existing data factories; real Run owns all Anys."""
    def __init__(self, ctx, count=4, indices=None):
        self.ctx, self.count = ctx, count
        self.indices = indices or {}
        self.calls, self.released, self.bindings = [], [], []
        self.handle = 1000
        self.partial = None
        self.invalid_utf8 = False
        self.failure = None
        self.fail_field = "construction_speed"
        self.cleanup_error = None
        self.source_error = 0
        self.count_error = 0
        self.gate = None
        self.original_query = None
        self.element_error = 0
        self.local_player = True
        self.generation_error = 0
        self.command_valid = self.command_accepted = True
        self.command_owners, self.generated_positions, self.carriers = [], [], {}
        self.read_started = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.patchers = []
    def query(self, result):
        return Query(result)
    def evaluate(self, client, expression, bindings, **options):
        self.calls.append(expression)
        if expression == "Country.IsLocalPlayer":
            return self.query(queue_result(action=data.Action.EVALUATE, kind=data.Kind.BOOL,
                raw_bits=int(self.local_player), snapshot=bytes((int(self.local_player),)), snapshot_bytes=1, total=1))
        if expression == "Country.AccessGovernmentConstructionQueue":
            self.source_release_start = len(self.released)
            result = queue_result(action=data.Action.EVALUATE, kind=data.Kind.LIST,
                value_handle=100, type_id=70, error=self.source_error,
                snapshot=bytes(24), snapshot_bytes=24, total=24)
            query = self.query(result)
            if self.gate is not None:
                class Deferred(Query):
                    def read(inner):
                        self.loop.call_soon_threadsafe(self.read_started.set)
                        return result if self.gate.is_set() else None
                query = Deferred(result)
            self.original_query = query
            return query
        binding = bindings[0]
        self.bindings.append(binding)
        position = binding.object - 201
        getter = expression.removeprefix("ConstructionQueueElement.")
        if getter == "CancelConstruction":
            self.handle += 1
            self.generated_positions.append(position)
            primary, control = 0x7000 + position * 0x100, 0x9000 + position * 0x100
            self.carriers[self.handle] = (primary, control, primary + 0x18, self.ctx.world)
            return self.query(queue_result(action=data.Action.EVALUATE, kind=data.Kind.COMMAND,
                value_handle=self.handle, error=self.generation_error, payload_size=24,
                snapshot=struct.pack("<3Q", primary, control, primary + 0x18), snapshot_bytes=24, total=24))
        names = {"GetIndex": "index", "CalcPercentComplete": "progress",
            "GetConstructionSpeed": "construction_speed", "IsValidConstruction": "valid_construction",
            "GetBuilding.GetState.GetName": "state_name", "GetBuilding.GetName": "building_name",
            "GetTooltip": "tooltip", "GetQueueNumber": "queue_number", "GetWeeksLeft": "weeks_left",
            "GetBaseConstructionSpeed": "base_construction_speed",
            "IsCurrentlyUnderConstruction": "currently_under_construction"}
        name = names[getter]
        if self.failure is not None and name == self.fail_field:
            failure = self.failure
            class Failed(Query):
                def read(inner):
                    raise failure
            return Failed(None)
        self.handle += 1
        if name in ("index", "queue_number", "weeks_left"):
            value = self.indices.get(position, position) if name == "index" else position + 1 if name == "queue_number" else 4
            kind, bits, snapshot = data.Kind.I32, value, struct.pack("<i", value)
        elif name in ("progress", "construction_speed", "base_construction_speed"):
            bits = 300000 if name == "base_construction_speed" else 0 if position == 0 else (25000 if name == "progress" else 200000)
            kind, snapshot = data.Kind.FIXED, struct.pack("<q", bits)
        elif name in ("valid_construction", "currently_under_construction"):
            kind, bits = data.Kind.BOOL, int(name == "currently_under_construction")
            snapshot = bytes((bits,))
        else:
            kind, bits = data.Kind.STRING, 0
            snapshot = ("original native tooltip" if name == "tooltip" else
                "\x15header 山东" if name == "state_name" else "\x15header 武器厂").encode("utf-8")
        error, total = 0, len(snapshot)
        if self.partial is not None and name == self.partial:
            snapshot = "山".encode("utf-8")
            error, total = 234, len(snapshot) + 9
        if self.invalid_utf8 and name == "building_name":
            snapshot = b"\xff\x00"; total = len(snapshot)
        return self.query(queue_result(action=data.Action.EVALUATE, type_id=4, kind=kind,
            value_handle=self.handle, raw_bits=bits, snapshot=snapshot,
            snapshot_bytes=len(snapshot), total=total, error=error))
    def count_query(self, client, handle, **options):
        self.calls.append("count")
        return self.query(queue_result(action=data.Action.LIST_COUNT, value_handle=handle,
            total=self.count, error=self.count_error))
    def element(self, client, handle, index, **options):
        self.calls.append(("element", index))
        if self.element_error or index >= self.count:
            return self.query(queue_result(action=data.Action.LIST_ELEMENT, error=self.element_error or 1168))
        return self.query(queue_result(action=data.Action.LIST_ELEMENT, kind=data.Kind.OBJECT,
            value_handle=201 + index, type_id=500 + index, object=0x5000 + index,
            native_type_name=".?AU?$SAnyType@V?$CPdxPtr@VCDataConstructionQueueElement@@@@@PdxAnyImpl@@",
            snapshot=struct.pack("<Q", 0x5000 + index), snapshot_bytes=8, total=8))
    def release(self, client, handle, **options):
        self.released.append(handle)
        if self.cleanup_error is not None:
            error = self.cleanup_error
            class Failed(Query):
                def read(inner): raise error
            return Failed(None)
        return self.query(queue_result(action=data.Action.RELEASE))
    def command_value(self, client, handle, **options):
        self.calls.append("carrier")
        return self.query(queue_result(action=data.Action.COMMAND_VALUE, value_handle=handle,
            snapshot=struct.pack("<4Q", *self.carriers[handle]), snapshot_bytes=32, total=32))
    def clone(self, client, action, *, command, world, **options):
        self.calls.append("clone")
        assert action == commands.Action.CLONE
        assert command in [carrier[0] for carrier in self.carriers.values()]
        assert world == self.ctx.world
        assert self.released[self.source_release_start:] == []
        owner = Owner(self.ctx, 23476, 156, {}, self.command_valid, self.command_accepted)
        self.command_owners.append(owner)
        return self.query(NS(error=0, cloned_command=owner.command, owner=owner,
            world=world, type_token=23476))
    def __enter__(self):
        for name, factory in (("evaluate", self.evaluate), ("list_count", self.count_query),
                ("list_element", self.element), ("release", self.release), ("command_value", self.command_value)):
            patcher = patch.object(data, name, side_effect=factory)
            patcher.start(); self.patchers.append(patcher)
        for name, factory in (("request", self.clone),
                ("NativeCommand.from_result", lambda client, result, **kw: result.owner)):
            patcher = patch("pdx_runtime.v3_game.construction.commands." + name, side_effect=factory)
            patcher.start(); self.patchers.append(patcher)
        return self
    def __exit__(self, *error):
        for patcher in reversed(self.patchers):patcher.stop()


class QueueObservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = Context()
        self.api = ConstructionAPI(self.ctx)
        self.api._objects = AsyncMock(side_effect=AssertionError("no world Building scan"))
        self.ctx.support = lambda name: self.fail("no contextual formatter")
        self.ctx.process.read = lambda *args: self.fail("no private pointer read")

    async def test_selected_range_uses_producer_type_and_six_original_fields(self):
        with QueueBackend(self.ctx, count=8) as backend:
            result = await self.api.queue(start=2, limit=2, details=True)
        self.assertEqual(result["source_count"], 8)
        self.assertEqual(result["requested_range"], {"start": 2, "limit": 2})
        self.assertEqual(result["observed_range"], {"start": 2, "stop": 4})
        self.assertEqual(result["next"], 4)
        self.assertEqual([call for call in backend.calls if isinstance(call, tuple)], [("element", 2), ("element", 3)])
        self.assertEqual(backend.calls.count("Country.AccessGovernmentConstructionQueue"), 1)
        self.assertEqual(backend.calls.count("count"), 1)
        self.assertEqual([binding.type_id for binding in backend.bindings], [502] * 11 + [503] * 11)
        self.assertTrue(all(binding.retained for binding in backend.bindings))
        row = result["items"][0]
        self.assertEqual(row["progress"], Decimal("0.25"))
        self.assertEqual(row["construction_speed"], Decimal(2))
        self.assertIn("\x15header", row["state_name"])
        self.assertIn("武器厂", row["building_name"])
        self.assertEqual(row["queue_number"], 3)
        self.assertEqual(row["weeks_left"], 4)
        self.assertEqual(row["base_construction_speed"], Decimal(3))
        self.assertIs(row["currently_under_construction"], True)
        self.assertIn("original native tooltip", row["tooltip"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["cleanup"]["status"] if "cleanup" in result else "complete", "complete")
        self.assertEqual(len(backend.released), 25)
        self.api._objects.assert_not_awaited()

    async def test_zero_false_duplicate_dynamic_indices_are_not_missing_or_stable_ids(self):
        with QueueBackend(self.ctx, count=2, indices={0: 7, 1: 7}) as backend:
            result = await self.api.queue()
        self.assertEqual([row["index"] for row in result["items"]], [7, 7])
        self.assertEqual([row["source_position"] for row in result["items"]], [0, 1])
        row = result["items"][0]
        self.assertEqual(row["progress"], Decimal(0))
        self.assertEqual(row["construction_speed"], Decimal(0))
        self.assertIs(row["valid_construction"], False)

    async def test_empty_and_outside_range_keep_actual_source_count(self):
        with QueueBackend(self.ctx, count=0):
            empty = await self.api.queue(limit=3)
        self.assertEqual(empty["source_count"], 0)
        self.assertEqual(empty["items"], [])
        self.assertTrue(empty["complete"])
        with QueueBackend(self.ctx, count=2) as backend:
            outside = await self.api.queue(start=5, limit=1)
        self.assertEqual(outside["source_count"], 2)
        self.assertEqual(outside["items"], [])
        self.assertFalse(any(isinstance(call, tuple) for call in backend.calls))

    async def test_native_count_error_is_not_empty_queue_or_zero(self):
        with QueueBackend(self.ctx, count=12) as backend:
            backend.count_error = 13
            result = await self.api.queue(limit=3)
        self.assertIsNone(result["source_count"])
        self.assertEqual(result["count_result"].error, 13)
        self.assertEqual(result["count_result"].total, 12)
        self.assertEqual(result["items"], [])
        self.assertFalse(result["complete"])

    async def test_partial_formatted_name_preserves_bytes_prefix_and_original_any(self):
        with QueueBackend(self.ctx, count=1) as backend:
            backend.partial = "state_name"
            result = await self.api.queue(details=True)
        row = result["items"][0]
        field = row["fields"]["state_name"]
        self.assertEqual(row["state_name"], "山")
        self.assertEqual(field["snapshot"], "山".encode("utf-8"))
        self.assertEqual(field["error"], 234)
        self.assertFalse(field["copy_complete"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["range_complete"])
        self.assertEqual(backend.released, [])
        retained = self.ctx.owner_pool.snapshot()
        self.assertEqual(len(retained), 1)
        self.assertIs(retained[0].public_result, result)
        self.assertIn(field["native_result"].value_handle, retained[0].values)

    async def test_invalid_utf8_keeps_raw_bytes_and_real_decode_error(self):
        with QueueBackend(self.ctx, count=1) as backend:
            backend.invalid_utf8 = True
            result = await self.api.queue()
        field = result["items"][0]["fields"]["building_name"]
        self.assertEqual(field["snapshot"], b"\xff\x00")
        self.assertIsInstance(field["decode_error"], UnicodeDecodeError)
        self.assertIsNone(result["items"][0]["building_name"])
        self.assertFalse(result["complete"])

    async def test_transport_primary_keeps_read_prefix_and_same_original_graph(self):
        original = RuntimeError("original field callback failed")
        with QueueBackend(self.ctx, count=1) as backend:
            backend.failure = original
            with self.assertRaises(RuntimeError) as caught:
                await self.api.queue()
        self.assertIs(caught.exception, original)
        result = original.observed_result
        self.assertEqual(set(result["items"][0]["fields"]), {"index", "progress"})
        self.assertEqual(backend.released, [])
        retained = self.ctx.owner_pool.snapshot()[0]
        self.assertTrue(retained.retain_unknown)
        self.assertIs(retained.public_result, result)
        self.assertIsNotNone(retained.uncertain_query)

    async def test_cleanup_error_does_not_erase_complete_read(self):
        original = RuntimeError("original release failed")
        with QueueBackend(self.ctx, count=1) as backend:
            backend.cleanup_error = original
            result = await self.api.queue()
        self.assertTrue(result["complete"])
        self.assertIs(result["items"][0]["valid_construction"], False)
        self.assertEqual(result["cleanup"]["status"], "cleanup_unknown_retained")
        self.assertIn("original release failed", result["cleanup"]["reason"])
        self.assertEqual(len(backend.released), 1)
        self.assertEqual(len(self.ctx.owner_pool.snapshot()), 1)

    async def test_pending_resumes_same_source_without_factory_replay(self):
        from pdx_runtime.query_executor import PendingOperation, SerializedQueryExecutor, RetainedOwners
        self.ctx.owner_pool = RetainedOwners()
        self.ctx.session.owner_pool = self.ctx.owner_pool
        executor = SerializedQueryExecutor(timeout=0, poll_interval=.0001, owners=self.ctx.owner_pool)
        self.ctx._wake = executor._wake
        self.ctx._finish = executor._finish
        async def query(label, factory, owner=None):
            return await executor.query(label, factory, owner=owner)
        async def perform(label, work):
            return await executor.perform(label, work, context=self.ctx)
        self.ctx.query, self.ctx.perform = query, perform
        with QueueBackend(self.ctx, count=1) as backend:
            backend.gate = asyncio.Event()
            pending = await self.api.queue(limit=1)
            self.assertIsInstance(pending, PendingOperation)
            try:
                try:
                    await asyncio.wait_for(backend.read_started.wait(), timeout=1)
                except TimeoutError:
                    if pending.task.done():
                        pending.task.result()
                    raise
                self.assertEqual(backend.calls, ["Country.AccessGovernmentConstructionQueue"])
                self.assertIn(backend.original_query, executor.pending_queries)
            finally:
                backend.gate.set()
                result = await pending.resume(timeout=1)
        self.assertTrue(result["complete"])
        self.assertEqual(backend.calls.count("Country.AccessGovernmentConstructionQueue"), 1)
        self.assertEqual(executor.pending_queries, [])
        self.assertEqual(self.ctx.owner_pool.snapshot(), ())

    async def test_invalid_ranges_fail_before_native_work(self):
        for kwargs in ({"start": True}, {"start": -1}, {"limit": False}, {"limit": -1}):
            with self.assertRaises(ValueError):
                await self.api.queue(**kwargs)
        self.assertEqual(self.ctx.queries, [])


class QueueCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = Context()
        self.api = ConstructionAPI(self.ctx)
        self.ctx.process.read = lambda *args: self.fail("no pointer read")
        self.api._objects = AsyncMock(side_effect=AssertionError("no World scan"))

    async def test_current_source_position_generates_actual_row_command_once(self):
        with QueueBackend(self.ctx, count=4, indices={2: 73}) as backend, \
             patch.object(commands, "create", side_effect=AssertionError("no synthetic typed cancellation")):
            result = await self.api.cancel(position=2)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["selection_scope"], "current_unfiltered_government_source")
        self.assertEqual(result["source_position"], 2)
        self.assertEqual(backend.generated_positions, [2])
        self.assertEqual(backend.calls, ["Country.AccessGovernmentConstructionQueue", ("element", 2),
            "ConstructionQueueElement.CancelConstruction", "carrier", "clone"])
        self.assertEqual(backend.command_owners[0].calls, ["validate", "submit"])
        self.assertEqual(backend.command_owners[0].flags, 4)
        self.assertEqual(len(backend.released), 3)
        self.assertEqual(self.ctx.owner_pool.snapshot(), ())

    async def test_observe_then_cancel_makes_new_selection_not_old_wrapper_reuse(self):
        with QueueBackend(self.ctx, count=2, indices={1: 40}) as backend:
            observation = await self.api.queue(start=1, limit=1)
            row = observation["items"][0]
            self.assertEqual(row["index"], 40)
            self.assertIn(row["native_element"].value_handle, backend.released)
            backend.count = 3
            backend.indices[1] = 72
            result = await self.api.cancel(position=row["source_position"])
        self.assertTrue(result["accepted"])
        self.assertEqual(backend.calls.count("Country.AccessGovernmentConstructionQueue"), 2)
        self.assertEqual(backend.generated_positions, [1])
        self.assertEqual(backend.command_owners[0].calls, ["validate", "submit"])

    async def test_native_false_keeps_empty_reason_and_destroys_only_clone(self):
        self.ctx.validation_reason = ""
        with QueueBackend(self.ctx) as backend:
            backend.command_valid = False
            result = await self.api.cancel(position=0)
        self.assertIs(result["accepted"], False)
        self.assertIs(result["eligible"], False)
        self.assertEqual(result["reason"], "")
        self.assertEqual(result["validation"].validated, 0)
        self.assertEqual(backend.command_owners[0].calls, ["validate", "destroy"])
        self.assertEqual(len(backend.released), 3)

    async def test_raw_copied_element_kind_keeps_genuine_owned_binding(self):
        with QueueBackend(self.ctx) as backend:
            original_element = backend.element
            def raw_element(*args, **kwargs):
                query = original_element(*args, **kwargs)
                query.result = queue_result(**{**vars(query.result), "kind": data.Kind.RAW, "object": 0})
                return query
            with patch.object(data, "list_element", side_effect=raw_element):
                result = await self.api.cancel(position=1)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["native_element"].kind, data.Kind.RAW)
        self.assertTrue(backend.bindings[0].retained)
        self.assertEqual(backend.command_owners[0].calls, ["validate", "submit"])

    async def test_receiver_rejection_is_not_replayed(self):
        with QueueBackend(self.ctx) as backend:
            backend.command_accepted = False
            result = await self.api.cancel(position=0)
        self.assertIs(result["accepted"], False)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(backend.generated_positions, [0])
        self.assertEqual(backend.command_owners[0].calls, ["validate", "submit"])

    async def test_explicit_foreign_country_does_not_generate_local_player_cancel(self):
        self.ctx.explicit_country_id = 156
        with QueueBackend(self.ctx) as backend:
            backend.local_player = False
            result = await self.api.cancel(position=0)
        self.assertEqual(result["status"], "unavailable_for_country")
        self.assertIs(result["country_action_scope"]["value"], False)
        self.assertEqual(backend.calls, ["Country.IsLocalPlayer"])
        self.assertEqual(backend.command_owners, [])

    async def test_explicit_local_country_uses_original_player_domain_then_current_source(self):
        self.ctx.explicit_country_id = 156
        with QueueBackend(self.ctx) as backend:
            result = await self.api.cancel(position=0)
        self.assertTrue(result["accepted"])
        self.assertIs(result["country_action_scope"]["value"], True)
        self.assertEqual(backend.calls[0], "Country.IsLocalPlayer")
        self.assertEqual(backend.generated_positions, [0])

    async def test_source_and_element_native_errors_keep_original_results(self):
        for error_stage in ("source", "element"):
            with self.subTest(stage=error_stage), QueueBackend(self.ctx) as backend:
                setattr(backend, error_stage + "_error", 13)
                result = await self.api.cancel(position=0)
            self.assertEqual(result["native_result"].error, 13)
            self.assertIsNone(result["accepted"])
            self.assertEqual(backend.command_owners, [])
            self.assertEqual(backend.generated_positions, [])
            self.assertTrue(result["source_result"] is not None)

    async def test_missing_current_position_returns_native_element_error_without_count_scan(self):
        with QueueBackend(self.ctx, count=1) as backend:
            result = await self.api.cancel(position=4)
        self.assertEqual(result["native_element"].error, 1168)
        self.assertEqual(backend.calls, ["Country.AccessGovernmentConstructionQueue", ("element", 4)])
        self.assertEqual(backend.command_owners, [])

    async def test_generation_native_error_keeps_source_and_command_result_without_submit(self):
        with QueueBackend(self.ctx) as backend:
            backend.generation_error = 13
            result = await self.api.cancel(position=0)
        self.assertEqual(result["native_result"].error, 13)
        self.assertEqual(backend.generated_positions, [0])
        self.assertEqual(backend.command_owners, [])
        self.assertEqual(len(backend.released), 3)

    async def test_partial_optional_tooltip_does_not_gate_native_cancel_or_erase_bytes(self):
        with QueueBackend(self.ctx) as backend:
            backend.partial = "tooltip"
            result = await self.api.cancel(position=0, details=True)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["tooltip"]["snapshot"], "山".encode("utf-8"))
        self.assertEqual(result["tooltip"]["error"], 234)
        self.assertEqual(result["cleanup"]["status"], "partial_copy_retained")
        self.assertEqual(backend.command_owners[0].calls, ["validate", "submit"])
        self.assertEqual(backend.released, [])
        self.assertEqual(len(self.ctx.owner_pool.snapshot()), 1)

    async def test_cleanup_failure_does_not_replace_accepted_native_result(self):
        with QueueBackend(self.ctx) as backend:
            backend.cleanup_error = RuntimeError("release failed")
            result = await self.api.cancel(position=0)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["native_result"].accepted, 1)
        self.assertEqual(result["cleanup"]["status"], "cleanup_unknown_retained")
        self.assertEqual(backend.command_owners[0].calls, ["validate", "submit"])

    async def test_pending_selection_resumes_same_source_and_submits_once(self):
        from pdx_runtime.query_executor import PendingOperation, SerializedQueryExecutor, RetainedOwners
        self.ctx.owner_pool = RetainedOwners()
        self.ctx.session.owner_pool = self.ctx.owner_pool
        executor = SerializedQueryExecutor(timeout=0, poll_interval=.0001, owners=self.ctx.owner_pool)
        self.ctx._wake, self.ctx._finish = executor._wake, executor._finish
        async def query(label, factory, owner=None):
            return await executor.query(label, factory, owner=owner)
        async def perform(label, work):
            return await executor.perform(label, work, context=self.ctx)
        self.ctx.query, self.ctx.perform = query, perform
        with QueueBackend(self.ctx) as backend:
            backend.gate = asyncio.Event()
            pending = await self.api.cancel(position=1)
            self.assertIsInstance(pending, PendingOperation)
            try:
                await asyncio.wait_for(backend.read_started.wait(), timeout=1)
                self.assertEqual(backend.calls, ["Country.AccessGovernmentConstructionQueue"])
            finally:
                backend.gate.set()
                result = await pending.resume(timeout=1)
        self.assertTrue(result["accepted"])
        self.assertEqual(backend.calls.count("Country.AccessGovernmentConstructionQueue"), 1)
        self.assertEqual(backend.generated_positions, [1])
        self.assertEqual(backend.command_owners[0].calls, ["validate", "submit"])
        self.assertEqual(self.ctx.owner_pool.snapshot(), ())

    async def test_invalid_position_fails_before_native_work(self):
        for position in (True, -1, 0x80000000, "0", {}):
            with self.assertRaises(ValueError):
                await self.api.cancel(position=position)
        self.assertEqual(self.ctx.queries, [])


if __name__ == "__main__":
    unittest.main()
