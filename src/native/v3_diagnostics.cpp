#define NOMINMAX
#include "v3_diagnostics.h"
#include <algorithm>
#include <cstring>
#include <limits>
#include <new>
#include <vector>

namespace {
constexpr uintptr_t viewer_root_rva = 0x595e060;
constexpr uintptr_t viewer_getter_rva = 0x2dd83a0;
constexpr uintptr_t viewer_vtable_rva = 0x474faa8;
constexpr uintptr_t entry_vtable_rva = 0x4759668;
constexpr uintptr_t logging_ready_rva = 0x58fe25c;
constexpr uintptr_t application_root_rva = 0x592d128;
uintptr_t image_base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T field(uintptr_t object, size_t offset) {
    return *reinterpret_cast<const T*>(object+offset);
}
struct NativeString {
    uint64_t storage[2], length, capacity;
    const char* data() const {
        return capacity>15 ? reinterpret_cast<const char*>(storage[0]) : reinterpret_cast<const char*>(storage);
    }
};
struct ReaderLock {
    volatile LONG* value;
    explicit ReaderLock(uintptr_t viewer) : value(reinterpret_cast<volatile LONG*>(viewer+0x78)) {
        while ((InterlockedExchangeAdd(value,2)&1)!=0) {
            InterlockedExchangeAdd(value,-2);
            while ((*value&1)!=0) YieldProcessor();
        }
    }
    ~ReaderLock() { InterlockedExchangeAdd(value,-2); }
};
bool valid_viewer(uint64_t viewer) {
    return viewer && field<uintptr_t>(viewer,0)==image_base()+viewer_vtable_rva;
}
bool valid_entry(uint64_t entry) {
    return entry && field<uintptr_t>(entry,0)==image_base()+entry_vtable_rva;
}
void snapshot_fields(uint64_t viewer, PdxV3DiagnosticStatus& status) {
    status.viewer=viewer;
    status.store_count=field<uint32_t>(viewer,0x34);
    status.first_sequence=field<int32_t>(viewer,0x40);
    status.last_sequence=field<int32_t>(viewer,0x44);
    status.native_limit=field<uint32_t>(viewer,0x48);
    status.native_prune=field<uint32_t>(viewer,0x4c);
    if (status.first_sequence>0) status.flags|=PDX_V3_DIAGNOSTIC_PRUNED;
}
void publish_status(PdxV3DiagnosticsRequest& request,const PdxV3DiagnosticStatus& status) {
    request.error=status.error; request.flags|=status.flags;
    request.store_count=status.store_count;
    request.first_sequence=status.first_sequence; request.last_sequence=status.last_sequence;
    request.native_limit=status.native_limit; request.native_prune=status.native_prune;
}
struct PinnedEntries {
    std::vector<uint64_t> addresses;
    PdxV3DiagnosticSnapshot snapshot={sizeof(PdxV3DiagnosticSnapshot)};
    explicit PinnedEntries(uint32_t capacity) : addresses(capacity) {}
    ~PinnedEntries() {
        for (uint32_t i=0;i<snapshot.pinned_count;++i) PdxV3DiagnosticsReleaseEntry(addresses[i]);
    }
};
static_assert(sizeof(NativeString)==32);
static_assert(sizeof(PdxV3DiagnosticStatus)==40);
static_assert(sizeof(PdxV3DiagnosticSnapshot)==48);
static_assert(sizeof(PdxV3DiagnosticsRequest)==104);
static_assert(sizeof(PdxV3DiagnosticRecord)==48);
}

uint64_t WINAPI PdxV3DiagnosticsViewer() { return field<uint64_t>(image_base(),viewer_root_rva); }
uint64_t WINAPI PdxV3DiagnosticsGetViewer() {
    using Getter = uint64_t* (__fastcall*)();
    return *reinterpret_cast<Getter>(image_base()+viewer_getter_rva)();
}
DWORD WINAPI PdxV3DiagnosticsReadStatus(uint64_t viewer,PdxV3DiagnosticStatus* status) {
    if (!status || status->size!=sizeof(*status)) return ERROR_INVALID_PARAMETER;
    *status={sizeof(*status),ERROR_NOT_READY,0,0,-1,-1,0,0,viewer};
    if (!viewer) return status->error;
    if (!valid_viewer(viewer)) return status->error=ERROR_INVALID_DATA;
    ReaderLock lock(viewer);
    snapshot_fields(viewer,*status);
    return status->error=ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DiagnosticsRetainEntry(uint64_t entry) {
    if (!valid_entry(entry)) return ERROR_INVALID_DATA;
    InterlockedIncrement(reinterpret_cast<volatile LONG*>(entry+8));
    return ERROR_SUCCESS;
}
void WINAPI PdxV3DiagnosticsReleaseEntry(uint64_t entry) {
    if (!entry) return;
    if (InterlockedExchangeAdd(reinterpret_cast<volatile LONG*>(entry+8),-1)==1) {
        using Destructor = void (__fastcall*)(void*,uint32_t);
        const auto table=field<uintptr_t>(entry,0);
        reinterpret_cast<Destructor>(field<uintptr_t>(table,0))(reinterpret_cast<void*>(entry),1);
    }
}
DWORD WINAPI PdxV3DiagnosticsPinRecords(uint64_t viewer,int32_t after_sequence,
    uint32_t max_records,uint64_t* entries,uint32_t entry_capacity,PdxV3DiagnosticSnapshot* snapshot) {
    if (!snapshot || snapshot->size!=sizeof(*snapshot) || after_sequence<-1
        || (entry_capacity && !entries)) return ERROR_INVALID_PARAMETER;
    *snapshot={sizeof(*snapshot),ERROR_NOT_READY,0,0,0,0,-1,-1,0,0,viewer};
    if (!viewer) return snapshot->error;
    if (!valid_viewer(viewer)) return snapshot->error=ERROR_INVALID_DATA;
    ReaderLock lock(viewer);
    PdxV3DiagnosticStatus status={sizeof(status)};
    snapshot_fields(viewer,status);
    snapshot->flags=status.flags; snapshot->store_count=status.store_count;
    snapshot->first_sequence=status.first_sequence; snapshot->last_sequence=status.last_sequence;
    snapshot->native_limit=status.native_limit; snapshot->native_prune=status.native_prune;
    int32_t effective_after=after_sequence;
    if (after_sequence>status.last_sequence) {
        snapshot->flags|=PDX_V3_DIAGNOSTIC_RESET;
        effective_after=-1;
    }
    if (status.store_count && int64_t(status.first_sequence)>int64_t(effective_after)+1)
        snapshot->flags|=PDX_V3_DIAGNOSTIC_GAP;
    const auto data=field<uintptr_t>(viewer,0x28);
    const uint32_t capacity=std::min(max_records,entry_capacity);
    for (uint32_t i=0;i<status.store_count;++i) {
        const auto entry=field<uint64_t>(data,8ull*i);
        if (!valid_entry(entry)) return snapshot->error=ERROR_INVALID_DATA;
        if (field<int32_t>(entry,0x10)<=effective_after) continue;
        ++snapshot->eligible_count;
        if (snapshot->pinned_count<capacity) {
            InterlockedIncrement(reinterpret_cast<volatile LONG*>(entry+8));
            entries[snapshot->pinned_count++]=entry;
        }
    }
    return snapshot->error=ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DiagnosticsCopyRecord(uint64_t entry,void* output,uint32_t capacity,uint32_t* required) {
    if (!required || (capacity && !output)) return ERROR_INVALID_PARAMETER;
    *required=0;
    if (!valid_entry(entry)) return ERROR_INVALID_DATA;
    const auto& label=*reinterpret_cast<const NativeString*>(entry+0x30);
    const auto& source=*reinterpret_cast<const NativeString*>(entry+0x50);
    const auto& message=*reinterpret_cast<const NativeString*>(entry+0x70);
    const uint64_t total=sizeof(PdxV3DiagnosticRecord)+label.length+source.length+message.length;
    if (total>std::numeric_limits<uint32_t>::max()) return ERROR_ARITHMETIC_OVERFLOW;
    *required=static_cast<uint32_t>(total);
    if (capacity<total) return ERROR_MORE_DATA;
    PdxV3DiagnosticRecord record={*required,field<int32_t>(entry,0x10),field<uint32_t>(entry,0x14),
        field<uint32_t>(entry,0x18),field<uint32_t>(entry,0x1c),field<uint32_t>(entry,0x20),
        field<uint64_t>(entry,0x28),static_cast<uint32_t>(label.length),static_cast<uint32_t>(source.length),
        static_cast<uint32_t>(message.length),0};
    auto* cursor=static_cast<unsigned char*>(output);
    std::memcpy(cursor,&record,sizeof(record)); cursor+=sizeof(record);
    for (const NativeString* value : {&label,&source,&message}) {
        if (value->length) std::memcpy(cursor,value->data(),static_cast<size_t>(value->length));
        cursor+=value->length;
    }
    return ERROR_SUCCESS;
}
void WINAPI PdxV3DiagnosticsInterface(void* application,void* opaque) {
    auto* request=static_cast<PdxV3DiagnosticsRequest*>(opaque);
    if (!request || request->size<sizeof(*request)) return;
    request->error=ERROR_INVALID_PARAMETER;
    if (uint64_t(sizeof(*request))+request->output_capacity!=request->size
        || request->after_sequence<-1 || (request->operation!=1 && request->operation!=2)) return;
    request->flags=0; request->first_sequence=-1; request->last_sequence=-1;
    request->next_sequence=request->after_sequence;
    request->output_size=0; request->output_required=0; request->returned_count=0;
    request->eligible_count=0; request->store_count=0; request->has_more=0;
    request->native_limit=0; request->native_prune=0;
    request->execution_thread_id=GetCurrentThreadId();
    request->logging_ready=field<uint8_t>(image_base(),logging_ready_rva);
    request->subscription_coverage_known=0;
    request->viewer=PdxV3DiagnosticsViewer(); request->viewer_available=request->viewer?1u:0u;
    if (request->expected_viewer && request->expected_viewer!=request->viewer) {
        request->flags|=PDX_V3_DIAGNOSTIC_RESET; request->next_sequence=-1;
    }
    if (!application || reinterpret_cast<uintptr_t>(application)!=field<uintptr_t>(image_base(),application_root_rva)) {
        request->error=ERROR_NOT_READY; return;
    }
    try {
        if (request->operation==1) {
            PdxV3DiagnosticStatus status={sizeof(status)};
            PdxV3DiagnosticsReadStatus(request->viewer,&status); publish_status(*request,status);
            if (request->after_sequence>status.last_sequence) {
                request->flags|=PDX_V3_DIAGNOSTIC_RESET; request->next_sequence=-1;
            }
            return;
        }
        PinnedEntries pinned(request->max_records);
        const int32_t after=(request->flags&PDX_V3_DIAGNOSTIC_RESET)?-1:request->after_sequence;
        PdxV3DiagnosticsPinRecords(request->viewer,after,request->max_records,
            pinned.addresses.data(),request->max_records,&pinned.snapshot);
        const auto& snapshot=pinned.snapshot;
        request->error=snapshot.error; request->flags|=snapshot.flags;
        request->store_count=snapshot.store_count; request->eligible_count=snapshot.eligible_count;
        request->first_sequence=snapshot.first_sequence; request->last_sequence=snapshot.last_sequence;
        request->native_limit=snapshot.native_limit; request->native_prune=snapshot.native_prune;
        if (request->flags&PDX_V3_DIAGNOSTIC_RESET) request->next_sequence=-1;
        if (request->error) return;
        auto* output=reinterpret_cast<unsigned char*>(request+1);
        uint64_t required_total=0;
        bool short_output=false;
        for (uint32_t i=0;i<snapshot.pinned_count;++i) {
            uint32_t bytes=0;
            const DWORD size_error=PdxV3DiagnosticsCopyRecord(pinned.addresses[i],nullptr,0,&bytes);
            if (size_error!=ERROR_MORE_DATA) { request->error=size_error; return; }
            required_total+=bytes;
            if (required_total>std::numeric_limits<uint32_t>::max()) {
                request->error=ERROR_ARITHMETIC_OVERFLOW; return;
            }
            if (short_output || bytes>request->output_capacity-request->output_size) {
                short_output=true; continue;
            }
            request->error=PdxV3DiagnosticsCopyRecord(pinned.addresses[i],output+request->output_size,bytes,&bytes);
            if (request->error) return;
            request->output_size+=bytes; ++request->returned_count;
            request->next_sequence=field<int32_t>(pinned.addresses[i],0x10);
        }
        request->output_required=static_cast<uint32_t>(required_total);
        request->has_more=snapshot.eligible_count>request->returned_count?1u:0u;
        if (short_output) {
            request->flags|=PDX_V3_DIAGNOSTIC_OUTPUT_TRUNCATED; request->error=ERROR_MORE_DATA;
        }
    } catch (const std::bad_alloc&) { request->error=ERROR_NOT_ENOUGH_MEMORY; }
      catch (...) { request->error=ERROR_UNHANDLED_EXCEPTION; }
}
