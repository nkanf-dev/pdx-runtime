"""Copied views following the game's actual military source relationships."""
from __future__ import annotations

from ... import v3_data as data
from .._text import display_field
from .._run import KnownResultError, SourceUnavailable
from ._warfare import (WarfareRead, _source_country, _source_country_id, _formation,
                       _character, _related, _commander_order_state,
                       _produced_owner, _validate_submit, owned_read)


def _page_inputs(start, limit):
    if type(start) is not int or start < 0:
        raise ValueError("start must be a nonnegative source coordinate")
    if type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError("limit must be between 1 and 256")


def _report(key, source, start=0):
    return {key: [], "source": source, "status": "unavailable", "start": start,
            "next": start, "source_count": None, "has_more": None,
            "enumeration_complete": False, "atomic_snapshot": False, "details": []}


def _error(report, error):
    detail = {"reason": str(error)}
    if isinstance(error, KnownResultError):
        detail.update(native_result=error.result, native_error=error.result.error)
    report["details"].append(detail)


async def _elements(run, expression, bindings, named_type, report, start, limit):
    """One held source and count. Coordinates never become native identities."""
    source = await run.evaluate(expression, bindings)
    if source.kind != data.Kind.LIST or not isinstance(source.value, data.NativeList) or not source.value_handle:
        raise SourceUnavailable(expression + ": no retained native list")
    count = await run.call(expression + ".count", lambda: data.list_count(
        run.native, source.value_handle,
        adapter=run.value_adapters.get(source.value_handle, run.adapters["data"]), process=run.process))
    if type(count.total) is not int or count.total < 0:
        raise SourceUnavailable(expression + ": native count unavailable")
    report["source_count"] = count.total
    report["has_more"] = start < count.total
    for index in range(start, min(count.total, start + limit)):
        value = await run.call(expression + ".element", lambda index=index: data.list_element(
            run.native, source.value_handle, index,
            adapter=run.value_adapters.get(source.value_handle, run.adapters["data"]), process=run.process))
        binding = run.owned_binding(value, named_type)
        report["next"] = index + 1
        yield index, value, binding
    report["has_more"] = report["next"] < count.total
    report["enumeration_complete"] = not report["has_more"]


async def _directory(api, label, report, key, fill):
    run = WarfareRead(api, "military." + label)
    report["world"] = run.world
    try:
        await fill(run)
    except (SourceUnavailable, KnownResultError, LookupError) as error:
        _error(report, error)
        report["enumeration_complete"] = False
    except BaseException as error:
        report.update(status="partial" if report[key] else "unavailable",
                      native_results=tuple(run.operations), enumeration_complete=False)
        error.military_result = report
        raise
    report["status"] = ("partial" if report["details"] or any(
        row.get("unavailable") for row in report[key]) else "observed")
    if report["details"] and not report[key] and report.get("source_count") is None:
        report["status"] = "unavailable"
    report["native_results"] = tuple(run.operations)
    return await run.finish(report)


async def _name(run, row, binding, named_type, methods):
    fields = await run.functions(named_type)
    method = next((method for method in methods if method in fields), methods[0])
    value, reason = await run.field(named_type, method, (binding,), (data.Kind.STRING,))
    row.update(display_field(value))
    if reason:
        row["unavailable"]["name"] = reason


async def war_directory(api, *, start=0, limit=64):
    _page_inputs(start, limit)
    report = _report("wars", "Country.AccessWars", start)
    report["scope"] = "selected_country_wars"
    async def fill(run):
        country = await run.country()
        report["country_id"] = run.actor
        await run.provider("Country", "AccessWars")
        async for index, value, war in _elements(run, "Country.AccessWars", (country,),
                                                "War", report, start, limit):
            row = {"source_index": index, "unavailable": {}}
            report["wars"].append(row)
            row["id"] = await run.origin(value, "War")
            row["selector"] = {"type": "war", "id": row["id"]}
            await _name(run, row, war, "War", ("GetNameNoFormatting", "GetName"))
            for method, key in (("IsParticipant", "country_is_participant"),
                                ("IsAttacker", "country_is_attacker"),
                                ("IsDefender", "country_is_defender"),
                                ("IsWarLeader", "country_is_leader")):
                row[key], reason = await run.field("War", method, (war, country), (data.Kind.BOOL,),
                    arguments=1, expression="War." + method + "(Country.Self)")
                if reason:
                    row["unavailable"][key] = reason
    return await _directory(api, "war_directory", report, "wars", fill)


_READINESS = (
    ("GetFormationStatusDesc", "formation_status", data.Kind.STRING),
    ("GetLocationString", "location", data.Kind.STRING),
    ("IsMobilized", "mobilized", data.Kind.BOOL),
    ("IsFullyMobilized", "fully_mobilized", data.Kind.BOOL),
    ("GetMobilizationPercentage", "mobilization_fraction", data.Kind.FIXED),
    ("HasRaisedConscripts", "conscripts_raised", data.Kind.BOOL),
    ("GetNumCombatUnits", "combat_units", data.Kind.I32),
    ("GetCurrentManpower", "manpower", data.Kind.I64),
    ("GetMaxManpower", "max_manpower", data.Kind.I64),
    ("GetMorale", "morale", data.Kind.FIXED),
    ("GetOrganizationPercentage", "organization_fraction", data.Kind.FIXED),
    ("GetSupplyRatio", "supply_ratio", data.Kind.FIXED),
    ("IsTraveling", "travelling", data.Kind.BOOL),
    ("GetTravelDaysLeft", "travel_days_left", data.Kind.I32),
    ("IsInBattle", "in_battle", data.Kind.BOOL),
    ("GetAverageOffenseExcludeUnavailableManpower", "effective_offense", data.Kind.FIXED),
    ("GetAverageDefenseExcludeUnavailableManpower", "effective_defense", data.Kind.FIXED),
)


async def formation_directory(api, *, kind=None, start=0, limit=64):
    _page_inputs(start, limit)
    report = _report("formations", "Country.GetMilitaryFormationsArmy/Fleet", start)
    report["sources"] = []
    async def fill(run):
        country = await _source_country(run)
        report["country_id"] = _source_country_id(run)
        report["scope"] = "selected_country_formations"
        for suffix, source_kind in (("Army", "army"), ("Fleet", "fleet")):
            if kind is not None and kind != source_kind:
                continue
            expression = "Country.GetMilitaryFormations" + suffix
            source_report = _report("formations", expression, start)
            report["sources"].append(source_report)
            try:
                await run.provider("Country", "GetMilitaryFormations" + suffix)
                async for index, value, formation in _elements(run, expression, (country,),
                        "MilitaryFormation", source_report, start, limit):
                    row = {"source_index": index, "source": expression, "kind": source_kind,
                           "country_id": report["country_id"], "unavailable": {}}
                    report["formations"].append(row)
                    source_report["formations"].append(row)
                    row["id"] = await run.origin(value, "MilitaryFormation")
                    row["selector"] = {"type": "formation", "id": row["id"], "country_id": row["country_id"]}
                    await _name(run, row, formation, "MilitaryFormation", ("GetNameNoFormatting", "GetName"))
                    specs = _READINESS if source_kind == "army" else tuple(
                        spec for spec in _READINESS if spec[1] not in
                        ("mobilized", "fully_mobilized", "mobilization_fraction", "conscripts_raised"))
                    fields = await run.fields("MilitaryFormation", (formation,), specs)
                    row["unavailable"].update(fields.pop("unavailable"))
                    row.update(fields)
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                _error(source_report, error)
                _error(report, error)
        report["enumeration_complete"] = all(s["enumeration_complete"] for s in report["sources"])
        report["has_more"] = (True if any(s["has_more"] is True for s in report["sources"]) else
                              None if any(s["has_more"] is None for s in report["sources"]) else False)
        report["source_count"] = (sum(s["source_count"] for s in report["sources"])
            if all(s["source_count"] is not None for s in report["sources"]) else None)
    return await _directory(api, "formation_directory", report, "formations", fill)


async def commander_directory(api, *, role=None, start=0, limit=64):
    _page_inputs(start, limit)
    report = _report("commanders", "Country.AccessGenerals/AccessAdmirals", start)
    report["sources"] = []
    async def fill(run):
        country = await _source_country(run)
        report["country_id"] = _source_country_id(run)
        report["scope"] = "selected_country_commanders"
        for method, source_role in (("AccessGenerals", "general"), ("AccessAdmirals", "admiral")):
            if role is not None and role != source_role:
                continue
            expression = "Country." + method
            source_report = _report("commanders", expression, start)
            report["sources"].append(source_report)
            try:
                await run.provider("Country", method)
                async for index, value, character in _elements(run, expression, (country,),
                        "Character", source_report, start, limit):
                    row = {"source_index": index, "role": source_role, "country_id": report["country_id"], "unavailable": {}}
                    report["commanders"].append(row)
                    source_report["commanders"].append(row)
                    identity, reason = await run.field("Character", "GetID", (character,), (data.Kind.I32, data.Kind.U32))
                    row["id"] = api._native_full_id(identity)
                    if reason or row["id"] is None:
                        row["unavailable"]["id"] = reason or "Native commander ID unavailable"
                    else:
                        row["selector"] = {"type": "commander", "id": row["id"], "country_id": row["country_id"]}
                    await _name(run, row, character, "Character", ("GetFullNameNoFormatting", "GetFullName"))
                    for method_name, key, kind in (("GetRankName", "rank", data.Kind.STRING),
                            ("GetCommandLimitRaw", "command_limit", data.Kind.I32),
                            ("HasMilitaryFormation", "has_formation", data.Kind.BOOL),
                            ("IsTravellingToFormation", "travelling_to_formation", data.Kind.BOOL)):
                        row[key], reason = await run.field("Character", method_name, (character,), (kind,))
                        if reason:
                            row["unavailable"][key] = reason
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                _error(source_report, error)
                _error(report, error)
        report["enumeration_complete"] = all(s["enumeration_complete"] for s in report["sources"])
    return await _directory(api, "commander_directory", report, "commanders", fill)


async def commander_read(api, character_id, *, traits=False):
    async def observe(run):
        character = await _character(run, character_id)
        row = {"id": character_id, "country_id": _source_country_id(run), "unavailable": {},
               "atomic_snapshot": False}
        await _name(run, row, character, "Character", ("GetFullNameNoFormatting", "GetFullName"))
        fields = await run.fields("Character", (character,), (
            ("GetAllRoleNames", "roles", data.Kind.STRING), ("IsGeneral", "general", data.Kind.BOOL),
            ("IsAdmiral", "admiral", data.Kind.BOOL), ("GetRankName", "rank", data.Kind.STRING),
            ("GetCommandLimitRaw", "command_limit", data.Kind.I32),
            ("GetCommandLimitDesc", "command_limit_description", data.Kind.STRING),
            ("GetTraitsDesc", "traits_description", data.Kind.STRING),
            ("GetCommanderDetails", "commander_description", data.Kind.STRING),
            ("HasMilitaryFormation", "has_formation", data.Kind.BOOL),
            ("IsTravellingToFormation", "travelling_to_formation", data.Kind.BOOL),
            ("HasAdvancingOrder", "has_advancing_order", data.Kind.BOOL)))
        row["unavailable"].update(fields.pop("unavailable"))
        row.update(fields)
        row["order_state"] = await _commander_order_state(run, character)
        for getter, key, target in (("GetMilitaryFormation", "formation", "MilitaryFormation"),
                                    ("GetFront", "order_target_front", "Front")):
            row[key], reason = await _related(run, character, "Character", getter, target)
            if reason:
                row["unavailable"][key] = reason
        if traits:
            from .forces import _traits
            row["traits"], reason = await _traits(run, character)
            if reason:
                row["unavailable"]["traits"] = reason
        row["status"] = "partial" if row["unavailable"] or row["order_state"]["unavailable"] else "observed"
        return row
    return await owned_read(api, "military.commander_read", observe)


async def mobilization_state(api, formation_id):
    async def observe(run):
        kind, formation = await _formation(run, formation_id, kind="army")
        if formation is None:
            raise SourceUnavailable("Army is absent from the selected country's original source")
        return {"formation_id": formation_id, "country_id": _source_country_id(run),
                "atomic_snapshot": False, **await run.fields("MilitaryFormation", (formation,), _READINESS[:6])}
    return await owned_read(api, "military.mobilization_state", observe)


async def set_mobilization(api, formation_id, *, enabled=True, conscripts=False, validate_only=False):
    async def operation(run):
        _, formation = await _formation(run, formation_id, kind="army")
        if formation is None:
            raise SourceUnavailable("Army is absent from the selected country's original source")
        method = "RaiseConscripts" if conscripts else "Mobilize" if enabled else "Demobilize"
        state_getter = "HasRaisedConscripts" if conscripts else "IsMobilized"
        before, reason = await run.field("MilitaryFormation", state_getter, (formation,), (data.Kind.BOOL,))
        result = {"formation_id": formation_id, "action": method, "accepted": None,
                  "before": before, "unavailable": {}}
        if reason:
            result["unavailable"]["before"] = reason
        desired = True if conscripts else enabled
        if not reason and before is desired:
            result.update(status="unchanged", submitted=False)
            return result
        owner = await _produced_owner(run, "MilitaryFormation." + method, (formation,))
        accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if accepted:
            try:
                result["observed"], reason = await run.field("MilitaryFormation", state_getter,
                                                           (formation,), (data.Kind.BOOL,))
                if reason:
                    result["unavailable"]["observed"] = reason
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
                if getattr(error, "query", None) is not None:
                    result["observation_query"] = error.query
        return result
    return await owned_read(api, "military.set_mobilization", operation)


async def ship_directory(api, *, fleet_id=None, start=0, limit=64):
    _page_inputs(start, limit)
    from .ships import _ship_list, _formation_relation
    report = _report("ships", "Country.GetShipList -> ShipList.GetShips", start)
    report["fleet_filter"] = fleet_id
    async def fill(run):
        ship_list = await _ship_list(run)
        report["country_id"] = _source_country_id(run)
        report["scope"] = "selected_country_ships"
        await run.provider("ShipList", "GetShips")
        async for index, value, ship in _elements(run, "ShipList.GetShips", (ship_list,),
                                                "Ship", report, start, limit):
            row = {"source_index": index, "country_id": report["country_id"], "unavailable": {}}
            report["ships"].append(row)
            row["id"] = await run.origin(value, "Ship")
            row["fleet"], reason = await _formation_relation(run, ship, "Ship", "GetFleet")
            if reason:
                row["unavailable"]["fleet"] = reason
            if fleet_id is not None and row["fleet"] is not None and row["fleet"].get("id") != fleet_id:
                report["ships"].pop()
                continue
            row["selector"] = {"type": "ship", "id": row["id"], "country_id": report["country_id"]}
            await _name(run, row, ship, "Ship", ("GetNameNoFormatting", "GetName"))
            fields = await run.fields("Ship", (ship,), (
                ("GetHitPoints", "hull_current", data.Kind.FIXED),
                ("GetHitPointsMax", "hull_max", data.Kind.FIXED),
                ("GetTotalCrew", "crew_current", data.Kind.I32),
                ("GetCrewMax", "crew_max", data.Kind.FIXED),
                ("IsOutdated", "outdated", data.Kind.BOOL),
                ("IsToRetrofit", "marked_for_retrofit", data.Kind.BOOL)))
            row["unavailable"].update(fields.pop("unavailable"))
            row.update(fields)
    return await _directory(api, "ship_directory", report, "ships", fill)
