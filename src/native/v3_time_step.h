#pragma once
#include <Windows.h>
#include <stdint.h>

#if defined(PDX_V3_TIME_STEP_BUILD) || defined(PDX_RUNTIME_BUILD)
#define PDX_V3_TIME_STEP_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_TIME_STEP_API extern "C" __declspec(dllimport)
#endif

// One native CPartialDailyTickCommand, not one hour/day. Native execution advances
// low32 date hours by6 and runs actual tick tasks plus native followup commands.
// Synchronous raw calls require caller-proven native access/thread/object scope.
// Factory writes one owned command pointer; Submit consumes it and uses the exact
// original CD2D00 preparation+Session receiver with natural flags7. Never replay
// an unknown submission. Queue acceptance is not observed tick completion.
PDX_V3_TIME_STEP_API DWORD WINAPI PdxV3TimeStepFactory(uint64_t* owned_command);
PDX_V3_TIME_STEP_API DWORD WINAPI PdxV3TimeStepSubmit(uint64_t* owned_command, uint32_t* accepted);
PDX_V3_TIME_STEP_API DWORD WINAPI PdxV3TimeStepDestroy(uint64_t* owned_command);

enum PdxV3TimeStepAction : uint32_t {
    PDX_V3_TIME_STEP_OBSERVE = 0,
    PDX_V3_TIME_STEP_SUBMIT_ONE = 1,
};
struct PdxV3TimeStepResult {
    uint32_t size;
    uint32_t error;
    uint32_t action;
    uint32_t accepted;
    uint32_t execution_thread_id;
    uint32_t paused;
    uint32_t game_started;
    uint32_t transition_pending;
    uint32_t initialized;
    uint32_t thread_stop;
    uint32_t world_updating;
    uint32_t queues_settled;
    uint32_t outgoing_active;
    uint32_t outgoing_pending;
    uint32_t incoming_active;
    uint32_t incoming_pending;
    uint32_t session_buffered;
    uint32_t logic_tick_pending;     // raw producer flag, NOT completion identity
    uint32_t raw_counter_5964f10;    // raw native accounting, NOT completion count
    uint32_t raw_counter_5964f14;
    uint64_t application;
    uint64_t world;
    uint64_t idler;
    uint64_t logic;
    uint64_t session;
    uint64_t dispatcher;
    uint64_t thread_handle;
    uint64_t active_command;
    uint64_t date_raw;              // native8B date; low32 is incremented by6
    uint64_t frame;
};

// Copies one snapshot. Uses native dispatch+outgoing+incoming locks in their
// observed order, with TryAcquire only. BUSY means no settled snapshot was taken.
// All locks are released before returning; no pending callback/work is drained.
PDX_V3_TIME_STEP_API DWORD WINAPI PdxV3TimeStepObserve(PdxV3TimeStepResult* result);

// Existing Interface callback shape. SUBMIT_ONE requires already-paused, started,
// ready world with empty native queues. Takes baseline, RELEASES snapshot locks,
// then submits exactly one native command. Baseline and admission are not an
// atomic transaction with other game/native producers: caller must inspect the
// later independent observation. No unpause/sleep/date write, auto startup,
// second hook, background poll, replay or save-policy override is performed.
PDX_V3_TIME_STEP_API void WINAPI PdxV3TimeStepInterface(void* application, void* opaque);
