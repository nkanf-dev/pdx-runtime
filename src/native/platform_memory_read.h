#pragma once
#include <Windows.h>
#include <cstdint>
#include <limits>

// Shared Win32 memory-read contract. No game/layout/source policy. The native
// operation is injected so production and owned tests use identical preflight.
namespace pdx::platform_memory {
struct ReadResult { bool success; SIZE_T received; DWORD error; };

template<class NativeRead>
DWORD checked_read(uint64_t address, void* output, uint32_t size, uint32_t* received,
                   uint32_t maximum_bytes, NativeRead native_read) {
    if (!received) return ERROR_INVALID_PARAMETER;
    *received = 0; // Every preflight failure has an explicit empty byte receipt.
    if (!output || !size || size > maximum_bytes) return ERROR_INVALID_PARAMETER;
    constexpr auto maximum_address = (std::numeric_limits<uintptr_t>::max)();
    // The complete half-open span [address,address+size) must be representable
    // before any OS read. Never delegate wrapped spans to OS-specific behavior.
    if (address > maximum_address || uint64_t(size) > maximum_address - address)
        return ERROR_ARITHMETIC_OVERFLOW;
    const auto result = native_read(static_cast<uintptr_t>(address), output, size);
    if (result.received > size) return ERROR_INVALID_DATA;
    *received = static_cast<uint32_t>(result.received);
    if (result.success) return result.received == size ? ERROR_SUCCESS : ERROR_PARTIAL_COPY;
    // A failed native operation cannot become success when no OS error is set.
    return result.error ? result.error : ERROR_PARTIAL_COPY;
}
}
