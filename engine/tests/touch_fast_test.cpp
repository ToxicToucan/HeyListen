// Copyright (C) 2026 Sacrappy
// gpu/vk/touch_fast.h (RX_VK_TOUCH_FAST) against the locked texture_touch's rules: every combination of the texture's state, the memory's
// answers and the segment order. The unlocked look may only answer Skip where the locked path would only note the look; everything it
// cannot decide goes to the lock. Built and run by tests/test_touch_fast.py.
#include <cstdio>

#include "../gpu/vk/touch_fast.h"

using namespace mkwvk::touch_fast;

namespace {
int failures = 0, cases = 0;

/// What the locked texture_touch (vk_texture.cpp) does on a first look of the submission, for the same inputs: true when it only notes
/// the look (no upload, no deferral bookkeeping, no other branch)
bool locked_only_notes(const In& in, bool dirty, bool gpu_dirty) {
    if (!in.for_sampling) return true;   // (texture_touch returns at once: texture_touch_quick answers those, never the unlocked look)
    if (in.deferred_before) return false;   // (defer_note_done)
    if (in.gpu_written) return false;       // (the GPU's branch: CPU writes over the GPU's, only_cleared)
    if (in.debug_reupload) return false;
    if (in.uploaded && in.upload_value == in.rec_value && in.upload_seg > in.seg) return false;   // (uploaded again here)
    if (!in.uploaded) return false;          // (a first upload)
    if (in.sparse || in.virtual_storage) return false;   // (the page walk writes the texture)
    return !dirty && !gpu_dirty;
}
}  // namespace

int main() {
    for (int bits = 0; bits < (1 << 11); bits++) {
        In in;
        in.for_sampling = bits & 1;
        in.uploaded = bits & 2;
        in.gpu_written = bits & 4;
        in.sparse = bits & 8;
        in.virtual_storage = bits & 16;
        in.deferred_before = bits & 32;
        in.debug_reupload = bits & 64;
        const bool dirty = bits & 128, gpu_dirty = bits & 256;
        const bool same_value = bits & 512, later_seg = bits & 1024;
        in.rec_value = 7, in.seg = 3;
        const uint64_t value = same_value ? 7 : 6, seg = later_seg ? 4 : 2;
        int dirty_asked = 0, read_order_calls = 0;
        const Verdict v = decide(
            in, [&] { dirty_asked++; return dirty; }, [&] { return gpu_dirty; },
            [&](In& o) { read_order_calls++, o.upload_value = value, o.upload_seg = seg; });
        In seen = in;
        seen.upload_value = value, seen.upload_seg = seg;
        const bool only_notes = locked_only_notes(seen, dirty, gpu_dirty);
        cases++;
        if (v == Verdict::Skip && !only_notes) {
            failures++;
            if (failures < 20) std::printf("bits %#x: the unlocked look skips a look the locked path would act on\n", bits);
        }
        // (and it decides every case it can: an unchanged sampled texture of this kind is not sent to the lock)
        if (v != Verdict::Skip && only_notes && in.for_sampling) {
            failures++;
            if (failures < 20) std::printf("bits %#x: an unchanged texture sent to the lock\n", bits);
        }
        // (the segment order is read after the memory, and only when the memory said nothing: texture_touch stores it before upload_gen)
        if (read_order_calls && (dirty || gpu_dirty)) {
            failures++;
            if (failures < 20) std::printf("bits %#x: the segment order read for a dirty texture\n", bits);
        }
        if (v == Verdict::LockedLaterSeg && !(same_value && later_seg)) {
            failures++;
            if (failures < 20) std::printf("bits %#x: a later segment named without one\n", bits);
        }
        if (precheck(in) != Verdict::Skip && dirty_asked) {
            failures++;
            if (failures < 20) std::printf("bits %#x: memory looked at for a case the precheck sends to the lock\n", bits);
        }
    }
    std::printf("touch_fast: %d cases, %d failures\n", cases, failures);
    return failures ? 1 : 0;
}
