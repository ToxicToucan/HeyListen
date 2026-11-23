# READB2: the 0x40 ring (R2) and the 0x2000 pair (R3), design note

Owner: READB2, branch `refactor/readb2` (from `refactor/step0`), 2026-11-21. Step 1 of `refactor-plan.md`. Ledger rows R2 and R3 in
`readback-ledger.md`; the GPU cost of the readback protocol itself is `gpu-pass-cost.md` B3. The exposure meter (R1) is EXPO1's
(`refactor/expo1`, `exposure.md`) and is not touched here.

## Verdict first

1. **Producers and consumers are identified** (section 1). R2 and R3 are not one thing each:
   - R2 is a per-object *read-and-reset result word*: the game, for every object of a list, invalidates a 64-byte slot of a ring, reads one
     float (a sample count from the GPU), turns it into a 0..1 fraction stored on the object, and stores zero back. 63 calls a frame in the
     Market, 30 in Kakariko, 11 in Hyrule field. Only about half of them (35, 2, 1 a frame) hit a range any GPU program writes; the rest
     are slots nothing writes in the last two frames.
   - R3 is a two-slot ring of 0x2000 bytes, swapped each call, read by a per-object loop that takes the largest of a few result words per
     request and clears the request. It is all zeros in most frames (96% of Market reads, 68% of Kakariko, 100% of Hyrule field) and a few
     small integers otherwise.
2. **What each costs the GPU** (and not only the CPU's waits): the readback refresh (`rbrefresh`, 1.02 ms a frame on the PC 3050) copies,
   for every submission part, every page the CPU has read in the last 600 epochs from guest memory over the mirror. **R2's pages are 69 of
   the 89 pages of that set in the Market (78%), 36 of 55 in Kakariko (65%), 17 of 36 in Hyrule field (47%); R3's are 4 pages.** That is
   the lever that matters on a GPU-bound card, much more than the waits (`NO_READBACK_WAIT` is worth only +3% on the 3050).
3. **Decisions** (section 4):
   - **R3, refresh: out entirely** (option 2/1: nothing for the CPU to put in). The CHECK compared the refresh with the mirror 13,201 /
     17,401 / 46,801 times in the three scenes: **0 words differ** (54-192 million words compared). Exact.
   - **R2, refresh: the ranges the CPU read, not their pages.** The CHECK shows what the refresh really does for R2: it carries the CPU's
     reset (zero stored after the read) into the mirror, so a write-back does not put the old count back over it. **All of the differing words
     are inside the ranges the CPU read and none outside** (15,765 / 1,158 / 15 words, 0 elsewhere), so copying only those ranges brings the
     same words as copying the pages: equal by construction for the words that matter, 70 times fewer bytes (about 12 KB instead of
     280 KB a refresh in the Market).
   - **Waits (both): one frame late, kept as a separate part (`=3`), not recommended on yet.** The CHECK compared the value at the read's
     entry (what a no-wait read would give) with the value after the waits: R2 differs in 0.12 / 0.33 / 0.50% of reads, every difference
     being "nothing yet, then the result" (never a value turning to zero); R3 differs in 0.5-5.6% of reads once it carries data, with
     older results. That is one frame of extra latency on those reads, not provably harmless (section 4.3), and it does not shorten the
     frame while R1's wait for the same GPU completion stays (`readback.md`): it pays only together with EXPO1.
4. Measured effect and pictures: section 6 (filled in when measured).

## 1. How the producers were found

`RX_VK_RB_WHO=1` (logging only, default off; `vk_memory.cpp`, `rbwho_*`): every GPU-written buffer binding is kept with the program being
bound on its thread (`build_descriptors` sets it), every CPU read (`invalidate`) is matched against the writers of the current and the
previous 300-frame window, and the content the CPU sees after its waits is sampled. One line a window per read size: reads a frame,
reads with no writer, distinct ranges and pages, and each overlapping writer with the size of its binding.
Bare 1 MB guesses of the table entries would match too much; the writers below all bind exactly the size read.

Scenes: `market-spin` from `market-mid`, `kakariko-village`, `hyrule-field` (`build/s5/savelib`), 110 s each, main PC.

| class | reads a frame M / K / H | with no writer | distinct ranges / pages M / K / H | writer programs (binding size) |
|---|---|---|---|---|
| R2, size 0x40 | 63.4 / 30.4 / 11.4 | 28 / 28 / 10 | 188 / 69, 155 / 36, 136 / 17 | `612a27de13e44e79` (Market, 35 overlaps a frame; compute 1x1x1, 988 instructions, 4 stores, no atomics), `6214a97bec4cc0bf` (Kakariko 2, Hyrule 1; same shape), `0b4780da69a698b5` (0.33 a frame, vertex) |
| R3, size 0x2000 | 2.0 / 2.0 / 2.0 | 0 | 2 / 4 pages (`0x113c895000`, `0x113c897000`, one per frame by parity) | `6ccba775ea111ee9` (compute, 262 instructions, 8 stores), `abf896d5f74923a9` and `bc3ae540cf7abb61` (vertex programs with storage stores, 302 instructions each; Hyrule field only the first) |

The reads with no writer are slots no program wrote in the two windows: the game invalidates the slot of every flagged object whether or not
the GPU has anything for it. They cost no wait (no write-back is registered for them) but their pages are refreshed like the rest.

CPU readers (`HOS_RB_LR_LOG=1`, guest return addresses): R2 `0x80354174` inside `f_80354060` (module worker threads; one other site,
`0x807069dc`, reads 0x40 a few times in 3000 calls and was not looked at), reached from the module job `f_8007c120`. R3 `0x8034f658` inside
`f_8034f440` and `0x8007c4b8` (its caller in `f_8007c120`).

## 2. What the consumers do (read from the lifted code, plain words)

- **R2** (`f_80354060`): over the list of buffer objects of the module (count at +0xaa8 of the module object), for each one whose flags
  have bit 14: if its memory pool is CPU-cached it invalidates the slot `[ring base + (frame & mask) * stride]`, rounded out to 64 bytes;
  then reads the 32-bit float at the slot's first word, divides it by a per-object total (a multiple of a count kept on the object), clamps to
  0..1, stores it on the object (+0x7ec), and **stores zero into the slot's word**. A read-and-reset like the meter's, but on a plain store:
  the GPU programs do not add (no atomics), they write the count; the zero tells the next read "nothing was written". The consumers of the
  fraction were not followed (fades and glows of the objects are the guess).
- **R3** (`f_8034f440`, called from the module job): swaps the ring's parity, computes the new slot's address (0x2000 bytes at a
  stride), invalidates it when the pool is CPU-cached, then loops over the module's request list: for each object it takes the 16-bit slot
  index it was given (0xffff: none), reads the result words of that request, keeps the largest as a float on the object (+0x9c) and resets the
  index to none. The results are small integers (0x5a, 0xae); where nothing was requested the page is all zeros.

## 3. Options per the plan, and what the evidence says

| | same side (1) | compute on the CPU (2) | one frame late, deterministic (3) |
|---|---|---|---|
| R2 | the count is a sample count of the object's own geometry; a same-side version means a native module that owns the objects' list, their visibility fractions and everything that reads them: far beyond this step | not possible without the sample counts | the natural fit (a visibility fraction already a frame old); needs a deterministic form, see 4.3 |
| R3 | same: the consumers were not traced | no | same |

## 4. Decisions

### 4.1 Refresh (the GPU cost)

`memory_rb_refresh` (`vk_memory.cpp`) copies guest memory over the mirror, once per submission part, for every page read by `invalidate`
with at most 0x4000 bytes in the last 600 epochs. It exists for read-and-reset records (the meter, R2): the CPU's zero must be in the mirror
before the GPU's next writes land, or the write-back brings the old value back over it (doubled meter sums; for R2 a stale count over the
CPU's reset). Everything else in those pages is brought by the ordinary mirror sync.

- `HL_RB_R3=2`: R3's reads are not noted at all, so its four pages leave the set.
- `HL_RB_R2=2`: R2's reads are noted as exact ranges (`rb_exact`), not pages; the refresh copies those ranges (merged when they touch,
  and a range inside a page that is copied for another reason is absorbed: the merge now handles overlaps as well as neighbours). The
  held-back rule for recordings that join an open submission applies to them as to pages.
- Pages read by any other class (the meter's 0x1040 and 0xc0, the 0x3980 culling results) are untouched.

### 4.2 CHECK modes

`HL_RB_R2_CHECK=1`, `HL_RB_R3_CHECK=1` (diagnostics; the game sees what it saw without them, whatever `HL_RB_Rn` says):
- refresh: at every refresh the class's pages are copied twice more into a host buffer before the refresh copy, the mirror and guest
  memory as the GPU sees them; when the submission is done the two are compared word by word and the differing words counted, those
  inside the ranges the CPU read separately (`refresh check` log lines);
- late read: the value at the read's entry is kept and compared with the value after the waits (`late check` log lines).

Results (main PC, 110 s runs, `market-spin`, slot b, whole log lines in `build/s5/rb2-ck1-*`):

| | Market | Kakariko | Hyrule field |
|---|---|---|---|
| R3 refresh: refreshes, words compared, **differ** | 13,201, 54.1 M, **0** | 17,401, 71.3 M, **0** | 46,801, 191.7 M, **0** |
| R2 refresh: refreshes, words compared, differ, of those inside the read ranges | 13,201, 506.8 M, 15,765, **15,765** (all guest 0 over a mirror value) | 17,401, 86.9 M, 1,158, **1,158** | 46,801, 187.7 M, 15, **15** |
| R2 late: reads, differ (all "nothing, then a value") | 147,000, 175 (0.12%) | 81,000, 268 (0.33%) | 72,000, 360 (0.50%) |
| R3 late: reads, differ | 18,000, 99 (0.55%; 131 words) | 15,000, 837 (5.6%; 1,526 words) | (all zero) |

### 4.3 Late reads (`HL_RB_Rn=3`)

The two classes' reads skip every wait (the order ticket, the replay catch-up, the wait for the write-back): the CPU reads what guest memory
holds. The check says the value is what the waited read would give in 99.5-99.9% of R2 reads and differs by being older otherwise. This is
**not deterministic**: whether the write-back of the previous frame's submission has landed when the CPU reads depends on the GPU, so the
extra latency is 0 or 1 frame; a deterministic form needs the class's write-back to go through a staging buffer that the CPU applies at a
fixed point (the machinery exists for `RX_VK_POOL_MIRROR`: `staged_wbs`, `memory_staged_apply`), applying the results of submission N-2
at frame N, never N-1. It was not built here: it only matters together with R1 (EXPO1's design is the same shape, an engine-owned ring
handing the CPU an older finished frame; one mechanism should serve the meter, R2 and R3).

## 5. Switches

| switch | default | what |
|---|---|---|
| `HL_RB_R2`, `HL_RB_R3` | 0 | 1 both parts, 2 the refresh part only, 3 the late part only |
| `HL_RB_R2_CHECK`, `HL_RB_R3_CHECK` | 0 | 1 measures what the parts would change (no change in what the game sees) |
| `RX_VK_RB_WHO` | off | logging: writers of what the CPU reads, content samples |

## 6. Measurements (main PC, RTX 5070, `market-spin` from `market-mid`, speed lease, slot b with `vsync=0`, uncapped, `dlss=0 upscaling=0 resolution=1 hdr=0`; build `readb2-c`)

Pictures (`RX_VK_SHOTS`, 28-30 shots a run, off and on in market-mid, kakariko-village and hyrule-field saves; the scripted run lands at
the same night meadow in all three, so it is one picture three times): no difference by eye (moon, clouds, grass; not black, not white, no
flashes), the frames are not byte-equal because the game's timing differs run to run. No crash, no stall. Not a strong picture check:
the save-specific scenes were not reached by the script (the scene's run harness loads the last written save).

GPU cost of the refresh (`RX_VK_PASS_TIMING=1`, serialized spans, ms a frame, 2 runs each): `rbrefresh` **0.173 / 0.179 off, 0.096 / 0.066
with `HL_RB_R2=2 HL_RB_R3=2`**; its copied bytes 5.7 / 6.0 MB off, 1.7 / 1.1 MB on (-70 to -80%); the log's scatter line 0.41 to 0.25 MB a
submit. `wb` is unchanged (0.44 ms, 54 MB): the write-backs of R2/R3 are the data the CPU reads, and the bulk of `wb` is R5. On the PC
3050 the 5070's 5.7-times factor for the refresh predicts about 1.0 to 0.3-0.5 ms a frame (not measured: no game runs on it from here).

Speed (fps of the run, 3 runs each, interleaved, uniform options; a sanity figure, the first 15 s load and the camera turn are inside):
| arm | fps | GPU ms a frame (log) | readbacks that waited (of 3000) and ms each |
|---|---|---:|---|
| off | 76.2, 82.9, 77.5 (mean 78.9) | 3.53-3.63 | 347 / 309 / 371, 0.24-0.29 |
| `=2` refresh parts | 73.3, 83.2, 83.6 (mean 80.0) | 3.55-3.67 | 404 / 319 / 364, 0.23-0.33 |
| `=1` refresh + late | 83.6, 83.7, 84.2 (mean 83.8, +6%) | 3.62-3.67 | 283 / 299 / 297, 0.11-0.13 |

Said plainly: the refresh parts alone are inside the noise on this GPU-idle card (one slow first run); the late reads give +6% and halve
the wait per waiting call, all three runs above all three baselines but one. The late arm's picture is the unproven part (4.3). The gain
the refresh cut is for is on GPU-bound cards, to be measured on the PC 3050 with `buildeadb2` (`HL_RB_R2=2 HL_RB_R3=2` against off;
`=1` for the late reads).

## 7. Hand-over

- Branch `refactor/readb2` (3 commits over `refactor/step0`): `vk_memory.cpp` (rbwho diagnostics, class helpers, `rb_exact`, refresh
  CHECK, merge of overlapping ranges), `vk_replay.cpp` (class reads in `invalidate`, late check), `vk_draw.cpp` (one line: the program
  key for rbwho), `vk_internal.h`. EXPO1 touches `vk_draw.cpp`/`vk_replay.cpp` in other hunks only.
- Builds: `E:\OOT\HeyListenuild\s5ineadb2-c` (main PC), `C:\OOTuildseadb2` (PC 3050, exe + dll).
- Not done: the deterministic late form (4.3), which should be one mechanism with EXPO1's lag-2 ring; R2 reads whose consumer
  (the objects' fractions) and the second 0x40 site `0x807069dc` were not traced; R3's consumer only read; the PC 3050 was not run.
- Next: stack on `refactor/expo1` (`HL_EXPO_GPU`), run `HL_RB_R2=1 HL_RB_R3=1` with it: with R1 off the wait, the R2/R3 reads are the rest.
- Scripts in `E:\OOT\scratch-readb2` (run1.sh, meas.sh, cont.py); slot b's options restored.
