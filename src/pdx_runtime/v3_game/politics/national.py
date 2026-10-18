"""National identity, modifiers, colony progress, and country-creation catalogs.

All fields use registered native Data expressions. Reads copy scalar/text pages;
they do not generate commands or bind foreign borrowed wrapper pointers. A
catalog source_index identifies one observed occurrence, never a country key.
"""
from __future__ import annotations

from ... import v3_data as data
from .._choices import OwnedChoice, OwnedChoices
from .._political_data import (
    Field, binding, resolve_country_id, country_snapshot,
    country_source, project, snapshot, state_snapshot,
)
from .. import _run


def _text(name, expression):
    return Field(name, expression, data.Kind.STRING, text=True)


_IDENTITY = (
    _text("name", "Country.GetName"),
    Field("tag", "Country.GetTagName", data.Kind.STRING),
    _text("adjective", "Country.GetAdjectiveNoFormatting"),
    # Keep the original flag icon token; it is not a texture pointer or asset ID.
    Field("flag_text_icon", "Country.GetFlagTextIcon", data.Kind.STRING),
    # Native vector-to-string conversion, not a guessed CVector4f layout.
    Field("map_color", "GetString_CVector4f(Country.GetMapColor)", data.Kind.STRING),
    _text("primary_cultures", "Country.GetPrimaryCulturesDesc"),
    _text("state_religion", "Country.GetStateReligion.GetName"),
    _text("state_religion_description", "Country.GetStateReligion.GetDesc"),
    _text("government", "Country.GetGovernment.GetName"),
    _text("government_description", "Country.GetGovernment.GetDesc"),
    _text("transfer_of_power", "Country.GetGovernment.GetTransferOfPowerName"),
    _text("transfer_of_power_description", "Country.GetGovernment.GetTransferOfPowerDesc"),
)

_MODIFIER_SUMMARY = (
    _text("name", "Country.GetModifier.GetName"),
    _text("description", "Country.GetModifier.GetDesc"),
    Field("is_empty", "Country.GetModifier.IsEmpty", data.Kind.BOOL),
)
_MODIFIER_ENTRIES = (
    _text("name", "ModifierEntry.GetName"),
    Field("value", "ModifierEntry.GetValue", data.Kind.FIXED),
    _text("formatted_value", "ModifierEntry.GetFormattedValue"),
    _text("description", "ModifierEntry.GetDesc"),
    _text("breakdown", "ModifierEntry.GetBreakdown"),
    _text("value_with_breakdown", "ModifierEntry.GetValueWithBreakdown"),
    _text("tooltip", "ModifierEntry.GetTooltip"),
)
_TIMED_MODIFIERS = (
    _text("name", "TimedModifier.GetName"),
    _text("description", "TimedModifier.GetDesc"),
    _text("one_line_description", "TimedModifier.GetOneLineDesc"),
    Field("remaining_days", "TimedModifier.GetNumRemainingDays", data.Kind.I32),
    _text("remaining_duration", "TimedModifier.GetRemainingDuration"),
    _text("time_left", "TimedModifier.GetTimeLeftFormatted"),
    Field("remaining_fraction", "TimedModifier.GetPercentageRemainingDays", data.Kind.FLOAT),
    Field("is_decaying", "TimedModifier.IsDecaying", data.Kind.BOOL),
    Field("start_date", "TimedModifier.GetStartDate.GetString", data.Kind.STRING),
    Field("end_date", "TimedModifier.GetEndDate.GetString", data.Kind.STRING),
    _text("tooltip", "TimedModifier.GetTooltip"),
)

_STATE_INDEX = (
    Field("id", "State.GetID", data.Kind.I32, full_id=True),
    _text("name", "State.GetName"),
    Field("is_being_colonized", "State.IsBeingColonized", data.Kind.BOOL),
    Field("is_growing_colony", "State.IsGrowingColony", data.Kind.BOOL),
)
_COLONY_FIELDS = (
    *_STATE_INDEX,
    Field("is_valid", "State.IsValid", data.Kind.BOOL),
    _text("state_region", "State.GetStateRegion.GetName"),
    Field("owner_id", "State.GetOwner.GetID", data.Kind.I32, full_id=True),
    _text("owner_name", "State.GetOwner.GetName"),
    Field("progress", "State.GetColonyProgress", data.Kind.FIXED),
    Field("daily_progress", "State.GetDailyColonyProgress", data.Kind.FIXED),
    Field("progress_days", "State.GetColonyProgressAsDays", data.Kind.I32),
    _text("growth_date", "State.GetColonyGrowthDateDesc"),
    _text("growth_modifiers", "State.GetColonyGrowthModifierDesc"),
    _text("province_progress", "State.GetColonyProvinceProgressDesc"),
    Field("paused", "State.IsColonizationPaused", data.Kind.BOOL),
    Field("blocked_by_inactive_interest", "State.IsColonyProgressBlockedByInactiveInterest", data.Kind.BOOL),
    Field("is_company_colony", "State.IsCompanyColony", data.Kind.BOOL),
    Field("colonizer_count", "State.GetNumColonizers", data.Kind.I32),
    # Arg0 is a localization template, NOT a country ID (native UI tooltip).
    _text("colonizers", "State.GetColonizers('STATE_REGION_COLONIZER_ITEM')"),
    Field("target_country_id", "State.GetNextColonyProvince.GetState.GetOwner.GetID", data.Kind.I32, full_id=True),
    _text("target_country_name", "State.GetNextColonyProvince.GetState.GetOwner.GetName"),
    Field("tension", "Country.GetTensionWith(State.GetNextColonyProvince.GetState.GetOwner)", data.Kind.I32),
    _text("tension_description", "Country.GetTensionWithDesc(State.GetNextColonyProvince.GetState.GetOwner)"),
    _text("tension_tooltip", "Country.GetTensionTooltip(State.GetNextColonyProvince.GetState.GetOwner)"),
)

_COLONIZATION_OPTIONS = (
    *_STATE_INDEX,
    Field("actor_country_id", "GetPlayer.GetID", data.Kind.I32, full_id=True),
    _text("actor_name", "GetPlayer.GetName"),
    Field("is_valid", "State.IsValid", data.Kind.BOOL),
    Field("owner_id", "State.GetOwner.GetID", data.Kind.I32, full_id=True),
    _text("owner_name", "State.GetOwner.GetName"),
    Field("region_id", "State.GetStateRegion.GetID", data.Kind.I32, full_id=True),
    _text("region_name", "State.GetStateRegion.GetName"),
    _text("colonize_tooltip", "State.GetStateRegion.GetColonizeTooltip"),
    Field("colonizer_count", "State.GetNumColonizers", data.Kind.I32),
    _text("colonizers", "State.GetColonizers('STATE_REGION_COLONIZER_ITEM')"),
    Field("paused", "State.IsColonizationPaused", data.Kind.BOOL),
    Field("blocked_by_inactive_interest", "State.IsColonyProgressBlockedByInactiveInterest", data.Kind.BOOL),
    Field("progress", "State.GetColonyProgress", data.Kind.FIXED),
    Field("daily_progress", "State.GetDailyColonyProgress", data.Kind.FIXED),
)
_PLAYER_COLONY_STATES = (
    *_STATE_INDEX,
    Field("actor_country_id", "GetPlayer.GetID", data.Kind.I32, full_id=True),
    Field("owner_id", "State.GetOwner.GetID", data.Kind.I32, full_id=True),
    Field("region_id", "State.GetStateRegion.GetID", data.Kind.I32, full_id=True),
    _text("region_name", "State.GetStateRegion.GetName"),
    Field("paused", "State.IsColonizationPaused", data.Kind.BOOL),
    Field("progress", "State.GetColonyProgress", data.Kind.FIXED),
    Field("daily_progress", "State.GetDailyColonyProgress", data.Kind.FIXED),
)

_FORMATION_FIELDS = (
    _text("name", "CountryFormation.GetName"),
    _text("definition_name", "CountryFormation.GetDefinition.GetName"),
    _text("primary_culture", "CountryFormation.GetDefinition.GetPrimaryCultureName"),
    _text("cultures", "CountryFormation.ListCultures"),
    Field("is_major_formation", "CountryFormation.IsMajorFormation", data.Kind.BOOL),
    Field("can_form", "CountryFormation.CanFormCountry(Country.Self)", data.Kind.BOOL),
    _text("formation_conditions", "CountryFormation.GetFormDesc(Country.Self)"),
    Field("controlled_states", "CountryFormation.GetNumControlledStates(Country.Self)", data.Kind.I32),
    Field("required_states", "CountryFormation.GetNumRequiredStates", data.Kind.I32),
    Field("total_states", "CountryFormation.GetTotalNumStates", data.Kind.I32),
    _text("required_states_description", "CountryFormation.ListRequiredStates(Country.Self)"),
    Field("is_unification_candidate", "CountryFormation.IsCandidateForFormation(Country.Self)", data.Kind.BOOL),
    Field("supports_any_candidate", "CountryFormation.IsSupportingAnyCandidate(Country.Self)", data.Kind.BOOL),
    _text("candidate_support", "CountryFormation.GetSupportForAllCandidatesDesc(Country.Self)"),
    Field("supporter_count", "CountryFormation.GetNumSupportingCountries(Country.Self)", data.Kind.I32),
    _text("supporters", "CountryFormation.GetSupportingCountriesDesc(Country.Self)"),
    Field("candidate_count", "GetDataModelSize(CountryFormation.AccessFormationCandidates)", data.Kind.I32),
    Field("state_region_count", "GetDataModelSize(CountryFormation.AccessFormationStateRegions)", data.Kind.I32),
    Field("culture_country_count", "GetDataModelSize(CountryFormation.AccessFormationCultureCountries)", data.Kind.I32),
)
# These labels are evaluated again inside each ORIGINAL nested provider row.
# They do not certify or join that parent to an earlier source occurrence.
_FORMATION_PARENT = (
    _text("formation_name", "CountryFormation.GetName"),
    _text("formation_definition_name", "CountryFormation.GetDefinition.GetName"),
    _text("formation_cultures", "CountryFormation.ListCultures"),
    Field("formation_required_states", "CountryFormation.GetNumRequiredStates", data.Kind.I32),
    Field("formation_total_states", "CountryFormation.GetTotalNumStates", data.Kind.I32),
)
_FORMATION_COUNTRIES = (
    *_FORMATION_PARENT,
    Field("country_id", "Country.GetID", data.Kind.I32, full_id=True),
    _text("country_name", "Country.GetName"),
    Field("country_tag", "Country.GetTagName", data.Kind.STRING),
    Field("is_unification_candidate", "CountryFormation.IsCandidateForFormation(Country.Self)", data.Kind.BOOL),
    Field("supporter_count", "CountryFormation.GetNumSupportingCountries(Country.Self)", data.Kind.I32),
    _text("supporters", "CountryFormation.GetSupportingCountriesDesc(Country.Self)"),
    Field("supports_any_candidate", "CountryFormation.IsSupportingAnyCandidate(Country.Self)", data.Kind.BOOL),
    _text("candidate_support", "CountryFormation.GetSupportForAllCandidatesDesc(Country.Self)"),
    Field("controlled_states", "CountryFormation.GetNumControlledStates(Country.Self)", data.Kind.I32),
)
_FORMATION_REGIONS = (
    *_FORMATION_PARENT,
    Field("state_region_id", "StateRegion.GetID", data.Kind.I32, full_id=True),
    _text("state_region_name", "StateRegion.GetName"),
    Field("controlled", "CountryFormation.ControlsStateRegion(Country.Self,StateRegion.Self)", data.Kind.BOOL),
    _text("control_conditions", "CountryFormation.GetControlsStateRegionDesc(Country.Self,StateRegion.Self)"),
    Field("split_state", "StateRegion.IsSplitState", data.Kind.BOOL),
    Field("largest_owner_id", "StateRegion.GetLargestStateOwner.GetID", data.Kind.I32, full_id=True),
    _text("largest_owner_name", "StateRegion.GetLargestStateOwner.GetName"),
)
_UNIFICATION_SUPPORT = (
    Field("actor_country_id", "GetPlayer.GetID", data.Kind.I32, full_id=True),
    _text("actor_name", "GetPlayer.GetName"),
    Field("candidate_country_id", "Country.GetID", data.Kind.I32, full_id=True),
    _text("candidate_name", "Country.GetName"),
    _text("formation_name", "CountryFormation.GetName"),
    Field("supports_candidate", "CountryFormation.IsSupportingCandidate(GetPlayer, Country.Self)", data.Kind.BOOL),
    Field("supports_any_candidate", "CountryFormation.IsSupportingAnyCandidate(GetPlayer)", data.Kind.BOOL),
    Field("supported_candidate_id", "CountryFormation.GetSupportedCandidateCountry(GetPlayer).GetID", data.Kind.I32, full_id=True),
    _text("supported_candidate_name", "CountryFormation.GetSupportedCandidateCountry(GetPlayer).GetName"),
    _text("support_description", "CountryFormation.GetSupportCandidateDesc(GetPlayer, Country.Self)"),
)

_RELEASE_FIELDS = (
    _text("name", "CountryCreation.GetName"),
    _text("definition_name", "CountryCreation.GetDefinition.GetName"),
    _text("primary_culture", "CountryCreation.GetDefinition.GetPrimaryCultureName"),
    _text("cultures", "CountryCreation.ListCultures"),
    _text("capital_state_region", "CountryCreation.GetCapital.GetName"),
    Field("is_valid", "CountryCreation.IsValid", data.Kind.BOOL),
    Field("can_release", "CountryCreation.CanReleaseCountry(Country.Self)", data.Kind.BOOL),
    _text("release_conditions", "CountryCreation.GetReleaseDesc(Country.Self)"),
    Field("controlled_states", "CountryCreation.GetNumControlledStates(Country.Self)", data.Kind.I32),
    Field("releasable_states_count", "CountryCreation.GetNumReleasableStates(Country.Self)", data.Kind.I32),
    Field("required_states_count", "CountryCreation.GetNumRequiredStates", data.Kind.I32),
    Field("total_states", "CountryCreation.GetTotalNumStates", data.Kind.I32),
    Field("specific_provinces_only", "CountryCreation.IsReleasedInSpecificProvinces", data.Kind.BOOL),
    Field("provinces_to_release", "CountryCreation.GetNumProvincesToRelease", data.Kind.I32),
    _text("releasable_states", "CountryCreation.ListReleasableStates(Country.Self)"),
    _text("releasable_states_without_homeland", "CountryCreation.ListReleasableStatesWithoutHomeland(Country.Self)"),
    _text("potential_releasable_states", "CountryCreation.ListPotentialReleasableStates(Country.Self)"),
)


_RELEASE_WINDOW_FIELDS = (
    Field("make_independent", "ReleaseCountryWindow.WillMakeIndependent", data.Kind.BOOL),
    Field("make_own_subject", "ReleaseCountryWindow.WillMakeOwnSubject", data.Kind.BOOL),
    Field("play_as_released", "ReleaseCountryWindow.WillPlayAsReleased", data.Kind.BOOL),
    Field("keep_homeland_states", "ReleaseCountryWindow.WillKeepHomelandStates", data.Kind.BOOL),
    Field("can_make_independent", "ReleaseCountryWindow.CanMakeIndependent", data.Kind.BOOL),
    Field("can_make_own_subject", "ReleaseCountryWindow.CanMakeOwnSubject", data.Kind.BOOL),
    Field("subject_type_to_make_own_subject_exists", "ReleaseCountryWindow.SubjectTypeToMakeOwnSubjectExists", data.Kind.BOOL),
    Field("can_toggle_independent", "And(ReleaseCountryWindow.CanMakeIndependent,ReleaseCountryWindow.CanMakeOwnSubject)", data.Kind.BOOL),
    Field("can_play_as_released", "ReleaseCountryWindow.CanPlayAsReleased", data.Kind.BOOL),
    Field("can_keep_homeland_states", "ReleaseCountryWindow.CanReleaseWithKeepHomelandStates", data.Kind.BOOL),
    _text("independent_tooltip", "ReleaseCountryWindow.GetMakeIndependentTooltip"),
    _text("subject_tooltip", "ReleaseCountryWindow.GetMakeOwnSubjectTooltip"),
    _text("play_as_tooltip", "ReleaseCountryWindow.GetPlayAsReleasedTooltip"),
    _text("keep_homeland_tooltip", "ReleaseCountryWindow.GetKeepHomelandStatesTooltip"),
    _text("question", "ReleaseCountryWindow.GetPopupQuestion"),
    _text("creation_name", "ReleaseCountryWindow.GetCountryCreation.GetName"),
    _text("definition_name", "ReleaseCountryWindow.GetCountryCreation.GetDefinition.GetName"),
    _text("cultures", "ReleaseCountryWindow.GetCountryCreation.ListCultures"),
    _text("releasable_states", "ReleaseCountryWindow.GetCountryCreation.ListReleasableStates(GetPlayer)"),
    _text("releasable_states_without_homeland", "ReleaseCountryWindow.GetCountryCreation.ListReleasableStatesWithoutHomeland(GetPlayer)"),
    Field("actor_country_id", "GetPlayer.GetID", data.Kind.I32, full_id=True),
    _text("actor_name", "GetPlayer.GetName"),
    Field("actor_tag", "GetPlayer.GetTagName", data.Kind.STRING),
)
_RELEASE_STATES = (
    Field("id", "State.GetID", data.Kind.I32, full_id=True),
    _text("name", "State.GetName"),
    Field("owner_id", "State.GetOwner.GetID", data.Kind.I32, full_id=True),
    Field("region_id", "State.GetStateRegion.GetID", data.Kind.I32, full_id=True),
    _text("region_name", "State.GetStateRegion.GetName"),
)
_RELEASE_PLAYER = (
    Field("id", "GetPlayer.GetID", data.Kind.I32, full_id=True),
    _text("name", "GetPlayer.GetName"),
    Field("tag", "GetPlayer.GetTagName", data.Kind.STRING),
)
_RELEASE_TOGGLES = (
    ("make_independent", "can_toggle_independent", "ReleaseCountryWindow.ToggleMakeIndependent"),
    ("make_own_subject", "can_make_own_subject", "ReleaseCountryWindow.ToggleMakeOwnSubject"),
    ("play_as_released", "can_play_as_released", "ReleaseCountryWindow.TogglePlayAsReleased"),
    ("keep_homeland_states", "can_keep_homeland_states", "ReleaseCountryWindow.ToggleKeepHomelandStates"),
)


def _release_value(observation, name):
    rows = observation.get("rows", ())
    if not rows or name in rows[0].get("reasons", {}):
        return None
    return rows[0].get(name)


def _release_bool(observation, name):
    value = _release_value(observation, name)
    return value if type(value) is bool else None


_PAUSED_FIELD = (Field("paused", "State.IsColonizationPaused", data.Kind.BOOL),)
_ABANDON_FIELDS = (
    Field("is_valid", "State.IsValid", data.Kind.BOOL),
    Field("is_being_colonized", "State.IsBeingColonized", data.Kind.BOOL),
    Field("is_growing_colony", "State.IsGrowingColony", data.Kind.BOOL),
    Field("owner_id", "State.GetOwner.GetID", data.Kind.I32, full_id=True),
)


def _action_state(state):
    value = state.get("id") if isinstance(state, dict) else state
    if not _native_id(value):
        raise ValueError("colony actions require a full native state ID or an observed row with an ID")
    return value


async def _colony_action(api, label, state_id, country_id, fields, expression, *, paused=None):
    # The complete toggle workflow owns the existing political mutation lock.
    # A retained ctx.perform coroutine keeps it through pending read/submission.
    from .._political_actions import selected_command

    async with api._mutation_lock:
        selected = await resolve_country_id(api._ctx, country_id)
        before = await state_snapshot(api, label + ".before", fields, state_id=state_id,
                                      country_id=selected, perform=False)
        base = {"action": label.rsplit(".", 1)[-1], "country_id": selected,
                "state_id": state_id, "before": before, "after": None,
                "native_pages": list(before["native_pages"]), "effect_observed": None}
        if paused is not None:
            base["desired_paused"] = paused
            if not before["complete"] or type(before.get("paused")) is not bool:
                return dict(base, status="unavailable", submitted=False, accepted=None,
                            reason="Native paused state is unavailable; no toggle was submitted")
            if before["paused"] is paused:
                return dict(base, status="unchanged", submitted=False, accepted=None,
                            effect_observed=True, reason=None)
        country_binding = await binding(api._ctx, "Country", selected)
        state_binding = await binding(api._ctx, "State", state_id)
        outcome = await selected_command(api, label.rsplit(".", 1)[-1], expression, (country_binding, state_binding),
                                         actor_id=selected, perform=False)
        result = {**outcome, **base}
        if outcome.get("accepted") is not True:
            return result
        # Readback is separate evidence, never a reason to replay a delivered
        # native command or suppress its actual acceptance/validation receipts.
        try:
            after = await state_snapshot(api, label + ".after", fields, state_id=state_id,
                                         country_id=selected, perform=False)
        except Exception as error:
            result["readback_error"] = error
            result["readback_complete"] = False
            return result
        result["after"] = after
        result["native_pages"].extend(after["native_pages"])
        result["readback_complete"] = after["complete"]
        if paused is not None:
            if after["complete"]:
                result["effect_observed"] = after.get("paused") is paused
        else:
            # A vanished state can have a valid IsValid=False observation while
            # its now-absent owner getter fails. Preserve both original facts;
            # do not demand an unrelated successful owner read to report loss.
            field_errors = after.get("reasons", {}).get("fields", {})
            def known(name):
                return name in after and name not in field_errors
            owner_known = known("owner_id") and _native_id(after.get("owner_id"))
            flags = ("is_being_colonized", "is_growing_colony")
            if (known("is_valid") and after["is_valid"] is False or
                    owner_known and after["owner_id"] != selected or
                    all(known(name) and after[name] is False for name in flags)):
                result["effect_observed"] = True
            elif (known("is_valid") and after["is_valid"] is True and owner_known and
                    after["owner_id"] == selected and any(known(name) and after[name] is True for name in flags)):
                result["effect_observed"] = False
        return result


def _merge_observations(country_id, observations):
    """Combine copied read metadata without swallowing native/cleanup evidence."""
    complete = all(item.get("complete", False) for item in observations)
    result = {"country_id": country_id, "complete": complete,
              "status": "observed" if complete else "partial", "atomic_snapshot": False,
              "native_pages": [page for item in observations for page in item.get("native_pages", ())],
              "reasons": [item.get("reasons") for item in observations if not item.get("complete", False)]}
    warnings = [warning for item in observations for warning in item.get("cleanup_warnings", ())]
    if warnings:
        result["cleanup_warnings"] = warnings
    return result


def _native_id(value):
    return type(value) is int and 0 <= value < 0xffffffff


def _selector(state):
    if isinstance(state, dict):
        state = state.get("id")
    if _native_id(state) or isinstance(state, str) and state.strip():
        return state
    raise ValueError("state must be a full native ID, exact name, or observed row with an ID")


async def _state_pages(api, selected, ids):
    root = await binding(api._ctx, "Country", selected)
    observations, rows = [], []
    for start in range(0, len(ids), 64):
        state_ids = ids[start:start + 64]
        bindings = [(root, await binding(api._ctx, "State", value)) for value in state_ids]
        observed = await project(api, "politics.colony_details", _COLONY_FIELDS, bindings, perform=False)
        observations.append(observed)
        # The requested ID is selection context, not a replacement for GetID.
        # Preserve even a missing row; native absence must not erase a colony.
        for offset, requested in enumerate(state_ids):
            if offset < len(observed["rows"]):
                row = dict(observed["rows"][offset])
                row.pop("source_index", None)
            else:
                row = {field.name: None for field in _COLONY_FIELDS}
                row["reasons"] = {"row": "native page did not return the selected state"}
                observed["complete"] = False
                observed["reasons"].append({"requested_state_id": requested,
                    "reason": "native page did not return the selected state"})
            row["requested_state_id"] = requested
            rows.append(row)
    result = _merge_observations(selected, observations)
    result["rows"] = rows
    return result


async def _colonization_options(api, state_ids):
    observations, rows = [], []
    for start in range(0, len(state_ids), 64):
        batch = state_ids[start:start + 64]
        bindings = [(await binding(api._ctx, "State", state_id),) for state_id in batch]
        observed = await project(api, "politics.colonization_options", _COLONIZATION_OPTIONS,
                                 bindings, perform=False)
        observations.append(observed)
        for offset, requested in enumerate(batch):
            if offset < len(observed["rows"]):
                row = dict(observed["rows"][offset])
                row.pop("source_index", None)
            else:
                row = {field.name: None for field in _COLONIZATION_OPTIONS}
                row["reasons"] = {"row": "native page did not copy this requested state"}
                observed["complete"] = False
                observed["reasons"].append({"requested_state_id": requested, "reason": row["reasons"]["row"]})
            row["requested_state_id"] = requested
            rows.append(row)
    result = _merge_observations(None, observations)
    result.pop("country_id")  # The actor is original GetPlayer, observed in each row.
    result.update(rows=rows, actor_source="GetPlayer", requested_state_ids=list(state_ids),
                  requested_state_count=len(state_ids), command_validation_performed=False)
    if not state_ids:
        result["status"] = "empty_selection"
    return result


async def _colonization_observation(api, label, state_binding):
    """Copy the explicit target and original player-state provider independently."""
    observed = {"target_state": None, "player_states": None}
    try:
        observed["target_state"] = await snapshot(api, label + ".target", _COLONIZATION_OPTIONS,
                                                  ((state_binding,),), perform=False)
        observed["player_states"] = await project(api, label + ".player_states", _PLAYER_COLONY_STATES,
            ((),), source_expression="GetPlayer.AccessStates", perform=False)
    except Exception as error:
        # Preserve any earlier completed copy, including after native acceptance.
        error.colonization_observation = observed
        raise
    return observed


async def _formation_children(api, label, parent, source_expression, fields):
    """Separate nested observations; an ordinal request is not a parent identity."""
    observations = []
    for row in parent["rows"]:
        index = row["source_index"]
        step = data.CreationStep(parent["source_expression"], index, parent["source_count"])
        observed = await country_source(api, label, source_expression, fields,
            country_id=parent["country_id"], steps=(step,), perform=False)
        observed["requested_parent_occurrence"] = index
        observed["parent_identity_semantics"] = (
            "formation_* fields identify the actual parent label in each same-callback child row; "
            "not a stable ID or a join to an earlier catalog occurrence")
        if not observed["rows"]:
            observed["parent_label_available"] = False
            observed["parent_label_reason"] = "No child row copied a current parent label; the requested ordinal is not identity"
        observations.append(observed)
    return observations


async def _release_context(run, lease, result):
    """Materialize and prove one original release context under the shared owner."""
    lease.attach(run)
    made = await run.capture("release.context", lambda: data.create_release_country_context(
        run.native, lease.binding.object, output_bytes=4096,
        adapter=run.adapters["data"], process=run.process))
    result.update(context_result=made, native_result=made, native_error=made.error)
    if made.error not in (0, data.ERROR_MORE_DATA):
        result["status"] = "native_result"
        return None
    try:
        return run.owned_binding(made, "ReleaseCountryWindow")
    except RuntimeError as error:
        result.update(status="native_result", reason=str(error))
        return None


async def _choice_snapshot(api, lease, label, fields, bindings):
    """Tie a copied read that borrows a choice to an ordinary retained _run.Run."""

    owner = _run.Run(api._ctx, label)
    lease.attach(owner)
    try:
        result = await snapshot(api, label, fields, (bindings,), owner=owner, perform=False)
    except BaseException as error:
        # Canonical capture already distinguishes a local pre-dispatch error
        # from an original uncertain query. Settle only a known-empty scope.
        owner._forget_if_empty(None)
        error.choices = lease.choice._catalog
        error.read_owner = owner
        raise
    await owner.finish(result)
    return result


async def _creation_choices(api, label, source_expression, named_type, fields, country_id):
    """Country-creation catalog composition over the shared original owner."""

    async def read():
        run = _run.Run(api._ctx, label)
        observation = {"country_id": None, "status": "building", "complete": False,
                       "source_count": None, "native_pages": [], "reasons": []}
        selection = {"actor_country_id": None, "source_country_id": None}
        catalog = OwnedChoices(run, metadata={
            "source_expression": source_expression, "selection": selection, "read": observation,
            "filtering": {"native_source_is_country_scoped": True,
                          "python_row_filter_applied": False,
                          "command_eligibility_filter_applied": False},
            "completeness_semantics": "read.complete covers copied descriptions, not command eligibility or full action support",
            "source_index_semantics": "copied occurrence only; actions use original retained bindings"})
        try:
            selected = await resolve_country_id(api._ctx, country_id)
            run.actor = selected
            selection.update(actor_country_id=selected, source_country_id=selected)
            observation["country_id"] = selected
            root = await binding(api._ctx, "Country", selected)
            elements = []
            async for index, element, retained in run.elements(
                    source_expression, (root,), named_type):
                elements.append((index, element, retained))
            observation["source_count"] = len(elements)
            pages_complete = True
            for start in range(0, len(elements), 64):
                batch = elements[start:start + 64]
                # Explicit retained input rows, NOT another source lookup.
                # The original source and all owned CPdx Any values stay held.
                try:
                    page = await project(api, "politics." + label + ".descriptions", fields,
                        [(root, retained) for _, _, retained in batch], owner=run, perform=False)
                except BaseException:
                    # This query can still borrow catalog-owned native values.
                    # Keep them until the original query/owner is settled.
                    run.retain_unknown = True
                    raise
                observation["native_pages"].extend(page["native_pages"])
                pages_complete = pages_complete and page["complete"]
                if not page["complete"]:
                    observation["reasons"].append(page["reasons"])
                for offset, (index, element, retained) in enumerate(batch):
                    if offset < len(page["rows"]):
                        visible = dict(page["rows"][offset])
                    else:
                        visible = {field.name: None for field in fields}
                        visible["reasons"] = {"row": f"native page did not copy this retained {named_type}"}
                        pages_complete = False
                        observation["reasons"].append({"source_index": index, "reason": visible["reasons"]["row"]})
                    visible["source_index"] = index
                    catalog.add(element, named_type, retained, visible)
            observation["complete"] = pages_complete
            observation["status"] = "observed" if pages_complete else "partial"
            return catalog
        except BaseException as error:
            observation["status"], observation["complete"] = "retained_after_error", False
            observation["failure"] = error
            error.choices = catalog
            raise
        finally:
            observation["native_results"] = tuple(run.operations)
    return await api._ctx.perform("politics." + label, read)


class NationalManagementMixin:
    """National facts and selected actions through original native owners."""

    async def national_identity(self, *, country_id=None):
        """Current identity; flag is a native icon token, color is native vector text."""
        return await country_snapshot(self, "politics.national_identity", _IDENTITY, country_id=country_id)

    async def national_modifiers(self, *, country_id=None):
        """Active aggregate values/source breakdowns plus timed effects and duration.

        Aggregate entries can include timed effects; they are not falsely labeled
        permanent. Native descriptions contain original effect/source labels.
        """
        async def read():
            selected = await resolve_country_id(self._ctx, country_id)
            summary = await country_snapshot(self, "politics.national_modifiers.summary", _MODIFIER_SUMMARY,
                                             country_id=selected, perform=False)
            entries = await country_source(self, "politics.national_modifiers.entries",
                "Country.GetModifier.GetEntries", _MODIFIER_ENTRIES, country_id=selected, perform=False)
            timed = await country_source(self, "politics.national_modifiers.timed",
                "Country.GetTimedModifiers", _TIMED_MODIFIERS, country_id=selected, perform=False)
            result = _merge_observations(selected, (summary, entries, timed))
            result.update(modifier=summary, entries=entries, timed_modifiers=timed)
            return result
        return await self._ctx.perform("politics.national_modifiers", read)

    async def colonies(self, *, country_id=None):
        """Active/growing states with progress, blockers, competitors and tension.

        Read the country's original state index, then batch only selected full
        state IDs. Unknown classification rows remain visible, not silently lost.
        """
        async def read():
            selected = await resolve_country_id(self._ctx, country_id)
            index = await country_source(self, "politics.colonies.index", "Country.AccessStates",
                                         _STATE_INDEX, country_id=selected, perform=False)
            selected_ids, unclassified = [], []
            for row in index["rows"]:
                flags = (row.get("is_being_colonized"), row.get("is_growing_colony"))
                if any(value is True for value in flags) and _native_id(row.get("id")):
                    selected_ids.append(row["id"])
                elif (not all(value is False for value in flags) or any(
                        field in row.get("reasons", {}) for field in ("is_being_colonized", "is_growing_colony"))):
                    unclassified.append(row)
            details = await _state_pages(self, selected, selected_ids)
            result = _merge_observations(selected, (index, details))
            result.update(rows=details["rows"], unclassified_rows=unclassified,
                          observed_state_count=index["source_count"], colony_count=len(details["rows"]))
            if unclassified:
                result["complete"], result["status"] = False, "partial"
                result["reasons"].append("Some state rows have no known colony classification or full state ID")
            return result
        return await self._ctx.perform("politics.colonies", read)

    async def colony_details(self, state, *, country_id=None):
        """Read one full state ID, or resolve an exact state name within the country.

        Native tension targets the next colony province's owner. Its original
        tooltip includes available uprising conditions; no risk score is guessed.
        """
        target = _selector(state)
        async def read():
            selected = await resolve_country_id(self._ctx, country_id)
            observations = []
            if isinstance(target, str):
                index = await country_source(self, "politics.colony_details.index", "Country.AccessStates",
                                             _STATE_INDEX, country_id=selected, perform=False)
                observations.append(index)
                matches = [row for row in index["rows"] if target in (row.get("name"), row.get("name_raw"))]
                if len(matches) != 1 or not _native_id(matches[0].get("id")):
                    result = _merge_observations(selected, observations)
                    result.update(state=None, candidates=matches, complete=False,
                                  status="ambiguous" if len(matches) > 1 else "unavailable")
                    result["reasons"].append("State name does not select one observed full native ID")
                    return result
                state_id = matches[0]["id"]
            else:
                state_id = target
            details = await _state_pages(self, selected, [state_id])
            result = _merge_observations(selected, (*observations, details))
            observation_reasons = result["reasons"]
            result.update(details["rows"][0])
            # Keep field and page evidence separate when flattening the state.
            result["reasons"] = {"fields": details["rows"][0]["reasons"],
                                 "observations": observation_reasons}
            return result
        return await self._ctx.perform("politics.colony_details", read)

    async def colonization_options(self, state_id=None, *, states=None):
        """Read original colonization tooltips/facts for explicitly selected states.

        Supply one full State ID or an explicit iterable of State IDs/observed
        rows. No worldwide candidate search or command validation is performed.
        Region IDs are copied from the original getter, never from an ordinal;
        actions still select the original full State ID. Native tooltip text
        supplies restrictions/claims/local conditions without Python parsing.
        """
        if (state_id is None) == (states is None):
            raise ValueError("provide one state_id or explicit states, not both")
        if states is None:
            selected = [_action_state(state_id)]
        else:
            if isinstance(states, (str, bytes, dict)):
                raise ValueError("states must be an explicit iterable of full native State IDs or observed rows")
            try:
                selected = [_action_state(state) for state in states]
            except TypeError as error:
                raise ValueError("states must be an explicit iterable of full native State IDs or observed rows") from error
        return await self._ctx.perform("politics.colonization_options", lambda: _colonization_options(self, selected))

    async def colonize(self, state_id):
        """Establish a colony through the original region producer of one state.

        The native expression State.GetStateRegion.Colonize derives its region
        and creates the command in the same callback. GetPlayer is the native
        actor; no second Country binding or retained StateRegion is fabricated.
        Actual target/player states before and after are returned. Acceptance
        alone is not reported as a created colony or a completed effect.
        """
        from .._political_actions import selected_command

        selected = _action_state(state_id)
        async def work():
            async with self._mutation_lock:
                state = await binding(self._ctx, "State", selected)
                before = await _colonization_observation(self, "politics.colonize.before", state)
                outcome = await selected_command(self, "colonize", "State.GetStateRegion.Colonize",
                                                 (state,), perform=False)
                result = {**outcome, "actor_source": "GetPlayer", "requested_state_id": selected,
                          "before": before, "after": None}
                if outcome.get("accepted") is True:
                    try:
                        result["after"] = await _colonization_observation(self, "politics.colonize.after", state)
                    except Exception as error:
                        result["after"] = getattr(error, "colonization_observation", None)
                        result["readback_error"] = error
                return result
        return await self._ctx.perform("politics.colonize", work)

    async def set_colonization_paused(self, state, paused=True, *, country_id=None):
        """Set an explicit paused state with at most one original native toggle.

        Use a full state ID (or a returned colony row). Unknown initial state
        does not authorize a toggle. Delivery and observed readback stay separate.
        """
        state_id = _action_state(state)
        if type(paused) is not bool:
            raise ValueError("paused must be an explicit bool")
        label = "politics.set_colonization_paused"
        return await self._ctx.perform(label, lambda: _colony_action(self, label, state_id,
            country_id, _PAUSED_FIELD, "State.TogglePauseColonization", paused=paused))

    async def abandon_colony(self, state, *, country_id=None):
        """Abandon the selected full state ID through its native validated command.

        This is the explicit destructive intent. Readback reports the actual
        state/owner; native acceptance alone does not prove loss of the colony.
        """
        state_id = _action_state(state)
        label = "politics.abandon_colony"
        return await self._ctx.perform(label, lambda: _colony_action(self, label, state_id,
            country_id, _ABANDON_FIELDS, "State.AbandonColony"))

    async def formation_choices(self, country_id=None):
        """Copy descriptions over genuine owned formation values for selection.

        Keep this catalog open while using one of its choices, then explicitly
        ``await catalog.close()``. A choice uses its original retained binding,
        never its display name or source_index. If the native source supplies a
        raw/borrowed wrapper, its ownership failure remains explicit; it is not
        retagged into an owned choice. Exceptions retain ``error.choices``.
        """
        return await _creation_choices(self, "formation_choices", "Country.AccessFormableCountries",
                                       "CountryFormation", _FORMATION_FIELDS, country_id)

    async def form_country(self, choice):
        """Form the country selected by one genuine, still-open formation choice.

        The native producer is CountryFormation.FormCountry(Country.Self), with
        the catalog's selected country and the choice's original retained value.
        Native acceptance is separate from the actual post-command identity.
        """
        from .._political_actions import selected_command

        if not isinstance(choice, OwnedChoice) or choice.native_type != "CountryFormation":
            raise TypeError("form_country requires an owned CountryFormation choice")
        selected = choice._catalog.owner.actor
        if not _native_id(selected):
            raise ValueError("formation choice has no original country selection")
        async def work():
            async with self._mutation_lock:
                async with choice.borrow(self._ctx) as lease:
                    async def selector(run):
                        lease.attach(run)
                        country = await binding(self._ctx, "Country", selected)
                        return country, lease.binding
                    outcome = await selected_command(self, "form_country",
                        "CountryFormation.FormCountry(Country.Self)", selector,
                        actor_id=selected, perform=False)
                    result = {**outcome, "country_id": selected,
                              "formation": dict(choice), "after_identity": None}
                    if outcome.get("accepted") is True:
                        try:
                            result["after_identity"] = await country_snapshot(self, "politics.form_country.after",
                                _IDENTITY, country_id=selected, perform=False)
                        except Exception as error:
                            result["readback_error"] = error
                    return result
        return await self._ctx.perform("politics.form_country", work)

    async def support_unification(self, choice, candidate_country_id):
        """Use the original player support/withdraw producer for one candidate.

        GetPlayer is the actor, exactly as in the native formation GUI. The only
        bound Country is the explicitly requested candidate. Calling the native
        producer again can withdraw support; before/after observations report
        that behavior without an assumed toggle result or a completed claim.
        """
        from .._political_actions import selected_command

        if not isinstance(choice, OwnedChoice) or choice.native_type != "CountryFormation":
            raise TypeError("support_unification requires an owned CountryFormation choice")
        if not _native_id(candidate_country_id):
            raise ValueError("candidate_country_id must be a full native country ID; zero is valid")
        async def work():
            async with self._mutation_lock:
                async with choice.borrow(self._ctx) as lease:
                    candidate = await binding(self._ctx, "Country", candidate_country_id)
                    bindings = (candidate, lease.binding)
                    before = await _choice_snapshot(self, lease, "politics.support_unification.before",
                                                    _UNIFICATION_SUPPORT, bindings)
                    async def selector(run):
                        lease.attach(run)
                        return candidate, lease.binding
                    outcome = await selected_command(self, "support_unification",
                        "CountryFormation.SupportCandidate(GetPlayer, Country.Self)", selector,
                        perform=False)
                    result = {**outcome, "actor_source": "GetPlayer",
                              "catalog_country_id": choice._catalog.owner.actor,
                              "requested_candidate_country_id": candidate_country_id,
                              "formation": dict(choice), "before": before, "after": None}
                    if outcome.get("accepted") is True:
                        try:
                            result["after"] = await _choice_snapshot(self, lease, "politics.support_unification.after",
                                                                   _UNIFICATION_SUPPORT, bindings)
                        except Exception as error:
                            result["readback_error"] = error
                    return result
        return await self._ctx.perform("politics.support_unification", work)

    async def formation_candidates(self, *, country_id=None, details=True):
        """Native formation conditions, candidate countries and formation regions.

        Detailed sources use original nested providers. Each child row repeats
        its CURRENT formation labels in the same native callback. Child pages
        are separate observations, not joined to earlier catalog rows by index
        or name. Region lists contain the native formation regions; the required
        count states how many must be controlled, not that every region is needed.
        """
        if type(details) is not bool:
            raise ValueError("details must be a bool")
        async def read():
            catalog = await country_source(self, "politics.formation_candidates",
                "Country.AccessFormableCountries", _FORMATION_FIELDS, country_id=country_id, perform=False)
            result = dict(catalog)
            result["selector_identity"] = {"available": False,
                "reason": "No verified native CountryFormation ID/key getter; source_index is occurrence only"}
            result["catalog_complete"] = catalog["complete"]
            if not details:
                result["complete"] = False
                result["status"] = "partial" if catalog["status"] != "unavailable" else "unavailable"
                result["limitations"] = ["Structured candidate, culture-country and formation-region reads were not requested"]
                result["reasons"] = [*catalog["reasons"], {"scope": "formation_details", "reason": result["limitations"][0]}]
                return result
            candidates = await _formation_children(self, "politics.formation_candidates.candidates", catalog,
                "CountryFormation.AccessFormationCandidates", _FORMATION_COUNTRIES)
            regions = await _formation_children(self, "politics.formation_candidates.regions", catalog,
                "CountryFormation.AccessFormationStateRegions", _FORMATION_REGIONS)
            culture_countries = await _formation_children(self, "politics.formation_candidates.culture_countries", catalog,
                "CountryFormation.AccessFormationCultureCountries", _FORMATION_COUNTRIES)
            result.update(_merge_observations(catalog["country_id"],
                (catalog, *candidates, *regions, *culture_countries)))
            result.update(unification_candidates=candidates, formation_state_regions=regions,
                          culture_countries=culture_countries)
            result["limitations"] = [
                "Formation labels are display values, not stable mutation selectors",
                "Support/control descriptions preserve native text; no relationships are parsed from translated strings",
                "Separate nested pages are not joined to the catalog by ordinal or label",
            ]
            return result
        return await self._ctx.perform("politics.formation_candidates", read)

    async def release_choices(self, country_id=None):
        """Own releasable CountryCreation choices and copy their native descriptions.

        Explicitly close this catalog. Raw/borrowed wrappers are not retagged.
        This does not open or confirm a release popup. Pass one original choice
        to release_country_options for its native flag/predicate matrix, or to
        release_country to apply explicit options and submit the selected action.
        """
        return await _creation_choices(self, "release_choices", "Country.AccessReleasableCountries",
                                       "CountryCreation", _RELEASE_FIELDS, country_id)

    async def release_country_options(self, choice):
        """Read one original release context without toggling or generating a command.

        Select a genuine open CountryCreation choice from release_choices.
        ``window`` contains the four current flags, native UI predicates,
        tooltips, creation descriptions and actual GetPlayer actor. False is a
        copied native value, not proof of a missing/error field; inspect its
        original field reasons and native pages. No command eligibility is
        inferred or validated. A partial context result remains partial even
        when all requested window fields can be copied.
        """
        from .._political_actions import owned_operation

        if not isinstance(choice, OwnedChoice) or choice.native_type != "CountryCreation":
            raise TypeError("release_country_options requires an owned CountryCreation choice")
        async def read():
            async with choice.borrow(self._ctx) as lease:
                answer = {"action": "release_country_options", "status": "unavailable", "complete": False,
                          "actor_source": "GetPlayer", "catalog_country_id": choice._catalog.owner.actor,
                          "creation": dict(choice), "context_result": None, "context_available": False,
                          "window": None, "native_pages": [], "reasons": {},
                          "command_validation_performed": False}
                async def operation(run, result):
                    window = await _release_context(run, lease, result)
                    if window is None:
                        return
                    result["context_available"] = True
                    observed = await snapshot(self, "politics.release_country_options.window",
                        _RELEASE_WINDOW_FIELDS, ((window,),), owner=run, perform=False)
                    complete = observed["complete"] and result["context_result"].error == 0
                    result.update(window=observed, complete=complete,
                        status="partial" if observed["complete"] and not complete else observed["status"],
                        native_pages=observed["native_pages"], reasons=observed["reasons"])
                    if result["context_result"].error:
                        result["reasons"] = {"context_error": result["context_result"].error,
                                             "window": observed["reasons"]}
                return await owned_operation(self, "release_country_options", operation,
                                             result=answer, perform=False)
        return await self._ctx.perform("politics.release_country_options", read)

    async def release_country(self, choice, *, independent=True, play_as=False, keep_homeland_states=False):
        """Release an explicitly selected country through its original owned context.

        independent=True requests an independent country; False requests an own
        subject. Play-as and homeland retention default explicitly to False.
        Native radio/checkbox states are read after every needed original toggle.
        No popup is displayed, and no global release window is selected by name.
        Acceptance and actual player/state observations remain separate.
        """
        from .._political_actions import owned_operation

        if not isinstance(choice, OwnedChoice) or choice.native_type != "CountryCreation":
            raise TypeError("release_country requires an owned CountryCreation choice")
        if any(type(value) is not bool for value in (independent, play_as, keep_homeland_states)):
            raise ValueError("independent, play_as and keep_homeland_states must be explicit bool values")
        requested = {"make_independent": independent, "make_own_subject": not independent,
                     "play_as_released": play_as, "keep_homeland_states": keep_homeland_states}
        async def work():
            async with self._mutation_lock:
                async with choice.borrow(self._ctx) as lease:
                    answer = {"action": "release_country", "accepted": None, "completed": False,
                              "status": "not_submitted", "validation": None, "submission": None,
                              "actor_source": "GetPlayer", "catalog_country_id": choice._catalog.owner.actor,
                              "creation": dict(choice), "requested_options": requested,
                              "context_result": None, "initial_window": None, "final_window": None,
                              "updates": [], "before_player_states": None, "after": None}
                    async def operation(run, result):
                        window = await _release_context(run, lease, result)
                        if window is None:
                            return
                        current = await project(self, "politics.release_country.options", _RELEASE_WINDOW_FIELDS,
                                                ((window,),), owner=run, perform=False)
                        result["initial_window"] = result["final_window"] = current
                        actor = _release_value(current, "actor_country_id")
                        result["before_player_states"] = await project(self, "politics.release_country.before_states",
                            _RELEASE_STATES, ((),), source_expression="GetPlayer.AccessStates", owner=run, perform=False)
                        # Select the requested-on radio first. Switching to an
                        # own subject needs its CanMakeOwnSubject predicate, not
                        # the disabled independent control's stricter And(...).
                        # The opposite flag is always read again after a toggle.
                        radios = _RELEASE_TOGGLES[:2]
                        if not independent:
                            radios = tuple(reversed(radios))
                        for name, condition, expression in (*radios, *_RELEASE_TOGGLES[2:]):
                            observed = _release_bool(current, name)
                            if observed is None:
                                result.update(status="options_unavailable", blocked_option=name,
                                              reason="Original requested option did not return a known native bool")
                                return
                            if observed is requested[name]:
                                continue
                            permitted = _release_bool(current, condition)
                            if permitted is not True:
                                result.update(status="option_rejected" if permitted is False else "options_unavailable",
                                    blocked_option=name, option_condition=condition,
                                    reason="Original UI option predicate is false" if permitted is False else "Original UI option predicate is unavailable")
                                return
                            invoked = await run.capture("release." + name, lambda expression=expression:
                                data.invoke_property_action(run.native, expression, (window,), output_bytes=4096,
                                    adapter=run.adapters["data"], process=run.process))
                            update = {"option": name, "requested": requested[name], "before": observed,
                                      "expression": expression, "invocation": invoked}
                            result["updates"].append(update)
                            # Radio options can change one another. Observe this
                            # same owned context again before choosing any next toggle.
                            current = await project(self, "politics.release_country.options", _RELEASE_WINDOW_FIELDS,
                                                    ((window,),), owner=run, perform=False)
                            update["after"] = current
                            result["final_window"] = current
                        mismatches = {name: _release_bool(current, name) for name, wanted in requested.items()
                                      if _release_bool(current, name) is not wanted}
                        if mismatches:
                            result.update(status="options_not_applied", option_mismatches=mismatches,
                                          reason="Original context does not show all explicitly requested options")
                            return
                        command = await run.generated_owner("ReleaseCountryWindow.ReleaseCountry", (window,))
                        await run.validate_submit(command, result)
                        for stage, native in reversed(run.operations):
                            if stage == "command.submit":
                                result["submission"] = native
                                break
                        if result.get("accepted") is True:
                            after = {"player": None, "player_states": None, "original_actor_states": None}
                            result["after"] = after
                            try:
                                after["player"] = await project(self, "politics.release_country.after_player",
                                    _RELEASE_PLAYER, ((),), owner=run, perform=False)
                                after["player_states"] = await project(self, "politics.release_country.after_player_states",
                                    _RELEASE_STATES, ((),), source_expression="GetPlayer.AccessStates", owner=run, perform=False)
                                if _native_id(actor):
                                    original = await binding(self._ctx, "Country", actor)
                                    after["original_actor_states"] = await project(self, "politics.release_country.original_actor_states",
                                        _RELEASE_STATES, ((original,),), source_expression="Country.AccessStates", owner=run, perform=False)
                                else:
                                    after["original_actor_reason"] = "Original actor ID was not copied; no substitute was chosen"
                            except Exception as error:
                                result["readback_error"] = error
                    return await owned_operation(self, "release_country", operation, result=answer, perform=False)
        return await self._ctx.perform("politics.release_country", work)

    async def release_candidates(self, *, country_id=None):
        """Releasable-country catalog with native states/cultures and eligibility.

        Subject/independence choices belong to the original release model; this
        read does not guess the second CanReleaseCountryAs argument or its flags.
        """
        async def read():
            result = await country_source(self, "politics.release_candidates",
                "Country.AccessReleasableCountries", _RELEASE_FIELDS, country_id=country_id, perform=False)
            result["selector_identity"] = {"available": False,
                "reason": "No verified native CountryCreation ID/key getter; source_index is occurrence only"}
            result["release_options"] = {"complete": False,
                "reason": "Use release_country_options with an original release_choices choice for the native option matrix"}
            result["catalog_complete"] = result["complete"]
            result["complete"] = False
            result["reasons"].append({"scope": "release_options", "reason": result["release_options"]["reason"]})
            if result["status"] != "unavailable":
                result["status"] = "partial"
            return result
        return await self._ctx.perform("politics.release_candidates", read)
