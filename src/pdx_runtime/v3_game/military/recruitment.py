"""Owned unit-recruitment choices and original recruitment actions.

Regular recruitment uses the GUI's real Building.QueueMilitaryConstruction
command. Conscript OnClick is exposed only as the map-interaction opener until an
owned current MapListPanel source is available; opening it is not recruitment.
"""
from __future__ import annotations

from ... import v3_data as data, v3_objects as objects
from .._choices import OwnedChoice, OwnedChoices
from .._choice_sources import _choice_elements, _choice_binding, _finish_choice_report
from .._text import display_field
from .._projection import _projection_value
from ._warfare import (WarfareRead, SourceUnavailable, KnownResultError,
    _formation, _produced_owner, _validate_submit)
from .._run import KnownResultError, SourceUnavailable


def _report(source):
    return {"source": source, "status": "building", "complete": False,
            "reasons": [], "rejected_rows": [], "native_pages": [], "sources": []}


def _choice_input(choice, named_type, context, source_kind):
    if not isinstance(choice, OwnedChoice) or choice.native_type != named_type:
        raise TypeError("pass an original owned " + named_type + " choice")
    catalog = choice._catalog
    catalog._ensure_open()
    if not any(item is choice for item in catalog):
        raise ValueError("choice is not admitted by its original catalog")
    if catalog.context is not context or catalog.native is not context.native or catalog.process is not context.process:
        raise ValueError("choice belongs to another original context/native client/process")
    if catalog.metadata.get("source_kind") != source_kind:
        raise ValueError("choice belongs to another original source kind")
    return catalog


async def _type_row(run, unit_type, formation, group, mode, group_id, group_source, type_source):
    row = {"mode": mode, "group_id_string": group_id,
           "group_source_index": group_source, "source_index": type_source,
           "unavailable": {}, "validity_checked": False}
    name, reason = await run.field("CombatUnitType", "GetNameNoFormatting", (unit_type,),
        (data.Kind.STRING,))
    row.update(display_field(name))
    if reason:
        row["unavailable"]["name"] = reason
    for method, key in (("GetTooltipTag", "type_tooltip_tag"),
                        ("GetDesc", "description"), ("GetRequirements", "requirements")):
        value, reason = await run.field("CombatUnitType", method, (unit_type,),
            (data.Kind.STRING,))
        row[key] = value
        if reason:
            row["unavailable"][key] = reason
    predicate = "CanRecruitRegularUnit" if mode == "regular" else "CanRecruitConscriptUnit"
    available, reason = await run.field("MilitaryFormation", predicate,
        (formation, unit_type), (data.Kind.BOOL,), arguments=1,
        expression="MilitaryFormation." + predicate + "(CombatUnitType.Self)")
    row["available"] = available
    if reason:
        row["unavailable"]["available"] = reason
    row["complete"] = not row["unavailable"]
    return row


async def combat_unit_type_choices(api, formation_id, *, mode="regular", perform=True):
    """Return retained eligible CombatUnitType choices for one formation."""
    if mode not in ("regular", "conscript"):
        raise ValueError("mode must be regular or conscript")
    async def read():
        run = WarfareRead(api, "military.combat_unit_type_choices")
        report = _report("MilitaryFormation.GetEligibleTypesByGroup")
        catalog = OwnedChoices(run, metadata={"source_kind": "recruit_unit_types",
            "formation_id": formation_id, "mode": mode, "result": report})
        try:
            kind, formation = await _formation(run, formation_id)
            if formation is None:
                raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
            report.update(formation_id=formation_id, kind=kind, mode=mode)
            await run.provider("MilitaryFormation", "GetEligibleGroups")
            group_type = await run.type_id("CombatUnitGroup")
            unit_type_id = await run.type_id("CombatUnitType")
            async for group_source, group_value in _choice_elements(run, catalog,
                    "MilitaryFormation.GetEligibleGroups", (formation,)):
                group = _choice_binding(run, catalog, group_value, "CombatUnitGroup", group_type,
                    "MilitaryFormation.GetEligibleGroups", group_source)
                if group is None:
                    continue
                group_id, reason = await run.field("CombatUnitGroup", "GetIDString", (group,),
                    (data.Kind.STRING,))
                if reason or not isinstance(group_id, str) or not group_id:
                    report["reasons"].append({"group_source_index": group_source,
                        "field": "CombatUnitGroup.GetIDString", "reason": reason or "missing native ID string"})
                    continue
                await run.provider("MilitaryFormation", "GetEligibleTypesByGroup", 1)
                async for type_source, type_value in _choice_elements(run, catalog,
                        "MilitaryFormation.GetEligibleTypesByGroup(CombatUnitGroup.Self)",
                        (formation, group)):
                    unit_type = _choice_binding(run, catalog, type_value, "CombatUnitType",
                        unit_type_id, "MilitaryFormation.GetEligibleTypesByGroup", type_source)
                    if unit_type is None:
                        continue
                    row = await _type_row(run, unit_type, formation, group, mode,
                        group_id, group_source, type_source)
                    catalog.add(type_value, "CombatUnitType", unit_type, row)
            return _finish_choice_report(catalog)
        except BaseException as error:
            report["status"], report["complete"] = "retained_after_error", False
            report["native_results"] = tuple(run.operations)
            report["failure"] = error
            error.choices = catalog
            raise
    return await api._ctx.perform("military.combat_unit_type_choices", read) if perform else await read()


def _full_id(value, name):
    if type(value) is not int or not 0 <= value < 0xffffffff:
        raise ValueError(name + " must be a complete uint32 ID; zero is valid")
    return value


async def _object_records(run, domain, *, report):
    rows, start = [], 0
    while True:
        page = await run.capture("recruit." + domain.name.lower() + ".page",
            lambda start=start: objects.enumerate_objects(run.native, domain,
                start_index=start, max_items=1024, adapter=run.adapters["objects"],
                process=run.process))
        report.setdefault("native_object_pages", []).append(page)
        for row in page.records:
            if row.error:
                report["reasons"].append({"domain": domain.name,
                    "reason": "native object record error " + str(row.error),
                    "native_record": row})
            else:
                rows.append(row)
        if page.error:
            report["reasons"].append({"domain": domain.name,
                "reason": "native object page error " + str(page.error),
                "native_result": page})
            return rows
        if not page.truncated:
            return rows
        if page.next_index <= start:
            report["reasons"].append({"domain": domain.name,
                "reason": "native object pagination did not advance",
                "native_result": page})
            return rows
        start = page.next_index


async def regular_recruitment_options(api, type_choice, *, state_id=None, perform=True):
    """Copy owned military-building full IDs for one retained regular unit type.

    This is a candidate directory, not an eligibility gate. The selected native
    command validator decides whether any complete Building ID is legal.
    """
    types = _choice_input(type_choice, "CombatUnitType", api._ctx, "recruit_unit_types")
    if types.metadata.get("mode") != "regular":
        raise ValueError("regular recruitment options require a regular unit type choice")
    if state_id is not None:
        state_id = _full_id(state_id, "state_id")
    formation_id = types.metadata["formation_id"]
    async def read():
        async with type_choice.borrow(api._ctx) as type_lease:
            run = WarfareRead(api, "military.regular_recruitment_options")
            unit_type = type_lease.attach(run)
            result = {"formation_id": formation_id, "state_id": state_id,
                      "unit_type": dict(type_choice), "status": "building",
                      "options": [], "reasons": [], "native_pages": [],
                      "native_object_pages": [], "complete": False}
            try:
                kind, formation = await _formation(run, formation_id)
                if formation is None or kind != "army":
                    raise SourceUnavailable("Regular recruitment requires an owned army formation")
                country_id = await run.ctx.country_id()
                states = await _object_records(run, objects.Domain.STATE, report=result)
                owned_states = {row.resolved_id for row in states if row.owner_id == country_id}
                if state_id is not None:
                    owned_states &= {state_id}
                buildings = [row for row in await _object_records(run, objects.Domain.BUILDING, report=result)
                             if row.state_id in owned_states]
                building_type = await run.type_id("Building")
                fields = (
                    "Building.GetBuildingType.RecruitsCombatUnits",
                    "Building.GetNameNoFormatting",
                    "Building.GetState.GetNameNoFormatting",
                    "Building.GetBuildingType.GetNameNoFormatting",
                    "Building.GetCombatUnitCapacity",
                    "Building.GetNumUnitsOfCombatUnitTypeProvidedToFormation(CombatUnitType.Self, MilitaryFormation.Self)",
                    "Building.GetQueueMilitaryConstructionTooltip(MilitaryFormation.Self, CombatUnitType.Self)",
                )
                for start in range(0, len(buildings), 64):
                    batch = buildings[start:start + 64]
                    bindings = tuple((data.ObjectBinding.building(building_type, row.resolved_id),
                                      formation, unit_type) for row in batch)
                    page = await run.capture("recruit.buildings.project", lambda bindings=bindings:
                        data.project_page(run.native, fields, bindings, limit=64,
                            adapter=run.adapters["data"], process=run.process))
                    result["native_pages"].append(page)
                    if (page.error or not page.count_valid or page.fields_count != len(fields)
                            or page.row_count != len(batch)):
                        result["reasons"].append({"batch_start": start,
                            "reason": "native building projection is incomplete",
                            "native_result": page})
                    cells = {}
                    for cell in page.cells:
                        cells.setdefault(cell.row, {})[cell.field] = cell
                    for index, record in enumerate(batch):
                        copied = cells.get(index, {})
                        values, reasons = {}, {}
                        for position, name in enumerate(("recruits_combat_units", "name_raw",
                                "state_name_raw", "building_type_name_raw", "combat_unit_capacity",
                                "current_units_for_formation", "queue_tooltip_raw")):
                            value, reason = _projection_value(copied.get(position))
                            values[name] = value
                            if reason:
                                reasons[name] = reason
                        recruits = values["recruits_combat_units"]
                        if recruits is False and "recruits_combat_units" not in reasons:
                            continue
                        if type(recruits) is not bool:
                            reasons.setdefault("recruits_combat_units",
                                "native building recruitment capability is not a known bool")
                        name_fields = display_field(values.pop("name_raw"), "name")
                        state_fields = display_field(values.pop("state_name_raw"), "state_name")
                        type_fields = display_field(values.pop("building_type_name_raw"), "building_type_name")
                        tooltip_fields = display_field(values.pop("queue_tooltip_raw"), "queue_tooltip")
                        result["options"].append({"building_id": record.resolved_id,
                            "state_id": record.state_id, "levels": record.levels,
                            **name_fields, **state_fields, **type_fields, **tooltip_fields,
                            **values, "reasons": reasons,
                            "complete": not reasons and not page.error,
                            "native_eligibility_checked": False})
                result["complete"] = (not result["reasons"]
                    and all(row["complete"] for row in result["options"]))
                result["status"] = "observed" if result["complete"] else "partial"
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                result.update(status="unavailable", reason=str(error))
                if isinstance(error, KnownResultError):
                    result.update(native_result=error.result, native_error=error.result.error)
            result["native_results"] = tuple(run.operations)
            return await run.finish(result)
    return await api._ctx.perform("military.regular_recruitment_options", read) if perform else await read()


async def _expected_regular(run, formation, unit_type):
    value, reason = await run.field("MilitaryFormation",
        "GetNumExpectedRegularCombatUnitsOfType", (formation, unit_type), (data.Kind.I32,),
        arguments=1,
        expression="MilitaryFormation.GetNumExpectedRegularCombatUnitsOfType(CombatUnitType.Self)")
    return value, reason


async def recruit_regular_unit(api, type_choice, building_id, *, validate_only=False, perform=True):
    """Queue one regular unit for a complete selected Building ID."""
    types = _choice_input(type_choice, "CombatUnitType", api._ctx, "recruit_unit_types")
    if types.metadata.get("mode") != "regular":
        raise ValueError("regular recruitment requires a regular unit type choice")
    building_id = _full_id(building_id, "building_id")
    formation_id = types.metadata["formation_id"]
    async def work():
        async with type_choice.borrow(api._ctx) as type_lease:
            run = WarfareRead(api, "military.recruit_regular_unit")
            unit_type = type_lease.attach(run)
            result = {"formation_id": formation_id, "building_id": building_id,
                      "action": "recruit_regular_unit", "unit_type": dict(type_choice),
                      "accepted": None, "queue_submission_accepted": None,
                      "construction_queued_observed": None}
            try:
                kind, formation = await _formation(run, formation_id)
                if formation is None or kind != "army":
                    raise SourceUnavailable("Regular combat-unit recruitment requires an owned army formation")
                building = data.ObjectBinding.building(await run.type_id("Building"), building_id)
                before, reason = await _expected_regular(run, formation, unit_type)
                result["expected_regular_before"] = before
                if reason:
                    result.setdefault("unavailable", {})["expected_regular_before"] = reason
                owner = await _produced_owner(run,
                    "Building.QueueMilitaryConstruction(MilitaryFormation.Self, CombatUnitType.Self)",
                    (building, formation, unit_type))
                accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
                if accepted:
                    result["queue_submission_accepted"] = True
                    try:
                        after, reason = await _expected_regular(run, formation, unit_type)
                        result["expected_regular_after"] = after
                        if type(before) is int and type(after) is int:
                            result["construction_queued_observed"] = after > before
                        if reason:
                            result.setdefault("unavailable", {})["expected_regular_after"] = reason
                    except Exception as error:
                        run.retain_unknown = True
                        result["readback_error"] = str(error)
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                result.update(status="unavailable", reason=str(error), retryable=True)
                if isinstance(error, KnownResultError):
                    result.update(native_result=error.result, native_error=error.result.error)
            return await run.finish(result)
    return await api._ctx.perform("military.recruit_regular_unit", work) if perform else await work()


async def _map_interaction_active(api, type_lease, label):
    run = WarfareRead(api, label)
    type_lease.attach(run)
    row = {"active": None, "unavailable": {}}
    try:
        result = await run.evaluate("MapListPanelManager.HasCurrentInteraction", ())
        if result.kind == data.Kind.BOOL and type(result.value) is bool:
            row["active"] = result.value
        else:
            row["unavailable"]["active"] = "MapListPanelManager.HasCurrentInteraction did not return bool"
    except Exception:
        run.retain_unknown = True
        raise
    return await run.finish(row)


async def open_conscript_recruitment_map(api, type_choice, *, perform=True):
    """Invoke the real conscript map opener; this does not recruit a unit."""
    types = _choice_input(type_choice, "CombatUnitType", api._ctx, "recruit_unit_types")
    if types.metadata.get("mode") != "conscript":
        raise ValueError("conscript map opener requires a conscript unit type choice")
    formation_id = types.metadata["formation_id"]
    from .._political_actions import selected_property_action
    async def work():
        async with type_choice.borrow(api._ctx) as type_lease:
            before = await _map_interaction_active(api, type_lease,
                "military.conscript_recruitment_map.before")
            async def bindings(run):
                unit_type = type_lease.attach(run)
                kind, formation = await _formation(run, formation_id)
                if formation is None or kind != "army":
                    raise SourceUnavailable("Conscript recruitment requires an owned army formation")
                return formation, unit_type
            result = await selected_property_action(api, "open_conscript_recruitment_map",
                "MilitaryFormation.OnClickRecruitConscript(CombatUnitType.Self)", bindings,
                condition="MilitaryFormation.CanRecruitConscriptUnit(CombatUnitType.Self)",
                namespace="military", perform=False)
            result["formation_id"] = formation_id
            result["unit_type"] = dict(type_choice)
            result["before_map_interaction"] = before
            result["recruitment_completed"] = False
            result["completion_scope"] = (
                "original callback opens the conscription map interaction; final MapListOption.OnClick is not invoked")
            try:
                result["after_map_interaction"] = await _map_interaction_active(api, type_lease,
                    "military.conscript_recruitment_map.after")
            except Exception as error:
                result["readback_error"] = error
            return result
    return await api._ctx.perform("military.open_conscript_recruitment_map", work) if perform else await work()
