<!-- Copyright (C) 2026 Sacrappy -->
# DECOUPLE: what frame N+1 needs of frame N, and how to stop waiting for it (24 Nov 2026 -)

Branch `perf/decouple` from main `c79bee6f6`, worktree `E:\OOT\HeyListen-wt\decouple`, scratch `E:\OOT\scratch-decouple`. Lease name
`decouple` (priority 1 since the coordinator's message of 24 Nov). The owner's request: decouple the exposure computation and every
other value that depends on another frame, so that nothing waits and frames can be worked on in parallel. Targets: +30% on the RTX
3050 / i5-10400F (about +20% reached), 120+ fps on the RTX 5070 / 7800X3D, no stutters.

## Summary at the stop of 24 Nov, 16:40 (first session)

1. **The map** (section 1): no game thread waits for the GPU in a way that sets the frame any more. On the PC 3050 at the default
   settings the frame is our own serial replay: the order thread idles 0.00-1.05 ms a frame while the GPU idles 1.1-4.8 ms behind it.
   At 1440p native both PCs are within 4-17% of the GPU's own work. The Market is the game's own loop.
2. **The design** (section 2): game -> spine -> lanes -> GPU; the ceilings read off the logs are +13-21% on the 3050 at the default
   settings in Kokiri and Hyrule, +5-9% in the Market, +4-6% at most on the 5070 at 1440p native. Not measured gains.
3. **The owner's question** (section 3): reusing the frame before's recording and removing the replay for a pass were both measured
   before (MEMO1, step 2): neither pays; nothing changes in the stages.
4. **Built** (section 4), every switch off by default: the state per submission (`refactor/sub1`) and the order thread
   (`perf/pipe2`, PIPE4's hand-over) merged onto main in one tree; `RX_VK_CAP_DEPTH`; the guessed write-backs' snapshots per
   submission. Checks with `RX_VK_SUB_OVERLAP=1`: 0 differences in every counter (Kokiri), 0 flashes over one warp tour; the order
   thread: one tour, 0 flashes, no hang. **No fps number yet**: the PC 3050's list `matrix81` had not started at the stop.
5. **Not built**: the lanes themselves (`RX_VK_LANES`, section 4.3 has the recipe). `RX_VK_SUB_OVERLAP` and `RX_VK_ORDER_THREAD` do
   not work together yet (the first is left off when the second is on).

## State (keep current; last edit 24 Nov 23:35, third session DECOUPLE3; see 4.8-4.9 for the latest)

- **Third session (DECOUPLE3, from 21:25; resumed 23:10)**: the order thread's load hang **found and fixed** (section 4.9):
  the held present's signal sent the timeline back, and a late fix then waited for good. Fix `cc753074e` (timeline_order.h,
  tests/test_timeline_order.py: fails before). Build `dec3e` (= `cc753074e` + notes) launched 23:31 (`build-dec3e.sh`, log
  `build-dec3e.log`, suite first under the lease). `list-3050-3.txt` written: A 20 order-thread starts on dec3e (0 freezes
  expected), B the on/off fps arms (Kokiri, Hyrule, Market, 3 rounds), C optional: dec3d with `RX_VK_LATE_DEFER=0` (the cycle from
  the other side). Main PC: dec3d's loop of 12 (`AFF=FFF0`, `loop-dec3d-ld3.log`, the coordinator's) caught nothing in 8 starts
  that reached play; matrix97 on the 3050 caught 5 in 20. **Next**: suite and build of dec3e (`build rc=0`, `DONE`), tell the
  coordinator "list ready: dec3e"; on the main PC a loop of 40 starts on dec3e (`BINNAME=dec3e N=40 AFF=FFF0 TAG=ok`, `loop3.sh`)
  for function (no fps); read `matrix98` (list 3) when it comes; then the default-on proposal as one last commit (section 4.9).
  **Status 01:35 (25 Nov)**: dec3e built (`build rc=0` 01:23; suite 512 passed / 5 failed: the known three, plus test_wb_defer
  and the switch lists, both fixed in `5b2269dae`, which changes no code). "list ready: dec3e" sent 01:28. The 40-start loop
  runs from 01:28 (`chain-dec3e.sh`, log `loop-dec3e-ok.log`, `STOP-loop3` stops it). **The default-on proposal is the last
  commit, `770aa80c2` (was 6372b041f before a reorder)** (host/hos_main.c `order_thread_default`: RX_VK_ORDER_THREAD=1 when unset and hos_reserved_cpus()
  keeps a core for the replay; =0 off; tests/test_order_thread_default.py); the whole suite on the tip queued under a build
  lease (`suite-tip3.sh`, log `suite-tip3.log`, `tests-tip3.log`). The coordinator takes or leaves `770aa80c2` alone. Stopped 01:35 at the brief's hand-back: the loop and the suite were still waiting for leases and were stopped, their tickets released; relaunch `chain-dec3e.sh` and `suite-tip3.sh` with launch.py.
  Scratch: `aff.py` (a run under a CPU affinity: `FFF0` = 6 of the 7800X3D's cores with the replay's reserved one), `loop3.sh`
  (as `loop.sh`, plus `AFF=`, `EXTRA=`, `hang-<tag>.txt` per start with a watch line, `STOP-loop3` file to stop).

- **Second session (from 16:40)**, section 4.4: `b49b55244` the submit sequencer, the render conditions per group,
  `RX_VK_LANES_CHECK=1` (build `dec3a`, suite 513 passed / 3 known failed, `E:\OOT\scratch-decouple\tests-dec3a.log`). **The check
  reads 0 in the game** (series 4, `E:\OOT\scratch-decouple\c4-dec3a.log`, runs `build/s5/dec3a-*`, 17:03-17:22): `dec3a-lc-k`
  194,279 tickets taken / 166,347 sent / 27,932 dropped, `dec3a-lc-h` 109,459 / 97,737 / 11,722, `dec3a-tour-lc` 100,484 / 83,990 /
  16,494 (Market, Hyrule Field, Kakariko, Zora's River, Hyrule Field); in all three 0 out of order, 0 unknown, at most 2 open,
  0 lists all held, 0 capture views lost, 0 pool sync pairs lost; 0 flashes, 0 fatal lines.
- `7f12b5ae8` step 3a: `RX_VK_SUB_OVERLAP` with `RX_VK_ORDER_THREAD`, the order thread's done marks held behind a pending end
  (`submit_seq::DoneHold`). `ac7d78a3a` `RX_VK_LANES=1`: one pending group records beside the next big command buffer's pre-sync
  and dry pass, joined at its end (section 4.5). Both off by default; their tests pass (`test_submit_seq.py`, `test_defer_join.py`
  and the early-path tests); not yet run in the game.
- Build `dec3b` (= `ac7d78a3a`, both) queued 17:25 for the build lease (behind another request), with the suite; `chain-dec3b.sh`
  runs check series 5 (`c5.sh`: two warm-ups; on the order thread `RX_VK_LANES=1` in Kokiri and Hyrule with the checks,
  `RX_VK_SUB_OVERLAP=1` in Kokiri with the checks, the warp tour with each; a RAM wait before each run) once the build is done.
  Logs: `build-dec3b.log`, `c5-dec3b.log`.
- `matrix81` (the CPU gate) not run on the 3050 at 17:22 (next in its queue after matrix61 part 2, per the coordinator).
- Branch `perf/decouple`, nothing pushed. First session's code tip `b4f558d20`; suite on it: 512 passed, 3
  failed, the known three (`E:\OOT\scratch-decouple\tests-tip.log`). Switch lists regenerated.
- Builds in `E:\OOT\HeyListen\build\s5\bin`: `dec1a` (= `5054c6330`: main + `refactor/sub1`), `dec2a` (= `09484e740`: + `perf/pipe2`
  + `RX_VK_CAP_DEPTH`), `dec2b` (= `e67d919c8`: + the snapshots per submission; the tip's code). On the PC 3050: `C:\OOT\builds\dec1a`
  (hashes checked) and `pipe4f`.
- **Waiting on the PC 3050**: list `matrix81` (sent 15:28, corrected 15:38 to leave the Market out; its script `matrix81.sh` was
  there at 15:50, not started at 16:25, behind the coordinator's `matrix58`): `dec1a` at the default ini, Kokiri and Hyrule, plain
  against `RX_VK_SUB_OVERLAP=1`, 3 passes interleaved; a check run; two `pipe4f` runs with `HOS_THREAD_TIMES=5` for section 3.3.
  Read `C:\OOT\pc3050s-out\matrix81.txt` and `C:\OOT\pc3050s-out\dc81\`. Expected from the design: little or nothing from this
  switch alone (the present still waits for the frame whole); the list is there to have the base measured where the replay binds.
- Nothing of this stream runs or holds a lease; slots a and b hold their own exe and dll again (`bak-a`, `bak-b` compared).
- Scripts in `E:\OOT\scratch-decouple`: `build.sh <name> [tests]` (build lease priority 1, the suite, `mkg.ps1`), `c1.sh` / `c2.sh` /
  `c3.sh` (check series on a slot lease priority 1: counters, tours with the flash detector), `m1b.sh` (counter runs), `sum.py`
  (a run's counters, short), `syn.sh <file.cpp>` (a syntax check with the build's flags); all launched with
  `python -I E:/OOT/scratch-pipe4/tmp/launch.py <script>`. Runs are 175 s (tours 300 s): the machine was loaded all day and a 110 s
  run often has no play in it; a first run of a build with a new shader stamp only prepares shaders.
- **Next, in order**: (1) read `matrix81`; (2) on a quiet machine, the Market save with `RX_VK_SUB_OVERLAP=1` three times on `dec2b`
  (section 4.2: the loads that never reached play); (3) stage 2, `RX_VK_LANES`, by the recipe of section 4.3, starting with the
  submit sequencer (pure rules and a test) and `par_conds` per group, on the order thread; first make `RX_VK_SUB_OVERLAP`'s
  hand-off the order thread's (its captures are taken by the submitting threads, the pending end must be completed by its jobs);
  (4) two more order-thread tours and a PC 3050 function run on `dec2b` (PIPE4's rule ran once here; `pipe4f` froze the 3050's
  Market at the native ini); (5) stage 5 (R4 into the ring) only after the lanes.
- Measurement series of this stream: `m1.sh` (cut: 110 s runs), `m1b.sh` (`dc1-m-off`, `dc1-m-on`, `dc1-k-off`; cut at 14:42 to free
  the slot), `matrix80` on the PC 3050 (done, copy in `E:\OOT\scratch-decouple\matrix80.txt`), check series 1-3 (section 4).

## 1. The dependency map

Sources (every number below is read from one of these): the PC 3050's logs of its order-thread A/B of 23 Nov, default ini, build
`pipe2e` (`C:\OOT\pc3050s-out\pipe2\{mm,hf,kv,cc}-{A,B}2\game_log.txt`, copies in `E:\OOT\scratch-decouple\3050\pipe2\`; A = inline
replay, B = `RX_VK_ORDER_THREAD=1`); `matrix80` (PC 3050, 1440p native ini, build `pipe4f`, `RX_VK_WAIT_LOG=1 RX_VK_FEED_LOG=1`);
PIPE1's profiled run on the PC 3050 (`E:\OOT\scratch-pipe1\3050\pipe1\prof-A\game_log.txt`, Market 1080p, `RX_VK_PROF_FINE`);
PIPE4's gate runs on the main PC under a speed lease (`build/s5/p4g-*`, 1440p native); this stream's `build/s5/dc1-m-off` (main PC,
slot, machine shared).

### 1.1 Each dependency: producer, consumer, the latency it tolerates, what it forces today, what it costs

| # | Value (frame N -> N+1) | Producer | Consumer | Tolerated latency | Wait or order forced today | Cost measured |
|---|---|---|---|---|---|---|
| D1 | Exposure sums (R1), 0x40 result slots (R2), 0x2000 pair (R3) | GPU programs of frame N | module jobs `f_80357060`, `f_80354060`, `f_8034f440` | the console reads them one frame late, after a fence poll; the ring hands them 2 frames late (3 with the order thread): 0 pulses, pictures right (`readback-ring.md`, `frames-in-flight.md`) | none: the readback ring (`HL_RB_LAG`, on) | 0 waited for the GPU in every log read (`rbring R1: ... 0 waited for the GPU, 0 not done in time`) |
| D2 | Culling results 0x3980 x2 (R4), big lists 0x9b040 (R5), particle state 0x1b580 (R6) | GPU compute of frame N, written back at the end of its submission | `f_800998a0` / `f_80365f00`, `f_80362ff0`, `f_80364080` on the module workers and the main thread | not established for a fixed lag (R6 is half CPU-written: #50) | `invalidate` waits for the write-back's recording and the GPU (`wait_recorded`) | 5070 (dc1-m-off): 0.01-0.02 ms a frame a range. PC 3050 native: Kokiri 0.38-0.75 ms a frame on each module worker and 0.19-0.31 on the main thread (`160 of 3000 waited, 4.57 ms each`); Market 0.01-0.12 a thread (`21 of 3000, 3.10`); Hyrule `28 of 3000, 4.05`. Default ini: `19-175 of 3000, 0.5-5.0 ms each` |
| D3 | "The frame before is on the GPU": the game's fence wait and present pacing | the replay's progress (inline: the PT's chain; order thread: its jobs) | main thread (`nvnSyncWait` lr 8042cde0, present event lr 81207a6c) | the game itself allows one frame between its hand-over and the GPU | inline: the main thread waits for the present; order thread: it waits for the job of the fence's submission | inline, PC 3050: 2.0-4.2 ms a frame (default ini), 4.8-5.1 (native); 5070: 1.5-1.7. Order thread, PC 3050: fence join 0.85-2.8 (default), 3.9 (native Kokiri); 5070: 0.75-0.8. It is back-pressure: what it waits for is D4 |
| D4 | **The replay's own order: one recording after the other** | the dry pass's stream-ordered effects (reports, conditions, events, uniform updates), and the memory state every recording shares (epoch, capture view, write-back and synced lists, the segments' tables) | the next submission's replay | none needed: it is our own serial chain | the replaying thread records its own last segment of every big command buffer and waits for the workers' before the next submission may begin (JOIN1, REC1, SUB1) | PC 3050, default ini: `a frame's replay time: submit` 11.7-14.6 ms a frame; profiled (Market 1080p): dry pass 4.8, **segments' recording and wait 8.0-8.7**, small command buffers 1.6, memory work 0.8. **The order thread idles 0.00-0.56 ms a frame in all four scenes: it is the frame** |
| D5 | The GPU's feed | the replay (D4) | the GPU | - | the GPU idles when the next recording is not there | PC 3050 default ini: gaps 1.6-5.2 ms a frame inline, 1.1-4.8 with the order thread, of 14.2-18.9 ms frames. Native: idle 2.2-3.3 of 20.3-22.4 (replay not ready 1.0-1.8, sent and not started 0.9-1.0, sent before the GPU went idle 0.3). 5070 native: 0.5-0.8 inline, 0.3-0.4 with the order thread |
| D6 | Write-backs of GPU-written pages (W), the readback refresh | every compute and pass that writes a bound range | whatever the CPU reads next | lazy on a slow link (WBL1, on by itself on the 3050) | GPU copies, bus traffic | PC 3050: 16.2-16.7 MB a frame with the lazy rule (58 before); 5070: 52.6-55 MB. GPU time on the 3050 before the lazy rule: wb 0.86-1.35 ms, refresh 0.16-0.24 of 18.0-18.7 (`gpuov-own-overhead.md`) |
| D7 | Occlusion counts (R7) | not produced: every report reads 4096 | 445-920 render conditions a frame, evaluated on the CPU in the dry pass | same frame only (`occlusion.md`: a late count deletes buildings) | none | no wait; the GPU draws hidden objects (8.5 against 8.1 ms a frame with real counts on the 5070, `occlusion.md`) |
| D8 | Guest memory "as submitted" | the game's writes up to its submit | the replay, later | exact | the capture's copy on the submitting thread | 0.2-0.7 ms a frame on the PT and 0.2-0.7 on the held-submits thread (wait log, "capture copy"); not a wait |
| D9 | GPU -> GPU across frames (history, depth of the frame before) | frame N's passes | frame N+1's | - | none on one queue | none; it is what a second queue would have to order (ASYNC1) |

### 1.2 What bounds the frame, scene by scene (ms a frame)

PC 3050, default ini (render Auto), `pipe2e`, 23 Nov, one run each (the second of three):

| scene | arm | fps | frame | GPU time | uploads | GPU gaps | replay wall (submit) | order thread idle | main waits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Market | inline | 52.3 | 18.87 | 13.2-13.4 | 0.4-3.0 | 4.5-5.2 | | | present 1.99 |
| Market | order thread | 55.9 | 18.10 | 13.0-13.8 | 0.4-0.6 | 3.6-4.8 | 14.56 | 0.56 | fence 0.85 |
| Hyrule Field | inline | 56.0 | 16.23 | 12.8 | 0.3 | 2.9-3.0 | 12.67 | | present 3.84 |
| Hyrule Field | order thread | 60.6 | 15.42 | 12.8 | 0.3 | 2.1-2.4 | 13.73 | 0.00 | fence 2.78 |
| Kokiri | inline | 63.6 | 14.60 | 12.0 | 0.2 | 2.0-2.5 | | | present 2.81 |
| Kokiri | order thread | 67.9 | 14.22 | 12.1 | 0.2 | 1.2-1.6 | 11.66 | 0.44 | fence 1.78 |
| castle | inline | 63.4 | 17.10 | 14.9 | 0.3 | 1.6-1.9 | | | present 4.16 |
| castle | order thread | 68.8 | 14.83 | 13.1 | 0.3 | 1.1-1.3 | 13.48 | 0.00 | fence 2.81 |

PC 3050, default ini again, `matrix80` (`pipe4f`, 24 Nov, `RX_VK_FEED_LOG=1`, one run each): what the GPU's idle is made of.

| scene | arm | fps | frame | GPU busy | GPU idle | of it replay not ready (before the hand-over / small command buffers / dry pass / own segment / wait for segments) | sent, not started | sent before the GPU went idle | order thread idle | main waits |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| Market | inline | 50.4 | 18.01 | 13.48 | 4.53 | 3.61 (0.21 / 0.74 / 0.48 / 1.10 / 0.46) | 0.54 | 0.26 | | present 1.35 |
| Market | order thread | 55.3 | 17.64 | 13.73 | 3.91 | 2.87 (0.69 / 0.08 / 0.45 / 0.94 / 0.29) | 0.50 | 0.28 | 1.05 | fence 0.54 |
| Kokiri | inline | 60.3 | 15.66 | 12.85 | 2.81 | 2.11 (0.10 / 0.87 / 0.24 / 0.44 / 0.05) | 0.36 | 0.25 | | present 4.30 |
| Kokiri | order thread | 64.7 | 14.64 | 12.88 | 1.75 | 1.14 (0.70 / 0.05 / 0.05 / 0.19 / 0.05) | 0.22 | 0.37 | 0.01 | fence 2.75 |
| Hyrule Field | inline | 58.8 | 15.28 | 12.22 | 3.05 | 2.37 (0.09 / 1.33 / 0.13 / 0.43 / 0.05) | 0.34 | 0.24 | | present 2.93 |
| Hyrule Field | order thread | 63.2 | 14.51 | 12.39 | 2.12 | 1.52 (1.03 / 0.11 / 0.04 / 0.18 / 0.04) | 0.19 | 0.35 | 0.06 | fence 2.55 |

With the order thread the GPU's idle is no longer inside the big command buffers (own segment 0.18-0.19, wait 0.04-0.05 in Kokiri and
Hyrule): the GPU has the recordings of the submission before to run meanwhile. What is left is the difference of two rates: the
order thread needs the whole frame (idle 0.01-0.06) and the GPU 12.4-12.9 ms of it, so 1.75-2.12 ms of GPU idle fall wherever the
order thread has nothing for it yet ("before the hand-over" in the feed log is the time before the order thread began the
submission: its other jobs, the present). The frame shortens only if the order thread's work a frame falls under the GPU's.

PC 3050, 1440p native (`matrix80`, `pipe4f`, one run each): Market inline 22.42 = GPU busy 19.15 + idle 3.27; Kokiri inline 20.25 =
18.05 + 2.20, order thread 19.88 = 18.12 + 1.76 (order thread idle 0.00, fence join 3.92); Hyrule inline 20.94 = 18.12 + 2.82, order thread 19.97 = 17.74 + 2.23 (order thread idle 0.03, fence join 3.42). The Market's
order-thread run froze (State).

Main PC, 1440p native (PIPE4's gate, speed lease): castle inline 8.67 (GPU 8.0, gaps 0.5), order thread 8.38-8.56 (7.9-8.0, 0.3; idle 0.00 and
fence join 0.75 in the 8.38 run); Kokiri inline 8.57 (7.6, 0.8), order thread 7.96 (7.3, 0.4). Market: game-bound (PIPE2: order thread idle 1.9-2.9).

Reading:
1. **No game thread waits for the GPU any more in a way that sets the frame.** D1 is gone; D2 costs 0.0-0.75 ms on a thread and
   only where the GPU is the limit anyway (native Kokiri on the 3050); D3 is the game waiting for our own chain.
2. **On the PC 3050 at the default settings the frame is the replay's serial chain (D4), with or without the order thread**: the
   order thread never idles, the GPU idles 1.1-4.8 ms a frame behind it, and the game waits 0.85-2.8 ms a frame for it. The GPU
   needs 12.2-14.4 ms (its time plus uploads) of 14.2-18.1 ms frames.
3. At 1440p native both PCs are within 4-17% of the GPU's own work (3050: 9-17% inline; 5070 with the order thread: 4-6%).
4. The Market is the game's own loop on both PCs (order thread idle 0.56 on the 3050, 1.9-2.9 on the 5070).

## 2. The design

### 2.1 The pipeline: game -> spine -> lanes -> GPU

Three stages joined by bounded queues, every value that crosses a stage with a fixed lag:

- **Game** (frame N+1's CPU): reads GPU results from the ring only (D1 today, D2 in stage 5), never asks the GPU's timing; hands
  its submissions over after their capture (the order thread, PIPE2).
- **Spine** (one thread, the order thread): what must stay in stream order and nothing else: the memory work, the dry pass with
  its guest-memory effects, the small command buffers. It never records a segment and never waits for one.
- **Lanes** (the replay workers): the segments of up to K groups at once, each reading its own submission's state (epoch, capture
  view, write-back and synced lists, segment table, render conditions); the last lane of a group ends its submission (write-backs,
  the timeline's signal) and sends it. `vkQueueSubmit2` calls keep the stream's order (a group's segments, its tail, then the next
  submission's first recording).
- **GPU**: one queue. A second queue stays closed (ASYNC1) until D9 is mapped; nothing here needs it.

What must become per-recording or per-submission state (from `recording-state.md`, checked against main's code): the epoch and the
recording's value (REC1, built), the capture view (SUB1 A, built one capture deep; needs K deep), the pool's sync pair (SUB1 A,
two pairs; a short list for K), the write-back and synced lists (SUB1 A), the segments' init table (REC1), the uniform updates'
positions and prune (REC1, SUB1 A), the render conditions (one vector today: per group, not built), the end of a submission taken
by its last lane (SUB1 B, built), `rb_joined` and the ring's staging per submission (SUB1 B).

### 2.2 Stages, each behind a switch that is off, with its expected gain and its proof

| Stage | What | Switch | Expected, from the map | Proof |
|---|---|---|---|---|
| 1 | `refactor/sub1` (REC1 + SUB1 A + B) merged onto today's main: the state per submission, the tail of a submission sent by its last lane | `RX_VK_SUB_OVERLAP` (exists) | small alone (SUB1 on the 5070: replay not ready for segments 1.08 -> 0.79 ms): the next submission's capture ends the window. It is the base | `RX_VK_REC_OVERLAP_CHECK=1`: 0 segments differ from the plan, every per-submission count 0; `tests/test_recording_state.py`; tour with `RX_VK_FLASH=1` |
| 2 | The spine goes on beside pending groups: the next submission's memory work, small command buffers and dry pass while the lanes record; render conditions per group, the capture view K deep, the join made by completion | `RX_VK_LANES=<K>` (new) | PC 3050 default ini: the spine loses "segments' recording and wait" (8.0-8.7 of the 16.2-17.3 ms of submissions in the profiled run) and falls under the GPU's 12.2-14.4 ms: Hyrule 16.23 -> ~13.4 (+21% on inline, +15% on the order thread), Kokiri 14.60 -> ~12.6 (+16%, +13%), castle -> the GPU's 13.4-15.2, Market -> its game loop (~17.2: +5-9%). Native 1440p: 3050 +9-14% at most, 5070 +4-6% at most | a check mode that replays a group serially and compares draws, dispatches and write-back lists; 0 flashes over the warp tour; GPU gaps under 0.5 ms and order thread idle above 0 in the 3050's logs |
| 3 | The order thread (PIPE4's hand-over) merged under the lanes, its present on the spine | `RX_VK_ORDER_THREAD` | already measured: +5-8% on the 3050, +2-6% on the 5070; with stage 2 the two bounds above | PIPE4's tour: 0 dark frames |
| 4 | Fewer submissions a frame: a group's segments sent together when they are ready together | `RX_VK_LANE_BATCH` (new) | "sent, GPU not started" 0.87-1.00 ms a frame at 28-31 recordings (3050 native) | the feed log's line |
| 5 | D2 into the ring (0x3980, then the lists) with the ring's lag, R6 only after its CPU writers are mapped | `HL_RB_R4` (new) | 0.4-0.75 ms a module worker in native Kokiri on the 3050; elsewhere under 0.1. No fps expected where the GPU is the limit; it removes the last reads that depend on the GPU's timing | `HL_RB_LAG_CHECK` at lag 1: 0 differences; cut-grass scene |
| 6 | D7 on the GPU (conditional rendering from query results), only if stage 2 leaves the GPU as the limit | `RX_VK_OCC_GPU` (new) | at most the 5% of `occlusion.md` (8.5 -> 8.1 ms) | `RX_VK_OCC_REAL=4` pictures as the reference |

### 2.3 Risks

- One-frame flashes: a lane reading the next submission's bytes (the capture view) or a pool synced by a later submission. SUB1's
  counters catch both; they must read 0 before any picture run.
- The dark frames of the order thread: an ordering fault at the present (PIPE4). The lanes do not change the spine's order of
  events and waits; the present still needs the frame whole, so it joins every pending group.
- The exposure: the ring's lag must follow the pipeline's depth (2 inline, 3 with the order thread; unchanged by lanes, which
  finish inside the frame). The freeze of 23 Nov was a range dropped by the ring; the take-over rule is on main.
- Zone loads: the spine's queue grows while the game loads; K bounds the lanes, `RX_VK_ORDER_AHEAD` the presents.
- The Market stays the game's loop: nothing here moves it.
- `pipe4f` freezes on the PC 3050 (above): stage 3 waits for PIPE4's last state.

## 3. The owner's question (24 Nov): can the replay go, or the preparation of the commands be made cheaper?

The lanes make the preparation parallel. Whether the work itself can go was measured before this stream; nothing was run again.

### 3.1 Reusing the frame before's recording

Measured by MEMO1 on 22 Nov (`docs/research/memo1-replay-reuse.md` on `perf/memo1`, `RX_VK_REPEAT_STATS`, main PC, Market / castle /
Kokiri, the camera moving in the first two; every draw hashed with its program, pipeline and dynamic state, texture and sampler
handles and what they resolve to, vertex, index and storage buffers, arguments and targets; uniform contents left out):

- **The stream is not stable by position**: the draw count differs from the frame before's in about 100% of the frames (culling),
  and 2.1-3.1% of the draws are identical to the draw at the same place of the frame before (1.8-4.0% two frames before). A segment
  is a run of ~128 draws at a place, so a segment identical to the frame before's is rarer than that; whole passes identical to
  the frame before: 1.5-4.7% of the draws, 1.1-2.2% of `draw()` time. **A recorded secondary command buffer has nothing to be
  reused for.** (A per-segment hash was not run: the per-draw figure bounds it.)
- By hash anywhere in the two frames before: 33-40% of the draws but 14-22% of `draw()` time (the cheap draws repeat).
- What breaks it: the texture handle words and the registrations behind them change every frame for the costly draws (with
  textures left out of the key 45-76% of the time repeats); the uniform blocks' addresses move in the game's ring; storage and
  vertex buffers alternate with period 2; visibility changes the draw list.
- Worth, on the 5070's critical path (MEMO1 section 4): 0.21-0.57 ms a frame (2-6%) for strict repeats, 0.40-0.83 ms (5-9%) with a
  texture-free key; the ceiling with every draw repeating and half its cost saved 0.63-1.7 ms. Against the PC 3050's 8.0-8.7 ms of
  "segments' recording and wait" the same shares give a few percent; the lanes take that whole item off the chain. Not measured on
  the 3050.
- The dry pass (4.8 ms of the 3050's chain) cannot be replaced by a hash walk: a span's effect on the state depends on the state
  going in (MEMO1 section 5.4; `replay-cost.md` R6 item 3).

### 3.2 Removing the replay for a pass (direct emission)

The verdict of 22 Nov (`refactor-plan.md`: heavy passes are 54-63 draws, 95% of their uniform blocks are written after the recording,
ceiling ~5% of CPU, dropped) stands with this map, for a reason the map adds: **no pass holds the cost**. The frame is ~2,400 draws in
60-118 passes (MEMO1), each draw 5.8 us on the 5070 and 10.5 us on the 3050 (`prof-A`: draw idx 24.83 ms for 2,362 draws), so one
heavy pass is 0.3-0.7 ms summed over the recording threads and a quarter of that on the chain. The replay goes only where most
draws are produced natively (the draw loop `0x80255be0`, `direct-emission.md` brick D), and the dry pass's stream-ordered effects
(1,300 report counters, 641 render conditions, 85 CPU clears a frame on the 3050) stay with whatever is still translated. No pass
was found where it pays alone.

### 3.3 What this changes in the design

Nothing in the stages. One risk it points at, not measured yet: lanes add no CPU. On 16 Nov the 3050's threads ran 74-83 ms of CPU a
frame in Kokiri and Hyrule (`frame-truth.md`: 3.4 cores at 21.6-24.3 ms frames); at the 12.8-13.3 ms the lanes aim at that is 5.6-6.5
cores of a 6-core, 12-thread CPU. If the CPU is that full, the summed cost of the preparation (not its place on the chain) becomes
the limit, and MEMO1's section 5 (the texture resolution kept per thread, the program pick, same-frame duplicates: estimated there at
0.2-0.5, 0.2-0.4 and 0.1-0.4 ms of the 5070's critical path, none built) is where to look. To settle before stage 2: one PC 3050 run at the default ini with `HOS_THREAD_TIMES=5`.

## 4. Stages as built

### 4.1 Stage 1: the state per submission on today's main (`5054c6330`, build `dec1a`)

`refactor/sub1` (REC1, SUB1 A and B, 22 Nov, never merged) merged by hand over 153 commits of main: 17 conflict hunks (WBL1's lazy
flush and orphans, EXPO1's ring standing, CRASH3's guessed write-backs, SMALL1's prefix job). Six checks that cut the changed source
followed (`a1ce2732f`). Suite on `a1ce2732f`: 511 passed, 3 failed (the known three; `E:\OOT\scratch-decouple\tests-a1ce.log`).

Check runs (main PC, slot a, machine shared, 175 s runs, Kokiri; `build/s5/dec1a-chk-*` in the main checkout):

| run | switches | joins | segments that differ from the plan | workers' reads of value and epoch / differ | capture entries read by workers / newer than their submission | pool sync checks / synced by a later submission | list additions / to another list |
|---|---|---:|---:|---|---|---|---|
| `dec1a-chk-off` | `RX_VK_REC_OVERLAP_CHECK=1` | 14,400 | 0 | 31,906,614 / 0 | 24,096,551 / 0 | 31,742,976 / 0 | 2,911,351 / 0 |
| `dec1a-chk-on` | `RX_VK_SUB_OVERLAP=1 RX_VK_SUB_OVERLAP_CHECK=1` | 13,800 | 0 | not counted (*) | not counted (*) | not counted (*) | not counted (*) |

(*) the memory side's counters are read with `RX_VK_REC_OVERLAP_CHECK` only: this run's line reads 0 of 0. The run with both
switches is in series 2 (`dec2a`). With the switch on: 4,596 submissions handed off with their last segments pending, their ends
sent by a worker 3,109 times and by the replay thread 1,487, mean window 2.331 ms; no fatal line.

So the merge keeps REC1's proof with every switch off: no command stream changes. Speed is not read from these runs (another game
and a build shared the machine: the GPU's busy time moved from 10.0 to 17.1 ms a frame between the two runs).

### 4.2 The order thread merged (`a5baf50e9`), `RX_VK_CAP_DEPTH` (`09484e740`), build `dec2a`

- `perf/pipe2` at PIPE4's hand-over tip `f24afbe94` merged: one conflict (`memory_read_as_submitted`: the submission's capture view
  and PIPE3's stale count). `RX_VK_SUB_OVERLAP` is left off when `RX_VK_ORDER_THREAD` is on (logged): the pending submission's end
  is completed "on the replay thread" and by the next capture, neither of which is the order thread's job model yet.
- `RX_VK_CAP_DEPTH=<1..4>` (default 1, as SUB1): the entries a capture replaces are kept that many deep a page; a worker reads the
  newest one not newer than its submission's limit; the check line counts the views deeper than the history. Rules in
  `engine/gpu/vk/cap_view.h`, test `engine/tests/test_cap_view.py`. Inert until several submissions' segments record at once.
- Suite on `09484e740`: 512 passed, 3 failed (the known three; `E:\OOT\scratch-decouple\tests-dec2a.log`).

Series 1 and 2 of check runs with the switch on (main PC, slot a, machine shared; `build/s5/dec1a-*`, `build/s5/dec2a-*`):

| run | build | scene | switches | result |
|---|---|---|---|---|
| `dec2a-chk-on` | dec2a | Kokiri | `RX_VK_SUB_OVERLAP=1` and both checks | 15,000 joins, 0 segments differ; 34,001,020 reads of value and epoch, 0 differ; 24,139,193 capture entries read, 0 newer than the worker's submission; 33,144,573 pool sync checks, 0 by a later submission; 2,884,215 list additions, 0 to another list; 0 views deeper than the history; 4,989 hand-offs (ends sent by a worker 3,524, by the replay thread 1,465), mean window 2.836 ms |
| `dec1a-chk-on-m` | dec1a | Market (`market-mid`) | `RX_VK_SUB_OVERLAP=1`, its check | **never reached play**: the title, then the load screen (5 draws a frame, up to 360 fps) until "skip title: gave up after 120 s" |
| `dec1a-tour-on` | dec1a | Market (`market`), warp tour | `RX_VK_SUB_OVERLAP=1` | **never reached play**, the same way |
| `dec1a-tour-off` | dec1a | the same tour | none | play at 68.65 s, the five maps, 0 flashes, the meter's held reads 177 / 503 / 11 / 0 / 0 / 0 |
| `dec2a-m-on` | dec2a | Market (`market-mid`) | `RX_VK_SUB_OVERLAP=1`, both checks, `RX_VK_CAP_DEPTH=2`, the wait log, a job trace | play at 70.87 s |
| `dec2a-m-dj2` | dec2a | Market (`market-mid`) | `RX_VK_DEFER_JOIN=2`, REC1's check | play at 77.68 s; 16,200 joins, 0 segments differ |
| `dec2a-ot-on` | dec2a | Kokiri | `RX_VK_ORDER_THREAD=1`, the feed log | play at 98.01 s; frame 8.22-8.25 ms = GPU busy 7.45 + idle 0.77-0.79 (replay not ready 0.52-0.54); order thread idle 0.23-0.27, fence join 0.97-0.99 ms a frame |
| `dec2a-tour-ot` | dec2a | warp tour | `RX_VK_ORDER_THREAD=1` | play at 89.85 s; Market, Hyrule Field, Zora's River, Hyrule Field (the Kakariko warp did not take, as in PIPE4's on tours); **0 flashes, no hang**; 1 present forced ("waited its time", after present 2243), 7 waits gone past; the meter's held reads 11 / 0; the longest frame 5,454 ms |
| `dec2b-warm` | dec2b | Kokiri | none | **never reached play** (16:14; see below) |
| `dec2b-m-on-a` | dec2b | Market (`market-mid`) | `RX_VK_SUB_OVERLAP=1`, both checks | **never left the first screen** (1 draw a frame for 150 s) |
| `dec2b-m-on-b` | dec2b | Market (`market-mid`) | `RX_VK_SUB_OVERLAP=1` | play at 84.58 s |
| `dec2b-tour-on` | dec2b | warp tour | `RX_VK_SUB_OVERLAP=1` | play at 71.83 s; Market, Hyrule Field, Zora's River, Hyrule Field; **0 flashes**; the meter's held reads 9 / 10 / 0 / 0; the longest frame 2,981 ms |
| `dec2a-m-on-c` | dec2a | Market (`market-mid`) | `RX_VK_SUB_OVERLAP=1` | play at 55.40 s (`dec2a-m-on-b` did not start: the slot's exe was still held by the game before) |

`dec2a-tour-ot` is the first run in the game of PIPE4's last rule (`pipe4g`: the watch spares a stopped queue only while the order
thread finished a job in the last 250 ms), here merged under the per-submission state: one tour, no hang at a load, no dark frame.
One tour is not a verdict (pipe4f had one clean tour before it hung).

**Loads that never reach play: not the switch's doing, as far as this day's runs can say.** With `RX_VK_SUB_OVERLAP=1` the load of
a Market save did not finish in 2 runs of 2 on `dec1a` (15:27-15:33; the load screen at 0.1-0.6 fps for 5-11 s at a time, then idle
at 360 fps until "skip title: gave up after 120 s"), which looked like the switch. It is not shown to be: (1) the same run reached
play on `dec2a` (`dec2a-m-on`); (2) in series 3 a run with **no switch** failed the same way (`dec2b-warm`, Kokiri, 16:14: the load
screen until the title skip gave up; "the game read no controller for 11.2 s", presents at 0.1-0.3 fps) and the next one never left
the first screen (`dec2b-m-on-a`, 1 draw a frame at 360 fps for 150 s); (3) other streams' runs on this PC gave up at the title the
same way today (`c3-v42b` 13:10, `U-d1` 13:30, `U-d2` 13:36, `c3-v42d` 14:01, `V-t` and `p4t13-on` 14:16: `skip title: gave up` in
their `build/s5/<run>/game_log.txt`). The machine ran two games and a build or a lift through all of it. Over the day, the Market save with the switch on: `dec1a` 0 of 2, `dec2a` 2 of 2, `dec2b` 1 of 2 (the
failed one before the title). To settle on a quiet machine: the Market save with the switch alone, three runs, on `dec2b`.

One real gap was found by reading while looking for a cause and removed in `e67d919c8` (build `dec2b`): CRASH3's guessed write-backs
kept their snapshots in one map that every submission's start cleared; a pending submission's guessed write-backs would not be
checked or written once the next one had begun. With SUB1 B as built the pending end is always sent before the next submission's
start, so this was not live; it would be with the lanes. The snapshots are now the submission's own (`MemSub::guess_snaps`).
**Correction to `e67d919c8`'s commit message** (it cannot be amended: later commits sit on it): it calls the gap the "suspected cause
of the Market save's load never reaching play with the switch on" and says the map was cleared "while the pending one's workers
still added to it". Neither holds: as built, the pending submission's end is sent before the next one's start, so the gap was not
live with `RX_VK_SUB_OVERLAP=1`, and a run with no switch failed its load the same way (`dec2b-warm`). The commit removes a gap the
lanes would open; it is not shown to be, and is not expected to be, the cause of the load failures.

### 4.3 Stage 2 (`RX_VK_LANES`), not built: the recipe, from the code as merged

What stops the spine today, in order, and what each needs (one pending submission at a time is enough for most of the gain: the
spine then waits only when the lanes are a whole submission behind):

1. **`sub_complete_before_submit()` in `submit_recording`** (and `ReplayLock`): the first recording of submission N+1 that goes to
   the queue completes N (waits for its workers, sends its tail). Needed instead: a **submit sequencer**: every recording takes a
   ticket in stream order when it is begun (N's segments, N's tail, N+1's first recording, ...); a recording that is ready is sent
   if every ticket before it is sent, else left for whoever sends the one before (the group's `early_submit_ready` already works
   this way inside a group). Pure rules in a header with a test, as `completion_tracker.h`.
2. **`memory_begin_submission` / the next capture's take beside N's workers**: the lists are per submission (SUB1 A), the capture
   view is `RX_VK_CAP_DEPTH` deep (built), the pool's sync pair is two deep (SUB1 A): enough for one pending submission. The
   pre-sync of N+1's big command buffer marks pools for N+1 while N's workers still bind: covered by `pool_synced_for`'s pair
   before; the check's "pool synced by a later submission" must read 0 only for checks that then took the wrong pair (to refine).
3. **`par_conds`**: one vector, reset at each dry pass. Per group (`EarlyGroup`), a segment's `cond` an index into its group's.
4. **`ub_prune` and `ub_updates.resize(ub_before)`** beside pending readers: SUB1 A's exclusive lock covers the prune; the resize (a
   command buffer replayed twice) must join first.
5. **The order thread's accounting**: a submit job's value is done (`ot_value_done`, fence joins, `pushed`) when its tail is sent, not
   when `run_submit` returns; the present joins every pending group (it needs the frame whole), so do `wait_queue_empty`, texel
   reads, releases.
6. Check mode: `RX_VK_LANES_CHECK=1` runs every group joined (as today) but takes tickets and views as if pending, and counts a
   ticket sent out of order, a view lost, a pool pair missing; all 0 before the switch is tried in the game.

Gate for the switch: on the PC 3050 at the default ini, order thread idle above 0.5 ms a frame and GPU gaps under 0.5 ms in Kokiri
and Hyrule (today 0.01-0.06 and 1.75-2.12), fps +8% or more over the order-thread arm; 0 flashes over the warp tour; the meter's
held reads as off.

### 4.4 Stage 2's first pieces (`b49b55244`, build `dec3a`): the sequencer, the conditions per group, the check

- **The submit sequencer** (`engine/gpu/vk/submit_seq.h`, pure rules; `tests/test_submit_seq.py` with no game and no GPU). A unit
  of the stream takes a ticket when the spine begins it: a plain recording (`begin_recording`), or a big command buffer's group
  (taken in `replay_handle_early` after `r` is sent and before `rd` is begun; without `RX_VK_EARLY_GPU`, at the same place).
  A unit is finished where it takes its place in the queue: a recording in `submit_send` under `present_mu` (batched or not), a
  group in `group_lane_close` once every segment it uses is sent (`early_submit_ready`, before the submission's end, whose
  recording's ticket follows the group's), or at the end of `early_join`. `rd`, when it goes inside the group (`sub_after_rd`),
  drops its own ticket; a group recorded whole drops its ticket (or finishes it when its small-prefix job was sent). A recording
  made ready while an earlier unit is open is left ready (`Sequencer::ready`) for whoever finishes the one before (`ready_head`):
  the lanes' rule. Not covered: the segments of the multi-segment path (`kJoinMultiSeg`), the early pre-sync's recording and the
  upload chunk flush (`replay_flush_upload`), which have no tickets.
- **The render conditions per group**: `par_conds` / `par_conds_n` now name the writer's list (`conds_w`, one of a ring of
  `1 + 4`); a group holds the list its dry pass writes from its creation to its join (`group_conds_hold` / `_release`), its workers
  read it through `tl_conds` (`conds_r()`: the render condition's read and `gate_segment_begin`). With `RX_VK_LANES` or its check a
  dry pass's reset (`par_conds_reset`) takes the next list no group holds; with both off the writer's list is always the first, as
  before. Tests updated in the same commit: `test_grow_lock.py` (the fixture names the writer's list as the source does, the read is
  checked through `conds_r()`), `test_seg_early.py`, `test_early_gpu.py` (the group's ticket is finished before the submission's end).
- **`RX_VK_LANES_CHECK=1`**: every group joined as today; the tickets taken and finished as above, the lists rotated, and the
  memory side's per-submission counters on (`rec_check_on()`; the capture history kept as with `RX_VK_SUB_OVERLAP`). Every 600 joins
  the log's `vk: LANES_CHECK:` line gives tickets taken / sent / dropped, **sent out of order**, **unknown** (finished twice or never
  taken), the most open at once, lists rotated, **a dry pass that found every list held**, **capture views lost** (deeper than
  `RX_VK_CAP_DEPTH`), **pool sync pairs lost** (new: `st_sub_pool_lost`, a pool synced twice past the checking submission). The
  bold ones must read 0 in Kokiri, Hyrule and the warp tour before `RX_VK_LANES` is tried. With both checks off nothing changes
  but one pointer's indirection on the conditions' list.

### 4.5 Step 3a and the lanes for one group (`7f12b5ae8`, `ac7d78a3a`, build `dec3b`)

- **`RX_VK_SUB_OVERLAP` on the order thread.** A pending submission's end needs nobody's join: whoever puts its group's last
  segment in the queue sends it (`group_tail_send`). What the order thread lacked is its accounting: `ot_main`'s `finish()`
  published a submit job as done (`ot_value_done`, `ot_seq_done`, which the game's fence waits and `ot_join_all` read) when
  `run_submit` returned, before its end was sent. `submit_seq::DoneHold` (pure rules, tested) now keeps the marks just before such a
  job, the jobs behind it finishing meanwhile, until `ot_end_sent(value)`: the end's send or the join (`defer_join_now`). An end
  sent before the job's `finish()` runs is remembered (`sent`), so it holds nothing. The restriction in `sub_overlap_on()` is gone;
  the order thread's log adds "N submit jobs returned with their end pending (done marks held), M released".
- **`RX_VK_LANES=1`** (one group). The join that a big command buffer's start made of the pending group (`kJoinEarlyStart`, JOIN1's
  rule) is moved to the end of its dry pass: the older group (`lane_prev`, never a submission's end: that is still joined at the
  next submission's memory work) records beside the pre-sync, the segments' set-up and the dry pass, and is joined
  (`lane_prev_join`) before anything of the new group goes to the queue. With the early GPU path, `r` (the work before the command
  buffer, sent before the dry pass otherwise) waits too: it goes at the dry pass's end in `rd`'s place, every segment held until
  then (`dry_uploaded`). Any join made meanwhile joins the older group first. Its join leaves `par_active` on under the new group's
  segments (`lane_next`). `RX_VK_DEFER_JOIN` is 2 by default under the switch. Log every 240 big command buffers: groups kept, joined
  at the dry pass's end (and how many had recorded everything by then), joined by another join, the join's mean time.
- What runs beside the older group's workers now and did not before: `memory_sync_mirrors_before_segments` (a pool synced by the
  new pre-sync reads as synced after the older workers' segments: they sync it again in their own upload, redundant, not wrong),
  `texture_rb_refresh_now` and `memory_rb_segments_begin` (one global `rb_segs`: an older worker's refresh after it does not find
  its segment; the meter's held reads are the check), `texture_segments_begin` (per group since REC1), the conditions' list (per
  group, 4.4). Not proven: the check runs of series 5 are the proof.

### 4.6 matrix81 (PC 3050): `RX_VK_SUB_OVERLAP` halves the fps, the cause, the fix; the CPU gate

Copy `E:\OOT\scratch-decouple\3050\matrix81.txt`; logs on the 3050 `C:\OOT\pc3050s-out\dc81`, the ones read here copied to
`E:\OOT\scratch-decouple\3050\dc81\` (kA1, kB1, kChk, hB1, hT). Build `dec1a`, default ini, replay inline.

| arm | Kokiri fps (3 passes) | Hyrule fps (3 passes) |
|---|---|---|
| plain | 58.5 / 58.3 / 58.3 | 56.6 / 57.1 / 57.6 |
| `RX_VK_SUB_OVERLAP=1` | 30.3 / 30.3 / 30.3 | 25.5 / 25.6 / 25.9 (531-599 frames over 50 ms each) |
| both checks (kChk) | 30.2 | |
| `pipe4f` order thread, `HOS_THREAD_TIMES=5` (hT) | | 52.7 |

**The cause** (kA1 against kB1): the mirror syncs copy 10.39 MB a submission under the switch against 0.13 MB
(`vk: msync bytes`: presync 10.389, "guest memory 10.268"), 1,538,659-1,577,122 dirty pages from guest memory in 600 submissions
against 0 (`vk: mirror syncs (600 submits)`); the GPU's "uploads" 12.5 ms a frame against 0.2; the presents alternate 2-3 ms and
42-44 ms intervals (`present_trace.csv`: medians 15.5 against 17.6 ms, but the long ones 42-44). The main PC had it too, unseen in
fps: `dec2a-chk-on` 3,061,542 and `dec2b-tour-on` 4,744,625 pages from guest memory against 0 in `dec1a-chk-off`, `dec2a-ot-on`,
`dec3a-lc-k` (`build/s5/<run>/game_log.txt`). Mechanism: `memory_submission_handoff` fixes the submission's synced generation
(`gen_end`) at the handoff; the last group's segments then stamp the pages their GPU writes touch (`memory_stamp_gpu`) with newer
generations, so the next submission's syncs find those pages dirty with no capture of that generation and copy them again from guest
memory. Without the switch the end takes `gen_now`, those stamps included. **Fix `64f941b83`** (+ test `52aaa76d4`): the end takes
`gen_now` unless a generation for the CPU's writes (`gen_cpu_last`: a collection, a capture's take, the lazy write-back's flush
stamps) was taken since the handoff, which today's joins rule out (the next submission's memory work joins first). The lanes across
submissions will need a per-page answer there. Build `dec3c`; series 7 (`c7.sh`) checks the counter and the tour. **dec3b's step 3a
carries the same loss** (its `RX_VK_SUB_OVERLAP` arms run the same end); `RX_VK_LANES=1` alone does not (it never hands a submission
off). No 3050 list with `RX_VK_SUB_OVERLAP` arms before series 7 reads 0 pages from guest memory.

**The gate.** hT (Hyrule, order thread, 17.89 ms a frame; `prof: threads in 5 s`, last block): our threads 201% of a logical CPU
(order thread 81, replay workers 19+20+18+22, capture copy 6+7+6+7, held submits 8, Presentation Thread 7), the game's named threads
279% (main 29, module workers 28+22+26+20+21, executors 22+22+23+18+15+17, navmesh 10, texture compaction 6), three unnamed threads
95 + 94 + 92% (not identified); 761% in all on a 6-core / 12-thread CPU. The slack line: "the PT waited 15.82 ms a frame for the main
thread's hand-over ... game-bound"; the order thread idle 0.18 ms a frame. LIFT3's sampled profile (Market, 19.55 ms frames,
`E:\OOT\scratch-lift3\p3050.ana.txt`): the game threads' non-waiting time 68 ms a frame (executors 39.8, module workers 22.8,
main 5.5), about 3.5 cores; the replay thread busy 18.3 of 19.55 ms, 10.2 of them in the driver under the queue submits (the
coordinator's reading of that run). Arithmetic, not a measure: the named threads' 4.8 CPUs at 17.89 ms are 85.9 ms of CPU a frame;
at the GPU's 11.9 ms (hT's GPU time) that is 7.2 CPUs, at the lanes' 13.4 ms aim 6.4, both above the 6 cores (the unnamed 2.8 CPUs
come on top if they scale with the frame). **Withdrawn (4.7): hT ran with a cold shader cache; the unnamed threads are the shader workers.** The first reading was: the i5-10400F cannot feed the lanes' aim at the default ini: with about 5
cores for these threads the frame bound by CPU is ~17 ms (85.9 / 5), today's 17.89 within a few percent; one lane (K=1) is the
most to try there, and its gain is expected small until CPU is shed. Where to shed first: the three unnamed threads at 92-95% (to
identify: if they spin, 2.8 CPUs), the order thread's 10.2 ms a frame in the driver's queue submits (fewer submissions: stage 4,
`RX_VK_LANE_BATCH`), the capture copy threads (0.26 CPU), the held-submits thread (0.08). The lanes stand for machines with more
cores (the 5070 / 7800X3D: 8 cores, 16 threads).

### 4.7 Series 6-7 (dec3b, dec3c), the lanes inert in this game, the order thread's overlap flashes, the gate reopened

- **Series 6** (`dec3b`, `E:\OOT\scratch-decouple\c6-dec3b.log`): `dec3b-ln-k`, `dec3b-ln-h` (order thread, `RX_VK_LANES=1`,
  both checks): every check counter 0, at most 3 tickets open, 0 flashes, the meter's held reads in Hyrule 971 / 997 / 1000 / 1000.
  `dec3b-tour-ln`: 0 flashes. **But the lanes did nothing in any of them**: "groups kept pending past the next one's start 0" in
  all three. Every early group is the last big command buffer of its submission: DEFER_JOIN_CHECK's "joins made by: ... end of
  the command buffers' loop 1.00" (every other reason 0.00, `build/s5/dec3b-ln-k/game_log.txt`). So no submission has a second big
  command buffer for the pending group to overlap; the switch and the tour's ini or save are not the reason. The lanes can only work
  across submissions: the pending group is a submission's end (`RX_VK_SUB_OVERLAP`'s hand-off), and the join to move is the one at
  the next submission's memory work. `RX_VK_LANES=1` as built is therefore arm O again, and is left out of the PC 3050's list.
  `dec3b-tour-soot`: 16 flashes.
- **Series 7** (`dec3c` = the sync fix, `c7-dec3c.log`): `dec3c-so-k` (inline `RX_VK_SUB_OVERLAP=1`): 0 pages from guest memory,
  uploads 0.1 ms a frame, 0 flashes: **the fix removes the copies**. `dec3c-soot-k` (order thread + overlap + checks): counters 0,
  5,950 submissions handed off, 0 flashes. `dec3c-tour-soot` (order thread + overlap): **19 flashes**, 564 pages from guest memory,
  "the longest submission 86.0 ms ... gaps 22.7 ms (the longest 118.1)". So the tour's flashes are not the sync fault.
- **The flashes, looked at** (`E:\OOT\scratch-decouple\shots\dec3c-tour-soot\flash_<present>_{a,b,c}.png`): two kinds.
  (1) Presents 2497-2526 (10 flashes, cells 14-49 x 19-27, peak 71-81): mid-play in the Market at the spawn, before the first warp;
  `flash_2509_b` against `flash_2509_a`: the floor under the gate loses its shadow for one frame (lighter), and the frames around it
  alternate darker / brighter on the same cells: the floor's shading flickers from frame to frame. (2) Presents 2880, 2995, 3064,
  4140 (whole screen, 873-1208 cells brighter, peak 240-245): `flash_2880_b` is the arrival in Hyrule Field (its title card on
  screen): the whole scene blown out white, the exposure of another scene: a warp's first frames. Without the overlap the order
  thread's tours read 0 (`dec2a-tour-ot`, `dec3b-tour-ln`, where the lanes were inert); inline with the overlap before the fix
  `dec2b-tour-on` read 0 (with every GPU-written page copied again from guest memory, which may have hidden a stale read). The inline
  tour on `dec3c` (series 8, `E:\OOT\scratch-decouple\c8-dec3c.log`, `build/s5/dec3c-tour-so`, 19:14): **0 flashes**, all five maps, 0 pages
  from guest memory, the meter's held reads 12 / 365 / 686 / 11 / 5 / 0 / 0 / 0 / 455. So the flashes come with the overlap **on the order
  thread** only (step 3a, `7f12b5ae8`): not explained yet (suspects: the done marks held by `DoneHold` let a fence wait through
  before the end's write-backs, or the game threads' captures taken beside a pending end); the order thread with the overlap is not
  sent to the 3050.
- **The gate is reopened.** hT started with a cold shader cache for its build ("12 graphics pipelines from 5354 notes carried over
  from another shader translation (5342 notes name no program)", "noted pipelines warmed (in play) ... 5168 left out", where kA1 reads
  "5309 graphics pipelines ... built in 1586 ms"); its three unnamed threads at 92-99% are the background shader workers, in 16 of
  its 21 thread-time blocks; a warm session on the main PC shows unnamed threads at 6-28% (the coordinator's reading). hT's 761%,
  its named threads' times (contended by ~2.8 CPUs of translation) and its 52.7 fps are inflated; the arithmetic of 4.6 does not
  hold. The gate waits for list-3050-1's warm hT2.
- **list-3050-1** (`E:\OOT\scratch-decouple\list-3050-1.txt`, build `dec3c`): two prepare starts (0 left out), arms A inline,
  O order thread, S inline + overlap, Kokiri / Hyrule / Market, 3 passes interleaved, then hT2 warm. Next by its result: the lanes
  across submissions only if one lane can show a gain (it would be the overlap on the order thread, whose tour flashes first need a
  cause); else the next lever is the replay thread's ~10 ms a frame in the driver's queue submits, not more lanes.

### 4.8 matrix82 (PC 3050, dec3c, warm cache): the order thread gains, the inline overlap loses, a load hang 2 in 10

`E:\OOT\scratch-decouple\3050\matrix82.txt`; run logs of kO1, kO2, mO2, mO3 in `E:\OOT\scratch-decouple\3050\dc82\` (the coordinator's
reading of the fps; 1440p default ini, vsync off): Kokiri A 59.4 / 58.8 / 58.8, O 64.4 / 64.6 (kO1: hang), S 55.4 / 55.1 / 54.9;
Hyrule A 56.4 / 57.5 / 56.7, O 63.0 / 61.3 / 62.0, S 55.5 / 56.0 / 56.2; Market A 51.5 / 50.4 / 51.7, O 53.3 / 54.3 (mO3: hang),
S 50.0 / 50.5 / 50.0. The order thread +9.6% Kokiri, +9% Hyrule, +5% Market on its good runs; hT2 (warm, 60.9 fps): "the PT waited
13.46 ms a frame for the main thread's hand-over ... game-bound", order thread 75% of a core: **with the order thread the 3050 is
game-bound and has CPU left; the first gate verdict (4.6) was the cold cache's.**

- **`RX_VK_SUB_OVERLAP` inline stays off.** With the sync fix it no longer halves the fps but still loses 2-6%: kS1's replay time a
  frame draw 24.09 ms against 14.16, descriptors 12.69 against 5.75, the join 2.56 against 0.57 (coordinator's reading of
  matrix82): the pending group's workers record beside the next submission's serial recording and contend for the same locks and
  caches, for a window (the next submission's memory work joins) too short to keep the GPU busier. Its use is as the hand-off of the
  lanes across submissions, which is days of work (below).
- **The load hang with the order thread** (kO1, mO3; no FATAL): the presents stop 4.3 s (kO1, present 6205) and 2.9 s (mO3, present
  7122) after "play: began" (`present_trace.csv`); the order thread's 600-frame line stops (its last one is before play in both);
  the game's threads go on (vcores lines, OptimizeChangeBanc's recordings in kO1 growing to 400,000 with their live copies 21.8 ->
  50.8 MB: command memory never given back, its fences never reached). kO1 alone shows "ran without a core after 2000 ms ...
  OptimizeChangeBanc ... held 2000 ms at ntdll" (consequence, not cause: mO3 has none). Nothing in the logs says which wait. Built:
  the order thread's stall watch (`563bd7db9`, build `dec3d`): every thread's innermost renderer wait site in a table, logged with
  the order thread's job when its done mark stops for 3 s or no present comes for 5 s. Loop of starts on the main PC: `loop.sh`
  (`E:\OOT\scratch-decouple\loop-dec3d.log`), 12 starts, Kokiri and Market alternately.
- **The tour flashes** belong to the order thread **with** the overlap only: the order thread alone read 0 flashes over two tours
  (`dec2a-tour-ot`, `dec3b-tour-ln`), the inline overlap 0 (`dec3c-tour-so`). With the overlap off for good, they do not stand in the
  way of the order thread by default; their cause is left for the lanes' work.
- **After the next release** (estimates, not started): the lanes across submissions (skip the join in `submit_memory_work`, a
  per-page synced generation instead of `gen_end`, the capture view K deep in play, the order thread's overlap flashes, check runs
  and 3050 rounds): 3-5 days. Stage 4, fewer submissions a frame (`RX_VK_LANE_BATCH`, the replay thread's ~10 ms a frame in the
  driver's queue submits on the 3050): 1-2 days to a first measure.

### 4.9 The load hang named: the held present sent the timeline back, a late fix waited for good (`cc753074e`, build `dec3e`)

**Caught** on the PC 3050 (`E:\OOT\scratch-decouple\3050\matrix97.txt`, dec3d, 20 starts of the order thread, 90 s): 5 froze
(o2, o8, o12, o14 Market, o11 Kokiri: present traces of 40-42 s against 81-82 s), 40 watch lines. In four of them the order thread
sits in "a late fix (TICK-060)" on a submit job (o2: job value 51598, its done value 51597, 3.1 to 33 s), with a game thread
(ModuleSystemWorker, SceneLowPriority) in a late fix too; in o11 the order thread is in "present drain" on the present it holds
(holding 1, 33 s) while two game threads have been in a late fix 2 s longer: the flushes under the queue's lock wait 2 s each for
the same fix (late_fix_flush_locked, no wait site of its own). Stopped queues 2-4 in all five. No "timed out" line: the late fix's
waits loop without one.

**The cycle.** (1) The order thread holds a present (value P) until the CPU's signal its frame's stopped work waits for has come
(PIPE4), and runs the jobs behind it meanwhile, as it must (their work may set the events the stopped queues wait for). Their
values are above P; their recordings' ends signalled the timeline up to them (finish_recording: done_max, below the oldest stopped
submission only). (2) The present, recorded later, signalled P itself (do_present: `submit_recording(r)`, its own value whatever
was sent): a timeline signal below the current value, which a timeline must never get; the driver took it and the timeline went
back to P. `tl_signal_sent` is a maximum and kept the higher value. (3) A capture taken next whose late pages are written back by
one of the jobs behind P (late value L, P < L <= the highest sent) passed the deferral rule (RX_VK_LATE_DEFER: L at or below a
signal already sent) and became a late fix. (4) Every recording is then held behind the fix until L is reached
(submit_send: late_hold), and the timeline moves on only when a recording's end signals above the old highest value
(`at > signaled`), which only those held recordings would do. The fix waits for L, L waits for the held recordings, the held
recordings wait for the fix: for good. Inline (no order thread) presents are recorded in value order and it cannot happen; this is
why only the order thread froze, at loads, where presents are held (300 holds in 600 frames in kO1's lines).
The ~2 s stalls of kO1/mO3 (OptimizeChangeBanc "held 2000 ms") are the same thing before it locks: late_fix_flush_locked's 8 x
250 ms wait under the queue's lock.

**The fix** (`timeline_order.h`, `cc753074e`): the order thread notes the present's value (`ot_held_present`) from the moment it
takes the present job until the present is recorded; a recording's end signals below it as it does below a stopped submission
(`timeline_order::signal_at`), so the present's own signal still rises and the jobs behind it are reached by the next recording's
signal after it. The present never signals at or below the last signal (`signal_moves`; a line if it would: must read 0), and a
signal below one sent is said ("the timeline went back": must read 0). A capture's late value behind the held present is not
waited for (nothing reaches it before the present: the take goes on at once as a wait that ran out would, the cap does not learn
from it; "captures' late pages behind a present the order thread holds" counts them). Safety net, logged, in addition: a late
fix's wait applies the fix as it is after 2 s ("a late fix's value ... not reached in 2 s"). Tests: `test_timeline_order.py`
(fails before on the wiring; its sequence goes back without the held present's bound), `test_capture_wait_adapt.py` and
`test_capture_slot_ownership.py` updated for the new text.

**What it costs.** While a present is held (loads), the values behind it reach the timeline only once it is recorded: a fence wait
for them goes by the recordings' timeline (wait_recorded, as most do: "path recorded 300"); a wait that needs the timeline waits
for the present (at most RX_VK_ORDER_SIGNAL_MS, 2 s, as the held present itself). A take whose late pages are behind it keeps the
submit's copy of them, as a cut wait does (a possible one-frame exposure step during a load). Not measured yet: list-3050-3.
