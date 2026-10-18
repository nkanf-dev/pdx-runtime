#define PDX_V3_TIME_STEP_BUILD
#include <Windows.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cassert>
#include <initializer_list>
static unsigned char* fake_image;
static HMODULE WINAPI host_image(LPCWSTR) { return reinterpret_cast<HMODULE>(fake_image); }
#define GetModuleHandleW host_image
#include "../v3_time_step.cpp"
#undef GetModuleHandleW

namespace host {
unsigned failures=0, factories=0, submits=0, destroys=0, enters=0, leaves=0;
uintptr_t command_table[18]{};
unsigned char application[0x610]{}, idler[0x30]{}, logic[0x100]{}, world[0x160]{};
alignas(16) unsigned char dispatcher[0xb0]{};
void check(bool ok,const char* name) { printf("%s %s\n",ok?"PASS":"FAIL",name);if(!ok)++failures; }
template<class T> void put(void* p,size_t o,T v) { memcpy(static_cast<unsigned char*>(p)+o,&v,sizeof(v)); }
void jump(uintptr_t rva,void* target) {
    auto* p=fake_image+rva;p[0]=0x48;p[1]=0xb8;memcpy(p+2,&target,8);p[10]=0xff;p[11]=0xe0;
}
void* enter(void* storage,uint32_t mode,int flag,const char*) {
    check(reinterpret_cast<uintptr_t>(storage)%16==0&&mode==1&&flag==0,"existing native Interface guard shape");
    ++enters;return storage;
}
void leave(void*) { ++leaves; }
void* destroy(void* p,uint32_t flags) { check(flags==1,"owned command scalar delete shape");++destroys;delete[]static_cast<unsigned char*>(p);return nullptr; }
void* factory(uint32_t token) {
    check(token==12426,"exact registered partial tick token");++factories;
    auto* p=new unsigned char[40]{};put<uintptr_t>(p,0,reinterpret_cast<uintptr_t>(command_table));put<uint32_t>(p,32,0xffffffffu);return p;
}
bool submit(void** owned,uint32_t flags) {
    check(flags==7,"original natural receiver flags7");
    auto* session=fake_image+session_rva;
    // All snapshot locks must be released before natural preparation/receiver.
    for(size_t offset:{size_t(0x98),size_t(0x78),size_t(0x88)}) {
        auto* lock=reinterpret_cast<PSRWLOCK>(dispatcher+offset);
        const bool got=TryAcquireSRWLockExclusive(lock)!=FALSE;check(got,"no snapshot lock held on submission");
        if(got)ReleaseSRWLockExclusive(lock);
    }
    check(*owned!=nullptr,"one owned native command submitted");++submits;
    destroy(*owned,1);*owned=nullptr;
    put<uint32_t>(dispatcher,0x24,1);
    put<uintptr_t>(session,0x480,0);
    return true;
}
void ready() {
    memset(application,0,sizeof(application));memset(idler,0,sizeof(idler));memset(logic,0,sizeof(logic));memset(world,0,sizeof(world));memset(dispatcher,0,sizeof(dispatcher));
    auto* session=fake_image+session_rva;memset(session,0,0x490);
    put<uintptr_t>(fake_image,application_rva,reinterpret_cast<uintptr_t>(application));put<uintptr_t>(fake_image,idler_rva,reinterpret_cast<uintptr_t>(idler));
    put<uintptr_t>(application,0x608,reinterpret_cast<uintptr_t>(world));put<uint64_t>(application,0x500,123);
    put<uintptr_t>(idler,0,reinterpret_cast<uintptr_t>(fake_image)+0x4431500);put<uintptr_t>(idler,8,reinterpret_cast<uintptr_t>(logic));idler[0x20]=1;
    put<uintptr_t>(logic,0,reinterpret_cast<uintptr_t>(fake_image)+0x4436a08);logic[0xa8]=1;
    put<uint64_t>(world,8,43800000);put<uintptr_t>(session,0x3d8,reinterpret_cast<uintptr_t>(dispatcher));session[0x3f0]=1;put<uintptr_t>(session,0xd0,1);
}
DWORD WINAPI hold_lock(void* argument) { auto** handles=static_cast<void**>(argument);auto* lock=reinterpret_cast<PSRWLOCK>(dispatcher+0x98);AcquireSRWLockExclusive(lock);SetEvent(handles[0]);WaitForSingleObject(handles[1],INFINITE);ReleaseSRWLockExclusive(lock);return 0; }
}
int main() {
    using namespace host;
    printf("time_step_host pid=%lu; fake image/native queue mocks, NOT game tick evidence\n",GetCurrentProcessId());
    fake_image=static_cast<unsigned char*>(VirtualAlloc(nullptr,0x5c00000,MEM_RESERVE|MEM_COMMIT,PAGE_EXECUTE_READWRITE));if(!fake_image)return 2;
    command_table[0]=reinterpret_cast<uintptr_t>(&host::destroy);
    jump(0x3371000,reinterpret_cast<void*>(&host::factory));jump(0xcd2d00,reinterpret_cast<void*>(&host::submit));jump(0x33bd640,reinterpret_cast<void*>(&host::enter));jump(0x33bd750,reinterpret_cast<void*>(&host::leave));ready();
    uint64_t owned=0;check(PdxV3TimeStepFactory(nullptr)==ERROR_INVALID_PARAMETER,"factory null input");check(PdxV3TimeStepFactory(&owned)==0&&owned,"public raw factory owns one command");check(PdxV3TimeStepDestroy(&owned)==0&&!owned,"public raw destroy clears owner");
    PdxV3TimeStepResult r{};PdxV3TimeStepInterface(application,&r);check(r.error==0&&r.queues_settled&&r.date_raw==43800000&&r.paused,"raw settled baseline observation");check(submits==0,"observation never submits a tick");
    r.action=1;PdxV3TimeStepInterface(application,&r);check(r.error==0&&r.accepted&&submits==1&&r.date_raw==43800000,"one admission preserves baseline, no fabricated completion");check(field<uint64_t>(reinterpret_cast<uintptr_t>(world),8)==43800000,"adapter never writes native date");
    r.action=0;PdxV3TimeStepInterface(application,&r);check(r.error==0&&!r.queues_settled&&r.outgoing_pending==1,"explicit observation reports queued work");
    // Simulate the engine alone executing partial+followups; adapter merely observes.
    put<uint64_t>(world,8,43800006);put<uint32_t>(dispatcher,0x24,0);
    logic[0xdf]=1;put<uint32_t>(fake_image,0x5964f10,77);r.action=0;PdxV3TimeStepInterface(application,&r);
    check(r.error==0&&r.queues_settled&&r.date_raw==43800006&&r.logic_tick_pending==1,"settlement uses queues not guessed DF/accounting completion");
    const auto submitted=submits;
    idler[0x20]=0;r.action=1;PdxV3TimeStepInterface(application,&r);check(r.error==ERROR_NOT_READY&&submits==submitted,"unpaused input rejects without unpause or submission");
    ready();put<uint32_t>(dispatcher,0x54,1);r.action=1;PdxV3TimeStepInterface(application,&r);check(r.error==ERROR_NOT_READY&&submits==submitted,"existing followup queue rejects second admission");
    ready();fake_image[session_rva+0xc8]=1;r.action=1;PdxV3TimeStepInterface(application,&r);check(r.error==ERROR_NOT_READY&&submits==submitted,"stopped native worker rejects admission");
    ready();application[0x258]=1;r.action=1;PdxV3TimeStepInterface(application,&r);check(r.error==ERROR_NOT_READY&&submits==submitted,"world transition rejects admission");
    ready();void* handles[2]={CreateEventW(nullptr,TRUE,FALSE,nullptr),CreateEventW(nullptr,TRUE,FALSE,nullptr)};HANDLE thread=CreateThread(nullptr,0,hold_lock,handles,0,nullptr);WaitForSingleObject(handles[0],5000);
    r.action=1;PdxV3TimeStepInterface(application,&r);check(r.error==ERROR_BUSY&&submits==submitted,"Try-only snapshot returns BUSY without deadlock or admission");SetEvent(handles[1]);WaitForSingleObject(thread,5000);CloseHandle(thread);CloseHandle(handles[0]);CloseHandle(handles[1]);
    check(enters==leaves,"all native access scopes leave");VirtualFree(fake_image,0,MEM_RELEASE);printf("RESULT failures=%u\n",failures);return failures?1:0;
}
