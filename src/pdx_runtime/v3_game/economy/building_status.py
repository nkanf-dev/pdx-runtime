"""Native Building staffing, production methods and operational status."""
from __future__ import annotations

from pdx_runtime.v3_game.country import CountryAPI, _Read, _plain_name
from pdx_runtime.v3_game._values import fixed_decimal
from pdx_runtime import v3_data as data, v3_objects as objects

# Names and widths are current-metadata checked before every getter family.
_BUILDING = (
    ("employment", "employees", "GetNoOfEmployed", "integer", "people"),
    ("employment", "capacity", "GetEmployeeCap", "integer", "people"),
    ("employment", "fraction", "GetEmploymentPercentage", "fixed", "fraction"),
    ("hiring", "hiring", "ShouldShowAsHiring", "boolean", None),
    ("hiring", "unable_to_hire", "ShouldShowAsUnableToHire", "boolean", None),
    ("hiring", "unwilling_to_hire", "ShouldShowAsUnwillingToHire", "boolean", None),
    ("hiring", "has_failed_hires", "HasFailedHires", "boolean", None),
    ("wages", "average_annual", "GetAverageAnnualWage", "fixed", "pounds/year per employee"),
    ("wages", "average_annual_subsidy", "GetAverageAnnualWageSubsidies", "fixed", "pounds/year per employee"),
    ("wages", "last_expenses", "GetLastWagesExpenses", "fixed", "pounds (last settlement)"),
    ("cash", "current", "GetCurrentCashReserves", "fixed", "pounds"),
    ("cash", "capacity", "GetMaxCashReserves", "fixed", "pounds"),
    ("subsidies", "enabled", "IsSubsidized", "boolean", None),
    ("subsidies", "amount", "GetSubsidies", "fixed", "pounds/week"),
    ("input_supply", "has_shortage", "HasAnyInputShortage", "boolean", None),
    ("input_supply", "penalty", "GetInputShortagePenalty", "fixed", "fraction"),
    ("input_supply", "availability_output_multiplier", "CalcInputAvailabilityOutputMult", "fixed", "multiplier"),
)
_STATE = (
    ("unemployed_working_adults", "GetNumUnemployedWorkingAdults", "integer", "people"),
    ("insufficient_qualifications_for_available_positions", "HasInsufficientQualificationsForAvailablePositions", "boolean", None),
    ("average_annual_wage", "GetAverageAnnualWage", "fixed", "pounds/year per employee"),
    ("market_access", "GetMarketAccess", "fixed", "fraction"),
    ("isolated_from_market", "IsIsolatedFromMarket", "boolean", None),
)
_TEXTS = (
    ("not_hiring", "GetNotHiringReasonDesc", "current native not-hiring reason"),
    ("failed_hires", "GetAllFailedHiresDesc", "actual failed hiring attempts"),
    ("hiring_target", "GetAdjustHiringTargetDesc", "native hiring target explanation"),
    ("expansion_qualifications", "GetQueueConstructionPopQualificationsWarningBreakdown",
     "qualification warning for expansion; not proof of the cause of current understaffing"),
)


def _keep(ctx, read):
    retained = getattr(ctx, "_building_status_retained", None)
    if retained is None:
        retained = []
        ctx._building_status_retained = retained
    if read not in retained:
        retained.append(read)


def _decode(result, expected):
    if expected == "integer" and result.kind in (data.Kind.I32, data.Kind.U32, data.Kind.I64, data.Kind.U64):
        value = result.value
        return (value, None) if type(value) is int else (None, "native result is not an integer")
    if expected == "boolean" and result.kind == data.Kind.BOOL and type(result.value) is bool:
        return result.value, None
    if expected == "fixed" and result.kind == data.Kind.FIXED and isinstance(result.value, data.FixedPoint):
        return fixed_decimal(result.value), None
    return None, "native value kind does not match the observed getter's game quantity"


async def _value(api, read, receiver, binding, getter, expected):
    result, reason = await api._getter(read, receiver, binding, getter)
    if result is None:
        return None, reason
    return _decode(result, expected)


async def _string(api, read, receiver, binding, getter):
    result, reason = await api._getter(read, receiver, binding, getter)
    if result is None:
        return None, reason
    if result.kind != data.Kind.STRING or not isinstance(result.value, str):
        return None, "native field is not a complete text value"
    return result.value, None


async def _original_expression(api, read, expression, bindings):
    await read.check_world()
    result = await read.query(expression, lambda: data.evaluate(read.ctx.native, expression,
        bindings, output_bytes=4096, **api._options("data")))
    while result.error == data.ERROR_MORE_DATA and result.value_handle:
        if result.total <= result.snapshot_bytes:
            break
        previous = result.snapshot_bytes
        result = await read.query(expression + ".snapshot", lambda result=result: data.snapshot(
            read.ctx.native, result.value_handle, output_bytes=result.total, **api._options("data")))
        if result.error == data.ERROR_MORE_DATA and result.snapshot_bytes <= previous:
            return None, f"{expression}: native snapshot did not advance"
    await read.check_world()
    if result.error:
        return None, f"{expression}: native error {result.error}"
    if result.kind != data.Kind.STRING or not isinstance(result.value, str):
        return None, f"{expression}: native result is not complete text"
    return result.value, None


async def _production_methods(api, read, building):
    """Read original group/current/candidate sources, never activate a method."""
    ctx = read.ctx
    result = {"groups": None, "scope": "original native PM groups and options; not permission to change a method",
              "reasons": {}}
    signature, reason = await api._function(read, "Building", building.type_id, "AccessProductionMethodGroups")
    if signature is None:
        result["reasons"]["groups"] = reason
        return result
    source = await read.query("building_status.pm_groups", lambda: data.evaluate(ctx.native,
        "Building.AccessProductionMethodGroups", (building,), **api._options("data")))
    if source.error or not isinstance(source.value, data.NativeList):
        result["reasons"]["groups"] = f"native production-method groups source unavailable (error {source.error})"
        return result
    count = await read.query("building_status.pm_group_count", lambda: data.list_count(ctx.native,
        source.value_handle, **api._options("data")))
    if count.error:
        result["reasons"]["groups"] = f"native production-method group count error {count.error}"
        return result
    try:
        group_type = await ctx.type_id("ProductionMethodGroup")
        method_type = await ctx.type_id("ProductionMethod")
    except LookupError as error:
        result["reasons"]["groups"] = str(error)
        return result
    groups = []
    for index in range(count.total):
        selected = await read.query("building_status.pm_group", lambda index=index: data.list_element(
            ctx.native, source.value_handle, index, **api._options("data")))
        if (selected.error or not isinstance(selected.value, data.NativeObject)
                or "CPdxPtr@VCDataProductionMethodGroup@@" not in selected.value.native_type_name):
            result["reasons"]["groups"] = "the native PM group provider did not return an owning group context"
            result["groups"] = groups or None
            return result
        group_binding = data.ObjectBinding(group_type, selected.value_handle, retained=True)
        key, key_reason = await _string(api, read, "ProductionMethodGroup", group_binding, "GetKey")
        name, name_reason = await api._name(read, "ProductionMethodGroup", group_binding)
        group = {"key": key, "name": name, "current": {"key": None, "name": None},
                 "candidates": None, "reasons": {}}
        for field, gap in (("key", key_reason), ("name", name_reason)):
            if gap:
                group["reasons"][field] = gap
        active, active_reason = await api._function(read, "Building", building.type_id,
            "AccessProductionMethod", argument_count=1)
        for field, getter in (("key", "GetKey"), ("name", "GetName")):
            signature, gap = await api._function(read, "ProductionMethod", method_type, getter)
            if active is None or signature is None:
                group["reasons"]["current_" + field] = active_reason or gap
                continue
            value, gap = await _original_expression(api, read,
                "Building.AccessProductionMethod(ProductionMethodGroup.Self)." + getter,
                (building, group_binding))
            group["current"][field] = _plain_name(value) if value is not None and field == "name" else value
            if gap:
                group["reasons"]["current_" + field] = gap
        provider, gap = await api._function(read, "ProductionMethodGroup", group_type,
            "AccessBuildingProductionMethods", argument_count=1)
        if provider is None:
            group["reasons"]["candidates"] = gap
            groups.append(group)
            continue
        candidates = await read.query("building_status.pm_candidates", lambda: data.evaluate(ctx.native,
            "ProductionMethodGroup.AccessBuildingProductionMethods(Building.Self)",
            (building, group_binding), **api._options("data")))
        if candidates.error or not isinstance(candidates.value, data.NativeList):
            group["reasons"]["candidates"] = f"native PM options source error {candidates.error}"
            groups.append(group)
            continue
        candidate_count = await read.query("building_status.pm_candidate_count", lambda: data.list_count(
            ctx.native, candidates.value_handle, **api._options("data")))
        if candidate_count.error:
            group["reasons"]["candidates"] = f"native PM options count error {candidate_count.error}"
            groups.append(group)
            continue
        choices = []
        for position in range(candidate_count.total):
            candidate = await read.query("building_status.pm_candidate", lambda position=position: data.list_element(
                ctx.native, candidates.value_handle, position, **api._options("data")))
            if (candidate.error or not isinstance(candidate.value, data.NativeObject)
                    or "CPdxPtr@VCDataProductionMethod@@" not in candidate.value.native_type_name):
                group["reasons"]["candidates"] = "native PM candidate is not an owning method context"
                continue
            method = data.ObjectBinding(method_type, candidate.value_handle, retained=True)
            choice_key, choice_key_reason = await _string(api, read, "ProductionMethod", method, "GetKey")
            choice_name, choice_name_reason = await api._name(read, "ProductionMethod", method)
            choices.append({"key": choice_key, "name": choice_name,
                "selected": (choice_key == group["current"]["key"] if choice_key is not None
                    and group["current"]["key"] is not None else None),
                "reasons": {name: value for name, value in (("key", choice_key_reason),
                    ("name", choice_name_reason)) if value}})
        group["candidates"] = choices
        groups.append(group)
    result["groups"] = groups
    return result


async def read_building_status(ctx, building_id, *, details=True):
    """Read one full Building ID. The public caller supplies ctx.perform shield."""
    if type(building_id) is not int or not 0 <= building_id < 0xffffffff:
        raise ValueError("building_id must be a complete Building ID")
    await ctx.refresh()
    api, read = CountryAPI(ctx), None
    read = _Read(api)
    try:
        snapshot = await read.query("building_status.building", lambda: objects.read_ids(ctx.native,
            objects.Domain.BUILDING, (building_id,), **api._options("objects")))
        if snapshot.world != read.world:
            raise RuntimeError("building status crossed a world change; no mixed-world result returned")
        if (snapshot.error or len(snapshot.records) != 1
                or not snapshot.records[0].valid or snapshot.records[0].resolved_id != building_id):
            return {"id": building_id, "available": False, "reason": "this Building is not available in the current world"}
        record = snapshot.records[0]
        state_snapshot = await read.query("building_status.state", lambda: objects.read_ids(ctx.native,
            objects.Domain.STATE, (record.state_id,), **api._options("objects")))
        if state_snapshot.world != read.world:
            raise RuntimeError("building State relation crossed a world change; no mixed-world result returned")
        state_record = (state_snapshot.records[0] if not state_snapshot.error
            and len(state_snapshot.records) == 1 and state_snapshot.records[0].valid
            and state_snapshot.records[0].resolved_id == record.state_id else None)
        building = data.ObjectBinding.building(await ctx.type_id("Building"), building_id)
        name, name_reason = await api._name(read, "Building", building)
        key, key_reason = await api._building_key(read, building)
        result = {"id": building_id, "available": True, "key": key, "name": name,
            "state_id": record.state_id, "country_id": state_record.owner_id if state_record else None,
            "levels": record.levels, "employment": {}, "hiring": {}, "wages": {}, "cash": {},
            "subsidies": {}, "input_supply": {}, "state": {"id": record.state_id,
                "scope": "state-wide observations; not a diagnosis of this Building's understaffing"},
            "reasons": {name: reason for name, reason in (("name", name_reason), ("key", key_reason)) if reason},
            "snapshot": "separate native observations; no inferred hiring cause"}
        for section, field, getter, expected, unit in _BUILDING:
            value, reason = await _value(api, read, "Building", building, getter, expected)
            result[section][field] = value
            if unit:
                result[section].setdefault("units", {})[field] = unit
            if reason:
                result[section].setdefault("reasons", {})[field] = reason
        result["hiring"]["display_states"] = [name for name in ("hiring", "unable_to_hire", "unwilling_to_hire")
                                                if result["hiring"][name] is True]
        result["hiring"]["indicator_scope"] = "native UI flags, not an inferred labour/material/wage diagnosis"
        result["hiring"]["indicators_complete"] = all(type(result["hiring"][name]) is bool
            for name in ("hiring", "unable_to_hire", "unwilling_to_hire", "has_failed_hires"))
        if state_record is not None:
            state = data.ObjectBinding.state(await ctx.type_id("State"), record.state_id)
            for field, getter, expected, unit in _STATE:
                value, reason = await _value(api, read, "State", state, getter, expected)
                result["state"][field] = value
                if unit:
                    result["state"].setdefault("units", {})[field] = unit
                if reason:
                    result["state"].setdefault("reasons", {})[field] = reason
        else:
            result["state"]["reason"] = "the Building's State relation could not be observed"
            for field, getter, expected, unit in _STATE:
                result["state"][field] = None
                result["state"].setdefault("reasons", {})[field] = result["state"]["reason"]
                if unit:
                    result["state"].setdefault("units", {})[field] = unit
        result["hiring"]["descriptions"] = {}
        if details:
            from pdx_runtime.v3_game import _text as helper
            reader = getattr(helper, "read_building_text", None)
            for label, getter, scope in _TEXTS:
                signature, reason = await api._function(read, "Building", building.type_id, getter)
                localized = None
                if signature is not None and reader is not None:
                    try:
                        localized = await reader(ctx, building_id, "Building." + getter)
                    except BaseException as error:
                        if not getattr(helper,"settled_error",lambda error:False)(error):read.uncertain = True
                        raise
                complete = bool(localized and localized.get("text_complete") is True
                    and localized.get("context_supplied") is True and localized.get("native_temporaries_destroyed") is True
                    and localized.get("decode_error") is None and localized.get("world") == read.world)
                result["hiring"]["descriptions"][label] = {"text": localized.get("text") if localized else None,
                    "raw_text": localized.get("raw_text") if localized else None, "complete": complete, "scope": scope,
                    "localization_success": localized.get("localization_success") if localized else None,
                    "reason": (localized.get("reason") if localized else None) or reason or
                        (None if complete else "the contextual Building description reader is currently unavailable")}
        else:
            result["hiring"]["description_reason"] = "rich hiring descriptions were not requested"
        result["production_methods"] = await _production_methods(api, read, building)
        await read.check_world()
        return result
    finally:
        if read is not None:
            try:
                await read.close()
            except BaseException:
                _keep(ctx, read)
                raise
            if read.uncertain:
                _keep(ctx, read)
