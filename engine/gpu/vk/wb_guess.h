// Copyright (C) 2026 Sacrappy

// (CRASH3) The write-back of a guessed written range (vk_memory.cpp, RX_VK_WB_GUESS_CHECKED): a bare storage-table entry's
// first 64 bytes, which vk_draw.cpp marks written though the program may never write through that entry. It was copied whole
// from the mirror into guest memory; when the entry was a stale pointer into memory the game's heap had given to CPU objects,
// the copy put back the bytes the mirror's sync had read over the CPU's newer writes (the module jobs' destroy-path crashes of
// v0.4.3). Now guest memory is read when the range is bound (the snapshot: what the mirror's sync took), the range is staged,
// and once its submission is done the CPU writes only the words the GPU changed (the mirror's differ from the snapshot) that
// the CPU did not (guest memory still equals the snapshot). tests/test_wb_guess.py
#pragma once
#include <cstdint>
#include <cstring>

namespace wb_guess {

/// The spans of [0, n) to write back, by 4-byte words (the GPU's unit; a tail of fewer bytes as one word), in order and
/// merged: emit(offset, bytes). A word is written when every byte of it is known in the snapshot (known[i] != 0), guest
/// memory still holds the snapshot there (the CPU did not write it since the binding) and the mirror's word differs from it
/// (the GPU wrote it). Returns the words left alone because the CPU wrote them.
template <class F> uint64_t gpu_words(const uint8_t* guest, const uint8_t* src, const uint8_t* snap, const uint8_t* known,
                                      uint64_t n, F&& emit) {
    uint64_t cpu = 0, span_at = 0, span_n = 0;
    for (uint64_t w = 0; w < n;) {
        const uint64_t k = n - w < 4 ? n - w : 4;
        bool all_known = true;
        for (uint64_t i = 0; i < k; i++) all_known = all_known && known[w + i];
        const bool gpu = all_known && std::memcmp(src + w, snap + w, (size_t)k) != 0;
        const bool cpu_wrote = !all_known || std::memcmp(guest + w, snap + w, (size_t)k) != 0;
        if (gpu && cpu_wrote) cpu++;
        if (gpu && !cpu_wrote) {
            if (span_n && span_at + span_n == w) span_n += k;
            else {
                if (span_n) emit(span_at, span_n);
                span_at = w, span_n = k;
            }
        }
        w += k;
    }
    if (span_n) emit(span_at, span_n);
    return cpu;
}

}  // namespace wb_guess
