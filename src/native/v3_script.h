#pragma once
#include <Windows.h>
#include <stdint.h>

#if defined(PDX_V3_SCRIPT_BUILD)
#define PDX_V3_SCRIPT_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_SCRIPT_API extern "C" __declspec(dllimport)
#endif

enum PdxV3ScriptKind : uint32_t {
    PDX_V3_SCRIPT_TRIGGER = 1,
    PDX_V3_SCRIPT_EFFECT = 2,
    PDX_V3_SCRIPT_VALUE = 3,
    PDX_V3_SCRIPT_TYPED_VALUE = 4,
};
// Native CScopeObjectReference, not a GUI CPdxData scope/type ID.
// ID-based kinds carry the complete uint32 database ID in payload, zero-extended.
struct PdxV3ScriptScope { uint16_t type; uint16_t reserved[3]; uint64_t payload; };
enum PdxV3ScriptScopeType : uint16_t {
    PDX_V3_SCOPE_COUNTRY = 5,
    PDX_V3_SCOPE_BUILDING = 8,
    PDX_V3_SCOPE_POP = 0x3f,
    PDX_V3_SCOPE_STATE = 0x4c,
};

// Native parser owns the compiled object. Release exactly once with ScriptRelease.
// Parse output must be fresh storage or an owner already released; Parse resets
// its output and must not overwrite a live compiled owner.
// Pointer is process-local; do not transfer it across process/world lifetimes.
struct PdxV3ScriptOwner {
    uint32_t size;
    uint32_t error;
    uint32_t kind;
    uint32_t scope_type;
    uint64_t object;
    uint32_t diagnostic_bytes;
    uint32_t diagnostic_truncated;
    char diagnostic[512];
};

// Low-level caller-managed native context storage. Use the native ctor/dtor;
// zero bytes alone are NOT a constructed context. Alignment is part of the ABI.
struct alignas(16) PdxV3ScriptContext { unsigned char bytes[320]; };

// Synchronous low-level primitives require an existing native game access scope.
// Parse uses native CString/buffer/CMemoryFile/TextLexer/TokenReader lifetimes.
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptScopeId(uint16_t type, uint32_t full_id,
                                               PdxV3ScriptScope* result);
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptScopeValid(const PdxV3ScriptScope* scope,
                                                   uint32_t* valid);
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptParse(uint32_t kind, const char* text,
    uint32_t text_bytes, uint16_t scope_type, PdxV3ScriptOwner* owner);
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptRelease(PdxV3ScriptOwner* owner);
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptContextInit(PdxV3ScriptContext* context);
// Construct ROOT from an explicit native reference, with the native runner RNG
// seed and real native saved-scope containers. Release this context exactly once.
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptContextRoot(const PdxV3ScriptScope* scope,
                                                   PdxV3ScriptContext* context);
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptContextRelease(PdxV3ScriptContext* context);
// These direct engine calls accept a fully constructed native context and preserve
// caller-supplied ROOT/PREV/saved scopes. They do not guess or construct its bytes.
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptEvaluate(const PdxV3ScriptOwner* owner,
    const PdxV3ScriptContext* context, uint32_t* raw_boolean);
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptExecute(const PdxV3ScriptOwner* owner,
    PdxV3ScriptContext* context);

// Native numeric ScriptValue parses literal/range/script-math/event-target forms.
// Result is actual CFixedPoint raw64 (scale100000), not a guessed RAX/XMM value.
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptEvaluateValue(const PdxV3ScriptOwner* owner,
    const PdxV3ScriptContext* context, uint64_t* raw_bits);
// Native event-target compiler/evaluator returns its 16B type/payload value.
// Type1=fixed,2=bool,3=flag; object references keep their registered native type.
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptEvaluateTyped(const PdxV3ScriptOwner* owner,
    const PdxV3ScriptContext* context, PdxV3ScriptScope* value);
// Named scope/value insertion uses the native owned-context container. name is
// the native interned string/token ID; caller may use ScriptInternScopeName.
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptInternScopeName(const char* name,
    uint32_t name_bytes, uint32_t* native_name);
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptContextSetSaved(PdxV3ScriptContext* context,
    uint32_t native_name, const PdxV3ScriptScope* value);



// Non-blocking Interface callback request. Text bytes immediately follow this
// structure in the same caller-owned allocation; input scope must be explicit.
// The real Effect runner enters its own native (2,1) logic scope. Trigger runner
// uses the natural Interface runner path, including its own native RNG policy.
// The callback parses/runs/releases in one native scope, leaving no script owner.
struct PdxV3ScriptResult {
    uint32_t size;
    uint32_t error;
    uint32_t kind;
    uint32_t text_bytes;
    PdxV3ScriptScope scope;
    uint32_t scope_valid;
    uint32_t parsed;
    uint32_t executed;
    uint32_t raw_boolean;
    uint32_t execution_thread_id;
    uint32_t diagnostic_bytes;
    uint32_t diagnostic_truncated;
    uint32_t reserved;
    char diagnostic[512];
};
PDX_V3_SCRIPT_API void WINAPI PdxV3ScriptInterface(void* application, void* opaque);

struct PdxV3ScriptSavedScope { uint32_t name; uint32_t reserved; PdxV3ScriptScope value; };
// Old Trigger/Effect wire stays576B. Numeric request begins with the same prefix;
// saved_scopes and context are explicit caller-owned addresses, not hidden owners.
struct PdxV3ScriptValueResult {
    PdxV3ScriptResult script;
    uint32_t value_type;
    uint32_t value_reserved;
    uint64_t value_payload;
    uint64_t context;
    uint64_t saved_scopes;
    uint32_t saved_count;
    uint32_t text_offset;
};
PDX_V3_SCRIPT_API void WINAPI PdxV3ScriptValueInterface(void* application, void* opaque);


// Script scope IDs index the original80B scope registry, NOT DataFactory types.
// Closed metadata fields only: name token+0, save token+4, flags byte+8 and
// nonnull variable-store callback+0x30 as reported by native32b4080.
struct PdxV3ScriptScopeTypeInfo {
    uint32_t type;
    uint32_t name_token;
    int32_t save_token;
    uint32_t native_flags;
    uint32_t name_bytes;
    uint32_t name_required;
    uint32_t save_name_bytes;
    uint32_t save_name_required;
    uint64_t native_entry;
    uint32_t stores_variables;
    uint32_t reserved;
    char name[256];
    char save_name[256];
};
// Synchronous primitives need an existing native access scope. Row pointers are
// caller-owned. Names copy out original native CString bytes, with required byte
// counts if256B buffers truncate. Resolve compares complete native names exactly.
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptEnumerateScopeTypes(uint32_t start,
    uint32_t capacity, PdxV3ScriptScopeTypeInfo* rows, uint32_t* count, uint32_t* total);
PDX_V3_SCRIPT_API DWORD WINAPI PdxV3ScriptResolveScopeType(const char* name,
    uint32_t name_bytes, PdxV3ScriptScopeTypeInfo* row);
struct PdxV3ScriptScopeTypesResult {
    uint32_t size;
    uint32_t error;
    uint32_t action; //1 enumerate,2 resolve
    uint32_t start;
    uint32_t capacity;
    uint32_t count;
    uint32_t next;
    uint32_t total;
    uint32_t input_name_bytes;
    uint32_t resolved_type;
    uint32_t matched;
    uint32_t reserved;
    uint64_t input_name;
    uint64_t output_rows;
    uint32_t row_size;
    uint32_t execution_thread_id;
    uint32_t output_offset;
    uint32_t output_bytes;
};
PDX_V3_SCRIPT_API void WINAPI PdxV3ScriptScopeTypesInterface(void* application, void* opaque);
