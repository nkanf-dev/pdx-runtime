#include "v3_options.h"
#include <cstddef>

static_assert(sizeof(PdxV3OptionsRequest) == 64);
static_assert(offsetof(PdxV3OptionsRequest, options) == 16);
static_assert(offsetof(PdxV3OptionsRequest, started) == 48);

namespace {
constexpr uintptr_t factory_rva = 0x11394c0;
constexpr uintptr_t assign_rva = 0x2165cc0;
using Factory = void* (__fastcall*)(void**);
using Assign = void* (__fastcall*)(void*, const void*);
using Destroy = void* (__fastcall*)(void*, uint32_t);
uintptr_t image() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
uint8_t settings_constructed(uint64_t options) {
    return *reinterpret_cast<const uint8_t*>(static_cast<uintptr_t>(options) + 0x78);
}
}

DWORD WINAPI PdxV3OptionsCreate(uint64_t* owned) noexcept(false) {
    if (!owned || *owned) return ERROR_INVALID_PARAMETER;
    // Native factory constructs the entire224B derived object, not a base copy.
    reinterpret_cast<Factory>(image() + factory_rva)(reinterpret_cast<void**>(owned));
    return *owned ? ERROR_SUCCESS : ERROR_NOT_ENOUGH_MEMORY;
}
DWORD WINAPI PdxV3OptionsDestroy(uint64_t* owned) noexcept(false) {
    if (!owned || !*owned) return ERROR_INVALID_PARAMETER;
    void* options = reinterpret_cast<void*>(static_cast<uintptr_t>(*owned));
    const auto table = *reinterpret_cast<const uintptr_t* const*>(options);
    *owned = 0;
    // Complete derived object's scalar destructor is slot1, never clone slot0.
    reinterpret_cast<Destroy>(table[1])(options, 1);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3OptionsSettingsAddress(uint64_t options, uint64_t* settings) noexcept(false) {
    if (!options || !settings) return ERROR_INVALID_PARAMETER;
    *settings = 0;
    if (!settings_constructed(options)) return ERROR_INVALID_STATE;
    *settings = options + 8;
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3OptionsCopySettings(uint64_t options, uint64_t source) noexcept(false) {
    if (!options || !source) return ERROR_INVALID_PARAMETER;
    if (!settings_constructed(options)) return ERROR_INVALID_STATE;
    reinterpret_cast<Assign>(image() + assign_rva)(
        reinterpret_cast<void*>(static_cast<uintptr_t>(options) + 8),
        reinterpret_cast<const void*>(static_cast<uintptr_t>(source)));
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3OptionsCreateFromSettings(uint64_t source, uint64_t* owned,
    uint64_t* constructed) noexcept(false) {
    if (!source || !owned || *owned || constructed == owned) return ERROR_INVALID_PARAMETER;
    if (constructed) *constructed = 0;
    const DWORD error = PdxV3OptionsCreate(owned);
    if (error) return error;
    if (constructed) *constructed = *owned;
    DWORD assigned = ERROR_SUCCESS;
    try {
        assigned = PdxV3OptionsCopySettings(*owned, source);
    } catch (...) {
        // Both native destination CString objects were constructed by the full
        // factory before assignment. Native assignment keeps them destructible
        // even if a string allocation fails. Never touch/destroy the source.
        PdxV3OptionsDestroy(owned);
        return ERROR_UNHANDLED_EXCEPTION;
    }
    // Keep cleanup outside the assignment catch. A throwing destructor must
    // propagate to the callback's UNKNOWN state, not be mistaken for no owner.
    if (assigned) PdxV3OptionsDestroy(owned);
    return assigned;
}
void WINAPI PdxV3OptionsCallback(void*, void* opaque) noexcept(false) {
    auto* r = static_cast<PdxV3OptionsRequest*>(opaque);
    if (!r) return;
    if (r->size != sizeof(*r)) { r->error = ERROR_INVALID_PARAMETER; return; }
    r->error = ERROR_IO_PENDING;
    r->ownership = r->options ? PDX_V3_OPTIONS_OWNED : PDX_V3_OPTIONS_NONE;
    r->settings_address = 0;
    r->operation_options = r->options;
    r->started = 0;
    r->execution_thread_id = GetCurrentThreadId();
    try {
        switch (r->action) {
        case PDX_V3_OPTIONS_CREATE:
            r->started = 1;
            r->error = PdxV3OptionsCreate(&r->options);
            if (!r->error) r->ownership = PDX_V3_OPTIONS_OWNED;
            break;
        case PDX_V3_OPTIONS_DESTROY:
            r->started = 1;
            r->error = PdxV3OptionsDestroy(&r->options);
            if (!r->error) r->ownership = PDX_V3_OPTIONS_RELEASED;
            break;
        case PDX_V3_OPTIONS_SETTINGS_ADDRESS:
            r->started = 1;
            r->error = PdxV3OptionsSettingsAddress(r->options, &r->settings_address);
            break;
        case PDX_V3_OPTIONS_COPY_SETTINGS:
            r->started = 1;
            r->error = PdxV3OptionsCopySettings(r->options, r->source_settings);
            break;
        case PDX_V3_OPTIONS_CREATE_FROM_SETTINGS:
            r->started = 1;
            r->error = PdxV3OptionsCreateFromSettings(r->source_settings, &r->options,
                &r->operation_options);
            r->ownership = r->options ? PDX_V3_OPTIONS_OWNED : PDX_V3_OPTIONS_NONE;
            break;
        default:
            r->error = ERROR_INVALID_PARAMETER;
        }
    } catch (...) {
        // The output pointer and operation marker remain observable even if
        // native construction published a pointer or destruction began first.
        r->error = ERROR_UNHANDLED_EXCEPTION;
        r->ownership = PDX_V3_OPTIONS_UNKNOWN;
    }
}
