#include "v3_world_entry.h"
#include <cstring>

namespace {
constexpr uintptr_t application_root_rva = 0x592d128;
constexpr uintptr_t application_vtable_rva = 0x4430800;
constexpr uintptr_t frame_function_rva = 0x34b0a90;
constexpr uintptr_t options_factory_rva = 0x11394c0;
constexpr uintptr_t entry_function_rva = 0x661fa0;
constexpr uintptr_t options_vtable_rva = 0x44c2a20;
constexpr uintptr_t frontend_vtable_rva = 0x4497830;
constexpr uintptr_t generating_vtable_rva = 0x4497b98;
constexpr uintptr_t loading_vtable_rva = 0x4497cf0;
constexpr uintptr_t ingame_vtable_rva = 0x4431500;
constexpr size_t application_slots = 43;
using FrameFunction = void (__fastcall*)(void*, double);
using OptionsFactory = void* (__fastcall*)(void**);
using EntryFunction = void (__fastcall*)(void*, void*);
using OptionsDestructor = void* (__fastcall*)(void*, unsigned int);
SRWLOCK state_lock = SRWLOCK_INIT;
PdxV3WorldStatusInfo state = {sizeof(PdxV3WorldStatusInfo)};
uintptr_t shadow_vtable[application_slots + 1] = {};
FrameFunction original_frame = nullptr;
void* hooked_application = nullptr;
uintptr_t base = 0;
uint64_t next_request = 0;
uint64_t pending_options = 0;
uint64_t pending_opaque = 0;
bool observing_entry = false;
thread_local bool draining = false;

template<class T> T& field(void* object, size_t offset) {
    return *reinterpret_cast<T*>(reinterpret_cast<uintptr_t>(object) + offset);
}
void snapshot(void* app) {
    state.application_state = field<uint32_t>(app, 0x588);
    state.application_transition_pending = field<uint8_t>(app, 0x258);
    state.world = field<uintptr_t>(app, 0x608);
    state.active_idler = field<uintptr_t>(app, 0x1c0);
    state.active_idler_vtable = state.active_idler
        ? *reinterpret_cast<uintptr_t*>(state.active_idler) : 0;
    state.pending_idler = field<uintptr_t>(app, 0x248);
    state.frame = field<uint64_t>(app, 0x500);
    if (!observing_entry || state.pending_operation != PDX_V3_NONE) return;
    if (state.active_idler_vtable == base + generating_vtable_rva)
        state.phase = PDX_V3_GENERATING;
    else if (state.active_idler_vtable == base + loading_vtable_rva)
        state.phase = PDX_V3_LOADING;
    else if (state.world && state.application_state == 4
             && state.active_idler_vtable == base + ingame_vtable_rva
             && !state.application_transition_pending) {
        state.phase = PDX_V3_INGAME;
        observing_entry = false;
    }
}
DWORD submit(void* app, void* options) {
    if (field<uint8_t>(app, 0x258) || field<uintptr_t>(app, 0x608))
        return ERROR_BUSY;
    uintptr_t active = field<uintptr_t>(app, 0x1c0);
    if (!active || *reinterpret_cast<uintptr_t*>(active) != base + frontend_vtable_rva)
        return ERROR_INVALID_STATE;
    if (!options || *reinterpret_cast<uintptr_t*>(options) != base + options_vtable_rva)
        return ERROR_INVALID_PARAMETER;
    reinterpret_cast<EntryFunction>(base + entry_function_rva)(app, options);
    return ERROR_SUCCESS;
}
void drain(void* app) {
    if (draining) return;
    draining = true;
    AcquireSRWLockExclusive(&state_lock);
    state.interface_thread_id = GetCurrentThreadId();
    const uint32_t operation = state.pending_operation;
    void* options = reinterpret_cast<void*>(pending_options);
    void* opaque = reinterpret_cast<void*>(pending_opaque);
    pending_opaque = 0;
    state.pending_operation = PDX_V3_NONE;
    pending_options = 0;
    if (operation == PDX_V3_NONE) {
        snapshot(app);
        ReleaseSRWLockExclusive(&state_lock);
        draining = false;
        return;
    }
    state.phase = PDX_V3_EXECUTING;
    const uint64_t request = state.request_id;
    ReleaseSRWLockExclusive(&state_lock);

    DWORD error = ERROR_SUCCESS;
    uint64_t options_result = 0;
    uint32_t phase = PDX_V3_IDLE;
    bool submitted = false;
    try {
        if (operation == PDX_V3_INTERFACE_CALLBACK) {
            reinterpret_cast<PdxV3InterfaceCallback>(options)(app, opaque);
        } else if (operation == PDX_V3_REMOVE_HOOK) {
            InterlockedCompareExchangePointer(reinterpret_cast<void* volatile*>(app),
                reinterpret_cast<void*>(state.original_vtable), shadow_vtable + 1);
            phase = PDX_V3_DETACHED;
        } else if (operation == PDX_V3_CREATE_OPTIONS) {
            reinterpret_cast<OptionsFactory>(base + options_factory_rva)(&options);
            options_result = reinterpret_cast<uintptr_t>(options);
            phase = PDX_V3_OPTIONS_CREATED;
        } else if (operation == PDX_V3_DESTROY_OPTIONS) {
            if (!options || *reinterpret_cast<uintptr_t*>(options) != base + options_vtable_rva)
                error = ERROR_INVALID_PARAMETER;
            else {
                auto table = *reinterpret_cast<uintptr_t**>(options);
                reinterpret_cast<OptionsDestructor>(table[1])(options, 1);
            }
        } else {
            bool created = false;
            if (operation == PDX_V3_ENTER && !options) {
                // Check the actual phase before constructing an owned argument.
                if (field<uint8_t>(app, 0x258) || field<uintptr_t>(app, 0x608))
                    error = ERROR_BUSY;
                else {
                    reinterpret_cast<OptionsFactory>(base + options_factory_rva)(&options);
                    created = true;
                }
            }
            if (!error) error = submit(app, options);
            if (error && created) {
                auto table = *reinterpret_cast<uintptr_t**>(options);
                reinterpret_cast<OptionsDestructor>(table[1])(options, 1);
            }
            if (!error) {
                submitted = true;
                phase = PDX_V3_ENTRY_SUBMITTED;
            }
        }
    } catch (...) {
        // A throwing entry may already own its argument; do not replay or delete it.
        error = ERROR_UNHANDLED_EXCEPTION;
    }
    AcquireSRWLockExclusive(&state_lock);
    state.error = error;
    state.phase = error ? PDX_V3_FAILED : phase;
    state.options_result = options_result;
    state.completed_request_id = request;
    if (submitted) observing_entry = true;
    if (operation == PDX_V3_REMOVE_HOOK && !error) state.installed = 0;
    snapshot(app);
    ReleaseSRWLockExclusive(&state_lock);
    draining = false;
}
void __fastcall frame_hook(void* app, double delta_time) {
    original_frame(app, delta_time);
    if (app == hooked_application) drain(app);
}
DWORD enqueue(uint32_t operation, uint64_t options, uint64_t opaque = 0) {
    AcquireSRWLockExclusive(&state_lock);
    DWORD error = ERROR_SUCCESS;
    if (!state.installed) error = ERROR_INVALID_STATE;
    else if (state.pending_operation || state.phase == PDX_V3_EXECUTING)
        error = ERROR_BUSY;
    else {
        pending_options = options;
        pending_opaque = opaque;
        state.pending_operation = operation;
        state.request_id = ++next_request;
        state.error = 0;
        state.options_result = 0;
        state.phase = PDX_V3_QUEUED;
    }
    ReleaseSRWLockExclusive(&state_lock);
    return error;
}
}

DWORD WINAPI PdxV3InstallInterfaceHook(void*) {
    AcquireSRWLockExclusive(&state_lock);
    if (state.installed) {
        ReleaseSRWLockExclusive(&state_lock);
        return ERROR_ALREADY_EXISTS;
    }
    base = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    void* app = nullptr;
    uint32_t received = 0;
    DWORD read_error = PdxRuntimeRead(base + application_root_rva, &app, sizeof(app), &received);
    if (read_error || received != sizeof(app) || !app) {
        ReleaseSRWLockExclusive(&state_lock);
        return read_error ? read_error : ERROR_NOT_READY;
    }
    uintptr_t* table = nullptr;
    read_error = PdxRuntimeRead(reinterpret_cast<uintptr_t>(app), &table, sizeof(table), &received);
    if (read_error || received != sizeof(table)
        || reinterpret_cast<uintptr_t>(table) != base + application_vtable_rva) {
        ReleaseSRWLockExclusive(&state_lock);
        return read_error ? read_error : ERROR_INVALID_STATE;
    }
    read_error = PdxRuntimeRead(reinterpret_cast<uintptr_t>(table - 1), shadow_vtable,
                                sizeof(shadow_vtable), &received);
    if (read_error || received != sizeof(shadow_vtable)
        || shadow_vtable[9] != base + frame_function_rva
        || shadow_vtable[34] != base + entry_function_rva) {
        ReleaseSRWLockExclusive(&state_lock);
        return read_error ? read_error : ERROR_INVALID_STATE;
    }
    original_frame = reinterpret_cast<FrameFunction>(shadow_vtable[9]);
    shadow_vtable[9] = reinterpret_cast<uintptr_t>(&frame_hook);
    hooked_application = app;
    state.main_module_base = base;
    state.application = reinterpret_cast<uintptr_t>(app);
    state.original_vtable = reinterpret_cast<uintptr_t>(table);
    state.hook_vtable = reinterpret_cast<uintptr_t>(shadow_vtable + 1);
    state.interface_function = base + frame_function_rva;
    state.options_factory = base + options_factory_rva;
    state.entry_function = base + entry_function_rva;
    state.phase = PDX_V3_IDLE;
    state.installed = 1;
    if (InterlockedCompareExchangePointer(reinterpret_cast<void* volatile*>(app),
            shadow_vtable + 1, table) != table) {
        state.installed = 0;
        state.error = ERROR_INVALID_STATE;
        ReleaseSRWLockExclusive(&state_lock);
        return ERROR_INVALID_STATE;
    }
    ReleaseSRWLockExclusive(&state_lock);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3QueueWorldEntry(uint64_t options) { return enqueue(PDX_V3_ENTER, options); }
DWORD WINAPI PdxV3QueueCreateOptions(void*) { return enqueue(PDX_V3_CREATE_OPTIONS, 0); }
DWORD WINAPI PdxV3QueueSubmitOptions(uint64_t options) { return enqueue(PDX_V3_SUBMIT_OPTIONS, options); }
DWORD WINAPI PdxV3QueueDestroyOptions(uint64_t options) { return enqueue(PDX_V3_DESTROY_OPTIONS, options); }
DWORD WINAPI PdxV3RemoveInterfaceHook(void*) { return enqueue(PDX_V3_REMOVE_HOOK, 0); }
DWORD WINAPI PdxV3QueueInterfaceCallback(uint64_t callback_address, uint64_t opaque) {
    if (!callback_address) return ERROR_INVALID_PARAMETER;
    return enqueue(PDX_V3_INTERFACE_CALLBACK, callback_address, opaque);
}
DWORD WINAPI PdxV3Status(PdxV3WorldStatusInfo* result) {
    if (!result) return ERROR_INVALID_PARAMETER;
    AcquireSRWLockShared(&state_lock);
    *result = state;
    ReleaseSRWLockShared(&state_lock);
    return ERROR_SUCCESS;
}
