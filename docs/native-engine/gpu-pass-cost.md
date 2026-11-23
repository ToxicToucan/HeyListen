# GPU pass cost and CPU cost by module: what step 1, 2 and 3 are worth (STEP0, 2026-11-21)

Step 0 of `refactor-plan.md`, second deliverable. Two tables: where a frame's GPU time goes by kind of pass (and what our translation
adds), and where its CPU time goes by module; then the candidates for step 2 (direct rendering) ranked by ms and the order of the steps.
No engine change: existing diagnostics only.

## Sources and their limits (read first)

| Source | Machine, scene | What it gives | Limit |
|---|---|---|---|
| `HL_PROFILE_LOCAL=1 HOS_PERF_REPORT=1 RX_VK_PERF_INTERVAL=60 HL_PROFILE_BATCH_S=10 RX_VK_PROF_FINE=1` (`profile_local/*.json`, the game's per-pass GPU timestamps, **not serialized**) | main PC, RTX 5070, Market `market-spin`, 100% render, 150 s run, 7 batches = 33 s of Market, 2,367 frames | per pass (kind, key, count, ms) and per dispatch (program key) | the report keeps the 24 slowest passes of each 10 s batch (`kMaxPasses`), so small passes are cut; one batch file was lost (deleted by mistake while cleaning the slot), 7 of 8 kept; the sampler thread costs 1.1 ms of CPU a frame |
| `RX_VK_PASS_TIMING=1` and `=2` (log block every 600 presents, timestamps around every span, **serialized**: spans add up to more than an unprofiled frame, read the shares) | main PC, Market, Kakariko, Hyrule field | every span by kind (pass, dispatch, barrier, write-back, refresh, mesh conversion, uploads), the 25 slowest spans, with `=2` the 20 programs whose draw runs took longest | 8.2-8.3 ms a frame in Market against the unprofiled 3.5-4.4 ms of GPU: shares only |
| the 3050 logs in `E:\OOT\scratch-step0\3050\`: `100a`, `100b`, `50` (logs only), then `100c` (150 s, 100% render, 1440p, 14 profile batches of 10 s = 101 s of Market, 4,228 frames) and `pt2` (`RX_VK_PASS_TIMING=2`) | PC 3050 | the per-pass profile (B3), GPU time lines, GPU tail, PROF_FINE CPU lines, perf summaries | same 24-passes-per-batch cap as the 5070 profile; the profile sampler adds CPU (24.0 ms frames in `100c`) |
| `replay-cost.md` REPLAY1 / DESC1 / ORDER2 (branches `perf/desc-044`, `perf/order2`), `E:\OOT\scratch-trace1\REPORT.md` (WPR) | main PC / PC 3050 | CPU by thread and phase | measured by other streams, cited as is |
| `shader-efficiency.md` (stream P4) | PC 3050, Kokiri and Hyrule field | program keys to what they are, mesh/compute ms on the 3050 | a different scene from Market |

## Getting a per-pass profile (done for the 3050 as `100c`)

The profile is written by `profile_queue()` in `engine/gpu/vk/perf_report.cpp`: a batch every `HL_PROFILE_BATCH_S` seconds of play
(default 300; range 10-3600), a final batch only at a clean exit, into `profile_local/` in the game folder. A harness-killed run shorter than
300 s writes nothing unless `HL_PROFILE_BATCH_S=10` is set. The command that worked: `HL_PROFILE_LOCAL=1 HOS_PERF_REPORT=1
RX_VK_PERF_INTERVAL=60 HL_PROFILE_BATCH_S=10 RX_VK_PROF_FINE=1`, 150 s. The tool that sums the files is `E:\OOT\scratch-step0\plagg.py` (local).

## B1. GPU time by kind, main PC, Market (RTX 5070, 100% render, 2560x1440)

Unprofiled frame: 14.0 ms (73 fps with the sampler on), GPU busy 3.5-4.4 ms (`refactor-plan.md`); the GPU is idle for the rest (the
queue picture says 6.25 ms of 14.6 empty between command buffers, 98% of it before an upload command buffer: the replay had not sent more work yet).
The serialized `RX_VK_PASS_TIMING=1` blocks, ms a frame of GPU (shares are what counts):

| Kind | Market 8.32 ms | Kakariko 6.26 | Hyrule field 7.42 | What it is |
|---|---:|---:|---:|---|
| **geometry: render passes** (shadow, main, post, UI) | 4.23 (51%) over 175 spans, 2,322 draws | 3.08 (49%) | 3.86 (52%) | the game's own work, ~24 us a pass |
| **compute: dispatches** | 2.03 (24%) over 168 spans | 1.69 (27%) | 1.93 (26%) | the game's own work; ~100 of the 168 are 1x1x1 or tiny |
| game barriers | 0.51 incl. 0.32 for the game's 141 `nvnCommandBufferBarrier`s | 0.39 | 0.38 | game's own (mostly) |
| **emulation: write-back copies** (`wb`, mirror to guest memory) | 0.47 (10.8 spans, 53 MB) | 0.38 (53 MB) | 0.46 (59 MB) | ours, the readbacks' other half |
| **emulation: readback refresh** (`rbrefresh`, CPU's zero back into the mirror) | 0.18 (27 spans, 5.9 MB) | 0.07 | 0.10 | ours (R1 of the ledger) |
| **emulation: mirror sync** (`msync` scatter, CPU pages to mirrors) | 0.17 (1,337 items, 5.2 MB) | 0.13 | 0.14 | ours |
| **emulation: barriers of the upload command buffer** (`u barrier`, write-back or sync) | 0.19 (127 spans) | 0.12 | 0.14 | ours |
| **emulation: mesh-draw argument conversion** (`meshconv`) | 0.10 (24 spans) | 0.13 | 0.12 | ours (indirect mesh draws) |
| texture upload copies (`xfer`, buffer to texture) | 0.06 | 0.06 | 0.06 | ours (BC/ASTC decode runs elsewhere: not in this table) |
| events, uniform-update copies, pool mirror makes, clears | 0.03 + 0.02 + 0.02 + 0.00 | 0.05 | 0.05 | ours/game |
| **emulation total** | **~1.3-1.8 ms (16-21%)** | ~1.1 (17%) | ~1.3 (18%) | wb + rbrefresh + msync + u barrier + meshconv + the rest |

Not timed at all by these blocks: the present's blit to the window (0.07-0.66 ms on the 3050 log), the renderer's HDR pass and its own
copies, BC/ASTC transcoding passes made elsewhere, clears made by the dry pass on the CPU. "Dry-pass-related" GPU cost is zero: the dry pass
is CPU-only (see B2). **Emulation is a fifth of the GPU's serialized time on the 5070; the other four fifths are the game's own passes
and dispatches.** On the 3050 the "10.8 ms resolution-independent" of the plan is geometry + compute + this overhead; applying the 5070's
shares (an estimate, not a measurement) puts ~2.3 ms of it as emulation, ~2.9 as dispatches and ~5.6 as the geometry passes' vertex/mesh
side. A 3050 profile would replace the estimate.

### Top passes, unserialized (profile JSON, Market, ms a frame, 100% render, 5070)

The 24-per-batch cap cuts the tail; the listed passes sum to 2.65 ms of the frame's ~4 ms of GPU. Kind is the report's heuristic
(`profile_report.h` `pass_kind`: colour+depth is "main", depth only "shadow", colour only with few draws or small is "post"); keys are
`pass_key` (target format, size class, first program) and not program keys.

| Kind | Pass key | ms a frame | a frame | us each | Notes |
|---|---|---:|---:|---:|---|
| main | c9e43b88b6716a41 | 0.341 | 1.0 | 337 | the biggest single pass: the scene's main colour+depth pass, max 1.34 ms |
| main | ef317a5479c11e0b | 0.168 | 0.3 | 536 | every third frame |
| compute | ee7764a6b1ef459c | 0.151 | 1.0 | 152 | grid 8x9x12; 11.8 k SPIR-V instructions, 112 global-memory sites (`shader-efficiency.md`) |
| post | 9151d1cc545e89d3 | 0.146 | 1.0 | 146 | screen effect |
| compute | ab7699ee7f536755 | 0.143 | 1.0 | 143 | grid 4x4x6 |
| compute | 9dc98871eb7fc101 | 0.118 | 1.0 | 118 | the cooperative-matrix ("matrix net", 192 coop-matrix ops) program, grid 64x36x1: the GPU's tensor path, nothing to remove (P4) |
| main | 02c9788dfa1c5fd3 | 0.116 | 1.0 | 116 | |
| compute | 435a0138b9013040 | 0.110 | 1.0 | 110 | **one of the exposure meter's two sum programs**, 1x1x1: pure latency |
| main | 38661608ba66017e | 0.109 | 1.0 | 109 | |
| compute | 09cc9a93a2d7dd81 | 0.104 | 38.6 | 2.7 | 1x1x1, 39 dispatches a frame: latency |
| compute | 27ee9883e2a20323 | 0.104 | 1.0 | 104 | grid 16x9x12, 32 texture ops |
| compute | 612a27de13e44e79 | 0.102 | 16.3 | 6.3 | 1x1x1 |
| main | b624742a9cb7e025 | 0.098 | 16.4 | 6.0 | 16 small passes a frame |
| main | bf87457d0996edb2 | 0.088 | 1.0 | 88 | |
| compute | 755a777ada52677d | 0.087 | 20.2 | 4.3 | 1x1x1, 20 a frame: "latency, not code" (P4) |
| shadow | 154bf4dd0315403e | 0.086 | 10.3 | 8.3 | 10 shadow passes a frame |
| main | 647395d47f52b44b | 0.083 | 1.0 | 83 | |
| compute | aa76b702d5b1ff0d | 0.083 | 3.0 | 28 | |
| main | 85583e00276a14c5 | 0.077 | 0.1 | 561 | every ten frames |
| main | 0b3bf877b1d267b5 | 0.070 | 1.6 | 45 | |
| compute | 26fe0c70ab9fc547 | 0.066 | 1.0 | 68 | grid 80x60x1; one 8 ms spike in 1,523 frames |
| main | b904a551db3bf98a | 0.066 | 1.0 | 68 | |
| compute | b612216258832b9c | 0.061 | 1.0 | 62 | |
| compute | abccf81b45c5eb50 | 0.039 | 0.6 | 61 | indirect dispatch |
| | by kind (listed only) | main 1.25, compute 1.17, post 0.15, shadow 0.09 | | | |

By programs (`RX_VK_PASS_TIMING=2`, serialized, Market, draw runs a frame, longest first): `fb7de12443c80159` 0.228 ms (16.7 draws),
`d8180bd8c6ecc77b` 0.144 (a 2560x1440 full-screen pass with 6 sampled textures; 0.49 ms in Kokiri on the 3050), `d8e59341cf4f629c` 0.114 (a
mesh+fragment program, 1.41 ms in Kokiri on the 3050), `46c14f8ee79c5349` 0.109 (62 draws), `1a7482c0ec9991ea` 0.108 (full-screen fragment,
1.07 ms Kokiri / 0.92 Hyrule on the 3050), `636eed4d5000d1c0` 0.086, `3427a1dbefd5e02c` 0.084, `ffb53ed102dd9908` 0.084 (483 draws at 0.17 us:
instanced, cheap), `48ed51830b1d8fb7` 0.066, `262c53789ef5dda1` 0.064, `3b4b12921b5d7d1c` 0.062, `c254a02b6a76abe8` 0.061 (14 runs). **No program
or pass takes more than ~0.34 ms of the 5070's frame: the 5070 is CPU-bound, and no single pass is the lever there.** The 3050 is the GPU
judge, and `shader-efficiency.md` (Kokiri and Hyrule field, 3050) already says where its ms are: mesh pipelines 7.2 ms (Kokiri) and 4.5
(Hyrule) of 20 ms, compute 2.2-2.4, vertex+fragment 2.9-3.5, fixed dispatch costs 120 dispatches x 36 us.

### The 3050, what the logs hold (Market, 100% render, 1440p)

| Number | 100% render (`100b`) | 50% render (`50`) | Reading |
|---|---:|---:|---|
| frames/s | 41.7-44.4 | 50.6-53.4 | |
| draws / dispatches / passes / barriers a frame | 2,113-2,176 / 147-162 / 164-167 / 174-175 | 2,116-2,146 / 135-145 / 161-164 / 168-170 | same scene |
| "GPU time" a frame | 17.5-18.1 ms | 12.6 ms | the plan's 18.1 / 12.6, so ~10.8 ms is resolution-independent (5.5 ms in the pixel work at 100%) |
| longest submission | 4.4-4.5 ms | 2.8 ms | |
| uploads | 0.5-0.7 ms | 0.5 ms | texture uploads and decodes |
| gaps between recordings | 4.7-4.9 ms | 6.5 ms | the GPU idle between pieces (waiting for the replay) |
| GPU tail before the late waits | 13.7-14.1 ms busy | 8.05-8.16 ms | what a readback waits for (ledger) |
| summary `parts_ms`: game / render / gpu_wait | 0.66 / 8.5 / 14.2 | | per frame of a 22.7 ms frame |
| `submissions` (replay thread wall, PROF_FINE) | 13.7 ms (13.9 per frame), of it dry pass 3.25, segments' recording and wait 7.5, 17.9 small buffers recorded whole 1.5, memory work 0.86 | | CPU, below |

## B3. The PC 3050, per pass and per program (Market, 100% render, 1440p; the judge machine)

The 3050 is GPU-bound here: 17.7-18.1 ms of GPU in a 24 ms frame, and `RX_VK_NO_READBACK_WAIT=1` gives only +3% (42.0 to 43.4 fps) because GPU
work then runs back to back at ~22.5 ms a frame (coordinator's measurement). On this machine the lever is GPU work, not the waits.

Serialized `RX_VK_PASS_TIMING` block (`pt2`: 19.46 ms a frame in all against 18.1 ms of unprofiled GPU time, so shares are right and sums
are ~8% high), ms a frame:

| Group | ms | share | Spans | Module it maps to |
|---|---:|---:|---|---|
| **Geometry: render passes** | 11.87 | 61% | 180 spans, 2,220 draws, 66 us a pass | step 2 (draw lists) for the scene passes; list below |
| **Compute: dispatches** | 4.02 | 21% | 153 spans, 26 us a span | the game's own; owners: the meter (step 1), culling (step 3); the rest stays translated |
| Game barriers (149 `nvnCommandBufferBarrier`) | 0.42 (of 0.62 for all barriers) | 2% | | the game's own |
| **Emulation: readback refresh** (`rbrefresh`) | **1.02** | 5.2% | 48 spans, 5.9 MB, 21 us a span | step 1 |
| **Emulation: write-back copies** (`wb`) | **0.93** | 4.8% | 10.7 spans, 52.7 MB, 87 us a span | step 1, then step 2 (pools a module takes over) |
| **Emulation: mirror sync** (`msync`) | 0.25 | 1.3% | 29 spans, 1.2 MB | step 2 |
| **Emulation: upload-buffer barriers** | 0.21 | 1.1% | 124 spans | step 1 and 2 |
| **Emulation: texture uploads** (`xfer`, `mmake`) | 0.19 + 0.05 (+0.16 texture to texture) | 1.3% | | step 4 |
| **Emulation: mesh argument conversion** (`meshconv`) | 0.12 | 0.6% | 24 spans | step 2 |
| Events, uniform-update copies, clears | 0.06 | 0.3% | | |
| **Emulation total** | **about 2.9-3.1 ms** | **15-16%** | | |
| Dry-pass-related GPU cost | 0 | | the dry pass is CPU-only | |

The 3050 pays 5.7 times the 5070's readback-refresh time (1.02 against 0.18 ms; 21 us a scatter against 6.5 us) and 2 times its write-back
time, so the readback protocol's own GPU work (`rbrefresh` + `wb`) is **1.95 ms, 10% of the 3050's GPU frame**, against 0.65 ms on the 5070.
Untimed in these blocks: the present's blit (0.66 ms in the 3050 log), BC/ASTC passes made elsewhere.

### Per-pass ranking (unserialized profile `100c`, Market, 4,228 frames, ms a frame)

The cap keeps the 24 slowest passes of each 10 s batch, so the list sums to 8.5 of the 17.7 ms; the rest is the long tail of small passes and
dispatches (counted in the serialized block above). By kind, listed passes only: main (colour+depth scene passes) 6.14, compute 1.88, post
0.47, shadow 0.01 (the ten small 384x384 shadow passes are in the tail: 0.13 ms serialized).

| Rank | Kind | Pass key | ms a frame | a frame | us each | What it is, module |
|---:|---|---|---:|---:|---:|---|
| 1 | main | 38661608ba66017e | 1.059 | 1.0 | 1,060 | a full-screen pass every frame; its size and time match the serialized pass with first program `1a7482c0ec9991ea` (1.07 ms, 11 samplers, 1,786 fragment instructions, 16 texture ops, P4): pixel work, resolution-dependent, not a draw-list module |
| 2 | main | ef317a5479c11e0b | 0.905 | 0.5 | 1,785 | the heavy scene pass, every second frame; mesh pipelines. Step 2 |
| 3 | main | 647395d47f52b44b | 0.467 | 0.9 | 517 | scene pass. Step 2 |
| 4 | post | 9151d1cc545e89d3 | 0.466 | 1.0 | 467 | the screen effect (`d8180bd8c6ecc77b`, full-screen, 0.48 ms serialized): pixel work |
| 5 | main | c9e43b88b6716a41 | 0.453 | 1.0 | 443 | scene pass (max 3.5 ms). Step 2 |
| 6 | compute | 9dc98871eb7fc101 | 0.419 | 1.0 | 420 | the cooperative-matrix net, grid 64x36x1: the GPU's tensor path, stays |
| 7 | compute | ee7764a6b1ef459c | 0.331 | 1.0 | 332 | grid 8x9x12, 112 global-memory helper sites (P4: 0.4-0.5 ms from hoisting) |
| 8 | main | bf87457d0996edb2 | 0.307 | 1.0 | 308 | scene pass. Step 2 |
| 9 | compute | 27ee9883e2a20323 | 0.290 | 1.0 | 290 | grid 16x9x12, 32 texture ops, stays |
| 10 | main | b624742a9cb7e025 | 0.281 | 11.4 | 25 | 11 small passes a frame (the `c254a02b6a76abe8` family, 20 runs, 0.25-0.33 ms serialized). Step 2 |
| 11 | compute | 171c34b219c9c1a5 | 0.245 | 1.0 | 245 | grid 80x23x1 |
| 12 | main | 0b3bf877b1d267b5 | 0.242 | 2.1 | 117 | scene pass |
| 13 | compute | 26fe0c70ab9fc547 | 0.237 | 1.0 | 238 | grid 80x60x1 |
| 14 | main | b904a551db3bf98a | 0.220 | 1.0 | 220 | scene pass |
| 15 | compute | 435a0138b9013040 | 0.202 | 1.0 | 202 | **the exposure meter's sum program** (1x1x1; 0.11 ms on the 5070). Step 1 removes it with R1 |
| 16 | main | 02c9788dfa1c5fd3 | 0.184 | 0.4 | 485 | scene pass |
| | main | eleven heavy passes: 154f6935, 85583e00, 6c9177fe, 2454dd31, 3021d6c9, 6f1d57bb, 1cd5b84e, f56547ee, d612b7d7, 68ad55de, 5b7d763c | 0.235, 0.205, 0.188, 0.153, 0.152, 0.149, 0.133, 0.111, 0.062, 0.057, 0.044 (1.5 together) | 0.02-0.2 each | 1,100-1,800 | scene passes that run once every 5-50 frames (max 2-3 ms each): they make the long frames. Step 2 |
| | compute | ab7699ee7f536755, 6637ac1d052d22d4 | 0.113, 0.040 | | | stay |

Per program (`pt2`, `RX_VK_PASS_TIMING=2`, serialized draw runs, longest first; ms a frame, draws a frame): `1a7482c0ec9991ea` 1.073 (1
draw, full-screen), `fb7de12443c80159` 0.834 (14.7 draws at 57 us: the heaviest geometry program), `d8180bd8c6ecc77b` 0.476 (full-screen),
`7b8eefac7128cd28` 0.435 (24.9 draws), `17c69696efa907c7` 0.318 (27.4), `46c14f8ee79c5349` 0.315 (60 draws), `3427a1dbefd5e02c` 0.311
(full-screen), `c254a02b6a76abe8` 0.252 (19.7 small passes), `ffb53ed102dd9908` 0.239 (383 instanced draws at 0.62 us), `d8e59341cf4f629c`
0.231 (mesh+fragment, 1 draw), `b89bd6c260cc9318` 0.198, `48ed51830b1d8fb7` 0.198, `e81516ff53914df9` 0.190, `b7914ae65792d0c8` 0.183,
`a14724a987c513eb` 0.155, `e38fe3f7f7659776` 0.155, `c03570482ea9a59e` 0.141, `462cbda9324ed360` 0.117, `ada8fb4146e75f6d` 0.114,
`b54c711bc898254e` 0.111. The top 20 sum to ~6.9 ms of the 11.9 ms of passes: **four programs are full-screen pixel work (`1a7482c0`,
`d8180bd8`, `3427a1db`, `d8e59341`: ~2.1 ms); the rest is scene geometry spread over many programs of 0.1-0.8 ms**. No program is more than
5.5% of the GPU frame.

### What this says about the 3050's ~10.8 ms of resolution-independent GPU time

Serialized shares applied to the 18.1 ms: scene geometry passes ~11 ms (of which full-screen pixel work ~2-3 ms is resolution-dependent), compute
~3.7 ms, our emulation ~2.7-2.9 ms. The emulation is the part no game change keeps and every module removes; the readback protocol (`rbrefresh` +
`wb`) is two thirds of it.

## B2. CPU time by module, one table (ms of CPU a frame; bold = on the critical path)

Main PC = Ryzen 7 7800X3D (REPLAY1, Market, 11.9-12.8 ms frames, 70-72 ms of CPU a frame, ~6 cores busy); 3050 = i5-10400F (24.3 ms
frames; ORDER2's counters from PROF_FINE, and the WPR trace `TRACE1`). Replay phases are summed over the threads that do them.

| Module (what a native module would own) | Main PC ms | PC 3050 ms | Step | Source |
|---|---:|---:|---|---|
| Game: BaseProcExecutor, ModuleSystemWorker, main thread, NavMesh workers | ~27 / ~15 / 3.2-4.0 / ~2.5 (~50 in all) | 4.8 cores busy; the game's threads' ready-queue wait 3.7% only | 3 (visibility job ~4.4 of it), later | REPLAY1 section 1; WPR |
| Visibility / culling job (`f_800998a0` and its readbacks R4) | ~4.4 (the plan) | not split | 3 | `refactor-plan.md`, ledger R4 |
| Readback waits (summed over threads) | 0.9 (Market) / 4.2 / 3.6 | ~16 (389 of 3000 calls x 1.5 ms) | 1 | ledger |
| **Replay: per-draw work in the segment workers** (draw idx 5.64 us a draw x 2,313 = 13.06; 14.5 with all draw sections) | 14.5 | 20.3 (draw idx 9.63 us x 2,112) of 25.8 in the workers | 2 | REPLAY1 section 2; 3050 PROF_FINE |
| - of it descriptors (6.2 pushed a draw) | 7.4 | | 2 | REPLAY1 |
| - of it texture slots (kept 3.94 + resolved again 1.22 + view/sampler misses 1.7) | 3.8-3.9 | | 2 | REPLAY1, DESC1 (no gain from `HL_DESC_PATCH`) |
| - of it program lookup (memo on) | 1.4-1.5 | | 2 | DESC1 |
| - of it user uniform buffers, storage buffers, vertex/index binds, pipeline bind | 1.4 / 0.9 / 0.9 / 0.7 | | 2 | REPLAY1 |
| **Replay: the dry pass** (serial; also writes guest memory: 963 report counters, clears, render-condition reads) | **2.2** (ops 1.69; 43 depth clears 0.65) | **3.25-3.5** | 2 | REPLAY1, ORDER2, 3050 PROF_FINE |
| **Replay: the replay/order thread's own segment + waits for the workers** | **3.76 + 0.95 whole buffers** (submissions 7.7 ms wall) | **7.5 (+1.5 whole buffers); order thread 9.0 of 24.3 ms (37% of the critical path)** | 2 | REPLAY1, TRACE1 |
| Replay: commands, dispatch, programs, textures, pipelines, queue submit | 4.2 / 0.8 / 1.5 / 1.1 / 0.4 / 0.2 | queue submits 14 a frame 0.57 ms | 2, 4 | REPLAY1, ORDER2 |
| Replay: capture, mirror sync, write-back diff | 0.12 per submit (210 pages); sync 0.45 wall; write-back 52.8 MB a frame, 7 MB a submit compared, in the order thread's 2-2.5 | `capture copy/hash worker` 1.27 ms on the path | 1, 2 (they disappear with the pools they serve) | REPLAY1, TRACE1 |
| **Presenter** (game thread that submits and presents) | 4.6-5.5 | **4.2, plus 2.4 woken from timers/DPC**; waits ~10.5 ms for the order thread's replay | 2 | TRACE1 |
| Order thread ("mkw_vk order") | 2.0-2.5 | **9.0** (9.7 CPU, 46% of it in the driver and syscalls) | 2, 4 | REPLAY1, TRACE1 |
| Replay workers (4-5 threads) | 9.9-12.1 | idle 85% | 2 | REPLAY1, TRACE1 |

Not oversubscribed anywhere (3050: 3.7% ready-queue wait). Point fixes inside the current architecture gave nothing measurable
(`refactor-plan.md`: DESC1, ORDER1, WAIT1, PRESENT1, GAMEWAIT1).

## Candidates for step 2 (direct rendering), ranked by measured ms

Ranked by the CPU and GPU time that the module removes **on the 3050**, because that is where the frame is both CPU-path-bound
(order thread 9 ms) and GPU-heavy; main-PC ms in brackets.

| Rank | Candidate | What it removes | Measured ms (3050 / main PC) | Basis |
|---:|---|---|---|---|
| 1 | **The draw loop as records** (draw idx + descriptors + texture slots + uniform binds, per draw in the workers, with the decode of every draw of the ~2,100 a frame) | the per-draw replay: the 25.8 ms of worker CPU (20.3 draw idx) and, because the order thread records 84% of `run_submit` itself, most of the 9 ms critical path | 20.3 (draw idx) of 25.8 worker ms; critical path 7.5 own segment / 14.5 [main PC] | REPLAY1 and 3050 PROF_FINE; TRACE1 |
| 2 | **The dry pass** as a native pass over the records (no state walk twice; counters, clears and render-condition reads moved with it) | the serial 3.25-3.5 ms (2.2) in front of every segment | 3.25-3.5 / 2.2 | ORDER2 (analysis: not a pure walk) |
| 3 | **Mesh/task pipelines on the GPU side** (the drawing of the geometry itself: the program text is the game's, but the *translated* global-memory helper and mesh-argument conversion are ours) | GPU: meshconv 0.10 + the helper's cost: `shader-efficiency.md` measures 0.4-1.1 ms a program for the three biggest mesh programs | ~1-3 / 0.10 (meshconv) | `shader-efficiency.md` (Kokiri, 3050); `meshconv` span here |
| 4 | **Write-back, refresh and mirror syncs of the pools the module takes over** | GPU `wb` 0.47 + `rbrefresh` 0.18 + `msync` 0.17 + u barriers 0.19 = ~1.0 ms (5070, Market, serialized), and the CPU diffs (7 MB a submit) | ~1.0 / 1.0 (shares, 3050 not measured) | PASS_TIMING block above |
| 5 | **Tiny dispatches** (1x1x1 programs: 39 + 20 + 16 + 1 a frame, 4-6 us each; the meter's two sum programs among them) folded or batched by their owning module | GPU latency ~0.4 ms (5070) of fixed cost | ~0.4-1 (120 dispatches x 36 us on the 3050 = 4.3 ms in all, of which the tiny ones are most) | profile JSON; `shader-efficiency.md` |
| 6 | Full-screen/pixel work (d8180bd8, 1a7482c0, da7a7ab2: ~2.0 ms on the 3050) | not a module: resolution-dependent, stays | 0 | `shader-efficiency.md` |

The two largest GPU items, the main scene pass (c9e43b88, 0.34 ms on the 5070) and the game's compute (ee7764a6 0.15, ab7699ee 0.14), are
the game's own work that no step changes; direct rendering removes the **overhead around them** (items 1-4), not their shaders.

## Recommended order of modules, with expected ms, by machine type

The 3050 data splits the ranking. The 3050 is **GPU-bound at 1440p** (GPU 17.7-18.1 ms in a 24 ms frame; removing every readback wait gives
only +3%, 42.0 to 43.4 fps). The 5070 is **CPU-bound** (GPU 3.5-4.4 ms in an 11.5-14 ms frame; the same switch bounds a gain of up to +17%).

| Machine type | What bounds the frame | Levers (ms of frame) | Modules that pull them |
|---|---|---|---|
| **CPU-bound** (fast GPU: RTX 5070 class) | the CPU chain: order thread, replay, readback waits, dry pass | readback waits up to +17% (0.9-4.2 ms summed); per-draw CPU 14.5 ms summed; dry pass 2.2; the replay's own segment 3.8 on the critical path; visibility ~4.4 | step 1 (readbacks), then 2 (draw loop, dry pass), then 3 |
| **GPU-bound** (RTX 3050 class at 1440p) | GPU work: scene geometry ~11 ms, compute ~3.7, **our emulation ~3 ms** | emulation ~3 ms (`rbrefresh` 1.0, `wb` 0.9, `msync` 0.25, `meshconv` 0.12, barriers 0.2, `xfer` 0.2); the 11 heavy scene passes ~1.5 ms and 5 passes of 0.3-1.1 ms; ~60 tiny dispatches a frame at 4-7 us; the translated global-memory helper in the mesh and compute programs (0.4-1.1 ms a program, P4) | step 2 for the scene passes and for the pools' write-back and refresh; step 1 for `rbrefresh` + `wb` (1.95 ms) and the meter's 0.20 ms sum program |

On the 3050 the readback wait is not the lever (+3%), but the readback protocol's GPU work is (1.95 ms, 10% of its GPU frame). Step 1 still pays
there, through R1 and the other readback pages' refresh and write-back, not through shorter waits. At 50% render, where the GPU has slack,
the waits matter again.

| Order | Module | CPU-bound machines (5070) | GPU-bound machines (3050) |
|---:|---|---|---|
| 1 | **Readbacks**: R1 exposure first (same side on the GPU), then R2/R3 one frame late, R6 with the particle job | up to +17% (the waits), plus the bug family #93, white flashes, #50 | -1.95 ms GPU (`rbrefresh` + `wb`, if every GPU-written page the CPU reads goes), -0.2 ms (the meter's sum program), +3% from the waits |
| 2 | **Draw loop and dry pass as records, then Vulkan draw lists for the scene passes** | 14.5 ms summed per-draw CPU and 2.2 ms dry pass; on the 3050 CPU the order thread is 9 ms of the critical path | the GPU's scene geometry (11 ms of passes, ~4-5 ms of it the heavy mesh passes): removes `msync` 0.25, `meshconv` 0.12, barriers 0.2 and the helper cost of the mesh programs (0.4-1.1 ms each, P4): about 1-2 ms, plus whatever owning the draw lists saves per pass (not measurable until a module exists) |
| 3 | **Visibility job** (+ R4) | ~4.4 ms CPU | little GPU effect |
| 4 | Thin NVN layer | residual CPU | residual |
| side | Pixel and compute work no module owns: `1a7482c0`, `d8180bd8`, `3427a1db`, `d8e59341`, `9dc98871`, `ee7764a6`, `27ee9883` (together ~4 ms on the 3050) | | resolution scale and shader-level fixes (hoisting the global-memory helper), outside the refactor's modules |

By ms: on the 3050, step 1's protocol cost (1.95 ms of GPU) and step 2's scene passes (11 ms, part of it ours to remove) lead; on the 5070,
step 2's CPU (14.5 + 2.2 ms and the order thread) and step 1's waits (up to +17%) lead. Step 1 stays first for both: it is the cheapest module,
the only one that pays on both machine types (waits on the 5070, GPU protocol work on the 3050), and it deletes the bug family. Step 2's gain
on the 3050 has to come from a prototype of one scene pass: the profile cannot separate a pass's own cost (the game's shaders, which stay)
from what our translation adds around it (the emulation rows, ~3 ms, about 15% of the GPU frame, which bounds the second).

## Not verified

- The 3050 emulation share is from its own `pt2` block (serialized). Pass keys of the unserialized profile are matched to serialized spans only where signature and ms agree.
- The 5070 profile covers 33 s of Market with the 24-passes-per-batch cap; passes below ~0.04 ms a frame are not in it.
- Pass keys of the JSON profile (target format, size, first program) are not program keys; the table maps them to programs only where
  `shader-efficiency.md` and the `PASS_TIMING=2` list agree (marked). The other passes are named by kind and number.
- The CPU table mixes streams' measurements (REPLAY1 with `HL_THREAD_CPU`, TRACE1's WPR, the 3050's PROF_FINE) taken on different builds
  of the same day; they are cited, not re-measured here.
