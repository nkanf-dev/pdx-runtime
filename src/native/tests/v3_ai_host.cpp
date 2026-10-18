#define NOMINMAX
#include "../v3_console.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <stdexcept>

namespace {
unsigned char application[0x620]={}, idler[0x30]={}, logic[0xb0]={};
unsigned char world[0x180]={}, country_manager[0x3000]={};
unsigned char country[0xd00]={}, country_fallback[0xd00]={};
unsigned char actor[0x280]={}, actor_fallback[0x280]={};
uint32_t roles[8]={};
constexpr uint32_t country_id=0x02000004,actor_id=0x03000007;
uint32_t guard_depth=0, guard_enters=0, guard_leaves=0, country_calls=0, actor_calls=0, mutation_calls=0;
bool null_country=false, alias_country=false, null_actor=false, fallback_actor=false;
bool throw_country=false,throw_actor=false;
uintptr_t base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> void set(void* object,size_t offset,T value) { *reinterpret_cast<T*>(static_cast<unsigned char*>(object)+offset)=value; }
void map_region(uintptr_t rva,DWORD flags=PAGE_READWRITE) {
    const auto address=(base()+rva)&~uintptr_t(0xffff);
    void* memory=VirtualAlloc(reinterpret_cast<void*>(address),0x10000,MEM_RESERVE|MEM_COMMIT,flags);
    assert(memory || GetLastError()==ERROR_INVALID_ADDRESS);
}
void thunk(uintptr_t rva,void* target) {
    map_region(rva,PAGE_EXECUTE_READWRITE);
    auto* bytes=reinterpret_cast<unsigned char*>(base()+rva);
    const auto difference=static_cast<int64_t>(reinterpret_cast<uintptr_t>(target))-static_cast<int64_t>(base()+rva+5);
    assert(difference>=INT32_MIN && difference<=INT32_MAX);
    const auto displacement=static_cast<int32_t>(difference);
    bytes[0]=0xe9;std::memcpy(bytes+1,&displacement,4);
    FlushInstructionCache(GetCurrentProcess(),bytes,5);
}
void* __fastcall enter_guard(void* storage,uint32_t kind,int mode,const char*) {
    assert(guard_depth==0 && kind==1 && mode==0);
    guard_depth=1;++guard_enters;return storage;
}
void __fastcall leave_guard(void*) { assert(guard_depth==1);guard_depth=0;++guard_leaves; }
void* __fastcall resolve_country(const uint32_t* id) {
    assert(guard_depth==1);++country_calls;
    if (throw_country) throw std::runtime_error("Country resolver fixture");
    if (null_country) return nullptr;
    if (alias_country || *id==country_id) return country;
    return country_fallback;
}
void* __fastcall resolve_actor(const uint32_t* id) {
    assert(guard_depth==1 && *id==actor_id && *id!=country_id);++actor_calls;
    if (throw_actor) throw std::runtime_error("actor resolver fixture");
    if (null_actor) return nullptr;
    if (fallback_actor) return actor_fallback;
    return actor;
}
void __fastcall mutation_poison() {
    ++mutation_calls;throw std::runtime_error("query called mutation/get_or_create");
}
void setup() {
    for (uintptr_t rva : {0x592d128u,0x5964e20u,0x5967270u}) map_region(rva);
    *reinterpret_cast<uintptr_t*>(base()+0x592d128)=reinterpret_cast<uintptr_t>(application);
    *reinterpret_cast<uintptr_t*>(base()+0x592d0e0)=reinterpret_cast<uintptr_t>(idler);
    *reinterpret_cast<uintptr_t*>(base()+0x5964e20)=0x1111;
    *reinterpret_cast<uintptr_t*>(base()+0x5964e18)=reinterpret_cast<uintptr_t>(country_fallback);
    *reinterpret_cast<uintptr_t*>(base()+0x5967270)=0x2222;
    *reinterpret_cast<uintptr_t*>(base()+0x59646e8)=reinterpret_cast<uintptr_t>(actor_fallback);
    set(application,0x1c0,reinterpret_cast<uintptr_t>(idler));set(application,0x608,reinterpret_cast<uintptr_t>(world));
    set(idler,0,base()+0x4431500);set(idler,8,reinterpret_cast<uintptr_t>(logic));set(idler,0x18,reinterpret_cast<uintptr_t>(application));set(idler,0x20,uint8_t(1));
    set(logic,0,base()+0x4436a08);set(logic,0xa8,uint8_t(1));
    set(world,0,base()+0x443a438);set(world,0x120,reinterpret_cast<uintptr_t>(country_manager));set(world,0xe0,reinterpret_cast<uintptr_t>(roles));set(world,0xec,uint32_t(8));
    set(country_manager,0,base()+0x44adfc8);set(country_manager,0x2680,base()+0x44f4dd8);set(country_manager,0x28e8,uint8_t(0));
    set(country,8,base()+0x4494d08);set(country,0x18,country_id);set(country,0x20,actor_id);
    set(country_fallback,8,base()+0x4494d08);set(country_fallback,0x18,uint32_t(0xffffffff));
    set(actor,0,base()+0x44f0930);set(actor,8,actor_id);set(actor,0x50,country_id);
    set(actor,0x140,uint8_t(1));set(actor,0x141,uint8_t(0xaa));
    set(actor_fallback,0,base()+0x44f0930);set(actor_fallback,8,uint32_t(0xffffffff));
    thunk(0x33bd640,reinterpret_cast<void*>(&enter_guard));thunk(0x33bd750,reinterpret_cast<void*>(&leave_guard));
    thunk(0x7bb480,reinterpret_cast<void*>(&resolve_country));thunk(0xc5a4b0,reinterpret_cast<void*>(&resolve_actor));
    thunk(0x1514810,reinterpret_cast<void*>(&mutation_poison));thunk(0x3af1740,reinterpret_cast<void*>(&mutation_poison));
}
PdxV3ConsoleAiState state(uint32_t id=country_id) {
    PdxV3ConsoleAiState value={};value.size=sizeof(value);value.country_full_id=id;value.error=ERROR_IO_PENDING;return value;
}
PdxV3ConsoleAiState query(uint32_t id=country_id) {
    auto value=state(id);PdxV3ConsoleAiStateInterface(application,&value);
    assert(guard_depth==0 && guard_enters==guard_leaves && mutation_calls==0);return value;
}
void check_predicate(uint8_t enabled,uint8_t override_value,uint32_t role_value,uint32_t expected) {
    set(actor,0x140,enabled);set(country_manager,0x28e8,override_value);roles[4]=role_value;
    auto value=query();
    assert(value.error==0 && value.country_error==0 && value.actor_error==0 && value.manager_error==0 && value.role_error==0);
    assert(value.actor_enabled_raw==enabled && value.manager_override_raw==override_value && value.role_raw==role_value);
    assert(value.observed_predicate_valid==1 && value.observed_predicate==expected);
}
}
int main() {
    static_assert(sizeof(PdxV3ConsoleRequest)==96 && sizeof(PdxV3ConsolePlayerState)==160 && sizeof(PdxV3ConsoleAiState)==144);
    setup();
    unsigned char country_before[sizeof(country)],actor_before[sizeof(actor)],state_before[sizeof(country_manager)],world_before[sizeof(world)],app_before[sizeof(application)];
    uint32_t roles_before[8];
    std::memcpy(country_before,country,sizeof(country));std::memcpy(actor_before,actor,sizeof(actor));
    std::memcpy(state_before,country_manager,sizeof(country_manager));std::memcpy(world_before,world,sizeof(world));
    std::memcpy(app_before,application,sizeof(application));std::memcpy(roles_before,roles,sizeof(roles));
    auto normal=query();
    assert(normal.error==0 && normal.country_full_id==country_id && normal.country_error==0 && normal.actor_full_id==actor_id);
    assert(normal.actor_error==0 && normal.owner_country_full_id==country_id && normal.actor_enabled_raw==1 && normal.manager_override_raw==0);
    assert(normal.role_index==4 && normal.role_count==8 && normal.role_raw==0 && normal.role_error==0);
    assert(normal.observed_predicate_valid==1 && normal.observed_predicate==1 && normal.paused==1 && normal.simulation_started==1);
    assert(normal.application==reinterpret_cast<uintptr_t>(application) && normal.world==reinterpret_cast<uintptr_t>(world));
    assert(normal.ai_manager==reinterpret_cast<uintptr_t>(country_manager+0x2680) && normal.country==reinterpret_cast<uintptr_t>(country) && normal.actor==reinterpret_cast<uintptr_t>(actor));
    assert(country_calls==1 && actor_calls==1 && normal.execution_thread_id!=0 && normal.reserved==0);
    assert(!std::memcmp(country_before,country,sizeof(country)) && !std::memcmp(actor_before,actor,sizeof(actor)));
    assert(!std::memcmp(state_before,country_manager,sizeof(country_manager)) && !std::memcmp(world_before,world,sizeof(world)));
    assert(!std::memcmp(app_before,application,sizeof(application)) && !std::memcmp(roles_before,roles,sizeof(roles)));
    check_predicate(0,0,0,0);check_predicate(0,3,2,0);check_predicate(1,0,0,1);
    check_predicate(1,0,2,0);check_predicate(2,3,99,1);check_predicate(1,3,0,1);
    set(actor,0x140,uint8_t(1));set(country_manager,0x28e8,uint8_t(0));roles[4]=0;
    set(world,0xec,uint32_t(4));auto bounds=query();
    assert(bounds.error==0 && bounds.role_error==ERROR_NOT_FOUND && bounds.role_raw==0xffffffff && bounds.observed_predicate_valid==1 && bounds.observed_predicate==0);
    set(country_manager,0x28e8,uint8_t(3));bounds=query();assert(bounds.observed_predicate_valid==1 && bounds.observed_predicate==1);
    set(world,0xe0,uint64_t(0));set(world,0xec,uint32_t(0));auto empty=query();
    assert(empty.role_count==0 && empty.role_error==ERROR_NOT_FOUND && empty.observed_predicate_valid==1 && empty.observed_predicate==1);
    set(world,0xec,uint32_t(8));auto null_roles=query();
    assert(null_roles.error==0 && null_roles.actor_error==0 && null_roles.manager_error==0 && null_roles.role_error==ERROR_INVALID_DATA && null_roles.observed_predicate_valid==0);
    set(world,0xe0,reinterpret_cast<uintptr_t>(roles));
    auto calls=actor_calls;set(country,0x20,uint32_t(0xffffffff));auto absent=query();
    assert(absent.error==0 && absent.country_error==0 && absent.actor_error==ERROR_NOT_FOUND && absent.actor_full_id==0xffffffff && absent.actor==0);
    assert(absent.manager_error==0 && absent.manager_override_raw==3 && absent.role_error==0 && absent.observed_predicate_valid==0 && actor_calls==calls);
    set(country,0x20,actor_id);
    *reinterpret_cast<uintptr_t*>(base()+0x5967270)=0;auto no_actor_root=query();
    assert(no_actor_root.error==0 && no_actor_root.actor_error==ERROR_NOT_READY && no_actor_root.role_error==0 && actor_calls==calls);
    *reinterpret_cast<uintptr_t*>(base()+0x5967270)=0x2222;
    fallback_actor=true;auto af=query();assert(af.actor_error==ERROR_NOT_FOUND && af.actor==0 && af.observed_predicate_valid==0);fallback_actor=false;
    null_actor=true;auto an=query();assert(an.actor_error==ERROR_NOT_FOUND && an.role_error==0);null_actor=false;
    set(actor,8,uint32_t(0x01000007));auto stale_actor=query();assert(stale_actor.actor_error==ERROR_NOT_FOUND && stale_actor.actor==0);set(actor,8,actor_id);
    set(actor,0x50,uint32_t(0x01000004));auto wrong_owner=query();
    assert(wrong_owner.actor_error==ERROR_INVALID_DATA && wrong_owner.owner_country_full_id==0x01000004 && wrong_owner.actor==0 && wrong_owner.actor_enabled_raw==0 && wrong_owner.role_error==0);
    set(actor,0x50,country_id);set(actor,0,uint64_t(0));auto wrong_actor_type=query();assert(wrong_actor_type.actor_error==ERROR_INVALID_DATA);set(actor,0,base()+0x44f0930);
    calls=actor_calls;auto missing_country=query(0x01000004);assert(missing_country.country_error==ERROR_NOT_FOUND && missing_country.actor_error==ERROR_NOT_READY && missing_country.role_error==ERROR_NOT_READY && actor_calls==calls);
    alias_country=true;auto stale_country=query(0x01000004);assert(stale_country.country_error==ERROR_NOT_FOUND && actor_calls==calls);alias_country=false;
    null_country=true;auto cn=query();assert(cn.country_error==ERROR_NOT_FOUND && actor_calls==calls);null_country=false;
    auto cc=country_calls;auto invalid=query(0xffffffff);assert(invalid.country_error==ERROR_NOT_FOUND && country_calls==cc);
    *reinterpret_cast<uintptr_t*>(base()+0x5964e20)=0;auto no_country_root=query();assert(no_country_root.country_error==ERROR_NOT_READY && no_country_root.manager_error==0 && country_calls==cc);
    *reinterpret_cast<uintptr_t*>(base()+0x5964e20)=0x1111;
    set(country,8,uint64_t(0));auto wrong_country_type=query();assert(wrong_country_type.country_error==ERROR_INVALID_DATA);set(country,8,base()+0x4494d08);
    throw_country=true;auto failed_country=query();assert(failed_country.error==ERROR_UNHANDLED_EXCEPTION && failed_country.observed_predicate_valid==0);throw_country=false;
    throw_actor=true;auto failed_actor=query();assert(failed_actor.error==ERROR_UNHANDLED_EXCEPTION && failed_actor.observed_predicate_valid==0);throw_actor=false;
    auto reused=normal;set(country,0x20,uint32_t(0xffffffff));PdxV3ConsoleAiStateInterface(application,&reused);
    assert(reused.error==0 && reused.actor_error==ERROR_NOT_FOUND && reused.actor==0 && reused.actor_enabled_raw==0 && reused.observed_predicate_valid==0);set(country,0x20,actor_id);
    auto bad_size=state();bad_size.size=140;auto enters=guard_enters;PdxV3ConsoleAiStateInterface(application,&bad_size);assert(bad_size.error==ERROR_IO_PENDING && guard_enters==enters);
    assert(PdxV3ConsoleReadAiState(reinterpret_cast<uintptr_t>(application),&bad_size)==ERROR_INVALID_PARAMETER);
    PdxV3ConsoleAiStateInterface(application,nullptr);assert(PdxV3ConsoleReadAiState(0,nullptr)==ERROR_INVALID_PARAMETER);
    auto wrong_app=state();PdxV3ConsoleAiStateInterface(country,&wrong_app);assert(wrong_app.error==ERROR_NOT_READY);
    set(application,0x258,uint8_t(1));auto closing=query();assert(closing.error==ERROR_NOT_READY);set(application,0x258,uint8_t(0));
    set(application,0x608,uint64_t(0));auto no_world=query();assert(no_world.error==ERROR_NOT_READY);set(application,0x608,reinterpret_cast<uintptr_t>(world));
    set(world,0,uint64_t(0));auto wrong_world_type=query();assert(wrong_world_type.error==ERROR_INVALID_DATA && wrong_world_type.country_manager==0);set(world,0,base()+0x443a438);
    set(world,0x120,uint64_t(0));auto no_state=query();assert(no_state.error==ERROR_NOT_READY);set(world,0x120,reinterpret_cast<uintptr_t>(country_manager));
    // A CGameState vptr at payload+0 is the exact previous fixture mistake.
    // It must not pass as the inline CCountryManager payload receiver.
    set(country_manager,0,base()+0x443a438);auto mistaken_payload_type=query();assert(mistaken_payload_type.error==ERROR_INVALID_DATA && mistaken_payload_type.ai_manager==0);
    set(country_manager,0,uint64_t(0));auto wrong_payload_type=query();assert(wrong_payload_type.error==ERROR_INVALID_DATA);set(country_manager,0,base()+0x44adfc8);
    set(country_manager,0x2680,uint64_t(0));auto wrong_manager_type=query();assert(wrong_manager_type.error==ERROR_INVALID_DATA);set(country_manager,0x2680,base()+0x44f4dd8);
    auto direct=state();assert(PdxV3ConsoleReadAiState(reinterpret_cast<uintptr_t>(application),&direct)==0 && direct.actor_error==0);
    assert(guard_depth==0 && guard_enters==guard_leaves && mutation_calls==0);
    std::puts("PASS copied AI144B; Interface scope; fresh Country/actor full IDs and owner; raw bytes; separate errors; native scheduling predicate; no creation/dispatch/writes; exception cleanup; console96B/Player160B preserved");
    return 0;
}
