"""Original exile-pool and political-character read sources.

No Character full-ID wrapper is invented. Characters are reached through the
original global pool, country ruler/heir, IG leaders, movement supporters and
company executives. Nested traits/interactions repeat their actual parent ID
in the same native callback; observations are never joined by cached index.
"""
from __future__ import annotations

from ... import v3_data as data
from .._political_data import Field, country_snapshot, country_source
from .._choice_sources import (
    _choice_capture, _choice_evaluate, _choice_elements, _choice_binding,
    _copy_owned_rows, _finish_choice_report,
)
from .._run import rejected
from .. import _run


K = data.Kind


def _text(name, expression):
    return Field(name, expression, K.STRING, text=True)


def _id(name, expression, kind=K.I32):
    return Field(name, expression, kind, full_id=True)


# The leaf signatures below are registered native Character functions. Full
# prefixes are kept when a Character is reached through a native promotion.
_CHARACTER_SCALARS = (
    ("valid", "IsValid", K.BOOL),
    ("age", "GetAge", K.I32),
    ("popularity", "GetPopularity", K.FIXED),
    ("prominence", "GetProminence", K.FIXED),
    ("loyalty", "GetLoyalty", K.FIXED),
    ("agitator", "IsAgitator", K.BOOL),
    ("interest_group_leader", "IsIGLeader", K.BOOL),
    ("ruler", "IsRulerOfOwnCountry", K.BOOL),
    ("heir", "IsHeirOfOwnCountry", K.BOOL),
    ("in_exile_pool", "IsInExilePool", K.BOOL),
    ("executive", "IsExecutive", K.BOOL),
)
_CHARACTER_TEXT = (
    ("name", "GetFullNameNoFormatting"),
    ("roles", "GetAllRoleNames"),
    ("primary_role_title", "GetPrimaryRoleTitle"),
    ("ideology", "GetIdeology.GetNameNoFormatting"),
    ("ideology_description", "GetIdeology.GetDesc"),
    ("traits_description", "GetTraitsDesc"),
    ("popularity_description", "GetPopularityDesc"),
    ("prominence_description", "GetProminenceDesc"),
    ("home_country_name", "GetHomeCountry.GetNameNoFormatting"),
    ("country_name", "GetCountry.GetNameNoFormatting"),
    ("culture", "GetCulture.GetNameNoFormatting"),
    ("religion", "GetReligion.GetNameNoFormatting"),
)


def _character_fields(prefix="Character", *, exile=False):
    fields = (
        _id("id", prefix + ".GetID"),
        _id("home_country_id", prefix + ".GetHomeCountry.GetID"),
        _id("current_country_id", prefix + ".GetCountry.GetID"),
        *(Field(name, prefix + "." + getter, kind) for name, getter, kind in _CHARACTER_SCALARS),
        *(_text(name, prefix + "." + getter) for name, getter in _CHARACTER_TEXT),
    )
    if exile:
        fields += (
            Field("acceptance", prefix + ".GetAcceptanceValueIn(Country.Self)", K.I32),
            _text("acceptance_description", prefix + ".GetAcceptanceValueDescIn(Country.Self)"),
            _text("acceptance_status", prefix + ".GetAcceptanceStatusIn(Country.Self).GetNameNoFormatting"),
            Field("will_join_movement_if_invited", prefix + ".WillJoinMovementIfInvited(Country.Self)", K.BOOL),
            Field("will_start_movement_if_invited", prefix + ".WillStartMovementIfInvited(Country.Self)", K.BOOL),
            Field("will_not_start_or_join_movement_if_invited", prefix + ".WillNotStartOrJoinAnyMovementIfInvited(Country.Self)", K.BOOL),
        )
    return fields


_EXILE_SUMMARY = (
    Field("all_exiles_count", "GetExilePool.GetNumAllExiles", K.I32),
    Field("inviteable_exiles_count", "GetExilePool.GetNumInviteableExiles", K.I32),
    # Original GUI visibility variable. It is not a filter applied to rows by
    # this API and does not validate invite_exile for every candidate.
    Field("ui_only_show_available", "GetVariableSystem.Exists('politics_overview_exile_pool_show_available')", K.BOOL),
    Field("empty_agitator_slots", "Country.GetEmptyAgitatorSlots", K.I32),
    Field("used_agitator_slots", "Country.GetUsedAgitatorSlots", K.I32),
    Field("total_agitator_slots", "Country.GetTotalAgitatorSlots", K.I32),
)
_EXILE_FILTERS = (
    _id("interest_group_id", "InterestGroup.GetID", K.U32),
    _text("interest_group_name", "InterestGroup.GetNameNoFormatting"),
    Field("filtered", "GetExilePool.IsInterestGroupFiltered(InterestGroup.Self)", K.BOOL),
)
_ROSTER_SUMMARY = (
    Field("has_ruler", "Country.HasRuler", K.BOOL),
    Field("has_heir", "Country.HasHeir", K.BOOL),
    Field("empty_agitator_slots", "Country.GetEmptyAgitatorSlots", K.I32),
    Field("used_agitator_slots", "Country.GetUsedAgitatorSlots", K.I32),
    Field("total_agitator_slots", "Country.GetTotalAgitatorSlots", K.I32),
)
_MOVEMENTS = (_text("name", "PoliticalMovement.GetNameNoFormatting"),)


def _parent_fields(prefix):
    return (_id("character_id", prefix + ".GetID"),
            _text("character_name", prefix + ".GetFullNameNoFormatting"))


def _trait_fields(prefix):
    return (
        *_parent_fields(prefix),
        _text("name", "CharacterTrait.GetNameNoFormatting"),
        _text("description", "CharacterTrait.GetDesc"),
        _text("modifiers", "CharacterTrait.GetModifiers"),
        Field("disabled", "CharacterTrait.IsDisabled", K.BOOL),
    )


def _interaction_fields(prefix):
    # Original localization uses Character.Self for these arguments. A chained
    # prefix reaches that same Character through its actual original parent.
    arg = "(" + prefix + ".Self)"
    return (
        *_parent_fields(prefix),
        _text("name", "CharacterInteraction.GetName"),
        _text("description", "CharacterInteraction.GetDesc"),
        _text("info", "CharacterInteraction.GetInfo"),
        _text("requirements", "CharacterInteraction.GetRequirements" + arg),
        _text("effects", "CharacterInteraction.GetEffects" + arg),
        Field("show_requirements", "CharacterInteraction.ShouldShowRequirements", K.BOOL),
        Field("has_cooldown", "CharacterInteraction.HasCooldown" + arg, K.BOOL),
        Field("on_cooldown", "CharacterInteraction.IsOnCooldown" + arg, K.BOOL),
        _text("cooldown", "CharacterInteraction.GetCooldownDesc" + arg),
        _text("cooldown_duration", "CharacterInteraction.GetCooldownDuration" + arg),
        _text("cooldown_remaining", "CharacterInteraction.GetCooldownRemaining" + arg),
    )


def _assemble(country_id, **sections):
    observed = []
    for name, value in sections.items():
        if isinstance(value, list):
            observed.extend((f"{name}[{index}]", item) for index, item in enumerate(value))
        else:
            observed.append((name, value))
    complete = all(item.get("complete") is True for _, item in observed)
    return {"country_id": country_id, "read_complete": complete, "complete": complete,
        "status": "observed" if complete else "partial", "atomic_snapshot": False,
        "reasons": {name: item.get("reasons") for name, item in observed if item.get("complete") is not True},
        "native_pages": [page for _, item in observed for page in item.get("native_pages", ())],
        "limitations": [], **sections}


def _limit(result, key, reason):
    result["complete"] = False
    result["status"] = "partial"
    result["reasons"][key] = reason
    result["limitations"].append(reason)


async def _details(api, label, country_id, prefix, steps=()):
    """Return independent nested observations, each with a current parent ID."""
    result = {}
    for name, source, fields in (
            ("traits", prefix + ".AccessTraits", _trait_fields(prefix)),
            ("interactions", prefix + ".GetInteractions", _interaction_fields(prefix))):
        value = await country_source(api, label + "." + name, source, fields,
            country_id=country_id, steps=steps, perform=False)
        value["relationship_source"] = label
        value["requested_occurrences"] = [step.index for step in steps]
        value["parent_identity_semantics"] = "character_id is read in this native callback; not joined to an earlier source occurrence"
        if name == "interactions":
            value["validity_checked"] = False
            value["context"] = "original native character-interaction context; country_id does not replace its player"
            value["selector_identity"] = "native interaction IDs/keys are not exposed by these registered getters; names and indices are not selectors"
        result[name] = value
    return result


async def _row_details(api, label, parent, prefix, *, steps=()):
    result = {"traits": [], "interactions": []}
    for row in parent["rows"]:
        path = (*steps, data.CreationStep(parent["source_expression"], row["source_index"], parent["source_count"]))
        values = await _details(api, label, parent["country_id"], prefix, path)
        for name, value in values.items():
            result[name].append(value)
    return result


def _choice_input(choice, named_type, context):
    from .._choices import OwnedChoice
    if not isinstance(choice, OwnedChoice) or choice.native_type != named_type:
        raise TypeError("pass an original owned " + named_type + " choice, not an ID/name/index")
    catalog = choice._catalog
    catalog._ensure_open()
    if not any(item is choice for item in catalog):
        raise ValueError("choice is not an admitted record of its original catalog")
    if catalog.context is not context:
        raise ValueError("choice belongs to another original context")
    if catalog.native is not context.native or catalog.process is not context.process:
        raise ValueError("choice belongs to another original native client/process")
    return catalog


def _choice_report(source):
    return {"source": source, "status": "working", "complete": False,
            "reasons": [], "rejected_rows": [], "native_pages": [], "sources": []}


class CharacterMixin:
    """Political character reads without world-pointer scans or guessed owners."""

    async def character_choices(self, source="exiles", *, country_id=None):
        """Return closable choices backed only by actual retained Character values.

        source is exiles, ruler, heir, interest_group_leaders,
        movement_supporters or company_executives. A catalog is one original
        source graph, not an exhaustive roster. See metadata["result"] for
        rejected raw/inline values, partial descriptors and original results.
        Close the catalog after all of its interaction catalogs are closed.
        """
        from .._choices import OwnedChoices
        from .._political_data import resolve_country_id
        allowed = {"exiles", "ruler", "heir", "interest_group_leaders", "movement_supporters", "company_executives"}
        if source not in allowed:
            raise ValueError("source must name one of the original political character sources")
        async def read():
            run = _run.Run(self._ctx, "character_choices")
            report = _choice_report(source)
            catalog = OwnedChoices(run, metadata={"source": source, "result": report})
            try:
                if country_id is not None:
                    run.actor = await resolve_country_id(self._ctx, country_id)
                country = await run.country()
                report["country_id"] = run.actor
                character_type = await self._ctx.type_id("Character")
                candidates = []
                def character(value, expression, index=None):
                    binding = _choice_binding(run, catalog, value, "Character", character_type, expression, index)
                    if binding is not None:
                        candidates.append((value, binding, {"expression": expression, "source_index": index}))
                if source == "exiles":
                    async for index, value in _choice_elements(run, catalog, "GetExilePool.GetExiles", (country,)):
                        character(value, "GetExilePool.GetExiles", index)
                elif source in ("ruler", "heir"):
                    expression = "Country.GetRuler" if source == "ruler" else "Country.GetHeir"
                    character(await _choice_evaluate(run, catalog, expression, (country,)), expression)
                else:
                    expression, parent_type, promotion = {
                        "interest_group_leaders": ("Country.AccessAllInterestGroups", "InterestGroup", "InterestGroup.GetLeader"),
                        "company_executives": ("Country.GetCompanies", "Company", "Company.GetExecutive"),
                        "movement_supporters": ("Country.AccessPoliticalMovements", "PoliticalMovement", "PoliticalMovement.GetSupportingCharacters"),
                    }[source]
                    parent_type_id = await self._ctx.type_id(parent_type)
                    async for index, parent in _choice_elements(run, catalog, expression, (country,)):
                        binding = _choice_binding(run, catalog, parent, parent_type, parent_type_id, expression, index)
                        if binding is None:
                            continue
                        if source == "movement_supporters":
                            async for child_index, value in _choice_elements(run, catalog, promotion, (binding,)):
                                character(value, promotion, child_index)
                        else:
                            character(await _choice_evaluate(run, catalog, promotion, (binding,)), promotion, index)
                for candidate, row in await _copy_owned_rows(run, catalog,
                        _character_fields(exile=source == "exiles"), candidates, (country,)):
                    catalog.add(candidate[0], "Character", candidate[1], row)
                return _finish_choice_report(catalog)
            except Exception as error:
                error.choices = catalog
                report["native_results"] = tuple(run.operations)
                raise
        return await self._ctx.perform("politics.character_choices", read)

    async def interaction_choices(self, character_choice):
        """Return actual owned interaction records under the chosen Character.

        The parent Character catalog remains borrowed until this child catalog
        is closed. Raw/inline native interactions remain rejected diagnostics;
        no owning converter, ID or stable selector is invented for them.
        No candidate calls IsCommandValid or ExecuteCommand during this read.
        """
        from .._choices import OwnedChoices
        _choice_input(character_choice, "Character", self._ctx)
        async def read():
            _choice_input(character_choice, "Character", self._ctx)
            async with character_choice.borrow(self._ctx) as lease:
                run = _run.Run(self._ctx, "interaction_choices")
                character = lease.attach(run)
                report = _choice_report("Character.GetInteractions")
                catalog = OwnedChoices(run, metadata={"character_choice": character_choice, "result": report})
                try:
                    named_type = await self._ctx.type_id("CharacterInteraction")
                    candidates = []
                    async for index, value in _choice_elements(run, catalog, "Character.GetInteractions", (character,)):
                        binding = _choice_binding(run, catalog, value, "CharacterInteraction", named_type,
                            "Character.GetInteractions", index)
                        if binding is not None:
                            candidates.append((value, binding, {"expression": "Character.GetInteractions", "source_index": index}))
                    for candidate, row in await _copy_owned_rows(run, catalog, _interaction_fields("Character"), candidates, (character,)):
                        row["validity_checked"] = False
                        catalog.add(candidate[0], "CharacterInteraction", candidate[1], row)
                    report["validity_checked"] = False
                    return _finish_choice_report(catalog)
                except Exception as error:
                    error.choices = catalog
                    report["native_results"] = tuple(run.operations)
                    raise
        return await self._ctx.perform("politics.interaction_choices", read)

    async def character_interact(self, character_choice, interaction_choice, *, readback=True):
        """Validate and invoke one selected original CharacterInteraction once.

        Both arguments are actual OwnedChoice records. The interaction must
        belong to that exact Character choice. Native callback acceptance is
        not final gameplay completion. Optional after fields are observations,
        not a policy-derived claim that a particular interaction took effect.
        """
        from .._political_actions import selected_property_action, owned_operation
        character_catalog = _choice_input(character_choice, "Character", self._ctx)
        interaction_catalog = _choice_input(interaction_choice, "CharacterInteraction", self._ctx)
        if interaction_catalog.metadata.get("character_choice") is not character_choice:
            raise ValueError("interaction was not produced under this exact Character choice")
        async def work():
            async with self._mutation_lock:
                _choice_input(character_choice, "Character", self._ctx)
                _choice_input(interaction_choice, "CharacterInteraction", self._ctx)
                async with character_choice.borrow(self._ctx) as character_lease, interaction_choice.borrow(self._ctx) as interaction_lease:
                    async def bindings(run):
                        return character_lease.attach(run), interaction_lease.attach(run)
                    result = await selected_property_action(self, "character_interact",
                        "CharacterInteraction.ExecuteCommand(Character.Self)", bindings,
                        condition="CharacterInteraction.IsCommandValid(Character.Self)", perform=False)
                    result["effect_observed"] = None
                    if not readback or not result.get("invoked"):
                        return result
                    async def observe(run, answer):
                        character = character_lease.attach(run)
                        interaction_lease.attach(run)
                        candidate = (character_choice.native_result, character, {"expression": "selected Character choice"})
                        rows = await _copy_owned_rows(run, character_catalog, _character_fields(), [candidate], report=answer)
                        answer["character"] = rows[0][1]
                    try:
                        result["after"] = await owned_operation(self, "character_interact.after", observe, perform=False)
                    except Exception as error:
                        # Preserve the native invocation and retain the attached
                        # read owner/leases if the later copy is unsettled.
                        result["readback_error"] = error
                    return result
        return await self._ctx.perform("politics.character_interact", work)

    async def exiles(self, *, country_id=None, details=True):
        """Read the actual current global exile model and invitation context.

        The native pool can be IG-filtered. Global pool counts retain their
        original player context; country_id selects acceptance/slots perspective,
        not a replacement owner for GetExilePool. The GUI availability toggle is
        reported, but its per-character command-validation visibility is not
        applied here. Names/occurrences never become interaction selectors.
        """
        async def read():
            summary = await country_snapshot(self, "politics.exiles.summary", _EXILE_SUMMARY,
                country_id=country_id, perform=False)
            selected = summary["country_id"]
            exiles = await country_source(self, "politics.exiles.rows", "GetExilePool.GetExiles",
                _character_fields(exile=True), country_id=selected, perform=False)
            filters = await country_source(self, "politics.exiles.filters", "Country.AccessAllInterestGroups",
                _EXILE_FILTERS, country_id=selected, perform=False)
            sections = {"summary": summary, "exiles": exiles, "interest_group_filters": filters}
            if details:
                sections.update(await _row_details(self, "politics.exiles.characters", exiles, "Character"))
            result = _assemble(selected, **sections)
            result["scope"] = "current native global ExilePool.GetExiles; not an unfiltered world-character scan"
            result["pool_counts_scope"] = "original global exile pool/player context, not replaced by country_id"
            result["details_included"] = bool(details)
            result["interaction_validity_checked"] = False
            all_count = summary.get("all_exiles_count")
            source_count = exiles["source_count"]
            comparison = None
            if ("all_exiles_count" not in summary.get("reasons", {}).get("fields", {})
                    and type(all_count) is int and all_count >= 0 and type(source_count) is int):
                comparison = source_count == all_count == len(exiles["rows"])
            result["all_count_matches_read_count"] = comparison
            if comparison is False:
                _limit(result, "pool_scope", "Native source/read count differs from all-exiles count; the pool may be filtered or changed between reads.")
            elif comparison is None:
                _limit(result, "pool_scope", "The all-exiles count could not be compared with the current native source count.")
            if exiles["rows"] and not details:
                _limit(result, "character_details", "Structured traits and interaction descriptors were not requested.")
            if any(item["rows"] for item in sections.get("interactions", ())):
                _limit(result, "interaction_validity", "Interaction requirements/effects/cooldowns were read, but IsCommandValid was not called for every candidate.")
            return result
        return await self._ctx.perform("politics.exiles", read)

    async def political_characters(self, *, country_id=None, details=True):
        """Read rulers/heirs, IG leaders, movement supporters and company executives.

        Sections preserve each original source and its errors. They are not a
        deduplicated or exhaustive national roster: unattached agitators and
        other characters absent from these sources can be missing. Native full
        Character IDs are returned, but never used to invent object bindings.
        """
        async def read():
            summary = await country_snapshot(self, "politics.political_characters.summary", _ROSTER_SUMMARY,
                country_id=country_id, perform=False)
            selected = summary["country_id"]
            sections = {"summary": summary}
            traits, interactions = [], []
            for role, getter in (("ruler", "GetRuler"), ("heir", "GetHeir")):
                field = "has_" + role
                if summary.get(field) is False and field not in summary.get("reasons", {}).get("fields", {}):
                    sections[role] = {"present": False, "country_id": selected, "status": "absent",
                        "complete": True, "reasons": {}, "native_pages": []}
                    continue
                prefix = "Country." + getter
                sections[role] = await country_snapshot(self, "politics.political_characters." + role,
                    _character_fields(prefix), country_id=selected, perform=False)
                if details:
                    values = await _details(self, "politics.political_characters." + role, selected, prefix)
                    traits.append(values["traits"]); interactions.append(values["interactions"])
            for name, source, prefix, parent_fields in (
                    ("interest_group_leaders", "Country.AccessAllInterestGroups", "InterestGroup.GetLeader",
                        (_id("interest_group_id", "InterestGroup.GetID", K.U32), _text("interest_group_name", "InterestGroup.GetNameNoFormatting"))),
                    ("company_executives", "Country.GetCompanies", "Company.GetExecutive",
                        (_text("company_name", "Company.GetNameNoFormatting"),))):
                parents = await country_source(self, "politics.political_characters." + name, source,
                    (*parent_fields, *_character_fields(prefix)), country_id=selected, perform=False)
                sections[name] = parents
                if details:
                    values = await _row_details(self, "politics.political_characters." + name, parents, prefix)
                    traits.extend(values["traits"]); interactions.extend(values["interactions"])
            movements = await country_source(self, "politics.political_characters.movements",
                "Country.AccessPoliticalMovements", _MOVEMENTS, country_id=selected, perform=False)
            sections["movements"] = movements
            supporters = []
            for row in movements["rows"]:
                path = (data.CreationStep("Country.AccessPoliticalMovements", row["source_index"], movements["source_count"]),)
                members = await country_source(self, "politics.political_characters.movement_supporters",
                    "PoliticalMovement.GetSupportingCharacters",
                    (_text("movement_name", "PoliticalMovement.GetNameNoFormatting"), *_character_fields()),
                    country_id=selected, steps=path, perform=False)
                members["requested_movement_occurrence"] = row["source_index"]
                supporters.append(members)
                if details:
                    values = await _row_details(self, "politics.political_characters.movement_supporters", members, "Character", steps=path)
                    traits.extend(values["traits"]); interactions.extend(values["interactions"])
            sections["movement_supporters"] = supporters
            if details:
                sections.update(traits=traits, interactions=interactions)
            result = _assemble(selected, **sections)
            result["details_included"] = bool(details)
            result["roster_complete"] = False
            result["interaction_validity_checked"] = False
            _limit(result, "roster_scope", "No exhaustive national Character source is established; unattached agitators and characters outside the named original sources may be missing.")
            if not details:
                _limit(result, "character_details", "Only native trait descriptions were read; structured traits and interaction descriptors were not requested.")
            if any(item["rows"] for item in interactions):
                _limit(result, "interaction_validity", "Interaction validity was not tested. Only original requirements/effects/cooldown descriptors are included.")
            result["definition_identity"] = "CharacterTrait and CharacterInteraction names/occurrences are display observations, not native IDs or action selectors"
            return result
        return await self._ctx.perform("politics.political_characters", read)
