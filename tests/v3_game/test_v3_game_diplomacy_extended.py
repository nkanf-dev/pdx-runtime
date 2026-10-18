"""Diplomacy extension routing and copied-page CPU contracts; no native game."""
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.v3_game.diplomacy import relations as extension
from tests.v3_game.test_v3_game_political_data import cell


class Context:
    def __init__(self,values):
        self.values=values;self._types={"Country":5};self.world=1
        self.adapters={"data":"data.dll","console":"console.dll"};self.native=object();self.process=object()
        self.calls=[];self.diagnostics=[];self.performs=[]
    async def country_id(self):return 0
    async def type_id(self,name):raise AssertionError("cachedCountrytype expected")
    async def perform(self,label,fn):self.performs.append(label);return await fn()
    async def query(self,label,factory):
        q=factory();self.calls.append(q)
        values=[self.values[expr] for expr in q.fields]
        cells=tuple(cell(value,j) for j,value in enumerate(values))
        return data.ProjectionPage(0,1,len(values),0,1,1,True,1,cells)


def factory(client,fields,binding_rows,**kwargs):
    return NS(fields=tuple(fields),bindings=binding_rows,options=kwargs,cleanup_error=None)


class DiplomacyExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ranking_real_page_exact_zero(self):
        values={"Country.GetID":0,"Country.GetTagName":"ABC","Country.GetName":"#bold Name#!",
            "Country.GetPrestige":0,"Country.GetPrestigeDesc":"native prestige",
            "Country.GetRank":"Minor Power","Country.GetRankNumber":10,
            "Country.GetRankProgress":0,"Country.GetRankDesc":"native rank",
            "Country.GetPrestigeForCurrentRank":2,"Country.GetPrestigeForNextRank":3,
            "Country.GetPrestigeToMaintainRank":1,"Country.GetDaysUntilRankLoss":0,
            "Country.GetNextRank":"Major Power","Country.GetHighestPossibleRank":"Great Power"}
        ctx=Context(values)
        with patch.object(data,"project_page",side_effect=factory):
            result=await DiplomacyAPI(ctx).ranking(0)
        self.assertEqual(result["id"],0);self.assertEqual(result["prestige"],0)
        self.assertEqual(result["name"],"Name");self.assertEqual(result["name_raw"],"#bold Name#!")
        self.assertEqual(len(ctx.calls),1);self.assertEqual(len(ctx.performs),1)
        # Fixture supplied int0 for nativeFLOAT and real mismatch remains visible.
        self.assertIn("rank_progress",result["reasons"]["fields"])

    async def test_effective_foreign_rights_use_actual_player_context(self):
        values={"Country.GetID":7,"Country.GetTagName":"ZZZ","Country.GetName":"Z",
            "GetPlayer.GetID":0,"GetPlayer.HasForeignInvestmentRights(Country.Self)":False,
            "Country.HasForeignInvestmentRights(GetPlayer)":True}
        ctx=Context(values)
        with patch.object(data,"project_page",side_effect=factory):
            result=await DiplomacyAPI(ctx).foreign_investment_rights(7)
        self.assertFalse(result["player_can_invest"]);self.assertTrue(result["target_can_invest"])
        self.assertEqual(result["source_country_id"],0)
        self.assertEqual(len(ctx.calls[0].bindings[0]),1) # no conflicting Country alias
        self.assertTrue(result["complete"])

    async def test_liberty_desire_signed_fixed_units_and_native_text(self):
        values={"Country.GetID":0,"Country.GetTagName":"ABC","Country.GetName":"A",
            "Country.IsSubject":True,"Country.GetLibertyDesireRaw":data.FixedPoint(0),
            "Country.GetLibertyDesireLevelLabel":"loyal","Country.GetWeeklyLibertyDesireChange":data.FixedPoint((-2500)&((1<<64)-1)),
            "Country.GetWeeklyLibertyDesireChangeDesc":"-0.025 original",
            "Country.GetWeeklyLibertyDesireChangeFromSupportIndependence":data.FixedPoint(0),
            "Country.GetWeeklyLibertyDesireChangeFromSupportIndependenceDesc":"none"}
        ctx=Context(values)
        with patch.object(data,"project_page",side_effect=factory):
            result=await DiplomacyAPI(ctx).liberty_desire(0)
        self.assertEqual(result["liberty_desire"],Decimal("0"))
        self.assertEqual(result["weekly_change"],Decimal("-0.025"));self.assertTrue(result["complete"])


    async def test_leverage_uses_exact_gui_parameter_directions(self):
        with patch.object(extension,"country_source",AsyncMock(return_value={"rows":[]})) as read:
            await DiplomacyAPI(NS()).power_bloc_leverage(7)
        fields={f.name:f.expression for f in read.call_args.args[3]}
        self.assertEqual(fields["current"],"PowerBloc.GetIndividualLeverage(Country.Self)")
        self.assertEqual(fields["target"],"Country.GetLeverageTarget(PowerBloc.Self)")
        self.assertEqual(fields["weekly_gain"],"Country.GetWeeklyLeverageGain(PowerBloc.Self)")

    async def test_attitude_source_country_is_explicit(self):
        with patch.object(extension,"country_snapshot",AsyncMock(return_value={})) as read:
            await DiplomacyAPI(NS()).attitude(7)
        fields={f.name:f.expression for f in read.call_args.args[2]}
        self.assertEqual(fields["source_country_id"],"GetPlayer.GetID")
        self.assertEqual(fields["attitude"],"Country.GetAttitudeTowards(GetPlayer).GetName")


if __name__=="__main__":unittest.main()
