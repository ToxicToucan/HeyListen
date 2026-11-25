// Copyright (C) 2026 Sacrappy
// The replay's timeline signals (stream DECOUPLE, docs/research/decouple-cross-frame.md section 4.9). The timeline's value V means:
// every submission up to V is recorded whole and its GPU work done. A recording's end signals the highest value it can say that of:
// the newest value recorded whole (done_max), below the oldest submission stopped at an event wait, and (with RX_VK_ORDER_THREAD)
// below a present handed over and not recorded yet. The order thread runs the jobs behind a present it holds for the CPU's signal
// (PIPE4); their values are higher than the present's, so without the last bound they signalled past it, and the present's own
// signal then went BACK on the timeline (a timeline signal must rise: the driver took the lower value). A late fix deferred
// meanwhile (RX_VK_LATE_DEFER: its value at or below the highest signal sent) then waited for a value the timeline had gone back
// below, with every recording held behind the fix: the PC 3050's load hang (matrix82 kO1, mO3; matrix97, 5 starts in 20).
// The pure rule, with no Vulkan: tests/test_timeline_order.py.
#pragma once
#include <algorithm>
#include <cstdint>

namespace timeline_order {

/// The value a recording's end may signal: done_max, below oldest_stopped (UINT64_MAX: none stopped) and below held_present
/// (0: none; a present handed over to the order thread and not recorded yet)
inline uint64_t signal_at(uint64_t done_max, uint64_t oldest_stopped, uint64_t held_present) {
    uint64_t at = oldest_stopped == UINT64_MAX ? done_max : std::min(done_max, oldest_stopped - 1);
    if (held_present) at = std::min(at, held_present - 1);
    return at;
}
/// Whether a signal of `at` moves the timeline on: only a value above the last one sent (a timeline never goes back)
inline bool signal_moves(uint64_t at, uint64_t signaled) { return at > signaled; }

}   // namespace timeline_order
