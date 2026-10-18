"""Native query/command ownership for building policy and subsidy operations.

Keep the exact pending query and native owner. Submission and cleanup never
replay an unresolved request or replace a copied primary result.
"""
from __future__ import annotations
from pdx_runtime.v3_wire import V3Wire
from pdx_runtime.v3_game._run import read as _game_read, complete as _game_complete, submit as _game_submit
import asyncio
from pdx_runtime import v3_data as data, v3_commands as commands

BUILDING_COMMAND_PLANS = []


def _world(plan):
    status = V3Wire(plan["client"]).world_status()
    value = status.get("world", 0)
    world = int(value, 0) if isinstance(value, str) else value
    if status.get("status") != "ok" or world != plan["world"] or status.get("transition_pending"):
        raise RuntimeError("world changed/unavailable; do not reuse building references")
    return world


def _ok(result, label):
    if result.error:
        raise RuntimeError(f"{label}: native error {result.error}; inspect exact recorded result")
    return result








def _begin_consumption(plan, owner, action):
    if not owner.command or owner.pending is not None or owner.world != plan["world"]:
        raise RuntimeError("consuming recipe requires its known unsubmitted unique owner in the same world")
    attempt = {"action":action, "owner":owner, "command_before":owner.command,
        "owner_type_token":owner.type_token, "world":owner.world, "requested_type_token":0,
        "command_wire":bytes(plan.get("command_wire", b"")), "serialized":plan.get("serialized"),
        "validation":plan.get("validation"), "original_query":None,
        "completed_result":None, "callback_completed":False, "allocations":{},
        "queue_entered":False, "queue_rejected":False, "handoff_uncertain":True,
        "outcome":"dispatch_not_yet_known", "flags":4 if action == commands.Action.SUBMIT else None,
        "payload_offset":commands.v3_session.OPAQUE_OFFSET if action == commands.Action.SUBMIT else 0}
    plan["consumptions"].append(attempt)
    plan["safe_to_discard"] = False
    return attempt





def _record_consumption(plan, attempt, result):
    """Expose the actual NativeCommand completion; do not add a second receipt gate."""
    owner = attempt["owner"]
    native_attempt = owner.attempt
    attempt.update(completed_result=result, callback_completed=True,
                   native_attempt=native_attempt,
                   native_outcome=getattr(native_attempt, "outcome", None))
    outcome = getattr(native_attempt, "outcome", "unknown")
    if outcome == "consumed":
        attempt["handoff_uncertain"] = False
        attempt["outcome"] = (("accepted" if result.accepted else "receiver_rejected_consumed")
            if attempt["action"] == commands.Action.SUBMIT else "destroyed")
    elif outcome == "owned":
        attempt.update(handoff_uncertain=False, outcome="native_precondition_owned")
    else:
        attempt["outcome"] = "native_outcome_unknown"
        if not any(value is attempt for value in plan["uncertain_operations"]):
            plan["uncertain_operations"].append(attempt)
    if not attempt["handoff_uncertain"]:
        plan["uncertain_operations"] = [value for value in plan["uncertain_operations"] if value is not attempt]
    plan["safe_to_discard"] = False  # storage/effect are separate; cleanup decides storage


def _consumption_failure(plan, pending, error):
    attempt = pending.get("consumption")
    if attempt is None:
        return
    query = pending.get("query")
    native_attempt = attempt["owner"].attempt
    attempt.update(original_query=query, error=error, native_attempt=native_attempt,
                   native_outcome=getattr(native_attempt, "outcome", None))
    cached = getattr(native_attempt, "result", None)
    if cached is None and query is not None:
        cached = getattr(query, "_result", None)
    if cached is not None:
        _record_consumption(plan, attempt, cached)
        pending.update(callback_completed=True, result=cached)
    else:
        attempt["outcome"] = "original_delivery_or_storage_unknown"
        if not any(value is attempt for value in plan["uncertain_operations"]):
            plan["uncertain_operations"].append(attempt)
    plan["safe_to_discard"] = False


def _finished(plan, result):
    pending = plan["pending"]
    value = getattr(result, "value_handle", 0)
    if value and value not in plan["values"]:
        plan["values"].append(value)
    if getattr(result, "action", None) == commands.Action.CLONE and getattr(result, "cloned_command", 0):
        # Unknown delivery may settle AFTER its preparation coroutine exited.
        # Preserve the returned unique owner until this EXACT result is adopted.
        plan["unadopted_clones"].append(result)
        plan["status"] = "native_unique_clone_completed_not_adopted"
    handle = pending.get("release_handle")
    if handle is not None:
        plan["release_attempts"][handle]["result"] = result
        if not result.error:
            plan["values"].remove(handle)
    attempt = pending.get("consumption")
    if attempt is not None:
        _record_consumption(plan, attempt, result)
    plan["operations"].append({"label":pending["label"], "query":pending["query"],
                               "owner":pending.get("owner"), "result":result, "consumption":attempt})
    if attempt is not None and attempt["handoff_uncertain"]:
        pending.update(callback_completed=True, result=result)
        plan["status"] = "completed_callback_handoff_uncertain_no_replay"
    else:
        plan["pending"] = None
    return result


async def settle_pending(plan, *, timeout_seconds=30.0):
    """Read the original query/owner only; never generate/clone/submit again.

    Deadline limits repeated observations, not an already blocked synchronous
    transport read. Timeout/cancellation preserves original owner/query/intent.
    Inspect a recovered Clone result explicitly before adopting its unique owner.
    """
    pending = plan["pending"]
    if pending is None or pending.get("query") is None:
        raise RuntimeError("no exact recoverable pending handle")
    if pending.get("callback_completed"):
        raise RuntimeError("original callback already completed; handoff remains unknown, do not replay/read/free again")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    try:
        while True:
            if loop.time() >= deadline:
                raise TimeoutError("elapsed deadline; retain exact original callback, no replay")
            owner = pending.get("owner")
            result = (await _game_complete(owner)) if owner is not None else (await _game_read(pending["query"]))
            if result is not None:
                break
            await asyncio.sleep(min(0.005, max(0.0, deadline-loop.time())))
    except BaseException as error:
        pending["error"] = error
        _consumption_failure(plan, pending, error)
        plan["status"] = "original_callback_unknown_retained_no_replay"
        raise
    return _finished(plan, result)


async def _once(plan, label, make_query, *, owner=None, release_handle=None, consume_action=None):
    if plan["pending"] is not None or plan.get("uncertain_operations"):
        raise RuntimeError("prior operation unresolved; retain its original native handle")
    plan["safe_to_discard"] = False
    attempt = _begin_consumption(plan, owner, consume_action) if consume_action is not None else None
    pending = {"label":label, "query":None, "owner":owner,
               "release_handle":release_handle, "consumption":attempt}
    plan["pending"] = pending
    try:
        query = (await _game_submit(make_query))
    except BaseException as error:
        pending.update(query=getattr(error, "query", None), error=error)
        _consumption_failure(plan, pending, error)
        plan["status"] = "submission_exception_original_handle_retained"
        raise
    pending["query"] = query
    if attempt is not None:
        attempt["original_query"] = query
    return await settle_pending(plan)


def _new(client, process, *, expected_world, actor_id, data_adapter, commands_adapter,
         objects_adapter, access_adapter, call_adapter):
    if not expected_world or client.pid != process.pid:
        raise ValueError("one current world and matching process/client are required")
    for prior in BUILDING_COMMAND_PLANS:
        if prior["client"] is client and prior["world"] == expected_world and (prior["pending"] or prior["buffers"]):
            raise RuntimeError("prior building callback/buffer is unresolved; do not start another attempt")
    plan = {"client":client, "process":process, "world":expected_world,
        "actor_id":actor_id, "data_adapter":data_adapter, "commands_adapter":commands_adapter,
        "objects_adapter":objects_adapter, "access_adapter":access_adapter,
        "call_adapter":call_adapter, "pending":None, "values":[], "owners":[], "buffers":[],
        "operations":[], "release_attempts":{}, "destroy_attempts":set(), "types":{},
        "submit_count":0, "status":"preparing_only", "owner":None, "unadopted_clones":[],
        "consumptions":[], "uncertain_operations":[], "safe_to_discard":False}
    BUILDING_COMMAND_PLANS.append(plan)
    return plan




async def _eval(plan, label, expression, bindings):
    return _ok(await _once(plan, label, lambda:data.evaluate(plan["client"], expression,
        bindings, adapter=plan["data_adapter"], process=plan["process"])), label)


async def _types(plan):
    from pdx_runtime.v3_game._run import types
    current=await types()
    if current is not None:
        plan["types"]=current
        return
    result = _ok(await _once(plan, "current_types", lambda:data.enumerate_types(plan["client"],
        capacity=1400, adapter=plan["data_adapter"], process=plan["process"])), "current_types")
    plan["types"] = {row.name:row.type_id for row in result.rows}










def adopt_completed_clone(plan, result):
    """Adopt ONLY this recorded original Clone result, without another native call.

    After explicit settle_pending recovery this registers unique storage ownership;
    it does not resume preparation, serialize/validate for the caller, or submit.
    A failed/partial result stays in the ledger for explicit inspection.
    """
    if plan["pending"] is not None or not any(value is result for value in plan["unadopted_clones"]):
        raise RuntimeError("requires the exact settled unadopted Clone result")
    _world(plan)
    clone = getattr(result, "cloned_command", 0)
    source = plan.get("clone_source_pointer", 0)
    if (result.action != commands.Action.CLONE or result.error or not clone
            or result.world != plan["world"] or clone == getattr(result, "command", 0)
            or (source and clone == source)):
        raise RuntimeError("invalid/alias Clone receipt; keep exact unadopted result, never delete shared source")
    owner = commands.NativeCommand.from_result(plan["client"], result,
        adapter=plan["commands_adapter"], process=plan["process"])
    plan["owners"].append(owner)
    plan["owner"] = owner
    plan["unadopted_clones"] = [value for value in plan["unadopted_clones"] if value is not result]
    plan["status"] = "native_unique_clone_adopted_preparation_not_resumed"
    return owner












async def cleanup_known(plan):
    """Return cleanup details beside native results; never replay an attempted action."""
    cleanup = plan.setdefault("cleanup", {})
    cleanup.setdefault("results", [])
    cleanup.setdefault("errors", [])
    cleanup.update(status="pending", pending=None)
    plan["safe_to_discard"] = False

    def retained(status, error=None):
        cleanup.update(status=status, pending=plan.get("pending"),
                       unreleased_values=tuple(plan["values"]))
        if error is not None:
            cleanup["errors"].append(error)
        return plan

    if (plan["pending"] is not None or plan["buffers"] or plan["uncertain_operations"]
            or any(value["handoff_uncertain"] for value in plan["consumptions"])
            or plan["unadopted_clones"]):
        return retained("retained_pending_or_unknown")
    for owner in plan["owners"]:
        if not owner.command:
            continue
        if owner in plan["destroy_attempts"] or owner.pending is not None:
            return retained("original_destroy_retained_no_replay")
        plan["destroy_attempts"].add(owner)
        try:
            result = await _once(plan, "destroy_known_unique", owner.destroy, owner=owner,
                                 consume_action=commands.Action.DESTROY)
        except Exception as error:
            return retained("destroy_unresolved", error)
        cleanup["results"].append(result)
        if result.error or getattr(owner.attempt, "outcome", None) != "consumed":
            return retained("destroy_native_error_or_unknown")
    for handle in tuple(reversed(plan["values"])):
        if handle in plan["release_attempts"]:
            return retained("original_release_retained_no_replay")
        plan["release_attempts"][handle] = {"result": None}
        try:
            result = await _once(plan, "release_known_data", lambda h=handle:data.release(
                plan["client"], h, adapter=plan["data_adapter"], process=plan["process"]),
                release_handle=handle)
        except Exception as error:
            return retained("release_unresolved", error)
        cleanup["results"].append(result)
        if result.error:
            return retained("release_native_error")
    plan["safe_to_discard"] = not any(owner.command or owner.pending is not None for owner in plan["owners"])
    plan["status"] = "all_known_native_storage_released"
    return retained("complete")
