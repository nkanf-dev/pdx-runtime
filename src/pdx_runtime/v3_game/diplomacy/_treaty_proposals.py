"""Native mutual treaty terms over the existing generic command/session API.

No connection, game launch or native call occurs on import. The ordinary treaty
proposal helper owns Reader/context/command lifetimes. This module adds the
original mutual-article field/cost path; it does not alter the free_text helper.
"""
from __future__ import annotations

import hashlib
import struct

from ... import native_call, v3_commands as commands
from ._treaty_catalog import article_types
from pdx_runtime.v3_game.diplomacy.treaty import proposal as mature

INVALID = 0xFFFFFFFF


def state_for(ctx, actor):
    state = mature.accept._state(ctx.native, ctx.process, actor, ctx.image_base,
        {name: ctx.adapters[name] for name in ("data", "access", "call", "script", "commands")})
    state["world"] = ctx.world
    return state


def terms_wire(actor, first, second, keys, binding_days):
    """Complete original new-voluntary payload with genuine mutual clause keys."""
    for name, value in (("actor", actor), ("first", first), ("second", second)):
        mature._integer(value, name, 0, INVALID - 1)
    if first == second or actor not in (first, second):
        raise ValueError("a treaty needs two distinct countries including the acting country")
    mature._integer(binding_days, "binding_days", 0, 0x7FFFFFFF)
    if not keys or any(not isinstance(key, str) or not key or "\0" in key for key in keys):
        raise ValueError("treaty articles require actual nonempty definition keys")
    articles = tuple({0x2F11: key, 0x60C7: commands.UInt32(first), 0x60C8: commands.UInt32(second), 0x81B: ()}
                     for key in keys)
    payload = {0x60C0: articles, 0x60BA: commands.UInt32(INVALID),
        0x32CF: commands.UInt32(first), 0x32D0: commands.UInt32(second), 0x60CB: mature.NO_DATE,
        0x60CA: binding_days, 0x60BB: True, 0x62AB: False, 0x60E2: commands.UInt32(INVALID),
        0x60E3: (), 0x60E4: (), 0x620E: commands.Token(0x165)}
    return commands.encode_command(mature.TOKEN, {0x2840: commands.UInt32(actor),
        0x41: {0x240: commands.UInt32(actor), 0x37C4: payload, 0x1B: {}, 0x27FE: commands.Int64(0)}})


def _normalized_articles(value):
    """Only D54E20's proved omission of an empty parameter vector is restored."""
    result = []
    for article in value:
        if not isinstance(article, tuple):
            raise RuntimeError("native treaty article is not a typed block")
        items = {part.key.value: part.value for part in article if isinstance(part, commands.Assignment)}
        if len(items) != len(article):
            raise RuntimeError("native article fields are missing or duplicated")
        items.setdefault(0x81B, ())
        result.append(items)
    return result


def check_wire(wire, state, *, staging=False):
    # Reuse the original full payload/context/cost checker, with precisely the
    # native empty81B omission restored in the copied comparison tree only.
    tree = commands.decode_command(wire)
    domain = mature._field(tree.value, 0x41)
    payload = mature._field(domain, 0x37C4)
    original = commands.decode_command(state["input_wire"])
    expected = mature._field(mature._field(original.value, 0x41), 0x37C4)
    actual_articles = _normalized_articles(mature._field(payload, 0x60C0))
    expected_articles = _normalized_articles(mature._field(expected, 0x60C0))
    if actual_articles != expected_articles:
        raise RuntimeError("native article key, country refs or typed terms changed")
    repaired = []
    for item in tree.value:
        if item.key != commands.Token(0x41):
            repaired.append(item); continue
        block = []
        for field in item.value:
            if field.key != commands.Token(0x37C4):
                block.append(field); continue
            body = tuple(commands.Assignment(child.key, mature._field(expected, 0x60C0))
                         if child.key == commands.Token(0x60C0) else child for child in field.value)
            block.append(commands.Assignment(field.key, body))
        repaired.append(commands.Assignment(item.key, tuple(block)))
    comparison = commands.encode_tree(commands.Assignment(tree.key, tuple(repaired)))
    mature.check_prepared_wire(comparison, state, staging=staging)
    return tree


async def default_binding_days(state):
    memory = mature._alloc(state, 4)
    memcpy = state["process"].resolve_export("ntdll.dll", "memcpy")
    await mature._batch(state, "native_default_binding_period", ((memcpy,
        (memory, state["image_base"] + 0x58FEA58, 4)),))
    years = struct.unpack("<i", state["process"].read(memory, 4))[0]
    mature._free(state, memory)
    days = years * 365  # Exact original model upstream, not a chosen strategy.
    mature._integer(days, "native default binding days", 0, 0x7FFFFFFF)
    return days


async def _mutual_contract(state, owner, article_index):
    """Check this actual Reader-created entry before any mutual cost call.

    The vector belongs to the retained original command. Only copied scalars
    leave this callback; the later cost call resolves its definition anew.
    """
    process = state["process"]
    size = 40 * (article_index + 1)
    memory = mature._alloc(state, size + 0xEC)
    memcpy = process.resolve_export("ntdll.dll", "memcpy")
    calls = ((memcpy, (0, owner.command + 0x28, 8)),
        (memcpy, (memory, 0, size)),
        (memcpy, (0, memory + article_index * 40, 8)),
        (memcpy, (memory + size, 0, 0xEC)))
    def prepare(address):
        raw = bytearray(native_call.encode_batch(calls, request_address=address))
        args = [struct.unpack_from("<Q", raw, 32 + i * 56 + 24)[0] for i in range(len(calls))]
        struct.pack_into("<Q", raw, args[0] - address, args[1] + 8)
        struct.pack_into("<Q", raw, args[2] - address, args[3] + 8)
        return bytes(raw)
    await mature._batch(state, "mutual_entry_contract_" + str(article_index), calls, prepare=prepare)
    raw = process.read(memory, size + 0xEC)
    capacity, count = struct.unpack_from("<ii", raw, article_index * 40 + 0x18)
    kind = raw[size + 0x40]
    declared_inputs = struct.unpack_from("<i", raw, size + 0xE8)[0]
    mature._free(state, memory)
    if kind != 1:
        raise ValueError("the original treaty definition is not mutual; choose its directed terms path")
    if declared_inputs != 0:
        raise ValueError("this article requires native input terms; choose a preparation path that supplies those inputs")
    if count != 0 or capacity < 0:
        raise ValueError("this mutual preparation requires an actual empty native input vector")
    return {"kind": "mutual", "native_kind": kind, "input_count": count, "declared_input_count": declared_inputs,
        "source": "actual original Reader-created owned article entry"}


async def _mutual_cost(state, owner, article_index, *, country_id=None):
    """Original mutual branch: native D513B0 for each actual party, no payer guess."""
    process, image = state["process"], state["image_base"]
    size = 40 * (article_index + 1)
    memory = mature._alloc(state, 64 + size + 0x48)
    perspective = state["actor_full_country_id"] if country_id is None else country_id
    process.write(memory, struct.pack("<4I", state["source_full_country_id"], state["target_full_country_id"],
        perspective, 0))
    memcpy = process.resolve_export("ntdll.dll", "memcpy")
    # Prefix copied from this uniquely owned command's actual Article vector.
    calls = ((image + 0x7BB480, (memory,)), (image + 0x7BB480, (memory + 4,)),
        (image + 0x7BB480, (memory + 8,)),
        (memcpy, (0, owner.command + 0x28, 8)),
        (memcpy, (memory + 64, 0, size)),
        (memcpy, (0, memory + 64 + article_index * 40, 8)),
        (memcpy, (0, 0, 8)), (memcpy, (0, 0, 8)), (memcpy, (0, 0, 8)),
        (memcpy, (0, memory + 64 + article_index * 40, 8)),
        (memcpy, (memory + 64 + size, 0, 0x48)),
        (image + 0xD513B0, (memory + 16, 0, 0, 0, 0, 0)))
    def prepare(address):
        raw = bytearray(native_call.encode_batch(calls, request_address=address))
        records = [address + 32 + i * 56 for i in range(len(calls))]
        args = [struct.unpack_from("<Q", raw, 32 + i * 56 + 24)[0] for i in range(len(calls))]
        def put(index, position, value): struct.pack_into("<Q", raw, args[index] - address + position * 8, value)
        put(3, 0, args[4] + 8)
        put(5, 0, args[11] + 8)
        for index in range(3):
            put(6 + index, 0, args[11] + (2 + index) * 8)
            put(6 + index, 1, records[index] + 32)
        put(9, 0, args[10] + 8)
        return bytes(raw)
    batch = await mature._batch(state, "native_mutual_article_cost_" + str(article_index), calls, prepare=prepare)
    if batch.calls[11].rax != memory + 16:
        raise RuntimeError("native mutual article cost output changed")
    result = struct.unpack("<q", process.read(memory + 16, 8))[0]
    kind = process.read(memory + 64 + size + 0x40, 1)[0]
    mature._free(state, memory)
    if kind != 1:
        raise ValueError("this article is not mutual; its directed payer/input terms need a directed preparation path")
    return result


async def prepare(ctx, actor, first, second, keys, *, binding_days=None, expected_world=None, expected_image=None, on_state=None):
    """Native prepare only. The original typed Reader resolves the real keys."""
    mature._no_prior_unknown(ctx.native)
    if ctx.native.pid != ctx.process.pid:
        raise ValueError("the treaty session and process must be the same existing game")
    state = state_for(ctx, actor)
    state["world"] = ctx.world if expected_world is None else expected_world
    state["image_base"] = ctx.image_base if expected_image is None else expected_image
    state.update(status="preparing_mutual_proposal", actor_full_country_id=actor,
        source_full_country_id=first, target_full_country_id=second, article_keys=tuple(keys),
        reader_receipts=[], staging_owner=None, creation_result=None, reader_storage=None,
        cost_source=None, context_source=None)
    if on_state is not None:
        on_state(state)  # Strong facade ledger, before the first allocation/call.
    mature.ATTEMPTS.append(state)
    try:
        state["binding_days_source"] = "native_game_default" if binding_days is None else "explicit"
        if binding_days is None:
            binding_days = await default_binding_days(state)
        state["binding_days"] = binding_days
        wire = terms_wire(actor, first, second, keys, binding_days)
        state["input_wire"] = wire
        for full_id in (first, second):
            await mature._country_valid(state, full_id, "treaty_party_full_ref")
        created = await mature.accept._operation(state, "registered_native_treaty_factory", lambda:
            commands.request(ctx.native, commands.Action.CREATE, type_token=mature.TOKEN, world=state["world"],
                adapter=ctx.adapters["commands"], process=ctx.process))
        state["creation_result"] = created
        if (created.error or created.action != commands.Action.CREATE or created.type_token != mature.TOKEN
                or created.world != state["world"] or not created.command):
            state["cleanup_uncertain"] = bool(created.command)
            raise RuntimeError("native treaty factory did not return its unique owner")
        staging = commands.NativeCommand.from_result(ctx.native, created, adapter=ctx.adapters["commands"], process=ctx.process)
        state["staging_owner"] = staging
        await mature._read_owned(state, staging, wire, "complete_mutual_original_reader")
        staged = await mature.accept._operation(state, "serialize_mutual_staging", staging.serialize, owner=staging)
        state["staging_serialized"] = staged
        if staged.error or staging.attempt.outcome != "owned":
            raise RuntimeError("native mutual staging serialization ownership is unknown")
        check_wire(staged.data, state, staging=True)
        state["article_contracts"] = tuple([await _mutual_contract(state, staging, index)
            for index in range(len(keys))])
        await mature._native_context(state, staging)
        party_values = {party: tuple([await _mutual_cost(state, staging, index, country_id=party)
            for index in range(len(keys))]) for party in (first, second)}
        party_totals = {party: sum(values) for party, values in party_values.items()}
        for cost in party_totals.values():
            mature._integer(cost, "native treaty maintenance cost", -(1 << 63), (1 << 63) - 1)
        total = party_totals[actor]
        state["cost_source"] = {"value": total, "party_values": party_values, "party_totals": party_totals,
            "source": "original mutual D513B0 per-party fixed-scale calculation", "native_calculation_needed": True}
        await mature._read_owned(state, staging, commands.encode_command(mature.TOKEN,
            {0x41: {0x27FE: commands.Int64(total)}}), "mutual_cost_original_reader")
        cloned = await mature.accept._operation(state, "clone_mutual_proposal", staging.clone, owner=staging)
        if cloned.cloned_command:
            state["unadopted_clone"] = cloned
        if (cloned.error or cloned.action != commands.Action.CLONE or staging.attempt.outcome != "owned" or cloned.type_token != mature.TOKEN
                or cloned.world != state["world"] or not cloned.cloned_command or cloned.cloned_command == staging.command):
            state["cleanup_uncertain"] = True
            raise RuntimeError("native mutual proposal Clone ownership is unknown")
        owner = commands.NativeCommand.from_result(ctx.native, cloned, adapter=ctx.adapters["commands"], process=ctx.process)
        state["unique_owner"], state["unadopted_clone"] = owner, None
        serialized = await mature.accept._operation(state, "serialize_mutual_proposal", owner.serialize, owner=owner)
        state["serialized"] = serialized
        if serialized.error or owner.attempt.outcome != "owned":
            raise RuntimeError("native mutual proposal serialization is unknown")
        state["wire_tree"] = check_wire(serialized.data, state)
        # Existing mature submit API keeps this entirely internal; no review hash
        # or file is required from a player.
        state["prepared_wire_sha256"] = hashlib.sha256(serialized.data).hexdigest()
        validation = await mature.accept._operation(state, "validate_mutual_proposal",
            lambda: owner.validate(capacity=65536), owner=owner)
        state["validation"] = validation
        if validation.error not in (0, 122) or owner.attempt.outcome != "owned":
            raise RuntimeError("native mutual proposal validation ownership is unknown")
        state["status"] = "prepared_native_proposal_valid" if validation.validated else "native_proposal_rejected_owned"
        return state
    except BaseException as error:
        error.query_state = state
        state["failure"] = error
        raise


async def submit(state):
    return await mature.submit_proposal(state, actor_full_country_id=state["actor_full_country_id"],
        prepared_wire_sha256=state["prepared_wire_sha256"])


async def cleanup(state):
    return await mature.cleanup_completed(state)
