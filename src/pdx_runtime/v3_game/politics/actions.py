"""Original law-imposition and amendment-repeal actions over owned choices.

The source country is an observation context, not a second actor binding.
Native command producers determine actor/target semantics. Read descriptors
never generate candidate commands and retain native zero/False/errors/pages.
"""
from __future__ import annotations

from ... import v3_data as data, v3_commands as commands
from .._choices import OwnedChoice, OwnedChoices
from .._choice_sources import (
    _choice_evaluate, _choice_elements, _choice_binding, _copy_owned_rows, _finish_choice_report,
)
from .._run import SourceUnavailable
from .. import _run
from .._political_actions import selected_command, owned_operation
from .._political_data import Field, binding, resolve_country_id, country_source, project
from .law_details import (
    _LAW_EFFECTS, _AMENDMENTS, _ACTIVE_LAW_SOURCES, _CANDIDATE_LAW_SOURCES,
)

K = data.Kind
_REFORM_COMMAND = 0x5a23
_REFORM_INTEREST_GROUPS = 0x30a6


def _text(name, expression):
    return Field(name, expression, K.STRING, text=True)


def _id(name, expression, kind=K.I32):
    return Field(name, expression, kind, full_id=True)


_LAWS = (
    *_LAW_EFFECTS,
    _text("on_impose", "Law.GetOnImposeDesc"),
    _text("law_country_name", "Law.GetCountry.GetName"),
    _text("active_group_law_name", "Law.GetActiveGroupLaw.GetNameNoFormatting"),
    Field("visible", "Law.IsVisible", K.BOOL),
    _id("observed_player_country_id", "GetPlayer.GetID"),
)
_AMENDMENT_CHOICES = (
    *_AMENDMENTS,
    _text("provider_law_name", "Law.GetNameNoFormatting"),
    _id("provider_law_country_id", "Law.GetCountry.GetID"),
)
_LAW_AFTER = (
    _text("law_name", "Law.GetNameNoFormatting"),
    _id("law_country_id", "Law.GetCountry.GetID"),
    _text("law_country_name", "Law.GetCountry.GetName"),
    Field("valid", "Law.IsValid", K.BOOL),
    Field("active", "Law.IsActive", K.BOOL),
    Field("being_enacted", "Law.IsBeingEnacted", K.BOOL),
    _text("active_group_law_name", "Law.GetActiveGroupLaw.GetNameNoFormatting"),
    _text("on_impose", "Law.GetOnImposeDesc"),
)
_AMENDMENT_AFTER = (
    _text("name", "Amendment.GetNameNoFormatting"),
    Field("active", "Amendment.IsActive", K.BOOL),
    _text("law_name", "Amendment.GetAttachedLaw.GetNameNoFormatting"),
    _id("law_country_id", "Amendment.GetAttachedLaw.GetCountry.GetID"),
    _id("sponsor_id", "Amendment.GetSponsor.GetID", K.U32),
    Field("has_cooldown", "Amendment.HasCooldown", K.BOOL),
    Field("cooled_down", "Amendment.HasCooledDown", K.BOOL),
    _text("cooldown_end_date", "Amendment.GetCooldownEndDate"),
    Field("has_timeout", "Amendment.HasTimeout", K.BOOL),
    Field("timed_out", "Amendment.HasTimedOut", K.BOOL),
    _text("timeout_date", "Amendment.GetTimeoutDate"),
    _text("on_repeal_effects", "Amendment.GetOnRepealEffectsDesc"),
)


_GOVERNMENT_IGS = (
    _id("id", "InterestGroup.GetID", K.U32),
    _text("name", "InterestGroup.GetNameNoFormatting"),
    _id("country_id", "InterestGroup.GetCountry.GetID"),
    Field("in_government", "InterestGroup.IsInGovernment", K.BOOL),
    Field("clout", "InterestGroup.GetClout", K.FIXED),
    Field("approval", "InterestGroup.GetApprovalValue", K.I32),
)
_GOVERNMENT_PARTIES = (
    _text("name", "Party.GetNameNoFormatting"),
    _id("country_id", "Party.GetCountry.GetID"),
    Field("valid", "Party.IsValid", K.BOOL),
    Field("in_government", "Party.IsInGovernment", K.BOOL),
    Field("can_join_government", "Party.CanJoinGovernment", K.BOOL),
    _text("join_description", "Party.GetCanJoinGovernmentDesc"),
    _text("members_summary", "Party.GetMembersSummary"),
    _text("members_breakdown", "Party.GetMembersBreakdown"),
)
_GOVERNMENT_IDS = (_id("id", "InterestGroup.GetID", K.U32),)


def _choice(choice, named_type, context):
    if not isinstance(choice, OwnedChoice) or choice.native_type != named_type:
        raise TypeError("pass an original owned " + named_type + " choice, not an ID/name/index")
    catalog = choice._catalog
    if not any(item is choice for item in catalog):
        raise ValueError("choice was not admitted by its original source catalog")
    if (catalog.context is not context or catalog.native is not context.native
            or catalog.process is not context.process):
        raise ValueError("choice belongs to another original context/client/process")
    return catalog


def _boolean(value, name):
    if type(value) is not bool:
        raise ValueError(name + " must be True or False")


def _report(source):
    return {"source": source, "status": "working", "complete": False,
            "reasons": [], "rejected_rows": [], "sources": [], "native_pages": [],
            "eligibility_checked": False,
            "identity_semantics": "Only the original retained choice selects an action; names and occurrences are not native IDs."}


async def _admit(run, catalog, fields, candidates, roots, named_type):
    for candidate, row in await _copy_owned_rows(run, catalog, fields, candidates, roots):
        row["eligibility_checked"] = False
        catalog.add(candidate[0], named_type, candidate[1], row)


async def _append_source(run, catalog, expression, roots, named_type, type_id, fields,
                         *, copy_roots=None, provenance=None):
    candidates = []
    copy_roots = roots if copy_roots is None else copy_roots
    async for index, native in _choice_elements(run, catalog, expression, roots):
        retained = _choice_binding(run, catalog, native, named_type, type_id, expression, index)
        if retained is not None:
            source = {"expression": expression, "source_index": index, **(provenance or {})}
            candidates.append((native, retained, source))
        if len(candidates) == 64:
            await _admit(run, catalog, fields, candidates, copy_roots, named_type)
            candidates = []
    if candidates:
        await _admit(run, catalog, fields, candidates, copy_roots, named_type)


def _government_inputs(value, context, label):
    if isinstance(value, OwnedChoice):
        values = (value,)
    else:
        if isinstance(value, (str, bytes, dict)):
            raise TypeError(label + " must contain original owned InterestGroup/Party choices")
        try:
            values = tuple(value)
        except TypeError as error:
            raise TypeError(label + " must contain original owned InterestGroup/Party choices") from error
    for choice in values:
        if not isinstance(choice, OwnedChoice) or choice.native_type not in ("InterestGroup", "Party"):
            raise TypeError(label + " must contain original owned InterestGroup/Party choices, not IDs/names")
        _choice(choice, choice.native_type, context)
    return values


def _unique_ids(values):
    # Full native IDs define the set. Preserve native/input order, not names.
    return list(dict.fromkeys(values))


def _final_government_ids(current, added, removed, replacement=None):
    if replacement is not None:
        return _unique_ids(replacement)
    additions, removals = set(added), set(removed)
    kept = [value for value in current if value not in removals or value in additions]
    return _unique_ids((*kept, *added))


def _required_ids(rows):
    ids, complete = [], True
    for row in rows:
        value = row.get("id")
        if (row.get("reasons", {}).get("id") or type(value) is not int or not 0 <= value < 0xffffffff):
            complete = False
        else:
            ids.append(value)
    return ids, complete


async def _copied_government_ids(run, catalog, candidates, report):
    copied = await _copy_owned_rows(run, catalog, _GOVERNMENT_IDS, candidates, report=report)
    rows = [row for _, row in copied]
    ids, required = _required_ids(rows)
    required = required and len(rows) == len(candidates)
    # These are explicit owned binding rows. Optional descriptor/page diagnostics
    # do not replace complete ID cells. Missing/failed ID cells still stop input.
    required = required and all(page.fields_count == 1 for page in report["native_pages"])
    report.update(rows=rows, ids=ids, required_ids_complete=required,
        complete=required and not report["reasons"] and not report["rejected_rows"])
    report["status"] = "observed" if report["complete"] else "partial" if rows else "unavailable"
    if not required:
        raise SourceUnavailable("The complete native IG input set is unavailable; no partial government was submitted")
    return ids


def _government_report_ids(report):
    """Required membership/count/IDs, independent of optional page diagnostics."""
    ids, required = _required_ids(report["rows"])
    count = report["source_count"]
    required = required and count is not None and len(report["rows"]) == count
    next_start = 0
    for page in report["native_pages"]:
        required = (required and page.count_valid and page.source_count == count
            and page.fields_count == 1 and page.start == next_start)
        next_start += page.row_count
    required = required and next_start == len(report["rows"])
    report.update(ids=ids, required_ids_complete=bool(required))
    return ids, bool(required)


async def _government_source_ids(api, run, expression, roots, observations):
    # Copy borrowed IG IDs inside the original callback and ownership graph.
    report = await project(api, "reform_government.ids", _GOVERNMENT_IDS, (roots,),
        source_expression=expression, perform=False, owner=run)
    observations.append(report)
    ids, required = _government_report_ids(report)
    if not required:
        raise SourceUnavailable("The complete native membership/count/IG IDs are unavailable; no partial government was submitted")
    return ids


async def _government_selected_ids(api, run, choices, leases, observations, label):
    ids = []
    # Batch adjacent IG inputs; a Party is expanded through its actual provider.
    candidates = []
    async def flush():
        if not candidates:
            return []
        report = _report(label + ".InterestGroup")
        observations.append(report)
        catalog = OwnedChoices(run, metadata={"result": report})
        copied = await _copied_government_ids(run, catalog, tuple(candidates), report)
        candidates.clear()
        return copied
    for choice in choices:
        retained = leases[id(choice)].binding
        if choice.native_type == "InterestGroup":
            candidates.append((choice.native_result, retained, {"expression": "selected original InterestGroup"}))
        else:
            ids.extend(await flush())
            ids.extend(await _government_source_ids(api, run, "Party.GetMembers", (retained,), observations))
    ids.extend(await flush())
    return ids


async def _selected(api, label, choice, named_type, expression, fields, target_scope,
                    observation_scope, readback):
    catalog = _choice(choice, named_type, api._ctx)
    _boolean(readback, "readback")
    async def work():
        async with choice.borrow(api._ctx) as lease:
            async def select(run):
                # No duplicate Country actor/target alias and no source ordinal.
                return (lease.attach(run),)
            result = await selected_command(api, label, expression, select, perform=False)
            result.update(choice=dict(choice), target_scope=target_scope, effect_observed=None)
            if not readback or result.get("accepted") is not True:
                return result
            if not result.get("ownership_settled"):
                result["readback_skipped"] = "Original command ownership remains unsettled"
                return result
            async def observe(run, report):
                retained = lease.attach(run)
                candidate = (choice.native_result, retained, {"expression": "selected original " + named_type})
                rows = await _copy_owned_rows(run, catalog, fields, [candidate], report=report)
                report["selected"] = rows[0][1]
                report["observation_scope"] = observation_scope
                report["completion_claimed"] = False
            try:
                result["after"] = await owned_operation(api, label + ".after", observe, perform=False)
            except Exception as error:
                result["readback_error"] = error
            return result
    return await api._ctx.perform("politics." + label, work)


class PoliticalActionsMixin:
    """Explicit native selections; accepted commands are not completed effects."""

    async def government_choices(self, country_id=None, *, source="interest_groups"):
        """Owned IG or Party inputs for an explicitly requested government reform.

        source is interest_groups (all), current (current government IGs), or
        parties (actual active Parties). No Party ID is invented. A Party action
        input must be expanded through its original GetMembers provider, never
        through a display name or a copied source occurrence.
        """
        sources = {
            "interest_groups": ("Country.AccessAllInterestGroups", "InterestGroup", _GOVERNMENT_IGS),
            "current": ("Country.AccessInterestGroupsInGovernment", "InterestGroup", _GOVERNMENT_IGS),
            "parties": ("Country.AccessActiveParties", "Party", _GOVERNMENT_PARTIES),
        }
        if source not in sources:
            raise ValueError("source must be interest_groups, current or parties")
        expression, named_type, fields = sources[source]
        async def read():
            run = _run.Run(self._ctx, "government_choices")
            report = _report(expression)
            catalog = OwnedChoices(run, metadata={"source": source, "result": report})
            try:
                selected = await resolve_country_id(self._ctx, country_id)
                country = await binding(self._ctx, "Country", selected)
                report["source_country_id"] = selected
                type_id = await self._ctx.type_id(named_type)
                await _append_source(run, catalog, expression, (country,), named_type, type_id, fields)
                return _finish_choice_report(catalog)
            except BaseException as error:
                error.choices = catalog
                report.update(status="retained_after_error", complete=False, failure=error)
                raise
            finally:
                report["native_results"] = tuple(run.operations)
        return await self._ctx.perform("politics.government_choices", read)

    async def reform_government(self, country_id=None, *, add=(), remove=(), replace=None,
                                readback=True):
        """Submit one complete government IG set through the original factory.

        add/remove contain actual InterestGroup or Party choices from
        government_choices(). Delta mode reads the entire current government,
        keeps every unchanged IG, removes requested members, then adds requested
        members (add wins on overlap). Party.GetMembers expands a real Party to
        actual IGs; no Party ID is serialized. Native/input order is preserved.

        replace is an explicit whole-government replacement, including an empty
        tuple. It cannot be combined with add/remove. The original reader appends
        IDs, so each call materializes a fresh command exactly once.

        country_id is the explicit native country actor field for this factory
        route, not the original UI Confirm producer's GetPlayer default. Native
        validation/permissions decide legality; no extra player/owner gate is
        imposed here. Baseline and Party-member IDs are copied inside their
        original native source callbacks, including borrowed provider rows.
        Selected IG inputs keep their genuine retained bindings.

        This is not a legitimacy preview or an atomic game-state transaction.
        Delta mode preserves the observed baseline; later independent changes
        are not merged. Optional readback observes actual current membership.
        """
        additions = _government_inputs(add, self._ctx, "add")
        removals = _government_inputs(remove, self._ctx, "remove")
        replacement = None if replace is None else _government_inputs(replace, self._ctx, "replace")
        if replacement is not None and (additions or removals):
            raise ValueError("replace explicitly replaces the whole government; do not combine it with add/remove")
        _boolean(readback, "readback")
        from contextlib import AsyncExitStack
        from .._political_actions import selected_factory

        async def work():
            async with self._mutation_lock:
                selected = await resolve_country_id(self._ctx, country_id)
                selection = {"mode": "replacement" if replacement is not None else "delta",
                    "country_id": selected, "current_ids": None, "added_ids": [], "removed_ids": [],
                    "replacement_ids": None, "final_ids": None, "observations": [],
                    "atomic_snapshot": False,
                    "delta_order": "remove then add; add wins on overlap; preserve native/input order"}
                async with AsyncExitStack() as stack:
                    choices = (*additions, *removals, *(replacement or ()))
                    leases = {}
                    for choice in choices:
                        if id(choice) not in leases:
                            leases[id(choice)] = await stack.enter_async_context(choice.borrow(self._ctx))
                    async def parameters(run):
                        for lease in leases.values():
                            lease.attach(run)
                        if replacement is None:
                            country = await binding(self._ctx, "Country", selected)
                            selection["current_ids"] = await _government_source_ids(self, run,
                                "Country.AccessInterestGroupsInGovernment", (country,), selection["observations"])
                            selection["removed_ids"] = await _government_selected_ids(self, run,
                                removals, leases, selection["observations"], "remove")
                            selection["added_ids"] = await _government_selected_ids(self, run,
                                additions, leases, selection["observations"], "add")
                            final_ids = _final_government_ids(selection["current_ids"],
                                selection["added_ids"], selection["removed_ids"])
                        else:
                            selection["replacement_ids"] = await _government_selected_ids(self, run,
                                replacement, leases, selection["observations"], "replace")
                            final_ids = _final_government_ids((), (), (), selection["replacement_ids"])
                        selection["final_ids"] = final_ids
                        # Exact native field: one flat block of UInt32 full IDs.
                        # Country is the separate root actor_id, not a parameter.
                        return {_REFORM_INTEREST_GROUPS: tuple(commands.UInt32(value) for value in final_ids)}
                    result = await selected_factory(self, "reform_government", _REFORM_COMMAND,
                        parameters, actor_id=selected, perform=False)
                    result.update(country_id=selected, selection=selection, effect_observed=None,
                        target_scope="explicit native country field of registered reform_government_command",
                        preview_performed=False)
                    if not readback or result.get("accepted") is not True:
                        return result
                    if not result.get("ownership_settled") or result.get("cleanup"):
                        result["readback_skipped"] = "Original command ownership or cleanup remains unsettled"
                        return result
                    try:
                        # Independent copied observation after the action graph
                        # settles. No original typed IG/Party is rebound here.
                        after = await country_source(self, "politics.reform_government.after",
                            "Country.AccessInterestGroupsInGovernment", _GOVERNMENT_IDS,
                            country_id=selected, perform=False)
                        result["after"] = after
                        observed_ids, complete_ids = _government_report_ids(after)
                        if complete_ids:
                            result["observed_interest_group_ids"] = observed_ids
                            result["effect_observed"] = set(observed_ids) == set(selection["final_ids"])
                        result["observation_scope"] = "independent current government IG membership for the explicit country; not a legitimacy/event completion receipt"
                    except Exception as error:
                        result["readback_error"] = error
                    return result
        return await self._ctx.perform("politics.reform_government", work)

    async def law_choices(self, country_id=None, *, scope="imposition"):
        """Owned laws under one selected country, without command validation.

        imposition follows the original UI: active country laws, then each
        retained Law.GetLawAlternatives. active/candidates/enacting/all expose
        those original country providers directly. Overlaps remain separate;
        no unsupported Law ID/key is synthesized from a name or occurrence.

        The copied law_country_id/name are from the actual law in that callback,
        not copied from the requested source country. Keep the returned catalog
        open while acting. Exceptions retain the actual catalog on error.choices.
        """
        if scope not in ("imposition", "active", "candidates", "enacting", "all"):
            raise ValueError("scope must be imposition, active, candidates, enacting or all")
        async def read():
            run = _run.Run(self._ctx, "law_choices")
            report = _report(scope)
            catalog = OwnedChoices(run, metadata={"scope": scope, "result": report})
            try:
                selected = await resolve_country_id(self._ctx, country_id)
                country = await binding(self._ctx, "Country", selected)
                report["source_country_id"] = selected
                law_type = await self._ctx.type_id("Law")
                if scope in ("enacting", "all"):
                    source = "Country.GetLawBeingEnacted"
                    native = await _choice_evaluate(run, catalog, source, (country,))
                    retained = _choice_binding(run, catalog, native, "Law", law_type, source)
                    if retained is not None:
                        await _admit(run, catalog, _LAWS,
                            [(native, retained, {"expression": source})], (country,), "Law")
                sources = ((_ACTIVE_LAW_SOURCES if scope in ("imposition", "active", "all") else ())
                    + (_CANDIDATE_LAW_SOURCES if scope in ("candidates", "all") else ()))
                for source in sources:
                    if scope != "imposition":
                        await _append_source(run, catalog, source, (country,), "Law", law_type, _LAWS)
                        continue
                    async for index, parent in _choice_elements(run, catalog, source, (country,)):
                        retained = _choice_binding(run, catalog, parent, "Law", law_type, source, index)
                        if retained is None:
                            continue
                        # Parent/child are both Law. Only the leaf is bound for
                        # descriptor copying; the parent remains owned by run.
                        await _append_source(run, catalog, "Law.GetLawAlternatives", (retained,),
                            "Law", law_type, _LAWS, copy_roots=(country,),
                            provenance={"parent_source": source, "parent_occurrence": index})
                return _finish_choice_report(catalog)
            except BaseException as error:
                error.choices = catalog
                report.update(status="retained_after_error", complete=False, failure=error)
                raise
            finally:
                report["native_results"] = tuple(run.operations)
        return await self._ctx.perform("politics.law_choices", read)

    async def amendment_choices(self, law_choice, *, active_only=False):
        """Owned active/all amendments from one original selected Law provider.

        Parent labels/IDs are copied with each amendment in the same callback.
        Inactive/proposed entries stay visible with active_only=False. Support,
        sponsor and timers are native fields, never inferred repeal eligibility.
        Close this catalog before closing the original law catalog.
        """
        _choice(law_choice, "Law", self._ctx)
        _boolean(active_only, "active_only")
        source = "Law.GetActiveAmendments" if active_only else "Law.GetAllAmendments"
        async def read():
            async with law_choice.borrow(self._ctx) as lease:
                run = _run.Run(self._ctx, "amendment_choices")
                law = lease.attach(run)
                report = _report(source)
                catalog = OwnedChoices(run, metadata={"law_choice": law_choice, "active_only": active_only,
                    "result": report})
                try:
                    amendment_type = await self._ctx.type_id("Amendment")
                    await _append_source(run, catalog, source, (law,), "Amendment", amendment_type,
                        _AMENDMENT_CHOICES)
                    return _finish_choice_report(catalog)
                except BaseException as error:
                    error.choices = catalog
                    report.update(status="retained_after_error", complete=False, failure=error)
                    raise
                finally:
                    report["native_results"] = tuple(run.operations)
        return await self._ctx.perform("politics.amendment_choices", read)

    async def repeal_amendment(self, choice, *, readback=True):
        """Validate/submit original Amendment.Repeal for one retained amendment.

        The native producer controls actor/target and cooldown/support legality.
        Optional readback reports that original amendment and attached law;
        copied IsActive=False alone is not claimed as confirmed repeal completion.
        """
        return await _selected(self, "repeal_amendment", choice, "Amendment", "Amendment.Repeal",
            _AMENDMENT_AFTER, "selected original Amendment; actor/target are determined by the native producer",
            "selected original amendment and attached law; not a repeal completion receipt", readback)

    async def impose_law(self, choice, *, readback=True):
        """Validate/submit original Law.StartImposing for one retained law.

        Original GUI text names Law.GetCountry as the imposed country and
        Law.GetActiveGroupLaw as the law being replaced. Source-country metadata
        is not an actor or separate target binding. The original producer uses
        its native player actor; Python does not construct a competing Country.
        Acceptance/readback of law fields does not prove the legal-imposition
        journal entry started, nor that the law passed or enactment completed.
        """
        return await _selected(self, "impose_law", choice, "Law", "Law.StartImposing",
            _LAW_AFTER, "selected Law.GetCountry per original GUI contract; actor comes from the native producer",
            "selected original law and its native country; not a legal-imposition journal-entry receipt", readback)
