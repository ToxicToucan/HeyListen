# WBL1: the write-back put off to the frame's end (`RX_VK_WB_LAZY`)

Stream WBL1, branch `perf/wbl1` (from `refactor/integ4` 7cf591e03), 2026-11-22/23. The mechanism is pixl27's (ProjectReLink,
GPL-3.0-or-later, `RX_VK_WB_LAZY` and `RX_VK_WB_GUARD`), ported by hand into our diverged `vk_memory.cpp`. Original change by pixl27
(ProjectReLink).

## Why

The RTX 3050 PC measured its link at 6.5 GB/s to VRAM and 6.6 GB/s from VRAM (`RX_VK_LINK_PROBE`). The write-back copies 52-60 MB a frame
from the GPU mirrors to guest memory: 8-9 ms of bus a frame there, 2.5-3 ms on the 5070's 53 GB/s link.

## What was already there

- TICK-015 (`wb_lazy_on`, off with `RX_VK_WB_EAGER=1`): whole game pools the CPU neither reads nor writes are left out for good. A CPU write
  makes the whole pool eager (issue 50). castle-move on the 5070: 7.9 MB a submit copied, of which 5.2 are "pools the CPU writes" (the 144 MB
  pool: the CPU writes ~10 of its pages a frame). That pool is what TICK-015 cannot skip and ReLink's page-level rule can.
- The write-back diff (shadows, `wb_diff.comp`) compares each written run and writes only the words that differ.
- The readback ring (`HL_RB_LAG=2`, `rb_ring.h`) serves ranges the CPU reads back (meters 0x1040, 0x40 slots, 0x2000) and stages them
  apart from the write-back; they are small runs, under the 128 KB floor of this rule, so the two do not meet. The ring does not make a
  deferral safe for other pages: a read it does not know about still sees guest memory.
- `drawn_writeback.h` is texture-only (texels the GPU drew, written back 2 frames later); not touched.

## What it does here

Name: all new code is `wbl_*` (TICK-015's `wb_lazy_on` is another rule and runs first). Rules without Vulkan are in
`engine/gpu/vk/wb_defer.h`, tested by `engine/tests/test_wb_defer.py`.

- On when the measured GPU-to-system speed is under `RX_VK_SLOW_LINK_GBPS` (9); `RX_VK_WB_LAZY=1` forces it, `=0` turns it off. Needs the
  write-back diff and a pool shadow.
- In `memory_end_submission`, after `lazy_filter`: the whole pages of a written run of 128 KB or more (`RX_VK_WB_LAZY_MIN_KB`) that the
  shadow knows, that nothing read back (`rb_pages`), that are not texture storage (`replay_tex_storage_in`), that the CPU did not write in
  120 frames (`RX_VK_WB_LAZY_QUIET`) and that are not already shared are only noted (`wbl_pending`).
- Flush: a thirtieth of the pending 64-page words a frame (`RX_VK_WB_LAZY_FRAMES`), queued into the present's recording (`do_present`,
  `memory_wb_flush_queue`), through the same diff pass; the pages flushed are not put off again in that recording.
- Sync: a mirror sync that finds a pending page written by the CPU runs the diff for it first (`wbl_sync_flush`); the page becomes shared.
- Ours, not in ReLink: a CPU invalidate over a pending page (`memory_cpu_reads`, any size) writes it back at once and never puts it off
  again (`wbl_note_read`); a pool that loses its shadow or mirror copies its pending pages whole (CPU-written ones skipped) with the dead
  mirror held until that copy is recorded (`wbl_orphans`, `kWblHold`); after a sync flush the shadow forgets the page (we have no
  mirror-to-shadow baseline copy); a texture registered over pending pages writes them back (`memory_wb_texture`).
- Protection as mechanism (`RX_VK_WB_LAZY_PROTECT`, on with the rule; ReLink's guard code, made permanent): pending pages are PAGE_NOACCESS.
  A read by a game thread faults, the page is given back and queued; the next recording's end writes the pages of its 4 MB region
  (`RX_VK_WB_LAZY_REGION_KB`) back and never puts them off again. `RX_VK_WB_GUARD=1` adds the log lines. Windows only.
- `RX_VK_WB_LAZY_HOT` (default 1 = off): put a page off only after N frames of writes running. Measured at 8: 1,270 pages a frame put
  off instead of 10,400 (the written pages differ from frame to frame), the saving gone.

## Measurements (main PC, RTX 5070, link 53 GB/s from VRAM: the rule is forced on)

MB a frame is the bytes in the runs the diff compares or copies (log line "write-back runs (600 frames)"; the diff writes less than that
over the bus). Speed leases, priority 2, fps counted from "play: began" + 10 s (`build/s5/ab_main.sh`), 3 rounds interleaved.

| scene | MB a frame off | on | fps off | fps on |
|---|---:|---:|---:|---:|
| castle-move (hyrule-castle save) | 55 | 17 | 109.6 / 115.5 / 118.4 / 114.1 | 114.3 / 120.2 / 116.0 / 115.5 |
| market-spin (market-mid save) | 53 | 16 | 82.6 / 96.5 / 87.2 / 92.9 | 82.5 / 94.6 / 89.6 / 92.3 |

(four A/B sets, one per build wbl1, wbl2, wbl3, wbl4; the builds differ in the read-safety layers, not in what is put off, except wbl1
with `HOT=8` which is not listed.) fps is inside the noise on this machine (+4.3, +4.0, -2.1, +1.2 % and -0.2, -2.0, +2.7, -0.6 %), as the
link is fast; the gain is for the 3050 (its list ran on the first build: Kokiri +3.1 %, Hyrule +4.1 %, Market castle-move +1.1 %, market-spin
+1.9 %, auto-gated; that build had no read protection).

## Guard (reads of a page put off, by a thread that did not announce them)

| build | what | castle-move | market-spin |
|---|---|---|---|
| wbl1 (ReLink as is) | first touched by a read, whole run | 210 (texture upload) | 840 + 45 game + 2 |
| 3050, wbl1, Hyrule Field | game worker f_812f0fc0, pool 0x103d000000 | 262 | |
| wbl2 (page learned) | game reads kept coming | ~200 new pages a window | |
| wbl3 (4 MB region learned) | game reads gone | 0 game; 597 texture upload | 5 game; 664 texture upload |
| wbl4 (+ texture registration) | | 2 game, 420 texture upload | 3 game |

The texture reads are `Tegra::Texture::UnswizzleTexture` (hookshot_vk, CPU texture upload) over pages in pool 0x100f000000, in one or two
windows a run: texels a GPU write put there are read once at the texture's first use. Writing the pages back at registration cut them
(387+210 to 210+210) but not to 0; the first read of such a page is stale. A one-shot texture would stay wrong. This is NOT solved. The
exact fix is a settle at the upload: write the pending pages back and wait for the GPU before the unswizzle's read, rare but a stall.

## Not verified

- Pictures: one off/on pair (castle-move, frame 4500) looked alike; the game's clock differs between runs, so no pixel comparison. No
  garbled texture seen in the pairs looked at. The shots are in `E:\OOT\scratch-wbl1\shots{3,4,5}`.
- The stale texture read above; reads by threads of the 3050's Market and Kokiri guard runs (not read line by line here).
- Zone changes, long play, `RX_VK_WB_LAZY_PROTECT` cost (arming syscalls) and the 3050's fps with protection on.
- Linux: the protection is Windows-only; the rule itself compiles in the same file.
- A texture created over pending pages after a CPU-side upload is mis-timed only if the registration is late; not exercised.

## Proposal

Auto on slow links is the intended default, but NOT yet: keep it off by default until the texture-upload reads are 0 or settled and the
3050 confirms guard and fps with the protection. Until then: `RX_VK_WB_LAZY=1` to try.

## Files

`engine/gpu/vk/wb_defer.h`, `vk_memory.cpp` (core block after `lazy_filter`; second block after `wbd_table_get`; hooks in `mirror_sync`,
`memory_end_submission`, `mirror_free`, `shadow_free`, `mirrors_give_back`, `memory_cpu_reads`), `vk_replay.cpp` (`do_present`,
`texture_created_of`), `vk_internal.h`, `engine/tests/test_wb_defer.{cpp,py}`, regenerated `docs/SWITCHES.md` and
`engine/docs/switches.json`, README credit. Scratch: `E:\OOT\scratch-wbl1`; logs `build/s5/{ab-*wbl*,*wbl[1-4]*}`.

## Update 2026-11-23: settle, and the 3050's wbl4 results

- 3050 (wbl4, read protection on, auto gate): Hyrule hyrule-walk 42.3 -> 45.1 (+7 %, p99 -9 ms; the first run list: 41.0 -> 43.0), Market castle-move 48.0 -> 48.7 (+1.2 %);
  write-back runs 58.9 -> 16.7 MB a frame (Hyrule), 52.0 -> 15.2 (Market). Guard first-touch reads: Hyrule 203 (texture unswizzle 201, game 2),
  Market 215 (210 + 5), Kokiri village 1 (a new reader, hookshot_vk +0x3b17f1, guest 0x103d37f000; unresolved, the wbl4 map was not kept).
- Added after that (commit 08d0b3377, NOT yet built or run): `memory_wbl_settle` in `guest_bytes` (vk_texture.cpp): before a texture upload reads
  guest memory, the pending pages of exactly that range (CPU-unwritten) are copied from their mirrors by a one-off command buffer, fence-waited,
  and written into guest memory (`RX_VK_WB_LAZY_SETTLE=0` off). The guard's fault handler does the same for the 4 MB region of a faulting page,
  on any reader's thread, with try-locks only (falls back to the old learn-and-write-back-later path when a lock is busy).
- perf/wbl1 was rebased on main d929c4759 (only the generated switch lists conflicted).

## State at stop (2026-11-23 reboot)

Done: port of RX_VK_WB_LAZY with guard, protection, region learning, texture registration write-back, settle (commits on perf/wbl1; last
commit see `git log -1`); main-PC results above (MB a frame 55 -> 17, fps noise); 3050 results of wbl4.
Half done: the settle code (08d0b3377) compiles (clang syntax + object check) and test_wb_defer passes, but it has never been BUILT or RUN.
Unknown until run: a stall or deadlock of the settle on the replay thread, the guard counts with settle (expect texture reads and game reads
about 0), fps unchanged. The default is still OFF (RX_VK_WB_LAZY unset = auto on slow links is the intended default only after the 3050 confirms
the settle build: guard Hyrule / Market / Kokiri village, fps Hyrule and Market 3 rounds A off / B auto).
Exact next command (after the reboot, nothing is leased): `cd /e/OOT/HeyListen && nohup bash /e/OOT/scratch-wbl1/all6.sh > /e/OOT/scratch-wbl1/all6.out 2>&1 &`
(builds wbl6 at build priority 1 with mkg -Owner wbl1, then guard + shots runs under a slot lease and the castle-move/market-spin off/on A/B under
speed leases, priority 2, bin/wbl6; results in build/s5/t-wbl6* and abc7.out/abm7.out; save the wbl6 map first:
`cp /e/OOT/builder-work/sa/build/{hookshot_vk.map,hookshot_vk.sym,HeyListen.sym} /e/OOT/scratch-wbl1/wbl6map/` to resolve guard readers).
Then: send the 3050 confirm list for bin\wbl6 (guard on Hyrule, Market, Kokiri village; fps Hyrule and Market, 3 rounds A off / B auto); if it
confirms, make the auto gate the default (it already is when RX_VK_WB_LAZY is unset: check the default-off statement in this doc and the
final report), update this doc, commit, report.
Leases/scripts in use at the stop: only a queued build ticket (wbl1, prio 1, released at the stop) from /e/OOT/scratch-wbl1/build9.sh under
all6.sh; no game, no slot, no speed lease. Scratch: E:\OOT\scratch-wbl1 (scripts build*.sh func*.sh runs*.sh all*.sh), logs build/s5/*wbl*.
