#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include "../v3_script_random_scope.h"
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <type_traits>
#include <vector>

using pdx_script_detail::RandomAllow;
using pdx_script_detail::RandomOperations;
using pdx_script_detail::RandomStorage40;
using pdx_script_detail::SourceLocation24;

namespace {
int checks = 0, failures = 0, cases = 0;
void check(bool ok, const char* expression, int line) {
    ++checks;
    if (!ok) { ++failures; std::printf("FAIL line%d: %s\n", line, expression); }
}
#define CHECK(x) check(!!(x), #x, __LINE__)

// Only the native generator fields consumed by the internal bridge are modeled.
// Counter/seed sentinels detect accidental reseeding or wrong owner offsets.
struct alignas(8) Generator {
    uintptr_t vtable = 0x4434be8;
    uint32_t counter = 0x15ba2;
    uint32_t seed = 0xc2dea4b9;
    volatile LONG owner = 0;
    uint32_t canary = 0x98765432;
};
static_assert(offsetof(Generator, counter) == 8);
static_assert(offsetof(Generator, seed) == 12);
static_assert(offsetof(Generator, owner) == 16);
static_assert(sizeof(RandomStorage40) == 40 && alignof(RandomStorage40) == 8);
static_assert(sizeof(SourceLocation24) == 24);
static_assert(offsetof(SourceLocation24, line) == 0);
static_assert(offsetof(SourceLocation24, column) == 4);
static_assert(offsetof(SourceLocation24, file) == 8);
static_assert(offsetof(SourceLocation24, function) == 16);
static_assert(offsetof(RandomStorage40, generator) == 0);
static_assert(offsetof(RandomStorage40, acquired) == 8);
static_assert(offsetof(RandomStorage40, destination) == 16);
static_assert(offsetof(RandomStorage40, previous) == 24);
static_assert(offsetof(RandomStorage40, restore) == 32);
static_assert(!std::is_copy_constructible_v<RandomAllow>);
static_assert(!std::is_move_constructible_v<RandomAllow>);
static_assert(std::is_nothrow_destructible_v<RandomAllow>);

enum class Mode { acquire, nested, foreign, zero_owner, throw_ctor,
                  wrong_return, invalid_acquired, wrong_generator_unacquired };
enum Event { construct_begin, construct_return, thread_checked, body_enter,
             dtor_begin, active_restored, owner_released, dtor_end };
struct PrefixCopy { RandomStorage40 value; };
struct Fixture {
    Generator generator;
    RandomStorage40 prior{};
    void* volatile active = &prior;
    uint32_t tid = 0x4321;
    Mode mode = Mode::acquire;
    int constructs = 0, destroys = 0, thread_checks = 0;
    RandomStorage40* last = nullptr;
    std::vector<Event> events;
    std::vector<PrefixCopy> destroyed;
    SourceLocation24 expected{69, 3, "fixture.cpp", "random_scope_host"};
};
Fixture* current = nullptr;

uint32_t thread_id() {
    ++current->thread_checks;
    current->events.push_back(thread_checked);
    return current->tid;
}
RandomStorage40* __fastcall construct(RandomStorage40* p, uintptr_t flags,
                                      const SourceLocation24* source) {
    Fixture& f = *current;
    ++f.constructs;
    f.last = p;
    f.events.push_back(construct_begin);
    CHECK(flags == 0); // The proved native LockCtor's second argument is zero.
    CHECK(source != nullptr);
    CHECK(source->line == f.expected.line && source->column == f.expected.column);
    CHECK(source->file == f.expected.file && source->function == f.expected.function);
    CHECK((reinterpret_cast<uintptr_t>(p) & 7) == 0);
    CHECK(p->generator == 0 && p->acquired == 0);
    CHECK(p->destination == 0 && p->previous == 0 && p->restore == 0);
    p->generator = reinterpret_cast<uintptr_t>(&f.generator);
    if (f.mode == Mode::nested) {
        CHECK(static_cast<uint32_t>(f.generator.owner) == f.tid);
        p->acquired = 0;
    } else if (f.mode == Mode::foreign) {
        CHECK(f.generator.owner != 0);
        CHECK(static_cast<uint32_t>(f.generator.owner) != f.tid);
        p->acquired = 0;
    } else if (f.mode == Mode::zero_owner) {
        CHECK(f.generator.owner == 0);
        p->acquired = 0;
    } else if (f.mode == Mode::wrong_generator_unacquired) {
        p->generator = reinterpret_cast<uintptr_t>(&f.prior);
        p->acquired = 0;
    } else {
        CHECK(f.generator.owner == 0);
        InterlockedExchange(&f.generator.owner, static_cast<LONG>(f.tid));
        p->acquired = f.mode == Mode::invalid_acquired ? 2 : 1;
    }
    if (f.mode == Mode::throw_ctor) throw std::runtime_error("constructor did not return");
    f.events.push_back(construct_return);
    return f.mode == Mode::wrong_return ? &f.prior : p;
}
void __fastcall destroy(RandomStorage40* p) {
    Fixture& f = *current;
    ++f.destroys;
    f.events.push_back(dtor_begin);
    f.destroyed.push_back({*p});
    CHECK(p->generator == reinterpret_cast<uintptr_t>(&f.generator));
    CHECK(p->destination == reinterpret_cast<uintptr_t>(&f.active));
    CHECK(p->restore == 1);
    CHECK(f.active == p);
    // Emulate the original full destructor, using actual Windows atomics.
    if (p->restore) {
        InterlockedExchangePointer(reinterpret_cast<void* volatile*>(p->destination),
                                   reinterpret_cast<void*>(p->previous));
        f.events.push_back(active_restored);
        p->restore = 0;
    }
    if (p->acquired) {
        const auto removed = static_cast<uint32_t>(InterlockedExchange(&f.generator.owner, 0));
        CHECK(removed == f.tid);
        p->acquired = 0;
        f.events.push_back(owner_released);
    }
    f.events.push_back(dtor_end);
}
RandomOperations operations(Fixture& f) {
    current = &f;
    return {reinterpret_cast<uintptr_t>(&f.generator), &f.active, construct, destroy, thread_id};
}
void unchanged_seed(const Fixture& f, uint32_t counter = 0x15ba2) {
    CHECK(f.generator.vtable == 0x4434be8);
    CHECK(f.generator.counter == counter);
    CHECK(f.generator.seed == 0xc2dea4b9);
    CHECK(f.generator.canary == 0x98765432);
}
void case_name(const char* name) { ++cases; std::printf("CASE %s\n", name); }

void invalid_operations() {
    case_name("N_invalid_operations_no_ctor_no_dtor");
    for (int missing = 0; missing != 5; ++missing) {
        Fixture f;
        auto op = operations(f);
        if (missing == 0) op.generator = 0;
        if (missing == 1) op.active = nullptr;
        if (missing == 2) op.construct = nullptr;
        if (missing == 3) op.destroy = nullptr;
        if (missing == 4) op.thread_id = nullptr;
        {
            RandomAllow scope(op, f.expected);
            CHECK(scope.error() == ERROR_INVALID_PARAMETER);
            CHECK(!scope.published());
            scope.publish();
            CHECK(!scope.published());
        }
        CHECK(f.constructs == 0 && f.destroys == 0 && f.thread_checks == 0);
        CHECK(f.active == &f.prior && f.generator.owner == 0);
        unchanged_seed(f);
    }
}
void constructor_throw() {
    case_name("N_ctor_did_not_return_no_adapter_cleanup");
    Fixture f; auto op = operations(f); f.mode = Mode::throw_ctor;
    bool caught = false;
    try { RandomAllow scope(op, f.expected); CHECK(false); }
    catch (const std::runtime_error&) { caught = true; }
    CHECK(caught && f.constructs == 1 && f.destroys == 0 && f.thread_checks == 0);
    CHECK(f.active == &f.prior);
    CHECK(static_cast<uint32_t>(f.generator.owner) == f.tid);
    unchanged_seed(f);
    // Fixture housekeeping only. Adapter must not clean a constructor that did not return.
    InterlockedExchange(&f.generator.owner, 0);
}
void lock_only_normal() {
    case_name("L_normal_release_owner_without_active_exchange");
    Fixture f; auto op = operations(f);
    {
        RandomAllow scope(op, f.expected);
        CHECK(scope.error() == ERROR_SUCCESS && !scope.published());
        CHECK(f.active == &f.prior);
        CHECK(f.last->destination == 0 && f.last->previous == 0 && f.last->restore == 0);
        CHECK(static_cast<uint32_t>(f.generator.owner) == f.tid);
    }
    CHECK(f.destroys == 0 && f.generator.owner == 0 && f.active == &f.prior);
    CHECK(f.thread_checks == 2);
    unchanged_seed(f);
}
void lock_only_throw() {
    case_name("L_post_ctor_throw_inline_cleanup_not_full_dtor");
    Fixture f; auto op = operations(f); bool caught = false;
    try {
        RandomAllow scope(op, f.expected);
        CHECK(scope.error() == ERROR_SUCCESS);
        throw std::runtime_error("before publish");
    } catch (const std::runtime_error&) { caught = true; }
    CHECK(caught && f.destroys == 0 && f.generator.owner == 0);
    CHECK(f.active == &f.prior);
    unchanged_seed(f);
}
void published_normal(bool zero_prior) {
    case_name(zero_prior ? "P_null_previous_exact_restore" : "P_nonnull_previous_order_and_once");
    Fixture f; auto op = operations(f);
    if (zero_prior) f.active = nullptr;
    void* const before = f.active;
    {
        RandomAllow scope(op, f.expected);
        CHECK(scope.error() == ERROR_SUCCESS && !scope.published());
        CHECK(f.active == before && f.destroys == 0);
        scope.publish();
        CHECK(scope.published());
        CHECK(f.active == f.last);
        CHECK(f.last->destination == reinterpret_cast<uintptr_t>(&f.active));
        CHECK(f.last->previous == reinterpret_cast<uintptr_t>(before));
        CHECK(f.last->restore == 1);
        const auto previous = f.last->previous;
        scope.publish();
        CHECK(scope.published() && f.active == f.last && f.last->previous == previous);
        f.events.push_back(body_enter);
    }
    CHECK(f.active == before && f.generator.owner == 0 && f.destroys == 1);
    CHECK(f.destroyed.size() == 1 && f.destroyed[0].value.previous == reinterpret_cast<uintptr_t>(before));
    const std::vector<Event> expected{construct_begin, construct_return, thread_checked,
        body_enter, dtor_begin, active_restored, owner_released, dtor_end};
    CHECK(f.events == expected);
    unchanged_seed(f);
}
void published_throw() {
    case_name("P_body_throw_original_full_dtor_once");
    Fixture f; auto op = operations(f); bool caught = false;
    try {
        RandomAllow scope(op, f.expected);
        CHECK(scope.error() == ERROR_SUCCESS);
        scope.publish();
        CHECK(f.active == f.last);
        throw std::runtime_error("inside published body");
    } catch (const std::runtime_error&) { caught = true; }
    CHECK(caught && f.active == &f.prior && f.generator.owner == 0 && f.destroys == 1);
    unchanged_seed(f);
}
void nested(bool publish_inner) {
    case_name(publish_inner ? "P_same_tid_nested_restore_outer_then_prior" : "L_same_tid_nested_throw_no_release_outer");
    Fixture f; auto op = operations(f); RandomStorage40* outer_prefix = nullptr;
    {
        RandomAllow outer(op, f.expected);
        CHECK(outer.error() == ERROR_SUCCESS);
        outer.publish(); outer_prefix = f.last;
        CHECK(f.active == outer_prefix && outer_prefix->acquired == 1);
        f.mode = Mode::nested;
        try {
            RandomAllow inner(op, f.expected);
            CHECK(inner.error() == ERROR_SUCCESS && !inner.published());
            CHECK(f.last != outer_prefix && f.last->acquired == 0);
            CHECK(f.active == outer_prefix);
            if (publish_inner) {
                inner.publish();
                CHECK(f.active == f.last && f.last->previous == reinterpret_cast<uintptr_t>(outer_prefix));
            } else throw std::runtime_error("nested lock-only");
        } catch (const std::runtime_error&) { CHECK(!publish_inner); }
        CHECK(f.active == outer_prefix && static_cast<uint32_t>(f.generator.owner) == f.tid);
        CHECK(f.destroys == (publish_inner ? 1 : 0));
    }
    CHECK(f.active == &f.prior && f.generator.owner == 0);
    CHECK(f.destroys == (publish_inner ? 2 : 1));
    if (publish_inner) {
        CHECK(f.destroyed[0].value.acquired == 0);
        CHECK(f.destroyed[0].value.previous == reinterpret_cast<uintptr_t>(outer_prefix));
        CHECK(f.destroyed[1].value.acquired == 1);
    }
    unchanged_seed(f);
}
void foreign_nonnull() {
    case_name("foreign_acquired0_nonnull_active_is_not_authorization");
    Fixture f; auto op = operations(f); f.mode = Mode::foreign;
    f.generator.owner = static_cast<LONG>(f.tid + 1);
    {
        RandomAllow scope(op, f.expected);
        CHECK(scope.error() == ERROR_BUSY && !scope.published());
        CHECK(f.last->acquired == 0 && f.active == &f.prior);
        scope.publish();
        CHECK(!scope.published() && f.active == &f.prior);
    }
    CHECK(static_cast<uint32_t>(f.generator.owner) == f.tid + 1);
    CHECK(f.destroys == 0 && f.active == &f.prior);
    unchanged_seed(f);
}
void zero_owner() {
    case_name("zero_owner_acquired0_cannot_publish");
    Fixture f; auto op = operations(f); f.mode = Mode::zero_owner;
    { RandomAllow scope(op, f.expected); CHECK(scope.error() == ERROR_BUSY);
      scope.publish(); CHECK(!scope.published()); }
    CHECK(f.generator.owner == 0 && f.active == &f.prior && f.destroys == 0);
    unchanged_seed(f);
}
void invalid_return_or_flag(Mode mode) {
    case_name(mode == Mode::wrong_return ? "L_wrong_return_valid_owned_prefix_cleanup" : "L_invalid_acquired_value_no_publish");
    Fixture f; auto op = operations(f); f.mode = mode;
    { RandomAllow scope(op, f.expected); CHECK(scope.error() == ERROR_INVALID_DATA);
      scope.publish(); CHECK(!scope.published()); }
    CHECK(f.active == &f.prior && f.generator.owner == 0 && f.destroys == 0);
    unchanged_seed(f);
}
void wrong_generator_unacquired() {
    case_name("unacquired_mismatched_generator_no_foreign_release");
    Fixture f; auto op = operations(f); f.mode = Mode::wrong_generator_unacquired;
    { RandomAllow scope(op, f.expected); CHECK(scope.error() == ERROR_INVALID_DATA);
      scope.publish(); CHECK(!scope.published()); }
    CHECK(f.active == &f.prior && f.generator.owner == 0 && f.destroys == 0);
    unchanged_seed(f);
}
void no_seed_reset() {
    case_name("native_counter_advance_preserved_no_seed_reset");
    Fixture f; auto op = operations(f);
    { RandomAllow scope(op, f.expected); CHECK(scope.error() == ERROR_SUCCESS);
      scope.publish(); ++f.generator.counter; }
    unchanged_seed(f, 0x15ba3);
    CHECK(f.active == &f.prior && f.generator.owner == 0 && f.destroys == 1);
}
[[noreturn]] void expected_terminate() noexcept {
    std::puts("EXPECTED_TERMINATE_INVARIANT_NO_CONTINUATION");
    std::fflush(stdout);
    ExitProcess(91);
}
int fatal_case(const char* which) {
    Fixture f; auto op = operations(f);
    std::set_terminate(expected_terminate);
    {
        RandomAllow scope(op, f.expected);
        CHECK(scope.error() == ERROR_SUCCESS && !scope.published());
        if (!std::strcmp(which, "--fatal-owner")) f.generator.owner = static_cast<LONG>(f.tid + 1);
        else f.last->generator = reinterpret_cast<uintptr_t>(&f.prior);
    }
    std::puts("UNREACHABLE_AFTER_OWNER_INVARIANT");
    return 3;
}
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && (!std::strcmp(argv[1], "--fatal-owner") || !std::strcmp(argv[1], "--fatal-generator")))
        return fatal_case(argv[1]);
    invalid_operations(); constructor_throw(); lock_only_normal(); lock_only_throw();
    published_normal(false); published_normal(true); published_throw();
    nested(true); nested(false); foreign_nonnull(); zero_owner();
    invalid_return_or_flag(Mode::wrong_return); invalid_return_or_flag(Mode::invalid_acquired);
    wrong_generator_unacquired(); no_seed_reset();
    std::printf("random_scope_host cases=%d checks=%d failures=%d; MOCK_ONLY_NO_GAME\n", cases, checks, failures);
    return failures ? 1 : 0;
}
