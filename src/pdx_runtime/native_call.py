"""Compose exact-address ordinary Win64 calls from the public bridge primitives.

No signature, execution context or target legality is inferred. Positional bits
1..4 populate both matching GP and XMM registers; later bits occupy 8-byte stack
slots. Use a pointer argument for an ABI-required by-reference struct, and pass a
hidden sret pointer explicitly in its ABI position. This is not __vectorcall.
The native callback supplies no game guard, TLS or domain. A caller may instead
use its own analyzed DLL callback through an explicitly selected runtime context.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Iterable

from .native_bridge import NativeBridge, RemoteProcess
from .native_callback import CallbackContext, CallbackResult, ERROR_IO_PENDING, submit_callback

REQUEST = struct.Struct("<IIQQQQ16s")
ARGUMENT_POINTER_OFFSET = 24


def float32_bits(value: float) -> int:
    """IEEE float bits in the low32 of a positional argument slot."""
    return struct.unpack("<I", struct.pack("<f", value))[0]


def float64_bits(value: float) -> int:
    """IEEE double bits in one positional argument slot."""
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def encode_request(target: int, arguments: Iterable[int] = (), *,
                   request_address: int) -> bytes:
    """Pack one contiguous request+arguments allocation at its exact address.

    ``request_address`` is the base of live process-local storage. Allocate
    ``REQUEST.size + 8 * argument_count`` bytes, then write the returned bytes.
    A zero base can build a pre-allocation template, but its arguments pointer
    must be patched after allocation before calling native code. Ordinary uint64
    slot range is enforced by struct.pack, not an address/profile restriction.
    Request, arguments and all pointees must remain live until the call returns
    or dispatch completion is confirmed. Packing does not allocate or dispatch.
    """
    arguments = tuple(arguments)
    pointer = request_address + REQUEST.size if arguments and request_address else 0
    return REQUEST.pack(REQUEST.size, ERROR_IO_PENDING, target, len(arguments),
                        pointer, 0, bytes(16)) + struct.pack(f"<{len(arguments)}Q", *arguments)


@dataclass(frozen=True)
class NativeCallResult:
    size: int
    error: int
    target: int
    argument_count: int
    arguments_address: int
    rax: int
    xmm0: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> NativeCallResult:
        return cls(*REQUEST.unpack_from(data))

    @property
    def xmm0_low64(self) -> int:
        return int.from_bytes(self.xmm0[:8], "little")

    @property
    def xmm0_high64(self) -> int:
        return int.from_bytes(self.xmm0[8:], "little")


class NativeCallRequest(CallbackResult):
    """Shared callback lifetime with public target/argument-buffer composition.

    Inherited client/process/callback_address/result_address/request_id expose
    the low-level handles. No argument allocation exists outside result storage.
    ``read`` is nonblocking; unknown delivery retains the whole allocation in
    exception.query and requires explicit recovery, never automatic replay.
    Pointees and the loaded target/adapter DLLs remain the caller's responsibility.
    """
    def __init__(self, client: NativeBridge, process: RemoteProcess,
                 callback_address: int, result_address: int, owns_process: bool,
                 *, target: int, argument_count: int, context: CallbackContext):
        super().__init__(client, process, callback_address, result_address,
                         owns_process, REQUEST.size + 8 * argument_count,
                         NativeCallResult.from_bytes, context=context)
        self.target = target
        self.argument_count = argument_count
        self.arguments_address = result_address + REQUEST.size if argument_count else 0


def submit(client: NativeBridge, target: int, arguments: Iterable[int] = (), *,
           context: CallbackContext, adapter: str | int = "pdx_runtime.dll",
           process: RemoteProcess | None = None) -> NativeCallRequest:
    """Queue one exact-address call through the explicit runtime context.

    ``context`` supplies dispatch, completion and owned-process construction.
    No game queue is selected here. The adapter must already be loaded. Queue
    placement alone does not make a target legal: the caller proves its scope,
    object and thread needs.
    An explicit shared process is not closed by the handle. Request+arguments
    use one owned allocation; patch failure is prequeue and releases it. Results
    are raw RAX/XMM0 bits, not inferred value or target success interpretation.
    """
    arguments = tuple(arguments)
    initial = encode_request(target, arguments, request_address=0)

    def prepare(client, process, callback, address, owns_process):
        handle = NativeCallRequest(client, process, callback, address, owns_process,
                                   target=target, argument_count=len(arguments), context=context)
        if arguments:
            process.write(address + ARGUMENT_POINTER_OFFSET,
                          struct.pack("<Q", handle.arguments_address))
        return handle

    return submit_callback(client, initial, context=context, adapter=adapter,
        callback_symbol="PdxNativeCallInterface", decode_result=NativeCallResult.from_bytes,
        process=process, handle_factory=prepare)


BATCH_REQUEST = struct.Struct("<IIQQQ")


@dataclass(frozen=True)
class NativeCallBatchResult:
    size: int
    error: int
    count: int
    requests_address: int
    completed: int
    calls: tuple[NativeCallResult, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> NativeCallBatchResult:
        fields = BATCH_REQUEST.unpack_from(data)
        if fields[0] != BATCH_REQUEST.size or fields[4] > fields[2]:
            raise ValueError("invalid native batch completion header")
        end = BATCH_REQUEST.size + fields[2] * REQUEST.size
        if end > len(data):
            raise ValueError("native batch requests exceed owned result storage")
        calls = tuple(NativeCallResult.from_bytes(
            data[BATCH_REQUEST.size + i * REQUEST.size:BATCH_REQUEST.size + (i + 1) * REQUEST.size])
            for i in range(fields[2]))
        return cls(*fields, calls)


def encode_batch(calls: Iterable[tuple[int, Iterable[int]]], *, request_address: int = 0) -> bytes:
    """Pack a fixed sequence: header, all56B requests, then their argument arrays.

    Calls run in order but no signature/dependency/result-pointer inference occurs.
    Use explicit caller-owned output addresses for dependencies between targets.
    The whole byte buffer must stay live until callback completion is established.
    """
    calls = tuple((target, tuple(arguments)) for target, arguments in calls)
    arguments_offset = BATCH_REQUEST.size + len(calls) * REQUEST.size
    records, arguments_data = [], []
    for target, arguments in calls:
        pointer = request_address + arguments_offset if request_address and arguments else 0
        records.append(REQUEST.pack(REQUEST.size, ERROR_IO_PENDING, target, len(arguments),
                                    pointer, 0, bytes(16)))
        encoded = struct.pack(f"<{len(arguments)}Q", *arguments)
        arguments_data.append(encoded)
        arguments_offset += len(encoded)
    header = BATCH_REQUEST.pack(BATCH_REQUEST.size, ERROR_IO_PENDING, len(calls),
        request_address + BATCH_REQUEST.size if request_address and calls else 0, 0)
    return header + b"".join(records) + b"".join(arguments_data)


def submit_batch(client: NativeBridge, calls: Iterable[tuple[int, Iterable[int]]], *,
                 context: CallbackContext, adapter: str | int = "pdx_runtime.dll",
                 process: RemoteProcess | None = None) -> CallbackResult:
    """Submit a fixed sequence through one explicit runtime context; never replay it.

    Same required runtime context and ordinaryWin64 ABI as submit(). No guard,
    TLS or scheduler is added. Batch completion and each raw result are separate:
    a returned validation error stops
    the sequence; an exception/fault leaves a possibly executed prefix. Never
    repeat that prefix merely because a later target failed or delivery is unknown.
    Low-level PdxNativeCallBatchInterface can also compose with analyzed access
    callbacks.
    """
    calls = tuple((target, tuple(arguments)) for target, arguments in calls)
    initial = encode_batch(calls)
    def prepare(client, process, callback, address, owns):
        process.write(address, encode_batch(calls, request_address=address))
        return CallbackResult(client, process, callback, address, owns, len(initial),
                              NativeCallBatchResult.from_bytes, context=context)
    return submit_callback(client, initial, context=context, adapter=adapter,
        callback_symbol="PdxNativeCallBatchInterface", decode_result=NativeCallBatchResult.from_bytes,
        process=process, handle_factory=prepare)
