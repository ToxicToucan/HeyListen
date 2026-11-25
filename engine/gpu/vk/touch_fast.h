// Copyright (C) 2026 Sacrappy
// (stream PIPE1) RX_VK_TOUCH_FAST: a texture's first look of a submission (texture_touch) without the parallel replay's ParLock when
// all it would do is note the look. The decision alone lives here, free of the renderer's types, so tests/touch_fast_test.cpp can
// drive it against the locked path's rules (vk_texture.cpp texture_touch_unlocked applies it).
#pragma once

#include <cstdint>

namespace mkwvk::touch_fast {

/// What the unlocked look reads of a texture and of the recording thread (each field read once, in this order where it matters:
/// upload_gen first, then upload_value / upload_seg, see texture_touch_unlocked)
struct In {
    bool for_sampling = true;
    bool uploaded = false;       // (uploaded from guest memory as of upload_gen)
    bool gpu_written = false;    // (the GPU drew, copied or stored into it: texture_touch's other branch)
    bool sparse = false;         // (a sparse image: its page walk)
    bool virtual_storage = false;  // (storage at or above 1 << 40: the page map's walk, which writes the texture)
    bool deferred_before = false;  // (defer_frame: held back by the upload budget once; the locked path notes it coming in)
    bool debug_reupload = false;   // (RX_VK_REUPLOAD_DIM: a debugging upload every frame)
    uint64_t upload_value = 0, upload_seg = 0;   // (the submission and segment its last upload was recorded in)
    uint64_t rec_value = 0, seg = 0;             // (this submission, this thread's segment)
};

enum class Verdict {
    Skip,            // nothing to do: the locked path would only note the look (its contents are as uploaded)
    Locked,          // a case the unlocked look does not decide: the locked texture_touch
    LockedLaterSeg,  // uploaded in this submission by a segment after this one: the locked path uploads it again here
    LockedDirty,     // the CPU wrote its storage since the upload, or a GPU write landed in it: the locked path uploads
};

/// The cases before any memory is looked at. Skip here means "look at the memory next" (dirty() and gpu_dirty())
inline Verdict precheck(const In& in) {
    if (!in.for_sampling) return Verdict::Locked;   // (texture_touch_quick answers those: never here)
    if (!in.uploaded || in.gpu_written || in.sparse || in.virtual_storage || in.deferred_before || in.debug_reupload)
        return Verdict::Locked;
    return Verdict::Skip;
}

/// The whole decision: precheck, then the memory (dirty: the CPU wrote its storage since upload_gen; gpu_dirty: a GPU write into it
/// not yet taken), then the segment order, read last (a concurrent upload stores upload_value / upload_seg before upload_gen)
template <class Dirty, class GpuDirty, class ReadOrder>
Verdict decide(In in, Dirty&& dirty, GpuDirty&& gpu_dirty, ReadOrder&& read_order) {
    const Verdict v = precheck(in);
    if (v != Verdict::Skip) return v;
    if (gpu_dirty() || dirty()) return Verdict::LockedDirty;
    read_order(in);   // (upload_value / upload_seg as they are now: after the memory look)
    if (in.upload_value == in.rec_value && in.upload_seg > in.seg) return Verdict::LockedLaterSeg;
    return Verdict::Skip;
}

}  // namespace mkwvk::touch_fast
