#pragma once
#include <Windows.h>
#include <cstddef>
#include <cstdint>
#include <exception>

// Internal synchronous bridge for the game's proved inline RandomAllow40.
// No generator/seed is created. Never retain this scope across callbacks.
namespace pdx_script_detail {
struct SourceLocation24 {
    uint32_t line, column;
    const char* file;
    const char* function;
};
struct alignas(8) RandomStorage40 {
    uintptr_t generator = 0;
    uint8_t acquired = 0;
    uint8_t pad09[7] = {};
    uintptr_t destination = 0;
    uintptr_t previous = 0;
    uint8_t restore = 0;
    uint8_t pad21[7] = {};
};
static_assert(sizeof(SourceLocation24) == 24);
static_assert(offsetof(SourceLocation24, file) == 8);
static_assert(offsetof(SourceLocation24, function) == 16);
static_assert(sizeof(RandomStorage40) == 40 && alignof(RandomStorage40) == 8);
static_assert(offsetof(RandomStorage40, acquired) == 8);
static_assert(offsetof(RandomStorage40, destination) == 16);
static_assert(offsetof(RandomStorage40, previous) == 24);
static_assert(offsetof(RandomStorage40, restore) == 32);
using LockCtor = RandomStorage40* (__fastcall*)(RandomStorage40*, uintptr_t,
                                               const SourceLocation24*);
using FullDtor = void (__fastcall*)(RandomStorage40*);
using ThreadId = uint32_t (*)();
struct RandomOperations {
    uintptr_t generator;
    void* volatile* active;
    LockCtor construct;
    FullDtor destroy;
    ThreadId thread_id;
};

class RandomAllow final {
    RandomStorage40 storage_{};
    RandomOperations operations_;
    bool lock_constructed_ = false;
    bool published_ = false;
    DWORD error_ = ERROR_NOT_READY;

    void release_lock_only() noexcept {
        // Original34BA487..4D0: acquired0 is a no-op, including foreign/nested.
        // Do not invoke the full pointer-restoring destructor before publication.
        if (!storage_.acquired) return;
        if (storage_.generator != operations_.generator) std::terminate();
        auto* owner = reinterpret_cast<volatile LONG*>(storage_.generator + 0x10);
        const auto removed = static_cast<uint32_t>(InterlockedExchange(owner, 0));
        // Mirror the proved native ownership invariant; a violation is fatal,
        // not success/retry or permission to continue after corrupt ownership.
        if (removed != operations_.thread_id()) std::terminate();
        storage_.acquired = 0;
    }
public:
    RandomAllow(const RandomOperations& operations, const SourceLocation24& source)
        : operations_(operations) {
        if (!operations_.generator || !operations_.active || !operations_.construct
            || !operations_.destroy || !operations_.thread_id) {
            error_ = ERROR_INVALID_PARAMETER;
            return;
        }
        // A throwing native constructor has not returned a constructed prefix.
        // No class destructor runs on that path; no foreign token is released.
        const auto* returned = operations_.construct(&storage_, 0, &source);
        lock_constructed_ = true;
        if (returned != &storage_ || storage_.generator != operations_.generator
            || storage_.acquired > 1) {
            error_ = ERROR_INVALID_DATA;
            return;
        }
        const auto owner = *reinterpret_cast<const volatile uint32_t*>(
            operations_.generator + 0x10);
        // acquired0 also denotes legitimate same-thread nesting. Check the
        // actual native owner, not just that byte or a non-null active pointer.
        error_ = owner == operations_.thread_id() ? ERROR_SUCCESS : ERROR_BUSY;
    }
    ~RandomAllow() noexcept {
        if (published_) operations_.destroy(&storage_); // original7E06D0 once
        else if (lock_constructed_) release_lock_only();
    }
    RandomAllow(const RandomAllow&) = delete;
    RandomAllow& operator=(const RandomAllow&) = delete;
    RandomAllow(RandomAllow&&) = delete;
    RandomAllow& operator=(RandomAllow&&) = delete;

    DWORD error() const noexcept { return error_; }
    bool published() const noexcept { return published_; }
    void publish() noexcept {
        if (error_ || published_) return;
        // Original caller's 8-byte XCHG: publish THIS native-constructed prefix,
        // save the exact previous scope, and arm full native cleanup. No call,
        // allocation, user code or throwing operation belongs in this gap.
        storage_.destination = reinterpret_cast<uintptr_t>(operations_.active);
        storage_.previous = reinterpret_cast<uintptr_t>(InterlockedExchangePointer(
            operations_.active, &storage_));
        storage_.restore = 1;
        published_ = true;
    }
};
} // namespace pdx_script_detail
