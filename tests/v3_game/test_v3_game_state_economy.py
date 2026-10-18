"""Offline State economy/society reads through the real shared data helper."""
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as D
from pdx_runtime.v3_game import _read as O
from pdx_runtime.v3_game.economy import states as S

FULL_STATE_ID = 0x8F000123


class Context:
    def __init__(self):
        self.native = object(); self.process = object(); self.adapters = {"data": 7}
        self.world = 0x7000; self.diagnostics = []; self.types = []; self.calls = []
        self.refreshes = 0
    async def refresh(self): self.refreshes += 1
    async def country_id(self): raise AssertionError("State reads must not infer a country")
    async def type_id(self, name): self.types.append(name); return 83
    async def query(self, label, factory):
        self.calls.append(label)
        return factory().result
    def _remember(self, query): pass


def cell(index, field, value=0, *, error=0, total=None, binding_error=0):
    kind = {"fixed": D.Kind.FIXED, "integer": (D.Kind.I32 if
        field.name.startswith("urbanization.") else D.Kind.I64),
        "boolean": D.Kind.BOOL, "text": D.Kind.STRING}[field.kind]
    raw = value.encode("utf-8") if field.kind == "text" else (int(value) & ((1 << 64) - 1)).to_bytes(8, "little")
    bits = 0 if field.kind == "text" else int(value) & ((1 << 64) - 1)
    return D.ProjectionCell(0, index, error, kind, 4, bits, raw,
        len(raw) if total is None else total, binding_error=binding_error)


def page(cells, *, world=0x7000):
    return D.ProjectionPage(0, world, len(S.FIELDS), 0, 17, 1, True, 1, tuple(cells))


class Tests(unittest.IsolatedAsyncioTestCase):
    async def read(self, cells):
        ctx = Context()
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=NS(result=page(cells))) as send, \
             patch.object(D, "enumerate_functions") as metadata:
            answer = await S.read_state_conditions(ctx, FULL_STATE_ID)
        metadata.assert_not_called()
        self.assertEqual(ctx.types, ["State"])
        self.assertEqual(ctx.refreshes, 1)
        self.assertEqual(ctx.calls, ["economy.state_conditions"])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1], tuple(f.expression for f in S.FIELDS))
        state_binding = send.call_args.args[2][0][0]
        self.assertEqual(state_binding.object, FULL_STATE_ID)
        self.assertEqual(state_binding.domain, "State")
        self.assertEqual(state_binding.pack()[4:8], (3).to_bytes(4, "little"))
        self.assertEqual(answer["state_id"], FULL_STATE_ID)
        return answer

    async def test_all_required_fields_full_and_sign_preserving(self):
        vals = {"pollution.generation": -200000, "pollution.level": 400000,
            "pollution.impact": 57000, "pollution.impact_daily_change": -1234,
            "pollution.health_effects_reduction": -12750, "urbanization.amount": 100,
            "urbanization.needed_per_level": 50,
            "urbanization.buildings_max_level_description": "原生上限\x15!",
            "turmoil.amount": 23000, "turmoil.effects_description": "动荡\x15!",
            "turmoil.has_effects": 1, "obstinance.amount": -11000,
            "obstinance.supporters": 2**40 + 21, "obstinance.effect_scale": 90000,
            "obstinance.effects_description": "政治效果\x15!",
            "migration.weekly_immigration": 2**39 + 8,
            "migration.weekly_emigration": 11,
            "migration.weekly_net": -(2**39 + 3),
            "migration.annual_projected_net": -(2**42 + 17),
            "migration.flow_description": "年度预计净迁移原文",
            "migration.flow_and_slave_trade_description": "迁移及奴隶贸易原文",
            "migration.details_description": "明细\x15保持原文!",
            "demographics.birthrate": 2500, "demographics.mortality": 1750,
            "demographics.net_growth": -(2**40 + 51),
            "demographics.total_change": 2**39 + 29,
            "demographics.growth_description": "人口增长说明\x15原文!"}
        answer = await self.read([cell(i, field, vals.get(field.name, 0))
            for i, field in enumerate(S.FIELDS)])
        self.assertEqual(len(S.FIELDS), 27)
        self.assertTrue(answer["complete"])
        self.assertEqual(answer["reasons"], {})
        self.assertTrue(all(answer[name]["complete"] for name in S.GROUPS))
        self.assertEqual(answer["pollution"]["level"], Decimal(4))
        self.assertEqual(answer["pollution"]["impact_daily_change"], Decimal("-0.01234"))
        self.assertEqual(answer["turmoil"]["amount"], Decimal("0.23"))
        self.assertEqual(answer["turmoil"]["has_effects"], True)
        self.assertEqual(answer["obstinance"]["supporters"], 2**40 + 21)
        self.assertEqual(answer["migration"]["weekly_net"], -(2**39 + 3))
        self.assertEqual(answer["demographics"]["birthrate"], Decimal("0.025"))
        self.assertEqual(answer["demographics"]["mortality"], Decimal("0.0175"))
        self.assertEqual(answer["demographics"]["net_growth"], -(2**40 + 51))
        self.assertEqual(answer["demographics"]["total_change"], 2**39 + 29)
        self.assertEqual(answer["demographics"]["growth_description"],
            vals["demographics.growth_description"])
        self.assertEqual(answer["values"]["demographics.net_growth"], -(2**40 + 51))
        self.assertEqual(answer["migration"]["annual_projected_net"], -(2**42 + 17))
        self.assertEqual(answer["migration"]["annual_net"], answer["migration"]["annual_projected_net"])
        self.assertEqual(answer["values"]["migration.annual_net"], answer["values"]["migration.annual_projected_net"])
        self.assertEqual(answer["aliases"]["migration.annual_net"], "migration.annual_projected_net")
        self.assertIn("not a historical", answer["migration"]["semantics"]["annual_projected_net"])
        self.assertEqual(sum(f.expression == "State.GetAnnualPopNetMigration" for f in S.FIELDS), 1)
        self.assertEqual(answer["migration"]["details_description"], vals["migration.details_description"])
        self.assertEqual(answer["migration"]["flow_description"], vals["migration.flow_description"])
        self.assertEqual(answer["migration"]["flow_and_slave_trade_description"], vals["migration.flow_and_slave_trade_description"])
        self.assertEqual(answer["migration"]["sources"]["flow_description"], "State.GetMigrationDesc")
        self.assertEqual(answer["migration"]["sources"]["flow_and_slave_trade_description"], "State.GetMigrationAndSlaveTradeDetailsDesc")
        self.assertNotIn("annual_immigration",answer["migration"])
        self.assertNotIn("annual_emigration",answer["migration"])
        self.assertEqual(answer["migration"]["units"]["weekly_net"], "people/week")
        self.assertEqual(answer["migration"]["units"]["annual_net"], "people/year")
        self.assertEqual(answer["demographics"]["units"]["birthrate"], "ratio/year")
        self.assertEqual(answer["demographics"]["units"]["mortality"], "ratio/year")
        self.assertIn("timeframe unproven", answer["demographics"]["units"]["net_growth"])
        self.assertIn("timeframe unproven", answer["demographics"]["units"]["total_change"])
        self.assertNotIn("ratio", answer["demographics"]["units"]["net_growth"])
        self.assertNotIn("impact", answer["pollution"]["units"])
        self.assertEqual(answer["sources"]["pollution.level"],
            "State.GetStateRegion.GetPollutionLevel")
        self.assertEqual(answer["pollution"]["observations"]["level"]["source"],
            "State.GetStateRegion.GetPollutionLevel")

    async def test_partial_or_failed_native_row_preserved_not_complete(self):
        cells = [cell(i, field, "" if field.kind == "text" else 0)
            for i, field in enumerate(S.FIELDS)]
        idx = next(i for i, field in enumerate(S.FIELDS) if field.name == "migration.weekly_net")
        cells[idx] = cell(idx, S.FIELDS[idx], -(2**37), error=13, binding_error=5)
        text_idx = next(i for i, field in enumerate(S.FIELDS) if field.name == "migration.details_description")
        cells[text_idx] = cell(text_idx, S.FIELDS[text_idx], "原生\x15!", total=100)
        answer = await self.read(cells)
        self.assertFalse(answer["complete"])
        self.assertFalse(answer["migration"]["complete"])
        self.assertTrue(answer["pollution"]["complete"])
        self.assertEqual(answer["migration"]["weekly_net"], -(2**37))
        self.assertEqual(answer["migration"]["details_description"], "原生\x15!")
        self.assertEqual(answer["migration"]["observations"]["weekly_net"]["error"], 13)
        self.assertEqual(answer["migration"]["observations"]["weekly_net"]["binding_error"], 5)
        self.assertIn("native getter error", answer["reasons"]["migration.weekly_net"])
        self.assertIn("incomplete native copy", answer["migration"]["reasons"]["details_description"])

    async def test_demographics_zero_and_mixed_native_errors(self):
        cells = [cell(i, field, "" if field.kind == "text" else 0)
            for i, field in enumerate(S.FIELDS)]
        net_idx = next(i for i, f in enumerate(S.FIELDS)
            if f.name == "demographics.net_growth")
        cells[net_idx] = cell(net_idx, S.FIELDS[net_idx], -(2**41 + 7), error=13)
        growth_idx = next(i for i, f in enumerate(S.FIELDS)
            if f.name == "demographics.growth_description")
        cells[growth_idx] = cell(growth_idx, S.FIELDS[growth_idx], "原生增长\x15!", total=100)
        answer = await self.read(cells)
        demo = answer["demographics"]
        self.assertFalse(answer["complete"])
        self.assertFalse(demo["complete"])
        self.assertTrue(answer["migration"]["complete"])
        self.assertEqual(demo["birthrate"], Decimal(0))
        self.assertEqual(demo["mortality"], Decimal(0))
        self.assertEqual(demo["total_change"], 0)
        self.assertEqual(demo["net_growth"], -(2**41 + 7))
        self.assertEqual(demo["growth_description"], "原生增长\x15!")
        self.assertEqual(demo["observations"]["net_growth"]["error"], 13)
        self.assertIn("native getter error", demo["reasons"]["net_growth"])
        self.assertIn("incomplete native copy", demo["reasons"]["growth_description"])
        self.assertEqual(demo["sources"]["net_growth"], "State.GetPopNetGrowth")
        self.assertEqual(demo["sources"]["total_change"], "State.GetPopTotalChange")
        self.assertNotIn("net_growth_rate", demo)
        self.assertNotIn("annual_net_growth", demo)

    async def test_missing_field_is_not_an_authoritative_zero(self):
        absent = "pollution.level"
        answer = await self.read([cell(i, field, "" if field.kind == "text" else 0)
            for i, field in enumerate(S.FIELDS) if field.name != absent])
        self.assertIsNone(answer["pollution"]["level"])
        self.assertFalse(answer["pollution"]["complete"])
        self.assertFalse(answer["complete"])
        self.assertIn("did not copy", answer["pollution"]["reasons"]["level"])

    async def test_out_of_range_id_rejected_before_native_query(self):
        for invalid in (-1, 2**32, "3", True, 3.0):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    await S.read_state_conditions(Context(), invalid)

    async def test_old_adapter_reads_same_full_state_id_without_catalog(self):
        ctx = Context()
        def native(_native, expression, bindings, **kwargs):
            self.assertEqual(bindings[0].object, FULL_STATE_ID)
            self.assertEqual(bindings[0].domain, "State")
            field = next(f for f in S.FIELDS if f.expression == expression)
            encoded = cell(0, field, "原生说明" if field.kind == "text" else
                -5000000000 if field.name == "migration.annual_projected_net" else 0)
            return NS(result=NS(error=0, kind=encoded.kind, type_id=4,
                raw_bits=encoded.raw_bits, value_handle=0,
                snapshot=encoded.snapshot, total=encoded.total))
        with patch.object(O, "_projection_supported", return_value=False), \
             patch.object(D, "evaluate", side_effect=native) as evaluate, \
             patch.object(D, "enumerate_functions") as metadata:
            answer = await S.read_state_conditions(ctx, FULL_STATE_ID)
        self.assertEqual(evaluate.call_count, len(S.FIELDS))
        metadata.assert_not_called()
        self.assertTrue(answer["complete"])
        self.assertEqual(answer["migration"]["annual_net"], -5000000000)
        self.assertEqual(answer["migration"]["details_description"], "原生说明")
        self.assertEqual(ctx.types, ["State"])

    async def test_world_change_keeps_values_without_claiming_complete(self):
        ctx = Context()
        cells = [cell(i, f, "" if f.kind == "text" else 0)
            for i, f in enumerate(S.FIELDS)]
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=NS(result=page(cells, world=0x9000))):
            answer = await S.read_state_conditions(ctx, FULL_STATE_ID)
        self.assertFalse(answer["complete"])
        self.assertFalse(answer["world_consistent"])
        self.assertFalse(answer["pollution"]["complete"])
        self.assertEqual(answer["pollution"]["generation"], Decimal(0))
        self.assertIn("_world", answer["reasons"])


class ExplicitStateReads(unittest.IsolatedAsyncioTestCase):
    async def observe(self, fields, read, values, *, failed_field=None):
        ctx = Context()
        cells = [cell(i, field, values.get(field.name, "" if field.kind == "text" else 0),
            error=71 if field.name == failed_field else 0)
            for i, field in enumerate(fields)]
        native_page = D.ProjectionPage(0, ctx.world, len(fields), 0, 17, 1, True, 1, tuple(cells))
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=NS(result=native_page)) as send, \
             patch.object(D, "enumerate_functions") as metadata:
            answer = await read(ctx, FULL_STATE_ID)
        metadata.assert_not_called()
        self.assertEqual(ctx.types, ["State"])
        self.assertEqual(ctx.refreshes, 1)
        self.assertEqual(len(ctx.calls), 1)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1], tuple(f.expression for f in fields))
        self.assertEqual(send.call_args.args[2][0][0].object, FULL_STATE_ID)
        self.assertEqual(answer["state_id"], FULL_STATE_ID)
        self.assertNotIn("country_id", answer)
        self.assertEqual(len(S.FIELDS), 27)
        return answer

    async def test_world_market_access_one_page_false_zero_and_original_text(self):
        answer = await self.observe(S.WORLD_MARKET_ACCESS, S.read_state_world_market_access,
            {"is_world_market_hub": False, "world_market_access": 0,
             "access_description": "", "trade_potential": -123456,
             "trade_potential_description": "原生潜力\x15!"})
        self.assertTrue(answer["complete"])
        self.assertIs(answer["is_world_market_hub"], False)
        self.assertEqual(answer["world_market_access"], Decimal(0))
        self.assertEqual(answer["access_description"], "")
        self.assertEqual(answer["trade_potential"], Decimal("-1.23456"))
        self.assertEqual(answer["trade_potential_description"], "原生潜力\x15!")
        self.assertEqual(answer["sources"]["is_world_market_hub"], "State.IsWorldMarketHub")
        self.assertEqual(len(S.WORLD_MARKET_ACCESS), 5)

    async def test_world_access_native_error_keeps_actual_zero(self):
        answer = await self.observe(S.WORLD_MARKET_ACCESS, S.read_state_world_market_access,
            {}, failed_field="world_market_access")
        self.assertFalse(answer["complete"])
        self.assertEqual(answer["world_market_access"], Decimal(0))
        self.assertEqual(answer["observations"]["world_market_access"]["error"], 71)
        self.assertIn("native getter error 71", answer["reasons"]["world_market_access"])

    async def test_migration_context_native_pull_target_tags_and_date_text(self):
        answer = await self.observe(S.MIGRATION_CONTEXT, S.read_state_migration,
            {"migration_pull": 1234567, "migration_pull_description": "原生吸引力",
             "migration_and_slave_trade_description": "原生迁移与奴隶贸易条件",
             "is_mass_migration_target": True, "mass_migration_culture_native_id": 0xF1000021,
             "mass_migration_culture_name": "文化显示名",
             "mass_migration_origin_country_native_id": 7,
             "mass_migration_origin_country_tag": "LONG_NATIVE_KEY",
             "mass_migration_origin_country_name": "来源国显示名",
             "mass_migration_end_date_text": "原生日期文本，不解析",
             "owning_country_mass_migration_attraction": 250000})
        self.assertTrue(answer["complete"])
        self.assertEqual(len(S.MIGRATION_CONTEXT), 11)
        self.assertEqual(answer["migration_pull"], Decimal("12.34567"))
        self.assertTrue(answer["is_mass_migration_target"])
        self.assertEqual(answer["mass_migration_culture_native_id"], 0xF1000021)
        self.assertEqual(answer["mass_migration_origin_country_tag"], "LONG_NATIVE_KEY")
        self.assertEqual(answer["mass_migration_end_date_text"], "原生日期文本，不解析")
        self.assertEqual(answer["owning_country_mass_migration_attraction"], Decimal("2.5"))
        self.assertEqual(answer["sources"]["owning_country_mass_migration_attraction"],
            "State.GetCountry.GetLimitedMassMigrationCountryAttraction")
        self.assertNotIn("culture_key", answer)
        self.assertNotIn("origin_country_full_id", answer)
        self.assertIn("not discovery of all targets", answer["scope"])
        self.assertIn("not an identifier", answer["identity_semantics"]["mass_migration_culture_name"])

    async def test_migration_context_preserves_native_u32_culture_i32_country_bits(self):
        ctx=Context();fields=S.MIGRATION_CONTEXT
        cells=[cell(i,f,"" if f.kind=="text" else 0) for i,f in enumerate(fields)]
        for name,kind,bits in (("mass_migration_culture_native_id",D.Kind.U32,0xF1000021),
                              ("mass_migration_origin_country_native_id",D.Kind.I32,0x8F000007)):
            index=next(i for i,f in enumerate(fields) if f.name==name)
            cells[index]=D.ProjectionCell(0,index,0,kind,4,bits,bits.to_bytes(4,"little"),4)
        native_page=D.ProjectionPage(0,ctx.world,len(fields),0,17,1,True,1,tuple(cells))
        with patch.object(O,"_projection_supported",return_value=True), \
             patch.object(D,"project_page",return_value=NS(result=native_page)):
            answer=await S.read_state_migration(ctx,FULL_STATE_ID)
        self.assertEqual(answer["mass_migration_culture_native_id"],0xF1000021)
        self.assertEqual(answer["mass_migration_origin_country_native_id"],0x8F000007-(1<<32))
        self.assertEqual(answer["observations"]["mass_migration_origin_country_native_id"]["raw_bits"],0x8F000007)
        self.assertEqual(answer["observations"]["mass_migration_culture_native_id"]["kind"],D.Kind.U32)
        self.assertIn("not independently proved",answer["identity_semantics"]["mass_migration_origin_country_native_id"])

    async def test_migration_no_target_empty_native_promotions_do_not_become_zero_ids(self):
        ctx=Context();fields=S.MIGRATION_CONTEXT
        cells=[cell(i,f,"" if f.kind=="text" else 0) for i,f in enumerate(fields)]
        index=next(i for i,f in enumerate(fields) if f.name=="mass_migration_culture_native_id")
        cells[index]=D.ProjectionCell(0,index,0,D.Kind.EMPTY,-1,0,b"",0)
        native_page=D.ProjectionPage(0,ctx.world,len(fields),0,17,1,True,1,tuple(cells))
        with patch.object(O,"_projection_supported",return_value=True), \
             patch.object(D,"project_page",return_value=NS(result=native_page)):
            answer=await S.read_state_migration(ctx,FULL_STATE_ID)
        self.assertIs(answer["is_mass_migration_target"],False)
        self.assertIsNone(answer["mass_migration_culture_native_id"])
        self.assertEqual(answer["migration_pull"],Decimal(0))
        self.assertEqual(answer["mass_migration_end_date_text"],"")
        self.assertEqual(answer["observations"]["mass_migration_culture_native_id"]["error"],0)
        self.assertEqual(answer["observations"]["mass_migration_culture_native_id"]["kind"],D.Kind.EMPTY)
        self.assertFalse(answer["complete"])

    async def test_migration_native_error_and_original_pending_prefix_preserved(self):
        answer=await self.observe(S.MIGRATION_CONTEXT,S.read_state_migration,{},failed_field="migration_pull")
        self.assertFalse(answer["complete"])
        self.assertEqual(answer["migration_pull"],Decimal(0))
        self.assertIn("native getter error 71",answer["reasons"]["migration_pull"])
        ctx=Context();fields=S.MIGRATION_CONTEXT
        partial=D.ProjectionPage(0,ctx.world,len(fields),0,17,1,True,1,(cell(0,fields[0],123456),))
        original=NS(partial_result=partial)
        failure=OSError("original State migration copy pending");failure.query=original
        with patch.object(O,"_projection_supported",return_value=True), \
             patch.object(D,"project_page",side_effect=failure) as sent:
            with self.assertRaises(OSError) as caught:
                await S.read_state_migration(ctx,FULL_STATE_ID)
        self.assertIs(caught.exception,failure);self.assertIs(caught.exception.query,original)
        self.assertEqual(failure.state_observations["values"]["migration_pull"],Decimal("1.23456"))
        self.assertEqual(sent.call_count,1)

    async def test_population_metrics_literacy_native_below_expected_and_signed_counts(self):
        answer = await self.observe(S.POPULATION_METRICS, S.read_state_population_metrics,
            {"population": 2**45 + 3, "literacy": 75000,
             "unemployed_working_adults": 2**35 + 5, "jobseekers": 0,
             "standard_of_living": 1125000, "expected_standard_of_living": 1250000,
             "below_expected_standard_of_living_fraction": 12500,
             "expected_standard_of_living_description": "原生生活水平说明"})
        self.assertTrue(answer["complete"])
        self.assertEqual(answer["population"], 2**45 + 3)
        self.assertEqual(answer["unemployed_working_adults"], 2**35 + 5)
        self.assertEqual(answer["jobseekers"], 0)
        self.assertEqual(answer["literacy"], Decimal("0.75"))
        self.assertEqual(answer["standard_of_living"], Decimal("11.25"))
        self.assertEqual(answer["expected_standard_of_living"], Decimal("12.5"))
        self.assertEqual(answer["below_expected_standard_of_living_fraction"], Decimal("0.125"))
        self.assertEqual(answer["sources"]["below_expected_standard_of_living_fraction"],
            "State.GetPopulationRatioBelowExpectedStandardOfLiving")
        self.assertEqual(len(S.POPULATION_METRICS), 14)

    async def test_population_metrics_failed_getter_does_not_recompute_fraction(self):
        answer = await self.observe(S.POPULATION_METRICS, S.read_state_population_metrics,
            {"below_expected_standard_of_living_fraction": 17500},
            failed_field="below_expected_standard_of_living_fraction")
        self.assertFalse(answer["complete"])
        self.assertEqual(answer["below_expected_standard_of_living_fraction"], Decimal("0.175"))
        self.assertIn("native getter error 71", answer["reasons"]["below_expected_standard_of_living_fraction"])

    async def test_food_security_native_counts_ratios_and_false_famine(self):
        answer = await self.observe(S.FOOD_SECURITY, S.read_state_food_security,
            {"population_total": 2**42 + 3, "starving_population": 2**40 + 1,
             "mild_starving_population": 2**39 + 2, "severe_starving_population": 3,
             "starving_fraction": 12500, "mild_starving_fraction": 10000,
             "severe_starving_fraction": 2500, "has_famine": False,
             "states_with_famine": 0, "famine_description": "原生饥荒说明"})
        self.assertTrue(answer["complete"])
        self.assertEqual(answer["population_total"], 2**42 + 3)
        self.assertEqual(answer["mild_starving_population"], 2**39 + 2)
        self.assertEqual(answer["severe_starving_population"], 3)
        self.assertEqual(answer["starving_fraction"], Decimal("0.125"))
        self.assertEqual(answer["units"]["starving_fraction"], "ratio")
        self.assertIs(answer["has_famine"], False)
        self.assertEqual(answer["states_with_famine"], 0)
        self.assertEqual(answer["famine_description"], "原生饥荒说明")
        self.assertEqual(answer["sources"]["severe_starving_population"],
            "State.GetFoodSecurity.GetNumStarvingPopsSevere")
        self.assertNotIn("pop_food_security", answer)
        self.assertNotIn("pop_ids", answer)
        self.assertEqual(len(S.FOOD_SECURITY), 10)

    async def test_food_security_error_keeps_native_value_not_derived_total(self):
        answer = await self.observe(S.FOOD_SECURITY, S.read_state_food_security,
            {"starving_population": 19, "mild_starving_population": 2,
             "severe_starving_population": 3}, failed_field="starving_population")
        self.assertFalse(answer["complete"])
        self.assertEqual(answer["starving_population"], 19)
        self.assertEqual(answer["observations"]["starving_population"]["raw_bits"], 19)
        self.assertIn("native getter error 71", answer["reasons"]["starving_population"])


class HarvestCopiedRead(unittest.IsolatedAsyncioTestCase):
    def context(self):
        from pdx_runtime.query_executor import Diagnostics
        ctx=Context();ctx.queries=[];ctx.types=[];ctx.remembered=[];ctx.labels=[]
        ctx.diagnostics=Diagnostics()
        async def forbidden():raise AssertionError("no world/player preflight")
        async def type_id(name):ctx.types.append(name);return 83
        async def query(label,factory):ctx.queries.append(label);return factory().result
        async def perform(label,work):ctx.labels.append(label);return await work()
        ctx.refresh=forbidden;ctx.country_id=forbidden;ctx.type_id=type_id;ctx.query=query
        ctx.perform=perform;ctx._remember=lambda q:ctx.remembered.append(q)
        return ctx

    def page(self,start=0,count=1,*,error=0,count_valid=True,failed=None,partial=False):
        fields=S.HARVEST_CONDITIONS;cells=[];rows=min(64,max(0,count-start))
        values={"name":"原生状态", "type_name":"本地化类型非key", "type_description":"类型说明",
            "intensity":125000,"duration_days":30,"remaining_days":-2,
            "remaining_duration_label":"less than","remaining_duration_range":"20–40 days",
            "effects_description":"instance native effects","type_effects_description":"definition native effects",
            "affected_state_native_id":0x1A000234,"affected_state_name":"关联州非input",
            "country_native_id":0x3A000005,"country_tag":"LONG_COUNTRY_TAG"}
        for index in range(start,start+rows):
            for column,field in enumerate(fields):
                value=values[field.name]
                kind={"text":D.Kind.STRING,"fixed":D.Kind.FIXED,"integer":D.Kind.I32}[field.kind]
                bits=0 if field.kind=="text" else value&((1<<64)-1)
                raw=value.encode() if field.kind=="text" else bits.to_bytes(8,"little")
                cells.append(D.ProjectionCell(index,column,93 if field.name==failed else 0,
                    kind,82,bits,raw,len(raw),1,0xFFFFFFFF,None,0))
        if partial:cells=cells[:1]
        return D.ProjectionPage(error,0x7000,len(fields),start,17,count,count_valid,rows,tuple(cells))

    async def test_original_source_all65_rows_fields_no_id_gate_and_native_modes(self):
        from pdx_runtime.v3_game.economy import EconomyAPI
        ctx=self.context()
        def send(native,fields,bindings,**kwargs):
            self.assertEqual(kwargs["source_expression"],"State.GetHarvestConditions")
            self.assertEqual(bindings[0][0].domain,"State")
            self.assertEqual(bindings[0][0].object,FULL_STATE_ID)
            return NS(result=self.page(kwargs["start"],65))
        with patch.object(D,"project_page",side_effect=send) as sent:
            result=await EconomyAPI(ctx).state_harvest(FULL_STATE_ID)
        self.assertEqual(ctx.types,["State"]);self.assertEqual(sent.call_count,2)
        self.assertEqual(ctx.labels,["economy.state_harvest"])
        self.assertEqual(len(result["conditions"]),65);self.assertTrue(result["complete"])
        row=result["conditions"][0]
        self.assertEqual(row["intensity"],Decimal("1.25"))
        self.assertEqual(row["duration_days"],30);self.assertEqual(row["remaining_days"],-2)
        self.assertEqual(row["remaining_duration_range"],"20–40 days")
        self.assertNotEqual(row["affected_state_native_id"],FULL_STATE_ID)
        self.assertEqual(row["affected_state_name"],"关联州非input")
        self.assertEqual(row["country_tag"],"LONG_COUNTRY_TAG")
        self.assertEqual(row["bindings"]["type_name"]["mode"],1)
        self.assertNotIn("type_key",row);self.assertNotIn("instance_id",row)
        self.assertNotIn("modifier_vector",row)

    async def test_instance_type_effects_and_error_bearing_values_remain_distinct(self):
        from pdx_runtime.v3_game.economy import EconomyAPI
        ctx=self.context()
        with patch.object(D,"project_page",return_value=NS(result=self.page(failed="effects_description"))):
            result=await EconomyAPI(ctx).state_harvest(FULL_STATE_ID)
        row=result["conditions"][0]
        self.assertFalse(result["complete"])
        self.assertEqual(row["effects_description"],"instance native effects")
        self.assertEqual(row["type_effects_description"],"definition native effects")
        self.assertEqual(row["observations"]["effects_description"]["error"],93)
        self.assertEqual(row["intensity"],Decimal("1.25"))

    async def test_true_empty_source_not_failed_source_default_count0(self):
        from pdx_runtime.v3_game.economy import EconomyAPI
        for native in (self.page(count=0),self.page(count=0,error=21,count_valid=False)):
            with patch.object(D,"project_page",return_value=NS(result=native)):
                result=await EconomyAPI(self.context()).state_harvest(FULL_STATE_ID)
            self.assertEqual(result["conditions"],[])
            self.assertEqual(result["complete"],native.error==0)
            self.assertEqual(result["source_count"],0 if native.count_valid else None)

    async def test_pending_original_page_keeps_native_prefix_and_same_query(self):
        from pdx_runtime.v3_game.economy import EconomyAPI
        original=NS(partial_result=self.page(partial=True));failure=OSError("harvest copy pending")
        failure.query=original
        with patch.object(D,"project_page",side_effect=failure) as sent:
            with self.assertRaises(OSError) as caught:
                await EconomyAPI(self.context()).state_harvest(FULL_STATE_ID)
        self.assertIs(caught.exception,failure);self.assertIs(caught.exception.query,original)
        self.assertEqual(failure.harvest_observations["conditions"][0]["name"],"原生状态")
        self.assertEqual(sent.call_count,1)

    async def test_cleanup_warning_does_not_erase_copied_fields(self):
        from pdx_runtime.v3_game.economy import EconomyAPI
        ctx=self.context();warning=OSError("harvest cleanup pending")
        original=NS(result=self.page(),cleanup_error=warning)
        with patch.object(D,"project_page",return_value=original):
            result=await EconomyAPI(ctx).state_harvest(FULL_STATE_ID)
        self.assertTrue(result["complete"])
        self.assertEqual(ctx.remembered,[original])
        self.assertIs(result["cleanup_warnings"][0]["cleanup_error"],warning)

    async def test_missing_or_transport_error_is_original_not_native_empty(self):
        from pdx_runtime.v3_game.economy import EconomyAPI
        failure=OSError("actual export missing or transport failed")
        with patch.object(D,"project_page",side_effect=failure) as sent:
            with self.assertRaises(OSError) as caught:
                await EconomyAPI(self.context()).state_harvest(FULL_STATE_ID)
        self.assertIs(caught.exception,failure);self.assertEqual(sent.call_count,1)
        self.assertIsNone(failure.harvest_observations["source_count"])
        self.assertFalse(failure.harvest_observations["complete"])



if __name__ == "__main__": unittest.main()
