# EXPO1: the exposure meter that stopped adapting (2026-11-23)

Owner: EXPO1, branch `fix/expo1` (from main `2ed3bab9c`). Code: `engine/gpu/vk/rb_ring.h` (`Standing`, `stage`, `served`,
`settle_at`), `engine/gpu/vk/vk_rbring.cpp` (`rbring_stage_bind`, `rbring_flush`, `rbring_serves`), the hold in `vk_replay.cpp`
`invalidate()`, the diagnostic in `vk_draw.cpp`. Test: `engine/tests/test_rb_ring.py`.

## What the owner saw

After the shortcut from Zora's Domain to Lake Hylia the daytime picture was too bright and washed out. In the logs the exposure meter's
hold (`RX_VK_METER_HOLD`) said `exposure sums: 2000 reads, 2000 of them given the last result again` in every window from some point
of the session to its end: the game's CPU was handed the same sums over and over, so its exposure adaptation stood still at the value of
the zone where it froze.

## Root cause

`HL_RB_LAG` (STEP1's readback ring, `docs/native-engine/readback-ring.md`; on by default since `31a80e384`, 22 Nov 00:04) answers the
CPU's reads of the GPU results it serves from a ring of its own, and so leaves out the game's write-back of those ranges: a program's
binding written in a mirror is *staged* (`rbring_stage_bind` returns true, `gpu_buffer_bind` pushes no write-back), the end of the
submission copies it into the ring.

A range is served only while every exact written binding of it was staged and nothing else writes into it (`served`: not `tainted`,
`writes - first_stage == staged`). One written binding that went to guest memory directly (its pool had no mirror at that moment: a pool
made or given back around a zone change, a binding in a parallel segment before its mirror exists, `vk_memory.cpp` `gpu_buffer_bind`)
counts in `writes` and not in `staged`, and the range drops to the old path for good; an overlapping written binding (`tainted`) does
the same. That part was intended.

The bug: `rbring_stage_bind` did not look at that standing. It kept staging every later binding of the range, so its write-backs stayed
left out while its reads had gone to the old path. The old path reads guest memory, where nothing but the CPU's own stores landed any
more: OoT's meter stores zero after each read, so every later read found zero. `RX_VK_METER_HOLD` (a zero read is given the last
result) then handed back the sums of the read before the drop at every read, for the rest of the session. The ring's own per-class
counters show the same: once the meter froze, the R1 line counts one served read a frame instead of five (the meter's blocks no
longer go through `rbring_apply`).

The same flaw applied to the R2 (0x40) and R3 (0x2000) ranges the ring learns: a range dropped to the old path read stale data from
then on. Nothing else was looked at for those here.

## Fix

- `rb_ring::stage` decides whether a written binding is staged. A tainted range never is: its write-backs go on, the old path reads them.
- A range whose exact writes went to guest memory (with their write-back) is *taken over again* by its next binding in a mirror: the
  staged count is re-based (`first_stage`), and the range is answered by the ring only once the GPU has completed the submission that
  staged it (`settle`, set by `rbring_flush` via `settle_at`, checked in `rbring_serves` against `vk_timeline_completed()`), so no
  write-back of an earlier binding can land in guest memory after one of the ring's answers. Its older ring entries are marked applied
  (none of them is handed out over the newer values the old path gave meanwhile). Until then the old path answers, and it now has the
  write-back of that direct binding to read.
- `HL_RB_LAG_KEEP=0` is the old behaviour, for A/B.
- Logs: the R1/R2/R3 lines add "ranges taken over again" and "written bindings of tainted ranges given their write-back"; a take-over
  is logged (every 200th, all with `HL_METER_LOG`); the hold says once a streak when a block has been given the last result 600 times
  in a row (`vk: exposure sums: block ... read zero 600 times in a row`).
- Diagnostic `HL_METER_LOG=<rows>` (off by default): per frame, the meter programs' clears and dispatches by block and buffer; each
  meter read (at its first reads, when its held / fresh state changes, and every 300 reads) with the ring's standing of the range and its
  entries; every R1 range of the ring every 3000 reads.

## Evidence

### Owner sessions (logs as they are; `held` = reads given the last result again)

| session | ring | 2000-read windows | fully held (>= 1990) | held reads |
|---|---|---:|---:|---:|
| owner-c6 (20 Nov) | off | 32 | 0 | 7.2% |
| owner-ls1553 (20 Nov) | off | 51 | 0 | 0.1% |
| owner-rc (20 Nov) | off | 49 | 0 | 0.1% |
| owner-stutter (21 Nov) | off | 84 | 0 | 9.3% |
| owner-integ1 (22 Nov) | `HL_RB_LAG=2` | 72 | 37 | 52.8% |
| owner-mem1 (22 Nov) | 2 | 157 | 78 | 53.6% |
| owner-integ3 (22 Nov) | 2 | 152 | 138 | 90.9% |
| owner-acc1 (22 Nov) | 2 | 281 | 202 | 73.2% |
| integ4-owner (22 Nov) | 2 | 97 | 70 | 75.4% |
| integ4b-owner (23 Nov) | 2 | 217 | 67 | 33.4% |
| lift2-owner (23 Nov) | 2 | 227 | 194 | 86.3% |

Before the ring (`E:\OOT\slots\owner-*`, 20-21 Nov) no window is fully held in any session; Hyrule Field alone shows a few hundred held
reads a window (up to ~1000, one of the two blocks), and they go back to 0 in the next zone. Every session with the ring on freezes for
good. In the frozen part the ring's R1 line counts one served read a frame instead of five (`integ4-owner`: `3000 reads` every 600
presents before, every 3000 after): the meter's blocks had left the ring for the old path.

### Scripted warp tour (market save, `kokiri-idle`, `!cheat 1001` / `!cheat 1003` at play+10/40/70/100 s, 230 s, slot runs, build `expo1s` / `expo1t` = this branch + `HL_RB_TEST_DIRECT`)

`HL_METER_LOG=6000` in every run. Before = `HL_RB_LAG_KEEP=0` (the old staging), after = the default.

| run | arm | held-share series (2000-read windows) | held reads | meter frozen (`600 times in a row`) | ranges taken over again |
|---|---|---|---:|---|---:|
| ex1-b1 | before | 3 12 25 0 0 41 0 0 0 | 0.5% | no | - |
| ex1-b2 | before | 3 0 0 44 0 12 0 0 38 0 0 0 0 | 0.4% | no | - |
| ex1-b3 | before | 3 424 **2000 2000** | 55.3% | yes, at the zone change into Hyrule Field (frame 10806) | - |
| ex1-t0 | before + injected direct write at frame 13000 | 804 1000 1205 2000 | 62.6% | yes, at the save's load (frame 9001), before the injection | - |
| ex1-t2 | before + injection | 830 999 1000 | 47.1% | yes, at the save's load (frame 6647) | - |
| ex1-a1 | after | 3 0 0 33 0 12 0 0 35 0 0 0 0 | 0.3% | no | 4 |
| ex1-a2 | after | 3 11 12 0 11 | 0.4% | no | 4 |
| ex1-a3 | after (20 fps: two games and a build at once) | 24 | 1.2% | no | 8 |
| ex1-t1 | after + injection | 13 15 24 2 | 0.7% | no | 11 |
| ex1-t3 | after + injection | 5 25 0 12 | 0.5% | no | 15 |

(1000 of 2000 in a frozen run is the whole meter: the other block had never read a non-zero sum, so the hold has nothing to give it
and does not count it.) The freeze came on its own in 3 of the 5 "before" runs; with the fix 0 of 5, while every one of them took the
meter's blocks over again at the zone changes (the trigger happens in every run; only its effect is gone).

What `HL_METER_LOG` showed at the freeze (`ex1-b3`, frame 10805-10807, the zone change into Hyrule Field):
- the meter program's block was bound in the pool's **guest import** (`meter work frame 10805: 435a0138b9013040 -> 0x113c88e000 (buffer
  0x24449981450+0x4000)`, the same buffer as at frame 7116, before the pool's first mirror existed); the next frame it was a new mirror
  (`0x2447441f6d0`): the pool had lost its mirror over the load and its first binding after it went to guest memory directly;
- the ring's standing went `writes 2073 first 1 staged 2071`: one exact write more than staged, `served 0`, "ring does not serve";
- from then on every read: `00000000 00000000 00000000 00000000 zero, HELD`, `writes` and `staged` both going up by one a frame (the
  later bindings staged again, their write-backs left out) for the rest of the run.

The injected direct write (`HL_RB_TEST_DIRECT=13000`, `ex1-t0`) shows the mechanism the other way round: the one read after it found real
sums (`frame 13001 0x113c88e000: 429062a0 42817555 42891bbc 4286c841 (... ring does not serve)`, the direct write's write-back), and every
read after that zero again. With the fix the same injection reads `taken over again by the ring ... 1 of its 3154 written bindings
went to guest memory (since the last staged one)` and the next reads are fresh sums served by the ring.

### Pictures (`RX_VK_SHOTS`, every 300 presents, mean luma 0-255 of a 96x54 grid; shots in `E:\OOT\scratch-expo1\shots`, not in the repository)

The Market by day after coming back from Hyrule Field (the last Market visit of the tour):
- after (a2, t1, t3): 62.7-66.5;
- frozen (b3: 89.3-93.1; t2: 83.5-85.2; t0 before its injection: 85.4-88.9, and 64.8-65.2 once the injected direct write had handed the
  CPU one fresh read): washed out, hazy sky, low contrast (`b3/vk_013200.png` against `t1/vk_013200.png`).
Hyrule Field and the first Market visit read the same in both arms (42-46 and 63-66): the frozen sums happened to be the Field's.

## Since when

The staging without a look at the range's standing came with `HL_RB_LAG` itself (`76f15e0f5`, 21 Nov 19:33, off by default; `044ad62fe`
the same evening only tolerated direct writes before the first staged one). It reached every build when the ring was turned on by
default: `31a80e384` (22 Nov 00:04, "readback ring (HL_RB_LAG=2) ... on by default"). The first owner session after it,
`owner-integ1` (22 Nov 05:09), already freezes; none of the sessions before (20-21 Nov) does.

## State at stop (2026-11-23 reboot)

Stopped at 12:32 for the owner's reboot of the main PC.

- **Done:** root cause (above), fix + test + switch docs (commit `9b2d7bfb6`), the test switch `HL_RB_TEST_DIRECT` (`3039e831c`),
  10 warp tours before/after with held shares and pictures (tables above), full test suite (495 passed; failures only the known
  `nx2/test_nup_*`, `test_renderer_new_handler`, and `test_switches_documented` before the docs were regenerated: it passes now).
  Builds: `E:\OOT\HeyListen\build\s5\bin\expo1s` (fix + diagnostics, = `9b2d7bfb6`) and `bin\expo1t` (+ `HL_RB_TEST_DIRECT`,
  = `3039e831c`). The vk dll of both was built without a play profile ("no play profile ... built as usual").
- **Not done (cut by the stop):**
  1. the speed A/B (brief step 4): `bash build/s5/ab_env.sh expo1 expo1t 3 hyrule-castle castle-move "HL_RB_LAG_KEEP=0" "-"` (one
     exe, the old staging against the fix; castle-move has no zone change, so expect equal: it checks that the fix costs nothing
     where nothing is taken over). It was queued as `env-expo1`, never granted; released.
  2. the dark-to-bright picture tour (the owner's case): run `ex1-d0` was killed in its load; nothing kept. Command (slot lease,
     priority 2, from `E:\OOT\scratch-expo1`; make the shot folders first, `RX_VK_SHOTS` needs them):
     `mkdir -p shots/{d0,d1,d2,d3}; S="RX_VK_SHOT_EVERY=150 HL_METER_LOG=6000 HL_RB_TEST_DIRECT=12500:4000"; WARPS="10:1019 50:1007 90:1019 130:1007" bash tourd.sh /e/OOT/HeyListen/build/s5/bin/expo1t ex1-d0 "HL_RB_LAG_KEEP=0 $S RX_VK_SHOTS=E:/OOT/scratch-expo1/shots/d0" ex1-d1 "$S RX_VK_SHOTS=E:/OOT/scratch-expo1/shots/d1"`
     (1019 = Inside the Deku Tree, 1007 = Lake Hylia; then `python -I held.py <run>/game_log.txt` and
     `powershell -File luma.ps1 -Dir shots/d0`).
  3. this note's last paragraphs (the A/B numbers, the dark-to-bright pictures) and the final report.
- **Leases and scripts at the stop:** slot lease `20261123-121223-expo1-slot-4072` (slot a, run `ex1-d0`) and queued speed ticket
  `20261123-113902-env-expo1-speed-22f5`: both released. Scripts in `E:\OOT\scratch-expo1`: `tour.sh` / `tourd.sh` (warp tours on a
  slot lease, RAM-retry on runguard exit 12), `build.sh <name>` (build lease priority 1 then mkg.ps1), `held.py`, `luma.ps1`,
  `series.py`. All stopped; slot a's exe and dll put back from `slotbak-a`; no game of mine running.
- **First after the reboot:** `bash build/s5/machine.sh status` (main checkout), then item 1 (speed A/B), then item 2 (pictures).

### Not verified here

- The owner's path (Zora's Domain to Lake Hylia) itself; a dark-to-bright tour (Inside the Deku Tree / Lake Hylia) was queued (see
  the speed and pictures notes below when filled).
- R2 / R3 ranges dropped to the old path: they had the same flaw and the same fix; what reading stale R2 / R3 data did in the game was
  not looked at (#93, night flicker, is not shown to be this).
