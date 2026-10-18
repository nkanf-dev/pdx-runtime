// Internal composition contract. This header has no game adapter dependency.
#pragma once
#include "pdx_runtime_bridge.h"

constexpr uint32_t PDX_RUNTIME_MESSAGE_BYTES = PDX_RUNTIME_MAX_RAW * 2 + 256;

// Runs synchronously with other transport dispatches serialized. Worker thread
// identity is not fixed. Return true only when
// the adapter owns the command (including its malformed argument responses).
// command/token_context/response are borrowed until return; never retain them.
// A false return must leave response and the token stream untouched.
// response_capacity includes NUL.
// The handler code must remain loaded for the worker lifetime. This is not a
// game execution-context callback and grants no native game-call permission.
using PdxRuntimeCommandHandler = bool (*)(const char* command, char* token_context,
    char* response, uint32_t response_capacity);

// Composition calls this outside loader lock. The handler is fixed before the
// first listener/workers; there is no runtime plugin or re-registration API.
// Repeated calls return ALREADY_INITIALIZED while listening. Explicit restart
// after listener failure preserves that handler and existing operation owners.
DWORD PdxRuntimeStartTransport(PdxRuntimeCommandHandler handler);

// Internal dispatch entry for the worker and isolated, stub-adapter host tests.
// The caller owns writable, NUL-terminated request and a MESSAGE_BYTES response.
void PdxRuntimeDispatchRequest(char* request, char* response,
    PdxRuntimeCommandHandler handler);
