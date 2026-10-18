#include "v3_lifecycle.h"
#include <cstddef>
#include <cstring>
#include <string>
#include <vector>

namespace {
constexpr uintptr_t app_root_rva = 0x592d128;
constexpr uintptr_t idler_root_rva = 0x592d0e0;
constexpr uintptr_t session_mutex_rva = 0x5957218;
constexpr uintptr_t manager_save_rva = 0x3398820;
constexpr uintptr_t resolve_path_rva = 0x339a1a0;
constexpr uintptr_t string_create_rva = 0x61c420;
constexpr uintptr_t string_destroy_rva = 0x61f810;
constexpr uintptr_t heap_allocate_rva = 0x417d960;
constexpr uintptr_t heap_free_rva = 0x417d850;
constexpr uintptr_t file_constructor_rva = 0x3b43930;
constexpr uintptr_t vfs_exists_rva = 0x3b30170;
constexpr uintptr_t options_factory_rva = 0x11394c0;
constexpr uintptr_t app_load_rva = 0x662010;
constexpr uintptr_t task_release_rva = 0x626920;
constexpr uintptr_t guard_enter_rva = 0x33bd640;
constexpr uintptr_t guard_leave_rva = 0x33bd750;
constexpr uintptr_t take_mutex_rva = 0x418ef70;
constexpr uintptr_t release_mutex_rva = 0x418ef78;
constexpr uintptr_t pause_rva = 0x33bdc10;
constexpr uintptr_t speed_rva = 0x7e0960;
constexpr uintptr_t request_quit_rva = 0x34b0dd0;
constexpr uintptr_t exit_save_rva = 0x7d6c60;
constexpr uintptr_t exit_save_name_rva = 0x512f988;
constexpr uintptr_t array_initialize_rva = 0x61c180;
constexpr uintptr_t enumerate_local_rva = 0x339a500;
constexpr uintptr_t enumerate_cloud_rva = 0x339a830;
constexpr uintptr_t open_descriptor_rva = 0x3399d10;
constexpr uintptr_t rename_path_rva = 0x3b499d0;
constexpr uintptr_t delete_path_rva = 0x3b49ff0;
constexpr uintptr_t delete_named_rva = 0x3399400;
using GuardEnter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using GuardLeave = void (__fastcall*)(void*);
using MutexTake = int (__fastcall*)(void*);
using MutexRelease = int (__fastcall*)(void*);
using StringCreate = void* (__fastcall*)(void*, const char*, size_t);
using StringDestroy = void (__fastcall*)(void*);
using ResolvePath = void* (__fastcall*)(void*, void*, const void*);
using SaveState = void (__fastcall*)(void*, const void*, uint8_t, bool);
using FileConstruct = void* (__fastcall*)(void*, const void*, uint32_t, uint32_t);
using VfsExists = void* (__fastcall*)(void*);
using Allocate = void* (__fastcall*)(size_t);
using OptionsFactory = void* (__fastcall*)(void**);
using AppLoad = void (__fastcall*)(void*, void*, void*);
using TaskRelease = void (__fastcall*)(void*);
using SetPaused = void (__fastcall*)(void*, bool, uint32_t);
using SetSpeed = void (__fastcall*)(void*, int32_t);
using IdlerMessage = void (__fastcall*)(void*, uint32_t, uint32_t, void*);
using ReadProcess = BOOL (WINAPI*)(HANDLE, LPCVOID, LPVOID, SIZE_T, SIZE_T*);
using DateFormat = void* (__fastcall*)(const void*, void*);
using DateParse = void* (__fastcall*)(void*, const void*);
using ConsoleManager = void* (__fastcall*)();
using ConsoleDispatch = void* (__fastcall*)(void*, void*, const void*);

struct StringView {
    const char* data;
    int32_t length;
    uint8_t null_terminated;
    uint8_t padding[3] = {};
};
struct NativeString { unsigned char bytes[32] = {}; };
struct DateText { unsigned char bytes[14] = {}; };
struct NativeValue40 { alignas(8) unsigned char bytes[40] = {}; };
struct NativeArguments { NativeString* data; uint32_t capacity; uint32_t count; void* allocator; };
struct OwnedFile {
    uint64_t value;
    ~OwnedFile() { if (value) PdxV3DestroyLoadFile(&value); }
};
struct OwnedNativeString {
    NativeString value;
    explicit OwnedNativeString(uintptr_t base) : image(base) {
        *reinterpret_cast<uint64_t*>(value.bytes + 0x18) = 15;
    }
    ~OwnedNativeString() {
        reinterpret_cast<StringDestroy>(image + string_destroy_rva)(&value);
    }
    uintptr_t image;
};
struct OwnedConsoleReply {
    NativeValue40 value;
    explicit OwnedConsoleReply(uintptr_t base) : image(base) {
        *reinterpret_cast<uint64_t*>(value.bytes + 0x20) = 15;
    }
    ~OwnedConsoleReply() {
        reinterpret_cast<StringDestroy>(image + string_destroy_rva)(value.bytes + 8);
    }
    uintptr_t image;
};
struct alignas(16) AccessStorage { unsigned char bytes[80] = {}; };
struct AccessScope {
    AccessStorage& storage;
    uintptr_t base;
    AccessScope(AccessStorage& buffer, uintptr_t image, uint32_t mode, int role)
        : storage(buffer), base(image) {
        reinterpret_cast<GuardEnter>(base + guard_enter_rva)(storage.bytes, mode, role,
            "pdx-runtime/native/v3_lifecycle.cpp");
    }
    ~AccessScope() { reinterpret_cast<GuardLeave>(base + guard_leave_rva)(storage.bytes); }
};
struct MutexScope {
    uintptr_t base;
    void* mutex;
    MutexScope(uintptr_t image, void* object) : base(image), mutex(object) {
        const int error = reinterpret_cast<MutexTake>(base + take_mutex_rva)(mutex);
        if (error) throw error;
    }
    ~MutexScope() { reinterpret_cast<MutexRelease>(base + release_mutex_rva)(mutex); }
};
static_assert(sizeof(StringView) == 16);
static_assert(sizeof(PdxV3LifecycleResult) == 2208);
static_assert(sizeof(NativeArguments) == 24);
static_assert(sizeof(PdxV3SaveDescriptorArray) == 24);
static_assert(sizeof(PdxV3SaveCatalogRequest) == 72);
static_assert(sizeof(PdxV3SaveDescriptorRecord) == 56);
static_assert(sizeof(PdxV3SaveManagementRequest) == 64);
static_assert(sizeof(PdxV3NativeFileResult) == 40);
uintptr_t base_address() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T field(uintptr_t object, uintptr_t offset) {
    return *reinterpret_cast<const T*>(object + offset);
}
uintptr_t current_app(uintptr_t base) { return field<uintptr_t>(base, app_root_rva); }
uintptr_t current_idler(uintptr_t base) { return field<uintptr_t>(base, idler_root_rva); }
StringView view_of(const char* value) {
    return {value, static_cast<int32_t>(std::strlen(value)), 0, {0,0,0}};
}
DWORD copy_native_string(const NativeString& value, char* output, uint32_t capacity) {
    const uintptr_t object = reinterpret_cast<uintptr_t>(&value);
    const uint64_t length = field<uint64_t>(object, 0x10);
    if (length + 1 > capacity) return ERROR_INSUFFICIENT_BUFFER;
    const char* data = field<uint64_t>(object, 0x18) > 15
        ? reinterpret_cast<const char*>(field<uintptr_t>(object, 0))
        : reinterpret_cast<const char*>(&value);
    std::memcpy(output, data, static_cast<size_t>(length));
    output[length] = 0;
    return ERROR_SUCCESS;
}
bool native_file_exists(const char* path, uintptr_t base) {
    StringView view = view_of(path);
    return reinterpret_cast<VfsExists>(base + vfs_exists_rva)(&view) != nullptr;
}
void file_status(const char* path, PdxV3LifecycleResult* result) {
    result->file_present = 0;
    result->file_size = 0;
    result->file_verified = 0;
    const uintptr_t base = base_address();
    if (!native_file_exists(path, base)) return;
    OwnedFile file{PdxV3OpenLoadFile(path)};
    if (!file.value) return;
    const uintptr_t table = field<uintptr_t>(file.value, 0);
    result->file_size = reinterpret_cast<uint64_t (__fastcall*)(void*)>(
        field<uintptr_t>(table, 0x90))(reinterpret_cast<void*>(file.value));
    result->file_present = 1;
}
DWORD observe(void* application, PdxV3LifecycleResult* result, uintptr_t base) {
    uintptr_t root = 0;
    SIZE_T received = 0;
    if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(base + app_root_rva),
                           &root, sizeof(root), &received)) return GetLastError();
    if (!root || root != reinterpret_cast<uintptr_t>(application)) return ERROR_NOT_READY;
    result->application = root;
    result->world = field<uintptr_t>(root, 0x608);
    result->current_state = field<uint32_t>(root, 0x588);
    result->pending_state = field<uint32_t>(root, 0x58c);
    result->transition_pending = field<uint8_t>(root, 0x258);
    result->active_idler = field<uintptr_t>(root, 0x1c0);
    result->active_idler_vtable = result->active_idler ? field<uintptr_t>(result->active_idler, 0) : 0;
    result->flags = (result->flags & (PDX_V3_LIFE_OVERWRITE | PDX_V3_LIFE_NATIVE_EXIT_SAVE))
        | (!result->world && !result->transition_pending
        && result->active_idler_vtable == base + 0x4497830 ? PDX_V3_LIFE_IN_MAIN_MENU : 0u)
        | (field<uint8_t>(root, 0x25d) ? PDX_V3_LIFE_QUIT_REQUESTED : 0u);
    result->save_manager = field<uintptr_t>(root, 0x510);
    result->frame = field<uint64_t>(root, 0x500);
    if (result->world && !result->transition_pending) {
        AccessStorage storage;
        AccessScope scope(storage, base, 1, 0);
        const uintptr_t world = field<uintptr_t>(root, 0x608);
        if (world) {
            result->date_raw = field<uint64_t>(world, 8);
            PdxV3FormatGameDate(result->date_text, sizeof(result->date_text));
            result->speed = field<int32_t>(world, 0x70);
            const uintptr_t idler = current_idler(base);
            if (idler) result->paused = field<uint8_t>(idler, 0x20);
        }
    }
    return ERROR_SUCCESS;
}
}

DWORD WINAPI PdxV3ResolveSavePath(const char* name, char* output, uint32_t capacity) {
    if (!name || !*name || !output || !capacity) return ERROR_INVALID_PARAMETER;
    const uintptr_t base = base_address();
    const uintptr_t app = current_app(base);
    const uintptr_t manager = app ? field<uintptr_t>(app, 0x510) : 0;
    if (!manager) return ERROR_NOT_READY;
    NativeString path;
    const StringView view = view_of(name);
    reinterpret_cast<ResolvePath>(base + resolve_path_rva)(reinterpret_cast<void*>(manager), &path, &view);
    DWORD error = copy_native_string(path, output, capacity);
    reinterpret_cast<StringDestroy>(base + string_destroy_rva)(&path);
    return error;
}
DWORD WINAPI PdxV3BeginSave(const char* name, uint32_t save_type, uint32_t overwrite, uint64_t* owned_task) {
    if (!name || !*name || !owned_task || save_type > 5) return ERROR_INVALID_PARAMETER;
    *owned_task = 0;
    const uintptr_t base = base_address();
    const uintptr_t app = current_app(base);
    if (!app || !field<uintptr_t>(app, 0x608) || field<uint8_t>(app, 0x258)) return ERROR_NOT_READY;
    const uintptr_t manager = field<uintptr_t>(app, 0x510);
    if (!manager) return ERROR_NOT_READY;
    char path[PDX_V3_LIFE_PATH_BYTES] = {};
    DWORD error = PdxV3ResolveSavePath(name, path, sizeof(path));
    if (error) return error;
    AcquireSRWLockExclusive(reinterpret_cast<PSRWLOCK>(base + session_mutex_rva));
    try {
        AccessStorage storage;
        AccessScope scope(storage, base, 2, 0);
        MutexScope manager_lock(base, reinterpret_cast<void*>(manager + 0x320));
        const uintptr_t previous_handle = field<uintptr_t>(manager, 8);
        const uintptr_t previous_task = previous_handle ? field<uintptr_t>(previous_handle, 0x18) : 0;
        if (previous_task) {
            const uint32_t previous_state = field<uint32_t>(previous_task, 0x60);
            if (previous_state != 2 && previous_state != 3) {
                throw static_cast<DWORD>(ERROR_BUSY);
            }
        }
        if (!overwrite && native_file_exists(path, base)) throw static_cast<DWORD>(ERROR_FILE_EXISTS);
        const StringView view = view_of(name);
        // Native console save performs the same Session lock / Logic(2,0) scope.
        reinterpret_cast<SaveState>(base + manager_save_rva)(reinterpret_cast<void*>(manager), &view,
                                                           static_cast<uint8_t>(save_type), false);
        const uintptr_t handle = field<uintptr_t>(manager, 8);
        if (handle) {
            InterlockedIncrement(reinterpret_cast<volatile LONG*>(handle + 8));
            *owned_task = handle;
        }
    } catch (DWORD native_error) {
        ReleaseSRWLockExclusive(reinterpret_cast<PSRWLOCK>(base + session_mutex_rva));
        return native_error;
    } catch (...) {
        ReleaseSRWLockExclusive(reinterpret_cast<PSRWLOCK>(base + session_mutex_rva));
        return ERROR_UNHANDLED_EXCEPTION;
    }
    ReleaseSRWLockExclusive(reinterpret_cast<PSRWLOCK>(base + session_mutex_rva));
    return *owned_task ? ERROR_SUCCESS : ERROR_WRITE_FAULT;
}
DWORD WINAPI PdxV3PollSave(uint64_t owned_task, const char* resolved_path, uint32_t overwrite, PdxV3LifecycleResult* result) {
    if (!owned_task || !resolved_path || !result) return ERROR_INVALID_PARAMETER;
    result->owned_task_handle = owned_task;
    result->task = field<uintptr_t>(owned_task, 0x18);
    result->task_state = result->task ? field<uint32_t>(result->task, 0x60) : 2;
    result->task_completed = result->task_state == 2 || result->task_state == 3;
    file_status(resolved_path, result);
    if (!result->task_completed) return ERROR_IO_PENDING;
    // Native writer writes .tmp, closes, then renames to resolved_path. A terminal
    // task is not alone write success; require the final nonempty file as well.
    result->file_verified = result->task_state == 2 && result->file_present && result->file_size != 0
        && !overwrite;
    // An existing overwrite destination alone cannot prove replacement. Keep
    // completion/file status public but require independent content/load evidence.
    return result->task_state == 2 && result->file_present && result->file_size != 0
        ? ERROR_SUCCESS : ERROR_WRITE_FAULT;
}
DWORD WINAPI PdxV3ReleaseSaveTask(uint64_t* owned_task) {
    if (!owned_task || !*owned_task) return ERROR_INVALID_PARAMETER;
    const uintptr_t base = base_address();
    uintptr_t handle = *owned_task;
    *owned_task = 0;
    reinterpret_cast<TaskRelease>(base + task_release_rva)(&handle);
    return ERROR_SUCCESS;
}
uint64_t WINAPI PdxV3OpenLoadFile(const char* native_path) {
    if (!native_path || !*native_path) return 0;
    const uintptr_t base = base_address();
    if (!native_file_exists(native_path, base)) return 0;
    void* allocation = reinterpret_cast<Allocate>(base + heap_allocate_rva)(104);
    if (!allocation) return 0;
    const StringView view = view_of(native_path);
    try {
        return reinterpret_cast<uintptr_t>(reinterpret_cast<FileConstruct>(base + file_constructor_rva)(
            allocation, &view, 1, 0));
    } catch (...) {
        reinterpret_cast<void (__fastcall*)(void*)>(base + heap_free_rva)(allocation);
        return 0;
    }
}
uint64_t WINAPI PdxV3CreateLoadOptions(void*) {
    void* options = nullptr;
    reinterpret_cast<OptionsFactory>(base_address() + options_factory_rva)(&options);
    // The complete native console load caller 0x11340ab..0x11340b0 constructs
    // this same owned object and sets its load flag before handing it to the idler.
    if (options) *reinterpret_cast<uint8_t*>(reinterpret_cast<uintptr_t>(options) + 0x90) = 1;
    return reinterpret_cast<uintptr_t>(options);
}
DWORD WINAPI PdxV3DestroyLoadFile(uint64_t* owned_file) {
    if (!owned_file || !*owned_file) return ERROR_INVALID_PARAMETER;
    void* file = reinterpret_cast<void*>(*owned_file);
    *owned_file = 0;
    auto table = *reinterpret_cast<uintptr_t**>(file);
    reinterpret_cast<void* (__fastcall*)(void*, uint32_t)>(table[0])(file, 1);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DestroyLoadOptions(uint64_t* owned_options) {
    if (!owned_options || !*owned_options) return ERROR_INVALID_PARAMETER;
    void* options = reinterpret_cast<void*>(*owned_options);
    *owned_options = 0;
    auto table = *reinterpret_cast<uintptr_t**>(options);
    reinterpret_cast<void* (__fastcall*)(void*, uint32_t)>(table[1])(options, 1);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3LoadOwned(uint64_t* owned_file, uint64_t* owned_options) {
    if (!owned_file || !owned_options || !*owned_file || !*owned_options) return ERROR_INVALID_PARAMETER;
    const uintptr_t base = base_address();
    const uintptr_t app = current_app(base);
    const uintptr_t idler = app ? field<uintptr_t>(app, 0x1c0) : 0;
    if (!app || field<uint8_t>(app, 0x258) || field<uintptr_t>(app, 0x608)
        || !idler || field<uintptr_t>(idler, 0) != base + 0x4497830) return ERROR_BUSY;
    void* file = reinterpret_cast<void*>(*owned_file);
    void* options = reinterpret_cast<void*>(*owned_options);
    *owned_file = 0;
    *owned_options = 0;
    // Natural ctor consumes CFile after reading into its own buffer and retains
    // options through load. On a native exception ownership is unknown; no replay.
    try {
        reinterpret_cast<AppLoad>(base + app_load_rva)(reinterpret_cast<void*>(app), file, options);
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ExitToMainMenu(void*) {
    const uintptr_t base = base_address();
    const uintptr_t app = current_app(base);
    uintptr_t idler = current_idler(base);
    if (!app || !idler || !field<uintptr_t>(app, 0x608) || field<uint8_t>(app, 0x258))
        return ERROR_NOT_READY;
    // Native quit-to-menu message0x6db. Argument0 follows the discard/non-ironman
    // branch, not the explicit save-to-menu branch. The native frame owns teardown.
    reinterpret_cast<IdlerMessage>(field<uintptr_t>(field<uintptr_t>(idler, 0), 0x98))(
        reinterpret_cast<void*>(idler), 0x6db, 0, nullptr);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3SetGameSpeed(int32_t speed) {
    if (speed < 0 || speed > 4) return ERROR_INVALID_PARAMETER;
    const uintptr_t base = base_address();
    const uintptr_t app = current_app(base);
    uintptr_t world = app ? field<uintptr_t>(app, 0x608) : 0;
    if (!world || field<uint8_t>(app, 0x258)) return ERROR_NOT_READY;
    reinterpret_cast<SetSpeed>(base + speed_rva)(reinterpret_cast<void*>(world), speed);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3SetGamePaused(uint32_t paused) {
    const uintptr_t base = base_address();
    const uintptr_t app = current_app(base);
    uintptr_t idler = current_idler(base);
    if (!app || !idler || !field<uintptr_t>(app, 0x608) || field<uint8_t>(app, 0x258)) return ERROR_NOT_READY;
    reinterpret_cast<SetPaused>(base + pause_rva)(reinterpret_cast<void*>(idler), paused != 0,
                                               field<uint32_t>(base, 0x595735c));
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3RequestApplicationQuit(void* application) {
    if (!application) return ERROR_INVALID_PARAMETER;
    reinterpret_cast<void (__fastcall*)(void*)>(base_address() + request_quit_rva)(application);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3RequestApplicationExit(uint32_t native_save) {
    const uintptr_t base = base_address();
    const uintptr_t app = current_app(base);
    const uintptr_t idler = app ? field<uintptr_t>(app, 0x1c0) : 0;
    if (!idler) return ERROR_NOT_READY;
    reinterpret_cast<IdlerMessage>(field<uintptr_t>(field<uintptr_t>(idler, 0), 0x98))(
        reinterpret_cast<void*>(idler), 0x6e9, native_save, nullptr);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3SaveForApplicationExit(void*) {
    const uintptr_t base = base_address();
    const uintptr_t app = current_app(base);
    if (!app || !field<uintptr_t>(app, 0x608)) return ERROR_NOT_READY;
    // The native helper owns Session lock -> Logic(2,1) -> original save -> restore.
    reinterpret_cast<void (__fastcall*)()>(base + exit_save_rva)();
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ResolveNativeExitSavePath(char* output, uint32_t capacity) {
    if (!output || !capacity) return ERROR_INVALID_PARAMETER;
    const uintptr_t base = base_address();
    const uintptr_t app = current_app(base);
    if (!app) return ERROR_NOT_READY;
    if (field<uintptr_t>(app, 0x608)) {
        const auto ironman = reinterpret_cast<void* (__fastcall*)()>(base + 0x3029580)();
        if (!field<uint8_t>(base, 0x58fd50c) && field<uint8_t>(reinterpret_cast<uintptr_t>(ironman), 0x112))
            return ERROR_NOT_SUPPORTED;
    }
    char name[PDX_V3_LIFE_PATH_BYTES] = {};
    const DWORD error = copy_native_string(
        *reinterpret_cast<const NativeString*>(base + exit_save_name_rva), name, sizeof(name));
    if (error) return error;
    if (!name[0]) return ERROR_NOT_READY;
    return PdxV3ResolveSavePath(name, output, capacity);
}
DWORD WINAPI PdxV3FormatGameDate(char* output, uint32_t capacity) {
    if (!output || !capacity) return ERROR_INVALID_PARAMETER;
    const uintptr_t base = base_address();
    const uintptr_t app = current_app(base);
    const uintptr_t world = app ? field<uintptr_t>(app, 0x608) : 0;
    if (!world) return ERROR_NOT_READY;
    DateText text;
    reinterpret_cast<DateFormat>(base + 0x33b0b30)(reinterpret_cast<void*>(world + 8), &text);
    const uint8_t length = text.bytes[13];
    if (length + 1u > capacity) return ERROR_INSUFFICIENT_BUFFER;
    std::memcpy(output, text.bytes, length);
    output[length] = 0;
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3SetGameDate(const char* natural_date) {
    if (!natural_date || !*natural_date) return ERROR_INVALID_PARAMETER;
    const uintptr_t base = base_address();
    const uintptr_t app = current_app(base);
    if (!app || !field<uintptr_t>(app, 0x608) || field<uint8_t>(app, 0x258)) return ERROR_NOT_READY;
    // Preflight the engine parser: the official date callback does not check its
    // parse-result tag before using the payload. Never execute an invalid value.
    NativeValue40 parsed;
    const StringView view = view_of(natural_date);
    reinterpret_cast<DateParse>(base + 0x33b13e0)(&parsed, &view);
    if (parsed.bytes[32] != 0) {
        if (parsed.bytes[32] == 1)
            reinterpret_cast<StringDestroy>(base + string_destroy_rva)(&parsed);
        return ERROR_INVALID_PARAMETER;
    }
    OwnedNativeString name(base);
    OwnedNativeString argument(base);
    OwnedConsoleReply reply(base);
    DWORD error = ERROR_SUCCESS;
    try {
        reinterpret_cast<StringCreate>(base + string_create_rva)(&name.value, "date", 4);
        reinterpret_cast<StringCreate>(base + string_create_rva)(&argument.value, natural_date, std::strlen(natural_date));
        NativeString strings[2] = {name.value, argument.value};
        // These are borrowed CString images: their native allocations remain
        // owned by name/argument, and the original dispatcher only reads them.
        NativeArguments arguments = {strings, 2, 2, nullptr};
        void* manager = reinterpret_cast<ConsoleManager>(base + 0x3af13c0)();
        reinterpret_cast<ConsoleDispatch>(base + 0x3af1740)(manager, &reply.value, &arguments);
        if (!reply.value.bytes[0]) error = ERROR_ACCESS_DENIED;
    } catch (...) { error = ERROR_UNHANDLED_EXCEPTION; }
    return error;
}
void WINAPI PdxV3LifecycleInterface(void* application, void* opaque) {
    auto* result = static_cast<PdxV3LifecycleResult*>(opaque);
    if (!result || result->size != sizeof(PdxV3LifecycleResult)) return;
    const uint32_t action = result->action;
    const int32_t requested_speed = result->speed;
    const uint32_t requested_paused = result->paused;
    const uint32_t requested_overwrite = (result->flags & PDX_V3_LIFE_OVERWRITE) != 0;
    const uint32_t requested_exit_save = (result->flags & PDX_V3_LIFE_NATIVE_EXIT_SAVE) != 0;
    const uintptr_t base = base_address();
    result->error = ERROR_SUCCESS;
    result->execution_thread_id = GetCurrentThreadId();
    try {
        DWORD error = observe(application, result, base);
        if (error) { result->error = error; return; }
        if (action == PDX_V3_LIFE_OBSERVE) return;
        if (action == PDX_V3_LIFE_RESOLVE_SAVE_PATH) {
            result->error = PdxV3ResolveSavePath(result->path, result->resolved_path, sizeof(result->resolved_path));
        } else if (action == PDX_V3_LIFE_SAVE) {
            result->error = PdxV3ResolveSavePath(result->path, result->resolved_path, sizeof(result->resolved_path));
            if (!result->error) result->error = PdxV3BeginSave(result->path, result->save_type, requested_overwrite, &result->owned_task_handle);
            if (!result->error) {
                const DWORD save_status = PdxV3PollSave(result->owned_task_handle, result->resolved_path, requested_overwrite, result);
                result->native_result = save_status;
                result->error = save_status == ERROR_IO_PENDING ? ERROR_SUCCESS : save_status;
            }
        } else if (action == PDX_V3_LIFE_POLL_SAVE) {
            const DWORD save_status = PdxV3PollSave(result->owned_task_handle, result->resolved_path, requested_overwrite, result);
            result->native_result = save_status;
            result->error = save_status == ERROR_IO_PENDING ? ERROR_SUCCESS : save_status;
        } else if (action == PDX_V3_LIFE_RELEASE_SAVE) {
            result->error = PdxV3ReleaseSaveTask(&result->owned_task_handle);
        } else if (action == PDX_V3_LIFE_LOAD) {
            if (!(result->flags & PDX_V3_LIFE_IN_MAIN_MENU)) { result->error = ERROR_BUSY; return; }
            uint64_t file = PdxV3OpenLoadFile(result->path);
            if (!file) { result->error = ERROR_FILE_NOT_FOUND; return; }
            uint64_t options = 0;
            try {
                options = PdxV3CreateLoadOptions(nullptr);
                result->error = options ? PdxV3LoadOwned(&file, &options) : ERROR_NOT_ENOUGH_MEMORY;
            } catch (...) {
                if (file) PdxV3DestroyLoadFile(&file);
                if (options) PdxV3DestroyLoadOptions(&options);
                throw;
            }
            if (file) PdxV3DestroyLoadFile(&file);
            if (options) PdxV3DestroyLoadOptions(&options);
        } else if (action == PDX_V3_LIFE_EXIT_TO_MENU) {
            const auto ironman = reinterpret_cast<void* (__fastcall*)()>(base + 0x3029580)();
            if (!field<uint8_t>(base, 0x58fd50c) && field<uint8_t>(reinterpret_cast<uintptr_t>(ironman), 0x112)) {
                result->error = ERROR_NOT_SUPPORTED;
                return;
            }
            result->error = PdxV3ExitToMainMenu(nullptr);
        } else if (action == PDX_V3_LIFE_RESOLVE_EXIT_SAVE_PATH) {
            result->error = PdxV3ResolveNativeExitSavePath(result->resolved_path, sizeof(result->resolved_path));
        } else if (action == PDX_V3_LIFE_EXIT_APPLICATION) {
            if (result->transition_pending || (result->flags & PDX_V3_LIFE_QUIT_REQUESTED)) {
                result->error = ERROR_BUSY; return;
            }
            if (result->active_idler_vtable != base + 0x4497830
                && result->active_idler_vtable != base + 0x4431500) {
                result->error = ERROR_NOT_SUPPORTED; return;
            }
            if (result->save_manager) {
                // Release this mutex before the natural save branch takes Session.
                MutexScope manager_lock(base, reinterpret_cast<void*>(result->save_manager + 0x320));
                const uintptr_t handle = field<uintptr_t>(result->save_manager, 8);
                const uintptr_t task = handle ? field<uintptr_t>(handle, 0x18) : 0;
                const uint32_t state = task ? field<uint32_t>(task, 0x60) : 2;
                if (state != 2 && state != 3) { result->error = ERROR_BUSY; return; }
            }
            if (requested_exit_save && !result->world) { result->error = ERROR_NOT_READY; return; }
            if (!requested_exit_save && result->world) {
                const auto ironman = reinterpret_cast<void* (__fastcall*)()>(base + 0x3029580)();
                if (!field<uint8_t>(base, 0x58fd50c) && field<uint8_t>(reinterpret_cast<uintptr_t>(ironman), 0x112)) {
                    result->error = ERROR_NOT_SUPPORTED; return;
                }
            }
            result->error = PdxV3RequestApplicationExit(requested_exit_save);
        } else if (action == PDX_V3_LIFE_SET_DATE) {
            result->error = PdxV3SetGameDate(result->path);
        } else if (action == PDX_V3_LIFE_SET_SPEED || action == PDX_V3_LIFE_SET_PAUSED) {
            AccessStorage storage;
            AccessScope scope(storage, base, 1, 0);
            result->error = action == PDX_V3_LIFE_SET_SPEED ? PdxV3SetGameSpeed(requested_speed)
                                                         : PdxV3SetGamePaused(requested_paused);
        } else result->error = ERROR_INVALID_PARAMETER;
        observe(application, result, base);
    } catch (...) { result->error = ERROR_UNHANDLED_EXCEPTION; }
}

namespace {
using EnumerateSaves = void (__fastcall*)(void*, PdxV3SaveDescriptorArray*, const void*, const void*);
using OpenDescriptor = void* (__fastcall*)(void*, uint64_t*, const NativeString*, uint32_t);
struct OwnedDescriptorArray {
    PdxV3SaveDescriptorArray value = {};
    OwnedDescriptorArray() { PdxV3InitializeSaveDescriptorArray(&value); }
    ~OwnedDescriptorArray() { PdxV3DestroySaveDescriptorArray(&value); }
};
struct OwnedDescriptor {
    uint64_t value = 0;
    ~OwnedDescriptor() { if (value) PdxV3DestroySaveDescriptor(&value); }
};
void append_native_string(std::vector<unsigned char>& output, uintptr_t object) {
    const uint64_t native_length = field<uint64_t>(object, 0x10);
    const uint32_t length = static_cast<uint32_t>(native_length);
    const auto* prefix = reinterpret_cast<const unsigned char*>(&length);
    output.insert(output.end(), prefix, prefix + sizeof(length));
    const auto* data = field<uint64_t>(object, 0x18) > 15
        ? reinterpret_cast<const unsigned char*>(field<uintptr_t>(object, 0))
        : reinterpret_cast<const unsigned char*>(object);
    output.insert(output.end(), data, data + length);
}
std::vector<unsigned char> descriptor_record(uint64_t descriptor) {
    PdxV3SaveDescriptorRecord record = {};
    record.cloud = field<uint8_t>(descriptor, 0x1c0);
    record.is_valid = field<uint8_t>(descriptor, 0x2c);
    record.is_ironman_save = field<uint8_t>(descriptor, 0x98);
    record.number_of_players = field<int32_t>(descriptor, 0x1c8);
    record.modified_time_raw = field<int64_t>(descriptor, 0x1b8);
    record.last_played_date_raw = field<uint64_t>(descriptor, 0x218);
    record.country_flag_id = field<uint32_t>(descriptor, 0x270);
    record.game_date_raw = field<uint64_t>(descriptor, 0x210);
    std::vector<unsigned char> output(sizeof(record));
    append_native_string(output, descriptor + 0x198);
    append_native_string(output, descriptor + 0x158);
    append_native_string(output, descriptor + 0x178);
    append_native_string(output, descriptor + 8);
    char date[16] = {};
    PdxV3FormatSaveLastPlayedDate(descriptor, date, sizeof(date));
    const uint32_t date_length = static_cast<uint32_t>(std::strlen(date));
    const auto* date_prefix = reinterpret_cast<const unsigned char*>(&date_length);
    output.insert(output.end(), date_prefix, date_prefix + sizeof(date_length));
    output.insert(output.end(), date, date + date_length);
    const uintptr_t base = base_address();
    OwnedNativeString rank(base);
    reinterpret_cast<void* (__fastcall*)(void*, const void*)>(base + 0xcf0160)(
        &rank.value, reinterpret_cast<const void*>(field<uintptr_t>(descriptor, 0x268)));
    append_native_string(output, reinterpret_cast<uintptr_t>(&rank.value));
    char game_date[16] = {};
    PdxV3FormatSaveGameDate(descriptor, game_date, sizeof(game_date));
    const uint32_t game_date_length = static_cast<uint32_t>(std::strlen(game_date));
    const auto* game_date_prefix = reinterpret_cast<const unsigned char*>(&game_date_length);
    output.insert(output.end(), game_date_prefix, game_date_prefix + sizeof(game_date_length));
    output.insert(output.end(), game_date, game_date + game_date_length);
    record.size = static_cast<uint32_t>(output.size());
    std::memcpy(output.data(), &record, sizeof(record));
    return output;
}
DWORD catalog_manager(void* application, PdxV3SaveCatalogRequest* request, uintptr_t base) {
    uintptr_t root = 0;
    SIZE_T received = 0;
    if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(base + app_root_rva),
                           &root, sizeof(root), &received)) return GetLastError();
    if (!root || root != reinterpret_cast<uintptr_t>(application)) return ERROR_NOT_READY;
    request->application = root;
    request->save_manager = field<uintptr_t>(root, 0x510);
    if (!request->save_manager) return ERROR_NOT_READY;
    if (field<uint8_t>(root, 0x258) || field<uint8_t>(root, 0x25d)) return ERROR_BUSY;
    MutexScope manager_lock(base, reinterpret_cast<void*>(request->save_manager + 0x320));
    const uintptr_t handle = field<uintptr_t>(request->save_manager, 8);
    const uintptr_t task = handle ? field<uintptr_t>(handle, 0x18) : 0;
    const uint32_t state = task ? field<uint32_t>(task, 0x60) : 2;
    return state == 2 || state == 3 ? ERROR_SUCCESS : ERROR_BUSY;
}
}
DWORD WINAPI PdxV3InitializeSaveDescriptorArray(PdxV3SaveDescriptorArray* output) {
    if (!output) return ERROR_INVALID_PARAMETER;
    reinterpret_cast<void* (__fastcall*)(void*)>(base_address() + array_initialize_rva)(output);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3EnumerateLocalSaves(void* manager, PdxV3SaveDescriptorArray* output,
    const char* native_directory, const char* name_filter) {
    if (!manager || !output || !native_directory || !name_filter) return ERROR_INVALID_PARAMETER;
    const StringView directory = view_of(native_directory);
    const StringView filter = view_of(name_filter);
    reinterpret_cast<EnumerateSaves>(base_address() + enumerate_local_rva)(manager, output, &directory, &filter);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3EnumerateCloudSaves(void* manager, PdxV3SaveDescriptorArray* output,
    const char* native_directory, const char* name_filter) {
    if (!manager || !output || !native_directory || !name_filter) return ERROR_INVALID_PARAMETER;
    const StringView directory = view_of(native_directory);
    const StringView filter = view_of(name_filter);
    reinterpret_cast<EnumerateSaves>(base_address() + enumerate_cloud_rva)(manager, output, &directory, &filter);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DestroySaveDescriptor(uint64_t* owned_descriptor) {
    if (!owned_descriptor || !*owned_descriptor) return ERROR_INVALID_PARAMETER;
    void* descriptor = reinterpret_cast<void*>(*owned_descriptor);
    *owned_descriptor = 0;
    const auto table = *reinterpret_cast<uintptr_t**>(descriptor);
    reinterpret_cast<void* (__fastcall*)(void*, uint32_t)>(table[0])(descriptor, 1);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DestroySaveDescriptorArray(PdxV3SaveDescriptorArray* owned_array) {
    if (!owned_array) return ERROR_INVALID_PARAMETER;
    auto* descriptors = reinterpret_cast<uint64_t*>(owned_array->data);
    for (uint32_t i = 0; i < owned_array->count; ++i) {
        if (descriptors[i]) PdxV3DestroySaveDescriptor(&descriptors[i]);
    }
    if (owned_array->data) {
        void* allocator = reinterpret_cast<void*>(owned_array->allocator);
        const auto table = *reinterpret_cast<uintptr_t**>(allocator);
        reinterpret_cast<void (__fastcall*)(void*, void*)>(table[2])(
            allocator, reinterpret_cast<void*>(owned_array->data));
    }
    *owned_array = {};
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3OpenSaveDescriptor(void* manager, const char* native_path,
    uint32_t cloud, uint64_t* owned_descriptor) {
    if (!manager || !native_path || !*native_path || !owned_descriptor || cloud > 1)
        return ERROR_INVALID_PARAMETER;
    *owned_descriptor = 0;
    const uintptr_t base = base_address();
    if (!cloud && !native_file_exists(native_path, base)) return ERROR_FILE_NOT_FOUND;
    OwnedNativeString path(base);
    reinterpret_cast<StringCreate>(base + string_create_rva)(&path.value, native_path, std::strlen(native_path));
    reinterpret_cast<OpenDescriptor>(base + open_descriptor_rva)(manager, owned_descriptor, &path.value, cloud);
    return *owned_descriptor ? ERROR_SUCCESS : ERROR_FILE_NOT_FOUND;
}
DWORD WINAPI PdxV3CopySaveDescriptor(uint64_t descriptor, void* output,
    uint32_t capacity, uint32_t* required) {
    if (!descriptor || !required || (!output && capacity)) return ERROR_INVALID_PARAMETER;
    const auto record = descriptor_record(descriptor);
    *required = static_cast<uint32_t>(record.size());
    if (capacity < record.size()) return ERROR_MORE_DATA;
    std::memcpy(output, record.data(), record.size());
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3FormatSaveLastPlayedDate(uint64_t descriptor, char* output, uint32_t capacity) {
    if (!descriptor || !output || !capacity) return ERROR_INVALID_PARAMETER;
    DateText text;
    reinterpret_cast<DateFormat>(base_address() + 0x33b0b30)(
        reinterpret_cast<const void*>(descriptor + 0x218), &text);
    const uint8_t length = text.bytes[13];
    if (length + 1u > capacity) return ERROR_INSUFFICIENT_BUFFER;
    std::memcpy(output, text.bytes, length);
    output[length] = 0;
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3FormatSaveGameDate(uint64_t descriptor, char* output,
    uint32_t capacity) noexcept(false) {
    if (!descriptor || !output || !capacity) return ERROR_INVALID_PARAMETER;
    DateText text;
    // Exact serialized game_date key0x3245 maps to this native Date8 at+210.
    // real_date/last-played at+218 remains a separate original field.
    reinterpret_cast<DateFormat>(base_address() + 0x33b0b30)(
        reinterpret_cast<const void*>(descriptor + 0x210), &text);
    const uint8_t length = text.bytes[13];
    if (length + 1u > capacity) return ERROR_INSUFFICIENT_BUFFER;
    std::memcpy(output, text.bytes, length);
    output[length] = 0;
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3FormatSaveCountryRank(uint64_t descriptor, char* output,
    uint32_t capacity, uint32_t* required) {
    if (!descriptor || !required || (!output && capacity)) return ERROR_INVALID_PARAMETER;
    const uintptr_t base = base_address();
    OwnedNativeString rank(base);
    reinterpret_cast<void* (__fastcall*)(void*, const void*)>(base + 0xcf0160)(
        &rank.value, reinterpret_cast<const void*>(field<uintptr_t>(descriptor, 0x268)));
    *required = static_cast<uint32_t>(field<uint64_t>(reinterpret_cast<uintptr_t>(&rank.value), 0x10) + 1);
    return copy_native_string(rank.value, output, capacity);
}
DWORD WINAPI PdxV3GetSaveCountryFlagId(uint64_t descriptor, uint32_t* output) {
    if (!descriptor || !output) return ERROR_INVALID_PARAMETER;
    *output = field<uint32_t>(descriptor, 0x270);
    return ERROR_SUCCESS;
}
void WINAPI PdxV3SaveCatalogInterface(void* application, void* opaque) {
    auto* request = static_cast<PdxV3SaveCatalogRequest*>(opaque);
    if (!request || request->size < sizeof(*request)) return;
    request->error = ERROR_INVALID_PARAMETER;
    if (uint64_t(sizeof(*request)) + request->directory_bytes + request->filter_bytes
        + request->output_capacity != request->size) return;
    if (!request->sources || (request->sources & ~(PDX_V3_SAVE_LOCAL | PDX_V3_SAVE_CLOUD))) return;
    if (request->action > PDX_V3_SAVE_DESCRIBE_PATH) return;
    const auto* input = reinterpret_cast<const char*>(request + 1);
    if (std::memchr(input, 0, request->directory_bytes + request->filter_bytes)) return;
    request->output_size = 0;
    request->output_required = 0;
    request->count = 0;
    request->total_count = 0;
    request->execution_thread_id = GetCurrentThreadId();
    const uintptr_t base = base_address();
    try {
        request->error = catalog_manager(application, request, base);
        if (request->error) return;
        std::string directory(input, request->directory_bytes);
        const std::string filter(input + request->directory_bytes, request->filter_bytes);
        if (request->action == PDX_V3_SAVE_LIST && directory.empty()) {
            char buffer[PDX_V3_LIFE_PATH_BYTES] = {};
            request->error = copy_native_string(*reinterpret_cast<const NativeString*>(
                request->save_manager + 0x50), buffer, sizeof(buffer));
            if (request->error) return;
            directory = buffer;
        }
        std::vector<unsigned char> output;
        auto append = [&](uint64_t descriptor) {
            const auto record = descriptor_record(descriptor);
            ++request->total_count;
            output.insert(output.end(), record.begin(), record.end());
        };
        if (request->action == PDX_V3_SAVE_LIST) {
            OwnedDescriptorArray descriptors;
            void* manager = reinterpret_cast<void*>(request->save_manager);
            if (request->sources & PDX_V3_SAVE_LOCAL)
                PdxV3EnumerateLocalSaves(manager, &descriptors.value, directory.c_str(), filter.c_str());
            if (request->sources & PDX_V3_SAVE_CLOUD)
                PdxV3EnumerateCloudSaves(manager, &descriptors.value, directory.c_str(), filter.c_str());
            const auto* items = reinterpret_cast<const uint64_t*>(descriptors.value.data);
            for (uint32_t i = 0; i < descriptors.value.count; ++i) append(items[i]);
        } else {
            if (directory.empty() || request->sources == (PDX_V3_SAVE_LOCAL | PDX_V3_SAVE_CLOUD)) {
                request->error = ERROR_INVALID_PARAMETER; return;
            }
            OwnedDescriptor descriptor;
            request->error = PdxV3OpenSaveDescriptor(reinterpret_cast<void*>(request->save_manager),
                directory.c_str(), request->sources == PDX_V3_SAVE_CLOUD, &descriptor.value);
            if (request->error) return;
            append(descriptor.value);
        }
        request->output_required = static_cast<uint32_t>(output.size());
        // Publish complete records only, never a partial native string/record.
        uint32_t cursor = 0;
        while (cursor < output.size()) {
            PdxV3SaveDescriptorRecord record;
            std::memcpy(&record, output.data() + cursor, sizeof(record));
            if (uint64_t(cursor) + record.size > request->output_capacity) break;
            cursor += record.size;
            ++request->count;
        }
        request->output_size = cursor;
        if (cursor) std::memcpy(reinterpret_cast<unsigned char*>(request + 1)
            + request->directory_bytes + request->filter_bytes, output.data(), cursor);
        request->error = cursor < output.size() ? ERROR_MORE_DATA : ERROR_SUCCESS;
    } catch (...) { request->error = ERROR_UNHANDLED_EXCEPTION; }
}

namespace {
using RenamePath = void* (__fastcall*)(PdxV3NativeFileResult*, const void*, const void*) noexcept(false);
using DeletePath = void* (__fastcall*)(PdxV3NativeFileResult*, const void*) noexcept(false);
using DeleteNamed = bool (__fastcall*)(void*, const void*, bool) noexcept(false);
struct OwnedNativeFileResult {
    PdxV3NativeFileResult value = {};
    ~OwnedNativeFileResult() { PdxV3DestroyNativeFileResult(&value); }
};
void copy_management_message(PdxV3SaveManagementRequest* request,
    const PdxV3NativeFileResult& result) {
    const uintptr_t object = reinterpret_cast<uintptr_t>(&result);
    const uint32_t length = static_cast<uint32_t>(field<uint64_t>(object, 0x10));
    request->message_required = length;
    request->message_size = length < request->message_capacity ? length : request->message_capacity;
    const void* data = field<uint64_t>(object, 0x18) > 15
        ? reinterpret_cast<const void*>(field<uintptr_t>(object, 0)) : &result;
    if (request->message_size) std::memcpy(reinterpret_cast<unsigned char*>(request + 1)
        + request->source_bytes + request->destination_bytes, data, request->message_size);
    if (request->message_size < length) request->error = ERROR_MORE_DATA;
}
}
DWORD WINAPI PdxV3RenameNativeSavePath(const char* source_native_path,
    const char* replacement_view, PdxV3NativeFileResult* output) noexcept(false) {
    if (!source_native_path || !*source_native_path || !replacement_view || !*replacement_view || !output)
        return ERROR_INVALID_PARAMETER;
    const StringView source = view_of(source_native_path);
    const StringView replacement = view_of(replacement_view);
    reinterpret_cast<RenamePath>(base_address() + rename_path_rva)(output, &source, &replacement);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DeleteNativeSavePath(const char* native_path, PdxV3NativeFileResult* output) noexcept(false) {
    if (!native_path || !*native_path || !output) return ERROR_INVALID_PARAMETER;
    const StringView path = view_of(native_path);
    reinterpret_cast<DeletePath>(base_address() + delete_path_rva)(output, &path);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DestroyNativeFileResult(PdxV3NativeFileResult* owned_result) noexcept(false) {
    if (!owned_result) return ERROR_INVALID_PARAMETER;
    auto* bytes = reinterpret_cast<unsigned char*>(owned_result);
    if (bytes[32] == 1) reinterpret_cast<StringDestroy>(base_address() + string_destroy_rva)(owned_result);
    *owned_result = {};
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DeleteNamedSave(void* manager, const char* native_name,
    uint32_t cloud, uint32_t* native_success) noexcept(false) {
    if (!manager || !native_name || !*native_name || cloud > 1 || !native_success)
        return ERROR_INVALID_PARAMETER;
    const StringView name = view_of(native_name);
    *native_success = reinterpret_cast<DeleteNamed>(base_address() + delete_named_rva)(manager, &name, cloud != 0);
    return ERROR_SUCCESS;
}
void WINAPI PdxV3SaveManagementInterface(void* application, void* opaque) noexcept(false) {
    auto* request = static_cast<PdxV3SaveManagementRequest*>(opaque);
    if (!request || request->size < sizeof(*request)) return;
    request->error = ERROR_INVALID_PARAMETER;
    if (uint64_t(sizeof(*request)) + request->source_bytes + request->destination_bytes
        + request->message_capacity != request->size || request->action > PDX_V3_SAVE_DELETE_NAMED
        || request->cloud > 1) return;
    const auto* input = reinterpret_cast<const char*>(request + 1);
    if (!request->source_bytes || std::memchr(input, 0, request->source_bytes + request->destination_bytes)) return;
    request->message_size = 0;
    request->message_required = 0;
    request->native_success = 0;
    request->native_result_tag = UINT32_MAX;
    request->execution_thread_id = GetCurrentThreadId();
    try {
        PdxV3SaveCatalogRequest readiness = {};
        request->error = catalog_manager(application, &readiness, base_address());
        request->application = readiness.application;
        request->save_manager = readiness.save_manager;
        if (request->error) return;
        const std::string source(input, request->source_bytes);
        const std::string destination(input + request->source_bytes, request->destination_bytes);
        if (request->action == PDX_V3_SAVE_DELETE_NAMED) {
            if (!destination.empty()) { request->error = ERROR_INVALID_PARAMETER; return; }
            if (request->cloud) {
                const uintptr_t platform = field<uintptr_t>(request->application, 0x1b8);
                const uintptr_t provider = platform ? field<uintptr_t>(platform, 0x68) : 0;
                // The original manager cloud branch dereferences this provider.
                // Existence is readiness only, not proof of cloud availability.
                if (!provider) { request->error = ERROR_NOT_READY; return; }
            }
            request->error = PdxV3DeleteNamedSave(reinterpret_cast<void*>(request->save_manager),
                source.c_str(), request->cloud, &request->native_success);
            return;
        }
        if (request->cloud) { request->error = ERROR_NOT_SUPPORTED; return; }
        OwnedNativeFileResult result;
        if (request->action == PDX_V3_SAVE_RENAME_NATIVE) {
            if (destination.empty()) { request->error = ERROR_INVALID_PARAMETER; return; }
            request->error = PdxV3RenameNativeSavePath(source.c_str(), destination.c_str(), &result.value);
        } else {
            if (!destination.empty()) { request->error = ERROR_INVALID_PARAMETER; return; }
            request->error = PdxV3DeleteNativeSavePath(source.c_str(), &result.value);
        }
        if (request->error) return;
        request->native_result_tag = reinterpret_cast<const unsigned char*>(&result.value)[32];
        request->native_success = request->native_result_tag == 0;
        if (request->native_result_tag == 1) copy_management_message(request, result.value);
    } catch (...) { request->error = ERROR_UNHANDLED_EXCEPTION; }
}
