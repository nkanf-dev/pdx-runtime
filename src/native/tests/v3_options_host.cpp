// Single-TU MSVC x64 fixture. Test mocks only; this is NOT actual V3 execution.
// Compile this file alone: /O2 /W4 /EHsc /MD. No game process is used.
#include <Windows.h>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <new>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#define PDX_V3_OPTIONS_BUILD
#define PDX_V3_ACCESS_BUILD
static HMODULE WINAPI FixtureGetModuleHandleW(LPCWSTR name);
// Windows.h is already included: only the included product implementation sees
// this redirect. The DLL and the installed game are never changed.
#define GetModuleHandleW FixtureGetModuleHandleW
#include "../v3_options.cpp"
#include "../v3_access.cpp"
#undef GetModuleHandleW

namespace fixture {
int failures = 0;
int checks = 0;
void check(bool value, const char* text) {
    ++checks;
    if (!value) ++failures;
    std::printf("%s %s\n", value ? "PASS" : "FAIL", text);
}

// Genuine native CString32 shape, not std::string and not a pointer-only blob.
// Allocation is owned by THIS host's mocked native functions, never by the
// adapter. Long UTF-8 payloads must be deep-copied and individually released.
struct CString32 {
    union { char local[16]; char* pointer; } value;
    uint64_t length;
    uint64_t capacity;
};
struct Settings112 {
    unsigned char prefix[0x28];
    CString32 name;
    CString32 password;
    unsigned char trailing[8];
};
struct Native224 {
    uintptr_t* table;
    Settings112 settings;
    unsigned char settings_live;
    unsigned char derived[103];
};
static_assert(sizeof(CString32) == 32);
static_assert(offsetof(CString32, length) == 16);
static_assert(offsetof(CString32, capacity) == 24);
static_assert(sizeof(Settings112) == 112);
static_assert(offsetof(Settings112, name) == 0x28);
static_assert(offsetof(Settings112, password) == 0x48);
static_assert(offsetof(Settings112, trailing) == 0x68);
static_assert(sizeof(Native224) == 224);
static_assert(offsetof(Native224, settings) == 8);
static_assert(offsetof(Native224, settings_live) == 0x78);

const char source_name[] = "source-owned long UTF-8 name "
    "\xE6\xB5\x8B\xE8\xAF\x95 / \xC3\xA9 / "
    "independent allocation beyond the sixteen-byte inline storage";
const char source_password[] = "source-owned long UTF-8 password "
    "\xE5\xAF\x86\xE7\xA0\x81 / \xF0\x9F\x8C\x8D / "
    "a different independent allocation beyond inline storage";
const char destination_name[] = "destination-owned long UTF-8 name "
    "\xE5\x88\x9D\xE5\xA7\x8B / never borrowed from the source allocation";
const char destination_password[] = "destination-owned long UTF-8 password "
    "\xE7\x8B\xAC\xE7\xAB\x8B / preserved if second assignment throws";

std::unordered_set<char*> live_strings;
std::unordered_map<Native224*, unsigned> live_owners;
std::vector<unsigned> destruction_counts(1, 0);
unsigned factory_calls = 0;
unsigned assign_calls = 0;
unsigned destructor_calls = 0;
unsigned clone_calls = 0;
unsigned string_releases = 0;
unsigned factory_profile = 0; // 0: actual default-shaped empty; 1/2: owned long strings.
bool factory_return_null = false;
bool factory_throw_after_publish = false;
bool destructor_throw_after_release = false;
bool assignment_throw_after_first = false;
bool factory_marker_fault = false; // Host-only fault injection, NOT native factory behavior.
bool require_scope = false;
bool scope_active = false;
unsigned boundary_scope_failures = 0;
DWORD scope_thread = 0;
unsigned enters = 0;
unsigned leaves = 0;
uint32_t expected_mode = 0;
int32_t expected_flag = 0;
const char* expected_source = nullptr;
void* last_scope_storage = nullptr;
unsigned char* fake_image = nullptr;
unsigned image_calls = 0;

void native_boundary() {
    if (require_scope && (!scope_active || scope_thread != GetCurrentThreadId()))
        ++boundary_scope_failures;
}
const char* text(const CString32& s) {
    return s.capacity > 15 ? s.value.pointer : s.value.local;
}
bool equals(const CString32& s, const char* expected) {
    return s.length == std::strlen(expected)
        && std::memcmp(text(s), expected, static_cast<size_t>(s.length) + 1) == 0;
}
void construct_string(CString32& s, const char* input) {
    std::memset(&s, 0, sizeof(s));
    s.length = std::strlen(input);
    s.capacity = 15;
    if (s.length <= 15) {
        std::memcpy(s.value.local, input, static_cast<size_t>(s.length) + 1);
    } else {
        s.capacity = s.length;
        s.value.pointer = new char[static_cast<size_t>(s.length) + 1];
        std::memcpy(s.value.pointer, input, static_cast<size_t>(s.length) + 1);
        live_strings.insert(s.value.pointer);
    }
}
void release_string(CString32& s) {
    if (s.capacity > 15) {
        const auto erased = live_strings.erase(s.value.pointer);
        check(erased == 1, "mock native CString releases its own allocation exactly once");
        if (erased == 1) { delete[] s.value.pointer; ++string_releases; }
    }
    std::memset(&s, 0, sizeof(s));
    s.capacity = 15;
}
void assign_string(CString32& dst, const CString32& src) {
    if (&dst == &src) return;
    CString32 replacement{};
    construct_string(replacement, text(src));
    release_string(dst);
    dst = replacement; // Test-owned allocation transfer, not settings bytecopy.
}
void release_owned_storage(Native224* p) {
    release_string(p->settings.password);
    release_string(p->settings.name);
    p->settings_live = 0;
    live_owners.erase(p);
    delete p;
}
void reclaim_unknown_only_for_host_teardown(uint64_t address) {
    // Mock publication is completely known to this fixture. Product code must
    // NOT replay a destructor on an UNKNOWN owner; direct test-memory teardown
    // avoids doing that while also leaving no host allocations behind.
    auto* p = reinterpret_cast<Native224*>(address);
    if (live_owners.find(p) != live_owners.end()) release_owned_storage(p);
}

void* __fastcall clone_base128(void*, uint32_t) {
    ++clone_calls;
    check(false, "base128 clone slot0 must never be called");
    throw 128;
}
void* __fastcall scalar_destroy(void* object, uint32_t flags) {
    native_boundary();
    ++destructor_calls;
    auto* p = static_cast<Native224*>(object);
    check(flags == 1, "native scalar destructor slot1 receives deleting flag1");
    const auto found = live_owners.find(p);
    check(found != live_owners.end(), "destructor receives a complete factory-owned224 object");
    if (found == live_owners.end()) throw 224;
    ++destruction_counts[found->second];
    release_owned_storage(p);
    if (destructor_throw_after_release) throw 571;
    return nullptr;
}
uintptr_t options_table[] = {
    reinterpret_cast<uintptr_t>(&clone_base128),
    reinterpret_cast<uintptr_t>(&scalar_destroy),
};
void* __fastcall native_factory11394c0(void** out) {
    native_boundary();
    ++factory_calls;
    if (factory_return_null) { *out = nullptr; return out; }
    auto* p = new Native224{};
    p->table = options_table;
    const unsigned id = static_cast<unsigned>(destruction_counts.size());
    destruction_counts.push_back(0);
    live_owners.emplace(p, id);
    for (size_t i = 0; i != sizeof(p->settings.prefix); ++i)
        p->settings.prefix[i] = static_cast<unsigned char>(id * 19 + i);
    for (size_t i = 0; i != sizeof(p->settings.trailing); ++i)
        p->settings.trailing[i] = static_cast<unsigned char>(id * 23 + i);
    construct_string(p->settings.name, factory_profile == 1 ? source_name
        : factory_profile == 2 ? destination_name : "");
    construct_string(p->settings.password, factory_profile == 1 ? source_password
        : factory_profile == 2 ? destination_password : "");
    p->settings_live = factory_marker_fault ? 0 : 1;
    for (size_t i = 0; i != sizeof(p->derived); ++i)
        p->derived[i] = static_cast<unsigned char>(id * 31 + i);
    *out = p; // Deliberately publish before the configured construction exception.
    if (factory_throw_after_publish) throw 113;
    return out;
}
void* __fastcall native_assignment2165cc0(void* destination, const void* source_settings) {
    native_boundary();
    ++assign_calls;
    auto* dst = static_cast<Settings112*>(destination);
    const auto* src = static_cast<const Settings112*>(source_settings);
    // Exact saved2165cc0 instruction ranges. Do NOT copy seven prefix padding
    // bytes0x21..0x27 or trailing padding byte0x6B. No scalar setter is exposed.
    std::memmove(dst->prefix, src->prefix, 0x20);
    dst->prefix[0x20] = src->prefix[0x20];
    assign_string(dst->name, src->name);
    if (assignment_throw_after_first) throw 216;
    assign_string(dst->password, src->password);
    std::memmove(dst->trailing, src->trailing, 3);
    std::memmove(dst->trailing + 4, src->trailing + 4, 4);
    return destination;
}

bool install_jump(size_t rva, uintptr_t target_address) {
    auto* address = fake_image + rva;
    auto* page = reinterpret_cast<void*>(reinterpret_cast<uintptr_t>(address) & ~uintptr_t(0xFFF));
    if (!VirtualAlloc(page, 0x1000, MEM_COMMIT, PAGE_READWRITE)) return false;
    // Win64 absolute indirect jump preserves every argument register/stack slot.
    const unsigned char prefix[] = {0xFF, 0x25, 0, 0, 0, 0};
    std::memcpy(address, prefix, sizeof(prefix));
    std::memcpy(address + sizeof(prefix), &target_address, sizeof(target_address));
    DWORD previous = 0;
    return VirtualProtect(page, 0x1000, PAGE_EXECUTE_READ, &previous)
        && FlushInstructionCache(GetCurrentProcess(), page, 0x1000);
}
bool initialize_image() {
    fake_image = static_cast<unsigned char*>(VirtualAlloc(nullptr, 0x2167000,
        MEM_RESERVE, PAGE_NOACCESS));
    return fake_image && install_jump(0x11394c0, reinterpret_cast<uintptr_t>(&native_factory11394c0))
        && install_jump(0x2165cc0, reinterpret_cast<uintptr_t>(&native_assignment2165cc0));
}
Native224* object(uint64_t owner) { return reinterpret_cast<Native224*>(owner); }
unsigned identity(uint64_t owner) { return live_owners.at(object(owner)); }
uint64_t create(unsigned profile) {
    factory_profile = profile;
    uint64_t owner = 0;
    check(PdxV3OptionsCreate(&owner) == ERROR_SUCCESS && owner != 0,
        "owner is produced by actual adapter calling mock native224 factory");
    return owner;
}
uint64_t settings(uint64_t owner) {
    uint64_t address = 0;
    check(PdxV3OptionsSettingsAddress(owner, &address) == ERROR_SUCCESS && address == owner + 8,
        "settings112 is borrowed from the constructed factory224 owner+8");
    return address;
}
PdxV3OptionsRequest options_request(uint32_t action, uint64_t owner = 0, uint64_t source_address = 0) {
    PdxV3OptionsRequest r{};
    r.size = sizeof(r);
    r.error = ERROR_IO_PENDING;
    r.action = action;
    r.options = owner;
    r.source_settings = source_address;
    r.operation_options = 0xBADC0FFEE; // Verify callback resets this diagnostic output.
    r.reserved0 = 0x12345678;
    r.reserved1 = 0x87654321;
    return r;
}
void* WINAPI mock_enter(void* storage, uint32_t mode, int32_t flag, const char* source_label) {
    check(!scope_active && reinterpret_cast<uintptr_t>(storage) % 16 == 0
        && mode == expected_mode && flag == expected_flag && source_label == expected_source,
        "access scope keeps aligned80 storage and exact raw mode/flag/source");
    std::memset(storage, 0xA7, 80);
    scope_active = true;
    scope_thread = GetCurrentThreadId();
    last_scope_storage = storage;
    ++enters;
    return storage;
}
void WINAPI mock_leave(void* storage) {
    const auto* bytes = static_cast<const unsigned char*>(storage);
    check(scope_active && scope_thread == GetCurrentThreadId() && storage == last_scope_storage
        && bytes[0] == 0xA7 && bytes[79] == 0xA7,
        "same-thread native scope leaves once with the same live80 storage");
    scope_active = false;
    ++leaves;
}
PdxV3AccessRequest scoped(PdxV3OptionsRequest& inner) {
    PdxV3AccessRequest r{};
    r.size = sizeof(r);
    r.error = ERROR_IO_PENDING;
    r.mode = expected_mode = 0xFEDCBA98;
    r.flag = expected_flag = -17;
    expected_source = "pdx-runtime/options-owned-host";
    r.callback = reinterpret_cast<uint64_t>(&PdxV3OptionsCallback);
    r.opaque = reinterpret_cast<uint64_t>(&inner);
    r.source = reinterpret_cast<uint64_t>(expected_source);
    r.enter = reinterpret_cast<uint64_t>(&mock_enter);
    r.leave = reinterpret_cast<uint64_t>(&mock_leave);
    return r;
}
void invoke_scoped(PdxV3OptionsRequest& inner, PdxV3AccessRequest& outer) {
    const auto before_enters = enters;
    const auto before_leaves = leaves;
    void* const context = reinterpret_cast<void*>(uintptr_t(0x12345678));
    require_scope = true;
    PdxV3AccessCallback(context, &outer);
    require_scope = false;
    check(outer.error == ERROR_SUCCESS && outer.context == reinterpret_cast<uint64_t>(context)
        && outer.entered == 1 && outer.left == 1 && outer.enter_result != 0
        && outer.execution_thread_id == GetCurrentThreadId()
        && enters == before_enters + 1 && leaves == before_leaves + 1 && !scope_active,
        "outer access completion metadata remains independent of inner options error");
    check(inner.started == 1 && inner.execution_thread_id == GetCurrentThreadId()
        && inner.operation_options != 0xBADC0FFEE && inner.reserved0 == 0x12345678
        && inner.reserved1 == 0x87654321 && outer.opaque == reinterpret_cast<uint64_t>(&inner),
        "inner options operation/thread/reserved metadata and reset trace survive scoped composition");
}
void destroy(uint64_t& owner) {
    check(PdxV3OptionsDestroy(&owner) == ERROR_SUCCESS && owner == 0,
        "adapter clears owner and calls derived scalar destructor slot1");
}

void test_nulls_and_guards() {
    const auto before_factory = factory_calls;
    const auto before_assign = assign_calls;
    const auto before_destroy = destructor_calls;
    uint64_t zero = 0;
    uint64_t occupied = 1;
    uint64_t output = 0xABC;
    check(PdxV3OptionsCreate(nullptr) == ERROR_INVALID_PARAMETER
        && PdxV3OptionsCreate(&occupied) == ERROR_INVALID_PARAMETER && occupied == 1,
        "Create rejects null output and refuses to overwrite an occupied pointer cell");
    check(PdxV3OptionsDestroy(nullptr) == ERROR_INVALID_PARAMETER
        && PdxV3OptionsDestroy(&zero) == ERROR_INVALID_PARAMETER && zero == 0,
        "Destroy rejects ordinary null inputs without destructor dispatch");
    check(PdxV3OptionsSettingsAddress(0, &output) == ERROR_INVALID_PARAMETER && output == 0xABC
        && PdxV3OptionsSettingsAddress(1, nullptr) == ERROR_INVALID_PARAMETER,
        "SettingsAddress rejects ordinary null owner/output inputs");
    check(PdxV3OptionsCopySettings(0, 1) == ERROR_INVALID_PARAMETER
        && PdxV3OptionsCopySettings(1, 0) == ERROR_INVALID_PARAMETER,
        "CopySettings rejects null destination or native source");
    check(PdxV3OptionsCreateFromSettings(0, &zero) == ERROR_INVALID_PARAMETER
        && PdxV3OptionsCreateFromSettings(1, nullptr) == ERROR_INVALID_PARAMETER
        && PdxV3OptionsCreateFromSettings(1, &occupied) == ERROR_INVALID_PARAMETER
        && PdxV3OptionsCreateFromSettings(1, &zero, &zero) == ERROR_INVALID_PARAMETER
        && zero == 0 && occupied == 1,
        "CreateFromSettings rejects null source/output and occupied owner");
    PdxV3OptionsCallback(nullptr, nullptr);
    auto malformed = options_request(PDX_V3_OPTIONS_CREATE);
    malformed.size -= 1;
    malformed.started = 99;
    malformed.ownership = 88;
    PdxV3OptionsCallback(nullptr, &malformed);
    check(malformed.error == ERROR_INVALID_PARAMETER && malformed.started == 99
        && malformed.ownership == 88, "wrong request size rejects before native work or metadata reset");
    auto unknown = options_request(0xFFFFFFFF);
    PdxV3OptionsCallback(nullptr, &unknown);
    check(unknown.error == ERROR_INVALID_PARAMETER && unknown.started == 0
        && unknown.ownership == PDX_V3_OPTIONS_NONE, "unknown action records validation error without starting");
    check(factory_calls == before_factory && assign_calls == before_assign
        && destructor_calls == before_destroy, "ordinary invalid inputs perform no native calls");

    factory_return_null = true;
    check(PdxV3OptionsCreate(&zero) == ERROR_NOT_ENOUGH_MEMORY && zero == 0,
        "native factory null result reports allocation failure without an owner");
    factory_return_null = false;
    uint64_t owner = create(0);
    const uint64_t body = settings(owner);
    object(owner)->settings_live = 0; // Host-only guard-negative input, not a product setter.
    output = 0xABC;
    const auto guard_assigns = assign_calls;
    check(PdxV3OptionsSettingsAddress(owner, &output) == ERROR_INVALID_STATE && output == 0
        && PdxV3OptionsCopySettings(owner, body) == ERROR_INVALID_STATE
        && assign_calls == guard_assigns, "unconstructed-settings guard returns invalid state before assignment");
    object(owner)->settings_live = 1;
    destroy(owner);
}

void test_default_self_and_deep_copy() {
    uint64_t defaults = create(0);
    auto* d = object(defaults);
    check(d->settings_live == 1 && d->settings.name.capacity == 15 && d->settings.password.capacity == 15
        && equals(d->settings.name, "") && equals(d->settings.password, "")
        && sizeof(*d) == 224 && d->derived[0] != 0 && d->derived[102] != 0,
        "default factory constructs both empty CString32 fields and full224 derived tail");
    const Native224 default_snapshot = *d;
    check(PdxV3OptionsCopySettings(defaults, settings(defaults)) == ERROR_SUCCESS
        && std::memcmp(&default_snapshot, d, sizeof(*d)) == 0,
        "default self-assignment preserves every byte without cloning");
    uint64_t default_copy = 0;
    factory_profile = 0;
    check(PdxV3OptionsCreateFromSettings(settings(defaults), &default_copy) == ERROR_SUCCESS
        && equals(object(default_copy)->settings.name, "")
        && equals(object(default_copy)->settings.password, ""),
        "CreateFromSettings accepts a real default factory settings112 source");
    destroy(default_copy);
    destroy(defaults);

    uint64_t source_owner = create(1);
    uint64_t destination_owner = create(2);
    const unsigned source_id = identity(source_owner);
    const unsigned destination_id = identity(destination_owner);
    auto* src = object(source_owner);
    auto* dst = object(destination_owner);
    const Native224 before = *dst;
    const char* source_first = text(src->settings.name);
    const char* source_second = text(src->settings.password);
    const unsigned releases_before = string_releases;
    check(src->settings.name.capacity > 15 && src->settings.password.capacity > 15
        && source_first != source_second && live_strings.size() == 4,
        "both source and destination are full factory owners with two independent long UTF8 CString32 fields");
    check(PdxV3OptionsCopySettings(destination_owner, settings(source_owner)) == ERROR_SUCCESS,
        "actual adapter dispatches native2165cc0 settings assignment");
    check(equals(dst->settings.name, source_name) && equals(dst->settings.password, source_password)
        && text(dst->settings.name) != source_first && text(dst->settings.password) != source_second
        && text(dst->settings.name) != text(dst->settings.password)
        && string_releases == releases_before + 2 && live_strings.size() == 4,
        "assignment deeply owns both long UTF8 copies and releases only old destination strings");
    check(std::memcmp(dst->settings.prefix, src->settings.prefix, 0x20) == 0
        && dst->settings.prefix[0x20] == src->settings.prefix[0x20]
        && std::memcmp(dst->settings.prefix + 0x21, before.settings.prefix + 0x21, 7) == 0
        && std::memcmp(dst->settings.trailing, src->settings.trailing, 3) == 0
        && dst->settings.trailing[3] == before.settings.trailing[3]
        && std::memcmp(dst->settings.trailing + 4, src->settings.trailing + 4, 4) == 0,
        "exact2165cc0 prefix/trailing bytes copied; native padding holes remain destination-owned");
    check(dst->table == before.table && dst->settings_live == before.settings_live
        && std::memcmp(dst->derived, before.derived, sizeof(dst->derived)) == 0,
        "base settings assignment leaves complete derived224 fields and vtable unchanged");
    const Native224 self_snapshot = *dst;
    const auto self_releases = string_releases;
    check(PdxV3OptionsCopySettings(destination_owner, settings(destination_owner)) == ERROR_SUCCESS
        && std::memcmp(&self_snapshot, dst, sizeof(*dst)) == 0 && string_releases == self_releases,
        "long-string selfcopy preserves ownership, pointers, bytes and allocation lifetimes");
    destroy(source_owner);
    check(destruction_counts[source_id] == 1 && destruction_counts[destination_id] == 0
        && equals(dst->settings.name, source_name) && equals(dst->settings.password, source_password)
        && live_strings.size() == 2, "destination remains valid after source owner releases both strings");
    destroy(destination_owner);
    check(destruction_counts[destination_id] == 1 && live_strings.empty(),
        "destination releases its own two deep copies without source replay");

    source_owner = create(1);
    const unsigned retained_source_id = identity(source_owner);
    uint64_t fresh = 0;
    factory_profile = 2;
    const auto factories_before = factory_calls;
    check(PdxV3OptionsCreateFromSettings(settings(source_owner), &fresh) == ERROR_SUCCESS
        && fresh != source_owner && factory_calls == factories_before + 1
        && object(fresh)->settings_live == 1 && equals(object(fresh)->settings.name, source_name)
        && equals(object(fresh)->settings.password, source_password),
        "CreateFromSettings creates full224 via factory then deeply assigns a borrowed native body");
    const unsigned fresh_id = identity(fresh);
    destroy(fresh);
    check(destruction_counts[fresh_id] == 1 && destruction_counts[retained_source_id] == 0
        && equals(object(source_owner)->settings.name, source_name)
        && equals(object(source_owner)->settings.password, source_password) && live_strings.size() == 2,
        "source remains valid after copied destination is destroyed first");
    destroy(source_owner);
    check(clone_calls == 0, "no operation calls inherited base128 clone slot0");
}

void test_publication_and_destroy_exceptions() {
    factory_profile = 1;
    factory_throw_after_publish = true;
    uint64_t raw = 0;
    bool caught = false;
    const auto destruction_before = destructor_calls;
    try { PdxV3OptionsCreate(&raw); } catch (...) { caught = true; }
    check(caught && raw != 0 && live_owners.find(object(raw)) != live_owners.end()
        && destructor_calls == destruction_before,
        "raw Create exception propagates and preserves published pointer without guessed cleanup");
    reclaim_unknown_only_for_host_teardown(raw);
    auto published = options_request(PDX_V3_OPTIONS_CREATE);
    auto publication_scope = scoped(published);
    invoke_scoped(published, publication_scope);
    check(published.error == ERROR_UNHANDLED_EXCEPTION && published.ownership == PDX_V3_OPTIONS_UNKNOWN
        && published.options != 0 && destructor_calls == destruction_before,
        "scoped factory exception preserves published pointer and reports UNKNOWN only in inner layer");
    reclaim_unknown_only_for_host_teardown(published.options);

    factory_throw_after_publish = false;
    uint64_t source_owner = create(1);
    auto from_published = options_request(PDX_V3_OPTIONS_CREATE_FROM_SETTINGS, 0, settings(source_owner));
    const auto assigns_before = assign_calls;
    factory_throw_after_publish = true;
    PdxV3OptionsCallback(nullptr, &from_published);
    check(from_published.error == ERROR_UNHANDLED_EXCEPTION
        && from_published.ownership == PDX_V3_OPTIONS_UNKNOWN && from_published.options != 0
        && assign_calls == assigns_before && destructor_calls == destruction_before,
        "CreateFromSettings factory exception preserves published pointer without assignment or destruction");
    reclaim_unknown_only_for_host_teardown(from_published.options);
    factory_throw_after_publish = false;
    destroy(source_owner);

    uint64_t owner = create(2);
    const auto id = identity(owner);
    auto destruction = options_request(PDX_V3_OPTIONS_DESTROY, owner);
    auto destruction_scope = scoped(destruction);
    const auto calls_before = destructor_calls;
    destructor_throw_after_release = true;
    invoke_scoped(destruction, destruction_scope);
    destructor_throw_after_release = false;
    check(destruction.error == ERROR_UNHANDLED_EXCEPTION && destruction.options == 0
        && destruction.ownership == PDX_V3_OPTIONS_UNKNOWN && destruction_counts[id] == 1
        && destruction.operation_options == owner && destructor_calls == calls_before + 1,
        "throwing slot1 destructor clears pointer first, reports UNKNOWN, and executes exactly once");
    check(PdxV3OptionsDestroy(&destruction.options) == ERROR_INVALID_PARAMETER
        && destructor_calls == calls_before + 1 && destruction_counts[id] == 1,
        "cleared owner cannot dispatch a second destructor after uncertain native destruction");
}

void test_returned_assignment_error_cleanup() {
    uint64_t source_owner = create(1);
    const uint64_t source_body = settings(source_owner);
    const auto source_id = identity(source_owner);
    const auto assigns_before = assign_calls;
    const auto destroys_before = destructor_calls;
    // Native11394c0 sets marker true. This deliberately invalid HOST fixture
    // tests the adapter's returned-DWORD cleanup branch, not engine behavior.
    factory_marker_fault = true;
    factory_profile = 2;
    uint64_t output = 0;
    uint64_t trace = 0xBADC0FFEE;
    const auto first_id = static_cast<unsigned>(destruction_counts.size());
    check(PdxV3OptionsCreateFromSettings(source_body, &output, &trace) == ERROR_INVALID_STATE
        && output == 0 && trace != 0 && trace != 0xBADC0FFEE
        && destruction_counts[first_id] == 1 && destruction_counts[source_id] == 0
        && assign_calls == assigns_before && destructor_calls == destroys_before + 1,
        "host-only marker fault: returned assignment error destroys known-created owner once and keeps diagnostic trace");
    auto invalid_build = options_request(PDX_V3_OPTIONS_CREATE_FROM_SETTINGS, 0, source_body);
    auto invalid_scope = scoped(invalid_build);
    const auto second_id = static_cast<unsigned>(destruction_counts.size());
    invoke_scoped(invalid_build, invalid_scope);
    check(invalid_build.error == ERROR_INVALID_STATE && invalid_build.options == 0
        && invalid_build.operation_options != 0 && invalid_build.ownership == PDX_V3_OPTIONS_NONE
        && destruction_counts[second_id] == 1 && destruction_counts[source_id] == 0
        && assign_calls == assigns_before && destructor_calls == destroys_before + 2,
        "host-only marker fault: scoped returned error keeps outer success and inner error/NONE trace");
    auto throwing_cleanup = options_request(PDX_V3_OPTIONS_CREATE_FROM_SETTINGS, 0, source_body);
    auto throwing_scope = scoped(throwing_cleanup);
    const auto throwing_id = static_cast<unsigned>(destruction_counts.size());
    destructor_throw_after_release = true;
    invoke_scoped(throwing_cleanup, throwing_scope);
    destructor_throw_after_release = false;
    factory_marker_fault = false;
    check(throwing_cleanup.error == ERROR_UNHANDLED_EXCEPTION && throwing_cleanup.options == 0
        && throwing_cleanup.operation_options != 0 && throwing_cleanup.ownership == PDX_V3_OPTIONS_UNKNOWN
        && destruction_counts[throwing_id] == 1 && destruction_counts[source_id] == 0
        && assign_calls == assigns_before && destructor_calls == destroys_before + 3,
        "host-only marker fault: returned-error cleanup exception propagates as inner UNKNOWN, not a second cleanup");
    check(PdxV3OptionsDestroy(&throwing_cleanup.options) == ERROR_INVALID_PARAMETER
        && destruction_counts[throwing_id] == 1 && destructor_calls == destroys_before + 3,
        "returned-error cleanup failure leaves zero owner and cannot dispatch another destructor");
    check(equals(object(source_owner)->settings.name, source_name)
        && equals(object(source_owner)->settings.password, source_password),
        "all returned-error cleanup paths leave both source-owned strings intact");
    destroy(source_owner);
}

void test_assignment_exceptions_and_scoped_metadata() {
    uint64_t source_owner = create(1);
    uint64_t destination_owner = create(2);
    const uint64_t source_body = settings(source_owner);
    const auto source_id = identity(source_owner);
    const auto destination_id = identity(destination_owner);
    const Settings112 before = object(destination_owner)->settings;
    const auto destroys_before = destructor_calls;
    const auto factories_before = factory_calls;
    assignment_throw_after_first = true;
    bool caught = false;
    try { PdxV3OptionsCopySettings(destination_owner, source_body); } catch (...) { caught = true; }
    check(caught && destination_owner != 0 && destruction_counts[destination_id] == 0
        && destruction_counts[source_id] == 0 && destructor_calls == destroys_before
        && factory_calls == factories_before,
        "raw CopySettings exception propagates while both existing owner pointers remain retained");
    check(equals(object(destination_owner)->settings.name, source_name)
        && equals(object(destination_owner)->settings.password, destination_password)
        && std::memcmp(object(destination_owner)->settings.trailing, before.trailing, 8) == 0
        && equals(object(source_owner)->settings.name, source_name)
        && equals(object(source_owner)->settings.password, source_password),
        "partial native string assignment stays destructible without inventing rollback or altering source");
    auto copy = options_request(PDX_V3_OPTIONS_COPY_SETTINGS, destination_owner, source_body);
    auto copy_scope = scoped(copy);
    invoke_scoped(copy, copy_scope);
    check(copy.error == ERROR_UNHANDLED_EXCEPTION && copy.ownership == PDX_V3_OPTIONS_UNKNOWN
        && copy.options == destination_owner && copy.source_settings == source_body
        && copy.operation_options == destination_owner && destructor_calls == destroys_before,
        "scoped CopySettings failure keeps existing destination pointer and distinct inner error574/UNKNOWN");

    auto build = options_request(PDX_V3_OPTIONS_CREATE_FROM_SETTINGS, 0, source_body);
    auto build_scope = scoped(build);
    const auto new_id = static_cast<unsigned>(destruction_counts.size());
    factory_profile = 2;
    invoke_scoped(build, build_scope);
    check(build.error == ERROR_UNHANDLED_EXCEPTION && build.options == 0
        && build.ownership == PDX_V3_OPTIONS_NONE && destruction_counts[new_id] == 1
        && build.operation_options != 0 && live_owners.find(object(build.operation_options)) == live_owners.end()
        && destructor_calls == destroys_before + 1 && destruction_counts[source_id] == 0
        && destruction_counts[destination_id] == 0,
        "CreateFromSettings copy failure destroys only its known-created destination once; inner NONE/error574");
    uint64_t raw_new = 0;
    uint64_t raw_trace = 0xBADC0FFEE;
    const auto raw_id = static_cast<unsigned>(destruction_counts.size());
    check(PdxV3OptionsCreateFromSettings(source_body, &raw_new, &raw_trace) == ERROR_UNHANDLED_EXCEPTION
        && raw_new == 0 && raw_trace != 0 && raw_trace != 0xBADC0FFEE && destruction_counts[raw_id] == 1
        && destructor_calls == destroys_before + 2 && destruction_counts[source_id] == 0,
        "raw CreateFromSettings reports cleaned assignment exception with zero owner and one cleanup");

    auto cleanup_throw = options_request(PDX_V3_OPTIONS_CREATE_FROM_SETTINGS, 0, source_body);
    const auto cleanup_id = static_cast<unsigned>(destruction_counts.size());
    destructor_throw_after_release = true;
    PdxV3OptionsCallback(nullptr, &cleanup_throw);
    destructor_throw_after_release = false;
    check(cleanup_throw.error == ERROR_UNHANDLED_EXCEPTION && cleanup_throw.options == 0
        && cleanup_throw.ownership == PDX_V3_OPTIONS_UNKNOWN && destruction_counts[cleanup_id] == 1
        && cleanup_throw.operation_options != 0 && destruction_counts[source_id] == 0,
        "CreateFromSettings cleanup exception preserves cleared pointer, diagnostic trace and UNKNOWN");
    const auto cleanup_calls = destructor_calls;
    check(PdxV3OptionsDestroy(&cleanup_throw.options) == ERROR_INVALID_PARAMETER
        && destructor_calls == cleanup_calls && destruction_counts[cleanup_id] == 1,
        "cleanup exception cannot replay a second native destructor; trace is not an owner");
    assignment_throw_after_first = false;

    auto invalid = options_request(PDX_V3_OPTIONS_COPY_SETTINGS, destination_owner, 0);
    auto invalid_scope = scoped(invalid);
    invoke_scoped(invalid, invalid_scope);
    check(invalid.error == ERROR_INVALID_PARAMETER && invalid.options == destination_owner
        && invalid.ownership == PDX_V3_OPTIONS_OWNED && invalid_scope.error == ERROR_SUCCESS,
        "inner null-source validation error87 does not overwrite successful outer scope status");
    auto address = options_request(PDX_V3_OPTIONS_SETTINGS_ADDRESS, destination_owner);
    auto address_scope = scoped(address);
    invoke_scoped(address, address_scope);
    check(address.error == ERROR_SUCCESS && address.settings_address == destination_owner + 8
        && address.ownership == PDX_V3_OPTIONS_OWNED,
        "scoped SettingsAddress exposes borrowed settings only and retains destination ownership");
    auto success = options_request(PDX_V3_OPTIONS_COPY_SETTINGS, destination_owner, source_body);
    auto success_scope = scoped(success);
    invoke_scoped(success, success_scope);
    check(success.error == ERROR_SUCCESS && success.ownership == PDX_V3_OPTIONS_OWNED
        && equals(object(destination_owner)->settings.name, source_name)
        && equals(object(destination_owner)->settings.password, source_password),
        "normal scoped native assignment succeeds with separate complete metadata layers");
    destroy(destination_owner);
    destroy(source_owner);
}
} // namespace fixture

static HMODULE WINAPI FixtureGetModuleHandleW(LPCWSTR name) {
    fixture::check(name == nullptr, "test-only module lookup uses main-image form");
    ++fixture::image_calls;
    return reinterpret_cast<HMODULE>(fixture::fake_image);
}

int main() {
    using namespace fixture;
    std::printf("v3_options_host: MSVC x64 native-shaped ownership mocks; NOT actual V3 execution\n");
    if (!initialize_image()) {
        std::printf("FAIL owned fake-image setup; Win32 error=%lu\n", GetLastError());
        if (fake_image) VirtualFree(fake_image, 0, MEM_RELEASE);
        return 2;
    }
    test_nulls_and_guards();
    test_default_self_and_deep_copy();
    test_publication_and_destroy_exceptions();
    test_returned_assignment_error_cleanup();
    test_assignment_exceptions_and_scoped_metadata();
    check(live_owners.empty() && live_strings.empty(), "all test-owned224 owners and long string allocations released");
    check(clone_calls == 0 && boundary_scope_failures == 0 && enters == leaves && !scope_active,
        "no base128 clone, no scope/thread boundary violations, and every native guard leaves");
    check(image_calls == factory_calls + assign_calls,
        "actual included options source resolves exactly the mocked factory and assignment RVAs");
    check(VirtualFree(fake_image, 0, MEM_RELEASE) != 0, "owned fake executable image released before host exits");
    std::printf("RESULT checks=%d failures=%d factory=%u assign=%u slot1_destroy=%u slot0_clone=%u scopes=%u/%u\n",
        checks, failures, factory_calls, assign_calls, destructor_calls, clone_calls, enters, leaves);
    return failures ? 1 : 0;
}
