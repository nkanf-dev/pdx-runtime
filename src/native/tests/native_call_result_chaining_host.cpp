#include "../native_call.h"
#include "../v3_access.h"
#include <Windows.h>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>

namespace {
int failures = 0, checks = 0, cases = 0;
void check(bool value, const char* name) {
    ++checks;
    if (!value) { ++failures; std::printf("FAIL %s\n", name); }
}
struct NativeObject { uint32_t full_id; uint32_t reserved; uint64_t value; };
struct Payload2 {
    PdxNativeCallBatchRequest batch;
    PdxNativeCallRequest calls[2];
    uint64_t resolver_args[1];
    uint32_t full_id, received;
    NativeObject object;
};
struct Payload3 {
    PdxNativeCallBatchRequest batch;
    PdxNativeCallRequest calls[3];
    uint64_t resolver_args[1], copy_args[4], getter_args[6];
    uint32_t full_id, received;
    NativeObject object;
};
template<class Payload> struct Envelope {
    PdxV3AccessRequest scope;
    Payload payload;
    char source[32];
    uint64_t context;
};
static_assert(sizeof(PdxNativeCallRequest) == 56);
static_assert(sizeof(PdxNativeCallBatchRequest) == 32);
static_assert(offsetof(PdxNativeCallRequest, arguments) == 24);
static_assert(offsetof(PdxNativeCallRequest, rax) == 32);
static_assert(offsetof(Payload2, calls) == 32);
static_assert(offsetof(Payload3, calls) == 32);
static_assert(offsetof(Envelope<Payload2>, payload) == 88);
static_assert(offsetof(Envelope<Payload3>, payload) == 88);

struct Fixture {
    uintptr_t begin = 0, end = 0;
    NativeObject* expected = nullptr;
    const uint64_t* result_word = nullptr;
    const char* source = nullptr;
    DWORD thread = 0;
    unsigned depth = 0, enters = 0, leaves = 0;
    unsigned resolver_calls = 0, getter_calls = 0, multi_calls = 0;
    bool native_zero = false, getter_throws = false;
} fixture;
constexpr uint32_t full_id = 0xEF000123u;
constexpr uint32_t raw_mode = 0xFEDCBA98u;
constexpr int32_t raw_flag = -17;
constexpr uint64_t value = 0x1122334455667788ull;

bool inside(const void* address, size_t size) {
    const auto start = reinterpret_cast<uintptr_t>(address);
    return start >= fixture.begin && start <= fixture.end && size <= fixture.end - start;
}
void in_scope(const char* name) {
    check(fixture.depth == 1 && fixture.enters == 1 && fixture.leaves == 0, name);
    check(fixture.thread == GetCurrentThreadId(), "all native targets use one callback thread");
}
void* WINAPI enter_scope(void* storage, uint32_t mode, int32_t flag, const char* source) {
    check(fixture.depth == 0 && fixture.enters == 0, "one Enter precedes entire batch");
    check(reinterpret_cast<uintptr_t>(storage) % 16 == 0, "original Access storage align16");
    check(mode == raw_mode && flag == raw_flag && source == fixture.source,
          "caller raw mode/flag/owned source unchanged");
    fixture.thread = GetCurrentThreadId(); ++fixture.enters; fixture.depth = 1;
    std::memset(storage, 0xA5, 80);
    return storage;
}
void WINAPI leave_scope(void* storage) {
    in_scope("Leave follows targets once");
    check(static_cast<unsigned char*>(storage)[0] == 0xA5
        && static_cast<unsigned char*>(storage)[79] == 0xA5, "same live80B scope storage");
    ++fixture.leaves; fixture.depth = 0;
}
__declspec(noinline) NativeObject* resolver(const uint32_t* id) {
    in_scope("resolver executes inside original Access"); ++fixture.resolver_calls;
    check(inside(id, sizeof(*id)) && *id == full_id, "resolver receives actual full uint32 pointer ABI");
    check(inside(fixture.expected, sizeof(*fixture.expected)), "resolver's returned object belongs to batch owner");
    return fixture.native_zero ? nullptr : fixture.expected;
}
__declspec(noinline) uint64_t getter(NativeObject* object) {
    in_scope("single-argument getter executes before Leave"); ++fixture.getter_calls;
    check(fixture.resolver_calls == 1, "dependent getter follows one returned resolver");
    check(inside(fixture.result_word, sizeof(*fixture.result_word)), "previous RAX word belongs to batch owner");
    check(*fixture.result_word == reinterpret_cast<uintptr_t>(object),
          "actual ASM stored previous RAX then loaded it as next argument");
    if (fixture.getter_throws) throw std::runtime_error("own getter C++ failure");
    if (!object) {
        check(fixture.native_zero, "native zero is passed unchanged, not an adapter error");
        return 0;
    }
    check(object == fixture.expected && object->full_id == full_id, "no adjacent-field or pointer inference");
    return object->value;
}
__declspec(noinline) uint64_t multi_getter(uint64_t a, NativeObject* object,
                                         uint64_t b, uint64_t c, uint64_t d, uint64_t e) {
    in_scope("multi-argument getter executes in same scope"); ++fixture.multi_calls;
    check(fixture.resolver_calls == 1 && object == fixture.expected,
          "explicit8B copy filled the selected second argument");
    check(a == 11 && b == 22 && c == 33 && d == 44 && e == 55,
          "independent constants and two stack arguments preserved");
    check(*fixture.result_word == reinterpret_cast<uintptr_t>(object), "copy source is preceding captured RAX");
    return object->value + a + b + c + d + e;
}
PdxNativeCallRequest request(uint64_t target, uint64_t count, const uint64_t* arguments) {
    return {sizeof(PdxNativeCallRequest), ERROR_IO_PENDING, target, count, arguments, 0xDEADBEEF, {}};
}
template<class Payload> void begin_case(Envelope<Payload>& envelope, const char* name) {
    ++cases; std::printf("CASE %s\n", name);
    fixture = {};
    fixture.begin = reinterpret_cast<uintptr_t>(&envelope.payload);
    fixture.end = fixture.begin + sizeof(envelope.payload);
    fixture.expected = &envelope.payload.object;
    fixture.result_word = &envelope.payload.calls[0].rax;
    std::memcpy(envelope.source, "own-batch-result-chain", sizeof("own-batch-result-chain"));
    fixture.source = envelope.source;
    envelope.context = 0x123456789ABCDEF0ull;
    auto& scope = envelope.scope;
    scope = {}; scope.size = sizeof(scope); scope.error = ERROR_IO_PENDING;
    scope.mode = raw_mode; scope.flag = raw_flag;
    scope.callback = reinterpret_cast<uintptr_t>(&PdxNativeCallBatchInterface);
    scope.opaque = reinterpret_cast<uintptr_t>(&envelope.payload.batch);
    scope.source = reinterpret_cast<uintptr_t>(envelope.source);
    scope.enter = reinterpret_cast<uintptr_t>(&enter_scope);
    scope.leave = reinterpret_cast<uintptr_t>(&leave_scope);
    envelope.payload.full_id = full_id;
    envelope.payload.object = {full_id, 0, value};
    envelope.payload.resolver_args[0] = reinterpret_cast<uintptr_t>(&envelope.payload.full_id);
}
template<class Payload> DWORD run(Envelope<Payload>& envelope) {
    const DWORD error = PdxV3AccessInvoke(&envelope.context, &envelope.scope);
    check(fixture.enters == 1 && fixture.leaves == 1 && fixture.depth == 0,
          "one Access Enter/Leave encloses whole callback");
    check(envelope.scope.entered == 1 && envelope.scope.left == 1,
          "original Access metadata records restoration");
    check(envelope.scope.execution_thread_id == fixture.thread
        && envelope.scope.context == reinterpret_cast<uintptr_t>(&envelope.context),
          "same original callback context and thread recorded");
    return error;
}
void prepare_two(Envelope<Payload2>& envelope) {
    auto& p = envelope.payload;
    p.calls[0] = request(reinterpret_cast<uintptr_t>(&resolver), 1, p.resolver_args);
    // A real uint64 field is a legal one-element input vector; no adjacent words are used.
    p.calls[1] = request(reinterpret_cast<uintptr_t>(&getter), 1, &p.calls[0].rax);
    p.batch = {sizeof(p.batch), ERROR_IO_PENDING, 2, p.calls, 0};
    check(p.calls[1].arguments == &p.calls[0].rax && p.calls[1].argument_count == 1,
          "two-call dependency is explicit previous-RAX storage, not its address as an argument value");
}
void two_normal() {
    Envelope<Payload2> envelope{}; begin_case(envelope, "two_call_previous_RAX_to_one_argument");
    prepare_two(envelope);
    check(run(envelope) == 0 && envelope.scope.error == 0, "outer callback completes normally");
    const auto& p = envelope.payload;
    check(p.batch.error == 0 && p.batch.completed == 2 && fixture.getter_calls == 1,
          "both actual calls returned in order");
    check(p.calls[0].rax == reinterpret_cast<uintptr_t>(&p.object) && p.calls[1].rax == value,
          "resolver raw pointer and getter scalar captured separately");
}
void two_native_zero() {
    Envelope<Payload2> envelope{}; begin_case(envelope, "native_zero_is_not_adapter_error");
    prepare_two(envelope); fixture.native_zero = true;
    check(run(envelope) == 0, "raw native-zero sequence still completes");
    const auto& p = envelope.payload;
    check(p.batch.error == 0 && p.batch.completed == 2 && fixture.getter_calls == 1,
          "no invented null-result skip or error gate");
    check(p.calls[0].rax == 0 && p.calls[1].rax == 0 && p.calls[0].error == 0 && p.calls[1].error == 0,
          "zero result remains raw and nullable callee owns its semantics");
}
void two_adapter_error() {
    Envelope<Payload2> envelope{}; begin_case(envelope, "adapter_error_stops_before_dependent_call");
    prepare_two(envelope); envelope.payload.calls[0].target = 0;
    check(run(envelope) == 0, "outer scope and inner batch error remain distinct");
    const auto& p = envelope.payload;
    check(p.batch.error == ERROR_INVALID_PARAMETER && p.batch.completed == 1,
          "returned failing call is included in completed prefix");
    check(fixture.resolver_calls == 0 && fixture.getter_calls == 0
        && p.calls[1].error == ERROR_IO_PENDING && p.calls[1].rax == 0xDEADBEEF,
          "dependent call is not executed, reset or replayed");
}
void prepare_three(Envelope<Payload3>& envelope, uint64_t original_runtime_read) {
    auto& p = envelope.payload;
    p.getter_args[0] = 11; p.getter_args[1] = 0; p.getter_args[2] = 22;
    p.getter_args[3] = 33; p.getter_args[4] = 44; p.getter_args[5] = 55;
    p.copy_args[0] = reinterpret_cast<uintptr_t>(&p.calls[0].rax);
    p.copy_args[1] = reinterpret_cast<uintptr_t>(&p.getter_args[1]);
    p.copy_args[2] = 8; p.copy_args[3] = reinterpret_cast<uintptr_t>(&p.received);
    p.calls[0] = request(reinterpret_cast<uintptr_t>(&resolver), 1, p.resolver_args);
    p.calls[1] = request(original_runtime_read, 4, p.copy_args);
    p.calls[2] = request(reinterpret_cast<uintptr_t>(&multi_getter), 6, p.getter_args);
    p.batch = {sizeof(p.batch), ERROR_IO_PENDING, 3, p.calls, 0};
    check(inside(reinterpret_cast<void*>(p.copy_args[0]), 8)
        && inside(reinterpret_cast<void*>(p.copy_args[1]), 8)
        && inside(reinterpret_cast<void*>(p.copy_args[3]), 4),
          "original PdxRuntimeRead source, selected slot and count are caller-owned batch storage");
}
void three_normal(uint64_t original_runtime_read) {
    Envelope<Payload3> envelope{}; begin_case(envelope, "three_calls_original_PdxRuntimeRead_to_multiarg_slot");
    prepare_three(envelope, original_runtime_read);
    check(run(envelope) == 0, "three-call scope completes");
    const auto& p = envelope.payload;
    check(p.batch.error == 0 && p.batch.completed == 3 && fixture.multi_calls == 1,
          "resolver, original runtime read and multiarg getter returned in order");
    check(p.received == 8 && p.calls[1].rax == ERROR_SUCCESS
        && p.getter_args[1] == reinterpret_cast<uintptr_t>(&p.object),
          "actual carrier ReadProcessMemory copied exactly8B into ordinary argument vector");
    check(p.calls[2].rax == value + 165, "additional constants and stack values affect actual getter result");
}
void three_prefix_error(uint64_t original_runtime_read) {
    Envelope<Payload3> envelope{}; begin_case(envelope, "returned_prefix_preserved_dependency_not_executed");
    prepare_three(envelope, original_runtime_read); envelope.payload.calls[1].target = 0;
    check(run(envelope) == 0, "scope restored after middle adapter validation error");
    const auto& p = envelope.payload;
    check(p.batch.error == ERROR_INVALID_PARAMETER && p.batch.completed == 2
        && p.calls[0].error == 0 && p.calls[0].rax == reinterpret_cast<uintptr_t>(&p.object),
          "returned resolver prefix remains observable");
    check(p.received == 0 && p.getter_args[1] == 0 && fixture.multi_calls == 0
        && p.calls[2].error == ERROR_IO_PENDING && p.calls[2].rax == 0xDEADBEEF,
          "no copy or dependent getter execution after adapter failure");
}
void two_exception() {
    Envelope<Payload2> envelope{}; begin_case(envelope, "throwing_dependency_keeps_unknown_prefix_without_replay");
    prepare_two(envelope); fixture.getter_throws = true;
    check(run(envelope) == ERROR_UNHANDLED_EXCEPTION, "original Access catches C++ exception only after Leave");
    const auto& p = envelope.payload;
    check(p.batch.completed == 1 && p.batch.error == ERROR_IO_PENDING
        && p.calls[0].error == 0 && p.calls[0].rax == reinterpret_cast<uintptr_t>(&p.object)
        && p.calls[1].error == ERROR_IO_PENDING && fixture.getter_calls == 1,
          "completed prefix retained and uncertain call not replayed");
}
struct LoadedCarrier { HMODULE module = nullptr; ~LoadedCarrier() { if (module) FreeLibrary(module); } };
}
int wmain(int argc, wchar_t** argv) {
    if (argc != 2) {
        std::puts("usage: native_call_result_chaining_host.exe <existing carrier DLL path>"); return 2;
    }
    LoadedCarrier carrier{LoadLibraryW(argv[1])};
    if (!carrier.module) { std::printf("FAIL LoadLibraryW error=%lu\n", GetLastError()); return 2; }
    const auto read = GetProcAddress(carrier.module, "PdxRuntimeRead");
    if (!read) { std::printf("FAIL PdxRuntimeRead export error=%lu\n", GetLastError()); return 2; }
    std::printf("result_chain_host pid=%lu; actualASM/Access; existing carrier read export; NO_GAME\n", GetCurrentProcessId());
    std::wprintf(L"CARRIER %ls\n", argv[1]);
    two_normal(); two_native_zero(); two_adapter_error();
    three_normal(reinterpret_cast<uintptr_t>(read));
    three_prefix_error(reinterpret_cast<uintptr_t>(read)); two_exception();
    std::printf("RESULT cases=%d checks=%d failures=%d carrier_initialize_called=0 game_executed=0\n", cases, checks, failures);
    return failures ? 1 : 0;
}
