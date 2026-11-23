// Copyright (C) 2026 Sacrappy

// RX_VK_READBACK_LATE=<rule>[,<rule>...]: the CPU reads of GPU results that do not wait for their write-back (vk_replay.cpp
// invalidate). Off unless set: every such read waits, as before. The CPU then reads what guest memory holds, the write-back
// of the frame before. Rules, either of the first two is enough to make a range late:
//   newest       the range's writer is the newest submission handed out: the game reads it right after submitting the work
//                that fills it, so it cannot count on that work being done;
//   <hex size>   every range of exactly that many bytes, whoever wrote it;
//   -<hex size>  never late, whatever the other rules say (the light meter's ranges: a stale meter is a flashing picture).
// (The first two are Longshot's, #238, measured on a Steam Deck; the third is this tree's, from the light meter's sizes.)
#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace readback_late {

struct Rules {
    bool newest = false;
    std::vector<uint64_t> sizes, never;
    bool any() const { return newest || !sizes.empty(); }
};

inline Rules parse(const char* text) {
    Rules r;
    for (const char* p = text; p && *p;) {
        if (!strncmp(p, "newest", 6)) r.newest = true;
        else if (*p == '-') {
            if (const uint64_t n = strtoull(p + 1, nullptr, 16)) r.never.push_back(n);
        } else if (const uint64_t n = strtoull(p, nullptr, 16)) r.sizes.push_back(n);
        p = strchr(p, ',');
        if (p) p++;
    }
    return r;
}

inline bool wanted(const Rules& r, uint64_t size, bool newest) {
    for (const uint64_t s : r.never)
        if (s == size) return false;
    if (newest && r.newest) return true;
    for (const uint64_t s : r.sizes)
        if (s == size) return true;
    return false;
}

} // namespace readback_late
