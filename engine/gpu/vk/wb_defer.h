// Copyright (C) 2026 Sacrappy
//
// The rules of the write-back put off to the frame's end (vk_memory.cpp "the write-back put off", RX_VK_WB_LAZY), without Vulkan,
// so a small program can test them (tests/test_wb_defer.py). The mechanism is pixl27's (ProjectReLink, GPL-3.0-or-later,
// RX_VK_WB_LAZY): the page-by-page choice, the flush's spread over frames and the link gate are as there; the code that applies
// them is ours.
#pragma once
#include <cstdint>
#include <cstdlib>

namespace wb_defer {

/// RX_VK_SLOW_LINK_GBPS (default 9): a measured GPU-to-system speed under it is a slow link
constexpr double kDefaultSlowLinkGbps = 9.0;

/// Whether the deferred write-back is on. `env` is RX_VK_WB_LAZY (null or empty: unset): 1 forces it on, 0 forces it off, whatever
/// the link; unset, it is on when the measured GPU-to-system speed (0: not measured) is under the line
inline bool on(const char* env, double d2h_gbps, double line_gbps) {
    if (env && *env) return std::atoi(env) != 0;
    return d2h_gbps > 0 && d2h_gbps < line_gbps;
}

/// The line: RX_VK_SLOW_LINK_GBPS when it is a positive number, else 9
inline double line(const char* env) {
    if (env && *env) {
        const double v = std::atof(env);
        if (v > 0) return v;
    }
    return kDefaultSlowLinkGbps;
}

/// A run (offsets in the pool, bytes) is looked at for pages to put off: whole 4-byte words, big enough, at least one whole page,
/// and not a texture's storage
inline bool run_eligible(uint64_t dst, uint64_t size, uint64_t min_run, uint64_t first_page, uint64_t last_page, bool texture) {
    return !((dst | size) & 3) && size >= min_run && first_page < last_page && !texture;
}

/// What the page facts say of one whole page of an eligible run: may its write-back wait for the flush?
/// known: the pool's shadow holds what guest memory does for it; mixed: the CPU and the GPU share it (a texture's storage, a page
/// the CPU wrote while it was put off); read: the CPU reads it back (invalidate, rb_pages); flushing: its turn in this recording's
/// flush; cpu_age: frames since a mirror sync found it written by the CPU (quiet: the age it must have)
inline bool page_may_wait(bool known, bool mixed, bool read, bool flushing, uint64_t cpu_age, uint64_t quiet) {
    return known && !mixed && !read && !flushing && cpu_age >= quiet;
}

/// A page is put off only once the GPU has written it in `need` frames running (RX_VK_WB_LAZY_HOT, default 1: every page). MEASURED
/// at 8: the pages written are other ones from frame to frame, 1,270 a frame were put off instead of 10,400 (the 5070 castle-move), so
/// the saving was gone; kept as a knob
inline bool hot_enough(uint32_t streak, uint32_t need) { return streak >= need; }

/// RX_VK_WB_LAZY_HOT (default 1 frame, at least 1)
inline uint32_t hot_frames(const char* env) {
    if (env && *env) {
        const long v = std::atol(env);
        return v < 1 ? 1u : (uint32_t)v;
    }
    return 1;
}

/// A read of a page put off teaches the region around it (RX_VK_WB_LAZY_REGION_KB, default 4096, a power of two, at least 4): MEASURED on the
/// 5070, a page at a time did not converge (the pages a game thread reads move along the pool from frame to frame, ~200 new ones a window);
/// the region's pages are written back and not put off again. region_bytes 0: the page only
inline uint64_t region_bytes(const char* env) {
    uint64_t kb = 4096;
    if (env && *env) {
        const long v = std::atol(env);
        kb = v < 4 ? 4u : (uint64_t)v;
    }
    uint64_t p = 4;
    while (p < kb) p <<= 1;   // (rounded up to a power of two)
    return p << 10;
}
inline uint64_t region_base(uint64_t guest, uint64_t region) { return guest & ~(region - 1); }

/// The flush spread over `period` presents: a 64-page word of a pool's table is written back at the present whose number is its
/// index modulo period (period 1: all of them every present)
inline bool word_in_share(uint64_t word, uint64_t period, uint64_t phase) { return period <= 1 || word % period == phase; }

/// RX_VK_WB_LAZY_FRAMES (default 30, at least 1)
inline uint64_t period(const char* env) {
    if (env && *env) {
        const long v = std::atol(env);
        return v < 1 ? 1u : (uint64_t)v;
    }
    return 30;
}

/// RX_VK_WB_LAZY_QUIET (default 120 frames, 0 allowed)
inline uint64_t quiet(const char* env) {
    if (env && *env) {
        const long v = std::atol(env);
        return v < 0 ? 0u : (uint64_t)v;
    }
    return 120;
}

/// RX_VK_WB_LAZY_MIN_KB (default 128, at least 8): runs under it are written back as before
inline uint64_t min_run_bytes(const char* env) {
    uint64_t kb = 128;
    if (env && *env) {
        const long v = std::atol(env);
        kb = v < 8 ? 8u : (uint64_t)v;
    }
    return kb << 10;
}

}   // namespace wb_defer
