#include "v3_time_step.h"
#include <cstddef>

static_assert(sizeof(PdxV3TimeStepResult) == 160);
static_assert(offsetof(PdxV3TimeStepResult, application) == 80);
static_assert(offsetof(PdxV3TimeStepResult, date_raw) == 144);

namespace {
constexpr uintptr_t application_rva = 0x592d128;
constexpr uintptr_t idler_rva = 0x592d0e0;
constexpr uintptr_t session_rva = 0x5956f70;
constexpr uint32_t partial_tick_token = 12426;
constexpr uint32_t natural_submit_flags = 7;
using Factory = void* (__fastcall*)(uint32_t);
using Submit = bool (__fastcall*)(void**, uint32_t);
using Destroy = void* (__fastcall*)(void*, uint32_t);
using GuardEnter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using GuardLeave = void (__fastcall*)(void*);
uintptr_t base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T field(uintptr_t object, uintptr_t offset) {
    return *reinterpret_cast<const T*>(object + offset);
}
struct alignas(16) AccessStorage { unsigned char bytes[80] = {}; };
struct AccessScope {
    AccessStorage storage;
    uintptr_t image;
    explicit AccessScope(uintptr_t module) : image(module) {
        reinterpret_cast<GuardEnter>(image + 0x33bd640)(storage.bytes, 1, 0,
            "pdx-runtime/native/v3_time_step.cpp");
    }
    ~AccessScope() { reinterpret_cast<GuardLeave>(image + 0x33bd750)(storage.bytes); }
};
struct TryLock {
    PSRWLOCK lock;
    bool held;
    explicit TryLock(uintptr_t address) : lock(reinterpret_cast<PSRWLOCK>(address)),
        held(TryAcquireSRWLockExclusive(lock) != FALSE) {}
    ~TryLock() { if (held) ReleaseSRWLockExclusive(lock); }
    TryLock(const TryLock&) = delete;
    TryLock& operator=(const TryLock&) = delete;
};
struct CommandOwner {
    uint64_t command = 0;
    ~CommandOwner() { if (command) PdxV3TimeStepDestroy(&command); }
};
DWORD observe(PdxV3TimeStepResult* r) {
    const uintptr_t image = base();
    r->application = field<uintptr_t>(image, application_rva);
    r->session = image + session_rva;
    r->initialized = field<uint8_t>(r->session, 0x3f0);
    r->thread_stop = field<uint8_t>(r->session, 0xc8);
    r->thread_handle = field<uintptr_t>(r->session, 0xd0);
    r->dispatcher = field<uintptr_t>(r->session, 0x3d8);
    if (!r->application || !r->dispatcher) return ERROR_NOT_READY;
    r->transition_pending = field<uint8_t>(r->application, 0x258);
    r->world = field<uintptr_t>(r->application, 0x608);
    r->idler = field<uintptr_t>(image, idler_rva);
    r->frame = field<uint64_t>(r->application, 0x500);
    if (r->transition_pending || !r->world || !r->idler
        || field<uintptr_t>(r->idler, 0) != image + 0x4431500) return ERROR_NOT_READY;
    r->paused = field<uint8_t>(r->idler, 0x20);
    r->logic = field<uintptr_t>(r->idler, 8);
    if (!r->logic || field<uintptr_t>(r->logic, 0) != image + 0x4436a08) return ERROR_NOT_READY;
    r->game_started = field<uint8_t>(r->logic, 0xa8);
    // Session3386f10 holds dispatcher+98, then outgoing+78 in338cad0 and
    // incoming+88 in3382630. Try-only sampling avoids a blocking lock inversion
    // against a native Logic guard. Lock lifetime ends BEFORE any submission.
    TryLock dispatch(r->dispatcher + 0x98);
    if (!dispatch.held) return ERROR_BUSY;
    TryLock outgoing(r->dispatcher + 0x78);
    if (!outgoing.held) return ERROR_BUSY;
    TryLock incoming(r->dispatcher + 0x88);
    if (!incoming.held) return ERROR_BUSY;
    r->active_command = field<uintptr_t>(r->session, 0x480);
    r->outgoing_active = field<uint32_t>(r->dispatcher, 0xc);
    r->outgoing_pending = field<uint32_t>(r->dispatcher, 0x24);
    r->incoming_active = field<uint32_t>(r->dispatcher, 0x54);
    r->incoming_pending = field<uint32_t>(r->dispatcher, 0x6c);
    r->session_buffered = field<uint32_t>(r->session, 0x304);
    r->logic_tick_pending = field<uint8_t>(r->logic, 0xdf);
    r->world_updating = field<uint8_t>(r->world, 0x14e);
    r->date_raw = field<uint64_t>(r->world, 8);
    r->raw_counter_5964f10 = field<uint32_t>(image, 0x5964f10);
    r->raw_counter_5964f14 = field<uint32_t>(image, 0x5964f14);
    r->queues_settled = !r->outgoing_active && !r->outgoing_pending
        && !r->incoming_active && !r->incoming_pending && !r->session_buffered
        && !r->active_command && !r->world_updating;
    return ERROR_SUCCESS;
}
}

DWORD WINAPI PdxV3TimeStepFactory(uint64_t* owned) {
    if (!owned || *owned) return ERROR_INVALID_PARAMETER;
    *owned = reinterpret_cast<uintptr_t>(reinterpret_cast<Factory>(base() + 0x3371000)(partial_tick_token));
    return *owned ? ERROR_SUCCESS : ERROR_NOT_FOUND;
}
DWORD WINAPI PdxV3TimeStepSubmit(uint64_t* owned, uint32_t* accepted) {
    if (!owned || !*owned || !accepted) return ERROR_INVALID_PARAMETER;
    *accepted = 0;
    // Transfer before entry: an exception has unknown native ownership effects,
    // so never give the old command back for destruction/replay.
    void* command = reinterpret_cast<void*>(*owned);
    *owned = 0;
    *accepted = reinterpret_cast<Submit>(base() + 0xcd2d00)(&command, natural_submit_flags) ? 1u : 0u;
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3TimeStepDestroy(uint64_t* owned) {
    if (!owned || !*owned) return ERROR_INVALID_PARAMETER;
    auto* command = reinterpret_cast<void*>(*owned);
    const auto table = *reinterpret_cast<uintptr_t**>(command);
    *owned = 0;
    reinterpret_cast<Destroy>(table[0])(command, 1);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3TimeStepObserve(PdxV3TimeStepResult* r) {
    if (!r) return ERROR_INVALID_PARAMETER;
    const uint32_t action = r->action;
    *r = {};
    r->size = sizeof(*r); r->action = action;
    r->execution_thread_id = GetCurrentThreadId();
    return r->error = observe(r);
}
void WINAPI PdxV3TimeStepInterface(void* application, void* opaque) {
    auto* r = static_cast<PdxV3TimeStepResult*>(opaque);
    if (!r) return;
    const uint32_t action = r->action;
    *r = {}; r->size = sizeof(*r); r->action = action;
    r->error = ERROR_NOT_READY; r->execution_thread_id = GetCurrentThreadId();
    if (action != PDX_V3_TIME_STEP_OBSERVE && action != PDX_V3_TIME_STEP_SUBMIT_ONE) {
        r->error = ERROR_INVALID_PARAMETER; return;
    }
    const uintptr_t image = base();
    const uintptr_t app = field<uintptr_t>(image, application_rva);
    if (!app || reinterpret_cast<uintptr_t>(application) != app) return;
    try {
        AccessScope scope(image);
        r->error = observe(r); // Every snapshot lock releases inside observe().
        if (r->error || action == PDX_V3_TIME_STEP_OBSERVE) return;
        if (!r->paused || !r->game_started || !r->initialized || r->thread_stop
            || !r->thread_handle || !r->queues_settled) {
            r->error = ERROR_NOT_READY; return;
        }
        CommandOwner command;
        r->error = PdxV3TimeStepFactory(&command.command);
        if (r->error) return;
        r->error = PdxV3TimeStepSubmit(&command.command, &r->accepted);
        if (!r->error && !r->accepted) r->error = ERROR_ACCESS_DENIED;
    } catch (...) { r->error = ERROR_UNHANDLED_EXCEPTION; }
}
