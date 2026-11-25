<!-- Copyright (C) 2026 Sacrappy -->
# PIPE1: overlapping the game with the replay, the recorders' contention, the dry pass (23 Nov 2026)

Branch `perf/pipe1` from `refactor/integ4` (133566ac7). Main PC (RTX 5070, Ryzen 7 7800X3D), speed leases (priority 2). Builds: `integ4b`
(the integ4 tip without PIPE1, series s1), `pipe1a` (this branch: `RX_VK_TOUCH_FAST`, off by default, and the dry pass's mark timers; same
shader and state stamps as integ4b), `sub1` (refactor/sub1 673c5e2d3, SUB1 Stage B, in a slot of its own). Scenes `market-spin` from
`market-mid`, `castle-move` from `hyrule-castle`, `kokiri-idle` from `kokiri-forest`, the build's `HL_*` defaults. fps from "play: began" + 10 s.
Job traces: `HOS_JOBTRACE` (85-90 s of a run), read with `tools/bench/chainb.py` and `critpath.py` and with this stream's scripts in
`E:\OOT\scratch-pipe1` (`loop.py`: the main thread's and the Presentation Thread's frame by what each waits for; `waits.py`, `sig.py`: who ends a
wait; `tl.py`: a timeline; `pl.py`: the ParLock and dry-mark tables; `fps.py`). Profiles (`RX_VK_PROF_FINE`, `RX_VK_PARLOCK_STATS`) are separate
runs from the fps runs. A traced window runs at the run's own rate (Market: 87 fps in the window, 89 fps over the run).

## Summary

1. **The game already overlaps frame N+1's game work with frame N's replay.** 72-89% of the main thread's wait for the module workers (the
   game's job phase, 2.8-5.7 ms a frame) falls inside the Presentation Thread's (PT's) replay. What is left is a two-stage pipeline: the main
   thread's loop (the game) and the PT's chain (the replay, run inline in its submits, and the present), joined once a frame each way (main -> PT:
   the frame's hand-over, a game queue; PT -> main: the game's "present wait" event). The frame is the longer of the two stages, not their sum.
2. **The Market on the 5070 is bound by the game, not by the replay.** Main thread loop 11.1 ms, PT chain 9.65 ms: the PT waits 1.8 ms a frame
   for the main thread (3.6 ms with `RX_VK_FRAMES_IN_FLIGHT=2`). No change to the replay can raise the Market's fps on this PC until the game's own
   loop is shorter. This is why FIF1, SUB1, MID_SEG, TEXRES1, DRAW1, ORDER3's knobs and BIND1's estimates all came out at 0 in the Market.
   **The castle and Kokiri are bound by the replay**: PT chain 7.3 / 8.3 ms against a game loop of 6.5 / 7.1 ms.
3. **SUB1's asynchronous submit gave 0 because its work moved to the present on the same thread** (PT submit 7.65 -> 5.34 ms, PT present
   1.20 -> 3.02 ms) in a scene where the PT already waited 1.4-1.6 ms for the game. The old `RX_VK_ASYNC=1` (a replay thread of its own) is slower
   (67 fps): the game then waits on its own polls.
4. **Contention**: a texture's first look of a submission took ParLock ~340 times a big command buffer. `RX_VK_TOUCH_FAST` answers 99% of
   them without the lock (0 disagreements in 6.4 M looks checked under the lock, three scenes): ParLock takes 610 -> 280 a big command buffer,
   a segment's first draws' locks 1.08 -> 0.39 a draw, the Market's replay thread -0.6 ms a frame. fps inside the spread in every scene
   (Market +0.2%, castle +0.8%, Kokiri +0.5%; PC 3050 equal): **on by default** (=0 off) under the owner's rule. The lock's wait partly moves to
   its other places: the next lever is ParLock split by what it guards (section 8).
5. **The dry pass's target marks are 0.25 ms a frame inside the mark** and ~0.3 ms more that follows the first mark of new targets wherever
   it is (a clear or a draw) but is outside the mark's timed parts (section 5). In the replay-bound scenes the dry pass is 0.54-0.96 ms of a
   7.3-8.3 ms chain; the replay thread's own segments (2.5-3 ms) are the larger item there.
6. **Proposed**: `RX_VK_TOUCH_FAST` on (done here). The +30% does not live in the replay on the 5070: the Market needs a shorter game loop
   (main thread + EX0 job + module jobs, 11.1 ms); the replay-bound scenes can gain up to +10-21% from moving the replay off the PT (section 3,
   2-4 weeks with its gates), less from shortening the PT's chain (castle knobs, section 7).

## 1. The frame's two stages (job traces, ms a frame)

Per frame (cut at the main thread's first held submit), what each thread waits for (`loop.py`; lr = the game's caller of the wait):

| | Market (integ4b) | Market, FIF=2 | castle | castle, FIF=2 | Kokiri | Kokiri, FIF=2 |
|---|---:|---:|---:|---:|---:|---:|
| frame | 11.49 | 10.96 | 7.59 | 8.42 | 8.58 | 8.89 |
| main: waits for the module workers (lr 80706d40) | 5.66 | 5.37 | 2.81 | 3.01 | 2.75 | 2.75 |
| main: waits for EX0's job (lr 802ead44) | 1.67 | 1.57 | 0.99 | 1.16 | 1.15 | 1.19 |
| main: module polls (lr 8009a478) + other waits | 0.90 | 0.86 | 0.86 | 0.91 | 0.92 | 0.99 |
| main: **present wait** (lr 81207a6c, signalled by the PT) | 0.36 | 0.13 | **1.07** | 0.85 | **1.48** | 0.99 |
| main: running | 2.91 | 3.03 | 1.86 | 2.49 | 2.28 | 2.97 |
| **game loop** (frame - present wait) | **11.13** | 10.83 | 6.52 | 7.57 | 7.10 | 7.90 |
| PT: its submits (the replay inline, marks 1/2) | 7.35 | 6.56 | 6.17 | 7.02 | 6.92 | 7.06 |
| PT: present (marks 5/6) | 1.13 | 0.00 | 0.54 | 0.00 | 0.48 | 0.00 |
| PT: running, other | 1.17 | 0.82 | 0.64 | 0.84 | 0.93 | 1.23 |
| **PT: waits for the main thread's hand-over** (queue lr 834a3084) | **1.84** | **3.58** | 0.25 | 0.55 | 0.26 | 0.60 |
| **PT chain** (frame - that wait) | **9.65** | 7.38 | **7.34** | 7.87 | **8.32** | 8.29 |
| main's module-worker wait inside the PT's submits | 84% | 89% | 85% | | 72% | |
| fps of the run (play + 10 s) | 89.3 | 90.8 | 123.1 | 119.3 | 112.0 | 110.9 |

How a frame goes (`tl.py`, Market): the main thread hands its frame over (two held submits, then the queue message the PT waits for), then waits
~5 ms for the module workers (the game's job phase, running while the PT replays the frame just handed over), runs ~2-3 ms, posts a job to EX0
and waits for it (~1.6 ms), and hands the next frame over. The PT receives the frame, replays its submits (the held ones first, on the held
submits' thread under `flush_cs`), presents, signals the game's present event and waits for the next frame. Who ends each wait (`sig.py`): the
main thread's lr 80706d40 waits are ended by the module workers MW1-MW5 (~10 a frame), its 802ead44 wait by EX0, whose job queue is fed by the
main thread itself (once a frame); none of them by the PT. The only PT -> main edge is the present wait.

So the stages are coupled only at the hand-over and at the present wait, and **the frame = max(game loop, PT chain)** plus the waits that
coupling adds. The Market's game loop (11.1 ms) is longer than its PT chain (9.65 ms); the castle's and Kokiri's PT chains (7.3, 8.3 ms) are
longer than their game loops (6.5, 7.1 ms), and there the main thread waits 1.1-1.5 ms a frame for the PT's present instead.

`critpath.py` attributes 80% of the Market's path to the PT (8.1-9.3 ms) because the walk back from a present runs through the PT's contiguous
replay to the hand-over; that is right for one frame, but the next hand-over comes at the main thread's cadence, so a faster PT only waits
longer. The throughput is set by the longer stage.

Interference (is the game's job phase slowed by the replay's threads?): with `RX_VK_PARALLEL=0` (the replay on the PT alone) the main thread's
module-worker wait is 5.50 ms against 5.66 with five recorders: the recorders cost the game ~0.15 ms a frame of its job phase, little. (That
run is replay-bound: PT submits 11.3 ms, the main thread waits 2.75 ms for the present.) A `RX_VK_PARALLEL=3` run was disturbed (55.8 fps,
121 ms p99) and is not used.

PC 3050 (Task 39, 1080p, 100%, upscaler off; traced, ms a frame):

| | Market | Market, FIF=2 | castle | castle, FIF=2 |
|---|---:|---:|---:|---:|
| frame | 19.51 | 20.86 | 18.66 | 19.50 |
| main: module workers / EX0 / polls+other / present wait / running | 9.91 / 2.59 / 1.78 / 0.72 / 4.51 | 9.86 / 2.75 / 2.11 / 0.91 / 5.22 | 5.94 / 2.16 / 2.64 / **4.16** / 3.76 | 5.96 / 2.39 / 2.58 / 3.96 / 4.61 |
| game loop | 18.8 | 19.9 | 14.5 | 15.5 |
| PT: submits / present / other | 13.93 / 1.62 / 2.15 | 15.73 / 0.01 / 2.15 | 15.55 / 1.02 / 1.70 | 16.70 / 0.01 / 2.02 |
| PT waits for the hand-over | 1.81 | 2.97 | 0.38 | 0.78 |
| PT chain | 17.7 | 17.9 | 18.3 | 18.7 |
| fps (untraced A/B runs, Task 39) | 50.9 | | 58.7 | |

The same picture: the 3050's Market is just game-bound (1.8 ms of slack), its castle replay-bound by 3.8 ms; FIF=2 makes the PT's submits
longer (+1.1-1.8 ms) and gives nothing (Task 39: Market 50.3 -> 50.8, castle 58.5 -> 57.9 fps, p99 +2-4 ms).

## 2. What stops the game from going on while frame N is replayed

- **Nothing in the Market**: it does go on; the PT is the one that waits.
- **In the replay-bound scenes, the game's present wait** (lr 81207a6c): the main thread does not hand frame N+1 over before the PT has
  finished frame N's submits and its present call and signalled the game's event. That is the game's own frame pacing (one frame in flight
  between its two threads), not a fence or a readback: the main thread's two `nvnSyncWait`s a frame (lr 8042cde0, 8042ce34) are already
  signalled at the first look in every run (traces' marks 30/31: 2.0 a frame, all "signalled").
- **The fence that would stop it under an asynchronous replay** (`HOS_NVN_SYNC_LOG`, Market): right after its hold the main thread waits,
  with no timeout, on the fence the PT took after its previous frame (lr 8042cde0: `FenceSync` by the PT at lr 805903ac -> 42007, then the main
  thread's `SyncWait` 42007 with "latest 42008"); the other wait (lr 8042ce34) is on fence 0. So the game itself allows one frame between its
  hand-over and the replay: frame N+1's game work may run while frame N is replayed, not further. An asynchronous replay keeps that: the wait is
  answered when frame N's recording is done (`wait_recorded`), which it is by the time the main thread has run its frame.
- **The PT's own chain**: the PT replays inline (`submit_q` under `sync_order_mu`), so its game code, its present and the replay of the held
  submits (`flush_cs`, 0.5-0.9 ms a frame in the castle and Kokiri: "held flush" in `chainb.py`) are in series with the replay.
- **The command memory and the pool memory the game rewrites** are already covered: each recording is a host copy (`flatten` at
  `nvnCommandBufferEndRecording`) that is freed only when the replay has passed the submits made before its release (`dead_copy_add`,
  `rx_nvn_replayed`), and the guest pages the CPU wrote are captured at the submit (`memory_capture_submit`, 0.12-0.43 ms a submit, on the
  submitting thread). The snapshot an asynchronous replay needs is therefore already paid for; the risk is the reads that do not go through the
  capture view (SUB1 Stage A made the view per submission for the segment workers; mirror syncs follow `RX_VK_SYNC_CAPTURE`).
- **The game's polls of GPU-signalled events**: with `RX_VK_ASYNC=1` the PT polls its frame's event (lr 80590520, 1.67 ms a frame) and the main
  thread's module polls grow 0.72 -> 2.43 ms, event lr 80070d5c 0.09 -> 1.06 ms: the game reads what the replay has not written yet.
  `HOS_PRESENT_NOWAIT` answers the PT's poll (lr 805904a0/80590530) at once; the others have no such answer.

### Why SUB1 (and FIF1) gave nothing

SUB1 Stage B (`RX_VK_SUB_OVERLAP=1`, its own build in slot b; U0 = Stage A, U = Stage B, Market, traced):

| | U0 | U |
|---|---:|---:|
| frame | 11.43 | 10.98 |
| PT submits | 7.65 | **5.34** |
| PT present | 1.20 | **3.02** |
| PT waits for the main thread | 1.44 | 1.61 |
| main: module-worker wait / EX0 / present wait | 5.53 / 1.67 / 0.52 | 5.35 / 1.54 / 0.27 |
| fps of the run | 84.6 | 84.1 |

The submission returns with its last segments recording, and the present (which must have the frame's work whole before it shows it) waits for
them: 2.3 ms moved from the submits to the present, on the same thread. And in the Market the PT had 1.4 ms of slack already. FIF=2 moves the
present to a thread of its own; in the Market the PT then waits 3.6 ms for the main thread (no gain), and in the castle and Kokiri the PT's
submits grow by 0.1-0.85 ms (the present thread's drain and present hold `sync_order_mu` while the PT's next submit wants it) and the main
thread still waits for the present event: 119.3 / 110.9 fps against 123.1 / 112.0 (one run each).

## 3. The overlap design (staged, with gates)

What can still be won, scene by scene: the replay-bound scenes' PT chain above their game loop, 0.8 ms (castle) and 1.2 ms (Kokiri); in the
Market nothing until the game loop shrinks. Moving the replay off the PT (an asynchronous submit) makes the replay stage the replay alone (PT
submits 6.2-6.9 ms) and takes the PT's present, its own game code and the held flush (1.1-1.9 ms) out of it. Upper bounds:

| scene | today | game loop | replay alone | frame with an asynchronous replay (max) | gain |
|---|---:|---:|---:|---:|---:|
| Market | 11.49 | 11.13 | ~7.4 | 11.1 | 0-3% |
| castle | 7.59 | 6.52 | ~6.2 | ~6.5-6.9 | +10-16% |
| Kokiri | 8.58 | 7.10 | ~6.9 | ~7.1-7.4 | +16-21% |

Stages and gates:
- **Stage 0 (measure, cheap): a per-frame counter of each stage's slack**, without a job trace: the PT's wait for the hand-over and the main
  thread's present wait (both are guest waits the host sees: `hos_waitnote`), logged with the frame statistics. Gate for every later stage: the
  scene is replay-bound (present wait > 0.5 ms a frame). On the 5070 today: castle, Kokiri yes, Market no.
- **Stage 1: the PT's submits replayed by an order thread, the PT returning after the capture.** `submit_q` takes the capture and notes the
  waits (as `RX_VK_FRAMES_IN_FLIGHT`'s signal capture does), hands the submission to the order thread (the sync-waits model kept: `qruns`,
  `burst_*`), and the present becomes a job of the same thread (FIF's present job, without the second thread that contends for
  `sync_order_mu`). Needs: `rx_nvn_pushed` to count handed-over submissions (the recording copies' lifetime follows it already); every game
  wait that the replay answers (fence waits, `nvnQueueFinish`, event polls, `wait_queue_empty`) to wait for the order thread's progress
  (`sub_join` of SUB1's list); the game's present poll answered at once (`HOS_PRESENT_NOWAIT`); the readback ring at L = 3 (FIF1). Gate: castle
  and Kokiri +8% or more, Market not below the spread, 0 flashes in the frame log over 4 zones (the old `RX_VK_ASYNC` had 28 a 1000 frames).
- **Stage 2: every guest read of the replay through the submission's capture view** (texture uploads, mirror syncs, the readback ring, the
  render conditions' operands), with a check mode comparing what a read got with the page's captured bytes (SUB1's per-submission view and
  `HOS_NVN_HOLD_CHECK` are the base). This is what the old async path's flashes were (OoT rewrites double-buffered data right after a submit).
  Gate: the check reports 0 differing reads in 4 zones and a zone change.
- **Stage 3: the main thread's held submits on the order thread too**, so the PT never waits in `flush_cs` (0.5-0.9 ms a frame in the
  replay-bound scenes). Gate: as stage 1.
- Effort: stage 0 one day; stage 1 one to two weeks (the waits, the present job, the order thread's ownership of the replay's globals that
  `submit_q` and `present` take today); stage 2 one week plus the picture checks; stage 3 two days. Risk: the old asynchronous path's black and
  white frames, the light meter (FIF1's ring needed L = 3), hitches at zone loads (the order thread's queue grows while the game loads).

What would make the Market move instead is the game loop: the main thread runs 2.9 ms, waits 1.7 ms for EX0's job (posted by itself, a serial
step) and 5.7 ms for the module workers. That is the lifter / native-module work's territory (`critical-jobs.md`), not the replay's.

## 4. Parallel efficiency: where the recorders' time goes, and RX_VK_TOUCH_FAST

### 4.1 The counters (Market, pipe1a, switch off: run s3 mA; `RX_VK_PARLOCK_STATS`, means of 45-55 windows, a big command buffer)

| ParLock place | taken | waited | waiting ms | held ms |
|---|---:|---:|---:|---:|
| vk_draw.cpp:3971, a texture slot's first look of the submission (`texture_touch`) | 177.5 | 24.7 | 0.259 | 0.026 |
| vk_draw.cpp:2954, a view hit's first look (`texture_view_of`) | 160.9 | 23.7 | 0.161 | 0.022 |
| vk_draw.cpp:4768, `targets()` | 93.0 | 7.3 | 0.036 | 0.225 |
| vk_draw.cpp:2963, a view miss (`texture_view_of` resolving) | 46.0 | 6.7 | 0.038 | 0.122 |
| vk_draw.cpp:8135, the program pick | 14.8 | 2.5 | 0.085 | 0.018 |
| everything | 610 | | 0.658 | 0.594 |

A draw by its place in its segment (us, ParLock takes a draw): 0-31 9.95 (1.07), 32-63 6.53 (0.72), 64-95 5.69 (0.59), 96-127 5.28 (0.56), ...,
288+ 1.89 (0.08). The segments' first draws are 3-5x dearer than the late ones, and they take a lock a draw: the first look of each texture
of the submission (380 a big command buffer, `texture_touch_quick` failing on `checked_epoch`) lands where a segment starts, because each
segment is a new part of the stream for its thread. That is the "5.8 against 3.2 us a draw" of BIND1: the serial replay sees each texture once a
submission on one thread, warm; five recorders see it five times, cold, and the first of them takes ParLock to do it.

Cache-line sharing and SMT: the shared counters were already split per thread (TICK-052: `SplitCount`, per-thread `par_counts`); the texture's
`last_frame` / `last_value` stores are made only when they differ (TICK-052). The replay's threads and the game's: with `RX_VK_PARALLEL=0` the game's
job phase is 0.15 ms a frame shorter (section 1), so the five recorders do not slow the game much. `RX_VK_PARALLEL_IDEAL` (workers on physical
cores of their own) is screened in section 7. No hardware counters were read (WPR needs an administrator).

### 4.2 RX_VK_TOUCH_FAST=1 (built, off by default; `gpu/vk/touch_fast.h`, `vk_texture.cpp texture_touch_unlocked`)

For a texture uploaded from memory, not the GPU's, not sparse or virtual, not held back by the upload budget, with no CPU write into its storage
since its upload (`memory_written_runs` on the page generations, which only the submit's capture stamps) and no GPU write into it not taken yet,
and not uploaded in this submission by a later segment, the locked `texture_touch` only notes the look (`checked_epoch`). The unlocked look
asks exactly those questions and notes the look itself; anything else takes the lock as before. Reads in order: `upload_gen` (acquire), the
memory, then `upload_value` / `upload_seg` (acquire), against `texture_touch`'s stores of the segment before `upload_gen` (a release fence
added between them). Serial replay: unchanged (only while `par_active`). `RX_VK_TOUCH_FAST_CHECK=1` asks every unlocked answer again under
ParLock (the memory since the generation the look read, the GPU writes, the segment order) and gives a disagreement to the locked path; logged
every 1200 presents (first version: every 3600, which printed nothing in a 110 s run at 50 fps; fixed in 881746b91). Unit test `tests/test_touch_fast.py` (2048 combinations of the decision against the locked rules, plus the wiring).

| (one profile run each) | Market off | Market on | castle off | castle on |
|---|---:|---:|---:|---:|
| first looks without the lock / under it (3600 presents) | | 3.62 M / 39 k | | 1.82 M / 8 k |
| ParLock taken, a big command buffer | 610 | 280 | 374 | 221 |
| ParLock waiting, ms a big command buffer | 0.658 | 0.403 | 0.199 | 0.167 |
| a segment's first 32 draws: us (locks a draw) | 9.95 (1.07) | 8.84 (0.39) | 11.94 (1.07) | 11.59 (0.64) |
| textures, summed ms a frame | 3.95 | 2.57 | 1.79 | 1.20 |
| draw() whole, summed ms a frame | 14.29 | 13.16 | 9.98 | 10.25 |
| replay thread: submissions, ms a frame | 7.95 | 7.37 | 6.48 | 6.74 |
| replay thread: its own segments, ms a frame | 3.05 | 2.79 | 2.97 | 3.04 |

Check mode (`RX_VK_TOUCH_FAST_CHECK=1`): Market 1.61 M looks asked again under the lock, **0 disagreed** (castle and Kokiri: section 6). "Written since" was 0 in
every run: a texture whose storage the CPU rewrites is caught before (its `checked_epoch` or its upload path), never at an unlocked first look.

In the Market the switch takes 0.6 ms a frame off the replay thread and 1.1 ms off the recorders' sum; in the castle, where the lock waited 0.2 ms
a command buffer to begin with, the profiles cannot tell it from noise. The Market has 1.8 ms of slack on the replay side (section 1), so this
cannot show in its fps; the castle is replay-bound but has little lock contention.

**The lock's wait moves, it does not go.** With the touches gone, the other ParLock places wait more: Market, a big command buffer, the view
miss (vk_draw.cpp:2963) 0.038 -> 0.101 ms, `memory_bind` (vk_memory.cpp:6613) 0.032 -> 0.087, the program pick (8135) 0.085 -> 0.118; on the
PC 3050 (Task 39, profile runs prof-A / prof-B, Market 1080p): touches 338 -> 0 takes, but the view miss 0.085 -> 0.287 ms, `memory_bind`
0.128 -> 0.344, the pick 0.122 -> 0.209; all places 1.264 -> 1.174 ms; textures 6.72 -> 5.37 ms summed, draw() 27.2 -> 27.9 (noise). One lock
serialises five recorders over textures, views, programs, pipelines and pools; taking one class of work out of it lets the next class queue.
What would remove the contention is the lock split by what it guards (the texture set and its uploads; the program and pipeline caches; the
pools), each with its own fast path: a design, not done here (section 8).


## 5. The dry pass

What it is per frame (one profile run each, pipe1a, switch off; `RX_VK_PROF_FINE`):

| | Market | castle | PC 3050 Market |
|---|---:|---:|---:|
| dry pass (replay wall's) | 2.37 | 1.17 | 4.40 |
| its ops | 1.92 | 0.81 | |
| of them "clear ds" (42 a frame in the Market) | 0.61 (14.5 us each) | 0.06 | |
| report counters (1,300 a frame, written to guest memory in stream order) | 0.22 | 0.14 | |
| draws (the loop and the state copies at the points) | 0.22 | 0.12 | |
| the target marks, timed inside (`dry_mark_written`: resolve / ParLock / mark) | 0.236 / 0.002 / 0.016 | 0.054 / 0.002 / 0.009 | 0.426 / 0.024 / 0.038 |
| the replay thread's own segments after it | 3.05 | 2.97 | 4.94 |

- **The marks are 0.25 ms a frame, not the 0.6-0.9 ms the "clear ds" op time suggested** (BIND1). The op's time runs to the next op's start;
  inside the mark the lock is not waited for (0.002 ms) and the marking is 0.016 ms; the resolution of the targets (`targets()`, under ParLock)
  is 5 us a mark in the Market against 1.3 us in the castle. The other ~0.35 ms of the Market's "clear ds" is outside the mark: section 6's
  runs without the clear marks (`RX_VK_NO_DRY_CLEAR_MARKS=1`), with the pool's spin and with the workers' ideal processors tell where.
- **Overlap**: the dry pass of a big command buffer already runs beside its own segments (`RX_VK_SEG_EARLY`: a segment goes out as the dry pass
  passes its end). Overlapping it with the *previous* command buffer's segments is what REC1/SUB1 analysed: every big command buffer is the last
  of its submission, so the next dry pass belongs to the next submission, behind its capture and `memory_begin_submission`; SUB1 Stage B built
  that hand-off and the next dry pass still could not start before the capture (section 2).
- **Folding it into recording** is not possible as such: the dry pass is the only pass that sees the stream in order, and its products are the
  state at each boundary plus the stream-ordered side effects (counters, render conditions, uniform updates, events, marks). A worker that
  started from the previous boundary's state would have to replay everything before its own segment: the serial chain again.
- **What it weighs on the chain**: in the Market the replay is not the limit; in the castle and Kokiri the dry pass is 0.54-0.83 ms of a 7.3-8.3 ms
  PT chain (`chainb.py`), the replay thread's own segments ~3 ms and its wait for the workers 0.2-0.9 ms. The dry pass is not the largest item of
  the replay-bound scenes; the replay thread's own segment is.

Where the rest of the "clear ds" time goes (Market, one profile run each, pipe1a):

| run | dry pass (wall) | its ops | "clear ds" op | "draw idx" op | marks inside the mark (resolve + lock + mark) |
|---|---:|---:|---:|---:|---:|
| plain | 2.37 | 1.92 | 0.61 | 0.22 | 0.25 |
| `RX_VK_NO_DRY_CLEAR_MARKS=1` | 2.07 | 1.66 | - | **0.80** | 0.24 |
| `RX_VK_POOL_SPIN=200` | 2.02 | 1.62 | 0.54 | 0.19 | 0.23 |
| `RX_VK_PARALLEL_IDEAL=1` (13 windows only) | 2.38 | 1.93 | 0.68 | 0.23 | 0.12 (22.7 marks) |

Without the clear marks the same cost appears on the draw that marks the new targets instead (0.22 -> 0.80): it follows the first mark of a
new set of targets, whichever op makes it, and ~0.3 ms a frame of it lies outside `dry_mark_written`'s timed parts (its second ParLock's
release is the one untimed step; a release that hands the lock to a waiting worker is a kernel call and can cost the releaser its core).
Workers on cores of their own (IDEAL) or spinning (SPIN) do not remove it. It is a Market-only cost (castle 0.06 ms, Kokiri: no "clear ds" in
the top ops), where the replay has 1.8 ms of slack: not pursued further. The cheap test of the release theory is one more timer (after the
lock's scope) or the mark's `texture_written` under `targets()`'s own hold (one take instead of two).

## 6. Measures (5070; fps from play + 10 s; 3 interleaved rounds; one build in slot c; pictures every 300 presents)

`RX_VK_TOUCH_FAST` A/B (pipe1a, switch by environment; s3 Market and castle, s5 Kokiri):

| scene | off: rounds | off median | on: rounds | on median | on/off |
|---|---|---:|---|---:|---:|
| market-spin | 91.4 / 90.7 / 94.6 | 91.4 | 91.5 / 91.6 / 93.7 | 91.6 | +0.2% |
| castle-move | 121.9 / (101.1, 47 frames over 50 ms: dropped) / 132.3 | 127.1 | 123.0 / 128.2 / 133.0 | 128.2 | +0.8% |
| kokiri-idle | 117.5 / 119.1 / 118.3 | 118.3 | 118.9 / 120.4 / 116.2 | 118.9 | +0.5% |
| PC 3050, Market 1080p (Task 39) | 50.7 / 51.1 / 51.0 | 50.9 (mean) | 50.5 / 49.9 / 51.4 | 50.6 (mean) | -0.6% |
| PC 3050, castle 1080p (Task 39) | 58.7 / 58.5 / 58.8 | 58.7 (mean) | 58.7 / 58.6 / 60.0 | 59.1 (mean) | +0.7% |

All inside the spread, as the anatomy predicts: the Market's replay has slack, and the castle's and Kokiri's replay-bound chains have little
lock contention to begin with (ParLock waiting 0.2-0.3 ms a big command buffer against the Market's 0.66).

Check mode (`RX_VK_TOUCH_FAST_CHECK=1`, every unlocked answer asked again under ParLock): Market 1.61 M, castle 1.97 M, Kokiri 2.82 M looks,
**0 disagreed**; "written since" 0 and "uploaded by a later segment" 0 in every run, "other cases" (sent to the lock) 0.4-1%.
(On the PC 3050 the first build printed no counter line in a 110 s run, a bug in the log's clock, fixed in 881746b91; its re-check was sent to
the coordinator as a two-run list on `pipe1b`.)

Pictures: 1145 shots over 20 runs, luma checked per run (`luma.py`: the black ones are the boot and the zone load in every run of
both arms; one-shot luma jumps only in the castle, in both arms at the same places of the walk), and looked at by eye at present 12000 of
each scene with the switch on: the Market square, the castle road and Kokiri's path drawn whole, textures right, no missing geometry.
Not a pixel comparison (a different frame rate is a different game time at the same present).

## 7. Existing replay knobs in a replay-bound scene (castle-move, 3 interleaved rounds)

ORDER3 and REPLAY2 tried the pool's spin, the replay thread's share of the segments and more recorders in the Market, and found nothing; the
Market is game-bound (section 1), so it could not have shown anything. **Not run here**: the castle screen (s4) was queued behind other
leases and withdrawn when the owner's play lease started. The run list, for the next stream (one build, slot c, `castle-move` from
`hyrule-castle`, 3 interleaved rounds, one warm-up; Kokiri as the second scene if one arm moves): off; `RX_VK_POOL_SPIN=200` (the replay
thread's wait for the workers, 0.9 ms a frame in the castle, p90 2.8 ms, is partly the wake of sleeping workers); `RX_VK_PARALLEL_IDEAL=1`;
`RX_VK_SEG_TAIL=50` (the replay thread's own segments, ~3 ms a frame, are the largest item of the castle's chain); `HOS_NVN_HOLD_PRIO=1` (the
held flush, 0.5-0.9 ms on the chain); `RX_VK_PARALLEL=6`. Script: `E:\OOT\scratch-pipe1\s4.sh` (+ `series2.sh`).

## 8. Proposals, effort left, what is not verified

**Defaults.**
- `RX_VK_TOUCH_FAST`: **on** (e8627c14c; =0 off). Inside the spread everywhere, leaner, 0 disagreements. The checks ran with the switch set
  by the environment on `pipe1a`; the default-on source itself was not run in the game (it changes only the switch's default).
- `RX_VK_TOUCH_FAST_CHECK`: off (diagnostic).
- `RX_VK_FRAMES_IN_FLIGHT`: stays off (no gain in any scene on either PC; worse p99 on the 3050).

**What the +30% needs, by scene, on the 5070.**
- Market (game-bound, 11.1 ms loop): the game's loop: the main thread's own 2.9 ms, the EX0 job it posts and waits for (1.7 ms, a serial step),
  the module jobs (5.7 ms on five workers). That is lifter / native-module work (`critical-jobs.md`, VIS3), not the replay's. Once the loop is
  below ~9.6 ms the replay's chain binds again, and TOUCH_FAST's -0.6 ms of the replay thread starts to count.
- castle / Kokiri (replay-bound by 0.8 / 1.2 ms): the asynchronous replay of section 3 (upper bound +10-16% / +16-21%), or shortening the PT's
  chain: its own segments (2.5-3 ms a frame), the wait for the workers (0.5-0.9), the held flush before its first submit (0.5-0.9), the dry
  pass (0.8-1.0).

**Effort left for the overlap design** (section 3): stage 0 (the stages' slack counters) 1 day; stage 1 (the PT's submits on the order thread,
the present as its job, every game wait joined) 1-2 weeks; stage 2 (every replay read through the submission's capture view, with a check
mode) about 1 week plus picture checks in 4 zones and a zone change; stage 3 (the held submits there too) 2 days. Gates as listed; the first gate
(replay-bound scenes) holds today for the castle and Kokiri on the 5070 and for the castle on the PC 3050 (replay-bound by 3.8 ms).

**Next contention lever**: ParLock split by what it guards. With the touches gone, its takes are `targets()` (93 a big command buffer, held
0.22 ms), the view misses (46, held 0.12), the pools' `memory_bind` (vk_memory.cpp:6613), the program pick (vk_shader.cpp, a shader-stamp
file). TEXRES1's `RX_VK_TEXRES_TARGETS` (targets() by fingerprint, no lock on a hit) covers the first; a texture-set lock and a pool lock apart
from the program/pipeline caches would let the rest run side by side. Estimate on the 5070: the recorders' sum -0.5 to -1 ms, the critical
path -0.1 to -0.3 ms in the replay-bound scenes (2-4%); larger on the PC 3050, where ParLock waits 1.2 ms a big command buffer.

**Not verified.**
- The default-on build in the game (the A/B and checks set the switch by the environment on the same binary).
- The PC 3050's check mode (its first run printed no counter: the log bug; re-check list sent).
- AMD / Intel CPUs (the unlocked reads rely on the acquire loads and the release fence, portable, but run only on a 7800X3D and the 3050's CPU);
  Linux.
- Zones other than the three scenes, zone changes, long play.
- The remaining ~0.3 ms of the Market's dry-pass marks (section 5): measured where it lands, not what it is.
- Pixel-exact pictures (none possible across arms).
- The asynchronous replay's numbers in section 3 are upper bounds read off the traces, not measured.

## 9. Dead ends and caveats

- `RX_VK_ASYNC=1` (the old replay thread): 67 fps in the Market against 89; the game polls what the replay has not written yet, and the old path
  had the black/white frames it was turned off for.
- `RX_VK_FRAMES_IN_FLIGHT=2` again, now with the anatomy: no gain in the Market (game-bound), worse in the castle and Kokiri (the present thread
  holds `sync_order_mu` against the PT's next submit), worse p99 on the 3050.
- `RX_VK_PARALLEL=0` (serial replay) to test the recorders' interference with the game: the game's job phase only 0.15 ms shorter.
- A `RX_VK_PARALLEL=3` traced run was disturbed (55.8 fps, 121 ms p99), not used.
- Lost runs: three series lost runs to `runguard` exit 11 (another process's compiler or build script; once my own syntax check during my lease:
  the first SUB1 arms), all re-run.
- `critpath.py`'s "80% of the path is the PT" is right per frame and wrong as a throughput statement: the next hand-over comes at the game's
  cadence (section 1).

## 10. Raw data and scripts

Runs: `E:\OOT\HeyListen\build\s5\p1-s{1..5}-*` (game_log.txt, present_trace.csv), traces and shots in `E:\OOT\scratch-pipe1` (`*.jt.txt`,
`shots\`), PC 3050 in `E:\OOT\scratch-pipe1\3050\pipe1`. Series scripts `series.sh`, `series2.sh`, `s2.sh` .. `s5.sh`; analysis `loop.py`,
`waits.py`, `sig.py`, `tl.py`, `pl.py`, `fps.py`, `luma.py` (all in `E:\OOT\scratch-pipe1`), with `tools/bench/chainb.py`, `critpath.py` and
`E:\OOT\scratch-bind1\avg.py`. Builds `build/s5/bin/pipe1a` (8c7e9a407), `pipe1b` (881746b91).
