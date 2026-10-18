"""Governance reads and selected law negotiation from original native sources.

All values come from registered native getters. These reads neither generate
commands nor decide political rules. Nested lists use original provider creation
steps, not borrowed wrapper rebinding. Each nested row repeats its parent label
or full IG ID in the same callback; separate snapshots are never joined by name
or occurrence. Native errors, partial text and copied pages remain in the result.
"""
from __future__ import annotations

from ... import v3_data as data
from .._political_data import Field, country_snapshot, country_source
from .._run import KnownResultError, SourceUnavailable


K = data.Kind


def _text(name, expression):
    return Field(name, expression, K.STRING, text=True)


def _identity(name, expression, kind=K.U32):
    return Field(name, expression, kind, full_id=True)


_GOVERNMENT = (
    _text("name", "Country.GetGovernment.GetName"),
    _text("description", "Country.GetGovernment.GetDesc"),
    _text("transfer_of_power", "Country.GetGovernment.GetTransferOfPowerName"),
    _text("transfer_of_power_description", "Country.GetGovernment.GetTransferOfPowerDesc"),
    Field("has_ruler", "Country.HasRuler", K.BOOL),
    Field("has_heir", "Country.HasHeir", K.BOOL),
    Field("total_government_clout", "Country.GetTotalGovernmentClout", K.FIXED),
    Field("free_reform_days_remaining", "Country.GetRemainingDaysForFreeGovernmentReformRaw", K.I32),
    _text("interest_groups_description", "Country.GetGovernmentInterestGroupsDesc"),
    _text("interest_groups_population_description", "Country.GetGovernmentInterestGroupsPopulationDesc"),
)

_LEGITIMACY = (
    Field("legitimacy", "Country.GetGovernmentLegitimacy", K.I32),
    _text("breakdown", "Country.GetGovernmentLegitimacyDesc"),
    _text("level", "Country.GetGovernmentLegitimacyLevel.GetNameNoFormatting"),
    Field("level_lower_bound", "Country.GetGovernmentLegitimacyLevel.GetLowerBound", K.I32),
    Field("level_upper_bound", "Country.GetGovernmentLegitimacyLevel.GetUpperBound", K.I32),
    Field("level_width", "Country.GetGovernmentLegitimacyLevel.GetWidth", K.I32),
    _text("effects", "Country.GetGovernmentLegitimacyLevel.GetEffects"),
    Field("loyalists_or_radicals_gained", "Country.GetLoyalistsOrRadicalsGainedFromLegitimacy", K.I64),
)

_IG_COALITION = (
    _identity("id", "InterestGroup.GetID"),
    _text("name", "InterestGroup.GetNameNoFormatting"),
    Field("clout", "InterestGroup.GetClout", K.FIXED),
    Field("approval", "InterestGroup.GetApprovalValue", K.I32),
    Field("in_government", "InterestGroup.IsInGovernment", K.BOOL),
    Field("leading_government", "InterestGroup.IsLeadingGovernment", K.BOOL),
    _identity("leader_id", "InterestGroup.GetLeader.GetID", K.I32),
    _text("leader_name", "InterestGroup.GetLeader.GetFullNameNoFormatting"),
)

_PARTIES = (
    _text("name", "Party.GetNameNoFormatting"),
    Field("valid", "Party.IsValid", K.BOOL),
    Field("in_government", "Party.IsInGovernment", K.BOOL),
    Field("can_join_government", "Party.CanJoinGovernment", K.BOOL),
    _text("can_join_government_description", "Party.GetCanJoinGovernmentDesc"),
    _identity("leader_interest_group_id", "Party.GetLeader.GetID"),
    _text("leader_interest_group_name", "Party.GetLeader.GetNameNoFormatting"),
    _identity("leader_character_id", "Party.GetLeaderCharacter.GetID", K.I32),
    _text("leader_character_name", "Party.GetLeaderCharacter.GetFullNameNoFormatting"),
    _text("members_summary", "Party.GetMembersSummary"),
    _text("members_breakdown", "Party.GetMembersBreakdown"),
    Field("momentum", "Party.GetMomentum", K.FIXED),
    _text("momentum_description", "Party.GetMomentumDesc"),
    Field("last_election_voters", "Party.GetNumVotersLastElection", K.I64),
    Field("voting_power", "Party.GetCurrentVotingPower", K.I64),
    Field("voting_power_share", "Party.GetCurrentVotingPowerShare", K.FIXED),
    Field("poll_voters", "Party.GetCurrentPollNumVoters", K.I64),
    Field("poll_voters_difference", "Party.GetCurrentPollNumVotersDifference", K.I64),
    Field("poll_voting_power", "Party.GetCurrentPollVotingPower", K.I64),
    Field("poll_voting_power_difference", "Party.GetCurrentPollVotingPowerDifference", K.I64),
    Field("poll_voting_power_share", "Party.GetCurrentPollVotingPowerShare", K.FIXED),
)

_PARTY_MEMBERS = (
    _text("party_name", "Party.GetNameNoFormatting"),
    _identity("party_leader_interest_group_id", "Party.GetLeader.GetID"),
    *_IG_COALITION,
)

_ELECTIONS = (
    Field("has_elections", "Country.HasElections", K.BOOL),
    Field("campaigning", "Country.IsInElectionCampaignPeriod", K.BOOL),
    Field("days_until_election", "Country.GetDaysUntilElection", K.I32),
    _text("next_election_date", "Country.GetNextElectionDate"),
    Field("electoral_confidence", "Country.GetElectoralConfidence", K.FIXED),
    _text("electoral_confidence_effects", "Country.GetElectoralConfidenceEffectDesc"),
    _text("poll_description", "Country.GetElectionPollTooltip"),
    _text("last_results_description", "Country.GetLastElectionResultsTooltip"),
    Field("has_trend_data", "Country.HasElectionTrendData", K.BOOL),
    Field("has_previous_election", "Country.GetElection.HasPreviousElection", K.BOOL),
    Field("election_campaigning", "Country.GetElection.IsInCampaignPeriod", K.BOOL),
    _text("title", "Country.GetElection.GetTitle"),
    _text("election_date", "Country.GetElection.GetDateFormatted"),
    _text("last_election_date_description", "Country.GetElection.GetLastElectionDateDesc"),
    Field("last_election_year", "Country.GetElection.GetLastElectionYearRaw", K.I32),
    Field("next_election_year", "Country.GetElection.GetNextElectionYearRaw", K.I32),
    Field("eligible_voters", "Country.GetElection.GetEligibleVoters", K.I64),
    Field("total_voters", "Country.GetElection.GetTotalVoters", K.I64),
    Field("voter_turnout", "Country.GetElection.GetVoterTurnout", K.FIXED),
    _text("votes_graph_start", "Country.GetElection.GetVotesGraphDateStart"),
    _text("votes_graph_end", "Country.GetElection.GetVotesGraphDateEnd"),
    Field("votes_graph_max", "Country.GetElection.GetVotesGraphMax", K.FIXED),
)

_NEGOTIATION = (
    Field("negotiating", "Country.IsNegotiating", K.BOOL),
    Field("available_negotiations", "Country.GetNumAvailableNegotiations", K.I32),
    _identity("negotiating_interest_group_id", "Country.GetNegotiatingInterestGroup.GetID"),
    _text("negotiating_interest_group_name", "Country.GetNegotiatingInterestGroup.GetNameNoFormatting"),
    _text("enacting_law", "Country.GetLawBeingEnacted.GetNameNoFormatting"),
    Field("law_is_being_enacted", "Country.GetLawBeingEnacted.IsBeingEnacted", K.BOOL),
    Field("enactment_progress", "Country.GetLawEnactmentProgress", K.FIXED),
    _text("approving_interest_groups", "Country.GetLawBeingEnacted.GetIGsApproveList"),
    _text("disapproving_interest_groups", "Country.GetLawBeingEnacted.GetIGsDisapproveList"),
    _text("undecided_interest_groups", "Country.GetLawBeingEnacted.GetIGsUndecidedList"),
)

_NEGOTIATION_CANDIDATES = (
    *_IG_COALITION,
    Field("amenability", "InterestGroup.GetAmenability", K.FIXED),
    _text("amenability_label", "InterestGroup.GetAmenabilityLabel"),
    Field("has_negotiated", "InterestGroup.HasNegotiated", K.BOOL),
    Field("negotiating", "InterestGroup.IsNegotiating", K.BOOL),
    _text("ideologies_description", "InterestGroup.GetIdeologyDesc"),
)

_IG_POLITICS = (
    *_IG_COALITION,
    Field("marginal", "InterestGroup.IsMarginal", K.BOOL),
    Field("powerful", "InterestGroup.IsPowerful", K.BOOL),
    Field("insurrectionary", "InterestGroup.IsInsurrectionary", K.BOOL),
    Field("revolutionary", "InterestGroup.IsRevolutionary", K.BOOL),
    Field("secessionist", "InterestGroup.IsSecessionist", K.BOOL),
    Field("political_strength", "InterestGroup.GetPoliticalStrength", K.I64),
    Field("political_strength_including_party_votes", "InterestGroup.GetTotalPoliticalStrengthIncludingPartyVotes", K.I64),
    Field("population", "InterestGroup.GetPopulation", K.I64),
    Field("politically_active", "InterestGroup.GetPoliticallyActiveRaw", K.I64),
    Field("politically_active_percentage", "InterestGroup.GetPoliticallyActivePercentage", K.FIXED),
    Field("country_support", "InterestGroup.GetCountrySupport", K.FIXED),
    _text("influence_description", "InterestGroup.GetInfluenceDesc"),
    _text("influence_reason", "InterestGroup.GetInfluenceReason"),
    _text("pop_potential_description", "InterestGroup.GetPopPotentialDesc"),
    _text("political_strength_description", "InterestGroup.GetPoliticalStrengthDesc"),
    _text("ideologies_description", "InterestGroup.GetIdeologyDesc"),
    _text("traits_description", "InterestGroup.GetTraitsDesc"),
    _text("leader_ideology", "InterestGroup.GetLeaderIdeology.GetNameNoFormatting"),
    _text("leader_ideology_description", "InterestGroup.GetLeaderIdeologyDesc"),
    Field("leader_popularity", "InterestGroup.GetLeader.GetPopularity", K.FIXED),
    Field("leader_age", "InterestGroup.GetLeader.GetAge", K.I32),
    Field("possible_leader_ideologies_count", "InterestGroup.GetNumPossibleLeaderIdeologies", K.I32),
    Field("has_party", "InterestGroup.HasParty", K.BOOL),
    _text("party_name", "InterestGroup.GetParty.GetNameNoFormatting"),
)

_IG_PARENT = (
    _identity("interest_group_id", "InterestGroup.GetID"),
    _text("interest_group_name", "InterestGroup.GetNameNoFormatting"),
)

_IDEOLOGIES = (
    *_IG_PARENT,
    _text("name", "Ideology.GetNameNoFormatting"),
    _text("description", "Ideology.GetDesc"),
    _text("law_stances", "Ideology.GetLawStanceDesc"),
    Field("character_ideology", "Ideology.IsCharacterIdeology", K.BOOL),
)

_TRAITS = (
    *_IG_PARENT,
    _text("name", "InterestGroupTrait.GetNameNoFormatting"),
    _text("description", "InterestGroupTrait.GetDesc"),
    # The original GUI uses this exact parent-context argument:
    # gui/custom_tooltip.gui and gui/interest_group_panel.gui.
    Field("enabled", "InterestGroupTrait.IsEnabled(InterestGroup.Self)", K.BOOL),
    _text("base_modifiers", "InterestGroupTrait.GetBaseModifiersDesc"),
    _text("base_triggers", "InterestGroupTrait.GetBaseTriggersDesc"),
)

_PARTY_IDENTITY = {
    "stable_id_available": False,
    "reason": "The registered Party getters expose no native ID or key. Names and source_index are not command selectors.",
}


def _character_fields(prefix):
    return (
        _identity("id", prefix + ".GetID", K.I32),
        _text("name", prefix + ".GetFullNameNoFormatting"),
        Field("valid", prefix + ".IsValid", K.BOOL),
        Field("age", prefix + ".GetAge", K.I32),
        _text("roles", prefix + ".GetAllRoleNames"),
        Field("popularity", prefix + ".GetPopularity", K.FIXED),
        _text("popularity_description", prefix + ".GetPopularityDesc"),
        Field("prominence", prefix + ".GetProminence", K.FIXED),
        _text("prominence_description", prefix + ".GetProminenceDesc"),
        _text("ideology", prefix + ".GetIdeology.GetNameNoFormatting"),
        _text("traits_description", prefix + ".GetTraitsDesc"),
    )


def _bundle(country_id, **sections):
    """Keep each helper result intact, including errors and page objects."""
    results = []
    for name, section in sections.items():
        if isinstance(section, list):
            results.extend((f"{name}[{index}]", value) for index, value in enumerate(section))
        else:
            results.append((name, section))
    complete = all(value.get("complete") is True for _, value in results)
    return {
        "country_id": country_id,
        "status": "observed" if complete else "partial",
        "complete": complete,
        "reasons": {name: value.get("reasons") for name, value in results if value.get("complete") is not True},
        "native_pages": [page for _, value in results for page in value.get("native_pages", ())],
        "atomic_snapshot": False,
        **sections,
    }


def _absent(country_id):
    # A proven HasRuler/HasHeir false is not a missing getter or a zero ID.
    return {"country_id": country_id, "present": False, "status": "absent",
            "complete": True, "reasons": {}, "native_pages": []}


async def _nested(api, label, parent, source_expression, fields):
    """Read independent nested observations, not an index-based joined graph."""
    observations = []
    for row in parent["rows"]:
        index = row["source_index"]
        step = data.CreationStep(parent["source_expression"], index, parent["source_count"])
        value = await country_source(api, label, source_expression, fields,
            country_id=parent["country_id"], steps=(step,), perform=False)
        # This records the request, not the identity of the actual parent. The
        # latter comes only from parent getters repeated in each native row.
        value["requested_parent_occurrence"] = index
        value["parent_identity_semantics"] = "parent fields in each row were read in the original nested provider; no cross-snapshot join"
        observations.append(value)
    return observations


class GovernanceMixin:
    """Governance reads and selected negotiation through original native commands.

    Read results retain native page objects. They are not an atomic game
    snapshot. A complete read does not establish command eligibility or stable
    identities where the native getter set has none. See result limitations.
    """

    async def government(self, *, country_id=None):
        """Government type, succession, rulers and the current coalition."""
        async def read():
            summary = await country_snapshot(self, "politics.government.summary", _GOVERNMENT,
                country_id=country_id, perform=False)
            selected = summary["country_id"]
            characters = {}
            for role, getter in (("ruler", "GetRuler"), ("heir", "GetHeir")):
                presence_field = "has_" + role
                presence_errors = summary.get("reasons", {}).get("fields", {})
                if summary.get(presence_field) is False and presence_field not in presence_errors:
                    characters[role] = _absent(selected)
                else:
                    characters[role] = await country_snapshot(self, "politics.government." + role,
                        _character_fields("Country." + getter), country_id=selected, perform=False)
            groups = await country_source(self, "politics.government.interest_groups",
                "Country.AccessInterestGroupsInGovernment", _IG_COALITION,
                country_id=selected, perform=False)
            parties = await country_source(self, "politics.government.parties",
                "Country.GetPartiesInGovernment", _PARTIES, country_id=selected, perform=False)
            result = _bundle(selected, summary=summary, **characters, interest_groups=groups, parties=parties)
            result["party_selector_identity"] = dict(_PARTY_IDENTITY)
            return result
        return await self._ctx.perform("politics.government", read)

    async def legitimacy(self, *, country_id=None):
        """Native points, level bounds, breakdown and signed loyalty effect.

        ``loyalists_or_radicals_gained`` is the original signed native value,
        not two inferred counts. Level bounds and effects are not hardcoded.
        """
        return await country_snapshot(self, "politics.legitimacy", _LEGITIMACY, country_id=country_id)

    async def parties(self, *, country_id=None):
        """Active parties, momentum, polls and original nested IG memberships.

        Party names and source occurrences are not stable selectors. Membership
        rows repeat their actual parent party label/leader in the same callback;
        they are not attached to an earlier party snapshot by index or name.
        """
        async def read():
            parties = await country_source(self, "politics.parties.rows", "Country.AccessActiveParties",
                _PARTIES, country_id=country_id, perform=False)
            members = await _nested(self, "politics.parties.members", parties, "Party.GetMembers", _PARTY_MEMBERS)
            result = _bundle(parties["country_id"], parties=parties, memberships=members)
            result["party_selector_identity"] = dict(_PARTY_IDENTITY)
            return result
        return await self._ctx.perform("politics.parties", read)

    async def elections(self, *, country_id=None):
        """Election timetable, campaigning, voters, confidence and party results.

        Election data is read even when HasElections is false: historical
        results can still exist. Missing objects remain native errors, not
        invented zero turnout or dates. Fixed-point shares remain native units.
        """
        async def read():
            summary = await country_snapshot(self, "politics.elections.summary", _ELECTIONS,
                country_id=country_id, perform=False)
            selected = summary["country_id"]
            parties = await country_source(self, "politics.elections.parties", "Country.GetElection.GetParties",
                _PARTIES, country_id=selected, perform=False)
            independent = await country_source(self, "politics.elections.independent_interest_groups",
                "Country.GetElection.GetIndependentIGs", _IG_COALITION, country_id=selected, perform=False)
            last_parties = await country_source(self, "politics.elections.last_parties", "Country.AccessLastElectionParties",
                _PARTIES, country_id=selected, perform=False)
            result = _bundle(selected, summary=summary, parties=parties,
                independent_interest_groups=independent, last_election_parties=last_parties)
            result["party_selector_identity"] = dict(_PARTY_IDENTITY)
            return result
        return await self._ctx.perform("politics.elections", read)

    async def negotiation_options(self, *, country_id=None):
        """Current enactment/negotiation, IG amenability and native stance text.

        All native interest groups are returned, not a guessed eligible subset.
        This read does not generate StartNegotiationWith commands to validate
        them. Amenability and HasNegotiated are observations, not eligibility.
        """
        async def read():
            summary = await country_snapshot(self, "politics.negotiation_options.summary", _NEGOTIATION,
                country_id=country_id, perform=False)
            candidates = await country_source(self, "politics.negotiation_options.candidates",
                "Country.AccessAllInterestGroups", _NEGOTIATION_CANDIDATES,
                country_id=summary["country_id"], perform=False)
            result = _bundle(summary["country_id"], summary=summary, candidates=candidates)
            result["candidate_scope"] = "all native interest groups; not filtered by command eligibility"
            result["eligibility_checked"] = False
            result["read_complete"] = result["complete"]
            result["complete"] = False
            result["status"] = "partial"
            result["reasons"]["eligibility"] = "Native command eligibility was not queried."
            result["limitations"] = ["Eligibility requires native StartNegotiationWith validation, which this read does not perform."]
            return result
        return await self._ctx.perform("politics.negotiation_options", read)

    async def negotiate(self, interest_group_id, *, readback=True):
        """Start ordinary law negotiation with one explicitly selected full IG ID.

        Selection uses InterestGroup.GetID from the country's retained native
        source, never a list occurrence or name. Only that selected command is
        generated, validated and (if allowed) submitted. Native rejection and
        acceptance remain intact. Readback is separate evidence of the current
        negotiation, not a claim that its events or amendments are completed.
        """
        if type(interest_group_id) is not int or not 0 <= interest_group_id < 0xffffffff:
            raise ValueError("interest_group_id must be a complete uint32 ID; zero is valid")
        from .._political_actions import selected_command

        async def work():
            async with self._mutation_lock:
                selected_country = {}

                async def select(run):
                    country = await run.country()
                    selected_country["id"] = run.actor
                    issues = run.public_result.setdefault("selection_issues", [])
                    async for index, element, group in run.elements("Country.AccessAllInterestGroups", (country,), "InterestGroup"):
                        identity_query = None
                        def read_identity():
                            nonlocal identity_query
                            identity_query = data.evaluate(run.native, "InterestGroup.GetID", (group,),
                                output_bytes=65536, adapter=run.adapters["data"], process=run.process)
                            return identity_query
                        try:
                            identity = await run.call("InterestGroup.GetID", read_identity)
                        except KnownResultError as error:
                            original_query = getattr(error, "query", None)
                            if original_query is None:
                                original_query = identity_query
                            partial = getattr(error, "partial_result", None)
                            if partial is None:
                                partial = getattr(original_query, "partial_result", None)
                            issues.append({"source_index": index, "source_result": element, "binding": group,
                                "native_result": error.result, "error": error, "query": original_query,
                                "partial_result": partial, "reason": str(error)})
                            # A typed error from an unsettled query is not a
                            # completed failed identity read. Keep its graph;
                            # never continue into another selected action.
                            if (getattr(run, "retain_unknown", False)
                                    or getattr(run, "uncertain_query", None) is not None):
                                raise
                            continue
                        value = identity.value
                        if identity.kind != K.U32 or type(value) is not int or not 0 <= value < 0xffffffff:
                            issues.append({"source_index": index, "source_result": element, "binding": group,
                                "native_result": identity, "error": None, "query": identity_query,
                                "partial_result": getattr(identity_query, "partial_result", None),
                                "reason": "InterestGroup.GetID did not return a valid native uint32 reference"})
                            continue
                        if value == interest_group_id:
                            # Source, element and scalar owners stay in this run
                            # until the selected command's outcome is settled.
                            return country, group
                    raise SourceUnavailable(f"Interest group {interest_group_id} is not in Country.AccessAllInterestGroups")

                result = await selected_command(self, "negotiate",
                    "Country.StartNegotiationWith(InterestGroup.Self)", select, perform=False)
                result.update(interest_group_id=interest_group_id,
                    country_id=selected_country.get("id"), effect_observed=None)
                if not readback or result.get("accepted") is not True:
                    return result
                if result.get("ownership_settled") is not True:
                    result["readback_skipped"] = "Original command ownership remains unsettled"
                    return result
                try:
                    after = await country_snapshot(self, "politics.negotiate.after", (
                        Field("negotiating", "Country.IsNegotiating", K.BOOL),
                        _identity("interest_group_id", "Country.GetNegotiatingInterestGroup.GetID"),
                        _text("interest_group_name", "Country.GetNegotiatingInterestGroup.GetNameNoFormatting"),
                    ), country_id=selected_country["id"], perform=False)
                except Exception as error:
                    result["readback_error"] = error
                    result["readback_complete"] = False
                    return result
                result["after"] = after
                result["readback_complete"] = after["complete"]
                field_errors = after.get("reasons", {}).get("fields", {})
                if not any(name in field_errors for name in ("negotiating", "interest_group_id")):
                    result["effect_observed"] = (after["negotiating"] is True
                        and after["interest_group_id"] == interest_group_id)
                return result
        return await self._ctx.perform("politics.negotiate", work)

    async def interest_group_politics(self, *, country_id=None, details=True):
        """IG leadership, strength/support, ideologies and active/inactive traits.

        With details=True (default), structured ideologies, possible leader
        ideologies and traits are read in their original nested providers. Each
        nested row includes the actual parent IG ID from that callback. No
        stable ideology/trait ID is invented from a name or list occurrence.
        details=False returns only the IG rows and native summary descriptions.
        """
        async def read():
            groups = await country_source(self, "politics.interest_group_politics.rows",
                "Country.AccessAllInterestGroups", _IG_POLITICS, country_id=country_id, perform=False)
            sections = {"interest_groups": groups}
            if details:
                for name, source, fields in (
                        ("ideologies", "InterestGroup.AccessAllIdeologies", _IDEOLOGIES),
                        ("traits", "InterestGroup.AccessTraits", _TRAITS),
                        ("possible_leader_ideologies", "InterestGroup.GetPossibleLeaderIdeologies", _IDEOLOGIES)):
                    sections[name] = await _nested(self, "politics.interest_group_politics." + name,
                        groups, source, fields)
            result = _bundle(groups["country_id"], **sections)
            result["details_included"] = bool(details)
            result["definition_selector_identity"] = {
                "stable_id_available": False,
                "reason": "Registered Ideology and InterestGroupTrait getters provide display data, not native IDs or keys.",
            }
            return result
        return await self._ctx.perform("politics.interest_group_politics", read)
