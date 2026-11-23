/* Why the GPU sat idle between two recordings (vk_replay.cpp, stream ORDER3, RX_VK_FEED_LOG).
   The GPU's own gap before a recording starts is split by what the host knew about that recording, in one clock:
     ready   the host had finished it (its commands recorded, or a segment waiting for the ones before it in the queue's order)
     sent    the host's vkQueueSubmit2 that carries it (for a recording held back for a late fix or a batch, the flush's)
     start   the GPU's first timestamp in it
   Idle time from the end of what ran before it, e, to start, g:
     not ready   e..ready          the replay had not finished recording what the GPU runs next: the feed is late. Split by the replay's
                                   own passes (run_submit holding the replay lock): busy = a pass ran in that time, idle = none did
                                   (the game had not handed the submission over yet, or its capture was still being taken)
     held        ready..sent       done but not handed to the queue: the in-order submit of the segments before it, a late fix, a batch
     after send  sent..g           handed over, the GPU not on it yet (the driver and the hardware's start latency)
     queued      the whole gap     it was sent before the GPU went idle: waits inside the GPU or the driver, not the host's doing
   No Vulkan in here: the times are numbers, so tests/test_feed_stats.cpp runs it with made-up ones. */
#pragma once

#include <algorithm>
#include <cstdint>
#include <vector>

namespace mkwvk::feed {

/// One recording as the host and the GPU saw it (ns on one clock; kind: 0 a whole recording, 1 a segment, 2 the early pre-sync's)
struct Rec {
    double ready = 0, sent = 0, start = 0, end = 0;
    int kind = 0;
    double handed = 0;   // (stream REPLAY2: when the game's submission reached the replay, run_submit's entry; 0: unknown)
};

/// (stream REPLAY2) what the replay thread was doing, in a run_submit pass: from `t` on, until the next mark
enum Phase {
    kOut = 0,      // not in a pass (the replay lock free, or taken by another thread)
    kSetup,        // run_submit: the recording begun, the memory work of the submission
    kWhole,        // a command buffer recorded on the replay thread as it comes (serial replay, or one segment)
    kDry,          // a big command buffer's dry pass
    kPresync,      // the mirrors' pre-sync and the readback refresh before a big command buffer's segments
    kJobs,         // a big command buffer's segment recordings begun (their pools, init command buffers)
    kOwn,          // the replay thread's own segment
    kJoin,         // the replay thread waiting for the segments (in order, and their submits)
    kAfter,        // after the segments: the next recording begun, the profile
    kFinish,       // finish_recording: the recording's end
    kSubmit,       // submit_recording (vkEndCommandBuffer, the queue lock, vkQueueSubmit2)
    kCapture,      // submit_q: the submission's capture of the mirrored pages, taken under sync_order_mu, before run_submit
    kQueue,        // submit_q: the queue's bookkeeping between the capture and run_submit
    kPhases
};
inline const char* phase_name(int p) {
    static const char* const n[kPhases] = {"not in a pass", "setup", "small cb recorded whole", "dry pass", "pre-sync", "segment setup", "own segment",
                                           "wait for segments", "after segments", "finish", "submit", "capture", "queue"};
    return p >= 0 && p < kPhases ? n[p] : "?";
}
struct Mark {
    double t = 0;
    int id = 0;
};

/// The phase marks' time in [lo, hi] by phase into out[kPhases] (marks sorted by t: a mark's phase lasts to the next one's)
inline void phase_cover(const std::vector<Mark>& marks, double lo, double hi, double* out) {
    if (hi <= lo || marks.empty()) return;
    size_t a = 0, b = marks.size();
    while (a < b) {   // (the first mark after lo)
        const size_t m = (a + b) / 2;
        if (marks[m].t <= lo) a = m + 1; else b = m;
    }
    int cur = a ? marks[a - 1].id : kOut;
    double at = lo;
    for (size_t i = a; i < marks.size() && marks[i].t < hi; i++) {
        out[cur] += marks[i].t - at;
        at = marks[i].t, cur = marks[i].id;
    }
    out[cur] += hi - at;
}

/// A pass of the replay (run_submit with the replay lock held), host ns
struct Span {
    double t0 = 0, t1 = 0;
};

/// How much of [lo, hi] the spans (sorted by t0, not overlapping: one lock) cover
inline double covered(const std::vector<Span>& spans, double lo, double hi) {
    if (hi <= lo || spans.empty()) return 0;
    size_t a = 0, b = spans.size();
    while (a < b) {   // (the first span that ends after lo)
        const size_t m = (a + b) / 2;
        if (spans[m].t1 <= lo) a = m + 1; else b = m;
    }
    double sum = 0;
    for (size_t i = a; i < spans.size() && spans[i].t0 < hi; i++) sum += std::min(hi, spans[i].t1) - std::max(lo, spans[i].t0);
    return sum;
}

struct Totals {
    double busy = 0;            // (the union of the recordings' run times)
    double idle = 0;            // (gaps of under 100 ms between them)
    double not_ready = 0, held = 0, after_send = 0, queued = 0;
    double not_ready_kind[3] = {0, 0, 0};
    double not_ready_busy = 0;  // (of not_ready: the replay was in a pass; the rest it was not)
    double before_hand[3] = {0, 0, 0};          // (stream REPLAY2: of not_ready, by kind: before the game's submission reached the replay)
    double phase[3][kPhases] = {};              // (and from the hand-over on: what the replay thread was doing, by recording kind)
    int64_t kind_gaps[3] = {0, 0, 0};           // (gaps before a recording of that kind)
    int64_t gaps = 0, big_gaps = 0;   // (gaps of 0.5 ms and over)
    double big_idle = 0;              // (their time)
    double longest = 0;
    double window = 0;          // (first start to last end, gaps of 100 ms or more left out)
    int64_t recs = 0;
};

/// The recordings of a window, any order: sorted by start, each gap against the latest end so far
inline Totals attribute(std::vector<Rec> v, const std::vector<Span>& spans = {}, double tolerance_ns = 50e3,
                        const std::vector<Mark>& marks = {}) {
    Totals t;
    std::sort(v.begin(), v.end(), [](const Rec& a, const Rec& b) { return a.start < b.start; });
    double end = 0, lo = 0;
    bool have = false;
    for (const Rec& r : v) {
        if (r.end <= r.start) continue;
        t.recs++;
        const bool first = !have;
        if (first) lo = r.start, have = true;
        if (!first && r.start > end) {
            const double e = end, g = r.start, gap = g - e;
            if (gap < 1e8) {
                t.idle += gap, t.gaps++;
                t.longest = std::max(t.longest, gap);
                if (gap >= 0.5e6) t.big_gaps++, t.big_idle += gap;
                if (r.sent && r.sent < e - tolerance_ns) {   // (sent well before the GPU went idle)
                    t.queued += gap;
                } else {
                    const double ready = r.ready ? std::clamp(r.ready, e, g) : e;
                    const double sent = r.sent ? std::clamp(r.sent, ready, g) : ready;
                    t.not_ready += ready - e, t.not_ready_kind[r.kind < 0 || r.kind > 2 ? 0 : r.kind] += ready - e;
                    t.not_ready_busy += covered(spans, e, ready);
                    const int k = r.kind < 0 || r.kind > 2 ? 0 : r.kind;
                    t.kind_gaps[k]++;
                    const double from = r.handed ? std::clamp(r.handed, e, ready) : e;   // (before it: the game had not handed it over)
                    t.before_hand[k] += from - e;
                    phase_cover(marks, from, ready, t.phase[k]);
                    t.held += sent - ready;
                    t.after_send += g - sent;
                }
            } else {
                t.window -= gap;
            }
        }
        if (r.end > end) {
            t.busy += r.end - std::max(end, r.start);
            end = r.end;
        }
    }
    if (have) t.window += end - lo;
    return t;
}

/// a window's totals added to the run's
inline void accumulate(Totals& a, const Totals& b) {
    a.busy += b.busy, a.idle += b.idle, a.not_ready += b.not_ready, a.held += b.held, a.after_send += b.after_send, a.queued += b.queued;
    for (int k = 0; k < 3; k++) a.not_ready_kind[k] += b.not_ready_kind[k];
    a.gaps += b.gaps, a.big_gaps += b.big_gaps, a.big_idle += b.big_idle, a.window += b.window, a.recs += b.recs;
    a.longest = std::max(a.longest, b.longest), a.not_ready_busy += b.not_ready_busy;
    for (int k = 0; k < 3; k++) {
        a.before_hand[k] += b.before_hand[k], a.kind_gaps[k] += b.kind_gaps[k];
        for (int p = 0; p < kPhases; p++) a.phase[k][p] += b.phase[k][p];
    }
}

} // namespace mkwvk::feed
