# FIX1: the pseudo-operation fold (#97) and ASTC on AMD integrated GPUs (#127)

## State

- Part A (branch fix/fold-pseudo): step 1 done, the fold had three wrong cases (below). Fix and test written. Steps 2 and 3 (pictures, cost) waiting for a build lease.
- Part B (branch fix/amd-igpu-astc): done, tests pass on that branch (policy header + test, switch lists regenerated). No AMD hardware here.

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

(to fill in)

## Part B: ASTC on AMD integrated GPUs

`gpu/vk/astc_cpu_policy.h` `by_default(windows, vendor, device, integrated)`: on Windows, vendor 0x1002 and device type
INTEGRATED_GPU (the two Ryzen desktop ids 0x164E and 0x13C0 stay named); `RX_VK_ASTC_CPU` set always wins (0 keeps the GPU path).
Test: `tests/test_astc_cpu_policy.py`; `tests/test_astc_cpu.py` updated (a 780M and an 890M now take the CPU decode).
