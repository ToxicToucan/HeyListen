# FG1: frame generation for ordinary GPUs (design study, 2026-11-24)

Study only: no game run, no build, no lease. Branch `docs/fg1`, worktree `E:/OOT/HeyListen-wt/fg1`, main at `5a5662a5b`.
Vendor documents were read today (URLs inline); code references are `file:line` in this worktree. Scratch copies of the AMD
documents are in `E:/OOT/scratch-fg1` (not in the repo).

## State

- [x] Our frame generation read (vk_framegen.cpp, vk_fsr3fi.cpp, vk_dlss.cpp, vk_fsr3_inputs.*, present path in vk_replay.cpp)
- [x] AMD FSR 3.1 / FSR 4 FG, swapchain, licence read from the FidelityFX SDK v2.4.0 docs
- [x] NVIDIA DLSS-G (Streamline guide, NGX headers), Smooth Motion, XeSS-FG, RIFE looked up
- [x] Recommendation and plan written
- [ ] Nothing left except review by the owner. Open questions are in section 7.

## Summary (the five lines)

1. The premise of the brief is out of date: main already has three frame generators (own block matching 2x-8x on any GPU, AMD
   FSR 3.1 interpolation 2x on any GPU, NVIDIA DLSS-G through NGX on RTX 40+). The RTX 3050 can run the first two, never DLSS-G
   and never FSR 4 FG.
2. What the 3050 lacks is quality, not an option: main hands every generator the final picture with the HUD in it. Longshot's
   HUD-less chain (#1464, #1487, #1538, #1691, #1755) fixes the HUD smearing and is not in main (five commits, none an ancestor
   of main).
3. Build first: port that HUD-less chain onto main (HUD-less colour, HUD layer fit), then feed the same HUD-less picture to the
   built-in interpolator and to DLSS-G (NGX has `pHudless`/`pUI`; we pass neither today).
4. "FSR 4 frame generation" is Windows 11 plus Radeon RX 9000 only, a 36.8 MB signed DLL, and cannot be tested here. Build it
   second, as a context-only FG (no swapchain proxy), for the players who ask; it does nothing for a 3050.
5. Do not build a frame-generation swapchain proxy (FSR swapchain, Streamline): it would replace our swapchain, pacing and
   overlay path, and it is Windows-only.

## 1. What exists today

### 1a. In our tree (main)

| Generator | Where | GPUs | Notes |
|---|---|---|---|
| Built-in block matching (luma flow, 16x16 blocks, median, synth), 2x to 8x and Auto | `engine/gpu/vk/vk_framegen.cpp:835-947` (`framegen_record`), shaders `shaders/fg_*.comp` | any Vulkan 1.3 GPU | colour only, no depth or motion; the only one with Auto and 3x-8x |
| AMD FSR 3.1 frame interpolation, 2x, 17 compute passes (7 optical flow, 10 interpolation) | `vk_fsr3fi.cpp`, `vk_fsr3fi.h:1-20`; called from `vk_framegen.cpp:757-797` (`fsr3fi_frame`) | any GPU with push descriptors, storage formats, subgroup ops (`fsr3fi::capable`) | AMD's MIT GLSL compiled to SPIR-V, no AMD DLL; `docs/SWITCHES.md:246` |
| NVIDIA DLSS-G through NGX, 2x (RTX 40) to 6x (RTX 50) | `vk_dlss.cpp:488-930` (`fg_start`, `dlss_fg_frames`), `vk_framegen.cpp:799-833` (`dlssg_frames`) | RTX 40 and 50 only; needs `nvngx_dlssg.dll` from `scripts/ngx_sdk.py` | picks up our depth, motion, camera; no HUD-less (see 2) |

The row logic is `framegen_rows.h:13-47` (BY_OWN, BY_DLSS, BY_FSR3; FSR 3.1 capped at 2x, DLSS at its runtime's most + 1).
`docs/native-engine/framegen-vendors.md` (FG1 phase 1, 2026-11-18) still says "HeyListen main does not have DLSS FG"; that was
true on 2026-11-18 and is stale since f0ae985b9. Two of its claims are wrong today: DLSS-G is on main, and "NGX has no UI tag in
the NGX interface of v310" is false (the helper's eval struct has `pHudless`, `pUI`, `pUIAlpha`:
`E:/OOT/ngx-sdk/include/nvsdk_ngx_helpers_dlssg_vk.h:36-38`, parameters `DLSSG.HUDLess`/`DLSSG.UI` in `nvsdk_ngx_defs_dlssg.h:76-86`).
It should be corrected when this study is accepted.

### 1b. Vendor options

| Option | Vulkan | Hardware | Licence and shipping |
|---|---|---|---|
| FSR 3.1.x frame interpolation (SDK sources) | yes, as GLSL/HLSL source, our path | any GPU with typed UAV load and RGBA16 UNORM (https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/blob/main/Kits/FidelityFX/docs/techniques/frame-interpolation.md, "Limitations") | the `framegeneration\fsr3` sources are in the MIT list of `Kits/FidelityFX/docs/license.md` (lines 549-600 name them, the MIT text is at the end). Source may live in our repo; already does (`engine/third_party/fidelityfx-sdk`, v1.1.4, MIT) |
| FSR 3.1.7 frame generation swapchain (Vulkan) | yes: replaces `vkCreateSwapchainKHR`, acquire, present (frame-interpolation-swap-chain.md, line 33 and 58) | table at its top: 3.1.7 "None" extra hardware | the VK swapchain sources are MIT-listed too (license.md lists `fsr3\vk\FrameInterpolationSwapchainVK*.cpp`); the shipped build is `amd_fidelityfx_framegeneration_vk.dll`, 36 806 928 bytes (GitHub contents API, SDK v2.4.0, published 2026-10-28) |
| FSR 4 Frame Generation 4.0.2 (ML) | yes, SDK v2.4.0 (loader + `amd_fidelityfx_framegeneration_vk.dll`) | **Windows 11, Radeon RX 9000 series or later, Vulkan 1.2+** (frame-interpolation-ml.md "Limitations"); reference cost 2.1 ms at 1440p on an RX 9060 XT, 313 MB at 2560x1440 | the DLL is under AMD's general licence: binary-only redistribution with notices, "No reverse engineering, decompilation, or disassembly" (license.md lines 1-9). Not source. We already treat the FSR 4 upscaler DLLs this way (`engine/scripts/ffx_sdk.py:1-14`: fetched by the user, hash-checked, never committed) |
| NVIDIA DLSS Frame Generation via Streamline | yes (interposer owns swapchain, optical-flow queue, Reflex through Streamline mandatory; V-Sync with FG D3D12 only, Dynamic MFG D3D12 only) (https://raw.githubusercontent.com/NVIDIAGameWorks/Streamline/main/docs/ProgrammingGuideDLSS_G.md) | RTX 40/50, Windows 10 20H1+, HAGS | NVIDIA's own licence, binaries only; not source in an open repo |
| NVIDIA DLSS-G via NGX directly | yes, what main does | RTX 40 (2x), RTX 50 (up to 6x, `MultiFrameCountMax`) | same NVIDIA SDK licence as DLSS SR; fetched by the user (`scripts/ngx_sdk.py`) |
| NVIDIA Smooth Motion (driver) | yes, implicit layer `VK_LAYER_NV_present` replaces the present, `NVPRESENT_ENABLE_SMOOTH_MOTION=1`; presents from an async compute queue, which can clash with overlays (`NVPRESENT_QUEUE_FAMILY=1` works around) (https://download.nvidia.com/XFree86/Linux-x86_64/595.84/README/nvpresent.html) | RTX 40 and newer | driver feature, nothing to ship; sees only the final picture (HUD included), so it is the fallback, not a design |
| Intel XeSS-FG | DirectX 12 only per Intel's guide (https://intel.com/content/www/us/en/developer/articles/technical/xess-fg-developer-guide.html); Vulkan appears for XeSS-SR only | Arc (ML) or any SM 6.4 GPU (SDK 2.1+) | `github.com/intel/xess` licence shows NOASSERTION; no Vulkan, so out |
| AMD AFMF (driver) | driver-level | Radeon | nothing to ship; not under our control |
| RIFE (rife-ncnn-vulkan) | Vulkan compute (ncnn) | any, but a neural network over every frame | MIT (GitHub API); last push 2024-01-02; far heavier than FSR 3.1 on a 3050; no depth or motion input. Not recommended |

What "FSR 4" changes for frame generation: FG 4.0.x is a neural network that replaces FSR 3.1's analytic interpolation; same
API order (`ffxConfigure`, `ffxDispatch...PrepareV2` with depth, motion vectors, camera position/up/right/forward, jitter, frame time,
reset; then `ffxDispatchDescFrameGeneration` with `outputs[]`, `numGeneratedFrames`, optional HUD-less colour)
(frame-interpolation-ml.md "Call order", frame-interpolation-api.md lines 120-140, 300-350). Frame ID must rise by exactly 1
or the history resets. The context only computes the image; presentation is separate, so a context-only use (no swapchain proxy)
is documented as possible but loses driver-assisted pacing, which needs the AMD swapchain dispatching FG itself
(frame-interpolation-swap-chain.md lines 206-214). 4.0.2 does not support the debug view.

## 2. What each generator needs, and what our renderer already makes

Our inputs are assembled by `vk_fsr3_inputs.cpp` (header `vk_fsr3_inputs.h:30-52`), at the gameplay upscale's dispatch, shared
by FSR 3.1 FI and DLSS-G (`vk_framegen.cpp:925`: `fsr3_inputs::take`). The DLSS/FSR 4 upscalers have their own chain in
`vk_temporal.cpp` (header comment lines 1-47) with actor motion; FSR 3.1 FI gets actor motion only when FSR is an engine of that
chain (PsychoV35 on or `RX_VK_FSR3_CHAIN=1`, `vk_temporal.cpp:34-37`).

| Input | FSR 3.1 FI | FSR 4 FG | DLSS-G (NGX) | What we have |
|---|---|---|---|---|
| Colour (final picture, display size) | yes | yes (input colour) | `pBackbuffer` | yes: the window texture, RGBA8, or RGB10A2 for Native HDR PQ (`framegen_record` copies it, vk_framegen.cpp:872; two copies kept `fg.frames[2]`) |
| Depth | yes, reversed accepted | yes, flags `DEPTH_INVERTED`/`INFINITE` (api.md line 66-68) | `pDepth`, `depthInverted` | yes: R32F reversed, render size (`vk_fsr3_inputs.h:36`) |
| Motion vectors | yes, render pixels (low-res permutation, `Inputs::motion_scale`, vk_fsr3fi.h:46) | yes, scale + jitter-cancel flag | `pMVecs`, scale 1 in render pixels (vk_dlss.cpp:884-891) | yes: RG16F render pixels toward the previous frame, no jitter, camera motion from depth; actor motion only on the temporal chain |
| Camera jitter | yes | yes (`jitterOffset`) | yes | yes: the one shared jitter, read back from the projection (`vk_fsr3_inputs.h:6-13`) |
| Camera matrices / position, up, right, forward | near, far, fov | position, up, right, forward required; "incorrect or low-precision values may result in incorrect rendering" (ml.md "Camera data") | view/projection/clip-to-prev | have projection, view-projection, previous view-projection (`vk_fsr3_inputs.h:38-43`); DLSS-G already derives the camera vectors from them (vk_dlss.cpp:895-901). FSR 4 FG can reuse that code |
| Frame time | yes | yes | via sequence | yes: `frame_ms` |
| Reset / cut | yes | yes (`reset` or frame ID gap) | `reset` | yes: `Frame::reset`, `Inputs::sequence` |
| **HUD-less colour** | optional (`HUDLessAttachedFactor`, vk_fsr3fi.cpp:181, 878) | optional `HUDLessColor` | `pHudless` | **missing on main.** FSR 3.1 is told "one colour, with whatever the game drew on it" (vk_fsr3fi.cpp:878) and "no separate HUD-less colour" (vk_fsr3fi.cpp:1151); `vk_fsr3fi.cpp:109` says "the HUD-less and UI inputs this game does not have" |
| UI colour + alpha / mask | optional | optional (UI texture, callback) | `pUI` / `pUIAlpha` | **missing** |
| Reactive / transparency mask | not an input of FI | not listed in the FG API | none | not produced, and not needed by any of the three |
| Distortion field | no | no | optional | not needed (no lens distortion) |

Format notes: our depth and motion are at render size while the colour is at display size, which all three accept (FSR 4 FG's API
says `renderSize` is the size of depth and motion, api.md line 312).

## 3. How it fits our presentation

The path (all `engine/gpu/vk`):

1. The game's present reaches `do_present` in `vk_replay.cpp`. With frame generation on, a recording of its own is submitted
   after the one that signals the game's present value, so the game is not held back (vk_replay.cpp:6936-6960, 7048-7058):
   `framegen_record(cmd, window texture view, ...)` fills one `OutSet` (made frames + the real frame), `framegen_queue` hands it
   to the display thread.
2. The display thread (`display_main`, vk_framegen.cpp:450-538) paces the set: it keeps an even line of present times, can drop
   made frames when FIFO has no refresh for them (lines 481-493), honours VRR minimum gaps, and calls
   `present_image` for each frame.
3. `present_image` (vk_replay.cpp:7563 on) owns the swapchain: it recreates it on resize/HDR/V-Sync/VRR change, acquires,
   scales the frame to the window (`framegen_scale`), makes the HDR10 picture if asked (`framegen_hdr`), draws Dear ImGui
   (`menu_record`, line 7801, "over all of it"), submits the blit, and calls `vkQueuePresentKHR` on a compute-family queue of
   its own when there is one (7857-7870). Swapchain creation is `create_swapchain`, vk_main.cpp:874, with the present mode
   chosen by `present_mode.h`.
4. The user-visible "Presentation Thread" is the game's own render thread (vk_reflex.cpp:13: "OoT simulates on its main thread
   and renders on its presentation thread"); with frame generation on, presents come from the display thread and Reflex is not
   used (vk_reflex.cpp:15).

What a frame-generation swapchain proxy (the FSR FG swapchain, Streamline's interposer) would replace: `create_swapchain`,
the acquire (vk_replay.cpp:7633), the present call (7867-7869), and our whole display-thread pacing. Conflicts:

- **Our pacing**: the proxy paces by its own busy-wait threads and, on RX 9000, driver-assisted pacing
  (swap-chain.md lines 190-229). Ours handles FIFO leave-out, VRR gaps, Auto timeline, the frame rate limit as "rate presented"
  (framegen_rows.h:62-77). Two pacers cannot both own the present.
- **Overlay and scaling after the made frame**: ours draws the window scaling, HDR conversion and ImGui per shown frame in
  `present_image`. The proxy would need all of that moved into its UI-composition callback (swap-chain.md lines 133-146), which
  runs on the present thread on a second queue.
- **Queues**: the proxy wants game, async-compute, present and image-acquire queues (swap-chain.md lines 89-103); we have
  `dev.queue` plus one compute `dev.present_queue` guarded by `present_queue_mu`.
- **HDR**: the proxy replaces `vkSetHdrMetadataEXT` (swap-chain.md line 58); our HDR10 swapchain and PQ passes
  (`opts.hdr`, `present_native_pq`) sit around the present.
- **Linux**: AMD's runtime is Windows only (`engine/scripts/ffx_sdk.py:11`); FSR 4 FG needs Windows 11. NVIDIA's Smooth Motion layer
  exists on Linux but is the driver's. Our own display thread works wherever our renderer does (vk_framegen.cpp uses Win32
  timers directly: `CreateWaitableTimerExW`, line 439; I did not find the Linux shim and list it as an open question).
- **Storm hold, shots, V-Sync rows** all assume `present_image` is the one present.

None of this is needed for FSR 3.1 FI, built-in, DLSS-G, or an FSR 4 FG used as a context (all four only write images into our
`OutSet`). So the swapchain proxy has no benefit that outweighs rewriting `present_image`.

## 4. The UI problem

Where things are drawn relative to the upscaler:

- The game's scene is drawn small, the upscale network (or our DLSS/FSR stand-in) writes the 3D picture into the frame's
  B10G11R11 target, and **the HUD is drawn afterwards on that same target** (vk_temporal.cpp:7-9; the dispatch is the
  `gameplay_network` pass, vk_draw.cpp:9105, with the stand-ins at 9437-9500). So the HUD is after the upscaler and inside the
  picture we capture at the present.
- Our own overlay (Dear ImGui menu, FPS counter) is drawn last, per shown frame, on the swapchain image
  (vk_replay.cpp:7801). It is never in the interpolated picture and needs nothing.
- Therefore the only UI problem is the game's HUD (hearts, minimap, item and button icons, text boxes), which is in the
  colour every generator gets. On main it is interpolated with the scene, and moves with the camera motion field: smearing and
  halos. (Longshot's reports #1310, #1565 describe exactly this.)

How to give the interpolator a HUD-less image or mask:

1. **HUD-less colour by copy at the upscale dispatch.** Copy the upscale's output (exactly the picture before the HUD draws)
   cropped like the presented frame. The hook point exists: `fsr3_stand_in`/`temporal_stand_in`/the game's own network
   dispatch in vk_draw.cpp:9437-9495 all see `out`. Longshot did this as `framegen_hud_before` (#1464, `fg_hud.comp`) and fixed the
   native-resolution case in #1691. Main's own upscale-stub finder refuses an output as big as its input unless the key is a
   gameplay network (vk_draw.cpp:9162), which is the same size trap #1691 describes for the capture.
2. **FSR 3.1 fed HUD-less + presented**: #1487 gives `in.color` = HUD-less, `in.present` = presented frame, sets
   `hudless_attached_factor` (we hard-set it 0 at vk_fsr3fi.cpp:878) and binds `present` as binding 2 of the inpainting pass
   (vk_fsr3fi.cpp:1152 binds `color, color` today). No shader regeneration was needed (git show 310e7c9df: only
   `fg_hud.comp` changed, the AMD passes unchanged). #1538 fixes the encoding (sRGB under the HUD).
3. **HUD layer**: #1755 fits per pixel how see-through the HUD is (`D = B + T x S`, S from the HUD-less copy of two frames) and
   lays it over each made frame (`fg_hud_layer.comp`, `fg_hud_composite.comp`); cost: +24 bytes/pixel of VRAM (88 MB at 1440p)
   and no fps cost measured on an RX 6700 XT. This also gives a UI-alpha-like input.
4. **For DLSS-G**: pass `pHudless` and either `pUI` (premultiplied colour + alpha) or `pUIAlpha` from the fitted T and B
   (`vk_dlss.cpp:872-926` passes only backbuffer, depth, motion). The UIR mode also needs
   `NVSDK_NGX_DLSSG_Parameter_UserInterfaceRecompositionEnabled` at create time (defs_dlssg.h:86-88). Unverified in play (RTX 5070 on
   the main PC could test it; not RTX 40).
5. **For the built-in interpolator** (`fg_synth.comp`): interpolate two HUD-less frames and composite the real HUD with the
   fit; this removes HUD smear for every GPU and every multiplier, including 3x-8x and Auto, which FSR 3.1 cannot do.
6. **For FSR 4 FG**: `HUDLessColor` in `ffxConfigureDescFrameGeneration`, or UI texture/callback modes (api.md lines 220-300).

Longshot's chain is at `longshot/main` (remote ref in this clone). Those commits are not ancestors of main
(`git merge-base --is-ancestor` on 310e7c9df, d664e3428, ed96031ac, ad9fa48e0, 8595b9804: all NOT in main), their paths lack the
`engine/` prefix, and their licence line is GPL-3.0-or-later, same as our engine; porting needs the owner's usual credit rule
(README line for each ported author).

## 5. Comparison for the target (RTX 3050, 2560x1440 out, 50-65 fps base)

- DLSS-G: impossible (RTX 40+).
- FSR 4 FG: impossible (RDNA 4 only).
- Smooth Motion: impossible (RTX 40+).
- FSR 3.1 FI at 2x: possible, 17 compute passes at 2560x1440 on a card that is already the bottleneck. No measured cost exists in
  this repo for the 3050 (no doc names it; `docs/benchmarks` has no frame generation round). AMD's 4.0.2 table lists 2.1 ms at
  1440p on an RX 9060 XT for the ML version only; nothing for 3.1.
- Built-in interpolator: cheaper (luma at quarter size, 16x16 blocks), lower quality, any multiplier. Its cost and artefacts on a
  3050 are also unmeasured here.
- Quality gap that the HUD-less chain closes applies to both.

## 6. Recommendation and plan

**Build first: the HUD-less chain on main** (port of Longshot #1464/#1691, #1487, #1538, #1755 plus its tests), then use it for
DLSS-G and the built-in interpolator. **Second: FSR 4 frame generation as a context-only generator** for RX 9000 owners.
**Not: any swapchain proxy.**

Switch names (everything off by default; frame generation itself is already Off by default, `docs/USER_GUIDE.md:51`):

- `RX_VK_FSR_FG_HUDLESS` and `RX_VK_FSR_FG_HUD_LAYER` (Longshot's names) for steps 1-3; default off on introduction, then on once
  an A/B is within noise (the "Defaults: my call" rule).
- Step 4 reuses them for DLSS-G (`RX_VK_DLSS_FG_HUDLESS`) and the built-in interpolator (`RX_VK_FG_HUDLESS`).
- Step 5: Frame generation by value 3, `framegen_rows::BY_FSR4` (saved `frame_generation_by=3`), env `RX_VK_FSR4_FG` (0 never, 1 over the row).

Ordered plan (agent hours = one capable agent including tests and review; no fps rounds, per the hybrid-engine phase rule, so
measurement steps are "function + picture" first):

| # | Step | Size | Notes |
|---|---|---|---|
| 0 | Correct `framegen-vendors.md` (DLSS-G is on main, NGX has HUDless/UI) and add the missing `THIRD_PARTY_NOTICES.md` row for `engine/third_party/fidelityfx-sdk` (v1.1.4 MIT, FSR 3.1 shaders: the table lists only `third_party/fidelityfx/` "API headers") | 1 h | docs only |
| 1 | HUD-less capture: port `fg_hud.comp` and the capture at the gameplay upscale dispatch (vk_draw.cpp stand-ins and the game's own network), with the native-resolution fix; copies kept per frame; unit tests as `tests/test_fsr3fi_hud_capture.py` style | 6-8 h | pure Vulkan, runs on the 3050. Open: B10G11R11 target format vs our RGBA8/RGB10A2 window copy |
| 2 | Give FSR 3.1 FI `color = HUD-less`, `present = presented`, set `hudless_attached_factor`, bind inpainting binding 2 to `present`, reset history when the source kind changes; encoding fix (#1538) incl. PQ | 4-6 h | no AMD shader change |
| 3 | HUD layer fit and composite (`fg_hud_layer`, `fg_hud_composite`, glslang-made SPIR-V) and tests | 5-6 h | +88 MB at 1440p |
| 4a | Built-in interpolator on HUD-less pair + HUD composite | 5-7 h | helps every GPU and 3x-8x |
| 4b | DLSS-G `pHudless` + `pUIAlpha`/`pUI`, UIR create flag | 5-6 h | test on the RTX 5070 only |
| 5 | (branch `feat/fg-fsr4-cap`: written without hardware, untested; `engine/gpu/vk/vk_fsr4fg.cpp`, the AMD DLL still to be pinned in `ffx_sdk.py`) FSR 4 FG context-only: extend `ffx_sdk.py` with `amd_fidelityfx_framegeneration_vk.dll` (36 806 928 bytes, hash recorded at fetch), `vk_fsr4fg.cpp` next to `vk_fsr4.cpp` (loader, `ffxCreateContext`, configure/prepare/dispatch order, frame ID, camera vectors reused from vk_dlss.cpp:895), `BY_FSR4` row, graceful "needs RX 9000" | 12-16 h | cannot be tested here; needs Mooseca (RX 9070 XT). Pacing stays ours (no driver-assisted pacing) |
| 6 | 3050 acceptance: FSR 3.1 FI vs built-in vs off at 1440p, HUD view, pacing | 3-4 h agent, plus leases | only when fps rounds are allowed again |

Order of value for the stated target: 1-3, then 4a, then 6, then 4b, then 5.

What to measure (when allowed): displayed fps and game fps (the performance statistics already carry `framegen`, `framegen_x`,
`framegen_auto`, `docs/performance-statistics.md:103-105`; the 10 s after "play: began" rule); frame pacing with
`RX_VK_FRAME_LOG` and the display thread's own 5 s line (vk_framegen.cpp:476-480: on time, late, spread of shown intervals);
input latency (see open questions: no hardware tool; the display thread shows frames "a game frame late", vk_replay.cpp:7730,
so FG adds at least one game frame of delay, 16-20 ms at 50-60 fps); HUD artefacts by `RX_VK_SHOTS` + `RX_VK_SHOT_MADE` in Hyrule
Field turning the camera, the same spots Longshot used (hearts, item box, minimap, button prompts), and the `fsr3fi` 600-present
line counting presents with the HUD marked.

Main risks:

- GPU time on the 3050: FSR 3.1 FI plus the HUD layer on a card already at its limit may lower the base rate more than the
  extra frames gain; unmeasured.
- Format mismatch of the HUD-less copy (B10G11R11 scene target vs RGBA8 window, PQ path): the #1538 class of bugs.
- Animated HUD parts (a beating heart, counters) show the presented pixel on frames they change (Longshot's own note on #1755).
- Menus, movies and loading have no upscale dispatch: they must fall back to the current path (Longshot did the same).
- Longshot's code is large and upstream moved on (1432 commits ahead of main); port by behaviour with tests, not by cherry-pick
  (paths differ: `gpu/vk` vs `engine/gpu/vk`).
- FSR 4 FG: cannot be validated here, loses driver-assisted pacing without the AMD swapchain, Windows only, 36.8 MB runtime to be
  fetched under AMD's binary-only licence (no reverse engineering).
- Reflex is not used with frame generation (vk_reflex.cpp:15); on a 3050 (NVIDIA) latency with FG is therefore not helped by it.

## 7. Open questions (need a run, a machine, or an owner answer)

1. Cost of FSR 3.1 FI and of the built-in interpolator at 2560x1440 on the RTX 3050 (pass timings: `RX_VK_PASS_TIMING`, vk_gprof)
   and the net displayed fps from a 50-65 fps base. No number exists in the repo.
2. Does the pre-HUD copy see the whole HUD in every scene (menus, dialogue boxes, the pause screen)? Longshot reports 600/600 in
   the field; HeyListen's own upscale paths (Off, own network) were not checked.
3. Can DLSS-G take our fitted HUD (UIR mode) without artefacts, on RTX 50 (the only NVIDIA FG hardware we have)?
4. Does FSR 4 FG's context run well without the AMD swapchain on RX 9000, and is the HUD-less input enough? Needs an RX 9000
   owner (Mooseca has an RX 9070 XT per the owner's notes).
5. Linux: vk_framegen.cpp calls `CreateWaitableTimerExW` unconditionally; where Linux gets it from, and whether the display
   thread paces right under Linux, was not checked (no Linux run). FSR 4 FG has no Linux build at all.
6. Input latency with FG on: needs a measurement method (the repo has Reflex timings only without frame generation).
7. Licence question only the owner can settle: Longshot's HUD commits are GPL-3.0-or-later, as our engine; the credit lines in
   the README must name each ported author (the existing rule).
8. Should the AMD FSR 3.1 swapchain's multi-frame or driver features be revisited if AMD's 3.1.7 swapchain is the only way to
   get 3x? Not needed while the built-in interpolator makes 3x-8x.
9. Smooth Motion (NVIDIA, RTX 40+) as a documented user option for players whose in-game FG is weaker: whether it clashes with our
   present queue (the README says it presents from an async compute queue; we also present from a compute-family queue,
   vk_main.cpp:814) is untested.

## Sources

- AMD FidelityFX SDK v2.4.0 (published 2026-10-28, GitHub API): docs `Kits/FidelityFX/docs/techniques/frame-interpolation-ml.md`,
  `frame-interpolation.md`, `frame-interpolation-api.md`, `frame-interpolation-swap-chain.md`, `Kits/FidelityFX/docs/license.md`
  (raw.githubusercontent.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/main/...); tree listing for `signedbin`.
- NVIDIA Streamline `docs/ProgrammingGuideDLSS_G.md`; NGX headers in `E:/OOT/ngx-sdk/include`; NVIDIA Smooth Motion README
  (download.nvidia.com/XFree86/Linux-x86_64/595.84/README/nvpresent.html).
- Intel XeSS-FG developer guide (intel.com/content/www/us/en/developer/articles/technical/xess-fg-developer-guide.html).
- rife-ncnn-vulkan (github.com/nihui/rife-ncnn-vulkan, MIT per GitHub API).
- Longshot commits 310e7c9df, d664e3428, ed96031ac, ad9fa48e0 (descriptions read with `git show`, remote ref `longshot/main`).
