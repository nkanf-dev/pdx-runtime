"""Explicit Victoria 3 named-pipe wire adapter over an existing transport.

Constructing this view performs no I/O. It never opens, reconnects, replaces or
owns a transport, process, session, lock or native query. Commands and responses
use the exact original client's request method. The caller selects V3 here;
the shared NativeBridge only knows generic request/status/read/write operations.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from .native_bridge import RemoteProcess, RUNTIME_DLL
from .native_distribution import RUNTIME_EXPORT_ALIASES


# Victoria 3 selects its legacy adapter names explicitly. No arbitrary missing
# module is redirected, and the shared resolver has no default game catalog.
V3_MODULE_ALIASES = MappingProxyType({
    'pdx_v3_access.dll': RUNTIME_DLL,
    'pdx_v3_commands.dll': RUNTIME_DLL,
    'pdx_v3_console.dll': RUNTIME_DLL,
    'pdx_v3_construction_validate.dll': RUNTIME_DLL,
    'pdx_v3_data.dll': RUNTIME_DLL,
    'pdx_v3_diagnostics.dll': RUNTIME_DLL,
    'pdx_v3_lifecycle.dll': RUNTIME_DLL,
    'pdx_v3_objects.dll': RUNTIME_DLL,
    'pdx_v3_options.dll': RUNTIME_DLL,
    'pdx_v3_play_finalize.dll': RUNTIME_DLL,
    'pdx_v3_query.dll': RUNTIME_DLL,
    'pdx_v3_script.dll': RUNTIME_DLL,
    'pdx_v3_start_game.dll': RUNTIME_DLL,
    'pdx_v3_time_step.dll': RUNTIME_DLL,
    'v3_construction_validate.dll': RUNTIME_DLL,
    'v3_session_callbacks.dll': RUNTIME_DLL,
    'v3_session_hook.dll': RUNTIME_DLL,
})
V3_EXPORT_ALIASES = MappingProxyType({**RUNTIME_EXPORT_ALIASES, **V3_MODULE_ALIASES})


def resolve_v3_export(process: RemoteProcess, module_name: str | int,
                      symbol: str | int) -> int:
    """Resolve through the same process with the explicitly selected V3 policy."""
    return process.resolve_export(module_name, symbol, aliases=V3_EXPORT_ALIASES)


class WireTransport(Protocol):
    """The original generic transport; no V3 method or ownership is required."""
    def request(self, command: str) -> dict: ...


@dataclass(frozen=True)
class V3Wire:
    """V3 command encoding, without changing the supplied client's identity.

    This adapter does not classify acknowledgements, retry or normalize values.
    It returns the original response object and propagates original exceptions.
    Native queue acceptance, completion, access and ownership remain distinct.
    """
    client: WireTransport

    def queue_interface_callback(self, callback_address: int, opaque_address: int = 0) -> dict:
        """Queue verified void WINAPI(app, opaque) on the Application interface.

        Keep function/DLL/storage alive through completed_request_id. This context
        alone does not supply a game-access scope or the Session execution domain.
        """
        return self.client.request(f"interface-callback {callback_address:#x} {opaque_address:#x}")

    def construction_status(self) -> dict:
        return self.client.request("construction-status")

    def queue_construction(self, actor_id: int, state_id: int, building_type: int) -> dict:
        """Queue the analyzed one-level command; requires native Session drain."""
        return self.client.request(f"construction-submit {actor_id:#x} {state_id:#x} {building_type:#x}")

    def install_interface_hook(self) -> dict:
        """Install the precisely analyzed Application slot-8 callback on V3 only."""
        return self.client.request("hook-install")

    def remove_interface_hook(self) -> dict:
        """Queue restore on the native interface callback; keep the DLL loaded."""
        return self.client.request("hook-remove")

    def world_status(self) -> dict:
        return self.client.request("world-status")

    def enter_world(self, options: int = 0) -> dict:
        """Queue native factory+entry; status distinguishes submit from in-game."""
        return self.client.request(f"world-enter {options:#x}")

    def create_options(self) -> dict:
        """Queue the native factory. Read options_result after request completes."""
        return self.client.request("options-create")

    def submit_options(self, options: int) -> dict:
        """Queue native Application entry, transferring options ownership."""
        return self.client.request(f"options-submit {options:#x}")

    def destroy_options(self, options: int) -> dict:
        """Queue native delete of an owned, unsubmitted options object."""
        return self.client.request(f"options-destroy {options:#x}")
