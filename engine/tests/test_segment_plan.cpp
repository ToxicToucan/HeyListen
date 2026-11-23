// Copyright (C) 2026 Sacrappy

#include "../gpu/vk/segment_plan.h"

#include <cstdio>

#define CHECK(name, condition) do { if (!(condition)) { std::fprintf(stderr, "FAIL %s at line %d\n", name, __LINE__); return 1; } } while (0)

int main() {
    using mkwvk::segment_plan::recorders;
    using mkwvk::segment_plan::rounds;
    using mkwvk::segment_plan::segment_count;
    using mkwvk::segment_plan::with_mid;
    const int kMaxBatch = 6;

    CHECK("4 workers are 4 recorders", recorders(4, false) == 4);
    CHECK("the replay thread is one more", recorders(4, true) == 5);
    CHECK("no workers", recorders(0, false) == 0 && recorders(-1, true) == 1);

    // (the base: one segment per 160, at most kMaxBatch, whatever the recorders)
    CHECK("base: 830 draws are 5 segments", segment_count(830, 160, 4, kMaxBatch, false) == 5);
    CHECK("base: 1200 draws are capped at 6", segment_count(1200, 160, 4, kMaxBatch, false) == 6);
    CHECK("base: below 2 x per is one segment", segment_count(319, 160, 4, kMaxBatch, false) == 1);
    CHECK("base: zero work is still one segment", segment_count(0, 160, 4, kMaxBatch, false) == 1);

    // (RX_VK_PARALLEL_ROUNDS: at most the recorders)
    CHECK("one round: 830 draws on 4 workers are 4", segment_count(830, 160, 4, kMaxBatch, true) == 4);
    CHECK("one round + self: 830 on 5 recorders are 5", segment_count(830, 160, recorders(4, true), kMaxBatch, true) == 5);
    CHECK("one round + self: 1200 on 5 recorders are 5", segment_count(1200, 160, 5, kMaxBatch, true) == 5);
    CHECK("one round: fewer draws than recorders x per keep their count", segment_count(500, 160, 4, kMaxBatch, true) == 3);
    CHECK("one round: below 2 x per is one segment", segment_count(319, 160, 5, kMaxBatch, true) == 1);
    CHECK("one round: the cap below the recorders wins", segment_count(2000, 160, 8, kMaxBatch, true) == kMaxBatch);
    CHECK("one round: a cap of 2", segment_count(2000, 160, 5, 2, true) == 2);
    CHECK("one round with no recorders: the cap alone", segment_count(2000, 160, 0, kMaxBatch, true) == kMaxBatch);

    // (RX_VK_PARALLEL_SEG: another segment size)
    CHECK("per 32 (the floor)", segment_count(830, 32, 4, kMaxBatch, false) == kMaxBatch && segment_count(830, 32, 4, kMaxBatch, true) == 4);
    CHECK("per 640: Kokiri's ~750 is one segment", segment_count(750, 640, 4, kMaxBatch, true) == 1);
    CHECK("a bad per or cap is not a division by zero", segment_count(830, 0, 4, kMaxBatch, false) == kMaxBatch &&
                                                          segment_count(830, 160, 4, 0, false) == 1);

    // (the rounds, a diagnosis)
    CHECK("5 segments on 4 are 2 rounds", rounds(5, 4) == 2);
    CHECK("4 on 4 are 1", rounds(4, 4) == 1);
    CHECK("5 on 5 are 1", rounds(5, 5) == 1);
    CHECK("6 on 4 are 2", rounds(6, 4) == 2);
    CHECK("none are 0", rounds(0, 4) == 0);
    // (RX_VK_PARALLEL_BALANCE: the boundaries by recording time)
    using mkwvk::segment_plan::balanced_targets;
    using mkwvk::segment_plan::Profile;
    using mkwvk::segment_plan::profile_add;
    {
        Profile p;
        int64_t out[5] = {};
        balanced_targets(p, 4, 800, out);
        CHECK("no profile yet: even counts", out[0] == 200 && out[1] == 400 && out[2] == 600);
        // (an even cost: even boundaries)
        const double f1[4] = {0, .25, .5, .75}, t1[4] = {.25, .5, .75, 1}, m1[4] = {1, 1, 1, 1};
        profile_add(p, f1, t1, m1, 4, 0.25);
        balanced_targets(p, 4, 800, out);
        CHECK("an even cost: even boundaries", out[0] == 200 && out[1] == 400 && out[2] == 600);
        // (the first half costs three times the second: a sample taken whole the first time, blended after)
        Profile q;
        const double f2[2] = {0, .5}, t2[2] = {.5, 1}, m2[2] = {3, 1};
        profile_add(q, f2, t2, m2, 2, 0.25);
        balanced_targets(q, 2, 800, out);
        CHECK("front-loaded: the boundary moves forward", out[0] > 250 && out[0] < 290);   // (2/3 of the first half: 267)
        balanced_targets(q, 4, 800, out);
        CHECK("front-loaded, 4 parts: equal time", out[0] > 120 && out[0] < 147 && out[1] > 250 && out[1] < 290 && out[2] > 380 && out[2] < 420);
        profile_add(q, f1, t1, m1, 4, 0.25);   // (then an even frame: moves a quarter of the way back)
        balanced_targets(q, 2, 800, out);
        CHECK("blended: between", out[0] > 267 && out[0] < 400);
        CHECK("boundaries in order", out[0] > 0 && out[0] < 800);
        // (bad samples change nothing)
        Profile r;
        const double fb[1] = {.5}, tb[1] = {.5}, mb[1] = {5};
        profile_add(r, fb, tb, mb, 1, 0.25);
        CHECK("an empty range is no sample", !r.valid);
        balanced_targets(r, 3, 900, out);
        CHECK("still even", out[0] == 300 && out[1] == 600);
        // (RX_VK_SEG_TAIL: the last part shorter)
        Profile e;
        balanced_targets(e, 4, 700, out, 0.5);   // (3.5 parts: 200 each, the last 100)
        CHECK("tail 0.5, no profile: the last part is half", out[0] == 200 && out[1] == 400 && out[2] == 600);
        balanced_targets(p, 4, 800, out, 1.0);
        CHECK("tail 1 is the default", out[0] == 200 && out[1] == 400 && out[2] == 600);
        balanced_targets(p, 4, 700, out, 0.5);
        CHECK("tail 0.5, even cost: the last part is half", out[0] > 195 && out[0] < 205 && out[2] > 595 && out[2] < 605);
        balanced_targets(p, 2, 800, out, 0.25);
        CHECK("tail 0.25 of two parts: the boundary at 4/5", out[0] > 635 && out[0] < 645);
        balanced_targets(p, 3, 800, out, 100.0);
        CHECK("a tail above 1 is 1", out[0] > 265 && out[0] < 270);
    }
    // (stream SMALL1, RX_VK_MID_SEG: a command buffer under two segments' worth, from the given work on, is two segments)
    CHECK("mid: off (0) leaves one segment", with_mid(segment_count(200, 128, 4, 10, false), 200, 10, 0) == 1);
    CHECK("mid: 105 of a threshold of 100 is two", with_mid(segment_count(105, 128, 4, 10, false), 105, 10, 100) == 2);
    CHECK("mid: 99 of a threshold of 100 stays one", with_mid(segment_count(99, 128, 4, 10, false), 99, 10, 100) == 1);
    CHECK("mid: already more than one is unchanged", with_mid(segment_count(830, 128, 4, 10, false), 830, 10, 100) == segment_count(830, 128, 4, 10, false));
    CHECK("mid: a cap of 1 keeps one", with_mid(1, 500, 1, 100) == 1);
    CHECK("mid: never fewer than before", with_mid(3, 10, 6, 100) == 3);
    std::puts("segment plan: all checks passed");
}
