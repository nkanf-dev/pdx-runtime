"""Selected power-bloc membership and principle actions over original choices.

Catalogs hold genuine retained native PowerBloc/PowerBlocPrinciple values. A
copied name or source occurrence never selects an action. Catalog descriptors
are copied in 64-row/32-field pages and do not create candidate commands.
"""
from __future__ import annotations

from ... import v3_data as data
from .._choices import OwnedChoice, OwnedChoices
from .._choice_sources import (
    _choice_evaluate, _choice_elements, _choice_binding, _copy_owned_rows, _finish_choice_report,
)
from .._political_actions import owned_operation, selected_command
from .._political_data import Field, binding, country_snapshot, snapshot
from .._run import rejected
from .. import _run

K = data.Kind


def _text(name, expression):
    return Field(name, expression, K.STRING, text=True)


def _id_field(name, expression):
    return Field(name, expression, K.I32, full_id=True)


_BLOCS = (
    _text("name", "PowerBloc.GetNameNoFormatting"),
    Field("valid", "PowerBloc.IsValid", K.BOOL),
    Field("active", "PowerBloc.IsActive", K.BOOL),
    _id_field("leader_id", "PowerBloc.GetLeader.GetID"),
    _text("leader_name", "PowerBloc.GetLeader.GetName"),
    _text("identity_name", "PowerBloc.GetIdentity.GetNameNoFormatting"),
    _text("identity_description", "PowerBloc.GetIdentity.GetDesc"),
    Field("member_count", "PowerBloc.GetNumMembers", K.I32),
    _text("members_description", "PowerBloc.GetMembersBreakdown"),
    Field("rank", "PowerBloc.GetRankRaw", K.I32),
    _text("rank_description", "PowerBloc.GetRankTooltip"),
    Field("mandates", "PowerBloc.GetNumMandates", K.I32),
    Field("cohesion", "PowerBloc.GetCohesion", K.FIXED),
    _text("cohesion_description", "PowerBloc.GetCohesionDesc"),
    Field("founding_date", "PowerBloc.GetFoundingDate.GetString", K.STRING),
    _id_field("actor_country_id", "GetPlayer.GetID"),
    Field("actor_is_member", "PowerBloc.IsMember(GetPlayer.Self)", K.BOOL),
    Field("actor_is_leader", "PowerBloc.IsLeader(GetPlayer.Self)", K.BOOL),
    Field("actor_has_pending_invitation", "PowerBloc.HasPendingInvitationForCountry(GetPlayer.Self)", K.BOOL),
    Field("actor_has_pending_request", "PowerBloc.HasPendingRequestToJoinFromCountry(GetPlayer.Self)", K.BOOL),
)
_PRINCIPLES = (
    _text("bloc_name", "PowerBloc.GetNameNoFormatting"),
    _id_field("bloc_leader_id", "PowerBloc.GetLeader.GetID"),
    _text("name", "PowerBlocPrinciple.GetNameNoFormatting"),
    _text("group_name", "PowerBlocPrinciple.GetGroup.GetNameNoFormatting"),
    _text("description", "PowerBlocPrinciple.GetDesc"),
    Field("possible", "PowerBlocPrinciple.IsPossible", K.BOOL),
    _text("requirements", "PowerBlocPrinciple.GetIsPossibleDesc"),
    _text("trigger_requirements", "PowerBlocPrinciple.GetIsPossibleTriggerDesc"),
    _text("incompatibilities", "PowerBlocPrinciple.GetIncompatibilitiesDesc"),
    _text("leader_effects", "PowerBlocPrinciple.GetLeaderEffectDesc"),
    _text("member_effects", "PowerBlocPrinciple.GetMemberEffectDesc"),
    _text("non_leader_effects", "PowerBlocPrinciple.GetNonLeaderEffectDesc"),
    _text("bloc_effects", "PowerBlocPrinciple.GetPowerBlocEffectDesc"),
    _text("institution_effects", "PowerBlocPrinciple.GetInstitutionEffectDesc"),
    _text("production_methods", "PowerBlocPrinciple.GetProductionMethodsDesc"),
    _text("buildings", "PowerBlocPrinciple.GetBuildingTypesDesc"),
    _text("companies", "PowerBlocPrinciple.GetCompanyTypesDesc"),
    _text("mobilization_options", "PowerBlocPrinciple.GetMobilizationOptionsDesc"),
    Field("currently_present", "PowerBloc.HasPrinciple(PowerBlocPrinciple.Self)", K.BOOL),
    Field("on_cooldown", "PowerBloc.IsPrincipleOnCooldown(PowerBlocPrinciple.Self)", K.BOOL),
    Field("selection_date", "PowerBloc.GetPrincipleData(PowerBlocPrinciple.Self).GetSelectionDate.GetString", K.STRING),
    Field("cooldown_date", "PowerBloc.GetPrincipleData(PowerBlocPrinciple.Self).GetCooldownDate.GetString", K.STRING),
    Field("bloc_mandates", "PowerBloc.GetNumMandates", K.I32),
)


_PRINCIPLE_AFTER = (
    *(Field(field.name,
            "GetPlayer.GetPowerBloc." + field.expression[len("PowerBloc."):] if field.expression.startswith("PowerBloc.") else field.expression,
            field.kind, field.text, field.full_id) for field in _PRINCIPLES),
    _id_field("observed_player_country_id", "GetPlayer.GetID"),
)


_CUSTOMIZATION_PREVIEW = (
    _text("name", "PowerBlocCustomizationPopup.GetNewCustomName"),
    _text("confirm_description", "PowerBlocCustomizationPopup.ConfirmGetDesc"),
    Field("map_texture_key", "PowerBlocCustomizationPopup.GetPowerBlocMapTextureSelector.GetSelectedItem.GetKey", K.STRING),
    _text("map_texture_name", "PowerBlocCustomizationPopup.GetPowerBlocMapTextureSelector.GetSelectedItem.GetName"),
    Field("map_texture_index", "PowerBlocCustomizationPopup.GetPowerBlocMapTextureSelector.GetSelectedItem.GetIndex", K.I32),
    _text("map_texture_tooltip", "PowerBlocCustomizationPopup.GetPowerBlocMapTextureSelector.GetSelectedItem.GetTooltipTag"),
    _text("statue_tooltip", "PowerBlocCustomizationPopup.GetStatue.GetTooltipTag"),
    _text("statue_hero_name", "PowerBlocCustomizationPopup.GetStatueHeroType.GetName"),
    Field("statue_hero_entity", "PowerBlocCustomizationPopup.GetStatueHeroType.GetEntity", K.STRING),
    _text("statue_hero_tooltip", "PowerBlocCustomizationPopup.GetStatueHeroType.GetTooltipTag"),
    _text("statue_pedestal_name", "PowerBlocCustomizationPopup.GetStatuePedestalType.GetName"),
    Field("statue_pedestal_entity", "PowerBlocCustomizationPopup.GetStatuePedestalType.GetEntity", K.STRING),
    _text("statue_pedestal_tooltip", "PowerBlocCustomizationPopup.GetStatuePedestalType.GetTooltipTag"),
    Field("coa_small_frame", "PowerBlocCustomizationPopup.GetCustomCoaPreview.GetSmallPowerBlocCoaFrame", K.I32),
    Field("coa_medium_frame", "PowerBlocCustomizationPopup.GetCustomCoaPreview.GetMediumPowerBlocCoaFrame", K.I32),
    Field("coa_large_frame", "PowerBlocCustomizationPopup.GetCustomCoaPreview.GetLargePowerBlocCoaFrame", K.I32),
)


def _choice(choice, named_type, context):
    if not isinstance(choice, OwnedChoice) or choice.native_type != named_type:
        raise TypeError("pass an original owned " + named_type + " choice, not an ID/name/index")
    catalog = choice._catalog
    if not any(item is choice for item in catalog):
        raise ValueError("choice was not admitted by its original catalog")
    if (catalog.context is not context or catalog.native is not context.native
            or catalog.process is not context.process):
        raise ValueError("choice belongs to another original context/client/process")
    return catalog


def _full_id(value):
    if type(value) is not int or not 0 <= value < 0xffffffff:
        raise ValueError("country_id must be a complete uint32 ID; zero is valid")
    return value


def _readback(value):
    if type(value) is not bool:
        raise ValueError("readback must be True or False")


def _report(source):
    return {"source": source, "status": "working", "complete": False,
            "reasons": [], "rejected_rows": [], "sources": [], "native_pages": [],
            "eligibility_checked": False,
            "identity_semantics": "Actions use original retained choices, never copied names or source occurrences."}


async def _catalog_rows(api, run, catalog, source, roots, named_type, fields):
    type_id = await api._ctx.type_id(named_type)
    candidates = []
    async for index, native in _choice_elements(run, catalog, source, roots):
        retained = _choice_binding(run, catalog, native, named_type, type_id, source, index)
        if retained is not None:
            candidates.append((native, retained, {"expression": source, "source_index": index}))
        if len(candidates) == 64:
            await _admit(run, catalog, fields, candidates, roots, named_type)
            candidates = []
    if candidates:
        await _admit(run, catalog, fields, candidates, roots, named_type)


async def _admit(run, catalog, fields, candidates, roots, named_type):
    for candidate, row in await _copy_owned_rows(run, catalog, fields, candidates, roots):
        row["eligibility_checked"] = False
        catalog.add(candidate[0], named_type, candidate[1], row)


def _membership_fields(country):
    return (
        _text("bloc_name", "PowerBloc.GetNameNoFormatting"),
        _id_field("bloc_leader_id", "PowerBloc.GetLeader.GetID"),
        _id_field("country_id", country + ".GetID"),
        Field("member", "PowerBloc.IsMember(" + country + ".Self)", K.BOOL),
        Field("pending_invitation", "PowerBloc.HasPendingInvitationForCountry(" + country + ".Self)", K.BOOL),
        Field("pending_request", "PowerBloc.HasPendingRequestToJoinFromCountry(" + country + ".Self)", K.BOOL),
        Field("country_in_a_bloc", country + ".IsInPowerBloc", K.BOOL),
    )


async def _selected_membership(api, label, choice, expression, country_id, readback):
    catalog = _choice(choice, "PowerBloc", api._ctx)
    _readback(readback)
    if country_id is not None:
        _full_id(country_id)
    async def work():
        async with choice.borrow(api._ctx) as lease:
            target = None
            async def select(run):
                nonlocal target
                bloc = lease.attach(run)
                if country_id is not None:
                    # Country is the target, not a second alias for the actor.
                    # The original producer obtains its actor through GetPlayer.
                    target = await binding(api._ctx, "Country", country_id)
                    return bloc, target
                return (bloc,)
            result = await selected_command(api, label, expression, select,
                namespace="diplomacy", perform=False)
            result.update(bloc=dict(choice), target_country_id=country_id,
                membership_observed=None, effect_observed=None)
            if not readback or result.get("accepted") is not True:
                return result
            if not result.get("ownership_settled"):
                result["readback_skipped"] = "Original command ownership remains unsettled"
                return result
            async def observe(run, report):
                bloc = lease.attach(run)
                candidate = (choice.native_result, bloc, {"expression": "selected original PowerBloc"})
                country = "Country" if target is not None else "GetPlayer"
                rows = await _copy_owned_rows(run, catalog, _membership_fields(country),
                    [candidate], (target,) if target is not None else (), report=report)
                report["membership"] = rows[0][1]
            try:
                after = await owned_operation(api, label + ".after", observe,
                    namespace="diplomacy", perform=False)
                result["after"] = after
                row = after.get("membership", {})
                if "member" not in row.get("reasons", {}) and type(row.get("member")) is bool:
                    result["membership_observed"] = row["member"]
                    if label == "kick_from_bloc":
                        result["effect_observed"] = not row["member"]
                # A pending invitation/request is not membership completion.
            except Exception as error:
                result["readback_error"] = error
            return result
    return await api._ctx.perform("diplomacy." + label, work)


class BlocActionsMixin:
    """Closable bloc choices and explicit selected native actions.

    A successful command means acceptance, not final membership or principle
    completion. Optional readback is separate and never replaces acceptance.
    """

    async def bloc_choices(self, source="all"):
        """Actual PowerBloc choices from all, leverage, or current native sources.

        all uses registered GetAllPowerBlocs. leverage uses the actor Country's
        GetPowerBlocsWithLeverage; current uses its actual GetPowerBloc value.
        Keep the returned catalog open while acting, then await catalog.close().
        Raw/borrowed rows stay in report diagnostics and are never retagged.
        Exceptions expose the actual partial catalog as error.choices.
        """
        sources = {"all": "GetAllPowerBlocs", "leverage": "Country.GetPowerBlocsWithLeverage",
                   "current": "Country.GetPowerBloc"}
        if source not in sources:
            raise ValueError("source must be all, leverage or current")
        async def read():
            run = _run.Run(self._ctx, "bloc_choices", namespace="diplomacy")
            report = _report(sources[source])
            catalog = OwnedChoices(run, metadata={"source": source, "result": report})
            try:
                roots = () if source == "all" else (await run.country(),)
                report["country_id"] = run.actor
                if source == "current":
                    native = await _choice_evaluate(run, catalog, sources[source], roots)
                    type_id = await self._ctx.type_id("PowerBloc")
                    retained = _choice_binding(run, catalog, native, "PowerBloc", type_id, sources[source])
                    if retained is not None:
                        await _admit(run, catalog, _BLOCS,
                            [(native, retained, {"expression": sources[source]})], roots, "PowerBloc")
                else:
                    await _catalog_rows(self, run, catalog, sources[source], roots, "PowerBloc", _BLOCS)
                return _finish_choice_report(catalog)
            except BaseException as error:
                error.choices = catalog
                report.update(status="retained_after_error", complete=False, failure=error)
                raise
            finally:
                report["native_results"] = tuple(run.operations)
        return await self._ctx.perform("diplomacy.bloc_choices", read)

    async def request_join_bloc(self, choice, *, readback=True):
        """Request admission to the selected original bloc, not instant membership."""
        return await _selected_membership(self, "request_join_bloc", choice,
            "PowerBloc.RequestToJoin", None, readback)

    async def invite_to_bloc(self, choice, country_id, *, readback=True):
        """Invite one full-ID target; the original producer chooses its actor."""
        _full_id(country_id)
        return await _selected_membership(self, "invite_to_bloc", choice,
            "PowerBloc.Invite(Country.Self)", country_id, readback)

    async def kick_from_bloc(self, choice, country_id, *, readback=True):
        """Remove one full-ID target from the selected bloc by original Kick."""
        _full_id(country_id)
        return await _selected_membership(self, "kick_from_bloc", choice,
            "PowerBloc.Kick(Country.Self)", country_id, readback)

    async def leave_bloc(self, *, readback=True):
        """Leave the actor's actual current bloc through Country.LeavePowerBloc."""
        _readback(readback)
        async def work():
            actor = {}
            async def select(run):
                country = await run.country()
                actor["id"] = run.actor
                return (country,)
            result = await selected_command(self, "leave_bloc", "Country.LeavePowerBloc",
                select, namespace="diplomacy", perform=False)
            result.update(country_id=actor.get("id"), membership_observed=None, effect_observed=None)
            if not readback or result.get("accepted") is not True:
                return result
            if not result.get("ownership_settled"):
                result["readback_skipped"] = "Original command ownership remains unsettled"
                return result
            try:
                after = await country_snapshot(self, "diplomacy.leave_bloc.after", (
                    Field("in_a_bloc", "Country.IsInPowerBloc", K.BOOL),
                    Field("is_bloc_leader", "Country.IsPowerBlocLeader", K.BOOL),
                ), country_id=actor["id"], perform=False)
                result["after"] = after
                if "in_a_bloc" not in after["reasons"]["fields"] and type(after.get("in_a_bloc")) is bool:
                    result["membership_observed"] = after["in_a_bloc"]
                    result["effect_observed"] = not after["in_a_bloc"]
            except Exception as error:
                result["readback_error"] = error
            return result
        return await self._ctx.perform("diplomacy.leave_bloc", work)

    async def customize_bloc(self, choice, *, name=None, readback=True):
        """Change one owned bloc's name through its original isolated context.

        name=None is read-only preview and never confirms. An explicit string,
        including an empty string, uses the zero-formal OnEditName action with
        CUTF8String input. Native first-NUL handling and ASCII space/TAB/CR/LF
        trimming are authoritative; Python does not normalize the supplied text.

        The original context preserves COA, map texture/color and statue. This
        method exposes no arbitrary setters for those fields and never shows a
        popup. ConfirmClicked is an immediate property effect, not a command
        producer. Its callback acceptance and optional observed name are separate.
        """
        _choice(choice, "PowerBloc", self._ctx)
        _readback(readback)
        if name is not None and not isinstance(name, str):
            raise TypeError("name must be an explicit string or None for read-only preview")
        async def work():
            async with choice.borrow(self._ctx) as lease:
                answer = {"action": "customize_bloc", "bloc": dict(choice), "requested_name": name,
                    "expression": "PowerBlocCustomizationPopup.ConfirmClicked",
                    "status": "not_invoked", "accepted": None, "completed": False,
                    "context_result": None, "name_input": None, "validation": None,
                    "invocation": None, "invoked": False, "effect_observed": None,
                    "acceptance_scope": "original ConfirmClicked callback; not final gameplay effect",
                    "name_input_semantics": "native CUTF8String; first NUL then ASCII space/TAB/CR/LF trim",
                    "target_scope": "original selected PowerBloc; existing-bloc context only"}
                async def operation(run, result):
                    bloc = lease.attach(run)
                    made = await run.capture("customize.context", lambda: data.create_power_bloc_context(
                        run.native, bloc.object, output_bytes=4096,
                        adapter=run.adapters["data"], process=run.process))
                    result.update(context_result=made, native_result=made, native_error=made.error)
                    if made.error not in (0, 122, data.ERROR_MORE_DATA):
                        result["status"] = "native_result"
                        return
                    try:
                        popup = run.owned_binding(made, "PowerBlocCustomizationPopup")
                    except RuntimeError as error:
                        result.update(status="native_result", reason=str(error))
                        return
                    if name is None:
                        result["status"] = "preview"
                    else:
                        edited = await run.capture("customize.name", lambda: data.invoke_action(
                            run.native, "PowerBlocCustomizationPopup.OnEditName",
                            data.TypedInput.utf8_string(name), bindings=(popup,), output_bytes=65536,
                            adapter=run.adapters["data"], process=run.process))
                        result.update(name_input=edited, native_result=edited, native_error=edited.error,
                            name_input_invoked=bool(edited.invoked), name_input_accepted=bool(edited.accepted),
                            status="name_input_rejected" if not edited.accepted else "native_result")
                        # A partial postcall input snapshot is not a failed setter.
                        # Never replay it, and never confuse this input Any with
                        # GetNewCustomName or with a returned command.
                        if edited.invoked and edited.accepted and edited.error in (0, 122, data.ERROR_MORE_DATA):
                            validation = await run.capture("customize.condition", lambda: data.evaluate(
                                run.native, "PowerBlocCustomizationPopup.ConfirmIsValid", (popup,),
                                output_bytes=65536, adapter=run.adapters["data"], process=run.process))
                            result.update(validation=validation, native_result=validation, native_error=validation.error)
                            if validation.error:
                                result["status"] = "native_result"
                            elif validation.kind != K.BOOL or type(validation.value) is not bool:
                                result.update(status="native_result", reason="Original ConfirmIsValid did not return a native bool")
                            elif not validation.value:
                                result.update(status="rejected", eligible=False, accepted=False)
                            else:
                                result["eligible"] = True
                                invoked = await run.capture("customize.confirm", lambda: data.invoke_property_action(
                                    run.native, "PowerBlocCustomizationPopup.ConfirmClicked", (popup,),
                                    output_bytes=65536, adapter=run.adapters["data"], process=run.process))
                                result.update(invocation=invoked, native_result=invoked, native_error=invoked.error,
                                    invoked=bool(invoked.invoked), accepted=bool(invoked.accepted),
                                    status="invoked" if invoked.invoked and not invoked.error else "native_result")
                    if readback or name is None:
                        try:
                            result["draft"] = await snapshot(self, "diplomacy.customize_bloc.draft",
                                _CUSTOMIZATION_PREVIEW, ((popup,),), perform=False, owner=run)
                            result["draft"]["descriptor_scope"] = (
                                "current selected texture and statue descriptors plus COA preview frames; "
                                "texture index/frame numbers are observations, not input identities or full appearance aggregates")
                        except Exception as error:
                            result["draft_readback_error"] = error
                    if readback and result["accepted"] is True:
                        try:
                            after = await snapshot(self, "diplomacy.customize_bloc.after", (
                                _text("name", "PowerBloc.GetNameNoFormatting"),
                            ), ((bloc,),), perform=False, owner=run)
                            result["after"] = after
                            draft = result.get("draft", {})
                            if (type(draft.get("name_raw")) is str and type(after.get("name_raw")) is str
                                    and not draft.get("reasons", {}).get("fields", {}).get("name")
                                    and not after["reasons"]["fields"].get("name")):
                                result["effect_observed"] = after["name_raw"] == draft["name_raw"]
                            result["observation_scope"] = "selected bloc's original copied name versus original native draft name; not command completion"
                        except Exception as error:
                            result["readback_error"] = error
                return await owned_operation(self, "customize_bloc", operation,
                    namespace="diplomacy", result=answer, perform=False)
        return await self._ctx.perform("diplomacy.customize_bloc", work)

    async def principle_choices(self, bloc_choice):
        """Original currently held principles of one owned PowerBloc choice.

        Copied descriptors include native effects, present/cooldown flags and
        dates. Possible/requirements are native getters, not command validation.
        These choices can select UpgradeToNextLevel. This is not a new-principle
        selection window or a claim that every replacement is available.
        Close this child catalog before closing the parent bloc catalog.
        """
        _choice(bloc_choice, "PowerBloc", self._ctx)
        async def read():
            async with bloc_choice.borrow(self._ctx) as lease:
                run = _run.Run(self._ctx, "principle_choices", namespace="diplomacy")
                bloc = lease.attach(run)
                report = _report("PowerBloc.GetPrinciples")
                catalog = OwnedChoices(run, metadata={"bloc_choice": bloc_choice, "result": report})
                try:
                    await _catalog_rows(self, run, catalog, "PowerBloc.GetPrinciples", (bloc,),
                        "PowerBlocPrinciple", _PRINCIPLES)
                    return _finish_choice_report(catalog)
                except BaseException as error:
                    error.choices = catalog
                    report.update(status="retained_after_error", complete=False, failure=error)
                    raise
                finally:
                    report["native_results"] = tuple(run.operations)
        return await self._ctx.perform("diplomacy.principle_choices", read)

    async def upgrade_bloc_principle(self, choice, *, readback=True):
        """Submit the original PowerBlocPrinciple.UpgradeToNextLevel command.

        Native creation captures the current player Country's full reference.
        Validation/execution resolve that Country's current PowerBloc then,
        not the source bloc when the choice or command was created. Only the
        principle is bound; its source bloc remains lifetime provenance.

        Readback uses GetPlayer.GetPowerBloc at observation time. If the player
        Country changed meanwhile, it need not be the command actor's bloc.
        completed stays False and effect_observed stays None.
        """
        catalog = _choice(choice, "PowerBlocPrinciple", self._ctx)
        bloc_choice = catalog.metadata.get("bloc_choice")
        _choice(bloc_choice, "PowerBloc", self._ctx)
        _readback(readback)
        async def work():
            async with bloc_choice.borrow(self._ctx) as bloc_lease, choice.borrow(self._ctx) as principle_lease:
                async def select(run):
                    bloc_lease.attach(run)  # Provenance lifetime only, not a native target binding.
                    return (principle_lease.attach(run),)
                result = await selected_command(self, "upgrade_bloc_principle",
                    "PowerBlocPrinciple.UpgradeToNextLevel", select,
                    namespace="diplomacy", perform=False)
                result.update(principle=dict(choice), effect_observed=None,
                    target_scope="native_player_country_current_bloc_at_execution",
                    actor_scope="native current-player Country full reference captured at command creation")
                if not readback or result.get("accepted") is not True:
                    return result
                if not result.get("ownership_settled"):
                    result["readback_skipped"] = "Original command ownership remains unsettled"
                    return result
                async def observe(run, report):
                    bloc_lease.attach(run)  # Keep the original source alive without binding it.
                    principle = principle_lease.attach(run)
                    candidate = (choice.native_result, principle, {"expression": "selected original principle"})
                    rows = await _copy_owned_rows(run, catalog, _PRINCIPLE_AFTER, [candidate], report=report)
                    report["selected_principle"] = rows[0][1]
                    report["observation_scope"] = "GetPlayer.GetPowerBloc at observation time; not command-actor readback if the player Country changed"
                    report["completion_claimed"] = False
                try:
                    result["after"] = await owned_operation(self, "upgrade_bloc_principle.after", observe,
                        namespace="diplomacy", perform=False)
                except Exception as error:
                    result["readback_error"] = error
                return result
        return await self._ctx.perform("diplomacy.upgrade_bloc_principle", work)
