"""Reusable Windows x64 DLL loader and a small native named-pipe client.

Low-level process memory, allocation, export resolution and thread APIs stay public.
The carrier worker handles raw memory only. Game calls require a separately proven
native execution-context hook, never an arbitrary call on this worker.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from collections.abc import Mapping
from ctypes import wintypes
import json
import os
from pathlib import Path
import struct
import threading
from time import monotonic
from subprocess import list2cmdline
from typing import Iterable

from .windows import (WindowsAPI, MODULEENTRY32W, INVALID_HANDLE_VALUE,
                         TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32, ERROR_NO_MORE_FILES)

PROTOCOL = 1
MAX_RAW = 4096
PROCESS_NATIVE_ACCESS = 0x0002 | 0x0008 | 0x0010 | 0x0020 | 0x0400 | 0x00100000
MEM_COMMIT_RESERVE = 0x1000 | 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
INFINITE = 0xFFFFFFFF
ERROR_ALREADY_INITIALIZED = 1247
RUNTIME_DLL = "pdx_runtime.dll"


def runtime_dll_path() -> Path:
    """The one native artifact shipped inside the installed Python package."""
    return Path(__file__).resolve().parent / "_native" / RUNTIME_DLL


class BridgeError(RuntimeError):
    def __init__(self, operation: str, winerror: int = 0, **details):
        self.operation, self.winerror, self.details = operation, winerror, details
        super().__init__(f"{operation}: Win32 error {winerror}; {details}")

    def as_dict(self) -> dict:
        return {"status": "error", "operation": self.operation,
                "error": self.winerror, **self.details}

    def add_context(self, **details) -> BridgeError:
        """Keep this original error and its code; append observed context only."""
        for name, value in details.items():
            self.details.setdefault(name, value)
        self.args = (f"{self.operation}: Win32 error {self.winerror}; {self.details}",)
        return self


def _error_info(error: Exception) -> dict:
    return {"type": type(error).__name__, "message": str(error),
            "winerror": getattr(error, "winerror", None)}


def _access_context(api, pid: int, requested_access: int, error: BridgeError) -> dict:
    """Failure-only observations. Never replace the saved native error."""
    try:
        result = api.diagnose_process_access(pid, requested_access,
                                            original_winerror=error.winerror)
        if not isinstance(result, dict):
            raise TypeError("access diagnostics did not return a dictionary")
        return result
    except Exception as diagnostic_error:
        return {"pid": pid, "requested_access": requested_access,
                "original_winerror": error.winerror,
                "errors": [_error_info(diagnostic_error)]}


def pipe_name(pid: int) -> str:
    return rf"\\.\pipe\pdx-runtime-{pid}"


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", wintypes.LPVOID), ("AllocationBase", wintypes.LPVOID),
                ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]


@dataclass(frozen=True)
class MemoryRegion:
    base_address: int
    allocation_base: int
    allocation_protection: int
    partition_id: int
    size: int
    state: int
    protection: int
    type: int


class NativeWindowsAPI(WindowsAPI):
    def __init__(self):
        super().__init__()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "module_next": ("Module32NextW", [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)], wintypes.BOOL),
            "allocate": ("VirtualAllocEx", [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD], wintypes.LPVOID),
            "free": ("VirtualFreeEx", [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD], wintypes.BOOL),
            "query_memory": ("VirtualQueryEx", [wintypes.HANDLE, wintypes.LPCVOID,
                ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t], ctypes.c_size_t),
            "protect_memory": ("VirtualProtectEx", [wintypes.HANDLE, wintypes.LPVOID,
                ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "flush_instruction_cache": ("FlushInstructionCache", [wintypes.HANDLE,
                wintypes.LPCVOID, ctypes.c_size_t], wintypes.BOOL),
            "write_process_memory": ("WriteProcessMemory", [wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCVOID, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
            "create_thread": ("CreateRemoteThread", [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.HANDLE),
            "wait": ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "thread_exit": ("GetExitCodeThread", [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "process_exit": ("GetExitCodeProcess", [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "create_process": ("CreateProcessW", [wintypes.LPCWSTR, wintypes.LPWSTR,
                wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD,
                wintypes.LPVOID, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
                ctypes.POINTER(PROCESS_INFORMATION)], wintypes.BOOL),
            "create_file": ("CreateFileW", [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE),
            "wait_pipe": ("WaitNamedPipeW", [wintypes.LPCWSTR, wintypes.DWORD], wintypes.BOOL),
            "pipe_mode": ("SetNamedPipeHandleState", [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID, wintypes.LPVOID], wintypes.BOOL),
            "write_file": ("WriteFile", [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID], wintypes.BOOL),
            "read_file": ("ReadFile", [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID], wintypes.BOOL),
        }
        for attribute, (name, arguments, result) in signatures.items():
            function = getattr(kernel, name)
            function.argtypes, function.restype = arguments, result
            setattr(self, attribute, function)

    @staticmethod
    def failure(operation: str) -> BridgeError:
        return BridgeError(operation, ctypes.get_last_error())

    def modules(self, pid: int) -> list[dict]:
        handle = self.snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            result = []
            available = self.module_first(handle, ctypes.byref(entry))
            while available:
                result.append({"name": entry.szModule, "path": entry.szExePath,
                               "base": ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value,
                               "size": int(entry.modBaseSize)})
                available = self.module_next(handle, ctypes.byref(entry))
            if ctypes.get_last_error() != ERROR_NO_MORE_FILES:
                raise self.failure("Module32FirstW/Module32NextW")
            return result
        finally:
            self.close_handle(handle)


class RemoteThread:
    """A thread result is a DWORD, not an x64 address or HMODULE."""
    def __init__(self, api: NativeWindowsAPI, handle: int, tid: int):
        self.api, self.handle, self.tid = api, handle, tid

    def poll(self) -> int | None:
        """Sample this original thread handle once; None pending, else exit DWORD.

        A real exit259 is not confused with STILL_ACTIVE: native signaling is
        checked before GetExitCodeThread. No wait loop, close or replay occurs.
        """
        if not self.handle:
            raise RuntimeError("thread observation handle is closed")
        result = self.api.wait(self.handle, 0)
        if result == 258:
            return None
        if result != 0:
            if result == INFINITE:
                raise self.api.failure("WaitForSingleObject(thread)")
            raise BridgeError("WaitForSingleObject(thread)", wait_result=result, tid=self.tid)
        code = wintypes.DWORD()
        if not self.api.thread_exit(self.handle, ctypes.byref(code)):
            raise self.api.failure("GetExitCodeThread")
        return code.value

    def wait(self, timeout_ms: int = INFINITE) -> int:
        result = self.api.wait(self.handle, timeout_ms)
        if result != 0:
            raise BridgeError("WaitForSingleObject", ctypes.get_last_error() if result == INFINITE else 0,
                              wait_result=result, tid=self.tid)
        code = wintypes.DWORD()
        if not self.api.thread_exit(self.handle, ctypes.byref(code)):
            raise self.api.failure("GetExitCodeThread")
        return code.value

    def close(self):
        if self.handle:
            self.api.close_handle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


class LibraryLoadPending(BridgeError):
    """The original load is retained. Resume ``attempt``; do not dispatch again."""
    def __init__(self, attempt: LibraryLoadAttempt):
        self.attempt = self.native_load = attempt
        super().__init__("LoadLibraryW", reason="original load needs continuation",
                         path=str(attempt.path), state=attempt.state,
                         tid=attempt.thread.tid if attempt.thread else None,
                         argument_address=attempt.argument_address,
                         loader_exit_dword=attempt.loader_exit_dword,
                         observation_error=str(attempt.last_error) if attempt.last_error else None)


class LibraryLoadAttempt:
    """Own one original loader thread and its input until completion is known.

    The process keeps only unfinished attempts. This object retains the original
    process too, so context exit can defer closing its handle until resume() has
    freed the input. There is no global registry, PID reopen, replay, or worker.
    Keep a pending exception's attempt to continue it after a ``with`` block.
    """
    def __init__(self, process: RemoteProcess, path: Path, key: str, loader: int):
        self.process, self.path, self.key = process, path, key
        self.loader_address = loader
        self.argument_address: int | None = None
        self.thread: RemoteThread | None = None
        self.loader_exit_dword: int | None = None
        self.last_error: Exception | None = None
        self._result: dict | None = None
        self._error: Exception | None = None
        self.state = "preparing"

    def _retire(self):
        self.process._library_loads.pop(self.key, None)
        if self.process._close_requested:
            self.process.close()

    def _cleanup(self):
        # Called only before dispatch, or after the original thread is signaled
        # AND its exit DWORD has been read. Visibility is not completion proof.
        if self.argument_address is not None:
            self.process.free(self.argument_address)
            self.argument_address = None
        if self.thread is not None and self.thread.handle:
            # RemoteThread.close() discards its handle without checking the
            # native result. This owner must keep failed cleanup resumable.
            if not self.process.api.close_handle(self.thread.handle):
                raise self.process.api.failure("CloseHandle(loader thread)")
            self.thread.handle = None

    def resume(self) -> dict:
        """Observe/finish this same attempt once; pending raises with this object.

        Successful explicit continuation is idempotent. A later *new*
        process.load_library(path) still performs a new native LoadLibraryW call.
        """
        if self._result is not None:
            return self._result
        if self.state == "failed":
            raise self._error
        if self.thread is not None and self.loader_exit_dword is None:
            self.state = "waiting"
            try:
                code = self.thread.poll()
            except Exception as error:
                self.last_error = error
                raise LibraryLoadPending(self) from error
            if code is None:
                raise LibraryLoadPending(self) from self.last_error
            self.loader_exit_dword = code
        self.state = "cleanup"
        try:
            self._cleanup()
        except Exception as error:
            self.last_error = error
            raise LibraryLoadPending(self) from error
        # A known pre-dispatch error can require cleanup continuation, but must
        # never be converted into a new load or a module-visibility success.
        if self._error is not None:
            self.state = "failed"
            self._retire()
            raise self._error
        self.state = "module"
        try:
            module = next((item for item in self.process.modules()
                           if os.path.normcase(os.path.abspath(item["path"])) == self.key), None)
        except Exception as error:
            self.last_error = error
            raise LibraryLoadPending(self) from error
        if module is None:
            self._error = BridgeError("LoadLibraryW", reason="requested module absent after remote thread",
                                      path=str(self.path), loader_exit_dword=self.loader_exit_dword)
            self.state = "failed"
            self._retire()
            raise self._error
        self._result = {**module, "loader_address": self.loader_address,
                        "loader_exit_dword": self.loader_exit_dword}
        self.state = "completed"
        self._retire()
        return self._result


class RemoteProcess:
    """Explicit low-level Windows primitives. No game ABI or layout gate."""
    def __init__(self, pid: int, api: NativeWindowsAPI | None = None):
        self.api, self.pid = api or NativeWindowsAPI(), pid
        self._library_loads: dict[str, LibraryLoadAttempt] = {}
        self._close_requested = False
        self.handle = self.api.open_process(PROCESS_NATIVE_ACCESS, False, pid)
        if not self.handle:
            error = self.api.failure("OpenProcess")
            error.add_context(stage="open_process", target_pid=pid,
                              requested_access=PROCESS_NATIVE_ACCESS)
            if error.winerror == 5:
                error.add_context(access_diagnostics=_access_context(
                    self.api, pid, PROCESS_NATIVE_ACCESS, error),
                    hint="Windows denied the requested process rights. Compare the observed "
                         "controller and target access levels; no permissions were changed.")
            raise error

    @classmethod
    def launch(cls, executable: str | Path, arguments: Iterable[str] = (), *,
               cwd: str | Path | None = None, creation_flags: int = 0,
               api: NativeWindowsAPI | None = None) -> RemoteProcess:
        """Create one Windows process and retain its original OS handle.

        Arguments are separate strings, encoded using Windows CRT quoting, not
        shell text. No game/debug flags, DLL loading, hook installation or world
        entry are implicit. Environment is inherited; handles are not inherited.
        The primary thread handle is closed immediately. close() releases only
        our observation handle, never terminates the launched process.
        """
        if isinstance(arguments, (str, bytes)):
            raise TypeError("arguments must be an iterable of strings, not command text")
        executable = str(Path(executable).resolve())
        arguments = tuple(arguments)
        if any(not isinstance(argument, str) for argument in arguments):
            raise TypeError("process arguments must be strings")
        cwd = str(Path(cwd).resolve()) if cwd is not None else None
        if any("\0" in text for text in (executable, *arguments, *((cwd,) if cwd else ()))):
            raise ValueError("process paths and arguments cannot contain NUL")
        command_line = list2cmdline((executable, *arguments))
        command = ctypes.create_unicode_buffer(command_line)
        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        information = PROCESS_INFORMATION()
        api = api or NativeWindowsAPI()
        if not api.create_process(executable, command, None, None, False,
                                  creation_flags, None, cwd,
                                  ctypes.byref(startup), ctypes.byref(information)):
            raise api.failure("CreateProcessW")
        process = cls.__new__(cls)
        process.api, process.pid = api, information.dwProcessId
        process._library_loads = {}
        process._close_requested = False
        process.handle = information.hProcess
        process.executable, process.arguments = executable, arguments
        process.command_line = command_line
        process.primary_thread_id = information.dwThreadId
        api.close_handle(information.hThread)
        return process

    @property
    def pending_library_loads(self) -> tuple[LibraryLoadAttempt, ...]:
        """Actionable original attempts; each supports one-shot resume()."""
        return tuple(self._library_loads.values())

    def close(self):
        # A pending LoadLibraryW still owns input in this exact process. Keep
        # its original handle usable for eventual VirtualFreeEx, even on context
        # exit. The last retired attempt completes this requested close.
        self._close_requested = True
        if self._library_loads:
            return
        if self.handle:
            self.api.close_handle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def modules(self) -> list[dict]:
        return self.api.modules(self.pid)

    def poll(self) -> int | None:
        """Read this retained process handle once: None running, else exit DWORD.

        Native quit acknowledgement and pipe disconnection are not process exit.
        Keep this object open before submitting quit; its handle remains tied to
        that process even if the OS later reuses its PID. This never waits, closes
        the process, or replays a native operation.
        """
        if not self.handle:
            raise RuntimeError("process observation handle is closed")
        result = self.api.wait(self.handle, 0)
        if result == 258:  # WAIT_TIMEOUT: the process object is not signaled.
            return None
        if result != 0:
            if result == INFINITE:
                raise self.api.failure("WaitForSingleObject(process)")
            raise BridgeError("WaitForSingleObject(process)", wait_result=result, pid=self.pid)
        code = wintypes.DWORD()
        if not self.api.process_exit(self.handle, ctypes.byref(code)):
            raise self.api.failure("GetExitCodeProcess")
        # 259 can itself be a real exit code. The signaled handle, not its value,
        # establishes process completion.
        return code.value

    def read(self, address: int, size: int) -> bytes:
        if size < 0:
            raise ValueError("size must be non-negative")
        buffer = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        if not self.api.read_process_memory(self.handle, address, buffer, size, ctypes.byref(count)):
            raise self.api.failure("ReadProcessMemory")
        if count.value != size:
            raise BridgeError("ReadProcessMemory", 299, expected=size, received=count.value)
        return buffer.raw

    def write(self, address: int, data: bytes) -> int:
        count = ctypes.c_size_t()
        if not self.api.write_process_memory(self.handle, address, data, len(data), ctypes.byref(count)):
            raise self.api.failure("WriteProcessMemory")
        if count.value != len(data):
            raise BridgeError("WriteProcessMemory", 299, expected=len(data), written=count.value)
        return count.value

    def allocate(self, size: int, protection: int = PAGE_READWRITE) -> int:
        address = self.api.allocate(self.handle, None, size, MEM_COMMIT_RESERVE, protection)
        if not address:
            raise self.api.failure("VirtualAllocEx")
        return address

    def free(self, address: int):
        if not self.api.free(self.handle, address, 0, MEM_RELEASE):
            raise self.api.failure("VirtualFreeEx")

    def query_memory(self, address: int) -> MemoryRegion:
        """Copy one native virtual-memory region, including free/reserved regions.

        Raw Windows state/protection/type values are preserved. This observes a
        region at one instant; it does not promise pointee validity or ownership.
        """
        info = MEMORY_BASIC_INFORMATION()
        count = self.api.query_memory(self.handle, address, ctypes.byref(info), ctypes.sizeof(info))
        if not count:
            raise self.api.failure("VirtualQueryEx")
        return MemoryRegion(info.BaseAddress or 0, info.AllocationBase or 0,
                            info.AllocationProtect, info.PartitionId, info.RegionSize,
                            info.State, info.Protect, info.Type)

    def protect(self, address: int, size: int, protection: int) -> int:
        """Set caller-selected page protection; return the first page's old flags.

        This is the exact Windows primitive, not an automatic patch/restore
        mechanism. A range may span pages with different old flags; query them
        separately when the caller needs to restore each original protection.
        """
        old = wintypes.DWORD()
        if not self.api.protect_memory(self.handle, address, size, protection, ctypes.byref(old)):
            raise self.api.failure("VirtualProtectEx")
        return old.value

    def flush_instruction_cache(self, address: int = 0, size: int = 0) -> None:
        """Explicit native instruction-cache flush; no writes or code execution."""
        if not self.api.flush_instruction_cache(self.handle, address, size):
            raise self.api.failure("FlushInstructionCache")

    def start_thread(self, function_address: int, argument_address: int = 0) -> RemoteThread:
        """Only use a verified DWORD WINAPI(void*) entry; this is not a game-call ABI."""
        tid = wintypes.DWORD()
        handle = self.api.create_thread(self.handle, None, 0, function_address, argument_address, 0, ctypes.byref(tid))
        if not handle:
            raise self.api.failure("CreateRemoteThread")
        return RemoteThread(self.api, handle, tid.value)

    def resolve_export(self, module_name: str | int, symbol: str | int, _seen=None, *,
                       aliases: Mapping[str, str | int] | None = None) -> int:
        """Resolve a target export with an optional, explicit module-alias map.

        Loaded module names and exact integer bases always win. Only a missing
        string selector present in aliases may use its one mapped loaded target.
        Alias names/targets are case-insensitive and accept an omitted .dll.
        No distribution or game alias policy is selected by default. Forwarded
        exports retain this exact explicit policy and final export ownership.
        """
        seen = set() if _seen is None else _seen
        identity = module_name.casefold() if isinstance(module_name, str) else module_name
        key = (identity, symbol)
        if key in seen:
            raise BridgeError("resolve_export", reason="forwarder cycle", module=module_name, symbol=symbol)
        seen.add(key)
        normalized = identity
        if isinstance(normalized, str) and not normalized.endswith(".dll"):
            normalized += ".dll"
        loaded = self.modules()
        module = next((item for item in loaded
                       if (item["base"] == normalized if isinstance(normalized, int)
                           else item["name"].casefold() == normalized)), None)
        if module is None and isinstance(normalized, str) and aliases is not None:
            for source, target in aliases.items():
                if not isinstance(source, str):
                    raise TypeError("module alias names must be strings")
                alias = source.casefold()
                if not alias.endswith(".dll"):
                    alias += ".dll"
                if alias != normalized:
                    continue
                if isinstance(target, bool) or not isinstance(target, (str, int)):
                    raise TypeError("module alias targets must be module names or integer bases")
                if isinstance(target, int) and not 0 < target <= 0xffffffffffffffff:
                    raise ValueError("module alias base must be a positive uint64 address")
                if isinstance(target, str) and (not target or "\0" in target):
                    raise ValueError("module alias target must be a non-empty name without NUL")
                target = target.casefold() if isinstance(target, str) else target
                if isinstance(target, str) and not target.endswith(".dll"):
                    target += ".dll"
                module = next((item for item in loaded
                    if (item["base"] == target if isinstance(target, int)
                        else item["name"].casefold() == target)), None)
                break
        if module is None:
            raise BridgeError("resolve_export", reason="module not loaded", module=normalized)
        base = module["base"]
        dos = self.read(base, 64)
        if dos[:2] != b"MZ":
            raise BridgeError("resolve_export", reason="missing DOS header")
        nt = self.read(base + struct.unpack_from("<I", dos, 0x3C)[0], 264)
        if nt[:4] != b"PE\0\0" or struct.unpack_from("<HH", nt, 4)[0] != 0x8664 or struct.unpack_from("<H", nt, 24)[0] != 0x20B:
            raise BridgeError("resolve_export", reason="requires x64 PE32+")
        export_rva, export_size = struct.unpack_from("<II", nt, 24 + 112)
        if not export_rva or not export_size:
            raise BridgeError("resolve_export", reason="no export directory")
        directory = self.read(base + export_rva, export_size)
        ordinal_base, count, names_count, functions_rva, names_rva, ordinals_rva = struct.unpack_from("<IIIIII", directory, 16)
        if count > module["size"] // 4 or names_count > module["size"] // 4:
            raise BridgeError("resolve_export", reason="invalid export count")

        def rva_read(rva, size):
            offset = rva - export_rva
            if 0 <= offset and offset + size <= len(directory):
                return directory[offset:offset + size]
            return self.read(base + rva, size)

        def cstring(rva):
            offset = rva - export_rva
            if 0 <= offset < len(directory):
                return directory[offset:].split(b"\0", 1)[0].decode("ascii")
            data = bytearray()
            for i in range(1024):
                value = self.read(base + rva + i, 1)
                if value == b"\0":
                    return data.decode("ascii")
                data.extend(value)
            raise BridgeError("resolve_export", reason="unterminated export string")

        if isinstance(symbol, int):
            index = symbol - ordinal_base
        else:
            name_rvas = struct.unpack(f"<{names_count}I", rva_read(names_rva, names_count * 4))
            ordinals = struct.unpack(f"<{names_count}H", rva_read(ordinals_rva, names_count * 2))
            index = next((ordinals[i] for i, rva in enumerate(name_rvas) if cstring(rva) == symbol), -1)
        if not 0 <= index < count:
            raise BridgeError("resolve_export", reason="export not found", module=normalized, symbol=symbol)
        function_rva = struct.unpack("<I", rva_read(functions_rva + index * 4, 4))[0]
        if not function_rva:
            raise BridgeError("resolve_export", reason="empty export", symbol=symbol)
        if export_rva <= function_rva < export_rva + export_size:
            forward_module, forward_symbol = cstring(function_rva).rsplit(".", 1)
            forwarded = int(forward_symbol[1:]) if forward_symbol.startswith("#") else forward_symbol
            return self.resolve_export(forward_module, forwarded, seen, aliases=aliases)
        address = base + function_rva
        owners = getattr(self, "_export_owners", None)
        if owners is None:
            self._export_owners = owners = {}
        owners[address] = base
        return address

    def export_owner(self, address: int) -> int:
        """Return the actual module base of an export, not its adapter alias.

        resolve_export records the final target of a forwarded export too.
        Retained native handles can keep this base when default aliases change.
        """
        owner = getattr(self, "_export_owners", {}).get(address)
        if owner is not None:
            return owner
        module = next((item for item in self.modules()
                       if item["base"] <= address < item["base"] + item["size"]), None)
        if module is None:
            raise BridgeError("export_owner", reason="address is not in a resident module", address=address)
        return module["base"]

    def load_library(self, dll_path: str | Path, *, timeout_ms: int = INFINITE) -> dict:
        """Load once, or continue the exact unfinished load of this path.

        The initial wait defaults to the raw infinite wait. Set timeout_ms=0
        to submit without blocking, or select a bounded native wait. Timeout or
        wait failure immediately samples the same thread once, never replays.
        Unknown completion retains both its handle and UTF-16 argument; the
        raised LibraryLoadPending exposes an attempt that can resume even after
        process context exit. Module visibility alone cannot settle a thread.
        """
        path = Path(dll_path).resolve()
        key = os.path.normcase(str(path))
        pending = self._library_loads.get(key)
        if pending is not None:
            return pending.resume()
        if self._close_requested:
            raise RuntimeError("process is closing; only retained library loads can resume")
        if not path.is_file():
            raise FileNotFoundError(path)
        loader = self.resolve_export("kernel32.dll", "LoadLibraryW")
        encoded = (str(path) + "\0").encode("utf-16-le")
        attempt = LibraryLoadAttempt(self, path, key, loader)
        self._library_loads[key] = attempt  # Own resources before any dispatch.
        try:
            attempt.argument_address = self.allocate(len(encoded))
            self.write(attempt.argument_address, encoded)
            attempt.thread = self.start_thread(loader, attempt.argument_address)
        except Exception as error:
            attempt._error = error
            return attempt.resume()  # No dispatch: release owned input, then raise.
        attempt.state = "waiting"
        try:
            attempt.loader_exit_dword = attempt.thread.wait(timeout_ms)
        except Exception as error:
            attempt.last_error = error
        return attempt.resume()


class NativeBridge:
    """Independent pipe client. Compose raw primitives in any Python host."""
    def __init__(self, pid: int, api: NativeWindowsAPI | None = None):
        self.api, self.pid = api or NativeWindowsAPI(), pid
        self.handle = None
        self.load_info = None
        self.connection_info = {"target_pid": pid, "pipe_name": pipe_name(pid),
                                "client_owner_pid": None, "owner_source": "unavailable"}
        self._lock = threading.Lock()
        self._generation = 0
        self._failed_connection = None
        self._recovery_pending = False
        self._transport_error = None

    def _pipe_error(self, operation: str, *, timeout_ms=None, requested_access=None):
        # Save last-error before diagnostics or cleanup can change it.
        error = self.api.failure(operation)
        error.add_context(stage={"WaitNamedPipeW": "wait_pipe", "CreateFileW(pipe)": "open_pipe",
                                 "SetNamedPipeHandleState": "pipe_mode"}.get(operation, "pipe_io"),
                          target_pid=self.pid, pipe_name=pipe_name(self.pid),
                          pipe_handle=self.handle, timeout_ms=timeout_ms,
                          client_owner_pid=None, owner_source="unavailable")
        if requested_access is not None:
            error.add_context(requested_access=requested_access)
        if error.winerror == 5:
            error.add_context(access_diagnostics=_access_context(
                self.api, self.pid, requested_access or 0, error),
                requested_access_applicable=requested_access is not None,
                hint="Windows denied pipe access. Process-token observations are context, "
                     "not proof of the pipe access rule; no permissions were changed.")
        elif error.winerror in (121, 231):
            error.add_context(endpoint_observation="busy", hint=
                "The endpoint currently has no available pipe instance. This does not "
                "identify another client's native operation or permit another DLL load.")
        elif error.winerror == 2:
            error.add_context(endpoint_observation="absent")
        return error

    @classmethod
    def connect(cls, pid: int, timeout_ms: int = 5000) -> NativeBridge:
        """Acquire one independent pipe; zero makes one nonblocking open attempt.

        WaitNamedPipe does not reserve its observed instance. If another opener
        wins CreateFile, only acquisition continues within the same total wait
        budget. No request has been sent and no native operation is replayed.
        """
        if not isinstance(timeout_ms, int) or not 0 <= timeout_ms <= INFINITE:
            raise ValueError("timeout_ms must be 0..INFINITE")
        bridge = cls(pid)
        name = pipe_name(pid)
        access = 0x80000000 | 0x40000000
        deadline = (monotonic() + timeout_ms / 1000
                    if timeout_ms not in (0, INFINITE) else None)
        wait_ms = timeout_ms
        while True:
            # WaitNamedPipe(0) is the server's DEFAULT wait, not a zero probe.
            if wait_ms and not bridge.api.wait_pipe(name, wait_ms):
                raise bridge._pipe_error("WaitNamedPipeW", timeout_ms=wait_ms).add_context(
                    delivery="not_sent")
            bridge.handle = bridge.api.create_file(name, access, 0, None, 3, 0, None)
            if bridge.handle != INVALID_HANDLE_VALUE:
                break
            error = bridge._pipe_error("CreateFileW(pipe)", timeout_ms=wait_ms,
                                       requested_access=access).add_context(delivery="not_sent")
            bridge.handle = None
            if error.winerror != 231 or not timeout_ms:
                raise error
            if deadline is not None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise error  # Keep the last real OS error, not a made-up timeout.
                wait_ms = max(1, int(remaining * 1000))
            # Only an unacquired instance can reach here. The server publishes
            # more instances independently of other clients' idle/native work.
        mode = wintypes.DWORD(2)  # PIPE_READMODE_MESSAGE
        if not bridge.api.pipe_mode(bridge.handle, ctypes.byref(mode), None, None):
            error = bridge._pipe_error("SetNamedPipeHandleState", timeout_ms=timeout_ms)
            error.client = bridge  # Retain an actionable owner if cleanup fails.
            bridge.close()
            if bridge.connection_info.get("cleanup_errors"):
                error.add_context(cleanup_errors=bridge.connection_info["cleanup_errors"])
            raise error
        return bridge

    @classmethod
    def attach(cls, pid: int, dll_path: str | Path | None = None, *,
               process: RemoteProcess | None = None, timeout_ms: int = 0) -> NativeBridge:
        """Connect or reuse a resident runtime before loading one missing carrier.

        Pending errors retain ``connection_attempt``. Resume that exact object;
        calling attach again creates a new operation, not its continuation.
        A busy endpoint never permits injection. No status request is implicit.
        """
        return ConnectionAttempt(pid, dll_path, process=process,
                                 bridge_type=cls).resume(timeout_ms=timeout_ms)

    @classmethod
    def load(cls, pid: int, dll_path: str | Path | None = None) -> NativeBridge:
        """Explicitly load the selected DLL, initialize it, then connect.

        Unlike attach(), this retains the raw explicit-load semantics. Pending
        work exposes the same original loader/initializer through its operation.
        """
        return ConnectionAttempt(pid, dll_path, bridge_type=cls,
                                 explicit_load=True).resume(timeout_ms=INFINITE,
                                                           connect_timeout_ms=5000)

    def close(self):
        """Close only this client's handle after any active request exchange."""
        with self._lock:
            if self.handle:
                try:
                    if not self.api.close_handle(self.handle):
                        raise self.api.failure("CloseHandle(pipe)")
                except Exception as error:
                    self.connection_info.setdefault("cleanup_errors", []).append(_error_info(error))
                    return  # Keep the local handle available for explicit cleanup.
                self.handle = None
            self._generation += 1
            self._failed_connection = None
            self._recovery_pending = False

    def recover(self, timeout_ms: int = 0) -> NativeBridge:
        """Reconnect this known-failed local client, without resending any request.

        The original object/lock survives. Never close an unfailed connection,
        and never hold the exchange lock across pipe acquisition or caller waits.
        """
        with self._lock:
            if self.handle:
                if self._failed_connection != (self.handle, self._generation):
                    return self
                if not self.api.close_handle(self.handle):
                    raise self._pipe_error("CloseHandle(failed pipe)")
                self.handle = None
                self._generation += 1
                self._failed_connection = None
                self._recovery_pending = True
            if not self._recovery_pending:
                raise BridgeError("reconnect", reason="client was closed, not failed",
                                  target_pid=self.pid, pipe_name=pipe_name(self.pid))
            generation = self._generation
        replacement = type(self).connect(self.pid, timeout_ms=timeout_ms)
        with self._lock:
            if self.handle is None and self._generation == generation and self._recovery_pending:
                self.handle, replacement.handle = replacement.handle, None
                self._generation += 1
                self._recovery_pending = False
                self._transport_error = None
                self.connection_info["recovered"] = True
                return self
            # Another recovery or explicit close won. Never overwrite its handle.
            usable = self.handle is not None
        replacement.close()
        if usable:
            return self
        raise BridgeError("reconnect", reason="client closed while reconnecting",
                          target_pid=self.pid, pipe_name=pipe_name(self.pid))

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def request(self, command: str) -> dict:
        """One correlated write/read exchange; a failed stream cannot be reused."""
        message = command.encode("ascii")
        if not message or len(message) >= MAX_RAW * 2 + 256 or b"\0" in message:
            raise ValueError("request must be non-empty ASCII without NUL and fit the pipe message")
        with self._lock:
            if not self.handle:
                raise BridgeError("request", 6 if self._recovery_pending else 0,
                                  reason="connection closed", target_pid=self.pid,
                                  pipe_name=pipe_name(self.pid), delivery="not_sent")
            if self._failed_connection == (self.handle, self._generation):
                raise BridgeError("request", self._transport_error.winerror,
                                  reason="failed pipe needs recovery", target_pid=self.pid,
                                  pipe_name=pipe_name(self.pid), delivery="not_sent")
            try:
                count = wintypes.DWORD()
                if not self.api.write_file(self.handle, message, len(message), ctypes.byref(count), None):
                    raise self._pipe_error("WriteFile(pipe)")
                if count.value != len(message):
                    raise BridgeError("WriteFile(pipe)", 299, written=count.value,
                                      target_pid=self.pid, pipe_name=pipe_name(self.pid))
                buffer = ctypes.create_string_buffer(MAX_RAW * 2 + 256)
                if not self.api.read_file(self.handle, buffer, len(buffer), ctypes.byref(count), None):
                    raise self._pipe_error("ReadFile(pipe)")
            except BridgeError as error:
                self._failed_connection = (self.handle, self._generation)
                self._transport_error = error
                raise
            return json.loads(buffer.raw[:count.value])

    def status(self) -> dict:
        result = self.request("status")
        # Reuse this requested observation. No handshake or identity receipt gate.
        if isinstance(result, dict) and result.get("status") == "ok":
            self.connection_info["status"] = {key: result[key] for key in
                ("pid", "dll_base", "protocol") if key in result}
        return result

    def read(self, address: int, size: int) -> bytes:
        if not 0 < size <= MAX_RAW:
            raise ValueError(f"size must be 1..{MAX_RAW}")
        result = self.request(f"read {address:#x} {size}")
        if result["status"] != "ok":
            raise BridgeError("native read", result["error"], received=result.get("bytes", 0))
        return bytes.fromhex(result["hex"])

    def write(self, address: int, data: bytes) -> int:
        if not 0 < len(data) <= MAX_RAW:
            raise ValueError(f"data must contain 1..{MAX_RAW} bytes")
        result = self.request(f"write {address:#x} {data.hex()}")
        if result["status"] != "ok":
            raise BridgeError("native write", result["error"], written=result.get("bytes", 0))
        return result["bytes"]


class ConnectionPending(BridgeError):
    """Continue this original operation; no new attach/load dispatch is needed."""
    def __init__(self, attempt: ConnectionAttempt):
        self.connection_attempt = attempt
        last = attempt.last_error
        super().__init__(last.operation if isinstance(last, BridgeError) else "connect runtime",
                         getattr(last, "winerror", 0),
                         **attempt.information(), endpoint_observation=attempt._endpoint_state,
                         hint=(last.details.get("hint") if isinstance(last, BridgeError) else None)
                              or "Resume this original connection attempt; do not submit another load.",
                         observation_error=_error_info(last) if last else None)


class ConnectionAttempt:
    """One shared connect/discover/load/initialize operation, with native owners.

    resume() advances until ready or one pending observation. It never sleeps,
    creates a background task, retries a mutation, or asks for status. The caller
    schedules continuation. LibraryLoadAttempt alone owns the loader state;
    this object only keeps that original attempt and initializer RemoteThread.
    """
    def __init__(self, pid: int, dll_path: str | Path | None = None, *,
                 process: RemoteProcess | None = None, bridge_type=NativeBridge,
                 explicit_load: bool = False):
        if process is not None and process.pid != pid:
            raise ValueError("process and pid refer to different targets")
        self.pid, self.process = pid, process
        self.owns_process = process is None
        self.path = Path(dll_path) if dll_path is not None else runtime_dll_path()
        self.path = self.path.resolve()
        self.bridge_type, self.explicit_load = bridge_type, explicit_load
        self.state = "load" if explicit_load else "connect"
        self.module = None
        self.candidates = []
        self.library_load = None
        self.initializer_thread = None
        self.initialize_code = None
        self.initialize_address = None
        self.bridge = None
        self.last_error = None
        self.diagnostics = {}
        self._may_load = not explicit_load
        self._loaded = False
        self._endpoint_state = None
        self._error = None
        self._process_closed = False

    @property
    def pending_native(self) -> bool:
        return bool((self.library_load is not None and self.module is None and
                     self.library_load.state not in ("completed", "failed")) or
                    (self.initializer_thread is not None and self.initialize_code is None))

    def information(self) -> dict:
        return {"stage": self.state, "target_pid": self.pid,
                "pipe_name": pipe_name(self.pid), "requested_dll_path": str(self.path),
                "candidate_modules": self.candidates, "selected_module": self.module,
                "initialize_address": self.initialize_address,
                "initialize_error": self.initialize_code,
                "ambiguity": self.diagnostics.get("ambiguity"),
                "client_owner_pid": None, "owner_source": "unavailable"}

    def _pending(self, error=None):
        if error is not None:
            self.last_error = error
        raise ConnectionPending(self) from error

    def _observe_endpoint(self, error):
        category = "busy" if error.winerror in (121, 231) else "absent"
        self.last_error = error
        self.diagnostics["connection_error"] = error.as_dict()
        # A busy controller does not authorize an export scan or injection.
        # Copy candidate paths at most once per endpoint state transition.
        if category == "busy" and self._endpoint_state != category:
            try:
                self.candidates = [dict(m) for m in self.process.modules()
                    if m["name"].casefold() == RUNTIME_DLL.casefold()
                    or os.path.normcase(os.path.abspath(m["path"])) == os.path.normcase(str(self.path))]
            except Exception as observation_error:
                self.diagnostics["module_observation_error"] = _error_info(observation_error)
        self._endpoint_state = category
        error.add_context(**self.information())

    def _connect(self, timeout_ms):
        try:
            return self.bridge_type.connect(self.pid, timeout_ms=timeout_ms)
        except BridgeError as error:
            if error.winerror not in (2, 121, 231):
                raise
            self._observe_endpoint(error)
            return None

    def _discover(self):
        key = os.path.normcase(str(self.path))
        pending = [p for p in self.process.pending_library_loads
                   if os.path.normcase(os.path.abspath(p.path)) == key
                   or Path(p.path).name.casefold() == RUNTIME_DLL.casefold()]
        if len(pending) == 1:
            self.library_load = pending[0]
            self._may_load = False
            self.state = "load"
            return
        if len(pending) > 1:
            self.diagnostics["ambiguity"] = "several original runtime loads are unfinished"
            self._may_load = False
            self.state = "connect"
            self._pending()
        try:
            loaded = self.process.modules()
        except BridgeError as error:
            if error.winerror not in (24, 299):
                raise
            self._pending(error)
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
        found = []
        for module in loaded:
            path = Path(module["path"])
            selected = os.path.normcase(os.path.abspath(path)) == key
            if not selected and (not module["name"].casefold().endswith(".dll")
                                 or path.resolve().is_relative_to(system_root)):
                continue
            try:
                address = self.process.resolve_export(module["base"], "PdxRuntimeInitialize")
            except BridgeError as error:
                # Export absence is not a loader observation failure. Never use
                # an unreadable module snapshot as permission to inject again.
                if error.winerror:
                    self._pending(error)
                continue
            found.append({**module, "initialize_address": address})
        self.candidates = found
        if len(found) > 1:
            self.diagnostics["ambiguity"] = "several resident runtimes; serving image is unknown"
            self._may_load = False
            self.state = "connect"
            self._pending()
        if found:
            self.module = found[0]
            self.initialize_address = self.module["initialize_address"]
            self.state = "initialize"
            self._may_load = False
        else:
            self.state = "load"

    def _close_owned_process(self):
        if self.owns_process and self.process is not None and not self._process_closed and not self.pending_native:
            try:
                self.process.close()
                self._process_closed = True
            except Exception as error:
                self.diagnostics.setdefault("cleanup_errors", []).append(_error_info(error))

    def _complete(self, bridge):
        self.bridge = bridge
        if self._loaded:
            bridge.load_info = self.module
        bridge.connection_attempt = self
        self.state = "completed"
        if isinstance(getattr(bridge, "connection_info", None), dict):
            bridge.connection_info.update(self.information())
        self._close_owned_process()
        return bridge

    def resume(self, timeout_ms: int = 0, *, connect_timeout_ms: int | None = None) -> NativeBridge:
        """Observe the original owners once; success is idempotent.

        timeout_ms controls native load/initializer waits. Zero never waits for
        their completion. Pipe acquisition has its own optional wait budget.
        A LibraryLoadPending keeps its original .attempt/.native_load unchanged.
        """
        if self.bridge is not None:
            return self.bridge
        if self._error is not None:
            raise self._error
        pipe_timeout = timeout_ms if connect_timeout_ms is None else connect_timeout_ms
        try:
            if self.process is None:
                self.process = RemoteProcess(self.pid)
            if not self.explicit_load:
                exit_code = self.process.poll()
                if exit_code is not None:
                    raise BridgeError("connect runtime", reason="target process exited", exit_code=exit_code)
            if self.state == "connect":
                bridge = self._connect(pipe_timeout)
                if bridge is not None:
                    return self._complete(bridge)
                if self.last_error.winerror != 2 or not self._may_load:
                    self._pending()
                self.state = "discover"
            if self.state == "discover":
                self._discover()
            if self.state == "load":
                if self.library_load is not None:
                    self.module = self.library_load.resume()
                else:
                    if not self.explicit_load:
                        # Another controller may have initialized after discovery.
                        bridge = self._connect(pipe_timeout)
                        if bridge is not None:
                            return self._complete(bridge)
                        if self.last_error.winerror != 2:
                            self.state = "connect"
                            self._pending()
                    self._may_load = False
                    self.module = (self.process.load_library(self.path) if timeout_ms == INFINITE
                                   else self.process.load_library(self.path, timeout_ms=timeout_ms))
                self._may_load = False
                self._loaded = True
                self.state = "initialize"
            if self.state == "initialize":
                if self.initialize_address is None:
                    self.initialize_address = self.process.resolve_export(self.module["base"], "PdxRuntimeInitialize")
                if self.initializer_thread is None:
                    self.last_error = None
                    self.initializer_thread = self.process.start_thread(self.initialize_address)
                if self.initialize_code is None:
                    try:
                        self.initialize_code = (self.initializer_thread.poll() if timeout_ms == 0
                                                else self.initializer_thread.wait(timeout_ms))
                    except Exception as error:
                        self._pending(error)
                    if self.initialize_code is None:
                        self._pending()
                    try:
                        self.initializer_thread.close()
                    except Exception as error:
                        self.diagnostics.setdefault("cleanup_errors", []).append(_error_info(error))
                    self.module.update(initialize_address=self.initialize_address,
                                       initialize_error=self.initialize_code)
                if self.initialize_code not in (0, ERROR_ALREADY_INITIALIZED):
                    error = BridgeError("PdxRuntimeInitialize", self.initialize_code)
                    if self.explicit_load or self.initialize_code != 231:
                        raise error
                    # A competing endpoint won after our absence observation.
                    # Keep the loaded image/result, never initialize or load again.
                    self.diagnostics["initialize_error"] = error.as_dict()
                self.state = "connect"
                bridge = self._connect(pipe_timeout)
                if bridge is not None:
                    return self._complete(bridge)
                self._pending()
            raise RuntimeError("invalid connection operation stage")
        except LibraryLoadPending as error:
            self.library_load = error.attempt
            error.connection_attempt = self
            error.add_context(**self.information())
            raise
        except ConnectionPending:
            raise
        except Exception as error:
            self._error = error
            error.connection_attempt = self
            if isinstance(error, BridgeError):
                error.add_context(**self.information())
            self._close_owned_process()
            raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--dll", type=Path, help="load DLL before connecting")
    parser.add_argument("command", nargs="*", default=["status"], help="status | read <address> <size> | write <address> <hex>")
    args = parser.parse_args(argv)
    try:
        bridge = NativeBridge.load(args.pid, args.dll) if args.dll else NativeBridge.connect(args.pid)
        with bridge:
            result = bridge.request(" ".join(args.command) if args.command else "status")
            if bridge.load_info:
                result["load"] = bridge.load_info
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "ok" else 1
    except BridgeError as error:
        print(json.dumps(error.as_dict(), indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
