// SPDX-License-Identifier: MPL-2.0
#pragma once
#include <cstddef>

namespace pdx::dependent_sequence {
// Caller owns the step storage, status meanings and continuation policy.
// Progress is written after each returned invocation. Exceptions preserve the
// original outputs/progress; this traversal never retries or rolls back.
template<class Adapter>
void run(std::size_t count, Adapter& adapter) {
    for (std::size_t index = 0; index < count; ++index) {
        adapter.invoke(index);
        adapter.completed(index + 1);
        if (index + 1 < count && !adapter.continue_after(index)) {
            adapter.stop();
            return;
        }
    }
}
} // namespace pdx::dependent_sequence
