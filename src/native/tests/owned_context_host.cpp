#include "../owned_context_core.h"
#include <array>
#include <cstddef>
#include <cstdio>
#include <initializer_list>

// CPU-only lifetime tests. No game image, native library, process discovery,
// address-based engine callback, runtime DLL, or actual V3 object is used.
namespace {
constexpr int success = 0;
constexpr int no_memory = 8;
constexpr int config_error = 317;
enum class Event { allocate, construct, acquire, retain, configure, free_raw, release, destroy };
enum class FailureStage { allocate, construct, acquire, configure };
struct TestFailure { FailureStage stage; };

struct Fixture {
    alignas(16) unsigned char storage[32]{};
    std::array<Event, 16> events{};
    std::size_t event_count = 0;
    int allocate_calls = 0, construct_calls = 0, acquire_calls = 0;
    int retain_calls = 0, configure_calls = 0, raw_frees = 0;
    int releases = 0, full_destructors = 0, references = 0;
    bool complete = false, bad_lifetime = false;
    bool null_allocate = false, throw_allocate = false, throw_construct = false;
    bool null_acquire = false, throw_acquire = false, throw_configure = false;
    int configure_status = success;

    void record(Event event) noexcept {
        if (event_count < events.size()) events[event_count++] = event;
        else bad_lifetime = true;
    }
    void* allocate() {
        ++allocate_calls; record(Event::allocate);
        if (throw_allocate) throw TestFailure{FailureStage::allocate};
        return null_allocate ? nullptr : storage;
    }
    void construct(void* pointer) {
        ++construct_calls; record(Event::construct);
        if (pointer != storage) bad_lifetime = true;
        if (throw_construct) throw TestFailure{FailureStage::construct};
        complete = true; // Model the native complete ctor's normal return, ref0.
    }
    void* acquire() {
        ++acquire_calls; record(Event::acquire);
        if (throw_acquire) throw TestFailure{FailureStage::acquire};
        if (null_acquire) return nullptr;
        complete = true;
        return storage;
    }
    void free_raw(void* pointer) noexcept {
        ++raw_frees; record(Event::free_raw);
        if (pointer != storage || complete || references != 0) bad_lifetime = true;
    }
    void retain(void* pointer) noexcept {
        ++retain_calls; record(Event::retain);
        if (pointer != storage || !complete || references != 0) bad_lifetime = true;
        ++references;
    }
    int configure(void* pointer) {
        ++configure_calls; record(Event::configure);
        if (pointer != storage || !complete || references != 1 || retain_calls != 1)
            bad_lifetime = true;
        if (throw_configure) throw TestFailure{FailureStage::configure};
        return configure_status;
    }
    void release(void* pointer) noexcept {
        ++releases; record(Event::release);
        if (pointer != storage || !complete || references != 1) bad_lifetime = true;
        --references;
        if (references == 0) {
            ++full_destructors; record(Event::destroy); complete = false;
        }
    }
    bool sequence(std::initializer_list<Event> expected) const noexcept {
        if (event_count != expected.size()) return false;
        std::size_t index = 0;
        for (Event event : expected) if (events[index++] != event) return false;
        return !bad_lifetime;
    }
};

int allocate_construct(Fixture& fixture, void*& output) {
    return pdx::owned_context::allocate_construct(
        [&]() { return fixture.allocate(); },
        [&](void* value) noexcept { fixture.free_raw(value); },
        [&](void* value) { fixture.construct(value); },
        [&](void* value) noexcept { fixture.retain(value); },
        [&](void* value) noexcept { fixture.release(value); },
        [&](void* value) { return fixture.configure(value); },
        success, no_memory, output);
}
int from_factory(Fixture& fixture, void*& output) {
    return pdx::owned_context::from_ref0_factory(
        [&]() { return fixture.acquire(); },
        [&](void* value) noexcept { fixture.retain(value); },
        [&](void* value) noexcept { fixture.release(value); },
        [&](void* value) { return fixture.configure(value); },
        success, no_memory, output);
}
int failures = 0;
void check(bool passed, const char* label) {
    std::printf("%s %s\n", passed ? "PASS" : "FAIL", label);
    if (!passed) ++failures;
}

void test_allocate_construct() {
    {
        Fixture fixture; fixture.null_allocate = true; void* output = nullptr;
        const auto status = allocate_construct(fixture, output);
        check(status == no_memory && output == nullptr && fixture.raw_frees == 0
            && fixture.construct_calls == 0 && fixture.retain_calls == 0
            && fixture.configure_calls == 0 && fixture.releases == 0
            && fixture.full_destructors == 0 && fixture.sequence({Event::allocate}),
            "null allocation has no construction or cleanup");
    }
    {
        Fixture fixture; fixture.throw_allocate = true; void* output = nullptr;
        bool caught = false;
        try { allocate_construct(fixture, output); }
        catch (const TestFailure& error) { caught = error.stage == FailureStage::allocate; }
        check(caught && output == nullptr && fixture.raw_frees == 0
            && fixture.releases == 0 && fixture.full_destructors == 0
            && fixture.sequence({Event::allocate}),
            "allocator exception has no unowned cleanup");
    }
    {
        Fixture fixture; fixture.throw_construct = true; void* output = nullptr;
        bool caught = false;
        try { allocate_construct(fixture, output); }
        catch (const TestFailure& error) { caught = error.stage == FailureStage::construct; }
        check(caught && output == nullptr && fixture.raw_frees == 1
            && fixture.retain_calls == 0 && fixture.configure_calls == 0
            && fixture.releases == 0 && fixture.full_destructors == 0
            && fixture.sequence({Event::allocate, Event::construct, Event::free_raw}),
            "constructor throw frees raw storage only, never full destructor");
    }
    {
        Fixture fixture; fixture.configure_status = config_error; void* output = nullptr;
        const auto status = allocate_construct(fixture, output);
        check(status == config_error && output == nullptr && fixture.raw_frees == 0
            && fixture.retain_calls == 1 && fixture.configure_calls == 1
            && fixture.releases == 1 && fixture.full_destructors == 1
            && fixture.references == 0 && fixture.sequence({Event::allocate,
                Event::construct, Event::retain, Event::configure, Event::release, Event::destroy}),
            "configuration status is preserved and releases one owned ref");
    }
    {
        Fixture fixture; fixture.throw_configure = true; void* output = nullptr;
        bool caught = false;
        try { allocate_construct(fixture, output); }
        catch (const TestFailure& error) { caught = error.stage == FailureStage::configure; }
        check(caught && output == nullptr && fixture.raw_frees == 0
            && fixture.releases == 1 && fixture.full_destructors == 1
            && fixture.references == 0 && fixture.sequence({Event::allocate,
                Event::construct, Event::retain, Event::configure, Event::release, Event::destroy}),
            "configuration exception releases one owned ref without raw free");
    }
    {
        Fixture fixture; void* output = nullptr;
        const auto status = allocate_construct(fixture, output);
        check(status == success && output == fixture.storage && fixture.raw_frees == 0
            && fixture.releases == 0 && fixture.full_destructors == 0
            && fixture.references == 1 && fixture.sequence({Event::allocate,
                Event::construct, Event::retain, Event::configure}),
            "normal constructor retains before configuration and transfers one ref");
        fixture.release(output); output = nullptr;
        check(fixture.raw_frees == 0 && fixture.releases == 1
            && fixture.full_destructors == 1 && fixture.references == 0
            && fixture.sequence({Event::allocate, Event::construct, Event::retain,
                Event::configure, Event::release, Event::destroy}),
            "successful transfer is not released again by an expired helper guard");
    }
}

void test_ref0_factory() {
    {
        Fixture fixture; fixture.null_acquire = true; void* output = nullptr;
        const auto status = from_factory(fixture, output);
        check(status == no_memory && output == nullptr && fixture.retain_calls == 0
            && fixture.configure_calls == 0 && fixture.releases == 0 && fixture.raw_frees == 0
            && fixture.full_destructors == 0 && fixture.sequence({Event::acquire}),
            "null ref0 factory result is not retained or released");
    }
    {
        Fixture fixture; fixture.throw_acquire = true; void* output = nullptr;
        bool caught = false;
        try { from_factory(fixture, output); }
        catch (const TestFailure& error) { caught = error.stage == FailureStage::acquire; }
        check(caught && output == nullptr && fixture.retain_calls == 0
            && fixture.configure_calls == 0 && fixture.releases == 0 && fixture.raw_frees == 0
            && fixture.full_destructors == 0 && fixture.sequence({Event::acquire}),
            "factory exception cannot clean an unreturned internal allocation");
    }
    {
        Fixture fixture; fixture.configure_status = config_error; void* output = nullptr;
        const auto status = from_factory(fixture, output);
        check(status == config_error && output == nullptr && fixture.raw_frees == 0
            && fixture.releases == 1 && fixture.full_destructors == 1
            && fixture.references == 0 && fixture.sequence({Event::acquire,
                Event::retain, Event::configure, Event::release, Event::destroy}),
            "factory configuration status is preserved and releases once");
    }
    {
        Fixture fixture; fixture.throw_configure = true; void* output = nullptr;
        bool caught = false;
        try { from_factory(fixture, output); }
        catch (const TestFailure& error) { caught = error.stage == FailureStage::configure; }
        check(caught && output == nullptr && fixture.raw_frees == 0
            && fixture.releases == 1 && fixture.full_destructors == 1
            && fixture.references == 0 && fixture.sequence({Event::acquire,
                Event::retain, Event::configure, Event::release, Event::destroy}),
            "factory configuration exception releases once");
    }
    {
        Fixture fixture; void* output = nullptr;
        const auto status = from_factory(fixture, output);
        check(status == success && output == fixture.storage && fixture.raw_frees == 0
            && fixture.releases == 0 && fixture.full_destructors == 0
            && fixture.references == 1 && fixture.sequence({Event::acquire,
                Event::retain, Event::configure}),
            "factory normal return retains before configuration and transfers one ref");
        fixture.release(output); output = nullptr;
        check(fixture.raw_frees == 0 && fixture.releases == 1
            && fixture.full_destructors == 1 && fixture.references == 0
            && fixture.sequence({Event::acquire, Event::retain, Event::configure,
                Event::release, Event::destroy}),
            "factory transfer has no duplicate helper release");
    }
}
} // namespace

int main() {
    try {
        test_allocate_construct();
        test_ref0_factory();
    } catch (...) {
        check(false, "unexpected CPU host exception");
    }
    std::printf("owned_context_host: %d failure(s)\n", failures);
    return failures ? 1 : 0;
}
