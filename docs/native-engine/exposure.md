# EXPO1: the exposure meter (readback R1), design note

Owner: EXPO1, branch `refactor/expo1` (from `refactor/step0`), 2026-11-21. Step 1 of `refactor-plan.md`, first module. Ledger row: R1 in
`readback-ledger.md`. This note holds the map of what the game does with the meter (done), the design (done, and it differs from the brief's
first idea, see the verdict), the check and measurement plans, and a hand-over. **Nothing is implemented or measured yet**; what is left is
listed at the end.

## Verdict first

The brief's goal was "measure, adapt and use all stay on the GPU". The map says that cannot be done alone: **the adapted values are read by
the game's CPU in at least a dozen places** (light, fog and effect parameters computed on the CPU and uploaded), so a GPU-only adaptation
would leave the CPU with nothing to read. And the GPU itself does not use the adapted exposure: the GPU-side word the engine's old
diagnostic watches (`c3+0x38` of the final pass, `RX_VK_EXPOSURE_LOG`) stays 1.0 in the Market (re-checked today, 3,752 rows, all 1) and is
not the meter's output. What the meter feeds is CPU code.

So the right shape is refactor-plan option 3 plus a clean producer: **the CPU keeps its adaptation (the translated function stays, bit
exact by construction) but it is handed the sums of an older frame that is already finished, from an engine-owned ring, so it never waits
for the GPU**. The lag is chosen and fixed (not "whatever happens to be done"). The GPU never has to add into memory the CPU resets, which
removes the doubled-sum pulses (white flashes, #93) at their source and makes `RX_VK_METER_ZERO / HOLD / STRICT_SLOW_MS` unnecessary
with the switch on. Section "Design" has it.

## 1. Map: what `f_80357060` does (guest address; plain words, no translated code)

### 1.1 Who calls it, and what it is

- Callers (seven, one per module job of the same shape): `f_80074b60`, `f_80076e60`, `f_8007c120` (the one the Market uses; case at
  `0x8007c9e8`), `f_8034dac0`, `f_80350b40`, `f_80352340`, `f_831f25a0`. Each does the same three things, around the call:
  1. store the module's own value `[module + 0xde4]` into the meter object `[meter + 0x37c]` (the module's brightness-scale input);
  2. call the helper with `w1` = bit 0 of `[[module + 0x138] + 0x5f0] + 0x88` (the "enabled" bit; clear: the helper returns at once);
  3. copy the object's third average `[meter + 0x36c]` to `[module + 0x1530]`.
  The meter object is `[module + 0xc00]`. The module object is also reachable globally as `[[0x84c9e740]] + 0x1e8`; this is how the readers
  in 1.4 find `[module + 0x1530]`.
- It is a method on the meter object (about 900 instructions). The three GPU sums it reads are in three rings of 0x1040-byte blocks; its
  invalidates are the five return addresses the ledger names: `0x80357df8` (ring A), `0x80357c80` (B), `0x80357d58` (C), and
  `0x80357cc8`, `0x80357d10` (the 0xc0 ring, see 1.3). Its flushes (`0x390`) follow every CPU write.

### 1.2 The three chains (sum read, normalise, moving average)

Each chain reads one word of a GPU-written 0x1040 block (after a `nvnMemoryPoolInvalidateMappedRange` on the block), stores zero back into
the block (A and C: the first word; B: the first four words), flushes it, turns the word into a number `v`, and runs it through a moving
average. Every chain reads and zeroes **every call, unconditionally** (A only when the mode word `[0x398]` is 0; the other values give a
constant 1.0 or 0.0 and no read): the zero is part of the GPU/CPU protocol, so a frame that does not read still has to reset.

| chain | object ring | block word read | `v` | held-value word | hold flag in `[0x3a0]` | average stored at |
|---|---|---|---|---|---|---|
| A | `meter+0x30` (data at `+0x48`, offset in `[+0x40]`) | word 0 (float) | `1 + (k / 256) * (sum - 256)` with `k = [0x310]` | `[0x380]` | bit 3 | `[0x32c]` |
| B | `meter+0x1d8` | word 3 (float, at `+0xc`) | `clamp01((sum / 256 - lo) / (hi - lo))` with `lo = [0x308]`, `hi = [0x30c]`; `0` when `hi <= lo`; a NaN sum reads as `lo` | `[0x388]` | bit 5 | `[0x34c]` |
| C | `meter+0x268` | word 0 (float) | `sum / 256` | `[0x384]` | bit 4 | `[0x36c]` |

- A hold flag set (by other code, one shot) replaces the fresh `v` by the held word, and the flag is cleared when the average has taken it
  (A and B clear it after the average, C when it picks the value). The read, zero and flush still happen.
- The atomic request word `[0x3a4]` (bit 0 "flagged", bit 1 its echo one call later) gates the averages of A and B: A averages when the word
  has no bit 0 or when A is held; B averages when neither bit is set or when B is held; C always averages. The word is a two-call delay
  line (bit 0 becomes bit 1 on the next call, bit 1 goes on the one after). Who sets bit 0 is not mapped.
- A byte flag `[0x3b9] == 1` makes the whole call return at once after clearing itself (no read, no zero for that frame: the GPU's next sum
  is then added to an unreset block, which is the doubled-sum pulse; `RX_VK_METER_ZERO` exists for this).
- The moving average: a ring of floats `[0x320]` (capacity `[0x318]`), a running sum `[0x328]`, a cursor byte `[0x330]` and a count byte
  `[0x331]`. The window length is a game parameter (three small accessor functions at `0x831cd830`, `0x831cdb60`, `0x831cdcb0`, one per
  chain, reading the parameter object `[[meter + 0x390] + 0x150]`). Per update: count = count + 1; when the window length is smaller than
  that the count is cut to the window length and the oldest element is **subtracted** from the running sum; then the new `v` is stored at the
  cursor and **added** (in single precision, in this order, so the running sum drifts exactly as the translated code does), the cursor
  advances modulo the window length, the average is `sum / (float) count`. Chains B and C use the next 0x20 / 0x40 bytes after A's state
  (B: `+0x338..+0x34c`, C: `+0x358..+0x36c`). Any native version has to repeat this order of operations to stay bit equal.
- **The window is short and game-configured; the average is already a lagged value**: even today the CPU never uses the newest sum alone.

### 1.3 What the helper writes for the GPU (not the meter's results)

After the three chains it fills two CPU-to-GPU blocks. They contain nothing derived from the sums:
- the 0xc0 block of the ring at `meter+0xc0` (the ledger's "0xc0 companion"): 16 bytes of zero, two copies of one four-float value taken from
  a game-state getter at `0x834a5280`, and 0x60 zero bytes; invalidated (the pool is CPU-cached) before and flushed after;
- the 0x40-byte constant block of the ring at `meter+0x150` (slot toggled by a bit of `[0x19a]`): the sun direction (negated, normalised,
  zero when degenerate), the value `[0x37c]`, and a handful of lighting values copied or clamped from the game's environment state; flushed.

### 1.4 Who reads the adapted values on the CPU (static scan of the image, loads at the object's offsets, 2026-11-21)

- `[module + 0x1530]` (the third average) is loaded in nine functions: `0x803a8340` (lerps two light parameters by it, result stored in the
  light state: a CPU-computed light value), `0x803e2a70` and `0x803e5d90` (branches on it against 0 / 1 / 0.5), `0x80477aa0`, `0x80493480`,
  `0x807fc220`, `0x80b49ee0`, `0x834378d0`, `0x8343a440`.
- `[meter + 0x32c]` and `[meter + 0x34c]` (the first and second averages) are loaded by `0x803d94b0` (a property getter that returns
  `base + a * avg1 + b * avg2` with `base`, `a`, `b` at `[meter + 0x2f8 / 0x300 / 0x304]`: scripted light/effect parameters) and by a few
  other functions that also load `[module + 0xc00]` (`0x80660320`, `0x80bd5470` for avg1; `0x8028ca00`, `0x81033f20`, `0x82dd5350`,
  `0x82e17b20`, `0x82e21a90` for avg2); `0x80356530` stores a time-of-day interpolated value into `[meter + 0x32c]` itself (the game
  overrides the average for scripted lighting). Their meaning is not mapped one by one.
- Nothing else was found reading the three rings (only the helper's invalidate sites; a scan for the rings' addresses was not made).

So: the producer is the GPU, the **consumers are CPU functions**. Strategy 1 of the plan ("same side") does not apply to this meter.

## 2. Design

### 2.1 What is removed and what stays

Stays, unchanged and translated: the helper and every reader above (so the adaptation is the game's own, bit for bit, on whatever sums it is
given). Changes: **which sums the invalidate at the three read sites returns, and when**. Today the answer is "the GPU's current content of
the block, after waiting for the previous frame's heavy submission and its write-back"; the zero the CPU stores races the GPU's next add.

### 2.2 The feed (native module `hl::expo`, vk-side hooks)

1. **Producer.** Where the two programs `435a0138b9013040` and `592079442d7129da` are dispatched (`vk_draw.cpp`, where `meter_out` already
   records their block and the `RX_VK_METER_ZERO` fill already clears its first 16 bytes), the engine, right after the dispatch, copies
   those 16 bytes with `vkCmdCopyBuffer` into a slot of a small host-visible ring of its own (slot = the block's guest address, the
   dispatch's sequence number, the 16 bytes). The slot is complete when the queue's timeline reaches the value of the submission that holds
   the copy (known at submit time; read with `vkGetSemaphoreCounterValue`, never waited on in the normal path).
2. **Consumer.** At the invalidate of a meter block (`size == 0x1040 && memory_is_meter(...)`, `vk_replay.cpp invalidate()`), with
   `HL_EXPO_GPU=1`, the engine does not wait for any write-back (the whole `ticket / recorded / late fix / strict` path is skipped for that
   range): it writes into the guest block the 16 bytes of the slot of that block whose sequence number is `L` dispatches before the newest
   one, `L = HL_EXPO_LAG` (default 2). It last, after everything else the invalidate does, so no write-back can land between the answer and
   the CPU's load. The CPU's later zero store is harmless: nothing accumulates into the guest block any more.
3. **No more accumulation or refresh for these blocks.** With the switch on, the meter blocks are left out of `memory_note_readback`
   (the refresh that copies the CPU's zero back into the mirror) and out of the write-back registration of the programs' output
   (`memory_note_gpu_write`), and the `RX_VK_METER_ZERO` fill stays (it is what makes a slot hold one frame's sums, not a running total).
   Saved GPU work: the meter's share of `rbrefresh` and `wb` (0.18 and a part of 0.47 ms on the 5070), plus the late-fix wait.
4. **Lag choice, `L = 2` (frames).** The game's CPU frame N reads, by today's protocol, the sums of frame N-1 after waiting for the GPU. A
   slot `L` dispatches old is complete as soon as the GPU is not more than `L - 1` frames behind the CPU: on the 3050, whose GPU runs one
   frame behind, `L = 2` finds it done without waiting (the wait that today costs ~14 ms). `L` counts the programs' dispatches (a block
   gets one per frame), so the lag is deterministic in frames and does not depend on timing; if a slot is not complete yet (GPU further behind
   than `L`), the read waits for it (bounded, `RX_VK_STRICT_BOUND_MS`), then reads. `L = 1` with the wait reproduces today's value exactly
   (this is the `_CHECK` baseline). A first-frame and zone-change rule is part of the module: before `L` slots exist the oldest one is
   given (or zeros, which the game's own first-frame handling knows).
5. **What it costs in picture terms**: the exposure and every parameter derived from it lag the camera by one extra frame (~16 ms at 60
   fps) beyond today's. The averages are windows of several frames (1.2), so the visible effect is a shift of the adaptation curve by one
   frame, not a different curve. This is the one thing that is not bit-equal; it is why `L = 1` exists as a check mode and why the module
   is off by default.

### 2.3 Rejected

- **GPU adaptation compute step** (the brief's design 2): would need the three moving averages reproduced bit-exactly on the GPU (float
  order of operations above is possible), and then the nine-plus CPU readers would still need the value on the CPU: another readback,
  one frame late, of a value the CPU already computes cheaply from three numbers. The ring above gives the CPU the same data with no GPU
  adaptation code and no risk of a second implementation of the game's logic drifting. Reconsider only if the game's lighting code that
  reads the averages is made native and moved to the GPU (step 2/3 of the plan).
- **A gate or answer on `f_80357060`** (native reimplementation of the helper): 900 instructions, many flags not mapped (1.2), no gain
  over keeping the translated helper and changing only what the invalidate returns.
- **Reading the guest block one frame late as the engine's `RX_VK_READBACK_LATE` does**: it gives zeros or the stale/doubled sums (the
  existing note in `readback.md`: "a rule for the meter's ranges has to be exact"); the ring is the exact version.

### 2.4 `_CHECK` mode

`HL_EXPO_GPU=2` (check): the old path runs as today (wait, read the guest block); in addition the ring slot at `L = 1` is read. At each
meter invalidate the two 16-byte values are compared bit for bit and counted; the first few mismatches are logged with the block, the
frame and both values. Expected: 0 mismatches (same GPU data, only another route). Then the translated adaptation's output
(`[meter + 0x32c/0x34c/0x36c]`) is logged every frame for runs at `L = 1`, `L = 2` and the old path, so the lag's effect on the averages is
a measured series, not a claim. Pictures: `RX_VK_SHOTS=E:/dir` in the Market (day), Kakariko at night, Lost Woods, and inside the Deku
Tree (dark interior) from `build/s5/savelib/*`, old path against `L = 2`, same save, same scripted input.

### 2.5 Measurement plan (when implemented)

`bash build/s5run.sh --slot <slot> --save build/s5/savelib/market-mid <tag> market-spin` with `dlss=0 upscaling=0 resolution=1 hdr=0
vsync=0` and no frame cap, under a SPEED lease (priority 2), x3 interleaved old / `L = 2`; read `RX_VK_RB_PROF` readback waits (the meter's
wait is the one named `0x113c88b000+0x1040`) and fps. 3050 variants: exe and dll in `C:/OOT/builds/expo1/` (the coordinator routes the
3050 runs; no game run over ssh).

## 3. What was verified today

- The map in section 1 is from the disassembly of the image's `f_80357060` (910 instructions), its seven callers and the nine plus readers
  (capstone over `image.bin`; no translated code is kept in the tree). Offsets, call relationships and what is computed are stated; the
  code is not.
- Two functional runs of `market-spin` on slot b (`expo1-diag1`, `expo1-diag2`, `RX_VK_EXPOSURE_LOG=2`): the old final-pass word reads 1
  in every one of its 3,752 rows; the first-read logs of `RX_VK_LOG_READBACK` list only the 0x2000 ring in their first 300 rows (they
  hold the log's cap), so the block contents were not captured. No speed number from these runs is used.

## 4. Not done, and why

- No code yet: not the producer copy, the consumer branch, the native ring, the switch, the check mode, nor the unit test
  (`engine/native/tests/expo_test.cpp`). A first implementation is about 250 lines (ring and lag rule in `engine/native/src/gfx`, a C header,
  the two vk hooks) and needs one build that includes the vk dll; the vk dll is part of the full tree build, which needs the build lease and
  a lift of the shared work folder. That build was not started: another agent was lifting in it while this was written, and a build is not
  to start while a speed lease is held or queued.
- Open questions the implementation must settle, in order: (1) which of the two programs writes which of the three 0x1040 blocks, and the
  slot size (16 bytes assumed from the existing zero fill, the CPU reads words 0 and 3 only); (2) whether the whole 0x1040 block or only its
  first 16 bytes are rebound by the programs (the write-back range today is the whole block); (3) that nothing else in the 0x1040 block is
  written by the GPU or read by the CPU (the game's code reads only words 0 and 3, per 1.2); (4) the first frames after a zone change
  (the translated side may set the byte `[0x3b9]` or the request word, which the ring must not fight).

## 5. Hand-over

- Branch `refactor/expo1`, worktree `E:\OOT\HeyListen-wt\expo1`, one commit: this note. Scratch: `E:\OOT\scratch-expo1` (disassembly
  helpers `dis_range.py`, `pat.py`, `pat2.py`, `pat3.py`, `ctx.py`; the two run folders are `build/s5/expo1-diag1` and `-diag2` in the main
  checkout). No lease held, no process left.
- Decision for the coordinator: **accept the fixed lag `L = 2` (one more frame of exposure latency, measured by `_CHECK`) as the way to
  remove the meter's wait**, or say the exposure must stay at exactly the old lag, in which case this readback cannot lose its wait on
  a GPU that runs a frame behind (the data of frame N-1 does not exist before the GPU finishes it).
- Next steps in order: slot run with `RX_VK_LOG_READBACK2` filtered to 0x1040 (add a size filter to the diagnostic or raise its cap) to
  settle open question 1-3; write `hl::expo` (ring, lag rule) with its unit test; vk hooks; `_CHECK` at `L = 1`; pictures; speed lease
  measurement; 3050 variants.

## 6. Implemented and measured (second session, 2026-11-21)

Decision taken by the coordinator: fixed lag `L = 2`. Code: `engine/gpu/vk/expo_feed.h` (the pure rules, tested by `tests/test_expo_feed.py`),
`vk_expo.cpp` (ring, producer, consumer, check), the producer call after the meter dispatch in `vk_draw.cpp`, the consumer branch in
`invalidate()` (`vk_replay.cpp`). Switch `HL_EXPO_GPU` (0 default, 1 on, 2 check), `HL_EXPO_LAG` (2), `HL_EXPO_WAIT_MS` (4), `HL_EXPO_LOG=<rows>`.
Diagnostics added (default off): `RX_VK_LOG_METER=<rows>`, `RX_VK_LOG_READBACK2_SIZE`, `RX_VK_LOG_READBACK2_ROWS`.

Open questions of section 4, answered by `RX_VK_LOG_METER` and the 0x1040 read logs:
1. Market: only `435a0138b9013040` runs, writing block `0x113c88e000` (the game's B ring; the CPU reads its word 3). Kakariko and Lost Woods: also
   `592079442d7129da`, writing `0x113c88b000` (ring A). The third block `0x113c891000` (C) is never written by a program in these scenes and reads 0.
2. Both programs bind the whole 0x1040 block at offset 0.
3. The program fills the whole block (about 1,028 non-zero words: the four sums, then a table); the CPU reads and zeroes only words 0-3 (B: reads word 3).
4. Zone change: not exercised by a scripted scene (the scenes started from saves); the ring keeps counting across loads (the two double-dispatch frames at
   the start of a run show as the only mismatches below). Left for a hand-played check.

`_CHECK` (`HL_EXPO_GPU=2`, uniform options, "Render resolution: 100% chosen, drawn at 100%"): Market 4,999 of 5,000 reads equal; Kakariko 6,994 of 7,000;
Lost Woods 6,991 of 7,000. Every mismatch row has the old path equal to the ring's **lag 2** slot: the replay had already recorded the next dispatch
(loading frames record two a frame, or the replay ran ahead), so "newest recorded" was one ahead of what the old wait returned. With `L = 2` that case
returns the same sums the old path read; nothing else differed (0 reads with the newest slot not finished). `L = 2` against the old path is one dispatch
older in all other frames (visible in the rows: lag2 equals the previous row's old value).

Pictures (`RX_VK_SHOTS`, mode 1, same options): Market day, Kakariko village (day: the save has no night), inside the Deku Tree (dark): all right, no
flash, no tint (shots in `E:\OOT\scratch-expo1\shots-m1`, `shotsd-*`). Lost Woods and a night scene were checked in mode 2 only (old path's pictures).
Served reads: 3,000 reads, 0 took an older slot, at most 1 found nothing (the first read).

Speed (main PC, SPEED lease priority 2, slot a, `market-spin` on `market-mid`, dlss 0, upscaling 0, resolution 100%, hdr 0, vsync off, no cap
(`frame_rate_limit=5`), x3 interleaved, off then on): fps off 70.6 / 81.1 / 81.2 (mean 77.6), on 78.9 / 84.5 / 76.8 (mean 80.1): **inside the noise
(+3%)**. The calls that wait (`readbacks of GPU results` line) stay at 270-500 of 3,000 at 0.1-0.6 ms: the other rings (0x2000, 0x40) still wait. This
is the expected result on the 5070 (the meter waits 0.2 ms there); the gain, if any, is for the 3050, where the meter's wait is part of the ~14 ms: not
measured here (3050 runs go through its own session). The binaries are in `C:/OOT/builds/expo1/` on the 3050 (`HeyListen.exe`, `hookshot_vk.dll`,
`HeyListen.map`): the same exe runs both variants, `HL_EXPO_GPU=0` and `HL_EXPO_GPU=1`.
Default stays **off**: the meter is one of several waiting ranges, so the frame gains only once R2 and R3 (0x40 ring, 0x2000 pair) are also off the wait.
