#pragma once
#include <Windows.h>
#include <stdint.h>
#ifdef PDX_V3_LIFECYCLE_BUILD
#define PDX_V3_LIFE_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_LIFE_API extern "C" __declspec(dllimport)
#endif
#define PDX_V3_LIFE_PATH_BYTES 1024u

enum PdxV3LifecycleAction : uint32_t {
    PDX_V3_LIFE_OBSERVE = 0,
    PDX_V3_LIFE_SAVE = 1,
    PDX_V3_LIFE_POLL_SAVE = 2,
    PDX_V3_LIFE_RELEASE_SAVE = 3,
    PDX_V3_LIFE_LOAD = 4,
    PDX_V3_LIFE_EXIT_TO_MENU = 5,
    PDX_V3_LIFE_SET_SPEED = 6,
    PDX_V3_LIFE_SET_PAUSED = 7,
    PDX_V3_LIFE_RESOLVE_SAVE_PATH = 8,
    PDX_V3_LIFE_SET_DATE = 9,
    PDX_V3_LIFE_EXIT_APPLICATION = 10,
    PDX_V3_LIFE_RESOLVE_EXIT_SAVE_PATH = 11,
};
enum PdxV3LifecycleFlags : uint32_t {
    PDX_V3_LIFE_IN_MAIN_MENU = 1,
    PDX_V3_LIFE_OVERWRITE = 2,
    PDX_V3_LIFE_QUIT_REQUESTED = 4,
    PDX_V3_LIFE_NATIVE_EXIT_SAVE = 8,
};
struct PdxV3LifecycleResult {
    uint32_t size;
    uint32_t error;
    uint32_t action;
    uint32_t flags;
    uint32_t save_type;
    uint32_t paused;
    int32_t speed;
    uint32_t current_state;
    uint32_t pending_state;
    uint32_t transition_pending;
    uint32_t task_state;
    uint32_t task_completed;
    uint32_t file_present;
    uint32_t file_verified;
    uint32_t execution_thread_id;
    uint32_t native_result;
    uint64_t application;
    uint64_t world;
    uint64_t active_idler;
    uint64_t active_idler_vtable;
    uint64_t save_manager;
    uint64_t owned_task_handle;
    uint64_t task;
    uint64_t file_size;
    uint64_t date_raw;
    uint64_t frame;
    char path[PDX_V3_LIFE_PATH_BYTES];
    char resolved_path[PDX_V3_LIFE_PATH_BYTES];
    char date_text[16];
};
// Caller supplies its proven native context. Native strings use UTF-8.
// Save names are relative to the game's actual save root; the native function
// always appends its native extension. It does not accept an arbitrary absolute path.
// BeginSave rejects existing VFS final paths unless overwrite is explicit. Enum values
// stay raw: 3 is the observed ordinary-save default, not a cross-build enum name.
// PollSave takes the same overwrite choice: an old destination alone is not
// replacement verification. Paths returned here remain native VFS paths.
PDX_V3_LIFE_API DWORD WINAPI PdxV3ResolveSavePath(const char* name, char* output, uint32_t capacity);
PDX_V3_LIFE_API DWORD WINAPI PdxV3BeginSave(const char* name, uint32_t save_type, uint32_t overwrite, uint64_t* owned_task);
PDX_V3_LIFE_API DWORD WINAPI PdxV3PollSave(uint64_t owned_task, const char* resolved_path, uint32_t overwrite, PdxV3LifecycleResult* result);
PDX_V3_LIFE_API DWORD WINAPI PdxV3ReleaseSaveTask(uint64_t* owned_task);
PDX_V3_LIFE_API uint64_t WINAPI PdxV3OpenLoadFile(const char* native_path);
PDX_V3_LIFE_API uint64_t WINAPI PdxV3CreateLoadOptions(void* unused);
PDX_V3_LIFE_API DWORD WINAPI PdxV3DestroyLoadFile(uint64_t* owned_file);
PDX_V3_LIFE_API DWORD WINAPI PdxV3DestroyLoadOptions(uint64_t* owned_options);
PDX_V3_LIFE_API DWORD WINAPI PdxV3LoadOwned(uint64_t* owned_file, uint64_t* owned_options);
PDX_V3_LIFE_API DWORD WINAPI PdxV3ExitToMainMenu(void* unused);
PDX_V3_LIFE_API DWORD WINAPI PdxV3SetGameSpeed(int32_t speed);
PDX_V3_LIFE_API DWORD WINAPI PdxV3SetGamePaused(uint32_t paused);
// Native dotted date formatter uses the engine calendar, not host Gregorian math.
PDX_V3_LIFE_API DWORD WINAPI PdxV3FormatGameDate(char* output, uint32_t capacity);
PDX_V3_LIFE_API DWORD WINAPI PdxV3SetGameDate(const char* natural_date);
// Raw native quit request. It requests the engine drain/loop-stop path, not
// immediate process termination. It does not itself choose a save destination.
PDX_V3_LIFE_API DWORD WINAPI PdxV3RequestApplicationQuit(void* application);
// Raw current-idler message6e9: nonzero requests original native exit-save;
// active ironman can save even when this argument is0. Caller owns that intent.
PDX_V3_LIFE_API DWORD WINAPI PdxV3RequestApplicationExit(uint32_t native_save);
PDX_V3_LIFE_API DWORD WINAPI PdxV3SaveForApplicationExit(void* unused);
// Ordinary EXIT_SAVE_NAME only. Ironman owns a different target, not guessed here.
PDX_V3_LIFE_API DWORD WINAPI PdxV3ResolveNativeExitSavePath(char* output, uint32_t capacity);
PDX_V3_LIFE_API void WINAPI PdxV3LifecycleInterface(void* application, void* opaque);

struct PdxV3SaveDescriptorArray {
    uint64_t data;
    uint32_t capacity;
    uint32_t count;
    uint64_t allocator;
};
enum PdxV3SaveCatalogSource : uint32_t {
    PDX_V3_SAVE_LOCAL = 1,
    PDX_V3_SAVE_CLOUD = 2,
};
enum PdxV3SaveCatalogAction : uint32_t {
    PDX_V3_SAVE_LIST = 0,
    PDX_V3_SAVE_DESCRIBE_PATH = 1,
};
struct PdxV3SaveCatalogRequest {
    uint32_t size;
    uint32_t error;
    uint32_t action;
    uint32_t sources;
    uint32_t directory_bytes;
    uint32_t filter_bytes;
    uint32_t output_capacity;
    uint32_t output_size;
    uint32_t output_required;
    uint32_t count;
    uint32_t total_count;
    uint32_t execution_thread_id;
    uint64_t application;
    uint64_t save_manager;
    uint64_t descriptor;
};
// Record followed by seven length-prefixed UTF-8 strings: native VFS path,
// directory, filename stem, version name, native last-played dotted date,
// native localized country-rank text, and serialized game_date dotted text.
// Timestamp stays native raw int64; date raw fields are8B object snapshots.
// last_played_date is serialized real_date+218; game_date is separate+210.
// country_flag_id is a coat-of-arms resource ID, not a CountryFullID.
struct PdxV3SaveDescriptorRecord {
    uint32_t size;
    uint32_t cloud;
    uint32_t is_valid;
    uint32_t is_ironman_save;
    int32_t number_of_players;
    uint32_t reserved;
    int64_t modified_time_raw;
    uint64_t last_played_date_raw;
    uint32_t country_flag_id;
    uint32_t metadata_reserved;
    uint64_t game_date_raw;
};
// Raw native ownership/allocator primitives. Enumerate appends owned native
// descriptor pointers. DestroyArray destroys them and frees through its allocator.
PDX_V3_LIFE_API DWORD WINAPI PdxV3InitializeSaveDescriptorArray(PdxV3SaveDescriptorArray* output);
PDX_V3_LIFE_API DWORD WINAPI PdxV3EnumerateLocalSaves(void* manager, PdxV3SaveDescriptorArray* output,
    const char* native_directory, const char* name_filter);
PDX_V3_LIFE_API DWORD WINAPI PdxV3EnumerateCloudSaves(void* manager, PdxV3SaveDescriptorArray* output,
    const char* native_directory, const char* name_filter);
PDX_V3_LIFE_API DWORD WINAPI PdxV3DestroySaveDescriptorArray(PdxV3SaveDescriptorArray* owned_array);
PDX_V3_LIFE_API DWORD WINAPI PdxV3OpenSaveDescriptor(void* manager, const char* native_path,
    uint32_t cloud, uint64_t* owned_descriptor);
PDX_V3_LIFE_API DWORD WINAPI PdxV3DestroySaveDescriptor(uint64_t* owned_descriptor);
PDX_V3_LIFE_API DWORD WINAPI PdxV3CopySaveDescriptor(uint64_t descriptor, void* output,
    uint32_t capacity, uint32_t* required);
PDX_V3_LIFE_API DWORD WINAPI PdxV3FormatSaveLastPlayedDate(uint64_t descriptor, char* output, uint32_t capacity);
PDX_V3_LIFE_API DWORD WINAPI PdxV3FormatSaveGameDate(uint64_t descriptor, char* output,
    uint32_t capacity) noexcept(false);
PDX_V3_LIFE_API DWORD WINAPI PdxV3FormatSaveCountryRank(uint64_t descriptor, char* output,
    uint32_t capacity, uint32_t* required);
PDX_V3_LIFE_API DWORD WINAPI PdxV3GetSaveCountryFlagId(uint64_t descriptor, uint32_t* output);
PDX_V3_LIFE_API void WINAPI PdxV3SaveCatalogInterface(void* application, void* opaque);


enum PdxV3SaveManagementAction : uint32_t {
    PDX_V3_SAVE_RENAME_NATIVE = 0,
    PDX_V3_SAVE_DELETE_PATH = 1,
    PDX_V3_SAVE_DELETE_NAMED = 2,
};
struct PdxV3SaveManagementRequest {
    uint32_t size;
    uint32_t error;
    uint32_t action;
    uint32_t cloud;
    uint32_t source_bytes;
    uint32_t destination_bytes;
    uint32_t message_capacity;
    uint32_t message_size;
    uint32_t message_required;
    uint32_t native_success;
    uint32_t native_result_tag;
    uint32_t execution_thread_id;
    uint64_t application;
    uint64_t save_manager;
};
// Exact native VFS result: tag0 success, tag1 owns error CString32 in bytes0..31.
// Supply fresh result storage; copy/destroy an earlier result before reuse.
// Explicit noexcept(false) preserves native C++ unwind across the C-linkage raw
// exports under /EHsc. Callers own exception handling and cannot blindly replay.
struct PdxV3NativeFileResult { uint64_t storage[5]; };
// Native rename replaces old physical trailing components with replacement_view.
// It is not a general destination VFS-path resolver. Native replacement is not
// enabled: an existing destination is not silently deleted or overwritten.
PDX_V3_LIFE_API DWORD WINAPI PdxV3RenameNativeSavePath(const char* source_native_path,
    const char* replacement_view, PdxV3NativeFileResult* output) noexcept(false);
PDX_V3_LIFE_API DWORD WINAPI PdxV3DeleteNativeSavePath(const char* native_path,
    PdxV3NativeFileResult* output) noexcept(false);
PDX_V3_LIFE_API DWORD WINAPI PdxV3DestroyNativeFileResult(PdxV3NativeFileResult* owned_result) noexcept(false);
// The original manager name API always joins live root/name/native extension.
// It returns native AL (local absence check, not necessarily an actual unlink).
PDX_V3_LIFE_API DWORD WINAPI PdxV3DeleteNamedSave(void* manager, const char* native_name,
    uint32_t cloud, uint32_t* native_success) noexcept(false);
PDX_V3_LIFE_API void WINAPI PdxV3SaveManagementInterface(void* application, void* opaque) noexcept(false);
