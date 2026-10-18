#include "v3_ui_dialogs.h"
#include "v3_ui_dialogs_profile.h"
#include "v3_access.h"
#include "pdx_runtime_bridge.h"
#include "v3_data.h"
#include "original_source_core.h"
#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <vector>

namespace {
namespace profile = pdx_v3_ui_dialogs_profile;
namespace source = pdx::original_source_core;

// V3 supplies only typed layout reads. Checked local memory access belongs to
// the existing shared runtime carrier; no second Win32 walker lives here.
bool read_bytes(uintptr_t address, void* output, size_t bytes) {
    if (!address || !bytes || bytes > UINTPTR_MAX - address || bytes > PDX_RUNTIME_MAX_RAW) return false;
    uint32_t received = 0;
    return PdxRuntimeRead(static_cast<uint64_t>(address), output, static_cast<uint32_t>(bytes), &received) == ERROR_SUCCESS
        && received == bytes;
}
template<class T> bool load(uintptr_t address, T& value) {
    return read_bytes(address, &value, sizeof(value));
}
template<class T> bool member(uintptr_t object, size_t offset, T& value) {
    uintptr_t address = 0; return source::add(object, offset, address) && load(address, value);
}
struct Guard {
    PdxV3AccessStorage storage{};
    Guard() { PdxV3AccessEnter(&storage, 1, 0, "pdx-runtime/ui-dialog-original-sources"); }
    ~Guard() { PdxV3AccessLeave(&storage); }
    Guard(const Guard&) = delete; Guard& operator=(const Guard&) = delete;
};
struct Property {
    PdxV3DataProperty value{}; bool initialized = false;
    ~Property() { if (initialized) PdxV3DataDestroyProperty(&value); }
    Property() = default; Property(const Property&) = delete; Property& operator=(const Property&) = delete;
};
struct Properties {
    std::array<Property, PDX_V3_UI_DIALOG_MAX_FIELDS> fields{};
    int32_t type = -1;
};
struct Root {
    uintptr_t object = 0; uint64_t handle = 0;
    std::vector<unsigned char> request;
    ~Root() { if (handle) PdxV3DataRelease(&handle); }
    Root() = default; Root(const Root&) = delete; Root& operator=(const Root&) = delete;
};
struct Binding {
    PdxV3DataBinding value{}; PdxV3DataScope scope{};
    Binding() { value.bytes[48] = 0xff; }
    ~Binding() { PdxV3DataDestroyBinding(&value); }
    Binding(const Binding&) = delete; Binding& operator=(const Binding&) = delete;
};
struct Any {
    PdxV3DataAny value{};
    ~Any() {
        uintptr_t type = 0; std::memcpy(&type, value.bytes, sizeof(type));
        if (type) PdxV3DataDestroyAny(&value);
    }
    Any() = default; Any(const Any&) = delete; Any& operator=(const Any&) = delete;
};

struct Adapter {
    using Status = DWORD;
    using Properties = ::Properties; using Root = ::Root; using Binding = ::Binding;
    uintptr_t image, application; PdxV3UiDialogsRequest* r;
    const profile::SourceProfile& config;
    PdxV3UiDialogRecord* current = nullptr;
    bool partial_error = false;
    bool ok(Status status) const { return status == ERROR_SUCCESS; }
    const source::Descriptor& descriptor() const { return config.descriptor; }
    uint64_t start_index() const { return r->start_index; }
    uint64_t item_limit() const { return r->max_items; }
    bool read_pointer(uintptr_t address, uintptr_t& pointer) { return load(address, pointer); }
    bool read_array(uintptr_t address, pdx::data_projection_core::PointerArrayDescriptor& array) {
        PdxV3DataArray native{};
        if (!load(address, native)) return false;
        array = {native.data, native.count, native.capacity, sizeof(uintptr_t), INT32_MAX};
        return true;
    }
    bool matches_tags(uintptr_t object, source::Tags tags) {
        uintptr_t actual = 0;
        if (!load(object, actual)) return false;
        for (size_t index = 0; index < tags.count; ++index)
            if (tags.values[index] <= profile::image_size && actual == image + tags.values[index]) return true;
        return false;
    }
    DWORD source_error(source::Error error) const {
        switch (error) {
        case source::Error::ok: return ERROR_SUCCESS;
        case source::Error::missing: return ERROR_NOT_READY;
        case source::Error::wrong_type: return ERROR_INVALID_DATA;
        case source::Error::overflow: return ERROR_ARITHMETIC_OVERFLOW;
        case source::Error::read_failed: return ERROR_PARTIAL_COPY;
        default: return ERROR_INVALID_DATA;
        }
    }
    DWORD compile_all(Properties& properties) {
        if (!PdxV3DataFactory()) return ERROR_NOT_READY;
        properties.type = PdxV3DataFindType(config.type_name);
        if (properties.type < 0) return ERROR_NOT_READY;
        for (uint32_t index = 0; index < config.getter_count; ++index) {
            auto& property = properties.fields[index];
            property.initialized = true; // Compile constructs original storage, even on compile failure.
            const auto* expression = config.getters[index].expression;
            const auto status = PdxV3DataCompile(expression, static_cast<uint32_t>(std::strlen(expression)), &property.value);
            // These two public Compile failures precede its native constructor.
            // A normal compile failure still owns constructed property storage.
            if (status == ERROR_NOT_READY || status == ERROR_INVALID_PARAMETER) property.initialized = false;
            if (status) return status;
            uint32_t mode = 0, parameters_a = 0, parameters_b = 0;
            std::memcpy(&mode, property.value.bytes + profile::property_mode_offset, sizeof(mode));
            std::memcpy(&parameters_a, property.value.bytes + profile::property_parameters_a, sizeof(parameters_a));
            std::memcpy(&parameters_b, property.value.bytes + profile::property_parameters_b, sizeof(parameters_b));
            // These are the exact hard-coded original zero-argument read-only
            // getters. Do not redirect an action category or evaluate parameters
            // that might refresh/delete an original borrowed source.
            if ((mode != 1 && mode != 2) || parameters_a || parameters_b) return ERROR_NOT_SUPPORTED;
        }
        return ERROR_SUCCESS;
    }
    DWORD produce_root(Root& root) {
        if (config.root_kind == profile::RootKind::original_application) {
            root.object = application; return ERROR_SUCCESS;
        }
        const auto length = std::strlen(config.root_expression);
        const auto end = (sizeof(PdxV3DataRequest) + length + 7) & ~size_t(7);
        root.request.resize(end);
        auto* original = reinterpret_cast<PdxV3DataRequest*>(root.request.data());
        *original = {}; original->size = static_cast<uint32_t>(end);
        original->action = PDX_V3_DATA_EVALUATE; original->type_id = -1;
        original->text_offset = sizeof(*original); original->text_bytes = static_cast<uint32_t>(length);
        original->bindings_offset = original->output_offset = original->size;
        std::memcpy(root.request.data() + original->text_offset, config.root_expression, length);
        const auto status = PdxV3DataEvaluate(original);
        root.handle = original->value_handle; // Held/released only in this callback, including failures.
        if (status) return status;
        if (!root.handle || original->kind != PDX_V3_DATA_RAW
            || original->payload_size != sizeof(uintptr_t) || !original->payload_address
            || std::strcmp(original->native_type_name, profile::popup_root_any_type)) return ERROR_NOT_SUPPORTED;
        if (!load(original->payload_address, root.object)) return ERROR_PARTIAL_COPY;
        return root.object ? ERROR_SUCCESS : ERROR_NOT_READY;
    }
    uintptr_t root_object(const Root& root) const { return root.object; }
    void observe_source(const source::View& view) {
        r->source_produced = view.source_produced; r->count_valid = view.count_valid;
        r->source_count = view.count_valid ? static_cast<uint32_t>(view.count) : 0;
    }
    DWORD stale_ordinal() { r->truncated = 1; return ERROR_RETRY; }
    void observe_window(uint64_t start, uint64_t count, uint64_t total) {
        r->truncated = start != 0 || count < total;
    }
    void begin_record(uint64_t ordinal) {
        auto* records = reinterpret_cast<PdxV3UiDialogRecord*>(reinterpret_cast<unsigned char*>(r) + r->records_offset);
        current = records + r->returned_count++; *current = {};
        current->source_index = static_cast<uint32_t>(ordinal);
        current->fields_offset = r->fields_offset + r->field_count * sizeof(PdxV3UiDialogField);
    }
    void record_error(DWORD error) {
        if (!current->error) current->error = error;
        if (error == ERROR_MORE_DATA) r->truncated = 1;
        else if (error) partial_error = true;
    }
    DWORD bind(uintptr_t original, Properties& properties, Binding& binding) {
        auto status = PdxV3DataBindScopedObject(original, properties.type, &binding.value);
        if (!status) status = PdxV3DataBindingScope(&binding.value, &binding.scope);
        if (!status && binding.scope.type_id != properties.type) status = ERROR_INVALID_DATA;
        return status;
    }
    DWORD metadata(uintptr_t original) {
        if (config.consumed_offset != profile::no_field) {
            uint8_t consumed = 0;
            if (!member(original, config.consumed_offset, consumed)) return ERROR_PARTIAL_COPY;
            if (consumed > 1) return ERROR_INVALID_DATA;
            current->consumed = consumed; current->metadata_valid |= PDX_V3_UI_DIALOG_CONSUMED_VALID;
        }
        auto dialog = original;
        if (config.dialog_owner) {
            source::View owned;
            const auto status = source::resolve(original, *config.dialog_owner, *this, owned);
            if (status != source::Error::ok) return source_error(status);
            dialog = owned.singleton;
        }
        current->has_dialog = dialog != 0; current->metadata_valid |= PDX_V3_UI_DIALOG_OWNER_VALID;
        uintptr_t widget = 0;
        if (dialog && !member(dialog, config.widget_offset, widget)) return ERROR_PARTIAL_COPY;
        current->has_widget = widget != 0; current->metadata_valid |= PDX_V3_UI_DIALOG_WIDGET_VALID;
        return ERROR_SUCCESS;
    }
    DWORD consume(uintptr_t original, Binding& binding, Properties& properties) {
        const auto metadata_status = metadata(original);
        if (metadata_status) return metadata_status;
        auto* fields = reinterpret_cast<PdxV3UiDialogField*>(reinterpret_cast<unsigned char*>(r) + r->fields_offset);
        for (uint32_t index = 0; index < config.getter_count; ++index) {
            if (r->field_count >= r->fields_capacity) return ERROR_INSUFFICIENT_BUFFER;
            auto& field = fields[r->field_count++]; field = {}; ++current->field_count;
            field.field = config.getters[index].field;
            field.text_offset = r->text_offset + r->text_bytes_copied;
            PdxV3DataArray scopes{reinterpret_cast<uintptr_t>(&binding.scope), 1, 1, 0}, arguments{};
            PdxV3DataScopeValue context{}; int32_t context_type = -1;
            field.error = PdxV3DataPrepare(&properties.fields[index].value, &scopes, &arguments, &context, &context_type);
            Any value;
            if (!field.error) field.error = PdxV3DataEvaluatePrepared(&properties.fields[index].value, &context, &arguments, &value.value);
            if (!field.error) {
                const auto capacity = r->text_capacity - r->text_bytes_copied;
                // BOOL data is copied into this private scratch, not the text
                // arena. A zero-capacity string still reports its required size.
                std::vector<unsigned char> scratch(sizeof(PdxV3DataRequest) + std::max<uint32_t>(capacity, 8));
                auto* copied = reinterpret_cast<PdxV3DataRequest*>(scratch.data());
                *copied = {}; copied->size = static_cast<uint32_t>(scratch.size());
                copied->output_offset = sizeof(*copied);
                copied->output_bytes = config.getters[index].expected_kind == PDX_V3_DATA_BOOL ? 8 : capacity;
                field.error = PdxV3DataSnapshotCopied(copied, &value.value);
                field.kind = copied->kind;
                if (field.kind != config.getters[index].expected_kind) field.error = ERROR_INVALID_DATA;
                else if (field.kind == PDX_V3_DATA_BOOL) {
                    if (!field.error && copied->raw_bits <= 1) { field.raw_bits = copied->raw_bits; field.complete = 1; }
                    else if (!field.error) field.error = ERROR_INVALID_DATA;
                } else if (!field.error || field.error == ERROR_MORE_DATA) {
                    if (copied->snapshot_bytes > capacity || copied->snapshot_bytes > copied->total) {
                        field.error = ERROR_INVALID_DATA; record_error(field.error); continue;
                    }
                    if (r->text_bytes_required > UINT32_MAX - copied->total) {
                        field.error = ERROR_ARITHMETIC_OVERFLOW; record_error(field.error); continue;
                    }
                    field.text_bytes_copied = copied->snapshot_bytes; field.text_bytes_required = copied->total;
                    if (field.text_bytes_copied) std::memcpy(reinterpret_cast<unsigned char*>(r) + field.text_offset,
                        scratch.data() + copied->output_offset, field.text_bytes_copied);
                    r->text_bytes_copied += field.text_bytes_copied;
                    r->text_bytes_required += field.text_bytes_required;
                    field.complete = !field.error && field.text_bytes_copied == field.text_bytes_required;
                    if (!field.complete && !field.error) field.error = ERROR_MORE_DATA;
                }
            }
            if (field.error) record_error(field.error);
        }
        return ERROR_SUCCESS;
    }
    DWORD finish() const {
        return partial_error ? ERROR_PARTIAL_COPY : r->truncated ? ERROR_MORE_DATA : ERROR_SUCCESS;
    }
};

DWORD snapshot(void* application, PdxV3UiDialogsRequest* r) {
    const auto validated = PdxV3UiDialogsValidate(r);
    if (validated) return validated;
    const auto image = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    if (!profile::matches(image, read_bytes)) return r->error = ERROR_NOT_SUPPORTED;
    r->execution_thread_id = GetCurrentThreadId();
    if (r->execution_thread_id != r->interface_thread_id) return r->error = ERROR_INVALID_THREAD_ID;
    const auto app = reinterpret_cast<uintptr_t>(application);
    uintptr_t original_app = 0, vtable = 0; uint8_t inactive = 1; int32_t mode = -1;
    if (!load(image + profile::application_global, original_app) || !app || app != original_app
        || !load(app, vtable) || vtable != image + profile::application_vtable
        || !member(app, profile::inactive_offset, inactive) || inactive
        || !member(app, profile::mode_offset, mode) || mode != profile::ingame_mode) return r->error = ERROR_NOT_READY;
    Guard gate;
    uintptr_t world = 0, interface_root = 0, interface_app = 0;
    if (!load(image + profile::application_global, original_app) || original_app != app
        || !load(app, vtable) || vtable != image + profile::application_vtable
        || !member(app, profile::inactive_offset, inactive) || inactive
        || !member(app, profile::mode_offset, mode) || mode != profile::ingame_mode
        || !member(app, profile::world_offset, world) || world != r->source_world
        || !load(image + profile::interface_root_global, interface_root) || !interface_root
        || !member(interface_root, profile::interface_root_application_offset, interface_app)
        || interface_app != app) return r->error = ERROR_NOT_READY;
    Adapter adapter{image, app, r, *profile::find(r->source)};
    return r->error = source::run(adapter);
}
}

DWORD WINAPI PdxV3UiDialogsValidate(PdxV3UiDialogsRequest* r) {
    if (!r || r->size < sizeof(*r)) return ERROR_INVALID_PARAMETER;
    r->source_count = r->returned_count = r->field_count = r->text_bytes_copied = r->text_bytes_required = 0;
    r->truncated = r->source_produced = r->count_valid = r->execution_thread_id = 0;
    const uint64_t fields = uint64_t(sizeof(*r)) + uint64_t(r->max_items) * sizeof(PdxV3UiDialogRecord);
    const uint64_t text = fields + uint64_t(r->max_items) * PDX_V3_UI_DIALOG_MAX_FIELDS * sizeof(PdxV3UiDialogField);
    if (r->action != PDX_V3_UI_DIALOGS_SNAPSHOT || !profile::find(r->source) || r->reserved
        || !r->interface_thread_id || !r->source_world || !r->max_items || r->max_items > PDX_V3_UI_DIALOG_MAX_ITEMS
        || r->start_index > INT32_MAX || r->text_capacity > PDX_V3_UI_DIALOG_MAX_TEXT
        || r->records_offset != sizeof(*r) || r->fields_offset != fields
        || r->fields_capacity != uint64_t(r->max_items) * PDX_V3_UI_DIALOG_MAX_FIELDS
        || r->text_offset != text || uint64_t(r->size) != text + r->text_capacity) return r->error = ERROR_INVALID_PARAMETER;
    return r->error = ERROR_SUCCESS;
}
void WINAPI PdxV3UiDialogsInterface(void* application, void* opaque) {
    auto* request = static_cast<PdxV3UiDialogsRequest*>(opaque);
    try { snapshot(application, request); }
    catch (...) { if (request && request->size >= sizeof(*request)) request->error = ERROR_UNHANDLED_EXCEPTION; }
}
static_assert(sizeof(PdxV3UiDialogsRequest) == 96);
static_assert(sizeof(PdxV3UiDialogRecord) == 32);
static_assert(sizeof(PdxV3UiDialogField) == 40);
