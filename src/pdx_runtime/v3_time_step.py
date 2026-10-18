"""One explicit V3 partial daily tick, through the original native command receiver.

A native partial tick advances6 hours, not1 hour/day. Submission and observation
are separate queued operations. read() only reads an existing callback handle; it
never submits an effect or observation. No unpause, sleep estimate, date write,
background polling, automatic world entry or unknown-delivery replay.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .native_bridge import NativeBridge, RemoteProcess
from .v3 import CallbackResult, ERROR_IO_PENDING, submit_callback

RESULT = struct.Struct("<20I10Q")
PARTIAL_TICK_HOURS = 6


@dataclass(frozen=True)
class Snapshot:
    size: int
    error: int
    action: int
    accepted: int
    execution_thread_id: int
    paused: int
    game_started: int
    transition_pending: int
    initialized: int
    thread_stop: int
    world_updating: int
    queues_settled: int
    outgoing_active: int
    outgoing_pending: int
    incoming_active: int
    incoming_pending: int
    session_buffered: int
    logic_tick_pending: int
    raw_counter_5964f10: int
    raw_counter_5964f14: int
    application: int
    world: int
    idler: int
    logic: int
    session: int
    dispatcher: int
    thread_handle: int
    active_command: int
    date_raw: int
    frame: int

    @classmethod
    def from_bytes(cls, data: bytes) -> Snapshot:
        fields = RESULT.unpack(data)
        if fields[0] != RESULT.size:
            raise ValueError("native time-step layout mismatch")
        return cls(*fields)

    @property
    def date_hours(self) -> int:
        return self.date_raw & 0xffffffff


@dataclass(frozen=True)
class Observation:
    """Comparison of a completed submission baseline and one explicit snapshot.

    completed means date+6 and an observed settled paused native boundary. It
    does not erase raw errors or claim all later asynchronous external effects.
    Raw accounting counters and Logic+DF are not used as completion identities.
    """
    completed: bool
    pending: bool
    delta_hours: int
    reason: str
    baseline: Snapshot
    current: Snapshot


def compare(baseline: Snapshot, current: Snapshot) -> Observation:
    delta = current.date_hours - baseline.date_hours
    reason = ""
    pending = False
    if baseline.error or not baseline.accepted or baseline.action != 1:
        reason = "submission was not confirmed accepted"
    elif current.error:
        reason = "native observation unavailable"
    elif (current.application, current.world, current.session, current.idler) != (
            baseline.application, baseline.world, baseline.session, baseline.idler):
        reason = "native world or execution binding changed"
    elif not current.paused or not current.game_started or current.transition_pending:
        reason = "paused started world no longer held"
    elif not current.initialized or not current.thread_handle or current.thread_stop:
        reason = "native Session worker is no longer ready"
    elif delta not in (0, PARTIAL_TICK_HOURS):
        reason = "unexpected native date advancement"
    elif delta == 0 or not current.queues_settled:
        reason = "native tick or followup commands pending"
        pending = True
    return Observation(not reason, pending, delta, reason, baseline, current)


def _submit(client: NativeBridge, action: int, *, adapter: str | int,
            process: RemoteProcess | None) -> CallbackResult:
    initial = RESULT.pack(RESULT.size, ERROR_IO_PENDING, action, *([0] * 17), *([0] * 10))
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3TimeStepInterface", decode_result=Snapshot.from_bytes,
        process=process)


def observe(client: NativeBridge, *, adapter: str | int = "pdx_v3_time_step.dll",
            process: RemoteProcess | None = None) -> CallbackResult:
    """Explicitly queue ONE independent native boundary snapshot, never a tick."""
    return _submit(client, 0, adapter=adapter, process=process)


class Step:
    """One submission handle plus explicit observation composition, no second queue.

    submission/client/process/adapter remain public. submission.read() only reads
    the already queued admission callback and its baseline. observe() is an
    explicit new observation submission; its returned query.read() never queues.
    Unknown delivery retains the shared handle in exception.query, never replayed.
    """
    def __init__(self, client: NativeBridge, submission: CallbackResult,
                 adapter: str | int, process: RemoteProcess | None):
        self.client = client
        self.submission = submission
        self.adapter = adapter
        self.process = process

    def observe(self, *, process: RemoteProcess | None = None) -> CallbackResult:
        return observe(self.client, adapter=self.adapter,
                       process=process if process is not None else self.process)


def submit(client: NativeBridge, *, adapter: str | int = "pdx_v3_time_step.dll",
           process: RemoteProcess | None = None) -> Step:
    """Explicitly queue ONE partial-tick admission in an already paused world.

    Native baseline locks are released before factory/submit; another native
    producer may intervene. Acceptance is not an atomic date guarantee. Retain
    unknown/pending callback storage and inspect it; never automatically resubmit.
    Native followups/autosave policy remain unchanged. Later call observe() and
    compare() explicitly to establish the same-world date+6 settled boundary.
    """
    request = _submit(client, 1, adapter=adapter, process=process)
    return Step(client, request, adapter, process)
