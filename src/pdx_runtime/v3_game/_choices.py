"""Lightweight V3 choices backed by genuine retained native Data values.

A catalog is one existing source owner, not a global registry or a new native
lifetime protocol. Only actual owned CPdxPtr<CDataT> Any results are admitted.
A list position chooses a Python record; actions use that record's original
retained binding, never re-resolve an engine ordinal or a display name.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import asyncio
from types import MappingProxyType

from .. import v3_data as data
from ._run import owned_binding
from . import _run


class ChoicesBusyError(RuntimeError):
    """A native borrower is still active or its original operation is unsettled."""


class OwnedChoice(Mapping):
    """Copied visible fields plus a private binding held by its source catalog."""
    def __init__(self,catalog,result,named_type,binding,fields):
        self._catalog=catalog
        self._result=result
        self._type=named_type
        self._binding=binding
        self._adapter=getattr(catalog.owner,"value_adapters",{}).get(result.value_handle,catalog.adapters["data"])
        self._fields=MappingProxyType(dict(fields))

    def __getitem__(self,key):return self._fields[key]
    def __iter__(self):return iter(self._fields)
    def __len__(self):return len(self._fields)

    @property
    def native_result(self):
        return self._result

    @property
    def native_type(self):
        return self._type

    def borrow(self,context):
        """Lease this exact retained binding inside the original owning coroutine.

        Use one outer ctx.perform. Attach the existing action/read Run to the
        lease before native work. Pending work is not canceled or replayed here.
        """
        return ChoiceLease(self,context)


class ChoiceLease:
    """Local borrow bookkeeping; no game query, world stamp or command registry."""
    def __init__(self,choice,context):
        self.choice,self.context=choice,context
        self._active=False
        self._body_open=False
        self._owners=[]
        self._failed=False

    @property
    def binding(self):
        if not self._active:
            raise RuntimeError("enter the native choice lease before borrowing its binding")
        return self.choice._binding

    def attach(self,owner):
        """Keep an existing native operation owner until its original work settles."""
        if not self._active:
            raise RuntimeError("choice lease is not active")
        if owner.ctx is not self.context:
            raise ValueError("native choice and operation belong to different contexts")
        catalog=self.choice._catalog
        if owner is catalog.owner:
            raise ValueError("borrow with a separate operation owner; do not consume the catalog source owner")
        if (getattr(owner,"native",catalog.native) is not catalog.native
                or getattr(owner,"process",catalog.process) is not catalog.process):
            raise ValueError("operation uses a different original native client/process")
        adapter=self.choice._adapter
        prior=getattr(owner,"_choice_data_adapter",adapter)
        if prior!=adapter:
            raise ValueError("cannot combine choices from different native Data registries")
        value_adapters=getattr(owner,"value_adapters",{})
        if any(value_adapters.get(h,owner.adapters["data"])!=adapter for h in getattr(owner,"values",())):
            raise ValueError("operation already owns values from another native Data registry")
        owner._choice_data_adapter=adapter
        if hasattr(owner,"adapters"):
            owner.adapters["data"]=adapter
        if owner not in self._owners:
            self._owners.append(owner)
        return self.binding

    def settle(self):
        """Release only after attached original owners report completed cleanup.

        No receipt/world probe or callback is made. An unknown operation keeps
        the source catalog alive. Resume that operation through its existing
        owner/executor, then call settle again; never replay a selected action.
        """
        if not self._active:
            return True
        if self._body_open:
            return False
        if self._owners:
            if any(getattr(owner,"status",None)!="complete" for owner in self._owners):
                return False
        elif self._failed:
            return False
        self._active=False
        self.choice._catalog._leases.discard(self)
        return True

    async def __aenter__(self):
        if self._active:
            raise RuntimeError("choice lease is already active")
        catalog=self.choice._catalog
        catalog._ensure_open()
        if self.context is not catalog.context:
            raise ValueError("native choice belongs to a different original context")
        self._active=True
        self._body_open=True
        catalog._leases.add(self)
        return self

    async def __aexit__(self,exc_type,exc,traceback):
        self._body_open=False
        if exc is not None:
            self._failed=True
        self.settle()
        return False


class OwnedChoices(Sequence):
    """An explicitly closable catalog over one original native source owner.

    owner must already own the genuine Data values. Native/process/adapter
    identity is captured locally once; this module never attaches a process,
    queries a world, manufactures a Data wrapper, or copies a native pointer.
    """
    def __init__(self,owner,*,context=None,metadata=None):
        self.owner=owner
        self.context=owner.ctx if context is None else context
        if self.context is not owner.ctx:
            raise ValueError("catalog context must be its original owner context")
        self.native=getattr(owner,"native",self.context.native)
        self.process=getattr(owner,"process",self.context.process)
        self.adapters=MappingProxyType(dict(getattr(owner,"adapters",self.context.adapters)))
        self.metadata=MappingProxyType(dict(metadata or {}))
        self._choices=[]
        self._leases=set()
        self._closed=False
        self._closing=False
        self._close_task=None
        self.close_result=None

    @property
    def closed(self):return self._closed

    @property
    def active_leases(self):return len(self._leases)

    def __len__(self):return len(self._choices)
    def __getitem__(self,index):return self._choices[index]

    def _ensure_open(self):
        if self._closed or self._closing:
            raise RuntimeError("native choice catalog is closed or closing")

    def add(self,result,named_type,binding,fields):
        """Admit only a genuinely owned CPdxPtr wrapper and its actual handle."""
        self._ensure_open()
        # Canonical retained admission uses the original Any header/handle.
        # A RAW copied payload need not have a host NativeObject decoder.
        try:
            original_binding=owned_binding(self.owner,result,named_type)
        except RuntimeError as error:
            raise ValueError("native choice lacks the original owning CPdxPtr Any") from error
        if not isinstance(binding,data.ObjectBinding) or binding!=original_binding:
            raise ValueError("choice binding is not the original retained result handle/type")
        choice=OwnedChoice(self,result,named_type,binding,fields)
        self._choices.append(choice)
        return choice

    async def close(self):
        """Release through the original owner, or refuse while it is borrowed."""
        if self._closed:
            return self.close_result
        for lease in tuple(self._leases):
            lease.settle()
        if self._leases:
            raise ChoicesBusyError("native choices still have active or unsettled borrowers")
        if self._close_task is None or self._close_task.done():
            self._closing=True
            async def finish():
                result={"status":"closing","closed":False}
                self.close_result=await self.owner.finish(result)
                self._closed=getattr(self.owner,"status",None)=="complete"
                self.close_result["closed"]=self._closed
                if self._closed:
                    self.close_result["status"]="closed"
                return self.close_result
            self._close_task=asyncio.create_task(finish())
        # Join original cleanup; cancellation of one caller does not replay it.
        return await asyncio.shield(self._close_task)

    async def __aenter__(self):
        self._ensure_open()
        return self

    async def __aexit__(self,exc_type,exc,traceback):
        if exc is None:
            await self.close()
        else:
            # Preserve the primary exception and original source graph. If no
            # native borrower remains, ordinary cleanup can still be explicit.
            try:
                await self.close()
            except Exception as cleanup_error:
                self.close_result={"status":"retained","closed":False,"cleanup_error":cleanup_error}
        return False
