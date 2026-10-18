"""Prewar diplomatic plays, with original native choices and selected commands."""
from __future__ import annotations

from ... import v3_data as data
from .._choices import OwnedChoice, OwnedChoices
from .._choice_sources import (_choice_elements, _choice_evaluate, _choice_binding,
    _copy_owned_rows, _finish_choice_report)
from .._political_data import Field, resolve_country_id, binding, project
from .._political_actions import selected_command, owned_operation
from .. import _run

F=Field
_PLAY=(
    F("name","DiplomaticPlay.GetName",text=True),
    F("type_name","DiplomaticPlay.GetDiplomaticPlayType.GetName",text=True),
    F("initiator_id","DiplomaticPlay.GetInitiator.GetID",full_id=True),
    F("target_id","DiplomaticPlay.GetTarget.GetID",full_id=True),
    F("phase","DiplomaticPlay.GetPhaseName",text=True),
    F("escalation","DiplomaticPlay.GetEscalation",data.Kind.I32),
    F("escalation_progress","DiplomaticPlay.GetEscalationProgress",data.Kind.FIXED),
    F("escalation_pause","DiplomaticPlay.GetEscalationPause",data.Kind.I32),
    F("has_escalated_into_war","DiplomaticPlay.HasEscalatedIntoWar",data.Kind.BOOL),
    F("start_date","DiplomaticPlay.GetStartDate",text=True),
    F("end_date","DiplomaticPlay.GetEndDate",text=True),
    F("initiator_maneuvers","DiplomaticPlay.GetNumInitiatorManeuversRemaining",data.Kind.I32),
    F("target_maneuvers","DiplomaticPlay.GetNumTargetManeuversRemaining",data.Kind.I32),
    F("actor_maneuvers","DiplomaticPlay.GetNumManeuversRemaining(Country.Self)",data.Kind.I32),
    F("actor_confidence","DiplomaticPlay.GetConfidence(Country.Self)",data.Kind.I32),
    F("actor_confidence_desc","DiplomaticPlay.GetConfidenceDesc(Country.Self)",text=True),
    F("can_add_goal","DiplomaticPlay.CanAddWarGoal(Country.Self)",data.Kind.BOOL),
    F("add_goal_desc","DiplomaticPlay.GetCanAddWarGoalDesc(Country.Self)",text=True),
    F("can_yield","DiplomaticPlay.CanYield(Country.Self)",data.Kind.BOOL),
    F("yield_desc","DiplomaticPlay.GetYieldValidTooltip(Country.Self)",text=True),
    F("is_initiator","DiplomaticPlay.IsInitiator(Country.Self)",data.Kind.BOOL),
    F("is_target","DiplomaticPlay.IsTarget(Country.Self)",data.Kind.BOOL),
    F("is_committed","DiplomaticPlay.IsCommittedParticipant(Country.Self)",data.Kind.BOOL),
    F("is_on_fence","DiplomaticPlay.IsOnTheFence(Country.Self)",data.Kind.BOOL),
    F("is_initiator_leaning","DiplomaticPlay.IsInitiatorLeaning(Country.Self)",data.Kind.BOOL),
    F("is_target_leaning","DiplomaticPlay.IsTargetLeaning(Country.Self)",data.Kind.BOOL),
    F("is_initiator_backer","DiplomaticPlay.IsInitiatorBacker(Country.Self)",data.Kind.BOOL),
    F("is_target_backer","DiplomaticPlay.IsTargetBacker(Country.Self)",data.Kind.BOOL),
    F("is_undecided","DiplomaticPlay.IsUndecidedParticipant(Country.Self)",data.Kind.BOOL),
    F("support_stance_desc","DiplomaticPlay.GetSupportStanceDesc(Country.Self)",text=True),
    F("valid","DiplomaticPlay.IsValid",data.Kind.BOOL),
    F("controlled_country_id","GetPlayer.GetID",full_id=True),
)
_CIVILWAR_CONTROL = (
    F("controlled_country_id", "GetPlayer.GetID", data.Kind.I32, full_id=True),
    F("controlled_country_name", "GetPlayer.GetNameNoFormatting", data.Kind.STRING, text=True),
    F("initiator_id", "DiplomaticPlay.GetInitiator.GetID", data.Kind.I32, full_id=True),
    F("target_id", "DiplomaticPlay.GetTarget.GetID", data.Kind.I32, full_id=True),
    F("switch_side_breakdown", "DiplomaticPlay.GetSwitchSideBreakdown", data.Kind.STRING, text=True),
    F("has_enable_switch_sides", "DiplomaticPlay.GetDiplomaticPlayType.HasEnableSwitchSides", data.Kind.BOOL),
)
_COUNTRY=(
    F("id","Country.GetID",full_id=True),F("name","Country.GetName",text=True),
    F("is_initiator","DiplomaticPlay.IsInitiator(Country.Self)",data.Kind.BOOL),
    F("is_target","DiplomaticPlay.IsTarget(Country.Self)",data.Kind.BOOL),
    F("is_committed","DiplomaticPlay.IsCommittedParticipant(Country.Self)",data.Kind.BOOL),
    F("support_stance_desc","DiplomaticPlay.GetSupportStanceDesc(Country.Self)",text=True),
)
_GOAL=(
    F("name","WarGoal.GetName",text=True),F("description","WarGoal.GetDesc",text=True),
    F("holder_id","WarGoal.GetHolder.GetID",full_id=True),
    F("target_id","WarGoal.GetTarget.GetID",full_id=True),
    F("creator_id","WarGoal.GetCreator.GetID",full_id=True),
    F("target_state_id","WarGoal.GetTargetState.GetID",full_id=True),
    F("type_name","WarGoal.GetType.GetName",text=True),
    F("primary","WarGoal.IsPrimaryDemand",data.Kind.BOOL),
    F("part_of_play","WarGoal.IsPartOfPlay",data.Kind.BOOL),
    F("part_of_war","WarGoal.IsPartOfWar",data.Kind.BOOL),
    F("executed","WarGoal.IsExecuted",data.Kind.BOOL),
    F("contested","WarGoal.IsContested",data.Kind.BOOL),
    F("infamy","WarGoal.GetInfamyAmount",data.Kind.FIXED),
    F("maneuver_cost","WarGoal.GetManeuversCostToAdd",data.Kind.I32),
    F("make_primary_maneuvers","WarGoal.CalcMakePrimaryDemandManeuversCost",data.Kind.I32),
    F("make_primary_infamy","WarGoal.CalcMakePrimaryDemandInfamyAmount",data.Kind.FIXED),
    F("make_primary_desc","WarGoal.GetMakePrimaryDemandManeuversCostDesc",text=True),
    F("make_primary_incident_desc","WarGoal.GetMakePrimaryDemandDiplomaticIncidentDesc",text=True),
    F("valid","WarGoal.IsValid",data.Kind.BOOL),
)
_SWAY=(
    F("valid","Sway.IsValid",data.Kind.BOOL),
    F("proposal","Sway.GetProposalDesc",text=True),
    F("reverse","Sway.IsReverseSway",data.Kind.BOOL),
    F("calling_in_obligation","Sway.IsCallingInObligation",data.Kind.BOOL),
    F("repudiates_obligation_if_rejected","Sway.WouldRepudiateObligationIfRejected",data.Kind.BOOL),
    F("takes_effect_immediately","Sway.GetSwayOffer.TakesEffectImmediately",data.Kind.BOOL),
    F("requires_input","Sway.GetSwayOffer.RequiresAnyInput",data.Kind.BOOL),

)
_STANCES={
    "lean_initiator":("LeanTowardsInitiator","is_initiator_leaning"),
    "lean_target":("LeanTowardsTarget","is_target_leaning"),
    "on_fence":("BeOnTheFence","is_on_fence"),
    "neutral":("DeclareNeutrality",None),
    "support_initiator":("SupportInitiator","is_initiator_backer"),
    "support_target":("SupportTarget","is_target_backer"),
    "switch_side":("SwitchSide",None),
}
_PARTICIPANTS={"initiator_side":"AccessInitiatorSideCountries","target_side":"AccessTargetSideCountries",
    "initiator_leaning":"AccessInitiatorLeaning","target_leaning":"AccessTargetLeaning",
    "on_fence":"AccessOnTheFence","undecided":"AccessUndecided"}


def _choice(value,named_type):
    if not isinstance(value,OwnedChoice) or value.native_type!=named_type:
        raise TypeError("select an actual retained "+named_type+" choice")
    return value


def _merge_row(answer,row):
    reasons=answer.pop("reasons",[])
    answer.update(row)
    answer["descriptor_reasons"]=reasons
    answer["complete"]=row["complete"] and not reasons


def _catalog(run,**metadata):
    return OwnedChoices(run,metadata={**metadata,"result":{"reasons":[],"rejected_rows":[],"sources":[]}})


def _civilwar_choice(choice, context):
    _choice(choice, "DiplomaticPlay")
    catalog = choice._catalog
    catalog._ensure_open()
    if not any(record is choice for record in catalog):
        raise ValueError("choice was not admitted by its original catalog")
    if (catalog.context is not context or catalog.native is not context.native
            or catalog.process is not context.process):
        raise ValueError("choice belongs to another original context/client/process")
    return catalog


def _civilwar_initiator_control(row):
    """Current copied role only, independent of optional/global copy errors."""
    fields = ("controlled_country_id", "initiator_id")
    if any(row.get(field) is None or field in row.get("reasons", {}) for field in fields):
        return None
    return row["controlled_country_id"] == row["initiator_id"]


class DiplomaticPlayMixin:
    async def play_choices(self,*,country_id=None):
        """Observe the original active source; no candidate command is generated."""
        async def work():
            actor=await resolve_country_id(self._ctx,country_id)
            run=_run.Run(self._ctx,"play_choices",namespace="diplomacy");run.actor=actor
            catalog=_catalog(run,source_country_id=actor,source="Country.AccessActiveDiplomaticPlays",
                identity="original retained DiplomaticPlay; no synthetic scalar ID")
            try:
                country=await run.country();type_id=await self._ctx.type_id("DiplomaticPlay");candidates=[]
                async for index,value in _choice_elements(run,catalog,"Country.AccessActiveDiplomaticPlays",(country,)):
                    selected=_choice_binding(run,catalog,value,"DiplomaticPlay",type_id,"Country.AccessActiveDiplomaticPlays",index)
                    if selected is not None:candidates.append((value,selected,"Country.AccessActiveDiplomaticPlays"))
                for (value,selected,source),row in await _copy_owned_rows(run,catalog,_PLAY,candidates,(country,)):
                    catalog.add(value,"DiplomaticPlay",selected,row)
                return _finish_choice_report(catalog)
            except Exception as error:
                error.choices=catalog
                raise
        return await self._ctx.perform("diplomacy.play_choices",work)

    async def play_details(self,choice,*,country_id=None,participants=True,goals=True):
        """Read original play, participant subsets and goals without identity joins."""
        _choice(choice,"DiplomaticPlay")
        if type(participants) is not bool or type(goals) is not bool:raise TypeError("details switches must be bool")
        async def work():
            actor=await resolve_country_id(self._ctx,country_id)
            async with choice.borrow(self._ctx) as lease:
                async def read(run,result):
                    selected=lease.attach(run);run.actor=actor;country=await run.country()
                    copied=await _copy_owned_rows(run,choice._catalog,_PLAY,[(choice.native_result,selected,"selected")],(country,),report=result)
                    _merge_row(result,copied[0][1]);result["source_country_id"]=actor
                    if participants:
                        result["participants"]={}
                        for name,source in _PARTICIPANTS.items():
                            result["participants"][name]=await project(self,"play.participants."+name,_COUNTRY,
                                ((selected,),),source_expression="DiplomaticPlay."+source,perform=False,owner=run)
                    if goals:
                        result["goals"]={}
                        for side in ("Initiator","Target"):
                            result["goals"][side.lower()]=await project(self,"play.goals."+side,_GOAL,
                                ((selected,),),source_expression="DiplomaticPlay.Get"+side+"WarGoals",perform=False,owner=run)
                return await owned_operation(self,"play_details",read,namespace="diplomacy",actor_id=actor,perform=False)
        return await self._ctx.perform("diplomacy.play_details",work)

    async def play_goal_choices(self,play_choice,*,side="both"):
        _choice(play_choice,"DiplomaticPlay")
        if side not in ("both","initiator","target"):raise ValueError("side must be both, initiator or target")
        async def work():
            run=_run.Run(self._ctx,"play_goal_choices",namespace="diplomacy")
            catalog=_catalog(run,play=play_choice,side=side)
            try:
                async with play_choice.borrow(self._ctx) as lease:
                    parent=lease.attach(run);type_id=await self._ctx.type_id("WarGoal");candidates=[]
                    for selected_side in (("initiator","target") if side=="both" else (side,)):
                        source="DiplomaticPlay.Get"+selected_side.title()+"WarGoals"
                        async for index,value in _choice_elements(run,catalog,source,(parent,)):
                            selected=_choice_binding(run,catalog,value,"WarGoal",type_id,source,index)
                            if selected is not None:candidates.append((value,selected,source))
                    for (value,selected,source),row in await _copy_owned_rows(run,catalog,_GOAL,candidates,(parent,)):
                        catalog.add(value,"WarGoal",selected,row)
                    return _finish_choice_report(catalog)
            except Exception as error:
                error.choices=catalog
                raise
        return await self._ctx.perform("diplomacy.play_goal_choices",work)

    async def play_sway_choices(self,play_choice,country_id):
        """Observe player/country incoming and accepted original sways.

        Outgoing-offer argument order is not inferred from a two-argument name.
        It needs an original Proposal model or an independently proven selector.
        """
        _choice(play_choice,"DiplomaticPlay")
        async def work():
            target=await resolve_country_id(self._ctx,country_id)
            run=_run.Run(self._ctx,"play_sway_choices",namespace="diplomacy")
            catalog=_catalog(run,play=play_choice,target_country_id=target,
                perspective="original GetPlayer; Country is the selected other participant",
                limitations=("Outgoing offers require a proven Proposal source or native argument order",
                    "Candidate costs/acceptance need an original explicit actor/target offer context"))
            try:
                async with play_choice.borrow(self._ctx) as lease:
                    parent=lease.attach(run);country=await binding(self._ctx,"Country",target)
                    type_id=await self._ctx.type_id("Sway");candidates=[]
                    for name,source in (("incoming","DiplomaticPlay.GetIncomingSway(Country.Self,GetPlayer.Self)"),
                        ("accepted","DiplomaticPlay.GetAcceptedSway(Country.Self)")):
                        value=await _choice_evaluate(run,catalog,source,(parent,country))
                        selected=_choice_binding(run,catalog,value,"Sway",type_id,source)
                        if selected is not None:candidates.append((value,selected,name))
                    for (value,selected,source),row in await _copy_owned_rows(run,catalog,_SWAY,candidates,(parent,country)):
                        catalog.add(value,"Sway",selected,row)
                    return _finish_choice_report(catalog)
            except Exception as error:
                error.choices=catalog
                raise
        return await self._ctx.perform("diplomacy.play_sway_choices",work)

    async def civil_war_side_options(self, choice):
        """Copy current native player/play fields without generating a command.

        Initiator control is a three-state comparison of the two clean copied
        identity fields only. It is a current-role observation, not command
        eligibility, captured destination, application or causal proof.
        """
        _civilwar_choice(choice, self._ctx)
        async def work():
            _civilwar_choice(choice, self._ctx)
            async with choice.borrow(self._ctx) as lease:
                async def observe(run, answer):
                    retained = lease.attach(run)
                    copied = await _copy_owned_rows(run, choice._catalog, _CIVILWAR_CONTROL,
                        [(choice.native_result, retained, "selected")], report=answer)
                    _merge_row(answer, copied[0][1])
                    answer["initiator_control_observed"] = _civilwar_initiator_control(answer)
                    answer["observation_scope"] = "current copied GetPlayer versus current selected-play initiator; non-atomic and not command eligibility or causality"
                return await owned_operation(self, "civil_war_side_options", observe,
                    namespace="diplomacy", perform=False)
        return await self._ctx.perform("diplomacy.civil_war_side_options", work)

    async def play_civil_war_initiator(self, choice, *, readback=True):
        """Use the original human SwitchSide(GetPlayer) route to play as initiator.

        The original command captures its actor from the formal GetPlayer
        Country wrapper and its destination from the selected play's initiator.
        This is not a bidirectional switch-back or a support/lean stance. No
        context-country retargeting, side selector or policy prevalidator is used.
        Native acceptance is not precise target application: completed stays
        None and effect_observed stays None. The independent three-state after
        observation says only whether the copied current player controls the
        copied CURRENT initiator, not the command's captured destination or cause.
        The decoded original route has no AskConfirmation edge. This API does
        not confirm UI or claim every downstream script is popup-free.
        """
        _civilwar_choice(choice, self._ctx)
        if type(readback) is not bool:
            raise TypeError("readback must be bool")
        async def work():
            _civilwar_choice(choice, self._ctx)
            async with choice.borrow(self._ctx) as lease:
                async def selected(run):
                    # Set domain observation state before the original producer,
                    # including any pending public result. Native fields stay raw.
                    run.public_result.update(completed=None, effect_observed=None,
                        after_initiator_control_observed=None)
                    return (lease.attach(run),)
                result = await selected_command(self, "play_civil_war_initiator",
                    "DiplomaticPlay.SwitchSide(GetPlayer)", selected,
                    namespace="diplomacy", perform=False)
                result["control_observation_scope"] = "independent current GetPlayer/current initiator comparison, not captured-destination application, causality or atomicity"
                if readback and result.get("accepted") and result.get("ownership_settled"):
                    try:
                        async def observe(run, answer):
                            retained = lease.attach(run)
                            copied = await _copy_owned_rows(run, choice._catalog, _CIVILWAR_CONTROL,
                                [(choice.native_result, retained, "selected")], report=answer)
                            _merge_row(answer, copied[0][1])
                        after = await owned_operation(self, "play_civil_war_initiator.after", observe,
                            namespace="diplomacy", perform=False)
                        result["after"] = after
                        result["after_initiator_control_observed"] = _civilwar_initiator_control(after)
                    except Exception as error:
                        result["readback_error"] = error
                return result
        return await self._ctx.perform("diplomacy.play_civil_war_initiator", work)

    async def set_play_stance(self,choice,stance,*,readback=True):
        _choice(choice,"DiplomaticPlay")
        if stance not in _STANCES:raise ValueError("stance must be one of "+", ".join(_STANCES))
        method,observed_field=_STANCES[stance]
        return await self._play_action(choice,"set_play_stance","DiplomaticPlay."+method+"(Country.Self)",
            readback=readback,observed_field=observed_field,fields=_PLAY)

    async def yield_play(self,choice,*,readback=True):
        _choice(choice,"DiplomaticPlay")
        return await self._play_action(choice,"yield_play","DiplomaticPlay.Yield(Country.Self)",readback=readback,fields=_PLAY)

    async def make_play_goal_primary(self,choice,*,readback=True):
        _choice(choice,"WarGoal")
        return await self._play_action(choice,"make_play_goal_primary","WarGoal.MakeWarGoalPrimaryDemand(Country.Self)",
            readback=readback,fields=_GOAL,observed_field="primary")

    async def respond_to_sway(self,choice,*,accept,readback=True):
        _choice(choice,"Sway")
        if type(accept) is not bool:raise TypeError("accept must be bool")
        fields=tuple(f for f in _SWAY if not any(t in f.expression for t in ("Country.Self","DiplomaticPlay.Self")))
        return await self._play_action(choice,"respond_to_sway","Sway."+("AcceptSway" if accept else "RejectSway"),
            readback=readback,fields=fields)

    async def _play_action(self,choice,label,expression,*,readback,fields,observed_field=None):
        if type(readback) is not bool:raise TypeError("readback must be bool")
        async def work():
            needs_actor="Country.Self" in expression or any("Country.Self" in field.expression for field in fields)
            actor=await self._ctx.country_id() if needs_actor else None
            async with choice.borrow(self._ctx) as lease:
                async def selected(run):
                    retained=lease.attach(run)
                    return (await run.country(),retained) if "Country.Self" in expression else (retained,)
                result=await selected_command(self,label,expression,selected,namespace="diplomacy",actor_id=actor,perform=False)
                result["effect_observed"]=None
                if needs_actor:result["actor_country_id"]=actor
                if readback and result.get("accepted") and result.get("ownership_settled"):
                    try:
                        async def observe(run,answer):
                            retained=lease.attach(run)
                            roots=(await run.country(),) if any("Country.Self" in field.expression for field in fields) else ()
                            copied=await _copy_owned_rows(run,choice._catalog,fields,
                                [(choice.native_result,retained,"selected")],roots,report=answer)
                            _merge_row(answer,copied[0][1])
                            if needs_actor:answer["observed_country_id"]=actor
                        after=await owned_operation(self,label+".after",observe,namespace="diplomacy",actor_id=actor,perform=False)
                        result["after"]=after
                        if observed_field and after.get(observed_field) is True and not after.get("reasons",{}).get(observed_field):
                            result.update(completed=True,effect_observed=True)
                    except Exception as error:
                        result["readback_error"]=error
                return result
        return await self._ctx.perform("diplomacy."+label,work)

    async def add_play_goal(self,play_choice,draft_choice,*,article_choice=None,readback=True):
        """Add one actual populated native draft, not a reconstructed goal by name.

        New draft/input construction remains with its original native producer.
        An optional original ArticleDraft uses AddTreatyWarGoal explicitly.
        """
        _choice(play_choice,"DiplomaticPlay");_choice(draft_choice,"WarGoalDraft")
        choices=(play_choice,draft_choice)
        expression="DiplomaticPlay.AddWarGoal(WarGoalDraft.Self)"
        if article_choice is not None:
            _choice(article_choice,"ArticleDraft");choices+=(article_choice,)
            expression="DiplomaticPlay.AddTreatyWarGoal(WarGoalDraft.Self,ArticleDraft.Self)"
        return await self._play_multi_action("add_play_goal",expression,choices,readback=readback,goals=True)

    async def send_sway(self,play_choice,country_id,offer_choice,*,readback=True):
        """Send the selected original populated SwayOffer to an explicit country."""
        _choice(play_choice,"DiplomaticPlay");_choice(offer_choice,"SwayOffer")
        if country_id is None:raise ValueError("choose an explicit sway recipient")
        return await self._play_multi_action("send_sway",
            "DiplomaticPlay.CreateSway(GetPlayer.Self,Country.Self,SwayOffer.Self)",
            (play_choice,offer_choice),country_id=country_id,readback=readback)

    async def _play_multi_action(self,label,expression,choices,*,country_id=None,readback,goals=False):
        from contextlib import AsyncExitStack
        if type(readback) is not bool:raise TypeError("readback must be bool")
        async def work():
            target=await resolve_country_id(self._ctx,country_id) if country_id is not None else None
            async with AsyncExitStack() as stack:
                leases=[await stack.enter_async_context(choice.borrow(self._ctx)) for choice in choices]
                async def selected(run):
                    retained=tuple(lease.attach(run) for lease in leases)
                    return ((await binding(self._ctx,"Country",target),)+retained) if target is not None else retained
                result=await selected_command(self,label,expression,selected,namespace="diplomacy",perform=False)
                result.update(effect_observed=None,completed=False)
                if target is not None:result["target_country_id"]=target
                if readback and result.get("accepted") and result.get("ownership_settled"):
                    try:
                        async def observe(run,answer):
                            parent=leases[0].attach(run);actor=await run.country()
                            copied=await _copy_owned_rows(run,choices[0]._catalog,_PLAY,
                                [(choices[0].native_result,parent,"selected")],(actor,),report=answer)
                            _merge_row(answer,copied[0][1])
                            if goals:
                                answer["goals"]={side.lower():await project(self,"play.after.goals."+side,_GOAL,
                                    ((parent,),),source_expression="DiplomaticPlay.Get"+side+"WarGoals",
                                    perform=False,owner=run) for side in ("Initiator","Target")}
                        result["after"]=await owned_operation(self,label+".after",observe,namespace="diplomacy",perform=False)
                    except Exception as error:result["readback_error"]=error
                return result
        return await self._ctx.perform("diplomacy."+label,work)
