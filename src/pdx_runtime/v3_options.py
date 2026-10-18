"""Native full224-byte Options construction and112-byte settings assignment.

This is not a full Options clone, a Python-built settings structure or a scalar
configuration API. Use a real native factory owner and constructed settings source.
Assignment preserves the native two-CString deep-copy behavior; derived fields are
not copied. All raw factory/world-entry/load/destructor APIs remain open.
"""
from __future__ import annotations

from pdx_runtime.v3_wire import resolve_v3_export
from dataclasses import dataclass
from enum import IntEnum
import struct

from . import v3, v3_access
from .native_bridge import NativeBridge, RemoteProcess

REQUEST = struct.Struct("<4I4Q4I")
OPTIONS_BYTES = 224
SETTINGS_BYTES = 112


class Action(IntEnum):
    CREATE = 1
    DESTROY = 2
    SETTINGS_ADDRESS = 3
    COPY_SETTINGS = 4
    CREATE_FROM_SETTINGS = 5


class Ownership(IntEnum):
    NONE = 0
    OWNED = 1
    RELEASED = 2
    UNKNOWN = 3


@dataclass(frozen=True)
class OptionsResult:
    size: int
    error: int
    action: int
    ownership: int
    options: int
    source_settings: int
    settings_address: int
    operation_options: int
    started: int
    execution_thread_id: int
    reserved0: int
    reserved1: int

    @classmethod
    def from_bytes(cls, data: bytes) -> OptionsResult:
        fields = REQUEST.unpack(data)
        if fields[0] != REQUEST.size:
            raise ValueError("native Options request layout mismatch")
        return cls(*fields)

    @property
    def owns_pointer(self) -> bool:
        return self.ownership == Ownership.OWNED and bool(self.options)

    @property
    def ownership_unknown(self) -> bool:
        return self.ownership == Ownership.UNKNOWN


def submit(client: NativeBridge, action: Action | int, *, options: int = 0,
           source_settings: int = 0, mode: int, flag: int, dispatch: str,
           adapter: str | int = "pdx_v3_options.dll",
           access_adapter: str | int = "pdx_v3_access.dll",
           process: RemoteProcess | None = None, **scope_options):
    """One explicit operation via existing scoped callback ownership, never replay.

    ``mode/flag/dispatch`` are explicit raw scope/placement choices. This helper
    does not infer target legality, build GUI contexts or force Session use.
    Result is AccessResult; its .error is the scope layer, .callee_result is the
    OptionsResult resource status. Native pointer and callback allocation have
    distinct lifetimes. The returned owner must be destroyed/transferred exactly
    once after known completion; no __del__, automatic WorldEntry or Load occurs.

    Existing options/source pointees must remain live through this exact callback.
    The helper retains their public addresses but cannot own a raw caller's other
    handles. Unknown delivery retains exception.query and the whole request. Stop
    competing use/free and inspect that handle; never infer a latest ID/replay.
    """
    action = Action(action)
    initial = REQUEST.pack(REQUEST.size, v3.ERROR_IO_PENDING, action,
        Ownership.OWNED if options else Ownership.NONE,
        options, source_settings, 0, 0, 0, 0, 0, 0)
    owns_process = process is None
    process = process or RemoteProcess(client.pid)
    try:
        callback = resolve_v3_export(process, adapter, "PdxV3OptionsCallback")
        query = v3_access.submit_callback(client, callback, initial, mode=mode,
            flag=flag, dispatch=dispatch, adapter=access_adapter, process=process,
            decode_payload=OptionsResult.from_bytes, **scope_options)
    except Exception as error:
        if hasattr(error, "query"):
            query = error.query
            query._owns_process = owns_process
            query.options_action = action
            query.options_pointer = options
            query.source_settings_address = source_settings
        elif owns_process:
            process.close()
        raise
    query._owns_process = owns_process
    query.options_action = action
    query.options_pointer = options
    query.source_settings_address = source_settings
    return query


def create(client: NativeBridge, **scope_options):
    """Construct a native complete224-byte owner, not the128-byte inherited clone."""
    return submit(client, Action.CREATE, **scope_options)


def destroy(client: NativeBridge, owned_options: int, **scope_options):
    """One native slot1 destruction; error/unknown is not permission to repeat it.

    Do not pass an Options pointer already transferred into WorldEntry/LoadOwned.
    Only a known successful result ownershipRELEASED proves destructor completion.
    """
    return submit(client, Action.DESTROY, options=owned_options, **scope_options)


def settings_address(client: NativeBridge, native_options: int, **scope_options):
    """Borrow the true constructed112-byte settings address while its owner lives."""
    return submit(client, Action.SETTINGS_ADDRESS, options=native_options, **scope_options)


def copy_settings(client: NativeBridge, native_options: int, source_settings: int,
                  **scope_options):
    """Native assignment into a factory-constructed destination; source stays live.

    Source may be the native settings address of another owner. This preserves
    native self-assignment. It is NOT a memcpy/full224 clone; derived strings,
    player/objective pointers and flags stay the destination's own native values.
    A C++ assignment error can mean partial mutation; inspect and never auto-retry.
    """
    return submit(client, Action.COPY_SETTINGS, options=native_options,
                  source_settings=source_settings, **scope_options)


def create_from_settings(client: NativeBridge, source_settings: int, **scope_options):
    """Native full constructor then base settings assignment, separate owner.

    A known assignment failure cleans the known-constructed destination and
    returns ownershipNONE. Factory/destruction exceptions remain UNKNOWN with
    any published output visible; do not drop a pointer merely because error!=0.
    """
    return submit(client, Action.CREATE_FROM_SETTINGS, source_settings=source_settings,
                  **scope_options)
