# What a draw costs in the replay, and what was taken out of it (stream DRAW1, 2026-11-22)

Main PC (RTX 5070, Ryzen 7 7800X3D), `market-spin` from `market-mid`, uniform options, `HL_RB_LAG=2 HL_GFX_VAR=1`, the shipped parallel
replay. Tools: `RX_VK_PROF_FINE=1` (does not force the serial replay; its sums are now per thread, the old shared array lost updates),
`RX_VK_DRAW_SAMPLE=<hz>` (a sampler of the recording threads inside draw(): `vk: draw sample` lines, symbolised offline with `llvm-nm`).
Do not combine the sampler with `RX_VK_PARLOCK_STATS=1`: that run rendered 0 fps.

## Ranking (draw() summed over the recording threads: 14.1 ms a frame, 5.6 us a draw, ~2,540 draws, profiling overhead included)

| Part | ms a frame |
|---|---:|
| descriptor build, whole | 6.75 (textures 3.64, user uniform buffers 1.39, storage buffers 0.81, banks 0.52) |
| program lookup | 1.86 |
| vertex + index binds | 0.96 |
| bind descriptors (push call 0.60) | 0.92 |
| bind pipeline | 0.67 |
| push constants (checkpoint marker 0.26 of it) | 0.35 |
| hazards + pass | 0.34 |
| pipeline lookup | 0.30 |
| dynamic state | 0.29 |
| the vkCmdDraw call | 0.14 |

- The cost is in a minority of draws: draws of 5 us and more are 31% of the draws and 78% of the time; draws under 3 us are 49% of the
  draws and 1.9 ms.
- Slot misses: 1.0 a draw, 0.87 of them "another handle in the slot" (4 ways); registration version misses 0; a texture destroyed since 0.15.
  The programs that cost most a draw (8-13 us, a few over 60 us) are the texture-heavy ones with 2-10 misses a draw and a descriptor build
  at every draw (builds 0.84 a draw: the per-draw uniform buffers change the bind generation). The two guest functions behind the 14 us and
  31 us draws were not mapped here.
- Sampler (draw1a): about half of the samples in draw() are outside the library. In the one run with module names (few samples) it is the
  NVIDIA driver (nvoglv64 190 of 255 outside samples), not lock waits. In the library `build_descriptors` (inlined work) is the largest
  single function, then `gpu_buffer_bind` with `cap_excl_note`, `program_for` and its pick helpers, `texture_view_of`, `dynamic_state`.
- Why DESC1's descriptor patch gave nothing (inferred): the build is 48% of draw() but spread over ~15 functions of 1% each, and what it saves
  is bounded by the 0.84 builds a draw; the driver calls (pipeline bind, push, vertex binds, checkpoint) stay.

## Fixes (each behind a switch, =0 off; `RX_VK_DRAW_CHECK=1` compares the kept answer with the old one)

| Switch | What | Check |
|---|---|---|
| `RX_VK_DRAW_EXCL` | `cap_excl_hit` took a global mutex at every buffer binding; each thread searches its own copy (vk_memory.cpp) | 100,000,001 lookups, 0 differing |
| `RX_VK_DRAW_PROGFRONT` (off by default) | 32 kept program picks with program_for's own rule (it is in the shader stamp and untouched) | 0 differing pointers; answers 0.33 of the draws |
| `RX_VK_DRAW_PIPEFAST` (off by default) | the last 16 pipelines, not 2 | exact equality; fast hits stayed 0.58 a draw (no gain) |
| `RX_VK_DRAW_HPTR` | a texture handle's storage-pointer test kept with the program's slots | in DRAW_CHECK |
| `RX_VK_DRAW_LAZY` | the "may be left out" test only for a draw whose program or pipeline is not ready | pure function of the same state |

## Numbers (speed lease, slot c, warm-up run first, 3 interleaved rounds, all five on against all five off)

| | fps | p50 ms | p95 ms | submit | draw | descriptors | commands | programs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| off (3 runs, mean) | 89.6 | 10.38 | 15.08 | 6.71 | 12.95 | 6.23 | 3.76 | 1.76 |
| all on (3 runs, mean) | 89.5 | 10.30 | 15.14 | 6.44 | 11.98 | 5.34 | 3.59 | 1.82 |

(ms a frame, summed over threads for draw, descriptors, commands, programs.) The replay's draw CPU falls 7.5% and the replay thread's
submit 0.27 ms; frame rate and p95 do not move (the frame is not CPU-bound here). PROF_FINE with everything on: draw() whole 13.45 ms
against 14.1 before, program lookup 1.85 (unchanged: the front answers a third of the draws and the rest are real picks).

PC 3050 (all-on build draw1b, run through that PC's own session): DRAW_CHECK 0 mismatches; fps 47.9 -> 48.0 at 1440p and 56.2 -> 56.2 at
1080p (GPU-bound); the replay's summed draw 27.1 -> 25.6 ms and descriptors 12.4 -> 10.6 ms a frame.

Defaults (coordinator, default-on rule): EXCL, HPTR and LAZY on; PROGFRONT and PIPEFAST off (no measurable gain), =1 turns them on. The
7.5% / 14% figures above were measured with all five on; the per-switch split was not measured.

Not done: pictures in the four scenes, the 3050 runs, per-switch gains (section "Per switch" below if filled in).
