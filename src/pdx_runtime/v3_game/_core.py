"""One V3 session, native metadata and resumable ordinary Python operations."""
from __future__ import annotations
from pdx_runtime.v3_wire import V3Wire
import importlib
import inspect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .research import ResearchAPI
    from .notifications import NotificationsAPI
    from .time import TimeAPI
    from .storage import StorageAPI
    from .state_management import StateManagementAPI
    from .setup import SetupAPI

from ..native_bridge import RemoteProcess, BridgeError
from .. import v3_lifecycle as lifecycle, v3_player_control as player, v3_data as data
from ._run import ACTIVE_CONTEXT
from ..query_executor import Diagnostics, PendingOperation, RetainedOwners, SerializedQueryExecutor

DEFAULT_ADAPTERS = {"data":"pdx_v3_data.dll","objects":"pdx_v3_objects.dll",
    "commands":"pdx_v3_commands.dll","access":"pdx_v3_access.dll","call":"pdx_native_call.dll",
    "console":"pdx_v3_console.dll","lifecycle":"pdx_v3_lifecycle.dll","life":"pdx_v3_lifecycle.dll",
    "carrier":"pdx_runtime.dll","runtime":"pdx_runtime.dll","script":"pdx_v3_script.dll",
    "step":"pdx_v3_time_step.dll","options":"pdx_v3_options.dll"}
DOMAIN_CLASSES = {name:name.title()+"API" for name in
    ("country","economy","research","construction","politics","diplomacy","military","events","notifications","time","lifecycle","storage","setup")}
DOMAIN_CLASSES["state_management"] = "StateManagementAPI"


def integer(value):
    return int(value,0) if isinstance(value,str) else int(value)


class NativeRejection(RuntimeError):
    """A known native result, retained without inventing a rejection reason."""
    def __init__(self,label,result):
        self.label,self.result=label,result
        super().__init__(f"{label}: native error {getattr(result,'error',None)}")


class RawAPI:
    def __init__(self,context):
        self.session,self.native,self.process=context.session,context.native,context.process
        self.sdk=importlib.import_module("pdx_runtime")
    def __getattr__(self,name):
        try:return importlib.import_module("pdx_runtime."+name)
        except ModuleNotFoundError as error:
            if error.name=="pdx_runtime."+name:raise AttributeError(name) from error
            raise


def _retryable_read_error(error):
    return isinstance(error,(BridgeError,OSError,TimeoutError)) or (
        isinstance(error,RuntimeError) and "submission outcome unknown" in str(error))


def _busy_submit_error(error):
    return getattr(error,"winerror",getattr(error,"code",getattr(error,"error",None)))==170


class GameContext:
    def __init__(self,session,process=None,*,country_id=None,adapters=None,
                 timeout=30.0,poll_interval=.005):
        self.session,self.native=session,session.native
        self.process=process if process is not None else session.namespace.get("process")
        self.owns_process=self.process is None
        if self.process is None:self.process=RemoteProcess(self.native.pid)
        self.adapters={**DEFAULT_ADAPTERS,**(adapters or {})}
        self.owner_pool=getattr(session,"owner_pool",None)
        if self.owner_pool is None:
            session.owner_pool=self.owner_pool=RetainedOwners()
        self._executor=SerializedQueryExecutor(timeout=timeout,poll_interval=poll_interval,
            owners=self.owner_pool,diagnostics=Diagnostics(),retryable_read_error=_retryable_read_error,
            busy_submit_error=_busy_submit_error)
        self.world=0;self.image_base=0;self.interface_thread_id=0
        self.explicit_country_id=country_id
        self._types={};self._functions={};self._support={};self._domains={}
        self._all_types_complete=False
        self._shared_caches={}

    # These views expose the executor's one original state.
    @property
    def timeout(self):return self._executor.timeout
    @timeout.setter
    def timeout(self,value):self._executor.timeout=value

    @property
    def poll_interval(self):return self._executor.poll_interval
    @poll_interval.setter
    def poll_interval(self,value):self._executor.poll_interval=value

    @property
    def diagnostics(self):return self._executor.diagnostics
    @diagnostics.setter
    def diagnostics(self,value):self._executor.diagnostics=value

    @property
    def pending_queries(self):return self._executor.pending_queries
    @pending_queries.setter
    def pending_queries(self,value):self._executor.pending_queries=value

    @property
    def _query_lock(self):return self._executor._query_lock
    @_query_lock.setter
    def _query_lock(self,value):self._executor._query_lock=value

    @property
    def _wake(self):return self._executor._wake
    @_wake.setter
    def _wake(self,value):self._executor._wake=value

    @property
    def _operations(self):return self._executor._operations
    @_operations.setter
    def _operations(self,value):self._executor._operations=value

    def _remember(self,query):return self._executor._remember(query)
    def _forget(self,query):return self._executor._forget(query)

    async def blocking(self,function,/,*args,**kwargs):
        """Use the shared retained I/O boundary, without a workflow-wide lock."""
        return await self._executor.blocking(function,*args,**kwargs)

    async def wait(self,query,*,owner=None):
        return await self._executor.wait(query,owner=owner)

    async def submit(self,factory):return await self._executor.submit(factory)

    async def query(self,label,factory,*,owner=None):
        return await self._executor.query(label,factory,owner=owner)

    async def _finish(self,operation,timeout=None):
        return await self._executor._finish(operation,timeout)

    async def perform(self,label,async_callable):
        async def owned():
            token=ACTIVE_CONTEXT.set(self)
            try:
                value=async_callable() if callable(async_callable) else async_callable
                return await value if inspect.isawaitable(value) else value
            finally:ACTIVE_CONTEXT.reset(token)
        return await self._executor.perform(label,owned,context=self,task_name="v3-game:"+label)

    async def refresh(self):
        result=await self.query("world.observe",lambda:lifecycle.observe(self.native,
            adapter=self.adapters["lifecycle"],process=self.process))
        if result.error:raise NativeRejection("world.observe",result)
        status=await self.blocking(V3Wire(self.native).world_status)
        image=await self.blocking(self.native.status)
        world=integer(result.world)
        if world!=self.world:
            self._types.clear();self._functions.clear();self._all_types_complete=False
            self._shared_caches={}  # Old suspended readers keep only their old cache.
        self.world=world
        self.image_base=integer(image["main_module_base"])
        self.interface_thread_id=integer(status["interface_thread_id"])
        return result

    async def country_id(self):
        if self.explicit_country_id is not None:
            if isinstance(self.explicit_country_id,str):
                result=await self.query("country.resolve",lambda:player.resolve_tag(self.native,
                    self.explicit_country_id,adapter=self.adapters["console"],process=self.process))
                if result.error or result.target_error:raise NativeRejection("country.resolve",result)
                return result.target_country_id
            return integer(self.explicit_country_id)
        result=await self.query("country.current",lambda:player.observe_player(self.native,
            adapter=self.adapters["console"],process=self.process))
        if result.error or result.controlled_country_error or result.observer_enabled or result.controlled_country_id==0xffffffff:
            raise NativeRejection("current controlled country unavailable",result)
        return result.controlled_country_id

    async def type_id(self,name):
        """Resolve a copied native registry ID without requiring an active world.

        Positive hits do no I/O. A miss scans native metadata again, including
        late registrations; a prior missing name is never a permanent cache hit.
        """
        if name in self._types:return self._types[name]
        self._all_types_complete=False
        cursor=0
        while True:
            page=await self.query("metadata.types",lambda:data.enumerate_types(self.native,
                start=cursor,capacity=128,adapter=self.adapters["data"],process=self.process))
            if page.error:raise NativeRejection("metadata.types",page)
            self._types.update((row.name,row.type_id) for row in page.rows)
            if name in self._types:break
            if page.next<=cursor or page.next>=page.total:raise LookupError(f"native type {name!r} not found")
            cursor=page.next
        return self._types[name]

    async def all_types(self,*,refresh=False):
        """Return a copied snapshot of discovered native type names and IDs.

        The default reuses a completed metadata snapshot without I/O. Explicit
        refresh repeats only native metadata discovery, including late entries;
        it does not observe the world or refresh the lifecycle. Completed pages
        remain in the positive cache if a later page fails, and NativeRejection
        retains that exact failed result. Observed lifecycle changes may clear
        this context's cache; a snapshot is not a permanent registry guarantee.
        """
        if self._all_types_complete and not refresh:return dict(self._types)
        self._all_types_complete=False
        cursor=0
        while True:
            page=await self.query("metadata.types",lambda:data.enumerate_types(self.native,
                start=cursor,capacity=128,adapter=self.adapters["data"],process=self.process))
            if page.error:raise NativeRejection("metadata.types",page)
            self._types.update((row.name,row.type_id) for row in page.rows)
            if page.next<=cursor or page.next>=page.total:break
            cursor=page.next
        self._all_types_complete=True
        return dict(self._types)

    def shared_cache(self,name):
        """Private per-world copied metadata; never a native object/Any cache."""
        return self._shared_caches.setdefault(name,{})

    def support(self,name):
        if name not in self._support:
            self._support[name]=importlib.import_module("."+name, __package__)
        return self._support[name]

    def domain(self,name):
        if name not in self._domains:
            module=importlib.import_module("pdx_runtime.v3_game."+name)
            self._domains[name]=getattr(module,DOMAIN_CLASSES[name])(self)
        return self._domains[name]


class V3Game:
    """Game-level V3 APIs over the exact supplied RuntimeSession."""
    research: ResearchAPI
    notifications: NotificationsAPI
    time: TimeAPI
    storage: StorageAPI
    state_management: StateManagementAPI
    setup: SetupAPI

    def __init__(self,session,*,process=None,country_id=None,adapters=None,timeout=30.0):
        self._ctx=GameContext(session,process,country_id=country_id,adapters=adapters,timeout=timeout)
        self.raw=RawAPI(self._ctx)
    def __getattr__(self,name):
        if name in DOMAIN_CLASSES:return self._ctx.domain(name)
        raise AttributeError(name)
    @property
    def diagnostics(self):return self._ctx.diagnostics
    @classmethod
    async def attach(cls,*args,**kwargs):
        module=importlib.import_module("pdx_runtime.v3_game.lifecycle")
        return await module.attach(cls,*args,**kwargs)
    @classmethod
    async def launch(cls,*args,**kwargs):
        module=importlib.import_module("pdx_runtime.v3_game.lifecycle")
        return await module.launch(cls,*args,**kwargs)
    async def new_campaign(self,*args,**kwargs):return await self.lifecycle.new_campaign(*args,**kwargs)
    async def save(self,*args,**kwargs):return await self.lifecycle.save(*args,**kwargs)
    async def saves(self,*args,**kwargs):return await self.lifecycle.saves(*args,**kwargs)
    async def load(self,*args,**kwargs):return await self.lifecycle.load(*args,**kwargs)
    async def menu(self,*args,**kwargs):return await self.lifecycle.menu(*args,**kwargs)
    async def quit(self,*args,**kwargs):return await self.lifecycle.quit(*args,**kwargs)
    async def detach(self,*args,**kwargs):return await self.lifecycle.detach(*args,**kwargs)
    async def __aenter__(self):return self
    async def __aexit__(self,*exc):
        result=await self.detach()
        while isinstance(result,PendingOperation):result=await result.resume()
