#include "ck3_world_entry.h"

extern "C" __declspec(dllexport) PdxCK3WorldStatusInfo PdxCK3WorldState = {
    sizeof(PdxCK3WorldStatusInfo)};

namespace {
constexpr uintptr_t application_root_rva = 0x5cb87f8;
constexpr uintptr_t options_factory_rva = 0xe92780;
constexpr uintptr_t options_delete_rva = 0xe91d90;
constexpr uintptr_t options_vtable_rva = 0x44fc298;
constexpr uintptr_t bookmark_entry_rva = 0x892b30;
constexpr uintptr_t load_entry_rva = 0x892e40;
constexpr uintptr_t frontend_idler_rva = 0x44d5f30;
constexpr uintptr_t generating_idler_rva = 0x44d2c90;
constexpr uintptr_t loading_idler_rva = 0x44d2d98;
constexpr uintptr_t ingame_idler_rva = 0x44d6048;
constexpr uintptr_t world_root_rva = 0x5c68c50;
constexpr uintptr_t character_database_rva = 0x5c67568;
constexpr uintptr_t active_idler_offset = 0x28;
constexpr uintptr_t transition_offset = 0x30;
constexpr uintptr_t pending_idler_offset = 0x78;
constexpr uintptr_t bookmark_options_offset = 0x70;
constexpr uintptr_t world_data_offset = 0xa0;
using OptionsFactory = void* (__fastcall*)(void**, void*);
using OptionsDelete = void* (__fastcall*)(void*, uint32_t);
using BookmarkEntry = uint64_t (__fastcall*)(void*, void*);
using LoadEntry = uint64_t (__fastcall*)(void*, void*, void*);
struct alignas(8) EmptyOptional { unsigned char bytes[48] = {}; };
struct alignas(16) AccessStorage { unsigned char bytes[80] = {}; };
static_assert(sizeof(EmptyOptional) == 48);
static_assert(sizeof(AccessStorage) == 80);
static_assert(sizeof(PdxCK3WorldRequest) == 24);
static_assert(sizeof(PdxCK3WorldStatusInfo) == 112);
SRWLOCK state_lock = SRWLOCK_INIT;
PdxCK3WorldRequest pending = {};
uint64_t next_request = 0;
bool in_flight = false;

uintptr_t image_base() {
    return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
}
template<class T> T field(uintptr_t object, uintptr_t offset) {
    return *reinterpret_cast<const T*>(object + offset);
}
struct AccessScope {
    AccessStorage storage;
    AccessScope() {
        PdxCK3AccessEnter(storage.bytes, 1, 0, "pdx-runtime/native/ck3_world_entry.cpp");
    }
    ~AccessScope() { PdxCK3AccessLeave(storage.bytes); }
    AccessScope(const AccessScope&) = delete;
    AccessScope& operator=(const AccessScope&) = delete;
};
void snapshot(void* application, PdxCK3WorldStatusInfo& value) {
    const uintptr_t module = image_base();
    const uintptr_t app = reinterpret_cast<uintptr_t>(application);
    value.main_module_base = module;
    value.application = app;
    value.transition_pending = field<uint8_t>(app, transition_offset);
    value.active_idler = field<uintptr_t>(app, active_idler_offset);
    value.active_idler_vtable = value.active_idler
        ? field<uintptr_t>(value.active_idler, 0) : 0;
    value.pending_idler = field<uintptr_t>(app, pending_idler_offset);
    value.world = field<uintptr_t>(module, world_root_rva);
    value.world_data = value.world ? field<uintptr_t>(value.world, world_data_offset) : 0;
    value.world_ready = !value.transition_pending && value.world && value.world_data
        && field<uintptr_t>(module, character_database_rva)
        && value.active_idler_vtable == module + ingame_idler_rva;
    if (value.world_ready) value.phase = PDX_CK3_WORLD_READY;
    else if (value.active_idler_vtable == module + generating_idler_rva)
        value.phase = PDX_CK3_WORLD_GENERATING;
    else if (value.active_idler_vtable == module + loading_idler_rva)
        value.phase = PDX_CK3_WORLD_LOADING;
}
DWORD bookmark_phase(void* application) {
    const uintptr_t module = image_base();
    const uintptr_t app = reinterpret_cast<uintptr_t>(application);
    if (field<uint8_t>(app, transition_offset)) return ERROR_BUSY;
    const uintptr_t idler = field<uintptr_t>(app, active_idler_offset);
    if (!idler || field<uintptr_t>(idler, 0) != module + frontend_idler_rva)
        return ERROR_INVALID_STATE;
    return ERROR_SUCCESS;
}
bool is_options(void* options) {
    return options && field<uintptr_t>(reinterpret_cast<uintptr_t>(options), 0)
        == image_base() + options_vtable_rva;
}
void WINAPI world_callback(void* application, void*) {
    AcquireSRWLockExclusive(&state_lock);
    const auto request = pending;
    auto value = PdxCK3WorldState;
    value.phase = PDX_CK3_WORLD_EXECUTING;
    value.interface_thread_id = GetCurrentThreadId();
    PdxCK3WorldState = value;
    ReleaseSRWLockExclusive(&state_lock);

    void* options = reinterpret_cast<void*>(request.options);
    if (request.operation == PDX_CK3_WORLD_SUBMIT_BOOKMARK_OPTIONS
        || request.operation == PDX_CK3_WORLD_DESTROY_OPTIONS) {
        value.options_result = request.options;
        value.options_ownership = PDX_CK3_OPTIONS_CALLER;
    } else if (request.operation == PDX_CK3_WORLD_ENTER_DEFAULT_BOOKMARK
               || request.operation == PDX_CK3_WORLD_CREATE_DEFAULT_OPTIONS) {
        value.options_result = 0;
        value.options_ownership = PDX_CK3_OPTIONS_NONE;
    }
    bool created = false;
    bool called_entry = false;
    DWORD error = ERROR_SUCCESS;
    try {
        PdxCK3StatusInfo hook = {};
        PdxCK3Status(&hook);
        const uintptr_t app = reinterpret_cast<uintptr_t>(application);
        if (!application || app != field<uintptr_t>(image_base(), application_root_rva)
            || hook.application != app || hook.interface_thread_id != GetCurrentThreadId()
            || !hook.executing) error = ERROR_INVALID_STATE;
        if (!error) {
            AccessScope access;
            if (request.operation == PDX_CK3_WORLD_INSPECT) {
                value.phase = PDX_CK3_WORLD_IDLE;
                snapshot(application, value);
            } else if (request.operation == PDX_CK3_WORLD_CREATE_DEFAULT_OPTIONS) {
                EmptyOptional optional;
                PdxCK3NativeOptionsFactory(&options, optional.bytes);
                value.options_result = reinterpret_cast<uintptr_t>(options);
                value.options_ownership = PDX_CK3_OPTIONS_CALLER;
                value.phase = PDX_CK3_WORLD_OPTIONS_CREATED;
            } else if (request.operation == PDX_CK3_WORLD_DESTROY_OPTIONS) {
                if (!is_options(options)) error = ERROR_INVALID_PARAMETER;
                else {
                    PdxCK3NativeDeleteOptions(options, 1);
                    value.options_result = 0;
                    value.options_ownership = PDX_CK3_OPTIONS_NONE;
                    value.phase = PDX_CK3_WORLD_IDLE;
                }
            } else {
                error = bookmark_phase(application);
                if (!error && request.operation == PDX_CK3_WORLD_ENTER_DEFAULT_BOOKMARK) {
                    EmptyOptional optional;
                    PdxCK3NativeOptionsFactory(&options, optional.bytes);
                    created = true;
                }
                if (!error && !is_options(options)) error = ERROR_INVALID_PARAMETER;
                if (!error) {
                    value.options_result = reinterpret_cast<uintptr_t>(options);
                    value.options_ownership = PDX_CK3_OPTIONS_UNKNOWN;
                    called_entry = true;
                    PdxCK3NativeEnterBookmark(application, options);
                    value.options_ownership = PDX_CK3_OPTIONS_GAME;
                    value.phase = PDX_CK3_WORLD_ENTRY_SUBMITTED;
                    const uintptr_t idler = field<uintptr_t>(app, pending_idler_offset);
                    if (!field<uint8_t>(app, transition_offset) || !idler
                        || field<uintptr_t>(idler, 0) != image_base() + generating_idler_rva
                        || field<uintptr_t>(idler, bookmark_options_offset)
                            != reinterpret_cast<uintptr_t>(options)) error = ERROR_INVALID_STATE;
                } else if (created && is_options(options)) {
                    PdxCK3NativeDeleteOptions(options, 1);
                    value.options_ownership = PDX_CK3_OPTIONS_NONE;
                    value.options_result = 0;
                }
            }
            snapshot(application, value);
        }
    } catch (...) {
        error = ERROR_UNHANDLED_EXCEPTION;
        // Native entry may already have consumed its argument. Never retry/delete.
        if (called_entry) value.options_ownership = PDX_CK3_OPTIONS_UNKNOWN;
    }
    value.error = error;
    if (error) value.phase = PDX_CK3_WORLD_FAILED;
    value.completed_request_id = value.request_id;
    AcquireSRWLockExclusive(&state_lock);
    PdxCK3WorldState = value;
    in_flight = false;
    ReleaseSRWLockExclusive(&state_lock);
}
}

void* WINAPI PdxCK3NativeOptionsFactory(void** output, void* optional_input) {
    return reinterpret_cast<OptionsFactory>(image_base() + options_factory_rva)(output, optional_input);
}
void* WINAPI PdxCK3NativeDeleteOptions(void* options, uint32_t flags) {
    return reinterpret_cast<OptionsDelete>(image_base() + options_delete_rva)(options, flags);
}
uint64_t WINAPI PdxCK3NativeEnterBookmark(void* application, void* options) {
    return reinterpret_cast<BookmarkEntry>(image_base() + bookmark_entry_rva)(application, options);
}
uint64_t WINAPI PdxCK3NativeLoadPayload(void* application, void* owned_file, void* options) {
    return reinterpret_cast<LoadEntry>(image_base() + load_entry_rva)(application, owned_file, options);
}
DWORD WINAPI PdxCK3QueueWorldOperation(void* request) {
    if (!request) return ERROR_INVALID_PARAMETER;
    const auto value = *static_cast<const PdxCK3WorldRequest*>(request);
    if (value.size != sizeof(value) || value.operation > PDX_CK3_WORLD_DESTROY_OPTIONS
        || value.reserved || (value.operation == PDX_CK3_WORLD_ENTER_DEFAULT_BOOKMARK
                             && value.options)) return ERROR_INVALID_PARAMETER;
    AcquireSRWLockExclusive(&state_lock);
    if (in_flight) {
        ReleaseSRWLockExclusive(&state_lock);
        return ERROR_BUSY;
    }
    const auto old = PdxCK3WorldState;
    pending = value;
    in_flight = true;
    PdxCK3WorldState.operation = value.operation;
    PdxCK3WorldState.phase = PDX_CK3_WORLD_QUEUED;
    PdxCK3WorldState.error = 0;
    PdxCK3WorldState.request_id = ++next_request;
    PdxCK3CallbackRequest callback = {sizeof(PdxCK3CallbackRequest), 0,
        reinterpret_cast<uintptr_t>(&world_callback), 0};
    const DWORD error = PdxCK3QueueInterfaceCallback(&callback);
    if (error) {
        PdxCK3WorldState = old;
        in_flight = false;
    }
    ReleaseSRWLockExclusive(&state_lock);
    return error;
}
DWORD WINAPI PdxCK3WorldStatus(PdxCK3WorldStatusInfo* result) {
    if (!result) return ERROR_INVALID_PARAMETER;
    AcquireSRWLockShared(&state_lock);
    *result = PdxCK3WorldState;
    ReleaseSRWLockShared(&state_lock);
    return ERROR_SUCCESS;
}
