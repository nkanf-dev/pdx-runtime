"""Copied UI reminders and explicitly labelled state observations on one context.

This is a sampled view, not an engine event subscription or durable notification
history. Reading never selects an option, dismisses a reminder, or pauses time.
"""
from __future__ import annotations

from copy import deepcopy
import importlib

from .. import v3_objects as objects
from ..v3_ui import SOURCE_IDS as NATIVE_UI_SOURCES
from .._sampling import PageCursor, SampleHistory
from .events import EventsAPI
from .contracts import OperationResult, PerceptionSnapshot

# The real native UI provider owns its channel inventory. Do not duplicate it
# here or mistake multiple channels for multiple game-reminder families.
UI_SOURCES = ("event_windows", *NATIVE_UI_SOURCES)
STATE_SOURCES = ("wars", "plays")
_HISTORY_LIMIT = 128


def _selection(value, allowed, default):
    chosen = tuple(default if value is None else (value,) if isinstance(value, str) else value)
    unknown = set(chosen).difference(allowed)
    if unknown:
        raise ValueError("Unknown perception source: " + ", ".join(sorted(map(str, unknown))))
    return tuple(dict.fromkeys(chosen))


def _source(name, status, reason=None, **extra):
    return {"source": name, "status": status, "reason": reason, **extra}


class NotificationsAPI:
    """Read the UI's current reminders without installing a strategy policy.

    Local cursors cover at most 128 sampled change batches on this context.
    They do not recover messages that appeared and disappeared between samples.
    At most 512 identities are remembered. An evicted identity can later appear
    as first_observed again; this is not proof of a newly emitted native item.
    Stable native identity is never inferred from text or a list position.
    """
    def __init__(self, ctx):
        self.ctx = ctx
        if not hasattr(ctx, "_notifications_state"):
            ctx._notifications_state = {"world": None, "sequence": 0, "previous": {}, "history": []}
        self._state = ctx._notifications_state

    async def observe(self, *, sources=None, cursor=None, state_sources=()) -> OperationResult[PerceptionSnapshot]:
        """Return current receipts, source coverage and bounded local changes.

        sources=None reads all known UI sources. An empty tuple opts out of UI
        reads. state_sources=("wars", "plays") additionally reads a bounded
        selected-country war directory and world diplomatic plays, respectively;
        these rows are not native UI notifications. The caller decides whether
        a receipt deserves attention or a pause. Native UI capture is partial:
        unavailable or failed sources are explicit, and an empty event window
        list never establishes peace or the absence of other reminders.
        """
        selected = _selection(sources, UI_SOURCES, UI_SOURCES)
        states = _selection(state_sources, STATE_SOURCES, ())
        return await self.ctx.perform("notifications.observe", lambda:
            self._observe(sources=selected, cursor=cursor, state_sources=states))

    async def _event_rows(self, events):
        if events is None:
            events = await EventsAPI(self.ctx)._pending()
        read_world = self.ctx.world
        rows = []
        for event in events:
            texts = deepcopy(event.get("text", {}))
            rows.append({"source": "event_windows", "kind": "event_window", "world": read_world,
                "provenance": "native_ui_data", "native_identity": None,
                "native_identity_reason": "The EventsAPI token is local, not a native full ID.",
                "local_id": event["id"], "identity_scope": "local_event_instance",
                "raw_text": {key: value.get("raw_text") for key, value in texts.items()},
                "fields": texts, "event": deepcopy(event),
                "visibility": {"present_in_source": True, "on_screen": None,
                    "reason": "A current EventWindow is not proof of pixel visibility or focus."},
                "scope": {"source_scope": "current_ui", "affected_countries": None,
                    "reason": "Affected actors are not inferred from localized prose."},
                "actions": [{"operation": "events.choose", "event_id": event["id"],
                    "option_index": option["index"], "label": option.get("label"),
                    "enabled": option.get("enabled"), "default": option.get("default"),
                    "highlighted": option.get("highlighted"), "automatically_invoked": False}
                    for option in event.get("options", ())]})
        complete = all(not event.get("unavailable") and
            all(field.get("complete", False) for field in event.get("text", {}).values())
            for event in events)
        return _source("event_windows", "available" if complete else "partial",
            None if complete else "Some current event text fields are unavailable or incomplete.",
            coverage="current_event_windows_only", count=len(events), world=read_world, atomic_snapshot=False, history_complete=False), rows

    async def _play_page(self):
        """One fresh copied diplomatic-play page, never a War fallback."""
        scans = self._state.setdefault("state_scans", {})
        scan = scans.get("plays")
        if scan is None or scan.generation != self.ctx.world:
            scan = PageCursor(self.ctx.world)
            scans["plays"] = scan
        start = scan.next_index
        domain = objects.Domain.DIPLOMATIC_PLAY
        page = await self.ctx.query("notifications.state.plays", lambda:
            objects.enumerate_objects(self.ctx.native, domain, start_index=start, max_items=128,
                capacity=128, adapter=self.ctx.adapters["objects"], process=self.ctx.process))
        if page.error or page.world != self.ctx.world or page.domain != domain:
            raise RuntimeError("The state page failed or changed world/domain")
        if page.truncated and page.next_index <= start:
            raise RuntimeError("The state page did not advance")
        scan.advance(page.next_index if page.truncated else 0, complete=not page.truncated)
        values = []
        for row in page.records:
            if row.error:
                continue
            attacker = row.attacker_leader_id if not row.attacker_leader_error else None
            defender = row.defender_leader_id if not row.defender_leader_error else None
            values.append({"id": row.resolved_id, "attacker_id": attacker, "defender_id": defender,
                "attacker": None, "defender": None, "participant_coverage": "leaders only"})
        return values, {"enumeration_complete": start == 0 and not page.truncated,
            "page_start_index": start, "next_index": scan.next_index, "sampled": True,
            "sampling_status": "sampled_page", "page_scope": "current_fresh_page_only",
            "reason_non_atomic": "Other pages from prior rounds are not merged into this current observation."}

    async def _state_rows(self, source, *, actor=None, actor_world=None):
        # Settle the original public operation before reading another source.
        from ._core import PendingOperation
        directory = None
        read_world = self.ctx.world
        page_info = {"enumeration_complete": True, "sampled": True, "sampling_status": "sampled"}
        if source == "wars":
            source_error = None
            try:
                directory = await self.ctx.domain("military").wars(start=0, limit=64)
                while isinstance(directory, PendingOperation):
                    directory = await directory.resume()
            except Exception as error:
                self._raise_if_pending()
                directory = getattr(error, "military_result", None)
                if directory is None:
                    raise
                source_error = error
            if not isinstance(directory, dict) or not isinstance(directory.get("wars"), list):
                raise TypeError("The war provider did not return a copied country directory")
            values = directory["wars"]
            read_world = directory["world"]
            # Keep the actual report, including prefix rows, native errors and
            # cleanup warnings. A bounded prefix is not a complete country list.
            page_info.update(enumeration_complete=directory.get("enumeration_complete") is True,
                page_start_index=directory.get("start"), next_index=directory.get("next"),
                sampling_status="sampled_page", page_scope="current_country_source_page_only",
                directory=deepcopy(directory))
            reason = ("The selected country's Country.AccessWars page is a state observation; "
                "source membership and any copied role fields do not establish active/ended status, "
                "all participants or wars outside this source.")
            scope = {"source_scope": directory.get("scope"), "country_id": directory.get("country_id"),
                "source": directory.get("source"), "membership": "selected_country_source",
                "reason": reason}
            provider_status = directory.get("status")
            status = "unavailable" if provider_status == "unavailable" else "partial"
            if source_error is not None:
                page_info.update(error=source_error, enumeration_complete=False,
                    sampling_status="stopped" if getattr(source_error, "sampling_stopped", False) else "failed")
                if not getattr(source_error, "sampling_stopped", False):
                    status = "failed"
                reason += " The original read stopped: " + str(source_error)
            coverage = directory.get("scope")
        else:
            if getattr(self.ctx, "_sampling_gate", None) is not None:
                values, page_info = await self._play_page()
            else:
                values = await self.ctx.domain("diplomacy").plays(all_countries=True)
            while isinstance(values, PendingOperation):
                values = await values.resume()
            if not isinstance(values, list):
                raise TypeError("The state provider did not return copied rows")
            reason = "World diplomatic plays expose leaders only, not full participants."
            scope = {"source_scope": "world", "membership": "leaders_only", "reason": reason}
            status, coverage = "partial", "leaders_only"
        rows = []
        for value in values:
            identity = value.get("id")
            row_scope = deepcopy(scope)
            row_scope["player_participation"] = None
            if source == "wars":
                row_scope["source_country_participation"] = value.get("country_is_participant")
                if actor is not None and actor_world == read_world and directory.get("country_id") == actor:
                    row_scope["player_participation"] = value.get("country_is_participant")
            rows.append({"source": source, "kind": "war_state" if source == "wars" else "diplomatic_play_state",
                "world": read_world,
                "provenance": "state_observation", "native_identity": identity,
                "native_identity_reason": None if identity is not None else "The provider returned no full ID.",
                "identity_scope": "world_object" if identity is not None else None,
                "raw_text": None, "fields": deepcopy(value), "actions": [],
                "active": None, "active_reason": reason, "scope": row_scope,
                "visibility": {"present_in_source": True, "on_screen": None,
                    "reason": "This is a state query, not a UI reminder."}})
        return _source(source, status, reason, coverage=coverage, count=len(rows), world=read_world,
            history_complete=False, **page_info), rows

    async def _observe(self, *, sources=None, cursor=None, state_sources=(), observed=None, events=None):
        sources = _selection(sources, UI_SOURCES, UI_SOURCES)
        state_sources = _selection(state_sources, STATE_SOURCES, ())
        if cursor is not None and (not isinstance(cursor, dict) or
                set(cursor) != {"world", "sequence"} or
                type(cursor["world"]) is not int or cursor["world"] <= 0 or
                isinstance(cursor["sequence"], bool) or not isinstance(cursor["sequence"], int) or cursor["sequence"] < 0):
            raise ValueError("cursor must be the local {world, sequence} token returned by this context")
        if observed is None:
            observed = await self.ctx.refresh()
        world = self.ctx.world
        if getattr(observed, "error", 0) or not world or getattr(observed, "transition_pending", False):
            raise RuntimeError("The game world is not ready for UI perception")
        parts = getattr(observed, "date_parts", None)
        gate = getattr(self.ctx, "_sampling_gate", None)
        def stopped():
            return gate.stop_reason() if gate is not None else None
        def unsampled(name, reason):
            return _source(name, "partial", reason, coverage=None, count=None,
                           sampled=False, sampling_status="not_sampled")
        actor, actor_reason = None, None
        if stopped():
            actor_reason = "Actor was not sampled: " + stopped()
        else:
            try:
                actor = await self.ctx.country_id()
            except Exception as error:
                self._raise_if_pending()
                actor_reason = str(error)
        if gate is not None:
            gate.actor_country_id = actor
        status, receipts, provider_progress = [], [], None
        for source in (("event_windows",) if "event_windows" in sources and self.ctx.world == world else ()):
            copied_empty_count = (events == [] and gate is not None and
                getattr(gate, "event_checked", False) and gate.event_count == 0)
            if stopped() and not events and not copied_empty_count:
                status.append(unsampled(source, stopped()))
                continue
            try:
                # Already-copied event rows require no new query and remain
                # readable even when capture stopped for an event/interrupt.
                row, values = await self._event_rows(events)
                status.append(row); receipts.extend(values)
            except Exception as error:
                self._raise_if_pending()
                status.append(unsampled(source, str(error)) if getattr(error, "sampling_stopped", False)
                    else _source(source, "failed", str(error), coverage=None))
        async def read_state_sources():
            for source in state_sources:
                if self.ctx.world != world:
                    break
                if stopped():
                    status.append(unsampled(source, stopped()))
                    continue
                try:
                    row, values = await self._state_rows(source, actor=actor, actor_world=world)
                    status.append(row); receipts.extend(values)
                except Exception as error:
                    self._raise_if_pending()
                    status.append(unsampled(source, str(error)) if getattr(error, "sampling_stopped", False)
                        else _source(source, "failed", str(error), coverage=None))
        if gate is not None:
            await read_state_sources()
        native_sources = tuple(name for name in sources if name != "event_windows")
        if native_sources and self.ctx.world == world and stopped():
            status.extend(unsampled(name, stopped()) for name in native_sources)
        elif native_sources and self.ctx.world == world:
            try:
                provider = importlib.import_module("pdx_runtime.v3_ui")
                options = {"should_stop": gate.stop_reason, "max_queries": 12,
                    "priority_sources": tuple(name for name in getattr(gate, "required_ui_sources", ())
                        if name in native_sources)} if gate is not None else {}
                result = await provider.observe(self.ctx, sources=native_sources, **options)
                provider_progress = deepcopy(result.get("sampling_cursor"))
                by_source = {row["source"]: row for row in result["sources"]}
                status.extend(deepcopy(by_source.get(name, _source(name, "unavailable",
                    "The native provider did not report this source.", coverage=None))) for name in native_sources)
                receipts.extend(deepcopy(result["receipts"]))
            except Exception as error:
                self._raise_if_pending()
                status.extend(unsampled(name, str(error)) if getattr(error, "sampling_stopped", False)
                    else _source(name, "failed", str(error), coverage=None) for name in native_sources)
        if gate is None:
            await read_state_sources()
        world_changed = self.ctx.world != world
        for name in (*sources, *state_sources):
            if not any(row["source"] == name for row in status):
                status.append(_source(name, "unavailable", "The world changed before this source was read.", coverage=None))
        for receipt in receipts:
            receipt.setdefault("native_identity", None)
            receipt.setdefault("native_identity_reason", None if receipt["native_identity"] is not None else
                "No stable native identity has been proven for this UI source.")
            receipt.setdefault("local_id", None)
            receipt.setdefault("world", world)
            receipt.setdefault("actor_country_id", actor if receipt["world"] == world else None)
            if receipt["world"] != world:
                receipt["actor_reason"] = "The world changed; the initial actor observation does not describe this receipt."
            receipt.setdefault("scope", {"source_scope": "current_ui", "affected_countries": None,
                "reason": "The source did not provide a complete affected-actor scope."})
            receipt.setdefault("raw_text", {name: field.get("value") for name, field in receipt.get("fields", {}).items()
                if isinstance(field, dict) and isinstance(field.get("value"), str)})
            receipt["time"] = {"observed_date_parts": parts, "emitted_date_parts": None,
                "reason": "The clock and UI reads are sequential; notification emission time is not established."}
            receipt.setdefault("actions", [])
        snapshot = {"world": world, "world_after": self.ctx.world, "world_changed": world_changed,
            "actor_country_id": actor, "actor_reason": actor_reason, "date_parts": parts,
            "atomic_snapshot": False, "sources": status, "receipts": receipts,
            "selected_sources_complete": not world_changed and bool(status) and all(row["status"] == "available" for row in status),
            "coverage_complete": False, "history_complete": False,
            "history_reason": "Sampled current sources are not a complete or durable history of all game UI reminders."}
        if gate is not None:
            snapshot["sampling"] = gate.public()
            snapshot["provider_sampling_cursor"] = provider_progress
        if world_changed:
            snapshot.update(cursor=None, cursor_status="world_changed", changes=[])
        else:
            self._changes(snapshot, cursor)
        return snapshot

    def _raise_if_pending(self):
        if getattr(self.ctx, "pending_queries", ()):
            raise  # The original exception/owner stays live. Do not submit another query.

    @staticmethod
    def _key(receipt):
        identity = receipt.get("native_identity")
        if identity is not None:
            # Identity must be a copied scalar, not a borrowed native wrapper.
            if isinstance(identity, (int, str, bytes)) and not isinstance(identity, bool):
                return receipt["source"], "native", identity
            return None
        if receipt.get("identity_scope") == "local_event_instance" and receipt.get("local_id") is not None:
            return receipt["source"], "local", receipt["local_id"]
        return None

    @staticmethod
    def _content(receipt):
        observation_keys = {"time", "deduplication", "lifecycle", "source_index", "read_sequence",
                            "read_started_monotonic_ns", "read_finished_monotonic_ns"}
        def content(value):
            if isinstance(value, dict):
                return {key: content(item) for key, item in value.items() if key not in observation_keys}
            if isinstance(value, (list, tuple)):
                return [content(item) for item in value]
            return value
        return content(receipt)

    def _changes(self, snapshot, cursor):
        state = self._state
        if state["world"] != snapshot["world"]:
            state.update(world=snapshot["world"], sequence=0, previous={},
                         sample_history=SampleHistory(snapshot["world"], capacity=_HISTORY_LIMIT))
        history = state.setdefault("sample_history", SampleHistory(snapshot["world"], capacity=_HISTORY_LIMIT))
        current, changes = {}, []
        for receipt in snapshot["receipts"]:
            key = self._key(receipt)
            if key is None:
                receipt["deduplication"] = {"status": "unavailable", "reason":
                    "No proven identity; identical text or list position does not prove the same reminder."}
                changes.append({"lifecycle": "observed", "receipt": deepcopy(receipt)})
                continue
            if key in current:
                # Duplicate identity in one sampled source is ambiguous. Do not
                # fabricate a second ID or silently suppress the extra row.
                receipt["deduplication"] = {"status": "ambiguous", "reason": "The source repeated the same identity."}
                changes.append({"lifecycle": "observed", "receipt": deepcopy(receipt)})
                continue
            receipt["deduplication"] = {"status": "identity", "scope": key[1]}
            current[key] = deepcopy(receipt)
            previous = state["previous"].get(key)
            if previous is None or self._content(previous) != self._content(receipt):
                changes.append({"lifecycle": "first_observed" if previous is None else "updated",
                                "receipt": deepcopy(receipt)})
        available = {row["source"] for row in snapshot["sources"] if row["status"] == "available"}
        requested = {row["source"] for row in snapshot["sources"]}
        for key, previous in list(state["previous"].items()):
            if key not in current and key[0] in available:
                changes.append({"lifecycle": "no_longer_observed", "receipt": deepcopy(previous),
                    "reason": "Absent from a later current-source sample; not proof of dismissal, resolution or peace."})
                del state["previous"][key]
            elif key[0] not in requested:
                # Unrequested sources retain their last sampled identity; their
                # absence from this scan is not an observed lifecycle change.
                continue
        state["previous"].update(current)
        # Bound source identity memory as well as cursor history. Eviction is
        # not reported as a native lifecycle change.
        while len(state["previous"]) > 512:
            state["previous"].pop(next(iter(state["previous"])))
        local_cursor = None if cursor is None else {"generation": cursor["world"], "sequence": cursor["sequence"]}
        result = history.append(changes, local_cursor)
        state["sequence"] = history.sequence
        snapshot.update(cursor={"world": result["cursor"]["generation"], "sequence": result["cursor"]["sequence"]},
            cursor_status="world_changed" if result["cursor_status"] == "generation_changed" else result["cursor_status"],
            changes=result["changes"])
