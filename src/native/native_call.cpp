#include "native_call.h"
#include <cstddef>
#include <cstring>

static_assert(sizeof(PdxNativeCallRequest) == 56);
static_assert(offsetof(PdxNativeCallRequest, target) == 8);
static_assert(offsetof(PdxNativeCallRequest, argument_count) == 16);
static_assert(offsetof(PdxNativeCallRequest, arguments) == 24);
static_assert(offsetof(PdxNativeCallRequest, rax) == 32);
static_assert(offsetof(PdxNativeCallRequest, xmm0) == 40);

extern "C" void PdxNativeCallAsm(PdxNativeCallRequest* request) noexcept(false);

DWORD WINAPI PdxNativeCall(PdxNativeCallRequest* request) noexcept(false) {
    if (!request) return ERROR_INVALID_PARAMETER;
    request->size = sizeof(*request);
    request->rax = 0;
    std::memset(request->xmm0, 0, sizeof(request->xmm0));
    if (!request->target || (request->argument_count && !request->arguments)) {
        request->error = ERROR_INVALID_PARAMETER;
        return request->error;
    }
    request->error = ERROR_IO_PENDING;
    PdxNativeCallAsm(request);
    request->error = ERROR_SUCCESS;
    return ERROR_SUCCESS;
}

void WINAPI PdxNativeCallInterface(void*, void* opaque) noexcept(false) {
    PdxNativeCall(static_cast<PdxNativeCallRequest*>(opaque));
}

static_assert(sizeof(PdxNativeCallBatchRequest) == 32);
DWORD WINAPI PdxNativeCallBatch(PdxNativeCallBatchRequest* batch) noexcept(false) {
    if (!batch) return ERROR_INVALID_PARAMETER;
    batch->size = sizeof(*batch);
    batch->completed = 0;
    batch->error = ERROR_IO_PENDING;
    if (batch->count && !batch->requests) return batch->error = ERROR_INVALID_PARAMETER;
    for (uint64_t i = 0; i < batch->count; ++i) {
        const DWORD error = PdxNativeCall(batch->requests + i);
        ++batch->completed;
        if (error) return batch->error = error;
    }
    return batch->error = ERROR_SUCCESS;
}
void WINAPI PdxNativeCallBatchInterface(void*, void* opaque) noexcept(false) {
    PdxNativeCallBatch(static_cast<PdxNativeCallBatchRequest*>(opaque));
}
