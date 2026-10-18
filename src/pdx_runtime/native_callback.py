"""Owned callback storage with explicitly supplied runtime dispatch and completion.

This module does not choose a game, queue, process, or execution thread. A runtime
adapter supplies a :class:`CallbackContext`. Result storage stays live while a
callback is pending or its delivery is unknown; recovery never dispatches again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .native_bridge import BridgeError, RemoteProcess

ERROR_IO_PENDING = 997


class CallbackExecutionError(BridgeError):
    """A completed callback left the result's pending sentinel unchanged."""
    def __init__(self, result: object, winerror: int, request_id: int):
        self.result = result
        super().__init__("native callback", winerror, request_id=request_id,
                         reason="callback completed without a query result")


@dataclass(frozen=True)
class CallbackDisposition:
    """An adapter's protocol proof, not a guess from raw response text.

    ``kind`` is ``accepted``, ``rejected`` or ``unknown``. An accepted operation
    requires its positive integer ``request_id``. Only a protocol-proved rejected
    operation permits immediate release; its native ``error`` may be zero.
    Unknown responses remain attached verbatim to the retained result handle.
    """
    kind: str
    request_id: int | None = None
    error: int = 0
    reason: str | None = None


@dataclass(frozen=True)
class CallbackContext:
    """One explicit runtime's dispatch, completion source, and process factory.

    ``dispatch(callback_address, result_address)`` returns the raw submission.
    ``classify_submission(raw)`` returns a protocol-proved CallbackDisposition.
    The shared core never equates a raw status/error word with non-delivery.
    ``status_getter()`` returns ``status`` and ``completed_request_id``, plus
    optional ``error``, ``request_id`` and ``callback_error``. The callback error
    belongs only to the matching latest request, not to earlier completions.
    Adapters normalize native field names before returning this mapping. Status
    sampling is not an atomic transaction with copying the result bytes.
    ``process_factory()`` opens an owned process only when none was supplied.
    ``process_matches(process)`` is an explicit, side-effect-free target identity
    predicate. Only exact ``True`` confirms it; absent or unknown proof rejects
    before export lookup/allocation. Foreign runtimes may compare opaque target
    tokens instead of OS PIDs, but must supply this contract explicitly.
    Optional ``export_resolver(process, adapter, symbol)`` selects explicit adapter
    lookup policy. Without it, strict ``process.resolve_export(adapter, symbol)``
    is used; the shared core knows no game DLL names or alias catalog. The hook
    is keyword-only and receives the original process, never a proxy.
    None of these callbacks is inferred from the public ``client`` handle.
    The context is bound to that exact client object, not just an equal PID.
    """
    client: object
    dispatch: Callable[[int, int], dict]
    status_getter: Callable[[], dict]
    process_factory: Callable[[], RemoteProcess]
    process_matches: Callable[[RemoteProcess], bool]
    classify_submission: Callable[[object], CallbackDisposition]
    submit_operation: str = "callback submit"
    completion_operation: str = "callback completion"
    execution_error_factory: Callable[[object, int, int], BridgeError] = CallbackExecutionError
    export_resolver: Callable[[RemoteProcess, str | int, str], int] | None = field(
        default=None, kw_only=True)


class CallbackResult:
    """Keep result storage alive until the native callback has completed.

    ``process``, ``client``, ``callback_address`` and ``result_address`` are public
    composition points. ``read`` samples completion once, without waiting, and
    copies the result when complete. completion_status retains that observed
    terminal status before decoding, including when the decoder raises. It does
    not prove a later callback's error belongs to this query. A release failure
    is kept in cleanup_error; the original result or error stays primary.
    The same query owns unreleased storage/process and retries only cleanup on
    subsequent read()/close(). The saved Python result remains reusable.
    If queue delivery fails, the request may already have been accepted; the
    exception carries ``query`` and this object keeps its storage alive. Inspect
    native completion and recover explicitly rather than freeing an active input.
    The required context supplies native request/completed IDs; the real client
    remains public. No game-specific completion source is selected here.
    """
    def __init__(self, client: object, process: RemoteProcess,
                 callback_address: int, result_address: int,
                 owns_process: bool, result_size: int, decode_result: Callable[[bytes], object],
                 *, context: CallbackContext):
        if context.client is not client:
            raise ValueError("callback context belongs to a different client")
        if context.process_matches(process) is not True:
            raise ValueError("callback process identity is not confirmed")
        self.client = client
        self.process = process
        self.callback_address = callback_address
        self.result_address = result_address
        self.result_size = result_size
        self.decode_result = decode_result
        self.context = context
        self.status_getter = context.status_getter
        self.request_id: int | None = None
        self.submission: object | None = None
        self.completion_status: dict | None = None
        self.submission_error: BridgeError | None = None
        self._owns_process = owns_process
        self._result = None
        self._execution_error: BridgeError | None = None
        self.cleanup_error: BaseException | None = None
        self._freed = False
        self._released = False

    def read(self):
        if self.submission_error is not None:
            self._try_release()
            raise self.submission_error
        if self._result is not None:
            self._try_release()
            if self._execution_error is not None:
                raise self._execution_error
            return self._result
        if self._released:
            raise RuntimeError("query result storage was released without a result")
        if self.request_id is None:
            raise RuntimeError("submission outcome unknown; inspect native completion before releasing storage")
        status = self.status_getter()
        if status["status"] != "ok":
            raise BridgeError(self.context.completion_operation, status.get("error", 0))
        if status["completed_request_id"] < self.request_id:
            return None
        self.completion_status = status
        self._result = self.decode_result(self.process.read(self.result_address, self.result_size))
        if self._result.error == ERROR_IO_PENDING:
            # Completion status keeps only the latest operation's error, not error history.
            same_request = status.get("request_id") == self.request_id == status["completed_request_id"]
            callback_error = status.get("callback_error", 0) if same_request else 0
            self._execution_error = self.context.execution_error_factory(
                self._result, callback_error, self.request_id)
        self._try_release()
        if self._execution_error is not None:
            raise self._execution_error
        return self._result

    def recover(self, request_id: int) -> CallbackResult:
        """Bind an unknown delivery to its inspected native request ID; never resubmit.

        The caller must establish which native request belongs to this callback.
        Status has no opaque identity: its latest request ID cannot prove ownership.
        A rejected/unaccepted operation has no request ID and must not be bound.
        """
        if self.request_id is not None or self._released or self.submission_error is not None:
            raise RuntimeError("query already has a request ID, was rejected, or has been released")
        if request_id <= 0:
            raise ValueError("request_id must be positive")
        self.request_id = request_id
        return self

    def _try_release(self) -> None:
        """Keep a completed result/rejection separate from cleanup diagnostics."""
        try:
            self._release()
        except Exception as error:
            self.cleanup_error = error
        else:
            # A specialized resource hook may already retain its own failure.
            # Do not clear it merely because that hook returned normally.
            if self._released:
                self.cleanup_error = None

    def _release(self) -> None:
        if not self._freed:
            self.process.free(self.result_address)
            self._freed = True
        if not self._released:
            if self._owns_process:
                self.process.close()
            self._released = True

    def close(self) -> None:
        """Retry original cleanup, never a pending input or consuming operation.

        After a result/rejection is known, cleanup_error reports a failed release
        and this same query retains the unreleased storage/process for retry.
        """
        if self._released:
            return
        if self._result is not None or self.submission_error is not None:
            self._try_release()
            return
        if self.read() is None:
            raise RuntimeError("query is pending; retain its result storage until completion")


def submit_callback(client: object, initial_bytes: bytes, *,
                    context: CallbackContext, adapter: str | int, callback_symbol: str,
                    decode_result: Callable[[bytes], object],
                    process: RemoteProcess | None = None,
                    handle_factory: Callable | None = None) -> CallbackResult:
    """Compose allocation, export lookup and an explicitly supplied dispatch.

    All adapters use the existing size/error-leading result layouts. A custom
    handle factory may retain extra Python metadata; it does not add a transport.
    A custom factory receives the existing five construction arguments and must
    explicitly supply its handle's completion context. This permits multi-stage
    runtimes to use a different completion source from the dispatch stage.
    Unknown delivery preserves the handle in exception.query, without replay.
    """
    if context.client is not client:
        raise ValueError("callback context belongs to a different client")
    owns_process = process is None
    process = process if process is not None else context.process_factory()
    address = None
    try:
        if context.process_matches(process) is not True:
            raise ValueError("callback process identity is not confirmed")
        if context.export_resolver is None:
            callback = process.resolve_export(adapter, callback_symbol)
        else:
            callback = context.export_resolver(process, adapter, callback_symbol)
        address = process.allocate(len(initial_bytes))
        process.write(address, initial_bytes)
        if handle_factory is None:
            query = CallbackResult(client, process, callback, address, owns_process,
                                   len(initial_bytes), decode_result, context=context)
        else:
            query = handle_factory(client, process, callback, address, owns_process)
        if (query.client is not client or query.process is not process
                or query.callback_address != callback or query.result_address != address):
            raise ValueError("callback handle does not belong to this submission")
    except Exception:
        if address is not None:
            process.free(address)
        if owns_process:
            process.close()
        raise
    try:
        response = context.dispatch(callback, address)
        query.submission = response
        disposition = context.classify_submission(response)
        if not isinstance(disposition, CallbackDisposition):
            raise BridgeError(context.submit_operation, reason="invalid callback disposition")
        if disposition.kind == "accepted":
            request_id = disposition.request_id
            if type(request_id) is not int or request_id <= 0:
                raise BridgeError(context.submit_operation, reason="invalid callback request ID")
            query.request_id = request_id
            return query
        if disposition.kind != "rejected":
            raise BridgeError(context.submit_operation,
                reason=disposition.reason or "callback submission outcome unknown")
        if type(disposition.error) is not int:
            raise BridgeError(context.submit_operation, reason="invalid callback rejection error")
        details = {} if disposition.reason is None else {"reason": disposition.reason}
        query.submission_error = BridgeError(context.submit_operation, disposition.error, **details)
    except BaseException as error:
        # Exceptions, cancellation and malformed acknowledgements prove no rejection.
        # Retain the exact original allocation; explicit recovery never dispatches.
        error.query = query
        raise
    try:
        query._release()
    except BaseException as error:
        # Rejection is already known. Keep it primary and retain this exact
        # unused allocation/process; never turn cleanup into a new submission.
        query.cleanup_error = error
        query.submission_error.query = query
        raise query.submission_error from error
    raise query.submission_error
