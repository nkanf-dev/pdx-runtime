#include "v3_data.h"
#include "v3_data_projection_scope.h"
#include "dependent_sequence_core.h"
#include "v3_owned_context.h"
#include "v3_game_rule_selection.h"
#include "v3_data_native_fields.h"
#include "v3_data_original_sources.h"
#include "v3_data_company_sources.h"
#include "v3_data_rule_fields.h"
#include <cstddef>
#include <cstring>
#include <string>
#include <vector>
#include <algorithm>
#include <memory>

namespace {
constexpr uintptr_t factory_rva = 0x592ebb8;
constexpr uintptr_t app_rva = 0x592d128;
constexpr uintptr_t guard_enter_rva = 0x33bd640;
constexpr uintptr_t guard_leave_rva = 0x33bd750;
constexpr uintptr_t property_ctor_rva = 0x2d11f40;
constexpr uintptr_t property_dtor_rva = 0x2d120b0;
constexpr uintptr_t compile_rva = 0x3a7b010;
constexpr uintptr_t bind_rva = 0x35f13a0;
constexpr uintptr_t binding_scope_rva = 0x35ee7c0;
constexpr uintptr_t binding_dtor_rva = 0x3563660;
constexpr uintptr_t prepare_rva = 0x3a79b80;
constexpr uintptr_t evaluate_rva = 0x3a79570;
constexpr uintptr_t action_rva = 0x3a797b0;
constexpr uintptr_t empty_type_rva = 0x63af30;
constexpr uintptr_t any_dtor_rva = 0x7bd3f0;
constexpr uintptr_t any_move_rva = 0x79f630;
constexpr uintptr_t engine_free_rva = 0x417d850;
using EmptyType = uintptr_t (__fastcall*)();
using StorageFn = void* (__fastcall*)(void*);
using CompileFn = bool (__fastcall*)(void*, const void*, void*, uint8_t, void*);
using BindFn = void (__fastcall*)(uintptr_t, const int32_t*, void*);
using BindingScopeFn = bool (__fastcall*)(void*, PdxV3DataScope*);
using PrepareFn = bool (__fastcall*)(void*, void*, void*, void*, int32_t*);
using EvaluateFn = bool (__fastcall*)(void*, void*, void*, void*);
using MoveAnyFn = void* (__fastcall*)(void*, void*);
using GuardEnter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using GuardLeave = void (__fastcall*)(void*);

uintptr_t base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T get(uintptr_t p, uintptr_t o = 0) { return *reinterpret_cast<const T*>(p + o); }
template<class T> T get(const void* p, uintptr_t o = 0) { return get<T>(reinterpret_cast<uintptr_t>(p), o); }
uintptr_t country_wrapper(uint32_t full_id) {
    using Allocate = void* (__fastcall*)(uint64_t);
    using Constructor = void* (__fastcall*)(void*, uint32_t);
    const auto storage = reinterpret_cast<Allocate>(base() + 0x417d960)(0x1970);
    if (!storage) return 0;
    const auto wrapper = reinterpret_cast<Constructor>(base() + 0x1995740)(storage, full_id);
    InterlockedIncrement(reinterpret_cast<volatile LONG*>(reinterpret_cast<uintptr_t>(wrapper) + 8));
    return reinterpret_cast<uintptr_t>(wrapper);
}
void release_wrapper(uintptr_t wrapper) {
    if (wrapper && InterlockedDecrement(reinterpret_cast<volatile LONG*>(wrapper + 8)) == 0) {
        using Destroy = void* (__fastcall*)(void*, uint32_t);
        reinterpret_cast<Destroy>(get<uintptr_t>(get<uintptr_t>(wrapper)))(reinterpret_cast<void*>(wrapper), 1);
    }
}

struct IdDomain {
    const char* name; uintptr_t database; uintptr_t fallback; uintptr_t resolver;
    uintptr_t id_offset; uintptr_t cache; uintptr_t getter;
};
const IdDomain* id_domain(uint32_t domain) {
    static constexpr IdDomain country{"Country", 0x5964e20, 0x5964e18, 0x7bb480, 24, 0, 0};
    static constexpr IdDomain state{"State", 0x5964ef0, 0x5964e28, 0xc5af10, 8, 0x5966a10, 0xbefef0};
    static constexpr IdDomain pop{"Pop", 0x5964ec8, 0x5964388, 0x7cacf0, 8, 0x5968f60, 0x130da50};
    static constexpr IdDomain market{"Market", 0x5964ed8, 0x5964330, 0x7cada0, 8, 0x59684a0, 0x10594e0};
    static constexpr IdDomain building{"Building", 0x5966738, 0x5964278, 0xc5ae60, 16, 0x59679c8, 0xda3d50};
    switch (domain) {
    case PDX_V3_DATA_COUNTRY: return &country;
    case PDX_V3_DATA_STATE: return &state;
    case PDX_V3_DATA_POP: return &pop;
    case PDX_V3_DATA_MARKET: return &market;
    case PDX_V3_DATA_BUILDING: return &building;
    default: return nullptr;
    }
}
uint32_t binding_domain(uint32_t flags) {
    switch (flags) {
    case 2: return PDX_V3_DATA_COUNTRY;
    case 3: return PDX_V3_DATA_STATE;
    case 4: return PDX_V3_DATA_POP;
    case 5: return PDX_V3_DATA_MARKET;
    case 6: return PDX_V3_DATA_BUILDING;
    default: return 0;
    }
}
struct BuildingAccessor {
    uint32_t state_id; uint32_t padding; uint64_t building_type;
};
static_assert(sizeof(BuildingAccessor) == 16);
DWORD wrapper_for_id(uint32_t domain, uint32_t full_id, uint64_t* wrapper, uint64_t* source_world = nullptr) {
    if (!wrapper) return ERROR_INVALID_PARAMETER;
    *wrapper = 0; if (source_world) *source_world = 0;
    const auto* config = id_domain(domain);
    if (!config) return ERROR_NOT_SUPPORTED;
    const auto app = get<uintptr_t>(base(), app_rva);
    const auto world = app ? get<uintptr_t>(app, 0x608) : 0;
    if (!app || !world || !get<uintptr_t>(base(), config->database))
        return ERROR_NOT_READY;
    using Resolver = uintptr_t (__fastcall*)(const uint32_t*);
    const auto object = reinterpret_cast<Resolver>(base() + config->resolver)(&full_id);
    if (!object || full_id == UINT32_MAX || object == get<uintptr_t>(base(), config->fallback)
        || get<uint32_t>(object, config->id_offset) != full_id) return ERROR_NOT_FOUND;
    if (domain == PDX_V3_DATA_COUNTRY) {
        *wrapper = country_wrapper(full_id);
    } else {
        const auto cache = get<uintptr_t>(base(), config->cache);
        if (!cache) return ERROR_NOT_READY;
        using CacheGetter = uintptr_t (__fastcall*)(uintptr_t, const void*);
        const BuildingAccessor building{domain == PDX_V3_DATA_BUILDING ? get<uint32_t>(object, 0xe4) : 0,
            0, domain == PDX_V3_DATA_BUILDING ? get<uintptr_t>(object, 0xf0) : 0};
        const void* key = domain == PDX_V3_DATA_BUILDING ? static_cast<const void*>(&building)
                                                                      : static_cast<const void*>(&full_id);
        *wrapper = reinterpret_cast<CacheGetter>(base() + config->getter)(cache, key);
        // Native cache getters return a borrowed wrapper; caller CPdxPtr paths
        // retain +8 before use. The cache continues to own its separate reference.
        if (*wrapper) InterlockedIncrement(reinterpret_cast<volatile LONG*>(*wrapper + 8));
    }
    if (!*wrapper) return ERROR_NOT_ENOUGH_MEMORY;
    // Only this resolved/fallback/full-ID validated native path captures world.
    if (source_world) *source_world = world;
    return ERROR_SUCCESS;
}
uintptr_t empty_type() { return reinterpret_cast<EmptyType>(base() + empty_type_rva)(); }
struct StringView { const char* data; int32_t size; uint8_t is_null; uint8_t padding[3]; };
static_assert(sizeof(StringView) == 16);
static_assert(sizeof(PdxV3DataRequest) == 368);
static_assert(sizeof(PdxV3DataActionRequest) == 392);
static_assert(sizeof(PdxV3DataElementRequest) == 432);
static_assert(sizeof(PdxV3DataPointerArraySource) == 40);
static_assert(sizeof(PdxV3DataArrayElementRequest) == 472);
static_assert(sizeof(PdxV3DataSourceGuard) == 24);
static_assert(sizeof(PdxV3DataGuardedArrayElementRequest) == 496);
static_assert(sizeof(PdxV3DataInput) == 24);
static_assert(sizeof(PdxV3DataCommandValue) == 32);
static_assert(sizeof(PdxV3DataScope) == 16);
static_assert(sizeof(PdxV3DataArray) == 24);
static_assert(sizeof(PdxV3DataTypeInfo) == 424);
static_assert(sizeof(PdxV3DataFunctionInfo) == 448);
std::string cstring(uintptr_t p) {
    const auto n = get<uint64_t>(p, 16);
    const auto data = get<uint64_t>(p, 24) > 15 ? get<uintptr_t>(p) : p;
    return std::string(reinterpret_cast<const char*>(data), static_cast<size_t>(n));
}
void copy_text(char* output, size_t capacity, const std::string& text) {
    const auto n = std::min(capacity - 1, text.size());
    memcpy(output, text.data(), n); output[n] = 0;
}
uintptr_t type_record(int32_t id) {
    const auto f = PdxV3DataFactory();
    return f && id >= 0 && id < get<int32_t>(f, 44)
        ? get<uintptr_t>(f, 32) + 304ull * id : 0;
}
bool request_range(const PdxV3DataRequest* r, uint32_t offset, uint64_t n) {
    return offset >= sizeof(*r) && offset <= r->size && n <= uint64_t(r->size) - offset;
}
void* output(PdxV3DataRequest* r) { return reinterpret_cast<unsigned char*>(r) + r->output_offset; }
void reset_output(PdxV3DataRequest* r) {
    r->error = ERROR_SUCCESS; r->count = r->next = r->total = r->snapshot_bytes = 0;
    r->execution_thread_id = GetCurrentThreadId(); r->factory = PdxV3DataFactory();
    r->kind = PDX_V3_DATA_RAW; r->object = r->any_type = r->any_vtable = r->payload_address = 0;
    r->payload_size = 0; r->raw_bits = 0; memset(r->raw_any, 0, sizeof(r->raw_any));
    memset(r->native_type_name, 0, sizeof(r->native_type_name));
}
struct alignas(16) AccessStorage { unsigned char bytes[80] = {}; };
struct AccessScope {
    AccessStorage storage;
    AccessScope() { reinterpret_cast<GuardEnter>(base() + guard_enter_rva)(
        storage.bytes, 1, 0, "pdx-runtime/native/v3_data.cpp"); }
    ~AccessScope() { reinterpret_cast<GuardLeave>(base() + guard_leave_rva)(storage.bytes); }
};
struct PropertyOwner {
    PdxV3DataProperty value{};
    PropertyOwner() { reinterpret_cast<StorageFn>(base() + property_ctor_rva)(value.bytes); }
    ~PropertyOwner() { PdxV3DataDestroyProperty(&value); }
};
struct BindingOwner {
    PdxV3DataBinding value{};
    BindingOwner() { value.bytes[48] = 0xff; }
    ~BindingOwner() { PdxV3DataDestroyBinding(&value); }
    BindingOwner(const BindingOwner&) = delete;
    BindingOwner& operator=(const BindingOwner&) = delete;
};
void copy_any(void* destination, const PdxV3DataAny& source) {
    const auto tagged = get<uintptr_t>(source.bytes);
    const auto source_type = tagged & ~uintptr_t(1);
    const auto data = tagged & 1 ? get<uintptr_t>(source.bytes, 8)
        : reinterpret_cast<uintptr_t>(source.bytes + 8);
    using TypedCopyFn = void* (__fastcall*)(void*, const void*, uintptr_t);
    reinterpret_cast<TypedCopyFn>(base() + 0xbdf700)(destination,
        reinterpret_cast<const void*>(data), source_type);
}
struct ValueNode {
    PdxV3DataAny value{};
    int32_t type_id = -1;
    uint64_t handle = 0;
    uintptr_t state_name_context = 0; uintptr_t context_world = 0;
    PropertyOwner* property = nullptr;
    bool owns_property = true;
    std::vector<BindingOwner*> bindings;
    std::vector<PdxV3DataScope> scopes;
    std::vector<unsigned char> refs;
    PdxV3DataArray scope_array{};
    PdxV3DataArray args{};
    uint32_t references = 1;
    std::vector<ValueNode*> dependencies;
    ValueNode* next = nullptr;
    ~ValueNode() {
        if (get<uintptr_t>(value.bytes)) PdxV3DataDestroyAny(&value);
        for (auto* binding : bindings) delete binding;
        if (owns_property) delete property;
        for (auto* dependency : dependencies)
            if (--dependency->references == 0) delete dependency;
    }
};
SRWLOCK values_lock = SRWLOCK_INIT;
ValueNode* values = nullptr;
uint64_t next_handle = 0;
ValueNode* find_value(uint64_t handle) {
    for (auto* v = values; v; v = v->next)
        if (v->handle == handle) return v;
    return nullptr;
}
struct ValueLock {
    ValueLock() { AcquireSRWLockExclusive(&values_lock); }
    ~ValueLock() { ReleaseSRWLockExclusive(&values_lock); }
};
DWORD release_value_locked(uint64_t* handle) {
    if (!handle || !*handle) return ERROR_INVALID_PARAMETER;
    auto** cursor = &values;
    while (*cursor && (*cursor)->handle != *handle) cursor = &(*cursor)->next;
    if (!*cursor) return ERROR_INVALID_HANDLE;
    auto* node = *cursor; *cursor = node->next;
    if (--node->references == 0) delete node;
    *handle = 0; return ERROR_SUCCESS;
}
bool world_matches(const ValueNode* node) {
    if (!node || !node->context_world) return true;
    const auto app = get<uintptr_t>(base(), app_rva);
    return app && get<uintptr_t>(app, 0x608) == node->context_world;
}
std::string any_name(uintptr_t vtable) {
    // MSVC x64 RTTI COL has image-relative type descriptor at +12.
    const auto col = get<uintptr_t>(vtable - 8);
    const auto td = base() + get<uint32_t>(col, 12);
    return reinterpret_cast<const char*>(td + 16);
}
bool starts(const std::string& text, const char* prefix) { return text.rfind(prefix, 0) == 0; }
uint32_t classify(const std::string& name) {
    if (name == ".?AU?$SAnyType@USPdxAnyEmpty@PdxAnyImpl@@@PdxAnyImpl@@") return PDX_V3_DATA_EMPTY;
    if (name == ".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@") return PDX_V3_DATA_COMMAND;
    if (name == ".?AU?$SAnyType@_N@PdxAnyImpl@@") return PDX_V3_DATA_BOOL;
    if (name == ".?AU?$SAnyType@H@PdxAnyImpl@@") return PDX_V3_DATA_I32;
    if (name == ".?AU?$SAnyType@I@PdxAnyImpl@@") return PDX_V3_DATA_U32;
    if (name == ".?AU?$SAnyType@_J@PdxAnyImpl@@") return PDX_V3_DATA_I64;
    if (name == ".?AU?$SAnyType@_K@PdxAnyImpl@@") return PDX_V3_DATA_U64;
    if (name == ".?AU?$SAnyType@M@PdxAnyImpl@@") return PDX_V3_DATA_FLOAT;
    if (name == ".?AU?$SAnyType@N@PdxAnyImpl@@") return PDX_V3_DATA_DOUBLE;
    if (name == ".?AU?$SAnyType@VCFixedPoint@@@PdxAnyImpl@@") return PDX_V3_DATA_FIXED;
    if (name == ".?AU?$SAnyType@VCString@@@PdxAnyImpl@@"
        || name == ".?AU?$SAnyType@VCUTF8String@@@PdxAnyImpl@@"
        || name == ".?AU?$SAnyType@VCPdxStringView@@@PdxAnyImpl@@") return PDX_V3_DATA_STRING;
    if (name.find("CPdxGuiDataModelCreation") != std::string::npos
        || name.find("CPdxArray@") != std::string::npos
        || name.find("CPdxSpan@") != std::string::npos
        || name.find("CPdxGuiDataModelCollection@") != std::string::npos) return PDX_V3_DATA_LIST;
    if (name == ".?AU?$SAnyType@V?$CPdxPtr@VCReleaseCountryWindow@@@@@PdxAnyImpl@@"
        || name == ".?AU?$SAnyType@V?$CPdxPtr@VCPowerBlocCustomizationPopup@@@@@PdxAnyImpl@@"
        || name == ".?AU?$SAnyType@V?$CPdxPtr@VCMilitaryFormationNameChangePopup@@@@@PdxAnyImpl@@"
        || name == ".?AU?$SAnyType@V?$CPdxPtr@VCPowerBlocFormation@@@@@PdxAnyImpl@@"
        || name == ".?AU?$SAnyType@V?$CPdxPtr@VCShipChangeNamePopup@@@@@PdxAnyImpl@@")
        return PDX_V3_DATA_OBJECT;
    if (starts(name, ".?AU?$SAnyType@V?$CPdxPtr@VCStateNameChangePopup")
        || starts(name, ".?AU?$SAnyType@V?$CPdxPtr@VCData")
        || starts(name, ".?AU?$SAnyType@PEBVCData")
        || starts(name, ".?AU?$SAnyType@PEAVCData")) return PDX_V3_DATA_OBJECT;
    return PDX_V3_DATA_RAW;
}
DWORD snapshot_value(PdxV3DataRequest* r, const PdxV3DataAny& any, bool bounded = false,
    std::vector<unsigned char>* owned_bytes = nullptr) {
    memcpy(r->raw_any, any.bytes, 40);
    const uintptr_t tagged = get<uintptr_t>(any.bytes);
    r->any_type = tagged & ~uintptr_t(1);
    r->payload_address = tagged & 1 ? get<uintptr_t>(any.bytes, 8)
                                           : reinterpret_cast<uintptr_t>(any.bytes + 8);
    if (r->any_type == empty_type()) {
        r->kind = PDX_V3_DATA_EMPTY; r->payload_size = r->snapshot_bytes = 0;
        return ERROR_SUCCESS;
    }
    r->any_vtable = get<uintptr_t>(r->any_type);
    const auto name = any_name(r->any_vtable);
    copy_text(r->native_type_name, sizeof(r->native_type_name), name);
    r->kind = classify(name);
    if (owned_bytes && (r->kind < PDX_V3_DATA_EMPTY || r->kind > PDX_V3_DATA_STRING))
        return ERROR_NOT_SUPPORTED;
    // SAnyBaseType slot11 reports payload size (string=32, fixed=8, bool=1).
    using SizeFn = uint64_t (__fastcall*)(void*);
    const auto payload_size = reinterpret_cast<SizeFn>(
        get<uintptr_t>(r->any_vtable, 88))(reinterpret_cast<void*>(r->any_type));
    if (bounded && payload_size > UINT32_MAX) return ERROR_ARITHMETIC_OVERFLOW;
    r->payload_size = static_cast<uint32_t>(payload_size);
    const auto p = static_cast<uintptr_t>(r->payload_address);
    r->raw_bits = 0;
    if (r->payload_size) memcpy(&r->raw_bits, reinterpret_cast<void*>(p),
                                std::min<size_t>(8, r->payload_size));
    const void* data = reinterpret_cast<void*>(p);
    uint64_t bytes = r->payload_size;
    if (r->kind == PDX_V3_DATA_STRING) {
        if (name.find("VCPdxStringView") != std::string::npos) {
            data = reinterpret_cast<void*>(get<uintptr_t>(p)); bytes = get<uint32_t>(p, 8);
        } else {
            data = reinterpret_cast<void*>(get<uint64_t>(p, 24) > 15 ? get<uintptr_t>(p) : p);
            bytes = get<uint64_t>(p, 16);
        }
    } else if (r->kind == PDX_V3_DATA_OBJECT || r->kind == PDX_V3_DATA_COMMAND) {
        if (r->kind == PDX_V3_DATA_COMMAND
            && (r->any_type != reinterpret_cast<EmptyType>(base() + 0x10b4940)()
                || r->payload_size != 24)) return ERROR_INVALID_DATA;
        r->object = get<uintptr_t>(p);
    }
    if (bounded && bytes > UINT32_MAX) return ERROR_ARITHMETIC_OVERFLOW;
    r->total = static_cast<uint32_t>(bytes);
    if (owned_bytes) {
        pdx::data_projection_core::append_bytes(data, static_cast<size_t>(bytes), *owned_bytes);
        r->snapshot_bytes = static_cast<uint32_t>(bytes);
        return ERROR_SUCCESS;
    }
    if (!r->output_bytes) return ERROR_SUCCESS;
    if (!request_range(r, r->output_offset, r->output_bytes)) return ERROR_INVALID_PARAMETER;
    const auto copied = pdx::data_projection_core::copy_bytes(data, static_cast<size_t>(bytes), output(r), r->output_bytes);
    r->snapshot_bytes = static_cast<uint32_t>(copied.copied);
    // Retained raw API semantics remain unchanged; copied-only callers own no Any.
    return copied.truncated ? ERROR_MORE_DATA : ERROR_SUCCESS;
}
}

uint64_t WINAPI PdxV3DataFactory() { return get<uintptr_t>(base(), factory_rva); }
int32_t WINAPI PdxV3DataFindType(const char* name) {
    if (!name) return -1;
    const auto f = PdxV3DataFactory(); if (!f) return -1;
    for (int32_t i = 0; i < get<int32_t>(f, 44); ++i)
        if (cstring(get<uintptr_t>(f, 32) + 304ull * i) == name) return i;
    return -1;
}
uint64_t WINAPI PdxV3DataFindFunction(int32_t id, const char* name) {
    const auto t = type_record(id); if (!t || !name) return 0;
    // Use the same native name lookup as parser3a80250; descriptor is borrowed.
    using FindFn = uintptr_t (__fastcall*)(uintptr_t, const char**);
    return reinterpret_cast<FindFn>(base() + 0x3a92e50)(t + 72, &name);
}
DWORD WINAPI PdxV3DataEnumerateTypes(PdxV3DataRequest* r) {
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    reset_output(r);
    if (!r->factory) return r->error = ERROR_NOT_READY;
    if (!request_range(r, r->output_offset, uint64_t(r->capacity) * sizeof(PdxV3DataTypeInfo))
        || r->output_bytes < uint64_t(r->capacity) * sizeof(PdxV3DataTypeInfo))
        return r->error = ERROR_INVALID_PARAMETER;
    auto* rows = static_cast<PdxV3DataTypeInfo*>(output(r));
    r->total = get<uint32_t>(r->factory, 44);
    uint32_t i = r->start;
    while (i < r->total && r->count < r->capacity) {
        const auto p = get<uintptr_t>(r->factory, 32) + 304ull * i;
        if (get<uint64_t>(p, 16)) {
            auto& row = rows[r->count++]; row = {};
            row.type_id = static_cast<int32_t>(i); row.native_record = p;
            const auto name = cstring(p); const auto desc = cstring(p + 248);
            row.name_bytes = static_cast<uint32_t>(name.size());
            row.description_bytes = static_cast<uint32_t>(desc.size());
            copy_text(row.name, sizeof(row.name), name); copy_text(row.description, sizeof(row.description), desc);
            row.object_to_any = get<uintptr_t>(p, 216); row.any_to_object = get<uintptr_t>(p, 224);
            row.flags = (row.object_to_any && row.any_to_object) ? 1u : 0u;
        }
        ++i;
    }
    r->next = i; r->snapshot_bytes = r->count * sizeof(*rows); return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DataEnumerateFunctions(PdxV3DataRequest* r) {
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    reset_output(r);
    const auto t = type_record(r->type_id);
    if (!t) return r->error = r->factory ? ERROR_NOT_FOUND : ERROR_NOT_READY;
    if (!request_range(r, r->output_offset, uint64_t(r->capacity) * sizeof(PdxV3DataFunctionInfo))
        || r->output_bytes < uint64_t(r->capacity) * sizeof(PdxV3DataFunctionInfo))
        return r->error = ERROR_INVALID_PARAMETER;
    const auto table = t + 72; const auto buckets = get<uintptr_t>(table, 8);
    r->total = get<uint32_t>(table, 20) + get<uint8_t>(table, 24) + 1;
    auto* rows = static_cast<PdxV3DataFunctionInfo*>(output(r));
    uint32_t i = r->start;
    while (i < r->total && r->count < r->capacity) {
        const auto b = buckets + 168ull * i;
        if (get<uint8_t>(b, 4)) {
            auto& row = rows[r->count++]; row = {};
            row.type_id = r->type_id; row.native_descriptor = b + 40;
            row.return_type_id = get<int32_t>(b, 76); row.argument_count = get<uint32_t>(b, 72);
            const auto name = cstring(b + 40); const auto desc = cstring(b + 112);
            row.name_bytes = static_cast<uint32_t>(name.size()); row.description_bytes = static_cast<uint32_t>(desc.size());
            copy_text(row.name, sizeof(row.name), name); copy_text(row.description, sizeof(row.description), desc);
            memcpy(row.callbacks, reinterpret_cast<const void*>(b + 80), sizeof(row.callbacks));
            row.flags = get<uint8_t>(b, 4);
        }
        ++i;
    }
    r->next = i; r->snapshot_bytes = r->count * sizeof(*rows); return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DataCompile(const char* expression, uint32_t bytes, PdxV3DataProperty* p) {
    if (!expression || !p || bytes > INT32_MAX) return ERROR_INVALID_PARAMETER;
    if (!PdxV3DataFactory()) return ERROR_NOT_READY;
    reinterpret_cast<StorageFn>(base() + property_ctor_rva)(p->bytes);
    const StringView text{expression, static_cast<int32_t>(bytes), 0, {0,0,0}};
    return reinterpret_cast<CompileFn>(base() + compile_rva)(
        reinterpret_cast<void*>(PdxV3DataFactory()), &text, p->bytes, 1, nullptr)
            ? ERROR_SUCCESS : ERROR_INVALID_DATA;
}
void WINAPI PdxV3DataDestroyProperty(PdxV3DataProperty* p) {
    if (p) reinterpret_cast<StorageFn>(base() + property_dtor_rva)(p->bytes);
}
uint64_t WINAPI PdxV3DataCountryWrapper(uint32_t full_id) { return country_wrapper(full_id); }
void WINAPI PdxV3DataReleaseWrapper(uint64_t wrapper) { release_wrapper(wrapper); }
DWORD WINAPI PdxV3DataWrapperForId(uint32_t domain, uint32_t full_id, uint64_t* wrapper) {
    return wrapper_for_id(domain, full_id, wrapper);
}
namespace {
DWORD bind_object(uint64_t object, int32_t id, PdxV3DataBinding* b) {
    if (!object || !b || !type_record(id)) return ERROR_INVALID_PARAMETER;
    memset(b->bytes, 0, sizeof(b->bytes)); b->bytes[48] = 0xff;
    const auto t = type_record(id);
    if (!get<uintptr_t>(t, 216) || !get<uintptr_t>(t, 224)) return ERROR_NOT_SUPPORTED;
    reinterpret_cast<BindFn>(base() + bind_rva)(object, &id, b->bytes);
    memcpy(b->bytes + 56, &id, sizeof(id));
    return b->bytes[48] == 2 ? ERROR_SUCCESS : ERROR_INVALID_DATA;
}
DWORD bind_scoped_object(uint64_t object, int32_t id, PdxV3DataBinding* b) {
    if (!object || !b || !type_record(id)) return ERROR_INVALID_PARAMETER;
    // Original modes/layout/constructor are V3 ABI, never shared-core facts.
    const auto bound = pdx::data_projection_core::bind_scoped(object, id, *b,
        [](PdxV3DataBinding& binding, int32_t type) {
            memset(binding.bytes, 0, sizeof(binding.bytes)); binding.bytes[48] = 0xff;
            memcpy(binding.bytes + 56, &type, sizeof(type));
        },
        [](uint64_t original, int32_t, PdxV3DataBinding& binding) {
            reinterpret_cast<BindFn>(base() + bind_rva)(original,
                reinterpret_cast<const int32_t*>(binding.bytes + 56), binding.bytes);
        },
        [](const PdxV3DataBinding& binding, int32_t type) {
            return (binding.bytes[48] == 0 || binding.bytes[48] == 2)
                && get<int32_t>(binding.bytes, 56) == type;
        });
    return bound ? ERROR_SUCCESS : ERROR_INVALID_DATA;
}
}
DWORD WINAPI PdxV3DataBindObject(uint64_t object, int32_t id, PdxV3DataBinding* b) {
    try { return bind_object(object, id, b); }
    catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3DataBindScopedObject(uint64_t object, int32_t id, PdxV3DataBinding* b) {
    try { return bind_scoped_object(object, id, b); }
    catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
namespace {
DWORD binding_scope(PdxV3DataBinding* b, PdxV3DataScope* s) {
    if (!b || !s || b->bytes[48] == 0xff) return ERROR_INVALID_PARAMETER;
    *s = {};
    return reinterpret_cast<BindingScopeFn>(base() + binding_scope_rva)(b->bytes, s)
        ? ERROR_SUCCESS : ERROR_INVALID_DATA;
}
}
DWORD WINAPI PdxV3DataBindingScope(PdxV3DataBinding* b, PdxV3DataScope* s) {
    try { return binding_scope(b, s); }
    catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
void WINAPI PdxV3DataDestroyBinding(PdxV3DataBinding* b) {
    if (b && b->bytes[48] != 0xff)
        reinterpret_cast<StorageFn>(base() + binding_dtor_rva)(b->bytes);
}
namespace {
DWORD prepare(PdxV3DataProperty* p, PdxV3DataArray* scopes,
    PdxV3DataArray* args, PdxV3DataScopeValue* scope, int32_t* id) {
    if (!p || !scopes || !args || !scope || !id) return ERROR_INVALID_PARAMETER;
    return reinterpret_cast<PrepareFn>(base() + prepare_rva)(p->bytes, scopes, args, scope, id)
        ? ERROR_SUCCESS : ERROR_INVALID_DATA;
}
}
DWORD WINAPI PdxV3DataPrepare(PdxV3DataProperty* p, PdxV3DataArray* scopes,
    PdxV3DataArray* args, PdxV3DataScopeValue* scope, int32_t* id) {
    try { return prepare(p, scopes, args, scope, id); }
    catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
namespace {
DWORD evaluate_prepared(PdxV3DataProperty* p, PdxV3DataScopeValue* scope,
    PdxV3DataArray* args, PdxV3DataAny* value, bool preserve_constant = false) {
    if (!p || !scope || !args || !value) return ERROR_INVALID_PARAMETER;
    memset(value->bytes, 0, sizeof(value->bytes));
    const auto empty = empty_type(); memcpy(value->bytes, &empty, 8);
    // Compiled constants already live at property+120 and need no callstack.
    if (get<uintptr_t>(p->bytes, 120) != empty) {
        pdx::data_projection_core::materialize_constant(preserve_constant,
            [&] { copy_any(value->bytes, *reinterpret_cast<const PdxV3DataAny*>(p->bytes + 120)); },
            [&] { reinterpret_cast<MoveAnyFn>(base() + any_move_rva)(value->bytes, p->bytes + 120); });
        return ERROR_SUCCESS;
    }
    const auto mode = get<uint32_t>(p->bytes, 56);
    if (mode != 1 && mode != 2) return ERROR_NOT_SUPPORTED;
    return reinterpret_cast<EvaluateFn>(base() + evaluate_rva)(
        p->bytes + 24, scope, value->bytes, args) ? ERROR_SUCCESS : ERROR_INVALID_DATA;
}
}
DWORD WINAPI PdxV3DataEvaluatePrepared(PdxV3DataProperty* p, PdxV3DataScopeValue* scope,
    PdxV3DataArray* args, PdxV3DataAny* value) {
    try { return evaluate_prepared(p, scope, args, value); }
    catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
namespace {
DWORD action_prepared(PdxV3DataProperty* p, PdxV3DataScopeValue* scope,
    PdxV3DataArray* args, PdxV3DataAny* input, uint32_t intent, uint32_t* accepted) {
    if (!p || !scope || !args || !input || !accepted || intent != PDX_V3_DATA_EXPLICIT_ACTION)
        return ERROR_INVALID_PARAMETER;
    *accepted = 0;
    const auto mode = get<uint32_t>(p->bytes, 56);
    if (mode != 3 && mode != 4) return ERROR_NOT_SUPPORTED;
    *accepted = reinterpret_cast<EvaluateFn>(base() + action_rva)(
        p->bytes + 24, scope, input->bytes, args) ? 1u : 0u;
    return ERROR_SUCCESS;
}
}
DWORD WINAPI PdxV3DataActionPrepared(PdxV3DataProperty* p, PdxV3DataScopeValue* scope,
    PdxV3DataArray* args, PdxV3DataAny* input, uint32_t intent, uint32_t* accepted) {
    try { return action_prepared(p, scope, args, input, intent, accepted); }
    catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
namespace {
uintptr_t primitive_type(uint32_t kind) {
    uintptr_t getter = 0;
    switch (kind) {
    case PDX_V3_DATA_BOOL: getter = 0x79f7a0; break;
    case PDX_V3_DATA_I32: getter = 0x7b7740; break;
    case PDX_V3_DATA_U32: getter = 0xbdde30; break;
    case PDX_V3_DATA_I64: getter = 0xbdddd0; break;
    case PDX_V3_DATA_U64: getter = 0x234a940; break;
    case PDX_V3_DATA_FLOAT: getter = 0x76ebf0; break;
    case PDX_V3_DATA_DOUBLE: getter = 0xbddbf0; break;
    case PDX_V3_DATA_FIXED: getter = 0x76eb90; break;
    case PDX_V3_DATA_STRING: getter = 0x63e0a0; break;
    case PDX_V3_DATA_UTF8_INPUT: getter = 0x20d22f0; break;
    default: return 0;
    }
    return reinterpret_cast<EmptyType>(base() + getter)();
}
void copy_payload(void* destination, const void* payload, uintptr_t type) {
    using TypedCopyFn = void* (__fastcall*)(void*, const void*, uintptr_t);
    reinterpret_cast<TypedCopyFn>(base() + 0xbdf700)(destination, payload, type);
}
DWORD any_input(const PdxV3DataInput* input, uint32_t bytes,
                PdxV3DataAny* value, ValueNode* owner) {
    if (!input || !value || bytes < sizeof(*input)) return ERROR_INVALID_PARAMETER;
    memset(value->bytes, 0, sizeof(value->bytes));
    const auto empty = empty_type(); memcpy(value->bytes, &empty, 8);
    if (input->kind == PDX_V3_DATA_EMPTY) return ERROR_SUCCESS;
    if (input->kind == PDX_V3_DATA_RETAINED_INPUT) {
        auto* source = find_value(input->value_handle);
        if (!source) return ERROR_INVALID_HANDLE;
        if (!world_matches(source)) return ERROR_NOT_READY;
        if (owner && owner->context_world && source->context_world
            && owner->context_world != source->context_world) return ERROR_NOT_READY;
        if (owner) {
            owner->dependencies.push_back(source); ++source->references;
            if (source->context_world) owner->context_world = source->context_world;
        }
        copy_any(value->bytes, source->value); return ERROR_SUCCESS;
    }
    const auto type = primitive_type(input->kind);
    if (!type) return ERROR_NOT_SUPPORTED;
    if (input->kind == PDX_V3_DATA_STRING || input->kind == PDX_V3_DATA_UTF8_INPUT) {
        if (input->string_bytes > bytes - sizeof(*input)) return ERROR_INVALID_PARAMETER;
        // Both native string types use the proven32B storage constructor and
        // destructor; their distinct Any singleton selects native type semantics.
        struct NativeStringOwner {
            alignas(8) unsigned char value[32]{};
            ~NativeStringOwner() { reinterpret_cast<StorageFn>(base() + 0x61f810)(value); }
        } string;
        using CreateString = void* (__fastcall*)(void*, const void*, uint64_t);
        reinterpret_cast<CreateString>(base() + 0x61c420)(string.value,
            reinterpret_cast<const unsigned char*>(input) + sizeof(*input), input->string_bytes);
        copy_payload(value->bytes, string.value, type);
    } else copy_payload(value->bytes, &input->raw_bits, type);
    return ERROR_SUCCESS;
}
}
DWORD WINAPI PdxV3DataAnyInput(const PdxV3DataInput* input, uint32_t bytes, PdxV3DataAny* value) {
    // A retained value can contain borrowed game/adapter buffers. Low-level callers
    // must keep its source handle alive; high-level action nodes record this dependency.
    try { ValueLock lock; return any_input(input, bytes, value, nullptr); }
    catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}

void WINAPI PdxV3DataDestroyAny(PdxV3DataAny* v) {
    if (!v) return;
    const bool heap = (v->bytes[0] & 1) != 0;
    const auto payload = get<uintptr_t>(v->bytes, 8);
    reinterpret_cast<StorageFn>(base() + any_dtor_rva)(v->bytes);
    if (heap && payload) reinterpret_cast<void (__fastcall*)(uintptr_t)>(base() + engine_free_rva)(payload);
    memset(v->bytes, 0, sizeof(v->bytes));
}
namespace {
DWORD snapshot_copied_value(PdxV3DataRequest* r, const PdxV3DataAny& any,
    std::vector<unsigned char>* owned_bytes = nullptr) {
    if (!owned_bytes) {
        auto probe = *r; probe.output_bytes = 0;
        const auto error = snapshot_value(&probe, any, true);
        if (error) return error;
        if (probe.kind < PDX_V3_DATA_EMPTY || probe.kind > PDX_V3_DATA_STRING)
            return ERROR_NOT_SUPPORTED;
    }
    const auto result = snapshot_value(r, any, true, owned_bytes);
    r->factory = r->object = r->value_handle = r->any_type = r->any_vtable = r->payload_address = 0;
    memset(r->raw_any, 0, sizeof(r->raw_any));
    if (r->kind == PDX_V3_DATA_STRING || r->kind == PDX_V3_DATA_EMPTY) r->raw_bits = 0;
    return result;
}
// Page-only reusable bindings/properties, owned by the enclosing Access/page.
struct PageEvaluation {
    ValueNode* bindings;
    std::vector<unsigned char>* bytes;
    const std::vector<const PdxV3DataAny*>* arguments = nullptr;
    // Original prepared native scope, used only by a same-callback consumer.
    // It is not a public raw input, Any retag, or lifetime extension.
    const PdxV3DataScope* borrowed_receiver = nullptr;
    PdxV3DataDomainTypeCache* domain_types = nullptr;
};
DWORD prepare_captured(PdxV3DataProperty* property, PdxV3DataArray* scopes,
    PdxV3DataArray* args, PdxV3DataScopeValue* context, int32_t* type,
    const std::vector<const PdxV3DataAny*>& captured,
    PdxV3DataScopeValue* initial_receiver = nullptr) {
    const auto status = PdxV3DataCapturedReferences(args, captured);
    if (status) return status;
    // Do not call outer3a79b80: it would replace frozen refs with lazy programs.
    PdxV3DataCapturedReceiver(property, scopes, context, type, [] {
        return *reinterpret_cast<const int32_t*>(reinterpret_cast<EmptyType>(base() + 0x626b80)());
    });
    if (initial_receiver) *initial_receiver = *context;
    const auto* expression = property->bytes + 24;
    using InnerPrepare = bool (__fastcall*)(const void*, PdxV3DataScopeValue*, int32_t*, PdxV3DataArray*);
    return reinterpret_cast<InnerPrepare>(base() + 0x3a78cf0)(expression, context, type, args)
        ? ERROR_SUCCESS : ERROR_INVALID_DATA;
}
// Caller holds ValueLock through every temporary's destruction.
DWORD run_expression_locked(PdxV3DataRequest* r, PdxV3DataActionRequest* action,
    uint64_t source_world = 0, PdxV3DataBinding* element = nullptr,
    ValueNode* source = nullptr, bool copied_only = false,
    PropertyOwner* precompiled = nullptr, PageEvaluation* page = nullptr,
    bool source_index_action = false) {
    std::unique_ptr<PropertyOwner> compiled_owner(page ? nullptr : precompiled);
    // A bounded page owns fresh global/application sources even when no world
    // exists. Its external inputs still need the same real ownership as an
    // in-world page; legacy world0 must not become an ownership credential.
    const bool guarded_roots = source_world != 0 || page != nullptr || source_index_action;
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    if (action && (r->size < sizeof(*action) || r->flags != PDX_V3_DATA_EXPLICIT_ACTION
        || action->input_bytes < sizeof(PdxV3DataInput)
        || !request_range(r, action->input_offset, action->input_bytes)))
        return r->error = ERROR_INVALID_PARAMETER;
    if (action) { action->compiled_mode = action->invoked = action->accepted = 0;
        if (r->text_offset < sizeof(*action) || r->bindings_offset < sizeof(*action)
            || action->input_offset < sizeof(*action)
            || (r->output_bytes && r->output_offset < sizeof(*action)))
            return r->error = ERROR_INVALID_PARAMETER; }
    reset_output(r); r->value_handle = 0;
    if (!r->factory) return r->error = ERROR_NOT_READY;
    if (!request_range(r, r->text_offset, r->text_bytes)
        || (r->bindings_count && !request_range(r, r->bindings_offset,
                            uint64_t(r->bindings_count) * sizeof(PdxV3DataBindingInput))))
        return r->error = ERROR_INVALID_PARAMETER;
    auto* node = new ValueNode;
    std::unique_ptr<ValueNode> pending(node);
    node->property = page ? precompiled : compiled_owner.release();
    node->owns_property = !page;
    if (source_world && !page && !source_index_action) {
        const auto app = get<uintptr_t>(base(), app_rva);
        if (!app || get<uintptr_t>(app, 0x608) != source_world)
            return r->error = ERROR_NOT_READY;
        node->context_world = source_world; // BEFORE compile/getter/production.
    }
    if (page || source_index_action) node->context_world = source_world; // observed in this Access
    if (source) { node->dependencies.push_back(source); ++source->references; }
    node->property = precompiled ? precompiled : new PropertyOwner;
    auto& property = *node->property;
    if (!precompiled) {
        const StringView expression{reinterpret_cast<const char*>(r) + r->text_offset,
            static_cast<int32_t>(r->text_bytes), 0, {0,0,0}};
        if (!reinterpret_cast<CompileFn>(base() + compile_rva)(
            reinterpret_cast<void*>(r->factory), &expression, property.value.bytes, 1, nullptr))
            return r->error = ERROR_INVALID_DATA;
    }
    if (action) {
        action->compiled_mode = get<uint32_t>(property.value.bytes, 56);
        if (action->compiled_mode != 3 && action->compiled_mode != 4)
            return r->error = ERROR_NOT_SUPPORTED;
    }
    const auto count = get<uint32_t>(property.value.bytes, 196);
    const auto required = get<uintptr_t>(property.value.bytes, 184);
    node->scopes.resize(count);
    auto& scopes = node->scopes;
    // One 40-byte CPdxData parameter-reference per compiled expression/constant.
    const auto parameter_count = get<uint32_t>(property.value.bytes, 12)
                               + get<uint32_t>(property.value.bytes, 172);
    // Raw scopes have no general lease across argument getters. A parameter
    // expression could rebuild the backing collection during Prepare.
    if (((element && element->bytes[48] <= 1) || (page && page->borrowed_receiver)) && parameter_count) {
        if (!(page && page->arguments)) return r->error = ERROR_NOT_SUPPORTED;
    }
    node->refs.resize(static_cast<size_t>(parameter_count) * 40);
    auto& refs = node->refs;
    node->scope_array = {reinterpret_cast<uintptr_t>(scopes.data()), count, count, 0};
    node->args = {reinterpret_cast<uintptr_t>(refs.data()), parameter_count, parameter_count, 0};
    auto& scope_array = node->scope_array; auto& args = node->args;
    const auto* inputs = reinterpret_cast<const PdxV3DataBindingInput*>(
        reinterpret_cast<unsigned char*>(r) + r->bindings_offset);
    // Keep each engine-created Any wrapper alive until evaluation and snapshot.
    auto* binding_owner = page ? page->bindings : node;
    auto& bindings = binding_owner->bindings;
    bool used_element = false;
    for (uint32_t i = 0; i < count; ++i) {
        const auto id = get<int32_t>(required, 8ull * i);
        scopes[i].type_id = id; scopes[i].is_any = 1;
        if (page && page->borrowed_receiver && id == page->borrowed_receiver->type_id) {
            scopes[i] = *page->borrowed_receiver; // original object/is_any, exact registered terminal type
            continue;
        }
        if (element && id == get<int32_t>(element->bytes, 56)) {
            // Original provider Binding0/1 is a raw scope, not an Any40.
            // BindingScope chooses object/is_any. Never overwrite that result.
            const auto error = binding_scope(element, &scopes[i]);
            if (error) return r->error = error;
            if (scopes[i].type_id != id) return r->error = ERROR_INVALID_DATA;
            used_element = true; continue;
        }
        if (page) {
            const auto cached = std::find_if(binding_owner->scopes.begin(), binding_owner->scopes.end(),
                [id](const PdxV3DataScope& scope) { return scope.type_id == id; });
            if (cached != binding_owner->scopes.end()) { scopes[i] = *cached; continue; }
        }
        const PdxV3DataBindingInput* match = nullptr;
        for (uint32_t j = 0; j < r->bindings_count; ++j) if (inputs[j].type_id == id) { match = inputs + j; break; }
        if (!match) return r->error = ERROR_NOT_FOUND;
        if (match->flags == 1) {
            auto* held = find_value(match->object);
            if (!held) return r->error = ERROR_INVALID_HANDLE;
            if (!world_matches(held)) return r->error = ERROR_NOT_READY;
            // New guarded production cannot bless an old provenance-free owner.
            if (guarded_roots && !held->context_world) return r->error = ERROR_INVALID_STATE;
            if (node->context_world && held->context_world && node->context_world != held->context_world)
                return r->error = ERROR_NOT_READY;
            binding_owner->dependencies.push_back(held); ++held->references;
            if (held->context_world) node->context_world = held->context_world;
            const auto record = type_record(id);
            if (!record || !get<uintptr_t>(record, 224)) return r->error = ERROR_NOT_SUPPORTED;
            auto pending_binding = std::make_unique<BindingOwner>();
            auto* binding = pending_binding.get(); bindings.push_back(binding); pending_binding.release();
            const auto empty = empty_type(); memcpy(binding->value.bytes, &empty, 8);
            copy_any(binding->value.bytes, held->value);
            const auto converter = get<uintptr_t>(record, 224);
            memcpy(binding->value.bytes + 40, &converter, 8);
            memcpy(binding->value.bytes + 56, &id, 4); binding->value.bytes[48] = 2;
            const auto error = binding_scope(&binding->value, &scopes[i]);
            if (error) return r->error = error;
        } else if (match->flags == 0 || binding_domain(match->flags)) {
            if (guarded_roots && match->flags == 0) return r->error = ERROR_NOT_SUPPORTED;
            struct WrapperOwner { uintptr_t pointer = 0; ~WrapperOwner() { release_wrapper(pointer); } } owned;
            uintptr_t wrapper = match->object;
            const auto domain = binding_domain(match->flags);
            if (domain) {
                const auto* config = id_domain(domain);
                if (!config) return r->error = ERROR_NOT_SUPPORTED;
                const auto actual_type = page && page->domain_types
                    ? page->domain_types->lookup(domain, [&] { return PdxV3DataFindType(config->name); })
                    : PdxV3DataFindType(config->name);
                if (id != actual_type || match->object > UINT32_MAX)
                    return r->error = ERROR_INVALID_PARAMETER;
                uint64_t wrapper_world = 0;
                const auto error = wrapper_for_id(domain, static_cast<uint32_t>(match->object), &owned.pointer, &wrapper_world);
                if (error) return r->error = error;
                if (node->context_world && wrapper_world && node->context_world != wrapper_world)
                    return r->error = ERROR_NOT_READY;
                if (wrapper_world) node->context_world = wrapper_world;
                wrapper = owned.pointer;
            }
            auto pending_binding = std::make_unique<BindingOwner>();
            auto* binding = pending_binding.get(); bindings.push_back(binding); pending_binding.release();
            auto error = bind_object(wrapper, id, &binding->value);
            if (owned.pointer) { release_wrapper(owned.pointer); owned.pointer = 0; }
            if (error) return r->error = error;
            error = binding_scope(&binding->value, &scopes[i]);
            if (error) return r->error = error;
        } else return r->error = ERROR_INVALID_PARAMETER;
        if (page) binding_owner->scopes.push_back(scopes[i]);
    }
    if (element && !used_element) {
        // A copied page column may be a literal or use another supplied root.
        // Legacy element/action calls still require their selected receiver.
        if (!(page && copied_only)) return r->error = ERROR_INVALID_PARAMETER;
    }
    PdxV3DataScopeValue context{}; int32_t context_type = -1;
    const auto empty = empty_type();
    if (get<uintptr_t>(property.value.bytes, 120) == empty) {
        const auto error = page && page->arguments
            ? prepare_captured(&property.value, &scope_array, &args, &context, &context_type, *page->arguments)
            : prepare(&property.value, &scope_array, &args, &context, &context_type);
        if (error) return r->error = error;
    }
    DWORD error = ERROR_SUCCESS;
    int32_t retained_input_type_id = -1; uintptr_t retained_input_any_type = 0;
    if (action) {
        const auto* input = reinterpret_cast<const PdxV3DataInput*>(
            reinterpret_cast<const unsigned char*>(r) + action->input_offset);
        action->input_kind = input->kind;
        if (input->kind == PDX_V3_DATA_RETAINED_INPUT) {
            const auto* held = find_value(input->value_handle);
            if (!held) return r->error = ERROR_INVALID_HANDLE;
            if (guarded_roots && !held->context_world) return r->error = ERROR_INVALID_STATE;
            retained_input_type_id = held->type_id;
            retained_input_any_type = get<uintptr_t>(held->value.bytes) & ~uintptr_t(1);
        }
        error = any_input(input, action->input_bytes, &node->value, node);
        if (error) return r->error = error;
        action->invoked = 1;
        error = action_prepared(&property.value, &context, &args, &node->value,
            r->flags, &action->accepted);
    } else error = evaluate_prepared(&property.value, &context, &args, &node->value, page != nullptr);
    if (error) return r->error = error;
    if (copied_only) {
        r->type_id = action ? -1 : get<int32_t>(property.value.bytes, 48);
        return r->error = snapshot_copied_value(r, node->value, page ? page->bytes : nullptr);
    }
    node->handle = ++next_handle; node->next = values; values = node; r->value_handle = node->handle;
    node->type_id = action
        ? (retained_input_any_type && (get<uintptr_t>(node->value.bytes) & ~uintptr_t(1)) == retained_input_any_type
            ? retained_input_type_id : -1)
        : get<int32_t>(property.value.bytes, 48);
    r->type_id = node->type_id;
    pending.release();
    // Keep the Any and all adapter-owned dependencies. Borrowed game buffers may
    // still change when the simulation changes; snapshot copies are independent.
    return r->error = snapshot_value(r, node->value);
}
DWORD run_expression(PdxV3DataRequest* r, PdxV3DataActionRequest* action) {
    ValueLock lock;
    return run_expression_locked(r, action);
}
}
DWORD WINAPI PdxV3DataEvaluate(PdxV3DataRequest* r) {
    try { return run_expression(r, nullptr); }
    catch (...) { return r ? r->error = ERROR_UNHANDLED_EXCEPTION : ERROR_UNHANDLED_EXCEPTION; }
}
namespace {
uintptr_t creation_payload(const ValueNode* source);
int32_t creation_count(uintptr_t payload);
static_assert(sizeof(PdxV3DataProjectionRequest) == 392);
static_assert(sizeof(PdxV3DataScopedProjectionRequest) == 408);
static_assert(sizeof(PdxV3DataCreationStep) == 40);
DWORD run_scoped_projection(PdxV3DataScopedProjectionRequest* nested);
DWORD run_projection(PdxV3DataProjectionRequest* projection) {
    if (!projection) return ERROR_INVALID_PARAMETER;
    auto* r = &projection->result;
    if (r->size < sizeof(*projection)) return r->error = ERROR_INVALID_PARAMETER;
    struct CopiedBoundary {
        PdxV3DataRequest* request;
        ~CopiedBoundary() {
            request->factory = request->object = request->value_handle = 0;
            request->any_type = request->any_vtable = request->payload_address = 0;
            memset(request->raw_any, 0, sizeof(request->raw_any));
            if (request->kind == PDX_V3_DATA_RAW || request->kind > PDX_V3_DATA_STRING) {
                request->kind = PDX_V3_DATA_EMPTY;
                request->raw_bits = request->payload_size = request->snapshot_bytes = request->total = 0;
            }
        }
    } copied{r};
    projection->source_produced = projection->count_valid = projection->source_count = 0;
    if ((r->action != PDX_V3_DATA_EVALUATE_COPIED && r->action != PDX_V3_DATA_SOURCE_COUNT)
        || r->flags || !projection->source_world || projection->reserved
        || r->text_offset < sizeof(*projection) || !r->text_bytes || r->text_bytes > INT32_MAX
        || !request_range(r, r->text_offset, r->text_bytes)
        || r->bindings_offset < sizeof(*projection)
        || (r->bindings_count && !request_range(r, r->bindings_offset,
            uint64_t(r->bindings_count) * sizeof(PdxV3DataBindingInput)))
        || r->output_offset < sizeof(*projection)
        || !request_range(r, r->output_offset, r->output_bytes))
        return r->error = ERROR_INVALID_PARAMETER;
    reset_output(r); r->value_handle = 0;
    const auto app = get<uintptr_t>(base(), app_rva);
    if (!r->factory || !app || get<uintptr_t>(app, 0x608) != projection->source_world)
        return r->error = ERROR_NOT_READY;
    ValueLock lock;
    if (r->action == PDX_V3_DATA_EVALUATE_COPIED) {
        const auto error = run_expression_locked(r, nullptr, projection->source_world,
            nullptr, nullptr, true);
        // MORE_DATA is a completed getter with an explicitly partial byte copy.
        if (!error || error == ERROR_MORE_DATA) projection->source_produced = 1;
        return error;
    }
    // Source-only root has no external output. Its Any, property, argument
    // contexts and Creation descriptor all die before ValueLock/Access exit.
    const auto output_bytes = r->output_bytes;
    struct CapacityRestore {
        PdxV3DataRequest* request; uint32_t bytes;
        ~CapacityRestore() { request->output_bytes = bytes; }
    } restore{r, output_bytes};
    r->output_bytes = 0;
    PdxV3DataScopedValue source_owner{&r->value_handle, release_value_locked};
    DWORD status = ERROR_SUCCESS;
    pdx::data_projection_core::CountObservation observation;
    const auto completed = pdx::data_projection_core::observe_source_count(
        [&]() {
            status = run_expression_locked(r, nullptr, projection->source_world);
            return status == ERROR_SUCCESS;
        },
        [&](uint64_t& count) {
            auto* source = find_value(r->value_handle);
            if (!source || source->context_world != projection->source_world || !world_matches(source)) {
                status = ERROR_NOT_READY; return false;
            }
            const auto payload = creation_payload(source);
            if (!payload) { status = ERROR_NOT_SUPPORTED; return false; }
            const auto native_count = creation_count(payload);
            if (native_count < 0) { status = ERROR_INVALID_DATA; return false; }
            count = static_cast<uint32_t>(native_count); return true;
        }, observation);
    projection->source_produced = observation.source_produced;
    projection->count_valid = observation.count_valid;
    projection->source_count = static_cast<uint32_t>(observation.count);
    if (!completed) return r->error = status;
    r->kind = PDX_V3_DATA_EMPTY; r->total = r->snapshot_bytes = r->payload_size = 0;
    r->raw_bits = 0;
    return r->error = ERROR_SUCCESS;
}
}
DWORD WINAPI PdxV3DataProjection(PdxV3DataProjectionRequest* r) {
    try {
        if (r && r->result.size >= sizeof(PdxV3DataRequest)
            && (r->result.action == PDX_V3_DATA_EVALUATE_SCOPED
                || r->result.action == PDX_V3_DATA_SCOPED_SOURCE_COUNT))
            return run_scoped_projection(reinterpret_cast<PdxV3DataScopedProjectionRequest*>(r));
        return run_projection(r);
    }
    catch (...) { return r ? r->result.error = ERROR_UNHANDLED_EXCEPTION : ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3DataSnapshotCopied(PdxV3DataRequest* r, const PdxV3DataAny* value) {
    if (!r || r->size < sizeof(*r) || !value) return ERROR_INVALID_PARAMETER;
    struct CopiedBoundary {
        PdxV3DataRequest* request;
        ~CopiedBoundary() {
            request->factory = request->object = request->value_handle = 0;
            request->any_type = request->any_vtable = request->payload_address = 0;
            memset(request->raw_any, 0, sizeof(request->raw_any));
        }
    } copied{r};
    try { ValueLock lock; return r->error = snapshot_copied_value(r, *value); }
    catch (...) { return r->error = ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3DataInvokeAction(PdxV3DataActionRequest* r) {
    if (!r) return ERROR_INVALID_PARAMETER;
    try { return run_expression(&r->result, r); }
    catch (...) { return r->result.error = ERROR_UNHANDLED_EXCEPTION; }
}

DWORD WINAPI PdxV3DataInvokeActionSequence(PdxV3DataActionSequenceRequest* sequence) {
    if (!sequence) return ERROR_INVALID_PARAMETER;
    if (sequence->size < sizeof(*sequence)) return ERROR_INVALID_PARAMETER;
    sequence->completed = sequence->stopped = 0;
    const auto span = [sequence](uint32_t offset, uint32_t bytes) {
        return offset <= sequence->size && bytes <= sequence->size - offset;
    };
    constexpr uint32_t maximum_actions = 32; // V3 packet budget, not shared policy
    if (!sequence->count || sequence->count > maximum_actions
        || sequence->steps_offset < sizeof(*sequence)
        || !span(sequence->steps_offset, sequence->count * sizeof(PdxV3DataActionSequenceStep)))
        return sequence->error = ERROR_INVALID_PARAMETER;
    auto* bytes = reinterpret_cast<unsigned char*>(sequence);
    auto* steps = reinterpret_cast<PdxV3DataActionSequenceStep*>(bytes + sequence->steps_offset);
    const auto table_end = sequence->steps_offset + sequence->count * sizeof(*steps);
    struct PacketSpan { uint32_t begin, end; } packets[maximum_actions]{};
    for (uint32_t index = 0; index < sequence->count; ++index) {
        const auto& step = steps[index];
        if (step.continuation > 1 || step.request_offset < table_end
            || !span(step.request_offset, sizeof(PdxV3DataActionRequest)))
            return sequence->error = ERROR_INVALID_PARAMETER;
        const auto* action = reinterpret_cast<const PdxV3DataActionRequest*>(bytes + step.request_offset);
        if (action->result.action != PDX_V3_DATA_INVOKE_ACTION
            || action->result.size < sizeof(*action) || !span(step.request_offset, action->result.size))
            return sequence->error = ERROR_INVALID_PARAMETER;
        packets[index] = {step.request_offset, step.request_offset + action->result.size};
    }
    // Original packets can write any byte in their own declared allocation.
    // Reject every overlap before invoking even the first action. Sort only
    // local spans: the descriptor order and original action receipts stay intact.
    std::sort(packets, packets + sequence->count,
        [](const PacketSpan& left, const PacketSpan& right) { return left.begin < right.begin; });
    for (uint32_t index = 1; index < sequence->count; ++index)
        if (packets[index].begin < packets[index - 1].end)
            return sequence->error = ERROR_INVALID_PARAMETER;
    sequence->error = ERROR_IO_PENDING;
    struct Adapter {
        PdxV3DataActionSequenceRequest& sequence;
        PdxV3DataActionSequenceStep* steps;
        unsigned char* bytes;
        DWORD first_error = ERROR_SUCCESS;
        PdxV3DataActionRequest* action(size_t index) {
            return reinterpret_cast<PdxV3DataActionRequest*>(bytes + steps[index].request_offset);
        }
        void invoke(size_t index) {
            const auto error = PdxV3DataInvokeAction(action(index));
            if (!first_error && error) first_error = error;
        }
        void completed(size_t count) { sequence.completed = static_cast<uint32_t>(count); }
        bool continue_after(size_t index) {
            const auto* result = action(index);
            // A successful handler may have a later snapshot/metadata error.
            // Continuation reads the ORIGINAL flags, never error == 0.
            return PdxV3DataContinueAction(*result, steps[index].continuation);
        }
        void stop() { sequence.stopped = 1; }
    } adapter{*sequence, steps, bytes};
    pdx::dependent_sequence::run(sequence->count, adapter);
    return sequence->error = adapter.first_error;
}
void WINAPI PdxV3DataInvokeActionSequenceInterface(void*, void* opaque) {
    PdxV3DataInvokeActionSequence(static_cast<PdxV3DataActionSequenceRequest*>(opaque));
}


namespace {
DWORD snapshot_request(PdxV3DataRequest* r) {
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    reset_output(r); ValueLock lock;
    const auto* node = find_value(r->value_handle);
    if (!node) return r->error = ERROR_INVALID_HANDLE;
    if (!world_matches(node)) return r->error = ERROR_NOT_READY;
    r->type_id = node->type_id;
    return r->error = snapshot_value(r, node->value);
}
}
DWORD WINAPI PdxV3DataSnapshot(PdxV3DataRequest* r) {
    try { return snapshot_request(r); }
    catch (...) { return r ? r->error = ERROR_UNHANDLED_EXCEPTION : ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3DataRelease(uint64_t* handle) {
    if (!handle || !*handle) return ERROR_INVALID_PARAMETER;
    ValueLock lock;
    return release_value_locked(handle);
}

namespace {
uintptr_t creation_payload(const ValueNode* node) {
    if (!node) return 0;
    const auto tagged = get<uintptr_t>(node->value.bytes);
    const auto type = tagged & ~uintptr_t(1);
    // Exact native Any singleton identity is checked by real GUI consumer35413e0.
    using CreationType = uintptr_t (__fastcall*)();
    if (type != reinterpret_cast<CreationType>(base() + 0x1821dd0)()) return 0;
    const auto payload = tagged & 1 ? get<uintptr_t>(node->value.bytes, 8)
        : reinterpret_cast<uintptr_t>(node->value.bytes + 8);
    return payload && get<uintptr_t>(payload) ? payload : 0;
}
int32_t creation_count(uintptr_t payload) {
    const auto provider = get<uintptr_t>(payload);
    using CountFn = int32_t (__fastcall*)(void*, const void*);
    return reinterpret_cast<CountFn>(get<uintptr_t>(get<uintptr_t>(provider)))(
        reinterpret_cast<void*>(provider), reinterpret_cast<void*>(payload + 8));
}
DWORD creation_element_observed(uintptr_t payload, int32_t index, PdxV3DataBinding* binding) {
    if (!binding) return ERROR_INVALID_PARAMETER;
    memset(binding->bytes, 0, sizeof(binding->bytes)); binding->bytes[48] = 0xff;
    const auto provider = get<uintptr_t>(payload);
    using ElementFn = void* (__fastcall*)(void*, void*, const void*, int32_t);
    reinterpret_cast<ElementFn>(get<uintptr_t>(get<uintptr_t>(provider), 24))(
        reinterpret_cast<void*>(provider), binding->bytes,
        reinterpret_cast<void*>(payload + 8), index);
    return binding->bytes[48] == 0xff ? ERROR_INVALID_DATA : ERROR_SUCCESS;
}
DWORD creation_element(uintptr_t payload, int32_t index, PdxV3DataBinding* binding) {
    if (!binding) return ERROR_INVALID_PARAMETER;
    memset(binding->bytes, 0, sizeof(binding->bytes)); binding->bytes[48] = 0xff;
    const auto count = creation_count(payload);
    if (index < 0 || index >= count) return ERROR_NOT_FOUND;
    return creation_element_observed(payload, index, binding);
}
}
namespace {
std::vector<unsigned char> expression_request(const PdxV3DataRequest* outer,
    uint32_t text_offset, uint32_t text_bytes) {
    const auto bindings_offset = (sizeof(PdxV3DataRequest) + text_bytes + 7) & ~size_t(7);
    const auto binding_bytes = size_t(outer->bindings_count) * sizeof(PdxV3DataBindingInput);
    std::vector<unsigned char> bytes(bindings_offset + binding_bytes);
    auto* r = reinterpret_cast<PdxV3DataRequest*>(bytes.data());
    *r = {}; r->size = static_cast<uint32_t>(bytes.size()); r->action = PDX_V3_DATA_EVALUATE;
    r->type_id = -1; r->text_offset = sizeof(*r); r->text_bytes = text_bytes;
    r->bindings_offset = static_cast<uint32_t>(bindings_offset); r->bindings_count = outer->bindings_count;
    r->output_offset = r->size;
    memcpy(bytes.data() + r->text_offset, reinterpret_cast<const unsigned char*>(outer) + text_offset, text_bytes);
    if (binding_bytes) memcpy(bytes.data() + bindings_offset,
        reinterpret_cast<const unsigned char*>(outer) + outer->bindings_offset, binding_bytes);
    return bytes;
}
DWORD compile_element_expression(const PdxV3DataRequest* r, PropertyOwner* property) {
    if (!r || !property || r->text_bytes > INT32_MAX) return ERROR_INVALID_PARAMETER;
    const StringView expression{reinterpret_cast<const char*>(r) + r->text_offset,
        static_cast<int32_t>(r->text_bytes), 0, {0,0,0}};
    return reinterpret_cast<CompileFn>(base() + compile_rva)(
        reinterpret_cast<void*>(PdxV3DataFactory()), &expression, property->value.bytes, 1, nullptr)
        ? ERROR_SUCCESS : ERROR_INVALID_DATA;
}

// These additive actions accept one canonical, disjoint layout. Validate every
// byte span before creating a native property or obtaining a borrowed element.
bool scoped_expression_valid(const PdxV3DataRequest* r, uint32_t offset, uint32_t bytes) {
    if (!bytes || bytes > INT32_MAX || !request_range(r, offset, bytes)) return false;
    const auto* text = reinterpret_cast<const char*>(r) + offset;
    return !memchr(text, 0, bytes)
        && MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text,
            static_cast<int>(bytes), nullptr, 0) > 0;
}
struct ScopedProjectionAdapter {
    using Status = DWORD;
    using Binding = BindingOwner;
    struct Context { PdxV3DataBinding* binding = nullptr; ValueNode* owner = nullptr; };
    struct Source {
        uint64_t handle = 0;
        PdxV3DataScopedValue lifetime{&handle, release_value_locked};
        ValueNode* node = nullptr;
        uintptr_t payload = 0;
        Source() = default;
        Source(const Source&) = delete;
        Source& operator=(const Source&) = delete;
    };
    PdxV3DataScopedProjectionRequest* nested;
    PdxV3DataRequest* result;
    PdxV3DataCreationStep* steps;
    std::vector<std::vector<unsigned char>> requests;
    std::vector<std::unique_ptr<PropertyOwner>> properties;
    std::unique_ptr<PropertyOwner> final_property;

    explicit ScopedProjectionAdapter(PdxV3DataScopedProjectionRequest* request)
        : nested(request), result(&request->projection.result),
          steps(reinterpret_cast<PdxV3DataCreationStep*>(
              reinterpret_cast<unsigned char*>(request) + request->steps_offset)) {}
    bool ok(Status status) const { return status == ERROR_SUCCESS; }
    Context root_context() const { return {}; }
    Context context(Source& source, Binding& binding) const { return {&binding.value, source.node}; }
    Status compile_all(size_t depth) {
        // Complete ALL compilation before core traversal constructs any Source.
        requests.resize(depth); properties.resize(depth);
        for (size_t i = 0; i < depth; ++i) {
            requests[i] = expression_request(result, steps[i].source_text_offset, steps[i].source_text_bytes);
            properties[i] = std::make_unique<PropertyOwner>();
            const auto status = compile_element_expression(
                reinterpret_cast<PdxV3DataRequest*>(requests[i].data()), properties[i].get());
            if (status) return status;
        }
        final_property = std::make_unique<PropertyOwner>();
        return compile_element_expression(result, final_property.get());
    }
    Status produce(size_t level, Context previous, Source& source) {
        auto* request = reinterpret_cast<PdxV3DataRequest*>(requests[level].data());
        // Capture publication even if a later snapshot throws, before Source
        // unwinds. The shared mechanism receives no V3 request/layout facts.
        const auto status = pdx::data_projection_core::capture_produced(source.handle,
            [&] { return run_expression_locked(request, nullptr, nested->projection.source_world,
                previous.binding, previous.owner, false, properties[level].release()); },
            [request]() noexcept { return request->value_handle; });
        if (status) return status;
        steps[level].source_produced = 1;
        source.node = find_value(request->value_handle);
        if (!source.node || source.node->context_world != nested->projection.source_world
            || !world_matches(source.node)) return ERROR_NOT_READY;
        source.payload = creation_payload(source.node);
        return source.payload ? ERROR_SUCCESS : ERROR_NOT_SUPPORTED;
    }
    static Status count_step(PdxV3DataCreationStep& step, Source& source) {
        const auto count = creation_count(source.payload);
        if (count < 0) return ERROR_INVALID_DATA;
        step.source_count = static_cast<uint32_t>(count); step.count_valid = 1;
        if (step.expected_count != UINT32_MAX && step.expected_count != step.source_count)
            return ERROR_RETRY;
        return step.index < step.source_count ? ERROR_SUCCESS : ERROR_NOT_FOUND;
    }
    Status count_and_check(size_t level, Source& source) { return count_step(steps[level], source); }
    static Status select_step(PdxV3DataCreationStep& step, Source& source, Binding& binding) {
        // The count was just observed inside this same original Access. Do not
        // ask the provider for count twice, retain/retag its Binding or replay.
        const auto status = creation_element_observed(source.payload,
            static_cast<int32_t>(step.index), &binding.value);
        if (status) return status;
        step.element_obtained = 1;
        step.element_type_id = get<int32_t>(binding.value.bytes, 56);
        step.binding_mode = binding.value.bytes[48];
        if (step.element_type_id < 0 || step.binding_mode > 3) return ERROR_INVALID_DATA;
        return ERROR_SUCCESS;
    }
    Status select(size_t level, Source& source, Binding& binding) {
        const auto status = select_step(steps[level], source, binding);
        if (!status) nested->completed_steps = static_cast<uint32_t>(level + 1);
        return status;
    }
    Status finish(Context previous) {
        if (result->action == PDX_V3_DATA_EVALUATE_SCOPED) {
            const auto status = run_expression_locked(result, nullptr, nested->projection.source_world,
                previous.binding, previous.owner, true, final_property.release());
            if (!status || status == ERROR_MORE_DATA) nested->projection.source_produced = 1;
            return status;
        }
        // Count's final source is fresh in the last selected binding. Use a
        // separate local request so its original Any never enters copied output.
        auto bytes = expression_request(result, result->text_offset, result->text_bytes);
        auto* request = reinterpret_cast<PdxV3DataRequest*>(bytes.data());
        Source source;
        auto status = pdx::data_projection_core::capture_produced(source.handle,
            [&] { return run_expression_locked(request, nullptr, nested->projection.source_world,
                previous.binding, previous.owner, false, final_property.release()); },
            [request]() noexcept { return request->value_handle; });
        if (status) return status;
        nested->projection.source_produced = 1;
        auto* node = find_value(request->value_handle);
        if (!node || node->context_world != nested->projection.source_world || !world_matches(node))
            return ERROR_NOT_READY;
        const auto payload = creation_payload(node);
        if (!payload) return ERROR_NOT_SUPPORTED;
        const auto count = creation_count(payload);
        if (count < 0) return ERROR_INVALID_DATA;
        nested->projection.source_count = static_cast<uint32_t>(count);
        nested->projection.count_valid = 1;
        result->kind = PDX_V3_DATA_EMPTY;
        return ERROR_SUCCESS;
    }
};
DWORD run_scoped_projection(PdxV3DataScopedProjectionRequest* nested) {
    if (!nested) return ERROR_INVALID_PARAMETER;
    auto* r = &nested->projection.result;
    if (r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    PdxV3DataCopiedBoundary copied{r};
    if (r->size < sizeof(*nested)) return r->error = ERROR_INVALID_PARAMETER;
    auto& projection = nested->projection;
    projection.source_produced = projection.count_valid = projection.source_count = 0;
    nested->completed_steps = 0;
    // Validate only the table structure before touching its output receipts.
    // Caller-supplied receipt bits must never survive a semantic rejection.
    if (nested->steps_count > PDX_V3_DATA_MAX_SCOPED_DEPTH
        || nested->steps_offset != sizeof(*nested)
        || !request_range(r, nested->steps_offset,
            uint64_t(nested->steps_count) * sizeof(PdxV3DataCreationStep)))
        return r->error = ERROR_INVALID_PARAMETER;
    auto* steps = reinterpret_cast<PdxV3DataCreationStep*>(
        reinterpret_cast<unsigned char*>(nested) + nested->steps_offset);
    uint64_t cursor = uint64_t(nested->steps_offset) + uint64_t(nested->steps_count) * sizeof(*steps);
    for (uint32_t i = 0; i < nested->steps_count; ++i) {
        auto& step = steps[i];
        step.source_produced = step.count_valid = step.source_count = step.element_obtained = 0;
        step.element_type_id = -1; step.binding_mode = UINT32_MAX;
    }
    if ((r->action != PDX_V3_DATA_EVALUATE_SCOPED && r->action != PDX_V3_DATA_SCOPED_SOURCE_COUNT)
        || r->flags || !projection.source_world || projection.reserved || nested->reserved
        || r->bindings_count)
        return r->error = ERROR_INVALID_PARAMETER;
    for (uint32_t i = 0; i < nested->steps_count; ++i) {
        const auto& step = steps[i];
        if (step.source_text_offset != cursor || step.index > INT32_MAX
            || (step.expected_count != UINT32_MAX && step.expected_count > INT32_MAX)
            || !scoped_expression_valid(r, step.source_text_offset, step.source_text_bytes))
            return r->error = ERROR_INVALID_PARAMETER;
        cursor += step.source_text_bytes;
    }
    if (cursor != r->text_offset || !scoped_expression_valid(r, r->text_offset, r->text_bytes))
        return r->error = ERROR_INVALID_PARAMETER;
    cursor = (cursor + r->text_bytes + 7) & ~uint64_t(7);
    if (cursor > UINT32_MAX || r->bindings_offset != cursor || r->output_offset != cursor
        || !request_range(r, r->output_offset, r->output_bytes)
        || cursor + r->output_bytes != r->size
        || (r->action == PDX_V3_DATA_SCOPED_SOURCE_COUNT && r->output_bytes))
        return r->error = ERROR_INVALID_PARAMETER;
    reset_output(r); r->value_handle = 0;
    const auto app = get<uintptr_t>(base(), app_rva);
    if (!r->factory || !app || get<uintptr_t>(app, 0x608) != projection.source_world)
        return r->error = ERROR_NOT_READY;
    ValueLock lock;
    // The Interface Access is still outermost. Core recursion destroys each
    // original element binding before its source owner on every return/throw.
    ScopedProjectionAdapter adapter(nested);
    return r->error = pdx::data_projection_core::run_scoped_chain(adapter, nested->steps_count);
}

DWORD run_element(PdxV3DataElementRequest* e) {
    if (!e) return ERROR_INVALID_PARAMETER;
    auto* r = &e->action.result;
    const bool source_index_action = r->action == PDX_V3_DATA_INVOKE_SOURCE_INDEX_ACTION;
    const bool array_source = r->action == PDX_V3_DATA_EVALUATE_ARRAY_ELEMENT
                           || r->action == PDX_V3_DATA_INVOKE_ARRAY_ELEMENT_ACTION;
    auto header = array_source ? sizeof(PdxV3DataArrayElementRequest) : sizeof(*e);
    if (r->size < header) return r->error = ERROR_INVALID_PARAMETER;
    auto* a = array_source ? &reinterpret_cast<PdxV3DataArrayElementRequest*>(e)->source : nullptr;
    auto* subobject = a && (a->flags & PDX_V3_DATA_ARRAY_ELEMENT_SUBOBJECT)
        ? reinterpret_cast<PdxV3DataArrayElementSubobject*>(reinterpret_cast<unsigned char*>(e) + header) : nullptr;
    if (subobject) {
        header += sizeof(*subobject);
        if (r->size < header) return r->error = ERROR_INVALID_PARAMETER;
    }
    auto* guard = a && (a->flags & PDX_V3_DATA_ARRAY_SOURCE_GUARD)
        ? reinterpret_cast<PdxV3DataSourceGuard*>(reinterpret_cast<unsigned char*>(e) + header) : nullptr;
    if (guard) {
        header += sizeof(*guard);
        if (r->size < header) return r->error = ERROR_INVALID_PARAMETER;
        guard->checked = guard->passed = 0;
    }
    if (a) { a->count = a->stages = 0; }
    const bool count_only = a && (a->flags & PDX_V3_DATA_ARRAY_COUNT_ONLY);
    const bool direct_context = a && (a->flags & PDX_V3_DATA_ARRAY_DIRECT_CONTEXT);
    struct CopyBoundary {
        PdxV3DataRequest* r;
        ~CopyBoundary() {
            r->factory = r->object = r->value_handle = r->any_type = r->any_vtable = r->payload_address = 0;
            memset(r->raw_any, 0, sizeof(r->raw_any));
        }
    } copied{r};
    e->action.compiled_mode = e->action.invoked = e->action.accepted = 0;
    e->condition_checked = e->condition_passed = e->source_count = 0;
    e->element_type_id = -1;
    const bool action = r->action == PDX_V3_DATA_INVOKE_ELEMENT_ACTION
                     || r->action == PDX_V3_DATA_INVOKE_ARRAY_ELEMENT_ACTION || source_index_action;
    if ((!action && r->action != PDX_V3_DATA_EVALUATE_ELEMENT
                 && r->action != PDX_V3_DATA_EVALUATE_ARRAY_ELEMENT)
        || r->flags != (action ? PDX_V3_DATA_EXPLICIT_ACTION : 0)
        || (!source_index_action && !e->source_world) || r->start > INT32_MAX
        || (count_only && (action || direct_context || guard || e->condition_bytes
            || r->text_bytes || e->source_text_bytes))
        || (direct_context && action)
        || (subobject && (count_only || !subobject->byte_offset
            || subobject->byte_offset % alignof(uint64_t) || subobject->reserved
            || !subobject->expected_element_vtable))
        || r->text_offset < header || !request_range(r, r->text_offset, r->text_bytes)
        || (!count_only && !r->text_bytes)
        || (!count_only && !direct_context && (e->source_text_offset < header || !e->source_text_bytes))
        || (direct_context && e->source_text_bytes)
        || e->source_text_bytes > INT32_MAX
        || !request_range(r, e->source_text_offset, e->source_text_bytes)
        || (r->bindings_count && (r->bindings_offset < header
            || !request_range(r, r->bindings_offset, uint64_t(r->bindings_count) * sizeof(PdxV3DataBindingInput))))
        || (e->condition_bytes && (e->condition_offset < header
            || !request_range(r, e->condition_offset, e->condition_bytes)))
        || (r->output_bytes && (r->output_offset < header
            || !request_range(r, r->output_offset, r->output_bytes)))
        || (action && (e->action.input_offset < header
            || e->action.input_bytes < sizeof(PdxV3DataInput)
            || !request_range(r, e->action.input_offset, e->action.input_bytes)))
        || (a && (a->root_text_offset < header || !a->root_text_bytes
            || a->root_text_bytes > INT32_MAX
            || !request_range(r, a->root_text_offset, a->root_text_bytes)
            || (a->flags & ~(PDX_V3_DATA_ARRAY_COUNT_ONLY | PDX_V3_DATA_ARRAY_DIRECT_CONTEXT
                           | PDX_V3_DATA_ARRAY_SOURCE_GUARD | PDX_V3_DATA_ARRAY_ELEMENT_SUBOBJECT))
            || !a->interface_thread_id || a->array_offset % alignof(uint64_t)
            || a->index > INT32_MAX || a->source_type_id < 0))
        || (guard && (guard->text_offset < header || !guard->text_bytes
            || guard->text_bytes > INT32_MAX || !request_range(r, guard->text_offset, guard->text_bytes)
            || guard->input_offset < header || guard->input_bytes < sizeof(PdxV3DataInput)
            || !request_range(r, guard->input_offset, guard->input_bytes))))
        return r->error = ERROR_INVALID_PARAMETER;
    reset_output(r); r->value_handle = 0;
    const auto app = get<uintptr_t>(base(), app_rva);
    if (!r->factory || !app || (!source_index_action && get<uintptr_t>(app, 0x608) != e->source_world))
        return r->error = ERROR_NOT_READY;
    if (source_index_action) e->source_world = get<uintptr_t>(app, 0x608);
    if (a && GetCurrentThreadId() != a->interface_thread_id)
        return r->error = ERROR_INVALID_THREAD_ID;
    // This function is entered by the original after-frame Interface callback.
    // The enclosing AccessScope holds the ORIGINAL native exclusive gate until
    // every temporary below is destroyed. Reentrant owner deletion is NOT safe.
    ValueLock lock;
    std::unique_ptr<PropertyOwner> property;
    std::vector<unsigned char> condition_bytes, source_bytes, guard_bytes, expected_bytes;
    std::unique_ptr<PropertyOwner> condition, source_property, guard_property;
    std::unique_ptr<ValueNode> expected;
    DWORD error = ERROR_SUCCESS;
    if (!count_only) {
        // All consumer/source compilation precedes borrowed array/element reads.
        property = std::make_unique<PropertyOwner>();
        error = compile_element_expression(r, property.get());
        if (error) return r->error = error;
        if (e->condition_bytes) {
            condition_bytes = expression_request(r, e->condition_offset, e->condition_bytes);
            condition = std::make_unique<PropertyOwner>();
            error = compile_element_expression(reinterpret_cast<PdxV3DataRequest*>(condition_bytes.data()), condition.get());
            if (error) return r->error = error;
        }
        if (!direct_context) source_bytes = expression_request(r, e->source_text_offset, e->source_text_bytes);
        if (a && !direct_context) {
            source_property = std::make_unique<PropertyOwner>();
            error = compile_element_expression(reinterpret_cast<PdxV3DataRequest*>(source_bytes.data()), source_property.get());
            if (error) return r->error = error;
        }
    }
    if (guard) {
        const auto* input = reinterpret_cast<const PdxV3DataInput*>(
            reinterpret_cast<const unsigned char*>(r) + guard->input_offset);
        // A guard compares copied native values, never an old retained object,
        // command, raw pointer or implicit cross-type input coercion.
        if (input->kind < PDX_V3_DATA_EMPTY || input->kind > PDX_V3_DATA_STRING)
            return r->error = ERROR_INVALID_PARAMETER;
        expected = std::make_unique<ValueNode>();
        error = any_input(input, guard->input_bytes, &expected->value, expected.get());
        if (error) return r->error = error;
        PdxV3DataRequest probe{}; probe.size = sizeof(probe);
        error = snapshot_copied_value(&probe, expected->value);
        if (error) return r->error = error;
        const auto bytes = probe.total;
        if (bytes > UINT32_MAX - sizeof(PdxV3DataRequest)) return r->error = ERROR_ARITHMETIC_OVERFLOW;
        expected_bytes.resize(sizeof(PdxV3DataRequest) + bytes);
        auto* value = reinterpret_cast<PdxV3DataRequest*>(expected_bytes.data());
        *value = {}; value->size = static_cast<uint32_t>(expected_bytes.size());
        value->output_offset = sizeof(*value); value->output_bytes = bytes;
        error = snapshot_copied_value(value, expected->value);
        if (error) return r->error = error;
        guard_bytes = expression_request(r, guard->text_offset, guard->text_bytes);
        const auto output = guard_bytes.size();
        if (bytes > UINT32_MAX - output) return r->error = ERROR_ARITHMETIC_OVERFLOW;
        guard_bytes.resize(output + bytes);
        auto* check = reinterpret_cast<PdxV3DataRequest*>(guard_bytes.data());
        check->size = static_cast<uint32_t>(guard_bytes.size());
        check->output_offset = static_cast<uint32_t>(output); check->output_bytes = bytes;
        guard_property = std::make_unique<PropertyOwner>();
        error = compile_element_expression(check, guard_property.get());
        if (error) return r->error = error;
    }
    struct SourceOwner {
        uint64_t handle = 0;
        ~SourceOwner() { if (handle) release_value_locked(&handle); }
    } root_held;
    // Destruction order is intentional: element/list, then context, then root.
    BindingOwner context;
    SourceOwner held;
    ValueNode* root = nullptr;
    if (a) {
        auto root_bytes = expression_request(r, a->root_text_offset, a->root_text_bytes);
        auto* request = reinterpret_cast<PdxV3DataRequest*>(root_bytes.data());
        error = pdx::data_projection_core::capture_produced(root_held.handle,
            [&] { return run_expression_locked(request, nullptr, e->source_world); },
            [request]() noexcept { return request->value_handle; });
        if (error) return r->error = error;
        root = find_value(root_held.handle);
        if (!root || root->context_world != e->source_world || !world_matches(root))
            return r->error = ERROR_NOT_READY;
        a->stages |= PDX_V3_DATA_ROOT_PRODUCED;
        // Only a genuine engine C++ class-pointer Any is a pointer-array root.
        // Never reinterpret a number, Creation descriptor, CPdxPtr or old Any.
        if (request->kind != PDX_V3_DATA_RAW || request->payload_size != sizeof(uintptr_t)
            || (!starts(request->native_type_name, ".?AU?$SAnyType@PEAV")
                && !starts(request->native_type_name, ".?AU?$SAnyType@PEBV")))
            return r->error = ERROR_NOT_SUPPORTED;
        const auto object = get<uintptr_t>(request->payload_address);
        if (!object || object > UINTPTR_MAX - sizeof(PdxV3DataArray)
            || a->array_offset > UINTPTR_MAX - object - sizeof(PdxV3DataArray))
            return r->error = ERROR_INVALID_DATA;
        const auto array = get<PdxV3DataArray>(object + a->array_offset);
        const pdx::data_projection_core::PointerArrayDescriptor array_descriptor{
            array.data, array.count, array.capacity, sizeof(uintptr_t), INT32_MAX};
        if (pdx::data_projection_core::validate_pointer_array(array_descriptor) != pdx::data_projection_core::Error::ok)
            return r->error = ERROR_INVALID_DATA;
        a->count = array.count; a->stages |= PDX_V3_DATA_ARRAY_VALIDATED;
        if (a->expected_count != UINT32_MAX && a->expected_count != array.count)
            return r->error = ERROR_RETRY;
        if (count_only) {
            r->kind = PDX_V3_DATA_EMPTY;
            return r->error = ERROR_SUCCESS;
        }
        if (a->index >= array.count) return r->error = ERROR_NOT_FOUND;
        uintptr_t pointer = 0;
        const auto element_error = pdx::data_projection_core::pointer_element(array_descriptor, a->index,
            [](uintptr_t address, uintptr_t& value) { value = get<uintptr_t>(address); return true; }, &pointer);
        if (element_error != pdx::data_projection_core::Error::ok)
            return r->error = PdxV3DataProjectionError(element_error);
        if (!pointer) return r->error = ERROR_NOT_FOUND;
        if (subobject) {
            // The source contains original complete objects. Select their real
            // embedded subobject ADDRESS only after checking the original
            // complete-object vtable. This is not a pointer-field dereference,
            // reinterpret cast, retag, synthetic provider, or lifetime lease.
            uintptr_t selected = 0;
            const auto selection_error = PdxV3DataOriginalSubobject(pointer, subobject->byte_offset,
                subobject->expected_element_vtable, &selected);
            if (selection_error) return r->error = selection_error;
            pointer = selected;
        }
        // This is the ORIGINAL named-type converter, not a forged provider,
        // Binding retag or a claim that a unique owner honors intrusive refs.
        error = bind_scoped_object(pointer, a->source_type_id, &context.value);
        if (error) return r->error = error;
        a->stages |= PDX_V3_DATA_SOURCE_BOUND;
    }
    if (guard) {
        // Guard the exact fresh source context BEFORE GetOptions can rebuild its
        // raw elements, then retain the ordinary element BOOL enabled condition.
        auto* check = reinterpret_cast<PdxV3DataRequest*>(guard_bytes.data());
        error = run_expression_locked(check, nullptr, e->source_world, &context.value,
            root, true, guard_property.release());
        // A larger copied result already proves inequality to the expected
        // complete value. It is a checked false guard, not an action/copy error.
        if (error && error != ERROR_MORE_DATA) return r->error = error;
        guard->checked = 1; a->stages |= PDX_V3_DATA_SOURCE_GUARD_CHECKED;
        const auto* value = reinterpret_cast<const PdxV3DataRequest*>(expected_bytes.data());
        guard->passed = !error && check->kind == value->kind && check->total == value->total
            && check->snapshot_bytes == value->snapshot_bytes
            && (!check->snapshot_bytes || memcmp(guard_bytes.data() + check->output_offset,
                expected_bytes.data() + value->output_offset, check->snapshot_bytes) == 0);
        if (!guard->passed) return r->error = ERROR_RETRY;
        a->stages |= PDX_V3_DATA_SOURCE_GUARD_PASSED;
    }
    if (direct_context) {
        e->element_type_id = get<int32_t>(context.value.bytes, 56);
        if (condition) {
            auto* check = reinterpret_cast<PdxV3DataRequest*>(condition_bytes.data());
            error = run_expression_locked(check, nullptr, e->source_world, &context.value,
                root, true, condition.release());
            if (error) return r->error = error;
            e->condition_checked = 1; a->stages |= PDX_V3_DATA_CONDITION_CHECKED;
            if (check->kind != PDX_V3_DATA_BOOL) return r->error = ERROR_INVALID_DATA;
            e->condition_passed = (check->raw_bits & 0xff) != 0;
            if (!e->condition_passed) return r->error = ERROR_CANCELLED;
        }
        error = run_expression_locked(r, action ? &e->action : nullptr, e->source_world,
            &context.value, root, true, property.release());
        if (!error) a->stages |= PDX_V3_DATA_CONSUMER_COMPLETED;
        return error;
    }
    auto* source_request = reinterpret_cast<PdxV3DataRequest*>(source_bytes.data());
    error = pdx::data_projection_core::capture_produced(held.handle,
        [&] { return run_expression_locked(source_request, nullptr, e->source_world,
            a ? &context.value : nullptr, root, false, source_property.release(), nullptr, source_index_action); },
        [source_request]() noexcept { return source_request->value_handle; });
    if (error) return r->error = error;
    auto* source = find_value(held.handle);
    if (!source || source->context_world != e->source_world || !world_matches(source))
        return r->error = ERROR_NOT_READY;
    const auto payload = creation_payload(source);
    if (!payload) return r->error = ERROR_NOT_SUPPORTED;
    if (a) a->stages |= PDX_V3_DATA_LIST_PRODUCED;
    const auto count = creation_count(payload);
    if (count < 0) return r->error = ERROR_INVALID_DATA;
    e->source_count = static_cast<uint32_t>(count);
    if (r->capacity != UINT32_MAX && r->capacity != e->source_count)
        return r->error = ERROR_RETRY;
    {
        BindingOwner element;
        if (source_index_action) {
            auto* input = reinterpret_cast<PdxV3DataInput*>(reinterpret_cast<unsigned char*>(r) + e->action.input_offset);
            error = PdxV3DataSourceIndexInput(r->start, e->source_count, input);
            if (error) return r->error = error;
            // Count was observed in this same Access. Do not replay it.
            error = creation_element_observed(payload, static_cast<int32_t>(r->start), &element.value);
        } else error = creation_element(payload, static_cast<int32_t>(r->start), &element.value);
        if (error) return r->error = error;
        if (a) a->stages |= PDX_V3_DATA_ELEMENT_OBTAINED;
        e->element_type_id = get<int32_t>(element.value.bytes, 56);
        if (condition) {
            auto* check = reinterpret_cast<PdxV3DataRequest*>(condition_bytes.data());
            error = run_expression_locked(check, nullptr, e->source_world, &element.value,
                source, true, condition.release(), nullptr, source_index_action);
            if (error) return r->error = error;
            e->condition_checked = 1;
            if (a) a->stages |= PDX_V3_DATA_CONDITION_CHECKED;
            if (check->kind != PDX_V3_DATA_BOOL) return r->error = ERROR_INVALID_DATA;
            e->condition_passed = (check->raw_bits & 0xff) != 0;
            if (!e->condition_passed) return r->error = ERROR_CANCELLED;
        }
        if (!source_index_action) {
            error = run_expression_locked(r, action ? &e->action : nullptr,
                e->source_world, &element.value, source, true, property.release());
            if (!error && a) a->stages |= PDX_V3_DATA_CONSUMER_COMPLETED;
            return error;
        }
        // Preserve the original raw-parameter restriction. The root consumer
        // receives no borrowed row: destroy that Binding BEFORE mutation.
        if (element.value.bytes[48] <= 1
            && (get<uint32_t>(property->value.bytes, 12) + get<uint32_t>(property->value.bytes, 172)))
            return r->error = ERROR_NOT_SUPPORTED;
    }
    // The fresh source/root graph remains alive. This does not authorize an
    // action to delete its backing owner or invalidate its Creation cleanup.
    return run_expression_locked(r, &e->action, e->source_world,
        nullptr, source, true, property.release(), nullptr, true);
}
}
DWORD WINAPI PdxV3DataElement(PdxV3DataElementRequest* r) {
    try { return run_element(r); }
    catch (...) { return r ? r->action.result.error = ERROR_UNHANDLED_EXCEPTION : ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3DataCreationCount(uint64_t handle, int32_t* count) {
    if (!count) return ERROR_INVALID_PARAMETER;
    ValueLock lock; const auto* source = find_value(handle);
    if (!world_matches(source)) return ERROR_NOT_READY;
    const auto payload = creation_payload(source);
    if (!payload) return ERROR_NOT_SUPPORTED;
    *count = creation_count(payload); return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DataCreationElement(uint64_t handle, int32_t index, PdxV3DataBinding* binding) {
    ValueLock lock; const auto* source = find_value(handle);
    if (!world_matches(source)) return ERROR_NOT_READY;
    const auto payload = creation_payload(source);
    return payload ? creation_element(payload, index, binding) : ERROR_NOT_SUPPORTED;
}
DWORD WINAPI PdxV3DataListCount(PdxV3DataRequest* r) {
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    reset_output(r); ValueLock lock;
    const auto* source = find_value(r->value_handle);
    if (!world_matches(source)) return r->error = ERROR_NOT_READY;
    const auto payload = creation_payload(source);
    if (!payload) return r->error = ERROR_NOT_SUPPORTED;
    const auto count = creation_count(payload);
    if (count < 0) return r->error = ERROR_INVALID_DATA;
    r->kind = PDX_V3_DATA_LIST; r->total = static_cast<uint32_t>(count);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DataListElement(PdxV3DataRequest* r) {
    if (!r || r->size < sizeof(*r) || r->start > INT32_MAX) return ERROR_INVALID_PARAMETER;
    reset_output(r); ValueLock lock;
    auto* source = find_value(r->value_handle);
    if (!world_matches(source)) return r->error = ERROR_NOT_READY;
    const auto payload = creation_payload(source);
    if (!payload) return r->error = ERROR_NOT_SUPPORTED;
    BindingOwner binding;
    const auto error = creation_element(payload, static_cast<int32_t>(r->start), &binding.value);
    if (error) return r->error = error;
    // Native providers return Binding64; only owned Any-bearing modes2/3 can be
    // retained directly. The raw Binding primitive above remains usable for0/1.
    if (binding.value.bytes[48] != 2 && binding.value.bytes[48] != 3)
        return r->error = ERROR_NOT_SUPPORTED;
    auto node = std::make_unique<ValueNode>();
    const auto empty = empty_type(); memcpy(node->value.bytes, &empty, 8);
    PdxV3DataAny borrowed{}; memcpy(borrowed.bytes, binding.value.bytes, 40);
    copy_any(node->value.bytes, borrowed);
    node->type_id = get<int32_t>(binding.value.bytes, 56);
    node->dependencies.push_back(source); ++source->references;
    node->context_world = source->context_world;
    node->handle = ++next_handle; node->next = values; values = node.get();
    r->value_handle = node->handle; r->type_id = node->type_id;
    auto* published = node.release();
    return r->error = snapshot_value(r, published->value);
}
DWORD WINAPI PdxV3DataCreationModel(uint64_t handle, int32_t variant, uint64_t* model) {
    if (!model) return ERROR_INVALID_PARAMETER;
    ValueLock lock; const auto* source = find_value(handle);
    if (!world_matches(source)) return ERROR_NOT_READY;
    const auto payload = creation_payload(source);
    if (!payload) return ERROR_NOT_SUPPORTED;
    const auto provider = get<uintptr_t>(payload); *model = 0;
    using CreateFn = uint64_t* (__fastcall*)(void*, uint64_t*, const void*, int32_t);
    reinterpret_cast<CreateFn>(get<uintptr_t>(get<uintptr_t>(provider), 8))(
        reinterpret_cast<void*>(provider), model, reinterpret_cast<void*>(payload + 8), variant);
    return *model ? ERROR_SUCCESS : ERROR_INVALID_DATA;
}
DWORD WINAPI PdxV3DataModelDimensions(uint64_t model, int32_t* dimensions) {
    if (!model || !dimensions) return ERROR_INVALID_PARAMETER;
    const auto impl = get<uintptr_t>(model, 72);
    if (!impl) return ERROR_INVALID_DATA;
    using Invoke = int32_t* (__fastcall*)(void*, int32_t*);
    reinterpret_cast<Invoke>(get<uintptr_t>(get<uintptr_t>(impl), 16))(
        reinterpret_cast<void*>(impl), dimensions);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DataModelBinding(uint64_t model, const int32_t* coordinates,
                                  PdxV3DataBinding* binding) {
    if (!model || !coordinates || !binding) return ERROR_INVALID_PARAMETER;
    int32_t dimensions[2]{}; const auto error = PdxV3DataModelDimensions(model, dimensions);
    if (error) return error;
    if (coordinates[0] < 0 || coordinates[1] < 0 || coordinates[0] >= dimensions[0]
        || coordinates[1] >= dimensions[1]) return ERROR_NOT_FOUND;
    auto impl = get<uintptr_t>(model, 200); const bool mutable_element = impl != 0;
    if (!impl) impl = get<uintptr_t>(model, 136);
    if (!impl) return ERROR_INVALID_DATA;
    using Invoke = uintptr_t (__fastcall*)(void*, const int32_t*);
    const auto wrapper = reinterpret_cast<Invoke>(get<uintptr_t>(get<uintptr_t>(impl), 16))(
        reinterpret_cast<void*>(impl), coordinates);
    if (!wrapper) return ERROR_NOT_FOUND;
    memset(binding->bytes, 0, 64); binding->bytes[48] = 0xff;
    const auto id = get<int32_t>(model, 208);
    reinterpret_cast<BindFn>(base() + (mutable_element ? 0x35f1570 : bind_rva))(
        wrapper, &id, binding->bytes);
    memcpy(binding->bytes + 56, &id, 4);
    return binding->bytes[48] == 0xff ? ERROR_INVALID_DATA : ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DataReleaseModel(uint64_t* model) {
    if (!model || !*model) return ERROR_INVALID_PARAMETER;
    release_wrapper(*model); *model = 0; return ERROR_SUCCESS;
}


namespace {
DWORD initialize_state_name_context(uint64_t context) {
    if (!context || get<uintptr_t>(context) != base() + 0x45d0fd0
        || get<uintptr_t>(context, 0x38)) return ERROR_INVALID_PARAMETER;
    const auto app = get<uintptr_t>(base(), app_rva);
    if (!app || !get<uintptr_t>(app, 0x608)) return ERROR_NOT_READY;
    const auto id = get<uint32_t>(context, 0x88);
    using Resolver = uintptr_t (__fastcall*)(const uint32_t*);
    const auto state = reinterpret_cast<Resolver>(base() + 0xc5af10)(&id);
    if (!state || state == get<uintptr_t>(base(), 0x5964e28) || get<uint32_t>(state, 8) != id)
        return ERROR_NOT_FOUND;
    reinterpret_cast<StorageFn>(base() + 0x22a39e0)(reinterpret_cast<void*>(context));
    return get<uint32_t>(context, 0xbc) == 5 ? ERROR_SUCCESS : ERROR_INVALID_DATA;
}
DWORD create_state_name_context(uint32_t state_id, uint64_t* context) {
    if (!context) return ERROR_INVALID_PARAMETER;
    *context = 0;
    const auto app = get<uintptr_t>(base(), app_rva);
    if (!app || !get<uintptr_t>(app, 0x608) || !get<uintptr_t>(base(), 0x5964ef0))
        return ERROR_NOT_READY;
    using Resolver = uintptr_t (__fastcall*)(const uint32_t*);
    const auto state = reinterpret_cast<Resolver>(base() + 0xc5af10)(&state_id);
    if (!state || state_id == UINT32_MAX || state == get<uintptr_t>(base(), 0x5964e28)
        || get<uint32_t>(state, 8) != state_id) return ERROR_NOT_FOUND;
    void* owned = nullptr;
    const DWORD error = pdx::owned_context::from_ref0_factory(
        [state_id]() -> void* {
            uintptr_t pointer = 0;
            using Factory = uintptr_t* (__fastcall*)(uintptr_t*, const uint32_t*);
            reinterpret_cast<Factory>(base() + 0x21cf5f0)(&pointer, &state_id);
            return reinterpret_cast<void*>(pointer);
        },
        [](void* object) noexcept {
            pdx::v3_owned_context::retain_adapter_ref(reinterpret_cast<uintptr_t>(object));
        },
        [](void* object) noexcept { release_wrapper(reinterpret_cast<uintptr_t>(object)); },
        [](void* object) { return initialize_state_name_context(reinterpret_cast<uintptr_t>(object)); },
        DWORD(ERROR_SUCCESS), DWORD(ERROR_NOT_ENOUGH_MEMORY), owned);
    *context = reinterpret_cast<uintptr_t>(owned);
    return error;
}
}
DWORD WINAPI PdxV3DataInitializeStateNameContext(uint64_t context) {
    try { return initialize_state_name_context(context); }
    catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3DataCreateStateNameContext(uint32_t state_id, uint64_t* context) {
    try { return create_state_name_context(state_id, context); }
    catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
namespace {
DWORD publish_owned_context(PdxV3DataRequest* r, uintptr_t context, int32_t type,
    uintptr_t world, ValueNode* source = nullptr, bool state_name = false) {
    struct ContextOwner { uintptr_t pointer; ~ContextOwner() { release_wrapper(pointer); } } owned{context};
    BindingOwner binding;
    const auto bind_error = bind_object(context, type, &binding.value);
    if (bind_error) return r->error = bind_error;
    auto node = std::make_unique<ValueNode>();
    const auto empty = empty_type(); memcpy(node->value.bytes, &empty, 8);
    PdxV3DataAny borrowed{}; memcpy(borrowed.bytes, binding.value.bytes, 40);
    copy_any(node->value.bytes, borrowed);
    node->type_id = type; node->state_name_context = state_name ? context : 0;
    node->context_world = world;
    if (source) { node->dependencies.push_back(source); ++source->references; }
    node->handle = ++next_handle; node->next = values; values = node.get();
    r->value_handle = node->handle; r->type_id = type;
    auto* published = node.release();
    return r->error = snapshot_value(r, published->value);
}
DWORD state_name_context(PdxV3DataRequest* r) {
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    reset_output(r); r->value_handle = 0; ValueLock lock;
    const auto type = PdxV3DataFindType("StateNameChangePopup");
    if (type < 0) return r->error = ERROR_NOT_READY;
    uint64_t context = 0;
    const auto error = create_state_name_context(r->start, &context);
    if (error) return r->error = error;
    return publish_owned_context(r, context, type,
        get<uintptr_t>(get<uintptr_t>(base(), app_rva), 0x608), nullptr, true);
}

// Action22 reuses the wire field for input and published output. Take the
// input before any entry/Access failure; never expose it as a produced owner.
uint64_t take_owned_context_input(PdxV3DataRequest* r) noexcept {
    if (!r || r->size < offsetof(PdxV3DataRequest, value_handle) + sizeof(r->value_handle)) return 0;
    const auto source_handle = r->value_handle;
    r->value_handle = 0;
    return source_handle;
}
DWORD owned_context(PdxV3DataRequest* r, uint64_t source_handle) {
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    reset_output(r); ValueLock lock;
    auto* source = find_value(source_handle);
    if (!source) return r->error = ERROR_INVALID_HANDLE;
    if (!source->context_world || !world_matches(source)) return r->error = ERROR_NOT_READY;
    using namespace pdx::v3_owned_context;
    const char* name = nullptr;
    switch (r->flags) {
    case PDX_V3_DATA_RELEASE_COUNTRY_CONTEXT: name = "ReleaseCountryWindow"; break;
    case PDX_V3_DATA_POWER_BLOC_CONTEXT:
    case PDX_V3_DATA_POWER_BLOC_FORMATION_CONTEXT: name = "PowerBlocCustomizationPopup"; break;
    case PDX_V3_DATA_MILITARY_FORMATION_NAME_CONTEXT: name = "MilitaryFormationChangeNamePopup"; break;
    case PDX_V3_DATA_SHIP_NAME_CONTEXT: name = "ShipChangeNamePopup"; break;
    default: return r->error = ERROR_INVALID_PARAMETER;
    }
    const auto type = PdxV3DataFindType(name);
    if (type < 0) return r->error = ERROR_NOT_READY;
    uintptr_t context = 0;
    DWORD error = ERROR_SUCCESS;
    if (r->flags == PDX_V3_DATA_RELEASE_COUNTRY_CONTEXT) {
        error = release_country_from_owned_any(base(), source->value, release_wrapper, &context);
    } else if (r->flags == PDX_V3_DATA_POWER_BLOC_CONTEXT) {
        // Original Show producer consumes this exact CDataPowerBloc full ref.
        // Its original registered Any converter rejects other/raw input types.
        const auto source_type = PdxV3DataFindType("PowerBloc");
        const auto record = type_record(source_type);
        const auto converter = record ? get<uintptr_t>(record, 224) : 0;
        if (!converter) return r->error = ERROR_NOT_SUPPORTED;
        using FromAny = bool (__fastcall*)(void*, uintptr_t*);
        uintptr_t wrapper = 0;
        if (!reinterpret_cast<FromAny>(converter)(source->value.bytes, &wrapper) || !wrapper)
            return r->error = ERROR_INVALID_DATA;
        error = bloc_existing_from_full_id(base(), get<uint32_t>(wrapper, 0x10), release_wrapper, &context);
    } else if (r->flags == PDX_V3_DATA_MILITARY_FORMATION_NAME_CONTEXT) {
        error = military_formation_from_owned_any(base(), source->value, release_wrapper, &context);
    } else if (r->flags == PDX_V3_DATA_POWER_BLOC_FORMATION_CONTEXT) {
        error = bloc_formation_from_owned_any(base(), source->value, release_wrapper, &context);
    } else {
        error = ship_name_from_owned_any(base(), source->value, release_wrapper, &context);
    }
    if (error) return r->error = error;
    return publish_owned_context(r, context, type, source->context_world, source);
}

}
DWORD WINAPI PdxV3DataOwnedContext(PdxV3DataRequest* r) {
    const auto source_handle = take_owned_context_input(r);
    try { return owned_context(r, source_handle); }
    catch (...) { return r ? r->error = ERROR_UNHANDLED_EXCEPTION : ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3DataStateNameContext(PdxV3DataRequest* r) {
    try { return state_name_context(r); }
    catch (...) { return r ? r->error = ERROR_UNHANDLED_EXCEPTION : ERROR_UNHANDLED_EXCEPTION; }
}
namespace {
DWORD state_name_draft(PdxV3DataRequest* r) {
    if (!r || r->size < sizeof(*r) || r->start > 5) return ERROR_INVALID_PARAMETER;
    reset_output(r); ValueLock lock;
    const auto* node = find_value(r->value_handle);
    if (!node || !node->state_name_context) return r->error = ERROR_INVALID_HANDLE;
    const auto app = get<uintptr_t>(base(), app_rva);
    if (!app || !node->context_world || get<uintptr_t>(app, 0x608) != node->context_world)
        return r->error = ERROR_NOT_READY;
    const auto context = node->state_name_context;
    if (get<uintptr_t>(context, 0x38) || get<uint32_t>(context, 0xbc) != 5)
        return r->error = ERROR_INVALID_DATA;
    const auto text = cstring(r->start == 0 ? context + 0x90
        : get<uintptr_t>(context, 0xb0) + 32ull * (r->start - 1));
    r->kind = PDX_V3_DATA_STRING; r->total = static_cast<uint32_t>(text.size());
    r->type_id = node->type_id; r->object = context;
    if (!r->output_bytes) return ERROR_SUCCESS;
    if (!request_range(r, r->output_offset, r->output_bytes)) return r->error = ERROR_INVALID_PARAMETER;
    r->snapshot_bytes = static_cast<uint32_t>(std::min<size_t>(r->output_bytes, text.size()));
    if (r->snapshot_bytes) memcpy(output(r), text.data(), r->snapshot_bytes);
    return r->error = r->snapshot_bytes < text.size() ? ERROR_MORE_DATA : ERROR_SUCCESS;
}

}
DWORD WINAPI PdxV3DataStateNameDraft(PdxV3DataRequest* r) {
    try { return state_name_draft(r); }
    catch (...) { return r ? r->error = ERROR_UNHANDLED_EXCEPTION : ERROR_UNHANDLED_EXCEPTION; }
}

namespace {
DWORD copy_name_bytes(PdxV3DataRequest* r, uintptr_t text, uintptr_t object) {
    const auto bytes = cstring(text);
    r->kind = PDX_V3_DATA_STRING; r->total = static_cast<uint32_t>(bytes.size()); r->object = object;
    if (!r->output_bytes) return ERROR_SUCCESS;
    if (!request_range(r, r->output_offset, r->output_bytes)) return r->error = ERROR_INVALID_PARAMETER;
    r->snapshot_bytes = static_cast<uint32_t>(std::min<size_t>(r->output_bytes, bytes.size()));
    if (r->snapshot_bytes) memcpy(output(r), bytes.data(), r->snapshot_bytes);
    return r->error = r->snapshot_bytes < bytes.size() ? ERROR_MORE_DATA : ERROR_SUCCESS;
}
DWORD state_name_override(PdxV3DataRequest* r) {
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    if (r->flags > 5) return r->error = ERROR_INVALID_PARAMETER;
    reset_output(r);
    const auto app = get<uintptr_t>(base(), app_rva);
    if (!app || !get<uintptr_t>(app, 0x608) || !get<uintptr_t>(base(), 0x5964ef0))
        return r->error = ERROR_NOT_READY;
    using Resolver = uintptr_t (__fastcall*)(const uint32_t*);
    const auto state = reinterpret_cast<Resolver>(base() + 0xc5af10)(&r->start);
    if (!state || r->start == UINT32_MAX || state == get<uintptr_t>(base(), 0x5964e28)
        || get<uint32_t>(state, 8) != r->start) return r->error = ERROR_NOT_FOUND;
    const auto hub_names = get<uintptr_t>(state, 0x1c58);
    if (r->flags && !hub_names)
        return r->error = ERROR_INVALID_DATA;
    return copy_name_bytes(r, r->flags == 0 ? state + 0x1c38
        : hub_names + 32ull * (r->flags - 1), state);
}
DWORD data_command(uint64_t handle, PdxV3DataCommandValue* command) {
    if (!command) return ERROR_INVALID_PARAMETER;
    *command = {}; ValueLock lock;
    const auto* node = find_value(handle);
    if (!node) return ERROR_INVALID_HANDLE;
    const auto app = get<uintptr_t>(base(), app_rva);
    if (node->context_world && (!app || get<uintptr_t>(app, 0x608) != node->context_world))
        return ERROR_NOT_READY;
    const auto tagged = get<uintptr_t>(node->value.bytes);
    const auto type = tagged & ~uintptr_t(1);
    if (type != reinterpret_cast<EmptyType>(base() + 0x10b4940)()) return ERROR_NOT_SUPPORTED;
    using SizeFn = uint64_t (__fastcall*)(void*);
    if (reinterpret_cast<SizeFn>(get<uintptr_t>(get<uintptr_t>(type), 88))(
            reinterpret_cast<void*>(type)) != 24) return ERROR_INVALID_DATA;
    const auto payload = tagged & 1 ? get<uintptr_t>(node->value.bytes, 8)
        : reinterpret_cast<uintptr_t>(node->value.bytes + 8);
    if (!get<uintptr_t>(payload) || !get<uintptr_t>(payload, 8)) return ERROR_INVALID_DATA;
    memcpy(command, reinterpret_cast<const void*>(payload), 24);
    command->world = node->context_world;
    return ERROR_SUCCESS;
}
}
DWORD WINAPI PdxV3DataStateNameOverride(PdxV3DataRequest* r) {
    try { return state_name_override(r); }
    catch (...) { return r ? r->error = ERROR_UNHANDLED_EXCEPTION : ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3DataCommand(uint64_t handle, PdxV3DataCommandValue* command) {
    try { return data_command(handle, command); }
    catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3DataCommandSnapshot(PdxV3DataRequest* r) {
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    reset_output(r);
    try {
        PdxV3DataCommandValue command{};
        const auto error = data_command(r->value_handle, &command);
        if (error) return r->error = error;
        r->object = command.command; r->total = sizeof(command);
        if (!r->output_bytes) return ERROR_SUCCESS;
        if (!request_range(r, r->output_offset, r->output_bytes)) return r->error = ERROR_INVALID_PARAMETER;
        r->snapshot_bytes = std::min<uint32_t>(r->output_bytes, sizeof(command));
        memcpy(output(r), &command, r->snapshot_bytes);
        return r->error = r->snapshot_bytes < sizeof(command) ? ERROR_MORE_DATA : ERROR_SUCCESS;
    } catch (...) { return r->error = ERROR_UNHANDLED_EXCEPTION; }
}

void WINAPI PdxV3DataInterface(void* application, void* opaque) {
    auto* r = static_cast<PdxV3DataRequest*>(opaque);
    if (!r) return;
    const auto source_handle = r->size >= offsetof(PdxV3DataRequest, action) + sizeof(r->action)
        && r->action == PDX_V3_DATA_CREATE_OWNED_CONTEXT ? take_owned_context_input(r) : 0;
    if (r->size < sizeof(*r)) return;
    r->execution_thread_id = GetCurrentThreadId(); r->error = ERROR_NOT_READY;
    const auto app = reinterpret_cast<uintptr_t>(application);
    if (!app || app != get<uintptr_t>(base(), app_rva) || get<uint8_t>(app, 0x258)) return;
    try {
        AccessScope scope;
        switch (r->action) {
        case PDX_V3_DATA_TYPES: PdxV3DataEnumerateTypes(r); break;
        case PDX_V3_DATA_FUNCTIONS: PdxV3DataEnumerateFunctions(r); break;
        case PDX_V3_DATA_EVALUATE: PdxV3DataEvaluate(r); break;
        case PDX_V3_DATA_EVALUATE_COPIED:
        case PDX_V3_DATA_SOURCE_COUNT:
        case PDX_V3_DATA_EVALUATE_SCOPED:
        case PDX_V3_DATA_SCOPED_SOURCE_COUNT:
            PdxV3DataProjection(reinterpret_cast<PdxV3DataProjectionRequest*>(r)); break;
        case PDX_V3_DATA_SNAPSHOT: PdxV3DataSnapshot(r); break;
        case PDX_V3_DATA_RELEASE: r->error = PdxV3DataRelease(&r->value_handle); break;
        case PDX_V3_DATA_LIST_COUNT: PdxV3DataListCount(r); break;
        case PDX_V3_DATA_LIST_ELEMENT: PdxV3DataListElement(r); break;
        case PDX_V3_DATA_INVOKE_ACTION:
            PdxV3DataInvokeAction(reinterpret_cast<PdxV3DataActionRequest*>(r)); break;
        case PDX_V3_DATA_CREATE_STATE_NAME_CONTEXT: PdxV3DataStateNameContext(r); break;
        case PDX_V3_DATA_CREATE_OWNED_CONTEXT: owned_context(r, source_handle); break;
        case PDX_V3_DATA_STATE_NAME_DRAFT: PdxV3DataStateNameDraft(r); break;
        case PDX_V3_DATA_STATE_NAME_OVERRIDE: PdxV3DataStateNameOverride(r); break;
        case PDX_V3_DATA_COMMAND_VALUE: PdxV3DataCommandSnapshot(r); break;
        case PDX_V3_DATA_EVALUATE_ELEMENT:
        case PDX_V3_DATA_INVOKE_ELEMENT_ACTION:
        case PDX_V3_DATA_EVALUATE_ARRAY_ELEMENT:
        case PDX_V3_DATA_INVOKE_ARRAY_ELEMENT_ACTION:
        case PDX_V3_DATA_INVOKE_SOURCE_INDEX_ACTION:
            PdxV3DataElement(reinterpret_cast<PdxV3DataElementRequest*>(r)); break;
        default: r->error = ERROR_INVALID_PARAMETER;
        }
    } catch (...) { r->error = ERROR_UNHANDLED_EXCEPTION; }
}

void WINAPI PdxV3DataInvokeSourceIndexInterface(void* application, void* opaque) {
    PdxV3DataInterface(application, opaque);
}
void WINAPI PdxV3DataCreateOwnedContextInterface(void* application, void* opaque) {
    PdxV3DataInterface(application, opaque);
}

namespace {
static_assert(sizeof(PdxV3DataPageRequest) == 88);
static_assert(sizeof(PdxV3DataPageCell) == 80);
// One canonical V3 argument frame for final AND prefix sources. Properties
// outlive produced Any/source owners; direct refs always point to real Anys.
// This is callback-local original ValueNode ownership, not another pool/VM.
struct PageCapturedArguments {
    std::vector<PdxV3DataPageSpan> expressions;
    std::vector<std::unique_ptr<PropertyOwner>> properties;
    std::vector<DWORD> errors;
    std::vector<std::unique_ptr<ScopedProjectionAdapter::Source>> values;
    std::vector<const PdxV3DataAny*> references;
    template<class Compile>
    void compile(const PdxV3DataPageSpan* arguments, size_t count, Compile compiler) {
        expressions.assign(arguments, arguments + count);
        properties.resize(count); errors.resize(count);
        for (size_t i = 0; i < count; ++i) errors[i] = compiler(expressions[i], properties[i]);
    }
};

struct PageProjectionAdapter {
    using Status = DWORD;
    struct Source {
        ScopedProjectionAdapter::Source value;
        pdx::original_source_core::View original;
        bool original_array = false;
    };
    struct Row {
        // Declared first: the genuine ref1 wrapper dies AFTER all row bindings.
        struct WrapperOwner {
            uintptr_t pointer = 0;
            ~WrapperOwner() { release_wrapper(pointer); }
        } company;
        ValueNode bindings;
        BindingOwner element;
        uint32_t binding_kind = UINT32_MAX, binding_error = ERROR_SUCCESS;
        uint64_t binding_type_offset = 0, binding_type_bytes = 0;
    };
    PdxV3DataPageRequest* page;
    const PdxV3DataPageSpan* fields;
    const PdxV3DataPageField* native_fields = nullptr;
    std::vector<PdxV3DataPageField> native_field_descriptors;
    int32_t native_decree_type = -1, native_decree = -1, gui_rule_type = -1;
    const PdxV3DataPageSpan* rows;
    ValueNode root_bindings;
    PdxV3DataDomainTypeCache domain_types;
    ScopedProjectionAdapter::Context outer_context;
    std::vector<std::unique_ptr<PropertyOwner>> properties;
    std::vector<DWORD> compile_errors;
    std::unique_ptr<PropertyOwner> source_property;
    PageCapturedArguments arguments;
    std::vector<PdxV3DataPageCell> cells;
    std::vector<unsigned char> copied;
    DWORD source_compile_error = ERROR_SUCCESS;
    const PdxV3DataPageGroup* group = nullptr;
    const PdxV3DataPageSpan* sources = nullptr;
    std::vector<PdxV3DataPageSource> original_source_descriptors;
    int32_t preset_type = -1, setting_type = -1, company_type = -1;
    std::vector<std::unique_ptr<PropertyOwner>> source_properties;
    std::vector<DWORD> source_errors;
    std::vector<PdxV3DataSourceObservation> observations;
    pdx::data_projection_core::SourceCursor cursor{};
    uint32_t current_start = 0, selected_rows = 0, row_offset = 0, row_budget = 0;
    bool range_complete = true;
    bool has_source() const { return group || page->source_bytes; }
    PdxV3DataSourceObservation& observation() { return observations.back(); }
    explicit PageProjectionAdapter(PdxV3DataPageRequest* request) : page(request),
        fields(reinterpret_cast<const PdxV3DataPageSpan*>(reinterpret_cast<const char*>(page) + page->fields_offset)),
        rows(reinterpret_cast<const PdxV3DataPageSpan*>(reinterpret_cast<const char*>(page) + page->rows_offset)) {
        current_start = page->start; row_budget = page->limit;
        if (page->flags & PDX_V3_DATA_PAGE_NATIVE_FIELDS) {
            const auto* input = reinterpret_cast<const PdxV3DataPageField*>(fields);
            native_field_descriptors.assign(input, input + page->fields_count);
            native_fields = native_field_descriptors.data(); // immutable before every source borrow
        }
        if (page->flags & PDX_V3_DATA_PAGE_GROUPED) {
            group = reinterpret_cast<const PdxV3DataPageGroup*>(reinterpret_cast<const PdxV3DataPageScope*>(page + 1) + 1);
            sources = reinterpret_cast<const PdxV3DataPageSpan*>(reinterpret_cast<const char*>(page) + group->sources_offset);
            cursor = {group->source_index, page->start};
        }
        if (page->flags & PDX_V3_DATA_PAGE_ORIGINAL_SOURCES) {
            const auto count = group ? group->sources_count : 1;
            original_source_descriptors.resize(count);
            const auto* input = reinterpret_cast<const unsigned char*>(page) + (group ? group->sources_offset : page->source_offset);
            std::memcpy(original_source_descriptors.data(), input, size_t(count) * sizeof(PdxV3DataPageSource));
        }
    }
    bool original_source(size_t index) const {
        return !original_source_descriptors.empty()
            && original_source_descriptors[index].kind != PDX_V3_DATA_SOURCE_EXPRESSION;
    }
    bool company_source(size_t index = 0) const {
        return !original_source_descriptors.empty()
            && original_source_descriptors[index].kind == PDX_V3_DATA_SOURCE_FULL_COMPANY_TYPES;
    }
    PdxV3DataPageSpan source_expression(size_t index) const {
        if (!original_source_descriptors.empty()) {
            const auto& source = original_source_descriptors[index];
            return {source.expression_offset, source.expression_bytes};
        }
        return group ? sources[index] : PdxV3DataPageSpan{page->source_offset, page->source_bytes};
    }
    Status compile_source(size_t index, std::unique_ptr<PropertyOwner>& property) {
        if (!original_source(index)) return compile(source_expression(index), property);
        if (company_source(index)) {
            if (company_type < 0) company_type = PdxV3DataFindType(pdx::v3_company_sources::type_name);
            return company_type < 0 ? ERROR_NOT_READY : ERROR_SUCCESS;
        }
        if (preset_type < 0) preset_type = PdxV3DataFindType("GuiGameRulePreset");
        if (setting_type < 0) setting_type = PdxV3DataFindType("GameRuleSetting");
        return preset_type < 0 || setting_type < 0 ? ERROR_NOT_READY : ERROR_SUCCESS;
    }
    PdxV3DataPageSpan field_expression(size_t field) const {
        return native_fields ? PdxV3DataPageSpan{native_fields[field].expression_offset, native_fields[field].expression_bytes}
                             : fields[field];
    }
    bool is_native_field(size_t field) const { return native_fields && native_fields[field].kind != PDX_V3_DATA_FIELD_EXPRESSION; }
    bool ok(Status status) const { return status == ERROR_SUCCESS; }
    Status success_status() const { return ERROR_SUCCESS; }
    size_t row_count() const { return selected_rows; }
    size_t field_count() const { return page->fields_count; }
    std::vector<unsigned char> request(PdxV3DataPageSpan expression, size_t row) {
        const auto& bindings = rows[row];
        const auto offset = (sizeof(PdxV3DataRequest) + expression.count + 7) & ~size_t(7);
        const auto bytes = size_t(bindings.count) * sizeof(PdxV3DataBindingInput);
        std::vector<unsigned char> storage(offset + bytes);
        auto* r = reinterpret_cast<PdxV3DataRequest*>(storage.data());
        r->size = static_cast<uint32_t>(storage.size()); r->action = PDX_V3_DATA_EVALUATE;
        r->type_id = -1; r->text_offset = sizeof(*r); r->text_bytes = expression.count;
        r->bindings_offset = static_cast<uint32_t>(offset); r->bindings_count = bindings.count;
        r->output_offset = r->size;
        memcpy(storage.data() + sizeof(*r), reinterpret_cast<const char*>(page) + expression.offset, expression.count);
        if (bytes) memcpy(storage.data() + offset, reinterpret_cast<const char*>(page) + bindings.offset, bytes);
        return storage;
    }
    DWORD compile(PdxV3DataPageSpan text, std::unique_ptr<PropertyOwner>& property) {
        try {
            property = std::make_unique<PropertyOwner>();
            auto bytes = request(text, 0);
            return compile_element_expression(reinterpret_cast<PdxV3DataRequest*>(bytes.data()), property.get());
        } catch (const std::bad_alloc&) { return ERROR_NOT_ENOUGH_MEMORY; }
          catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
    }
    void compile_all() {
        properties.resize(page->fields_count); compile_errors.resize(page->fields_count);
        for (size_t i = 0; i < properties.size(); ++i) {
            const auto expression = field_expression(i);
            if (is_native_field(i) && !expression.count) {
                if (native_fields[i].kind == PDX_V3_DATA_FIELD_GAME_RULE_KEY) {
                    if (gui_rule_type < 0) gui_rule_type = PdxV3DataFindType(pdx::v3_rule_fields::gui_type_name);
                    if (setting_type < 0) setting_type = PdxV3DataFindType(pdx::v3_rule_fields::setting_type_name);
                    compile_errors[i] = gui_rule_type < 0 && setting_type < 0 ? ERROR_NOT_READY : ERROR_SUCCESS;
                } else {
                    // Actual current named row, not a guessed alias of the
                    // separate raw GetType Any return metadata.
                    auto& type = native_fields[i].kind == PDX_V3_DATA_FIELD_DECREE_ID ? native_decree : native_decree_type;
                    if (type < 0) type = PdxV3DataFindType(native_fields[i].kind == PDX_V3_DATA_FIELD_DECREE_ID ? "Decree" : "DecreeType");
                    compile_errors[i] = type < 0 ? ERROR_NOT_READY : ERROR_SUCCESS;
                }
            } else {
                compile_errors[i] = compile(expression, properties[i]);
                if (!compile_errors[i] && (is_native_field(i) || !original_source_descriptors.empty())
                    && (get<uint32_t>(properties[i]->value.bytes, 12) || get<uint32_t>(properties[i]->value.bytes, 172)))
                    compile_errors[i] = ERROR_NOT_SUPPORTED; // no lazy argument program in a borrowed field
            }
        }
        if (group) {
            observations.reserve(group->sources_count);
            cells.reserve(size_t(page->limit) * page->fields_count);
            source_properties.resize(group->sources_count); source_errors.resize(group->sources_count);
            for (size_t i = cursor.source; i < group->sources_count; ++i)
                source_errors[i] = compile_source(i, source_properties[i]);
        } else if (page->source_bytes)
            source_compile_error = compile_source(0, source_property);
        if (page->flags & PDX_V3_DATA_PAGE_CAPTURE_ARGUMENTS) {
            const auto* extension = reinterpret_cast<const PdxV3DataPageScope*>(page + 1);
            const auto* spans = reinterpret_cast<const PdxV3DataPageSpan*>(
                reinterpret_cast<const char*>(page) + extension->arguments_offset);
            arguments.compile(spans, extension->arguments_count,
                [this](PdxV3DataPageSpan expression, std::unique_ptr<PropertyOwner>& property) { return compile(expression, property); });
            if (!(page->flags & PDX_V3_DATA_PAGE_SCOPED)) source_compile_error = capture_arguments();
        }
    }
    Status capture_frame(PageCapturedArguments& frame, uint32_t count) {
        if (count != frame.expressions.size()) return ERROR_INVALID_PARAMETER;
        // Every compilation finished before capture. Allocate all storage before
        // publishing refs so pointers remain stable through every nested frame.
        frame.values.resize(count); frame.references.resize(count);
        for (size_t i = 0; i < count; ++i) {
            if (frame.errors[i]) return frame.errors[i];
            frame.values[i] = std::make_unique<ScopedProjectionAdapter::Source>();
            auto bytes = request(frame.expressions[i], 0);
            auto* r = reinterpret_cast<PdxV3DataRequest*>(bytes.data());
            auto& held = *frame.values[i];
            PageEvaluation evaluation{&root_bindings, nullptr, nullptr, nullptr, &domain_types};
            const auto status = pdx::data_projection_core::capture_produced(held.handle,
                [&] { return run_expression_locked(r, nullptr, page->world, nullptr, nullptr,
                    false, frame.properties[i].get(), &evaluation); },
                [r]() noexcept { return r->value_handle; });
            if (status) return status;
            held.node = find_value(held.handle);
            if (!held.node) return ERROR_INVALID_HANDLE;
            frame.references[i] = &held.node->value;
        }
        return ERROR_SUCCESS;
    }
    Status capture_arguments() {
        if (source_compile_error) return source_compile_error;
        if (!(page->flags & PDX_V3_DATA_PAGE_CAPTURE_ARGUMENTS)) return ERROR_SUCCESS;
        const auto count = get<uint32_t>(source_property->value.bytes, 12)
                         + get<uint32_t>(source_property->value.bytes, 172);
        return capture_frame(arguments, count);
    }
    void begin_source(pdx::data_projection_core::SourceCursor position, size_t budget) {
        current_start = static_cast<uint32_t>(position.row); row_budget = static_cast<uint32_t>(budget);
        selected_rows = 0; row_offset = page->row_count;
        observations.push_back({static_cast<uint32_t>(position.source), ERROR_IO_PENDING, 0, 0, 0,
            current_start, 0, current_start, row_offset, 0, 1, 0});
    }
    void observe_row(Status selected) {
        observation().next = static_cast<uint32_t>(cursor.row);
        if (selected) observation().range_complete = 0;
    }
    void end_source(Status status, pdx::data_projection_core::SourceCursor position) {
        auto& observed = observation(); observed.error = status;
        observed.next = static_cast<uint32_t>(position.row);
        observed.finished = observed.count_valid && observed.next >= observed.source_count && !status;
        if (status) observed.range_complete = 0;
        range_complete = range_complete && observed.range_complete;
    }
    bool source_finished() const { return observations.back().finished != 0; }
    Status run_compiled() {
        return group ? pdx::data_projection_core::run_compiled_groups(*this, cursor, group->sources_count, page->limit)
                     : pdx::data_projection_core::run_compiled_page(*this);
    }
    Status open(Source& source) {
        uint32_t count = page->rows_count;
        if (has_source()) {
            const auto error = group ? source_errors[cursor.source] : source_compile_error;
            if (error) return error;
            const auto index = group ? cursor.source : 0;
            if (original_source(index)) {
                source.original_array = true;
                pdx::original_source_core::Error resolved;
                if (company_source(index)) {
                    // Native no-argument getter returns CURRENT TLS SLOT, not
                    // DB/CPdxPtr. Never keep it past this callback's copied read.
                    using SlotGetter = uintptr_t (__fastcall*)();
                    const auto slot = reinterpret_cast<SlotGetter>(base() + pdx::v3_company_sources::slot_getter_rva)();
                    // This exact production early-return branch is also
                    // exercised with an injected owned SLOT in the CPU host.
                    const auto status = pdx::v3_company_sources::open_page_source(*page, slot, base(), source.original);
                    if (status) return status;
                    resolved = pdx::original_source_core::Error::ok;
                } else {
                    if (!outer_context.binding) return ERROR_INVALID_STATE;
                    PdxV3DataScope scope{};
                    const auto status = binding_scope(outer_context.binding, &scope);
                    if (status) return status;
                    // Actual native value SLOT, not underlying object/Any/lease.
                    if (scope.type_id != preset_type || scope.is_any) return ERROR_NOT_SUPPORTED;
                    resolved = pdx::v3_original_sources::resolve_preset(scope.object, source.original);
                }
                if (group) observation().source_produced = source.original.source_produced;
                if (resolved != pdx::original_source_core::Error::ok) return PdxV3DataProjectionError(resolved);
                count = static_cast<uint32_t>(source.original.count);
            } else {
                auto bytes = request(source_expression(index), 0);
                auto* r = reinterpret_cast<PdxV3DataRequest*>(bytes.data());
                PageEvaluation evaluation{&root_bindings, nullptr,
                    (page->flags & PDX_V3_DATA_PAGE_CAPTURE_ARGUMENTS) ? &arguments.references : nullptr,
                    nullptr, &domain_types};
                const auto status = pdx::data_projection_core::capture_produced(source.value.handle,
                    [&] { return run_expression_locked(r, nullptr, page->world, outer_context.binding, outer_context.owner,
                        false, group ? source_properties[cursor.source].get() : source_property.get(), &evaluation); },
                    [r]() noexcept { return r->value_handle; });
                if (group) observation().source_produced = source.value.handle != 0;
                if (status) return status;
                source.value.node = find_value(source.value.handle);
                source.value.payload = creation_payload(source.value.node);
                if (!source.value.payload) return ERROR_NOT_SUPPORTED;
                const auto observed = creation_count(source.value.payload);
                if (observed < 0) return ERROR_INVALID_DATA;
                count = static_cast<uint32_t>(observed);
            }
        }
        if (group) { observation().source_count = count; observation().count_valid = 1; }
        else { page->source_count = count; page->count_valid = 1; }
        if (current_start > count) return ERROR_NOT_FOUND;
        selected_rows = company_source() ? count : std::min(row_budget, count - current_start);
        if (!group && !company_source()) page->row_count = selected_rows;
        size_t cell_count = size_t(row_offset + selected_rows) * page->fields_count;
        if (company_source() && !pdx::v3_company_sources::cell_count(count, page->fields_count, cell_count))
            return ERROR_ARITHMETIC_OVERFLOW;
        cells.resize(cell_count);
        // Mark unexecuted cells explicitly if a later non-cell allocation fails.
        for (size_t i = size_t(row_offset) * page->fields_count; i < cells.size(); ++i) {
            auto& cell = cells[i];
            cell.row = current_start + static_cast<uint32_t>(i / page->fields_count) - row_offset;
            cell.field = static_cast<uint32_t>(i % page->fields_count);
            cell.error = ERROR_IO_PENDING; cell.kind = PDX_V3_DATA_EMPTY;
            cell.type_id = -1; cell.binding_mode = cell.binding_kind = UINT32_MAX;
        }
        return ERROR_SUCCESS;
    }
    Status select(Source& source, size_t row, Row& selected) {
        if (group) {
            observation().row_count = static_cast<uint32_t>(row + 1);
            page->row_count = row_offset + observation().row_count;
        }
        if (company_source()) page->row_count = static_cast<uint32_t>(row + 1);
        if (!has_source()) return ERROR_SUCCESS;
        DWORD status = ERROR_SUCCESS;
        try {
            if (source.original_array) {
                uintptr_t original = 0;
                const auto observed = company_source()
                    ? pdx::v3_company_sources::element(source.original, current_start + row, original)
                    : pdx::v3_original_sources::preset_element(source.original, current_start + row, original);
                if (observed != pdx::original_source_core::Error::ok) status = PdxV3DataProjectionError(observed);
                else if (company_source()) {
                    using Factory = void** (__fastcall*)(void**, const uintptr_t*);
                    selected.company.pointer = pdx::v3_company_sources::construct_wrapper(original,
                        reinterpret_cast<Factory>(base() + pdx::v3_company_sources::wrapper_factory_rva));
                    status = selected.company.pointer
                        ? bind_object(selected.company.pointer, company_type, &selected.element.value)
                        : ERROR_NOT_ENOUGH_MEMORY;
                } else status = bind_scoped_object(original, setting_type, &selected.element.value);
            } else status = creation_element_observed(source.value.payload,
                static_cast<int32_t>(current_start + row), &selected.element.value);
        }
        catch (const std::bad_alloc&) { return ERROR_NOT_ENOUGH_MEMORY; }
        catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
        if (status) return status;
        const auto mode = selected.element.value.bytes[48];
        if (mode == 2 || mode == 3) {
            selected.binding_type_offset = copied.size();
            // Describe the original Any, not a reconstructed/retagged value.
            // This is copied metadata, never a prerequisite for field reads.
            try {
                const auto type = get<uintptr_t>(selected.element.value.bytes) & ~uintptr_t(1);
                if (type == empty_type()) selected.binding_kind = PDX_V3_DATA_EMPTY;
                else {
                    const auto name = any_name(get<uintptr_t>(type));
                    selected.binding_kind = classify(name);
                    pdx::data_projection_core::append_bytes(name.data(), name.size(), copied);
                    selected.binding_type_bytes = name.size();
                }
            } catch (const std::bad_alloc&) { selected.binding_error = ERROR_NOT_ENOUGH_MEMORY; }
              catch (...) { selected.binding_error = ERROR_UNHANDLED_EXCEPTION; }
        }
        return ERROR_SUCCESS;
    }
    Status copy_native_field(Source& source, Row& selected, size_t field, PdxV3DataPageCell& cell) {
        if (native_fields[field].kind == PDX_V3_DATA_FIELD_GAME_RULE_KEY) {
            PdxV3DataScope scope{};
            const auto status = binding_scope(&selected.element.value, &scope);
            if (status) return status;
            using CStringCopy = void* (__fastcall*)(void*, const void*);
            using CStringDestroy = void (__fastcall*)(void*);
            return pdx::v3_rule_fields::copy_key(scope, selected.element.value.bytes[48], gui_rule_type, setting_type,
                reinterpret_cast<CStringCopy>(base() + pdx::v3_native_fields::cstring_copy_rva),
                reinterpret_cast<CStringDestroy>(base() + pdx::v3_native_fields::cstring_destroy_rva),
                [&](const void* bytes, uint64_t count) {
                    cell.kind = PDX_V3_DATA_STRING; cell.type_id = -1; cell.total = count;
                    if (count) pdx::data_projection_core::append_bytes(bytes, static_cast<size_t>(count), copied);
                });
        }
        uintptr_t wrapper = 0;
        // The original row and every ancestor walker frame remain alive through
        // receiver evaluation, key copy, then original Any-container cleanup.
        std::vector<unsigned char> receiver_bytes;
        ScopedProjectionAdapter::Source receiver;
        const auto expression = field_expression(field);
        if (expression.count) {
            receiver_bytes = request(expression, 0);
            auto* r = reinterpret_cast<PdxV3DataRequest*>(receiver_bytes.data());
            PageEvaluation evaluation{&root_bindings, nullptr, nullptr, nullptr, &domain_types};
            const auto status = pdx::data_projection_core::capture_produced(receiver.handle,
                [&] { return run_expression_locked(r, nullptr, page->world, &selected.element.value,
                    source.value.node ? source.value.node : outer_context.owner,
                    false, properties[field].get(), &evaluation); },
                [r]() noexcept { return r->value_handle; });
            if (status) return status;
            receiver.node = find_value(receiver.handle);
            if (!receiver.node) return ERROR_INVALID_HANDLE;
            const auto decoded = pdx::v3_native_fields::borrowed_decree_type(base(), receiver.node->value, &wrapper);
            if (decoded) return decoded;
        } else {
            PdxV3DataScope scope{};
            const auto status = binding_scope(&selected.element.value, &scope);
            if (status) return status;
            // Actual named row scope only. Never reinterpret an Any pointer as
            // a wrapper or retag GetType's distinct raw return to this type.
            const auto expected = native_fields[field].kind == PDX_V3_DATA_FIELD_DECREE_ID ? native_decree : native_decree_type;
            if (scope.type_id != expected || scope.is_any) return ERROR_NOT_SUPPORTED;
            wrapper = scope.object;
        }
        if (native_fields[field].kind == PDX_V3_DATA_FIELD_DECREE_ID) {
            uint32_t id = 0;
            const auto status = pdx::v3_native_fields::decree_id(wrapper, &id);
            if (status) return status;
            cell.kind = PDX_V3_DATA_U32; cell.type_id = -1; cell.raw_bits = id; cell.total = sizeof(id);
            pdx::data_projection_core::append_bytes(&id, sizeof(id), copied);
            return ERROR_SUCCESS;
        }
        using CStringCopy = void* (__fastcall*)(void*, const void*);
        using CStringDestroy = void (__fastcall*)(void*);
        return pdx::v3_native_fields::copy_decree_type_key(wrapper,
            reinterpret_cast<CStringCopy>(base() + pdx::v3_native_fields::cstring_copy_rva),
            reinterpret_cast<CStringDestroy>(base() + pdx::v3_native_fields::cstring_destroy_rva),
            [&](const void* bytes, uint64_t count) {
                cell.kind = PDX_V3_DATA_STRING; cell.type_id = -1; cell.total = count;
                if (count) pdx::data_projection_core::append_bytes(bytes, static_cast<size_t>(count), copied);
            });
    }
    void copy_cell(Source& source, Row& selected, size_t row, size_t field, Status selection_error) {
        auto& cell = cells[(row_offset + row) * page->fields_count + field];
        cell.row = static_cast<uint32_t>(current_start + row); cell.field = static_cast<uint32_t>(field);
        cell.offset = copied.size(); cell.error = selection_error ? selection_error : compile_errors[field];
        if (has_source() && !selection_error) cell.binding_mode = selected.element.value.bytes[48];
        cell.binding_kind = selected.binding_kind; cell.binding_error = selected.binding_error;
        cell.binding_type_offset = selected.binding_type_offset; cell.binding_type_bytes = selected.binding_type_bytes;
        if (cell.error) return;
        if (is_native_field(field)) {
            try { cell.error = copy_native_field(source, selected, field, cell); }
            catch (const std::bad_alloc&) { cell.error = ERROR_NOT_ENOUGH_MEMORY; }
            catch (...) { cell.error = ERROR_UNHANDLED_EXCEPTION; }
            cell.bytes = copied.size() - cell.offset;
            return;
        }
        std::vector<unsigned char> bytes;
        PdxV3DataRequest* r = nullptr;
        try {
            bytes = request(field_expression(field), has_source() ? 0 : row);
            r = reinterpret_cast<PdxV3DataRequest*>(bytes.data());
            PdxV3DataCopiedBoundary boundary{r};
            PageEvaluation evaluation{has_source() ? &root_bindings : &selected.bindings, &copied,
                nullptr, nullptr, &domain_types};
            cell.error = run_expression_locked(r, nullptr, page->world,
                has_source() ? &selected.element.value : nullptr,
                has_source() ? (source.value.node ? source.value.node : outer_context.owner) : nullptr,
                true, properties[field].get(), &evaluation);
        } catch (const std::bad_alloc&) { cell.error = ERROR_NOT_ENOUGH_MEMORY; }
          catch (...) { cell.error = ERROR_UNHANDLED_EXCEPTION; }
        // Keep already-observed metadata even if copying bytes failed. The
        // boundary scrub has run on both normal and exception exits.
        if (r) {
            cell.type_id = r->type_id;
            cell.kind = r->kind; cell.total = r->total; cell.raw_bits = r->raw_bits;
        }
        cell.bytes = copied.size() - cell.offset;
    }
    void publish() {
        // Full snapshot keeps only attempted original positions on an outer
        // failure. Count=N remains the required domain, never a complete flag.
        if (company_source()) cells.resize(size_t(page->row_count) * page->fields_count);
        if (group) {
            cells.resize(size_t(page->row_count) * page->fields_count);
            if (page->error && !observations.empty() && observation().error == ERROR_IO_PENDING) {
                observation().error = page->error; observation().range_complete = 0;
                observation().next = static_cast<uint32_t>(cursor.row);
            }
        }
        const size_t prefix = group ? sizeof(PdxV3DataPageGroupOutput) + observations.size() * sizeof(PdxV3DataSourceObservation) : 0;
        PdxV3DataPagePublicationBuffer publication;
        if (!PdxV3DataPageAllocatePublication(*page, prefix, cells.size(), copied.size(),
            [](size_t bytes) { return VirtualAlloc(nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE); },
            [] { return GetLastError(); }, publication)) return;
        const auto table_bytes = publication.table_bytes, payload_offset = publication.payload_offset, bytes = publication.bytes;
        void* storage = publication.storage;
        for (auto& cell : cells) {
            cell.offset += payload_offset; cell.binding_type_offset += payload_offset;
        }
        if (group) {
            const PdxV3DataPageGroupOutput output{static_cast<uint32_t>(prefix),
                static_cast<uint32_t>(cursor.source), static_cast<uint32_t>(cursor.row),
                static_cast<uint32_t>(observations.size()), cursor.source < group->sources_count,
                range_complete && !page->error};
            memcpy(storage, &output, sizeof(output));
            if (!observations.empty()) memcpy(static_cast<unsigned char*>(storage) + sizeof(output),
                observations.data(), observations.size() * sizeof(PdxV3DataSourceObservation));
        }
        if (table_bytes) memcpy(static_cast<unsigned char*>(storage) + prefix, cells.data(), table_bytes);
        if (!copied.empty()) memcpy(static_cast<unsigned char*>(storage) + payload_offset, copied.data(), copied.size());
        page->output_address = reinterpret_cast<uintptr_t>(storage); page->output_size = bytes;
        page->cells_count = static_cast<uint32_t>(cells.size());
    }
};

struct PageScopedAdapter {
    using Status = DWORD;
    using Context = ScopedProjectionAdapter::Context;
    using Source = ScopedProjectionAdapter::Source;
    using Binding = BindingOwner;
    PageProjectionAdapter& page;
    PdxV3DataPageScope* scoped;
    PdxV3DataCreationStep* steps;
    std::vector<std::unique_ptr<PropertyOwner>> properties;
    std::vector<PageCapturedArguments> arguments;
    PageScopedAdapter(PageProjectionAdapter& adapter, PdxV3DataPageScope* scope)
        : page(adapter), scoped(scope), steps(reinterpret_cast<PdxV3DataCreationStep*>(
            reinterpret_cast<unsigned char*>(adapter.page) + scope->steps_offset)) {}
    bool ok(Status status) const { return status == ERROR_SUCCESS; }
    Context root_context() const { return {}; }
    Context context(Source& source, Binding& binding) const { return {&binding.value, source.node}; }
    Status compile_all(size_t depth) {
        page.compile_all();
        properties.resize(depth); arguments.resize(depth);
        const auto* frames = page.page->flags & PDX_V3_DATA_PAGE_CAPTURE_STEP_ARGUMENTS
            ? reinterpret_cast<const PdxV3DataPageSpan*>(reinterpret_cast<const char*>(page.page) + scoped->reserved) : nullptr;
        for (size_t i = 0; i < depth; ++i) {
            const auto status = page.compile({steps[i].source_text_offset, steps[i].source_text_bytes}, properties[i]);
            if (status) return status;
            if (frames && frames[i].count) {
                const auto* spans = reinterpret_cast<const PdxV3DataPageSpan*>(
                    reinterpret_cast<const char*>(page.page) + frames[i].offset);
                arguments[i].compile(spans, frames[i].count,
                    [this](PdxV3DataPageSpan expression, std::unique_ptr<PropertyOwner>& property) { return page.compile(expression, property); });
            }
        }
        // No original source/row has been borrowed yet. Final and EVERY prefix
        // frame capture genuine Anys in the same owned root bindings here.
        auto status = page.capture_arguments();
        if (status) return status;
        for (size_t i = 0; i < depth; ++i) if (frames && frames[i].count) {
            const auto count = get<uint32_t>(properties[i]->value.bytes, 12)
                             + get<uint32_t>(properties[i]->value.bytes, 172);
            status = page.capture_frame(arguments[i], count);
            if (status) return status;
        }
        return ERROR_SUCCESS;
    }
    Status produce(size_t level, Context previous, Source& source) {
        auto bytes = page.request({steps[level].source_text_offset, steps[level].source_text_bytes}, 0);
        auto* r = reinterpret_cast<PdxV3DataRequest*>(bytes.data());
        PageEvaluation evaluation{&page.root_bindings, nullptr,
            arguments[level].references.empty() ? nullptr : &arguments[level].references,
            nullptr, &page.domain_types};
        const auto status = pdx::data_projection_core::capture_produced(source.handle,
            [&] { return run_expression_locked(r, nullptr, page.page->world,
                previous.binding, previous.owner, false, properties[level].get(), &evaluation); },
            [r]() noexcept { return r->value_handle; });
        if (status) return status;
        steps[level].source_produced = 1;
        source.node = find_value(source.handle); source.payload = creation_payload(source.node);
        return source.payload ? ERROR_SUCCESS : ERROR_NOT_SUPPORTED;
    }
    Status count_and_check(size_t level, Source& source) {
        return ScopedProjectionAdapter::count_step(steps[level], source);
    }
    Status select(size_t level, Source& source, Binding& binding) {
        const auto status = ScopedProjectionAdapter::select_step(steps[level], source, binding);
        if (!status) scoped->completed_steps = static_cast<uint32_t>(level + 1);
        return status;
    }
    Status finish(Context previous) {
        page.outer_context = previous;
        return page.run_compiled();
    }
};

bool valid_page(PdxV3DataPageRequest* r) {
    const auto span = [r](uint32_t offset, uint64_t bytes) {
        return offset >= sizeof(*r) && offset <= r->size && bytes <= uint64_t(r->size) - offset;
    };
    const bool grouped = (r->flags & PDX_V3_DATA_PAGE_GROUPED) != 0;
    const bool native_fields = (r->flags & PDX_V3_DATA_PAGE_NATIVE_FIELDS) != 0;
    const bool original_sources = (r->flags & PDX_V3_DATA_PAGE_ORIGINAL_SOURCES) != 0;
    bool full_company = false;
    if (original_sources && !grouped && r->source_bytes == sizeof(PdxV3DataPageSource)
        && span(r->source_offset, sizeof(PdxV3DataPageSource))) {
        PdxV3DataPageSource source{};
        std::memcpy(&source, reinterpret_cast<const char*>(r) + r->source_offset, sizeof(source));
        full_company = source.kind == PDX_V3_DATA_SOURCE_FULL_COMPANY_TYPES;
    }
    // Capability subset, not a claim that future combinations are unsafe.
    // Explicit zero window means full N; never reinterpret a caller budget64.
    if (full_company && (r->start || r->limit
        || (r->flags & ~(PDX_V3_DATA_PAGE_ORIGINAL_SOURCES | PDX_V3_DATA_PAGE_NATIVE_FIELDS)))) return false;
    if ((r->flags & ~(PDX_V3_DATA_PAGE_SCOPED | PDX_V3_DATA_PAGE_CAPTURE_ARGUMENTS
                    | PDX_V3_DATA_PAGE_GROUPED | PDX_V3_DATA_PAGE_CAPTURE_STEP_ARGUMENTS | PDX_V3_DATA_PAGE_NATIVE_FIELDS
                    | PDX_V3_DATA_PAGE_ORIGINAL_SOURCES)) || !r->fields_count || r->fields_count > PDX_V3_DATA_PAGE_FIELDS
        || !r->rows_count || r->rows_count > PDX_V3_DATA_PAGE_ROWS
        || (!full_company && (!r->limit || r->limit > PDX_V3_DATA_PAGE_ROWS))
        || r->start > INT32_MAX || ((r->source_bytes || grouped) && r->rows_count != 1)
        || (!r->source_bytes && !grouped && (r->start || r->limit < r->rows_count))
        || r->fields_offset % 4 || r->rows_offset % 4
        || !span(r->fields_offset, uint64_t(r->fields_count)
            * (native_fields ? sizeof(PdxV3DataPageField) : sizeof(PdxV3DataPageSpan)))
        || !span(r->rows_offset, uint64_t(r->rows_count) * sizeof(PdxV3DataPageSpan))) return false;
    const auto* bytes = reinterpret_cast<const char*>(r);
    const auto text = [&](PdxV3DataPageSpan value) {
        return value.count && value.count <= INT32_MAX && span(value.offset, value.count)
            && !memchr(bytes + value.offset, 0, value.count);
    };
    if (r->source_bytes && !original_sources && !text({r->source_offset, r->source_bytes})) return false;
    bool any_original_source = false, any_company_source = false;
    const auto valid_source = [&](const PdxV3DataPageSource& source) {
        if (source.kind != PDX_V3_DATA_SOURCE_EXPRESSION) any_original_source = true;
        if (source.kind == PDX_V3_DATA_SOURCE_FULL_COMPANY_TYPES) any_company_source = true;
        return pdx::v3_original_sources::valid_source(source, text);
    };
    if (original_sources && !grouped) {
        if (r->source_bytes != sizeof(PdxV3DataPageSource) || !span(r->source_offset, sizeof(PdxV3DataPageSource))) return false;
        PdxV3DataPageSource source{}; std::memcpy(&source, bytes + r->source_offset, sizeof(source));
        if (!valid_source(source)) return false;
    }
    if (r->flags) {
        if (r->size < sizeof(*r) + sizeof(PdxV3DataPageScope) || (!r->source_bytes && !grouped)) return false;
        auto* scope = reinterpret_cast<PdxV3DataPageScope*>(r + 1);
        scope->completed_steps = 0;
        if (original_sources && !full_company && (!(r->flags & PDX_V3_DATA_PAGE_SCOPED) || !scope->steps_count
            || (r->flags & PDX_V3_DATA_PAGE_CAPTURE_ARGUMENTS))) return false;
        if ((!(r->flags & PDX_V3_DATA_PAGE_CAPTURE_STEP_ARGUMENTS) && scope->reserved)
            || scope->steps_count > PDX_V3_DATA_MAX_SCOPED_DEPTH
            || bool(scope->steps_count) != bool(r->flags & PDX_V3_DATA_PAGE_SCOPED)
            || scope->arguments_count > PDX_V3_DATA_PAGE_FIELDS
            || bool(scope->arguments_count) != bool(r->flags & PDX_V3_DATA_PAGE_CAPTURE_ARGUMENTS)
            || scope->steps_offset < sizeof(*r) + sizeof(*scope) || scope->steps_offset % 4
            || !span(scope->steps_offset, uint64_t(scope->steps_count) * sizeof(PdxV3DataCreationStep))
            || scope->arguments_offset < sizeof(*r) + sizeof(*scope) || scope->arguments_offset % 4
            || !span(scope->arguments_offset, uint64_t(scope->arguments_count) * sizeof(PdxV3DataPageSpan))) return false;
        const auto* arguments = reinterpret_cast<const PdxV3DataPageSpan*>(bytes + scope->arguments_offset);
        for (uint32_t i = 0; i < scope->arguments_count; ++i) if (!text(arguments[i])) return false;
        auto* steps = reinterpret_cast<PdxV3DataCreationStep*>(reinterpret_cast<char*>(r) + scope->steps_offset);
        for (uint32_t i = 0; i < scope->steps_count; ++i) {
            auto& step = steps[i];
            step.source_produced = step.count_valid = step.source_count = step.element_obtained = 0;
            step.element_type_id = -1; step.binding_mode = UINT32_MAX;
            if (step.index > INT32_MAX || (step.expected_count != UINT32_MAX && step.expected_count > INT32_MAX)
                || !text({step.source_text_offset, step.source_text_bytes})) return false;
        }
        if (r->flags & PDX_V3_DATA_PAGE_CAPTURE_STEP_ARGUMENTS) {
            const auto header = sizeof(*r) + sizeof(*scope) + (grouped ? sizeof(PdxV3DataPageGroup) : 0);
            if (!(r->flags & PDX_V3_DATA_PAGE_SCOPED) || !scope->steps_count || scope->reserved % 4
                || scope->reserved < header
                || !span(scope->reserved, uint64_t(scope->steps_count) * sizeof(PdxV3DataPageSpan))) return false;
            const auto* frames = reinterpret_cast<const PdxV3DataPageSpan*>(bytes + scope->reserved);
            for (uint32_t i = 0; i < scope->steps_count; ++i) {
                if (frames[i].count > PDX_V3_DATA_PAGE_FIELDS) return false;
                if (!frames[i].count) continue; // no captured-frame substitution for this step
                if (frames[i].offset < header || frames[i].offset % 4
                    || !span(frames[i].offset, uint64_t(frames[i].count) * sizeof(PdxV3DataPageSpan))) return false;
                const auto* captures = reinterpret_cast<const PdxV3DataPageSpan*>(bytes + frames[i].offset);
                for (uint32_t j = 0; j < frames[i].count; ++j) if (!text(captures[j])) return false;
            }
        }
    }
    if (grouped) {
        if (r->size < sizeof(*r) + sizeof(PdxV3DataPageScope) + sizeof(PdxV3DataPageGroup)
            || r->source_bytes || (r->flags & PDX_V3_DATA_PAGE_CAPTURE_ARGUMENTS)) return false;
        const auto* group = reinterpret_cast<const PdxV3DataPageGroup*>(
            reinterpret_cast<const PdxV3DataPageScope*>(r + 1) + 1);
        if (group->reserved || !group->sources_count || group->sources_count > PDX_V3_DATA_PAGE_SOURCES
            || group->source_index > group->sources_count || group->sources_offset % 4
            || group->sources_offset < sizeof(*r) + sizeof(PdxV3DataPageScope) + sizeof(*group)
            || !span(group->sources_offset, uint64_t(group->sources_count)
                * (original_sources ? sizeof(PdxV3DataPageSource) : sizeof(PdxV3DataPageSpan)))) return false;
        if (original_sources) {
            for (uint32_t i = 0; i < group->sources_count; ++i) {
                PdxV3DataPageSource source{};
                std::memcpy(&source, bytes + group->sources_offset + size_t(i) * sizeof(source), sizeof(source));
                if (!valid_source(source)) return false;
            }
        } else {
            const auto* sources = reinterpret_cast<const PdxV3DataPageSpan*>(bytes + group->sources_offset);
            for (uint32_t i = 0; i < group->sources_count; ++i) if (!text(sources[i])) return false;
        }
    }
    if (original_sources && !any_original_source) return false;
    if (any_company_source && !full_company) return false; // group not yet implemented for full snapshot
    if (native_fields) {
        const auto* fields = reinterpret_cast<const PdxV3DataPageField*>(bytes + r->fields_offset);
        if (!pdx::v3_native_fields::valid_fields(fields, r->fields_count, text)) return false;
    } else {
        const auto* fields = reinterpret_cast<const PdxV3DataPageSpan*>(bytes + r->fields_offset);
        for (uint32_t i = 0; i < r->fields_count; ++i) if (!text(fields[i])) return false;
    }
    const auto* rows = reinterpret_cast<const PdxV3DataPageSpan*>(bytes + r->rows_offset);
    for (uint32_t i = 0; i < r->rows_count; ++i)
        if (rows[i].offset % alignof(PdxV3DataBindingInput)
            || !span(rows[i].offset, uint64_t(rows[i].count) * sizeof(PdxV3DataBindingInput))) return false;
    return true;
}
}
void WINAPI PdxV3DataProjectPageInterface(void* application, void* opaque) {
    auto* r = static_cast<PdxV3DataPageRequest*>(opaque);
    if (!r || r->size < sizeof(*r)) return;
    r->error = ERROR_INVALID_PARAMETER; r->execution_thread_id = GetCurrentThreadId();
    r->source_count = r->count_valid = r->row_count = r->cells_count = 0;
    r->world = r->output_address = r->output_size = 0;
    if (!valid_page(r)) return;
    const auto app = reinterpret_cast<uintptr_t>(application);
    if (!app || app != get<uintptr_t>(base(), app_rva) || get<uint8_t>(app, 0x258)) {
        r->error = ERROR_NOT_READY; return;
    }
    try {
        AccessScope access;
        r->world = get<uintptr_t>(app, 0x608);
        // Application-owned global sources (for example the setup GUI) need
        // no world. Full-ID roots still use their original world resolver.
        if (!PdxV3DataFactory()) { r->error = ERROR_NOT_READY; return; }
        ValueLock lock;
        PageProjectionAdapter adapter(r);
        try {
            if (r->flags & PDX_V3_DATA_PAGE_SCOPED) {
                auto* scope = reinterpret_cast<PdxV3DataPageScope*>(r + 1);
                PageScopedAdapter chain(adapter, scope);
                r->error = pdx::data_projection_core::run_scoped_chain(chain, scope->steps_count);
            } else { adapter.compile_all(); r->error = adapter.run_compiled(); }
        }
        catch (const std::bad_alloc&) { r->error = ERROR_NOT_ENOUGH_MEMORY; }
        catch (...) { r->error = ERROR_UNHANDLED_EXCEPTION; }
        adapter.publish();
    } catch (const std::bad_alloc&) { r->error = ERROR_NOT_ENOUGH_MEMORY; }
      catch (...) { r->error = ERROR_UNHANDLED_EXCEPTION; }
}
void WINAPI PdxV3DataProjectSourcesInterface(void* application, void* opaque) {
    PdxV3DataProjectPageInterface(application, opaque);
}
void WINAPI PdxV3DataProjectTypedStepsInterface(void* application, void* opaque) {
    PdxV3DataProjectPageInterface(application, opaque);
}
void WINAPI PdxV3DataProjectNativeFieldsInterface(void* application, void* opaque) {
    PdxV3DataProjectPageInterface(application, opaque);
}
void WINAPI PdxV3DataProjectOriginalSourcesInterface(void* application, void* opaque) {
    PdxV3DataProjectPageInterface(application, opaque);
}
void WINAPI PdxV3DataProjectCompanyTypesInterface(void* application, void* opaque) {
    PdxV3DataProjectPageInterface(application, opaque);
}
void WINAPI PdxV3DataProjectRuleFieldsInterface(void* application, void* opaque) {
    PdxV3DataProjectPageInterface(application, opaque);
}


namespace {
static_assert(sizeof(PdxV3DataGameRuleRequest) == 160);
struct GameRuleActionPacket { PdxV3DataActionRequest action; PdxV3DataInput input; };
static_assert(sizeof(GameRuleActionPacket) == 416);

bool valid_game_rule_request(const PdxV3DataGameRuleRequest& r) {
    const auto span = [&r](uint32_t offset, uint32_t bytes) {
        return offset >= sizeof(r) && offset <= r.size && bytes <= r.size - offset;
    };
    if (r.size < sizeof(r) || r.rule_input_offset % 8 || r.setting_input_offset % 8 || r.output_offset % 8
        || r.rule_input_bytes < sizeof(PdxV3DataInput) || r.setting_input_bytes < sizeof(PdxV3DataInput)
        || !span(r.rule_input_offset, r.rule_input_bytes) || !span(r.setting_input_offset, r.setting_input_bytes)
        || !span(r.output_offset, r.output_bytes)
        || uint64_t(r.rule_input_offset) + r.rule_input_bytes > r.setting_input_offset
        || uint64_t(r.setting_input_offset) + r.setting_input_bytes > r.output_offset) return false;
    const auto* bytes = reinterpret_cast<const unsigned char*>(&r);
    for (const auto input : {std::pair<uint32_t, uint32_t>{r.rule_input_offset, r.rule_input_bytes},
                             {r.setting_input_offset, r.setting_input_bytes}}) {
        const auto* value = reinterpret_cast<const PdxV3DataInput*>(bytes + input.first);
        if (value->kind != PDX_V3_DATA_STRING || value->string_bytes > input.second - sizeof(*value)) return false;
    }
    return true;
}
std::vector<unsigned char> game_rule_expression(const char* expression) {
    const auto length = std::strlen(expression);
    std::vector<unsigned char> bytes((sizeof(PdxV3DataRequest) + length + 7) & ~size_t(7));
    auto* r = reinterpret_cast<PdxV3DataRequest*>(bytes.data());
    r->size = static_cast<uint32_t>(bytes.size()); r->action = PDX_V3_DATA_EVALUATE; r->type_id = -1;
    r->text_offset = sizeof(*r); r->text_bytes = static_cast<uint32_t>(length);
    r->bindings_offset = r->output_offset = r->size;
    std::memcpy(bytes.data() + r->text_offset, expression, length);
    return bytes;
}
// Same existing retained-value Binding64 construction, followed by ORIGINAL
// BindingScope. This copies a genuine Any; it never retags a raw root pointer.
DWORD game_rule_root_scope(const ValueNode& root, int32_t type, BindingOwner& binding, PdxV3DataScope& scope) {
    const auto record = type_record(type);
    const auto converter = record ? get<uintptr_t>(record, 224) : 0;
    if (!converter) return ERROR_NOT_SUPPORTED;
    const auto empty = empty_type(); std::memcpy(binding.value.bytes, &empty, 8);
    copy_any(binding.value.bytes, root.value);
    std::memcpy(binding.value.bytes + 40, &converter, 8);
    std::memcpy(binding.value.bytes + 56, &type, 4); binding.value.bytes[48] = 2;
    return binding_scope(&binding.value, &scope);
}
struct GameRuleConsumer {
    PdxV3DataGameRuleRequest& r;
    unsigned char* bytes;
    uint32_t cursor;
    // Properties precede every producer/frame owner so they die LAST.
    std::vector<std::vector<unsigned char>> requests;
    std::vector<std::unique_ptr<PropertyOwner>> properties;
    ValueNode owners;
    ValueNode rule_input, setting_input;
    std::vector<PdxV3DataScope> named_scopes;
    std::vector<unsigned char> named_references;
    std::vector<const PdxV3DataAny*> named_captured;
    PdxV3DataArray named_scope_array{}, named_args{};
    PdxV3DataScope row_scope{};
    // Reverse cleanup: canonical Creation -> root Binding -> root Any/source ->
    // original named frames/CStrings -> properties. No ancestor reentry.
    ScopedProjectionAdapter::Source root;
    BindingOwner root_binding;
    ScopedProjectionAdapter::Source choices;
    uintptr_t row = 0, target = 0;
    uint32_t final_capacity = 0;
    DWORD first_action_error = ERROR_SUCCESS;
    explicit GameRuleConsumer(PdxV3DataGameRuleRequest& request)
        : r(request), bytes(reinterpret_cast<unsigned char*>(&request)), cursor(r.output_offset) {}
    DWORD failed(DWORD error) {
        if (!r.error || r.error == ERROR_IO_PENDING) r.error = error;
        r.observation_error = error;
        if (r.outcome != PDX_V3_DATA_RULE_NATIVE_NULL && r.outcome != PDX_V3_DATA_RULE_OUTPUT_PARTIAL)
            r.outcome = PDX_V3_DATA_RULE_NATIVE_ERROR;
        return r.error;
    }
    DWORD budget() { r.outcome = PDX_V3_DATA_RULE_OUTPUT_PARTIAL; r.stopped = r.planned > r.completed; return failed(ERROR_MORE_DATA); }
    bool append(const std::string& value, uint32_t& offset, uint32_t& count) {
        if (value.size() > uint64_t(r.output_offset) + r.output_bytes - cursor) return false;
        offset = cursor; count = static_cast<uint32_t>(value.size());
        if (count) std::memcpy(bytes + cursor, value.data(), count);
        cursor += count; r.copied_output_bytes = cursor - r.output_offset;
        return true;
    }
    DWORD compile_all() {
        static const char* expressions[] = {"GetGameRules", "JominiGameRules.AccessNamedGameRule('').Next",
            "GuiGameRule.GetRule.GetSettings", "GuiGameRule.GetSetting.GetKey", "GameRuleSetting.GetKey",
            "GuiGameRule.Next", "GuiGameRule.Prev"};
        requests.reserve(7); properties.reserve(7);
        for (const auto* expression : expressions) {
            requests.push_back(game_rule_expression(expression));
            properties.push_back(std::make_unique<PropertyOwner>());
            const auto error = compile_element_expression(reinterpret_cast<PdxV3DataRequest*>(requests.back().data()), properties.back().get());
            if (error) return error;
        }
        return ERROR_SUCCESS;
    }
    DWORD produce(size_t property, ScopedProjectionAdapter::Source& source, const PdxV3DataScope* receiver = nullptr) {
        auto* request = reinterpret_cast<PdxV3DataRequest*>(requests[property].data());
        PageEvaluation evaluation{&owners, nullptr, nullptr, receiver};
        const auto status = pdx::data_projection_core::capture_produced(source.handle,
            [&] { return run_expression_locked(request, nullptr, 0, nullptr, nullptr, false,
                properties[property].get(), &evaluation); },
            [request]() noexcept { return request->value_handle; });
        if (status) return status;
        source.node = find_value(source.handle);
        return source.node ? ERROR_SUCCESS : ERROR_INVALID_HANDLE;
    }
    DWORD copied_key(size_t property, std::string& output, PdxV3DataBinding* element = nullptr) {
        std::vector<unsigned char> copied;
        PageEvaluation evaluation{&owners, &copied, nullptr, element ? nullptr : &row_scope};
        auto* request = reinterpret_cast<PdxV3DataRequest*>(requests[property].data());
        const auto status = run_expression_locked(request, nullptr, 0, element, nullptr, true,
            properties[property].get(), &evaluation);
        if (status) return status;
        if (request->kind != PDX_V3_DATA_STRING) return ERROR_INVALID_DATA;
        output.assign(copied.begin(), copied.end());
        return ERROR_SUCCESS;
    }
    DWORD resolve() {
        r.stage = PDX_V3_DATA_RULE_ROOT;
        const auto root_type = PdxV3DataFindType("JominiGameRules");
        const auto row_type = PdxV3DataFindType("GuiGameRule");
        if (root_type < 0 || row_type < 0) return failed(ERROR_NOT_FOUND);
        auto status = compile_all(); if (status) return failed(status);
        status = any_input(reinterpret_cast<const PdxV3DataInput*>(bytes + r.rule_input_offset), r.rule_input_bytes, &rule_input.value, &rule_input);
        if (status) return failed(status);
        status = any_input(reinterpret_cast<const PdxV3DataInput*>(bytes + r.setting_input_offset), r.setting_input_bytes, &setting_input.value, &setting_input);
        if (status) return failed(status);
        status = produce(0, root); if (status) return failed(status);
        PdxV3DataScope initial{};
        status = game_rule_root_scope(*root.node, root_type, root_binding, initial);
        if (status) return failed(status);
        r.root_observed = 1; r.root_found = initial.object != 0;
        if (!initial.object) { r.outcome = PDX_V3_DATA_RULE_NATIVE_NULL; return ERROR_SUCCESS; }
        r.stage = PDX_V3_DATA_RULE_RESOLVE;
        auto& property = properties[1]->value;
        const auto parameters = get<uint32_t>(property.bytes, 12) + get<uint32_t>(property.bytes, 172);
        if (parameters != 1) return failed(ERROR_INVALID_PARAMETER);
        const auto scope_count = get<uint32_t>(property.bytes, 196);
        const auto required = get<uintptr_t>(property.bytes, 184);
        named_scopes.resize(scope_count);
        for (uint32_t i = 0; i < scope_count; ++i) {
            if (get<int32_t>(required, 8ull * i) != root_type) return failed(ERROR_NOT_FOUND);
            named_scopes[i] = initial;
        }
        named_references.resize(parameters * sizeof(PdxV3DataDirectParameterRef));
        named_scope_array = {reinterpret_cast<uintptr_t>(named_scopes.data()), scope_count, scope_count, 0};
        named_args = {reinterpret_cast<uintptr_t>(named_references.data()), parameters, parameters, 0};
        named_captured = {&rule_input.value};
        PdxV3DataScopeValue receiver{}, prepared_initial{}; int32_t initial_type = -1;
        status = prepare_captured(&property, &named_scope_array, &named_args, &receiver, &initial_type,
            named_captured, &prepared_initial);
        // Report an inner-produced zero only after its actual initialized root
        // receiver was nonnull. A zero local/default or earlier frame failure is
        // NOT evidence that the original named producer returned null.
        if (prepared_initial.object && !receiver.object) { r.row_observed = 1; r.outcome = PDX_V3_DATA_RULE_NATIVE_NULL; }
        if (status) return failed(status);
        r.row_observed = 1; r.row_found = receiver.object != 0;
        if (!receiver.object) { r.outcome = PDX_V3_DATA_RULE_NATIVE_NULL; return ERROR_SUCCESS; }
        // initial_type is deliberately NOT a terminal type. Exact fixed member
        // metadata is GuiGameRule; preserve native object/is_any unchanged.
        row_scope = {receiver.object, row_type, receiver.is_any, {0,0,0}};
        if (row_scope.is_any) return failed(ERROR_INVALID_DATA);
        row = row_scope.object;
        return ERROR_SUCCESS;
    }
    DWORD inspect() {
        r.stage = PDX_V3_DATA_RULE_CHOICES;
        auto status = produce(2, choices, &row_scope); if (status) return failed(status);
        choices.payload = creation_payload(choices.node);
        if (!choices.payload) return failed(ERROR_NOT_SUPPORTED);
        const auto count = creation_count(choices.payload);
        if (count < 0) return failed(ERROR_INVALID_DATA);
        r.count_valid = 1; r.choice_count = static_cast<uint32_t>(count);
        if (!count) { r.outcome = PDX_V3_DATA_RULE_EMPTY; return ERROR_SUCCESS; }
        const auto table_bytes = uint64_t(r.choice_count) * sizeof(PdxV3DataPageSpan);
        if (table_bytes > r.output_bytes) return budget();
        r.keys_offset = cursor; cursor += static_cast<uint32_t>(table_bytes);
        r.copied_output_bytes = cursor - r.output_offset;
        auto* keys = reinterpret_cast<PdxV3DataPageSpan*>(bytes + r.keys_offset);
        const auto current = get<uintptr_t>(row, 8);
        std::vector<unsigned char> target_bytes;
        PdxV3DataRequest probe{}; probe.size = sizeof(probe);
        status = snapshot_copied_value(&probe, setting_input.value, &target_bytes);
        if (status) return failed(status);
        const std::string requested(target_bytes.begin(), target_bytes.end());
        r.stage = PDX_V3_DATA_RULE_KEYS;
        for (uint32_t index = 0; index < r.choice_count; ++index) {
            BindingOwner choice;
            status = creation_element_observed(choices.payload, static_cast<int32_t>(index), &choice.value);
            if (status) return failed(status);
            PdxV3DataScope scope{}; status = binding_scope(&choice.value, &scope);
            if (status) return failed(status);
            if (scope.is_any) return failed(ERROR_INVALID_DATA); // exact const provider mode0, never retag
            if (scope.object == current) { r.current_found = 1; r.current_index = index; }
            std::string key; status = copied_key(4, key, &choice.value);
            if (status) return failed(status);
            if (key == requested && !r.target_found) { r.target_found = 1; r.target_index = index; target = scope.object; }
            if (!append(key, keys[index].offset, keys[index].count)) return budget();
            ++r.keys_count; final_capacity = std::max(final_capacity, static_cast<uint32_t>(key.size()));
        }
        if (current) {
            std::string key; status = copied_key(3, key); if (status) return failed(status);
            if (!append(key, r.initial_key_offset, r.initial_key_bytes)) return budget();
            r.initial_key_valid = 1;
        }
        return ERROR_SUCCESS;
    }
    GameRuleActionPacket* packet(size_t index) {
        return reinterpret_cast<GameRuleActionPacket*>(bytes + r.receipts_offset + index * sizeof(GameRuleActionPacket));
    }
    void invoke(size_t index) {
        auto* item = packet(index); *item = {};
        auto& action = item->action; auto& result = action.result;
        result.size = sizeof(*item); result.error = ERROR_IO_PENDING;
        result.action = PDX_V3_DATA_INVOKE_ACTION; result.flags = PDX_V3_DATA_EXPLICIT_ACTION; result.type_id = -1;
        result.text_offset = result.bindings_offset = result.output_offset = sizeof(*item);
        action.input_offset = sizeof(action); action.input_bytes = sizeof(item->input); action.input_kind = PDX_V3_DATA_EMPTY;
        item->input.kind = PDX_V3_DATA_EMPTY;
        r.receipts_count = static_cast<uint32_t>(index + 1);
        r.receipts_bytes = r.receipts_count * sizeof(*item);
        r.copied_output_bytes = r.receipts_offset - r.output_offset + r.receipts_bytes;
        PageEvaluation evaluation{&owners, nullptr, nullptr, &row_scope};
        PdxV3DataCopiedBoundary copied_only{&result}; // all failure/exception exits too
        const auto error = run_expression_locked(&result, &action, 0, nullptr, nullptr, true,
            properties[r.direction == 1 ? 5 : 6].get(), &evaluation);
        if (!first_action_error && error) first_action_error = error;
        if ((!r.error || r.error == ERROR_IO_PENDING) && error) r.error = error;
    }
    void completed(size_t count) { r.completed = static_cast<uint32_t>(count); }
    bool continue_after(size_t index) { return PdxV3DataContinueAction(packet(index)->action, 1); }
    void stop() { r.stopped = 1; r.outcome = PDX_V3_DATA_RULE_ACTION_STOPPED; }
    DWORD run() {
        auto status = resolve(); if (status || !r.row_found) return status;
        status = inspect(); if (status || !r.choice_count) return status;
        r.stage = PDX_V3_DATA_RULE_PLAN;
        const auto selected = pdx::v3_game_rule_selection::plan(r.choice_count, r.current_index, r.target_index);
        r.outcome = selected.steps ? PDX_V3_DATA_RULE_UNOBSERVED : selected.outcome;
        r.direction = selected.direction; r.planned = selected.steps;
        if (!selected.steps) {
            if (selected.outcome == PDX_V3_DATA_RULE_ALREADY_SELECTED) {
                r.final_observed = r.final_selected = 1;
                r.final_key_valid = r.initial_key_valid; r.final_key_offset = r.initial_key_offset; r.final_key_bytes = r.initial_key_bytes;
            }
            r.stage = PDX_V3_DATA_RULE_COMPLETE; return ERROR_SUCCESS;
        }
        const uint64_t end = uint64_t(r.output_offset) + r.output_bytes;
        if (final_capacity > end - cursor) return budget();
        r.final_key_offset = cursor; cursor += final_capacity;
        const auto aligned = (uint64_t(cursor) + 7) & ~uint64_t(7);
        if (aligned > end) return budget();
        cursor = static_cast<uint32_t>(aligned); r.receipts_offset = cursor;
        r.copied_output_bytes = cursor - r.output_offset;
        const auto capacity = static_cast<uint32_t>(std::min<uint64_t>(selected.steps, (end - cursor) / sizeof(GameRuleActionPacket)));
        r.stage = PDX_V3_DATA_RULE_ACTIONS;
        pdx::v3_game_rule_selection::execute(selected, capacity, *this);
        // The generic loop checks continuation only when its local suffix exists.
        // This consumer also reports an actual last-record rejection (including
        // at a byte-budget boundary), without fabricating another invocation.
        if (r.receipts_count && !PdxV3DataContinueAction(packet(r.receipts_count - 1)->action, 1)) stop();
        r.stage = PDX_V3_DATA_RULE_FINAL;
        r.final_observed = 1; r.final_selected = get<uintptr_t>(row, 8) == target;
        std::string final_key; status = copied_key(3, final_key);
        if (status) return failed(status);
        if (final_key.size() > final_capacity) return budget();
        if (!final_key.empty()) std::memcpy(bytes + r.final_key_offset, final_key.data(), final_key.size());
        r.final_key_bytes = static_cast<uint32_t>(final_key.size()); r.final_key_valid = 1;
        if (r.completed < selected.steps && !r.stopped) return budget();
        if (!r.stopped) r.outcome = r.final_selected ? PDX_V3_DATA_RULE_SELECTED : PDX_V3_DATA_RULE_CURRENT_NOT_MEMBER;
        r.stage = PDX_V3_DATA_RULE_COMPLETE;
        return first_action_error;
    }
};
}
DWORD WINAPI PdxV3DataSelectGameRule(PdxV3DataGameRuleRequest* r) {
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    // Leave only declared input/output spans; no input pointer can be mistaken
    // for a produced owner, and no original borrowed pointer is published.
    std::memset(&r->stage, 0, sizeof(*r) - offsetof(PdxV3DataGameRuleRequest, stage));
    r->stage = PDX_V3_DATA_RULE_VALIDATE; r->error = ERROR_INVALID_PARAMETER;
    r->current_index = r->target_index = UINT32_MAX;
    if (!valid_game_rule_request(*r)) return r->error;
    r->error = ERROR_IO_PENDING; r->execution_thread_id = GetCurrentThreadId();
    try {
        ValueLock lock;
        GameRuleConsumer consumer(*r);
        const auto status = consumer.run();
        return r->error = (!r->error || r->error == ERROR_IO_PENDING) ? status : r->error;
    } catch (const std::bad_alloc&) {
        if (r->error == ERROR_IO_PENDING || !r->error) r->error = ERROR_NOT_ENOUGH_MEMORY;
        r->observation_error = ERROR_NOT_ENOUGH_MEMORY; r->outcome = PDX_V3_DATA_RULE_NATIVE_ERROR; return r->error;
    } catch (...) {
        if (r->error == ERROR_IO_PENDING || !r->error) r->error = ERROR_UNHANDLED_EXCEPTION;
        r->observation_error = ERROR_UNHANDLED_EXCEPTION; r->outcome = PDX_V3_DATA_RULE_NATIVE_ERROR; return r->error;
    }
}
void WINAPI PdxV3DataSelectGameRuleInterface(void*, void* opaque) {
    PdxV3DataSelectGameRule(static_cast<PdxV3DataGameRuleRequest*>(opaque));
}
