"""Offline count harness. Never constructs a live bridge or process."""
from collections import Counter
from contextlib import ExitStack
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands, v3_objects as objects
from pdx_runtime.v3_game.construction import ConstructionAPI
from tests.v3_game.test_v3_game_construction import Context, Query, Owner


class CountBackend:
    def __init__(self, states=2, types=4, new_types=2, *, fields=None):
        self.ctx = Context()
        self.states = [NS(resolved_id=13+i, owner_id=156, valid=True) for i in range(states)]
        self.buildings = [NS(resolved_id=100+s*types+t, state_id=13+s,
            levels=t, type_pointer=0x55500+t, valid=True) for s in range(states) for t in range(types)]
        self.candidates = {row.resolved_id: [f"building_{t}" for t in range(types+new_types)] for row in self.states}
        self.fields = fields or {}
        self.handles = {}
        self.handle = 1000
        self.released = []
        self.owners = []
        self.field_reads = Counter()
        self.produced_items = 0
        self.metadata = {
            88: ("GetPotentialUrbanBuildings",),
            103: ("GetNameNoFormatting", "GetMaxLevels", "GetTotalLevels"),
            207: ("GetKey", "GetNameNoFormatting", "GetRequiredConstruction", "GetConstructionCosts"),
        }

    def result(self, value=None, *, retained=True, error=0):
        if retained:
            self.handle += 1
            self.handles[self.handle] = value
        return Query(NS(error=error, value=value, value_handle=self.handle if retained else 0))

    def functions(self, native, type_id, *, start, capacity, **kw):
        rows = [NS(name=name, argument_count=0, callbacks=(1,0,0,0)) for name in self.metadata[type_id]]
        return Query(NS(error=0, rows=rows, next=len(rows), total=len(rows)))

    def objects(self, native, domain, **kw):
        raise AssertionError("selected State menus must not enumerate World objects")

    def scalar(self, key, getter):
        self.field_reads[key, getter] += 1
        defaults = {"GetKey":key, "GetNameNoFormatting":f"Name {key}",
            "GetRequiredConstruction":Decimal(0), "GetConstructionCosts":None,
            "GetMaxLevels":0, "GetTotalLevels":0}
        result = self.fields.get((key, getter), defaults[getter])
        return result if isinstance(result, tuple) else (result, 0)

    def evaluate(self, native, expression, bindings, **kw):
        binding, = bindings
        if expression.startswith("State."):
            query = self.result(None)
            handle = query.result.value_handle
            self.handles[handle] = ("source", binding.object)
            query.result.value = data.NativeList(handle, 0, b"", "owned")
            return query
        if binding.retained:
            state, key = self.handles[binding.object]
        else:
            row = next(row for row in self.buildings if row.resolved_id == binding.object)
            key = f"building_{row.type_pointer-0x55500}"
        value, error = self.scalar(key, expression.rsplit(".", 1)[-1])
        return self.result(value, error=error)

    def count(self, native, handle, **kw):
        _, state = self.handles[handle]
        return Query(NS(error=0, total=len(self.candidates[state])))

    def element(self, native, handle, index, **kw):
        _, state = self.handles[handle]
        query = self.result((state,self.candidates[state][index]))
        retained = query.result.value_handle
        query.result.value = data.NativeObject(0,103,retained,"CPdxPtr@VCDataBuilding@@")
        return query

    def projection(self, native, source, index, expression, bindings, **kw):
        if "expected_count" in kw:
            raise AssertionError("field copy must not add a second source-count gate")
        binding, = bindings
        candidates = self.candidates[binding.object]
        self.produced_items += len(candidates)
        value, error = self.scalar(candidates[index], expression.rsplit(".",1)[-1]) if candidates else (None,1168)
        return Query(NS(error=error, value=value, source_count=len(candidates)))

    def release(self, native, handle, **kw):
        if handle not in self.handles:
            raise AssertionError("released owner twice")
        del self.handles[handle]
        self.released.append(handle)
        return Query(NS(error=0,value_handle=0))

    def create(self, native, token, *, parameters, actor_id, **kw):
        key = parameters[10232]
        owner = Owner(self.ctx,token,actor_id,parameters,valid=key != "building_1")
        self.owners.append(owner)
        return Query(NS(error=0,owner=owner))

    def patches(self):
        stack = ExitStack()
        for name, replacement in (("enumerate_functions", self.functions), ("evaluate",self.evaluate),
                ("list_count",self.count),("list_element",self.element),("evaluate_element",self.projection),
                ("release",self.release)):
            stack.enter_context(patch.object(data,name,replacement))
        stack.enter_context(patch.object(objects,"enumerate_objects",self.objects))
        stack.enter_context(patch.object(commands,"create",self.create))
        stack.enter_context(patch.object(commands.NativeCommand,"from_result",lambda native,result,**kw:result.owner))
        return stack

    async def run(self, api_class=ConstructionAPI, **options):
        with self.patches():
            rows = await api_class(self.ctx).options(state=13, **options)
        counts = Counter(label for label,*_ in self.ctx.queries)
        return {"rows":rows,"queries":len(self.ctx.queries),"counts":dict(counts),
            "source_produced_items_model":self.produced_items,
            "unreleased_handles":len(self.handles), "command_calls":dict(Counter(call for owner in self.owners for call in owner.calls)),
            "field_reads":dict(self.field_reads)}




class ConstructionComplexityTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_source_keys_do_not_repeat_type_details(self):
        backend = CountBackend(states=1, types=4, new_types=0)
        result = await backend.run(validate=True)
        self.assertEqual({row["building_key"] for row in result["rows"]},
            {"building_0", "building_1", "building_2", "building_3"})
        self.assertEqual(len(result["rows"]), 4)
        for key in ("building_0", "building_1", "building_2", "building_3"):
            self.assertEqual(result["field_reads"][key, "GetRequiredConstruction"], 1)
        self.assertEqual(result["unreleased_handles"], 0)
        self.assertNotIn("submit", result["command_calls"])

    async def test_default_discovery_keeps_data_without_implicit_validation(self):
        result = await CountBackend(states=2, types=4, new_types=2).run()
        self.assertEqual(result["command_calls"], {})
        self.assertEqual(result["unreleased_handles"], 0)
        self.assertEqual(len(result["rows"]), 6)
        for row in result["rows"]:
            self.assertIsNone(row["eligible"])
            self.assertEqual(row["validation_status"], "not_requested")
            self.assertIsNone(row["reason_details"]["eligible"])
            self.assertFalse(row["reason_complete"])
            for field in ("existing_levels", "maximum_levels", "required_construction"):
                self.assertEqual(row["reason_details"][field], row[field])

    async def test_explicit_validation_keeps_native_false_and_cleans_all_commands(self):
        result=await CountBackend(2,4,2).run(validate=True,details=True)
        expected = len(result["rows"])
        self.assertEqual(result["command_calls"], {"serialize": expected, "validate": expected, "destroy": expected})
        rejected=[row for row in result["rows"] if row["building_key"]=="building_1"]
        self.assertEqual(len(rejected),1)
        self.assertTrue(all(row["eligible"] is False for row in rejected))
        self.assertTrue(all(row["validation_status"]=="validated" for row in rejected))
        self.assertTrue(all(row["reason_raw"] for row in rejected))

    async def test_new_candidates_keep_zero_false_none_and_native_field_errors(self):
        fields={("building_4","GetMaxLevels"):False,
            ("building_4","GetRequiredConstruction"):(None,13),
            ("building_4","GetConstructionCosts"):0}
        after=await CountBackend(1,4,1,fields=fields).run()
        row=after["rows"][-1]
        self.assertEqual(row["building_key"],"building_4")
        self.assertIs(row["maximum_levels"],False)
        self.assertEqual(row["existing_levels"],0)
        self.assertIsNone(row["required_construction"])
        self.assertEqual(row["required_construction_reason"],"native getter error 13")
        self.assertEqual(row["construction_costs_description"],0)
        self.assertEqual(after["unreleased_handles"],0)

    async def test_unknown_key_keeps_partial_candidate_and_fallback_reads(self):
        fields={("building_4","GetKey"):(None,13)}
        async def missing(*args):
            raise RuntimeError("original key source unavailable")
        with patch.object(ConstructionAPI,"_generated_key",missing):
            after=await CountBackend(1,4,1,fields=fields).run()
        self.assertEqual(len(after["rows"]),5)
        row=after["rows"][-1]
        self.assertIsNone(row["building_key"])
        self.assertEqual(row["building_key_reason"],"original key source unavailable")
        self.assertIsNotNone(row["name"])
        self.assertIsNone(row["eligible"])
        self.assertEqual(after["unreleased_handles"],0)


    async def test_new_observation_does_not_reuse_candidate_values_or_seen_keys(self):
        backend=CountBackend(1,4,1)
        api=ConstructionAPI(backend.ctx)
        with backend.patches():
            first=await api.options(state=13)
            backend.fields[("building_4","GetMaxLevels")]=17
            backend.candidates[13].append("building_5")
            second=await api.options(state=13)
        self.assertEqual(len(first),5)
        self.assertEqual(len(second),6)
        self.assertEqual(first[-1]["maximum_levels"],0)
        self.assertEqual(second[-2]["maximum_levels"],17)
        self.assertEqual(second[-1]["building_key"],"building_5")
        self.assertEqual(backend.handles,{})
        self.assertEqual(backend.owners,[])

    async def test_details_alone_does_not_enable_validation(self):
        result=await CountBackend(2,4,2).run(details=True)
        self.assertEqual(result["command_calls"],{})
        self.assertTrue(all(row["eligible"] is None for row in result["rows"]))
        self.assertTrue(all(row["validation_status"]=="not_requested" for row in result["rows"]))

    async def test_failed_owned_element_keeps_scoped_recovery_and_original_cleanup(self):
        backend=CountBackend(1,0,3)
        original=backend.element
        def failed(*args,**kwargs):
            query=original(*args,**kwargs)
            query.result.error=13
            return query
        backend.element=failed
        result=await backend.run()
        self.assertEqual(len(result["rows"]),3)
        self.assertEqual(result["command_calls"],{})
        self.assertEqual(result["unreleased_handles"],0)
        self.assertTrue(any(row.get("native_error")==13 for row in backend.ctx.diagnostics))
        self.assertTrue(any(label.startswith("construction.source.") for label in result["counts"]))


    async def test_raw_provider_candidates_are_not_lost_or_validated_by_default(self):
        backend=CountBackend(2,0,3)
        original_element=backend.element
        def borrowed(*args,**kwargs):
            query=original_element(*args,**kwargs)
            query.result.value=data.NativeObject(0,103,query.result.value_handle,"PEBVCDataBuilding@@")
            return query
        backend.element=borrowed
        result=await backend.run()
        self.assertEqual(len(result["rows"]),3)
        self.assertEqual(result["command_calls"],{})
        self.assertEqual(result["unreleased_handles"],0)
        self.assertTrue(all(row["validation_status"]=="not_requested" for row in result["rows"]))
        self.assertTrue(all(row["eligible"] is None for row in result["rows"]))
        self.assertEqual([row["building_key"] for row in result["rows"]],
            ["building_0","building_1","building_2"])
        self.assertEqual(result["counts"]["construction.source.Building.GetBuildingType.GetKey"], len(result["rows"]))

    async def test_completed_owned_source_does_not_repeat_the_scoped_menu_scan(self):
        for states,types in ((1,4),(2,4),(2,8)):
            result=await CountBackend(states,types,0).run()
            self.assertEqual(result["source_produced_items_model"],0)
            self.assertFalse(any(label.startswith("construction.source.") for label in result["counts"]))



if __name__ == "__main__":
    unittest.main()
