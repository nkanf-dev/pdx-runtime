"""Ordinary native AcceptTreatyProposal composition. No game/client on import.

Preparing is NOT consent to submit. Caller supplies original native context and
explicitly selects/reviews a real proposal. No synthetic permission is created.
"""
from .... import v3_commands as commands, v3_data as data
from . import query
ATTEMPTS = []
TOKEN = 14275


def _state(client, process, source_country_id, image_base, adapters):
    state = query.new_state(client, process, source_country_id, adapters["data"])
    state.update(image_base=image_base, world=None, country_id=source_country_id,
        access_adapter=adapters["access"], call_adapter=adapters["call"], script_adapter=adapters["script"],
        commands_adapter=adapters["commands"], owned_buffers=[], treaties=[], articles=[], types={},
        row_errors=0, sources_retained=True, pointers_live=True, status="preparing_accept",
        unique_owner=None, clone_result=None, unadopted_clone=None, serialized=None,
        validation=None, submit_result=None, submission_attempted=False,
        handoff_uncertain=False, cleanup_uncertain=False, proposal_full_id=None,
        actor_full_country_id=None, observer_results=[])
    ATTEMPTS.append(state)
    return state


async def _operation(state, label, make, *, owner=None):
    result = await query.operation(state, label, make, owner=owner)
    state["last_command_query"] = state["queries"][-1]
    return result


def _known_ok(result, label):
    if result.error: raise RuntimeError(label + ": native error " + str(result.error))
    return result


def _one(block, token):
    values = [item.value for item in block if isinstance(item, commands.Assignment)
        and isinstance(item.key, commands.Token) and item.key.value == token]
    if len(values) != 1: raise RuntimeError("native wire field missing/ambiguous: " + hex(token))
    return values[0]


def _accept_wire(wire, expected_ref):
    tree = commands.decode_command(wire)
    if not isinstance(tree.key, commands.Token) or tree.key.value != TOKEN:
        raise RuntimeError("native producer returned another command type")
    actor = _one(tree.value, 0x2840); domain = _one(tree.value, 0x41)
    repeated_actor = _one(domain, 0x240); target = _one(domain, 0x57D6); accept = _one(domain, 0x37C5)
    if (not isinstance(actor, commands.UInt32) or not isinstance(repeated_actor, commands.UInt32)
            or not isinstance(target, commands.UInt32) or type(accept) is not bool
            or actor.value != repeated_actor.value or target.value != expected_ref or accept is not True):
        raise RuntimeError("native Accept wire actor/ref/bool provenance changed")
    return tree, actor.value


async def prepare_accept(client, process, *, source_country_id, proposal_index, image_base,
                         data_adapter="pdx_v3_data.dll", access_adapter="pdx_v3_access.dll",
                         call_adapter="pdx_native_call.dll", script_adapter="pdx_v3_script.dll",
                         commands_adapter="pdx_v3_commands.dll", wire_capacity=65536):
    """Generate/Clone/Serialize/Validate, never submit. No default proposal index.

    The source Country is not assumed to be the command actor. The genuine
    producer captures GameState+F8; inspect actor_full_country_id and actual
    proposal terms before a separate user-intended submit_accept call.
    """
    query._u32(source_country_id, "source_country_id")
    if getattr(client, "pid", None) != getattr(process, "pid", None):
        raise ValueError("native client/process PID mismatch; no proposal request")
    if not isinstance(proposal_index, int) or isinstance(proposal_index, bool) or proposal_index < 0:
        raise ValueError("proposal_index must be an explicit current native source index")
    state = _state(client, process, source_country_id, image_base,
        {"data": data_adapter, "access": access_adapter, "call": call_adapter,
         "script": script_adapter, "commands": commands_adapter})
    try:
        state["world"] = query.observation(state, "accept_initial_world")
        await query._types(state, 256)
        country = data.ObjectBinding.country(state["types"]["Country"], source_country_id)
        source = await query._evaluate(state, "draft_source", "Country.GetDraftTreaties", country)
        state["source"] = source
        count = await query.complete(state, "draft_count", lambda: data.list_count(client,
            source.value_handle, adapter=data_adapter, process=process))
        state["native_draft_count"] = count.total
        if not count.total:
            state["status"] = "native_draft_source_empty_no_command_generated"
            return state  # No index0 fallback, other Country search or creation.
        if proposal_index >= count.total: raise ValueError("selected proposal index exceeds actual native count")
        element = await query.complete(state, "draft_element", lambda: data.list_element(client,
            source.value_handle, proposal_index, adapter=data_adapter, process=process))
        value = query._owned_object(state, element, "CDataTreaty", "Treaty")
        full_ref = await query._full_id(state, "proposal_full_ref", value, element.value_handle)
        validity = await query._scope_valid(state, "proposal_valid", query.TREATY_SCOPE, full_ref, element.value_handle)
        state.update(proposal_index=proposal_index, proposal_full_id=full_ref,
            proposal_element=element, proposal_validity=validity)
        if not validity["valid"]: raise RuntimeError("original draft source ref no longer valid")
        receiver = data.ObjectBinding(state["types"]["Treaty"], element.value_handle, retained=True)
        state["proposal_receiver"] = receiver  # Original named-context converter; Any tag is unchanged.
        generated = await query._evaluate(state, "native_accept_producer", "Treaty.AcceptTreatyProposal", receiver)
        state["generated_any"] = generated
        carrier_result = await query.complete(state, "command_value", lambda: data.command_value(client,
            generated.value_handle, output_bytes=32, adapter=data_adapter, process=process))
        carrier = carrier_result.value
        state["carrier_result"] = carrier_result; state["carrier"] = carrier
        if (not isinstance(carrier, data.NativeDataCommand) or not carrier.primary_pointer
                or not carrier.generation_world or carrier.generation_world != state["world"]):
            raise RuntimeError("native shared command lacks its original source-world; never later-stamp it")
        clone = await _operation(state, "native_unique_clone", lambda: commands.request(client,
            commands.Action.CLONE, command=carrier.primary_pointer, world=carrier.generation_world,
            capacity=8, adapter=commands_adapter, process=process))
        state["clone_result"] = clone
        if clone.cloned_command: state["unadopted_clone"] = clone
        if (clone.action != commands.Action.CLONE or clone.type_token != TOKEN
                or clone.world != carrier.generation_world):
            state["cleanup_uncertain"] = True  # Do not infer an unrecognized clone output owns nothing.
            raise RuntimeError("native Clone did not return a proved same-world unique Finalize owner")
        if clone.error:
            raise RuntimeError("native Clone returned an error; preserve any published clone and original query")
        owner = commands.NativeCommand.from_result(client, clone, adapter=commands_adapter, process=process)
        state["unique_owner"] = owner; state["unadopted_clone"] = None
        serialized = await _operation(state, "serialize_unique_accept", lambda: owner.serialize(capacity=wire_capacity), owner=owner)
        state["serialized"] = serialized; _known_ok(serialized, "serialize_unique_accept")
        tree, actor = _accept_wire(serialized.data, full_ref)
        state["wire_tree"] = tree; state["actor_full_country_id"] = actor
        validated = await _operation(state, "validate_unique_accept", lambda: owner.validate(), owner=owner)
        state["validation"] = validated; _known_ok(validated, "validate_unique_accept")
        state["status"] = "prepared_native_accept_valid" if validated.validated else "native_accept_validation_rejected_owned"
        return state
    except BaseException as error:
        state["failure"] = {"type": type(error).__name__, "message": str(error)[:500]}
        state["status"] = "accept_prepare_failed_retained"
        error.query_state = state
        raise


async def submit_accept(state, *, actor_full_country_id, proposal_full_id, flags=4):
    """One explicit user decision for the reviewed native proposal and actor.

    Matching intent fields is not a permission grant. Caller still owns the
    original native source-client/player/network context. Native Validate and
    original Session apply; there is no direct Execute or implicit submit.
    """
    query._u32(actor_full_country_id, "actor_full_country_id")
    query._u32(proposal_full_id, "proposal_full_id")
    if state["pending"] is not None or state["handoff_uncertain"] or state["submission_attempted"]:
        raise RuntimeError("unresolved/already attempted intent; inspect exact prior request, never replay")
    owner = state["unique_owner"]
    if owner is None or not owner.command or state["status"] != "prepared_native_accept_valid":
        raise RuntimeError("no owned, natively valid prepared Accept command")
    if actor_full_country_id != state["actor_full_country_id"] or proposal_full_id != state["proposal_full_id"]:
        raise ValueError("explicit decision does not match the reviewed original native actor/ref")
    query.observation(state, "accept_submit_world")
    state["submission_attempted"] = True
    try:
        result = await _operation(state, "submit_native_accept_once", lambda: owner.submit(flags=flags), owner=owner)
    except BaseException:
        state["handoff_query"] = (state["pending"]["query"] if state["pending"] is not None else owner.pending)
        state["handoff_uncertain"] = True; state["status"] = "accept_delivery_unknown_no_replay"
        raise
    state["submit_result"] = result
    state["handoff_query"] = state["last_command_query"]
    if result.action != commands.Action.SUBMIT or (not result.error and result.world != owner.world):
        state["handoff_uncertain"] = True
        state["status"] = "unexpected_submit_action_or_world_no_replay"
        return result
    if result.error:
        if not result.command:
            # 574 after original Session owner-clear is UNKNOWN, even though the
            # 88B callback is completed, buffer freed and owner.pending isNone.
            state["handoff_uncertain"] = True
            state["status"] = "accept_handoff_unknown_after_owner_clear"
        else: state["status"] = "native_prequeue_failure_command_still_owned"
    else:
        if result.command:
            state["handoff_uncertain"] = True
            state["status"] = "unexpected_submit_owner_receipt_no_replay"
            raise RuntimeError("native Submit did not consume its supplied owner")
        state["status"] = "native_accept_enqueued_effect_unobserved" if result.accepted else "native_accept_rejected_consumed"
    return result


async def observe_accept_membership(state, *, max_elements=128):
    """Independent fresh native list evidence, NOT accepted-as-effect.

    Complete draft absence + same-fullRef in-force presence supports the native
    proposal-state transition. Partial scans report None, never false absence.
    Parties/Article terms can be independently composed with the query helper.
    This observation never clears a handoff_uncertain ledger or retries Submit.
    """
    if state["pending"] is not None: raise RuntimeError("unresolved original callback; preserve it first")
    if not isinstance(max_elements, int) or isinstance(max_elements, bool) or max_elements <= 0:
        raise ValueError("max_elements must be positive")
    if state["proposal_full_id"] is None: raise RuntimeError("no selected native proposal")
    query.observation(state, "accept_observer_world")
    binding = data.ObjectBinding.country(state["types"]["Country"], state["country_id"])
    result = {"world": state["world"], "target_full_ref": state["proposal_full_id"], "views": []}
    serial = len(state["observer_results"])
    for field in ("GetDraftTreaties", "GetInForceTreaties"):
        label = "observe_" + str(serial) + "_" + field
        source = await query._evaluate(state, label, "Country." + field, binding)
        count = await query.complete(state, label + "_count", lambda: data.list_count(state["client"],
            source.value_handle, adapter=state["data_adapter"], process=state["process"]))
        ids = []
        for index in range(min(count.total, max_elements)):
            element = await query.complete(state, label + "_element_" + str(index), lambda index=index: data.list_element(
                state["client"], source.value_handle, index, adapter=state["data_adapter"], process=state["process"]))
            value = query._owned_object(state, element, "CDataTreaty", "Treaty")
            ids.append(await query._full_id(state, label + "_ref_" + str(index), value, element.value_handle))
        complete = len(ids) == count.total
        present = state["proposal_full_id"] in ids
        result["views"].append({"name": field, "native_count": count.total, "scanned": len(ids),
            "complete": complete, "target_present": True if present else False if complete else None,
            "full_refs": ids})
    state["observer_results"].append(result)
    return result


async def cleanup_completed(state):
    """No automatic cancellation, rollback, replay, or destructor dispatch."""
    if (state["pending"] is not None or state["handoff_uncertain"] or state["cleanup_uncertain"]
            or state["unadopted_clone"] is not None): return False
    owner = state["unique_owner"]
    if owner is not None and owner.pending is not None: return False
    if owner is not None and owner.command:
        result = await _operation(state, "destroy_owned_unique", lambda: owner.destroy(), owner=owner)
        if result.error or result.command:
            state["cleanup_uncertain"] = True
            return False
    await query.cleanup_completed(state)
    state["status"] = "completed_known_cleanup"
    return True
