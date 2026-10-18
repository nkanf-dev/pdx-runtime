#include "../v3_objects.h"
#include <cstdio>
#include <vector>
#include <cstring>

namespace {
int failures = 0;
void check(bool pass, const char* message) {
    std::printf("%s %s\n", pass ? "PASS" : "FAIL", message);
    if (!pass) ++failures;
}
std::vector<unsigned char> buffer(uint32_t domain, uint32_t operation,
                                  uint32_t capacity, uint32_t input_count) {
    const uint32_t stride = PdxV3ObjectOperationRecordSize(domain, operation);
    const uint32_t input_stride = operation == PDX_V3_READ_MARKET_GOODS
        || operation == PDX_V3_READ_MARKET_GOODS_DETAILS ? 16 : 4;
    const uint32_t records = (sizeof(PdxV3ObjectsRequest) + input_count * input_stride + 7) & ~7u;
    std::vector<unsigned char> bytes(records + capacity * stride);
    auto& request = *reinterpret_cast<PdxV3ObjectsRequest*>(bytes.data());
    request = {sizeof(PdxV3ObjectsRequest), ERROR_IO_PENDING, domain, operation,
        0, capacity, capacity, input_count,
        operation != PDX_V3_ENUMERATE_OBJECTS && operation != PDX_V3_ENUMERATE_POP_DETAILS
            ? uint32_t(sizeof(PdxV3ObjectsRequest)) : 0,
        records, stride, 0, 0, 0, 0, 0, 0, uint32_t(bytes.size()), 0, 0, 0};
    return bytes;
}
std::vector<unsigned char> definition_buffer(uint32_t operation, uint32_t capacity,
                                              uint32_t input_count, uint32_t text_capacity,
                                              uint32_t domain = PDX_V3_LAW_DEFINITION) {
    const uint32_t stride = PdxV3ObjectOperationRecordSize(domain, operation);
    const uint32_t records = (sizeof(PdxV3ObjectsRequest) + sizeof(PdxV3DefinitionOptions) + input_count * 4 + 7) & ~7u;
    const uint32_t text = records + capacity * stride;
    std::vector<unsigned char> bytes(text + text_capacity);
    auto& request = *reinterpret_cast<PdxV3ObjectsRequest*>(bytes.data());
    request = {sizeof(PdxV3ObjectsRequest), ERROR_IO_PENDING, domain,
        operation, 0, capacity, capacity, input_count, sizeof(PdxV3ObjectsRequest),
        records, stride, 0, 0, 0, 0, 0, 0, uint32_t(bytes.size()), 0, 0, 0};
    *reinterpret_cast<PdxV3DefinitionOptions*>(bytes.data() + request.ids_offset)
        = {text, text_capacity, 0, 0, 0, text};
    return bytes;
}

}
int main() {
    std::printf("v3_objects_host: only layout/preconditions; no game/DB access\n");
    check(sizeof(PdxV3ObjectsRequest) == 96, "header ABI size");
    check(PdxV3ObjectRecordSize(PDX_V3_COUNTRY) == 32
        && PdxV3ObjectRecordSize(PDX_V3_STATE) == 32
        && PdxV3ObjectRecordSize(PDX_V3_BUILDING) == 40
        && PdxV3ObjectRecordSize(PDX_V3_POP) == 32
        && PdxV3ObjectRecordSize(PDX_V3_MARKET) == 32
        && PdxV3ObjectRecordSize(PDX_V3_MILITARY_FORMATION) == 40
        && PdxV3ObjectRecordSize(PDX_V3_WAR) == 72
        && PdxV3ObjectRecordSize(PDX_V3_DIPLOMATIC_PLAY) == 56
        && PdxV3ObjectRecordSize(PDX_V3_CHARACTER) == 32
        && PdxV3ObjectRecordSize(99) == 0, "nine independently proved domain strides");
    check(PdxV3ReadObjects(nullptr) == ERROR_INVALID_PARAMETER, "null input rejected");
    auto bytes = buffer(PDX_V3_BUILDING, PDX_V3_READ_OBJECT_IDS, 1, 2);
    auto& request = *reinterpret_cast<PdxV3ObjectsRequest*>(bytes.data());
    request.record_size = 32;
    check(PdxV3ReadObjects(&request) == ERROR_INVALID_PARAMETER, "wrong stride rejected");
    request.record_size = 40;
    request.buffer_size -= 1;
    check(PdxV3ReadObjects(&request) == ERROR_INVALID_PARAMETER, "output capacity bounds checked");
    request.buffer_size += 1;
    request.records_offset = request.ids_offset;
    check(PdxV3ReadObjects(&request) == ERROR_INVALID_PARAMETER, "input/output overlap rejected");
    bytes = buffer(PDX_V3_STATE, PDX_V3_ENUMERATE_OBJECTS, 0, 0);
    auto* no_world = reinterpret_cast<PdxV3ObjectsRequest*>(bytes.data());
    no_world->returned_count = 77;
    PdxV3ObjectsInterface(nullptr, no_world);
    check(no_world->error == ERROR_NOT_READY && no_world->returned_count == 0
        && no_world->database == 0, "null application callback produces no invented records");
    for (uint32_t domain : {uint32_t(PDX_V3_POP), uint32_t(PDX_V3_MARKET)}) {
        auto batch = buffer(domain, PDX_V3_READ_OBJECT_IDS, 2, 3);
        auto* input = reinterpret_cast<PdxV3ObjectsRequest*>(batch.data());
        input->returned_count = input->checked_count = 99;
        PdxV3ObjectsInterface(nullptr, input);
        check(input->error == ERROR_NOT_READY && input->returned_count == 0
            && input->checked_count == 0 && input->record_size == 32,
            domain == PDX_V3_POP ? "Pop null application no fabricated fields"
                                : "Market null application no fabricated fields");
    }
    check(PdxV3ObjectOperationRecordSize(PDX_V3_POP, PDX_V3_READ_POP_DETAILS) == 96
        && PdxV3ObjectOperationRecordSize(PDX_V3_MARKET, PDX_V3_READ_MARKET_GOODS) == 56
        && PdxV3ObjectOperationRecordSize(PDX_V3_MARKET, PDX_V3_ENUMERATE_MARKET_GOODS) == 56
        && PdxV3ObjectOperationRecordSize(PDX_V3_MARKET, PDX_V3_READ_MARKET_GOODS_DETAILS) == 104
        && PdxV3ObjectOperationRecordSize(PDX_V3_MARKET, PDX_V3_ENUMERATE_MARKET_GOODS_DETAILS) == 104,
        "new explicit operation record ABI");
    check(PdxV3ObjectOperationRecordSize(PDX_V3_COUNTRY, PDX_V3_READ_POP_DETAILS) == 0
        && PdxV3ObjectOperationRecordSize(PDX_V3_POP, PDX_V3_READ_MARKET_GOODS) == 0,
        "operation and domain do not masquerade as another type");
    for (uint32_t operation : {uint32_t(PDX_V3_READ_POP_DETAILS),
            uint32_t(PDX_V3_READ_MARKET_GOODS), uint32_t(PDX_V3_ENUMERATE_MARKET_GOODS)}) {
        const auto domain = operation == PDX_V3_READ_POP_DETAILS ? PDX_V3_POP : PDX_V3_MARKET;
        auto batch = buffer(domain, operation, 1, 1);
        auto* input = reinterpret_cast<PdxV3ObjectsRequest*>(batch.data());
        PdxV3ObjectsInterface(nullptr, input);
        check(input->error == ERROR_NOT_READY && input->returned_count == 0,
              "extended callback validates then returns unavailable without game access");
    }
    auto wrong_market = buffer(PDX_V3_MARKET, PDX_V3_ENUMERATE_MARKET_GOODS, 1, 2);
    check(PdxV3ReadObjects(reinterpret_cast<PdxV3ObjectsRequest*>(wrong_market.data()))
        == ERROR_INVALID_PARAMETER, "Goods definition page requires exactly one full MarketID");
    for (uint32_t operation : {uint32_t(PDX_V3_READ_MARKET_GOODS_DETAILS),
            uint32_t(PDX_V3_ENUMERATE_MARKET_GOODS_DETAILS)}) {
        auto batch = buffer(PDX_V3_MARKET, operation, 1, 1);
        auto* input = reinterpret_cast<PdxV3ObjectsRequest*>(batch.data());
        PdxV3ObjectsInterface(nullptr, input);
        check(input->error == ERROR_NOT_READY && input->returned_count == 0
            && input->record_size == 104, "MarketGoodsDetails no game access with null Application");
    }
    auto formation = buffer(PDX_V3_MILITARY_FORMATION, PDX_V3_READ_OBJECT_IDS, 1, 1);
    auto* formation_input = reinterpret_cast<PdxV3ObjectsRequest*>(formation.data());
    PdxV3ObjectsInterface(nullptr, formation_input);
    check(formation_input->error == ERROR_NOT_READY && formation_input->returned_count == 0
        && formation_input->record_size == 40, "formation callback does not invent object or owner");
    auto war = buffer(PDX_V3_WAR, PDX_V3_READ_OBJECT_IDS, 1, 1);
    auto* war_input = reinterpret_cast<PdxV3ObjectsRequest*>(war.data());
    PdxV3ObjectsInterface(nullptr, war_input);
    check(war_input->error == ERROR_NOT_READY && war_input->returned_count == 0
        && war_input->record_size == 72, "War callback does not invent join or leader records");
    auto play = buffer(PDX_V3_DIPLOMATIC_PLAY, PDX_V3_READ_OBJECT_IDS, 1, 1);
    auto* play_input = reinterpret_cast<PdxV3ObjectsRequest*>(play.data());
    PdxV3ObjectsInterface(nullptr, play_input);
    check(play_input->error == ERROR_NOT_READY && play_input->returned_count == 0
        && play_input->record_size == 56, "DP callback does not invent native play or leaders");
    auto character = buffer(PDX_V3_CHARACTER, PDX_V3_READ_OBJECT_IDS, 1, 1);
    auto* character_input = reinterpret_cast<PdxV3ObjectsRequest*>(character.data());
    PdxV3ObjectsInterface(nullptr, character_input);
    check(character_input->error == ERROR_NOT_READY && character_input->returned_count == 0
        && character_input->record_size == 32, "Character callback does not invent a valid false field");
    check(sizeof(PdxV3DefinitionOptions) == 32 && sizeof(PdxV3LawDefinitionObject) == 72
        && PdxV3ObjectRecordSize(PDX_V3_LAW_DEFINITION) == 0
        && PdxV3ObjectOperationRecordSize(PDX_V3_LAW_DEFINITION, PDX_V3_ENUMERATE_DEFINITIONS) == 72,
        "Law definitions have explicit non-RefDB operation/layout");
    const unsigned char key_data[] = {0x6c, 0x61, 0x77, 0x5f, 0xe8, 0x87, 0xaa, 0xe7, 0x94, 0xb1};
    PdxV3DefinitionKeyView key_view{reinterpret_cast<uint64_t>(key_data), int32_t(sizeof(key_data)), 0, {0,0,0}};
    unsigned char copied_key[sizeof(key_data)];
    std::memset(copied_key, 0x55, sizeof(copied_key));
    uint32_t needed = 0;
    check(PdxV3CopyDefinitionKey(&key_view, copied_key, sizeof(copied_key) - 1, &needed) == ERROR_MORE_DATA
        && needed == sizeof(key_data) && copied_key[0] == 0x55 && copied_key[sizeof(copied_key)-1] == 0x55,
        "short definition key copies no partial UTF8");
    check(PdxV3CopyDefinitionKey(&key_view, copied_key, sizeof(copied_key), &needed) == 0
        && std::memcmp(copied_key, key_data, sizeof(key_data)) == 0, "definition key copies exact native UTF8 bytes");
    check(PdxV3CopyDefinitionKey(&key_view, nullptr, 0, &needed) == ERROR_MORE_DATA
        && needed == sizeof(key_data), "definition key size query remains explicit");
    PdxV3DefinitionKeyView empty_view{0, 0, 0, {0,0,0}};
    check(PdxV3CopyDefinitionKey(&empty_view, nullptr, 0, &needed) == 0 && needed == 0,
        "valid empty definition key is not an unavailable value");
    check(!PdxV3DefinitionTextFits(6, 8, 4) && PdxV3DefinitionTextFits(6, 10, 4)
        && !PdxV3DefinitionTextFits(10, 8, 0), "whole record text budget does not wrap or split keys");
    auto definitions = definition_buffer(PDX_V3_ENUMERATE_DEFINITIONS, 2, 0, 64);
    auto* definition_input = reinterpret_cast<PdxV3ObjectsRequest*>(definitions.data());
    PdxV3ObjectsInterface(nullptr, definition_input);
    check(definition_input->error == ERROR_NOT_READY && definition_input->returned_count == 0,
        "definition callback never enters a DB with null Application");
    auto* definition_options = reinterpret_cast<PdxV3DefinitionOptions*>(definitions.data() + definition_input->ids_offset);
    definition_options->text_offset = definition_input->records_offset;
    check(PdxV3ReadObjects(definition_input) == ERROR_INVALID_PARAMETER,
        "definition text arena overlap rejected before game access");
    check(PdxV3LawDefinitionDatabaseType(0x140000000ULL + 0x450db20, 0x140000000ULL, false)
        && PdxV3LawDefinitionDatabaseType(0x140000000ULL + 0x4425638, 0x140000000ULL, true),
        "specific native LawType and LawGroup producers establish typed receivers");
    check(!PdxV3LawDefinitionDatabaseType(0x140000000ULL + 0x450db20, 0x140000000ULL, true)
        && !PdxV3LawDefinitionDatabaseType(0x140000000ULL + 0x4425638, 0x140000000ULL, false)
        && !PdxV3LawDefinitionDatabaseType(0x140000000ULL + 0x44250c8, 0x140000000ULL, false)
        && !PdxV3LawDefinitionDatabaseType(0x140000000ULL + 0x4425c28, 0x140000000ULL, false),
        "another common-layout definition database cannot masquerade as Law");
    check(sizeof(PdxV3TechnologyDefinitionObject) == 64
        && PdxV3ObjectRecordSize(PDX_V3_TECHNOLOGY_DEFINITION) == 0
        && PdxV3ObjectOperationRecordSize(PDX_V3_TECHNOLOGY_DEFINITION, PDX_V3_ENUMERATE_DEFINITIONS) == 64,
        "Technology definition has its own explicit 64B non-RefDB ABI");
    check(PdxV3TechnologyDefinitionDatabaseType(0x140000000ULL + 0x44250c8, 0x140000000ULL)
        && !PdxV3TechnologyDefinitionDatabaseType(0x140000000ULL + 0x450db20, 0x140000000ULL),
        "Technology typed provider cannot be replaced by another definition DB");
    check(PdxV3TechnologyCategoryIndexInRange(0, 3) && PdxV3TechnologyCategoryIndexInRange(2, 3)
        && !PdxV3TechnologyCategoryIndexInRange(3, 3) && !PdxV3TechnologyCategoryIndexInRange(-1, 3)
        && !PdxV3TechnologyCategoryIndexInRange(0, -1), "category positive bounds precede unchecked native getter");
    auto tech_definitions = definition_buffer(PDX_V3_ENUMERATE_DEFINITIONS, 1, 0, 64, PDX_V3_TECHNOLOGY_DEFINITION);
    auto* technology_input = reinterpret_cast<PdxV3ObjectsRequest*>(tech_definitions.data());
    PdxV3ObjectsInterface(nullptr, technology_input);
    check(technology_input->error == ERROR_NOT_READY && technology_input->returned_count == 0
        && technology_input->record_size == 64, "Technology callback does not invent definitions or categories");
    check(PdxV3ObjectRecordSize(PDX_V3_INTEREST_GROUP) == 40
        && PdxV3ObjectOperationRecordSize(PDX_V3_INTEREST_GROUP, PDX_V3_READ_INTEREST_GROUP_DETAILS) == 56
        && PdxV3ObjectOperationRecordSize(PDX_V3_INTEREST_GROUP, PDX_V3_ENUMERATE_DEFINITIONS) == 0,
        "live IG40B and explicit name56B are not definition ordinals");
    check(PdxV3InterestGroupTypesMatch(0x140000000ULL, 0x140000000ULL + 0x44395d0, 0x140000000ULL + 0x44a4290)
        && !PdxV3InterestGroupTypesMatch(0x140000000ULL, 0x140000000ULL + 0x44250c8, 0x140000000ULL + 0x44a4290)
        && !PdxV3InterestGroupTypesMatch(0x140000000ULL, 0x140000000ULL + 0x44395d0, 0x140000000ULL + 0x44a09a0),
        "specific IG RTTI rejects Party and other common-layout DB receivers");
    PdxV3OwnedCString inline_name{{0, 0, 3, 15}};
    std::memcpy(inline_name.storage, "IG!", 3);
    PdxV3OwnedCStringBytes string_view{};
    check(PdxV3OwnedCStringView(&inline_name, &string_view) == 0
        && string_view.data == reinterpret_cast<uintptr_t>(inline_name.storage) && string_view.size == 3,
        "native CString SSO pure borrowed view respects exact32B layout");
    const unsigned char heap_name[] = "a-long-owned-native-name";
    PdxV3OwnedCString heap_string{{reinterpret_cast<uintptr_t>(heap_name), 0, sizeof(heap_name)-1, 31}};
    check(PdxV3OwnedCStringView(&heap_string, &string_view) == 0
        && string_view.data == reinterpret_cast<uintptr_t>(heap_name) && string_view.size == sizeof(heap_name)-1,
        "native CString heap view reads native length not strlen");
    inline_name.storage[2] = 16;
    check(PdxV3OwnedCStringView(&inline_name, &string_view) == ERROR_INVALID_DATA
        && !string_view.data && !string_view.size,
        "malformed native CString does not become an invented display name");
    auto ig_base = buffer(PDX_V3_INTEREST_GROUP, PDX_V3_READ_OBJECT_IDS, 1, 1);
    auto* ig_base_input = reinterpret_cast<PdxV3ObjectsRequest*>(ig_base.data());
    PdxV3ObjectsInterface(nullptr, ig_base_input);
    check(ig_base_input->error == ERROR_NOT_READY && !ig_base_input->returned_count,
        "IG callback with no Application fabricates no object or Country");
    auto ig_details = definition_buffer(PDX_V3_READ_INTEREST_GROUP_DETAILS, 1, 1, 64, PDX_V3_INTEREST_GROUP);
    auto* ig_detail_input = reinterpret_cast<PdxV3ObjectsRequest*>(ig_details.data());
    PdxV3ObjectsInterface(nullptr, ig_detail_input);
    check(ig_detail_input->error == ERROR_NOT_READY && !ig_detail_input->returned_count && ig_detail_input->record_size == 56,
        "IG name callback with no Application never invokes game localization or dtor");
    auto* ig_text = reinterpret_cast<PdxV3InterestGroupOptions*>(ig_details.data() + ig_detail_input->ids_offset);
    ig_text->text_offset = ig_detail_input->records_offset;
    check(PdxV3ReadObjects(ig_detail_input) == ERROR_INVALID_PARAMETER,
        "IG name arena overlap rejected before native scope or allocation");
    check(sizeof(PdxV3ShippingLaneObject) == 128
        && PdxV3ObjectRecordSize(PDX_V3_SHIPPING_LANE) == 128
        && PdxV3ObjectOperationRecordSize(PDX_V3_SHIPPING_LANE, PDX_V3_READ_OBJECT_IDS) == 128
        && PdxV3ObjectOperationRecordSize(PDX_V3_SHIPPING_LANE, PDX_V3_ENUMERATE_DEFINITIONS) == 0
        && PdxV3ObjectOperationRecordSize(PDX_V3_SHIPPING_LANE, PDX_V3_READ_INTEREST_GROUP_DETAILS) == 0,
        "real ShippingLane fullID128B does not borrow legacy TradeRoute or definition layout");
    check(PdxV3ShippingLaneTypesMatch(0x140000000ULL, 0x140000000ULL + 0x4439410, 0x140000000ULL + 0x4427d98)
        && !PdxV3ShippingLaneTypesMatch(0x140000000ULL, 0x140000000ULL + 0x44395d0, 0x140000000ULL + 0x4427d98)
        && !PdxV3ShippingLaneTypesMatch(0x140000000ULL, 0x140000000ULL + 0x4439410, 0x140000000ULL + 0x44a4290),
        "specific true Lane RefDB and native actor RTTI cannot be IG or another DB");
    check(PdxV3StateUsesMarketArea(1, 0) && PdxV3StateUsesMarketArea(1, 0xAB000003)
        && !PdxV3StateUsesMarketArea(0, 3) && !PdxV3StateUsesMarketArea(1, 0xFFFFFFFF),
        "effective State Market preserves original Area versus Country selector including IDzero");
    auto lane = buffer(PDX_V3_SHIPPING_LANE, PDX_V3_READ_OBJECT_IDS, 1, 1);
    auto* lane_input = reinterpret_cast<PdxV3ObjectsRequest*>(lane.data());
    PdxV3ObjectsInterface(nullptr, lane_input);
    check(lane_input->error == ERROR_NOT_READY && !lane_input->returned_count && lane_input->record_size == 128,
        "Lane callback without Application invents no Country State Market or quantity");
    check(sizeof(PdxV3CompanyObject) == 56 && sizeof(PdxV3CompanyDetails) == 72
        && PdxV3ObjectRecordSize(PDX_V3_COMPANY) == 56
        && PdxV3ObjectOperationRecordSize(PDX_V3_COMPANY, PDX_V3_READ_COMPANY_DETAILS) == 72
        && PdxV3ObjectOperationRecordSize(PDX_V3_COMPANY, PDX_V3_ENUMERATE_DEFINITIONS) == 0,
        "liveCompany56B and nativeName72B are not CompanyType definition lookup");
    check(PdxV3CompanyTypesMatch(0x140000000ULL, 0x140000000ULL + 0x44394f0, 0x140000000ULL + 0x449eac0)
        && !PdxV3CompanyTypesMatch(0x140000000ULL, 0x140000000ULL + 0x4439410, 0x140000000ULL + 0x449eac0)
        && !PdxV3CompanyTypesMatch(0x140000000ULL, 0x140000000ULL + 0x44394f0, 0x140000000ULL + 0x4507110),
        "typedCompany RefDB and liveactor cannot be Lane or staticCompanyType");
    check(PdxV3CompanyDefinitionType(0x140000000ULL, 0x140000000ULL + 0x4507110)
        && !PdxV3CompanyDefinitionType(0x140000000ULL, 0x140000000ULL + 0x449eac0),
        "CompanyType borroweddefinition identity never masquerades as liveCompany");
    auto company = buffer(PDX_V3_COMPANY, PDX_V3_READ_OBJECT_IDS, 1, 1);
    auto* company_input = reinterpret_cast<PdxV3ObjectsRequest*>(company.data());
    PdxV3ObjectsInterface(nullptr, company_input);
    check(company_input->error == ERROR_NOT_READY && !company_input->returned_count && company_input->record_size == 56,
        "Company callback with no Application does not invent instance Country or Type");
    auto company_names = definition_buffer(PDX_V3_READ_COMPANY_DETAILS, 1, 1, 64, PDX_V3_COMPANY);
    auto* company_names_input = reinterpret_cast<PdxV3ObjectsRequest*>(company_names.data());
    PdxV3ObjectsInterface(nullptr, company_names_input);
    check(company_names_input->error == ERROR_NOT_READY && !company_names_input->returned_count && company_names_input->record_size == 72,
        "CompanyName callback with no Application never constructs native CString");
    auto* company_options = reinterpret_cast<PdxV3CompanyOptions*>(company_names.data() + company_names_input->ids_offset);
    company_options->text_offset = company_names_input->records_offset;
    check(PdxV3ReadObjects(company_names_input) == ERROR_INVALID_PARAMETER,
        "CompanyName arena overlap rejected before game getters or allocations");
    check(sizeof(PdxV3ShipObject) == 88 && sizeof(PdxV3ShipDetails) == 104
        && PdxV3ObjectRecordSize(PDX_V3_SHIP) == 88
        && PdxV3ObjectOperationRecordSize(PDX_V3_SHIP, PDX_V3_READ_SHIP_DETAILS) == 104
        && PdxV3ObjectOperationRecordSize(PDX_V3_SHIP, PDX_V3_ENUMERATE_DEFINITIONS) == 0,
        "liveShip88B and originalNameNoFormatting104B explicit layout");
    check(PdxV3ShipTypesMatch(0x140000000ULL, 0x140000000ULL + 0x4439598, 0x140000000ULL + 0x44e1a10)
        && !PdxV3ShipTypesMatch(0x140000000ULL, 0x140000000ULL + 0x44394f0, 0x140000000ULL + 0x44e1a10)
        && !PdxV3ShipTypesMatch(0x140000000ULL, 0x140000000ULL + 0x4439598, 0x140000000ULL + 0x4513f88),
        "specificShip RefDB and actor cannot be Company or ShipType definition");
    check(PdxV3ShipTemplateTypesMatch(0x140000000ULL, 0x140000000ULL + 0x44398e0, 0x140000000ULL + 0x44e2030, true)
        && PdxV3ShipTemplateTypesMatch(0x140000000ULL, 0x140000000ULL + 0x4439448, 0x140000000ULL + 0x44e2048, false)
        && !PdxV3ShipTemplateTypesMatch(0x140000000ULL, 0x140000000ULL + 0x44398e0, 0x140000000ULL + 0x44e2030, false),
        "TemplateVersion and Template stay two separate actualRefDB domains");
    auto ship = buffer(PDX_V3_SHIP, PDX_V3_READ_OBJECT_IDS, 1, 1);
    auto* ship_input = reinterpret_cast<PdxV3ObjectsRequest*>(ship.data());
    PdxV3ObjectsInterface(nullptr, ship_input);
    check(ship_input->error == ERROR_NOT_READY && !ship_input->returned_count && ship_input->record_size == 88,
        "Ship callback with no Application invents no native relation");
    auto ship_names = definition_buffer(PDX_V3_READ_SHIP_DETAILS, 1, 1, 64, PDX_V3_SHIP);
    auto* ship_name_input = reinterpret_cast<PdxV3ObjectsRequest*>(ship_names.data());
    PdxV3ObjectsInterface(nullptr, ship_name_input);
    check(ship_name_input->error == ERROR_NOT_READY && !ship_name_input->returned_count && ship_name_input->record_size == 104,
        "ShipNameNoFormatting callback without Application constructs no native CString");
    auto* ship_text = reinterpret_cast<PdxV3ShipOptions*>(ship_names.data() + ship_name_input->ids_offset);
    ship_text->text_offset = ship_name_input->records_offset;
    check(PdxV3ReadObjects(ship_name_input) == ERROR_INVALID_PARAMETER,
        "ShipName textarena overlap rejected before native getter or allocation");
    PdxV3ObjectsInterface(nullptr, nullptr);
    return failures ? 1 : 0;
}
