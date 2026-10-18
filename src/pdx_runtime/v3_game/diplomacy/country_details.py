"""Recognition evidence, native obligations and contextual subject options.

These are copied Data projections and optional exact original Script predicates.
No candidate command, IsValid(command), Prepare, Serialize, Validate or command
submission is made. Country ID is selected once; debtor/creditor and action-country
leaves remain full native chains.
"""
from __future__ import annotations

from ... import v3_data as data, v3_script as script
from .._political_data import Field, country_snapshot, country_source


K = data.Kind
_RECOGNITION_KEY = "je_earn_recognition"
_RECOGNITION_GROUP = "GetJournalEntryGroup('je_group_foreign_affairs')"
_ENTRY = "Country.GetJournalEntry(JournalEntryType.Self)"
_DEBTOR = "DiplomaticRelations.GetCountryWhichOwesObligation"
_CREDITOR = "DiplomaticRelations.GetCountryWhichNotOwesObligation"
_ACTION_FAMILIES = ("General", "Overlord", "Subject", "PowerBlocLeader", "PowerBlocMember")
# Original installed CountryType keys, not a recognition classification table.
_COUNTRY_TYPE_KEYS = ("recognized", "colonial", "unrecognized", "decentralized", "company")


def _text(name, expression):
    return Field(name, expression, K.STRING, text=True)


def _id(name, expression, kind=K.I32):
    return Field(name, expression, kind, full_id=True)


_RANK = (
    _text("rank", "Country.GetRank"),
    _text("rank_description", "Country.GetRankDesc"),
    _text("rank_tooltip", "Country.GetRankTooltip"),
    _text("highest_possible_rank", "Country.GetHighestPossibleRank"),
    Field("rank_number", "Country.GetRankNumber", K.I32),
    Field("rank_progress", "Country.GetRankProgress", K.FLOAT),
    _text("next_rank", "Country.GetNextRank"),
    _text("next_rank_description", "Country.GetNextRankDesc"),
    Field("next_rank_number", "Country.GetNextRankNumber", K.I32),
    _text("previous_rank", "Country.GetPreviousRank"),
    _text("target_rank", "Country.GetTargetRank"),
    Field("days_until_rank_loss", "Country.GetDaysUntilRankLoss", K.I32),
    Field("prestige", "Country.GetPrestige", K.I32),
    _text("prestige_description", "Country.GetPrestigeDesc"),
    Field("prestige_for_current_rank", "Country.GetPrestigeForCurrentRank", K.I32),
    Field("prestige_for_next_rank", "Country.GetPrestigeForNextRank", K.I32),
    Field("prestige_to_maintain_rank", "Country.GetPrestigeToMaintainRank", K.I32),
)
_JOURNAL = (
    Field("type_key", "JournalEntryType.GetKey", K.STRING),
    Field("entry_type_key", _ENTRY + ".GetType.GetKey", K.STRING),
    _text("name", _ENTRY + ".GetName"),
    _text("reason", _ENTRY + ".GetReason"),
    _text("status_description", _ENTRY + ".GetStatusDesc"),
    Field("active", _ENTRY + ".IsActive", K.BOOL),
    Field("complete_entry", _ENTRY + ".IsComplete", K.BOOL),
    Field("progressing", _ENTRY + ".IsProgressing", K.BOOL),
    Field("can_complete", _ENTRY + ".CanComplete", K.BOOL),
    Field("has_goal", _ENTRY + ".HasGoal", K.BOOL),
    Field("has_progress_bar", _ENTRY + ".HasProgressBar", K.BOOL),
    _text("activation_requirements", _ENTRY + ".GetActivationTooltip"),
    _text("completion_requirements", _ENTRY + ".GetCompletionTooltip"),
    _text("progress_description", _ENTRY + ".GetProgressDesc"),
    _text("failure_requirements", _ENTRY + ".GetFailureTooltip"),
    _text("invalidation_requirements", _ENTRY + ".GetInvalidationTooltip"),
    _text("completion_effects", _ENTRY + ".GetOnCompleteTooltip"),
    Field("timeout_days_left", _ENTRY + ".GetTimeoutDaysLeft", K.I32),
    _text("timeout_description", _ENTRY + ".GetTimeoutTooltip"),
)
_PROGRESS = (
    Field("entry_type_key", _ENTRY + ".GetType.GetKey", K.STRING),
    _text("entry_name", _ENTRY + ".GetName"),
    _text("name", "ScriptedProgressBar.GetName"),
    _text("description", "ScriptedProgressBar.GetDesc"),
    _text("second_description", "ScriptedProgressBar.GetSecondDesc"),
    _text("periodic_progress_breakdown", "ScriptedProgressBar.GetPeriodicProgressBreakdown"),
    Field("current_value", _ENTRY + ".GetCurrentBarValue(ScriptedProgressBar.Self)", K.FIXED),
    Field("current_progress", _ENTRY + ".GetCurrentBarProgress(ScriptedProgressBar.Self)", K.FLOAT),
    Field("min_value", "ScriptedProgressBar.GetMinValue", K.FIXED),
    Field("max_value", "ScriptedProgressBar.GetMaxValue", K.FIXED),
    Field("start_value", "ScriptedProgressBar.GetStartValue", K.FIXED),
)
_RELATION_PARTIES = (
    _id("relation_id", "DiplomaticRelations.GetID", K.U32),
    _id("first_country_id", "DiplomaticRelations.GetFirstCountry.GetID"),
    _text("first_country_name", "DiplomaticRelations.GetFirstCountry.GetNameNoFormatting"),
    _id("second_country_id", "DiplomaticRelations.GetSecondCountry.GetID"),
    _text("second_country_name", "DiplomaticRelations.GetSecondCountry.GetNameNoFormatting"),
    _id("debtor_country_id", _DEBTOR + ".GetID"),
    _text("debtor_country_name", _DEBTOR + ".GetNameNoFormatting"),
    _id("creditor_country_id", _CREDITOR + ".GetID"),
    _text("creditor_country_name", _CREDITOR + ".GetNameNoFormatting"),
    Field("debtor_owes_creditor", _DEBTOR + ".OwesObligationTo(" + _CREDITOR + ")", K.BOOL),
    Field("days_left_for_creditor", "DiplomaticRelations.CalcOwedObligationDaysLeft(" + _CREDITOR + ")", K.I32),
)


def _obligation_fields(direction):
    # Original GUI rebinds the flag's Country to debtor for From, creditor for
    # To. Keep that exact owner through full promotions instead of reusing the
    # selected root Country alias (which would silently change the meaning).
    owner, other, getter = (_DEBTOR, _CREDITOR, "From") if direction == "from" else (_CREDITOR, _DEBTOR, "To")
    return (*_RELATION_PARTIES,
        _text("expiration_date", owner + ".GetObligation" + getter + "ExpirationDate(" + other + ")"),
        _text("expiration_description", owner + ".GetObligation" + getter + "ExpirationDesc(" + other + ")"))


_OBLIGATION_ACTIONS = (
    _id("relation_id", "DiplomaticRelations.GetID", K.U32),
    _id("debtor_country_id", _DEBTOR + ".GetID"),
    _id("creditor_country_id", _CREDITOR + ".GetID"),
    _id("action_id", "DiplomaticAction.GetID", K.U32),
    Field("action_key", "DiplomaticAction.GetType.GetKey", K.STRING),
    _text("name", "DiplomaticAction.GetNameNoFormatting"),
    _id("action_first_country_id", "DiplomaticAction.GetFirstCountry.GetID"),
    _id("action_second_country_id", "DiplomaticAction.GetSecondCountry.GetID"),
    Field("can_call_in_obligation", "DiplomaticAction.CanCallInObligation", K.BOOL),
    Field("can_owe_obligation", "DiplomaticAction.CanOweObligation", K.BOOL),
    Field("can_potentially_owe_obligation", "DiplomaticAction.CanPotentiallyOweObligation", K.BOOL),
    Field("calling_in_obligation", "DiplomaticAction.IsCallingInObligation", K.BOOL),
    Field("promising_obligation", "DiplomaticAction.IsPromisingObligation", K.BOOL),
    Field("has_incomplete_target_data", "DiplomaticAction.HasIncompleteTargetData", K.BOOL),
    Field("acceptance_without_obligation", "DiplomaticAction.GetAcceptanceValueNoObligation", K.I32),
    Field("acceptance_calling_in", "DiplomaticAction.GetAcceptanceValueWithCallInObligation", K.I32),
    Field("acceptance_owing", "DiplomaticAction.GetAcceptanceValueWithOweObligation", K.I32),
    _text("acceptance_calling_in_description", "DiplomaticAction.GetWillAcceptDescWithCallInObligation"),
    _text("acceptance_owing_description", "DiplomaticAction.GetWillAcceptDescWithOweObligation"),
    _text("effects", "DiplomaticAction.GetEffectsDesc"),
    _text("tooltip", "DiplomaticAction.GetTooltip"),
)
_OBLIGATION_SUMMARY = (
    _id("native_player_country_id", "GetPlayer.GetID"),
    _text("native_player_name", "GetPlayer.GetNameNoFormatting"),
    _text("country_name", "Country.GetNameNoFormatting"),
)
_SUBJECT_SUMMARY = (
    *_OBLIGATION_SUMMARY,
    Field("target_is_subject", "Country.IsSubject", K.BOOL),
    Field("player_is_subject", "GetPlayer.IsSubject", K.BOOL),
    Field("target_is_subject_of_player", "Country.IsSubjectOf(GetPlayer.Self)", K.BOOL),
    Field("player_is_subject_of_target", "GetPlayer.IsSubjectOf(Country.Self)", K.BOOL),
)


def _subject_type_fields(prefix="SubjectType"):
    return (
        Field("key", prefix + ".GetKey", K.STRING),
        _text("name", prefix + ".GetNameNoFormatting"),
        _text("description", prefix + ".GetDesc"),
        Field("income_transfer", prefix + ".GetIncomeTransfer", K.FIXED),
        Field("can_start_own_diplomatic_plays", prefix + ".CanStartOwnDiplomaticPlays", K.BOOL),
        Field("join_overlord_wars", prefix + ".JoinOverlordWars", K.BOOL),
        Field("accepts_any_overlord_type", prefix + ".AcceptsAnyOverlordType", K.BOOL),
        Field("accepts_only_recognized_overlord", prefix + ".AcceptsOnlyRecognizedOverlordType", K.BOOL),
        Field("annex_on_country_formation", prefix + ".ShouldAnnexOnCountryFormation", K.BOOL),
    )


def _subject_current_fields(country):
    prefix = country + ".GetSubjectType"
    return (*_subject_type_fields(prefix),
        _id("overlord_country_id", country + ".GetOverlord.GetID"),
        _text("overlord_country_name", country + ".GetOverlord.GetNameNoFormatting"),
        # These are BOOL type-existence properties, not IsValid(Command).
        Field("higher_type_valid", prefix + ".GetHigherAutonomySubjectType(" + country + ".Self).IsValid", K.BOOL),
        Field("lower_type_valid", prefix + ".GetLowerAutonomySubjectType(" + country + ".Self).IsValid", K.BOOL))


_SUBJECT_PLAYS = tuple(field for key, prefix in (("dp_annex_subject", "annexation"), ("dp_independence", "independence"))
    for field in (
        _text(prefix + "_name", "GetDiplomaticPlayType('" + key + "').GetName"),
        _text(prefix + "_description", "GetDiplomaticPlayType('" + key + "').GetStartDescCountry(GetPlayer.Self,Country.Self)"),
        _text(prefix + "_tooltip", "GetDiplomaticPlayType('" + key + "').GetStartTooltipCountry(GetPlayer.Self,Country.Self)")))


_PACT_GROUP_SOURCE = "Country.AccessActiveDiplomaticPactTypes"
_PACT_SOURCE = "ContextualDiplomaticActionType.AccessCountry.AccessActiveDiplomaticPacts(ContextualDiplomaticActionType.Self)"
_PACT = "ContextualDiplomaticPact.GetDiplomaticPact"
_PACT_SUMMARY = (
    _id("observed_country_id", "Country.GetID"),
    _text("country_name", "Country.GetNameNoFormatting"),
    Field("is_subject", "Country.IsSubject", K.BOOL),
)
_PACT_GROUPS = (
    _id("group_country_id", "ContextualDiplomaticActionType.AccessCountry.GetID"),
    _text("group_country_name", "ContextualDiplomaticActionType.AccessCountry.GetNameNoFormatting"),
    Field("group_type_key", "ContextualDiplomaticActionType.GetDiplomaticActionType.GetKey", K.STRING),
    _text("group_type_name", "ContextualDiplomaticActionType.GetDiplomaticActionType.GetNameNoFormatting"),
)


def _pact_fields(prefix):
    action = prefix + ".GetDiplomaticAction"
    return (
        _id("pact_id", prefix + ".GetID", K.U32),
        Field("pact_type_key", prefix + ".GetDiplomaticActionType.GetKey", K.STRING),
        _text("pact_type_name", prefix + ".GetDiplomaticActionType.GetNameNoFormatting"),
        _text("name", prefix + ".GetName"),
        _text("description", prefix + ".GetDesc"),
        _text("pact_tooltip", prefix + ".GetTooltip"),
        _id("first_country_id", prefix + ".GetFirstCountry.GetID"),
        _text("first_country_name", prefix + ".GetFirstCountry.GetNameNoFormatting"),
        _id("second_country_id", prefix + ".GetSecondCountry.GetID"),
        _text("second_country_name", prefix + ".GetSecondCountry.GetNameNoFormatting"),
        Field("subject_relation", prefix + ".IsSubjectRelation", K.BOOL),
        Field("has_daily_relations_progress", prefix + ".HasDailyRelationsProgress", K.BOOL),
        Field("daily_relations_progress", prefix + ".GetDailyRelationsProgress", K.FIXED),
        _text("about_to_break_reason", prefix + ".GetAboutToBreakReasonDesc"),
        _text("auto_break_reason", prefix + ".GetAutoBreakReasonDesc"),
        _text("outliner_effect", prefix + ".GetOutlinerEffectDesc"),
        Field("linked_action_type_key", action + ".GetType.GetKey", K.STRING),
        _id("linked_action_first_country_id", action + ".GetFirstCountry.GetID"),
        _id("linked_action_second_country_id", action + ".GetSecondCountry.GetID"),
        Field("linked_action_maintenance_cost", action + ".GetMaintenanceCost", K.FIXED),
        Field("linked_action_base_maintenance_cost", action + ".GetBaseMaintenanceCost", K.FIXED),
        _text("linked_action_maintenance_description", action + ".GetMaintenanceCostDesc"),
    )


_PACT_ROWS = (
    _id("group_country_id", "ContextualDiplomaticActionType.AccessCountry.GetID"),
    Field("group_type_key", "ContextualDiplomaticActionType.GetDiplomaticActionType.GetKey", K.STRING),
    _id("context_country_id", "ContextualDiplomaticPact.GetContextCountry.GetID"),
    _text("context_country_name", "ContextualDiplomaticPact.GetContextCountry.GetNameNoFormatting"),
    _id("other_country_id", "ContextualDiplomaticPact.GetOtherCountry.GetID"),
    _text("other_country_name", "ContextualDiplomaticPact.GetOtherCountry.GetNameNoFormatting"),
    Field("ongoing_for_context_country", "ContextualDiplomaticPact.IsOngoingForContextCountry", K.BOOL),
    Field("ongoing_for_other_country", "ContextualDiplomaticPact.IsOngoingForOtherCountry", K.BOOL),
    _text("context_tooltip", "ContextualDiplomaticPact.GetTooltip"),
    *_pact_fields(_PACT),
)
_SUBJECT_PACT = (
    _id("observed_subject_country_id", "Country.GetID"),
    Field("observed_is_subject", "Country.IsSubject", K.BOOL),
    *_pact_fields("Country.GetSubjectPact"),
)


def _pact_scope(section, requested):
    """Keep fresh copied identities; an earlier occurrence is not a join key."""
    section["requested_group_occurrence"] = requested["source_index"]
    section["requested_group_country_id"] = requested.get("group_country_id")
    section["requested_group_type_key"] = requested.get("group_type_key")
    section["identity_semantics"] = "actual group and pact identities are copied in each callback; source_index is only an occurrence"
    scope_reasons = []
    for row in section["rows"]:
        issues = []
        for field in ("group_country_id", "group_type_key"):
            expected, actual = requested.get(field), row.get(field)
            if (field in requested.get("reasons", {}) or field in row["reasons"]
                    or expected is None or actual is None):
                issues.append({"field": field, "reason": "requested or fresh native group identity is unproven",
                    "requested": expected, "actual": actual})
            elif expected != actual:
                issues.append({"field": field, "reason": "native group identity changed at the traversed occurrence",
                    "requested": expected, "actual": actual})
        group_key, pact_key = row.get("group_type_key"), row.get("pact_type_key")
        if (group_key is None or pact_key is None
                or "group_type_key" in row["reasons"] or "pact_type_key" in row["reasons"]):
            issues.append({"field": "pact_type_key", "reason": "native pact-to-group type scope is unproven",
                "group_type_key": group_key, "actual": pact_key})
        elif group_key != pact_key:
            issues.append({"field": "pact_type_key", "reason": "actual native pact type differs from its fresh group type",
                "group_type_key": group_key, "actual": pact_key})
        row["scope_reasons"] = issues
        if issues:
            scope_reasons.append({"source_index": row["source_index"], "pact_id": row.get("pact_id"),
                "reason": "fresh copied pact scope differs or is unproven; actual observations are retained",
                "details": issues})
    section["scope_reasons"] = scope_reasons
    if scope_reasons:
        section["complete"], section["status"] = False, "partial"
        section["reasons"].extend(scope_reasons)
    return section


def _combine(country_id, **sections):
    results = []
    for name, section in sections.items():
        if isinstance(section, list):
            results.extend((f"{name}[{index}]", value) for index, value in enumerate(section))
        else:
            results.append((name, section))
    complete = all(value.get("complete") is True for _, value in results)
    return {"country_id": country_id, "read_complete": complete, "complete": complete,
        "status": "observed" if complete else "partial", "atomic_snapshot": False,
        "reasons": {name: value.get("reasons") for name, value in results if value.get("complete") is not True},
        "native_pages": [page for _, value in results for page in value.get("native_pages", ())], **sections}


def _absent(country_id, reason):
    return {"country_id": country_id, "status": "absent", "present": False,
            "complete": True, "reasons": {}, "reason": reason, "native_pages": []}


def _clean_false(snapshot, field):
    return snapshot.get(field) is False and field not in snapshot.get("reasons", {}).get("fields", {})


class CountryDiplomacyMixin:
    """Country-level native reads; actual source scope is explicit in each view."""

    async def pacts(self, *, country_id=None, include_subject=True):
        """Copy original selected-country active-pact UI rows, not action options.

        Native ContextualDiplomaticActionType.Self selects each child source.
        Every child repeats actual group/pact/type/party identities. Occurrences
        are traversal only; changed identities remain visible with scope reasons.
        complete/read_complete describe these requested native reads and their
        identity scope. Provider coverage and maintenance interpretation are
        separate scope_notes, not failures of a successfully copied view.
        No Pact owner, ID lookup, candidate command or universal catalog is made.
        """
        if type(include_subject) is not bool:
            raise TypeError("include_subject must be bool")
        async def read():
            fields = _PACT_SUMMARY if include_subject else _PACT_SUMMARY[:2]
            summary = await country_snapshot(self, "diplomacy.pacts.summary", fields,
                country_id=country_id, perform=False)
            selected = summary["country_id"]
            groups = await country_source(self, "diplomacy.pacts.groups", _PACT_GROUP_SOURCE,
                _PACT_GROUPS, country_id=selected, perform=False)
            sources = []
            for group in groups["rows"]:
                copied = await country_source(self, "diplomacy.pacts.instances", _PACT_SOURCE,
                    _PACT_ROWS, country_id=selected,
                    steps=(data.CreationStep(_PACT_GROUP_SOURCE, group["source_index"], groups["source_count"]),),
                    perform=False)
                sources.append(_pact_scope(copied, group))
            sections = {"summary": summary, "active_pact_groups": groups, "active_pact_sources": sources}
            if include_subject:
                if _clean_false(summary, "is_subject"):
                    sections["subject_pact"] = _absent(selected, "Native Country.IsSubject is false")
                else:
                    subject = await country_snapshot(self, "diplomacy.pacts.subject", _SUBJECT_PACT,
                        country_id=selected, perform=False)
                    subject["observation_scope"] = "original selected Country.GetSubjectPact supplemental chain; no inferred lookup or deduplication"
                    sections["subject_pact"] = subject
            result = _combine(selected, **sections)
            result["pacts"] = [row for source in sources for row in source["rows"]]
            result["subject_pact_requested"] = include_subject
            result["source_scope"] = "original selected-country active-pact UI collections"
            result["maintenance_cost_scope"] = "linked native DiplomaticAction maintenance; native values are copied without inferring a payer or rate"
            result["read_completeness_scope"] = "requested copied summary/groups/instances and optional subject-pact supplemental reads, including identity scope"
            result["command_validation_performed"] = False
            result["owning_pact_lookup_mapped"] = False
            # Read completeness describes the requested native view, not an
            # unproven claim about every possible provider or maintenance unit.
            # Keep those semantic limits separate from real read/scope errors.
            result["scope_notes"] = {
                "provider_coverage": "The original selected-country active-pact providers define this view; no whole-world or unfiltered registry is inferred.",
                "maintenance_semantics": "Linked GetMaintenanceCost values do not identify a payer, time unit or allocation rule."}
            return result
        return await self._ctx.perform("diplomacy.pacts", read)

    async def recognition(self, *, country_id=None, journal=True, country_types=()):
        """Read rank restrictions and the original Earning Recognition JE model.

        Country.GetJournalEntry takes JournalEntryType, not a string key. The
        native group/type sources identify je_earn_recognition by GetKey; each
        progress row repeats its actual entry key in the same callback. Rank
        labels and JE absence/completion are not country-recognition predicates.
        Optional country_types selects up to five original installed type keys
        for exact native is_country_type predicates. These equalities do not
        read the separate general recognition/is_unrecognized flag.
        """
        if isinstance(country_types, str):
            raise ValueError("country_types must be a tuple of original CountryType keys")
        country_types = tuple(country_types)
        if (len(country_types) > len(_COUNTRY_TYPE_KEYS)
                or any(key not in _COUNTRY_TYPE_KEYS for key in country_types)
                or len(set(country_types)) != len(country_types)):
            raise ValueError("choose distinct original CountryType keys: " + ", ".join(_COUNTRY_TYPE_KEYS))
        async def read():
            rank = await country_snapshot(self, "diplomacy.recognition.rank", _RANK,
                country_id=country_id, perform=False)
            selected = rank["country_id"]
            sources, progress, entries = [], [], []
            if journal:
                for state, getter in (("active", "GetActiveJournalEntryTypes"), ("inactive", "GetInactiveJournalEntryTypes")):
                    expression = _RECOGNITION_GROUP + "." + getter + "(Country.Self)"
                    source = await country_source(self, "diplomacy.recognition." + state, expression,
                        _JOURNAL, country_id=selected, perform=False)
                    source["journal_source_state"] = state
                    sources.append(source)
                    for row in source["rows"]:
                        if row.get("type_key") != _RECOGNITION_KEY or row.get("reasons", {}).get("type_key"):
                            continue
                        entries.append({"source_state": state, **row})
                        bars = await country_source(self, "diplomacy.recognition.progress_bars",
                            _ENTRY + ".GetScriptedProgressBars", _PROGRESS,
                            country_id=selected,
                            steps=(data.CreationStep(expression, row["source_index"], source["source_count"]),),
                            perform=False)
                        bars["requested_type_occurrence"] = row["source_index"]
                        bars["source_state"] = state
                        bars["identity_semantics"] = "entry_type_key is copied in this callback; no cached-index join"
                        bars["requested_type_key"] = _RECOGNITION_KEY
                        scope_reasons = []
                        for bar in bars["rows"]:
                            actual_key = bar.get("entry_type_key")
                            key_error = bar["reasons"].get("entry_type_key")
                            if key_error or actual_key is None:
                                matches = None
                                reason = "The copied progress row's journal-entry key is unproven; it is not attributed to Earning Recognition."
                            elif actual_key != _RECOGNITION_KEY:
                                matches = False
                                reason = "The native progress row belongs to a different journal entry; the earlier source index was only an occurrence."
                            else:
                                matches, reason = True, None
                            bar["requested_type_key_matches"] = matches
                            if reason:
                                evidence = {"source_index": bar["source_index"],
                                    "requested_type_key": _RECOGNITION_KEY,
                                    "actual_type_key": actual_key, "reason": reason}
                                bar["identity_scope_reason"] = reason
                                scope_reasons.append(evidence)
                        bars["identity_scope_reasons"] = scope_reasons
                        if scope_reasons:
                            bars["complete"], bars["status"] = False, "partial"
                            bars["reasons"].extend(scope_reasons)
                        progress.append(bars)
            sections = {"rank": rank, "journal_sources": sources, "progress_bars": progress}
            if country_types:
                observations = []
                scope = script.Scope.id(script.ScopeType.COUNTRY, selected)
                for key in country_types:
                    text = "is_country_type = " + key
                    native = await self._ctx.query("diplomacy.recognition.country_type." + key,
                        lambda text=text: script.trigger(self._ctx.native, text, scope,
                            adapter=self._ctx.adapters["script"], process=self._ctx.process))
                    reasons = []
                    if native.error:
                        reasons.append("native script error " + str(native.error))
                    if native.scope != scope:
                        reasons.append("native returned scope differs from the requested full Country ID")
                    if not native.scope_valid:
                        reasons.append("native country scope was not valid")
                    if not native.parsed:
                        reasons.append("native predicate was not parsed")
                    if not native.executed:
                        reasons.append("native predicate was not executed")
                    if native.kind != script.ScriptKind.TRIGGER or native.boolean is None:
                        reasons.append("native trigger boolean is unavailable")
                    observations.append({"type_key": key, "expression": text,
                        "requested_scope": scope, "native_result": native,
                        "scope": native.scope, "error": native.error,
                        "scope_valid": native.scope_valid, "parsed": native.parsed,
                        "executed": native.executed, "raw_boolean": native.raw_boolean,
                        "boolean": native.boolean,
                        "matches": native.boolean if not reasons else None,
                        "diagnostic": native.diagnostic, "diagnostic_raw": native.diagnostic_raw,
                        "diagnostic_bytes": native.diagnostic_bytes,
                        "diagnostic_truncated": native.diagnostic_truncated,
                        "complete": not reasons, "reasons": reasons})
                complete = all(row["complete"] for row in observations)
                sections["country_type_observations"] = {"rows": observations,
                    "complete": complete, "status": "observed" if complete else "partial",
                    "reasons": [{"type_key": row["type_key"], "reasons": row["reasons"]}
                        for row in observations if row["reasons"]], "native_pages": [],
                    "scope": "independent exact original CountryType equalities; not the general recognition flag",
                    "atomic_snapshot": False}
            result = _combine(selected, **sections)
            result["country_type_predicates_requested"] = list(country_types)
            result["recognition_entries"] = entries
            result["requested_journal_key"] = _RECOGNITION_KEY
            result["journal_requested"] = bool(journal)
            result["complete"] = False
            result["status"] = "partial"
            result["reasons"]["recognition_predicate"] = "A general native recognition/is_unrecognized flag is not mapped here. Optional exact CountryType equalities, rank text and journal state are not substituted for it."
            result["recognition_predicate_mapped"] = False
            result["recognized"] = None
            result["command_validation_performed"] = False
            result["completeness_scope"] = "rank, requested journal evidence and optional exact CountryType predicates are described by read_complete; general country recognition remains unknown"
            return result
        return await self._ctx.perform("diplomacy.recognition", read)

    async def obligations(self, *, country_id=None, action_contexts=True):
        """Native debtors/creditors, expiry and per-action obligation capabilities.

        From/To dates follow the original GUI's debtor/creditor owner context.
        Nested action contexts target the native counterparty and retain their
        own first/second-country IDs. Their actor is the original native player,
        not an impersonation of a foreign country_id. CanCall/CanOwe are not
        promoted into universal permissions for an obligation relationship.
        """
        async def read():
            summary = await country_snapshot(self, "diplomacy.obligations.summary", _OBLIGATION_SUMMARY,
                country_id=country_id, perform=False)
            selected = summary["country_id"]
            sections, action_rows = {"summary": summary}, []
            for direction in ("from", "to"):
                source_expression = "Country.GetObligationsOwed" + direction.title()
                source = await country_source(self, "diplomacy.obligations." + direction,
                    source_expression, _obligation_fields(direction), country_id=selected, perform=False)
                sections["owed_" + direction] = source
                if action_contexts:
                    counterparty = _DEBTOR if direction == "from" else _CREDITOR
                    for row in source["rows"]:
                        for family in _ACTION_FAMILIES:
                            actions = await country_source(self, "diplomacy.obligations.actions." + family,
                                counterparty + ".GetAll" + family + "DiploActions", _OBLIGATION_ACTIONS,
                                country_id=selected,
                                steps=(data.CreationStep(source_expression, row["source_index"], source["source_count"]),),
                                perform=False)
                            actions["requested_relation_occurrence"] = row["source_index"]
                            actions["relationship_source"] = source_expression
                            actions["action_family"] = family
                            actions["identity_semantics"] = "relation_id and both parties are native fields from this callback; not joined by occurrence"
                            action_rows.append(actions)
            sections["action_contexts"] = action_rows
            result = _combine(selected, **sections)
            result["action_contexts_requested"] = bool(action_contexts)
            result["action_actor_scope"] = "original native player; each action exposes its own first/second-country IDs"
            result["command_validation_performed"] = False
            result["obligation_capability_scope"] = "per native DiplomaticAction; not a relationship-wide permission"
            result["action_contexts_status"] = "observed" if action_contexts and result["read_complete"] else "partial" if action_contexts else "not_requested"
            result["completeness_scope"] = "requested copied obligation relations and optional action contexts; not command validation"
            return result
        return await self._ctx.perform("diplomacy.obligations", read)

    async def subject_options(self, *, country_id=None, include_types=True):
        """Native subject types and actual subject/overlord action descriptors.

        Complete means the requested copied reads completed. Available,
        potential and all remain separate original sources. Neither membership
        nor type-existence flags are command validation. Annexation and
        independence are play-type previews only; demand/forced-play mode is
        a separate confirmation contract, not part of this read's completeness.
        """
        from .actions import DIPLOMATIC_ACTION_FIELDS
        async def read():
            summary = await country_snapshot(self, "diplomacy.subject_options.summary", _SUBJECT_SUMMARY,
                country_id=country_id, perform=False)
            selected = summary["country_id"]
            sections = {"summary": summary}
            for role, country in (("target", "Country"), ("player", "GetPlayer")):
                if _clean_false(summary, role + "_is_subject"):
                    sections[role + "_subject_type"] = _absent(selected, "Native " + role + " IsSubject is false")
                    continue
                current = await country_snapshot(self, "diplomacy.subject_options." + role + ".type",
                    _subject_current_fields(country), country_id=selected, perform=False)
                sections[role + "_subject_type"] = current
                for direction, getter in (("higher", "GetHigherAutonomySubjectType"), ("lower", "GetLowerAutonomySubjectType")):
                    name = role + "_" + direction + "_subject_type"
                    if _clean_false(current, direction + "_type_valid"):
                        sections[name] = _absent(selected, "Native adjacent SubjectType.IsValid is false")
                    else:
                        prefix = country + ".GetSubjectType." + getter + "(" + country + ".Self)"
                        sections[name] = await country_snapshot(self, "diplomacy.subject_options." + name,
                            _subject_type_fields(prefix), country_id=selected, perform=False)
            if include_types:
                sections["subject_types"] = await country_source(self, "diplomacy.subject_options.types", "GetSubjectTypes",
                    _subject_type_fields(), country_id=selected, perform=False)
            actions = []
            for family in ("Subject", "Overlord"):
                for scope in ("All", "Available", "Potential"):
                    source = "Country.Get" + scope + family + "DiploActions"
                    values = await country_source(self, "diplomacy.subject_options." + scope + family,
                        source, DIPLOMATIC_ACTION_FIELDS, country_id=selected, perform=False)
                    values["action_family"] = family
                    values["native_source_scope"] = scope
                    actions.append(values)
            sections["actions"] = actions
            sections["play_previews"] = await country_snapshot(self, "diplomacy.subject_options.play_previews", _SUBJECT_PLAYS,
                country_id=selected, perform=False)
            result = _combine(selected, **sections)
            result["subject_types_requested"] = bool(include_types)
            result["command_validation_performed"] = False
            result["actor_scope"] = "original native GetPlayer; Country is the selected target only"
            result["completeness_scope"] = "requested copied subject types, six native action collections and play previews"
            result["selected_action_eligibility"] = {
                "status": "not_checked", "valid": None,
                "reason": "No candidate command or IsValid(command) was generated. Source membership is not final selected-action eligibility."}
            result["play_mode"] = {
                "status": "not_selected", "mode": None,
                "reason": "Annexation/independence previews do not choose or confirm demand-versus-forced-play mode."}
            return result
        return await self._ctx.perform("diplomacy.subject_options", read)
