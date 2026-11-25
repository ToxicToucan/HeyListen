// Copyright (C) 2026 Sacrappy

// (CRASH3) gpu/vk/wb_guess.h: the checked write-back of a guessed range (a bare storage entry's 64-byte head) writes the words
// the GPU changed and never goes over the CPU's writes. Built and run by test_wb_guess.py.
#include "../gpu/vk/wb_guess.h"
#include <cstdio>
#include <cstring>
#include <utility>
#include <vector>

static int fails = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

using Spans = std::vector<std::pair<uint64_t, uint64_t>>;
static uint64_t cpu_left;
static Spans spans_of(const uint8_t* guest, const uint8_t* src, const uint8_t* snap, const uint8_t* known, uint64_t n) {
    Spans s;
    cpu_left = wb_guess::gpu_words(guest, src, snap, known, n, [&](uint64_t at, uint64_t len) { s.push_back({at, len}); });
    return s;
}
/// the write-back of the head into guest: whole (as before), or checked
static void write_back(uint8_t* guest, const uint8_t* mirror, const uint8_t* snap, const uint8_t* known, uint64_t n, bool checked) {
    if (!checked) {
        std::memcpy(guest, mirror, n);
        return;
    }
    wb_guess::gpu_words(guest, mirror, snap, known, n, [&](uint64_t at, uint64_t len) { std::memcpy(guest + at, mirror + at, len); });
}

int main() {
    uint8_t g[64], m[64], snap[64], known[64];
    for (int k = 0; k < 64; k++) g[k] = m[k] = snap[k] = (uint8_t)k, known[k] = 1;
    CHECK(spans_of(g, m, snap, known, 64).empty() && cpu_left == 0);   // (nothing the GPU wrote: nothing written)
    m[8] = 0xaa, m[13] = 0xbb, m[40] = 0xcc;   // (the GPU's words: 8..11, 12..15, 40..43)
    CHECK((spans_of(g, m, snap, known, 64) == Spans{{8, 8}, {40, 4}}));
    g[41] = 0x77;   // (the CPU wrote word 40 too since the binding: left to the CPU)
    CHECK((spans_of(g, m, snap, known, 64) == Spans{{8, 8}}) && cpu_left == 1);
    known[9] = 0;   // (a byte no binding's snapshot took: its word never written)
    CHECK((spans_of(g, m, snap, known, 64) == Spans{{12, 4}}));
    known[9] = 1, g[41] = 41;
    uint8_t t1[6] = {1, 2, 3, 4, 5, 6}, t2[6] = {1, 2, 3, 4, 5, 9}, k6[6] = {1, 1, 1, 1, 1, 1};
    CHECK((spans_of(t1, t2, t1, k6, 6) == Spans{{4, 2}}));   // (a tail shorter than a word, as one)

    // The crash (CRASH3's watch, c3-base): a stale table entry points into the game's heap. At the binding the 64 bytes are
    // a block of 0x90 bytes straddling them (the snapshot, what the mirror's sync read); the program does not write through
    // the entry; the CPU then frees the block and allocates another, writing its chunk header at +0x20, before the write-back
    uint8_t heap[64], mirror[64], hsnap[64], hknown[64];
    for (int k = 0; k < 64; k++) heap[k] = (uint8_t)(0x40 + k), hknown[k] = 1;
    std::memcpy(hsnap, heap, 64);
    std::memcpy(mirror, heap, 64);   // (the GPU wrote nothing)
    const uint8_t header[8] = {0x28, 0, 0, 0, 0x11, 0x22, 0x33, 0x44};
    std::memcpy(heap + 0x20, header, 8);
    uint8_t before[64], old_way[64];
    std::memcpy(before, heap, 64);
    std::memcpy(old_way, heap, 64);
    write_back(old_way, mirror, hsnap, hknown, 64, false);
    CHECK(std::memcmp(old_way + 0x20, header, 8) != 0);   // (as before: the header reverted, the heap corrupt)
    write_back(heap, mirror, hsnap, hknown, 64, true);
    CHECK(!std::memcmp(heap, before, 64));                // (checked: guest memory as the CPU left it)

    // A real result: the GPU wrote a count at +4 while the CPU wrote +0x30 on the same page: the count reaches guest memory,
    // the CPU's word stays
    uint8_t g2[64], m2[64], s2[64];
    for (int k = 0; k < 64; k++) g2[k] = m2[k] = s2[k] = (uint8_t)(0x80 + k);
    m2[4] = 7;
    g2[0x30] = 0x55;
    write_back(g2, m2, s2, hknown, 64, true);
    CHECK(g2[4] == 7 && g2[0x30] == 0x55);

    if (fails) std::printf("%d checks failed\n", fails);
    else std::printf("wb guess: all cases right\n");
    return fails ? 1 : 0;
}
