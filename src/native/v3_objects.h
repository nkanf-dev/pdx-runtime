#pragma once
#include <Windows.h>
#include <stdint.h>

#if defined(PDX_V3_OBJECTS_BUILD) || defined(PDX_RUNTIME_BUILD)
#define PDX_V3_OBJECTS_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_OBJECTS_API extern "C" __declspec(dllimport)
#endif

// Analyzed V3 1.13.11 image only. IDs include their full generation bits.
enum PdxV3ObjectDomain : uint32_t {
    PDX_V3_COUNTRY = 1, PDX_V3_STATE = 2, PDX_V3_BUILDING = 3,
    PDX_V3_POP = 4, PDX_V3_MARKET = 5, PDX_V3_MILITARY_FORMATION = 6, PDX_V3_WAR = 7,
    PDX_V3_DIPLOMATIC_PLAY = 8, PDX_V3_CHARACTER = 9, PDX_V3_LAW_DEFINITION = 10,
    PDX_V3_TECHNOLOGY_DEFINITION = 11, PDX_V3_INTEREST_GROUP = 12, PDX_V3_SHIPPING_LANE = 13, PDX_V3_COMPANY = 14, PDX_V3_SHIP = 15
};
enum PdxV3ObjectOperation : uint32_t {
    PDX_V3_ENUMERATE_OBJECTS = 1, PDX_V3_READ_OBJECT_IDS = 2,
    PDX_V3_ENUMERATE_POP_DETAILS = 3, PDX_V3_READ_POP_DETAILS = 4,
    PDX_V3_READ_MARKET_GOODS = 5, PDX_V3_ENUMERATE_MARKET_GOODS = 6,
    PDX_V3_READ_MARKET_GOODS_DETAILS = 7, PDX_V3_ENUMERATE_MARKET_GOODS_DETAILS = 8,
    PDX_V3_ENUMERATE_DEFINITIONS = 9, PDX_V3_READ_DEFINITION_INDICES = 10,
    PDX_V3_ENUMERATE_INTEREST_GROUP_DETAILS = 11, PDX_V3_READ_INTEREST_GROUP_DETAILS = 12,
    PDX_V3_ENUMERATE_COMPANY_DETAILS = 13, PDX_V3_READ_COMPANY_DETAILS = 14,
    PDX_V3_ENUMERATE_SHIP_DETAILS = 15, PDX_V3_READ_SHIP_DETAILS = 16
};

// One caller-owned buffer: this header, optional uint32 IDs, typed output records.
// Offsets are relative to the header. No retained reference. Original Name
// getter temporaries may allocate native strings; their game dtor runs before return.
// max_items and capacity are caller-selected, with no global result cap.
struct PdxV3ObjectsRequest {
    uint32_t size;
    uint32_t error;
    uint32_t domain;
    uint32_t operation;
    uint32_t start_index;       // Slot index for ENUMERATE; input index for READ_IDS.
    uint32_t max_items;
    uint32_t capacity;
    uint32_t input_count;
    uint32_t ids_offset;
    uint32_t records_offset;
    uint32_t record_size;
    uint32_t returned_count;
    uint32_t checked_count;     // Full IDs checked by the bulk resolver, not DB size.
    uint32_t scanned_count;     // Slots/inputs considered in this request.
    uint32_t next_index;
    uint32_t slot_bound;        // Database index bound, NOT a live-object count.
    uint32_t truncated;         // Unscanned slots/inputs remain; not a live-count claim.
    uint32_t buffer_size;
    uint64_t database;
    uint64_t fallback;
    uint64_t world;             // Observed under this scope, not a durable handle.
};
struct PdxV3CountryObject {
    uint32_t requested_id;
    uint32_t resolved_id;
    uint32_t error;
    uint32_t slot_index;
    uint64_t pointer;
    uint64_t gdp_raw64;
};
struct PdxV3StateObject {
    uint32_t requested_id;
    uint32_t resolved_id;
    uint32_t error;
    uint32_t slot_index;
    uint64_t pointer;
    uint32_t owner_id;
    uint32_t reserved;
};
struct PdxV3BuildingObject {
    uint32_t requested_id;
    uint32_t resolved_id;
    uint32_t error;
    uint32_t slot_index;
    uint64_t pointer;
    uint32_t state_id;
    int32_t levels;
    uint64_t type_pointer;      // Dynamic Type pointer only; no guessed name/fallback.
};

struct PdxV3PopObject {
    uint32_t requested_id;
    uint32_t resolved_id;
    uint32_t error;
    uint32_t slot_index;
    uint64_t pointer;
    uint64_t type_pointer;      // Borrowed native constructor Type argument at +0x10.
};
struct PdxV3MarketObject {
    uint32_t requested_id;
    uint32_t resolved_id;
    uint32_t error;
    uint32_t slot_index;
    uint64_t pointer;           // Reference-object origin, NOT serializer subobject+0x10.
    uint32_t owner_id;          // Cached full Country reference at +0x848; may be -1.
    uint32_t reserved;
};

struct PdxV3MilitaryFormationObject {
    uint32_t requested_id, resolved_id, error, slot_index;
    uint64_t pointer; // Actual CMilitaryFormation native reference origin.
    uint32_t owner_country_id, owner_country_error;
    uint64_t owner_country_pointer;
};

struct PdxV3WarObject {
    uint32_t requested_id, resolved_id, error, slot_index;
    uint64_t pointer;
    uint32_t diplomatic_play_id, diplomatic_play_error;
    uint64_t diplomatic_play_pointer;
    uint32_t attacker_leader_id, defender_leader_id;
    uint32_t attacker_leader_error, defender_leader_error;
    uint64_t attacker_leader_pointer, defender_leader_pointer;
};

struct PdxV3DiplomaticPlayObject {
    uint32_t requested_id, resolved_id, error, slot_index;
    uint64_t pointer;
    uint32_t attacker_leader_id, defender_leader_id;
    uint32_t attacker_leader_error, defender_leader_error;
    uint64_t attacker_leader_pointer, defender_leader_pointer;
};

struct PdxV3CharacterObject {
    uint32_t requested_id, resolved_id, error, slot_index;
    uint64_t pointer; // Actual CCharacter origin; not a CDataCharacter wrapper.
    uint32_t is_female, is_female_error;
};

// Explicit new operations preserve the old Pop32B / Market32B ABI.
struct PdxV3PopDetails {
    uint32_t requested_id, resolved_id, error, slot_index;
    uint64_t pointer, type_pointer;
    int64_t total_size, workforce, dependents;
    uint32_t state_id, country_id;
    uint32_t quantities_error, state_error, country_error, reserved;
    uint64_t state_pointer, country_pointer;
};
// READ_MARKET_GOODS input array at ids_offset; each Goods is a real native pointer.
struct PdxV3MarketGoodsInput {
    uint32_t market_id, reserved;
    uint64_t goods_pointer;
};
struct PdxV3MarketGoodsValue {
    uint32_t requested_market_id, resolved_market_id, market_error, goods_error;
    uint64_t market_pointer, goods_pointer, price_raw64;
    int32_t goods_index;
    uint32_t definition_index, price_error, reserved;
};
struct PdxV3MarketGoodsDetails {
    PdxV3MarketGoodsValue price; // Preserve the already verified 56B prefix.
    uint64_t buy_orders_raw64, sell_orders_raw64, imports_raw64, exports_raw64;
    uint32_t buy_orders_error, sell_orders_error, imports_error, exports_error;
};
// ENUMERATE_MARKET_GOODS has input_count=1 and uint32 full MarketID at ids_offset.
// start/next/slot_bound are Goods definition indexes/count, NOT a RefDB live count.

// Definitions are not full-ID RefDatabase objects. New operations9/10 only.
struct PdxV3DefinitionKeyView {
    uint64_t data;
    int32_t size;
    uint8_t is_null;
    uint8_t reserved[3];
}; // Borrowed native CPdxStringView output; no destructor/AddRef.
struct PdxV3DefinitionOptions {
    uint32_t text_offset, text_capacity, text_bytes_written, reserved;
    uint64_t text_bytes_required, required_buffer_size;
}; //32B at ids_offset, followed by input_count signed int32 definition ordinals.
struct PdxV3LawDefinitionObject {
    int32_t requested_index, resolved_index;
    uint32_t error, key_error;
    uint64_t pointer;
    int32_t group_index;
    uint32_t group_error;
    uint64_t group_pointer;
    uint32_t key_offset, key_bytes, key_size;
    uint32_t group_key_offset, group_key_bytes, group_key_size, group_key_error, reserved;
}; //72B, offsets relative to request; both keys are complete copied UTF-8 bytes.
struct PdxV3TechnologyDefinitionObject {
    int32_t requested_index, resolved_index;
    uint32_t error, key_error;
    uint64_t pointer; // Actual CTechnologyType definition, never CDataTechnology.
    int32_t category_index;
    uint32_t category_error;
    uint64_t category_pointer;
    uint32_t category_token, category_token_error, category_is_default;
    uint32_t key_offset, key_bytes, key_size;
}; //64B; only the full native Technology key is copied to the text arena.
inline bool PdxV3TechnologyDefinitionDatabaseType(uint64_t actual_vptr, uint64_t image_base) {
    return actual_vptr == image_base + 0x44250c8;
}
inline bool PdxV3TechnologyCategoryIndexInRange(int32_t index, int32_t count) {
    return index >= 0 && count >= 0 && index < count;
}

inline bool PdxV3DefinitionTextFits(uint32_t used, uint32_t capacity, uint64_t needed) {
    return used <= capacity && needed <= uint64_t(capacity - used);
}

// Live Ship refs; four16B relations are record layout, not native +24 offsets.
struct PdxV3ShipObject {
    uint32_t requested_id, resolved_id, error, slot_index;
    uint64_t pointer;
    uint32_t country_id, country_error; uint64_t country_pointer;
    uint32_t fleet_id, fleet_error; uint64_t fleet_pointer; // AssignedMilitaryFormation, NOT TargetFleet.
    uint32_t template_version_id, template_version_error; uint64_t template_version_pointer;
    uint32_t template_id, template_error; uint64_t template_pointer;
};
struct PdxV3ShipDetails {
    PdxV3ShipObject object;
    uint32_t name_no_formatting_error, name_no_formatting_offset, name_no_formatting_bytes, reserved;
};
using PdxV3ShipOptions = PdxV3DefinitionOptions;

// Live Company refs are not static CompanyType definition ordinals/full IDs.
struct PdxV3CompanyObject {
    uint32_t requested_id, resolved_id, error, slot_index;
    uint64_t pointer;
    uint32_t country_id, country_error; uint64_t country_pointer;
    uint64_t company_type_pointer; // True borrowed CCompanyType* at native +a0.
    uint32_t company_type_error, reserved;
};
struct PdxV3CompanyDetails {
    PdxV3CompanyObject object;
    uint32_t name_error, name_offset, name_bytes, reserved;
};
using PdxV3CompanyOptions = PdxV3DefinitionOptions;

// Real current ShippingLane transport reference; not a legacy TradeRoute alias.
struct PdxV3ShippingLaneObject {
    uint32_t requested_id, resolved_id, error, slot_index;
    uint64_t pointer;
    uint32_t country_id, country_error; uint64_t country_pointer;
    uint32_t begin_state_id, begin_state_error; uint64_t begin_state_pointer;
    uint32_t end_state_id, end_state_error; uint64_t end_state_pointer;
    uint32_t begin_market_id, begin_market_error; uint64_t begin_market_pointer;
    uint32_t end_market_id, end_market_error; uint64_t end_market_pointer;
    uint64_t convoy_cost_raw64, effectiveness_raw64;
    uint32_t convoy_cost_error, effectiveness_error;
};

// Real live CInterestGroup IDs, NOT CInterestGroupType definition ordinals.
struct PdxV3InterestGroupObject {
    uint32_t requested_id, resolved_id, error, slot_index;
    uint64_t pointer;
    uint32_t country_id, country_error;
    uint64_t country_pointer; // Checked full Country relation at native IG+0xa4.
};
struct PdxV3InterestGroupDetails {
    PdxV3InterestGroupObject object;
    uint32_t name_error, name_offset, name_bytes, reserved;
    // The complete localized native display bytes live in the caller text arena.
    // This name is not an InterestGroupType key or a durable identity.
};
// Same 32B caller-sized text metadata, with full uint32 IG IDs after this block.
using PdxV3InterestGroupOptions = PdxV3DefinitionOptions;
// Owned native CString returned by the original Name getter. Only game dtor may
// free it. bytes0..15: inline/heap; size64+16; capacity64+24; inline cap<=15.
struct alignas(8) PdxV3OwnedCString { uint64_t storage[4]; };
struct PdxV3OwnedCStringBytes { uint64_t data, size; };

inline bool PdxV3ShipTypesMatch(uintptr_t base, uintptr_t database_vptr, uintptr_t object_vptr) {
    return database_vptr == base + 0x4439598 && (!object_vptr || object_vptr == base + 0x44e1a10);
}
inline bool PdxV3ShipTemplateTypesMatch(uintptr_t base, uintptr_t database_vptr, uintptr_t object_vptr, bool version) {
    return database_vptr == base + (version ? 0x44398e0 : 0x4439448)
        && (!object_vptr || object_vptr == base + (version ? 0x44e2030 : 0x44e2048));
}
// Raw caller holds original accepted Interface/Gfx scope and true live objects.
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveShipObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ShipCountry(uint64_t ship);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ShipFleet(uint64_t ship);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ShipTemplateVersion(uint64_t ship);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ShipTemplate(uint64_t ship);
// Original17be620: rawShip RCX/outCString32 RDX. NotCompanyout-firstABI,
// not formattedGetName's DataShipcache. Destroy only after successful return.
PDX_V3_OBJECTS_API PdxV3OwnedCString* WINAPI PdxV3ShipNameNoFormatting(uint64_t ship, PdxV3OwnedCString* out);

inline bool PdxV3CompanyTypesMatch(uintptr_t base, uintptr_t db_vptr, uintptr_t object_vptr) {
    return db_vptr == base + 0x44394f0 && (!object_vptr || object_vptr == base + 0x449eac0);
}
// Native static definition identity, not a runtime RefDB ID/prototype generation.
inline bool PdxV3CompanyDefinitionType(uintptr_t base, uintptr_t definition_vptr) {
    return definition_vptr == base + 0x4507110;
}
// Caller holds original native scope and true current borrowed objects.
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveCompanyObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3CompanyCountry(uint64_t company);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3CompanyType(uint64_t company);
// Constructs ownedCString32; out must not already own a native string. After
// successful return copy its complete bytes then PdxV3DestroyNativeCString once.
PDX_V3_OBJECTS_API PdxV3OwnedCString* WINAPI PdxV3CompanyName(PdxV3OwnedCString* out, uint64_t company);

// Current real transport reference and original native relation/scalar primitives.
// Caller supplies current native scope and correctly typed live borrowed pointers.
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveShippingLaneObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ShippingLaneConvoyCostRaw64(uint64_t lane);
PDX_V3_OBJECTS_API uint64_t* WINAPI PdxV3ShippingLaneEffectiveness(uint64_t lane, uint64_t* bits);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3StateMarket(uint64_t state);
inline bool PdxV3ShippingLaneTypesMatch(uintptr_t base, uintptr_t db_vptr, uintptr_t object_vptr) {
    return db_vptr == base + 0x4439410 && (!object_vptr || object_vptr == base + 0x4427d98);
}
inline bool PdxV3StateUsesMarketArea(uint8_t native_flag, uint32_t area_id) {
    return native_flag && area_id != 0xffffffff;
}

// Original primitives: caller holds current native scope and real live pointers.
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveInterestGroupObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3InterestGroupCountry(uint64_t interest_group);
// Name constructs out; out must not already own a native string. Call native
// destroy exactly once after copying, including empty/inline names.
PDX_V3_OBJECTS_API PdxV3OwnedCString* WINAPI PdxV3InterestGroupName(PdxV3OwnedCString* out, uint64_t interest_group);
PDX_V3_OBJECTS_API void WINAPI PdxV3DestroyNativeCString(PdxV3OwnedCString* string);
inline bool PdxV3InterestGroupTypesMatch(uintptr_t image_base, uintptr_t database_vptr, uintptr_t object_vptr) {
    return database_vptr == image_base + 0x44395d0
        && (!object_vptr || object_vptr == image_base + 0x44a4290);
}
// Pure borrowed view until native CString destroy. No engine call/allocation.
inline DWORD PdxV3OwnedCStringView(const PdxV3OwnedCString* string, PdxV3OwnedCStringBytes* view) {
    if (!string || !view) return ERROR_INVALID_PARAMETER;
    *view = {};
    const uint64_t size = string->storage[2], capacity = string->storage[3];
    if (size > capacity || (capacity <= 15 && size > 15)) return ERROR_INVALID_DATA;
    const uintptr_t data = capacity > 15 ? uintptr_t(string->storage[0])
        : reinterpret_cast<uintptr_t>(string->storage);
    if (size && !data) return ERROR_INVALID_DATA;
    *view = {data, size};
    return ERROR_SUCCESS;
}

PDX_V3_OBJECTS_API uint32_t WINAPI PdxV3ObjectRecordSize(uint32_t domain);
PDX_V3_OBJECTS_API uint32_t WINAPI PdxV3ObjectOperationRecordSize(uint32_t domain, uint32_t operation);
// Direct real native resolvers. Caller supplies an existing native game-access
// scope. These preserve native fallback/diagnostic behavior, including null DB.
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveCountryObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveStateObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveBuildingObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolvePopObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveMarketObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveMilitaryFormationObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveWarObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveDiplomaticPlayObject(uint32_t full_id);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3ResolveCharacterObject(uint32_t full_id);
// Exact IsFemale leaf1a6a6f0 reads complete ID at minimal_data_character+10,
// resolves through native7cac40 and returns byte(raw+80)&1. No validity proof
// is implied by a raw false result; caller supplies the native access scope.
PDX_V3_OBJECTS_API uint32_t WINAPI PdxV3CharacterIsFemale(const void* minimal_data_character);
// Raw exact leaves run in caller's existing native scope. MinimalDataPop reads ID+10.
PDX_V3_OBJECTS_API int64_t WINAPI PdxV3PopTotalSize(const void* minimal_data_pop);
PDX_V3_OBJECTS_API int64_t WINAPI PdxV3PopWorkforce(const void* minimal_data_pop);
PDX_V3_OBJECTS_API int64_t WINAPI PdxV3PopDependents(const void* minimal_data_pop);
// Provider returns borrowed actual GoodsDB through the original native TLS/access path.
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3GoodsDatabase();
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3GoodsAtDefinitionIndex(uint64_t database, int32_t index);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3GoodsLookupKey(uint64_t database, int32_t key);
PDX_V3_OBJECTS_API uint64_t* WINAPI PdxV3GoodsTableValue(uint64_t table, uint64_t* bits, uint64_t goods);
// DataGoods minimum key: unused16, CGoods*+10, StateID+18, MarketID+1c.
PDX_V3_OBJECTS_API uint64_t* WINAPI PdxV3MarketPrice(const void* minimal_data_goods, uint64_t* bits);
// Ordinary CGoods branch of native GetMarketBuyOrders/GetMarketSellOrders.
PDX_V3_OBJECTS_API uint64_t* WINAPI PdxV3MarketBuyOrders(uint64_t market, uint64_t* bits, uint64_t goods);
PDX_V3_OBJECTS_API uint64_t* WINAPI PdxV3MarketSellOrders(uint64_t market, uint64_t* bits, uint64_t goods);
PDX_V3_OBJECTS_API uint64_t* WINAPI PdxV3MarketImports(const void* minimal_data_goods, uint64_t* bits);
PDX_V3_OBJECTS_API uint64_t* WINAPI PdxV3MarketExports(const void* minimal_data_goods, uint64_t* bits);
// Exact named financial balance leaf, NOT buy-minus-sell/supply-demand balance.
// Native DataMarket cache supplies borrowed context internally; caller owns bits only.
PDX_V3_OBJECTS_API uint64_t* WINAPI PdxV3MarketTariffsSubventionsBalance(
    const void* minimal_data_goods, uint64_t* bits);
inline bool PdxV3LawDefinitionDatabaseType(uint64_t actual_vptr, uint64_t image_base, bool group) {
    return actual_vptr == image_base + (group ? 0x4425638 : 0x450db20);
}
// Exact LawType definition primitives: caller supplies current native access scope.
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3LawDefinitionDatabase();
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3LawDefinitionAtIndex(uint64_t database, int32_t index);
PDX_V3_OBJECTS_API PdxV3DefinitionKeyView* WINAPI PdxV3LawDefinitionKey(
    uint64_t database, PdxV3DefinitionKeyView* view, int32_t index);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3LawGroupDefinitionDatabase();
PDX_V3_OBJECTS_API PdxV3DefinitionKeyView* WINAPI PdxV3LawGroupDefinitionKey(
    uint64_t database, PdxV3DefinitionKeyView* view, int32_t index);
// Exact CTechnologyType primitives. Category's raw native getter checks only -1;
// callers must supply an actual definition and valid positive category index.
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3TechnologyDefinitionDatabase();
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3TechnologyDefinitionAtIndex(uint64_t database, int32_t index);
PDX_V3_OBJECTS_API PdxV3DefinitionKeyView* WINAPI PdxV3TechnologyDefinitionKey(
    uint64_t database, PdxV3DefinitionKeyView* view, int32_t index);
PDX_V3_OBJECTS_API uint64_t WINAPI PdxV3TechnologyDefinitionCategory(uint64_t technology);
// Copies a borrowed native view only when the whole key fits; no partial UTF-8 write.
PDX_V3_OBJECTS_API DWORD WINAPI PdxV3CopyDefinitionKey(const PdxV3DefinitionKeyView* view,
    void* output, uint32_t capacity, uint32_t* required);
// Compose resolver/full-ID checks and minimum typed fields in caller's scope.
// ENUMERATE reads raw slots ONLY to obtain full IDs, then calls native resolver.
// READ_IDS preserves a typed record/error for each checked ID, including invalids.
PDX_V3_OBJECTS_API DWORD WINAPI PdxV3ReadObjects(PdxV3ObjectsRequest* request);
// PdxV3QueueInterfaceCallback ABI. Enters native (1,0) access once for the entire
// batch, then releases it before returning. Keep the whole buffer allocated until
// queue completion. Copied scalars are snapshots; all returned pointers are borrowed
// process-local observations and can become stale after this callback/world change.
PDX_V3_OBJECTS_API void WINAPI PdxV3ObjectsInterface(void* application, void* opaque);
