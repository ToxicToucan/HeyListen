// Copyright (C) 2026 Sacrappy
#pragma once
// Which retired host-memory imports to give back so that what a long session keeps pinned stays bounded (issues 131, 121).
// Pure bookkeeping, no Vulkan: vk_memory.cpp's retired_imports_give_back hands it the retired imports (oldest first) and the
// live ones, and frees what it names. Tests: tests/test_retired_bound.cpp.
//
// The rule: nothing is given back until the retired imports hold more than `limit` bytes. Then, oldest first and at most
// `max_n` a call, those that are idle (the caller's test: no mirror, the GPU past them) and old enough (`age` submissions since
// they were retired) go, until the retired bytes are back at the limit. A retired import that overlaps a live one (the live
// pool took it in: its pages stay pinned by the live import whatever happens) is the more doubtful case on some drivers: it is
// left alone until the retired bytes pass `overlap_over`; the ones that overlap nothing are the pages a dead zone pinned.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace mkwvk::retired_bound {

struct Cfg {
    uint64_t limit = 1024ull << 20;       // (retired imported bytes kept)
    uint64_t overlap_over = 2048ull << 20;  // (past this the ones overlapping a live import go too; 0: from the limit; UINT64_MAX: never)
    size_t max_n = 2;                     // (given back in one call)
    uint64_t age = 600;                   // (submissions a retired pool is kept at least)
};

struct Cand {
    uint64_t lo = 0, hi = 0;
    uint64_t retired_seq = 0;   // (the submission count when it was retired)
    bool idle = false;          // (the caller's rule: no mirror, nothing pending, the GPU past it)
};

struct Range {
    uint64_t lo, hi;
};

inline bool overlaps_any(const std::vector<Range>& live_sorted, uint64_t lo, uint64_t hi) {
    // (live ranges sorted by lo and disjoint, as the pool table keeps them)
    auto it = std::lower_bound(live_sorted.begin(), live_sorted.end(), hi, [](const Range& r, uint64_t v) { return r.lo < v; });
    if (it == live_sorted.begin()) return false;
    --it;   // (disjoint and sorted: the one just below hi is the only one that can reach lo)
    return it->hi > lo;
}

/// The indices of `cands` (oldest first) to give back now. `retired_bytes`: all the retired imports still held, idle or not;
/// `now`: the submission count.
inline std::vector<size_t> plan(const std::vector<Cand>& cands, const std::vector<Range>& live_sorted, uint64_t retired_bytes,
                                uint64_t now, const Cfg& cfg) {
    std::vector<size_t> out;
    if (retired_bytes <= cfg.limit || !cfg.max_n) return out;
    std::vector<char> taken(cands.size(), 0);
    for (int pass = 0; pass < 2 && out.size() < cfg.max_n && retired_bytes > cfg.limit; pass++) {
        if (pass == 1 && retired_bytes <= cfg.overlap_over) break;   // (the overlapping ones only past their own line)
        for (size_t i = 0; i < cands.size() && out.size() < cfg.max_n && retired_bytes > cfg.limit; i++) {
            const Cand& c = cands[i];
            if (taken[i] || !c.idle || c.hi <= c.lo || now < c.retired_seq + cfg.age) continue;
            const bool over = overlaps_any(live_sorted, c.lo, c.hi);
            if (over != (pass == 1)) continue;
            taken[i] = 1;
            out.push_back(i);
            retired_bytes -= std::min<uint64_t>(retired_bytes, c.hi - c.lo);
        }
    }
    return out;
}

}  // namespace mkwvk::retired_bound
