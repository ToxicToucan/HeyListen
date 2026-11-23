# STEP1: one deterministic readback ring for R1, R2 and R3 (`HL_RB_LAG`), design note

Owner: STEP1, branch `refactor/step1` (= `refactor/expo1` + `refactor/readb2` + this work), 2026-11-21. Step 1 of `refactor-plan.md`. Ledger rows R1,
R2, R3 in `readback-ledger.md`; the per-class notes are `exposure.md` (R1) and `readbacks-r2-r3.md` (R2, R3). Code: `engine/gpu/vk/rb_ring.h` (the
pure rules, tested by `engine/tests/test_rb_ring.py`), `vk_rbring.cpp` (ring, stage, serve, check), hooks in `vk_memory.cpp` (the binding and the
end of the submission) and `vk_replay.cpp` (`invalidate`).

## Verdict first

- **One mechanism serves the three classes.** A range the CPU reads back is learned from its reads; the next submission that writes it (a
  binding for writing in a mirror) no longer writes it back to guest memory: the end of the submission copies its bytes into a ring the engine
  owns, tagged with the frame count. The game's read at frame `c` applies to guest memory the newest not-yet-applied entry with
  `tag + L <= c` (`L = HL_RB_LAG`, 2 is the intended value) and never waits for the GPU. Nothing else writes the range, so what the game sees is a
  function of the frame count and of the GPU's *results*, not of the GPU's *timing*.
- **Lag 1 is the old path, bit for bit.** `HL_RB_LAG=1 HL_RB_LAG_CHECK=1` (the old path runs, the ring is compared with the value it read):
  see section 4: 0 differences in R1, R2 and R3, hundreds of thousands of reads.
- The same rule for the three classes (the exposure's old "dispatch N-L" is now "frame c-L": the meter dispatches once a frame, so the same
  thing). `HL_EXPO_GPU` (EXPO1's per-dispatch ring) stays as it was; with `HL_RB_LAG` set the unified ring answers first.
- Ranges the CPU reads that **no program writes** (the meter's other two rings and its 0xc0 companion block, the 0x40 slots nothing writes: half of
  the R2 reads) are answered with no wait either: they were waiting for write-backs of *other* data on the same pages (1.5-1.9 ms a frame on the
  5070 for the meter's unused ring alone).
- Results: sections 4 and 5 (filled with the measurements).

## 1. The rules (`rb_ring.h`)

| rule | what |
|---|---|
| class | by the read's size: `0x1040` and `0xc0` R1 (the meter's block and its companion), `0x40` R2, `0x2000` R3. Which ranges are *the game's* is learned: `rbring_note_read` (every `invalidate` of such a size) |
| producer | not a list of program keys: any binding **for writing** that is **exactly** a learned range (`gpu_buffer_bind`, mirror path). `RX_VK_RB_WHO` showed the same writers (`612a27de`, `6214a97b`, `0b4780da` for R2; `6ccba775`, `abf896d5`, `bc3ae540` for R3; `435a0138` / `59207944` for R1) and is how the ranges were found, but the code does not name them |
| staging | `rbring_stage_bind` queues {mirror buffer, offset, size} (no write-back is queued for it); `rbring_flush` at the end of the submission (`memory_end_submission`, before the write-backs) emits one barrier, `vkCmdCopyBuffer` per source buffer into the ring, one barrier to the host. Entry = {seq, tag = frames presented so far, timeline value of the submission, offset} |
| copy size | R1: the first 16 bytes (the CPU reads words 0 and 3 of the 0x1040 block); R2: 0x40; R3: 0x2000 |
| serve | `rbring_apply` (at the end of `invalidate`, after everything else, so no write-back can land between the answer and the CPU's load): `pick` = the newest entry with `seq > applied` and `tag + lag <= c`; written to guest memory with `host->gpu_write`. The CPU's own stores after the read (the zero it puts back) are never undone: an entry is applied once |
| not done | an entry whose submission the GPU has not finished waits (`HL_RB_LAG_WAIT_MS`, 200); with `L = 2` it never did in the runs (counted in the log) |
| quiet ranges | a learned range with **no** written binding over it (exact or overlapping) after 8 reads is answered with no wait, nothing applied. A written binding that overlaps a learned range without being exactly it, or an exact one that went to guest memory directly (no mirror), marks the range *not served*: it keeps the old path |
| ring | 4 MB of host memory, 8 entries an address, 1 MB staged by a submission at most; an entry the ring went round over is never handed out (`intact`) |

`c` is `presents_now` (frames presented by the replay); the replay is synchronous on the game's submitting thread by default, so it is the game's
frame count. The tag is read at the end of the submission, so a result written in frame `f` has tag `f - 1` (before its present) and is `c - tag = 1`
old at the read of frame `f + 1`, which is why lag 1 reproduces the old path and lag 2 is one frame older.

## 2. Switches

| switch | default | what |
|---|---|---|
| `HL_RB_LAG` | off | `1..6`: the ring answers the reads, `L` frames old, no wait, no write-back of staged ranges |
| `HL_RB_LAG_CHECK` | 0 | `1`: the old path runs unchanged; per class, a log line every 3000 reads: reads with a new entry and how many equal / differ from the waited value, ring entry not done, ring overwritten, reads with no new entry (waited value zero or not), the age of the newest entry (frames 0/1/2/3+); and the quiet ranges' changes by the old waits |
| `HL_RB_LAG_WAIT_MS` | 200 | how long a read waits for an entry the GPU has not finished |
| `HL_RB_LAG_LOG` | 0 | `1`: a log row a frame, `rbring frame <c> hash <sum of the reads' hashes> reads <n> r1 .. r2 .. r3 ..` (the hash of class, address and the bytes handed to the game; in check mode of what the waits gave); `2`: also the learned ranges that are not served |
| `HL_RB_R2`, `HL_RB_R3` (READB2) | 0 | still there for the ranges the ring does not serve: `=2` the exact-range refresh (R2) / no refresh (R3), proven equal |

## 3. What is left on the old path

The 0x3980 culling results (R4), the 0x1b580 / 0x9b040 ranges, and any read of a range written by a binding that is not exactly it or that went
to guest memory directly: those keep their waits (`RX_VK_RB_PROF` shows them: 0.01-0.06 ms a frame each in the Market). Not touched here.

## 4. Results

### 4.1 CHECK at lag 1: the ring against the waited values (main PC, 110 s runs, `market-spin`)

`HL_RB_LAG=1 HL_RB_LAG_CHECK=1` (the old path runs unchanged; at each read the ring's candidate is compared with the value the waits left):

| class | reads with a new entry | equal to the waited value | differ | ring entry not done / overwritten |
|---|---:|---:|---:|---:|
| R1 (meter, 0x1040 / 0xc0) | 16,459 | 16,459 | **0** | 0 / 0 |
| R2 (0x40 slots) | 69,436 | 69,436 | **0** | 0 / 0 |
| R3 (0x2000 pair) | 3,000 | 3,000 | **0** | 0 / 0 |

Reads with no new entry (already applied, or the ring has nothing): R2 77,564, **all** with a waited value of zero; the quiet ranges (no program
writes them) read 0 changes by the old waits in 3,000-read windows (0x40 and 0x2000 sizes). The newest entry's age at a read is 1 frame in every read that
has a new entry: a result written in frame f is read in frame f+1, so lag 2 is one frame older than the old path, as designed.

At **lag 2** the same check gives R2 86% equal and 14% different per new entry (the older frame's value: R2 values change from frame to frame), as
expected of a one-frame-older read; that difference is the cost of not waiting and is what the pictures below look at.

### 4.2 Determinism

What makes a read depend on the GPU's timing in the old path is *which write-back has landed* when the CPU reads (and, in the plain late read of
READB2 `=3`, whether it has landed at all). With the ring the answer is a function of the frame count and of the entries tagged at least `L` frames
earlier; the only timing-dependent branch is "the entry's submission is not finished", which waits (and never happens at `L = 2`: 0 waits and 0 reads
not done in every serve run, counted in the log). Together with 4.1 (at lag 1 the ring equals the waited value in every one of ~90,000 reads), the lag 2
value of a frame is the old path's value of the frame before: a fixed one-frame delay of a stream that is itself exact.

The log row `rbring frame <c> hash ... r1 .. r2 .. r3 ..` (`HL_RB_LAG_LOG=1`) is there for the run-to-run comparison, and it was tried: `hyrule-idle` from
`market-mid`, two serve runs and two old-path control runs. **It does not prove anything here, and this is said plainly:** the game's own state is not
the same from one run to the next (the wall-clock-driven clouds, water and NPCs make the meter's sums differ from the first frame that has any:
the serve runs' sequences differ from their first meter read, 2,044 and 5,606 distinct frame hashes; the old-path control differs just as early; the
first 9,000 rows that look equal are the title screen, where only R3's all-zero reads exist). A run-to-run comparison needs a scene whose game state is
frame-deterministic; none of the harness scenes is. The property that is checkable and was checked is the one above (no timing-dependent branch taken).
For the owner's hand test: with `HL_RB_LAG=2 HL_RB_LAG_LOG=1`, a still scene must give the same `hash` in consecutive rows once the scene is static, run after run.

### 4.3 CHECK at lag 1 in the other scenes (`market-spin` from each save, 110 s)

| scene | R1 new / differ | R2 new / differ | R3 new / differ | quiet ranges read / changed by the old waits |
|---|---:|---:|---:|---:|
| Kakariko | 18,058 / 8 | 9,765 / 0 | 4,500 / 3 | 183,000 / 0 |
| Lost Woods | 15,747 / 2 | 5,999 / 1 | 4,500 / 0 | 66,000 / 0 |
| inside the Deku Tree | 12,000 / 12 | 6,000 / 0 | 6,000 / 9 | 57,000 / 0 |

The 35 differences in 78,000 reads (0.04%) are all of one kind: the waited value is the one of a dispatch recorded **in the same frame** as the read
(tag = c, before the frame's present), which lag 1 (`tag + 1 <= c`) does not take, so the ring hands the previous one (rows such as "waited 3a5412fc, ring
39d41300, entry tag 11515" at frame 11516, then the waited value one frame ahead again). That is a timing the old path's wait made visible (the
replay had already recorded the next dispatch); at lag 2 it cannot happen. Nothing was "not done" or overwritten in any run.

### 4.4 What the game is handed at lag 2 (serve mode)

The age in frames of the entries applied (`rbring R1: ... age 1/2/3/4+`): R1 **0 / 2,400 / 0 / 0** in every window of 3,000 reads (100% exactly 2
frames old), 0 waited for the GPU, 0 not done in time, in every serve run (`market-spin`, 5 runs; the zone run below). Waits left in the Market, from
`RX_VK_RB_PROF` (ms a frame, sum over the ranges): old path 9.6 (one window of 4.7 from a load), ring 3.7 with 1.9 of it a load window; what remains
is the 0x3980 culling results (0.2) and a few 0x40 slots written by other bindings. The meter's rings: 1.5-1.9 ms a frame of wait (the unused ring A,
read at 0x113c88b000, and the 0xc0 block) gone.

### 4.5 Pictures (`RX_VK_SHOTS`, `market-spin` from each save, off and `HL_RB_LAG=2`, same options; 53-59 shots a run)

Market (day), Kakariko (day), Lost Woods (dark, the meter adapts from black), inside the Deku Tree (dark interior): by eye no flash, no tint, no
black or white frame beyond the load screens (mean luma of every shot, off / on: Market 14.8-131 / 14.1-127, Kakariko 17.7-136 / 14.0-133, Lost Woods
14.0-108 / 14.0-108, Deku Tree 13.4-107 / 14.1-107; 0 shots above 230). The camera spin makes the shots differ from run to run, so they are not
compared pixel by pixel. Shots: `E:\OOT\scratch-step1\shots2` (not in the repository).

### 4.6 Zone changes

The title-to-save load (the "Charger" dialog and the zone load that follows) happens in every run above with the ring on, and worked. A scripted walk from
`market-mid` (12 legs of 3 s forward) stayed inside the Market: no zone change was obtained from a script here. **To be tried by hand**: Market gate to
Hyrule Field and back, Kokiri Forest to Lost Woods, a door of Kakariko, with `HL_RB_LAG=2`: the ring keeps its entries across loads (frame counts only
go up), a range that is learned again after a load starts on the old path and is served after 8 reads (quiet) or its first staged write, so the first
frames of a new zone read as before.

### 4.7 Speed (main PC, RTX 5070, SPEED lease priority 2, `market-spin` from `market-mid`, slot a with `dlss=0 upscaling=0 resolution=1 hdr=0 vsync=0 frame_rate_limit=5`, x3 interleaved)

Build `step1d`, one exe. "Render resolution: 100% chosen, drawn at 100% with Upscaler 0" in every run. fps of the run (it includes the first 15 s load and the camera turn;
a sanity figure) and the p50 frame time, rounds interleaved off / `HL_RB_LAG=2` / `HL_RB_LAG=2 HL_RB_R2=2 HL_RB_R3=2` ("cut"):

| round | off fps (p50 ms) | `HL_RB_LAG=2` | cut |
|---|---|---|---|
| 1 | 72.7 (11.7) | 82.7 (11.0) | 70.2 (12.1) |
| 2 | 79.2 (11.5) | 86.5 (10.7) | 78.5 (10.9) |
| 3 | 80.2 (11.5) | 87.3 (10.7) | 86.8 (10.7) |
| mean | **77.4** (11.57) | **85.5** (10.8), **+10%** | 78.5 (11.2) |

Reading: `HL_RB_LAG=2` is above all three baseline runs in each round and its p50 and p95 frame times are lower (p95 16.9 / 15.7 / 15.3 against 17.8 / 17.1 / 16.8);
the readback wait per call falls from 0.035-0.039 to 0.010 ms. The "cut" arm (READB2's refresh cut for what the ring does not serve, which is almost nothing
here) is inside the noise and not different from off in the mean: a slow first run (70.2, 78.5, then 86.8), the machine warming over the series (every arm
rises from round 1 to round 3, off too). It was not repeated; the 5070 is GPU-idle in this scene (3.5-4.4 ms of GPU a frame), so a gain from fewer refresh/write-back
bytes is for the 3050 (variants in `C:\OOTuilds\step1`, ready; the 3050's own session runs them).


## 5. Not done / open

- **Run-to-run identical hash sequences** (4.2) could not be shown: the game's own state differs between runs from the first meter read. Needs a scene with a
  frame-deterministic game state, or the owner's hand test.
- **Zone changes inside the game** were not exercised (4.6): by hand. The load from the title is.
- **Entries in the same frame as the read** (4.3) are not handed out at lag 1; at lag 2 the question does not arise.
- Ranges the ring cannot serve (a binding that is not exactly the range, or one that went to guest memory directly: counted as `st_direct`, not logged
  yet) keep the old wait. R4 (0x3980), R5 and the big ranges are the next classes.
- `tests/test_parallel_rb_refresh.py` does not build its fixture since READB2's refresh-check code entered `vk_memory.cpp` (it cuts the region that now has
  `rbclass_*`, `texture_frame_no`, `std::deque` in it); it fails on `refactor/readb2` too. The fixture needs those stubs. `nx2/test_nup_*` fail as always here.
- The old `HL_EXPO_GPU` ring is kept for A/B; with `HL_RB_LAG` set it is not reached for the ranges the unified ring serves. It can go once `HL_RB_LAG`
  is the default.

## 6. Hand-over

- Branch `refactor/step1` (worktree `E:\OOT\HeyListen-wt\step1`), 10 commits over `refactor/expo1`: the merge of `refactor/expo1` and `refactor/readb2` (only the generated switch lists
  conflicted), `HL_RB_LAG` (`rb_ring.h`, `vk_rbring.cpp`, hooks in `vk_memory.cpp` / `vk_replay.cpp`), the quiet ranges, the 0xc0 class, diagnostics, this note.
  Nothing pushed or merged.
- Builds: `E:\OOT\HeyListen\build\s5\bin\step1d` (main PC), `C:\OOT\builds\step1` on the PC 3050 (`HeyListen.exe`, `hookshot_vk.dll`, `HeyListen.map`). One exe, variants
  by environment: off (nothing set); `HL_RB_LAG=2`; `HL_RB_LAG=2 HL_RB_R2=2 HL_RB_R3=2` (READB2's refresh cut for what the ring does not serve); the check
  `HL_RB_LAG=1 HL_RB_LAG_CHECK=1` (0 differences expected, lines `rbring check ...` in the log); `RX_VK_RB_PROF=1` for the waits by range.
- Scripts and logs: `E:\OOT\scratch-step1` (run1.sh, det*.sh, pics2.sh, speed.sh, cmp.py, rbp.py, aggr.py); runs `build/s5/step1-*` in the main checkout.
- Next: zone changes by hand; R4/R5 into the same ring; then `HL_RB_LAG=2` on by default if the 3050's numbers and the owner's play agree.
