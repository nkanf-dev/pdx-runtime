#pragma once
#include <Windows.h>
#include <stdint.h>
#ifdef PDX_V3_PLAY_FINALIZE_BUILD
#define PDX_V3_PLAY_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_PLAY_API extern "C" __declspec(dllimport)
#endif
struct PdxV3PlayFinalizeResult {
    uint32_t size;
    uint32_t error;
    uint32_t action;
    uint32_t country_id;
    uint32_t gui_mode;
    uint32_t paused;
    uint32_t simulation_started;
    uint32_t player_flags;
    uint32_t lobby_visible;
    uint32_t native_sync_done;
    uint32_t native_ready_called;
    uint32_t reserved;
    uint64_t application;
    uint64_t world;
    uint64_t idler;
    uint64_t gfx;
    uint64_t handler;
    uint64_t lobby;
    uint64_t player;
    uint64_t player_ref_database;
    uint32_t player_country_id;
    uint32_t handler_country_id;
    uint64_t handler_ref_database;
    uint32_t lobby_country_id;
    uint32_t lobby_selected_country_id;
    uint64_t lobby_ref_database;
};
// Native method adapters; caller supplies the real live Lobby and Interface scope.
PDX_V3_PLAY_API DWORD WINAPI PdxV3SyncLobbyFromPlayer(uint64_t lobby);
PDX_V3_PLAY_API DWORD WINAPI PdxV3ReadyLobby(uint64_t lobby);
// Resident public callback-compatible: action0 observe, action1 native sync+Ready.
// country_id must match the already-controlled current Player; no selection/start.
PDX_V3_PLAY_API void WINAPI PdxV3PlayFinalizeInterface(void* application, void* opaque);
