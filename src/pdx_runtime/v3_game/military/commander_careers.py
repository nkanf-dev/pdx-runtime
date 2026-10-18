"""Owned commander career choices and original UI property actions.

Catalogs retain genuine Character and CharacterInteraction values.  Interaction
execution invokes the game's registered void property action; it never invents a
GameCommand or treats callback acceptance as a completed career effect.
"""
from __future__ import annotations

from ... import v3_data as data
from .._choices import OwnedChoice, OwnedChoices
from .._choice_sources import _choice_elements, _choice_binding, _finish_choice_report
from .._text import display_field
from ._warfare import WarfareRead, SourceUnavailable
from .._run import SourceUnavailable


def _report(source):
    return {"source": source, "status": "building", "complete": False,
            "reasons": [], "rejected_rows": [], "native_pages": [], "sources": []}


def _choice_input(choice, named_type, context, source_kind):
    if not isinstance(choice, OwnedChoice) or choice.native_type != named_type:
        raise TypeError("pass an original owned " + named_type + " choice")
    catalog = choice._catalog
    catalog._ensure_open()
    if not any(item is choice for item in catalog):
        raise ValueError("choice is not admitted by its original catalog")
    if catalog.context is not context or catalog.native is not context.native or catalog.process is not context.process:
        raise ValueError("choice belongs to another original context/native client/process")
    if catalog.metadata.get("source_kind") != source_kind:
        raise ValueError("choice belongs to another original source kind")
    return catalog


async def _character_row(run, character, role, source_index):
    row = {"role": role, "source_index": source_index, "unavailable": {}}
    identity, reason = await run.field("Character", "GetID", (character,),
        (data.Kind.I32, data.Kind.U32))
    row["id"] = run.api._native_full_id(identity)
    if row["id"] is None:
        row["unavailable"]["id"] = reason or "Character.GetID did not return a complete native ID"
    fields = await run.functions("Character")
    getter = next((name for name in ("GetFullNameNoFormatting", "GetFullName", "GetFullNameWithTitle")
                   if name in fields), "GetFullName")
    name, reason = await run.field("Character", getter, (character,), (data.Kind.STRING,))
    row.update(display_field(name))
    if reason:
        row["unavailable"]["name"] = reason
    for method, key, kinds in (("GetRankName", "rank", (data.Kind.STRING,)),
            ("GetCommandLimitRaw", "command_limit", (data.Kind.I32,)),
            ("IsRetired", "retired", (data.Kind.BOOL,)),
            ("IsInExilePool", "in_exile_pool", (data.Kind.BOOL,))):
        value, reason = await run.field("Character", method, (character,), kinds)
        row[key] = value
        if reason:
            row["unavailable"][key] = reason
    row["complete"] = not row["unavailable"]
    return row


async def commander_choices(api, *, role=None, character_id=None, perform=True):
    """Return closable owned serving commanders without validating interactions."""
    if role not in (None, "general", "admiral"):
        raise ValueError("role must be general, admiral or None")
    async def read():
        run = WarfareRead(api, "military.commander_career_choices")
        report = _report("Country.AccessGenerals/AccessAdmirals")
        catalog = OwnedChoices(run, metadata={"source_kind": "commander_career",
            "role": role, "result": report})
        try:
            from ._warfare import _source_country, _source_country_id
            country = await _source_country(run)
            report["country_id"] = _source_country_id(run)
            character_type = await run.type_id("Character")
            for method, source_role in (("AccessGenerals", "general"), ("AccessAdmirals", "admiral")):
                if role is not None and role != source_role:
                    continue
                await run.provider("Country", method)
                async for source_index, value in _choice_elements(run, catalog,
                        "Country." + method, (country,)):
                    binding = _choice_binding(run, catalog, value, "Character", character_type,
                        "Country." + method, source_index)
                    if binding is None:
                        continue
                    row = await _character_row(run, binding, source_role, source_index)
                    if character_id is None or row.get("id") == character_id:
                        catalog.add(value, "Character", binding, row)
            return _finish_choice_report(catalog)
        except BaseException as error:
            report["status"], report["complete"] = "retained_after_error", False
            report["native_results"] = tuple(run.operations)
            report["failure"] = error
            error.choices = catalog
            raise
    return await api._ctx.perform("military.commander_career_choices", read) if perform else await read()


async def _interaction_row(run, interaction, character, source_index):
    row = {"source_index": source_index, "unavailable": {}, "validity_checked": False}
    name, reason = await run.field("CharacterInteraction", "GetName", (interaction,),
        (data.Kind.STRING,))
    row.update(display_field(name))
    if reason:
        row["unavailable"]["name"] = reason
    for method, key, arguments in (("GetTooltipTag", "interaction_tooltip_tag", 0),
            ("GetDesc", "description", 0), ("GetInfo", "info", 0),
            ("GetRequirements", "requirements", 1), ("GetEffects", "effects", 1),
            ("GetCommandDesc", "command_description", 1),
            ("GetCooldownRemaining", "cooldown_remaining", 1)):
        bindings = (interaction,) if arguments == 0 else (interaction, character)
        expression = ("CharacterInteraction." + method if arguments == 0 else
                      "CharacterInteraction." + method + "(Character.Self)")
        value, reason = await run.field("CharacterInteraction", method, bindings,
            (data.Kind.STRING,), arguments=arguments, expression=expression)
        row[key] = value
        if reason:
            row["unavailable"][key] = reason
    row["complete"] = not row["unavailable"]
    return row


async def commander_interaction_choices(api, commander_choice, *, perform=True):
    """Return exact interactions under one exact retained serving commander."""
    parent = _choice_input(commander_choice, "Character", api._ctx, "commander_career")
    async def read():
        async with commander_choice.borrow(api._ctx) as lease:
            run = WarfareRead(api, "military.commander_interaction_choices")
            character = lease.attach(run)
            report = _report("Character.GetInteractions")
            catalog = OwnedChoices(run, metadata={"source_kind": "commander_interactions",
                "commander_choice": commander_choice, "parent_catalog": parent, "result": report})
            try:
                await run.provider("Character", "GetInteractions")
                interaction_type = await run.type_id("CharacterInteraction")
                async for source_index, value in _choice_elements(run, catalog,
                        "Character.GetInteractions", (character,)):
                    binding = _choice_binding(run, catalog, value, "CharacterInteraction",
                        interaction_type, "Character.GetInteractions", source_index)
                    if binding is None:
                        continue
                    row = await _interaction_row(run, binding, character, source_index)
                    catalog.add(value, "CharacterInteraction", binding, row)
                report["validity_checked"] = False
                return _finish_choice_report(catalog)
            except BaseException as error:
                report["status"], report["complete"] = "retained_after_error", False
                report["native_results"] = tuple(run.operations)
                report["failure"] = error
                error.choices = catalog
                raise
    return await api._ctx.perform("military.commander_interaction_choices", read) if perform else await read()


async def _commander_state(api, commander_lease, interaction_lease, label):
    run = WarfareRead(api, label)
    character = commander_lease.attach(run)
    interaction_lease.attach(run)
    row = {"status": "observed", "unavailable": {}}
    try:
        identity, reason = await run.field("Character", "GetID", (character,),
            (data.Kind.I32, data.Kind.U32))
        row["character_id"] = run.api._native_full_id(identity)
        if row["character_id"] is None:
            row["unavailable"]["character_id"] = reason or "Character.GetID is incomplete"
        for method, key in (("IsRetired", "retired"), ("IsInExilePool", "in_exile_pool"),
                ("IsCommander", "commander"), ("IsGeneral", "general"),
                ("IsAdmiral", "admiral"), ("HasMilitaryFormation", "has_formation")):
            value, reason = await run.field("Character", method, (character,), (data.Kind.BOOL,))
            row[key] = value
            if reason:
                row["unavailable"][key] = reason
    except Exception:
        run.retain_unknown = True
        raise
    return await run.finish(row)


async def invoke_commander_interaction(api, commander_choice, interaction_choice, *,
        readback=True, perform=True):
    """Validate and invoke one original commander interaction property action."""
    _choice_input(commander_choice, "Character", api._ctx, "commander_career")
    interactions = _choice_input(interaction_choice, "CharacterInteraction", api._ctx,
        "commander_interactions")
    if interactions.metadata.get("commander_choice") is not commander_choice:
        raise ValueError("interaction was not produced under this exact commander choice")
    from .._political_actions import selected_property_action
    async def work():
        async with (commander_choice.borrow(api._ctx) as commander_lease,
                    interaction_choice.borrow(api._ctx) as interaction_lease):
            before = None
            if readback:
                before = await _commander_state(api, commander_lease, interaction_lease,
                    "military.commander_interaction.before")
            async def bindings(run):
                interaction = interaction_lease.attach(run)
                character = commander_lease.attach(run)
                return interaction, character
            result = await selected_property_action(api, "commander_interaction",
                "CharacterInteraction.ExecuteCommand(Character.Self)", bindings,
                condition="CharacterInteraction.IsCommandValid(Character.Self)",
                namespace="military", perform=False)
            result["commander"] = dict(commander_choice)
            result["interaction"] = dict(interaction_choice)
            result["before"] = before
            result["business_effect_observed"] = None
            result["observation_scope"] = (
                "retirement, exile-pool, commander-role and formation-presence bool states; not all possible interaction effects")
            if readback and result.get("invoked"):
                try:
                    after = await _commander_state(api, commander_lease, interaction_lease,
                        "military.commander_interaction.after")
                    result["after"] = after
                    changes = {}
                    compared = []
                    for key in ("retired", "in_exile_pool", "commander", "general",
                                "admiral", "has_formation"):
                        old, new = (before or {}).get(key), after.get(key)
                        if type(old) is bool and type(new) is bool:
                            compared.append(key)
                            if old != new:
                                changes[key] = {"before": old, "after": new}
                    result["observed_state_changes"] = changes
                    if "retired" in compared:
                        result["retirement_state_changed"] = "retired" in changes
                    if compared:
                        result["business_effect_observed"] = bool(changes)
                except Exception as error:
                    result["readback_error"] = error
            return result
    return await api._ctx.perform("military.commander_interaction", work) if perform else await work()
