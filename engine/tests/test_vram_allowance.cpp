// Copyright (C) 2026 Sacrappy

#include "../gpu/vk/vram_allowance.h"
#include <cstdio>
using vram_allowance::past_allowance;
static int bad;
#define CHECK(x) do { if (!(x)) { std::printf("FAIL: %s\n", #x); bad++; } } while (0)
int main() {
    const uint64_t MB = 1ull << 20;
    CHECK(past_allowance(5940 * MB, 5940 * MB) == 5940 * MB - 5940 * MB / 10);
    CHECK(past_allowance(6600 * MB, 6600 * MB + 8 * MB) < 6600 * MB);
    CHECK(past_allowance(6600 * MB + 8 * MB, 6600 * MB) < 6600 * MB);
    CHECK(past_allowance(5300 * MB, 6500 * MB) == 6500 * MB);
    CHECK(past_allowance(7602 * MB, 6982 * MB) == 6982 * MB);
    CHECK(past_allowance(5000 * MB, 0) == 0);
    CHECK(past_allowance(0, 0) == 0);
    if (bad) return 1;
    std::printf("PASS: the allowance rule\n");
    return 0;
}
