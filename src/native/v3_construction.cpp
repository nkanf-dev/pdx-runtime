#include "v3_construction.h"
#include <cstring>

namespace {
constexpr uintptr_t application_rva = 0x592d128;
constexpr uintptr_t session_rva = 0x5956f70;
constexpr uintptr_t factory_rva = 0x11847c0;
constexpr uintptr_t submit_rva = 0xcd2d00;
constexpr uintptr_t country_resolver_rva = 0x7bb480;
constexpr uintptr_t state_resolver_rva = 0xc5af10;
constexpr uintptr_t optional_reference_rva = 0x5963ea8;
constexpr uintptr_t country_fallback_rva = 0x5964e18;
constexpr uintptr_t state_fallback_rva = 0x5964e28;
using Factory = void* (__fastcall*)();
using Submit = unsigned char (__fastcall*)(void**, uint32_t);
using Delete = void* (__fastcall*)(void*, uint32_t);
using Resolve = void* (__fastcall*)(const uint32_t*);
SRWLOCK state_lock = SRWLOCK_INIT;
PdxV3ConstructionStatusInfo state = {sizeof(PdxV3ConstructionStatusInfo)};
uint64_t next_request = 0;
bool pending = false;
bool awaiting_effect = false;
thread_local bool draining = false;

uintptr_t module_base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T& field(uint64_t object, size_t offset) {
    return *reinterpret_cast<T*>(static_cast<uintptr_t>(object) + offset);
}
DWORD read_bytes(uint64_t address, void* output, size_t size) {
    SIZE_T received = 0;
    BOOL ok = ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address),
        output, size, &received);
    return ok && received == size ? ERROR_SUCCESS : (ok ? ERROR_PARTIAL_COPY : GetLastError());
}
DWORD request_valid(const PdxV3ConstructionRequest* request) {
    if (!request || request->size != sizeof(*request) || !request->building_type)
        return ERROR_INVALID_PARAMETER;
    return ERROR_SUCCESS;
}
DWORD resolve_country(uintptr_t base, uint32_t id, uint64_t* country) {
    void* value = reinterpret_cast<Resolve>(base + country_resolver_rva)(&id);
    *country = reinterpret_cast<uintptr_t>(value);
    if (!value || value == *reinterpret_cast<void**>(base + country_fallback_rva)
        || field<uint32_t>(*country, 0x18) != id) return ERROR_NOT_FOUND;
    return ERROR_SUCCESS;
}
DWORD resolve_state(uintptr_t base, uint32_t id) {
    void* value = reinterpret_cast<Resolve>(base + state_resolver_rva)(&id);
    if (!value || value == *reinterpret_cast<void**>(base + state_fallback_rva)
        || field<uint32_t>(reinterpret_cast<uintptr_t>(value), 8) != id)
        return ERROR_NOT_FOUND;
    return ERROR_SUCCESS;
}
DWORD observe_effect(uintptr_t base, const PdxV3ConstructionStatusInfo& previous) {
    PdxV3ConstructionQueueInfo after = {sizeof(PdxV3ConstructionQueueInfo)};
    uint64_t country = 0;
    DWORD error = ERROR_SUCCESS;
    try {
        const uint64_t app = *reinterpret_cast<uint64_t*>(base + application_rva);
        if (!app || field<uint64_t>(app, 0x608) != previous.world)
            error = ERROR_INVALID_STATE;
        if (!error) error = resolve_country(base, previous.request.actor_id, &country);
        if (!error) error = PdxV3ReadConstructionQueue(country, previous.request.state_id,
            previous.request.building_type, &after);
    } catch (...) { error = ERROR_UNHANDLED_EXCEPTION; }
    const bool observed = !error && !previous.before.error
        && after.matching_count == previous.before.matching_count + 1;
    AcquireSRWLockExclusive(&state_lock);
    state.after = after;
    state.error = error;
    state.effect_observed = observed;
    state.phase = observed ? PDX_V3_CONSTRUCTION_EFFECT_OBSERVED : PDX_V3_CONSTRUCTION_EFFECT_UNKNOWN;
    state.completed_request_id = previous.request_id;
    awaiting_effect = false;
    ReleaseSRWLockExclusive(&state_lock);
    return error;
}
}

DWORD WINAPI PdxV3ConstructionFactory(uint64_t* owned) {
    if (!owned || *owned) return ERROR_INVALID_PARAMETER;
    try {
        *owned = reinterpret_cast<uintptr_t>(reinterpret_cast<Factory>(module_base() + factory_rva)());
        return *owned ? ERROR_SUCCESS : ERROR_NOT_ENOUGH_MEMORY;
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3InitializeConstructionCommand(uint64_t command,
    const PdxV3ConstructionRequest* request) {
    DWORD error = request_valid(request);
    if (error || !command) return ERROR_INVALID_PARAMETER;
    field<uint32_t>(command, 0x20) = request->actor_id;
    field<uint32_t>(command, 0x28) = request->state_id;
    field<uint64_t>(command, 0x30) = request->building_type;
    // Native tag2 paths only consume ID/tag; zero their inactive context bytes.
    std::memset(reinterpret_cast<void*>(command + 0x38), 0, 24);
    field<uint32_t>(command, 0x38) = request->actor_id;
    field<uint8_t>(command, 0x48) = 2;
    field<int32_t>(command, 0x50) = 1;
    field<int32_t>(command, 0x54) = 1;
    field<int32_t>(command, 0x58) = -1;
    field<uint64_t>(command, 0x60) = *reinterpret_cast<uint64_t*>(module_base() + optional_reference_rva);
    field<uint8_t>(command, 0x68) = 0;
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3SubmitConstruction(uint64_t* owned, uint32_t flags, uint32_t* accepted) {
    if (!owned || !*owned || !accepted) return ERROR_INVALID_PARAMETER;
    *accepted = 0;
    void* command = reinterpret_cast<void*>(*owned);
    // cd2d00 consumes an owning pointer. On an exception its outcome is unknown.
    *owned = 0;
    try {
        *accepted = reinterpret_cast<Submit>(module_base() + submit_rva)(&command, flags) != 0;
        return ERROR_SUCCESS;
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3DestroyConstruction(uint64_t* owned) {
    if (!owned) return ERROR_INVALID_PARAMETER;
    if (!*owned) return ERROR_SUCCESS;
    void* command = reinterpret_cast<void*>(*owned);
    *owned = 0;
    try {
        auto table = *reinterpret_cast<uintptr_t**>(command);
        reinterpret_cast<Delete>(table[0])(command, 1);
        return ERROR_SUCCESS;
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3ReadConstructionQueue(uint64_t country, uint32_t state_id,
    uint64_t building_type, PdxV3ConstructionQueueInfo* result) {
    if (!country || !result) return ERROR_INVALID_PARAMETER;
    *result = {sizeof(*result)};
    result->country = country;
    result->queue = country + 0xa30;
    result->state_id = state_id;
    result->building_type = building_type;
    result->first_matching_index = UINT32_MAX;
    struct VectorHeader { uint64_t records; int32_t capacity; int32_t count; } header = {};
    DWORD error = read_bytes(country + 0xa90, &header, sizeof(header));
    if (!error && (header.count < 0 || header.capacity < header.count
        || (header.count && !header.records))) error = ERROR_INVALID_DATA;
    if (!error) {
        result->records = header.records;
        result->capacity = static_cast<uint32_t>(header.capacity);
        result->count = static_cast<uint32_t>(header.count);
        struct RecordKey { uint32_t state_id; uint32_t padding; uint64_t building_type; } key = {};
        for (uint32_t i = 0; i < result->count; ++i) {
            error = read_bytes(header.records + static_cast<uint64_t>(i) * 0x48, &key, sizeof(key));
            if (error) break;
            if (key.state_id == state_id && key.building_type == building_type) {
                if (!result->matching_count) result->first_matching_index = i;
                ++result->matching_count;
            }
        }
    }
    result->error = error;
    return error;
}
DWORD WINAPI PdxV3QueueConstruction(const PdxV3ConstructionRequest* request) {
    DWORD error = request_valid(request);
    if (error) return error;
    AcquireSRWLockExclusive(&state_lock);
    if (pending || awaiting_effect || state.phase == PDX_V3_CONSTRUCTION_SUBMITTING)
        error = ERROR_BUSY;
    else {
        state = {sizeof(state)};
        state.phase = PDX_V3_CONSTRUCTION_QUEUED;
        state.request_id = ++next_request;
        state.request = *request;
        pending = true;
    }
    ReleaseSRWLockExclusive(&state_lock);
    return error;
}
DWORD WINAPI PdxV3ConstructionStatus(PdxV3ConstructionStatusInfo* result) {
    if (!result) return ERROR_INVALID_PARAMETER;
    AcquireSRWLockShared(&state_lock);
    *result = state;
    ReleaseSRWLockShared(&state_lock);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3DrainConstruction(void* session) {
    const uintptr_t base = module_base();
    if (reinterpret_cast<uintptr_t>(session) != base + session_rva) return ERROR_INVALID_PARAMETER;
    if (draining) return ERROR_BUSY;
    draining = true;
    AcquireSRWLockExclusive(&state_lock);
    const bool observing = awaiting_effect;
    const PdxV3ConstructionStatusInfo previous = state;
    if (!pending && !observing) {
        ReleaseSRWLockExclusive(&state_lock);
        draining = false;
        return ERROR_SUCCESS;
    }
    pending = false;
    state.execution_thread_id = GetCurrentThreadId();
    state.main_module_base = base;
    state.session = reinterpret_cast<uintptr_t>(session);
    state.phase = PDX_V3_CONSTRUCTION_SUBMITTING;
    ReleaseSRWLockExclusive(&state_lock);
    if (observing) {
        DWORD error = observe_effect(base, previous);
        draining = false;
        return error;
    }
    DWORD error = ERROR_SUCCESS;
    uint64_t owned = 0, country = 0;
    uint32_t accepted = 0;
    PdxV3ConstructionQueueInfo before = {};
    uint64_t app = *reinterpret_cast<uint64_t*>(base + application_rva);
    const uint64_t world = app ? field<uint64_t>(app, 0x608) : 0;
    try {
        if (!world || !field<uint64_t>(reinterpret_cast<uintptr_t>(session), 0x3d8))
            error = ERROR_NOT_READY;
        if (!error) error = resolve_country(base, previous.request.actor_id, &country);
        if (!error) error = resolve_state(base, previous.request.state_id);
        if (!error) error = PdxV3ReadConstructionQueue(country, previous.request.state_id,
            previous.request.building_type, &before);
        if (!error) error = PdxV3ConstructionFactory(&owned);
        if (!error) error = PdxV3InitializeConstructionCommand(owned, &previous.request);
        if (!error) error = PdxV3SubmitConstruction(&owned, 4, &accepted);
        if (owned) PdxV3DestroyConstruction(&owned);
    } catch (...) { error = ERROR_UNHANDLED_EXCEPTION; }
    AcquireSRWLockExclusive(&state_lock);
    state.world = world;
    state.before = before;
    state.error = error;
    state.accepted = accepted;
    state.phase = error ? PDX_V3_CONSTRUCTION_FAILED
        : accepted ? PDX_V3_CONSTRUCTION_ACCEPTED : PDX_V3_CONSTRUCTION_REJECTED;
    awaiting_effect = !error && accepted;
    if (!awaiting_effect) state.completed_request_id = previous.request_id;
    ReleaseSRWLockExclusive(&state_lock);
    draining = false;
    return error;
}
