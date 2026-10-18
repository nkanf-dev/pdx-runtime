#include "../v3_commands.h"
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <stdexcept>
#include <vector>

namespace {
int failures = 0;
void check(bool condition, const char* name) {
    std::printf("%s %s\n", condition ? "PASS" : "FAIL", name);
    if (!condition) ++failures;
}
struct Command { uintptr_t* vtable; uint64_t value; };
uint32_t destroyed = 0, reads = 0, writes = 0;
struct NativeReason { uint64_t storage[2], length, capacity; };
uint32_t strings_constructed = 0, strings_destroyed = 0, guard_entered = 0, guard_left = 0;
const char* reason_text = "";
void* __fastcall construct_reason(NativeReason* out, const char* data, uint64_t length) {
    check(data && length == 0, "native CString construct exact pointer/uint64 length ABI");
    *out = {{0, 0}, 0, 15}; ++strings_constructed; return out;
}
void __fastcall destroy_reason(NativeReason* value) {
    if (value->capacity > 15) std::free(reinterpret_cast<void*>(value->storage[0]));
    *value = {{0, 0}, 0, 15}; ++strings_destroyed;
}
void set_reason(NativeReason* value, const char* text) {
    check(value->length == 0 && value->capacity == 15, "primary receives initialized caller-owned CString");
    const size_t size = std::strlen(text);
    if (size > 15) {
        auto* data = static_cast<char*>(std::malloc(size + 1));
        std::memcpy(data, text, size + 1); value->storage[0] = reinterpret_cast<uint64_t>(data);
        value->capacity = size;
    } else std::memcpy(value->storage, text, size + 1);
    value->length = size;
}
void* __fastcall enter_scope(void* bytes, uint32_t mode, int flag, const char*) {
    check(reinterpret_cast<uintptr_t>(bytes) % 16 == 0 && mode == 1 && flag == 0,
        "Interface native guard storage alignment and exact arguments");
    ++guard_entered; return bytes;
}
void __fastcall leave_scope(void*) { ++guard_left; }
std::vector<void*> thunk_pages;
void* allocate_page(uintptr_t address, DWORD protection) {
    const auto page = reinterpret_cast<void*>(address & ~uintptr_t(0xffff));
    for (auto* existing : thunk_pages) if (existing == page) return page;
    void* memory = VirtualAlloc(page, 0x10000, MEM_RESERVE | MEM_COMMIT, protection);
    if (!memory) throw std::runtime_error("self-owned ABI test page allocation failed");
    thunk_pages.push_back(memory); return memory;
}
void install_thunk(uintptr_t rva, void* target) {
    const auto image = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    allocate_page(image + rva, PAGE_EXECUTE_READWRITE);
    auto* code = reinterpret_cast<unsigned char*>(image + rva);
    code[0] = 0x48; code[1] = 0xb8;
    std::memcpy(code + 2, &target, sizeof(target)); code[10] = 0xff; code[11] = 0xe0;
    FlushInstructionCache(GetCurrentProcess(), code, 12);
}
void* __fastcall destroy(void* self, uint32_t deleting) {
    check(deleting == 1, "primary deleting destructor flag1");
    ++destroyed;
    delete static_cast<Command*>(self);
    return self;
}
uint32_t token_lookups = 0;
bool token_lookup_throws = false;
unsigned char token_registry[0x58] = {};
NativeReason token_strings[2] = {};
void* token_names[12500] = {};
unsigned char token_node[32] = {}, token_missing[32] = {};
struct TokenView { const char* data; int32_t bytes; uint8_t is_null; uint8_t padding[3]; };
void** __fastcall lookup_token(void* directory, void** node, const TokenView* view) {
    check(directory == token_registry + 8 && *reinterpret_cast<LONG*>(token_registry) == 2,
        "native token lookup uses real hash directory and one shared lock");
    check(view->is_null == 0 && view->bytes >= 0, "native token16B StringView retains explicit byte length");
    ++token_lookups;
    if (token_lookup_throws) throw std::runtime_error("native token lookup unwind");
    const bool found = view->bytes == 6 && _strnicmp(view->data, "native", 6) == 0;
    *node = found ? token_node : token_missing; return node;
}
void* __fastcall raw_token_name(uint32_t token) {
    return token == 12490 ? &token_strings[1] : &token_strings[0];
}
uint32_t clone_error_mode = 0;
void** __fastcall clone(void* self, void** owned) {
    check(*owned == nullptr, "primary slot8 clone owns a distinct empty output location");
    if (clone_error_mode == 1) throw std::runtime_error("clone before ownership write");
    *owned = new Command(*static_cast<Command*>(self));
    if (clone_error_mode == 2) throw std::runtime_error("clone after ownership write");
    return owned;
}
uint32_t __fastcall type(void*) { return 12490; }
unsigned char __fastcall validate(void* self, void* reason) {
    if (reason) set_reason(static_cast<NativeReason*>(reason), reason_text);
    else check(reason == nullptr, "primary validator nullable reason argument");
    if (static_cast<Command*>(self)->value == 99) throw std::runtime_error("test validator unwind");
    return static_cast<Command*>(self)->value == 5;
}
void __fastcall read(void* self, void* reader) {
    static_cast<Command*>(self)->value = *static_cast<uint64_t*>(reader);
    ++reads;
}
void __fastcall write(void* self, void* writer) {
    *static_cast<uint64_t*>(writer) = static_cast<Command*>(self)->value;
    ++writes;
}
}
int main() {
    std::printf("v3_commands_host pid=%lu; no game or native engine emulation\n", GetCurrentProcessId());
    check(PdxV3CommandFactory(12490, nullptr) == ERROR_INVALID_PARAMETER, "factory invalid pointer");
    uint64_t owned = 1;
    check(PdxV3CommandFactory(12490, &owned) == ERROR_INVALID_PARAMETER, "factory will not overwrite ownership");
    check(PdxV3CommandDestroy(nullptr) == ERROR_INVALID_PARAMETER, "destroy null ownership location");
    owned = 0;
    check(PdxV3CommandDestroy(&owned) == 0, "destroy already consumed pointer is no-op");
    uint16_t invalid_wire[] = {12490, 1, 0, 4};
    check(PdxV3CommandDeserialize(invalid_wire, sizeof(invalid_wire), &owned) == ERROR_INVALID_DATA,
        "native deserialize rejects nonblock structural header before engine entry");
    uintptr_t table[10] = {};
    table[0] = reinterpret_cast<uintptr_t>(&destroy);
    table[1] = reinterpret_cast<uintptr_t>(&write);
    table[3] = reinterpret_cast<uintptr_t>(&read);
    table[6] = reinterpret_cast<uintptr_t>(&validate);
    table[8] = reinterpret_cast<uintptr_t>(&clone);
    table[9] = reinterpret_cast<uintptr_t>(&type);
    owned = reinterpret_cast<uint64_t>(new Command{table, 5});
    uint32_t token = 0, valid = 0;
    check(PdxV3CommandType(owned, &token) == 0 && token == 12490, "primary slot9 exact type token ABI");
    check(PdxV3CommandValidate(owned, &valid) == 0 && valid == 1, "primary slot6 bool true");
    uint64_t data = 7;
    check(PdxV3CommandRead(owned, &data) == 0 && reads == 1, "primary slot3 native Reader stays caller-owned");
    data = 0;
    check(PdxV3CommandWrite(owned, &data) == 0 && writes == 1 && data == 7,
        "primary slot1 native Writer stays caller-owned");
    check(PdxV3CommandValidate(owned, &valid) == 0 && valid == 0, "primary slot6 bool false");
    install_thunk(0x61c420, reinterpret_cast<void*>(&construct_reason));
    install_thunk(0x61f810, reinterpret_cast<void*>(&destroy_reason));
    reason_text = "native domain rejection";
    char copied[64] = {}; uint32_t size = 0;
    check(PdxV3CommandValidateText(owned, copied, sizeof(copied), &size, &valid) == 0
        && valid == 0 && size == std::strlen(reason_text)
        && std::memcmp(copied, reason_text, size) == 0,
        "native false reason copied before CString destruction; command retained");
    check(strings_constructed == strings_destroyed && strings_destroyed == 1,
        "heap CString released exactly once");
    std::memset(copied, 0, sizeof(copied));
    check(PdxV3CommandValidateText(owned, copied, 5, &size, &valid) == ERROR_INSUFFICIENT_BUFFER
        && valid == 0 && size == std::strlen(reason_text) && std::memcmp(copied, reason_text, 5) == 0,
        "reason truncation retains prefix/full byte count and false bool");
    reason_text = "short reason";
    check(PdxV3CommandValidateText(owned, copied, sizeof(copied), &size, &valid) == 0
        && size == 12 && strings_constructed == strings_destroyed,
        "SSO CString reason uses 32B layout and native destructor");
    auto* object = reinterpret_cast<Command*>(owned); object->value = 5;
    reason_text = "";
    check(PdxV3CommandValidateText(owned, nullptr, 0, &size, &valid) == 0 && valid == 1 && size == 0,
        "native valid empty reason and zero-length copy");
    object->value = 99; reason_text = "allocated exception reason";
    check(PdxV3CommandValidateText(owned, copied, sizeof(copied), &size, &valid) == ERROR_UNHANDLED_EXCEPTION
        && strings_constructed == strings_destroyed,
        "native CString destroyed once on validator C++ unwind");
    object->value = 7;
    NativeReason raw = {{0, 0}, 0, 15}; reason_text = "raw reason";
    const auto before_raw_destroy = strings_destroyed;
    check(PdxV3CommandValidateReason(owned, &raw, &valid) == 0 && !valid && raw.length == 10
        && strings_destroyed == before_raw_destroy,
        "raw reason CString remains caller-owned");
    destroy_reason(&raw);
    install_thunk(0x33bd640, reinterpret_cast<void*>(&enter_scope));
    install_thunk(0x33bd750, reinterpret_cast<void*>(&leave_scope));
    const auto image = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    allocate_page(image + 0x592d128, PAGE_READWRITE);
    unsigned char application[0x610] = {};
    *reinterpret_cast<void**>(image + 0x592d128) = application;
    *reinterpret_cast<uint64_t*>(application + 0x608) = 0x12345;
    struct Callback { PdxV3CommandResult result; char output[64]; } callback = {};
    callback.result.size = sizeof(PdxV3CommandResult);
    callback.result.action = PDX_V3_COMMAND_VALIDATE; callback.result.command = owned;
    callback.result.buffer = reinterpret_cast<uint64_t>(callback.output);
    callback.result.buffer_capacity = sizeof(callback.output);
    reason_text = "native domain rejection";
    PdxV3CommandInterface(application, &callback.result);
    check(callback.result.error == 0 && callback.result.validated == 0 && callback.result.command == owned
        && callback.result.buffer_size == std::strlen(reason_text) && callback.result.type_token == 12490
        && std::memcmp(callback.output, reason_text, callback.result.buffer_size) == 0,
        "88B callback wire copies false native reason without consuming ownership");
    check(guard_entered == 1 && guard_left == 1 && strings_constructed + 1 == strings_destroyed,
        "callback native guard balanced and all temporary CString storage released");
    uint64_t copy = 0;
    check(PdxV3CommandClone(0, &copy) == ERROR_INVALID_PARAMETER && !copy,
        "native clone null source leaves output unowned");
    check(PdxV3CommandClone(owned, nullptr) == ERROR_INVALID_PARAMETER,
        "native clone null output never consumes source");
    clone_error_mode = 1;
    const uint32_t before_clone_error_destroy = destroyed;
    check(PdxV3CommandClone(owned, &copy) == ERROR_UNHANDLED_EXCEPTION && !copy
        && destroyed == before_clone_error_destroy && object->value == 7,
        "clone exception before output write preserves source and empty output");
    clone_error_mode = 2;
    check(PdxV3CommandClone(owned, &copy) == ERROR_UNHANDLED_EXCEPTION && copy && copy != owned
        && destroyed == before_clone_error_destroy && object->value == 7,
        "clone exception after output write preserves raw pointer for explicit cleanup");
    check(PdxV3CommandDestroy(&copy) == 0 && !copy && destroyed == before_clone_error_destroy + 1,
        "error-written clone cleaned explicitly without auto-consume or source deletion");
    clone_error_mode = 0;
    check(PdxV3CommandClone(owned, &copy) == 0 && copy && copy != owned
        && reinterpret_cast<Command*>(copy)->value == object->value,
        "native slot8 clone preserves fields and original ownership");
    check(PdxV3CommandClone(owned, &copy) == ERROR_INVALID_PARAMETER,
        "native clone refuses overwriting an owned output pointer");
    object->value = 11;
    check(reinterpret_cast<Command*>(copy)->value == 7,
        "native clone independently owns object storage");
    check(PdxV3CommandDestroy(&copy) == 0 && copy == 0 && destroyed == 2,
        "native cloned pointer destroys separately");
    struct CloneCallback { PdxV3CommandResult result; uint64_t output; } clone_callback = {};
    clone_callback.result.size = sizeof(PdxV3CommandResult);
    clone_callback.result.action = PDX_V3_COMMAND_CLONE;
    clone_callback.result.command = owned;
    clone_callback.result.buffer = reinterpret_cast<uint64_t>(&clone_callback.output);
    clone_callback.result.buffer_capacity = sizeof(clone_callback.output);
    PdxV3CommandInterface(application, &clone_callback.result);
    check(clone_callback.result.error == 0 && clone_callback.result.command == owned
        && clone_callback.result.buffer_size == 8 && clone_callback.output != owned,
        "88B clone callback retains source and returns independent owned pointer");
    check(PdxV3CommandDestroy(&clone_callback.output) == 0 && destroyed == 3,
        "callback clone result independently released");
    check(PdxV3CommandDestroy(&owned) == 0 && owned == 0 && destroyed == 4,
        "native deletion consumes ownership before call");
    check(PdxV3CommandDestroy(&owned) == 0 && destroyed == 4, "no repeated native deletion");
    allocate_page(image + 0x592ebd0, PAGE_READWRITE);
    *reinterpret_cast<void**>(image + 0x592ebd0) = token_registry;
    *reinterpret_cast<void***>(token_registry + 0x48) = token_names;
    *reinterpret_cast<int32_t*>(token_registry + 0x54) = 12490;
    token_strings[0] = {{0, 0}, 0, 15}; set_reason(&token_strings[0], "state");
    token_strings[1] = {{0, 0}, 0, 15}; set_reason(&token_strings[1], "queue_construction");
    token_names[439] = &token_strings[0]; token_names[12490] = &token_strings[1];
    token_node[4] = 1; *reinterpret_cast<uint32_t*>(token_node + 24) = 12490;
    token_missing[4] = 0xff;
    install_thunk(0x3b7a920, reinterpret_cast<void*>(&lookup_token));
    install_thunk(0x3b4e440, reinterpret_cast<void*>(&raw_token_name));
    uint32_t resolved = 0;
    check(PdxV3CommandResolveToken("NaTiVe", 6, &resolved) == 0 && resolved == 12490
        && *reinterpret_cast<LONG*>(token_registry) == 0,
        "native token-name resolution preserves case-insensitive engine result and unlocks");
    check(PdxV3CommandResolveToken("unknown", 7, &resolved) == ERROR_NOT_FOUND && resolved == UINT32_MAX,
        "unknown token is NOT_FOUND sentinel not fabricated token zero");
    check(PdxV3CommandResolveToken(nullptr, 0, &resolved) == ERROR_INVALID_PARAMETER,
        "token lookup rejects invalid raw name pointer without registration");
    token_lookup_throws = true;
    check(PdxV3CommandResolveToken("native", 6, &resolved) == ERROR_UNHANDLED_EXCEPTION
        && resolved == UINT32_MAX && *reinterpret_cast<LONG*>(token_registry) == 0,
        "native lookup exception restores shared token lock without inventing a token");
    token_lookup_throws = false;
    char name_bytes[32] = {};
    check(PdxV3CommandTokenNameText(12490, name_bytes, sizeof(name_bytes), &size) == 0 && size == 18
        && std::memcmp(name_bytes, "queue_construction", 18) == 0 && token_lookups == 3
        && *reinterpret_cast<LONG*>(token_registry) == 0,
        "native token text reads borrowed heap CString under one lock without lookup mutation");
    check(PdxV3CommandTokenNameText(439, name_bytes, 2, &size) == ERROR_INSUFFICIENT_BUFFER
        && size == 5 && std::memcmp(name_bytes, "st", 2) == 0,
        "token name snapshot supports SSO and explicit prefix/full byte count");
    check(PdxV3CommandTokenNameText(UINT32_MAX, name_bytes, sizeof(name_bytes), &size) == ERROR_NOT_FOUND
        && size == 0 && *reinterpret_cast<LONG*>(token_registry) == 0,
        "token name unknown fullID uses native table bound not static command allowlist");
    check(PdxV3CommandTokenName(12490) == reinterpret_cast<uint64_t>(&token_strings[1]),
        "raw token name stays a borrowed native CString pointer");
    struct TokenCallback { PdxV3CommandResult result; char bytes[32]; } token_callback = {};
    token_callback.result.size = sizeof(PdxV3CommandResult);
    token_callback.result.action = PDX_V3_COMMAND_TOKEN_NAME; token_callback.result.type_token = 12490;
    token_callback.result.buffer = reinterpret_cast<uint64_t>(token_callback.bytes);
    token_callback.result.buffer_capacity = sizeof(token_callback.bytes);
    PdxV3CommandInterface(application, &token_callback.result);
    check(token_callback.result.error == 0 && token_callback.result.buffer_size == 18
        && std::memcmp(token_callback.bytes, "queue_construction", 18) == 0,
        "88B callback copies actual native token name without temporary native ownership");
    token_callback.result.action = PDX_V3_COMMAND_RESOLVE_TOKEN;
    token_callback.result.buffer_size = 6; std::memcpy(token_callback.bytes, "NaTiVe", 6);
    PdxV3CommandInterface(application, &token_callback.result);
    check(token_callback.result.error == 0 && token_callback.result.type_token == 12490
        && token_lookups == 4 && *reinterpret_cast<LONG*>(token_registry) == 0,
        "88B callback resolves dynamic token name through actual native hash ABI");
    destroy_reason(&token_strings[0]); destroy_reason(&token_strings[1]);
    for (auto* page : thunk_pages) VirtualFree(page, 0, MEM_RELEASE);
    std::printf("failures=%d\n", failures);
    return failures ? 1 : 0;
}
