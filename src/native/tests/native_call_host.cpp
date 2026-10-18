#include "../native_call.h"
#include <Windows.h>
#include <immintrin.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <vector>

extern "C" uint64_t PdxTestStackProbe();
extern "C" uint64_t PdxTestRawReturn();
extern "C" uint64_t PdxTestNonvolatile(PdxNativeCallRequest* request);

namespace {
int failures = 0;
void check(bool condition, const char* name) {
    std::printf("%s %s\n", condition ? "PASS" : "FAIL", name);
    if (!condition) ++failures;
}
uint64_t double_bits(double value) {
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}
uint64_t float_bits(float value) {
    uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}
double returned_double(const PdxNativeCallRequest& request) {
    double value;
    std::memcpy(&value, request.xmm0, sizeof(value));
    return value;
}
PdxNativeCallRequest make_request(uint64_t target, const std::vector<uint64_t>& args) {
    return {sizeof(PdxNativeCallRequest), ERROR_IO_PENDING, target, args.size(),
        args.data(), 0, {}};
}
PdxNativeCallRequest call(uint64_t target, std::initializer_list<uint64_t> args) {
    const std::vector<uint64_t> arguments(args);
    auto request = make_request(target, arguments);
    check(PdxNativeCall(&request) == 0 && request.error == 0, "call completion");
    return request;
}
__declspec(noinline) uint64_t integer(uint64_t a, uint64_t b, uint64_t c, uint64_t d) {
    return (a ^ b) + 3 * c + 5 * d;
}
__declspec(noinline) double mixed(uint64_t a, double b, uint64_t c, double d,
                                double e, uint64_t f, double g) {
    return a + 2 * b + 3 * c + 4 * d + 5 * e + 6 * f + 7 * g;
}
__declspec(noinline) float mixed_float(uint64_t a, float b, uint64_t c, float d) {
    return static_cast<float>(a + c) + 2 * b + 4 * d;
}
__declspec(noinline) uint64_t stack_args(uint64_t a, uint64_t b, uint64_t c,
    uint64_t d, uint64_t e, uint64_t f, uint64_t g, uint64_t h, uint64_t i) {
    return a + 2*b + 3*c + 4*d + 5*e + 6*f + 7*g + 8*h + 9*i;
}
struct Triple { uint64_t a, b, c; };
__declspec(noinline) Triple returned_struct(uint64_t a, uint64_t b) {
    return {a, b, a ^ b};
}
__declspec(noinline) __m128 vector_result() {
    return _mm_castsi128_ps(_mm_set_epi64x(0x1020304050607080LL, 0x1122334455667788LL));
}
__declspec(noinline) uint64_t throws_exception() { throw 42; }
struct DestructorProbe {
    bool* destroyed;
    ~DestructorProbe() { *destroyed = true; }
};
void unwind_call(uint64_t target, const std::vector<uint64_t>& args, bool* destroyed) {
    DestructorProbe probe{destroyed};
    auto request = make_request(target, args);
    PdxNativeCall(&request);
}
}

int main() {
    std::printf("native_call_host pid=%lu; ordinary Win64 ABI\n", GetCurrentProcessId());
    check(PdxNativeCall(nullptr) == ERROR_INVALID_PARAMETER, "null request status");
    PdxNativeCallInterface(nullptr, nullptr);
    PdxNativeCallRequest invalid{};
    check(PdxNativeCall(&invalid) == ERROR_INVALID_PARAMETER && invalid.error == ERROR_INVALID_PARAMETER,
          "null target status");
    invalid.target = reinterpret_cast<uint64_t>(&integer);
    invalid.argument_count = 1;
    check(PdxNativeCall(&invalid) == ERROR_INVALID_PARAMETER, "null nonempty arguments status");

    const auto ints = call(reinterpret_cast<uint64_t>(&integer), {0xFFFFFFFF00000000ULL, 9, 11, 13});
    check(ints.rax == integer(0xFFFFFFFF00000000ULL, 9, 11, 13), "integer GP positional bits and raw RAX");
    const auto fp = call(reinterpret_cast<uint64_t>(&mixed),
        {11, double_bits(1.25), 17, double_bits(2.5), double_bits(3.75), 19, double_bits(4.5)});
    check(returned_double(fp) == mixed(11, 1.25, 17, 2.5, 3.75, 19, 4.5),
        "mixed double uses XMM1/XMM3 and positional stack FP");
    const auto floats = call(reinterpret_cast<uint64_t>(&mixed_float),
        {11, float_bits(1.25f), 17, float_bits(2.5f)});
    float float_value;
    std::memcpy(&float_value, floats.xmm0, sizeof(float_value));
    check(float_value == mixed_float(11, 1.25f, 17, 2.5f), "scalar float low32 bits");
    const auto stacked = call(reinterpret_cast<uint64_t>(&stack_args), {1,2,3,4,5,6,7,8,9});
    check(stacked.rax == 285, "five stack arguments positions 5 through 9");
    const auto probe = call(reinterpret_cast<uint64_t>(&PdxTestStackProbe), {1,2,3,4,0xA5});
    check(probe.rax == 1, "callee entry alignment and writable 32-byte home space");

    Triple storage{};
    const auto sret = call(reinterpret_cast<uint64_t>(&returned_struct),
        {reinterpret_cast<uint64_t>(&storage), 0xABC, 0x123});
    check(storage.a == 0xABC && storage.b == 0x123 && storage.c == (0xABC ^ 0x123)
        && sret.rax == reinterpret_cast<uint64_t>(&storage), "explicit hidden sret pointer shifts arguments");
    const auto raw = call(reinterpret_cast<uint64_t>(&PdxTestRawReturn), {});
    uint64_t xmm_words[2];
    std::memcpy(xmm_words, raw.xmm0, sizeof(xmm_words));
    check(raw.rax == 0xFEDCBA9876543210ULL && xmm_words[0] == 0x1122334455667788ULL
        && xmm_words[1] == 0x1020304050607080ULL, "simultaneous raw RAX and full128 XMM0");
    const auto vect = call(reinterpret_cast<uint64_t>(&vector_result), {});
    std::memcpy(xmm_words, vect.xmm0, sizeof(xmm_words));
    check(xmm_words[0] == 0x1122334455667788ULL && xmm_words[1] == 0x1020304050607080ULL,
        "ordinary ABI __m128 return capture");

    const std::vector<uint64_t> many_args(2048, 0xA5);
    auto many = make_request(reinterpret_cast<uint64_t>(&PdxTestStackProbe), many_args);
    check(PdxTestNonvolatile(&many) == 0 && many.rax == 1 && many.error == 0,
        "page-probed dynamic stack and all Win64 nonvolatile GP/XMM registers");
    const std::vector<uint64_t> callback_args{5,6,7,8};
    auto callback = make_request(reinterpret_cast<uint64_t>(&integer), callback_args);
    PdxNativeCallInterface(reinterpret_cast<void*>(1), &callback);
    check(callback.error == 0 && callback.rax == integer(5,6,7,8),
        "callback forwards opaque request without using first context");
    std::vector<uint64_t> batch_integer_args{1, 2, 3, 4};
    std::vector<uint64_t> batch_mixed_args{11, double_bits(1.25), 17, double_bits(2.5),
        double_bits(3.75), 19, double_bits(4.5)};
    PdxNativeCallRequest batch_calls[] = {
        make_request(reinterpret_cast<uint64_t>(&integer), batch_integer_args),
        make_request(reinterpret_cast<uint64_t>(&mixed), batch_mixed_args),
        make_request(reinterpret_cast<uint64_t>(&PdxTestRawReturn), {}),
    };
    PdxNativeCallBatchRequest batch{sizeof(PdxNativeCallBatchRequest), ERROR_IO_PENDING, 3, batch_calls, 0};
    PdxNativeCallBatchInterface(reinterpret_cast<void*>(1), &batch);
    check(batch.error == 0 && batch.completed == 3
        && batch_calls[0].rax == integer(1,2,3,4)
        && returned_double(batch_calls[1]) == mixed(11,1.25,17,2.5,3.75,19,4.5),
        "batch sequence preserves per-call integer/mixed ABI");
    std::memcpy(xmm_words, batch_calls[2].xmm0, sizeof(xmm_words));
    check(batch_calls[2].rax == 0xFEDCBA9876543210ULL
        && xmm_words[1] == 0x1020304050607080ULL, "batch raw RAX/XMM128 preserved");
    batch_calls[0] = make_request(reinterpret_cast<uint64_t>(&integer), batch_integer_args);
    batch_calls[1] = make_request(0, {});
    batch_calls[2] = make_request(reinterpret_cast<uint64_t>(&integer), batch_integer_args);
    batch_calls[2].rax = 0xDEAD;
    check(PdxNativeCallBatch(&batch) == ERROR_INVALID_PARAMETER && batch.completed == 2
        && batch_calls[0].error == 0 && batch_calls[1].error == ERROR_INVALID_PARAMETER
        && batch_calls[2].error == ERROR_IO_PENDING && batch_calls[2].rax == 0xDEAD,
        "batch stops on returned error without executing or resetting later calls");
    PdxNativeCallBatchRequest empty{sizeof(empty), ERROR_IO_PENDING, 0, nullptr, 9};
    check(PdxNativeCallBatch(&empty) == 0 && empty.completed == 0,
        "empty batch completes without target execution");
    batch_calls[1] = make_request(reinterpret_cast<uint64_t>(&throws_exception), {});
    batch.completed = 0;
    bool batch_caught = false;
    try { PdxNativeCallBatch(&batch); } catch (int value) { batch_caught = value == 42; }
    check(batch_caught && batch.completed == 1 && batch.error == ERROR_IO_PENDING
        && batch_calls[1].error == ERROR_IO_PENDING && batch_calls[2].rax == 0xDEAD,
        "batch exception preserves returned prefix and leaves unknown call pending");

    bool destroyed = false;
    bool caught = false;
    try {
        unwind_call(reinterpret_cast<uint64_t>(&throws_exception), many_args, &destroyed);
    } catch (int value) {
        caught = value == 42;
    }
    check(caught && destroyed, "C++ unwind crosses dynamically allocated MASM frame");
    std::printf("RESULT failures=%d\n", failures);
    return failures ? 1 : 0;
}
