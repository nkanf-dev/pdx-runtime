#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <Windows.h>
#include <stdint.h>

#ifdef PDX_V3_DATA_BUILD
#define PDX_V3_DATA_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_DATA_API extern "C" __declspec(dllimport)
#endif

// Exact native storage sizes, not layouts for callers to synthesize statements.
struct PdxV3DataProperty { alignas(8) unsigned char bytes[0x138]; };
struct PdxV3DataAny { alignas(8) unsigned char bytes[40]; };
struct PdxV3DataBinding { alignas(8) unsigned char bytes[64]; };
struct PdxV3DataScope { uint64_t object; int32_t type_id; uint8_t is_any; uint8_t padding[3]; };
struct PdxV3DataScopeValue { uint64_t object; uint8_t is_any; uint8_t padding[7]; };
struct PdxV3DataArray { uint64_t data; uint32_t capacity; uint32_t count; uint64_t allocator; };
struct PdxV3DataBindingInput { int32_t type_id; uint32_t flags; uint64_t object; };
// Binding flags: 0 = native CData wrapper; 1 = retained value;
// 2 = Country full ID; 3 = State full ID; 4 = Pop full ID;
// 5 = Market full ID; 6 = Building full ID.
enum PdxV3DataDomain : uint32_t {
    PDX_V3_DATA_COUNTRY = 1, PDX_V3_DATA_STATE = 2, PDX_V3_DATA_BUILDING = 3,
    PDX_V3_DATA_POP = 4, PDX_V3_DATA_MARKET = 5,
};

enum PdxV3DataAction : uint32_t {
    PDX_V3_DATA_TYPES = 1, PDX_V3_DATA_FUNCTIONS = 2,
    PDX_V3_DATA_EVALUATE = 3, PDX_V3_DATA_SNAPSHOT = 4, PDX_V3_DATA_RELEASE = 5,
    PDX_V3_DATA_LIST_COUNT = 6, PDX_V3_DATA_LIST_ELEMENT = 7,
    PDX_V3_DATA_INVOKE_ACTION = 8,
    PDX_V3_DATA_CREATE_STATE_NAME_CONTEXT = 9, PDX_V3_DATA_STATE_NAME_DRAFT = 10,
    PDX_V3_DATA_STATE_NAME_OVERRIDE = 11, PDX_V3_DATA_COMMAND_VALUE = 12,
    PDX_V3_DATA_EVALUATE_ELEMENT = 13, PDX_V3_DATA_INVOKE_ELEMENT_ACTION = 14,
    PDX_V3_DATA_EVALUATE_ARRAY_ELEMENT = 15, PDX_V3_DATA_INVOKE_ARRAY_ELEMENT_ACTION = 16,
    PDX_V3_DATA_EVALUATE_COPIED = 17, PDX_V3_DATA_SOURCE_COUNT = 18,
    PDX_V3_DATA_EVALUATE_SCOPED = 19, PDX_V3_DATA_SCOPED_SOURCE_COUNT = 20,
    PDX_V3_DATA_INVOKE_SOURCE_INDEX_ACTION = 21,
    PDX_V3_DATA_CREATE_OWNED_CONTEXT = 22,
};
enum PdxV3DataKind : uint32_t {
    PDX_V3_DATA_RAW = 0, PDX_V3_DATA_EMPTY = 1, PDX_V3_DATA_BOOL = 2,
    PDX_V3_DATA_I32 = 3, PDX_V3_DATA_U32 = 4, PDX_V3_DATA_I64 = 5,
    PDX_V3_DATA_U64 = 6, PDX_V3_DATA_FLOAT = 7, PDX_V3_DATA_DOUBLE = 8,
    PDX_V3_DATA_FIXED = 9, PDX_V3_DATA_STRING = 10, PDX_V3_DATA_OBJECT = 11,
    PDX_V3_DATA_LIST = 12, PDX_V3_DATA_COMMAND = 13,
};
struct PdxV3DataTypeInfo {
    int32_t type_id; uint32_t name_bytes; uint32_t description_bytes; uint32_t flags;
    uint64_t native_record; uint64_t object_to_any; uint64_t any_to_object;
    char name[128]; char description[256];
};
struct PdxV3DataFunctionInfo {
    int32_t type_id; int32_t return_type_id; uint32_t argument_count; uint32_t flags;
    uint32_t name_bytes; uint32_t description_bytes; uint64_t native_descriptor;
    uint64_t callbacks[4]; char name[128]; char description[256];
};
// Input/output header followed by expression UTF-8, bindings, and output bytes.
// size covers the whole allocation. Offsets are allocation-relative. output_bytes
// is caller capacity; snapshot_bytes/count state exactly what was copied.
struct PdxV3DataRequest {
    uint32_t size; uint32_t error; uint32_t action; uint32_t flags;
    int32_t type_id; uint32_t start; uint32_t capacity; uint32_t count;
    uint32_t next; uint32_t total; uint32_t execution_thread_id; uint32_t kind;
    uint64_t factory; uint64_t object; uint64_t value_handle; uint64_t any_type;
    uint64_t any_vtable; uint64_t payload_address;
    uint32_t payload_size; uint32_t snapshot_bytes;
    uint32_t text_offset; uint32_t text_bytes;
    uint32_t bindings_offset; uint32_t bindings_count;
    uint32_t output_offset; uint32_t output_bytes;
    uint64_t raw_bits; uint8_t raw_any[40]; char native_type_name[192];
};

// Action inputs are explicit, not extra expression syntax. Retained input is
// genuine-copied; its original handle remains unchanged. Strings follow this wire.
struct PdxV3DataInput {
    uint32_t kind; uint32_t string_bytes;
    uint64_t raw_bits; uint64_t value_handle;
};
constexpr uint32_t PDX_V3_DATA_RETAINED_INPUT = 0x100;
constexpr uint32_t PDX_V3_DATA_UTF8_INPUT = 0x101;
struct PdxV3DataActionRequest {
    PdxV3DataRequest result;
    uint32_t input_offset; uint32_t input_bytes;
    uint32_t compiled_mode; uint32_t invoked;
    uint32_t accepted; uint32_t input_kind;
};
constexpr uint32_t PDX_V3_DATA_EXPLICIT_ACTION = 1;

// Fresh original list production and element consumption share one callback.
// source_world is supplied BEFORE production and checked before any getter.
// No retained list receives a later world stamp. start selects the current
// provider index; it is not a stable application/domain identity.
struct PdxV3DataElementRequest {
    PdxV3DataActionRequest action;
    uint64_t source_world;
    uint32_t source_text_offset, source_text_bytes;
    uint32_t condition_offset, condition_bytes;
    int32_t element_type_id;
    uint32_t condition_checked, condition_passed, source_count;
};
// Generic fresh root projection/count. All intermediates die in the same native
// callback/Access scope; no list/element/result owner survives. source_produced
// is set only after root evaluation succeeds. count_valid distinguishes an
// actual empty Creation source from a missing/unsupported/error source.
struct PdxV3DataProjectionRequest {
    PdxV3DataRequest result;
    uint64_t source_world;
    uint32_t source_produced, count_valid, source_count, reserved;
};
// Additive actions19/20 only. The root392 prefix and actions17/18 stay exact.
// Step table follows this header, then original source expressions and the
// final expression, then aligned copied output. No external bindings/handles.
constexpr uint32_t PDX_V3_DATA_MAX_SCOPED_DEPTH = 8;
struct PdxV3DataScopedProjectionRequest {
    PdxV3DataProjectionRequest projection;
    uint32_t steps_offset, steps_count, completed_steps, reserved;
};
struct PdxV3DataCreationStep {
    uint32_t source_text_offset, source_text_bytes, index, expected_count;
    uint32_t source_produced, count_valid, source_count, element_obtained;
    int32_t element_type_id;
    uint32_t binding_mode;
};
// expected_count=UINT32_MAX is unspecified. An explicit mismatch is ERROR_RETRY.
// Each source is evaluated once using the preceding ORIGINAL Binding64; every
// property is compiled before any element is borrowed. Output fields are copied
// observations only, never stable element identity or a lifetime extension.
// Optional source-context bridge for an ORIGINAL native pointer-element array.
// root expression runs afresh inside the existing Interface callback/gate. The
// array has the fixed PdxV3DataArray ABI, not an arbitrary field/offset chain.
// Pointer elements are BORROWED from their original owner. BindObject does not
// promise protection from that owner's direct delete. All native temporaries
// must die before the source owner can refresh/delete and before scope exit.
struct PdxV3DataPointerArraySource {
    uint32_t root_text_offset, root_text_bytes, array_offset;
    int32_t source_type_id;
    uint32_t index, expected_count, interface_thread_id, flags;
    uint32_t count, stages;
};
struct PdxV3DataArrayElementRequest {
    PdxV3DataElementRequest element;
    PdxV3DataPointerArraySource source;
};
constexpr uint32_t PDX_V3_DATA_ARRAY_COUNT_ONLY = 1;
constexpr uint32_t PDX_V3_DATA_ARRAY_DIRECT_CONTEXT = 2;
constexpr uint32_t PDX_V3_DATA_ARRAY_SOURCE_GUARD = 4;
constexpr uint32_t PDX_V3_DATA_ARRAY_ELEMENT_SUBOBJECT = 8;
struct PdxV3DataArrayElementSubobject {
    uint32_t byte_offset, reserved;
    uint64_t expected_element_vtable;
};
// When flag8 is present, this16B suffix follows the array source, before an
// optional source guard. It selects an ORIGINAL embedded subobject, not a
// pointer field/cast. Match the original element vtable before adding offset.
struct PdxV3DataSourceGuard {
    uint32_t text_offset, text_bytes, input_offset, input_bytes;
    uint32_t checked, passed;
};
struct PdxV3DataGuardedArrayElementRequest {
    PdxV3DataArrayElementRequest array;
    PdxV3DataSourceGuard guard;
};
enum PdxV3DataSourceStage : uint32_t {
    PDX_V3_DATA_ROOT_PRODUCED = 1, PDX_V3_DATA_ARRAY_VALIDATED = 2,
    PDX_V3_DATA_SOURCE_BOUND = 4, PDX_V3_DATA_LIST_PRODUCED = 8,
    PDX_V3_DATA_ELEMENT_OBTAINED = 16, PDX_V3_DATA_CONDITION_CHECKED = 32,
    PDX_V3_DATA_CONSUMER_COMPLETED = 64,
    PDX_V3_DATA_SOURCE_GUARD_CHECKED = 128, PDX_V3_DATA_SOURCE_GUARD_PASSED = 256,
};

// All low-level calls require a live native Interface access scope. They do not
// manufacture object IDs or call registered getters out of their engine context.
PDX_V3_DATA_API uint64_t WINAPI PdxV3DataFactory();
PDX_V3_DATA_API int32_t WINAPI PdxV3DataFindType(const char* name);
PDX_V3_DATA_API uint64_t WINAPI PdxV3DataFindFunction(int32_t type_id, const char* name);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataEnumerateTypes(PdxV3DataRequest* request);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataEnumerateFunctions(PdxV3DataRequest* request);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataCompile(const char* expression, uint32_t bytes,
                                           PdxV3DataProperty* property);
PDX_V3_DATA_API void WINAPI PdxV3DataDestroyProperty(PdxV3DataProperty* property);
// Country constructor allocates the full native 0x1970 wrapper and holds one ref.
// The pointer is a CDataCountry wrapper, NOT VCCountry. Release its ref explicitly.
PDX_V3_DATA_API uint64_t WINAPI PdxV3DataCountryWrapper(uint32_t full_id);
PDX_V3_DATA_API void WINAPI PdxV3DataReleaseWrapper(uint64_t wrapper);
// Resolve the complete domain ID, then use its native CData factory/cache getter.
// out wrapper holds one engine ref; release with PdxV3DataReleaseWrapper.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataWrapperForId(uint32_t domain,
                                                  uint32_t full_id, uint64_t* wrapper);
// object must be a native CData wrapper matching type_id, not an arbitrary game pointer.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataBindObject(uint64_t object, int32_t type_id,
                                               PdxV3DataBinding* binding);
// Original raw0/Any2 scoped binding. Caller MUST already hold the original
// owner-thread Access scope until binding destruction; this confers no lease.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataBindScopedObject(uint64_t original_object,
    int32_t type_id, PdxV3DataBinding* binding);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataBindingScope(PdxV3DataBinding* binding,
                                                 PdxV3DataScope* scope);
PDX_V3_DATA_API void WINAPI PdxV3DataDestroyBinding(PdxV3DataBinding* binding);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataPrepare(PdxV3DataProperty* property,
    PdxV3DataArray* named_scopes, PdxV3DataArray* parameter_refs,
    PdxV3DataScopeValue* scope, int32_t* type_id);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataEvaluatePrepared(PdxV3DataProperty* property,
    PdxV3DataScopeValue* scope, PdxV3DataArray* parameter_refs, PdxV3DataAny* value);
// Action categories3/4 use native3a797b0 and a caller-supplied Any input/in-out.
// intent must be explicit; modes1/2 are not redirected through this entrypoint.
// invoked/accepted report call execution and native AL, not an observed world effect.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataActionPrepared(PdxV3DataProperty* property,
    PdxV3DataScopeValue* scope, PdxV3DataArray* parameter_refs, PdxV3DataAny* input,
    uint32_t intent, uint32_t* accepted);
// Destination must be fresh/uninitialized storage or empty Any. Destroy an existing
// owned Any first; this constructor does not replace/free a live destination payload.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataAnyInput(const PdxV3DataInput* input,
    uint32_t bytes, PdxV3DataAny* value);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataInvokeAction(PdxV3DataActionRequest* request);

// One caller-owned ordered sequence of the original ActionRequest packets.
// No Access is entered here: compose with the existing Access callback carrier.
struct PdxV3DataActionSequenceRequest {
    uint32_t size, error, count, steps_offset;
    uint32_t completed, stopped, reserved[2];
};
struct PdxV3DataActionSequenceStep {
    uint32_t request_offset;
    uint32_t continuation; // 0: always; 1: original invoked && accepted
};
PDX_V3_DATA_API DWORD WINAPI PdxV3DataInvokeActionSequence(PdxV3DataActionSequenceRequest* sequence);
PDX_V3_DATA_API void WINAPI PdxV3DataInvokeActionSequenceInterface(void* context, void* opaque);

// One typed named-rule consumer in the caller's original Interface/Access scope.
// Input spans hold the existing PdxV3DataInput STRING packets (not Data DSL).
// All output offsets are allocation-relative. Keys and original ActionRequest
// records are copied only; no borrowed row/definition pointer escapes.
struct PdxV3DataGameRuleRequest {
    uint32_t size, error, rule_input_offset, rule_input_bytes;
    uint32_t setting_input_offset, setting_input_bytes, output_offset, output_bytes;
    uint32_t stage, outcome, root_observed, root_found;
    uint32_t row_observed, row_found, count_valid, choice_count;
    uint32_t current_found, target_found, current_index, target_index;
    uint32_t direction, planned, completed, stopped;
    uint32_t receipts_offset, receipts_count, keys_offset, keys_count;
    uint32_t final_observed, final_selected, execution_thread_id, initial_key_valid;
    uint32_t initial_key_offset, initial_key_bytes, final_key_valid, final_key_offset;
    uint32_t final_key_bytes, receipts_bytes, copied_output_bytes, observation_error;
};
enum PdxV3DataGameRuleOutcome : uint32_t {
    PDX_V3_DATA_RULE_UNOBSERVED = 0, PDX_V3_DATA_RULE_NATIVE_NULL = 1,
    PDX_V3_DATA_RULE_EMPTY = 2, PDX_V3_DATA_RULE_TARGET_ABSENT = 3,
    PDX_V3_DATA_RULE_CURRENT_NOT_MEMBER = 4, PDX_V3_DATA_RULE_ALREADY_SELECTED = 5,
    PDX_V3_DATA_RULE_SELECTED = 6, PDX_V3_DATA_RULE_ACTION_STOPPED = 7,
    PDX_V3_DATA_RULE_OUTPUT_PARTIAL = 8, PDX_V3_DATA_RULE_NATIVE_ERROR = 9,
};
enum PdxV3DataGameRuleStage : uint32_t {
    PDX_V3_DATA_RULE_VALIDATE = 1, PDX_V3_DATA_RULE_ROOT = 2,
    PDX_V3_DATA_RULE_RESOLVE = 3, PDX_V3_DATA_RULE_CHOICES = 4,
    PDX_V3_DATA_RULE_KEYS = 5, PDX_V3_DATA_RULE_PLAN = 6,
    PDX_V3_DATA_RULE_ACTIONS = 7, PDX_V3_DATA_RULE_FINAL = 8,
    PDX_V3_DATA_RULE_COMPLETE = 9,
};
PDX_V3_DATA_API DWORD WINAPI PdxV3DataSelectGameRule(PdxV3DataGameRuleRequest* request);
PDX_V3_DATA_API void WINAPI PdxV3DataSelectGameRuleInterface(void* context, void* opaque);

// flags selects this V3 factory. value_handle is the original source Any on
// input and the new context Any on output. No raw pointer input or popup Show.
enum PdxV3DataOwnedContextKind : uint32_t {
    PDX_V3_DATA_RELEASE_COUNTRY_CONTEXT = 1,
    PDX_V3_DATA_POWER_BLOC_CONTEXT = 2,
    PDX_V3_DATA_MILITARY_FORMATION_NAME_CONTEXT = 3,
    PDX_V3_DATA_POWER_BLOC_FORMATION_CONTEXT = 4,
    PDX_V3_DATA_SHIP_NAME_CONTEXT = 5,
};
PDX_V3_DATA_API DWORD WINAPI PdxV3DataOwnedContext(PdxV3DataRequest* request);
PDX_V3_DATA_API void WINAPI PdxV3DataCreateOwnedContextInterface(void* application, void* opaque);

// The original provider Binding (including raw modes0/1) never escapes callback.
// Only copied primitive/string/empty results are returned; no retained Any handle.
// Optional condition is an original native BOOL getter on the same fresh element;
// false or any failure prevents the action. All names remain engine expressions.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataElement(PdxV3DataElementRequest* request);
PDX_V3_DATA_API void WINAPI PdxV3DataDestroyAny(PdxV3DataAny* value);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataEvaluate(PdxV3DataRequest* request);
// Actions17/18 root and19/20 nested copied-only reads/counts. For19/20 pass a
// PdxV3DataScopedProjectionRequest allocation through this same export.
// Existing raw APIs remain unrestricted.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataProjection(PdxV3DataProjectionRequest* request);
// Copy an original Any primitive/string inside the caller's live Access scope.
// No owner/pointer/result handle survives; destroy the input Any in that scope.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataSnapshotCopied(PdxV3DataRequest* request,
    const PdxV3DataAny* value);
// Create initializes a real isolated native context from a valid complete State ID.
// It does not Show, install in the popup manager, Confirm or change a State name.
// out context owns one engine reference. Use ReleaseWrapper after low-level use.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataCreateStateNameContext(uint32_t state_id, uint64_t* context);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataInitializeStateNameContext(uint64_t context);
// Request create: start=State full ID; returns retained native Any/context pointer.
// Request draft: value_handle=context result; start=0 State name, 1..5 hub drafts.
// Draft copies native bytes directly; no unregistered text getter is invented.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataStateNameContext(PdxV3DataRequest* request);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataStateNameDraft(PdxV3DataRequest* request);
// Native CDataCommand is a shared command carrier, not an owned unique command.
// Pointers below are borrowed until source handle release. Clone the primary
// command through its native copy slot before generic CommandSubmit consumes it.
struct PdxV3DataCommandValue { uint64_t command, shared_control, game_command, world; };
PDX_V3_DATA_API DWORD WINAPI PdxV3DataCommand(uint64_t value_handle, PdxV3DataCommandValue* command);
// COMMAND_VALUE request copies the same 32B carrier, including generation world.
// This performs no clone/validate/submit and leaves the source Any owned.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataCommandSnapshot(PdxV3DataRequest* request);
// Request override: start=complete State ID; flags=0 State name, 1..5 hub names.
// Reads stored world-object overrides, NOT localized display names or popup drafts.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataStateNameOverride(PdxV3DataRequest* request);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataSnapshot(PdxV3DataRequest* request);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataRelease(uint64_t* value_handle);
// Creation provider APIs consume the retained native Creation24 result. Count and
// element invoke its actual provider; element writes a Binding64, NOT an Any40.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataCreationCount(uint64_t value_handle, int32_t* count);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataCreationElement(uint64_t value_handle,
                                                    int32_t index, PdxV3DataBinding* binding);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataListCount(PdxV3DataRequest* request);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataListElement(PdxV3DataRequest* request);
// Model pointer is one owned engine ref. Keep source value_handle alive until
// model release: model callbacks can borrow its Context/backing. No UI is invoked.
PDX_V3_DATA_API DWORD WINAPI PdxV3DataCreationModel(uint64_t value_handle,
                                                   int32_t variant, uint64_t* model);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataModelDimensions(uint64_t model, int32_t* dimensions2);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataModelBinding(uint64_t model,
    const int32_t* coordinates2, PdxV3DataBinding* binding);
PDX_V3_DATA_API DWORD WINAPI PdxV3DataReleaseModel(uint64_t* model);

// Optional bounded matrix projection. Inputs are allocation-relative spans.
// With source_bytes!=0 one binding row produces a fresh Creation, then [start,
// start+limit) is projected. Otherwise each input binding row is an output row.
// All field properties compile once. Every native Any/Binding dies in Access.
// output_address owns ONLY copied bytes allocated by VirtualAlloc; the caller
// reads output_size then VirtualFree(...,0,MEM_RELEASE), never a game destructor.
// The buffer begins with cells_count cells followed by exact copied byte spans.
constexpr uint32_t PDX_V3_DATA_PAGE_ROWS = 64, PDX_V3_DATA_PAGE_FIELDS = 32;
struct PdxV3DataPageSpan { uint32_t offset, count; };
struct PdxV3DataPageRequest {
    uint32_t size, error;
    uint64_t world; // output: current world observed inside this callback's Access
    uint32_t fields_offset, fields_count, rows_offset, rows_count;
    uint32_t source_offset, source_bytes, start, limit;
    uint32_t execution_thread_id, source_count, count_valid, row_count;
    uint64_t output_address, output_size;
    uint32_t cells_count, flags;
};
// Optional prefix selection uses the same CreationStep records/semantics as
// actions19/20, but starts in the page's existing typed root binding row.
constexpr uint32_t PDX_V3_DATA_PAGE_SCOPED = 1, PDX_V3_DATA_PAGE_CAPTURE_ARGUMENTS = 2;
constexpr uint32_t PDX_V3_DATA_PAGE_CAPTURE_STEP_ARGUMENTS = 8;
constexpr uint32_t PDX_V3_DATA_PAGE_NATIVE_FIELDS = 16;
// Flag16 replaces ONLY the input field table with explicit typed Field16
// records. Legacy Span8 input and every copied Page output stay unchanged.
enum PdxV3DataNativeFieldKind : uint32_t {
    PDX_V3_DATA_FIELD_EXPRESSION = 0, PDX_V3_DATA_FIELD_DECREE_TYPE_KEY = 1,
    PDX_V3_DATA_FIELD_DECREE_ID = 2, PDX_V3_DATA_FIELD_GAME_RULE_KEY = 3,
};
struct PdxV3DataPageField { uint32_t kind, expression_offset, expression_bytes, reserved; };
constexpr uint32_t PDX_V3_DATA_PAGE_ORIGINAL_SOURCES = 32;
enum PdxV3DataOriginalSourceKind : uint32_t {
    PDX_V3_DATA_SOURCE_EXPRESSION = 0, PDX_V3_DATA_SOURCE_PRESET_STORED_SETTINGS = 1,
    PDX_V3_DATA_SOURCE_FULL_COMPANY_TYPES = 2,
};
// Flag32: single source points to one Source16; grouped table contains Source16
// records. Native profiles have canonical zero expression span, not fake DSL.
struct PdxV3DataPageSource { uint32_t kind, expression_offset, expression_bytes, reserved; };
struct PdxV3DataPageScope {
    uint32_t steps_offset, steps_count, completed_steps;
    uint32_t arguments_offset, arguments_count;
    // Zero in legacy packets. With CAPTURE_STEP_ARGUMENTS this is the offset of
    // steps_count PageSpan entries, each pointing to that step's argument-span
    // table in original native reference SLOT order. Step40 ABI stays unchanged.
    uint32_t reserved;
};
constexpr uint32_t PDX_V3_DATA_PAGE_GROUPED = 4, PDX_V3_DATA_PAGE_SOURCES = 32;
// Appended after PageScope only when GROUPED is set. One typed root binding row.
struct PdxV3DataPageGroup {
    uint32_t sources_offset, sources_count, source_index, reserved;
};
// Grouped owned bytes start here, then observations, Cell80 table and payload.
// range_complete describes this fragment's enumeration, not cross-page history.
struct PdxV3DataPageGroupOutput {
    uint32_t size, next_source, next_row, source_observation_count, has_more, range_complete;
};
struct PdxV3DataSourceObservation {
    uint32_t source_index, error, source_produced, count_valid, source_count, start;
    uint32_t row_count, next, row_offset, finished, range_complete, reserved;
};
// arguments_offset points to PageSpan native-expression records in final-source
// parameter-reference SLOT order. They run with the existing root binding row,
// before any prefix source. Only genuine captured Anys feed native decoders.
struct PdxV3DataPageCell {
    uint32_t row, field, error, kind;
    int32_t type_id; uint32_t binding_mode; // original provider mode; UINT32_MAX if none
    uint64_t raw_bits, offset, bytes, total;
    uint32_t binding_kind, binding_error; // UINT32_MAX kind means no original Any
    uint64_t binding_type_offset, binding_type_bytes; // copied original Any RTTI name
};
// Capability is this additive export, not a guess based on old action numbers.
PDX_V3_DATA_API void WINAPI PdxV3DataProjectPageInterface(void* application, void* opaque);
// Capability alias; one Page implementation/evaluator, not a separate protocol.
PDX_V3_DATA_API void WINAPI PdxV3DataProjectSourcesInterface(void* application, void* opaque);
// Additive alias of the same Page/owned walker. Per-prefix argument Anys are
// captured in the original root frame before any prefix collection is borrowed.
PDX_V3_DATA_API void WINAPI PdxV3DataProjectTypedStepsInterface(void* application, void* opaque);
// Explicit native-field capability; same Page/group/walker and copied results.
PDX_V3_DATA_API void WINAPI PdxV3DataProjectNativeFieldsInterface(void* application, void* opaque);
PDX_V3_DATA_API void WINAPI PdxV3DataProjectOriginalSourcesInterface(void* application, void* opaque);
// Kind2 capability: standalone one-callback full snapshot, start=limit=0.
// No pagination/group/prefix in the first implemented subset; old Page remains.
PDX_V3_DATA_API void WINAPI PdxV3DataProjectCompanyTypesInterface(void* application, void* opaque);
// Kind3 canonical Rule key from the original Gui row or const Setting parent.
// No effective-default kind4 is provided; this is the same Page/walker/copy ABI.
PDX_V3_DATA_API void WINAPI PdxV3DataProjectRuleFieldsInterface(void* application, void* opaque);

// Existing Application callback queue ABI; acquires the proven native (1,0) scope.
PDX_V3_DATA_API void WINAPI PdxV3DataInterface(void* application, void* opaque);
// Same ElementRequest/DataInterface implementation. Action21 observes its world
// in the callback and derives an I32 input from a freshly selected source row.
PDX_V3_DATA_API void WINAPI PdxV3DataInvokeSourceIndexInterface(void* application, void* opaque);
