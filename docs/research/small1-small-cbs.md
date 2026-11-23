# SMALL1: the command buffers the replay records whole, and what taking them off the serial chain gives (22 Nov 2026)

Branch `perf/small1` from `refactor/integ4` (617b60a87). Main PC (RTX 5070, Ryzen 7 7800X3D), speed leases (priority 2), builds `small1a` (logging only) and
`small1b` (all three switches), `market-spin` from `market-mid` and `castle-move` from `hyrule-castle`, `HL_*` defaults of the build, `RX_VK_FEED_LOG=1
RX_VK_SMALL_LOG=1` on every run (logging only; no `RX_VK_PASS_TIMING`, no `RX_VK_PROF_FINE`), fps from "play: began" + 10 s
(`build/s5/ab_main.sh`'s counting, `scratch-small1/fps.py`).

**Verdict in one paragraph.** The "17 small command buffers" of the brief are not 17 small things. In both scenes ten of them are empty (0 draws or
dispatches, 1.2 us each). The cost is five to seven command buffers of 14 to 200 draws and dispatches, mostly compute (light culling, particle and
skinning dispatches): 0.85 ms (Market) to 1.5-1.9 ms (castle) of the replay thread's frame. The ones of 128 to 255 are the expensive ones and they are
recorded whole only because the segment rule wants two segments' worth (2 x 128 = 256, or 2 x 160) before it cuts a command buffer at all. A new
switch, **`RX_VK_MID_SEG=<work>`**, cuts a command buffer of that much work into two segments anyway. Castle-move: **+13 to +18% fps** and no
50 ms hitches (see the tables); Market: no change beyond the spread. Recording the small ones on a worker beside the next big command buffer's dry pass
(**`RX_VK_SMALL_PREFIX`**) works (0 mismatches in 30,000 recordings, no error) and takes "small cb recorded whole" from 0.56 to 0.04 ms, but the time
moves into the dry pass and the segments and the GPU's idle does not shrink: no fps change on the 5070.

## 1. What the small recordings are (`RX_VK_SMALL_LOG=1`, logging only)

`replay_handle` (vk_replay.cpp) runs a command buffer's dry pass (state at every 32 draws, render conditions, uniform updates, counters, events) and then,
unless it has at least `2 x per` draws and dispatches (`per` = 128 with the fine segments, 160 with `RX_VK_FINE_SEGMENTS=0`), records it **whole, on the replay
thread** (`record_segment` into the open recording). `RX_VK_SMALL_LOG=1` counts these by work (draws + dispatches), with the ops of their stream, the time
of the dry pass and of the recording, and the order of the command buffers in a frame (`small_seq`). Market, 5070, steady windows, a frame:

| work | cbs | ops each | dry us | record us each | stopped at a wait |
|---|---:|---:|---:|---:|---:|
| 0 | 10.0 | 1.6 | 2.2 | 1.1 | 50% |
| 1-2 | 2.0 | 14.5 | 3.7 | 18.5 | 0% |
| 3-7 | 2.0 | 47 | 15 | 28.5 | 0% |
| 8-31 | 2.0 | 163 | 10 | 143 | 0% |
| 32-127 | 1.0 (the 105) | 551 | 48 | 346 | 0% |
| 128+ (a 295 that is cut when the plan has it) | 0.1 | 5060 | 723 | 1038 | 100% |
| **total** | **17.1** | | **0.20 ms** | **0.85 ms** | |

Castle-move (the same table): 10 empty ones, 2 of 1-2, 3 of 3-7, one of 14 (268 us) and **two of 128+ (the 159 and the 201: 722 us each)**; total 18.0 cbs,
dry 0.27 ms, record 1.46-1.86 ms. The ops of the Market's small ones, per frame: bind ubo 339, bind ssbo 252, bind program 167, bind tex 163, **dispatch 143**,
bind state 110, barrier 53, bind img 50, draw idx 29, wait event 14 (the big, wait-stopped ones are the bucket above). So the mid-size ones are
compute batches: 100-160 dispatches of ~3 us each, one pipeline bind and three or four buffer binds per dispatch.

The frame, in the order the replay sees it (Market; `#` a new frame, `|` the end of a piece, `w` stopped at a wait; numbers are draws + dispatches):

```
# 0 | 0 | 0w | 0w | 0w | 1017w | 0 | 0 | 0 14 2 105 0w | 295w | 5 4 19 0w | 846 | 2 |
```
Castle-move: `0 | 0 14 2 159 0w | 201w | 6 4 4 0w | 1155 | 2`. So a frame is three big pieces (1017w, 295w + 846; 2,200 draws) and, in front of
each, a piece of small ones that ends in a wait for the other queue (`0w`): `[0 14 2 105 0w]` waits for what `[295w]` does, `[5 4 19 0w]` for `[846]`. The pieces of the
two queues that wait for each other are replayed as one **burst** (one recording, `submit_job` -> `run_submit` -> `schedule` -> `burst_end`).
Consequences:
- the small ones are in a different `run_submit` than the big one they precede (the stop at the wait ends the loop): a prefix inside one submission never has anything to prefix;
- the Market's mid-size ones are 105 + 14 + 2 + 19 + 5 + 4, ~0.6 ms; the castle's are 159 + 201 (+ 14), ~1.5 ms: they are *just under* the threshold at which the replay cuts a command buffer;
- the empty ones and the game's own waits are free (1 us).

The GPU's side of the same frame (RX_VK_FEED_LOG, 5070, A arm, means of the 3 windows of 6 runs): window 12.3 ms = GPU busy 7.4 + idle 4.3; idle = replay not ready
3.8 (of it whole recordings 2.6, segments 1.2) + ready not sent 0.07 + **sent, GPU not started 0.34 ms** (the 5070's driver start latency: 0.9 on the 3050, not 5070) + 0.1. In "whole recordings": small cb recorded whole 0.60, dry pass 0.5, before the hand-over 0.55, setup 0.4, capture 0.4.
Castle: busy 6.4-8.0, idle 2.0-5.1 (not ready 1.7-4.7: whole recordings 1.5-4.0, of it "small cb recorded whole" 1.06-1.56 ms).

## 2. The changes (all off by default)

### 2a. `RX_VK_MID_SEG=<work>` (the one that works)
`segment_plan::with_mid(n, total, cap, mid_min)`: a command buffer that the rule leaves as one segment but has at least `<work>` draws and dispatches is two segments
(`replay_handle`'s generic path, `replay_handle_early`'s plan, and the lean dry pass's count). The plan, the segment submits, the profile, the pre-sync are the ones
every big command buffer has already, so nothing new is trusted: only a smaller command buffer goes through it. A cb that is cut costs the pre-sync, the segment
setup and a second submit (0.1-0.2 ms) and saves about half of its recording (and the replay thread no longer holds the whole of it): worth it from ~0.5 ms of recording.
`RX_VK_MID_SEG=96` cuts the Market's 105 as well (0.35 ms: not worth it there); 128 leaves it whole and cuts the castle's two.
Unit tests: `tests/test_segment_plan.cpp` (with_mid), `tests/test_small_prefix.py` (it is applied at the three places that count segments).

### 2b. `RX_VK_SMALL_PREFIX=1` (works, no gain)
Small command buffers of a **burst** (any with `RX_VK_SMALL_PREFIX_MIN`=8 or more draws and dispatches, and every one after a pending one, a wait-stopped one included)
get only their dry pass in place; their recording (the `Segment` with a heap copy of the starting state) is left pending (`small_pend`). The next big command buffer that the
early path cuts takes them as **the first job of its `EarlyGroup`** (`PendingSmall` / `EarlyJob::pre`; one job records the small ones in order into one recording of its own, a worker,
while the replay thread runs the big one's dry pass); the job is submitted ahead of the big one's segments by the same in-order submit rule. Anything else that comes first
(a serial replay, a single-segment cb, a multi-segment cb that is not early, `finish_recording`, the end of a `run_submit` outside a burst) records them whole at that point,
in order, into the recording that is open (`small_pend_flush`): the same commands as before, later. The render conditions of the pending ones stay (`par_conds` is not reset
while any is pending; the big one's follow, `cond0`). A check counts, per recording, the draws and dispatches recorded against the dry pass's count: 0 of 30,996 differed.
Tests: `tests/test_small_prefix.py` (source shape of every flush point and of the early path's offsets, plus a model of the in-order submit and of the condition indices);
the existing source-shape tests of the early path were updated for the offsets (test_seg_early, test_grow_lock, test_seg_gpu_time, test_defer_join), not weakened.

### 2c. Logging: `RX_VK_SMALL_LOG=1` (section 1).

## 3. Measures (5070, speed leases, interleaved rounds; fps from "play: began" + 10 s; every arm has `RX_VK_FEED_LOG=1 RX_VK_SMALL_LOG=1`)

Market (`market-spin` from `market-mid`), 3 rounds, build small1b:

| arm | fps r1 / r2 / r3 | mean | p99 ms | >50 ms | not ready ms | whole rec. | segments | small cb whole | dry pass |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A default | 84.8 / 83.3 / 84.2 | **84.1** | 20.7 | 8 | 3.80 | 2.62 | 1.18 | 0.60 | 0.48 |
| P `SMALL_PREFIX=1` | 83.2 / 82.2 / 84.3 | 83.2 (-1.1%) | 20.3 | 8 | 3.69 | 2.02 | 1.67 | 0.04 | 0.85 |
| M `MID_SEG=96` | 83.8 / 79.8 / 83.5 | 82.4 (-2.0%) | 20.9 | 6 | 4.08 | 2.54 | 1.55 | 0.12 | 0.50 |
| Q both | 82.6 / 80.7 / 83.9 | 82.4 (-2.0%) | 20.2 | 8 | 3.94 | 2.28 | 1.47 | 0.02 | 0.52 |
| `MID_SEG=128`, 1 round (s5), against A of the same series | 84.5 against 83.1 | +1.7% | 19.9 | 2 | 3.73 / 3.91 | 2.41 / 2.71 | 1.32 / 1.20 | 0.31 / 0.65 | 0.38 / 0.53 |
| `RX_VK_BATCH=1` (fewer submits), 1 round | 81.4 | | | | 3.83 | 3.01 | 0.82 | | |
| `RX_VK_DRY_LEAN=1`, 1 round | 83.7 | | | | 4.00 | 2.81 | 1.18 | | |

(The feed columns are the means of the last three 600-present windows of rounds 2 and 3.)
The spread of one arm is 79.8-84.8 fps: **none of the Market arms differs from the default beyond it** (the two with the 105 cut are the lowest, which is what the
cost of a second segment against 0.35 ms of recording says). `SMALL_PREFIX` did what it was built for: "small cb recorded whole" 0.60 -> 0.04 ms, 6.9 command
buffers a frame (1.7 jobs) recorded by a worker, 0 mismatches in 31,000 recordings. The replay thread's other steps grew by what it took: the dry pass 0.48 -> 0.85 (it now
runs beside a worker that records and takes the same locks: ParLock for the textures, `ub_grow_mu`) and the segments' step 1.18 -> 1.67 (the big one's first segment can
only go out after the prefix job, which a worker records at ~2x the serial cost, 0.5 -> ~1 ms). The GPU's idle does not move (4.1-4.5 against 4.3 ms): on the 5070 the Market's
chain is the game's own submit thread and the in-order hand-over, as BIND1 and REPLAY2 found, not the small ones' recording.

Castle-move (`hyrule-castle`), build small1b, 3 interleaved rounds (s5; the earlier rounds of A and M96 in s1/s3 agree):

| arm | fps r1 / r2 / r3 | mean | p99 ms | >50 ms | GPU idle | not ready | small cb whole |
|---|---|---:|---:|---:|---:|---:|---:|
| A default | 110.4 / 110.2 / 112.5 | **111.0** | 13.8 | 15 | 2.1 | 1.8 | 1.16 |
| `RX_VK_MID_SEG=128` | 126.1 / 126.5 / 123.9 | **125.5 (+13.1%)** | 11.5 | 3 | 0.7 | 0.3 | 0.03 |
| earlier A rounds (s1 default build, s3) | 109.4 / 111.8 / 108.0 / 111.1 / 98.9 | 107.9 | 21.0 | 50 | | | |
| earlier `MID_SEG=96` rounds (s3) | 128.7 / 125.9 | 127.3 | 11.7 | 1 | | | |
| earlier `MID_SEG=96` + `SMALL_PREFIX` (s3) | 126.9 / 124.3 | 125.6 | 12.3 | 1 | | | |

Kokiri Forest (`kokiri-idle`, `kokiri-forest`), 3 rounds: A 103.7 / 105.5 / 105.0 (mean 104.7) -> `MID_SEG=128` 114.5 / 113.2 / 113.5 (**113.7, +8.6%**); the "small cb recorded whole"
1.16 -> 0.31 ms, GPU idle 2.14 -> 1.14, not ready 1.77 -> 0.79. p99 equal (12.7 / 12.9 ms); frames over 50 ms: A 0, 0, 1 against M 1, 5, 2 (not understood: the idle
scene's loading hitches, or a cost of the cut; the p99 does not show it).

Where it comes from, castle: the default's two command buffers of 159 and 201 draws and dispatches (722 us each, recorded whole on the replay thread) become two segments each; the replay
thread's "small cb recorded whole" 1.16 -> 0.03 ms and the GPU idle falls by 1.4 ms of 9 ms; the added step (pre-sync, segment setup, one more submit) is under 0.2 ms each.
The Market has no command buffer in this range (105 is the largest), so nothing there to win; at 96 the 105 is cut for a loss, at 128 it is not.

## 4. Pictures
`RX_VK_SHOTS` runs (one per arm, `scratch-small1/shots`): Market default (A), `SMALL_PREFIX` (P), `MID_SEG=96` + `SMALL_PREFIX` (Q); castle default and `MID_SEG=128`.
The shots at the same present number are the same until the title and the load menu (mean difference 0.00-0.9 of 255 for the first 1,500 presents: animation), then the
runs part (a different frame rate means a different game time at the same present number), so a pixel comparison of gameplay frames is not possible (mean difference 20 of 255 over a castle run).
Looked at side by side (frames 3000, 6000, 9000 and 12000-12600 of each): same title, same grass and lighting, the load menu identical, the castle road and the Market square drawn
as in the default arm, no missing geometry, no black or white frames. **Not a pixel-exact verification.**

## 5. Dead ends and things not done
- `SMALL_PREFIX` within one submission (my first version): the small command buffers and the big one are never in the same `run_submit` (the piece ends at a wait): 0 engagements. Carrying them across the pieces of a burst is what engaged.
- Fewer submits (`RX_VK_BATCH=1`, existing): no gain (81.4); `RX_VK_DRY_LEAN=1` (existing): no gain (83.7). The 5070's "sent, GPU not started" is 0.34 ms, so there is little to win on this card;
  the 3050's flat 0.9 ms is for its own run. Nothing was built for the submits: the command buffers of one `run_submit` already share one recording, and the 11 submits a frame are the game's.
- `SMALL_DIRECT` (REPLAY2: no dry pass, no barrier pair around a small cb) had no gain on the 5070 either; the small ones' cost is their ops (compute batches), not the pair.
- Pending small command buffers on a worker cost more than the same work on the replay thread would (about twice a worker's per-draw cost, BIND1 section 2b) and delay the first segment's submit: only
  worth it where the replay thread is the limit and the workers idle (the 3050?).

## 6. Proposed defaults
- `RX_VK_MID_SEG`: **on, at the segment size (128 with the fine segments, 160 otherwise)**: castle +13%, Kokiri +8.6%, Market within noise (+1.7%), fewer hitches in the castle, and leaner by construction (the replay thread
  records less: "small cb recorded whole" 1.16 -> 0.03 ms), through the path every big command buffer already takes. Not changed in the code here (default 0): the build that was measured has the switch off and the value in the environment;
  turning it on is `e ? std::max<int64_t>(0, atoll(e)) : par_seg_per()` as the default in `mid_seg_min()` (the tests that look at it keep passing). To confirm first on the PC 3050 (below), where the whole-recording time is larger.
- `RX_VK_SMALL_PREFIX`: **off**. Correct, no gain on the 5070; it adds about 170 lines to the early path. Keep as a switch for the 3050's result, or drop.
- `RX_VK_SMALL_LOG`: off (logging).

## 7. Not verified
- The PC 3050 (6 cores, 12 threads; the brief's 1.94 ms of "small cb recorded whole" and the 0.9 ms start latency): not run; the run list (Market at 100% and 50%, A / prefix / mid-seg / both, castle, pictures) was sent to the coordinator for its session.
  The 5070's numbers do not predict it: there the replay thread is not the limit in the Market while the workers idle (BIND1), but SMT sharing makes the workers' time dearer.
- Pixel-exact pictures; AMD and Intel (segments of small command buffers on other drivers); the other scenes (Hyrule Field, Lost Woods, Deku Tree, Death Mountain); long play. No lifter change.
- The Kokiri hitch counts (8 frames over 50 ms against 1) with `MID_SEG=128`.
- The source changed after the build small1b only in comments, the removed default argument of `replay_handle_early` and tests; small1b was built from 5f7f392c7.
- Unit tests: the suite (`python -I -B engine/tests/run_all.py`): 490 passed, 7 failed, of which the known environment ones (nx2/test_nup_paths, nx2/test_nup_read_name) and three that also fail on `refactor/integ4` here
  (test_display_startup_failure, test_env_file, test_target_image_pool); the two that were mine (test_grow_lock, test_seg_gpu_time) and test_seg_early / test_defer_join were updated for the early path's new offsets and pass.

## 8. How to reproduce
`RX_VK_SMALL_LOG=1` prints, every 120 presents: the small command buffers by work, their ops by kind, the phases of the whole recording, and the order of the command buffers of the last frames. Builds `build/s5/bin/small1a` (logging only), `small1b`.
Runs: `scratch-small1/series.sh <name> <bin> <rounds> tag:scene:save:ENV=V,...` under one speed lease; `scratch-small1/fps.py`, `table.py`, `feedsum.py`, `cmp.py`.
