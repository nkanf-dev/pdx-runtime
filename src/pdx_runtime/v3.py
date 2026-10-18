"""Small V3 native-query helpers composed from public bridge primitives.

In a persistent Python session::

    from pdx_runtime import v3
    q = v3.submit(native, 1)  # adapter DLL must already be loaded
    q.read()                # None while pending; QueryResult after completion

The native callback supplies its own analyzed Interface access scope. Raw process,
export, allocation and callback APIs remain directly usable; this module does not
add a broker, polling loop, synchronous wait or a game-side Python environment.
"""
from __future__ import annotations

from pdx_runtime.v3_wire import V3Wire, resolve_v3_export
from collections.abc import Mapping
from dataclasses import dataclass
import struct
from typing import Callable

from .native_bridge import BridgeError, NativeBridge, RemoteProcess
from . import native_callback
from .native_callback import CallbackContext, CallbackDisposition, ERROR_IO_PENDING

COUNTRY_RESULT = struct.Struct("<IIIIQQQ")


class QueryExecutionError(BridgeError):
    """The callback completed without replacing the result's pending sentinel."""
    def __init__(self, result: object, winerror: int, request_id: int):
        self.result = result
        super().__init__("V3 query callback", winerror, request_id=request_id,
                         reason="callback completed without a query result")


@dataclass(frozen=True)
class QueryResult:
    size: int
    error: int
    requested_id: int
    resolved_id: int
    country: int
    fallback: int
    gdp_raw64: int

    @classmethod
    def from_bytes(cls, data: bytes) -> QueryResult:
        return cls(*COUNTRY_RESULT.unpack(data))

    @property
    def valid(self) -> bool:
        return self.error == 0

    @property
    def gdp_raw64_hex(self) -> str:
        return f"0x{self.gdp_raw64:016x}"


def _classify_interface_submission(response: object) -> CallbackDisposition:
    """Apply the native WorldResponse/Interface enqueue acknowledgement protocol.

    WorldResponse emits status=error exactly when its operation_error is nonzero;
    that is the queue's returned rejection code, not world_error from an earlier
    callback. Transport/JSON failures raise before a response reaches this adapter.
    Missing, inconsistent or unrecognized fields do not prove non-delivery.
    """
    if not isinstance(response, Mapping):
        return CallbackDisposition("unknown", reason="invalid V3 submission mapping")
    error = response.get("error")
    if type(error) is not int or error < 0:
        return CallbackDisposition("unknown", reason="invalid V3 submission error")
    status = response.get("status")
    if status == "ok" and error == 0:
        return CallbackDisposition("accepted", request_id=response.get("request_id"))
    if status == "error" and error > 0:
        return CallbackDisposition("rejected", error=error)
    return CallbackDisposition("unknown", reason="unknown V3 submission acknowledgement")


def callback_context(client: NativeBridge, *,
                     status_getter: Callable[[], dict] | None = None) -> CallbackContext:
    """Adapt the V3 Interface queue to the shared owned-callback mechanics.

    The caller selects this game adapter explicitly. The shared runtime receives
    only normalized completion fields; it does not know V3 status field names.
    A custom status source still uses its own native request/completed IDs.
    """
    source = status_getter if status_getter is not None else V3Wire(client).world_status
    expected_pid = getattr(client, "pid", None)

    def process_matches(process):
        actual_pid = getattr(process, "pid", None)
        current_pid = getattr(client, "pid", None)
        return (type(expected_pid) is int and expected_pid > 0
                and type(current_pid) is int and current_pid == expected_pid
                and type(actual_pid) is int and actual_pid == expected_pid)

    def completion_status():
        status = dict(source())
        if "world_error" in status:
            status["callback_error"] = status["world_error"]
        return status

    return CallbackContext(
        client=client, dispatch=V3Wire(client).queue_interface_callback,
        status_getter=completion_status,
        process_factory=lambda: RemoteProcess(expected_pid),
        process_matches=process_matches,
        classify_submission=_classify_interface_submission,
        submit_operation="V3 query submit",
        completion_operation="V3 query completion",
        execution_error_factory=QueryExecutionError,
        export_resolver=resolve_v3_export)


class CallbackResult(native_callback.CallbackResult):
    """V3-compatible facade for the shared owned-callback result handle.

    Existing V3 construction selects its Interface completion adapter here, not
    in the shared runtime. ``status_getter`` can select a native Session source.
    Pending storage, unknown delivery, recovery and release use the shared code.
    """
    def __init__(self, client: NativeBridge, process: RemoteProcess,
                 callback_address: int, result_address: int,
                 owns_process: bool, result_size: int, decode_result: Callable[[bytes], object],
                 *, status_getter: Callable[[], dict] | None = None):
        super().__init__(client, process, callback_address, result_address,
                         owns_process, result_size, decode_result,
                         context=callback_context(client, status_getter=status_getter))


class CountryQuery(CallbackResult):
    """The existing Country result handle, using shared callback ownership."""
    def __init__(self, client: NativeBridge, process: RemoteProcess,
                 callback_address: int, result_address: int,
                 country_id: int, owns_process: bool):
        super().__init__(client, process, callback_address, result_address,
                         owns_process, COUNTRY_RESULT.size, QueryResult.from_bytes)
        self.country_id = country_id


def submit_callback(client: NativeBridge, initial_bytes: bytes, *,
                    adapter: str | int, callback_symbol: str,
                    decode_result: Callable[[bytes], object],
                    process: RemoteProcess | None = None,
                    handle_factory: Callable | None = None) -> CallbackResult:
    """Submit through the explicit V3 Interface adapter and shared ownership.

    Existing V3 factories retain their five-argument construction contract.
    Unknown delivery preserves the original handle in exception.query; no replay.
    """
    if handle_factory is None:
        def handle_factory(client, process, callback, address, owns_process):
            return CallbackResult(client, process, callback, address, owns_process,
                                  len(initial_bytes), decode_result)
    return native_callback.submit_callback(client, initial_bytes,
        context=callback_context(client), adapter=adapter,
        callback_symbol=callback_symbol, decode_result=decode_result,
        process=process, handle_factory=handle_factory)


def submit(client: NativeBridge, country_id: int, *,
           adapter: str | int = "pdx_v3_query.dll",
           process: RemoteProcess | None = None) -> CountryQuery:
    """Queue the native Country query; return the existing non-blocking handle.

    Select an already-loaded adapter by name or exact module base. Raw loader,
    process and callback primitives stay public. Supply a shared RemoteProcess
    to reuse it; otherwise the result handle releases its own process connection.
    """
    if not 0 <= country_id <= 0xFFFFFFFF:
        raise ValueError("country_id must be the complete uint32 ID")
    initial = COUNTRY_RESULT.pack(COUNTRY_RESULT.size, ERROR_IO_PENDING,
                                  country_id, 0, 0, 0, 0)
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3QueryCountryInterface", decode_result=QueryResult.from_bytes,
        process=process, handle_factory=lambda client, process, callback, address, owns:
            CountryQuery(client, process, callback, address, country_id, owns))
