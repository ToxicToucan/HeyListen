# FIX1: the pseudo-operation fold (#97) and ASTC on AMD integrated GPUs (#127)

## State

- Part A (branch fix/fold-pseudo): step 1 found three wrong cases, fixed, test kept, fold stays ON. Step 2 done (no black leaves on the RTX 5070, driver 617.42, fold on, off, and the unfixed build). Step 3: the fps A/B was not run on this PC (coordinator: it goes to the PC 3050 from build fix1fold); first-start numbers below come from the picture runs.
- Part B (branch fix/amd-igpu-astc): done, tests pass on that branch. No AMD hardware here.

## Part A, step 1: the fold against the SPIR-V semantics

`engine/tests/test_pseudo_fold_semantics.py` runs the real ConstantPropagationPass over bit-field extracts (unsigned and signed) and
and/or/xor of immediates, each with a zero and a sign flag attached, and compares the value and both flags with what the SPIR-V back end
makes of the unfolded instruction (OpBitFieldUExtract/SExtract defined for offset + count <= 32; zero = result == 0; sign = result < 0 as
signed). Edge cases (offset 0..40, count 0..40, bases 0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, alternating bits) then 3 million random
immediates.

Before the fix (origin/main):

- `BitFieldUExtract(base, 0, 32)` folded to 0: `(1U << 32) - 1` is undefined in C++ (0 on x86), SPIR-V defines it as the base.
  Count 32 is also `UExtract(base, 0, 32)` after R2P-like chains of constants. 542 of 2.9 M defined cases differed, all of this kind.
- `BitFieldSExtract(base, off, 0)` threw `LogicError` (the shift by 32 check), while SPIR-V defines a count of 0 as 0; 94 760 cases.
  A throw in the pass fails the translation of the whole program.
- A constant extract with offset + count over 32 (undefined in SPIR-V) threw too, and with the fold on that instruction, which keeps
  its flags and used to reach the back end, now failed the program's translation.
- And/or/xor, the zero flag and the sign flag matched everywhere.

Fix: count 0 and offset 32 give 0, count 32 gives the base, an undefined range is left alone for the back end (no throw).
After: 3 010 341 defined cases, 0 differ, 0 throw.

## Part A, steps 2 and 3

Market save, kokiri-idle, "!cheat 1002" at 12 s through HOS_INPUT_LIVE (Kakariko's entrance), RX_VK_SHOTS every 300 presents.
Fold on (fix1fold), fold off (RX_VK_FOLD_PSEUDO=0) and the unfixed fold on (gpuov2: main's translator): the ivy and bushes at the
village gate are green in all three; no black leaves on the RTX 5070. (Shots stay in E:\OOT\scratch-fix1\shots-*, not in the repo.)

First start (build/s5/pf-on, pf-off, pu-on game_log.txt): the fold changes the translation of 15% of 81 sampled programs ("update:" line of pf-off).
Fold off on a cache made with it on: the shaders are prepared before the game, "prepare: 81 s, 91508 of 95955 steps; translation 953 s"
of work, play began 213.26 s after the process started; fold on (cache current): 63.09 s; unfixed on: 71.56 s. These are not equal-cache
comparisons (the off start also predicted pipelines), only the cost of a stamp change.

Decision: the fold was wrong in three cases (step 1), so it is fixed and stays on. Whether the fix cures #97 on NVIDIA is not shown here
(no such card); RX_VK_FOLD_PSEUDO=0 stays the player's workaround.

## Part B: ASTC on AMD integrated GPUs

`gpu/vk/astc_cpu_policy.h` `by_default(windows, vendor, device, integrated)`: on Windows, vendor 0x1002 and device type
INTEGRATED_GPU (the two Ryzen desktop ids 0x164E and 0x13C0 stay named); `RX_VK_ASTC_CPU` set always wins (0 keeps the GPU path).
Test: `tests/test_astc_cpu_policy.py`; `tests/test_astc_cpu.py` updated (a 780M and an 890M now take the CPU decode).

## Black leaves (#97): the cause (FIX2, 25 Nov 2026)

Short: the leaves were black because dead code elimination removed a VoteAny that the fold had left without a reader.

- Bisect (build fix1leaves, save black-leaves, fold of BitFieldUExtract only): program 7650519f88e59b19 (a 64x1x1 compute shader with
  ballots and shuffles; translated four times). Folds are numbered per translation: 728 flagged instructions, the 104 R2P groups of
  seven. RANGE 0-189 green, 189-378 black; 216-243 black, 216-217, 217-224, 224-231, 231-238, 238-243 each green (several folds
  are needed at once: four consecutive R2P groups).
- What those folds are: `R2P P, mask 0x40` (PR mode, byte 0) = P6 from bit 6 of the register, P0..P5 unchanged. The guest wrote
  P6 with `VOTE.ANY P6, PT` just before, then the lane election (ballot, lowest set lane, shuffle).
  R2P's IR is `P6 = Select(zero(BitFieldUExtract(0x40, 6, 1)), old P6, not zero(extract(src)))`. With the fold the condition is
  the immediate false, FoldSelect keeps the source arm and the old value (the VoteAny, uses 1) has no reader.
- IR diff of the same translation with RANGE 216-243 against RANGE 0-0 (scratch fix2/ir/f2-i1.txt and f2-i2.txt): the only
  differences are the folded extracts, their selects, and the two VoteAny instructions (and nothing else) that dead code
  elimination then removed. Votes, ballots and shuffles are not "side effects" in Inst::MayHaveSideEffects.
- Why it matters: a VOTE is a convergence point of the guest program; with the vote gone the ballot and shuffle after it see fewer
  lanes on NVIDIA and the vegetation compute produces zero lighting data.
- The fold values are right (test_pseudo_fold_semantics); the fix is in dead code elimination: VoteAll/Any/Equal, SubgroupBallot and
  the shuffles count as side effects (microinstruction.cpp). Test: tests/test_dce_convergent_ops.py (fails without the change).

## State (black leaves, branch fix/black-leaves; paused 24 Nov by the owner, work in progress)

Reproduced on the RTX 5070 from build/s5/savelib/black-leaves (Kakariko, bushes behind a house), build fix1leaves (main + the bisect
switches): with the fold on the leaves are black-ish, `RX_VK_FOLD_PSEUDO=0` green. Pictures in E:\OOT\scratch-fix1\shots-<arm> (not in
the repo), run folders E:\OOT\HeyListenuild\s5\<arm>. The measure is the ratio of the foliage's brightness to the house wall's,
last shots of a run (steady from present ~6600; the day/night cycle changes the absolute light): black 0.6-0.78, green 0.9-1.1.

Switches added (temporary, uncommitted-for-release): RX_VK_FOLD_PSEUDO_OPS=<and,or,xor,ue,se> (opcodes that fold), RX_VK_FOLD_PSEUDO_TRACE=1
(a `vk: foldtrace <key> ...` log line per program: opcode, immediates, result, flags), HL_SKIP_GIVEUP_S=<s> (host/oot_autoload.c: the
title skip gave up after 120 s when a start re-translated; remove before merging), plus the cherry-picked RX_VK_FOLD_PSEUDO_ONLY/_SKIP/_RANGE/_LOG.

Findings:
- Which opcode: L-on (all) black, L-off green; OPS=se (O-se) green, OPS=and,or,xor (O-bit) green, OPS=ue (U-ue) black. So the culprit is
  BitFieldUExtract folded with flags.
- What the ue folds are: every traced one is R2P in PR mode with an immediate mask (`BitFieldUExtract(mask, index 0..6, 1)`, masks 0x40, 0x20,
  0x3, 0x7f, 0x10, 0x8...) feeding the zero flag; values and flags match the SPIR-V semantics (test_pseudo_fold_semantics). So the
  folded values are right and the trigger is probably what the constant predicates do afterwards (a later pass, or the NVIDIA compiler).
- Which programs (ONLY= by code key prefix, with OPS=ue): first hex digit 0, 3, 4, 5 green (U-d0, d3, d4, d5), 7 black (U-d7), 1, 2, 6 not
  decided (the start did not reach play), half 8-f green (U-hi). Inside 7: 70-73 green (W-0), 74-77 black (W-1), 78-7b green (W-2), 7c-7f green (W-3).
- Inside 74-77 (123 programs, keys in scratch keys-q1.txt, sorted): a 7-bit binary-coded set of arms C-0..C-5 (q1-arms.txt: arm b
  holds the programs whose index has bit b set). Done: C-0 green, C-1 green, C-3 green; C-2 not started (rc=12, no RAM), C-4 and C-5 not run
  (and bit 6 has no arm). So the culprit's index i has i&0b1011 == 0: 16 candidates, listed in scratch candidates.txt:
  740216983d9a892f,741371d06e4ded02,747f09131cef1ffe,749f3c06bf893ce6,751a5964d0a15ac9,752f8499255f6154,75775d005e3f1817,758f172937ba2061,
  75fb6063c8feebeb,7650519f88e59b19,76acecd3f1aa9607,76d9dc419913a797,771dc940e32b5ce5,7745c2a1af888577,77cbbeae87898f2f,77e5ce81d22409b5.
  (This assumes a single culprit program; W-1's blackness is then one of these, but an arm set that is green does not exclude a second
  program only if it was black elsewhere, which was not seen.)

Next step: slot lease, build fix1leaves (already in build/s5/bin/fix1leaves), env `RX_VK_FOLD_PSEUDO_OPS=ue RX_VK_FOLD_PSEUDO_TRACE=1
HL_SKIP_GIVEUP_S=400 RX_VK_FOLD_PSEUDO_ONLY=<8 of the candidates>` twice (then 4, 2, 1: four arms find the single program), from
savelib/black-leaves, scene kokiri-idle, RX_VK_SHOTS every 300, RUNSEC 300 (E:\OOT\scratch-fix1\pic2.sh `[SEED=set-W-1] bash pic2.sh
fix1leaves black-leaves "<tag>|<env>" ...` swaps one cache set per arm in cs2/; sets set-W-1, set-L-off are kept as seeds; series.py prints the
leaf/wall ratio of a run). Then dump that program's SPIR-V with the fold on and off (RX_VK_DUMP_SPV / RX_VK_IR, local only) and diff:
look for what the constant Select/predicate folds expose (the NVIDIA compiler is known to miscompile some predicate/max forms, see FoldSMax32).
Then fix (or exclude R2P-with-constant-mask from the fold), extend the test, regenerate the switch lists, run the whole run_all, take
before/after pictures, and remove HL_SKIP_GIVEUP_S.
