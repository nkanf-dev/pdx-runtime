"""Read actual current Technology PODs and the original definition key.

Usage in Root's existing RuntimeSession:
    result = await read_current(session, process, currentintent)

currentintent is an ordinary dict with: world (expected current world),
optional actor_id (full Country ID; omitted uses the actual controlled country),
identity (Root's fresh current.json mapping: base and modules), and await_result
(the Root exact-query async waiter). Optional submit_query is an async factory
scheduler for the existing session. Optional with_progress consumes the current
receiver with the original progress/cost getters inside that same Access and
returns copied native fixed-point PODs. identity defaults to session.namespace's
identity. Optional record retains every operation. Runtime Country type, original
Interface thread and actual controlled Country are observed, never hardcoded.
No connection, loading, launch, command, time step, save or process close occurs.

The original getter returns a BORROWED CDataTechnology*. It is used only inside
ONE original owner-thread Access(1,0) callback. It is not held for a later call.
Country.Self has a separate genuine full-ID-created Country binding owner: its
raw pointer Any is not CPdxPtr, but its ValueNode retains the owning Binding2.
The original owner-thread Access/no-reentry contract is the native ABI premise,
not a new approval flag. Typed definition-pointer copies are provenance/equality
observations only, never input/lease. Only the copied ordinal enters the existing
Objects key reader. A fresh current POD confirms unchanged selection across that
key read. CPU tests do not prove runtime lifetime or actual current-key success.
"""
from __future__ import annotations

from pdx_runtime.v3_wire import V3Wire, resolve_v3_export
import struct
from ... import native_call, v3_access as access, v3_data as data, v3_player_control as player_control, v3_objects as objects

CURRENT_GETTER_RVA = 0x1D20740
CURRENT_PRESENT_RVA = 0x1D20690
CURRENT_PROGRESS_RVA = 0x1E67DC0
CURRENT_COST_RVA = 0x1E682F0
NORMAL_DEFINITION_VTABLE_RVA = 0x45149E0
NULL_DEFINITION_VTABLE_RVA = 0x4429808
COUNTRY_SELF_TYPE = ".?AU?$SAnyType@PEBVCDataCountry@@@PdxAnyImpl@@"


def _uint(name, value, bits, *, positive=False):
    if type(value) is not int or not (int(positive) <= value < (1 << bits)):
        raise ValueError(name + " must be an explicit " + ("positive " if positive else "") + "uint" + str(bits))
    return value


def compose_current(runtime_read, current_getter, present_getter, country_wrapper, image_base,
                    *, batch_handler=None, progress_getter=None, cost_getter=None):
    """Copy current identity and optionally consume current progress in ONE Access.

    Current Technology stays borrowed inside this callback. Optional original
    progress/cost getters receive that fresh current receiver and the genuinely
    owned Country.Self receiver. Only copied fixed-point PODs leave the callback.
    A nested ordinary-call batch has count0 when the original presence Bool is
    false. No default/no-current Technology is sent to the numeric getters.
    """
    inputs = (("runtime_read", runtime_read), ("current_getter", current_getter),
              ("present_getter", present_getter), ("country_wrapper", country_wrapper),
              ("image_base", image_base))
    for name, value in inputs:
        _uint(name, value, 64, positive=True)
    metrics = any(value is not None for value in (batch_handler, progress_getter, cost_getter))
    if metrics:
        for name, value in (("batch_handler", batch_handler), ("progress_getter", progress_getter),
                            ("cost_getter", cost_getter)):
            _uint(name, value, 64, positive=True)
    labels = ("country_prefix", "original_has_current", "original_current",
              "wrapper_source_patch", "wrapper_prefix", "definition_source_patch", "definition_prefix")
    if metrics:
        labels += ("presence_guard_count", "consume_current_metrics_inside_access")
    count = len(labels)
    args_at = native_call.BATCH_REQUEST.size + count * native_call.REQUEST.size
    storage_at = args_at + count * 32
    offsets = {"country": storage_at, "wrapper": storage_at + 0x18,
               "definition": storage_at + 0x30, "received": storage_at + 0x48}
    received_count = count + (2 if metrics else 0)
    size = offsets["received"] + received_count * 4
    sizes = {0: 0x18, 3: 8, 4: 0x18, 5: 8, 6: 0x18}
    if metrics:
        size = (size + 7) & ~7
        offsets.update(progress=size, cost=size + 8, guard=size + 16)
        guard_size = native_call.BATCH_REQUEST.size + native_call.REQUEST.size + 32
        offsets["metrics"] = offsets["guard"] + guard_size
        metrics_size = native_call.BATCH_REQUEST.size + 4 * native_call.REQUEST.size + 4 * 32
        size = offsets["metrics"] + metrics_size
        sizes[7] = 1

    def prepare(address):
        def at(name): return address + offsets[name]
        def arg(index): return address + args_at + index * 32
        def rax(index): return address + native_call.BATCH_REQUEST.size + index * native_call.REQUEST.size + 32
        def read(index, source, target, length):
            return runtime_read, (source, target, length, at("received") + index * 4)
        calls = [
            read(0, country_wrapper, at("country"), 0x18),
            (present_getter, (country_wrapper, 0, 0, 0)),
            (current_getter, (country_wrapper, 0, 0, 0)),
            read(3, rax(2), arg(4), 8),
            read(4, 0, at("wrapper"), 0x18),
            read(5, at("wrapper") + 0x10, arg(6), 8),
            read(6, 0, at("definition"), 0x18),
        ]
        if metrics:
            # Native Bool is AL. Copy ONE byte into a zero-high-byte uint64
            # count, not the undefined upper bits of the getter's RAX.
            calls.extend((read(7, rax(1), at("guard") + 8, 1),
                          (batch_handler, (0, at("guard"), 0, 0))))
        raw = bytearray(native_call.encode_batch(calls, request_address=address))
        raw.extend(bytes(size - len(raw)))
        if metrics:
            metrics_args_at = native_call.BATCH_REQUEST.size + 4 * native_call.REQUEST.size
            def metric_arg(index): return at("metrics") + metrics_args_at + index * 32
            consumers = (
                read(count, rax(2), metric_arg(2), 8),
                read(count + 1, rax(2), metric_arg(3), 8),
                (progress_getter, (0, at("progress"), country_wrapper, 0)),
                (cost_getter, (0, at("cost"), country_wrapper, 0)),
            )
            consumed = native_call.encode_batch(consumers, request_address=at("metrics"))
            guard = native_call.encode_batch(((batch_handler, (0, at("metrics"), 0, 0)),),
                                             request_address=at("guard"))
            raw[offsets["guard"]:offsets["guard"] + len(guard)] = guard
            raw[offsets["metrics"]:offsets["metrics"] + len(consumed)] = consumed
        return bytes(raw)

    def decode(payload):
        batch = native_call.NativeCallBatchResult.from_bytes(payload)
        if len(payload) != size or len(batch.calls) != count:
            raise ValueError("incomplete current-Technology batch allocation")
        received = struct.unpack_from("<" + str(received_count) + "I", payload, offsets["received"])
        country_id = struct.unpack_from("<I", payload, offsets["country"] + 0x10)[0]
        wrapper_vtable = struct.unpack_from("<Q", payload, offsets["wrapper"])[0]
        definition = struct.unpack_from("<Q", payload, offsets["wrapper"] + 0x10)[0]
        definition_vtable = struct.unpack_from("<Q", payload, offsets["definition"])[0]
        index = struct.unpack_from("<i", payload, offsets["definition"] + 0x10)[0]
        settled = batch.error == 0 and batch.completed == count
        def ok(index):
            call = batch.calls[index]
            return call.error == 0 and call.rax & 0xffffffff == 0 and received[index] == sizes[index]
        copied = settled and all(ok(i) for i in sizes)
        original_ok = all(batch.calls[i].error == 0 for i in (1, 2))
        present = bool(batch.calls[1].rax & 0xff)
        wrapper_type = wrapper_vtable == image_base + 0x44E2D38
        normal = definition_vtable == image_base + NORMAL_DEFINITION_VTABLE_RVA
        null_definition = definition_vtable == image_base + NULL_DEFINITION_VTABLE_RVA
        known = bool(copied and original_ok and wrapper_type and definition != 0)
        current = known and present and normal and index >= 0
        none = known and not present and null_definition and index == -1
        metrics_known = False
        metric_receipts = []
        if metrics and settled:
            guard = native_call.NativeCallBatchResult.from_bytes(payload[offsets["guard"]:offsets["metrics"]])
            if current and guard.error == 0 and guard.count == guard.completed == 1:
                numbers = native_call.NativeCallBatchResult.from_bytes(payload[offsets["metrics"]:])
                address = batch.requests_address - native_call.BATCH_REQUEST.size
                reads_ok = (numbers.count == numbers.completed == 4 and numbers.error == 0
                    and all(call.error == 0 for call in numbers.calls)
                    and all(numbers.calls[i].rax & 0xffffffff == 0 and received[count + i] == 8 for i in (0, 1)))
                metrics_known = bool(reads_ok
                    and numbers.calls[2].rax == address + offsets["progress"]
                    and numbers.calls[3].rax == address + offsets["cost"])
                metric_receipts = [{"adapter_error": call.error, "rax": call.rax} for call in numbers.calls]
            elif none and (guard.error or guard.count or guard.completed):
                # The zero-count branch must genuinely skip every consumer.
                none, known = False, False
        return {"status": ("current_definition_pod" if current else "no_current_research" if none
                           else "unresolved_current_technology"),
                "current_known": bool(current or none),
                "has_current_research": present if current or none else None,
                "country_id": country_id if copied else None,
                "definition_index": index if current else None,
                "definition_pointer_observation": definition if current or none else None,
                "key_known": False, "key": None, "prefix_copied": bool(copied),
                "normal_definition_type": normal, "null_definition_type": null_definition,
                "progress_known": metrics_known,
                "progress_bits": struct.unpack_from("<Q", payload, offsets["progress"])[0] if metrics_known else None,
                "cost_bits": struct.unpack_from("<Q", payload, offsets["cost"])[0] if metrics_known else None,
                "metrics_receipts": metric_receipts,
                "batch_error": batch.error, "completed": batch.completed,
                "receipts": [{"label": label, "adapter_error": call.error,
                    "read_error": (call.rax & 0xffffffff) if i in sizes else None,
                    "received": received[i] if i in sizes else None, "requested": sizes.get(i)}
                    for i, (label, call) in enumerate(zip(labels, batch.calls))],
                "boundary": "same-Access current receiver consumed; copied definition/numeric PODs only"}
    decode.layout = {"offsets": dict(offsets), "call_count": count, "args_at": args_at,
                     "size": size, "labels": labels, "metrics_enabled": metrics}
    return prepare(0), prepare, decode

async def _once(record, wait, label, submit, *, submit_query=None):
    """Retain the exact submission entry before receipt, including unknown/no-query."""
    row = {"label": label, "query": None, "result": None}
    record["operations"].append(row)
    record["pending"] = row
    try:
        query = await submit_query(submit) if submit_query is not None else submit()
    except BaseException as error:
        row["error"] = error
        row["query"] = getattr(error, "query", None)
        record["status"] = "unknown"
        raise  # Keep entry even without error.query. Keep every dependent owner.
    row["query"] = query
    try:
        result = await wait(label, query)
    except BaseException as error:
        row["error"] = error
        record["status"] = "unknown"
        raise
    row["result"] = result
    record["pending"] = None
    return result


async def read_current(session, process, currentintent):
    """One read-only observation, exact-query retention, normal owned-Any cleanup.

    Root's waiter must settle the exact query, not dispatch another one on timeout.
    Unknown submission/wait retains that query AND the Country owner in record.
    An explicit caller can inspect them. This function never reconnects or retries.
    """
    intent = currentintent
    record = intent.setdefault("record", {})
    if record:
        raise RuntimeError("retain the original current-Technology observation; do not reuse its record")
    record.update(status="preparing", operations=[], pending=None, country_owner=None,
                  release_attempted=False, result=None)
    world = _uint("world", intent["world"], 64, positive=True)
    session.namespace.setdefault("current_technology_observations", []).append(record)
    identity = intent.get("identity", session.namespace.get("identity"))
    if type(identity) is not dict:
        raise ValueError("fresh Root identity/current.json mapping is required")
    def number(value):
        return int(value, 0) if type(value) is str else value
    if identity.get("closed") or number(identity.get("pid")) != session.native.pid:
        raise ValueError("fresh current identity must belong to this existing live client")
    image = _uint("image base", number(identity["base"]), 64, positive=True)
    modules = {}
    for name in ("data", "access", "call", "carrier", "console", "objects"):
        entry = identity["modules"][name]
        base = entry["base"] if type(entry) is dict else entry
        modules[name] = _uint(name + " module", number(base), 64, positive=True)
    initial_status = V3Wire(session.native).world_status()
    tid = _uint("original Interface thread", number(initial_status["interface_thread_id"]), 32, positive=True)
    wait = intent["await_result"]
    if not callable(wait) or session.native.pid != process.pid:
        raise ValueError("one existing client/process and exact-query waiter required")
    def current_world():
        value = V3Wire(session.native).world_status()
        found = value.get("world")
        found = int(found, 0) if isinstance(found, str) else found
        thread = value.get("interface_thread_id")
        thread = int(thread, 0) if isinstance(thread, str) else thread
        if value.get("status") != "ok" or found != world or thread != tid or value.get("transition_pending"):
            raise RuntimeError("original world/Interface source changed; retain original observation")
        return value
    async def once(label, submit):
        return await _once(record, wait, label, submit, submit_query=intent.get("submit_query"))
    try:
        record["world_before"] = current_world()
        controlled = await once("current-technology.controlled-country", lambda: player_control.observe_player(
            session.native, adapter=modules["console"], process=process))
        record["controlled_country"] = controlled
        if (controlled.error or controlled.controlled_country_error or controlled.world != world
                or controlled.execution_thread_id != tid):
            raise RuntimeError("actual controlled Country source is unavailable")
        actual_actor = _uint("actual controlled Country ID", controlled.controlled_country_id, 32)
        actor = _uint("actor_id", intent.get("actor_id", actual_actor), 32)
        record["actor_id"] = actor
        # An explicit actor uses its own genuine full-ID Country.Self binding.
        # The controlled country remains an observation/default, not the target.
        # current_pod below checks the copied Country ID against this actor.
        country_type = None
        start = 0
        while country_type is None:
            page = await once("current-technology.country-type." + str(start), lambda: data.enumerate_types(
                session.native, start=start, capacity=64, adapter=modules["data"], process=process))
            if page.error:
                raise RuntimeError("fresh Data type enumeration failed")
            matches = [row for row in page.rows if row.name == "Country"]
            if len(matches) > 1:
                raise RuntimeError("native Country Data type name is not unique")
            if matches:
                country_type = matches[0].type_id
                if not matches[0].can_bind_object or country_type < 0:
                    raise RuntimeError("native Country Data type cannot bind its original wrapper")
                record["country_type"] = matches[0]
                break
            if page.next >= page.total:
                raise RuntimeError("native Country Data type was not found")
            if page.next <= start:
                raise RuntimeError("native Data type enumeration did not advance")
            start = page.next
            current_world()
        current_world()
        owner = await once("current-technology.country-owner", lambda: data.evaluate(session.native,
            "Country.Self", (data.ObjectBinding.country(country_type, actor),),
            output_bytes=8, adapter=modules["data"], process=process))
        record["country_owner"] = owner
        if (owner.error or owner.action != data.Action.EVALUATE
                or owner.execution_thread_id != tid or owner.bindings_count != 1
                or not owner.value_handle or owner.native_type_name != COUNTRY_SELF_TYPE
                or owner.kind != data.Kind.OBJECT or owner.payload_size != 8
                or owner.snapshot_bytes != 8 or len(owner.snapshot) != 8 or not owner.object
                or struct.unpack("<Q", owner.snapshot)[0] != owner.object):
            raise RuntimeError("original full-ID Country.Self owner shape is not proved")
        current_world()
        read = resolve_v3_export(process, modules["carrier"], "PdxRuntimeRead")
        handler = resolve_v3_export(process, modules["call"], "PdxNativeCallBatchInterface")
        metric_options = ({"batch_handler": handler, "progress_getter": image + CURRENT_PROGRESS_RVA,
                           "cost_getter": image + CURRENT_COST_RVA} if intent.get("with_progress") else {})
        initial, prepare, decode = compose_current(read, image + CURRENT_GETTER_RVA,
            image + CURRENT_PRESENT_RVA, owner.object, image, **metric_options)
        async def current_pod(label):
            current_world()
            receipt = await once(label, lambda: access.submit_callback(
                session.native, handler, initial, process=process, mode=1, flag=0, dispatch="interface",
                adapter=modules["access"], call_adapter=modules["call"], prepare_payload=prepare,
                decode_payload=decode, source="pdx_runtime.v3_game.research._current"))
            if (receipt.error or receipt.entered != 1 or receipt.left != 1
                    or receipt.execution_thread_id != tid or receipt.callbackfn != handler
                    or receipt.mode != 1 or receipt.flag != 0):
                raise RuntimeError("original callback/access receipt is incomplete")
            copied = receipt.callee_result
            if copied["country_id"] != actor or not copied["prefix_copied"]:
                raise RuntimeError("original Country/current POD source is incomplete")
            return receipt, copied
        first_receipt, first = await current_pod("current-technology.original-current-pod-before")
        record["current_before_receipt"] = first_receipt
        record["current_before"] = first
        if not first["current_known"]:
            raise RuntimeError("actual current definition/default POD was not established")
        if intent.get("with_progress") and first["has_current_research"] and not first.get("progress_known"):
            raise RuntimeError("original current progress/cost getters did not return complete same-Access PODs")
        copied = dict(first)
        if first["has_current_research"]:
            current_world()
            # ONLY the copied native ordinal is a later callback input. The
            # previous definition pointer is observation-only, never a receiver.
            text_capacity = 4096
            while True:
                definitions = await once("current-technology.definition-key." + str(text_capacity), lambda:
                    objects.read_technology_definition_indices(session.native, (first["definition_index"],),
                        text_capacity=text_capacity, adapter=modules["objects"], process=process))
                record["definitions_result"] = definitions
                if definitions.error != 234:
                    break
                options = definitions.definition_options
                if (definitions.world != world or definitions.next_index != 0 or definitions.records
                        or options is None or options.text_capacity != text_capacity
                        or options.text_bytes_required <= text_capacity
                        or options.required_buffer_size != options.text_offset + options.text_bytes_required):
                    raise RuntimeError("native whole-key required-capacity protocol did not advance")
                # This exact query is completed. Preserve its receipt/result in
                # operations, then grow ONLY this copied ordinal's readonly query.
                # Unknown delivery never reaches this branch or gets replayed.
                text_capacity = options.text_bytes_required
                current_world()
            if (definitions.error or definitions.world != world or definitions.truncated
                    or len(definitions.records) != 1):
                raise RuntimeError("one complete same-world Technology definition/key was not returned")
            definition = definitions.records[0]
            if (not isinstance(definition, objects.TechnologyDefinition) or definition.error or definition.key_error
                    or definition.requested_index != first["definition_index"]
                    or definition.resolved_index != first["definition_index"]
                    or definition.pointer != first["definition_pointer_observation"]
                    or definition.key_bytes_count != definition.key_size
                    or len(definition.key_bytes) != definition.key_size):
                raise RuntimeError("actual current POD has no complete typed definition-key join")
            second_receipt, second = await current_pod("current-technology.original-current-pod-after")
            record["current_after_receipt"] = second_receipt
            record["current_after"] = second
            stable = (second["current_known"] and second["has_current_research"] is True
                and second["definition_index"] == first["definition_index"]
                and second["definition_pointer_observation"] == first["definition_pointer_observation"])
            if stable:
                copied.update(status="current_technology", key_known=True, key=definition.key,
                              key_bytes=bytes(definition.key_bytes), stable_across_observations=True)
            else:
                copied.update(status="observation_changed", current_known=False,
                              has_current_research=None, definition_index=None,
                              definition_pointer_observation=None, key_known=False, key=None,
                              stable_across_observations=False)
        else:
            copied.update(stable_across_observations=False)  # One truthful original default observation.
        record["world_after"] = current_world()
        record["result"] = {**copied, "actor_id": actor, "world": world,
                            "atomic_snapshot": False, "technology_completion_observed": False,
                            "boundary": "current selection/key and optional same-Access progress/cost PODs; no completion, unlock or save"}
        record["status"] = copied["status"]
        return record["result"]
    except BaseException as error:
        record["error"] = error
        if record["status"] != "unknown":
            record["status"] = "failed"
        raise
    finally:
        # Unknown callback delivery keeps every dependency. Do not release its
        # Country receiver, close process, replay, or free caller-owned buffers.
        owner = record.get("country_owner")
        if (record["pending"] is None and owner is not None and owner.value_handle
                and not record["release_attempted"]):
            record["release_attempted"] = True
            released = await once("current-technology.release-country-owner", lambda: data.release(
                session.native, owner.value_handle, adapter=modules["data"], process=process))
            record["release_result"] = released
            if released.error:
                record["status"] = "release_failed"
                raise RuntimeError("Country owner release failed; retain exact release query")


if __name__ == "__main__":
    print(__doc__)
