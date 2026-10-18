"""Original visible Country journal models; no buttons, actions or raw leases."""
from __future__ import annotations
from types import SimpleNamespace
from ... import v3_data as data
from ._read import _text_info, _diagnostic
from .._text import plain_text as _plain

SOURCES = ("AccessVisibleJournalEntries", "AccessActiveJournalEntries")
BASIC = {"name": "GetName", "description": "GetReason", "active": "IsActive",
         "status": "GetStatusDesc", "progress": "GetProgressDesc", "goal": "GetGoalDesc",
         "valid": "IsValid", "instance_tag": "GetTooltipTag"}
DETAILS = {"completion_conditions": "GetCompletionTooltip", "completion_effects": "GetOnCompleteTooltip",
           "failure_conditions": "GetFailureTooltip", "failure_effects": "GetOnFailTooltip",
           "timeout": "GetTimeoutTooltip", "timeout_effects": "GetOnTimeoutTooltip",
           "activation_conditions": "GetActivationTooltip", "activation_effects": "GetOnActivationTooltip",
           "involved_countries": "GetInvolvedCountriesDesc", "tooltip": "GetTooltip",
           "can_complete": "CanComplete", "can_time_out": "CanTimeOut"}


def _pure(function):
    callbacks = getattr(function, "callbacks", ())
    return (function.argument_count == 0 and len(callbacks) == 4
            and any(callbacks[:2]) and not any(callbacks[2:]))


class _CleanupUnconfirmed(RuntimeError):
    pass


def _owner(state, result, role, parent=None):
    handle = getattr(result, "value_handle", 0)
    if not handle:
        return
    node = {"handle": handle, "result": result, "role": role, "parent": parent,
            "children": [], "release_attempted": False, "released": False}
    state["owners"][handle] = node
    if parent is not None:
        state["owners"][parent]["children"].append(handle)


async def _query(ctx, state, label, factory, *, parents=()):
    operation = {"label": label, "query": None, "result": None, "parents": tuple(parents)}
    state["operations"].append(operation)
    state["pending"] = operation
    def submit():
        try:
            query = factory()
        except BaseException as error:
            operation["query"] = getattr(error, "query", None)
            operation["error"] = error
            raise
        operation["query"] = query
        return query
    try:
        result = await ctx.query(label, submit)
    except BaseException as error:
        operation["error"] = error
        # An original query or cancellation can have an unpublished child owner.
        # Its parents stay strongly held. Ordinary prequeue rejections do not.
        if operation["query"] is None and isinstance(error, Exception):
            state["pending"] = None
        raise
    operation["result"] = result
    state["pending"] = None
    return result


async def _functions(ctx, type_id, state):
    cache = getattr(ctx, "_journal_metadata", None)
    if cache is None:
        cache = ctx._journal_metadata = {}
    key = (ctx.world, type_id)
    if key in cache:
        return cache[key]
    rows, start = [], 0
    while True:
        page = await _query(ctx, state, "journal.metadata", lambda:
            data.enumerate_functions(ctx.native, type_id, start=start, capacity=128,
                adapter=ctx.adapters["data"], process=ctx.process))
        if page.error:
            return {}, "Current journal metadata is unavailable (native error " + str(page.error) + ")."
        rows.extend(page.rows)
        if page.next <= start or page.next >= page.total:
            break
        start = page.next
    result = ({r.name: r for r in rows}, None)
    cache[key] = result
    return result


async def _release(ctx, state, result):
    handle = getattr(result, "value_handle", 0)
    if not handle:
        return True
    node = state["owners"][handle]
    if node["released"]:
        return True
    if (node["release_attempted"] or state["pending"] is not None or
            getattr(ctx, "pending_queries", ()) or
            any(not state["owners"][child]["released"] for child in node["children"])):
        return False
    node["release_attempted"] = True
    released = await _query(ctx, state, "journal.release", lambda:
        data.release(ctx.native, handle, adapter=ctx.adapters["data"], process=ctx.process), parents=(handle,))
    node["release_query"] = state["operations"][-1]["query"]
    node["release_result"] = released
    if released.error == 0 and getattr(released, "value_handle", None) == 0:
        node["released"] = True
        return True
    state["cleanup_unconfirmed"] = True
    return False


def _put(entry, name, result):
    booleans = {"active", "valid", "can_complete", "can_time_out"}
    expected = data.Kind.BOOL if name in booleans else data.Kind.STRING
    if result.error:
        entry[name] = None
        entry["unavailable"][name] = "The game did not provide this journal field (native error " + str(result.error) + ")."
    elif result.kind != expected:
        entry[name] = None
        entry["unavailable"][name] = "The game returned the wrong native value kind; expected " + ("a boolean." if name in booleans else "CString text.")
    elif expected == data.Kind.STRING:
        info = _text_info(SimpleNamespace(error=result.error, result=result))
        entry["text"][name] = info
        entry[name] = _plain(info["raw_text"]) if info["complete"] else None
        if info["reason"]:
            entry["unavailable"][name] = info["reason"]
    else:
        entry[name] = result.value


async def _owned_field(ctx, state, expression, bindings, raw, parent):
    capacity = 16384
    while True:
        result = await _query(ctx, state, "journal.field", lambda:
            data.evaluate(ctx.native, expression, bindings, output_bytes=capacity,
                adapter=ctx.adapters["data"], process=ctx.process), parents=(parent,))
        raw.append(result)
        _owner(state, result, "field", parent)
        released = await _release(ctx, state, result)
        if not released:
            raise _CleanupUnconfirmed("Journal read stopped with its original value cleanup retained internally.")
        if result.error != 234 or result.total <= capacity:
            return result
        capacity = result.total


async def _read(ctx, state, *, details=False):
    await ctx.refresh()
    world = ctx.world
    country_id = await ctx.country_id()
    country_type = await ctx.type_id("Country")
    journal_type = await ctx.type_id("JournalEntry")
    country_functions, country_error = await _functions(ctx, country_type, state)
    journal_functions, journal_error = await _functions(ctx, journal_type, state)
    output = {"entries": [], "available": False, "unavailable": {}, "scope": "visible"}
    if country_error or journal_error:
        output["unavailable"]["journal"] = country_error or journal_error
        return output
    getter = next((n for n in SOURCES if n in country_functions and _pure(country_functions[n])), None)
    if getter is None:
        output["unavailable"]["journal"] = "No original visible Country journal source was found in current metadata."
        return output
    expression = "Country." + getter
    bindings = (data.ObjectBinding.country(country_type, country_id),)
    evidence = state
    evidence.update(source=expression, world=world, country_id=country_id)
    _diagnostic(ctx, evidence)
    source = await _query(ctx, state, "journal.source", lambda:
        data.evaluate(ctx.native, expression, bindings, output_bytes=32,
            adapter=ctx.adapters["data"], process=ctx.process))
    evidence["source_result"] = source
    _owner(state, source, "source")
    try:
        if source.error or source.kind != data.Kind.LIST or not source.value_handle:
            output["unavailable"]["journal"] = "The original Country getter did not provide a journal list."
            return output
        counted = await _query(ctx, state, "journal.count", lambda:
            data.list_count(ctx.native, source.value_handle, adapter=ctx.adapters["data"], process=ctx.process), parents=(source.value_handle,))
        evidence["count_result"] = counted
        if counted.error:
            output["unavailable"]["journal"] = "The original journal source count is unavailable."
            return output
        output["available"] = True
        fields = {**BASIC, **(DETAILS if details else {})}
        # GetType/GetKey is the original type-key chain, not an invented entry ID.
        key_expression = None
        if "GetType" in journal_functions and _pure(journal_functions["GetType"]):
            try:
                type_id = await ctx.type_id("JournalEntryType")
                type_functions, _ = await _functions(ctx, type_id, state)
                if "GetKey" in type_functions and _pure(type_functions["GetKey"]):
                    key_expression = "JournalEntry.GetType.GetKey"
            except LookupError:
                pass
        for index in range(counted.total):
            raw = {"provider_position": index, "fields": {}}
            evidence["entries"].append(raw)
            element = await _query(ctx, state, "journal.entry_owner", lambda:
                data.list_element(ctx.native, source.value_handle, index, output_bytes=0,
                    adapter=ctx.adapters["data"], process=ctx.process), parents=(source.value_handle,))
            raw["owner_result"] = element
            _owner(state, element, "entry", source.value_handle)
            entry = {"id": None, "key": None, "text": {}, "unavailable": {}}
            try:
                if element.error or element.kind != data.Kind.OBJECT or not element.value_handle:
                    # Binding0/1 or raw-pointer Any is not an owning entry lease.
                    # One callback's whole tooltip is useful without pretending
                    # independent provider positions denote the same object.
                    entry["sampling"] = "single_callback_tooltip"
                    for name in fields:
                        entry[name] = None
                        entry["unavailable"][name] = "This provider did not expose an owned entry; separate fields are not joined by list position."
                    tooltip = journal_functions.get("GetTooltip")
                    if tooltip is not None and _pure(tooltip):
                        capacity = 65536
                        raw["tooltip_samples"] = []
                        while True:
                            copied = await _query(ctx, state, "journal.tooltip_sample", lambda:
                                data.evaluate_element(ctx.native, expression, index, "JournalEntry.GetTooltip", bindings,
                                    world=world, expected_count=counted.total, output_bytes=capacity,
                                    adapter=ctx.adapters["data"], process=ctx.process))
                            raw["tooltip_samples"].append(copied)
                            if copied.error != 234 or copied.result.total <= capacity:
                                break
                            capacity = copied.result.total
                        _put(entry, "tooltip", copied.result)
                    output["entries"].append(entry)
                    continue
                entry["sampling"] = "owned_entry"
                # Select the named receiver's original converter. The genuine
                # provider Any is retained unchanged, including any alias type.
                entry_bindings = (data.ObjectBinding(journal_type, element.value_handle, retained=True),)
                raw["receiver_type_id"] = journal_type
                expressions = {name: "JournalEntry." + n for name, n in fields.items()
                    if n in journal_functions and _pure(journal_functions[n])}
                if key_expression:
                    expressions["key"] = key_expression
                for name in fields:
                    if name not in expressions:
                        entry[name] = None
                        entry["unavailable"][name] = "This journal field has no available zero-argument native getter."
                for name, field_expression in expressions.items():
                    reads = raw["fields"][name] = []
                    result = await _owned_field(ctx, state, field_expression, entry_bindings, reads, element.value_handle)
                    _put(entry, name, result)
                entry["unavailable"]["id"] = "Current native JournalEntry metadata has no full entry ID getter."
                if entry["key"] is None:
                    entry["unavailable"].setdefault("key", "No native journal type-key source is available.")
                entry["definition_key"] = entry["key"]  # Definition key, never an instance ID.
                output["entries"].append(entry)
            finally:
                if not await _release(ctx, state, element):
                    state["cleanup_unconfirmed"] = True
        return output
    finally:
        if not await _release(ctx, state, source):
            state["cleanup_unconfirmed"] = True


async def read(ctx, *, details=False):
    # This strong ledger is separate from the bounded diagnostics ring. Native
    # query/owner/child dependencies stay reachable until cleanup is confirmed.
    if not hasattr(ctx, "_journal_reads"):
        ctx._journal_reads = {}
        ctx._journal_read_sequence = 0
    ctx._journal_read_sequence += 1
    identity = ctx._journal_read_sequence
    state = {"read_id": identity, "entries": [], "owners": {}, "operations": [],
             "pending": None, "cleanup_unconfirmed": False, "status": "reading"}
    ctx._journal_reads[identity] = state
    try:
        result = await _read(ctx, state, details=details)
        state["public"] = result
        return result
    except _CleanupUnconfirmed as error:
        state["error"] = error
        return {"entries": [], "available": False,
                "unavailable": {"journal": str(error)}, "scope": "visible"}
    except BaseException as error:
        state["error"] = error
        raise
    finally:
        complete = (state["pending"] is None and not state["cleanup_unconfirmed"] and
                    all(owner["released"] for owner in state["owners"].values()))
        state["status"] = "complete" if complete else "retained"
        if complete:
            ctx._journal_reads.pop(identity)
