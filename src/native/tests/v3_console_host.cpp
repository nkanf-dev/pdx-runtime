#define NOMINMAX
#include "../v3_console.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
unsigned strings_destroyed=0, release_calls=0;
void* expected_buffer=nullptr;
void* expected_allocator=nullptr;
uint64_t expected_context=0, expected_object=0;
uint32_t expected_argument_count=2, dispatch_calls=0, parse_calls=0, heap_strings=0;
std::vector<std::string> expected_explicit;
bool throw_dispatch=false;
unsigned char application[16]={}, manager_storage[248]={};
void* __fastcall manager_getter() { return manager_storage; }
void* __fastcall construct_string(PdxV3ConsoleString* out,const char* text,uint64_t length) {
    *out={{0,0},length,length>15?length:15};
    void* target=out->storage;
    if (length>15) {
        target=new char[static_cast<size_t>(length)+1];
        out->storage[0]=reinterpret_cast<uint64_t>(target);++heap_strings;
    }
    std::memcpy(target,text,static_cast<size_t>(length));
    return out;
}

uintptr_t allocation_table[3]={};
struct Allocator { uintptr_t* vtable; } allocator={allocation_table};
void* __fastcall construct_array(PdxV3ConsoleArguments* out) {
    *out={0,0,0,reinterpret_cast<uint64_t>(&allocator)};return out;
}
PdxV3ConsoleString argument_strings[2]={};
void __fastcall destroy_string(PdxV3ConsoleString* value) {
    ++strings_destroyed;
    if (value->capacity>15) { delete[] reinterpret_cast<char*>(value->storage[0]);--heap_strings; }
    value->length=0;value->capacity=15;
}
void __fastcall release_buffer(void* self,void* data) {
    assert(self==expected_allocator);
    assert(data==expected_buffer);
    ++release_calls;
}
void* __fastcall parse_arguments(PdxV3ConsoleArguments* out,const PdxV3ConsoleString* text,uint8_t delimiter) {
    ++parse_calls;
    assert(delimiter==32);
    if (text->length==0 || reinterpret_cast<const char*>(text->storage)[0]==' ') {
        *out={0,0,0,reinterpret_cast<uint64_t>(&allocator)};
    } else {
        assert(text->length==3);
        *out={reinterpret_cast<uint64_t>(argument_strings),2,2,reinterpret_cast<uint64_t>(&allocator)};
    }
    return out;
}
void* __fastcall dispatcher(void* manager,PdxV3ConsoleNativeResult* out,const PdxV3ConsoleArguments* args) {
    assert(manager==reinterpret_cast<void*>(0x1234) || manager==manager_storage);
    if (expected_explicit.empty()) assert(args->count==2);
    else {
        assert(args->count==expected_explicit.size() && args->allocator==0);
        const auto* values=reinterpret_cast<const PdxV3ConsoleString*>(args->data);
        for (size_t i=0;i<expected_explicit.size();++i) {
            const auto& value=values[i];
            const char* text=value.capacity>15 ? reinterpret_cast<const char*>(value.storage[0])
                                               : reinterpret_cast<const char*>(value.storage);
            assert(std::string(text,static_cast<size_t>(value.length))==expected_explicit[i]);
        }
    }
    ++dispatch_calls;
    if (throw_dispatch) throw std::runtime_error("native dispatcher test unwind");
    out->success=1;out->message={{0,0},0,15};
    return out;
}
void* __fastcall exact(PdxV3ConsoleNativeResult* out,const PdxV3ConsoleArguments* args,void* context) {
    assert(context==reinterpret_cast<void*>(expected_context));assert(args->count==expected_argument_count);
    out->success=1;return out;
}
void* __fastcall object_method(void* object,PdxV3ConsoleNativeResult* out,const PdxV3ConsoleArguments* args) {
    assert(object==reinterpret_cast<void*>(expected_object));assert(args->count==expected_argument_count);
    out->success=2;return out;
}
unsigned register_calls=0, unregister_calls=0, descriptor_destroys=0;
void __fastcall register_descriptor(void* manager,const PdxV3ConsoleDescriptor* descriptor) {
    assert(manager==manager_storage);
    assert(*reinterpret_cast<LONG*>(manager_storage+168)==1);
    assert(descriptor->name.length==3 && descriptor->callback==reinterpret_cast<uint64_t>(&exact));
    ++register_calls;
}
void __fastcall unregister_descriptor(void* manager,const char* name) {
    assert(manager==manager_storage && std::strcmp(name,"abc")==0);++unregister_calls;
}
void __fastcall destroy_descriptor(PdxV3ConsoleDescriptor*) { ++descriptor_destroys; }
void install_thunk(uintptr_t rva,void* target) {
    const auto base=reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    const auto region=(base+rva)&~uintptr_t(0xffff);
    void* memory=VirtualAlloc(reinterpret_cast<void*>(region),0x10000,MEM_RESERVE|MEM_COMMIT,PAGE_EXECUTE_READWRITE);
    assert(memory!=nullptr || GetLastError()==ERROR_INVALID_ADDRESS);
    auto* code=reinterpret_cast<unsigned char*>(base+rva);
    code[0]=0x48;code[1]=0xb8;
    const auto address=reinterpret_cast<uintptr_t>(target);
    std::memcpy(code+2,&address,8);code[10]=0xff;code[11]=0xe0;
    FlushInstructionCache(GetCurrentProcess(),code,12);
}
}
int main() {
    static_assert(sizeof(PdxV3ConsoleRequest)==96);
    allocation_table[2]=reinterpret_cast<uintptr_t>(&release_buffer);
    expected_allocator=&allocator;expected_buffer=argument_strings;
    install_thunk(0x61f810,reinterpret_cast<void*>(&destroy_string));
    install_thunk(0x3b12c60,reinterpret_cast<void*>(&parse_arguments));
    install_thunk(0x3af1740,reinterpret_cast<void*>(&dispatcher));
    PdxV3ConsoleArguments args={reinterpret_cast<uint64_t>(argument_strings),2,2,reinterpret_cast<uint64_t>(&allocator)};
    PdxV3ConsoleDestroyArguments(&args);
    assert(strings_destroyed==2 && release_calls==1);
    assert(args.data==0 && args.count==0 && args.capacity==0);
    PdxV3ConsoleDestroyArguments(&args);
    assert(release_calls==1);
    PdxV3ConsoleString text={{0,0},3,15};
    PdxV3ConsoleNativeResult result={};
    PdxV3ConsoleExecuteText(0x1234,&result,&text);
    assert(result.success==1 && release_calls==2 && strings_destroyed==4);
    args={reinterpret_cast<uint64_t>(argument_strings),2,2,reinterpret_cast<uint64_t>(&allocator)};
    expected_context=0x6789;
    PdxV3ConsoleInvokeExact(reinterpret_cast<uint64_t>(&exact),expected_context,&result,&args);
    assert(result.success==1);
    uintptr_t object_table[4]={};object_table[3]=reinterpret_cast<uintptr_t>(&object_method);
    struct Object { uintptr_t* vtable; } object={object_table};expected_object=reinterpret_cast<uint64_t>(&object);
    PdxV3ConsoleInvokeObject(expected_object,&result,&args);
    assert(result.success==2);
    install_thunk(0x61c420,reinterpret_cast<void*>(&construct_string));
    install_thunk(0x61c180,reinterpret_cast<void*>(&construct_array));
    install_thunk(0x3af13c0,reinterpret_cast<void*>(&manager_getter));
    const auto image=reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    void* root_page=VirtualAlloc(reinterpret_cast<void*>((image+0x592d128)&~uintptr_t(0xffff)),0x10000,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);
    assert(root_page!=nullptr);
    *reinterpret_cast<void**>(image+0x592d128)=application;
    struct RequestWithText { PdxV3ConsoleRequest header; char text[3]; } whitespace={};
    whitespace.header.size=sizeof(PdxV3ConsoleRequest)+3;
    whitespace.header.action=1;whitespace.header.input_size=3;
    std::memcpy(whitespace.text,"   ",3);
    const auto before=dispatch_calls;
    PdxV3ConsoleInterface(application,&whitespace.header);
    assert(whitespace.header.error==ERROR_INVALID_PARAMETER && whitespace.header.argument_count==0);
    assert(dispatch_calls==before);
    PdxV3ConsoleRequest empty={};empty.size=sizeof(empty);empty.action=4;
    empty.exact_callback=reinterpret_cast<uint64_t>(&exact);empty.exact_context=expected_context;
    expected_argument_count=0;
    PdxV3ConsoleInterface(application,&empty);
    assert(empty.error==0 && empty.native_success==1 && empty.argument_count==0);
    empty.action=5;empty.exact_context=expected_object;
    PdxV3ConsoleInterface(application,&empty);
    assert(empty.error==0 && empty.native_success==2 && empty.argument_count==0);
    empty.action=3;
    PdxV3ConsoleInterface(application,&empty);
    assert(empty.error==0 && empty.argument_count==0);
    install_thunk(0x3af3090,reinterpret_cast<void*>(&register_descriptor));
    install_thunk(0x3af14b0,reinterpret_cast<void*>(&unregister_descriptor));
    install_thunk(0x7c9c50,reinterpret_cast<void*>(&destroy_descriptor));
    PdxV3ConsoleDescriptor descriptor;
    PdxV3ConsoleInitializeDescriptor(&descriptor);
    assert(descriptor.name.capacity==15 && descriptor.parameters[9].capacity==15);
    descriptor.name.length=3;descriptor.callback=reinterpret_cast<uint64_t>(&exact);
    PdxV3ConsoleRegisterDescriptor(reinterpret_cast<uint64_t>(manager_storage),&descriptor);
    assert(register_calls==1 && *reinterpret_cast<LONG*>(manager_storage+168)==0);
    PdxV3ConsoleUnregister(reinterpret_cast<uint64_t>(manager_storage),"abc");
    assert(unregister_calls==1);
    PdxV3ConsoleDestroyDescriptor(&descriptor);assert(descriptor_destroys==1);
    expected_explicit={"native_probe", "two words \"quoted\"", "", "中文"};
    std::vector<unsigned char> payload;
    auto append_u32=[&](uint32_t value) {
        const auto* bytes=reinterpret_cast<const unsigned char*>(&value);
        payload.insert(payload.end(),bytes,bytes+4);
    };
    append_u32(static_cast<uint32_t>(expected_explicit.size()));
    for (const auto& value:expected_explicit) {
        append_u32(static_cast<uint32_t>(value.size()));
        payload.insert(payload.end(),value.begin(),value.end());
    }
    std::vector<unsigned char> wire(sizeof(PdxV3ConsoleRequest)+payload.size());
    auto* argv_request=reinterpret_cast<PdxV3ConsoleRequest*>(wire.data());
    argv_request->size=static_cast<uint32_t>(wire.size());argv_request->action=8;
    argv_request->input_size=static_cast<uint32_t>(payload.size());
    std::memcpy(argv_request+1,payload.data(),payload.size());
    const auto before_parse=parse_calls,before_release=release_calls,before_destroy=strings_destroyed;
    PdxV3ConsoleInterface(application,argv_request);
    assert(argv_request->error==0 && argv_request->native_success==1 && argv_request->argument_count==4);
    assert(parse_calls==before_parse && release_calls==before_release && heap_strings==0);
    assert(strings_destroyed==before_destroy+5);
    throw_dispatch=true;
    PdxV3ConsoleInterface(application,argv_request);
    assert(argv_request->error==ERROR_UNHANDLED_EXCEPTION && heap_strings==0);
    assert(strings_destroyed==before_destroy+10 && release_calls==before_release);
    throw_dispatch=false;
    const auto before_invalid=dispatch_calls;
    --argv_request->input_size;--argv_request->size;
    PdxV3ConsoleInterface(application,argv_request);
    assert(argv_request->error==ERROR_INVALID_PARAMETER && dispatch_calls==before_invalid && heap_strings==0);
    std::puts("PASS console native ownership/ABI and explicit argv boundaries/SSO/heap/Unicode/empty parameters/unwind/no tokenizer/no allocator release");
    return 0;
}
