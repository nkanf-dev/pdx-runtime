#pragma once
#include <Windows.h>
#include <stdint.h>

#if defined(PDX_V3_ACCESS_BUILD) || defined(PDX_RUNTIME_BUILD)
#define PDX_V3_ACCESS_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_ACCESS_API extern "C" __declspec(dllimport)
#endif

// Native 0x33bd640/0x33bd750 storage: 80 bytes, aligned to 16 bytes.
// These are synchronous current-thread primitives, not thread entry points.
// Keep Enter and Leave on one thread in one native call stack. Do not retain an
// entered guard across queued callbacks or return to Python while it is entered.
struct alignas(16) PdxV3AccessStorage { unsigned char bytes[80]; };

// mode/flag are raw native values, not claimed read/write permissions. (1,0)
// is observed around Interface Gfx access; that fact does not prove other thread
// domains or arbitrary targets legal. Caller proves context and object lifetimes.
// Null storage/source sets ERROR_INVALID_PARAMETER and returns null. Otherwise
// the native Enter return is raw, including null; no success meaning is inferred.
PDX_V3_ACCESS_API void* WINAPI PdxV3AccessEnter(PdxV3AccessStorage* storage,
    uint32_t mode, int32_t flag, const char* source);
PDX_V3_ACCESS_API DWORD WINAPI PdxV3AccessLeave(PdxV3AccessStorage* storage);

typedef void (WINAPI* PdxV3AccessHandler)(void* context, void* opaque);

// Caller owns request, callback code, source string and opaque/pointees until
// callback completion. Native guard storage is local to the same callback.
// Zero enter/leave select main-module+0x33bd640/+0x33bd750; nonzero values are
// exact ABI-compatible addresses, useful for explicit composition/host tests.
struct PdxV3AccessRequest {
    uint32_t size;
    uint32_t error;
    uint32_t mode;
    int32_t flag;
    uint64_t callback;
    uint64_t opaque;
    uint64_t source;
    uint64_t enter;
    uint64_t leave;
    uint64_t context;
    uint64_t enter_result;
    uint32_t execution_thread_id;
    uint32_t entered;
    uint32_t left;
    uint32_t reserved;
};

// Enter -> caller's void(context,opaque) -> Leave in ONE invocation. No dispatch,
// TLS setup, game permission inference or automatic thread-domain choice. The
// raw mode/flag and source are passed through unchanged. Guard Leave executes
// on normal return and C++ unwind from the callback. After restoration, a caught
// C++ exception reports ERROR_UNHANDLED_EXCEPTION rather than crossing this C
// export or the game callback boundary. Native faults are not converted or
// replayed. The native Leave operation is expected not to throw.
PDX_V3_ACCESS_API DWORD WINAPI PdxV3AccessInvoke(void* context, PdxV3AccessRequest* request);

// Compatible with BOTH existing Interface and Session callback shapes. Queue
// placement is chosen by the caller; this helper does not force Interface.
PDX_V3_ACCESS_API void WINAPI PdxV3AccessCallback(void* context, void* opaque);
