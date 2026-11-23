#include "../gpu/vk/feed_stats.h"

#include <cmath>
#include <cstdio>

#define CHECK(name, condition) do { if (!(condition)) { std::fprintf(stderr, "FAIL %s at line %d\n", name, __LINE__); return 1; } } while (0)

using namespace mkwvk::feed;

static bool near(double a, double b) { return std::fabs(a - b) < 1.0; }

int main() {
    // (1) the GPU's gap is split at the host's ready and sent times
    {
        std::vector<Rec> v;
        v.push_back({0, 0, 0, 1000e3, 0});                       // a recording that ran 0..1 ms
        v.push_back({1500e3, 1800e3, 2000e3, 3000e3, 1});        // ready at 1.5, sent at 1.8, started at 2.0
        const Totals t = attribute(v);
        CHECK("busy", near(t.busy, 2000e3));
        CHECK("idle", near(t.idle, 1000e3));
        CHECK("not ready", near(t.not_ready, 500e3));
        CHECK("held", near(t.held, 300e3));
        CHECK("after send", near(t.after_send, 200e3));
        CHECK("queued", near(t.queued, 0));
        CHECK("not ready by kind", near(t.not_ready_kind[1], 500e3) && near(t.not_ready_kind[0], 0));
        CHECK("window", near(t.window, 3000e3));
        CHECK("one big gap", t.big_gaps == 1 && near(t.longest, 1000e3));
    }
    // (2) sent before the GPU went idle: the whole gap is the GPU's own
    {
        std::vector<Rec> v;
        v.push_back({0, 0, 0, 1000e3, 0});
        v.push_back({100e3, 200e3, 1400e3, 2000e3, 0});
        const Totals t = attribute(v);
        CHECK("queued", near(t.queued, 400e3) && near(t.not_ready, 0) && near(t.held, 0) && near(t.after_send, 0));
    }
    // (3) a recording that was ready and sent before the end is ordered by start, out of order in the input; overlaps add no idle
    {
        std::vector<Rec> v;
        v.push_back({0, 0, 800e3, 1500e3, 0});
        v.push_back({0, 0, 0, 1000e3, 0});
        const Totals t = attribute(v);
        CHECK("overlap: no idle", near(t.idle, 0) && t.gaps == 0);
        CHECK("busy of the union", near(t.busy, 1500e3));
    }
    // (4) gaps of 100 ms and over are left out of the window and the idle
    {
        std::vector<Rec> v;
        v.push_back({0, 0, 0, 1000e3, 0});
        v.push_back({0, 0, 500e6, 501e6, 0});
        const Totals t = attribute(v);
        CHECK("long gap left out", near(t.idle, 0) && near(t.window, 1000e3 + 1000e3));
    }
    // (5) unknown host times (0): the gap counts as not ready for the whole of it, never negative
    {
        std::vector<Rec> v;
        v.push_back({0, 0, 0, 1000e3, 0});
        v.push_back({0, 0, 1200e3, 2000e3, 2});
        const Totals t = attribute(v);
        CHECK("unknown times", near(t.idle, 200e3) && t.not_ready >= 0 && t.held >= 0 && t.after_send >= 0 &&
                               near(t.not_ready + t.held + t.after_send + t.queued, 200e3));
    }
    // (6) clocks far from zero: the first recording is not a gap from 0
    {
        std::vector<Rec> v;
        v.push_back({1e15, 1e15, 1e15 + 10, 1e15 + 1000e3, 0});
        v.push_back({1e15, 1e15, 1e15 + 1500e3, 1e15 + 2000e3, 0});
        const Totals t = attribute(v);
        CHECK("first is no gap", t.gaps == 1 && near(t.idle, 500e3) && near(t.window, 2000e3 - 10));
    }
    // (7) the replay's passes: how much of the not-ready time the replay was in a pass
    {
        std::vector<Rec> v;
        v.push_back({0, 0, 0, 1000e3, 0});
        v.push_back({2000e3, 2100e3, 2200e3, 3000e3, 0});          // idle 1.0..2.2: not ready 1.0..2.0, sent 2.1
        std::vector<Span> spans = {{1200e3, 1500e3}, {1800e3, 2500e3}};
        const Totals t = attribute(v, spans);
        CHECK("not ready", near(t.not_ready, 1000e3));
        CHECK("covered by passes", near(t.not_ready_busy, 300e3 + 200e3));
        CHECK("covered edges", near(covered(spans, 0, 5000e3), 1000e3) && near(covered(spans, 1400e3, 1900e3), 200e3) && near(covered({}, 0, 1), 0));
    }
    // (8) stream REPLAY2: the not-ready time before the hand-over, then by what the replay thread was doing
    {
        std::vector<Rec> v;
        v.push_back({0, 0, 0, 1000e3, 0});
        Rec r{3000e3, 3100e3, 3200e3, 4000e3, 0};
        r.handed = 1500e3;   // idle 1.0..3.2: not ready 1.0..3.0, handed over at 1.5
        v.push_back(r);
        std::vector<Mark> marks = {{1500e3, kSetup}, {1600e3, kWhole}, {2000e3, kPresync}, {2600e3, kDry}, {2800e3, kOut}};
        const Totals t = attribute(v, {}, 50e3, marks);
        CHECK("before hand", near(t.before_hand[0], 500e3) && t.kind_gaps[0] == 1);
        CHECK("setup", near(t.phase[0][kSetup], 100e3));
        CHECK("whole", near(t.phase[0][kWhole], 400e3));
        CHECK("presync", near(t.phase[0][kPresync], 600e3));
        CHECK("dry", near(t.phase[0][kDry], 200e3));
        CHECK("out", near(t.phase[0][kOut], 200e3));
        double sum = 0;
        for (int p = 0; p < kPhases; p++) sum += t.phase[0][p];
        CHECK("the phases add up to the time since the hand-over", near(sum, 1500e3));
        CHECK("none for others", near(t.phase[1][kSetup], 0));
    }
    std::puts("feed_stats: ok");
    return 0;
}
