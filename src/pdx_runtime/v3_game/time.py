"""Normal game clock with readable observations and interruptible waiting.

Only original lifecycle pause/speed/observe operations are used. Ordinary time
never submits partial Steps, SET_DATE, save, quit, or an automatic event choice.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect
from datetime import date, datetime, timedelta
import sys
from .. import v3_lifecycle as life
from .._sampling import (SamplingStopped as _SamplingStopped, QuerySamplingControl,
                         DelegatingQueryContext, PageCursor)
from .events import EventsAPI, _diagnostic
from .notifications import NotificationsAPI, UI_SOURCES, STATE_SOURCES, _selection
from ._run import rejected
from . import _run


def _target(value):
    if isinstance(value, datetime):
        return value.replace(minute=0, second=0, microsecond=0)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        parts = value.replace("-", ".").split(".")
        values = tuple(int(part) for part in parts)
    else:
        values = tuple(value)
    if len(values) == 3:
        values += (0,)
    if len(values) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        raise ValueError("date must be YYYY-MM-DD or (year, month, day[, hour])")
    return datetime(*values)


def _date_text(value):
    return value.strftime("%Y-%m-%d") + (" " + value.strftime("%H:00") if value.hour else "")


class _ClockObservation(dict):
    """JSON-safe raw clock facts; native_result is the exact decoded source.

    The original result is a Python attribute, not a JSON field. Three native
    text components expose day precision; the parser's added zero is not proof
    of an observed native hour. No finer precision is inferred from date_raw.
    """
    def __init__(self, observed, parts):
        self.native_result = observed
        text = getattr(observed, "date_text", None)
        components = len(text.split(".")) if text else 0
        super().__init__(date_raw=getattr(observed, "date_raw", None),
            date_text=text, date_parts=parts, error=getattr(observed, "error", None),
            text_precision="hour" if components == 4 else "day" if components == 3 else None,
            hour_source="native_text" if components == 4 else
                "parser_default_zero" if components == 3 else "unavailable")


class TimeRun(dict):
    """A background clock wait. await .wait() or .interrupt() for its notice.

    It keeps the exact original background task. Cancelling a caller's wait does
    not cancel a native query or restart the clock operation.
    """
    def __init__(self, target, signal, identity, *, direct_player=False):
        super().__init__(background=True, run_id=identity, target=_date_text(target), status="running")
        if direct_player:
            self["watch"] = identity
        self._signal = signal
        self._task = None
        self.notice = None
        self.perception = None
        self.captured_changes = []
        self.capture_truncated = False

    async def wait(self):
        return await asyncio.shield(self._task)

    async def interrupt(self):
        self._signal.set()
        return await self.wait()

    def __await__(self):
        return self.wait().__await__()


# These bounds apply only to one clock sampling round, not the raw SDK.
_SAMPLE_DATA_QUERIES = 12
_SAMPLE_CLOCK_QUERIES = 16
_SAMPLE_INTERVAL = 1.0


class _SamplingContext(DelegatingQueryContext):
    """V3 context adapter; generic query admission lives in _sampling."""
    def __init__(self, base, gate):
        super().__init__(base, gate.control)
        object.__setattr__(self, "_sampling_gate", gate)

    async def refresh(self):
        reason = await self._sampling_gate.check()
        if reason:
            raise _SamplingStopped(reason)
        return self._sampling_gate.latest

    async def type_id(self, name):
        from .. import v3_data as data
        gate = self._sampling_gate
        if gate.stop_reason():
            raise _SamplingStopped(gate.stop_reason())
        # Existing fake contexts may expose a type lookup but no native type
        # cache. Production GameContext always uses this copied-page path.
        if not hasattr(self._base, "_types"):
            return await self._base.type_id(name)
        cache = self._base._types
        if name in cache:
            return cache[name]
        scan = getattr(self._base, "_time_type_scan", None)
        if scan is None or scan.generation != self.world:
            scan = PageCursor(self.world)
            self._base._time_type_scan = scan
        if scan.complete:
            raise LookupError("native type " + repr(name) + " not found")
        while True:
            cursor = scan.next_index
            page = await self.query("metadata.types", lambda: data.enumerate_types(self.native,
                start=cursor, capacity=128, adapter=self.adapters["data"], process=self.process))
            if page.error:
                raise RuntimeError("Native type metadata error " + str(page.error))
            cache.update((row.name, row.type_id) for row in page.rows)
            scan.advance(page.next, complete=page.next <= cursor or page.next >= page.total)
            if name in cache:
                return cache[name]
            if scan.complete:
                raise LookupError("native type " + repr(name) + " not found")

    def domain(self, name):
        from copy import copy
        api = copy(self._base.domain(name))
        if hasattr(api, "_ctx"):
            api._ctx = self
        if hasattr(api, "ctx"):
            api.ctx = self
        return api


class _SamplingGate:
    """V3 clock/notice adapter over the game-independent admission control."""
    def __init__(self, api, signal, target, observed, *, allow_paused=False):
        self.api, self.signal, self.target = api, signal, target
        self.world = api.ctx.world
        self.allow_paused = allow_paused
        self.event_count = 0
        self.control = QuerySamplingControl(api.ctx.query, self._checkpoint, self._stop_requested,
            query_limit=_SAMPLE_DATA_QUERIES, checkpoint_limit=_SAMPLE_CLOCK_QUERIES, observation=observed)
        self.context = _SamplingContext(api.ctx, self)

    def _stop_requested(self):
        return "interrupted" if self.signal.is_set() else None

    async def _checkpoint(self):
        observed = await self.api._observe()
        reason = None
        if observed.world != self.world:
            reason = "world_changed"
        elif observed.date_parts is not None and datetime(*observed.date_parts) >= self.target:
            reason = "date"
        elif observed.paused and not self.allow_paused:
            reason = "paused"
        return observed, reason

    @property
    def reason(self): return self.control.reason
    @reason.setter
    def reason(self, value): self.control.reason = value
    @property
    def latest(self): return self.control.observation
    @latest.setter
    def latest(self, value): self.control.observation = value
    @property
    def data_queries(self): return self.control.queries
    @property
    def clock_queries(self): return self.control.checkpoints

    def stop_reason(self): return self.control.stop_reason()
    async def check(self): return await self.control.check()

    def public(self):
        return {"status": "partial" if self.reason else "sampled", "reason": self.reason,
                "data_queries": self.data_queries, "data_query_limit": _SAMPLE_DATA_QUERIES,
                "clock_queries": self.clock_queries, "clock_query_limit": _SAMPLE_CLOCK_QUERIES,
                "interval_seconds": _SAMPLE_INTERVAL}


class TimeAPI:
    def __init__(self, ctx):
        self.ctx = ctx
        self._run = None
        self._serial = 0

    async def _observe(self):
        observed = await self.ctx.refresh()
        if observed.error:
            raise RuntimeError("Cannot read the game clock: native error " + str(observed.error))
        if not observed.world or observed.transition_pending:
            raise RuntimeError("The game world is not ready for normal time")
        self._last_observed = observed
        return observed

    def _public(self, observed):
        parts = observed.date_parts
        current = datetime(*parts) if parts is not None else None
        return {"date": _date_text(current) if current is not None else None,
            "paused": bool(observed.paused), "speed": observed.displayed_speed,
            "clock": _ClockObservation(observed, parts)}

    async def observe(self):
        """Return the actual date, pause state and displayed game speed (0–5)."""
        async def read():
            return self._public(await self._observe())
        return await self.ctx.perform("time.observe", read)

    async def _pause_native(self):
        result = await self.ctx.query("time.pause", lambda:
            life.set_paused(self.ctx.native, True, adapter=self.ctx.adapters["lifecycle"], process=self.ctx.process))
        if result.error:
            raise RuntimeError("Pause was rejected: native error " + str(result.error))
        observed = await self._observe()
        while not observed.paused:
            await asyncio.sleep(.02)
            observed = await self._observe()
        return self._public(observed)

    async def pause(self):
        """Pause normally. Any current run returns an interrupted notice first."""
        async def stop():
            if self._run is not None and not self._run._task.done():
                return await self._run.interrupt()
            return await self._pause_native()
        return await self.ctx.perform("time.pause", stop)

    async def _resume_native(self, speed):
        if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 5:
            raise ValueError("speed must be a displayed game speed from 1 to 5")
        result = await self.ctx.query("time.speed", lambda:
            life.set_speed(self.ctx.native, speed - 1, adapter=self.ctx.adapters["lifecycle"], process=self.ctx.process))
        if result.error:
            raise RuntimeError("Speed change was rejected: native error " + str(result.error))
        result = await self.ctx.query("time.resume", lambda:
            life.set_paused(self.ctx.native, False, adapter=self.ctx.adapters["lifecycle"], process=self.ctx.process))
        if result.error:
            raise RuntimeError("Resume was rejected: native error " + str(result.error))
        observed = await self._observe()
        # A native UI reminder may pause immediately after resume. Do not
        # wait forever for an unpaused observation or override that pause.
        while observed.speed != speed - 1:
            await asyncio.sleep(.02)
            observed = await self._observe()
        return self._public(observed)

    async def resume(self, speed=3):
        """Resume normal time at the requested displayed speed. Choose no policy."""
        async def start():
            if self._run is not None and not self._run._task.done():
                await self._run.interrupt()
            return await self._resume_native(speed)
        return await self.ctx.perform("time.resume", start)

    async def interrupt(self):
        """Stop the current wait and pause after its original native query settles."""
        return await self.pause()

    def _player(self):
        player = self.ctx.session.namespace.get("player")
        return player if isinstance(getattr(player, "interrupt", None), asyncio.Event) else None

    async def _events(self):
        gate = getattr(self, "_capture_gate", None)
        api = EventsAPI(gate.context if gate is not None else self.ctx)
        type_id = await api._prepare()
        count = await api._count(type_id)
        if gate is not None:
            gate.event_checked = True
        if not count:
            return []
        if gate is not None:
            gate.event_count = count
            if self.ctx.world != gate.world:
                gate.reason = "world_changed"
                return []
            # Stop the clock before reading long modal text/option projections.
            if gate.stop_reason() == "interrupted":
                return []
            if not gate.latest.paused:
                await self._pause_native()
                gate.latest = self._last_observed
            gate.allow_paused = True
        return await api._pending()

    async def _perception(self, observed, events, sources, states, cursor):
        gate = getattr(self, "_capture_gate", None)
        return await NotificationsAPI(gate.context if gate is not None else self.ctx)._observe(
            observed=observed, events=events, sources=sources, state_sources=states, cursor=cursor)

    @staticmethod
    def _stop_condition(predicate, snapshot):
        if predicate is None:
            return False
        result = predicate(snapshot)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("stop_when must be a synchronous predicate returning bool")
        if not isinstance(result, bool):
            raise TypeError("stop_when must return bool, not an inferred truth value")
        return result

    @staticmethod
    def _guard_contract(predicate):
        declared = predicate is not None and (hasattr(predicate, "required_ui_sources") or
                                              hasattr(predicate, "required_state_sources"))
        policy = getattr(predicate, "unknown_policy", None) if declared else None
        if declared and policy not in ("stop", "continue"):
            raise ValueError("A declared guard requires unknown_policy='stop' or 'continue'")
        return {"required_ui_sources": _selection(getattr(predicate, "required_ui_sources", ()), UI_SOURCES, ()),
                "required_state_sources": _selection(getattr(predicate, "required_state_sources", ()), STATE_SOURCES, ()),
                "unknown_policy": policy, "declared": declared}

    @staticmethod
    def _guard_dependencies(contract, snapshot):
        by_source = {row["source"]: row for row in snapshot.get("sources", ())}
        missing = []
        for kind, names in (("ui", contract["required_ui_sources"]), ("state", contract["required_state_sources"])):
            for name in names:
                row = by_source.get(name)
                fresh = row is not None and row.get("sampled") is not False and not row.get("stale", False)
                valid = fresh and (row.get("status") == "available" if kind == "ui" else
                    row.get("enumeration_complete") is True and row.get("status") not in ("failed", "unavailable"))
                if not valid:
                    missing.append({"source": name, "kind": kind,
                        "status": row.get("status") if row is not None else "not_requested",
                        "reason": row.get("reason") if row is not None else "The required source was not requested."})
        result = {**contract, "sampled": not missing, "missing": missing,
                  "safety_certified": False,
                  "reason": "A false predicate requests no stop; it is not a peace or safety certification."}
        snapshot["guard_dependencies"] = result
        return result

    async def _begin(self, target, speed, *, stop_when=None, notification_sources=None,
                     state_sources=(), perception_cursor=None):
        if stop_when is not None and not callable(stop_when):
            raise TypeError("stop_when must be a synchronous predicate returning bool")
        # Validate before interrupting an existing run or changing the clock.
        sources = _selection(notification_sources, UI_SOURCES, UI_SOURCES)
        states = _selection(state_sources, STATE_SOURCES, ())
        guard_contract = self._guard_contract(stop_when)
        if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 5:
            raise ValueError("speed must be a displayed game speed from 1 to 5")
        player = self._player()
        old_watch = getattr(player, "watch", None)
        if old_watch is not None and not old_watch.done() and old_watch is not asyncio.current_task():
            await player.stop_watch()
        if self._run is not None and not self._run._task.done():
            await self._run.interrupt()
        signal = player.interrupt if player is not None else asyncio.Event()
        signal.clear()
        observed = await self._observe()
        original_world = self.ctx.world
        current = datetime(*observed.date_parts) if observed.date_parts else None
        gate = _SamplingGate(self, signal, target, observed, allow_paused=observed.paused)
        gate.required_ui_sources = guard_contract["required_ui_sources"]
        self._capture_gate = gate
        events = []
        if current is None:
            raise RuntimeError("Native date is unavailable; no clock run started")
        if current >= target:
            gate.reason = "date"
        else:
            try:
                events = await self._events()
                gate.event_checked = True
            except _SamplingStopped:
                pass
        if self.ctx.world != original_world or gate.latest.world != original_world:
            gate.reason = "world_changed"
        elif signal.is_set():
            gate.reason = "interrupted"
        elif events or gate.event_count:
            gate.reason = "event"
        if gate.reason in ("date", "event", "paused", "interrupted") and not gate.latest.paused:
            await self._pause_native()
            gate.latest = self._last_observed
        snapshot = await self._perception(gate.latest, events, sources, states, perception_cursor)
        dependencies = self._guard_dependencies(guard_contract, snapshot)
        captures = deepcopy(snapshot.get("changes", []))
        capture_truncated = len(captures) > 128
        captures = captures[-128:]
        cursor = snapshot.get("cursor")
        if self.ctx.world != original_world or snapshot.get("world_changed"):
            gate.reason = "world_changed"
        elif signal.is_set():
            gate.reason = "interrupted"
        elif gate.reason not in ("date", "event", "paused"):
            # Do not decide from the clock captured before a UI batch.
            gate.latest = await self._observe()
            if gate.latest.world != original_world:
                gate.reason = "world_changed"
            elif gate.latest.date_parts is not None and datetime(*gate.latest.date_parts) >= target:
                gate.reason = "date"
            elif gate.latest.paused and not observed.paused:
                gate.reason = "paused"
        if gate.reason == "world_changed":
            return {"reason": "world_changed", "target": _date_text(target), "perception": snapshot,
                    "captured_changes": captures, "capture_truncated": capture_truncated,
                    "sampling": gate.public()}
        reason = gate.reason if gate.reason in ("date", "event", "paused", "interrupted") else None
        if reason is None and not getattr(gate, "event_checked", False):
            reason = "sampling_incomplete"
        if reason is None and self._stop_condition(stop_when, snapshot):
            reason = "condition"
        if reason is None and guard_contract["unknown_policy"] == "stop" and not dependencies["sampled"]:
            reason = "sampling_incomplete"
        if reason is not None:
            paused = self._public(gate.latest) if gate.latest.paused else await self._pause_native()
            return {"reason": reason, **paused, "target": _date_text(target), "events": events,
                    "event_count": gate.event_count if gate.event_count else len(events),
                    "perception": snapshot, "captured_changes": captures,
                    "capture_truncated": capture_truncated, "sampling": gate.public()}
        await self._resume_native(speed)
        if self.ctx.world != original_world:
            return {"reason": "world_changed", "target": _date_text(target), "perception": snapshot,
                    "captured_changes": captures, "capture_truncated": capture_truncated,
                    "sampling": gate.public()}
        if signal.is_set():
            return {"reason": "interrupted", **(await self._pause_native()), "target": _date_text(target),
                    "perception": snapshot, "captured_changes": captures, "capture_truncated": capture_truncated,
                    "sampling": gate.public()}
        self._serial += 1
        if player is not None:
            player.watch_sequence += 1
            identity = player.watch_sequence
        else:
            identity = self._serial
        handle = TimeRun(target, signal, identity, direct_player=player is not None)
        handle.perception = snapshot
        handle.captured_changes = captures
        handle.capture_truncated = capture_truncated
        self._run = handle

        async def drive():
            nonlocal cursor
            try:
                while True:
                    observed = await self._observe()
                    gate = _SamplingGate(self, signal, target, observed)
                    gate.required_ui_sources = guard_contract["required_ui_sources"]
                    self._capture_gate = gate
                    events = []
                    if observed.world != original_world:
                        gate.reason = "world_changed"
                    elif signal.is_set():
                        gate.reason = "interrupted"
                    elif observed.date_parts is not None and datetime(*observed.date_parts) >= target:
                        gate.reason = "date"
                    elif observed.paused:
                        gate.reason = "paused"
                    else:
                        try:
                            events = await self._events()
                            gate.event_checked = True
                        except _SamplingStopped:
                            pass
                        if self.ctx.world != original_world or gate.latest.world != original_world:
                            gate.reason = "world_changed"
                        elif signal.is_set():
                            gate.reason = "interrupted"
                        elif events or gate.event_count:
                            gate.reason = "event"
                    # Known terminal clock conditions win before any long UI
                    # read. A stopped gate makes the remaining sources explicit
                    # partial/not_sampled; copied event rows need no new query.
                    if gate.reason in ("date", "event", "paused", "interrupted") and not gate.latest.paused:
                        await self._pause_native()
                        gate.latest = self._last_observed
                    snapshot = await self._perception(gate.latest, events, sources, states, cursor)
                    dependencies = self._guard_dependencies(guard_contract, snapshot)
                    handle.perception = snapshot
                    cursor = snapshot.get("cursor")
                    handle.captured_changes.extend(deepcopy(snapshot.get("changes", [])))
                    if len(handle.captured_changes) > 128:
                        handle.capture_truncated = True
                        del handle.captured_changes[:-128]
                    if self.ctx.world != original_world or snapshot.get("world_changed"):
                        reason = "world_changed"
                    elif signal.is_set():
                        reason = "interrupted"
                    elif gate.reason in ("date", "event", "paused"):
                        reason = gate.reason
                    else:
                        # One fresh clock observation after a bounded sample;
                        # never reuse its pre-sample date or paused flag.
                        gate.latest = await self._observe()
                        if gate.latest.world != original_world:
                            reason = "world_changed"
                        elif signal.is_set():
                            reason = "interrupted"
                        elif gate.latest.date_parts is not None and datetime(*gate.latest.date_parts) >= target:
                            reason = "date"
                        elif gate.latest.paused:
                            reason = "paused"
                        elif self._stop_condition(stop_when, snapshot):
                            reason = "condition"
                        elif guard_contract["unknown_policy"] == "stop" and not dependencies["sampled"]:
                            reason = "sampling_incomplete"
                        else:
                            try:
                                await asyncio.wait_for(signal.wait(), _SAMPLE_INTERVAL)
                            except asyncio.TimeoutError:
                                pass
                            continue
                    if reason == "world_changed":
                        notice = {"reason": reason, "target": _date_text(target),
                                  "message": "The original game world changed; this clock run stopped.",
                                  "sampling": gate.public()}
                    else:
                        paused = self._public(gate.latest) if gate.latest.paused else await self._pause_native()
                        notice = {"reason": reason, **paused, "target": _date_text(target), "events": events,
                                  "event_count": gate.event_count if gate.event_count else len(events),
                                  "sampling": gate.public()}
                    break
            except Exception as error:
                # Core keeps the original unknown query. Never submit even a
                # pause behind an unsettled native operation.
                _diagnostic(self.ctx, {"domain": "time", "operation": "run", "error": error})
                if not getattr(self.ctx, "pending_queries", ()) and self.ctx.world == original_world:
                    try:
                        await self._pause_native()
                    except Exception as pause_error:
                        _diagnostic(self.ctx, {"domain": "time", "pause_error": pause_error})
                notice = {"reason": "error", "error": str(error), "target": _date_text(target),
                          "original_operation_retained": True}
            notice.update(perception=handle.perception, captured_changes=deepcopy(handle.captured_changes),
                          capture_truncated=handle.capture_truncated)
            handle.notice = notice
            handle.update(status="complete", notice=notice)
            if player is not None:
                player.notices[str(identity)] = {"watch": identity, **notice}
                while len(player.notices) > 64:
                    del player.notices[next(iter(player.notices))]
                module = sys.modules.get(type(player).__module__)
                write = getattr(module, "atomic", None)
                if write is not None and player.folder is not None:
                    try:
                        write(player.folder / "notices.json", player.notices)
                    except OSError as error:
                        _diagnostic(self.ctx, {"domain": "time", "notification_error": error})
            return notice

        handle._task = asyncio.create_task(drive(), name="v3-normal-time")
        if player is not None:
            player.watch = handle._task
        return handle

    async def run_for(self, *, days, speed=3, wait=False, stop_when=None,
                      notification_sources=None, state_sources=(), perception_cursor=None):
        """Run normal time for whole days, stopping at an event or interruption.

        With wait=False, returns a TimeRun once started; its .wait() gives the
        terminal notice. With wait=True, the retained operation also waits for
        that notice. If startup or waiting returns PendingOperation, resume that
        same operation to continue the requested behavior. With Root's existing
        Player installed, its ordinary execute/follow mechanism
        also observes this run and interrupts it for the next execute request.
        Notification and predicate options have the same meaning as run_until.
        Reading a reminder does not consume it or imply an automatic pause.
        """
        if isinstance(days, bool) or not isinstance(days, int) or days < 0:
            raise ValueError("days must be a nonnegative number of whole game days")
        async def start():
            observed = await self._observe()
            if observed.date_parts is None:
                raise RuntimeError("Native date is unavailable")
            result = await self._begin(datetime(*observed.date_parts) + timedelta(days=days), speed,
                stop_when=stop_when, notification_sources=notification_sources,
                state_sources=state_sources, perception_cursor=perception_cursor)
            return await result.wait() if wait and isinstance(result, TimeRun) else result
        return await self.ctx.perform("time.run_for", start)

    async def run_until(self, date, *, speed=3, wait=False, stop_when=None,
                        notification_sources=None, state_sources=(), perception_cursor=None):
        """Run until a date, EventWindow, native pause, interruption or predicate.

        With wait=True, any PendingOperation retains the terminal-notice wait,
        including when startup is still pending. wait=False returns the original
        background TimeRun once startup completes.

        UI sources are sampled inside this exact clock task, never by a parallel
        native poller. Non-modal reminders do not pause by default. stop_when is
        an optional synchronous bool predicate on a copied perception, whose
        source coverage may be partial. False requests no stop; it certifies
        neither peace nor safety. A declared guard can expose required_ui_sources,
        required_state_sources and unknown_policy="stop" or "continue". Only
        those explicit dependencies are checked; missing fresh dependencies with
        policy stop return sampling_incomplete without resuming. state_sources
        alone is observation, not an implicit guard dependency. The predicate
        runs on the initial baseline too. state_sources opts into separately
        labelled world-state fallback reads. perception_cursor is a local
        notifications cursor from a prior reviewed sample, not an engine cursor.
        The notice retains at most 128 sampled changes; capture_truncated marks
        eviction. Ephemeral reminders between samples may never be observed.
        """
        target = _target(date)
        async def start():
            result = await self._begin(target, speed,
                stop_when=stop_when, notification_sources=notification_sources,
                state_sources=state_sources, perception_cursor=perception_cursor)
            return await result.wait() if wait and isinstance(result, TimeRun) else result
        return await self.ctx.perform("time.run_until", start)
