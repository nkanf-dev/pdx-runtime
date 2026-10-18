#define NOMINMAX
#include "../v3_console.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstddef>
#include <climits>
#include <initializer_list>
#include <stdexcept>
#include <vector>
#ifdef NDEBUG
#error This host requires active runtime checks; do not define NDEBUG.
#endif

// Links the actual v3_console.cpp. No helper implementation is copied here.
namespace {
unsigned checks=0, cases=0;
const char* current_case="setup";
void check(bool value,const char* expression,int line) {
    ++checks;
    if (!value) {
        std::fprintf(stderr,"FAIL case=%s line=%d check=%s checks=%u\n",current_case,line,expression,checks);
        std::exit(1);
    }
}
#define CHECK(expression) check(!!(expression),#expression,__LINE__)
constexpr size_t pool_bytes=0x19e000, slot_stride=0x678;
constexpr uint32_t actor_ids[2]={0x03000007u,0xa500000cu};
constexpr uint32_t country_ids[2]={0x82000004u,0x05000005u};
constexpr uint32_t valid_all=PDX_V3_AI_CACHE_ACTOR_FIELDS|PDX_V3_AI_CACHE_ACTOR_IDENTITY
    |PDX_V3_AI_CACHE_OWNER_IDENTITY|PDX_V3_AI_CACHE_ROLE_VALUE|PDX_V3_AI_CACHE_PREDICATE;
constexpr uint32_t normal_flags=PDX_V3_AI_CACHE_CONTEXT_VALID|PDX_V3_AI_CACHE_HEADER_VALID
    |PDX_V3_AI_CACHE_TOTAL_KNOWN|PDX_V3_AI_CACHE_COMPLETE;
unsigned char application[0x620]={}, idler[0x30]={}, logic[0xb0]={}, world[0x180]={};
unsigned char *payload=nullptr,*pool=nullptr,*countries=nullptr,*cache_page=nullptr,*block_page=nullptr,*role_page=nullptr;
unsigned char *outside=nullptr,*fallback=nullptr,*fault_pages=nullptr;
unsigned char* actors[2]={};
struct Region { unsigned char* data;size_t size; };
std::vector<Region> guarded;
uint32_t live_actor_ids[2]={},live_country_ids[2]={};
uint32_t guard_depth=0,guard_enters=0,guard_leaves=0,guard_serial=0;
uint32_t actor_calls=0,country_calls=0,mutation_calls=0;
uint32_t throw_actor_call=0,throw_country_call=0;
bool actor_null=false,actor_alias=false,country_null=false,country_alias=false;
bool actor_access_fault=false,country_access_fault=false,hide_misaligned_pool_page=false;
uintptr_t base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> void put(void* object,size_t offset,T value) {
    std::memcpy(static_cast<unsigned char*>(object)+offset,&value,sizeof(value));
}
template<class T> T get(const void* object,size_t offset) {
    T value;std::memcpy(&value,static_cast<const unsigned char*>(object)+offset,sizeof(value));return value;
}
uintptr_t ptr(const void* object) { return reinterpret_cast<uintptr_t>(object); }
uintptr_t manager() { return ptr(payload)+0x2680; }
void protect(void* object,size_t size,DWORD permission) {
    DWORD previous=0;CHECK(VirtualProtect(object,size,permission,&previous)!=0);
}
void protect_sources(DWORD permission) {
    for (const auto& region:guarded) protect(region.data,region.size,permission);
}
unsigned char* allocate(size_t size,bool scope_protected=true) {
    auto* result=static_cast<unsigned char*>(VirtualAlloc(nullptr,size,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
    CHECK(result!=nullptr);
    if (scope_protected) guarded.push_back({result,size});
    return result;
}
void map_region(uintptr_t rva,DWORD flags=PAGE_READWRITE) {
    const auto address=(base()+rva)&~uintptr_t(0xffff);
    auto* result=VirtualAlloc(reinterpret_cast<void*>(address),0x10000,MEM_RESERVE|MEM_COMMIT,flags);
    CHECK(result || GetLastError()==ERROR_INVALID_ADDRESS);
}
void thunk(uintptr_t rva,void* target) {
    map_region(rva,PAGE_EXECUTE_READWRITE);
    auto* bytes=reinterpret_cast<unsigned char*>(base()+rva);
    const auto distance=static_cast<int64_t>(ptr(target))-static_cast<int64_t>(base()+rva+5);
    CHECK(distance>=INT32_MIN && distance<=INT32_MAX);
    const auto displacement=static_cast<int32_t>(distance);
    bytes[0]=0xe9;std::memcpy(bytes+1,&displacement,4);
    CHECK(FlushInstructionCache(GetCurrentProcess(),bytes,5)!=0);
}
void* __fastcall enter_guard(void* storage,uint32_t kind,int mode,const char*) {
    CHECK(guard_depth==0 && kind==1 && mode==0);
    guard_depth=1;++guard_enters;++guard_serial;
    protect_sources(PAGE_READONLY);
    if (hide_misaligned_pool_page) protect(pool+0x5000,0x1000,PAGE_NOACCESS);
    return storage;
}
void __fastcall leave_guard(void*) {
    CHECK(guard_depth==1);protect_sources(PAGE_NOACCESS);
    guard_depth=0;++guard_leaves;
}
void* __fastcall resolve_actor(const uint32_t* full_id) {
    CHECK(guard_depth==1 && guard_serial==guard_enters);++actor_calls;
    if (actor_access_fault && actor_calls==2) {
        const volatile uint32_t native_read=*reinterpret_cast<volatile uint32_t*>(outside);(void)native_read;
    }
    if (throw_actor_call==actor_calls) throw std::runtime_error("actor resolver fixture");
    if (actor_null) return nullptr;
    if (actor_alias) return actors[1];
    for (unsigned i=0;i<2;++i)
        if (*full_id==live_actor_ids[i] && get<uint32_t>(actors[i],8)==*full_id) return actors[i];
    return fallback;
}
void* __fastcall resolve_country(const uint32_t* full_id) {
    CHECK(guard_depth==1 && guard_serial==guard_enters);++country_calls;
    if (country_access_fault && country_calls==2) {
        const volatile uint32_t native_read=*reinterpret_cast<volatile uint32_t*>(outside);(void)native_read;
    }
    if (throw_country_call==country_calls) throw std::runtime_error("owner resolver fixture");
    if (country_null) return nullptr;
    if (country_alias) return countries+0x1000;
    for (unsigned i=0;i<2;++i) {
        auto* country=countries+i*0x1000;
        if (*full_id==live_country_ids[i] && get<uint32_t>(country,0x18)==*full_id) return country;
    }
    return outside;
}
void __fastcall mutation_poison() {
    ++mutation_calls;throw std::runtime_error("query called create/append/update/dispatch");
}
void setup() {
    payload=allocate(0x3000);pool=allocate(pool_bytes);countries=allocate(0x2000);
    cache_page=allocate(0x1000);block_page=allocate(0x1000);role_page=allocate(0x1000);
    outside=allocate(0x1000,false);fallback=allocate(0x1000,false);fault_pages=allocate(0x2000,false);
    protect(outside,0x1000,PAGE_NOACCESS);protect(fallback,0x1000,PAGE_NOACCESS);
    protect(fault_pages+0x1000,0x1000,PAGE_NOACCESS);
    guarded.push_back({fault_pages,0x1000});
    for (uintptr_t rva:{0x592d128u,0x5964e20u,0x5967270u}) map_region(rva);
    thunk(0x33bd640,reinterpret_cast<void*>(&enter_guard));thunk(0x33bd750,reinterpret_cast<void*>(&leave_guard));
    thunk(0xc5a4b0,reinterpret_cast<void*>(&resolve_actor));thunk(0x7bb480,reinterpret_cast<void*>(&resolve_country));
    for (uintptr_t rva:{0x1514810u,0x63e260u,0x1513a40u,0x3af1740u})
        thunk(rva,reinterpret_cast<void*>(&mutation_poison));
}
void reset(const char* name) {
    current_case=name;++cases;CHECK(guard_depth==0 && guard_enters==guard_leaves);
    protect_sources(PAGE_READWRITE);
    std::memset(application,0,sizeof(application));std::memset(idler,0,sizeof(idler));
    std::memset(logic,0,sizeof(logic));std::memset(world,0,sizeof(world));
    for (const auto& region:guarded) std::memset(region.data,0,region.size);
    actor_calls=country_calls=mutation_calls=throw_actor_call=throw_country_call=0;
    actor_null=actor_alias=country_null=country_alias=false;actor_access_fault=country_access_fault=hide_misaligned_pool_page=false;
    actors[0]=pool+7*slot_stride;actors[1]=pool+12*slot_stride;
    put(reinterpret_cast<void*>(base()),0x592d128,ptr(application));
    put(reinterpret_cast<void*>(base()),0x592d0e0,ptr(idler));
    put(reinterpret_cast<void*>(base()),0x5964e20,ptr(payload)+0x10);
    put(reinterpret_cast<void*>(base()),0x5964e18,ptr(outside));
    put(reinterpret_cast<void*>(base()),0x5967270,manager()+0x10);
    put(reinterpret_cast<void*>(base()),0x59646e8,ptr(fallback));
    put(application,0x1c0,ptr(idler));put(application,0x608,ptr(world));
    put(idler,0,base()+0x4431500);put(idler,8,ptr(logic));put(idler,0x18,ptr(application));put(idler,0x20,uint8_t(1));
    put(logic,0,base()+0x4436a08);put(logic,0xa8,uint8_t(1));
    put(world,0,base()+0x443a438);put(world,0x120,ptr(payload));put(world,0xe0,ptr(role_page));put(world,0xec,uint32_t(8));
    put(payload,0,base()+0x44adfc8);put(payload,0x2680,base()+0x44f4dd8);
    // Actual D=M+10, G=M+18. G stores block array, signed capacity and count.
    put(payload,0x2680+0x18,ptr(block_page));put(payload,0x2680+0x20,int32_t(1));put(payload,0x2680+0x24,int32_t(1));
    put(block_page,0,ptr(pool));
    put(payload,0x2680+0x68,ptr(cache_page));put(payload,0x2680+0x70,int32_t(4));put(payload,0x2680+0x74,int32_t(3));
    put(cache_page,0,ptr(actors[0]));put(cache_page,8,ptr(actors[1]));put(cache_page,16,ptr(actors[0]));
    for (unsigned i=0;i<2;++i) {
        live_actor_ids[i]=actor_ids[i];live_country_ids[i]=country_ids[i];
        auto* country=countries+i*0x1000;
        put(country,8,base()+0x4494d08);put(country,0x18,country_ids[i]);put(country,0x20,actor_ids[i]);
        put(actors[i],0,base()+0x44f0930);put(actors[i],8,actor_ids[i]);put(actors[i],0x50,country_ids[i]);
        put(actors[i],0x140,uint8_t(i==0?2:1));put(actors[i],0x141,uint8_t(i==0?0xa7:3));
    }
    put(role_page,4*5,uint32_t(9));
}
std::vector<unsigned char> source_copy() {
    protect_sources(PAGE_READWRITE);std::vector<unsigned char> result;
    for (const auto& region:guarded) result.insert(result.end(),region.data,region.data+region.size);
    for (const auto region:{Region{application,sizeof(application)},Region{idler,sizeof(idler)},Region{logic,sizeof(logic)},Region{world,sizeof(world)}})
        result.insert(result.end(),region.data,region.data+region.size);
    for (uintptr_t rva:{0x592d128u,0x592d0e0u,0x5964e20u,0x5964e18u,0x5967270u,0x59646e8u}) {
        const auto* start=reinterpret_cast<const unsigned char*>(base()+rva);
        result.insert(result.end(),start,start+8);
    }
    protect_sources(PAGE_NOACCESS);return result;
}
struct Wire {
    PdxV3ConsoleAiCacheSnapshot header;
    PdxV3ConsoleAiCacheRow rows[4];
    uint64_t canary;
};
Wire request(uint32_t capacity=4) {
    Wire wire;std::memset(&wire,0xa5,sizeof(wire));wire.header={};
    wire.header.size=static_cast<uint32_t>(sizeof(wire.header)+uint64_t(capacity)*sizeof(wire.rows[0]));
    wire.header.row_capacity=capacity;wire.header.row_bytes=sizeof(wire.rows[0]);wire.header.error=ERROR_IO_PENDING;
    wire.canary=0x1122334455667788ull;return wire;
}
DWORD invoke(Wire& wire,bool direct=false,void* app=application,bool one_scope=true) {
    const auto before=source_copy();const auto enters=guard_enters,leaves=guard_leaves;
    DWORD status=ERROR_SUCCESS;
    if (direct) status=PdxV3ConsoleReadAiCache(ptr(app),&wire.header);
    else { PdxV3ConsoleAiCacheInterface(app,&wire.header);status=wire.header.error; }
    CHECK(guard_depth==0 && guard_enters==guard_leaves);
    CHECK(guard_enters==enters+(one_scope?1u:0u) && guard_leaves==leaves+(one_scope?1u:0u));
    CHECK(mutation_calls==0 && source_copy()==before);CHECK(wire.canary==0x1122334455667788ull);
    CHECK(wire.header.row_count<=4 && wire.header.row_count<=wire.header.row_capacity);
    return status;
}
Wire query(uint32_t capacity=4,bool direct=false) {
    auto wire=request(capacity);invoke(wire,direct);return wire;
}
void complete(const Wire& wire,uint32_t total=3) {
    CHECK(wire.header.error==ERROR_SUCCESS && wire.header.row_count==total && wire.header.native_total==static_cast<int32_t>(total));
    CHECK((wire.header.flags&normal_flags)==normal_flags && !(wire.header.flags&(PDX_V3_AI_CACHE_TRUNCATED|PDX_V3_AI_CACHE_PREFIX_FAULT)));
    CHECK(wire.header.required_bytes==sizeof(wire.header)+uint64_t(total)*sizeof(wire.rows[0]));
    CHECK(wire.header.returned_bytes==sizeof(wire.header)+total*sizeof(wire.rows[0]));
    CHECK(wire.header.world==ptr(world) && wire.header.application==ptr(application) && wire.header.execution_thread_id!=0);
}
void row_error(const Wire& wire,uint32_t index) {
    complete(wire);CHECK(wire.header.flags&PDX_V3_AI_CACHE_ROW_ERRORS);CHECK(wire.header.row_error_count!=0);
    CHECK(wire.rows[index].cache_index==index);
    CHECK(wire.rows[index].actor_error || wire.rows[index].owner_error || wire.rows[index].link_error || wire.rows[index].role_error);
}
void normal_and_capacity() {
    reset("ordered duplicates, raw bytes, full IDs, continuous scope");auto wire=query();complete(wire);
    CHECK(wire.header.flags==normal_flags && wire.header.row_error_count==0 && wire.header.manager_error==0);
    CHECK(wire.header.paused==1 && wire.header.simulation_started==1 && wire.header.role_count==8);
    CHECK(actor_calls==3 && country_calls==3);
    for (unsigned i=0;i<3;++i) {
        const unsigned member=i==1?1:0;const auto& row=wire.rows[i];
        CHECK(row.cache_index==i && row.valid_fields==valid_all && row.actor_full_id==actor_ids[member]);
        CHECK(row.owner_country_full_id==country_ids[member] && row.actor_error==0 && row.owner_error==0 && row.link_error==0 && row.role_error==0);
        CHECK(row.actor_enabled_raw==(member==0?2u:1u) && row.actor_141_raw==(member==0?0xa7u:3u));
        CHECK(row.role_index==(country_ids[member]&0xffffff) && row.role_raw==(member==0?0u:9u));
        CHECK(row.observed_predicate==(member==0?1u:0u) && row.reserved0==0 && row.reserved1==0 && row.reserved2==0);
    }
    CHECK(!std::memcmp(&wire.rows[0].valid_fields,&wire.rows[2].valid_fields,sizeof(wire.rows[0])-sizeof(uint32_t)));
    reset("direct exported helper");wire=query(4,true);complete(wire);
    for (uint32_t capacity:{0u,1u,2u}) {
        reset("capacity-short whole prefix");wire=query(capacity);
        CHECK(wire.header.error==ERROR_MORE_DATA && wire.header.row_count==capacity && wire.header.native_total==3);
        CHECK(wire.header.flags==(PDX_V3_AI_CACHE_CONTEXT_VALID|PDX_V3_AI_CACHE_HEADER_VALID|PDX_V3_AI_CACHE_TOTAL_KNOWN|PDX_V3_AI_CACHE_TRUNCATED));
        CHECK(wire.header.required_bytes==288 && wire.header.returned_bytes==96+capacity*64);
        CHECK(actor_calls==capacity && country_calls==capacity);
        for (uint32_t i=0;i<capacity;++i) CHECK(wire.rows[i].cache_index==i && wire.rows[i].valid_fields==valid_all);
        const auto* tail=reinterpret_cast<const unsigned char*>(&wire.rows[capacity]);
        for (size_t i=0;i<(4-capacity)*64;++i) CHECK(tail[i]==0xa5);
    }
}
void provenance_cases() {
    for (uintptr_t bad:{uintptr_t(0),ptr(fallback),ptr(outside),ptr(outside)+1}) {
        reset("invalid cached pointer rejected before actor read");put(cache_page,0,bad);auto wire=query();row_error(wire,0);
        CHECK(wire.rows[0].actor_error!=0 && !(wire.rows[0].valid_fields&PDX_V3_AI_CACHE_ACTOR_FIELDS));
        CHECK(actor_calls==2 && country_calls==2);
    }
    reset("in-pool misaligned address rejected before actor read");put(cache_page,0,ptr(actors[0])+1);auto wire=query();row_error(wire,0);
    CHECK(wire.rows[0].actor_error==ERROR_INVALID_DATA && !(wire.rows[0].valid_fields&PDX_V3_AI_CACHE_ACTOR_FIELDS) && actor_calls==2);
    reset("in-pool misaligned inaccessible page proves no actor read");
    hide_misaligned_pool_page=true;put(cache_page,0,ptr(pool)+0x5001);wire=query();row_error(wire,0);
    CHECK(wire.rows[0].actor_error==ERROR_INVALID_DATA && wire.rows[0].valid_fields==0 && actor_calls==2 && country_calls==2);
    reset("stale generation full-ID resolver");put(actors[0],8,uint32_t(0x04000007));wire=query();row_error(wire,0);
    CHECK(wire.rows[0].actor_full_id==0x04000007 && wire.rows[0].actor_error==ERROR_NOT_FOUND);
    CHECK(!(wire.rows[0].valid_fields&PDX_V3_AI_CACHE_ACTOR_IDENTITY) && wire.header.row_error_count==2);
    reset("resolver pointer mismatch retained");actor_alias=true;wire=query();row_error(wire,0);
    CHECK(wire.rows[0].actor_error!=0 && !(wire.rows[0].valid_fields&PDX_V3_AI_CACHE_ACTOR_IDENTITY));
    reset("native actor resolver null retained");actor_null=true;wire=query();row_error(wire,0);CHECK(wire.rows[0].actor_error==ERROR_NOT_FOUND);
    reset("wrong actor vtable");put(actors[0],0,uint64_t(0));wire=query();row_error(wire,0);
    CHECK(wire.rows[0].actor_error==ERROR_INVALID_DATA && !(wire.rows[0].valid_fields&PDX_V3_AI_CACHE_ACTOR_IDENTITY));
    reset("actual erased dead slot: zero vptr and old ID OR 00ffffff");
    std::memset(actors[0],0,slot_stride);put(actors[0],8,actor_ids[0]|0x00ffffffu);wire=query();row_error(wire,0);
    CHECK(wire.rows[0].actor_error==ERROR_INVALID_DATA && wire.rows[0].valid_fields==0 && actor_calls==1);
    reset("typed slot with invalid full ID sentinel");put(actors[0],8,uint32_t(0xffffffff));wire=query();row_error(wire,0);
    CHECK(wire.rows[0].actor_error==ERROR_NOT_FOUND && wire.rows[0].actor_full_id==0xffffffff && actor_calls==1);
    reset("slot index and full ID mismatch");put(actors[0],8,uint32_t(0x03000006));wire=query();row_error(wire,0);
    CHECK(wire.rows[0].actor_error!=0 && !(wire.rows[0].valid_fields&PDX_V3_AI_CACHE_ACTOR_IDENTITY));
}
void owner_and_roles() {
    reset("owner full-ID stale generation");put(actors[0],0x50,uint32_t(0x81000004));auto wire=query();row_error(wire,0);
    CHECK(wire.rows[0].owner_country_full_id==0x81000004 && wire.rows[0].owner_error==ERROR_NOT_FOUND);
    CHECK((wire.rows[0].valid_fields&PDX_V3_AI_CACHE_ACTOR_IDENTITY) && !(wire.rows[0].valid_fields&PDX_V3_AI_CACHE_OWNER_IDENTITY));
    reset("owner invalid ID sentinel");put(actors[0],0x50,uint32_t(0xffffffff));wire=query();row_error(wire,0);CHECK(wire.rows[0].owner_error==ERROR_NOT_FOUND && country_calls==1);
    reset("owner resolver null");country_null=true;wire=query();row_error(wire,0);CHECK(wire.rows[0].owner_error==ERROR_NOT_FOUND);
    reset("owner resolver alias full ID");country_alias=true;wire=query();row_error(wire,0);CHECK(wire.rows[0].owner_error==ERROR_NOT_FOUND);
    reset("owner wrong playable vtable at +8");put(countries,8,uint64_t(0));wire=query();row_error(wire,0);CHECK(wire.rows[0].owner_error==ERROR_INVALID_DATA);
    reset("separate country actor cross-link");put(countries,0x20,uint32_t(0x03000006));wire=query();row_error(wire,0);
    CHECK(wire.rows[0].actor_error==0 && wire.rows[0].owner_error==0 && wire.rows[0].link_error==ERROR_INVALID_DATA);
    CHECK(wire.rows[0].valid_fields&PDX_V3_AI_CACHE_OWNER_IDENTITY);
    for (uint8_t override_raw:{uint8_t(0),uint8_t(3)}) {
        reset("role out of bounds, native override gate");put(world,0xec,uint32_t(4));put(payload,0x28e8,override_raw);wire=query();row_error(wire,0);
        CHECK(wire.rows[0].role_error==ERROR_NOT_FOUND && wire.rows[0].role_raw==0xffffffff);
        CHECK(!(wire.rows[0].valid_fields&PDX_V3_AI_CACHE_ROLE_VALUE) && (wire.rows[0].valid_fields&PDX_V3_AI_CACHE_PREDICATE));
        CHECK(wire.rows[0].observed_predicate==(override_raw?1u:0u) && wire.header.manager_override_raw==override_raw);
    }
    reset("role null in range is not zero");put(world,0xe0,uint64_t(0));wire=query();row_error(wire,0);
    CHECK(wire.rows[0].role_error==ERROR_INVALID_DATA && !(wire.rows[0].valid_fields&PDX_V3_AI_CACHE_PREDICATE));
    reset("disabled actor with nonzero override");put(actors[0],0x140,uint8_t(0));put(payload,0x28e8,uint8_t(2));wire=query();complete(wire);
    CHECK(wire.rows[0].actor_enabled_raw==0 && wire.rows[0].actor_141_raw==0xa7 && wire.rows[0].observed_predicate==0);
    reset("zero actor and zero owner full IDs, real slot zero");
    actors[0]=pool;live_actor_ids[0]=0;live_country_ids[0]=0;
    put(actors[0],0,base()+0x44f0930);put(actors[0],8,uint32_t(0));put(actors[0],0x50,uint32_t(0));
    put(actors[0],0x140,uint8_t(2));put(actors[0],0x141,uint8_t(0xfe));
    put(countries,0x18,uint32_t(0));put(countries,0x20,uint32_t(0));put(cache_page,0,ptr(actors[0]));put(cache_page,16,ptr(actors[0]));
    wire=query();complete(wire);CHECK(wire.rows[0].actor_full_id==0 && wire.rows[0].owner_country_full_id==0 && wire.rows[0].role_index==0);
    CHECK(wire.rows[0].valid_fields==valid_all && wire.rows[0].actor_enabled_raw==2 && wire.rows[0].actor_141_raw==0xfe);
}
void metadata_and_context() {
    for (int32_t capacity:{0,1}) {
        reset("empty cache valid including zero capacity");put(payload,0x26f0,capacity);put(payload,0x26f4,int32_t(0));put(payload,0x26e8,uint64_t(0));
        auto wire=query(0);complete(wire,0);CHECK(wire.header.flags==normal_flags && actor_calls==0 && country_calls==0);
    }
    struct BadMetadata { size_t offset;int32_t value; };
    for (const auto bad:{BadMetadata{0x26f0,-1},BadMetadata{0x26f4,-1},BadMetadata{0x26f0,2},BadMetadata{0x26a0,-1},BadMetadata{0x26a4,-1},BadMetadata{0x26a0,0},BadMetadata{0x26a4,2}}) {
        reset("negative or count-greater-than-capacity metadata");put(payload,bad.offset,bad.value);auto wire=query();
        CHECK(wire.header.error==ERROR_INVALID_DATA && wire.header.row_count==0 && actor_calls==0 && country_calls==0);
        CHECK(!(wire.header.flags&PDX_V3_AI_CACHE_COMPLETE));
        if (bad.offset==0x26f0 || bad.offset==0x26f4) {
            CHECK(!(wire.header.flags&PDX_V3_AI_CACHE_HEADER_VALID) && wire.header.required_bytes==0);
            CHECK(wire.header.flags&PDX_V3_AI_CACHE_TOTAL_KNOWN);
            CHECK(wire.header.native_total==(bad.offset==0x26f4?bad.value:3));
        } else CHECK((wire.header.flags&PDX_V3_AI_CACHE_HEADER_VALID) && wire.header.required_bytes==288);
    }
    for (size_t offset:{size_t(0x26e8),size_t(0x2698),size_t(0)}) {
        reset("nonempty null cache/block metadata");if (offset) put(payload,offset,uint64_t(0));else put(block_page,0,uint64_t(0));auto wire=query();
        CHECK(wire.header.error==ERROR_INVALID_DATA && wire.header.row_count==0 && actor_calls==0);
    }
    for (unsigned overflow=0;overflow<3;++overflow) {
        reset("cache/block address arithmetic overflow before dereference");
        if (overflow==0) put(payload,0x26e8,~uintptr_t(0)-16);
        else if (overflow==1) put(payload,0x2698,~uintptr_t(0)-4);
        else put(block_page,0,~uintptr_t(0)-100);
        auto wire=query();CHECK(wire.header.error==ERROR_INVALID_DATA && wire.header.row_count==0 && actor_calls==0 && country_calls==0);
        if (overflow==0) CHECK(!(wire.header.flags&PDX_V3_AI_CACHE_HEADER_VALID) && wire.header.required_bytes==0);
        else CHECK((wire.header.flags&PDX_V3_AI_CACHE_HEADER_VALID) && wire.header.required_bytes==288);
    }
    for (uintptr_t root:{uintptr_t(0x5967270),uintptr_t(0x5964e20)}) {
        reset("root database must equal current payload inline database");put(reinterpret_cast<void*>(base()),root,uintptr_t(0x1111));auto wire=query();
        CHECK(wire.header.error==ERROR_NOT_READY && wire.header.row_count==0 && actor_calls==0 && country_calls==0);
    }
    reset("expected current world mismatch");auto wire=request();wire.header.expected_world=ptr(world)+8;invoke(wire);
    CHECK(wire.header.error==ERROR_NOT_READY && wire.header.row_count==0 && actor_calls==0);
    reset("expected current world match");wire=request();wire.header.expected_world=ptr(world);invoke(wire);complete(wire);CHECK(wire.header.expected_world==ptr(world));
    reset("current application mismatch");wire=request();invoke(wire,false,world);CHECK(wire.header.error==ERROR_NOT_READY && wire.header.row_count==0);
    reset("closing current application");put(application,0x258,uint8_t(1));wire=query();CHECK(wire.header.error==ERROR_NOT_READY && wire.header.row_count==0);
    reset("current world unavailable");put(application,0x608,uint64_t(0));wire=query();CHECK(wire.header.error==ERROR_NOT_READY && wire.header.row_count==0);
    reset("wrong current world vtable");put(world,0,uint64_t(0));wire=query();CHECK(wire.header.error==ERROR_INVALID_DATA && wire.header.row_count==0);
    reset("wrong payload manager vtable");put(payload,0,base()+0x443a438);wire=query();CHECK(wire.header.error==ERROR_INVALID_DATA && wire.header.row_count==0);
    reset("wrong AI manager vtable");put(payload,0x2680,uint64_t(0));wire=query();CHECK(wire.header.error==ERROR_INVALID_DATA && wire.header.row_count==0);
}
void request_validation() {
    for (unsigned kind=0;kind<6;++kind) {
        reset("request validation before guard");auto wire=request();
        switch (kind) {
        case 0:wire.header.size=95;break;
        case 1:wire.header.size-=1;break;
        case 2:wire.header.size+=1;break;
        case 3:wire.header.reserved=1;break;
        case 4:wire.header.row_bytes=63;break;
        case 5:wire.header.row_capacity=UINT32_MAX;wire.header.size=UINT32_MAX;break;
        }
        CHECK(invoke(wire,true,application,false)==ERROR_INVALID_PARAMETER);
        CHECK(wire.header.error==ERROR_INVALID_PARAMETER && wire.header.native_total==-1);
        CHECK(wire.header.required_bytes==0 && wire.header.returned_bytes==96 && wire.header.flags==0);
        CHECK(wire.header.row_count==0 && wire.header.row_error_count==0 && wire.header.reserved==0);
        CHECK(actor_calls==0 && country_calls==0);
    }
    reset("null request pointers");const auto enters=guard_enters;
    CHECK(PdxV3ConsoleReadAiCache(ptr(application),nullptr)==ERROR_INVALID_PARAMETER);
    PdxV3ConsoleAiCacheInterface(application,nullptr);CHECK(guard_enters==enters);
    reset("reused caller allocation overwrites old rows");auto wire=query();complete(wire);
    protect_sources(PAGE_READWRITE);put(cache_page,0,uint64_t(0));invoke(wire);row_error(wire,0);
    CHECK(wire.rows[0].valid_fields==0 && wire.rows[0].actor_enabled_raw==0 && wire.rows[0].observed_predicate==0);
}
void fault_prefixes() {
    reset("C++ actor resolver throw574 retains whole prefix and leaves guard");throw_actor_call=2;auto wire=query();
    CHECK(wire.header.error==ERROR_UNHANDLED_EXCEPTION && wire.header.row_count==2 && wire.header.native_total==3);
    CHECK(wire.header.flags&PDX_V3_AI_CACHE_PREFIX_FAULT);
    CHECK(!(wire.header.flags&(PDX_V3_AI_CACHE_COMPLETE|PDX_V3_AI_CACHE_TRUNCATED)));
    CHECK(wire.header.returned_bytes==224 && wire.header.required_bytes==288 && wire.rows[0].valid_fields==valid_all);
    CHECK(wire.rows[1].cache_index==1 && wire.rows[1].actor_error==ERROR_UNHANDLED_EXCEPTION);
    CHECK(wire.rows[1].valid_fields==PDX_V3_AI_CACHE_ACTOR_FIELDS && wire.header.row_error_count==1);
    const auto* tail=reinterpret_cast<const unsigned char*>(&wire.rows[2]);for (size_t i=0;i<2*64;++i) CHECK(tail[i]==0xa5);
    reset("C++ owner resolver throw574 whole prefix");throw_country_call=2;wire=query();
    CHECK(wire.header.error==ERROR_UNHANDLED_EXCEPTION && wire.header.row_count==2 && wire.header.returned_bytes==224);
    CHECK(wire.rows[1].owner_error==ERROR_UNHANDLED_EXCEPTION);
    CHECK(wire.rows[1].valid_fields==(PDX_V3_AI_CACHE_ACTOR_FIELDS|PDX_V3_AI_CACHE_ACTOR_IDENTITY));
    CHECK(wire.header.flags&PDX_V3_AI_CACHE_PREFIX_FAULT);
    for (bool actor_fault:{true,false}) {
        reset("native resolver access violation998 is not C++574");
        actor_access_fault=actor_fault;country_access_fault=!actor_fault;wire=query();
        CHECK(wire.header.error==ERROR_NOACCESS && wire.header.row_count==2 && wire.header.returned_bytes==224);
        CHECK(wire.header.native_total==3 && wire.header.required_bytes==288 && wire.header.row_error_count==1);
        CHECK((wire.header.flags&PDX_V3_AI_CACHE_PREFIX_FAULT) && !(wire.header.flags&(PDX_V3_AI_CACHE_COMPLETE|PDX_V3_AI_CACHE_TRUNCATED)));
        CHECK(wire.rows[0].valid_fields==valid_all && wire.rows[1].cache_index==1);
        CHECK((actor_fault?wire.rows[1].actor_error:wire.rows[1].owner_error)==ERROR_NOACCESS);
        CHECK(wire.rows[1].valid_fields==(actor_fault?uint32_t(PDX_V3_AI_CACHE_ACTOR_FIELDS):uint32_t(PDX_V3_AI_CACHE_ACTOR_FIELDS|PDX_V3_AI_CACHE_ACTOR_IDENTITY)));
        tail=reinterpret_cast<const unsigned char*>(&wire.rows[2]);for (size_t i=0;i<2*64;++i) CHECK(tail[i]==0xa5);
    }
    reset("unexpected cache array read fault998 after known whole row");
    put(payload,0x26e8,ptr(fault_pages)+0xff8);put(fault_pages,0xff8,ptr(actors[0]));wire=query();
    CHECK(wire.header.error==ERROR_NOACCESS && wire.header.row_count==2 && wire.header.native_total==3);
    CHECK(wire.header.flags&PDX_V3_AI_CACHE_PREFIX_FAULT);
    CHECK(!(wire.header.flags&(PDX_V3_AI_CACHE_COMPLETE|PDX_V3_AI_CACHE_TRUNCATED)));
    CHECK(wire.header.returned_bytes==224 && wire.header.required_bytes==288 && wire.rows[0].valid_fields==valid_all);
    CHECK(wire.rows[1].cache_index==1 && wire.rows[1].valid_fields==0 && wire.rows[1].actor_error==ERROR_NOACCESS);
    CHECK(wire.header.row_error_count==1);
    tail=reinterpret_cast<const unsigned char*>(&wire.rows[2]);for (size_t i=0;i<2*64;++i) CHECK(tail[i]==0xa5);
}
}
int main(int argc,char** argv) {
    if (argc==2 && !std::strcmp(argv[1],"--fail-check-control")) {
        current_case="intentional CHECK failure control";CHECK(false);
    }
    static_assert(sizeof(PdxV3ConsoleRequest)==96 && sizeof(PdxV3ConsolePlayerState)==160 && sizeof(PdxV3ConsoleAiState)==144);
    static_assert(sizeof(PdxV3ConsoleAiCacheSnapshot)==96 && sizeof(PdxV3ConsoleAiCacheRow)==64);
    static_assert(offsetof(PdxV3ConsoleAiCacheSnapshot,expected_world)==72 && offsetof(PdxV3ConsoleAiCacheSnapshot,application)==88);
    static_assert(slot_stride*1024==pool_bytes);
    setup();normal_and_capacity();provenance_cases();owner_and_roles();metadata_and_context();request_validation();fault_prefixes();
    CHECK(guard_depth==0 && guard_enters==guard_leaves && mutation_calls==0);
    protect_sources(PAGE_READWRITE);
    for (const auto& region:guarded) CHECK(VirtualFree(region.data,0,MEM_RELEASE)!=0);
    CHECK(VirtualFree(outside,0,MEM_RELEASE)!=0 && VirtualFree(fallback,0,MEM_RELEASE)!=0);
    // fault_pages first page was freed above; it is one allocation, not two frees.
    std::printf("PASS v3_ai_cache_host: cases=%u checks=%u guards=%u/%u; actual Console96/Player160/Ai144 ABI; cache96+row64; real 1024*0x678 pool; ordered duplicate membership; scope-protected readonly sources; full IDs/owner/link; retained row errors; exact whole prefixes234/574/998; no create/append/update/dispatch or query writes\n",cases,checks,guard_enters,guard_leaves);
    return 0;
}
