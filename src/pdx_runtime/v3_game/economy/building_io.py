"""Native Building goods input/output reads with resumable query ownership."""
from __future__ import annotations

from pdx_runtime.v3_wire import V3Wire, resolve_v3_export
from pdx_runtime import v3_data as data

GETTERS = ("GetConsumption", "GetProduction", "GetConsumptionValue", "GetProductionValue")
LABELS = ("GetKey", "GetName")
PROVIDERS = {"consumed": "AccessConsumedGoods", "produced": "AccessProducedGoods"}
OWNED_GOODS_ANY = ".?AU?$SAnyType@V?$CPdxPtr@VCDataGoods@@@@@PdxAnyImpl@@"


def _integer(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer in [{lower}, {upper}]")
    return value


def _page_end(result, start):
    if not (isinstance(result.next, int) and isinstance(result.total, int)
            and result.total >= 0 and 0 <= result.next <= result.total):
        raise ValueError("invalid native metadata pagination")
    if result.next < result.total and result.next <= start:
        raise ValueError("native metadata pagination did not advance")
    return result.next >= result.total


def _single(rows, name, arguments=None):
    selected = [row for row in rows if row.name == name]
    if len(selected) != 1:
        return None, f"expected one named {name} row; observed {len(selected)}"
    row = selected[0]
    if arguments is not None and row.argument_count != arguments:
        return None, f"{name} argument_count is {row.argument_count}, expected {arguments}"
    return row, None


def _unavailable(state, reason):
    for name in LABELS + GETTERS:
        state["fields"].setdefault(name, {"status": "context_unavailable", "reason": reason})
    state["status"] = "complete_with_gaps"
    state["phase"] = "done"


def _metadata_key(client, process, adapter):
    """Metadata identity only. Missing cache identity never blocks a normal read."""
    if process is None:
        return None
    try:
        status = V3Wire(client).world_status()
        world = status.get("world", 0)
        world = int(world, 0) if isinstance(world, str) else int(world)
        if status.get("status") != "ok" or not world:
            return None
        callback = resolve_v3_export(process, adapter, "PdxV3DataInterface")
        owner = process.export_owner(callback)
        if isinstance(owner, bool) or not isinstance(owner, int) or owner <= 0:
            return None
        return world, owner
    except Exception:
        return None


def _metadata_ready(state):
    cache, key = state.get("metadata_cache"), state.get("metadata_cache_key")
    if cache is not None and key is not None and not state["metadata_errors"]:
        # Actual immutable metadata rows only. No provider, Any, retained value,
        # game object or source signature is reused between Buildings/sources.
        cache[key] = {"type_rows": tuple(state["type_rows"]),
            "function_rows": {name: tuple(rows) for name, rows in state["function_rows"].items()}}
    building = state["function_rows"]["Building"]
    goods = state["function_rows"]["Goods"]
    for name in GETTERS:
        row, gap = _single(building, name, 1)
        state["signatures"][name] = {"row": row, "gap": gap}
        if gap:
            state["fields"][name] = {"status": "metadata_gap", "reason": gap}
    for name in LABELS:
        row, gap = _single(goods, name, 0)
        state["signatures"][name] = {"row": row, "gap": gap}
        if gap:
            state["fields"][name] = {"status": "metadata_gap", "reason": gap}
    provider = PROVIDERS[state["source"]]
    row, gap = _single(building, provider, 0)
    state["signatures"][provider] = {"row": row, "gap": gap}
    self_row, self_gap = _single(goods, "Self", 0)
    state["signatures"]["Goods.Self"] = {"row": self_row, "gap": self_gap}
    if gap:
        _unavailable(state, gap)
        return
    if self_gap:
        for name in GETTERS:
            state["fields"][name] = {"status": "metadata_gap", "reason": self_gap}
    state["field_order"] = [name for name in LABELS + GETTERS
                            if state["signatures"][name]["row"] is not None
                            and not (name in GETTERS and self_gap)]
    state["field_index"] = 0
    state["phase"] = "source"


def _next_function_receiver(state):
    state["receiver_index"] += 1
    state["start"] = 0
    if state["receiver_index"] == 2:
        _metadata_ready(state)


def _accept_result(state, result):
    pending = state["pending"]
    label, phase = pending["label"], state["phase"]
    pending["result"] = result
    pending.pop("factory", None)
    state["pending"] = None
    if result.action in (data.Action.EVALUATE, data.Action.LIST_ELEMENT) and result.value_handle:
        if result.value_handle not in state["owned_values"]:
            state["owned_values"].append(result.value_handle)
    if phase == "release":
        handle = pending["value_handle"]
        if result.error or result.value_handle != 0:
            state["release_errors"].append({"value_handle": handle, "result": result,
                "reason": "native_error" if result.error else "native release did not clear its handle"})
        else:
            state["released_values"].append(handle)
            state["owned_values"].remove(handle)
        state["release_index"] += 1
        return
    if phase == "types":
        if result.error:
            state["metadata_errors"].append({"label": label, "result": result})
            _unavailable(state, "current native type enumeration failed")
            return
        state["type_rows"].extend(result.rows)
        try:
            ended = _page_end(result, state["start"])
        except ValueError as exc:
            state["metadata_errors"].append({"label": label, "reason": str(exc), "result": result})
            _unavailable(state, str(exc))
            return
        if not ended:
            state["start"] = result.next
            return
        for receiver in ("Building", "Goods"):
            row, gap = _single(state["type_rows"], receiver)
            if gap:
                _unavailable(state, gap)
                return
            state["types"][receiver] = row.type_id
        state["phase"] = "functions"
        state["receiver_index"] = 0
        state["start"] = 0
        return
    if phase == "functions":
        receiver = ("Building", "Goods")[state["receiver_index"]]
        if result.error:
            state["metadata_errors"].append({"label": label, "result": result})
            _next_function_receiver(state)
            return
        state["function_rows"][receiver].extend(result.rows)
        try:
            ended = _page_end(result, state["start"])
        except ValueError as exc:
            state["metadata_errors"].append({"label": label, "reason": str(exc), "result": result})
            _next_function_receiver(state)
            return
        if ended:
            _next_function_receiver(state)
        else:
            state["start"] = result.next
        return
    if phase == "source":
        state["source_result"] = result
        if result.error or not result.value_handle or result.kind != data.Kind.LIST:
            _unavailable(state, "native Building Goods list was not obtained; see source_result")
        else:
            state["phase"] = "count"
        return
    if phase == "count":
        state["count_result"] = result
        if result.error:
            _unavailable(state, "native Goods list count failed; see count_result")
        elif not 0 <= state["goods_index"] < result.total:
            _unavailable(state, "selected Goods index is absent from this native list")
        else:
            state["phase"] = "element"
        return
    if phase == "element":
        state["element_result"] = result
        state["element_return_type_id"] = result.type_id  # Preserve the original provider alias.
        if (result.error or result.kind != data.Kind.OBJECT or not result.object
                or not result.value_handle or result.native_type_name != OWNED_GOODS_ANY):
            _unavailable(state, "provider did not return the exact owned CDataGoods Any")
        else:
            state["phase"] = "fields"
        return
    if phase == "fields":
        name = state["field_order"][state["field_index"]]
        field = {"status": "native_error" if result.error else "ok", "result": result,
                 "expression": pending["expression"], "kind": result.kind,
                 "raw_bits": result.raw_bits, "snapshot": bytes(result.snapshot),
                 "native_type_name": result.native_type_name}
        state["fields"][name] = field
        if not result.error:
            try:
                if name in GETTERS:
                    if result.kind != data.Kind.FIXED:
                        raise ValueError("original IO getter did not return native fixed-point")
                    field["value"] = result.value  # FixedPoint bits, no invented scale/unit.
                else:
                    if (result.kind != data.Kind.STRING or result.snapshot_bytes != result.total
                            or len(result.snapshot) != result.total):
                        raise ValueError("native Goods label is not a complete string snapshot")
                    field["value"] = bytes(result.snapshot).decode("utf-8", "strict")
            except (ValueError, UnicodeError) as exc:
                field.update(status="payload_error", reason=str(exc))
        state["field_index"] += 1
        return
    raise RuntimeError(f"unexpected Building IO phase {phase}")


def _submit_next(state):
    client, process, adapter = state["client"], state["process"], state["adapter"]
    options = {"process": process, "adapter": adapter}
    phase = state["phase"]
    expression = None
    release_handle = None
    if phase == "types":
        label, action = f"types:{state['start']}", data.Action.TYPES
        make = lambda: data.enumerate_types(client, start=state["start"], capacity=state["page_capacity"], **options)
    elif phase == "functions":
        receiver = ("Building", "Goods")[state["receiver_index"]]
        label, action = f"functions:{receiver}:{state['start']}", data.Action.FUNCTIONS
        make = lambda: data.enumerate_functions(client, state["types"][receiver],
            start=state["start"], capacity=state["page_capacity"], **options)
    elif phase == "source":
        expression = "Building." + PROVIDERS[state["source"]]
        label, action = "building_goods_source", data.Action.EVALUATE
        bindings = (data.ObjectBinding.building(state["types"]["Building"], state["building_full_id"]),)
        make = lambda: data.evaluate(client, expression, bindings, output_bytes=state["output_bytes"], **options)
    elif phase == "count":
        label, action = "building_goods_count", data.Action.LIST_COUNT
        make = lambda: data.list_count(client, state["source_result"].value_handle, **options)
    elif phase == "element":
        label, action = "building_owned_goods", data.Action.LIST_ELEMENT
        make = lambda: data.list_element(client, state["source_result"].value_handle,
            state["goods_index"], output_bytes=state["output_bytes"], **options)
    elif phase == "fields":
        if state["field_index"] == len(state["field_order"]):
            state["phase"] = "done"
            state["status"] = ("complete" if not state["metadata_errors"]
                and all(field["status"] == "ok" for field in state["fields"].values())
                else "complete_with_gaps")
            return False
        name = state["field_order"][state["field_index"]]
        # Keep the element Any and its reported type_id unchanged. The current
        # named Goods converter accepts/rejects that original owned Any itself.
        goods = data.ObjectBinding(state["types"]["Goods"], state["element_result"].value_handle, retained=True)
        if name in GETTERS:
            expression = f"Building.{name}(Goods.Self)"
            bindings = (data.ObjectBinding.building(state["types"]["Building"], state["building_full_id"]), goods)
        else:
            expression, bindings = f"Goods.{name}", (goods,)
        label, action = name, data.Action.EVALUATE
        make = lambda: data.evaluate(client, expression, bindings, output_bytes=state["output_bytes"], **options)
    elif phase == "release":
        if state["release_index"] == len(state["release_order"]):
            state["phase"] = "done"
            state["owners_released"] = not state["owned_values"]
            state["pointers_live"] = not state["owners_released"]
            state["status"] = "released" if state["owners_released"] else "release_incomplete"
            return False
        release_handle = state["release_order"][state["release_index"]]
        label, action = f"release:{release_handle}", data.Action.RELEASE
        make = lambda: data.release(client, release_handle, **options)
    else:
        return False
    # Record before dispatch. The optional scheduler retries only known unqueued
    # admissions; an uncertain original callback is always retained, never replayed.
    pending = {"label": f"building_io:{label}", "action": action, "expression": expression,
               "value_handle": release_handle, "query": None}
    state["pending"] = pending
    state["queries"].append(pending)
    if state.get("submit_query") is not None:
        pending["factory"] = make
        return True  # The async continuation owns scheduling and this exact call.
    try:
        pending["query"] = make()
    except Exception as exc:
        pending["submission_exception"] = exc
        pending["query"] = getattr(exc, "query", None)
        state["status"] = "pending_unknown" if pending["query"] is not None else "submission_unknown"
        return False  # Report the unknown attempt; a later continue completes this same query.
    return True


def _configure_submit(state, submit_query):
    if submit_query is not None:
        state["submit_query"] = submit_query
    pending = state.get("pending")
    error = pending.get("submission_exception") if pending is not None else None
    # The original SDK explicitly rejected this very first metadata admission
    # and released its storage. No callback/query or Any was ever accepted.
    if (state.get("submit_query") is not None and pending is not None
            and pending.get("query") is None and not state.get("owned_values")
            and state["phase"] == "types" and state["start"] == 0
            and pending.get("action") == data.Action.TYPES
            and getattr(error, "operation", None) == "V3 query submit"
            and getattr(error, "winerror", None) == 170
            and getattr(error, "query", None) is None):
        state.setdefault("rejected_submissions", []).append(pending)
        state["pending"] = None
        state["status"] = "retrying_known_prequeue_busy"


async def continue_building_io(state, *, await_result, submit_query=None):
    """Continue the same exact pending query, then remaining concrete IO reads.

    None/exception/wrong receipt preserves pending and all known source owners.
    Completed field errors do not prevent other independent fields from running.
    """
    _configure_submit(state, submit_query)
    while state["phase"] != "done":
        if state["pending"] is None and not _submit_next(state):
            break
        pending = state["pending"]
        if (pending["query"] is None and pending.get("factory") is not None
                and not pending.get("submission_attempted")):
            pending["submission_attempted"] = True
            try:
                pending["query"] = await state["submit_query"](pending["factory"])
            except Exception as exc:
                pending["submission_exception"] = exc
                pending["query"] = getattr(exc, "query", None)
        if pending["query"] is None:
            state["status"] = "submission_unknown"
            return state
        try:
            result = await await_result(pending["label"], pending["query"])
        except Exception as exc:
            pending["completion_exception"] = exc
            state["status"] = "pending_unknown"
            return state
        if result is None:
            state["status"] = "pending_unknown"
            return state
        if not isinstance(result, data.DataResult) or result.action != pending["action"]:
            pending["unexpected_receipt"] = result
            state["status"] = "pending_unknown"
            return state
        pending.pop("completion_exception", None)
        pending.pop("unexpected_receipt", None)
        _accept_result(state, result)
    return state


async def start_building_io(client, *, building_full_id, await_result,
        source="produced", goods_index=0, process=None, adapter="pdx_v3_data.dll",
        page_capacity=64, output_bytes=4096, metadata_cache=None, submit_query=None):
    """Discover current signatures and read ONE caller-selected real Goods element.

    No automatic candidate selection or alternate source is attempted. Metadata
    type IDs are current named rows. Output fields preserve raw native FixedPoint
    bits and original DataResult. Missing labels remain explicit metadata gaps.
    """
    _integer(building_full_id, "building_full_id", 0, 0xfffffffe)
    _integer(goods_index, "goods_index", 0, 0x7fffffff)
    _integer(page_capacity, "page_capacity", 1, 0x7fffffff)
    _integer(output_bytes, "output_bytes", 0, 0xffffffff)
    if source not in PROVIDERS:
        raise ValueError("source must be produced or consumed")
    state = {"client": client, "process": process, "adapter": adapter,
        "building_full_id": building_full_id, "source": source, "goods_index": goods_index,
        "page_capacity": page_capacity, "output_bytes": output_bytes,
        "phase": "types", "start": 0, "status": "reading", "pending": None,
        "queries": [], "owned_values": [], "type_rows": [], "types": {},
        "function_rows": {"Building": [], "Goods": []}, "metadata_errors": [],
        "signatures": {}, "fields": {}, "atomic": False,
        "observation_scope": "separate callbacks over one held native source/element; not an atomic table",
        "fixed_point_scale": None, "fixed_point_unit": None,
        "released_values": [], "release_errors": [], "owners_released": False,
        "pointers_live": True, "submit_query": submit_query, "metadata_cache": metadata_cache,
        "metadata_cache_key": None, "metadata_cache_hit": False}
    if metadata_cache is not None:
        key = _metadata_key(client, process, adapter)
        state["metadata_cache_key"] = key
        cached = metadata_cache.get(key) if key is not None else None
        if cached is not None:
            state["type_rows"] = list(cached["type_rows"])
            state["function_rows"] = {name: list(rows) for name, rows in cached["function_rows"].items()}
            for receiver in ("Building", "Goods"):
                row, gap = _single(state["type_rows"], receiver)
                if gap:
                    break
                state["types"][receiver] = row.type_id
            else:
                state["metadata_cache_hit"] = True
                _metadata_ready(state)  # Rebuild each source's actual signatures.
    return await continue_building_io(state, await_result=await_result)


async def start_release_building_io(state, *, await_result):
    """Release known value owners once, in child-before-source order.

    No automatic release of an unknown operation. Only error0 plus a cleared
    value_handle removes an owner. Other receipts keep the owner and record the
    failure. continue_building_io resumes an unknown release
    using that exact original query.
    """
    if state["pending"] is not None or state["phase"] != "done":
        raise RuntimeError("finish the exact pending read/release before releasing owners")
    if "release_order" in state:
        raise RuntimeError("release was already attempted; do not replay releases")
    state["release_order"] = list(reversed(state["owned_values"]))
    state["release_index"] = 0
    state["phase"] = "release"
    state["status"] = "releasing"
    return await continue_building_io(state, await_result=await_result)


def _save_bulk_row(state):
    index = state["bulk_current"]
    count = state.get("count_result")
    element = state.get("element_result")
    state["bulk_count_observations"].append({"index": index, "result": count})
    if count is not None and not count.error:
        if count.total != state["bulk_initial_count"]:
            state["bulk_diagnostics"].append({"index": index, "kind": "count_changed",
                "initial_count": state["bulk_initial_count"], "observed_count": count.total})
        if index >= count.total:
            state["bulk_diagnostics"].append({"index": index, "kind": "index_absent_at_count",
                "observed_count": count.total})
    elif count is not None:
        state["bulk_diagnostics"].append({"index": index, "kind": "count_error", "result": count})
    if element is not None and element.error:
        state["bulk_diagnostics"].append({"index": index, "kind": "element_error_after_count",
            "result": element})
    fields = state["fields"]
    state["rows"].append({"index": index, "count_result": count,
        "element_result": element, "element_return_type_id": state.get("element_return_type_id"),
        "fields": fields, "status": "ok" if all(value["status"] == "ok"
            for value in fields.values()) else "with_gaps"})
    state["bulk_next"] = index + 1
    state["bulk_current"] = None


async def continue_all_building_io(state, *, await_result, submit_query=None):
    """Continue the original pending query and the remaining original-count indices.

    This keeps the same metadata, true provider owner, and owner ledger. Each
    original index gets a fresh native count/bounds check and list_element call.
    Equal counts cannot prove equal contents or order. No stable Goods identity,
    atomic table, freeze, deduplication, or newly grown-index coverage is inferred.
    start_release_building_io(state, ...) releases all retained row owners.
    """
    if not state.get("bulk"):
        raise ValueError("requires the state returned by read_all_building_io")
    _configure_submit(state, submit_query)
    if "release_order" in state:
        return await continue_building_io(state, await_result=await_result)
    if state["bulk_complete"]:
        return state
    while True:
        if state["phase"] != "done" or state["pending"] is not None:
            await continue_building_io(state, await_result=await_result)
            if state["phase"] != "done" or state["pending"] is not None:
                return state
        if state["bulk_initial_count"] is None:
            count = state.get("count_result")
            if count is None or count.error:
                state["bulk_diagnostics"].append({"kind": "initial_count_unavailable",
                    "count_result": count, "source_result": state.get("source_result")})
                state["bulk_complete"] = True
                state["bulk_current"] = None
                state["status"] = "bulk_complete_with_gaps"
                return state
            state["bulk_initial_count"] = count.total
            state["bulk_field_gaps"] = {name: dict(value) for name, value in state["fields"].items()
                                        if value["status"] == "metadata_gap"}
            if count.total == 0:
                state["bulk_count_observations"].append({"index": None, "result": count})
                state["bulk_current"] = None
        if state["bulk_current"] is not None:
            _save_bulk_row(state)
        if state["bulk_next"] >= state["bulk_initial_count"]:
            state["bulk_complete"] = True
            state["status"] = ("bulk_complete_with_gaps" if state["metadata_errors"]
                or state["bulk_field_gaps"] or state["bulk_diagnostics"]
                or any(row["status"] != "ok" for row in state["rows"]) else "bulk_complete")
            return state
        # Reset only the per-element portion. No metadata or source is recreated.
        state["bulk_current"] = state["bulk_next"]
        state["goods_index"] = state["bulk_current"]
        state["fields"] = {name: dict(value) for name, value in state["bulk_field_gaps"].items()}
        state["field_index"] = 0
        for name in ("count_result", "element_result", "element_return_type_id"):
            state.pop(name, None)
        state["phase"] = "count"
        state["status"] = "reading_all"


async def read_all_building_io(client, *, building_full_id, await_result,
        source="produced", process=None, adapter="pdx_v3_data.dll", page_capacity=64,
        output_bytes=4096, metadata_cache=None, submit_query=None):
    """Read every index in the first original provider count, with copied row results.

    Rows keep labels, all four IO fields, original raw bits, errors, and native
    receipts. The collection can change between callbacks: this traverses the
    INITIAL index range, not an atomic or stable-identity list. Changed counts and
    failed bounds/element observations stay in bulk_diagnostics. No alternate
    source, pointer cast, extra source evaluation, or automatic release is used.
    Unknown operations retain bulk_next/bulk_current and the exact state.pending.
    """
    state = await start_building_io(client, building_full_id=building_full_id,
        await_result=await_result, source=source, goods_index=0, process=process,
        adapter=adapter, page_capacity=page_capacity, output_bytes=output_bytes,
        metadata_cache=metadata_cache, submit_query=submit_query)
    state.update(bulk=True, rows=[], bulk_next=0, bulk_current=0,
        bulk_initial_count=None, bulk_complete=False, bulk_field_gaps={},
        bulk_count_observations=[], bulk_diagnostics=[], collection_may_change=True,
        observation_scope="initial native count indices; separate callbacks over one held provider; not an atomic table")
    if state["phase"] != "done" or state["pending"] is not None:
        return state  # Preserve the first unknown request instead of awaiting it again here.
    return await continue_all_building_io(state, await_result=await_result)
