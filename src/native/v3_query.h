#pragma once
#include <Windows.h>
#include <stdint.h>

#if defined(PDX_V3_QUERY_BUILD) || defined(PDX_RUNTIME_BUILD)
#define PDX_V3_QUERY_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_QUERY_API extern "C" __declspec(dllimport)
#endif

// V3 SHA256 7f53e3260cf46b6247f6906837464944038c4f74e860274ad3d394e0c04f811f only.
// These synchronous primitives run in the caller's native game-access scope.
// An interface callback alone is NOT such a scope. No worker-thread dispatch here.
// Returned pointers are raw process-local addresses, not retained references.
struct PdxV3CountryResult {
    uint32_t size;
    uint32_t error;
    uint32_t requested_id;
    uint32_t resolved_id;
    uint64_t country;
    uint64_t fallback;
    uint64_t gdp_raw64;
};

// Direct call of native resolver 0x7bb480. It can return the non-null fallback.
// Database-null and non-invalid ID can enter the game's native diagnostic path.
PDX_V3_QUERY_API uint64_t WINAPI PdxV3ResolveCountry(uint32_t full_id);
// Direct call of native getter 0x1bc28e0 with its minimal ID-bearing wrapper.
// This primitive preserves the native fallback behavior; it does not prove validity.
PDX_V3_QUERY_API DWORD WINAPI PdxV3CountryGdpBits(uint32_t full_id, uint64_t* bits);
// Compose resolver + full-ID/fallback checks + native getter in ONE existing scope.
// error distinguishes unavailable/invalid from a genuine raw64 zero value.
PDX_V3_QUERY_API DWORD WINAPI PdxV3QueryCountry(uint32_t full_id, PdxV3CountryResult* result);

// Callback-compatible composition for PdxV3QueueInterfaceCallback.
// opaque points to a live PdxV3CountryResult with requested_id set by the caller.
// Caller retains this storage until callback completion. It enters native (1,0)
// Interface Gfx access scope 0x33bd640 and restores it with 0x33bd750.
// Those numbers name observed native scope values, NOT a claimed read-only permission.
PDX_V3_QUERY_API void WINAPI PdxV3QueryCountryInterface(void* application, void* opaque);
