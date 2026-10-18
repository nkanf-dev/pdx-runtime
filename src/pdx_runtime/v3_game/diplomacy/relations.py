"""Political diplomacy observations using original bounded Data providers.

No method below chooses policy, generates commands for discovery, validates a
candidate list, or parses localized names as IDs. Targeted bilateral views use
the original GetPlayer context and return that observed player ID explicitly.
"""
from __future__ import annotations

from ... import v3_data as data
from .._political_data import Field, country_snapshot, country_source
from .._run import SourceUnavailable

I=data.Kind.I32
F=data.Kind.FIXED
S=data.Kind.STRING
B=data.Kind.BOOL
FLOAT=data.Kind.FLOAT


def _text(name, expression):
    return Field(name, expression, S, text=True)


def _country(prefix="Country", *, name="name"):
    return (Field("id",prefix+".GetID",I,full_id=True),
            Field("tag",prefix+".GetTagName",S),_text(name,prefix+".GetName"))


def _join(parts):
    """Combine independent observations without claiming an atomic snapshot."""
    complete=all(part.get("complete",False) for part in parts.values())
    return {**parts,"complete":complete,"status":"observed" if complete else "partial",
            "atomic_snapshot":False}


class DiplomacyReadMixin:
    async def ranking(self, target=None):
        """Native prestige/rank, thresholds, rank-loss timer and source text."""
        fields=_country()+(
            Field("prestige","Country.GetPrestige",I),_text("prestige_breakdown","Country.GetPrestigeDesc"),
            _text("rank","Country.GetRank"),Field("rank_number","Country.GetRankNumber",I),
            Field("rank_progress","Country.GetRankProgress",FLOAT),_text("rank_description","Country.GetRankDesc"),
            Field("prestige_current_rank","Country.GetPrestigeForCurrentRank",I),
            Field("prestige_next_rank","Country.GetPrestigeForNextRank",I),
            Field("prestige_to_maintain_rank","Country.GetPrestigeToMaintainRank",I),
            Field("days_until_rank_loss","Country.GetDaysUntilRankLoss",I),
            _text("next_rank","Country.GetNextRank"),_text("highest_possible_rank","Country.GetHighestPossibleRank"))
        return await country_snapshot(self,"diplomacy.ranking",fields,country_id=target)

    async def infamy(self, target=None):
        """Native infamy amount, tier and source/consequence description."""
        return await country_snapshot(self,"diplomacy.infamy",_country()+(
            Field("infamy","Country.GetInfamy",F),_text("level","Country.GetInfamyLabel"),
            _text("description","Country.GetInfamyDesc")),country_id=target)

    async def attitude(self, target):
        """Target country's actual attitude toward the native controlled player.

        The source is GetPlayer, not an invented second Country binding. The
        returned source_country_id distinguishes this from explicit read-only
        country contexts. Catalysts and strategy explanations are native text.
        """
        relation="Country.GetAttitudeTowards(GetPlayer)"
        return await country_snapshot(self,"diplomacy.attitude",_country()+(
            Field("source_country_id","GetPlayer.GetID",I,full_id=True),
            _text("attitude",relation+".GetName"),Field("score",relation+".CalcScore",F),
            _text("description",relation+".GetDesc"),_text("reasons_text",relation+".GetReasonsDesc"),
            _text("strategic_desire",relation+".GetStrategicDesireDesc"),
            _text("catalyst",relation+".GetStrategicDesireCatalystDesc"),
            _text("ai_strategies","Country.GetAIStrategiesDesc")),country_id=target)

    async def interests(self, target=None):
        """Native interests with regional involvement/tier; no old declare toggle."""
        region="Interest.GetStrategicRegion"
        tier="Country.GetCurrentInterestTierTypeIn("+region+")"
        result=await country_source(self,"diplomacy.interests","Country.AccessInterests",(
            _text("region",region+".GetName"),Field("region_tooltip_tag",region+".GetTooltipTag",S),
            Field("owner_id","Interest.GetOwner.GetID",I,full_id=True),
            _text("status_name","Interest.GetInterestStatusName"),
            _text("source_description","Interest.GetInterestSourceDesc"),
            Field("involvement","Country.GetCurrentInvolvementIn("+region+")",F),
            Field("target_involvement","Country.GetTargetInvolvementIn("+region+")",F),
            Field("growth","Country.GetInvolvementGrowth("+region+")",F),
            _text("target_breakdown","Country.GetTargetInvolvementDescIn("+region+")"),
            _text("tier",tier+".GetName"),Field("tier_rank",tier+".GetRank",I),
            _text("tier_effects",tier+".GetDescription")),country_id=target)
        if isinstance(result,dict):
            result["selector_identity"]="StrategicRegion tooltip/name are original descriptors, not parsed IDs; source_index is an occurrence."
        return result

    async def liberty_desire(self, target=None):
        """Liberty desire, level, weekly drift and original contributing factors."""
        return await country_snapshot(self,"diplomacy.liberty_desire",_country()+(
            Field("is_subject","Country.IsSubject",B),Field("liberty_desire","Country.GetLibertyDesireRaw",F),
            _text("level","Country.GetLibertyDesireLevelLabel"),
            Field("weekly_change","Country.GetWeeklyLibertyDesireChange",F),
            _text("weekly_change_breakdown","Country.GetWeeklyLibertyDesireChangeDesc"),
            Field("support_independence_change","Country.GetWeeklyLibertyDesireChangeFromSupportIndependence",F),
            _text("support_independence_breakdown","Country.GetWeeklyLibertyDesireChangeFromSupportIndependenceDesc")),country_id=target)

    async def subjects(self, target=None):
        """All direct subjects with full IDs, type/autonomy rights and payment ratio."""
        return await country_source(self,"diplomacy.subjects","Country.GetSubjects",_country()+(
            _text("subject_type","Country.GetSubjectType.GetName"),
            Field("subject_type_key","Country.GetSubjectType.GetKey",S),
            _text("subject_type_description","Country.GetSubjectType.GetDesc"),
            Field("overlord_id","Country.GetOverlord.GetID",I,full_id=True),
            Field("top_overlord_id","Country.GetTopOverlord.GetID",I,full_id=True),
            Field("income_transfer_fraction","Country.GetSubjectType.GetIncomeTransfer",F),
            Field("can_start_plays","Country.GetSubjectType.CanStartOwnDiplomaticPlays",B),
            Field("joins_overlord_wars","Country.GetSubjectType.JoinOverlordWars",B),
            Field("liberty_desire","Country.GetLibertyDesireRaw",F)),country_id=target)

    async def subject_status(self, target=None):
        """Selected country's overlord hierarchy and native subject-type rules."""
        return await country_snapshot(self,"diplomacy.subject_status",_country()+(
            Field("is_subject","Country.IsSubject",B),
            Field("overlord_id","Country.GetOverlord.GetID",I,full_id=True),
            _text("overlord_name","Country.GetOverlord.GetName"),
            Field("top_overlord_id","Country.GetTopOverlord.GetID",I,full_id=True),
            _text("subject_type","Country.GetSubjectType.GetName"),
            Field("subject_type_key","Country.GetSubjectType.GetKey",S),
            Field("income_transfer_fraction","Country.GetSubjectType.GetIncomeTransfer",F),
            Field("can_start_plays","Country.GetSubjectType.CanStartOwnDiplomaticPlays",B),
            Field("joins_overlord_wars","Country.GetSubjectType.JoinOverlordWars",B),
            _text("rules","Country.GetSubjectType.GetDesc")),country_id=target)

    async def foreign_investment_rights(self, target):
        """Both permission directions with the native controlled player.

        This reports effective rights, not a guessed entitlement based on a
        treaty name. Construction/ownership operations belong to economy.
        """
        return await country_snapshot(self,"diplomacy.foreign_investment_rights",_country()+(
            Field("source_country_id","GetPlayer.GetID",I,full_id=True),
            Field("player_can_invest","GetPlayer.HasForeignInvestmentRights(Country.Self)",B),
            Field("target_can_invest","Country.HasForeignInvestmentRights(GetPlayer)",B)),country_id=target)

    async def power_bloc(self, target=None):
        """Country's actual bloc identity, leader, membership size/rank and effects."""
        bloc="Country.GetPowerBloc"
        return await country_snapshot(self,"diplomacy.power_bloc",(
            Field("country_id_observed","Country.GetID",I,full_id=True),
            Field("is_member","Country.IsInPowerBloc",B),Field("is_leader","Country.IsPowerBlocLeader",B),
            _text("name",bloc+".GetName"),_text("identity",bloc+".GetIdentity.GetName"),
            _text("identity_description",bloc+".GetIdentity.GetDesc"),
            Field("leader_id",bloc+".GetLeader.GetID",I,full_id=True),_text("leader",bloc+".GetLeader.GetName"),
            Field("member_count",bloc+".GetNumMembers",I),Field("rank",bloc+".GetRankRaw",I),
            _text("rank_description",bloc+".GetRankTooltip"),_text("members_description",bloc+".GetMembersBreakdown"),
            _text("leader_effects",bloc+".GetIdentity.GetLeaderEffectDesc"),
            _text("member_effects",bloc+".GetIdentity.GetMemberEffectDesc"),
            _text("bloc_effects",bloc+".GetIdentity.GetPowerBlocEffectDesc")),country_id=target)

    async def power_bloc_members(self, target=None):
        """Native current-member list; Country IDs come from each original member."""
        return await country_source(self,"diplomacy.power_bloc_members","Country.GetPowerBloc.GetMembers",_country()+(
            Field("prestige","Country.GetPrestige",I),_text("rank","Country.GetRank"),
            Field("is_bloc_leader","Country.IsPowerBlocLeader",B)),country_id=target)

    async def power_bloc_cohesion(self, target=None):
        """Cohesion/target/change and mandates stock, progress, rate and next date."""
        b="Country.GetPowerBloc."
        return await country_snapshot(self,"diplomacy.power_bloc_cohesion",(
            _text("name",b+"GetName"),Field("cohesion",b+"GetCohesion",F),
            Field("target",b+"GetCohesionTarget",F),Field("weekly_change",b+"GetCohesionWeeklyChange",F),
            _text("cohesion_breakdown",b+"GetCohesionDesc"),Field("mandates",b+"GetNumMandates",I),
            Field("mandate_progress",b+"GetCurrentMandateProgress",F),
            Field("mandate_progress_fraction",b+"GetCurrentMandateProgressPercentage",F),
            Field("weekly_mandate_progress",b+"GetWeeklyMandateProgress",F),
            _text("mandate_progress_breakdown",b+"GetWeeklyMandateProgressDesc"),
            Field("next_mandate_date",b+"GetNextMandateDate.GetString",S)),country_id=target)

    async def power_bloc_principles(self, target=None):
        """Current original principles and native effect/constraint descriptions.

        It does not pretend to initialize the principle-selection UI or infer
        mandate prices from a level number. Selection costs are a separate view.
        """
        return await country_source(self,"diplomacy.power_bloc_principles","Country.GetPowerBloc.GetPrinciples",(
            _text("name","PowerBlocPrinciple.GetName"),_text("group","PowerBlocPrinciple.GetGroup.GetName"),
            _text("description","PowerBlocPrinciple.GetDesc"),
            _text("requirements","PowerBlocPrinciple.GetIsPossibleDesc"),
            _text("incompatibilities","PowerBlocPrinciple.GetIncompatibilitiesDesc"),
            _text("leader_effects","PowerBlocPrinciple.GetLeaderEffectDesc"),
            _text("member_effects","PowerBlocPrinciple.GetMemberEffectDesc"),
            _text("bloc_effects","PowerBlocPrinciple.GetPowerBlocEffectDesc"),
            _text("institution_effects","PowerBlocPrinciple.GetInstitutionEffectDesc")),country_id=target)

    async def power_bloc_leverage(self, target=None):
        """Competing blocs' leverage over a selected country, from original sources."""
        return await country_source(self,"diplomacy.power_bloc_leverage","Country.GetPowerBlocsWithLeverage",(
            _text("name","PowerBloc.GetName"),Field("leader_id","PowerBloc.GetLeader.GetID",I,full_id=True),
            Field("current","PowerBloc.GetIndividualLeverage(Country.Self)",F),
            Field("target","Country.GetLeverageTarget(PowerBloc.Self)",F),
            Field("share","Country.GetCurrentLeverageShare(PowerBloc.Self)",F),
            Field("target_share","Country.GetTargetLeverageShare(PowerBloc.Self)",F),
            Field("weekly_gain","Country.GetWeeklyLeverageGain(PowerBloc.Self)",F),
            Field("advantage","PowerBloc.GetLeverageAdvantage(Country.Self)",F),
            _text("advantage_breakdown","PowerBloc.GetLeverageAdvantageDesc(Country.Self)")),country_id=target)

    async def power_bloc_budget(self, target=None):
        """Original bloc income/expense transfers, not trade subventions."""
        return await country_snapshot(self,"diplomacy.power_bloc_budget",(
            Field("income","Country.GetPowerBlocIncomeRaw",F),_text("income_breakdown","Country.GetPowerBlocIncomeDesc"),
            Field("expense","Country.GetPowerBlocExpenseRaw",F),_text("expense_breakdown","Country.GetPowerBlocExpenseDesc")),country_id=target)

    async def power_struggle(self, target=None):
        """Native leadership challenge text, current contender and prestige."""
        b="Country.GetPowerBloc."
        return await country_snapshot(self,"diplomacy.power_struggle",(
            _text("bloc_name",b+"GetName"),_text("description",b+"GetPowerStruggleDesc"),
            Field("contender_id",b+"GetPowerStruggleContender.GetID",I,full_id=True),
            _text("contender",b+"GetPowerStruggleContender.GetName"),
            Field("total_prestige",b+"GetTotalPrestige",I)),country_id=target)

    async def lobbies(self, target=None):
        """Original lobby membership text, target, clout and appeasement effects."""
        return await country_source(self,"diplomacy.lobbies","Country.GetPoliticalLobbies",(
            _text("name","PoliticalLobby.GetName"),_text("type","PoliticalLobby.GetType.GetName"),
            Field("owner_id","PoliticalLobby.GetCountry.GetID",I,full_id=True),
            Field("target_id","PoliticalLobby.GetTargetCountry.GetID",I,full_id=True),
            _text("target_name","PoliticalLobby.GetTargetCountry.GetName"),
            _text("members","PoliticalLobby.GetMembersDesc"),Field("clout","PoliticalLobby.GetTotalClout",F),
            _text("clout_breakdown","PoliticalLobby.GetTotalCloutBreakdown"),
            Field("appeasement","PoliticalLobby.GetAppeasement",I),
            _text("appeasement_changes","PoliticalLobby.GetAppeasementChangesDesc"),
            _text("appeasement_effects","PoliticalLobby.GetAppeasementEffectsDesc"),
            Field("established","PoliticalLobby.GetEstablishmentDate.GetString",S),
            _text("disband_reason","PoliticalLobby.GetDisbandReasonDesc")),country_id=target)


    async def _select_treaty_binding(self, run, treaty_id, *, include_drafts=False):
        """Get one genuine retained Treaty by its native full reference."""
        from ..politics import _id
        target=_id(treaty_id,"Treaty")
        country=await run.country()
        sources=("Country.GetInForceTreaties","Country.GetDraftTreaties") if include_drafts else ("Country.GetInForceTreaties",)
        for source in sources:
            async for _,element,binding in run.elements(source,(country,),"Treaty"):
                if await run.origin(element,"Treaty") == target:
                    return binding
        raise SourceUnavailable("Selected treaty is not present in the original current country source")

    async def treaty_commitment(self, treaty_id):
        """Read selected treaty duration, enforcement and native breach explanations.

        The full ID selects the actual retained Treaty, never an ordinal or
        localized label. Native peer/context text is kept rather than inferred
        from an in-force flag. No command is generated by this read.
        """
        from .._political_actions import owned_operation
        from .._political_data import snapshot
        from ..politics import _id
        treaty_id=_id(treaty_id,"Treaty")
        async def read(run,result):
            treaty=await self._select_treaty_binding(run,treaty_id)
            observed=await snapshot(self,"diplomacy.treaty_commitment",(
                _text("name","Treaty.GetName"),
                Field("first_country_id","Treaty.GetFirstCountry.GetID",I,full_id=True),
                Field("second_country_id","Treaty.GetSecondCountry.GetID",I,full_id=True),
                Field("binding_days","Treaty.GetBindingDays",I),Field("binding_days_left","Treaty.GetBindingDaysLeft",I),
                Field("binding_end_date","Treaty.GetBindingPeriodEndGameDate.GetString",S),
                _text("remaining","Treaty.GetBindingPeriodRemainingFormatted"),
                Field("frozen_for_both","Treaty.IsFrozenForBothCountries",B),
                _text("first_fulfillment","Treaty.GetNonFulfillmentDescFor(Treaty.GetFirstCountry)"),
                _text("second_fulfillment","Treaty.GetNonFulfillmentDescFor(Treaty.GetSecondCountry)"),
                _text("first_withdrawal_penalties","Treaty.GetWithdrawPenaltiesDesc(Treaty.GetFirstCountry)"),
                _text("second_withdrawal_penalties","Treaty.GetWithdrawPenaltiesDesc(Treaty.GetSecondCountry)"),
                _text("first_can_withdraw","Treaty.GetCanWithdrawDesc(Treaty.GetFirstCountry)"),
                _text("second_can_withdraw","Treaty.GetCanWithdrawDesc(Treaty.GetSecondCountry)")),((treaty,),),perform=False)
            result.update(observed,id=treaty_id)
        return await owned_operation(self,"treaty_commitment",read,namespace="diplomacy",result={"id":treaty_id})

    async def withdraw_treaty(self, treaty_id, *, readback=True):
        """Withdraw one actual in-force treaty using its original native producer.

        Native commitment penalties and legality remain in Validate/Submit.
        Readback separately checks current country membership; accepted is not
        silently promoted to termination. No automatic inverse or replay.
        """
        import asyncio
        from .._political_actions import selected_command, owned_operation
        from ..politics import _id
        treaty_id=_id(treaty_id,"Treaty")
        if type(readback) is not bool:
            raise ValueError("readback must be a bool")
        lock=getattr(self,"_extended_mutation_lock",None)
        if lock is None:
            self._extended_mutation_lock=lock=asyncio.Lock()
        async def change():
            async with lock:
                async def select(run):
                    return (await self._select_treaty_binding(run,treaty_id),)
                result=await selected_command(self,"withdraw_treaty","Treaty.WithdrawFromTreaty",select,
                    namespace="diplomacy",perform=False)
                result["id"]=treaty_id
                if not (readback and result.get("accepted") is True and result.get("ownership_settled")):
                    return result
                async def observe(run,after):
                    country=await run.country()
                    after.update(current_country_membership=False,source_complete=False)
                    async for _,element,_ in run.elements("Country.GetInForceTreaties",(country,),"Treaty"):
                        if await run.origin(element,"Treaty")==treaty_id:
                            after["current_country_membership"]=True
                    after["source_complete"]=True
                try:
                    after=await owned_operation(self,"withdraw_treaty.readback",observe,namespace="diplomacy",perform=False)
                except Exception as error:
                    result["readback_error"]=error
                else:
                    result["after"]=after
                    result["completed"]=bool(after.get("source_complete") and after.get("current_country_membership") is False)
                    result["effect_observed"]=result["completed"]
                    if result["completed"]:
                        result["status"]="withdrawn"
                return result
        return await self._ctx.perform("diplomacy.withdraw_treaty",change)
