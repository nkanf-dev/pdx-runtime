#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <Windows.h>
#include <stdint.h>

#if defined(PDX_RUNTIME_BUILD) || defined(PDX_V3_UI_DIALOGS_BUILD)
#define PDX_V3_UI_DIALOGS_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_UI_DIALOGS_API extern "C" __declspec(dllimport)
#endif

// One bounded current source. All offsets are allocation-relative. Source
// ordinals are snapshot positions, never stable IDs or native history cursors.
// No pointer, native result handle, callback, or action input appears on this wire.
struct PdxV3UiDialogsRequest {
    uint32_t size, error, action, interface_thread_id;
    uint32_t source, max_items, start_index, records_offset;
    uint32_t fields_offset, fields_capacity, text_offset, text_capacity;
    uint32_t source_count, returned_count, field_count, text_bytes_copied;
    uint32_t text_bytes_required, truncated, source_produced, count_valid;
    uint32_t execution_thread_id, reserved;
    uint64_t source_world;
};
struct PdxV3UiDialogRecord {
    uint32_t source_index, error, fields_offset, field_count;
    uint32_t metadata_valid, consumed, has_dialog, has_widget;
};
struct PdxV3UiDialogField {
    uint32_t field, error, kind, text_offset;
    uint32_t text_bytes_copied, text_bytes_required, complete, reserved;
    uint64_t raw_bits;
};
enum PdxV3UiDialogSource : uint32_t {
    PDX_V3_UI_JOMINI_NOTIFICATIONS = 1,
    PDX_V3_UI_GENERAL_INFORMATION = 2,
    PDX_V3_UI_CONFIRMATIONS = 3,
};
enum PdxV3UiDialogFieldId : uint32_t {
    PDX_V3_UI_DIALOG_TITLE = 1, PDX_V3_UI_DIALOG_TEXT = 2,
    PDX_V3_UI_DIALOG_ACCEPT_LABEL = 3, PDX_V3_UI_DIALOG_DECLINE_LABEL = 4,
    PDX_V3_UI_DIALOG_HAS_ACCEPT = 5, PDX_V3_UI_DIALOG_HAS_DECLINE = 6,
    PDX_V3_UI_DIALOG_DESCRIPTION = 7,
};
constexpr uint32_t PDX_V3_UI_DIALOGS_SNAPSHOT = 1;
constexpr uint32_t PDX_V3_UI_DIALOG_CONSUMED_VALID = 1;
constexpr uint32_t PDX_V3_UI_DIALOG_OWNER_VALID = 2;
constexpr uint32_t PDX_V3_UI_DIALOG_WIDGET_VALID = 4;
constexpr uint32_t PDX_V3_UI_DIALOG_MAX_FIELDS = 6;
constexpr uint32_t PDX_V3_UI_DIALOG_MAX_ITEMS = 256;
constexpr uint32_t PDX_V3_UI_DIALOG_MAX_TEXT = 1048576;
// Pure owned-host validation. Does not read game memory or call an original API.
PDX_V3_UI_DIALOGS_API DWORD WINAPI PdxV3UiDialogsValidate(PdxV3UiDialogsRequest* request);
// Enqueue only on the original Interface queue. It enters the original Access
// scope and destroys all roots, properties, bindings and Anys before scope exit.
PDX_V3_UI_DIALOGS_API void WINAPI PdxV3UiDialogsInterface(void* application, void* opaque);
