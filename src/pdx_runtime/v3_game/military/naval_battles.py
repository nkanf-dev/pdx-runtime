"""Original naval-battle observations, including both sides and combat ships.

The fleet, NavalBattle, template-group and CombatShip Creations stay inside
native scoped Page callbacks. Only copied scalar cells escape. Source indices
are observation coordinates, never persistent IDs or command targets.

Public entry: ``await naval_battles(game.military, fleet_id=None)``. The usual
MilitaryAPI facade can delegate directly to this function without another
perform/refresh wrapper. This read never pauses or generates game commands.
"""
from __future__ import annotations

from ... import v3_data as data
from ._warfare import KnownResultError, SourceUnavailable, owned_read
from .battles import _paged_rows, _missing_page
from .._run import KnownResultError, SourceUnavailable


_FLEETS = "Country.GetMilitaryFormationsFleet"
_BATTLES = "MilitaryFormation.GetActiveNavalBattles"
_FLEET_FIELDS = (
    ("MilitaryFormation.GetIDString", "fleet_id", False),
    ("MilitaryFormation.GetNameNoFormatting", "name", True),
)
_BATTLE_FIELDS = (
    ("NavalBattle.GetName", "name", True),
    ("NavalBattle.GetTooltipTag", "tooltip_tag", False),
    ("NavalBattle.GetLocationName", "location", False),
    ("NavalBattle.GetStatusString", "battle_status", False),
    ("NavalBattle.IsActive", "active", False),
    ("NavalBattle.HasEnded", "ended", False),
    ("NavalBattle.GetDurationInDaysRaw", "duration_days", False),
    ("NavalBattle.GetTurnNumber", "turn_number", False),
    ("NavalBattle.GetDateDesc", "date_description", False),
    ("NavalBattle.GetTerrain", "terrain", False),
    ("NavalBattle.GetTerrainDesc", "terrain_description", False),
    ("NavalBattle.HasInvasion", "has_invasion", False),
    ("NavalBattle.EndedInconclusively", "inconclusive", False),
    ("NavalBattle.CalcCurrentAttackerPowerProjection", "attacker_power", False),
    ("NavalBattle.CalcCurrentDefenderPowerProjection", "defender_power", False),
    ("NavalBattle.GetAttackerLostShips", "attacker_lost_ships", False),
    ("NavalBattle.GetDefenderLostShips", "defender_lost_ships", False),
    ("NavalBattle.GetBattleCondition.IsValid", "condition_valid", False),
    ("NavalBattle.GetBattleCondition.GetNameNoFormatting", "condition_name", True),
    ("NavalBattle.GetBattleCondition.GetDesc", "condition_description", False),
    ("NavalBattle.GetBattleCondition.GetModifierDesc", "condition_modifiers", False),
)
_SIDE_FIELDS = (
    ("GetCountry.GetID", "country_id", False),
    ("GetCountry.GetNameNoFormatting", "country_name", True),
    ("GetCommander.IsValid", "commander_valid", False),
    ("GetCommander.GetID", "commander_id", False),
    ("GetCommander.GetFullNameNoFormatting", "commander_name", True),
    ("GetFormation.IsValid", "formation_valid", False),
    ("GetFormation.GetIDString", "formation_id", False),
    ("GetFormation.GetNameNoFormatting", "formation_name", True),
    ("GetNumShips", "ships", False),
    ("GetStartingNumShips", "starting_ships", False),
    ("GetNumShipsSunk", "sunk_ships", False),
    ("GetNumShipsWithdrawn", "withdrawn_ships", False),
    ("GetTotalCrew", "crew", False),
    ("GetStartingCrew", "starting_crew", False),
    ("GetMaxCrew", "max_crew", False),
    ("GetTotalHull", "hull", False),
    ("GetStartingHull", "starting_hull", False),
    ("GetMaxHull", "max_hull", False),
    ("GetHullDamageDone", "hull_damage_done", False),
    ("GetHullDamageTaken", "hull_damage_taken", False),
    ("GetCrewDamageDone", "crew_damage_done", False),
    ("GetCrewDamageTaken", "crew_damage_taken", False),
    ("GetNumSuccessfulAttacks", "successful_attacks", False),
    ("GetNumMissedAttacks", "missed_attacks", False),
    ("GetNumFailedWithdrawals", "failed_withdrawals", False),
    ("GetStatus", "status_text", False),
    ("GetStatusDesc", "status_description", False),
    ("GetOutcome", "outcome", False),
    ("HasWon", "won", False),
    ("HasLost", "lost", False),
)
_GROUP_FIELDS = (
    ("NavalBattleShipTemplateGroup.GetIDString", "group_key", False),
    ("NavalBattleShipTemplateGroup.GetTemplate.GetNameNoFormatting", "template_name", True),
    ("NavalBattleShipTemplateGroup.GetTemplate.GetType.GetNameNoFormatting", "ship_type_name", True),
    ("NavalBattleShipTemplateGroup.GetNumTotalShips", "total_ships", False),
    ("NavalBattleShipTemplateGroup.GetNumActiveShips", "active_ships", False),
    ("NavalBattleShipTemplateGroup.GetNumSunkShips", "sunk_ships", False),
    ("NavalBattleShipTemplateGroup.GetNumWithdrawnShips", "withdrawn_ships", False),
)
_SHIP_FIELDS = (
    ("CombatShip.GetShip.GetNameNoFormatting", "name", True),
    ("CombatShip.GetTooltipTag", "combat_tooltip_tag", False),
    ("CombatShip.GetShip.GetTooltipTag", "ship_tooltip_tag", False),
    ("CombatShip.GetStatus", "combat_status", False),
    ("CombatShip.IsValid", "valid", False),
    ("CombatShip.IsActive", "active", False),
    ("CombatShip.IsSunk", "sunk", False),
    ("CombatShip.IsWithdrawn", "withdrawn", False),
    ("CombatShip.GetCrewCurrent", "crew", False),
    ("CombatShip.GetCrewPercentage", "crew_fraction", False),
    ("CombatShip.GetHitPoints", "hull", False),
    ("CombatShip.GetHitPointsPercentage", "hull_fraction", False),
    ("CombatShip.GetCurrentReadiness", "readiness", False),
    ("CombatShip.GetInitiative", "initiative", False),
)


def _identity(api, row, key, *, valid_key=None):
    raw = row.get(key)
    row[key + "_raw"] = raw
    row[key] = api._native_full_id(raw)
    if row[key] is None and not (valid_key and row.get(valid_key) is False):
        row["unavailable"].setdefault(key, "Original scalar did not contain a complete native ID")


def _source_count(page_report):
    pages = page_report["native_pages"]
    return pages[0]["source_count"] if pages and pages[0]["count_valid"] else None


def _degraded(report):
    return report["status"] != "observed"


async def _read_page(run, *, fields, country, source, steps=()):
    try:
        return await _paged_rows(run, fields=fields, bindings=(country,),
            source_expression=source, steps=steps)
    except (SourceUnavailable, KnownResultError, LookupError) as error:
        return _missing_page(source, error)


async def _side(run, country, fleet_step, battles, role):
    """Project one side for all battles in one bounded source traversal."""
    prefix = "NavalBattle.Get" + role.title() + "Side."
    result = await _read_page(run, fields=tuple(
        (prefix + method, key, display) for method, key, display in _SIDE_FIELDS),
        country=country, source=_BATTLES, steps=(fleet_step,))
    for row in result["rows"] or ():
        _identity(run.api, row, "country_id")
        _identity(run.api, row, "commander_id", valid_key="commander_valid")
        _identity(run.api, row, "formation_id", valid_key="formation_valid")
    if any(row["unavailable"] for row in result["rows"] or ()):
        result["status"] = "partial"
    # These are fresh non-atomic pages. A changed count is not silently joined
    # to the earlier battle roster or retried behind the caller's back.
    observed_count = _source_count(result)
    prior_count = _source_count(battles)
    result["joinable_to_roster"] = True
    if observed_count is not None and prior_count is not None and observed_count != prior_count:
        result["joinable_to_roster"] = False
        result["status"] = "partial"
        result["details"].append({"reason": "Naval battle source count changed between observations",
            "roster_count": prior_count, "side_count": observed_count})
    return result


async def _observe(api, fleet_id, include_ships, run):
    if not await run.page_available():
        raise SourceUnavailable("Native scoped Page projection is required for naval-battle Creations")
    from ._warfare import _source_country
    country = await _source_country(run)
    fleets = await _read_page(run, fields=_FLEET_FIELDS, country=country, source=_FLEETS)
    observations, details, found = [], [], False
    for fleet in fleets["rows"] or ():
        _identity(api, fleet, "fleet_id")
        if fleet_id is not None and fleet["fleet_id"] != fleet_id:
            continue
        found = True
        fleet_step = data.CreationStep(_FLEETS, fleet["source_index"], _source_count(fleets))
        battles = await _read_page(run, fields=_BATTLE_FIELDS, country=country,
            source=_BATTLES, steps=(fleet_step,))
        side_reports = {}
        if battles["rows"]:
            for role in ("attacker", "defender"):
                side_reports[role] = await _side(run, country, fleet_step, battles, role)
        side_rows = {role: {row["source_index"]: row for row in report["rows"] or ()}
                     if report["joinable_to_roster"] else {}
                     for role, report in side_reports.items()}
        for battle in battles["rows"] or ():
            index = battle["source_index"]
            battle.update(source_fleet_id=fleet["fleet_id"], atomic_snapshot=False,
                source_scoped_identity={"fleet_source_index": fleet["source_index"],
                    "battle_source_index": index, "stable_game_identity": False},
                id=None, id_unavailable_reason="No native persistent NavalBattle ID getter is exposed")
            battle["sides"] = {}
            for role in ("attacker", "defender"):
                side = side_rows[role].get(index)
                if side is None:
                    side = {"unavailable": {"side": "The native side page did not copy this battle row"}}
                side["ship_groups"] = None
                if include_ships:
                    group_source = "NavalBattle.Get" + role.title() + "Side.GetShipTemplateGroups"
                    steps = (fleet_step, data.CreationStep(_BATTLES, index, _source_count(battles)))
                    groups = await _read_page(run, fields=_GROUP_FIELDS, country=country,
                        source=group_source, steps=steps)
                    for group in groups["rows"] or ():
                        ships = await _read_page(run, fields=_SHIP_FIELDS, country=country,
                            source="NavalBattleShipTemplateGroup.GetShips", steps=steps + (
                                data.CreationStep(group_source, group["source_index"], _source_count(groups)),))
                        for ship in ships["rows"] or ():
                            ship["source_scoped_identity"] = {
                                "fleet_source_index": fleet["source_index"],
                                "battle_source_index": index, "side": role,
                                "group_source_index": group["source_index"],
                                "ship_source_index": ship["source_index"], "stable_game_identity": False}
                        group["combat_ships"] = ships
                        if _degraded(ships):
                            group["unavailable"]["combat_ships"] = "Native combat-ship page is partial or unavailable"
                    if any(group["unavailable"] for group in groups["rows"] or ()):
                        groups["status"] = "partial"
                    side["ship_groups"] = groups
                    if _degraded(groups):
                        side["unavailable"]["ship_groups"] = "Native ship-group page is partial or unavailable"
                if side["unavailable"] or _degraded(side_reports[role]):
                    battle["unavailable"][role] = "Inspect copied side fields and original page results"
                battle["sides"][role] = side
            battle["status"] = "partial" if battle["unavailable"] else "observed"
            observations.append(battle)
        details.append({"fleet": fleet, "battles": battles, "side_pages": side_reports})
    partial = (_degraded(fleets) or any(fleet["unavailable"] for fleet in fleets["rows"] or ())
        or any(_degraded(item["battles"]) for item in details)
        or any(row["status"] != "observed" for row in observations))
    status = "partial" if partial else "observed"
    if fleet_id is not None and not found:
        status = "unavailable" if partial else "not_found"
    from ._warfare import _source_country_id
    return {"status": status, "country_id": _source_country_id(run), "actor_country_id": run.actor,
        "requested_fleet_id": fleet_id,
        "battles": observations, "fleets": fleets, "sources": details,
        "feature_ids": ["war.naval-battle"], "atomic_snapshot": False,
        "coverage": "active_selected_country_fleet_naval_battles" if include_ships else "active_selected_country_fleet_naval_battle_summaries",
        "includes_combat_ships": include_ships,
        "identity_scope": "source observations; a battle can appear under multiple owned fleets; no name/tooltip deduplication",
        "war_filter": "Not inferred: the original fleet battle source does not expose a proved War relation"}


async def naval_battles(api, *, fleet_id=None, include_ships=True, perform=True):
    """Read active naval battles of all owned fleets or one explicit fleet.

    Includes native conditions, both sides, hull/crew losses, sunk/withdrawn
    ships, ship-template groups and per-CombatShip state. ``include_ships=False``
    requests a smaller summary, not a failed full read. Returned indices and
    tooltip text are not persistent identities or reusable command targets.
    """
    if fleet_id is not None:
        api._id("fleet_id", fleet_id)
    if type(include_ships) is not bool:
        raise TypeError("include_ships must be a boolean")
    async def read():
        if perform:
            await api._ctx.refresh()
        async def observe(run):
            return await _observe(api, fleet_id, include_ships, run)
        return await owned_read(api, "military.naval_battles", observe)
    return await api._ctx.perform("military.naval_battles", read) if perform else await read()
