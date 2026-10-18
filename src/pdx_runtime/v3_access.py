"""V3 native access scope around one caller-selected Interface or Session callback.

mode/flag are raw native values, not read/write permissions. Caller chooses the
execution context and proves target legality. No entered guard crosses callback
completion. Low-level synchronous Enter/Leave exports remain usable on one native
stack; do not call them with RemoteThread or split them across callbacks.
"""
from __future__ import annotations

from pdx_runtime.v3_wire import resolve_v3_export
from dataclasses import dataclass
import struct
from typing import Callable, Iterable

from . import native_call, v3, v3_session
from .native_bridge import NativeBridge, RemoteProcess

REQUEST = struct.Struct("<IIII7Q4I")
FLAG = struct.Struct("<i")
PAYLOAD_OFFSET = REQUEST.size


def encode_request(callbackfn: int, opaque: int, source: int, *, mode: int,
                   flag: int, enter: int = 0, leave: int = 0) -> bytes:
    """Pack the public 88-byte scope request with exact process-local addresses.

    mode is uint32 and flag is int32; neither value is given invented permission
    semantics. Zero enter/leave select the observed V3 RVAs. Caller retains all
    referenced memory/code until dispatch completion. This neither owns nor
    dispatches memory and is also usable from the raw public queue APIs.
    """
    flag_bits = struct.unpack("<I", FLAG.pack(flag))[0]
    return REQUEST.pack(REQUEST.size, v3.ERROR_IO_PENDING, mode, flag_bits,
                        callbackfn, opaque, source, enter, leave, 0, 0, 0, 0, 0, 0)


@dataclass(frozen=True)
class AccessResult:
    size: int
    error: int
    mode: int
    flag: int
    callbackfn: int
    opaque: int
    source: int
    enter: int
    leave: int
    context: int
    enter_result: int
    execution_thread_id: int
    entered: int
    left: int
    reserved: int
    payload: bytes
    callee_result: object | None = None

    @classmethod
    def from_bytes(cls, data: bytes, *, payload_size: int = 0,
                   decode_payload: Callable[[bytes], object] | None = None) -> AccessResult:
        fields = list(REQUEST.unpack_from(data))
        fields[3] = FLAG.unpack_from(data, 12)[0]
        payload = data[PAYLOAD_OFFSET:PAYLOAD_OFFSET + payload_size]
        callee = decode_payload(payload) if decode_payload else None
        return cls(*fields, payload, callee)


def submit_callback(client: NativeBridge, callbackfn: int, initial_bytes: bytes = b"", *,
                    mode: int, flag: int, dispatch: str,
                    source: str = "pdx-runtime/v3_access.py",
                    adapter: str | int = "pdx_v3_access.dll",
                    process: RemoteProcess | None = None,
                    session_adapter: str | int = "v3_session_callbacks.dll",
                    call_adapter: str | int = "pdx_native_call.dll",
                    enter: int = 0, leave: int = 0,
                    decode_payload: Callable[[bytes], object] | None = None,
                    prepare_payload: Callable[[int], bytes] | None = None):
    """One owned allocation: scope request + callee opaque bytes + source string.

    ``dispatch`` is explicitly "interface" or "session"; no domain is selected
    silently. callbackfn is exact void WINAPI(context,opaque) code, including a
    caller's own analyzed DLL. prepare_payload(address) patches internal callee
    pointers before queueing and must preserve initial_bytes length. The returned
    existing shared handle exposes scope_address, callee_address, source_address,
    handler_address, prepare_payload and decode_payload, plus client/process and
    request IDs. It stays live through final callback completion, never replayed.
    """
    if dispatch not in ("interface", "session"):
        raise ValueError('dispatch must be "interface" or "session"')
    initial_bytes = bytes(initial_bytes)
    source_bytes = source.encode("utf-8") + b"\0"
    initial = encode_request(callbackfn, 0, 0, mode=mode, flag=flag,
                             enter=enter, leave=leave) + initial_bytes + source_bytes
    payload_size = len(initial_bytes)

    def decode(data):
        return AccessResult.from_bytes(data, payload_size=payload_size,
                                       decode_payload=decode_payload)

    def prepare(address):
        callee_address = address + PAYLOAD_OFFSET
        payload = prepare_payload(callee_address) if prepare_payload else initial_bytes
        if len(payload) != payload_size:
            raise ValueError("prepared payload changed allocation size")
        return encode_request(callbackfn, callee_address,
            callee_address + payload_size, mode=mode, flag=flag, enter=enter,
            leave=leave) + payload + source_bytes

    def expose(handle, address):
        handle.scope_address = address
        handle.callee_address = address + PAYLOAD_OFFSET
        handle.source_address = handle.callee_address + payload_size
        handle.handler_address = callbackfn
        handle.prepare_payload = prepare_payload
        handle.decode_payload = decode_payload
        return handle

    owns_process = process is None
    process = process or RemoteProcess(client.pid)
    try:
        if dispatch == "interface":
            def factory(client, process, callback, address, owns):
                process.write(address, prepare(address))
                return expose(v3.CallbackResult(client, process, callback, address, owns,
                                         len(initial), decode), address)
            handle = v3.submit_callback(client, initial, adapter=adapter,
                callback_symbol="PdxV3AccessCallback", decode_result=decode,
                process=process, handle_factory=factory)
            scope_address = handle.result_address
        else:
            handler = resolve_v3_export(process, adapter, "PdxV3AccessCallback")
            handle = v3_session.submit_callback(client, handler, initial,
                decode_result=decode, session_adapter=session_adapter,
                call_adapter=call_adapter, process=process, _prepare_payload=prepare)
            scope_address = handle.opaque_address
    except Exception as error:
        if hasattr(error, "query"):
            query = error.query
            address = query.opaque_address if dispatch == "session" else query.result_address
            expose(query, address)
            query._owns_process = owns_process
        elif owns_process:
            process.close()
        raise
    handle._owns_process = owns_process
    return expose(handle, scope_address)


def submit_call(client: NativeBridge, target: int, arguments: Iterable[int] = (), *,
                mode: int, flag: int, dispatch: str,
                adapter: str | int = "pdx_v3_access.dll",
                call_adapter: str | int = "pdx_native_call.dll",
                process: RemoteProcess | None = None, **options):
    """Compose the unchanged raw Win64 helper as the callee inside one scope.

    No ABI packing/lifecycle is copied: native_call.encode_request and its raw
    NativeCallResult decoder are reused. AccessResult.error reports scope/helper
    completion, while .callee_result carries the raw target RAX/XMM0. Target,
    pointees and DLL lifetimes plus execution domain remain caller responsibilities.
    """
    arguments = tuple(arguments)
    owns_process = process is None
    process = process or RemoteProcess(client.pid)
    try:
        handler = resolve_v3_export(process, call_adapter, "PdxNativeCallInterface")
        handle = submit_callback(client, handler,
            native_call.encode_request(target, arguments, request_address=0),
            mode=mode, flag=flag, dispatch=dispatch, adapter=adapter,
            call_adapter=call_adapter, process=process,
            decode_payload=native_call.NativeCallResult.from_bytes,
            prepare_payload=lambda address: native_call.encode_request(
                target, arguments, request_address=address), **options)
    except Exception as error:
        if owns_process:
            if hasattr(error, "query"):
                error.query._owns_process = True
            else:
                process.close()
        raise
    handle._owns_process = owns_process
    handle.target = target
    handle.argument_count = len(arguments)
    handle.arguments_address = handle.callee_address + native_call.REQUEST.size if arguments else 0
    return handle


def submit_batch(client: NativeBridge, calls: Iterable[tuple[int, Iterable[int]]], *,
                 mode: int, flag: int, dispatch: str,
                 adapter: str | int = "pdx_v3_access.dll",
                 call_adapter: str | int = "pdx_native_call.dll",
                 process: RemoteProcess | None = None, **options):
    """Run an explicit ordinary-call sequence inside ONE existing native scope.

    Context/ABI/pointee legality remains the caller's responsibility. Calls use
    native_call.encode_batch unchanged. There is no nested per-call guard or new
    queue. A failed/unknown prefix is never replayed. Inspect scope.error and the
    callee batch's own error/completed/calls independently.
    """
    calls = tuple((target, tuple(arguments)) for target, arguments in calls)
    owns_process = process is None
    process = process or RemoteProcess(client.pid)
    try:
        callback = resolve_v3_export(process, call_adapter, "PdxNativeCallBatchInterface")
        handle = submit_callback(client, callback, native_call.encode_batch(calls),
            mode=mode, flag=flag, dispatch=dispatch, adapter=adapter,
            call_adapter=call_adapter, process=process,
            decode_payload=native_call.NativeCallBatchResult.from_bytes,
            prepare_payload=lambda address: native_call.encode_batch(calls, request_address=address),
            **options)
    except Exception as error:
        if owns_process:
            if hasattr(error, "query"):
                error.query._owns_process = True
            else:
                process.close()
        raise
    handle._owns_process = owns_process
    handle.calls = calls
    return handle
