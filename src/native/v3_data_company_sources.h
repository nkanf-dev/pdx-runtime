#pragma once
#include "v3_data_original_sources.h"
#include "v3_data_projection_scope.h"
#include <limits>

// V3-only native facts. The shared source core sees opaque tags/steps/arrays;
// no Company class, VM expression, image RVA or ownership protocol lives there.
namespace pdx::v3_company_sources {
inline constexpr uintptr_t slot_getter_rva = 0x7445a0;
inline constexpr uintptr_t database_vtable_rva = 0x4506f68;
inline constexpr uintptr_t wrapper_factory_rva = 0x11fa850;
inline constexpr const char* type_name = "CompanyType";
// Count validity and an actually READ raw DWORD are separate observations.
// A negative signed count/capacity error must not erase already observed bits.
struct CountObservation { uint32_t raw = 0; bool read = false; };
struct Reader : v3_original_sources::Reader {
    CountObservation* observed;
    explicit Reader(CountObservation* value) : observed(value) {}
    bool read_array(uintptr_t address, data_projection_core::PointerArrayDescriptor& value) {
        if (!v3_original_sources::Reader::read_array(address, value)) return false;
        if (observed) { observed->raw = static_cast<uint32_t>(value.count); observed->read = true; }
        return true;
    }
};
inline void observe_count(PdxV3DataPageRequest& page, const CountObservation& observed) {
    if (observed.read) page.source_count = observed.raw;
}
inline original_source_core::Error resolve(uintptr_t original_slot, uintptr_t image,
                                          original_source_core::View& view, CountObservation* observed = nullptr) {
    if (observed) *observed = {};
    const uintptr_t database_tag[] = {image + database_vtable_rva};
    const original_source_core::PointerStep steps[] = {{0, {database_tag, 1}}};
    const original_source_core::Descriptor profile{{}, steps, 1,
        original_source_core::Kind::pointer_array, 0x80, {}, sizeof(uintptr_t), INT32_MAX};
    Reader reader(observed);
    return original_source_core::resolve(original_slot, profile, reader, view);
}
// EXACT Page.open native-source early-return branch: read/validate once,
// publish only actually observed count bits, then return the original status.
// No invalid count can reach a row loop or placeholder allocation.
inline DWORD open_page_source(PdxV3DataPageRequest& page, uintptr_t slot, uintptr_t image,
                              original_source_core::View& view) {
    CountObservation observed;
    const auto error = resolve(slot, image, view, &observed);
    observe_count(page, observed);
    return PdxV3DataProjectionError(error);
}
inline original_source_core::Error element(const original_source_core::View& view,
                                          uint64_t index, uintptr_t& definition) {
    const original_source_core::Descriptor profile{{}, nullptr, 0,
        original_source_core::Kind::pointer_array, 0x80, {}, sizeof(uintptr_t), INT32_MAX};
    v3_original_sources::Reader reader;
    return original_source_core::element(profile, view, index, reader, definition);
}
// Original factory owns allocation/partial unwind. Only its NORMAL RETURN
// exposes a complete CPdxPtr with one caller reference (not ref0/cache-borrow).
// Do not retain it again, fabricate an Any, free raw storage or guess cleanup
// for an owner never returned by a throwing native factory.
template<class Factory>
uintptr_t construct_wrapper(uintptr_t definition, Factory factory) {
    void* cell = nullptr;
    factory(&cell, &definition);
    return reinterpret_cast<uintptr_t>(cell);
}
// Page's Cell80 count is uint32 even though output byte offsets are uint64.
// Check BEFORE allocation/getters; required source count remains observable.
inline bool cell_count(uint64_t rows, uint32_t fields, size_t& cells) {
    if (!fields || rows > UINT32_MAX / uint64_t(fields)) return false;
    const uint64_t count = rows * fields;
    if (count > std::numeric_limits<size_t>::max() / sizeof(PdxV3DataPageCell)) return false;
    cells = static_cast<size_t>(count); return true;
}
}
