// HL_RB_LAG: one deterministic readback mechanism for the engine's three GPU-result classes (docs/native-engine/readback-ring.md).
// The pure rules, with no Vulkan in them, so a small program can test them (tests/test_rb_ring.py):
// - a class is told by what the CPU reads: R1 the exposure meter's 0x1040 block (its first 16 bytes), R2 a 0x40 slot, R3 a 0x2000 block;
// - every submission that wrote such a range (a binding written by a program, in the mirror) stages the range's bytes into an entry of an
//   engine-owned ring, tagged with the frame count at the end of that submission; the game's own write-back of the range is not made;
// - a read at frame c is answered by applying to guest memory the newest entry of the range with tag + lag <= c that was not applied
//   yet (lag 1: what the old path waits for; lag 2: one frame older, finished without waiting); nothing else writes the range, so what
//   the game sees depends on the frame count and not on the GPU's timing.
#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace rb_ring {

constexpr unsigned kMaxEntries = 8;      // entries kept an address
constexpr unsigned kMaxLag = 6;
constexpr uint64_t kArena = 4ull << 20;  // bytes of the ring's host memory
constexpr uint64_t kMaxPending = 1ull << 20;   // bytes staged by one submission at most

/// HL_RB_LAG=<L> (unset: 2, the default since the 3050 and 5070 A/Bs of 2026-11-21; 0: off, the old waiting path; 1..kMaxLag),
/// HL_RB_LAG_CHECK=1 (the old path runs, the ring is compared with what it read; no change in what the game sees), HL_RB_LAG_WAIT_MS
/// (a read whose entry is not finished waits this long; 200)
struct Config {
    unsigned lag = 2;
    bool check = false;
    double wait_ms = 200.0;
};
inline Config parse(const char* lag, const char* check, const char* wait) {
    Config c;
    if (lag && *lag) {
        const long v = atol(lag);
        c.lag = v <= 0 ? 0u : (v > (long)kMaxLag ? (unsigned)kMaxLag : (unsigned)v);
    }
    c.check = check && *check && atoi(check) != 0;
    if (wait && *wait) {
        const double w = atof(wait);
        if (w >= 0) c.wait_ms = w;
    }
    return c;
}

/// The class of a CPU read: 1 the meter's block (size 0x1040: the game has three such rings and the Market's programs write only one of them,
/// the others are read all the same; and its 0xc0 companion block), 2 a 0x40 slot, 3 a 0x2000 block, 0 none
inline int class_of(uint64_t size) { return size == 0x1040 || size == 0xc0 ? 1 : size == 0x40 ? 2 : size == 0x2000 ? 3 : 0; }
/// The bytes of the range that are staged and handed back (the CPU reads words 0 and 3 of the meter's block only)
inline uint64_t copy_bytes(int cls, uint64_t size) { return cls == 1 && size == 0x1040 ? 16 : size; }

struct Entry {
    uint64_t seq = 0;     // 1, 2, ... in staging order (0: empty)
    int64_t tag = 0;      // frames (presents) done when the submission that wrote it ended
    uint64_t value = 0;   // the timeline value of that submission: the entry is complete when the GPU has reached it
    uint64_t at = 0;      // offset in the ring's memory
    uint64_t cum = 0;     // bytes the ring had handed out before it (its place is overwritten once the ring has gone round)
};

/// The entry a read at frame c is answered with: the newest (highest seq) with tag + lag <= c and seq > applied; -1 when there is none
inline int pick(const Entry* e, unsigned n, int64_t c, unsigned lag, uint64_t applied) {
    int best = -1;
    for (unsigned i = 0; i < n; i++)
        if (e[i].seq > applied && e[i].tag + (int64_t)lag <= c && (best < 0 || e[i].seq > e[best].seq)) best = (int)i;
    return best;
}
/// The newest entry whatever its tag (for the age statistics), -1 when none
inline int newest(const Entry* e, unsigned n) {
    int best = -1;
    for (unsigned i = 0; i < n; i++)
        if (e[i].seq && (best < 0 || e[i].seq > e[best].seq)) best = (int)i;
    return best;
}
/// An entry's place is still its own: the ring has handed out less than kArena bytes since it (and the entry's own bytes)
inline bool intact(const Entry& e, uint64_t total, uint64_t bytes) { return e.seq && total - e.cum + bytes <= kArena; }

constexpr unsigned kQuietReads = 8;   // reads of a range no program writes before it is answered without a wait (the write-backs of before are long done)
constexpr uint64_t kUnsettled = ~0ull;

/// A learned range's standing (vk_rbring.cpp keeps one a range): the written bindings of exactly it (writes, counted at each binding),
/// how many of them were staged, the writes before the staged ones (first_stage: they went to guest memory with their write-back), a
/// written binding over it that is not exactly it (tainted), the CPU's reads, and settle: a timeline value the GPU must have reached
/// before the ring answers (0 none; kUnsettled until the end of the submission that staged it gives the value)
struct Standing {
    bool stageable = false, tainted = false;
    uint64_t writes = 0, staged = 0, first_stage = 0, reads = 0, settle = 0;
};
/// The ring answers the range's reads (done: the timeline value the GPU has completed): every exact write since first_stage was staged,
/// nothing else's write-back lands in it, the write-backs of the writes before have landed (settle), and either a staged entry exists or
/// no program has written it at all (a result slot nothing wrote: nothing to wait for)
inline bool served(const Standing& a, uint64_t done) {
    if (a.tainted || a.writes - a.first_stage != a.staged || a.settle > done) return false;
    return a.stageable || (a.writes == 0 && a.reads >= kQuietReads);
}
/// A written binding of exactly the range, in a mirror, counted in `writes` just before: true when it is staged (no write-back is made
/// for it; the end of the submission copies it into the ring). (EXPO1) A range the ring does not serve keeps its write-backs: its reads
/// take the old path, which reads guest memory. A tainted range never is staged. Before 2026-11-23 every learned range was staged, served
/// or not, so a range that one direct write (a binding in guest memory while its pool had no mirror, after a zone change) had moved to
/// the old path never got its GPU results again: OoT's exposure meter read the game's own zeros for good, the light meter's hold handed
/// back the last sums for ever, and the exposure froze (docs/research/expo1-stuck-meter.md). Exact writes before this one that were not
/// staged went to guest memory with their write-back: the ring takes the range over again from this binding, and answers only once the
/// GPU is past this submission (settle), so none of those write-backs can land after one of its answers
inline bool stage(Standing& a) {
    if (a.tainted) return false;
    const uint64_t before = a.writes ? a.writes - 1 : 0;   // (the exact writes before this one)
    const bool unstaged_before = a.stageable ? before - a.first_stage != a.staged : before != 0;
    a.first_stage = before >= a.staged ? before - a.staged : 0;
    if (unstaged_before) a.settle = kUnsettled;
    a.staged++, a.stageable = true;
    return true;
}
/// The end of the submission that staged it: a range waiting for its settle value gets this submission's
inline void settle_at(Standing& a, uint64_t value) {
    if (a.settle == kUnsettled) a.settle = value;
}

/// Adds an entry to a full table of n entries (n <= kMaxEntries): the oldest one goes when the table is full
inline void push(Entry* e, unsigned& n, const Entry& add) {
    if (n < kMaxEntries) {
        e[n++] = add;
        return;
    }
    unsigned oldest = 0;
    for (unsigned i = 1; i < n; i++)
        if (e[i].seq < e[oldest].seq) oldest = i;
    e[oldest] = add;
}

/// The ring's memory: the offset of the next `bytes` (16-byte aligned), wrapping to 0 when they would not fit; `total` counts what was
/// handed out including the bytes skipped at a wrap
inline uint64_t allocate(uint64_t& at, uint64_t& total, uint64_t bytes) {
    bytes = (bytes + 15) & ~15ull;
    if (at + bytes > kArena) total += kArena - at, at = 0;
    const uint64_t r = at;
    at += bytes;
    total += bytes;
    return r;
}

/// FNV-1a of a read's class, address and the bytes it was handed, for the per-frame served-values hash (summed over a frame's reads,
/// so the order of the game's threads does not matter)
inline uint64_t hash_read(int cls, uint64_t guest, const void* data, uint64_t n) {
    uint64_t h = 1469598103934665603ull;
    auto mix = [&](const uint8_t* p, uint64_t k) {
        for (uint64_t i = 0; i < k; i++) h = (h ^ p[i]) * 1099511628211ull;
    };
    const uint64_t head[2] = {(uint64_t)cls, guest};
    mix((const uint8_t*)head, sizeof head);
    if (data && n) mix((const uint8_t*)data, n);
    return h;
}

}   // namespace rb_ring
