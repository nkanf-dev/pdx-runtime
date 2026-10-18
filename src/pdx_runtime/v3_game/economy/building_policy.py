"""Native Building policy preparation, submission and independent readback."""
from __future__ import annotations
from pdx_runtime import v3_data as data, v3_objects as objects, v3_commands as commands
from pdx_runtime import v3_player_control as player
from pdx_runtime.v3_game._run import command_field, rejected

BUILDING_POLICY_PLANS = []
POLICIES = {
    "subsidies": ("ToggleSubsidies", "IsSubsidized", 20250),
    "autoexpand": ("ToggleAutoExpand", "IsAutoExpanding", 22799),
}


def _full_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 0xffffffff:
        raise ValueError("requires an actual complete native uint32 full ID; zero is allowed")
    return value


def _ready(plan):
    if plan["pending"] is not None or plan.get("uncertain_operations"):
        raise RuntimeError("original callback/handoff unresolved; retain it without replay or readback")


async def _player(owner_api, plan, label):
    result = owner_api._ok(await owner_api._once(plan, label, lambda:player.observe_player(
        plan["client"], adapter=plan["console_adapter"], process=plan["process"])), label)
    if (result.world != plan["world"] or result.controlled_country_error or result.observer_enabled
            or result.controlled_country_id != plan["actor_id"] or not result.controlled_country
            or not result.paused or not result.simulation_started):
        raise RuntimeError("requires this current nonobserver controlled actor in the same paused started world")
    plan.setdefault("players", []).append(result)
    return result


async def _identity(owner_api, plan, label):
    """Copy full IDs through original resolvers, never reuse an engine pointer."""
    client, process = plan["client"], plan["process"]
    building = owner_api._ok(await owner_api._once(plan, label+"_building", lambda:objects.read_ids(
        client, objects.Domain.BUILDING, (plan["building_full_id"],),
        adapter=plan["objects_adapter"], process=process)), label+"_building")
    if (building.world != plan["world"] or len(building.records) != 1
            or building.records[0].error
            or building.records[0].requested_id != plan["building_full_id"]
            or building.records[0].resolved_id != plan["building_full_id"]):
        raise RuntimeError("explicit original Building full ID is unavailable in this world")
    row = building.records[0]
    state_id = _full_id(row.state_id)
    state = owner_api._ok(await owner_api._once(plan, label+"_state", lambda:objects.read_ids(
        client, objects.Domain.STATE, (state_id,), adapter=plan["objects_adapter"],
        process=process)), label+"_state")
    if (state.world != plan["world"] or len(state.records) != 1 or state.records[0].error
            or state.records[0].requested_id != state_id or state.records[0].resolved_id != state_id
            or state.records[0].owner_id != plan["actor_id"]):
        raise RuntimeError("fresh Building -> State.owner_id differs from intended actor; no permission inference")
    # Keep IDs for later consumption, not a borrowed pointer identity. The
    # next original full-ID binding resolves its current State/type in Access.
    signature = (row.resolved_id, state_id)
    plan.setdefault("identity_observations", []).append({"label":label, "building":building, "state":state})
    if "identity" in plan and plan["identity"] != signature:
        raise RuntimeError("Building State identity changed; do not substitute another native target")
    plan["identity"] = signature
    return signature


async def read_state(owner_api, plan, label="fresh_building_policy_bool"):
    """A new native evaluate, not a snapshot of an earlier retained Bool.

    IsSubsidized is the engine's effective subsidy state, including its original
    Country/type rules. Original ToggleSubsidies may also clear that Country's
    per-type override when turning subsidies off. Do not infer that other
    Buildings of that type are unchanged. IsAutoExpanding is the original
    enabled flag, not proof that a construction order or level has appeared.
    Separate resolver/evaluate callbacks are fresh checks, not an atomic freeze.
    """
    _ready(plan)
    await _identity(owner_api, plan, label)
    result = await owner_api._eval(plan, label, "Building."+POLICIES[plan["policy"]][1],
        (plan["building_binding"],))
    if result.kind != data.Kind.BOOL or not isinstance(result.value, bool):
        raise RuntimeError("original Building observer did not return native Bool; no truthy coercion")
    plan.setdefault("bool_observations", []).append({"label":label, "result":result, "enabled":result.value})
    return result.value


async def _metadata(owner_api, plan):
    await owner_api._types(plan)
    if not all(name in plan["types"] for name in ("Building", "Command", "bool")):
        raise RuntimeError("current native Building/Command/bool type metadata is unavailable")
    result = owner_api._ok(await owner_api._once(plan, "current_building_policy_metadata",
        lambda:data.enumerate_functions(plan["client"], plan["types"]["Building"], capacity=1400,
        adapter=plan["data_adapter"], process=plan["process"])), "current_building_policy_metadata")
    plan["building_metadata"] = result
    if result.next < result.total:
        raise RuntimeError("explicit Building metadata page incomplete; no hidden metadata retry")
    producer, observer, _ = POLICIES[plan["policy"]]
    for name, kind in ((producer, "Command"), (observer, "bool")):
        matches = [row for row in result.rows if row.name == name]
        if (len(matches) != 1 or matches[0].argument_count != 0
                or matches[0].return_type_id != plan["types"][kind]):
            raise RuntimeError("current original Building policy producer/observer signature differs")
    plan["building_binding"] = data.ObjectBinding.building(plan["types"]["Building"], plan["building_full_id"])


async def _validate(owner_api, plan, label):
    owner = plan["owner"]
    result = await owner_api._once(plan, label, lambda:owner.validate(capacity=65536), owner=owner)
    plan["validation"] = result
    attempt = owner.attempt
    truncated = (result.error == 122 and result.buffer_size > result.buffer_capacity
                 and attempt is not None and attempt.outcome == "owned"
                 and attempt.result is result and attempt.query is not None
                 and getattr(attempt.query, "_result", None) is result)
    expected_type = 0 if truncated else POLICIES[plan["policy"]][2]
    if ((result.error and not truncated) or result.action != commands.Action.VALIDATE
            or result.command != owner.command or result.type_token != expected_type
            or result.world != plan["world"] or result.validated not in (0, 1)):
        raise RuntimeError("original Building policy validation receipt differs; inspect exact result")
    return bool(result.validated)


async def prepare_toggle(owner_api, client, process, *, expected_world, actor_id,
        building_full_id, policy, data_adapter="pdx_v3_data.dll",
        objects_adapter="pdx_v3_objects.dll", commands_adapter="pdx_v3_commands.dll",
        console_adapter="pdx_v3_console.dll", access_adapter="pdx_v3_access.dll",
        call_adapter="pdx_native_call.dll"):
    """Generate/clone/serialize/native-validate one original command; no submit.

    Choose policy="subsidies" OR policy="autoexpand" for this plan. To request
    the other policy, create a separate plan after inspecting fresh current state.
    The original producer chooses its actor; exact Serialize must match actor_id.
    """
    if policy not in POLICIES:
        raise ValueError("choose subsidies or autoexpand for this one independent intent")
    _full_id(actor_id); _full_id(building_full_id)
    if (isinstance(expected_world, bool) or not isinstance(expected_world, int)
            or not 0 < expected_world <= 0xffffffffffffffff):
        raise ValueError("requires a concrete original current world, never zero/later stamping")
    plan = owner_api._new(client, process, expected_world=expected_world, actor_id=actor_id,
        data_adapter=data_adapter, commands_adapter=commands_adapter, objects_adapter=objects_adapter,
        access_adapter=access_adapter, call_adapter=call_adapter)
    plan.update(kind="building_policy", policy=policy, building_full_id=building_full_id,
        console_adapter=console_adapter, generation_expression="Building."+POLICIES[policy][0])
    BUILDING_POLICY_PLANS.append(plan)
    await _player(owner_api, plan, "player_before_building_policy_prepare")
    await _metadata(owner_api, plan)
    plan["before_enabled"] = await read_state(owner_api, plan, "before_building_policy_toggle")
    plan["expected_after_enabled"] = not plan["before_enabled"]
    generated = await owner_api._eval(plan, "generate_building_policy_only", plan["generation_expression"],
        (plan["building_binding"],))
    plan["producer_result"] = generated
    if not isinstance(generated.value, data.NativeDataCommand):
        raise RuntimeError("original Building producer did not publish shared CDataCommand")
    carrier_result = owner_api._ok(await owner_api._once(plan, "building_policy_native_carrier",
        lambda:data.command_value(client, generated.value_handle, adapter=data_adapter,
        process=process)), "building_policy_native_carrier")
    plan["carrier_result"] = carrier_result
    carrier = carrier_result.value
    if carrier.generation_world != plan["world"]:
        raise RuntimeError("original shared command source world differs; no clone or world restamping")
    plan["clone_source_pointer"] = carrier.primary_pointer
    cloned = await owner_api._once(plan, "building_policy_unique_clone", lambda:commands.request(
        client, commands.Action.CLONE, command=carrier.primary_pointer, world=carrier.generation_world,
        capacity=8, adapter=commands_adapter, process=process))
    plan["clone_result"] = cloned
    owner_api._ok(cloned, "building_policy_unique_clone")
    owner = owner_api.adopt_completed_clone(plan, cloned)
    token = POLICIES[policy][2]
    if owner.type_token != token:
        raise RuntimeError("original Building producer returned a different command type")
    serialized = owner_api._ok(await owner_api._once(plan, "serialize_building_policy",
        owner.serialize, owner=owner), "serialize_building_policy")
    plan["serialized"], plan["command_wire"] = serialized, bytes(serialized.data)
    tree = commands.decode_command(serialized.data)
    plan["tree"] = tree
    if (serialized.action != commands.Action.SERIALIZE or serialized.command != owner.command
            or serialized.world != plan["world"] or serialized.type_token != token
            or tree.key != commands.Token(token) or commands.encode_tree(tree) != serialized.data
            or command_field(tree, 10304) != commands.UInt32(actor_id)
            or command_field(tree, 10232) != (commands.UInt32(building_full_id),)
            or command_field(tree, 1012) != commands.Token(10760)):
        raise RuntimeError("native actor/single-Building UInt32 vector/toggle mode/wire differs from intent")
    plan["status"] = ("prepared_not_submitted" if await _validate(owner_api, plan, "validate_building_policy")
                      else "native_building_policy_validator_rejected_no_submit")
    return plan


async def submit_prepared(owner_api, plan, *, toggle=False):
    """A separate explicit one-toggle Session intent. No idempotent Set claim."""
    _ready(plan)
    if toggle is not True or plan["status"] != "prepared_not_submitted" or plan["submit_count"]:
        raise ValueError("requires toggle=True and this new validated unsubmitted policy intent")
    await _player(owner_api, plan, "player_before_building_policy_submit")
    if await read_state(owner_api, plan, "explicit_building_policy_precheck") != plan["before_enabled"]:
        plan["status"] = "policy_bool_changed_unsubmitted_owner_retained"
        return None
    if not await _validate(owner_api, plan, "fresh_native_building_policy_validate"):
        plan["status"] = "fresh_native_building_policy_validator_rejected_no_submit"
        return None
    owner = plan["owner"]
    plan["submit_count"] = 1
    plan["status"] = "native_building_policy_toggle_attempted"
    result = await owner_api._once(plan, "submit_building_policy_once", lambda:owner.submit(flags=4),
        owner=owner, consume_action=commands.Action.SUBMIT)
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


async def observe_result(owner_api, plan):
    """Independent fresh Bool for the same identity; accepted is not an effect."""
    _ready(plan)
    if (plan["submit_count"] != 1 or not plan["consumptions"]
            or plan["consumptions"][-1]["outcome"] != "accepted"):
        raise RuntimeError("requires exact known acceptance; no readback on unknown/rejected handoff")
    await _player(owner_api, plan, "player_before_building_policy_readback")
    after = await read_state(owner_api, plan, "independent_after_building_policy_toggle")
    plan["after_enabled"] = after
    plan["bool_flip_observed"] = after == plan["expected_after_enabled"]
    plan["status"] = ("accepted_and_fresh_bool_flip_observed" if plan["bool_flip_observed"]
                      else "accepted_fresh_bool_did_not_flip")
    return after


async def cleanup_known(owner_api, plan):
    """Explicit known storage cleanup only. Never toggle back as compensation."""
    return await owner_api.cleanup_known(plan)
