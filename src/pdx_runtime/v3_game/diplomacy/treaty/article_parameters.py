"""Private original Article parameter reader. No connection/native work on import.

C598E0 fresh fullRef -> observed Article.params24 view -> D54E20/D53110.
The same-callback POD view is never an owner, fake Any or cross-callback pointer.
Only original parameter wire/text bytes and copied scalar evidence are returned.
"""
from __future__ import annotations
from pdx_runtime.v3_wire import resolve_v3_export
import struct
from .... import native_call, v3_access, v3_commands as commands
from . import proposal as primitive
READS = []
INVALID = 0xFFFFFFFF


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low},{high}]")
    return value


def _offsets(wire_capacity, text_capacity):
    result = {"id": 0, "scope": 8, "prefix": 32, "first": 104,
        "file": 144, "writer": 240, "metadata": 264, "received": 344,
        "zero": 512, "wire": 1024}
    result["text"] = (1024 + wire_capacity + 7) & ~7
    result["size"] = result["text"] + text_capacity
    return result


def compose(*, image_base, runtime_read, memcpy, scope_valid, storage,
            wire_capacity, text_capacity):
    """Fixed existing32/56B ABI sequence; no inferred callback dependency DSL.

    C598E0 returns an original Article or its fully constructed native fallback.
    memcpy48 relies on this original readable-object ABI under the live native
    Access/gate/no-reentry scope, NOT on AV recovery or a posthoc SafeRPM check.
    The native serializer only reads this exact original constArray24 POD view.
    The fallible first-variant SafeRPM probe feeds copied diagnostics only; it
    is never passed to D54E20 or another original native object consumer.
    """
    o = _offsets(wire_capacity, text_capacity)
    def at(name, delta=0):return storage + o[name] + delta
    labels = ("scope62", "resolve_article", "patch_article_source", "copy_original_prefix",
        "patch_variant_probe_source", "probe_first_variant", "construct_file", "set_file_write",
        "original_parameter_serializer", "copy_actual_ref", "copy_parent_and_local_parties",
        "copy_param_bounds", "copy_first_tag", "copy_first_string_bounds", "copy_file_status",
        "destroy_writer", "destroy_file", "scrub_prefix", "scrub_first_variant",
        "scrub_article_source", "scrub_variant_source", "scrub_resolver_rax", "scrub_file", "scrub_writer")
    count = len(labels)
    def prepare(address):
        records = [address + 32 + i * 56 for i in range(count)]
        argstart = 32 + count * 56
        def arg(index, position=0):return address + argstart + index * 32 + position * 8
        calls = ((scope_valid, (at("scope"), at("scope", 16), 0, 0)),
            (image_base + 0xC598E0, (at("id"), 0, 0, 0)),
            (memcpy, (arg(3, 1), records[1] + 32, 8, 0)),
            (memcpy, (at("prefix"), 0, 0x48, 0)),
            (runtime_read, (at("prefix", 0x30), arg(5), 8, at("received", 4 * 4))),
            (runtime_read, (0, at("first"), 40, at("received", 5 * 4))),
            (image_base + 0x3AFF620, (at("file"), at("wire"), wire_capacity, 0)),
            (image_base + 0x3AFF800, (at("file"), 0, 1, 0)),
            (image_base + 0xD54E20, (at("writer"), 0, at("prefix", 0x30), 0)),
            (memcpy, (at("metadata"), at("prefix", 8), 4, 0)),
            (memcpy, (at("metadata", 4), at("prefix", 0x20), 12, 0)),
            (memcpy, (at("metadata", 16), at("prefix", 0x38), 8, 0)),
            (memcpy, (at("metadata", 24), at("first", 0x20), 1, 0)),
            (memcpy, (at("metadata", 32), at("first", 0x10), 16, 0)),
            (memcpy, (at("metadata", 48), at("file", 0x48), 16, 0)),
            (image_base + 0x3B54500, (at("writer"), 0, 0, 0)),
            (image_base + 0x3AFF770, (at("file"), 0, 0, 0)),
            (memcpy, (at("prefix"), at("zero"), 0x48, 0)),
            (memcpy, (at("first"), at("zero"), 40, 0)),
            (memcpy, (arg(3, 1), at("zero"), 8, 0)),
            (memcpy, (arg(5), at("zero"), 8, 0)),
            (memcpy, (records[1] + 32, at("zero"), 8, 0)),
            (memcpy, (at("file"), at("zero"), 96, 0)),
            (memcpy, (at("writer"), at("zero"), 24, 0)))
        return native_call.encode_batch(calls, request_address=address)
    return prepare(0), prepare, labels, o


def decode_parameters(wire, native_count):
    """Original token/marker codec; the returned tuple retains native order."""
    if native_count == 0:
        if wire:
            raise ValueError("native empty parameter array emitted unexpected bytes")
        return ()
    tree = commands.decode_command(wire)
    if tree.key != commands.Token(0x81B) or len(tree.value) != native_count:
        raise ValueError("original parameter vector count/token differs from native serialization")
    for parameter in tree.value:
        if not isinstance(parameter, tuple) or len(parameter) > 1 or any(
                not isinstance(item, commands.Assignment) for item in parameter):
            raise ValueError("original parameter is not a canonical native variant block")
    return tree.value


async def _execute(state, label, initial, prepare, count):
    process = state["process"]
    primitive._world(state, label + "_before")
    callback = resolve_v3_export(process, state["call_adapter"], "PdxNativeCallBatchInterface")
    result = await primitive.accept._operation(state, label, lambda:
        v3_access.submit_callback(state["client"], callback, initial, mode=1, flag=0,
            dispatch="interface", adapter=state["access_adapter"],
            call_adapter=state["call_adapter"], process=process,
            prepare_payload=prepare, decode_payload=native_call.NativeCallBatchResult.from_bytes))
    batch = result.callee_result
    if (result.error or not result.entered or not result.left or batch is None or batch.error
            or batch.completed != count or len(batch.calls) != count or any(call.error for call in batch.calls)):
        state["cleanup_uncertain"] = True
        raise RuntimeError("unresolved native read prefix; retain exact query and caller storage, never replay")
    primitive._world(state, label + "_after")
    return batch


async def _copy_heap_text_fresh(state, memory, o, required):
    """Rare binary16-bit string-length case: fresh same-callback raw native bytes.

    Uses safe reads only after its resolver/prefix copy. A changed first variant,
    count or length produces complete=False; no native consumer sees a partial
    SafeRPM output. This read has its own later observation time, not an atomic
    promise that all earlier generic parameter values remained unchanged.
    """
    image = state["image_base"]; process = state["process"]
    memcpy = process.resolve_export("ntdll.dll", "memcpy")
    rawread = resolve_v3_export(process, state["runtime_adapter"], "PdxRuntimeRead")
    rpm = process.resolve_export("kernel32.dll", "ReadProcessMemory")
    def at(name, delta=0):return memory + o[name] + delta
    # A separately listed17-call sequence avoids any cross-callback pointer.
    count = 18
    def prepare18(address):
        calls = ((image + 0xC598E0, (at("id"), 0, 0, 0)),
            (memcpy, (0, address + 64, 8, 0)), (memcpy, (at("prefix"), 0, 0x48, 0)),
            (rawread, (at("prefix", 0x30), 0, 8, at("received", 12))),
            (rawread, (0, at("first"), 40, at("received", 16))),
            (rawread, (at("first"), 0, 8, at("received", 20))),
            (rpm, (0xFFFFFFFFFFFFFFFF, 0, at("text"), required, at("metadata", 64))),
            (process.resolve_export("kernel32.dll", "GetLastError"), (0, 0, 0, 0)),
            (memcpy, (at("metadata"), at("prefix", 8), 4, 0)),
            (memcpy, (at("metadata", 16), at("prefix", 0x38), 8, 0)),
            (memcpy, (at("metadata", 24), at("first", 0x20), 1, 0)),
            (memcpy, (at("metadata", 32), at("first", 0x10), 16, 0)),
            (memcpy, (at("prefix"), at("zero"), 0x48, 0)),
            (memcpy, (at("first"), at("zero"), 40, 0)),
            (memcpy, (0, at("zero"), 8, 0)), (memcpy, (0, at("zero"), 8, 0)),
            (memcpy, (address + 64, at("zero"), 8, 0)), (memcpy, (0, at("zero"), 8, 0)))
        encoded = bytearray(native_call.encode_batch(calls, request_address=address))
        args = [struct.unpack_from("<Q", encoded, 32 + i * 56 + 24)[0] for i in range(count)]
        def setarg(i, pos, val):struct.pack_into("<Q", encoded, args[i] - address + pos * 8, val)
        setarg(1, 0, args[2] + 8); setarg(3, 1, args[4]); setarg(5, 1, args[6] + 8)
        setarg(14, 0, args[2] + 8); setarg(15, 0, args[4]); setarg(17, 0, args[6] + 8)
        return bytes(encoded)
    process.write(at("metadata"), bytes(80)); process.write(at("received"), bytes(96))
    batch = await _execute(state, "fresh_exact_native_heap_text", prepare18(0), prepare18, count)
    raw = process.read(memory, o["size"])
    md = o["metadata"]
    actual = struct.unpack_from("<I", raw, md)[0]
    capacity, n = struct.unpack_from("<ii", raw, md + 16)
    length, cap = struct.unpack_from("<2Q", raw, md + 32)
    copied = struct.unpack_from("<Q", raw, md + 64)[0]
    received = struct.unpack_from("<24I", raw, o["received"])
    probes = all(batch.calls[i].rax & INVALID == 0 and received[i] == size for i, size in ((3,8),(4,40),(5,8)))
    scrub = not any(raw[o["prefix"]:o["prefix"]+72]) and not any(raw[o["first"]:o["first"]+40]) and batch.calls[0].rax == 0
    complete = bool(actual == state["article_full_id"] and 0 <= n <= capacity and n == 1
        and raw[md+24] == 9 and cap > 15 and length == required <= cap
        and probes and batch.calls[6].rax == 1 and copied == required and scrub)
    receipt = {"requested": required, "received": copied, "read_succeeded": batch.calls[6].rax == 1,
        "read_error": 0 if batch.calls[6].rax == 1 else batch.calls[7].rax & INVALID,
        "actual_article_full_id": actual, "current_native_parameter_count": n,
        "current_native_tag": raw[md+24], "current_native_string_bytes": length,
        "borrowed_storage_scrubbed": scrub, "observation": "subsequent fresh callback",
        "probe_reads": [{"index": i, "error": batch.calls[i].rax & INVALID,
            "received": received[i], "requested": size} for i,size in ((3,8),(4,40),(5,8))]}
    return (raw[o["text"]:o["text"]+required] if complete else None), complete, receipt


async def read_article_parameters(client, process, *, article_full_id, image_base,
        world=None, wire_capacity=65536, text_capacity=131072,
        data_adapter="pdx_v3_data.dll", access_adapter="pdx_v3_access.dll",
        call_adapter="pdx_native_call.dll", script_adapter="pdx_v3_script.dll",
        runtime_adapter="pdx_runtime_bridge.dll"):
    """Read original typed parameters/full text from this fresh real Article.

    No submitted-text input, display-name equality, mutation or GUI owner lease.
    Native file overflow and wire limitations remain explicit readonly sizing
    receipts. No automatic sizing retry. A second fresh raw-text read is used
    only when the native binary serializer's16-bit string limit cannot represent
    this actual sole tag9 CString; that text read has its own observation time.
    """
    if any(previous["client"] is client and (previous["pending"] is not None
            or previous["cleanup_uncertain"]) for previous in READS):
        raise RuntimeError("this client has an unresolved original terms read; preserve it, do not reconstruct/replay")
    _integer(article_full_id, "article_full_id", 0, INVALID - 1)
    _integer(image_base, "image_base", 1, (1 << 64) - 1)
    _integer(wire_capacity, "wire_capacity", 1, 0x7FFFFFFF)
    _integer(text_capacity, "text_capacity", 1, 0x7FFFFFFF)
    if getattr(client, "pid", None) != getattr(process, "pid", None):
        raise ValueError("client/process identity mismatch")
    state = primitive.accept._state(client, process, article_full_id, image_base,
        {"data": data_adapter, "access": access_adapter, "call": call_adapter,
         "script": script_adapter, "commands": None})
    state.update(status="article_terms_read_started", article_full_id=article_full_id,
        result=None, native_temporaries_closed=False, runtime_adapter=runtime_adapter)
    READS.append(state)
    try:
        state["world"] = primitive._world(state, "article_terms_initial_world")
        if world is not None and world != state["world"]:
            raise RuntimeError("original source world changed before this fresh read")
        o = _offsets(wire_capacity, text_capacity)
        memory = primitive._alloc(state, o["size"]); state["storage"] = memory
        process.write(memory + o["id"], struct.pack("<I", article_full_id))
        process.write(memory + o["scope"], struct.pack("<H3HQI", 0x62, 0, 0, 0, article_full_id, INVALID))
        # Canonical v3_commands.cpp concrete Writer24 construction. It owns no
        # file/payload and is closed by the original3B54500 on the same stack.
        process.write(memory + o["writer"], struct.pack("<QQi4B", image_base + 0x48504F0,
            memory + o["file"], 0, 1, 0, 1, 0))
        initial, prepare, labels, o = compose(image_base=image_base,
            runtime_read=resolve_v3_export(process, runtime_adapter, "PdxRuntimeRead"),
            memcpy=process.resolve_export("ntdll.dll", "memcpy"),
            scope_valid=resolve_v3_export(process, script_adapter, "PdxV3ScriptScopeValid"),
            storage=memory, wire_capacity=wire_capacity, text_capacity=text_capacity)
        batch = await _execute(state, "original_article_parameter_serializer", initial, prepare, len(labels))
        state["native_temporaries_closed"] = True
        raw = process.read(memory, o["size"]); md = o["metadata"]
        actual, treaty_ref, local_source, local_target, capacity, count = struct.unpack_from("<4Iii", raw, md)
        tag = raw[md + 24]
        string_bytes, string_capacity = struct.unpack_from("<2Q", raw, md + 32)
        file_length, position, file_capacity, flags = struct.unpack_from("<4I", raw, md + 48)
        scope = struct.unpack_from("<I", raw, o["scope"] + 16)[0]
        received = struct.unpack_from("<24I", raw, o["received"])
        if (batch.calls[0].rax & INVALID or scope != 1 or actual != article_full_id
                or not 0 <= count <= capacity <= 0x7FFFFFFF):
            raise RuntimeError("original Article fullRef/scope/fallback/parameter identity is invalid")
        if batch.calls[7].rax & 0xFF != 1:
            raise RuntimeError("original File SetMode did not report native writable mode")
        if (any(raw[o["prefix"]:o["prefix"]+72]) or any(raw[o["first"]:o["first"]+40])
                or any(raw[o["file"]:o["file"]+96]) or any(raw[o["writer"]:o["writer"]+24])
                or batch.calls[1].rax):
            raise RuntimeError("borrowed/native temporary storage was not scrubbed")
        overflow = bool(flags & 0xFF)
        wire_complete = bool(not overflow and 0 <= position == file_length <= file_capacity == wire_capacity)
        wire = raw[o["wire"]:o["wire"]+position] if wire_complete else b""
        parameters = None; wire_error = None
        if wire_complete:
            try:parameters = decode_parameters(wire, count)
            except (ValueError, TypeError, UnicodeError) as error:
                wire_error = str(error); wire_complete = False
        first_ok = all(batch.calls[i].rax & INVALID == 0 and received[i] == size for i,size in ((4,8),(5,40)))
        single_string = bool(count == 1 and first_ok and tag == 9 and string_bytes <= string_capacity
            and (string_capacity > 15 or string_bytes <= 15))
        serialized_single_string = bool(parameters is not None and count == 1
            and len(parameters[0]) == 1 and parameters[0][0].key == commands.Token(0x8F)
            and isinstance(parameters[0][0].value, str))
        has_string_variant = single_string or serialized_single_string
        raw_text = None; text_complete = False; text_source = None; raw_text_receipt = None
        if single_string and parameters is not None:
            field = parameters[0]
            if len(field) == 1 and field[0].key == commands.Token(0x8F) and isinstance(field[0].value, str):
                candidate = field[0].value.encode("utf-8")
                if len(candidate) == string_bytes:
                    raw_text = candidate; text_complete = True
                    text_source = "original D53110 tag9->0x8F binary CString, native byte-length equality checked"
        if single_string and not text_complete and string_capacity > 15 and string_bytes <= text_capacity:
            raw_text, text_complete, raw_text_receipt = await _copy_heap_text_fresh(state, memory, o, string_bytes)
            if text_complete:text_source = "subsequent fresh same-callback original tag9 CString raw bytes"
        text = raw_text.decode("utf-8", "strict") if text_complete else None
        native_serialized_parameters = parameters
        wire_value_complete = bool(wire_complete)
        if single_string:
            wire_value_complete = bool(parameters is not None and len(parameters[0]) == 1
                and parameters[0][0].key == commands.Token(0x8F)
                and isinstance(parameters[0][0].value, str)
                and len(parameters[0][0].value.encode("utf-8")) == string_bytes)
            if text_complete:
                # Use the actual later native CString bytes when its original
                # binary16-bit representation cannot carry the whole value.
                # This is copied native input, never a caller/display replacement.
                parameters = ((commands.Assignment(commands.Token(0x8F), text),),)
        values_complete = bool(parameters is not None and (not has_string_variant or text_complete))
        result = {"article_full_id": actual, "treaty_full_id": treaty_ref,
            "local_source_full_country_id": local_source, "local_target_full_country_id": local_target,
            "native_parameter_count": count, "parameters": parameters, "parameter_wire": wire,
            "wire_complete": wire_complete, "wire_value_complete": wire_value_complete,
            "native_serialized_parameters": native_serialized_parameters,
            "parameter_values_complete": values_complete,
            "wire_overflow": overflow, "wire_error": wire_error,
            "wire_capacity": wire_capacity, "native_wire_bytes_written": position,
            "single_native_string": bool(has_string_variant), "first_parameter_probe_complete": first_ok,
            "required_text_bytes": string_bytes if single_string else None,
            "text_complete": text_complete, "text": text, "text_bytes": raw_text, "text_source": text_source,
            "raw_text_receipt": raw_text_receipt,
            "first_parameter_probe_reads": [{"index": i, "error": batch.calls[i].rax & INVALID,
                "received": received[i], "requested": size} for i,size in ((4,8),(5,40))],
            "complete": values_complete,
            "borrowed_storage_scrubbed": True, "display_text_used": False,
            "caller_submitted_text_used": False, "world": state["world"]}
        state["result"] = result
        state["status"] = "original_article_parameters_read" if result["complete"] else "readonly_capacity_or_wire_incomplete"
        primitive._free(state, memory)
        return state
    except BaseException as error:
        state["failure"] = {"type": type(error).__name__, "message": str(error)[:500]}
        state["status"] = "article_terms_read_failed_retained"
        error.query_state = state
        raise


async def cleanup_completed(state):
    """Unknown callback/native prefix retains all original caller storage."""
    if state["pending"] is not None or state["cleanup_uncertain"] or not state["native_temporaries_closed"]:
        return False
    primitive._free(state, *tuple(state["owned_buffers"]))
    return True
