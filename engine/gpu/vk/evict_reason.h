// Copyright (C) 2026 Sacrappy

// Rules of the eviction pass (vk_texture.cpp). tests/test_evict_reason.py
//
// Ported from Longshot 25ee6a83 (#323), without the rest of Longshot's header (the reason tally): only the rule that is used.
#pragma once
#include <cstdint>

namespace evict_reason {

// A write-back before eviction puts an image's texels over all of its storage and marks those pages written. It is stale when
// another image was drawn into that storage after this one's last drawing (other_written: the newest such drawing's frame, 0 for
// none; vk_texture.cpp's drawn_others): that memory is the other image's now, and a finalized image there is uploaded from what
// was written. A drawing in the same frame keeps the write-back: which of the two came last is not known
inline bool write_back_stale(uint64_t last_written, uint64_t other_written) { return other_written > last_written; }

}  // namespace evict_reason
