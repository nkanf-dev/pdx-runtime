#pragma once
#include <Windows.h>
#include <stdint.h>

#ifdef PDX_V3_COMMANDS_BUILD
#define PDX_V3_COMMAND_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_COMMAND_API extern "C" __declspec(dllimport)
#endif

enum PdxV3CommandAction : uint32_t {
    PDX_V3_COMMAND_ENUMERATE = 1,
    PDX_V3_COMMAND_CREATE = 2,
    PDX_V3_COMMAND_DESERIALIZE = 3,
    PDX_V3_COMMAND_SERIALIZE = 4,
    PDX_V3_COMMAND_VALIDATE = 5,
    PDX_V3_COMMAND_DESTROY = 6,
    PDX_V3_COMMAND_SUBMIT = 7,
    PDX_V3_COMMAND_CLONE = 8,
    PDX_V3_COMMAND_TOKEN_NAME = 9,
    PDX_V3_COMMAND_RESOLVE_TOKEN = 10,
};
struct PdxV3CommandRegistration {
    uint32_t type_token;
    uint32_t factory_index;
    uint64_t factory;
    char token_name[128];
};
// VALIDATE with nonzero buffer_capacity returns native reason UTF-8 in buffer;
// buffer_size is its full byte length, even if the copied prefix is truncated.
// Zero capacity retains nullable primary validation. CLONE writes an owned uint64
// pointer to the first eight buffer bytes and leaves command as the source.
// TOKEN_NAME copies UTF-8/full byte length through the same buffer; RESOLVE_TOKEN
// reads buffer_size input bytes and returns type_token (UINT32_MAX if not found).
// Layout remains 88 bytes.
struct PdxV3CommandResult {
    uint32_t size;
    uint32_t error;
    uint32_t action;
    uint32_t type_token;
    uint64_t command;
    uint64_t buffer;
    uint32_t buffer_capacity;
    uint32_t buffer_size;
    uint32_t count;
    uint32_t submit_flags;
    uint32_t validated;
    uint32_t accepted;
    uint32_t execution_thread_id;
    uint32_t reserved;
    uint64_t application;
    uint64_t world;
    uint64_t session;
};

// Raw primitives require the caller's real native access scope/thread. A created
// command belongs to the caller until Destroy or Submit consumes *owned. No game
// object fields are inferred. CReader/CMemoryFile build every typed container.
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandFactory(uint32_t type_token, uint64_t* owned);
// Primary slot8 native copy construction. Source remains owned; output must be
// zero and becomes independently owned until Destroy or Submit consumes it.
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandClone(uint64_t source, uint64_t* owned);
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandDestroy(uint64_t* owned);
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandType(uint64_t command, uint32_t* type_token);
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandValidate(uint64_t command, uint32_t* valid);
// Raw primary slot6 with a caller-owned initialized 32B native CString, or null.
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandValidateReason(uint64_t command, void* native_reason, uint32_t* valid);
// Owns a temporary native CString; copies UTF-8 bytes without a trailing NUL.
// size receives the full byte count, even when capacity returns INSUFFICIENT_BUFFER.
// A false native bool is not a DWORD error and does not consume command ownership.
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandValidateText(uint64_t command, void* bytes, uint32_t capacity,
    uint32_t* size, uint32_t* valid);
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandSubmit(uint64_t* owned, uint32_t flags, uint32_t* accepted);
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandDeserialize(const void* bytes, uint32_t size, uint64_t* owned);
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandSerialize(uint64_t command, void* bytes, uint32_t capacity, uint32_t* size);
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandEnumerate(PdxV3CommandRegistration* entries, uint32_t capacity, uint32_t* count);
// Engine token directory, not the command factory list. A raw name pointer is
// a borrowed native CString (including the engine fallback) and must not be freed.
// Text snapshots hold the native directory read lock while copying UTF-8.
PDX_V3_COMMAND_API uint64_t WINAPI PdxV3CommandTokenName(uint32_t type_token);
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandTokenNameText(uint32_t type_token, void* bytes,
    uint32_t capacity, uint32_t* size);
// Native hash lookup only: no token interning, registration or factory creation.
// Unknown names return NOT_FOUND and UINT32_MAX, never a fabricated token zero.
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandResolveToken(const char* name, uint32_t name_bytes,
    uint32_t* type_token);
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandRead(uint64_t command, void* native_reader);
PDX_V3_COMMAND_API DWORD WINAPI PdxV3CommandWrite(uint64_t command, void* native_writer);
PDX_V3_COMMAND_API void WINAPI PdxV3CommandInterface(void* application, void* opaque);
PDX_V3_COMMAND_API void WINAPI PdxV3CommandSession(void* session, void* opaque);
