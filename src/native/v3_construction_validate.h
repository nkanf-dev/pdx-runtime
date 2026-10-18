#pragma once
#include "v3_construction.h"

struct PdxV3ConstructionValidationInfo {
    uint32_t size;
    uint32_t error;
    uint32_t actor_id;
    uint32_t state_id;
    uint64_t building_type;
    uint64_t carrier;
    uint32_t raw_low;
    uint32_t raw_high;
    uint32_t validated;
    uint32_t execution_thread_id;
    uint64_t application;
    uint64_t world;
};

// Native low-level validator; caller owns the command and execution scope.
// RCX=C+18, RDX=two raw uint32 output words, R8=null diagnostics.
PDX_API DWORD WINAPI PdxV3ValidateConstructionCommand(uint64_t command, uint32_t output[2]);
// Callback-compatible composition for the already-loaded carrier's interface queue.
// opaque is live ValidationInfo storage; set size/actor/state/type/carrier first.
// Builds with carrier Factory/Initialize, enters native (1,0), validates, destroys.
// This does NOT submit or reserve a successful validation for later execution.
PDX_API void WINAPI PdxV3ValidateConstructionInterface(void* application, void* opaque);
