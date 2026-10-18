"""Observe CCharacter IDs and GetHealth raw values from CK3 1.20.0.2.

External reads are not a native Runtime or a consistent world snapshot. Native
function RVAs and low-level decoding helpers remain public for later integration.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sys
from typing import Callable

from .windows import (
    QueryFailure, ReadFailure, WindowsAPI, WindowsProcess, address_argument,
    file_sha256, positive_pid, size_argument,
)

CK3_SHA256 = "ae1ba6ff060ba603842f6f4a2ded0af4b7d3666b3dd271f75fb01b0da8e81b2d"
CHARACTER_DATABASE_RVA = 0x5C67568
CHARACTER_FALLBACK_RVA = 0x5C67570
CHARACTER_DATABASE_VTABLE_RVA = 0x4766838
DATABASE_SLOTS_OFFSET = 0x20
DATABASE_BOUND_OFFSET = 0x2C
SLOT_SIZE = 16
SLOT_OBJECT_OFFSET = 8
CHARACTER_ID_OFFSET = 0x18
CHARACTER_TYPE_OFFSET = 0x1C
CHARACTER_TYPE_TAG = 0x43686172
CHARACTER_COMPONENT_OFFSET = 0x1B0
HEALTH_SELECTOR_OFFSET = 0x6C
COMPONENT_HEALTH_OFFSET = 0x2F0
HEALTH_DEFAULT_ARRAY_RVA = 0x54501C8
HEALTH_DEFAULT_COUNT_RVA = 0x54501D4
NATIVE_CHARACTER_RESOLVER_RVA = 0x896E90
NATIVE_HEALTH_GETTER_RVA = 0x28C6500
NATIVE_BIRTH_DATE_GETTER_RVA = 0x1774BE0
BIRTH_DATE_OFFSET = 0x60

ReadBytes = Callable[[int, int], bytes]


def read_uint(read: ReadBytes, address: int, size: int, *, signed: bool = False) -> int:
    data = read(address, size)
    if len(data) != size:
        raise ReadFailure("read_failed", requested_bytes=size, received_bytes=len(data))
    return int.from_bytes(data, "little", signed=signed)


@dataclass(frozen=True)
class CharacterDatabase:
    address: int
    slots: int
    index_bound: int
    fallback: int


def character_database(read: ReadBytes, base: int) -> CharacterDatabase:
    """Read the native global reference-database pointer, not CCharacterManager."""
    address = read_uint(read, base + CHARACTER_DATABASE_RVA, 8)
    fallback = read_uint(read, base + CHARACTER_FALLBACK_RVA, 8)
    if not address:
        return CharacterDatabase(0, 0, 0, fallback)
    return CharacterDatabase(address,
                             read_uint(read, address + DATABASE_SLOTS_OFFSET, 8),
                             read_uint(read, address + DATABASE_BOUND_OFFSET, 4),
                             fallback)


def resolve_character(read: ReadBytes, database: CharacterDatabase, character_id: int) -> int:
    """Mirror native 0x896e90. Failure returns the native fallback, not necessarily 0."""
    if not 0 <= character_id <= 0xFFFFFFFF:
        raise ValueError("character_id must fit uint32")
    index = character_id & 0xFFFFFF
    if not database.address or index >= database.index_bound:
        return database.fallback
    obj = read_uint(read, database.slots + SLOT_SIZE * index + SLOT_OBJECT_OFFSET, 8)
    if not obj or read_uint(read, obj + CHARACTER_ID_OFFSET, 4) != character_id:
        return database.fallback
    return obj


def read_character_health(read: ReadBytes, base: int, character: int) -> dict:
    """Mirror GetHealth's field selection. No scale or signedness is inferred."""
    component = read_uint(read, character + CHARACTER_COMPONENT_OFFSET, 8)
    if not component:
        return {"health_raw64": 0, "health_raw64_hex": "0x0000000000000000",
                "health_source": "component_null"}
    selector = read_uint(read, character + HEALTH_SELECTOR_OFFSET, 2, signed=True)
    if selector < 0:
        address = component + COMPONENT_HEALTH_OFFSET
        source = "character_component"
    else:
        count = read_uint(read, base + HEALTH_DEFAULT_COUNT_RVA, 4, signed=True)
        array = read_uint(read, base + HEALTH_DEFAULT_ARRAY_RVA, 8)
        if count <= 0 or not array:
            raise QueryFailure("health_defaults_not_loaded", count=count)
        address = array + 8 * (count - 1)
        source = "global_last_value"
    raw = read_uint(read, address, 8)
    return {"health_raw64": raw, "health_raw64_hex": f"0x{raw:016x}",
            "health_source": source, "health_selector_i16": selector}


def sample_characters(read: ReadBytes, base: int, limit: int = 20,
                      character_id: int | None = None, scan_limit: int = 4096) -> dict:
    """Sample slots, or resolve one full ID. scan_limit=0 permits the full table."""
    if limit < 1 or scan_limit < 0:
        raise ValueError("limit must be positive and scan_limit nonnegative")
    result = {"status": "ok", "characters": [], "errors": [],
              "stats": {"scanned_slots": 0, "empty_slots": 0, "fallback_slots": 0,
                        "invalid_objects": 0},
              "health_interpretation": {"signedness": "unknown", "scale": "unknown",
                                        "unit": "unknown"}}
    try:
        database = character_database(read, base)
        result["database_index_bound"] = database.index_bound
        if not database.address:
            return {**result, "status": "character_database_not_loaded"}
        if character_id is not None:
            obj = resolve_character(read, database, character_id)
            if not obj or obj == database.fallback:
                return {**result, "status": "invalid_character_id"}
            candidates = [(character_id & 0xFFFFFF, obj)]
        else:
            end = min(database.index_bound, scan_limit) if scan_limit else database.index_bound
            candidates = ((index, None) for index in range(end))
        for index, obj in candidates:
            result["stats"]["scanned_slots"] += 1
            if obj is None:
                obj = read_uint(read, database.slots + SLOT_SIZE * index + SLOT_OBJECT_OFFSET, 8)
            if not obj or obj == database.fallback:
                key = "empty_slots" if not obj else "fallback_slots"
                result["stats"][key] += 1
                continue
            try:
                full_id = read_uint(read, obj + CHARACTER_ID_OFFSET, 4)
                tag = read_uint(read, obj + CHARACTER_TYPE_OFFSET, 4)
                if (full_id == 0xFFFFFFFF or full_id & 0xFFFFFF != index
                        or tag != CHARACTER_TYPE_TAG
                        or (character_id is not None and full_id != character_id)):
                    result["stats"]["invalid_objects"] += 1
                    if character_id is not None:
                        return {**result, "status": "invalid_character_id"}
                    continue
                health = read_character_health(read, base, obj)
                result["characters"].append({"character_id": full_id,
                                             "character_id_hex": f"0x{full_id:08x}", **health})
                if len(result["characters"]) >= limit:
                    break
            except QueryFailure as error:
                result["errors"].append({"slot_index": index, **error.as_dict()})
        result["limit_reached"] = len(result["characters"]) >= limit
        result["scan_limit_reached"] = (character_id is None and scan_limit > 0
                                       and database.index_bound > scan_limit
                                       and result["stats"]["scanned_slots"] == scan_limit)
        if result["errors"]:
            result["status"] = "partial" if result["characters"] else "read_failed"
        elif result["scan_limit_reached"]:
            result["status"] = "partial"
        return result
    except QueryFailure as error:
        result["errors"].append(error.as_dict())
        result["status"] = "partial" if result["characters"] else error.status
        return result


def query(pid: int | None = None, limit: int = 20, character_id: int | None = None, *,
          scan_limit: int = 4096, read_rva: int | None = None,
          read_address: int | None = None, size: int | None = None) -> dict:
    result = {"sample_kind": "external_sample", "consistent_snapshot": False,
              "native_lock_protected": False,
              "sample_started_utc": datetime.now(timezone.utc).isoformat()}
    process = None
    try:
        api = WindowsAPI()
        if pid is None:
            matches = api.find_processes("ck3.exe")
            if not matches:
                raise QueryFailure("process_not_found", executable="ck3.exe")
            if len(matches) != 1:
                raise QueryFailure("multiple_processes", pids=matches, action="specify --pid")
            pid = matches[0]
        process = WindowsProcess(api, pid)
        result["process"] = {"pid": pid, "path": process.image_path()}
        module = api.main_module(pid)
        base = module["base"]
        result["main_module"] = {**module, "base": f"0x{base:x}"}
        if read_rva is not None or read_address is not None:
            address = base + read_rva if read_rva is not None else read_address
            result["raw_read"] = {"address": f"0x{address:x}", "size": size,
                                  "bytes_hex": process.read(address, size).hex()}
            result["status"] = "ok"
        else:
            actual_sha = file_sha256(module["path"])
            result["build"] = {"sha256": actual_sha,
                               "matches_known_layout": actual_sha == CK3_SHA256}
            if actual_sha != CK3_SHA256:
                raise QueryFailure("unsupported_build")
            result.update(sample_characters(process.read, base, limit, character_id, scan_limit))
    except QueryFailure as error:
        result.update(error.as_dict())
    except OSError as error:
        result.update(status="file_read_failed", detail=str(error))
    finally:
        if process is not None:
            process.close()
    result["sample_finished_utc"] = datetime.now(timezone.utc).isoformat()
    return result


def character_id_argument(value: str) -> int:
    number = int(value, 0)
    if not 0 <= number <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("Character ID must fit uint32")
    return number


def positive_count(value: str) -> int:
    number = int(value, 0)
    if number <= 0:
        raise argparse.ArgumentTypeError("count must be positive")
    return number


def scan_limit_argument(value: str) -> int:
    number = int(value, 0)
    if number < 0:
        raise argparse.ArgumentTypeError("scan limit must be nonnegative; 0 scans the full table")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=positive_pid, help="PID; otherwise find ck3.exe")
    parser.add_argument("--limit", type=positive_count, default=20)
    parser.add_argument("--scan-limit", type=scan_limit_argument, default=4096,
                        help="slot sample budget; 0 scans the full table")
    parser.add_argument("--character-id", type=character_id_argument, help="full uint32 ID")
    raw = parser.add_mutually_exclusive_group()
    raw.add_argument("--read-rva", type=address_argument)
    raw.add_argument("--read-address", type=address_argument)
    parser.add_argument("--size", type=size_argument)
    args = parser.parse_args(argv)
    raw_requested = args.read_rva is not None or args.read_address is not None
    if raw_requested != (args.size is not None):
        parser.error("raw read requires --read-rva/--read-address together with --size")
    if raw_requested and args.character_id is not None:
        parser.error("raw read and --character-id are separate modes")
    result = query(args.pid, args.limit, args.character_id, scan_limit=args.scan_limit,
                   read_rva=args.read_rva, read_address=args.read_address, size=args.size)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ok", "partial", "character_database_not_loaded"} else 1


if __name__ == "__main__":
    sys.exit(main())
