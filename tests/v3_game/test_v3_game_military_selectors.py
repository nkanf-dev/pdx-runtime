"""Task selector behavior on real source relationships, using CPU native-boundary fakes."""
from types import SimpleNamespace as NS
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game.military import MilitaryAPI, _observations, _warfare
from pdx_runtime.v3_game.military._selectors import CountrySource
from tests.v3_game.test_v3_game_warfare_reads import WarfareReadFixture


class DirectoryTests(IsolatedAsyncioTestCase):
    async def test_native_ids_keep_generation_bits_but_never_admit_invalid_sentinel(self):
        self.assertEqual(MilitaryAPI._native_full_id(-2130706428), 0x81000004)
        self.assertEqual(MilitaryAPI._native_full_id(0), 0)
        self.assertIsNone(MilitaryAPI._native_full_id(-1))
        self.assertIsNone(MilitaryAPI._native_full_id("4294967295"))
        self.assertIsNone(MilitaryAPI._native_full_id(True))

    async def test_native_war_directory_bounds_one_country_source_without_deep_children(self):
        fixture = WarfareReadFixture()
        fixture.war_ids = [fixture.WAR_ID, fixture.WAR_ID + 1]
        fixture.war_values[fixture.WAR_ID + 1] = dict(fixture.war_values[fixture.WAR_ID])
        api = MilitaryAPI(fixture)
        with fixture.patches():
            first = await api.wars(limit=1)
            second = await api.wars(start=first["next"], limit=1)
        self.assertEqual([row["id"] for row in first["wars"]], [fixture.WAR_ID])
        self.assertEqual([row["id"] for row in second["wars"]], [fixture.WAR_ID + 1])
        self.assertTrue(first["has_more"])
        self.assertFalse(first["enumeration_complete"])
        self.assertTrue(second["enumeration_complete"])
        self.assertEqual(first["source_count"], 2)
        self.assertEqual(first["country_id"], 156)
        self.assertEqual(first["world"], fixture.world)
        self.assertEqual(deepcopy(first["wars"][0]["selector"]), {"type": "war", "id": fixture.WAR_ID})
        self.assertFalse(any("GetAttackers" in expression or "GetActiveBattles" in expression
                             for expression in fixture.expressions))
        self.assertFalse(fixture.handles)

    async def test_directory_unknown_stop_retains_copied_prefix_and_original_exception(self):
        fixture = WarfareReadFixture()
        api = MilitaryAPI(fixture)
        failure = RuntimeError("sampling stopped")
        original = fixture.evaluate
        def evaluate(native, expression, bindings, **kwargs):
            if expression == "War.IsAttacker(Country.Self)":
                raise failure
            return original(native, expression, bindings, **kwargs)
        fixture.evaluate = evaluate
        with fixture.patches(), self.assertRaises(RuntimeError) as raised:
            await api.wars(limit=1)
        self.assertIs(raised.exception, failure)
        report = failure.military_result
        self.assertEqual(report["wars"][0]["id"], fixture.WAR_ID)
        self.assertEqual(report["wars"][0]["name_raw"], "#bold Himalayan War#!")
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["enumeration_complete"])
        self.assertTrue(fixture.handles)


class Context:
    def __init__(self):
        self.world = 77
        self.calls = []
        self.refreshes = 0
        self.diagnostics = []
        self.adapters = {}
    async def country_id(self):
        return 11
    async def refresh(self):
        self.refreshes += 1
        return NS(paused=True)
    async def perform(self, label, operation):
        self.calls.append(label)
        return await operation()


class SourceRun:
    instances = []
    def __init__(self, api, label):
        self.api = api
        self.actor = None
        self.world = api._ctx.world
        self.operations = []
        self.sources = []
        self.instances.append(self)
    async def country(self):
        self.actor = await self.api._ctx.country_id()
        return data.ObjectBinding.country(1, self.actor)
    async def type_id(self, name):
        return {"Country": 1, "MilitaryFormation": 2, "Character": 3}.get(name, 9)
    async def functions(self, name):
        names = (("GetIDString", "GetNameNoFormatting") if name == "MilitaryFormation" else
                 ("GetID", "GetFullNameNoFormatting") if name == "Character" else
                 ("AccessGenerals", "AccessAdmirals") if name == "Country" else ())
        return {name: NS(argument_count=0, callbacks=(1, 0, 0, 0)) for name in names}
    async def provider(self, *args):
        return None
    async def elements(self, expression, bindings, named_type):
        country = bindings[0].object
        self.sources.append((expression, country))
        if country == 22 and expression in ("Country.GetMilitaryFormationsArmy", "Country.AccessGenerals"):
            yield 0, object(), data.ObjectBinding(2 if named_type == "MilitaryFormation" else 3, 99, retained=True)
    async def field(self, named_type, method, bindings, kinds, **kwargs):
        if method == "IsValid": return False, None
        values = {"GetIDString": "42", "GetID": 17, "GetNameNoFormatting": "#bold Allied army#!",
                  "GetFullNameNoFormatting": "Allied general", "GetCurrentManpower": 0,
                  "IsTraveling": False, "IsMobilized": False, "HasRaisedConscripts": False,
                  "GetOrganizationPercentage": 0, "GetSupplyRatio": None}
        if method in values:
            return values[method], "native supply field unavailable" if method == "GetSupplyRatio" else None
        return None, "fixture field unavailable"
    async def fields(self, named_type, bindings, specs):
        row = {"unavailable": {}}
        for method, key, kinds in specs:
            row[key], reason = await self.field(named_type, method, bindings, kinds)
            if reason: row["unavailable"][key] = reason
        return row
    async def finish(self, result):
        return result


class SelectorSourceTests(IsolatedAsyncioTestCase):
    async def test_foreign_readiness_uses_observed_country_source_and_preserves_zero_false_unknown(self):
        context = Context()
        api = MilitaryAPI(context)
        api._pause_for_action = AsyncMock()
        with patch.object(_warfare, "WarfareRead", SourceRun):
            result = await api.formation(42, country_id=22).read()
        self.assertEqual(result["country_id"], 22)
        self.assertEqual(result["actor_country_id"], 11)
        self.assertEqual(result["manpower"], 0)
        self.assertIs(result["travelling"], False)
        self.assertIsNone(result["supply_ratio"])
        self.assertIn("native supply field unavailable", result["unavailable"]["supply_ratio"])
        self.assertEqual(result["status"], "partial")
        self.assertIn(("Country.GetMilitaryFormationsArmy", 22), SourceRun.instances[-1].sources)
        self.assertNotIn(("Country.GetMilitaryFormationsArmy", 11), SourceRun.instances[-1].sources)
        self.assertIn("strategic_objective", result)
        self.assertEqual(context.refreshes, 1)
        self.assertEqual(len(context.calls), 1)
        api._pause_for_action.assert_not_awaited()

    async def test_foreign_selected_action_reaches_original_producer_and_native_validator(self):
        context = Context()
        api = MilitaryAPI(context)
        api._pause_for_action = AsyncMock()
        produced = AsyncMock(return_value=object())
        async def reject(run, owner, result, **kwargs):
            result.update(status="rejected", accepted=False, reason="original native ownership rule")
            return False
        with patch.object(_warfare, "WarfareRead", SourceRun), \
                patch.object(_observations, "_produced_owner", produced), \
                patch.object(_observations, "_validate_submit", reject):
            result = await api.formation(42, country_id=22).mobilization.set()
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "original native ownership rule")
        self.assertEqual(produced.await_args.args[1], "MilitaryFormation.Mobilize")
        self.assertIn(("Country.GetMilitaryFormationsArmy", 22), SourceRun.instances[-1].sources)
        self.assertEqual(len(context.calls), 1)
        api._pause_for_action.assert_awaited_once_with()

    async def test_commander_source_can_resolve_foreign_roster_without_local_player_guard(self):
        context = Context()
        api = MilitaryAPI(context)
        with patch.object(_warfare, "WarfareRead", SourceRun):
            result = await api.commander(17, country_id=22).read()
        self.assertEqual(result["country_id"], 22)
        self.assertEqual(result["name_raw"], "Allied general")
        self.assertIn(("Country.AccessGenerals", 22), SourceRun.instances[-1].sources)
        self.assertIn("order_target_front", result)
        self.assertNotIn("skill", result)  # no invented numeric-skill placeholder

    async def test_selected_war_front_and_battle_expansion_are_lazy_and_keep_source_values(self):
        context = Context()
        api = MilitaryAPI(context)
        fronts = {"fronts": [{"id": 7, "war_id": 5, "sides": {"initiator": {
            "formations": [{"formation_id": 42, "country_id": 22, "manpower": 0}]}}}], "status": "observed"}
        with patch.object(__import__("pdx_runtime.v3_game.military.battles", fromlist=["front_situations"]),
                "front_situations", AsyncMock(return_value=fronts)) as read, \
                patch.object(_warfare, "battles", AsyncMock(side_effect=AssertionError("unrequested battle read"))):
            selected = api.war(5)
            read.assert_not_awaited()
            report = await selected.fronts()
        self.assertEqual(read.await_args.kwargs, {"war_id": 5})
        row = report["fronts"][0]["sides"]["initiator"]["formations"][0]
        self.assertEqual(row["selector"], {"type": "formation", "id": 42, "country_id": 22})
        self.assertEqual(row["manpower"], 0)
