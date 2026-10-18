// Same translation unit permits mock native Session storage without a game PID.
// Tests exercise production queue checks; no substitute queue implementation.
#include "../v3_session_hook.cpp"
#include <cstdio>
#include <cstring>

namespace {
int failures = 0;
unsigned callback_calls = 0, drain_calls = 0, original_calls = 0;
alignas(16) unsigned char host_session[0x400]{};
alignas(16) unsigned char host_dispatcher[0x80]{};
int opaque_storage = 0;
PdxV3SessionCallback* host_slot = nullptr;
HANDLE entered = nullptr, proceed = nullptr;
bool block = false;
void check(bool condition, const char* name) {
    std::printf("%s %s\n", condition ? "PASS" : "FAIL", name);
    if (!condition) ++failures;
}
unsigned char __fastcall original(void* session) {
    if (session != host_session) return 0;
    ++original_calls;
    return 0x37;
}
unsigned char __fastcall third_party(void*) { return 0x51; }
DWORD WINAPI drain(void* session) {
    if (session != host_session) return ERROR_INVALID_PARAMETER;
    ++drain_calls;
    return ERROR_SUCCESS;
}
void WINAPI queued(void* session, void* opaque) {
    check(session == host_session && opaque == &opaque_storage, "exact callback Session and opaque");
    ++callback_calls;
    if (block) { SetEvent(entered); WaitForSingleObject(proceed, INFINITE); }
}
DWORD WINAPI iterate(void*) { return (*host_slot)(host_session); }
template<class T> void field(size_t offset, T value) {
    std::memcpy(host_session + offset, &value, sizeof(value));
}
uint64_t queue_request(DWORD expected, const char* name) {
    const auto before = PdxV3SessionCallbackState;
    const auto counter = next_callback_request;
    uint64_t output_id = 0xDEADBEEF;
    const DWORD error = PdxV3QueueSessionCallback(reinterpret_cast<uintptr_t>(&queued),
        reinterpret_cast<uintptr_t>(&opaque_storage), &output_id);
    check(error == expected, name);
    if (expected) {
        check(output_id == 0xDEADBEEF && next_callback_request == counter
            && std::memcmp(&before, &PdxV3SessionCallbackState, sizeof(before)) == 0,
            "rejected queue leaves request ID, output and existing callback untouched");
    } else check(output_id == counter + 1 && PdxV3SessionCallbackState.request_id == output_id,
                 "accepted queue assigns exactly next request ID");
    return output_id;
}
void native_ready() {
    field<uint64_t>(0xc0, reinterpret_cast<uintptr_t>(host_session));
    field<uint64_t>(0x3d8, reinterpret_cast<uintptr_t>(host_dispatcher));
    field<uint64_t>(0xd0, 1);
    field<uint8_t>(0x3f0, 1);
    field<uint8_t>(0xc8, 0);
    field<uint8_t>(0x3f3, 0);  // generic callbacks are not construction execution.
}
}

int main() {
    std::printf("v3_session_callback_host pid=%lu; mock Session, NOT game lifecycle evidence\n",
        GetCurrentProcessId());
    check(sizeof(PdxV3SessionHookStatusInfo) == 152 && sizeof(PdxV3SessionCallbackStatusInfo) == 56,
        "existing status wire layouts unchanged");
    host_slot = static_cast<PdxV3SessionCallback*>(VirtualAlloc(nullptr, 4096,
        MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
    if (!host_slot) return 2;
    *host_slot = original;
    PdxV3SessionHookBinding request{sizeof(request), 0, reinterpret_cast<uintptr_t>(host_slot),
        reinterpret_cast<uintptr_t>(&original), reinterpret_cast<uintptr_t>(host_session),
        reinterpret_cast<uintptr_t>(&drain)};
    queue_request(ERROR_INVALID_STATE, "inactive queue rejects");
    check(PdxV3SessionHookInstallAt(&request) == 0,
        "InstallAt host works without native worker/game readiness fields");
    auto hook = *host_slot;

    *host_slot = original;
    queue_request(ERROR_INVALID_STATE, "lost hook restored to original rejects phantom acceptance");
    check(!PdxV3SessionHookState.current_callback_matches && active && PdxV3SessionHookState.installed,
        "lost slot updates match diagnostic without automatic repair/deactivation");
    *host_slot = third_party;
    queue_request(ERROR_INVALID_STATE, "third-party slot rejects phantom acceptance");
    *host_slot = hook;
    DWORD old_protection = 0;
    VirtualProtect(host_slot, 4096, PAGE_NOACCESS, &old_protection);
    void* current = nullptr;
    const DWORD slot_read_error = read_memory(reinterpret_cast<uintptr_t>(host_slot), &current, sizeof(current));
    check(slot_read_error != ERROR_SUCCESS, "protected slot really fails ReadProcessMemory");
    queue_request(slot_read_error, "slot exact ReadProcessMemory failure propagated");
    DWORD ignored = 0;
    VirtualProtect(host_slot, 4096, old_protection, &ignored);
    queue_request(0, "restored real hook accepts first request");
    *host_slot = original;
    queue_request(ERROR_INVALID_STATE, "lost original with pending request rejects without cancellation");
    *host_slot = third_party;
    queue_request(ERROR_INVALID_STATE, "third-party with pending request rejects without cancellation");
    *host_slot = hook;
    queue_request(ERROR_BUSY, "usable slot retains ordinary BUSY for pending request");
    check((*host_slot)(host_session) == 0x37 && callback_calls == 1
        && drain_calls == 1 && original_calls == 1,
        "generic completion plus unchanged host construction drain and original dispatch");
    check(PdxV3SessionCallbackState.phase == PDX_V3_SESSION_CALLBACK_COMPLETED
        && PdxV3SessionCallbackState.completed_request_id == 1,
        "accepted request completed once");

    // Exercise the production native_binding branch against owned mock storage.
    native_binding = true;
    native_ready();
    field<uint8_t>(0x3f0, 0);
    queue_request(ERROR_NOT_READY, "native uninitialized Session rejects");
    native_ready(); field<uint64_t>(0x3d8, 0);
    queue_request(ERROR_NOT_READY, "native missing dispatcher rejects");
    native_ready(); field<uint64_t>(0xd0, 0);
    queue_request(ERROR_NOT_READY, "native missing worker record D0 rejects");
    native_ready(); field<uint8_t>(0xc8, 1);
    queue_request(ERROR_NOT_READY, "native stopped worker C8 rejects");
    native_ready(); field<uint64_t>(0x3d8, 1);
    const DWORD native_read_error = snapshot_native();
    check(native_read_error != ERROR_SUCCESS, "invalid dispatcher really fails native snapshot");
    queue_request(native_read_error, "native exact readiness read failure propagated");
    native_ready();
    const auto native_request = queue_request(0, "native ready with execution3f3=0 accepts generic callback");
    field<uint8_t>(0x3f0, 0);
    queue_request(ERROR_NOT_READY, "new enqueue rejects reset without cancelling existing queued request");
    native_ready();
    dispatch_queued_callback(host_session);
    check(PdxV3SessionCallbackState.completed_request_id == native_request && callback_calls == 2,
        "production generic dispatcher completes native-ready request once");
    native_binding = false;

    const auto cancelled = queue_request(0, "queue before explicit Remove");
    auto stale = *host_slot;
    *host_slot = original; // native re-initialization already restored original.
    check(PdxV3SessionHookRemove(nullptr) == 0 && !active && *host_slot == original,
        "explicit Remove accepts already-restored original slot");
    check(PdxV3SessionCallbackState.phase == PDX_V3_SESSION_CALLBACK_CANCELLED
        && PdxV3SessionCallbackState.completed_request_id == cancelled,
        "only explicit Remove cancels existing unclaimed callback");
    check(stale(host_session) == 0x37 && callback_calls == 2 && drain_calls == 1,
        "already fetched stale hook does not replay cancelled callback or drain");
    check(PdxV3SessionHookInstallAt(&request) == 0, "explicit Remove then InstallAt rebinds same slot");
    const auto reinstalled = queue_request(0, "queue after explicit reinstall");
    (*host_slot)(host_session);
    (*host_slot)(host_session);
    check(callback_calls == 3 && PdxV3SessionCallbackState.completed_request_id == reinstalled,
        "reinstalled hook completes new callback exactly once");

    // Native initialization restored our original slot while QUEUED. Repair the
    // same binding in place, preserving the exact native request and opaque.
    native_binding = true;
    native_ready();
    const auto repair_queued = queue_request(0, "queue before native original-slot displacement");
    const auto queued_before_repair = PdxV3SessionCallbackState;
    const auto queued_calls_before = callback_calls;
    *host_slot = original;
    check(PdxV3SessionHookInstallAt(&request) == 0 && *host_slot == hook && active,
        "same native binding reclaims only original slot without Remove");
    check(std::memcmp(&queued_before_repair, &PdxV3SessionCallbackState,
        sizeof(queued_before_repair)) == 0, "repair preserves entire QUEUED receipt and owner");
    check(PdxV3SessionHookInstallAt(&request) == ERROR_ALREADY_EXISTS,
        "healthy repaired hook is reused without replay");
    native_binding = false;  // actual callback executes only the explicit host drain path.
    (*host_slot)(host_session); (*host_slot)(host_session);
    native_binding = true;
    check(callback_calls == queued_calls_before + 1
        && PdxV3SessionCallbackState.completed_request_id == repair_queued,
        "original queued request executes exactly once after repair");
    *host_slot = third_party;
    check(PdxV3SessionHookInstallAt(&request) == ERROR_INVALID_STATE
        && *host_slot == third_party, "repair never overwrites foreign active callback");
    *host_slot = original;
    auto different = request; different.drain = reinterpret_cast<uintptr_t>(&original);
    check(PdxV3SessionHookInstallAt(&different) == ERROR_ALREADY_INITIALIZED
        && *host_slot == original, "repair does not rebind immutable owner");
    check(PdxV3SessionHookInstallAt(&request) == 0, "original same owner remains recoverable");

    entered = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    proceed = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    block = true;
    const auto repair_executing = queue_request(0, "queue original request before executing displacement");
    const auto executing_calls_before = callback_calls;
    native_binding = false;  // running callback must never enter game Access RVAs.
    HANDLE repair_thread = CreateThread(nullptr, 0, iterate, nullptr, 0, nullptr);
    check(repair_thread && WaitForSingleObject(entered, 5000) == WAIT_OBJECT_0,
        "original request is executing before native slot displacement");
    *host_slot = original;
    native_binding = true;   // exercise only the production repair decision while blocked.
    const auto executing_before_repair = PdxV3SessionCallbackState;
    check(PdxV3SessionHookInstallAt(&request) == ERROR_BUSY && *host_slot == original,
        "executing request delays repair without Remove or overwrite");
    check(std::memcmp(&executing_before_repair, &PdxV3SessionCallbackState,
        sizeof(executing_before_repair)) == 0, "busy repair preserves executing original request");
    native_binding = false;  // release original callback on the host-only completion path.
    SetEvent(proceed);
    check(WaitForSingleObject(repair_thread, 5000) == WAIT_OBJECT_0,
        "original executing request finishes normally");
    native_binding = true;
    check(PdxV3SessionHookInstallAt(&request) == 0, "same install resumes repair after original completion");
    native_binding = false;
    (*host_slot)(host_session);
    check(callback_calls == executing_calls_before + 1
        && PdxV3SessionCallbackState.completed_request_id == repair_executing,
        "completed original request is not replayed after repair");
    CloseHandle(repair_thread); CloseHandle(entered); CloseHandle(proceed);
    block = false; native_binding = false;

    // Preserve existing removal-while-executing semantics.
    entered = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    proceed = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    block = true;
    const auto executing = queue_request(0, "queue before own host concurrent execution");
    HANDLE thread = CreateThread(nullptr, 0, iterate, nullptr, 0, nullptr);
    check(thread && WaitForSingleObject(entered, 5000) == WAIT_OBJECT_0,
        "own host callback reaches executing state");
    queue_request(ERROR_BUSY, "executing request remains BUSY");
    check(PdxV3SessionHookRemove(nullptr) == 0
        && PdxV3SessionCallbackState.phase == PDX_V3_SESSION_CALLBACK_EXECUTING,
        "explicit Remove does not cancel executing request");
    SetEvent(proceed);
    check(WaitForSingleObject(thread, 5000) == WAIT_OBJECT_0, "executing own host callback exits");
    check(callback_calls == 6 && PdxV3SessionCallbackState.completed_request_id == executing
        && PdxV3SessionCallbackState.phase == PDX_V3_SESSION_CALLBACK_COMPLETED,
        "executing callback finishes exactly once after Remove");
    CloseHandle(thread); CloseHandle(entered); CloseHandle(proceed); block = false;
    check(PdxV3SessionHookInstallAt(&request) == 0 && PdxV3SessionHookRemove(nullptr) == 0,
        "final explicit reinstall and cleanup leave original callback");
    VirtualFree(host_slot, 0, MEM_RELEASE);
    std::printf("RESULT failures=%d\n", failures);
    return failures ? 1 : 0;
}
