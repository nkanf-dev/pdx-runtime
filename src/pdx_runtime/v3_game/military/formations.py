"""Owned formation-management targets, policies, and one-shot commands.

HQ identity is copied only from a genuine retained ``CPdxPtr<CDataHQ>`` list
value.  ``MilitaryFormation.GetHomeHQ`` and ``GetStrategicObjective`` remain
promote-only relations: only scalar chains leave those evaluations.

The registered command schemas used here were recovered from the shipped
2026-10-16 executable's original readers/writers, not inferred from GUI names:

* ``rehome_military_formation``: ``formation: UInt32``, ``home_hq: UInt32``;
* ``designate_strategic_objective``: ``formations: [UInt32]``, ``state: UInt32``.

Borrow-policy keys come from the original populated command's serialized
``borrow_setting`` token.  Source indices and localized labels are never policy
identities.  Rename delegates to the initialized native popup context in
``warrename``; the game's original Confirm preserves its flag and color fields.
"""
from __future__ import annotations

from ... import v3_commands as commands, v3_data as data
from .._text import display_field
from ._warfare import (
    KnownResultError,
    SourceUnavailable,
    _command_result,
    _formation,
    _id_owner,
    _owner_unsettled,
    _produced_owner,
    _token,
    _validate_submit,
    owned_read,
)
from .._run import KnownResultError, SourceUnavailable


async def _native_policy_identity(run, token):
    """Return a native enum token and its copied token-directory name when available."""
    if not hasattr(run.api, "_warformation_policy_tokens"):
        run.api._warformation_policy_tokens = {}
    cache = run.api._warformation_policy_tokens
    key = (run.world, token)
    if key not in cache:
        result = await run.ctx.query("military.borrow_policy.token_name", lambda: commands.token_name(
            run.ctx.native, token, capacity=4096,
            adapter=run.ctx.adapters["commands"], process=run.ctx.process))
        run.operations.append(("borrow_policy.token_name", result))
        raw_name = (result.token_name
                    if isinstance(result.token_name, str) and result.token_name else None)
        truncated = getattr(result, "token_name_truncated", False)
        name = raw_name if not result.error and not truncated else None
        identity = {"policy": name, "policy_token": token}
        if raw_name is not None and name is None:
            identity["policy_name_raw"] = raw_name
        if result.error:
            identity.update(policy_name_native_error=result.error,
                            policy_name_native_result=result,
                            policy_name_error=f"native token-name error {result.error}")
        elif truncated:
            identity.update(policy_name_native_error=122,
                            policy_name_native_result=result,
                            policy_name_error="native token name is truncated")
        elif name is None:
            identity["policy_name_error"] = "native token directory returned no name"
        cache[key] = identity
    return dict(cache[key])


def _complete_id(api, value, label):
    identity = api._native_full_id(value)
    if identity is None:
        raise SourceUnavailable(label + " did not return a complete native ID")
    return identity


def _tree_field(tree, token, label):
    values = []

    def visit(item):
        if isinstance(item, commands.Assignment):
            if item.key == commands.Token(token):
                values.append(item.value)
            visit(item.value)
        elif isinstance(item, tuple):
            for child in item:
                visit(child)

    visit(tree)
    if len(values) != 1:
        raise SourceUnavailable(label + ": populated command field is missing or duplicated")
    return values[0]


async def _serialized_tree(run, owner, label):
    serialized = await _command_result(run, owner, label + ".serialize", owner.serialize)
    if serialized.error:
        raise KnownResultError(label + ".serialize", serialized)
    if _owner_unsettled(owner):
        raise SourceUnavailable(label + ": command serialization ownership is unresolved")
    try:
        return commands.decode_command(serialized.data)
    except ValueError as error:
        raise SourceUnavailable(label + ": populated command wire could not be decoded") from error


async def _borrow_policy_identity(run, owner, formation_id):
    tree = await _serialized_tree(run, owner, "borrow_policy")
    type_token = await _token(run, "military_formation_borrowing")
    if tree.key != commands.Token(type_token):
        raise SourceUnavailable("Borrow option producer returned a different native command type")
    formation_token = await _token(run, "formation")
    formation = _tree_field(tree, formation_token, "formation")
    if formation != commands.UInt32(formation_id):
        raise SourceUnavailable("Borrow option command targets a different formation")
    setting_token = await _token(run, "borrow_setting")
    setting = _tree_field(tree, setting_token, "borrow_setting")
    if not isinstance(setting, commands.Token):
        raise SourceUnavailable("Borrow option identity is not a native policy token")
    return await _native_policy_identity(run, setting.value)


def _policy_matches(identity, wanted):
    return (identity["policy_token"] == wanted if type(wanted) is int else
            identity.get("policy") == wanted)


async def _borrow_row(run, option, formation_id, owner=None):
    made_owner = owner is None
    if owner is None:
        owner = await _produced_owner(run, "MilitaryFormationBorrowSetting.GetBorrowingCommand", (option,))
    try:
        identity = await _borrow_policy_identity(run, owner, formation_id)
        row = {**identity, "unavailable": {}}
        if identity.get("policy_name_error"):
            row["unavailable"]["policy"] = identity["policy_name_error"]
        name, reason = await run.field("MilitaryFormationBorrowSetting", "GetName", (option,),
            (data.Kind.STRING,))
        row.update(display_field(name))
        if reason:
            row["unavailable"]["name"] = reason
        description, reason = await run.field("MilitaryFormationBorrowSetting", "GetDesc", (option,),
            (data.Kind.STRING,))
        row["description"] = description
        if reason:
            row["unavailable"]["description"] = reason
        selected, reason = await run.field("MilitaryFormationBorrowSetting", "IsSelected", (option,),
            (data.Kind.BOOL,))
        row["selected"] = selected
        if reason:
            row["unavailable"]["selected"] = reason
        return row, owner
    except Exception:
        if made_owner and not _owner_unsettled(owner):
            await run.destroy(owner)
        raise


async def _current_borrow_policy(run, formation, formation_id):
    await run.provider("MilitaryFormation", "GetBorrowSettings")
    async for _, _, option in run.elements("MilitaryFormation.GetBorrowSettings", (formation,),
            "MilitaryFormationBorrowSetting"):
        selected, reason = await run.field("MilitaryFormationBorrowSetting", "IsSelected", (option,),
            (data.Kind.BOOL,))
        if reason:
            raise SourceUnavailable(reason)
        if not selected:
            continue
        owner = await _produced_owner(run,
            "MilitaryFormationBorrowSetting.GetBorrowingCommand", (option,))
        try:
            return await _borrow_policy_identity(run, owner, formation_id)
        finally:
            if not _owner_unsettled(owner):
                await run.destroy(owner)
    return None


async def formation_borrow_policies(api, formation_id):
    """List every current native borrowing policy without a static enum whitelist."""
    async def observe(run):
        kind, formation = await _formation(run, formation_id)
        if formation is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
        await run.provider("MilitaryFormation", "GetBorrowSettings")
        policies, details = [], []
        async for _, _, option in run.elements("MilitaryFormation.GetBorrowSettings", (formation,),
                "MilitaryFormationBorrowSetting"):
            owner = None
            try:
                owner = await _produced_owner(run,
                    "MilitaryFormationBorrowSetting.GetBorrowingCommand", (option,))
                row, owner = await _borrow_row(run, option, formation_id, owner)
                policies.append(row)
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                details.append(str(error))
                if owner is not None and _owner_unsettled(owner):
                    break
            finally:
                if owner is not None and not _owner_unsettled(owner) and owner.command:
                    await run.destroy(owner)
        selected = [row for row in policies if row.get("selected") is True]
        current = selected[0] if len(selected) == 1 else None
        return {"formation_id": formation_id, "kind": kind,
                "status": "partial" if details else "observed", "atomic_snapshot": False,
                "selected_policy": current.get("policy") if current else None,
                "selected_policy_token": current.get("policy_token") if current else None,
                "selected_policy_identity": current,
                "policies": policies, "details": details}
    return await owned_read(api, "military.formation_borrow_policies", observe)


async def set_formation_borrow_policy(api, formation_id, policy, *, validate_only=False):
    """Select one current policy by copied native name or raw native enum token."""
    if not ((isinstance(policy, str) and policy) or
            (type(policy) is int and 0 <= policy <= 0xffff)):
        raise ValueError("policy must be a nonempty native token name or uint16 policy token")

    async def operation(run):
        kind, formation = await _formation(run, formation_id)
        if formation is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
        await run.provider("MilitaryFormation", "GetBorrowSettings")
        observed, details = [], []
        async for _, _, option in run.elements("MilitaryFormation.GetBorrowSettings", (formation,),
                "MilitaryFormationBorrowSetting"):
            owner = None
            try:
                owner = await _produced_owner(run,
                    "MilitaryFormationBorrowSetting.GetBorrowingCommand", (option,))
                identity = await _borrow_policy_identity(run, owner, formation_id)
                observed.append(identity)
                if not _policy_matches(identity, policy):
                    await run.destroy(owner)
                    owner = None
                    continue
                selected, reason = await run.field("MilitaryFormationBorrowSetting", "IsSelected",
                    (option,), (data.Kind.BOOL,))
                if reason:
                    raise SourceUnavailable(reason)
                before = identity if selected else await _current_borrow_policy(
                    run, formation, formation_id)
                result = {"formation_id": formation_id, "kind": kind,
                          "policy": identity.get("policy"),
                          "policy_token": identity["policy_token"],
                          "allowed_country_or_policy": identity,
                          "selected_policy": identity.get("policy"),
                          "selected_policy_token": identity["policy_token"],
                          "selected_policy_before": before,
                          "action": "set_formation_borrow_policy", "accepted": None}
                if selected:
                    await run.destroy(owner)
                    return {**result, "status": "unchanged", "eligible": True,
                            "accepted": None, "selected_policy_after": identity,
                            "native_effect": {"submitted": False,
                                              "reason": "policy already selected"}}
                submitted = await _validate_submit(run, owner, result,
                    validate_only=validate_only)
                result["native_effect"] = {
                    "producer": "MilitaryFormationBorrowSetting.GetBorrowingCommand",
                    "accepted": result.get("accepted"),
                }
                if submitted:
                    try:
                        _, current = await _formation(run, formation_id)
                        if current is None:
                            raise SourceUnavailable("Formation left the current owned sources after submission")
                        after = await _current_borrow_policy(run, current, formation_id)
                        result["selected_policy_after"] = after
                        result["observed_selected_policy_matches"] = (
                            after is not None and
                            after["policy_token"] == identity["policy_token"])
                        result["native_effect"]["observed_selected_policy"] = after
                    except (SourceUnavailable, KnownResultError, LookupError) as error:
                        result["observation_error"] = str(error)
                    except Exception as error:
                        run.retain_unknown = True
                        result["observation_error"] = str(error)
                return result
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                details.append(str(error))
                if owner is not None and _owner_unsettled(owner):
                    break
            finally:
                if owner is not None and not _owner_unsettled(owner) and owner.command:
                    await run.destroy(owner)
        selector = ({"policy_token": policy} if type(policy) is int else {"policy": policy})
        return {"formation_id": formation_id, "kind": kind, **selector,
                "status": "not_found", "accepted": None, "observed_policies": observed,
                "details": details,
                "reason": "Requested native borrowing policy was not in the current original source"}
    return await owned_read(api, "military.set_formation_borrow_policy", operation)


async def _hq_summary(run, element, binding):
    identity = _complete_id(run.api, await run.origin(element, "HQ"),
                            "Owned CDataHQ wrapper origin")
    row = {"id": identity, "unavailable": {}}
    fields = await run.functions("Hq")
    name_getter = next((name for name in ("GetNameNoFlagNoConcept", "GetNameNoFlag", "GetName")
                       if name in fields), "GetName")
    name, reason = await run.field("Hq", name_getter, (binding,), (data.Kind.STRING,))
    row.update(display_field(name))
    if reason:
        row["unavailable"]["name"] = reason
    for method, key in (("IsLandHQ", "land"), ("IsNavalHQ", "naval"),
                        ("IsTemporaryHQ", "temporary")):
        value, reason = await run.field("Hq", method, (binding,), (data.Kind.BOOL,))
        row[key] = value
        if reason:
            row["unavailable"][key] = reason
    country, reason = await run.field("Country", "GetID", (binding,),
        (data.Kind.I32, data.Kind.U32), expression="Hq.GetCountry.GetID")
    row["country_id"] = run.api._native_full_id(country)
    if row["country_id"] is None:
        row["unavailable"]["country_id"] = reason or "Hq.GetCountry.GetID returned no complete ID"
    region, reason = await run.field("StrategicRegion", "GetName", (binding,),
        (data.Kind.STRING,), expression="Hq.GetStrategicRegion.GetName")
    row["strategic_region_name_raw"] = region
    if reason:
        row["unavailable"]["strategic_region_name"] = reason
    return row


async def _home_readback(run, formation):
    """Copy scalars through the promote-only relation; never retain its Hq wrapper."""
    valid, reason = await run.field("Hq", "IsValid", (formation,), (data.Kind.BOOL,),
        expression="MilitaryFormation.GetHomeHQ.IsValid")
    if reason:
        return None, reason
    if not valid:
        return None, None
    fields = await run.functions("Hq")
    getter = next((name for name in ("GetNameNoFlagNoConcept", "GetNameNoFlag", "GetName")
                   if name in fields), "GetName")
    name, name_reason = await run.field("Hq", getter, (formation,), (data.Kind.STRING,),
        expression="MilitaryFormation.GetHomeHQ." + getter)
    result = {**display_field(name), "id": None, "unavailable": {
        "id": "GetHomeHQ is a promote-only borrowed relation; no Hq.GetID exists and its wrapper is not retained"}}
    if name_reason:
        result["unavailable"]["name"] = name_reason
    for method, key in (("IsLandHQ", "land"), ("IsNavalHQ", "naval"),
                        ("IsTemporaryHQ", "temporary")):
        value, field_reason = await run.field("Hq", method, (formation,), (data.Kind.BOOL,),
            expression="MilitaryFormation.GetHomeHQ." + method)
        result[key] = value
        if field_reason:
            result["unavailable"][key] = field_reason
    country, field_reason = await run.field("Country", "GetID", (formation,),
        (data.Kind.I32, data.Kind.U32), expression="MilitaryFormation.GetHomeHQ.GetCountry.GetID")
    result["country_id"] = run.api._native_full_id(country)
    if result["country_id"] is None:
        result["unavailable"]["country_id"] = (field_reason or
            "Home HQ country did not return a complete ID")
    return result, None


async def _home_candidates(run, formation_kind):
    country = await run.country()
    method = "AccessLandHQs" if formation_kind == "army" else "AccessNavalHQs"
    await run.provider("Country", method)
    rows = []
    async for _, element, binding in run.elements(
            "Country." + method, (country,), "Hq", wrapper_type="HQ"):
        rows.append(await _hq_summary(run, element, binding))
    return method, rows


async def formation_home_options(api, formation_id):
    """List genuine full-HQ targets and the scalar current-home observation."""
    async def observe(run):
        kind, formation = await _formation(run, formation_id)
        if formation is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
        source, targets = await _home_candidates(run, kind)
        current, reason = await _home_readback(run, formation)
        result = {"formation_id": formation_id, "kind": kind, "status": "observed",
                  "atomic_snapshot": False, "target_source": "Country." + source,
                  "home_targets": targets, "current_home": current}
        if reason:
            result["current_home_unavailable_reason"] = reason
        return result
    return await owned_read(api, "military.formation_home_options", observe)


async def set_formation_home(api, formation_id, hq_id, *, validate_only=False):
    """Validate or rehome one formation to one explicit complete native HQ ref.

    ``formation_home_options`` is discovery only.  This action does not require
    the target to appear in that catalog; the selected native command validator
    decides target ownership, formation kind, path, and current eligibility.
    """
    hq_id = _complete_id(api, hq_id, "hq_id")

    async def operation(run):
        kind, formation = await _formation(run, formation_id)
        if formation is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
        before, before_reason = await _home_readback(run, formation)
        target = {"id": hq_id, "identity": "complete_native_hq_ref"}
        result = {"formation_id": formation_id, "kind": kind, "selected_home_location": target,
                  "home_hq_id": hq_id, "home_before": before, "new_home": None,
                  "action": "rehome_military_formation", "accepted": None}
        if before_reason:
            result["home_before_unavailable_reason"] = before_reason
        owner, created = await _id_owner(run, "rehome_military_formation",
            {"formation": formation_id, "home_hq": hq_id})
        if owner is None:
            result.update(status="native_result", native_error=created.error, native_result=created)
            return result
        submitted = await _validate_submit(run, owner, result, validate_only=validate_only)
        result["eligibility"] = result.get("eligible")
        result["native_effect"] = {"command": "rehome_military_formation",
                                   "accepted": result.get("accepted")}
        if submitted:
            try:
                _, current = await _formation(run, formation_id)
                if current is None:
                    raise SourceUnavailable("Formation left the current owned sources after submission")
                result["new_home"], reason = await _home_readback(run, current)
                result["native_effect"]["observed_home"] = result["new_home"]
                if reason:
                    result["new_home_unavailable_reason"] = reason
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                result["observation_error"] = str(error)
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military.set_formation_home", operation)


async def _strategic_objective(run, formation):
    """Copy a State identity and labels through the promote-only relation."""
    valid, reason = await run.field("State", "IsValid", (formation,), (data.Kind.BOOL,),
        expression="MilitaryFormation.GetStrategicObjective.IsValid")
    if reason:
        return None, reason
    if not valid:
        return None, None
    identity, id_reason = await run.field("State", "GetID", (formation,),
        (data.Kind.I32, data.Kind.U32), expression="MilitaryFormation.GetStrategicObjective.GetID")
    identity = run.api._native_full_id(identity)
    fields = await run.functions("State")
    getter = next((name for name in ("GetNameNoFormatting", "GetName") if name in fields), "GetName")
    name, name_reason = await run.field("State", getter, (formation,), (data.Kind.STRING,),
        expression="MilitaryFormation.GetStrategicObjective." + getter)
    result = {"id": identity, **display_field(name), "unavailable": {}}
    if identity is None:
        result["unavailable"]["id"] = id_reason or "Strategic objective did not return a complete State ID"
    if name_reason:
        result["unavailable"]["name"] = name_reason
    return result, None


async def _strategic_owner(run, formation_id, state_id):
    actor = await run.ctx.country_id()
    type_token = await _token(run, "designate_strategic_objective")
    formations_token = await _token(run, "formations")
    state_token = await _token(run, "state")
    parameters = {
        formations_token: (commands.UInt32(formation_id),),
        state_token: commands.UInt32(state_id),
    }
    created = await run.ctx.query("military.command.create", lambda: commands.create(
        run.ctx.native, type_token, actor_id=actor, world=run.world, parameters=parameters,
        adapter=run.ctx.adapters["commands"], process=run.ctx.process))
    run.operations.append(("create", created))
    if created.error or not created.command:
        if created.command:
            run.unadopted_command = created
        return None, created
    return run.adopt(created, expected_world=created.world), created


async def strategic_objective(api, formation_id, target_state_id=None, *, validate_only=False):
    """Set one army's objective, or clear its currently observed objective with ``None``.

    This setter always encodes exactly one formation.  ``0xffffffff`` is not a
    clear sentinel or a valid State input.  The native command is a toggle for
    one State.  A repeated set request is
    therefore returned as unchanged instead of accidentally clearing it.
    """
    if target_state_id is not None:
        target_state_id = _complete_id(api, target_state_id, "target_state_id")

    async def operation(run):
        kind, formation = await _formation(run, formation_id, kind="army")
        if formation is None:
            raise SourceUnavailable("Army formation is not in the country's current army source")
        before, reason = await _strategic_objective(run, formation)
        if reason:
            raise SourceUnavailable(reason)
        clearing = target_state_id is None
        if clearing:
            if before is None:
                return {"formation_id": formation_id, "kind": kind,
                        "target_state_or_clear": "clear", "objective_before": None,
                        "objective_readback": None, "status": "unchanged", "eligible": True,
                        "accepted": None,
                        "native_effect": {"submitted": False,
                                          "reason": "no current objective"},
                        "action": "clear_strategic_objective"}
            selected_state = before.get("id")
            if selected_state is None:
                raise SourceUnavailable("Current strategic objective has no complete State identity")
            action = "clear_strategic_objective"
        else:
            selected_state = target_state_id
            action = "set_strategic_objective"
            if before is not None and before.get("id") == selected_state:
                return {"formation_id": formation_id, "kind": kind,
                        "target_state_or_clear": selected_state, "objective_before": before,
                        "objective_readback": before, "status": "unchanged", "eligible": True,
                        "accepted": None,
                        "native_effect": {"submitted": False,
                                          "reason": "objective already selected"},
                        "action": action}
        result = {"formation_id": formation_id, "kind": kind,
                  "target_state_or_clear": "clear" if clearing else selected_state,
                  "command_state_id": selected_state, "objective_before": before,
                  "objective_readback": before, "action": action, "accepted": None}
        owner, created = await _strategic_owner(run, formation_id, selected_state)
        if owner is None:
            result.update(status="native_result", native_error=created.error, native_result=created)
            return result
        submitted = await _validate_submit(run, owner, result, validate_only=validate_only)
        result["native_effect"] = {"command": "designate_strategic_objective",
                                   "accepted": result.get("accepted")}
        if submitted:
            try:
                _, current = await _formation(run, formation_id, kind="army")
                if current is None:
                    raise SourceUnavailable("Army left the current owned source after submission")
                result["objective_readback"], read_reason = await _strategic_objective(run, current)
                if read_reason:
                    result["objective_readback_unavailable_reason"] = read_reason
                result["observed_objective_matches"] = (
                    result["objective_readback"] is None if clearing else
                    result["objective_readback"] is not None and
                    result["objective_readback"].get("id") == selected_state)
                result["native_effect"]["objective_readback"] = result["objective_readback"]
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                result["observation_error"] = str(error)
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military.strategic_objective", operation)


async def rename_formation(api, formation_id, new_name, *, validate_only=False):
    """Compatibility name for the original owned-context rename implementation."""
    from .formation_names import formation_rename
    return await formation_rename(api, formation_id, new_name, validate_only=validate_only)
