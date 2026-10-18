"""Ordinary-Python free_text Treaty proposal and response composition.

No connection, PID lookup, launch, build, submit, or native operation on import.
Native leaf RVAs retain the actual 1.13.11 analysis provenance. Prepare never
submits. Reader status is copied before its destructor in one Access callback.
"""
from __future__ import annotations
from pdx_runtime.v3_wire import resolve_v3_export
import hashlib
import struct
from .... import v3_commands as c, v3_access, native_call, v3_data as d
from . import accept
query = accept.query
ATTEMPTS = []
TOKEN = 14273
INVALID = 0xFFFFFFFF
NO_DATE = 0x029C77F8


def _no_prior_unknown(client):
    for previous in ATTEMPTS:
        if previous["client"] is not client:
            continue
        owners = (previous.get("staging_owner"), previous.get("unique_owner"))
        unknown_owner = any(owner is not None and owner.attempt is not None
            and owner.attempt.outcome in ("pending", "unknown") for owner in owners)
        if (previous["pending"] is not None or previous["handoff_uncertain"]
                or previous["cleanup_uncertain"] or previous["unadopted_clone"] is not None or unknown_owner):
            raise RuntimeError("this original client has an unresolved prior native attempt; inspect it, never reconstruct/replay")


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def free_text_wire(*, actor_full_country_id, source_full_country_id,
                   target_full_country_id, text, binding_days):
    """One new, voluntary, directed free_text article; no amendment/enforcement.

    The native context and actor's native maintenance cost are generated after
    a checked Reader fills this owned staging command. Empty context/Int64(0)
    here are lifecycle-safe staging values, NEVER a prepared proposal or a
    claimed semantic default. Prepare replaces both via original producers.
    """
    for name, value in (("actor", actor_full_country_id), ("source", source_full_country_id),
                        ("target", target_full_country_id)):
        _integer(value, name, 0, INVALID - 1)
    if source_full_country_id == target_full_country_id or actor_full_country_id != target_full_country_id:
        raise ValueError("this representative directed path requires distinct parties and actor=target payer")
    _integer(binding_days, "binding_days", 0, 0x7FFFFFFF)
    if not isinstance(text, str) or not text or "\0" in text:
        raise ValueError("text must be an explicit nonempty NUL-free string")
    if len(text.encode("utf-8")) > 65535:
        raise ValueError("text exceeds the original binary string length")
    article = {0x2F11: "free_text", 0x60C7: c.UInt32(source_full_country_id),
               0x60C8: c.UInt32(target_full_country_id), 0x81B: ({0x8F: text},)}
    payload = {0x60C0: (article,), 0x60BA: c.UInt32(INVALID),
        0x32CF: c.UInt32(source_full_country_id), 0x32D0: c.UInt32(target_full_country_id),
        0x60CB: NO_DATE, 0x60CA: binding_days, 0x60BB: True, 0x62AB: False,
        0x60E2: c.UInt32(INVALID), 0x60E3: (), 0x60E4: (), 0x620E: c.Token(0x165)}
    return c.encode_command(TOKEN, {0x2840: c.UInt32(actor_full_country_id),
        0x41: {0x240: c.UInt32(actor_full_country_id), 0x37C4: payload,
               0x1B: {}, 0x27FE: c.Int64(0)}})


def context_seed(counter, salt):
    """Exact 221DBC1..221DC0A hash of copied native5148090/94 scalars.

    This is only a reproducible native name-selection seed. It grants no
    eligibility and writes neither the native RNG counter nor a game flag.
    """
    _integer(counter, "counter", 0, INVALID); _integer(salt, "salt", 0, INVALID)
    value = (salt - counter * 0x4AD685B3) & INVALID
    value = ((value ^ (value >> 8)) + 0x68E31DA4) & INVALID
    value ^= (value << 8) & INVALID
    value = (value * 0x1B56C4E9) & INVALID
    value ^= value >> 8
    value = (value * 0x92D68CA2) & INVALID
    return value ^ (value >> 8)


def _alloc(state, size, initial=None):
    address = state["process"].allocate(size)
    state["owned_buffers"].append(address)
    state["process"].write(address, bytes(size) if initial is None else initial)
    return address


def _free(state, *addresses):
    for address in addresses:
        state["process"].free(address)
        state["owned_buffers"].remove(address)


def _world(state, label):
    return query.observation(state, label)


async def _batch(state, label, calls, *, prepare=None):
    _world(state, label + "_before")
    if prepare is None:
        make = lambda: v3_access.submit_batch(state["client"], calls, mode=1, flag=0,
            dispatch="interface", process=state["process"], adapter=state["access_adapter"],
            call_adapter=state["call_adapter"])
    else:
        callback = resolve_v3_export(state["process"], state["call_adapter"], "PdxNativeCallBatchInterface")
        make = lambda: v3_access.submit_callback(state["client"], callback,
            native_call.encode_batch(calls), mode=1, flag=0, dispatch="interface",
            process=state["process"], adapter=state["access_adapter"],
            call_adapter=state["call_adapter"], prepare_payload=prepare,
            decode_payload=native_call.NativeCallBatchResult.from_bytes)
    result = await accept._operation(state, label, make)
    batch = result.callee_result
    if (result.error or not result.entered or not result.left or batch is None
            or batch.error or batch.completed != len(calls)
            or len(batch.calls) != len(calls) or any(x.error for x in batch.calls)):
        state["cleanup_uncertain"] = True
        raise RuntimeError(label + ": incomplete original callback prefix; retain every input, never replay")
    _world(state, label + "_after")
    return batch


async def _country_valid(state, full_id, label, *, tag=5):
    memory = _alloc(state, 20, struct.pack("<H3HQI", tag, 0, 0, 0, full_id, INVALID))
    target = resolve_v3_export(state["process"], state["script_adapter"], "PdxV3ScriptScopeValid")
    batch = await _batch(state, label, ((target, (memory, memory + 16)),))
    valid = struct.unpack("<I", state["process"].read(memory + 16, 4))[0]
    _free(state, memory)
    if batch.calls[0].rax & INVALID or valid != 1:
        raise ValueError(label + ": original native full-ref is not valid")


async def _read_owned(state, owner, wire, label):
    """Original CMemoryFile/CBinLexer/CReader lifecycle around slot3.

    No raw C++ object layout is fabricated: zeroed storage is passed to the
    original constructors. The CBinLexer vptr install is the exact3371185..18C
    original construction step. The native destructor chain runs on this stack.
    Only copied status bytes are examined after callback completion.
    """
    process = state["process"]; image = state["image_base"]
    storage = _alloc(state, 0x400)
    raw = _alloc(state, len(wire), wire)
    file, lexer, reader, copied, vptr = (storage + n for n in (0, 0x60, 0x110, 0x2A0, 0x2F0))
    process.write(vptr, struct.pack("<Q", image + 0x4851170))
    memcpy = process.resolve_export("ntdll.dll", "memcpy")
    read = resolve_v3_export(process, state["commands_adapter"], "PdxV3CommandRead")
    calls = ((image + 0x3AFF620, (file, raw, len(wire), 0)),
             (image + 0x3B4EA20, (lexer, file, 0)),
             (memcpy, (lexer, vptr, 8)),
             (image + 0x3B50950, (reader, lexer, 0)),
             (image + 0x3B52C00, (reader,)),
             (read, (owner.command, reader)),
             (memcpy, (copied, reader, 0x38)),
             (image + 0x3B50C80, (reader,)),
             (image + 0x3B4E880, (lexer, 0)),
             (image + 0x3AFF770, (file,)))
    state["reader_storage"] = {"label": label, "storage": storage, "wire": raw,
        "lifecycle": "not_yet_confirmed"}
    batch = await _batch(state, label, calls)
    status = process.read(copied, 0x38)
    receipt = {"error_count": struct.unpack_from("<I", status, 0x14)[0],
        "failed": status[0x20], "last_token": struct.unpack_from("<I", status, 0x30)[0],
        "read_export_error": batch.calls[5].rax & INVALID,
        "native_destructors_returned": True}
    state["reader_receipts"].append(receipt)
    state["reader_storage"]["lifecycle"] = "original_destructors_returned"
    _free(state, raw, storage)
    if receipt["read_export_error"] or receipt["error_count"] or receipt["failed"] != 0:
        state["status"] = "original_reader_rejected_owned"
        raise RuntimeError("original native Reader rejected this exact wire; no Serialize/Validate/Submit")
    return receipt


async def _native_context(state, owner):
    # Both fields come from the original live native scalar source, not from a
    # borrowed model. Counter is NOT incremented or replaced by this helper.
    memory = _alloc(state, 8)
    memcpy = state["process"].resolve_export("ntdll.dll", "memcpy")
    await _batch(state, "copy_native_context_seed", ((memcpy,
        (memory, state["image_base"] + 0x5148090, 8)),))
    counter, salt = struct.unpack("<II", state["process"].read(memory, 8))
    _free(state, memory)
    seed = context_seed(counter, salt)
    state["context_source"] = {"counter": counter, "salt": salt, "seed": seed,
        "source_rva": 0x5148090, "native_rng_written": False,
        "producer_rva": 0x11A4DA0}
    # 11A4DA0's output is a newly constructed non-string tag3/4 variant. The
    # staging context is native tag0. No active CString is overwritten/leaked.
    batch = await _batch(state, "original_context_producer", ((state["image_base"] + 0x11A4DA0,
        (owner.command + 0x98, owner.command + 0x28, seed)),))
    if batch.calls[0].rax != owner.command + 0x98:
        state["cleanup_uncertain"] = True
        raise RuntimeError("original context producer output identity changed")


async def _native_cost(state, owner):
    """Same D513B0 calculation as original model2228E80 ->180/188.

    Resolve source/target/actor and consume their borrowed pointers in this SAME
    callback. The actual article definition is read from this owned command's
    native vector inside that callback. No resolver RAX is read in later Python.
    free_text is directed, maintenance_paid_by=target_country. This representative
    entry requires actor=that payer. It therefore always preserves the original
    D513B0 output, even when the real native output happens to be zero. Final27FE
    is filled via original Reader. Other payer branches are not guessed here.
    """
    if state["actor_full_country_id"] != state["target_full_country_id"]:
        raise ValueError("the representative path must use the real target payer actor")
    process = state["process"]; image = state["image_base"]
    memory = _alloc(state, 32, struct.pack("<4I2Q", state["source_full_country_id"],
        state["target_full_country_id"], state["actor_full_country_id"], 0, 0, 0))
    memcpy = process.resolve_export("ntdll.dll", "memcpy")
    # Hard-coded original native call sequence, NOT a dependency DSL.
    # 0 source resolver;1 target;2 actor;3 copies owned vector pointer into memcpy arg;
    # 4 memcpy copies article[0].definition into cost arg;5..7 forward resolver RAX;
    # 8 original D513B0(sret, definition, source, target, actor, nullable reason).
    calls = ((image + 0x7BB480, (memory,)),
        (image + 0x7BB480, (memory + 4,)), (image + 0x7BB480, (memory + 8,)),
        (memcpy, (0, owner.command + 0x28, 8)),
        (memcpy, (0, 0, 8)),
        (memcpy, (0, 0, 8)), (memcpy, (0, 0, 8)), (memcpy, (0, 0, 8)),
        (image + 0xD513B0, (memory + 16, 0, 0, 0, 0, 0)))
    def prepare(address):
        encoded = bytearray(native_call.encode_batch(calls, request_address=address))
        records = [address + 32 + i * 56 for i in range(9)]
        args = [struct.unpack_from("<Q", encoded, 32 + i * 56 + 24)[0] for i in range(9)]
        def set_arg(call, pos, value):
            struct.pack_into("<Q", encoded, args[call] - address + pos * 8, value)
        set_arg(3, 0, args[4] + 8)
        set_arg(4, 0, args[8] + 8)
        for i in range(3):
            set_arg(5 + i, 0, args[8] + (2 + i) * 8)
            set_arg(5 + i, 1, records[i] + 32)
        return bytes(encoded)
    batch = await _batch(state, "original_actor_cost_producer", calls, prepare=prepare)
    cost = struct.unpack("<Q", process.read(memory + 16, 8))[0]
    if batch.calls[8].rax != memory + 16:
        raise RuntimeError("same-callback native cost output identity changed; no use/replay")
    cost = cost if cost < 1 << 63 else cost - (1 << 64)
    state["cost_source"] = {"value": cost, "source": "original D513B0 same-callback native calculation",
                            "native_calculation_needed": True}
    _free(state, memory)
    return cost


def _field(block, token, *, optional=False):
    values = [a.value for a in block if isinstance(a, c.Assignment)
        and isinstance(a.key, c.Token) and a.key.value == token]
    if not values and optional:
        return None
    if len(values) != 1:
        raise RuntimeError("native wire field missing/ambiguous: " + hex(token))
    return values[0]


def check_prepared_wire(wire, state, *, staging=False):
    tree = c.decode_command(wire)
    if tree.key != c.Token(TOKEN):
        raise RuntimeError("native producer returned another command type")
    domain = _field(tree.value, 0x41); payload = _field(domain, 0x37C4)
    actor = c.UInt32(state["actor_full_country_id"])
    if _field(tree.value, 0x2840) != actor or _field(domain, 0x240) != actor:
        raise RuntimeError("native actor mirrors changed")
    expected = c.decode_command(state["input_wire"])
    expected_payload = _field(_field(expected.value, 0x41), 0x37C4)
    # Native writer omits only these proved native defaults. All nondefault
    # nested terms (real key, source/target, exact text) must compare as typed.
    omissions = {0x60BA: c.UInt32(INVALID), 0x60CB: NO_DATE, 0x60E2: c.UInt32(INVALID),
                 0x60E3: (), 0x60E4: (), 0x620E: c.Token(0x165)}
    for assignment in expected_payload:
        token = assignment.key.value
        got = _field(payload, token, optional=token in omissions)
        if got is None and token in omissions:
            got = omissions[token]
        if got != assignment.value:
            raise RuntimeError("native typed proposal term changed: " + hex(token))
    if staging:
        # The payload and all potentially uninitialized scalars are now proved
        # filled by native Reader. Context/cost are NOT yet prepared semantics.
        if _field(domain, 0x27FE) != c.Int64(0):
            raise RuntimeError("native staging Int64 was not initialized by Reader")
        # FDDE60 always emits domain1B; tag0's original11A59C0 emits no
        # children. 11A4DA0 is fresh construction, not assignment/destruction
        # of an already active owned CString. Prove tag0 before that producer.
        if _field(domain, 0x1B) != ():
            raise RuntimeError("native staging context is not original empty tag0; do not overwrite an active variant")
        return tree
    if _field(domain, 0x27FE) != c.Int64(state["cost_source"]["value"]):
        raise RuntimeError("native actor-cost calculation not preserved")
    context = _field(domain, 0x1B)
    if not context:
        raise RuntimeError("original native context producer did not supply a context")
    if len(context) != 1 or context[0].key not in (c.Token(0x60D8), c.Token(0x51D)):
        raise RuntimeError("unexpected original context variant")
    return tree


async def prepare_proposal(client, process, *, actor_full_country_id,
        source_full_country_id, target_full_country_id, text, binding_days,
        image_base, commands_adapter="pdx_v3_commands.dll",
        access_adapter="pdx_v3_access.dll", call_adapter="pdx_native_call.dll",
        script_adapter="pdx_v3_script.dll", data_adapter="pdx_v3_data.dll",
        wire_capacity=65536):
    """Fill/Reader-check -> true native context/cost -> Clone/Serialize/Validate.

    binding_days is explicit user intent in native days (zero means no binding
    period); native Validate remains authoritative. This is only a new voluntary
    free_text proposal, not amendments, enforced treaties or every ArticleType.
    The caller supplies previously observed real full country IDs and image base.
    This function neither submits nor changes the player/source-client context.
    """
    _no_prior_unknown(client)
    wire = free_text_wire(actor_full_country_id=actor_full_country_id,
        source_full_country_id=source_full_country_id, target_full_country_id=target_full_country_id,
        text=text, binding_days=binding_days)
    if getattr(client, "pid", None) != getattr(process, "pid", None):
        raise ValueError("client/process identity mismatch")
    _integer(image_base, "image_base", 1, (1 << 64) - 1)
    state = accept._state(client, process, actor_full_country_id, image_base,
        {"data": data_adapter, "access": access_adapter, "call": call_adapter,
         "script": script_adapter, "commands": commands_adapter})
    state.update(status="preparing_proposal", actor_full_country_id=actor_full_country_id,
        source_full_country_id=source_full_country_id, target_full_country_id=target_full_country_id,
        input_wire=wire, text=text, binding_days=binding_days, reader_receipts=[],
        staging_owner=None, creation_result=None, reader_storage=None,
        cost_source=None, context_source=None)
    ATTEMPTS.append(state)
    try:
        state["world"] = _world(state, "proposal_initial_world")
        await _country_valid(state, source_full_country_id, "source_full_ref")
        await _country_valid(state, target_full_country_id, "target_full_ref")
        created = await accept._operation(state, "registered_native_factory", lambda: c.request(client,
            c.Action.CREATE, type_token=TOKEN, world=state["world"], adapter=commands_adapter, process=process))
        state["creation_result"] = created
        if (created.error or created.action != c.Action.CREATE or created.type_token != TOKEN
                or created.world != state["world"] or not created.command):
            if created.command:
                state["cleanup_uncertain"] = True
            raise RuntimeError("registered native factory did not return a proved same-world unique owner")
        staging = c.NativeCommand.from_result(client, created, adapter=commands_adapter, process=process)
        state["staging_owner"] = staging
        await _read_owned(state, staging, wire, "complete_original_reader")
        staged_wire = await accept._operation(state, "serialize_checked_staging_payload",
            lambda: staging.serialize(capacity=wire_capacity), owner=staging)
        state["staging_serialized"] = staged_wire
        if staged_wire.error or staging.attempt.outcome != "owned":
            raise RuntimeError("checked staging payload Serialize failed/unknown; no producer")
        check_prepared_wire(staged_wire.data, state, staging=True)
        await _native_context(state, staging)
        cost = await _native_cost(state, staging)
        # A scalar-only patch uses native Read too; no raw command field store.
        cost_wire = c.encode_command(TOKEN, {0x41: {0x27FE: c.Int64(cost)}})
        await _read_owned(state, staging, cost_wire, "native_cost_original_reader")
        cloned = await accept._operation(state, "clone_filled_native_proposal", staging.clone, owner=staging)
        if cloned.cloned_command:
            state["unadopted_clone"] = cloned
        if (cloned.error or staging.attempt.outcome != "owned" or cloned.action != c.Action.CLONE
                or cloned.type_token != TOKEN or cloned.world != state["world"]
                or not cloned.cloned_command or cloned.cloned_command == staging.command):
            state["cleanup_uncertain"] = True
            raise RuntimeError("Clone output ownership is not proved; keep exact receipt")
        owner = c.NativeCommand.from_result(client, cloned, adapter=commands_adapter, process=process)
        state["unique_owner"] = owner; state["unadopted_clone"] = None
        serialized = await accept._operation(state, "serialize_filled_proposal",
            lambda: owner.serialize(capacity=wire_capacity), owner=owner)
        state["serialized"] = serialized
        if serialized.error or owner.attempt.outcome != "owned":
            raise RuntimeError("native proposal Serialize failed or ownership is unknown")
        state["wire_tree"] = check_prepared_wire(serialized.data, state)
        state["prepared_wire_sha256"] = hashlib.sha256(serialized.data).hexdigest()
        validated = await accept._operation(state, "validate_filled_proposal", owner.validate, owner=owner)
        state["validation"] = validated
        if validated.error not in (0, 122) or owner.attempt.outcome != "owned":
            raise RuntimeError("native proposal Validate receipt/ownership is unknown")
        state["status"] = "prepared_native_proposal_valid" if validated.validated else "native_proposal_rejected_owned"
        return state
    except BaseException as error:
        state["failure"] = {"type": type(error).__name__, "message": str(error)[:500]}
        if state["status"] != "original_reader_rejected_owned":
            state["status"] = "proposal_prepare_failed_retained"
        error.query_state = state
        raise


async def _submit_once(state, *, actor_full_country_id, prepared_wire_sha256, flags, ready_status):
    """One original Session owner transfer; independent of the prepare calls."""
    _integer(actor_full_country_id, "actor_full_country_id", 0, INVALID - 1)
    owner = state["unique_owner"]
    if (state["pending"] is not None or state["handoff_uncertain"] or state["cleanup_uncertain"]
            or state["submission_attempted"] or state["status"] != ready_status
            or owner is None or not owner.command or owner.pending is not None):
        raise RuntimeError("no unattempted, known-owned, natively valid prepared proposal")
    if (actor_full_country_id != state["actor_full_country_id"]
            or prepared_wire_sha256 != state["prepared_wire_sha256"]):
        raise ValueError("explicit actor/exact-terms review does not match this prepared proposal")
    _world(state, "proposal_submit_world")
    state["submission_attempted"] = True
    try:
        result = await accept._operation(state, "submit_proposal_once", lambda: owner.submit(flags), owner=owner)
    except BaseException:
        state["handoff_uncertain"] = True; state["status"] = "proposal_delivery_unknown_no_replay"
        raise
    state["submit_result"] = result
    if owner.attempt.outcome == "unknown":
        state["handoff_uncertain"] = True; state["status"] = "proposal_handoff_unknown_no_replay"
    elif result.error:
        state["status"] = "native_prequeue_failure_owned_no_retry"
    else:
        state["status"] = "native_proposal_enqueued_effect_unobserved" if result.accepted else "native_proposal_rejected_consumed"
    return result


async def cleanup_completed(state):
    """Explicit known-owner cleanup only. Native unknowns retain all inputs."""
    if state["pending"] is not None or state["handoff_uncertain"] or state["cleanup_uncertain"] or state["unadopted_clone"] is not None:
        return False
    for key in ("unique_owner", "staging_owner"):
        owner = state[key]
        if owner is not None and (owner.pending is not None or (owner.attempt is not None
                and owner.attempt.outcome in ("pending", "unknown"))):
            return False
        if owner is not None and owner.command:
            result = await accept._operation(state, "destroy_" + key, owner.destroy, owner=owner)
            if result.error or owner.attempt.outcome != "consumed":
                state["cleanup_uncertain"] = True
                return False
    _free(state, *tuple(state["owned_buffers"]))
    state["status_before_cleanup"] = state["status"]
    state["status"] = "completed_known_cleanup"
    return True




async def submit_proposal(state, *, actor_full_country_id, prepared_wire_sha256, flags=4):
    """Separate Root consent for exact original serialized free_text terms."""
    return await _submit_once(state, actor_full_country_id=actor_full_country_id,
        prepared_wire_sha256=prepared_wire_sha256, flags=flags,
        ready_status="prepared_native_proposal_valid")


async def _proposal_snapshot(state, full_ref, label):
    """Copied original CTreaty state, with borrowed resolver RAX consumed locally."""
    _integer(full_ref, "proposal_full_id", 0, INVALID - 1)
    process = state["process"]; image = state["image_base"]
    memory = _alloc(state, 0x98)
    process.write(memory, struct.pack("<I", full_ref))
    rpm = process.resolve_export("kernel32.dll", "ReadProcessMemory")
    memcpy = process.resolve_export("ntdll.dll", "memcpy")
    calls = ((image + 0xC5A180, (memory,)),
        (memcpy, (0, 0, 8)),
        (rpm, (0xFFFFFFFFFFFFFFFF, 0, memory + 8, 0x88, memory + 0x90)))
    def prepare(address):
        encoded = bytearray(native_call.encode_batch(calls, request_address=address))
        args1 = struct.unpack_from("<Q", encoded, 32 + 56 + 24)[0]
        args2 = struct.unpack_from("<Q", encoded, 32 + 112 + 24)[0]
        struct.pack_into("<2Q", encoded, args1 - address, args2 + 8, address + 32 + 32)
        return bytes(encoded)
    batch = await _batch(state, label, calls, prepare=prepare)
    raw = process.read(memory + 8, 0x88)
    received = struct.unpack("<Q", process.read(memory + 0x90, 8))[0]
    _free(state, memory)
    if batch.calls[2].rax != 1 or received != 0x88:
        raise RuntimeError("same-callback original Treaty state copy failed")
    actual = struct.unpack_from("<I", raw, 8)[0]
    parties = struct.unpack_from("<2I", raw, 0x80)
    if actual != full_ref or raw[0x2C] not in (0, 1):
        raise RuntimeError("original Treaty ref/fallback/state identity changed")
    return {"full_ref": actual, "is_draft": bool(raw[0x2C]), "party_full_refs": parties,
        "world": state["world"]}


async def prepare_response(client, process, *, actor_full_country_id, proposal_full_id,
        image_base, commands_adapter="pdx_v3_commands.dll",
        access_adapter="pdx_v3_access.dll", call_adapter="pdx_native_call.dll",
        script_adapter="pdx_v3_script.dll", data_adapter="pdx_v3_data.dll", wire_capacity=65536):
    """Prepare one ACCEPT for a real current draft fullRef, never decline.

    The caller selects/reviews actual proposal terms separately. This fills the
    same original14275 typed fields as AcceptTreatyProposal, with explicit actor
    intent, native Scope65/current draft byte+party checks, original Reader,
    unique Clone, Serialize and true-branch Validate. No borrowed GUI model or
    false-branch Validate shortcut is used. No command is submitted here.
    """
    _no_prior_unknown(client)
    _integer(actor_full_country_id, "actor_full_country_id", 0, INVALID - 1)
    _integer(proposal_full_id, "proposal_full_id", 0, INVALID - 1)
    _integer(image_base, "image_base", 1, (1 << 64) - 1)
    if getattr(client, "pid", None) != getattr(process, "pid", None):
        raise ValueError("matching live client/process are required")
    state = accept._state(client, process, actor_full_country_id, image_base,
        {"data": data_adapter, "access": access_adapter, "call": call_adapter,
         "script": script_adapter, "commands": commands_adapter})
    state.update(status="preparing_response", actor_full_country_id=actor_full_country_id,
        source_full_country_id=None, target_full_country_id=None, proposal_full_id=proposal_full_id,
        reader_receipts=[], staging_owner=None, creation_result=None, reader_storage=None,
        cost_source=None, context_source=None, response_observations=[])
    ATTEMPTS.append(state)
    try:
        state["world"] = _world(state, "response_initial_world")
        await _country_valid(state, actor_full_country_id, "response_actor_valid")
        await _country_valid(state, proposal_full_id, "response_proposal_valid", tag=0x65)
        before = await _proposal_snapshot(state, proposal_full_id, "response_current_draft")
        state["response_before"] = before
        if (not before["is_draft"] or before["party_full_refs"][0] == before["party_full_refs"][1]
                or actor_full_country_id not in before["party_full_refs"]):
            raise ValueError("selected native Treaty is not a current distinct-party draft for this actor")
        state["source_full_country_id"], state["target_full_country_id"] = before["party_full_refs"]
        wire = c.encode_command(14275, {0x2840: c.UInt32(actor_full_country_id),
            0x41: {0x240: c.UInt32(actor_full_country_id), 0x57D6: c.UInt32(proposal_full_id), 0x37C5: True}})
        state["input_wire"] = wire
        created = await accept._operation(state, "registered_response_factory", lambda: c.request(client,
            c.Action.CREATE, type_token=14275, world=state["world"], adapter=commands_adapter, process=process))
        state["creation_result"] = created
        if (created.error or created.action != c.Action.CREATE or created.type_token != 14275
                or created.world != state["world"] or not created.command):
            if created.command:
                state["cleanup_uncertain"] = True
            raise RuntimeError("response factory same-world unique ownership not proved")
        staging = c.NativeCommand.from_result(client, created, adapter=commands_adapter, process=process)
        state["staging_owner"] = staging
        await _read_owned(state, staging, wire, "response_original_reader")
        clone = await accept._operation(state, "clone_typed_response", staging.clone, owner=staging)
        if clone.cloned_command:
            state["unadopted_clone"] = clone
        if (clone.error or staging.attempt.outcome != "owned" or clone.type_token != 14275
                or clone.world != state["world"] or not clone.cloned_command
                or clone.cloned_command == staging.command):
            state["cleanup_uncertain"] = True
            raise RuntimeError("response Clone ownership not proved; preserve receipt")
        owner = c.NativeCommand.from_result(client, clone, adapter=commands_adapter, process=process)
        state["unique_owner"] = owner; state["unadopted_clone"] = None
        serialized = await accept._operation(state, "serialize_typed_response",
            lambda: owner.serialize(capacity=wire_capacity), owner=owner)
        state["serialized"] = serialized
        if serialized.error or owner.attempt.outcome != "owned":
            raise RuntimeError("response native Serialize failed/unknown")
        tree, actor = accept._accept_wire(serialized.data, proposal_full_id)
        if actor != actor_full_country_id:
            raise RuntimeError("response native actor provenance changed")
        state["wire_tree"] = tree
        state["prepared_wire_sha256"] = hashlib.sha256(serialized.data).hexdigest()
        valid = await accept._operation(state, "validate_true_branch_response", owner.validate, owner=owner)
        state["validation"] = valid
        if valid.error not in (0, 122) or owner.attempt.outcome != "owned":
            raise RuntimeError("response native Validate receipt/ownership unknown")
        state["status"] = "prepared_native_response_valid" if valid.validated else "native_response_rejected_owned"
        return state
    except BaseException as error:
        state["failure"] = {"type": type(error).__name__, "message": str(error)[:500]}
        state["status"] = "response_prepare_failed_retained"
        error.query_state = state
        raise


async def submit_response(state, *, actor_full_country_id, proposal_full_id,
                          prepared_wire_sha256, flags=4):
    """Separate explicit ACCEPT decision for this actual reviewed live draft."""
    _integer(proposal_full_id, "proposal_full_id", 0, INVALID - 1)
    if proposal_full_id != state["proposal_full_id"]:
        raise ValueError("explicit response target differs from the reviewed native draft")
    result = await _submit_once(state, actor_full_country_id=actor_full_country_id,
        prepared_wire_sha256=prepared_wire_sha256, flags=flags,
        ready_status="prepared_native_response_valid")
    state["status"] = state["status"].replace("proposal", "response")
    return result


async def observe_response_state(state):
    """Independent fresh copied native state; never clears unknown/replays Submit."""
    if state["pending"] is not None:
        raise RuntimeError("original unknown callback must settle before another operation")
    seen = await _proposal_snapshot(state, state["proposal_full_id"], "observe_response_" + str(len(state["response_observations"])))
    seen["same_parties"] = seen["party_full_refs"] == state["response_before"]["party_full_refs"]
    seen["native_draft_to_inforce_transition"] = bool(state["response_before"]["is_draft"]
        and not seen["is_draft"] and seen["same_parties"])
    state["response_observations"].append(seen)
    return seen
