"""Read-only V3 UI Data-source projections over an existing query context.

This module does not open a session, alter UI state, or install agent policy.
Each field uses a copied-root, fresh-list-element, or profiled original
embedded-subobject projection. Source, getter and cleanup share one native
Interface/Access callback. Only copied
scalars/UTF-8 bytes cross that boundary. Different fields are NOT atomic and a
provider index is NOT an identity. No polling history, stable event stream, or
coverage of every UI surface is claimed. See ``source_catalog()`` for gaps.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
from pathlib import Path
from time import monotonic_ns

from . import v3_data as data
from . import v3_ui_map as map_data
from ._sampling import QuerySamplingControl, SamplingStopped, PageCursor, RoundRobinProgress


@dataclass(frozen=True)
class _Source:
    name: str
    root: str | None
    kind: str
    fields: tuple[tuple[str, str], ...] = ()
    actions: tuple[tuple[str, str], ...] = ()
    gap: str | None = None


_SOURCES = (
    _Source("message_feed", "MessageFeedHandler.GetItems", "message", (
        ("title", "FeedMessageItem.GetPlayerMessageItem.GetName"),
        ("description", "FeedMessageItem.GetPlayerMessageItem.GetDescription"),
        ("date", "FeedMessageItem.GetPlayerMessageItem.GetDate"),
        ("message_group_tag", "FeedMessageItem.GetPlayerMessageItem.GetMessageGroupTag"),
        ("is_set_to_delete", "FeedMessageItem.IsSetToDelete"),
        ("is_expanded", "FeedMessageItem.IsExpanded")), (
        ("expand", "FeedMessageItem.Expand"),
        ("mark_to_delete", "FeedMessageItem.MarkToDelete"))),
    _Source("toast", "ToastMessageHandler.GetMessageItem", "toast", (
        ("title", "ToastMessageHandler.GetMessageItem.GetName"),
        ("description", "ToastMessageHandler.GetMessageItem.GetDescription"),
        ("message_group_tag", "ToastMessageHandler.GetMessageItem.GetMessageGroupTag")), (
        ("dismiss", "ToastMessageHandler.DismissMessage"),)),
    _Source("message_popups", "PopupManager.Self", "message_popup", (
        ("title", "PlayerMessageItem.GetName"),
        ("description", "PlayerMessageItem.GetDescription"),
        ("date", "PlayerMessageItem.GetDate"),
        ("message_group_tag", "PlayerMessageItem.GetMessageGroupTag")), (
        ("close", "PopupManager.CloseMessage(PlayerMessageItem.AccessSelf)"),)),
    _Source("alerts", "TopFrontend.AccessAlerts", "alert", (
        ("title", "Alert.GetName"),
        ("tooltip", "Alert.GetToolTip"),
        ("is_read", "Alert.IsRead"),
        ("is_adding", "Alert.IsAdding"),
        ("is_added", "Alert.IsAdded"),
        ("is_removal_requested", "Alert.IsRemovalRequested")), (
        ("open", "PopupManager.OpenAlert(Alert.AccessSelf)"),
        ("mark_as_hidden", "Alert.MarkAsHidden"))),
    _Source("important_alerts", "TopFrontend.AccessImportantActions", "important_alert", (
        ("title", "ImportantAlert.GetName"),
        ("tooltip", "ImportantAlert.GetToolTip"),
        ("is_read", "ImportantAlert.IsRead"),
        ("has_group", "ImportantAlert.HasGroup"),
        ("num_alerts", "ImportantAlert.GetNumAlerts"),
        ("is_group_expanded", "ImportantAlert.IsAlertGroupExpanded")), (
        ("mark_as_read", "ImportantAlert.MarkAsRead"),
        ("mark_as_hidden", "ImportantAlert.MarkAsHidden"),
        ("toggle_group", "ImportantAlert.ToggleAlertGroupExpanded"))),
    _Source("grouped_important_alert_children", "TopFrontend.AccessImportantActions", "grouped_alert_child", (
        ("title", "Alert.GetName"),
        ("tooltip", "Alert.GetToolTip"),
        ("is_read", "Alert.IsRead"),
        ("is_adding", "Alert.IsAdding"),
        ("is_added", "Alert.IsAdded"),
        ("is_removal_requested", "Alert.IsRemovalRequested")), (
        ("open", "PopupManager.OpenAlert(Alert.AccessSelf)"),
        ("mark_as_hidden", "Alert.MarkAsHidden"))),
    _Source("diplomatic_proposals", "TopFrontend.AccessProposals", "diplomatic_proposal", (
        ("tooltip", "Proposal.GetToolTip"),
        ("texture", "Proposal.GetTexture"),
        ("actor_name", "Proposal.GetActor.GetName"),
        ("recipient_name", "Proposal.GetRecipient.GetName")), (
        ("open", "PopupManager.OpenProposal(Proposal.AccessSelf)"),
        ("hide", "PopupManager.HideProposal(Proposal.AccessSelf)"),
        ("decline", "PopupManager.DeclineProposal(Proposal.AccessSelf)"))),
    _Source("hidden_diplomatic_proposals", "TopFrontend.AccessHiddenProposals", "diplomatic_proposal", (
        ("tooltip", "Proposal.GetToolTip"),
        ("texture", "Proposal.GetTexture"),
        ("actor_name", "Proposal.GetActor.GetName"),
        ("recipient_name", "Proposal.GetRecipient.GetName")), (
        ("open", "PopupManager.OpenProposal(Proposal.AccessSelf)"),
        ("decline", "PopupManager.DeclineProposal(Proposal.AccessSelf)"))),
    _Source("outliner_wars", "Outliner.AccessCategory('wars').GetSubEntries", "outliner_attention", (
        ("title", "OutlinerEntry.GetTitle"),
        ("war_name", "OutlinerEntry.AccessScope.AccessWar.GetName"),
        ("is_shown", "OutlinerEntry.IsShown"),
        ("is_pinned", "OutlinerEntry.IsPinned"),
        ("is_expanded", "OutlinerEntry.IsExpanded"))),
    _Source("outliner_diplomatic_plays", "Outliner.AccessCategory('diplo_plays').GetSubEntries", "outliner_attention", (
        ("title", "OutlinerEntry.GetTitle"),
        ("play_name", "OutlinerEntry.AccessScope.AccessDiplomaticPlay.GetName"),
        ("escalation", "OutlinerEntry.AccessScope.AccessDiplomaticPlay.GetEscalation"),
        ("is_shown", "OutlinerEntry.IsShown"),
        ("is_pinned", "OutlinerEntry.IsPinned"),
        ("is_expanded", "OutlinerEntry.IsExpanded"))),
    _Source("outliner_armies", "Outliner.AccessCategory('army').GetSubEntries", "outliner_attention", (
        ("title", "OutlinerEntry.GetTitle"),
        ("is_shown", "OutlinerEntry.IsShown"),
        ("is_mobilizing", "OutlinerEntry.AccessScope.AccessMilitaryFormation.IsMobilizing"),
        ("is_fully_mobilized", "OutlinerEntry.AccessScope.AccessMilitaryFormation.IsFullyMobilized"))),
    _Source("outliner_navies", "Outliner.AccessCategory('navy').GetSubEntries", "outliner_attention", (
        ("title", "OutlinerEntry.GetTitle"),
        ("is_shown", "OutlinerEntry.IsShown"),
        ("has_naval_mission", "OutlinerEntry.AccessScope.AccessMilitaryFormation.HasNavalMission"),
        ("has_invasion", "OutlinerEntry.AccessScope.AccessMilitaryFormation.HasInvasion"),
        ("is_about_to_transport_armies", "OutlinerEntry.AccessScope.AccessMilitaryFormation.IsAboutToTransportArmies"))),
    _Source("outliner_diplomatic_pacts", "Outliner.AccessCategory('diplo_pacts').GetSubEntries", "outliner_attention", (
        ("title", "OutlinerEntry.GetTitle"),
        ("tooltip", "OutlinerEntry.AccessScope.AccessDiplomaticPact.GetTooltip"),
        ("effect_description", "OutlinerEntry.AccessScope.AccessDiplomaticPact.GetOutlinerEffectDesc"),
        ("is_shown", "OutlinerEntry.IsShown"))),
    _Source("jomini_notifications", None, "jomini_notification", (
        ("title", "JominiNotification.GetTitle"),
        ("text", "JominiNotification.GetText"),
        ("accept_label", "JominiNotification.GetAcceptButtonText"),
        ("decline_label", "JominiNotification.GetDeclineButtonText"),
        ("has_accept", "JominiNotification.HasOnAccept"),
        ("has_decline", "JominiNotification.HasOnDecline"))),
    _Source("general_information_dialogs", "PopupManager.Self", "general_information", (
        ("title", "GeneralInformationWindow.GetTitle"),
        ("text", "GeneralInformationWindow.GetText"))),
    _Source("confirmations", "PopupManager.Self", "confirmation", (
        ("description", "ConfirmationWindow.GetDesc"),)),
    _Source("map_notifications", None, "map_notification", (
        ("text", "MapNotification.GetText"),)),
)
SOURCE_IDS = tuple(source.name for source in _SOURCES)
_BY_NAME = {source.name: source for source in _SOURCES}

_COVERAGE_GAPS = (
    "This is current UI-source polling, not a native message history or durable queue.",
    "Transient items can appear and expire between reads; no native cursor is proven.",
    "Stable native item IDs and cross-field identity are not proven; indices are ordinals only.",
    "UI source membership does not prove actual rendered pixels, active wars, or player participation.",
    "Additional outliner categories and arbitrary dialog surfaces are not all enumerated.",
    "Nested alert children are fresh ordinal paths, never stable parent/child identities.",
)


# Static profile: Victoria3 v1.13.11, exact executable fingerprint. The file
# hash AND mapped immutable code anchors are checked before any layout read.
# The engine itself proves this ORIGINAL embedded type: CMessagePopup::Show
# passes window+0x88 and the same dynamic PlayerMessageItem type-id global as
# registration into SetDataContext. No pointer field, retag or CPdxPtr is made.
_POPUP_PROFILE = {
    "sha256": "7f53e3260cf46b6247f6906837464944038c4f74e860274ad3d394e0c04f811f",
    "array_offset": 0x40, "subobject_offset": 0x88, "element_vtable_rva": 0x45c54e8,
    "anchors": (
        (0x21c6b6a, bytes.fromhex("488d71404c8bea4863560c33db85d2")),
        (0x21cffc1, bytes.fromhex("488d8e88000000498bd4e8a09a2100904989")),
        (0x21b6917, bytes.fromhex("8b055b5f7b034c8d83880000004c8d4c24408944244033d2c644242001488bcfe8d4934301488b")),
        (0x21b683d, bytes.fromhex("8bda4881c188000000e835000000488bcfe8")),
    ),
}


def _popup_profile(ctx):
    """Check original module file/hash and immutable code, never a UI object."""
    base = ctx.image_base
    module = next((m for m in ctx.process.modules() if m["base"] == base), None)
    if module is None or module["name"].lower() != "victoria3.exe":
        return {"supported": False, "reason": "The original Victoria3 main module path is unavailable."}
    path = Path(module["path"])
    stat = path.stat()
    cache = getattr(ctx, "_ui_popup_profile_cache", {})
    key = (base, str(path), stat.st_size, stat.st_mtime_ns)
    digest = cache.get(key)
    if digest is None:
        with path.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        ctx._ui_popup_profile_cache = {key: digest}
    if digest != _POPUP_PROFILE["sha256"]:
        return {"supported": False, "reason": "No message-popup source profile matches this executable.", "sha256": digest}
    for rva, expected in _POPUP_PROFILE["anchors"]:
        if ctx.process.read(base + rva, len(expected)) != expected:
            return {"supported": False, "reason": "The mapped message-popup code does not match the proven source profile."}
    return {"supported": True, "sha256": digest, "mapped_code_anchors": True,
            "source_scope": "current native message-popup windows, not a historical feed"}


def source_catalog():
    """Return copied static capability descriptions, not native-read success."""
    return [{"source": source.name, "root_expression": source.root,
             "reader": ("copied_root_projection" if source.name == "toast" else
                        "profiled_embedded_subobject_projection" if source.name == "message_popups" else
                        "profiled_native_snapshot" if source.name in ("map_notifications", "jomini_notifications", "general_information_dialogs", "confirmations") else
                        "fresh_scoped_creation_path_projection" if source.name == "grouped_important_alert_children" else
                        "fresh_list_element_projection" if source.fields else None),
             "fields": dict(source.fields), "reason": source.gap,
             "native_validation": "NOT RUN"} for source in _SOURCES]


def _field(result, expression, sequence, started, finished):
    raw = result.result
    error = result.error
    kind = int(raw.kind)
    record = {"value": None, "raw_text": None, "raw_bytes": None,
              "complete": False, "reason": None, "error": error,
              "expression": expression, "read_sequence": sequence,
              "read_started_monotonic_ns": started, "read_finished_monotonic_ns": finished,
              "native_kind": kind, "copied_bytes": raw.snapshot_bytes,
              "required_bytes": raw.total}
    # The SDK decoder enforces this too. Do not accept a provider substitute
    # that accidentally returns an owned handle or any borrowed native pointer.
    if any(getattr(raw, name, 0) for name in
           ("factory", "object", "value_handle", "any_type", "any_vtable", "payload_address")) or any(getattr(raw, "raw_any", b"")):
        record["reason"] = "The result crossed the copied-only boundary with a native pointer."
        return record
    if not data.Kind.EMPTY <= kind <= data.Kind.STRING:
        record["reason"] = "The game did not return a copied primitive or string."
        return record
    if kind == data.Kind.STRING:
        copied = bytes(raw.snapshot)
        record["raw_bytes"] = copied
        try:
            record["raw_text"] = copied.decode("utf-8")
        except UnicodeDecodeError:
            record["reason"] = "The copied bytes are not valid UTF-8; raw_bytes is preserved."
        if len(copied) != raw.snapshot_bytes or raw.snapshot_bytes != raw.total:
            record["reason"] = record["reason"] or "Only part of the native text was copied."
    if error:
        record["reason"] = record["reason"] or "Native UI projection error " + str(error) + "."
    if record["reason"] is not None:
        return record
    try:
        value = result.value
    except (TypeError, ValueError) as failure:
        record["reason"] = "The copied native value could not be decoded: " + str(failure)
        return record
    if value is None:
        record["reason"] = "The game returned an empty native value."
        return record
    if isinstance(value, data.FixedPoint):
        value = {"raw_bits": value.bits, "signed_raw": value.signed}
    elif type(value) not in (str, bool, int, float):
        record["reason"] = "The decoded value is not a copied scalar."
        return record
    record.update(value=value, complete=True)
    return record


def _source_record(name, world):
    source = _BY_NAME.get(name)
    return {"source": name, "status": "unavailable", "reason": None,
            "root_expression": source.root if source else None,
            "world": world, "atomic_snapshot": False, "count": None,
            "observed_source_counts": [], "coverage": list(_COVERAGE_GAPS),
            "history_available": False, "native_cursor_available": False,
            "stable_identity_available": False}


async def _read_dialog_source(ctx, source, description, output, observations, progress,
                              query, *, world, max_items, output_bytes):
    # This adapter resolves the original current owner only inside one Access
    # callback. Its profile/VM ABI is not a shared source-mechanism assumption.
    from . import v3_ui_dialogs as dialogs
    from .target_descriptor import TargetMismatch, UnsupportedBuild
    start_index = progress.positions.get(source.name, 0)
    item_limit = min(max_items, dialogs.MAX_ITEMS)
    text_limit = min(output_bytes, dialogs.MAX_TEXT)
    description.update(adapter_item_limit=item_limit, adapter_text_capacity=text_limit)
    try:
        result, sequence, started, finished = await query("ui." + source.name + ".snapshot", lambda:
            dialogs.snapshot(ctx.native, source=source.name, process=ctx.process,
                image_base=ctx.image_base, world=world, interface_thread_id=ctx.interface_thread_id,
                max_items=item_limit, text_capacity=text_limit, start_index=start_index,
                adapter=ctx.adapters["data"]))
    except (TargetMismatch, UnsupportedBuild) as failure:
        if getattr(ctx, "pending_queries", ()):
            raise
        description["reason"] = "No verified current dialog-source profile: " + str(failure)
        return
    description["count_observation"] = {"source_produced": bool(result.source_produced),
        "count_valid": bool(result.count_valid), "error": result.error,
        "read_sequence": sequence, "read_started_monotonic_ns": started,
        "read_finished_monotonic_ns": finished}
    if ctx.world != world or result.source_world != world:
        description.update(status="partial", reason="The context world changed during native dialog observation.")
        return
    if not result.source_produced or not result.count_valid:
        description["reason"] = "The native dialog source/count was not produced (error " + str(result.error) + ")."
        return
    description["count"] = result.source_count
    observations.append(result.source_count)
    records_complete = True
    for record in result.records:
        fields = {}
        for field in record.fields:
            value = field.value
            raw_bytes = bytes(field.raw_bytes) if int(field.kind) == int(data.Kind.STRING) else None
            raw_text = field.raw_text if raw_bytes is not None else None
            reason = ("Native dialog field error " + str(field.error) + "." if field.error else
                "Only part of the native dialog field was copied." if not field.complete else
                "The copied native text is not valid UTF-8." if raw_bytes is not None and raw_text is None else
                "The native dialog field is unavailable as a copied scalar." if value is None else None)
            fields[field.name] = {"value": value if reason is None else None,
                "raw_text": raw_text, "raw_bytes": raw_bytes,
                "complete": reason is None, "reason": reason, "error": field.error,
                "expression": field.expression, "read_sequence": sequence,
                "read_started_monotonic_ns": started, "read_finished_monotonic_ns": finished,
                "native_kind": int(field.kind), "copied_bytes": field.text_bytes_copied,
                "required_bytes": field.text_bytes_required}
        metadata = {key: bool(getattr(record, key)) if record.metadata_valid & bit else None
            for key, bit in (("consumed", 1), ("has_dialog", 2), ("has_widget", 4))}
        receipt = {"source": source.name, "kind": source.kind, "provenance": "native_ui_data",
            "world": world, "source_index": record.source_index, "native_identity": None,
            "fields": fields, "raw_text": {key: field["raw_text"] for key, field in fields.items()
                if field["raw_bytes"] is not None}, "source_metadata": metadata,
            "visibility": {"rendered": None, "reason": "Original ownership/widget existence is not rendered visibility."},
            "observations_are_atomic": False,
            "identity_uncertainty": "Snapshot ordinals are not stable identities between callbacks.",
            "actions": []}
        output["receipts"].append(receipt)
        records_complete = records_complete and not record.error and len(fields) == len(source.fields) and all(field["complete"] for field in fields.values())
    next_index = result.records[-1].source_index + 1 if result.records else 0
    progress.positions[source.name] = next_index if next_index < result.source_count else 0
    description.update(sampled_start_index=start_index, sampled_next_index=progress.positions[source.name],
        text_bytes_copied=result.text_bytes_copied, text_bytes_required=result.text_bytes_required,
        truncated=bool(result.truncated))
    full = not result.error and not result.truncated and start_index == 0 and result.returned_count == result.source_count and records_complete
    description["status"] = "available" if full else "partial"
    if not full:
        description["reason"] = "Only a bounded or partial current dialog-source snapshot was copied; unsampled state is unknown."


async def _read_grouped_alert_children(ctx, source, description, output, observations,
                                       progress, query, *, world, max_items, output_bytes):
    """Copy one fresh nested leaf/count path per slot; keep ordinal work only."""
    cursor = progress.positions.get(source.name, (0, 0))
    if not isinstance(cursor, tuple) or len(cursor) != 2:
        cursor = (0, 0)
    parent_index, slot = cursor
    started_at_origin = cursor == (0, 0)
    width = len(source.fields)
    parent_count = None
    children_total = counted_parents = 0
    receipts = []
    stopped = False
    all_fields_complete = True
    description.update(status="partial", count=None, parent_source_count=None,
                       sampled_start_path={"parent_index": parent_index, "item_field_slot": slot})
    while counted_parents < max_items and len(receipts) < max_items:
        child_count = None
        index, field_start = divmod(slot, width)
        parent_complete = False
        while len(receipts) < max_items:
            fields = {}
            for field_offset, (field_name, expression) in enumerate(source.fields[field_start:], field_start):
                try:
                    result, seq, start, finish = await query(
                        "ui." + source.name + "." + str(parent_index) + "." + str(index) + "." + field_name,
                        lambda expression=expression, index=index: data.evaluate_scoped(ctx.native,
                            (data.CreationStep(source.root, parent_index, parent_count),
                             data.CreationStep("ImportantAlert.GetAlerts", index, child_count)),
                            expression, world=world, output_bytes=output_bytes,
                            adapter=ctx.adapters["data"], process=ctx.process))
                except SamplingStopped as failure:
                    if getattr(ctx, "pending_queries", ()):
                        raise
                    description["reason"] = str(failure)
                    stopped = True
                    break
                # Counts are returned by THIS leaf callback even on an absent
                # element. A separate count query would waste a one-slot quota.
                # Never accept an empty-source receipt after a world transition.
                if ctx.world != world:
                    description["reason"] = "The context world changed during nested UI observation."
                    stopped = True
                    break
                outer = result.steps[0] if len(result.steps) == 2 else None
                inner = result.steps[1] if len(result.steps) == 2 else None
                description["count_observation"] = {
                    "scalar_produced": bool(result.source_produced), "error": result.error,
                    "path_counts": [{"source_produced": bool(step.source_produced),
                        "count_valid": bool(step.count_valid), "count": step.source_count}
                        for step in result.steps],
                    "read_sequence": seq, "read_started_monotonic_ns": start,
                    "read_finished_monotonic_ns": finish}
                if outer is None or not outer.source_produced or not outer.count_valid:
                    description["reason"] = "The native outer alert source/count was not produced."
                    stopped = True
                    break
                if parent_count is not None and outer.source_count != parent_count:
                    description["reason"] = "The fresh parent source count changed during nested UI observation."
                    stopped = True
                    break
                parent_count = outer.source_count
                description["parent_source_count"] = parent_count
                if not parent_count:
                    observations.append(0)
                    description.update(status="available", count=0)
                    progress.positions[source.name] = (0, 0)
                    return
                if parent_index >= parent_count:
                    description["reason"] = "The fresh parent ordinal is out of range; the next sample starts at zero."
                    parent_index = slot = 0
                    stopped = True
                    break
                if inner is None or not inner.source_produced or not inner.count_valid:
                    description["reason"] = "The native child source/count was not produced."
                    stopped = True
                    break
                if child_count is not None and inner.source_count != child_count:
                    description["reason"] = "The fresh child source count changed during nested UI observation."
                    stopped = True
                    break
                if child_count is None:
                    child_count = inner.source_count
                    observations.append(child_count)
                    children_total += child_count
                    counted_parents += 1
                if not child_count:
                    parent_complete = True
                    break
                if index >= child_count:
                    description["reason"] = "The fresh child ordinal is out of range; the next sample restarts this parent."
                    slot = 0
                    stopped = True
                    break
                fields[field_name] = _field(result, expression, seq, start, finish)
                slot = index * width + field_offset + 1
            field_start = 0
            if fields:
                receipt = {"source": source.name, "kind": source.kind, "provenance": "native_ui_data",
                    "world": world, "source_index": index, "parent_source_index": parent_index,
                    "source_path": [{"expression": source.root, "index": parent_index},
                                    {"expression": "ImportantAlert.GetAlerts", "index": index}],
                    "native_identity": None, "fields": fields,
                    "raw_text": {key: value["raw_text"] for key, value in fields.items() if value["raw_bytes"] is not None},
                    "visibility": {"rendered": None, "reason": "Native child-source membership, not pixels or group expansion."},
                    "observations_are_atomic": False,
                    "identity_uncertainty": "Parent and child ordinals can change between callbacks even if both counts match.",
                    "actions": [{"name": action, "expression": expression, "enabled": None,
                        "reason": "GUI declaration only; no original parent/child identity or action permission is retained."}
                        for action, expression in source.actions]}
                receipts.append(receipt)
                output["receipts"].append(receipt)
                all_fields_complete = all_fields_complete and len(fields) == width and all(field["complete"] for field in fields.values())
            if stopped or parent_complete:
                break
            index += 1
            if child_count is not None and index >= child_count:
                parent_complete = True
                break
        if stopped or not parent_complete:
            break
        parent_index += 1
        slot = 0
        if parent_count is not None and parent_index >= parent_count:
            break
    complete_pass = parent_count is not None and parent_index >= parent_count
    progress.positions[source.name] = (0, 0) if complete_pass else (parent_index, slot)
    description["sampled_next_path"] = {"parent_index": progress.positions[source.name][0],
                                        "item_field_slot": progress.positions[source.name][1]}
    if complete_pass and started_at_origin:
        description["count"] = children_total
    if complete_pass and started_at_origin and all_fields_complete and not stopped:
        description["status"] = "available"
    elif description["reason"] is None:
        description["reason"] = "Only a bounded fresh nested ordinal slice was read; unsampled children are unknown."


async def observe(ctx, *, sources=None, max_items=64, output_bytes=16384,
                  max_queries=128, should_stop=None, priority_sources=()):
    """Copy UI sources through the supplied original context, without UI actions.

    All roots/counts/elements and their cleanup stay in their own native
    callback. This function opens no session and does not refresh/pause/replay.
    Pending callbacks stay with ctx.query. Bounds limit only this reader, not
    the unrestricted raw SDK. An older DLL without actions17/18 is unavailable,
    never bypassed with retained borrowed UI pointers.
    """
    if type(max_items) is not int or max_items < 1:
        raise ValueError("max_items must be a positive integer")
    if type(output_bytes) is not int or output_bytes < 1:
        raise ValueError("output_bytes must be a positive integer")
    if type(max_queries) is not int or max_queries < 1:
        raise ValueError("max_queries must be a positive integer")
    if should_stop is not None and not callable(should_stop):
        raise TypeError("should_stop must be a callable or None")
    selected = SOURCE_IDS if sources is None else (sources,) if isinstance(sources, str) else tuple(sources)
    if any(not isinstance(name, str) for name in selected):
        raise TypeError("sources must contain source names")
    selected = tuple(dict.fromkeys(selected))
    priorities = (priority_sources,) if isinstance(priority_sources, str) else tuple(priority_sources)
    if any(not isinstance(name, str) for name in priorities) or set(priorities).difference(selected):
        raise ValueError("priority_sources must be selected source names")
    world = ctx.world
    if type(world) is not int or not 0 < world <= 0xffffffffffffffff:
        raise ValueError("a current original context world is required")
    output = {"world": world, "atomic_snapshot": False, "sources": [], "receipts": [],
              "read_started_monotonic_ns": monotonic_ns(),
              "history_available": False, "native_cursor": None,
              "query_budget": max_queries, "query_count": 0, "sampling_interrupted": False}
    sequence = 0

    async_control = should_stop is not None and (inspect.iscoroutinefunction(should_stop)
        or inspect.iscoroutinefunction(getattr(should_stop, "__call__", None)))
    pending_control = None

    def external_stop():
        nonlocal async_control, pending_control
        if should_stop is None or async_control:
            return None
        control = should_stop()
        if inspect.isawaitable(control):
            # Detect an ordinary callable that returns an awaitable ONCE.
            # Hand this exact awaitable to the next checkpoint; do not close,
            # duplicate or silently ignore its policy result.
            async_control = True
            pending_control = control
            return None
        if control is True or isinstance(control, str) and control:
            output["sampling_interrupted"] = True
            return control if isinstance(control, str) else "UI sampling was interrupted before a new native query; unsampled state is unknown."
        if control not in (None, False):
            raise TypeError("should_stop must return a reason, bool or None")
        return None

    async def checkpoint():
        nonlocal pending_control
        if should_stop is None or not async_control:
            return None, None
        control = pending_control if pending_control is not None else should_stop()
        pending_control = None
        if inspect.isawaitable(control):
            control = await control
        if control is True or isinstance(control, str) and control:
            output["sampling_interrupted"] = True
            return None, (control if isinstance(control, str) else
                "UI sampling was interrupted before a new native query; unsampled state is unknown.")
        if control not in (None, False):
            raise TypeError("should_stop must return a reason, bool or None")
        return None, None

    sampler = QuerySamplingControl(ctx.query, checkpoint, external_stop,
        query_limit=max_queries, checkpoint_limit=max_queries + len(selected) + 1)

    async def consume_pending_control():
        if pending_control is None:
            return None
        _, reason = await checkpoint()
        if reason:
            sampler.reason = reason
        return reason

    async def sampling_reason():
        reason = await sampler.check()
        pending_reason = await consume_pending_control()
        reason = pending_reason or reason
        return ("UI query budget was reached; unsampled state is unknown." if reason == "query_budget" else reason)

    async def query(label, factory):
        nonlocal sequence
        started = monotonic_ns()
        try:
            result = await sampler.query(label, factory)
        except Exception as failure:
            if getattr(failure, "sampling_stopped", False) and not getattr(ctx, "pending_queries", ()):
                sampler.reason = str(failure) or "UI sampling stopped before submission."
                raise SamplingStopped(sampler.reason) from failure
            raise
        finally:
            sequence = sampler.queries
            output["query_count"] = sequence
            # A callback may first return an awaitable at admission or the
            # post-settlement check. Settle that exact check once before a
            # budget stop could skip the next checkpoint. Never do this while
            # an original native owner is still pending.
            if not getattr(ctx, "pending_queries", ()):
                await consume_pending_control()
        return result, sequence, started, monotonic_ns()

    async def type_id(name):
        # Metadata discovery is part of this SAME budget/control flow. Only
        # copied name/id pairs are cached, never native descriptor pointers.
        cache = getattr(ctx, "_ui_type_cache", {})
        if cache.get("world") != world:
            cache = {"world": world, "types": {}, "page": PageCursor(world)}
            ctx._ui_type_cache = cache
        if name in cache["types"]:
            return cache["types"][name]
        page_cursor = cache.setdefault("page", PageCursor(world))
        if page_cursor.complete:
            raise LookupError("Native UI type is unavailable: " + name)
        cursor = page_cursor.next_index
        while True:
            page, _, _, _ = await query("ui.metadata.types", lambda:
                data.enumerate_types(ctx.native, start=cursor, capacity=128,
                    adapter=ctx.adapters["data"], process=ctx.process))
            if page.error:
                raise LookupError("Native UI type metadata is unavailable (error " + str(page.error) + ").")
            cache["types"].update((row.name, row.type_id) for row in page.rows)
            page_cursor.advance(page.next, complete=page.next <= cursor or page.next >= page.total)
            if name in cache["types"]:
                return cache["types"][name]
            if page_cursor.complete:
                raise LookupError("Native UI type is unavailable: " + name)
            cursor = page.next

    descriptions = {name: _source_record(name, world) for name in selected}
    output["sources"] = [descriptions[name] for name in selected]
    priority = ("message_popups", "toast", "alerts", "important_alerts", "outliner_wars",
                "outliner_diplomatic_plays", "outliner_armies", "outliner_navies",
                "outliner_diplomatic_pacts", "map_notifications", "message_feed")
    rank = {name: index for index, name in enumerate(priority)}
    order = sorted(selected, key=lambda name: rank.get(name, len(rank)))
    progress = getattr(ctx, "_ui_sampling_progress", None)
    if progress is None or progress.generation != world:
        progress = RoundRobinProgress(world)
        ctx._ui_sampling_progress = progress
        ctx._ui_last_sample = {}
    last_sample = ctx._ui_last_sample
    base_order = tuple(order)
    order = progress.plan(base_order, priority=priorities)
    for order_index, name in enumerate(order):
        description = descriptions[name]
        description.update(stale=True, last_sample_monotonic_ns=last_sample.get(name), attempt_age_rounds=progress.age(name))
        reason = await sampling_reason()
        if reason is not None:
            description.update(status="partial", reason=reason, sampled=False, sampling_status="not_sampled")
            continue
        # Rotate on the NEXT capture if this source spends the remaining budget.
        # Only copied ordinal progress is retained; no native source/row owner.
        progress.attempted(name, base_order, next_position=progress.positions.get(name, 0))
        description["sampled"] = True
        source = _BY_NAME.get(name)
        if source is None or source.gap:
            description["reason"] = source.gap if source else "Unknown UI source; no reader is installed."
            continue
        source_receipts = []
        observations = []
        stop = False
        expected_count = None
        try:
            if name in ("jomini_notifications", "general_information_dialogs", "confirmations"):
                await _read_dialog_source(ctx, source, description, output, observations, progress,
                    query, world=world, max_items=max_items, output_bytes=output_bytes)
                continue
            if name == "grouped_important_alert_children":
                await _read_grouped_alert_children(ctx, source, description, output, observations,
                    progress, query, world=world, max_items=max_items, output_bytes=output_bytes)
                continue
            if name == "map_notifications":
                # Same exact executable fingerprint as the popup profile; the
                # native map adapter ALSO checks its own centralized immutable
                # anchors/header before its original getter/access operation.
                profile = _popup_profile(ctx)
                profile = {**profile, "source_scope":"current native map-notification ownership, not pixels/history"}
                description["profile"] = profile
                if not profile["supported"]:
                    description["reason"] = profile["reason"].replace("message-popup", "UI-source")
                    continue
                mapped, seq, start, finish = await query("ui.map_notifications.snapshot", lambda:
                    map_data.snapshot(ctx.native, world=world, interface_thread_id=ctx.interface_thread_id,
                        max_items=max_items, text_capacity=output_bytes,
                        start_index=progress.positions.get(name, 0),
                        adapter=ctx.adapters.get("carrier", "pdx_runtime.dll"), process=ctx.process))
                description.update(single_callback=True, source_produced=bool(mapped.source_produced),
                    collection_valid=bool(mapped.collection_valid), byte_budget=mapped.text_capacity,
                    text_bytes_copied=mapped.text_bytes_copied, text_bytes_required=mapped.text_bytes_required,
                    text_required_covers_all_positions=mapped.returned_count == mapped.source_count,
                    item_budget=mapped.max_items, native_error=mapped.error)
                if not mapped.source_produced or not mapped.collection_valid or mapped.error not in (0, 234):
                    description["reason"] = "Native map-notification source is unavailable/unsupported (error " + str(mapped.error) + ")."
                    continue
                observations.append(mapped.source_count)
                description["count"] = mapped.source_count
                next_position = mapped.start_index + mapped.returned_count
                progress.positions[name] = next_position if next_position < mapped.source_count else 0
                description.update(sampled_start_index=mapped.start_index,
                    sampled_next_index=progress.positions[name], stale=False)
                last_sample[name] = finish
                for row in mapped.records:
                    reason = None
                    raw_text = row.raw_text
                    if row.error:
                        reason = "Native map text projection error " + str(row.error) + "."
                    if raw_text is None:
                        reason = "The copied native map bytes are not valid UTF-8; raw_bytes is preserved."
                    if not row.complete:
                        reason = reason or "Only part of the original map text was copied."
                    field = {"value":raw_text if row.complete and reason is None else None,
                        "raw_text":raw_text, "raw_bytes":row.raw_bytes, "complete":bool(row.complete and reason is None),
                        "reason":reason, "error":row.error, "copied_bytes":row.text_bytes_copied,
                        "required_bytes":row.text_bytes_required, "expression":"MapNotification.GetText",
                        "read_sequence":seq, "read_started_monotonic_ns":start,
                        "read_finished_monotonic_ns":finish, "native_kind":int(data.Kind.STRING)}
                    output["receipts"].append({"source":name, "kind":source.kind, "provenance":"native_ui_data",
                        "world":world, "source_index":row.source_index, "native_identity":None,
                        "fields":{"text":field}, "raw_text":{"text":raw_text}, "single_callback":True,
                        "observations_are_atomic":False,
                        "visibility":{"rendered":None, "reason":"Map ownership includes pending/fading items; lens/zoom/animation can hide pixels."},
                        "identity_uncertainty":"Array ordinal is not a durable event identity; no pointer is returned.", "actions":[]})
                description["status"] = "partial" if mapped.truncated or any(not row.complete or row.error or row.raw_text is None for row in mapped.records) else "available"
                if description["status"] == "partial":
                    description["reason"] = "The bounded map snapshot has truncated/unavailable text or positions."
                continue
            popup_type = None
            if name == "message_popups":
                profile = _popup_profile(ctx)
                description["profile"] = profile
                if not profile["supported"]:
                    description["reason"] = profile["reason"]
                    continue
                popup_type = await type_id("PlayerMessageItem")

            def popup_source(index=0, count=None):
                return data.PointerArraySource("PopupManager.Self", _POPUP_PROFILE["array_offset"],
                    popup_type, ctx.interface_thread_id, index=index, expected_count=count,
                    element_subobject_offset=_POPUP_PROFILE["subobject_offset"],
                    expected_element_vtable=ctx.image_base + _POPUP_PROFILE["element_vtable_rva"])

            if name == "toast":
                presence, seq, start, finish = await query("ui.toast.presence", lambda:
                    data.evaluate_copied(ctx.native, "ToastMessageHandler.HasMessage", world=world,
                        output_bytes=output_bytes, adapter=ctx.adapters["data"], process=ctx.process))
                flag = _field(presence, "ToastMessageHandler.HasMessage", seq, start, finish)
                description["presence"] = flag
                if not presence.source_produced or not flag["complete"] or type(flag["value"]) is not bool:
                    description["reason"] = flag["reason"] or "The adapter did not confirm copied-root capability."
                    continue
                expected_count = int(flag["value"])
                observations.append(expected_count)
            elif name == "message_popups":
                counted, seq, start, finish = await query("ui.message_popups.count", lambda:
                    data.pointer_array_count(ctx.native, popup_source(), world=world,
                        adapter=ctx.adapters["data"], process=ctx.process))
                description["count_observation"] = {"source_stages": counted.source_stages,
                    "error": counted.error, "read_sequence": seq,
                    "read_started_monotonic_ns": start, "read_finished_monotonic_ns": finish}
                if counted.error or counted.source_stages != 3 or counted.root_count is None:
                    description["reason"] = "Current native message-popup windows are unavailable (native error " + str(counted.error) + ")."
                    continue
                expected_count = counted.root_count
                observations.append(expected_count)
            else:
                counted, seq, start, finish = await query("ui." + name + ".count", lambda:
                    data.source_count(ctx.native, source.root, world=world,
                        adapter=ctx.adapters["data"], process=ctx.process))
                description["count_observation"] = {"source_produced": bool(counted.source_produced),
                    "count_valid": bool(counted.count_valid), "error": counted.error,
                    "read_sequence": seq, "read_started_monotonic_ns": start,
                    "read_finished_monotonic_ns": finish}
                if counted.error or not counted.source_produced or not counted.count_valid:
                    description["reason"] = "Fresh UI source production/count is unavailable (native error " + str(counted.error) + ")."
                    continue
                expected_count = counted.source_count
                observations.append(expected_count)
            description["count"] = expected_count
            if ctx.world != world:
                description["reason"] = "The context world changed during UI observation."
                continue
            width = len(source.fields)
            start_slot = progress.positions.get(name, 0)
            if start_slot >= expected_count * width:
                start_slot = 0
            start_index, start_field = divmod(start_slot, width)
            next_position = start_slot
            description.update(sampled_start_index=start_index, sampled_start_field=start_field)
            for index in range(start_index, min(expected_count, start_index + max_items)):
                fields = {}
                field_begin = start_field if index == start_index else 0
                for field_offset, (field_name, expression) in enumerate(source.fields[field_begin:], field_begin):
                    if name == "toast":
                        factory = lambda expression=expression: data.evaluate_copied(ctx.native,
                            expression, world=world, output_bytes=output_bytes,
                            adapter=ctx.adapters["data"], process=ctx.process)
                    elif name == "message_popups":
                        factory = lambda expression=expression, index=index: data.evaluate_pointer_array(
                            ctx.native, popup_source(index, expected_count), expression, world=world,
                            output_bytes=output_bytes, adapter=ctx.adapters["data"], process=ctx.process)
                    else:
                        factory = lambda expression=expression, index=index: data.evaluate_element(
                            ctx.native, source.root, index, expression, world=world,
                            expected_count=expected_count, output_bytes=output_bytes,
                            adapter=ctx.adapters["data"], process=ctx.process)
                    try:
                        result, seq, start, finish = await query("ui." + name + "." + str(index) + "." + field_name, factory)
                    except SamplingStopped as stopped:
                        if getattr(ctx, "pending_queries", ()):
                            raise
                        description["reason"] = str(stopped)
                        stop = True
                        break
                    fields[field_name] = _field(result, expression, seq, start, finish)
                    next_position = index * width + field_offset + 1
                    if name != "toast":
                        # Source count is re-observed within each field callback,
                        # but a stable count still cannot prove item identity.
                        count = result.root_count if name == "message_popups" else result.source_count
                        observations.append(count)
                        if count != expected_count:
                            fields[field_name].update(value=None, complete=False,
                                reason="The UI source count changed or was not produced for this field.")
                            description["reason"] = fields[field_name]["reason"]
                            stop = True
                    elif not result.source_produced:
                        fields[field_name].update(value=None, complete=False,
                            reason="The adapter did not confirm fresh scalar production.")
                    if ctx.world != world:
                        fields[field_name].update(value=None, complete=False,
                            reason="The context world changed during UI observation.")
                        description["reason"] = fields[field_name]["reason"]
                        stop = True
                    if stop:
                        break
                receipt = {"source": name, "kind": source.kind, "provenance": "native_ui_data",
                    "world": world, "source_index": index, "native_identity": None,
                    "fields": fields, "raw_text": {key: field["raw_text"] for key, field in fields.items()
                        if field["raw_bytes"] is not None},
                    "visibility": {"rendered": None, "reason": "UI-source membership is not a rendered-pixel observation."},
                    "observations_are_atomic": False,
                    "identity_uncertainty": "The same index/count can refer to different items between callbacks.",
                    "actions": [{"name": action, "expression": expression, "enabled": None,
                        "reason": "GUI declaration only; no original item identity or action permission is retained."}
                        for action, expression in source.actions]}
                if fields:
                    source_receipts.append(receipt)
                    output["receipts"].append(receipt)
                if stop:
                    break
                next_position = (index + 1) * width
            progress.positions[name] = next_position if next_position < expected_count * width else 0
            next_index, next_field = divmod(progress.positions[name], width)
            description.update(sampled_next_index=next_index, sampled_next_field=next_field)
            complete = all(len(receipt["fields"]) == len(source.fields) and
                all(field["complete"] for field in receipt["fields"].values()) for receipt in source_receipts)
            bounded = start_index != 0 or start_field != 0 or expected_count > start_index + max_items
            description["status"] = "available" if complete and not stop and not bounded else "partial"
            if bounded:
                description["reason"] = "Only a bounded current-source ordinal slice starting at " + str(start_index) + " was read."
            elif description["reason"] is None and not complete:
                description["reason"] = "One or more UI fields were not available as complete copied scalars."
        except SamplingStopped as stopped:
            if getattr(ctx, "pending_queries", ()):
                raise
            description.update(status="partial", reason=str(stopped), sampled=False, sampling_status="not_sampled")
        except Exception as failure:
            if getattr(ctx, "pending_queries", ()):
                raise  # The original callback/owner must be resumed, never replayed.
            unsupported = getattr(failure, "winerror", None) in (50, 127)
            description["status"] = "unavailable" if unsupported else "failed"
            description["reason"] = ("Native source capability is unsupported by this DLL: " if unsupported else
                "UI source query failed: ") + str(failure)
        finally:
            description["observed_source_counts"] = observations
            description["read_finished_monotonic_ns"] = monotonic_ns()
            if observations:
                description["stale"] = False
                last_sample[name] = description["read_finished_monotonic_ns"]
    output["read_finished_monotonic_ns"] = monotonic_ns()
    copied_progress = progress.copied()
    copied_progress["world"] = copied_progress.pop("generation")
    copied_progress["position_unit"] = "fresh ordinal work: item-field slot, or (parent index, child-field slot); not native identity"
    output["sampling_cursor"] = copied_progress
    output["query_count_kind"] = "reserved callback slots, including guarded pre-submission aborts"
    return output
