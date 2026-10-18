"""Owned native naval design contexts and original GUI producers.

Ship rename consumes the original retained Ship handle through the exact
owned-context factory. The returned Any header and adopted handle bind the
GUI-null CShipChangeNamePopup context without decoding a host pointer. The
original OnEditName and Confirm paths edit the draft and produce the command.
No popup manager state, source ordinal, raw pointer retag, or synthetic command
fields are used here.
"""
from __future__ import annotations

from ... import v3_data as data
from .._text import display_field
from ._warfare import KnownResultError, SourceUnavailable, owned_read
from .ships import _ship
from .._run import KnownResultError, SourceUnavailable


SHIP_RENAME_METADATA = {
    "gui_type": "ShipChangeNamePopup",
    "native_type": "CShipChangeNamePopup",
    "native_command": "CShipChangeNameCommand",
    "source": "ShipList.GetShips/full Ship ID",
    "factory_input": "owned CPdxPtr<CDataShip> value handle",
    "methods": {"OnEditName": 1, "Confirm": 0},
    "gui_max_characters": 50,
}


async def ship_rename(api, ship_id, new_name, *, validate_only=False):
    """Rename one owned ship through the original initialized popup context.

    The UI edit-box limit is reported as metadata but is not reimplemented as a
    client policy. The original OnEditName and Confirm paths retain final native
    validation authority.
    """
    if not isinstance(new_name, str):
        raise TypeError("new_name must be a string")

    async def operation(run):
        source_index, ship = await _ship(run, ship_id)
        context_result = await run.call("ship_rename.context", lambda: data.create_ship_name_context(
            run.native, ship.object, output_bytes=4096,
            adapter=run.adapters["data"], process=run.process))
        try:
            context_binding = run.owned_binding(context_result, "ShipChangeNamePopup")
        except RuntimeError as error:
            raise SourceUnavailable(
                "Ship rename factory did not return the exact owned popup context") from error

        # The proved factory owns source conversion and initialization. Do not
        # re-read GetShip or metadata as a second identity/permission seal.
        edited = await run.capture("ship_rename.edit_name", lambda: data.invoke_action(
            run.native, "ShipChangeNamePopup.OnEditName",
            data.TypedInput.utf8_string(new_name), (context_binding,), output_bytes=4096,
            adapter=run.adapters["data"], process=run.process))
        result = {
            "ship_id": ship_id,
            "source_index": source_index,
            "identity_scope": "native_full_ship_id",
            "new_name": new_name,
            "action": "rename_ship",
            "accepted": None,
            "edit_action": edited,
            "native_metadata": dict(SHIP_RENAME_METADATA),
            "native_effect": {
                "context_type": "ShipChangeNamePopup",
                "edit_invoked": edited.invoked,
                "edit_accepted": edited.accepted,
                "edit_error": edited.error,
                "producer": "ShipChangeNamePopup.Confirm",
                "command_accepted": None,
            },
        }
        if edited.invoked != 1 or edited.accepted != 1:
            result.update(
                status="native_result" if edited.error else "edit_rejected",
                native_error=edited.error, native_result=edited, eligible=None,
                reason="Native ShipChangeNamePopup.OnEditName action was not accepted")
            return result

        try:
            owner = await run.generated_owner(
                "ShipChangeNamePopup.Confirm", (context_binding,))
            submitted = await run.validate_submit(owner, result, validate_only=validate_only)
        except (SourceUnavailable, KnownResultError, LookupError) as error:
            result.update(status="unavailable", reason=str(error))
            if isinstance(error, KnownResultError):
                result.update(status="native_result", native_result=error.result,
                              native_error=error.result.error)
            return result
        except BaseException as error:
            error.ship_rename_result = result
            raise
        result["native_effect"]["command_accepted"] = result.get("accepted")
        if submitted:
            result["observed_name_matches"] = None
            try:
                _, current_ship = await _ship(run, ship_id)
                observed, reason = await run.field(
                    "Ship", "GetNameNoFormatting", (current_ship,),
                    (data.Kind.STRING,))
                result["name_readback"] = {
                    **display_field(observed), "unavailable": {}}
                if reason:
                    result["name_readback"]["unavailable"]["name"] = reason
                result["observed_name_matches"] = (
                    observed == new_name
                    if isinstance(observed, str) and reason is None else None)
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

    return await owned_read(api, "military.ship_rename", operation)
