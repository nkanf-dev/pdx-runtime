#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <Windows.h>
#include <stdint.h>

#if defined(PDX_RUNTIME_BUILD) || defined(PDX_V3_UI_BUILD)
#define PDX_V3_UI_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_UI_API extern "C" __declspec(dllimport)
#endif

// One current, bounded original map-notification source snapshot. Header size
// covers the entire caller-owned allocation. All offsets are allocation-relative.
// No native address, owner, item identity or historical cursor is returned.
struct PdxV3UiMapRequest {
    uint32_t size, error, action, interface_thread_id;
    uint32_t max_items, records_offset, text_offset, text_capacity;
    uint32_t source_count, returned_count, text_bytes_copied, text_bytes_required;
    uint32_t truncated, source_produced, collection_valid, execution_thread_id;
    uint32_t start_index, reserved;
    uint64_t source_world;
};
struct PdxV3UiMapRecord {
    uint32_t source_index, error, text_offset, text_bytes_copied, text_bytes_required, complete;
};
constexpr uint32_t PDX_V3_UI_MAP_SNAPSHOT = 1;
// Pure request validation is also usable by an owned host. Never touches a game.
PDX_V3_UI_API DWORD WINAPI PdxV3UiMapValidate(PdxV3UiMapRequest* request);
// Original Interface queue callback. Owns one original Access scope and every
// borrowed source/binding/VM temporary until all bounded text copies complete.
PDX_V3_UI_API void WINAPI PdxV3UiMapNotificationsInterface(void* application, void* opaque);
