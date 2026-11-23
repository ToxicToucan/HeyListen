// Copyright (C) 2026 Sacrappy

// evict_reason (gpu/vk/evict_reason.h): when the eviction pass leaves a texture's write-back out.
// tests/test_evict_reason.py
#include "../gpu/vk/evict_reason.h"

#include <cstdio>

static int failures = 0;
static void expect(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        failures++;
    }
}

int main() {
    using namespace evict_reason;
    // (OoT's Hyrule Field at a 6 GB budget: a 1920 x 1080 target last drawn at frame 3804, a HUD label drawn inside it at 3987)
    expect(write_back_stale(3804, 3987), "another image drawn into its storage since: the write-back would put old texels over it");
    expect(!write_back_stale(3804, 0), "nothing else drawn there: written back");
    expect(!write_back_stale(3804, 1468), "the other image drawn before it: written back");
    expect(!write_back_stale(3804, 3804), "both drawn in the same frame: written back, as before");
    if (failures) return 1;
    std::printf("PASS: evict_reason\n");
    return 0;
}
