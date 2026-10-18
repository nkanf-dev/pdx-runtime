#define NOMINMAX
#include "../v3_console.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cctype>
#include <initializer_list>
#include <stdexcept>

namespace {
unsigned char application[0x620]={}, idler[0x30]={}, logic[0xb0]={};
unsigned char player[0x78]={}, other_player[0x78]={};
unsigned char country[0xd00]={}, second_country[0xd00]={}, fallback[0xd00]={};
uintptr_t player_list[2]={};
uint32_t guard_depth=0, guard_mode=0, mutex_depth=0, reference_calls=0, string_destroys=0;
bool missing_player=false, wrong_player=false, country_valid=true, throw_reference=false, throw_tag=false;
int mutex_error=0;
uintptr_t base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> void set(void* object,size_t offset,T value) { *reinterpret_cast<T*>(static_cast<unsigned char*>(object)+offset)=value; }
template<class T> T get(const void* object,size_t offset) { return *reinterpret_cast<const T*>(static_cast<const unsigned char*>(object)+offset); }
void map_region(uintptr_t rva,DWORD flags=PAGE_READWRITE) {
    const auto address=(base()+rva)&~uintptr_t(0xffff);
    void* memory=VirtualAlloc(reinterpret_cast<void*>(address),0x10000,MEM_RESERVE|MEM_COMMIT,flags);
    assert(memory || GetLastError()==ERROR_INVALID_ADDRESS);
}
void thunk(uintptr_t rva,void* target) {
    map_region(rva,PAGE_EXECUTE_READWRITE);
    auto* bytes=reinterpret_cast<unsigned char*>(base()+rva);
    // Native mutex import thunks are8 bytes apart: use a5-byte relative jump,
    // not overlapping12-byte absolute thunks. All fixture targets share this EXE.
    const auto difference=static_cast<int64_t>(reinterpret_cast<uintptr_t>(target))-static_cast<int64_t>(base()+rva+5);
    assert(difference>=INT32_MIN && difference<=INT32_MAX);
    const auto displacement=static_cast<int32_t>(difference);
    bytes[0]=0xe9;std::memcpy(bytes+1,&displacement,4);
    FlushInstructionCache(GetCurrentProcess(),bytes,5);
}
void* __fastcall enter_guard(void* storage,uint32_t mode,int flag,const char*) {
    assert(guard_depth==0 && (mode==1 || mode==2) && flag==0);
    guard_depth=1;guard_mode=mode;return storage;
}
void __fastcall leave_guard(void*) { assert(guard_depth==1 && mutex_depth==0);guard_depth=0; }
int __fastcall enter_mutex(void* object) {
    assert(object==application+0x590 && mutex_depth==0 && guard_depth==1);
    if (mutex_error) return mutex_error;
    mutex_depth=1;return 0;
}
void __fastcall leave_mutex(void* object) { assert(object==application+0x590 && mutex_depth==1);mutex_depth=0; }
void* __fastcall get_player(void* current_idler) {
    assert(current_idler==idler && guard_depth==1 && guard_mode==1 && mutex_depth==0);
    if (missing_player) return nullptr;
    if (wrong_player) return other_player;
    return player;
}
uint32_t __fastcall playable_id(const void* playable) {
    assert(guard_depth==1 && mutex_depth==0);
    return get<uint32_t>(playable,0x10);
}
void* __fastcall resolve_country(const uint32_t* id) {
    assert(guard_depth==1 && mutex_depth==0);
    if (*id==get<uint32_t>(country,0x18)) return country;
    if (*id==get<uint32_t>(second_country,0x18)) return second_country;
    return fallback;
}
void* __fastcall resolve_reference(const void* reference) {
    assert(guard_depth==1 && guard_mode==1 && mutex_depth==0);
    ++reference_calls;
    if (throw_reference) throw std::runtime_error("reference fixture failure");
    const auto id=get<uint32_t>(reference,8);
    return static_cast<unsigned char*>(resolve_country(&id))+8;
}
void* __fastcall construct_string(PdxV3ConsoleString* out,const char* bytes,uint64_t length) {
    assert(length<=15);*out={{0,0},length,15};std::memcpy(out->storage,bytes,static_cast<size_t>(length));return out;
}
void __fastcall destroy_string(PdxV3ConsoleString* value) { ++string_destroys;value->length=0;value->capacity=15; }
void* __fastcall resolve_tag(const PdxV3ConsoleString* tag) {
    assert(guard_depth==1 && guard_mode==2 && mutex_depth==0 && tag->length==3);
    if (throw_tag) throw std::runtime_error("tag fixture failure");
    const auto* chars=reinterpret_cast<const char*>(tag->storage);
    if (std::toupper(chars[0])=='S' && std::toupper(chars[1])=='W' && std::toupper(chars[2])=='E') return second_country;
    return fallback;
}
bool __fastcall persistent_valid(const void* persistent) { assert(persistent==second_country+0x10);return country_valid; }
void setup() {
    for (uintptr_t rva : {0x592d128u,0x595735cu,0x595720au,0x5964e20u,0x4494d08u}) map_region(rva);
    *reinterpret_cast<uintptr_t*>(base()+0x592d128)=reinterpret_cast<uintptr_t>(application);
    *reinterpret_cast<uintptr_t*>(base()+0x592d0e0)=reinterpret_cast<uintptr_t>(idler);
    *reinterpret_cast<uint32_t*>(base()+0x595735c)=3;
    *reinterpret_cast<uint8_t*>(base()+0x595720a)=0;
    *reinterpret_cast<uintptr_t*>(base()+0x5964e20)=0x1111;
    *reinterpret_cast<uintptr_t*>(base()+0x5964e18)=reinterpret_cast<uintptr_t>(fallback);
    *reinterpret_cast<uintptr_t*>(base()+0x4494d08+8)=reinterpret_cast<uintptr_t>(&playable_id);
    set(application,0x1c0,reinterpret_cast<uintptr_t>(idler));set(application,0x608,uint64_t(0x2222));
    set(application,0x568,reinterpret_cast<uintptr_t>(player_list));set(application,0x574,uint32_t(2));set(application,0x580,uint32_t(3));
    set(idler,0,base()+0x4431500);set(idler,8,reinterpret_cast<uintptr_t>(logic));set(idler,0x18,reinterpret_cast<uintptr_t>(application));set(idler,0x20,uint8_t(1));
    set(logic,0,base()+0x4436a08);set(logic,0xa8,uint8_t(1));
    set(country,8,base()+0x4494d08);set(country,0x18,uint32_t(0x01000001));
    set(second_country,8,base()+0x4494d08);set(second_country,0x18,uint32_t(0x02000002));set(second_country,0xca4,uint32_t(26));
    static uintptr_t persistent_table[2]={0,reinterpret_cast<uintptr_t>(&persistent_valid)};
    set(second_country,0x10,reinterpret_cast<uintptr_t>(persistent_table));
    set(fallback,8,base()+0x4494d08);set(fallback,0x18,uint32_t(0xffffffff));
    set(player,0x48,uint8_t(0xd7));set(player,0x50,uint64_t(0x1111));set(player,0x58,uint32_t(0x01000001));
    set(player,0x60,uint64_t(0x1111));set(player,0x68,uint32_t(0x02000002));set(player,0x70,uint32_t(3));set(other_player,0x70,uint32_t(8));
    player_list[0]=reinterpret_cast<uintptr_t>(player);player_list[1]=reinterpret_cast<uintptr_t>(other_player);
    thunk(0x33bd640,reinterpret_cast<void*>(&enter_guard));thunk(0x33bd750,reinterpret_cast<void*>(&leave_guard));
    thunk(0x418ef70,reinterpret_cast<void*>(&enter_mutex));thunk(0x418ef78,reinterpret_cast<void*>(&leave_mutex));
    thunk(0x33bdb80,reinterpret_cast<void*>(&get_player));thunk(0x6c5eb0,reinterpret_cast<void*>(&resolve_reference));
    thunk(0x7bb480,reinterpret_cast<void*>(&resolve_country));thunk(0x10ddf00,reinterpret_cast<void*>(&resolve_tag));
    thunk(0x61c420,reinterpret_cast<void*>(&construct_string));thunk(0x61f810,reinterpret_cast<void*>(&destroy_string));
}
PdxV3ConsolePlayerState state(uint32_t action=1) { PdxV3ConsolePlayerState value={};value.size=sizeof(value);value.action=action;return value; }
}
int main() {
    setup();
    auto observed=state();PdxV3ConsolePlayerStateInterface(application,&observed);
    assert(observed.error==0 && observed.controlled_country_id==0x01000001 && observed.selected_country_id==0x02000002);
    assert(observed.controlled_country_error==0 && observed.selected_country_error==0 && observed.observer_enabled==0);
    assert(observed.paused==1 && observed.simulation_started==1 && guard_depth==0 && mutex_depth==0 && reference_calls==2);
    set(player,0x48,uint8_t(0xf7));set(player,0x50,uint64_t(0));set(player,0x58,uint32_t(0xffffffff));
    auto observer=state();PdxV3ConsolePlayerStateInterface(application,&observer);
    assert(observer.error==0 && observer.observer_enabled==1 && observer.controlled_country_error==ERROR_NOT_FOUND);
    assert(observer.selected_country_error==0 && observer.selected_country_id==0x02000002);
    assert(guard_depth==0 && mutex_depth==0);
    auto tag=state(2);tag.tag_bytes=3;std::memcpy(tag.tag,"swe",3);PdxV3ConsolePlayerStateInterface(application,&tag);
    assert(tag.error==0 && tag.target_error==0 && tag.target_country_id==0x02000002 && tag.target_control_eligible==1 && string_destroys==1);
    country_valid=false;PdxV3ConsolePlayerStateInterface(application,&tag);assert(tag.target_error==0 && tag.target_control_eligible==0);
    auto missing=state(2);missing.tag_bytes=3;std::memcpy(missing.tag,"ZZZ",3);PdxV3ConsolePlayerStateInterface(application,&missing);
    assert(missing.error==0 && missing.target_error==ERROR_NOT_FOUND && missing.target_country_id==0xffffffff && string_destroys==3);
    auto bad=state(2);bad.tag_bytes=2;PdxV3ConsolePlayerStateInterface(application,&bad);assert(bad.error==ERROR_INVALID_PARAMETER);
    throw_reference=true;auto failed_ref=state();PdxV3ConsolePlayerStateInterface(application,&failed_ref);
    assert(failed_ref.error==ERROR_UNHANDLED_EXCEPTION && guard_depth==0 && mutex_depth==0);throw_reference=false;
    throw_tag=true;PdxV3ConsolePlayerStateInterface(application,&tag);
    assert(tag.error==ERROR_UNHANDLED_EXCEPTION && guard_depth==0 && mutex_depth==0 && string_destroys==4);throw_tag=false;
    wrong_player=true;auto wrong=state();PdxV3ConsolePlayerStateInterface(application,&wrong);assert(wrong.error==ERROR_NOT_FOUND && wrong.player==0);wrong_player=false;
    mutex_error=5;auto lock_fail=state();PdxV3ConsolePlayerStateInterface(application,&lock_fail);assert(lock_fail.error==ERROR_LOCK_FAILED && guard_depth==0 && mutex_depth==0);mutex_error=0;
    set(application,0x608,uint64_t(0));auto no_world=state();PdxV3ConsolePlayerStateInterface(application,&no_world);assert(no_world.error==ERROR_NOT_READY && guard_depth==0);
    std::puts("PASS copied Player Country/observer state; native scope/mutex ordering; separate references; tag CString lifecycle/eligibility; missing/error cleanup");
    return 0;
}
