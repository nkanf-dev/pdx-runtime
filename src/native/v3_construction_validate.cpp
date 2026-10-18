#include "v3_construction_validate.h"

namespace {
constexpr uintptr_t application_rva = 0x592d128;
constexpr uintptr_t validator_rva = 0x1176640;
using Factory = DWORD(WINAPI*)(uint64_t*);
using Initialize = DWORD(WINAPI*)(uint64_t, const PdxV3ConstructionRequest*);
using Destroy = DWORD(WINAPI*)(uint64_t*);
using Validate = uint32_t* (__fastcall*)(void*, uint32_t*, void*);
using GuardEnter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using GuardLeave = void (__fastcall*)(void*);
struct alignas(16) AccessStorage { unsigned char bytes[80] = {}; };
struct AccessScope {
    uintptr_t base;
    AccessStorage& storage;
    AccessScope(uintptr_t image_base, AccessStorage& buffer) : base(image_base), storage(buffer) {
        reinterpret_cast<GuardEnter>(base + 0x33bd640)(storage.bytes, 1, 0,
            "pdx-runtime/native/v3_construction_validate.cpp");
    }
    ~AccessScope() { reinterpret_cast<GuardLeave>(base + 0x33bd750)(storage.bytes); }
    AccessScope(const AccessScope&) = delete;
    AccessScope& operator=(const AccessScope&) = delete;
};
template<class T> T field(uintptr_t object, size_t offset) {
    return *reinterpret_cast<const T*>(object + offset);
}
}
DWORD WINAPI PdxV3ValidateConstructionCommand(uint64_t command, uint32_t output[2]) {
    if (!command || !output) return ERROR_INVALID_PARAMETER;
    output[0] = 0;
    output[1] = 0;
    const uintptr_t base = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    reinterpret_cast<Validate>(base + validator_rva)(reinterpret_cast<void*>(command + 0x18), output, nullptr);
    return ERROR_SUCCESS;
}
void WINAPI PdxV3ValidateConstructionInterface(void* application, void* opaque) {
    auto* result = static_cast<PdxV3ConstructionValidationInfo*>(opaque);
    if (!result) return;
    const PdxV3ConstructionValidationInfo input = *result;
    *result = {sizeof(*result), ERROR_INVALID_PARAMETER, input.actor_id, input.state_id,
        input.building_type, input.carrier};
    result->execution_thread_id = GetCurrentThreadId();
    result->application = reinterpret_cast<uintptr_t>(application);
    if (input.size != sizeof(input) || !input.carrier || !input.building_type) return;
    HMODULE carrier = reinterpret_cast<HMODULE>(input.carrier);
    auto factory = reinterpret_cast<Factory>(GetProcAddress(carrier, "PdxV3ConstructionFactory"));
    auto initialize = reinterpret_cast<Initialize>(GetProcAddress(carrier, "PdxV3InitializeConstructionCommand"));
    auto destroy = reinterpret_cast<Destroy>(GetProcAddress(carrier, "PdxV3DestroyConstruction"));
    if (!factory || !initialize || !destroy) { result->error = ERROR_PROC_NOT_FOUND; return; }
    const uintptr_t base = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    const uintptr_t app = reinterpret_cast<uintptr_t>(application);
    if (!app || app != field<uintptr_t>(base, application_rva) || field<uint8_t>(app, 0x258)
        || !field<uintptr_t>(app, 0x608)) { result->error = ERROR_NOT_READY; return; }
    uint64_t owned = 0;
    const PdxV3ConstructionRequest request = {sizeof(request), input.actor_id, input.state_id, 0,
        input.building_type};
    AccessStorage storage;
    try {
        AccessScope scope(base, storage);
        result->world = field<uintptr_t>(app, 0x608);
        DWORD error = ERROR_SUCCESS;
        try {
            error = factory(&owned);
            if (!error) error = initialize(owned, &request);
            uint32_t raw[2] = {};
            if (!error) {
                error = PdxV3ValidateConstructionCommand(owned, raw);
                result->raw_low = raw[0];
                result->raw_high = raw[1];
                result->validated = !error;
            }
        } catch (...) { error = ERROR_UNHANDLED_EXCEPTION; }
        if (owned) {
            DWORD cleanup = destroy(&owned);
            if (!error) error = cleanup;
        }
        result->error = error;
    } catch (...) { result->error = ERROR_UNHANDLED_EXCEPTION; }
}
