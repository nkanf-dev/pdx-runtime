"""Economy read facades over the real shared projection helper, without a game."""
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_data as D
from pdx_runtime.v3_game.economy import EconomyAPI
from pdx_runtime.v3_game import _read as O
from pdx_runtime.v3_game.economy.observations import MINTING, INVESTMENT_POOL


class Context:
    def __init__(self):
        self.native = object(); self.process = object(); self.adapters = {"data": 8}
        self.world = 0x9000; self.diagnostics = []; self.labels = []; self.type_calls = 0
    async def refresh(self): pass
    async def country_id(self): return 0x300009c
    async def type_id(self, name): self.type_calls += 1; return 83
    async def query(self, label, factory): return factory().result
    async def perform(self, label, work): self.labels.append(label); return await work()


def projected(fields, *, error_at=None):
    cells = []
    for i, field in enumerate(fields):
        kind = {"fixed": D.Kind.FIXED, "text": D.Kind.STRING,
            "boolean": D.Kind.BOOL, "integer": D.Kind.I32}[field.kind]
        text = "原生[Country.GetName]\x15!" if field.kind == "text" else None
        raw = text.encode() if text is not None else (0).to_bytes(8, "little")
        cells.append(D.ProjectionCell(0, i, 13 if i == error_at else 0, kind, 82, 0, raw, len(raw)))
    return NS(result=D.ProjectionPage(0, 0x9000, len(fields), 0, 77, 1, True, 1, tuple(cells)))


class Tests(unittest.IsolatedAsyncioTestCase):
    async def read(self, method, fields, **kwargs):
        ctx = Context()
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=projected(fields, **kwargs)) as native, \
             patch.object(D, "enumerate_functions") as metadata:
            answer = await getattr(EconomyAPI(ctx), method)()
        self.assertEqual(native.call_count, 1)
        self.assertEqual(native.call_args.args[1], tuple(field.expression for field in fields))
        self.assertEqual(ctx.type_calls, 1); metadata.assert_not_called()
        self.assertEqual(ctx.labels, ["economy." + method])
        self.assertEqual(answer["country_id"], 0x300009c)
        return answer

    async def test_minting_separates_prediction_from_current_income(self):
        answer = await self.read("minting", MINTING)
        self.assertEqual(answer["predicted_weekly_income"], Decimal(0))
        self.assertEqual(answer["reserve_limit"], Decimal(0))
        self.assertEqual(answer["reserve_ratio"], Decimal(0))
        self.assertNotIn("current_income", answer)
        self.assertEqual(answer["sources"]["predicted_weekly_income"], "Country.PredictMinting")
        self.assertTrue(answer["complete"])

    async def test_investment_pool_keeps_actual_text_and_zero_without_invented_ids(self):
        answer = await self.read("investment_pool", INVESTMENT_POOL)
        self.assertEqual(answer["balance"], Decimal(0)); self.assertEqual(answer["weekly_change"], Decimal(0))
        self.assertEqual(answer["previous_investment_count"], 0)
        self.assertIs(answer["show_next_investment"], False)
        self.assertEqual(answer["next_investment_description"], "原生[Country.GetName]\x15!")
        self.assertNotIn("next_building_id", answer); self.assertNotIn("contributions_amount", answer)
        self.assertTrue(answer["complete"])



class PopEconomy(unittest.IsolatedAsyncioTestCase):
    POP_ID = 0xAF000123

    def context(self):
        ctx = Context(); ctx.adapters["objects"] = 9
        ctx.refreshes = 0; ctx.types = []
        async def refresh(): ctx.refreshes += 1
        async def country_id(): raise AssertionError("explicit Pop read needs no selected country")
        async def type_id(name): ctx.types.append(name); return 87
        ctx.refresh = refresh; ctx.country_id = country_id; ctx.type_id = type_id
        return ctx

    def record(self, **changes):
        from pdx_runtime import v3_objects as objects
        values = dict(requested_id=self.POP_ID, resolved_id=self.POP_ID,
            error=0, slot_index=0x123, pointer=0x1234567890, type_pointer=0xAABBCC,
            total_size=2**45 + 17, workforce=2**40 + 5, dependents=0,
            state_id=0x8F000234, country_id=0xCE000345,
            quantities_error=0, state_error=0, country_error=0, reserved=0,
            state_pointer=0x12345678, country_pointer=0x87654321)
        values.update(changes)
        return objects.PopDetails(**values)

    def scalar_page(self, fields, *, error_field=None):
        cells = []
        for index, field in enumerate(fields):
            value = (2**40 + 5 if field.name == "size.workforce" else
                -123456 if field.name == "wealth_income.income" else
                12500 if field.name == "literacy.rate" else 0)
            kind = {"fixed": D.Kind.FIXED, "text": D.Kind.STRING,
                "boolean": D.Kind.BOOL, "integer": (D.Kind.I64 if
                    field.name.startswith("size.") else D.Kind.I32)}[field.kind]
            raw = b"" if field.kind == "text" else (value & ((1 << 64) - 1)).to_bytes(8, "little")
            cells.append(D.ProjectionCell(0, index, 71 if field.name == error_field else 0,
                kind, 82, value & ((1 << 64) - 1), raw, len(raw)))
        return NS(result=D.ProjectionPage(0, 0x9000, len(fields), 0, 77, 1, True, 1, tuple(cells)))

    async def observe(self, record=None, *, error_field=None, native_error=0):
        from pdx_runtime import v3_objects as objects
        from pdx_runtime.v3_game.economy.observations import POP_ECONOMY
        ctx = self.context(); record = self.record() if record is None else record
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=self.scalar_page(POP_ECONOMY,
                 error_field=error_field)) as scalar, \
             patch.object(objects, "read_pop_details", return_value=NS(result=NS(
                 error=native_error, world=ctx.world, records=(record,)))) as read_record, \
             patch.object(D, "enumerate_functions") as metadata:
            result = await EconomyAPI(ctx).pop_details(self.POP_ID)
        self.assertEqual(ctx.refreshes, 1)
        self.assertEqual(ctx.types, ["Pop"])
        self.assertEqual(ctx.labels, ["economy.pop_details"])
        metadata.assert_not_called()
        self.assertEqual(scalar.call_count, 1)
        self.assertEqual(len(POP_ECONOMY), 32)
        bound = scalar.call_args.args[2][0][0]
        self.assertEqual((bound.domain, bound.object), ("Pop", self.POP_ID))
        self.assertEqual(int.from_bytes(bound.pack()[4:8], "little"), 4)
        read_record.assert_called_once_with(ctx.native, (self.POP_ID,), adapter=9, process=ctx.process)
        return result

    async def test_full_pop_id_counts_false_zero_original_descriptions_and_scalar_types(self):
        answer = await self.observe()
        self.assertTrue(answer["complete"])
        self.assertEqual(answer["pop_id"], self.POP_ID)
        self.assertEqual(answer["state_id"], 0x8F000234)
        self.assertEqual(answer["country_id"], 0xCE000345)
        self.assertEqual(answer["size"]["workforce"], 2**40 + 5)
        self.assertIs(answer["employment"]["is_employed"], False)
        self.assertIs(answer["employment"]["is_slave"], False)
        self.assertEqual(answer["standard_of_living"]["current"], 0)
        self.assertEqual(answer["wealth_income"]["wealth"], 0)
        self.assertEqual(answer["literacy"]["rate"], Decimal("0.125"))
        self.assertEqual(answer["wealth_income"]["income"], Decimal("-1.23456"))
        self.assertEqual(answer["needs"]["description"], "")
        self.assertEqual(answer["food_security"]["raw"], Decimal(0))
        self.assertEqual(answer["native_quantities"]["total"], 2**45 + 17)
        self.assertNotIn("profession_key", answer["identity"])
        self.assertEqual(answer["observations"]["standard_of_living.current"]["kind"], D.Kind.I32)
        self.assertEqual(answer["observations"]["size.workforce"]["kind"], D.Kind.I64)

    async def test_pop_scalar_error_does_not_gate_native_ids_or_actual_value(self):
        answer = await self.observe(error_field="wealth_income.income")
        self.assertFalse(answer["complete"])
        self.assertEqual(answer["wealth_income"]["income"], Decimal("-1.23456"))
        self.assertEqual(answer["native_record"].resolved_id, self.POP_ID)
        self.assertEqual(answer["state_id"], 0x8F000234)
        self.assertIn("native getter error 71", answer["reasons"]["wealth_income.income"])

    async def test_native_relation_errors_do_not_erase_copied_pop_values_or_bits(self):
        answer = await self.observe(record=self.record(state_error=55, state_id=0xFFFFFFFF))
        self.assertFalse(answer["complete"])
        self.assertEqual(answer["state_id"], 0xFFFFFFFF)
        self.assertEqual(answer["native_errors"]["state"], 55)
        self.assertEqual(answer["size"]["workforce"], 2**40 + 5)
        self.assertEqual(answer["wealth_income"]["income"], Decimal("-1.23456"))

    async def test_native_record_pending_keeps_original_query_and_scalar_prefix(self):
        from pdx_runtime import v3_objects as objects
        from pdx_runtime.v3_game.economy.observations import POP_ECONOMY
        ctx = self.context(); failure = OSError("original Pop record pending")
        original = NS(partial_result=None); failure.query = original
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=self.scalar_page(POP_ECONOMY)), \
             patch.object(objects, "read_pop_details", side_effect=failure) as read_record:
            with self.assertRaises(OSError) as caught:
                await EconomyAPI(ctx).pop_details(self.POP_ID)
        self.assertIs(caught.exception, failure)
        self.assertIs(caught.exception.query, original)
        self.assertFalse(failure.pop_observations["complete"])
        self.assertEqual(failure.pop_observations["wealth_income"]["income"], Decimal("-1.23456"))
        self.assertEqual(read_record.call_count, 1)

    async def test_pop_invalid_id_and_wrong_root_fail_before_native(self):
        from pdx_runtime.v3_game.economy.observations import POP_ECONOMY
        ctx = self.context()
        for bad in (-1, 0x100000000, True, "3"):
            with self.assertRaises(ValueError):
                await EconomyAPI(ctx).pop_details(bad)
        with self.assertRaises(ValueError):
            await O.read_pop_fields(ctx, self.POP_ID, MINTING, label="wrong.root")
        for read in (lambda: O.read_fields(ctx, POP_ECONOMY, label="wrong.root"),
                     lambda: O.read_state_fields(ctx, 3, POP_ECONOMY, label="wrong.root")):
            with self.assertRaises(ValueError): await read()
        self.assertEqual(ctx.refreshes, 0)


class PopulationMetrics(unittest.IsolatedAsyncioTestCase):
    async def test_country_metrics_original_fields_one_lookup_and_native_ratios(self):
        from pdx_runtime.v3_game.economy.observations import POPULATION_METRICS
        ctx = Context()
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=projected(POPULATION_METRICS)) as send:
            answer = await EconomyAPI(ctx).population_metrics()
        self.assertTrue(answer["complete"])
        self.assertEqual(answer["country_id"], 0x300009c)
        self.assertEqual(ctx.type_calls, 1)
        self.assertEqual(ctx.labels, ["economy.population_metrics"])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(len(POPULATION_METRICS), 14)
        self.assertEqual(answer["literacy_total"], Decimal(0))
        self.assertEqual(answer["literacy_incorporated"], Decimal(0))
        self.assertEqual(answer["units"]["literacy_total"], "ratio")
        self.assertEqual(answer["sources"]["standard_of_living"], "Country.GetAverageSoLByPopulation")
        self.assertEqual(answer["sources"]["expected_standard_of_living_poor"],
            "Country.GetExpectedStandardOfLivingPoor")
        self.assertNotIn("below_expected_standard_of_living_fraction", answer)

    async def test_country_metrics_native_error_preserves_value(self):
        from pdx_runtime.v3_game.economy.observations import POPULATION_METRICS
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=projected(POPULATION_METRICS, error_at=1)):
            answer = await EconomyAPI(Context()).population_metrics()
        self.assertFalse(answer["complete"])
        self.assertEqual(answer["literacy_total"], Decimal(0))
        self.assertEqual(answer["observations"]["literacy_total"]["error"], 13)


class PopNeedsCopiedRead(unittest.IsolatedAsyncioTestCase):
    def context(self):
        from pdx_runtime.query_executor import Diagnostics
        ctx=Context();ctx.queries=[];ctx.types=[];ctx.remembered=[]
        ctx.diagnostics=Diagnostics()
        async def forbidden():raise AssertionError("no host world/country preflight")
        async def type_id(name):ctx.types.append(name);return 87
        async def query(label,factory):ctx.queries.append(label);return factory().result
        ctx.refresh=forbidden;ctx.country_id=forbidden;ctx.type_id=type_id;ctx.query=query
        ctx._remember=lambda q:ctx.remembered.append(q)
        return ctx

    def page(self, start=0, count=1, *, rows=None, error=0, count_valid=True, failed=None, partial=False):
        from pdx_runtime.v3_game.economy.observations import POP_NEED_GOODS
        rows=min(64,count-start) if rows is None else rows
        cells=[]
        for index in range(start,start+max(0,rows)):
            for column,field in enumerate(POP_NEED_GOODS):
                value=("good_"+str(index) if field.name=="goods_key" else "" if field.kind=="text"
                    else 12500 if field.name=="budget_share" else 0)
                kind=D.Kind.STRING if field.kind=="text" else D.Kind.FIXED
                raw=value.encode() if field.kind=="text" else int(value).to_bytes(8,"little")
                cells.append(D.ProjectionCell(index,column,93 if field.name==failed else 0,kind,82,
                    0 if field.kind=="text" else value,raw,len(raw),1,0xFFFFFFFF,None,0))
        if partial:cells=cells[:1]
        return D.ProjectionPage(error,0,len(POP_NEED_GOODS),start,17,count,count_valid,max(0,rows),tuple(cells))

    async def test_full_source_all70_native_rows_one_pop_type_no_prewarm_or_false_mode1_gate(self):
        ctx=self.context()
        def send(native,fields,bindings,**kwargs):
            self.assertEqual(kwargs["source_expression"],"Pop.GetGoodsConsumption")
            self.assertEqual(bindings[0][0].domain,"Pop")
            self.assertEqual(bindings[0][0].object,0xAF000123)
            return NS(result=self.page(kwargs["start"],70))
        with patch.object(D,"project_page",side_effect=send) as sent, \
             patch.object(D,"evaluate") as evaluate:
            result=await EconomyAPI(ctx).pop_needs(0xAF000123)
        self.assertEqual(ctx.types,["Pop"]);self.assertEqual(sent.call_count,2)
        evaluate.assert_not_called()
        self.assertEqual(len(result["goods"]),70);self.assertTrue(result["complete"])
        self.assertFalse(result["contract_complete"])
        self.assertEqual(result["goods"][-1]["goods_key"],"good_69")
        self.assertEqual(result["goods"][0]["budget_share"],Decimal("0.125"))
        self.assertEqual(result["goods"][0]["bindings"]["goods_key"]["mode"],1)
        self.assertEqual(result["world"],0)
        self.assertNotIn("native_cost",result["goods"][0])

    async def test_top_is_separate_original_source_not_python_truncation_or_sort(self):
        ctx=self.context()
        with patch.object(D,"project_page",return_value=NS(result=self.page(count=2))) as sent:
            result=await EconomyAPI(ctx).pop_needs(0xAF000123,top=True)
        self.assertEqual(sent.call_args.kwargs["source_expression"],"Pop.GetTopGoodsConsumption")
        self.assertEqual(result["source_count"],2)
        self.assertEqual([r["goods_key"] for r in result["goods"]],["good_0","good_1"])

    async def test_optional_price_error_does_not_erase_key_zero_amount_budget_or_empty_text(self):
        ctx=self.context()
        with patch.object(D,"project_page",return_value=NS(result=self.page(failed="state_price"))):
            result=await EconomyAPI(ctx).pop_needs(0xAF000123)
        row=result["goods"][0]
        self.assertEqual(row["goods_key"],"good_0")
        self.assertEqual(row["consumption_amount"],Decimal(0))
        self.assertEqual(row["budget_share"],Decimal("0.125"))
        self.assertEqual(row["state_shortage_description"],"")
        self.assertEqual(row["state_price"],Decimal(0))
        self.assertEqual(row["observations"]["state_price"]["error"],93)
        self.assertFalse(result["complete"])

    async def test_zero_source_and_failed_source_do_not_share_empty_success(self):
        for native in (self.page(count=0),self.page(count=0,error=21,count_valid=False)):
            ctx=self.context()
            with patch.object(D,"project_page",return_value=NS(result=native)):
                result=await EconomyAPI(ctx).pop_needs(0xAF000123)
            self.assertEqual(result["goods"],[])
            self.assertEqual(result["complete"],native.error==0)
            self.assertEqual(result["source_count"],0 if native.count_valid else None)

    async def test_second_page_pending_preserves_original_query_and_copied_prefix_no_replay(self):
        ctx=self.context();original=NS(partial_result=self.page(64,70,partial=True))
        failure=OSError("original Pop page copy pending");failure.query=original
        with patch.object(D,"project_page",side_effect=(NS(result=self.page(0,70)),failure)) as sent:
            with self.assertRaises(OSError) as caught:
                await EconomyAPI(ctx).pop_needs(0xAF000123)
        self.assertIs(caught.exception,failure);self.assertIs(caught.exception.query,original)
        copied=failure.pop_needs_observations
        self.assertEqual(len(copied["goods"]),65)
        self.assertEqual(copied["goods"][-1]["goods_key"],"good_64")
        self.assertEqual(sent.call_count,2)

    async def test_cleanup_warning_keeps_primary_and_same_original_query(self):
        ctx=self.context();cleanup=OSError("copied allocation cleanup pending")
        native=self.page();query=NS(result=native,cleanup_error=cleanup)
        with patch.object(D,"project_page",return_value=query):
            result=await EconomyAPI(ctx).pop_needs(0xAF000123)
        self.assertTrue(result["complete"])
        self.assertEqual(ctx.remembered,[query])
        self.assertIs(result["cleanup_warnings"][0]["cleanup_error"],cleanup)
        self.assertIs(result["cleanup_warnings"][0]["query"],query)

    async def test_missing_page_or_transport_remains_original_error_without_fallback(self):
        for failure in (OSError("resolve exact Page export missing"),OSError("transport unavailable")):
            ctx=self.context()
            with patch.object(D,"project_page",side_effect=failure) as sent, \
                 patch.object(D,"evaluate") as fallback:
                with self.assertRaises(OSError) as caught:
                    await EconomyAPI(ctx).pop_needs(0xAF000123)
            self.assertIs(caught.exception,failure);self.assertEqual(sent.call_count,1)
            fallback.assert_not_called()
            self.assertIsNone(failure.pop_needs_observations["source_count"])
            self.assertFalse(failure.pop_needs_observations["complete"])



class PopCultureCopiedRead(unittest.IsolatedAsyncioTestCase):
    def context(self):
        from pdx_runtime.query_executor import Diagnostics
        ctx=Context();ctx.diagnostics=Diagnostics();ctx.queries=[];ctx.types=[];ctx.country_calls=0
        async def type_id(name):ctx.types.append(name);return 87
        async def query(label,factory):ctx.queries.append(label);return factory().result
        async def forbidden_country():
            ctx.country_calls+=1
            raise AssertionError("no selected Country lookup for explicit Pop")
        ctx.type_id=type_id;ctx.query=query;ctx.country_id=forbidden_country
        ctx._remember=lambda query:None
        return ctx

    def page(self,ctx,*,failed=None,wrong_kind=False,partial=False,world=None):
        from pdx_runtime.v3_game.economy.observations import POP_CULTURE
        kinds={"culture.native_id":D.Kind.U32,"assimilation.target_native_culture_id":D.Kind.U32,
            "assimilation.individual_count":D.Kind.I64,"conversion.individual_count":D.Kind.I64}
        special={"culture.native_id":0xAF001234,"assimilation.target_native_culture_id":0,
            "assimilation.individual_count":1<<40,"conversion.individual_count":-(1<<34),
            "acceptance.status_ordinal":-1,"acceptance.status_lower_bound":-3,
            "religion.name":"equal display","conversion.target_religion_name":"equal display"}
        cells=[]
        for index,field in enumerate(POP_CULTURE):
            kind=kinds.get(field.name,D.Kind.STRING if field.kind=="text" else D.Kind.BOOL if field.kind=="boolean" else D.Kind.I32)
            value=special.get(field.name,"" if field.kind=="text" else 0)
            bits=0 if field.kind=="text" else value&((1<<64)-1)
            raw=value.encode() if field.kind=="text" else b""
            if wrong_kind and field.name=="acceptance.value":kind=D.Kind.FIXED;bits=125000
            cells.append(D.ProjectionCell(0,index,1168 if failed==field.name else 0,kind,82,bits,raw,len(raw)))
        if partial:cells=cells[:2]
        return D.ProjectionPage(0,ctx.world if world is None else world,len(POP_CULTURE),0,17,1,True,1,tuple(cells))

    async def read(self,ctx,page):
        from pdx_runtime.v3_game import _read as common
        with patch.object(common,"_projection_supported",new=AsyncMock(return_value=True)), \
             patch.object(D,"project_page",return_value=NS(result=page)) as sent:
            result=await EconomyAPI(ctx).pop_culture(0xAF000123)
        return result,sent

    async def test_exact30_single_fullid_page_no_selected_country_lookup_or_fake_identity(self):
        from pdx_runtime.v3_game.economy.observations import POP_CULTURE,POP_ECONOMY
        ctx=self.context();result,sent=await self.read(ctx,self.page(ctx))
        self.assertEqual(ctx.labels,["economy.pop_culture"]);self.assertEqual(ctx.types,["Pop"])
        self.assertEqual(ctx.country_calls,0);sent.assert_called_once()
        self.assertEqual(sent.call_args.args[1],tuple(field.expression for field in POP_CULTURE))
        self.assertEqual(sent.call_args.args[2][0][0],D.ObjectBinding.pop(87,0xAF000123))
        self.assertEqual((len(POP_CULTURE),len(POP_ECONOMY)),(30,32))
        self.assertTrue(result["complete"]);self.assertFalse(result["contract_complete"])
        self.assertEqual(result["culture"]["native_id"],0xAF001234)
        self.assertEqual(result["observations"]["culture.native_id"]["kind"],D.Kind.U32)
        self.assertEqual(result["assimilation"]["target_native_culture_id"],0)
        self.assertEqual(result["assimilation"]["individual_count"],1<<40)
        self.assertEqual(result["conversion"]["individual_count"],-(1<<34))
        self.assertEqual(result["acceptance"]["status_ordinal"],-1)
        self.assertEqual(result["acceptance"]["value"],0)
        self.assertIs(result["assimilation"]["is_assimilating"],False)
        self.assertEqual(result["religion"]["name"],result["conversion"]["target_religion_name"])
        self.assertNotIn("id",result["religion"]);self.assertNotIn("key",result["religion"])
        self.assertNotIn("status_id",result["acceptance"])
        self.assertNotIn("rate",result["conversion"])
        self.assertIn("Pop.GetReligion.GetTaboosDesc",sent.call_args.args[1])
        self.assertNotIn("Pop.GetCulture.GetTaboosDesc",sent.call_args.args[1])

    async def test_native_target_error_does_not_gate_false_flags_counts_or_other_target_data(self):
        ctx=self.context();result,_=await self.read(ctx,self.page(ctx,failed="conversion.target_religion_name"))
        self.assertFalse(result["complete"])
        self.assertEqual(result["conversion"]["target_religion_name"],"equal display")
        self.assertEqual(result["observations"]["conversion.target_religion_name"]["error"],1168)
        self.assertIs(result["conversion"]["is_converting"],False)
        self.assertEqual(result["conversion"]["individual_count"],-(1<<34))
        self.assertEqual(result["culture"]["native_id"],0xAF001234)

    async def test_wrong_live_kind_world_change_preserves_native_bits_without_scalar_coercion(self):
        ctx=self.context();result,_=await self.read(ctx,self.page(ctx,wrong_kind=True,world=0))
        self.assertFalse(result["complete"]);self.assertFalse(result["world_consistent"])
        self.assertIsNone(result["acceptance"]["value"])
        observed=result["observations"]["acceptance.value"]
        self.assertEqual(observed["raw_bits"],125000)
        self.assertEqual(observed["kind"],D.Kind.FIXED)
        self.assertEqual(observed["native_value"],D.FixedPoint(125000))
        self.assertEqual(result["culture"]["native_id"],0xAF001234)

    async def test_original_pending_query_prefix_decorated_not_replayed_or_replaced(self):
        from pdx_runtime.v3_game import _read as common
        ctx=self.context();original=NS(partial_result=self.page(ctx,partial=True))
        failure=OSError("same copied Pop allocation pending");failure.query=original
        with patch.object(common,"_projection_supported",new=AsyncMock(return_value=True)), \
             patch.object(D,"project_page",side_effect=failure) as sent:
            with self.assertRaises(OSError) as caught:await EconomyAPI(ctx).pop_culture(0xAF000123)
        self.assertIs(caught.exception,failure);self.assertIs(caught.exception.query,original)
        result=failure.pop_observations
        self.assertEqual(result["culture"]["native_id"],0xAF001234)
        self.assertIsNone(result["conversion"]["individual_count"])
        self.assertFalse(result["contract_complete"]);self.assertFalse(result["complete"])
        sent.assert_called_once()



class PopAcceptanceCopiedRead(unittest.IsolatedAsyncioTestCase):
    """CPU forwarding/decoder/owner fixtures; do not exercise the native compiler."""
    POP_ID = 0xAF000123
    EXPRESSIONS = (
        "Pop.GetCulture.GetAcceptanceIn(Pop.GetCountry.Self)",
        "Pop.GetCulture.GetAcceptanceDescIn(Pop.GetCountry.Self)",
        "Pop.GetReligion.GetAcceptanceIn(Pop.GetCountry.Self)",
        "Pop.GetReligion.GetAcceptanceDescIn(Pop.GetCountry.Self)",
        "Pop.GetAcceptanceStatus.GetEffectsDescIn(Pop.GetCountry.Self)",
    )

    def context(self):
        ctx = PopCultureCopiedRead().context(); ctx.refreshes = 0
        async def refresh(): ctx.refreshes += 1
        ctx.refresh = refresh
        return ctx

    def rows(self):
        values = (-7, b"", 0, "真实说明".encode(), "原生\x00尾[Country.GetName]\x15!".encode())
        rows = []
        for index, value in enumerate(values):
            numeric = index in (0, 2)
            raw = value.to_bytes(4, "little", signed=True) if numeric else value
            rows.append(NS(error=0, kind=D.Kind.I32 if numeric else D.Kind.STRING,
                type_id=-1, raw_bits=value & 0xffffffff if numeric else 0,
                value_handle=101 + index, snapshot=raw, total=len(raw)))
        return rows

    async def read(self, ctx, rows, *, snapshots=(), release_error=0,
                   release_failure=None, on_evaluate=None):
        evaluated = iter(rows)
        def evaluate(*args, **kwargs):
            index = self.evaluated.call_count - 1
            if on_evaluate is not None: on_evaluate(index)
            result = next(evaluated)
            if isinstance(result, BaseException): raise result
            return NS(result=result)
        def release(*args, **kwargs):
            if release_failure is not None: raise release_failure
            return NS(result=NS(error=release_error, value_handle=0))
        snapshot_returns = [item if isinstance(item, BaseException) else NS(result=item)
            for item in snapshots]
        with patch.object(O, "_projection_supported", new=AsyncMock(
                side_effect=AssertionError("ordinary expressions must not probe Page support"))) as support, \
             patch.object(D, "project_page") as page, \
             patch.object(D, "enumerate_functions") as metadata, \
             patch.object(D, "evaluate", side_effect=evaluate) as evaluated_mock, \
             patch.object(D, "snapshot", side_effect=snapshot_returns) as snapshotted, \
             patch.object(D, "release", side_effect=release) as released:
            self.evaluated = evaluated_mock; self.snapshotted = snapshotted
            self.released = released; self.support = support
            self.page = page; self.metadata = metadata
            return await EconomyAPI(ctx).pop_acceptance(self.POP_ID)

    async def test_exact_five_ordinary_calls_full_pop_no_page_selected_country_or_rescale(self):
        from pdx_runtime.v3_game.economy.observations import POP_ACCEPTANCE, POP_CULTURE
        ctx = self.context(); rows = self.rows(); answer = await self.read(ctx, rows)
        self.assertEqual(ctx.labels, ["economy.pop_acceptance"])
        self.assertEqual(ctx.types, ["Pop"]); self.assertEqual(ctx.refreshes, 1)
        self.assertEqual(ctx.country_calls, 0); self.assertEqual(len(POP_CULTURE), 30)
        self.assertEqual(tuple(field.expression for field in POP_ACCEPTANCE), self.EXPRESSIONS)
        self.assertEqual(tuple(call.args[1] for call in self.evaluated.call_args_list), self.EXPRESSIONS)
        for call in self.evaluated.call_args_list:
            self.assertEqual(call.args[2], (D.ObjectBinding.pop(87, self.POP_ID),))
            self.assertEqual(call.kwargs, {"output_bytes": 4096, "adapter": 8, "process": ctx.process})
        self.support.assert_not_called(); self.page.assert_not_called(); self.metadata.assert_not_called()
        self.snapshotted.assert_not_called()
        self.assertEqual([call.args[1] for call in self.released.call_args_list], [105, 104, 103, 102, 101])
        self.assertEqual(answer["culture"], {"value": -7, "description": ""})
        self.assertEqual(answer["religion"]["value"], 0)
        self.assertEqual(answer["status"]["effects_description"], rows[4].snapshot.decode())
        observed = answer["observations"]["culture.value"]
        self.assertEqual((observed["kind"], observed["raw_bits"], observed["snapshot"]),
            (D.Kind.I32, 0xfffffff9, b"\xf9\xff\xff\xff"))
        self.assertEqual(answer["units"], {}); self.assertEqual(answer["pages"], [])
        self.assertTrue(answer["complete"]); self.assertFalse(answer["contract_complete"])
        self.assertEqual(answer["argument_source"], "Pop.GetCountry.Self")
        self.assertNotIn("country_id", answer); self.assertNotIn("id", answer["status"])

    async def test_native_error_wrong_kind_invalid_utf8_and_empty_kind_remain_observations(self):
        ctx = self.context(); rows = self.rows(); rows[0].error = 1168
        rows[2].kind = D.Kind.BOOL; rows[2].raw_bits = 0; rows[2].snapshot = b"\x00"; rows[2].total = 1
        rows[3].snapshot = b"a\xff\x00"; rows[3].total = 3
        rows[4].kind = D.Kind.EMPTY; rows[4].snapshot = b""; rows[4].total = 0
        answer = await self.read(ctx, rows)
        self.assertEqual(answer["culture"]["value"], -7)
        self.assertEqual(answer["observations"]["culture.value"]["error"], 1168)
        self.assertIsNone(answer["religion"]["value"])
        self.assertIs(answer["observations"]["religion.value"]["native_value"], False)
        self.assertEqual(answer["observations"]["religion.value"]["kind"], D.Kind.BOOL)
        self.assertEqual(answer["religion"]["description"], "a\ufffd\x00")
        self.assertEqual(answer["observations"]["religion.description"]["snapshot"], b"a\xff\x00")
        self.assertIn("invalid UTF-8", answer["reasons"]["religion.description"])
        self.assertIsNone(answer["status"]["effects_description"])
        self.assertEqual(answer["observations"]["status.effects_description"]["kind"], D.Kind.EMPTY)
        self.assertFalse(answer["complete"]); self.assertEqual(self.evaluated.call_count, 5)

    async def test_snapshot_uses_original_any_without_getter_replay_or_shorter_prefix_loss(self):
        for advances in (True, False):
            with self.subTest(advances=advances):
                ctx = self.context(); rows = self.rows(); full = b"prefix\x00full"
                rows[1].error = D.ERROR_MORE_DATA; rows[1].snapshot = b"prefix\x00"; rows[1].total = len(full)
                copied = NS(error=0 if advances else 13, kind=D.Kind.STRING, type_id=-1,
                    raw_bits=0, value_handle=102, snapshot=full if advances else b"x", total=len(full))
                answer = await self.read(ctx, rows, snapshots=(copied,))
                self.assertEqual(self.evaluated.call_count, 5); self.snapshotted.assert_called_once()
                self.assertEqual(self.snapshotted.call_args.args[1], 102)
                self.assertEqual(self.snapshotted.call_args.kwargs["output_bytes"], len(full))
                self.assertEqual(answer["culture"]["description"], (full if advances else b"prefix\x00").decode())
                self.assertEqual(answer["complete"], advances)
                if not advances:
                    self.assertEqual(answer["observations"]["culture.description"]["copies"][0]["error"], 13)
                self.assertEqual([call.args[1] for call in self.released.call_args_list].count(102), 1)

    async def test_original_pending_snapshot_keeps_query_prefix_and_original_read_owners(self):
        ctx = self.context(); rows = self.rows()
        rows[1].error = D.ERROR_MORE_DATA; rows[1].snapshot = b"prefix\x00"; rows[1].total = 40
        original_query = NS(original="same pending snapshot")
        failure = OSError("original snapshot still pending"); failure.query = original_query
        with self.assertRaises(OSError) as caught:
            await self.read(ctx, rows, snapshots=(failure,))
        self.assertIs(caught.exception, failure); self.assertIs(failure.query, original_query)
        answer = failure.pop_observations
        self.assertEqual(answer["culture"], {"value": -7, "description": "prefix\x00"})
        self.assertIsNone(answer["religion"]["value"])
        self.assertFalse(answer["complete"]); self.assertFalse(answer["contract_complete"])
        self.assertEqual(self.evaluated.call_count, 2); self.snapshotted.assert_called_once()
        self.released.assert_not_called()
        retained = ctx._country_observation_reads[0]
        self.assertIsInstance(retained, O._Read); self.assertTrue(retained.uncertain)
        self.assertEqual(retained.owners, [101, 102])

    async def test_failed_release_does_not_erase_complete_primary_or_replace_original_read(self):
        ctx = self.context(); answer = await self.read(ctx, self.rows(), release_error=5)
        self.assertTrue(answer["complete"]); self.assertEqual(answer["culture"]["value"], -7)
        self.assertFalse(answer["contract_complete"]); self.assertEqual(len(answer["cleanup_warnings"]), 1)
        self.assertIn("native error 5", answer["cleanup_warnings"][0])
        self.released.assert_called_once()
        retained = ctx._country_observation_reads[0]
        self.assertIsInstance(retained, O._Read); self.assertTrue(retained.uncertain)
        self.assertEqual(retained.owners, [101, 102, 103, 104, 105])

    async def test_cleanup_cancellation_propagates_same_error_with_settled_primary(self):
        from asyncio import CancelledError
        ctx = self.context(); original_query = NS(original="same pending value release")
        failure = CancelledError("original release cancellation"); failure.query = original_query
        with self.assertRaises(CancelledError) as caught:
            await self.read(ctx, self.rows(), release_failure=failure)
        self.assertIs(caught.exception, failure); self.assertIs(failure.query, original_query)
        answer = failure.pop_observations
        self.assertTrue(answer["complete"]); self.assertEqual(answer["culture"]["value"], -7)
        self.assertFalse(answer["contract_complete"])
        self.assertEqual(self.evaluated.call_count, 5); self.released.assert_called_once()
        self.assertTrue(ctx._country_observation_reads[0].uncertain)

    async def test_world_change_keeps_all_copied_values_without_extra_query_or_atomic_claim(self):
        ctx = self.context()
        def change_world(index):
            if index == 2: ctx.world = 0
        answer = await self.read(ctx, self.rows(), on_evaluate=change_world)
        self.assertEqual(answer["culture"]["value"], -7); self.assertEqual(answer["religion"]["value"], 0)
        self.assertFalse(answer["world_consistent"]); self.assertFalse(answer["complete"])
        self.assertIn("_world", answer["reasons"])
        self.assertEqual(ctx.refreshes, 1); self.assertEqual(self.evaluated.call_count, 5)
        self.assertEqual(answer["snapshot"], "separate native observations")

    async def test_invalid_full_id_and_legacy_field_route_reject_before_native_operations(self):
        from pdx_runtime.v3_game.economy.observations import POP_ACCEPTANCE
        ctx = self.context()
        with patch.object(D, "evaluate") as sent:
            for invalid in (-1, 0x100000000, True, "3"):
                with self.assertRaises(ValueError): await EconomyAPI(ctx).pop_acceptance(invalid)
            with self.assertRaises(TypeError):
                await O.read_pop_fields(ctx, self.POP_ID, POP_ACCEPTANCE, label="wrong_route")
            with self.assertRaises(ValueError):
                O.Field("argument", self.EXPRESSIONS[0], "integer")
            sent.assert_not_called()
        self.assertEqual(ctx.types, []); self.assertEqual(ctx.queries, [])
        self.assertEqual(ctx.refreshes, 0); self.assertEqual(ctx.country_calls, 0)


if __name__ == "__main__": unittest.main()
