<!-- Copyright (C) 2026 Sacrappy -->
# PIPE2: the stages' slack and the replay on an order thread (23 Nov 2026 -)

## Summary (PIPE3, 24 Nov)

1. **The present is off the order thread's chain** (`RX_VK_ORDER_PRESENT_THREAD`, on with `RX_VK_ORDER_THREAD`; section 8): a
   present thread of its own takes the present's job, the order thread hands it over as it reaches it and goes on, and a submit
   handed over after a present waits for that present to finish before it replays (`ot_present_join`), so the GPU's order cannot
   change. **It does not shorten the frame.** In Kokiri the gate that preserves the order costs 0.47-0.51 ms a frame in 587 of 600
   frames - the present's own serial part (0.44 ms of recording) - and the order thread's chain is 7.76 ms against 7.79 with the
   present in the chain (section 10): the next frame's submits reach the order thread before the present is done, so there is
   nothing to overlap. In the castle 0.33-0.43 ms a frame; in the game-bound Market 0.39 ms in 194 of 600 frames, where the order
   thread has 1.9 ms of idle and the present thread's 0.46 ms a present does fit out of the way.
2. **The Kokiri numbers are in one usable lease** (02:33-02:54): off 105.1 / 118.5 / 112.1, on with the present in the chain
   128.1 / 92.6, on with the present thread 128.1 / 115.2 / 119.9 fps (medians 112.1 / 110.4 / 119.9: the present thread +7.0%
   against off, +8.6% against the present-in-chain arm's median, on rounds that hitched differently). The frame the order thread's
   chain gives: 8.28-8.48 ms off, 7.79-7.86 (present in the chain) and 7.76 (present thread) on, against 7.10-7.20 of GPU work:
   the present thread's chain is the same as the present-in-chain arm's.
   **The castle series is not usable**: the machine was not fit during its lease (39-115 frames over 50 ms in every run, loads
   48-109 s against PIPE2's 40.4, the off arm as bad as the on arms).
3. **The Market's extra frames over 25 ms are the game's own loaders** (section 10a, one run each arm with `HL_STALL_WATCH=50`):
   `ARes_Loading_0..3` (15-47 ms of user CPU in a gap), `GeneralFormatAsync_`, `BaseProcCreateAndDeleteThread` at 96-100% of a
   458 ms window, the main thread asleep 301 ms of it; the order thread and the present thread idle in every dump. In this round
   the on arm had 4 frames over 25 ms against the off arm's 5 (and a better p50, 9.02 against 9.49): PIPE2's "the order thread
   adds stutters" is not reproduced.
4. **The flashes are not fixed.** The tour (01:48-01:56): 0 flashes off, **2 on** (presents 7268 and 7432, whole-screen dark
   differences, peak 225; PIPE2's were small ground patches), the meter 12 / 352 / 1171 / 2000 (the last window frozen, both arms).
   The counters say where the reads that can do it are (section 9): 45,568 texture pages whose shadow copy was not their
   submission's own (the split into "no capture of its own" and "a later capture's copy" was added after `pipe3b` was built, so the
   tour's number is the two together), 15,837 re-upload pages still read from guest memory, 357,929 first-upload pages by design
   (issue 66) - all reads that take the frame after the submission's under the order thread. A re-upload now takes the newest copy
   up to its submit (76,138 pages over the tour) and the flashes stayed. What a fix needs is PIPE1's stage 2 (a view per
   submission): the shadow keeps one copy per page, so a page written after the submit and re-taken since has no as-of-submit copy
   to read. The replay's other reads are counted too: 192,000 of 5.5 M in Kokiri and 5,632 of 140,000 in the Market took guest
   memory for a page written after its capture's copy.

## Summary (PIPE2, end of the gate series; 23 Nov 23:05)

1. **The gate does not pass.** One clean speed lease, `pipe2e` (`0bb228966`), 3 interleaved rounds a scene, fps from play + 10 s:
   **castle-move +6.2%** (median 127.6 -> 135.5; rounds +0.6 / +7.0 / +4.9), **kokiri-idle +4.7%** (121.2 -> 126.9; +6.7 / +3.1 / +4.5)
   against the brief's +8%. **The Market is inside its spread** (the clean round +0.3%, its frame p50 equal within 1%; the other two
   rounds hitched in both arms). **The meter is not held over a run** (0 of 2000 reads given the last result again in every on run;
   308-496 before the fix) **but the warp tour freezes it in both arms** (off's last window 2000 of 2000, the on arm 395/681 and 11 at
   the end). **Flashes: the on arm has 2 real one-frame picture differences in Zora's River plus one warp transition frame, the off arm
   0** (section 6); the pictures are otherwise right (same scenes, textures, HUD; the on arm one frame of exposure lag brighter).
2. **Why, from the same counters: the overlap works and the GPU's own work is the floor.** In every on run the PT waits 5.6-10.3 ms for
   the main thread's hand-over and the main thread 0.00 ms for the present (1.4-1.7 off); the order thread idles 0.01-0.18 ms in the
   replay-bound scenes and 1.9-2.9 in the game-bound Market. The CPU work above the GPU's own time per frame fell by 27-33% (castle
   0.73 -> 0.53 ms, Kokiri 1.04 -> 0.70). The GPU needs 7.6-7.7 ms a frame in the castle (94-95% of the on frame) and 7.1-7.2 in
   Kokiri (91-93%): **the castle's whole room is ~+6.7%** (the order thread takes +6.2% of it) and **Kokiri's ~+11%**, of which +4.7%
   is taken - Kokiri's +8% misses by ~0.3 ms of CPU above the GPU (needs 0.38, has 0.70). PIPE1's +10-16% upper bounds did not include
   the GPU's share.
3. **The meter's held reads are fixed for a run** (`0bb228966`, sections 4-6): the ring hands the meter's block its last applied entry
   back when no entry is new. Over the warp tour both arms still hold reads after a zone change (off worse: 2000 of 2000 frozen at the
   end) - the next zone's blocks have nothing staged yet; that is the meter's zone-change rule, not the order thread, and it is not
   fixed.
4. **Next** (in order): the present out of the order thread's chain (a present thread, FIF's, without FIF's contention) for Kokiri's
   ~0.3 ms - the env knobs tried (thread priority, pool spin, four recorders, section 7) give at most +0.8% and leave the CPU above the
   GPU at 0.6-0.7 ms; the two flashes' reads outside the capture view (stage 2: texture uploads, collected writes at replay time); the
   meter's first reads after a zone change (section 6); a valid PROF_FINE pair on the on arm (`series.sh`'s comma bug is fixed); the PC
   3050 list, which the coordinator routes.


Branch `perf/pipe2` from origin/main 4b599eb04 (PIPE1 merged, `RX_VK_TOUCH_FAST` on). Worktree `E:\OOT\HeyListen-wt\pipe2`. Design:
`pipe1-frame-overlap.md` section 3 (stages 0-3 with their gates). Main PC (RTX 5070); PC 3050 runs are routed by the coordinator.

## State (PIPE4 stopped 24 Nov 14:35; the order thread goes to DECOUPLE, branch perf/decouple)

**Handover (read this first).** PIPE4 is stopped by the owner's priority change. Nothing of PIPE4 is running or leased: the last slot
ticket was cancelled before it was granted, the pipe4g tours never ran, and slot c's own exe and dll are back (`bak-c` matches).
Branch `perf/pipe2` at the tip below holds everything; not pushed.

*The dark frames (evidence).* They are the lit scene drawn black for one or more frames (sky, HUD and Navi right:
`E:\OOT\scratch-pipe3\shots\p3t-on\flash_7268_{a,b}.png`), not a late read. On pipe4b, every big flash sits on a present that
**forced the stopped lit passes past their event waits** (`vk: wait gone past (a present, ...)`, 14 waits a present, events
`0x100d6c80xx` 19 values short: the lighting work that sets them was still behind the present in the order thread's job queue) and/or
on a present that **let go of the CPU's signal after 200 ms**: `p4t2-on` flashes 6358/6362/6520/6523 against forced presents
6357-6363 and 6512-6522; `p4t3-on` flashes 5712-5716 and 5936-5940 against let-go 5715/5939 and forced 5933-5942; the off arm
(`p4t2-off`, `p4t9-off`) has no wait forced by a present and 0 flashes. Inline the Presentation Thread cannot present before the main
thread has handed the frame over; with the order thread and `HOS_PRESENT_NOWAIT` the present job is queued before the frame's last
work and its signal, and when the game is slow (a zone's first frames) the present forced the frame out unfinished.

*Builds* (`build/s5/bin/<name>`; tours = 4-warp tour, `build/s5/pipe4/tour.sh`, runs `build/s5/<tag>`):

| build | engine change on top of the previous | tours (on) | result |
|---|---|---|---|
| pipe4a | log of each wait gone past and of a present's let-go (`02cf81c1e`) | p4t1 | instrumentation only |
| pipe4b | the time watch (`sync_watch`) does not force while jobs are queued (`fbd417c9f`) | p4t2, p4t3 | 4 and 9 flashes (off 0): not the cause |
| pipe4c | **the present is held until its signal and no stopped queue before it, the jobs behind it run meanwhile, the next present forces it, 200 ms** (`7ed2ea569`) | p4t4, p4t5, p4t6, p4t7 | no dark frame; 3 small flashes in 2 tours that reached the later zones (72-160 cells) at presents let go after 200 ms |
| pipe4d | + a read as submitted takes the capture's copy of a CPU-stale page (`0550f3d22`) | p4t8, p4t9 | 5 and 1 flashes; reverted (`1bd22639f`) |
| pipe4e | + the held present waits 2 s and the next present waits behind it (`e42aa06b0`) | p4t10, p4t11 | 0 flashes but **froze**: 6 frames after play (Market), presents stopped 81 s in (Zora's River) |
| pipe4f | the next present forces the held one again, the 2 s limit kept (`714f7dad7`) | p4t12, p4t13 | p4t12: whole route, **0 flashes, 0 presents forced**, meter 11/12/0/0; p4t13: **hung at the load** (play never began; the watch spared 4,000 forces) |
| pipe4g | the watch spares only while an order-thread job finished in the last 250 ms (`0c9273591`) | not run | built 14:31, tests 503 passed + the known failures |

Tests: pipe4f 505 passed, 2 failed (`test_nup_paths`, `test_nup_read_name`); pipe4g 503 passed, 4 failed: the two above, `test_renderer_new_handler` (known, local) and `test_switches_documented` (the switch lists were not regenerated for `0c9273591`'s comment; regenerated in the handover commit, `--check` passes).

*Best build to measure:* **pipe4g** for function (it carries pipe4f's present rule and the fix for pipe4f's load hang, not yet run in
the game); **pipe4c** is the last build with a picture series without hangs (no dark frame; small let-go flashes). For fps, pipe4c
and pipe4f/g differ only when a present is held (rare in steady play), so pipe4c's numbers stand: main PC gate (cut short) castle
+2.3% (medians 126.6 -> 129.5, every on run above every off run), Kokiri +6.5% (one round), meter 0 held reads on.

*Next, in order, and why:* (1) two tours on pipe4g (on) and one off: pipe4g has never run; the question is whether the load hang is
gone and the 0 flashes of `p4t12-on` hold. (2) If a hang remains, it is a job of the order thread waiting for a stopped queue's
work: find which (a dump of the order thread's stack while hung, or log `ot_run`'s job kind and value on entry/exit for the last 50
jobs) and make that wait force the stopped queue itself (as `wait_queue_empty` does) rather than relying on the watch. (3) The
2 s present hold: measure how often it is taken (`the present after N forced (waited its time ...)`); if it stalls zone loads,
bring it down (500 ms) - 200 ms let small flashes through. (4) The late-read class (section 9) did not explain any flash seen here.

*Open risks:* the 2 s held present can turn a slow zone frame into a 2 s hitch; `ot_seq_done` / `ot_value_done` move past a held
present (jobs behind it finish first), so a join on "every job handed over" can return before that present is shown; pipe4b's
watch rule (spare while jobs are queued) is what hung pipe4f - pipe4g limits it, untested. The tours on this machine often started
slowly (play 130-147 s after start under load); `tour.sh` now runs 300 s (`RUNS`, `LIMS`).

PIPE4 log (lease name `pipe4`, scratch `E:\OOT\scratch-pipe4`, scripts `build/s5/pipe4/{build,tour,gate}.sh`, launches through
`E:\OOT\scratch-pipe4\tmp\launch.py`; tables `E:\OOT\scratch-pipe4\tmp\gate.py`):
- Step 1 done: origin/main merged (`179d7e41c`; EXPO1, WBL1). PIPE2's meter fix `0bb228966` is kept: EXPO1's take-over covers a
  range the ring does not serve (the zone change), PIPE2's covers a served meter block read with no new entry at lag 3 (the order
  thread) - different paths in `rbring_apply`, no overlap; the rbring line carries both counts.
- Step 2 done: the present thread removed (`cbec76010`, reverts `f109058c2`).
- Step 3 lead: the two dark frames of PIPE3's tour (`build/s5/p3t-on`, presents 7268 and 7432) are the lit scene drawn black (sky,
  HUD and Navi right; `flash_7268_{a,b}.png`) - Longshot's "lit pass before its producer" picture, an ordering fault rather than a
  late read. The same tour's order stats: 294 waits gone past (19 forced by presents) on, 39 (2) off. Building `pipe4a` with a log
  of each wait gone past and of a present's signal let go, then one on tour (`p4t1-on`), to tie them to the flash presents.
- `p4t1-on` (pipe4a, slot b, 08:25; the machine loaded: 17.8 fps, the game ended at play + 75 s, Zora's River not reached, 0
  flashes): 31 waits gone past, **all by time** (`sync_watch`, hold 50 ms), none by a present, no signal let go; at present 5864 eleven
  waits of one lighting submission with events 19 values short (`0x100d6c80xx == 13046, has 13027`) - the same events and the same
  19-value gap as PIPE3's lines right after its two dark frames. Cause: `sync_watch` forces a queue stopped 50 ms past its wait
  without knowing that the work setting the event may be among the submissions handed over to the order thread and not replayed yet.
  Fix `pipe4b`: the watch does not force while the order thread has jobs queued (`RX_VK_ORDER_WATCH_PENDING=1`: as before). Built
  with the tests, then tours on / off / on (`p4t2-on`, `p4t2-off`, `p4t3-on`): `E:\OOT\scratch-pipe4\tmp\s2.log`.
- pipe4b (tests 504 passed / the 3 known failures): **4 and 9 flashes on, 0 off** (`p4t2-on`, `p4t3-on`, `p4t2-off`, slot a
  09:36-09:46, all reached Zora's River). The watch fix was not it (8 forces spared in one run). What ties: every flash sits at a
  present that forced 14 waits (`wait gone past (a present, ...)`, events `0x100d6c80xx` 19 values short) and/or let go of the CPU's
  signal after 200 ms: flashes 6358/6362/6520/6523 against forced presents 6357-6363 and 6512-6522 and let-go 6361/6365/6523
  (`p4t2-on`); flashes 5712-5716 and 5936-5940 against let-go 5715/5939 and forced 5933-5942 (`p4t3-on`); the off arm 0 forced by a
  present. **The dark frames are the order thread's present forcing the lit passes past their waits** while the work that sets
  their events was still behind the present in the job queue (the game slow to hand the frame over: the zone's loading frames).
- Fix `7ed2ea569` (pipe4c): the present is held, up to `RX_VK_ORDER_SIGNAL_MS` (200), until the signal has come and no queue is
  stopped before it, the order thread running the jobs behind it meanwhile (up to the next present, which forces the held one);
  the time watch does not force while one is held. Tours on / off / on: `p4t4-on`, `p4t4-off`, `p4t5-on` (`s3.log`).
- `p4t4-on` (pipe4c, slot a 10:28-10:33; the series was cut after it: the owner needed slot a, the lease released at 10:33:35):
  reached Zora's River; **no dark frame**; 3 presents forced in the whole tour (presents 128, 1440, 3297, each "waited its time"),
  against 9-10 forced clusters on pipe4b; one small flash at 3633 (72 cells, peak 70: Link and a ground patch darker on Zora's
  River's first frames, `E:\OOT\scratch-pipe4\shots\p4t4-on\flash_3633_{a,b}.png`) on a loaded machine (39 ms frames, the order thread
  89.7 jobs queued on average, 417 at most; slot b's game running and taking the focus) - the late-read class (section 9), not the
  ordering fault. Two more on tours queued: `p4t5-on`, `p4t6-on` (`s4.log`).
- `p4t5-on` (slot a 10:37-10:42, cut after it again for the owner's second window, released 10:42:51): 0 flashes, 1 present forced,
  but the game ended before the fourth warp (Market, Field, Kakariko only; as `p4t1-on`, cause not found, rc 0). The order thread's
  backlog on these tours (20-125 jobs on average, up to 891) is the same on pipe3b's `p3t-on` (22-176, up to 1336): not this fix's.
- `p4t6-on`, `p4t7-on` (pipe4c, slot b 11:20-11:29): the game reached play 143 s after its start (a loaded machine), so a 200 s
  run ended before the warps (Market only / Market and Kakariko); tours now run 300 s (`RUNS`, `LIMS` in `tour.sh`). `p4t6-on` had
  one flash with no forced present: 1669, 160 cells, peak 84, the Market gate's shade and ground darker for a frame
  (`shots\p4t6-on\flash_1669_{a,b}.png`) - the late-read class.
- Fix for that class `0550f3d22` (pipe4d): with the order thread, `memory_collect_written` at the replay stamps pages the game wrote
  after the submit, so a read as submitted found the capture entry stale and took guest memory (the next frame's). For a page made
  stale by a CPU write the capture's copy is now taken (the write came after the submission); a page whose newest stamp is a GPU
  write-back still reads guest memory (a GPU stamp generation per page and region, kept with the order thread only).
  `RX_VK_ORDER_READ_CAPTURE=0`: as before. Texture storage (`memory_as_submitted`) is unchanged.
- pipe4d (tests: the 3 known failures plus `test_capture_read_mapped.py`, whose stubs lack the new names): tours `p4t8-on` 5
  flashes (Market 1466/1467/1468/1479, Zora's River 3206), `p4t9-on` 1 (1676; the game ended in the Market), `p4t9-off` 0 (all five
  maps). The late-read change served 9,588 and 1 pages and the flashes stayed: **reverted** (`1bd22639f`).
- What the remaining flashes have: no wait forced (no `wait gone past` line in `p4t6-on` / `p4t8-on`), but a present let go after
  200 ms without the CPU's signal (`p4t8-on`: forced 1483 and 3210, right after the flashes): the frame shown without the main
  thread's writes up to its signal. Fix `e42aa06b0` (pipe4e): the held present waits up to 2 s (`RX_VK_ORDER_SIGNAL_MS`, was 200)
  and the next present waits behind it instead of forcing it - the Presentation Thread held at `RX_VK_ORDER_AHEAD`, as inline.
- The main PC gate on pipe4c (speed lease 12:28-12:49, slot c; `python -I E:/OOT/scratch-pipe4/tmp/gate.py p4g`, runs
  `build/s5/p4g-*`), **cut after Kokiri's second off run**: the coordinator moved fps A/Bs to the PC 3050 (owner's decision) and
  four slot requests waited behind the lease. castle-move off 127.1 / 123.8 / 126.6 (median 126.6), on 129.5 / 127.8 / 131.7
  (129.5, +2.3%), every on run above every off run; p50 7.99-8.24 off, 7.73-7.97 on; frames over 25 ms 1/0/0 off, 0/2/0 on.
  kokiri-idle off 116.5 / 116.0, on 123.9 (one round: +6.5%); p50 8.52-8.57 off, 8.03 on. Market not run. The meter (exposure sums
  given the last result again): castle 1 of 8000 a run off, 0 on; Kokiri 2 of 16000 off, 0 on.
- pipe4e built (12:21-12:28, tests 504 passed / the 3 known failures). Tours `p4t10-on`, `p4t11-on` (slot a 13:09-13:21): 0
  flashes, but **the game froze**: 6 frames after play in `p4t10-on`, presents stopped 81 s after play in `p4t11-on` (Zora's River).
  With the next present waiting behind the held one, the jobs behind it never ran while the game's threads joined them before the
  main thread's signal. Fix `714f7dad7` (pipe4f): the next present forces the held one again (as pipe4c); a held present with
  nothing behind it waits up to 2 s. Build and tours `p4t12-on`, `p4t13-on` queued (`s9.sh`, `s9.log`).
- pipe4f (tests 505 passed, 2 failed: only `test_nup_paths`, `test_nup_read_name`): `p4t12-on` ran its whole 223 s after play,
  Market, Field, Zora's River, Field, **0 flashes, 0 presents forced**, meter windows 11 / 12 / 0 / 0. `p4t13-on` **hung at the load**
  (presents stopped 121 s after start, play never began) with 4,000 forces spared by the watch: an order-thread job waited for the
  stopped queue's own work and the watch, seeing jobs queued, never forced it (pipe4e's freezes may be the same). Fix `0c9273591`
  (pipe4g): the watch spares only while an order-thread job has finished within the last 250 ms. Build and tours `p4t14-on`,
  `p4t15-on` queued (`s10.sh`, `s10.log`).

Where it stands: the three tasks of the brief are done and measured. (1) The present thread works and is safe, but **the gate that
keeps the GPU's order takes the present's serial part back into the chain** (Kokiri 0.47-0.51 ms a frame, chain 7.76 against 7.79):
it does not shorten the frame. (2) The Market's extra frames over 25 ms are the game's own loaders, not the order thread (one run
each arm, section 10a). (3) The flashes are **not** fixed: 2 on the on arm, 0 off; the reads that can be them are counted now
(section 9) and what a fix needs is stage 2's view per submission. Committed on `perf/pipe2`: `f109058c2` (the present thread),
`e5cb7eca6` (the read counters and the re-upload rule); the notes and `docs/SWITCHES.md` follow. Nothing of this stream is running
or leased now (`machine.sh status`: another stream's lease). Builds: `build/s5/bin/pipe3b` (the measured one; the notes' counters'
split of the own-capture-empty case is in the tree, not in `pipe3b`). Scripts: `build/s5/pipe3/{build,func,tour}.sh`,
`E:/OOT/scratch-pipe3/tmp/{s1,smoke,tour1}.sh`, `tab.py` (the series' table), `launch.py`.

PIPE3 (this session, lease name `pipe3`, worktree `E:\OOT\HeyListen-wt\pipe2`, branch `perf/pipe2`): (1) the present out of the
order thread's chain (`RX_VK_ORDER_PRESENT_THREAD`, default 1 with `RX_VK_ORDER_THREAD`; section 8); (2) the check of the reads
that run a frame late (section 9): `memory_as_submitted` (texture storage) and `memory_read_as_submitted` (the replay's other
reads) count the pages whose capture copy is older than a write since the submission, and a re-upload takes the page's newest copy
up to its submission's own instead of guest memory (the order thread only).

Built `pipe3b` (01:12, 744 s, tests 502 passed / the 3 known failures, the switches doc regenerated after). Smoke (slot b, 01:18):
Kokiri and the Market, order thread on, 0 flashes, no hang. The present thread's lines: the Market (game-bound windows) 0.71-1.12
ms a present (the CPU's signal wait 0.38-0.76, which is `submit_order_mu` against the PT's captures; recorded and shown 0.29-0.32),
the order thread waiting for a present's blit 60-94 times, 0.07-0.11 ms a frame. In the title/menu windows (2.8 ms frames) the gate
costs more (0.14-0.30 ms a frame): the next frame's submits do reach the order thread before the present is done there.
The late-read counters, first numbers: `replay reads ... of them N took guest memory for a page written after its capture's copy`
192,000 of 5.5 M reads in Kokiri (3.5%), 5,632 of 140,000 in the Market (4%); `texture read outside its submission's capture`
fires with the submission's own capture 0 (a capture that took no pages) and a later capture's copy in the shadow, i.e. the game
wrote the texture's storage after the submit - the console's GPU reads those bytes, so which way a flash goes is not settled by the
count alone (section 9, the tour's correlation is what settles it).

Next (for the coordinator, in order): the fixes the measurements point at — (a) the present's serial part is still the chain in the
replay-bound scenes, so the only way past it is the present's blit on a queue of its own with the semaphores that keep the next
frame's draws off the presented image (section 10, the last paragraph); (b) the flashes need stage 2's view per submission (section
9); (c) the meter's first reads after a zone change (PIPE2 section 6); (d) a valid PROF_FINE pair on the on arm; (e) a castle
series on a machine that is quiet (PIPE3's was not, section 10).

Where PIPE2 left it: **the gate is measured and does not pass** (the Summary; section 5 for the fps, section 6 for the tour, section 7 for the
knobs). The gate series ran on `pipe2e` (`0bb228966`) in one clean speed lease (22:19-22:57); the tour in slot a at 23:22-23:29; the
Kokiri knobs in one clean speed lease at 23:40-00:02. Nothing of this stream is running or leased now; the machine is on another
stream's build. Gate table: `python -I E:/OOT/scratch-pipe2/tmp/gate.py "p2g-*"` and `"p2c-*"`; shots
`E:\OOT\scratch-pipe2\shots\p2t2-{off,on}`. Launches go through `E:\OOT\scratch-pipe2\tmp\launch.py` (a detached bash; the harness's
shell alternates between Git bash and PowerShell, so a plain `nohup ... &` from a call is not dependable - a doubled launch cost one
tour and one lease today).

Clean baseline, castle off (the series' warm-up `p2s1-warm-c`, 127.2 fps from play + 10 s): frames 8.37-8.79 ms, the PT waited
0.26 ms for the main thread's hand-over, the main thread 1.34-1.43 ms for the present (89-94% of frames over 0.5 ms): replay-bound, the
PT's chain 8.1-8.5 ms. **GPU time 7.6-7.7 ms a frame** in the played windows (1.7 at the title screen; not 5.5, that was a run with
another agent's build in it): the GPU is 89-91% busy, so the stage 1 gate's +8% (7.9 ms frames) is at or beyond the GPU's own work in
this scene - see section 3. The main thread's held submits are replayed on the `HOS_NVN_HOLD=2` flush thread (0.73-0.90 ms a flush),
not on the PT: stage 3's "held flush" is already off the PT in this build.

Losses today: the first series start (18:36-18:40) ran the warm-up and castle off-1 (the run came out at 66.9 fps, 36 frames over 50 ms:
another agent's build began during it, deleted) and then died on runguard exit 11; a second start got the lease at 18:51 and died on
exit 11 again (my own two tour games left running by a doubled launch: killed, both slot leases released, the slots' own exes restored
from `build/s5/pipe2/bak-{a,b}`); a first tour attempt (slot b, 19:31) never started: runguard exit 12, 1065 MB of RAM free with
zone3's game in slot a (a second game does not fit: the tour waits until the machine is free).

Built `build/s5/bin/pipe2e` (19:28, 571 s, tests 503 passed / the 3 known failures) from `0bb228966`: the tip plus the readback
ring's meter re-apply (`vk_rbring.cpp`: with the order thread the game reads and zeroes the meter's block, so a frame's second read
finds its own zero and no new entry; the ring now hands the last applied entry back - the same value the meter hold gave - for the
meter's block only). Committed as `0bb228966`, `fix(vk): the readback ring hands the meter's block its last result back when no entry
is new (order thread, lag 3)`. The gate series now runs on pipe2e (tags `p2g-*`).

Done before this session: stage 0 (`HL_SLACK`, on) and stage 1 (`RX_VK_ORDER_THREAD`, off) on perf/pipe2, tip 1758bb3b0 + the doc; tests
only the known environmental failures. Builds `build/s5/bin/pipe2a..pipe2d` (pipe2d = the tip's code). Function runs (slot, shared
machine, so not comparable): castle on 126.5 / off 119.9; Kokiri on 115.3 / off 118.0; order thread the bound, its replay 8.1-8.5 ms a
frame against the PT's inline chain 7.3; meter 308-496 of 2000 reads given the last result again (0 off).

Open this session:
- Uncommitted, untested: `vk_rbring.cpp`, `rbring_apply` hands the meter's block the last applied entry back when no entry is new
  (class R1, `size == 0x1040`, `memory_is_meter`): the game reads and zeroes the block, so at lag 3 a read with no new entry sees its
  own zero and the meter hold counts it. New stat `again` in the rbring log line. To build and test (unit tests unaffected).
- The castle's GPU time is 5.5 ms a frame (`p2s1-c-off-1/game_log.txt`) against 8.3 ms frames, so the GPU has ~2.8 ms of slack: a
  win is possible; the question is the CPU contention between the replay's recorders and the game's job threads (13 threads on 8 cores).


Done:
- Stage 0 (`HL_SLACK`, on) and stage 1 (`RX_VK_ORDER_THREAD`, off) on perf/pipe2, tip 1758bb3b0 + this doc; tests: only the known
  environmental failures (test_nup_paths, test_nup_read_name, test_renderer_new_handler). Builds `build/s5/bin/pipe2a..pipe2d`
  (pipe2d = the tip's code).
- Stage 0 slack, main PC, slot runs (machine shared): castle off: main's present wait 1.31-1.60 ms a frame (85-98% of frames over
  0.5 ms), PT's hand-over wait 0.22-0.65: replay-bound. Kokiri off: present wait 1.62-1.66 ms (96-98%), PT 0.23-0.25: replay-bound.
  Market: not run yet.
- Stage 1 works (no crash, 0 flashes in function runs without shots, the meter not frozen) and the overlap happens (the PT waits for
  the main thread, the main thread never for the present). pipe2c / pipe2d: the order thread is now the bound (idle ~0.1 ms a
  frame), its replay slower than the PT's inline chain (8.1-8.5 ms a frame); the meter hold gives 15-25% of reads the last result
  (0% off). No clean fps yet: slot runs only (castle on 126.5 / off 119.9; Kokiri on 115.3 / off 118.0, shared machine).

Next (exact): the speed series that was stopped before its lease was granted (castle + Kokiri, 3 interleaved rounds, then a
`RX_VK_PROF_FINE` pair), then `python -I build/s5/pipe2/fps.py <tags>`:

    cd /e/OOT/HeyListen && A=(); A+=("p2s1-warm-c|castle-move|hyrule-castle|-"); for i in 1 2 3; do A+=("p2s1-c-off-$i|castle-move|hyrule-castle|-" "p2s1-c-on-$i|castle-move|hyrule-castle|RX_VK_ORDER_THREAD=1"); done; A+=("p2s1-warm-k|kokiri-idle|kokiri-forest|-"); for i in 1 2 3; do A+=("p2s1-k-off-$i|kokiri-idle|kokiri-forest|-" "p2s1-k-on-$i|kokiri-idle|kokiri-forest|RX_VK_ORDER_THREAD=1"); done; A+=("p2s1-pc-off|castle-move|hyrule-castle|RX_VK_PROF_FINE=1" "p2s1-pc-on|castle-move|hyrule-castle|RX_VK_ORDER_THREAD=1 RX_VK_PROF_FINE=1"); bash build/s5/pipe2/speed.sh s1 pipe2d "${A[@]}"

Then the Market (same form, save market-mid, scene market-spin), the tour (`bash build/s5/pipe2/tour.sh pipe2d "p2t-off|-"
"p2t-on|RX_VK_ORDER_THREAD=1"`: flashes, meter, maps), and if the gate passes (castle and Kokiri +8%, Market within the spread, 0
flashes, meter not frozen, pictures right) the PC 3050 list in `build/s5/pipe2/`. Slot leases at priority 1, speed at 2 (coordinator).

Open risks / leads:
- Why the replay is slower on the order thread: one slot profile (shared GPU, not trusted) showed vkQueueSubmit at 10 ms a frame
  against 2.5 off and the parallel segments 4x slower: check under a speed lease (the PROF_FINE pair above); suspects the GPU queue
  running ahead (no present pacing on the CPU side), contention with the PT's captures, the 64-72 jobs queued (registrations).
- The meter's held reads (15-25%): the ring at lag 3 or the readback join's narrowing; watch the picture's exposure in the tour.
- Reads outside the capture view (stage 2) not covered: texture uploads and collected writes read guest memory at replay time.
- `RX_VK_ORDER_RB_JOIN=1` (a readback waits for its own thread's submissions only) follows the inline replay's semantics; =2 is the
  conservative arm if pictures go wrong.
- Scripts: `build/s5/pipe2/build.sh <name> [tests]`, `func.sh`, `tour.sh`, `speed.sh`, `fps.py`.

## Stage 0: the stages' slack (`HL_SLACK`, on; =0 off)

`host/hos_slack.c`: the PT's blocking receive at lr 0x834a3084 on the presenting thread (the game's message-queue receive: the same lr
serves the module workers' and BaseProcExecutors' queues, so only the presenting thread counts) and the main thread's event wait at lr
0x81207a6c (the present wait, signalled by the PT), both timed from their start where they block (`nn_os_sync.c` event_wait,
queue_get). Every 600 presents: `slack (600 frames, X ms a frame): the PT waited A ms a frame for the main thread's hand-over, the main
thread B ms a frame for the present (P% of frames over 0.5 ms): replay-bound | game-bound | balanced`. `HL_SLACK_LRS=<pt>,<main>`
(hex) for other callers. Cost: two clock reads on waits that block anyway.

Gate for the later stages: present wait > 0.5 ms a frame.

## Stage 1: the order thread (`RX_VK_ORDER_THREAD=1`, off by default)

What it does (`vk_replay.cpp`, block after `cpu_wait_open`):
- `submit_q` (every submitting thread: the PT, and the main thread's held submits the PT flushes) takes the capture under
  `submit_order_mu`, gives the submission its value (`q_values`, so `rx_nvn_pushed` / `fence()` count handed-over submissions; the
  recording copies' lifetime follows `rx_nvn_replayed`, the GPU's), notes its waits (with the submission's value), tags it for the
  readback ring (`rb_note_value`) and pushes a job; it returns at once. The order thread runs the job as `submit_q` ran it inline:
  `sync_order_mu`, `q_last` / `value_queue`, `submit_job` (qruns, bursts), `update_blocked`.
- `present` hands a present job over (at most `RX_VK_ORDER_AHEAD`=2 not yet shown); the job waits (bounded, `RX_VK_ORDER_SIGNAL_MS`
  200) for the CPU's signal its own frame's stopped work waits for (FIF's rule, now limited to submissions before the present),
  drains the stopped queues and presents.
- Registrations (texture, sampler) and releases (texture, program) are jobs (their sync bodies run on the order thread).
- Joins (wait for the order thread's progress, not on the order thread itself): a fence wait waits for its submission's job
  (`ot_wait_for`, with the game's timeout; a poll is told "not yet": nothing of it is on the GPU; `RX_VK_ORDER_POLL_JOIN=1`: polls
  wait too); `wait_queue_empty` (nvnQueueFinish, texel reads and writes, the vmap and invalidate catch-ups) and every CPU readback the
  ring does not serve (`invalidate`) wait for all jobs handed over; the host's event polls (`nvnEventGetValue`) too, through the
  `mkwvk_order_join` export (`HOS_ORDER_EVENT_JOIN=0`: not), the core given up only when jobs are pending. `fence_q` answers from
  the handed-over submissions (`ot_q_last`) without `sync_order_mu`.
- Switches on with it: the CPU's signals in the capture sequence (`signal_capture_on`), the readback ring's frame clock
  (`rb_fif_on`) and lag 3 (`vk_rbring.cpp`, unless `HL_RB_LAG` says), the host's `HOS_PRESENT_NOWAIT` (the PT's present poll at lr
  805904a0 / 80590530 answered at once).
- `resume_queue`'s capture of a CPU-signalled wait (`memory_capture_now`) takes `submit_order_mu`: the submitting threads capture
  meanwhile, and the copy helpers serve one capture at a time.
- Log every 600 presents: `vk: order thread (...)`: its idle time, its queue's depth, the present's signal waits, and each join site's
  waited / asked counts and ms a frame.

## Attempts and measures

### 1. pipe2a, function runs (slot a, 23 Nov 15:12, machine shared: fps not comparable)

| run | fps | stage 0 slack (each 600-frame window) | order thread |
|---|---:|---|---|
| castle, off | 119.9 | PT waits 0.22-0.65 ms, main's present wait 1.31-1.60 ms a frame (85-98% of frames > 0.5 ms): replay-bound | - |
| castle, on | 121.1 | PT waits 7.6-8.1 ms, main 0.00: game-bound | idle 2.8 ms a frame; one readback a frame joined, 2.2-2.3 ms a frame |
| Kokiri, on | 73.4 | PT 11.0 ms, main 0.00 | idle 4.2-4.3 ms; one readback a frame joined, 3.7-3.8 ms a frame |

No crash, 0 flashes (the detector was off in these runs: no shots), the meter not held (0 of 2000). The overlap works (the PT waits for
the main thread, the main thread never for the present; fence waits, polls, catch-ups and event polls never had to wait for the order
thread) but one CPU readback a frame not served by the ring joined *every* job handed over, so the main thread waited for the replay
of the frame before again (Kokiri fell to 73 fps). Inline, a reader only had its own thread's submits replayed; the other threads'
(the PT's) were being replayed beside it. Fixed in pipe2b: `RX_VK_ORDER_RB_JOIN=1` (default) waits for the reading thread's own jobs.

### 2. pipe2b and pipe2c, function runs (slot a)

pipe2b (own thread's jobs): the main thread's one readback a frame (0x115bcf9580 +0x1b580, `RX_VK_ORDER_LOG`) still waited 2 ms a frame
(once 56 ms): its last job was a texture registration, queued behind the PT's frame. Inline, registrations never waited for the replay.
Kokiri on 117.6 against off 118.0 fps. pipe2c counts only the thread's submissions: readbacks 0 waits; now the order thread is the bound:

| run | fps | order thread | joins |
|---|---:|---|---|
| Kokiri on | 115.3 | idle 0.11 ms a frame, 64 jobs queued on average (138 at most) | fence waits 495 / 600 frames, 0.79 ms a frame |
| castle on | 126.5 | idle 0.07 ms a frame, 72 jobs queued (154 at most) | fence waits 511, 0.67 ms a frame |

The meter: 308-496 of 2000 reads given the last result again (0 in the runs before; not frozen: frozen is >= 1990). The order thread's
work, 8.1-8.5 ms a frame, is more than the PT's inline chain (submits + present, 6.7-7.4 ms in PIPE1's traces): the replay got slower
on its own thread.

### 3. The GPU's own work per frame: what the gate's +8% meets (clean runs, 23 Nov 19:20-20:15)

The engine logs the GPU's busy time a frame (`vk: GPU time X ms a frame`, the sum of the recordings' timestamp spans, every 120
presents; the gaps between recordings and the uploads are separate). In the two scenes the gate uses, on this PC, a frame of the played
scene is 88-95% GPU-busy:

| run (one run each) | fps | frame, the played windows (ms) | GPU busy a frame (ms) | GPU of the frame |
|---|---:|---|---:|---:|
| castle off, `p2s1-warm-c` (pipe2d) | 127.2 | 8.37 / 8.56 / 8.55 / 8.79 / 8.49 | 7.68 / 7.62 / 7.88 / 7.82 / 7.64 | 89-92% |
| castle off, `p2g-warm-c` (pipe2e) | 126.4 | 7.84 / 8.12 / 8.56 / 8.73 / 8.76 | 6.44 / 7.78 / 7.86 / 7.90 / 7.92 | 82-96% |
| Kokiri off, `p2b-f-off-kok` (pipe2b, shared machine) | 118.0 | 8.29 / 8.36 / 8.16 / 8.29 / 8.63 | 7.30 / 7.28 / 7.20 / 7.36 / 7.34 | 85-89% |
| Kokiri on, `p2c-f-on-kok` (pipe2c, shared machine) | 115.3 | 8.68 / 8.03 / 7.99 / 8.07 / 8.14 | 7.24 / 7.28 / 7.26 / 7.24 / 7.24 | 83-91% |

(each line: the last five 600-present windows of the run, and the GPU line's five 120-present windows that fall in each.)

(Title screen, for scale: GPU 1.7 ms of 2.8 ms frames.) The GPU's work a frame is the same in both arms (it does not depend on where
the replay runs), and it varies with what the camera sees: in the castle's fast windows 5.4-6.7 ms of GPU work against 7.2-7.5 ms
frames, in its slow ones 7.6-8.1 against 8.4-8.8.

What it means for the gate: **the frame cannot be shorter than the GPU's work plus the present's ~0.2 ms.**

- **castle**: the off frame is 8.43 ms against 7.70 of GPU work: 0.73 ms of CPU above the GPU, of which the present needs ~0.2. So the
  best possible frame is ~7.9 ms (126.6 fps) and the off fps 127.6 is already there in the run's average: **the castle has at most
  ~+6.7% of room**, and the on arm takes +6.2% of it (section 5). The +8% the gate asks for needs a frame of 7.85 ms, which is below
  the GPU's own work in the scene's slow windows (7.6-7.7 + 0.2).
- **Kokiri**: the off frame is 8.24 against 7.20 of GPU work: 1.04 ms of CPU above the GPU, so a frame of ~7.4 ms (+11%) is the
  ceiling and +8% (7.58) needs 0.38 ms of overhead. The on arm gets to 0.70 (7.90) - +4.7%, i.e. half the room is still there.

So PIPE1's upper bounds (+10-16%, +16-21%, section 3 there) were read off the traces and did not include the GPU's share: **on this PC
the castle has ~6.7% of room and Kokiri ~11%**, not 10-21%, and of that the order thread as built (the present in its own chain) takes
+6.2% and +4.7%. PIPE1's traced castle window ran at 7.59 ms a frame, which today's GPU work
(7.6-8.0) would not allow: the scene moment (the camera turns move the GPU's work between 5.4 and 8.1 ms a frame within one run) or the
builds differ.

### 4. The first clean on/off pair (castle-move, pipe2e, 21:21-21:27: the lease was cut by another agent's game after three runs)

| run | fps (play + 10 s) | frame p50 / mean (ms) | >50 ms | slack: PT waits for main / main for present | GPU time | order thread |
|---|---:|---|---:|---|---:|---|
| `p2g-c-off-1` | 137.8 | 6.68 / 7.26 | 18 | 0.21 / 1.10 | 5.0 | - |
| `p2g-c-on-1` | 138.6 | 6.38 / 7.22 | 26 | 5.63 / 0.00 | 5.4 | idle 0.18, fence join 0.40 |
| `p2g-c-off-2` | 126.6 | 7.83 / 7.90 | 6 | 0.32 / 1.43 | 7.5 | - |
| `p2g-warm-c` (off) | 126.4 | 7.92 / 7.91 | 0 | 0.24 / 1.55 | 7.9 | - |

Three things:
- **The overlap works and its cost is small**: the on arm's frame median is 0.30 ms shorter than the off arm's (6.38 against 6.68) and
  the CPU work above the GPU's own time is halved (frame - GPU: on 0.66 ms, off 1.39 ms). The PT waits 5.63 ms for the main thread and
  the main thread never waits for the present; the order thread idles 0.18 ms; the fence join costs 0.40 ms.
- **The fps of the two arms is the same within the noise (+0.6%)** because the GPU's own work differs between the runs: the walk's
  camera turns move it from 5.0 to 7.9 ms a frame, and a run's window covers a different stretch of the walk (`c-off-1` and `c-on-1`
  saw 5.0-5.4 ms of GPU, `c-off-2` and the warm-up 7.5-7.9). The interleaved rounds are the only way to compare: one round is not
  enough, and the hitches (18-26 frames over 50 ms, max 300-450 ms: a load hitch inside the window) are in both arms.
- **The meter's fix holds**: `p2g-c-on-1`'s log has `exposure sums: 2000 reads, 0 of them given the last result again` in every window
  (the runs before the fix gave 308-496 of 2000 on the on arm, 0 off): `0bb228966` does what it says.

### 5. The gate series (pipe2e, one clean speed lease, 22:19-22:57, 19 runs, 3 interleaved rounds a scene, fps from play + 10 s)

| scene | round | off | on | on/off |
|---|---|---:|---:|---:|
| castle-move | 1 | 137.8 | 138.6 | +0.6% |
| | 2 | 126.6 | 135.5 | +7.0% |
| | 3 | 127.6 | 133.8 | +4.9% |
| | **median** | **127.6** | **135.5** | **+6.2%** (mean 130.7 -> 136.0, +4.1%) |
| kokiri-idle | 1 | 120.9 | 129.0 | +6.7% |
| | 2 | 123.1 | 126.9 | +3.1% |
| | 3 | 121.2 | 126.7 | +4.5% |
| | **median** | **121.2** | **126.9** | **+4.7%** (mean 121.7 -> 127.5, +4.8%) |
| market-spin | 1 | 101.3 | 101.6 | +0.3% |
| | 2 | 84.7 (34 frames over 50 ms) | 86.9 (21) | +2.6% |
| | 3 | 99.1 (10) | 87.5 (22) | -11.7% |
| | **median** | 99.1 | 87.5 | -11.7% (mean 95.0 -> 92.0, -3.2%) |

Warm-ups (off arm): castle 126.4, Kokiri 121.8, Market 103.2 fps. The PROF_FINE pair at the end does not count: `series.sh` passed
`RX_VK_PROF_FINE=1,RX_VK_ORDER_THREAD=1` as one `export`, so the "on" prof run had neither switch (its slack line is the inline arm's:
PT waits 0.27, main 1.65); it is a fourth off sample (127.9) and the comma is now turned into a space in `series.sh`.

The counters behind the numbers (medians of the last five 600-present windows, `python -I E:/OOT/scratch-pipe2/tmp/gate.py "p2g-*"`):

| | castle off | castle on | Kokiri off | Kokiri on | Market off | Market on |
|---|---:|---:|---:|---:|---:|---:|
| frame (ms) | 8.43 | 8.13 | 8.24 | 7.90 | 9.89 | 10.50 |
| PT waits for the main thread | 0.23 | 7.75 | 0.40 | 7.20 | 1.39 | 9.67 |
| main waits for the present | 1.52 | 0.00 | 1.62 | 0.00 | 0.39 | 0.00 |
| GPU time (ms) | 7.70 | 7.60 | 7.20 | 7.20 | 7.20 | 7.00 |
| frame - GPU (CPU above the GPU) | 0.73 | 0.53 | 1.04 | 0.70 | 2.69 | 3.50 |
| order thread idle (ms) | - | 0.01 | - | 0.16 | - | 2.30 |
| fence join (ms) | - | 0.82 | - | 0.80 | - | 0.15 |

**The gate does not pass: castle +6.2%, Kokiri +4.7% (the brief's +8%), the Market inside its spread in the one clean round (+0.3%,
the other two rounds hitched on both sides: the Market's frame p50 is 9.3-9.6 ms in every run while the mean is 9.8-11.8), the meter at
0 held reads.** What the overlap does is exactly what section 3 predicted: the CPU work above the GPU's own time falls by 27-33%
(castle 0.73 -> 0.53, Kokiri 1.04 -> 0.70) and the PT's wait moves from the present to the main thread (1.52 -> 0.00 present wait,
0.23 -> 7.75 hand-over wait). What is left above the GPU is 0.5-0.7 ms, the present (0.18-0.2) and the last submission's latency in
the frame. **The castle's +8% is not there at all**: its whole room is ~+6.7% (section 3), and the order thread takes +6.2% of it.
**Kokiri misses +8% by ~0.3 ms**: +8% needs 0.38 ms of CPU above the GPU and the arm has 0.70. The next lever for that 0.3 ms is the
present out of the replay's chain (a present thread of its own, FIF's, without the contention FIF met) or the replay's tail; behind
both sits the GPU's own work (94-95% of the frame in the castle's slow windows, 91-93% in Kokiri's).

The gate, condition by condition:

| the brief's gate | measured | verdict |
|---|---|---|
| castle-move +8% or more against off | +6.2% median (+4.1% mean; +0.6 / +7.0 / +4.9 by round) | **no** |
| Kokiri +8% or more against off | +4.7% median (+4.8% mean; +3.1 / +6.7 / +4.5) | **no** |
| Market not below the spread | the clean round +0.3%; the frame medians 9.28-9.57 (on) against 9.39-9.47 (off), inside 1%; the run means are hitch-dominated on both sides (frames over 25 ms: off 8 / 126 / 35, on 13 / 156 / 81) | yes |
| the meter not frozen, M near 0 | over a run: 0 of 2000 in every on run and every window, against 308-496 before `0bb228966` (off: 0-1); **over the warp tour: 395/681 on, and the off arm freezes at 2000** (section 6) | yes over a run, **no over the tour** |
| 0 flashes over a warp tour, pictures right | **3 flashes on the on arm** (2 real one-frame ground/texture differences at presents 6366/6374 in Zora's River, 1 warp transition frame at 6605), **0 off**; the pictures otherwise right (section 6) | **no** (the flashes) |

The overlap's own evidence in these runs: the PT waits 5.6-10.3 ms for the main thread's hand-over in every on run and the main thread
0.00 ms for the present (1.4-1.7 off); the order thread idles 0.01-0.18 ms a frame in the replay-bound scenes (it, not the GPU queue,
was the bound) and 1.9-2.9 ms in the game-bound Market; the fence join (the game's own `nvnSyncWait` on the PT's fence, answered when
the order thread reaches it) costs 0.40-0.86 ms a frame on the main thread's side.

The Market needs its caveat: the on arm has more frames over 25 ms in every round (13 / 156 / 81 against the off arm's 8 / 126 / 35)
and its round 3 is 11.7% below the off arm's (87.5 against 99.1). Its frame p50 is equal in all six runs (9.28-9.57 on, 9.39-9.47 off),
so the difference is in the tail: **the order thread adds stutters in the game-bound scene**. The candidates are the ring at lag 3 and
the fence join (0.15 ms a frame there); not diagnosed. "Not below the spread" is true of the p50 and of the clean round, not of the
tails.

### 6. The tour: flashes, the meter across zone changes, the pictures (pipe2e, slot a, 23:22-23:29, `tour.sh` with `RX_VK_FLASH=1 RX_VK_SHOT_EVERY=1`)

The warp tour (`E:/OOT/scratch-foot1/tour_live.sh`: Market save, `kokiri-idle` inputs, `!cheat 1001` HyruleField, `1002` Kakariko,
`1012` ZoraRiver, `1007` HyruleField at 15/45/75/105 s after play; one shot every present; the flash detector's own period, so the run
is slow and the readbacks are staggered). Both arms reached all five maps (`Market_Child HyruleField_Child KakarikoVillage_Child
ZoraRiver_Child HyruleField_Child`):

| arm | run rate (play + 10 s) | frames over 50 ms | flashes (`vk: flash at`) | the meter's windows (2000 reads each) | shots |
|---|---:|---:|---|---|---|
| off (`p2t2-off`) | 43.1 fps (mean 23.19 ms) | 133 (max 463 ms) | **0** | 13, 1164, **2000** | 26 |
| on (`p2t2-on`) | 37.4 fps (mean 26.72 ms) | 65 (max 8238 ms, a zone load) | **3** (presents 6366, 6374, 6605) | 17, 395, 681, 11 | 37 (3 flash triples + 3 dumps) |

- **Flashes: the on arm has 3, the off arm 0.** Presents 6366 and 6374 (Zora's River, 74 presents after its load) are small (75 and 69
  of 2304 cells, peak 67-70, all in cells 19-33 x 28-35, the ground below Link) and the middle frame's ground is missing the stone
  texture patch its two neighbours both have (`flash_6366_b.png` against `_a`/`_c`, identical to each other). Present 6605 is the
  HyruleField warp's own transition frame (1202 cells, the screen mostly black with the sky above - the same kind of frame the off arm
  has as a plain black load frame at present 3000). So the tour gives **2 real one-frame picture differences on the on arm** (a texture
  read a frame ahead: stage 2's exposure, the reads outside the capture view) and one transition frame the detector cannot tell from a
  flash.
- **The meter across the warps is bad in both arms, worse off**: the off arm's last window is **2000 of 2000 held (frozen)**, the on
  arm's are 395 and 681 (20-34%) and its last is 11. The freezes follow the zone changes (the new zone's blocks have nothing staged yet;
  a read then finds the game's own zero and the hold supplies the last result). This is not the order thread's doing - the off arm
  freezes hardest - but it means **the gate's "M near 0" is not met over a warp tour by either arm**; over a run with no zone change it
  is (the gate series' on runs: 0 of 2000, section 5).
- **The pictures are otherwise right**: at the same presents both arms show the same scene, textures, geometry and HUD
  (`vk_003000.png` is the black HyruleField load in both; `vk_003600.png` is the cave mouth in HyruleField in both). The on arm's frame
  is a little brighter - the meter's one extra frame of lag (the ring at lag 3 instead of 2), as EXPO1/STEP1 described. Shots:
  `E:\OOT\scratch-pipe2\shots\p2t2-{off,on}\`.

### 7. Kokiri's last 0.3 ms: the order thread's knobs (pipe2e, one clean speed lease 23:40-00:02, two interleaved rounds)

Kokiri is the one scene whose +8% is not walled off by the GPU (section 3/5: the GPU needs 7.1-7.2 ms, the on frame is 7.73-7.90), so the
order thread's chain got one more turn of knobs, all env-only on the same build:

| arm | rounds (fps) | median | against off | frame (ms) | GPU (ms) | frame - GPU |
|---|---|---:|---:|---:|---:|---:|
| off | 122.5, 120.0 | 121.2 | - | 8.17, 8.50 | 7.10, 7.10 | 1.07, 1.40 |
| on (base) | 129.0, 127.4 | 128.2 | **+5.7%** | 7.73, 7.80 | 7.10, 7.20 | 0.63, 0.60 |
| on, `RX_VK_ORDER_PRIO=2` | 129.8, 128.4 | 129.1 | +6.5% | 7.70, 7.82 | 7.10, 7.10 | 0.60, 0.72 |
| on, `RX_VK_POOL_SPIN=200` | 129.1, 128.3 | 128.7 | +6.2% | 7.77, 7.78 | 7.10, 7.10 | 0.67, 0.68 |
| on, `RX_VK_PARALLEL=4` | 129.5, 126.1 | 127.8 | +5.4% | 7.70, 7.89 | 7.10, 7.10 | 0.60, 0.79 |

**None of the knobs reaches +8%**: the thread's priority (+0.8%) and the pool's spin (+0.5%) are inside the rounds' spread (the base arm
itself spread 129.0 / 127.4), and fewer recorders (PARALLEL=4) is not better. The CPU above the GPU stays at 0.6-0.7 ms whatever the
knob: the order thread's chain is set by its jobs' serial part (the frame's last submit, the drain and the present), not by its
scheduling or its workers' wake. That is where the 0.3 ms Kokiri needs would have to come from: the present out of the chain (a
present thread of its own, FIF's, which contended for `sync_order_mu` when FIF tried it on the PT's side) or a shorter tail before the
present.

## PIPE3: the present out of the order thread's chain (24 Nov)

### 8. The design

`RX_VK_ORDER_PRESENT_THREAD` (default 1 with `RX_VK_ORDER_THREAD`; =0 the PIPE2 arm, bit for bit): the present's job is run by a
thread of its own (`mkw_vk present thread`).

- The order thread hands the present over as it reaches it in its queue (so every submission handed over before it is replayed:
  the present's `drain` and its recording see the whole frame) and goes on with the next job.
- What a GPU submission's order needs is kept by `ot_present_join()`: any job that may put work on the queue (a submit, a release,
  an event signal) waits, before it starts, until no present handed over before it is unfinished. The registrations (table copies)
  do not wait: they are what the present runs beside. So nothing of the next frame reaches the GPU before the picture the present
  shows; the present's own `sync_order_mu` section (drain, `do_present`) is never contended by a submit in the usual frame - the
  next frame's submissions arrive a frame later (the PT waits 7 ms for the main thread's hand-over), when the present is long done.
  That is the difference from FIF's present thread, which held `sync_order_mu` against the PT's next inline submit.
- What the present thread does: the CPU's signal wait (FIF's rule, `RX_VK_ORDER_SIGNAL_MS`), then `sync_order_mu`: `drain(0)`,
  `update_blocked`, `present_value`, `ubo_check_frame`, `do_present` - exactly the body the order thread ran. The thread is at
  ABOVE_NORMAL (`RX_VK_ORDER_PRESENT_PRIO`) and not pinned to the replay's core (the order thread is).
- The present's globals (`rec_value_now`, `ub_first_of_recording`, `present_value`, `do_present`'s statics) are written by the present
  thread and by the order thread's recordings, never at the same time: the gate makes any GPU-submitting job wait for the present,
  and a registration does not record. The replay's other globals stay the order thread's alone.
- One line a 600 presents: `vk: the present's thread (600 presents, X ms a present): the CPU's signal A ms, waiting for the order
  thread's frame B, draining C, recorded and shown D; the order thread waited for a present's blit N times, M ms a frame`.

### 9. The flashes: the reads that run a frame late

The order thread's replay runs a frame or more after the submits it replays. Every read the replay makes through a capture is as of
that submission; the reads that fall back to guest memory are not, and a page the game wrote for the *next* frame gives the frame a
picture from the frame after it. Two paths, both counted now:

- `memory_as_submitted` (a texture's storage, on a first upload or a re-upload): a page whose newest copy in the shadow is older or
  newer than the submission's own capture was read from guest memory. With the order thread a re-upload now takes the newest copy
  *up to* its submission's own (a page written before that submit would have been in that capture; so that copy is the page as of
  the submit) and counts what it served (`st_earlier_used`); a page whose copy is a *later* capture's (the game wrote it after the
  submit and a capture took it since) is still read from guest memory - no copy of it as of the submit is kept - and is logged with
  its page and the two capture numbers (`vk: texture read outside its submission's capture`).
- `memory_read_as_submitted` (the replay's other reads: uniforms, bindings, draws' operands): a page whose capture entry was taken
  before a write since is skipped and guest memory read; the reads that did so are counted and logged with the existing
  `replay reads of guest memory as submitted` line.

Measurement: section 10.

### 10a. The Market and its extra frames over 25 ms (one run each arm, `HL_STALL_WATCH=50`, 03:46-03:48)

A speed lease at last (03:46, one round each arm, the stall watch on: 18 dumps off, 23 on):

| arm | fps | p50 (ms) | >25 ms | >50 ms | frame / GPU (ms) | PT waits / main waits | order thread | present thread | the gate |
|---|---:|---:|---:|---:|---|---|---|---|---|
| off | 101.4 | 9.49 | 5 | 2 | 9.81 / 7.20 | 1.19 / 0.52 | - | - | - |
| on | 107.7 | 9.02 | 4 | 3 | 9.34 / 7.20 | 8.51 / 0.00 | 9.34, idle 1.92 | 0.46 ms (signal 0.01, lock 0.01, recorded and shown 1.01) | 194 waits, 0.39 ms a frame |

**The extra frames over 25 ms are the game's own loaders, the same threads in both arms.** Every stall dump names them: the
`ARes_Loading_0..3` threads take 45-135 Mcycles inside the gap (15-47 ms of user CPU each), `GeneralFormatAsync_`,
`BaseProcCreateAndDeleteThread` and `game::cull::Module` run at 96-100% of a 458 ms window, and `sead::MainThread` sleeps 301 ms
of it (the game waiting on its own load). The order thread and the present thread are idle in those dumps (`host:mkw_vk_order_thread`
12.9 Mcycles, at ntdll: asleep; the present thread 1.1 Mcycles). The off arm's dumps name the same `ARes_Loading_0` (103 Mcycles).
**In this round the on arm has 4 frames over 25 ms against the off arm's 5** and a better p50 (9.02 against 9.49): PIPE2's
"the order thread adds stutters" (13/156/81 against 8/126/35) is not reproduced here - those rounds were hitched on both sides
(section 5). The dumps: `E:\OOT\scratch-pipe3\stall-{off,on}` (`python -I tools/bench/jobtrace.py <dump>`, e.g. stall_07.jt: 458 ms
window, 240,763 records).

The present thread in the Market: 0.46 ms a present (1.01 ms of recording in the last five windows), the gate 0.39 ms a frame in
194 of 600 frames - the game-bound scene's frames are 9.3 ms, so both fit inside the order thread's 1.9 ms of idle.

### 10. The measurements (pipe3b)

Build `pipe3b` (01:12, 744 s; tests 502 passed, the 3 known environmental failures). Arms: `-` (off), `RX_VK_ORDER_THREAD=1
RX_VK_ORDER_PRESENT_THREAD=0` (the PIPE2 chain), `RX_VK_ORDER_THREAD=1` (the present thread). `build/s5/ab_env.sh` (slot c, one
warm-up, interleaved rounds), fps from `play: began` + 10 s; the counters are the medians of the last five 600-present windows
(`python -I E:/OOT/scratch-pipe3/tmp/tab.py env-p3c 0 1 2`).

**The castle series cannot be used.** Nine runs, 01:24-01:47, one speed lease: every run had 39-215 frames over 25 ms and 39-115
over 50 ms (PIPE2's clean castle runs: 0-18 over 50 ms), the loads took 48-109 s against PIPE2's 40.4 s, and one run spent its
window in the load (play at 109 s, 5.8 fps). The machine was not fit (no other game or compiler: runguard passed every run); the
desktop's own load (Discord, Opera, Spotify, qBittorrent) is the likely cause. The numbers, for the record:

| arm | fps (rounds) | median | p50 (ms) | steady frame / GPU (ms) | present thread | the gate |
|---|---|---:|---|---|---|---|
| off | 82.5, 65.7, 120.7 | 82.5 | 9.06, 7.86, 6.72 | 10.63/6.20, 9.87/7.20, 6.30/5.10 | - | - |
| on, present in the chain | 116.7, 133.6 | 125.1 | 6.17, 7.53 | 6.08/4.60, 8.12/7.60 | - | - |
| on, present thread | 22.1, 128.5, 92.1 | 92.1 | 6.82, 6.44, 6.63 | 4.16/5.80, 6.67/5.00, 7.13/5.50 | 0.38-0.51 ms a present (signal 0.01-0.25, lock 0.00-0.53, recorded and shown 0.25-0.41) | 508-583 waits, 0.33-0.43 ms a frame |

What they do say: the present thread's own line is 0.4-0.5 ms a present (of which 0.25-0.41 is the recording), and the order
thread waits for a present's blit 508-583 times over 600 frames, 0.33-0.43 ms a frame - i.e. **in the castle the gate takes back
most of what the present's removal from the chain gives**: the next frame's submits do reach the order thread before the present
is done (the camera walk's frames are 6.5-7 ms and the present is 0.4 ms, but the PT hands frame N+1 over while N is presented).

The Kokiri and Market series and the tour follow (the machine's queue: another stream's 45-minute speed lease was granted at 01:59).

**Kokiri series (02:33-02:54, one speed lease, 2.5 rounds of 3)** — the same arms, same build. The machine was better than for the
castle (frames over 50 ms: off 2/6/15, the PIPE2 clean range) but one arm-1 run was lost to a load (play at 109 s, and `env-p3k-1-2`
has no play at all):

| arm | fps (rounds) | median | vs off | frame (ms) of the last five windows | GPU | frame - GPU | ot idle | present thread | the gate |
|---|---|---:|---:|---|---:|---:|---:|---|---|
| off | 105.1, 118.5, 112.1 | 112.1 | - | 8.28, 8.29, 8.48 | 7.10-7.20 | 1.08-1.28 | - | - | - |
| on, present in the chain | 128.1, 92.6 | 110.4 | -1.5% | 7.79, 7.86 | 7.10 | 0.69-0.76 | 0.05, 0.10 | - | - |
| on, present thread | 128.1, 115.2, 119.9 | 119.9 | +7.0% | 7.76, 8.98, 8.13 | 7.10 | 0.66, 1.88, 1.03 | 0.05, 1.27, 0.46 | 0.53-0.55 ms (signal 0.08-0.09, lock 0.00, recorded and shown 0.41-0.44) | 587-589 waits, 0.47-0.51 ms a frame |

**The present thread does not shorten the frame.** Arm 2's order thread is no longer the bound in 2 of 3 rounds (idle 0.46-1.27 ms:
the game's own loop is), and in the round where it still is the chain is 7.76 ms against arm 1's 7.79: the gate puts the present's
serial part (0.44 ms of recording) back into the chain, 0.47-0.51 ms a frame of waiting in 587 of 600 frames. That is exactly the
0.5 ms arm 1's present costs the chain; the difference between the arms' fps is the hitches (arm 2's 115.2/119.9 rounds carry 23/33
frames over 50 ms). Against the gate the present thread buys only what the present does *outside* `sync_order_mu` - the CPU's
signal wait (0.08 ms) and whatever `present_image` waits for - and nothing of the frame's chain.

Why the next frame's submits are there so early: the order thread's queue holds the frame's registrations (64-72 jobs) and the PT,
free of the replay, hands frame N+1's submits over while N is presented; the present's 0.4-0.5 ms is then the only thing between
the two frames' submissions, and `ot_present_join` preserves that with a wait rather than letting the next frame's work onto the
queue first. A design that does not wait would have to put the present's blit on a queue of its own (with the semaphores that keep
the next frame's draws off the presented image), which is the second-queue design PIPE1 left out.

**The tour (01:48-01:56, slot a, `build/s5/pipe3/tour.sh pipe3b`)**: the off arm 0 flashes (4 maps reached, the meter 13 / 1981),
the on arm **2 flashes** (5 maps): presents 7268 (`686 of 2304 cells, peak 225, cells 11,0-63,35`) and 7432 (`1606 of 2304, peak 225,
0,0-63,35`) - both whole-screen dark differences, not the small ground patches of PIPE2's tour; the meter 12 / 352 / 1171 / **2000**
(the last window frozen, as the off arm's was: the zone-change rule, unchanged). So the flashes are not fixed by the re-upload
change.

What the counters say about them: the on arm's tour had 45,568 texture pages whose shadow copy was not their submission's own (the
counter did not yet split "no capture of its own" from "a later capture's copy": that split is in the tree after `pipe3b`), 76,138
re-upload pages served from an *earlier* copy (the new rule), 15,837 re-upload pages still read from guest memory, and 357,929
first-upload pages read from guest memory by design (issue 66). The flashes are whole-screen (686 and 1606 of 2304 cells), which fits
one big texture read a frame late (the tour's log has 2368x2368 storage re-uploaded), and the reads that take guest memory (either
kind) are the only ones that can do it. **Not settled**: the counter that says "a later capture had taken the
page" was written before I noticed `own == 0` makes it trivially true; it is split now (own empty / a real later copy), and the next
tour will say which of the two classes carries the flash. A fix for either needs the per-submission view PIPE1's stage 2 describes:
the shadow keeps one copy per page, so a page written after the submit and re-taken since has no as-of-submit copy to read.

## Dead ends