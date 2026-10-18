#pragma once
#include <Windows.h>
#include <stdint.h>

#if defined(PDX_V3_OPTIONS_BUILD) || defined(PDX_RUNTIME_BUILD)
#define PDX_V3_OPTIONS_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_OPTIONS_API extern "C" __declspec(dllimport)
#endif

// CIngameInitOptions is the native 224-byte derived owner created by11394c0.
// Its inherited clone slot0 creates only a128-byte BASE object: not a full clone.
// These synchronous calls require caller-proven context and live native storage.
// C++ exceptions propagate unless the function explicitly reports a cleaned-up
// CreateFromSettings failure. Native faults are not caught or automatically replayed.
PDX_V3_OPTIONS_API DWORD WINAPI PdxV3OptionsCreate(uint64_t* owned_options) noexcept(false);
PDX_V3_OPTIONS_API DWORD WINAPI PdxV3OptionsDestroy(uint64_t* owned_options) noexcept(false);
// Settings address is a BORROWED112-byte native object at options+8. It remains
// live only while the source options remains owned/live. Not a serialized blob.
PDX_V3_OPTIONS_API DWORD WINAPI PdxV3OptionsSettingsAddress(uint64_t options,
    uint64_t* settings_address) noexcept(false);
// Destination must be a complete native options owner with its settings already
// constructed. Source must be a true native-constructed112-byte settings object.
// Uses native2165cc0 assignment, including two CString deep copies. Self-assignment
// preserves native behavior. Copies base settings ONLY; derived224 fields remain
// the destination's own values. No guessed scalar setter or complete-option clone.
PDX_V3_OPTIONS_API DWORD WINAPI PdxV3OptionsCopySettings(uint64_t options,
    uint64_t source_settings) noexcept(false);
// Construct a fresh complete224-byte owner, then native-assign base settings.
// On assignment C++ failure, destroy that known-constructed destination and report
// ERROR_UNHANDLED_EXCEPTION with zero owner. Factory/cleanup exceptions propagate;
// a published output pointer is never hidden or discarded by this helper.
PDX_V3_OPTIONS_API DWORD WINAPI PdxV3OptionsCreateFromSettings(uint64_t source_settings,
    uint64_t* owned_options, uint64_t* constructed_options = nullptr) noexcept(false);
// constructed_options is optional diagnostic output after the full factory
// returns. It can remain nonzero after cleanup: never treat it as another owner.

// Destroy takes one owned pointer cell and clears it BEFORE the native scalar
// destructor slot1. A thrown destruction leaves uncertain effects; never replay.
// Transferring to existing WorldEntry/LoadOwned also consumes ownership: do not
// later send that former pointer to this destructor. No remote __del__ exists.
enum PdxV3OptionsAction : uint32_t {
    PDX_V3_OPTIONS_CREATE = 1,
    PDX_V3_OPTIONS_DESTROY = 2,
    PDX_V3_OPTIONS_SETTINGS_ADDRESS = 3,
    PDX_V3_OPTIONS_COPY_SETTINGS = 4,
    PDX_V3_OPTIONS_CREATE_FROM_SETTINGS = 5,
};
enum PdxV3OptionsOwnership : uint32_t {
    PDX_V3_OPTIONS_NONE = 0,
    PDX_V3_OPTIONS_OWNED = 1,
    PDX_V3_OPTIONS_RELEASED = 2,
    PDX_V3_OPTIONS_UNKNOWN = 3,
};
struct PdxV3OptionsRequest {
    uint32_t size;
    uint32_t error;
    uint32_t action;
    uint32_t ownership;
    uint64_t options;
    uint64_t source_settings;
    uint64_t settings_address;
    uint64_t operation_options;     // acted-on pointer, may now be released/unknown
    uint32_t started;
    uint32_t execution_thread_id;
    uint32_t reserved0;
    uint32_t reserved1;
};
// Raw void(context,opaque) callback adapter. It does NOT dispatch or add a game
// guard/TLS/permission. Compose it with the public v3_access callback if a scope
// is required. Request/source/code storage stays live until confirmed completion.
// C++ failures report error574/ownershipUNKNOWN after native unwind; no retry.
PDX_V3_OPTIONS_API void WINAPI PdxV3OptionsCallback(void* context, void* opaque) noexcept(false);
