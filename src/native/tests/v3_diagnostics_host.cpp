#define NOMINMAX
#include "../v3_diagnostics.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <thread>
#include <vector>

namespace {
struct NativeString {
    uint64_t storage[2],length,capacity;
    void assign(const char* value) {
        length=std::strlen(value);
        if (length<=15) { capacity=15;std::memcpy(storage,value,length); }
        else { capacity=length;storage[0]=reinterpret_cast<uint64_t>(value); }
    }
};
struct Entry {
    uint64_t vtable; LONG references; uint32_t pad;
    int32_t sequence; uint32_t category,line,group,level,pad2;
    uint64_t time;
    NativeString label,source,message;
};
struct Viewer {
    uint64_t vtable; unsigned char preceding[0x20];
    uint64_t data; uint32_t capacity,count;uint64_t allocator;
    int32_t first,last;uint32_t limit,prune;
    unsigned char rest[0x28];LONG lock;uint32_t padding;
};
static_assert(sizeof(Viewer)==0x80);
static_assert(sizeof(Entry)==0x90);
Viewer viewer={};
Entry entries[3]={};
uint64_t owner_addresses[3]={};
unsigned destroyed=0;
unsigned char application[8]={};
uintptr_t base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
void map_page(uintptr_t rva,DWORD flags=PAGE_READWRITE) {
    const auto address=(base()+rva)&~uintptr_t(0xffff);
    void* memory=VirtualAlloc(reinterpret_cast<void*>(address),0x10000,MEM_COMMIT|MEM_RESERVE,flags);
    assert(memory || GetLastError()==ERROR_INVALID_ADDRESS);
}
void __fastcall entry_destructor(void* address,uint32_t flags) {
    assert(flags==1 && viewer.lock==0);
    ++destroyed;
    assert(static_cast<Entry*>(address)->references==0);
}
void setup() {
    map_page(0x595e060);map_page(0x592d128);map_page(0x58fe25c);map_page(0x4759668);
    *reinterpret_cast<uintptr_t*>(base()+0x4759668)=reinterpret_cast<uintptr_t>(&entry_destructor);
    *reinterpret_cast<uint64_t*>(base()+0x595e060)=reinterpret_cast<uint64_t>(&viewer);
    *reinterpret_cast<uintptr_t*>(base()+0x592d128)=reinterpret_cast<uintptr_t>(application);
    *reinterpret_cast<uint8_t*>(base()+0x58fe25c)=1;
    viewer.vtable=base()+0x474faa8;viewer.data=reinterpret_cast<uint64_t>(owner_addresses);
    viewer.capacity=3;viewer.count=3;viewer.first=5;viewer.last=7;viewer.limit=100000;viewer.prune=1000;
    for (uint32_t i=0;i<3;++i) {
        entries[i].vtable=base()+0x4759668;entries[i].references=1;
        entries[i].sequence=static_cast<int32_t>(5+i);entries[i].category=0x10000;
        entries[i].line=247+i;entries[i].group=i;entries[i].level=4;entries[i].time=123456+i;
        entries[i].label.assign("script");entries[i].source.assign("native_script.cpp");
        entries[i].message.assign(i==0?"first diagnostic":"second diagnostic with native heap bytes");
        owner_addresses[i]=reinterpret_cast<uint64_t>(&entries[i]);
    }
}
std::vector<unsigned char> request(uint32_t operation,int32_t after,uint32_t maximum,uint32_t capacity) {
    std::vector<unsigned char> data(sizeof(PdxV3DiagnosticsRequest)+capacity);
    auto& value=*reinterpret_cast<PdxV3DiagnosticsRequest*>(data.data());
    value.size=static_cast<uint32_t>(data.size());value.operation=operation;value.after_sequence=after;
    value.max_records=maximum;value.output_capacity=capacity;return data;
}
}
int main() {
    setup();
    PdxV3DiagnosticStatus status={sizeof(status)};
    assert(PdxV3DiagnosticsReadStatus(reinterpret_cast<uint64_t>(&viewer),&status)==0);
    assert(status.store_count==3 && status.first_sequence==5 && status.last_sequence==7 && viewer.lock==0);
    uint64_t selected[2]={};PdxV3DiagnosticSnapshot snapshot={sizeof(snapshot)};
    assert(PdxV3DiagnosticsPinRecords(reinterpret_cast<uint64_t>(&viewer),4,2,selected,2,&snapshot)==0);
    assert(snapshot.pinned_count==2 && snapshot.eligible_count==3 && viewer.lock==0);
    assert(entries[0].references==2 && entries[1].references==2 && entries[2].references==1);
    uint32_t needed=0;
    assert(PdxV3DiagnosticsCopyRecord(selected[1],nullptr,0,&needed)==ERROR_MORE_DATA);
    std::vector<unsigned char> copied(needed);
    assert(PdxV3DiagnosticsCopyRecord(selected[1],copied.data(),needed,&needed)==0 && viewer.lock==0);
    const auto& record=*reinterpret_cast<PdxV3DiagnosticRecord*>(copied.data());
    assert(record.native_sequence==6 && record.category==0x10000 && record.source_line==248 && record.level_raw==4);
    assert(record.native_time_raw==123457 && record.message_bytes==entries[1].message.length);
    PdxV3DiagnosticsReleaseEntry(selected[0]);PdxV3DiagnosticsReleaseEntry(selected[1]);
    assert(entries[0].references==1 && entries[1].references==1);
    auto full=request(2,-1,2,4096);
    PdxV3DiagnosticsInterface(application,full.data());
    auto& result=*reinterpret_cast<PdxV3DiagnosticsRequest*>(full.data());
    assert(result.error==0 && result.returned_count==2 && result.eligible_count==3 && result.has_more==1);
    assert(result.flags&PDX_V3_DIAGNOSTIC_GAP);assert(result.flags&PDX_V3_DIAGNOSTIC_PRUNED);
    assert(result.next_sequence==6 && result.subscription_coverage_known==0 && viewer.lock==0);
    for (const auto& entry:entries) assert(entry.references==1);
    auto short_buffer=request(2,4,3,48);
    PdxV3DiagnosticsInterface(application,short_buffer.data());
    const auto& short_result=*reinterpret_cast<PdxV3DiagnosticsRequest*>(short_buffer.data());
    assert(short_result.error==ERROR_MORE_DATA && short_result.returned_count==0 && short_result.next_sequence==4);
    assert(short_result.output_required>48 && short_result.flags&PDX_V3_DIAGNOSTIC_OUTPUT_TRUNCATED);
    auto reset=request(2,99,1,4096);
    PdxV3DiagnosticsInterface(application,reset.data());
    const auto& reset_result=*reinterpret_cast<PdxV3DiagnosticsRequest*>(reset.data());
    assert(reset_result.flags&PDX_V3_DIAGNOSTIC_RESET);assert(reset_result.next_sequence==5);
    auto changed=request(2,7,1,4096);
    reinterpret_cast<PdxV3DiagnosticsRequest*>(changed.data())->expected_viewer=0x1;
    PdxV3DiagnosticsInterface(application,changed.data());
    assert(reinterpret_cast<PdxV3DiagnosticsRequest*>(changed.data())->flags&PDX_V3_DIAGNOSTIC_RESET);
    viewer.lock=1;
    std::thread writer([] { InterlockedAnd(&viewer.lock,~1L); });
    PdxV3DiagnosticStatus raced={sizeof(raced)};
    assert(PdxV3DiagnosticsReadStatus(reinterpret_cast<uint64_t>(&viewer),&raced)==0);
    writer.join();assert(viewer.lock==0);
    assert(PdxV3DiagnosticsRetainEntry(owner_addresses[0])==0);
    InterlockedDecrement(&entries[0].references); // native store released its owner after pin
    PdxV3DiagnosticsReleaseEntry(owner_addresses[0]);assert(destroyed==1);
    *reinterpret_cast<uint64_t*>(base()+0x595e060)=0;
    auto absent=request(1,-1,0,0);PdxV3DiagnosticsInterface(application,absent.data());
    const auto& unavailable=*reinterpret_cast<PdxV3DiagnosticsRequest*>(absent.data());
    assert(unavailable.error==ERROR_NOT_READY && unavailable.viewer_available==0 && unavailable.subscription_coverage_known==0);
    assert(destroyed==1 && viewer.lock==0);
    std::puts("PASS native viewer status/paging; reader lock pin/copy/release; exact native destructor; gap/reset/truncation/absence");
    return 0;
}
