<!-- Copyright (C) 2026 Sacrappy -->

# STALL1 (22 Nov 2026): what the long os_wait "lock" hitches and the working-set falls are

Main PC, RTX 5070, build stall1 (commit caa9ea60e), Hyrule Field `castle-move` and Market `market-spin`, `HL_STALL_WATCH=40`
(host/hos_jobtrace.c: a ring of the job trace and a dump of every gap between presents).

- Clean machine (speed lease, nothing else running): Hyrule Field 123.6 fps, 4 frames over 50 ms in 144 s, 1 over 100, 0 over 500;
  one "lock" hitch (53 ms). Market 88.8 fps, 10 over 50 ms, 1 over 100, 2 "lock" hitches (142 ms in all). The long "lock" waits are
  not there without memory trouble.
- Under memory pressure (a fixed 6 GB hog, free RAM near 2 GB, or the machine shared by other games) the same scene gave up to 57
  "lock" hitches of 50-270 ms (6 s) and a 2.2 s frame, with 110,000 to 350,000 page faults and a working set that moved by -434 to
  +1166 MB in the gap.
- In every long gap nearly all of the 100 guest threads, the presenting thread among them, sit in a wait (ntdll+0x1659c4, the
  condition-variable / address wait). The presenting thread waits for the frame hand-off queue (guest caller 0x834a3084, main thread
  -> presenting thread), and the main thread (or a loader: ARes_Loading_*, host:mkw_vk_order) is the one running, taking page
  faults, or reading a file (ntdll+0x161e14). The wait is not a lock held by another thread: it is the pipeline waiting for the one
  thread that is faulting.
- HL_MEM_COLD is not the cause: its budget is 64 MB a pass per kind, a fall of 300-450 MB in a frame is Windows' (system short of
  RAM: the game's working set went to 0.5-1.1 GB while 0.1-1 GB of 32 GB was free), and runs with `HL_MEM_COLD=0 HL_MEM_COLD_HEAPS=0`
  were not better (clean machine 10 hitches over 50 ms, with pressure 10 against 8).
- The start of play (first 3 s of a zone) is the same: the loader and `mkw_vk_order` threads first-touch ~300,000 pages (+784 MB) in
  1.1 s while the others wait.
