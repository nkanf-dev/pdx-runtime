#pragma once
#include <Windows.h>
#include <stdint.h>

#ifdef PDX_RUNTIME_BUILD
#define PDX_API extern "C" __declspec(dllexport)
#else
#define PDX_API extern "C" __declspec(dllimport)
#endif

#define PDX_RUNTIME_PROTOCOL 1u
#define PDX_RUNTIME_MAX_RAW 4096u

struct PdxRuntimeStatusInfo {
    uint32_t protocol;
    uint32_t pid;
    uint64_t main_module_base;
    uint64_t dll_base;
    uint32_t error;
    uint32_t max_raw;
};

// Call after LoadLibrary, outside loader lock. Return is a Win32 error code.
PDX_API DWORD WINAPI PdxRuntimeInitialize(void* unused);
PDX_API DWORD WINAPI PdxRuntimeStatus(PdxRuntimeStatusInfo* result);
PDX_API DWORD WINAPI PdxRuntimeRead(uint64_t address, void* output, uint32_t size, uint32_t* received);
PDX_API DWORD WINAPI PdxRuntimeWrite(uint64_t address, const void* data, uint32_t size, uint32_t* written);
