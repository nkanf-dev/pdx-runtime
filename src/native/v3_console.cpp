#define NOMINMAX
#include "v3_console.h"
#include <algorithm>
#include <cstddef>
#include <cstring>
#include <vector>

namespace {
constexpr uintptr_t manager_get_rva = 0x3af13c0;
constexpr uintptr_t dispatch_rva = 0x3af1740;
constexpr uintptr_t parse_rva = 0x3b12c60;
constexpr uintptr_t string_construct_rva = 0x61c420;
constexpr uintptr_t string_destroy_rva = 0x61f810;
constexpr uintptr_t object_list_rva = 0x3af3610;
constexpr uintptr_t object_lock_rva = 0x59b2e50;
constexpr uintptr_t app_root_rva = 0x592d128;
uintptr_t base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T field(uintptr_t p, size_t n) { return *reinterpret_cast<const T*>(p+n); }
using StringConstruct = void* (__fastcall*)(PdxV3ConsoleString*, const char*, uint64_t);
using StringDestroy = void (__fastcall*)(PdxV3ConsoleString*);
using Parse = void* (__fastcall*)(PdxV3ConsoleArguments*, const PdxV3ConsoleString*, uint8_t);
using Dispatch = void* (__fastcall*)(void*, PdxV3ConsoleNativeResult*, const PdxV3ConsoleArguments*);
using Exact = void* (__fastcall*)(PdxV3ConsoleNativeResult*, const PdxV3ConsoleArguments*, void*);
using Getter = void* (__fastcall*)();
using Release = void (__fastcall*)(void*, void*);
using ObjectExact = void* (__fastcall*)(void*, PdxV3ConsoleNativeResult*, const PdxV3ConsoleArguments*);
struct WriteLock {
    volatile LONG* lock;
    explicit WriteLock(uintptr_t p) : lock(reinterpret_cast<volatile LONG*>(p)) {
        while (InterlockedBitTestAndSet(lock,0)) YieldProcessor();
        while (*lock>1) YieldProcessor();
    }
    ~WriteLock() { InterlockedAnd(lock,~1L); }
};
struct ReadLock {
    volatile LONG* lock;
    explicit ReadLock(uintptr_t p) : lock(reinterpret_cast<volatile LONG*>(p)) {
        while ((InterlockedExchangeAdd(lock,2)&1)!=0) {
            InterlockedExchangeAdd(lock,-2);
            while ((*lock&1)!=0) YieldProcessor();
        }
    }
    ~ReadLock() { InterlockedExchangeAdd(lock,-2); }
};
struct OwnedString {
    PdxV3ConsoleString value = {{0,0},0,15};
    OwnedString() = default;
    OwnedString(const char* p, uint64_t n) { PdxV3ConsoleConstructString(&value,p,n); }
    ~OwnedString() { PdxV3ConsoleDestroyString(&value); }
};
struct OwnedArguments {
    PdxV3ConsoleArguments value = {};
    ~OwnedArguments() { PdxV3ConsoleDestroyArguments(&value); }
};
struct OwnedResult {
    PdxV3ConsoleNativeResult value = {0,{},{{0,0},0,15}};
    ~OwnedResult() { PdxV3ConsoleDestroyString(&value.message); }
};
struct ExplicitArguments {
    std::vector<PdxV3ConsoleString> strings;
    explicit ExplicitArguments(uint32_t count) : strings(count, {{0,0},0,15}) {}
    ~ExplicitArguments() {
        for (auto& string:strings)
            reinterpret_cast<StringDestroy>(base()+string_destroy_rva)(&string);
    }
    PdxV3ConsoleArguments view() const {
        const auto count=static_cast<uint32_t>(strings.size());
        return {reinterpret_cast<uint64_t>(strings.data()),count,count,0};
    }
};
const char* string_data(const PdxV3ConsoleString& s) {
    return s.capacity>15 ? reinterpret_cast<const char*>(s.storage[0]) : reinterpret_cast<const char*>(s.storage);
}
struct Output {
    std::vector<unsigned char> bytes;
    template<class T> void append(const T& value) {
        const auto* p=reinterpret_cast<const unsigned char*>(&value);
        bytes.insert(bytes.end(),p,p+sizeof(value));
    }
    void text(const PdxV3ConsoleString& value) {
        const auto length=static_cast<uint32_t>(value.length);
        append(length);
        const auto* p=reinterpret_cast<const unsigned char*>(string_data(value));
        bytes.insert(bytes.end(),p,p+length);
    }
};
void record_visitor(uint64_t object,uint32_t kind,void* opaque) {
    auto& out=*static_cast<Output*>(opaque);
    const auto start=out.bytes.size();
    PdxV3ConsoleCommandRecord rec = {};
    rec.kind=kind;
    rec.object=object;
    if (kind==1) {
        rec.developer_allowed=field<uint8_t>(object,0);
        rec.alias_count=std::min(field<uint32_t>(object,40),3u);
        rec.parameter_count=std::min(field<uint32_t>(object,200),10u);
        rec.callback=field<uintptr_t>(object,176);
        rec.context=field<uintptr_t>(object,192);
    } else {
        rec.developer_allowed=field<uint8_t>(object,136);
        rec.callback=field<uintptr_t>(field<uintptr_t>(object,0),24);
        rec.context=object;
    }
    out.append(rec);
    out.text(*reinterpret_cast<const PdxV3ConsoleString*>(object+(kind==1?8:48)));
    out.text(*reinterpret_cast<const PdxV3ConsoleString*>(object+(kind==1?144:80)));
    for (uint32_t i=0;i<rec.alias_count;++i)
        out.text(*reinterpret_cast<const PdxV3ConsoleString*>(object+48+32*i));
    for (uint32_t i=0;i<rec.parameter_count;++i)
        out.text(*reinterpret_cast<const PdxV3ConsoleString*>(object+208+32*i));
    rec.size=static_cast<uint32_t>(out.bytes.size()-start);
    std::memcpy(out.bytes.data()+start,&rec,sizeof(rec));
}
void copy_output(PdxV3ConsoleRequest& request,const void* p,uint32_t length) {
    request.output_required=length;
    request.output_size=std::min(length,request.output_capacity);
    if (request.output_size) std::memcpy(reinterpret_cast<unsigned char*>(&request)+sizeof(request)+request.input_size,p,request.output_size);
    if (length>request.output_capacity) request.error=ERROR_MORE_DATA;
}
static_assert(sizeof(PdxV3ConsoleString)==32);
static_assert(sizeof(PdxV3ConsoleArguments)==24);
static_assert(sizeof(PdxV3ConsoleNativeResult)==40);
static_assert(sizeof(PdxV3ConsoleRequest)==96);
static_assert(sizeof(PdxV3ConsoleCommandRecord)==48);
static_assert(sizeof(PdxV3ConsoleDescriptor)==528);
struct OwnedDescriptor {
    PdxV3ConsoleDescriptor value;
    OwnedDescriptor() { PdxV3ConsoleInitializeDescriptor(&value); }
    ~OwnedDescriptor() { PdxV3ConsoleDestroyDescriptor(&value); }
};
bool construct_wire_string(const unsigned char*& cursor,const unsigned char* end,PdxV3ConsoleString& output) {
    if (end-cursor<4) return false;
    uint32_t length;std::memcpy(&length,cursor,4);cursor+=4;
    if (static_cast<uint64_t>(end-cursor)<length || std::memchr(cursor,0,length)) return false;
    PdxV3ConsoleConstructString(&output,reinterpret_cast<const char*>(cursor),length);
    cursor+=length;return true;
}
}

void WINAPI PdxV3ConsoleInitializeDescriptor(PdxV3ConsoleDescriptor* descriptor) {
    *descriptor={};
    descriptor->name.capacity=15;descriptor->description.capacity=15;
    for (auto& value:descriptor->aliases) value.capacity=15;
    for (auto& value:descriptor->parameters) value.capacity=15;
}
void WINAPI PdxV3ConsoleDestroyDescriptor(PdxV3ConsoleDescriptor* descriptor) {
    using DestroyDescriptor = void (__fastcall*)(PdxV3ConsoleDescriptor*);
    reinterpret_cast<DestroyDescriptor>(base()+0x7c9c50)(descriptor);
}
void WINAPI PdxV3ConsoleRegisterDescriptor(uint64_t manager,const PdxV3ConsoleDescriptor* descriptor) {
    WriteLock lock(manager+168);
    using Register = void (__fastcall*)(void*, const PdxV3ConsoleDescriptor*);
    reinterpret_cast<Register>(base()+0x3af3090)(reinterpret_cast<void*>(manager),descriptor);
}
void WINAPI PdxV3ConsoleUnregister(uint64_t manager,const char* name) {
    using Unregister = void (__fastcall*)(void*, const char*);
    reinterpret_cast<Unregister>(base()+0x3af14b0)(reinterpret_cast<void*>(manager),name);
}

uint64_t WINAPI PdxV3ConsoleManager() { return reinterpret_cast<uint64_t>(reinterpret_cast<Getter>(base()+manager_get_rva)()); }
void WINAPI PdxV3ConsoleConstructString(PdxV3ConsoleString* out,const char* text,uint64_t length) {
    reinterpret_cast<StringConstruct>(base()+string_construct_rva)(out,text,length);
}
void WINAPI PdxV3ConsoleDestroyString(PdxV3ConsoleString* value) {
    reinterpret_cast<StringDestroy>(base()+string_destroy_rva)(value);
}
void WINAPI PdxV3ConsoleParse(PdxV3ConsoleArguments* out,const PdxV3ConsoleString* text) {
    reinterpret_cast<Parse>(base()+parse_rva)(out,text,32);
}
void WINAPI PdxV3ConsoleDestroyArguments(PdxV3ConsoleArguments* args) {
    if (!args->data) return;
    auto* strings=reinterpret_cast<PdxV3ConsoleString*>(args->data);
    for (uint32_t i=0;i<args->count;++i) PdxV3ConsoleDestroyString(strings+i);
    args->count=0;
    auto* allocator=reinterpret_cast<void*>(args->allocator);
    const auto table=*reinterpret_cast<uintptr_t*>(allocator);
    reinterpret_cast<Release>(field<uintptr_t>(table,16))(allocator,reinterpret_cast<void*>(args->data));
    args->data=0; args->capacity=0;
}
void WINAPI PdxV3ConsoleExecuteArguments(uint64_t manager,PdxV3ConsoleNativeResult* out,const PdxV3ConsoleArguments* args) {
    reinterpret_cast<Dispatch>(base()+dispatch_rva)(reinterpret_cast<void*>(manager),out,args);
}
void WINAPI PdxV3ConsoleExecuteText(uint64_t manager,PdxV3ConsoleNativeResult* out,const PdxV3ConsoleString* text) {
    OwnedArguments args;
    PdxV3ConsoleParse(&args.value,text);
    PdxV3ConsoleExecuteArguments(manager,out,&args.value);
}
void WINAPI PdxV3ConsoleInvokeExact(uint64_t callback,uint64_t context,PdxV3ConsoleNativeResult* out,const PdxV3ConsoleArguments* args) {
    reinterpret_cast<Exact>(callback)(out,args,reinterpret_cast<void*>(context));
}
void WINAPI PdxV3ConsoleInvokeObject(uint64_t object,PdxV3ConsoleNativeResult* out,const PdxV3ConsoleArguments* args) {
    const auto table=field<uintptr_t>(object,0);
    reinterpret_cast<ObjectExact>(field<uintptr_t>(table,24))(reinterpret_cast<void*>(object),out,args);
}
void WINAPI PdxV3ConsoleEnumerate(uint64_t manager,PdxV3ConsoleVisitor visitor,void* opaque) {
    {
        ReadLock lock(manager+168);
        const auto data=field<uintptr_t>(manager,0);
        const auto count=field<uint32_t>(manager,12);
        for (uint32_t i=0;i<count;++i) visitor(data+528ull*i,1,opaque);
    }
    {
        ReadLock lock(base()+object_lock_rva);
        const auto list=reinterpret_cast<uintptr_t>(reinterpret_cast<Getter>(base()+object_list_rva)());
        const auto data=field<uintptr_t>(list,0);
        const auto count=field<uint32_t>(list,12);
        for (uint32_t i=0;i<count;++i) visitor(field<uintptr_t>(data,8ull*i),2,opaque);
    }
}
void WINAPI PdxV3ConsoleInterface(void* application,void* opaque) {
    auto* request=static_cast<PdxV3ConsoleRequest*>(opaque);
    if (!request || request->size<sizeof(*request)) return;
    request->error=ERROR_INVALID_PARAMETER;
    if (uint64_t(sizeof(*request))+request->input_size+request->output_capacity!=request->size) return;
    request->native_success=0; request->output_size=0; request->output_required=0;
    request->count=0; request->total_count=0; request->argument_count=0; request->records_bytes=0;
    request->execution_thread_id=GetCurrentThreadId();
    request->application=reinterpret_cast<uint64_t>(application);
    if (!application || request->application!=field<uintptr_t>(base(),app_root_rva)) {
        request->error=ERROR_NOT_READY; return;
    }
    try {
        request->manager=PdxV3ConsoleManager();
        request->release_restricted=field<uint8_t>(request->manager,240);
        request->multiplayer_or_ironman=field<uint8_t>(request->manager,241);
        request->error=ERROR_SUCCESS;
        if (request->action==2) {
            Output output;
            PdxV3ConsoleEnumerate(request->manager,record_visitor,&output);
            size_t cursor=0;
            while (cursor<output.bytes.size()) {
                PdxV3ConsoleCommandRecord record;
                std::memcpy(&record,output.bytes.data()+cursor,sizeof(record));
                ++request->total_count;
                if (cursor+record.size<=request->output_capacity) ++request->count;
                cursor+=record.size;
            }
            request->records_bytes=static_cast<uint32_t>(output.bytes.size());
            copy_output(*request,output.bytes.data(),static_cast<uint32_t>(output.bytes.size()));
            return;
        }
        if (request->action==6) {
            const auto* cursor=reinterpret_cast<const unsigned char*>(request+1);
            const auto* end=cursor+request->input_size;
            uint32_t metadata[3];
            if (end-cursor<12 || !request->exact_callback) { request->error=ERROR_INVALID_PARAMETER;return; }
            std::memcpy(metadata,cursor,12);cursor+=12;
            if (metadata[0]>1 || metadata[1]>3 || metadata[2]>10) { request->error=ERROR_INVALID_PARAMETER;return; }
            OwnedDescriptor descriptor;
            descriptor.value.developer_allowed=static_cast<uint8_t>(metadata[0]);
            descriptor.value.alias_count=metadata[1];descriptor.value.parameter_count=metadata[2];
            descriptor.value.callback=request->exact_callback;descriptor.value.context=request->exact_context;
            if (!construct_wire_string(cursor,end,descriptor.value.name)
                || !descriptor.value.name.length || !construct_wire_string(cursor,end,descriptor.value.description)) {
                request->error=ERROR_INVALID_PARAMETER;return;
            }
            for (uint32_t i=0;i<metadata[1];++i)
                if (!construct_wire_string(cursor,end,descriptor.value.aliases[i])) { request->error=ERROR_INVALID_PARAMETER;return; }
            for (uint32_t i=0;i<metadata[2];++i)
                if (!construct_wire_string(cursor,end,descriptor.value.parameters[i])) { request->error=ERROR_INVALID_PARAMETER;return; }
            if (cursor!=end) { request->error=ERROR_INVALID_PARAMETER;return; }
            PdxV3ConsoleRegisterDescriptor(request->manager,&descriptor.value);
            request->native_success=1;return;
        }
        if (request->action==7) {
            const auto* input=reinterpret_cast<const char*>(request+1);
            if (!request->input_size || std::memchr(input,0,request->input_size)) { request->error=ERROR_INVALID_PARAMETER;return; }
            OwnedString name(input,request->input_size);
            PdxV3ConsoleUnregister(request->manager,string_data(name.value));
            request->native_success=1;return;
        }
        if (request->action==8) {
            const auto* cursor=reinterpret_cast<const unsigned char*>(request+1);
            const auto* end=cursor+request->input_size;
            if (end-cursor<4) { request->error=ERROR_INVALID_PARAMETER;return; }
            uint32_t count;std::memcpy(&count,cursor,4);cursor+=4;
            if (!count || count>static_cast<uint64_t>(end-cursor)/4) {
                request->error=ERROR_INVALID_PARAMETER;return;
            }
            ExplicitArguments owned(count);
            for (auto& string:owned.strings) {
                if (end-cursor<4) { request->error=ERROR_INVALID_PARAMETER;return; }
                uint32_t length;std::memcpy(&length,cursor,4);cursor+=4;
                if (static_cast<uint64_t>(end-cursor)<length || std::memchr(cursor,0,length)) {
                    request->error=ERROR_INVALID_PARAMETER;return;
                }
                reinterpret_cast<StringConstruct>(base()+string_construct_rva)(
                    &string,reinterpret_cast<const char*>(cursor),length);
                cursor+=length;
            }
            if (cursor!=end || !owned.strings[0].length) {
                request->error=ERROR_INVALID_PARAMETER;return;
            }
            const auto arguments=owned.view();
            request->argument_count=count;
            OwnedResult result;
            // The native dispatcher reads this borrowed CString array. Each
            // string stays owned above; its vector is not a native allocator.
            reinterpret_cast<Dispatch>(base()+dispatch_rva)(
                reinterpret_cast<void*>(request->manager),&result.value,&arguments);
            request->native_success=result.value.success;
            copy_output(*request,string_data(result.value.message),static_cast<uint32_t>(result.value.message.length));
            return;
        }
        const auto* input=reinterpret_cast<const char*>(request+1);
        if (std::memchr(input,0,request->input_size)) {
            request->error=ERROR_INVALID_PARAMETER; return;
        }
        OwnedString text(input,request->input_size);
        OwnedArguments args;
        if (request->input_size || request->action==3) {
            PdxV3ConsoleParse(&args.value,&text.value);
        } else {
            using ConstructArray = void* (__fastcall*)(PdxV3ConsoleArguments*);
            reinterpret_cast<ConstructArray>(base()+0x61c180)(&args.value);
        }
        request->argument_count=args.value.count;
        if (request->action==3) {
            Output output;
            const auto* values=reinterpret_cast<const PdxV3ConsoleString*>(args.value.data);
            for (uint32_t i=0;i<args.value.count;++i) output.text(values[i]);
            copy_output(*request,output.bytes.data(),static_cast<uint32_t>(output.bytes.size()));
            return;
        }
        OwnedResult result;
        if (request->action==1) {
            if (!args.value.count || !args.value.data
                || !reinterpret_cast<const PdxV3ConsoleString*>(args.value.data)->length) {
                request->error=ERROR_INVALID_PARAMETER; return;
            }
            PdxV3ConsoleExecuteArguments(request->manager,&result.value,&args.value);
        } else if (request->action==4 && request->exact_callback) {
            PdxV3ConsoleInvokeExact(request->exact_callback,request->exact_context,&result.value,&args.value);
        } else if (request->action==5 && request->exact_context) {
            PdxV3ConsoleInvokeObject(request->exact_context,&result.value,&args.value);
        } else { request->error=ERROR_INVALID_PARAMETER; return; }
        request->native_success=result.value.success;
        copy_output(*request,string_data(result.value.message),static_cast<uint32_t>(result.value.message.length));
    } catch (...) { request->error=ERROR_UNHANDLED_EXCEPTION; }
}


namespace {
constexpr uintptr_t player_app_root_rva = 0x592d128;
constexpr uintptr_t player_idler_root_rva = 0x592d0e0;
constexpr uintptr_t player_idler_vtable_rva = 0x4431500;
constexpr uintptr_t player_logic_vtable_rva = 0x4436a08;
constexpr uintptr_t player_getter_rva = 0x33bdb80;
constexpr uintptr_t player_tag_resolver_rva = 0x10ddf00;
constexpr uintptr_t player_country_root_rva = 0x5964e20;
constexpr uintptr_t player_country_fallback_rva = 0x5964e18;
constexpr uintptr_t player_country_resolver_rva = 0x7bb480;
constexpr uintptr_t player_country_playable_vtable_rva = 0x4494d08;
constexpr uint32_t player_invalid_id = 0xffffffff;
using PlayerGet = void* (__fastcall*)(void*);
using PlayableGet = void* (__fastcall*)(const void*);
using PlayableId = uint32_t (__fastcall*)(const void*);
using CountryResolve = void* (__fastcall*)(const uint32_t*);
using PlayerTagResolve = void* (__fastcall*)(const PdxV3ConsoleString*);
using PlayerCountryValid = bool (__fastcall*)(const void*);
using PlayerGuardEnter = void* (__fastcall*)(void*,uint32_t,int32_t,const char*);
using PlayerGuardLeave = void (__fastcall*)(void*);
using PlayerMutexEnter = int (__fastcall*)(void*);
using PlayerMutexLeave = void (__fastcall*)(void*);
struct PlayerAccess {
    alignas(16) unsigned char storage[80]={};
    explicit PlayerAccess(uint32_t mode) {
        reinterpret_cast<PlayerGuardEnter>(base()+0x33bd640)(storage,mode,0,"pdx-runtime/native/v3_console_player_state");
    }
    ~PlayerAccess() { reinterpret_cast<PlayerGuardLeave>(base()+0x33bd750)(storage); }
};
struct PlayerMutex {
    void* storage;
    bool entered;
    explicit PlayerMutex(uint64_t application) : storage(reinterpret_cast<void*>(application+0x590)),entered(false) {
        entered=reinterpret_cast<PlayerMutexEnter>(base()+0x418ef70)(storage)==0;
    }
    ~PlayerMutex() { if (entered) reinterpret_cast<PlayerMutexLeave>(base()+0x418ef78)(storage); }
};
struct PlayableReferenceCopy {
    uint64_t database;
    uint32_t id;
    uint32_t padding;
};
DWORD player_context(uint64_t application,PdxV3ConsolePlayerState& result) {
    result.application=application;
    if (!application || application!=field<uint64_t>(base(),player_app_root_rva)
        || field<uint8_t>(application,0x258)) return ERROR_NOT_READY;
    result.world=field<uint64_t>(application,0x608);
    result.idler=field<uint64_t>(application,0x1c0);
    if (!result.world || !result.idler || result.idler!=field<uint64_t>(base(),player_idler_root_rva)
        || field<uintptr_t>(result.idler,0)!=base()+player_idler_vtable_rva
        || field<uintptr_t>(result.idler,0x18)!=application) return ERROR_NOT_READY;
    result.local_machine_id=field<uint32_t>(base(),0x595735c);
    result.application_player_id=field<uint32_t>(application,0x580);
    result.observer_global=field<uint8_t>(base(),0x595720a);
    result.paused=field<uint8_t>(result.idler,0x20);
    const auto logic=field<uintptr_t>(result.idler,8);
    if (!logic || field<uintptr_t>(logic,0)!=base()+player_logic_vtable_rva) return ERROR_NOT_READY;
    result.simulation_started=field<uint8_t>(logic,0xa8);
    return ERROR_SUCCESS;
}
DWORD player_country_reference(const PlayableReferenceCopy& reference,uint64_t* country) {
    *country=0;
    if (!reference.database || reference.id==player_invalid_id) return ERROR_NOT_FOUND;
    if (!field<uintptr_t>(base(),player_country_root_rva)) return ERROR_NOT_READY;
    const auto playable=reinterpret_cast<uintptr_t>(reinterpret_cast<PlayableGet>(base()+0x6c5eb0)(&reference));
    if (!playable || field<uintptr_t>(playable,0)!=base()+player_country_playable_vtable_rva)
        return ERROR_NOT_FOUND;
    const auto table=field<uintptr_t>(playable,0);
    const uint32_t full_id=reinterpret_cast<PlayableId>(field<uintptr_t>(table,8))(reinterpret_cast<void*>(playable));
    if (full_id!=reference.id) return ERROR_NOT_FOUND;
    const auto origin=reinterpret_cast<uintptr_t>(reinterpret_cast<CountryResolve>(base()+player_country_resolver_rva)(&full_id));
    if (!origin || origin==field<uintptr_t>(base(),player_country_fallback_rva)
        || origin+8!=playable || field<uint32_t>(origin,0x18)!=full_id) return ERROR_NOT_FOUND;
    *country=origin;
    return ERROR_SUCCESS;
}
static_assert(sizeof(PdxV3ConsolePlayerState)==160);
static_assert(offsetof(PdxV3ConsolePlayerState,application)==80);
}

uint64_t WINAPI PdxV3ConsoleResolveTag(const PdxV3ConsoleString* tag) noexcept(false) {
    return reinterpret_cast<uint64_t>(reinterpret_cast<PlayerTagResolve>(base()+player_tag_resolver_rva)(tag));
}
DWORD WINAPI PdxV3ConsoleReadPlayerState(uint64_t application,PdxV3ConsolePlayerState* result) noexcept(false) {
    if (!result || result->size!=sizeof(*result)) return ERROR_INVALID_PARAMETER;
    PlayerAccess scope(1);
    result->error=player_context(application,*result);
    if (result->error) return result->error;
    const auto candidate=reinterpret_cast<uintptr_t>(reinterpret_cast<PlayerGet>(base()+player_getter_rva)(reinterpret_cast<void*>(result->idler)));
    if (!candidate) return result->error=ERROR_NOT_FOUND;
    PlayableReferenceCopy controlled={0,player_invalid_id,0},selected={0,player_invalid_id,0};
    {
        PlayerMutex mutex(application);
        if (!mutex.entered) return result->error=ERROR_LOCK_FAILED;
        // Getter returns after releasing this same mutex. Confirm it still owns
        // the current local entry before copying; no borrowed Player is retained.
        const auto data=field<uintptr_t>(application,0x568);
        const auto count=field<uint32_t>(application,0x574);
        const auto local_id=field<uint32_t>(application,0x580);
        bool current=false;
        for (uint32_t i=0;i<count;++i) {
            const auto player=field<uintptr_t>(data,8ull*i);
            if (player==candidate && field<uint32_t>(player,0x70)==local_id) { current=true;break; }
        }
        if (!current) return result->error=ERROR_NOT_FOUND;
        result->player=candidate;
        result->application_player_id=local_id;
        result->player_machine_id=field<uint32_t>(candidate,0x70);
        result->player_flags_raw=field<uint8_t>(candidate,0x48);
        result->observer_enabled=(result->player_flags_raw&0x20)?1u:0u;
        controlled={field<uint64_t>(candidate,0x50),field<uint32_t>(candidate,0x58),0};
        selected={field<uint64_t>(candidate,0x60),field<uint32_t>(candidate,0x68),0};
    }
    result->controlled_ref_database=controlled.database;
    result->controlled_country_id=controlled.id;
    result->selected_ref_database=selected.database;
    result->selected_country_id=selected.id;
    result->controlled_country_error=player_country_reference(controlled,&result->controlled_country);
    result->selected_country_error=player_country_reference(selected,&result->selected_country);
    return result->error=ERROR_SUCCESS;
}
void WINAPI PdxV3ConsolePlayerStateInterface(void* application,void* opaque) {
    auto* result=static_cast<PdxV3ConsolePlayerState*>(opaque);
    if (!result || result->size!=sizeof(*result)) return;
    const auto action=result->action,tag_bytes=result->tag_bytes;
    char tag[8];std::memcpy(tag,result->tag,8);
    *result={};result->size=sizeof(*result);result->action=action;result->tag_bytes=tag_bytes;
    std::memcpy(result->tag,tag,8);
    result->controlled_country_id=result->selected_country_id=result->target_country_id=player_invalid_id;
    result->controlled_country_error=result->selected_country_error=result->target_error=ERROR_NOT_READY;
    result->execution_thread_id=GetCurrentThreadId();
    try {
        if (action==1) {
            result->error=PdxV3ConsoleReadPlayerState(reinterpret_cast<uint64_t>(application),result);
        } else if (action==2 && tag_bytes==3 && !std::memchr(tag,0,3)) {
            // Same native access values and tag resolver used by real tag command.
            PlayerAccess scope(2);
            result->error=player_context(reinterpret_cast<uint64_t>(application),*result);
            if (result->error) return;
            OwnedString native_tag(tag,3);
            result->target_country=PdxV3ConsoleResolveTag(&native_tag.value);
            result->target_error=ERROR_NOT_FOUND;
            if (result->target_country && result->target_country!=field<uintptr_t>(base(),player_country_fallback_rva)) {
                const auto id=field<uint32_t>(result->target_country,0x18);
                const auto resolved=reinterpret_cast<uintptr_t>(reinterpret_cast<CountryResolve>(base()+player_country_resolver_rva)(&id));
                if (id!=player_invalid_id && resolved==result->target_country) {
                    result->target_country_id=id;result->target_error=ERROR_SUCCESS;
                    const auto persistent=resolved+0x10;
                    const auto table=field<uintptr_t>(persistent,0);
                    const bool native_valid=reinterpret_cast<PlayerCountryValid>(field<uintptr_t>(table,8))(reinterpret_cast<const void*>(persistent));
                    result->target_control_eligible=native_valid && field<uint32_t>(resolved,0xca4)!=0?1u:0u;
                }
            }
            result->error=ERROR_SUCCESS;
        } else result->error=ERROR_INVALID_PARAMETER;
    } catch (...) { result->error=ERROR_UNHANDLED_EXCEPTION; }
}


namespace {
constexpr uintptr_t ai_actor_root_rva = 0x5967270;
constexpr uintptr_t ai_actor_fallback_rva = 0x59646e8;
constexpr uintptr_t ai_actor_resolver_rva = 0xc5a4b0;
constexpr uintptr_t ai_actor_vtable_rva = 0x44f0930;
constexpr uintptr_t ai_game_state_vtable_rva = 0x443a438;
constexpr uintptr_t ai_payload_country_manager_vtable_rva = 0x44adfc8;
constexpr uintptr_t ai_manager_vtable_rva = 0x44f4dd8;
constexpr uint32_t ai_invalid_id = 0xffffffff;
using AiResolve = void* (__fastcall*)(const uint32_t*);
struct AiInterfaceAccess {
    alignas(16) unsigned char storage[80]={};
    AiInterfaceAccess() {
        reinterpret_cast<PlayerGuardEnter>(base()+0x33bd640)(storage,1,0,"pdx-runtime/native/v3_console_ai_state");
    }
    ~AiInterfaceAccess() { reinterpret_cast<PlayerGuardLeave>(base()+0x33bd750)(storage); }
};
void initialize_ai_state(PdxV3ConsoleAiState& result,uint32_t country_id) {
    result={};result.size=sizeof(result);result.country_full_id=country_id;
    result.actor_full_id=result.owner_country_full_id=result.role_raw=ai_invalid_id;
    result.role_index=country_id&0xffffff;
    result.country_error=result.actor_error=result.manager_error=result.role_error=ERROR_NOT_READY;
    result.error=ERROR_NOT_READY;result.execution_thread_id=GetCurrentThreadId();
}
DWORD ai_context(uint64_t application,PdxV3ConsoleAiState& result) {
    result.application=application;
    if (!application || application!=field<uintptr_t>(base(),player_app_root_rva)
        || field<uint8_t>(application,0x258)) return ERROR_NOT_READY;
    result.world=field<uintptr_t>(application,0x608);
    result.idler=field<uintptr_t>(application,0x1c0);
    if (!result.world || !result.idler || result.idler!=field<uintptr_t>(base(),player_idler_root_rva)
        || field<uintptr_t>(result.idler,0)!=base()+player_idler_vtable_rva
        || field<uintptr_t>(result.idler,0x18)!=application) return ERROR_NOT_READY;
    // Application+608 is the CGameState/world origin (native ctor7da2b0).
    if (field<uintptr_t>(result.world,0)!=base()+ai_game_state_vtable_rva) return ERROR_INVALID_DATA;
    const auto logic=field<uintptr_t>(result.idler,8);
    if (!logic || field<uintptr_t>(logic,0)!=base()+player_logic_vtable_rva) return ERROR_NOT_READY;
    result.paused=field<uint8_t>(result.idler,0x20);
    result.simulation_started=field<uint8_t>(logic,0xa8);
    // The144B country_manager field is the first contained manager in the owned data
    // payload, not another CGameState. Native ctor1011230 begins with an inline
    // CCountryManager at payload+0, then CAIManager at payload+2680.
    result.country_manager=field<uintptr_t>(result.world,0x120);
    if (!result.country_manager) return ERROR_NOT_READY;
    if (field<uintptr_t>(result.country_manager,0)!=base()+ai_payload_country_manager_vtable_rva) return ERROR_INVALID_DATA;
    result.ai_manager=result.country_manager+0x2680;
    if (field<uintptr_t>(result.ai_manager,0)!=base()+ai_manager_vtable_rva) return ERROR_INVALID_DATA;
    return ERROR_SUCCESS;
}
DWORD ai_country(PdxV3ConsoleAiState& result) {
    if (result.country_full_id==ai_invalid_id) return ERROR_NOT_FOUND;
    if (!field<uintptr_t>(base(),player_country_root_rva)) return ERROR_NOT_READY;
    const auto country=reinterpret_cast<uintptr_t>(reinterpret_cast<CountryResolve>(base()+player_country_resolver_rva)(&result.country_full_id));
    if (!country || country==field<uintptr_t>(base(),player_country_fallback_rva)
        || field<uint32_t>(country,0x18)!=result.country_full_id) return ERROR_NOT_FOUND;
    if (field<uintptr_t>(country,8)!=base()+player_country_playable_vtable_rva) return ERROR_INVALID_DATA;
    result.country=country;result.actor_full_id=field<uint32_t>(country,0x20);
    return ERROR_SUCCESS;
}
DWORD ai_actor(PdxV3ConsoleAiState& result) {
    if (result.actor_full_id==ai_invalid_id) return ERROR_NOT_FOUND;
    if (!field<uintptr_t>(base(),ai_actor_root_rva)) return ERROR_NOT_READY;
    const auto actor=reinterpret_cast<uintptr_t>(reinterpret_cast<AiResolve>(base()+ai_actor_resolver_rva)(&result.actor_full_id));
    if (!actor || actor==field<uintptr_t>(base(),ai_actor_fallback_rva)
        || field<uint32_t>(actor,8)!=result.actor_full_id) return ERROR_NOT_FOUND;
    if (field<uintptr_t>(actor,0)!=base()+ai_actor_vtable_rva) return ERROR_INVALID_DATA;
    result.owner_country_full_id=field<uint32_t>(actor,0x50);
    if (result.owner_country_full_id!=result.country_full_id) return ERROR_INVALID_DATA;
    result.actor=actor;result.actor_enabled_raw=field<uint8_t>(actor,0x140);
    return ERROR_SUCCESS;
}
DWORD ai_role(PdxV3ConsoleAiState& result) {
    // Role belongs to the checked requested Country. For a valid actor, owner
    // full-ID equality above makes this identical to the native scheduler index.
    if (result.role_index>=result.role_count) return ERROR_NOT_FOUND;
    if (!result.role_data) return ERROR_INVALID_DATA;
    result.role_raw=field<uint32_t>(result.role_data,4ull*result.role_index);
    return ERROR_SUCCESS;
}
static_assert(sizeof(PdxV3ConsoleAiState)==144);
static_assert(offsetof(PdxV3ConsoleAiState,application)==80);
static_assert(offsetof(PdxV3ConsoleAiState,country_manager)==104);
static_assert(offsetof(PdxV3ConsoleAiState,ai_manager)==112);
}

DWORD WINAPI PdxV3ConsoleReadAiState(uint64_t application,PdxV3ConsoleAiState* result) noexcept(false) {
    if (!result || result->size!=sizeof(*result)) return ERROR_INVALID_PARAMETER;
    const auto country_id=result->country_full_id;
    initialize_ai_state(*result,country_id);
    AiInterfaceAccess scope;
    result->error=ai_context(application,*result);
    if (result->error) return result->error;
    result->manager_override_raw=field<uint8_t>(result->ai_manager,0x268);
    result->manager_error=ERROR_SUCCESS;
    result->role_data=field<uintptr_t>(result->world,0xe0);
    result->role_count=field<uint32_t>(result->world,0xec);
    result->country_error=ai_country(*result);
    if (!result->country_error) {
        result->actor_error=ai_actor(*result);
        result->role_error=ai_role(*result);
        // A bounds miss is an explicit native override-gated case. A missing
        // in-bounds data pointer is an error, not a fabricated role_raw==0.
        const bool native_role_case=!result->role_error || result->role_index>=result->role_count;
        if (!result->actor_error && native_role_case) {
            result->observed_predicate_valid=1;
            result->observed_predicate=result->actor_enabled_raw!=0
                && ((!result->role_error && result->role_raw==0) || result->manager_override_raw!=0)?1u:0u;
        }
    }
    return result->error=ERROR_SUCCESS;
}
void WINAPI PdxV3ConsoleAiStateInterface(void* application,void* opaque) {
    auto* result=static_cast<PdxV3ConsoleAiState*>(opaque);
    if (!result || result->size!=sizeof(*result)) return;
    try { result->error=PdxV3ConsoleReadAiState(reinterpret_cast<uint64_t>(application),result); }
    catch (...) { result->error=ERROR_UNHANDLED_EXCEPTION;result->observed_predicate_valid=0; }
}


namespace {
constexpr uint32_t ai_cache_header_bytes = sizeof(PdxV3ConsoleAiCacheSnapshot);
constexpr uint32_t ai_cache_row_bytes = sizeof(PdxV3ConsoleAiCacheRow);
constexpr uintptr_t ai_cache_actor_stride = 0x678;
constexpr uintptr_t ai_cache_block_bytes = 1024 * ai_cache_actor_stride;
static_assert(sizeof(PdxV3ConsoleRequest)==96 && sizeof(PdxV3ConsolePlayerState)==160);
static_assert(sizeof(PdxV3ConsoleAiState)==144);
static_assert(sizeof(PdxV3ConsoleAiCacheSnapshot)==96);
static_assert(offsetof(PdxV3ConsoleAiCacheSnapshot,required_bytes)==24);
static_assert(offsetof(PdxV3ConsoleAiCacheSnapshot,expected_world)==72);
static_assert(sizeof(PdxV3ConsoleAiCacheRow)==64);
static_assert(offsetof(PdxV3ConsoleAiCacheRow,actor_error)==16);
static_assert(offsetof(PdxV3ConsoleAiCacheRow,observed_predicate)==48);

int ai_cache_fault_filter(DWORD code) noexcept {
    return code==EXCEPTION_ACCESS_VIOLATION || code==EXCEPTION_IN_PAGE_ERROR
        ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH;
}
// A leaf POD boundary only. This is not a memory pin or a C++ exception catcher.
// Keep all C++ lifetime objects in the caller, outside this SEH-only function.
bool ai_cache_copy_bytes(void* target,const void* source,size_t bytes) noexcept {
    __try { std::memcpy(target,source,bytes);return true; }
    __except(ai_cache_fault_filter(GetExceptionCode())) { return false; }
}
bool ai_cache_add(uintptr_t origin,uint64_t bytes,uintptr_t& end) noexcept {
    if (bytes>static_cast<uint64_t>(~uintptr_t(0)-origin)) return false;
    end=origin+static_cast<uintptr_t>(bytes);return true;
}
template<class T> bool ai_cache_read(uintptr_t origin,uintptr_t offset,T& output) noexcept {
    uintptr_t source=0,end=0;
    if (!ai_cache_add(origin,offset,source) || !ai_cache_add(source,sizeof(T),end)) return false;
    return ai_cache_copy_bytes(&output,reinterpret_cast<const void*>(source),sizeof(T));
}
DWORD ai_cache_context_read(uint64_t application,PdxV3ConsoleAiState* context) {
    __try { return ai_context(application,*context); }
    __except(ai_cache_fault_filter(GetExceptionCode())) { return ERROR_NOACCESS; }
}
DWORD ai_cache_resolve_read(uintptr_t target,const uint32_t* id,uintptr_t* output) {
    __try {
        *output=reinterpret_cast<uintptr_t>(reinterpret_cast<AiResolve>(target)(id));
        return ERROR_SUCCESS;
    } __except(ai_cache_fault_filter(GetExceptionCode())) { return ERROR_NOACCESS; }
}
struct AiCachePool {
    uintptr_t blocks=0;
    int32_t capacity=0,count=0;
};
DWORD ai_cache_pool(uintptr_t manager,AiCachePool& pool) {
    // G=M+18 is the block-pointer array. D=M+10 is the native RefDB origin.
    const auto origin=manager+0x18;
    if (!ai_cache_read(origin,0,pool.blocks) || !ai_cache_read(origin,8,pool.capacity)
        || !ai_cache_read(origin,0xc,pool.count)) return ERROR_NOACCESS;
    if (pool.capacity<0 || pool.count<0 || pool.count>pool.capacity
        || (pool.count && !pool.blocks)) return ERROR_INVALID_DATA;
    uintptr_t end=0;
    if (!ai_cache_add(pool.blocks,static_cast<uint64_t>(pool.count)*8,end)) return ERROR_INVALID_DATA;
    for (int32_t i=0;i<pool.count;++i) {
        uintptr_t block=0;
        if (!ai_cache_read(pool.blocks,static_cast<uintptr_t>(i)*8,block)) return ERROR_NOACCESS;
        if (!block || !ai_cache_add(block,ai_cache_block_bytes,end)) return ERROR_INVALID_DATA;
    }
    return ERROR_SUCCESS;
}
DWORD ai_cache_provenance(const AiCachePool& pool,uintptr_t actor,bool& contained) {
    contained=false;
    // This walks block metadata, never actor/pool slots as list membership.
    for (int32_t i=0;i<pool.count;++i) {
        uintptr_t block=0,end=0;
        if (!ai_cache_read(pool.blocks,static_cast<uintptr_t>(i)*8,block)) return ERROR_NOACCESS;
        if (!block || !ai_cache_add(block,ai_cache_block_bytes,end)) return ERROR_INVALID_DATA;
        if (actor>=block && actor<end && (actor-block)%ai_cache_actor_stride==0) {
            contained=true;return ERROR_SUCCESS;
        }
    }
    return ERROR_SUCCESS;
}
void ai_cache_initialize_row(PdxV3ConsoleAiCacheRow& row,uint32_t index) {
    row={};row.cache_index=index;
    row.actor_full_id=row.owner_country_full_id=row.role_index=row.role_raw=ai_invalid_id;
    row.actor_error=row.owner_error=row.link_error=row.role_error=ERROR_NOT_READY;
}
DWORD ai_cache_row(uintptr_t actor,const AiCachePool& pool,const PdxV3ConsoleAiState& context,
                  const PdxV3ConsoleAiCacheSnapshot& header,PdxV3ConsoleAiCacheRow& row) {
    const auto image=base();
    uintptr_t fallback=0;
    if (!ai_cache_read(image,ai_actor_fallback_rva,fallback)) {
        row.actor_error=ERROR_NOACCESS;return ERROR_NOACCESS;
    }
    if (!actor || actor==fallback) { row.actor_error=ERROR_NOT_FOUND;return ERROR_SUCCESS; }
    bool contained=false;
    const auto provenance=ai_cache_provenance(pool,actor,contained);
    if (provenance) { row.actor_error=provenance;return provenance; }
    if (!contained) { row.actor_error=ERROR_INVALID_DATA;return ERROR_SUCCESS; }
    // No actor-derived address or full ID is read until current block/stride
    // provenance is established. Do not dereference actor-owned dynamic arrays.
    uintptr_t vtable=0;
    if (!ai_cache_read(actor,0,vtable)) { row.actor_error=ERROR_NOACCESS;return ERROR_NOACCESS; }
    if (vtable!=image+ai_actor_vtable_rva) { row.actor_error=ERROR_INVALID_DATA;return ERROR_SUCCESS; }
    uint32_t full_id=ai_invalid_id,owner_id=ai_invalid_id;
    uint8_t enabled=0,marked=0;
    if (!ai_cache_read(actor,8,full_id) || !ai_cache_read(actor,0x50,owner_id)
        || !ai_cache_read(actor,0x140,enabled) || !ai_cache_read(actor,0x141,marked)) {
        row.actor_error=ERROR_NOACCESS;return ERROR_NOACCESS;
    }
    row.actor_full_id=full_id;row.owner_country_full_id=owner_id;
    row.actor_enabled_raw=enabled;row.actor_141_raw=marked;
    row.role_index=owner_id&0xffffff;
    row.valid_fields|=PDX_V3_AI_CACHE_ACTOR_FIELDS;
    if (full_id==ai_invalid_id) { row.actor_error=ERROR_NOT_FOUND;return ERROR_SUCCESS; }
    uintptr_t resolved=0;
    try {
        const auto error=ai_cache_resolve_read(image+ai_actor_resolver_rva,&full_id,&resolved);
        if (error) { row.actor_error=error;return error; }
    } catch (...) { row.actor_error=ERROR_UNHANDLED_EXCEPTION;return ERROR_UNHANDLED_EXCEPTION; }
    if (!resolved || resolved==fallback || resolved!=actor) {
        row.actor_error=ERROR_NOT_FOUND;return ERROR_SUCCESS;
    }
    row.actor_error=ERROR_SUCCESS;row.valid_fields|=PDX_V3_AI_CACHE_ACTOR_IDENTITY;
    if (owner_id==ai_invalid_id) { row.owner_error=ERROR_NOT_FOUND;return ERROR_SUCCESS; }
    uintptr_t country=0,country_fallback=0;
    if (!ai_cache_read(image,player_country_fallback_rva,country_fallback)) {
        row.owner_error=ERROR_NOACCESS;return ERROR_NOACCESS;
    }
    try {
        const auto error=ai_cache_resolve_read(image+player_country_resolver_rva,&owner_id,&country);
        if (error) { row.owner_error=error;return error; }
    } catch (...) { row.owner_error=ERROR_UNHANDLED_EXCEPTION;return ERROR_UNHANDLED_EXCEPTION; }
    if (!country || country==country_fallback) { row.owner_error=ERROR_NOT_FOUND;return ERROR_SUCCESS; }
    uint32_t country_id=ai_invalid_id,cross_link=ai_invalid_id;
    uintptr_t playable_vtable=0;
    if (!ai_cache_read(country,0x18,country_id) || !ai_cache_read(country,8,playable_vtable)
        || !ai_cache_read(country,0x20,cross_link)) {
        row.owner_error=ERROR_NOACCESS;return ERROR_NOACCESS;
    }
    if (country_id!=owner_id) { row.owner_error=ERROR_NOT_FOUND;return ERROR_SUCCESS; }
    if (playable_vtable!=image+player_country_playable_vtable_rva) {
        row.owner_error=ERROR_INVALID_DATA;return ERROR_SUCCESS;
    }
    row.owner_error=ERROR_SUCCESS;row.valid_fields|=PDX_V3_AI_CACHE_OWNER_IDENTITY;
    row.link_error=cross_link==full_id?ERROR_SUCCESS:ERROR_INVALID_DATA;
    if (row.role_index>=header.role_count) {
        row.role_error=ERROR_NOT_FOUND;
        row.observed_predicate=enabled!=0 && header.manager_override_raw!=0?1u:0u;
        row.valid_fields|=PDX_V3_AI_CACHE_PREDICATE;
        return ERROR_SUCCESS;
    }
    if (!context.role_data) { row.role_error=ERROR_INVALID_DATA;return ERROR_SUCCESS; }
    uint32_t role=ai_invalid_id;
    if (!ai_cache_read(context.role_data,static_cast<uintptr_t>(row.role_index)*4,role)) {
        row.role_error=ERROR_NOACCESS;return ERROR_NOACCESS;
    }
    row.role_raw=role;row.role_error=ERROR_SUCCESS;
    row.valid_fields|=PDX_V3_AI_CACHE_ROLE_VALUE|PDX_V3_AI_CACHE_PREDICATE;
    // Exact copied-input gate from150F470. Do NOT call that scheduler body.
    row.observed_predicate=enabled!=0 && (role==0 || header.manager_override_raw!=0)?1u:0u;
    return ERROR_SUCCESS;
}
DWORD ai_cache_copy_body(uint64_t application,PdxV3ConsoleAiCacheSnapshot& header,
                         PdxV3ConsoleAiCacheSnapshot* output) {
    PdxV3ConsoleAiState context={};
    auto error=ai_cache_context_read(application,&context);
    header.application=context.application;header.world=context.world;
    header.paused=context.paused;header.simulation_started=context.simulation_started;
    if (error) return error;
    if (header.expected_world && header.expected_world!=context.world) return ERROR_NOT_READY;
    uintptr_t country_root=0,actor_root=0;
    if (!ai_cache_read(base(),player_country_root_rva,country_root)
        || !ai_cache_read(base(),ai_actor_root_rva,actor_root)) return ERROR_NOACCESS;
    if (country_root!=context.country_manager+0x10 || actor_root!=context.ai_manager+0x10)
        return ERROR_NOT_READY;
    header.flags|=PDX_V3_AI_CACHE_CONTEXT_VALID;
    uint8_t override_raw=0;
    if (!ai_cache_read(context.ai_manager,0x268,override_raw)) return ERROR_NOACCESS;
    header.manager_override_raw=override_raw;header.manager_error=ERROR_SUCCESS;
    if (!ai_cache_read(context.world,0xec,header.role_count)
        || !ai_cache_read(context.world,0xe0,context.role_data)) return ERROR_NOACCESS;
    uintptr_t data=0,end=0;
    int32_t capacity=0,total=-1;
    if (!ai_cache_read(context.ai_manager,0x68,data)
        || !ai_cache_read(context.ai_manager,0x70,capacity)
        || !ai_cache_read(context.ai_manager,0x74,total)) return ERROR_NOACCESS;
    header.native_total=total;header.flags|=PDX_V3_AI_CACHE_TOTAL_KNOWN;
    if (capacity<0 || total<0 || total>capacity || (total && !data)
        || !ai_cache_add(data,static_cast<uint64_t>(total)*8,end)) return ERROR_INVALID_DATA;
    header.flags|=PDX_V3_AI_CACHE_HEADER_VALID;
    header.required_bytes=ai_cache_header_bytes+static_cast<uint64_t>(total)*ai_cache_row_bytes;
    const auto available=static_cast<uint32_t>(total);
    const auto limit=std::min(available,header.row_capacity);
    AiCachePool pool;
    if (limit) {
        error=ai_cache_pool(context.ai_manager,pool);
        if (error) return error;
    }
    for (uint32_t index=0;index<limit;++index) {
        PdxV3ConsoleAiCacheRow row;
        ai_cache_initialize_row(row,index);
        uintptr_t actor=0;
        if (!ai_cache_read(data,static_cast<uintptr_t>(index)*8,actor)) {
            row.actor_error=ERROR_NOACCESS;error=ERROR_NOACCESS;
        } else error=ai_cache_row(actor,pool,context,header,row);
        const auto destination=reinterpret_cast<uintptr_t>(output)+ai_cache_header_bytes
            +static_cast<uintptr_t>(index)*ai_cache_row_bytes;
        if (!ai_cache_copy_bytes(reinterpret_cast<void*>(destination),&row,sizeof(row))) return ERROR_NOACCESS;
        ++header.row_count;
        header.returned_bytes=ai_cache_header_bytes+header.row_count*ai_cache_row_bytes;
        if (row.actor_error || row.owner_error || row.link_error || row.role_error) {
            ++header.row_error_count;header.flags|=PDX_V3_AI_CACHE_ROW_ERRORS;
        }
        if (error) return error; // Known whole-row prefix, including this error row.
    }
    if (limit<available) { header.flags|=PDX_V3_AI_CACHE_TRUNCATED;return ERROR_MORE_DATA; }
    header.flags|=PDX_V3_AI_CACHE_COMPLETE;
    return ERROR_SUCCESS;
}
}

DWORD WINAPI PdxV3ConsoleReadAiCache(uint64_t application,PdxV3ConsoleAiCacheSnapshot* result) noexcept(false) {
    if (!result) return ERROR_INVALID_PARAMETER;
    PdxV3ConsoleAiCacheSnapshot input={};
    if (!ai_cache_copy_bytes(&input,result,sizeof(input))) return ERROR_NOACCESS;
    const uint64_t allocated=ai_cache_header_bytes+static_cast<uint64_t>(input.row_capacity)*ai_cache_row_bytes;
    PdxV3ConsoleAiCacheSnapshot header={};
    header.size=input.size;header.error=ERROR_NOT_READY;header.row_capacity=input.row_capacity;
    header.native_total=-1;header.returned_bytes=ai_cache_header_bytes;
    header.row_bytes=ai_cache_row_bytes;header.manager_error=ERROR_NOT_READY;
    header.expected_world=input.expected_world;header.execution_thread_id=GetCurrentThreadId();
    uintptr_t end=0;
    if (input.size<ai_cache_header_bytes || allocated>0xffffffffull || allocated!=input.size
        || input.row_bytes!=ai_cache_row_bytes || input.reserved
        || !ai_cache_add(reinterpret_cast<uintptr_t>(result),allocated,end)) {
        header.error=ERROR_INVALID_PARAMETER;
        if (!ai_cache_copy_bytes(result,&header,sizeof(header))) return ERROR_NOACCESS;
        return header.error;
    }
    try {
        AiInterfaceAccess scope;
        header.error=ai_cache_copy_body(application,header,result);
    } catch (...) { header.error=ERROR_UNHANDLED_EXCEPTION; }
    if (header.error==ERROR_NOACCESS || header.error==ERROR_UNHANDLED_EXCEPTION)
        header.flags|=PDX_V3_AI_CACHE_PREFIX_FAULT;
    if (!ai_cache_copy_bytes(result,&header,sizeof(header))) return ERROR_NOACCESS;
    return header.error;
}
void WINAPI PdxV3ConsoleAiCacheInterface(void* application,void* opaque) {
    auto* result=static_cast<PdxV3ConsoleAiCacheSnapshot*>(opaque);
    if (!result) return;
    // Direct helper owns the complete exception/row-prefix boundary and guard.
    PdxV3ConsoleReadAiCache(reinterpret_cast<uint64_t>(application),result);
}
