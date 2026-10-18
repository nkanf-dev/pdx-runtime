"""Native V3 validation/select/start results, using shared callback primitives.

Adapters must already be loaded. Requests are ordinary Python handles; read()
returns None while pending and never waits or resubmits. NativeBridge's existing
construction queue/status methods remain the direct construction submit API.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct

from .native_bridge import NativeBridge, RemoteProcess
from .v3 import CallbackResult, ERROR_IO_PENDING, submit_callback

VALIDATION_RESULT = struct.Struct("<IIIIQQIIIIQQ")
START_RESULT = struct.Struct("<10I5Q")
PLAY_FINALIZE_RESULT = struct.Struct("<12I8Q2IQ2IQ")


@dataclass(frozen=True)
class ConstructionValidationResult:
    size: int
    error: int
    actor_id: int
    state_id: int
    building_type: int
    carrier: int
    raw_low: int
    raw_high: int
    validated: int
    execution_thread_id: int
    application: int
    world: int

    @classmethod
    def from_bytes(cls, data: bytes) -> ConstructionValidationResult:
        return cls(*VALIDATION_RESULT.unpack(data))


class StartAction(IntEnum):
    SELECT_COUNTRY = 1
    START_PAUSED = 2
    SELECT_AND_START_PAUSED = 3
    OBSERVE_START = 4


@dataclass(frozen=True)
class StartResult:
    size: int
    error: int
    action: int
    country_id: int
    local_machine_id: int
    selected_country_id: int
    paused: int
    game_started: int
    start_accepted: int
    reserved: int
    application: int
    world: int
    idler: int
    logic: int
    player: int

    @classmethod
    def from_bytes(cls, data: bytes) -> StartResult:
        return cls(*START_RESULT.unpack(data))


def validate_construction(client: NativeBridge, actor_id: int, state_id: int,
                          building_type: int, *, carrier: int,
                          adapter: str | int = "pdx_v3_construction_validate.dll",
                          process: RemoteProcess | None = None) -> CallbackResult:
    """Run the native validator, not construction submit or a future reservation.

    ``carrier`` is the live module base exposing the native command primitives.
    ``validated`` means validator execution completed; raw_low/high are the exact
    native output words, without a guessed success interpretation.
    """
    initial = VALIDATION_RESULT.pack(VALIDATION_RESULT.size, ERROR_IO_PENDING,
        actor_id, state_id, building_type, carrier, 0, 0, 0, 0, 0, 0)
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3ValidateConstructionInterface",
        decode_result=ConstructionValidationResult.from_bytes, process=process)


def start(client: NativeBridge, action: StartAction | int, country_id: int = 0, *,
          adapter: str | int = "pdx_v3_start_game.dll",
          process: RemoteProcess | None = None) -> CallbackResult:
    """Select, start paused, or observe via the analyzed native callback.

    Completion means the callback returned. start_accepted is command acceptance;
    game_started is observed separately. This helper never automatically retries
    a start command or claims its callback completion means game_started.
    """
    action = StartAction(action)
    initial = START_RESULT.pack(START_RESULT.size, ERROR_IO_PENDING,
        action, country_id, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3StartGameInterface", decode_result=StartResult.from_bytes,
        process=process)


@dataclass(frozen=True)
class PlayFinalizeResult:
    size: int
    error: int
    action: int
    country_id: int
    gui_mode: int
    paused: int
    simulation_started: int
    player_flags: int
    lobby_visible: int
    native_sync_done: int
    native_ready_called: int
    reserved: int
    application: int
    world: int
    idler: int
    gfx: int
    handler: int
    lobby: int
    player: int
    player_ref_database: int
    player_country_id: int
    handler_country_id: int
    handler_ref_database: int
    lobby_country_id: int
    lobby_selected_country_id: int
    lobby_ref_database: int

    @classmethod
    def from_bytes(cls, data: bytes) -> PlayFinalizeResult:
        return cls(*PLAY_FINALIZE_RESULT.unpack(data))


def finalize_play(client: NativeBridge, country_id: int, *, observe: bool = False,
                  adapter: str | int = "pdx_v3_play_finalize.dll",
                  process: RemoteProcess | None = None) -> CallbackResult:
    """Observe or run native lobby sync/Ready for the already-controlled Player.

    This does not select a country or start simulation. Callback completion and
    native_sync_done/native_ready_called are separate from the next-frame GUI
    transition. Observe separately for gui_mode/lobby_visible and the raw refs;
    do not infer success or failure from one flag or auto-repeat the action.
    """
    initial = PLAY_FINALIZE_RESULT.pack(PLAY_FINALIZE_RESULT.size, ERROR_IO_PENDING,
        0 if observe else 1, country_id, *([0] * 22))
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3PlayFinalizeInterface",
        decode_result=PlayFinalizeResult.from_bytes, process=process)
