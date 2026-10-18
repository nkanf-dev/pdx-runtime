"""CPU tests for the original scoped naval-battle player read interface."""
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime.v3_game._run import SourceUnavailable
from pdx_runtime.v3_game.military import MilitaryAPI
from pdx_runtime.v3_game.military import naval_battles as naval


class Context:
    def __init__(self):
        self.refreshes = 0
        self.calls = []

    async def refresh(self):
        self.refreshes += 1

    async def perform(self, name, operation):
        self.calls.append(name)
        return await operation()


class Run:
    def __init__(self, api):
        self.api, self.actor = api, 156
        self.available = True
        self.calls = []
        self.page_size = 64
        self.fleet_ids = [0x81000001]
        self.battle_count = 1
        self.group_count = 1
        self.ship_count = 2
        self.fail_source = None
        self.fail_field = None
        self.error_source = None
        self.invalid_count_source = None
        self.raise_source = None
        self.change_side_count = False
        self.null_commander = False
        self.stuck_source = None
        self.receipts = []

    async def page_available(self):
        return self.available

    async def country(self):
        return "country"

    @staticmethod
    def cell_value(cell):
        return (cell.value, cell.reason) if cell is not None else (None, "not copied")

    def value(self, expression, index):
        if expression == "MilitaryFormation.GetIDString":
            return str(self.fleet_ids[index])
        if expression.endswith("GetCommander.IsValid"):
            return not self.null_commander
        if expression.endswith("GetCommander.GetID"):
            return -1 if self.null_commander else -2130706429
        if expression.endswith(".GetID"):
            return -2130706428
        if expression.endswith("GetFormation.GetIDString"):
            return "2164260865"
        if expression.endswith("GetIDString"):
            return "opaque-group-" + str(index)
        if expression.endswith(("IsValid", "IsActive")):
            return True
        if expression.endswith(("HasEnded", "HasInvasion", "EndedInconclusively", "HasWon", "HasLost")):
            return False
        if expression.endswith("IsSunk"):
            return index == 1
        if expression.endswith("IsWithdrawn"):
            return False
        if any(word in expression for word in ("GetName", "GetFullName", "GetLocationName")):
            return "#bold Original ship#!"
        if any(word in expression for word in ("TooltipTag", "GetStatus", "GetOutcome", "GetDesc", "ModifierDesc", "GetDateDesc", "GetTerrain")):
            return "original native text"
        if any(word in expression for word in ("PowerProjection", "Hull", "HitPoints", "Percentage", "Readiness")):
            return Decimal("0.00000")
        return 0

    async def page(self, fields, bindings=(), *, source_expression=None, start=0,
                   limit=64, steps=(), source_arguments=()):
        self.calls.append((source_expression, tuple(fields), start, tuple(steps), tuple(bindings)))
        if source_expression == self.raise_source:
            raise RuntimeError("unsettled original native query")
        if source_expression == self.fail_source:
            raise SourceUnavailable("original provider is absent")
        if source_expression == naval._FLEETS:
            total = len(self.fleet_ids)
        elif source_expression == naval._BATTLES:
            total = self.battle_count
            if self.change_side_count and "Side." in fields[0]:
                total += 1
        elif source_expression.endswith("GetShipTemplateGroups"):
            total = self.group_count
        elif source_expression == "NavalBattleShipTemplateGroup.GetShips":
            total = self.ship_count
        else:
            raise AssertionError(source_expression)
        count = min(limit, self.page_size, max(0, total - start))
        copied = []
        for index in range(start, start + count):
            copied.append(tuple(NS(value=self.value(field, index),
                reason="native partial field" if field == self.fail_field else None)
                for field in fields))
        error = 123 if source_expression == self.error_source else 0
        page = NS(error=error, world=77, start=start, source_count=total,
            count_valid=source_expression != self.invalid_count_source,
            row_count=count, rows=tuple(copied), cells=(),
            next=start if source_expression == self.stuck_source else start + count,
            has_more=start + count < total, steps=tuple(steps), completed_steps=len(steps))
        self.receipts.append(page)
        return page


class NavalBattleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = MilitaryAPI(Context())
        self.run = Run(self.api)
        self.finished = []

    async def call(self, **kwargs):
        async def read(api, label, operation):
            try:
                value = await operation(self.run)
            except SourceUnavailable as error:
                value = {"status": "unavailable", "reason": str(error)}
            self.finished.append(value)
            return value
        with patch.object(naval, "owned_read", read):
            return await naval.naval_battles(self.api, **kwargs)

    async def test_public_military_naval_battles_returns_both_sides_and_combat_ships(self):
        result = await self.call()
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["feature_ids"], ["war.naval-battle"])
        self.assertEqual(len(result["battles"]), 1)
        battle = result["battles"][0]
        self.assertIsNone(battle["id"])
        self.assertFalse(battle["source_scoped_identity"]["stable_game_identity"])
        side = battle["sides"]["attacker"]
        self.assertEqual(side["country_id"], 0x81000004)
        self.assertEqual(side["commander_id"], 0x81000003)
        self.assertEqual(side["country_id_raw"], -2130706428)
        group = side["ship_groups"]["rows"][0]
        self.assertEqual(group["group_key"], "opaque-group-0")
        ships = group["combat_ships"]["rows"]
        self.assertFalse(ships[0]["sunk"])
        self.assertTrue(ships[1]["sunk"])
        self.assertEqual(ships[0]["readiness"], Decimal("0.00000"))
        self.assertEqual(ships[0]["name_raw"], "#bold Original ship#!")
        self.assertEqual(ships[0]["name"], "Original ship")
        self.assertEqual(self.api._ctx.calls, ["military.naval_battles"])
        self.assertEqual(self.api._ctx.refreshes, 1)
        self.assertTrue(all(len(call[1]) <= 32 for call in self.run.calls))
        self.assertTrue(all(not field.startswith("Battle.")
            for call in self.run.calls for field in call[1]))

    async def test_nested_sources_remain_original_creation_steps_not_retained_rows(self):
        await self.call()
        calls = [call for call in self.run.calls if call[0] == "NavalBattleShipTemplateGroup.GetShips"]
        self.assertEqual(len(calls), 2)
        for call in calls:
            steps = call[3]
            self.assertEqual([s.source_expression for s in steps[:2]], [naval._FLEETS, naval._BATTLES])
            self.assertEqual(len(steps), 3)
            self.assertEqual([s.expected_count for s in steps], [1, 1, 1])
            self.assertEqual(call[4], ("country",))

    async def test_selected_fleet_keeps_full_id_and_does_not_read_other_fleets(self):
        self.run.fleet_ids = [0x81000001, 0x81000002]
        result = await self.call(fleet_id=0x81000002)
        self.assertEqual(len(result["battles"]), 1)
        self.assertEqual(result["battles"][0]["source_fleet_id"], 0x81000002)
        self.assertTrue(all(call[3][0].index == 1 for call in self.run.calls if call[3]))

    async def test_zero_fleets_and_zero_battles_are_observed_not_failure(self):
        self.run.fleet_ids = []
        result = await self.call()
        self.assertEqual((result["status"], result["battles"]), ("observed", []))
        self.run.fleet_ids = [0x81000001]
        self.run.battle_count = 0
        result = await self.call()
        self.assertEqual((result["status"], result["battles"]), ("observed", []))

    async def test_missing_selected_fleet_is_not_found(self):
        result = await self.call(fleet_id=0x81000002)
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["battles"], [])

    async def test_missing_page_export_is_explicit_unavailable(self):
        self.run.available = False
        result = await self.call()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(self.run.calls, [])

    async def test_provider_failure_preserves_base_battle_and_other_side(self):
        self.run.fail_source = "NavalBattle.GetAttackerSide.GetShipTemplateGroups"
        result = await self.call()
        self.assertEqual(result["status"], "partial")
        sides = result["battles"][0]["sides"]
        self.assertEqual(sides["attacker"]["ship_groups"]["status"], "unavailable")
        self.assertEqual(sides["defender"]["ship_groups"]["status"], "observed")
        self.assertEqual(result["battles"][0]["name"], "Original ship")

    async def test_partial_scalar_keeps_actual_zero_and_original_reason(self):
        self.run.fail_field = "CombatShip.GetCurrentReadiness"
        result = await self.call()
        self.assertEqual(result["status"], "partial")
        ship = result["battles"][0]["sides"]["attacker"]["ship_groups"]["rows"][0]["combat_ships"]["rows"][0]
        self.assertEqual(ship["readiness"], Decimal("0.00000"))
        self.assertIn("readiness", ship["unavailable"])

    async def test_page_error_retains_copied_rows_and_stops_instead_of_replaying(self):
        self.run.page_size = 1
        self.run.error_source = "NavalBattleShipTemplateGroup.GetShips"
        result = await self.call()
        report = result["battles"][0]["sides"]["attacker"]["ship_groups"]["rows"][0]["combat_ships"]
        self.assertEqual(report["status"], "partial")
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["native_pages"][0]["error"], 123)
        calls = [c for c in self.run.calls if c[0] == self.run.error_source]
        self.assertTrue(all(c[2] == 0 for c in calls))

    async def test_unknown_query_outcome_propagates_without_finish_or_retry(self):
        self.run.raise_source = "NavalBattleShipTemplateGroup.GetShips"
        with self.assertRaisesRegex(RuntimeError, "unsettled original"):
            await self.call()
        self.assertEqual(self.finished, [])
        self.assertEqual(sum(c[0] == self.run.raise_source for c in self.run.calls), 1)

    async def test_pagination_and_multiple_groups_keep_source_coordinates(self):
        self.run.page_size = 1
        self.run.group_count = 2
        result = await self.call()
        groups = result["battles"][0]["sides"]["attacker"]["ship_groups"]["rows"]
        self.assertEqual([g["source_index"] for g in groups], [0, 1])
        for group in groups:
            ships = group["combat_ships"]["rows"]
            self.assertEqual([s["source_index"] for s in ships], [0, 1])
            self.assertEqual(ships[0]["source_scoped_identity"]["group_source_index"], group["source_index"])

    async def test_two_fleets_do_not_deduplicate_by_names_or_tooltips(self):
        self.run.fleet_ids.append(0x81000002)
        result = await self.call()
        self.assertEqual(len(result["battles"]), 2)
        self.assertEqual([r["source_fleet_id"] for r in result["battles"]], self.run.fleet_ids)
        self.assertIn("no name/tooltip deduplication", result["identity_scope"])

    async def test_summary_omits_deep_sources_by_choice_not_native_unavailable(self):
        result = await self.call(include_ships=False)
        self.assertEqual(result["status"], "observed")
        self.assertFalse(result["includes_combat_ships"])
        self.assertTrue(all("GetShipTemplateGroups" not in c[0] for c in self.run.calls))

    async def test_changed_source_count_preserves_side_pages_without_wrong_join(self):
        self.run.change_side_count = True
        result = await self.call(include_ships=False)
        self.assertEqual(result["status"], "partial")
        self.assertIn("side", result["battles"][0]["sides"]["attacker"]["unavailable"])
        self.assertEqual(len(result["sources"][0]["side_pages"]["attacker"]["rows"]), 2)

    async def test_null_commander_is_absence_not_invented_full_id(self):
        self.run.null_commander = True
        result = await self.call()
        side = result["battles"][0]["sides"]["attacker"]
        self.assertIsNone(side["commander_id"])
        self.assertNotIn("commander_id", side["unavailable"])

    async def test_invalid_inputs_fail_before_context_refresh(self):
        for value in [True, -1, 0xffffffff, "1"]:
            with self.assertRaises(ValueError):
                await self.call(fleet_id=value)
        with self.assertRaises(TypeError):
            await self.call(include_ships=1)
        self.assertEqual(self.api._ctx.refreshes, 0)

    async def test_nonadvancing_page_does_not_loop_or_replay(self):
        self.run.page_size = 1
        self.run.stuck_source = "NavalBattleShipTemplateGroup.GetShips"
        result = await self.call()
        self.assertEqual(result["status"], "partial")
        calls = [c for c in self.run.calls if c[0] == self.run.stuck_source]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c[2] == 0 for c in calls))

    async def test_unavailable_root_count_does_not_claim_selected_fleet_absent(self):
        self.run.fleet_ids = []
        self.run.invalid_count_source = naval._FLEETS
        result = await self.call(fleet_id=0x81000001)
        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
