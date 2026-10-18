"""Ordinary current native Treaty/Article query; no connection on import.

A retained Data wrapper owns its bytes, not the World entity it references.
The query never carries a raw resolver RAX into a later memory read. Only
owned wrapper storage and a fixed image null-definition slot are copied. Native
reference validity uses the existing PdxV3ScriptScopeValid export inside Access.
No proposal, UI action, generic command or native mutation is generated.
"""
from __future__ import annotations
from pdx_runtime.v3_wire import V3Wire, resolve_v3_export
import struct

from .... import v3_data as data, v3_access
from ... import _run

VIEWS = ("GetMutualAgreements", "GetOffers", "GetDemands")
TREATY_SCOPE = 0x65
ARTICLE_SCOPE = 0x62
COUNTRY_SCOPE = 5
ARTICLE_TYPE_NULL_SLOT = 0x5963E88


def _u32(value, label):
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(label + " must be an integer full ID")
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(label + " must fit uint32 without masking")
    return value


def _number(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def new_state(client, process, country_id, data_adapter, commands_adapter=None):
    return {"client": client, "process": process, "country_id": country_id,
        "data_adapter": data_adapter, "commands_adapter": commands_adapter,
        "pending": None, "queries": [], "operations": [], "owned_values": []}


async def operation(state, label, factory, *, owner=None):
    """Keep the original query/result while the game context owns its wait."""
    pending = {"label": label, "query": None, "owner": owner}
    state["pending"] = pending
    try:
        query = await _run.submit(factory)
        pending["query"] = query
        state["queries"].append(query)
        result = await _run.complete(owner) if owner is not None else await _run.read(query)
        if result is None:
            raise RuntimeError("original Treaty query is pending; retain its exact handle")
    except BaseException as error:
        pending.update(error=error)
        if pending["query"] is None:
            pending["query"] = getattr(error, "query", None)
        raise
    handle = getattr(result, "value_handle", 0)
    if handle and handle not in state["owned_values"]:
        state["owned_values"].append(handle)
    state["operations"].append({"label": label, "query": query, "owner": owner, "result": result})
    state["pending"] = None
    return result


async def complete(state, label, make_query):
    result = await operation(state, label, make_query)
    if result.error:
        state["last_error_result"] = result
        error = _run.KnownResultError(label, result)
        error.native_result, error.native_label = result, label
        raise error
    return result


def observation(state, label):
    status = V3Wire(state["client"]).world_status()
    if status.get("status") != "ok":
        raise RuntimeError("current native world observation unavailable")
    image = _number(status.get("main_module_base", 0))
    world = _number(status.get("world", 0))
    state["operations"].append({"label": label, "world": world, "image_base": image,
        "transition_pending": int(bool(status.get("transition_pending", False)))})
    if image != state["image_base"] or not image or not world or status.get("transition_pending"):
        raise RuntimeError("fresh image/world observation changed or is not ready")
    if state.get("world") is not None and state["world"] != world:
        raise RuntimeError("source world witness changed; no later-world substitution")
    return world


async def source_check(state, label, handle):
    result = await complete(state, label, lambda: data.snapshot(state["client"], handle,
        output_bytes=32, adapter=state["data_adapter"], process=state["process"]))
    if result.value_handle != handle:
        raise RuntimeError("native retained source handle identity changed")
    observation(state, label + "_world")
    return result


def _owned_object(state, result, typename, label):
    # Native Any identity and a DataFactory context alias are different facts.
    # e.g. actual Country84/context83 and ArticleType149/context148. Do not
    # rewrite the Any or reject its exact CPdxPtr identity because IDs differ.
    # Native context binding/conversion, when needed later, remains explicit.
    value = result.value
    exact_name = ".?AU?$SAnyType@V?$CPdxPtr@V" + typename + "@@@@@PdxAnyImpl@@"
    if (not isinstance(value, data.NativeObject) or not value.pointer
            or result.native_type_name != exact_name or result.payload_size != 8):
        raise RuntimeError("unproved owned " + typename + " Any; never rebind borrowed const as owned")
    return value


async def _evaluate(state, label, expression, binding):
    observation(state, label + "_before")
    result = await complete(state, label, lambda: data.evaluate(state["client"], expression,
        (binding,), output_bytes=65536, adapter=state["data_adapter"], process=state["process"]))
    observation(state, label + "_after")
    return result


async def _copy_owned_storage(state, label, address, size, source_handle):
    # The address must be retained CData wrapper bytes or a fixed image global,
    # never a World pointer borrowed from a previous callback's resolver RAX.
    await source_check(state, label + "_source_before", source_handle)
    process = state["process"]
    target = process.resolve_export("ntdll.dll", "memcpy")
    buffer = process.allocate(size)
    state["owned_buffers"].append(buffer)  # Own it before any fallible write.
    process.write(buffer, bytes(size))
    result = await complete(state, label, lambda: v3_access.submit_call(state["client"], target,
        (buffer, address, size), mode=1, flag=0, dispatch="interface",
        adapter=state["access_adapter"], call_adapter=state["call_adapter"], process=process))
    raw = result.callee_result
    if raw is None or raw.error or raw.rax != buffer:
        raise RuntimeError("native owned-storage copy did not complete; retain inputs")
    copied = process.read(buffer, size)
    process.free(buffer); state["owned_buffers"].remove(buffer)
    await source_check(state, label + "_source_after", source_handle)
    return copied


async def _scope_valid(state, label, tag, full_id, source_handle):
    _u32(full_id, "native reference")
    await source_check(state, label + "_source_before", source_handle)
    process = state["process"]
    target = resolve_v3_export(process, state["script_adapter"], "PdxV3ScriptScopeValid")
    buffer = process.allocate(20)
    state["owned_buffers"].append(buffer)  # Constructor/write failure still owns input.
    process.write(buffer, struct.pack("<H3HQI", tag, 0, 0, 0, full_id, 0xFFFFFFFF))
    checked = await complete(state, label, lambda: v3_access.submit_call(state["client"], target,
        (buffer, buffer + 16), mode=1, flag=0, dispatch="interface",
        adapter=state["access_adapter"], call_adapter=state["call_adapter"], process=process))
    raw = checked.callee_result
    if raw is None or raw.error or raw.rax & 0xFFFFFFFF:
        raise RuntimeError("native typed-scope validity call failed; retain its input")
    valid = struct.unpack("<I", process.read(buffer + 16, 4))[0]
    if valid not in (0, 1):
        raise RuntimeError("native scope validity output was not written as Boolean")
    process.free(buffer); state["owned_buffers"].remove(buffer)
    await source_check(state, label + "_source_after", source_handle)
    return {"scope_type": tag, "full_id": full_id, "valid": bool(valid),
            "native_return": int(raw.rax & 0xFFFFFFFF)}


async def _types(state, capacity):
    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
        raise ValueError("type_page_capacity must be a positive integer")
    start = 0
    while True:
        result = await complete(state, "types_" + str(start), lambda start=start: data.enumerate_types(
            state["client"], start=start, capacity=capacity,
            adapter=state["data_adapter"], process=state["process"]))
        for row in result.rows:
            if row.name in state["types"] and state["types"][row.name] != row.type_id:
                raise RuntimeError("ambiguous native Data type name")
            state["types"][row.name] = row.type_id
        if result.next >= result.total: break
        if result.next <= start: raise RuntimeError("native type page did not advance")
        start = result.next
    for name in ("Country", "Treaty", "Article", "ArticleType"):
        if name not in state["types"]: raise RuntimeError("native Data type unavailable: " + name)


async def _full_id(state, label, value, source_handle):
    return struct.unpack("<I", await _copy_owned_storage(state, label,
        value.pointer + 0x10, 4, source_handle))[0]


async def _parties(state, treaty, binding):
    for name in ("GetLeftCountry", "GetRightCountry"):
        label = "treaty_" + str(treaty["query_occurrence"]) + "_" + name
        result = await _evaluate(state, label, "Treaty." + name, binding)
        value = _owned_object(state, result, "CDataCountry", "Country")
        full_id = await _full_id(state, label + "_full_id", value, result.value_handle)
        validity = await _scope_valid(state, label + "_valid", COUNTRY_SCOPE, full_id, result.value_handle)
        treaty["country_views"].append({"view": name, "full_country_id": full_id,
            "native_validity": validity, "owned_any_handle": result.value_handle,
            "data_wrapper": value.pointer, "pointers_live": True,
            "status": "native_country_ref_observed" if validity["valid"] else "invalid_country_ref_preserved",
            "semantics": "original selected-country view, not presumed fixed physical80/84 order"})
        if not validity["valid"]: state["row_errors"] += 1


async def _articles(state, treaty, binding):
    for view in VIEWS:
        prefix = "treaty_" + str(treaty["query_occurrence"]) + "_" + view
        source = await _evaluate(state, prefix + "_source", "Treaty." + view, binding)
        view_state = {"name": view, "source_handle": source.value_handle,
            "native_count": None, "article_row_indices": [], "pointers_live": True}
        treaty["views"].append(view_state)
        count = await complete(state, prefix + "_count", lambda: data.list_count(state["client"],
            source.value_handle, adapter=state["data_adapter"], process=state["process"]))
        view_state["native_count"] = count.total  # Zero is a real native result.
        for index in range(count.total):
            element = await complete(state, prefix + "_element_" + str(index), lambda index=index: data.list_element(
                state["client"], source.value_handle, index,
                adapter=state["data_adapter"], process=state["process"]))
            value = _owned_object(state, element, "CDataTreatyArticle", "Article")
            full_id = await _full_id(state, prefix + "_id_" + str(index), value, element.value_handle)
            valid = await _scope_valid(state, prefix + "_valid_" + str(index), ARTICLE_SCOPE,
                full_id, element.value_handle)
            row = {"treaty_query_occurrence": treaty["query_occurrence"],
                "treaty_source_index": treaty["source_index"], "source_treaty_full_id": treaty["full_treaty_id"],
                "view": view, "article_index": index, "full_article_id": full_id,
                "native_validity": valid, "owned_element": element,
                "element_handle": element.value_handle, "data_wrapper": value.pointer,
                "retained_binding": data.ObjectBinding(state["types"]["Article"], element.value_handle, retained=True),
                "source_world_witness": state["world"], "definition_origin_pointer": None,
                "definition_ordinal": None, "definition_is_instance_id": False,
                "definition_origin_db_joined": False,
                "pointers_live": True, "status": "native_article_ref_observed"}
            view_state["article_row_indices"].append(len(state["articles"]))
            state["articles"].append(row)  # Preserve every view occurrence; never deduplicate.
            if not valid["valid"]:
                row["status"] = "invalid_article_ref_preserved"; state["row_errors"] += 1; continue
            type_result = await _evaluate(state, prefix + "_type_" + str(index),
                "Article.GetArticleType", row["retained_binding"])
            type_value = _owned_object(state, type_result, "CDataTreatyArticleType", "ArticleType")
            origin = struct.unpack("<Q", await _copy_owned_storage(state, prefix + "_origin_" + str(index),
                type_value.pointer + 0x10, 8, type_result.value_handle))[0]
            fallback = struct.unpack("<Q", await _copy_owned_storage(state, prefix + "_default_type_" + str(index),
                state["image_base"] + ARTICLE_TYPE_NULL_SLOT, 8, type_result.value_handle))[0]
            row.update(definition_origin_pointer=origin, native_definition_fallback=fallback,
                article_type_handle=type_result.value_handle, article_type_data_wrapper=type_value.pointer,
                owned_type_result=type_result,
                article_type_return_type_id=type_result.type_id,
                article_type_context_type_id=state["types"]["ArticleType"])
            final = await _scope_valid(state, prefix + "_final_ref_" + str(index), ARTICLE_SCOPE,
                full_id, element.value_handle)
            if not final["valid"] or not origin or origin == fallback:
                row["status"] = "article_or_definition_became_invalid"; state["row_errors"] += 1
            else: row["status"] = "owned_article_and_definition_origin_observed"
        await source_check(state, prefix + "_final_source", source.value_handle)


async def cleanup_completed(state):
    """Release only completed known opaque Any owners; unknown inputs stay held."""
    if state["pending"] is not None:
        raise RuntimeError("pending/unknown operation must settle before any owner or buffer free")
    # Cleanup is a one-way handoff: even partial cleanup must not advertise
    # old pointer projections as usable. owned_values remains the owner ledger.
    state["pointers_live"] = False
    for treaty in state["treaties"]:
        treaty["pointers_live"] = False
        for party in treaty["country_views"]: party["pointers_live"] = False
        for view in treaty["views"]: view["pointers_live"] = False
    for article in state["articles"]: article["pointers_live"] = False
    for handle in tuple(reversed(state["owned_values"])):
        result = await complete(state, "release_" + str(handle), lambda handle=handle: data.release(
            state["client"], handle, adapter=state["data_adapter"], process=state["process"]))
        if result.value_handle: raise RuntimeError("native Any release did not consume its handle")
        state["owned_values"].remove(handle)
    for buffer in tuple(state["owned_buffers"]):
        state["process"].free(buffer); state["owned_buffers"].remove(buffer)
    state["sources_retained"] = False
    state["pointers_live"] = False
    for treaty in state["treaties"]:
        treaty["pointers_live"] = False
        for party in treaty["country_views"]: party["pointers_live"] = False
        for view in treaty["views"]: view["pointers_live"] = False
    for article in state["articles"]: article["pointers_live"] = False
    state["status_before_cleanup"] = state["status"]
    state["status"] = "query_complete_released"
    return state


async def query_treaties(client, process, *, country_id, image_base,
                         data_adapter="pdx_v3_data.dll", access_adapter="pdx_v3_access.dll",
                         call_adapter="pdx_native_call.dll", script_adapter="pdx_v3_script.dll",
                         type_page_capacity=256, treaty_indices=None, include_parties=True,
                         retain_sources=False):
    """Read current native Treaty views. No current/selected Country fallback.

    Source worlds are captured/checked by the native retained-Any graph. world
    here is a consistent caller witness, not a fabricated regular-Any epoch or
    a later poll substituted for native generation_world. All raw handles,
    bindings/adapters/retained owners remain public in the returned state.
    """
    _u32(country_id, "country_id")
    if not isinstance(image_base, int) or isinstance(image_base, bool) or image_base <= 0:
        raise ValueError("image_base must be a fresh positive native image base")
    state = new_state(client, process, country_id, data_adapter)
    state.update(country_id=country_id, image_base=image_base, access_adapter=access_adapter,
        call_adapter=call_adapter, script_adapter=script_adapter, owned_buffers=[],
        treaties=[], articles=[], types={}, row_errors=0, world=None,
        sources_retained=False, pointers_live=True, status="query_started")
    try:
        state["world"] = observation(state, "initial_world")
        await _types(state, type_page_capacity)
        binding = data.ObjectBinding.country(state["types"]["Country"], country_id)
        source = await _evaluate(state, "in_force_treaties", "Country.GetInForceTreaties", binding)
        state["source"] = source
        count = await complete(state, "treaty_count", lambda: data.list_count(client, source.value_handle,
            adapter=data_adapter, process=process))
        state["native_treaty_count"] = count.total
        selected = tuple(range(count.total)) if treaty_indices is None else tuple(treaty_indices)
        if any(not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < count.total
               for index in selected): raise ValueError("explicit Treaty index is outside the current native source")
        state["selected_treaty_indices"] = selected
        state["all_source_indices_in_native_order"] = selected == tuple(range(count.total))
        for occurrence, index in enumerate(selected):
            element = await complete(state, "treaty_element_" + str(occurrence), lambda index=index: data.list_element(
                client, source.value_handle, index, adapter=data_adapter, process=process))
            value = _owned_object(state, element, "CDataTreaty", "Treaty")
            full_id = await _full_id(state, "treaty_id_" + str(occurrence), value, element.value_handle)
            validity = await _scope_valid(state, "treaty_valid_" + str(occurrence), TREATY_SCOPE,
                full_id, element.value_handle)
            treaty = {"query_occurrence": occurrence, "source_index": index,
                "full_treaty_id": full_id, "native_validity": validity,
                "owned_element": element, "element_handle": element.value_handle,
                "data_wrapper": value.pointer, "retained_binding": data.ObjectBinding(
                    state["types"]["Treaty"], element.value_handle, retained=True),
                "source_world_witness": state["world"], "country_views": [], "views": [],
                "pointers_live": True, "status": "native_treaty_ref_observed"}
            state["treaties"].append(treaty)
            if not validity["valid"]:
                treaty["status"] = "invalid_treaty_ref_preserved"; state["row_errors"] += 1; continue
            if include_parties:
                await _parties(state, treaty, treaty["retained_binding"])
                treaty["source_country_in_observed_party_views"] = country_id in {
                    party["full_country_id"] for party in treaty["country_views"]}
                treaty["party_inclusion_semantics"] = "diagnostic only, not complete Treaty participation/authority"
                if any(not party["native_validity"]["valid"] for party in treaty["country_views"]):
                    treaty["status"] = "invalid_party_ref_preserved_views_not_queried"
                    continue  # Unknown view counts are not fabricated as native zero.
            await _articles(state, treaty, treaty["retained_binding"])
            final = await _scope_valid(state, "treaty_final_ref_" + str(occurrence), TREATY_SCOPE,
                full_id, element.value_handle)
            if not final["valid"]:
                treaty["status"] = "treaty_became_invalid"; state["row_errors"] += 1
            else: treaty["status"] = "native_views_observed"
        await source_check(state, "final_source", source.value_handle)
        state["view_membership_occurrences"] = len(state["articles"])
        state["view_query_coverage_complete"] = all(len(row["views"]) == len(VIEWS)
            and all(view["native_count"] is not None for view in row["views"])
            for row in state["treaties"])
        state["status"] = "query_complete_sources_retained"
        if retain_sources:
            state["sources_retained"] = True
        else: await cleanup_completed(state)
        return state
    except BaseException as error:
        state["status"] = "query_failed_retained"
        state["failure"] = {"type": type(error).__name__, "message": str(error)[:500]}
        error.query_state = state
        raise
