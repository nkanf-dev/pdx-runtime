"""Law, amendment, institution and domestic-pressure reads from original Data.

Pages copy at most 64 rows and 32 fields. Reads do not create, serialize or
validate commands. A source occurrence is not an ID: nested observations repeat
their actual parent labels/full country IDs in that same native callback. They
are never joined to an earlier parent row by its occurrence or display name.
"""
from __future__ import annotations

from ... import v3_data as data
from .._political_data import Field, country_snapshot, country_source, resolve_country_id
from .._run import SourceUnavailable

K = data.Kind


def _text(name, expression):
    return Field(name, expression, K.STRING, text=True)


def _identity(name, expression, kind=K.I32):
    return Field(name, expression, kind, full_id=True)


def _law_parent(prefix="Law"):
    return (
        _text("law_name", prefix + ".GetNameNoFormatting"),
        _text("law_type_name", prefix + ".GetLawType.GetNameNoFormatting"),
        _text("law_group_name", prefix + ".GetGroupName"),
        Field("law_group_key", prefix + ".GetGroup.GetKey", K.STRING),
        _identity("law_country_id", prefix + ".GetCountry.GetID"),
    )


_LAW_ROOT = "Country.GetLawBeingEnacted"
_ENACTMENT = (
    Field("is_enacting", "Country.IsEnactingLaw", K.BOOL),
    Field("is_progressing", "Country.HasProgressingLawEnactment", K.BOOL),
    Field("progress", "Country.GetLawEnactmentProgress", K.FIXED),
    _text("progress_description", "Country.GetLawEnactmentProgressDesc"),
    Field("maximum_setbacks", "Country.GetMaxLawEnactmentSetbacks", K.I32),
    _text("law_name", _LAW_ROOT + ".GetNameNoFormatting"),
    _text("law_type_name", _LAW_ROOT + ".GetLawType.GetNameNoFormatting"),
    Field("law_is_valid", _LAW_ROOT + ".IsValid", K.BOOL),
    Field("law_is_being_enacted", _LAW_ROOT + ".IsBeingEnacted", K.BOOL),
    Field("phase", _LAW_ROOT + ".GetEnactmentPhase", K.I32),
    Field("phase_key", _LAW_ROOT + ".GetCurrentEnactmentPhaseKey", K.STRING),
    Field("next_phase_key", _LAW_ROOT + ".GetNextEnactmentPhaseKey", K.STRING),
    _text("checkpoint_date", _LAW_ROOT + ".GetCheckpointEndDateString"),
    Field("checkpoint_base_days", _LAW_ROOT + ".GetCheckpointBaseDays", K.I32),
    Field("checkpoint_total_days", _LAW_ROOT + ".GetCheckpointTotalDays", K.I32),
    _text("checkpoint_days_breakdown", _LAW_ROOT + ".GetCheckpointTotalDaysBreakdown"),
    Field("checkpoint_remaining_days", _LAW_ROOT + ".GetCheckpointRemainingDays", K.I32),
    Field("checkpoint_progress", _LAW_ROOT + ".GetCheckpointProgress", K.FIXED),
    Field("daily_progress", _LAW_ROOT + ".GetDailyProgress", K.FIXED),
    Field("has_last_checkpoint", _LAW_ROOT + ".HasEnactmentLastCheckpoint", K.BOOL),
    Field("last_checkpoint_date", _LAW_ROOT + ".GetEnactmentLastCheckpointDate.GetString", K.STRING),
    _text("last_checkpoint_result", _LAW_ROOT + ".GetEnactmentLastCheckpointResult"),
    Field("setbacks", _LAW_ROOT + ".GetEnactmentSetbackCount", K.I32),
    *(field for outcome in ("Success", "Advance", "Debate", "Stall") for field in (
        Field(outcome.lower() + "_chance", _LAW_ROOT + ".GetCheckpoint" + outcome + "Chance", K.FIXED),
        _text(outcome.lower() + "_breakdown", _LAW_ROOT + ".GetCheckpoint" + outcome + "Breakdown"),
    )),
)

_LAW_EFFECTS = (
    *_law_parent(),
    _text("description", "Law.GetDesc"),
    _text("type_description", "Law.GetTypeDesc"),
    Field("valid", "Law.IsValid", K.BOOL),
    Field("active", "Law.IsActive", K.BOOL),
    Field("being_enacted", "Law.IsBeingEnacted", K.BOOL),
    Field("supported", "Law.IsSupported", K.BOOL),
    Field("missing_support", "Law.IsMissingSupport", K.BOOL),
    Field("has_necessary_laws", "Law.HasNecessaryLaws", K.BOOL),
    Field("has_necessary_technology", "Law.HasNecessaryTech", K.BOOL),
    _text("cannot_enact_description", "Law.CannotEnactDesc"),
    _text("on_enact", "Law.GetOnEnactDesc"),
    _text("on_activate", "Law.GetOnActivateDesc"),
    _text("on_deactivate", "Law.GetOnDeactivateDesc"),
    _text("replaced_law_effect", "Law.GetReplacedLawEffect"),
    _text("approval_prediction", "Law.PredictApprovalChangesDuringEnactmentDesc"),
    Field("legitimacy_change", "Law.PredictLegitimacyChangeToEnact", K.I32),
    _text("legitimacy_prediction", "Law.PredictLegitimacyChangeToEnactDesc"),
    _text("movement_loyalists_prediction", "Law.PredictMovementLoyalistsFromEnactmentDesc"),
    _text("enactment_activism_prediction", "Law.PredictRadicalismChangesFromEnactmentDesc"),
    _text("cancellation_activism_prediction", "Law.PredictRadicalismChangesFromCancellationDesc"),
    _text("cancel_description", "Law.GetCancelTooltip"),
    _text("institution_description", "Law.GetInstitutionTooltip"),
)

_AMENDMENTS = (
    *_law_parent("Amendment.GetAttachedLaw"),
    _text("name", "Amendment.GetNameNoFormatting"),
    _text("type_name", "Amendment.GetType.GetNameNoFormatting"),
    _text("type_description", "Amendment.GetType.GetDesc"),
    _text("parent_law_type_name", "Amendment.GetParentLawType.GetNameNoFormatting"),
    Field("active", "Amendment.IsActive", K.BOOL),
    _identity("sponsor_id", "Amendment.GetSponsor.GetID", K.U32),
    _text("sponsor_name", "Amendment.GetSponsor.GetNameNoFormatting"),
    Field("has_cooldown", "Amendment.HasCooldown", K.BOOL),
    Field("cooled_down", "Amendment.HasCooledDown", K.BOOL),
    _text("cooldown_end_date", "Amendment.GetCooldownEndDate"),
    Field("has_timeout", "Amendment.HasTimeout", K.BOOL),
    Field("timed_out", "Amendment.HasTimedOut", K.BOOL),
    _text("timeout_date", "Amendment.GetTimeoutDate"),
    *(field for stance in ("Endorsing", "Indifferent", "Opposing") for field in (
        Field(stance.lower() + "_interest_groups_count", "Amendment.GetNum" + stance + "InterestGroups", K.I32),
        _text(stance.lower() + "_interest_groups_summary", "Amendment.Get" + stance + "InterestGroupsSummary"),
        _text(stance.lower() + "_interest_groups_breakdown", "Amendment.Get" + stance + "InterestGroupsBreakdown"),
    )),
    _text("on_amended_effects", "Amendment.GetOnAmendedEffectsDesc"),
    _text("on_repeal_effects", "Amendment.GetOnRepealEffectsDesc"),
)

_INSTITUTIONS = (
    _text("name", "Institution.GetNameNoFormatting"),
    _identity("owner_country_id", "Institution.GetCountry.GetID"),
    Field("valid", "Institution.IsValid", K.BOOL),
    Field("level", "Institution.GetInvestmentLevel", K.I32),
    Field("target_level", "Institution.GetTargetInvestmentLevel", K.I32),
    Field("maximum_level", "Institution.GetMaxLevel", K.I32),
    Field("changing_level", "Institution.IsChangingLevel", K.BOOL),
    Field("investment_cost", "Institution.GetInvestmentCost", K.FIXED),
    _text("description", "Institution.GetDesc"),
    _text("providing_laws", "Institution.GetProvidingLawsDesc"),
    _text("modifiers", "Institution.GetModifierDesc"),
    _text("tooltip_modifiers", "Institution.GetTooltipModifierDesc"),
    _text("timed_modifiers", "Institution.GetTimedModifiersDesc"),
    _text("time_to_target", "Institution.GetTimeToReachTargetLevelDesc"),
)
_INSTITUTION_LEVELS = (
    _text("institution_name", "Institution.GetNameNoFormatting"),
    _identity("institution_country_id", "Institution.GetCountry.GetID"),
    Field("current_level", "Institution.GetInvestmentLevel", K.I32),
    Field("target_level", "Institution.GetTargetInvestmentLevel", K.I32),
    Field("maximum_level", "Institution.GetMaxLevel", K.I32),
    Field("level", "InstitutionInvestmentLevel.GetLevel", K.I32),
    Field("active", "InstitutionInvestmentLevel.IsActive", K.BOOL),
    Field("is_target", "InstitutionInvestmentLevel.IsTargetLevel", K.BOOL),
    Field("increasing", "InstitutionInvestmentLevel.IsIncreasing", K.BOOL),
    Field("decreasing", "InstitutionInvestmentLevel.IsDecreasing", K.BOOL),
    Field("increasing_to_level", "InstitutionInvestmentLevel.IsIncreasingToThisLevel", K.BOOL),
    Field("decreasing_to_level", "InstitutionInvestmentLevel.IsDecreasingToThisLevel", K.BOOL),
    Field("can_change_to_level", "Institution.CanChangeLevelTo(InstitutionInvestmentLevel.GetLevel)", K.BOOL),
    _text("change_description", "Institution.GetChangeLevelDesc(InstitutionInvestmentLevel.GetLevel)"),
    _text("providing_laws", "Institution.GetProvidingLawsDesc"),
)

_MOVEMENTS = (
    _text("name", "PoliticalMovement.GetNameNoFormatting"),
    _text("type_name", "PoliticalMovement.GetType.GetNameNoFormatting"),
    _identity("owner_country_id", "PoliticalMovement.GetCountry.GetID"),
    Field("valid", "PoliticalMovement.IsValid", K.BOOL),
    Field("support", "PoliticalMovement.GetSupport", K.FIXED),
    Field("target_support", "PoliticalMovement.GetTargetSupport", K.FIXED),
    _text("support_description", "PoliticalMovement.GetSupportDesc"),
    Field("activism", "PoliticalMovement.GetRadicalism", K.FIXED),
    Field("target_activism", "PoliticalMovement.GetTargetRadicalism", K.FIXED),
    _text("activism_drift_description", "PoliticalMovement.GetTargetRadicalismDesc"),
    Field("popular_support", "PoliticalMovement.GetPopularSupport", K.FIXED),
    Field("military_support", "PoliticalMovement.GetMilitarySupport", K.FIXED),
    Field("supporting_individuals", "PoliticalMovement.GetNumSupportingIndividuals", K.I64),
    Field("supporting_radicals", "PoliticalMovement.GetNumSupportingRadicals", K.I64),
    Field("supporting_military_individuals", "PoliticalMovement.GetNumSupportingMilitaryIndividuals", K.I64),
    Field("predicted_radicals_per_month", "PoliticalMovement.PredictNumberOfRadicalsEveryMonth", K.I64),
    Field("can_cause_obstinance", "PoliticalMovement.CanCauseObstinance", K.BOOL),
    Field("causing_obstinance", "PoliticalMovement.IsCausingObstinance", K.BOOL),
    Field("obstinate_states_count", "PoliticalMovement.GetNumOfObstinateStates", K.I32),
    Field("highest_state_obstinance", "PoliticalMovement.GetHighestStateObstinance", K.FIXED),
    _text("obstinate_states_breakdown", "PoliticalMovement.GetObstinateStatesBreakdown"),
    Field("insurrectionary", "PoliticalMovement.IsInsurrectionary", K.BOOL),
    Field("revolutionary", "PoliticalMovement.IsRevolutionary", K.BOOL),
    Field("secessionist", "PoliticalMovement.IsSecessionist", K.BOOL),
    Field("bolstered", "PoliticalMovement.IsBolstered", K.BOOL),
    Field("suppressed", "PoliticalMovement.IsSuppressed", K.BOOL),
)
_MOVEMENT_STATES = (
    _text("movement_name", "PoliticalMovement.GetNameNoFormatting"),
    _text("movement_type_name", "PoliticalMovement.GetType.GetNameNoFormatting"),
    _identity("movement_country_id", "PoliticalMovement.GetCountry.GetID"),
    _identity("state_id", "State.GetID"),
    _text("state_name", "State.GetName"),
    _identity("state_owner_id", "State.GetOwner.GetID"),
    Field("obstinance_from_movement", "State.GetObstinanceFromMovement(PoliticalMovement.Self)", K.FIXED),
    Field("obstinate_pops_from_movement", "State.GetNumOfObstinatePopsFromMovement(PoliticalMovement.Self)", K.I64),
    Field("obstinance", "State.GetObstinance", K.FIXED),
    Field("obstinance_supporters", "State.GetNumObstinanceSupporters", K.I64),
    Field("obstinance_effect_scale", "State.GetObstinanceEffectScale", K.FIXED),
    _text("obstinance_description", "State.GetObstinanceDesc"),
    _text("obstinance_effects", "State.GetObstinanceEffectsDesc"),
)

_CIVIL_WAR_PARENT = (
    _text("civil_war_name", "CivilWar.GetName"),
    _identity("origin_country_id", "CivilWar.GetOriginCountry.GetID"),
    _identity("uprising_country_id", "CivilWar.GetUprisingCountry.GetID"),
    _text("movement_name", "CivilWar.GetPoliticalMovement.GetNameNoFormatting"),
)
_CIVIL_WARS = (
    *_CIVIL_WAR_PARENT,
    _text("origin_country_name", "CivilWar.GetOriginCountry.GetName"),
    _text("uprising_country_name", "CivilWar.GetUprisingCountry.GetName"),
    Field("valid", "CivilWar.IsValid", K.BOOL),
    Field("revolution", "CivilWar.IsRevolution", K.BOOL),
    Field("secession", "CivilWar.IsSecession", K.BOOL),
    Field("has_broken_out", "CivilWar.HasBrokenOut", K.BOOL),
    Field("has_political_movement", "CivilWar.HasPoliticalMovement", K.BOOL),
    _text("movement_type_name", "CivilWar.GetPoliticalMovement.GetType.GetNameNoFormatting"),
    _identity("leading_interest_group_id", "CivilWar.GetLeadingInsurrectionaryIG.GetID", K.U32),
    _text("leading_interest_group_name", "CivilWar.GetLeadingInsurrectionaryIG.GetNameNoFormatting"),
    Field("progress", "CivilWar.GetProgress", K.FIXED),
    Field("maximum_progress", "CivilWar.GetMaxProgress", K.FIXED),
    _text("maximum_progress_description", "CivilWar.GetMaxProgressDesc"),
    Field("clock_progress", "CivilWar.GetClockTime", K.FIXED),
    Field("checkpoint_remaining_weeks", "CivilWar.GetClockCheckpointRemainingWeeks", K.I32),
    _text("checkpoint_date", "CivilWar.GetClockCheckpointDateString"),
    Field("progress_at_checkpoint", "CivilWar.GetProgressAtCheckpoint", K.FIXED),
    _text("progress_at_checkpoint_description", "CivilWar.GetProgressAtCheckpointDesc"),
    Field("uprising_states_count", "CivilWar.GetNumUprisingStates", K.I32),
    _text("uprising_states", "CivilWar.GetUprisingStatesString"),
    Field("uprising_battalions", "CivilWar.GetNumUprisingBattalions", K.I32),
    Field("uprising_potential_conscripts", "CivilWar.GetNumUprisingPotentialConscripts", K.I32),
    Field("uprising_ships", "CivilWar.GetNumUprisingShips", K.I32),
    _text("uprising_units_description", "CivilWar.GetUprisingNumUnitsDesc"),
    _text("uprising_conscription_rate", "CivilWar.GetUprisingConscriptionRateDesc"),
)
_CIVIL_WAR_IGS = (
    *_CIVIL_WAR_PARENT,
    _identity("interest_group_id", "InterestGroup.GetID", K.U32),
    _text("interest_group_name", "InterestGroup.GetNameNoFormatting"),
    Field("clout", "InterestGroup.GetClout", K.FIXED),
    Field("approval", "InterestGroup.GetApprovalValue", K.I32),
    Field("insurrectionary", "InterestGroup.IsInsurrectionary", K.BOOL),
)

_ACTIVE_LAW_SOURCES = (
    "Country.AccessActivePowerStructureLaws", "Country.AccessActiveEconomyLaws",
    "Country.AccessActiveHumanRightsLaws", "Country.AccessActiveOtherLaws",
)
_CANDIDATE_LAW_SOURCES = (
    "Country.AccessLawsWithGovernmentOrMovementSupport",
    "Country.AccessLawsWithoutGovernmentOrMovementSupport",
)


def _law_sources(scope):
    if scope not in ("enacting", "active", "candidates", "all"):
        raise ValueError("scope must be enacting, active, candidates or all")
    return ((_ACTIVE_LAW_SOURCES if scope in ("active", "all") else ())
            + (_CANDIDATE_LAW_SOURCES if scope in ("candidates", "all") else ()))


def _bool(value, name):
    if type(value) is not bool:
        raise ValueError(name + " must be True or False")


def _prefix(fields, prefix):
    return tuple(Field(f.name, f.expression.replace("Law.", prefix + "."),
                       f.kind, f.text, f.full_id) for f in fields)


def _bundle(country_id, **sections):
    observations = []
    for name, section in sections.items():
        if isinstance(section, list):
            observations.extend((f"{name}[{i}]", item) for i, item in enumerate(section))
        else:
            observations.append((name, section))
    complete = all(item["complete"] for _, item in observations)
    return {"country_id": country_id, "status": "observed" if complete else "partial",
            "complete": complete, "atomic_snapshot": False,
            "native_pages": [page for _, item in observations for page in item["native_pages"]],
            "reasons": {name: item["reasons"] for name, item in observations if not item["complete"]},
            **sections}


def _identities(result, *types):
    result["identity_limitations"] = {
        typ: "No registered scalar full ID/key getter is available here; display labels and source_index are not stable IDs or action selectors."
        for typ in types
    }
    return result


async def _nested(api, label, parent, source, fields, *, source_arguments=()):
    observations = []
    for row in parent["rows"]:
        index = row["source_index"]
        step = data.CreationStep(parent["source_expression"], index, parent["source_count"])
        result = await country_source(api, label, source, fields,
            country_id=parent["country_id"], steps=(step,),
            source_arguments=source_arguments, perform=False)
        result["requested_parent_occurrence"] = index
        result["parent_source_expression"] = parent["source_expression"]
        result["parent_identity_semantics"] = "Parent fields are from this nested callback, not an earlier parent row; no cross-snapshot join."
        observations.append(result)
    return observations


def _affected_movement_fields(intent, prefix="Law"):
    suffix = "StartEnact" if intent == "enactment" else "CancelEnact"
    return (
        *_law_parent(prefix),
        _text("movement_name", "PoliticalMovement.GetNameNoFormatting"),
        _text("movement_type_name", "PoliticalMovement.GetType.GetNameNoFormatting"),
        _identity("movement_country_id", "PoliticalMovement.GetCountry.GetID"),
        Field("activism", "PoliticalMovement.GetRadicalism", K.FIXED),
        Field("target_activism", "PoliticalMovement.GetTargetRadicalism", K.FIXED),
        Field("predicted_activism_change", prefix + ".GetRadicalismEffectOnMovementFrom" + suffix + "(PoliticalMovement.Self)", K.FIXED),
        Field("predicted_activism_target", prefix + ".GetRadicalismDriftTargetOnMovementFrom" + suffix + "(PoliticalMovement.Self)", K.FIXED),
    )


class LawDetailsMixin:
    """Native read models. Completeness describes copied fields, not stable IDs.

    Missing scalar identity is reported separately. Native rejection/errors,
    zero, False, raw display strings and copied pages are never discarded.
    """

    async def enactment_details(self, *, country_id=None):
        """Current phase, checkpoint/date, progress, setbacks and four chances.

        This never calls the older enactment reader or StopEnacting to learn a
        law ID. Native phase keys, chances and dates are not reconstructed.
        """
        result = await country_snapshot(self, "politics.enactment_details", _ENACTMENT, country_id=country_id)
        result["units"] = {"progress": "fraction", "checkpoint_progress": "fraction",
            "daily_progress": "native progress per day", "chances": "fraction", "checkpoint_days": "days"}
        return _identities(result, "Law")

    async def law_effects(self, *, country_id=None, scope="enacting", movements=True):
        """Enact/cancel effects and original conditions; no command validation.

        scope selects enacting (default), active, candidates or all original
        sources. all keeps overlapping observations rather than deduplicating
        by an unsupported law ID/name. movements adds native per-movement
        enact/cancel predictions, with parent law labels in the same callback.
        Native conditions are not a substitute for selected-action validation.
        """
        sources = _law_sources(scope)
        _bool(movements, "movements")
        async def read():
            selected = await resolve_country_id(self._ctx, country_id)
            laws, affected = [], []
            if scope in ("enacting", "all"):
                current = await country_snapshot(self, "politics.law_effects.enacting",
                    _prefix(_LAW_EFFECTS, _LAW_ROOT), country_id=selected, perform=False)
                current["law_scope"] = "enacting"
                laws.append(current)
                # Only a copied, error-free native false proves there is no
                # current enactment. An unknown field never becomes false.
                absent = (current.get("being_enacted") is False
                          and "being_enacted" not in current["reasons"]["fields"])
                if movements and not absent:
                    for intent, suffix in (("enactment", "StartEnact"), ("cancellation", "CancelEnact")):
                        result = await country_source(self, "politics.law_effects." + intent,
                            _LAW_ROOT + ".GetAffectedMovementsFrom" + suffix,
                            _affected_movement_fields(intent, _LAW_ROOT), country_id=selected, perform=False)
                        result["intent"] = intent
                        affected.append(result)
            for source in sources:
                parent = await country_source(self, "politics.law_effects.laws", source,
                    _LAW_EFFECTS, country_id=selected, perform=False)
                laws.append(parent)
                if movements:
                    for intent, suffix in (("enactment", "StartEnact"), ("cancellation", "CancelEnact")):
                        nested = await _nested(self, "politics.law_effects." + intent, parent,
                            "Law.GetAffectedMovementsFrom" + suffix, _affected_movement_fields(intent))
                        for result in nested:
                            result["intent"] = intent
                        affected.extend(nested)
            result = _bundle(selected, laws=laws, affected_movements=affected)
            result.update(scope=scope, movement_predictions_included=movements, eligibility_checked=False,
                overlapping_sources="Original source occurrences are preserved; they are not deduplicated by label or index.")
            return _identities(result, "Law", "PoliticalMovement")
        return await self._ctx.perform("politics.law_effects", read)

    async def amendments(self, *, country_id=None, scope="all", active_only=False):
        """Native amendment status, parent/type, sponsors, support and timers.

        all reads active/candidate law providers plus the current enactment.
        active_only selects GetActiveAmendments; otherwise GetAllAmendments
        preserves inactive/proposed entries and their native IsActive value.
        Full amendment and country-law IDs are unavailable to these getters.
        """
        sources = _law_sources(scope)
        _bool(active_only, "active_only")
        getter = "GetActiveAmendments" if active_only else "GetAllAmendments"
        async def read():
            selected = await resolve_country_id(self._ctx, country_id)
            laws, amendments = [], []
            if scope in ("enacting", "all"):
                amendments.append(await country_source(self, "politics.amendments.enacting",
                    _LAW_ROOT + "." + getter, _AMENDMENTS, country_id=selected, perform=False))
            for source in sources:
                parent = await country_source(self, "politics.amendments.laws", source,
                    _law_parent(), country_id=selected, perform=False)
                laws.append(parent)
                amendments.extend(await _nested(self, "politics.amendments.rows", parent,
                    "Law." + getter, _AMENDMENTS))
            result = _bundle(selected, laws=laws, amendments=amendments)
            result.update(scope=scope, active_only=active_only,
                overlapping_sources="Original source occurrences are preserved; they are not deduplicated by label or index.")
            return _identities(result, "Law", "Amendment", "AmendmentType")
        return await self._ctx.perform("politics.amendments", read)

    async def institution_effects(self, *, country_id=None, levels=True):
        """Current modifiers/providing laws and every native investment level.

        Each level includes CanChangeLevelTo and GetChangeLevelDesc, not a
        generated command. Native descriptions carry legal/technology limits;
        no separate technology list or chosen-level modifier table is invented.
        """
        _bool(levels, "levels")
        async def read():
            institutions = await country_source(self, "politics.institution_effects.rows",
                "Country.AccessInstitutions", _INSTITUTIONS, country_id=country_id, perform=False)
            investment_levels = (await _nested(self, "politics.institution_effects.levels", institutions,
                "Institution.AccessInvestmentLevels", _INSTITUTION_LEVELS)) if levels else []
            result = _bundle(institutions["country_id"], institutions=institutions, levels=investment_levels)
            result.update(levels_included=levels, eligibility_checked=False,
                units={"investment_cost": "bureaucracy", "level": "investment level"})
            return _identities(result, "Institution")
        return await self._ctx.perform("politics.institution_effects", read)

    async def movement_pressure(self, *, country_id=None, states=True):
        """Support/activism targets, drift causes, and native state obstinance.

        The game's English UI calls native GetRadicalism activism. With states
        enabled, each movement's country states are paged in its original
        context. Zero pressure and errors remain visible; no state is filtered
        by a guessed threshold. states=False keeps the native state breakdown.
        """
        _bool(states, "states")
        async def read():
            movements = await country_source(self, "politics.movement_pressure.rows",
                "Country.AccessPoliticalMovements", _MOVEMENTS, country_id=country_id, perform=False)
            state_pressure = (await _nested(self, "politics.movement_pressure.states", movements,
                "PoliticalMovement.GetCountry.AccessStates", _MOVEMENT_STATES)) if states else []
            result = _bundle(movements["country_id"], movements=movements, state_pressure=state_pressure)
            result.update(states_included=states,
                state_scope="All states from each movement's native country; no nonzero-only filter.",
                units={"support": "fraction", "target_support": "fraction", "activism": "fraction",
                    "target_activism": "fraction", "obstinance": "native fixed point", "population": "individuals"})
            return _identities(result, "PoliticalMovement")
        return await self._ctx.perform("politics.movement_pressure", read)

    async def civil_wars(self, *, country_id=None, interest_groups=True):
        """Revolution/secession risk, clocks, uprising countries, states and forces.

        Both Country.AccessCivilWars and AccessSecessions are read as original
        sources. They are not assumed disjoint and are never merged by name.
        Insurrectionary IGs use the selected Country as the native argument.
        """
        _bool(interest_groups, "interest_groups")
        async def read():
            selected = await resolve_country_id(self._ctx, country_id)
            sections, groups = {}, []
            for name, source in (("civil_wars", "Country.AccessCivilWars"), ("secessions", "Country.AccessSecessions")):
                parent = await country_source(self, "politics.civil_wars." + name,
                    source, _CIVIL_WARS, country_id=selected, perform=False)
                sections[name] = parent
                if interest_groups:
                    groups.extend(await _nested(self, "politics.civil_wars.interest_groups", parent,
                        "CivilWar.AccessInsurrectionaryIGs(Country.Self)", _CIVIL_WAR_IGS,
                        source_arguments=("Country.Self",)))
            result = _bundle(selected, **sections, insurrectionary_interest_groups=groups)
            result.update(interest_groups_included=interest_groups,
                overlapping_sources="Original civil-war and secession observations are not deduplicated.",
                units={"progress": "fraction", "maximum_progress": "fraction", "clock_progress": "fraction",
                       "checkpoint_remaining_weeks": "weeks", "forces": "native unit counts"})
            return _identities(result, "CivilWar", "PoliticalMovement")
        return await self._ctx.perform("politics.civil_wars", read)

    async def stop_institution(self, institution_id):
        """Stop one institution's size change, selected by its real full ID.

        Obtain the ID from institutions(), not from these copied display rows.
        Selection copies the established retained Institution wrapper's origin,
        never a command or occurrence. Only the selected original StopSizeChange
        is generated, validated and submitted. Acceptance is not effect readback.
        """
        if type(institution_id) is not int or not 0 <= institution_id < 0xffffffff:
            raise ValueError("institution_id must be a complete uint32 ID; zero is valid")
        from .._political_actions import selected_command

        async def work():
            async with self._mutation_lock:
                actor = {}
                async def select(run):
                    country = await run.country()
                    actor["id"] = run.actor
                    async for _, element, institution in run.elements("Country.AccessInstitutions", (country,), "Institution"):
                        full_id = await run.origin(element, "Institution")
                        if full_id == institution_id:
                            return country, institution
                    raise SourceUnavailable(f"Institution {institution_id} is not in Country.AccessInstitutions")
                result = await selected_command(self, "stop_institution", "Institution.StopSizeChange", select, perform=False)
                result.update(country_id=actor.get("id"), institution_id=institution_id,
                    effect_observed=None,
                    readback_reason="Submission acceptance does not prove that the institution stopped changing level.")
                return result
        return await self._ctx.perform("politics.stop_institution", work)
