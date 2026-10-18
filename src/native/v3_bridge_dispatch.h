// Internal Victoria 3 wire adapter, selected by the native composition root.
#pragma once
#include "pdx_runtime_dispatch.h"

bool PdxV3DispatchCommand(const char* command, char* token_context,
    char* response, uint32_t response_capacity);
