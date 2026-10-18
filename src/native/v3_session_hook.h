#pragma once
#include "pdx_runtime_bridge.h"

using PdxV3SessionCallback = unsigned char (__fastcall*)(void* session);
using PdxV3ConstructionDrain = DWORD (WINAPI*)(void* session);
struct PdxV3SessionHookBinding {
    uint32_t size;
    uint32_t reserved;
    uint64_t callback_slot;
    uint64_t expected_callback;
    uint64_t session;
    uint64_t drain;
};
struct PdxV3SessionHookStatusInfo {
    uint32_t size;
    uint32_t installed;
    uint32_t error;
    uint32_t execution_thread_id;
    uint64_t callback_slot;
    uint64_t original_callback;
    uint64_t hook_callback;
    uint64_t session;
    uint64_t drain;
    uint64_t callback_count;
    uint64_t drain_count;
    uint32_t last_drain_error;
    uint32_t last_original_result;
    uint64_t main_module_base;
    uint64_t carrier;
    uint64_t dispatcher;
    uint64_t thread_handle;
    uint32_t outgoing_count;
    uint32_t incoming_count;
    uint32_t current_callback_matches;
    uint32_t session_id;
    uint32_t initialized_3f0;
    uint32_t consumer_3f1;
    uint32_t execution_3f3;
    uint32_t receive_blocked_29a;
    uint32_t session_mode_290;
    uint32_t thread_stop_c8;
};

// Independent DLL: carrier is the already-loaded module exporting DrainConstruction.
// Only the aligned native callback pointer at Session+B8 is replaced atomically.
// Drain is bracketed by native (1,0) access and leaves it before original dispatch.
PDX_API DWORD WINAPI PdxV3SessionHookInstall(void* carrier);
PDX_API DWORD WINAPI PdxV3SessionHookRemove(void* unused);
PDX_API DWORD WINAPI PdxV3SessionHookStatus(PdxV3SessionHookStatusInfo* result);
// Low-level callback-slot installation; useful for native hosts with explicit binding.
// The slot and Session storage must outlive this module; removal does not unload it.
PDX_API DWORD WINAPI PdxV3SessionHookInstallAt(const PdxV3SessionHookBinding* binding);
// Public raw status data can be read through the module export RVA without new IPC.
extern "C" __declspec(dllexport) PdxV3SessionHookStatusInfo PdxV3SessionHookState;

using PdxV3SessionQueuedCallback = void (WINAPI*)(void* session, void* opaque);
enum PdxV3SessionCallbackPhase : uint32_t {
    PDX_V3_SESSION_CALLBACK_IDLE = 0,
    PDX_V3_SESSION_CALLBACK_QUEUED = 1,
    PDX_V3_SESSION_CALLBACK_EXECUTING = 2,
    PDX_V3_SESSION_CALLBACK_COMPLETED = 3,
    PDX_V3_SESSION_CALLBACK_CANCELLED = 4,
    PDX_V3_SESSION_CALLBACK_FAILED = 5,
};
struct PdxV3SessionCallbackStatusInfo {
    uint32_t size;
    uint32_t phase;
    uint32_t error;
    uint32_t execution_thread_id;
    uint64_t request_id;
    uint64_t completed_request_id;
    uint64_t callbackfn;
    uint64_t opaque;
    uint64_t session;
};
// Worker-safe: copies addresses, does not copy or take ownership of opaque storage.
// Runs one exact void WINAPI(Session*,opaque) at native callback entry, BEFORE
// the original Session callback acquires dispatch/Session locks. No world guard,
// TLS edit, or domain permission is implied. Callback supplies its own ABI/scope.
// Caller keeps code + opaque live until completed_request_id matches this request.
// Remove cancels an unclaimed request; an already executing callback finishes once.
PDX_API DWORD WINAPI PdxV3QueueSessionCallback(uint64_t callbackfn, uint64_t opaque,
    uint64_t* request_id);
PDX_API DWORD WINAPI PdxV3SessionCallbackStatus(PdxV3SessionCallbackStatusInfo* result);
extern "C" __declspec(dllexport) PdxV3SessionCallbackStatusInfo PdxV3SessionCallbackState;
