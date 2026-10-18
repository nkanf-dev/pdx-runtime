"""Owned native military-formation rename context and original Confirm action.

The low-level factory consumes the caller run's retained CDataMilitaryFormation
handle.  It constructs and initializes the original GUI-null popup context, so
current flag and color are preserved by the game's own Confirm producer.  This
module never opens PopupManager UI and never constructs command fields itself.
"""
from __future__ import annotations

from ... import v3_data as data
from .._text import display_field
from ._warfare import KnownResultError, SourceUnavailable, _formation, owned_read
from .._run import KnownResultError, SourceUnavailable


async def formation_rename(api, formation_id, new_name, *, validate_only=False):
    """Rename one owned formation through the original initialized popup context.

    ``OnEditName`` requires an explicit native CUTF8String input.  Command
    production, validation/submission, and fresh name observation remain distinct.
    The native factory owns normal construction and tested fault cleanup; this
    high-level path adds no pointer, command, or context ownership state machine.
    """
    if not isinstance(new_name, str):
        raise TypeError("new_name must be a string")

    async def operation(run):
        kind, formation = await _formation(run, formation_id)
        if formation is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")

        context_result = await run.call("formation_rename.context", lambda:
            data.create_military_formation_name_context(
                run.native, formation.object, output_bytes=4096,
                adapter=run.adapters["data"], process=run.process))
        try:
            context_binding = run.owned_binding(context_result, "MilitaryFormationChangeNamePopup")
        except RuntimeError as error:
            raise SourceUnavailable("Formation rename factory did not return the exact owned popup context") from error

        # Native invocation/acceptance is separate from post-input copy errors.
        # Keep that original receipt and never replay the edit for a missing copy.
        edited = await run.capture("formation_rename.edit_name", lambda: data.invoke_action(
            run.native, "MilitaryFormationChangeNamePopup.OnEditName",
            data.TypedInput.utf8_string(new_name), (context_binding,), output_bytes=4096,
            adapter=run.adapters["data"], process=run.process))
        result = {"formation_id": formation_id, "kind": kind, "new_name": new_name,
                  "action": "rename_formation", "accepted": None,
                  "edit_action": edited,
                  "native_effect": {"edit_invoked": edited.invoked == 1,
                                    "edit_accepted": edited.accepted == 1,
                                    "producer": "MilitaryFormationChangeNamePopup.Confirm",
                                    "command_accepted": None}}
        if edited.invoked != 1 or edited.accepted != 1:
            result.update(status="native_result" if edited.error else "edit_rejected",
                          native_error=edited.error, native_result=edited, eligible=None,
                          reason="Native OnEditName action was not accepted")
            return result

        try:
            owner = await run.generated_owner(
                "MilitaryFormationChangeNamePopup.Confirm", (context_binding,))
            submitted = await run.validate_submit(owner, result, validate_only=validate_only)
        except (SourceUnavailable, KnownResultError, LookupError) as error:
            result.update(status="unavailable", reason=str(error))
            if isinstance(error, KnownResultError):
                result.update(status="native_result", native_result=error.result,
                              native_error=error.result.error)
            return result
        except BaseException as error:
            error.formation_rename_result = result
            raise
        result["native_effect"]["command_accepted"] = result.get("accepted")
        if submitted:
            try:
                _, current = await _formation(run, formation_id)
                if current is None:
                    raise SourceUnavailable("Formation left the current owned sources after submission")
                fields = await run.functions("MilitaryFormation")
                getter = "GetNameNoFormatting" if "GetNameNoFormatting" in fields else "GetName"
                observed, reason = await run.field("MilitaryFormation", getter, (current,),
                    (data.Kind.STRING,))
                result["name_readback"] = {**display_field(observed), "unavailable": {}}
                if reason:
                    result["name_readback"]["unavailable"]["name"] = reason
                result["observed_name_matches"] = (
                    observed == new_name if observed is not None and reason is None else None)
                result["native_effect"]["observed_name_raw"] = observed
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                result["observation_error"] = str(error)
                if isinstance(error, KnownResultError):
                    result["observation_native_result"] = error.result
                    result["observation_native_error"] = error.result.error
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
                if getattr(error, "query", None) is not None:
                    result["observation_query"] = error.query
        return result

    return await owned_read(api, "military.formation_rename", operation)
