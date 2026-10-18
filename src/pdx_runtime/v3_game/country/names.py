"""Native State naming and the exact query/command ownership used by its consumers."""
from __future__ import annotations
from pdx_runtime.v3_game._run import read as _game_read, complete as _game_complete, submit as _game_submit
import asyncio
import time
from pdx_runtime import v3_data as data, v3_commands as commands

LAST_RENAME = None
RENAME_STATES = []

EDIT_NAMES = ("OnEditName", "OnEditCityName", "OnEditPortName", "OnEditFarmName",
              "OnEditMineName", "OnEditWoodName")


def _state(client, process, state_id, data_adapter, commands_adapter):
    return {"client": client, "process": process, "state_id": state_id,
            "data_adapter": data_adapter, "commands_adapter": commands_adapter,
            "status": "new", "pending": None, "owned_values": [], "operations": [],
            "clone_owner": None, "unsettled_clone": None}


async def _once(state, label, make_query, *, owner=None, timeout=30.0):
    """Submit once and sample only that handle; never submit/recover a second call."""
    if state["pending"] is not None:
        raise RuntimeError("an operation is unresolved; inspect that exact handle first")
    try:
        query = (await _game_submit(make_query))
    except Exception as error:
        state["pending"] = {"label": label, "query": getattr(error, "query", None),
                            "owner": owner, "error": error}
        state["status"] = "delivery_unknown"
        raise
    state["pending"] = {"label": label, "query": query, "owner": owner}
    deadline = time.monotonic() + timeout
    while True:
        try:
            result = (await _game_complete(owner)) if owner is not None else (await _game_read(query))
        except Exception as error:
            state["pending"]["error"] = error
            state["status"] = "completion_unknown"
            # A pending sentinel/native callback exception does not authorize free.
            raise
        if result is not None:
            state["operations"].append({"label": label, "request_id": query.request_id,
                                         "result": result})
            state["pending"] = None
            # A nonzero retained Any is owned even if its snapshot returned an error.
            if isinstance(result, (data.DataResult, data.ActionResult)):
                handle = result.value_handle
                if handle and label not in ("command_value",) and not label.startswith("release_"):
                    if handle not in state["owned_values"]:
                        state["owned_values"].append(handle)
            return result
        if time.monotonic() >= deadline:
            state["status"] = "completion_timeout"
            raise TimeoutError(f"{label} pending; preserve state['pending']; do not replay")
        # This is the external example's bounded completion wait, not a new native call.
        await asyncio.sleep(0.005)


def _ok(result, label):
    if result.error:
        raise RuntimeError(f"{label}: native error {result.error}; retain the recorded result")
    return result


async def _overrides(state, label):
    values = []
    for index in range(6):
        result = await _once(state, f"{label}_{index}", lambda index=index: data.state_name_override(
            state["client"], state["state_id"], index=index, output_bytes=65536,
            adapter=state["data_adapter"], process=state["process"]))
        _ok(result, label)
        if result.snapshot_bytes != result.total:
            raise RuntimeError("raw override truncated; stop, do not reuse a display/fallback name")
        values.append(result.snapshot)
    return tuple(values)


def _fields(tree, token):
    result = []
    def visit(value):
        if isinstance(value, commands.Assignment):
            if isinstance(value.key, commands.Token) and value.key.value == token:
                result.append(value.value)
            visit(value.value)
        elif isinstance(value, tuple):
            for item in value:
                visit(item)
    visit(tree)
    return result


async def release_values(state):
    """Explicit cleanup of completed Any owners; pending/unknown operations block it."""
    for handle in tuple(reversed(state["owned_values"])):
        result = await _once(state, f"release_{handle}", lambda handle=handle: data.release(
            state["client"], handle, adapter=state["data_adapter"], process=state["process"]))
        _ok(result, "release")
        if result.value_handle != 0:
            raise RuntimeError("native release did not clear its handle")
        state["owned_values"].remove(handle)


async def prepare_rename(client, process, *, state_id, new_city,
                         data_adapter, commands_adapter, generate_confirm=False):
    """Explicit generation/clone/observation phase; NEVER submits the command."""
    if not generate_confirm:
        raise ValueError("generate_confirm=True is required for this explicit example")
    # Never hide an earlier unresolved/native-owned attempt behind a new alias.
    global LAST_RENAME
    if LAST_RENAME is not None:
        previous = LAST_RENAME
        owner = previous["clone_owner"]
        if (previous["pending"] is not None or previous["unsettled_clone"] is not None
                or previous["owned_values"] or (owner is not None and owner.command)
                or previous["status"] in ("submission_started", "accepted_effect_not_yet_observed")):
            raise RuntimeError("prior rename has unresolved delivery/ownership/effect; inspect it first")
    state = _state(client, process, state_id, data_adapter, commands_adapter)
    # Keep every attempt and its exact handles/results in this ordinary namespace.
    LAST_RENAME = state
    RENAME_STATES.append(state)
    state["before"] = await _overrides(state, "before")
    desired = list(state["before"])
    desired[1] = new_city.encode("utf-8")
    state["desired"] = tuple(desired)
    # Native Initialize may use localized fallbacks. Confirm writes all six fields,
    # so explicitly restore every other draft to its original raw override bytes.
    # Empty overrides stay b""; they are never replaced with localized display text.
    desired_text = tuple(value.decode("utf-8", "strict") for value in desired)
    created = await _once(state, "create_context", lambda: data.create_state_name_context(
        client, state_id, adapter=data_adapter, process=process))
    _ok(created, "create_context")
    binding = created.value.binding()  # dynamic type from the real native Any
    for index, (name, text) in enumerate(zip(EDIT_NAMES, desired_text)):
        edited = await _once(state, f"edit_{index}", lambda name=name, text=text: data.invoke_action(
            client, f"StateNameChangePopup.{name}", data.TypedInput.utf8_string(text),
            (binding,), adapter=data_adapter, process=process))
        _ok(edited, name)
        if edited.invoked != 1 or edited.accepted != 1:
            raise RuntimeError(f"{name}: native typed setter rejected; no Confirm or retry")
        draft = await _once(state, f"draft_{index}", lambda index=index: data.state_name_draft(
            client, created.value_handle, index=index, output_bytes=65536,
            adapter=data_adapter, process=process))
        _ok(draft, "draft")
        if draft.snapshot != desired[index]:
            raise RuntimeError("draft bytes differ from explicitly selected raw fields")
    # Confirm is mode2: normal native evaluate generates shared CDataCommand only.
    generated = await _once(state, "confirm_generate", lambda: data.evaluate(
        client, "StateNameChangePopup.Confirm", (binding,), output_bytes=24,
        adapter=data_adapter, process=process))
    _ok(generated, "Confirm")
    if not isinstance(generated.value, data.NativeDataCommand):
        raise RuntimeError("Confirm did not return the proven native shared carrier")
    extracted = await _once(state, "command_value", lambda: data.command_value(
        client, generated.value_handle, adapter=data_adapter, process=process))
    _ok(extracted, "command_value")
    state["shared_carrier"] = extracted.value
    carrier = extracted.value
    if not carrier.generation_world:
        raise RuntimeError("no captured generation world; never replace it with a later world")
    cloned = await _once(state, "clone", lambda: commands.request(
        client, commands.Action.CLONE, command=carrier.primary_pointer, capacity=8,
        world=carrier.generation_world, adapter=commands_adapter, process=process))
    state["clone_result"] = cloned
    if cloned.error:
        # Native clone can have published a distinct pointer before returning574.
        # Keep query/result/source; do not call from_result, drop the pointer,
        # re-Clone or submit. Root inspects/settles the exact result separately.
        state["unsettled_clone"] = cloned
        state["status"] = "clone_error_requires_inspection"
        return state
    owner = commands.NativeCommand.from_result(client, cloned, adapter=commands_adapter, process=process)
    state["clone_owner"] = owner
    if owner.command == carrier.primary_pointer or owner.world != carrier.generation_world:
        raise RuntimeError("clone is not an independent owner in the captured generation world")
    serialized = await _once(state, "serialize_clone", lambda: owner.serialize(), owner=owner)
    _ok(serialized, "serialize_clone")
    state["tree"] = commands.decode_command(serialized.data)
    if commands.encode_tree(state["tree"]) != serialized.data:
        raise RuntimeError("native serialized command tree does not round-trip byte-exactly")
    if owner.type_token != 13303:
        raise RuntimeError("native clone is not ChangeNamesInState; no submit")
    # Exact serializer fields are from CEC470, not guessed object writes.
    if _fields(state["tree"], 0x1b7) != [commands.UInt32(state_id)]:
        raise RuntimeError("serialized complete State ID mismatch; no submit")
    if _fields(state["tree"], 0x33f8) != [desired_text[0]]:
        raise RuntimeError("serialized State-name override mismatch; no submit")
    if _fields(state["tree"], 0x33f9) != [desired_text[1:]]:
        raise RuntimeError("serialized five hub names mismatch; inspect native tree, no submit")
    validated = await _once(state, "validate_clone", lambda: owner.validate(), owner=owner)
    _ok(validated, "validate_clone")
    state["validation"] = validated
    if validated.validated != 1:
        state["status"] = "native_validation_rejected"
        return state  # native reason retained; explicit cleanup is still available
    state["after_generation"] = await _overrides(state, "after_generation")
    if state["after_generation"] != state["before"]:
        raise RuntimeError("generation alone changed raw world overrides; stop")
    # Clone is independent now. Explicitly release all completed shared Any graphs.
    await release_values(state)
    state["status"] = "prepared_not_submitted"
    return state


async def submit_prepared(state, *, submit=False):
    """A separate one-time explicit Session submit, then independent raw reads."""
    if not submit or state["status"] != "prepared_not_submitted":
        raise ValueError("requires submit=True and exactly one prepared, unsubmitted command")
    owner = state["clone_owner"]
    state["status"] = "submission_started"  # prohibit a second submit even after rejection/unknown
    submitted = await _once(state, "submit_clone", lambda: owner.submit(flags=4), owner=owner)
    state["submission"] = submitted
    if submitted.command or owner.command:
        raise RuntimeError("receiver did not report consumed ownership; stop and inspect")
    _ok(submitted, "Session submit")
    state["after"] = await _overrides(state, "after_submit")
    if not submitted.accepted:
        state["status"] = "receiver_rejected_consumed"
    elif state["after"] == state["desired"]:
        state["status"] = "observed_raw_override_change"
    else:
        # Acceptance is not command settlement. Never submit again or replace the
        # independent observation with a localized GetName string as proof.
        state["status"] = "accepted_effect_not_yet_observed"
    return state


async def observe_submitted(state):
    """Explicit independent read after a caller-observed dispatch boundary; no retry."""
    if state["status"] not in ("accepted_effect_not_yet_observed", "observed_raw_override_change"):
        raise ValueError("requires an accepted, consumed one-time submission")
    after = await _overrides(state, "observe_submitted")
    state.setdefault("observations", []).append(after)
    state["after"] = after
    if after == state["desired"]:
        state["status"] = "observed_raw_override_change"
    return state


async def destroy_prepared(state):
    """Separate explicit cleanup only for a known standalone owner, never shared ptr."""
    if state["pending"] is not None or state["unsettled_clone"] is not None:
        raise RuntimeError("unresolved delivery/clone requires exact inspection before cleanup")
    owner = state["clone_owner"]
    if owner is not None and owner.command:
        result = await _once(state, "destroy_clone", lambda: owner.destroy(), owner=owner)
        _ok(result, "destroy_clone")
    await release_values(state)
    state["status"] = "explicit_cleanup_complete"
    return state
