<!-- Copyright (C) 2026 Sacrappy -->
# LIFT2: LIFT1's piece 5 fixed, ProjectReLink's native floating point ported (23 Nov 2026)

Branch `perf/lift2` (worktree `E:\OOT\HeyListen-wt\lift2`), on main 2ed3bab9c with LIFT1's six lifter commits
(`perf/lift1`: A64_SUB_V, A64_VBYTES / A64_VMOVES, A64_VECFP, A64_FPXKEEP, A64_LOCAL_REGS / A64_LOCAL_FLAGS, the tests'
PATH) cherry-picked. Why: PIPE1 found the Market frame on the RTX 5070 / 7800X3D bound by the game's own main thread
(11.1 ms) rather than our replay (9.65 ms); the game-code levers count there.

## 1. LIFT1's piece 5 (A64_LOCAL_REGS / A64_LOCAL_FLAGS): why its lift stopped

`oot_patches: the output size places were found [1, 0, 1, 1, 1, 1, 1, 1] times` (OUT_EDITS [1], code_010.c). The eight
output-size edits of `nx2/oot_patches.py` sit in six functions:

| edit | function | an address `guest_locals.mentioned_addresses()` saw in it |
|---|---|---|
| 0 | f_81222ce0 (nnMain) | 0x81202ee4 only when the function's body holds it (found in lift1d, not in lift1b) |
| 1 | f_810a7b70 | none: `x0 + 724` and `x20 = x1`, no address in the text |
| 2, 3 | f_80ea2f30 | 0x80ea2ffc (a return address) |
| 4 | f_836ce9d0 | 0x836cea54 (a label) |
| 5, 6 | f_83b7b340 | 0x83b7b440, 0x83b7b4e0 |
| 7 | f_80b72340 | 0x80b723ac |

The edits name f_810a7b70 and nnMain only in prose ("f_810a7b70 sets up the display-size object", "nnMain
(f_81222ce0)"), which `_ADDRESS` (`void f_`, `L_`, `0x`) does not read. The lift gave f_810a7b70 locals, `c->x[21]`
became `x21_`, and the edit's text was not there. Fix: `_NAMED` also reads `f_<address>` not followed by `(` (a callee's
call keeps its locals), as ProjectReLink does for the same reason (it keeps every `f_` name of these files). The six
functions are now all in the set (test_guest_locals checks them).

**A second fault, found on the way (LIFT1's "pieces 1-4" build, lift1d4).** That build was the piece-5 tree with the
locals off. `nx2/lift.py` asked the keep_* functions only when the locals were on, and `guest_locals.op_text` kept the
main texts only with the flags as locals: with the locals off and A64_SUB_V / A64_VECFP / A64_VBYTES on, the gated
functions were written with those passes' text. lift1d4's log: every hl_gates gate "NOT gated: its lifted text hashes
..., the native code was written against ...", the step / whole / floor / solver / song / sound / horse / ocb hooks not
hooked ("f_832b9880 (ocb hook) not found with its recorded hash"). So LIFT1's +2.2 % (103.0 -> 105.3 fps in the Market)
was measured with the native bricks and the gameplay hooks off; it is not a number for pieces 1-4 as they would ship.
Fix: `guest_locals.text_passes()`; the keep_* sets and the main-text views run whenever a pass that changes the text is
on, the locals off too. Section 5 has the pieces measured again with their gates.

## 2. ProjectReLink's native floating point (pixl27, ProjectReLink 34efe84), ported

Read: ReLink's `third_party/a64-kit/lift/a64_lift.py` (A64_LOCAL_VREGS at 531, FPX_EASY_HEADER at 1047, NATIVE_FP at
1142, its use in `Lifter.write`), `nx2/build.py` 944 (a64_hot_fp.inc in the hot pieces' keys), `host/hos_thread.c` 474
(hos_fpcr_sync), and its `suyu/arm64_to_c.h` (only one comment differs from ours: the FPX helpers are the same). The
clone was only read; nothing of it was run. Our kit has more than ReLink's: the FP blocks (TICK-063, `a64_block.py`), the
slow-path rescues (TICK-044), and LIFT1's fpxkeep / vecfp / fastvec. Everything is in `engine/nx2/nativefp.py`, which wraps
the kit from outside as LIFT1's ports do (CONTRIBUTING.md: third_party/ is not edited). Three lift-time options, off unless
`=1` (A64_ variables, so in the lift key); all three off, a64_fpx.h and the hot text are the lift's without the module, to
the byte (test_native_fp checks it).

### A64_NATIVE_FP

The exact fast paths keep Arm's result AND its cumulative FPSR flags: every operation tests the gate (FPSR.IXC set, FPCR
at its default but FZ) and the operands' and result's ranges on integer copies (about fifteen instructions and four
branches for one multiply). The game reads FPSR nowhere in its hot code (ReLink's finding). In the hot functions:

- `a64_fpx_hot_*` -> `a64_fpx_nat_*`: one host instruction (add, sub, mul, div, sqrt, fma; binary32 and binary64); a NaN
  result goes to the exact wrapper (Arm's NaN choice and its default NaN stay).
- `a64_vfpx_add32 / sub32 / mul32` -> `a64_vfpx_nat_*`: a NaN lane is kept only when exactly one of its operands was a NaN
  (that operand, quieted, on both architectures), else the exact helper.
- `recomp_fp_to_int / recomp_fixed_to_fp` -> `a64_nat_*`: the host's truncating conversion (its out-of-range answer to the
  body) and SCVTF / UCVTF without fraction bits in round to nearest.
- (HeyListen, not in ReLink) the FP blocks: the block's gate is FPCR alone and its arithmetic ops (`a64_bk_vadd` ...
  `a64_bk_ssqrt` -> `a64_bk_n*`) only flag a NaN result; the block's per-op fallback then takes the native helpers.
  Compares and conversions inside a block keep their tests. The gate keeps the `&&` of RECOMP_FPX_OPEN
  (`A64_NAT_OPEN_BK(c)` is `1 && !(fpcr & mask)`): clang matches a function's PGO profile by its control flow's shape, and
  a `&&` is a counted branch, so the shipped profile still fits the hot functions. Only names change otherwise, as in ReLink.
- Not in the functions the text edits and hl_gates read (`fastvec.PLAIN0`): they keep main's text, so the gates' hashes hold.
- MSR FPCR, in every function, is followed by `hos_fpcr_sync(c)`; `host/hos_thread.c` sets MXCSR's FTZ and DAZ from FPCR.FZ
  there and at a thread's and a fiber's start. Unlike ReLink, the host only does so once the lift asked: a native lift's
  a64_fpx.h holds a constructor that calls `hos_fpcr_follow()` before main. A lift without the option leaves MXCSR alone,
  so the exe built from main's lift behaves as before. `HOS_FPCR_SYNC=0` turns the following off (a measurement switch).
- The helpers go into a64_fpx.h (static inline, unused elsewhere) rather than ReLink's own `a64_hot_fp.inc`: a change of
  the option re-lifts every piece anyway.

### A64_FPX_EASY

Inside the gate, past the fast path and the rescues (the kit's, TICK-044's, fpxkeep's), the binary32 FADD / FSUB / FMUL
wrappers compute what is left with the host's arithmetic where Arm's result and flags are known (NaN or infinite operands,
invalid operations, overflow, subnormal operands under FZ, tiny results), instead of the software body; result and FPSR
are the body's. ReLink's vector `_fix` route (redo only the lanes out of the fast test) is not ported: our lane routes are
TICK-044's and fpxkeep's, which keep NaN and infinite lanes on SSE and call these wrappers for the rest.

### A64_LOCAL_VREGS (with A64_LOCAL_REGS)

ReLink's `localize_regs` / `reg_writes` / `dirty_regs`: in a function with local registers, the vector registers it names
are locals too (`uint64_t vN_[2]`); the guest ABI decides the syncs (v0-v15 written back before a call or a return and read
again after a call, v16-v31 never there, everything at an SVC, a tail call or an indirect jump). fastvec's passes run on
such a function before the renaming (its shapes spell `c->vreg[N]`). The hot functions keep their context
(guest_locals.keep_hot: FP blocks and PGO), so here this reaches the cold code only; ReLink's `A64_LOCAL_REGS_HOT`
(locals in the hot functions alone) is not ported.

## 3. Precision

Measured game-free (`tests/test_native_fp.py`, 7800X3D, x86-64-v3 and -v2; random operands weighted to NaNs,
infinities, zeros, subnormals and range edges, random FPCR / FPSR, a sweep of special pairs):

- A64_FPX_EASY: 214,112 checks, result and FPSR equal to the software bodies, at v3 and v2.
- A64_NATIVE_FP, with MXCSR following FZ as the host sets it: 1.8 M checks at v3 (scalar shapes, vector ops,
  conversions, the blocks' ops) and 1.4 M at v2, **values equal to the bodies' in every case**. ReLink's note that a
  product rounding up to exactly the smallest normal is kept where Arm flushes it did not show: 1 in 8 scalar cases
  were built at that edge (exact products just below 2^-126) and the host's FTZ flushed them as Arm does (0 differences).
- The exact wrappers the other functions call, under that MXCSR: values unchanged (FTZ can make their fast path keep a
  flushed zero sum: right value, UFC not raised).
- With MXCSR at its default the same runs find thousands of mismatches (gradual underflow where Arm flushes): the
  host's following of FZ is required, and the test holds to it.
- Eight deliberately broken helpers are caught (a missing OFC, inf - inf as the host's NaN, a tiny product not flushed,
  a NaN kept in a scalar, a vector lane or a block, FCVTZS's out-of-range answer kept, UCVTF from 64 bits signed).

What does differ: **FPSR's cumulative flags** are not raised by native operations in the hot functions (and UFC for
flushed sums elsewhere). Code that reads FPSR (fetestexcept, a library's checks) would see fewer flags; the exact paths'
gate needs FPSR.IXC, which stays set once any exact operation raised it. Host code that runs on a guest thread (HLE
functions, hl_* native bricks) now runs with FTZ / DAZ when the game has FZ set (FPCR.FZ in 99.6 % of the calls,
perf-audit.md): it flushes tiny values as the guest would.

## 4. Builds

- `build/s5/bin/lift2all` (perf/lift2 70c6b2957, `A64_NATIVE_FP=1 A64_FPX_EASY=1 A64_LOCAL_VREGS=1`, pieces 1-5 on by
  default; -Block, shipped profile): built in 1,309 s, exe 457 MB. Lift: `locals: 94 functions that hold an address the text
  edits name kept`, `locals: 38 edited functions checked in the written C` (check_patched passes), all eight output size
  edits made, `A64_NATIVE_FP: block_ops 73773, blocks 10631, conv 1914, fpcr 7, hot 824, scalar 48053, vector 30192`.
  hl_gates after the lift: 22 gated, 16 gated already, 24 hooked, 2 hooked already, no "NOT gated", no hook missed
  (LIFT1's base build: 21 / 17 / 24 / 2). 3,227 `a64_fpx_hot_` calls and 595 block gates stay exact: the hot functions
  the text edits and gates read.
- The first lift2all attempt stopped in check_patched (section 1's second fix, commit 70c6b2957): a gate's constructor
  line above a gated function's head was counted for the function above it.
- Not built yet: lift2p5 (pieces 1-5, no native FP) and lift2p4 (pieces 1-4 with their gates). The base for the A/B is
  `build/s5/bin/integ4b` (refactor/integ4 7f9a8e8bf: the engine and lift inputs of main 2ed3bab9c, the coordinator's call).

## 5. Measurements

Not run yet (the run was stopped before the speed leases came). To do: `ab_two.sh` integ4b against lift2all, market-spin
(market-mid) and kokiri-idle (kokiri-village), 3 rounds; then lift2p4 against lift2p5 in the Market (LIFT1's piece 5),
and lift2p5 against lift2all (native FP alone).

## 6. Function and physics checks

Not run yet: `E:\OOT\scratch-lift2\fcheck.sh lift2all` (three zones with RX_VK_SHOTS, crash and hl math lines). No tool
compares a scripted run's positions or timers between two builds: the physics check can only be by picture (the
hyrule-walk shots side by side) and by the game-free proof above that the values are Arm's.

## 7. Proposed default, risks, not verified

Proposed: all three stay lift options, off by default, until the A/B shows a gain and the owner has played long sessions
on a lift2all build. Risks: FPSR flags (section 3); MXCSR FTZ/DAZ on the guest threads' host code; A64_LOCAL_VREGS assumes
the game keeps the AArch64 calling convention for v16-v31 everywhere (as the locals of piece 5 assume it for x9-x15).
Not verified: fps, the picture, the game's speed, the PGO profile's match on the native hot text (the build log does not
print clang's profile-mismatch count).
