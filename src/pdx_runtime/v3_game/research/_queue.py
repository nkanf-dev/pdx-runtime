"""Native research queue sources and commands on the existing game Run.

Top/Bottom are ordered native split views, with current selection observed
separately. Owned Technology rows stay in this Run; raw current pointers are
copied address observations only and are never rebound as retained receivers.
"""
from __future__ import annotations
import struct
from pdx_runtime import v3_data as data, v3_commands as commands, v3_objects as objects, v3_access
from pdx_runtime.v3_wire import V3Wire
from .. import _run


def new_plan(ctx, actor_id):
    run = _run.Run(ctx, "research_queue", namespace="research")
    plan = dict(run=run, client=ctx.native, process=ctx.process, world=ctx.world,
        actor_id=actor_id, image_base=ctx.image_base, types={}, submit_count=0,
        status="preparing", owner=None, observations=[])
    plan.update({name + "_adapter": ctx.adapters[name]
        for name in ("data", "commands", "objects", "access", "call")})
    run.public_result = plan
    return plan


def _world(plan):
    status = V3Wire(plan["client"]).world_status()
    value = status.get("world", 0)
    world = int(value, 0) if isinstance(value, str) else value
    if status.get("status") != "ok" or world != plan["world"] or status.get("transition_pending"):
        raise RuntimeError("research source world changed or is unavailable")
    return world


def _ok(result, label):
    if result.error:
        raise _run.KnownResultError(label, result)
    return result


async def _once(plan, label, factory, *, owner=None):
    result = await plan["run"].capture(label, factory, owner=owner)
    if getattr(result, "action", None) == commands.Action.CLONE and getattr(result, "cloned_command", 0):
        plan["run"].unadopted_command = result
    return result


def _country(plan):
    return data.ObjectBinding.country(plan["types"]["Country"], plan["actor_id"])


async def _eval(plan, label, expression, bindings):
    return _ok(await _once(plan, label, lambda: data.evaluate(plan["client"], expression,
        bindings, adapter=plan["data_adapter"], process=plan["process"])), label)


async def _types(plan):
    plan["types"] = {name: await plan["run"].ctx.type_id(name) for name in ("Country", "Technology")}


async def _element(plan, expression, index, type_name):
    source = await _eval(plan, "native_source", expression, (_country(plan),))
    selected = _ok(await _once(plan, "native_element", lambda: data.list_element(
        plan["client"], source.value_handle, index,
        adapter=plan["data_adapter"], process=plan["process"])), "native_element")
    value = selected.value
    if not isinstance(value, data.NativeObject) or f"CPdxPtr@VCData{type_name}" not in value.native_type_name:
        raise RuntimeError("native provider did not retain the actual typed object")
    plan["source"], plan["selected"] = source, selected
    return value, data.ObjectBinding(plan["types"][type_name], selected.value_handle, retained=True)


async def _wrapper_origin(plan, value, size):
    run, process = plan["run"], plan["process"]
    buffer = process.allocate(size)
    run.buffers.append(buffer)
    process.write(buffer, bytes(size))
    copied = _ok(await _once(plan, "technology_origin", lambda: v3_access.submit_call(
        plan["client"], process.resolve_export("ntdll.dll", "memcpy"),
        (buffer, value.pointer + 0x10, size), mode=1, flag=0, dispatch="interface",
        adapter=plan["access_adapter"], call_adapter=plan["call_adapter"], process=process)), "technology_origin")
    if copied.callee_result is None or copied.callee_result.error or copied.callee_result.rax != buffer:
        raise RuntimeError("technology origin copy incomplete; original query/buffer retained")
    origin = int.from_bytes(process.read(buffer, size), "little")
    process.free(buffer)
    run.buffers.remove(buffer)
    return origin


def adopt_completed_clone(plan, result):
    owner = plan["run"].adopt(result, expected_world=plan["world"])
    plan["run"].unadopted_command = None
    plan["owner"] = owner
    return owner


async def cleanup_known(plan):
    return await plan["run"].finish(plan)


async def _technology(plan, result):
    value = result.value
    if not isinstance(value, data.NativeObject) or "CPdxPtr@VCDataTechnology" not in value.native_type_name:
        raise RuntimeError("requires a retained original native Technology list/getter element")
    origin = await _wrapper_origin(plan, value, 8)
    records = [r for r in plan["definitions"].records if not r.error and r.pointer == origin]
    if len(records) != 1 or records[0].key_error or not records[0].key:
        raise RuntimeError("retained Technology origin has no unique complete native definition key")
    return {"key":records[0].key, "origin":origin, "result":result,
        "binding":data.ObjectBinding(plan["types"]["Technology"], result.value_handle, retained=True)}


def _current_snapshot(result):
    """Decode a copied opaque current-wrapper ADDRESS only. Not identity/lease.

    AccessCurrentlyResearchedTechnology returns CDataTechnology*, not CPdxPtr.
    Its copied pointer bits may be recorded/compared, never dereferenced, rebound
    as Technology, or passed into a later callback. A retained Any owns only its
    eight-byte pointer payload, not the pointed-to wrapper. Current key/identity
    are unknown. Cache reuse can mean same address/different contents, or a
    different address for the same technology; address equality proves neither.
    """
    value = result.value
    if (not isinstance(result, data.DataResult) or result.kind != data.Kind.OBJECT
            or result.native_type_name != ".?AU?$SAnyType@PEAVCDataTechnology@@@PdxAnyImpl@@"
            or not isinstance(value, data.NativeObject) or not value.pointer
            or result.payload_size != 8 or result.snapshot_bytes != 8 or result.total != 8
            or len(result.snapshot) != 8 or not result.value_handle
            or struct.unpack("<Q", result.snapshot)[0] != value.pointer
            or result.object != value.pointer or result.raw_bits != value.pointer):
        raise RuntimeError("current getter raw Any shape/address copy unknown; preserve original, no retag or empty-state inference")
    return {"result":result, "opaque_current_wrapper_address":value.pointer,
        "copied_address_bytes":bytes(result.snapshot), "key":None, "key_known":False,
        "observation_kind":"opaque_current_wrapper_address_not_technology_identity_or_lease"}


async def _selection(plan, label):
    bindings = (_country(plan),)
    present = await _eval(plan, label+"_present", "Country.HasCurrentlyResearchedTechnology", bindings)
    if not isinstance(present.value, bool):
        raise RuntimeError("native current-selection presence is not bool; preserve original result")
    current = None
    if present.value:
        result = await _eval(plan, label+"_technology", "Country.AccessCurrentlyResearchedTechnology", bindings)
        current = _current_snapshot(result)  # copied address hint only; never _technology(raw pointer)
    selected = None
    target = plan.get("target")
    if target is not None:
        # The producer/selection receiver is the original owned list Technology,
        # NOT the borrowed current pointer or a fabricated type47/308 conversion.
        selected = await _eval(plan,label+"_owned_target_selected", "Technology.IsResearching(Country.Self)",
            (target["binding"],_country(plan)))
        if not isinstance(selected.value,bool):
            raise RuntimeError("original owned-target selection getter is not bool; preserve raw result")
    return {"present_result":present, "present":present.value, "technology":current,
            "opaque_current_wrapper_address":None if current is None else current["opaque_current_wrapper_address"],
            "current_technology_identity":None, "current_technology_identity_known":False,
            "key":None, "key_known":False, "target_selected_result":selected,
            "target_is_current":None if selected is None else selected.value}


async def _ordered_part(plan, expression, label, capacity):
    source = await _eval(plan, label+"_source", expression, (_country(plan),))
    if not isinstance(source.value, data.NativeList):
        raise RuntimeError("original queue getter did not return retained native Creation list")
    count = _ok(await _once(plan, label+"_count", lambda:data.list_count(
        plan["client"], source.value_handle, adapter=plan["data_adapter"], process=plan["process"])), label)
    if count.total > capacity:
        raise RuntimeError("full native queue part exceeds explicit capacity; no truncated-order/absence claim")
    entries = []
    for index in range(count.total):
        element = _ok(await _once(plan, label+"_element", lambda i=index:data.list_element(
            plan["client"], source.value_handle, i, adapter=plan["data_adapter"], process=plan["process"])), label)
        entries.append(await _technology(plan, element))
    end_count = _ok(await _once(plan, label+"_end_count", lambda:data.list_count(
        plan["client"], source.value_handle, adapter=plan["data_adapter"], process=plan["process"])), label)
    if end_count.total != count.total:
        raise RuntimeError("native queue part changed during read; no stable-order claim")
    return {"source":source, "count":count, "end_count":end_count, "entries":tuple(entries)}


async def _split_bound(plan,label):
    """Copy exact native Top/Bottom signed split scalar under the existing Access.

    58fd370 is proved by both native builders' RIP-relative loads, not a guessed
    define layout. Caller supplies this exact build's fresh image base. No live
    buffer/row/engine-object pointer escapes the call; only four copied bytes do.
    """
    if getattr(plan["run"], "retain_unknown", False) or any(
            plan["run"].owner_unsettled(owner) for owner in plan["run"].owners):
        raise RuntimeError("original research query/handoff remains unresolved")
    _world(plan)
    process = plan["process"]
    buffer = process.allocate(4)
    plan["run"].buffers.append(buffer)
    process.write(buffer,bytes(4))
    result = _ok(await _once(plan,label,lambda:v3_access.submit_call(
        plan["client"],process.resolve_export("ntdll.dll","memcpy"),
        (buffer,plan["image_base"]+0x58fd370,4),mode=1,flag=0,dispatch="interface",
        adapter=plan["access_adapter"],call_adapter=plan["call_adapter"],process=process)),label)
    if result.callee_result is None or result.callee_result.error or result.callee_result.rax != buffer:
        raise RuntimeError("native queue split-bound copy incomplete; retain original callback/buffer")
    raw = process.read(buffer,4)
    value = struct.unpack("<i",raw)[0]
    process.free(buffer)
    plan["run"].buffers.remove(buffer)
    return {"value":value,"raw":raw,"result":result}


async def observe_queue(plan, label="fresh_queue", *, capacity=1024):
    """Read ALL Top+Bottom entries in native order; never sort/deduplicate them.

    Both original views omit current selection by pointer. Their union is the
    complete planned queue, not a replacement for the current selected technology.
    Current observation is presence plus an opaque address hint and independent
    owned-target selection bool. It does not resolve current identity/key or
    certify unchanged current technology, raw-pointer lease or atomic state.
    Retained Data roots own wrapper caches/providers and retained child wrappers.
    Only copied definition identities leave Access; no live manager pointer does.
    """
    if not isinstance(capacity,int) or isinstance(capacity,bool) or capacity <= 0:
        raise ValueError("capacity must be a positive explicit full-part bound")
    split_before = await _split_bound(plan,label+"_split_before")
    start = await _selection(plan, label+"_current_before")
    top = await _ordered_part(plan, "Country.GetResearchQueueTop", label+"_top", capacity)
    bottom = await _ordered_part(plan, "Country.GetResearchQueueBottom", label+"_bottom", capacity)
    end = await _selection(plan, label+"_current_after")
    split_after = await _split_bound(plan,label+"_split_after")
    observation = dict(label=label, world=plan["world"], actor_id=plan["actor_id"], top=top, bottom=bottom,
        entries=top["entries"]+bottom["entries"], current_before=start, current_after=end,
        atomic=False, current_presence_and_target_bool_consistent=start["present"] == end["present"] and start["target_is_current"] == end["target_is_current"],
        opaque_current_wrapper_address_changed=start["opaque_current_wrapper_address"] != end["opaque_current_wrapper_address"],
        split_before=split_before,split_after=split_after,
        split_stable_positive=split_before["value"] > 0 and split_before["value"] == split_after["value"])
    observation["keys"] = tuple(row["key"] for row in observation["entries"])
    # Top appends until its positive native split bound; Bottom skips that bound.
    # A nonpositive/changed bound can overlap the two views. Keep both original
    # parts, but do not deduplicate/reconstruct or claim complete planned order.
    observation["split_overlap"] = tuple(row["key"] for row in top["entries"]
        if any(other["key"] == row["key"] for other in bottom["entries"]))
    bound = split_before["value"]
    observation["split_shape_valid"] = (top["count"].total <= bound and
        (not bottom["count"].total or top["count"].total == bound))
    observation["complete_planned_order"] = (not observation["split_overlap"] and
        observation["current_presence_and_target_bool_consistent"] and observation["split_stable_positive"] and observation["split_shape_valid"])
    plan["observations"].append(observation)
    if not observation["split_stable_positive"]:
        raise RuntimeError("native Top/Bottom split bound nonpositive/changed; original parts retained, no complete-order claim")
    if not observation["split_shape_valid"]:
        raise RuntimeError("native Top/Bottom counts disagree with copied split bound; no complete-order claim")
    if observation["split_overlap"]:
        raise RuntimeError("native Top/Bottom views overlap; original parts retained, no reconstructed order")
    if not observation["current_presence_and_target_bool_consistent"]:
        raise RuntimeError("current presence/owned-target selection bool changed during sample; no submission precheck")
    return observation


async def _prepare(plan, *, index, expected_key, kind, list_expression, with_unlocks=False):
    client, process, expected_world = plan["client"], plan["process"], plan["world"]
    data_adapter, commands_adapter, objects_adapter = (
        plan["data_adapter"], plan["commands_adapter"], plan["objects_adapter"])
    plan.update(kind=kind, expected_key=expected_key, with_unlocks=with_unlocks)
    await _types(plan)
    definitions = _ok(await _once(plan,"technology_definitions",lambda:
        objects.enumerate_technology_definitions(client,start_index=0,max_items=1024,text_capacity=65536,
        adapter=objects_adapter,process=process)),"technology_definitions")
    plan["definitions"] = definitions
    if definitions.truncated or definitions.world != expected_world:
        raise RuntimeError("native definitions incomplete/wrong world; no guessed key/ordinal")
    _, binding = await _element(plan,list_expression,index,"Technology")
    target = await _technology(plan,plan["selected"])
    if target["key"] != expected_key:
        raise RuntimeError("native Technology origin/key differs from explicit intent; no alternative selected")
    plan["target"], plan["technology_key"] = target, target["key"]
    plan["before"] = await observe_queue(plan,"before_prepare")
    # The producer only generates shared command storage. It is not submission.
    expression = _producer_expression(kind,with_unlocks)
    generated = await _eval(plan,"native_queue_command_producer",expression,
        (binding,_country(plan)))
    if not isinstance(generated.value,data.NativeDataCommand):
        raise RuntimeError("original Technology producer did not return shared native command")
    carrier_result = _ok(await _once(plan,"command_carrier",lambda:
        data.command_value(client,generated.value_handle,adapter=data_adapter,process=process)),"command_carrier")
    carrier = carrier_result.value
    plan["carrier"],plan["clone_source_pointer"] = carrier,carrier.primary_pointer
    if carrier.generation_world != expected_world:
        raise RuntimeError("original producer world differs; no clone or later world stamping")
    cloned = _ok(await _once(plan,"clone_original_queue_command",lambda:
        commands.request(client,commands.Action.CLONE,command=carrier.primary_pointer,
        world=carrier.generation_world,capacity=8,adapter=commands_adapter,process=process)),"clone")
    owner = adopt_completed_clone(plan,cloned)
    token = 23339 if kind == "enqueue" else 23340
    if owner.type_token != token or owner.world != expected_world:
        raise RuntimeError("original clone type/world differs from queue intent")
    serialized = _ok(await _once(plan,"native_queue_serialize",owner.serialize,owner=owner),"serialize")
    plan["serialized"],plan["command_wire"] = serialized,bytes(serialized.data)
    tree = commands.decode_command(serialized.data)
    plan["tree"] = tree
    _check_schema(plan,tree)
    validation = await _once(plan,"native_queue_validate",lambda:owner.validate(capacity=65536),owner=owner)
    plan["validation"] = validation
    if validation.error not in (0,122):
        raise RuntimeError("original Validate callback error; preserve exact result/owner")
    plan["status"] = "prepared_not_submitted" if validation.validated else "native_validation_rejected_no_submit"
    return plan


def _producer_expression(kind,with_unlocks):
    if kind == "dequeue":
        return "Technology.RemoveFromQueue(Country.Self)"
    return "Technology.AddToQueueWithUnlocks(Country.Self)" if with_unlocks else "Technology.AddToQueue(Country.Self)"


def _check_schema(plan,tree):
    token = 23339 if plan["kind"] == "enqueue" else 23340
    if commands.encode_tree(tree) != plan["command_wire"] or tree.key != commands.Token(token):
        raise RuntimeError("native queue wire type/roundtrip differs from original intent")
    def one(block,token):
        values = [v.value for v in block if isinstance(v,commands.Assignment) and v.key == commands.Token(token)]
        if len(values) != 1:
            raise RuntimeError("native queue field absent/duplicate; preserve original tree")
        return values[0]
    if one(tree.value,10304) != commands.UInt32(plan["actor_id"]):
        raise RuntimeError("native queue actor differs from complete Country ID")
    parameters = one(tree.value,65)
    if not isinstance(parameters,tuple) or one(parameters,12713) != plan["technology_key"]:
        raise RuntimeError("native queue Technology key differs from proved natural identity")
    if plan["kind"] == "enqueue" and (type(one(parameters,23341)) is not bool
            or one(parameters,23341) != plan["with_unlocks"]):
        raise RuntimeError("native WithUnlocks flag differs from explicit enqueue intent")
    if plan["kind"] == "dequeue" and any(isinstance(v,commands.Assignment) and
            v.key == commands.Token(23341) for v in parameters):
        raise RuntimeError("native dequeue unexpectedly has an enqueue WithUnlocks field")


async def prepare_enqueue(plan, *, index, expected_key, with_unlocks=False,
                          list_expression="Country.AccessResearchableTechnologiesProduction"):
    """Prepare ONLY. Native enqueue may update selection when later submitted.

    with_unlocks=True is a separate explicit native recursive-prerequisite intent,
    NOT front insertion. Plain enqueue can hold an unselectable real technology;
    original Validate rejects researched/duplicate, not every researchability rule.
    """
    if not isinstance(with_unlocks,bool):
        raise TypeError("with_unlocks must be an explicit native bool")
    return await _prepare(plan, index=index, expected_key=expected_key, kind="enqueue",
        with_unlocks=with_unlocks, list_expression=list_expression)


async def prepare_dequeue(plan, *, index, expected_key, list_expression="Country.GetResearchQueueTop"):
    """Prepare ONLY one original dequeue from an explicit natural source.

    Original removal can also prune dependent queued technologies and select a
    replacement current research. It is not guaranteed to remove only one item.
    """
    return await _prepare(plan, index=index, expected_key=expected_key, kind="dequeue",
        list_expression=list_expression)


async def submit_prepared(plan,*,enqueue=False,dequeue=False):
    """Exactly one explicit independent queue intent. Neither is an inverse cleanup."""
    if ((enqueue,dequeue) != ((True,False) if plan["kind"] == "enqueue" else (False,True))
            or type(enqueue) is not bool or type(dequeue) is not bool
            or plan["status"] != "prepared_not_submitted" or plan["submit_count"]):
        raise ValueError("requires exactly the matching enqueue=True OR dequeue=True for a fresh prepared owner")
    before = await observe_queue(plan,"explicit_queue_submit_precheck")
    if before["keys"] != plan["before"]["keys"] or before["current_after"]["present"] != plan["before"]["current_after"]["present"] or before["current_after"]["target_is_current"] != plan["before"]["current_after"]["target_is_current"]:
        plan["status"] = "queue_or_current_selection_changed_unsubmitted_owner_retained"
        return None
    plan["submit_before"] = before
    owner = plan["owner"]
    plan["submit_count"],plan["status"] = 1,"native_queue_submit_attempted"
    result = await _once(plan,"submit_queue_once",lambda:owner.submit(flags=4),owner=owner)
    plan["submission"] = result
    plan["native_result"] = result
    plan["native_error"] = result.error
    native_outcome = getattr(getattr(owner, "attempt", None), "outcome", None)
    plan["native_outcome"] = native_outcome
    if native_outcome != "consumed":
        plan["status"] = "native_result_returned_ownership_" + (native_outcome or "unknown")
    elif result.error:
        plan["status"] = "native_result_returned_native_error"
    else:
        plan["status"] = "accepted_not_independently_observed" if result.accepted else "receiver_rejected_consumed"
    return result


async def observe_result(plan):
    """Fresh complete ordered readback, separate from accepted callback and current selection."""
    submitted = plan.get("submission")
    if (plan["submit_count"] != 1 or submitted is None or submitted.error or not submitted.accepted
            or plan["run"].owner_unsettled(plan["owner"])):
        raise RuntimeError("requires the original known accepted native submission")
    after = await observe_queue(plan,"independent_ordered_queue_after")
    plan["after"] = after
    plan["queue_changed"] = after["keys"] != plan["submit_before"]["keys"]
    plan["opaque_current_wrapper_address_changed"] = after["current_after"]["opaque_current_wrapper_address"] != plan["submit_before"]["current_after"]["opaque_current_wrapper_address"]
    target = plan["technology_key"]
    plan["target_planned_count_before"] = plan["submit_before"]["keys"].count(target)
    plan["target_planned_count_after"] = after["keys"].count(target)
    plan["target_is_current_before"] = plan["submit_before"]["current_after"]["target_is_current"]
    plan["target_is_current_after"] = after["current_after"]["target_is_current"]
    if type(plan["target_is_current_before"]) is not bool or type(plan["target_is_current_after"]) is not bool:
        raise RuntimeError("owned-target original selection before/after unknown; no selection inference from raw current pointer")
    target_changed = plan["target_is_current_before"] != plan["target_is_current_after"]
    plan["target_selection_changed"] = target_changed
    plan["current_selection_changed"] = True if target_changed else None  # complete current identity stays unknown
    plan["current_technology_identity_known"] = False
    plan["target_became_current_observed"] = not plan["target_is_current_before"] and plan["target_is_current_after"]
    plan["status"] = ("fresh_planned_queue_or_target_selection_changed" if plan["queue_changed"] or
        target_changed else "accepted_no_planned_or_target_selection_change_observed_current_identity_unknown")
    return after
