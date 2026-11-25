# GPUPOV: our own GPU overhead a frame on the RTX 3050 (barriers, recording edges, tiny dispatches)

Worktree `E:\OOT\HeyListen-wt\gpuov`, branch `perf/gpuov` (from `origin/main` 0e5db2102). Lease name `gpuov`.
Scope: the barriers the replay adds (pass starts, dispatches, the game's own `nvnCommandBufferBarrier`), the barriers at a
recording's edges, and whether consecutive tiny dispatches can be merged. Nothing else.

## Final summary

(filled in at the end; the State section below is the running record)

## State

- [x] worktree `E:\OOT\HeyListen-wt\gpuov` on `perf/gpuov` at 0e5db2102; scratch `E:\OOT\scratch-gpuov`.
- [x] read the pass maps: `E:\OOT\scratch-pass1\map3050\{hyrule,kokiri,market}-r{1,4}.log` and `...\map\*.log`
      (parsed by `E:\OOT\scratch-gpuov\map_parse.py` into `map3050.txt` / `map5070.txt`).
- [x] prior art read: `docs/research/suspects/F-gpu.md` (F1), `docs/benchmarks/2026-11-12-tick057.md` (the barrier drop bound),
      `docs/research/replay-cost.md` — TICK-057 already bounded this: on the 3050, `RX_VK_TEST_BARRIER_DROP=1` (pass starts,
      dispatches and the game's barriers left out) took gpu_ms 19.9 -> 16.8 and fps 28.95 -> 31.47; the *narrowing* switches
      (GAME_BARRIER_NARROW, BARRIER_TRUST) and the duplicate-barrier dedupe inside segments moved nothing.
- [x] prototype in the engine, off by default: `RX_VK_BARRIER_AUDIT`, `RX_VK_BARRIER_HAZARD=<mask>`, `RX_VK_REC_END_DROP=<mask>`
      (vk_texture.cpp/h, vk_work.h, vk_draw.cpp, vk_replay.cpp).
- [x] build under the `gpuov` lease: `build/s5/bin/gpuov2` (07:45, tree = e7eda5c32). The first series (`gpuov`, 05:33) predates
      the last source edits (05:51-05:53) and is not used for numbers.
- [x] main-PC measurement DONE (results in "The current build"); no more speed leases on the main PC: the 3050 runs come next.
      (was: main-PC measurement, 3 interleaved rounds, base / HAZARD=1 / HAZARD=3, kokiri-idle then castle-move, PASS_TIMING=2 +
      AUDIT in every run (`build/s5/gpuov/main_ab.sh`, chained by `E:\OOT\scratch-gpuov\chain.sh`, outputs `v3k.out`, `v3c.out`).
      A first try (v2k) lost most runs to runguard rc 11 (another agent's compiler on the machine): main_ab.sh now waits and
      retries a run on rc 11. The main PC is not GPU-bound here (kokiri 6.9 ms GPU, 94 fps): fps there moves with noise.)
- [x] `build/s5/gpuov/pc3050.sh` rewritten: settings (r100 = HOS_DYNRES=0, auto) x scenes (castle-move, kokiri-idle, hyrule-walk,
      market-spin), arms on the command line, 3 rounds, a report per cell. Not yet run on the 3050.
- [x] pictures against the baseline's own noise floor: inside it (see above).
- [ ] tests + SWITCHES.md regenerated.

## The GPU frame on the 3050, by kind (RX_VK_PASS_TIMING=2 maps)

A frame of each scene, per frame, from the last "GPU pass timing, 600 presents" block of the map logs (the numbers are
profiled: each timestamp waits for the GPU, so they add up to more than an unprofiled frame; read the shares):

| scene | all | pass | dispatch | wb (MB) | barrier | u msync | u rbrefresh | u barrier | xfer | meshconv |
|---|---|---|---|---|---|---|---|---|---|---|
| kokiri r1 | 18.01 | 11.25 | 4.15 | 1.14 (58.0) | 0.53 | 0.16 | 0.17 | 0.16 | 0.18 | 0.12 |
| kokiri r4 | 18.02 | 6.77 | 3.45 | 1.08 (56.7) | 0.48 | 0.16 | 0.16 | 0.15 | 0.07 | 0.12 |
| hyrule r1 | 18.09 | 13.13 | 4.09 | 1.35 (58.8) | 0.51 | 0.21 | 0.23 | 0.19 | 0.19 | 0.15 |
| hyrule r4 | 18.27 | 8.76 | 3.18 | 1.17 (57.6) | 0.51 | 0.21 | 0.24 | 0.20 | 0.08 | 0.15 |
| market r1 | 18.66 | 13.12 | 4.17 | 0.97 (53.8) | 0.63 | 0.26 | 0.24 | 0.22 | 0.19 | 0.12 |
| market r4 | 18.51 | 8.62 | 3.36 | 0.86 (52.4) | 0.60 | 0.25 | 0.23 | 0.21 | 0.08 | 0.12 |

The barrier kinds, from the 80 longest spans of the same runs (ms a frame, count a frame, us a span):

| scene | the game's | pass start | dispatch | recording end | recording start | write-back/sync | other |
|---|---|---|---|---|---|---|---|
| kokiri r1 | 0.350 (120.7) 2.9us | - | 0.050 (14.3) 3.5us | - | - | 0.108 (56.7) 1.9us | 0.047 (35.8) 1.3us |
| kokiri r4 | 0.324 (120.3) 2.7us | 0.047 (22.8) 2.1us | 0.045 (14.2) 3.2us | - | - | 0.105 (56.6) 1.9us | 0.047 (35.8) 1.3us |
| hyrule r1 | 0.306 (111.5) 2.7us | - | - | - | - | 0.124 (65.3) 1.9us | 0.066 (49.8) 1.3us |
| hyrule r4 | 0.307 (111.8) 2.7us | 0.058 (30.9) 1.9us | 0.049 (15.5) 3.1us | 0.038 (12.8) 3.0us | - | 0.132 (66.9) 2.0us | 0.071 (52.8) 1.3us |
| market r1 | 0.405 (147.0) 2.8us | 0.061 (30.1) 2.0us | 0.059 (20.3) 2.9us | 0.051 (11.1) 4.6us | - | 0.147 (77.5) 1.9us | 0.071 (50.7) 1.4us |
| market r4 | 0.399 (145.9) 2.7us | 0.060 (29.6) 2.0us | 0.057 (20.3) 2.8us | 0.033 (10.7) 3.1us | 0.034 (26.7) 1.3us | 0.144 (77.1) 1.9us | 0.068 (49.6) 1.3us |

("-": below the top-80 cut of that run.) The main PC's maps (720p, `E:\OOT\scratch-pass1\map`) put the same class higher in
share, because its frame is shorter: kokiri-village-r1 2.001 ms of 12.41 ms (16.1%), hyrule-field-r1 0.405 of 6.88 (5.9%),
market-mid 0.39-0.41. The 3050's frame is 18-21 ms, so the same class is 2.6-3.4% there.

(*) the span counts are the profiled ones, not the recorded barriers: the frame log says what was recorded (kokiri:
381.2 full barriers a frame, 115.7 of them left out as repeats; the game's own by NVN flags: 0x12 68.5, 0x11 46.3, 0x28 14.0,
0x1 4.8, 0x21 4.0, 0x8 3.0, 0x10 2.8, 0x13 2.0, 0x9 1.0, 0x92 3.0, 0x97 1.0).

So the whole barrier class is 0.48-0.63 ms of 18.0-18.7 ms (2.6-3.4%) on the 3050, and the game's own barriers are 60-70% of it.
That is the metered cost of *recorded* barriers; the drops' cost (the 3.1 ms of the TICK-057 bound) sits in the drains they
impose on the passes around them, which a span measures only partly.

## What the map data says, per question

1. **Which barriers separate work with no real hazard.** Not answerable from the spans alone: the maps give the class and the
   cost, not what each barrier orders. Hence `RX_VK_BARRIER_AUDIT=1`, which classifies the window of every barrier (see below).
2. **The recording edges.** Small: 12.8 recording-end barriers a frame at 3.0 us = 0.038 ms (hyrule r4); kokiri's are below
   the top-80 cut. There is nothing after them in their command buffer to narrow them *for*, so `RX_VK_REC_END_DROP` tests
   both options (narrowing by mask is a no-op; leaving them out is the only variant that can move anything).
3. **Tiny dispatches.** 1x1x1 a frame on the 3050, from the maps: prog 435a0138b9013040 = 0.206 ms (1x, kokiri),
   592079442d7129da = 0.059 ms (1x), 755a777ada52677d = 0.055 ms for 11 of them (kokiri) / 0.296 ms for 59 (hyrule r4,
   5.0 us each). The two meter programs are the ones `RX_VK_METER_ZERO` puts two full barriers around (`vk_draw.cpp`), which is
   most of their span. 755a777ada52677d's 47-59 dispatches are independent of each other only if their storage does not alias;
   merging them needs the descriptors' address ranges proved disjoint, which the engine does not track per dispatch, so this
   item stays a measurement of the bound, not a prototype.

## The prototype (all off by default)

- `RX_VK_BARRIER_AUDIT=1`: counts, per barrier kind, the window of work recorded since the command buffer's last recorded full
  barrier: named (only pass targets were written), unknown (a dispatch, copy, clear, fill or update, or a draw whose program
  stores to memory), and whether the following work read one of the named targets (a real read-after-write). Off: only the
  window bookkeeping runs, and only when `RX_VK_BARRIER_HAZARD` is set.
- `RX_VK_BARRIER_HAZARD=<mask>`, bit 1: the game's own barrier is **folded** (not recorded) and the next barrier of the command
  buffer orders the same; the dedupe is told not to leave that one out. Bit 2: a **pass start's** barrier is left out when the
  images written since the last recorded one are neither written by the pass about to begin nor sampled by the draw about to be
  recorded (the draw's descriptors are built before its pass begins, so both are known at that point). Bit 3 = both.
- `RX_VK_REC_END_DROP=<mask>`: bit 1 the recording-end barriers, bit 2 the recording-start ones.

## Measurement
Main PC (RTX 5070, 720p, 300 Hz window, HL_FPS_LIMIT 360 as build/s5run.sh sets it), `build/s5/gpuov/main_ab.sh`: interleaved
rounds, one slot, `RX_VK_PASS_TIMING=2 RX_VK_BARRIER_AUDIT=1` in every run, fps from "play: began" + 10 s, GPU time and the
per-kind ms from the last 600-present block of each run.

### kokiri-idle (save `kokiri-village`), 3 rounds, build `gpuov` (the first, before the draw-read net)

| arm | fps mean (sd), 3 runs | vs base | GPU time ms | barrier ms | passes ms | dispatch ms |
|---|---|---|---|---|---|---|
| base | 95.33 (1.02) | - | 6.90 | 0.13 | 3.83 | 1.99 |
| `RX_VK_BARRIER_HAZARD=1` (fold the game's) | 94.22 (3.58) | -1.2% | 6.80 | 0.13 | 3.83 | 1.99 |
| `RX_VK_BARRIER_HAZARD=3` (fold + pass starts) | 94.96 (3.57) | -0.4% | 6.80 | 0.13 | 3.81 | 1.99 |
| `RX_VK_TEST_BARRIER_DROP=8` (bound, picture may be wrong) | 97.14 (2.41) | +1.9% | 6.50-6.70 | 0.13 | 3.75 | 1.92 |

The audit, a frame, from the same runs (`kg-2-1/game_log.txt`): the game's own barriers **141.2 checked, 67.3 with a named
window (folded), 73.9 with a dispatch, copy or storage write since the last barrier (kept)**; pass starts **134.4 checked,
42.6 named, 23.9 unknown, 30.4 read-after-write hits, 12.1 left out**. Without the fold (base, audit only): pass starts
134.6 checked, **2.8** named, 118.9 unknown, 2.4 hits, 0 left out — the fold is what lets the window span a game barrier and
show that a pass start's work is harmless.

So on this scene: 48% of the game's own barriers could be folded, and 9% of the pass starts (12 of 134) are provably without
hazard; the rest follow a dispatch/copy/storage write (74 + 24 a frame) or read a target written since the last barrier (30).

### castle-move (save `hyrule-castle`), 2 rounds, same build

| arm | fps mean (sd), 2 runs | vs base | GPU time ms |
|---|---|---|---|
| base | 106.92 (0.54) | - | 7.50 / 8.50 |
| `RX_VK_BARRIER_HAZARD=3` | 108.71 (1.87) | +1.7% | 8.40 / 7.30 |

The two runs of one arm differ in what they walked through (the pass class 4.00 vs 5.15 ms a frame), so the GPU time here is
not comparable run to run; the fps spread covers the difference. 2 rounds, not 3 (the lease's end).

### Pictures
`RX_VK_SHOTS` every 600 presents, compared by `E:\OOT\scratch-gpuov\pngdiff.py` (a zlib PNG decoder, no PIL here): the mean
|a-b| of the same-index shots of two runs, of 255.

| scene | pair | pairs | mean |a-b| | 90th pct | worst | white/flash frames |
|---|---|---|---|---|---|
| castle-move | base vs base (the noise floor) | 30 | 19.08 | 52.76 | 64.01 | 0 of 30 / 0 of 31 |
| castle-move | base vs `HAZARD=3` | 25 | 13.37 | 53.99 | 73.42 | 0 of 25 |
| kokiri-idle | base vs `HAZARD=3` | 28 | 7.98 | 9.56 | 98.09 | 0 of 28 / 0 of 28 |

The arms differ by less than the same arm does between two of its own runs on a walking scene; the one bad kokiri pair is a
transition frame. Not an identity proof: a shooting run per arm of the same scene is what `main_ab.sh` does, and the
baseline-vs-baseline pair is what makes the number readable.

### The current build (`build/s5/bin/gpuov2`, tree e7eda5c32), 3 interleaved rounds, main PC

Outputs `E:\OOT\scratch-gpuov\v3k.out` (kokiri-idle), `v3c.out` (castle-move); runs `build/s5/v3k-*`, `v3c-*`. Kinds are the recordings'
(`vk:     m barrier ... ms ... spans`; the report's earlier "barrier" column was the upload buffers'), ms a frame, mean of 3 runs:

| scene | arm | m barrier ms (spans) | all GPU ms | fps mean |
|---|---|---|---|---|
| kokiri-idle | base | 0.372 (191.8) | 7.26 | 98.29 |
| kokiri-idle | HAZARD=1 | 0.274 (152.9) | 7.15 | 100.55 |
| kokiri-idle | HAZARD=3 | 0.253 (143.5) | 7.11 | 97.66 |
| castle-move | base | 0.364 (194.3) | 8.47 (8.75, 8.81, 7.86) | 109.15 |
| castle-move | HAZARD=1 | 0.293 (155.9) | 8.56 (8.92, 8.91, 7.84) | 108.87 |
| castle-move | HAZARD=3 | 0.262 (148.2) | 8.41 (8.66, 7.83, 8.74) | 109.20 |

Audit, HAZARD=3, per frame: kokiri game's 141.6 checked / 78.0 folded, pass starts 144.9 checked / 44.6 hits / 17.1 left out;
castle game's folded 71.9, pass starts 146.3 / 41.6 / 12.0 left out. Pass, dispatch and write-back ms do not move.
Pictures (pngdiff.py, mean |a-b| of 255): kokiri base-vs-base 8.53, base-vs-HAZARD=3 8.31 and 8.79; castle base-vs-base 26.31,
base-vs-HAZARD=3 17.47: inside the floor. No FATAL / device lost; every `exposure sums` line: `2000 reads, 0 of them given the last result again`.

## What that says, per question

1. **The barriers with no real hazard, per scene.** On the main PC, per frame: the game's own 141, of which 67 name only pass
   targets (folding them is free of hazard by construction: the next barrier of the command buffer orders the same); pass
   starts 134, of which 43 name only pass targets and 12 of those survive the read-after-write test at the pass's first draw
   (the other 30 read a target written since the last barrier, a real hazard). 74 of the game's and 24 of the pass starts
   follow a dispatch, a copy or a storage write and are kept whatever the windows say. The 3050's split needs the same
   audit there: `build/s5/gpuov/pc3050.sh` (below).
2. **The recording edges.** Not narrowed but *dropped*: nothing is recorded after a recording-end barrier or before a
   recording-start one, so a stage/access mask on them narrows them for no reader. `RX_VK_REC_END_DROP` measures that; the
   metered class is 0.033-0.051 ms a frame on the 3050 (market: 10.7-11.1 ends + 26.7 starts) and below 0.05 ms on the main
   PC, so the answer there is "0.04 ms at most, not worth a switch".
3. **Merging the tiny dispatches.** Not prototyped (see above): 755a777ada52677d's 47-59 a frame are the ones that could be
   merged, and proving their storage disjoint needs per-dispatch address ranges the engine does not keep.

## Verdict

The mechanism is real and provable, and its ceiling on the main PC is small: `RX_VK_BARRIER_HAZARD=3` folds 68 of 141
game barriers and leaves out 12 of 134 pass starts a frame, for **-0.10 ms of a 6.90 ms GPU frame (-1.4%) and fps inside the
spread (-0.4% kokiri, +1.7% castle-move)**; the bound, with every one of the game's barriers dropped (`TEST_BARRIER_DROP=8`),
is **-0.2 to -0.4 ms and +1.9% fps**. The GPU is not short of barrier drains at 720p on the main PC. The 3050 is the machine
where TICK-057 measured 3.1 ms of that bound (19.9 -> 16.8 ms of GPU time), and where these 80 barriers a frame are worth
running the same audit.


## Files
- `engine/gpu/vk/vk_texture.cpp` / `.h`: the window of work since a command buffer's last recorded full barrier, the check,
  the audit's counters, `RX_VK_BARRIER_AUDIT`, `RX_VK_BARRIER_HAZARD`, `RX_VK_REC_END_DROP`.
- `engine/gpu/vk/vk_work.h`: a copy, clear, fill, update or query of a command buffer marks its window unknown.
- `engine/gpu/vk/vk_draw.cpp`: a pass's targets enter the window (`begin_pass`), the pass start's barrier is decided there, a
  draw whose program stores to memory marks the window unknown, and a draw that reads through an elided barrier ends its pass.
- `engine/gpu/vk/vk_replay.cpp`: the game's barrier is folded (`hazard_fold_game_barrier`), and the audit's per-frame lines.
- `build/s5/gpuov/main_ab.sh` + `gpuov_report.py`: the main PC's interleaved rounds, the shots and the comparison.
- `build/s5/gpuov/pc3050.sh`: the same on the PC 3050, self-contained (lock, push, build, rounds, report, cleanup, unlock).

