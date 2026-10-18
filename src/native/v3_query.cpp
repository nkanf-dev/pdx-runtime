#include "v3_query.h"
#include <cstddef>

namespace {
constexpr uintptr_t application_root_rva = 0x592d128;
constexpr uintptr_t world_offset = 0x608;
constexpr uintptr_t country_database_rva = 0x5964e20;
constexpr uintptr_t country_fallback_rva = 0x5964e18;
constexpr uintptr_t country_resolver_rva = 0x7bb480;
constexpr uintptr_t country_gdp_getter_rva = 0x1bc28e0;
constexpr uintptr_t country_id_offset = 0x18;
using CountryResolver = void* (__fastcall*)(const uint32_t*);
using CountryGdpGetter = uint64_t* (__fastcall*)(const void*, uint64_t*);
constexpr uintptr_t guard_enter_rva = 0x33bd640;
constexpr uintptr_t guard_leave_rva = 0x33bd750;
constexpr uintptr_t ingame_idler_vtable_rva = 0x4431500;
using GuardEnter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using GuardLeave = void (__fastcall*)(void*);

// 0x7d6e73..0x7d6f15 brackets Interface Gfx initialization with (1,0).
// Native callers reserve 80 bytes and use 16-byte alignment for this scope object.
struct alignas(16) NativeAccessStorage {
    unsigned char bytes[80] = {};
};
static_assert(sizeof(NativeAccessStorage) == 80);
static_assert(alignof(NativeAccessStorage) == 16);
struct InterfaceAccessScope {
    NativeAccessStorage& storage;
    uintptr_t base;
    InterfaceAccessScope(NativeAccessStorage& buffer, uintptr_t image_base)
        : storage(buffer), base(image_base) {
        reinterpret_cast<GuardEnter>(base + guard_enter_rva)(
            storage.bytes, 1, 0, "pdx-runtime/native/v3_query.cpp");
    }
    ~InterfaceAccessScope() {
        reinterpret_cast<GuardLeave>(base + guard_leave_rva)(storage.bytes);
    }
    InterfaceAccessScope(const InterfaceAccessScope&) = delete;
    InterfaceAccessScope& operator=(const InterfaceAccessScope&) = delete;
};

struct CountryIdWrapper {
    uint64_t unused[2];
    uint32_t full_id;
};
static_assert(offsetof(CountryIdWrapper, full_id) == 0x10);
static_assert(sizeof(PdxV3CountryResult) == 40);

uintptr_t module_base() {
    return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
}
template<class T> T field(uintptr_t object, uintptr_t offset) {
    return *reinterpret_cast<const T*>(object + offset);
}
void* resolve(uintptr_t base, uint32_t full_id) {
    return reinterpret_cast<CountryResolver>(base + country_resolver_rva)(&full_id);
}
void get_gdp(uintptr_t base, uint32_t full_id, uint64_t* bits) {
    const CountryIdWrapper wrapper = {{0, 0}, full_id};
    // Captured getter reads only wrapper+0x10, resolves it, and copies Country+0x88.
    reinterpret_cast<CountryGdpGetter>(base + country_gdp_getter_rva)(&wrapper, bits);
}
}

uint64_t WINAPI PdxV3ResolveCountry(uint32_t full_id) {
    return reinterpret_cast<uintptr_t>(resolve(module_base(), full_id));
}

DWORD WINAPI PdxV3CountryGdpBits(uint32_t full_id, uint64_t* bits) {
    if (!bits) return ERROR_INVALID_PARAMETER;
    get_gdp(module_base(), full_id, bits);
    return ERROR_SUCCESS;
}

DWORD WINAPI PdxV3QueryCountry(uint32_t full_id, PdxV3CountryResult* result) {
    if (!result) return ERROR_INVALID_PARAMETER;
    *result = {sizeof(PdxV3CountryResult), ERROR_SUCCESS, full_id, 0, 0, 0, 0};
    const uintptr_t base = module_base();
    const uintptr_t app = field<uintptr_t>(base, application_root_rva);
    if (!app || !field<uintptr_t>(app, world_offset)
        || !field<uintptr_t>(base, country_database_rva)) {
        result->error = ERROR_NOT_READY;
        return result->error;
    }
    result->fallback = field<uintptr_t>(base, country_fallback_rva);
    result->country = reinterpret_cast<uintptr_t>(resolve(base, full_id));
    if (!result->country || result->country == result->fallback) {
        result->error = ERROR_NOT_FOUND;
        return result->error;
    }
    result->resolved_id = field<uint32_t>(result->country, country_id_offset);
    if (result->resolved_id != full_id) {
        result->error = ERROR_NOT_FOUND;
        return result->error;
    }
    get_gdp(base, full_id, &result->gdp_raw64);
    return ERROR_SUCCESS;
}

void WINAPI PdxV3QueryCountryInterface(void* application, void* opaque) {
    auto* result = static_cast<PdxV3CountryResult*>(opaque);
    if (!result) return;
    const uint32_t full_id = result->requested_id;
    *result = {sizeof(PdxV3CountryResult), ERROR_NOT_READY, full_id, 0, 0, 0, 0};
    const uintptr_t base = module_base();
    const uintptr_t app = reinterpret_cast<uintptr_t>(application);
    if (!app || app != field<uintptr_t>(base, application_root_rva)
        || field<uint8_t>(app, 0x258)
        || !field<uintptr_t>(app, world_offset)) return;
    const uintptr_t idler = field<uintptr_t>(app, 0x1c0);
    if (!idler || field<uintptr_t>(idler, 0) != base + ingame_idler_vtable_rva) return;
    NativeAccessStorage storage;
    try {
        InterfaceAccessScope scope(storage, base);
        // Recheck the world/root and full ID under the actual native scope.
        PdxV3QueryCountry(full_id, result);
    } catch (...) {
        // Native scope restoration runs on C++ unwind; no blind retry or replay.
        result->error = ERROR_UNHANDLED_EXCEPTION;
    }
}
