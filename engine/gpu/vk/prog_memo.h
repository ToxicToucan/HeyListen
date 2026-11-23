// Copyright (C) 2026 Sacrappy

// (HeyListen, HL_PROG_MEMO) The program pick's validated-handle memo. A recording thread's pick for one (program, vertex inputs, colour
// outputs) key is a variant, and the variant stands while the textures bound to its answers' slots have the types it was translated
// for. The pick used to keep one handle vector, the last one it validated: a draw whose textures were other ones of the same types
// (31% of the picks on the PC 3050, kokiri-idle) read the descriptors' handles for the compare, then again in `still_valid`, and
// swapped the vector in, so that the draw after it, with the first textures again, did the same. The memo keeps the last few
// vectors that were validated for the variant and answers from them, and the validity of a vector that is not there is worked out
// from the vector itself: one pass over the descriptors for both.
//
// A vector is, for each answer of the variant, the texture slot (20 bits, bit 31 says the handle was not 0) and the slot's version
// (bumped at every registration and release): what the type answer is a function of (`texture_answer`), so equal vectors are the
// same answer. No Vulkan, no NvnState: tests/prog_memo_test.cpp drives it with a made-up world.
#pragma once
#include <cstdint>
#include <vector>

namespace mkwvk::progmemo {

constexpr int kWays = 4;
constexpr uint32_t kNonZero = 0x80000000u;   // (the handle was not 0: an unbound slot takes any type, a bound slot 0 is a real one)
constexpr uint32_t kSlotMask = 0xFFFFFu;

/// The word of one answer's slot in a vector
inline uint32_t slot_word(uint32_t handle) { return (handle & kSlotMask) | (handle ? kNonZero : 0); }
/// A handle that stands for the slot word where the answer is worked out (only the slot decides it, and 0 is told from the rest)
inline uint32_t handle_of(uint32_t word) { return (word & kNonZero) ? ((word & kSlotMask) ? (word & kSlotMask) : 0x100000u) : 0u; }

struct Set {
    std::vector<uint32_t> v[kWays];
    uint8_t n = 0, next = 0;
    bool has(const std::vector<uint32_t>& now) const {
        for (int i = 0; i < n; i++)
            if (v[i] == now) return true;
        return false;
    }
    /// Remembers a vector the variant was validated for (the oldest goes when full)
    void add(const std::vector<uint32_t>& now) {
        if (has(now)) return;
        v[next] = now;
        next = (uint8_t)((next + 1) % kWays);
        if (n < kWays) n++;
    }
    void clear() {
        for (auto& x : v) x.clear();
        n = next = 0;
    }
};

}  // namespace mkwvk::progmemo
