"""Original country diplomatic-action catalogs, copied without candidate commands.

The five source families are the native country-interaction UI models. They are
not a fabricated registry of all game definitions. Each row keeps its native
key and actual parties. Available/potential membership is native source data,
not a host-side reconstruction of the game's diplomatic rules.
"""
from __future__ import annotations

import asyncio

from ... import v3_data as data, v3_commands as commands
from .._political_data import Field as F, binding, project, resolve_country_id, snapshot
from .._choices import OwnedChoice, OwnedChoices
from .._choice_sources import _choice_elements, _choice_binding, _copy_owned_rows, _finish_choice_report
from .._political_actions import owned_operation, selected_command
from .._run import KnownResultError, rejected
from .. import _run

K=data.Kind
DIPLOMATIC_ACTION_FIELDS=(
    F("native_key","DiplomaticAction.GetType.GetKey",K.STRING),
    F("name","DiplomaticAction.GetName",K.STRING,text=True),
    F("propose_name","DiplomaticAction.GetProposeName",K.STRING,text=True),
    F("break_name","DiplomaticAction.GetBreakName",K.STRING,text=True),
    F("ask_to_break_name","DiplomaticAction.GetAskToBreakName",K.STRING,text=True),
    F("description","DiplomaticAction.GetDesc",K.STRING,text=True),
    F("effects","DiplomaticAction.GetEffectsDesc",K.STRING,text=True),
    F("first_country_id","DiplomaticAction.GetFirstCountry.GetID",full_id=True),
    F("second_country_id","DiplomaticAction.GetSecondCountry.GetID",full_id=True),
    F("active_pact","DiplomaticAction.IsActivePact",K.BOOL),
    F("non_active_pact","DiplomaticAction.IsNonActivePact",K.BOOL),
    F("persistent_pact","DiplomaticAction.IsPersistentPact",K.BOOL),
    F("requires_approval","DiplomaticAction.RequiresApproval",K.BOOL),
    F("requires_approval_to_propose","DiplomaticAction.RequiresApprovalToPropose",K.BOOL),
    F("requires_approval_to_break","DiplomaticAction.RequiresApprovalToBreak",K.BOOL),
    F("incomplete_target_data","DiplomaticAction.HasIncompleteTargetData",K.BOOL),
    F("maintenance_cost","DiplomaticAction.GetMaintenanceCost",K.FIXED),
    F("base_maintenance_cost","DiplomaticAction.GetBaseMaintenanceCost",K.FIXED),
    F("maintenance_cost_description","DiplomaticAction.GetMaintenanceCostDesc",K.STRING,text=True),
    F("ai_will_accept_no_obligation","DiplomaticAction.WillAIAcceptNoObligation",K.BOOL),
    F("ai_may_accept_no_obligation","DiplomaticAction.WillAIMaybeAcceptNoObligation",K.BOOL),
    F("acceptance_value_no_obligation","DiplomaticAction.GetAcceptanceValueNoObligation",K.I32),
    F("random_acceptance_chance_no_obligation","DiplomaticAction.GetRandomAcceptanceChanceNoObligation",K.FIXED),
    F("can_call_in_obligation","DiplomaticAction.CanCallInObligation",K.BOOL),
    F("can_owe_obligation","DiplomaticAction.CanOweObligation",K.BOOL),
    F("ongoing_action","DiplomaticAction.GetType.IsOngoingDiplomaticAction",K.BOOL),
    F("ongoing_pact","DiplomaticAction.GetType.IsOngoingDiplomaticPact",K.BOOL),
    F("instant_action","DiplomaticAction.GetType.IsInstantDiplomaticAction",K.BOOL),
    F("must_select_first_state","DiplomaticAction.GetType.MustSelectFirstState",K.BOOL),
    F("must_select_second_state","DiplomaticAction.GetType.MustSelectSecondState",K.BOOL),
    F("first_country_is_player","DiplomaticAction.GetFirstCountry.IsPlayer",K.BOOL),
    F("second_country_is_player","DiplomaticAction.GetSecondCountry.IsPlayer",K.BOOL),
)

_DIPLOMATIC_ACTION_DETAILS_FIELDS = (
    F("native_key", "DiplomaticAction.GetType.GetKey", K.STRING),
    F("native_action_id", "DiplomaticAction.GetID", K.U32),
    F("first_country_id", "DiplomaticAction.GetFirstCountry.GetID", K.I32, full_id=True),
    F("second_country_id", "DiplomaticAction.GetSecondCountry.GetID", K.I32, full_id=True),
    F("first_state_id", "DiplomaticAction.GetFirstState.GetID", K.I32, full_id=True),
    F("first_state_name", "DiplomaticAction.GetFirstState.GetName", K.STRING, text=True),
    F("second_state_id", "DiplomaticAction.GetSecondState.GetID", K.I32, full_id=True),
    F("second_state_name", "DiplomaticAction.GetSecondState.GetName", K.STRING, text=True),
    F("calling_in_obligation", "DiplomaticAction.IsCallingInObligation", K.BOOL),
    F("promising_obligation", "DiplomaticAction.IsPromisingObligation", K.BOOL),
    F("can_potentially_owe_obligation", "DiplomaticAction.CanPotentiallyOweObligation", K.BOOL),
    F("requires_confirmation", "DiplomaticAction.RequiresConfirmation", K.BOOL),
    F("target_states_description", "DiplomaticAction.GetTargetStatesDesc", K.STRING, text=True),
    F("ai_will_accept", "DiplomaticAction.WillAIAccept", K.BOOL),
    F("ai_will_accept_description", "DiplomaticAction.GetWillAcceptDesc", K.STRING, text=True),
    F("ai_acceptance_breakdown", "DiplomaticAction.GetWillAcceptBreakdown", K.STRING, text=True),
    F("type_has_modify_pact", "DiplomaticAction.GetType.HasModifyPact", K.BOOL),
    F("type_has_reverse_pact", "DiplomaticAction.GetType.HasReversePact", K.BOOL),
    F("type_has_transfer_pact", "DiplomaticAction.GetType.HasTransferPact", K.BOOL),
)


_ACTION_GROUPS={"general":"General","overlord":"Overlord","subject":"Subject",
                "bloc_leader":"PowerBlocLeader","bloc_member":"PowerBlocMember"}
_SOURCE_SCOPES={"all":"All","available":"Available","potential":"Potential"}
_CONTEXT=(F("target_country_id","Country.GetID",full_id=True),
          F("target_name","Country.GetName",K.STRING,text=True),
          F("observed_player_country_id","GetPlayer.GetID",full_id=True))
_GENERAL_SOURCE = "Country.GetAllGeneralDiploActions"
_DIPLOMATIC_SOURCES = {
    "Country.Get" + scope_name + group_name + "DiploActions": (group, scope)
    for group, group_name in _ACTION_GROUPS.items()
    for scope, scope_name in _SOURCE_SCOPES.items()
}
_UNCLOSED_SELECTORS = ("intent", "pact", "proposal_accept_decline", "stop")


def _diplomatic_choice(choice, ctx):
    if not isinstance(choice, OwnedChoice) or choice.native_type != "DiplomaticAction":
        raise TypeError("select an original owned DiplomaticAction choice, not a key/ID/index")
    catalog = choice._catalog
    catalog._ensure_open()
    if (catalog.context is not ctx or catalog.native is not ctx.native
            or catalog.process is not ctx.process):
        raise ValueError("choice belongs to another original context/client/process")
    if not any(record is choice for record in catalog):
        raise ValueError("choice was not admitted by its original catalog")
    if catalog.metadata.get("source") not in _DIPLOMATIC_SOURCES:
        raise ValueError("choice must come from an original native country diplomatic-action source")
    return catalog


# These are command-field edits, not Window/source/intent selectors.
_COMMAND_EDIT_FIELDS = ("obligation", "first_state", "second_state", "country_only")
_COMMAND_EDIT_UNCLOSED = _UNCLOSED_SELECTORS
_COMMAND_EDIT_SCOPE = ("Reads observe the retained original General row. Explicit command edits rebuild a separate command; "
                       "not in-place source or Window setters. Original-row after data is not edited-selector readback. "
                       "Only native command type21256 has these four field edits; other original command types are kept and reported edit_unimplemented.")
_OBLIGATION_VALUES = {"none": 357, "owe_obligation": 21620, "call_in_obligation": 21619}


class _CommandEditUnsupported(ValueError):
    """The original supported wire representation cannot be preserved for editing."""


def _command_key(item, token):
    return (isinstance(item, commands.Assignment) and isinstance(item.key, commands.Token)
            and item.key.value == token)


def _edit_diplomatic_wire(wire, edits):
    """Edit ALL matching numeric schema occurrences, preserving order/duplicates.

    ReadField accumulates repeated parameters/targets blocks. Missing refs/bool
    are therefore materialized only when globally absent across all targets
    blocks, once in the last such block. Actor omission is independently native
    UINTMAX. These values come from this complete original Writer, not a factory
    default. Existing occurrences are never collapsed, deleted or reordered.
    """
    try:
        tree = commands.decode_command(wire)
        if commands.encode_tree(tree) != wire:
            raise _CommandEditUnsupported("Original wire has noncanonical or unsupported scalar bytes")
    except (ValueError, TypeError, OverflowError) as error:
        raise _CommandEditUnsupported("Original wire editing is unsupported: " + str(error)) from error
    if not isinstance(tree.key, commands.Token) or tree.key.value != 21256:
        raise _CommandEditUnsupported("Original command is not native type21256")
    actors = 0
    blocks = []
    counts = {10374: 0, 10375: 0, 24084: 0, 24085: 0, 24086: 0}
    domains = {11760: 0, 21268: 0, 24152: 0}
    for root_index, root in enumerate(tree.value):
        if _command_key(root, 10304):
            if not isinstance(root.value, commands.UInt32):
                raise _CommandEditUnsupported("Original root actor is not canonical UInt32")
            actors += 1
        if not _command_key(root, 65):
            continue
        if not isinstance(root.value, tuple):
            raise _CommandEditUnsupported("Original parameters block is unsupported")
        for domain_index, item in enumerate(root.value):
            if _command_key(item, 12795):
                if not isinstance(item.value, tuple):
                    raise _CommandEditUnsupported("Original targets block is unsupported")
                blocks.append((root_index, domain_index))
                for field in item.value:
                    for token in counts:
                        if _command_key(field, token):
                            valid = (type(field.value) is bool if token == 24086
                                     else isinstance(field.value, commands.UInt32))
                            if not valid:
                                raise _CommandEditUnsupported("Original target field has an unsupported native type")
                            counts[token] += 1
            for token in domains:
                if not _command_key(item, token):
                    continue
                domains[token] += 1
                if token == 11760:
                    valid = type(item.value) is str
                else:
                    allowed = (357, 21620, 21619) if token == 21268 else (24150, 24151, 24431)
                    valid = isinstance(item.value, commands.Token) and item.value.value in allowed
                if not valid:
                    raise _CommandEditUnsupported("Original definition/enum is unsupported or noncanonical")
    if not blocks or not all(domains.values()):
        raise _CommandEditUnsupported("Original Writer parameters/targets/definition/enum form is unsupported")
    requested = {}
    for name, token in (("first_state", 24084), ("second_state", 24085)):
        if name in edits:
            requested[token] = commands.UInt32(edits[name])
    if "country_only" in edits:
        requested[24086] = edits["country_only"]
    original_omissions = []
    last = blocks[-1]
    output = []
    for root_index, root in enumerate(tree.value):
        if not _command_key(root, 65):
            output.append(root)
            continue
        domain = []
        for domain_index, item in enumerate(root.value):
            if _command_key(item, 12795):
                fields = []
                for field in item.value:
                    replacement = next((value for token, value in requested.items()
                                        if _command_key(field, token)), None)
                    fields.append(commands.Assignment(field.key, replacement)
                                  if replacement is not None else field)
                if (root_index, domain_index) == last:
                    for token, count in counts.items():
                        if count == 0:
                            original = False if token == 24086 else commands.UInt32(0xffffffff)
                            fields.append(commands.Assignment(commands.Token(token), requested.get(token, original)))
                            original_omissions.append((token, original))
                domain.append(commands.Assignment(item.key, tuple(fields)))
            elif _command_key(item, 21268) and "obligation" in edits:
                domain.append(commands.Assignment(item.key, commands.Token(_OBLIGATION_VALUES[edits["obligation"]])))
            else:
                domain.append(item)
        output.append(commands.Assignment(root.key, tuple(domain)))
    if actors == 0:
        output.append(commands.Assignment(commands.Token(10304), commands.UInt32(0xffffffff)))
        original_omissions.append((10304, commands.UInt32(0xffffffff)))
    edited = commands.Assignment(tree.key, tuple(output))
    return commands.encode_tree(edited), original_omissions


def _keep_deserialize_partial(run, result, error):
    """Keep the original uncertain query/result; never manufacture a new owner."""
    query = getattr(error, "query", None) or getattr(run, "uncertain_query", None)
    partial = getattr(error, "partial_result", None)
    if partial is None:
        partial = getattr(query, "partial_result", None)
    if partial is None:
        partial = getattr(query, "_result", None)
    if partial is None:
        partial = getattr(error, "result", None)
    result.update(deserialize_error=error, deserialize_query=query)
    if partial is not None:
        result["deserialize_result"] = partial
        if getattr(partial, "command", 0):
            run.unadopted_command = partial
            run.retain_unknown = True


async def _execute_diplomatic_edits(api, lease, edits):
    from .._political_actions import _known_owners
    answer = {"action": "execute_diplomatic_action", "expression": "DiplomaticAction.ExecuteAction",
        "accepted": None, "completed": False, "status": "not_submitted",
        "validation": None, "submission": None, "command_edits": dict(edits),
        "wire_edit_performed": False, "native_parameter_edit_observed": None,
        "edit_occurrence_policy": "all matching numeric schema occurrences; globally absent Writer refs/bool materialized once, not per repeated block"}
    scope = None
    async def edit(run, result):
        nonlocal scope
        scope = run
        retained = lease.attach(run)
        original = await run.generated_owner("DiplomaticAction.ExecuteAction", (retained,))
        serialized = await run.command_result("command.serialize", original.serialize, owner=original)
        result.update(original_serialization=serialized, original_wire=serialized.data,
            native_result=serialized, native_error=serialized.error)
        if run.owner_unsettled(original) or getattr(run, "retain_unknown", False):
            result.update(status="native_result", reason="Original serialization ownership remains unsettled")
            return
        if serialized.error:
            result.update(status="native_error", reason="Original serialization did not return a complete stream")
            return
        try:
            if original.type_token != 21256:
                raise _CommandEditUnsupported("Original command owner is not native type21256")
            wire, omissions = _edit_diplomatic_wire(serialized.data, edits)
        except _CommandEditUnsupported as error:
            result.update(status="edit_unimplemented", reason=str(error), edit_error=error)
            return
        result.update(edited_wire=wire, original_writer_omissions=omissions, wire_edit_performed=True,
            edit_scope="requested supported wire fields only; no total Reader success or final effect is inferred")
        try:
            receipt = await run.capture("command.deserialize", lambda: commands.request(run.native,
                commands.Action.DESERIALIZE, data=wire, world=original.world,
                adapter=run.adapters["commands"], process=run.process))
        except BaseException as error:
            _keep_deserialize_partial(run, result, error)
            raise
        result.update(deserialize_result=receipt, native_result=receipt, native_error=receipt.error)
        if receipt.error or not receipt.command:
            if receipt.command:
                run.unadopted_command = receipt
            raise KnownResultError("command.deserialize", receipt)
        owner = run.adopt(receipt, expected_world=original.world)
        if receipt.action != commands.Action.DESERIALIZE or owner.type_token != original.type_token:
            result.update(status="edit_unimplemented", reason="Native reconstructed command class/action differs")
            return
        await run.validate_submit(owner, result)
        for stage, native in reversed(run.operations):
            if stage == "command.submit":
                result["submission"] = native
                break
    result = await owned_operation(api, "execute_diplomatic_action", edit,
        namespace="diplomacy", result=answer, perform=False)
    # The finisher may discover unknown cleanup; do not use its earlier snapshot
    # to authorize a readback. This changes no primary native result.
    result["ownership_settled"] = _known_owners(scope)
    return result


class DiplomaticActionsMixin:
    async def diplomatic_action_choices(self, target, *, group="general", scope="all"):
        """Keep genuine rows from one native country diplomatic-action source.

        Groups are general, overlord, subject, bloc_leader and bloc_member.
        Scope is all, available or potential, using that exact native provider.

        Only original owning CPdxPtr<CDataDiplomaticAction> Anys are admitted.
        Borrowed-pointer/failed-owning-converter values remain rejected-row
        evidence. RAW payloads may own an exact native header. The source
        Country is the source target; actual native parties and command actor
        remain original, without host role replacement. Discovery creates no candidate commands.
        Close the returned catalog after its selected operations have settled.
        """
        if type(group) is not str or group not in _ACTION_GROUPS:
            raise ValueError("choose a native diplomatic-action group: " + ", ".join(_ACTION_GROUPS))
        if type(scope) is not str or scope not in _SOURCE_SCOPES:
            raise ValueError("scope must be all, available or potential")
        if target is None:
            raise ValueError("choose an explicit diplomatic target country")
        source = "Country.Get" + _SOURCE_SCOPES[scope] + _ACTION_GROUPS[group] + "DiploActions"
        group_name = _ACTION_GROUPS[group]
        async def read():
            target_id = await resolve_country_id(self._ctx, target)
            country = await binding(self._ctx, "Country", target_id)
            run = _run.Run(self._ctx, "diplomatic_action_choices", namespace="diplomacy")
            report = {"source": source, "source_group": group, "source_scope": scope, "status": "working", "complete": False,
                "reasons": [], "rejected_rows": [], "sources": [], "native_pages": [],
                "command_validation_performed": False, "target_country_id": target_id,
                "actor_scope": "original native command actor and native parties preserved, no host replacement",
                "available": True if scope == "available" else None,
                "availability_source": "native available collection; not NativeValidate" if scope == "available" else "native source membership; not NativeValidate",
                "unclosed_selectors": list(_UNCLOSED_SELECTORS),
                "supported_command_edits": list(_COMMAND_EDIT_FIELDS),
                "command_edit_scope": _COMMAND_EDIT_SCOPE.replace("General", group_name),
                "read_scope": "retained original " + group_name + " rows; no command parameters are edited by this read",
                "atomic_snapshot": False}
            catalog = OwnedChoices(run, metadata={"source": source, "source_group": group, "source_scope": scope,
                "target_country_id": target_id, "target_binding": country, "result": report})
            try:
                candidates = []
                async def admit():
                    for candidate, row in await _copy_owned_rows(run, catalog,
                            DIPLOMATIC_ACTION_FIELDS, candidates, (country,)):
                        row["command_validation_performed"] = False
                        catalog.add(candidate[0], "DiplomaticAction", candidate[1], row)
                async for index, native in _choice_elements(run, catalog, source, (country,)):
                    retained = _choice_binding(run, catalog, native, "DiplomaticAction",
                        None, source, index)
                    if retained is not None:
                        candidates.append((native, retained,
                            {"expression": source, "source_group": group, "source_scope": scope, "source_index": index}))
                    if len(candidates) == 64:
                        await admit()
                        candidates = []
                if candidates:
                    await admit()
                return _finish_choice_report(catalog)
            except BaseException as error:
                error.choices = catalog
                report.update(status="retained_after_error", complete=False, failure=error)
                raise
            finally:
                report["native_results"] = tuple(run.operations)
        return await self._ctx.perform("diplomacy.diplomatic_action_choices", read)

    async def diplomatic_action_details(self, choice):
        """Copy one explicit extra descriptor row from its original selected source.

        This is one bounded read, not a selector editor or command preview.
        State/obligation/confirmation/AI/type flags are current native values;
        they do not determine start/stop intent or close selector mutations.
        native_action_id is the native uint32 row identifier, not a persistent
        full-reference identity, Pact ID or lookup handle. Nullable State fields
        remain partial observations and never turn the whole row into no action.
        """
        catalog = _diplomatic_choice(choice, self._ctx)
        source = catalog.metadata["source"]
        source_group, source_scope = _DIPLOMATIC_SOURCES[source]
        group_name = _ACTION_GROUPS[source_group]
        async def work():
            _diplomatic_choice(choice, self._ctx)
            async with choice.borrow(self._ctx) as lease:
                async def observe(run, report):
                    retained = lease.attach(run)
                    candidate = (choice.native_result, retained,
                        {"expression": source, "source_group": source_group, "source_scope": source_scope})
                    copied = await _copy_owned_rows(run, catalog,
                        _DIPLOMATIC_ACTION_DETAILS_FIELDS, [candidate], report=report)
                    row = copied[0][1]
                    complete = row["complete"] and not report["reasons"]
                    report.update(row=row, read_complete=complete, complete=complete,
                        status="observed" if complete else "partial",
                        source_native_result=choice.native_result,
                        source=source, source_group=source_group, source_scope=source_scope,
                        observation_scope="same retained original " + group_name + " action; actual key and country parties copied in this callback",
                        native_action_id_semantics="native uint32 row identifier only; not persistent/full-reference/Pact identity or a lookup handle",
                        command_validation_performed=False, selector_mutation_performed=False,
                        unclosed_selectors=list(_UNCLOSED_SELECTORS),
                        supported_command_edits=list(_COMMAND_EDIT_FIELDS),
                        command_edit_scope=_COMMAND_EDIT_SCOPE.replace("General", group_name),
                        read_scope="retained original " + group_name + " row; no command parameters are edited by this read",
                        atomic_snapshot=False)
                return await owned_operation(self, "diplomatic_action_details", observe,
                    namespace="diplomacy", perform=False)
        return await self._ctx.perform("diplomacy.diplomatic_action_details", work)

    async def execute_diplomatic_action(self, choice, *, obligation=None, first_state=None,
                                        second_state=None, country_only=None, readback=True):
        """Execute one original selected-source action, optionally editing four fields.

        None leaves each field unchanged; obligation="none" explicitly selects
        the native no-obligation enum. State refs include zero and UINTMAX.
        State edits do not change country_only. An explicit bool edit is a
        separate native semantic choice. Intent/pact/proposal/stop are not set.
        No edits keeps the original no-Serialize selected-command route.
        With edits, one original clone is serialized and a separate owned native
        command is reconstructed; only that command is validated/submitted once.
        Native acceptance and copied original-row after data are not final effect
        or a readback of the rewritten command's selector values.
        """
        catalog = _diplomatic_choice(choice, self._ctx)
        source = catalog.metadata["source"]
        source_group, source_scope = _DIPLOMATIC_SOURCES[source]
        group_name = _ACTION_GROUPS[source_group]
        if type(readback) is not bool:
            raise TypeError("readback must be bool")
        if obligation is not None and (type(obligation) is not str or obligation not in _OBLIGATION_VALUES):
            raise ValueError("obligation must be none, owe_obligation, call_in_obligation or None")
        for name, value in (("first_state", first_state), ("second_state", second_state)):
            if value is not None and (type(value) is not int or not 0 <= value <= 0xffffffff):
                raise ValueError(name + " must be a full uint32 State ref or None")
        if country_only is not None and type(country_only) is not bool:
            raise ValueError("country_only must be bool or None")
        edits = {name: value for name, value in (("obligation", obligation), ("first_state", first_state),
            ("second_state", second_state), ("country_only", country_only)) if value is not None}
        async def work():
            _diplomatic_choice(choice, self._ctx)
            async with choice.borrow(self._ctx) as lease:
                if edits:
                    result = await _execute_diplomatic_edits(self, lease, edits)
                else:
                    async def select(run):
                        return (lease.attach(run),)
                    result = await selected_command(self, "execute_diplomatic_action",
                        "DiplomaticAction.ExecuteAction", select,
                        namespace="diplomacy", perform=False)
                result.update(choice=dict(choice), effect_observed=None,
                    target_country_id=catalog.metadata["target_country_id"],
                    source=source, source_group=source_group, source_scope=source_scope,
                    actor_scope="original native command actor and native parties preserved, no host replacement",
                    unclosed_selectors=list(_COMMAND_EDIT_UNCLOSED),
                    supported_command_edits=list(_COMMAND_EDIT_FIELDS),
                    command_edit_scope=_COMMAND_EDIT_SCOPE.replace("General", group_name),
                    action_scope="original " + group_name + " action; explicit wire edits rebuild a separate command, not in-place source or Window setters; no intent/pact/proposal/stop edit")
                if not readback or result.get("accepted") is not True:
                    return result
                if not result.get("ownership_settled"):
                    result["readback_skipped"] = "Original command ownership remains unsettled"
                    return result
                async def observe(run, report):
                    retained = lease.attach(run)
                    candidate = (choice.native_result, retained,
                        {"expression": source, "source_group": source_group, "source_scope": source_scope})
                    rows = await _copy_owned_rows(run, catalog, DIPLOMATIC_ACTION_FIELDS,
                        [candidate], report=report)
                    report["row"] = rows[0][1]
                    report["observation_scope"] = "same retained original row; not rewritten-command selector readback or final effect proof"
                try:
                    result["after"] = await owned_operation(self,
                        "execute_diplomatic_action.after", observe,
                        namespace="diplomacy", perform=False)
                except asyncio.CancelledError as error:
                    # The primary operation already completed. Keep a formal
                    # exception exit without depending on private lease owners.
                    # Existing exception context, including None, is untouched.
                    try:
                        if not hasattr(error, "diplomatic_action_context"):
                            error.diplomatic_action_context = {
                                "action": "execute_diplomatic_action", "stage": "after_readback",
                                "completed_primary_result": result,
                                "submission": result.get("submission"),
                                "native_result": result.get("native_result"),
                            }
                    except BaseException:
                        # Diagnostics must never replace the original cancel.
                        pass
                    raise
                except Exception as error:
                    result["readback_error"] = error
                return result
        return await self._ctx.perform("diplomacy.execute_diplomatic_action", work)

    async def diplomatic_options(self,target,*,groups=None,scope="all"):
        """Copy real actions, effects, costs and native acceptance observations.

        Default discovery does not create, serialize or validate any command.
        Select ``scope="available"`` or ``"potential"`` for the corresponding
        original game source. All-scope eligibility stays unknown until checked
        for a selected action. Rows are not joined or deduplicated by name/index.
        """
        if scope not in _SOURCE_SCOPES:raise ValueError("scope must be all, available or potential")
        groups=tuple(_ACTION_GROUPS) if groups is None else tuple(groups)
        if not groups or any(group not in _ACTION_GROUPS for group in groups):
            raise ValueError("choose native diplomatic-action groups: "+", ".join(_ACTION_GROUPS))
        if target is None:raise ValueError("choose an explicit diplomatic target country")
        async def work():
            target_id=await resolve_country_id(self._ctx,target)
            root=await binding(self._ctx,"Country",target_id)
            context=await snapshot(self,"diplomacy.action_context",_CONTEXT,((root,),),perform=False)
            sources={};rows=[]
            for group in groups:
                source="Country.Get"+_SOURCE_SCOPES[scope]+_ACTION_GROUPS[group]+"DiploActions"
                copied=await project(self,"diplomacy.action_options."+group,DIPLOMATIC_ACTION_FIELDS,
                    ((root,),),source_expression=source,perform=False)
                sources[group]=copied
                for original in copied["rows"]:
                    row=dict(original)
                    row.update(category=group,source_expression=source,source_scope=scope,
                        action=original.get("native_key"),available=True if scope=="available" else None,
                        availability_source="native available collection" if scope=="available" else "not validated",
                        active=original.get("active_pact"),pending=None,
                        cost=original.get("maintenance_cost"),cost_kind="native maintenance cost")
                    rows.append(row)
            complete=context["complete"] and all(source["complete"] for source in sources.values())
            return {"country":{"requested_id":target_id,"id":context.get("target_country_id"),
                        "name":context.get("target_name")},
                "target_country_id":target_id,"actions":rows,"sources":sources,"context":context,
                "complete":complete,"status":"observed" if complete else "partial",
                "complete_native_action_catalog":False,
                "action_scope":"original country diplomatic-action collections: "+", ".join(groups),
                "source_scope":scope,"command_validation_performed":False,"atomic_snapshot":False,
                "formal_treaty_options":"treaty_options",
                "actor_source":"original native country-action providers; actual parties are copied in each row"}
        return await self._ctx.perform("diplomacy.diplomatic_options",work)
