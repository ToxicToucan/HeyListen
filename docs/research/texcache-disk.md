# TEXCACHE: a disk cache of the transcoded (BC) textures

## Summary (kept current)

- **perf/bc7c's "78 fps against 112" was not a cost of the disk cache.** It was the tour's average: the off arm spent 123 s of
  its 615 s on near-empty frames (fewer than 50 draws: warp and load screens) at 289 fps, the cache arms 34-37 s. On the frames
  that draw a scene (50 draws and more) all four arms ran the same: off 67.3 fps, cold 67.6, cold2 68.4, warm 68.4. The cold arm
  (no hits) had the short screens too, so off's long ones were that run's machine state, not a cache gain. Steady state: no loss.
  This stream reports fps on scene frames only, and load-screen time apart.
- Rebuilt on `perf/texcache`: the render thread never touches the disk (4.4-6.0 ms of render-thread time in a whole warm tour, the
  longest call under 1 ms), reads ahead on a thread of its own into a bounded pool, a miss transcodes as today; bounded writes,
  copies forward, pack format 2 with encoder version and per-record hash; writes from the one-layer path too (cards under 8 GB and
  integrated GPUs: the batched path, the only one bc7c wrote from, is never taken there).
- Measured gain so far: the laptop (Vega 8), list 1: camera turning 10.4-10.6 fps warm against 8.0-8.2 off or cold (+30%), GPU
  uploads while turning 12-55 ms a frame against 55-125. The main PC is too loaded to show a zone-entry gain or loss.
- Tried and turned off: holding a texture back while its blocks are read (left draws out mid-play, flash detector), letting go of
  stale pool blocks (re-reads).
- Weak point: the read-ahead follows the cold run's creation order; it served 72% of the on-disk textures on the laptop (slow
  frames give it time) and 23-30% on the main PC (3600 found, 1067 taken).
- Default: OFF (`RX_VK_BC_DISK=1` turns it on). Proposed rule below; the 3050's list 2 decides it.

## Texture cost measured before any code (24 Nov)

PC 3050 (RTX 3050), today's PIPE2 runs, `E:\OOT\scratch-decouple\3050\pipe2\{cc,hf,kv,mm}-{A2,B2}\game_log.txt` (110 s each):
- After "play: began", uploads are 0.51-1.71 ms of 12.1-13.1 ms of GPU a frame (4-13%, the mean of the "vk: GPU time" lines
  after play), and nearly all of it sits in 2-3 frames a run: "a frame's uploads took" 64-357 ms after play (cc-A2: 167, 73;
  hf-A2: 237, 189, 66; kv-A2: 357, 269, 64).
- In those big frames the ASTC transcode on the GPU is the largest part: hf-A2's 237 ms frame has "ASTC on the GPU 4 (225.9 MB,
  217 ms)": three 1365x1365 x90 terrain arrays 84 + 64 + 34 ms. Summed over each run's "uploads took" lines (before play too):
  ASTC on the GPU 202-749 ms, CPU uploads 181-227 ms.
- The same ASTC bytes are transcoded again within a session: "transcodes of bytes transcoded before" 19-1103 a run (hf-A2 1103,
  mm-A2 845); the memory BC cache is lazy on 8 GB cards' sibling rule and stores nothing there ("BC cache: 0 entries").
- So on the 3050 a warm disk cache can only take away the 2-3 big upload frames after a zone loads (stutters), not steady fps.

Laptop (Vega 8, main14, camera turning in Hyrule Field, MIR_ref_w1; numbers relayed by the coordinator, log not here):
- GPU-bound: "vk: GPU time" 65-70 ms a frame with uploads 55-76 ms while turning, 6.3-8.6 textures made a frame.
- The four biggest upload frames: ASTC on the GPU 611 ms in all, on the CPU 882 ms, the CPU part NOT ASTC (NVN 0x4b, 0x46,
  0x49, 0x4d, 0x8b unswizzled; too slow for their bytes: page faults suspected, a HL_MEM_COLD=0 run was under way).
- 1365x1365 x90 terrain arrays: 84-111 ms each on the GPU.
- So a BC-block cache addresses at most the GPU ASTC share there (about 40% of the big frames' upload time), not the CPU part.

## The 78 against 112 fps of perf/bc7c (the cause)

Runs (main PC, perf/bc7c build, warp tour, Market save): `E:\OOT\HeyListen\build\s5\bc7c-off`, `bc7c-cold`, `bc7c-cold2`,
`bc7c-warm` (game_log.txt, bench.json). bench.json fps: off 111.7, cold 78.2, cold2 77.2, warm 77.5.

Split of the "vk: N presents, F fps; a frame: D draws" lines after play (each line 120 presents, 120/F seconds):

| arm | all | near-empty frames (< 50 draws) | scene frames (>= 50 draws) |
|---|---|---|---|
| off   | 111.8 fps, 615 s | 123 s at 289 fps | 492 s at 67.3 fps |
| cold  | 76.5 fps, 594 s  | 37 s at 209 fps  | 557 s at 67.6 fps |
| cold2 | 77.1 fps, 616 s  | 34 s at 227 fps  | 583 s at 68.4 fps |
| warm  | 77.4 fps, 617 s  | 36 s at 221 fps  | 581 s at 68.4 fps |

- The tour warps every 40 s; the near-empty stretches start at each warp (19, 59, 99 ... 579 s) in every arm. In the off arm they
  lasted up to 28 s from 219 s on (259-287 s: 28 s, 499-524: 24 s, 579-595: 16 s), in the cache arms 3-7 s. The same 16 maps in
  the same order in all four ("map:" lines).
- About 35,500 of the off arm's frames are those 289 fps frames against 33,100 scene frames: that is also why its median present
  was 3.5 ms against 9-11 ms (the median falls on an empty frame), the other figure the bc7c notes gave.
- So the gap is the metric (fps averaged over a tour whose load screens are 4 times faster than its scenes), not a per-frame cost of
  the cache. From now on this stream reports fps on scene frames only (>= 50 draws) and the time on load screens apart.
- Were off's longer screens the cache's gain (faster loads)? No: the cold arm, which had no hits at all (an empty folder: it only
  wrote), has the short screens too. Lengths of the near-empty stretches, one per warp, in seconds:
  off 3 3 4 1 6 9 28 3 8 13 10 9 24 15 16; cold 5 4 4 2 5 6 4 4 3 4 5 2 3 6; cold2 3 4 3 1 5 3 3 2 3 4 3 2 5 7 7; warm 4 5 4 3 6 7 4 3 3
  4 4 5 4 6 6. Off's long ones begin at the 7th warp (Market to Kokiri) and stay long to the end: a 5-draw screen at ~300 fps held
  for up to 28 s before the next "map:" line, i.e. before the game reported the next zone, in that run only. The machine's state that
  day (shared main PC) is the likely cause; it was not found in the log (no upload, deferral or stall lines in the stretch).
- Per warp, from the "map:" line to the first scene line and the long frames (> 50 ms) in the 10 s after it
  (`E:\OOT\scratch-texcache\warps.py`): off 72.7 s in all, 191 long frames, worst 1138 ms; cold2 66.4 s, 226, worst 1036 ms; warm
  77.3 s, 280, worst 864 ms. perf/bc7c's warm cache did not shorten the zone entries nor their hitches (it read on the render
  thread, 3 ms a read).
- What the owner asked to check besides: the cache does not change any texture's format or mips (the blocks are the ones the
  GPU encoder makes for the same image, BC7 or BC1, same levels; off makes the same BC images), so nothing at draw time differs.
- On this branch the same split is made from each run and the A/B on the PC 3050 is done in one scene, off against warm (list below).

## What was built (perf/texcache)

- `engine/gpu/vk/texcache_pack.h` (pure): pack format 2 (`HLTXCP`; bc7c's format 1 is not read), records `{magic, size, key,
  hash}` + blocks, the scan with a torn tail cut, the encoder version (hash of the three shaders + key revision + format), the
  two-pack index with rotation at half the cap and copies forward (a record of the older pack used again is appended to the
  current one: bc7c's gap), the read-ahead's runs. Tested by `engine/tests/test_texcache_pack.py` (+ `.cpp`), in run_all.
- `engine/gpu/vk/vk_bc_disk.h`: one thread does the scan, the reads ahead (normal priority), the writes and the copies forward
  (background mode: low IO and CPU priority). The render thread: `bcdisk::probe` (index lookup; asks the records after it to be read
  ahead) and `bcdisk::took` (counting, copy forward, keep the read-ahead a run in front), never a file.
- `vk_texture.cpp`: the pool of read-ahead blocks in host buffers (`RX_VK_BC_DISK_POOL_MB`, 384), dropped when not taken within
  `RX_VK_BC_DISK_POOL_FRAMES` (1800); a hit copies the buffer into the image (no staging copy, no decode, no encode); writes only of
  blocks not on disk, the waiting bytes bounded (`RX_VK_BC_DISK_WRITE_MB`, 256); the encoder hash and the folder worked out once.
  The memory BC cache keeps its own rules (RX_VK_BC_CACHE_LAZY untouched). Counters every 600 presents: "vk: BC disk cache".
- Files: `hookshot_texcache_a.pack` / `_b.pack` in the game folder or `RX_VK_BC_DISK_DIR`. Made at runtime from the player's own
  game: never in the repo, a release or a package (the test checks the release scripts do not name them).

## Main PC warp tour, texcache1 (24 Nov 17:12-17:36)

`E:\OOT\scratch-texcache\tour.sh` (Market save, 6 "!cheat" warps every 40 s, 270 s runs, slot a), arms in this order on one cache
folder `E:\OOT\scratch-texcache\cache`: off1, cold, warm1, off2, warm2; runs `E:\OOT\HeyListen\build\s5\tc-texcache1-<arm>`, split
by `E:\OOT\scratch-texcache\warps.py`. The main PC was shared (other agents' runs): read the zone numbers as rough.
- off1 reached play only at 151 s (a new exe's first start): 2 warps, not used.
- Scene frames (>= 50 draws): off2 61.6 fps (173 s), warm1 61.1 (202 s), warm2 73.5 (185 s), cold 43.5 (185 s; the writer's
  first run). Steady state, warm against off: within noise (61.1 and 73.5 against 61.6), as the render thread's time in the cache
  says: 6.0 ms (warm1) and 4.4 ms (warm2) in the whole run, the longest call 0.72 and 0.013 ms.
- warm2 is the outlier, and not by the cache: scene fps per zone (off2 / warm1 / warm2 / cold) Kakariko 50.1 / 49.2 / 84.0 / 28.8,
  DeathMountainTrail 73.2 / 75.6 / 105.1 / 36.7, GoronCity 79.5 / 55.5 / 78.1 / 76.2, HyruleField 61.4 / 54.7 / 39.9 / 35.9, and
  warm2 spent 12 s of scene in HyruleField (the slowest zone) against 37 s in the others. The same zone varies by up to 40% between
  runs of the same build: the shared main PC's load. No fps gain or loss is claimed from the main PC; only the render-thread
  counters (4.4-6.0 ms in a whole run) say there is no steady-state cost.
- Per warp, map line to scene and frames > 50 ms in the 10 s after, the four warps all arms have (HyruleField, Kakariko,
  DeathMountainTrail, GoronCity): off2 21.0 s, 41 long frames (7.7 s of them); warm1 17.0 s, 22 (4.7 s); warm2 19.3 s, 45 (11.8
  s; HyruleField 9.6 s with a 2041 ms frame); cold 38.5 s, 58 (17.5 s). Mixed: no warm win shown on this machine.
- Why: warm2 found 3600 textures on disk but took 1067 from the pool; the read-ahead (3094 MB read, pool full 2204 times, 2406
  read and never taken) runs in the cold run's creation order and cannot keep up with a zone whose textures are made within a few
  frames; the rest were transcoded. And the cache rotated (4096 MB cap, 1946 MB written cold): 548 copies forward and 923
  rewrites in warm2 (707 MB written in 107 s at background IO priority, 614 not written: backlog full). The scan of 2-3.6 GB took
  4.5-7.5 s in background mode (no cache meanwhile).
- texcache2 answers: a new texture whose blocks are on disk is held back (like the upload budget's deferral, its draws left out)
  while its own record is read first, up to RX_VK_BC_DISK_HOLD_MS (400); stale pool blocks let go for new runs; cap 8192 MB;
  the scan at normal priority.

## Main PC warp tour, texcache2, flash detector (24 Nov 18:05-18:23)

Same tour, RX_VK_FLASH=1 RX_VK_SHOT_EVERY=1, slot b, arms off1, hold (RX_VK_BC_DISK_HOLD_MS=400, its default then), nohold (=0),
off2; runs `E:\OOT\HeyListen\build\s5\tc-texcache2-<arm>`, pictures `E:\OOT\scratch-texcache\shots-tc-texcache2-<arm>`. The main PC
was loaded (scene frames about 30 fps, a 7.9 s frame in off2): no timing is read from this tour.
- Flashes: off1 0, off2 0, nohold 0, hold 5 (presents 5385, 5394, 5601, 5605, 6282). flash_5601_a/b/c: in Kakariko, mid-play,
  frame b has the ground drawn white and the walls behind the gate missing (draws left out by the hold); frames a and c are right.
  Frame a also shows textures served from the disk cache (2393 in that run) drawn correctly.
- hold: held back 2446 textures, 1160 came in, 1028 timed out (transcoded after 400 ms); presents with a texture held 1044; the
  "longest hold" counter (23.4 s) counts until the texture's next lookup, not the time it was missing on screen.
- The stale pool eviction (5 s) made the read-ahead read 9.8 GB (hold) and 4.3 GB (nohold) for 2393 and 543 textures taken, 8416
  and 3829 blocks let go: blocks dropped and read again.
- So texcache3 turns both off by default (RX_VK_BC_DISK_HOLD_MS=0, RX_VK_BC_DISK_POOL_STALE_MS=0); they stay as switches. The hold
  is not acceptable as it is: it leaves draws out wherever a texture is made again, not only at zone entries.

## Why the main PC gains nothing (texcache4, quiet machine, speed lease, 24 Nov 20:08-)

`E:\OOT\scratch-texcache\speedtour.sh` (speed lease, slot c, RAM wait before each run: no other game, 5000 MB free), the same
6-warp tour; runs `E:\OOT\HeyListen\build\s5\st-texcache4-<arm>`. texcache4 lists each cache key under the map it was first wanted
on (`hookshot_texcache_maps.bin`) and reads the entered map's list first. A prime run made the lists (1910 keys, 15 KB).
- Scene frames: off1 128.1 fps, warm1 124.2 (quiet machine; steady fps is VAR1's question, not used here).
- Zone entries, per warp (map line to scene; frames > 50 ms in the 10 s after): off1 16.6 s in all, 28 frames, worst 1083 ms;
  warm1 17.8 s, 28 frames, worst 1668 ms. The same.
- Served: 1551 textures from the cache against 3093 lookups found on disk and transcoded: 33% (texcache1-3: 26-30%). The map
  lists were read (1383 records, 1145 MB) but 2743 read blocks aged out unused.
- Why: the zone's textures are made in a burst in the same present as the "map:" line or the next one (warm1's log: map line
  6476, then at 6486 one frame with 158 transcodes; Kakariko 9008 then 9014 with 251). Nothing a map change or a first texture
  triggers can be read in time; the reads land after the burst and serve only textures made again later.
- What serving the burst would save here: replay-thread CPU per texture in the upload frames (`E:\OOT\scratch-texcache\routes.py`):
  transcode 0.269 ms (off1, 2051 textures) / 0.224 ms (warm1), disk hit 0.105 ms (344); non-ASTC CPU uploads 0.15 ms. Most of a
  hit is hashing the ASTC bytes for the key (1034 ms in warm1's run; the off arm pays it too, the memory BC cache uses the same
  key). The GPU transcode itself is about 1 ms of GPU a frame on this card. So with everything served an entry's upload frames
  would lose about 40% (about 40 ms of a 100 ms frame); the rest is the non-ASTC uploads and the work that is not uploads. Getting
  it needs the blocks in RAM before the burst: holding textures (rejected: draws left out) or reading the next map on a guess.
- The laptop differs: at 10 fps the upload budget's deferral spreads the burst over many presents, and the reads keep up (list 2:
  72-89% served, uploads while turning 16-34 ms a frame against 83-86 off).
- Block 2 (20:35-20:59, same lease rules): zone entries off2 16.9 s / 31 frames > 50 ms, warm2 25.4 s / 49, off3 21.4 s / 72,
  warm3 23.2 s / 46; served 30% and 38%. No entry gain in three pairs.
- OPEN, not explained: scene frames off1/off2/off3 128.1 / 128.8 / 127.3 fps, warm1/warm2/warm3 124.2 / 103.4 / 91.5. The warm
  arms' losses come in episodes in different zones (warm2 HyruleField 51 fps against 123, Market 56 against 98, Kakariko 67 against
  105; warm3 GoronCity 49 against 126-130, Dodongo 77 against 160); there the GPU time is the same (6.3-6.8 ms a frame) and the "gaps
  between recordings" are 11-14 ms against 0.7-1.0: the CPU side waits. The render thread's time in the cache code is 5-7 ms per
  run, no upload runs then, so not the cache's render-thread work. Candidates: memory (working set 18.4 GB at warm2's end against
  16.9 off2, the pool's 1 GB of host-cached buffers, warm3's working set cut from 18.3 to 15.3 GB) or this PC's run-to-run swings
  that VAR1 is hunting. The 3050's list 3 (off against warm, interleaved, quiet) is the test; a short main PC pair with
  RX_VK_BC_DISK_POOL_MB=256 would test the memory candidate.

## Laptop list 1 (texcache1, Vega 8, Hyrule Field camera turning, 24 Nov)

Runs on the laptop `C:\Users\Yanis\Desktop\OOT\runs\amd-1124\TX1_off1, TX2_cold, TX3_warm, TX4_off2, TX5_warm2`; copies of four
logs in `E:\OOT\scratch-texcache\laptop1\`. fps read by the laptop's session (turning 60 s window / first still window):
off 8.1 / 10.2, cold 8.2 / 9.7, warm 10.6 / 11.2, off 8.0 / 10.1, warm 10.4 / 10.0. Turning: +30% warm. No FATAL, device lost.
- GPU uploads a frame while turning ("vk: GPU time"): off 55-125 ms, warm 12-55 ms.
- warm2's line: 1685 textures from the disk (1336 MB), 662 found on disk but not yet read ahead (transcoded), 0 not on disk; render
  thread 11.6 ms in all (the longest call 0.075 ms); read ahead 2093 (1722 MB, 2407 ms on its thread), pool full 310 times, 405
  blocks (383 MB) in the pool at the end, never taken; the index 382 ms (its thread).
- The ms of the "a frame's uploads took" lines are the replay thread's CPU time, not GPU time: "ASTC on the GPU" counted the disk
  hits too in texcache1 (texcache2 gives them their own route, "ASTC from the disk cache"). The 1365x1365 x90 terrain arrays take
  107-112 ms off, 127-142 ms warm2 there: whether they were hits is not in texcache1's log (texcache2 says, and counts the ASTC
  uploads the cache cannot take: part of a texture, BC3/resized, not BC).
- Memory: warm2 working set 9226 MB against 8523 off2, private 11629 against 11120 (the pool's 383 MB and the written blocks'
  buffers); staging arenas made 99 (2297 ms) against 54 (1310 ms). That may be the "on the CPU" growth (1039-1117 ms off,
  1363-1473 warm: non-ASTC uploads, which the cache does not touch): memory pressure on a 15 GB machine. Not proven: texcache2 arm
  with a smaller pool on the laptop.
- Pool: no record was read twice (2093 read = 1685 taken + 405 left + 3 aged out). It filled with records read ahead in the cold
  run's creation order that this session wanted later or never, so 662 on-disk textures came before their run reached them.
  texcache2: a full pool lets go of blocks read ahead more than 5 s ago (RX_VK_BC_DISK_POOL_STALE_MS) for the run a texture asked
  for; ageing by time (RX_VK_BC_DISK_POOL_MS, 60 s), not presents (1800 presents is 3 minutes at 10 fps).
- Writer: the cold run wrote 611 MB in 20 s (background mode: low IO priority) and 220 transcodes found the 256 MB backlog full.
  texcache2: RX_VK_BC_DISK_WRITE_BG=0 writes at normal IO priority (lowest CPU priority), to be tried on the laptop.

## Laptop list 2 (texcache3, 24 Nov evening)

Runs on the laptop `OOT\runs\amd-1124\TX2_1_off .. TX2_8_warm2`; numbers read by the laptop's session (relayed; run-to-run noise
1-2 fps). Turning fps: off 6.3 / 6.9; cold 7.3; cold with normal IO priority 9.5; warm 9.1 / 9.7; bigpool (1024 MB) 11.0;
smallpool (128 MB) 7.1; still 11-13 in every arm. GPU uploads a frame while turning: off 83 / 86, cold 82, cold-io 39, warm 34 / 26,
bigpool 16, smallpool 71 ms. Served from the cache: warm 2732 of 3616 on-disk textures, bigpool 3502 of 3951 (pool never full),
smallpool 718 of 1691. Writes: background mode left 212 transcodes unwritten in the cold run, normal IO priority 2. Working set
+300 to +900 MB against off, with 10.5-11 GB free.
- So in texcache4: the pool's default from the RAM free at start (1024 MB with 8 GB free or more, 512 with 4 GB, else 256), and
  writes at normal IO priority by default (RX_VK_BC_DISK_WRITE_BG=1 for background mode).
- The "on the CPU" growth follows the pool: smallpool served least and was slowest; nothing points at memory pressure at 1024 MB
  on that machine.

## CPU-decode chips (astc_cpu_policy.h)

`bc_ready()` needs `astc_ready()`, which is false when `astc_on_cpu()`: on those chips no texture is BC at all (the GPU BC encoder
is not set up), so this cache (BC blocks) does nothing there. Feasible later, not done: (a) cache the CPU decoder's RGBA8 output:
skips the 10 s decodes but stays RGBA8 and is big (a 1365x1365 x90 array with mips is about 890 MB); (b) BC from a CPU encoder in
the background, written for the next visit, and the image made BC when the disk has its blocks: keeps BC (a quarter of RGBA8),
no GPU burst; needs a CPU BC1/BC7 encoder and a format choice at creation that asks the disk index.

## The default (decided 24 Nov, texcache6)

PC 3050 list 3 (texcache5, matrix96, `E:\OOT\scratch-texcache\3050\matrix96.txt`): fps from play, hyrule-walk off 56.61 / 56.73 /
57.87, warm 61.24 / 59.64 / 61.05 (+6.3% on the means, every warm above every off); castle-move off 63.56 / 64.61 / 64.21, warm
65.18 / 66.80 / 64.70 (+2.2%). No lost-fps episode in any warm arm there (gaps between recordings 2.4-2.9 ms warm, 3.0 off): the
main PC's episodes were the other agents' load. Working set +870 MB (pool 1024 MB, 32 GB machine).
- Rule (texcache_pack.h on_by_default, tested): on for integrated GPUs that transcode ASTC on the GPU and for discrete cards with
  less than 10 GB of VRAM; off for 10 GB and more and for the CPU-decode chips. RX_VK_BC_DISK=0/1 forces it. A start line
  "vk: BC disk cache on|off (by default: <why>; <GPU kind>, <VRAM> MB of VRAM)". No menu row: the options have no place for a
  developer switch of this kind; the env switch stays.
- Disk: cap 4096 MB by default (RX_VK_BC_DISK_MB): a rolling window, not a whole playthrough (a 6-zone tour wrote 2 GB cold, 150 s
  of Hyrule Field 0.75-0.85 GB, so a playthrough's 30-odd zones would be 10 GB or more); the older pack goes when the newer holds
  half, what was used again copied forward. The game folder by default. Never the disk's last GB (room_to_write, checked before
  every record). Any write failure (read-only folder, locked file, full disk) stops the writes for the session with one log line;
  reads go on; a read that fails its hash is transcoded as before; a pack that cannot be read is deleted at the scan.
- Pool by RAM (pool_mb_for_ram): 1024 MB with 24 GB or more, 512 with 14 GB (the 16 GB PCs), 256 below. Why 512 at 16 GB: on the
  15 GB laptop 512 served 87% of the on-disk textures (list 3) and 1024 89% (list 2), the turning fps 9.7-9.9 against 11.0 within
  the 1-2 fps run noise, and the game's working set is already 8.4-11.8 GB there (laptop, 3050): +0.5 GB is not worth 2% more
  served. The 3050 measured with 1024 (32 GB); a 16 GB 3050 PC with 512 is not measured.

## Who gets it by default (proposal, not decided)

Proposal: on by default where the GPU's ASTC transcode is expensive relative to the frame, by device class first:
- integrated GPUs that decode ASTC on the GPU (astc_ready() and VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU: the Vega APUs today):
  measured +30% while turning on the laptop (list 1). On, with a folder size cap.
- discrete cards with 8 GB or less (bc_layers() == 1, the one-layer path: the RTX 3050 class): on only if the 3050's list 2 shows
  warm with fewer or shorter post-zone upload frames and scene fps within noise of off. Not decided.
- everything else (the RTX 5070 class): off. The transcode is about 1 ms of GPU a frame there (bc7c's pass timing); nothing to win.
- CPU-decode chips (astc_cpu_policy.h): nothing to do, the cache holds BC blocks and those chips make none (see above).
A runtime measure (GPU ms per MB of the first transcodes) would be finer, but the class rule needs no timestamps, and the two
classes measured so far differ by device class, not by a few percent.

## State

- 2026-11-25 00:52: texcache6 (commit 68366a529): the cache on by default for integrated GPUs that transcode ASTC and discrete cards
  under 10 GB; 4096 MB rolling cap, the disk's last GB never written, writes off for the session on any write error; pool by RAM.
  Build build/s5/bin/texcache6 (-Block -Lift -Pgo), suite under the build lease: 510 passed, the 3 known failures and
  test_crash_record (passes alone, `tests-texcache6.log` in my scratch: a flake under the parallel run's load).
- Main PC function check (`E:\OOT\scratch-texcache\check6.sh`, runs build/s5/ck-texcache6-<arm>, kokiri-idle, slot b): default
  arm reached play with "vk: BC disk cache off (by default: a discrete GPU with 10 GB of VRAM or more: nothing to gain; discrete
  GPU, 11943 MB of VRAM)"; RX_VK_BC_DISK=1 cold wrote 68 records (263 MB) into an empty folder, the warm start read them (67 read
  ahead, 11 textures from the cache before the run's last counter line), "read-ahead pool 1024 MB (from the machine's RAM)". The
  RX_VK_BC_DISK=0 arm never reached play in 3 starts (0.8 fps: the machine was loaded by other agents' jobs; the default arm, the
  same path, did). Pictures warm against off NOT compared this time (the warm run was too slow to reach the same frames).
- List `E:\OOT\scratch-texcache\list-3050-4.txt`: the default against RX_VK_BC_DISK=0, hyrule-walk and market-spin, after primes.
- Test cache folders `E:\OOT\scratch-texcache\cache` (4.3 GB) and `cache6` (263 MB), mine. No leases, nothing running.
