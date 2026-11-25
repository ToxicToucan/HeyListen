# PASS1: pass map and the 3050 switches (23 Nov 2026) - STATE: code written, not built, no measurements yet

**The machine queue blocked it.** At 12:00 the main PC's queue held five speed leases (45 min each, priority 2) ahead of a build
lease at priority 5, so the build was not granted in the first hour and would not have been for about four more; the ticket was
released. Nothing below was compiled or run. What exists is source (commit on `perf/pass1`), a test of the wiring and of the VRS
selection rule, the diagnostic that produces the pass map, and the 3050 script.

## What the pass map will be made from (one run, no code beyond this branch)

`RX_VK_PASS_TIMING=2 RX_VK_PASS_TIMING_TOP=80` (new: the number of spans and programs the report lists, default 25 and 20) prints every 600
presents, per pass: `pass WxH fFMT Nc[+d], draws bucket, N sampled, first prog, last prog, dw<0|some|all> disc<0|Y> a2c<0|Y> latez<0|some|all>
blend<...> cl<mask> vrs<0|1>` with its GPU ms, and per dispatch `dispatch prog <key> grid XxYxZ`. New in the label: `dw` draws that write
depth, `disc` rasterizer discard, `a2c` alpha to coverage, `latez` draws whose fragment program writes depth or kills (early Z lost,
from `Shader::Info::stores_frag_depth` / `uses_demote_to_helper_invocation`), `blend`, `cl` the attachments cleared by loadOp
(RX_VK_LOAD_CLEAR). Run it at `resolution=1` (100%) and `resolution=4` (50%) in the slot's hookshot_options.ini, scenes Kokiri village
(`savelib/kokiri-village`), Hyrule field and Market (`market-mid`), on the 5070 and the 3050. Intermediate-image captures go to
E:\OOT\scratch-pass1 with the existing `RX_VK_SHOTS` / `RX_VK_DUMP*` switches.

Load/store ops today: every pass is `loadOp LOAD, storeOp STORE` on every attachment (`begin_pass`, vk_draw.cpp); no pass discards
(`DONT_CARE`) anything, and clears are `vkCmdClearAttachments` inside the pass.

## Known 3050 numbers (from `docs/native-engine/gpu-pass-cost.md`, `shader-efficiency.md`, the task brief)

Top programs, 3050, 1440p 100%: `1a7482c0` ~1.0 ms full screen in every scene (the best VRS candidate if it is fog or light: check its
`dw`/`latez` in the map before using it), Kokiri `918adef1` 1.7, `43ec86f3` 1.3, `d8e59341` 1.1 (1.41 in Market), Hyrule `739acd67` 1.4,
`d905f52b` 0.9; `d8180bd8` full-screen 2560x1440 with 6 sampled textures 0.49 ms Kokiri. The top-10 table by 3050 cost, with each pass's
formats, sizes, ops, 50% scale cost and early-Z status, is to be filled from the run above.

## The switches (all off by default; `tests/test_pass1_switches.py` checks the wiring and compiles the VRS rule)

| Switch | What | Why it is safe off |
|---|---|---|
| `RX_VK_VRS=<prog hex prefix or WxH or all>,...` (`RX_VK_VRS_MIN_W`, default 64) | pipeline shading rate 2x2 (VK_KHR_fragment_shading_rate, dynamic, combiners KEEP) for draws of the named fragment programs, or colour-only passes of that size; never multisampled, never a depth-writing draw | the extension is not enabled and the pipelines have no new dynamic state unless the variable is set |
| `RX_VK_LOAD_CLEAR=1` | a full-target clear that opens a pass becomes the pass's loadOp CLEAR (colour with mask 0xF; depth when it covers every aspect), the clear call is skipped | only the first clear of a pass converts |
| `RX_VK_SHADOW_SHIFT=1..3` | the sun's shadow map (square depth-only 2D, 8192+ texels, 12288 D16, 288 MB) made 2^n smaller per side; viewport, scissor and clears shrink with it; no upload from guest storage | reached by `create` only with the variable set |

Not done: storeOp DONT_CARE (no frame lookahead exists at `begin_pass`: a depth buffer's next pass reloads it, so it cannot be proved
dead there; it needs the map plus a per-frame "last pass of this target" pass), grass density and volumetric fog resolution (both are
guest-side sizes chosen by the game's own code; the only replay-level handle is the same texture-size remap as the shadow map, to be
tried once the map names their targets), barrier cuts, merging the 1x1x1 dispatches.

## Risks to check in the first build

- `RX_VK_SHADOW_SHIFT`: a shader that reads the shadow map by texel (texelFetch) or a PCF kernel built from the guest's size changes the
  look; picture check against off in Kokiri and Hyrule (sun shadows, Link's shadow).
- `RX_VK_VRS` on a pass that is later read by texel (compute): 2x2 blocks show; pick only passes the map shows as smooth.
- The code has never been compiled: expect a first round of build errors (`vkCmdSetFragmentShadingRateKHR` added to `VKX_DEVICE3`).

The 3050 A/B: `build/s5/pass1/ab_pass1.sh <tag> <bin dir> [rounds] [scenes]` (arms `-`, load-clear, VRS list, shadow shift 1 and 2, all
three; fps from play + 10 s; `LEASE=none` when a lease is already held; `HL_REPO`, `HL_SLOTS`, `HL_SLOT`, `VRS_LIST` set the layout).
