"""Actual V3 land-battle, front, occupation, and exhaustion observations.

The original WarParticipant and Battle Creation rows are inline native values.
They never leave their producer callback.  Only copied scalar leaves are public.
Front and State roots use their original owning sources.  A source index is an
observation-local coordinate, never a Battle identity or an action target.
"""
from __future__ import annotations

from ... import v3_data as data
from ...native_bridge import BridgeError
from .._text import display_field
from ._warfare import (
    WarfareRead,
    SourceUnavailable,
    _copied,
    _source_count,
    _war,
    owned_read,
)
from .._values import fixed_decimal
from .._run import KnownResultError, SourceUnavailable
from .. import _run


_ID_KINDS = (data.Kind.I32, data.Kind.U32)
_TEXT = (data.Kind.STRING,)
_FIXED = (data.Kind.FIXED,)


async def _metadata(run, entries):
    """Validate only the scalar/source leaf of an original VM expression chain.

    Intermediate relation getters can be Promote-only and absent from the
    Function enumeration. The VM resolves those borrowed relations inside the
    same callback; requiring them as Function providers would reject a valid
    scalar chain without adding lifetime safety.
    """
    entries = tuple(entries)
    if entries:
        named_type, method, arguments = entries[-1]
        await run.provider(named_type, method, arguments)


async def _inline(run, source, index, count, bindings, expression, kinds, metadata=()):
    try:
        await _metadata(run, metadata)
    except (SourceUnavailable, LookupError) as error:
        return None, str(error)
    return await _copied(run, source, index, count, expression, bindings, kinds)


def _unavailable(row, key, reason):
    if reason:
        row.setdefault("unavailable", {})[key] = reason


def _native_id(run, row, key, value, reason, expression):
    row[key + "_raw"] = value
    row[key] = run.api._native_full_id(value)
    if row[key] is None and not reason:
        reason = expression + ": original getter did not return a complete native ID"
    _unavailable(row, key, reason)


async def _inline_field(run, row, *, source, index, count, bindings, expression,
                        key, kinds, metadata=(), identity=False, display=False):
    value, reason = await _inline(
        run, source, index, count, bindings, expression, kinds, metadata
    )
    if identity:
        _native_id(run, row, key, value, reason, expression)
    elif display:
        row.update(display_field(value, key))
        _unavailable(row, key, reason)
    else:
        row[key] = value
        _unavailable(row, key, reason)
    return value


async def _wars(run: WarfareRead, wanted):
    """Yield owned War bindings.  A requested ID uses the established _war seam."""
    if wanted is not None:
        _, binding = await _war(run, wanted)
        yield wanted, binding
        return
    country = await run.country()
    await run.provider("Country", "AccessWars")
    async for _, element, binding in run.elements("Country.AccessWars", (country,), "War"):
        yield await run.origin(element, "War"), binding


def _source_locator(war_id, source, index):
    return {
        "war_id": war_id,
        "source": source,
        "source_index": index,
        "stable_game_identity": False,
    }


async def _battle_condition(run, source, index, count, war, role):
    title = role.title()
    prefix = "Battle.Get" + title + "Condition"
    row = {"role": role, "unavailable": {}}
    valid = await _inline_field(
        run, row, source=source, index=index, count=count, bindings=(war,),
        expression=prefix + ".IsValid", key="valid", kinds=(data.Kind.BOOL,),
        metadata=(("Battle", "Get" + title + "Condition", 0),
                  ("BattleCondition", "IsValid", 0)),
    )
    for method, key, display in (
        ("GetNameNoFormatting", "name", True),
        ("GetDesc", "description", False),
        ("GetModifierDesc", "modifier_description", False),
        ("GetTooltipTag", "tooltip_tag", False),
    ):
        await _inline_field(
            run, row, source=source, index=index, count=count, bindings=(war,),
            expression=prefix + "." + method, key=key, kinds=_TEXT, display=display,
            metadata=(("Battle", "Get" + title + "Condition", 0),
                      ("BattleCondition", method, 0)),
        )
    if valid is False:
        row["reason"] = "The original BattleCondition relation was invalid in this observation"
    return row


async def _battle_side(run, source, index, count, war, role):
    title = role.title()
    prefix = "Battle.Get" + title + "Side"
    row = {"role": role, "unavailable": {}}
    base = (("Battle", "Get" + title + "Side", 0),)
    for method, key, kinds in (
        ("GetNumUnits", "unit_count", (data.Kind.I32,)),
        ("GetNumUnitsDesc", "unit_count_description", _TEXT),
        ("GetNumUnitsAtStartOfBattleDesc", "starting_units_description", _TEXT),
        ("GetNumUnitsAtEndOfBattleDesc", "ending_units_description", _TEXT),
        ("GetStatus", "status", _TEXT),
        ("GetStatusDesc", "status_description", _TEXT),
        ("GetOutcome", "outcome", _TEXT),
        ("HasEscaped", "escaped", (data.Kind.BOOL,)),
        ("HasLost", "lost", (data.Kind.BOOL,)),
        ("HasWon", "won", (data.Kind.BOOL,)),
        ("IsAttacker", "native_is_attacker", (data.Kind.BOOL,)),
        ("IsDefender", "native_is_defender", (data.Kind.BOOL,)),
    ):
        await _inline_field(
            run, row, source=source, index=index, count=count, bindings=(war,),
            expression=prefix + "." + method, key=key, kinds=kinds,
            metadata=base + (("BattleSide", method, 0),),
        )

    await _inline_field(
        run, row, source=source, index=index, count=count, bindings=(war,),
        expression=prefix + ".GetCountry.GetID", key="country_id", kinds=_ID_KINDS,
        identity=True,
        metadata=base + (("BattleSide", "GetCountry", 0), ("Country", "GetID", 0)),
    )
    await _inline_field(
        run, row, source=source, index=index, count=count, bindings=(war,),
        expression=prefix + ".GetCountry.GetNameNoFormatting", key="country_name", kinds=_TEXT,
        display=True,
        metadata=base + (("BattleSide", "GetCountry", 0),
                         ("Country", "GetNameNoFormatting", 0)),
    )
    await _inline_field(
        run, row, source=source, index=index, count=count, bindings=(war,),
        expression=prefix + ".GetCommander.GetID", key="commander_id", kinds=_ID_KINDS,
        identity=True,
        metadata=base + (("BattleSide", "GetCommander", 0), ("Character", "GetID", 0)),
    )
    await _inline_field(
        run, row, source=source, index=index, count=count, bindings=(war,),
        expression=prefix + ".GetCommander.GetFullNameNoFormatting", key="commander_name",
        kinds=_TEXT, display=True,
        metadata=base + (("BattleSide", "GetCommander", 0),
                         ("Character", "GetFullNameNoFormatting", 0)),
    )
    await _inline_field(
        run, row, source=source, index=index, count=count, bindings=(war,),
        expression=prefix + ".GetFormation.GetIDString", key="formation_id", kinds=_TEXT,
        identity=True,
        metadata=base + (("BattleSide", "GetFormation", 0),
                         ("MilitaryFormation", "GetIDString", 0)),
    )
    await _inline_field(
        run, row, source=source, index=index, count=count, bindings=(war,),
        expression=prefix + ".GetFormation.GetNameNoFormatting", key="formation_name",
        kinds=_TEXT, display=True,
        metadata=base + (("BattleSide", "GetFormation", 0),
                         ("MilitaryFormation", "GetNameNoFormatting", 0)),
    )

    # These are real no-argument scalar chains below BattleSide.GetFormation.
    # They describe the commanded formation's current state. They are not
    # relabelled as an exact roster of the formation's units in this Battle.
    formation_state = {
        "formation_id": row.get("formation_id"),
        "name": row.get("formation_name"),
        "name_raw": row.get("formation_name_raw"),
        "unavailable": {},
    }
    formation_base = base + (("BattleSide", "GetFormation", 0),)
    for method, key, kinds in (
        ("GetNumCombatUnits", "combat_units", (data.Kind.I32,)),
        ("GetCurrentManpower", "current_manpower", (data.Kind.I64,)),
        ("GetMaxManpower", "max_manpower", (data.Kind.I64,)),
        ("GetOrganizationPercentage", "organization", _FIXED),
        ("IsInBattle", "in_battle", (data.Kind.BOOL,)),
    ):
        await _inline_field(
            run, formation_state, source=source, index=index, count=count, bindings=(war,),
            expression=prefix + ".GetFormation." + method, key=key, kinds=kinds,
            metadata=formation_base + (("MilitaryFormation", method, 0),),
        )
    row["commanded_formation_current_state"] = formation_state

    unit_summary = {"unavailable": {}}
    for method, key, kinds in (
        ("Get" + title + "LostUnits", "lost_units", (data.Kind.I32,)),
        ("Get" + title + "NumWantedUnitsDesc", "wanted_units_description", _TEXT),
    ):
        await _inline_field(
            run, unit_summary, source=source, index=index, count=count, bindings=(war,),
            expression="Battle." + method, key=key, kinds=kinds,
            metadata=(("Battle", method, 0),),
        )
    row["participating_units"] = {
        "coverage": "native_side_aggregate_and_commanded_and_borrowed_formations",
        "under_command_formation_id": row.get("formation_id"),
        "native_side_unit_count": row.get("unit_count"),
        "starting_units_description": row.get("starting_units_description"),
        "ending_units_description": row.get("ending_units_description"),
        **unit_summary,
        "borrowed_formations": None,
        "unit_types": None,
        "unavailable": {
            **unit_summary["unavailable"],
            "borrowed_formations": "The original borrowed-formation source has not been read",
            "unit_types": "The original commanded-formation unit-type source has not been read",
        },
    }
    return row


_CULTURE_STAT_FIELDS = (
    ("CultureCasualtyStatistics.GetCulture.GetName", "culture_name", True),
    ("CultureCasualtyStatistics.GetDead", "dead", False),
    ("CultureCasualtyStatistics.GetWounded", "wounded", False),
    ("CultureCasualtyStatistics.GetDemoralized", "demoralized", False),
)

_BORROWED_FORMATION_FIELDS = (
    ("MilitaryFormation.GetIDString", "formation_id", False),
    ("MilitaryFormation.GetNameNoFormatting", "name", True),
    ("MilitaryFormation.GetCountry.GetID", "country_id", False),
)

_BATTLE_UNIT_TYPE_FIELDS = (
    ("BattleUnitType.GetCombatUnitType.GetName", "unit_type_name", True),
    ("BattleUnitType.GetNumberOfRegularUnits", "regular_units", False),
    ("BattleUnitType.GetNumberOfConscriptUnits", "conscript_units", False),
    ("BattleUnitType.GetAverageMorale", "average_morale", False),
)


_BATTLE_COMBAT_UNIT_FIELDS = (
    ("CombatUnit.GetName", "name", True),
    ("CombatUnit.IsConscript", "conscript", False),
    ("CombatUnit.GetManpowerRaw", "manpower", False),
    ("CombatUnit.GetMaxManpower", "max_manpower", False),
    ("CombatUnit.GetMoraleRaw", "morale", False),
    ("CombatUnit.GetOffenseRaw", "offense", False),
    ("CombatUnit.GetDefenseRaw", "defense", False),
    ("CombatUnit.GetCurrentVeterancyLevel", "veterancy_level", False),
    ("CombatUnit.GetCurrentExperienceTowardsNextLevel", "veterancy_progress", False),
    ("CombatUnit.GetCasualtiesDesc", "casualties", False),
    ("CombatUnit.GetBuilding.GetID", "building_id", False),
    ("CombatUnit.GetBuilding.GetNameNoFormatting", "building_name", True),
)


def _page_observation(page):
    """Expose the original copied page beside convenient receipt fields."""
    return {
        "page": page,
        "error": page.error,
        "world": page.world,
        "start": page.start,
        "source_count": page.source_count,
        "count_valid": page.count_valid,
        "row_count": page.row_count,
        "next": page.next,
        "has_more": page.has_more,
        "steps": page.steps,
        "completed_steps": page.completed_steps,
    }


async def _paged_rows(run, *, fields, bindings, source_expression, steps, source_arguments=()):
    """Read all native copied pages without leasing any Creation element."""
    if len(fields) > 32:
        raise ValueError("native projection page supports at most 32 scalar fields")
    rows, pages, details, start = [], [], [], 0
    while True:
        page = await run.page(
            tuple(expression for expression, _, _ in fields),
            bindings=bindings, source_expression=source_expression, start=start, limit=64,
            steps=steps, source_arguments=source_arguments,
        )
        pages.append(_page_observation(page))
        if page.error:
            details.append({"start": start, "native_error": page.error, "world": page.world})
        if not page.count_valid:
            details.append({
                "start": start, "reason": "native page source count is unavailable",
                "native_error": page.error, "world": page.world,
            })
        for local_index, cells in enumerate(page.rows):
            row = {
                "source_index": page.start + local_index,
                "atomic_snapshot": False,
                "unavailable": {},
            }
            for field_index, (_, key, display) in enumerate(fields):
                cell = cells[field_index] if field_index < len(cells) else None
                value, reason = run.cell_value(cell)
                if isinstance(value, data.FixedPoint):
                    value = fixed_decimal(value)
                if display:
                    row.update(display_field(value, key))
                else:
                    row[key] = value
                _unavailable(row, key, reason)
            rows.append(row)
        # A failed page can stop within a row. Its row cursor cannot identify
        # the unexecuted scalar suffix; do not replay it on another page.
        if page.error or not page.has_more:
            break
        if page.next <= start:
            details.append({
                "start": start, "reason": "native projection pagination did not advance",
                "native_error": page.error, "world": page.world,
            })
            break
        start = page.next
    observed = (bool(pages) and not details and
                all(page["count_valid"] and not page["error"] for page in pages))
    partial_cells = any(row["unavailable"] for row in rows)
    status = ("observed" if observed and not partial_cells else
              "partial" if rows or pages else "unavailable")
    return {
        "status": status,
        "source": source_expression,
        "rows": rows if rows or pages else None,
        "native_pages": pages,
        "details": details,
        "atomic_snapshot": False,
    }


async def _battle_culture_statistics(run, war, battle_source, battle_index, role):
    return await _paged_rows(
        run, fields=_CULTURE_STAT_FIELDS, bindings=(war,),
        source_expression="Battle.Get" + role.title() + "Statistics",
        steps=(data.CreationStep(battle_source, battle_index),),
    )


def _missing_page(source, error):
    detail = {"reason": str(error), "error": error}
    if isinstance(error, KnownResultError):
        detail["native_result"] = error.result
    if hasattr(error, "formation_sources"):
        detail["formation_sources"] = error.formation_sources
    return {
        "status": "unavailable", "source": source, "rows": None,
        "native_pages": [], "details": [detail], "atomic_snapshot": False,
    }


async def _battle_borrowed_formations(run, war, battle_source, battle_index, role):
    result = await _paged_rows(
        run, fields=_BORROWED_FORMATION_FIELDS, bindings=(war,),
        source_expression="Battle.Get" + role.title() + "BorrowedFormations",
        steps=(data.CreationStep(battle_source, battle_index),),
    )
    for row in result["rows"] or ():
        for key, expression in (
            ("formation_id", "MilitaryFormation.GetIDString"),
            ("country_id", "MilitaryFormation.GetCountry.GetID"),
        ):
            _native_id(run, row, key, row[key], row["unavailable"].get(key), expression)
    if any(row["unavailable"] for row in result["rows"] or ()):
        result["status"] = "partial"
    return result


async def _battle_formation(run, country_id, formation_id, cache):
    """Select a true owned wrapper from the observed formation's own Country.

    Enemy and allied formations need not occur in the player's roster. Each
    actually observed Country's army/fleet source is read at most once here.
    The existing Run retains the true wrappers, not copied Battle pointers.
    """
    if country_id is None or formation_id is None:
        raise SourceUnavailable("The observed formation or its Country has no complete native ID")
    country = data.ObjectBinding.country(await run.type_id("Country"), country_id)
    observations = []
    for method in ("GetMilitaryFormationsArmy", "GetMilitaryFormationsFleet"):
        key = (country_id, method)
        if key not in cache:
            bindings = {}
            observation = {
                "country_id": country_id, "source": "Country." + method,
                "rows": [], "status": "observed", "details": [],
                "atomic_snapshot": False,
            }
            cache[key] = (bindings, observation)
            try:
                await run.provider("Country", method)
                async for source_index, _, formation in run.elements(
                    "Country." + method, (country,), "MilitaryFormation"
                ):
                    value, reason = await run.field(
                        "MilitaryFormation", "GetIDString", (formation,), _TEXT
                    )
                    row = {"source_index": source_index, "unavailable": {}}
                    _native_id(run, row, "formation_id", value, reason,
                               "MilitaryFormation.GetIDString")
                    observation["rows"].append(row)
                    if row["unavailable"]:
                        observation["status"] = "partial"
                    if row["formation_id"] is not None:
                        bindings[row["formation_id"]] = formation
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                observation["status"] = "partial" if observation["rows"] else "unavailable"
                observation["details"] = _missing_page(observation["source"], error)["details"]
        bindings, observation = cache[key]
        observations.append(observation)
        if formation_id in bindings:
            return bindings[formation_id], observations
    error = SourceUnavailable(
        "Formation was not present in its Country's current original army or fleet sources"
    )
    error.formation_sources = observations
    raise error


async def _battle_combat_units(run, war, formation, battle_source, battle_index, unit_type_index):
    """Copy GetUnits leaves while the original raw unit-type prefix is alive.

    Each Page owns its fresh prefix graph. The canonical native adapter captures
    Formation.Self before the first War/Battle borrow, then consumes GetUnits
    without reentering an ancestor. No BattleUnitType or CombatUnit Any escapes.
    """
    if not await run.page_available(typed_steps=True):
        return _missing_page("BattleUnitType.GetUnits", SourceUnavailable(
            "PdxV3DataProjectTypedStepsInterface is unavailable; "
            "the original GetUnits page was not submitted"
        ))
    result = await _paged_rows(
        run, fields=_BATTLE_COMBAT_UNIT_FIELDS, bindings=(war, formation),
        source_expression="BattleUnitType.GetUnits",
        steps=(
            data.CreationStep(battle_source, battle_index),
            data.CreationStep(
                "Battle.GetBattleUnitTypeList( MilitaryFormation.Self )", unit_type_index,
                source_arguments=("MilitaryFormation.Self",),
            ),
        ),
    )
    for row in result["rows"] or ():
        _native_id(run, row, "building_id", row["building_id"],
                   row["unavailable"].get("building_id"), "CombatUnit.GetBuilding.GetID")
    if any(row["unavailable"] for row in result["rows"] or ()):
        result["status"] = "partial"
    return result


async def _battle_unit_types(run, war, formation, battle_source, battle_index):
    result = await _paged_rows(
        run, fields=_BATTLE_UNIT_TYPE_FIELDS, bindings=(war, formation),
        source_expression="Battle.GetBattleUnitTypeList( MilitaryFormation.Self )",
        steps=(data.CreationStep(battle_source, battle_index),),
        source_arguments=("MilitaryFormation.Self",),
    )
    for row in result["rows"] or ():
        try:
            units = await _battle_combat_units(
                run, war, formation, battle_source, battle_index, row["source_index"]
            )
        except (SourceUnavailable, KnownResultError, LookupError) as error:
            units = _missing_page("BattleUnitType.GetUnits", error)
        except BridgeError as error:
            # Optional typed-prefix export absence is known before submission.
            # Pending/transport errors keep their original query and propagate.
            if (getattr(error, "query", None) is not None or
                    error.operation != "resolve_export" or
                    error.details.get("reason") not in ("export not found", "empty export")):
                raise
            units = _missing_page("BattleUnitType.GetUnits", error)
        row["units"] = units
        if units["status"] != "observed":
            row["unavailable"]["units"] = (
                "The original CombatUnit page is partial or unavailable"
            )
    return result


async def battle_details(api, *, war_id=None):
    """Read deep fields of active land battles without inventing a Battle ID."""
    async def observe(run):
        rows, failures, deep_missing = [], [], set()
        formations = {}
        page_available = await run.page_available()
        async for current_war, war in _wars(run, war_id):
            source = "War.GetActiveBattles"
            try:
                await run.provider("War", "GetActiveBattles")
                count = await _source_count(run, source, (war,))
                for index in range(count):
                    row = {
                        "war_id": current_war,
                        "source": source,
                        "source_index": index,
                        "source_scoped_identity": _source_locator(current_war, source, index),
                        "id": None,
                        "id_unavailable_reason": "V3 1.13.11 Battle metadata has no native Battle ID getter",
                        "atomic_snapshot": False,
                        "unavailable": {},
                    }
                    for expression, key, kinds, display in (
                        ("Battle.GetName", "name", _TEXT, True),
                        ("Battle.GetTooltipTag", "tooltip_tag", _TEXT, False),
                        ("Battle.GetLocationName", "location", _TEXT, False),
                        ("Battle.GetTerrain", "terrain", _TEXT, False),
                        ("Battle.GetTerrainDesc", "terrain_description", _TEXT, False),
                        ("Battle.GetOffenseRaw", "attacker_offense", _FIXED, False),
                        ("Battle.GetDefenseRaw", "defender_defense", _FIXED, False),
                        ("Battle.CalcPushOfWar", "push_of_war", _FIXED, False),
                        ("Battle.GetAttackerTotalDead", "attacker_dead", _FIXED, False),
                        ("Battle.GetDefenderTotalDead", "defender_dead", _FIXED, False),
                        ("Battle.GetAttackerTotalWounded", "attacker_wounded", _FIXED, False),
                        ("Battle.GetDefenderTotalWounded", "defender_wounded", _FIXED, False),
                        ("Battle.GetAttackerTotalDemoralized", "attacker_demoralized", _FIXED, False),
                        ("Battle.GetDefenderTotalDemoralized", "defender_demoralized", _FIXED, False),
                        ("Battle.GetAttackerUnderCommandBreakdown", "attacker_under_command", _TEXT, False),
                        ("Battle.GetDefenderUnderCommandBreakdown", "defender_under_command", _TEXT, False),
                    ):
                        method = expression.split(".", 1)[1]
                        await _inline_field(
                            run, row, source=source, index=index, count=count, bindings=(war,),
                            expression=expression, key=key, kinds=kinds, display=display,
                            metadata=(("Battle", method, 0),),
                        )
                    row["sides"] = {
                        role: await _battle_side(run, source, index, count, war, role)
                        for role in ("attacker", "defender")
                    }
                    row["conditions"] = {
                        role: await _battle_condition(run, source, index, count, war, role)
                        for role in ("attacker", "defender")
                    }
                    row["aggregate_casualty_statistics"] = {
                        role: {
                            "dead": row.get(role + "_dead"),
                            "wounded": row.get(role + "_wounded"),
                            "demoralized": row.get(role + "_demoralized"),
                            "lost_units": row["sides"][role]["participating_units"].get("lost_units"),
                        }
                        for role in ("attacker", "defender")
                    }
                    row["deep_page_projection"] = {"available": page_available}
                    if page_available:
                        statistics = {}
                        for role in ("attacker", "defender"):
                            child_source = "Battle.Get" + role.title() + "Statistics"
                            try:
                                statistics[role] = await _battle_culture_statistics(
                                    run, war, source, index, role
                                )
                            except (SourceUnavailable, KnownResultError, LookupError) as error:
                                statistics[role] = _missing_page(child_source, error)
                            if statistics[role]["status"] != "observed":
                                deep_missing.add("culture_casualty_statistics")
                        row["culture_casualty_statistics"] = statistics
                        if all(value["status"] == "observed" for value in statistics.values()):
                            row["unavailable"].pop("culture_casualty_statistics", None)
                        else:
                            row["unavailable"]["culture_casualty_statistics"] = (
                                "One or more native casualty-statistic pages are partial or unavailable"
                            )

                        for role in ("attacker", "defender"):
                            side = row["sides"][role]
                            units = side["participating_units"]
                            child_source = "Battle.Get" + role.title() + "BorrowedFormations"
                            try:
                                borrowed = await _battle_borrowed_formations(
                                    run, war, source, index, role
                                )
                            except (SourceUnavailable, KnownResultError, LookupError) as error:
                                borrowed = _missing_page(child_source, error)
                            units["borrowed_formations"] = borrowed
                            if borrowed["status"] == "observed":
                                units["unavailable"].pop("borrowed_formations", None)
                            else:
                                deep_missing.add("borrowed_formations")
                                units["unavailable"]["borrowed_formations"] = (
                                    "The original borrowed-formation page is partial or unavailable"
                                )

                            # The commanded source and each actual borrowed source retain
                            # their own copied page, errors, and read-local coordinates.
                            owners = [(side.get("country_id"), side.get("formation_id"), units)]
                            owners.extend((item.get("country_id"), item.get("formation_id"), item)
                                          for item in borrowed["rows"] or ())
                            for country_id, formation_id, owner in owners:
                                try:
                                    formation, formation_sources = await _battle_formation(
                                        run, country_id, formation_id, formations
                                    )
                                    unit_types = await _battle_unit_types(
                                        run, war, formation, source, index
                                    )
                                    unit_types["formation_sources"] = formation_sources
                                except (SourceUnavailable, KnownResultError, LookupError) as error:
                                    unit_types = _missing_page(
                                        "Battle.GetBattleUnitTypeList( MilitaryFormation.Self )", error
                                    )
                                owner["unit_types"] = unit_types
                                if unit_types["status"] == "observed":
                                    owner["unavailable"].pop("unit_types", None)
                                else:
                                    deep_missing.add("participating_unit_types")
                                    owner["unavailable"]["unit_types"] = (
                                        "The original BattleUnitType page is partial or unavailable"
                                    )
                                if (unit_types["status"] != "observed" or any(
                                        item["units"]["status"] != "observed"
                                        for item in unit_types["rows"] or ())):
                                    deep_missing.add("participating_combat_units")
                    else:
                        deep_missing.update((
                            "culture_casualty_statistics", "borrowed_formations",
                            "participating_unit_types", "participating_combat_units",
                        ))
                        row["culture_casualty_statistics"] = None
                        row["unavailable"]["culture_casualty_statistics"] = (
                            "PdxV3DataProjectPageInterface is unavailable; base scalar battle "
                            "observations remain valid"
                        )
                        for role in ("attacker", "defender"):
                            units = row["sides"][role]["participating_units"]
                            for key in ("unit_types", "borrowed_formations"):
                                units["unavailable"][key] = (
                                    "PdxV3DataProjectPageInterface is unavailable; base scalar side "
                                    "and formation observations remain valid"
                                )
                    rows.append(row)
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                failures.append({"war_id": current_war, "source": source, "reason": str(error)})
        return {
            "status": ("partial" if rows and (deep_missing or failures) else
                       "observed" if rows or not failures else "unavailable"),
            "battles": rows if rows or not failures else None,
            "coverage": ("active_land_battle_details" if rows and not deep_missing else
                         "active_land_battle_details_partial"),
            "feature_ids": ["war.land-battle-detail"],
            "page_projection_available": page_available,
            "reason": (
                "Observed native scalar battle detail is preserved; inspect missing outputs and "
                "original native page/error receipts for any remaining deep fields"
            ) if rows and (deep_missing or failures) else None,
            "missing_required_outputs": sorted(deep_missing) if rows else [],
            "details": failures,
        }
    return await owned_read(api, "military.battle_details", observe)


async def battle_history(api, *, war_id=None):
    """Read the original ended-battle source and its final outcome fields."""
    async def observe(run):
        rows, failures = [], []
        async for current_war, war in _wars(run, war_id):
            source = "War.GetEndedBattles"
            try:
                await run.provider("War", "GetEndedBattles")
                count = await _source_count(run, source, (war,))
                for index in range(count):
                    row = {
                        "war_id": current_war,
                        "source": source,
                        "source_index": index,
                        "source_scoped_identity": _source_locator(current_war, source, index),
                        "id": None,
                        "id_unavailable_reason": "V3 1.13.11 Battle metadata has no native Battle ID getter",
                        "atomic_snapshot": False,
                        "unavailable": {},
                    }
                    specs = (
                        ("Battle.GetName", "name", _TEXT, False, True),
                        ("Battle.GetTooltipTag", "tooltip_tag", _TEXT, False, False),
                        ("Battle.GetStartDate", "start_date", _TEXT, False, False),
                        ("Battle.GetEndDate", "end_date", _TEXT, False, False),
                        ("Battle.GetDateDesc", "date_description", _TEXT, False, False),
                        ("Battle.GetWinner.GetID", "winner_country_id", _ID_KINDS, True, False),
                        ("Battle.GetWinner.GetNameNoFormatting", "winner_country_name", _TEXT, False, True),
                        ("Battle.GetLoser.GetID", "loser_country_id", _ID_KINDS, True, False),
                        ("Battle.GetLoser.GetNameNoFormatting", "loser_country_name", _TEXT, False, True),
                        ("Battle.EndedInconclusively", "inconclusive", (data.Kind.BOOL,), False, False),
                        ("Battle.GetFinalAttackerManpower", "final_attacker_manpower", (data.Kind.I64,), False, False),
                        ("Battle.GetFinalDefenderManpower", "final_defender_manpower", (data.Kind.I64,), False, False),
                        ("Battle.GetFinalAttackerUnits", "final_attacker_units", (data.Kind.I32,), False, False),
                        ("Battle.GetFinalDefenderUnits", "final_defender_units", (data.Kind.I32,), False, False),
                        ("Battle.GetAttackerTotalDead", "attacker_dead", _FIXED, False, False),
                        ("Battle.GetDefenderTotalDead", "defender_dead", _FIXED, False, False),
                        ("Battle.GetAttackerTotalWounded", "attacker_wounded", _FIXED, False, False),
                        ("Battle.GetDefenderTotalWounded", "defender_wounded", _FIXED, False, False),
                        ("Battle.GetAttackerTotalDemoralized", "attacker_demoralized", _FIXED, False, False),
                        ("Battle.GetDefenderTotalDemoralized", "defender_demoralized", _FIXED, False, False),
                        ("Battle.GetNumCapturedProvinces", "captured_provinces", (data.Kind.I32,), False, False),
                        ("Battle.GetOccupationWon", "occupation_won", _TEXT, False, False),
                        ("Battle.GetOccupationWonRaw", "occupation_won_raw", _FIXED, False, False),
                        ("Battle.GetVictoryScore", "victory_score", _TEXT, False, False),
                        ("Battle.GetVictoryScoreRaw", "victory_score_raw", _FIXED, False, False),
                    )
                    for expression, key, kinds, identity, display in specs:
                        tail = expression.split(".", 1)[1]
                        if tail.startswith("GetWinner."):
                            metadata = (("Battle", "GetWinner", 0),
                                ("Country", tail.split(".", 1)[1], 0))
                        elif tail.startswith("GetLoser."):
                            metadata = (("Battle", "GetLoser", 0),
                                ("Country", tail.split(".", 1)[1], 0))
                        else:
                            metadata = (("Battle", tail, 0),)
                        await _inline_field(
                            run, row, source=source, index=index, count=count, bindings=(war,),
                            expression=expression, key=key, kinds=kinds, metadata=metadata,
                            identity=identity, display=display,
                        )
                    rows.append(row)
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                failures.append({"war_id": current_war, "source": source, "reason": str(error)})
        return {
            "status": "partial" if failures and rows else "unavailable" if failures else "observed",
            "battles": rows if rows or not failures else None,
            "coverage": "ended_land_battle_history",
            "feature_ids": ["war.battle-history"],
            "details": failures,
        }
    return await owned_read(api, "military.battle_history", observe)


async def _front_side_members(run, front, role, member_type):
    title = role.title()
    method = "GetCountries" if member_type == "Country" else "GetArmies"
    source = "Front.Get" + title + "Side." + method
    await _metadata(run, (("Front", "Get" + title + "Side", 0),
                          ("FrontSide", method, 0)))
    count = await _source_count(run, source, (front,))
    rows = []
    for index in range(count):
        row = {"source_index": index, "unavailable": {}}
        if member_type == "Country":
            await _inline_field(
                run, row, source=source, index=index, count=count, bindings=(front,),
                expression="Country.GetID", key="country_id", kinds=_ID_KINDS,
                identity=True, metadata=(("Country", "GetID", 0),),
            )
            await _inline_field(
                run, row, source=source, index=index, count=count, bindings=(front,),
                expression="Country.GetNameNoFormatting", key="name", kinds=_TEXT,
                display=True, metadata=(("Country", "GetNameNoFormatting", 0),),
            )
        else:
            await _inline_field(
                run, row, source=source, index=index, count=count, bindings=(front,),
                expression="MilitaryFormation.GetIDString", key="formation_id", kinds=_TEXT,
                identity=True, metadata=(("MilitaryFormation", "GetIDString", 0),),
            )
            await _inline_field(
                run, row, source=source, index=index, count=count, bindings=(front,),
                expression="MilitaryFormation.GetNameNoFormatting", key="name", kinds=_TEXT,
                display=True, metadata=(("MilitaryFormation", "GetNameNoFormatting", 0),),
            )
            await _inline_field(
                run, row, source=source, index=index, count=count, bindings=(front,),
                expression="MilitaryFormation.GetCountry.GetID", key="country_id", kinds=_ID_KINDS,
                identity=True, metadata=(("Country", "GetID", 0),),
            )
            for method_name, key, kinds in (
                ("GetMaxManpower", "max_manpower", (data.Kind.I64,)),
                ("GetMorale", "morale", _FIXED),
                ("GetTravelDaysLeft", "travel_days_left", (data.Kind.I32,)),
                ("GetSupplyRatio", "supply_ratio", _FIXED),
                ("GetAverageOffenseExcludeUnavailableManpower", "effective_offense", _FIXED),
                ("GetAverageDefenseExcludeUnavailableManpower", "effective_defense", _FIXED),
                ("GetNumCombatUnits", "combat_units", (data.Kind.I32,)),
                ("GetCurrentManpower", "manpower", (data.Kind.I64,)),
                ("GetOrganizationPercentage", "organization", _FIXED),
                ("IsTraveling", "travelling", (data.Kind.BOOL,)),
                ("IsInBattle", "in_battle", (data.Kind.BOOL,)),
            ):
                await _inline_field(
                    run, row, source=source, index=index, count=count, bindings=(front,),
                    expression="MilitaryFormation." + method_name, key=key, kinds=kinds,
                    metadata=(("MilitaryFormation", method_name, 0),),
                )
        rows.append(row)
    return rows


async def _front_battle_refs(run, front, source):
    await run.provider("Front", source.split(".", 1)[1])
    count = await _source_count(run, source, (front,))
    rows = []
    for index in range(count):
        row = {
            "source": source,
            "source_index": index,
            "stable_game_identity": False,
            "unavailable": {},
        }
        for expression, key, display in (
            ("Battle.GetName", "name", True),
            ("Battle.GetTooltipTag", "tooltip_tag", False),
            ("Battle.GetStatusString", "status", False),
        ):
            await _inline_field(
                run, row, source=source, index=index, count=count, bindings=(front,),
                expression=expression, key=key, kinds=_TEXT, display=display,
                metadata=(("Battle", expression.split(".", 1)[1], 0),),
            )
        rows.append(row)
    return rows


async def _front_side(run, front, role):
    title = role.title()
    prefix = "Front.Get" + title + "Side"
    row = {"canonical_role": role, "unavailable": {}}
    for method, key, kinds in (
        ("IsInitiatorSide", "native_is_initiator", (data.Kind.BOOL,)),
        ("IsTargetSide", "native_is_target", (data.Kind.BOOL,)),
        ("GetNumUnits", "units", (data.Kind.I32,)),
        ("GetNumUnitsBreakdown", "units_breakdown", _TEXT),
        ("GetAdvancement", "advancement", _FIXED),
        ("GetAdvancementProgress", "advancement_progress", _FIXED),
        ("GetAdvantage", "advantage", (data.Kind.I32,)),
        ("GetAdvantageDesc", "advantage_description", _TEXT),
        ("GetUnitOffense", "unit_offense", _FIXED),
        ("GetUnitDefense", "unit_defense", _FIXED),
        ("GetUnitMorale", "unit_morale", _FIXED),
        ("GetUnitMoraleDesc", "unit_morale_description", _TEXT),
        ("GetTotalNumCasualties", "casualties", (data.Kind.I64,)),
        ("GetTotalNumDead", "dead", (data.Kind.I64,)),
        ("GetTotalNumWounded", "wounded", (data.Kind.I64,)),
    ):
        value, reason = await run.field(
            "FrontSide", method, (front,), kinds, expression=prefix + "." + method
        )
        row[key] = value
        _unavailable(row, key, reason)
    value, reason = await run.field(
        "Country", "GetID", (front,), _ID_KINDS,
        expression=prefix + ".GetCountry.GetID"
    )
    _native_id(run, row, "primary_country_id", value, reason,
               prefix + ".GetCountry.GetID")
    value, reason = await run.field(
        "Country", "GetNameNoFormatting", (front,), _TEXT,
        expression=prefix + ".GetCountry.GetNameNoFormatting"
    )
    row.update(display_field(value, "primary_country_name"))
    _unavailable(row, "primary_country_name", reason)
    try:
        row["countries"] = await _front_side_members(run, front, role, "Country")
    except (SourceUnavailable, KnownResultError, LookupError) as error:
        row["countries"] = None
        row["unavailable"]["countries"] = str(error)
    try:
        row["formations"] = await _front_side_members(run, front, role, "MilitaryFormation")
    except (SourceUnavailable, KnownResultError, LookupError) as error:
        row["formations"] = None
        row["unavailable"]["formations"] = str(error)
    return row


async def front_situations(api, *, war_id=None, front_id=None):
    """Read canonical front sides, actual rosters, advance, combat, and occupation."""
    async def observe(run):
        rows, failures, seen = [], [], set()
        async for current_war, war in _wars(run, war_id):
            try:
                await run.provider("War", "AccessFronts")
                async for _, _, front in run.elements("War.AccessFronts", (war,), "Front"):
                    identity, reason = await run.field("Front", "GetID", (front,), _ID_KINDS)
                    identity = run.api._native_full_id(identity)
                    if front_id is not None and identity != front_id:
                        if identity is None:
                            failures.append({"war_id": current_war, "source": "War.AccessFronts",
                                             "reason": reason or "A front identity was unavailable"})
                        continue
                    if identity is not None and identity in seen:
                        continue
                    if identity is not None:
                        seen.add(identity)
                    row = {
                        "id": identity,
                        "war_id": current_war,
                        "atomic_snapshot": False,
                        "unavailable": {},
                    }
                    _unavailable(row, "id", reason if identity is None else None)
                    name, reason = await run.field(
                        "Front", "GetNameNoFormatting", (front,), _TEXT
                    )
                    row.update(display_field(name))
                    _unavailable(row, "name", reason)
                    for method, key, kinds in (
                        ("GetRequiredAdvanceProgressForBattle", "required_advance_for_battle", _FIXED),
                        ("GetOccupationProgressDesc", "occupation_progress", _TEXT),
                        ("GetPlayerAdvantageRaw", "player_advantage", (data.Kind.I32,)),
                        ("GetPlayerAdvantageDesc", "player_advantage_description", _TEXT),
                    ):
                        value, field_reason = await run.field("Front", method, (front,), kinds)
                        row[key] = value
                        _unavailable(row, key, field_reason)
                    row["sides"] = {
                        role: await _front_side(run, front, role)
                        for role in ("initiator", "target")
                    }
                    for source, key in (
                        ("Front.AccessActiveBattles", "active_battles"),
                        ("Front.AccessEndedBattles", "ended_battles"),
                    ):
                        try:
                            row[key] = await _front_battle_refs(run, front, source)
                        except (SourceUnavailable, KnownResultError, LookupError) as error:
                            row[key] = None
                            row["unavailable"][key] = str(error)
                    rows.append(row)
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                failures.append({"war_id": current_war, "source": "War.AccessFronts", "reason": str(error)})
        return {
            "status": "partial" if failures and rows else "unavailable" if failures else "observed",
            "fronts": rows if rows or not failures else None,
            "coverage": "country_war_front_situations",
            "feature_ids": ["war.front-situation"],
            "details": failures,
        }
    return await owned_read(api, "military.front_situations", observe)


async def war_exhaustion(api, *, war_id=None):
    """Read actual per-participant exhaustion, support text, and casualty causes."""
    async def observe(run):
        wars, failures = [], []
        async for current_war, war in _wars(run, war_id):
            participants = []
            unavailable = {}
            for role, source in (
                ("attacker", "War.GetAttackers"),
                ("defender", "War.GetDefenders"),
            ):
                try:
                    await run.provider("War", source.split(".", 1)[1])
                    count = await _source_count(run, source, (war,))
                    for index in range(count):
                        row = {
                            "side": role,
                            "source": source,
                            "source_index": index,
                            "atomic_snapshot": False,
                            "unavailable": {},
                        }
                        specs = (
                            ("WarParticipant.GetCountry.GetID", "country_id", _ID_KINDS, True, False,
                             (("WarParticipant", "GetCountry", 0), ("Country", "GetID", 0))),
                            ("WarParticipant.GetCountry.GetNameNoFormatting", "country_name", _TEXT, False, True,
                             (("WarParticipant", "GetCountry", 0), ("Country", "GetNameNoFormatting", 0))),
                            ("WarParticipant.GetExhaustionDesc", "exhaustion_description", _TEXT, False, False,
                             (("WarParticipant", "GetExhaustionDesc", 0),)),
                            ("WarParticipant.GetWarSupportDesc", "war_support_description", _TEXT, False, False,
                             (("WarParticipant", "GetWarSupportDesc", 0),)),
                            ("WarParticipant.GetWarExhaustionFromAcceptanceOfDeadDesc", "dead_acceptance_exhaustion", _TEXT, False, False,
                             (("WarParticipant", "GetWarExhaustionFromAcceptanceOfDeadDesc", 0),)),
                            ("WarParticipant.GetCanCapitulateDesc", "capitulation_constraints", _TEXT, False, False,
                             (("WarParticipant", "GetCanCapitulateDesc", 0),)),
                            ("WarParticipant.GetCasualties", "casualties", (data.Kind.I64,), False, False,
                             (("WarParticipant", "GetCasualties", 0),)),
                            ("WarParticipant.GetNumDead", "dead", (data.Kind.I64,), False, False,
                             (("WarParticipant", "GetNumDead", 0),)),
                            ("WarParticipant.GetNumWounded", "wounded", (data.Kind.I64,), False, False,
                             (("WarParticipant", "GetNumWounded", 0),)),
                            ("WarParticipant.GetNumDeadFromBattle", "battle_dead", (data.Kind.I64,), False, False,
                             (("WarParticipant", "GetNumDeadFromBattle", 0),)),
                            ("WarParticipant.GetNumWoundedFromBattle", "battle_wounded", (data.Kind.I64,), False, False,
                             (("WarParticipant", "GetNumWoundedFromBattle", 0),)),
                            ("WarParticipant.GetNumDeadFromAttrition", "attrition_dead", (data.Kind.I64,), False, False,
                             (("WarParticipant", "GetNumDeadFromAttrition", 0),)),
                            ("WarParticipant.GetNumWoundedFromAttrition", "attrition_wounded", (data.Kind.I64,), False, False,
                             (("WarParticipant", "GetNumWoundedFromAttrition", 0),)),
                            ("WarParticipant.IsWarLeader", "war_leader", (data.Kind.BOOL,), False, False,
                             (("WarParticipant", "IsWarLeader", 0),)),
                            ("WarParticipant.IsPeaceNegotiator", "peace_negotiator", (data.Kind.BOOL,), False, False,
                             (("WarParticipant", "IsPeaceNegotiator", 0),)),
                        )
                        for expression, key, kinds, identity, display, metadata in specs:
                            await _inline_field(
                                run, row, source=source, index=index, count=count,
                                bindings=(war,), expression=expression, key=key, kinds=kinds,
                                metadata=metadata, identity=identity, display=display,
                            )
                        participants.append(row)
                except (SourceUnavailable, KnownResultError, LookupError) as error:
                    unavailable[role] = str(error)
                    failures.append({"war_id": current_war, "source": source, "reason": str(error)})
            wars.append({
                "war_id": current_war,
                "participants": participants if participants or not unavailable else None,
                "unavailable": unavailable,
                "atomic_snapshot": False,
            })
        return {
            "status": "partial" if failures and wars else "unavailable" if failures else "observed",
            "wars": wars if wars or not failures else None,
            "coverage": "war_participant_exhaustion",
            "feature_ids": ["war.war-exhaustion"],
            "details": failures,
        }
    return await owned_read(api, "military.war_exhaustion", observe)


async def state_occupation(api):
    """Read the country's original State roster and native occupation pie rows."""
    async def observe(run):
        country = await run.country()
        await run.provider("Country", "AccessStates")
        rows, failures = [], []
        async for _, _, state in run.elements("Country.AccessStates", (country,), "State"):
            try:
                row = {"country_id": run.actor, "atomic_snapshot": False, "unavailable": {}}
                value, reason = await run.field("State", "GetID", (state,), _ID_KINDS)
                _native_id(run, row, "state_id", value, reason, "State.GetID")
                name, reason = await run.field("State", "GetNameNoFormatting", (state,), _TEXT)
                row.update(display_field(name))
                _unavailable(row, "name", reason)
                for method, key, kinds in (
                    ("HasOccupation", "has_occupation", (data.Kind.BOOL,)),
                    ("IsFullyOccupied", "fully_occupied", (data.Kind.BOOL,)),
                    ("IsContested", "contested", (data.Kind.BOOL,)),
                    ("GetDevastationLevel", "devastation", _FIXED),
                    ("GetDevastationDailyChange", "devastation_daily_change", _FIXED),
                    ("GetOccupantsTooltip", "occupants_description", _TEXT),
                ):
                    value, field_reason = await run.field("State", method, (state,), kinds)
                    row[key] = value
                    _unavailable(row, key, field_reason)
                for relation, key in (
                    ("GetOccupant", "occupant_country_id"),
                    ("GetEnemyWithHighestOccupation", "highest_occupation_country_id"),
                ):
                    expression = "State." + relation + ".GetID"
                    try:
                        value, field_reason = await run.field(
                            "Country", "GetID", (state,), _ID_KINDS,
                            expression=expression
                        )
                    except (SourceUnavailable, KnownResultError, LookupError) as error:
                        value, field_reason = None, str(error)
                    _native_id(run, row, key, value, field_reason, expression)
                source = "State.GetOccupationPiechartData"
                try:
                    await run.provider("State", "GetOccupationPiechartData")
                    count = await _source_count(run, source, (state,))
                    shares = []
                    for index in range(count):
                        share = {"source_index": index, "unavailable": {}}
                        await _inline_field(
                            run, share, source=source, index=index, count=count,
                            bindings=(state,), expression="OccupationPiechartData.GetCountry.GetID",
                            key="country_id", kinds=_ID_KINDS, identity=True,
                            metadata=(("OccupationPiechartData", "GetCountry", 0),
                                      ("Country", "GetID", 0)),
                        )
                        await _inline_field(
                            run, share, source=source, index=index, count=count,
                            bindings=(state,),
                            expression="OccupationPiechartData.GetCountry.GetNameNoFormatting",
                            key="country_name", kinds=_TEXT, display=True,
                            metadata=(("OccupationPiechartData", "GetCountry", 0),
                                      ("Country", "GetNameNoFormatting", 0)),
                        )
                        await _inline_field(
                            run, share, source=source, index=index, count=count,
                            bindings=(state,),
                            expression="OccupationPiechartData.GetOccupationFraction",
                            key="occupation_fraction", kinds=_FIXED,
                            metadata=(("OccupationPiechartData", "GetOccupationFraction", 0),),
                        )
                        shares.append(share)
                    row["occupation_shares"] = shares
                except (SourceUnavailable, KnownResultError, LookupError) as error:
                    row["occupation_shares"] = None
                    row["unavailable"]["occupation_shares"] = str(error)
                rows.append(row)
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                failures.append({"source": "Country.AccessStates", "reason": str(error)})
        return {
            "status": "partial" if failures and rows else "unavailable" if failures else "observed",
            "states": rows if rows or not failures else None,
            "coverage": "country_state_occupation",
            "feature_ids": ["war.occupation"],
            "details": failures,
        }
    return await owned_read(api, "military.state_occupation", observe)


__all__ = [
    "battle_details",
    "battle_history",
    "front_situations",
    "war_exhaustion",
    "state_occupation",
]
