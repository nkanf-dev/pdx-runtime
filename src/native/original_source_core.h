#pragma once
#include "data_projection_core.h"
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>

// Game-free original-source projection and lifetime orchestration. Adapters
// inject root production, memory reads, tag meaning, array ABI and VM/copy APIs.
// A tag is an opaque adapter key; it is not assumed to be an RVA or a vtable.
// Borrowed addresses below are internal to one run(), never returned as leases.
namespace pdx::original_source_core {
using Error = data_projection_core::Error;
struct Tags { const uintptr_t* values = nullptr; size_t count = 0; };
struct PointerStep { size_t offset = 0; Tags target_tags{}; };
enum class Kind { pointer_member, pointer_array };
struct Descriptor {
    Tags root_tags{};
    const PointerStep* steps = nullptr;
    size_t step_count = 0;
    Kind kind = Kind::pointer_member;
    size_t field_offset = 0;
    Tags element_tags{};
    size_t element_stride = sizeof(uintptr_t);
    uint64_t maximum_count = 0;
};
struct View {
    uintptr_t owner = 0, singleton = 0;
    data_projection_core::PointerArrayDescriptor array{};
    uint64_t count = 0;
    bool source_produced = false, count_valid = false;
};
constexpr size_t maximum_pointer_depth = 8;
inline bool add(uintptr_t pointer, size_t offset, uintptr_t& output) {
    if (offset > std::numeric_limits<uintptr_t>::max() - pointer) return false;
    output = pointer + offset; return true;
}
inline bool valid_tags(Tags tags) { return !tags.count || tags.values; }

template<class Adapter>
Error resolve(uintptr_t root, const Descriptor& d, Adapter& adapter, View& view) {
    view = {};
    if (d.step_count > maximum_pointer_depth || (d.step_count && !d.steps)
        || !valid_tags(d.root_tags) || !valid_tags(d.element_tags)
        || !d.maximum_count || d.element_stride != sizeof(uintptr_t)) return Error::invalid;
    if (!root) return Error::missing;
    if (d.root_tags.count && !adapter.matches_tags(root, d.root_tags)) return Error::wrong_type;
    auto owner = root;
    for (size_t index = 0; index < d.step_count; ++index) {
        const auto& step = d.steps[index]; uintptr_t address = 0, target = 0;
        if (!valid_tags(step.target_tags)) return Error::invalid;
        if (!add(owner, step.offset, address)) return Error::overflow;
        if (!adapter.read_pointer(address, target)) return Error::read_failed;
        if (!target) return Error::missing;
        if (step.target_tags.count && !adapter.matches_tags(target, step.target_tags)) return Error::wrong_type;
        owner = target;
    }
    view.owner = owner; view.source_produced = true;
    uintptr_t field = 0;
    if (!add(owner, d.field_offset, field)) return Error::overflow;
    if (d.kind == Kind::pointer_member) {
        if (!adapter.read_pointer(field, view.singleton)) return Error::read_failed;
        if (view.singleton && d.element_tags.count
            && !adapter.matches_tags(view.singleton, d.element_tags)) return Error::wrong_type;
        view.count = view.singleton ? 1 : 0;
    } else if (d.kind == Kind::pointer_array) {
        if (!adapter.read_array(field, view.array)) return Error::read_failed;
        view.array.stride = d.element_stride; view.array.maximum_count = d.maximum_count;
        const auto status = data_projection_core::validate_pointer_array(view.array);
        if (status != Error::ok) return status;
        view.count = view.array.count;
    } else return Error::invalid;
    if (view.count > d.maximum_count) return Error::invalid;
    view.count_valid = true; return Error::ok;
}

template<class Adapter>
Error element(const Descriptor& d, const View& view, uint64_t index,
              Adapter& adapter, uintptr_t& output) {
    output = 0;
    if (!view.source_produced || !view.count_valid || index >= view.count) return Error::missing;
    if (d.kind == Kind::pointer_member) output = view.singleton;
    else {
        const auto status = data_projection_core::pointer_element(view.array, index,
            [&](uintptr_t address, uintptr_t& value) { return adapter.read_pointer(address, value); }, &output);
        if (status != Error::ok) return status;
    }
    if (!output) return Error::missing;
    if (d.element_tags.count && !adapter.matches_tags(output, d.element_tags)) return Error::wrong_type;
    return Error::ok;
}

// Compile all consumers before obtaining any transient root. Lexical RAII order:
// field Any (inside consume) -> Binding -> Root -> Properties -> caller's gate.
// The same mechanism handles pointer chains, nullable singletons and arrays.
// No game class, numeric type ID, layout, native ABI or Win32 status lives here.
template<class Adapter>
typename Adapter::Status run(Adapter& adapter) {
    typename Adapter::Properties properties;
    auto status = adapter.compile_all(properties);
    if (!adapter.ok(status)) return status;
    typename Adapter::Root root;
    status = adapter.produce_root(root);
    if (!adapter.ok(status)) return status;
    View view;
    const auto source_error = resolve(adapter.root_object(root), adapter.descriptor(), adapter, view);
    adapter.observe_source(view);
    if (source_error != Error::ok) return adapter.source_error(source_error);
    const auto start = adapter.start_index();
    if (start > view.count) return adapter.stale_ordinal();
    const auto count = std::min<uint64_t>(view.count - start, adapter.item_limit());
    adapter.observe_window(start, count, view.count);
    for (uint64_t position = 0; position < count; ++position) {
        const auto index = start + position;
        adapter.begin_record(index);
        uintptr_t original = 0;
        const auto selected = element(adapter.descriptor(), view, index, adapter, original);
        if (selected != Error::ok) { adapter.record_error(adapter.source_error(selected)); continue; }
        typename Adapter::Binding binding;
        status = adapter.bind(original, properties, binding);
        if (!adapter.ok(status)) { adapter.record_error(status); continue; }
        status = adapter.consume(original, binding, properties);
        if (!adapter.ok(status)) adapter.record_error(status);
    }
    return adapter.finish();
}
}
