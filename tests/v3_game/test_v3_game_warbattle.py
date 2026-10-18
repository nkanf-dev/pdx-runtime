"""CPU-only tests for the independent V3 warfare observation module.

No game, process, controller, DLL, release environment, or runfile is opened.
The fixture keeps Battle and WarParticipant rows scalar-only and records every
original Creation projection used by the module.
"""
from contextlib import ExitStack
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pdx_runtime.v3_game._run import KnownResultError
from pdx_runtime import v3_data as data
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_game.military import battles as warbattle


class API:
    def __init__(self):
        self._ctx = object()

    @staticmethod
    def _native_full_id(value):
        if isinstance(value, str):
            raw = value.strip()
            if not raw.isascii() or not raw.isdigit():
                return None
            value = int(raw)
        if type(value) is not int or not -(1 << 31) <= value < 0xFFFFFFFF:
            return None
        value &= 0xFFFFFFFF
        return None if value == 0xFFFFFFFF else value


class Run:
    def __init__(self, api):
        self.api = api
        self.actor = 156
        self.providers = []
        self.element_sources = []
        self.element_bindings = []
        self.field_calls = []
        self.country_rosters = {
            0x81000011: (2164260931,),
            0x81000022: (2164260932,),
            0x81000055: (2164261031, 2164261032),
            0x81000066: (2164261131,),
        }
        self.formation_field_errors = {}
        self.partial_field = None
        self.field_errors = {}
        self.forbidden_providers = set()
        self.page_available_value = True
        self.typed_page_available_value = True
        self.page_availability_calls = []
        self.page_calls = []
        self.page_errors = {}
        self.page_source_counts = {}
        self.page_cell_reasons = {}
        self.page_values = {}
        self.page_count_valid = {}
        self.page_exceptions = {}
        self.page_row_limits = {}
        self.page_receipts = {}

    async def type_id(self, name):
        if name != "Country":
            raise AssertionError("unexpected binding type: " + name)
        return 1

    async def page_available(self, *, typed_steps=False):
        self.page_availability_calls.append(typed_steps)
        return self.typed_page_available_value if typed_steps else self.page_available_value

    async def page(self, fields, bindings=(), *, source_expression=None, start=0,
                   limit=64, steps=(), source_arguments=()):
        self.page_calls.append({
            "fields": tuple(fields), "bindings": tuple(bindings),
            "source_expression": source_expression, "start": start, "limit": limit,
            "steps": tuple(steps), "source_arguments": tuple(source_arguments),
        })
        if source_expression in self.page_exceptions:
            raise self.page_exceptions[source_expression]
        if source_expression in ("Battle.GetAttackerStatistics", "Battle.GetDefenderStatistics"):
            side = "Attacker" if "Attacker" in source_expression else "Defender"
            values = (f"#bold {side} Culture {start}#!", 100 + start, 200 + start, 300 + start)
        elif source_expression in ("Battle.GetAttackerBorrowedFormations",
                                   "Battle.GetDefenderBorrowedFormations"):
            attacker = "Attacker" in source_expression
            values = (str((2164261031 if attacker else 2164261131) + start),
                      "#bold Borrowed Army " + str(start) + "#!",
                      0x81000055 if attacker else 0x81000066)
        elif source_expression == "Battle.GetBattleUnitTypeList( MilitaryFormation.Self )":
            values = (f"#bold Infantry {start}#!", 5 + start, 2 + start, Decimal("0.75000"))
        elif source_expression == "BattleUnitType.GetUnits":
            values = ("#bold Battalion " + str(start) + "#!", False, 1000 + start, 1500,
                      Decimal("0.80000"), Decimal("12.50000"), Decimal("14.75000"),
                      2, Decimal("0.25000"), "Casualty factors", 0x81000077,
                      "#bold Barracks#!")
        else:
            raise AssertionError("unexpected page source: " + repr(source_expression))
        source_key = (source_expression, steps[-1].index) if len(steps) > 1 else source_expression
        def configured(mapping, default):
            return mapping.get(source_key, mapping.get(source_expression, default))
        source_count = configured(self.page_source_counts, 1)
        page_limit = min(limit, configured(self.page_row_limits,
                         64 if source_expression == "BattleUnitType.GetUnits" else 1))
        row_count = min(page_limit, max(0, source_count - start))
        rows, cells = [], []
        for local_index in range(row_count):
            index = start + local_index
            row_cells = []
            for field_index, (field, value) in enumerate(zip(fields, values)):
                value = self.page_values.get((source_expression, index, field), value)
                reason = self.page_cell_reasons.get((source_expression, index, field))
                row_cells.append(SimpleNamespace(
                    row=index, field=field_index,
                    fake_value=value, fake_reason=reason, error=0 if reason is None else 122,
                    kind=(data.Kind.STRING if isinstance(value, str) else
                          data.Kind.FIXED if isinstance(value, (Decimal, data.FixedPoint)) else
                          data.Kind.BOOL if type(value) is bool else
                          data.Kind.EMPTY if value is None else data.Kind.I64),
                    snapshot=value.encode("utf-8") if isinstance(value, str) else b"",
                    total=len(value.encode("utf-8")) if isinstance(value, str) else 0,
                    complete=reason is None,
                ))
            rows.append(tuple(row_cells))
            cells.extend(row_cells)
        receipts = configured(self.page_receipts, None)
        page = SimpleNamespace(
            error=configured(self.page_errors, 0), world=77,
            fields_count=len(fields), start=start, execution_thread_id=4321,
            source_count=source_count,
            count_valid=configured(self.page_count_valid, True), row_count=row_count,
            cells=tuple(cells), rows=tuple(rows),
            steps=tuple(steps) if receipts is None else receipts,
            completed_steps=len(steps) if receipts is None else
                            sum(receipt.element_obtained for receipt in receipts),
            next=start + row_count, has_more=start + row_count < source_count,
        )
        return page

    @staticmethod
    def cell_value(cell):
        if cell is None:
            return None, "native projection did not copy this field"
        return cell.fake_value, cell.fake_reason

    async def country(self):
        return "country"

    async def provider(self, named_type, method, arguments=0):
        key = (named_type, method, arguments)
        if key in self.forbidden_providers:
            raise AssertionError("Promote-only intermediate was required as a Function provider: " + repr(key))
        self.providers.append(key)
        return object()

    async def origin(self, element, named_type, size=4, borrowed=False):
        return element

    async def elements(self, expression, bindings, named_type):
        self.element_sources.append((expression, named_type))
        self.element_bindings.append((expression, tuple(bindings), named_type))
        if expression == "Country.GetMilitaryFormationsArmy":
            country_id = bindings[0].object
            for index, formation_id in enumerate(self.country_rosters.get(country_id, ())):
                yield index, None, ("formation", country_id, formation_id)
        elif expression == "Country.GetMilitaryFormationsFleet":
            return
        elif expression == "Country.AccessWars":
            yield 0, 0x8100002A, "war"
        elif expression == "War.AccessFronts":
            yield 0, None, "front"
        elif expression == "Country.AccessStates":
            yield 0, None, "state"
        else:
            raise AssertionError("inline source escaped through elements: " + expression)

    async def field(self, named_type, method, bindings, kinds, *, arguments=0, expression=None):
        expression = expression or named_type + "." + method
        self.field_calls.append(expression)
        if expression == "MilitaryFormation.GetIDString":
            formation_id = bindings[0][2]
            return str(formation_id), self.formation_field_errors.get(formation_id)
        if expression == self.partial_field:
            return "partial native text", expression + ": native error 122; native text snapshot is partial"
        if expression in self.field_errors:
            return None, self.field_errors[expression]
        ids = {
            "Front.GetID": 0x81000033,
            "State.GetID": -2130706306,
            "Front.GetInitiatorSide.GetCountry.GetID": 0x81000011,
            "Front.GetTargetSide.GetCountry.GetID": 0x81000022,
            "State.GetOccupant.GetID": 0x81000011,
            "State.GetEnemyWithHighestOccupation.GetID": 0x81000022,
        }
        if expression in ids:
            return ids[expression], None
        texts = {
            "Front.GetNameNoFormatting": "#bold Tibet Front#!",
            "State.GetNameNoFormatting": "#bold Lhasa#!",
            "Front.GetInitiatorSide.GetCountry.GetNameNoFormatting": "#bold Initiator#!",
            "Front.GetTargetSide.GetCountry.GetNameNoFormatting": "#bold Target#!",
            "Front.GetOccupationProgressDesc": "Occupation 25%",
            "Front.GetPlayerAdvantageDesc": "Advantage +7",
            "FrontSide.GetNumUnitsBreakdown": "12 battalions",
            "FrontSide.GetAdvantageDesc": "Superior",
            "FrontSide.GetUnitMoraleDesc": "High morale",
            "State.GetOccupantsTooltip": "Occupied by Attacker",
        }
        if expression in texts:
            return texts[expression], None
        if data.Kind.BOOL in kinds:
            return True, None
        if data.Kind.STRING in kinds:
            return expression + " value", None
        if data.Kind.FIXED in kinds:
            return Decimal("0.62500"), None
        if data.Kind.I64 in kinds:
            return 9000, None
        if data.Kind.I32 in kinds:
            return 12, None
        if data.Kind.U32 in kinds:
            return 0x81000044, None
        raise AssertionError((named_type, method, expression, kinds))


class Harness:
    def __init__(self):
        self.api = API()
        self.run = Run(self.api)
        self.count_calls = []
        self.copy_calls = []
        self.copy_errors = {}

    async def owned_read(self, api, label, operation):
        self.label = label
        return await operation(self.run)

    async def find_war(self, run, wanted):
        self.wanted = wanted
        return "country", "war"

    async def source_count(self, run, source, bindings):
        self.count_calls.append((source, bindings))
        counts = {
            "War.GetActiveBattles": 1,
            "War.GetEndedBattles": 1,
            "Front.GetInitiatorSide.GetCountries": 1,
            "Front.GetInitiatorSide.GetArmies": 1,
            "Front.GetTargetSide.GetCountries": 1,
            "Front.GetTargetSide.GetArmies": 1,
            "Front.AccessActiveBattles": 1,
            "Front.AccessEndedBattles": 0,
            "War.GetAttackers": 1,
            "War.GetDefenders": 1,
            "State.GetOccupationPiechartData": 1,
        }
        return counts[source]

    async def copied(self, run, source, index, count, expression, bindings, kinds):
        self.copy_calls.append((source, index, count, expression, bindings, kinds))
        if expression in self.copy_errors:
            return self.copy_errors[expression]
        role = "Attacker" if "Attacker" in expression or source.endswith("Attackers") else "Defender"
        ids = {
            "Battle.GetAttackerSide.GetCountry.GetID": 0x81000011,
            "Battle.GetDefenderSide.GetCountry.GetID": 0x81000022,
            "Battle.GetAttackerSide.GetCommander.GetID": -2130706401,
            "Battle.GetDefenderSide.GetCommander.GetID": 0x81000032,
            "Battle.GetAttackerSide.GetFormation.GetIDString": "2164260931",
            "Battle.GetDefenderSide.GetFormation.GetIDString": "2164260932",
            "Battle.GetWinner.GetID": 0x81000011,
            "Battle.GetLoser.GetID": 0x81000022,
            "Country.GetID": 0x81000011 if "Initiator" in source else 0x81000022,
            "MilitaryFormation.GetIDString": "2164260944",
            "WarParticipant.GetCountry.GetID": 0x81000011 if source.endswith("Attackers") else 0x81000022,
            "OccupationPiechartData.GetCountry.GetID": 0x81000011,
        }
        if expression in ids:
            return ids[expression], None
        names = {
            "Battle.GetName": "#bold Battle of Lhasa#!",
            "Battle.GetAttackerSide.GetCountry.GetNameNoFormatting": "#bold Attacker#!",
            "Battle.GetDefenderSide.GetCountry.GetNameNoFormatting": "#bold Defender#!",
            "Battle.GetAttackerSide.GetCommander.GetFullNameNoFormatting": "#bold General A#!",
            "Battle.GetDefenderSide.GetCommander.GetFullNameNoFormatting": "#bold General D#!",
            "Battle.GetAttackerSide.GetFormation.GetNameNoFormatting": "#bold First Army#!",
            "Battle.GetDefenderSide.GetFormation.GetNameNoFormatting": "#bold Second Army#!",
            "Battle.GetAttackerCondition.GetNameNoFormatting": "#bold Mud#!",
            "Battle.GetDefenderCondition.GetNameNoFormatting": "#bold Dug In#!",
            "Battle.GetWinner.GetNameNoFormatting": "#bold Attacker#!",
            "Battle.GetLoser.GetNameNoFormatting": "#bold Defender#!",
            "Country.GetNameNoFormatting": "#bold Front Country#!",
            "MilitaryFormation.GetNameNoFormatting": "#bold Front Army#!",
            "WarParticipant.GetCountry.GetNameNoFormatting": "#bold " + role + "#!",
            "OccupationPiechartData.GetCountry.GetNameNoFormatting": "#bold Occupier#!",
        }
        if expression in names:
            return names[expression], None
        strings = {
            "Battle.GetTooltipTag": "BATTLE_LHASA",
            "Battle.GetLocationName": "Lhasa",
            "Battle.GetTerrain": "Mountain",
            "Battle.GetTerrainDesc": "Mountain penalties",
            "Battle.GetStartDate": "1 January 1900",
            "Battle.GetEndDate": "9 January 1900",
            "Battle.GetDateDesc": "8 days",
            "Battle.GetOccupationWon": "+2.5%",
            "Battle.GetVictoryScore": "+4.0",
            "WarParticipant.GetExhaustionDesc": "War exhaustion factors",
            "WarParticipant.GetWarSupportDesc": "War support factors",
            "WarParticipant.GetWarExhaustionFromAcceptanceOfDeadDesc": "Accepted dead",
            "WarParticipant.GetCanCapitulateDesc": "Cannot capitulate yet",
        }
        if expression in strings:
            return strings[expression], None
        if data.Kind.BOOL in kinds:
            return True, None
        if data.Kind.STRING in kinds:
            return expression + " value", None
        if data.Kind.FIXED in kinds:
            return Decimal("0.50000"), None
        if data.Kind.I64 in kinds:
            return 1234, None
        if data.Kind.I32 in kinds:
            return 7, None
        if data.Kind.U32 in kinds:
            return 0x81000011, None
        raise AssertionError((source, expression, kinds))

    def patches(self):
        stack = ExitStack()
        stack.enter_context(patch.object(warbattle, "owned_read", self.owned_read))
        stack.enter_context(patch.object(warbattle, "_war", self.find_war))
        stack.enter_context(patch.object(warbattle, "_source_count", self.source_count))
        stack.enter_context(patch.object(warbattle, "_copied", self.copied))
        return stack


class NativePageRun:
    """Actual project_page wire/query with mocked process bytes, never a game call."""
    def __init__(self):
        self.api = API()
        self.client = Mock(pid=1)
        self.status = {"status": "ok", "request_id": 7, "completed_request_id": 7}
        self.client.request.side_effect = lambda command: (
            {"status": "ok", "request_id": 7, "error": 0}
            if command.startswith("interface-callback ") else self.status)
        self.process = Mock(pid=1)
        self.process.resolve_export.return_value = 0xA100
        self.process.export_owner.return_value = 0xA000
        self.process.allocate.return_value = 0x2000
        self.query = None
        self.error = 0
        self.count_valid = True
        self.source_count = 1
        self.completed_steps = 2
        self.values = (
            "#bold Native Battalion#!", False, 0, 1000,
            data.FixedPoint(0), data.FixedPoint(1250000), data.FixedPoint(1475000),
            0, data.FixedPoint(25000), "Native casualties", -2130706313,
            "#bold Native Barracks#!",
        )
        self.copy_fault = None

    @staticmethod
    def cell_value(cell):
        return warbattle.WarfareRead.cell_value(cell)

    async def page_available(self, *, typed_steps=False):
        if not typed_steps:
            raise AssertionError("deep GetUnits must probe typed-prefix support")
        return True

    async def page(self, fields, bindings=(), **kwargs):
        self.query = data.project_page(self.client, fields, (tuple(bindings),),
                                       process=self.process, **kwargs)
        self.raw = bytearray(self.process.write.call_args.args[1])
        h = list(data.PAGE_REQUEST.unpack_from(self.raw))
        suffix = list(data.PAGE_SCOPED_SUFFIX.unpack_from(self.raw, data.PAGE_REQUEST.size))
        suffix[2] = self.completed_steps
        data.PAGE_SCOPED_SUFFIX.pack_into(self.raw, data.PAGE_REQUEST.size, *suffix)
        for index in range(suffix[1]):
            offset = suffix[0] + index * data.CREATION_STEP.size
            step = list(data.CREATION_STEP.unpack_from(self.raw, offset))
            if index < self.completed_steps:
                step[4:] = 1, 1, step[2] + 1, 1, 50 + index, 1
            data.CREATION_STEP.pack_into(self.raw, offset, *step)
        count = len(fields) if self.count_valid and self.source_count else 0
        table = bytearray(count * data.PAGE_CELL.size)
        payload = bytearray()
        for index, value in enumerate(self.values[:count]):
            if isinstance(value, str):
                kind, bits, snapshot = data.Kind.STRING, 0, value.encode()
            elif isinstance(value, data.FixedPoint):
                kind, bits, snapshot = data.Kind.FIXED, value.bits, b""
            elif type(value) is bool:
                kind, bits, snapshot = data.Kind.BOOL, int(value), b""
            elif value is None:
                kind, bits, snapshot = data.Kind.EMPTY, 0, b""
            else:
                kind, bits, snapshot = data.Kind.I64, value & ((1 << 64) - 1), b""
            data.PAGE_CELL.pack_into(table, index * data.PAGE_CELL.size,
                h[9], index, 0, kind, 77, 3, bits, len(table) + len(payload),
                len(snapshot), len(snapshot), data.Kind.OBJECT, 0, 0, 0)
            payload.extend(snapshot)
        self.copied = bytes(table + payload)
        h[1], h[11], h[12], h[13], h[14] = (
            self.error, 4321, self.source_count, int(self.count_valid), int(bool(count)))
        h[15], h[16], h[17] = 0x3000 if count else 0, len(self.copied), count
        data.PAGE_REQUEST.pack_into(self.raw, 0, *h)
        def read(address, size):
            if address == 0x2000:
                return bytes(self.raw)
            if self.copy_fault is not None and address == 0x3000 + 65536:
                raise self.copy_fault
            offset = address - 0x3000
            return self.copied[offset:offset + size]
        self.process.read.side_effect = read
        return self.query.read()


class WarBattleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = Harness()

    async def test_battle_detail_keeps_inline_rows_scalar_and_never_invents_id(self):
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["feature_ids"], ["war.land-battle-detail"])
        self.assertEqual(result["missing_required_outputs"], [])
        self.assertTrue(result["page_projection_available"])
        battle = result["battles"][0]
        self.assertIsNone(battle["id"])
        self.assertFalse(battle["source_scoped_identity"]["stable_game_identity"])
        self.assertEqual(battle["name"], "Battle of Lhasa")
        self.assertEqual(battle["name_raw"], "#bold Battle of Lhasa#!")
        self.assertEqual(battle["sides"]["attacker"]["country_id"], 0x81000011)
        self.assertEqual(battle["sides"]["attacker"]["commander_name"], "General A")
        self.assertEqual(battle["sides"]["attacker"]["formation_id"], 2164260931)
        formation = battle["sides"]["attacker"]["commanded_formation_current_state"]
        self.assertEqual(formation["combat_units"], 7)
        self.assertEqual(formation["current_manpower"], 1234)
        self.assertTrue(formation["in_battle"])
        units = battle["sides"]["attacker"]["participating_units"]
        self.assertEqual(units["coverage"],
                         "native_side_aggregate_and_commanded_and_borrowed_formations")
        self.assertEqual(units["lost_units"], 7)
        self.assertEqual(battle["conditions"]["defender"]["name"], "Dug In")
        unit_types = units["unit_types"]
        self.assertEqual(unit_types["status"], "observed")
        self.assertEqual(unit_types["rows"][0]["unit_type_name"], "Infantry 0")
        self.assertEqual(unit_types["rows"][0]["unit_type_name_raw"], "#bold Infantry 0#!")
        self.assertEqual(unit_types["rows"][0]["regular_units"], 5)
        self.assertNotIn("unit_types", units["unavailable"])
        culture = battle["culture_casualty_statistics"]["attacker"]
        self.assertEqual(culture["status"], "observed")
        self.assertEqual(culture["rows"][0]["culture_name"], "Attacker Culture 0")
        self.assertEqual(culture["rows"][0]["dead"], 100)
        self.assertEqual(culture["native_pages"][0]["error"], 0)
        self.assertEqual(culture["native_pages"][0]["world"], 77)
        self.assertEqual(culture["native_pages"][0]["page"].world, 77)
        self.assertEqual(battle["aggregate_casualty_statistics"]["attacker"]["lost_units"], 7)
        sources = [call["source_expression"] for call in self.h.run.page_calls]
        self.assertEqual(sources[:2], [
            "Battle.GetAttackerStatistics", "Battle.GetDefenderStatistics"
        ])
        self.assertEqual(self.h.run.page_calls[0]["fields"], (
            "CultureCasualtyStatistics.GetCulture.GetName",
            "CultureCasualtyStatistics.GetDead",
            "CultureCasualtyStatistics.GetWounded",
            "CultureCasualtyStatistics.GetDemoralized",
        ))
        unit_call = next(call for call in self.h.run.page_calls
                         if call["source_expression"].startswith("Battle.GetBattleUnitTypeList"))
        self.assertEqual(unit_call["fields"], (
            "BattleUnitType.GetCombatUnitType.GetName",
            "BattleUnitType.GetNumberOfRegularUnits",
            "BattleUnitType.GetNumberOfConscriptUnits",
            "BattleUnitType.GetAverageMorale",
        ))
        self.assertEqual(unit_call["source_arguments"], ("MilitaryFormation.Self",))
        self.assertEqual(unit_call["bindings"][0], "war")
        self.assertEqual(unit_call["bindings"][1], ("formation", 0x81000011, 2164260931))
        self.assertEqual(len(unit_call["steps"]), 1)
        self.assertEqual(unit_call["steps"][0].source_expression, "War.GetActiveBattles")
        self.assertEqual(unit_call["steps"][0].index, 0)
        self.assertEqual(unit_call["limit"], 64)
        self.assertFalse(any(name == "Battle" for _, name in self.h.run.element_sources))
        self.assertTrue(all(call[0] == "War.GetActiveBattles" for call in self.h.copy_calls))

    async def test_borrowed_formations_keep_original_pages_and_typed_unit_sources(self):
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        battle = result["battles"][0]
        for role, country_id, formation_id in (
            ("attacker", 0x81000055, 2164261031),
            ("defender", 0x81000066, 2164261131),
        ):
            units = battle["sides"][role]["participating_units"]
            borrowed = units["borrowed_formations"]
            self.assertEqual(borrowed["status"], "observed")
            self.assertEqual(borrowed["source"], "Battle.Get" + role.title() + "BorrowedFormations")
            formation = borrowed["rows"][0]
            self.assertEqual(formation["formation_id"], formation_id)
            self.assertEqual(formation["formation_id_raw"], str(formation_id))
            self.assertEqual(formation["country_id"], country_id)
            self.assertEqual(formation["name"], "Borrowed Army 0")
            self.assertEqual(formation["unit_types"]["status"], "observed")
            self.assertEqual(formation["unit_types"]["rows"][0]["regular_units"], 5)
            self.assertNotIn("borrowed_formations", units["unavailable"])
            matching = [call for call in self.h.run.page_calls
                        if call["source_expression"] == "Battle.GetBattleUnitTypeList( MilitaryFormation.Self )"
                        and call["bindings"][1] == ("formation", country_id, formation_id)]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["source_arguments"], ("MilitaryFormation.Self",))
        self.assertNotIn("borrowed_formations", result["missing_required_outputs"])
        self.assertNotIn("participating_unit_types", result["missing_required_outputs"])

    async def test_foreign_commanded_formations_do_not_use_player_roster(self):
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        side = result["battles"][0]["sides"]["defender"]
        self.assertEqual(side["participating_units"]["unit_types"]["status"], "observed")
        countries = [bindings[0].object for expression, bindings, _ in self.h.run.element_bindings
                     if expression.startswith("Country.GetMilitaryFormations")]
        self.assertNotIn(self.h.run.actor, countries)
        self.assertIn(0x81000022, countries)
        self.assertEqual(set(countries), {0x81000011, 0x81000022, 0x81000055, 0x81000066})

    async def test_each_observed_country_source_is_scanned_at_most_once(self):
        source = "Battle.GetAttackerBorrowedFormations"
        self.h.run.page_source_counts[source] = 2
        # Both borrowed rows share the actual commanded formation's Country.
        self.h.run.page_values[(source, 0, "MilitaryFormation.GetCountry.GetID")] = 0x81000011
        self.h.run.page_values[(source, 1, "MilitaryFormation.GetCountry.GetID")] = 0x81000011
        self.h.run.country_rosters[0x81000011] += (2164261031, 2164261032)
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        units = result["battles"][0]["sides"]["attacker"]["participating_units"]
        self.assertEqual(len(units["borrowed_formations"]["rows"]), 2)
        calls = [item for item in self.h.run.element_bindings
                 if item[0] == "Country.GetMilitaryFormationsArmy" and item[1][0].object == 0x81000011]
        self.assertEqual(len(calls), 1)
        for formation in units["borrowed_formations"]["rows"]:
            self.assertEqual(formation["unit_types"]["status"], "observed")
        self.assertFalse(any(item[0] == "Country.GetMilitaryFormationsFleet"
                             for item in self.h.run.element_bindings))

    async def test_unrelated_roster_error_does_not_hide_selected_formation(self):
        self.h.run.country_rosters[0x81000011] = (123, 2164260931)
        self.h.run.formation_field_errors[123] = "native formation identity copy is partial"
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        types = result["battles"][0]["sides"]["attacker"]["participating_units"]["unit_types"]
        self.assertEqual(types["status"], "observed")
        self.assertEqual(types["rows"][0]["regular_units"], 5)
        observation = types["formation_sources"][0]
        self.assertEqual(observation["status"], "partial")
        self.assertIn("partial", observation["rows"][0]["unavailable"]["formation_id"])

    async def test_borrowed_partial_identity_and_source_error_keep_copied_rows(self):
        source = "Battle.GetAttackerBorrowedFormations"
        self.h.run.page_errors[source] = 21
        self.h.run.page_source_counts[source] = 2
        self.h.run.page_cell_reasons[(source, 0, "MilitaryFormation.GetIDString")] = "native copy incomplete"
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        borrowed = result["battles"][0]["sides"]["attacker"]["participating_units"]["borrowed_formations"]
        self.assertEqual(borrowed["status"], "partial")
        self.assertEqual(borrowed["rows"][0]["formation_id"], 2164261031)
        self.assertEqual(borrowed["rows"][0]["unit_types"]["status"], "observed")
        self.assertEqual(borrowed["native_pages"][0]["page"].error, 21)
        self.assertEqual(borrowed["native_pages"][0]["source_count"], 2)
        self.assertEqual(len(borrowed["native_pages"]), 1)
        self.assertIn("borrowed_formations", result["missing_required_outputs"])
        self.assertEqual(result["battles"][0]["sides"]["defender"]["participating_units"]
                         ["borrowed_formations"]["status"], "observed")

    async def test_missing_formation_keeps_borrowed_row_and_original_source_observations(self):
        self.h.run.country_rosters[0x81000055] = ()
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        borrowed = result["battles"][0]["sides"]["attacker"]["participating_units"]["borrowed_formations"]
        self.assertEqual(borrowed["status"], "observed")
        self.assertEqual(borrowed["rows"][0]["formation_id"], 2164261031)
        types = borrowed["rows"][0]["unit_types"]
        self.assertEqual(types["status"], "unavailable")
        observations = types["details"][0]["formation_sources"]
        self.assertEqual([row["source"] for row in observations], [
            "Country.GetMilitaryFormationsArmy", "Country.GetMilitaryFormationsFleet",
        ])
        self.assertTrue(all(row["rows"] == [] for row in observations))
        self.assertIn("participating_unit_types", result["missing_required_outputs"])
        self.assertNotIn("borrowed_formations", result["missing_required_outputs"])

    async def test_empty_borrowed_source_is_observed_without_fabricated_rows(self):
        self.h.run.page_source_counts["Battle.GetAttackerBorrowedFormations"] = 0
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        borrowed = result["battles"][0]["sides"]["attacker"]["participating_units"]["borrowed_formations"]
        self.assertEqual(borrowed["status"], "observed")
        self.assertEqual(borrowed["rows"], [])
        self.assertEqual(borrowed["native_pages"][0]["source_count"], 0)
        self.assertTrue(borrowed["native_pages"][0]["count_valid"])

    async def test_failed_borrowed_source_is_not_reported_as_empty(self):
        source = "Battle.GetAttackerBorrowedFormations"
        self.h.run.page_source_counts[source] = 0
        self.h.run.page_count_valid[source] = False
        self.h.run.page_errors[source] = 1168
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        borrowed = result["battles"][0]["sides"]["attacker"]["participating_units"]["borrowed_formations"]
        self.assertEqual(borrowed["status"], "partial")
        self.assertFalse(borrowed["native_pages"][0]["count_valid"])
        self.assertEqual(borrowed["native_pages"][0]["error"], 1168)
        self.assertIn("borrowed_formations", result["missing_required_outputs"])

    async def test_completed_source_exception_preserves_original_native_result(self):
        source = "Battle.GetAttackerBorrowedFormations"
        native = SimpleNamespace(error=1168, value_handle=0, world=77)
        error = KnownResultError(source, native)
        self.h.run.page_exceptions[source] = error
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        borrowed = result["battles"][0]["sides"]["attacker"]["participating_units"]["borrowed_formations"]
        self.assertIs(borrowed["details"][0]["error"], error)
        self.assertIs(borrowed["details"][0]["native_result"], native)
        self.assertIsNone(borrowed["rows"])
        self.assertEqual(result["battles"][0]["terrain"], "Mountain")

    async def test_deep_rows_preserve_zero_false_and_none_without_truthiness_filter(self):
        source = "Battle.GetBattleUnitTypeList( MilitaryFormation.Self )"
        self.h.run.page_values[(source, 0, "BattleUnitType.GetNumberOfRegularUnits")] = 0
        self.h.run.page_values[(source, 0, "BattleUnitType.GetNumberOfConscriptUnits")] = False
        self.h.run.page_values[(source, 0, "BattleUnitType.GetAverageMorale")] = None
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        row = result["battles"][0]["sides"]["attacker"]["participating_units"]["unit_types"]["rows"][0]
        self.assertEqual(row["regular_units"], 0)
        self.assertIs(row["conscript_units"], False)
        self.assertIsNone(row["average_morale"])
        self.assertNotIn("regular_units", row["unavailable"])
        self.assertNotIn("conscript_units", row["unavailable"])
        self.assertNotIn("average_morale", row["unavailable"])

    async def test_zero_country_and_formation_ids_are_not_treated_as_absent(self):
        source = "Battle.GetAttackerBorrowedFormations"
        self.h.run.page_values[(source, 0, "MilitaryFormation.GetIDString")] = "0"
        self.h.run.page_values[(source, 0, "MilitaryFormation.GetCountry.GetID")] = 0
        self.h.run.country_rosters[0] = (0,)
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        borrowed = result["battles"][0]["sides"]["attacker"]["participating_units"]["borrowed_formations"]
        formation = borrowed["rows"][0]
        self.assertEqual(formation["formation_id"], 0)
        self.assertEqual(formation["country_id"], 0)
        self.assertEqual(formation["unit_types"]["status"], "observed")
        self.assertIn(("formation", 0, 0), [call["bindings"][1] for call in self.h.run.page_calls
                      if call["source_arguments"]])

    async def test_invalid_borrowed_id_preserves_raw_value_without_binding_country(self):
        source = "Battle.GetAttackerBorrowedFormations"
        self.h.run.page_values[(source, 0, "MilitaryFormation.GetIDString")] = "4294967295"
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        formation = result["battles"][0]["sides"]["attacker"]["participating_units"]["borrowed_formations"]["rows"][0]
        self.assertIsNone(formation["formation_id"])
        self.assertEqual(formation["formation_id_raw"], "4294967295")
        self.assertIn("formation_id", formation["unavailable"])
        self.assertEqual(formation["unit_types"]["status"], "unavailable")
        self.assertNotIn(0x81000055, [bindings[0].object for expression, bindings, _ in self.h.run.element_bindings
                                   if expression.startswith("Country.GetMilitaryFormations")])

    async def test_unknown_page_error_escapes_to_existing_run_without_fallback(self):
        source = "Battle.GetAttackerBorrowedFormations"
        error = RuntimeError("original query pending; no replay")
        self.h.run.page_exceptions[source] = error
        with self.h.patches(), self.assertRaises(RuntimeError) as raised:
            await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        self.assertIs(raised.exception, error)
        self.assertEqual(sum(call["source_expression"] == source for call in self.h.run.page_calls), 1)
        self.assertFalse(any(named_type == "Battle" for _, named_type in self.h.run.element_sources))

    async def test_empty_unit_type_sources_need_no_fabricated_combat_units(self):
        self.h.run.page_source_counts["Battle.GetBattleUnitTypeList( MilitaryFormation.Self )"] = 0
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["missing_required_outputs"], [])
        self.assertEqual(result["battles"][0]["sides"]["attacker"]["participating_units"]
                         ["unit_types"]["rows"], [])

    async def test_combat_unit_deep_route_uses_typed_prefix_and_only_copied_leaves(self):
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        battle = result["battles"][0]
        units = battle["sides"]["attacker"]["participating_units"]["unit_types"]["rows"][0]["units"]
        self.assertEqual(units["status"], "observed")
        self.assertEqual(units["source"], "BattleUnitType.GetUnits")
        self.assertEqual(units["rows"][0]["name"], "Battalion 0")
        self.assertIs(units["rows"][0]["conscript"], False)
        self.assertEqual(units["rows"][0]["manpower"], 1000)
        self.assertEqual(units["rows"][0]["morale"], Decimal("0.80000"))
        self.assertEqual(units["rows"][0]["building_id"], 0x81000077)
        self.assertEqual(units["rows"][0]["building_name"], "Barracks")
        calls = [call for call in self.h.run.page_calls
                 if call["source_expression"] == "BattleUnitType.GetUnits"]
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0]["bindings"], ("war", ("formation", 0x81000011, 2164260931)))
        for call in calls:
            self.assertEqual(call["source_arguments"], ())
            self.assertEqual(len(call["steps"]), 2)
            war_step, type_step = call["steps"]
            self.assertIsInstance(type_step, data.CreationStep)
            self.assertEqual((war_step.source_expression, war_step.index, war_step.source_arguments),
                             ("War.GetActiveBattles", 0, ()))
            self.assertEqual(type_step.source_expression,
                             "Battle.GetBattleUnitTypeList( MilitaryFormation.Self )")
            self.assertEqual(type_step.source_arguments, ("MilitaryFormation.Self",))
            self.assertEqual(call["fields"], tuple(row[0] for row in warbattle._BATTLE_COMBAT_UNIT_FIELDS))
            self.assertLessEqual(len(call["fields"]), 32)
            self.assertEqual(call["limit"], 64)
        self.assertFalse(any(name in ("Battle", "BattleUnitType", "CombatUnit")
                             for _, name in self.h.run.element_sources))
        self.assertEqual(result["missing_required_outputs"], [])

    async def test_combat_unit_pages_batch_64_rows_then_continue_native_cursor(self):
        self.h.run.page_source_counts["BattleUnitType.GetUnits"] = 65
        units = await warbattle._battle_combat_units(
            self.h.run, "war", "formation", "War.GetActiveBattles", 3, 7)
        self.assertEqual(units["status"], "observed")
        self.assertEqual(len(units["rows"]), 65)
        self.assertEqual([page["row_count"] for page in units["native_pages"]], [64, 1])
        self.assertEqual([page["start"] for page in units["native_pages"]], [0, 64])
        self.assertEqual([row["source_index"] for row in units["rows"]], list(range(65)))
        for call in self.h.run.page_calls:
            self.assertEqual(call["bindings"], ("war", "formation"))
            self.assertEqual(call["steps"][0].index, 3)
            self.assertEqual(call["steps"][1].index, 7)
            self.assertEqual(call["steps"][1].source_arguments, ("MilitaryFormation.Self",))
            self.assertEqual(call["limit"], 64)
        self.assertEqual(len(self.h.run.page_calls), 2)

    async def test_each_type_source_index_is_its_own_fresh_prefix_coordinate(self):
        type_source = "Battle.GetBattleUnitTypeList( MilitaryFormation.Self )"
        self.h.run.page_source_counts[type_source] = 2
        self.h.run.page_source_counts[("BattleUnitType.GetUnits", 1)] = 0
        types = await warbattle._battle_unit_types(
            self.h.run, "war", "formation", "War.GetActiveBattles", 4)
        calls = [call for call in self.h.run.page_calls if call["source_expression"] == "BattleUnitType.GetUnits"]
        self.assertEqual([call["steps"][1].index for call in calls], [0, 1])
        self.assertTrue(all(call["steps"][0].index == 4 for call in calls))
        self.assertEqual(types["rows"][0]["units"]["status"], "observed")
        self.assertEqual(types["rows"][1]["units"]["rows"], [])
        self.assertEqual(types["rows"][1]["units"]["status"], "observed")
        self.assertTrue(all(not row["atomic_snapshot"] for row in types["rows"]))

    async def test_empty_getunits_is_observed_but_failed_capture_count_is_not_empty(self):
        source = "BattleUnitType.GetUnits"
        self.h.run.page_source_counts[source] = 0
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        units = result["battles"][0]["sides"]["attacker"]["participating_units"]["unit_types"]["rows"][0]["units"]
        self.assertEqual(units["status"], "observed")
        self.assertEqual(units["rows"], [])
        self.assertTrue(units["native_pages"][0]["count_valid"])
        self.assertEqual(result["missing_required_outputs"], [])
        self.h.run.page_errors[source] = 13
        self.h.run.page_count_valid[source] = False
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        units = result["battles"][0]["sides"]["attacker"]["participating_units"]["unit_types"]["rows"][0]["units"]
        self.assertEqual(units["status"], "partial")
        self.assertFalse(units["native_pages"][0]["count_valid"])
        self.assertEqual(units["native_pages"][0]["error"], 13)
        self.assertIn("participating_combat_units", result["missing_required_outputs"])
        self.assertNotIn("participating_unit_types", result["missing_required_outputs"])

    async def test_combat_unit_partial_cells_page_errors_and_prefix_receipts_survive(self):
        source = "BattleUnitType.GetUnits"
        self.h.run.page_source_counts[source] = 65
        self.h.run.page_errors[source] = 21
        self.h.run.page_cell_reasons[(source, 0, "CombatUnit.GetName")] = "native string copy is partial"
        receipts = (
            data.CreationStepReceipt(100, 20, 0, None, 1, 1, 1, 1, 40, 1),
            data.CreationStepReceipt(120, 50, 0, None, 1, 1, 1, 1, 41, 1),
        )
        self.h.run.page_receipts[source] = receipts
        units = await warbattle._battle_combat_units(
            self.h.run, "war", "formation", "War.GetActiveBattles", 0, 0)
        self.assertEqual(units["status"], "partial")
        self.assertEqual(units["rows"][0]["name"], "Battalion 0")
        self.assertEqual(units["rows"][0]["name_raw"], "#bold Battalion 0#!")
        self.assertIn("partial", units["rows"][0]["unavailable"]["name"])
        page = units["native_pages"][0]
        self.assertIs(page["steps"], receipts)
        self.assertEqual(page["completed_steps"], 2)
        self.assertEqual(page["error"], 21)
        self.assertEqual(page["source_count"], 65)
        self.assertEqual(len(self.h.run.page_calls), 1)
        self.assertEqual(len(units["native_pages"]), 1)

    async def test_getunits_missing_typed_export_keeps_actual_bridge_error_and_base_observations(self):
        source = "BattleUnitType.GetUnits"
        for reason in ("export not found", "empty export"):
            with self.subTest(reason=reason):
                error = BridgeError("resolve_export", 127, reason=reason,
                                    symbol="PdxV3DataProjectTypedStepsInterface")
                self.h.run.page_exceptions[source] = error
                with self.h.patches():
                    result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
                battle = result["battles"][0]
                unit_type = battle["sides"]["attacker"]["participating_units"]["unit_types"]["rows"][0]
                units = unit_type["units"]
                self.assertEqual(units["status"], "unavailable")
                self.assertIsNone(units["rows"])
                self.assertIs(units["details"][0]["error"], error)
                self.assertEqual(unit_type["regular_units"], 5)
                self.assertEqual(battle["terrain"], "Mountain")
                self.assertIn("participating_combat_units", result["missing_required_outputs"])
                self.assertNotIn("participating_unit_types", result["missing_required_outputs"])
                self.assertFalse(any(name in ("BattleUnitType", "CombatUnit")
                                     for _, name in self.h.run.element_sources))

    async def test_getunits_pending_query_and_other_bridge_errors_propagate_without_replay(self):
        source = "BattleUnitType.GetUnits"
        for error in (
            BridgeError("read_process", 5, reason="access denied"),
            BridgeError("resolve_export", 5, reason="access denied"),
            BridgeError("resolve_export", 127, reason="export not found"),
        ):
            if error.details["reason"] == "export not found":
                error.query = object()
            self.h.run.page_exceptions[source] = error
            self.h.run.page_calls.clear()
            with self.h.patches(), self.assertRaises(BridgeError) as caught:
                await warbattle.battle_details(self.h.api, war_id=0x8100002A)
            self.assertIs(caught.exception, error)
            self.assertEqual(sum(call["source_expression"] == source for call in self.h.run.page_calls), 1)
            if hasattr(error, "query"):
                self.assertIs(caught.exception.query, error.query)

    async def test_actual_wire_deep_consumer_selects_typed_capability_and_preserves_real_cells(self):
        run = NativePageRun()
        roots = (data.ObjectBinding(91, 1001, retained=True),
                 data.ObjectBinding(92, 1002, retained=True))
        units = await warbattle._battle_combat_units(run, *roots, "War.GetActiveBattles", 3, 7)
        raw = bytes(run.process.write.call_args.args[1])
        header = data.PAGE_REQUEST.unpack_from(raw)
        suffix = data.PAGE_SCOPED_SUFFIX.unpack_from(raw, data.PAGE_REQUEST.size)
        self.assertEqual(run.process.resolve_export.call_args.args[1], "PdxV3DataProjectTypedStepsInterface")
        self.assertEqual((header[4], header[6], header[10]), (12, 1, 64))
        self.assertEqual(header[18], data.PAGE_SCOPED | data.PAGE_CAPTURE_STEP_ARGUMENTS)
        self.assertEqual(raw[header[7]:header[7] + header[8]], b"BattleUnitType.GetUnits")
        self.assertEqual((suffix[1], suffix[4]), (2, 0))
        step_arguments = []
        for index in range(suffix[1]):
            offset, count = data.PAGE_SPAN.unpack_from(raw, suffix[5] + index * data.PAGE_SPAN.size)
            arguments = []
            for slot in range(count):
                start, length = data.PAGE_SPAN.unpack_from(raw, offset + slot * data.PAGE_SPAN.size)
                arguments.append(raw[start:start + length])
            step_arguments.append(arguments)
        self.assertEqual(step_arguments, [[], [b"MilitaryFormation.Self"]])
        binding_offset, binding_count = data.PAGE_SPAN.unpack_from(raw, header[5])
        self.assertEqual(binding_count, 2)
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, binding_offset), (91, 1, 1001))
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw, binding_offset + data.BINDING_INPUT.size), (92, 1, 1002))
        row = units["rows"][0]
        self.assertEqual(row["name"], "Native Battalion")
        self.assertIs(row["conscript"], False)
        self.assertEqual(row["manpower"], 0)
        self.assertEqual(row["morale"], Decimal("0.00000"))
        self.assertEqual(row["building_id"], 0x81000077)
        page = units["native_pages"][0]["page"]
        self.assertIsInstance(page, data.ProjectionPage)
        self.assertEqual(page.value_handle, 0)
        self.assertEqual(tuple(step.binding_mode for step in page.steps), (1, 1))
        self.assertTrue(all(cell.binding_mode == 3 for cell in page.cells))
        self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                             for call in run.client.request.call_args_list), 1)

    async def test_actual_wire_missing_capability_never_allocates_submits_or_falls_back(self):
        run = NativePageRun()
        error = BridgeError("resolve_export", 127, reason="export not found")
        run.process.resolve_export.side_effect = error
        with self.assertRaises(BridgeError) as caught:
            await warbattle._battle_combat_units(run, data.ObjectBinding(91, 1001, retained=True),
                data.ObjectBinding(92, 1002, retained=True), "War.GetActiveBattles", 0, 0)
        self.assertIs(caught.exception, error)
        self.assertEqual(run.process.resolve_export.call_count, 1)
        self.assertEqual(run.process.resolve_export.call_args.args[1], "PdxV3DataProjectTypedStepsInterface")
        run.process.allocate.assert_not_called()
        run.client.request.assert_not_called()

    async def test_actual_wire_capture_failure_keeps_zero_unproven_count_and_prefix_receipts(self):
        run = NativePageRun()
        run.error, run.count_valid, run.source_count, run.completed_steps = 13, False, 0, 0
        units = await warbattle._battle_combat_units(run, data.ObjectBinding(91, 1001, retained=True),
            data.ObjectBinding(92, 1002, retained=True), "War.GetActiveBattles", 0, 0)
        self.assertEqual(units["status"], "partial")
        page = units["native_pages"][0]
        self.assertEqual((page["error"], page["count_valid"], page["completed_steps"]), (13, False, 0))
        self.assertTrue(all(not step.count_valid for step in page["steps"]))
        self.assertEqual(page["page"].value_handle, 0)
        self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                             for call in run.client.request.call_args_list), 1)

    async def test_actual_wire_partial_copy_keeps_original_query_and_resumes_copy_not_getters(self):
        run = NativePageRun()
        run.values = ("x" * 150000,) + run.values[1:]
        run.copy_fault = OSError("original CombatUnit copied-tail fault")
        with self.assertRaises(OSError) as caught:
            await warbattle._battle_combat_units(run, data.ObjectBinding(91, 1001, retained=True),
                data.ObjectBinding(92, 1002, retained=True), "War.GetActiveBattles", 0, 0)
        self.assertIs(caught.exception, run.copy_fault)
        self.assertIs(caught.exception.query, run.query)
        self.assertEqual(run.query.partial_result.completed_steps, 2)
        self.assertFalse(run.query.partial_result.cells[0].complete)
        run.process.free.assert_not_called()
        run.copy_fault = None
        completed = run.query.read()
        self.assertEqual(len(completed.cells[0].snapshot), 150000)
        self.assertEqual(completed.value_handle, 0)
        self.assertEqual(run.process.allocate.call_count, 1)
        self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                             for call in run.client.request.call_args_list), 1)

    async def test_combat_unit_nonadvancing_page_keeps_count_but_is_partial_without_retry(self):
        self.h.run.page_source_counts["BattleUnitType.GetUnits"] = 3
        self.h.run.page_row_limits["BattleUnitType.GetUnits"] = 0
        units = await warbattle._battle_combat_units(
            self.h.run, "war", "formation", "War.GetActiveBattles", 0, 0)
        self.assertEqual(units["status"], "partial")
        self.assertEqual(units["rows"], [])
        self.assertEqual(units["native_pages"][0]["source_count"], 3)
        self.assertTrue(units["native_pages"][0]["count_valid"])
        self.assertIn("did not advance", units["details"][0]["reason"])
        self.assertEqual(len(self.h.run.page_calls), 1)

    async def test_projection_field_budget_rejects_33_before_native_page(self):
        with self.assertRaisesRegex(ValueError, "at most 32"):
            await warbattle._paged_rows(self.h.run,
                fields=(("CombatUnit.GetName", "name", True),) * 33,
                bindings=("war", "formation"), source_expression="BattleUnitType.GetUnits",
                steps=(data.CreationStep("War.GetActiveBattles", 0),))
        self.assertFalse(self.h.run.page_calls)

    async def test_actual_wire_none_zero_and_false_leaves_are_not_coalesced(self):
        run = NativePageRun()
        values = list(run.values)
        values[2], values[7], values[9], values[10] = 0, 0, None, 0
        run.values = tuple(values)
        units = await warbattle._battle_combat_units(run, data.ObjectBinding(91, 1001, retained=True),
            data.ObjectBinding(92, 1002, retained=True), "War.GetActiveBattles", 0, 0)
        row = units["rows"][0]
        self.assertEqual(units["status"], "observed")
        self.assertIs(row["conscript"], False)
        self.assertEqual(row["manpower"], 0)
        self.assertEqual(row["veterancy_level"], 0)
        self.assertIsNone(row["casualties"])
        self.assertEqual(row["building_id_raw"], 0)
        self.assertEqual(row["building_id"], 0)
        self.assertEqual(row["unavailable"], {})

    async def test_missing_typed_preflight_keeps_scalar_sources_and_never_calls_getunits_page(self):
        self.h.run.typed_page_available_value = False
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        battle = result["battles"][0]
        unit_type = battle["sides"]["attacker"]["participating_units"]["unit_types"]["rows"][0]
        self.assertEqual(unit_type["regular_units"], 5)
        self.assertEqual(unit_type["units"]["status"], "unavailable")
        self.assertIsNone(unit_type["units"]["rows"])
        self.assertEqual(unit_type["units"]["native_pages"], [])
        self.assertIn("was not submitted", unit_type["units"]["details"][0]["reason"])
        self.assertEqual(battle["terrain"], "Mountain")
        self.assertIn("participating_combat_units", result["missing_required_outputs"])
        self.assertNotIn("participating_unit_types", result["missing_required_outputs"])
        self.assertNotIn("BattleUnitType.GetUnits", [call["source_expression"] for call in self.h.run.page_calls])
        self.assertTrue(any(self.h.run.page_availability_calls))

    async def test_real_warfare_run_missing_typed_preflight_avoids_capture_and_unknown_owner(self):
        error = BridgeError("resolve_export", 127, reason="export not found")
        process = Mock(pid=1)
        process.resolve_export.side_effect = error
        blocking = AsyncMock(side_effect=lambda function, *args: function(*args))
        ctx = SimpleNamespace(native=object(), process=process, adapters={"data": 0xA000},
                              world=77, diagnostics=[], blocking=blocking, query=AsyncMock())
        api = API()
        api._ctx = ctx
        run = warbattle.WarfareRead(api, "test.deep_missing_typed")
        with patch.object(data, "project_page") as factory:
            first = await warbattle._battle_combat_units(run,
                data.ObjectBinding(91, 1001, retained=True), data.ObjectBinding(92, 1002, retained=True),
                "War.GetActiveBattles", 0, 0)
            second = await warbattle._battle_combat_units(run,
                data.ObjectBinding(91, 1001, retained=True), data.ObjectBinding(92, 1002, retained=True),
                "War.GetActiveBattles", 0, 1)
        self.assertEqual((first["status"], second["status"]), ("unavailable", "unavailable"))
        self.assertIsNone(first["rows"])
        factory.assert_not_called()
        ctx.query.assert_not_awaited()
        process.allocate.assert_not_called()
        process.resolve_export.assert_called_once()
        self.assertEqual(process.resolve_export.call_args.args, (0xA000, "PdxV3DataProjectTypedStepsInterface"))
        self.assertFalse(getattr(run, "retain_unknown", False))
        self.assertFalse(run.values)
        self.assertFalse(run.operations)
        await run.cleanup()
        self.assertEqual(run.status, "complete")
        self.assertNotIn(run, ctx.owner_pool.snapshot())

    async def test_old_page_adapter_keeps_base_scalars_and_reports_deep_partial(self):
        self.h.run.page_available_value = False
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        battle = result["battles"][0]
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["page_projection_available"])
        self.assertEqual(battle["terrain"], "Mountain")
        self.assertIsNone(battle["culture_casualty_statistics"])
        self.assertIsNone(battle["sides"]["attacker"]["participating_units"]["unit_types"])
        self.assertIn("PdxV3DataProjectPageInterface",
                      battle["unavailable"]["culture_casualty_statistics"])
        self.assertFalse(self.h.run.page_calls)

    async def test_page_native_error_world_and_copied_values_are_preserved(self):
        self.h.run.page_errors["Battle.GetAttackerStatistics"] = 21
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        statistics = result["battles"][0]["culture_casualty_statistics"]["attacker"]
        self.assertEqual(statistics["status"], "partial")
        self.assertEqual(statistics["rows"][0]["dead"], 100)
        observation = statistics["native_pages"][0]
        self.assertEqual(observation["error"], 21)
        self.assertEqual(observation["world"], 77)
        self.assertEqual(observation["page"].error, 21)
        self.assertEqual(statistics["details"][0]["native_error"], 21)
        self.assertIn("culture_casualty_statistics", result["missing_required_outputs"])

    async def test_deep_page_partial_cell_keeps_copied_value_and_reason(self):
        field = "CultureCasualtyStatistics.GetCulture.GetName"
        self.h.run.page_cell_reasons[("Battle.GetAttackerStatistics", 0, field)] = (
            "native string copy is incomplete (10/20 bytes)"
        )
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        statistics = result["battles"][0]["culture_casualty_statistics"]["attacker"]
        row = statistics["rows"][0]
        self.assertEqual(statistics["status"], "partial")
        self.assertEqual(row["culture_name"], "Attacker Culture 0")
        self.assertEqual(row["culture_name_raw"], "#bold Attacker Culture 0#!")
        self.assertIn("incomplete", row["unavailable"]["culture_name"])

    async def test_deep_page_paginates_with_native_count_and_start(self):
        self.h.run.page_source_counts["Battle.GetAttackerStatistics"] = 2
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        statistics = result["battles"][0]["culture_casualty_statistics"]["attacker"]
        self.assertEqual([row["source_index"] for row in statistics["rows"]], [0, 1])
        self.assertEqual([page["start"] for page in statistics["native_pages"]], [0, 1])
        self.assertEqual([page["source_count"] for page in statistics["native_pages"]], [2, 2])
        calls = [call for call in self.h.run.page_calls
                 if call["source_expression"] == "Battle.GetAttackerStatistics"]
        self.assertEqual([call["start"] for call in calls], [0, 1])
        self.assertTrue(all(call["limit"] == 64 for call in calls))

    async def test_partial_native_text_is_kept_with_its_original_error(self):
        self.h.copy_errors["Battle.GetTerrainDesc"] = (
            "partial terrain", "Battle.GetTerrainDesc: native error 122; native text snapshot is partial"
        )
        with self.h.patches():
            result = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
        battle = result["battles"][0]
        self.assertEqual(battle["terrain_description"], "partial terrain")
        self.assertIn("native error 122", battle["unavailable"]["terrain_description"])

    async def test_history_uses_ended_source_and_projects_real_outcome(self):
        with self.h.patches():
            result = await warbattle.battle_history(self.h.api, war_id=0x8100002A)
        battle = result["battles"][0]
        self.assertEqual(result["feature_ids"], ["war.battle-history"])
        self.assertEqual(battle["source"], "War.GetEndedBattles")
        self.assertEqual(battle["winner_country_id"], 0x81000011)
        self.assertEqual(battle["loser_country_name"], "Defender")
        self.assertEqual(battle["end_date"], "9 January 1900")
        self.assertEqual(battle["captured_provinces"], 7)
        self.assertNotIn("War.GetActiveBattles", [source for source, _ in self.h.count_calls])

    async def test_front_situation_uses_canonical_sides_and_actual_rosters(self):
        with self.h.patches():
            result = await warbattle.front_situations(self.h.api, war_id=0x8100002A)
        front = result["fronts"][0]
        self.assertEqual(front["id"], 0x81000033)
        self.assertEqual(front["name"], "Tibet Front")
        self.assertEqual(front["name_raw"], "#bold Tibet Front#!")
        self.assertEqual(front["sides"]["initiator"]["canonical_role"], "initiator")
        self.assertEqual(front["sides"]["target"]["canonical_role"], "target")
        self.assertEqual(front["sides"]["initiator"]["countries"][0]["name"], "Front Country")
        self.assertEqual(front["sides"]["target"]["formations"][0]["name"], "Front Army")
        sources = [source for source, _ in self.h.count_calls]
        self.assertIn("Front.GetInitiatorSide.GetCountries", sources)
        self.assertIn("Front.GetTargetSide.GetArmies", sources)
        self.assertIn("Front.AccessActiveBattles", sources)
        self.assertFalse(any("GetLeftSide" in call or "GetRightSide" in call
                             for call in self.h.run.field_calls))

    async def test_exhaustion_projects_battle_and_attrition_causes_in_mode1(self):
        with self.h.patches():
            result = await warbattle.war_exhaustion(self.h.api, war_id=0x8100002A)
        participants = result["wars"][0]["participants"]
        self.assertEqual(result["feature_ids"], ["war.war-exhaustion"])
        self.assertEqual([row["side"] for row in participants], ["attacker", "defender"])
        self.assertEqual(participants[0]["battle_dead"], 1234)
        self.assertEqual(participants[0]["attrition_wounded"], 1234)
        self.assertEqual(participants[1]["country_name"], "Defender")
        self.assertFalse(any(name == "WarParticipant" for _, name in self.h.run.element_sources))
        participant_calls = [call for call in self.h.copy_calls if call[0] in
                             ("War.GetAttackers", "War.GetDefenders")]
        self.assertTrue(participant_calls)
        self.assertTrue(all(call[3].startswith("WarParticipant.") for call in participant_calls))

    async def test_state_occupation_keeps_native_shares_and_state_text(self):
        with self.h.patches():
            result = await warbattle.state_occupation(self.h.api)
        state = result["states"][0]
        self.assertEqual(result["feature_ids"], ["war.occupation"])
        self.assertEqual(state["name"], "Lhasa")
        self.assertEqual(state["name_raw"], "#bold Lhasa#!")
        self.assertEqual(state["occupant_country_id"], 0x81000011)
        share = state["occupation_shares"][0]
        self.assertEqual(share["country_name"], "Occupier")
        self.assertEqual(share["occupation_fraction"], Decimal("0.50000"))
        self.assertIn(("Country", "AccessStates", 0), self.h.run.providers)
        self.assertIn(("State", "GetOccupationPiechartData", 0), self.h.run.providers)

    async def test_promote_only_intermediates_are_not_required_as_function_providers(self):
        self.h.run.forbidden_providers.update({
            ("Battle", "GetAttackerSide", 0),
            ("Battle", "GetDefenderSide", 0),
            ("Battle", "GetAttackerCondition", 0),
            ("Battle", "GetDefenderCondition", 0),
            ("BattleSide", "GetCountry", 0),
            ("BattleSide", "GetCommander", 0),
            ("BattleSide", "GetFormation", 0),
            ("Front", "GetInitiatorSide", 0),
            ("Front", "GetTargetSide", 0),
            ("FrontSide", "GetCountry", 0),
            ("OccupationPiechartData", "GetCountry", 0),
        })
        with self.h.patches():
            battle = await warbattle.battle_details(self.h.api, war_id=0x8100002A)
            front = await warbattle.front_situations(self.h.api, war_id=0x8100002A)
            states = await warbattle.state_occupation(self.h.api)
        self.assertEqual(battle["status"], "observed")
        self.assertEqual(len(front["fronts"]), 1)
        self.assertEqual(len(states["states"]), 1)

    async def test_missing_optional_state_relation_keeps_primary_state(self):
        self.h.run.forbidden_providers.update({
            ("State", "GetOccupant", 0),
            ("State", "GetEnemyWithHighestOccupation", 0),
        })
        self.h.run.field_errors["State.GetOccupant.GetID"] = (
            "State.GetOccupant.GetID: native relation unavailable"
        )
        with self.h.patches():
            result = await warbattle.state_occupation(self.h.api)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(len(result["states"]), 1)
        state = result["states"][0]
        self.assertEqual(state["name"], "Lhasa")
        self.assertIsNone(state["occupant_country_id"])
        self.assertIn("relation unavailable", state["unavailable"]["occupant_country_id"])
        self.assertEqual(state["highest_occupation_country_id"], 0x81000022)
        self.assertNotIn(("State", "GetOccupant", 0), self.h.run.providers)


if __name__ == "__main__":
    unittest.main()
