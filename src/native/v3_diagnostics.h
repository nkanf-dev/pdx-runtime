#pragma once
#include <Windows.h>
#include <stdint.h>
#ifdef PDX_V3_DIAGNOSTICS_BUILD
#define PDX_V3_DIAGNOSTICS_API extern "C" __declspec(dllexport)
#else
#define PDX_V3_DIAGNOSTICS_API extern "C" __declspec(dllimport)
#endif
// These flags describe the observed native stream, not a Script parser result.
enum PdxV3DiagnosticFlags : uint32_t {
    PDX_V3_DIAGNOSTIC_GAP = 1,
    PDX_V3_DIAGNOSTIC_RESET = 2,
    PDX_V3_DIAGNOSTIC_PRUNED = 4,
    PDX_V3_DIAGNOSTIC_OUTPUT_TRUNCATED = 8
};
struct PdxV3DiagnosticStatus {
    uint32_t size, error, flags, store_count;
    int32_t first_sequence, last_sequence;
    uint32_t native_limit, native_prune;
    uint64_t viewer;
};
struct PdxV3DiagnosticSnapshot {
    uint32_t size, error, flags, pinned_count, eligible_count, store_count;
    int32_t first_sequence, last_sequence;
    uint32_t native_limit, native_prune;
    uint64_t viewer;
};
// This header is followed by output_capacity bytes of complete records.
// operation1 status; operation2 records after the supplied signed native ID.
struct PdxV3DiagnosticsRequest {
    uint32_t size, error, operation, flags;
    int32_t after_sequence, first_sequence, last_sequence, next_sequence;
    uint32_t max_records, output_capacity, output_size, output_required;
    uint32_t returned_count, eligible_count, store_count, has_more;
    uint32_t logging_ready, viewer_available, subscription_coverage_known, execution_thread_id;
    uint64_t expected_viewer, viewer;
    uint32_t native_limit, native_prune;
};
// Followed by label_bytes, source_bytes, message_bytes UTF-8 bytes (no terminators).
struct PdxV3DiagnosticRecord {
    uint32_t size;
    int32_t native_sequence;
    uint32_t category, source_line, group_raw, level_raw;
    uint64_t native_time_raw;
    uint32_t label_bytes, source_bytes, message_bytes, reserved;
};
// Passive lookup does not create an empty store or alter logger subscriptions.
PDX_V3_DIAGNOSTICS_API uint64_t WINAPI PdxV3DiagnosticsViewer();
// Explicit native lazy getter. Creation alone does not attach any logger sink.
PDX_V3_DIAGNOSTICS_API uint64_t WINAPI PdxV3DiagnosticsGetViewer();
PDX_V3_DIAGNOSTICS_API DWORD WINAPI PdxV3DiagnosticsReadStatus(uint64_t viewer, PdxV3DiagnosticStatus*);
// Preallocate entry_addresses before this call. Retains each selected native
// entry under the native reader lock. Caller releases each returned owner.
PDX_V3_DIAGNOSTICS_API DWORD WINAPI PdxV3DiagnosticsPinRecords(uint64_t viewer,
    int32_t after_sequence, uint32_t max_records, uint64_t* entry_addresses,
    uint32_t entry_capacity, PdxV3DiagnosticSnapshot*);
PDX_V3_DIAGNOSTICS_API DWORD WINAPI PdxV3DiagnosticsRetainEntry(uint64_t entry);
PDX_V3_DIAGNOSTICS_API void WINAPI PdxV3DiagnosticsReleaseEntry(uint64_t entry);
// Requires a retained entry; copy outside the native store lock. A short buffer
// returns ERROR_MORE_DATA without writing a partial record. No native strings
// are constructed and no native logger is called by this operation.
PDX_V3_DIAGNOSTICS_API DWORD WINAPI PdxV3DiagnosticsCopyRecord(uint64_t entry,
    void* output, uint32_t output_capacity, uint32_t* output_required);
PDX_V3_DIAGNOSTICS_API void WINAPI PdxV3DiagnosticsInterface(void* application, void* opaque);
