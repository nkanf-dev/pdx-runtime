#include "v3_play_finalize.h"
#include <cstddef>

namespace {
constexpr uintptr_t app_root_rva = 0x592d128;
constexpr uintptr_t idler_root_rva = 0x592d0e0;
constexpr uintptr_t idler_vtable_rva = 0x4431500;
constexpr uintptr_t gfx_vtable_rva = 0x45b13d8;
constexpr uintptr_t handler_vtable_rva = 0x45ae328;
constexpr uintptr_t lobby_vtable_rva = 0x472d4f8;
constexpr uintptr_t logic_vtable_rva = 0x4436a08;
constexpr uintptr_t player_getter_rva = 0x33bdb80;
constexpr uintptr_t sync_lobby_rva = 0x2ae63a0;
constexpr uintptr_t ready_lobby_rva = 0x2ae79d0;
constexpr uintptr_t guard_enter_rva = 0x33bd640;
constexpr uintptr_t guard_leave_rva = 0x33bd750;
using NativeMethod = void (__fastcall*)(void*);
using PlayerGetter = void* (__fastcall*)(void*);
using IsVisible = bool (__fastcall*)(void*);
using GuardEnter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using GuardLeave = void (__fastcall*)(void*);
struct alignas(16) AccessStorage { unsigned char bytes[80] = {}; };
struct InterfaceScope {
    AccessStorage& storage;
    uintptr_t base;
    InterfaceScope(AccessStorage& buffer, uintptr_t image) : storage(buffer), base(image) {
        reinterpret_cast<GuardEnter>(base + guard_enter_rva)(
            storage.bytes, 1, 0, "pdx-runtime/native/v3_play_finalize.cpp");
    }
    ~InterfaceScope() {
        reinterpret_cast<GuardLeave>(base + guard_leave_rva)(storage.bytes);
    }
};
static_assert(sizeof(PdxV3PlayFinalizeResult) == 144);
uintptr_t image_base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T field(uintptr_t object, uintptr_t offset) {
    return *reinterpret_cast<const T*>(object + offset);
}
DWORD inspect(void* application, PdxV3PlayFinalizeResult* result, uintptr_t base) {
    const uintptr_t app = reinterpret_cast<uintptr_t>(application);
    if (!app || app != field<uintptr_t>(base, app_root_rva)
        || field<uint8_t>(app, 0x258)) return ERROR_NOT_READY;
    result->application = app;
    result->world = field<uintptr_t>(app, 0x608);
    if (!result->world) return ERROR_NOT_READY;
    result->idler = field<uintptr_t>(app, 0x1c0);
    if (!result->idler || result->idler != field<uintptr_t>(base, idler_root_rva)
        || field<uintptr_t>(result->idler, 0) != base + idler_vtable_rva)
        return ERROR_NOT_READY;
    result->paused = field<uint8_t>(result->idler, 0x20);
    const uintptr_t logic = field<uintptr_t>(result->idler, 8);
    if (!logic || field<uintptr_t>(logic, 0) != base + logic_vtable_rva)
        return ERROR_NOT_READY;
    result->simulation_started = field<uint8_t>(logic, 0xa8);
    result->gfx = field<uintptr_t>(result->idler, 0x10);
    if (!result->gfx || field<uintptr_t>(result->gfx, 0) != base + gfx_vtable_rva)
        return ERROR_NOT_READY;
    result->handler = field<uintptr_t>(result->gfx, 0x20);
    if (!result->handler || field<uintptr_t>(result->handler, 0) != base + handler_vtable_rva)
        return ERROR_NOT_READY;
    result->gui_mode = field<uint32_t>(result->handler, 8);
    result->lobby = field<uintptr_t>(result->handler, 0x10);
    if (!result->lobby || field<uintptr_t>(result->lobby, 0) != base + lobby_vtable_rva
        || field<uintptr_t>(result->lobby, 0x90) != result->handler
        || field<uintptr_t>(result->lobby, 0x88) != result->idler)
        return ERROR_NOT_READY;
    auto table = reinterpret_cast<uintptr_t*>(base + lobby_vtable_rva);
    result->lobby_visible = reinterpret_cast<IsVisible>(table[7])(
        reinterpret_cast<void*>(result->lobby)) ? 1u : 0u;
    result->player = reinterpret_cast<uintptr_t>(reinterpret_cast<PlayerGetter>(base + player_getter_rva)(
        reinterpret_cast<void*>(result->idler)));
    if (!result->player) return ERROR_NOT_READY;
    result->player_flags = field<uint8_t>(result->player, 0x48);
    result->player_ref_database = field<uintptr_t>(result->player, 0x50);
    result->player_country_id = field<uint32_t>(result->player, 0x58);
    result->handler_ref_database = field<uintptr_t>(result->handler, 0x18);
    result->handler_country_id = field<uint32_t>(result->handler, 0x20);
    result->lobby_ref_database = field<uintptr_t>(result->lobby, 0xa8);
    result->lobby_country_id = field<uint32_t>(result->lobby, 0xb0);
    result->lobby_selected_country_id = field<uint32_t>(result->lobby, 0xa0);
    return ERROR_SUCCESS;
}
}

DWORD WINAPI PdxV3SyncLobbyFromPlayer(uint64_t lobby) {
    const uintptr_t base = image_base();
    if (!lobby || field<uintptr_t>(lobby, 0) != base + lobby_vtable_rva)
        return ERROR_INVALID_PARAMETER;
    // Full native Show body: player-reference sync, native event597, ready(false).
    reinterpret_cast<NativeMethod>(base + sync_lobby_rva)(reinterpret_cast<void*>(lobby));
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ReadyLobby(uint64_t lobby) {
    const uintptr_t base = image_base();
    if (!lobby || field<uintptr_t>(lobby, 0) != base + lobby_vtable_rva)
        return ERROR_INVALID_PARAMETER;
    // Full native Ready body copies controlled reference and queues ready(true).
    reinterpret_cast<NativeMethod>(base + ready_lobby_rva)(reinterpret_cast<void*>(lobby));
    return ERROR_SUCCESS;
}
void WINAPI PdxV3PlayFinalizeInterface(void* application, void* opaque) {
    auto* result = static_cast<PdxV3PlayFinalizeResult*>(opaque);
    if (!result || result->size != sizeof(PdxV3PlayFinalizeResult)) return;
    const uint32_t action = result->action;
    const uint32_t full_id = result->country_id;
    *result = {sizeof(PdxV3PlayFinalizeResult), ERROR_NOT_READY, action, full_id};
    const uintptr_t base = image_base();
    try {
        AccessStorage storage;
        InterfaceScope scope(storage, base);
        result->error = inspect(application, result, base);
        if (result->error || action == 0) return;
        if (action != 1) {
            result->error = ERROR_INVALID_PARAMETER;
            return;
        }
        if (!result->simulation_started || !result->paused || !result->player_ref_database
            || result->player_country_id != full_id) {
            result->error = ERROR_INVALID_STATE;
            return;
        }
        if (result->gui_mode == 2) return;
        if (result->gui_mode != 1) {
            result->error = ERROR_INVALID_STATE;
            return;
        }
        result->error = PdxV3SyncLobbyFromPlayer(result->lobby);
        if (result->error) return;
        result->native_sync_done = 1;
        if (field<uintptr_t>(result->lobby, 0xa8) != result->player_ref_database
            || field<uint32_t>(result->lobby, 0xb0) != full_id) {
            result->error = ERROR_INVALID_STATE;
            return;
        }
        result->error = PdxV3ReadyLobby(result->lobby);
        if (result->error) return;
        result->native_ready_called = 1;
        // No Mode call. Next native frame compares these refs and owns transition.
        result->error = inspect(application, result, base);
    } catch (...) {
        result->error = ERROR_UNHANDLED_EXCEPTION;
    }
}
