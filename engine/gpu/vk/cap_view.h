// Copyright (C) 2026 Sacrappy
// The capture view of a submission, several captures deep (stream DECOUPLE, docs/research/decouple-cross-frame.md stage 2).
// A capture replaces a page's entry in the capture table. A recording thread that works on an earlier submission (a segment's worker
// while the replay thread has begun the submissions after it) must read "guest memory as it was at its submission": the newest entry
// of the page that is not newer than the newest capture its submission saw (its limit). SUB1 kept the one entry the newest capture
// replaced (cap_prev); with K submissions' segments recording at once a page may have been taken by K captures past a worker's limit,
// so the entries replaced are kept `depth` deep, newest first. The pure rules, with no Vulkan in them (tests/test_cap_view.py).
#pragma once
#include <cstdint>
#include <cstdlib>

namespace cap_view {

constexpr unsigned kMaxDepth = 4;

struct Entry {
    uint32_t gen = 0, slot = 0;   // (gen 0: no entry)
};

/// RX_VK_CAP_DEPTH=<1..kMaxDepth> (unset: 1, SUB1's one entry): how many replaced entries a page keeps
inline unsigned parse_depth(const char* e) {
    if (!e || !*e) return 1;
    const long v = atol(e);
    return v < 1 ? 1u : v > (long)kMaxDepth ? kMaxDepth : (unsigned)v;
}

/// A capture replaces the page's entry: the replaced one goes to the front of the page's history, the oldest falls out
inline void push(Entry* hist, unsigned depth, Entry replaced) {
    for (unsigned i = depth; i-- > 1;) hist[i] = hist[i - 1];
    if (depth) hist[0] = replaced;
}

/// The page's entry as a submission whose newest capture is `limit` saw it (limit 0: no limit, the newest): `cur` when it is not
/// newer than the limit, else the newest replaced entry that is not; none ({0, 0}: guest memory, which may hold later bytes) when
/// every kept entry is newer or the page had none then. *old: the entry is a replaced one (its validity is not the page's current
/// generation's: it was valid when it was replaced); *lost: entries newer than the limit were found and none at or under it within
/// the depth kept, so an entry at or under the limit may have fallen out (the check's "view deeper than the history" count)
inline Entry pick(Entry cur, const Entry* hist, unsigned depth, uint32_t limit, bool* old, bool* lost) {
    *old = false, *lost = false;
    if (!limit || cur.gen <= limit) return cur;
    for (unsigned i = 0; i < depth; i++) {
        if (!hist[i].gen) return {};   // (the page had no entry before that capture)
        if (hist[i].gen <= limit) {
            *old = true;
            return hist[i];
        }
    }
    *lost = depth != 0;
    return {};
}

}   // namespace cap_view
