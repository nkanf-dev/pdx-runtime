"""Original diplomatic-play type options and selected native start producers.

Country is the selected target binding. The actor remains the original native
GetPlayer expression; no second Country alias is rebound to impersonate it.
The GUI opens a confirmation model after testing GetStartCommand*. These APIs
never fabricate that model or claim to choose demand versus forced-play mode.
"""
from __future__ import annotations

from contextlib import AsyncExitStack

from ... import v3_data as data
from .._choices import OwnedChoice, OwnedChoices
from .._choice_sources import (
    _choice_evaluate, _choice_elements, _choice_binding, _copy_owned_rows,
    _finish_choice_report,
)
from .._political_data import Field, binding, resolve_country_id
from .._political_actions import selected_command
from .._run import rejected
from .. import _run


K = data.Kind
_MODE_LIMIT = "The original GetStartCommand producer's demand-versus-forced-play mode is not proven; the confirmation model was not used."
_OPTIONS_LIMITS = (
    "Native potential/nonpotential source membership is not command validation.",
    "Structured infamy and likely-backer predictions are not mapped here; their original confirmation-model context is not established.",
    _MODE_LIMIT,
)


def _text(name, expression):
    return Field(name, expression, K.STRING, text=True)


def _id(name, expression):
    return Field(name, expression, K.I32, full_id=True)


def _key(value):
    # Preserve the caller's exact key. There is no localized-name lookup or
    # ordinal conversion. Reject only values that cannot use this verified
    # quoted-literal form without inventing an unproven escaping grammar.
    if (not isinstance(value, str) or not value
            or any(char in "'\\" or ord(char) < 32 for char in value)):
        raise ValueError("native definition_key must be nonempty and contain no quotes, escapes or control characters")
    return value


def _owned(value, named_type, context):
    if not isinstance(value, OwnedChoice) or value.native_type != named_type:
        raise TypeError("select an actual retained " + named_type + " choice")
    catalog = value._catalog
    catalog._ensure_open()
    if catalog.context is not context or catalog.native is not context.native or catalog.process is not context.process:
        raise ValueError("choice belongs to another original context/client/process")
    if not any(record is value for record in catalog):
        raise ValueError("choice was not admitted by its original catalog")
    return value


def _target_kind(country_id, state_id, definition):
    if (country_id is None) == (state_id is None):
        raise ValueError("select exactly one target_country_id or target_state_id")
    if state_id is not None:
        if type(state_id) is not int or not 0 <= state_id < 0xffffffff:
            raise ValueError("target_state_id must be a complete uint32 ID; zero is valid")
        if definition is not None:
            raise ValueError("CountryWithDefinition applies only to a Country target, not a State target")
        return "State"
    return "CountryWithDefinition" if definition is not None else "Country"


async def _target(api, country_id, state_id, definition):
    kind = _target_kind(country_id, state_id, definition)
    if kind == "State":
        full_id, domain = state_id, "State"
    else:
        full_id, domain = await resolve_country_id(api._ctx, country_id), "Country"
    return kind, full_id, await binding(api._ctx, domain, full_id)


def _arguments(kind):
    if kind == "State":
        return "(GetPlayer,State.Self)"
    if kind == "CountryWithDefinition":
        return "(GetPlayer.Self,Country.Self,CountryDefinition.Self)"
    return "(GetPlayer.Self,Country.Self)"


def _type_fields(kind):
    return (
        _text("name", "DiplomaticPlayType.GetName"),
        _text("start_description", "DiplomaticPlayType.GetStartDesc" + kind + _arguments(kind)),
        _text("start_tooltip", "DiplomaticPlayType.GetStartTooltip" + kind + _arguments(kind)),
        _text("war_goal_type", "DiplomaticPlayType.GetWarGoalType.GetNameNoFormatting"),
        _text("war_goal_description", "DiplomaticPlayType.GetWarGoalType.GetDesc"),
        Field("epic", "DiplomaticPlayType.IsEpic", K.BOOL),
        Field("enable_switch_sides", "DiplomaticPlayType.HasEnableSwitchSides", K.BOOL),
    )


_DEFINITION = (
    _text("name", "CountryDefinition.GetName"),
    _text("adjective", "CountryDefinition.GetAdjective"),
    _text("primary_culture", "CountryDefinition.GetPrimaryCultureName"),
    _text("tier", "CountryDefinition.GetTier"),
    Field("valid", "CountryDefinition.IsValid", K.BOOL),
)
_DRAFT = (
    _text("name", "WarGoalDraft.GetNameNoFormatting"),
    _text("description", "WarGoalDraft.GetDesc"),
    _text("type_name", "WarGoalDraft.GetType.GetNameNoFormatting"),
    _id("holder_country_id", "WarGoalDraft.GetHolder.GetID"),
    _id("target_country_id", "WarGoalDraft.GetTarget.GetID"),
    _id("creator_country_id", "WarGoalDraft.GetCreator.GetID"),
    _id("target_state_id", "WarGoalDraft.GetTargetState.GetID"),
    Field("valid", "WarGoalDraft.IsValid", K.BOOL),
    Field("valid_data_context", "WarGoalDraft.IsValidDataContext", K.BOOL),
    Field("incomplete_selection", "WarGoalDraft.HasIncompleteSelectionData", K.BOOL),
    Field("infamy", "WarGoalDraft.GetInfamyAmount", K.FIXED),
    _text("infamy_description", "WarGoalDraft.GetInfamyDesc"),
    Field("maneuvers_cost", "WarGoalDraft.GetManeuversCostToAdd", K.I32),
    _text("maneuvers_description", "WarGoalDraft.GetManeuversCostToAddDesc"),
    _text("incident_description", "WarGoalDraft.GetAddWarGoalDiplomaticIncidentDesc"),
    Field("has_country_creation", "WarGoalDraft.HasCountryCreation", K.BOOL),
    Field("has_country_formation", "WarGoalDraft.HasCountryFormation", K.BOOL),
    Field("holds_article", "WarGoalDraft.HoldsArticle", K.BOOL),
)
_OFFER = (
    _text("type_name", "SwayOffer.GetSwayOfferType.GetName"),
    _text("proposal_description", "Sway.GetProposalDesc"),
    Field("requires_input", "SwayOffer.RequiresAnyInput", K.BOOL),
    Field("takes_effect_immediately", "SwayOffer.TakesEffectImmediately", K.BOOL),
    _id("third_party_country_id", "SwayOffer.GetThirdPartyCountry.GetID"),
    _text("third_party_country_name", "SwayOffer.GetThirdPartyCountry.GetNameNoFormatting"),
    _id("state_id", "SwayOffer.GetState.GetID"),
    _text("state_name", "SwayOffer.GetState.GetNameNoFormatting"),
)


def _catalog(run, **metadata):
    return OwnedChoices(run, metadata={**metadata, "result": {
        "reasons": [], "rejected_rows": [], "sources": [], "native_pages": []}})


def _finish(catalog, limitations=()):
    _finish_choice_report(catalog)
    result = catalog.metadata["result"]
    result["read_complete"] = result["complete"]
    result["limitations"] = list(limitations)
    if limitations:
        result["complete"] = False
        if result["status"] == "observed":
            result["status"] = "partial"
    return catalog


class PlayOptionsMixin:
    """Targeted native play candidates; explicit unproven confirmation-mode gap."""

    async def play_options(self, target_country_id=None, *, target_state_id=None,
                           definition_choice=None, include_nonpotential=True):
        """Read actual target potential/nonpotential sources without commands.

        Returned records are genuine owned DiplomaticPlayType values only.
        Raw/inline values remain native rejected-row evidence. Descriptions and
        tooltips are copied; no candidate IsValid(command) or Validate is run.
        """
        _target_kind(target_country_id, target_state_id, definition_choice)
        if type(include_nonpotential) is not bool:
            raise TypeError("include_nonpotential must be bool")
        if definition_choice is not None:
            _owned(definition_choice, "CountryDefinition", self._ctx)
        return await self._play_type_catalog(target_country_id, target_state_id,
            definition_choice, include_nonpotential=include_nonpotential)

    async def play_type_choices(self, definition_key, target_country_id=None, *,
                                target_state_id=None, definition_choice=None):
        """Look up one real native definition key with GetDiplomaticPlayType.

        The key is an input to the original lookup, not a parsed/localized name.
        No Type ID/key getter is invented. Ownership rejection remains visible.
        """
        key = _key(definition_key)
        _target_kind(target_country_id, target_state_id, definition_choice)
        if definition_choice is not None:
            _owned(definition_choice, "CountryDefinition", self._ctx)
        return await self._play_type_catalog(target_country_id, target_state_id, definition_choice, key=key)

    async def _play_type_catalog(self, country_id, state_id, definition, *, include_nonpotential=False, key=None):
        label = "play_type_choices" if key is not None else "play_options"
        async def work():
            kind, target_id, target_binding = await _target(self, country_id, state_id, definition)
            async with AsyncExitStack() as stack:
                if definition is not None:
                    _owned(definition, "CountryDefinition", self._ctx)
                    definition_lease = await stack.enter_async_context(definition.borrow(self._ctx))
                run = _run.Run(self._ctx, label, namespace="diplomacy")
                roots = (target_binding,)
                if definition is not None:
                    roots += (definition_lease.attach(run),)
                catalog = _catalog(run, target_kind=kind, target_id=target_id, target_binding=target_binding,
                    definition_choice=definition, requested_key=key,
                    actor_scope="original native GetPlayer; Country binding is target only",
                    mode_known=False, confirmation_used=False)
                try:
                    named_type = await self._ctx.type_id("DiplomaticPlayType")
                    candidates = []
                    if key is not None:
                        expression = "GetDiplomaticPlayType('" + key + "')"
                        value = await _choice_evaluate(run, catalog, expression, roots)
                        retained = _choice_binding(run, catalog, value, "DiplomaticPlayType", named_type, expression)
                        if retained is not None:
                            candidates.append((value, retained, {"expression": expression, "requested_key": key}))
                    else:
                        owner = "State" if kind == "State" else "Country"
                        getters = ("GetPotentialDiploPlays", "GetNonPotentialDiploPlays") if include_nonpotential else ("GetPotentialDiploPlays",)
                        for getter in getters:
                            expression = owner + "." + getter
                            async for index, value in _choice_elements(run, catalog, expression, roots):
                                retained = _choice_binding(run, catalog, value, "DiplomaticPlayType", named_type, expression, index)
                                if retained is not None:
                                    candidates.append((value, retained, {"expression": expression, "source_index": index}))
                    for candidate, row in await _copy_owned_rows(run, catalog, _type_fields(kind), candidates, roots):
                        row["eligibility_checked"] = False
                        catalog.add(candidate[0], "DiplomaticPlayType", candidate[1], row)
                    return _finish(catalog, _OPTIONS_LIMITS)
                except Exception as error:
                    error.choices = catalog
                    raise
        return await self._ctx.perform("diplomacy." + label, work)

    async def country_definition_choices(self, country_id):
        """Read an existing country's actual GetDefinition promotion.

        This is not a catalog of releasable countries. The confirmation model's
        AccessCountryCreations source is not replaced by guessed definitions.
        """
        async def work():
            selected = await resolve_country_id(self._ctx, country_id)
            country = await binding(self._ctx, "Country", selected)
            run = _run.Run(self._ctx, "country_definition_choices", namespace="diplomacy")
            catalog = _catalog(run, source="Country.GetDefinition", source_country_id=selected)
            try:
                value = await _choice_evaluate(run, catalog, "Country.GetDefinition", (country,))
                retained = _choice_binding(run, catalog, value, "CountryDefinition",
                    await self._ctx.type_id("CountryDefinition"), "Country.GetDefinition")
                if retained is not None:
                    for candidate, row in await _copy_owned_rows(run, catalog, _DEFINITION,
                            [(value, retained, "Country.GetDefinition")], (country,)):
                        catalog.add(candidate[0], "CountryDefinition", candidate[1], row)
                return _finish(catalog)
            except Exception as error:
                error.choices = catalog
                raise
        return await self._ctx.perform("diplomacy.country_definition_choices", work)

    async def start_play_native(self, play_type, *, target_country_id=None, target_state_id=None,
                                definition_choice=None):
        """Submit one original GetStartCommand*; its default mode is unproven.

        play_type is an actual type choice or a real native definition key. A
        choice retains its original target and definition; do not override them.
        A key invokes the original chained lookup/producer without inventing an
        owning wrapper. This API invokes no ShowConfirmation* or confirmation-
        model action. Accepted is native delivery, not proof of a demand,
        forced play, or final gameplay effect.
        """
        if isinstance(play_type, OwnedChoice):
            _owned(play_type, "DiplomaticPlayType", self._ctx)
            if target_country_id is not None or target_state_id is not None or definition_choice is not None:
                raise ValueError("an owned play type choice retains its original target/definition; request fresh options to change them")
            metadata = play_type._catalog.metadata
            if "target_kind" not in metadata or "target_binding" not in metadata:
                raise ValueError("choice has no original play-options target context")
            kind, target_id, target_binding = metadata["target_kind"], metadata["target_id"], metadata["target_binding"]
            definition = metadata.get("definition_choice")
            key = None
        else:
            key = _key(play_type)
            kind = _target_kind(target_country_id, target_state_id, definition_choice)
            target_id = target_binding = None
            definition = definition_choice
        if definition is not None:
            _owned(definition, "CountryDefinition", self._ctx)
        prefix = "DiplomaticPlayType" if key is None else "GetDiplomaticPlayType('" + key + "')"
        expression = prefix + ".GetStartCommand" + kind + _arguments(kind)
        async def work():
            selected_target_id, selected_target_binding = target_id, target_binding
            if selected_target_binding is None:
                _, selected_target_id, selected_target_binding = await _target(self, target_country_id, target_state_id, definition)
            async with AsyncExitStack() as stack:
                type_lease = definition_lease = None
                if key is None:
                    _owned(play_type, "DiplomaticPlayType", self._ctx)
                    type_lease = await stack.enter_async_context(play_type.borrow(self._ctx))
                if definition is not None:
                    _owned(definition, "CountryDefinition", self._ctx)
                    definition_lease = await stack.enter_async_context(definition.borrow(self._ctx))
                async def selected(run):
                    bindings = (selected_target_binding,)
                    if type_lease is not None:
                        bindings += (type_lease.attach(run),)
                    if definition_lease is not None:
                        bindings += (definition_lease.attach(run),)
                    return bindings
                result = await selected_command(self, "start_play_native", expression, selected,
                    namespace="diplomacy", perform=False)
                result.update(target_kind=kind, target_id=selected_target_id, requested_key=key,
                    mode_known=False, start_mode=None, confirmation_used=False,
                    mode_reason=_MODE_LIMIT, contract_complete=False, effect_observed=None,
                    actor_scope="original native GetPlayer; Country binding is target only")
                return result
        return await self._ctx.perform("diplomacy.start_play_native", work)

    async def war_goal_draft_choices(self, parent_choice):
        """Read an actual demanded/offered draft, not AddWarGoalPanel candidates."""
        if not isinstance(parent_choice, OwnedChoice) or parent_choice.native_type not in ("DiplomaticDemand", "SwayOffer"):
            raise TypeError("pass an actual DiplomaticDemand or SwayOffer choice")
        parent_type = parent_choice.native_type
        expression = "DiplomaticDemand.GetDemandedWarGoal" if parent_type == "DiplomaticDemand" else "SwayOffer.GetOfferedWarGoal"
        return await self._play_promoted_choices(parent_choice, parent_type, "WarGoalDraft", expression, _DRAFT,
            "war_goal_draft_choices", "Existing demanded/offered draft only; AddWarGoalPanel candidate creation and parameter selection are not established.")

    async def sway_offer_choices(self, sway_choice):
        """Read an actual Sway.GetSwayOffer; no SwayCountryPanel is fabricated."""
        return await self._play_promoted_choices(sway_choice, "Sway", "SwayOffer", "Sway.GetSwayOffer", _OFFER,
            "sway_offer_choices", "Existing sway offer only; candidate offer construction and parameterized acceptance/cost context are not established.")

    async def _play_promoted_choices(self, parent_choice, parent_type, named_type, expression, fields, label, limitation):
        _owned(parent_choice, parent_type, self._ctx)
        async def work():
            _owned(parent_choice, parent_type, self._ctx)
            async with parent_choice.borrow(self._ctx) as lease:
                run = _run.Run(self._ctx, label, namespace="diplomacy")
                parent = lease.attach(run)
                catalog = _catalog(run, parent_choice=parent_choice, source=expression)
                try:
                    value = await _choice_evaluate(run, catalog, expression, (parent,))
                    retained = _choice_binding(run, catalog, value, named_type, await self._ctx.type_id(named_type), expression)
                    if retained is not None:
                        for candidate, row in await _copy_owned_rows(run, catalog, fields,
                                [(value, retained, expression)], (parent,)):
                            catalog.add(candidate[0], named_type, candidate[1], row)
                    return _finish(catalog, (limitation,))
                except Exception as error:
                    error.choices = catalog
                    raise
        return await self._ctx.perform("diplomacy." + label, work)
