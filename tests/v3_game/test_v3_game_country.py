"""CountryAPI CPU composition tests. No process, session or DLL is opened."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_objects as objects
from pdx_runtime.v3_game.country import CountryAPI, _plain_name


COUNTRY = 0xAB000001
STATE_A = 0xCD000003
STATE_B = 0xCD000004
BUILDING_A = 0xEF000007
BUILDING_B = 0xEF000008
TYPE_IDS = {"Country": 101, "State": 109, "Building": 117,
            "BuildingType": 131, "int64": 211, "CFixedPoint": 223}


def function(receiver, name, result_type=0, *, arguments=0, callbacks=(1, 0, 0, 0)):
    return SimpleNamespace(type_id=TYPE_IDS[receiver], name=name,
                           return_type_id=result_type, argument_count=arguments,
                           callbacks=callbacks)


def value(kind, *, handle=1, type_id=0, text="", signed=0, error=0, native_type_name=""):
    result = data.DataResult.from_bytes(data._request(data.Action.EVALUATE))
    if kind == data.Kind.STRING:
        snapshot = text.encode("utf-8")
    elif kind in (data.Kind.I64, data.Kind.FIXED):
        snapshot = struct.pack("<q", signed)
    else:
        snapshot = b""
    return replace(result, error=error, kind=kind, type_id=type_id, value_handle=handle,
                   snapshot=snapshot, snapshot_bytes=len(snapshot), total=len(snapshot),
                   payload_size=len(snapshot), raw_bits=signed & 0xFFFFFFFFFFFFFFFF,
                   any_type=0x1000, any_vtable=0x2000,
                   object=0x3000 if kind == data.Kind.OBJECT else 0,
                   native_type_name=native_type_name)


class Query:
    def __init__(self, result):
        self.result = result
        self.read_count = 0

    def read(self):
        self.read_count += 1
        return self.result


class Context:
    def __init__(self):
        self.native = object()
        self.process = object()
        self.session = object()  # intentionally has no live_session or native methods
        self.adapters = {"data": "custom-data", "objects": "custom-objects"}
        self.world = 0x1234
        self.diagnostics = []
        self.operations = []
        self.queries = []
        self.waited = []
        self.type_names = []
        self.fail_expression = None
        self.refresh_count = 0

    async def refresh(self):
        self.refresh_count += 1
        return {"world": self.world}

    async def country_id(self):
        return COUNTRY

    async def type_id(self, name):
        self.type_names.append(name)
        return TYPE_IDS[name]

    async def query(self, label, factory):
        query = factory()
        self.queries.append((label, query))
        if label == self.fail_expression:
            raise TimeoutError("unknown completion; original query retained")
        return await self.wait(query)

    async def wait(self, query):
        self.waited.append(query)
        return query.read()

    async def perform(self, label, work):
        self.operations.append(label)
        return await work()


class PlainNameTests(unittest.TestCase):
    def test_saved_country_and_state_control_markup_keeps_native_display_text(self):
        country = ("\x15tooltippable_name \x15tooltip:dw_1696362872,nAAAAA==,"
                   "DATA_COUNTRY_NAME_TOOLTIP,CountryTooltip \x16CHI156!\x16flag_overlay!大清\x15!\x15!")
        state = "\x15tooltippable_name \x15tooltip:dw_1919667357,FwAAAA==,EMPTY_STRING,StateTooltip 山东\x15!\x15!"
        self.assertEqual(_plain_name(country), "大清")
        self.assertEqual(_plain_name(state), "山东")

    def test_goods_icon_nbsp_and_nested_styles_leave_readable_name(self):
        goods = ("\x15tooltippable_name \x15tooltip:dw_241073271,...,"
                 "DATA_GOODS_NAME_TOOLTIP,GoodsTooltip \x16paper!\xa0纸张\x15!\x15!")
        self.assertEqual(_plain_name(goods), "纸张")
        self.assertEqual(_plain_name("#tooltippable_name #tooltip:goods,paper @paper! 纸张#!#!"), "纸张")
        self.assertEqual(_plain_name("#BOLD Iron Mine#!"), "Iron Mine")


class CountryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = Context()
        self.api = CountryAPI(self.ctx)
        self.released = []
        self.evaluated = []
        self.metadata_calls = []
        self.object_calls = []
        self.snapshots = []
        self.next_handle = 0
        self.metadata = {
            TYPE_IDS["Country"]: [function("Country", "GetName"),
                                  function("Country", "GetTotalPopulation", TYPE_IDS["int64"]),
                                  function("Country", "GetGDP", TYPE_IDS["CFixedPoint"])],
            TYPE_IDS["State"]: [function("State", "GetName")],
            TYPE_IDS["Building"]: [function("Building", "GetName"),
                                   function("Building", "GetBuildingType", 999)],
            TYPE_IDS["BuildingType"]: [function("BuildingType", "GetKey")],
        }
        self.handlers = {}
        self.pages = {
            objects.Domain.STATE: [
                [SimpleNamespace(error=0, resolved_id=STATE_A, owner_id=COUNTRY)],
                [SimpleNamespace(error=0, resolved_id=STATE_B, owner_id=COUNTRY),
                 SimpleNamespace(error=0, resolved_id=1, owner_id=COUNTRY + 1),
                 SimpleNamespace(error=1168, resolved_id=2, owner_id=COUNTRY)],
            ],
            objects.Domain.BUILDING: [
                [SimpleNamespace(error=0, resolved_id=BUILDING_A, state_id=STATE_A, levels=3),
                 SimpleNamespace(error=0, resolved_id=40, state_id=1, levels=77)],
                [SimpleNamespace(error=0, resolved_id=BUILDING_B, state_id=STATE_B, levels=0)],
            ],
        }
        self.metadata_page_size = 1  # force sparse metadata pagination
        self.release_error = None
        self.object_error = 0
        self.object_world = self.ctx.world
        self.object_stuck = False
        self.patches = [
            patch.object(data, "enumerate_functions", self.functions),
            patch.object(data, "evaluate", self.evaluate),
            patch.object(data, "release", self.release),
            patch.object(data, "snapshot", self.snapshot),
            patch.object(objects, "enumerate_objects", self.enumerate_objects),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def options(self, native, options, role):
        self.assertIs(native, self.ctx.native)
        self.assertIs(options["process"], self.ctx.process)
        self.assertEqual(options["adapter"], self.ctx.adapters[role])

    def functions(self, native, type_id, *, start, capacity, **options):
        self.options(native, options, "data")
        self.metadata_calls.append((type_id, start))
        rows = self.metadata[type_id]
        # Native function bucket indices need not equal the number of rows.
        index = start // 7
        selected = rows[index:index + self.metadata_page_size]
        next_ = min(index + self.metadata_page_size, len(rows)) * 7
        return Query(SimpleNamespace(error=0, rows=tuple(selected), next=next_, total=len(rows) * 7))

    def evaluate(self, native, expression, bindings, *, output_bytes, **options):
        self.options(native, options, "data")
        self.next_handle += 1
        handle = self.next_handle
        binding, = bindings
        self.evaluated.append((expression, binding, handle))
        if expression in self.handlers:
            return Query(self.handlers[expression](handle, binding))
        if expression == "Country.GetName":
            result = value(data.Kind.STRING, handle=handle, text="大清")
        elif expression == "Country.GetTotalPopulation":
            result = value(data.Kind.I64, handle=handle, type_id=TYPE_IDS["int64"],
                           signed=300000001, native_type_name="int64")
        elif expression == "Country.GetGDP":
            result = value(data.Kind.FIXED, handle=handle, type_id=TYPE_IDS["CFixedPoint"],
                           signed=123456789, native_type_name="CFixedPoint")
        elif expression == "State.GetName":
            result = value(data.Kind.STRING, handle=handle, text=f"州{binding.object}")
        elif expression == "Building.GetName":
            result = value(data.Kind.STRING, handle=handle, text="铁矿")
        elif expression == "Building.GetBuildingType.GetKey":
            self.assertEqual(binding.type_id, TYPE_IDS["Building"])
            self.assertEqual(binding.domain, "Building")
            self.assertFalse(binding.retained)
            result = value(data.Kind.STRING, handle=handle, text="building_iron_mine")
        else:
            raise AssertionError(expression)
        return Query(result)

    def release(self, native, handle, **options):
        self.options(native, options, "data")
        self.released.append(handle)
        if self.release_error:
            return Query(SimpleNamespace(error=self.release_error, value_handle=handle))
        return Query(SimpleNamespace(error=0, value_handle=0))

    def snapshot(self, native, handle, *, output_bytes, **options):
        self.options(native, options, "data")
        self.snapshots.append((handle, output_bytes))
        return Query(value(data.Kind.STRING, handle=handle, text="完整原生名称"))

    def enumerate_objects(self, native, domain, *, start_index, max_items, **options):
        self.options(native, options, "objects")
        self.object_calls.append((domain, start_index))
        pages = self.pages[domain]
        index = start_index // 13
        return Query(SimpleNamespace(error=self.object_error, world=self.object_world,
                    records=tuple(pages[index]), truncated=index + 1 < len(pages),
                    next_index=start_index if self.object_stuck else (index + 1) * 13))

    def assert_clean(self, *, capacity_diagnostics=False):
        self.assertEqual(set(self.released), {item[2] for item in self.evaluated})
        self.assertEqual(len(self.released), len(set(self.released)))
        self.assertEqual(self.api._retained_reads, [])
        if capacity_diagnostics:
            self.assertEqual([row.get("event") for row in self.ctx.diagnostics],["capacities"])
        else:self.assertEqual(self.ctx.diagnostics, [])
        self.assertEqual([query for _, query in self.ctx.queries], self.ctx.waited)
        self.assertTrue(all(query.read_count == 1 for query in self.ctx.waited))

    def capacity_metadata(self):
        for getter in ("GetPowerDelta","GetPowerIncome","GetDeficitPowerRatio","GetExcessPowerRatio"):
            self.metadata[TYPE_IDS["Country"]].append(function("Country",getter,TYPE_IDS["CFixedPoint"],arguments=1))
        for getter in ("GetInstitutionInvestmentBureaucracyCost","GetInstitutionInvestmentBureaucracyFactor","GetInstitutionInvestment"):
            self.metadata[TYPE_IDS["Country"]].append(function("Country",getter,TYPE_IDS["CFixedPoint"]))
            self.handlers["Country."+getter]=lambda handle,binding:value(data.Kind.FIXED,handle=handle,
                type_id=TYPE_IDS["CFixedPoint"],signed=2500000,native_type_name="CFixedPoint")
        for resource in ("bureaucracy","authority","influence"):
            for getter,raw in (("GetPowerDelta",-13412345),("GetPowerIncome",215678901),
                    ("GetDeficitPowerRatio",35246),("GetExcessPowerRatio",0)):
                expr="Country."+getter+"('"+resource+"')"
                self.handlers[expr]=lambda handle,binding,raw=raw:value(data.Kind.FIXED,handle=handle,
                    type_id=TYPE_IDS["CFixedPoint"],signed=raw,native_type_name="CFixedPoint")

    async def test_settled_capacity_text_copy_error_releases_self_and_prior_values(self):
        from pdx_runtime.v3_game import _text as localizer
        self.capacity_metadata()
        self.metadata[TYPE_IDS["Country"]].append(function("Country","GetPowerExpensesString",arguments=1))
        self.handlers["Country.Self"]=lambda handle,binding:value(data.Kind.OBJECT,handle=handle,
            type_id=TYPE_IDS["Country"],native_type_name="CDataCountry const *")
        async def completed(ctx,record,*args):
            record["native_phase"]={"settled":True,"calls":10}
            error=OSError("completed capacity copy failed");localizer._annotate_error(error,record);raise error
        with patch.object(localizer,"_format",side_effect=completed):
            with self.assertRaisesRegex(OSError,"completed capacity copy failed"):await self.api.capacities(details=True)
        self.assertEqual(set(self.released),{item[2] for item in self.evaluated})
        self.assertEqual(self.api._retained_reads,[]);self.assertEqual(self.ctx._localization_retained,[])

    async def test_unsettled_capacity_text_keeps_self_without_replaying(self):
        from pdx_runtime.v3_game import _text as localizer
        self.capacity_metadata()
        self.metadata[TYPE_IDS["Country"]].append(function("Country","GetPowerExpensesString",arguments=1))
        self.handlers["Country.Self"]=lambda handle,binding:value(data.Kind.OBJECT,handle=handle,
            type_id=TYPE_IDS["Country"],native_type_name="CDataCountry const *")
        async def pending(ctx,record,*args):
            record["native_phase"]={"settled":False,"calls":13}
            error=TimeoutError("original capacity prefix pending");localizer._annotate_error(error,record);raise error
        with patch.object(localizer,"_format",side_effect=pending) as formatter:
            with self.assertRaisesRegex(TimeoutError,"original capacity prefix pending"):await self.api.capacities(details=True)
        self.assertEqual(formatter.call_count,1)
        held=[handle for expression,_,handle in self.evaluated if expression=="Country.Self"]
        self.assertEqual(len(held),1);self.assertNotIn(held[0],self.released)
        self.assertTrue(self.api._retained_reads);self.assertTrue(self.ctx._localization_retained)

    async def test_capacities_uses_real_parameterized_topbar_getters_and_exact_units(self):
        self.capacity_metadata()
        result=await self.api.capacities()
        self.assertEqual(result["country_id"],COUNTRY)
        for resource in ("bureaucracy","authority","influence"):
            row=result[resource]
            self.assertEqual(row["balance"],Decimal("-134.12345"))
            self.assertEqual(row["produced"],Decimal("2156.78901"))
            self.assertEqual(row["deficit_ratio"],Decimal("0.35246"))
            self.assertEqual(row["excess_ratio"],0)
            self.assertEqual(row["used"],Decimal("2290.91246"));self.assertNotIn("used",row["reasons"])
            self.assertIn("separate native",row["used_observation"])
            self.assertEqual(row["units"]["balance"],resource+" points")
        self.assertEqual(result["institutions"]["bureaucracy_cost"],Decimal("25"))
        self.assertEqual(result["institutions"]["investment_levels"],Decimal("25"))
        self.assertIsNone(result["institutions"]["police_bureaucracy_cost"])
        self.assertIn("not police-only",result["institutions"]["reasons"]["police_bureaucracy_cost"])
        self.assertFalse(any("WeeklyBalance" in expr for expr,_,_ in self.evaluated))
        self.assertFalse(any("BudgetPanel" in expr for expr,_,_ in self.evaluated))
        self.assert_clean(capacity_diagnostics=True)

    async def test_capacities_used_is_exact_native_fixed_math_under_low_precision(self):
        from decimal import localcontext
        self.capacity_metadata()
        with localcontext() as context:
            context.prec=3
            result=await self.api.capacities()
        self.assertEqual(str(result["bureaucracy"]["used"]),"2290.91246")
        self.assert_clean(capacity_diagnostics=True)

    async def test_capacities_used_preserves_negative_and_signed64_wrap_no_clamp(self):
        self.capacity_metadata()
        for getter,raw in (("GetPowerIncome",100),("GetPowerDelta",1000)):
            self.handlers["Country."+getter+"('bureaucracy')"]=lambda handle,binding,raw=raw:value(data.Kind.FIXED,
                handle=handle,type_id=TYPE_IDS["CFixedPoint"],signed=raw,native_type_name="CFixedPoint")
        result=await self.api.capacities()
        self.assertEqual(result["bureaucracy"]["used"],Decimal("-0.00900"))
        self.assert_clean(capacity_diagnostics=True)

    async def test_capacities_partial_native_error_keeps_other_resources_and_zero(self):
        self.capacity_metadata()
        expr="Country.GetPowerDelta('bureaucracy')"
        self.handlers[expr]=lambda handle,binding:value(data.Kind.FIXED,handle=handle,error=13)
        result=await self.api.capacities()
        self.assertIsNone(result["bureaucracy"]["balance"])
        self.assertIn("native error 13",result["bureaucracy"]["reasons"]["balance"])
        self.assertEqual(result["authority"]["balance"],Decimal("-134.12345"))
        self.assertEqual(result["bureaucracy"]["excess_ratio"],Decimal(0));self.assert_clean(capacity_diagnostics=True)

    async def test_capacities_wrong_signature_not_called_and_no_other_resource_gate(self):
        self.capacity_metadata()
        for row in self.metadata[TYPE_IDS["Country"]]:
            if row.name=="GetPowerIncome":row.argument_count=0
        result=await self.api.capacities()
        self.assertIsNone(result["bureaucracy"]["produced"])
        self.assertIn("1-argument",result["bureaucracy"]["reasons"]["produced"])
        self.assertFalse(any("GetPowerIncome(" in expr for expr,_,_ in self.evaluated))
        self.assertEqual(result["bureaucracy"]["balance"],Decimal("-134.12345"));self.assert_clean(capacity_diagnostics=True)

    async def test_capacities_unknown_query_keeps_original_owner_and_no_replay(self):
        self.capacity_metadata()
        expression="Country.GetPowerDelta('bureaucracy')";self.ctx.fail_expression=expression
        with self.assertRaises(TimeoutError):await self.api.capacities()
        self.assertEqual(sum(expr==expression for expr,_,_ in self.evaluated),1)
        self.assertTrue(self.api._retained_reads)
        self.assertEqual(self.released,[])

    def state_taxation_metadata(self):
        for getter in ("CalcTaxCapacity", "CalcTaxCapacityUsage", "CalcTaxCapacityBalance"):
            self.metadata[TYPE_IDS["State"]].append(function("State", getter, TYPE_IDS["CFixedPoint"]))
        self.tax_raw = {STATE_A: {"CalcTaxCapacity":10012345,"CalcTaxCapacityUsage":12054321,
                                 "CalcTaxCapacityBalance":-2041976},
                        STATE_B: {"CalcTaxCapacity":0,"CalcTaxCapacityUsage":0,"CalcTaxCapacityBalance":0}}
        for getter in ("CalcTaxCapacity", "CalcTaxCapacityUsage", "CalcTaxCapacityBalance"):
            self.handlers["State." + getter] = lambda handle,binding,getter=getter: value(data.Kind.FIXED,
                handle=handle, type_id=TYPE_IDS["CFixedPoint"], signed=self.tax_raw[binding.object][getter],
                native_type_name="CFixedPoint")

    async def test_states_default_rows_and_cost_unchanged_with_available_tax_getters(self):
        self.state_taxation_metadata()
        rows=await self.api.states()
        self.assertTrue(all(set(row)=={"id","name","country_id","reasons"} for row in rows))
        self.assertFalse(any("CalcTaxCapacity" in expr for expr,_,_ in self.evaluated))
        self.assert_clean()

    async def test_states_details_reads_actual_state_tax_capacity_usage_balance_and_zero(self):
        self.state_taxation_metadata()
        rows=await self.api.states(details=True)
        a,b=[row["taxation"] for row in rows]
        self.assertEqual(a["scope"],"state");self.assertEqual(a["capacity"],Decimal("100.12345"))
        self.assertEqual(a["usage"],Decimal("120.54321"));self.assertEqual(a["balance"],Decimal("-20.41976"))
        self.assertTrue(a["insufficient"]);self.assertEqual(b["capacity"],Decimal(0))
        self.assertEqual(b["usage"],Decimal(0));self.assertEqual(b["balance"],Decimal(0));self.assertFalse(b["insufficient"])
        self.assertEqual(a["units"]["capacity"],"taxation capacity points")
        reads=[(expr,binding) for expr,binding,_ in self.evaluated if "CalcTaxCapacity" in expr]
        self.assertEqual(len(reads),6)
        self.assertTrue(all(binding.domain=="State" and binding.object in (STATE_A,STATE_B) for _,binding in reads))
        self.assertFalse(any("Country.GetPower" in expr or "BudgetPanel" in expr for expr,_,_ in self.evaluated))
        self.assert_clean()

    async def test_states_details_missing_usage_keeps_capacity_and_balance_without_false_sufficiency(self):
        self.state_taxation_metadata()
        self.metadata[TYPE_IDS["State"]]=[f for f in self.metadata[TYPE_IDS["State"]] if f.name!="CalcTaxCapacityUsage"]
        a=(await self.api.states(details=True))[0]["taxation"]
        self.assertEqual(a["capacity"],Decimal("100.12345"));self.assertIsNone(a["usage"])
        self.assertEqual(a["balance"],Decimal("-20.41976"));self.assertIsNone(a["insufficient"])
        self.assertIn("missing or ambiguous",a["reasons"]["usage"])
        self.assertFalse(any(expr=="State.CalcTaxCapacityUsage" for expr,_,_ in self.evaluated));self.assert_clean()

    async def test_states_details_known_native_error_is_local_not_country_or_other_state(self):
        self.state_taxation_metadata()
        original=self.handlers["State.CalcTaxCapacity"]
        self.handlers["State.CalcTaxCapacity"]=lambda handle,binding:(value(data.Kind.FIXED,handle=handle,error=13)
            if binding.object==STATE_A else original(handle,binding))
        a,b=[row["taxation"] for row in await self.api.states(details=True)]
        self.assertIsNone(a["capacity"]);self.assertIsNone(a["insufficient"])
        self.assertEqual(a["usage"],Decimal("120.54321"));self.assertIn("native error 13",a["reasons"]["capacity"])
        self.assertEqual(b["capacity"],Decimal(0));self.assertFalse(b["insufficient"]);self.assert_clean()

    async def test_states_details_wrong_scalar_kind_not_coerced_to_capacity(self):
        self.state_taxation_metadata()
        self.handlers["State.CalcTaxCapacityBalance"]=lambda handle,binding:value(data.Kind.I64,
            handle=handle,type_id=TYPE_IDS["CFixedPoint"],signed=100,
            native_type_name="int64")
        a=(await self.api.states(details=True))[0]["taxation"]
        self.assertIsNone(a["balance"]);self.assertIn("native scalar kind",a["reasons"]["balance"])
        self.assertTrue(a["insufficient"]);self.assert_clean()

    async def test_states_details_unknown_original_query_keeps_state_name_owner_without_replay(self):
        self.state_taxation_metadata();self.ctx.fail_expression="State.CalcTaxCapacity"
        with self.assertRaises(TimeoutError):await self.api.states(details=True)
        self.assertEqual(sum(expr=="State.CalcTaxCapacity" for expr,_,_ in self.evaluated),1)
        self.assertEqual(self.released,[]);self.assertTrue(self.api._retained_reads)
        self.assertTrue(self.api._retained_reads[-1].owners)

    async def test_summary_is_concise_typed_and_preserves_full_id_and_exact_quantity(self):
        result = await self.api.summary()
        self.assertEqual(result, {"id": COUNTRY, "name": "大清", "population": 300000001,
            "gdp": Decimal("1234.56789"), "units": {"population": "people", "gdp": "currency"}, "reasons": {}})
        self.assertEqual(self.ctx.operations, ["country.summary"])
        self.assertTrue(self.ctx.refresh_count)
        self.assertEqual([start for type_id, start in self.metadata_calls if type_id == TYPE_IDS["Country"]], [0, 7, 14])
        for _, binding, _ in self.evaluated:
            self.assertEqual(binding, data.ObjectBinding.country(TYPE_IDS["Country"], COUNTRY))
        self.assert_clean()

    async def test_states_auto_page_and_filter_actual_country_owner_without_index_masking(self):
        result = await self.api.states()
        self.assertEqual([row["id"] for row in result], [STATE_A, STATE_B])
        self.assertTrue(all(row["country_id"] == COUNTRY and row["name"] for row in result))
        self.assertEqual(self.object_calls, [(objects.Domain.STATE, 0), (objects.Domain.STATE, 13)])
        self.assertEqual(self.ctx.operations, ["country.states"])
        for _, binding, _ in self.evaluated:
            self.assertEqual(binding.domain, "State")
            self.assertFalse(binding.retained)
        self.assert_clean()

    async def test_buildings_join_domestic_states_and_evaluate_key_inside_one_native_callback(self):
        result = await self.api.buildings()
        self.assertEqual([row["id"] for row in result], [BUILDING_A, BUILDING_B])
        self.assertEqual([row["levels"] for row in result], [3, 0])
        self.assertEqual([row["state_id"] for row in result], [STATE_A, STATE_B])
        self.assertTrue(all(row["key"] == "building_iron_mine" and row["name"] == "铁矿" for row in result))
        self.assertEqual(self.ctx.operations, ["country.buildings"])
        self.assertNotIn("owner", repr(result))
        self.assertNotIn("type_id", repr(result))
        self.assertNotIn("pointer", repr(result))
        keys = [(binding, handle) for expression, binding, handle in self.evaluated if expression == "Building.GetBuildingType.GetKey"]
        self.assertTrue(keys)
        self.assertTrue(all(binding.type_id == TYPE_IDS["Building"] and not binding.retained for binding, _ in keys))
        self.assertNotIn("Building.GetBuildingType", [item[0] for item in self.evaluated])
        self.assertNotIn("BuildingType.GetKey", [item[0] for item in self.evaluated])
        self.assert_clean()

    async def test_building_details_use_original_context_reader_and_keep_native_rich(self):
        calls=[]
        async def tooltip(ctx,building_id):
            self.assertIs(ctx,self.ctx);calls.append(building_id)
            return {"text":"铁矿\n就业 1000", "raw_text":"#BOLD 铁矿#!\n就业 1000",
                    "text_complete":True,"context_supplied":True,"native_temporaries_destroyed":True,
                    "decode_error":None,"world":self.ctx.world,"localization_success":None}
        from pdx_runtime.v3_game import _text as localizer
        with patch.object(localizer, "read_building_tooltip", side_effect=tooltip):
            rows=await self.api.buildings(state=STATE_A,details=True)
        self.assertEqual(calls,[BUILDING_A]);self.assertEqual(rows[0]["tooltip"],"铁矿\n就业 1000")
        self.assertEqual(rows[0]["tooltip_raw"],"#BOLD 铁矿#!\n就业 1000")
        self.assertTrue(rows[0]["tooltip_complete"]);self.assertNotIn("receipts",rows[0]);self.assert_clean()

    async def test_building_details_incomplete_keeps_other_real_values_and_reason(self):
        async def tooltip(ctx,building_id):return {"text":None,"raw_text":None,"text_complete":False,
            "reason":"Current native description unavailable"}
        from pdx_runtime.v3_game import _text as localizer
        with patch.object(localizer, "read_building_tooltip", side_effect=tooltip):
            rows=await self.api.buildings(state=STATE_A,details=True)
        self.assertEqual(rows[0]["levels"],3);self.assertEqual(rows[0]["name"],"铁矿")
        self.assertFalse(rows[0]["tooltip_complete"])
        self.assertEqual(rows[0]["reasons"]["tooltip"],"Current native description unavailable");self.assert_clean()

    async def test_buildings_state_full_id_or_id_row_filters_before_building_name_reads(self):
        for selected in (STATE_A, {"id": STATE_A}):
            with self.subTest(state=selected):
                before = len(self.evaluated)
                result = await self.api.buildings(state=selected)
                self.assertEqual([row["id"] for row in result], [BUILDING_A])
                self.assertEqual(result[0]["state_id"], STATE_A)
                self.assertEqual(set(result[0]), {"id", "name", "key", "state_id", "state_name", "country_id", "levels", "reasons"})
                building_reads = [(expression, binding.object) for expression, binding, _ in self.evaluated[before:]
                                  if expression.startswith("Building.")]
                self.assertEqual(building_reads, [("Building.GetName", BUILDING_A),
                                                 ("Building.GetBuildingType.GetKey", BUILDING_A)])
        self.assert_clean()

    async def test_buildings_observed_state_name_matches_owned_state_and_unknown_or_foreign_is_empty(self):
        self.handlers["State.GetName"] = lambda handle, binding: value(
            data.Kind.STRING, handle=handle, text="四川" if binding.object == STATE_A else "直隶")
        result = await self.api.buildings(state="四川")
        self.assertEqual([row["id"] for row in result], [BUILDING_A])
        self.assertEqual(result[0]["state_name"], "四川")
        building_reads = [(expression, binding.object) for expression, binding, _ in self.evaluated
                          if expression.startswith("Building.")]
        self.assertEqual(building_reads, [("Building.GetName", BUILDING_A),
                                         ("Building.GetBuildingType.GetKey", BUILDING_A)])
        for selected in ("未观测州名", 1, {"id": 1}, STATE_A & 0xFFFFFF):
            with self.subTest(state=selected):
                before = len(self.object_calls)
                before_names = len(self.evaluated)
                self.assertEqual(await self.api.buildings(state=selected), [])
                self.assertTrue(all(domain == objects.Domain.STATE for domain, _ in self.object_calls[before:]))
                self.assertFalse(any(expression.startswith("Building.") for expression, _, _ in self.evaluated[before_names:]))
        self.assert_clean()

    async def test_native_key_parser_rejection_is_reason_not_fabricated_key_or_zero(self):
        self.handlers["Building.GetBuildingType.GetKey"] = lambda handle, binding: value(data.Kind.EMPTY, handle=handle, error=13)
        result = await self.api.buildings()
        self.assertTrue(all(row["key"] is None and "native error 13" in row["reasons"]["key"] for row in result))
        self.assertTrue(all(row["name"] == "铁矿" for row in result))
        self.assert_clean()

    async def test_native_key_chain_is_not_gated_by_redundant_type_metadata(self):
        self.metadata[TYPE_IDS["BuildingType"]] = []
        result = await self.api.buildings()
        self.assertTrue(all(row["key"] == "building_iron_mine" for row in result))
        self.assertIn("Building.GetBuildingType.GetKey", [item[0] for item in self.evaluated])
        self.assertNotIn("BuildingType.GetKey", [item[0] for item in self.evaluated])
        self.assertNotIn("BuildingType", self.ctx.type_names)
        self.assert_clean()

    async def test_missing_or_action_getter_is_none_with_reason_and_no_native_action(self):
        self.metadata[TYPE_IDS["Country"]] = [
            function("Country", "GetName", callbacks=(0, 0, 1, 0)),
            function("Country", "GetGDP", TYPE_IDS["CFixedPoint"])]
        result = await self.api.summary()
        self.assertIsNone(result["name"])
        self.assertIsNone(result["population"])
        self.assertIn("not a zero-argument", result["reasons"]["name"])
        self.assertIn("missing", result["reasons"]["population"])
        self.assertEqual([item[0] for item in self.evaluated], ["Country.GetGDP"])
        self.assert_clean()

    async def test_known_scalar_error_and_wrong_kind_release_owners_and_continue(self):
        self.handlers["Country.GetTotalPopulation"] = lambda handle, binding: value(data.Kind.EMPTY, handle=handle,
            type_id=TYPE_IDS["int64"], error=1168)
        self.handlers["Country.GetGDP"] = lambda handle, binding: value(data.Kind.I64, handle=handle,
            type_id=TYPE_IDS["CFixedPoint"], signed=0)
        result = await self.api.summary()
        self.assertIsNone(result["population"])
        self.assertIsNone(result["gdp"])
        self.assertIn("1168", result["reasons"]["population"])
        self.assertIn("native scalar kind", result["reasons"]["gdp"])
        self.assert_clean()

    async def test_long_name_uses_snapshot_of_original_value_without_getter_replay(self):
        def truncated(handle, binding):
            return replace(value(data.Kind.STRING, handle=handle, text="完", error=data.ERROR_MORE_DATA), total=18)
        self.handlers["Country.GetName"] = truncated
        result = await self.api.summary()
        self.assertEqual(result["name"], "完整原生名称")
        names = [item for item in self.evaluated if item[0] == "Country.GetName"]
        self.assertEqual(len(names), 1)
        self.assertEqual(self.snapshots, [(names[0][2], 18)])
        self.assert_clean()

    async def test_unknown_dependent_query_preserves_upstream_owner_without_release_or_replay(self):
        self.ctx.fail_expression = "Building.GetBuildingType.GetKey"
        with self.assertRaisesRegex(TimeoutError, "original query retained"):
            await self.api.buildings()
        self.assertEqual(len(self.api._retained_reads), 1)
        self.assertTrue(self.released)  # earlier copied State rows were safely released
        self.assertTrue(set(self.released).isdisjoint(self.api._retained_reads[0].owners))
        self.assertTrue(self.ctx.diagnostics)
        pending = self.ctx.queries[-1][1]
        self.assertEqual(pending.read_count, 0)
        self.assertEqual(sum(label == "Building.GetBuildingType.GetKey" for label, _ in self.ctx.queries), 1)
        self.assertTrue(self.api._retained_reads[0].owners)

    async def test_known_release_failure_is_retained_internally_and_not_replayed(self):
        self.release_error = 6
        with self.assertRaisesRegex(RuntimeError, "cleanup: native error 6"):
            await self.api.summary()
        self.assertEqual(len(self.released), 1)
        self.assertTrue(self.ctx.diagnostics)
        self.assertTrue(self.api._retained_reads[0].owners)

    async def test_empty_domestic_states_returns_empty_without_building_or_name_queries(self):
        self.pages[objects.Domain.STATE] = [[]]
        self.assertEqual(await self.api.buildings(), [])
        self.assertEqual(self.object_calls, [(objects.Domain.STATE, 0)])
        self.assertEqual(self.evaluated, [])
        self.assert_clean()

    async def test_object_native_error_is_reported_and_world_change_preserves_observed_rows(self):
        self.object_error = 21
        with self.assertRaisesRegex(RuntimeError, "native error 21"):
            await self.api.states()
        self.assertEqual(self.evaluated, [])
        self.object_error = 0
        self.object_world += 1
        rows = await self.api.states()
        self.assertEqual([row["id"] for row in rows], [STATE_A, STATE_B])
        self.assertTrue(all(row["name"] for row in rows))
        changed = [row for row in self.ctx.diagnostics if row.get("event") == "world_changed"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["world"], self.object_world)
        self.assertEqual(set(self.released), {item[2] for item in self.evaluated})

    async def test_each_name_receiver_prefers_current_no_formatting_getter(self):
        for receiver in ("Country", "State", "Building"):
            self.metadata[TYPE_IDS[receiver]].append(function(receiver, "GetNameNoFormatting"))
            self.handlers[f"{receiver}.GetNameNoFormatting"] = lambda handle, binding: value(
                data.Kind.STRING, handle=handle, text="原生名称")
        summary = await self.api.summary()
        states = await self.api.states()
        buildings = await self.api.buildings()
        self.assertEqual(summary["name"], "原生名称")
        self.assertTrue(all(row["name"] == "原生名称" for row in states + buildings))
        expressions = [item[0] for item in self.evaluated]
        self.assertNotIn("Country.GetName", expressions)
        self.assertNotIn("State.GetName", expressions)
        self.assertNotIn("Building.GetName", expressions)
        self.assert_clean()

    async def test_no_formatting_getter_wrong_signature_is_not_invoked(self):
        self.metadata[TYPE_IDS["Country"]].append(function("Country", "GetNameNoFormatting", arguments=1))
        result = await self.api.summary()
        self.assertEqual(result["name"], "大清")
        self.assertNotIn("Country.GetNameNoFormatting", [item[0] for item in self.evaluated])
        self.assert_clean()

    async def test_known_no_formatting_failure_falls_back_and_rich_name_stays_only_in_diagnostics(self):
        self.metadata[TYPE_IDS["Country"]].append(function("Country", "GetNameNoFormatting"))
        self.handlers["Country.GetNameNoFormatting"] = lambda handle, binding: value(data.Kind.EMPTY, handle=handle, error=13)
        rich = "\x15tooltippable_name \x15tooltip:country,156 \x16CHI156!\x16flag_overlay!大清\x15!\x15!"
        self.handlers["Country.GetName"] = lambda handle, binding: value(data.Kind.STRING, handle=handle, text=rich)
        result = await self.api.summary()
        self.assertEqual(result["name"], "大清")
        self.assertEqual(result["reasons"], {})
        self.assertNotIn(rich, repr(result))
        self.assertEqual(self.ctx.diagnostics[-1]["rich_name"], rich)
        self.assertEqual(self.ctx.diagnostics[-1]["source"], "Country.GetName")
        self.assertEqual(self.ctx.diagnostics[-1]["id"], COUNTRY)
        self.ctx.diagnostics.clear()
        self.assert_clean()

    async def test_state_and_building_rich_names_return_plain_with_original_evidence(self):
        rich = "\x15tooltippable_name \x15tooltip:dw_state,some-value \x16iron_mine!铁矿\x15!\x15!"
        for expression in ("State.GetName", "Building.GetName"):
            self.handlers[expression] = lambda handle, binding: value(data.Kind.STRING, handle=handle, text=rich)
        result = await self.api.buildings()
        self.assertTrue(all(row["name"] == "铁矿" and row["state_name"] == "铁矿" for row in result))
        self.assertTrue(all(row["key"] == "building_iron_mine" for row in result))
        self.assertTrue(all(item["rich_name"] == rich for item in self.ctx.diagnostics))
        self.ctx.diagnostics.clear()
        self.assert_clean()

    async def test_unknown_no_formatting_query_never_falls_back_or_replays(self):
        self.metadata[TYPE_IDS["Country"]].append(function("Country", "GetNameNoFormatting"))
        self.handlers["Country.GetNameNoFormatting"] = lambda handle, binding: value(data.Kind.STRING, handle=handle, text="大清")
        self.ctx.fail_expression = "Country.GetNameNoFormatting"
        with self.assertRaises(TimeoutError):
            await self.api.summary()
        self.assertEqual([item[0] for item in self.evaluated], ["Country.GetNameNoFormatting"])
        self.assertEqual(self.released, [])
        self.assertEqual(len(self.api._retained_reads), 1)

    async def test_real_perform_timeout_resumes_same_coroutine_and_original_query(self):
        from pdx_runtime.v3_game._core import GameContext, PendingOperation
        context = GameContext(SimpleNamespace(native=self.ctx.native), process=self.ctx.process, timeout=0)
        context.world = self.ctx.world
        context.adapters = self.ctx.adapters
        context.refresh = self.ctx.refresh
        context.country_id = self.ctx.country_id
        context.type_id = self.ctx.type_id
        started, proceed = asyncio.Event(), asyncio.Event()
        original_query = None

        async def query(label, factory):
            nonlocal original_query
            submitted = factory()
            self.ctx.queries.append((label, submitted))
            if label == "Country.GetGDP":
                original_query = submitted
                started.set()
                await proceed.wait()
            return await self.ctx.wait(submitted)

        context.query = query
        api = CountryAPI(context)
        pending = await api.summary()
        try:
            self.assertIsInstance(pending, PendingOperation)
            await asyncio.wait_for(started.wait(), 1)
            self.assertIsNotNone(original_query)
            self.assertEqual(original_query.read_count, 0)
            self.assertEqual(self.released, [])  # live operation still owns completed values
            proceed.set()
            result = await pending.resume(timeout=1)
        finally:
            proceed.set()
            if isinstance(pending, PendingOperation):
                await pending.task
        self.assertEqual(result["name"], "大清")
        self.assertEqual(result["gdp"], Decimal("1234.56789"))
        self.assertEqual(original_query.read_count, 1)
        self.assertEqual(sum(expression == "Country.GetGDP" for expression, _, _ in self.evaluated), 1)
        self.assertEqual(set(self.released), {item[2] for item in self.evaluated})
        self.assertEqual(api._retained_reads, [])

    async def test_object_pagination_without_progress_stops_instead_of_looping(self):
        self.object_stuck = True
        with self.assertRaisesRegex(RuntimeError, "pagination did not advance"):
            await self.api.states()
        self.assertEqual(len(self.object_calls), 1)


if __name__ == "__main__":
    unittest.main()
