# Readback ledger: every GPU-to-CPU data flow the game depends on (STEP0, 2026-11-21)

Step 0 of `refactor-plan.md`. Question: which GPU results does the game's CPU read in play, who writes them on the GPU, who reads them,
how often, how big, how long does the CPU wait, which bugs hang on them, and how does each one go away (same side on the GPU, computed on
the CPU, or one frame late and deterministic). Nothing in the engine changed for it: diagnostics that already exist, switched on.

## How it was measured

Main PC (RTX 5070, Ryzen 7 7800X3D), whole machine under a speed lease, slot b, build of local `main` d9c5974d9 (no code change),
save `market-mid` (and `kakariko-village`, `hyrule-field` from `build/s5/savelib`), scene `market-spin` (camera turning in place),
options `dlss=0 upscaling=0 resolution=1 hdr=0 vsync=0`, no frame cap ("Render resolution: 100% chosen, drawn at 100% with Upscaler 0"),
110 s runs. Diagnostics on: `RX_VK_RB_PROF=1` (every CPU read of GPU results, timed by phase, by thread, by site and by range; a line every
300 presents), `HOS_RB_LR_LOG=1` (the reads by guest return address, size and thread), `RX_VK_LOG_WRITEBACK=1` (the pools written back),
`RX_VK_EXPOSURE_LOG=2`, `RX_VK_PASS_TIMING=1` (the GPU's own spans). The speed of these runs (Market 64, Kakariko 96, Hyrule
field 85 fps with the diagnostics on) is not a benchmark. The PC 3050 numbers come from the logs the coordinator put in
`E:\OOT\scratch-step0\3050\` (100% and 50% render, Market, 1440p; the 3050 runs `RX_VK_RB_PROF` off, so only the summary line is there).
`HL_TRACE` (the crash recorder) is not a readback tool and was not used.

What a "readback" is here: the game calls `nvnMemoryPoolInvalidateMappedRange` on a range the GPU wrote (on the console the memory is
shared, the call is free). On the PC the engine must make that range current: wait for the replay to reach the recording that writes it
(`ticket` and `recorded` phases), then for the GPU to finish (`late fix` = the GPU's completion of the previous frame's heavy submission,
whose last piece is the write-back), then read the bytes. Plus the mirror-side work the protocol needs (`rbrefresh`: the CPU's zero written
back into the mirror before the GPU adds again).

## Per-frame totals (RX_VK_RB_PROF "all", mean of the last 3 windows of 300 presents, play phase)

| scene | fps (diag on) | invalidate calls a frame | of them on GPU-written ranges | pending (writer not done) | summed wait over threads, ms a frame | of it: ticket / recorded (late fix) |
|---|---:|---:|---:|---:|---:|---|
| Market (5070) | 64 | 80.9 | 52.9 | 9.1 | 0.93 | 0.60 / 0.20 (0.07) |
| Kakariko (5070) | 96 | 48.2 | 20.2 | 10.6 | 4.23 | 1.45 / 2.70 (2.48) |
| Hyrule field (5070) | 85 | 29.3 | 19.3 | 12.2 | 3.60 | 0.73 / 2.78 (2.52) |
| Market, PC 3050, 100% render | 44 | ~81 | | | ~16 (389 of 3000 calls waited, 1.46-1.63 ms each; ~10 waiting calls a frame) | not split |
| Market, PC 3050, 50% render | 52 | ~81 | | | ~8 (281-311 of 3000 waited, 1.03-1.16 ms each) | not split |

Reading it: the waits are summed over the threads that read (the main thread and five ModuleSystemWorkers wait at once), so the loss on the
frame's chain is smaller than the sum; but the same reads wait on the same GPU completion, so removing a few of them shortens nothing
(`readback.md`: `READBACK_LATE` +0.6%, `NO_READBACK_WAIT` +17% as the bound). Market on the 5070 hardly waits (the GPU is done before the
CPU asks); Kakariko and Hyrule field, where the CPU is faster than the GPU tail, wait 3.6-4.2 ms summed, 2.5-2.8 of it the late fix. On the
PC 3050 the same reads cost ~16 ms summed: the log's "GPU tail before the late waits" is 13.8 ms busy with no gap at 100% render and 8.1 ms
at 50% (9.7 recordings each), i.e. **on the 3050 a readback waits for most of the previous frame's GPU work**, which is why it is the
enabler for overlapping the CPU phase with the GPU (the 3050's summary row: game 0.66 ms, render 8.5, gpu_wait 14.2 ms of a 22.7 ms frame).
GPU cost of the protocol itself (5070, serialized spans): `rbrefresh` 0.18 ms a frame (27 spans, 5.9 MB), the write-back copies
`wb` 0.45-0.47 ms (10.8 spans, 53 MB a frame) and their barriers, see `gpu-pass-cost.md`.

## The ledger

Sizes are the `invalidate` range's size; a "site" is the guest function that asked (return address inside it, `HOS_RB_LR_LOG`), the
function names are the ones the existing notes use (`readback.md`, `gfx-record-job.md`, `critical-jobs.md`). Calls a frame are from the
Market run (calls/frame 80.9; the share of each caller is the last 1,500 log rows of that run, the rest of the scenes in the notes).
The wait columns are `RX_VK_RB_PROF` per range, ms a frame, Market / Kakariko / Hyrule field (5070).

| # | Record (range) | Calls a frame | Producer on the GPU | Consumer (guest) | Wait ms a frame M / K / H | Bugs and mitigations in the engine |
|---|---|---|---|---|---|---|
| R1 | **Exposure meter**: `0x1040` blocks (+ `0xc0` companions), one ring, 3 blocks and 2 companions a frame (the one the wait profile names: `0x113c88b000+0x1040`, 1 call a frame) | 3.1% of calls (about 3 + 2) | Two 1x1x1 compute programs, `435a0138b9013040` and `592079442d7129da` (the engine's own special case in `vk_draw.cpp`, `meter_out`): add the frame's light sums into the first 16 bytes of the block. Each dispatch ~0.11 ms (Market, 5070) | the ring helper `f_80357060`, 7 callers (lr `0x80357c80`, `0x80357cc8`, `0x80357d10`, `0x80357d58`, `0x80357df8`), on the module workers and the main thread. It **reads the sums, then stores 0 back** (read-and-reset protocol) and fills in the rest of the block; the adapted exposure goes to the final pass's constants (`c3+0x38` of the final fragment stage) | 0.22 / 1.22 / 1.05 (late fix 0.02 / 0.73 / 0.74) | White flashes and the one-frame exposure pulses (a skipped or stale read doubles the next sum: 78, 163, 254 in sprint 5's log), **#93 night flicker** (the same doubled-sum family; ~1 pulse in 10,000 frames even with the fixes). Mitigations: `RX_VK_METER_ZERO` / `RX_VK_METER_HOLD` (sums cleared on the GPU before each program, a zero read given the last result again), `RX_VK_READBACK_STRICT` (+`STRICT_BOUND` 4 ms), `RX_VK_NO_RB_REFRESH` off (the refresh), per-part refresh (TICK-006) |
| R2 | **Small result ring**: `0x40` ranges, many adjacent ones in a row (`0x10508d8000+0x40`, `0x105183f000+0x40`, `0x10517fc000+0x40`, `0x10518e4000+0x40` by scene) | **~65 (80% of all calls in Market, 64% Kakariko, 40% Hyrule)**, one wait covers the run | Not determined here. In Market the range is not a write-back one (0 of 1 calls with write-backs); in Kakariko/Hyrule field it is (1.0 with write-backs, pending 0.7-0.9). A GPU compute result of 64 bytes records; to be traced (method below) | `f_80354060` (lr `0x80354174`; the readback.md "0x40 range from many addresses in a row"), reached from the module job `f_8007c120` ("GPU memory invalidate") | 0.18 / 0.77 / 0.79 (Market: ticket only, the wait is the replay lock, not the GPU) | none named. It is 2 ms of 8 summed in `readback.md`'s castle-move table. Mitigation: `RX_VK_READBACK_LATE=2000,40` exists and gives nothing alone |
| R3 | **Alternating `0x2000` pair** (`0x113c895000`, `0x113c897000`, one each frame by parity) | 1 a frame (2.5% of calls) | not determined (a GPU-written pool, `kUseGpuWrite`, read next frame) | `f_8034f440` (lr `0x8034f658`) and `f_8007c120` (lr `0x8007c4b8`), module workers + main thread | 0.11+0.10 / 0.63+0.66 / 0.52+0.54 | none named; `READBACK_LATE` names it |
| R4 | **Culling/visibility results**: two `0x3980` pools (`0x113b623000`, `0x113b628000`), 1.4-2 calls each a frame (7% of calls in Kakariko, 12% in Hyrule field) | 1.7+1.8 | GPU compute that fills the culling result (the engine's dry pass counts these writes under `kUseGpuWrite`) | the culling / render-list job `f_800998a0` and `f_80365f00` (lr `0x80366168`, `0x8036635c`, `0x803665d4`, `0x80099d84`) | 0.02 / 0.02 / 0.02 (0.9 pending but the GPU is done) | none named. This is the *visibility job* of step 3 (~4.4 ms of CPU), so the readback and the CPU consumer go together |
| R5 | **Big GPU-written lists**: `0x9b040`, `0x8b040` (twice each, two ring halves `0x117cf04000`/`0x117cf9f040`), `0x44b040`, in the 112-160 MB pool `0x1178000000` | 1.5 a frame (0.5 each) | GPU compute (instance/draw data), written back 4.3-5.2 MB a submit; this pool is the main write-back (`RX_VK_LOG_WRITEBACK`: 4.3 / 4.7 / 5.2 MB a submit of 52.8 MB a frame in all) | `f_80362ff0` (lr `0x803633cc`) | 0.01 each (pending 0.5 each) | none named; large reads, not a wait problem (0.01 ms) but 5 MB a submit of write-back copies and compares |
| R6 | **Particle/debris state, `0x1b580`** (`0x115bcde000`, `0x115bcf9580`, 4 MB pool) | 1 a frame (0.5 each) | GPU compute that updates the state the CPU also appends to | `f_80364080` (lr `0x8036473c`): the slot-table scan plus `DispatchCompute` plus an invalidate | 0.01 / 0.01 / 0.01 | **#50 cut grass** (leaves and debris): `RX_VK_WB_POOL_EAGER` (on by default; "a pool whose GPU words are left out, a particle effect's state, issue 50's cut grass"). The tie to R6 is inferred from that note and from what `f_80364080` does (`gfx-record-job.md`); it is not proven by a log here |
| R7 | **Samples-passed reports and render conditions** (occlusion): ~963 reports and ~445 conditions a frame (dry pass counters) | not invalidates: written on the CPU | The console's GPU writes the counts. **The engine does not read them back:** `RX_VK_OCC_FIXED` (default 4096, "everything visible") is written in stream order on the CPU | the game's render conditions (445 a frame) | 0 (no wait) | the fixed value is a deliberate approximation: it ended the sun-visibility flicker (1 frame in ~70) but draws test-occluded geometry. `RX_VK_OCC_REAL=1..5` (off) measures real counts with Vulkan queries; mode 5 is "one frame late by the report's place in the frame" and is the readback form of this row |
| R8 | Textures the GPU rendered and the CPU reads | ~0 | render-target copies | texture catch-up in `invalidate` ("tex" phase) | 0.00-0.03 | none seen; no `tex-rb-submit` in a traced frame |
| R9 | Fence polls (the game asks whether a submission is done, answered "done" early from the recording) | many | the replay's own bookkeeping | the main thread and workers | n/a (answered from the recording: `direct-emission.md` section 12) | a wrong "done" is the stale-meter bug; `RX_VK_READBACK_TICKET` keeps the order |
| W | **Write-back of GPU-written pages in general** (not an invalidate: the replay copies the mirror's GPU words back into guest memory so any later CPU read sees them) | 10.8 spans, 189-202 copy runs a frame, 53 MB a frame, 7 MB a submit compared (`wb` 0.45-0.47 ms of GPU) | every compute and pass that writes a bound range | whatever reads that memory next, including R1-R6 | in the readbacks above | `RX_VK_WB_POOL_EAGER`, `WB_REPORTS`, cpu-owned words (the one-frame dark flashes of 11-07: counters written back over the CPU's) |

Where the site and the size alone say what the data is, they were used; **the producer program of R2-R6 was not identified in this
step** (the diagnostics log the reading side and the pools; they do not log which dispatch wrote a range). Method to settle them, as an
add-on diagnostic (default off): at each dispatch/draw, when a bound storage range overlaps a range that an `invalidate` later reads
(`vk_memory.cpp`'s `rb_pages` / `memory_note_gpu_write`), log the program key and the range once; one Market and one Kakariko run give the
table. The guest-side consumers (`f_80357060`, `f_80354060`, `f_8034f440`, `f_80362ff0`, `f_80364080`, `f_80365f00`, `f_800998a0`) are
already named in `gfx-record-job.md` and `critical-jobs.md` as the module job family `0x8034xxxx-0x8036xxxx` driven by `f_8007c120`.

## What each readback costs, ranked (5070 summed ms a frame, 3 scenes; 3050 is ~4-8x more per call)

1. R2 (the `0x40` ring): 0.18 / 0.77 / 0.79, and the largest *count* (65 calls a frame).
2. R1 (exposure meter): 0.22 / 1.22 / 1.05, the only one with a correctness protocol (read-and-reset).
3. R3 (`0x2000` pair): 0.21 / 1.29 / 1.06.
4. R4-R6: 0.02-0.06 each: not waits, but their consumers are the culling job and the particle job.
All of the waits are the *same* GPU completion (`late fix`, the last piece of the previous heavy submission, 2.5-2.8 ms in the fast
scenes), which is why the only gain measured is "all or nothing".

## Removal strategy per readback (refactor-plan.md's three options), with risk

| # | Strategy | Why | Risk | Check |
|---|---|---|---|---|
| R1 | **1. Same side on the GPU**: a native module owns measuring (the two sum programs), adapting (the CPU adaptation logic moves to a small GPU pass or a native step run when the sums are ready on the GPU) and applying (the final pass's exposure constant comes from the GPU buffer). The 0x1040 block's CPU contract (read, store 0) is replaced by a GPU-side reset. First module, as the plan says | the one read with an exact protocol; every mitigation so far (zero, hold, strict, refresh) exists to emulate shared memory | **medium-high**: the adapted exposure must be bit-equal to the translated adaptation (`_CHECK` mode, 0 mismatches), and the CPU side must not use the exposure for anything else (light, fog, sky): to be read in `f_80357060`'s callers before building. No gameplay logic reads it as far as the notes show (not proven) | `HL_EXPOSURE_CHECK=1` style: run both, count mismatches; frame-log luma steps (the existing 1-in-10,000 pulse is the baseline); `RX_VK_SHOTS` pictures in 3 scenes |
| R2 | **3 (one frame late) or 1**, after the producer is identified. If the ring is a visibility/culling count the CPU only uses as a hint, one frame late with the same key as R4 | 65 calls a frame of one waiting range; readback.md showed one-frame lateness for `2000,40` gives no gain *alone* because the meter still waits: so it only pays together with R1 | medium (unknown consumer semantics) | with R1 done, `READBACK_LATE=40` plus frame-log |
| R3 | **3 (one frame late)**, alternating by frame parity already (the game reads what the previous frame wrote); proven one readback at a time | `READBACK_LATE=2000` gave a right picture in castle-move | low-medium | frame-log, 3 scenes |
| R4 | **2 (CPU-computed) with step 3**: the visibility job native on the CPU makes its own result; the GPU culling result is no longer read | the consumer *is* the visibility job (`f_800998a0`, ~4.4 ms of CPU); a native version should not need the GPU's help | medium-high (the job's full logic) | `_CHECK` of the culling list against the translated one |
| R5 | keep write-back, drop the compares: **1** when the draw lists are produced by the native module (step 2), the GPU-written lists then need no CPU read | 5 MB a submit of copies, 7 MB compared | medium | none needed until step 2 |
| R6 | **2 or 3**, with the particle job (#50): the native module owns the CPU appends and the GPU update; if the CPU only needs the count, one frame late with a deterministic ring | cut grass depends on eager write-back of a half-GPU, half-CPU page | medium-high (gameplay-visible: the leaves) | the cut-grass scene with `RX_VK_WB_POOL_EAGER=0` as the stress case, frame-by-frame pictures |
| R7 | **3** (mode 5 of `OCC_REAL`) only if "everything visible" proves too coarse for step 2's overdraw; otherwise keep fixed | no wait today; real counts cost an occlusion query per draw | low (off by default today) | `RX_VK_OCC_LOG` dumps |
| W | goes away with each producer module (no GPU-written page left to copy back) | 0.45 ms GPU a frame + write-back diffs on the CPU | follows R1-R6 | write-back MB a submit falls |

## Not verified

- The producer programs of R2-R6 (above), and whether R2's `0x40` range in Market is GPU-written at all (0 of 1 calls with write-backs
  there, 1 of 1 in the other two scenes).
- The 3050's per-range waits: its runs had `RX_VK_RB_PROF` off; its per-call average (1.46-1.63 ms) and the GPU tail are in its log, the
  split by range is not. A 3050 run with `RX_VK_RB_PROF=1 HOS_RB_LR_LOG=1` (110 s) would give it; no extra setting.
- #93 and the white flashes are tied to R1 by the existing notes (`readback.md`, `SWITCHES.md`), not re-reproduced; 0 pulses were looked
  for in these runs (no frame log).
- Wait ms are with the diagnostics on (`RB_PROF` takes a stack trace per call), so they are slightly high; the counts are exact.
