#pragma once
#include <Windows.h>
#include <stdint.h>

#if defined(PDX_NATIVE_CALL_BUILD) || defined(PDX_RUNTIME_BUILD)
#define PDX_NATIVE_CALL_API extern "C" __declspec(dllexport)
#else
#define PDX_NATIVE_CALL_API extern "C" __declspec(dllimport)
#endif

// Ordinary Microsoft x64 ABI, not __vectorcall. Each arguments[i] is one
// positional 64-bit argument bit pattern. Positions 1..4 populate BOTH their
// corresponding GP register (RCX/RDX/R8/R9) and XMM register (XMM0/1/2/3), with
// the upper 64 XMM bits zero. Positions 5+ occupy successive 8-byte stack slots.
// Integers/pointers use their ABI bit representation; scalar float/double use
// IEEE bits in the low 32/64 bits. Structs needing by-reference passing use a
// caller-owned pointer. An ABI-required hidden sret pointer is an explicit
// positional argument and shifts later positions; no signature is inferred.
//
// target/arguments and all pointed-to storage are process-local addresses.
// Caller supplies readable arguments[argument_count], sufficient native stack,
// a valid ordinary-ABI target, and the target's required execution context and
// object lifetimes. There is no profile/address gate or game guard/TLS/domain.
// Return capture is raw RAX and all 128 bits of XMM0, not type interpretation.
struct PdxNativeCallRequest {
    uint32_t size;                 // output: sizeof(PdxNativeCallRequest)
    uint32_t error;                // 997 until call returns; then Win32 status
    uint64_t target;
    uint64_t argument_count;
    const uint64_t* arguments;
    uint64_t rax;
    unsigned char xmm0[16];
};

// Synchronous, on the calling thread. Checks null request/target and a null
// arguments pointer for nonzero count only. Native exceptions/faults propagate;
// no catch, retry, or replay. Ordinary home space, stack alignment, stack probes,
// nonvolatile register preservation and Win64 unwind metadata are supplied.
PDX_NATIVE_CALL_API DWORD WINAPI PdxNativeCall(PdxNativeCallRequest* request) noexcept(false);

// Compatible with the public void WINAPI(first_context, opaque) callback shape.
// opaque is a live request; first_context is ignored. This wrapper ONLY forwards
// to PdxNativeCall. Queue placement does not make arbitrary targets legal there.
// Caller retains request/arguments/pointees until confirmed callback completion.
PDX_NATIVE_CALL_API void WINAPI PdxNativeCallInterface(void* first_context, void* opaque) noexcept(false);

// A caller-owned array of ordinary requests executed in order on ONE native
// callback thread. Requests/arguments/pointees retain their individual ABI and
// lifetime contracts. completed advances after each returned call; a fault or
// exception leaves the executed prefix observable and must never trigger replay.
struct PdxNativeCallBatchRequest {
    uint32_t size;
    uint32_t error;
    uint64_t count;
    PdxNativeCallRequest* requests;
    uint64_t completed;
};
PDX_NATIVE_CALL_API DWORD WINAPI PdxNativeCallBatch(PdxNativeCallBatchRequest* batch) noexcept(false);
PDX_NATIVE_CALL_API void WINAPI PdxNativeCallBatchInterface(void* first_context, void* opaque) noexcept(false);
