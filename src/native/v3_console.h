#pragma once
#include <Windows.h>
#include <stdint.h>
#ifdef PDX_V3_CONSOLE_BUILD
#define PDX_V3_CONSOLE_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_CONSOLE_API extern "C" __declspec(dllimport)
#endif
// Engine-owned CString and CPdxArray storage, not std::string/std::vector ABI.
struct PdxV3ConsoleString { uint64_t storage[2]; uint64_t length; uint64_t capacity; };
struct PdxV3ConsoleArguments { uint64_t data; uint32_t capacity; uint32_t count; uint64_t allocator; };
struct PdxV3ConsoleNativeResult { uint8_t success; uint8_t padding[7]; PdxV3ConsoleString message; };
// Payload: input UTF-8 bytes, then output_capacity bytes. size includes both.
// action1 execute text; 2 enumerate; 3 parse only; 4 exact kind1 callback;
// action5 exact kind2 object method; 6 register descriptor; 7 unregister name.
// action8 execute explicit arguments: uint32 count, then uint32 length + UTF-8
// bytes per string. Element0 is the command name. No tokenizer is called.
// Exact invocation inputs are parameters only.
struct PdxV3ConsoleRequest {
    uint32_t size, error, action, native_success;
    uint32_t input_size, output_capacity, output_size, output_required;
    uint32_t count, total_count, release_restricted, multiplayer_or_ironman;
    uint32_t execution_thread_id, reserved;
    uint64_t application, manager, exact_callback, exact_context;
    uint32_t argument_count, records_bytes;
};
// Variable list record: this header then name, description, aliases and parameters
// encoded as uint32 length + bytes. aliases/parameters counts are explicit.
struct PdxV3ConsoleCommandRecord {
    uint32_t size, kind, developer_allowed, alias_count;
    uint32_t parameter_count, reserved;
    uint64_t object, callback, context;
};
using PdxV3ConsoleVisitor = void (__fastcall*)(uint64_t object, uint32_t kind, void* opaque);

struct PdxV3ConsoleDescriptor {
    uint8_t developer_allowed; uint8_t padding[7];
    PdxV3ConsoleString name;
    uint32_t alias_count; uint32_t reserved;
    PdxV3ConsoleString aliases[3];
    PdxV3ConsoleString description;
    uint64_t callback, completion_callback, context;
    uint32_t parameter_count; uint32_t parameter_reserved;
    PdxV3ConsoleString parameters[10];
};
// Register copies the descriptor/strings into the real native directory. The
// callback code and context remain caller-owned until unregister and call drain.
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleInitializeDescriptor(PdxV3ConsoleDescriptor*);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleDestroyDescriptor(PdxV3ConsoleDescriptor*);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleRegisterDescriptor(uint64_t manager, const PdxV3ConsoleDescriptor*);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleUnregister(uint64_t manager, const char* name);

PDX_V3_CONSOLE_API uint64_t WINAPI PdxV3ConsoleManager();
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleConstructString(PdxV3ConsoleString*, const char*, uint64_t);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleDestroyString(PdxV3ConsoleString*);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleParse(PdxV3ConsoleArguments*, const PdxV3ConsoleString*);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleDestroyArguments(PdxV3ConsoleArguments*);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleExecuteArguments(uint64_t manager, PdxV3ConsoleNativeResult*, const PdxV3ConsoleArguments*);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleExecuteText(uint64_t manager, PdxV3ConsoleNativeResult*, const PdxV3ConsoleString*);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleInvokeExact(uint64_t callback, uint64_t context, PdxV3ConsoleNativeResult*, const PdxV3ConsoleArguments*);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleInvokeObject(uint64_t object, PdxV3ConsoleNativeResult*, const PdxV3ConsoleArguments*);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleEnumerate(uint64_t manager, PdxV3ConsoleVisitor, void* opaque);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleInterface(void* application, void* opaque);

// Independent read-only Player state/tag lookup. No console mutation/dispatch.
// action1 observes copied current local Player refs/observer bit; action2 resolves
// exactly3 native tag bytes through the real tag helper. Console action1..8 and
// PdxV3ConsoleRequest remain unchanged. Country pointers are borrowed snapshots.
struct PdxV3ConsolePlayerState {
    uint32_t size, error, action, tag_bytes;
    uint32_t local_machine_id, application_player_id, player_machine_id, player_flags_raw;
    uint32_t observer_enabled, observer_global, paused, simulation_started;
    uint32_t controlled_country_id, controlled_country_error, selected_country_id, selected_country_error;
    uint32_t target_country_id, target_error, target_control_eligible, execution_thread_id;
    uint64_t application, world, idler, player;
    uint64_t controlled_ref_database, selected_ref_database;
    uint64_t controlled_country, selected_country, target_country;
    char tag[8];
};
// Exact native tag helper, caller must hold its own established Logic scope.
PDX_V3_CONSOLE_API uint64_t WINAPI PdxV3ConsoleResolveTag(const PdxV3ConsoleString* tag) noexcept(false);
// Caller supplies real Application/Interface context. It holds the native
// Interface guard plus Player-list mutex, copies, unlocks then resolves refs.
PDX_V3_CONSOLE_API DWORD WINAPI PdxV3ConsoleReadPlayerState(uint64_t application, PdxV3ConsolePlayerState*) noexcept(false);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsolePlayerStateInterface(void* application, void* opaque);

// Independent read-only CountryAI snapshot. This144B wire does not change the
// console96B request/actions or Player160B state. Input: fresh Country full ID.
// Per-reference errors are independent of error (context/exception status).
// Pointers are borrowed snapshots. Query never creates actors or dispatches AI.
// world is the CGameState origin. country_manager is the first contained
// CCountryManager at the owned world+120 payload origin, not another CGameState.
struct PdxV3ConsoleAiState {
    uint32_t size, error, country_full_id, country_error;
    uint32_t actor_full_id, actor_error, owner_country_full_id, actor_enabled_raw;
    uint32_t manager_override_raw, manager_error, role_index, role_raw;
    uint32_t role_count, role_error, observed_predicate, observed_predicate_valid;
    uint32_t paused, simulation_started, execution_thread_id, reserved;
    uint64_t application, world, idler, country_manager, ai_manager, country, actor, role_data;
};
// Caller supplies real Application/Interface context. One native Interface guard
// covers the native Country/actor resolvers, ownership checks and copied values.
// Actor resolver is noncreating0xc5a4b0, never get_or_create0x1514810.
PDX_V3_CONSOLE_API DWORD WINAPI PdxV3ConsoleReadAiState(uint64_t application, PdxV3ConsoleAiState*) noexcept(false);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleAiStateInterface(void* application, void* opaque);


// Copied ordered cached CountryAI cohort. This new branch does not change the
// existing Console96B/Player160B/AiState144B ABI or native AI command handlers.
// size is the whole caller allocation: header96 + row_capacity * row64.
// Inputs: size,row_capacity,row_bytes(64),expected_world(optional),reserved(0).
// Every engine pointer stays callback-local except copied application/world
// diagnostics. Those numbers are not a lease or later memory-read inputs.
enum PdxV3ConsoleAiCacheFlags : uint32_t {
    PDX_V3_AI_CACHE_CONTEXT_VALID = 1u,
    PDX_V3_AI_CACHE_HEADER_VALID = 2u,
    PDX_V3_AI_CACHE_TOTAL_KNOWN = 4u,
    PDX_V3_AI_CACHE_COMPLETE = 8u,
    PDX_V3_AI_CACHE_TRUNCATED = 16u,
    PDX_V3_AI_CACHE_ROW_ERRORS = 32u,
    PDX_V3_AI_CACHE_PREFIX_FAULT = 64u,
};
enum PdxV3ConsoleAiCacheValidFields : uint32_t {
    PDX_V3_AI_CACHE_ACTOR_FIELDS = 1u,
    PDX_V3_AI_CACHE_ACTOR_IDENTITY = 2u,
    PDX_V3_AI_CACHE_OWNER_IDENTITY = 4u,
    PDX_V3_AI_CACHE_ROLE_VALUE = 8u,
    PDX_V3_AI_CACHE_PREDICATE = 16u,
};
struct PdxV3ConsoleAiCacheSnapshot {
    uint32_t size, error, row_capacity, row_count;
    int32_t native_total;
    uint32_t flags;
    uint64_t required_bytes;
    uint32_t returned_bytes, row_bytes;
    uint32_t manager_override_raw, manager_error, role_count, paused;
    uint32_t simulation_started, execution_thread_id, row_error_count, reserved;
    uint64_t expected_world, world, application;
};
struct PdxV3ConsoleAiCacheRow {
    uint32_t cache_index, valid_fields, actor_full_id, owner_country_full_id;
    uint32_t actor_error, owner_error, link_error, role_error;
    uint32_t actor_enabled_raw, actor_141_raw, role_index, role_raw;
    uint32_t observed_predicate, reserved0, reserved1, reserved2;
};
// One real Interface Access(1,0) covers current-context checks, exact cache
// membership, pool-storage provenance, native full-ID/owner checks and copying.
// No UI pump/reentry, dispatch, ScopeSuspend, implicit resize/retry or mutation.
// Capacity-short returns MORE_DATA234 and whole rows0..row_count-1 only. Row
// errors never filter members. Native exception/read-fault returns its own error
// and a known whole-row prefix, not ordinary truncation or fabricated remainder.
PDX_V3_CONSOLE_API DWORD WINAPI PdxV3ConsoleReadAiCache(
    uint64_t application, PdxV3ConsoleAiCacheSnapshot*) noexcept(false);
PDX_V3_CONSOLE_API void WINAPI PdxV3ConsoleAiCacheInterface(void* application, void* opaque);
