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

## 23 Nov

| Item | State | Note |
|---|---|---|
| LIFT2 native FP, FPX_EASY, local vregs | on by default on local main 1434a3edd, not pushed | 5070 Market +10-12%; =0 gives the exact path back. Of run_all's 4 failures, test_cmpflags is fixed (its standalone driver runs with the options off); test_nup_paths (no symlink privilege), test_nup_read_name (no zstandard) and test_renderer_new_handler (MSVC clang: no oldnames.lib) come from this PC's setup, not LIFT2 |
| 3050 GPU cost at 1440p 100% | profiled (Task 28) | passes 9.1-12.9 ms, compute 3.4-4.5 ms, write-backs 0.8-1.4 ms, barriers and copies about 0.9 ms |
| FP16 instead of FP32 | dropped | Ampere consumer GPUs (GA10x) run non-tensor FP16 at the FP32 rate; the game's TAAU network already uses the tensor cores. Only register and bandwidth savings would remain, at a precision risk |
| BC7C ASTC→BC7 disk cache | NO-GO (perf/bc7c cda7c43fb) | what stays RGBA8 is negligible (Kokiri 14 MB sampled); warm disk cache no better than off at zone entries (0.7-7 s of frames over 33 ms per zone either way): the hitches are not the BC transcode (~1 ms GPU a zone); disk arms 78 fps vs 112 off (cause not hunted). RX_VK_BC_DISK stays off, unmerged. The zone-entry stalls go to ZONE2 (log analysis by cause) |
| SPEC1 shader specialisation on constant-buffer values | NO-GO (perf/spec1 ea923c53e) | the costly programs (d8e59341, 739acd67, d905f52b) have no branch on cbuf words, only constant math; the foldable ones (43ec86f3, 918adef1 mesh/task) cost ~0.1 ms. Hand-specialised 43ec86f3: 0.103-0.109 -> 0.092 ms (-0.012 ms a frame), 1a7482c0 unchanged. A real implementation is ~7 days for 0.1-0.5% of GPU time, plus variant compiles (stutters) and correctness risk. Notes in the branch |
| ASYNC1 async compute | CLOSED | 3050 stamps: of 2.5-2.9 ms of compute a frame, 0.02-0.08 ms runs beside graphics; every compute run depends on the graphics before it. Only a cross-frame overlap (PIPE1 frame-overlap refactor) could use the second queue |
| PASS1 pass map on the 3050 | map done; LOAD_CLEAR and SHADOW_SHIFT on the 3050 A/B (pictures = off); VRS slows the 5070 2x (cause open) | owner's go 23 Nov for VRS, quality options and load/store; 3050: 18-21 ms a frame at 100%, 12.6-15 at 50%: ~6 ms scale with resolution; 1a7482c0 ~1 ms is the VRS candidate | per pass and dispatch: what it draws (intermediate captures), ms at 100% and 50%, bound type. Then VRS 2x2 on low-frequency passes (VK_KHR_fragment_shading_rate), quality options (12288² shadow map, grass, volumetric fog), load/store ops, barrier cuts and merging the 1x1x1 (0.2 ms) and 47-per-frame dispatches |

## 24 November

| Item | State | Note |
|---|---|---|
| CUM1 main (b6d20c1ae) against integ4b on the 3050 | measured | default settings + vsync off (render Auto): castle +4.8%, Market +5.8%, Kokiri +4.2%, Hyrule +2.5%; 1440p native: +2.0%, +3.2%, +4.4%, +3.7% (median of 3, fps from play + 10 s; every new run above every old one). p99 unchanged; frames over 50 ms in Hyrule at native stay 53-61 for three runs: the gains are on the mean, not on the stutters |
| CUM2 same builds, DLSS fixed 67% at 1440p | measured | castle +1%, Market +8%, Kokiri +3%, Hyrule +6% |
| Sum against v0.4.3 at Market on the 3050 | not measured as one | integ4 +12.6% (earlier), main over integ4b +3 to +8%, order thread +5% (unmerged): about +23% if they add up. Target +30% |
| PIPE4 order thread to shippable | running | perf/pipe2 merged with main; wait and present let-go logging for the 2 dark frames |
| GPUOV2 barriers with no hazard (RX_VK_BARRIER_HAZARD, off) | done on the 5070, 3050 run routed | =3 folds 78 of 142 game barriers a frame and leaves out 17 of 145 pass starts at Kokiri; barrier time 0.37 -> 0.25 ms; fps unchanged on the 5070 (not GPU-bound); pictures and meter clean. Owner's rule 24 Nov: work-reducing changes without fps gain are kept if they produce no bug, so it merges after the 3050 run |
| FIX1 fold of bit-field extracts | merged 88eee8a9c | three wrong constant folds fixed, test over 3 M immediates. Does NOT cure #97: the owner still sees black leaves at Kakariko with it; cause search resumed from his save |
| FIX1 CPU ASTC decode on AMD integrated GPUs (#127) | merged 5e0d70a19 | default on Windows for AMD integrated GPUs except the Vega-generation APUs (24 Nov, laptop 5700U: CPU decode 1.3 fps turning vs 14.2 with the GPU decode, no reset); RX_VK_ASTC_CPU=0 opts out |
| LIFTSPLIT one writer in the lifted C's folder | merged f7c71c18b + 5a5662a5b | OS lock on gen.lock, count check, edits that miss name their place. The first merge broke the gates step under python -I; fixed with a test |
| FG1 frame generation study | done, docs/fg1 | three generators exist already (own 2x-8x, FSR 3.1 2x, DLSS-G on RTX 40/50); the gap is the HUD inside the interpolated picture |
| HUD1 HUD-less frame generation | running | port of Longshot's chain, 4 steps, all off by default |
| FG3050 what frame generation gives on the 3050 | running on the 3050 | off / FSR 3.1 2x / own 3x, Market and Hyrule |
| CRASH2 module-job crash family | done, see the crash family row below | Crater runs with child Link are invalid (he burns); the adult save came from a friend of the owner |
| LOCK1 lock-on triangles (#89) | done: cause narrowed, see LOCK2 below | docs/research/lock1-lockon-triangles.md on fix/lockon-89 |
| Crash family (module jobs, Crater, #126 #129) | fixed on main (RX_VK_WB_GUESS_CHECKED, default 1) | write-back watch on the friend's adult save: fix off, 42254 and 17448 write-backs into live heap blocks in the Crater; fix on, 0. The owner's 22 min by hand on a quiet machine: no crash, no stutter. GPU cost of the fix 0.1-0.5 ms |
| DECOUPLE order thread (perf/decouple de82506b9) | freeze FIXED (cc753074e: the timeline went back behind a held present); in integ/night1, on by default from 8 logical processors | 3050 matrix100: 20 of 20 starts clean (5 of 20 froze before); order thread +9.8% Kokiri, +7.9% Hyrule, +4.2% Market (3/3 each). Inline SUB_OVERLAP loses 2-6%, off. After the release: lanes across submissions (3-5 days), fewer submissions a frame (1-2 days) |
| Cold shader cache at the first start | measured on the 3050 | 51.7 vs 59.4 fps (-13%) with 3 background shader threads at ~95%: what a player sees right after an update |
| LIFT3 game code speed (perf/lift3 938cce426) | done: L1 native FP in cold functions +1.5%, L2 hot list +400 with fresh PGO +1.25%, both +3.2% on the 3050 (3/3); to finalise (picture and physics check, profile with castle-move) | 3050 Market: the critical path is the Presentation Thread (18.08 of 19.45 ms; driver and system 10.23 ms under queue submits, renderer 6.89). Game threads 68 ms of CPU a frame, cold functions 25.4% of guest time. A64_NATIVE_FP_COLD +1.5% (52.15/52.68/52.24 vs 51.59/51.82/51.27); HOS_SHORT_SLEEP_TIMER nothing. Next: +400 Market functions in the hot list with fresh PGO (lift3m1) |
| GPU11 the GPU cost that does not scale with resolution (perf/gpu11 46cdb2674) | done; only lever: volumetric fog off, +2.7 to +5.2% on the 3050 (one run a cell), becomes a menu option (LAPTOP1) | 3050 1440p GPU ms at 100% / 50%: Kokiri 17.39 / 11.90 (fixed part 10.1), Hyrule 16.99 / 12.20 (10.6), Market 18.90 / 13.55 (11.7). Kokiri fixed part: passes 5.22, dispatches 3.21, barriers 0.48, upload buffer 0.47, write-back 0.39, mesh conversion 0.12. Top: foliage mesh pass d8e59341 1.5 ms (81% in the mesh stage, foliage drawn twice), grass 4f0c0b40 1.08, fog ee7764a6 + 27ee9883 0.7-0.8. Fog at half rate is impossible (ping-pong images by frame parity), only fog off is clean. RX_VK_MESH_DIRECT ~1%; a 3584 mesh pad lost the device |
| TEXCACHE disk cache of decoded textures (perf/texcache 68366a529 = texcache6) | done, in integ/night1: on by default under 10 GB of VRAM and on integrated GPUs with GPU ASTC, 4 GB rolling cap | 3050: texcache5 warm +6.3% hyrule-walk, +2.2% castle-move; texcache6 default against off +3.4% hyrule-walk (3/3), +1% Market (noise); laptop +30-67% turning; no gain on the 5070 (off there) |
| VAR1 run-to-run variance on the main PC | done (perf/var1, notes var1-run-variance.md) | the variable is other work on the machine: quiet runs spread 4% (112.7-117.1 fps), a build or a second game beside the run halves it (111.8 -> 55-57) with GPU time flat and the gap between recordings 2.3 -> 10-14 ms. Recipe: speed lease, clean at start and end, warm cache, scene frames, 3 runs, real above 6% |
| LOCK2 lock-on ring (#89, fix/lockon-89 b22220748) | NEGATIVE, unmerged | RX_VK_WB_SHADOW_SYNC keeps the ring page known at the write-back (log) but the ring census stays E-E- in 155 of 155 samples, as with it off: the write-back refill is not the cause. Next: guest-side write watch on the pool page |
| PRIO1 background compile threads off the replay's core (perf/worker-priority c81f500c8) | measured, to merge | 3050 matrix102: cold shader cache (first start after an update) +5.7% (3/3); warm equal (58.64 against 58.74). The first form (prio1a) also pinned the jobs workers and lost 1% warm |
| LAPTOP1 low-end machines (perf/lowend b8479aa7d) | done, in integ/night1 | laptop 5700U: 6-8 fps turning -> 14.2 on the rule's first start -> ~16 after; goal 25-30 not reached: the no-mesh fallback's mesh stages (~11 ms) need two meshlets a 64-thread workgroup (2-3 days, after the release). Volumetric fog row (off: +2.7 to +5.2% on the 3050, one run a cell), Render resolution 33%, first-start low-end rule |
| AMD2 retired imports bounded on AMD and Intel (issues 131, 121) | merged 8d35f672d | issue 131 reproduced on the laptop by a stress hook (414 retired imports, 6.7 GB, the driver refuses, 0 draws) and gone with the bound (about 1000 give-backs, 0 refusals). The RX 6750 XT device loss stays unexplained |
| INPUT1 Sprint with L3, Touchpad opens menu | merged 8d35f672d | both off / unchanged by default |
| FG2 Base frame rate limit, AMD FSR 4 FG (feat/fg-fsr4-cap 269843d7a) | to merge | base 48 at 3x = 48.0 and 144.4 fps shown on the 5070; FSR 4 FG untested on real hardware |

## 25 November: v0.4.3 against tonight's integration (RTX 3050, camera turning)

Scene market-spin with three saves, each build at its own defaults, 3 interleaved rounds, fps from play (matrix104).

| Zone | v0.4.3 | night1b (order thread on) | night1b, order thread off |
|---|---|---|---|
| Market | 44.28 (44.98 43.99 43.86) | 55.75 (+25.9%) | 52.86 (+19.4%) |
| Hyrule Field | 53.30 (53.61 53.79 52.50) | 64.08 (+20.2%) | 60.33 (+13.2%) |
| Kokiri Forest | 54.42 (54.56 54.88 53.82) | 73.68 (+35.4%) | 66.70 (+22.6%) |

Every night1b run is above every v0.4.3 run. Frames over 16.7 ms: Market 94% -> 48%, Hyrule 65% -> 11-15%, Kokiri 51% -> 15-16%. 1% low: Market ~10 -> 14-16 fps, Hyrule 6-8 -> 10-13, Kokiri 7-8 -> 12-14. Target +30%: reached in Kokiri, not in the Market (+26%) nor Hyrule Field (+20%). Not in night1b yet: the black-leaves fix, the compile threads off the replay's core (+5.7% on a cold start), LIFT3's L1 + L2 (+3.2%). Open on the order thread: brightness spikes of one or two frames seen by the owner on the 5070 (FLASH1).
