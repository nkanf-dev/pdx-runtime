"""Native event-option and bounded field reads for the V3 events domain."""
from __future__ import annotations
from pdx_runtime import v3_data as data
from pdx_runtime.v3_game._text import plain_text

# Portable copy of the tested helper; source SHA256: a604eebc083ef7b3939cf1b0af4c56610873dc6845c6d28fdf33b97dd3550a1a
import struct
from pdx_runtime import native_call


def compose(runtime_read: int, application_root_cell: int, slots: int):
    """Return initial bytes, prepare_payload(address), decode_payload(bytes).

    Use ordinary v3_access.submit_callback with PdxNativeCallBatchInterface.
    No game pointer captured in one callback is used by a later callback.
    A larger fresh slots value is a new readonly observation, never a replay
    of mutation. slots is owned output capacity, not a game/business limit.
    No callback here calls an original game getter, constructor, or action.
    """
    if isinstance(slots, bool) or not isinstance(slots, int) or slots <= 0:
        raise ValueError("slots must be a positive owned capacity")
    if slots * 16 > 4096:
        raise ValueError("one raw read would exceed PDX_RUNTIME_MAX_RAW=4096 bytes")
    if not runtime_read or not application_root_cell:
        raise ValueError("explicit current export and exact PE root cell required")
    labels = ["app_cell", "app_prefix", "world_patch", "world_prefix",
              "manager_patch", "manager_prefix", "list_patch", "pointer_array"]
    for i in range(slots):
        labels += [f"event_{i}_patch", f"event_{i}_prefix", f"event_{i}_id_copy"]
    labels += ["metadata_copy", "scrub_app", "scrub_world", "scrub_manager",
               "scrub_pointers", "scrub_event_prefixes"]
    dynamic = [1, 3, 5, 7] + [9 + 3 * i for i in range(slots)]
    labels += [f"scrub_source_argument_{i}" for i in dynamic]
    call_count = len(labels)
    argument_start = native_call.BATCH_REQUEST.size + call_count * native_call.REQUEST.size
    end = argument_start + call_count * 32
    offsets = {}
    def reserve(name, size, align=8):
        nonlocal end
        end = (end + align - 1) // align * align
        offsets[name] = end
        end += size
    reserve("app", 0x610)
    reserve("world", 0x128)
    reserve("manager", 0x330)
    reserve("pointers", 8 * slots)
    reserve("event_prefixes", 16 * slots)
    reserve("ids", 4 * slots)
    reserve("metadata", 8)
    reserve("received", 4 * call_count)
    zero_size = max(0x610, 0x128, 0x330, 8 * slots, 16 * slots)
    reserve("zero", zero_size)
    total_size = end
    expected = []

    def prepare(address):
        nonlocal expected
        counts = offsets["received"]
        def at(name, delta=0): return address + offsets[name] + delta
        def arg(index): return address + argument_start + index * 32
        calls = []
        sizes = []
        def read(src, dst, size):
            index = len(calls)
            calls.append((runtime_read, (src, dst, size, address + counts + 4 * index)))
            sizes.append(size)
        read(application_root_cell, arg(1), 8)
        read(0, at("app"), 0x610)
        read(at("app", 0x608), arg(3), 8)
        read(0, at("world"), 0x128)
        read(at("world", 0x120), arg(5), 8)
        read(0, at("manager"), 0x330)
        read(at("manager", 0x320), arg(7), 8)
        read(0, at("pointers"), 8 * slots)
        for i in range(slots):
            read(at("pointers", 8 * i), arg(9 + 3 * i), 8)
            read(0, at("event_prefixes", 16 * i), 12)
            read(at("event_prefixes", 16 * i + 8), at("ids", 4 * i), 4)
        read(at("manager", 0x328), at("metadata"), 8)
        read(at("zero"), at("app"), 0x610)
        read(at("zero"), at("world"), 0x128)
        read(at("zero"), at("manager"), 0x330)
        read(at("zero"), at("pointers"), 8 * slots)
        read(at("zero"), at("event_prefixes"), 16 * slots)
        for index in dynamic:
            read(at("zero"), arg(index), 8)
        assert len(calls) == call_count
        expected = sizes
        result = native_call.encode_batch(calls, request_address=address)
        return result + bytes(total_size - len(result))

    initial = prepare(0)
    def decode(payload):
        batch = native_call.NativeCallBatchResult.from_bytes(payload)
        received = struct.unpack_from(f"<{call_count}I", payload, offsets["received"])
        capacity, count = struct.unpack_from("<ii", payload, offsets["metadata"])
        receipts = [{"label": labels[i], "adapter_error": call.error,
                     "read_error": call.rax & 0xffffffff, "received": received[i],
                     "requested": expected[i]}
                    for i, call in enumerate(batch.calls)]
        settled = (batch.error == 0 and batch.completed == call_count
                   and len(batch.calls) == call_count)
        def ok(i):
            return (i < len(receipts) and receipts[i]["adapter_error"] == 0
                    and receipts[i]["read_error"] == 0
                    and receipts[i]["received"] == receipts[i]["requested"])
        meta_index = 8 + 3 * slots
        header_ok = settled and all(ok(i) for i in list(range(7)) + [meta_index])
        header_ok = header_ok and 0 <= count <= capacity
        fits = header_ok and count <= slots
        # An empty native list has no required payload or element dereference.
        active_ok = fits and (count == 0 or ok(7))
        if active_ok:
            active_ok = all(ok(8 + 3 * i) and ok(9 + 3 * i) and ok(10 + 3 * i)
                            for i in range(count))
        scrub_start = meta_index + 1
        scrub_ok = settled and all(ok(i) for i in range(scrub_start, call_count))
        scrub_regions = [(offsets["app"], 0x610), (offsets["world"], 0x128),
                         (offsets["manager"], 0x330), (offsets["pointers"], 8 * slots),
                         (offsets["event_prefixes"], 16 * slots)]
        scrub_regions += [(argument_start + i * 32, 8) for i in dynamic]
        scrub_zero = all(not any(payload[o:o+n]) for o, n in scrub_regions)
        complete = bool(active_ok and scrub_ok and scrub_zero)
        ids = list(struct.unpack_from(f"<{count}I", payload, offsets["ids"])) if complete else []
        if 0xffffffff in ids:
            complete, ids = False, []
        return {"native_count": count, "native_capacity": capacity,
                "owned_slots": slots, "header_copied": bool(header_ok),
                "capacity_sufficient": bool(fits), "complete_ids_copied": complete,
                "full_ids": ids, "borrowed_storage_scrubbed": bool(scrub_ok and scrub_zero),
                "batch_error": batch.error, "completed": batch.completed,
                "receipts": receipts}
    # Layout is only caller-owned offsets for review and CPU fixtures.
    decode.layout = {"offsets": offsets, "call_count": call_count,
                     "argument_start": argument_start, "dynamic_source_calls": dynamic,
                     "labels": labels, "expected": expected, "size": total_size}
    return initial, prepare, decode

"""Ordinary async reads of one real pending Window and its original options.
Root supplies its matching SDK and ledger await. No connection, trigger or Select.
All stored values are copied native results, not retained Window/Option pointers.
"""
# Portable copy of the tested helper; source SHA256: bdf8f645858f412c541fc03f4c657a0fff21ec8ce9225dd26dbb474238269a25
async def _read_original_options(data, native, process, adapter, await_result,
                               world, interface_thread_id, window_type,
                               window_index, window_count, record):
    source = data.PointerArraySource("PopupManager.Self", 0x10, window_type,
        interface_thread_id, index=window_index, expected_count=window_count)
    record["options"] = []
    async def observed(label, submit):
        # Save the exact query before waiting. Unknown delivery never replays it.
        try:
            query = submit()
        except BaseException as error:
            record["submit_error"] = error
            if getattr(error, "query", None) is not None:
                record["pending"] = error.query
            raise
        record["pending"] = query
        result = await await_result(label, query)
        record.pop("pending", None)
        return result
    for name, expression in (("key", "EventWindow.GetEvent.GetKey"),
                             ("tag", "EventWindow.GetEvent.GetTooltipTag")):
        result = await observed("events.real." + name, lambda expression=expression:
            data.evaluate_pointer_array(native, source, expression, world=world,
                                        adapter=adapter, process=process))
        record[name] = result
        if result.error or result.result.kind != data.Kind.STRING:
            raise RuntimeError(("original Window/Event getter failed", name, result))
    # Original native opaque tooltip bytes carry the Event reference. Do not
    # decode/reconstruct them, or replace them with the definition key.
    record["instance_tag_bytes"] = record["tag"].result.snapshot
    first = await observed("events.option.first", lambda: data.evaluate_element(
        native, "EventWindow.GetOptions", 0, "EventOption.GetText", world=world,
        pointer_array=source, adapter=adapter, process=process))
    record["first_option"] = first
    if first.error:
        raise RuntimeError(("original option source has no completed first element", first))
    option_count = first.source_count
    for index in range(option_count):
        row = {}
        for field, expression in (("text", "EventOption.GetText"),
                                  ("enabled", "EventOption.IsEnabled"),
                                  ("default", "EventOption.IsDefaultOption")):
            result = first if index == 0 and field == "text" else await observed(
                "events.option." + str(index) + "." + field,
                lambda index=index, expression=expression: data.evaluate_element(
                    native, "EventWindow.GetOptions", index, expression,
                    world=world, expected_count=option_count, pointer_array=source,
                    adapter=adapter, process=process))
            row[field] = result
            if result.error:
                record["incomplete_option"] = row
                raise RuntimeError(("original option getter failed", index, field, result))
        if row["text"].result.kind != data.Kind.STRING or any(
                row[field].result.kind != data.Kind.BOOL for field in ("enabled", "default")):
            raise RuntimeError(("original option getter native types differ", row))
        # Index is the current provider position, NOT a script option ID/lease.
        row["provider_index"] = index
        record["options"].append(row)
    after = await observed("events.real.tag.after-options", lambda:
        data.evaluate_pointer_array(native, source, "EventWindow.GetEvent.GetTooltipTag",
                                    world=world, adapter=adapter, process=process))
    record["tag_after"] = after
    if after.error or after.result.snapshot != record["instance_tag_bytes"]:
        raise RuntimeError(("native instance changed during readonly observations", after))
    return record


# Additional getters are original metadata entries, all with zero arguments.
WINDOW_FIELDS = (
    ("title", "EventWindow.GetTitle", "STRING"),
    ("description", "EventWindow.GetDescription", "STRING"),
    ("flavor", "EventWindow.GetFlavor", "STRING"),
    ("title_tooltip", "EventWindow.GetTitleTooltip", "STRING"),
)
OPTION_FIELDS = (
    ("desc", "EventOption.GetDesc", "STRING"),
    ("highlighted", "EventOption.IsHighlightedOption", "BOOL"),
)


async def read_pending_options(data, native, process, adapter, await_result,
                               world, interface_thread_id, window_type,
                               window_index, window_count, record, *,
                               output_bytes=16384):
    """Read original options plus optional original text/flag getters.

    Existing keys/options remain native ElementResult objects. New Window fields
    are in record["window_fields"]; each option gains desc/highlighted results.
    field_diagnostics names each original expression, kind, byte completeness and
    native error. Exact bytes remain in result.result.snapshot. No label is used
    as a description, and no unsupported field becomes an empty invented value.

    Each extra query guards the copied original event tag inside its own native
    callback. The complete Python read spans callbacks, not an atomic snapshot;
    provider positions/counts are not stable option IDs. No action is submitted.
    Root owns the ledger and all query storage. Unknown queries stop this reader
    and stay in record["pending"]/record["unknown_query"]; never redispatch them.
    """
    if record.get("pending") is not None or record.get("unknown_query") is not None:
        raise RuntimeError("retain and reconcile the original reader query; do not redispatch")
    if "guard_expression" not in data.PointerArraySource.__dataclass_fields__:
        raise RuntimeError("matching source-guard SDK/base required")
    if isinstance(output_bytes, bool) or not isinstance(output_bytes, int) or output_bytes < 0:
        raise ValueError("output_bytes must be a nonnegative copied-byte capacity")
    try:
        await _read_original_options(data, native, process, adapter, await_result,
            world, interface_thread_id, window_type, window_index, window_count, record)
    except BaseException as error:
        record["core_read_error"] = error
        if record.get("pending") is not None:
            record["unknown_query"] = {"query": record["pending"], "error": error}
        raise
    record["core_identity_verified"] = True  # Original reader's bookend succeeded.
    record["window_fields"] = {}
    record["field_diagnostics"] = {}
    record["optional_reads"] = []
    record["atomic_snapshot"] = False

    async def observed(label, expression, submit):
        attempt = {"label": label, "expression": expression, "query": None, "result": None}
        record["optional_reads"].append(attempt)
        try:
            query = submit()
        except BaseException as error:
            attempt["submit_error"] = error
            query = getattr(error, "query", None)
            if query is not None:
                attempt["query"] = record["pending"] = query
            # The SDK preserves accepted/unknown deliveries in exception.query.
            # A missing query on an ordinary submit exception is prequeue/rejected.
            if query is not None or not isinstance(error, Exception):
                record["unknown_query"] = attempt
                raise
            return None, attempt
        attempt["query"] = record["pending"] = query
        try:
            result = await await_result(label, query)
        except BaseException as error:
            attempt["wait_error"] = error
            record["unknown_query"] = attempt
            raise  # Keep the exact original query and Root's owners. No fallback.
        attempt["result"] = result
        record.pop("pending", None)
        return result, attempt

    def diagnose(expression, result, attempt, kind_name):
        expected = int(getattr(data.Kind, kind_name))
        diagnostic = {"expression": expression, "expected_kind": expected, "ok": False}
        if result is None:
            diagnostic.update(status="submit_failed", error=attempt["submit_error"])
            return diagnostic
        value = result.result
        diagnostic.update(native_error=result.error, kind=int(value.kind),
            native_type_name=value.native_type_name, snapshot_bytes=value.snapshot_bytes,
            total=value.total, guard_checked=result.guard_checked, guard_passed=result.guard_passed)
        if result.error:
            diagnostic["status"] = "native_error"
        elif not result.guard_checked or not result.guard_passed:
            diagnostic["status"] = "source_guard_not_confirmed"
        elif int(value.kind) != expected:
            diagnostic["status"] = "unexpected_native_kind"
        elif kind_name == "STRING" and (value.snapshot_bytes != value.total or
                len(value.snapshot) != value.snapshot_bytes):
            diagnostic["status"] = "incomplete_native_bytes"
        else:
            diagnostic.update(status="ok", ok=True)
            if kind_name == "STRING":
                try:
                    value.snapshot.decode("utf-8")
                    diagnostic["utf8_valid"] = True
                except UnicodeDecodeError:
                    diagnostic.update(status="ok_raw_bytes", utf8_valid=False)
        return diagnostic

    try:
        copied_tag = record["tag"].value
        if copied_tag.encode("utf-8") != record["instance_tag_bytes"]:
            raise ValueError("opaque native tag is not losslessly copied UTF-8")
        guarded = data.PointerArraySource("PopupManager.Self", 0x10, window_type,
            interface_thread_id, index=window_index, expected_count=window_count,
            guard_expression="EventWindow.GetEvent.GetTooltipTag",
            guard_input=data.TypedInput.string(copied_tag))
    except Exception as error:
        # Core options remain available; do not execute unguarded optional reads.
        for name, expression, _kind in WINDOW_FIELDS:
            record["field_diagnostics"]["window." + name] = {
                "expression": expression, "status": "guard_input_unavailable", "ok": False, "error": error}
        for index, row in enumerate(record["options"]):
            for name, expression, _kind in OPTION_FIELDS:
                record["field_diagnostics"]["option." + str(index) + "." + name] = {
                    "expression": expression, "status": "guard_input_unavailable", "ok": False, "error": error}
        record["optional_identity_verified"] = False
        record["status"] = "original_options_with_optional_field_diagnostics"
        return record

    for name, expression, kind_name in WINDOW_FIELDS:
        result, attempt = await observed("events.window." + name, expression,
            lambda expression=expression: data.evaluate_pointer_array(native, guarded,
                expression, world=world, output_bytes=output_bytes, adapter=adapter, process=process))
        record["window_fields"][name] = result
        record["field_diagnostics"]["window." + name] = diagnose(expression, result, attempt, kind_name)
    for row in record["options"]:
        index = row["provider_index"]
        for name, expression, kind_name in OPTION_FIELDS:
            result, attempt = await observed("events.option." + str(index) + "." + name, expression,
                lambda index=index, expression=expression: data.evaluate_element(native,
                    "EventWindow.GetOptions", index, expression, world=world,
                    expected_count=len(record["options"]), pointer_array=guarded,
                    output_bytes=output_bytes, adapter=adapter, process=process))
            row[name] = result
            record["field_diagnostics"]["option." + str(index) + "." + name] = diagnose(
                expression, result, attempt, kind_name)
    # This bookend is a diagnostic, not a reason to discard already copied options.
    expression = "EventWindow.GetEvent.GetTooltipTag"
    after, attempt = await observed("events.optional.tag.after-fields", expression, lambda:
        data.evaluate_pointer_array(native, guarded, expression, world=world,
            output_bytes=output_bytes, adapter=adapter, process=process))
    record["tag_after_optional"] = after
    diagnostic = diagnose(expression, after, attempt, "STRING")
    record["field_diagnostics"]["identity.after_optional"] = diagnostic
    record["optional_identity_verified"] = bool(diagnostic["ok"] and
        after.result.snapshot == record["instance_tag_bytes"])
    record["status"] = ("original_options_and_optional_fields_read" if
        record["optional_identity_verified"] and all(d["ok"] for d in record["field_diagnostics"].values())
        else "original_options_with_optional_field_diagnostics")
    return record


def _diagnostic(ctx, record):
    diagnostics = ctx.diagnostics
    append = getattr(diagnostics, "append", None)
    if append is not None:
        append(record)
    else:
        rows = diagnostics.setdefault("events", [])
        rows.append(record)
        del rows[:-64]


def _copied(result, *, rich=False):
    if result is None:
        return None
    if result.error:
        return None
    try:
        value = result.value
    except (TypeError, ValueError):
        return None
    return plain_text(value) if rich and isinstance(value, str) else value


def _text_info(result, diagnostic=None):
    """Describe the exact copied native text, independently of display cleaning.

    complete reports native byte-copy/UTF-8 completeness, not visibility of all
    hidden script effects or existence of every optional localization scope.
    """
    if result is None:
        return {"raw_text": None, "raw_bytes": None, "complete": False,
                "reason": "The game did not provide this text."}
    value = result.result
    raw = value.snapshot
    reason = None
    complete = not result.error and value.kind == data.Kind.STRING
    if result.error:
        reason = "The game did not provide complete text (native error " + str(result.error) + ")."
    elif value.kind != data.Kind.STRING:
        reason = "The game returned a non-text value for this field."
    elif len(raw) != value.snapshot_bytes or value.snapshot_bytes != value.total:
        complete = False
        reason = "The game returned only part of this text (" + str(value.snapshot_bytes) + " of " + str(value.total) + " bytes)."
    if diagnostic is not None and not diagnostic.get("ok"):
        complete = False
        reason = reason or "This field was not confirmed for the original event instance."
    try:
        native_text = raw.decode("utf-8") if value.kind == data.Kind.STRING else None
    except UnicodeDecodeError:
        native_text = None
        complete = False
        reason = "The copied native bytes are not valid UTF-8; raw_bytes is preserved."
    return {"raw_text": native_text, "raw_bytes": raw, "complete": bool(complete), "reason": reason}
