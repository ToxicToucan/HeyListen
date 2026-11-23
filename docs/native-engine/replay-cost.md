# What the frame's heavy replay is made of (stream R4, 2026-11-15, PC 3050, castle-move)

Tools (logging only, `RX_VK_PROF_FINE=1`, every 120 presents): `a frame (fine)` (draw sections summed over the recording threads, now with the
program lookup, the pipeline lookup and the texture slots resolved again), `a frame (fine, replay thread, wall)` (submissions, the dry pass,
the segments, the command buffers recorded whole), `replay_handle_early's steps` and `ops of the dry pass / segment workers / whole`.
`RX_VK_PRESYNC_STATS=1` now also times the dirty-page scan and the chunk diff.

## Where the replay thread's wall time goes (ms a frame, 2.2-3 big command buffers, ~2,300 draws)
Submissions with 300 draws or more: 14.3. Of the early path's steps: the mirror pre-sync before the dry pass 4.2, the dry loop 2.6, the replay
thread's own (last) segment 4.7, waits and submits 0.6; 18 small command buffers recorded whole here 2.4.
Summed over threads, per draw: textures 2.0 us (of it 0.9 resolved again: 1.0 us each), program lookup 1.2, user uniform buffers 0.55,
storage buffers 0.56, descriptor push 0.66, pipeline bind 0.55, vertex+index binds 0.45. Raw `vkCmd*` calls are a small part; most of the
rest is already cached per thread (texture slots, handle hits, banks, vertex sets, descriptor reuse), so there is no large repeated work
left to remove between draws. The program lookup is in vk_shader.cpp (shader stamp): not touched.

## The pre-sync: the chunk diff hashed every page again
On this PC (slow link) the mirror syncs' chunk diff is on, and the capture's copy is meant to make its hashes (TICK-060). The claimed copy
(`RX_VK_CAPTURE_CLAIM`, on by default since sprint 5) dropped them: 6 slots of ~1,340 a submit were hashed by the copy, the pre-sync hashed
the rest again, 3.7 ms of its 5.4 ms. `claim_run` now passes the hash arrays on (`RX_VK_CLAIM_HASH`, default on, =0 as before): 1,451 slots
hashed by the copy, 13 hashed again, the pre-sync 2.5 ms, the heavy submissions 14.3 -> 10.4 ms a frame.
Speed (interleaved, 3 against 3): castle-move 31.9 -> 36.1 fps, kokiri-idle 34.0 -> 37.3 fps.

## Tried, no gain over the spread (removed)
A segment's place on a fixed worker (TICK-052's affinity, on the early path), 16 handles a texture slot (resolved again 0.92 -> 0.64 a draw, time
the same), later early segments shorter (the first longest): 36.2 against 36.1 fps.

# R6: shortening the heavy replay R (2026-11-15 night, PC 3050, castle-move and kokiri-idle)

What a frame's replay is, from a job trace of castle-move (`HOS_JOBTRACE`, one 25 ms frame; C2's marks): the main thread's held submit at
6.0 ms, the Presentation Thread's own small submits 8.9-9.9, `present` entered at 13.0, and the heavy replay on the replay thread from
about 12.2 (the main thread's signal) to 23.2: mirror pre-sync (1.4), then for each of the three big command buffers its dry pass
(0.6-0.8), its segments handed out as the dry pass passes their ends, **its own last segment (2.8, 1.2 and 1.8 ms: 5.8 of the 11)**,
the in-order waits and submits, and between them the small command buffers recorded whole (1.1 and 0.4 ms); the GPU's tail follows
(23.2 to 25.4). The workers' segments were done before the replay thread's own segment was.

## What was tried, in the brief's order

1. **The small command buffers recorded by workers.** The 18 a frame are 157 of the frame's 1,760 draws (16 us a draw on the replay
   thread against 8 on a worker: cold caches), 2.8 ms. But they sit *behind* the big command buffer in the same submission (cb 0 the
   big one, cb 1-7 small; the next big one is the next submission, replayed under its own `replay_mu` hold), so no later dry pass of
   the same submission is there to hide them behind, and a job left over between submissions would break the queue order against the
   other queues' replays. Two variants measured: replaying a command buffer of under 96 draws once, in order, without its dry pass
   (the dry pass of a small one is ~0.1 ms: 'dry pass' 5.10 against 5.02 ms, nothing); and keeping their dry-pass products as pieces
   recorded by a worker as the next big command buffer's first job (the pieces were all flushed at the submission's end: 'recorded
   whole' 1.95 against 1.94 ms). Neither kept.
2. **The own segment.** R5's `RX_VK_PLAN_TAIL` (all segments end together) did not shorten R, and `RX_VK_PARALLEL=5` and `=6`
   (more recorders) did not change the own segment either (4.6-4.9 ms against 4.7-4.9, 4 rounds). What did: more, shorter segments
   than recorders (below).
3. **The heavy buffers' dry pass before the signal.** Not done, because the dry pass is not a pure walk. In stream order it also
   *writes guest memory*: the counters of the reports (963 a frame), the clears the dry pass makes on the CPU (depth-stencil: 32 a
   frame, 14 us each; colour: 43 at 2.7 us), and it reads the render conditions' operands (445 a frame) out of the memory those
   reports and the game write, and it marks the textures the draws write. A clear made before the signal lands before the game's own
   writes of the same range, which the stream orders after it; the marks reach the textures other work reads. Only the state walk
   (bind ops, draws: ~1.2 of the dry pass's 2.4 ms a frame) is pure, and running it twice (early, then again for the effects) costs
   what it saves. What would have to hold to run it early: the effects deferred to the signal and replayed from the recorded stream
   with the state of each op, which is the dry pass again.
4. **R5's `RX_VK_DRY_WANTED`** (the dry pass copies the state only at the points a boundary takes; cherry-picked d043efd8). Castle-move,
   fine profile, two rounds against `=0`: dry loop 2.77/2.93 -> 2.61/2.43 ms, own segment 5.20/4.98 -> 4.91/4.80 (fps 34.1/34.7 ->
   35.0/34.9, inside the spread). On, as R5 left it.

## Tried, no gain (removed)

- `RX_VK_SEG_PRESYNC`: the early path's mirror pre-sync as `RX_VK_PRESYNC_EARLY`'s job on a worker beside the dry pass, the segments
  held until it is done (par_active must stay off while the sync records its chunk hashes). The first big command buffer's pre-sync is
  1.3 ms and the dry pass 1.2: they overlapped, but the submit of the pre-sync's own recording (0.12 ms) and the held segments took
  the difference back: 'pre-sync + dry loop' 3.94 against 4.0 ms, castle-move fps 37.0 against 36.9 over 4 rounds.
- `RX_VK_SEG_SUBMIT`: each segment submitted, in order, by the thread that finishes it (the replay thread submitted all of them after
  its own segment, ten submits of 0.1 ms). With the finer segments: 37.3 against 37.6 fps (4 rounds), nothing.

## More, shorter segments (`RX_VK_FINE_SEGMENTS`, default on under 16 hardware threads)

A big command buffer was cut into one segment a recorder (4 workers and the replay thread: 5), balanced by recording time; the workers'
started as the dry pass passed their ends, the replay thread's own when the dry pass was over, and it was the last to finish. Cut into up
to ten segments of ~128 draws instead (`RX_VK_SEG_MAX=10 RX_VK_PARALLEL_ROUNDS=0 RX_VK_PARALLEL_SEG=128`), the last ones are short and a
recorder that is done takes the next (the pool's queue; the replay thread helps from it). The replay thread's own segment 4.7-5.0 ->
3.6 ms (3.1-3.2 with 12 segments of ~96), the waits for the segments 0.7 -> 1.4. Castle-move, 4 rounds interleaved: 36.1 -> 37.6 fps
(+4%), 1% low unchanged. 12 segments (96 or 64 draws) gave the same.

## Confirmed on main 38ab626c (PC 3050, plain runs, interleaved, new defaults against `RX_VK_FINE_SEGMENTS=0 RX_VK_DRY_WANTED=0`)

- castle-move, 3 rounds: 35.6 / 34.3 / 36.2 (mean 35.4) -> 37.6 / 37.9 / 37.7 (mean 37.7), +2.3 fps (+6%); 1% low 7.9-12.0 -> 18.2-19.8.
- kokiri-idle, 3 valid rounds (the first, after the rebuild, was a cold-cache throwaway): 37.4 / 39.8 / 36.9 -> 37.1 / 37.4 / 37.5 / 36.9:
  no gain, inside the spread.
- Picture: frame log (320x180, ~35 s each) of castle-move, kokiri-idle, a saved zone (Kakariko) and the Zora run (D4's movement
  script, the reporter's save): 0 one-frame luma dips in all of them, "uploaded by the later segment" lines 0, no made frames;
  flashes by framelog.py: castle 449 against 418, Zora 415 against 572 (small blocks, not dips), kokiri 0 against 4, zone 0 against 0.
  Shots of the four scenes by eye: right.
- Suites on the final HEAD: Windows 291 passed, 1 failed (nx2/test_nup_paths, known); Linux 307 passed, 2 failed (nx2/test_nup_read_name:
  no `zstandard` in WSL; the byte-compile check: git refuses the worktree path there).

## ORDER2 (the order thread off the critical path on 6-core CPUs)

Evidence: Market on an i5-10400F. The order thread runs 9.0 ms of a 24.3 ms frame; the presenter waits on it ~10.5 ms. ORDER1's counters
(PC 3050, RX_VK_PROF_FINE): locked passes 12.6 ms wall / 9.5 ms cpu, submissions 11.8 ms, dry pass 3.5, segments' recording and wait 6.6,
queue submits 14 a frame (0.57 ms), slot waits 30 a frame (0.13 ms); SEG_MERGE never engaged, FENCE_POLL changed nothing. So the driver
calls are the small part there; the dry pass (serial) and the wait for segments are the big ones. Step order re-aimed accordingly:
 A. segments: the in-order submit by whichever thread finishes (EARLY_GPU, exists), and the replay thread no longer joining the
    segments before it goes on (join deferred to the next point that needs the recordings: next begin_recording of the same state,
    present, wait) -- not started.
 B. dry pass: parallel / early / overlapped with the previous frame's segments -- not started.
 C. GPU-completion off the order thread (done, below), D. memory sync off the order thread, E. submission queue instead of
    sync_order_mu on the presenter, F. smaller own segment (more to the idle workers).

Invariants to keep (every step):
 1. Submission order: vkQueueSubmit2 calls keep the order the replay made them in (one queue; guarded by dev.present_mu); fences and the
    recordings timeline are signalled in that order. Anything handed to another thread is enqueued under dev.present_mu.
 2. The game's fence/semaphore semantics: a value's timeline signal, value_rec / whole_val / q_last marks are made at the submit that
    finishes the value, never earlier; a game fence is never answered before its recording is done.
 3. Readback ordering: invalidate (CPU readback) after a fence poll sees every write-back of the recording (RX_VK_READBACK_TICKET,
    memory_staged_apply before `completed()` is told): completion tracking must not make `submitted`/slot reuse visible earlier.
 4. Present ordering: a present follows the work it presents (sync_order_mu, dev.present_mu) and the swapchain's acquire/done fences.
 5. A slot is reused only after its submit retired AND its fence is reset; its command pools are reset only then.
 6. Event waits (RX_VK_WATCH_EVENT, perf/wait-044): sync_watch's kick is still sent by update_blocked / event_signal_queued; nothing here
    changes the watch. Integrate by merging perf/wait-044 after measuring; the two touch different lines.
 7. Blocking waits that are semantics, not bookkeeping (occ_sync / occ_go_on, replay_flush_upload) stay blocking.

### Step C (done, 6f2966edc): `RX_VK_COMPLETION_THREAD=1` (off)
engine/gpu/vk/completion_tracker.h: a thread waits for each submit's fence in queue order and resets it; the four submit sites (submit_recording,
presync early, submit_job, the merged segment submit) hand over under dev.present_mu; recording_wait reads an atomic (blocks on a
condition variable only when the GPU is still on the slot). Groups need no FenceGroup. Batch recordings keep the old path.
`RX_VK_COMPLETION_CHECK=1`: checks, at each retire, the recordings timeline reached the submit's value and the fence is reset (logs
and counts violations; counters logged every 7200 slot waits: found done / waited / ms). Tests: engine/tests/test_completion_tracker.py
(order, groups, stale generations, 48-slot ring, waiters, stop).
Expected gain: small on the PC 3050 (slot waits 0.13 ms + polls); it is the cheap, safe base.

### Hand-over
- Branch perf/order2 (from perf/order-044 3b8a320e7), worktree E:\OOT\HeyListen-wt\order2. Step C built (mkg, 0 failed, 21 Nov), unit tests pass; full suite 480 passed, 4 failed = 2 known nx2 + test_dry_lean/test_seg_gpu_time, fixed in the F commit (the second was already failing on perf/order-044).
- To validate when machines are free: build; run Market and castle-move with RX_VK_COMPLETION_THREAD=1 RX_VK_COMPLETION_CHECK=1: 0 violations, pictures as default, no stalls; then A/B with RX_VK_PROF_FINE.
- Step A (defer the join) analysed, NOT implemented: the join is not where the time is (the replay thread waits for `done` of each job, each already
  being submitted in order by its finisher under EARLY_GPU; par_pool.done.wait afterwards is ~0). What the thread really spends is the dry pass
  and its OWN last segment, recorded after the dry pass (replay_handle_early: record_job(last)). Deferring the wait for the segments would
  let the thread go on into the "what follows" recording and the next dry pass, but needs: heap-owned jobs (they live on the stack with seg_cv /
  seg_mu captured by reference), par_active/par_seq/texture_segments_begin(nullptr) kept until the real join, ub_updates readers vs the next
  appender (ub_growing covers one command buffer), thread-local draw/texture recording state, and every submit_recording kept behind the
  segments' in-order submits (sub_mu drain). Too much to build blind (no game runs): ask before doing it, with a plan to test it by picture.
### Step F (done): `RX_VK_SEG_TAIL=<percent>` (default 100 = off)
The replay thread's own (last) segment gets that share of the time of each other segment (50: half), the rest goes to the idle workers
(segment_plan::balanced_targets(..., tail); the profile-balanced boundaries move later in the command buffer; both the early plan and the
post-dry-pass plan use it). 100 is bit-identical to before (integer path). Try 60, 40 on the Market (RX_VK_PROF_FINE: "the replay thread
recorded x ms" in the early-segments log line; slowest segment must not exceed it much).


### Step B (dry pass parallel / overlapped): analysed, NOT implemented (21 Nov)
A submission is a list of command buffers replayed in a loop (run_submit -> replay_handle per handle); `state` (cur_state) flows from one
dry pass into the next, and the dry pass also mutates replay-thread-only shared structures in stream order: ev_values, ub_updates,
par_conds, par_points (cleared at each dry pass), the written marks (dry_mark_*), the textures it makes, occ_* conditions, rec_open_*.
- Parallel dry pass over one stream: needs state composition across chunks (NvnState is ~27 KB with generation counters and the
  uniform/event side effects): not a safe offline change.
- Overlap dry(N+1) with the segments of N: dry(N+1) can start from the state dry(N) left, not waiting for N's recordings, but the
  `nr` recording that "what follows" N goes into, the in-order submit drain (jobs, sub_mu, seg_cv are locals of replay_handle_early),
  par_active/par_seq/texture_segments_begin and par_points would all have to outlive the call (same list as step A). It is the
  same change as A, so it carries the same risk, and it only pays once the own segment (RX_VK_SEG_TAIL) is not what delays the next
  dry pass.
Measure first (needs a game run under a speed lease, RX_VK_PROF_FINE=1): the dry-pass op-kind table (op_cost_ns[0]) and "its own
segment" against "dry loop"/"waits and submits", with RX_VK_SEG_TAIL=100/60/40. If the own segment falls enough, the order thread's
critical path is dry pass + short tail and B's gain is the dry pass alone (3.5 ms): then decide with that table.


# ORDER3 (the GPU's idle between recordings, where it comes from; 21-22 Nov 2026, branch refactor/order3)

Branch refactor/order3 = refactor/step1 (627e374e8, the readback ring) + perf/order2's six commits (completion thread, SEG_TAIL, counters).
All measures below with `HL_RB_LAG=2` (the default to come), Market `market-spin` from `market-mid`, 100%, no upscaler, vsync 0, on the
main PC (RTX 5070, 7800X3D) under a speed lease.

## The tool: `RX_VK_FEED_LOG=1` (off)
`RX_VK_PASS_TIMING` cannot tell: it turns the parallel replay off (`replay_handle`: `gprof_on()` takes the serial path), so the 6.4-6.9 ms
the 3050 showed between command buffers belongs to a serial replay, not to the shipped one. The existing "GPU time ... gaps between recordings"
line is the shipped configuration's total; `RX_VK_FEED_LOG` splits it. Per recording the host notes when it was **ready** (recorded; a segment
also waits for the ones before it in the queue's order), when it was **sent** (the `vkQueueSubmit2` that carries it; a held recording's is its
batch's flush), the GPU's own timestamps are put on the host clock (calibrated timestamps, refreshed every 50 ms), and `feed_stats.h`
splits each gap before a recording: replay not ready (and of it, how much of the time the replay was inside a `run_submit` pass) / ready but not
sent (in-order submit, late fix, batch) / sent but the GPU not started / sent before the GPU went idle (a wait inside the GPU or the
driver). Logged every 600 presents ("GPU feed (RX_VK_FEED_LOG)"). Do not combine it with `RX_VK_TL_S` / `RX_VK_COMPLETION_CHECK` (the first
test run with both lost the segments from the list; the order3 runs without them are complete). The timeline (`RX_VK_TL`) has new marks in a
big command buffer's replay: `early+/-`, `dry+/-`, `seg-out`, `seg+/-`, `seg-submit`, `own+/-`.

## What it shows (5070, steady windows, ms a frame, frame 10.9)
GPU busy 7.2, idle between recordings 3.7: **replay not ready 3.25 (88%)**, ready but not sent 0.07, sent and not started 0.29, sent before the
GPU went idle 0.13. A run with the replay-pass split (slot lease): not ready 2.15 of which 2.04 with the replay inside a pass. So the idle is
the replay's own speed (the game had handed the work over), 1/3 of it inside the big command buffers (the GPU caught up with the segments) and
2/3 before the small recordings between them. The late-fix hold and the batch are not a factor (0.07 ms).

## Knobs tried (interleaved, 3 rounds; `A` = `HL_RB_LAG=2`)
See the hand-over table below: SEG_TAIL=50/30 moves the replay thread's own segment 0.98 -> 0.61 / 0.47 ms a big command buffer and its wait for
the workers 0.19 -> 0.58 / 0.79: the same total, the frame the same. `RX_VK_POOL_SPIN=200` (workers and the replay thread look 200 us for work
before they sleep): nothing on the waits (0.19), and a lower 1% low. `RX_VK_COMPLETION_THREAD=1`: inside the noise.

### Numbers (main PC, speed lease, `market-spin` from `market-mid`, 3 interleaved rounds, fps of the run: a sanity figure; p50 10.5-10.8 ms in every arm)
First series (build order3, `HL_RB_LAG=2 RX_VK_FEED_LOG=1` on every arm; the E arm lost round 1 to a queued build of another agent, runguard exit 11):

| arm | fps rounds 1/2/3 | replay thread's own segment / its wait for the workers (ms a big command buffer) |
|---|---|---|
| A (`HL_RB_LAG=2`) | 86.8 / 87.3 / 87.5 | 0.98 / 0.19 |
| B `RX_VK_SEG_TAIL=50` | 88.9 / 88.0 / 80.2 (hitchy) | 0.61 / 0.58 |
| C `RX_VK_SEG_TAIL=30` | 86.9 / 88.7 / 82.2 (hitchy) | 0.47 / 0.79 |
| D `RX_VK_POOL_SPIN=200` | 84.6 / 80.7 / 85.0 | 0.97 / 0.19 |
| E `RX_VK_COMPLETION_THREAD=1` | - / 85.4 / 87.3 | 0.99 / 0.18 |

Second series (build order3b, the 3050's configuration on the 5070: fine segments, 4 workers):

| arm | fps rounds 1/2/3 | own segment / wait | slowest segment | segments a big cb |
|---|---|---|---|---|
| A (5070 defaults) | 80.2 / 86.0 / 88.1 | 0.98 / 0.19 | 1.41 | 4.5 |
| S4 `RX_VK_FINE_SEGMENTS=1 RX_VK_PARALLEL=4` | 87.4 / 89.2 / 88.8 | 0.74 / 0.24 | 1.13 | 5.6 |
| S6 `... RX_VK_PARALLEL=6` | 87.6 / 89.5 / 87.5 | 0.83 / 0.16 | 1.22 | 5.6 |
| S8 `... RX_VK_PARALLEL=8` | 86.6 / 86.8 / 87.1 | 0.84 / 0.16 | 1.28 | 5.6 |
| S4T `... RX_VK_SEG_TAIL=40` | 81.3 / 87.5 / 86.3 | 0.40 / 0.40 | 1.25 | 5.5 |

Reading: no knob changes the frame. The replay thread's own segment and its wait for the workers always add to ~1.2 ms a big command buffer
(a segment is ~1.1-1.3 ms at ~128 draws; the last one cannot start before the dry pass, 0.6 ms, has ended), so what the order thread spends is
the dry pass plus the longest segment, whoever records it; more recorders (4, 6, 8), a smaller own share (50, 40, 30) and a spin before sleeping
all leave that sum alone. The 5070's frame (10.6 ms) does not move with any of them, so the 5070 cannot judge this lever: the 3050 can, with the
feed log (below). The rounds with a 1% low under 10 fps (B-3, C-3, D-2, A-1, S4T-1) are machine hitches, not the knob: the same arms have clean rounds.

## For the PC 3050 (its own session; files in `C:\OOT\builds\order3`: HeyListen.exe, hookshot_vk.dll, HeyListen.map = build order3b)
Same options for every run (dlss 0, upscaling 0, resolution fixed at 1440p 100%, hdr 0, vsync 0, no cap), `market-spin` from `market-mid`, 3 interleaved
rounds, `HL_RB_LAG=2` and `RX_VK_FEED_LOG=1` on all, no `RX_VK_PASS_TIMING`, no `RX_VK_TL_S`:
A plain; B `RX_VK_SEG_TAIL=50`; C `RX_VK_SEG_TAIL=30`; D `RX_VK_POOL_SPIN=200`; E `RX_VK_COMPLETION_THREAD=1`; F `RX_VK_EARLY_GPU=2`; G `RX_VK_PRESYNC_EARLY=1`.
Read per run: fps and p50/p95; "GPU feed" lines (steady windows: GPU busy, idle, and the idle split into replay not ready (of it with the replay in a
pass) / ready not sent / sent not started / queued), "GPU time ... gaps between recordings", "parallel replay, early segments ... a command buffer: dry pass
... the replay thread recorded x ms and waited y ms". With `RX_VK_PROF_FINE=1` in a separate untimed run: the order thread's wall by pass.


# REPLAY2 (what the replay does while the GPU waits for the small recordings; 22 Nov 2026, branch refactor/replay2 from refactor/order3)

All measures: `HL_RB_LAG=2 HL_GFX_VAR=1 RX_VK_FEED_LOG=1`, `market-spin` from `market-mid`, 100%, no upscaler, vsync 0, main PC (RTX 5070) under a speed lease.

## The tool: `RX_VK_FEED_LOG` now splits "replay not ready" by what the submitting thread was doing
`feed_stats.h` has `Phase` marks: the replay (it runs on the game's submitting thread, `submit_q` under `sync_order_mu`) pushes a mark each time it starts
something else, inside a pass: `capture` (`memory_capture_submit`), `setup` (`run_submit` up to the first command buffer: memory work, `begin_recording`),
`small cb recorded whole`, `dry pass`, `pre-sync`, `segment setup`, `own segment`, `wait for segments`, `after segments`, `finish`, `submit`. Each recording also keeps
the time the game's call reached the replay (`submit_q`'s entry). The not-ready time of a gap is cut at that time: before it the game had not submitted;
after it the phases are the replay thread's. Logged with the "GPU feed" line, per kind of recording ("GPU feed, the replay not ready for whole recordings
/ segments ..."), and `submit_q waited ... for sync_order_mu` (always 0.00). Logging only; `tests/test_feed_stats.cpp` covers the phase split.

## What it shows (5070, steady windows, ms a frame, frame ~10.5, GPU busy ~7.5, idle ~3.0, replay not ready ~2.5)
Whole recordings (about 5.4 gaps a frame, 1.4-1.7 ms): the game's own time before its call 0.2-0.3, capture 0.13-0.15, setup 0.2-0.25, small command
buffers recorded whole 0.4-0.45, the dry pass of the next big command buffer 0.26-0.3 (the empty `rd` recording that follows it is a whole recording too:
what the GPU waits for is the first segment, which cannot go out before the dry pass has passed its first boundary), pre-sync 0.07, segment setup 0.06.
Segments (2.5-2.8 gaps, 0.9-1.0): the replay thread's own segment 0.7-0.8 and its wait for the workers 0.24.
So the "small recordings" wait is not one thing: about a fifth is the game, and the replay's share is spread over seven steps of 0.05-0.45 ms,
none of them a wait on another thread (`submit_q` never waits for `sync_order_mu`) and none a lock: it is the serial path of the thread that submits.

## Tried
- `RX_VK_SMALL_DIRECT` (a command buffer of under 96 draws replayed once, in order, with no dry pass and no segment/barrier pair around it; 19 a frame,
  3.4 draws each, 1/3 of them stopped at a wait): the header walk and the replay agreed (2 mismatches in 192,000), 'small cb recorded whole' 0.45 -> 0.48,
  'dry pass' unchanged (the dry pass in the idle is the big buffers'), fps 92.8 -> 94.4 (one round). No gain: removed.
- Existing switches seen through the new split (1 round, then 3 interleaved: see the hand-over below): `RX_VK_EARLY_GPU=0|1|2`, `RX_VK_PRESYNC_EARLY=1`.
- `RX_VK_EARLY_GPU=1` (the default) against `=0`, 6 interleaved rounds (fps 83.2 / 90.0 / 94.3 / 94.6 / 78.2 / 90.2 against 93.1 / 90.2 / 92.9 / 91.8 / 92.1 / 90.6;
  GPU idle 3.0-3.9 against 3.1-3.4 ms, replay not ready 2.6-3.5 against 2.4-2.6): no difference beyond the spread (the `=0` arm has no 1% hitch rounds, which the
  default's two low rounds, 83.2 and 78.2, are not enough to call). `=2` (first segment short): 88.8 / 85.4 / 85.1, idle 3.7: not better. `RX_VK_PRESYNC_EARLY=1`, one round: no change.
  The 5070 cannot judge these (its frame is the game's): the 3050 can, with the split.

## Why no change was kept, and what is left
- The replay is not a pool that something else fills: the "replay not ready" is the game's own submitting thread, one serial chain. Every step of it is
  already short (0.05-0.45 ms a frame on the 5070), and each one was measured against a cheaper form (SMALL_DIRECT) or an existing switch without a gain.
- The one lever that is large and not yet touched is the one ORDER2 analysed and left: the replay thread does not wait for the workers' segments before it returns
  (own segment 0.7-0.8 + wait 0.24 a big command buffer, 2.5 a frame: ~2.4 ms of a 10.5 ms frame sits on the submitting thread and the workers are idle after it).
  It needs the jobs on the heap, `par_active`, `par_seq`, `texture_segments_begin` and the in-order submits kept until a real join (the next `replay_mu` taker's first
  act), and the Serial-mode replay of the next submission to be safe beside workers that are still recording (today only the dry pass runs beside them: `ub_growing`,
  ParLock). That is a design for its own stream with a check mode comparing pictures, not a change to make in a measuring pass.


# JOIN1 (the join of a big command buffer's segments deferred; 22 Nov 2026, branch refactor/join1 from refactor/replay2)

## What was built
- `replay_handle_early`'s jobs, in-order submit rule and join are now `EarlyGroup` (on the heap, held by the pool's jobs), `early_submit_job`, `early_submit_ready`,
  `early_record_job`, `early_join`. Same behaviour with the switch off (source-shape tests updated).
- `RX_VK_DEFER_JOIN=1|2` (off; a diagnosis, NOT for play): the last segment goes to the pool too, `replay_handle_early` returns without recording or waiting, and the join is
  made by the replay thread later: =1 after the next command buffer's dry pass, =2 only at the latest points. Joins are forced before: the next big command buffer's start
  (its pre-sync and `texture_segments_begin`), a serial replay, an early pre-sync on a worker, the multi-segment path, `occ_go_on`, and run_submit's end of loop (a join never
  outlives `replay_mu`). A dry pass beside pending segments continues `par_conds` (not reset, `cond0`) and holds `ub_grow_mu`.
- `RX_VK_DEFER_JOIN_CHECK=1`: at each join, every segment's recorded draws and dispatches against the dry pass's plan; counts the steps that ran beside pending segments.
- `RX_VK_SMALL_ASYNC`: NOT built (see below).

## Shared state, why nothing useful can run beside the pending segments (read from the code)
`begin_recording` (the recording that follows) calls `memory_table_begin` (global `table_defer`/`table_pending`, which a worker's `texture_uploads_recorded` ->
`memory_table_flush` also writes, unlocked), `memory_recording_value` (global `mem_rec_value`), `memory_rb_recording_begun` (`rb_joined`) and `texture_begin_recording`;
the next pre-sync marks pools `synced_epoch`, which a pending segment's bind consults (a pool it binds later would be taken as synced with the copy behind it in the queue);
a new submission's `memory_begin_submission` bumps `mirror_epoch` and clears `mirror_synced`/`writebacks`; `finish_recording`'s `memory_end_submission` needs every worker's
write-backs; `texture_segments_begin` replaces the table the workers index; `ub_prune`, `par_conds_reset` free what workers read. Serial-mode ops write guest memory and `ev_values`.
Only the dry pass (ParLock, `ub_grow_mu`, `par_conds_n`) is built to run beside workers, and in a real frame the next step after a big command buffer is always one of the forced joins.

## Results (main PC, RTX 5070, speed lease priority 3, `market-spin` from `market-mid`, 3 interleaved rounds, `HL_RB_LAG=2 HL_GFX_VAR=1 RX_VK_FEED_LOG=1`, RUNGUARD_MIN_FREE_MB=2800: the owner's browser left 3.7 GB free)
| arm | fps r1/r2/r3 | p95 ms | not ready ms | GPU idle ms | own seg / wait-or-join |
|---|---|---|---|---|---|
| A off | 74.7 / 85.7 / 87.1 | 17.0 / 15.8 / 15.5 | 2.39 / 1.98 / 1.84 | 2.64 / 2.24 / 2.11 | 0.95 / 0.19 |
| D1 | 54.4 / 75.2 / 88.8 | 32.4 / 17.7 / 15.3 | 2.86 / 2.20 / 1.91 | 3.11 / 2.45 / 2.17 | 1.0 / 0.78 (the join) |
| D2 (unsafe) | 86.0 / 87.6 / 78.8 | 15.8 / 15.3 / 16.6 | 2.04 / 1.90 / 2.32 | 2.29 / 2.16 / 2.58 | 1.0 / 0.77 |
Round 1 of each arm is the slow one (machine state). No gain in not-ready, idle, fps or p95; the wait just becomes the join (0.19 -> 0.78 ms) because nothing runs in between.
Check (D1/D2 with CHECK, Market, Kakariko, Lost Woods, Inside the Deku Tree, `kokiri-idle`, ~10,000 joins each): 0 segments differ from the plan; steps beside pending segments per join:
recording begun 1.00, serial small command buffer 0.00, dry pass 0.00. No crash, hang or lost device in any run (no validation layer was on).
Not verified: pictures. `RX_VK_SHOTS` wrote no file in the bench runs (windowed; a HOS_HEADLESS try also wrote none), so off/on pictures were not compared.

## SMALL_ASYNC: why not built
A small command buffer is 3.4 draws, ~0.024 ms recorded whole on the 5070 (~0.06 on the 3050); on a worker it needs its own recording and a `vkQueueSubmit2` (~0.04 ms, 14 a frame = 0.57 ms on the 3050)
plus a new `rec_cur` (`begin_recording`, unsafe beside workers, above) at every one. Net loss or break-even, and unsafe until the memory/texture globals above are per-recording.

## For the PC 3050 (files in `C:\OOT\builds\join1`)
Not needed for a verdict: the 5070's chain already shows the join has nothing to overlap with. If wanted: A plain vs `RX_VK_DEFER_JOIN=1`, `HL_RB_LAG=2 HL_GFX_VAR=1 RX_VK_FEED_LOG=1`, 3 interleaved rounds, 1440p and 1080p.


# FEED1 (the held submits' hand-over: why they are held, and what a stream could still gain; 22 Nov 2026, branch refactor/feed1 from refactor/join1)

Result: **RX_VK_FEED_STREAM is not built.** The premise (the main thread hands its held submits over once a frame) is not what the code does: each held
submit is handed to the replay on its own, 5 us after the hold. What is left to overlap is 0.2 ms of a 10.4 ms frame, and it is the game's own code.

## Why the main thread's submits are held (`host/nvn_native.c`, `HOS_NVN_HOLD=2`, the default since TICK-054)
- `nvnQueueSubmitCommands` (the submit, ~line 2835): a thread other than the presenting one (OoT: the main thread, 2 submits a frame, 3.38 ms after the
  present's end, 2 us apart) does not replay; it appends the handles to `held[]` (`held_cs`), sets `hold_event` and returns. The presenting thread (the Presentation
  Thread, PT) replays its own submits inline and, before each, flushes the held ones under `flush_cs` (`mine` branch, cause `HF_SUBMIT`).
- The host thread "hos held submits" (`hold_flush_thread`, woken by the event) calls `held_flush_why(HF_THREAD)`: capture and replay of each held submit, in order, under
  `flush_cs`. Measured on VIS3's three A traces (1,550 frames): hold to the replay's first mark **5 us** (p90 5-6 us). Nothing waits for a frame's end.
- What the hold protects, rule by rule:
  1. **Capture time**: the capture is taken at the flush, not at the submit (`HOS_NVN_HOLD_CHECK`: 0.14% of the captured pages differ), so the main thread does not do the 30 MB copy
     (HOLD=3 tried: the copy moves along the loop). The reason is where the work runs, not an NVN rule.
  2. **Order of submissions**: one queue order. `flush_cs` makes the PT's submit wait for the held ones, and `held_flush_why` copies the list and replays it in order.
     This is the rule that makes the PT's first submit wait 0.53 ms (ready 0.24 ms after the last hold, enters the replay 0.77 ms after it).
  3. **Visibility of a submit to the game's pollers**: `nvnSyncWait` on a fence past the replayed one (`HF_SYNC`), `nvnEventGetValue` (`HF_EVENT`, mode 2), `nvnQueueFinish`
     (`HF_FINISH`), the present (`HF_PRESENT`) flush what is held first: a held submit may be what signals them, and `held_count()` is added to fence numbers.
     With `HL_RB_LAG=2` the readbacks (R1-R3) no longer wait for the GPU, so those waits are not readbacks' any more, but the game's own fence and event polls still need
     them (the flush is the answer to the poll, and it happens at the hold anyway): this reason is unchanged and costs nothing, the flush has begun 5 us after the hold.
  4. **MKW writes data in place right after its submit** (the comment at the top of the held-submit block): holding is how a flush happens when the game's queue flush
     would; `nvnQueueFlush` itself is a no-op in mode 2 (the thread is on it). `HOS_NVN_HOLD_LINGER_US` (flush later) gives wrong pictures (white exposure): do not.
- `RX_VK_BATCH` / late-fix holds in `vk_replay.cpp` (`late_hold`, `batch_on`) are another hold, of the Vulkan submits, not of the game's; they are off or ~0.07 ms (ORDER3).

## What a stream could still gain (VIS3's A traces, `HOS_JOBTRACE`, 1,550 frames, marks 1/2 = a submit's replay, 3 = a hold; mean ms a frame)
| step | ms |
|---|---:|
| present end to the first hold (the main thread's draw code: nothing exists to replay before it) | 1.6-1.8 |
| hold to the replay's start | 0.005 |
| last hold to the PT's hand-over wake (queue `lr 834a3084`) | 0.24 |
| the PT ready, blocked in `flush_cs` behind the held replay (capture 0.34 + memory work 0.32 of the first held submit) | 0.52-0.56 |
| the replays, first mark to last end | 7.55-7.9 |
| of that, idle between replays (the PT's own game code between its submits: before submit 2 0.01, 3 0.02, 4 0.04, 5 0.15) | 0.18-0.22 |
So the replay already starts on each submit as the game produces it. The ~2.5 ms of "game side" is the main thread's draw code (1.7) and the held submits' own replay (0.77);
the idle between replays, which an asynchronous hand-over of the PT's submits could hide, is 0.2 ms (2%). Hiding the PT's 0.53 ms wait would need the PT's first capture
taken before the held replay ends: the captures' order (`memory_capture_submit`'s FIFO, dirty marks) is the held thread's to keep, and a gain of at most 0.15 ms (the PT's
first capture) is not worth a second capture order. What gates the next present is the serial replay (held 0.77 + PT 6.8) behind the main thread's draw code, and the main thread's
draw code waits for the previous present (`HOS_PRESENT_NOWAIT`'s territory, a different rule, with wrong pictures at stake).
Scripts: `E:\OOT\scratch-feed1\gaps.py`, `gaps2.py` (the scratch folder; they read `tools/bench/chainb.py`'s trace format). No new run: no code changed.
