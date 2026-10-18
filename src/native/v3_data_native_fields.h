#pragma once
#include "v3_data.h"
#include <cstdint>
#include <cstring>
#include <new>

// V3-only native layout/copy profile. Shared walkers know none of these facts.
namespace pdx::v3_native_fields {
inline constexpr uintptr_t decree_type_raw_any_rva = 0x5146980;
inline constexpr uintptr_t cstring_copy_rva = 0x61c500, cstring_destroy_rva = 0x61f810;
// The production parser and owned wire host use this exact typed-table check.
// Text spans are checked by the caller against its original request allocation.
template<class Text>
bool valid_fields(const PdxV3DataPageField* fields, size_t count, Text text) {
    bool any_native = false;
    for (size_t i = 0; i < count; ++i) {
        const auto& field = fields[i];
        if (field.reserved || field.kind > PDX_V3_DATA_FIELD_GAME_RULE_KEY) return false;
        if ((field.kind == PDX_V3_DATA_FIELD_DECREE_ID || field.kind == PDX_V3_DATA_FIELD_GAME_RULE_KEY)
            && (field.expression_offset || field.expression_bytes)) return false;
        if (field.kind == PDX_V3_DATA_FIELD_EXPRESSION) {
            if (!text(PdxV3DataPageSpan{field.expression_offset, field.expression_bytes})) return false;
        } else {
            any_native = true;
            if (field.expression_bytes ? !text(PdxV3DataPageSpan{field.expression_offset, field.expression_bytes})
                                       : field.expression_offset != 0) return false;
        }
    }
    return any_native;
}
inline uintptr_t pointer_at(uintptr_t address) {
    uintptr_t value = 0; std::memcpy(&value, reinterpret_cast<const void*>(address), sizeof(value)); return value;
}
inline uint64_t integer_at(const void* address) {
    uint64_t value = 0; std::memcpy(&value, address, sizeof(value)); return value;
}
// Consume the ACTUAL mutable raw Any. No named binding, retag, ref increment,
// borrowed-wrapper release, or equivalence with the distinct return TypeInfo.
inline DWORD borrowed_decree_type(uintptr_t image, const PdxV3DataAny& any, uintptr_t* wrapper) {
    if (!wrapper) return ERROR_INVALID_PARAMETER;
    *wrapper = 0;
    const auto tagged = integer_at(any.bytes);
    if ((tagged & ~uintptr_t(1)) != image + decree_type_raw_any_rva) return ERROR_NOT_SUPPORTED;
    const auto payload = tagged & 1 ? pointer_at(reinterpret_cast<uintptr_t>(any.bytes) + 8)
                                   : reinterpret_cast<uintptr_t>(any.bytes) + 8;
    if (!payload) return ERROR_INVALID_DATA;
    *wrapper = pointer_at(payload);
    return ERROR_SUCCESS;
}
// Exact original CDataDecree full32 ID word, not a universal CData origin rule.
inline DWORD decree_id(uintptr_t wrapper, uint32_t* id) {
    if (!id) return ERROR_INVALID_PARAMETER;
    *id = 0;
    if (!wrapper) return ERROR_NOT_FOUND;
    std::memcpy(id, reinterpret_cast<const void*>(wrapper + 0x10), sizeof(*id));
    return ERROR_SUCCESS;
}
// CopyCtor and Destroy are the original CString ABI in production. Tests inject
// owned-process functions, never game VM/type/layout substitutes. A completed
// copy is consumed before any cleanup; cleanup failure cannot erase its bytes.
// Callers pass a positively proved native CString address; no universal
// definition/wrapper layout or type conversion is implied by this mechanism.
template<class CopyCtor, class Destroy, class Consume>
DWORD copy_cstring_at(uintptr_t cstring, CopyCtor copy, Destroy destroy, Consume consume) {
    if (!cstring) return ERROR_NOT_FOUND;
    alignas(8) unsigned char destination[32]{};
    bool completed = false;
    DWORD status = ERROR_SUCCESS;
    try {
        copy(destination, reinterpret_cast<const void*>(cstring));
        completed = true;
        const auto count = integer_at(destination + 0x10);
        const auto capacity = integer_at(destination + 0x18);
        const auto data = capacity <= 15 ? reinterpret_cast<uintptr_t>(destination)
                                        : pointer_at(reinterpret_cast<uintptr_t>(destination));
        if (count > capacity || (count && !data)) status = ERROR_INVALID_DATA;
        else consume(reinterpret_cast<const void*>(data), count);
    } catch (const std::bad_alloc&) { status = ERROR_NOT_ENOUGH_MEMORY; }
      catch (...) { status = ERROR_UNHANDLED_EXCEPTION; }
    // Never destroy a destination whose native construction did not return.
    if (completed) {
        try { destroy(destination); }
        catch (...) { if (!status) status = ERROR_UNHANDLED_EXCEPTION; }
    }
    return status;
}
template<class CopyCtor, class Destroy, class Consume>
DWORD copy_decree_type_key(uintptr_t wrapper, CopyCtor copy, Destroy destroy, Consume consume) {
    if (!wrapper) return ERROR_NOT_FOUND;
    const auto definition = pointer_at(wrapper + 0x10);
    if (!definition) return ERROR_NOT_FOUND;
    return copy_cstring_at(definition + 0x18, copy, destroy, consume);
}
}
