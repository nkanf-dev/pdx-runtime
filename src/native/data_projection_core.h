#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <type_traits>

// Shared, game-independent mechanisms. Native layouts, type encodings,
// constructors, dispatch/access ABI and error-code mappings are injected by
// adapters. No source address or wrapper here confers an external lifetime.
namespace pdx::data_projection_core {
enum class Error { ok, invalid, missing, wrong_type, overflow, read_failed };

template<class Handle, class Release>
class ScopedValue {
    Handle* value_;
    Release release_;
    bool (*present_)(const Handle&);
public:
    ScopedValue(Handle* value, Release release, bool (*present)(const Handle&))
        : value_(value), release_(release), present_(present) {}
    ~ScopedValue() { if (present_(*value_)) release_(value_); }
    ScopedValue(const ScopedValue&) = delete;
    ScopedValue& operator=(const ScopedValue&) = delete;
};

// A producer may publish its owned handle before a later copy throws. Capture
// that publication on both return and unwind, before the enclosing owner dies.
// The injected reader only copies the adapter's handle field and must not throw.
template<class Handle, class Produce, class ReadPublished>
auto capture_produced(Handle& destination, Produce produce, ReadPublished read_published)
    -> decltype(produce()) {
    static_assert(noexcept(read_published()), "published-handle reader must not throw");
    static_assert(std::is_nothrow_copy_assignable<Handle>::value, "published handle assignment must not throw");
    struct Capture {
        Handle& destination; ReadPublished read;
        ~Capture() noexcept { destination = read(); }
    } capture{destination, read_published};
    return produce();
}

struct SubobjectDescriptor { size_t byte_offset, alignment; uintptr_t expected_type_tag; };
template<class ReadTypeTag>
Error select_original_subobject(uintptr_t original, const SubobjectDescriptor& descriptor,
                               ReadTypeTag read_type_tag, uintptr_t* output) {
    if (!output) return Error::invalid;
    *output = 0;
    if (!descriptor.byte_offset || !descriptor.alignment
        || descriptor.byte_offset % descriptor.alignment || !descriptor.expected_type_tag) return Error::invalid;
    if (!original) return Error::missing;
    if (original > std::numeric_limits<uintptr_t>::max() - descriptor.byte_offset) return Error::overflow;
    if (read_type_tag(original) != descriptor.expected_type_tag) return Error::wrong_type;
    *output = original + descriptor.byte_offset;
    return Error::ok;
}

struct PointerArrayDescriptor {
    uintptr_t data;
    uint64_t count, capacity;
    size_t stride;
    uint64_t maximum_count;
};
inline Error validate_pointer_array(const PointerArrayDescriptor& descriptor) {
    if (!descriptor.stride || descriptor.count > descriptor.capacity
        || descriptor.capacity > descriptor.maximum_count) return Error::invalid;
    if (descriptor.count && !descriptor.data) return Error::missing;
    if (descriptor.count > std::numeric_limits<uintptr_t>::max() / descriptor.stride) return Error::overflow;
    const auto bytes = descriptor.count * descriptor.stride;
    if (bytes && descriptor.data > std::numeric_limits<uintptr_t>::max() - bytes) return Error::overflow;
    return Error::ok;
}
template<class ReadElement>
Error pointer_element(const PointerArrayDescriptor& descriptor, uint64_t index,
                      ReadElement read_element, uintptr_t* output) {
    if (!output) return Error::invalid;
    *output = 0;
    const auto valid = validate_pointer_array(descriptor);
    if (valid != Error::ok) return valid;
    if (index >= descriptor.count) return Error::missing;
    return read_element(descriptor.data + index * descriptor.stride, *output) ? Error::ok : Error::read_failed;
}

struct ByteCopy { size_t copied, required; bool truncated; };
inline ByteCopy copy_bytes(const void* source, size_t bytes, void* destination, size_t capacity) {
    const auto copied = std::min(bytes, capacity);
    if (copied) std::memcpy(destination, source, copied);
    return {copied, bytes, copied != bytes};
}

// Append exact observed bytes to adapter-owned storage while the original
// value is alive. Growth never re-executes a producer/getter.
template<class Buffer>
void append_bytes(const void* source, size_t bytes, Buffer& destination) {
    const auto* first = static_cast<const unsigned char*>(source);
    if (bytes) destination.insert(destination.end(), first, first + bytes);
}

// Borrowed compiled templates must survive every row. Adapters inject genuine
// typed copy/move operations, never a byte-copy substitute for owned values.
template<class Copy, class Move>
void materialize_constant(bool preserve_template, Copy copy, Move move) {
    if (preserve_template) copy(); else move();
}

struct CountObservation { bool source_produced = false, count_valid = false; uint64_t count = 0; };
template<class Produce, class ReadCount>
bool observe_source_count(Produce produce, ReadCount read_count, CountObservation& observation) {
    observation = {};
    if (!produce()) return false;
    observation.source_produced = true;
    uint64_t count = 0;
    if (!read_count(count)) return false;
    observation.count = count; observation.count_valid = true;
    return true;
}

template<class Original, class Type, class Binding, class Initialize, class Construct, class Validate>
bool bind_scoped(Original original, Type type, Binding& binding,
                 Initialize initialize, Construct construct, Validate validate) {
    initialize(binding, type);
    construct(original, type, binding);
    return validate(binding, type);
}

// One original source/page, one row binding at a time, many copied fields.
// Adapter compilation runs before borrowing. Row failures become cell errors;
// a failed cell does not discard siblings. No retries or engine layouts here.
template<class Adapter, class RowDone>
typename Adapter::Status run_compiled_page(Adapter& adapter, RowDone row_done, size_t row_limit) {
    typename Adapter::Source source;
    const auto status = adapter.open(source);
    if (!adapter.ok(status)) return status;
    const auto rows = std::min<size_t>(adapter.row_count(), row_limit);
    for (size_t row = 0; row < rows; ++row) {
        typename Adapter::Row binding;
        const auto selected = adapter.select(source, row, binding);
        for (size_t field = 0; field < adapter.field_count(); ++field)
            adapter.copy_cell(source, binding, row, field, selected);
        row_done(selected);
    }
    return adapter.success_status();
}
template<class Adapter>
typename Adapter::Status run_compiled_page(Adapter& adapter) {
    return run_compiled_page(adapter, [](typename Adapter::Status) {}, std::numeric_limits<size_t>::max());
}
struct SourceCursor { size_t source, row; };
// One source's borrowed frame ends before the next begins. A source failure is
// an observation, not a reason to discard later independent sources. Exceptions
// leave the cursor at the unfinished row/source; adapters keep copied prefixes.
template<class Adapter>
typename Adapter::Status run_compiled_groups(Adapter& adapter, SourceCursor& cursor,
                                            size_t sources, size_t budget) {
    while (cursor.source < sources && budget) {
        adapter.begin_source(cursor, budget);
        const auto status = run_compiled_page(adapter, [&](typename Adapter::Status selected) {
            ++cursor.row; --budget;
            adapter.observe_row(selected);
        }, budget);
        adapter.end_source(status, cursor);
        if (!adapter.ok(status) || adapter.source_finished()) {
            ++cursor.source; cursor.row = 0;
        }
    }
    return adapter.success_status();
}
template<class Adapter>
typename Adapter::Status run_page(Adapter& adapter) {
    adapter.compile_all();
    return run_compiled_page(adapter);
}

// Adapter-driven fresh chain traversal. The adapter owns native layouts and
// status codes. Source and Binding are non-copyable RAII owners. Lexical order
// is intentional: each binding dies before its source, deepest frame first.
// compile_all runs once, before any original source or element is borrowed.

namespace detail {
template<class Adapter>
typename Adapter::Status scoped_chain_frame(Adapter& adapter, size_t depth,
                                           size_t level, typename Adapter::Context context) {
    if (level == depth) return adapter.finish(context);
    typename Adapter::Source source;
    auto status = adapter.produce(level, context, source);
    if (!adapter.ok(status)) return status;
    status = adapter.count_and_check(level, source);
    if (!adapter.ok(status)) return status;
    typename Adapter::Binding binding;
    status = adapter.select(level, source, binding);
    if (!adapter.ok(status)) return status;
    return scoped_chain_frame(adapter, depth, level + 1, adapter.context(source, binding));
}
}
template<class Adapter>
typename Adapter::Status run_scoped_chain(Adapter& adapter, size_t depth) {
    const auto status = adapter.compile_all(depth);
    if (!adapter.ok(status)) return status;
    return detail::scoped_chain_frame(adapter, depth, 0, adapter.root_context());
}
}
