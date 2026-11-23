# The engine refactor plan (2026-11-21)

Validated by the project owner on 2026-11-21. This plan takes priority over every other performance stream until it is done.

## Why

Measured on 2026-11-21 in the Market (owner's mid-Market save, camera turning: `market-spin`):

- **CPU, RTX 5070 / Ryzen 7 7800X3D:** about 11.5 ms frames, GPU busy only 3.5-4.4 ms. About 70 ms of CPU a frame: ~50 ms on ~13 game threads, ~20 ms in the NVN-to-Vulkan replay. About 14.5 ms of that replay is per-draw work over ~2,400 draws.
- **Critical path, PC 3050 (i5-10400F, 6 cores / 12 threads, RTX 3050), WPR trace:** 24.3 ms frames.
  - The replay's "order" thread runs 9.0 ms of each frame on the critical path (37%).
  - The presenting game thread runs 4.2 ms and waits 2.4 ms on timers.
  - The replay workers are idle 85% of the time.
  - The CPU is not oversubscribed: ready-queue waits are 3.7%.
  - See `E:\OOT\scratch-trace1\REPORT.md` (local) and `replay-cost.md`.
- **GPU, PC 3050:** 18.1 ms of GPU a frame at a 100% render (1440p) and 12.6 ms at 50%. About 10.8 ms a frame is resolution-independent: geometry, compute, and what our translation adds.
- **Point fixes inside the current architecture gave nothing measurable:**
  - descriptor cache, submit merging, fence polling (`replay-cost.md` DESC1, ORDER1);
  - event-driven waits, pinned cores (WAIT1);
  - urgent presenter cores (PRESENT1, `present-path.md`);
  - blocking poll lanes (GAMEWAIT1, `game-waits.md`).
  The cost is in the architecture: capture, mirror sync, replaying every NVN command, and GPU readbacks.
- **GPU readbacks:** the game's CPU reads GPU-written results, which on the Switch's shared memory is free. On a PC each read forces a wait, and reading too early or too late causes bugs: cut grass (#50), white flashes, night flicker (#93).

## Principle: module by module, each module complete

- Neither micro-optimisations (shown not to move the frame) nor one big rewrite (months without a playable reference).
- One **whole module** at a time: its inputs, its logic, what it produces for the GPU. Each module is native and readable (rules in `README.md`: nothing of the game in the repository, no lifter change).
- Each module is checked against the translated path it replaces: a `_CHECK` mode with 0 mismatches, pictures compared with `RX_VK_SHOTS=<drive path>`.
- Each module sits behind a switch. It goes on by default only with 0 mismatches, identical pictures and a measured gain.
- One owner (agent) per module, with its own branch, a design note in this folder, and a hand-over section kept current.

## Removing the GPU readbacks

For each readback, find who produces the data on the GPU and who consumes it on the CPU, then pick one of:

1. **Producer and consumer on the same side:** a native module owns the whole loop on the GPU. Example: the exposure meter, where measuring, adapting and applying the exposure all stay on the GPU, so nothing comes back to the CPU.
2. **Compute it on the CPU:** when the GPU computation is cheap, the native module produces the value itself.
3. **One frame late, deterministic:** only where the game's logic provably tolerates it (for example occlusion results). Proven one readback at a time.

Target: no blocking readback. A case tied to gameplay logic is decided individually after study.

## Steps

| Step | Work | Delivers |
|---|---|---|
| 0. Measure | Per-pass GPU time on the PC 3050 at a 100% render, at 50%, and at 1080p; a ledger of every readback (producer pass, consumer guest function, count a frame, bytes, wait ms, bugs linked) | A ranked list of modules by measured ms (GPU and CPU) |
| 1. Readbacks | One native module per readback, in ledger order: exposure first, then cut grass, then occlusion | Waits and a family of bugs removed |
| 2. Direct rendering of the heavy passes | The native module writes our records (`direct-emission.md`), then Vulkan draw lists, for the most expensive passes: no capture, no mirror, no NVN replay for them | Most of the order thread's work, the dry pass and part of the 10.8 ms of resolution-independent GPU work removed |
| 3. Visibility | The visibility job (~4.4 ms CPU) native together with the loop that calls it (`visibility-job.md`) | The largest remaining translated CPU block |
| 4. Thin NVN layer | What stays translated keeps today's path, on far fewer passes | Emulation cost becomes residual |

## Measurement protocol (every step)

- **Scene:** `bash build/s5run.sh --slot <slot> --save build/s5/savelib/market-mid <tag> market-spin` (from `E:\OOT\HeyListen`), plus `hyrule-walk`.
- **Options:** identical and explicit (`dlss=0`, `upscaling=0`, `resolution` fixed, `hdr=0`, `vsync=0`, no frame cap). Check the "Render resolution" lines in each log.
- **Machine:** timed runs only under a SPEED lease (`build/s5/machine.sh`). Never start `mkg.ps1` while a speed lease is held or queued.
- **Machines and judges:**
  - The PC 3050 is the judge, through its own Claude Code session (never ssh game runs), at 1440p and at 1080p.
  - The RTX 5070 is the second machine.
- **Repeats:** x3 interleaved to screen; more to adopt.

## Agents

- At most 2-3 module owners at a time; no other performance stream runs alongside.
- Paused until the refactor ends (2026-11-21): VRAM4 (4 GB cards, branch `perf/vram4`) and the order-thread work (ORDER2, `perf/order2`, design note in `replay-cost.md`).

## Results so far (night of 2026-11-21 to 22)

Market, `market-spin`, uniform options; RTX 5070 and PC 3050 (1440p unless said).

| Stream | Branch | What it found | Gain | State |
|---|---|---|---|---|
| Step 1: readback ring | `refactor/step1` | R1-R3 served from a lag-2 staged ring, no wait; ranges nobody writes answered at once | 5070 +10%, 3050 +5% (1440p) / +8% (1080p) | **On by default in `refactor/integ1`** (`HL_RB_LAG=0` turns it off) |
| Native key-0xa0 draw loop | `refactor/writer1` | `HL_GFX_VAR=1`: same pictures, same speed, less translated work | 0 | **On by default in `refactor/integ1`** |
| Step 2, per pass | `refactor/step2p` | heavy passes are 54-63 draws; 95% of their uniform blocks are written after recording | ceiling ~5% CPU | dropped |
| Step 2, uniform writer | `refactor/writer1` | the writer is the batch job 0x8009bb80 (skin palettes, vector copies, instanced loop) | ~3% of CPU, off the critical path | not built |
| Translated shader quality | `refactor/shaderq` | translation ~1:1 with the guest; `RX_VK_POLISH` rewrites | 0 on both GPUs (3050 GPU 17.35 vs 17.3 ms) | off |
| Replay feed | `refactor/order3`, `refactor/replay2` | `RX_VK_FEED_LOG`: GPU idle is 78-88% "replay not ready", one serial chain, no lock; `RX_VK_PASS_TIMING` forces the serial replay (its gaps are not the shipped ones) | knobs 0 | off; diagnostics kept |
| Deferred join | `refactor/join1` | the next recording's begin touches globals the workers use, so every join is forced at once | 0 | off |

What this says:
- On the 3050 at 1440p the GPU is busy 83% of the frame with the game's own work (16.2 ms); its lever is a lower render resolution with upscaling. The replay's own speed is at most ~+14% there, ~+28% on the 5070.
- The next replay step needs the recording state made per-recording (`table_defer`/`table_pending`, `synced_epoch`, the segment texture table, `mirror_epoch`, `ub_prune`), so a recording can begin while workers finish the last one.
- Long sessions (#121): readback waits and mirror syncs grow with play time; step 1 removes the waits. To check with a 1 h+ run.
