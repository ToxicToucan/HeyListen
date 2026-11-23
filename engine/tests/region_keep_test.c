// Copyright (C) 2026 Sacrappy

/* Game-free test of host/hos_regionkeep.c (tests/test_region_keep.py builds it with HK_TEST; Windows only): a freed region is kept
   resident with its bytes, a region allocated over it reads as zero (the whole, a part, with the rest kept), a kept region that has
   aged is discarded by the thread, the cap discards the oldest, a region with under 1 MB resident is not kept. Exits non-zero on a
   failure. HL_REGION_KEEP=2 HL_REGION_KEEP_MB=12 HL_REGION_KEEP_LOWMEM_MB=0 are set here before the first call. */
#include "hk_test_stubs.h"
#include <stdlib.h>
#include <string.h>

uint8_t *hk_base;
int hos_keep_enabled(void);
int hos_keep_try(uint64_t addr, uint64_t size);
void hos_keep_reuse(uint64_t lo, uint64_t hi);
void hos_keep_discard(uint64_t addr, uint64_t size);

static int failures;
#define CHECK(cond, ...) do { if (!(cond)) { failures++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); putchar('\n'); } } while (0)

static int discards;
static uint64_t discarded_lo[64], discarded_size[64];
void hos_keep_discard(uint64_t addr, uint64_t size) {
    if (discards < 64) discarded_lo[discards] = addr, discarded_size[discards] = size;
    discards++;
    /* (what the engine does: pages read as zero afterwards) */
    memset(GP(addr), 0, (size_t)size);
}

#define MB (1ULL << 20)
static void fill(uint64_t a, uint64_t n, uint8_t v) { memset(GP(a), v, (size_t)n); }
static int all(uint64_t a, uint64_t n, uint8_t v) {
    const uint8_t *p = (const uint8_t *)GP(a);
    for (uint64_t i = 0; i < n; i += 4096) if (p[i] != v) return 0;
    return p[n - 1] == v;
}

int main(void) {
    _putenv("HL_REGION_KEEP=2");
    _putenv("HL_REGION_KEEP_MB=12");
    _putenv("HL_REGION_KEEP_LOWMEM_MB=0");
    hk_base = (uint8_t *)VirtualAlloc(NULL, 128 * MB, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    CHECK(hk_base != NULL, "no window");
    CHECK(hos_keep_enabled() == 1, "not enabled by HL_REGION_KEEP=2");

    /* 1: an 8 MB region, resident, freed: kept with its bytes */
    fill(0, 8 * MB, 0xAB);
    CHECK(hos_keep_try(0, 8 * MB) == 1, "a resident 8 MB region was not kept");
    CHECK(all(0, 8 * MB, 0xAB), "the kept region lost its bytes");

    /* 2: a new region over its middle 2 MB: zero there, the two ends still kept and untouched */
    hos_keep_reuse(2 * MB, 4 * MB);
    CHECK(all(2 * MB, 2 * MB, 0), "the reused part is not zero");
    CHECK(all(0, 2 * MB, 0xAB) && all(4 * MB, 4 * MB, 0xAB), "the ends of the kept region changed");

    /* 3: a region over everything that is left: all zero, nothing kept (a second use of the same ranges finds nothing to do) */
    hos_keep_reuse(0, 8 * MB);
    CHECK(all(0, 8 * MB, 0), "the whole region is not zero");
    fill(0, 8 * MB, 0xCD);   /* (the game wrote in its new region) */
    hos_keep_reuse(0, 8 * MB);
    CHECK(all(0, 8 * MB, 0xCD), "a live region was zeroed again (it was still on the list)");

    /* 4: under 1 MB resident: not kept */
    fill(16 * MB, 512 * 1024, 0x11);
    CHECK(hos_keep_try(16 * MB, 8 * MB) == 0, "a region with 0.5 MB resident was kept");

    /* 5: aged: HL_REGION_KEEP=2 s, the thread looks once a second */
    discards = 0;
    fill(24 * MB, 4 * MB, 0x22);
    CHECK(hos_keep_try(24 * MB, 4 * MB) == 1, "the 4 MB region was not kept");
    Sleep(1000);
    CHECK(discards == 0, "discarded before it aged (%d)", discards);
    Sleep(3500);
    CHECK(discards == 1 && discarded_lo[0] == 24 * MB && discarded_size[0] == 4 * MB, "the aged region was not discarded: %d discards", discards);

    /* 6: the cap (12 MB): three regions of 5 MB: the oldest goes at the third */
    discards = 0;
    fill(32 * MB, 5 * MB, 0x33);
    fill(40 * MB, 5 * MB, 0x44);
    fill(48 * MB, 5 * MB, 0x55);
    CHECK(hos_keep_try(32 * MB, 5 * MB) == 1 && hos_keep_try(40 * MB, 5 * MB) == 1, "the first two were not kept");
    CHECK(discards == 0, "discarded under the cap");
    CHECK(hos_keep_try(48 * MB, 5 * MB) == 1, "the third was not kept");
    CHECK(discards == 1 && discarded_lo[0] == 32 * MB, "the cap did not discard the oldest: %d, at %llu MB", discards, (unsigned long long)(discarded_lo[0] / MB));
    CHECK(all(40 * MB, 5 * MB, 0x44) && all(48 * MB, 5 * MB, 0x55), "a kept region changed");
    /* (a reuse of the second: zero, and the third stays) */
    hos_keep_reuse(40 * MB, 45 * MB);
    CHECK(all(40 * MB, 5 * MB, 0), "reused region not zero");
    CHECK(all(48 * MB, 5 * MB, 0x55), "the other kept region changed");

    if (failures) { printf("%d failures\n", failures); return 1; }
    printf("PASS: HL_REGION_KEEP keeps, zeroes on reuse (whole, part), ages out, caps, skips under 1 MB\n");
    return 0;
}
