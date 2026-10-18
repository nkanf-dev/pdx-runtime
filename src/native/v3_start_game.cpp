#include "v3_start_game.h"
#include <cstddef>

namespace {
constexpr uintptr_t application_root_rva = 0x592d128;
constexpr uintptr_t idler_root_rva = 0x592d0e0;
constexpr uintptr_t machine_id_rva = 0x595735c;
constexpr uintptr_t country_database_rva = 0x5964e20;
constexpr uintptr_t country_fallback_rva = 0x5964e18;
constexpr uintptr_t country_resolver_rva = 0x7bb480;
constexpr uintptr_t player_getter_rva = 0x33bdb80;
constexpr uintptr_t set_playable_vtable_rva = 0x4732fd0;
constexpr uintptr_t set_playable_validate_rva = 0x2b061b0;
constexpr uintptr_t set_playable_execute_rva = 0x2b05aa0;
constexpr uintptr_t start_vtable_rva = 0x4436780;
constexpr uintptr_t start_factory_rva = 0x7d7040;
constexpr uintptr_t submit_rva = 0xcd2d00;
constexpr uintptr_t pause_rva = 0x33bdc10;
constexpr uintptr_t guard_enter_rva = 0x33bd640;
constexpr uintptr_t guard_leave_rva = 0x33bd750;
constexpr uintptr_t ingame_idler_vtable_rva = 0x4431500;
constexpr uintptr_t ingame_logic_vtable_rva = 0x4436a08;
using Resolver = void* (__fastcall*)(const uint32_t*);
using PlayerGetter = void* (__fastcall*)(void*);
using PlayableValidate = bool (__fastcall*)(const void*, void*);
using PlayableExecute = void (__fastcall*)(void*);
using StartFactory = void* (__fastcall*)(const void*, void**);
using Submit = bool (__fastcall*)(void**, uint32_t);
using Destroy = void* (__fastcall*)(void*, uint32_t);
using SetPaused = void (__fastcall*)(void*, bool, uint32_t);
using GuardEnter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using GuardLeave = void (__fastcall*)(void*);

struct CommandHeader {
    uint64_t vtable;
    uint8_t flags;
    uint8_t padding[3];
    uint32_t sender;
    uint32_t sequence;
    uint32_t reserved;
};
struct SetPlayableTemplate {
    CommandHeader header;
    uint32_t country_id;
    uint32_t machine_id;
    uint8_t flag;
    uint8_t padding[7];
};
static_assert(sizeof(CommandHeader) == 24);
static_assert(sizeof(SetPlayableTemplate) == 40);
static_assert(offsetof(SetPlayableTemplate, country_id) == 0x18);
static_assert(offsetof(SetPlayableTemplate, machine_id) == 0x1c);
static_assert(offsetof(SetPlayableTemplate, flag) == 0x20);
struct alignas(16) AccessStorage { unsigned char bytes[80] = {}; };
static_assert(sizeof(PdxV3StartResult) == 80);
struct InterfaceScope {
    AccessStorage& storage;
    uintptr_t base;
    InterfaceScope(AccessStorage& buffer, uintptr_t image) : storage(buffer), base(image) {
        reinterpret_cast<GuardEnter>(base + guard_enter_rva)(
            storage.bytes, 1, 0, "pdx-runtime/native/v3_start_game.cpp");
    }
    ~InterfaceScope() {
        reinterpret_cast<GuardLeave>(base + guard_leave_rva)(storage.bytes);
    }
};
uintptr_t module_base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T field(uintptr_t object, uintptr_t offset) {
    return *reinterpret_cast<const T*>(object + offset);
}
uintptr_t app_from(uintptr_t base) { return field<uintptr_t>(base, application_root_rva); }
uintptr_t current_idler(uintptr_t base) { return field<uintptr_t>(base, idler_root_rva); }
void observe(uintptr_t base, PdxV3StartResult* result) {
    result->application = app_from(base);
    result->world = result->application ? field<uintptr_t>(result->application, 0x608) : 0;
    result->idler = current_idler(base);
    result->local_machine_id = field<uint32_t>(base, machine_id_rva);
    result->game_started = 0;
    if (!result->idler) return;
    result->paused = field<uint8_t>(result->idler, 0x20);
    result->logic = field<uintptr_t>(result->idler, 8);
    result->game_started = result->logic ? field<uint8_t>(result->logic, 0xa8) : 0;
    auto player = reinterpret_cast<PlayerGetter>(base + player_getter_rva)(
        reinterpret_cast<void*>(result->idler));
    result->player = reinterpret_cast<uintptr_t>(player);
    result->selected_country_id = player ? field<uint32_t>(result->player, 0x58) : 0xffffffffu;
}
}

DWORD WINAPI PdxV3SelectCountry(uint32_t full_id) {
    const uintptr_t base = module_base();
    if (!field<uintptr_t>(base, country_database_rva)) return ERROR_NOT_READY;
    void* country = reinterpret_cast<Resolver>(base + country_resolver_rva)(&full_id);
    if (!country || reinterpret_cast<uintptr_t>(country) == field<uintptr_t>(base, country_fallback_rva)
        || field<uint32_t>(reinterpret_cast<uintptr_t>(country), 0x18) != full_id)
        return ERROR_NOT_FOUND;
    SetPlayableTemplate command = {};
    command.header.vtable = base + set_playable_vtable_rva;
    command.header.flags = 8;
    command.country_id = full_id;
    command.machine_id = field<uint32_t>(base, machine_id_rva);
    // 0x7d48bf..0x7d48d4: native stack command, validator, then executor.
    if (!reinterpret_cast<PlayableValidate>(base + set_playable_validate_rva)(&command, nullptr))
        return ERROR_ACCESS_DENIED;
    reinterpret_cast<PlayableExecute>(base + set_playable_execute_rva)(&command);
    auto* player = reinterpret_cast<PlayerGetter>(base + player_getter_rva)(
        reinterpret_cast<void*>(current_idler(base)));
    if (!player || field<uint32_t>(reinterpret_cast<uintptr_t>(player), 0x58) != full_id)
        return ERROR_NOT_READY;
    return ERROR_SUCCESS;
}
uint64_t WINAPI PdxV3StartGameFactory(void*) {
    const uintptr_t base = module_base();
    CommandHeader command = {};
    command.vtable = base + start_vtable_rva;
    void* owned = nullptr;
    reinterpret_cast<StartFactory>(base + start_factory_rva)(&command, &owned);
    return reinterpret_cast<uintptr_t>(owned);
}
DWORD WINAPI PdxV3SubmitStartGame(uint64_t* owned_command, uint32_t* accepted) {
    if (!owned_command || !accepted || !*owned_command) return ERROR_INVALID_PARAMETER;
    const uintptr_t base = module_base();
    if (field<uintptr_t>(*owned_command, 0) != base + start_vtable_rva)
        return ERROR_INVALID_PARAMETER;
    void* owned = reinterpret_cast<void*>(*owned_command);
    *owned_command = 0;
    // Natural start at 0x7d5fe4 uses 0x0e: native outgoing lock + start flags.
    *accepted = reinterpret_cast<Submit>(base + submit_rva)(&owned, 0x0e) ? 1u : 0u;
    return *accepted ? ERROR_SUCCESS : ERROR_ACCESS_DENIED;
}
DWORD WINAPI PdxV3DestroyStartGame(uint64_t* owned_command) {
    if (!owned_command || !*owned_command) return ERROR_INVALID_PARAMETER;
    const uintptr_t base = module_base();
    if (field<uintptr_t>(*owned_command, 0) != base + start_vtable_rva)
        return ERROR_INVALID_PARAMETER;
    void* owned = reinterpret_cast<void*>(*owned_command);
    *owned_command = 0;
    auto table = *reinterpret_cast<uintptr_t**>(owned);
    reinterpret_cast<Destroy>(table[0])(owned, 1);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3SetPaused(uint32_t paused) {
    const uintptr_t base = module_base();
    uintptr_t idler = current_idler(base);
    if (!idler) return ERROR_NOT_READY;
    reinterpret_cast<SetPaused>(base + pause_rva)(reinterpret_cast<void*>(idler),
        paused != 0, field<uint32_t>(base, machine_id_rva));
    return field<uint8_t>(idler, 0x20) == (paused != 0) ? ERROR_SUCCESS : ERROR_INVALID_STATE;
}
void WINAPI PdxV3StartGameInterface(void* application, void* opaque) {
    auto* result = static_cast<PdxV3StartResult*>(opaque);
    if (!result || result->size != sizeof(PdxV3StartResult)) return;
    const uint32_t action = result->action;
    const uint32_t full_id = result->country_id;
    *result = {sizeof(PdxV3StartResult), ERROR_NOT_READY, action, full_id};
    const uintptr_t base = module_base();
    const uintptr_t app = reinterpret_cast<uintptr_t>(application);
    if (!app || app != app_from(base) || field<uint8_t>(app, 0x258)
        || !field<uintptr_t>(app, 0x608)) return;
    const uintptr_t idler = field<uintptr_t>(app, 0x1c0);
    if (!idler || idler != current_idler(base)
        || field<uintptr_t>(idler, 0) != base + ingame_idler_vtable_rva) return;
    const uintptr_t logic = field<uintptr_t>(idler, 8);
    if (!logic || field<uintptr_t>(logic, 0) != base + ingame_logic_vtable_rva) return;
    try {
        AccessStorage storage;
        InterfaceScope scope(storage, base);
        result->error = ERROR_SUCCESS;
        if (action == PDX_V3_SELECT_COUNTRY || action == PDX_V3_SELECT_AND_START_PAUSED)
            result->error = PdxV3SelectCountry(full_id);
        if (!result->error && (action == PDX_V3_START_PAUSED || action == PDX_V3_SELECT_AND_START_PAUSED)) {
            auto* player = reinterpret_cast<PlayerGetter>(base + player_getter_rva)(reinterpret_cast<void*>(idler));
            if (!player || field<uint32_t>(reinterpret_cast<uintptr_t>(player), 0x58) == 0xffffffffu)
                result->error = ERROR_NOT_READY;
            // Starting can update ironman metadata. This task uses the non-ironman
            // temporary world and refuses the native ironman branch rather than
            // modifying settings or touching any original save.
            if (!field<uint8_t>(base, 0x58fd50c)
                && field<uint8_t>(reinterpret_cast<uintptr_t>(reinterpret_cast<void* (__fastcall*)()>(base + 0x3029580)()), 0x112))
                result->error = ERROR_NOT_SUPPORTED;
            if (!result->error) result->error = PdxV3SetPaused(1);
            if (!result->error && !field<uint8_t>(logic, 0xa8)) {
                uint64_t owned = PdxV3StartGameFactory(nullptr);
                result->error = PdxV3SubmitStartGame(&owned, &result->start_accepted);
            }
        } else if (action != PDX_V3_SELECT_COUNTRY && action != PDX_V3_OBSERVE_START) {
            if (!result->error) result->error = ERROR_INVALID_PARAMETER;
        }
        observe(base, result);
    } catch (...) {
        result->error = ERROR_UNHANDLED_EXCEPTION;
    }
}
