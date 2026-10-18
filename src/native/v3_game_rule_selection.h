#pragma once
#include "v3_data.h"
#include "dependent_sequence_core.h"
#include <algorithm>
#include <cstdint>

// V3 adapter policy only: positions belong to the current original canonical
// definition array. They are never stable identities or shared-core facts.
namespace pdx::v3_game_rule_selection {
struct Plan { uint32_t outcome = PDX_V3_DATA_RULE_UNOBSERVED, direction = 0, steps = 0; };
inline Plan plan(uint32_t count, uint32_t current, uint32_t target) {
    if (!count) return {PDX_V3_DATA_RULE_EMPTY, 0, 0};
    if (target >= count) return {PDX_V3_DATA_RULE_TARGET_ABSENT, 0, 0};
    if (current >= count) return {PDX_V3_DATA_RULE_CURRENT_NOT_MEMBER, 0, 0};
    if (current == target) return {PDX_V3_DATA_RULE_ALREADY_SELECTED, 0, 0};
    const uint64_t n = count;
    const auto next = static_cast<uint32_t>((uint64_t(target) + n - current) % n);
    const auto prev = static_cast<uint32_t>((uint64_t(current) + n - target) % n);
    return {PDX_V3_DATA_RULE_SELECTED, next <= prev ? 1u : 2u, std::min(next, prev)};
}
template<class Adapter>
void execute(const Plan& selected, uint32_t capacity, Adapter& adapter) {
    // The shared loop knows only ordered invoke/completed/continue/stop. Every
    // V3 action receipt and predicate is supplied by the V3 adapter.
    dependent_sequence::run(std::min(selected.steps, capacity), adapter);
}
} // namespace pdx::v3_game_rule_selection
