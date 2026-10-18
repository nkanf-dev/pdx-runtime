#pragma once
#include "v3_data.h"
#include "data_projection_core.h"
#include <cstring>
#include <vector>

// Applied before validation/compilation. Also scrubs unsupported-result failures
// and C++ exception exits; no source descriptor, pointer or raw Any is published.
struct PdxV3DataCopiedBoundary {
    PdxV3DataRequest* request;
    ~PdxV3DataCopiedBoundary() {
        request->factory = request->object = request->value_handle = 0;
        request->any_type = request->any_vtable = request->payload_address = 0;
        memset(request->raw_any, 0, sizeof(request->raw_any));
        if (request->kind == PDX_V3_DATA_STRING || request->kind == PDX_V3_DATA_EMPTY)
            request->raw_bits = 0;
        if (request->kind < PDX_V3_DATA_EMPTY || request->kind > PDX_V3_DATA_STRING) {
            request->kind = PDX_V3_DATA_EMPTY;
            request->raw_bits = request->payload_size = request->snapshot_bytes = request->total = 0;
        }
    }
};



// Publication is secondary to an already observed native/VM failure. There is
// no extra diagnostic slot in Page's unchanged ABI; never replace a primary
// merely because an owned copied buffer could not be allocated/published.
inline void PdxV3DataPagePublicationFailure(PdxV3DataPageRequest& page, DWORD error) {
    if (!page.error || page.error == ERROR_IO_PENDING) page.error = error;
}
struct PdxV3DataPagePublicationBuffer {
    size_t table_bytes = 0, payload_offset = 0, bytes = 0;
    void* storage = nullptr;
};
// These are the EXACT production publication branches, with the allocator and
// its last-error read injected for CPU regressions. No provider/getter/replay,
// owner/transport/FSM or synthetic available-byte result is introduced.
template<class Allocate, class LastError>
bool PdxV3DataPageAllocatePublication(PdxV3DataPageRequest& page, size_t prefix,
    size_t cell_count, size_t copied_bytes, Allocate allocate, LastError last_error,
    PdxV3DataPagePublicationBuffer& output) {
    output = {};
    if (cell_count > (SIZE_MAX - prefix) / sizeof(PdxV3DataPageCell)) {
        PdxV3DataPagePublicationFailure(page, ERROR_ARITHMETIC_OVERFLOW); return false;
    }
    output.table_bytes = cell_count * sizeof(PdxV3DataPageCell);
    output.payload_offset = prefix + output.table_bytes;
    if (copied_bytes > SIZE_MAX - output.payload_offset) {
        PdxV3DataPagePublicationFailure(page, ERROR_ARITHMETIC_OVERFLOW); return false;
    }
    output.bytes = output.payload_offset + copied_bytes;
    if (!output.bytes) return false;
    output.storage = allocate(output.bytes);
    if (!output.storage) {
        PdxV3DataPagePublicationFailure(page, last_error()); return false;
    }
    return true;
}

// A Page callback can validate many full-ID rows of the same V3 domain.
// Cache only successful original type lookups for THIS callback. Missing types
// are retried; per-row resolver/full-ID/world checks are never cached or skipped.
struct PdxV3DataDomainTypeCache {
    int32_t ids[PDX_V3_DATA_MARKET + 1];
    PdxV3DataDomainTypeCache() noexcept { for (auto& id : ids) id = -1; }
    template<class Lookup>
    int32_t lookup(uint32_t domain, Lookup original) {
        if (!domain || domain >= sizeof(ids) / sizeof(ids[0])) return original();
        if (ids[domain] >= 0) return ids[domain];
        const auto id = original();
        if (id >= 0) ids[domain] = id;
        return id;
    }
};

// V3's original GUI index-input convention. Count is from the fresh provider,
// not a cached UI observation. No action runs when its requested row is absent.
inline DWORD PdxV3DataSourceIndexInput(uint32_t index, uint32_t count, PdxV3DataInput* input) {
    if (index >= count) return ERROR_NOT_FOUND;
    *input = {PDX_V3_DATA_I32, 0, index, 0};
    return ERROR_SUCCESS;
}

// Explicit caller policy for original data-action receipts. Error is not a
// continuation condition: accepted actions can fail during their later copy.
inline bool PdxV3DataContinueAction(const PdxV3DataActionRequest& action, uint32_t continuation) {
    return !continuation || (action.invoked == 1 && action.accepted == 1);
}

// V3 adapter glue: maps its existing status/handle convention to shared core.
inline bool PdxV3DataValuePresent(const uint64_t& value) { return value != 0; }
struct PdxV3DataScopedValue : pdx::data_projection_core::ScopedValue<uint64_t, DWORD (*)(uint64_t*)> {
    using Base = pdx::data_projection_core::ScopedValue<uint64_t, DWORD (*)(uint64_t*)>;
    PdxV3DataScopedValue(uint64_t* value, DWORD (*release)(uint64_t*))
        : Base(value, release, PdxV3DataValuePresent) {}
};
inline DWORD PdxV3DataProjectionError(pdx::data_projection_core::Error error) {
    using E = pdx::data_projection_core::Error;
    switch (error) {
    case E::ok: return ERROR_SUCCESS;
    case E::missing: return ERROR_NOT_FOUND;
    case E::overflow: return ERROR_ARITHMETIC_OVERFLOW;
    case E::wrong_type: case E::read_failed: return ERROR_INVALID_DATA;
    default: return ERROR_INVALID_PARAMETER;
    }
}
inline DWORD PdxV3DataOriginalSubobject(uintptr_t original, uint32_t offset,
                                      uintptr_t expected_vtable, uintptr_t* output) {
    return PdxV3DataProjectionError(pdx::data_projection_core::select_original_subobject(original,
        {offset, alignof(uint64_t), expected_vtable},
        [](uintptr_t address) { return *reinterpret_cast<const uintptr_t*>(address); }, output));
}

// Exact V3 CPdxData parameter-reference40. Original3a799f0 first follows value
// when nonnull and copies that real Any; expression/scopes/arguments are unused.
struct PdxV3DataDirectParameterRef {
    const PdxV3DataAny* value;
    uint64_t expression, scopes, arguments;
    uint8_t tag, padding[7];
};
static_assert(sizeof(PdxV3DataDirectParameterRef) == 40);
inline DWORD PdxV3DataCapturedReferences(PdxV3DataArray* args,
    const std::vector<const PdxV3DataAny*>& captured) {
    if (captured.size() != args->count) return ERROR_INVALID_PARAMETER;
    auto* refs = reinterpret_cast<PdxV3DataDirectParameterRef*>(args->data);
    for (size_t i = 0; i < captured.size(); ++i) refs[i] = {captured[i], 0, 0, 0, 0, {}};
    return ERROR_SUCCESS;
}
// Same receiver initialization as original3a79b80, separate from its lazy-ref
// builder. The original inner3a78cf0 receives these values and frozen refs.
template<class GlobalType>
void PdxV3DataCapturedReceiver(const PdxV3DataProperty* property, const PdxV3DataArray* scopes,
    PdxV3DataScopeValue* context, int32_t* type, GlobalType global_type) {
    int32_t index = -1; std::memcpy(&index, property->bytes + 100, sizeof(index));
    *context = {};
    if (index >= 0 && static_cast<uint32_t>(index) < scopes->count) {
        const auto& scope = reinterpret_cast<const PdxV3DataScope*>(scopes->data)[index];
        context->object = scope.object; *type = scope.type_id;
    } else {
        context->is_any = property->bytes[92] != 0;
        *type = global_type();
    }
}
