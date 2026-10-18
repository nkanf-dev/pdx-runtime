"""CPU-only building status composition; no session/process/DLL is opened."""
from __future__ import annotations

from decimal import Decimal, localcontext, Inexact, Rounded
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_data as data, v3_objects as objects
from pdx_runtime.v3_game.economy import building_status as status
from pdx_runtime.v3_game import _text as localizer

TYPES = {"Building": 902, "State": 904, "BuildingType": 906,
    "ProductionMethodGroup": 910, "ProductionMethod": 912,
    "CFixedPoint": 923, "int64": 925, "bool": 927, "string": 929, "list": 931, "raw": 933}
BUILDING, STATE, COUNTRY = 0xAB000007, 0xCD000003, 0xEF000001


class Query:
    def __init__(self, result): self.result = result; self.reads = 0
    def read(self): self.reads += 1; return self.result


class Context:
    def __init__(self):
        self.native, self.process, self.session = object(), object(), object()
        self.adapters = {name: name + "-adapter" for name in ("data", "objects")}
        self.world = 70000
        self.diagnostics, self.queries = [], []
        self.fail_expression = None
    async def refresh(self): return NS(world=self.world)
    async def type_id(self, name): return TYPES[name]
    async def query(self, label, factory):
        query = factory()
        self.queries.append((label, query))
        if label == self.fail_expression:
            raise TimeoutError("original query retained")
        return query.read()
    async def text(self, ctx, building_id, expression):
        assert ctx is self and building_id == BUILDING
        return {"text": "真实原因：" + expression, "raw_text": "\x15header 原生原因：" + expression,
            "text_complete": True, "decode_error": None, "context_supplied": True,
            "native_temporaries_destroyed": True, "world": self.world, "localization_success": None}


class BuildingStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = Context()
        self.formatter = AsyncMock(side_effect=self.ctx.text)
        self.metadata, self.calls, self.releases, self.values, self.lists = {}, [], [], {}, {}
        self.handlers, self.next_handle = {}, 0
        self.state_error = self.building_error = self.release_error = 0
        self.object_world = self.ctx.world
        self.pm_mode = "empty"
        self.metadata_pages = []
        for name in ("Building", "State", "BuildingType", "ProductionMethodGroup", "ProductionMethod"):
            self.metadata[TYPES[name]] = []
        self.add("Building", "GetNameNoFormatting", "string")
        self.add("Building", "GetBuildingType", "raw")
        self.add("BuildingType", "GetKey", "string")
        self.add("Building", "AccessProductionMethodGroups", "list")
        for section, field, getter, expected, unit in status._BUILDING:
            self.add("Building", getter, {"fixed": "CFixedPoint", "integer": "int64", "boolean": "bool"}[expected])
        for field, getter, expected, unit in status._STATE:
            self.add("State", getter, {"fixed": "CFixedPoint", "integer": "int64", "boolean": "bool"}[expected])
        for label, getter, scope in status._TEXTS:
            self.add("Building", getter, "string")
        for receiver in ("ProductionMethodGroup", "ProductionMethod"):
            self.add(receiver, "GetKey", "string")
            self.add(receiver, "GetNameNoFormatting", "string")
            self.add(receiver, "GetName", "string")
        self.add("Building", "AccessProductionMethod", "raw", arguments=1)
        self.add("ProductionMethodGroup", "AccessBuildingProductionMethods", "list", arguments=1)
        self.patches = [patch.object(localizer, "read_building_text", self.formatter),
            patch.object(data, "enumerate_functions", self.functions),
            patch.object(data, "evaluate", self.evaluate), patch.object(data, "release", self.release),
            patch.object(data, "list_count", self.list_count), patch.object(data, "list_element", self.list_element),
            patch.object(objects, "read_ids", self.read_ids)]
        for item in self.patches: item.start(); self.addCleanup(item.stop)

    def add(self, receiver, name, result, *, arguments=0, callbacks=(1,0,0,0)):
        self.metadata[TYPES[receiver]].append(NS(type_id=TYPES[receiver], name=name,
            return_type_id=TYPES[result], argument_count=arguments, callbacks=callbacks))

    def result(self, kind, value=None, *, type_id=None, error=0, handle=None):
        if handle is None:
            self.next_handle += 1; handle = self.next_handle
        if type_id is None:
            type_id = {data.Kind.STRING: TYPES["string"], data.Kind.FIXED: TYPES["CFixedPoint"],
                data.Kind.I64: TYPES["int64"], data.Kind.BOOL: TYPES["bool"], data.Kind.LIST: TYPES["list"]}.get(kind, TYPES["raw"])
        raw = value.encode("utf-8") if isinstance(value,str) else bytes(8)
        result = NS(error=error, kind=kind, type_id=type_id, value=value, value_handle=handle,
            snapshot=raw, snapshot_bytes=len(raw), total=len(raw), native_type_name="")
        self.values[handle] = result
        return result

    def functions(self, native, type_id, *, start, capacity, **options):
        self.assertIs(native, self.ctx.native); self.assertIs(options["process"], self.ctx.process)
        rows = self.metadata[type_id]; page = rows[start:start+3]
        self.metadata_pages.append((type_id,start))
        return Query(NS(error=0, rows=tuple(page), next=min(start+3,len(rows)), total=len(rows)))

    def evaluate(self, native, expression, bindings, *, output_bytes=4096, **options):
        self.assertIs(native, self.ctx.native); self.assertIs(options["process"], self.ctx.process)
        self.calls.append((expression,tuple(bindings)))
        if expression in self.handlers: return Query(self.handlers[expression](bindings))
        if expression == "Building.GetNameNoFormatting": return Query(self.result(data.Kind.STRING,"工艺装备工坊"))
        if expression == "Building.GetBuildingType.GetKey": return Query(self.result(data.Kind.STRING,"building_tooling_workshops"))
        if expression == "Building.AccessProductionMethodGroups":
            result = self.result(data.Kind.LIST)
            result.value = data.NativeList(result.value_handle,0x9000,b"","model")
            self.lists[result.value_handle] = ([] if self.pm_mode=="empty" else [
                {"kind":"group","key":"pmg_tools","name":"生产方式","current_key":"pm_basic","current_name":"基础工具"}])
            return Query(result)
        if expression.startswith("Building.AccessProductionMethod(ProductionMethodGroup.Self)."):
            group = self.values[bindings[1].object].record
            field = "current_key" if expression.endswith(".GetKey") else "current_name"
            return Query(self.result(data.Kind.STRING,group[field]))
        if expression == "ProductionMethodGroup.AccessBuildingProductionMethods(Building.Self)":
            result = self.result(data.Kind.LIST); result.value = data.NativeList(result.value_handle,0x9000,b"","model")
            self.lists[result.value_handle] = [{"kind":"method","key":"pm_basic","name":"基础工具"},
                {"kind":"method","key":"pm_advanced","name":"先进工具"}]
            return Query(result)
        if expression.startswith("ProductionMethodGroup.") or expression.startswith("ProductionMethod."):
            record = self.values[bindings[0].object].record
            return Query(self.result(data.Kind.STRING,record["key" if expression.endswith(".GetKey") else "name"]))
        receiver, getter = expression.split(".",1)
        row = next(row for row in self.metadata[TYPES[receiver]] if row.name==getter)
        if row.return_type_id==TYPES["int64"]:
            number = {"GetNoOfEmployed":78,"GetEmployeeCap":5000,"GetNumUnemployedWorkingAdults":1500}.get(getter,0)
            return Query(self.result(data.Kind.I64,number))
        if row.return_type_id==TYPES["bool"]:
            return Query(self.result(data.Kind.BOOL,getter in ("ShouldShowAsUnableToHire","HasFailedHires")))
        if row.return_type_id==TYPES["CFixedPoint"]:
            raw = {"GetEmploymentPercentage":1560,"GetAverageAnnualWage":250000,"GetMarketAccess":100000,
                "CalcInputAvailabilityOutputMult":100000}.get(getter,0)
            return Query(self.result(data.Kind.FIXED,data.FixedPoint(raw)))
        raise AssertionError(expression)

    def list_count(self, native, handle, **options): return Query(NS(error=0,total=len(self.lists[handle])))

    def list_element(self, native, handle, index, **options):
        row = self.lists[handle][index]
        result = self.result(data.Kind.OBJECT)
        result.record = row
        if self.pm_mode=="borrowed":
            result.error = 50; result.value = None
        else:
            type_name = "ProductionMethodGroup" if row["kind"]=="group" else "ProductionMethod"
            result.value = data.NativeObject(0x5000,TYPES[type_name],result.value_handle,"CPdxPtr@VCData"+type_name+"@@")
        return Query(result)

    def release(self, native, handle, **options):
        self.releases.append(handle)
        return Query(NS(error=self.release_error,value_handle=handle if self.release_error else 0))

    def read_ids(self, native, domain, ids, **options):
        self.assertIs(native,self.ctx.native);self.assertIs(options["process"],self.ctx.process)
        if domain==objects.Domain.BUILDING:
            error=self.building_error;row=NS(valid=not error,resolved_id=ids[0],state_id=STATE,levels=1)
        else:
            error=self.state_error;row=NS(valid=not error,resolved_id=ids[0],owner_id=COUNTRY)
        return Query(NS(error=error,world=self.object_world,records=(row,)))

    def assert_clean(self):
        self.assertEqual(set(self.releases),set(self.values))
        self.assertEqual(len(self.releases),len(set(self.releases)))
        self.assertFalse(getattr(self.ctx,"_building_status_retained",[]))
        self.assertTrue(all(q.reads==1 for _,q in self.ctx.queries))

    async def test_structured_staffing_flags_wages_market_and_subsidy_are_native_values(self):
        result=await status.read_building_status(self.ctx,BUILDING)
        self.assertEqual(result["id"],BUILDING);self.assertEqual(result["country_id"],COUNTRY)
        self.assertEqual(result["employment"]["employees"],78)
        self.assertEqual(result["employment"]["capacity"],5000)
        self.assertEqual(result["employment"]["fraction"],Decimal("0.01560"))
        self.assertEqual(result["hiring"]["display_states"],["unable_to_hire"])
        self.assertTrue(result["hiring"]["has_failed_hires"])
        self.assertFalse(result["subsidies"]["enabled"])
        self.assertEqual(result["wages"]["average_annual"],Decimal("2.5"))
        self.assertEqual(result["state"]["unemployed_working_adults"],1500)
        self.assertEqual(result["state"]["market_access"],Decimal(1))
        self.assertIn("state-wide",result["state"]["scope"])
        self.assertIn("not an inferred",result["hiring"]["indicator_scope"])
        self.assertFalse(result["input_supply"]["has_shortage"])
        self.assert_clean()

    async def test_actual_reason_text_uses_building_context_not_bare_data_getter(self):
        result=await status.read_building_status(self.ctx,BUILDING)
        texts=result["hiring"]["descriptions"]
        for name in ("not_hiring","failed_hires","hiring_target","expansion_qualifications"):
            self.assertTrue(texts[name]["complete"])
            self.assertIn("真实原因",texts[name]["text"])
            self.assertIn("\x15header",texts[name]["raw_text"])
        self.assertEqual(self.formatter.await_count,4)
        text_getters={getter for _,getter,_ in status._TEXTS}
        self.assertFalse(any(expr.split(".")[-1] in text_getters for expr,_ in self.calls))
        self.assertIn("not proof",texts["expansion_qualifications"]["scope"])
        self.assert_clean()

    async def test_details_false_skips_all_rich_formatting(self):
        result=await status.read_building_status(self.ctx,BUILDING,details=False)
        self.assertEqual(result["hiring"]["descriptions"],{})
        self.formatter.assert_not_awaited()
        self.assertEqual(result["employment"]["employees"],78)
        self.assert_clean()

    async def test_missing_scalar_is_none_not_zero_and_other_observations_continue(self):
        self.metadata[TYPES["Building"]]=[row for row in self.metadata[TYPES["Building"]] if row.name!="GetEmployeeCap"]
        result=await status.read_building_status(self.ctx,BUILDING,details=False)
        self.assertIsNone(result["employment"]["capacity"])
        self.assertIn("missing",result["employment"]["reasons"]["capacity"])
        self.assertEqual(result["employment"]["employees"],78)
        self.assert_clean()

    async def test_native_failure_or_wrong_kind_does_not_become_a_hiring_cause(self):
        self.handlers["Building.GetAverageAnnualWage"]=lambda bs:self.result(data.Kind.FIXED,data.FixedPoint(0),error=13)
        self.handlers["Building.ShouldShowAsUnableToHire"]=lambda bs:self.result(data.Kind.I64,1,type_id=TYPES["bool"])
        result=await status.read_building_status(self.ctx,BUILDING,details=False)
        self.assertIsNone(result["wages"]["average_annual"])
        self.assertIsNone(result["hiring"]["unable_to_hire"])
        self.assertFalse(result["hiring"]["indicators_complete"])
        self.assertNotIn("diagnosis",result)
        self.assertEqual(result["input_supply"]["penalty"],Decimal(0))
        self.assert_clean()

    async def test_action_shaped_metadata_is_never_invoked(self):
        for row in self.metadata[TYPES["Building"]]:
            if row.name=="GetSubsidies":row.callbacks=(0,0,1,0)
        result=await status.read_building_status(self.ctx,BUILDING,details=False)
        self.assertIsNone(result["subsidies"]["amount"])
        self.assertFalse(any(expr=="Building.GetSubsidies" for expr,_ in self.calls))
        self.assert_clean()

    async def test_state_unavailable_is_explicit_unknown_not_zero_labour_or_market(self):
        self.state_error=1168
        result=await status.read_building_status(self.ctx,BUILDING,details=False)
        self.assertIsNone(result["country_id"])
        self.assertIsNone(result["state"]["market_access"])
        self.assertIsNone(result["state"]["unemployed_working_adults"])
        self.assertIn("could not be observed",result["state"]["reason"])
        self.assertFalse(any(expr.startswith("State.") for expr,_ in self.calls))
        self.assert_clean()

    async def test_observed_world_change_preserves_status_and_adds_diagnostic(self):
        self.object_world+=1
        result=await status.read_building_status(self.ctx,BUILDING,details=False)
        self.assertEqual(result["id"],BUILDING)
        self.assertTrue(result["available"])
        self.assertTrue(self.calls)
        self.assertTrue(any(item.get("event")=="world_changed" for item in self.ctx.diagnostics))
        self.assert_clean()


    async def test_missing_building_is_available_false_not_empty_factory(self):
        self.building_error=1168
        result=await status.read_building_status(self.ctx,BUILDING)
        self.assertFalse(result["available"])
        self.assertFalse(self.calls)

    async def test_fixed_quantities_use_shared_exact_scale_under_low_precision(self):
        self.handlers["Building.GetAverageAnnualWage"]=lambda bs:self.result(data.Kind.FIXED,data.FixedPoint((1<<63)-1))
        with localcontext() as context:
            context.prec=2;context.traps[Inexact]=context.traps[Rounded]=True
            result=await status.read_building_status(self.ctx,BUILDING,details=False)
        self.assertEqual(result["wages"]["average_annual"],Decimal("92233720368547.75807"))
        self.assert_clean()


    async def test_unknown_formatter_prefix_keeps_status_read_and_never_replays(self):
        self.formatter.side_effect=RuntimeError("original formatter prefix retained")
        with self.assertRaisesRegex(RuntimeError,"prefix retained"):
            await status.read_building_status(self.ctx,BUILDING)
        self.formatter.assert_awaited_once()
        self.assertFalse(self.releases)
        self.assertTrue(self.ctx._building_status_retained)

    async def test_settled_local_copy_error_closes_status_values(self):
        error=OSError("settled local copy error")
        record={"native_phase":{"settled":True}}
        localizer._annotate_error(error,record)
        self.formatter.side_effect=error
        with self.assertRaisesRegex(OSError,"settled local copy error"):
            await status.read_building_status(self.ctx,BUILDING)
        self.formatter.assert_awaited_once()
        self.assertTrue(self.releases);self.assertFalse(getattr(self.ctx,"_building_status_retained",[]))
        self.assert_clean()

    async def test_original_production_groups_current_and_candidates_use_actual_keys(self):
        self.pm_mode="owned"
        result=await status.read_building_status(self.ctx,BUILDING,details=False)
        group,=result["production_methods"]["groups"]
        self.assertEqual(group["key"],"pmg_tools")
        self.assertEqual(group["current"]["key"],"pm_basic")
        self.assertEqual([row["key"] for row in group["candidates"]],["pm_basic","pm_advanced"])
        self.assertEqual([row["selected"] for row in group["candidates"]],[True,False])
        parameter_reads=[bindings for expr,bindings in self.calls if "ProductionMethodGroup.Self" in expr]
        self.assertTrue(parameter_reads)
        self.assertTrue(all(bs[0].domain=="Building" and bs[1].retained for bs in parameter_reads))
        self.assertFalse(any(any(term in expr for term in ("Activate","ToggleSubsidies","Execute")) for expr,_ in self.calls))
        self.assert_clean()

    async def test_borrowed_pm_group_is_not_retagged_into_an_owning_context(self):
        self.pm_mode="borrowed"
        result=await status.read_building_status(self.ctx,BUILDING,details=False)
        self.assertIsNone(result["production_methods"]["groups"])
        self.assertIn("owning group",result["production_methods"]["reasons"]["groups"])
        self.assertEqual(result["employment"]["employees"],78)
        self.assertFalse(any("ProductionMethodGroup.Self" in expr for expr,_ in self.calls))
        self.assert_clean()

    async def test_incomplete_localized_text_never_claims_complete_reason(self):
        self.formatter.side_effect=None
        self.formatter.return_value={"text":None,"raw_text":"partial",
            "text_complete":False,"context_supplied":True,"native_temporaries_destroyed":True,
            "decode_error":None,"world":self.ctx.world,"reason":"native copy incomplete"}
        result=await status.read_building_status(self.ctx,BUILDING)
        self.assertFalse(result["hiring"]["descriptions"]["not_hiring"]["complete"])
        self.assertEqual(result["hiring"]["descriptions"]["not_hiring"]["raw_text"],"partial")
        self.assertEqual(result["employment"]["employees"],78)
        self.assert_clean()


if __name__=="__main__":unittest.main()
