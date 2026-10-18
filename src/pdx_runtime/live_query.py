"""Read Country GDP raw bits from one known Victoria 3 build, outside the game.

This is a read-only external sample, not a Runtime adapter or a locked snapshot.
Only the executable file identity and the fixed layout below are supported.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from typing import Callable

# Compatibility reexports: these are the canonical shared objects, not copies.
from .windows import (
    ERROR_NO_MORE_FILES, INVALID_HANDLE_VALUE, MAX_RAW_BYTES, MODULEENTRY32W,
    PROCESSENTRY32W, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_VM_READ,
    QueryFailure, ReadFailure, TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32,
    TH32CS_SNAPPROCESS, WindowsAPI, WindowsProcess, address_argument,
    file_sha256, positive_pid, size_argument,
)

V3_SHA256 = "7f53e3260cf46b6247f6906837464944038c4f74e860274ad3d394e0c04f811f"
APPLICATION_RVA = 0x592D128
WORLD_OFFSET = 0x608
COUNTRY_DATABASE_RVA = 0x5964E20
COUNTRY_FALLBACK_RVA = 0x5964E18
DATABASE_SLOTS_OFFSET = 0x20
DATABASE_BOUND_OFFSET = 0x2C
SLOT_SIZE = 16
SLOT_OBJECT_OFFSET = 8
COUNTRY_ID_OFFSET = 0x18
COUNTRY_GDP_OFFSET = 0x88
MAX_SCAN_SLOTS = 4096
MAX_LIMIT = 1000


def sample_countries(read: Callable[[int, int], bytes], base: int,
                     limit: int = 20, country_id: int | None = None) -> dict:
    """Decode fixed fields. Every read is external and may occur at a different time."""
    result = {"status": "ok", "countries": [], "errors": [],
              "stats": {"scanned_slots": 0, "empty_slots": 0,
                        "fallback_slots": 0, "id_mismatches": 0, "read_errors": 0},
              "gdp_interpretation": {"signedness": "unknown", "scale": "unknown",
                                     "unit": "unknown"}}
    stage = "application"

    def integer(address: int, size: int) -> int:
        data = read(address, size)
        if len(data) != size:
            raise ReadFailure("read_failed", requested_bytes=size, received_bytes=len(data))
        return int.from_bytes(data, "little", signed=False)

    try:
        application = integer(base + APPLICATION_RVA, 8)
        if not application:
            return {**result, "status": "world_not_loaded", "reason": "application_null"}
        stage = "world"
        if not integer(application + WORLD_OFFSET, 8):
            return {**result, "status": "world_not_loaded", "reason": "world_null"}
        stage = "country_database"
        database = integer(base + COUNTRY_DATABASE_RVA, 8)
        if not database:
            return {**result, "status": "country_database_not_loaded"}
        stage = "country_fallback"
        fallback = integer(base + COUNTRY_FALLBACK_RVA, 8)
        stage = "country_slots"
        slots = integer(database + DATABASE_SLOTS_OFFSET, 8)
        bound = integer(database + DATABASE_BOUND_OFFSET, 4)
        result["database_index_bound"] = bound
        if country_id is not None:
            index = country_id & 0xFFFFFF
            if index >= bound:
                return {**result, "status": "invalid_country_id", "reason": "index_out_of_range"}
            indices = range(index, index + 1)
        else:
            if not bound:
                return result
            indices = range(min(bound, MAX_SCAN_SLOTS))
        if not slots:
            return {**result, "status": "country_slots_null"}
        for index in indices:
            stage = "country_slot"
            result["stats"]["scanned_slots"] += 1
            # A failed slot read ends this scan; no retries across world changes.
            obj = integer(slots + SLOT_SIZE * index + SLOT_OBJECT_OFFSET, 8)
            if not obj or obj == fallback:
                key = "empty_slots" if not obj else "fallback_slots"
                result["stats"][key] += 1
                if country_id is not None:
                    return {**result, "status": "invalid_country_id", "reason": key}
                continue
            try:
                stage = "country_id"
                full_id = integer(obj + COUNTRY_ID_OFFSET, 4)
                if full_id & 0xFFFFFF != index or (country_id is not None and full_id != country_id):
                    result["stats"]["id_mismatches"] += 1
                    if country_id is not None:
                        return {**result, "status": "invalid_country_id", "reason": "full_id_mismatch"}
                    continue
                stage = "country_gdp"
                raw = integer(obj + COUNTRY_GDP_OFFSET, 8)
                result["countries"].append({"country_id": full_id, "country_id_hex": f"0x{full_id:08x}",
                                            "gdp_raw64": raw, "gdp_raw64_hex": f"0x{raw:016x}"})
                if len(result["countries"]) >= limit:
                    break
            except ReadFailure as error:
                result["stats"]["read_errors"] += 1
                if len(result["errors"]) < limit:
                    result["errors"].append({"slot_index": index, "stage": stage, **error.as_dict()})
        result["scan_budget_exhausted"] = (country_id is None and bound > MAX_SCAN_SLOTS
                                             and result["stats"]["scanned_slots"] == MAX_SCAN_SLOTS)
        result["limit_reached"] = len(result["countries"]) >= limit
        # This reports only scan progress, never complete live-world enumeration.
        if result["stats"]["read_errors"]:
            result["status"] = "partial" if result["countries"] else "read_failed"
        elif result["scan_budget_exhausted"]:
            result["status"] = "partial"
        return result
    except ReadFailure as error:
        result["stats"]["read_errors"] += 1
        result["errors"].append({"stage": stage, **error.as_dict()})
        result["status"] = "partial" if result["countries"] else "read_failed"
        return result


def query(pid: int | None, limit: int, country_id: int | None, *,
          read_rva: int | None = None, read_address: int | None = None,
          size: int | None = None) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    result = {"sample_kind": "external_sample", "consistent_snapshot": False,
              "native_lock_protected": False, "sample_started_utc": started,
              "status": "unavailable"}
    process = None
    try:
        api = WindowsAPI()
        if pid is None:
            matches = api.find_processes("victoria3.exe")
            if not matches:
                raise QueryFailure("process_not_found", executable="victoria3.exe")
            if len(matches) != 1:
                raise QueryFailure("multiple_processes", pids=matches, action="specify --pid")
            pid = matches[0]
        result["process"] = {"pid": pid}
        process = WindowsProcess(api, pid)
        result["process"]["path"] = process.image_path()
        module = api.main_module(pid)
        base = module["base"]
        result["main_module"] = {**module, "base": f"0x{base:x}"}
        if read_rva is not None or read_address is not None:
            # Raw observation uses no game layout and needs no loaded world or known build.
            address = base + read_rva if read_rva is not None else read_address
            result["raw_read"] = {"address": f"0x{address:x}", "size": size,
                                  "source": "main_module_rva" if read_rva is not None else "virtual_address"}
            if read_rva is not None:
                result["raw_read"]["rva"] = f"0x{read_rva:x}"
            result["raw_read"]["bytes_hex"] = process.read(address, size).hex()
            result["status"] = "ok"
        else:
            # One disk-file identity check enables only this fixed layout, not a profile capability.
            actual_sha = file_sha256(module["path"])
            result["build"] = {"sha256": actual_sha, "expected_sha256": V3_SHA256,
                               "identity_source": "main_module_file", "matches_known_layout": actual_sha == V3_SHA256}
            if actual_sha != V3_SHA256:
                raise QueryFailure("unsupported_build")
            result.update(sample_countries(process.read, base, limit, country_id))
    except QueryFailure as error:
        result.update(error.as_dict())
    except OSError as error:
        result.update(status="file_read_failed", detail=str(error))
    finally:
        if process is not None:
            process.close()
    result["sample_finished_utc"] = datetime.now(timezone.utc).isoformat()
    return result


def country_id_argument(value: str) -> int:
    number = int(value, 0)
    if not 0 <= number <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("Country ID must be a full uint32 value")
    return number


def limit_argument(value: str) -> int:
    number = int(value)
    if not 1 <= number <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"limit must be between 1 and {MAX_LIMIT}")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=positive_pid, help="PID; otherwise find victoria3.exe")
    parser.add_argument("--limit", type=limit_argument, default=20, help="maximum countries returned (default: 20)")
    parser.add_argument("--country-id", type=country_id_argument, help="one full uint32 Country ID, decimal or 0x...")
    raw = parser.add_mutually_exclusive_group()
    raw.add_argument("--read-rva", type=address_argument, help="raw bytes at main module base + RVA; no game layout required")
    raw.add_argument("--read-address", type=address_argument, help="raw bytes at virtual address; no game layout required")
    parser.add_argument("--size", type=size_argument, help="raw read byte count (required with --read-rva/--read-address)")
    args = parser.parse_args(argv)
    raw_requested = args.read_rva is not None or args.read_address is not None
    if raw_requested != (args.size is not None):
        parser.error("raw read requires --read-rva or --read-address together with --size")
    if raw_requested and args.country_id is not None:
        parser.error("raw read and --country-id are separate query modes")
    if raw_requested:
        result = query(args.pid, args.limit, args.country_id, read_rva=args.read_rva,
                       read_address=args.read_address, size=args.size)
    else:
        result = query(args.pid, args.limit, args.country_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ok", "partial", "world_not_loaded"} else 1


if __name__ == "__main__":
    sys.exit(main())
