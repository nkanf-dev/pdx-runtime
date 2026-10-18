"""Retained Python operations over one serialized, injected query transport.

The executor owns only Python task and query references. The supplied query or
completion owner reads and releases its native storage. Timeout and caller
cancellation do not cancel that task, resubmit a query, or replay a release.
Runtime adapters provide transport error classification and any task-local scope.
"""
from __future__ import annotations

import asyncio
import inspect
from contextvars import copy_context
from functools import partial
from collections.abc import Callable, Generator
from typing import Literal, Protocol, TypeAlias, TypeVar, overload, runtime_checkable

__all__ = [
    "CompletionOwner", "Diagnostics", "DiagnosticsSink", "OperationResult",
    "PendingOperation", "PendingResult", "Query", "RetainedOwners", "SerializedQueryExecutor",
]


class Query(Protocol):
    """An owned transport submission; None means it has not settled yet."""

    def read(self) -> object | None: ...


class CompletionOwner(Protocol):
    """An owner that must complete the original query without being replaced."""

    def complete(self) -> object | None: ...


class DiagnosticsSink(Protocol):
    def append(self, record: dict[str, object]) -> None: ...


_T_co = TypeVar("_T_co", covariant=True)


@runtime_checkable
class PendingResult(Protocol[_T_co]):
    """Read-only typing view of the existing PendingOperation, not a new handle.

    ``resume()`` and direct ``await`` continue the same owned task. Another
    timeout returns the same handle, not a new submission. ``timeout=None`` uses
    the context's configured timeout; it does not override it with an infinite
    wait. A final exception is raised by resume/await rather than returned.

    ``done`` means task completion, including failure or cancellation. The dict
    metadata stays ``{"status": "pending", "operation": label}`` even after the
    task finishes. Serialized metadata alone cannot resume the task.
    ``pending_queries`` is a retention snapshot, including completed callbacks
    whose storage cleanup is unfinished, not a native-pending status or a promise
    that each query belongs only to this operation.
    """

    @property
    def done(self) -> bool: ...

    @property
    def pending_queries(self) -> tuple[object, ...]: ...

    @overload
    def __getitem__(self, key: Literal["status"]) -> Literal["pending"]: ...

    @overload
    def __getitem__(self, key: Literal["operation"]) -> str: ...

    async def resume(self, timeout: float | None = None) -> _T_co | "PendingResult[_T_co]": ...

    def __await__(self) -> Generator[object, None, _T_co | "PendingResult[_T_co]"]: ...


OperationResult: TypeAlias = _T_co | PendingResult[_T_co]


class Diagnostics(dict):
    """Internal evidence supports both domain namespaces and append(record)."""
    def append(self,record):
        entries=self.setdefault("operations",[]);entries.append(record)
        del entries[:-64]


class PendingOperation(dict):
    """A short serializable handle; its original coroutine and owners stay private."""
    def __init__(self,context,label,task):
        super().__init__(status="pending",operation=label)
        self.context,self.label,self.task=context,label,task
    @property
    def done(self):return self.task.done()
    @property
    def pending_queries(self):return tuple(self.context.pending_queries)
    async def resume(self,timeout=None):
        self.context._wake.set()
        return await self.context._finish(self,timeout)
    def __await__(self):return self.resume().__await__()
    def __repr__(self):return f"PendingOperation({self.label!r}, done={self.done})"


class RetainedOwners:
    """Strong identity ownership for one session, without cleanup or game policy."""
    def __init__(self):
        self._owners={}

    def retain(self,owner):
        self._owners[id(owner)]=owner
        return owner

    def discard(self,owner):
        self._owners.pop(id(owner),None)

    def snapshot(self) -> tuple[object,...]:
        return tuple(self._owners.values())


class SerializedQueryExecutor:
    """One query slot and retained workflows, without game or session policy.

    A submission exception carrying an unreleased ``query`` retains that exact
    query. A busy predicate selects retryable errors; it does not prove that a
    factory submitted nothing. Retry also requires the transport's explicit
    ``error.details["delivery"] == "not_sent"`` proof. Unmarked failures preserve
    their original error even when their code means busy. Completion and native
    resource lifetime remain the query's or completion owner's responsibility.
    """

    def __init__(self,*,timeout: float | None=30.0,poll_interval: float=.005,
                 diagnostics: DiagnosticsSink | None=None,
                 retryable_read_error: Callable[[Exception],bool] | None=None,
                 busy_submit_error: Callable[[Exception],bool] | None=None,
                 owners: RetainedOwners | None=None):
        self.timeout,self.poll_interval=timeout,poll_interval
        self.owner_pool=RetainedOwners() if owners is None else owners
        self.diagnostics=Diagnostics() if diagnostics is None else diagnostics
        self.retryable_read_error=(retryable_read_error if retryable_read_error is not None
            else lambda error:isinstance(error,(OSError,TimeoutError)))
        self.busy_submit_error=(busy_submit_error if busy_submit_error is not None
            else lambda error:False)
        self._pending_queries=[]
        self._query_lock=asyncio.Lock();self._locked_query=None;self._wake=asyncio.Event()
        self._operations=set()
        self._query_waits={}

    async def blocking(self,function: Callable,/,*args,**kwargs):
        """Run one blocking call in the event loop's reusable thread pool.

        The original future stays owned until its result is consumed. Cancelling
        an observer of perform/wait never cancels this call. Even direct task
        cancellation cannot abandon a running factory or completion and release
        its slot while native work still uses the original storage. Such direct
        cancellation is suppressed until this call settles; no call is replayed.
        Context variables reach the worker, but async workflow/CPU work stays on
        the loop. There is no executor-wide blocking-call mutex.
        """
        loop=asyncio.get_running_loop()
        future=loop.run_in_executor(None,copy_context().run,partial(function,*args,**kwargs))
        return await self._await_owned(future)

    async def _await_owned(self,future):
        """Settle this original call/task, never cancel or replace its work."""
        while not future.done():
            try:await asyncio.wait({future})
            except asyncio.CancelledError:
                # The owning frame keeps the exact Future. A running factory
                # may already have dispatched before it returns its query.
                # Public observer waits stay cancellable through perform().
                continue
        return future.result()

    @property
    def pending_queries(self):
        self._reconcile_completed_query()
        return self._pending_queries

    @pending_queries.setter
    def pending_queries(self,value):
        self._pending_queries=value

    def _remember(self,query):
        if not any(q is query for q in self._pending_queries):self._pending_queries.append(query)
        self.owner_pool.retain(query)

    def _forget(self,query):
        self._pending_queries[:]=[q for q in self._pending_queries if q is not query]
        self.owner_pool.discard(query)
        if self._locked_query is query:
            self._locked_query=None
            if self._query_lock.locked():self._query_lock.release()

    def _release_query_slot(self,query):
        if self._locked_query is query:
            self._locked_query=None
            if self._query_lock.locked():self._query_lock.release()

    def _reconcile_completed_query(self):
        queries=tuple(self._pending_queries)
        locked=self._locked_query
        if locked is not None and not any(query is locked for query in queries):
            queries+=(locked,)
        for query in queries:
            if id(query) not in self._query_waits and getattr(query,"_released",False) is True:
                self._forget(query)  # Released records only; no native read/replay.

    async def wait(self,query: Query,*,owner: CompletionOwner | None=None):
        """Observe one retained completion task for this exact query and owner.

        Concurrent observers share its original reader. Cancelling an observer
        does not abandon a pending callback or run owner.complete concurrently.
        After completion, wait(query) without the consuming owner retries only
        the query's cached-result cleanup and retires its retention entries.
        Reading pending_queries or submitting new work also retires records
        already released by a direct raw close, without native I/O or replay.
        """
        key=id(query)
        existing=self._query_waits.get(key)
        if existing is None:
            task=asyncio.create_task(self._wait(query,owner=owner),name="pdx-query-completion")
            self._query_waits[key]=(query,owner,task)
            def finished(done):
                self._query_waits.pop(key,None)
                # A cancelled observer may not retrieve a final exception. The
                # task still retains it for any observer already awaiting it.
                if not done.cancelled():done.exception()
            task.add_done_callback(finished)
        else:
            original,original_owner,task=existing
            if original is not query or original_owner is not owner:
                raise ValueError("query completion already has a different owner")
        return await self._await_owned(task)

    async def _wait(self,query: Query,*,owner: CompletionOwner | None=None):
        self._remember(query)
        reader=owner.complete if owner is not None else query.read
        failures=0
        while True:
            try:result=await self.blocking(reader)
            except asyncio.CancelledError:raise
            except Exception as error:
                # Released storage is terminal, including proved rejection with
                # no callback result. A cached completion also must not replay
                # a consuming owner, even if its cleanup still owns storage.
                released=getattr(query,"_released",False)
                cached_result=getattr(query,"_result",None) is not None
                rejected=getattr(query,"submission_error",None) is not None
                if released or cached_result or rejected:
                    if released:self._forget(query)
                    else:self._release_query_slot(query)
                    record={"query":query,"error":error}
                    if cached_result:record["callback_completed"]=True
                    if rejected:record["submission_rejected"]=True
                    if released:record["query_released"]=True
                    cleanup_error=getattr(query,"cleanup_error",None)
                    if cleanup_error is not None:record["cleanup_error"]=cleanup_error
                    self.diagnostics.append(record)
                    raise  # Final receipt/rejection: never retry owner.complete.
                if not self.retryable_read_error(error):
                    if self._locked_query is query:
                        self._locked_query=None
                        if self._query_lock.locked():self._query_lock.release()
                    self.diagnostics.append({"query":query,"error":error})
                    raise
                failures+=1
                self.diagnostics.append({"query":query,"error":error,"read_failures":failures})
                await asyncio.sleep(min(.25,self.poll_interval*max(1,failures)))
                continue
            if result is not None:
                if getattr(query,"_released",True):
                    self._forget(query)
                else:
                    # Native completion is final even when buffer/process
                    # cleanup is not. Keep this original query strongly owned;
                    # release only the callback slot so unrelated work can run.
                    self._release_query_slot(query)
                    self.diagnostics.append({"query":query,"callback_completed":True,
                        "cleanup_pending":True,"cleanup_error":getattr(query,"cleanup_error",None)})
                return result
            await asyncio.sleep(self.poll_interval)

    async def submit(self,factory: Callable[[],Query]):
        """Serialize only one transport callback, never a whole workflow."""
        self._reconcile_completed_query()
        while self._query_lock.locked():
            self._reconcile_completed_query()
            if not self._query_lock.locked():break
            await asyncio.sleep(self.poll_interval)
        await self._query_lock.acquire()
        try:
            while True:
                try:query=await self.blocking(factory);break
                except Exception as error:
                    query=getattr(error,"query",None)
                    if query is not None and not getattr(query,"_released",False):
                        self.diagnostics.append({"submission_error":error,"query":query});break
                    details=getattr(error,"details",None)
                    if (isinstance(details,dict) and details.get("delivery")=="not_sent"
                            and self.busy_submit_error(error)):
                        await asyncio.sleep(self.poll_interval);continue
                    raise
            self._locked_query=query;self._remember(query)
            return query
        except BaseException:
            if self._query_lock.locked():self._query_lock.release()
            raise

    async def query(self,label,factory: Callable[[],Query],*,owner: CompletionOwner | None=None):
        query=await self.submit(factory)
        result=await self.wait(query,owner=owner)
        self.diagnostics.append({"label":label,"result":result})
        return result

    async def _finish(self,operation,timeout=None):
        seconds=self.timeout if timeout is None else timeout
        try:
            # asyncio.wait never cancels the original task on timeout or caller
            # cancellation. Avoid a detached shield future logging its error
            # before the retained operation is resumed (notably Python 3.14).
            if operation.task.done():return operation.task.result()
            if seconds is not None and seconds<=0:return operation
            done,_=await asyncio.wait({operation.task},timeout=seconds)
            return operation.task.result() if done else operation
        finally:
            if operation.task.done():self._operations.discard(operation.task)

    async def perform(self,label,async_callable,*,context=None,task_name=None):
        """Retain one original task; an adapter may supply its existing context."""
        async def owned():
            value=async_callable() if callable(async_callable) else async_callable
            return await value if inspect.isawaitable(value) else value
        task=asyncio.create_task(owned(),name=task_name or "pdx-operation:"+label)
        self._operations.add(task)
        task.add_done_callback(self._operations.discard)
        operation=PendingOperation(self if context is None else context,label,task)
        return await self._finish(operation)
