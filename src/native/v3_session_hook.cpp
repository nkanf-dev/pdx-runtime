#include "v3_session_hook.h"

extern "C" __declspec(dllexport) PdxV3SessionHookStatusInfo PdxV3SessionHookState = {
    sizeof(PdxV3SessionHookStatusInfo)};
extern "C" __declspec(dllexport) PdxV3SessionCallbackStatusInfo PdxV3SessionCallbackState = {
    sizeof(PdxV3SessionCallbackStatusInfo)};
namespace {
constexpr uintptr_t session_rva = 0x5956f70;
constexpr uintptr_t callback_rva = 0x3386f10;
using GuardEnter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using GuardLeave = void (__fastcall*)(void*);
struct alignas(16) NativeAccessStorage { unsigned char bytes[80] = {}; };
struct NativeAccessScope {
    uintptr_t base;
    NativeAccessStorage& storage;
    NativeAccessScope(uintptr_t image_base, NativeAccessStorage& buffer) : base(image_base), storage(buffer) {
        reinterpret_cast<GuardEnter>(base + 0x33bd640)(storage.bytes, 1, 0,
            "pdx-runtime/native/v3_session_hook.cpp");
    }
    ~NativeAccessScope() { reinterpret_cast<GuardLeave>(base + 0x33bd750)(storage.bytes); }
    NativeAccessScope(const NativeAccessScope&) = delete;
    NativeAccessScope& operator=(const NativeAccessScope&) = delete;
};
SRWLOCK hook_lock = SRWLOCK_INIT;
PdxV3SessionHookBinding binding = {};
volatile LONG active = 0;
bool binding_set = false;
bool native_binding = false;
uint64_t next_callback_request = 0;

DWORD read_memory(uint64_t address, void* output, size_t size) {
    SIZE_T count = 0;
    BOOL ok = ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address), output, size, &count);
    return ok && count == size ? ERROR_SUCCESS : (ok ? ERROR_PARTIAL_COPY : GetLastError());
}
template<class T> DWORD read_field(uint64_t object, size_t offset, T* result) {
    return read_memory(object + offset, result, sizeof(*result));
}
DWORD snapshot_native() {
    auto& status = PdxV3SessionHookState;
    uint8_t init = 0, consumer = 0, execution = 0, blocked = 0, mode = 0, stop = 0;
    DWORD error = read_field(status.session, 0x3d8, &status.dispatcher);
    if (!error) error = read_field(status.session, 0xd0, &status.thread_handle);
    if (!error) error = read_field(status.session, 0x3ec, &status.session_id);
    if (!error) error = read_field(status.session, 0x3f0, &init);
    if (!error) error = read_field(status.session, 0x3f1, &consumer);
    if (!error) error = read_field(status.session, 0x3f3, &execution);
    if (!error) error = read_field(status.session, 0x29a, &blocked);
    if (!error) error = read_field(status.session, 0x290, &mode);
    if (!error) error = read_field(status.session, 0xc8, &stop);
    status.initialized_3f0 = init;
    status.consumer_3f1 = consumer;
    status.execution_3f3 = execution;
    status.receive_blocked_29a = blocked;
    status.session_mode_290 = mode;
    status.thread_stop_c8 = stop;
    if (!error && status.dispatcher) {
        error = read_field(status.dispatcher, 0xc, &status.outgoing_count);
        if (!error) error = read_field(status.dispatcher, 0x6c, &status.incoming_count);
    }
    return error;
}
void dispatch_queued_callback(void* session) {
    uint64_t callbackfn = 0, opaque = 0, request = 0;
    AcquireSRWLockExclusive(&hook_lock);
    auto& queued = PdxV3SessionCallbackState;
    if (active && reinterpret_cast<uintptr_t>(session) == binding.session
        && queued.phase == PDX_V3_SESSION_CALLBACK_QUEUED) {
        callbackfn = queued.callbackfn;
        opaque = queued.opaque;
        request = queued.request_id;
        queued.phase = PDX_V3_SESSION_CALLBACK_EXECUTING;
        queued.execution_thread_id = GetCurrentThreadId();
        queued.session = reinterpret_cast<uintptr_t>(session);
    }
    ReleaseSRWLockExclusive(&hook_lock);
    if (!request) return;
    DWORD error = ERROR_SUCCESS;
    try {
        reinterpret_cast<PdxV3SessionQueuedCallback>(callbackfn)(session, reinterpret_cast<void*>(opaque));
    } catch (...) { error = ERROR_UNHANDLED_EXCEPTION; }
    AcquireSRWLockExclusive(&hook_lock);
    queued.error = error;
    queued.phase = error ? PDX_V3_SESSION_CALLBACK_FAILED : PDX_V3_SESSION_CALLBACK_COMPLETED;
    queued.completed_request_id = request;
    ReleaseSRWLockExclusive(&hook_lock);
}
unsigned char __fastcall callback_hook(void* session) {
    // Immutable binding survives removal and an already-fetched hook pointer.
    auto original = reinterpret_cast<PdxV3SessionCallback>(binding.expected_callback);
    auto drain = reinterpret_cast<PdxV3ConstructionDrain>(binding.drain);
    bool run_drain = false;
    AcquireSRWLockExclusive(&hook_lock);
    auto& status = PdxV3SessionHookState;
    status.execution_thread_id = GetCurrentThreadId();
    ++status.callback_count;
    if (native_binding) status.error = snapshot_native();
    run_drain = InterlockedCompareExchange(&active, 0, 0) != 0
        && reinterpret_cast<uintptr_t>(session) == binding.session;
    if (native_binding) run_drain = run_drain && !status.error && status.initialized_3f0
        && status.dispatcher && status.execution_3f3 && !status.consumer_3f1 && !status.thread_stop_c8;
    ReleaseSRWLockExclusive(&hook_lock);
    dispatch_queued_callback(session);
    if (run_drain && InterlockedCompareExchange(&active, 0, 0)) {
        DWORD error = ERROR_SUCCESS;
        try {
            if (native_binding) {
                // At callback entry neither dispatch nor Session/outgoing lock is held.
                // Release this native scope before the original callback takes them.
                NativeAccessStorage storage;
                NativeAccessScope scope(static_cast<uintptr_t>(status.main_module_base), storage);
                error = drain(session);
            } else error = drain(session);
        } catch (...) { error = ERROR_UNHANDLED_EXCEPTION; }
        AcquireSRWLockExclusive(&hook_lock);
        ++status.drain_count;
        status.last_drain_error = error;
        ReleaseSRWLockExclusive(&hook_lock);
    }
    const unsigned char result = original(session);
    AcquireSRWLockExclusive(&hook_lock);
    status.last_original_result = result;
    if (native_binding) status.error = snapshot_native();
    ReleaseSRWLockExclusive(&hook_lock);
    return result;
}
DWORD pin_module(uint64_t function) {
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
            reinterpret_cast<LPCWSTR>(function), &module)) return GetLastError();
    return ERROR_SUCCESS;
}
DWORD install_at(const PdxV3SessionHookBinding* request) {
    if (!request || request->size != sizeof(*request) || !request->callback_slot
        || request->callback_slot % sizeof(void*) || !request->expected_callback
        || !request->session || !request->drain) return ERROR_INVALID_PARAMETER;
    auto& status = PdxV3SessionHookState;
    if (active && !native_binding) return ERROR_ALREADY_EXISTS;
    if (binding_set && (binding.callback_slot != request->callback_slot
        || binding.expected_callback != request->expected_callback || binding.session != request->session
        || binding.drain != request->drain)) return ERROR_ALREADY_INITIALIZED;
    void* current = nullptr;
    DWORD error = read_memory(request->callback_slot, &current, sizeof(current));
    if (error) return error;
    if (active && current == reinterpret_cast<void*>(&callback_hook)) return ERROR_ALREADY_EXISTS;
    if (reinterpret_cast<uintptr_t>(current) != request->expected_callback) return ERROR_INVALID_STATE;
    // Native init can restore this same binding's original callback without
    // updating our DLL state. Reclaim only that exact slot, under hook_lock.
    // Keep QUEUED request ID/function/opaque intact; an in-flight request must
    // finish before repair, never be cancelled or submitted a second time.
    if (native_binding && PdxV3SessionCallbackState.phase == PDX_V3_SESSION_CALLBACK_EXECUTING)
        return ERROR_BUSY;
    error = pin_module(reinterpret_cast<uintptr_t>(&callback_hook));
    if (!error) error = pin_module(request->drain);
    if (error) return error;
    if (!binding_set) {
        binding = *request;
        binding_set = true;
    }
    status.callback_slot = binding.callback_slot;
    status.original_callback = binding.expected_callback;
    status.hook_callback = reinterpret_cast<uintptr_t>(&callback_hook);
    status.session = binding.session;
    status.drain = binding.drain;
    void* previous = InterlockedCompareExchangePointer(
        reinterpret_cast<void* volatile*>(binding.callback_slot), reinterpret_cast<void*>(&callback_hook),
        reinterpret_cast<void*>(binding.expected_callback));
    if (reinterpret_cast<uintptr_t>(previous) != binding.expected_callback) return ERROR_INVALID_STATE;
    InterlockedExchange(&active, 1);
    status.installed = 1;
    status.current_callback_matches = 1;
    status.error = ERROR_SUCCESS;
    return ERROR_SUCCESS;
}
}
DWORD WINAPI PdxV3SessionHookInstallAt(const PdxV3SessionHookBinding* request) {
    AcquireSRWLockExclusive(&hook_lock);
    DWORD error = install_at(request);
    PdxV3SessionHookState.error = error;
    ReleaseSRWLockExclusive(&hook_lock);
    return error;
}
DWORD WINAPI PdxV3SessionHookInstall(void* carrier) {
    if (!carrier) return ERROR_INVALID_PARAMETER;
    auto drain = GetProcAddress(static_cast<HMODULE>(carrier), "PdxV3DrainConstruction");
    if (!drain) return GetLastError();
    const uintptr_t base = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    const uintptr_t session = base + session_rva;
    uint64_t argument = 0, dispatcher = 0, thread_handle = 0;
    uint8_t initialized = 0, stop = 0;
    DWORD error = read_field(session, 0xc0, &argument);
    if (!error) error = read_field(session, 0x3d8, &dispatcher);
    if (!error) error = read_field(session, 0x3f0, &initialized);
    if (!error) error = read_field(session, 0xd0, &thread_handle);
    if (!error) error = read_field(session, 0xc8, &stop);
    if (error) return error;
    if (argument != session || !dispatcher || !initialized || !thread_handle || stop)
        return ERROR_NOT_READY;
    PdxV3SessionHookBinding request = {sizeof(request), 0, session + 0xb8, base + callback_rva,
        session, reinterpret_cast<uintptr_t>(drain)};
    AcquireSRWLockExclusive(&hook_lock);
    if (binding_set && !native_binding) error = ERROR_ALREADY_INITIALIZED;
    else {
        native_binding = true;
        PdxV3SessionHookState.main_module_base = base;
        PdxV3SessionHookState.carrier = reinterpret_cast<uintptr_t>(carrier);
        error = install_at(&request);
        if (!error) snapshot_native();
    }
    PdxV3SessionHookState.error = error;
    ReleaseSRWLockExclusive(&hook_lock);
    return error;
}
DWORD WINAPI PdxV3SessionHookRemove(void*) {
    AcquireSRWLockExclusive(&hook_lock);
    DWORD error = ERROR_SUCCESS;
    if (!binding_set) error = ERROR_INVALID_STATE;
    else {
        InterlockedExchange(&active, 0);
        void* previous = InterlockedCompareExchangePointer(
            reinterpret_cast<void* volatile*>(binding.callback_slot),
            reinterpret_cast<void*>(binding.expected_callback), reinterpret_cast<void*>(&callback_hook));
        if (previous != reinterpret_cast<void*>(&callback_hook)
            && previous != reinterpret_cast<void*>(binding.expected_callback)) error = ERROR_INVALID_STATE;
        PdxV3SessionHookState.installed = 0;
        PdxV3SessionHookState.current_callback_matches = 0;
        auto& queued = PdxV3SessionCallbackState;
        if (queued.phase == PDX_V3_SESSION_CALLBACK_QUEUED) {
            queued.phase = PDX_V3_SESSION_CALLBACK_CANCELLED;
            queued.error = ERROR_CANCELLED;
            queued.completed_request_id = queued.request_id;
        }
    }
    PdxV3SessionHookState.error = error;
    ReleaseSRWLockExclusive(&hook_lock);
    return error;
}
DWORD WINAPI PdxV3SessionHookStatus(PdxV3SessionHookStatusInfo* result) {
    if (!result) return ERROR_INVALID_PARAMETER;
    AcquireSRWLockExclusive(&hook_lock);
    if (binding_set) {
        void* current = nullptr;
        DWORD error = read_memory(binding.callback_slot, &current, sizeof(current));
        PdxV3SessionHookState.current_callback_matches = !error && current == reinterpret_cast<void*>(&callback_hook);
        if (!error && native_binding) error = snapshot_native();
        if (error) PdxV3SessionHookState.error = error;
    }
    *result = PdxV3SessionHookState;
    ReleaseSRWLockExclusive(&hook_lock);
    return ERROR_SUCCESS;
}

DWORD WINAPI PdxV3QueueSessionCallback(uint64_t callbackfn, uint64_t opaque, uint64_t* request_id) {
    if (!callbackfn || !request_id) return ERROR_INVALID_PARAMETER;
    AcquireSRWLockExclusive(&hook_lock);
    auto& queued = PdxV3SessionCallbackState;
    DWORD error = ERROR_SUCCESS;
    if (!active || !binding_set) error = ERROR_INVALID_STATE;
    else {
        void* current = nullptr;
        error = read_memory(binding.callback_slot, &current, sizeof(current));
        PdxV3SessionHookState.current_callback_matches = !error
            && current == reinterpret_cast<void*>(&callback_hook);
        if (!error && !PdxV3SessionHookState.current_callback_matches)
            error = ERROR_INVALID_STATE;
        if (!error && native_binding) {
            error = snapshot_native();
            const auto& status = PdxV3SessionHookState;
            if (!error && (!status.initialized_3f0 || !status.dispatcher
                || !status.thread_handle || status.thread_stop_c8)) error = ERROR_NOT_READY;
        }
    }
    if (!error && (queued.phase == PDX_V3_SESSION_CALLBACK_QUEUED
        || queued.phase == PDX_V3_SESSION_CALLBACK_EXECUTING)) error = ERROR_BUSY;
    if (!error) {
        queued = {sizeof(queued)};
        queued.request_id = ++next_callback_request;
        queued.phase = PDX_V3_SESSION_CALLBACK_QUEUED;
        queued.callbackfn = callbackfn;
        queued.opaque = opaque;
        *request_id = queued.request_id;
    }
    ReleaseSRWLockExclusive(&hook_lock);
    return error;
}
DWORD WINAPI PdxV3SessionCallbackStatus(PdxV3SessionCallbackStatusInfo* result) {
    if (!result) return ERROR_INVALID_PARAMETER;
    AcquireSRWLockShared(&hook_lock);
    *result = PdxV3SessionCallbackState;
    ReleaseSRWLockShared(&hook_lock);
    return ERROR_SUCCESS;
}
