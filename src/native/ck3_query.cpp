#include "ck3_query.h"
#include <cstring>

extern "C" __declspec(dllexport) PdxCK3StatusInfo PdxCK3State = {sizeof(PdxCK3StatusInfo)};

namespace {
constexpr uintptr_t app_root_rva = 0x5cb87f8;
constexpr uintptr_t app_vtable_rva = 0x449bda8;
constexpr uintptr_t frame_rva = 0x88e4d0;
constexpr size_t app_slots = 29;
constexpr size_t frame_slot = 7;
constexpr uintptr_t world_root_rva = 0x5c68c50;
constexpr uintptr_t world_data_offset = 0xa0;
constexpr uintptr_t active_idler_offset = 0x28;
constexpr uintptr_t transition_offset = 0x30;
constexpr uintptr_t ingame_idler_rva = 0x44d6048;
constexpr uintptr_t database_rva = 0x5c67568;
constexpr uintptr_t fallback_rva = 0x5c67570;
constexpr uintptr_t resolver_rva = 0x896e90;
constexpr uintptr_t health_rva = 0x28c6500;
constexpr uintptr_t birth_date_rva = 0x1774be0;
constexpr uintptr_t guard_enter_rva = 0x383d9b0;
constexpr uintptr_t guard_leave_rva = 0x383dad0;
using Frame = uint64_t (__fastcall*)(void*);
using Resolver = void* (__fastcall*)(const uint32_t*);
using Health = uint64_t* (__fastcall*)(const void*, uint64_t*);
using BirthDate = void* (__fastcall*)(const void*);
using Enter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using Leave = void (__fastcall*)(void*);
SRWLOCK state_lock = SRWLOCK_INIT;
uintptr_t shadow_vtable[app_slots + 1] = {};
uintptr_t base = 0;
Frame original_frame = nullptr;
void* hooked_application = nullptr;
uint64_t next_request = 0;
uint32_t pending_id = 0;
PdxCK3CallbackRequest pending_callback = {};
thread_local bool draining = false;
enum Operation : uint32_t { None = 0, Character = 1, Callback = 2, Remove = 3 };

uintptr_t image_base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T field(uintptr_t object, uintptr_t offset) {
    return *reinterpret_cast<const T*>(object + offset);
}
DWORD read_local(uintptr_t address, void* output, size_t size) {
    SIZE_T received = 0;
    if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address),
                           output, size, &received)) return GetLastError();
    return received == size ? ERROR_SUCCESS : ERROR_PARTIAL_COPY;
}
struct alignas(16) AccessStorage { unsigned char bytes[80] = {}; };
static_assert(sizeof(AccessStorage) == 80);
static_assert(sizeof(PdxCK3CharacterResult) == 48);
static_assert(sizeof(PdxCK3StatusInfo) == 168);
struct AccessScope {
    AccessStorage storage;
    AccessScope() { PdxCK3AccessEnter(storage.bytes, 1, 0, "pdx-runtime/native/ck3_query.cpp"); }
    ~AccessScope() { PdxCK3AccessLeave(storage.bytes); }
    AccessScope(const AccessScope&) = delete;
    AccessScope& operator=(const AccessScope&) = delete;
};
bool ready(uintptr_t app, PdxCK3StatusInfo* snapshot = nullptr) {
    const uintptr_t module = image_base();
    const uintptr_t world = field<uintptr_t>(module, world_root_rva);
    const uintptr_t data = world ? field<uintptr_t>(world, world_data_offset) : 0;
    const uintptr_t idler = app ? field<uintptr_t>(app, active_idler_offset) : 0;
    const uint32_t transition = app ? field<uint8_t>(app, transition_offset) : 0;
    const uintptr_t database = field<uintptr_t>(module, database_rva);
    const bool usable = app && app == field<uintptr_t>(module, app_root_rva)
        && world && data && database && !transition && idler
        && field<uintptr_t>(idler, 0) == module + ingame_idler_rva;
    if (snapshot) {
        snapshot->world = world;
        snapshot->world_data = data;
        snapshot->active_idler = idler;
        snapshot->character_database = database;
        snapshot->transition_pending = transition;
        snapshot->world_ready = usable ? 1 : 0;
    }
    return usable;
}
void drain(void* application) {
    if (draining) return;
    draining = true;
    AcquireSRWLockExclusive(&state_lock);
    PdxCK3State.interface_thread_id = GetCurrentThreadId();
    ++PdxCK3State.callback_count;
    const Operation operation = static_cast<Operation>(PdxCK3State.pending_operation);
    const uint32_t full_id = pending_id;
    const auto callback = pending_callback;
    const uint64_t request = PdxCK3State.request_id;
    PdxCK3State.pending_operation = None;
    PdxCK3State.executing = operation != None;
    ReleaseSRWLockExclusive(&state_lock);

    DWORD error = ERROR_SUCCESS;
    PdxCK3CharacterResult result = {};
    PdxCK3StatusInfo snapshot = {};
    bool removed = false;
    try {
        if (operation == Character) {
            result.requested_id = full_id;
            PdxCK3QueryCharacterInterface(application, &result);
            error = result.error;
        } else if (operation == Callback) {
            reinterpret_cast<PdxCK3InterfaceCallback>(callback.callback)(
                application, reinterpret_cast<void*>(callback.opaque));
        } else if (operation == Remove) {
            removed = InterlockedCompareExchangePointer(
                reinterpret_cast<void* volatile*>(application),
                reinterpret_cast<void*>(PdxCK3State.original_vtable), shadow_vtable + 1)
                == shadow_vtable + 1;
            if (!removed) error = ERROR_INVALID_STATE;
        }
        // World/root status is sampled under the same native interface access
        // scope; callback code is not forced into a scope it did not request.
        AccessScope scope;
        ready(reinterpret_cast<uintptr_t>(application), &snapshot);
    } catch (...) { error = ERROR_UNHANDLED_EXCEPTION; }

    AcquireSRWLockExclusive(&state_lock);
    if (operation != None) {
        PdxCK3State.error = error;
        if (operation == Character) PdxCK3State.character = result;
        PdxCK3State.completed_request_id = request;
    }
    if (removed) PdxCK3State.installed = 0;
    PdxCK3State.executing = 0;
    PdxCK3State.world = snapshot.world;
    PdxCK3State.world_data = snapshot.world_data;
    PdxCK3State.active_idler = snapshot.active_idler;
    PdxCK3State.character_database = snapshot.character_database;
    PdxCK3State.transition_pending = snapshot.transition_pending;
    PdxCK3State.world_ready = snapshot.world_ready;
    ReleaseSRWLockExclusive(&state_lock);
    draining = false;
}
uint64_t __fastcall frame_hook(void* application) {
    const uint64_t original_result = original_frame(application);
    if (application == hooked_application) drain(application);
    return original_result;
}
DWORD enqueue(Operation operation, uint32_t id, const PdxCK3CallbackRequest& callback) {
    AcquireSRWLockExclusive(&state_lock);
    DWORD error = ERROR_SUCCESS;
    if (!PdxCK3State.installed) error = ERROR_INVALID_STATE;
    else if (PdxCK3State.pending_operation || PdxCK3State.executing) error = ERROR_BUSY;
    else {
        pending_id = id;
        pending_callback = callback;
        PdxCK3State.pending_operation = operation;
        PdxCK3State.request_id = ++next_request;
        PdxCK3State.error = 0;
    }
    ReleaseSRWLockExclusive(&state_lock);
    return error;
}
}

uint64_t WINAPI PdxCK3AccessEnter(void* storage, uint32_t mode, int flag, const char* source) {
    return reinterpret_cast<uintptr_t>(reinterpret_cast<Enter>(image_base() + guard_enter_rva)(
        storage, mode, flag, source));
}
void WINAPI PdxCK3AccessLeave(void* storage) {
    reinterpret_cast<Leave>(image_base() + guard_leave_rva)(storage);
}
uint64_t WINAPI PdxCK3ResolveCharacter(uint32_t full_id) {
    return reinterpret_cast<uintptr_t>(reinterpret_cast<Resolver>(image_base() + resolver_rva)(&full_id));
}
DWORD WINAPI PdxCK3CharacterHealthBits(uint64_t character, uint64_t* bits) {
    if (!character || !bits) return ERROR_INVALID_PARAMETER;
    reinterpret_cast<Health>(image_base() + health_rva)(reinterpret_cast<void*>(character), bits);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxCK3QueryCharacter(uint32_t full_id, PdxCK3CharacterResult* result) {
    if (!result) return ERROR_INVALID_PARAMETER;
    *result = {sizeof(PdxCK3CharacterResult), ERROR_NOT_READY, full_id, 0, 0, 0, 0, 0};
    const uintptr_t module = image_base();
    const uintptr_t app = field<uintptr_t>(module, app_root_rva);
    if (!ready(app)) return result->error;
    result->fallback = field<uintptr_t>(module, fallback_rva);
    result->character = PdxCK3ResolveCharacter(full_id);
    if (!result->character || result->character == result->fallback || full_id == UINT32_MAX
        || field<uint32_t>(result->character, 0x1c) != 0x43686172) {
        result->error = ERROR_NOT_FOUND; return result->error;
    }
    result->resolved_id = field<uint32_t>(result->character, 0x18);
    if (result->resolved_id != full_id) {
        result->error = ERROR_NOT_FOUND; return result->error;
    }
    PdxCK3CharacterHealthBits(result->character, &result->health_raw64);
    result->birth_date_address = reinterpret_cast<uintptr_t>(
        reinterpret_cast<BirthDate>(module + birth_date_rva)(reinterpret_cast<void*>(result->character)));
    result->error = ERROR_SUCCESS;
    return ERROR_SUCCESS;
}
void WINAPI PdxCK3QueryCharacterInterface(void* application, void* opaque) {
    auto* result = static_cast<PdxCK3CharacterResult*>(opaque);
    if (!result) return;
    const uint32_t full_id = result->requested_id;
    *result = {sizeof(PdxCK3CharacterResult), ERROR_NOT_READY, full_id, 0, 0, 0, 0, 0};
    if (!application || reinterpret_cast<uintptr_t>(application)
        != field<uintptr_t>(image_base(), app_root_rva)) return;
    try {
        AccessScope scope;
        PdxCK3QueryCharacter(full_id, result);
    } catch (...) { result->error = ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxCK3InstallInterfaceHook(void*) {
    AcquireSRWLockExclusive(&state_lock);
    if (PdxCK3State.installed) {
        ReleaseSRWLockExclusive(&state_lock); return ERROR_ALREADY_EXISTS;
    }
    // An in-flight call may still use the old shadow table after removal.
    // Do not rewrite its original target or storage by reinstalling this module.
    if (original_frame) {
        ReleaseSRWLockExclusive(&state_lock); return ERROR_INVALID_STATE;
    }
    base = image_base();
    void* app = nullptr;
    DWORD error = read_local(base + app_root_rva, &app, sizeof(app));
    uintptr_t* table = nullptr;
    if (!error && app) error = read_local(reinterpret_cast<uintptr_t>(app), &table, sizeof(table));
    if (error || !app || reinterpret_cast<uintptr_t>(table) != base + app_vtable_rva) {
        ReleaseSRWLockExclusive(&state_lock);
        return error ? error : ERROR_NOT_READY;
    }
    error = read_local(reinterpret_cast<uintptr_t>(table - 1), shadow_vtable, sizeof(shadow_vtable));
    if (error || shadow_vtable[frame_slot + 1] != base + frame_rva) {
        ReleaseSRWLockExclusive(&state_lock); return error ? error : ERROR_INVALID_STATE;
    }
    HMODULE pinned = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
            reinterpret_cast<LPCWSTR>(&frame_hook), &pinned)) {
        error = GetLastError();
        ReleaseSRWLockExclusive(&state_lock); return error;
    }
    original_frame = reinterpret_cast<Frame>(shadow_vtable[frame_slot + 1]);
    shadow_vtable[frame_slot + 1] = reinterpret_cast<uintptr_t>(&frame_hook);
    hooked_application = app;
    PdxCK3State.main_module_base = base;
    PdxCK3State.application = reinterpret_cast<uintptr_t>(app);
    PdxCK3State.original_vtable = reinterpret_cast<uintptr_t>(table);
    PdxCK3State.hook_vtable = reinterpret_cast<uintptr_t>(shadow_vtable + 1);
    if (InterlockedCompareExchangePointer(reinterpret_cast<void* volatile*>(app),
                                         shadow_vtable + 1, table) != table) {
        ReleaseSRWLockExclusive(&state_lock); return ERROR_INVALID_STATE;
    }
    PdxCK3State.installed = 1;
    PdxCK3State.error = 0;
    ReleaseSRWLockExclusive(&state_lock);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxCK3QueueCharacter(void* request) {
    if (!request) return ERROR_INVALID_PARAMETER;
    const auto value = *static_cast<PdxCK3CharacterRequest*>(request);
    if (value.size != sizeof(value)) return ERROR_INVALID_PARAMETER;
    return enqueue(Character, value.full_id, {});
}
DWORD WINAPI PdxCK3QueueInterfaceCallback(void* request) {
    if (!request) return ERROR_INVALID_PARAMETER;
    const auto value = *static_cast<PdxCK3CallbackRequest*>(request);
    if (value.size != sizeof(value) || !value.callback) return ERROR_INVALID_PARAMETER;
    return enqueue(Callback, 0, value);
}
DWORD WINAPI PdxCK3RemoveInterfaceHook(void*) { return enqueue(Remove, 0, {}); }
DWORD WINAPI PdxCK3Status(PdxCK3StatusInfo* result) {
    if (!result) return ERROR_INVALID_PARAMETER;
    AcquireSRWLockShared(&state_lock);
    *result = PdxCK3State;
    ReleaseSRWLockShared(&state_lock);
    return ERROR_SUCCESS;
}
