// HL_EXPO_GPU: the exposure meter's sums, handed to the game's CPU from an engine-owned ring instead of from the GPU's write-back
// (docs/native-engine/exposure.md). The pure rules, with no Vulkan in them, so a small program can test them (tests/test_expo_feed.py):
// - a block (the 0x1040 range one of the meter's compute programs writes) has a ring of kRing slots; each dispatch recorded gets the
//   next sequence number s (1, 2, ...) and the slot s % kRing; the GPU fills the slot's four sum words, then its stamp with s;
// - the read that comes from the game's CPU wants the sums of sequence `newest - (lag - 1)`: lag 1 is the newest dispatch recorded
//   (what the old path waits for), lag 2 the one before (finished a frame earlier, so nothing waits);
// - a slot whose stamp is not the wanted sequence is not there yet (or was overwritten); the read waits a bounded time, then takes the
//   newest older stamp, then zeros.
#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace expo_feed {

constexpr unsigned kRing = 16;          // slots a block
constexpr unsigned kBlocks = 8;         // blocks (the game has three rings; the Market's programs write one)
constexpr unsigned kSlotBytes = 32;     // 16 bytes of sums, the stamp, padding
constexpr unsigned kStampOffset = 16;

/// HL_EXPO_GPU: 0 off, 1 on, 2 check (the old path runs, the ring is compared); HL_EXPO_LAG: 1 .. kRing - 2, default 2
struct Config {
    int mode = 0;
    unsigned lag = 2;
};
inline Config parse(const char* mode, const char* lag) {
    Config c;
    if (mode && *mode) c.mode = atoi(mode) < 0 ? 0 : (atoi(mode) > 2 ? 2 : atoi(mode));
    if (lag && *lag) {
        const long v = atol(lag);
        c.lag = v < 1 ? 1u : (v > (long)kRing - 2 ? kRing - 2 : (unsigned)v);
    }
    return c;
}

/// The sequence a read wants when `newest` dispatches have been recorded: 0 when there is none yet
inline uint32_t wanted(uint32_t newest, unsigned lag) { return newest >= lag ? newest - (lag - 1) : 0; }
inline unsigned slot_of(uint32_t seq) { return seq % kRing; }

/// The slot of the newest stamp older than `want` (and not older than the ring holds), -1 when there is none. `stamps` are the
/// kRing stamps as the GPU left them
inline int newest_older(const uint32_t* stamps, uint32_t want) {
    int best = -1;
    uint32_t at = 0;
    for (unsigned i = 0; i < kRing; i++)
        if (stamps[i] && stamps[i] < want && stamps[i] + kRing > want && stamps[i] > at) best = (int)i, at = stamps[i];
    return best;
}

/// The blocks: guest address -> index, and each one's count of dispatches recorded
struct Ledger {
    uint64_t guest[kBlocks] = {};
    uint32_t recorded[kBlocks] = {};
    unsigned used = 0;
    int find(uint64_t g) const {
        for (unsigned i = 0; i < used; i++)
            if (guest[i] == g) return (int)i;
        return -1;
    }
    int add(uint64_t g) {   // -1 when the table is full
        if (const int i = find(g); i >= 0) return i;
        if (used == kBlocks) return -1;
        guest[used] = g;
        return (int)used++;
    }
    uint32_t issue(int block) { return ++recorded[block]; }
};

}   // namespace expo_feed
