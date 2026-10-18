"""Owned warfare sources for formations, units, mobilization options and careers.

Actions use complete native IDs, ID strings, or exact retained owned choices.
Source ordinals and tooltip tags are read-only diagnostics, never mutation
identities. A selected producer is materialized once, validated once and
submitted at most once. Original void property actions stay distinct from
GameCommand producers and from observed gameplay effects.
"""
from __future__ import annotations

from ... import v3_data as data
from .._text import display_field
from ._warfare import (
    KnownResultError,
    SourceUnavailable,
    _character,
    _formation,
    _produced_owner,
    _validate_submit,
    owned_read,
)
from .._run import KnownResultError, SourceUnavailable, owned_binding, rejected


def _identity_text(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty native ID string")
    return value


def _record_field(row, unavailable, key, value, reason):
    row[key] = value
    if reason:
        unavailable[key] = reason


def _choice_input(choice, named_type, context, source_kind):
    from .._choices import OwnedChoice
    if not isinstance(choice, OwnedChoice) or choice.native_type != named_type:
        raise TypeError("pass an original owned " + named_type + " choice, not an ID/name/index")
    catalog = choice._catalog
    catalog._ensure_open()
    if not any(item is choice for item in catalog):
        raise ValueError("choice is not an admitted record of its original catalog")
    if catalog.context is not context or catalog.native is not context.native or catalog.process is not context.process:
        raise ValueError("choice belongs to another original context/native client/process")
    if catalog.metadata.get("source_kind") != source_kind:
        raise ValueError("choice does not belong to the required original warfare source")
    return catalog


def _choice_report(source):
    return {"source": source, "status": "building", "complete": False,
            "reasons": [], "rejected_rows": [], "source_count": 0,
            "admitted_count": 0}


async def _owned_choice_elements(run, catalog, expression, bindings, named_type, *, count_source=True):
    """Yield only actual owned CPdxPtr rows; weak/raw wrappers stay diagnostics."""
    source = await run.evaluate(expression, bindings)
    if (source.kind != data.Kind.LIST or not isinstance(source.value, data.NativeList)
            or not source.value_handle):
        raise SourceUnavailable(expression + ": original source did not return a retained native list")
    count = await run.call(expression + ".count", lambda: data.list_count(run.native,
        source.value_handle, adapter=run.value_adapters.get(source.value_handle, run.adapters["data"]),
        process=run.process))
    if type(count.total) is not int or count.total < 0:
        raise SourceUnavailable(expression + ": native list count is unavailable or invalid")
    if count_source:
        catalog.metadata["result"]["source_count"] += count.total
    for index in range(count.total):
        child = await run.call(expression + ".element", lambda index=index: data.list_element(
            run.native, source.value_handle, index,
            adapter=run.value_adapters.get(source.value_handle, run.adapters["data"]), process=run.process))
        try:
            binding = owned_binding(run, child, named_type)
        except (RuntimeError, TypeError, ValueError) as error:
            catalog.metadata["result"]["rejected_rows"].append({
                "expression": expression, "source_index": index,
                "reason": str(error), "native_result": child})
            continue
        yield index, child, binding


def _finish_choice_catalog(catalog):
    report = catalog.metadata["result"]
    report["admitted_count"] = len(catalog)
    report["complete"] = (not report["reasons"] and not report["rejected_rows"]
                          and all(choice.get("complete") is True for choice in catalog))
    report["status"] = "observed" if report["complete"] else "partial" if len(catalog) else "unavailable"
    report["native_results"] = tuple(catalog.owner.operations)
    return catalog


async def _entry_by_id(run, formation, wanted):
    """Resolve a fresh source row by its original GetIDString, never by ordinal."""
    await run.provider("MilitaryFormation", "GetCombatUnitEntries")
    matched = None
    async for _, _, entry in run.elements("MilitaryFormation.GetCombatUnitEntries",
            (formation,), "MilitaryFormationUnitEntry"):
        identity, reason = await run.field("MilitaryFormationUnitEntry", "GetIDString",
            (entry,), (data.Kind.STRING,))
        if reason:
            raise SourceUnavailable(reason)
        if identity == wanted:
            if matched is not None:
                raise SourceUnavailable("MilitaryFormationUnitEntry.GetIDString is duplicated in the current source")
            matched = entry
    if matched is None:
        raise SourceUnavailable(f"Unit entry native ID string {wanted!r} is not present in the current formation source")
    return matched


async def _group_by_id(run, formation, wanted):
    """Resolve a fresh group source row by original CombatUnitGroup.GetIDString."""
    await run.provider("MilitaryFormation", "GetEligibleGroups")
    matched = None
    async for _, _, group in run.elements("MilitaryFormation.GetEligibleGroups",
            (formation,), "CombatUnitGroup"):
        identity, reason = await run.field("CombatUnitGroup", "GetIDString", (group,),
            (data.Kind.STRING,))
        if reason:
            raise SourceUnavailable(reason)
        if identity == wanted:
            if matched is not None:
                raise SourceUnavailable("CombatUnitGroup.GetIDString is duplicated in the current source")
            matched = group
    if matched is None:
        raise SourceUnavailable(f"Combat unit group native ID string {wanted!r} is not present in the current formation source")
    return matched


async def _type_summary(run, binding, prefix):
    row, unavailable = {"unavailable": {}}, {}
    name, reason = await run.field("CombatUnitType", "GetNameNoFormatting", (binding,),
        (data.Kind.STRING,), expression=prefix + ".GetNameNoFormatting")
    row.update(display_field(name))
    if reason:
        unavailable["name"] = reason
    for method, key in (("GetTooltipTag", "type_tooltip_tag"), ("GetDesc", "description"),
                        ("GetRequirements", "requirements"), ("GetIconString", "icon")):
        value, reason = await run.field("CombatUnitType", method, (binding,), (data.Kind.STRING,),
            expression=prefix + "." + method)
        _record_field(row, unavailable, key, value, reason)
    group_id_string, reason = await run.field("CombatUnitGroup", "GetIDString", (binding,),
        (data.Kind.STRING,), expression=prefix + ".GetGroup.GetIDString")
    _record_field(row, unavailable, "group_id_string", group_id_string, reason)
    group_name, reason = await run.field("CombatUnitGroup", "GetName", (binding,),
        (data.Kind.STRING,), expression=prefix + ".GetGroup.GetName")
    _record_field(row, unavailable, "group_name_raw", group_name, reason)
    row["unavailable"] = unavailable
    row["complete"] = not row["unavailable"]
    return row


async def formation_disband(api, formation_id, *, validate_only=False):
    """Validate or disband one currently owned formation via its original producer."""
    async def operation(run):
        kind, formation = await _formation(run, formation_id)
        if formation is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
        result = {"formation_id": formation_id, "kind": kind, "action": "disband_formation",
                  "accepted": None}
        owner = await _produced_owner(run, "MilitaryFormation.Disband", (formation,))
        accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if accepted:
            try:
                _, current = await _formation(run, formation_id)
                result["post_state"] = {"present_in_owned_sources": current is not None,
                                        "disband_observed": current is None}
            except (SourceUnavailable, KnownResultError) as error:
                result["observation_error"] = str(error)
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military.disband_formation", operation)


async def formation_unit_composition(api, formation_id, *, include_units=True):
    """Read unit-type composition and optional individual unit quality from one formation."""
    async def observe(run):
        kind, formation = await _formation(run, formation_id)
        if formation is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
        await run.provider("MilitaryFormation", "GetCombatUnitEntries")
        entries = []
        async for entry_index, _, entry in run.elements("MilitaryFormation.GetCombatUnitEntries",
                (formation,), "MilitaryFormationUnitEntry"):
            row = {"source_index": entry_index, "unavailable": {}}
            name, reason = await run.field("CombatUnitType", "GetNameNoFormatting", (entry,),
                (data.Kind.STRING,),
                expression="MilitaryFormationUnitEntry.GetUnitType.GetNameNoFormatting")
            row.update(display_field(name))
            if reason:
                row["unavailable"]["name"] = reason
            for method, key, kinds in (
                    ("GetIDString", "entry_id_string", (data.Kind.STRING,)),
                    ("GetCurrentNumRegularUnits", "current_regular", (data.Kind.I32,)),
                    ("GetExpectedNumRegularUnits", "expected_regular", (data.Kind.I32,)),
                    ("GetCurrentNumConscriptUnits", "current_conscripts", (data.Kind.I32,)),
                    ("GetExpectedNumConscriptUnits", "expected_conscripts", (data.Kind.I32,)),
                    ("GetCurrentNumTotalUnits", "current_total", (data.Kind.I32,)),
                    ("GetExpectedNumTotalUnits", "expected_total", (data.Kind.I32,)),
                    ("GetMobilization", "mobilization_fraction", (data.Kind.FIXED,))):
                value, reason = await run.field("MilitaryFormationUnitEntry", method, (entry,), kinds)
                _record_field(row, row["unavailable"], key, value, reason)
            for method, key in (("GetTooltipTag", "type_tooltip_tag"), ("GetDesc", "type_description")):
                value, reason = await run.field("CombatUnitType", method, (entry,),
                    (data.Kind.STRING,), expression="MilitaryFormationUnitEntry.GetUnitType." + method)
                _record_field(row, row["unavailable"], key, value, reason)
            for method, key in (("GetIDString", "group_id_string"), ("GetName", "group_name_raw")):
                value, reason = await run.field("CombatUnitGroup", method, (entry,),
                    (data.Kind.STRING,),
                    expression="MilitaryFormationUnitEntry.GetUnitType.GetGroup." + method)
                _record_field(row, row["unavailable"], key, value, reason)
            row["is_regular_upgrading"], reason = await run.field("MilitaryFormation",
                "IsRegularUnitTypeUpgrading", (formation, entry), (data.Kind.BOOL,), arguments=1,
                expression="MilitaryFormation.IsRegularUnitTypeUpgrading(MilitaryFormationUnitEntry.GetUnitType.Self)")
            if reason:
                row["unavailable"]["is_regular_upgrading"] = reason
            row["is_conscript_upgrading"], reason = await run.field("MilitaryFormation",
                "IsConscriptUnitTypeUpgrading", (formation, entry), (data.Kind.BOOL,), arguments=1,
                expression="MilitaryFormation.IsConscriptUnitTypeUpgrading(MilitaryFormationUnitEntry.GetUnitType.Self)")
            if reason:
                row["unavailable"]["is_conscript_upgrading"] = reason
            units = []
            if include_units:
                try:
                    await run.provider("MilitaryFormationUnitEntry", "GetCombatUnits")
                    async for unit_index, _, unit in run.elements(
                            "MilitaryFormationUnitEntry.GetCombatUnits", (entry,), "CombatUnit"):
                        unit_row = {"unit_source_index": unit_index, "unavailable": {}}
                        unit_name, unit_reason = await run.field("CombatUnit", "GetName", (unit,),
                            (data.Kind.STRING,))
                        unit_row.update(display_field(unit_name))
                        if unit_reason:
                            unit_row["unavailable"]["name"] = unit_reason
                        for method, key, kinds in (
                                ("IsConscript", "conscript", (data.Kind.BOOL,)),
                                ("GetManpowerRaw", "manpower", (data.Kind.I64,)),
                                ("GetMaxManpower", "max_manpower", (data.Kind.I64,)),
                                ("GetMoraleRaw", "morale", (data.Kind.FIXED,)),
                                ("GetOffenseRaw", "offense", (data.Kind.FIXED,)),
                                ("GetDefenseRaw", "defense", (data.Kind.FIXED,)),
                                ("GetCurrentVeterancyLevel", "veterancy_level", (data.Kind.I32,)),
                                ("GetCurrentExperienceTowardsNextLevel", "veterancy_progress", (data.Kind.FIXED,)),
                                ("GetCasualtiesDesc", "casualties", (data.Kind.STRING,))):
                            value, unit_reason = await run.field("CombatUnit", method, (unit,), kinds)
                            _record_field(unit_row, unit_row["unavailable"], key, value, unit_reason)
                        for method, key, kinds in (("GetID", "building_id", (data.Kind.I32, data.Kind.U32)),
                                ("GetNameNoFormatting", "building_name_raw", (data.Kind.STRING,))):
                            value, unit_reason = await run.field("Building", method, (unit,), kinds,
                                expression="CombatUnit.GetBuilding." + method)
                            if key == "building_id":
                                value = run.api._native_full_id(value)
                            _record_field(unit_row, unit_row["unavailable"], key, value, unit_reason)
                        units.append(unit_row)
                except (SourceUnavailable, KnownResultError) as error:
                    row["unavailable"]["units"] = str(error)
            row["units"] = units if include_units else None
            entries.append(row)
        return {"formation_id": formation_id, "kind": kind, "status": "observed",
                "atomic_snapshot": False, "entries": entries}
    return await owned_read(api, "military.formation_unit_composition", observe)


async def _combat_unit_choice_row(run, unit, entry_id_string, entry_source_index, unit_source_index):
    row = {"entry_id_string": entry_id_string, "entry_source_index": entry_source_index,
           "source_index": unit_source_index, "unavailable": {}}
    name, reason = await run.field("CombatUnit", "GetName", (unit,), (data.Kind.STRING,))
    row.update(display_field(name))
    if reason:
        row["unavailable"]["name"] = reason
    for method, key, kinds in (("IsConscript", "conscript", (data.Kind.BOOL,)),
            ("GetManpowerRaw", "manpower", (data.Kind.I64,)),
            ("GetMaxManpower", "max_manpower", (data.Kind.I64,)),
            ("GetMoraleRaw", "morale", (data.Kind.FIXED,)),
            ("GetOffenseRaw", "offense", (data.Kind.FIXED,)),
            ("GetDefenseRaw", "defense", (data.Kind.FIXED,)),
            ("GetCurrentVeterancyLevel", "veterancy_level", (data.Kind.I32,)),
            ("GetCurrentExperienceTowardsNextLevel", "veterancy_progress", (data.Kind.FIXED,)),
            ("GetCasualtiesDesc", "casualties", (data.Kind.STRING,))):
        value, reason = await run.field("CombatUnit", method, (unit,), kinds)
        _record_field(row, row["unavailable"], key, value, reason)
    for method, key, kinds in (("GetID", "building_id", (data.Kind.I32, data.Kind.U32)),
            ("GetNameNoFormatting", "building_name_raw", (data.Kind.STRING,))):
        value, reason = await run.field("Building", method, (unit,), kinds,
            expression="CombatUnit.GetBuilding." + method)
        if key == "building_id":
            value = run.api._native_full_id(value)
        _record_field(row, row["unavailable"], key, value, reason)
    row["complete"] = not row["unavailable"]
    return row


async def combat_unit_choices(api, formation_id, *, entry_id_string=None, perform=True):
    """Return exact owned CombatUnit values; weak/raw source rows are rejected."""
    from .._choices import OwnedChoices
    from ._warfare import WarfareRead
    if entry_id_string is not None:
        entry_id_string = _identity_text(entry_id_string, "entry_id_string")
    async def read():
        run = WarfareRead(api, "military.combat_unit_choices")
        report = _choice_report("MilitaryFormationUnitEntry.GetCombatUnits")
        catalog = OwnedChoices(run, metadata={"source_kind": "combat_units",
            "formation_id": formation_id, "entry_id_string": entry_id_string, "result": report})
        try:
            kind, formation = await _formation(run, formation_id)
            if formation is None:
                raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
            report["formation_id"], report["kind"] = formation_id, kind
            await run.provider("MilitaryFormation", "GetCombatUnitEntries")
            matched_entry = entry_id_string is None
            async for entry_source_index, entry_result, entry in _owned_choice_elements(run, catalog,
                    "MilitaryFormation.GetCombatUnitEntries", (formation,),
                    "MilitaryFormationUnitEntry", count_source=False):
                identity, reason = await run.field("MilitaryFormationUnitEntry", "GetIDString",
                    (entry,), (data.Kind.STRING,))
                if reason:
                    report["reasons"].append({"entry_source_index": entry_source_index,
                        "field": "MilitaryFormationUnitEntry.GetIDString", "reason": reason})
                    continue
                if entry_id_string is not None and identity != entry_id_string:
                    continue
                matched_entry = True
                await run.provider("MilitaryFormationUnitEntry", "GetCombatUnits")
                async for unit_source_index, unit_result, unit in _owned_choice_elements(run, catalog,
                        "MilitaryFormationUnitEntry.GetCombatUnits", (entry,), "CombatUnit"):
                    row = await _combat_unit_choice_row(run, unit, identity,
                        entry_source_index, unit_source_index)
                    catalog.add(unit_result, "CombatUnit", unit, row)
            if not matched_entry:
                report["reasons"].append({"entry_id_string": entry_id_string,
                    "reason": "native entry ID string is not present in the current formation source"})
            return _finish_choice_catalog(catalog)
        except BaseException as error:
            report["status"], report["complete"] = "retained_after_error", False
            report["failure"] = error
            report["native_results"] = tuple(run.operations)
            error.choices = catalog
            raise
    return (await api._ctx.perform("military.combat_unit_choices", read)
            if perform else await read())


async def disband_combat_unit(api, unit_choice, *, validate_only=False, perform=True):
    """Disband one exact retained CombatUnit choice with its original producer."""
    catalog = _choice_input(unit_choice, "CombatUnit", api._ctx, "combat_units")
    formation_id = catalog.metadata["formation_id"]
    from ._warfare import WarfareRead
    async def work():
        async with unit_choice.borrow(api._ctx) as lease:
            run = WarfareRead(api, "military.disband_combat_unit_choice")
            unit = lease.attach(run)
            result = {"formation_id": formation_id, "action": "disband_combat_unit",
                      "unit": dict(unit_choice), "accepted": None}
            try:
                kind, formation = await _formation(run, formation_id)
                if formation is None:
                    raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
                result["kind"] = kind
                owner = await _produced_owner(run,
                    "MilitaryFormation.GetDisbandUnitCommand(CombatUnit.Self)", (formation, unit))
                await _validate_submit(run, owner, result, validate_only=validate_only)
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                result.update(status="unavailable", reason=str(error), retryable=True)
                if isinstance(error, KnownResultError):
                    result.update(native_result=error.result, native_error=error.result.error)
            return await run.finish(result)
    return (await api._ctx.perform("military.disband_combat_unit_choice", work)
            if perform else await work())


async def formation_recruit_options(api, formation_id):
    """Read current native group/type sources and both regular/conscript predicates."""
    async def observe(run):
        kind, formation = await _formation(run, formation_id)
        if formation is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
        await run.provider("MilitaryFormation", "GetEligibleGroups")
        groups = []
        async for group_index, _, group in run.elements("MilitaryFormation.GetEligibleGroups",
                (formation,), "CombatUnitGroup"):
            group_row = {"source_index": group_index, "unavailable": {}, "unit_types": []}
            for method, key in (("GetIDString", "group_id_string"), ("GetName", "name_raw"),
                                ("GetDesc", "description")):
                value, reason = await run.field("CombatUnitGroup", method, (group,), (data.Kind.STRING,))
                _record_field(group_row, group_row["unavailable"], key, value, reason)
            await run.provider("MilitaryFormation", "GetEligibleTypesByGroup", 1)
            async for type_index, _, unit_type in run.elements(
                    "MilitaryFormation.GetEligibleTypesByGroup(CombatUnitGroup.Self)",
                    (formation, group), "CombatUnitType"):
                type_row = await _type_summary(run, unit_type, "CombatUnitType")
                type_row["source_index"] = type_index
                for method, key in (("CanRecruitRegularUnit", "can_recruit_regular"),
                                    ("CanRecruitConscriptUnit", "can_recruit_conscript")):
                    value, reason = await run.field("MilitaryFormation", method,
                        (formation, unit_type), (data.Kind.BOOL,), arguments=1,
                        expression="MilitaryFormation." + method + "(CombatUnitType.Self)")
                    _record_field(type_row, type_row["unavailable"], key, value, reason)
                group_row["unit_types"].append(type_row)
            selected, reason = await run.field("CombatUnitType", "GetTooltipTag", (formation, group),
                (data.Kind.STRING,),
                expression="MilitaryFormation.GetSelectedUnitTypeForGroup(CombatUnitGroup.Self).GetTooltipTag")
            _record_field(group_row, group_row["unavailable"], "selected_type_tooltip_tag", selected, reason)
            groups.append(group_row)
        return {"formation_id": formation_id, "kind": kind, "status": "observed",
                "atomic_snapshot": False, "groups": groups,
                "regular_recruit_action_available_via_owned_choice": True,
                "regular_recruit_action_note":
                    "Use combat_unit_type_choices(mode='regular') and recruit_regular_unit with one complete Building ID; the original producer is Building.QueueMilitaryConstruction",
                "conscript_map_opener_available_via_owned_choice": True,
                "conscript_recruit_action_available": False,
                "conscript_recruit_action_unavailable_reason":
                    "open_conscript_recruitment_map invokes the original opener; final MapListOption.OnClick still requires the owned current MapListPanel source"}
    return await owned_read(api, "military.formation_recruit_options", observe)


_UNIT_ACTIONS = {
    "upgrade_regular_type": ("entry", "MilitaryFormation.GetUpgradeRegularUnitTypeCommand(MilitaryFormationUnitEntry.GetUnitType.Self)"),
    "upgrade_conscript_type": ("entry", "MilitaryFormation.GetUpgradeConscriptUnitTypeCommand(MilitaryFormationUnitEntry.GetUnitType.Self)"),
    "upgrade_group": ("group", "MilitaryFormation.GetUpgradeUnitGroupCommand(CombatUnitGroup.Self)"),
    "set_default_type": ("entry", "MilitaryFormation.GetSetDefaultUnitTypeCommand(MilitaryFormationUnitEntry.GetUnitType.Self)"),
    "disband_regular_type": ("entry", "MilitaryFormation.GetDisbandUnitTypeRegularCommand(MilitaryFormationUnitEntry.GetUnitType.Self)"),
    "disband_conscript_type": ("entry", "MilitaryFormation.GetDisbandUnitTypeConscriptCommand(MilitaryFormationUnitEntry.GetUnitType.Self)"),
}


async def formation_unit_action(api, formation_id, action, *, entry_id_string=None,
        group_id_string=None, validate_only=False):
    """Act on a row re-resolved by its original native ID string.

    Individual CombatUnit disband is not accepted by ID/name/index because no
    complete CombatUnit ID getter is proved. Use combat_unit_choices and pass one
    exact retained OwnedChoice to disband_combat_unit instead.
    """
    if action not in _UNIT_ACTIONS:
        raise ValueError("unknown or identity-unsafe formation unit action")
    selector, expression = _UNIT_ACTIONS[action]
    if selector == "entry":
        entry_id_string = _identity_text(entry_id_string, "entry_id_string")
    else:
        group_id_string = _identity_text(group_id_string, "group_id_string")

    async def operation(run):
        kind, formation = await _formation(run, formation_id)
        if formation is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
        result = {"formation_id": formation_id, "kind": kind, "action": action,
                  "accepted": None}
        if selector == "entry":
            entry = await _entry_by_id(run, formation, entry_id_string)
            bindings = (formation, entry)
            result["entry_id_string"] = entry_id_string
        else:
            group = await _group_by_id(run, formation, group_id_string)
            bindings = (formation, group)
            result["group_id_string"] = group_id_string
        if action in ("upgrade_regular_type", "upgrade_conscript_type"):
            method = ("IsRegularUnitTypeUpgrading" if action == "upgrade_regular_type" else
                      "IsConscriptUnitTypeUpgrading")
            before, reason = await run.field("MilitaryFormation", method, (formation, entry),
                (data.Kind.BOOL,), arguments=1,
                expression="MilitaryFormation." + method +
                           "(MilitaryFormationUnitEntry.GetUnitType.Self)")
            result["upgrading_before"] = before
            if reason:
                result.setdefault("unavailable", {})["upgrading_before"] = reason
        owner = await _produced_owner(run, expression, bindings)
        accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if accepted:
            try:
                if action in ("upgrade_regular_type", "upgrade_conscript_type"):
                    after, reason = await run.field("MilitaryFormation", method, (formation, entry),
                        (data.Kind.BOOL,), arguments=1,
                        expression="MilitaryFormation." + method +
                                   "(MilitaryFormationUnitEntry.GetUnitType.Self)")
                    result["upgrading_after"] = after
                    if reason:
                        result.setdefault("unavailable", {})["upgrading_after"] = reason
                if action == "set_default_type":
                    selected, reason = await run.field("CombatUnitType", "GetTooltipTag",
                        (formation, entry), (data.Kind.STRING,),
                        expression="MilitaryFormation.GetSelectedUnitTypeForGroup("
                                   "MilitaryFormationUnitEntry.GetUnitType.GetGroup.Self).GetTooltipTag")
                    result["selected_default_tooltip_tag"] = selected
                    if reason:
                        result.setdefault("unavailable", {})["selected_default_tooltip_tag"] = reason
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military." + action, operation)


async def _mobilization_option_row(run, option, formation, source_index):
    row = {"source_index": source_index, "unavailable": {}}
    name, reason = await run.field("MobilizationOption", "GetNameNoFormatting", (option,),
        (data.Kind.STRING,))
    row.update(display_field(name))
    if reason:
        row["unavailable"]["name"] = reason
    for method, key in (("GetDesc", "description"), ("GetTooltip", "tooltip"),
                        ("GetTooltipTag", "option_tooltip_tag")):
        value, reason = await run.field("MobilizationOption", method, (option,),
            (data.Kind.STRING,))
        _record_field(row, row["unavailable"], key, value, reason)
    for expression, key in (("MobilizationOption.GetUnitModifier.GetDesc", "effects"),
            ("MobilizationOption.GetUpkeepModifier.GetDesc", "goods_cost"),
            ("MobilizationOption.GetUpkeepUnscaledModifier.GetDesc", "goods_cost_unscaled")):
        value, reason = await run.field("Modifier", "GetDesc", (option,),
            (data.Kind.STRING,), expression=expression)
        _record_field(row, row["unavailable"], key, value, reason)
    for method, key in (("GetTooltipWithActivateDesc", "activate_description"),
                        ("GetTooltipWithDeactivateDesc", "deactivate_description")):
        value, reason = await run.field("MobilizationOption", method, (option, formation),
            (data.Kind.STRING,), arguments=1,
            expression="MobilizationOption." + method + "(MilitaryFormation.AccessSelf)")
        _record_field(row, row["unavailable"], key, value, reason)
    for method, key in (("IsActivated", "activated"), ("IsPossible", "possible"),
                        ("CanBeTurnedOff", "can_be_turned_off")):
        value, reason = await run.field("MobilizationOption", method, (option, formation),
            (data.Kind.BOOL,), arguments=1,
            expression="MobilizationOption." + method + "(MilitaryFormation.AccessSelf)")
        _record_field(row, row["unavailable"], key, value, reason)
    row["complete"] = not row["unavailable"]
    return row


async def mobilization_options(api, formation_id):
    """Copy current option descriptions; source indexes remain read-only diagnostics."""
    async def observe(run):
        kind, formation = await _formation(run, formation_id)
        if formation is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
        await run.provider("MilitaryFormation", "GetMobilizationOptions")
        options = []
        async for source_index, _, option in run.elements("MilitaryFormation.GetMobilizationOptions",
                (formation,), "MobilizationOption"):
            options.append(await _mobilization_option_row(run, option, formation, source_index))
        return {"formation_id": formation_id, "kind": kind, "status": "observed",
                "atomic_snapshot": False, "options": options,
                "mutation_available_via_owned_choice": True,
                "mutation_note": "Use mobilization_option_choices and pass one original OwnedChoice; never pass source_index or tooltip text"}
    return await owned_read(api, "military.mobilization_options", observe)


async def mobilization_option_choices(api, formation_id, *, perform=True):
    """Return closable options backed by their genuine retained CPdxPtr values."""
    from .._choices import OwnedChoices
    from ._warfare import WarfareRead
    async def read():
        run = WarfareRead(api, "military.mobilization_option_choices")
        report = _choice_report("MilitaryFormation.GetMobilizationOptions")
        catalog = OwnedChoices(run, metadata={"source_kind": "mobilization_options",
            "formation_id": formation_id, "result": report})
        try:
            kind, formation = await _formation(run, formation_id)
            if formation is None:
                raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
            report["formation_id"], report["kind"] = formation_id, kind
            await run.provider("MilitaryFormation", "GetMobilizationOptions")
            async for source_index, element, option in _owned_choice_elements(run, catalog,
                    "MilitaryFormation.GetMobilizationOptions", (formation,), "MobilizationOption"):
                row = await _mobilization_option_row(run, option, formation, source_index)
                catalog.add(element, "MobilizationOption", option, row)
            return _finish_choice_catalog(catalog)
        except BaseException as error:
            report["status"], report["complete"] = "retained_after_error", False
            report["failure"] = error
            report["native_results"] = tuple(run.operations)
            error.choices = catalog
            raise
    return (await api._ctx.perform("military.mobilization_option_choices", read)
            if perform else await read())


_TOGGLE = object()


async def _mobilization_choice_action(api, option_choice, desired, *, validate_only, perform, label):
    catalog = _choice_input(option_choice, "MobilizationOption", api._ctx, "mobilization_options")
    formation_id = catalog.metadata["formation_id"]
    from ._warfare import WarfareRead
    async def work():
        async with option_choice.borrow(api._ctx) as lease:
            run = WarfareRead(api, "military." + label)
            option = lease.attach(run)
            action = "toggle_mobilization_option" if desired is _TOGGLE else "set_mobilization_option"
            result = {"formation_id": formation_id, "action": action,
                      "option": dict(option_choice), "accepted": None}
            if desired is not _TOGGLE:
                result["activated"] = desired
            try:
                kind, formation = await _formation(run, formation_id)
                if formation is None:
                    raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
                result["kind"] = kind
                before, reason = await run.field("MobilizationOption", "IsActivated",
                    (option, formation), (data.Kind.BOOL,), arguments=1,
                    expression="MobilizationOption.IsActivated(MilitaryFormation.AccessSelf)")
                result["activated_before"] = before
                if reason:
                    result.setdefault("unavailable", {})["activated_before"] = reason
                if desired is not _TOGGLE and (reason or type(before) is not bool):
                    result.update(status="unavailable", eligible=None,
                        reason=reason or "Current native activation state is not a known bool")
                elif desired is not _TOGGLE and before is desired:
                    result.update(status="unchanged", eligible=None, accepted=None,
                                  activated_after=before)
                else:
                    owner = await _produced_owner(run,
                        "MobilizationOption.Toggle(MilitaryFormation.AccessSelf)", (option, formation))
                    accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
                    if accepted:
                        try:
                            after, after_reason = await run.field("MobilizationOption", "IsActivated",
                                (option, formation), (data.Kind.BOOL,), arguments=1,
                                expression="MobilizationOption.IsActivated(MilitaryFormation.AccessSelf)")
                            result["activated_after"] = after
                            if after_reason:
                                result.setdefault("unavailable", {})["activated_after"] = after_reason
                        except Exception as error:
                            run.retain_unknown = True
                            result["observation_error"] = str(error)
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                result.update(status="unavailable", reason=str(error), retryable=True)
                if isinstance(error, KnownResultError):
                    result.update(native_result=error.result, native_error=error.result.error)
            return await run.finish(result)
    return (await api._ctx.perform("military." + label, work) if perform else await work())


async def toggle_mobilization_option(api, option_choice, *, validate_only=False, perform=True):
    """Toggle one exact retained MobilizationOption choice once."""
    return await _mobilization_choice_action(api, option_choice, _TOGGLE,
        validate_only=validate_only, perform=perform, label="toggle_mobilization_option_choice")


async def set_mobilization_option(api, option_choice, activated, *, validate_only=False, perform=True):
    """Set one retained option to a desired bool without blind or redundant toggle."""
    if type(activated) is not bool:
        raise TypeError("activated must be bool")
    return await _mobilization_choice_action(api, option_choice, activated,
        validate_only=validate_only, perform=perform, label="set_mobilization_option_choice")


async def _candidate(run, wanted, role=None):
    country = await run.country()
    sources = (("AccessGeneralsOffer", "general"), ("AccessAdmiralsOffer", "admiral"))
    for method, source_role in sources:
        if role is not None and source_role != role:
            continue
        await run.provider("Country", method)
        async for _, _, candidate in run.elements("Country." + method, (country,), "Character"):
            identity, reason = await run.field("Character", "GetID", (candidate,),
                (data.Kind.I32, data.Kind.U32))
            if reason:
                raise SourceUnavailable(reason)
            if run.api._native_full_id(identity) == wanted:
                return source_role, candidate
    raise SourceUnavailable(f"Commander candidate {wanted} was not present in the country's current offer sources")


async def _character_name(run, character, row, unavailable):
    fields = await run.functions("Character")
    getter = next((name for name in ("GetFullNameNoFormatting", "GetFullName", "GetFullNameWithTitle")
                   if name in fields), "GetFullName")
    name, reason = await run.field("Character", getter, (character,), (data.Kind.STRING,))
    row.update(display_field(name))
    if reason:
        unavailable["name"] = reason


async def _traits(run, character):
    rows = []
    try:
        await run.provider("Character", "AccessTraits")
        async for index, _, trait in run.elements("Character.AccessTraits", (character,), "CharacterTrait"):
            row = {"source_index": index, "unavailable": {}}
            name, reason = await run.field("CharacterTrait", "GetNameNoFormatting", (trait,),
                (data.Kind.STRING,))
            row.update(display_field(name))
            if reason:
                row["unavailable"]["name"] = reason
            for method, key in (("GetTooltipTag", "trait_tooltip_tag"), ("GetDesc", "description"),
                                ("GetModifiers", "modifiers")):
                value, reason = await run.field("CharacterTrait", method, (trait,),
                    (data.Kind.STRING,))
                _record_field(row, row["unavailable"], key, value, reason)
            rows.append(row)
        return rows, None
    except (SourceUnavailable, KnownResultError) as error:
        return None, str(error)


async def commander_candidates(api, *, role=None):
    """Read offer fields only; command eligibility is a selected-candidate call."""
    if role not in (None, "general", "admiral"):
        raise ValueError("role must be general, admiral or None")
    async def observe(run):
        country = await run.country()
        rows, failures = [], []
        for method, source_role in (("AccessGeneralsOffer", "general"),
                                    ("AccessAdmiralsOffer", "admiral")):
            if role is not None and role != source_role:
                continue
            try:
                await run.provider("Country", method)
                async for source_index, _, candidate in run.elements("Country." + method,
                        (country,), "Character"):
                    row = {"role": source_role, "source_index": source_index, "country_id": run.actor,
                           "unavailable": {}}
                    identity, reason = await run.field("Character", "GetID", (candidate,),
                        (data.Kind.I32, data.Kind.U32))
                    row["id"] = run.api._native_full_id(identity)
                    if row["id"] is None:
                        row["unavailable"]["id"] = reason or "Candidate did not return a complete native ID"
                    await _character_name(run, candidate, row, row["unavailable"])
                    for field, key, kinds in (("GetRankName", "rank", (data.Kind.STRING,)),
                            ("GetCommandLimitRaw", "command_limit", (data.Kind.I32,))):
                        value, reason = await run.field("Character", field, (candidate,), kinds)
                        _record_field(row, row["unavailable"], key, value, reason)
                    row["traits"], reason = await _traits(run, candidate)
                    if reason:
                        row["unavailable"]["traits"] = reason
                    row["eligible"] = None
                    row["unavailable"]["eligible"] = (
                        "Eligibility is validated only for an explicitly selected candidate via commander_recruit_options")
                    row["cost"] = None
                    row["unavailable"]["cost"] = (
                        "No pure native scalar recruitment-cost getter is evidenced; the default offer list does not materialize commands")
                    rows.append(row)
            except (SourceUnavailable, KnownResultError) as error:
                failures.append({"source": "Country." + method, "reason": str(error)})
        return {"status": "partial" if failures and rows else "unavailable" if failures else "observed",
                "country_id": run.actor, "atomic_snapshot": False,
                "candidates": rows if rows or not failures else None, "details": failures}
    return await owned_read(api, "military.commander_candidates", observe)


async def commander_recruit_options(api, candidate_id, *, formation_id=None):
    """Validate exactly one selected current offer without submitting it."""
    return await recruit_commander(api, candidate_id, formation_id=formation_id, validate_only=True)


async def recruit_commander(api, candidate_id, *, formation_id=None, validate_only=False):
    """Recruit one current offer, optionally into one currently owned formation."""
    async def operation(run):
        role, candidate = await _candidate(run, candidate_id)
        result = {"candidate_id": candidate_id, "role": role,
                  "action": "recruit_commander", "accepted": None}
        if formation_id is None:
            expression, bindings = "Character.GetRecruitCommandNoFormation", (candidate,)
        else:
            kind, formation = await _formation(run, formation_id)
            if formation is None:
                raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
            if (role == "general") is not (kind == "army"):
                raise SourceUnavailable("Candidate role does not match the selected formation kind")
            result.update(formation_id=formation_id, formation_kind=kind)
            expression, bindings = "Character.GetRecruitCommand(MilitaryFormation.Self)", (candidate, formation)
        owner = await _produced_owner(run, expression, bindings)
        accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if accepted:
            try:
                await _character(run, candidate_id)
            except SourceUnavailable:
                result["observed_in_owned_commander_sources"] = False
            except KnownResultError as error:
                result["observation_error"] = str(error)
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
            else:
                result["observed_in_owned_commander_sources"] = True
        return result
    return await owned_read(api, "military.recruit_commander", operation)


async def _promotion_fields(run, character):
    row, unavailable = {"unavailable": {}}, {}
    for method, key in (("GetRankName", "current_rank"),
                        ("GetNextCommanderRankBenefits", "next_rank_benefits")):
        value, reason = await run.field("Character", method, (character,), (data.Kind.STRING,))
        _record_field(row, unavailable, key, value, reason)
    value, reason = await run.field("CommanderRank", "GetTooltipTag", (character,),
        (data.Kind.STRING,), expression="Character.GetNextCommanderRank.GetTooltipTag")
    _record_field(row, unavailable, "next_rank_tooltip_tag", value, reason)
    row["unavailable"] = unavailable
    return row


async def commander_promotion(api, character_id, *, validate_only=False):
    """Read rank transition fields and validate or submit GetPromoteCommand once."""
    async def operation(run):
        character = await _character(run, character_id)
        before = await _promotion_fields(run, character)
        result = {"character_id": character_id, "action": "promote_commander",
                  "before": before, "accepted": None}
        owner = await _produced_owner(run, "Character.GetPromoteCommand", (character,))
        accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if accepted:
            try:
                result["after"] = await _promotion_fields(run, character)
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military.promote_commander", operation)


async def commander_interactions(api, character_id):
    """Copy career descriptions; owned choices select the original void action."""
    async def observe(run):
        character = await _character(run, character_id)
        await run.provider("Character", "GetInteractions")
        options = []
        async for index, _, interaction in run.elements("Character.GetInteractions",
                (character,), "CharacterInteraction"):
            row = {"source_index": index, "unavailable": {}}
            name, reason = await run.field("CharacterInteraction", "GetName", (interaction,),
                (data.Kind.STRING,))
            row.update(display_field(name))
            if reason:
                row["unavailable"]["name"] = reason
            for method, key, arguments in (("GetTooltipTag", "interaction_tooltip_tag", 0),
                    ("GetDesc", "description", 0), ("GetInfo", "info", 0),
                    ("GetRequirements", "requirements", 1), ("GetEffects", "effects", 1),
                    ("GetCommandDesc", "command_description", 1),
                    ("GetCooldownRemaining", "cooldown_remaining", 1)):
                bindings = (interaction,) if arguments == 0 else (interaction, character)
                expression = ("CharacterInteraction." + method if arguments == 0 else
                              "CharacterInteraction." + method + "(Character.Self)")
                value, reason = await run.field("CharacterInteraction", method, bindings,
                    (data.Kind.STRING,), arguments=arguments, expression=expression)
                _record_field(row, row["unavailable"], key, value, reason)
            for method, key in (("HasCooldown", "has_cooldown"), ("IsOnCooldown", "on_cooldown")):
                value, reason = await run.field("CharacterInteraction", method,
                    (interaction, character), (data.Kind.BOOL,), arguments=1,
                    expression="CharacterInteraction." + method + "(Character.Self)")
                _record_field(row, row["unavailable"], key, value, reason)
            row["eligible"] = None
            row["validity_checked"] = False
            row["action_available_via_owned_choice"] = True
            row["action_note"] = (
                "Use commander_choices, commander_interaction_choices and invoke_commander_interaction; the selected original IsCommandValid predicate and ExecuteCommand property callback are each invoked once")
            options.append(row)
        return {"character_id": character_id, "status": "observed", "atomic_snapshot": False,
                "interactions": options}
    return await owned_read(api, "military.commander_interactions", observe)
