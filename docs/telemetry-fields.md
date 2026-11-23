<!-- Copyright (C) 2026 Sacrappy -->

# Performance summary: the fields added in v0.4.4

What the game side of the performance summary (`gpu/vk/perf_report.h`, `perf_report.cpp`) sends beyond `docs/performance-statistics.md`'s
v0.4.3 tables, and what a server needs to read each of them. Everything here is **new keys only**: a v0.4.3 summary is still
valid, and a reader that ignores unknown keys loses nothing. A key is left out when it would be empty, never sent as 0 for
"not measured". No path, no name, no ID.

The switches: `HL_TEL_WAITS=0` (the wait kinds), `HL_TEL_MEMORY=0` (the memory record's new fields and the page-fault inputs),
`HL_TEL_STALLS=0` (stalls and the load window). All on when the performance statistics row is on. `HL_HITCH_LOG=<ms>` is a local
diagnostic: it logs every frame of that many ms or more with the numbers its cause was made of, and sends nothing.

## `maps.<map>.hitch_detail.os_wait.which` and `.which_ms` (new object `which_ms`; `which` already exists for `readback` and `descriptors`)

An `os_wait` hitch is a frame whose presenting thread was off the CPU while nothing of the renderer ran. v0.4.4 says which wait it
was. `which` is label -> number of hitches, `which_ms` is the same labels -> the hitch frames' summed ms. At most 8 labels, the
rest under `other`. Labels:

| Label | The presenting thread was... |
|---|---|
| `lock` | blocked on a guest mutex, condition variable, event, semaphore or message queue (the host's `hos_thread_wait` and `hos_cv_sleep`) |
| `sleep` | in a guest or host sleep that was not the frame pacing (the pacing sleep is taken out) |
| `vcore` | waiting for a virtual core after a block (`vc_acquire`) |
| `file` | in a file open, read, write or flush of the game's own (nn::fs) |
| `ws_trim` | none of the above explains half of it, and the process's working set fell by 64 MB or more since the frame before |
| `page_fault` | none explains half of it, and the process took 2,000 page faults or more in the frame (soft and hard together: Windows has no per-frame hard-fault count) |
| `off_cpu` | none of the above: preempted, or a wait the host does not time |

A kind is chosen when it makes up at least half of the frame's `os_wait`. Server: nothing to add if `which` is already summed per
label; add `which_ms` the same way (sum per label). A v0.4.3 summary has no `which` on `os_wait`, and so does a v0.4.4 one run with
`HL_TEL_WAITS=0`.

## `maps.<map>.stalls` (new)

`{"count", "ms_sum", "ms_max", "load_count"}`: gaps of more than 2 s and at most 60 s between two frames. The frame buckets and
`hitch_detail` leave such a gap out (it is no frame: a pause or a stall of the window), which is why v0.4.3's longest hitches all
stop at about 2 s. `load_count` is how many were next to an area change (below). A gap can also be the window being dragged or
minimised; the map block is sent whenever it has a stall. Server: a new per-map row, sum per version.

## `hitch_detail.<cause>.load_count`, `load_ms_sum`, `load_ms_max` (existing keys, now filled)

See "Why the load hitches were always 0" below. A hitch or stall is also a load's when the map change is noticed within 5 s after it.

## `memory` (existing object, new keys at its end)

v0.4.3's `vram_used_gb`, `vram_allowed_gb`, `ram_used_gb` (one sample at the summary's end) are unchanged. New:

| Key | Type | What |
|---|---|---|
| `ram_class` | string | The PC's RAM as a bucket: `<=4`, `8`, `16`, `32`, `64+`, `unknown` |
| `ram_free_min_gb` | number | The least free physical RAM the period's once-a-second samples saw, GB (left out when not read) |
| `commit_pct_max` | number | The highest system commit charge in percent of the commit limit (left out when not read) |
| `ws_trims` | integer | Frames where the process's working set fell by 64 MB or more (a trim by the OS or by the game's cold-page pass) |
| `page_faults` | integer | The process's page faults in the period (soft and hard) |
| `cold_held_s` | number | Seconds the cold-page trimming was held back in all, since the start (only with `HL_MEM_COLD_LOWMEM` set) |
| `cold_passes` | integer | Trimming passes left out in all (the same condition) |

Server: store the object as it comes (`memory_stats` row per summary: the keys above); the `vram_*` and `ram_used_gb` keys keep
their place. The extra keys are there when `HL_TEL_MEMORY` is not 0 and the period had a sample.

## What the `descriptors` hitches of v0.4.3 were (no new field: the cause is counted differently)

Two things made a frame a "descriptors" hitch in v0.4.3, neither a lock or a pool wait on the game's thread:

1. The descriptor time is summed over the replay's recorder threads and sampled (one draw in eight is timed and counted eight times).
   A thread switched out for a few ms inside a sampled draw became eight times that, and the sum over five threads can be several
   times the frame (a 67 ms frame with 517 ms of "descriptors" was seen). Since v0.4.4 a sampled scope over 1 ms is counted once
   (`RX_VK_PROF_CAP=<us>`, 0: as before): on a Market run the descriptors hitches went 9 -> 2, on Hyrule Field 6 -> 0 (the frames over
   50 ms are unchanged: they are counted under what they really waited on, mostly `os_wait`/`lock`).
2. What stays are zone-entry frames where the recorders' first touch of textures and memory (tens of thousands of page faults a frame)
   makes the descriptor build 50 to 100 ms, with `upload` beside it (an exact run, `RX_VK_PROF_ALL=1`, still had 5 and 11 of them).

## Why `memory_stats` was empty for v0.4.3

The game never had a key called `memory_stats`: v0.4.3 already sends one top-level object `memory` with the three keys above in every
summary (`perf_report.h` `summary_json`, the v0.4.3 tag has it at the same place). So the client was sending it. Whatever table
`memory_stats` fills from must read a different key from `memory`, or skip a record whose `vram_*` values are 0 (a driver without
`VK_EXT_memory_budget` reports both as 0, `vram_usage_now`). That is the server's side to check: the client's `ram_used_gb` is the
process's private bytes, not 0, on every summary. Not verified against the server.

`ws_trims` and the `ws_trim` / `page_fault` labels are a find of their own: in local Hyrule Field runs the working set fell by 300 to
450 MB in a frame, with 50,000 to 100,000 page faults and a hitch of 140 to 1,300 ms, after which the pages faulted back in. That is
the game's cold-page pass (`HL_MEM_COLD`) or the OS trimming, and the record now says how often it happens on players' PCs.

## Why the load hitches were always 0

`load_count` was only raised on a frame whose "loading" flag was on, and that flag could not be seen: it is read by the host's
inventory tick (`oot_inventory_refresh`, every half second) from the game thread, which is exactly the thread an area change blocks,
so the tick never ran while `Sequence_IsLoadingBanc` was set (from the code's own comment; the cause is not proven in the game, the fix is: local runs now show load rows 1 to 3 per map and a 2.2 s stall). The second path, "the map changed on this frame", was set when the
host named a new map, which is also from that tick (and sampled by the renderer once a second), so it came 0.5 to 1.5 s after the
load's long frames and marked only the one frame after the change. And a load's freeze is mostly over 2 s, which the frame feed drops
altogether. v0.4.4 keeps the last 5 s of hitches and stalls and, when it sees the map change, marks the ones inside that window as
the load's (`pr::reclassify_as_load`), and counts the over-2 s gaps as `stalls`.
