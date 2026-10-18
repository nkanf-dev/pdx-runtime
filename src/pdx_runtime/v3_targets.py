"""V3 target discovery from already analyzed fields, using a raw read callable.

Pass NativeBridge.read (or another exact read function). These reads do not call
native resolvers or validation and are not a locked, single-tick snapshot.
Evidence: 7bb480/c5af10 resolvers; 1177100/1176640/14afe50 State owner.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator

Read = Callable[[int, int], bytes]
COUNTRY_DATABASE_RVA = 0x5964E20
COUNTRY_FALLBACK_RVA = 0x5964E18
STATE_DATABASE_RVA = 0x5964EF0
STATE_FALLBACK_RVA = 0x5964E28
DATABASE_SLOTS_OFFSET = 0x20
DATABASE_BOUND_OFFSET = 0x2C
SLOT_STRIDE = 0x10
SLOT_POINTER_OFFSET = 8
COUNTRY_ID_OFFSET = 0x18
STATE_ID_OFFSET = 8
STATE_OWNER_OFFSET = 0xE48
BUILDING_DATABASE_RVA = 0x5966738
BUILDING_FALLBACK_RVA = 0x5964278
BUILDING_ID_OFFSET = 0x10
BUILDING_STATE_OFFSET = 0xE4
BUILDING_LEVEL_OFFSET = 0xE8
BUILDING_TYPE_OFFSET = 0xF0
BUILDING_TYPE_FALLBACK_RVA = 0x5963FC0
ID_INDEX_MASK = 0xFFFFFF


def _uint(read: Read, address: int, size: int) -> int:
    data = read(address, size)
    if len(data) != size:
        raise ValueError(f"short raw read at {address:#x}: {len(data)} of {size} bytes")
    return int.from_bytes(data, "little")


def _database(read: Read, base: int, root_rva: int) -> tuple[int, int, int]:
    database = _uint(read, base + root_rva, 8)
    if not database:
        raise ValueError(f"reference database is null at RVA {root_rva:#x}")
    slots = _uint(read, database + DATABASE_SLOTS_OFFSET, 8)
    bound = _uint(read, database + DATABASE_BOUND_OFFSET, 4)
    if bound and not slots:
        raise ValueError("non-empty reference database has null slots")
    return database, slots, bound


def _resolve(read: Read, base: int, full_id: int, root_rva: int,
             fallback_rva: int, id_offset: int) -> int:
    if not 0 <= full_id < 0xFFFFFFFF:
        raise ValueError("full_id must be uint32, excluding the -1 sentinel")
    _, slots, bound = _database(read, base, root_rva)
    index = full_id & ID_INDEX_MASK
    if index >= bound:
        raise ValueError(f"ID {full_id:#x} has index outside database bound {bound}")
    pointer = _uint(read, slots + index * SLOT_STRIDE + SLOT_POINTER_OFFSET, 8)
    fallback = _uint(read, base + fallback_rva, 8)
    if not pointer or pointer == fallback:
        raise ValueError(f"ID {full_id:#x} resolves to an empty/fallback slot")
    if _uint(read, pointer + id_offset, 4) != full_id:
        raise ValueError(f"full ID mismatch for {full_id:#x}")
    return pointer


def resolve_country(read: Read, base: int, country_id: int) -> int:
    """Copy the Country resolver's slot/full-ID checks, without native execution."""
    return _resolve(read, base, country_id, COUNTRY_DATABASE_RVA,
                    COUNTRY_FALLBACK_RVA, COUNTRY_ID_OFFSET)


def resolve_state(read: Read, base: int, state_id: int) -> int:
    """Copy the State resolver's slot/full-ID checks, without native execution."""
    return _resolve(read, base, state_id, STATE_DATABASE_RVA,
                    STATE_FALLBACK_RVA, STATE_ID_OFFSET)


def iter_owned_states(read: Read, base: int, country_id: int, *,
                      start: int = 0, stop: int | None = None) -> Iterator[dict[str, int]]:
    """Yield observed real State slots with exactly this full Country owner ID.

    `stop` is a caller-selected exclusive scan boundary, not an object count.
    Native validation must recheck any chosen target in its execution context.
    Raw read errors propagate; no stale pointer, guessed ID or fallback is used.
    """
    resolve_country(read, base, country_id)
    _, slots, bound = _database(read, base, STATE_DATABASE_RVA)
    fallback = _uint(read, base + STATE_FALLBACK_RVA, 8)
    end = bound if stop is None else min(stop, bound)
    if not 0 <= start <= end:
        raise ValueError("require 0 <= start <= effective stop")
    for index in range(start, end):
        state = _uint(read, slots + index * SLOT_STRIDE + SLOT_POINTER_OFFSET, 8)
        if not state or state == fallback:
            continue
        full_id = _uint(read, state + STATE_ID_OFFSET, 4)
        if full_id == 0xFFFFFFFF or full_id & ID_INDEX_MASK != index:
            continue
        owner_id = _uint(read, state + STATE_OWNER_OFFSET, 4)
        if owner_id == country_id:
            yield {"slot_index": index, "state_id": full_id,
                   "state_pointer": state, "owner_id": owner_id}


def resolve_building(read: Read, base: int, building_id: int) -> int:
    """Copy c5ae60's slot/full-ID checks; a Building ID is at +0x10."""
    return _resolve(read, base, building_id, BUILDING_DATABASE_RVA,
                    BUILDING_FALLBACK_RVA, BUILDING_ID_OFFSET)


def iter_existing_building_targets(read: Read, base: int, country_id: int,
                                  state_id: int, *, start: int = 0,
                                  stop: int | None = None) -> Iterator[dict[str, int]]:
    """Yield real positive-level Buildings' Type pointers for one owned State.

    An existing Building/Type is an input candidate, not proof it is buildable.
    The native command validator must decide that in its real execution context.
    """
    resolve_country(read, base, country_id)
    state = resolve_state(read, base, state_id)
    if _uint(read, state + STATE_OWNER_OFFSET, 4) != country_id:
        raise ValueError("State owner does not match the full actor CountryID")
    _, slots, bound = _database(read, base, BUILDING_DATABASE_RVA)
    fallback = _uint(read, base + BUILDING_FALLBACK_RVA, 8)
    type_fallback = _uint(read, base + BUILDING_TYPE_FALLBACK_RVA, 8)
    end = bound if stop is None else min(stop, bound)
    if not 0 <= start <= end:
        raise ValueError("require 0 <= start <= effective stop")
    for index in range(start, end):
        building = _uint(read, slots + index * SLOT_STRIDE + SLOT_POINTER_OFFSET, 8)
        if not building or building == fallback:
            continue
        full_id = _uint(read, building + BUILDING_ID_OFFSET, 4)
        if full_id == 0xFFFFFFFF or full_id & ID_INDEX_MASK != index:
            continue
        if _uint(read, building + BUILDING_STATE_OFFSET, 4) != state_id:
            continue
        level_bytes = read(building + BUILDING_LEVEL_OFFSET, 4)
        if len(level_bytes) != 4:
            raise ValueError("short Building level read")
        level = int.from_bytes(level_bytes, "little", signed=True)
        building_type = _uint(read, building + BUILDING_TYPE_OFFSET, 8)
        if level > 0 and building_type and building_type != type_fallback:
            yield {"actor_id": country_id, "state_id": state_id,
                   "state_pointer": state, "building_id": full_id,
                   "building_pointer": building, "building_level": level,
                   "building_type": building_type, "building_slot_index": index}
