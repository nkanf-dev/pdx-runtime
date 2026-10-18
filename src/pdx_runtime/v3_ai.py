"""Copied native CountryAI state and original console AI assignments.

The query does not create an actor. TAG, all and self keep their different native
scopes. Commands are submitted once through the normal console dispatcher.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import IntFlag
import struct
from .native_bridge import NativeBridge, RemoteProcess
from .v3 import CallbackResult, ERROR_IO_PENDING, submit_callback
from . import v3_console

STATE = struct.Struct("<20I8Q")

@dataclass(frozen=True)
class AiState:
    size: int
    error: int
    country_full_id: int
    country_error: int
    actor_full_id: int
    actor_error: int
    owner_country_full_id: int
    actor_enabled_raw: int
    manager_override_raw: int
    manager_error: int
    role_index: int
    role_raw: int
    role_count: int
    role_error: int
    observed_predicate: int
    observed_predicate_valid: int
    paused: int
    simulation_started: int
    execution_thread_id: int
    reserved: int
    application: int
    world: int
    idler: int
    country_manager: int
    ai_manager: int
    country: int
    actor: int
    role_data: int

    @classmethod
    def from_bytes(cls, data: bytes) -> AiState:
        result = cls(*STATE.unpack(data))
        if result.size != STATE.size:
            raise ValueError("invalid native AiState layout")
        return result


def observe_country_ai(client: NativeBridge, country_full_id: int, *,
                       adapter: str | int = "pdx_v3_console.dll",
                       process: RemoteProcess | None = None) -> CallbackResult:
    """Copy one existing CountryAI without creating it in one native Interface scope.

    Input is a fresh uint32 full Country ID, not a tag or actor slot index. Native
    Country/actor resolution, full-ID generation and owner checks precede the
    actor-byte copy. Country, actor, manager and role errors stay separate.
    world is the CGameState origin. country_manager is the first contained
    CCountryManager at its owned world+0x120 data payload origin. Native actor/AI
    manager fields use that payload, not the CGameState origin. Wire remains144B.
    The query never calls get_or_create and never dispatches a command.

    observed_predicate is valid only when observed_predicate_valid!=0 and error
    is zero. It is the scheduling condition in native0x150f470, not proof that
    all AI tasks stopped. An out-of-range role entry has role_error1168, but the
    native override-gated predicate can still be valid. An in-range null role
    pointer is an error; no zero role is fabricated. All pointers are borrowed.
    """
    if not isinstance(country_full_id, int) or isinstance(country_full_id, bool):
        raise TypeError("country_full_id must be an integer full Country ID")
    if not 0 <= country_full_id <= 0xFFFFFFFF:
        raise ValueError("country_full_id must fit uint32 without masking")
    values = [0] * 28
    values[0:3] = [STATE.size, ERROR_IO_PENDING, country_full_id]
    initial = STATE.pack(*values)
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3ConsoleAiStateInterface",
        decode_result=AiState.from_bytes, process=process)


def disable_ai(client: NativeBridge, selector: str, *, output_capacity: int = 65536,
               adapter: str | int = "pdx_v3_console.dll",
               process: RemoteProcess | None = None) -> CallbackResult:
    """Submit native disable_ai once, retaining native lookup and restrictions.

    TAG assigns one actor flag0. all assigns current cached actor flags0, not a
    future-global off mode. self also clears the manager-wide human-role override;
    it is not the observer's selected Country. Native success is not effect proof.
    No hidden retry, snapshot, restore, actor creation or selector rewriting occurs
    in Python. The native command itself can create an actor for TAG/self.
    """
    if not isinstance(selector, str):
        raise TypeError("selector must be a native console string")
    return v3_console.execute_arguments(client, ("disable_ai", selector),
        output_capacity=output_capacity, adapter=adapter, process=process)


def enable_ai(client: NativeBridge, selector: str, *, output_capacity: int = 65536,
              adapter: str | int = "pdx_v3_console.dll",
              process: RemoteProcess | None = None) -> CallbackResult:
    """Submit native enable_ai once; observe_country_ai supplies separate evidence.

    TAG assigns one actor flag1. all assigns current cached actor flags1 but does
    not enable the manager override. self also enables that manager-wide human-role
    override, which can affect other enabled human-role actors. Native success is
    not effect proof. No toggle, idempotence shortcut, retry or restoration occurs.
    """
    if not isinstance(selector, str):
        raise TypeError("selector must be a native console string")
    return v3_console.execute_arguments(client, ("enable_ai", selector),
        output_capacity=output_capacity, adapter=adapter, process=process)


# A separate copied cache ABI. STATE and the original command paths stay intact.
CACHE_SNAPSHOT = struct.Struct("<4IiIQ10I3Q")
CACHE_ROW = struct.Struct("<16I")


class AiCacheFlags(IntFlag):
    CONTEXT_VALID = 1
    HEADER_VALID = 2
    TOTAL_KNOWN = 4
    COMPLETE = 8
    TRUNCATED = 16
    ROW_ERRORS = 32
    PREFIX_FAULT = 64


class AiCacheValidFields(IntFlag):
    ACTOR_FIELDS = 1
    ACTOR_IDENTITY = 2
    OWNER_IDENTITY = 4
    ROLE_VALUE = 8
    PREDICATE = 16


@dataclass(frozen=True)
class AiCacheRow:
    """One cache slot, including duplicates and failed references.

    Raw byte values are not booleans. Only fields named by valid_fields are
    known. In particular, copied actor bytes do not prove actor identity, and
    an owner/link/role error does not remove this slot from the cohort.
    There are no engine pointers in a row.
    """
    cache_index: int
    valid_fields: AiCacheValidFields
    actor_full_id: int
    owner_country_full_id: int
    actor_error: int
    owner_error: int
    link_error: int
    role_error: int
    actor_enabled_raw: int
    actor_141_raw: int
    role_index: int
    role_raw: int
    observed_predicate: int
    reserved0: int
    reserved1: int
    reserved2: int

    @property
    def known_fields(self) -> AiCacheValidFields:
        return self.valid_fields

    @property
    def actor_fields_known(self) -> bool:
        return bool(self.valid_fields & AiCacheValidFields.ACTOR_FIELDS)

    @property
    def actor_identity_known(self) -> bool:
        return bool(self.valid_fields & AiCacheValidFields.ACTOR_IDENTITY)

    @property
    def owner_identity_known(self) -> bool:
        return bool(self.valid_fields & AiCacheValidFields.OWNER_IDENTITY)

    @property
    def role_value_known(self) -> bool:
        return bool(self.valid_fields & AiCacheValidFields.ROLE_VALUE)

    @property
    def observed_predicate_known(self) -> bool:
        return bool(self.valid_fields & AiCacheValidFields.PREDICATE)

    @property
    def has_errors(self) -> bool:
        return any((self.actor_error, self.owner_error, self.link_error, self.role_error))


@dataclass(frozen=True)
class AiCacheSnapshot:
    """A single ordered, copied cache observation, not a lease or an AI-off claim.

    complete describes membership coverage, even when individual rows failed.
    A fault can leave only a known whole-row prefix. A later larger read is a
    different snapshot, never continuation of this one. application/world are
    non-owning diagnostics, not handles for later engine-memory reads.
    """
    size: int
    error: int
    row_capacity: int
    row_count: int
    native_total: int
    flags: AiCacheFlags
    required_bytes: int
    returned_bytes: int
    row_bytes: int
    manager_override_raw: int
    manager_error: int
    role_count: int
    paused: int
    simulation_started: int
    execution_thread_id: int
    row_error_count: int
    reserved: int
    expected_world: int
    world: int
    application: int
    rows: tuple[AiCacheRow, ...]

    @property
    def context_valid(self) -> bool:
        return bool(self.flags & AiCacheFlags.CONTEXT_VALID)

    @property
    def header_valid(self) -> bool:
        return bool(self.flags & AiCacheFlags.HEADER_VALID)

    @property
    def total_known(self) -> bool:
        """The signed native count was copied; its array may still be invalid."""
        return bool(self.flags & AiCacheFlags.TOTAL_KNOWN)

    @property
    def complete(self) -> bool:
        return bool(self.flags & AiCacheFlags.COMPLETE)

    @property
    def truncated(self) -> bool:
        return bool(self.flags & AiCacheFlags.TRUNCATED)

    @property
    def prefix_fault(self) -> bool:
        return bool(self.flags & AiCacheFlags.PREFIX_FAULT)

    @property
    def has_row_errors(self) -> bool:
        return bool(self.flags & AiCacheFlags.ROW_ERRORS)

    @classmethod
    def from_bytes(cls, data: bytes, *, capacity: int | None = None,
                   expected_world: int | None = None) -> AiCacheSnapshot:
        """Decode the whole allocation, checking its whole-row prefix contract.

        Optional request values check that native output did not change inputs.
        Signed native totals, including known negative corrupt metadata, stay
        raw. They are not converted into an empty successful cache.
        """
        if len(data) < CACHE_SNAPSHOT.size:
            raise ValueError("incomplete native AiCacheSnapshot header")
        values = list(CACHE_SNAPSHOT.unpack_from(data))
        values[5] = AiCacheFlags(values[5])
        result = cls(*values, rows=())
        allocation = _cache_allocation_size(result.row_capacity)
        if result.size != len(data) or result.size != allocation:
            raise ValueError("invalid native AiCacheSnapshot allocation")
        if result.row_bytes != CACHE_ROW.size or result.reserved != 0:
            raise ValueError("invalid native AiCacheSnapshot request layout")
        if capacity is not None:
            _cache_allocation_size(capacity)
            if result.row_capacity != capacity:
                raise ValueError("native AiCacheSnapshot changed request capacity")
        if expected_world is not None:
            _cache_expected_world(expected_world)
            if result.expected_world != expected_world:
                raise ValueError("native AiCacheSnapshot changed expected_world")
        if int(result.flags) & ~127:
            raise ValueError("unknown native AiCacheSnapshot flags")
        if result.row_count > result.row_capacity:
            raise ValueError("native AiCacheSnapshot row count exceeds capacity")
        if result.header_valid and not result.context_valid:
            raise ValueError("native AiCacheSnapshot header lacks valid context")
        if result.context_valid and result.expected_world and result.world != result.expected_world:
            raise ValueError("native AiCacheSnapshot context mismatches expected_world")
        # TOTAL_KNOWN means that the signed native count was copied, not that
        # its array metadata was valid. A negative raw count can be known on an
        # error13 response. Only HEADER_VALID permits required_bytes and rows.
        if result.total_known and not result.context_valid:
            raise ValueError("native AiCacheSnapshot total lacks valid context")
        if result.header_valid:
            if not result.total_known or result.native_total < 0:
                raise ValueError("invalid native AiCacheSnapshot array header")
            if result.required_bytes != CACHE_SNAPSHOT.size + result.native_total * CACHE_ROW.size:
                raise ValueError("invalid native AiCacheSnapshot required bytes")
            if result.row_count > result.native_total:
                raise ValueError("native AiCacheSnapshot prefix exceeds total")
        elif result.required_bytes != 0 or result.row_count != 0:
            raise ValueError("native AiCacheSnapshot has rows or size without valid array header")
        prefix_bytes = CACHE_SNAPSHOT.size + result.row_count * CACHE_ROW.size
        # An unexecuted callback retains the exact pending input. Preserve it so
        # shared CallbackResult can raise its existing QueryExecutionError.
        if result.error == ERROR_IO_PENDING:
            if result.flags or result.row_count or result.returned_bytes or result.required_bytes:
                raise ValueError("inconsistent pending native AiCacheSnapshot")
        elif result.returned_bytes != prefix_bytes:
            raise ValueError("invalid native AiCacheSnapshot returned prefix bytes")
        if result.complete:
            if (not result.header_valid or result.truncated or result.prefix_fault or
                    result.error != 0 or result.row_count != result.native_total):
                raise ValueError("inconsistent complete native AiCacheSnapshot")
        if result.truncated:
            if (not result.header_valid or result.complete or result.prefix_fault or
                    result.error != 234 or result.native_total <= result.row_capacity or
                    result.row_count != result.row_capacity):
                raise ValueError("inconsistent truncated native AiCacheSnapshot")
        if result.prefix_fault:
            if result.error not in (998, 574) or result.complete or result.truncated:
                raise ValueError("inconsistent faulted native AiCacheSnapshot prefix")
        if result.error == 0 and not result.complete:
            raise ValueError("successful native AiCacheSnapshot lacks complete coverage")
        if result.error == 234 and not result.truncated:
            raise ValueError("native AiCacheSnapshot MORE_DATA lacks truncation")
        if result.error in (998, 574) and not result.prefix_fault:
            raise ValueError("native AiCacheSnapshot fault lacks prefix status")
        if result.context_valid and result.manager_error == 0 and result.manager_override_raw > 255:
            raise ValueError("invalid known native AI manager byte")
        rows = []
        for index in range(result.row_count):
            fields = list(CACHE_ROW.unpack_from(data, CACHE_SNAPSHOT.size + index * CACHE_ROW.size))
            fields[1] = AiCacheValidFields(fields[1])
            row = AiCacheRow(*fields)
            if row.cache_index != index:
                raise ValueError("native AiCacheSnapshot rows are not an ordered prefix")
            if int(row.valid_fields) & ~31 or any((row.reserved0, row.reserved1, row.reserved2)):
                raise ValueError("invalid native AiCacheRow layout or known fields")
            if row.actor_fields_known and (row.actor_enabled_raw > 255 or row.actor_141_raw > 255):
                raise ValueError("invalid known native AI actor byte")
            if row.actor_identity_known and row.actor_error:
                raise ValueError("native AI actor identity is both valid and failed")
            if row.owner_identity_known and row.owner_error:
                raise ValueError("native AI owner identity is both valid and failed")
            if row.role_value_known and (row.role_error or row.role_index >= result.role_count):
                raise ValueError("invalid known native AI role value")
            if row.observed_predicate_known and row.observed_predicate > 1:
                raise ValueError("invalid known native AI scheduling predicate")
            rows.append(row)
        row_errors = sum(row.has_errors for row in rows)
        if result.row_error_count != row_errors or result.has_row_errors != bool(row_errors):
            raise ValueError("inconsistent native AiCacheSnapshot row error count or flag")
        return cls(*values, rows=tuple(rows))


def _cache_allocation_size(capacity: int) -> int:
    if not isinstance(capacity, int) or isinstance(capacity, bool):
        raise TypeError("capacity must be an integer row count")
    if capacity < 0:
        raise ValueError("capacity must be nonnegative")
    size = CACHE_SNAPSHOT.size + capacity * CACHE_ROW.size
    if size > 0xFFFFFFFF:
        raise ValueError("native AI cache whole allocation must fit uint32")
    return size


def _cache_expected_world(expected_world: int) -> None:
    if not isinstance(expected_world, int) or isinstance(expected_world, bool):
        raise TypeError("expected_world must be an integer diagnostic world value")
    if not 0 <= expected_world <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("expected_world must fit uint64 without masking")


def observe_cached_ai(client: NativeBridge, *, capacity: int = 256,
                      expected_world: int = 0,
                      adapter: str | int = "pdx_v3_console.dll",
                      process: RemoteProcess | None = None) -> CallbackResult:
    """Copy the exact current native all-cache cohort once, without dispatch.

    Native code holds one Interface scope and preserves slot order, duplicates
    and error rows. It never creates, refreshes or changes an actor. Actor bytes
    and uint32 role values stay raw and require their validity bits. Coverage is
    separate from row validity and does not prove that AI jobs stopped.

    capacity=0 requests discovery only. A nonempty cache returns error234 with
    required_bytes and no rows. Capacity-short results retain a whole-row prefix.
    Faults retain only their known prefix and native status. No resize, retry,
    pause or job-drain gate is added. expected_world=0 omits the mismatch check;
    a nonzero value is not a pin, epoch or ownership lease.

    The returned shared CallbackResult keeps its allocation alive while pending.
    read() checks completion once; it does not dispatch another callback.
    """
    size = _cache_allocation_size(capacity)
    _cache_expected_world(expected_world)
    values = [0] * 20
    values[0:3] = [size, ERROR_IO_PENDING, capacity]
    values[8] = CACHE_ROW.size
    values[17] = expected_world
    initial = CACHE_SNAPSHOT.pack(*values) + bytes(size - CACHE_SNAPSHOT.size)
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3ConsoleAiCacheInterface",
        decode_result=lambda data: AiCacheSnapshot.from_bytes(
            data, capacity=capacity, expected_world=expected_world), process=process)
