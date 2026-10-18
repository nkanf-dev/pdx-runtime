#pragma once
#include "v3_data.h"
#include "original_source_core.h"
#include <cstring>

namespace pdx::v3_original_sources {
// Profile facts stay here. The shared resolver sees only opaque pointer steps,
// array shape, and an injected reader; it knows no GUI/Data type or game layout.
inline const original_source_core::Descriptor& preset_settings() {
    static const original_source_core::PointerStep steps[] = {{0, {}}};
    static const original_source_core::Descriptor descriptor{{}, steps, 1,
        original_source_core::Kind::pointer_array, 0x28, {}, sizeof(uintptr_t), INT32_MAX};
    return descriptor;
}
struct Reader {
    bool read_pointer(uintptr_t address, uintptr_t& value) {
        std::memcpy(&value, reinterpret_cast<const void*>(address), sizeof(value)); return true;
    }
    bool read_array(uintptr_t address, data_projection_core::PointerArrayDescriptor& value) {
        PdxV3DataArray array{}; std::memcpy(&array, reinterpret_cast<const void*>(address), sizeof(array));
        value = {array.data, array.count, array.capacity, sizeof(uintptr_t), INT32_MAX}; return true;
    }
    bool matches_tags(uintptr_t address, original_source_core::Tags tags) {
        uintptr_t actual = 0; read_pointer(address, actual);
        for (size_t i = 0; i < tags.count; ++i) if (actual == tags.values[i]) return true;
        return false;
    }
};
inline original_source_core::Error resolve_preset(uintptr_t original_slot, original_source_core::View& view) {
    Reader reader; return original_source_core::resolve(original_slot, preset_settings(), reader, view);
}
inline original_source_core::Error preset_element(const original_source_core::View& view,
    uint64_t index, uintptr_t& original) {
    Reader reader; return original_source_core::element(preset_settings(), view, index, reader, original);
}
template<class Text>
bool valid_source(const PdxV3DataPageSource& source, Text text) {
    if (source.reserved) return false;
    if (source.kind == PDX_V3_DATA_SOURCE_EXPRESSION)
        return text(PdxV3DataPageSpan{source.expression_offset, source.expression_bytes});
    return (source.kind == PDX_V3_DATA_SOURCE_PRESET_STORED_SETTINGS
            || source.kind == PDX_V3_DATA_SOURCE_FULL_COMPANY_TYPES)
        && !source.expression_offset && !source.expression_bytes;
}
}
