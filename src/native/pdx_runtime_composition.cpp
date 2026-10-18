// The one-DLL composition root may know concrete game adapters. The shared
// transport does not include, select or interpret any game-specific adapter.
#include "pdx_runtime_dispatch.h"
#include "v3_bridge_dispatch.h"

DWORD WINAPI PdxRuntimeInitialize(void*) {
    return PdxRuntimeStartTransport(PdxV3DispatchCommand);
}
