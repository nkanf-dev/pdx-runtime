#pragma once
#include "pdx_runtime_bridge.h"

struct PdxV3ConstructionRequest {
    uint32_t size;
    uint32_t actor_id;
    uint32_t state_id;
    uint32_t reserved;
    uint64_t building_type;
};
struct PdxV3ConstructionQueueInfo {
    uint32_t size;
    uint32_t error;
    uint64_t country;
    uint64_t queue;
    uint64_t records;
    uint32_t count;
    uint32_t capacity;
    uint32_t matching_count;
    uint32_t first_matching_index;
    uint32_t state_id;
    uint32_t reserved;
    uint64_t building_type;
};
enum PdxV3ConstructionPhase : uint32_t {
    PDX_V3_CONSTRUCTION_IDLE = 0,
    PDX_V3_CONSTRUCTION_QUEUED = 1,
    PDX_V3_CONSTRUCTION_SUBMITTING = 2,
    PDX_V3_CONSTRUCTION_ACCEPTED = 3,
    PDX_V3_CONSTRUCTION_REJECTED = 4,
    PDX_V3_CONSTRUCTION_EFFECT_OBSERVED = 5,
    PDX_V3_CONSTRUCTION_EFFECT_UNKNOWN = 6,
    PDX_V3_CONSTRUCTION_FAILED = 7,
};
struct PdxV3ConstructionStatusInfo {
    uint32_t size;
    uint32_t phase;
    uint32_t error;
    uint32_t accepted;
    uint32_t execution_thread_id;
    uint32_t effect_observed;
    uint64_t request_id;
    uint64_t completed_request_id;
    uint64_t main_module_base;
    uint64_t session;
    uint64_t world;
    PdxV3ConstructionRequest request;
    PdxV3ConstructionQueueInfo before;
    PdxV3ConstructionQueueInfo after;
};

// Queue/Status are worker-safe. Request pointers are copied, never retained.
// This first command requests one level, mode 1, optional -1/[5963ea8], byte68=0.
PDX_API DWORD WINAPI PdxV3QueueConstruction(const PdxV3ConstructionRequest* request);
PDX_API DWORD WINAPI PdxV3ConstructionStatus(PdxV3ConstructionStatusInfo* result);
// Call once per native Session callback 3386f10, with Session=base+5956f70.
// The bridge's IPC thread must not call this. No hook is installed by this unit.
PDX_API DWORD WINAPI PdxV3DrainConstruction(void* session);

// Low-level native calls require the caller's valid game execution context.
// Factory returns an owned 112-byte command; Initialize only fills that object.
// Submit consumes ownership on both acceptance and rejection. Never replay it.
PDX_API DWORD WINAPI PdxV3ConstructionFactory(uint64_t* owned);
PDX_API DWORD WINAPI PdxV3InitializeConstructionCommand(uint64_t command,
    const PdxV3ConstructionRequest* request);
PDX_API DWORD WINAPI PdxV3SubmitConstruction(uint64_t* owned, uint32_t flags,
    uint32_t* accepted);
PDX_API DWORD WINAPI PdxV3DestroyConstruction(uint64_t* owned);
// Raw queue access is public and is not a lock-protected consistent snapshot.
// Filters use full State ID and the exact native Building Type pointer.
PDX_API DWORD WINAPI PdxV3ReadConstructionQueue(uint64_t country, uint32_t state_id,
    uint64_t building_type, PdxV3ConstructionQueueInfo* result);
