"""Victoria 3 politics in game terms, using the caller's existing session.

Law keys come from the native LawType database and a checked DataLaw.GetLawType
origin. They are never interpreted as CountryLaw IDs. StartEnacting is ordinary
legislation, not a script effect that passes a law. Native commands called
bolster/suppress act on political movements in this game version, not IGs.
"""
from __future__ import annotations

import asyncio

from ... import v3_access as access, v3_commands as commands, v3_data as data, v3_objects as objects
from .._text import display_field
from .._values import fixed_decimal
from .governance import GovernanceMixin
from .national import NationalManagementMixin
from .law_details import LawDetailsMixin
from .characters import CharacterMixin
from .actions import PoliticalActionsMixin
from .cultures import CulturePoliticsMixin
from .._run import KnownResultError, SourceUnavailable, retained_runs, rejected
from .. import _run


_ACTIVE_LAWS = ("AccessActivePowerStructureLaws", "AccessActiveEconomyLaws",
                "AccessActiveHumanRightsLaws", "AccessActiveOtherLaws")
_LAW_OPTIONS = ("AccessLawsWithGovernmentOrMovementSupport",
                "AccessLawsWithoutGovernmentOrMovementSupport")


def _id(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 0xffffffff:
        raise ValueError(label + " must be a game ID")
    return value


def _target(value, label):
    if isinstance(value, int) and not isinstance(value, bool):
        return _id(value, label)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be a game key, name, or ID")
    return value


def _matches(row, target):
    return target in (row.get("id"), row.get("key"), row.get("name"), row.get("name_raw"))


class PoliticsAPI(GovernanceMixin, NationalManagementMixin, LawDetailsMixin, CharacterMixin, PoliticalActionsMixin, CulturePoliticsMixin):
    """Laws, institutions, interest groups, political movements, and decrees."""
    def __init__(self, ctx):
        self._ctx = ctx
        self._mutation_lock = asyncio.Lock()

    @property
    def _retained(self):
        return retained_runs(self._ctx)

    async def _run(self, label, operation):
        async def execute():
            await self._ctx.refresh()
            run = _run.Run(self._ctx, label)
            # Keep uncertain owners even if the bounded diagnostics rotate.
            try:
                result = await operation(run)
            except KnownResultError as error:
                completion = getattr(run, "public_result", None)
                if not isinstance(completion, dict):
                    completion = {"native_result": error.result,
                                  "partial_result": getattr(error, "partial_result", None)}
                await run.finish(completion)
                error.cleanup = completion.get("cleanup", {"status": run.status})
                error.observed_result = completion
                raise
            # A cleanup warning must not erase an observed result or an accepted
            # native submission. Use the same canonical scope finisher as the
            # selected operations; non-dict read shapes stay unchanged.
            completion = result if isinstance(result, dict) else {"result": result}
            await run.finish(completion)
            if completion is not result and "cleanup" in completion:
                self._ctx.diagnostics.append({"label": "politics." + label,
                    "result": result, "cleanup": completion["cleanup"]})
            return result
        async def work():
            if label in {"enact", "stop_enactment", "set_institution", "bolster", "suppress", "set_decree", "remove_decree"}:
                # Serialize political intents, not reads or other game domains.
                # A pending original operation retains this lock until settled.
                async with self._mutation_lock:
                    return await execute()
            return await execute()
        return await self._ctx.perform("politics." + label, work)

    async def _definitions(self, run):
        found, start = {}, 0
        # Copied catalog evidence only; native ownership stays in the original
        # run. Keep the pointer -> original record contract for other consumers.
        run.law_definition_records, run.law_definition_issues = [], []
        run.law_definition_evidence = {}
        while True:
            query = None
            def read_page():
                nonlocal query
                query = objects.enumerate_law_definitions(self._ctx.native,
                    start_index=start, max_items=256, text_capacity=262144,
                    adapter=self._ctx.adapters["objects"], process=self._ctx.process)
                return query
            try:
                page = await run.call("law_definitions", read_page)
            except KnownResultError as error:
                if getattr(error, "query", None) is None:
                    error.query = query
                if getattr(error, "partial_result", None) is None:
                    error.partial_result = getattr(error.query, "partial_result", None)
                raise
            if page.world != run.world:
                raise RuntimeError("Law definitions belong to another world")
            for row in page.records:
                errors = {name: getattr(row, name) for name in
                    ("error", "key_error", "group_error", "group_key_error")}
                pointer_ok = (type(row.pointer) is int and row.pointer > 0
                    and row.pointer != getattr(page, "fallback", None))
                entry = {"record": row, "query": query, "native_result": page,
                    "partial_result": getattr(query, "partial_result", None),
                    "errors": errors, "decode_errors": {}, "key": None, "group": None,
                    "key_reason": None, "group_reason": None}
                if row.error or not pointer_ok:
                    entry["key_reason"] = "Native LawType definition pointer is unavailable"
                elif row.key_error:
                    entry["key_reason"] = "Native LawType key error " + str(row.key_error)
                else:
                    try:
                        entry["key"] = row.key
                    except UnicodeDecodeError as error:
                        entry["decode_errors"]["key"] = error
                        entry["key_reason"] = "Native LawType key is not complete UTF-8"
                if row.error or not pointer_ok or row.group_error or row.group_key_error:
                    entry["group_reason"] = "Native LawGroup context is unavailable"
                else:
                    try:
                        entry["group"] = row.group_key
                    except UnicodeDecodeError as error:
                        entry["decode_errors"]["group"] = error
                        entry["group_reason"] = "Native LawGroup key is not complete UTF-8"
                run.law_definition_records.append(entry)
                if entry["key_reason"] is not None or entry["group_reason"] is not None:
                    run.law_definition_issues.append(entry)
                if not row.error and pointer_ok:
                    found[row.pointer] = row
                    run.law_definition_evidence[row.pointer] = entry
            if page.next_index >= page.slot_bound:
                return found
            if page.next_index <= start:
                raise RuntimeError("Native law definition page did not advance")
            start = page.next_index

    async def _law(self, run, element, binding, definitions, *, source_index=None, source_expression=None):
        issues, identity_reasons, reads = [], {}, []
        operation_start = len(run.operations)
        def known_error(stage, error, query=None):
            # Only a completed failed read can become row evidence. Unknown
            # transport/cancellation retains and propagates the original graph.
            if (getattr(run, "retain_unknown", False)
                    or getattr(run, "uncertain_query", None) is not None):
                raise error
            original_query = getattr(error, "query", None)
            if original_query is None:
                original_query = query
            partial = getattr(error, "partial_result", None)
            if partial is None:
                partial = getattr(original_query, "partial_result", None)
            issues.append({"stage": stage, "error": error, "native_result": error.result,
                "query": original_query, "partial_result": partial,
                "source_result": element, "binding": binding,
                "source_index": source_index, "source_expression": source_expression})
            return str(error)

        async def read_value(expression):
            query = None
            def evaluate():
                nonlocal query
                query = data.evaluate(run.native, expression, (binding,), output_bytes=65536,
                    adapter=run.adapters["data"], process=run.process)
                return query
            try:
                native = await run.call(expression, evaluate)
            except KnownResultError as error:
                reason = known_error(expression, error, query)
                reads.append(issues[-1])
                return None, reason
            reads.append({"stage": expression, "query": query, "native_result": native,
                "partial_result": getattr(query, "partial_result", None), "error": None})
            return native, None

        law_id, pointer, definition, observed_id = None, None, None, None
        try:
            observed_id = await run.origin(element, "Law")
        except KnownResultError as error:
            identity_reasons["id"] = known_error("Law.origin", error)
        else:
            if type(observed_id) is int and 0 <= observed_id < 0xffffffff:
                law_id = observed_id
            else:
                identity_reasons["id"] = "Native Law origin is not a complete full ID"
        typ, reason = await read_value("Law.GetLawType")
        if typ is None:
            identity_reasons["origin"] = reason
        else:
            try:
                pointer = await run.origin(typ, "LawType", 8)
            except KnownResultError as error:
                identity_reasons["origin"] = known_error("LawType.origin", error)
            else:
                definition = definitions.get(pointer)
        evidence = getattr(run, "law_definition_evidence", {}).get(pointer)
        if definition is None or evidence is None:
            identity_reasons["key"] = "Native law definition origin has no matching key"
        elif evidence["key_reason"] is not None:
            identity_reasons["key"] = evidence["key_reason"]
        row = dict(id=law_id, key=evidence["key"] if evidence is not None else None,
            group=evidence["group"] if evidence is not None else None, law_type_origin=pointer, id_observed=observed_id,
            source_index=source_index, source_expression=source_expression,
            definition=evidence, definition_issues=getattr(run, "law_definition_issues", []),
            source_result=element, source_binding=binding, native_issues=issues, native_reads=reads,
            identity_complete=(law_id is not None and definition is not None
                and evidence is not None and evidence["key"] is not None
                and evidence["key_reason"] is None), identity_reasons=identity_reasons)
        if evidence is not None and evidence["group_reason"] is not None:
            row["group_reason"] = evidence["group_reason"]
        try:
            functions = await run.functions("Law")
        except KnownResultError as error:
            functions = None
            metadata_reason = known_error("Law.functions", error)
        name = None
        if functions is not None:
            try:
                name = await run.scalar("Law", "GetName", binding, optional=True)
            except KnownResultError as error:
                row["name_reason"] = known_error("Law.GetName", error)
        row.update(display_field(name))
        if row["name"] is None and "name_reason" not in row:
            row["name_reason"] = "Native law API does not provide a display name"
        # Panel membership is not in-force evidence. Read each original native
        # predicate; missing/error values remain unknown rather than false.
        reasons = {}
        for field, getter in (("active", "IsActive"), ("enacting", "IsBeingEnacted")):
            metadata = functions.get(getter) if functions is not None else None
            if (metadata is None or metadata.argument_count != 0
                    or any(metadata.callbacks[2:]) or not any(metadata.callbacks[:2])):
                row[field] = None
                reasons[field] = (metadata_reason if functions is None
                    else "Native Law." + getter + " is unavailable")
                continue
            observed, reason = await read_value("Law." + getter)
            if observed is None:
                row[field] = None
                reasons[field] = reason
            elif observed.kind != data.Kind.BOOL or type(observed.value) is not bool:
                row[field] = None
                reasons[field] = "Native Law." + getter + " did not return a boolean"
            else:
                row[field] = observed.value
        if row["active"] is True and row["enacting"] is True:
            row["status"] = "active_and_enacting"
        elif row["active"] is True:
            row["status"] = "active"
        elif row["enacting"] is True:
            row["status"] = "enacting"
        elif row["active"] is False and row["enacting"] is False:
            row["status"] = "inactive"
        else:
            row["status"] = "unknown"
        if reasons:
            row["state_reasons"] = reasons
        row["native_results"] = tuple(run.operations[operation_start:])
        return row

    async def laws(self):
        """Current law-panel entries, with actual in-force and enacting flags.

        The native panel can include both the current law and its proposed
        replacement. ``active`` comes only from Law.IsActive, not list membership
        or enactment progress. Use law_options() for candidates and eligibility.
        """
        async def read(run):
            definitions = await self._definitions(run)
            country, rows, seen = await run.country(), [], set()
            for getter in _ACTIVE_LAWS:
                async for source_index, element, binding in run.elements("Country." + getter, (country,), "Law"):
                    row = await self._law(run, element, binding, definitions,
                        source_index=source_index, source_expression="Country." + getter)
                    if not row["identity_complete"] or row["id"] not in seen:
                        rows.append(row)
                        if row["identity_complete"]:
                            seen.add(row["id"])
            return rows
        return await self._run("laws", read)

    async def _law_option_details(self, run, row, binding):
        national={"scope":"national_interest_group_clout","units":"fraction","reasons":{}}
        checkpoint={"scope":"first_checkpoint_candidate","units":"fraction","reasons":{}}
        row.update(national_clout=national,checkpoint=checkpoint)
        for field,getter,named_type,kind in (
                ("approve_fraction","GetPercentPolStrApprove","CFixedPoint",data.Kind.FIXED),
                ("disapprove_fraction","GetPercentPolStrDisapprove","CFixedPoint",data.Kind.FIXED),
                ("undecided_fraction","GetPercentPolStrUndecided","CFixedPoint",data.Kind.FIXED),
                ("approve_igs_description","GetIGsApproveList","CString",data.Kind.STRING),
                ("disapprove_igs_description","GetIGsDisapproveList","CString",data.Kind.STRING),
                ("undecided_igs_description","GetIGsUndecidedList","CString",data.Kind.STRING)):
            national[field],reason=await self._political_detail_value(run,binding,"Law",getter,named_type,kind)
            if reason:national["reasons"][field]=reason
        scope_reason=None
        if row.get("enacting") is True:
            scope_reason="Law is already being enacted; current checkpoint data is not a first-checkpoint candidate"
        elif row.get("active") is True:
            scope_reason="Law is already active; first-checkpoint candidate data does not apply"
        elif row.get("active") is not False or row.get("enacting") is not False:
            scope_reason="Native law active/enacting state is unavailable; first-checkpoint candidate scope is unproven"
        for outcome in ("success","advance","debate","stall"):
            for suffix,ending,named_type,kind in (
                    ("fraction","Chance","CFixedPoint",data.Kind.FIXED),
                    ("breakdown","Breakdown","CString",data.Kind.STRING)):
                field=outcome+"_"+suffix
                if scope_reason:
                    checkpoint[field]=None;checkpoint["reasons"][field]=scope_reason
                    continue
                getter="GetCheckpoint"+outcome.capitalize()+ending
                checkpoint[field],reason=await self._political_detail_value(run,binding,"Law",getter,named_type,kind)
                if reason:checkpoint["reasons"][field]=reason
        if scope_reason is None:
            for getter in ("IsActive","IsBeingEnacted"):
                current,reason=await self._political_detail_value(run,binding,"Law",getter,"bool",data.Kind.BOOL)
                if current is not False:
                    scope_reason=reason or "Native law state changed during the first-checkpoint candidate read"
                    for field in tuple(checkpoint):
                        if field.endswith(("_fraction","_breakdown")):
                            # Keep values already observed. A changed scope is
                            # a reason, not permission to erase copied outputs.
                            previous=checkpoint["reasons"].get(field)
                            checkpoint["reasons"][field]=(previous+"; "+scope_reason) if previous else scope_reason
                    break
        for field in ("approve_igs_description", "disapprove_igs_description", "undecided_igs_description"):
            national.update(display_field(national[field], field))
        for outcome in ("success", "advance", "debate", "stall"):
            field = outcome + "_breakdown"
            checkpoint.update(display_field(checkpoint[field], field))
        return row

    async def law_options(self, *, details=False):
        """Read original law candidates without generating candidate commands.

        National clout shares are not checkpoint probabilities. Checkpoint
        values are the original first-checkpoint candidate outcomes, never a
        final law-passage forecast. CannotEnactDesc is copied native context,
        not a boolean eligibility result. Use validate_enactment for one
        selected law, or enact for an explicit submission.
        """
        if type(details) is not bool:raise ValueError("details must be True or False")
        async def read(run):
            definitions = await self._definitions(run)
            country, rows, seen = await run.country(), [], set()
            for getter in _LAW_OPTIONS:
                async for source_index, element, binding in run.elements("Country." + getter, (country,), "Law"):
                    row = await self._law(run, element, binding, definitions,
                        source_index=source_index, source_expression="Country." + getter)
                    if row["identity_complete"]:
                        if row["id"] in seen:
                            continue
                        seen.add(row["id"])
                    from .._political_data import Field, snapshot
                    context = await snapshot(self, "politics.law_options.context",
                        (Field("reason", "Law.CannotEnactDesc", data.Kind.STRING, text=True),),
                        ((binding,),), perform=False, owner=run)
                    copied = dict(row, enactable=None, eligibility_checked=False,
                                  reason=context.get("reason"), reason_raw=context.get("reason_raw"),
                                  reason_truncated=None, native_context=context)
                    rows.append(copied)
                    if details:await self._law_option_details(run,rows[-1],binding)
            return rows
        return await self._run("law_options", read)

    async def validate_enactment(self, law_key):
        """Validate one selected original law command without submitting it."""
        from .._political_actions import owned_operation
        target = _target(law_key, "Law")
        async def selected(run, result):
            definitions = await self._definitions(run)
            result.update(definition_records=run.law_definition_records,
                definition_issues=run.law_definition_issues, selection_issues=[])
            country = await run.country()
            incomplete_matches, unresolved = [], []
            for getter in _LAW_OPTIONS:
                async for source_index, element, binding in run.elements("Country." + getter, (country,), "Law"):
                    row = await self._law(run, element, binding, definitions,
                        source_index=source_index, source_expression="Country." + getter)
                    if not row["identity_complete"]:
                        result["selection_issues"].append(row)
                    if target not in (row.get("id"), row.get("key")):
                        if row.get("id" if type(target) is int else "key") is None:
                            unresolved.append(row)
                        continue
                    if not row["identity_complete"]:
                        incomplete_matches.append(row)
                        continue
                    result.update(law=row, selection_status="matched")
                    owner = await run.generated_owner("Law.StartEnacting", (binding,))
                    await run.validate_submit(owner, result, validate_only=True)
                    return
            result.update(status="unavailable", eligible=None, requested_law=target,
                selection_status=("identity_unavailable" if incomplete_matches else
                    "unknown" if unresolved else "absent"),
                reason=("Selected native law has no complete definition key join" if incomplete_matches else
                    "Native law identity is incomplete; target presence is unknown" if unresolved else
                    "Law is not in this country's current law choices"))
            if incomplete_matches:
                result["law"] = incomplete_matches[0]
        return await owned_operation(self, "validate_enactment", selected,
            result={"action": "validate_enactment", "accepted": None, "completed": False,
                    "eligible": None, "validation": None})

    async def enactment(self):
        """Return the current legislation target, or None when none is active."""
        return await self._run("enactment", self._enactment)

    async def _enactment(self, run):
        country = await run.country()
        if not await run.scalar("Country", "IsEnactingLaw", country):
            return None
        # Copy the full ID only from an actual owned law in the original
        # country sources. Do not construct StopEnacting as an identity receipt
        # and do not rebind the borrowed GetLawBeingEnacted alias as owned Law.
        definitions = await self._definitions(run)
        for getter in (*_ACTIVE_LAWS, *_LAW_OPTIONS):
            async for source_index, element, binding in run.elements("Country." + getter, (country,), "Law"):
                row = await self._law(run, element, binding, definitions,
                        source_index=source_index, source_expression="Country." + getter)
                if row.get("enacting") is True:
                    return dict(row, country_id=run.actor, status="enacting",
                        progress=await run.scalar("Country", "GetLawEnactmentProgress", country, optional=True))
        # A native running-law observation is still useful if this changing
        # provider has no corresponding owned occurrence. Preserve it without
        # fabricating an ID or replacing the observation with an exception.
        typ = await run.evaluate("Country.GetLawBeingEnacted.GetLawType", (country,))
        origin = await run.origin(typ, "LawType", 8)
        definition = definitions.get(origin)
        evidence = run.law_definition_evidence.get(origin) if definition is not None else None
        return dict(id=None, key=evidence["key"] if evidence is not None else None,
            group=evidence["group"] if evidence is not None else None,
            definition=evidence, definition_issues=run.law_definition_issues,
            identity_complete=False, law_type_origin=origin, country_id=run.actor, status="enacting",
            key_reason=(evidence["key_reason"] if evidence is not None else
                "Native law definition origin has no matching key"),
            group_reason=evidence["group_reason"] if evidence is not None else None,
            identity_reason="No owned current-law occurrence was observed in the original country sources",
            progress=await run.scalar("Country", "GetLawEnactmentProgress", country, optional=True))

    async def enact(self, law_key):
        """Start the selected original law command; acceptance is not passage.

        Select by a native full Law ID or definition key from the current source.
        The source country is context, not a decoded command-actor receipt.
        Original command validation decides whether a different enactment can
        start. A clean same-law enacting observation can be an unchanged result.
        """
        target = _target(law_key, "Law")
        async def change(run):
            receipt = {"action": "enact", "status": "not_submitted", "accepted": None,
                "completed": None, "validation": None, "submission": None,
                "law": None, "current": None, "source_country_id": None,
                "native_actor_id": None,
                "actor_scope": "original Law.StartEnacting command; source-country context is not a native actor receipt",
                "completion_scope": "Starting enactment is not final law passage; final completion is not observed",
                "active_observed": None, "enacting_observed": None,
                "native_results": run.operations}
            run.public_result = receipt
            definitions = await self._definitions(run)
            receipt.update(definition_records=run.law_definition_records,
                definition_issues=run.law_definition_issues, selection_issues=[])
            country = await run.country()
            receipt["source_country_id"] = run.actor
            incomplete_matches, unresolved = [], []
            for getter in _LAW_OPTIONS:
                async for source_index, element, binding in run.elements("Country." + getter, (country,), "Law"):
                    row = await self._law(run, element, binding, definitions,
                        source_index=source_index, source_expression="Country." + getter)
                    if not row["identity_complete"]:
                        receipt["selection_issues"].append(row)
                    if target not in (row.get("id"), row.get("key")):
                        if row.get("id" if type(target) is int else "key") is None:
                            unresolved.append(row)
                        continue
                    if not row["identity_complete"]:
                        incomplete_matches.append(row)
                        continue
                    receipt.update(law=row, selection_status="matched")
                    reasons = row.get("state_reasons", {})
                    receipt["active_observed"] = row.get("active") if "active" not in reasons else None
                    receipt["enacting_observed"] = row.get("enacting") if "enacting" not in reasons else None
                    if receipt["enacting_observed"] is True:
                        receipt.update(status="unchanged", reason="The selected original law is already being enacted")
                        return receipt
                    try:
                        owner = await run.generated_owner("Law.StartEnacting", (binding,))
                    except SourceUnavailable as error:
                        receipt.update(status="unavailable", reason=str(error), source_unavailable=error)
                        if run.operations:
                            stage, native = run.operations[-1]
                            if stage in ("Law.StartEnacting", "Law.StartEnacting.carrier"):
                                receipt.update(native_result=native, native_error=native.error)
                        return receipt
                    settled = await run.validate_submit(owner, receipt)
                    for stage, native in reversed(run.operations):
                        if stage == "command.submit":
                            receipt["submission"] = native
                            break
                    if receipt.get("accepted") is True:
                        if not settled:
                            receipt["readback_skipped"] = "Original command ownership remains unsettled"
                            return receipt
                        receipt["readback_scope"] = "source-country enactment observation; not command actor or final passage proof"
                        try:
                            receipt["current"] = await self._enactment(run)
                        except Exception as error:
                            receipt["observation_reason"] = str(error)
                            receipt["readback_error"] = error
                    return receipt
            receipt.update(status="unavailable", requested_law=target,
                selection_status=("identity_unavailable" if incomplete_matches else
                    "unknown" if unresolved else "absent"),
                reason=("Selected native law has no complete definition key join" if incomplete_matches else
                    "Native law identity is incomplete; target presence is unknown" if unresolved else
                    "Law is not in this country's current law choices"))
            if incomplete_matches:
                receipt["law"] = incomplete_matches[0]
            return receipt
        return await self._run("enact", change)

    async def stop_enactment(self):
        """Stop the current original enactment command, without retargeting it.

        Only a clean native IsEnactingLaw=False observation is an unchanged
        result. Unknown/error fields do not stand for False. The original
        producer and native validator decide the selected command's legality.
        """
        async def change(run):
            receipt = {"action": "stop_enactment", "status": "not_submitted", "accepted": None,
                "completed": None, "validation": None, "submission": None,
                "source_country_id": None, "native_actor_id": None,
                "actor_scope": "original Country.GetLawBeingEnacted.StopEnacting command; source context is not a native actor receipt",
                "target_scope": "original current-law getter at command generation; no serialized target receipt",
                "before": None, "after": None, "enacting_observed": None,
                "enacting_after_observed": None,
                "completion_scope": "Native acceptance and observed source-country state are separate; final completion is not inferred",
                "native_results": run.operations}
            run.public_result = receipt
            country = await run.country()
            receipt["source_country_id"] = run.actor
            before = await run.capture("stop_enactment.before", lambda: data.evaluate(run.native,
                "Country.IsEnactingLaw", (country,), output_bytes=4096,
                adapter=run.adapters["data"], process=run.process))
            receipt["before"] = before
            if before.error == 0 and before.kind == data.Kind.BOOL and type(before.value) is bool:
                receipt["enacting_observed"] = before.value
            if receipt["enacting_observed"] is False:
                receipt.update(status="unchanged", reason="No law is being enacted in the observed source country")
                return receipt
            try:
                owner = await run.generated_owner("Country.GetLawBeingEnacted.StopEnacting", (country,))
            except SourceUnavailable as error:
                receipt.update(status="unavailable", reason=str(error), source_unavailable=error)
                if run.operations:
                    stage, native = run.operations[-1]
                    if stage in ("Country.GetLawBeingEnacted.StopEnacting", "Country.GetLawBeingEnacted.StopEnacting.carrier"):
                        receipt.update(native_result=native, native_error=native.error)
                return receipt
            settled = await run.validate_submit(owner, receipt)
            for stage, native in reversed(run.operations):
                if stage == "command.submit":
                    receipt["submission"] = native
                    break
            if receipt.get("accepted") is True:
                if not settled:
                    receipt["readback_skipped"] = "Original command ownership remains unsettled"
                    return receipt
                receipt["readback_scope"] = "source-country IsEnactingLaw observation; not command actor or final effect proof"
                try:
                    after = await run.capture("stop_enactment.after", lambda: data.evaluate(run.native,
                        "Country.IsEnactingLaw", (country,), output_bytes=4096,
                        adapter=run.adapters["data"], process=run.process))
                    receipt["after"] = after
                    if after.error == 0 and after.kind == data.Kind.BOOL and type(after.value) is bool:
                        receipt["enacting_after_observed"] = after.value
                except Exception as error:
                    receipt["observation_reason"] = str(error)
                    receipt["readback_error"] = error
            return receipt
        return await self._run("stop_enactment", change)

    async def _institutions(self, run):
        country = await run.country()
        async for _, element, binding in run.elements("Country.AccessInstitutions", (country,), "Institution"):
            row = dict(id=_id(await run.origin(element, "Institution"), "Institution"),
                key=await run.scalar("Institution", "GetKey", binding, optional=True),
                **display_field(await run.scalar("Institution", "GetName", binding)),
                level=await run.scalar("Institution", "GetInvestmentLevel", binding),
                target_level=await run.scalar("Institution", "GetTargetInvestmentLevel", binding),
                changing=await run.scalar("Institution", "IsChangingLevel", binding),
                max_level=await run.scalar("Institution", "GetMaxLevel", binding, optional=True),
                cost=await run.scalar("Institution", "GetInvestmentCost", binding, optional=True),
                time_description=await run.scalar("Institution", "GetTimeToReachTargetLevelDesc", binding))
            if row["key"] is None:
                row["key_reason"] = "Native institution API exposes a name and ID, not a definition key"
            yield row, binding

    async def institutions(self):
        """Return current institutions, levels, targets, and available native costs."""
        async def read(run):
            return [row async for row, _ in self._institutions(run)]
        return await self._run("institutions", read)

    async def set_institution(self, key, level):
        """Set an investment target with the selected native institution receiver."""
        target = _target(key, "Institution")
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level < (1 << 31):
            raise ValueError("Institution level must be a nonnegative integer")
        async def change(run):
            async for row, binding in self._institutions(run):
                if not _matches(row, target):
                    continue
                if ((row["changing"] is True and type(row["target_level"]) is int and row["target_level"] == level)
                        or (row["changing"] is False and type(row["level"]) is int and row["level"] == level)):
                    return dict(action="set_institution", status="unchanged", accepted=None,
                        institution=row, target_level=level, target_satisfied_observed=True,
                        observed_target_level=row["target_level"], observed_level=row["level"],
                        observed_changing=row["changing"])
                receipt = dict(action="set_institution", status="not_submitted", accepted=None,
                    institution=row, target_level=level, completed=None, validation=None, submission=None,
                    target_satisfied_observed=None, native_results=run.operations)
                run.public_result = receipt
                expression = f"Institution.StartChangingLevelTo({int(level)})"
                try:
                    owner = await run.generated_owner(expression, (binding,))
                except SourceUnavailable as error:
                    receipt.update(status="unavailable", reason=str(error), source_unavailable=error)
                    if run.operations:
                        stage, native = run.operations[-1]
                        if stage in (expression, expression + ".carrier"):
                            receipt.update(native_result=native, native_error=native.error)
                    return receipt
                settled = await run.validate_submit(owner, receipt)
                for stage, native in reversed(run.operations):
                    if stage == "command.submit":
                        receipt["submission"] = native
                        break
                if receipt.get("accepted") is True:
                    if not settled or getattr(run, "retain_unknown", False):
                        receipt["readback_skipped"] = "Original command ownership remains unsettled"
                        return receipt
                    try:
                        receipt["observed_target_level"] = await run.scalar("Institution", "GetTargetInvestmentLevel", binding)
                        receipt["observed_level"] = await run.scalar("Institution", "GetInvestmentLevel", binding)
                    except Exception as error:
                        receipt["observation_reason"] = str(error)
                        receipt["readback_error"] = error
                return receipt
            return rejected("set_institution", "Institution is not present in this country", institution=target)
        return await self._run("set_institution", change)

    async def _interest_group_fields(self, run, rows):
        # Country.AccessAllInterestGroups supplies genuine retained IG wrappers.
        # Their complete native references join the independently checked
        # Objects country-owned IDs; localized names are never an identity join.
        for row in rows:
            row.update(clout=None,approval=None,in_government=None,
                units={"clout":"fraction","approval":"points"},reasons={})
        by_id={row["id"]:row for row in rows}
        seen=set()
        try:
            metadata=await run.functions("InterestGroup")
            expected={}
            for named_type in ("CFixedPoint","int32","bool"):
                try:expected[named_type]=await self._ctx.type_id(named_type)
                except LookupError:expected[named_type]=None
            country=await run.country()
            async for _,element,binding in run.elements("Country.AccessAllInterestGroups",(country,),"InterestGroup"):
                full_id=_id(await run.origin(element,"InterestGroup"),"Interest group")
                row=by_id.get(full_id)
                if row is None or full_id in seen:continue
                seen.add(full_id)
                for field,getter,named_type,kind in (
                        ("clout","GetClout","CFixedPoint",data.Kind.FIXED),
                        ("approval","GetApprovalValue","int32",data.Kind.I32),
                        ("in_government","IsInGovernment","bool",data.Kind.BOOL)):
                    function=metadata.get(getter)
                    if (function is None or function.argument_count!=0
                            or function.return_type_id!=expected[named_type]
                            or any(function.callbacks[2:]) or not any(function.callbacks[:2])):
                        row["reasons"][field]="Native InterestGroup."+getter+" read signature is unavailable"
                        continue
                    try:
                        result=await run.evaluate("InterestGroup."+getter,(binding,))
                    except KnownResultError as error:
                        row["reasons"][field]=str(error)
                        continue
                    value=result.value
                    valid=(result.kind==kind and (
                        isinstance(value,data.FixedPoint) if field=="clout" else
                        type(value) is int if field=="approval" else type(value) is bool))
                    if not valid:
                        row["reasons"][field]="Native InterestGroup."+getter+" returned an unexpected value kind"
                    else:
                        row[field]=fixed_decimal(value) if field=="clout" else value
        except KnownResultError as error:
            for row in rows:
                for field in ("clout","approval","in_government"):
                    if row[field] is None:row["reasons"].setdefault(field,str(error))
        for row in rows:
            if row["id"] not in seen:
                for field in ("clout","approval","in_government"):
                    row["reasons"].setdefault(field,"No current Country-owned InterestGroup Data reference matched this full ID")
        return rows

    async def interest_groups(self, *, details=False):
        """Country-owned IG IDs/names; optional native clout, approval and government state.

        With details=True, clout is a fraction (1 means100%), approval is points,
        and in_government is the original native boolean. Unknown fields remain
        None with reasons. Separate native callbacks are not an atomic snapshot.
        """
        if type(details) is not bool:raise ValueError("details must be True or False")
        async def read(run):
            actor = await self._ctx.country_id()
            run.actor=actor
            rows, start = [], 0
            while True:
                page = await run.call("interest_groups", lambda start=start: objects.enumerate_interest_group_details(
                    self._ctx.native, start_index=start, max_items=256, text_capacity=262144,
                    adapter=self._ctx.adapters["objects"], process=self._ctx.process))
                if page.world != run.world:
                    raise RuntimeError("Interest groups belong to another world")
                for row in page.records:
                    if row.error or row.country_error:
                        raise RuntimeError("Native interest group ownership is unavailable")
                    if row.country_id == actor:
                        rows.append(dict(id=row.resolved_id, country_id=actor, **display_field(row.name),
                            key=None, key_reason="Native snapshot provides the display name, not an IG definition key"))
                if page.next_index >= page.slot_bound:
                    return await self._interest_group_fields(run,rows) if details else rows
                if page.next_index <= start:
                    raise RuntimeError("Native interest group page did not advance")
                start = page.next_index
        return await self._run("interest_groups", read)

    async def _movements(self, run):
        country = await run.country()
        async for _, element, binding in run.elements("Country.AccessPoliticalMovements", (country,), "PoliticalMovement"):
            row = dict(id=_id(await run.origin(element, "PoliticalMovement"), "Movement"),
                **display_field(await run.scalar("PoliticalMovement", "GetName", binding)),
                bolstered=await run.scalar("PoliticalMovement", "IsBolstered", binding),
                suppressed=await run.scalar("PoliticalMovement", "IsSuppressed", binding))
            yield row, binding

    async def _political_detail_value(self, run, binding, receiver, getter, expected_type, kind, *, expression=None):
        receiver_label="related political object" if type(receiver) is int else receiver
        try:
            function=(await run.functions(receiver)).get(getter)
        except LookupError as error:
            return None,str(error)
        # Avoid invoking a formal-argument/action callback as a scalar getter.
        # Expected return metadata is descriptive, not a gate over observed data.
        if (function is None or function.argument_count!=0
                or any(function.callbacks[2:]) or not any(function.callbacks[:2])):
            return None,"Native "+receiver_label+"."+getter+" read signature is unavailable"
        reasons=[]
        try:
            expected=await self._ctx.type_id(expected_type)
        except LookupError as error:
            reasons.append(str(error))
        else:
            if function.return_type_id!=expected:
                reasons.append("Native "+getter+" return metadata differs from expected "+expected_type)
        try:
            result=await run.evaluate(expression or (str(receiver)+"."+getter),(binding,))
        except KnownResultError as error:
            result=error.result
            reasons.append(str(error))
        if result.kind!=kind:
            reasons.append("Native "+getter+" returned an unexpected value kind")
        value=result.value
        if result.kind==data.Kind.STRING:
            if result.snapshot_bytes!=result.total or len(result.snapshot)!=result.total:
                reasons.append("Native "+getter+" text is incomplete")
        if isinstance(value,data.FixedPoint):
            value=fixed_decimal(value)
        # Zero, False, None, partial text and unexpected copied scalar kinds
        # remain visible. The original result/error also remains in run.operations.
        return value,"; ".join(reasons) or None

    async def _political_chain_text(self, run, binding, receiver, getter, leaves):
        # Returned const/raw aliases are consumed by the native expression in
        # ONE callback. Their pointers/Anys are never rebound as owned objects.
        try:
            function=(await run.functions(receiver)).get(getter)
            if (function is None or function.argument_count!=0 or function.return_type_id<0
                    or any(function.callbacks[2:]) or not any(function.callbacks[:2])):
                return None,"Native "+receiver+"."+getter+" read signature is unavailable"
            returned=function.return_type_id
            alias_error=None
            try:metadata=await run.functions(returned)
            except KnownResultError as error:
                metadata={};alias_error=str(error)
            catalog=returned
            if not any(leaf in metadata for leaf in leaves):
                # These relations are used by the original GUI/localization.
                # A const-return alias can omit the named type's leaf catalog.
                # Only metadata validation falls back: the original Character
                # binding and full native chain expression stay unchanged.
                named={ ("Character","GetIdeology"):"Ideology",
                    ("Character","GetInterestGroup"):"InterestGroup",
                    ("PoliticalMovement","GetCoreIdeology"):"Ideology" }.get((receiver,getter))
                if named is not None:
                    metadata=await run.functions(named)
                    catalog=named
            for leaf in leaves:
                if leaf in metadata:
                    return await self._political_detail_value(run,binding,catalog,leaf,"CString",data.Kind.STRING,
                        expression=receiver+"."+getter+"."+leaf)
            if alias_error:return None,alias_error
        except (KnownResultError,LookupError) as error:
            return None,str(error)
        return None,"Native related object has no requested text getter"

    async def _movement_characters(self, run, binding):
        try:
            function=(await run.functions("PoliticalMovement")).get("GetSupportingCharacters")
            if (function is None or function.argument_count!=0 or any(function.callbacks[2:]) or not any(function.callbacks[:2])):
                return None,"Native movement supporting-character source is unavailable"
            source=await run.evaluate("PoliticalMovement.GetSupportingCharacters",(binding,))
            if not isinstance(source.value,data.NativeList) or not source.value_handle:
                return None,"Native supporting-character source is not a retained list"
            count=await run.call("movement_characters.count",lambda:data.list_count(self._ctx.native,
                source.value_handle,adapter=self._ctx.adapters["data"],process=self._ctx.process))
            if type(count.total) is not int or count.total<0:return None,"Native supporting-character count is unavailable"
            character_type=await self._ctx.type_id("Character")
            rows=[]
            for index in range(count.total):
                element=await run.call("movement_characters.element",lambda index=index:data.list_element(
                    self._ctx.native,source.value_handle,index,adapter=self._ctx.adapters["data"],process=self._ctx.process))
                value=element.value
                owned=".?AU?$SAnyType@V?$CPdxPtr@VCDataCharacter@@@@@PdxAnyImpl@@"
                if (not isinstance(value,data.NativeObject) or value.native_type_name!=owned or not value.pointer
                        or not element.value_handle or element.value_handle not in run.values):
                    return None,"Native supporting Character is borrowed without a retained owner; no pointer was rebound"
                character=data.ObjectBinding(character_type,element.value_handle,retained=True)
                row={"id":None,"name":None,"ideology":None,"interest_group":None,
                    "popularity":None,"popularity_description":None,"roles":None,"is_agitator":None,"is_ig_leader":None,
                    "units":{"popularity":"points"},"reasons":{}}
                valid,reason=await self._political_detail_value(run,character,"Character","IsValid","bool",data.Kind.BOOL)
                if valid is not True:
                    row["reasons"]["identity"]=reason or "Native supporting Character is invalid"
                    rows.append(row);continue
                identity,reason=await self._political_detail_value(run,character,"Character","GetID","int32",data.Kind.I32)
                if identity is None or (identity&0xffffffff)==0xffffffff:
                    row["reasons"]["identity"]=reason or "Native Character ID is unavailable"
                    rows.append(row);continue
                full_id=identity&0xffffffff  # Preserve all original signed-int32 reference bits.
                verified=await run.call("movement_character.identity",lambda full_id=full_id:objects.read_ids(
                    self._ctx.native,objects.Domain.CHARACTER,(full_id,),adapter=self._ctx.adapters["objects"],process=self._ctx.process))
                if (verified.world!=run.world or len(verified.records)!=1 or verified.records[0].error
                        or verified.records[0].requested_id!=full_id or verified.records[0].resolved_id!=full_id):
                    row["reasons"]["identity"]="Native Character full ID did not match the independent character resolver"
                    rows.append(row);continue
                row["id"]=full_id
                names=await run.functions("Character")
                getter="GetFullNameNoFormatting" if "GetFullNameNoFormatting" in names else "GetFullName"
                row["name"],reason=await self._political_detail_value(run,character,"Character",getter,"CString",data.Kind.STRING)
                if reason:row["reasons"]["name"]=reason
                for field,getter in (("ideology","GetIdeology"),("interest_group","GetInterestGroup")):
                    row[field],reason=await self._political_chain_text(run,character,"Character",getter,("GetNameNoFormatting","GetName"))
                    if reason:row["reasons"][field]=reason
                for field,getter,named_type,kind in (
                        ("popularity","GetPopularity","CFixedPoint",data.Kind.FIXED),
                        ("popularity_description","GetPopularityDesc","CString",data.Kind.STRING),
                        ("roles","GetAllRoleNames","CString",data.Kind.STRING),
                        ("is_agitator","IsAgitator","bool",data.Kind.BOOL),
                        ("is_ig_leader","IsIGLeader","bool",data.Kind.BOOL)):
                    row[field],reason=await self._political_detail_value(run,character,"Character",getter,named_type,kind)
                    if reason:row["reasons"][field]=reason
                rows.append(row)
            for row in rows:
                for field in ("name", "ideology", "interest_group", "popularity_description", "roles"):
                    row.update(display_field(row[field], field))
            return rows,None
        except (KnownResultError,LookupError) as error:
            return None,str(error)

    async def _movement_laws(self, run, binding, getter):
        try:
            function=(await run.functions("PoliticalMovement")).get(getter)
            if (function is None or function.argument_count!=0 or any(function.callbacks[2:]) or not any(function.callbacks[:2])):
                return None,"Native movement affected-law source is unavailable"
            if not hasattr(run,"movement_law_definitions"):
                run.movement_law_definitions=await self._definitions(run)
            rows=[]
            async for _,element,law in run.elements("PoliticalMovement."+getter,(binding,),"Law"):
                rows.append(await self._law(run,element,law,run.movement_law_definitions))
            return rows,None
        except (KnownResultError,LookupError) as error:
            return None,str(error)
        except RuntimeError as error:
            if "wrapper ownership is not established" in str(error):
                return None,"Native affected Law is borrowed without a retained owner; no pointer was rebound"
            raise

    async def _movement_details(self, run, row, binding):
        row.update(support_fraction=None,radicalism_fraction=None,supporting_individuals=None,insurrectionary=None,causing_obstinance=None,
            core_ideology={"name":None,"description":None,"law_stances":None},supporting_characters=None,
            affected_laws_success=None,affected_laws_stall=None,
            affected_laws_note="Original movement success/stall effect lists; not an enactment order or proof a law is active",
            units={"support_fraction":"fraction","radicalism_fraction":"fraction","supporting_individuals":"individuals"},
            radicalism_label="The native GetRadicalism value is shown as activism by this game's UI",reasons={})
        for field,getter,named_type,kind in (
                ("support_fraction","GetSupport","CFixedPoint",data.Kind.FIXED),
                ("radicalism_fraction","GetRadicalism","CFixedPoint",data.Kind.FIXED),
                ("supporting_individuals","GetNumSupportingIndividuals","int64",data.Kind.I64),
                ("insurrectionary","IsInsurrectionary","bool",data.Kind.BOOL),
                ("causing_obstinance","IsCausingObstinance","bool",data.Kind.BOOL)):
            row[field],reason=await self._political_detail_value(run,binding,"PoliticalMovement",getter,named_type,kind)
            if reason:row["reasons"][field]=reason
        for field,leaves in (("name",("GetNameNoFormatting","GetName")),("description",("GetDesc",)),("law_stances",("GetLawStanceDesc",))):
            row["core_ideology"][field],reason=await self._political_chain_text(run,binding,"PoliticalMovement","GetCoreIdeology",leaves)
            row["core_ideology"].update(display_field(row["core_ideology"][field], field))
            if reason:row["reasons"]["core_ideology."+field]=reason
        row["supporting_characters"],reason=await self._movement_characters(run,binding)
        if reason:row["reasons"]["supporting_characters"]=reason
        for field,getter in (("affected_laws_success","GetAffectedLawsSuccess"),("affected_laws_stall","GetAffectedLawsStall")):
            row[field],reason=await self._movement_laws(run,binding,getter)
            if reason:row["reasons"][field]=reason
        return row

    async def political_movements(self, *, details=False):
        """Movement IDs/names/flags; optional native support, activism and political background.

        Fractions are not percentages. Core ideology/law stances are political
        positions, not the predicted drift target of a numeric metric. Supporting
        characters are not relabelled as leaders. Default reads stay lightweight.
        """
        if type(details) is not bool:raise ValueError("details must be True or False")
        async def read(run):
            rows=[]
            async for row,binding in self._movements(run):
                rows.append(await self._movement_details(run,row,binding) if details else row)
            return rows
        return await self._run("political_movements", read)

    async def _movement_action(self, kind, movement, enabled):
        target = _target(movement, "Political movement")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        async def change(run):
            async for row, binding in self._movements(run):
                if not _matches(row, target):
                    continue
                field = "bolstered" if kind == "bolster" else "suppressed"
                before = row[field]
                if type(before) is not bool:
                    return dict(action=kind, status="unavailable", accepted=None, movement=row,
                        enabled=enabled, observed=before, desired_state_observed=None,
                        reason="Native movement state is not a known boolean; a toggle cannot set the requested state")
                if before == enabled:
                    return dict(action=kind, status="unchanged", accepted=None, movement=row,
                        enabled=enabled, observed=before, desired_state_observed=True)
                receipt = dict(action=kind, status="not_submitted", accepted=None, movement=row,
                    enabled=enabled, observed=None, desired_state_observed=None,
                    validation=None, submission=None, native_results=run.operations)
                run.public_result = receipt
                expression = "PoliticalMovement." + {"bolster": "ToggleBolstering", "suppress": "ToggleSuppression"}[kind]
                try:
                    owner = await run.generated_owner(expression, (binding,))
                except SourceUnavailable as error:
                    receipt.update(status="unavailable", reason=str(error), source_unavailable=error)
                    if run.operations:
                        stage, native = run.operations[-1]
                        if stage in (expression, expression + ".carrier"):
                            receipt.update(native_result=native, native_error=native.error)
                    return receipt
                settled = await run.validate_submit(owner, receipt)
                for stage, native in reversed(run.operations):
                    if stage == "command.submit":
                        receipt["submission"] = native
                        break
                if receipt.get("accepted") is True:
                    if not settled or getattr(run, "retain_unknown", False):
                        receipt["readback_skipped"] = "Original command ownership remains unsettled"
                        return receipt
                    try:
                        after = await run.scalar("PoliticalMovement", "IsBolstered" if kind == "bolster" else "IsSuppressed", binding)
                    except Exception as error:
                        receipt["observation_reason"] = str(error)
                        receipt["readback_error"] = error
                    else:
                        receipt["observed"] = after
                        receipt["desired_state_observed"] = after == enabled if type(after) is bool else None
                        receipt["status"] = "observed" if receipt["desired_state_observed"] is True else "accepted"
                return receipt
            return rejected(kind, "Political movement not found; these commands do not target interest groups", movement=target)
        return await self._run(kind, change)

    async def bolster(self, movement, enabled=True):
        """Bolster a political movement, not an interest group."""
        return await self._movement_action("bolster", movement, enabled)

    async def suppress(self, movement, enabled=True):
        """Suppress a political movement, not an interest group."""
        return await self._movement_action("suppress", movement, enabled)

    async def _state(self, run, state):
        full_id = _id(state, "State")
        return data.ObjectBinding.state(await self._ctx.type_id("State"), full_id)

    def _decree_key_bytes(self, observed, column):
        # project decoded these same copied Page rows. This is not a join to a
        # second source/callback. Native bytes and text-view limits are separate.
        rows = iter(observed["rows"])
        for page in observed["native_pages"]:
            for cells in page.rows:
                row = next(rows)
                cell = next((cell for cell in cells if cell.field == column), None)
                row.update(key_raw=None if cell is None else cell.snapshot,
                           key_total=None if cell is None else cell.total,
                           key_complete=False if cell is None else cell.complete,
                           key_native_cell=cell, key_native_text=None,
                           key_text_lossless=False, key_encoding_reason=None)
                native_reasons = []
                if cell is None:
                    native_reasons.append("native projection did not copy this field")
                    row["key_encoding_reason"] = "Native key bytes are unavailable"
                else:
                    if cell.error:
                        native_reasons.append("native getter error " + str(cell.error))
                    if cell.binding_error:
                        native_reasons.append("native provider binding error " + str(cell.binding_error))
                    if cell.kind != data.Kind.STRING:
                        native_reasons.append("native kind " + str(cell.kind) + " differs from expected " + str(data.Kind.STRING))
                        row["key_encoding_reason"] = "The native copied key field is not text"
                    elif not cell.complete:
                        native_reasons.append("native string copy is incomplete (" + str(len(cell.snapshot)) + "/" + str(cell.total) + " bytes)")
                        row["key_encoding_reason"] = "Incomplete raw key bytes do not provide the whole key text"
                    else:
                        try:
                            text = cell.snapshot.decode("utf-8", "strict")
                            if text.encode("utf-8", "strict") == cell.snapshot:
                                row["key_native_text"] = text
                                row["key_text_lossless"] = row["key"] == text
                                if not row["key_text_lossless"]:
                                    row["key_encoding_reason"] = "The existing key view differs from the complete UTF-8 key text"
                        except UnicodeError:
                            row["key_encoding_reason"] = "Copied key bytes have no lossless UTF-8 text selector"
                # Encoding loss belongs to the view, not to a successful native
                # byte copy. Keep only the original cell/binding/copy failures.
                if native_reasons:
                    row["reasons"]["key"] = "; ".join(native_reasons)
                else:
                    row["reasons"].pop("key", None)

    def _decree_native_key(self, row):
        cell = row.get("key_native_cell")
        if (cell is not None and cell.kind == data.Kind.STRING and cell.complete
                and not cell.error and not cell.binding_error):
            return row.get("key_native_text")
        return None

    async def _decree_options(self, run, state):
        from .._political_data import Field, project
        from .._projection import _projection_supported
        st = await self._state(run, state)
        supported = await _projection_supported(self._ctx, native_fields=True)
        fields = [Field("name", "DecreeType.GetName", kind=data.Kind.STRING, text=True)]
        if supported:
            fields.insert(0, Field("key", data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY),
                                   kind=data.Kind.STRING))
        observed = await project(self, "politics.decree_options.copy", fields, ((st,),),
            source_expression="State.GetPotentialDecrees", owner=run, perform=False)
        if supported:
            self._decree_key_bytes(observed, 0)
        for row in observed["rows"]:
            row.update(state_id=state, eligible=None, eligibility_checked=False,
                       reason="Native decree eligibility has not been validated")
            if not supported:
                row.update(key=None, key_raw=None, key_total=None, key_complete=False, key_native_cell=None,
                           key_native_text=None, key_text_lossless=False,
                           key_encoding_reason="Native key bytes are unavailable")
                row["reasons"]["key"] = "Native decree key copy export is unavailable"
            if "key" in row["reasons"]:
                row["key_reason"] = row["reasons"]["key"]
        complete = supported and not observed["reasons"] and all(not row["reasons"] for row in observed["rows"])
        observed.update(complete=complete, status="observed" if complete else "partial" if observed["rows"] else "unavailable")
        run.operations.append(("decree_options.observation", observed))
        return observed

    def _decree_read_rows(self, observed, label):
        rows = observed["rows"]
        for row in rows:
            row["read_complete"] = observed["complete"]
            if observed["reasons"] or row["reasons"]:
                row.update(source_reasons=observed["reasons"], native_pages=observed["native_pages"])
        if not rows and observed["reasons"]:
            original = next((reason["native_result"] for reason in observed["reasons"]
                             if "native_result" in reason), None)
            if original is None:
                original = observed["native_pages"][-1]
            error = KnownResultError(label + ": native source read is incomplete", original)
            error.partial_result = observed
            raise error
        return rows

    async def decree_options(self, state):
        """Copy native decree keys and names; eligibility remains unvalidated."""
        _id(state, "State")
        async def read(run):
            return self._decree_read_rows(await self._decree_options(run, state), "decree_options")
        return await self._run("decree_options", read)

    async def set_decree(self, state, decree):
        """Request one native decree key; the original command captures its actor."""
        _id(state, "State")
        async def change(run):
            result = dict(action="set_decree", status="unavailable", accepted=None, reason=None,
                          state_id=state, requested_decree_key=decree, key_scope="request",
                          decree=None, active_decree_observed=None)
            run.public_result = result
            if not isinstance(decree, str):
                result.update(status="unsupported", reason="Selected decree requires a native key string; definition IDs are not supported")
                return result
            try:
                literal = data.plain_cstring_literal(decree)
            except (ValueError, UnicodeError) as error:
                result.update(status="unsupported", literal_error=error,
                              reason="This native key cannot use the supported plain CString literal route")
                return result
            observed = await self._decrees(run, state)
            result["observation"] = observed
            for row in observed["rows"]:
                if self._decree_native_key(row) == decree:
                    result.update(status="unchanged", active_decree_observed=True, decree=row,
                                  reason="Native active decree observed; no command was submitted")
                    return result
            st = await self._state(run, state)
            expression = "GetDecreeType(" + literal + ").GetSetCommand(State.Self)"
            try:
                owner = await run.generated_owner(expression, (st,))
                await run.validate_submit(owner, result)
            except KnownResultError as error:
                result.update(status="native_error", native_error=error.result.error,
                              native_result=error.result, reason=str(error))
            except SourceUnavailable as error:
                result["reason"] = str(error)
            result["native_results"] = tuple(run.operations)
            return result
        return await self._run("set_decree", change)

    async def _decrees(self, run, state):
        from .._political_data import Field, project
        from .._projection import _projection_supported
        st = await self._state(run, state)
        if await _projection_supported(self._ctx, native_fields=True):
            fields = (Field("id", data.NativeField(data.NativeFieldKind.DECREE_ID),
                            kind=data.Kind.U32, full_id=True),
                      Field("key", data.NativeField(data.NativeFieldKind.DECREE_TYPE_KEY, "Decree.GetType"),
                            kind=data.Kind.STRING))
            observed = await project(self, "politics.decrees.copy", fields, ((st,),),
                source_expression="State.AccessDecrees", owner=run, perform=False)
            self._decree_key_bytes(observed, 1)
        else:
            # Older adapters still expose the original owned instance ID.
            # Never obtain an optional key by creating a candidate command.
            observed = dict(rows=[], status="partial", complete=False, reasons=[], native_pages=[],
                            source_expression="State.AccessDecrees", atomic_snapshot=False)
            try:
                async for _, element, _ in run.elements("State.AccessDecrees", (st,), "Decree"):
                    row = dict(id=None, key=None, reasons={"key": "NativeFields export unavailable"},
                               native_result=element)
                    observed["rows"].append(row)
                    try:
                        row["id"] = await run.origin(element, "Decree")
                        if row["id"] == 0xffffffff:
                            row["reasons"]["id"] = "native invalid-reference sentinel"
                    except RuntimeError as error:
                        if str(error) != "Native Decree wrapper ownership is not established":
                            raise
                        row["reasons"]["id"] = str(error)
            except KnownResultError as error:
                if observed["rows"] and observed["rows"][-1]["id"] is None:
                    observed["rows"][-1]["reasons"]["id"] = str(error)
                observed["reasons"].append({"reason": str(error), "native_result": error.result})
            except RuntimeError as error:
                if str(error) != "Native Decree wrapper ownership is not established":
                    raise
                original = run.operations[-1][1]
                observed["rows"].append(dict(id=None, key=None, native_result=original,
                    reasons={"id": str(error), "key": "Native decree key copy export is unavailable"}))
                observed["reasons"].append({"reason": str(error), "native_result": original})
        for row in observed["rows"]:
            if "key_raw" not in row:
                row.update(key_raw=None, key_total=None, key_complete=False, key_native_cell=None,
                           key_native_text=None, key_text_lossless=False,
                           key_encoding_reason="Native key bytes are unavailable")
            row["state_id"] = state
            if "key" in row["reasons"]:
                row["key_reason"] = row["reasons"]["key"]
        complete = not observed["reasons"] and all(not row["reasons"] for row in observed["rows"])
        observed.update(complete=complete, status="observed" if complete else "partial" if observed["rows"] else "unavailable")
        run.operations.append(("decrees.observation", observed))
        return observed

    async def decrees(self, state):
        """Copy each active decree's complete native ID and native type key."""
        _id(state, "State")
        async def read(run):
            return self._decree_read_rows(await self._decrees(run, state), "decrees")
        return await self._run("decrees", read)

    async def remove_decree(self, state, decree):
        """Remove one native instance, selected by its full ID or clean type key."""
        _id(state, "State")
        target = _target(decree, "Decree")
        async def change(run):
            observed = await self._decrees(run, state)
            by_id = isinstance(target, int)
            matches, uncertain = {}, False
            for row in observed["rows"]:
                clean_id = (type(row["id"]) is int and 0 <= row["id"] < 0xffffffff
                            and "id" not in row["reasons"])
                native_key = self._decree_native_key(row)
                if not clean_id or not by_id and native_key is None:
                    uncertain = True
                    continue
                if (row["id"] if by_id else native_key) == target:
                    matches[row["id"]] = row
            if len(matches) > 1:
                return dict(action="remove_decree", status="rejected", accepted=None,
                    reason="Native decree key is ambiguous; select a full decree ID",
                    decree=target, state_id=state, observation=observed)
            if not matches:
                incomplete = uncertain or bool(observed["reasons"])
                return dict(action="remove_decree", status="unavailable" if incomplete else "rejected",
                    accepted=None, reason="Native decree identity is unsupported or incomplete" if incomplete
                    else "Active decree not found in this state", decree=target, state_id=state, observation=observed)
            row = next(iter(matches.values()))
            result = dict(action="remove_decree", status="unavailable", accepted=None, reason=None,
                          decree=row, state_id=state, observation=observed)
            st = await self._state(run, state)
            try:
                async for _, element, binding in run.elements("State.AccessDecrees", (st,), "Decree"):
                    if await run.origin(element, "Decree") != row["id"]:
                        continue
                    owner = await run.generated_owner("Decree.GetRemoveCommand", (binding,))
                    await run.validate_submit(owner, result)
                    result["native_results"] = tuple(run.operations)
                    return result
            except KnownResultError as error:
                result.update(status="native_error", native_error=error.result.error,
                              native_result=error.result, reason=str(error))
            except SourceUnavailable as error:
                result["reason"] = str(error)
            except RuntimeError as error:
                if str(error) != "Native Decree wrapper ownership is not established":
                    raise
                result["reason"] = str(error)
            if result["reason"] is None:
                result["reason"] = "Selected full decree ID is absent from the fresh native source"
            result["native_results"] = tuple(run.operations)
            return result
        return await self._run("remove_decree", change)
