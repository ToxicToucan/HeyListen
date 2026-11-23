<!-- Copyright (C) 2026 Sacrappy -->

# STALL2 (22 Nov 2026): the stutters of the owner's 23 minutes on integ4

Data: `build/s5/integ4-owner` (game_log.txt, present_trace.csv), `build/s5/integ4-owner-stalls` (80 dumps), main PC (RTX 5070,
7800X3D, 31.9 GB), switches `HL_HITCH_LOG=rel:2 HL_STALL_WATCH=16 HOS_PERF_REPORT HOS_THREAD_TIMES RX_VK_SLOW_LOG=50
HL_MEM_ACCOUNT=30`. 185,802 frames over 23:00.8, median 7.82 ms (p90 12.3, p99 20.1, p99.9 59.6). Analysis only: no game run, no
build. Scratch scripts: `E:\OOT\scratch-stall2` (dumps.py, blocks.py, pf.py, bymap.py). The reading of the log is only as good as
the report's matching, which was wrong (section 2): every number below comes from the fixed `engine/scripts/stutter_report.py`.

## Short answers

1. **Page-fault stutters.** 132 of the 223 stutters of 50 ms or more (20.9 s of lost time, 2.87 million faults, 7.3 us of wall
   clock a fault, median 9 us). By the thread with the most CPU in the gap (spinning threads left out): the presenting/render
   thread (`Presentation_Thread`, `host:mkw_vk_order`, `host:hos_held_submits`) 54 of 104 that have a thread list (10.5 s of 17.4),
   the loaders (`ARes_Loading_*`, `Resource_*`) 22, the game's logic threads 15, `OptimizeChangeBanc` 12. The hitch lines of those
   frames put 53 percent of the gap in `render` (descriptors/textures `desc` 32 percent, `upload` 17, `drvcomp` 13, `readback` 13) and
   36 percent in `game`, which is the game thread waiting for the renderer (os_wait lock/sleep). **The "no memory pressure" premise
   is false**: the PC's available RAM was 0.6 to 3.7 GB for the whole session (median 1.47 GB, 46 samples), the system committed
   80 of 103 GB, and the system read 7.7 million pages (29 GB, a system-wide counter: file reads count too) from disk. The game's working set stayed at 6 to 10 GB while its private
   commit grew from 10 to 30 GB (host side 6.6 to 17 GB). These are not clean first touches: of the 127 page-fault gaps with a stall
   block, 73 grew the working set (+3.9 GB net: first touch), 37 shrank it (-3.7 GB net with 4.0 GB faulted back: the memory was
   taken away, by our own discard of freed zone regions or by Windows, and touched again), 17 were flat. The 2.87 million faults of
   the 132 stutters are 11 GB of 4 KB pages, and the run's 31.4 million faults are 123 GB: 12 times the working set. Most faults
   are touches of memory that was there before.
2. **"Unknown".** The matching, not the rate cap or the thresholds: the log had a hitch line for 215 of the 222 stutters of 50 ms
   or more, and the greedy by-order match lost the sequence after the first dropped line. Fixed with a longest-common-subsequence
   alignment (long ones first): unknown at 50 ms and over 99 to 7, at 100 ms and over 22 to 3, matched to a hitch line or a block 218 of 222. The
   rest were named causes the report had no label for (the game's own SleepThread 9, render not split 7, other OS waits 2).
3. **The Market.** Not the Market as such: its **entry** (40 of the 53 stutters of 50 ms or more of its 235 s fall in the 26 s after
   entering, 4.2 s lost) and its exit (7, 1.8 s); the 3.5 minutes between have 11. Entering it cost the most of any zone because it
   was entered with the least free RAM (968 MB) and read the most from disk (713,719 pages, 2.7 GB in the 30 s window).
4. **The rule.** `delta 8 ms, ratio 2x, floor 16.7 ms, abs 50 ms` (the new defaults): 1,724 stutters (75 a minute) instead of
   25,501 (1,108 a minute), 89 percent of the excess time of the `8 ms / 2x` stutters, never more than 17 in a second (the game's
   20 a second cap is not hit any more).

Design (section 6): the memory is not the guest heap's lazy commit that HL_PREFAULT covered, so a pre-touch cannot be the fix. The
dominant cost is the memory manager's work (10 us a fault) on the critical threads while the PC is short of RAM, fed by our own
zone-change churn (discard then re-touch) and by the host-side growth. Plan: measure first (HL_FAULT_WATCH, one build), then keep
freed zone memory resident when RAM allows, and cut the host-side growth; expected effect and effort in 6.5.

## 1. Page-fault stutters in detail

### 1.1 Which thread

The stall blocks (`stallwatch: gap`) carry each thread's CPU cycles since the 16 ms mark and where it was at the end. Faults are
kernel time on the faulting thread, so the thread with the most cycles is the likely faulter, **after leaving out the spinners**:
`ModuleSystemWorker1-5` and others sit at `HeyListen.exe+0x1ada7ffa..0x1ada8010` or `ntdll+0x6ed2d` with 400 to 800 Mcycles; that is
`sleep_away`'s `pause` loop (disassembled from the slot's exe: `pause; cmpq hos_now_ns.freq`), not faulting. ntdll offsets read:
0x1659c4 ZwWaitForAlertByThreadId (a wait), 0x161e14 ZwReadFile, 0x161dd4 ZwWaitForSingleObject, 0x1623d4 ZwDelayExecution,
0x1627f4 ZwCreateFile, 0x161f94 ZwOpenKey.

| the thread with the most (non-spin) CPU in the gap | page-fault stutters of 50 ms or more |
|---|---|
| render/present: `Presentation_Thread` 43, `host:mkw_vk_order` 8, `host:hos_held_submits` 3 | 54 (10.5 s) |
| loaders: `ARes_Loading_0` 11, `ARes_Loading_1` 7, `Resource_Memory`, `tera::FileResourceMgr` | 22 (2.8 s) |
| game logic: `sead::MainThread` 3, `BaseProcCreateAndDeleteThread` 4, `PlacementMgr` 2, `ModuleSystemWorker*` | 15 (1.4 s) |
| `OptimizeChangeBanc` (the game's bank thread, 300 us on, 3 ms off) | 12 (2.4 s) |
| no thread list (hitch line only) | 28 (3.5 s) |

The loaders' top place is `ZwReadFile` in 4 of the first 10: the kernel copies file data into guest buffers nobody touched, so
those faults are inside the kernel's read. The render thread's end-of-gap place is `nvoglv64.dll` (the driver) in one, `win32u` in
others: faults taken by the driver while it creates resources are not ours to avoid by pre-touching.

**What is not known**: faults are not counted per thread (the process total only). The above is an inference from cycles; the
80 dumps are no help (next paragraph). The one tool that answers this exactly is `GetWsChangesEx` (section 6.1).

**The dumps cover only 00:45 to 00:52.** `HL_STALL_WATCH=16` caught a 16 ms gap every 100 ms from the first second it was allowed
to dump, and the 80-dump quota was full after 7 seconds of the 23 minutes. The job-trace chains (who signals the presenting
thread) exist for no stutter of the Market or any other zone; the per-thread blocks in the log (8,265 of them, 20 a second at most)
are what this analysis used. Next runs: `HL_STALL_WATCH=rel:8` and `HL_STALL_WATCH_MAX=400`.

### 1.2 On what memory

`HL_MEM_ACCOUNT=30` (46 samples). Growth by zone entry (the sample after the map line against the one before):

| zone (entry) | private commit | guest heap | guest regions | host "other" | pages read from disk in the 30 s | available RAM | stutters of 50 ms or more in the first 30 s (ms lost) |
|---|---|---|---|---|---|---|---|
| LostWood (00:38) | +6171 MB | +1453 | +1360 | +3381 | 350,491 (1.3 GB) | 1068 MB | 10 (2637) |
| KokiriForest | +3147 | +604 | +853 | +1686 | 242,273 | 3580 | 9 (1191) |
| HyruleField 1 | +3259 | +621 | +464 | +2217 | 132,822 | 3629 | 6 (850) |
| **Market** | +2750 | +139 | +621 | +1973 | **713,719 (2.7 GB)** | **968** | **42 (4347)** |
| HyruleField 2 | +708 | +5 | +543 | +165 | 196,065 | 1838 | 7 (1794) |
| Kakariko | +446 | +7 | +7 | +431 | 289,692 | 1224 | 5 (784) |
| DeathMountainTrail 1 | +288 | +5 | +100 | +175 | 153,616 | 1287 | 7 (782) |
| GoronCity | +344 | +1 | +2 | +340 | 144,750 | 2873 | 7 (809) |
| DeathMountainTrail 2 | -78 | 0 | 0 | -77 | 443,801 | 759 | 5 (649) |
| Dodongo | +226 | +30 | -1 | -1432 | 370,838 | 1716 | 6 (940) |
| ZoraRiver | +1304 | +8 | -2 | +1303 | 379,655 | 1266 | 11 (2044) |
| Wasteland | -1786 | 0 | +42 | -1825 | 280,168 | 950 | 6 (597) |

- 70 percent of the page-fault time (86 stutters, 14.5 of 20.9 s) falls within 30 s of a zone entry.
- **Guest heap lazy commit** (the exception handler that commits 1 MB on the first touch of reserved heap, and the only place
  HL_PREFAULT works): the log's "N MB of the game's memory committed on first touch" lines stop at **4096 MB, at log line 62879,
  just after HyruleField 1** (00:00 to 06:40). 51 of the 132 stutters (9.1 s) are before 06:40, 81 (11.8 s) after it, when no heap
  megabyte is committed any more. Later zones reuse the heap. HL_PREFAULT could not have touched the later 56 percent.
- **Host "other"** is where the account shows the growth (6.6 GB at the first sample to 17 GB): the allocations of 16 to 64 MB go
  from 1,060 MB to 9,485 MB, the ones of 2 to 16 MB from 682 to 1,172 MB, four 1 GB blocks stay (4.3 to 5 GB). What those are is
  not in the account (it sums by size): they have the size of the Vulkan back end's capture/mirror buffers (vk_memory.cpp
  `cap_buf_get`: 2 to 16 MB classes, own memory object from 16 MB) and the VMA blocks; `RX_VK_RES_LOG` (MemKind) would name them.
- **Falls are ours as often as Windows'.** `hos_region_free` runs `DiscardVirtualMemory` on every guest region a zone frees (on
  `Resource_Memory`, "for the regions a change of area frees it runs for milliseconds at a time"), and the next zone first-touches
  the same address space again. The stalls with a working-set fall are the zone changes: 08:37.5 (HyruleField to Market, -402 MB,
  213,555 faults, 647 ms), 00:37.5 (-361 MB, 303,942 faults, 889 ms), 19:16 to 20:23 (Dodongo/Zora: -366, -284, -230, -283, -203 MB).
- The 20 GB the game holds committed but not resident is the other half: private 30 GB, working set 6 to 10 GB.

### 1.3 First touches, or trims?

Cannot be confirmed as asked, because the machine was not unpressured. What the data say instead:

| class (127 gaps of 50 ms or more with a block) | gaps | gap time | faults | working set, net | wall a fault (median) |
|---|---|---|---|---|---|
| working set grew (+5 MB or more) | 73 | 12.2 s | 1.74 M (6.6 GB) | +3899 MB | 12.2 us |
| working set fell (-5 MB or more) | 37 | 6.5 s | 1.04 M (4.0 GB) | -3731 MB | 8.8 us |
| flat | 17 | 1.6 s | 0.19 M | +1 MB | 9.2 us |

A demand-zero fault on a quiet 7800X3D costs about 1 us (up to 3 us with several threads on the address-space lock). 9 to 12 us
means the memory manager is working to give the page: trimming others, waiting for the zero-page list to refill, writing modified
pages, with available RAM near 1 GB. That is the cost to cut, not the trap that the batch of HL_PREFAULT removes. Whoever holds the
other 20 GB of the PC is not in the log (the account only has the game's side). The machine is the agents' build PC, so the
session was probably not typical of a player's; but a 16 GB player is always in this state, and a 32 GB one with a browser and
Discord close to it (the account says: free 2751 MB with the game at 8.1 GB resident, at the start).

## 2. The "unknown" ones

`stutter_report.py` matched a stutter to its hitch line by order and length, looking at the next 4 lines. The game's cap (20
lines a second; 5,153 lines dropped in 545 notices, 20 percent of the 25,584 the rule would have written) makes the lines a
sub-sequence of the stutters, so the pointer drifted after the first dropped line, and a long stutter got a short line of about its
length or none. Check: at 50 ms and over the log had **215 hitch lines and 237 gap blocks for 222 stutters**; the report had matched
158. The threshold of the stall watch (16 ms) and the 20 a second caps are not why (the stall watch's 80 dumps are: section 1.1).
Fix (committed): `align()`: levels of 150, 60, 30, 16, 10, 6, 0 ms, each a longest-common-subsequence match of the unmatched
stutters and lines of that level between the pairs already found; the blocks use 5 ms or 15 percent (a block's gap is measured
between presents and differs from the trace's interval by up to 65 ms on the long ones, 582 against 645 ms). Three labels the report
lacked: `guest sleep (the game's own SleepThread)` (os_wait sleep over half the frame: 9 at 50 ms and over, 60 ms each at start-up),
`other OS wait (kernel, queue)`, `render thread busy, not split` (render over half the frame and none of it in pipe/xlate/upload/
memsync/readback: 7, in the driver or win32u at the end of the gap; 58 to 226 ms, which no switch explains yet).

| at 50 ms or more | before | after |
|---|---|---|
| unknown | 99 | 7 (4 of the 222 have no line or block at all, 343.2 ms at 00:32.6 the longest) |
| page faults | 84 | 132 |
| lock wait on the frame hand-off | 17 | 34 |
| readback wait | 14 | 17 |
| zone load | 8 | 8 |

The stutters before the first `map:` line (00:00 to 00:38, shown as "?", 71 of the 222) are the start-up: the movie threads, the
title screens and `SleepThread`s of about 60 ms (the "guest sleep" label), not play.

## 3. The Market

Time in each zone is taken from the map lines placed between matched hitch lines (about 2 s of uncertainty):

| zone | seconds | visible stutters a minute (new rule) | 50 ms or more | a minute | 100 ms or more | ms lost |
|---|---|---|---|---|---|---|
| LostWood | 285 | 62 | 13 | 2.7 | 7 | 3808 |
| **Market** | 235 | 77 | **53** | **13.5** | 13 | 5664 |
| HyruleField (both) | 190 | 102 | 18 | 5.7 | 6 | 2678 |
| GoronCity | 127 | 62 | 8 | 3.8 | 4 | 1050 |
| DeathMountainTrail (both) | 113 | 90 | 13 | 6.9 | 4 | 1524 |
| ZoraRiver | 112 | 105 | 13 | 7.0 | 6 | 1554 |
| others | 280 | 31 to 63 | 33 | | | |

The Market is only the worst when counted a minute: 40 of its 53 are in the 26 s after entering (08:37.5 to 09:03: 24 page faults,
12 readback waits), 7 at the exit, 11 in the 3.5 minutes between. The 66 earlier counted were 18 stutters taken for Market that
belonged to the neighbours, the wrong matches carrying the wrong zone; 53 with the fixed matching. What is special:

- The entry reads **2.7 GB** from disk (the most of any zone, the double of the next but one) with **968 MB available** (the
  third lowest at entry, after two small entries). The stutter time of an entry follows how much it allocates against how
  little RAM there is: growth of 1 GB or more with 1.3 GB or less free costs 2 to 4 s (Market +2.75 GB / 968 MB: 4.3 s, LostWood
  +6.2 GB / 1068: 2.6 s, Zora +1.3 GB / 1266: 2.0 s); growth of 3 GB with 3.6 GB free costs about 1 s (Kokiri 1.2 s, HyruleField 1
  0.85 s); entries that add little (Kakariko, DeathMountain, Goron, Wasteland) cost under 1 s whatever was free (DMT 2 and Wasteland
  had 759 and 950 MB free and 0.6 s). The Market adds the length: 25 s of streaming after the screen is up, not a loading screen
  (1 of the 40 is labelled `zone load`).
- STALL1 measured the same Market on a clean machine: 10 stutters over 50 ms in 144 s (4 a minute) at 88.8 fps. 13.5 a minute with 1 GB free.
- `readback wait` stutters (12 of 40 on entry) are the renderer waiting for the GPU copy of freshly loaded textures; their cost
  does not depend on RAM and is a second, smaller topic.

## 4. The relative rule

On this run: median 7.82 ms; the rule `2 ms and 1.25x` gave 25,501 stutters (one frame in seven), 3,424 of them under 6 ms, 14,000
between 8 and 16 ms: the normal jitter of a 130 fps game (the 90th percentile is 1.57x the median). `4 ms/1.5x` 10,151, `6/1.75`
4,456, `8/2` 2,339 (never over 23 in a second), `8/2/floor 16.7` 1,724 (max 17 in a second), `12/2` 1,292, `16/2` 690.

Default now: **`--delta 8 --ratio 2 --floor 16.7 --abs 50`**: a frame over the median of the 30 before by 8 ms and 2x, and over
16.7 ms (a missed 60 Hz refresh: what a player on any common display sees as a hitch; at a 7.8 ms median the 8/2 rule alone starts
at 15.8 ms), or over 50 ms. It keeps 1,724 stutters and 89 percent of the excess time (frame minus median) of the 2,339 of `8/2`. Classes moved with it: minor under 3x, major 3 to 6x, severe
over 6x or 100 ms. For the game's own switches `HL_HITCH_LOG=rel:8` and `HL_STALL_WATCH=rel:8` (their ratio is fixed at 1.25: at a
median of 8 ms or less 8 ms is the stricter, which is the wanted rule; at a 4 ms median (240 fps) it flags 12 ms, 3x, and the tool
filters the rest). `rel:8` keeps the log's cap out of the picture (1 second of 1,356 has more than 20 candidate frames, against 542 at `rel:2`).

Changed in `engine/scripts/stutter_report.py` and `engine/tests/test_stutter_report.py` (explicit `--delta 2 --ratio 1.25 --floor 0`
for the old fixtures; a new test of the default rule and of align() with dropped lines).

## 5. Why HL_PREFAULT did not help, and why this is not the same case

HL_PREFAULT (STALL1, `host/mkw_glue.c prefault_mb`): in the exception handler that commits one megabyte of the reserved guest heap,
`VirtualLock` + `VirtualUnlock` the megabyte, **on the thread that faulted, before it continues**. Three reasons it cannot help
here or on the 3050:

1. **It does the same work in one call, at the same moment, on the same thread.** A fault's cost under pressure is the memory
   manager making a free zeroed page (trimming others, writing modified pages, zeroing), not the trap; a batch of 256 pages pays
   that work 256 times in a row and the loader waits for all of it. On the 3050 under the 6 GB hog the pages came from other
   processes' trims, VirtualLock also needs the working-set minimum (512 MB asked) that forces the trimming harder. Our 9 to 12 us
   a fault is that regime: batching removes at most the 1 us.
2. **No lead time.** The touch happens when the first touch would have happened. A pre-touch only helps if it runs on another
   thread, ahead of the loader, early enough, and that needs a prediction of what the zone will use.
3. **It only covers the lazily committed heap**: 4.1 GB in this run, all of it before 06:40; a megabyte it has pre-touched stays
   pre-touched for the rest of the run (the heap is reused), so every zone after the first few passes it by. The host's own
   allocations (the render thread's 41 to 54 percent, the 9.5 GB in 16 to 64 MB blocks), the discarded zone regions and the
   driver's are not touched.

How this run differs from the 3050's: the main PC's stutters are not the hog-induced storms (it had 31 GB and a clean-machine
baseline of 4 a minute in the Market); but it had 1 GB available in the worst places, which is the same regime in a milder form.
The cure that works in both is to **not need the pages again** (keep, reuse) and to **not hold more than needed** (MEM1), not
to touch them earlier.

## 6. Design for the dominant cause

Dominant cause, in order of lost time (20.9 s of 50 ms and over, 70 percent of it at zone entries): faults taken by the render
thread and the loaders at 9 to 12 us, on memory (a) our own zone change just gave back (`discard_pages`: working-set falls of 3.7 GB
net over 37 gaps), (b) the host back end's growth and churn (+11 GB of 16 to 64 MB blocks in the run), (c) the guest heap's lazy
commit (first 4 GB only), in a machine with 0.6 to 3.7 GB available.

### 6.1 Step 0 (the measurement the design needs): `HL_FAULT_WATCH=1`, diagnostic, off by default

`InitializeProcessForWsWatch` + `GetWsChangesEx` in a worker every 100 ms (the buffer of 64 K entries): each entry is `{FaultingPc,
FaultingVa, FaultingThreadId, Flags}` for a page added to the working set. Per window log: faults by thread name (the job
trace's name table), by region (guest heap / guest regions / guest pools / host private by allocation / mapped / image: the same
classification as `account_main`) and by Pc module; and one line each for `hos_region_free` (bytes, thread, ms of the discard) and
the guest-heap commit handler (megabytes). Also in the account line: the system's list sizes (`SystemMemoryListInformation`: free,
zeroed, standby, modified) and the five biggest other processes' working sets, which settle what holds the other 20 GB.
Effort: about 150 lines, one build, no engine behaviour change. Result: the thread x memory x zone-phase table that section 1 can
only infer.

### 6.2 Candidate fixes, judged with the data

| candidate | covers | verdict |
|---|---|---|
| Pre-touch the heap's lazily committed megabytes during the loading screen (the owner's candidate) | the guest heap's first 4 GB (39 percent of the stutters, 44 percent of the time, all before 06:40), nothing later | No: the faults come after the screen is up (1 of 40 Market entry stutters is `zone load`), the memory is not known until the loader asks, and it adds resident memory to a pressured PC. Only as the commit-ahead below |
| Commit-ahead + `lock or [p],0` touch on a low-priority worker (move first touches off the critical thread) | heap first touches, ARes loaders | Partly: free in the first minutes of a run. Takes the exception + `VirtualAlloc` out of the loader (4,100 to 5,000 one-megabyte exceptions in the run); the touch itself is the same 10 us, on another core, in parallel with the loader; gain only if the worker is ahead, otherwise nothing. Keep as a small extra, not the fix |
| Pre-touched pool for our own host allocations (capture/staging/upload buffers) | the render thread's 41 to 54 percent | Yes, as **retention**, if step 0 shows the churn: keep freed capture buffers (`cap_free`) and the VMA free blocks resident and reuse them, instead of freeing and re-faulting (read in the code: `cap_buf_retire` frees, `create_buffer_cached` allocates; not yet seen in a trace); its size must be capped by available RAM (the 9.5 GB in 16 to 64 MB blocks is already too much: it is the pressure). The first fix to try after step 0 names the allocations |
| Large pages | the 4 KB fault count by 512 | No: `MEM_LARGE_PAGES` needs SeLockMemoryPrivilege (not for players), 2 MB contiguous physical memory (not there at 1 GB available), cannot be trimmed or discarded (`hos_large_pages` already disables the discard) and pins RAM: it worsens the pressure |
| **Keep the zone's freed regions resident instead of discarding them, when RAM allows** | the "fell" class (37 gaps, 6.5 s, 31 percent of the time) and the re-touch after it (a share of the 73 "grew") | **Yes: the cheapest change with a measured cause.** `hos_region_free` does not call `discard_pages`; the guest must still see zero, so the region's bytes are zeroed by `memset` on `Resource_Memory` (where the discard already runs, off the critical path, 366 MB in about 40 ms) when freed, or lazily by the worker, with a "zeroed" mark kept per region: a region allocated again before it is zeroed waits for it (or discards, as today). Gate: available RAM over 6 GB (the PC's own `GlobalMemoryStatusEx`), else as today |
| Cut the footprint (MEM1's growth: 10 to 30 GB private) | the pressure itself, all of it | The biggest lever: at 6 GB available the 9 us fault is 1 us. Not mine; named here because every fix above competes with it for RAM |

### 6.3 Proposed design (after step 0 confirms the table)

1. **Retention instead of discard** (`HL_REGION_KEEP`, default on when available RAM over 6 GB, off otherwise; `=0` off): the
   guest regions a zone frees keep their pages; zeroed off-thread; reuse of the address space finds resident zero pages. Applies to
   `hos_region_free` / `discard_pages` only (not the guest heap's MEM_RESET path, not the pools' `HOS_NOACCESS_RAM` give-back).
   Risk: the guest reads a stale byte if the zeroing is skipped: the "zeroed" mark per region and a test with a region written,
   freed, re-allocated and read; `HOS_KEEP_FREED_PAGES` exists for the debug case (no zeroing: do not reuse its path).
2. **Warm capture buffers** (`RX_VK_CAP_KEEP_MB=<n>`, default tied to available RAM): the spares of `cap_free` stay up to n MB
   between zones (today `cap_buf_retire` frees and `create_buffer_cached` allocates again, in the render thread); each new block's
   pages are first touched by a worker (`lock or`) right after the allocation and before the first capture, at low priority.
3. **Commit-ahead for the heap** (`HL_COMMIT_AHEAD=<MB>`, default 8): when the handler commits megabyte M, a low-priority worker
   commits and touches M+1 .. M+8 (never beyond the heap's reserved size, never over a granule `hos_gp` has dropped).
4. Step-0 telemetry stays (`HL_FAULT_WATCH`), plus the account line's system lists.

### 6.4 Why this and not a pre-touch pool

The pre-touch pays the fault where it was and adds resident pages; retention removes the fault (the page is already there, zeroed
once, off-thread). Where the memory is the driver's or Windows' it is not ours to keep, and the answer there is less footprint.

### 6.5 Expected effect and effort

- Step 0: 1 day with the owner's run or a scripted Market + zone-change run under the pressure of a fixed hog (STALL1's
  `memhog.py`, 6 GB, free RAM near 2 GB), then the table.
- Retention (1): removes the fell class: 37 gaps, 6.5 s of 20.9 s, a part of the re-touch after it; I expect 30 to 45 percent of
  the 50 ms stutter time of zone changes with 32 GB, nothing under 6 GB available (gated off). Effort: about 120 lines in
  hos_mem.c / hos_guestpages.c, 1 test, 1 build; risk medium (zero semantics).
- Warm capture buffers (2): the render thread's share (up to half of the 50 ms time of zone entries if the churn is where the
  account says); effort about 80 lines in vk_memory.cpp, risk low. Depends on step 0.
- Commit-ahead (3): the first 6 minutes only; about 60 lines, risk low, the gain limited to what the worker is ahead by.
- Not claimed: fps. The effects are the counts and ms of stutters of 50 ms and over per zone entry.

### 6.6 Measurement plan (main PC, speed lease)

- Build A (integ4 tip) and B (the fix), PGO as the rules say, **one build lease each, no `mkg.ps1` while a speed lease is held**.
- Scenes: Hyrule Field to Market and back (the owner's route: `market-spin` plus a castle-move field leg), then the zone changes
  Kakariko to Death Mountain Trail to Goron City to Dodongo. A B A B, 3 each.
- Pressure: once clean (nothing else running), once with `memhog.py` holding 6 GB so that available RAM is 1.5 to 3 GB (the
  owner's session). Both A and B in both.
- Switches: `HL_HITCH_LOG=rel:8 HL_STALL_WATCH=rel:8 HL_STALL_WATCH_MAX=400 HL_MEM_ACCOUNT=10 HL_FAULT_WATCH=1`.
- Metric: `stutter_report.py` on each run: stutters of 50 ms and over in the 30 s after each map line, total ms lost, page-fault
  class only, the faults a gap and the ws falls; fps as a sanity check only. The short A/B first (castle-move x3, then Market x3);
  the full 3x6 only to confirm an adoption.
- Machine time requested: step 0 needs one build and 2 speed runs of about 3 minutes; the A/B 12 runs of 3 minutes + 2 builds.

## 7. What was changed in the repository

- `engine/scripts/stutter_report.py` (copied to `build/s5`): `align()`, the three labels, the new default rule and classes, doc.
- `engine/tests/test_stutter_report.py`: the old fixtures with explicit old rule, a test of the new default and of `align()`.
- This file. No game code, no build, no run.

## 8. Step 0 measured (build stall2 = perf/stall2 8bcbb2ad7, slot c, 22 Nov evening)

Runs: `build/s5/s2f-market` (market save, `market-spin`, 84.4 fps from play, play began 49.3 s after the start) and
`build/s5/s2f-hyrule-field` (hyrule-field save, `castle-move`, 74.8 fps, play at 55.6 s), 116 s each, with `HL_FAULT_WATCH=10
HL_STALL_WATCH=rel:8 MAX=400 HL_HITCH_LOG=rel:8 HL_MEM_ACCOUNT=30 HOS_HOST_SPLIT=40,30 RX_VK_LEDGER=30`. They are short: the Market
run holds one zone entry (the load, then 63 s of play), the field run one load and 21 s of play. There is no zone tour, so no growth
over a session here (the 23 minutes of section 1 and MEM1's sections 17 and 21 of memory-boundary.md are the growth data). Scripts:
`E:\OOT\scratch-stall2\fw_an.py` and `join.py`. `HL_FAULT_WATCH` found its buffer too small 0 times. Its per-second lines list the 6
largest threads and memories, so the sums below are lower bounds; the 30 s lines are exact for their top 10 cells.

### 8.1 Thread x memory x phase (pages added to the working set, 4 KB, from the seconds with 2000 pages or more)

| run, phase | pages (MB) | loaders (`ARes_Loading_*`, `Resource_Memory`) | host/driver tids | render + replay | OptimizeChangeBanc | game logic | guest address regions | guest GPU pool | host private, no owner | guest heap |
|---|---|---|---|---|---|---|---|---|---|---|
| Market, load (12 to 49 s) | 758 K (2960) | 350 K | 216 K | 23 K | 35 K | 29 K | 370 K | 50 K | 251 K | 58 K |
| **Market, play (63 s)** | 638 K (2490) | **545 K** | 22 K | 15 K | 29 K | 3 K | **372 K** | **219 K** | 24 K | 2 K |
| Field, load | 830 K (3240) | 420 K | 189 K + 48 K | 6 K | 33 K | 27 K | 421 K | 71 K | 239 K | 66 K |
| Field, play (21 s) | 184 K (719) | 54 K | 72 K | 8 K | 11 K | 6 K | 65 K | 64 K | 28 K | 6 K |

- **Loads.** `ARes_Loading_0/1` first-touch the guest's address regions (the loaded files' buffers: `ReadFile` into fresh memory, so
  the faults are inside the kernel's read) and the GPU pools. The "host private, no owner" 250 K pages by unnamed host threads are the
  driver and `ucrtbase` (shader and pipeline compile of a cold start; code in `nvoglv64.dll`): load phase only.
- **Market play.** The faults are the loaders': 545 K of 638 K pages, on guest regions (372 K = 1.45 GB) and GPU pools (219 K), for
  the whole 63 s while the Market streams in; the render thread takes 13 K. 122 of the 130 stutters of the Market's play phase (over
  16.7 ms and 2x) fall in a second where the loaders on guest regions or pools were the largest faulters: 84 on regions (2.2 s of
  excess frame time), 38 on pools (0.8 s). The stutters of 50 ms and over in play are at 18.2, 21.3, 22.9, 24.9, 41.5, 51.9, 52.9 and
  55.0 s, each with `ARes_Loading_0` at 4 to 10 K pages in that second.
- **Field play.** In the first seconds of play (1.3 to 3.9 s) the largest faulters are `host:mkw_vk order`, `host:mkw_vk replay
  worker` and `Presentation_Thread` on **guest GPU pools** (10 to 15 K pages a second): 29 stutters, 4.2 s of excess, with the 1.58 s
  and 1.03 s frames. The replay reads pool pages the game has not written yet, or that the cold pass has taken out; the watch cannot
  say which (next diagnostic: the cold pass's trims a second). The first 1.3 s of the Market run (572, 202, 195, 121 ms; `host:
  image` 2000 pages a second) are the exe's `.text` being paged in as the lifted code first runs: 6 stutters, 1.1 s, once a start.

### 8.2 Discards against trims

`hos_mem.c discard_pages` (`DiscardVirtualMemory` on each region the guest frees) is logged with the faults:

| phase | discards (calls, MB, wall ms) | guest-region pages faulted back |
|---|---|---|
| Market load | 760, 808 MB, 233 ms | 370 K (1445 MB) |
| **Market play** | **638, 3467 MB, 601 ms** | **372 K (1453 MB)** |
| Field load | 912, 1824 MB, 302 ms | 421 K |
| Field play | 35, 4 MB, 23 ms | 65 K |

The Market streams: the game frees and allocates regions all the time (55 MB a second freed, a discard every 100 ms), and the
loaders fault 42 percent of the freed bytes straight back. `HL_MEM_COLD` took out only 138 MB of pools and 54 MB of heaps in the
Market run: its pass was **held** while free RAM was under 3072 MB ("held, 2883 MB of RAM free"); in the field run it took out
1889 MB of pools and 563 MB of heaps (RAM above the line at first). The system's own trims cannot be seen from the process, but in
the Market the working-set falls come from our discards (3.5 GB against 0.2 GB of cold pages). **The falls are discards, then first
touches of the same address space**: the owner's three kinds of page-fault stutter (first touch in a grown zone, fall then
re-touch, flat) are one thing, the zone's memory churn.

### 8.3 Footprint, from HOS_HOST_SPLIT (censuses at 40, 71 and 102 s), RX_VK_LEDGER and HL_MEM_ACCOUNT

Market run, MB (RAM = resident, commit = charged):

| category | 40 s | 71 s | 102 s (RAM / commit) | grows? | reclaimable? |
|---|---|---|---|---|---|
| guest window | 4249 RAM | 6713 | 6768 / 7488 | with the zone (owner: guest heap 1.4 to 4.5 GB, regions 1.7 to 7.3 GB in 23 min) | pools 3471 RAM once uploaded: `HL_MEM_COLD` (held under 3 GB free); regions: keep rather than discard (8.4) |
| driver arenas (private 512 MB and up, 4 allocations) | 3501 RAM | 2255 | 2261 / 3841 | flat at 4 blocks (owner 4.3 to 5.0 GB) | owner not named; the driver's, not ours to free |
| driver 32 to 128 MB regions (182 allocations) | 17 RAM / 889 commit | 18 / 5561 | **20 / 5840** | **yes, with the VMA blocks** (owner: 1.0 to 9.5 GB) | commit only (0.3 percent resident): `RX_VK_BLOCK_RECLAIM` (MEM1, unmerged) saves commit, not RAM |
| back end's tagged memory (captures, staging, streams) | 283 | 330 | 346 (captures 218, staging 140) | slowly (captures made 49 to 3955 in a session, 70 to 120 MB kept) | spares already trimmed; small |
| C and Windows heaps | 332 | 433 | 443 (628 commit) | +0.4 GB over a session (MEM1) | no |
| images | 201 | 270 | 273 | no | no |
| job-trace ring (HL_STALL_WATCH) | 128 | 128 | 128 | diagnostic only | off |
| total RAM counted (= working set) | 8740 | 10201 | 10310 | | |
| commit charge | 11366 | 18870 | 19092 | **+7.7 GB in 60 s, 5.0 of it the driver's 32 to 128 MB regions** | |

VRAM ledger at the end (Market): texture images 2994, mirrors 1474, write-back shadows 229, staging 140, BC scratch 160, free inside
VMA blocks 567 (image 140, render-target 353), 34 retired chunks waiting for the GPU (1496 MB). Field run: images 2187, mirrors
1578, 15 retired chunks (654 MB), free in blocks 440.

**Who else holds the RAM** (the account line, queryable processes only): 8.0 to 8.25 GB in all: Spotify 1.1 to 1.2 GB, two
`claude.exe` at 1.0 and 1.1 GB, Discord 0.9 GB, opera 0.3 GB, the rest under 0.3 GB each. With the game at 10.3 GB that is 18.5 of
31.9 GB, and available RAM was **1.0 to 2.4 GB in the Market run and 2.0 to 3.9 GB in the field run**: about 12 GB of the PC is in
neither (kernel and driver memory, the modified list, the compressed store, processes this one cannot query: the agents' sessions
and builds share this PC). The owner's pressure is the same regime. The game is not the cause of those 12 GB and cannot cure them; its
10.3 GB working set and its commit-only driver memory are the part we control.

Growth without bound over a session (MEM1's long runs and the owner's 23 minutes): live imports (4 to 229, 6.6 GB), the driver's
16 to 64 MB commit that follows the VMA blocks (1.0 to 9.5 GB), free space inside VMA image blocks (0.2 to 3.5 GB), cold units left
in for good. Bounded: BC cache (1 GB), captures, the spill, pipelines, C heaps, the arenas. This pair adds the rate: **+5 GB of
commit-only driver memory in the first minute of a zone**, then flat.

### 8.4 Fixes, ranked

By stutter time saved (the owner's baseline: 132 page-fault stutters, 20.9 s; the Market's play phase here: 4.2 s of excess in 130):

| rank | fix | stutter effect (evidence) | RAM effect | effort, risk |
|---|---|---|---|---|
| 1 | **`HL_REGION_KEEP`**: keep a zone's freed regions resident instead of `discard_pages`, zero them off the critical thread (`Resource_Memory` or a worker); on only while available RAM is over 6 GB | the loaders' region faults: 84 of the Market play's 130 stutters (2.2 s of 4.2 s), 372 K pages (1.45 GB) re-touched after 3.47 GB discarded; most of the owner's 30 s-after-entry page-fault time | costs RAM, up to the churn set (0.5 to 1.5 GB resident that Windows would have reused); off below 6 GB, so it does not apply to the owner's pressured PC (1 to 2.4 GB available) | about 120 lines, medium (zero semantics) |
| 2 | the replay's pool faults at the start of play (field: 29 stutters, 4.2 s, the 1.58 s and 1.03 s frames): find out which of never-written and cold-taken-out; if the cold pass, an age rule that keeps what the replay reads; if never written, skip untouched pages in the capture's copy | the largest single stutters of play start | neutral | one more diagnostic, then about 60 lines |
| 3 | commit-ahead and a `lock or` pre-touch of the heap on a worker | the first 4 GB of heap only, the first minutes of a run | neutral or negative | 60 lines, low, small |

By RAM saved (none changes the Market's region churn):

| rank | fix | RAM | commit | evidence |
|---|---|---|---|---|
| 1 | the cold pass for pools also under 3 GB free (`HL_MEM_COLD_LOWMEM=0`, or hold only the heaps) | up to 3.3 GB of the pools' CPU copies (3471 resident in the Market, 3176 in the field) | 0 | pools are uploaded and mirrored in VRAM; STALL1's 3050 hog test went the other way (56 against 77 frames over 50 ms), so A/B first |
| 2 | `RX_VK_BLOCK_RECLAIM`, idle pool-mirror give-back, imports given back (MEM1, unmerged, function-validated only) | small (driver commit) | 2 to 5 GB of the 5.8 GB driver commit, and 6.6 GB of imports over a session | commit was 70.7 of 103 GB; at the limit the game dies out of commit, not RAM |
| 3 | tag the four 1 GB driver arenas (2.3 to 3.6 GB RAM) with `hos_mem_tag` at each `vkAllocateMemory`, then decide | unknown | | the largest unnamed block in RAM |
| 4 | captures (218 MB cached) | under 0.2 GB | | already bounded |

### 8.5 The zone tour

The runs hold one entry each. There is no scripted route for Kakariko and Death Mountain Trail; the Market to Hyrule Field gate is a
`LUP` leg of about 1.5 s (`run_bench.py`, TICK-031). A first command, **unverified**: check the `map:` lines of its log (Market,
HyruleField, Market, HyruleField) before reading anything from it:

`HL_RUN_SECONDS=260 HL_INPUT_SCRIPT="30:A,34:A,38:A,42:A,46:A,50:A,54:A,58:A,62:A,66:A,70:A,72:B,73:B,74:B,75:B,100:LUP/2500,150:LDOWN/2500,200:LUP/2500,240:LDOWN/2500" bash build/s5run.sh --slot c --save build/s5/savelib/market s2f-tour castle-move HL_FAULT_WATCH=10 HL_STALL_WATCH=rel:8 HL_STALL_WATCH_MAX=400 HL_HITCH_LOG=rel:8 HL_MEM_ACCOUNT=30 HOS_HOST_SPLIT=40,30 RX_VK_LEDGER=30`

and a second run of the same with `HL_MEM_COLD_LOWMEM=0` for rank 1 of the RAM list. Kakariko and Death Mountain Trail need the
owner's hands: one session of ten minutes over that route with the same switches.

## 9. Step 1 built: `HL_REGION_KEEP=<seconds>` (perf/stall2, host/hos_regionkeep.c, hooks in hos_mem.c)

**What it does.** When the game frees an address region (`FreeAddressRegion`, `hos_region_free`) above the heap, the region is not
discarded: it goes on a keep list with its resident bytes and the time. A later `hos_region_alloc` that takes any part of that address
space gets it zeroed in place before it returns (the overlap only; what is left of the entry stays kept, split in two if the new
region lies inside it). A region not re-allocated for `<seconds>` is discarded as before, by a thread of its own (1 s granularity).
Off by default (`HL_REGION_KEEP` unset or 0: the old path, byte for byte).

- `HL_REGION_KEEP_MB` (default 2048): resident MB on the list; over it the oldest are discarded first.
- `HL_REGION_KEEP_LOWMEM_MB` (default 1024): while the system's available RAM is under it the oldest are discarded one after another
  until it is not.
- Not kept (discarded at once, as before): a region with pages the Vulkan back end imported (`rx_guest_imported`), one while the
  guest-pages substrate is on (`hos_gp_ready`), one with under 1 MB resident, one when the 256-entry list is full.
- One log line a second when anything moved: `hl keep: this second: kept N MB, reused N MB (zeroed in X ms), discarded N MB (aged,
  over the cap, under pressure); on the list: N MB resident in N regions`.

**What the game expects (checked in the source).** `AllocateAddressRegion` only hands out address space (hos_mem.c). The game maps
pages in a region with `AllocateMemoryPages`, and above 4 GB that is `hos_commit(a, n)`: "A commit leaves committed pages as they are
and gives fresh ones zeroed" (the comment at the call, after the Horizon rule: only newly mapped pages read as zero). So a region
re-allocated over kept, still committed pages would show the game the old zone's bytes through `hos_commit`: the zeroing at
allocation is not optional, and it is what the new region would have read after the discard (demand-zero). Below 4 GB nothing is
kept (the keep is only for `freed >= HOS_HEAP_BASE`). The zeroing is a `memset` over the resident committed pages (no fault) and a
`DiscardVirtualMemory` over committed pages that are **not** resident (trimmed or paged out, their backing may hold the old bytes), with
`HOS_WRITTEN` after it (the soft write watch). It runs on the thread that called `hos_region_alloc`, which is the game's resource
memory thread, not the render or the main thread, and in the unit test it costs 0.3 ms for 8 MB, against about 2.5 ms a MB for faults
at the measured 10 us a page. The game never reads a byte it did not write, as with a discard.

**Not done, on purpose.** Lazy zeroing (zero on first touch by a handler) is not possible here: the pages are committed memory the CPU
touches without a handler. Zeroing on a worker ahead of the allocation would need a guess of which kept region the game takes next, and
a region allocated again before the worker reached it would have to wait: the synchronous memset on the resource thread is what is built.

**Test.** `tests/test_region_keep.py` builds `hos_regionkeep.c` with `-DHK_TEST` (a stub header, a 128 MB window) and runs
`tests/region_keep_test.c`: an 8 MB region kept with its bytes; a new region over its middle 2 MB reads zero while the two ends stay;
a new region over the rest is zero and a second use of the same range does not zero what the game wrote; under 1 MB resident not kept;
aged out by the thread after the time (not before); the cap discards the oldest; the other kept region is unchanged. PASS here.

**A/B (to run on the main PC, speed lease; slot c, build = this branch).** Common switches (both arms):
`HL_FAULT_WATCH=10 HL_STALL_WATCH=rel:8 HL_STALL_WATCH_MAX=400 HL_HITCH_LOG=rel:8 HL_MEM_ACCOUNT=30`. B adds `HL_REGION_KEEP=20`.

1. The scripted tour (Market, Hyrule Field, Market, Hyrule Field; unverified route), order A B B A, 4 runs of 260 s:
   `HL_RUN_SECONDS=260 HL_BENCH_LIMIT_S=300 HL_INPUT_SCRIPT="30:A,34:A,38:A,42:A,46:A,50:A,54:A,58:A,62:A,66:A,70:A,72:B,73:B,74:B,75:B,100:LUP/2500,150:LDOWN/2500,200:LUP/2500,240:LDOWN/2500" bash build/s5run.sh --slot c --save build/s5/savelib/market kt-<A1|B1|B2|A2> castle-move <common> [HL_REGION_KEEP=20]`
2. The Market entry, `market-spin`, order A B A B A B, 110 s: `bash build/s5run.sh --slot c --save build/s5/savelib/market km-<A1|B1|A2|B2|A3|B3> market-spin <common> [HL_REGION_KEEP=20]`.
3. **Before reading anything** from a tour run: `grep -n "map: " build/s5/kt-A1/game_log.txt` must show Market_Child, HyruleField_Child, Market_Child,
   HyruleField_Child. If it does not, stop and say so: the script's legs need changing.
4. The report: `python -I -B engine/scripts/keep_ab_report.py A=build/s5/kt-A1,build/s5/kt-A2 B=build/s5/kt-B1,build/s5/kt-B2` (and
   the same for `km-*`): per run the zone changes it read, fps from play+10 s, the stutters from play (all, page-fault class, 50 ms and
   over), and the count and excess ms in the 30 s after each zone change (the first map line, the start's own load, left out). In B the
   `hl keep:` lines say kept, reused and discarded MB: reused should be most of what is freed, and `discards logged` (the fault
   watch's discards) should fall by that much. A change in `guest: address regions` pages of `ARes_Loading_*` in the `hl fault:` lines is the direct
   proof.

**What would show it does not work.** Reused MB near 0 with kept MB high (the game takes other addresses: first-fit top-down hands the
same address only if the sizes fit; then the age rule just delays the discard, and resident RAM rises for nothing), or the working set
growing by the kept amount with stutters unchanged, or a visual fault in the zone after (stale data: the zeroing is wrong).
