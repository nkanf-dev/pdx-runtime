"""Native Player Country/observer control, composed with the existing console.

Mutation uses native tag/observe once. It never opens a GUI, writes game flags,
replays a command or converts console callback success into effect acceptance.
The independent observation is a copied native snapshot, not a retained Player.
"""
from __future__ import annotations
from dataclasses import dataclass
import struct
from .native_bridge import NativeBridge, RemoteProcess
from .v3 import CallbackResult, ERROR_IO_PENDING, submit_callback
from . import v3_console

STATE = struct.Struct("<20I9Q8s")

@dataclass(frozen=True)
class PlayerState:
    size: int
    error: int
    action: int
    tag_bytes: int
    local_machine_id: int
    application_player_id: int
    player_machine_id: int
    player_flags_raw: int
    observer_enabled: int
    observer_global: int
    paused: int
    simulation_started: int
    controlled_country_id: int
    controlled_country_error: int
    selected_country_id: int
    selected_country_error: int
    target_country_id: int
    target_error: int
    target_control_eligible: int
    execution_thread_id: int
    application: int
    world: int
    idler: int
    player: int
    controlled_ref_database: int
    selected_ref_database: int
    controlled_country: int
    selected_country: int
    target_country: int
    tag_raw: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> PlayerState:
        result = cls(*STATE.unpack(data))
        if result.size != STATE.size:
            raise ValueError("invalid native PlayerState layout")
        return result

    @property
    def tag(self) -> str:
        return self.tag_raw[:self.tag_bytes].decode("utf-8", errors="replace")


def _tag_bytes(tag: str) -> bytes:
    if not isinstance(tag, str):
        raise TypeError("tag must be a string")
    value = tag.encode("utf-8")
    if len(value) != 3 or b"\0" in value:
        raise ValueError("native tag command requires exactly 3 non-NUL UTF-8 bytes")
    return value


def _query(client: NativeBridge, action: int, tag: bytes, *,
           adapter: str | int, process: RemoteProcess | None) -> CallbackResult:
    initial = STATE.pack(STATE.size, ERROR_IO_PENDING, action, len(tag),
        *([0] * 16), *([0] * 9), tag.ljust(8, b"\0"))
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3ConsolePlayerStateInterface",
        decode_result=PlayerState.from_bytes, process=process)


def observe_player(client: NativeBridge, *, adapter: str | int = "pdx_v3_console.dll",
                   process: RemoteProcess | None = None) -> CallbackResult:
    """Copy current local Player country refs and observer bit in one native scope.

    controlled_country_id (+50/+58 ref) differs from selected_country_id (+60/+68
    ref). Their native resolution errors remain separate from snapshot.error.
    Observer state is player_flags_raw bit0x20, not merely an invalid Country ID.
    All process pointers are borrowed observations. No state is changed.
    """
    return _query(client, 1, b"", adapter=adapter, process=process)


def resolve_tag(client: NativeBridge, tag: str, *, adapter: str | int = "pdx_v3_console.dll",
                process: RemoteProcess | None = None) -> CallbackResult:
    """Resolve native 3-byte tag to a checked full Country ID, without switching.

    Native10ddf00 owns case normalization and mapping; Python does not parse a
    tag catalog. target_error reports lookup/full-ID validity separately from the
    callback error. target_control_eligible preserves the native tag command's
    persistent validity and nonempty native Country+ca4 preconditions.
    """
    return _query(client, 2, _tag_bytes(tag), adapter=adapter, process=process)


def switch_country(client: NativeBridge, tag: str, *, output_capacity: int = 65536,
                   adapter: str | int = "pdx_v3_console.dll",
                   process: RemoteProcess | None = None) -> CallbackResult:
    """Submit real registered tag once; observe_player independently checks effect.

    This forwards tag unchanged to the existing native dispatcher. native_success
    is callback submission outcome, not proof the Session receiver applied it.
    A short result buffer or unknown transport delivery must never cause replay.
    """
    _tag_bytes(tag)
    return v3_console.execute_arguments(client, ("tag", tag),
        output_capacity=output_capacity, adapter=adapter, process=process)


def toggle_observer(client: NativeBridge, *, output_capacity: int = 65536,
                    adapter: str | int = "pdx_v3_console.dll",
                    process: RemoteProcess | None = None) -> CallbackResult:
    """Submit real observe toggle once, not an absolute enabled-state setter.

    Caller reads a fresh observe_player result and chooses whether a toggle is
    needed. Leaving observer mode requires a native selected Country; the native
    error/message is preserved. No auto-selection, second toggle or retry occurs.
    """
    return v3_console.execute_arguments(client, ("observe",),
        output_capacity=output_capacity, adapter=adapter, process=process)
