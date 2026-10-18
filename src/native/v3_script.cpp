#include "v3_script.h"
#include "v3_script_random_scope.h"
#include <cstddef>
#include <cstring>
#include <functional>
#include <limits>
#include <string>
#include <stdexcept>

namespace {
constexpr uintptr_t effect_parser_rva = 0x2b30ac0;
constexpr uintptr_t trigger_parser_rva = 0x2b36820;
constexpr uintptr_t scope_valid_rva = 0x32b3b90;
constexpr uintptr_t scope_registry_rva = 0x32b3de0;
constexpr uintptr_t context_ctor_rva = 0x6509c0;
constexpr uintptr_t context_dtor_rva = 0x64ca50;
constexpr uintptr_t effect_runner_rva = 0x2b31140;
constexpr uintptr_t trigger_runner_rva = 0x2b374f0;
constexpr uintptr_t effect_execute_rva = 0x328f7f0;
constexpr uintptr_t trigger_evaluate_rva = 0x32ceb70;
constexpr uintptr_t string_ctor_rva = 0x61c420;
constexpr uintptr_t string_dtor_rva = 0x61f810;

// Exact CString32 shape recovered from parser/caller. Allocation and destruction
// belong to the game's native string functions, never the adapter CRT allocator.
struct NativeString {
    union { char local[16]; const char* pointer; } value;
    uint64_t length;
    uint64_t capacity;
};
static_assert(sizeof(NativeString) == 32);
static_assert(sizeof(PdxV3ScriptScope) == 16);
static_assert(sizeof(PdxV3ScriptOwner) == 544);
static_assert(sizeof(PdxV3ScriptResult) == 576);
static_assert(sizeof(PdxV3ScriptContext) == 320);
static_assert(sizeof(PdxV3ScriptSavedScope) == 24);
static_assert(sizeof(PdxV3ScriptValueResult) == 616);
static_assert(sizeof(PdxV3ScriptScopeTypeInfo) == 560);
static_assert(sizeof(PdxV3ScriptScopeTypesResult) == 80);
using ContextCustomizer = std::function<void(PdxV3ScriptContext&)>;
static_assert(sizeof(ContextCustomizer) == 64,
    "MSVC x64 native std::function ABI is required");

uintptr_t module_base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T field(uintptr_t object, uintptr_t offset) {
    return *reinterpret_cast<const T*>(object + offset);
}
using StringCtor = NativeString* (__fastcall*)(NativeString*, const char*, size_t);
using StringDtor = void (__fastcall*)(NativeString*);
struct StringOwner {
    NativeString native = {};
    uintptr_t base;
    StringOwner(uintptr_t image_base, const char* text, size_t size) : base(image_base) {
        reinterpret_cast<StringCtor>(base + string_ctor_rva)(&native, text, size);
    }
    ~StringOwner() { reinterpret_cast<StringDtor>(base + string_dtor_rva)(&native); }
    StringOwner(const StringOwner&) = delete;
    StringOwner& operator=(const StringOwner&) = delete;
};
void copy_diagnostic(const NativeString& text, PdxV3ScriptOwner* owner) {
    const char* data = text.capacity > 15 ? text.value.pointer : text.value.local;
    const size_t count = text.length < sizeof(owner->diagnostic) - 1
        ? static_cast<size_t>(text.length) : sizeof(owner->diagnostic) - 1;
    if (count) std::memcpy(owner->diagnostic, data, count);
    owner->diagnostic[count] = 0;
    owner->diagnostic_bytes = static_cast<uint32_t>(count);
    owner->diagnostic_truncated = text.length > count;
}
using EffectParser = void* (__fastcall*)(void**, const NativeString*, uint16_t, NativeString*);
struct TriggerVariant { union { void* object; NativeString diagnostic; }; uint8_t tag; uint8_t pad[7]; };
static_assert(sizeof(TriggerVariant) == 40);
using TriggerParser = void* (__fastcall*)(TriggerVariant*, const NativeString*, uint16_t);
using NativeDelete = void* (__fastcall*)(void*, uint32_t);
using ScopeRegistry = uintptr_t (__fastcall*)();
bool scope_type_registered(uintptr_t base, uint16_t type) {
    const uintptr_t registry = reinterpret_cast<ScopeRegistry>(base + scope_registry_rva)();
    // Native array header: pointer+0, capacity+8, count+12. Each type entry is 80B.
    return field<uintptr_t>(registry, 0) && type < field<uint32_t>(registry, 12);
}
using ScopeValid = uint8_t (__fastcall*)(const PdxV3ScriptScope*);
using ContextCtor = void* (__fastcall*)(void*);
using ContextDtor = void (__fastcall*)(void*);
using ContextRoot = void* (__fastcall*)(void*, const PdxV3ScriptScope*, const ContextCustomizer*);
using EffectRunner = void* (__fastcall*)(const void**, void*, const PdxV3ScriptScope*,
                                        const ContextCustomizer*);
using TriggerRunner = uint8_t (__fastcall*)(const void**, const PdxV3ScriptScope*,
                                           const ContextCustomizer*);
using EffectExecute = void (__fastcall*)(const void*, void*);
using TriggerEvaluate = uint8_t (__fastcall*)(const void*, const void*);

struct NativeContextOwner {
    PdxV3ScriptContext native = {};
    uintptr_t base;
    bool constructed = false;
    explicit NativeContextOwner(uintptr_t image_base) : base(image_base) {}
    ~NativeContextOwner() {
        if (constructed) reinterpret_cast<ContextDtor>(base + context_dtor_rva)(native.bytes);
    }
};
struct ParsedOwner {
    PdxV3ScriptOwner native = {};
    ~ParsedOwner() { if (native.object) PdxV3ScriptRelease(&native); }
};
using GuardEnter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using GuardLeave = void (__fastcall*)(void*);
struct alignas(16) AccessStorage { unsigned char bytes[80] = {}; };
struct AccessScope {
    AccessStorage storage;
    uintptr_t base;
    explicit AccessScope(uintptr_t image_base) : base(image_base) {
        reinterpret_cast<GuardEnter>(base + 0x33bd640)(storage.bytes, 1, 0,
            "pdx-runtime/native/v3_script.cpp");
    }
    ~AccessScope() { reinterpret_cast<GuardLeave>(base + 0x33bd750)(storage.bytes); }
};
// Original32C0930 enters Logic before native RandomAllow acquisition.
struct LogicScope {
    AccessStorage storage;
    uintptr_t base;
    explicit LogicScope(uintptr_t image_base) : base(image_base) {
        reinterpret_cast<GuardEnter>(base + 0x33bd640)(storage.bytes, 2, 1,
            "pdx-runtime/native/v3_script.cpp");
    }
    ~LogicScope() { reinterpret_cast<GuardLeave>(base + 0x33bd750)(storage.bytes); }
    LogicScope(const LogicScope&) = delete;
    LogicScope& operator=(const LogicScope&) = delete;
};
uint32_t random_thread_id() { return GetCurrentThreadId(); }
pdx_script_detail::RandomOperations random_operations(uintptr_t base) {
    return {base + 0x512ce20, reinterpret_cast<void* volatile*>(base + 0x512ce40),
        reinterpret_cast<pdx_script_detail::LockCtor>(base + 0x34bac50),
        reinterpret_cast<pdx_script_detail::FullDtor>(base + 0x7e06d0), random_thread_id};
}
using NativeAllocate = void* (__fastcall*)(size_t);
using NativeFree = void (__fastcall*)(void*);
using ValueCtor = void* (__fastcall*)(void*, const uint64_t*);
using ValueDtor = void (__fastcall*)(void*);
using ValueRead = void (__fastcall*)(void*, void*, uint16_t);
using ValueEvaluate = uint64_t* (__fastcall*)(const void*, uint64_t*, const void*, const void*, const void*);
using TypedCompile = uint32_t (__fastcall*)(void*, const void*, const uint64_t*, uint8_t);
using MemoryFileCtor = void* (__fastcall*)(void*, const char*, int, int);
using LexerCtor = void* (__fastcall*)(void*, void*, uint8_t);
using ReaderCtor = void* (__fastcall*)(void*, void*);
using NativeDtor = void (__fastcall*)(void*);
using ReaderNext = void (__fastcall*)(void*);
using ReaderDiagnostic = NativeString* (__fastcall*)(void*, NativeString*);

// Original parser2b30c03..2b30c2a: CTextLexer base160B plus derived16B
// counters at+A0/+A8, then TokenReader starts at lexer+B0. GetNext3b4f59a/5a4
// updates both counters. Each original ctor/dtor runs; borrowed file is88B.
struct NativeTextReader {
    alignas(16) unsigned char file[96] = {};
    alignas(16) unsigned char lexer[176] = {};
    alignas(16) unsigned char reader[352] = {};
    uintptr_t base;
    bool file_owned = false, lexer_owned = false, reader_owned = false;
    NativeTextReader(uintptr_t image_base, const std::string& text) : base(image_base) {
        try {
            reinterpret_cast<MemoryFileCtor>(base + 0x3aff620)(file, text.c_str(),
                static_cast<int>(text.size() + 1), 1);
            file_owned = true;
            reinterpret_cast<LexerCtor>(base + 0x3b4ea20)(lexer, file, 0);
            lexer_owned = true;
            *reinterpret_cast<uintptr_t*>(lexer) = base + 0x4851028; // native caller's CTextLexer vtable
            reinterpret_cast<ReaderCtor>(base + 0x3b50b10)(reader, lexer);
            reader_owned = true;
        } catch (...) { release(); throw; }
    }
    void release() {
        if (reader_owned) reinterpret_cast<NativeDtor>(base + 0x3b50c80)(reader);
        if (lexer_owned) reinterpret_cast<NativeDtor>(base + 0x3b4eb90)(lexer);
        if (file_owned) reinterpret_cast<NativeDtor>(base + 0x3aff770)(file);
        reader_owned = lexer_owned = file_owned = false;
    }
    ~NativeTextReader() { release(); }
    NativeTextReader(const NativeTextReader&) = delete;
    NativeTextReader& operator=(const NativeTextReader&) = delete;
};
DWORD parse_value(uintptr_t base, uint32_t kind, const char* text, uint32_t text_bytes,
                  uint16_t scope_type, PdxV3ScriptOwner* owner) {
    // Both numeric and typed event-target values share the native184B value owner
    // prefix. Its ctor/dtor retain the native expression's owned link arguments.
    void* value = reinterpret_cast<NativeAllocate>(base + 0x417d960)(184);
    if (!value) return ERROR_OUTOFMEMORY;
    const uint64_t zero = 0;
    bool constructed = false;
    try {
        reinterpret_cast<ValueCtor>(base + 0x12afc20)(value, &zero);
        constructed = true;
        if (kind == PDX_V3_SCRIPT_TYPED_VALUE) {
            struct StringView { const char* data; uint32_t bytes; uint32_t flags; };
            const StringView view = {text, text_bytes, 0};
            const uint64_t location = 0;
            const uint32_t consumed = reinterpret_cast<TypedCompile>(base + 0x32a2a90)(
                value, &view, &location, 0);
            if (consumed != text_bytes || !field<uint32_t>(reinterpret_cast<uintptr_t>(value), 12)) {
                reinterpret_cast<ValueDtor>(base + 0x7bb680)(value);
                reinterpret_cast<NativeFree>(base + 0x417d850)(value);
                return ERROR_BAD_FORMAT;
            }
        } else {
            // Native key/value reader expects a token with its value populated.
            // This is the original engine syntax, not a new expression language.
            std::string input("value = ");
            if (text_bytes > static_cast<uint32_t>((std::numeric_limits<int32_t>::max)() - 9))
                throw std::length_error("native ScriptValue text exceeds signed byte-count");
            input.append(text, text_bytes);
            NativeTextReader parser(base, input);
            reinterpret_cast<ReaderNext>(base + 0x3b52c00)(parser.reader);
            // The native constructor supplies a valid default literal(mode1).
            // Reset ONLY this owned temporary value to the reader's unset mode0:
            // D50CC0 sets mode1 on successful scalar AL, mode2/3 for range/math,
            // and leaves0 after a failed scalar read. Without this native sentinel,
            // logged invalid input could retain the constructor's genuine zero.
            *reinterpret_cast<uint32_t*>(static_cast<unsigned char*>(value) + 160) = 0;
            reinterpret_cast<ValueRead>(base + 0xd50cc0)(value, parser.reader, scope_type);
            if (parser.reader[32]) {
                StringOwner diagnostic(base, "", 0);
                reinterpret_cast<ReaderDiagnostic>(base + 0x3b516b0)(parser.reader, &diagnostic.native);
                copy_diagnostic(diagnostic.native, owner);
                reinterpret_cast<ValueDtor>(base + 0x7bb680)(value);
                reinterpret_cast<NativeFree>(base + 0x417d850)(value);
                return ERROR_BAD_FORMAT;
            }
            // Native ScriptValue mode0 is the reader's badly-read fallback, not a
            // fabricated successful zero. Numeric literals (including0) use mode1.
            if (!field<uint32_t>(reinterpret_cast<uintptr_t>(value), 160)) {
                reinterpret_cast<ValueDtor>(base + 0x7bb680)(value);
                reinterpret_cast<NativeFree>(base + 0x417d850)(value);
                return ERROR_BAD_FORMAT;
            }
        }
        owner->object = reinterpret_cast<uintptr_t>(value);
        return ERROR_SUCCESS;
    } catch (...) {
        if (constructed) reinterpret_cast<ValueDtor>(base + 0x7bb680)(value);
        reinterpret_cast<NativeFree>(base + 0x417d850)(value);
        throw;
    }
}
using NativeTokenString = const NativeString* (__fastcall*)(int32_t);
void copy_scope_name(const NativeString& native, char* output, uint32_t* bytes, uint32_t* required) {
    const char* input = native.capacity > 15 ? native.value.pointer : native.value.local;
    const size_t count = native.length < 255 ? static_cast<size_t>(native.length) : 255;
    if (count) std::memcpy(output, input, count);
    output[count] = 0;
    *bytes = static_cast<uint32_t>(count);
    *required = native.length > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(native.length);
}
void copy_scope_type_info(uintptr_t entry, uint32_t type, const NativeString& name,
    const NativeString* save_name, PdxV3ScriptScopeTypeInfo* row) {
    *row = {};
    row->type = type;
    row->name_token = field<uint32_t>(entry, 0);
    row->save_token = field<int32_t>(entry, 4);
    row->native_flags = field<uint8_t>(entry, 8);
    row->native_entry = entry;
    row->stores_variables = field<uintptr_t>(entry, 0x30) != 0;
    copy_scope_name(name, row->name, &row->name_bytes, &row->name_required);
    if (save_name)
        copy_scope_name(*save_name, row->save_name, &row->save_name_bytes, &row->save_name_required);
}
void scope_type_info(uintptr_t base, uintptr_t entry, uint32_t type, PdxV3ScriptScopeTypeInfo* row) {
    const auto token_string = reinterpret_cast<NativeTokenString>(base + 0x3b4e440);
    const int32_t save_token = field<int32_t>(entry, 4);
    // Original32b4080 uses the same token-string array for the save token. A
    // negative token is absent, not another scope type ID or an invented name.
    copy_scope_type_info(entry, type, *token_string(field<int32_t>(entry, 0)),
        save_token >= 0 ? token_string(save_token) : nullptr, row);
}
DWORD scope_registry(uintptr_t base, uintptr_t* entries, uint32_t* total) {
    const uintptr_t registry = reinterpret_cast<ScopeRegistry>(base + scope_registry_rva)();
    *entries = field<uintptr_t>(registry, 0);
    *total = field<uint32_t>(registry, 12);
    if (*total > 65536 || (*total && !*entries)) return ERROR_INVALID_DATA;
    return ERROR_SUCCESS;
}
DWORD owner_valid(const PdxV3ScriptOwner* owner, uint32_t kind) {
    return owner && owner->size == sizeof(*owner) && owner->kind == kind
        && owner->object && !owner->error ? ERROR_SUCCESS : ERROR_INVALID_PARAMETER;
}
}

DWORD WINAPI PdxV3ScriptScopeId(uint16_t type, uint32_t full_id, PdxV3ScriptScope* result) {
    if (!result || (type != PDX_V3_SCOPE_COUNTRY && type != PDX_V3_SCOPE_STATE
        && type != PDX_V3_SCOPE_BUILDING && type != PDX_V3_SCOPE_POP)) return ERROR_INVALID_PARAMETER;
    *result = {type, {0, 0, 0}, full_id};
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptScopeValid(const PdxV3ScriptScope* scope, uint32_t* valid) {
    if (!scope || !valid) return ERROR_INVALID_PARAMETER;
    *valid = 0;
    const uintptr_t base = module_base();
    if (!scope_type_registered(base, scope->type)) return ERROR_INVALID_PARAMETER;
    *valid = reinterpret_cast<ScopeValid>(base + scope_valid_rva)(scope);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptParse(uint32_t kind, const char* text, uint32_t text_bytes,
    uint16_t scope_type, PdxV3ScriptOwner* owner) {
    if (!owner || !text || text_bytes > static_cast<uint32_t>((std::numeric_limits<int32_t>::max)() - 1)
        || kind < PDX_V3_SCRIPT_TRIGGER || kind > PDX_V3_SCRIPT_TYPED_VALUE) return ERROR_INVALID_PARAMETER;
    *owner = {};
    owner->size = sizeof(*owner);
    owner->kind = kind;
    owner->scope_type = scope_type;
    const uintptr_t base = module_base();
    if (!scope_type_registered(base, scope_type)) return owner->error = ERROR_INVALID_PARAMETER;
    if (kind == PDX_V3_SCRIPT_VALUE || kind == PDX_V3_SCRIPT_TYPED_VALUE)
        return owner->error = parse_value(base, kind, text, text_bytes, scope_type, owner);
    StringOwner input(base, text, text_bytes);
    if (kind == PDX_V3_SCRIPT_EFFECT) {
        StringOwner diagnostic(base, "", 0);
        void* object = nullptr;
        reinterpret_cast<EffectParser>(base + effect_parser_rva)(
            &object, &input.native, scope_type, &diagnostic.native);
        owner->object = reinterpret_cast<uintptr_t>(object);
        copy_diagnostic(diagnostic.native, owner);
    } else {
        TriggerVariant parsed = {};
        reinterpret_cast<TriggerParser>(base + trigger_parser_rva)(&parsed, &input.native, scope_type);
        if (parsed.tag == 0) owner->object = reinterpret_cast<uintptr_t>(parsed.object);
        else if (parsed.tag == 1) {
            copy_diagnostic(parsed.diagnostic, owner);
            reinterpret_cast<StringDtor>(base + string_dtor_rva)(&parsed.diagnostic);
        } else return owner->error = ERROR_INVALID_DATA;
    }
    return owner->error = owner->object ? ERROR_SUCCESS : ERROR_BAD_FORMAT;
}
DWORD WINAPI PdxV3ScriptRelease(PdxV3ScriptOwner* owner) {
    if (!owner || owner->size != sizeof(*owner)
        || owner->kind < PDX_V3_SCRIPT_TRIGGER || owner->kind > PDX_V3_SCRIPT_TYPED_VALUE)
        return ERROR_INVALID_PARAMETER;
    if (owner->object) {
        void* object = reinterpret_cast<void*>(owner->object);
        if (owner->kind == PDX_V3_SCRIPT_VALUE || owner->kind == PDX_V3_SCRIPT_TYPED_VALUE) {
            const uintptr_t base = module_base();
            reinterpret_cast<ValueDtor>(base + 0x7bb680)(object);
            reinterpret_cast<NativeFree>(base + 0x417d850)(object);
        } else {
            const auto table = *reinterpret_cast<const uintptr_t* const*>(object);
            const size_t slot = owner->kind == PDX_V3_SCRIPT_EFFECT ? 0 : 2;
            reinterpret_cast<NativeDelete>(table[slot])(object, 1);
        }
        owner->object = 0;
    }
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptContextInit(PdxV3ScriptContext* context) {
    if (!context || reinterpret_cast<uintptr_t>(context) % 16) return ERROR_INVALID_PARAMETER;
    reinterpret_cast<ContextCtor>(module_base() + context_ctor_rva)(context->bytes);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptContextRoot(const PdxV3ScriptScope* scope, PdxV3ScriptContext* context) {
    if (!scope || !context || reinterpret_cast<uintptr_t>(context) % 16)
        return ERROR_INVALID_PARAMETER;
    const ContextCustomizer customize = [](PdxV3ScriptContext&) {};
    reinterpret_cast<ContextRoot>(module_base() + 0x2b30a00)(context->bytes, scope, &customize);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptContextRelease(PdxV3ScriptContext* context) {
    if (!context || reinterpret_cast<uintptr_t>(context) % 16) return ERROR_INVALID_PARAMETER;
    reinterpret_cast<ContextDtor>(module_base() + context_dtor_rva)(context->bytes);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptEvaluate(const PdxV3ScriptOwner* owner,
    const PdxV3ScriptContext* context, uint32_t* raw_boolean) {
    if (owner_valid(owner, PDX_V3_SCRIPT_TRIGGER) || !context || !raw_boolean)
        return ERROR_INVALID_PARAMETER;
    *raw_boolean = reinterpret_cast<TriggerEvaluate>(module_base() + trigger_evaluate_rva)(
        reinterpret_cast<const void*>(owner->object), context->bytes);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptExecute(const PdxV3ScriptOwner* owner, PdxV3ScriptContext* context) {
    if (owner_valid(owner, PDX_V3_SCRIPT_EFFECT) || !context) return ERROR_INVALID_PARAMETER;
    reinterpret_cast<EffectExecute>(module_base() + effect_execute_rva)(
        reinterpret_cast<const void*>(owner->object), context->bytes);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptEvaluateValue(const PdxV3ScriptOwner* owner,
    const PdxV3ScriptContext* context, uint64_t* raw_bits) {
    if (owner_valid(owner, PDX_V3_SCRIPT_VALUE) || !context || !raw_bits)
        return ERROR_INVALID_PARAMETER;
    // D4DF60 constructs/destructs its own native evaluation scratch. CFixedPoint
    // is returned through explicit RDX sret storage, not by interpreting RAX/XMM0.
    const uintptr_t base = module_base();
    const auto* location = reinterpret_cast<const unsigned char*>(owner->object) + 168;
    const uint32_t seed = field<uint32_t>(reinterpret_cast<uintptr_t>(context->bytes), 16);
    // Native328f7f0 constructs CCrudeRandom(seed,0x5ea6ba9f-seed) on its stack.
    // TopScope stores one seed, not a live CCrudeRandom pair at bytes+16.
    uint32_t random[2] = {seed, 0x5ea6ba9fu - seed};
    reinterpret_cast<ValueEvaluate>(base + 0xd4df60)(reinterpret_cast<const void*>(owner->object),
        raw_bits, context->bytes, random, location);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptEvaluateTyped(const PdxV3ScriptOwner* owner,
    const PdxV3ScriptContext* context, PdxV3ScriptScope* value) {
    if (owner_valid(owner, PDX_V3_SCRIPT_TYPED_VALUE) || !context || !value)
        return ERROR_INVALID_PARAMETER;
    const uintptr_t base = module_base();
    struct alignas(16) Scratch {
        unsigned char bytes[928] = {};
        uintptr_t base;
        bool constructed = false;
        explicit Scratch(uintptr_t image_base) : base(image_base) {}
        ~Scratch() {
            if (!constructed) return;
            // Exact native D4DF60 cleanup of CJominiEventScope scratch. No copy
            // or retention of scratch pointers crosses the synchronous return.
            const uintptr_t array = reinterpret_cast<uintptr_t>(bytes + 296);
            if (field<uintptr_t>(array, 0)) {
                using Clear = void (__fastcall*)(void*, uintptr_t, uint32_t);
                reinterpret_cast<Clear>(base + 0xc7aed0)(bytes + 296, 0, field<uint32_t>(array, 12));
                const uintptr_t allocator = field<uintptr_t>(array, 16);
                const uintptr_t release = field<uintptr_t>(field<uintptr_t>(allocator, 0), 16);
                using FreeBuffer = void (__fastcall*)(uintptr_t, uintptr_t);
                reinterpret_cast<FreeBuffer>(release)(allocator, field<uintptr_t>(array, 0));
            }
            const uintptr_t array0 = reinterpret_cast<uintptr_t>(bytes);
            if (field<uintptr_t>(array0, 0)) {
                const uintptr_t allocator = field<uintptr_t>(array0, 16);
                const uintptr_t release = field<uintptr_t>(field<uintptr_t>(allocator, 0), 16);
                using FreeBuffer = void (__fastcall*)(uintptr_t, uintptr_t);
                reinterpret_cast<FreeBuffer>(release)(allocator, field<uintptr_t>(array0, 0));
            }
        }
    } scratch(base);
    using ScratchCtor = void* (__fastcall*)(void*, const void*);
    reinterpret_cast<ScratchCtor>(base + 0x32a08e0)(scratch.bytes, context->bytes);
    scratch.constructed = true;
    struct Scopes { const void* root; const void* previous; const void* top; void* event_scope; };
    const Scopes scopes = {context->bytes, context->bytes, context->bytes, scratch.bytes};
    const auto* location = reinterpret_cast<const unsigned char*>(owner->object) + 168;
    using Evaluate = PdxV3ScriptScope* (__fastcall*)(const void*, PdxV3ScriptScope*, const Scopes*,
                                                   const void*, int, int);
    reinterpret_cast<Evaluate>(base + 0x32a2e00)(reinterpret_cast<const void*>(owner->object),
        value, &scopes, location, 0, 0);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptInternScopeName(const char* name, uint32_t name_bytes, uint32_t* native_name) {
    if (!name || !native_name || !name_bytes) return ERROR_INVALID_PARAMETER;
    struct StringView { const char* data; uint32_t bytes; uint32_t flags; };
    const StringView view = {name, name_bytes, 0};
    using NameRegistry = uintptr_t (__fastcall*)();
    using Intern = uint32_t* (__fastcall*)(uintptr_t, uint32_t*, const StringView*);
    const uintptr_t base = module_base();
    const uintptr_t registry = reinterpret_cast<NameRegistry>(base + 0x3b0cf90)();
    reinterpret_cast<Intern>(base + 0x3b0cc20)(registry, native_name, &view);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptContextSetSaved(PdxV3ScriptContext* context, uint32_t native_name,
    const PdxV3ScriptScope* value) {
    if (!context || !value) return ERROR_INVALID_PARAMETER;
    using Save = uintptr_t (__fastcall*)(void*, uint32_t, const PdxV3ScriptScope*);
    // Actual native D45080 caller passes TopScope+0x18 to this24B-entry map.
    reinterpret_cast<Save>(module_base() + 0x329f7d0)(context->bytes + 24, native_name, value);
    return ERROR_SUCCESS;
}
void WINAPI PdxV3ScriptInterface(void* application, void* opaque) {
    auto* result = static_cast<PdxV3ScriptResult*>(opaque);
    if (!result) return;
    const uint32_t kind = result->kind;
    const uint32_t text_bytes = result->text_bytes;
    const PdxV3ScriptScope scope = result->scope;
    *result = {};
    result->size = sizeof(*result);
    result->error = ERROR_NOT_READY;
    result->kind = kind;
    result->text_bytes = text_bytes;
    result->scope = scope;
    result->execution_thread_id = GetCurrentThreadId();
    if (kind != PDX_V3_SCRIPT_TRIGGER && kind != PDX_V3_SCRIPT_EFFECT) {
        result->error = ERROR_INVALID_PARAMETER;
        return;
    }
    const uintptr_t base = module_base();
    const uintptr_t app = field<uintptr_t>(base, 0x592d128);
    if (reinterpret_cast<uintptr_t>(application) != app || !app
        || field<uint8_t>(app, 0x258) || !field<uintptr_t>(app, 0x608)) return;
    try {
        AccessScope access(base);
        result->error = PdxV3ScriptScopeValid(&scope, &result->scope_valid);
        if (result->error) return;
        if (!result->scope_valid) { result->error = ERROR_NOT_FOUND; return; }
        // Immediate body keeps ParsedOwner and Context320 cleanup INSIDE the
        // synchronous native random owner. Trigger retains its previous lane.
        auto run_existing_body = [&]() {
            ParsedOwner parsed;
            result->error = PdxV3ScriptParse(kind, reinterpret_cast<const char*>(result + 1),
                text_bytes, scope.type, &parsed.native);
            result->diagnostic_bytes = parsed.native.diagnostic_bytes;
            result->diagnostic_truncated = parsed.native.diagnostic_truncated;
            std::memcpy(result->diagnostic, parsed.native.diagnostic, sizeof(result->diagnostic));
            if (result->error) return;
            result->parsed = 1;
            const void* object = reinterpret_cast<const void*>(parsed.native.object);
            // Captured native runner calls std::function's actual target at +0x38,
            // vtable slot2. A real MSVC callable provides that ABI and owns its object.
            // The reference parameter matches native std::function<void(TopScope&)>.
            // No-op customization keeps the explicit native root scope, native RNG,
            // and default saved-scope containers produced by the game's constructor.
            const ContextCustomizer customize = [](PdxV3ScriptContext&) {};
            if (kind == PDX_V3_SCRIPT_TRIGGER) {
                result->raw_boolean = reinterpret_cast<TriggerRunner>(base + trigger_runner_rva)(
                    &object, &scope, &customize);
            } else {
                NativeContextOwner context(base);
                // Native runner invokes customization only AFTER the context ctor.
                // Acquire ownership there, not before a possibly throwing ctor.
                const ContextCustomizer constructed = [&context](PdxV3ScriptContext&) { context.constructed = true; };
                reinterpret_cast<EffectRunner>(base + effect_runner_rva)(
                    &object, context.native.bytes, &scope, &constructed);
            }
            result->executed = 1;
            result->error = ERROR_SUCCESS;
        };
        if (kind == PDX_V3_SCRIPT_EFFECT) {
            LogicScope logic(base);
            const pdx_script_detail::SourceLocation24 source{
                __LINE__, 0, "pdx-runtime/native/v3_script.cpp", "PdxV3ScriptInterface"};
            pdx_script_detail::RandomAllow random(random_operations(base), source);
            if (random.error()) { result->error = random.error(); return; }
            random.publish();
            run_existing_body();
        } else {
            run_existing_body();
        }
    } catch (...) { result->error = ERROR_UNHANDLED_EXCEPTION; }
}

void WINAPI PdxV3ScriptValueInterface(void* application, void* opaque) {
    auto* result = static_cast<PdxV3ScriptValueResult*>(opaque);
    if (!result) return;
    const uint32_t kind = result->script.kind;
    const uint32_t text_bytes = result->script.text_bytes;
    const PdxV3ScriptScope scope = result->script.scope;
    const uint64_t context_address = result->context;
    const uint64_t saved_address = result->saved_scopes;
    const uint32_t saved_count = result->saved_count;
    const uint32_t text_offset = result->text_offset;
    *result = {};
    result->script.size = sizeof(*result);
    result->script.error = ERROR_NOT_READY;
    result->script.kind = kind;
    result->script.text_bytes = text_bytes;
    result->script.scope = scope;
    result->script.execution_thread_id = GetCurrentThreadId();
    result->context = context_address;
    result->saved_scopes = saved_address;
    result->saved_count = saved_count;
    result->text_offset = text_offset;
    if (kind != PDX_V3_SCRIPT_VALUE && kind != PDX_V3_SCRIPT_TYPED_VALUE) {
        result->script.error = ERROR_INVALID_PARAMETER;
        return;
    }
    if (text_offset < sizeof(*result) || (saved_count && !saved_address)) {
        result->script.error = ERROR_INVALID_PARAMETER;
        return;
    }
    const uintptr_t base = module_base();
    const uintptr_t app = field<uintptr_t>(base, 0x592d128);
    if (reinterpret_cast<uintptr_t>(application) != app || !app
        || field<uint8_t>(app, 0x258) || !field<uintptr_t>(app, 0x608)) return;
    try {
        AccessScope access(base);
        result->script.error = PdxV3ScriptScopeValid(&scope, &result->script.scope_valid);
        if (result->script.error) return;
        if (!result->script.scope_valid) { result->script.error = ERROR_NOT_FOUND; return; }
        ParsedOwner parsed;
        result->script.error = PdxV3ScriptParse(kind, reinterpret_cast<const char*>(result) + text_offset,
            text_bytes, scope.type, &parsed.native);
        result->script.diagnostic_bytes = parsed.native.diagnostic_bytes;
        result->script.diagnostic_truncated = parsed.native.diagnostic_truncated;
        std::memcpy(result->script.diagnostic, parsed.native.diagnostic, sizeof(result->script.diagnostic));
        if (result->script.error) return;
        result->script.parsed = 1;
        NativeContextOwner local(base);
        auto* context = reinterpret_cast<PdxV3ScriptContext*>(context_address);
        if (!context) {
            const ContextCustomizer constructed = [&local](PdxV3ScriptContext&) { local.constructed = true; };
            reinterpret_cast<ContextRoot>(base + 0x2b30a00)(local.native.bytes, &scope, &constructed);
            context = &local.native;
        }
        // A borrowed native context preserves caller ROOT/PREV and all saved
        // containers. It is never copied, released or assumed zero-initialized.
        const auto* saved = reinterpret_cast<const PdxV3ScriptSavedScope*>(saved_address);
        for (uint32_t i = 0; i < saved_count; ++i)
            PdxV3ScriptContextSetSaved(context, saved[i].name, &saved[i].value);
        if (kind == PDX_V3_SCRIPT_VALUE) {
            result->value_type = 1;
            result->script.error = PdxV3ScriptEvaluateValue(&parsed.native, context, &result->value_payload);
        } else {
            PdxV3ScriptScope value = {};
            result->script.error = PdxV3ScriptEvaluateTyped(&parsed.native, context, &value);
            result->value_type = value.type;
            result->value_payload = value.payload;
        }
        if (!result->script.error) result->script.executed = 1;
    } catch (...) { result->script.error = ERROR_UNHANDLED_EXCEPTION; }
}


DWORD WINAPI PdxV3ScriptEnumerateScopeTypes(uint32_t start, uint32_t capacity,
    PdxV3ScriptScopeTypeInfo* rows, uint32_t* count, uint32_t* total) {
    if (!count || !total || (capacity && !rows)) return ERROR_INVALID_PARAMETER;
    *count = 0;
    *total = 0;
    const uintptr_t base = module_base();
    uintptr_t entries = 0;
    const DWORD error = scope_registry(base, &entries, total);
    if (error) return error;
    if (start >= *total) return ERROR_SUCCESS;
    const uint32_t available = *total - start;
    *count = capacity < available ? capacity : available;
    for (uint32_t i = 0; i < *count; ++i)
        scope_type_info(base, entries + static_cast<uintptr_t>(start + i) * 80, start + i, rows + i);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ScriptResolveScopeType(const char* name, uint32_t name_bytes,
    PdxV3ScriptScopeTypeInfo* row) {
    if (!name || !row) return ERROR_INVALID_PARAMETER;
    *row = {};
    const uintptr_t base = module_base();
    uintptr_t entries = 0;
    uint32_t total = 0;
    const DWORD error = scope_registry(base, &entries, &total);
    if (error) return error;
    const auto token_string = reinterpret_cast<NativeTokenString>(base + 0x3b4e440);
    for (uint32_t i = 0; i < total; ++i) {
        const uintptr_t entry = entries + static_cast<uintptr_t>(i) * 80;
        const NativeString& native = *token_string(field<int32_t>(entry, 0));
        const char* data = native.capacity > 15 ? native.value.pointer : native.value.local;
        if (native.length == name_bytes && (!name_bytes || !std::memcmp(data, name, name_bytes))) {
            scope_type_info(base, entry, i, row);
            return ERROR_SUCCESS;
        }
    }
    return ERROR_NOT_FOUND;
}
void WINAPI PdxV3ScriptScopeTypesInterface(void* application, void* opaque) {
    auto* result = static_cast<PdxV3ScriptScopeTypesResult*>(opaque);
    if (!result) return;
    const uint32_t action = result->action;
    const uint32_t start = result->start;
    const uint32_t capacity = result->capacity;
    const uint32_t name_bytes = result->input_name_bytes;
    const uint64_t name_address = result->input_name;
    const uint64_t output_address = result->output_rows;
    const uint32_t output_offset = result->output_offset;
    const uint32_t output_bytes = result->output_bytes;
    *result = {};
    result->size = sizeof(*result);
    result->error = ERROR_NOT_READY;
    result->action = action;
    result->start = start;
    result->capacity = capacity;
    result->input_name_bytes = name_bytes;
    result->input_name = name_address;
    result->output_rows = output_address;
    result->row_size = sizeof(PdxV3ScriptScopeTypeInfo);
    result->execution_thread_id = GetCurrentThreadId();
    result->output_offset = output_offset;
    result->output_bytes = output_bytes;
    if ((action != 1 && action != 2) || (capacity && !output_address)
        || static_cast<uint64_t>(capacity) * sizeof(PdxV3ScriptScopeTypeInfo) > output_bytes
        || (action == 2 && (!name_address || capacity != 1))) {
        result->error = ERROR_INVALID_PARAMETER;
        return;
    }
    const uintptr_t base = module_base();
    const uintptr_t app = field<uintptr_t>(base, 0x592d128);
    if (!app || reinterpret_cast<uintptr_t>(application) != app) return;
    try {
        AccessScope access(base);
        auto* rows = reinterpret_cast<PdxV3ScriptScopeTypeInfo*>(output_address);
        if (action == 1) {
            result->error = PdxV3ScriptEnumerateScopeTypes(start, capacity, rows,
                &result->count, &result->total);
            result->next = start + result->count;
        } else {
            result->error = PdxV3ScriptResolveScopeType(reinterpret_cast<const char*>(name_address),
                name_bytes, rows);
            if (!result->error) {
                result->count = result->matched = 1;
                result->resolved_type = rows[0].type;
            }
        }
    } catch (...) { result->error = ERROR_UNHANDLED_EXCEPTION; }
}
