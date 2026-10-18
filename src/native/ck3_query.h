#pragma once
#include <Windows.h>
#include <stdint.h>

#if defined(PDX_CK3_QUERY_BUILD)
#define PDX_CK3_API extern "C" __declspec(dllexport)
#else
#define PDX_CK3_API extern "C" __declspec(dllimport)
#endif

// CK3 1.20.0.2 / ae1ba6ff060b only. Synchronous native functions require
// a live game thread and access scope; they are NOT RemoteThread entry points.
struct PdxCK3CharacterResult {
    uint32_t size;
    uint32_t error;
    uint32_t requested_id;
    uint32_t resolved_id;
    uint64_t character;
    uint64_t fallback;
    uint64_t health_raw64;
    uint64_t birth_date_address;
};
struct PdxCK3CharacterRequest {
    uint32_t size;
    uint32_t full_id;
};
using PdxCK3InterfaceCallback = void (WINAPI*)(void* application, void* opaque);
struct PdxCK3CallbackRequest {
    uint32_t size;
    uint32_t reserved;
    uint64_t callback;
    uint64_t opaque;
};
struct PdxCK3StatusInfo {
    uint32_t size;
    uint32_t installed;
    uint32_t error;
    uint32_t interface_thread_id;
    uint32_t pending_operation;
    uint32_t executing;
    uint32_t world_ready;
    uint32_t transition_pending;
    uint64_t request_id;
    uint64_t completed_request_id;
    uint64_t callback_count;
    uint64_t main_module_base;
    uint64_t application;
    uint64_t original_vtable;
    uint64_t hook_vtable;
    uint64_t world;
    uint64_t world_data;
    uint64_t active_idler;
    uint64_t character_database;
    PdxCK3CharacterResult character;
};

// These four copied-request entries are DWORD WINAPI(void*), suitable for the
// public RemoteProcess.start_thread. Install pins the module; after removal,
// reinstall on this module is refused so in-flight old hooks retain their target.
PDX_CK3_API DWORD WINAPI PdxCK3InstallInterfaceHook(void* unused);
PDX_CK3_API DWORD WINAPI PdxCK3QueueCharacter(void* request);
PDX_CK3_API DWORD WINAPI PdxCK3QueueInterfaceCallback(void* request);
PDX_CK3_API DWORD WINAPI PdxCK3RemoveInterfaceHook(void* unused);
PDX_CK3_API DWORD WINAPI PdxCK3Status(PdxCK3StatusInfo* result);

// Raw status is a sampled copy. Remote readers can see a concurrent update;
// inspect request/completion IDs, not pointer lifetime or a world epoch.
extern "C" __declspec(dllexport) PdxCK3StatusInfo PdxCK3State;

PDX_CK3_API uint64_t WINAPI PdxCK3ResolveCharacter(uint32_t full_id);
PDX_CK3_API DWORD WINAPI PdxCK3CharacterHealthBits(uint64_t character, uint64_t* bits);
PDX_CK3_API DWORD WINAPI PdxCK3QueryCharacter(uint32_t full_id, PdxCK3CharacterResult* result);
// Callback-compatible guarded query; opaque is caller-owned result storage.
PDX_CK3_API void WINAPI PdxCK3QueryCharacterInterface(void* application, void* opaque);

// Direct low-level native guard access. storage must be 80 bytes, align16 and
// live through leave; mode/flag retain native meaning, not invented permissions.
PDX_CK3_API uint64_t WINAPI PdxCK3AccessEnter(void* storage, uint32_t mode, int flag, const char* source);
PDX_CK3_API void WINAPI PdxCK3AccessLeave(void* storage);
