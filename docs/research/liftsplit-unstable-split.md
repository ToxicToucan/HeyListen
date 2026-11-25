# The lift's edits that missed their text at random (main7, 23 Nov)

## State

- 24 Nov, 08:15: DONE on `fix/lift-split` (not pushed). Cause found from the logs: not a split that varies but a gen folder
  another process rewrote during main7's lift. Confirmed that the lift itself is deterministic: two real lifts (main + the
  block lift, hot list, boundaries kept, random hash seeds, run in parallel into `E:\OOT\scratch-liftsplit`) gave 82 files,
  byte-identical, the same 11 splits, the same 7 oot_patches places (dynamic resolution in `code_006_1.c`, output size 0 in
  `code_013_1.c`), 294505 definitions. Guards and test written; tests pass (run_all: only the known local failures).

## What the logs say

main6 (passed), main7 (failed) and main7b (passed) lift the same input, the same options, and report the same function
counts. main7's edit stage differs from main6's in three ways:

1. 56 files instead of 74: the numbered chunks are the same 56 (the boundaries in `chunks.txt` were kept), but only one
   chunk is split (`code_023.c`, "4 MB ... in 1 parts", where main6 had 21 MB in 3 parts) and the other ten splits
   (`code_006.c` 47 MB, `code_013.c` 25 MB ...) are not reported. `code_023.c` at 4 MB is what the first part of main6's
   split is: the files read by the split were first parts already.
2. The edits report `applied already` / `gated already` (native_replace, hl_gates) for functions of `code_000`, `code_005`,
   `code_006`, `code_007`, `code_015`, `code_hot`: main6 made them in this run. Those files held text edited before this
   lift's edit stage.
3. Functions of the second parts are missing: `hl_gates: WARNING: f_8080fd70 is not in the lifted code` (it is in
   `code_006_1.c` in main6), and the two oot_patches places that main6 found in `code_006_1.c` (dynamic resolution) and
   `code_013_1.c` (output size edit 0) are found 0 times. A patch looks in every `code_*.c` of the folder, whole chunks and
   parts alike: it did not miss because of where a split put its function, the function was in no file.

So the gen folder, at the time of main7's edits, held the first parts of an earlier, finished split (patched already) and none
of its other parts: the state a second lift leaves behind when it runs beside this one (its `split_big` removes every
`code_NNN_M.c` it did not make, and rewrites each chunk as its first part; main7's own write had put the whole chunks there
before). The lease serialises the builds that ask for it; it does not stop a lift started beside a build, or one a stopped
wrapper (mkg.ps1's PowerShell, killed on a timeout) left running: `lift.py` is a child process, and the lease is released when
the wrapper ends. The scheduler's log shows bc7c holding a build lease for 10 s at 13:24:29 and again for 30 s at 12:33:09 (its
wrapper ended at once), and main7's lift ran 13:25-13:33.

This is the likeliest cause; the logs cannot prove it (main7's gen folder is gone, rewritten by main7b). What is certain is
that nothing in the lift checked that the folder it wrote was still its own.

## Fix

- `nx2/genlock.py`: `<gen>.lock` (pid, owner, time) held by lift.py (from the start of a `--write` run), split_hot.py,
  hl_gates.py and oot_patches.py run by hand. A second writer stops with a message naming the holder; a dead holder's lock is
  taken over. (mkg.ps1's `Remove-Item` of the old chunks still runs before the lift: it is not guarded.)
- `chunks.check_whole`: after the split, the files must define exactly the functions the lift made (294505 in the real lift,
  counted in 22 s on a finished gen folder). A folder another process rewrote is stopped with the counts and the likely
  cause, not at a patch that then fails at random.
- oot_patches: a patch or edit not found names the function (`OUT_NAMES`, `WHERE`) and the files it searched.

## Test

`python -I -B tests/test_lift_split_stable.py`: the real `chunks.write` and `split_big` over a made-up lifter, byte-identical
files under four hash seeds; the output size edits found whole chunks and parts alike, and a missing place names its function
and the files; a part removed (what a second lift does) is caught; the lock.

## Was v0.4.3 exposed?

The cause is not tied to the lift options turned on on 23 Nov (A64_NATIVE_FP, A64_FPX_EASY, A64_LOCAL_VREGS): they change the
text, not how it is split or patched, and `chunks.py` (the split, the boundaries) and the patches' search are the same as in
v0.4.3 (`git diff v0.4.3 origin/main` on chunks.py: nothing). A v0.4.3 player was exposed to the same thing only with two lifts
at once in one work folder (two Builder runs, or a stopped run's lift left beside a new one); a single lift on a player's PC
gives the same files every time, as the two lifts above did. No hit has been seen from a player. The lift options do make
the lift's text bigger (more chunks parts), not different in kind.
