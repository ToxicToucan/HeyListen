// Copyright (C) 2026 Sacrappy

// retired_bound::plan (gpu/vk/retired_bound.h): which retired host-memory imports go back so that a long session's imports stay
// bounded (issues 131, 121). No game, no GPU: a session is simulated, zones leaving 16 MB chunks retired all the time.
#include "../gpu/vk/retired_bound.h"

#include <cstdio>
#include <vector>

using namespace mkwvk::retired_bound;

namespace {
int failures = 0;
void expect(bool ok, const char* what) {
    if (!ok) std::printf("FAIL: %s\n", what), failures++;
}
constexpr uint64_t MB = 1ull << 20;
Cand mk(uint64_t lo_mb, uint64_t mb, uint64_t seq, bool idle = true) { return Cand{lo_mb * MB, (lo_mb + mb) * MB, seq, idle}; }
}  // namespace

int main() {
    Cfg cfg;
    cfg.limit = 100 * MB, cfg.overlap_over = 200 * MB, cfg.max_n = 2, cfg.age = 10;
    const std::vector<Range> none;

    {   // under the limit: nothing, however old
        std::vector<Cand> c = {mk(0, 16, 0), mk(100, 16, 0)};
        expect(plan(c, none, 100 * MB, 1000, cfg).empty(), "nothing is given back at the limit");
        expect(plan(c, none, 32 * MB, 1000, cfg).empty(), "nothing is given back under the limit");
    }
    {   // past it: the oldest first, at most max_n, and no more than what brings it back to the limit
        std::vector<Cand> c = {mk(0, 16, 1), mk(100, 16, 2), mk(200, 16, 3), mk(300, 16, 4)};
        auto r = plan(c, none, 112 * MB, 1000, cfg);
        expect(r.size() == 1 && r[0] == 0, "one chunk brings 112 MB back to 96");
        r = plan(c, none, 200 * MB, 1000, cfg);
        expect(r.size() == 2 && r[0] == 0 && r[1] == 1, "the two oldest, max_n a call");
    }
    {   // not idle, too young: skipped, the next ones go
        std::vector<Cand> c = {mk(0, 16, 1, false), mk(100, 16, 995), mk(200, 16, 3), mk(300, 16, 4)};
        auto r = plan(c, none, 150 * MB, 1000, cfg);
        expect(r.size() == 2 && r[0] == 2 && r[1] == 3, "a busy one and a young one are left alone");
    }
    {   // overlapping a live import: not before its own line
        std::vector<Range> live = {{0, 64 * MB}};
        std::vector<Cand> c = {mk(0, 16, 1), mk(16, 16, 1), mk(300, 16, 2)};
        auto r = plan(c, live, 150 * MB, 1000, cfg);
        expect(r.size() == 1 && r[0] == 2, "only the one that overlaps no live import goes under overlap_over");
        r = plan(c, live, 250 * MB, 1000, cfg);
        expect(r.size() == 2 && r[0] == 2 && (r[1] == 0 || r[1] == 1), "past overlap_over the overlapping ones go, after the free ones");
        expect(!overlaps_any(live, 64 * MB, 80 * MB) && overlaps_any(live, 63 * MB, 80 * MB) && overlaps_any(live, 0, 1) &&
                   !overlaps_any(none, 0, 1),
               "overlaps_any edges");
        Cfg never = cfg;
        never.overlap_over = UINT64_MAX;
        r = plan(c, live, 250 * MB, 1000, never);
        expect(r.size() == 1 && r[0] == 2, "overlap_over UINT64_MAX: the overlapping ones never go");
    }
    {   // nothing to give back under the age, whatever the bytes
        std::vector<Cand> c = {mk(0, 16, 995)};
        expect(plan(c, none, 500 * MB, 1000, cfg).empty(), "a chunk younger than the age stays");
    }
    {   // a session: every 20 submissions a zone leaves a 16 MB retired chunk; the plan is applied to what it names
        Cfg s;
        s.limit = 512 * MB, s.overlap_over = 1024 * MB, s.max_n = 2, s.age = 600;
        std::vector<Cand> held;
        uint64_t bytes = 0, peak = 0, freed = 0;
        for (uint64_t t = 0; t < 400000; t++) {
            if (t % 20 == 0) held.push_back(mk((t / 20) * 16, 16, t)), bytes += 16 * MB;
            auto r = plan(held, none, bytes, t, s);
            for (size_t k = r.size(); k-- > 0;) {
                bytes -= held[r[k]].hi - held[r[k]].lo, freed += held[r[k]].hi - held[r[k]].lo;
                held.erase(held.begin() + (ptrdiff_t)r[k]);
            }
            if (bytes > peak) peak = bytes;
        }
        expect(peak <= s.limit + 16 * MB + 30 * 16 * MB, "retired bytes stay at the limit over a session");
        expect(freed > 20000 * 16 * MB - 2 * s.limit, "and the rest was given back");
    }
    {   // a zero limit: bounded by the age alone
        Cfg s;
        s.limit = 0, s.overlap_over = 0, s.max_n = 2, s.age = 100;
        std::vector<Cand> held;
        uint64_t bytes = 0, peak = 0;
        for (uint64_t t = 0; t < 20000; t++) {
            if (t % 20 == 0) held.push_back(mk((t / 20) * 16, 16, t)), bytes += 16 * MB;
            auto r = plan(held, none, bytes, t, s);
            for (size_t k = r.size(); k-- > 0;) bytes -= 16 * MB, held.erase(held.begin() + (ptrdiff_t)r[k]);
            if (bytes > peak) peak = bytes;
        }
        expect(peak <= 8 * 16 * MB, "a zero limit: no more than the age's worth is held");
    }
    if (failures) return 1;
    std::printf("retired_bound: ok\n");
    return 0;
}
