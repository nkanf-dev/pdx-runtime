"""Thin CK3 native query adapter using the public RemoteProcess primitives.

Only enqueue/install exports run as remote threads. Character resolver/getter and
access guards execute on the native CInterfaceApplication slot-7 callback.
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import struct

from .native_bridge import BridgeError, RemoteProcess


class CharacterResult(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("error", ctypes.c_uint32),
                ("requested_id", ctypes.c_uint32), ("resolved_id", ctypes.c_uint32),
                ("character", ctypes.c_uint64), ("fallback", ctypes.c_uint64),
                ("health_raw64", ctypes.c_uint64), ("birth_date_address", ctypes.c_uint64)]


class QueryStatus(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in
                ("size", "installed", "error", "interface_thread_id", "pending_operation",
                 "executing", "world_ready", "transition_pending")] + [
                (name, ctypes.c_uint64) for name in
                ("request_id", "completed_request_id", "callback_count", "main_module_base",
                 "application", "original_vtable", "hook_vtable", "world", "world_data",
                 "active_idler", "character_database")] + [("character", CharacterResult)]


class CK3NativeQuery:
    """Caller keeps the DLL loaded; remove() queues detachment, never unloads it."""
    def __init__(self, process: RemoteProcess, module: int):
        self.process, self.module = process, module
        self.state_address = process.resolve_export(module, "PdxCK3State")

    @classmethod
    def load(cls, process: RemoteProcess, dll_path: str | Path) -> "CK3NativeQuery":
        module = process.load_library(dll_path)
        return cls(process, module["base"])

    def _request(self, export: str, data: bytes | None = None) -> int:
        address = self.process.allocate(len(data)) if data else 0
        try:
            if data:
                self.process.write(address, data)
            function = self.process.resolve_export(self.module, export)
            with self.process.start_thread(function, address) as thread:
                error = thread.wait()
            if error:
                raise BridgeError(export, error)
            return self.status()["request_id"]
        finally:
            if address:
                self.process.free(address)

    def install(self) -> dict:
        self._request("PdxCK3InstallInterfaceHook")
        return self.status()

    def queue_character(self, full_id: int) -> int:
        if not 0 <= full_id <= 0xFFFFFFFF:
            raise ValueError("full_id must fit uint32")
        return self._request("PdxCK3QueueCharacter", struct.pack("<II", 8, full_id))

    def queue_callback(self, callback: int, opaque: int = 0) -> int:
        """Raw native callback; caller owns code and opaque storage through completion."""
        if callback <= 0 or opaque < 0:
            raise ValueError("callback must be nonzero and opaque nonnegative")
        return self._request("PdxCK3QueueInterfaceCallback", struct.pack("<IIQQ", 24, 0, callback, opaque))

    def remove(self) -> int:
        return self._request("PdxCK3RemoveInterfaceHook")

    def status(self) -> dict:
        """One raw sampled copy; native status export also exists for in-process users."""
        data = self.process.read(self.state_address, ctypes.sizeof(QueryStatus))
        value = QueryStatus.from_buffer_copy(data)
        result = {name: getattr(value, name) for name, _ in QueryStatus._fields_ if name != "character"}
        result["character"] = {name: getattr(value.character, name) for name, _ in CharacterResult._fields_}
        result["character"]["health_raw64_hex"] = f"0x{value.character.health_raw64:016x}"
        result["health_interpretation"] = {"scale": "unknown", "signedness": "unknown", "unit": "unknown"}
        result["sample_kind"] = "native_interface_callback_status"
        result["consistent_status_copy"] = False
        return result
