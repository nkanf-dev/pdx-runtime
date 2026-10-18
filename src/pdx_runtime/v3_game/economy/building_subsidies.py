"""Native Building subsidy observations and explicit policy changes."""
from __future__ import annotations

import asyncio
from pdx_runtime.v3_game._values import fixed_decimal
from pdx_runtime.v3_game.country import _plain_name
from pdx_runtime import v3_data as data, v3_commands as commands, v3_player_control as player
from pdx_runtime.v3_game._run import ACTIVE_CONTEXT
from pdx_runtime.v3_game._run import command_field, rejected

_PRODUCERS = {True:"ToggleSubsidiesOn", False:"ToggleSubsidiesOff"}
# Existing building-policy native-chain evidence: mode1 sets true, mode2 false;
# the original serializer maps those modes to20254/20255. We only CHECK the
# native producer's original wire; no host-created mode or command is written.
_EXPLICIT_MODES = {True:20254, False:20255}
_SCOPE = "Effective subsidy state includes native Country/type rules; disabling may affect the Country's per-type override"
_ESTIMATE_REASON = "No verified native prospective subsidy-cost getter; current expenditure is not a forecast"


def _full_id(value):
    if type(value) is not int or not 0 <= value < 0xffffffff:
        raise ValueError("building_id must be a complete game Building ID")
    return value


def _settled(plan):
    return (plan["pending"] is None and not plan["buffers"] and not plan.get("uncertain_operations")
        and not plan.get("unadopted_clones")
        and not any(attempt.get("handoff_uncertain") for attempt in plan.get("consumptions",()))
        and all(owner.pending is None and getattr(getattr(owner,"attempt",None),"outcome",None)
                not in ("pending","unknown") for owner in plan["owners"]))


async def _player(owner_api, plan):
    observed=await owner_api._once(plan,"subsidy_current_player",lambda:player.observe_player(
        plan["client"],adapter=plan["console_adapter"],process=plan["process"]))
    owner_api._ok(observed,"subsidy_current_player")
    plan.setdefault("player_observations",[]).append(observed)
    if (observed.world!=plan["world"] or observed.controlled_country_error or observed.observer_enabled
            or observed.controlled_country_id!=plan["actor_id"] or not observed.controlled_country
            or not observed.simulation_started):
        raise RuntimeError("This country is not the current controlled actor in the started world")
    # Paused is deliberately NOT required. The explicit On/Off command remains
    # an idempotent native intent even if effective policy changes across ticks.
    return observed


async def _metadata(ctx, owner_api, plan):
    await owner_api._types(plan)
    if not all(name in plan["types"] for name in ("Building","Command","bool")):
        raise RuntimeError("Current native Building/Command/bool types are unavailable")
    plan["building_binding"]=data.ObjectBinding.building(plan["types"]["Building"],plan["building_full_id"])
    rows={};cursor=0
    while True:
        page=await owner_api._once(plan,"subsidy_building_metadata",lambda cursor=cursor:data.enumerate_functions(
            ctx.native,plan["types"]["Building"],start=cursor,capacity=256,
            adapter=plan["data_adapter"],process=ctx.process))
        owner_api._ok(page,"subsidy_building_metadata")
        for row in page.rows:rows[row.name]=row
        if page.next>=page.total:break
        if page.next<=cursor:raise RuntimeError("Native Building metadata did not advance")
        cursor=page.next
    plan["function_metadata"]=rows
    for name,kind in (("IsSubsidized","bool"),):
        if not _has(plan,name,kind):raise RuntimeError("Native Building."+name+" signature is unavailable")


def _has(plan,name,kind):
    row=plan["function_metadata"].get(name)
    return bool(row is not None and row.argument_count==0
        and row.return_type_id==plan["types"].get(kind)
        and any(row.callbacks[:2]) and not any(row.callbacks[2:]))


async def _current(policy, owner_api, plan, label):
    return await policy.read_state(owner_api,plan,label)


async def _cost(owner_api,plan):
    if not _has(plan,"GetSubsidies","CFixedPoint"):
        return None,"Native Building.GetSubsidies fixed-point getter is unavailable"
    result=await owner_api._once(plan,"subsidy_current_expenditure",lambda:data.evaluate(
        plan["client"],"Building.GetSubsidies",(plan["building_binding"],),output_bytes=8,
        adapter=plan["data_adapter"],process=plan["process"]))
    if result.error:return None,"Native subsidy-expenditure error "+str(result.error)
    if result.kind!=data.Kind.FIXED or not isinstance(result.value,data.FixedPoint):
        return None,"Native subsidy expenditure did not return a fixed-point amount"
    # Native SUBSIDIZED_YES_HEADER explicitly displays GetSubsidies per week.
    # Keep its original sign; this current expenditure is NOT an On forecast.
    return fixed_decimal(result.value),None


async def _prepare(ctx,policy,owner_api,plan,enabled):
    producer=_PRODUCERS[enabled]
    if not _has(plan,producer,"Command"):
        return None,None,"Native Building."+producer+" is unavailable"
    generated=await owner_api._eval(plan,"subsidy_generate_"+str(enabled),"Building."+producer,
        (plan["building_binding"],))
    if not isinstance(generated.value,data.NativeDataCommand):
        raise RuntimeError("Native subsidy producer did not publish a shared command")
    extracted=await owner_api._once(plan,"subsidy_command_carrier",lambda:data.command_value(
        ctx.native,generated.value_handle,adapter=plan["data_adapter"],process=ctx.process))
    owner_api._ok(extracted,"subsidy_command_carrier")
    carrier=extracted.value
    if (not isinstance(carrier,data.NativeDataCommand) or carrier.generation_world!=plan["world"]
            or not carrier.primary_pointer or not carrier.shared_control
            or carrier.game_command_pointer!=carrier.primary_pointer+0x18):
        raise RuntimeError("Native subsidy command source world/ownership is unavailable")
    plan["clone_source_pointer"]=carrier.primary_pointer
    cloned=await owner_api._once(plan,"subsidy_unique_clone",lambda:commands.request(ctx.native,
        commands.Action.CLONE,command=carrier.primary_pointer,world=carrier.generation_world,capacity=8,
        adapter=plan["commands_adapter"],process=ctx.process))
    plan["clone_result"]=cloned
    owner_api._ok(cloned,"subsidy_unique_clone")
    owner=owner_api.adopt_completed_clone(plan,cloned)
    plan["owner"]=owner
    token=policy.POLICIES["subsidies"][2]
    if owner.type_token!=token or owner.world!=plan["world"]:
        raise RuntimeError("Native subsidy command type/world differs")
    serialized=await owner_api._once(plan,"subsidy_serialize",owner.serialize,owner=owner)
    owner_api._ok(serialized,"subsidy_serialize")
    tree=commands.decode_command(serialized.data)
    plan["serialized"],plan["command_wire"],plan["tree"]=serialized,bytes(serialized.data),tree
    if (tree.key!=commands.Token(token) or commands.encode_tree(tree)!=serialized.data
            or command_field(tree,10304)!=commands.UInt32(plan["actor_id"])
            or command_field(tree,10232)!=(commands.UInt32(plan["building_full_id"]),)
            or command_field(tree,1012)!=commands.Token(_EXPLICIT_MODES[enabled])):
        raise RuntimeError("Native subsidy actor/Building/explicit On-Off wire differs")
    allowed=await policy._validate(owner_api,plan,"subsidy_native_validate")
    validation=plan["validation"]
    return owner,allowed,_plain_name(validation.reason) if validation.reason else None


async def _dispose(policy,owner_api,plan):
    # Cleanup is supplementary evidence, not a replacement for native results.
    try:
        await policy.cleanup_known(owner_api,plan)
    except Exception as error:
        cleanup=plan.setdefault("cleanup",{})
        cleanup.setdefault("errors",[]).append(error)
        cleanup.update(status="cleanup_error",pending=plan.get("pending"),
            unreleased_values=tuple(plan["values"]))
        plan["safe_to_discard"]=False
    return plan.get("cleanup",{})


def _answer(retained,plan,answer):
    plan["public_result"]=answer
    cleanup=plan.get("cleanup")
    if cleanup and cleanup.get("status")!="complete":
        answer["cleanup"]=cleanup
        answer["cleanup_warning"]="Native results remain available; original storage is retained until cleanup is complete"
    if plan.get("safe_to_discard",False) and _settled(plan):
        retained[:]=[prior for prior in retained if prior is not plan]
    return answer


def _retained_original(ctx,building_id,enabled):
    for prior in getattr(ctx,"_building_subsidy_runs",()):
        if (prior["world"]==ctx.world and prior["building_full_id"]==building_id
                and (not prior.get("safe_to_discard",False) or not _settled(prior))):
            answer={"building_id":building_id,"country_id":prior["actor_id"],
                "status":"original_handoff_retained","submitted":None,"accepted":None,
                "submission_attempted":bool(prior.get("submit_count")),
                "current":None,"effect_observed":None,"requested":enabled,
                "original_requested":prior.get("requested_enabled"),"scope":_SCOPE,
                "original_result":prior.get("public_result"),
                "reason":"Original subsidy operation is unresolved; its query, owner and native results remain retained without repeating or reversing it"}
            submitted=prior.get("submission")
            if submitted is not None:
                answer.update(native_result=submitted,native_error=submitted.error,
                    native_outcome=(prior.get("public_result") or {}).get("native_outcome"))
            if prior.get("cleanup"):answer["cleanup"]=prior["cleanup"]
            return answer
    return None


async def _execute(ctx,building_id,enabled):
    original=_retained_original(ctx,building_id,enabled)
    if original is not None:return original
    await ctx.refresh()
    original=_retained_original(ctx,building_id,enabled)
    if original is not None:return original
    actor=await ctx.country_id()
    from . import building_policy as policy, command_plans as owner_api
    plan=owner_api._new(ctx.native,ctx.process,expected_world=ctx.world,actor_id=actor,
        data_adapter=ctx.adapters["data"],commands_adapter=ctx.adapters["commands"],
        objects_adapter=ctx.adapters["objects"],access_adapter=ctx.adapters["access"],call_adapter=ctx.adapters["call"])
    plan.update(kind="building_subsidy",policy="subsidies",building_full_id=building_id,
        console_adapter=ctx.adapters["console"],requested_enabled=enabled)
    retained=getattr(ctx,"_building_subsidy_runs",None)
    if retained is None:retained=[];ctx._building_subsidy_runs=retained
    retained.append(plan)
    ctx.diagnostics.append({"operation":"building_subsidy","plan":plan})
    answer={"building_id":building_id,"country_id":actor,"current":None,"scope":_SCOPE,
        "atomic_snapshot":False}
    plan["public_result"]=answer
    try:
        await _player(owner_api,plan)
        await _metadata(ctx,owner_api,plan)
        current=await _current(policy,owner_api,plan,"subsidy_before")
        plan["before_enabled"]=current
        answer["current"]=current
        if enabled is None:
            answer.update(status="observed",can_enable=None,can_disable=None,enable_reason=None,disable_reason=None,
                current_cost=None,current_cost_unit="pounds/week",current_cost_period="week",
                current_cost_period_reason=None,
                estimated_cost=None,estimate_reason=_ESTIMATE_REASON)
            answer["current_cost"],answer["current_cost_reason"]=await _cost(owner_api,plan)
            for desired,key in ((True,"enable"),(False,"disable")):
                owner,allowed,reason=await _prepare(ctx,policy,owner_api,plan,desired)
                answer["can_"+key]=allowed;answer[key+"_reason"]=reason
                if owner is not None:answer[key+"_validation"]=plan["validation"]
                # Finish only known cleanup before another candidate. An
                # unresolved cleanup must not hide the eligibility already read.
                if owner is not None:
                    await _dispose(policy,owner_api,plan)
                    if not plan.get("safe_to_discard",False) or not _settled(plan):
                        if desired is True:
                            answer["disable_reason"]="Not sampled after unresolved native cleanup; original storage remains retained"
                        break
            answer["snapshot"]="separate fresh native observations; effective policy can change while running"
        else:
            answer.update(requested=enabled,before=current,submitted=False,accepted=None,effect_observed=False)
            if current==enabled:
                answer.update(status="unchanged",submitted=False,accepted=None,changed=False,effect_observed=True,reason=None)
            else:
                owner,allowed,reason=await _prepare(ctx,policy,owner_api,plan,enabled)
                if allowed is not True:
                    answer.update(status="rejected" if allowed is False else "unavailable",reason=reason or "Native subsidy rules reject this change")
                else:
                    await _player(owner_api,plan)
                    before_submit=await _current(policy,owner_api,plan,"subsidy_submit_precheck")
                    if before_submit==enabled:
                        answer.update(status="unchanged",submitted=False,accepted=None,current=before_submit,changed=False,effect_observed=True,reason=None)
                    elif not await policy._validate(owner_api,plan,"subsidy_fresh_native_validate"):
                        answer.update(status="rejected",reason=_plain_name(plan["validation"].reason) or "Native subsidy rules reject this change")
                    else:
                        plan["submit_count"]=1;plan["status"]="subsidy_submission_attempted"
                        submitted=await owner_api._once(plan,"subsidy_submit_once",lambda:owner.submit(flags=4),
                            owner=owner,consume_action=commands.Action.SUBMIT)
                        plan["submission"]=submitted
                        consumption=plan["consumptions"][-1]
                        answer.update(native_result=submitted,native_error=submitted.error,
                            native_outcome=consumption.get("native_outcome"),submission_attempted=True)
                        if (submitted.error or consumption["handoff_uncertain"]
                                or consumption["outcome"] not in ("accepted","receiver_rejected_consumed")):
                            answer.update(status="native_result",submitted=None,accepted=None,
                                current=None,effect_observed=None,
                                reason="Native command result returned; handoff is not established, retained without replay")
                        elif not submitted.accepted:
                            answer.update(status="rejected",submitted=True,accepted=False,reason="Game receiver rejected the subsidy change")
                        else:
                            answer.update(status="accepted",submitted=True,accepted=True,current=None,reason=None)
                            plan["accepted_answer"]=answer
                            after=await _current(policy,owner_api,plan,"subsidy_fresh_after")
                            answer.update(current=after,effect_observed=after==enabled,
                                status="observed" if after==enabled else "accepted",changed=after!=current)
        await _dispose(policy,owner_api,plan)
        return _answer(retained,plan,answer)
    except Exception as error:
        if _settled(plan):
            await _dispose(policy,owner_api,plan)
            if answer.get("accepted"):
                answer["observation_reason"]=str(error)
            else:
                answer.update(status="unavailable",reason=str(error))
                if enabled is not None:
                    answer["submitted"]=False;answer["accepted"]=None
            return _answer(retained,plan,answer)
        plan["error"]=error  # Original query, owners and source graph remain held.
        completed=next((attempt for attempt in reversed(plan["consumptions"])
            if attempt["action"]==commands.Action.SUBMIT and attempt.get("completed_result") is not None),None)
        if completed is not None:
            submitted=completed["completed_result"]
            plan["submission"]=submitted
            answer.update(status="native_result",submitted=None,accepted=None,
                submission_attempted=True,current=None,effect_observed=None,
                native_result=submitted,native_error=submitted.error,
                native_outcome=completed.get("native_outcome"),error=error)
            await _dispose(policy,owner_api,plan)
            return _answer(retained,plan,answer)
        if answer.get("status")=="observed":
            answer.update(partial=True,error=error)
            await _dispose(policy,owner_api,plan)
            return _answer(retained,plan,answer)
        raise


async def _operation(ctx,building_id,enabled):
    _full_id(building_id)
    original=_retained_original(ctx,building_id,enabled)
    if original is not None:return original
    lock=getattr(ctx,"_building_subsidy_lock",None)
    if lock is None:lock=asyncio.Lock();ctx._building_subsidy_lock=lock
    async with lock:
        # Also support direct helper calls: use the same context's original
        # query/owner settlement bridge, never another client or wait transport.
        token=ACTIVE_CONTEXT.set(ctx)
        try:return await _execute(ctx,building_id,enabled)
        finally:ACTIVE_CONTEXT.reset(token)


async def read_subsidy(ctx,building_id):
    """Current effective state, original On/Off eligibility, and current cost.

    This read can generate/clone/validate native command candidates, but never
    submits any business command or pauses time. Prospective cost stays unknown.
    """
    return await _operation(ctx,building_id,None)


async def set_subsidy(ctx,building_id,enabled=True):
    """Request explicit native subsidy On/Off once and observe the fresh state."""
    if type(enabled) is not bool:raise ValueError("enabled must be True or False")
    return await _operation(ctx,building_id,enabled)
