#include "v3_ui_map.h"
#include "v3_ui_map_profile.h"
#include "v3_data.h"
#include "v3_access.h"
#include "pdx_runtime_bridge.h"
#include "data_projection_core.h"
#include <algorithm>
#include <cstring>
#include <vector>
#include <limits>

namespace {
namespace profile = pdx_v3_ui_map_profile;
struct MemoryReadFailure { DWORD error; };
template<class T> T read(uintptr_t at, uintptr_t offset = 0) {
    static_assert(sizeof(T) <= PDX_RUNTIME_MAX_RAW, "typed source read exceeds shared primitive bound");
    if (!at) throw MemoryReadFailure{ERROR_INVALID_ADDRESS};
    if (offset > UINTPTR_MAX - at || sizeof(T) > UINTPTR_MAX - at - offset)
        throw MemoryReadFailure{ERROR_ARITHMETIC_OVERFLOW};
    T value{}; uint32_t received = 0;
    const auto status = PdxRuntimeRead(static_cast<uint64_t>(at + offset), &value, static_cast<uint32_t>(sizeof(value)), &received);
    if (status || received != sizeof(value))
        throw MemoryReadFailure{status ? status : ERROR_PARTIAL_COPY};
    return value;
}
bool range(const PdxV3UiMapRequest* r, uint64_t offset, uint64_t bytes) {
    return offset <= r->size && bytes <= r->size - offset;
}
struct Guard {
    PdxV3AccessStorage storage{};
    Guard() { PdxV3AccessEnter(&storage, 1, 0, "pdx-runtime/ui-map-source"); }
    ~Guard() { PdxV3AccessLeave(&storage); }
};
struct Property {
    PdxV3DataProperty value{};
    bool initialized = false;
    ~Property() { if (initialized) PdxV3DataDestroyProperty(&value); }
};
struct Binding {
    PdxV3DataBinding value{};
    Binding() { value.bytes[48] = 0xff; }
    ~Binding() { PdxV3DataDestroyBinding(&value); }
};
struct Any {
    PdxV3DataAny value{};
    ~Any() {
        uintptr_t type = 0; std::memcpy(&type, value.bytes, sizeof(type));
        if (type) PdxV3DataDestroyAny(&value);
    }
};
DWORD snapshot(void* application, PdxV3UiMapRequest* r) {
    const auto validation = PdxV3UiMapValidate(r);
    if (validation) return validation;
    const auto image = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    if (!profile::matches(image, [](uintptr_t at, void* output, size_t bytes) {
            uint32_t received = 0;
            return bytes <= PDX_RUNTIME_MAX_RAW
                && PdxRuntimeRead(static_cast<uint64_t>(at), output, static_cast<uint32_t>(bytes), &received) == ERROR_SUCCESS
                && received == bytes;
        })) return r->error = ERROR_NOT_SUPPORTED;
    r->execution_thread_id = GetCurrentThreadId();
    if (r->execution_thread_id != r->interface_thread_id) return r->error = ERROR_INVALID_THREAD_ID;
    const auto app = reinterpret_cast<uintptr_t>(application);
    if (!app || app != read<uintptr_t>(image, profile::application_global)
        || read<uint8_t>(app, profile::application_inactive_offset)) return r->error = ERROR_NOT_READY;
    Guard gate;
    if (read<uintptr_t>(app, profile::world_offset) != r->source_world) return r->error = ERROR_NOT_READY;
    const auto type = PdxV3DataFindType("MapNotification");
    if (type < 0) return r->error = ERROR_NOT_READY;
    // Compile BEFORE obtaining the transient manager/item source. GetText is
    // the original zero-argument VM getter, not a guessed callback ABI.
    Property property;
    property.initialized = true;
    static constexpr char expression[] = "MapNotification.GetText";
    const auto compile_error = PdxV3DataCompile(expression, sizeof(expression) - 1, &property.value);
    if (compile_error == ERROR_NOT_READY || compile_error == ERROR_INVALID_PARAMETER) property.initialized = false;
    if (compile_error) return r->error = compile_error;
    const auto owner = read<uintptr_t>(image, profile::root_global);
    const auto root_app = owner ? read<uintptr_t>(owner, profile::root_application_offset) : 0;
    if (!root_app || read<int32_t>(root_app, profile::mode_offset) != profile::ingame_mode)
        return r->error = ERROR_NOT_READY; // Before original getter's assert path.
    using OriginalGetter = uintptr_t (__fastcall*)();
    const auto gfx = reinterpret_cast<OriginalGetter>(image + profile::original_gfx_getter)();
    if (!gfx || read<uintptr_t>(gfx) != image + profile::gfx_vtable) return r->error = ERROR_NOT_READY;
    const auto handler = read<uintptr_t>(gfx, profile::gfx_handler_offset);
    if (!handler || read<uintptr_t>(handler) != image + profile::handler_vtable) return r->error = ERROR_NOT_READY;
    const auto manager = read<uintptr_t>(handler, profile::handler_manager_offset);
    if (!manager) return r->error = ERROR_NOT_READY;
    r->source_produced = 1;
    const auto array = read<PdxV3DataArray>(manager, profile::manager_array_offset);
    const pdx::data_projection_core::PointerArrayDescriptor descriptor{
        array.data, array.count, array.capacity, sizeof(uintptr_t), INT32_MAX};
    if (pdx::data_projection_core::validate_pointer_array(descriptor) != pdx::data_projection_core::Error::ok)
        return r->error = ERROR_INVALID_DATA;
    r->collection_valid = 1; r->source_count = array.count;
    auto* rows = reinterpret_cast<PdxV3UiMapRecord*>(reinterpret_cast<unsigned char*>(r) + r->records_offset);
    if (r->start_index >= array.count) r->start_index = 0;
    const auto limit = std::min(array.count - r->start_index, r->max_items);
    r->truncated = r->start_index != 0 || limit < array.count;
    for (uint32_t index = 0; index < limit; ++index) {
        auto& row = rows[index]; row = {}; row.source_index = r->start_index + index;
        row.text_offset = r->text_offset + r->text_bytes_copied;
        uintptr_t item = 0;
        const auto selected = pdx::data_projection_core::pointer_element(descriptor, row.source_index,
            [](uintptr_t address, uintptr_t& value) { value = read<uintptr_t>(address); return true; }, &item);
        if (selected != pdx::data_projection_core::Error::ok || !item) {
            row.error = ERROR_NOT_FOUND; ++r->returned_count; continue;
        }
        // Items have NO vtable. Bind the actual original CMapNotification* with
        // its dynamically resolved original type and original raw0 constructor.
        Binding binding;
        row.error = PdxV3DataBindScopedObject(item, type, &binding.value);
        PdxV3DataScope scope{};
        if (!row.error) row.error = PdxV3DataBindingScope(&binding.value, &scope);
        if (!row.error && scope.type_id != type) row.error = ERROR_INVALID_DATA;
        PdxV3DataArray scopes{reinterpret_cast<uintptr_t>(&scope), 1, 1, 0}, arguments{};
        PdxV3DataScopeValue context{}; int32_t context_type = -1;
        if (!row.error) row.error = PdxV3DataPrepare(&property.value, &scopes, &arguments, &context, &context_type);
        Any value;
        if (!row.error) row.error = PdxV3DataEvaluatePrepared(&property.value, &context, &arguments, &value.value);
        if (!row.error) {
            const auto capacity = r->text_capacity - r->text_bytes_copied;
            std::vector<unsigned char> bytes(sizeof(PdxV3DataRequest) + capacity);
            auto* copied = reinterpret_cast<PdxV3DataRequest*>(bytes.data());
            *copied = {}; copied->size = static_cast<uint32_t>(bytes.size());
            copied->output_offset = sizeof(*copied); copied->output_bytes = capacity;
            row.error = PdxV3DataSnapshotCopied(copied, &value.value);
            if (copied->kind != PDX_V3_DATA_STRING) row.error = ERROR_INVALID_DATA;
            row.text_bytes_copied = copied->snapshot_bytes;
            row.text_bytes_required = copied->total;
            if (row.text_bytes_copied > capacity) return r->error = ERROR_INVALID_DATA;
            pdx::data_projection_core::copy_bytes(bytes.data() + copied->output_offset,
                row.text_bytes_copied, reinterpret_cast<unsigned char*>(r) + row.text_offset, capacity);
            // A zero-capacity probe is complete evaluation, not a complete text
            // copy. No native Any is retained for a later expansion/resume.
            row.complete = !row.error && row.text_bytes_copied == row.text_bytes_required;
            if (!row.complete && !row.error) row.error = ERROR_MORE_DATA;
            r->text_bytes_copied += row.text_bytes_copied;
            if (r->text_bytes_required > UINT32_MAX - row.text_bytes_required) return r->error = ERROR_ARITHMETIC_OVERFLOW;
            r->text_bytes_required += row.text_bytes_required;
            if (!row.complete) r->truncated = 1;
        }
        ++r->returned_count;
    }
    return r->error = r->truncated ? ERROR_MORE_DATA : ERROR_SUCCESS;
}
}

DWORD WINAPI PdxV3UiMapValidate(PdxV3UiMapRequest* r) {
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    r->source_count = r->returned_count = r->text_bytes_copied = r->text_bytes_required = 0;
    r->truncated = r->source_produced = r->collection_valid = r->execution_thread_id = 0;
    if (r->action != PDX_V3_UI_MAP_SNAPSHOT || !r->source_world || !r->interface_thread_id || !r->max_items || r->reserved
        || r->records_offset < sizeof(*r) || r->records_offset % alignof(uint64_t)
        || !range(r, r->records_offset, uint64_t(r->max_items) * sizeof(PdxV3UiMapRecord))
        || r->text_offset < uint64_t(r->records_offset) + uint64_t(r->max_items) * sizeof(PdxV3UiMapRecord)
        || !range(r, r->text_offset, r->text_capacity)) return r->error = ERROR_INVALID_PARAMETER;
    return r->error = ERROR_SUCCESS;
}
void WINAPI PdxV3UiMapNotificationsInterface(void* application, void* opaque) {
    auto* request = static_cast<PdxV3UiMapRequest*>(opaque);
    try { snapshot(application, request); }
    catch (const MemoryReadFailure& failure) {
        if (request && request->size >= sizeof(*request)) request->error = failure.error;
    }
    catch (...) { if (request && request->size >= sizeof(*request)) request->error = ERROR_UNHANDLED_EXCEPTION; }
}
static_assert(sizeof(PdxV3UiMapRequest) == 80);
static_assert(sizeof(PdxV3UiMapRecord) == 24);
