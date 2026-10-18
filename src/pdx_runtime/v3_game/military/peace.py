"""Structured original war-goal and peace reads plus selected goal toggling.

War goal sources return owned CPdxPtr<CDataWarGoal> values.  Their immutable
wrapper+0x10 origin is the full generation-bearing CWarGoal reference.  A list
index remains local observation metadata and is never an identity or mutation
selector.
"""
from __future__ import annotations

from ... import v3_data as data
from .._text import display_field
from ._warfare import (SourceUnavailable, _copied, _peace_status, _produced_owner,
                        _source_count, _validate_submit, _war, owned_read)
from .._run import KnownResultError, SourceUnavailable, command_field


FEATURE_IDS = ("war.war-goals", "war.peace-read", "war.peace-goal-selection")
_TYPE_KEY_GAP = (
    "WarGoalType exposes an original display name and tooltip tag here, but no "
    "proved definition-key getter; neither display text nor tooltip tag is "
    "reported as a type key."
)


def _complete_id(api, value, expression, reason):
    raw = value
    complete = api._native_full_id(value)
    if complete is None and not reason:
        reason = expression + ": original getter did not return a complete ID"
    return complete, raw, reason


async def command_field(run, named_type, method, bindings, kinds, unavailable, key, *,
                 arguments=0, expression=None):
    value, reason = await run.field(
        named_type, method, bindings, kinds, arguments=arguments, expression=expression
    )
    if reason:
        unavailable[key] = reason
    return value, reason


async def _goal_rows(run, war, country, side):
    method = "GetAttackersWarGoals" if side == "attacker" else "GetDefendersWarGoals"
    await run.provider("War", method)
    source = "War." + method
    rows = []
    async for index, element, goal in run.elements(source, (war,), "WarGoal"):
        goal_id = await run.origin(element, "WarGoal")
        unavailable = {}
        row = {
            "id": goal_id,
            "source_index": index,
            "source_index_scope": "this retained native source observation only",
            "mutation_selector": {"war_goal_id": goal_id},
            "side": side,
            "atomic_snapshot": False,
            "unavailable": unavailable,
        }
        for method_name, key, kinds in (
                ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
                ("GetDesc", "description", (data.Kind.STRING,)),
                ("GetStatusString", "goal_status", (data.Kind.STRING,)),
                ("IsContested", "contested", (data.Kind.BOOL,)),
                ("IsExecuted", "executed", (data.Kind.BOOL,)),
                ("IsPressedInCurrentPeaceDeal", "pressed_in_current_peace", (data.Kind.BOOL,)),
                ("HoldsArticle", "holds_article", (data.Kind.BOOL,))):
            row[key], _ = await command_field(
                run, "WarGoal", method_name, (goal,), kinds, unavailable, key
            )
        row["pressed_in_proposed_peace"], _ = await command_field(
            run, "WarGoal", "IsPressedInPotentialPeaceDeal", (goal, country),
            (data.Kind.BOOL,), unavailable, "pressed_in_proposed_peace",
            arguments=1,
            expression="WarGoal.IsPressedInPotentialPeaceDeal(Country.Self)",
        )
        row.update(display_field(row.pop("name")))

        for relation, key in (("GetHolder", "holder_country_id"),
                              ("GetTarget", "target_country_id")):
            expression = "WarGoal." + relation + ".GetID"
            value, reason = await command_field(
                run, "Country", "GetID", (goal,),
                (data.Kind.I32, data.Kind.U32), unavailable, key,
                expression=expression,
            )
            value, raw, reason = _complete_id(
                run.api, value, "WarGoal." + relation + ".GetID", reason
            )
            row[key] = value
            row[key + "_raw"] = raw
            if reason:
                unavailable[key] = reason

        type_unavailable = {}
        type_name, _ = await command_field(
            run, "WarGoalType", "GetNameNoFormatting", (goal,),
            (data.Kind.STRING,), type_unavailable, "name",
            expression="WarGoal.GetType.GetNameNoFormatting",
        )
        type_tag, _ = await command_field(
            run, "WarGoalType", "GetTooltipTag", (goal,),
            (data.Kind.STRING,), type_unavailable, "tooltip_tag",
            expression="WarGoal.GetType.GetTooltipTag",
        )
        row["goal_type"] = {
            **display_field(type_name),
            "tooltip_tag": type_tag,
            "definition_key": None,
            "definition_key_unavailable_reason": _TYPE_KEY_GAP,
            "unavailable": type_unavailable,
        }

        state_unavailable = {}
        state_valid, state_valid_reason = await command_field(
            run, "State", "IsValid", (goal,), (data.Kind.BOOL,),
            state_unavailable, "valid",
            expression="WarGoal.GetTargetState.IsValid",
        )
        target_state = None
        if state_valid is True:
            state_id, state_id_reason = await command_field(
                run, "State", "GetID", (goal,),
                (data.Kind.I32, data.Kind.U32), state_unavailable, "id",
                expression="WarGoal.GetTargetState.GetID",
            )
            state_id, state_id_raw, state_id_reason = _complete_id(
                run.api, state_id, "WarGoal.GetTargetState.GetID", state_id_reason
            )
            if state_id_reason:
                state_unavailable["id"] = state_id_reason
            state_name, _ = await command_field(
                run, "State", "GetNameNoFormatting", (goal,),
                (data.Kind.STRING,), state_unavailable, "name",
                expression="WarGoal.GetTargetState.GetNameNoFormatting",
            )
            target_state = {
                "id": state_id,
                "id_raw": state_id_raw,
                **display_field(state_name),
                "unavailable": state_unavailable,
            }
        elif state_valid_reason:
            unavailable["target_state"] = state_valid_reason
        row["target_state"] = target_state

        article = None
        if row["holds_article"] is True:
            article_unavailable = {}
            article = {"unavailable": article_unavailable}
            for named_type, method_name, expression, key, kinds in (
                    ("ArticleDraft", "GetArticleNumber", "WarGoal.GetArticleDraft.GetArticleNumber",
                     "number", (data.Kind.I32,)),
                    ("ArticleDraft", "GetNameNoFormatting", "WarGoal.GetArticleDraft.GetNameNoFormatting",
                     "name", (data.Kind.STRING,)),
                    ("ArticleType", "GetNameNoFormatting",
                     "WarGoal.GetArticleDraft.GetArticleType.GetNameNoFormatting",
                     "type_name", (data.Kind.STRING,)),
                    ("ArticleType", "GetTooltipTag",
                     "WarGoal.GetArticleDraft.GetArticleType.GetTooltipTag",
                     "type_tooltip_tag", (data.Kind.STRING,))):
                article[key], _ = await command_field(
                    run, named_type, method_name, (goal,), kinds,
                    article_unavailable, key, expression=expression,
                )
            article.update(display_field(article.pop("name")))
            article.update(display_field(article.pop("type_name"), "type_name"))
            article["type_key"] = None
            article["type_key_unavailable_reason"] = (
                "ArticleType display name and tooltip tag are not a proved definition key"
            )
        row["article"] = article
        row["target_kind"] = (
            "article" if article is not None else
            "state" if target_state is not None else
            "country" if row["target_country_id"] is not None else None
        )
        rows.append(row)
    return rows


async def _all_goal_rows(run, war, country):
    goals, details = {}, []
    for side in ("attacker", "defender"):
        try:
            goals[side] = await _goal_rows(run, war, country, side)
        except (SourceUnavailable, KnownResultError, LookupError) as error:
            goals[side] = None
            details.append({"side": side, "reason": str(error)})
    return goals, details


async def war_goals(api, war_id):
    """Read owned goals with full IDs, type labels, and state/article targets."""
    async def observe(run):
        country, war = await _war(run, war_id)
        goals, details = await _all_goal_rows(run, war, country)
        return {
            "feature_id": "war.war-goals",
            "war_id": war_id,
            "status": "partial" if details else "observed",
            "goals": goals,
            "details": details,
            "coverage": "owned_structured_war_goals",
            "goal_selection_available": not details,
        }
    return await owned_read(api, "military.war_goals", observe)


async def _country_field(run, war, country, method, key, kind, unavailable, *,
                         arguments=1, expression=None, bindings=None):
    expression = expression or "War." + method + "(Country.Self)"
    bindings = bindings or (war, country)
    value, reason = await run.field(
        "War", method, bindings, (kind,), arguments=arguments, expression=expression
    )
    if reason:
        unavailable[key] = reason
    return value


async def _peace_participants(run, war, side):
    method = "GetAttackers" if side == "attacker" else "GetDefenders"
    await run.provider("War", method)
    source = "War." + method
    count = await _source_count(run, source, (war,))
    rows = []
    for index in range(count):
        unavailable = {}
        country_id, reason = await _copied(
            run, source, index, count, "WarParticipant.GetCountry.GetID",
            (war,), (data.Kind.I32, data.Kind.U32),
        )
        country_id, country_id_raw, reason = _complete_id(
            run.api, country_id, "WarParticipant.GetCountry.GetID", reason
        )
        row = {
            "source_index": index,
            "source_index_scope": "this fresh native participant source observation only",
            "country_id": country_id,
            "country_id_raw": country_id_raw,
            "side": side,
            "unavailable": unavailable,
        }
        if reason:
            unavailable["country_id"] = reason
        if country_id is None:
            rows.append(row)
            continue
        country = data.ObjectBinding.country(await run.type_id("Country"), country_id)
        country_fields = await run.functions("Country")
        name_getter = "GetNameNoFormatting" if "GetNameNoFormatting" in country_fields else "GetName"
        name, name_reason = await run.field("Country", name_getter, (country,), (data.Kind.STRING,))
        row.update(display_field(name))
        if name_reason:
            unavailable["name"] = name_reason
        for method_name, key, kind in (
                ("IsPeaceNegotiator", "peace_negotiator", data.Kind.BOOL),
                ("MustRatifyPeaceDeal", "must_ratify", data.Kind.BOOL),
                ("WouldAcceptActivePeaceDeal", "would_accept_active", data.Kind.BOOL),
                ("GetActivePeaceDealAcceptanceScore", "active_acceptance_score", data.Kind.STRING),
                ("GetActivePeaceDealAcceptanceTooltip", "active_acceptance_reason", data.Kind.STRING),
                ("GetCapitulationDesire", "capitulation_desire", data.Kind.I32),
                ("GetCapitulationDesireDesc", "capitulation_desire_reason", data.Kind.STRING)):
            row[key] = await _country_field(
                run, war, country, method_name, key, kind, unavailable
            )
        for method_name, key, kind in (
                ("WouldAcceptProposedPeaceDeal", "would_accept_proposed", data.Kind.BOOL),
                ("GetProposedPeaceDealAcceptanceScore", "proposed_acceptance_score", data.Kind.STRING),
                ("GetProposedPeaceDealAcceptanceTooltip", "proposed_acceptance_reason", data.Kind.STRING)):
            row[key] = await _country_field(
                run, war, country, method_name, key, kind, unavailable,
                arguments=2,
                expression="War." + method_name + "(Country.Self, GetPlayer)",
                bindings=(war, country),
            )
        row["proposed_acceptance_proposer"] = {
            "source": "GetPlayer",
            "semantics": "actual native controlled player used by war_panel.gui",
            "country_id": run.actor,
        }
        rows.append(row)
    return rows


async def peace_read(api, war_id):
    """Read active/proposed terms and participant acceptance as distinct facts."""
    async def observe(run):
        country, war = await _war(run, war_id)
        base = await _peace_status(run, war, country)
        unavailable = base["unavailable"]
        pressed_text, reason = await run.field(
            "War", "GetPressedWarGoalsString", (war,), (data.Kind.STRING,)
        )
        if reason:
            unavailable["pressed_war_goals_description"] = reason
        proposed_white, reason = await run.field(
            "War", "HasProposedWhitePeaceDeal", (war,), (data.Kind.BOOL,),
            arguments=1, expression="War.HasProposedWhitePeaceDeal(GetPlayer)",
        )
        if reason:
            unavailable["has_proposed_white_peace"] = reason

        goal_sides, goal_details = await _all_goal_rows(run, war, country)
        active_terms, proposed_terms = [], []
        active_complete = proposed_complete = not goal_details
        for side in ("attacker", "defender"):
            for goal in goal_sides.get(side) or ():
                if goal["pressed_in_current_peace"] is True:
                    active_terms.append(goal)
                elif goal["pressed_in_current_peace"] is None:
                    active_complete = False
                if goal["pressed_in_proposed_peace"] is True:
                    proposed_terms.append(goal)
                elif goal["pressed_in_proposed_peace"] is None:
                    proposed_complete = False

        participants, participant_details = [], []
        for side in ("attacker", "defender"):
            try:
                participants.extend(await _peace_participants(run, war, side))
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                participant_details.append({"side": side, "reason": str(error)})
        details = goal_details + participant_details
        return {
            "feature_id": "war.peace-read",
            "war_id": war_id,
            "status": "partial" if details or not active_complete or not proposed_complete else "observed",
            **base,
            "pressed_war_goals_description": pressed_text,
            "pressed_war_goals_description_scope": (
                "original native pressed-goals text; never parsed as identity"
            ),
            "active_terms": active_terms if active_complete else None,
            "active_terms_complete": active_complete,
            "proposed_terms": proposed_terms if proposed_complete else None,
            "proposed_terms_complete": proposed_complete,
            "all_term_goals": goal_sides,
            "has_proposed_white_peace": proposed_white,
            "participants": participants if not participant_details or participants else None,
            "participant_coverage": (
                "native_side_lists" if not participant_details else "partial_native_side_lists"
            ),
            "details": details,
            "coverage": "owned_active_and_proposed_terms_plus_participant_acceptance",
        }
    return await owned_read(api, "military.peace_read", observe)


async def _selected_goal(run, war, wanted):
    found = []
    for side, method in (("attacker", "GetAttackersWarGoals"),
                         ("defender", "GetDefendersWarGoals")):
        await run.provider("War", method)
        source = "War." + method
        async for index, element, goal in run.elements(source, (war,), "WarGoal"):
            goal_id = await run.origin(element, "WarGoal")
            if goal_id == wanted:
                found.append((side, index, goal))
    if len(found) > 1:
        raise SourceUnavailable("The original War sources returned the same complete WarGoal ID more than once")
    return found[0] if found else None


async def set_goal_pressed(api, war_id, goal_id, desired_pressed):
    """Set one proved owned WarGoal's proposed pressed state with one toggle at most."""
    if type(desired_pressed) is not bool:
        raise ValueError("desired_pressed must be bool")

    async def operation(run):
        country, war = await _war(run, war_id)
        selected = await _selected_goal(run, war, goal_id)
        if selected is None:
            return {
                "feature_id": "war.peace-goal-selection",
                "war_id": war_id,
                "war_goal_id": goal_id,
                "desired_pressed": desired_pressed,
                "status": "not_found",
                "accepted": None,
                "submitted": False,
                "reason": "Complete WarGoal ID was not present in the selected War's original goal sources",
            }
        side, source_index, goal = selected
        current, reason = await run.field(
            "WarGoal", "IsPressedInPotentialPeaceDeal", (goal, country),
            (data.Kind.BOOL,), arguments=1,
            expression="WarGoal.IsPressedInPotentialPeaceDeal(Country.Self)",
        )
        result = {
            "feature_id": "war.peace-goal-selection",
            "war_id": war_id,
            "war_goal_id": goal_id,
            "side": side,
            "source_index": source_index,
            "source_index_scope": "observation only; selection used the complete WarGoal ID",
            "desired_pressed": desired_pressed,
            "previous_pressed": current,
            "previous_pressed_unavailable_reason": reason,
            "action": "set_goal_pressed",
            "accepted": None,
            "submitted": False,
        }
        if reason:
            result.update(status="unavailable", reason=reason)
            return result
        if current is desired_pressed:
            result.update(status="unchanged", submitted=False, changed=False,
                          observed_pressed=current, observed_matches_desired=True)
            return result

        owner = await _produced_owner(
            run, "WarGoal.ToggleIsPressedInPotentialPeaceDeal(Country.Self)",
            (goal, country),
        )
        result["producer_selected"] = True
        accepted = await _validate_submit(run, owner, result)
        result["submitted"] = any(label == "command.submit" for label, _ in run.operations)
        if accepted:
            try:
                observed, observed_reason = await run.field(
                    "WarGoal", "IsPressedInPotentialPeaceDeal", (goal, country),
                    (data.Kind.BOOL,), arguments=1,
                    expression="WarGoal.IsPressedInPotentialPeaceDeal(Country.Self)",
                )
                result["observed_pressed"] = observed
                result["observed_pressed_unavailable_reason"] = observed_reason
                result["observed_matches_desired"] = (
                    observed is desired_pressed if observed_reason is None else None
                )
                result["changed"] = (
                    observed is desired_pressed if observed_reason is None else None
                )
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
                result["observed_matches_desired"] = None
                result["changed"] = None
        return result

    return await owned_read(api, "military.set_goal_pressed", operation)
