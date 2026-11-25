# CRASH2: the ModuleSystemWorker destroy-path family of v0.4.3

Question: why 20 of 49 v0.4.3 crash reports (41 %; v0.4.2: 9 of 105) die in f_80a30a00, f_80a31030, f_803b44e0 or
f_834bdb90 on a ModuleSystemWorker thread (chain f_80a31030 <- f_80a31220 <- f_82c20a40 <- f_83b78d80). Background:
docs/research/crash1-triage-2026-11-24.md (branch docs/crash1). Only function names and offsets here, no game code.

## State (kept current)

- 2026-11-24 08:20: worktree E:\OOT\HeyListen-wt\crash2, branch fix/crash2 from origin/main 0e5db2102. History done
  (below). Build lease requested (queue behind four builds). Loop script ready (E:\OOT\scratch-crash2\loop.sh, outside
  the repo): age toggle (cheat 999), then warps 1010 DMC, 1023 Fire Temple, 1026 Spirit, 1020 Dodongo, 1011 Goron City
  and so on every GAP s.
- Run 1 (bin main8 = main of 2026-11-23, slot b, 19 min, HL_TRACE=2 HL_JOBGROUP_CHECK=1 HL_STAGES_CHECK=1):
  build/s5/c2a-on/game_log.txt: 29 map loads (age toggle by cheat 999, then warps 1010 DMC, 1023 Fire, 1026 Spirit,
  1020 Dodongo, 1011 Goron City, 1009 DMT), no crash. Invariants clean: takes/passed/runs not run 0, bad finishes 0,
  bad counts 0, double completes 0, double runs 0, heap order 0, group ends checked 518098 with 0 faults, take count
  below 0: 0; stages: registrations compared 275625, differ 0; observation: lane drift 173. INVALID for the Crater
  (and Fire Temple) as a crash test: Link is adult without the Goron Tunic, so the heat kills him (owner saw it); these
  loads happen but are not the reports' path. Spirit Temple and Dodongo's Cavern loads are valid.
- Run 2 arm "c2a-off" (HL_JOBGROUP=0 HL_MODSTAGES=0): INVALID, the boot never reached play ("skip title: gave up
  after 120 s"): a helper of mine pressed A into the live-input file before play began. Redone as c2b-*.
- Run 3, arm c2b-off (HL_TRACE=2 HL_JOBGROUP=0 HL_MODSTAGES=0, the guest's own job group and stages, bin main8, slot a):
  REPRODUCED after 17 map loads (about 7 min of warps), on the map DodongoCavern (adult; a valid map). Log copy:
  E:\OOT\scratch-crash2\c2b-off-game_log.txt, recorder text E:\OOT\scratch-crash2\hl_trace_crash.txt. Fault: read at
  0x100000000000 in f_80a30a00+0x30a on ModuleSystemWorker4, chain f_80a31030+0x22e, f_80a31220+0xe09, f_82c20a40+0x963,
  f_8007fe60 (the translated group, so the natives were NOT involved: the frames show the guest's group, not hl::). So the
  family exists with HL_JOBGROUP=0 HL_MODSTAGES=0; the natives are not its cause (one sample, but the first arm tried).
  (The natives-on arm c2b-on was stopped by the coordinator's order at 13 maps, no crash yet: no conclusion from it.)
- What the recorder says (hl_trace_crash.txt): the objects the destroy path walks lie in the guest heap 0x11787ea8d8
  (size 0xf800000, inside the NVN pool the back end mirrors: "mirror made ... [0x1178000000, 0x117f000000)"). Those same
  pages, [0x117e70d000,+0x40) and [0x117e70e000,+0x40), were written back by the back end (GPU writes copied into guest memory,
  "write-back by the back end", every 250 to 760 ms up to 1.4 s before the crash, submission 218410) while guest objects
  allocated by SceneLowPriority 22 s earlier lived there (x24 = block 0x117e70dff8 size 0x28 straddles 0x117e70e000;
  the neighbours 0x117e70dce8 size 0x70). The same addresses held 0x90 and 0x40 blocks freed by ModuleSystemWorker2 22.7 s
  before. So a GPU write-back lands on the game's live heap objects (their chunk headers / fields): the destroy path then
  reads a bad pointer. No double free is visible in the recorder: the blocks the crashing thread frees just before are
  distinct, not freed before.
- Hypothesis (to prove): the back end writes GPU words back to guest addresses the game has already given to its heap
  (a GPU buffer freed or re-used while a recorded binding still lists it as written, or a write-back of the lazy kind
  with a stale mirror). v0.4.3 changed this path (bccc744c2: pools under 4096 KB always written back and a CPU write makes the
  pool eager, RX_VK_WB_SMALL_EAGER_KB, RX_VK_WB_POOL_EAGER; f0f7ae922: the GPU's writes visible to the host at each
  submission's end; 62b2cb694 / 539d20ad8 retired and idle imports), which fits the rise from 9 % to 41 % and "NVIDIA only".
  Test that would prove it: the same warp loop (loop.sh, cheats 999 then 1010/1023/1026/1020/1011) with
  RX_VK_WB_POOL_EAGER=0 RX_VK_WB_SMALL_EAGER_KB=0 (the v0.4.2 behaviour) against the default, HL_TRACE=2, many more
  loads (the first crash came after 17); and in the recorder a check at each write-back that the range holds no live
  game heap block (hl_trace has the live-blocks table: log "write-back into live block" with the block's allocator).
- Not done: a build of the worktree (mkg.ps1 failed when its build ticket was re-queued), the natives-on arm to a crash,
  the lifted-code reading beyond the call chain, the fix. The Crater runs (adult without the Goron Tunic burns) do not
  count; no Crater-valid run was made. Leases: all released, game window closed.

## CRASH3 (takes over from CRASH2, 2026-11-24)

### Summary (final, 14:05)

- Proven with the new watch (HL_TRACE=2 HL_TRACE_WB=1): on main (c3-base, RX_VK_WB_GUESS_CHECKED=0) the back end writes the
  64-byte head of a bare storage-table entry back into guest memory after the game has freed that GPU buffer and given the
  memory to CPU objects: block 0x117e709000 size 0xb000 freed by ModuleSystemWorker1 in the destroy path, a 0x90 block
  allocated over it by SceneLowPriority, then "write-back INTO A LIVE HEAP BLOCK" every submission; 15439 such write-backs
  in 480 s, four heads (0x117e709000, 0x117e70b000, 0x117e70c000, 0x117e70d000; 0x117e70d000 is one of CRASH2's crash
  addresses). Origin: compute programs a54f19e927d8faef and b612216258832b9c, descriptor c4+0x60, entry 2 of 16, size 0.
- Root cause: gpu/vk/vk_draw.cpp, the bare entry's head marked written (`gpu_buffer_bind(base, head, true)`, e900faa8bf) and
  copied whole mirror -> guest at each submission's end (vk_memory.cpp memory_end_submission), whether the GPU wrote it or
  not; with a stale entry the copy puts old bytes over live heap headers and fields. v0.4.3's bccc744c2
  (RX_VK_WB_POOL_EAGER) makes the 112 MB pool that holds the heap eager at the first CPU write, so all its heads are copied
  back since v0.4.3 (plausible reason for 9 % -> 41 %, not measured: see below).
- Fix: 19cb5cee2 + 38f72926d + 26ab8203a (RX_VK_WB_GUESS_CHECKED, default on): the head is staged; guest memory is read at
  the binding (snapshot); once the submission is done, memory_end_submission writes only the words the GPU changed and the
  CPU did not. Test tests/test_wb_guess.py (+ the cut-source fixtures updated, 0e895c494). c3-fix4 (720 s, 25 maps):
  0 write-backs into live heap blocks, no crash.
- Not settled: the v0.4.2 env arm (RX_VK_WB_POOL_EAGER=0 RX_VK_WB_SMALL_EAGER_KB=0) never reached play in 3 tries
  (c3-v42, c3-v42b, c3-v42d: "the game gave up waiting for the title"), so no env-only hotfix is shown; RX_VK_WB_LAZY=0
  was not run. No crash happened in any arm (base included), so "no crash with the fix" is not a crash-rate result.

### State (kept current)

- 11:25: worktree merged with main (28e394c2b). Scripts copied to E:\OOT\scratch-crash3. Detector written (not yet run):
  HL_TRACE_WB=1 with HL_TRACE=2 (host/hl_trace_core.c "the write-back watch"): the back end's write-backs of 16 KB or less
  kept as targets by page; a heap block (with its 16-byte header) allocated or freed over a target written back in the last
  5 s is told ("TRACE WB: ALLOCATED OVER / FREED UNDER A WRITE-BACK TARGET"), and each later write-back of that target while
  the block lives ("TRACE WB: write-back INTO A LIVE HEAP BLOCK"); counts every 30 s ("hl trace wb:"). The back end logs the
  origin of each written storage binding of 16 KB or less once ("vk: wb watch: written binding [lo, hi) (bare entry's head |
  sized) by program ... desc ... entry k of n: pointer, size word"). tests/hl_trace_test.c covers the watch.
- Lead from reading the code: the 64-byte write-backs at page-aligned addresses match vk_draw.cpp's "bare entry the program
  writes through: its first 64 bytes marked written" (a storage-buffer table entry with size word 0 in a program that writes
  through the table; e900faa8bf, 7 Nov, so in v0.4.2 too): the whole 64 bytes are copied from the mirror back to guest
  memory after each submission that binds it, whether the GPU wrote them or not.
- Built crash3x (watch only), then crash3f (watch + fix). Commits 180c853df (feat(trace): HL_TRACE_WB) and 19cb5cee2
  (fix(vk): the bare head's write-back staged and checked, RX_VK_WB_GUESS_CHECKED default 1, =0 as before). Build leases
  released.
- Why v0.4.3: bccc744c2 (RX_VK_WB_POOL_EAGER, RX_VK_WB_SMALL_EAGER_KB) makes a big pool eager at the first CPU write to any
  of its pages; the 112 MB pool that holds the game's heap is written by the CPU all the time, so its write-backs (heads
  included) are all copied back since v0.4.3, where v0.4.2 left those of pages the CPU had not written in the mirror.
  To be measured (A/B below).
- 11:40: queued slot lease 20261124-112813-crash3-c3ab-slot-8900 (behind a speed lease and fix1): loop3.sh on bin crash3f,
  480 s an arm, warps every 24 s (no Crater, no Fire Temple): c3-base (RX_VK_WB_GUESS_CHECKED=0), c3-fix (default),
  c3-v42 (GUESS_CHECKED=0 POOL_EAGER=0 SMALL_EAGER_KB=0), c3-nolazy (GUESS_CHECKED=0 RX_VK_WB_LAZY=0), all with
  HL_TRACE=2 HL_TRACE_WB=1. Output E:\OOT\scratch-crash3\c3ab.out, logs build/s5/c3-*/game_log.txt.
- 11:40: that lease's arms all refused by runguard (rc 12: 2239 MB RAM free, two other games running); no run made
  (c3ab-try1.out). loop3.sh now waits for 4500 MB free before each arm. Re-queued 11:45 with c3-base, c3-fix, c3-v42.
- run_all: 506 passed, 5 failed: the 3 known plus test_parallel_rb_refresh and test_msync_diff (they cut the source; fixed
  in 0e895c494, both pass now).
- 11:52 lease 20261124-114238-crash3-c3ab-slot-4854 (slot a). c3-base (RX_VK_WB_GUESS_CHECKED=0, i.e. as v0.4.3/main),
  build/s5/c3-base/game_log.txt, 7 maps by 11:56: HYPOTHESIS CONFIRMED. Lines 4062, 4071, 4220: the GPU buffer block
  0x117e709000 size 0xb000 (heap 0x11787ea8d8) is FREED by ModuleSystemWorker1 (by 0x8093f13c ... 0x80a30ae4: the destroy
  path) while [0x117e709000, +0x40) is written back every submission (610 times in 17.5 s); SceneLowPriority allocates
  block 0x117e708fd8 size 0x90 over it (by 0x80c5db4c 0x80cb2ff8 0x80c625ac: the same allocator chain as the objects of
  CRASH2's crash), and the back end keeps writing back the 64 bytes into that live block ("write-back INTO A LIVE HEAP
  BLOCK ... submission 31643", 4000+ times by line 7597). Origin (vk: wb watch lines): programs a54f19e927d8faef and
  b612216258832b9c, compute stage, storage descriptor c4+0x60, entry 2 of a 16-entry table, size word 0: a bare entry's
  head. The game keeps the freed buffer's address in that table entry; the program does not write through it (nothing
  on the console), but the back end copies the mirror's 64 bytes back each time.
- c3-base done 12:00 (rc 0, 16 maps, no crash): last count line "15439 write-backs into live heap blocks, 10 blocks
  allocated over and 15 freed under a target written back in the last 5 s; 14274 targets seen, 15 of them hit"; the
  live-block lines name four heads: 0x117e709000, 0x117e70b000, 0x117e70c000, 0x117e70d000 (+0x40 each; 0x117e70d000 is one
  of CRASH2's crash addresses). c3-fix running.
- c3-fix (bin crash3f, the first form of the fix: a head left out whenever the CPU wrote its page since the mirror's
  sync) done 12:09: rc 0, 16 maps, no crash; "hl trace wb: 0 write-backs into live heap blocks"; but "400196 runs staged;
  pieces: 0 written ... 400196 left out (the CPU wrote the page since the mirror's sync)": no head ever reached guest memory
  (the GPU's counts there, if any, stayed in the mirror). Second form (uncommitted at 12:10): a snapshot of guest memory at
  the binding; only the words the GPU changed and the CPU did not are written (wb_guess.h gpu_words). Building crash3g
  under build lease 20261124-120948-crash3-build-a556; c3-v42 running in slot a.
- 12:11 committed the second form (38f72926d), built crash3g. c3-v42 (bin crash3f with GUESS_CHECKED=0 POOL_EAGER=0
  SMALL_EAGER_KB=0) INVALID: never reached play (bench.json "never reached play", RAM low, cold pages held); slot a
  restored (md5 equal to the backup) and released 12:19. Queued: loop3b.sh (backup per lease: build/s5/crash3-bak-<tag>)
  c3g = c3-fix2 900 s on crash3g (ticket 20261124-121138-crash3-c3g-slot-e50f), c3v = c3-v42b 480 s on crash3g with
  GUESS_CHECKED=0 POOL_EAGER=0 SMALL_EAGER_KB=0. Outputs E:\OOT\scratch-crash3\c3g.out, c3v.out.
- c3-fix2 (crash3g, 12:57, slot a): INVALID and a bug of the second form: play began at 226.93 s, then the Presentation
  Thread logged nothing more (last line 4724), no live command read, 0 frames from play (c3-fix2.run). Likely cause:
  memory_staged_apply runs inside completed(), which a host path may call holding the host's SRW lock, and the second
  form calls hos_gpu_write_live (that lock) from there at every submission (the first form never reached the call).
  Fixed in 26ab8203a (pieces written at the next memory_end_submission, guess_apply, where lazy_apply writes its own);
  game killed, slot a restored (md5 equal to crash3-bak-c3g) and released 13:09. Built crash3h; queued c3-fix3 900 s
  (loop3c.sh: LF, keeps an existing backup). c3-v42b running in slot b since 13:08 (crash3g, GUESS_CHECKED=0: the hung
  path is not used).
- c3-v42b (13:08, slot b): INVALID, the title skip gave up after 120 s (bench.json "never reached play"); slot b
  restored before lock1 took it. Both tries of the v0.4.2 env (c3-v42, c3-v42b) failed to reach play; the other arms did.
- c3-fix3 (crash3h, 13:29, slot b, lease 20261124-131349-crash3-c3h-slot-1501): no hang (live commands read), but INVALID
  for the brief: the age toggle was undone ("cheats: age toggle undone, the map change was not requested": an event
  played), so Link stayed a child. Its first count line, still useful for the fix's behaviour: "guessed write-backs ...
  20003 runs staged; pieces: 1103 with words the GPU wrote (3341 words written), 18885 with none; 1568 words the GPU and
  the CPU both wrote left to the CPU; 1228 spans on a page written since the host's collection; 192 not written (no live
  pool)", and "hl trace wb: 0 write-backs into live heap blocks". Game stopped 13:41; loop3d.sh resends 999 until the
  toggle holds. Running now on the same lease: c3-fix4 (default) then c3-v42d, 720 s each.
- c3-fix4 (crash3h = 26ab8203a, slot b, 13:43-13:55): rc 0, 25 maps (Link adult from the first toggle; the loop's 999 at
  the 21st command toggles him back: 4 of the 25 map lines say child), no crash. Last count line: "hl trace wb: 0
  write-backs into live heap blocks, 12 blocks allocated over and 12 freed under a target written back in the last 5 s;
  51167 targets seen, 12 of them hit" (the 12 + 12 are sized write-back targets, [0x117e9a7000, +0x16c0) and the like,
  freed and reused by the game after its last write-back, 648 ms before in the first line: the game had stopped binding
  them). Fix's line: "640253 runs staged; pieces: 47572 with words the GPU wrote (163640 words written), 592665 with
  none; 110820 words the GPU and the CPU both wrote left to the CPU; 103291 spans on a page written since the host's
  collection; 6704 not written (no live pool)". c3-v42d running.
- c3-v42d (13:56, slot b): INVALID again, "never reached play ... gave up waiting for the title". Lease released 14:02,
  slot b restored first (dll md5 equal to crash3-bak-c3h; main12 copied its own exe at 14:02:48). run_all on the final
  code: 507 passed, 4 failed (the three known + test_switches_documented, regenerated in b48fc265e, --check up to date).
  No lease held, no loop or game of mine running. Done.
