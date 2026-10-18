"""Copied records from V3's existing native CLogViewer capture store.

This does not tail log files, attach sinks, change filters or clear records.
Native parser results remain separate from these log observations. Each page
is one native snapshot; record sequence IDs are not request ownership IDs.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import IntFlag
import struct
from .native_bridge import NativeBridge, RemoteProcess
from .v3 import CallbackResult, ERROR_IO_PENDING, submit_callback

HEADER = struct.Struct("<4I4i12I2Q2I")
RECORD = struct.Struct("<Ii4IQ4I")

class Flags(IntFlag):
    GAP = 1
    RESET = 2
    PRUNED = 4
    OUTPUT_TRUNCATED = 8

@dataclass(frozen=True)
class LogRecord:
    native_sequence: int
    category: int
    source_line: int
    group_raw: int
    level_raw: int
    native_time_raw: int
    label: str
    source: str
    message: str

@dataclass(frozen=True)
class DiagnosticsResult:
    size: int
    error: int
    operation: int
    flags: Flags
    after_sequence: int
    first_sequence: int
    last_sequence: int
    next_sequence: int
    max_records: int
    output_capacity: int
    output_size: int
    output_required: int
    returned_count: int
    eligible_count: int
    store_count: int
    has_more: int
    logging_ready: int
    viewer_available: int
    subscription_coverage_known: int
    execution_thread_id: int
    expected_viewer: int
    viewer: int
    native_limit: int
    native_prune: int
    records: tuple[LogRecord, ...]

    @property
    def gap(self) -> bool:
        return bool(self.flags & Flags.GAP)

    @property
    def reset(self) -> bool:
        return bool(self.flags & Flags.RESET)

    @property
    def output_truncated(self) -> bool:
        return bool(self.flags & Flags.OUTPUT_TRUNCATED)

    @classmethod
    def from_bytes(cls, data: bytes) -> DiagnosticsResult:
        fields = list(HEADER.unpack_from(data))
        if (fields[0] != len(data) or fields[0] != HEADER.size + fields[9]
                or fields[10] > fields[9] or fields[12] > fields[8]):
            raise ValueError("invalid native diagnostics result layout")
        cursor, end, records = HEADER.size, HEADER.size + fields[10], []
        for _ in range(fields[12]):
            if cursor + RECORD.size > end:
                raise ValueError("incomplete native diagnostic record")
            values = RECORD.unpack_from(data, cursor)
            record_size, label_size, source_size, message_size = values[0], *values[7:10]
            if (record_size != RECORD.size + label_size + source_size + message_size
                    or cursor + record_size > end):
                raise ValueError("invalid native diagnostic record size")
            cursor += RECORD.size
            texts = []
            for length in (label_size, source_size, message_size):
                texts.append(data[cursor:cursor+length].decode("utf-8", errors="replace"))
                cursor += length
            records.append(LogRecord(*values[1:7], *texts))
        if cursor != end:
            raise ValueError("unparsed native diagnostic output")
        fields[3] = Flags(fields[3])
        return cls(*fields, tuple(records))


def _uint32(name: str, value: int) -> int:
    if not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"{name} must be uint32")
    return value


def _submit(client: NativeBridge, operation: int, after_sequence: int, max_records: int,
            output_capacity: int, expected_viewer: int, *, adapter: str | int,
            process: RemoteProcess | None) -> CallbackResult:
    if not isinstance(after_sequence, int) or not -1 <= after_sequence <= 0x7FFFFFFF:
        raise ValueError("after_sequence must be -1 or a signed native sequence ID")
    max_records = _uint32("max_records", max_records)
    output_capacity = _uint32("output_capacity", output_capacity)
    size = _uint32("size", HEADER.size + output_capacity)
    if not isinstance(expected_viewer, int) or not 0 <= expected_viewer <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("expected_viewer must be uint64")
    initial = HEADER.pack(size, ERROR_IO_PENDING, operation, 0,
        after_sequence, -1, -1, after_sequence, max_records, output_capacity,
        *([0]*10), expected_viewer, 0, 0, 0)
    return submit_callback(client, initial + bytes(output_capacity), adapter=adapter,
        callback_symbol="PdxV3DiagnosticsInterface", decode_result=DiagnosticsResult.from_bytes,
        process=process)


def status(client: NativeBridge, *, after_sequence: int = -1, expected_viewer: int = 0,
           adapter: str | int = "pdx_v3_diagnostics.dll",
           process: RemoteProcess | None = None) -> CallbackResult:
    """Observe existing store/range without creating a viewer or attaching a sink.

    viewer_available does not imply all categories are subscribed. This minimal
    adapter reports subscription_coverage_known=0; native filters are unchanged.
    ERROR_NOT_READY means the native store/application is absent, not empty success.
    """
    return _submit(client, 1, after_sequence, 0, 0, expected_viewer,
                   adapter=adapter, process=process)


def records(client: NativeBridge, *, after_sequence: int = -1, max_records: int = 256,
            output_capacity: int = 65536, expected_viewer: int = 0,
            adapter: str | int = "pdx_v3_diagnostics.dll",
            process: RemoteProcess | None = None) -> CallbackResult:
    """Copy a page after a native sequence ID; never clear or replay a Script.

    Continue from next_sequence and optionally expected_viewer=result.viewer.
    has_more includes an explicit record bound or short output. Only whole records
    are published. ERROR_MORE_DATA preserves copied records; use a larger read
    buffer from the same cursor if the next whole record does not fit. gap means
    earlier IDs are outside the retained range; reset means a supplied cursor is
    beyond the current last ID or the observed viewer changed. These are stream
    observations, not proof that a specific command caused a message. native_time_raw
    and group_raw stay uninterpreted. max_records/output_capacity are caller bounds,
    not changes to native retention policy.
    """
    return _submit(client, 2, after_sequence, max_records, output_capacity,
                   expected_viewer, adapter=adapter, process=process)
