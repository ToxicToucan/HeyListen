# HUD1: HUD-less pictures for frame generation (2026-11-24)

Branch `feat/fg-hudless` (worktree `E:/OOT/HeyListen-wt/hud1`), from `docs/fg1` (main `5a5662a5b` plus the FG1 study,
`docs/research/fg1-frame-generation.md`). A port by behaviour of Longshot's HUD-less chain (#1464, #1691, #1487, #1538, #1755);
every step behind a switch that is off by default.

## State

Summary (2026-11-24): all four steps done, committed, each behind a switch that is off by default; pictures taken in the game
(Hyrule Field save, camera turned with the right stick, `RX_VK_SHOTS` + `RX_VK_SHOT_MADE=all`, FSR 3.1 2x, then built-in 3x).
Mean of |made frame - its set's game frame| over HUD rectangles (hearts / item box): switches off 29.7 / 18.3 (the HUD doubled and
smeared in the made frame), `RX_VK_FSR_FG_HUDLESS=1` 11.0 / 4.9, plus `RX_VK_FSR_FG_HUD_LAYER=1` 2.6 / 2.9, built-in 3x with
`RX_VK_FG_HUDLESS=1` 6.2 / 2.6 (8 to 11 sets each: a rough number, the pictures are the evidence; they stay on this PC, outside
the repo). Step 1 alone (capture, no consumer) leaves the pictures as main's (hearts 15.2 vs 14.5, 602 of 600 presents captured).
Not measured: a 3x baseline (that run never reached play), fps (no fps rounds in this phase), PQ / PsychoV35 pictures, AMD / Intel.

- [x] Step 1: HUD-less capture at the gameplay upscale dispatch, `RX_VK_FSR_FG_HUDLESS`
- [x] Step 2: FSR 3.1 interpolates the HUD-less colour, the presented frame beside it
- [x] Step 3: HUD layer fit and lay-over, `RX_VK_FSR_FG_HUD_LAYER`
- [x] Step 4: the same pair for the built-in interpolator, `RX_VK_FG_HUDLESS`
- Not touched on purpose: DLSS-G, FSR 4 (the brief), Native HDR's PQ present (falls back to the presented picture), PsychoV35's
  RGBA16F picture (not copied: another encoding than the frame's).
- Open: the built binary (hud1x) predates the commit that lets the HUD images go when frame generation idles (tested, not run
  in the game); Longshot's patched AMD passes (alpha mark, overlay factor 1) are not ported; HDR / PQ not covered.

## Step 1: the capture

- `vk_draw.cpp` `upscale_picture()` finds the gameplay upscale's output (as `temporal_stand_in` does; at 100% render resolution
  the known gameplay pass writes the scene's own size, Longshot #1691), the dispatch calls `framegen_hud_before()` after whichever
  drew it: the game's own network, FSR 3.1's, the temporal chain's, the Upscaler Off pass, the TAAU stand-in, and the stub
  (`upscale_stub`'s `stub_picture`).
- `vk_framegen.cpp` `framegen_hud_before()` copies the B10G11R11 picture into one of two images (`hud.before[k]`), `hud_source()`
  takes it as the present's own when it is fresh, still the picture it was copied from (`texture_alive`, new in vk_texture.cpp)
  and the presented picture's size. A present with no copy (menus, movies, loading) takes nothing and goes on as before.
- Nothing consumes the copy yet: with the switch on, step 1 changes no picture, it only counts (the 600-present `fsr3fi` line says
  how many presents had the picture from before the HUD).
- Test: `engine/tests/test_fsr3fi_hud_capture.py`.

## Step 2: FSR 3.1 takes the HUD-less colour

- `shaders/fg_hud.comp` (new, header `fg_hud_comp_spv.h` made by `tests/test_fsr3fi_hudless_colour.py --write`): the picture before the
  HUD, cropped as the frame, equal to the presented frame's pixel wherever nothing was drawn over the picture and sRGB-encoded
  under the HUD (the picture is linear B10G11R11, the frame RGBA8 sRGB: taken as it was, FSR 3.1 found the two different everywhere
  and every made frame was the presented one, Longshot #1538).
- `vk_fsr3fi.h/.cpp`: `Inputs::present` (the presented frame; null: one colour as before). With it `hudless_attached_factor` is 1,
  binding 2 of the inpainting pass is the presented frame (binding 3 stays the HUD-less colour), and the history is reset when the
  kind of source changes (a present with the picture after one without).
- `vk_framegen.cpp` `hud_source()` copies the picture as presented at the present (`hud.after[k]`), runs the pass, `fsr3fi_frame`
  hands the result to FSR 3.1 as `color` and the presented frame as `present`.
- Not ported: Longshot's patched AMD passes (FI_PATCHES: the alpha mark of #1464, the overlay factor of 1 where the two differ of
  #1538): this tree's AMD shaders are AMD's own and are not regenerated. The factor AMD's pass computes grows with the difference, so
  soft HUD edges may be laid partly; the HUD layer of step 3 replaces that lay-over.
- Test: `engine/tests/test_fsr3fi_hudless_colour.py` (and `test_psychov35_fsr3_fg.py`'s fixture follows `present` and `hudless`).

## Step 3: the HUD layer

- `shaders/fg_hud_layer.comp` (`hud_fit`): per pixel, from this frame's and the frame before's pictures (before the HUD S, as
  presented D), the game's blend D = B + T * S is fitted (least squares over the channels, decayed 0.9 a frame, started again when
  T no longer explains the frame, unknown where the HUD just appeared, nothing moved behind it, or a value is over 1).
  `shaders/fg_hud_composite.comp` (`hud_over`): the made frame's pixel becomes sRGB(B + T * linear(made)), the presented pixel where
  T is unknown, untouched where there is no HUD. Both headers by `tests/test_fsr3fi_hud_layer.py --write`.
- `vk_framegen.cpp`: two pairs of before/after pictures (`hud.before[k]`, `hud.after[k]`, k flips each present with a picture),
  `hud_layer()` after the HUD-less pass, `fsr3fi_frame` gives FSR 3.1 the HUD-less colour as its present too (it lays nothing) and
  runs the composite over `set.frame[0]` after copying the made frame. +24 bytes a pixel of VRAM (fit and layer RGBA16F, the
  second pair of pictures).
- `RX_VK_FSR_FG_HUD_LAYER=1` needs `RX_VK_FSR_FG_HUDLESS=1`; off by default.
- Test: `engine/tests/test_fsr3fi_hud_layer.py` (the fit and the lay-over as C++, the framegen wiring with spies, the headers).

## Step 4: the built-in interpolator

- `RX_VK_FG_HUDLESS=1` (off by default): `framegen_hud_wanted()` (FSR 3.1 asked for with `RX_VK_FSR_FG_HUDLESS`, or this switch) now
  decides the capture in vk_draw.cpp, so 3x to 8x and Auto, where FSR 3.1 is not asked for, get the picture from before the HUD.
- `hud.hudless[2]` (two, at k like the pictures) and `hud.pair`: this present and the one before both had the HUD-less colour, the
  same crop, and the HUD layer is made (the switch makes the fit pass itself). With the pair `framegen_record` finds its motion in
  the HUD-less luma, blends `hudless[prev]` and `hudless[cur]` (both the Auto and the fixed-multiplier loops), and runs
  `hud_composite` over each made frame (the layer: B + T * the made frame's scene, the presented pixel where T is not known).
  The game's own frame (the set's last) stays the presented copy.
- No pair (first present, a present without the picture, another crop, PQ, layer images or pipelines failing): as before.
- Test: `engine/tests/test_fg_hudless.py` (the wiring) and the `builtin` fixture of `test_fsr3fi_hud_capture.py` (the pair).
