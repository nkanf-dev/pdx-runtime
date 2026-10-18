"""Shared Windows x64 process observation primitives.

No game, executable name, build identity, or memory layout is selected here.
Callers supply the executable name and addresses. Reads are separate external
observations, not a locked or atomic snapshot. Importing this module opens no
process and loads no Windows library; WindowsAPI initialization binds kernel32.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import os

# CLI input limit only. WindowsProcess.read itself has no game/layout gate.
MAX_RAW_BYTES = 1024 * 1024

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20
TOKEN_INTEGRITY_LEVEL = 25
ERROR_INSUFFICIENT_BUFFER = 122
# Each failure-only integrity query uses at most two calls and a bounded buffer.
_MAX_DIAGNOSTIC_TOKEN_BYTES = 65536
_INTEGRITY_NAMES = {
    0x0000: "untrusted", 0x1000: "low", 0x2000: "medium",
    0x2100: "medium_plus", 0x3000: "high", 0x4000: "system",
    0x5000: "protected_process",
}
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
ERROR_NO_MORE_FILES = 18
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class QueryFailure(Exception):
    def __init__(self, status: str, **details):
        self.status = status
        self.details = details
        super().__init__(status)

    def as_dict(self) -> dict:
        return {"status": self.status, **self.details}


class ReadFailure(QueryFailure):
    pass


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD), ("modBaseAddr", ctypes.POINTER(ctypes.c_ubyte)),
        ("modBaseSize", wintypes.DWORD), ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256), ("szExePath", wintypes.WCHAR * 260),
    ]


class WindowsAPI:
    def __init__(self):
        if os.name != "nt":
            raise QueryFailure("unsupported_platform", required="64-bit Windows")
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise QueryFailure("unsupported_python", required="64-bit Python")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.open_process = kernel.OpenProcess
        self.open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.open_process.restype = wintypes.HANDLE
        self.close_handle = kernel.CloseHandle
        self.close_handle.argtypes = [wintypes.HANDLE]
        self.close_handle.restype = wintypes.BOOL
        self.read_process_memory = kernel.ReadProcessMemory
        self.read_process_memory.argtypes = [
            wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
        ]
        self.read_process_memory.restype = wintypes.BOOL
        self.create_snapshot = kernel.CreateToolhelp32Snapshot
        self.create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.create_snapshot.restype = wintypes.HANDLE
        self.process_first = kernel.Process32FirstW
        self.process_next = kernel.Process32NextW
        for function in (self.process_first, self.process_next):
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            function.restype = wintypes.BOOL
        self.module_first = kernel.Module32FirstW
        self.module_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        self.module_first.restype = wintypes.BOOL
        self.query_image = kernel.QueryFullProcessImageNameW
        self.query_image.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.query_image.restype = wintypes.BOOL

    @staticmethod
    def failure(operation: str) -> QueryFailure:
        return QueryFailure("windows_api_failed", operation=operation,
                            winerror=ctypes.get_last_error())

    def diagnose_process_access(self, pid: int, requested_access: int, *,
                                original_winerror: int) -> dict:
        """Best-effort observations after a failure, never an access preflight.

        Capture the original native error BEFORE calling this method. It remains
        separate from diagnostic errors, including cleanup errors. No permissions
        are changed. Only temporary limited-query process and TOKEN_QUERY handles
        are opened; the current-process pseudo-handle is never closed.

        These are separate observations of process primary tokens, not an atomic
        snapshot or an effective thread-token/DACL access check. An integrity
        difference alone does not prove why Windows denied the original request.
        Missing observations stay None; unknown integrity RIDs remain raw.
        """
        def observation(process_id):
            return {"pid": process_id, "path": None, "integrity_rid": None,
                    "integrity": None, "elevated": None}

        result = {"pid": pid, "requested_access": requested_access,
                  "original_winerror": original_winerror,
                  "token_scope": "process_primary",
                  "current": observation(os.getpid()),
                  "target": observation(pid), "errors": []}

        def attempt(subject, operation, action):
            try:
                return action()
            except QueryFailure as exc:
                result["errors"].append({"subject": subject, "operation": operation,
                                         "winerror": None, **exc.as_dict()})
            except Exception as exc:
                # Loading a DLL or an injected API can fail outside Win32 BOOL
                # conventions. Do not substitute a stale GetLastError value.
                result["errors"].append({
                    "subject": subject, "operation": operation,
                    "winerror": getattr(exc, "winerror", None),
                    "exception_type": type(exc).__name__, "message": str(exc)})
            return None

        def close_owned(handle):
            if not self.close_handle(handle):
                raise WindowsAPI.failure("CloseHandle")

        def image_path(handle):
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if not self.query_image(handle, 0, buffer, ctypes.byref(length)):
                raise WindowsAPI.failure("QueryFullProcessImageNameW")
            return buffer.value

        tokens = attempt("diagnostics", "bind_access_diagnostics", _AccessDiagnosticAPI)

        def observe_process(subject, handle):
            item = result[subject]
            item["path"] = attempt(subject, "QueryFullProcessImageNameW",
                                   lambda: image_path(handle))
            if tokens is None:
                return
            token = attempt(subject, "OpenProcessToken",
                            lambda: tokens.open_token(handle))
            if token is None:
                return
            try:
                item["elevated"] = attempt(subject, "GetTokenInformation(TokenElevation)",
                                           lambda: tokens.elevated(token))
                rid = attempt(subject, "GetTokenInformation(TokenIntegrityLevel)",
                              lambda: tokens.integrity_rid(token))
                item["integrity_rid"] = rid
                item["integrity"] = _INTEGRITY_NAMES.get(rid)
            finally:
                attempt(subject, "CloseHandle(token)", lambda: close_owned(token))

        if tokens is not None:
            current = attempt("current", "GetCurrentProcess", tokens.current_process)
            if current is not None:
                attempt("current", "observe_process", lambda: observe_process("current", current))

        def open_target():
            handle = self.open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                raise QueryFailure("windows_api_failed", operation="OpenProcess",
                                   winerror=ctypes.get_last_error(),
                                   requested_access=PROCESS_QUERY_LIMITED_INFORMATION)
            return handle

        target = attempt("target", "OpenProcess", open_target)
        if target is not None:
            try:
                attempt("target", "observe_process", lambda: observe_process("target", target))
            finally:
                attempt("target", "CloseHandle(process)", lambda: close_owned(target))
        return result

    def snapshot(self, flags: int, pid: int) -> int:
        handle = self.create_snapshot(flags, pid)
        if handle == INVALID_HANDLE_VALUE:
            raise self.failure("CreateToolhelp32Snapshot")
        return handle

    def find_processes(self, name: str) -> list[int]:
        """Find a caller-selected executable; no game-specific default is chosen."""
        handle = self.snapshot(TH32CS_SNAPPROCESS, 0)
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            result = []
            available = self.process_first(handle, ctypes.byref(entry))
            while available:
                if entry.szExeFile.casefold() == name.casefold():
                    result.append(int(entry.th32ProcessID))
                available = self.process_next(handle, ctypes.byref(entry))
            if ctypes.get_last_error() != ERROR_NO_MORE_FILES:
                raise self.failure("Process32FirstW/Process32NextW")
            return result
        finally:
            self.close_handle(handle)

    def main_module(self, pid: int) -> dict:
        handle = self.snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not self.module_first(handle, ctypes.byref(entry)):
                raise self.failure("Module32FirstW")
            return {"name": entry.szModule, "path": entry.szExePath,
                    "base": ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value,
                    "size": int(entry.modBaseSize)}
        finally:
            self.close_handle(handle)


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]


class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", _SID_AND_ATTRIBUTES)]


class _AccessDiagnosticAPI:
    """Lazy, read-only token bindings used only by failure diagnostics."""

    def __init__(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        security = ctypes.WinDLL("advapi32", use_last_error=True)
        self.current_process = kernel.GetCurrentProcess
        self.current_process.argtypes = []
        self.current_process.restype = wintypes.HANDLE
        signatures = {
            "open_process_token": ("OpenProcessToken", [wintypes.HANDLE, wintypes.DWORD,
                ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL),
            "get_token_information": ("GetTokenInformation", [wintypes.HANDLE, ctypes.c_int,
                wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "sid_subauthority_count": ("GetSidSubAuthorityCount", [wintypes.LPVOID],
                ctypes.POINTER(wintypes.BYTE)),
            "sid_subauthority": ("GetSidSubAuthority", [wintypes.LPVOID, wintypes.DWORD],
                ctypes.POINTER(wintypes.DWORD)),
        }
        for attribute, (name, arguments, restype) in signatures.items():
            function = getattr(security, name)
            function.argtypes, function.restype = arguments, restype
            setattr(self, attribute, function)

    def open_token(self, process):
        token = wintypes.HANDLE()
        if not self.open_process_token(process, TOKEN_QUERY, ctypes.byref(token)):
            raise WindowsAPI.failure("OpenProcessToken")
        if not token.value:
            raise QueryFailure("invalid_diagnostic_result", operation="OpenProcessToken",
                               reason="successful call returned no token handle")
        return token.value

    def elevated(self, token) -> bool:
        # TOKEN_ELEVATION contains one DWORD (TokenIsElevated).
        value, received = wintypes.DWORD(), wintypes.DWORD()
        operation = "GetTokenInformation(TokenElevation)"
        if not self.get_token_information(token, TOKEN_ELEVATION, ctypes.byref(value),
                                          ctypes.sizeof(value), ctypes.byref(received)):
            raise WindowsAPI.failure(operation)
        if received.value != ctypes.sizeof(value):
            raise QueryFailure("invalid_diagnostic_result", operation=operation,
                               returned_bytes=received.value)
        return bool(value.value)

    def integrity_rid(self, token) -> int:
        operation = "GetTokenInformation(TokenIntegrityLevel)"
        needed = wintypes.DWORD()
        ok = self.get_token_information(token, TOKEN_INTEGRITY_LEVEL, None, 0,
                                         ctypes.byref(needed))
        error = ctypes.get_last_error() if not ok else None
        if not ok and error != ERROR_INSUFFICIENT_BUFFER:
            raise QueryFailure("windows_api_failed", operation=operation, winerror=error)
        minimum = ctypes.sizeof(_TOKEN_MANDATORY_LABEL)
        if not minimum <= needed.value <= _MAX_DIAGNOSTIC_TOKEN_BYTES:
            raise QueryFailure("invalid_diagnostic_result", operation=operation,
                               winerror=error, required_bytes=needed.value)
        buffer = ctypes.create_string_buffer(needed.value)
        received = wintypes.DWORD()
        if not self.get_token_information(token, TOKEN_INTEGRITY_LEVEL, buffer, len(buffer),
                                          ctypes.byref(received)):
            raise WindowsAPI.failure(operation)
        if not minimum <= received.value <= len(buffer):
            raise QueryFailure("invalid_diagnostic_result", operation=operation,
                               returned_bytes=received.value)
        start, end = ctypes.addressof(buffer), ctypes.addressof(buffer) + received.value
        sid = _TOKEN_MANDATORY_LABEL.from_buffer(buffer).Label.Sid

        def require_inside(pointer, size):
            address = ctypes.cast(pointer, ctypes.c_void_p).value if pointer else None
            if address is None or not start + minimum <= address <= end - size:
                raise QueryFailure("invalid_diagnostic_result", operation=operation,
                                   reason="token SID points outside returned buffer")

        # SID has an 8-byte header followed by up to 15 DWORD subauthorities.
        # Check each native pointer before dereferencing; never walk arbitrary memory.
        require_inside(sid, 8)
        count_pointer = self.sid_subauthority_count(sid)
        require_inside(count_pointer, ctypes.sizeof(wintypes.BYTE))
        count = count_pointer.contents.value
        if not 1 <= count <= 15:
            raise QueryFailure("invalid_diagnostic_result", operation=operation,
                               reason="invalid token SID subauthority count", count=count)
        require_inside(sid, 8 + count * ctypes.sizeof(wintypes.DWORD))
        rid_pointer = self.sid_subauthority(sid, count - 1)
        require_inside(rid_pointer, ctypes.sizeof(wintypes.DWORD))
        return int(rid_pointer.contents.value)


class WindowsProcess:
    def __init__(self, api: WindowsAPI, pid: int):
        self.api = api
        self.handle = api.open_process(
            PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not self.handle:
            raise api.failure("OpenProcess")

    def close(self):
        if self.handle:
            self.api.close_handle(self.handle)
            self.handle = None

    def image_path(self) -> str:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not self.api.query_image(self.handle, 0, buffer, ctypes.byref(length)):
            raise self.api.failure("QueryFullProcessImageNameW")
        return buffer.value

    def read(self, address: int, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        success = self.api.read_process_memory(
            self.handle, address, buffer, size, ctypes.byref(count))
        if not success or count.value != size:
            raise ReadFailure("read_failed", operation="ReadProcessMemory",
                              winerror=ctypes.get_last_error(),
                              requested_bytes=size, received_bytes=count.value)
        return buffer.raw


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def positive_pid(value: str) -> int:
    number = int(value, 0)
    if not 0 < number <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("PID must be between 1 and 4294967295")
    return number


def address_argument(value: str) -> int:
    number = int(value, 0)
    if not 0 <= number <= 0xFFFFFFFFFFFFFFFF:
        raise argparse.ArgumentTypeError("address/RVA must fit uint64")
    return number


def size_argument(value: str) -> int:
    number = int(value, 0)
    if not 1 <= number <= MAX_RAW_BYTES:
        raise argparse.ArgumentTypeError(f"size must be between 1 and {MAX_RAW_BYTES} bytes")
    return number
