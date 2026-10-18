#include "../v3_access.h"
#include "../native_call.h"
#include <Windows.h>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace {
int failures = 0;
int sequence = 0;
int enters = 0;
int leaves = 0;
int callback_count = 0;
DWORD enter_thread = 0;
void* last_context = nullptr;
void* last_opaque = nullptr;
uint32_t expected_mode = 0;
int32_t expected_flag = 0;
const char* expected_source = nullptr;
void check(bool value, const char* name) {
    std::printf("%s %s\n", value ? "PASS" : "FAIL", name);
    if (!value) ++failures;
}
void* WINAPI mock_enter(void* storage, uint32_t mode, int32_t flag, const char* source) {
    check(reinterpret_cast<uintptr_t>(storage) % 16 == 0, "native 80B storage is aligned16");
    check(mode == expected_mode && flag == expected_flag && source == expected_source,
          "raw mode/flag/source passed unchanged");
    check(sequence == 0, "Enter precedes callback");
    enter_thread = GetCurrentThreadId();
    std::memset(storage, 0xA5, 80);
    sequence = 1;
    ++enters;
    return storage;
}
void WINAPI mock_leave(void* storage) {
    check(sequence == 2 && enter_thread == GetCurrentThreadId(), "Leave follows callback on same thread");
    const auto* data = static_cast<const unsigned char*>(storage);
    check(data[0] == 0xA5 && data[79] == 0xA5, "same live 80B native storage reaches Leave");
    sequence = 3;
    ++leaves;
}
void WINAPI handler(void* context, void* opaque) {
    check(sequence == 1 && GetCurrentThreadId() == enter_thread, "callback executes inside same-thread guard");
    ++callback_count;
    last_context = context;
    last_opaque = opaque;
    if (opaque) ++*static_cast<uint64_t*>(opaque);
    sequence = 2;
}
void WINAPI throwing_handler(void* context, void* opaque) {
    handler(context, opaque);
    throw 73;
}
void WINAPI raw_handler(void* context, void* opaque) {
    check(sequence == 1 && context == reinterpret_cast<void*>(0x1234), "raw callee receives exact context");
    PdxNativeCallInterface(context, opaque);
    ++callback_count;
    sequence = 2;
}
uint64_t raw_target(uint64_t a, double b, uint64_t c, uint64_t d, uint64_t e) {
    check(sequence == 1, "raw Win64 target is invoked before Leave");
    return a + static_cast<uint64_t>(b) + c + d + e;
}
uint64_t double_bits(double value) {
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}
PdxV3AccessRequest request(uint64_t callback, uint64_t opaque, uint32_t mode, int32_t flag) {
    sequence = 0;
    expected_mode = mode;
    expected_flag = flag;
    expected_source = "pdx-runtime/own-host";
    return {sizeof(PdxV3AccessRequest), ERROR_IO_PENDING, mode, flag, callback, opaque,
        reinterpret_cast<uint64_t>(expected_source), reinterpret_cast<uint64_t>(&mock_enter),
        reinterpret_cast<uint64_t>(&mock_leave), 0, 0, 0, 0, 0, 0};
}
}
int main() {
    std::printf("v3_access_host pid=%lu; guard ABI mocks, NOT game guard evidence\n", GetCurrentProcessId());
    check(PdxV3AccessInvoke(nullptr, nullptr) == ERROR_INVALID_PARAMETER, "null request status");
    PdxV3AccessCallback(nullptr, nullptr);
    check(PdxV3AccessEnter(nullptr, 1, 0, "host") == nullptr
        && GetLastError() == ERROR_INVALID_PARAMETER, "raw Enter null storage status");
    PdxV3AccessStorage storage{};
    check(PdxV3AccessEnter(&storage, 1, 0, nullptr) == nullptr
        && GetLastError() == ERROR_INVALID_PARAMETER, "raw Enter null source status");
    check(PdxV3AccessLeave(nullptr) == ERROR_INVALID_PARAMETER, "raw Leave null storage status");
    auto invalid = request(0, 0, 1, 0);
    check(PdxV3AccessInvoke(nullptr, &invalid) == ERROR_INVALID_PARAMETER
        && enters == 0 && leaves == 0, "null callback rejected before Enter");

    uint64_t value = 9;
    auto ordinary = request(reinterpret_cast<uint64_t>(&handler), reinterpret_cast<uint64_t>(&value),
        0xFEDCBA98, -17);
    check(PdxV3AccessInvoke(reinterpret_cast<void*>(0x1234), &ordinary) == 0,
        "synchronous scoped callback completion");
    check(sequence == 3 && enters == 1 && leaves == 1 && callback_count == 1 && value == 10,
        "Enter/callback/Leave once each, raw parameters unrestricted");
    check(ordinary.error == 0 && ordinary.entered == 1 && ordinary.left == 1
        && ordinary.execution_thread_id == GetCurrentThreadId()
        && ordinary.context == 0x1234 && ordinary.enter_result != 0
        && last_context == reinterpret_cast<void*>(0x1234) && last_opaque == &value,
        "public request captures context/thread/guard and caller opaque");

    auto wrapped = request(reinterpret_cast<uint64_t>(&handler), 0, 1, 0);
    PdxV3AccessCallback(nullptr, &wrapped);
    check(wrapped.error == 0 && wrapped.entered && wrapped.left && sequence == 3,
        "callback wrapper forwards a null context and null callee opaque unchanged");

    auto exceptional = request(reinterpret_cast<uint64_t>(&throwing_handler), 0, 3, 1);
    check(PdxV3AccessInvoke(reinterpret_cast<void*>(0x7777), &exceptional) == ERROR_UNHANDLED_EXCEPTION
        && exceptional.error == ERROR_UNHANDLED_EXCEPTION
        && exceptional.entered && exceptional.left && sequence == 3 && enters == leaves,
        "C++ callee exception reports error after RAII Leave exactly once");

    const uint64_t args[] = {11, double_bits(2.0), 17, 19, 23};
    PdxNativeCallRequest raw{sizeof(PdxNativeCallRequest), ERROR_IO_PENDING,
        reinterpret_cast<uint64_t>(&raw_target), 5, args, 0, {}};
    auto composed = request(reinterpret_cast<uint64_t>(&raw_handler), reinterpret_cast<uint64_t>(&raw), 1, 0);
    PdxV3AccessCallback(reinterpret_cast<void*>(0x1234), &composed);
    check(composed.error == 0 && composed.left && raw.error == 0 && raw.rax == 72,
        "unchanged PdxNativeCallInterface composes mixed XMM1+stack inside scope");
    check(enters == leaves, "all successfully entered scopes leave before returning");
    std::printf("RESULT failures=%d\n", failures);
    return failures ? 1 : 0;
}
