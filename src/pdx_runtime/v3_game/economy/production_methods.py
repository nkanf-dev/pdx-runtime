"""Building production-method choices, native effects and explicit changes."""
from __future__ import annotations

from pdx_runtime.v3_wire import resolve_v3_export
import struct
from pdx_runtime import v3_commands as commands, v3_data as data, v3_objects as objects, native_call, v3_access
from pdx_runtime.v3_game.country import CountryAPI, _Read, _plain_name
from pdx_runtime.v3_game.economy import building_status
from pdx_runtime.v3_game._run import command_field

COMMAND = 13208


def _building_id(value):
    if isinstance(value, dict): value = value.get("id", value.get("building_id"))
    if type(value) is not int or not 0 <= value < 0xffffffff:
        raise ValueError("building_id must be a complete Building ID")
    return value


def _method_key(value):
    if isinstance(value, dict): value = value.get("key", value.get("id"))
    if type(value) is int: return value
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("method must be an observed production-method key or row")
    return value


def _retained(ctx):
    records = getattr(ctx, "_production_method_operations", None)
    if records is None:
        records = []; ctx._production_method_operations = records
    return records


def _remember(ctx, operation):
    records = _retained(ctx)
    records[:] = [item for item in records if not item.get("settled")]
    records.append(operation)
    ctx.diagnostics.append({"operation": "production_methods", "record": operation})


async def _query(ctx, label, factory, owner=None):
    return await ctx.query("production_methods." + label, factory, owner=owner)


def _ok(result, label):
    if result.error: raise RuntimeError(f"{label}: native error {result.error}")
    return result


async def _identity(ctx, building_id):
    row = _ok(await _query(ctx, "building", lambda: objects.read_ids(ctx.native,
        objects.Domain.BUILDING, (building_id,), adapter=ctx.adapters["objects"], process=ctx.process)), "Building")
    if row.world != ctx.world: raise RuntimeError("production-method observation changed world")
    if (len(row.records) != 1 or not row.records[0].valid
            or row.records[0].resolved_id != building_id or not row.records[0].pointer):
        raise ValueError("the Building is not available in the current world")
    building = row.records[0]
    state = _ok(await _query(ctx, "state", lambda: objects.read_ids(ctx.native,
        objects.Domain.STATE, (building.state_id,), adapter=ctx.adapters["objects"], process=ctx.process)), "State")
    if state.world != ctx.world: raise RuntimeError("production-method State relation changed world")
    owner = (state.records[0].owner_id if len(state.records) == 1 and state.records[0].valid
             and state.records[0].resolved_id == building.state_id else None)
    return building, owner


async def _snapshot_fallback(ctx, building_id):
    world = ctx.world
    recipe = _PMSnapshotRecipe(group_capacity=4, methods_per_group=8, active_capacity=8)
    while True:
        record = {"kind": "snapshot", "building_id": building_id, "world": world,
                  "recipe": recipe, "storage": None, "settled": False}
        _remember(ctx, record)
        storage = ctx.process.allocate(recipe.storage_size)
        record["storage"] = storage
        ctx.process.write(storage, recipe.initial_storage(building_id))
        initial, prepare = recipe.compose(image_base=ctx.image_base,
            resolver_address=resolve_v3_export(ctx.process, ctx.adapters["objects"], "PdxV3ResolveBuildingObject"),
            runtime_read_address=resolve_v3_export(ctx.process, ctx.adapters.get("runtime", ctx.adapters["data"]), "PdxRuntimeRead"),
            storage=storage, full_building_id=building_id)
        callback = resolve_v3_export(ctx.process, ctx.adapters["call"], "PdxNativeCallBatchInterface")
        complete_prefix = False
        try:
            result = await _query(ctx, "snapshot.same_callback", lambda: v3_access.submit_callback(ctx.native,
                callback, initial, mode=1, flag=0, dispatch="interface", process=ctx.process,
                adapter=ctx.adapters["access"], call_adapter=ctx.adapters["call"], prepare_payload=prepare,
                decode_payload=native_call.NativeCallBatchResult.from_bytes))
            record["result"] = result
            batch = result.callee_result
            complete_prefix = bool(not result.error and result.entered and result.left and batch is not None
                and not batch.error and batch.completed == len(recipe.ops) and len(batch.calls) == len(recipe.ops)
                and not any(call.error for call in batch.calls))
            if not complete_prefix:
                raise RuntimeError("original PM snapshot prefix retained; it is not replayed")
            raw = ctx.process.read(storage, recipe.storage_size)
            observed = recipe.decode_owned(raw, batch, building_id)
            record["owned_snapshot"] = observed
        finally:
            # No game constructors/persistent consumers occur in this read-only
            # recipe. A fully returned resolver/SafeRPM batch no longer borrows
            # this storage, including on a later host copy/decode exception.
            if complete_prefix:
                ctx.process.free(storage); record["storage"] = None; record["settled"] = True
        await ctx.refresh()
        if ctx.world != world: raise RuntimeError("PM snapshot crossed a world change")
        if not observed.get("available"):
            return {"groups": None, "groups_reason": observed.get("reason", "native PM snapshot is incomplete")}
        counts = [group.get("native_method_count", 0) for group in observed["groups"]]
        if observed["groups_complete"] and observed["active_complete"] and all(group.get("complete") for group in observed["groups"]):
            groups = []
            for group in observed["groups"]:
                methods = [{"key": row["key"], "selected": row["active"], "name": None} for row in group["methods"]]
                groups.append({"key": None, "name": None,
                    "current_keys": [row["key"] for row in methods if row["selected"] is True],
                    "current_observed": True, "candidates": methods})
            return {"groups": groups, "groups_reason": None}
        ng, nm, na = max(1, observed["native_group_count"]), max([1] + counts), max(1, observed["native_active_count"])
        capacities = (max(recipe.group_capacity, ng), max(recipe.methods_per_group, nm), max(recipe.active_capacity, na))
        if max(capacities) > 512 or capacities == (recipe.group_capacity, recipe.methods_per_group, recipe.active_capacity):
            return {"groups": None, "groups_reason": "native PM arrays or keys exceed the complete copied snapshot; partial rows are not exposed"}
        # Known completed read, new capacity, new fresh full-ID resolver/walk.
        # Never continue a prior borrowed pointer or retry an unknown prefix.
        recipe = _PMSnapshotRecipe(group_capacity=capacities[0], methods_per_group=capacities[1], active_capacity=capacities[2])


async def _snapshot(ctx, building_id):
    """Fresh owning original Data sources; current PM is consumed in one VM.

    Do not follow the objects snapshot's borrowed CBuilding pointer on the host.
    Building.AccessProductionMethodGroups and the actual current-method getter
    retain their original native wrappers through each dependent expression.
    """
    world = ctx.world
    building, owner = await _identity(ctx, building_id)
    if ctx.world != world: raise RuntimeError("production-method identity changed world")
    api = CountryAPI(ctx); read = _Read(api)
    try:
        binding = data.ObjectBinding.building(await ctx.type_id("Building"), building_id)
        key, key_reason = await api._building_key(read, binding)
        if key is None:
            raise RuntimeError(key_reason or "the native Building type key is unavailable")
        observed = await building_status._production_methods(api, read, binding)
        groups = None
        if observed.get("groups") is not None:
            groups = []
            for source in observed["groups"]:
                candidates = source.get("candidates")
                if candidates is None:
                    groups = None; break
                current = (source.get("current") or {}).get("key")
                rows = []
                for choice in candidates:
                    if not isinstance(choice.get("key"), str) or not choice["key"]:
                        groups = None; break
                    rows.append({"key": choice["key"], "name": choice.get("name"),
                        "selected": choice["key"] == current if current is not None else None})
                if groups is None: break
                groups.append({"key": source.get("key"), "name": source.get("name"),
                    "current_keys": [current] if current is not None else [], "candidates": rows,
                    "current_observed": current is not None, "reasons": source.get("reasons", {})})
        await read.check_world()
        if ctx.world != world: raise RuntimeError("production-method source changed world")
        if groups is None or any(not group.get("current_observed") for group in groups):
            fallback = await _snapshot_fallback(ctx, building_id)
            if fallback.get("groups") is not None:
                groups = fallback["groups"]
            elif groups is None:
                observed.setdefault("reasons", {})["groups"] = fallback.get("groups_reason")
        snapshot = {"building_id": building_id, "building_key": key,
            "state_id": building.state_id, "country_id": owner, "world": world, "groups": groups,
            "groups_reason": observed.get("reasons", {}).get("groups"), "display": observed}
        ctx.diagnostics.append({"operation": "production_methods.snapshot", "snapshot": snapshot})
        return snapshot
    finally:
        try: await read.close()
        except BaseException:
            _keep_display(ctx, read); raise
        if read.uncertain: _keep_display(ctx, read)


def command_field(block, token):
    if isinstance(block, commands.Assignment): block = block.value
    values = [item.value for item in block if isinstance(item, commands.Assignment)
              and item.key == commands.Token(token)]
    if len(values) != 1: raise RuntimeError(f"native PM field {token} is missing or ambiguous")
    return values[0]


async def _cleanup(ctx, operation):
    owner = operation.get("owner")
    if owner is not None and owner.command and owner.pending is None and (
            owner.attempt is None or owner.attempt.outcome == "owned"):
        result = _ok(await _query(ctx, "destroy", owner.destroy, owner), "release PM command")
        if owner.command or result.command: raise RuntimeError("PM command remains owned")
    if owner is not None and not owner.command and owner.pending is None and not operation.get("awaiting_effect"):
        operation["settled"] = True


async def _prepare(ctx, snapshot, key, actor, *, kind):
    operation = {"kind": kind, "building_id": snapshot["building_id"], "building_key": snapshot["building_key"],
        "key": key, "world": snapshot["world"], "owner": None, "submitted": False, "settled": False}
    _remember(ctx, operation)
    # Field order is the original reader's dependency: type before method key.
    parameters = {10232: [commands.UInt32(snapshot["building_id"])],
                  21409: snapshot["building_key"], 13209: key}
    created = await _query(ctx, "create", lambda: commands.create(ctx.native, COMMAND,
        actor_id=actor, parameters=parameters, adapter=ctx.adapters["commands"], process=ctx.process))
    operation["created"] = created
    _ok(created, "prepare production method")
    owner = commands.NativeCommand.from_result(ctx.native, created,
        adapter=ctx.adapters["commands"], process=ctx.process)
    operation["owner"] = owner
    try:
        if owner.world != snapshot["world"] or owner.type_token != COMMAND:
            raise RuntimeError("native PM command has a different world or command type")
        serialized = _ok(await _query(ctx, "serialize", owner.serialize, owner), "serialize production method")
        operation["serialized"] = serialized
        tree = commands.decode_command(serialized.data)
        body = command_field(tree, 65)
        if (tree.key != commands.Token(COMMAND) or commands.encode_tree(tree) != serialized.data
                or command_field(tree, 10304) != commands.UInt32(actor)
                or command_field(body, 10232) != (commands.UInt32(snapshot["building_id"]),)
                or command_field(body, 21409) != snapshot["building_key"] or command_field(body, 13209) != key):
            raise ValueError("native reader did not resolve the requested Building/type/PM keys")
        operation["validation"] = _ok(await _query(ctx, "validate", lambda: owner.validate(capacity=0), owner),
                                          "validate production method")
        return operation
    except BaseException:
        await _cleanup(ctx, operation)
        raise


async def _effect_text(ctx, read, helper, handle, getter):
    try:
        value = await helper.read_retained_text(ctx, "ProductionMethod", handle, "ProductionMethod." + getter)
    except BaseException as error:
        if getattr(helper, "settled_error", lambda error: False)(error):
            return {"text": None, "raw_text": None, "complete": False,
                    "reason": "The native summary finished, but its text could not be copied."}
        read.uncertain = True
        raise
    complete = bool(value.get("available", True) is not False and value.get("text_complete") is True
        and value.get("decode_error") is None and value.get("context_supplied") is True
        and value.get("native_temporaries_destroyed") is True and value.get("world") == read.world
        and isinstance(value.get("text"), str))
    return {"text": value.get("text") if complete else None, "raw_text": value.get("raw_text"),
        "complete": complete, "localization_success": value.get("localization_success"),
        "reason": None if complete else value.get("reason", "The native PM summary is incomplete.")}


# Original customized_tooltips_l_english.yml:521 and actual native metadata
# agree on this one-Country-argument expression. GetPlayer is the original
# singleton getter; no additional/fabricated Building context is supplied.
# GetTooltipTag and blank goods summaries are not modifier descriptions.
_FULL_EFFECTS_EXPRESSION = "GetTooltip(GetPlayer.Self)"


async def _full_effects(ctx, api, read, helper, method_type, handle):
    """Native full PM tooltip, never a Building-specific outcome forecast."""
    expression = _FULL_EFFECTS_EXPRESSION
    signature, reason = await api._function(read, "ProductionMethod", method_type,
                                            "GetTooltip", argument_count=1)
    if signature is None:
        value = {"text": None, "raw_text": None, "complete": False, "reason": reason}
    else:
        value = await _effect_text(ctx, read, helper, handle, expression)
    value.update(scope="production_method_for_player_country",
        source_expression="ProductionMethod." + expression, building_specific=False)
    return value


async def _data_effects(ctx, api, read, binding):
    """Actual compact PM summaries from the original owning Data source."""
    result = {}; gap = "Native PM goods/employment summaries were not available from the current source."
    signature, _ = await api._function(read, "Building", binding.type_id, "AccessProductionMethodGroups")
    if signature is None: return result, gap
    source = await read.query("production_methods.effect_groups", lambda: data.evaluate(ctx.native,
        "Building.AccessProductionMethodGroups", (binding,), **api._options("data")))
    if source.error or not isinstance(source.value, data.NativeList): return result, gap
    count = await read.query("production_methods.effect_group_count", lambda: data.list_count(ctx.native,
        source.value_handle, **api._options("data")))
    if count.error: return result, gap
    group_type = await ctx.type_id("ProductionMethodGroup")
    method_type = await ctx.type_id("ProductionMethod")
    from pdx_runtime.v3_game import _text as helper
    for index in range(count.total):
        group = await read.query("production_methods.effect_group", lambda index=index: data.list_element(ctx.native,
            source.value_handle, index, **api._options("data")))
        if (group.error or not isinstance(group.value, data.NativeObject)
                or "CPdxPtr@VCDataProductionMethodGroup@@" not in group.value.native_type_name):
            continue
        group_binding = data.ObjectBinding(group_type, group.value_handle, retained=True)
        provider, _ = await api._function(read, "ProductionMethodGroup", group_type,
            "AccessBuildingProductionMethods", argument_count=1)
        if provider is None: continue
        choices = await read.query("production_methods.effect_choices", lambda: data.evaluate(ctx.native,
            "ProductionMethodGroup.AccessBuildingProductionMethods(Building.Self)",
            (binding, group_binding), **api._options("data")))
        if choices.error or not isinstance(choices.value, data.NativeList): continue
        n = await read.query("production_methods.effect_choice_count", lambda: data.list_count(ctx.native,
            choices.value_handle, **api._options("data")))
        if n.error: continue
        for offset in range(n.total):
            method = await read.query("production_methods.effect_choice", lambda offset=offset: data.list_element(ctx.native,
                choices.value_handle, offset, **api._options("data")))
            if (method.error or not isinstance(method.value, data.NativeObject)
                    or "CPdxPtr@VCDataProductionMethod@@" not in method.value.native_type_name):
                continue
            method_binding = data.ObjectBinding(method_type, method.value_handle, retained=True)
            key, _ = await building_status._string(api, read, "ProductionMethod", method_binding, "GetKey")
            if not isinstance(key, str) or not key: continue
            info = {}
            for name, getter in (("goods_effects", "GetCompactGoodsSummaryDesc"),
                                 ("employment_effects", "GetCompactEmploymentSummaryDesc")):
                signature, reason = await api._function(read, "ProductionMethod", method_type, getter)
                info[name] = (await _effect_text(ctx, read, helper, method.value_handle, getter) if signature else {
                    "text": None, "raw_text": None, "complete": False, "reason": reason})
            info["full_effects"] = await _full_effects(ctx, api, read, helper, method_type, method.value_handle)
            # Numeric IDs are accepted only if a genuine native getter supplies
            # one. An array position is never substituted for a PM definition ID.
            numeric, reason = await building_status._value(api, read, "ProductionMethod", method_binding,
                                                         "GetIndex", "integer")
            info["id"], info["id_reason"] = numeric, reason
            result[key] = info
    return result, gap


def _keep_display(ctx, read):
    records = getattr(ctx, "_production_method_display_retained", None)
    if records is None: records = []; ctx._production_method_display_retained = records
    if read not in records: records.append(read)


async def _display(ctx, building_id, *, details):
    """Reuse original GUI sources; display failure never invents an owning PM."""
    api = CountryAPI(ctx); read = _Read(api)
    try:
        binding = data.ObjectBinding.building(await ctx.type_id("Building"), building_id)
        result = await building_status._production_methods(api, read, binding)
        result["effects"], result["effects_reason"] = ({}, "Detailed PM summaries were not requested.")
        if details:
            result["effects"], result["effects_reason"] = await _data_effects(ctx, api, read, binding)
        return result
    except LookupError as error:
        return {"groups": None, "effects": {}, "effects_reason": str(error)}
    finally:
        try: await read.close()
        except BaseException:
            _keep_display(ctx, read)
            raise
        if read.uncertain: _keep_display(ctx, read)


def _names(snapshot, observed):
    groups = observed.get("groups") or []
    by_key = {}
    for group in groups:
        for row in group.get("candidates") or []:
            if row.get("key"):
                by_key.setdefault(row["key"], []).append(row)
        current = group.get("current") or {}
        if current.get("key"):
            by_key.setdefault(current["key"], []).append(current)
    for group in snapshot["groups"] or []:
        member_keys = {row["key"] for row in group["candidates"]}
        labels = [item for item in groups if {row.get("key") for row in item.get("candidates") or []} == member_keys]
        if len(labels) == 1:
            group["key"], group["name"] = labels[0].get("key"), labels[0].get("name")
        for row in group["candidates"]:
            names = {item.get("name") for item in by_key.get(row["key"], []) if item.get("name") is not None}
            row["name"] = next(iter(names)) if len(names) == 1 else None


async def read_production_methods(ctx, building_id, *, details=True):
    """Observe original groups/current/choices and native change legality."""
    building_id = _building_id(building_id)
    await ctx.refresh(); actor = await ctx.country_id()
    snapshot = await _snapshot(ctx, building_id)
    observed = (await _display(ctx, building_id, details=True)) if details else snapshot.get("display", {})
    _names(snapshot, observed)
    result = {"building_id": building_id, "building_key": snapshot["building_key"],
        "state_id": snapshot["state_id"], "country_id": snapshot["country_id"], "groups": [],
        "snapshot": "fresh original owning native PM sources; separate observations, not an atomic freeze"}
    if snapshot["groups"] is None:
        result["groups"] = None
        result["reason"] = snapshot.get("groups_reason") or "The current native PM option source is unavailable."
        return result
    for position, group in enumerate(snapshot["groups"]):
        current = group["current_keys"]
        row = {"key": group["key"], "name": group["name"], "position": position + 1,
               "current": {"key": current[0] if len(current) == 1 else None,
                   "name": next((method.get("name") for method in group["candidates"]
                       if len(current) == 1 and method["key"] == current[0]), None)}, "candidates": []}
        if group["key"] is None: row["key_reason"] = "native group key was not exposed by the original Data source"
        if len(current) != 1: row["current"]["reason"] = "native group has no unique active method"
        for method in group["candidates"]:
            operation = await _prepare(ctx, snapshot, method["key"], actor, kind="candidate")
            try:
                allowed = bool(operation["validation"].validated)
                effects = observed.get("effects", {}).get(method["key"], {})
                choice = {"id": effects.get("id"), "key": method["key"], "name": method.get("name"),
                    "selected": method["selected"], "allowed": allowed,
                    "reason": None if allowed else "Native production-method rules reject this change.",
                    "reason_complete": False, "id_reason": effects.get("id_reason") or
                        "use the native method key; no numeric PM ID was exposed",
                    "goods_effects": effects.get("goods_effects"), "employment_effects": effects.get("employment_effects"),
                    "full_effects": effects.get("full_effects"),
                    "full_effects_reason": None if effects.get("full_effects") else
                        observed.get("effects_reason", "The native full PM description was not returned by the current source."),
                    "qualification_warning": None,
                    "qualification_warning_reason": "The PM-change qualification description needs both Building and selected-method contexts; it is not replaced with an expansion warning."}
                if choice["full_effects"] is not None:
                    choice["full_effects_reason"] = choice["full_effects"].get("reason")
                if not effects:
                    choice["effects_reason"] = observed.get("effects_reason")
                row["candidates"].append(choice)
            finally:
                await _cleanup(ctx, operation)
        result["groups"].append(row)
    return result


def _selected(snapshot, key):
    if snapshot["groups"] is None: return None
    rows = [row for group in snapshot["groups"] for row in group["candidates"] if row["key"] == key]
    return rows[0]["selected"] if len(rows) == 1 else None


async def _observe_change(ctx, operation):
    snapshot = await _snapshot(ctx, operation["building_id"])
    if ctx.world != operation["world"] or snapshot["world"] != operation["world"]:
        operation["awaiting_effect"] = True
        return {"building_id": operation["building_id"], "method_key": operation["key"],
            "submitted": True, "accepted": True, "effect_observed": None,
            "status": "accepted_world_changed", "reason": "The world changed before selection could be observed."}
    observed = _selected(snapshot, operation["key"])
    operation["awaiting_effect"] = not observed
    operation["settled"] = observed
    result = {"building_id": operation["building_id"], "method_key": operation["key"],
        "submitted": True, "accepted": True, "effect_observed": observed,
        "status": "selected" if observed else "accepted_not_yet_observed",
        "effect_scope": "fresh active production method; not a claim about GDP/output/research settlement"}
    operation["result"] = result
    return result


async def set_production_method(ctx, building_id, method, *, details=False):
    """Select this exact native method once; no alternate PM or compensation."""
    building_id, key = _building_id(building_id), _method_key(method)
    await ctx.refresh(); actor = await ctx.country_id()
    # A known accepted-but-not-yet-observed or uncertain handoff is never sent
    # again merely because the caller asks the same high-level question twice.
    for previous in _retained(ctx):
        if (previous.get("kind") == "change" and not previous.get("settled")
                and previous.get("world") == ctx.world and previous.get("building_id") == building_id):
            if previous.get("awaiting_effect"):
                result = await _observe_change(ctx, previous)
                if key == previous["key"] or not result["effect_observed"]: return result
                break
            owner = previous.get("owner")
            if previous.get("submitted") or owner is not None and owner.pending is not None:
                return {"building_id": building_id, "method_key": previous["key"], "submitted": previous.get("submitted", False),
                    "accepted": None, "effect_observed": None, "status": "original_handoff_retained",
                    "reason": "the original PM command/query is retained; no duplicate change was sent"}
    snapshot = await _snapshot(ctx, building_id)
    if type(key) is int:
        observed = await _display(ctx, building_id, details=True)
        resolved = [method_key for method_key, info in observed.get("effects", {}).items() if info.get("id") == key]
        if len(resolved) != 1:
            return {"building_id": building_id, "method_id": key, "submitted": False, "accepted": None,
                "effect_observed": False, "reason": "this PM ID has no unique current native key for the Building"}
        key = resolved[0]
    matches = [row for group in snapshot["groups"] or [] for row in group["candidates"] if row["key"] == key]
    if snapshot["groups"] is not None and len(matches) != 1:
        return {"building_id": building_id, "method_key": key, "submitted": False, "accepted": None,
            "effect_observed": False, "reason": "this method key is not a unique current option for the Building"}
    if matches and matches[0]["selected"]:
        return {"building_id": building_id, "method_key": key, "submitted": False, "accepted": None,
            "effect_observed": True, "status": "already_selected"}
    operation = await _prepare(ctx, snapshot, key, actor, kind="change")
    owner = operation["owner"]
    try:
        allowed = bool(operation["validation"].validated)
        if not allowed:
            return {"building_id": building_id, "method_key": key, "submitted": False, "accepted": None,
                "allowed": False, "effect_observed": False, "reason": "Native production-method rules reject this change.",
                "reason_complete": False}
        fresh = await _snapshot(ctx, building_id)
        if fresh["world"] != operation["world"] or fresh["building_key"] != operation["building_key"] or await ctx.country_id() != actor:
            raise RuntimeError("PM actor/world/Building type changed before selection")
        if _selected(fresh, key):
            return {"building_id": building_id, "method_key": key, "submitted": False, "accepted": None,
                "effect_observed": True, "status": "already_selected"}
        fresh_validation = _ok(await _query(ctx, "validate.before_submit", lambda: owner.validate(capacity=0), owner),
                               "validate production method before selection")
        operation["fresh_validation"] = fresh_validation
        if not fresh_validation.validated:
            return {"building_id": building_id, "method_key": key, "submitted": False, "accepted": None,
                "allowed": False, "effect_observed": False, "reason": "Native production-method rules no longer allow this change.",
                "reason_complete": False}
        operation["submitted"] = True
        submitted = _ok(await _query(ctx, "submit", lambda: owner.submit(flags=4), owner), "select production method")
        operation["submission"] = submitted
        if owner.command or owner.pending is not None or submitted.command:
            return {"building_id": building_id, "method_key": key, "submitted": True, "accepted": None,
                "effect_observed": None, "status": "original_handoff_retained"}
        if not submitted.accepted:
            operation["settled"] = True
            return {"building_id": building_id, "method_key": key, "submitted": True, "accepted": False,
                "effect_observed": False, "status": "receiver_rejected"}
        operation["awaiting_effect"] = True
        return await _observe_change(ctx, operation)
    finally:
        await _cleanup(ctx, operation)


# Existing native-call result-chaining recipe: every pointer walk and key copy
# occurs inside the same Access callback. Host code only decodes owned bytes.
KEY_BYTES=4096  # PdxRuntimeRead bound; NEVER allocate less for dynamic CString length.


class _PMSnapshotRecipe:
    def __init__(self, *, group_capacity=8, methods_per_group=16, active_capacity=16):
        for value in (group_capacity,methods_per_group,active_capacity):
            if type(value) is not int or not 0 < value <= 512:
                raise ValueError("each explicit pointer-prefix capacity must be1..512 (<=4096B)")
        self.group_capacity=group_capacity;self.methods_per_group=methods_per_group
        self.active_capacity=active_capacity;self.cursor=0;self.reads=[];self.links=[];self.ops=[]
        self.id_cell=self.alloc(4);self.fallback=self.alloc(8)
        self.building=self.alloc(0x1f8);self.building_type=self.alloc(0xa8)
        self.groups=self.alloc(group_capacity*8);self.active=self.alloc(active_capacity*8)
        # Op0 is the fresh resolver, never a caller-provided borrowed Building*.
        self.ops.append(('resolver',))
        self.fallback_read=self.read(('image',0x5964278),self.fallback,8,'fallback')
        self.building_read=self.indirect(('rax',0),self.building,0x1f8,'Building')
        self.type_read=self.indirect(('owned',self.building+0xf0),self.building_type,0xa8,'BuildingType')
        self.groups_read=self.indirect(('owned',self.building_type+0x98),self.groups,group_capacity*8,'groups_prefix')
        self.active_read=self.indirect(('owned',self.building+0x1e8),self.active,active_capacity*8,'active_prefix')
        self.rows=[]
        for gi in range(group_capacity):
            header=self.alloc(0x50);methods=self.alloc(methods_per_group*8)
            group_read=self.indirect(('owned',self.groups+gi*8),header,0x50,f'group{gi}')
            methods_read=self.indirect(('owned',header+0x40),methods,methods_per_group*8,f'group{gi}.methods_prefix')
            group={'header':header,'methods':methods,'read':group_read,'methods_read':methods_read,'rows':[]}
            self.rows.append(group)
            for mi in range(methods_per_group):
                mh=self.alloc(0x38);key=self.alloc(KEY_BYTES)
                mr=self.indirect(('owned',methods+mi*8),mh,0x38,f'group{gi}.method{mi}')
                # This SafeRPM is permitted to fail for CString SSO. Metadata
                # and inline bytes are already in our owned method prefix.
                kr=self.indirect(('owned',mh+0x18),key,0,f'group{gi}.method{mi}.heap_key',
                    size_word=('owned',mh+0x28))
                group['rows'].append({'header':mh,'key':key,'read':mr,'key_read':kr})
        self.storage_size=(self.cursor+7)&~7

    def alloc(self,n):
        self.cursor=(self.cursor+7)&~7;off=self.cursor;self.cursor+=n;return off

    def read(self,source,destination,size,label):
        count=self.alloc(4);index=len(self.ops)
        self.ops.append(('read',source,destination,size,count,label))
        receipt={'index':index,'received':count,'size':size,'label':label}
        self.reads.append(receipt);return receipt

    def indirect(self,word,destination,size,label,*,size_word=None):
        # Read an owned pointer/RAX word INTO a later call's explicit arg slot.
        pointer_copy=self.read(word,None,8,label+'.src_patch')
        length_copy=self.read(size_word,None,8,label+'.size_patch') if size_word else None
        actual=self.read(('zero',0),destination,size,label)
        self.links.append((pointer_copy['index'],actual['index'],0))
        if length_copy:self.links.append((length_copy['index'],actual['index'],2))
        return actual

    def initial_storage(self,full_building_id):
        if type(full_building_id) is not int or not 0 <= full_building_id < 0xffffffff:
            raise ValueError("fresh complete uint32 Building ref required, not fallbackFFFFFFFF")
        result=bytearray(self.storage_size);struct.pack_into('<I',result,self.id_cell,full_building_id)
        return bytes(result)

    def compose(self, *, image_base,resolver_address,runtime_read_address,storage,full_building_id,
                resolver_by_reference=False):
        """Public Objects export(uint32) preferred; nativeC5AE60 uses constuint32*.

        PdxV3ResolveBuildingObject(uint32 fullID)->CBuilding* takes an immediate.
        Original game+C5AE60(const uint32* fullref)->CBuilding* takes &id_cell.
        Do not confuse either with a CDataBuilding pointer or mask generation.
        """
        self.initial_storage(full_building_id)  # validate before building pointers.
        def addr(source,batch_base):
            kind,value=source
            if kind=='owned':return storage+value
            if kind=='image':return image_base+value
            if kind=='rax':return batch_base+native_call.BATCH_REQUEST.size+value*native_call.REQUEST.size+32
            return 0
        def calls_at(batch_base):
            calls=[]
            for op in self.ops:
                if op[0]=='resolver':calls.append((resolver_address,(storage+self.id_cell if resolver_by_reference else full_building_id,)))
                else:
                    _,src,dst,size,count,_=op
                    calls.append((runtime_read_address,(addr(src,batch_base),storage+dst if dst is not None else 0,size,storage+count)))
            return calls
        initial=native_call.encode_batch(calls_at(0))
        def prepare(batch_base):
            payload=bytearray(native_call.encode_batch(calls_at(batch_base),request_address=batch_base))
            for copy_index,target_index,argument in self.links:
                copy_args=struct.unpack_from('<Q',payload,native_call.BATCH_REQUEST.size+copy_index*native_call.REQUEST.size+native_call.ARGUMENT_POINTER_OFFSET)[0]
                target_args=struct.unpack_from('<Q',payload,native_call.BATCH_REQUEST.size+target_index*native_call.REQUEST.size+native_call.ARGUMENT_POINTER_OFFSET)[0]
                struct.pack_into('<Q',payload,copy_args-batch_base+8,target_args+argument*8)
            return bytes(payload)
        return initial,prepare

    def decode_owned(self,raw,batch,full_building_id):
        """No process/RPM here. All offsets refer to caller-owned copied bytes."""
        if len(raw)<self.storage_size:raise ValueError("owned snapshot buffer truncated")
        if batch.error or batch.completed!=len(self.ops) or len(batch.calls)!=len(self.ops) or any(c.error for c in batch.calls):
            raise RuntimeError("native prefix/unknown retained; never repeat it or dereference returned pointers")
        u64=lambda off:struct.unpack_from('<Q',raw,off)[0]
        i32=lambda off:struct.unpack_from('<i',raw,off)[0]
        def copied(read,n=None):
            n=read['size'] if n is None else n
            return (batch.calls[read['index']].rax&0xffffffff)==0 and struct.unpack_from('<I',raw,read['received'])[0]==n
        pointer=batch.calls[0].rax
        if (not pointer or not copied(self.building_read) or not copied(self.fallback_read)
                or pointer==u64(self.fallback) or struct.unpack_from('<I',raw,self.building+0x10)[0]!=full_building_id):
            return {'available':False,'reason':'fresh Building resolver/fallback/full32 identity not established'}
        if not copied(self.type_read):return {'available':False,'reason':'current BuildingType prefix not copied'}
        ng=i32(self.building_type+0xa4);na=i32(self.building+0x1f4)
        if ng<0 or na<0:return {'available':False,'reason':'negative native count preserved as invalid, not zero'}
        ga=(ng==0 or copied(self.groups_read));aa=(na==0 or copied(self.active_read))
        active=[u64(self.active+i*8) for i in range(min(na,self.active_capacity))] if aa else []
        answer={'available':bool(ga),'full_building_id':full_building_id,'native_group_count':ng,
            'native_active_count':na,'active_prefix_copied':aa,'active_complete':bool(aa and na<=self.active_capacity),
            'groups_complete':bool(ga and ng<=self.group_capacity),'groups':[],
            'raw_pointers_are_diagnostic_not_leases':True,'snapshot_scope':'singleAccesscallback; notfutureWorldlease'}
        if not ga:return answer
        for gi in range(min(ng,self.group_capacity)):
            group=self.rows[gi];header=group['header'];out={'group_index':gi,'group_key':None,'group_key_status':'not_proved_not_guessed','methods':[]}
            answer['groups'].append(out)
            if not copied(group['read']):out.update(available=False,complete=False);answer['groups_complete']=False;continue
            count=i32(header+0x4c);out['native_method_count']=count
            ma=count>=0 and (count==0 or copied(group['methods_read']))
            out.update(available=ma,complete=bool(ma and count<=self.methods_per_group))
            if not ma:answer['groups_complete']=False;continue
            for mi in range(min(count,self.methods_per_group)):
                method=group['rows'][mi];mh=method['header'];mp=u64(group['methods']+mi*8)
                row={'method_index':mi,'active':True if mp in active else False if answer['active_complete'] else None,'key':None,'key_bytes':None,'key_complete':False}
                out['methods'].append(row)
                if not copied(method['read']):out['complete']=False;continue
                length=u64(mh+0x28);capacity=u64(mh+0x30)
                if capacity<=15:
                    complete=length<=capacity<=15;body=raw[mh+0x18:mh+0x18+length] if complete else None
                else:
                    complete=length<=capacity and length<=KEY_BYTES and (length==0 or copied(method['key_read'],length))
                    body=raw[method['key']:method['key']+length] if complete else None
                row.update(required_key_bytes=length,key_complete=bool(complete),key_bytes=body,
                    key=body.decode('utf-8','strict') if body is not None else None)
                if not complete:out['complete']=False
            if not out['complete']:answer['groups_complete']=False
        return answer
