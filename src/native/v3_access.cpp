#include "v3_access.h"
#include <cstddef>

static_assert(sizeof(PdxV3AccessStorage) == 80);
static_assert(alignof(PdxV3AccessStorage) == 16);
static_assert(sizeof(PdxV3AccessRequest) == 88);
static_assert(offsetof(PdxV3AccessRequest, callback) == 16);
static_assert(offsetof(PdxV3AccessRequest, source) == 32);
static_assert(offsetof(PdxV3AccessRequest, enter) == 40);
static_assert(offsetof(PdxV3AccessRequest, context) == 56);
static_assert(offsetof(PdxV3AccessRequest, execution_thread_id) == 72);

namespace {
constexpr uintptr_t enter_rva = 0x33bd640;
constexpr uintptr_t leave_rva = 0x33bd750;
using NativeEnter = void* (WINAPI*)(void*, uint32_t, int32_t, const char*);
using NativeLeave = void (WINAPI*)(void*);

uintptr_t target(uint64_t exact, uintptr_t rva) {
    return exact ? static_cast<uintptr_t>(exact)
        : reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)) + rva;
}

struct Scope {
    PdxV3AccessStorage storage{};
    PdxV3AccessRequest& request;
    NativeLeave leave;
    explicit Scope(PdxV3AccessRequest& input)
        : request(input), leave(reinterpret_cast<NativeLeave>(target(input.leave, leave_rva))) {
        auto enter = reinterpret_cast<NativeEnter>(target(input.enter, enter_rva));
        request.enter_result = reinterpret_cast<uintptr_t>(enter(
            &storage, request.mode, request.flag, reinterpret_cast<const char*>(request.source)));
        request.entered = 1;
    }
    ~Scope() noexcept {
        leave(&storage);
        request.left = 1;
    }
    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;
};
}

void* WINAPI PdxV3AccessEnter(PdxV3AccessStorage* storage, uint32_t mode,
                            int32_t flag, const char* source) {
    if (!storage || !source) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return nullptr;
    }
    return reinterpret_cast<NativeEnter>(target(0, enter_rva))(storage, mode, flag, source);
}

DWORD WINAPI PdxV3AccessLeave(PdxV3AccessStorage* storage) {
    if (!storage) return ERROR_INVALID_PARAMETER;
    reinterpret_cast<NativeLeave>(target(0, leave_rva))(storage);
    return ERROR_SUCCESS;
}

DWORD WINAPI PdxV3AccessInvoke(void* context, PdxV3AccessRequest* request) {
    if (!request) return ERROR_INVALID_PARAMETER;
    request->size = sizeof(*request);
    request->context = reinterpret_cast<uintptr_t>(context);
    request->execution_thread_id = GetCurrentThreadId();
    request->entered = request->left = request->reserved = 0;
    request->enter_result = 0;
    if (!request->callback || !request->source) {
        request->error = ERROR_INVALID_PARAMETER;
        return request->error;
    }
    request->error = ERROR_IO_PENDING;
    try {
        {
            Scope scope(*request);
            reinterpret_cast<PdxV3AccessHandler>(request->callback)(
                context, reinterpret_cast<void*>(request->opaque));
        }
        request->error = ERROR_SUCCESS;
        return ERROR_SUCCESS;
    } catch (...) {
        request->error = ERROR_UNHANDLED_EXCEPTION;
        return request->error;
    }
}

void WINAPI PdxV3AccessCallback(void* context, void* opaque) {
    PdxV3AccessInvoke(context, static_cast<PdxV3AccessRequest*>(opaque));
}
