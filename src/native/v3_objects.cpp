#include "v3_objects.h"
#include <algorithm>
#include <cstddef>
#include <cstring>

namespace {
constexpr uintptr_t application_root_rva = 0x592d128;
constexpr uintptr_t world_offset = 0x608;
constexpr uintptr_t ingame_idler_vtable_rva = 0x4431500;
constexpr uint32_t id_index_mask = 0xffffff;
constexpr uint32_t invalid_id = 0xffffffff;
constexpr uintptr_t guard_enter_rva = 0x33bd640;
constexpr uintptr_t guard_leave_rva = 0x33bd750;
constexpr uintptr_t country_gdp_getter_rva = 0x1bc28e0;
using Resolver = void* (__fastcall*)(const uint32_t*);
using GdpGetter = uint64_t* (__fastcall*)(const void*, uint64_t*);
using PopIntegerGetter = int64_t (__fastcall*)(const void*);
using CharacterBoolGetter = unsigned char (__fastcall*)(const void*);
using GoodsProvider = void* (__fastcall*)();
using GoodsIndexGetter = void* (__fastcall*)(void*, int32_t);
using GoodsValueGetter = uint64_t* (__fastcall*)(const void*, uint64_t*, const void*);
using MarketPriceGetter = uint64_t* (__fastcall*)(const void*, uint64_t*);
using DefinitionKeyGetter = PdxV3DefinitionKeyView* (__fastcall*)(void*, PdxV3DefinitionKeyView*, int32_t);
using TechnologyCategoryGetter = void* (__fastcall*)(const void*);
using InterestGroupNameGetter = PdxV3OwnedCString* (__fastcall*)(PdxV3OwnedCString*, const void*);
using NativeCStringDestructor = void (__fastcall*)(PdxV3OwnedCString*);

using GuardEnter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using GuardLeave = void (__fastcall*)(void*);
struct Domain {
    uintptr_t root;
    uintptr_t fallback;
    uintptr_t resolver;
    uintptr_t id_offset;
    uint32_t record_size;
};
constexpr Domain countries{0x5964e20, 0x5964e18, 0x7bb480, 0x18,
                           sizeof(PdxV3CountryObject)};
constexpr Domain states{0x5964ef0, 0x5964e28, 0xc5af10, 8,
                        sizeof(PdxV3StateObject)};
constexpr Domain buildings{0x5966738, 0x5964278, 0xc5ae60, 0x10,
                           sizeof(PdxV3BuildingObject)};
// Each real resolver independently proves this RefDatabase slot layout.
constexpr Domain pops{0x5964ec8, 0x5964388, 0x7cacf0, 8,
                      sizeof(PdxV3PopObject)};
constexpr Domain markets{0x5964ed8, 0x5964330, 0x7cada0, 8,
                         sizeof(PdxV3MarketObject)};
constexpr Domain military_formations{0x5966790, 0x5964b20, 0xc59d00, 8,
                                     sizeof(PdxV3MilitaryFormationObject)};
constexpr Domain wars{0x59666e0, 0x5963e60, 0xc9a7d0, 8, sizeof(PdxV3WarObject)};
constexpr Domain diplomatic_plays{0x5964ea8, 0x5964468, 0x7cdd20, 8,
                                  sizeof(PdxV3DiplomaticPlayObject)};
constexpr Domain characters{0x5964ed0, 0x5963fd0, 0x7cab90, 8,
                            sizeof(PdxV3CharacterObject)};
constexpr Domain interest_groups{0x5966750, 0x59642c8, 0xc5a990, 8,
                                 sizeof(PdxV3InterestGroupObject)};
constexpr Domain shipping_lanes{0x5966760, 0x5964950, 0xc6f920, 8,
                                 sizeof(PdxV3ShippingLaneObject)};
constexpr Domain companies{0x5966730, 0x5964b58, 0xc5adb0, 8, sizeof(PdxV3CompanyObject)};
constexpr Domain ships{0x5966780, 0x5964920, 0xcc1530, 8, sizeof(PdxV3ShipObject)};
constexpr Domain ship_template_versions{0x5966840, 0x59648f0, 0xd90be0, 8, 0};
constexpr Domain ship_templates{0x5966758, 0x5964900, 0x11d3d60, 8, 0};
const Domain* domain_config(uint32_t domain) {
    switch (domain) {
    case PDX_V3_COUNTRY: return &countries;
    case PDX_V3_STATE: return &states;
    case PDX_V3_BUILDING: return &buildings;
    case PDX_V3_POP: return &pops;
    case PDX_V3_MARKET: return &markets;
    case PDX_V3_MILITARY_FORMATION: return &military_formations;
    case PDX_V3_WAR: return &wars;
    case PDX_V3_DIPLOMATIC_PLAY: return &diplomatic_plays;
    case PDX_V3_CHARACTER: return &characters;
    case PDX_V3_INTEREST_GROUP: return &interest_groups;
    case PDX_V3_SHIPPING_LANE: return &shipping_lanes;
    case PDX_V3_COMPANY: return &companies;
    case PDX_V3_SHIP: return &ships;
    default: return nullptr;
    }
}
static_assert(sizeof(PdxV3ObjectsRequest) == 96);
static_assert(offsetof(PdxV3ObjectsRequest, database) == 72);
static_assert(sizeof(PdxV3CountryObject) == 32);
static_assert(sizeof(PdxV3StateObject) == 32);
static_assert(sizeof(PdxV3BuildingObject) == 40);
static_assert(sizeof(PdxV3PopObject) == 32);
static_assert(sizeof(PdxV3MarketObject) == 32);
static_assert(sizeof(PdxV3MilitaryFormationObject) == 40);
static_assert(sizeof(PdxV3WarObject) == 72);
static_assert(sizeof(PdxV3DiplomaticPlayObject) == 56);
static_assert(sizeof(PdxV3CharacterObject) == 32);
static_assert(sizeof(PdxV3InterestGroupObject) == 40);
static_assert(sizeof(PdxV3ShippingLaneObject) == 128);
static_assert(sizeof(PdxV3CompanyObject) == 56);
static_assert(sizeof(PdxV3ShipObject) == 88);
static_assert(sizeof(PdxV3ShipDetails) == 104);
static_assert(sizeof(PdxV3CompanyDetails) == 72);
static_assert(sizeof(PdxV3InterestGroupDetails) == 56);
static_assert(sizeof(PdxV3OwnedCString) == 32);
static_assert(sizeof(PdxV3DefinitionKeyView) == 16);
static_assert(sizeof(PdxV3DefinitionOptions) == 32);
static_assert(sizeof(PdxV3LawDefinitionObject) == 72);
static_assert(sizeof(PdxV3TechnologyDefinitionObject) == 64);
static_assert(sizeof(PdxV3PopDetails) == 96);
static_assert(sizeof(PdxV3MarketGoodsInput) == 16);
static_assert(sizeof(PdxV3MarketGoodsValue) == 56);
static_assert(sizeof(PdxV3MarketGoodsDetails) == 104);
struct MinimalGoodsMarketKey {
    uint64_t unused[2]; uintptr_t goods; uint32_t state_id, market_id;
};
static_assert(offsetof(MinimalGoodsMarketKey, goods) == 0x10);
static_assert(sizeof(MinimalGoodsMarketKey) == 32);
struct CountryIdWrapper { uint64_t unused[2]; uint32_t full_id; };
static_assert(offsetof(CountryIdWrapper, full_id) == 0x10);
struct alignas(16) NativeAccessStorage { unsigned char bytes[80] = {}; };
static_assert(sizeof(NativeAccessStorage) == 80);
static_assert(alignof(NativeAccessStorage) == 16);
struct InterfaceAccessScope {
    NativeAccessStorage& storage;
    uintptr_t base;
    InterfaceAccessScope(NativeAccessStorage& buffer, uintptr_t image_base)
        : storage(buffer), base(image_base) {
        // Captured Interface Gfx caller 0x7d6e73..0x7d6f15 uses these values.
        reinterpret_cast<GuardEnter>(base + guard_enter_rva)(
            storage.bytes, 1, 0, "pdx-runtime/native/v3_objects.cpp");
    }
    ~InterfaceAccessScope() {
        reinterpret_cast<GuardLeave>(base + guard_leave_rva)(storage.bytes);
    }
    InterfaceAccessScope(const InterfaceAccessScope&) = delete;
    InterfaceAccessScope& operator=(const InterfaceAccessScope&) = delete;
};
uintptr_t module_base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T field(uintptr_t object, uintptr_t offset) {
    return *reinterpret_cast<const T*>(object + offset);
}
uintptr_t resolve(uintptr_t base, const Domain& domain, uint32_t id) {
    return reinterpret_cast<uintptr_t>(
        reinterpret_cast<Resolver>(base + domain.resolver)(&id));
}
void reset_output(PdxV3ObjectsRequest& request) {
    request.error = ERROR_SUCCESS;
    request.returned_count = request.checked_count = request.scanned_count = 0;
    request.next_index = request.start_index;
    request.slot_bound = request.truncated = 0;
    request.database = request.fallback = request.world = 0;
}
bool fits(uint32_t offset, uint64_t count, uint32_t stride, uint32_t buffer_size) {
    return offset >= sizeof(PdxV3ObjectsRequest)
        && uint64_t(offset) + count * stride <= buffer_size;
}
bool enumerating(uint32_t operation) {
    return operation == PDX_V3_ENUMERATE_OBJECTS
        || operation == PDX_V3_ENUMERATE_POP_DETAILS;
}
bool market_goods_details(uint32_t operation) {
    return operation == PDX_V3_READ_MARKET_GOODS_DETAILS
        || operation == PDX_V3_ENUMERATE_MARKET_GOODS_DETAILS;
}
bool enumerating_market_goods(uint32_t operation) {
    return operation == PDX_V3_ENUMERATE_MARKET_GOODS
        || operation == PDX_V3_ENUMERATE_MARKET_GOODS_DETAILS;
}
bool market_goods(uint32_t operation) {
    return operation == PDX_V3_READ_MARKET_GOODS
        || operation == PDX_V3_ENUMERATE_MARKET_GOODS || market_goods_details(operation);
}
bool definition_operation(uint32_t operation) {
    return operation == PDX_V3_ENUMERATE_DEFINITIONS || operation == PDX_V3_READ_DEFINITION_INDICES;
}
bool interest_group_details(uint32_t operation) {
    return operation == PDX_V3_ENUMERATE_INTEREST_GROUP_DETAILS
        || operation == PDX_V3_READ_INTEREST_GROUP_DETAILS;
}
bool company_details(uint32_t operation) {
    return operation == PDX_V3_ENUMERATE_COMPANY_DETAILS || operation == PDX_V3_READ_COMPANY_DETAILS;
}
bool ship_details(uint32_t operation) {
    return operation == PDX_V3_ENUMERATE_SHIP_DETAILS || operation == PDX_V3_READ_SHIP_DETAILS;
}
bool text_operation(uint32_t operation) {
    return definition_operation(operation) || interest_group_details(operation) || company_details(operation) || ship_details(operation);
}
uint32_t operation_record_size(uint32_t domain, uint32_t operation) {
    if (domain == PDX_V3_LAW_DEFINITION && definition_operation(operation))
        return sizeof(PdxV3LawDefinitionObject);
    if (domain == PDX_V3_TECHNOLOGY_DEFINITION && definition_operation(operation))
        return sizeof(PdxV3TechnologyDefinitionObject);
    if (domain == PDX_V3_INTEREST_GROUP && interest_group_details(operation))
        return sizeof(PdxV3InterestGroupDetails);
    if (domain == PDX_V3_COMPANY && company_details(operation))
        return sizeof(PdxV3CompanyDetails);
    if (domain == PDX_V3_SHIP && ship_details(operation)) return sizeof(PdxV3ShipDetails);
    const Domain* config = domain_config(domain);
    if (!config) return 0;
    if (operation == PDX_V3_ENUMERATE_OBJECTS || operation == PDX_V3_READ_OBJECT_IDS)
        return config->record_size;
    if (domain == PDX_V3_POP && (operation == PDX_V3_ENUMERATE_POP_DETAILS
        || operation == PDX_V3_READ_POP_DETAILS)) return sizeof(PdxV3PopDetails);
    if (domain == PDX_V3_MARKET && market_goods(operation))
        return market_goods_details(operation) ? sizeof(PdxV3MarketGoodsDetails)
                                              : sizeof(PdxV3MarketGoodsValue);
    return 0;
}
DWORD validate(PdxV3ObjectsRequest& request, const Domain** config) {
    reset_output(request);
    *config = domain_config(request.domain);
    const uint32_t record_size = operation_record_size(request.domain, request.operation);
    if (request.size != sizeof(PdxV3ObjectsRequest) || (!*config && !definition_operation(request.operation)) || !record_size
        || request.buffer_size < sizeof(PdxV3ObjectsRequest)
        || request.record_size != record_size
        || request.records_offset % alignof(uint64_t)
        || !fits(request.records_offset, request.capacity, record_size, request.buffer_size))
        return request.error = ERROR_INVALID_PARAMETER;
    if (text_operation(request.operation)) {
        if (request.ids_offset % alignof(PdxV3DefinitionOptions)
            || ((request.operation == PDX_V3_ENUMERATE_DEFINITIONS
                || request.operation == PDX_V3_ENUMERATE_INTEREST_GROUP_DETAILS
                || request.operation == PDX_V3_ENUMERATE_COMPANY_DETAILS
                || request.operation == PDX_V3_ENUMERATE_SHIP_DETAILS) && request.input_count)
            || !fits(request.ids_offset, sizeof(PdxV3DefinitionOptions) + uint64_t(request.input_count) * 4,
                     1, request.buffer_size)) return request.error = ERROR_INVALID_PARAMETER;
        auto& options = *reinterpret_cast<PdxV3DefinitionOptions*>(
            reinterpret_cast<unsigned char*>(&request) + request.ids_offset);
        const uint64_t input_end = uint64_t(request.ids_offset) + sizeof(options) + uint64_t(request.input_count) * 4;
        const uint64_t records_end = uint64_t(request.records_offset) + uint64_t(request.capacity) * request.record_size;
        if (!fits(options.text_offset, options.text_capacity, 1, request.buffer_size)
            || request.records_offset < input_end || options.text_offset < records_end)
            return request.error = ERROR_INVALID_PARAMETER;
        options.text_bytes_written = 0;
        options.text_bytes_required = 0;
        options.required_buffer_size = options.text_offset;
        return ERROR_SUCCESS;
    }
    if (!enumerating(request.operation)) {
        const bool pairs = market_goods(request.operation) && !enumerating_market_goods(request.operation);
        const uint32_t input_size = pairs ? sizeof(PdxV3MarketGoodsInput) : sizeof(uint32_t);
        const uint32_t alignment = pairs ? 8 : 4;
        if (request.ids_offset % alignment
            || (enumerating_market_goods(request.operation) && request.input_count != 1)
            || !fits(request.ids_offset, request.input_count, input_size, request.buffer_size))
            return request.error = ERROR_INVALID_PARAMETER;
        const uint64_t ids_end = uint64_t(request.ids_offset) + uint64_t(request.input_count) * input_size;
        const uint64_t records_end = uint64_t(request.records_offset)
            + uint64_t(request.capacity) * request.record_size;
        if (request.input_count && request.capacity
            && request.ids_offset < records_end && request.records_offset < ids_end)
            return request.error = ERROR_INVALID_PARAMETER;
    }
    return ERROR_SUCCESS;
}
uintptr_t checked_resolve(uintptr_t base, const Domain& domain, uint32_t id, uint32_t* error) {
    if (!field<uintptr_t>(base, domain.root)) { *error = ERROR_NOT_READY; return 0; }
    const uintptr_t pointer = resolve(base, domain, id);
    *error = id != invalid_id && pointer && pointer != field<uintptr_t>(base, domain.fallback)
        && field<uint32_t>(pointer, domain.id_offset) == id ? ERROR_SUCCESS : ERROR_NOT_FOUND;
    return pointer;
}
DWORD population_component(uintptr_t base, uint32_t id) {
    const uintptr_t root = field<uintptr_t>(base, 0x5966648);
    if (!root) return ERROR_NOT_READY;
    const uint32_t index = id & id_index_mask;
    const uintptr_t bits = field<uintptr_t>(root, 0);
    if (index >= field<uint64_t>(root, 0x18) || !bits
        || !(field<uint32_t>(bits, uintptr_t(index >> 5) * 4) & (1u << (index & 31))))
        return ERROR_NOT_FOUND;
    const uintptr_t chunks = field<uintptr_t>(root, 0x80);
    if (!chunks || !field<uintptr_t>(chunks, uintptr_t(index >> 10) * 8)) return ERROR_INVALID_DATA;
    return ERROR_SUCCESS;
}
void pop_details(uintptr_t base, uint32_t id, uintptr_t pointer, DWORD error,
                 PdxV3PopDetails* record) {
    *record = {id, error ? 0 : id, error, id & id_index_mask, pointer,
        0, 0, 0, 0, invalid_id, invalid_id, error, error, error, 0, 0, 0};
    if (error) return;
    record->type_pointer = field<uintptr_t>(pointer, 0x10);
    record->quantities_error = population_component(base, id);
    if (!record->quantities_error) {
        const CountryIdWrapper wrapper{{0, 0}, id};
        record->total_size = reinterpret_cast<PopIntegerGetter>(base + 0x1df10e0)(&wrapper);
        record->workforce = reinterpret_cast<PopIntegerGetter>(base + 0x1df1110)(&wrapper);
        record->dependents = reinterpret_cast<PopIntegerGetter>(base + 0x1df1130)(&wrapper);
    }
    record->state_id = field<uint32_t>(pointer, 0x40);
    record->state_pointer = checked_resolve(base, states, record->state_id, &record->state_error);
    record->country_error = record->state_error;
    if (!record->state_error) {
        record->country_id = field<uint32_t>(record->state_pointer, 0xe48);
        record->country_pointer = checked_resolve(base, countries, record->country_id,
                                                 &record->country_error);
    }
}
uintptr_t goods_database(uintptr_t base) {
    const auto slot = reinterpret_cast<GoodsProvider>(base + 0x7b7290)();
    return slot ? field<uintptr_t>(reinterpret_cast<uintptr_t>(slot), 0) : 0;
}
uintptr_t goods_at(uintptr_t base, uintptr_t database, int32_t index) {
    return reinterpret_cast<uintptr_t>(reinterpret_cast<GoodsIndexGetter>(base + 0xc9bfb0)(
        reinterpret_cast<void*>(database), index));
}
DWORD read_goods_index(uintptr_t pointer, int32_t* index) {
    SIZE_T copied = 0;
    if (!pointer) return ERROR_NOT_FOUND;
    const BOOL ok = ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(pointer + 0x10),
        index, sizeof(*index), &copied);
    return ok && copied == sizeof(*index) ? ERROR_SUCCESS : (ok ? ERROR_PARTIAL_COPY : GetLastError());
}
void market_value(uintptr_t base, uintptr_t database, uint32_t market_id,
                  uintptr_t goods, uint32_t definition_index, PdxV3MarketGoodsValue* record) {
    *record = {market_id, 0, 0, 0, 0, goods, 0, -1, definition_index, 0, 0};
    record->market_pointer = checked_resolve(base, markets, market_id, &record->market_error);
    if (!record->market_error) record->resolved_market_id = market_id;
    record->goods_error = read_goods_index(goods, &record->goods_index);
    if (!record->goods_error) {
        const int32_t count = field<int32_t>(database, 0x8c);
        if (record->goods_index < 0 || record->goods_index >= count
            || (definition_index != invalid_id && uint32_t(record->goods_index) != definition_index)
            || goods == field<uintptr_t>(base, 0x5963e30)
            || goods_at(base, database, record->goods_index) != goods)
            record->goods_error = ERROR_NOT_FOUND;
    }
    record->price_error = record->market_error ? record->market_error : record->goods_error;
    if (!record->price_error) {
        const MinimalGoodsMarketKey key{{0, 0}, goods, invalid_id, market_id};
        reinterpret_cast<MarketPriceGetter>(base + 0x195c610)(&key, &record->price_raw64);
    }
}
void market_details(uintptr_t base, uintptr_t database, uint32_t market_id,
                    uintptr_t goods, uint32_t definition_index, PdxV3MarketGoodsDetails* record) {
    *record = {};
    market_value(base, database, market_id, goods, definition_index, &record->price);
    record->buy_orders_error = record->sell_orders_error = record->imports_error
        = record->exports_error = record->price.price_error;
    if (record->price.price_error) return;
    // Native 195ff00 ordinary CGoods branch, after exact GoodsDB identity check.
    reinterpret_cast<GoodsValueGetter>(base + 0x103f440)(
        reinterpret_cast<const void*>(record->price.market_pointer + 0x1d8),
        &record->buy_orders_raw64, reinterpret_cast<const void*>(goods));
    reinterpret_cast<GoodsValueGetter>(base + 0x103f440)(
        reinterpret_cast<const void*>(record->price.market_pointer + 0x228),
        &record->sell_orders_raw64, reinterpret_cast<const void*>(goods));
    const MinimalGoodsMarketKey key{{0, 0}, goods, invalid_id, market_id};
    reinterpret_cast<MarketPriceGetter>(base + 0x196e640)(&key, &record->imports_raw64);
    reinterpret_cast<MarketPriceGetter>(base + 0x196e6a0)(&key, &record->exports_raw64);
}
DWORD read_market_goods(uintptr_t base, PdxV3ObjectsRequest& request) {
    const uintptr_t database = goods_database(base);
    if (!database) return request.error = ERROR_NOT_READY;
    const int32_t count = field<int32_t>(database, 0x8c);
    if (count < 0) return request.error = ERROR_INVALID_DATA;
    request.database = database;
    request.fallback = field<uintptr_t>(base, 0x5963e30);
    request.slot_bound = static_cast<uint32_t>(count);
    auto* bytes = reinterpret_cast<unsigned char*>(&request);
    const uint32_t end = enumerating_market_goods(request.operation)
        ? request.slot_bound : request.input_count;
    const uint32_t limit = (std::min)(request.max_items, request.capacity);
    uint32_t index = (std::min)(request.start_index, end);
    while (index < end && request.returned_count < limit) {
        const uint32_t current = index++;
        uint32_t market_id;
        uintptr_t goods;
        uint32_t definition_index = invalid_id;
        if (enumerating_market_goods(request.operation)) {
            market_id = *reinterpret_cast<const uint32_t*>(bytes + request.ids_offset);
            definition_index = current;
            goods = goods_at(base, database, static_cast<int32_t>(current));
        } else {
            const auto& input = reinterpret_cast<const PdxV3MarketGoodsInput*>(bytes + request.ids_offset)[current];
            market_id = input.market_id;
            goods = input.goods_pointer;
        }
        auto* output = bytes + request.records_offset
            + size_t(request.returned_count) * request.record_size;
        if (market_goods_details(request.operation))
            market_details(base, database, market_id, goods, definition_index,
                           reinterpret_cast<PdxV3MarketGoodsDetails*>(output));
        else market_value(base, database, market_id, goods, definition_index,
                          reinterpret_cast<PdxV3MarketGoodsValue*>(output));
        ++request.checked_count;
        ++request.scanned_count;
        ++request.returned_count;
    }
    request.next_index = index;
    request.truncated = index < end;
    return request.error;
}
uintptr_t law_database(uintptr_t base, bool group) {
    const auto slot = reinterpret_cast<GoodsProvider>(base + (group ? 0x748650 : 0x7487d0))();
    return slot ? field<uintptr_t>(reinterpret_cast<uintptr_t>(slot), 0) : 0;
}
PdxV3DefinitionKeyView definition_key(uintptr_t base, uintptr_t database, int32_t index, bool group) {
    PdxV3DefinitionKeyView view{};
    reinterpret_cast<DefinitionKeyGetter>(base + (group ? 0x67d7f0 : 0x169a4e0))(
        reinterpret_cast<void*>(database), &view, index);
    return view;
}
DWORD key_status(const PdxV3DefinitionKeyView& view) {
    return view.size < 0 || (view.size && !view.data) ? ERROR_INVALID_DATA : ERROR_SUCCESS;
}
DWORD read_law_definitions(uintptr_t base, PdxV3ObjectsRequest& request) {
    const uintptr_t database = law_database(base, false);
    if (!database) return request.error = ERROR_NOT_READY;
    // Exact specific producer6fbe80 / CLawType consumer1695c60 use7487d0.
    // A different DB may share these fields/getters but is not a LawType receiver.
    if (!PdxV3LawDefinitionDatabaseType(field<uintptr_t>(database, 0), base, false))
        return request.error = ERROR_INVALID_STATE;
    const int32_t count = field<int32_t>(database, 0x8c);
    if (count < 0) return request.error = ERROR_INVALID_DATA;
    request.database = database;
    request.fallback = field<uintptr_t>(base, 0x5964110);
    request.slot_bound = uint32_t(count); // Definition count, not a RefDB/live-object bound.
    const uintptr_t group_database = law_database(base, true);
    const DWORD group_database_error = !group_database ? ERROR_NOT_READY
        : PdxV3LawDefinitionDatabaseType(field<uintptr_t>(group_database, 0), base, true)
            ? ERROR_SUCCESS : ERROR_INVALID_STATE;
    const int32_t group_count = !group_database_error ? field<int32_t>(group_database, 0x8c) : 0;
    const uintptr_t group_array = !group_database_error ? field<uintptr_t>(group_database, 0x80) : 0;
    auto* bytes = reinterpret_cast<unsigned char*>(&request);
    auto& options = *reinterpret_cast<PdxV3DefinitionOptions*>(bytes + request.ids_offset);
    const auto* inputs = reinterpret_cast<const int32_t*>(bytes + request.ids_offset + sizeof(options));
    const uint32_t end = request.operation == PDX_V3_ENUMERATE_DEFINITIONS ? request.slot_bound : request.input_count;
    const uint32_t limit = (std::min)(request.max_items, request.capacity);
    uint32_t index = (std::min)(request.start_index, end);
    while (index < end && request.returned_count < limit) {
        const uint32_t current = index;
        const int32_t requested = request.operation == PDX_V3_ENUMERATE_DEFINITIONS ? int32_t(current) : inputs[current];
        const uintptr_t pointer = reinterpret_cast<uintptr_t>(reinterpret_cast<GoodsIndexGetter>(base + 0x169afb0)(
            reinterpret_cast<void*>(database), requested));
        const DWORD error = requested < 0 || requested >= count || !pointer || pointer == request.fallback
            ? ERROR_NOT_FOUND : ERROR_SUCCESS;
        PdxV3LawDefinitionObject record{requested, error ? -1 : requested, error, error,
            pointer, -1, error, 0, 0, 0, 0, 0, 0, 0, error, 0};
        PdxV3DefinitionKeyView key{}, group_key{};
        if (!error) {
            key = definition_key(base, database, requested, false);
            record.key_error = key_status(key);
            if (!record.key_error) record.key_size = uint32_t(key.size);
            // Actual native EA8490/EA7480: LawType+40 is its LawGroup definition.
            record.group_pointer = field<uintptr_t>(pointer, 0x40);
            record.group_error = group_database_error ? group_database_error
                : group_count < 0 || (group_count && !group_array) ? ERROR_INVALID_DATA : ERROR_NOT_FOUND;
            if (!group_database_error && group_count >= 0 && group_array && record.group_pointer
                && record.group_pointer != field<uintptr_t>(base, 0x5964368)) {
                // The Group getter independently proves this definition array/count.
                // Match the actual pointer; do not invent Group fullID/generation layout.
                for (int32_t g = 0; g < group_count; ++g) {
                    if (field<uintptr_t>(group_array, uintptr_t(g) * 8) == record.group_pointer) {
                        record.group_index = g;
                        record.group_error = ERROR_SUCCESS;
                        break;
                    }
                }
            }
            record.group_key_error = record.group_error;
            if (!record.group_error) {
                group_key = definition_key(base, group_database, record.group_index, true);
                record.group_key_error = key_status(group_key);
                if (!record.group_key_error) record.group_key_size = uint32_t(group_key.size);
            }
        }
        ++request.checked_count;
        ++request.scanned_count;
        const uint64_t needed = uint64_t(record.key_size) + record.group_key_size;
        options.text_bytes_required = uint64_t(options.text_bytes_written) + needed;
        options.required_buffer_size = uint64_t(options.text_offset) + options.text_bytes_written + needed;
        if (!PdxV3DefinitionTextFits(options.text_bytes_written, options.text_capacity, needed)) {
            request.error = ERROR_MORE_DATA;
            break; // No partial key/record, and next_index still points at this input.
        }
        if (!record.key_error) {
            record.key_offset = options.text_offset + options.text_bytes_written;
            uint32_t required = 0;
            PdxV3CopyDefinitionKey(&key, bytes + record.key_offset, record.key_size, &required);
            record.key_bytes = record.key_size;
            options.text_bytes_written += record.key_bytes;
        }
        if (!record.group_key_error) {
            record.group_key_offset = options.text_offset + options.text_bytes_written;
            uint32_t required = 0;
            PdxV3CopyDefinitionKey(&group_key, bytes + record.group_key_offset, record.group_key_size, &required);
            record.group_key_bytes = record.group_key_size;
            options.text_bytes_written += record.group_key_bytes;
        }
        *reinterpret_cast<PdxV3LawDefinitionObject*>(bytes + request.records_offset
            + size_t(request.returned_count) * request.record_size) = record;
        ++request.returned_count;
        ++index;
    }
    request.next_index = index;
    request.truncated = index < end;
    return request.error;
}
uintptr_t technology_database(uintptr_t base) {
    const auto slot = reinterpret_cast<GoodsProvider>(base + 0x74d010)();
    return slot ? field<uintptr_t>(reinterpret_cast<uintptr_t>(slot), 0) : 0;
}
void technology_category(uintptr_t base, uintptr_t database, PdxV3TechnologyDefinitionObject& record) {
    record.category_index = field<int32_t>(record.pointer, 0x48);
    if (record.category_index == -1) {
        // Native default/unset result is distinct from an actual category entry.
        record.category_is_default = 1;
        record.category_pointer = reinterpret_cast<uintptr_t>(
            reinterpret_cast<TechnologyCategoryGetter>(base + 0x1716cf0)(reinterpret_cast<const void*>(record.pointer)));
        record.category_error = record.category_pointer
            && record.category_pointer == field<uintptr_t>(base, 0x5964430)
                ? ERROR_NOT_FOUND : ERROR_NOT_READY;
        record.category_token_error = record.category_error; // Do not invent a real token from fallback.
        return;
    }
    const int32_t count = field<int32_t>(database, 0xfc4);
    const uintptr_t entries = field<uintptr_t>(database, 0xfb8);
    record.category_error = count < 0 || (count && !entries) ? ERROR_INVALID_DATA : ERROR_NOT_FOUND;
    record.category_token_error = record.category_error;
    if (!PdxV3TechnologyCategoryIndexInRange(record.category_index, count) || !entries) return;
    // The original getter has no positive upper-bound check. It is now safe to call.
    record.category_pointer = reinterpret_cast<uintptr_t>(
        reinterpret_cast<TechnologyCategoryGetter>(base + 0x1716cf0)(reinterpret_cast<const void*>(record.pointer)));
    if (record.category_pointer != entries + uintptr_t(record.category_index) * 16
        || field<uintptr_t>(record.category_pointer, 0) != base + 0x4514a38) {
        record.category_error = record.category_token_error = ERROR_INVALID_STATE;
        return;
    }
    record.category_token = field<uint32_t>(record.category_pointer, 8);
    record.category_error = record.category_token_error = ERROR_SUCCESS;
}
DWORD read_technology_definitions(uintptr_t base, PdxV3ObjectsRequest& request) {
    const uintptr_t database = technology_database(base);
    if (!database) return request.error = ERROR_NOT_READY;
    if (!PdxV3TechnologyDefinitionDatabaseType(field<uintptr_t>(database, 0), base))
        return request.error = ERROR_INVALID_STATE;
    const int32_t count = field<int32_t>(database, 0x8c);
    if (count < 0) return request.error = ERROR_INVALID_DATA;
    request.database = database;
    request.fallback = field<uintptr_t>(base, 0x5964140);
    request.slot_bound = uint32_t(count); // Actual populated definition Count, never capacity+88.
    auto* bytes = reinterpret_cast<unsigned char*>(&request);
    auto& options = *reinterpret_cast<PdxV3DefinitionOptions*>(bytes + request.ids_offset);
    const auto* inputs = reinterpret_cast<const int32_t*>(bytes + request.ids_offset + sizeof(options));
    const uint32_t end = request.operation == PDX_V3_ENUMERATE_DEFINITIONS ? request.slot_bound : request.input_count;
    const uint32_t limit = (std::min)(request.max_items, request.capacity);
    uint32_t index = (std::min)(request.start_index, end);
    while (index < end && request.returned_count < limit) {
        const int32_t requested = request.operation == PDX_V3_ENUMERATE_DEFINITIONS ? int32_t(index) : inputs[index];
        const uintptr_t pointer = reinterpret_cast<uintptr_t>(reinterpret_cast<GoodsIndexGetter>(base + 0x70fe60)(
            reinterpret_cast<void*>(database), requested));
        const DWORD error = requested < 0 || requested >= count || !pointer || pointer == request.fallback
            ? ERROR_NOT_FOUND : field<uintptr_t>(pointer, 0) != base + 0x45149e0 ? ERROR_INVALID_STATE : ERROR_SUCCESS;
        PdxV3TechnologyDefinitionObject record{requested, error ? -1 : requested, error, error,
            pointer, -1, error, 0, 0, error, 0, 0, 0, 0};
        PdxV3DefinitionKeyView key{};
        if (!error) {
            reinterpret_cast<DefinitionKeyGetter>(base + 0x691aa0)(reinterpret_cast<void*>(database), &key, requested);
            record.key_error = key_status(key);
            if (!record.key_error) record.key_size = uint32_t(key.size);
            technology_category(base, database, record);
        }
        ++request.checked_count;
        ++request.scanned_count;
        options.text_bytes_required = uint64_t(options.text_bytes_written) + record.key_size;
        options.required_buffer_size = uint64_t(options.text_offset) + options.text_bytes_required;
        if (!PdxV3DefinitionTextFits(options.text_bytes_written, options.text_capacity, record.key_size)) {
            request.error = ERROR_MORE_DATA;
            break; // Whole record/text prefix, no skipped ordinal and no partial UTF-8.
        }
        if (!record.key_error) {
            record.key_offset = options.text_offset + options.text_bytes_written;
            uint32_t required = 0;
            PdxV3CopyDefinitionKey(&key, bytes + record.key_offset, record.key_size, &required);
            record.key_bytes = record.key_size;
            options.text_bytes_written += record.key_bytes;
        }
        *reinterpret_cast<PdxV3TechnologyDefinitionObject*>(bytes + request.records_offset
            + size_t(request.returned_count) * request.record_size) = record;
        ++request.returned_count;
        ++index;
    }
    request.next_index = index;
    request.truncated = index < end;
    return request.error;
}
bool state_uses_market_area(uintptr_t state) {
    return PdxV3StateUsesMarketArea(field<uint8_t>(state, 0x11b8), field<uint32_t>(state, 0xb48));
}
void state_market(uintptr_t base, uintptr_t state, uint32_t* id, uint32_t* error, uint64_t* pointer) {
    *id = invalid_id; *pointer = 0;
    // Exact original e3d8f0 effective-Market branch, not always ownerCountry Market.
    if (state_uses_market_area(state)) {
        constexpr Domain market_areas{0x5964ea0, 0x59649b0, 0x7cddd0, 8, 0};
        const uintptr_t area = checked_resolve(base, market_areas, field<uint32_t>(state, 0xb48), error);
        if (*error) return;
        if (field<uintptr_t>(area, 0) != base + 0x4430cd8) { *error = ERROR_INVALID_DATA; return; }
        *id = field<uint32_t>(area, 0x10);
    } else {
        const uintptr_t owner = checked_resolve(base, countries, field<uint32_t>(state, 0xe48), error);
        if (*error) return;
        *id = field<uint32_t>(owner, 0xa24);
    }
    *pointer = checked_resolve(base, markets, *id, error);
}
void shipping_lane(uintptr_t base, uint32_t id, uint32_t resolved_id, DWORD error,
                   uintptr_t pointer, PdxV3ShippingLaneObject& record) {
    record = {id, resolved_id, error, id & id_index_mask, pointer,
        invalid_id, error, 0, invalid_id, error, 0, invalid_id, error, 0,
        invalid_id, error, 0, invalid_id, error, 0, 0, 0, error, error};
    if (error) return;
    // Exact same dynamic ShippingLane type5968498 registered Country/Begin/End:
    // 1cd1eb0 -> +18Country; 1cd1f20 -> +48State; 1cd1f90 -> +4cState.
    record.country_id = field<uint32_t>(pointer, 0x18);
    record.country_pointer = checked_resolve(base, countries, record.country_id, &record.country_error);
    record.begin_state_id = field<uint32_t>(pointer, 0x48);
    record.begin_state_pointer = checked_resolve(base, states, record.begin_state_id, &record.begin_state_error);
    record.end_state_id = field<uint32_t>(pointer, 0x4c);
    record.end_state_pointer = checked_resolve(base, states, record.end_state_id, &record.end_state_error);
    record.begin_market_error = record.begin_state_error;
    record.end_market_error = record.end_state_error;
    if (!record.begin_state_error) state_market(base, record.begin_state_pointer,
        &record.begin_market_id, &record.begin_market_error, &record.begin_market_pointer);
    if (!record.end_state_error) state_market(base, record.end_state_pointer,
        &record.end_market_id, &record.end_market_error, &record.end_market_pointer);
    // Specific GetConvoyCostRaw consumes +58 then signed/100000 -> int32.
    // Preserve its native original bits, not the already truncated display int.
    record.convoy_cost_raw64 = PdxV3ShippingLaneConvoyCostRaw64(pointer);
    record.convoy_cost_error = ERROR_SUCCESS;
    // Specific EffectivenessRaw is SAnyType<CFixedPoint>, native out8 result.
    // Validate actual Country and State dependencies before its original getter.
    record.effectiveness_error = record.country_error ? record.country_error
        : record.begin_state_error ? record.begin_state_error : record.end_state_error;
    if (!record.effectiveness_error) {
        try {
            PdxV3ShippingLaneEffectiveness(pointer, &record.effectiveness_raw64);
        } catch (...) { record.effectiveness_error = ERROR_UNHANDLED_EXCEPTION; }
    }
}
void company_object(uintptr_t base, uint32_t id, uint32_t resolved_id, DWORD error,
                    uintptr_t pointer, PdxV3CompanyObject& record) {
    record = {id, resolved_id, error, id & id_index_mask, pointer,
        invalid_id, error, 0, 0, error, 0};
    if (error) return;
    // Exact registeredCompany.GetCountry1b59a10 consumes +60; d5de00 stores
    // that trueCountry.fullID argument. Country failure does not invalidateCompany.
    record.country_id = field<uint32_t>(pointer, 0x60);
    record.country_pointer = checked_resolve(base, countries, record.country_id, &record.country_error);
    // Exact Company.GetCompanyType1b5f760/1b5f820 consume +a0 through1944fd0.
    // Preserve only the real staticdefinitionorigin, never invent an ordinal/key.
    record.company_type_pointer = PdxV3CompanyType(pointer);
    record.company_type_error = !record.company_type_pointer
        || record.company_type_pointer == field<uintptr_t>(base, 0x5964b68) ? ERROR_NOT_FOUND
        : !PdxV3CompanyDefinitionType(base, field<uintptr_t>(record.company_type_pointer, 0))
            ? ERROR_INVALID_DATA : ERROR_SUCCESS;
}
void ship_object(uintptr_t base, uint32_t id, uint32_t resolved_id, DWORD error,
                 uintptr_t pointer, PdxV3ShipObject& record) {
    record = {id, resolved_id, error, id & id_index_mask, pointer,
        invalid_id, error, 0, invalid_id, error, 0, invalid_id, error, 0, invalid_id, error, 0};
    if (error) return;
    // Exact Ship.GetCountry and GetFleet read these complete native refs. The
    // latter is assignedFormation, never TargetFleet's conditional+79c redirect.
    record.country_id = field<uint32_t>(pointer, 0x14);
    record.country_pointer = checked_resolve(base, countries, record.country_id, &record.country_error);
    record.fleet_id = field<uint32_t>(pointer, 0x4c);
    record.fleet_pointer = checked_resolve(base, military_formations, record.fleet_id, &record.fleet_error);
    // GetTemplate crosses two actual RefDBs: Ship+48Version, Version+10Template.
    record.template_version_id = field<uint32_t>(pointer, 0x48);
    const uintptr_t version_db = field<uintptr_t>(base, ship_template_versions.root);
    if (!version_db) record.template_version_error = ERROR_NOT_READY;
    else if (!PdxV3ShipTemplateTypesMatch(base, field<uintptr_t>(version_db, 0), 0, true))
        record.template_version_error = ERROR_INVALID_DATA;
    else {
        record.template_version_pointer = checked_resolve(base, ship_template_versions,
            record.template_version_id, &record.template_version_error);
        if (!record.template_version_error && !PdxV3ShipTemplateTypesMatch(base,
            field<uintptr_t>(version_db, 0), field<uintptr_t>(record.template_version_pointer, 0), true))
            record.template_version_error = ERROR_INVALID_DATA;
    }
    record.template_error = record.template_version_error;
    if (!record.template_version_error) {
        record.template_id = field<uint32_t>(record.template_version_pointer, 0x10);
        const uintptr_t template_db = field<uintptr_t>(base, ship_templates.root);
        if (!template_db) record.template_error = ERROR_NOT_READY;
        else if (!PdxV3ShipTemplateTypesMatch(base, field<uintptr_t>(template_db, 0), 0, false))
            record.template_error = ERROR_INVALID_DATA;
        else {
            record.template_pointer = checked_resolve(base, ship_templates,
                record.template_id, &record.template_error);
            if (!record.template_error && !PdxV3ShipTemplateTypesMatch(base,
                field<uintptr_t>(template_db, 0), field<uintptr_t>(record.template_pointer, 0), false))
                record.template_error = ERROR_INVALID_DATA;
        }
    }
}
DWORD write_record(uintptr_t base, const Domain& domain, uint32_t domain_id,
                   uint32_t id, uintptr_t fallback, uint32_t operation, unsigned char* output) {
    const uintptr_t pointer = resolve(base, domain, id);
    const uint32_t slot = id & id_index_mask;
    const bool valid = id != invalid_id && pointer && pointer != fallback
        && field<uint32_t>(pointer, domain.id_offset) == id;
    const bool typed = !valid || (domain_id != PDX_V3_INTEREST_GROUP
        && domain_id != PDX_V3_SHIPPING_LANE && domain_id != PDX_V3_COMPANY && domain_id != PDX_V3_SHIP)
        || (domain_id == PDX_V3_INTEREST_GROUP && field<uintptr_t>(pointer, 0) == base + 0x44a4290)
        || (domain_id == PDX_V3_SHIPPING_LANE && field<uintptr_t>(pointer, 0) == base + 0x4427d98)
        || (domain_id == PDX_V3_COMPANY && field<uintptr_t>(pointer, 0) == base + 0x449eac0)
        || (domain_id == PDX_V3_SHIP && field<uintptr_t>(pointer, 0) == base + 0x44e1a10);
    const DWORD error = !typed ? ERROR_INVALID_DATA : valid ? ERROR_SUCCESS : ERROR_NOT_FOUND;
    const uint32_t resolved_id = !error ? id : 0;
    if (operation == PDX_V3_ENUMERATE_POP_DETAILS || operation == PDX_V3_READ_POP_DETAILS) {
        pop_details(base, id, pointer, error, reinterpret_cast<PdxV3PopDetails*>(output));
        return error;
    }
    switch (domain_id) {
    case PDX_V3_COUNTRY: {
        auto* record = reinterpret_cast<PdxV3CountryObject*>(output);
        *record = {id, resolved_id, error, slot, pointer, 0};
        if (valid) {
            const CountryIdWrapper wrapper{{0, 0}, id};
            // Real getter resolves wrapper+0x10 and copies Country+0x88.
            reinterpret_cast<GdpGetter>(base + country_gdp_getter_rva)(
                &wrapper, &record->gdp_raw64);
        }
        break;
    }
    case PDX_V3_STATE:
        *reinterpret_cast<PdxV3StateObject*>(output) = {id, resolved_id, error,
            slot, pointer, valid ? field<uint32_t>(pointer, 0xe48) : 0, 0};
        break;
    case PDX_V3_BUILDING:
        *reinterpret_cast<PdxV3BuildingObject*>(output) = {id, resolved_id, error,
            slot, pointer, valid ? field<uint32_t>(pointer, 0xe4) : 0,
            valid ? field<int32_t>(pointer, 0xe8) : 0,
            valid ? field<uintptr_t>(pointer, 0xf0) : 0};
        break;
    case PDX_V3_POP:
        // 12eeb80 stores the native Type argument here; population is component-backed.
        *reinterpret_cast<PdxV3PopObject*>(output) = {id, resolved_id, error,
            slot, pointer, valid ? field<uintptr_t>(pointer, 0x10) : 0};
        break;
    case PDX_V3_MARKET:
        // e4a4c0 resolves this exact field with the real Country resolver.
        *reinterpret_cast<PdxV3MarketObject*>(output) = {id, resolved_id, error,
            slot, pointer, valid ? field<uint32_t>(pointer, 0x848) : 0, 0};
        break;
    case PDX_V3_MILITARY_FORMATION: {
        auto* record = reinterpret_cast<PdxV3MilitaryFormationObject*>(output);
        *record = {id, resolved_id, error, slot, pointer, invalid_id, error, 0};
        if (valid) {
            // GetCountry 1add230: real formation resolver then raw owner fullID+fc.
            record->owner_country_id = field<uint32_t>(pointer, 0xfc);
            record->owner_country_pointer = checked_resolve(base, countries,
                record->owner_country_id, &record->owner_country_error);
        }
        break;
    }
    case PDX_V3_WAR: {
        auto* record = reinterpret_cast<PdxV3WarObject*>(output);
        *record = {id, resolved_id, error, slot, pointer, invalid_id, error, 0,
            invalid_id, invalid_id, error, error, 0, 0};
        if (valid) {
            record->diplomatic_play_id = field<uint32_t>(pointer, 0x18);
            record->diplomatic_play_pointer = checked_resolve(base, diplomatic_plays,
                record->diplomatic_play_id, &record->diplomatic_play_error);
            record->attacker_leader_error = record->defender_leader_error
                = record->diplomatic_play_error;
            if (!record->diplomatic_play_error) {
                record->attacker_leader_id = field<uint32_t>(record->diplomatic_play_pointer, 0x138);
                record->defender_leader_id = field<uint32_t>(record->diplomatic_play_pointer, 0x13c);
                record->attacker_leader_pointer = checked_resolve(base, countries,
                    record->attacker_leader_id, &record->attacker_leader_error);
                record->defender_leader_pointer = checked_resolve(base, countries,
                    record->defender_leader_id, &record->defender_leader_error);
            }
        }
        break;
    }
    case PDX_V3_DIPLOMATIC_PLAY: {
        auto* record = reinterpret_cast<PdxV3DiplomaticPlayObject*>(output);
        *record = {id, resolved_id, error, slot, pointer,
            invalid_id, invalid_id, error, error, 0, 0};
        if (valid) {
            record->attacker_leader_id = field<uint32_t>(pointer, 0x138);
            record->defender_leader_id = field<uint32_t>(pointer, 0x13c);
            record->attacker_leader_pointer = checked_resolve(base, countries,
                record->attacker_leader_id, &record->attacker_leader_error);
            record->defender_leader_pointer = checked_resolve(base, countries,
                record->defender_leader_id, &record->defender_leader_error);
        }
        break;
    }
    case PDX_V3_SHIP:
        ship_object(base, id, resolved_id, error, pointer,
            *reinterpret_cast<PdxV3ShipObject*>(output));
        break;
    case PDX_V3_COMPANY:
        company_object(base, id, resolved_id, error, pointer,
            *reinterpret_cast<PdxV3CompanyObject*>(output));
        break;
    case PDX_V3_SHIPPING_LANE:
        shipping_lane(base, id, resolved_id, error, pointer,
            *reinterpret_cast<PdxV3ShippingLaneObject*>(output));
        break;
    case PDX_V3_INTEREST_GROUP: {
        auto* record = reinterpret_cast<PdxV3InterestGroupObject*>(output);
        *record = {id, resolved_id, error, slot, pointer, invalid_id, error, 0};
        if (!error) {
            // Exact native Data GetCountry1b791a0 and independent7bb480 users
            // 1b7e0a0/1b7f740 consume this IG full Country relation.
            record->country_id = field<uint32_t>(pointer, 0xa4);
            record->country_pointer = checked_resolve(base, countries,
                record->country_id, &record->country_error);
        }
        break;
    }
    case PDX_V3_CHARACTER: {
        auto* record = reinterpret_cast<PdxV3CharacterObject*>(output);
        *record = {id, resolved_id, error, slot, pointer, 0, error};
        if (valid) {
            // IsFemale10f540 -> 1a76470 -> 1947620 -> real leaf1a6a6f0.
            // Only the complete ID at wrapper+10 is consumed by this exact leaf.
            const CountryIdWrapper wrapper{{0, 0}, id};
            record->is_female_error = ERROR_IO_PENDING;
            try {
                record->is_female = PdxV3CharacterIsFemale(&wrapper);
                record->is_female_error = ERROR_SUCCESS;
            } catch (...) {
                record->is_female_error = ERROR_UNHANDLED_EXCEPTION;
            }
        }
        break;
    }
    }
    return error;
}
struct ScopedNativeCString {
    uintptr_t base;
    PdxV3OwnedCString value{}; // Raw return storage: original getter constructs it.
    bool constructed = false;
    explicit ScopedNativeCString(uintptr_t image_base) : base(image_base) {}
    ~ScopedNativeCString() {
        if (constructed) reinterpret_cast<NativeCStringDestructor>(base + 0x61f810)(&value);
    }
    ScopedNativeCString(const ScopedNativeCString&) = delete;
    ScopedNativeCString& operator=(const ScopedNativeCString&) = delete;
};
DWORD read_interest_group_details(uintptr_t base, PdxV3ObjectsRequest& request) {
    auto* bytes = reinterpret_cast<unsigned char*>(&request);
    auto& options = *reinterpret_cast<PdxV3InterestGroupOptions*>(bytes + request.ids_offset);
    const uintptr_t slots = field<uintptr_t>(request.database, 0x20);
    request.slot_bound = field<uint32_t>(request.database, 0x2c);
    request.fallback = field<uintptr_t>(base, interest_groups.fallback);
    if (request.slot_bound && !slots) return request.error = ERROR_INVALID_DATA;
    const bool enumeration = request.operation == PDX_V3_ENUMERATE_INTEREST_GROUP_DETAILS;
    const uint32_t end = enumeration ? request.slot_bound : request.input_count;
    const uint32_t limit = (std::min)(request.max_items, request.capacity);
    uint32_t index = (std::min)(request.start_index, end);
    while (index < end && request.returned_count < limit) {
        const uint32_t current = index++;
        ++request.scanned_count;
        uint32_t id;
        if (enumeration) {
            const uintptr_t candidate = field<uintptr_t>(slots, uintptr_t(current) * 16 + 8);
            if (!candidate || candidate == request.fallback) continue;
            if (field<uintptr_t>(candidate, 0) != base + 0x44a4290)
                return request.error = ERROR_INVALID_DATA;
            id = field<uint32_t>(candidate, 8);
            if (id == invalid_id || (id & id_index_mask) != current) continue;
        } else {
            id = reinterpret_cast<const uint32_t*>(bytes + request.ids_offset + sizeof(options))[current];
        }
        ++request.checked_count;
        PdxV3InterestGroupDetails record{};
        const DWORD object_error = write_record(base, interest_groups, PDX_V3_INTEREST_GROUP,
            id, request.fallback, PDX_V3_READ_OBJECT_IDS, reinterpret_cast<unsigned char*>(&record.object));
        if (enumeration && object_error) {
            if (object_error == ERROR_INVALID_DATA) return request.error = object_error;
            continue;
        }
        record.name_error = object_error;
        if (!object_error) {
            // Original registered Data Name reaches1b7a370(outCString32,rawIG).
            // No fabricated Any/Data wrapper. The original CString dtor runs in
            // this same scope after every constructed copy/truncation/invalid view;
            // failed construction is never passed to a blind destructor.
            ScopedNativeCString name(base);
            try {
                reinterpret_cast<InterestGroupNameGetter>(base + 0x1b7a370)(
                    &name.value, reinterpret_cast<const void*>(record.object.pointer));
                name.constructed = true; // Never destroy an unconstructed failed return.
                PdxV3OwnedCStringBytes view{}; // CString length is64-bit, not CPdxStringView32.
                record.name_error = PdxV3OwnedCStringView(&name.value, &view);
                if (!record.name_error && view.size > UINT64_MAX - options.text_offset - options.text_bytes_written)
                    record.name_error = ERROR_ARITHMETIC_OVERFLOW;
                if (!record.name_error) {
                    options.text_bytes_required = uint64_t(options.text_bytes_written) + view.size;
                    options.required_buffer_size = uint64_t(options.text_offset) + options.text_bytes_required;
                    if (view.size > uint64_t(options.text_capacity) - options.text_bytes_written) {
                        request.next_index = current;
                        request.truncated = 1;
                        return request.error = ERROR_MORE_DATA;
                    }
                    record.name_offset = options.text_offset + options.text_bytes_written;
                    record.name_bytes = uint32_t(view.size);
                    if (view.size) std::memcpy(bytes + record.name_offset,
                        reinterpret_cast<const void*>(view.data), size_t(view.size));
                    options.text_bytes_written += record.name_bytes;
                }
            } catch (...) {
                record.name_error = ERROR_UNHANDLED_EXCEPTION;
            }
        }
        *reinterpret_cast<PdxV3InterestGroupDetails*>(bytes + request.records_offset
            + size_t(request.returned_count) * request.record_size) = record;
        ++request.returned_count;
    }
    request.next_index = index;
    request.truncated = index < end;
    return request.error;
}
DWORD read_company_details(uintptr_t base, PdxV3ObjectsRequest& request) {
    auto* bytes = reinterpret_cast<unsigned char*>(&request);
    auto& options = *reinterpret_cast<PdxV3CompanyOptions*>(bytes + request.ids_offset);
    const uintptr_t slots = field<uintptr_t>(request.database, 0x20);
    request.slot_bound = field<uint32_t>(request.database, 0x2c);
    request.fallback = field<uintptr_t>(base, companies.fallback);
    if (request.slot_bound && !slots) return request.error = ERROR_INVALID_DATA;
    const bool enumeration = request.operation == PDX_V3_ENUMERATE_COMPANY_DETAILS;
    const uint32_t end = enumeration ? request.slot_bound : request.input_count;
    const uint32_t limit = (std::min)(request.max_items, request.capacity);
    uint32_t index = (std::min)(request.start_index, end);
    while (index < end && request.returned_count < limit) {
        const uint32_t current = index++;
        ++request.scanned_count;
        uint32_t id;
        if (enumeration) {
            const uintptr_t candidate = field<uintptr_t>(slots, uintptr_t(current) * 16 + 8);
            if (!candidate || candidate == request.fallback) continue;
            if (!PdxV3CompanyTypesMatch(base, field<uintptr_t>(request.database, 0), field<uintptr_t>(candidate, 0)))
                return request.error = ERROR_INVALID_DATA;
            id = field<uint32_t>(candidate, 8);
            if (id == invalid_id || (id & id_index_mask) != current) continue;
        } else {
            id = reinterpret_cast<const uint32_t*>(bytes + request.ids_offset + sizeof(options))[current];
        }
        ++request.checked_count;
        PdxV3CompanyDetails record{};
        const DWORD object_error = write_record(base, companies, PDX_V3_COMPANY,
            id, request.fallback, PDX_V3_READ_OBJECT_IDS, reinterpret_cast<unsigned char*>(&record.object));
        if (enumeration && object_error) {
            if (object_error == ERROR_INVALID_DATA) return request.error = object_error;
            continue;
        }
        record.name_error = object_error ? object_error : record.object.company_type_error;
        if (!record.name_error) {
            // Original raw1b58b40(outCString32,realCompany). No fabricatedData
            // wrapper; Country query failure never gates this independentName.
            ScopedNativeCString name(base);
            try {
                PdxV3CompanyName(&name.value, record.object.pointer);
                name.constructed = true;
                PdxV3OwnedCStringBytes view{};
                record.name_error = PdxV3OwnedCStringView(&name.value, &view);
                if (!record.name_error && view.size > UINT64_MAX - options.text_offset - options.text_bytes_written)
                    record.name_error = ERROR_ARITHMETIC_OVERFLOW;
                if (!record.name_error) {
                    options.text_bytes_required = uint64_t(options.text_bytes_written) + view.size;
                    options.required_buffer_size = uint64_t(options.text_offset) + options.text_bytes_required;
                    if (view.size > uint64_t(options.text_capacity) - options.text_bytes_written) {
                        request.next_index = current;
                        request.truncated = 1;
                        return request.error = ERROR_MORE_DATA;
                    }
                    record.name_offset = options.text_offset + options.text_bytes_written;
                    record.name_bytes = uint32_t(view.size);
                    if (view.size) std::memcpy(bytes + record.name_offset,
                        reinterpret_cast<const void*>(view.data), size_t(view.size));
                    options.text_bytes_written += record.name_bytes;
                }
            } catch (...) { record.name_error = ERROR_UNHANDLED_EXCEPTION; }
        }
        *reinterpret_cast<PdxV3CompanyDetails*>(bytes + request.records_offset
            + size_t(request.returned_count) * request.record_size) = record;
        ++request.returned_count;
    }
    request.next_index = index;
    request.truncated = index < end;
    return request.error;
}
DWORD read_ship_details(uintptr_t base, PdxV3ObjectsRequest& request) {
    auto* bytes = reinterpret_cast<unsigned char*>(&request);
    auto& options = *reinterpret_cast<PdxV3ShipOptions*>(bytes + request.ids_offset);
    const uintptr_t slots = field<uintptr_t>(request.database, 0x20);
    request.slot_bound = field<uint32_t>(request.database, 0x2c);
    request.fallback = field<uintptr_t>(base, ships.fallback);
    if (request.slot_bound && !slots) return request.error = ERROR_INVALID_DATA;
    const bool enumeration = request.operation == PDX_V3_ENUMERATE_SHIP_DETAILS;
    const uint32_t end = enumeration ? request.slot_bound : request.input_count;
    const uint32_t limit = (std::min)(request.max_items, request.capacity);
    uint32_t index = (std::min)(request.start_index, end);
    while (index < end && request.returned_count < limit) {
        const uint32_t current = index++;
        ++request.scanned_count;
        uint32_t id;
        if (enumeration) {
            const uintptr_t candidate = field<uintptr_t>(slots, uintptr_t(current) * 16 + 8);
            if (!candidate || candidate == request.fallback) continue;
            if (!PdxV3ShipTypesMatch(base, field<uintptr_t>(request.database, 0), field<uintptr_t>(candidate, 0)))
                return request.error = ERROR_INVALID_DATA;
            id = field<uint32_t>(candidate, 8);
            if (id == invalid_id || (id & id_index_mask) != current) continue;
        } else {
            id = reinterpret_cast<const uint32_t*>(bytes + request.ids_offset + sizeof(options))[current];
        }
        ++request.checked_count;
        PdxV3ShipDetails record{};
        const DWORD object_error = write_record(base, ships, PDX_V3_SHIP, id,
            request.fallback, PDX_V3_READ_OBJECT_IDS, reinterpret_cast<unsigned char*>(&record.object));
        if (enumeration && object_error) {
            if (object_error == ERROR_INVALID_DATA) return request.error = object_error;
            continue;
        }
        record.name_no_formatting_error = object_error;
        if (!object_error) {
            ScopedNativeCString name(base);
            try {
                // Exact native17be620 takes rawShip RCX, out32 RDX. This is not
                // Company's out-firstABI or cachedformattedDataShip1cf3650.
                PdxV3ShipNameNoFormatting(record.object.pointer, &name.value);
                name.constructed = true;
                PdxV3OwnedCStringBytes view{};
                record.name_no_formatting_error = PdxV3OwnedCStringView(&name.value, &view);
                if (!record.name_no_formatting_error && view.size > UINT64_MAX - options.text_offset - options.text_bytes_written)
                    record.name_no_formatting_error = ERROR_ARITHMETIC_OVERFLOW;
                if (!record.name_no_formatting_error) {
                    options.text_bytes_required = uint64_t(options.text_bytes_written) + view.size;
                    options.required_buffer_size = uint64_t(options.text_offset) + options.text_bytes_required;
                    if (view.size > uint64_t(options.text_capacity) - options.text_bytes_written) {
                        request.next_index = current; request.truncated = 1;
                        return request.error = ERROR_MORE_DATA;
                    }
                    record.name_no_formatting_offset = options.text_offset + options.text_bytes_written;
                    record.name_no_formatting_bytes = uint32_t(view.size);
                    if (view.size) std::memcpy(bytes + record.name_no_formatting_offset,
                        reinterpret_cast<const void*>(view.data), size_t(view.size));
                    options.text_bytes_written += record.name_no_formatting_bytes;
                }
            } catch (...) { record.name_no_formatting_error = ERROR_UNHANDLED_EXCEPTION; }
        }
        *reinterpret_cast<PdxV3ShipDetails*>(bytes + request.records_offset
            + size_t(request.returned_count) * request.record_size) = record;
        ++request.returned_count;
    }
    request.next_index = index; request.truncated = index < end;
    return request.error;
}
}

uint32_t WINAPI PdxV3ObjectRecordSize(uint32_t domain) {
    const Domain* config = domain_config(domain);
    return config ? config->record_size : 0;
}
uint32_t WINAPI PdxV3ObjectOperationRecordSize(uint32_t domain, uint32_t operation) {
    return operation_record_size(domain, operation);
}
int64_t WINAPI PdxV3PopTotalSize(const void* wrapper) {
    return reinterpret_cast<PopIntegerGetter>(module_base() + 0x1df10e0)(wrapper);
}
int64_t WINAPI PdxV3PopWorkforce(const void* wrapper) {
    return reinterpret_cast<PopIntegerGetter>(module_base() + 0x1df1110)(wrapper);
}
int64_t WINAPI PdxV3PopDependents(const void* wrapper) {
    return reinterpret_cast<PopIntegerGetter>(module_base() + 0x1df1130)(wrapper);
}
uint64_t WINAPI PdxV3GoodsDatabase() { return goods_database(module_base()); }
uint64_t WINAPI PdxV3GoodsAtDefinitionIndex(uint64_t database, int32_t index) {
    return goods_at(module_base(), database, index);
}
uint64_t WINAPI PdxV3GoodsLookupKey(uint64_t database, int32_t key) {
    return reinterpret_cast<uintptr_t>(reinterpret_cast<GoodsIndexGetter>(module_base() + 0xdd4230)(
        reinterpret_cast<void*>(database), key));
}
uint64_t* WINAPI PdxV3GoodsTableValue(uint64_t table, uint64_t* bits, uint64_t goods) {
    return reinterpret_cast<GoodsValueGetter>(module_base() + 0x103f440)(
        reinterpret_cast<const void*>(table), bits, reinterpret_cast<const void*>(goods));
}
uint64_t* WINAPI PdxV3MarketPrice(const void* key, uint64_t* bits) {
    return reinterpret_cast<MarketPriceGetter>(module_base() + 0x195c610)(key, bits);
}
uint64_t* WINAPI PdxV3MarketBuyOrders(uint64_t market, uint64_t* bits, uint64_t goods) {
    return PdxV3GoodsTableValue(market + 0x1d8, bits, goods);
}
uint64_t* WINAPI PdxV3MarketSellOrders(uint64_t market, uint64_t* bits, uint64_t goods) {
    return PdxV3GoodsTableValue(market + 0x228, bits, goods);
}
uint64_t* WINAPI PdxV3MarketImports(const void* key, uint64_t* bits) {
    return reinterpret_cast<MarketPriceGetter>(module_base() + 0x196e640)(key, bits);
}
uint64_t* WINAPI PdxV3MarketExports(const void* key, uint64_t* bits) {
    return reinterpret_cast<MarketPriceGetter>(module_base() + 0x196e6a0)(key, bits);
}
uint64_t* WINAPI PdxV3MarketTariffsSubventionsBalance(const void* key, uint64_t* bits) {
    return reinterpret_cast<MarketPriceGetter>(module_base() + 0x19724c0)(key, bits);
}
uint64_t WINAPI PdxV3ResolveCountryObject(uint32_t full_id) {
    return resolve(module_base(), countries, full_id);
}
uint64_t WINAPI PdxV3ResolveStateObject(uint32_t full_id) {
    return resolve(module_base(), states, full_id);
}
uint64_t WINAPI PdxV3ResolveBuildingObject(uint32_t full_id) {
    return resolve(module_base(), buildings, full_id);
}
uint64_t WINAPI PdxV3ResolvePopObject(uint32_t full_id) {
    return resolve(module_base(), pops, full_id);
}
uint64_t WINAPI PdxV3ResolveMarketObject(uint32_t full_id) {
    return resolve(module_base(), markets, full_id);
}
uint64_t WINAPI PdxV3ResolveMilitaryFormationObject(uint32_t full_id) {
    return resolve(module_base(), military_formations, full_id);
}
uint64_t WINAPI PdxV3ResolveWarObject(uint32_t full_id) {
    return resolve(module_base(), wars, full_id);
}
uint64_t WINAPI PdxV3ResolveDiplomaticPlayObject(uint32_t full_id) {
    return resolve(module_base(), diplomatic_plays, full_id);
}
uint64_t WINAPI PdxV3ResolveCharacterObject(uint32_t full_id) {
    return resolve(module_base(), characters, full_id);
}
uint64_t WINAPI PdxV3ResolveShipObject(uint32_t full_id) { return resolve(module_base(), ships, full_id); }
uint64_t WINAPI PdxV3ShipCountry(uint64_t ship) { return resolve(module_base(), countries, field<uint32_t>(ship, 0x14)); }
uint64_t WINAPI PdxV3ShipFleet(uint64_t ship) { return resolve(module_base(), military_formations, field<uint32_t>(ship, 0x4c)); }
uint64_t WINAPI PdxV3ShipTemplateVersion(uint64_t ship) {
    return resolve(module_base(), ship_template_versions, field<uint32_t>(ship, 0x48));
}
uint64_t WINAPI PdxV3ShipTemplate(uint64_t ship) {
    const uintptr_t version = PdxV3ShipTemplateVersion(ship);
    return resolve(module_base(), ship_templates, field<uint32_t>(version, 0x10));
}
PdxV3OwnedCString* WINAPI PdxV3ShipNameNoFormatting(uint64_t ship, PdxV3OwnedCString* out) {
    using Getter = PdxV3OwnedCString* (__fastcall*)(const void*, PdxV3OwnedCString*);
    return reinterpret_cast<Getter>(module_base() + 0x17be620)(reinterpret_cast<const void*>(ship), out);
}
uint64_t WINAPI PdxV3ResolveCompanyObject(uint32_t full_id) { return resolve(module_base(), companies, full_id); }
uint64_t WINAPI PdxV3CompanyCountry(uint64_t company) {
    return resolve(module_base(), countries, field<uint32_t>(company, 0x60));
}
uint64_t WINAPI PdxV3CompanyType(uint64_t company) { return field<uintptr_t>(company, 0xa0); }
PdxV3OwnedCString* WINAPI PdxV3CompanyName(PdxV3OwnedCString* out, uint64_t company) {
    return reinterpret_cast<InterestGroupNameGetter>(module_base() + 0x1b58b40)(
        out, reinterpret_cast<const void*>(company));
}
uint64_t WINAPI PdxV3ResolveShippingLaneObject(uint32_t full_id) {
    return resolve(module_base(), shipping_lanes, full_id);
}
uint64_t WINAPI PdxV3ShippingLaneConvoyCostRaw64(uint64_t lane) { return field<uint64_t>(lane, 0x58); }
uint64_t* WINAPI PdxV3ShippingLaneEffectiveness(uint64_t lane, uint64_t* bits) {
    using Getter = uint64_t* (__fastcall*)(const void*, uint64_t*, void*);
    return reinterpret_cast<Getter>(module_base() + 0x131abf0)(reinterpret_cast<const void*>(lane), bits, nullptr);
}
uint64_t WINAPI PdxV3StateMarket(uint64_t state) {
    const uintptr_t base = module_base();
    uint32_t market_id;
    if (state_uses_market_area(state)) {
        const uint32_t area_id = field<uint32_t>(state, 0xb48);
        const auto area = reinterpret_cast<uintptr_t>(reinterpret_cast<Resolver>(base + 0x7cddd0)(&area_id));
        market_id = field<uint32_t>(area, 0x10);
    } else {
        const auto owner = resolve(base, countries, field<uint32_t>(state, 0xe48));
        market_id = field<uint32_t>(owner, 0xa24);
    }
    return resolve(base, markets, market_id);
}
uint64_t WINAPI PdxV3ResolveInterestGroupObject(uint32_t full_id) {
    return resolve(module_base(), interest_groups, full_id);
}
uint64_t WINAPI PdxV3InterestGroupCountry(uint64_t interest_group) {
    return resolve(module_base(), countries, field<uint32_t>(interest_group, 0xa4));
}
PdxV3OwnedCString* WINAPI PdxV3InterestGroupName(PdxV3OwnedCString* out, uint64_t interest_group) {
    return reinterpret_cast<InterestGroupNameGetter>(module_base() + 0x1b7a370)(
        out, reinterpret_cast<const void*>(interest_group));
}
void WINAPI PdxV3DestroyNativeCString(PdxV3OwnedCString* string) {
    reinterpret_cast<NativeCStringDestructor>(module_base() + 0x61f810)(string);
}
uint32_t WINAPI PdxV3CharacterIsFemale(const void* wrapper) {
    return reinterpret_cast<CharacterBoolGetter>(module_base() + 0x1a6a6f0)(wrapper);
}
uint64_t WINAPI PdxV3LawDefinitionDatabase() { return law_database(module_base(), false); }
uint64_t WINAPI PdxV3LawDefinitionAtIndex(uint64_t database, int32_t index) {
    return reinterpret_cast<uintptr_t>(reinterpret_cast<GoodsIndexGetter>(module_base() + 0x169afb0)(
        reinterpret_cast<void*>(database), index));
}
PdxV3DefinitionKeyView* WINAPI PdxV3LawDefinitionKey(uint64_t database, PdxV3DefinitionKeyView* view, int32_t index) {
    return reinterpret_cast<DefinitionKeyGetter>(module_base() + 0x169a4e0)(reinterpret_cast<void*>(database), view, index);
}
uint64_t WINAPI PdxV3LawGroupDefinitionDatabase() { return law_database(module_base(), true); }
PdxV3DefinitionKeyView* WINAPI PdxV3LawGroupDefinitionKey(uint64_t database, PdxV3DefinitionKeyView* view, int32_t index) {
    return reinterpret_cast<DefinitionKeyGetter>(module_base() + 0x67d7f0)(reinterpret_cast<void*>(database), view, index);
}
DWORD WINAPI PdxV3CopyDefinitionKey(const PdxV3DefinitionKeyView* view, void* output, uint32_t capacity, uint32_t* required) {
    if (!view || !required) return ERROR_INVALID_PARAMETER;
    *required = 0;
    const DWORD error = key_status(*view);
    if (error) return error;
    *required = uint32_t(view->size);
    if (*required > capacity) return ERROR_MORE_DATA;
    if (*required && !output) return ERROR_INVALID_PARAMETER;
    if (*required) std::memcpy(output, reinterpret_cast<const void*>(view->data), *required);
    return ERROR_SUCCESS;
}
uint64_t WINAPI PdxV3TechnologyDefinitionDatabase() { return technology_database(module_base()); }
uint64_t WINAPI PdxV3TechnologyDefinitionAtIndex(uint64_t database, int32_t index) {
    return reinterpret_cast<uintptr_t>(reinterpret_cast<GoodsIndexGetter>(module_base() + 0x70fe60)(
        reinterpret_cast<void*>(database), index));
}
PdxV3DefinitionKeyView* WINAPI PdxV3TechnologyDefinitionKey(uint64_t database, PdxV3DefinitionKeyView* view, int32_t index) {
    return reinterpret_cast<DefinitionKeyGetter>(module_base() + 0x691aa0)(reinterpret_cast<void*>(database), view, index);
}
uint64_t WINAPI PdxV3TechnologyDefinitionCategory(uint64_t technology) {
    return reinterpret_cast<uintptr_t>(reinterpret_cast<TechnologyCategoryGetter>(module_base() + 0x1716cf0)(
        reinterpret_cast<const void*>(technology)));
}
DWORD WINAPI PdxV3ReadObjects(PdxV3ObjectsRequest* request) {
    if (!request) return ERROR_INVALID_PARAMETER;
    const Domain* config;
    if (validate(*request, &config)) return request->error;
    const uintptr_t base = module_base();
    const uintptr_t app = field<uintptr_t>(base, application_root_rva);
    if (!app || !(request->world = field<uintptr_t>(app, world_offset)))
        return request->error = ERROR_NOT_READY;
    if (definition_operation(request->operation)) {
        if (request->domain == PDX_V3_TECHNOLOGY_DEFINITION) return read_technology_definitions(base, *request);
        return read_law_definitions(base, *request);
    }
    if (!(request->database = field<uintptr_t>(base, config->root))) return request->error = ERROR_NOT_READY;
    if (request->domain == PDX_V3_INTEREST_GROUP) {
        if (!PdxV3InterestGroupTypesMatch(base, field<uintptr_t>(request->database, 0), 0))
            return request->error = ERROR_INVALID_DATA;
        if (interest_group_details(request->operation)) return read_interest_group_details(base, *request);
    }
    if (request->domain == PDX_V3_SHIP) {
        if (!PdxV3ShipTypesMatch(base, field<uintptr_t>(request->database, 0), 0))
            return request->error = ERROR_INVALID_DATA;
        if (ship_details(request->operation)) return read_ship_details(base, *request);
    }
    if (request->domain == PDX_V3_COMPANY) {
        if (!PdxV3CompanyTypesMatch(base, field<uintptr_t>(request->database, 0), 0))
            return request->error = ERROR_INVALID_DATA;
        if (company_details(request->operation)) return read_company_details(base, *request);
    }
    if (request->domain == PDX_V3_SHIPPING_LANE
        && !PdxV3ShippingLaneTypesMatch(base, field<uintptr_t>(request->database, 0), 0))
        return request->error = ERROR_INVALID_DATA;
    if (market_goods(request->operation)) return read_market_goods(base, *request);
    const uintptr_t slots = field<uintptr_t>(request->database, 0x20);
    request->slot_bound = field<uint32_t>(request->database, 0x2c);
    request->fallback = field<uintptr_t>(base, config->fallback);
    if (request->slot_bound && !slots) return request->error = ERROR_INVALID_DATA;
    auto* bytes = reinterpret_cast<unsigned char*>(request);
    auto* output = bytes + request->records_offset;
    const uint32_t limit = (std::min)(request->max_items, request->capacity);
    const uint32_t end = enumerating(request->operation)
        ? request->slot_bound : request->input_count;
    uint32_t index = (std::min)(request->start_index, end);
    while (index < end && request->returned_count < limit) {
        uint32_t id;
        const uint32_t current = index++;
        ++request->scanned_count;
        if (enumerating(request->operation)) {
            const uintptr_t candidate = field<uintptr_t>(slots, uintptr_t(current) * 16 + 8);
            if (!candidate || candidate == request->fallback) continue;
            if (request->domain == PDX_V3_INTEREST_GROUP
                && !PdxV3InterestGroupTypesMatch(base, field<uintptr_t>(request->database, 0), field<uintptr_t>(candidate, 0)))
                return request->error = ERROR_INVALID_DATA;
            if (request->domain == PDX_V3_SHIPPING_LANE
                && !PdxV3ShippingLaneTypesMatch(base, field<uintptr_t>(request->database, 0), field<uintptr_t>(candidate, 0)))
                return request->error = ERROR_INVALID_DATA;
            if (request->domain == PDX_V3_COMPANY
                && !PdxV3CompanyTypesMatch(base, field<uintptr_t>(request->database, 0), field<uintptr_t>(candidate, 0)))
                return request->error = ERROR_INVALID_DATA;
            if (request->domain == PDX_V3_SHIP
                && !PdxV3ShipTypesMatch(base, field<uintptr_t>(request->database, 0), field<uintptr_t>(candidate, 0)))
                return request->error = ERROR_INVALID_DATA;
            id = field<uint32_t>(candidate, config->id_offset);
            if (id == invalid_id || (id & id_index_mask) != current) continue;
        } else {
            id = reinterpret_cast<const uint32_t*>(bytes + request->ids_offset)[current];
        }
        ++request->checked_count;
        const DWORD error = write_record(base, *config, request->domain, id,
            request->fallback, request->operation, output + size_t(request->returned_count) * request->record_size);
        if (!enumerating(request->operation) || !error) ++request->returned_count;
    }
    request->next_index = index;
    request->truncated = index < end;
    return request->error;
}
void WINAPI PdxV3ObjectsInterface(void* application, void* opaque) {
    auto* request = static_cast<PdxV3ObjectsRequest*>(opaque);
    if (!request) return;
    const Domain* config;
    if (validate(*request, &config)) return;
    request->error = ERROR_NOT_READY;
    const uintptr_t base = module_base();
    const uintptr_t app = reinterpret_cast<uintptr_t>(application);
    if (!app || app != field<uintptr_t>(base, application_root_rva)
        || field<uint8_t>(app, 0x258) || !field<uintptr_t>(app, world_offset)) return;
    const uintptr_t idler = field<uintptr_t>(app, 0x1c0);
    if (!idler || field<uintptr_t>(idler, 0) != base + ingame_idler_vtable_rva) return;
    NativeAccessStorage storage;
    try {
        InterfaceAccessScope scope(storage, base);
        // All enumeration, native resolvers/getter and typed copies share this scope.
        PdxV3ReadObjects(request);
    } catch (...) {
        request->error = ERROR_UNHANDLED_EXCEPTION;
    }
}
