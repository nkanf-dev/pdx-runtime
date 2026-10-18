#pragma once
#include <Windows.h>
#include <stdint.h>

#ifdef PDX_V3_START_GAME_BUILD
#define PDX_V3_START_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_START_API extern "C" __declspec(dllimport)
#endif

enum PdxV3StartAction : uint32_t {
    PDX_V3_SELECT_COUNTRY = 1,
    PDX_V3_START_PAUSED = 2,
    PDX_V3_SELECT_AND_START_PAUSED = 3,
    PDX_V3_OBSERVE_START = 4,
};
struct PdxV3StartResult {
    uint32_t size;
    uint32_t error;
    uint32_t action;
    uint32_t country_id;
    uint32_t local_machine_id;
    uint32_t selected_country_id;
    uint32_t paused;
    uint32_t game_started;
    uint32_t start_accepted;
    uint32_t reserved;
    uint64_t application;
    uint64_t world;
    uint64_t idler;
    uint64_t logic;
    uint64_t player;
};

// These lower-level primitives require an existing native Interface access scope.
// A returned pointer is owned until SubmitStartGame or DestroyStartGame.
PDX_V3_START_API DWORD WINAPI PdxV3SelectCountry(uint32_t full_id);
PDX_V3_START_API uint64_t WINAPI PdxV3StartGameFactory(void* unused);
PDX_V3_START_API DWORD WINAPI PdxV3SubmitStartGame(uint64_t* owned_command, uint32_t* accepted);
PDX_V3_START_API DWORD WINAPI PdxV3DestroyStartGame(uint64_t* owned_command);
PDX_V3_START_API DWORD WINAPI PdxV3SetPaused(uint32_t paused);

// PdxV3QueueInterfaceCallback-compatible. opaque is a live PdxV3StartResult.
// Caller supplies size/action/country_id, then retains memory through completion.
// Select + pause + submit compose the same public primitives. Completion means
// callback completion; start_accepted does NOT claim game_started is observed.
PDX_V3_START_API void WINAPI PdxV3StartGameInterface(void* application, void* opaque);
