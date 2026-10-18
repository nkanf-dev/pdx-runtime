"""Ordinary generic-command diplomatic composition. No launch/load/UI/IDA/save.

Offline Schema/ownership protocol only until a fresh game run is recorded.
Caller supplies fresh world, independently identified parties and explicit intent.
Proposal decline and pact break are different intents, never rollback/cleanup.
"""
from __future__ import annotations
from pdx_runtime.v3_wire import V3Wire
from .._run import ACTIVE_CONTEXT, read as _game_read, complete as _game_complete, submit as _game_submit
import asyncio
import struct
from pdx_runtime import v3_access, v3_commands as commands

EXECUTE_ACTION = 21256
RESPOND_PROPOSAL = 21275
PLANS = []  # retain unknown delivery, query, owner and input for the caller

def _id(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 0xffffffff:
        raise ValueError("requires a concrete full uint32 instance ID; zero is valid")
    return value

def action_parameters(action_key, first, second, *, first_state=0xffffffff,
                      second_state=0xffffffff, country_only=True, obligation=357):
    """CDiplomaticActionType text key; never an Agreement/live proposal ID.

    Raw generic commands still expose every enum/value. This business example
    always uses check_selectable=24150, not skip_selectable/response bypass.
    """
    _id(first); _id(second)
    if first == second or not isinstance(action_key, str) or not action_key or "\0" in action_key:
        raise ValueError("distinct parties and a nonempty native Definition key are required")
    for state in (first_state, second_state):
        if isinstance(state, bool) or not isinstance(state, int) or not 0 <= state <= 0xffffffff:
            raise ValueError("state must be full uint32 ref or native invalid sentinel")
    if type(country_only) is not bool or obligation not in (357, 21620, 21619):
        raise ValueError("explicit bool and one native obligation token are required")
    return {12795:{10374:commands.UInt32(first),10375:commands.UInt32(second),
                   24084:commands.UInt32(first_state),24085:commands.UInt32(second_state),
                   24086:country_only},11760:action_key,
            21268:commands.Token(obligation),24152:commands.Token(24150)}

def response_parameters(live_action_id, *, accept):
    """Same field11760, DIFFERENT domain: full CDiplomaticAction ID + bool."""
    _id(live_action_id)
    if type(accept) is not bool:
        raise ValueError("accept is a bool, not token/string/ID")
    return {11760:commands.UInt32(live_action_id),1288:accept}

def _world(plan):
    status = V3Wire(plan["client"]).world_status()
    if status.get("status") != "ok":
        raise RuntimeError("native world observer failed; retain the exact state")
    world = int(status["world"],0) if isinstance(status["world"],str) else status["world"]
    if world != plan["expected_world"] or status.get("transition_pending"):
        raise RuntimeError("world changed/unavailable; never reuse references or submit")
    plan["last_world_status"] = status
    return world

async def _once(plan,label,submit,*,owner=None,timeout_seconds=15.0,poll_interval=0.01):
    if plan["pending"] is not None:
        raise RuntimeError("prior operation unresolved; inspect its exact handle")
    if (isinstance(timeout_seconds,bool) or not isinstance(timeout_seconds,(int,float)) or
        not 0 < timeout_seconds < float("inf") or isinstance(poll_interval,bool) or
        not isinstance(poll_interval,(int,float)) or not 0 < poll_interval < float("inf")):
        raise ValueError("positive finite monotonic timeout and polling interval are required")
    _world(plan)
    try:
        query = (await _game_submit(submit))
    except Exception as error:
        plan["pending"]={"label":label,"query":getattr(error,"query",None),"owner":owner,"error":error}
        plan["status"]="delivery_unknown"
        raise
    loop=asyncio.get_running_loop();future=loop.create_future()
    deadline=loop.time()+timeout_seconds
    plan["pending"]={"label":label,"query":query,"owner":owner,
                     "deadline_monotonic":deadline,"timeout_seconds":timeout_seconds,
                     "poll_interval":poll_interval}
    timer=None
    def sample():
        nonlocal timer
        if future.done():return
        try:
            value=owner.complete() if owner is not None else query.read()
            if value is not None:future.set_result(value)
            else:
                remaining=deadline-loop.time()
                if remaining <= 0:
                    future.set_exception(TimeoutError("monotonic deadline elapsed; retain exact pending query, never replay"))
                else:timer=loop.call_later(min(poll_interval,remaining),sample)
        except Exception as error:future.set_exception(error)
    context=ACTIVE_CONTEXT.get()
    if context is None:timer=loop.call_later(0,sample)
    try:
        value=await future if context is None else await context.wait(query,owner=owner)
    except BaseException as error:
        plan["pending"]["error"]=error;plan["status"]="completion_unknown"
        raise
    finally:
        if timer is not None:timer.cancel()
    plan["operations"].append({"label":label,"query":query,"result":value})
    plan["pending"]=None
    try:_world(plan)
    except BaseException:
        plan["status"]="world_changed_after_completion"
        raise
    return value

async def _raw(plan,label,target,args):
    result=await _once(plan,label,lambda:v3_access.submit_call(plan["client"],target,args,
        mode=1,flag=0,dispatch="interface",adapter=plan["access_adapter"],
        call_adapter=plan["call_adapter"],process=plan["process"]))
    if result.error or result.callee_result.error:
        raise RuntimeError("native read callback/target failed; retain its input")
    return result.callee_result.rax

def _read(plan,address,fmt):
    return struct.unpack(fmt,plan["process"].read(address,struct.calcsize(fmt)))[0]

async def _valid(plan,label,pointer,slot):
    if not pointer:raise RuntimeError("native selector returned null")
    table=_read(plan,pointer,"<Q")
    fn=_read(plan,table+slot*8,"<Q")
    return bool((await _raw(plan,label,fn,(pointer,))) & 0xff)

async def observe(plan,label="observe"):
    """Independent manager predicates/identity, never command acceptance.

    Definition* originates from the NEW private command+40, proven exact class.
    No Any alias is retagged/rebound. Source is destroyed only as known storage.
    All full-ID generation bits and native invalid/fallback markers are kept.
    """
    world=_world(plan);base=plan["base"]
    definition=plan["definition"]
    if not await _valid(plan,label+"_type_valid",definition,0):
        raise RuntimeError("fresh native CDiplomaticActionType is invalid")
    manager=_read(plan,world+0x120,"<Q")
    if not manager:raise RuntimeError("current CountryManager unavailable")
    args=(plan["first"],plan["second"],definition)
    pending=await _raw(plan,label+"_pending",base+0xfaa7b0,(manager+0x2598,*args))
    pending_valid=await _valid(plan,label+"_pending_valid",pending,1)
    action_id=_read(plan,pending+8,"<I")
    if pending_valid and (_read(plan,pending+0x20,"<I") != plan["first"] or
                          _read(plan,pending+0x24,"<I") != plan["second"] or
                          _read(plan,pending+0x18,"<Q") != definition):
        raise RuntimeError("native pending identity differs from exact intended parties/type")
    pact=await _raw(plan,label+"_pact",base+0xec5c00,(manager+0x1f28,*args))
    pact_valid=await _valid(plan,label+"_pact_valid",pact,1)
    exists=await _raw(plan,label+"_pact_exists",base+0xec5aa0,(manager+0x1f28,*args))
    if bool(exists & 0xff) != pact_valid:
        raise RuntimeError("independent native pact predicate/selector disagree")
    if pact_valid and _read(plan,pact+0x30,"<Q") != definition:
        raise RuntimeError("native pact Definition differs; no identity inference")
    value={"world":world,"pending_valid":pending_valid,"action_id":action_id,
           "pending_fallback":pending == _read(plan,base+0x5964448,"<Q"),
           "pact_valid":pact_valid,"pact_id":_read(plan,pact+8,"<I"),
           "pact_fallback":pact == _read(plan,base+0x5964488,"<Q"),
           "pact_predicate_rax":exists,"first":plan["first"],"second":plan["second"],
           "definition":definition}
    plan["observations"].append({"label":label,**value});_world(plan)
    return value


def _plan(client,process,*,image_base,expected_world,action_key,first,second,intent,
          commands_adapter,access_adapter,call_adapter):
    _id(first);_id(second)
    if first == second or not expected_world or client.pid != process.pid:
        raise ValueError("fresh same-process/world and independently matched distinct parties required")
    for old in PLANS:
        if old["client"] is client and old["expected_world"] == expected_world:
            if old["pending"] is not None or any(o.command for o in old["owners"]) or old["status"] in (
                "submission_attempted","accepted_effect_not_observed","delivery_unknown","completion_unknown",
                "world_changed_after_completion","creation_owner_unresolved"):
                raise RuntimeError("prior plan unresolved/owned; never replay or prepare a replacement")
    plan={"client":client,"process":process,"base":image_base,"expected_world":expected_world,
          "action_key":action_key,"first":first,"second":second,"intent":intent,
          "commands_adapter":commands_adapter,"access_adapter":access_adapter,"call_adapter":call_adapter,
          "owner":None,"owners":[],"definition":0,"pending":None,"operations":[],"observations":[],
          "status":"new","submit_count":0,"buffers":[]}
    PLANS.append(plan);return plan

async def _create(plan,type_token,actor,parameters,label):
    result=await _once(plan,label,lambda:commands.create(plan["client"],type_token,
        actor_id=actor,parameters=parameters,adapter=plan["commands_adapter"],process=plan["process"]))
    plan.setdefault("creation_results",[]).append({"label":label,"result":result})
    if result.error or result.world != plan["expected_world"]:
        plan["status"]="creation_owner_unresolved"
        raise RuntimeError("fresh factory ownership/world not established; inspect exact result")
    owner=commands.NativeCommand.from_result(plan["client"],result,
        adapter=plan["commands_adapter"],process=plan["process"])
    plan["owners"].append(owner)
    serial=await _once(plan,label+"_serialize",lambda:owner.serialize(),owner=owner)
    if serial.error:raise RuntimeError("native serialize failed; retain owner")
    tree=commands.decode_command(serial.data)
    if tree.key != commands.Token(type_token) or commands.encode_tree(tree) != serial.data:
        raise RuntimeError("native type/wire mismatch; do not submit")
    plan["serialized"].append({"label":label,"result":serial,"tree":tree})
    return owner

def _fields(tree,token):
    if isinstance(tree,commands.Assignment):
        return ([tree.value] if tree.key == commands.Token(token) else []) + _fields(tree.value,token)
    return [v for child in tree for v in _fields(child,token)] if isinstance(tree,tuple) else []

async def _validate(plan,owner):
    result=await _once(plan,"native_validate",lambda:owner.validate(capacity=65536),owner=owner)
    plan["validation"]=result
    if result.error not in (0,122):raise RuntimeError("native validator errored; retain known owner/query")
    if not result.validated:
        plan["status"]="native_rejected_no_submission"
        await destroy_unsubmitted(plan,keep_status=True)
        return False
    plan["owner"]=owner;plan["status"]="prepared_not_submitted";return True

async def prepare(client,process,*,image_base,expected_world,action_key,first,second,intent,
                  commands_adapter,access_adapter,call_adapter,expected_action_id=None,
                  first_state=0xffffffff,second_state=0xffffffff,country_only=True):
    """Query/Factory/Serialize/Validate only. No effect is preparation or cleanup.

    intent is start_pact, break_pact, propose, accept, or decline. The example
    intentionally uses no obligation and check_selectable. Raw API keeps all.
    accept/decline require an independently supplied CURRENT pending fullID.
    """
    if intent not in ("start_pact","break_pact","propose","accept","decline"):
        raise ValueError("requires one separate explicit business intent")
    if intent in ("accept","decline"):_id(expected_action_id)
    plan=_plan(client,process,image_base=image_base,expected_world=expected_world,
        action_key=action_key,first=first,second=second,intent=intent,
        commands_adapter=commands_adapter,access_adapter=access_adapter,call_adapter=call_adapter)
    plan["expected_action_id"]=expected_action_id;plan["serialized"]=[]
    source=await _create(plan,EXECUTE_ACTION,first,action_parameters(action_key,first,second,
        first_state=first_state,second_state=second_state,country_only=country_only),"fresh_action_source")
    tree=plan["serialized"][-1]["tree"]
    expected={10304:commands.UInt32(first),10374:commands.UInt32(first),10375:commands.UInt32(second),
              11760:action_key,21268:commands.Token(357),24152:commands.Token(24150)}
    if any(_fields(tree,k) != [v] for k,v in expected.items()):
        raise RuntimeError("native source actor/parties/type/enums differ; retain owner")
    for key,value in [(24084,first_state),(24085,second_state)]:
        expected_values=[] if value == 0xffffffff else [commands.UInt32(value)]
        if _fields(tree,key) != expected_values:raise RuntimeError("native State refs differ")
    if _fields(tree,24086) != ([True] if country_only else []):
        raise RuntimeError("native country-only flag differs")
    plan["definition"]=_read(plan,source.command+0x40,"<Q")
    before=await observe(plan,"before");plan["before"]=before
    # E0BF10/E0C063 prove pact-action bit6 / requires-approval bit1.
    # Reject a wrong intent before submit, rather than observing the wrong effect.
    flags=_read(plan,plan["definition"]+0x48,"<B")
    plan["native_type_flags"]=flags
    if ((intent == "propose" and not flags & 2) or
        (intent == "start_pact" and (not flags & 64 or flags & 2)) or
        (intent == "break_pact" and not flags & 64)):
        plan["status"]="native_type_does_not_match_intent";return plan
    eligible=(before["pending_valid"] and before["action_id"] == expected_action_id) if intent in ("accept","decline") else (
        not before["pending_valid"] and before["pact_valid"] == (intent == "break_pact"))
    if not eligible:
        plan["status"]="intent_precondition_not_met";return plan
    if intent in ("accept","decline"):
        owner=await _create(plan,RESPOND_PROPOSAL,second,response_parameters(expected_action_id,
            accept=intent == "accept"),"fresh_response")
        tree=plan["serialized"][-1]["tree"]
        if (_fields(tree,10304) != [commands.UInt32(second)] or
            _fields(tree,11760) != [commands.UInt32(expected_action_id)] or
            _fields(tree,1288) != [intent == "accept"]):
            raise RuntimeError("native response actor/live-ID/bool differ")
    else:owner=source
    await _validate(plan,owner)
    return plan

async def submit(plan,*,intent):
    """Exactly one Session4 attempt, after new independent identity precheck."""
    if intent != plan["intent"] or plan["status"] != "prepared_not_submitted" or plan["submit_count"]:
        raise ValueError("requires same explicit intent and fresh native-valid unsubmitted owner")
    current=await observe(plan,"intent_precheck");old=plan["before"]
    if any(current[k] != old[k] for k in ("pending_valid","action_id","pact_valid","pact_id")):
        plan["status"]="intent_precondition_changed_known_owner_held";return plan
    owner=plan["owner"];plan["submit_count"]=1;plan["status"]="submission_attempted"
    result=await _once(plan,"submit_once",lambda:owner.submit(flags=4),owner=owner)
    plan["submission"]=result
    if result.error or result.command or owner.command:
        raise RuntimeError("delivery/ownership unknown; never resubmit/execute directly")
    if not result.accepted:
        plan["status"]="receiver_rejected_consumed";return plan
    plan["status"]="accepted_effect_not_observed"
    return await observe_effect(plan)

async def observe_effect(plan):
    """One read-only explicit observation; never retries a business mutation."""
    if plan["status"] not in ("accepted_effect_not_observed","observed_expected_effect"):
        raise ValueError("requires accepted consumed command, not preparation")
    value=await observe(plan,"after");intent=plan["intent"]
    absent=(not value["pending_valid"] and value["pending_fallback"] and value["action_id"] == 0xffffffff)
    if intent == "propose":matched=value["pending_valid"]
    elif intent == "start_pact":matched=value["pact_valid"] and absent
    elif intent == "break_pact":matched=not value["pact_valid"] and value["pact_fallback"] and value["pact_id"] == 0xffffffff and absent
    else:
        # Response removes the exactpending request immediately, but native
        # FAA7F0/FAB140 only mark SDeadRefObject; RefDB mayretain theold fullID.
        # Keep that resolution asdiagnostic, never equate it withpending status.
        process=plan["process"];address=process.allocate(4);plan["buffers"].append(address)
        process.write(address,struct.pack("<I",plan["expected_action_id"]))
        ptr=await _raw(plan,"old_live_action_resolve",plan["base"]+0x1fe0c90,(address,))
        old_valid=await _valid(plan,"old_live_action_valid",ptr,1)
        value["old_action_valid"]=old_valid;value["old_action_full_id"]=_read(plan,ptr+8,"<I")
        process.free(address);plan["buffers"].remove(address)
        value["old_action_retained_in_refdb"]=old_valid
        if old_valid and value["old_action_full_id"] != plan["expected_action_id"]:
            raise RuntimeError("native oldfullID resolver identity mismatch; do not inferresponse")
        matched=absent
        # accept_effect may create a reverse_pact or only a scripted effect.
        # Absence is response completion, not independent proof of those effects.
    plan["after"]=value
    if matched:plan["status"]="observed_expected_effect"
    return plan

async def destroy_unsubmitted(plan,*,keep_status=False):
    """Only known unique storage; no pact-break/decline as compensation."""
    if plan["pending"] is not None or plan["buffers"] or plan["status"] in (
        "delivery_unknown","completion_unknown","submission_attempted","world_changed_after_completion","creation_owner_unresolved"):
        raise RuntimeError("unresolved delivery/input must be inspected, not cleaned/replayed")
    for owner in plan["owners"]:
        if owner.command:
            result=await _once(plan,"destroy_known_unique",lambda:owner.destroy(),owner=owner)
            if result.error or owner.command:raise RuntimeError("native deletion outcome unknown")
    if not keep_status:plan["storage_released"]=True
    return plan
