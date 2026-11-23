// Copyright (C) 2026 Sacrappy

// The VRAM allowance as the renderer's rules take it (Longshot #1091's vram_compact::past_allowance, on its own here).
#pragma once
#include <cstdint>

namespace vram_allowance {
/// An allowance the driver gives as the count itself is past: AMD's Windows driver, once a process is over what it allows, reports
/// the allowance as what the process uses (an RX 6600 at 5940 of 5940 MB). Taken as it is, every give-back rule saw the count within
/// it while the driver paged (frames of 1-15 s). Within kAtCount of the count it is taken a tenth under the count, so the rules give
/// back until the driver says again how much it allows. An allowance under the count is past already and stays; 0 (unknown) too
inline constexpr uint64_t kAtCount = 16ull << 20;
inline constexpr uint64_t kPastShare = 10;
inline uint64_t past_allowance(uint64_t used, uint64_t allowed) {
    if (!allowed || allowed + kAtCount < used || allowed > used + kAtCount) return allowed;
    return used - used / kPastShare;
}
} // namespace vram_allowance
