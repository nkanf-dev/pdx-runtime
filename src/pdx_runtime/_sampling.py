"""Game-independent cooperative sampling over caller-owned callbacks.

No connection, native memory access, clock policy or background task is created.
A stopped sample never cancels an in-flight operation or replays its factory.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import inspect


class SamplingStopped(RuntimeError):
    sampling_stopped = True
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class QuerySamplingControl:
    """Bound new submissions and inject stop/checkpoint policy from an adapter.

    dispatch(label, factory, **kwargs) owns submission and settlement. checkpoint
    returns (copied_observation, reason_or_None). stop_requested returns a reason
    or None. A reserved slot counts conservatively even if pre-submit stop wins.
    No deadline can bound settlement of an already-owned unknown operation.
    """
    def __init__(self, dispatch, checkpoint, stop_requested, *, query_limit,
                 checkpoint_limit, observation=None):
        if type(query_limit) is not int or query_limit < 1 or type(checkpoint_limit) is not int or checkpoint_limit < 1:
            raise ValueError("sampling limits must be positive integers")
        self.dispatch, self.checkpoint, self.stop_requested = dispatch, checkpoint, stop_requested
        self.query_limit, self.checkpoint_limit = query_limit, checkpoint_limit
        self.observation = observation
        self.reason = None
        self.queries = self.checkpoints = 0

    def stop_reason(self):
        stop = self.stop_requested()
        if stop:
            self.reason = stop
        if self.reason is None and (self.queries >= self.query_limit or self.checkpoints >= self.checkpoint_limit):
            self.reason = "query_budget"
        return self.reason

    async def check(self):
        reason = self.stop_reason()
        if reason:
            return reason
        self.checkpoints += 1
        self.observation, reason = await self.checkpoint()
        self.reason = self.stop_requested() or reason
        return self.reason

    async def query(self, label, factory, **kwargs):
        reason = await self.check()
        if reason:
            raise SamplingStopped(reason)
        self.queries += 1
        def guarded_factory():
            # Recheck only external/terminal stop here. The current reserved
            # slot is valid even when it reaches the numerical query limit.
            stop = self.stop_requested()
            if stop:
                self.reason = stop
                raise SamplingStopped(stop)
            if self.reason and self.reason != "query_budget":
                raise SamplingStopped(self.reason)
            return factory()
        result = await self.dispatch(label, guarded_factory, **kwargs)
        stop = self.stop_requested()
        if stop:
            self.reason = stop
        return result


class DelegatingQueryContext:
    """Delegate original state/methods while replacing only query admission."""
    def __init__(self, base, control):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_sampling_control", control)

    def __getattr__(self, name):
        value = getattr(self._base, name)
        if inspect.ismethod(value) and value.__self__ is self._base:
            return value.__func__.__get__(self, type(self))
        return value

    def __setattr__(self, name, value):
        setattr(self._base, name, value)

    async def query(self, label, factory, **kwargs):
        return await self._sampling_control.query(label, factory, **kwargs)


@dataclass
class PageCursor:
    """Copied numeric continuation; the adapter owns page validity/decoding."""
    generation: object
    next_index: int = 0
    complete: bool = False

    def reset(self, generation):
        self.generation, self.next_index, self.complete = generation, 0, False

    def advance(self, next_index, *, complete):
        if type(next_index) is not int or next_index < 0:
            raise ValueError("next_index must be a nonnegative integer")
        self.next_index, self.complete = next_index, bool(complete)


class RoundRobinProgress:
    """Fair copied source/item positions over opaque keys and a generation."""
    def __init__(self, generation):
        self.generation = generation
        self.next_source = None
        self.positions = {}
        self.last_attempt = {}
        self.round = 0

    def plan(self, keys, *, priority=()):
        keys = tuple(dict.fromkeys(keys))
        self.round += 1
        rotated = keys
        if self.next_source in keys:
            start = keys.index(self.next_source)
            rotated = keys[start:] + keys[:start]
        pinned = tuple(key for key in dict.fromkeys(priority) if key in keys)
        return pinned + tuple(key for key in rotated if key not in pinned)

    def attempted(self, key, ordered_keys, *, next_position=0):
        keys = tuple(ordered_keys)
        self.positions[key] = next_position
        self.last_attempt[key] = self.round
        self.next_source = keys[(keys.index(key) + 1) % len(keys)] if keys else None

    def age(self, key):
        previous = self.last_attempt.get(key)
        return None if previous is None else self.round - previous

    def copied(self):
        return {"generation": self.generation, "next_source": self.next_source,
                "positions": dict(self.positions), "last_attempt": dict(self.last_attempt), "round": self.round}


class SampleHistory:
    """Bounded local copied change batches; never an external/native history."""
    def __init__(self, generation, *, capacity=128):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        self.generation, self.capacity = generation, capacity
        self.sequence = 0
        self.batches = []

    def append(self, changes, cursor=None):
        status = "baseline" if cursor is None else "valid"
        if cursor is not None:
            if cursor["generation"] != self.generation:
                status = "generation_changed"
            elif cursor["sequence"] > self.sequence:
                status = "invalid"
        self.sequence += 1
        self.batches.append((self.sequence, deepcopy(changes)))
        del self.batches[:-self.capacity]
        # The new batch can evict a batch still owed to this cursor. Evaluate
        # gaps after trimming, not against the pre-append window.
        if status == "valid" and self.batches and cursor["sequence"] < self.batches[0][0] - 1:
            status = "gap"
        if status in ("valid", "gap"):
            changes = [change for sequence, batch in self.batches if sequence > cursor["sequence"] for change in batch]
        return {"cursor": {"generation": self.generation, "sequence": self.sequence},
                "cursor_status": status, "changes": deepcopy(changes)}
