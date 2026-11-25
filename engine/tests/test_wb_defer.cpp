// Copyright (C) 2026 Sacrappy

// gpu/vk/wb_defer.h: the rules of the write-back put off to the frame's end (RX_VK_WB_LAZY). Built and run by test_wb_defer.py.
#include "../gpu/vk/wb_defer.h"
#include <cstdio>
#include <initializer_list>

static int fails = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

int main() {
    using namespace wb_defer;
    // the gate: forced on or off by RX_VK_WB_LAZY, else by the measured link
    CHECK(on("1", 53.0, 9.0));         // (forced on on a fast link)
    CHECK(!on("0", 6.6, 9.0));         // (forced off on the 3050's link)
    CHECK(on(nullptr, 6.6, 9.0));      // (auto: 6.6 GB/s from VRAM is slow)
    CHECK(!on(nullptr, 53.0, 9.0));    // (auto: the 5070's link is fast)
    CHECK(!on(nullptr, 9.0, 9.0));     // (auto: the line itself is not slow)
    CHECK(on(nullptr, 8.99, 9.0));
    CHECK(!on(nullptr, 0.0, 9.0));     // (auto: not measured, taken as fast)
    CHECK(on("", 6.6, 9.0));           // (an empty value is unset)
    CHECK(!on("", 53.0, 9.0));
    CHECK(on("2", 53.0, 9.0));         // (any non-zero number is on)
    CHECK(line(nullptr) == 9.0);
    CHECK(line("") == 9.0);
    CHECK(line("12.5") == 12.5);
    CHECK(line("0") == 9.0);           // (not a line: the default)
    CHECK(line("-3") == 9.0);
    // the run
    const uint64_t kMin = 128u << 10;
    CHECK(run_eligible(0, 1u << 20, kMin, 0, 256, false));
    CHECK(!run_eligible(2, 1u << 20, kMin, 0, 256, false));         // (not whole words)
    CHECK(!run_eligible(0, (1u << 20) + 2, kMin, 0, 256, false));
    CHECK(!run_eligible(0, kMin - 4, kMin, 0, 31, false));          // (under the minimum)
    CHECK(!run_eligible(0, 1u << 20, kMin, 5, 5, false));           // (no whole page)
    CHECK(!run_eligible(0, 1u << 20, kMin, 0, 256, true));          // (a texture's storage)
    // a page
    CHECK(page_may_wait(true, false, false, false, 200, 120));
    CHECK(page_may_wait(true, false, false, false, 120, 120));      // (exactly quiet long)
    CHECK(!page_may_wait(true, false, false, false, 119, 120));     // (the CPU wrote it lately)
    CHECK(!page_may_wait(false, false, false, false, 500, 120));    // (the shadow does not know it)
    CHECK(!page_may_wait(true, true, false, false, 500, 120));      // (shared with the CPU or a texture)
    CHECK(!page_may_wait(true, false, true, false, 500, 120));      // (the CPU reads it back)
    CHECK(!page_may_wait(true, false, false, true, 500, 120));      // (its turn in the flush)
    CHECK(page_may_wait(true, false, false, false, 0, 0));          // (RX_VK_WB_LAZY_QUIET=0)
    // the flush's share: every word once in `period` presents
    for (uint64_t period_n : {1ull, 2ull, 30ull}) {
        for (uint64_t w = 0; w < 100; w++) {
            int times = 0;
            for (uint64_t phase = 0; phase < period_n; phase++) times += word_in_share(w, period_n, phase);
            CHECK(times == 1);
        }
    }
    CHECK(word_in_share(7, 1, 0));
    CHECK(word_in_share(7, 0, 0));
    CHECK(word_in_share(31, 30, 1));
    CHECK(!word_in_share(31, 30, 0));
    // the switches' values
    CHECK(period(nullptr) == 30);
    CHECK(period("") == 30);
    CHECK(period("1") == 1);
    CHECK(period("0") == 1);
    CHECK(period("-5") == 1);
    CHECK(period("12") == 12);
    CHECK(quiet(nullptr) == 120);
    CHECK(quiet("0") == 0);
    CHECK(quiet("-1") == 0);
    CHECK(quiet("300") == 300);
    CHECK(min_run_bytes(nullptr) == (128u << 10));
    CHECK(min_run_bytes("2") == (8u << 10));
    CHECK(min_run_bytes("512") == (512u << 10));
    CHECK(!hot_enough(7, 8));
    CHECK(hot_enough(8, 8));
    CHECK(hot_enough(255, 8));
    CHECK(hot_enough(1, 1));
    CHECK(hot_frames(nullptr) == 1);
    CHECK(hot_frames("0") == 1);
    CHECK(hot_frames("30") == 30);
    CHECK(region_bytes(nullptr) == (4096ull << 10));
    CHECK(region_bytes("4") == (4ull << 10));
    CHECK(region_bytes("1000") == (1024ull << 10));
    CHECK(region_bytes("0") == (4ull << 10));
    CHECK(region_base(0x103d443010, 4ull << 20) == 0x103d400000);
    CHECK(region_base(0x1000, 4096) == 0x1000);
    if (fails) std::printf("%d checks failed\n", fails);
    else std::printf("wb defer: all cases right\n");
    return fails ? 1 : 0;
}
