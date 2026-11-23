<!-- Copyright (C) 2026 Sacrappy -->
# RX_VK_FRAMES_IN_FLIGHT on the readback ring (FIF1, 2026-11-22)

Branch `refactor/fif1` (from `refactor/integ2`). `RX_VK_FRAMES_IN_FLIGHT=2` hands the present to a thread of its own, so the CPU's frame N+1
overlaps the replay and the GPU of frame N. Default off. Ported by hand from `stream/r3-pipeline` (present hand-over, the ordered capture sequence
of 745cf4878, the wait log); not ported: the readback latch, the render thread, and 18c7f79ad (the light meter's strict wait is **not** forced:
the readback ring answers the meter's reads).

## What the ring needed
The ring counted frames with `presents_now`, the replay's own presents. With the present on another thread that count, and the point where a
submission's replay runs (the heavy submission goes on from the CPU's signal, after the CPU began the next frame), depend on timing. Now, with
the switch on, both ends use the game's own count of presents (`rb_clock`), an entry is tagged with the clock at its submission's submit
(`rb_tag_for`), and a read first waits (bounded, counted: `rb_wait_staged`) for the replay to have shown the presents whose entries it may
get. The default lag is 2 + (k - 1): L = 3 with k = 2 (`HL_RB_LAG` overrides). With the switch off nothing changes.

## Results (main PC, RTX 5070)
- Ring at L = 3 under k = 2: 0 reads waited for the GPU, 0 not done in time, 0 reads waited for the replay to stage their frame, in every run
  (12 picture runs, 10 starts, 14 timed runs); entries applied are 3 frames old. At L = 2 (`HL_RB_LAG=2`) 23 and 3 reads waited for the GPU in
  two of four scenes: L = 3 is the clean value.
- Other waits (`RX_VK_WAIT_LOG`, market-mid): the main thread's `wait_recorded` 0.02 ms a frame, readback ticket 0.00; the present's wait for a
  free slot 0.00 ms. The remaining waited ranges (0x3980 culling, small ranges) do not serialize the frames.
- Pulses (frame log, 25 s, lumastat.py): 0 in every run, both arms, Market, Kakariko, Lost Woods, Deku Tree (old: 565 in 661 frames). One pulse in
  the plain arm (Deku Tree). By eye: no flash, tint, black frame beyond the load screens.
- Starts: 10 starts from the title with k = 2 all reached play, no device loss; the log's "presents after the CPU's signal ... let go after
  200 ms" counted 0-2 a run (loads).
- Speed, market-spin from market-mid, speed lease, uniform options (dlss=0 upscaling=0 resolution=1 hdr=0 vsync=0 frame_rate_limit=5), frame log
  on in every timed run, one warm-up (plain) then 3 interleaved rounds: plain 86.2, 86.5, 89.7 fps (mean 87.5; p50 10.7, p95 16.6, p99 24.4 ms),
  FIF=2 86.3, 88.1, 88.8 fps (mean 87.7; p50 10.5, p95 16.8, p99 24.2 ms). **No gain** on the 5070: it is not held by the present's wait.
  An earlier series was spoiled by a foreign build (two runs refused by runguard, one at 56 fps).
- PC 3050 (run by the coordinator, 1440p, 3 rounds): A 48.07, F 48.10, G (=2 with L 3) 48.23 fps: no gain (GPU-bound, 19.3 ms), p99 40 -> 46-48 ms,
  rbring 0 waited / 0 not done, 1 and 2 pulses in 25 s against 565 before.

## Verdict
Clean (no pulses, no waits) but no speed: the switch stays off. The old +85% was the forced waits of that build, as R3's own second session found.
Not verified: validation layers, a zone change inside the game, the PC 3050's own CPU-bound case (it is GPU-bound at 1440p).
