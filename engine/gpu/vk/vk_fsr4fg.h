// Copyright (C) 2026 Sacrappy

// AMD FSR 4 frame generation (vk_fsr4fg.cpp): the third generator of the Frame generation by row's AMD side, through AMD's own runtime
// (amd_fidelityfx_loader_vk.dll and amd_fidelityfx_framegeneration_vk.dll, the user's, from AMD's FidelityFX SDK 2.4; none ship with
// the game). AMD's frame generation needs Windows 11 and a Radeon RX 9000: it is offered only where AMD's runtime itself says it
// runs on this GPU (its version query), and it makes ONE frame between two of the game's (2x), like AMD FSR 3.1's. Like DLSS FG it
// only writes the made frame into an image it is given: the renderer's display thread keeps presenting and pacing (no AMD swapchain).
// Any failure leaves the frame to the generators that were there before. NOT TRIED ON REAL HARDWARE: nothing the developer has can
// run it (RTX and Vega only); every call follows AMD's headers and documents, and every return code is logged in words.
#pragma once

#include "vk_dlss.h"   // (DlssFgInputs: the same scene inputs)

namespace mkwvk {

/// Why FSR 4 frame generation is not offered here, for the menu and the log
enum Fsr4FgWhy { FSR4FG_OK, FSR4FG_NOT_BUILT, FSR4FG_ENV_OFF, FSR4FG_NOT_WINDOWS, FSR4FG_NO_LOADER, FSR4FG_NO_PROVIDER, FSR4FG_FAILED, FSR4FG_UNKNOWN };
/// The reason, FSR4FG_UNKNOWN while AMD's runtime has not been asked yet (the menu asks when it opens; the first frame with the row
/// at FSR 4 asks too)
Fsr4FgWhy fsr4fg_why();
/// AMD's runtime says it makes frames on this GPU: the Frame generation by row has a third stop for it. Never true in a build without
/// FSR (HOOKSHOT_FSR), off Windows, with RX_VK_FSR4_FG=0, or once it has failed for the run
bool fsr4fg_offered();
/// FSR 4 frame generation is chosen (the row, or RX_VK_FSR4_FG=1 over it) and offered
bool fsr4fg_wanted();
/// Asks AMD's runtime once whether it runs here (the menu's thread, after the device is made). Nothing before the device
void fsr4fg_probe();
/// Records the one made frame between the frame before the game's `color` (w x h, RGBA8, GENERAL) and it into `out` (RGBA8, GENERAL, w x h),
/// from `in` (the scene's depth, motion and camera). `sequence` counts the presents: a gap in it is a new history for AMD's runtime.
/// Returns 1, or 0 when it made none (not chosen, no runtime, the game's HDR picture, any return code that is not OK): nothing is
/// written then and the other generators make the frame
int fsr4fg_frames(VkCommandBuffer cmd, VkImage color, VkImageView color_view, uint32_t w, uint32_t h, VkImage out, VkImageView out_view,
                  const DlssFgInputs& in, uint64_t sequence, bool native_pq);
/// Its context and memory let go while the row is not at FSR 4 (called every present; nothing once they are gone)
void fsr4fg_idle();
/// AMD's frame generation, in words: what the Frame generation by row says of it ("" when it can run)
const char* fsr4fg_why_text(Fsr4FgWhy why);

}  // namespace mkwvk
