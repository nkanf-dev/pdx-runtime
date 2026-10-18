"""Private fixed SafeRPM POD Treaty-ref discovery. No PID/native call on import.

Reads the real raw Country+3B0 uint32 fullRef array, not a retained GUI header.
All borrowed resolver RAX/header pointers are consumed and scrubbed within one
native Access callback. The raw source contains ALL treaties; fresh CTreaty+2C
filtering below identifies drafts. No new Runtime API/DSL/provider is added.
"""
from __future__ import annotations
from pdx_runtime.v3_wire import resolve_v3_export
import struct
from .... import native_call, v3_access


def compose(runtime_read, country_resolver, country_full_id, slots):
    """Return initial/prepare/decode for the existing native32/56B fixed batch.

    slots is caller-owned read capacity, never a business/native count. SafeRPM
    can report failure for the fixed read even when fewer active refs exist.
    Such a receipt is incomplete, NOT empty; a fresh smaller/larger read may be
    selected from the copied native count. No guessed pointer is dereferenced.
    """
    if type(country_full_id) is not int or not 0 <= country_full_id < 0xFFFFFFFF:
        raise ValueError("a real complete Country fullID is required")
    if type(slots) is not int or not 0 < slots <= 1024:
        raise ValueError("slots must fit one owned4B-ref SafeRPM read (1..1024)")
    if not runtime_read or not country_resolver:
        raise ValueError("explicit original current process exports/leaf addresses required")
    labels = ("resolve_country", "country_pointer_patch", "country_prefix",
        "refs_pointer_patch", "refs_u32_copy", "country_full_id_copy", "array_metadata_copy",
        "scrub_country_prefix", "scrub_country_source_argument", "scrub_refs_source_argument",
        "scrub_resolver_rax")
    count = len(labels)
    argument_start = 32 + count * 56
    end = argument_start + count * 32
    offsets = {}
    def reserve(name, size):
        nonlocal end
        end = (end + 7) & ~7; offsets[name] = end; end += size
    reserve("country_input", 8)
    reserve("prefix", 0x3C8)
    reserve("ids", slots * 4)
    reserve("identity", 8)
    reserve("metadata", 8)
    reserve("received", count * 4)
    reserve("zero", 0x3C8)
    total = end
    requested = [0, 8, 0x3C8, 8, slots * 4, 4, 8, 0x3C8, 8, 8, 8]
    def prepare(address):
        def at(name, extra=0):return address + offsets[name] + extra
        def arg(index):return address + argument_start + index * 32
        def read(index, source, dest, size):
            return (runtime_read, (source, dest, size, at("received", index * 4)))
        calls = ((country_resolver, (at("country_input"), 0, 0, 0)),
            read(1, address + 32 + 32, arg(2), 8),
            read(2, 0, at("prefix"), 0x3C8),
            read(3, at("prefix", 0x3B0), arg(4), 8),
            read(4, 0, at("ids"), slots * 4),
            read(5, at("prefix", 0x18), at("identity"), 4),
            read(6, at("prefix", 0x3B8), at("metadata"), 8),
            read(7, at("zero"), at("prefix"), 0x3C8),
            read(8, at("zero"), arg(2), 8),
            read(9, at("zero"), arg(4), 8),
            read(10, at("zero"), address + 32 + 32, 8))
        data = bytearray(native_call.encode_batch(calls, request_address=address))
        data.extend(bytes(total - len(data)))
        struct.pack_into("<I", data, offsets["country_input"], country_full_id)
        return bytes(data)
    def decode(payload):
        batch = native_call.NativeCallBatchResult.from_bytes(payload)
        received = struct.unpack_from("<11I", payload, offsets["received"])
        actual_id = struct.unpack_from("<I", payload, offsets["identity"])[0]
        capacity, native_count = struct.unpack_from("<ii", payload, offsets["metadata"])
        receipts = [{"label": labels[i], "adapter_error": call.error,
            "read_error": None if i == 0 else call.rax & 0xFFFFFFFF,
            "received": received[i], "requested": requested[i]} for i, call in enumerate(batch.calls)]
        settled = batch.error == 0 and batch.completed == count and len(batch.calls) == count
        def ok(index):
            if index >= len(receipts):return False
            item = receipts[index]
            return item["adapter_error"] == 0 and (index == 0 or (item["read_error"] == 0
                and item["received"] == item["requested"]))
        header_ok = bool(settled and all(ok(i) for i in (0, 1, 2, 3, 5, 6))
            and actual_id == country_full_id and 0 <= native_count <= capacity <= 0x7FFFFFFF)
        fits = header_ok and native_count <= slots
        scrub_ranges = ((offsets["prefix"], 0x3C8), (argument_start + 2 * 32, 8),
                        (argument_start + 4 * 32, 8), (32 + 32, 8))
        scrubbed = bool(settled and all(ok(i) for i in (7, 8, 9, 10))
            and all(not any(payload[start:start+size]) for start, size in scrub_ranges))
        complete = bool(fits and scrubbed and (native_count == 0 or ok(4)))
        ids = list(struct.unpack_from(f"<{native_count}I", payload, offsets["ids"])) if complete else []
        if 0xFFFFFFFF in ids:
            complete = False; ids = []
        return {"country_full_id": country_full_id, "observed_country_full_id": actual_id,
            "native_count": native_count, "native_capacity": capacity, "owned_slots": slots,
            "header_copied": header_ok, "capacity_sufficient": bool(fits),
            "complete_refs_copied": complete, "full_refs": ids,
            "borrowed_storage_scrubbed": scrubbed, "receipts": receipts,
            "batch_error": batch.error, "completed": batch.completed,
            "source": "raw Country+3B0 array<uint32 complete Treaty refs>; NOT drafts-only"}
    decode.layout = {"offsets": offsets, "argument_start": argument_start,
                     "call_count": count, "size": total, "labels": labels}
    return prepare(0), prepare, decode


async def read_country_refs(treaty, state, *, country_full_id, slots=64,
                            runtime_adapter="pdx_runtime_bridge.dll"):
    """One fresh readonly callback; returned fields are copied POD only.

    treaty is the imported .proposal module. state retains the
    exact shared process/client/world and unknown query ledger. No source Any,
    GUI list, previously borrowed header or prior resolver pointer is reused.
    """
    if state["pending"] is not None:
        raise RuntimeError("an earlier original callback is unresolved; do not stack another request")
    treaty._world(state, "draft_refs_before")
    process = state["process"]
    runtime_read = resolve_v3_export(process, runtime_adapter, "PdxRuntimeRead")
    initial, prepare, decode = compose(runtime_read, state["image_base"] + 0x7BB480,
        country_full_id, slots)
    callback = resolve_v3_export(process, state["call_adapter"], "PdxNativeCallBatchInterface")
    result = await treaty.accept._operation(state, "fresh_country_treaty_refs", lambda:
        v3_access.submit_callback(state["client"], callback, initial, mode=1, flag=0,
            dispatch="interface", adapter=state["access_adapter"], process=process,
            call_adapter=state["call_adapter"], prepare_payload=prepare, decode_payload=decode))
    treaty._world(state, "draft_refs_after")
    if result.error or not result.entered or not result.left:
        raise RuntimeError("original readonly native Access callback failed; preserve the receipt")
    return result.callee_result


async def discover_drafts(treaty, state, *, country_full_id, slots=64,
                          runtime_adapter="pdx_runtime_bridge.dll"):
    """Return current exact refs plus copied same-party native draft state.

    Every copied source ref is subsequently re-resolved using the frozen
    proposal helper's same-callback C5A180 snapshot. This is NOT an atomic current draft
    set: refs belong to the first callback's source snapshot; each row's draft
    byte/parties belong to that row's later callback. Additions after the source
    snapshot need not be detected. complete means full source copy and successful
    row checks, not collection stability or atomic absence. Compare the original
    POD pre/post source.full_refs sets, then explicitly select the intended new
    ref with its independently observed matching native parties/draft state.
    Raw submitted free_text stays the original command serialized evidence;
    display names/descriptions are not relabeled as exact original text.
    """
    source = await read_country_refs(treaty, state, country_full_id=country_full_id,
        slots=slots, runtime_adapter=runtime_adapter)
    result = {"source": source, "drafts": [], "all_treaties": [],
        "complete": False, "world": state["world"], "text_equivalence_claimed": False}
    if not source["complete_refs_copied"]:
        return result
    for index, full_ref in enumerate(source["full_refs"]):
        row = await treaty._proposal_snapshot(state, full_ref, "discover_treaty_" + str(index))
        row["queried_country_is_party"] = country_full_id in row["party_full_refs"]
        if not row["queried_country_is_party"]:
            return result
        result["all_treaties"].append(row)
        if row["is_draft"]:
            result["drafts"].append(row)
    result["complete"] = True
    result["full_draft_refs"] = [row["full_ref"] for row in result["drafts"]]
    return result
