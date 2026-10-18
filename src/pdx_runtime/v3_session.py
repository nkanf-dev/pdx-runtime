"""Compose public Interface enqueue and native Session callback completion.

The Session hook and raw-call adapter must already be loaded. No new pipe,
remote-thread three-argument call, polling loop, game guard or ABI inference.
"""
from __future__ import annotations

from pdx_runtime.v3_wire import V3Wire, resolve_v3_export
from dataclasses import dataclass
import struct
from typing import Callable, Iterable

from . import native_call, v3
from .native_bridge import BridgeError, NativeBridge, RemoteProcess

CALLBACK_STATUS = struct.Struct("<IIIIQQQQQ")
QUEUE_ARGUMENT_COUNT = 3
ENQUEUE_BYTES = native_call.REQUEST.size + 8 * QUEUE_ARGUMENT_COUNT
SESSION_REQUEST_OFFSET = ENQUEUE_BYTES
OPAQUE_OFFSET = ENQUEUE_BYTES + 8


@dataclass(frozen=True)
class SessionCallbackStatus:
    size: int
    phase: int
    error: int
    execution_thread_id: int
    request_id: int
    completed_request_id: int
    callbackfn: int
    opaque: int
    session: int

    @classmethod
    def from_bytes(cls, data: bytes) -> SessionCallbackStatus:
        return cls(*CALLBACK_STATUS.unpack(data))


def callback_status(process: RemoteProcess, *,
                    adapter: str | int = "v3_session_callbacks.dll") -> SessionCallbackStatus:
    """Read the public native state export once; no dispatch or wait."""
    address = resolve_v3_export(process, adapter, "PdxV3SessionCallbackState")
    return SessionCallbackStatus.from_bytes(process.read(address, CALLBACK_STATUS.size))


def enqueue(client: NativeBridge, callbackfn: int, opaque: int, request_address: int, *,
            session_adapter: str | int = "v3_session_callbacks.dll",
            call_adapter: str | int = "pdx_native_call.dll",
            process: RemoteProcess) -> native_call.NativeCallRequest:
    """Low-level three-argument QueueSessionCallback via the public raw adapter.

    Caller owns the live eight-byte request_address and opaque. Interface call
    completion means only that enqueue returned; retain opaque through the later
    Session request completion. NativeCallResult.rax is the enqueue Win32 code.
    """
    target = resolve_v3_export(process, session_adapter, "PdxV3QueueSessionCallback")
    return native_call.submit(client, target, (callbackfn, opaque, request_address),
                              context=v3.callback_context(client),
                              adapter=call_adapter, process=process)


class SessionCallbackResult(v3.CallbackResult):
    """One allocation, two distinct request IDs, shared CallbackResult ownership.

    request_id/submission identify the Interface enqueue call. session_request_id
    identifies the later Session callback. read() samples each stage without a
    wait. Code and pointees outside the owned opaque buffer remain caller-owned.
    Transport-unknown delivery retains this handle in exception.query; recover()
    binds only the inspected Interface request, never resubmits either stage.
    """
    def __init__(self, client, process, callback, address, owns_process, *,
                 callbackfn, state_address, payload_size, decode_result):
        super().__init__(client, process, callback, address, owns_process,
            OPAQUE_OFFSET + payload_size,
            lambda data: decode_result(data[OPAQUE_OFFSET:]),
            status_getter=self._completion_status)
        self.callbackfn = callbackfn
        self.opaque_address = address + OPAQUE_OFFSET
        self.session_state_address = state_address
        self.session_request_address = address + SESSION_REQUEST_OFFSET
        self.session_request_id: int | None = None
        self.enqueue_result: native_call.NativeCallResult | None = None
        self.session_status: SessionCallbackStatus | None = None
        self._enqueue_error: BridgeError | None = None

    def _completion_status(self) -> dict:
        status = SessionCallbackStatus.from_bytes(
            self.process.read(self.session_state_address, CALLBACK_STATUS.size))
        self.session_status = status
        completed = bool(self.session_request_id
                         and status.completed_request_id >= self.session_request_id)
        same = status.request_id == self.session_request_id == status.completed_request_id
        return {"status": "ok", "request_id": self.request_id if same else 0,
                "completed_request_id": self.request_id if completed else 0,
                "world_error": status.error if same else 0}

    def read(self):
        if self.submission_error is not None:
            return super().read()
        if self.session_request_id is None:
            if self.request_id is None:
                raise RuntimeError("Interface enqueue outcome unknown; inspect and recover its request ID")
            interface = V3Wire(self.client).world_status()
            if interface["status"] != "ok":
                raise BridgeError("Session enqueue completion", interface.get("error", 0))
            if interface["completed_request_id"] < self.request_id:
                return None
            self.enqueue_result = native_call.NativeCallResult.from_bytes(
                self.process.read(self.result_address, native_call.REQUEST.size))
            request = struct.unpack("<Q", self.process.read(self.session_request_address, 8))[0]
            if self.enqueue_result.error:
                # Raw enqueue did not finish; an unknown target outcome retains storage.
                error = BridgeError("Session enqueue raw call", self.enqueue_result.error)
                error.query = self
                raise error
            if self.enqueue_result.rax:
                self._enqueue_error = BridgeError("QueueSessionCallback", self.enqueue_result.rax)
                self.submission_error = self._enqueue_error
                self._enqueue_error.query = self
                self._try_release()
                raise self._enqueue_error
            if not request:
                error = BridgeError("QueueSessionCallback", reason="returned zero request ID")
                error.query = self
                raise error
            self.session_request_id = request
        result = super().read()
        if result is not None and self.session_status is not None:
            status = self.session_status
            same = status.request_id == self.session_request_id == status.completed_request_id
            if same and status.error:
                self._execution_error = v3.QueryExecutionError(result, status.error, self.session_request_id)
                raise self._execution_error
        return result


def submit_callback(client: NativeBridge, callbackfn: int, initial_bytes: bytes, *,
                    decode_result: Callable[[bytes], object],
                    session_adapter: str | int = "v3_session_callbacks.dll",
                    call_adapter: str | int = "pdx_native_call.dll",
                    process: RemoteProcess | None = None,
                    _prepare_payload: Callable[[int], bytes] | None = None) -> SessionCallbackResult:
    """Own opaque result bytes and enqueue one exact void(Session*,opaque).

    Results use the existing size/error-leading CallbackResult contract. Callback
    itself supplies target ABI/context and required guard; queue placement does
    not grant access. Session hook must be installed explicitly by the caller.
    """
    initial = bytes(OPAQUE_OFFSET) + initial_bytes

    def prepare(client, process, callback, address, owns):
        target = resolve_v3_export(process, session_adapter, "PdxV3QueueSessionCallback")
        state_address = resolve_v3_export(process, session_adapter, "PdxV3SessionCallbackState")
        payload = _prepare_payload(address + OPAQUE_OFFSET) if _prepare_payload else initial_bytes
        if len(payload) != len(initial_bytes):
            raise ValueError("prepared payload changed allocation size")
        handle = SessionCallbackResult(client, process, callback, address, owns,
            callbackfn=callbackfn, state_address=state_address,
            payload_size=len(payload), decode_result=decode_result)
        process.write(address, native_call.encode_request(target,
            (callbackfn, handle.opaque_address, handle.session_request_address),
            request_address=address) + bytes(8) + payload)
        return handle

    return v3.submit_callback(client, initial, adapter=call_adapter,
        callback_symbol="PdxNativeCallInterface", decode_result=decode_result,
        process=process, handle_factory=prepare)


def submit(client: NativeBridge, target: int, arguments: Iterable[int] = (), *,
           session_adapter: str | int = "v3_session_callbacks.dll",
           call_adapter: str | int = "pdx_native_call.dll",
           process: RemoteProcess | None = None) -> SessionCallbackResult:
    """Run one raw ordinary-Win64 target on the actual Session callback thread.

    Uses native_call.encode_request and NativeCallResult, not another ABI packer.
    The raw callback ignores its Session* argument and supplies no game guard.
    """
    arguments = tuple(arguments)
    owns_process = process is None
    process = process or RemoteProcess(client.pid)
    try:
        callbackfn = resolve_v3_export(process, call_adapter, "PdxNativeCallInterface")
    except Exception:
        if owns_process:
            process.close()
        raise
    try:
        handle = submit_callback(client, callbackfn,
            native_call.encode_request(target, arguments, request_address=0),
            decode_result=native_call.NativeCallResult.from_bytes,
            session_adapter=session_adapter, call_adapter=call_adapter, process=process,
            _prepare_payload=lambda address: native_call.encode_request(
                target, arguments, request_address=address))
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
    handle.arguments_address = handle.opaque_address + native_call.REQUEST.size if arguments else 0
    return handle


def submit_batch(client: NativeBridge, calls: Iterable[tuple[int, Iterable[int]]], *,
                 session_adapter: str | int = "v3_session_callbacks.dll",
                 call_adapter: str | int = "pdx_native_call.dll",
                 process: RemoteProcess | None = None) -> SessionCallbackResult:
    """One explicit native-call batch on the original Session callback thread.

    Enqueue/completion remain the existing two-stage Session contract. No guard
    or permissions are inferred, no extra queue/hook is installed, and pending or
    unknown batches retain every input without replaying an executed prefix.
    """
    calls = tuple((target, tuple(arguments)) for target, arguments in calls)
    owns_process = process is None
    process = process or RemoteProcess(client.pid)
    try:
        callback = resolve_v3_export(process, call_adapter, "PdxNativeCallBatchInterface")
        handle = submit_callback(client, callback, native_call.encode_batch(calls),
            decode_result=native_call.NativeCallBatchResult.from_bytes,
            session_adapter=session_adapter, call_adapter=call_adapter, process=process,
            _prepare_payload=lambda address: native_call.encode_batch(calls, request_address=address))
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
