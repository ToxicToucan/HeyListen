// Copyright (C) 2026 Sacrappy

// The storm hold's state machine (gpu/vk/storm_hold.h), with no game, no GPU and no environment behind it: the latch,
// its engage depth, its floor and its quiet rule, and the depth rule the first version used. Built by
// tests/test_storm_hold_present.py.
#include "storm_hold.h"

#include <algorithm>
#include <cstdio>

using namespace mkwvk;

namespace {

int failures = 0;
void expect(bool ok, const char* what) {
    if (!ok) std::printf("FAIL: %s\n", what), failures++;
}

StormHoldRules rules(int deep, int64_t min_frames, int64_t quiet_frames) {
    StormHoldRules r;
    r.deep = deep;
    r.min_frames = min_frames;
    r.quiet_frames = quiet_frames;
    r.off_depth = deep > 0 ? std::max<int64_t>(1, deep / 2) : 0;
    r.settle = 1;
    return r;
}

} // namespace

int main() {
    {   // (the defaults: the switches' numbers and the present held, not the draws)
        const StormHoldRules d;
        expect(d.deep == 200 && d.min_frames == 0 && d.quiet_frames == 0 && d.off_depth == 100 && d.settle == 1 && !d.whole_frames,
               "the defaults: engaged at 200 queued, released as the queue drops under half of that");
        expect(d.live_frames == 3 && d.live_off_depth == 50, "the release counts the jobs asked for in the last 3 presents, under 50");
        expect(d.hold_present, "the present is what is held by default");
    }
    {   // (RX_VK_STORM_HOLD=0: no latch, whatever the queue holds)
        StormHold h;
        const StormHoldRules r = rules(0, 400, 100);
        for (int i = 0; i < 10; i++)
            expect(h.tick(r, 5000, 0, 10 + i) == StormHoldEvent::None && !h.on, "deep 0 never engages");
    }
    {   // (engage at the depth, not below it; since_frame is the present it engaged at)
        StormHold h;
        const StormHoldRules r = rules(200, 400, 100);
        expect(h.tick(r, 199, 0, 50) == StormHoldEvent::None && !h.on, "199 queued is under the engage depth");
        expect(h.tick(r, 200, 0, 51) == StormHoldEvent::Engaged && h.on, "200 queued engages");
        expect(h.since_frame == 51, "the engage's present is remembered");
        expect(h.tick(r, 500, 0, 60) == StormHoldEvent::None && h.on, "already held: no second engage");
    }
    {   // (the floor: nothing releases within min_frames presents, quiet or not)
        StormHold h;
        const StormHoldRules r = rules(200, 400, 100);
        h.tick(r, 300, 0, 1000);
        expect(h.tick(r, 0, 10000, 1399) == StormHoldEvent::None && h.on, "399 presents after the engage: still held");
        expect(h.tick(r, 0, 10000, 1400) == StormHoldEvent::Released && !h.on,
               "at the floor, the queue long quiet: released");
    }
    {   // (the quiet rule: the queue's depth alone does not release while jobs are still being pushed)
        StormHold h;
        const StormHoldRules r = rules(200, 100, 50);
        h.tick(r, 300, 0, 0);
        expect(h.tick(r, 0, 49, 200) == StormHoldEvent::None && h.on,
               "the queue empty but a job pushed 49 presents ago: still held");
        expect(h.tick(r, 0, 50, 201) == StormHoldEvent::Released && !h.on, "quiet for the whole quiet window: released");
    }
    {   // (a held queue that refills stays held past the floor)
        StormHold h;
        const StormHoldRules r = rules(200, 100, 50);
        h.tick(r, 300, 0, 0);
        for (uint64_t f = 100; f < 400; f += 10)
            expect(h.tick(r, 400, 0, f) == StormHoldEvent::None && h.on, "the churn still pushing: held past the floor");
        expect(h.tick(r, 0, 50, 400) == StormHoldEvent::Released, "and released when it stops");
    }
    {   // (quiet_frames=0: the first version's rule, the queue below half the engage depth, still after the floor)
        StormHold h;
        const StormHoldRules r = rules(200, 400, 0);
        h.tick(r, 300, 0, 0);
        expect(h.tick(r, 100, 0, 400) == StormHoldEvent::None && h.on, "a queue at half the engage depth holds");
        expect(h.tick(r, 99, 0, 401) == StormHoldEvent::Released && !h.on, "under it, after the floor: released");
        StormHold h2;
        h2.tick(r, 300, 0, 0);
        expect(h2.tick(r, 0, 0, 399) == StormHoldEvent::None && h2.on, "the depth rule keeps the floor too");
    }
    {   // (the drained-queue rule: under the depth for settle presents running, a deep tick starts it again)
        StormHold h;
        StormHoldRules d;
        d.off_depth = 4, d.settle = 3;
        h.tick(d, 300, 0, 0);
        expect(h.tick(d, 3, 0, 10) == StormHoldEvent::None && h.on, "one present with the queue drained: still held");
        expect(h.tick(d, 3, 0, 11) == StormHoldEvent::None && h.on, "two: still held");
        expect(h.tick(d, 50, 0, 12) == StormHoldEvent::None && h.on, "a refill restarts the count");
        expect(h.tick(d, 0, 0, 13) == StormHoldEvent::None && h.tick(d, 0, 0, 14) == StormHoldEvent::None && h.on,
               "two again: held");
        expect(h.tick(d, 0, 0, 15) == StormHoldEvent::Released && !h.on, "the third running: released");
    }
    {   // (whole frames end the hold whatever the queue holds; a frame that left a draw out starts the count again)
        StormHold h;
        StormHoldRules d;
        d.whole_frames = true, d.settle = 3;
        h.tick(d, 300, 0, 0);
        expect(h.tick(d, 150, 0, 10, 0) == StormHoldEvent::None && h.tick(d, 150, 0, 11, 0) == StormHoldEvent::None, "two whole frames");
        expect(h.tick(d, 150, 0, 12, 5) == StormHoldEvent::None && h.on, "a frame with a draw left out restarts the count");
        expect(h.tick(d, 150, 0, 13, 0) == StormHoldEvent::None && h.tick(d, 150, 0, 14, 0) == StormHoldEvent::None && h.on, "two again");
        expect(h.tick(d, 150, 0, 15, 0) == StormHoldEvent::Released && !h.on, "the third whole frame running: released");
        StormHold h2;
        StormHoldRules off;
        off.whole_frames = false, off.settle = 3;
        h2.tick(off, 300, 0, 0);
        for (int i = 1; i < 10; i++) expect(h2.tick(off, 150, 0, i, 0) == StormHoldEvent::None && h2.on, "whole frames off: the queue rules");
    }
    {   // (live: the release counts the jobs a draw asked for lately, not the queue's stale rest; < 0: the queue)
        StormHold h;
        StormHoldRules d;
        d.off_depth = 10, d.live_off_depth = 10, d.settle = 2;
        h.tick(d, 300, 0, 0);
        expect(h.tick(d, 300, 0, 10, -1, 5) == StormHoldEvent::None && h.on, "one present with few live jobs: held");
        expect(h.tick(d, 300, 0, 11, -1, 50) == StormHoldEvent::None && h.on, "a live refill restarts the count");
        expect(h.tick(d, 300, 0, 12, -1, 5) == StormHoldEvent::None && h.on, "one again");
        expect(h.tick(d, 300, 0, 13, -1, 0) == StormHoldEvent::Released && !h.on, "the second running: released with 300 queued");
        StormHold h2;
        h2.tick(d, 300, 0, 0);
        for (int i = 1; i < 6; i++) expect(h2.tick(d, 300, 0, i, -1, -1) == StormHoldEvent::None && h2.on, "no live count: the queue rules");
    }
    {   // (re-engage: a second storm after a release starts its own clock)
        StormHold h;
        const StormHoldRules r = rules(200, 400, 100);
        h.tick(r, 300, 0, 0);
        expect(h.tick(r, 0, 500, 400) == StormHoldEvent::Released, "the first storm drains");
        expect(h.tick(r, 300, 0, 5000) == StormHoldEvent::Engaged && h.since_frame == 5000, "a second storm engages again");
        expect(h.tick(r, 0, 500, 5100) == StormHoldEvent::None && h.on, "with a floor of its own");
    }
    if (failures) {
        std::printf("FAIL: %d checks\n", failures);
        return 1;
    }
    std::puts("PASS: the storm hold latches at its depth, holds its floor, releases on the quiet and re-engages");
    return 0;
}
