#pragma once
#include "ck3_query.h"

#if defined(PDX_CK3_WORLD_ENTRY_BUILD)
#define PDX_CK3_WORLD_API extern "C" __declspec(dllexport)
#else
#define PDX_CK3_WORLD_API extern "C" __declspec(dllimport)
#endif

// CK3 1.20.0.2 / ae1ba6ff060b. Uses the ck3_query interface hook; no second hook.
enum PdxCK3WorldOperation : uint32_t {
    PDX_CK3_WORLD_INSPECT = 0,
    PDX_CK3_WORLD_ENTER_DEFAULT_BOOKMARK = 1,
    PDX_CK3_WORLD_CREATE_DEFAULT_OPTIONS = 2,
    PDX_CK3_WORLD_SUBMIT_BOOKMARK_OPTIONS = 3,
    PDX_CK3_WORLD_DESTROY_OPTIONS = 4,
};
enum PdxCK3WorldPhase : uint32_t {
    PDX_CK3_WORLD_IDLE = 0,
    PDX_CK3_WORLD_QUEUED = 1,
    PDX_CK3_WORLD_EXECUTING = 2,
    PDX_CK3_WORLD_OPTIONS_CREATED = 3,
    PDX_CK3_WORLD_ENTRY_SUBMITTED = 4,
    PDX_CK3_WORLD_GENERATING = 5,
    PDX_CK3_WORLD_LOADING = 6,
    PDX_CK3_WORLD_READY = 7,
    PDX_CK3_WORLD_FAILED = 8,
};
enum PdxCK3OptionsOwnership : uint32_t {
    PDX_CK3_OPTIONS_NONE = 0,
    PDX_CK3_OPTIONS_CALLER = 1,
    PDX_CK3_OPTIONS_GAME = 2,
    PDX_CK3_OPTIONS_UNKNOWN = 3,
};
struct PdxCK3WorldRequest {
    uint32_t size;
    uint32_t operation;
    uint64_t options;
    uint64_t reserved;
};
struct PdxCK3WorldStatusInfo {
    uint32_t size;
    uint32_t phase;
    uint32_t error;
    uint32_t operation;
    uint32_t interface_thread_id;
    uint32_t transition_pending;
    uint32_t options_ownership;
    uint32_t world_ready;
    uint64_t request_id;
    uint64_t completed_request_id;
    uint64_t main_module_base;
    uint64_t application;
    uint64_t options_result;
    uint64_t active_idler;
    uint64_t active_idler_vtable;
    uint64_t pending_idler;
    uint64_t world;
    uint64_t world_data;
};

// Copies the 24-byte request, then queues a public ck3_query callback. Suitable
// for RemoteProcess.start_thread. Install PdxCK3InstallInterfaceHook first.
PDX_CK3_WORLD_API DWORD WINAPI PdxCK3QueueWorldOperation(void* request);
// Sampled status contains no live domain calls. Queue INSPECT to refresh the
// lifecycle sample on the real interface thread. Pointers are not durable handles.
PDX_CK3_WORLD_API DWORD WINAPI PdxCK3WorldStatus(PdxCK3WorldStatusInfo* result);
extern "C" __declspec(dllexport) PdxCK3WorldStatusInfo PdxCK3WorldState;

// Synchronous low-level native ABI: require the game's interface context and
// native access scope. Never use these as RemoteThread entry points.
// Optional input is 48 bytes; +0x28 is presence. The factory can move an engaged
// optional's native string. A zeroed optional produces native defaults. Native
// RAX returns output, not the allocated options object; read *output instead.
PDX_CK3_WORLD_API void* WINAPI PdxCK3NativeOptionsFactory(void** output, void* optional_input);
PDX_CK3_WORLD_API void* WINAPI PdxCK3NativeDeleteOptions(void* options, uint32_t flags);
// On normal return the game's bookmark idler owns options; do not delete/reuse.
// Returned native RAX is not an accepted/executed/world-ready result.
PDX_CK3_WORLD_API uint64_t WINAPI PdxCK3NativeEnterBookmark(void* application, void* options);
// Load entry consumes an owned native CFile, NOT a CString or pathname. It copies
// payload via source.vslot5, deletes the source, and stores options in load logic.
// Normal onward InGame entry deletes options. Exception ownership is unknown.
// This is low-level payload access, not a proven .ck3 ZIP-file loader.
PDX_CK3_WORLD_API uint64_t WINAPI PdxCK3NativeLoadPayload(void* application, void* owned_file, void* options);
