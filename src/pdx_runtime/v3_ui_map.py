"""Bounded original native map-notification snapshots. No session or UI policy.

Only the exact profiled current-image source is supported. A source count is a
current ownership observation, not pixel visibility, history or event identity.
Every native item/binding/VM temporary dies inside its original callback.
"""
from __future__ import annotations
from dataclasses import dataclass
import struct

from .native_bridge import NativeBridge, RemoteProcess
from .v3 import ERROR_IO_PENDING, submit_callback

HEADER = struct.Struct("<18IQ")
RECORD = struct.Struct("<6I")
SNAPSHOT = 1


@dataclass(frozen=True)
class MapNotification:
    source_index: int
    error: int
    text_offset: int
    text_bytes_copied: int
    text_bytes_required: int
    complete: int
    raw_bytes: bytes

    @property
    def raw_text(self) -> str | None:
        try:
            return self.raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None


@dataclass(frozen=True)
class MapSnapshot:
    size: int
    error: int
    action: int
    interface_thread_id: int
    max_items: int
    records_offset: int
    text_offset: int
    text_capacity: int
    source_count: int
    returned_count: int
    text_bytes_copied: int
    text_bytes_required: int
    truncated: int
    source_produced: int
    collection_valid: int
    execution_thread_id: int
    start_index: int
    reserved: int
    source_world: int
    records: tuple[MapNotification, ...]

    @classmethod
    def from_bytes(cls, raw: bytes) -> MapSnapshot:
        if len(raw) < HEADER.size:
            raise ValueError("truncated map-notification header")
        fields = HEADER.unpack_from(raw)
        size,error,action,thread,limit,records,text,capacity,count,returned,copied,required,truncated,produced,valid,executed,start,reserved,world = fields
        if (size != len(raw) or action != SNAPSHOT or reserved
                or records < HEADER.size or records % 8 or returned > limit
                or records + limit * RECORD.size > text or text + capacity > size
                or copied > capacity or produced not in (0,1) or valid not in (0,1)
                or valid and not produced or not error and (not produced or not valid)):
            raise ValueError("invalid map-notification copied result bounds/evidence")
        rows = []
        end = text + copied
        for index in range(returned):
            row = RECORD.unpack_from(raw, records + index * RECORD.size)
            ordinal, row_error, offset, length, needed, complete = row
            if (offset < text or offset + length > end or complete not in (0,1)
                    or ordinal >= count or complete and (row_error or length != needed)):
                raise ValueError("invalid copied map-notification record")
            rows.append(MapNotification(*row, raw[offset:offset+length]))
        return cls(*fields, tuple(rows))


def snapshot(client: NativeBridge, *, world: int, interface_thread_id: int,
             max_items: int = 64, text_capacity: int = 16384, start_index: int = 0,
             adapter: str | int = "pdx_runtime.dll", process: RemoteProcess | None = None):
    """Queue one original source/array/raw0/GetText/copy/cleanup callback.

    Unknown completion retains the exact original query. No replay, fallback,
    game discovery, source acknowledgement, native pointer or value handle.
    start_index is an ordinal continuation only; no stable identity is claimed.
    Older DLLs without the export report unsupported through normal SDK errors.
    """
    if type(world) is not int or not 0 < world <= 0xffffffffffffffff:
        raise ValueError("a current source world is required")
    if type(interface_thread_id) is not int or not 0 < interface_thread_id <= 0xffffffff:
        raise ValueError("the original Interface owner thread is required")
    for name, value, minimum in (("max_items", max_items, 1), ("text_capacity", text_capacity, 0), ("start_index", start_index, 0)):
        if type(value) is not int or not minimum <= value <= 0xffffffff:
            raise ValueError(name + " is outside uint32 bounds")
    records = HEADER.size
    text = records + max_items * RECORD.size
    size = text + text_capacity
    if size > 0xffffffff:
        raise ValueError("map-notification request exceeds uint32 allocation size")
    initial = HEADER.pack(size, ERROR_IO_PENDING, SNAPSHOT, interface_thread_id,
        max_items, records, text, text_capacity, 0,0,0,0,0,0,0,0,start_index,0,world) + bytes(size-HEADER.size)
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3UiMapNotificationsInterface", decode_result=MapSnapshot.from_bytes,
        process=process)
