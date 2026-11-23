<!-- Copyright (C) 2026 Sacrappy -->
# Performance and stutter plan, from 2026-11-22

The owner's order (22 Nov, evening): keep going until fps rises by **30% or more** and the **stutters are gone**. Multi-day work is allowed, and every attempt is documented. This file is the index: each lever has its own research doc, and build/s5/night/MORNING.md has the timeline.

## Baselines (integ4, counted from "play: began" + 10 s)

| Machine | Scene | fps | Notes |
|---|---|---|---|
| RTX 5070 / 7800X3D | castle-move (Hyrule Field) | 124-126 | CPU-bound: the replay thread takes about 7.5 ms after about 1.7 ms of game draw code |
| RTX 5070 / 7800X3D | owner's 22-min session | 130.4 | 223 frames ≥ 50 ms, 69 ≥ 100 ms, 22 ≥ 250 ms; the PC was under memory pressure |
| RTX 3050 / i5-10400F | castle-move, under memory pressure | 39.4 | clean 47.6 |
| RTX 3050 | Market, 1440p, profiled | 44-46 | the GPU sits idle about 6 ms a frame waiting for upload command buffers |

## Where the time goes (measured)

- **5070:** the replay thread (Presentation Thread) is the critical path. Textures are resolved on the CPU and rebound for every draw; there is no bindless (SHD1's audit). Driver calls take about 3.2 ms of about 12 ms of draw CPU (VKC1).
- **3050:**
  - GPU starvation: about 6-6.6 ms a frame of empty queue before "u barrier" buffers, measured profiled (SUBQ1 measures it without the profiler).
  - Compute 3.4-4.4 ms a frame, mostly independent of resolution.
  - Write-backs of 52-60 MB a frame.
- **Stutters:**
  - Page faults under memory pressure, within 30 s of a zone entry (STALL2).
  - The process footprint grows: private commit 10 → 30 GB in 22 minutes, host side 6.6 → 17 GB.
  - Under pressure, LOWMEM removes the multi-second freezes (STALL1, Task 29).

## Levers

| Lever | Target | Status | Result | Doc |
|---|---|---|---|---|
| STEP1 readback ring, GFX_VAR, DRAW1, ORDER1 | 5070 / 3050 | on main (integ3) | 5070 +10%, 3050 +5-8% | refactor-plan.md |
| OCC1 occlusion gate | both | in integ4, on | Market +4.2% on both PCs | occ1 |
| STALL1 LOWMEM, floor off | stutters under pressure | in integ4, on | 3050: >500 ms frames 8 → 0 a run, +3 fps | stall1-findings.md |
| ACC1 #121 retire | memory over long sessions | in integ4, on | 843 MB given back in 55 min | accum |
| ZONE1 per-zone preload/release | first visits | closed | preload: 0; release: harmful | zone1 |
| STATES1 bigger shipped state list | first visits | closed | small gain, +13 s cold start | MORNING 16:20 |
| SHD1 optimising shader translation | 3050 GPU | **closed** | the driver already does it; 0 fps | shd1-shader-pilot.md |
| Native Ampere frontend (Gemini) | 3050 GPU | rejected | f16 and subgroups negligible; 3-4 months | shd1 |
| LIFT1 Longshot lifter pieces 1-4 | game CPU | A/B running (main PC) | – | lift1 |
| LIFT1 piece 5, locals | game CPU | build fails: oot_patches text edits | to fix | lift1 |
| NATIVE_FP (ReLink) | game CPU | to do after LIFT1 | ReLink: actor threads 47 → 30% busy | relink report |
| QUICK1 (Rekarina ideas: compute-only images, NV cache, shadow ladder, sleep cadence, holds) | 3050 / stutters | 3050 Task 31 running | – | quick1 |
| PORT2 (ReLink: zero upload, reaper, BG cap, commit marks, PCIe probe) | stutters / 3050 | build queued | – | port2 |
| BIND1 replay CPU breakdown → bindless | 5070 | running | – | bind1-replay-cpu.md |
| SUBQ1 3050 GPU starvation + lazy write-back | 3050 | running | – | subq1-gpu-starvation.md |
| STALL2 fault watch + footprint growth | stutters / RAM | step 0 approved | – | stutters-owner-integ4.md |

## Next candidates (not started)

1. **Footprint reduction** (after STALL2's attribution). The host side grew by 10 GB in 22 minutes, and every stutter fix competes with that. Expected effect: large on stutters for 16-32 GB PCs.
2. **Bindless descriptors**, if BIND1's breakdown shows ≥ 1.5 ms of replay CPU in descriptor work. Multi-day work.
3. **ASTC → BC7 disk cache** at install, against texture pop-in and upload stutters at zone entry. Our encoder is already better than Rekarina's BC3.
4. **Arrival hold** (Rekarina) together with work done during the hold.
5. **A multi-threaded replay rework**, if bindless is not enough. This is the 5070's ceiling.

## Night of 22 Nov: results so far (21:55)

| Lever | Result | Decision |
|---|---|---|
| SHD1 shader optimisation (spirv-opt, sign, contract) | 3050: 0 fps; 701ae221 -0.2 ms in Hyrule only | closed: the driver already does it |
| LIFT1 pieces 1-4 | 5070 castle-move 122.4 vs 122.2 | no gain: game code is not the critical path |
| BIND1 replay breakdown | replay 7.7 ms (dry pass 2.35, own segment 2.89, small cbs 0.94); bindless would save 0.15-0.3 ms | bindless dropped |
| SUBQ1 3050 GPU idle | unprofiled: Market idle 5.4 ms, 4.2 of it 'replay not ready', driven by 'small cb recorded whole' 1.9 ms; Kokiri/Hyrule GPU-bound; 50% render scale +17-40% | SMALL1 launched |
| QUICK1 (Rekarina ideas) | compute-only images within noise; NVIDIA cache dir: start-up 2.1 -> 36.5 s, not reused | all off; NV cache reverted |
| PORT2 (ReLink/Longshot) | zero upload: Market entry -34 ms; reaper/cap: noise; BG_CAP: confirming on the 3050; 3050 PCIe 6.5 GB/s | zero upload on, rest off; WBL1 launched |
| STALL2 owner-session analysis | memory pressure (1-2.4 GB available); freed zone regions discarded then 42% faulted back | HL_REGION_KEEP built; warp-tour A/B running |
| MEMO1 cross-frame repetition | running | – |
| SMALL1 small command buffers | A/B running | – |
| WBL1 lazy write-back for slow PCIe | build queued | – |
