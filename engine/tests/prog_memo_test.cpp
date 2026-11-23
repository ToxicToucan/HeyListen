// Copyright (C) 2026 Sacrappy

// The program pick's validated-handle memo (gpu/vk/prog_memo.h) against a made-up world: texture slots whose type answer is a function
// of (slot, version), a variant that was translated for some types, and draws that bind other textures of the same or other types,
// some slots re-registered (the version moves) between draws. The memo may answer "valid" only where the descriptors read again would.
#include <cstdint>
#include <cstdio>
#include <random>
#include <vector>

#include "../gpu/vk/prog_memo.h"

using namespace mkwvk::progmemo;

namespace {
constexpr int kSlots = 64, kAnswers = 4;
struct World {
    uint32_t version[kSlots + 1]{};
    // the type a registration made: a function of (slot, version)
    static uint8_t type_of(uint32_t slot, uint32_t version) { return (uint8_t)(((slot * 2654435761u) ^ (version * 40503u)) >> 7) % 2; }
    uint8_t answer(uint32_t handle) const {
        if (!handle) return 0xFF;
        const uint32_t id = handle & kSlotMask;
        return id <= kSlots ? type_of(id, version[id]) : 0;
    }
};
uint8_t want[kAnswers] = {0, 1, 1, 0};   // (what the variant was translated for)

// the reference: the descriptors' handles read again (still_valid); an unbound answer slot takes any
bool reference(const World& w, const uint32_t handles[kAnswers]) {
    for (int i = 0; i < kAnswers; i++)
        if (handles[i] && w.answer(handles[i]) != want[i]) return false;
    return true;
}
void vector_of(const World& w, const uint32_t handles[kAnswers], std::vector<uint32_t>& out) {
    out.clear();
    for (int i = 0; i < kAnswers; i++) {
        const uint32_t word = slot_word(handles[i]);
        out.push_back(word);
        out.push_back(w.version[word & kSlotMask]);
    }
}
// still_valid_from: the same compare from the vector alone
bool from_vector(const World& w, const std::vector<uint32_t>& v) {
    for (int i = 0; i < kAnswers; i++) {
        const uint32_t h = handle_of(v[(size_t)i * 2]);
        if (h && w.answer(h) != want[i]) return false;
    }
    return true;
}
}  // namespace

int main() {
    std::mt19937_64 rng(0x9A11);
    World w;
    for (auto& v : w.version) v = (uint32_t)(rng() % 3);
    // a handful of texture sets the draws take turns with; the slot 0 with a handle word that is not 0 and some handles 0 (unbound)
    uint32_t sets[12][kAnswers];
    for (auto& s : sets)
        for (auto& h : s) {
            const uint64_t r = rng();
            h = r % 9 == 0 ? 0u : (r % 7 == 0 ? 0x100000u : (uint32_t)(1 + (r >> 8) % kSlots) | (uint32_t)((r >> 40) % 5) << 20);   // (upper bits: the sampler)
        }
    std::vector<uint32_t> handles, now;
    Set valid;
    bool have_last = false;
    uint64_t hits_same = 0, hits_memo = 0, worked = 0, invalid = 0, bad = 0, steps = 6000000;
    for (uint64_t step = 0; step < steps; step++) {
        const uint64_t r = rng();
        if (r % 19 == 0) w.version[1 + (r >> 8) % kSlots]++;   // (a registration: a new type answer for that slot)
        if (r % 400000 == 1) {                                   // (the variant changes: a new pick)
            valid.clear();
            have_last = false;
        }
        const uint32_t* h = sets[(r >> 20) % 3 == 0 ? (r >> 32) % 12 : (r >> 32) % 3];
        vector_of(w, h, now);
        const bool ref = reference(w, h);
        if (have_last && now == handles) {
            // the pick's own compare: the same slots and versions as the last validated answer
            hits_same++;
            if (!ref) bad++;
            continue;
        }
        bool ok;
        if (valid.has(now)) {
            ok = true;
            hits_memo++;
        } else {
            ok = from_vector(w, now);
            worked++;
            if (ok) valid.add(now);
            else invalid++;
        }
        if (ok != ref) bad++;
        if (ok) handles.swap(now), have_last = true;
    }
    std::printf("prog memo: %llu same, %llu memo, %llu worked out (%llu not valid), %llu mismatches\n", (unsigned long long)hits_same,
                (unsigned long long)hits_memo, (unsigned long long)worked, (unsigned long long)invalid, (unsigned long long)bad);
    if (bad || !hits_memo || !worked || !invalid) return 1;
    return 0;
}
