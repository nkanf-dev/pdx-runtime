#pragma once
#include <type_traits>

// Callback-only construction lifetime mechanics. No engine ABI, registry,
// publication, or gameplay behavior.
// Caller serializes access and validates source ownership/world before entry.
// Construct/acquire may throw C++ exceptions. Retain/free/release MUST NOT throw.
namespace pdx::owned_context {
namespace detail {
template<class Cleanup>
struct cleanup_guard {
    void* pointer;
    Cleanup& cleanup;
    cleanup_guard(void* value, Cleanup& callback) noexcept
        : pointer(value), cleanup(callback) {}
    ~cleanup_guard() noexcept { if (pointer) cleanup(pointer); }
    cleanup_guard(const cleanup_guard&) = delete;
    cleanup_guard& operator=(const cleanup_guard&) = delete;
};

template<class Status, class Release, class Configure>
Status configure_owned(void* object, Release& release, Configure& configure,
                       Status success, void*& output) {
    cleanup_guard<Release> owned(object, release);
    const Status status = configure(object);
    if (status != success) return status;
    output = object;
    owned.pointer = nullptr;
    return success;
}
} // namespace detail

// Full construction is complete ONLY when construct(storage) returns normally.
// Before that return, the only adapter cleanup is free_raw(storage). Native
// partial-subobject unwind belongs to construct; NEVER call the full destructor.
// The constructor callback uses this storage in place and returns no owner.
// On success, output transfers exactly one adapter reference to the caller.
// On a status failure or C++ exception, output stays null.
// output MUST be empty on entry; this function never replaces an existing owner.
template<class Status, class Allocate, class FreeRaw, class Construct,
         class Retain, class Release, class Configure>
Status allocate_construct(Allocate&& allocate, FreeRaw&& free_raw,
                          Construct&& construct, Retain&& retain,
                          Release&& release, Configure&& configure,
                          Status success, Status no_memory, void*& output) {
    static_assert(std::is_nothrow_invocable_v<FreeRaw&, void*>);
    static_assert(std::is_nothrow_invocable_v<Retain&, void*>);
    static_assert(std::is_nothrow_invocable_v<Release&, void*>);
    output = nullptr;
    void* storage = allocate();
    if (!storage) return no_memory;
    detail::cleanup_guard<FreeRaw> raw(storage, free_raw);
    construct(storage);
    // No converter/configuration or fallible callback can precede this retain.
    retain(storage);
    raw.pointer = nullptr;
    return detail::configure_owned(storage, release, configure, success, output);
}

// Normal-return factory adapter, useful for the current StateName factory.
// acquire() must return a COMPLETE exclusive ref0 object, not an owned CPdxPtr.
// If acquire throws before returning, its internal allocation/unwind is opaque
// here. This overload cannot free memory that the factory never exposed.
template<class Status, class Acquire, class Retain, class Release, class Configure>
Status from_ref0_factory(Acquire&& acquire, Retain&& retain, Release&& release,
                        Configure&& configure, Status success,
                        Status no_memory, void*& output) {
    static_assert(std::is_nothrow_invocable_v<Retain&, void*>);
    static_assert(std::is_nothrow_invocable_v<Release&, void*>);
    output = nullptr;
    void* object = acquire();
    if (!object) return no_memory;
    retain(object);
    return detail::configure_owned(object, release, configure, success, output);
}
} // namespace pdx::owned_context
