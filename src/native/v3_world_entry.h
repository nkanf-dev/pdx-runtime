#pragma once
#include "pdx_runtime_bridge.h"

// Every queued game call runs after the original Application slot 8 frame.
enum PdxV3WorldOperation : uint32_t {
    PDX_V3_NONE = 0,
    PDX_V3_ENTER = 1,
    PDX_V3_CREATE_OPTIONS = 2,
    PDX_V3_SUBMIT_OPTIONS = 3,
    PDX_V3_DESTROY_OPTIONS = 4,
    PDX_V3_REMOVE_HOOK = 5,
    PDX_V3_INTERFACE_CALLBACK = 6,
};
enum PdxV3WorldPhase : uint32_t {
    PDX_V3_DETACHED = 0,
    PDX_V3_IDLE = 1,
    PDX_V3_QUEUED = 2,
    PDX_V3_EXECUTING = 3,
    PDX_V3_OPTIONS_CREATED = 4,
    PDX_V3_ENTRY_SUBMITTED = 5,
    PDX_V3_GENERATING = 6,
    PDX_V3_LOADING = 7,
    PDX_V3_INGAME = 8,
    PDX_V3_FAILED = 9,
};
struct PdxV3WorldStatusInfo {
    uint32_t size;
    uint32_t installed;
    uint32_t phase;
    uint32_t pending_operation;
    uint32_t error;
    uint32_t interface_thread_id;
    uint32_t application_state;
    uint32_t application_transition_pending;
    uint64_t request_id;
    uint64_t completed_request_id;
    uint64_t main_module_base;
    uint64_t application;
    uint64_t original_vtable;
    uint64_t hook_vtable;
    uint64_t interface_function;
    uint64_t options_factory;
    uint64_t entry_function;
    uint64_t options_result;
    uint64_t world;
    uint64_t active_idler;
    uint64_t active_idler_vtable;
    uint64_t pending_idler;
    uint64_t frame;
};

PDX_API DWORD WINAPI PdxV3InstallInterfaceHook(void* unused);
// options == 0 composes the native factory and native Application entry.
// A successful submit transfers options ownership to the game's idler.
PDX_API DWORD WINAPI PdxV3QueueWorldEntry(uint64_t options);
PDX_API DWORD WINAPI PdxV3QueueCreateOptions(void* unused);
PDX_API DWORD WINAPI PdxV3QueueSubmitOptions(uint64_t options);
PDX_API DWORD WINAPI PdxV3QueueDestroyOptions(uint64_t options);
// Removal is queued to the interface context. Keep the DLL loaded afterwards.
PDX_API DWORD WINAPI PdxV3RemoveInterfaceHook(void* unused);
PDX_API DWORD WINAPI PdxV3Status(PdxV3WorldStatusInfo* result);
// Explicit interface context, not a Session domain or GameStateAccess guard.
// Caller keeps callback code and opaque storage alive through completion.
using PdxV3InterfaceCallback = void (WINAPI*)(void* application, void* opaque);
PDX_API DWORD WINAPI PdxV3QueueInterfaceCallback(uint64_t callback_address, uint64_t opaque);
