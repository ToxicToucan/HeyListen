# Imported memory that only grows (issues 131 and 121)

## State (kept current)

- 24 Nov, commit on `fix/import-give-back`: bounded give-back of retired imports on every vendor, plus a give-back when an import
  is refused (`retired_bound.h`, `vk_memory.cpp`). Unit tests in `tests/test_retired_bound.py`. Build `amd2a` (whole suite, then
  mkg). NVIDIA tour and AMD laptop run list: see the end of this file.
- 24 Nov 22:50: main PC tours (RTX 5070, build amd2a, warps, 300 s, bound forced to 1 MB): E:\OOT\HeyListenuild\s5md2v-tiny\game_log.txt:
  19 maps, 49 retired imports (2075 MB) given back by the bounded path, 0 left, 0 FATAL/device lost; pictures identical to the
  immediate-give-back reference. Laptop list 1 (Vega 8, Hyrule Field, E:\OOT\scratch-amd2\A1): all six arms clean, old rule included
  (11 given back), so the old device loss is neither explained nor reproduced.
- Stress hook RX_VK_RETIRE_STRESS (commit 4d967c418, build amd2b queued) and laptop list 2 for hundreds of give-backs.
- 25 Nov: laptop list 2 (stress, RX_VK_RETIRE_STRESS=30, E:\OOT\scratch-amd2\A2): arm 1 (keep) reproduces issue 131 on an AMD
  driver (180 "not imported", 414 retired imports = 6711 MB, draws left out); arms 2-5 (the bound) ~1000 retirements and ~1000
  give-backs, retired left 32-673 MB, no refusal, no device lost; the old rule (arm 6) survived too. The refusal safety net
  (`retired_emergency_free`) was not exercised there (explicit KEEP_RETIRED=1 turns it off; the bound prevented refusals); test
  switch RX_VK_IMPORT_REFUSE_MB (commit b2b74712b) and list-laptop-3.txt exercise it. Not run on the main PC: no build place
  (amd2c withdrawn, the integration build carries it).
- Test hooks, off by default: RX_VK_RETIRE_STRESS, RX_VK_RETIRE_STRESS_N, RX_VK_IMPORT_REFUSE_MB.
- NOT known: why the RX 6750 XT lost the device with PR 85's give-back. Nothing here can show it without an AMD machine; the new
  switches let the laptop tell which of the candidate causes it is.

## Where the two numbers come from (player's log, issue 131, RX 9060 XT, v0.4.3)

`E:\OOT\incoming-logs\issue131\hookshot_log.4.txt`, the "vk: imports:" lines (4392 of them):

| point | live | retired still imported | given back |
|---|---|---|---|
| first line | 6 (183 MB) | 1 (32 MB) | 0 |
| line 4440 | 55 (1860 MB) | 12 (641 MB) | 0 |
| middle | 100 (3751 MB) | 36 (1982 MB) | 0 |
| last | 214 (7247 MB) | 94 (5643 MB) | 0 |

Total 12.9 GB. Then "pool ... not imported (VkResult -1000072003)" from line 355129 and every later import is refused.

**Live imports** (`imported`, the pool table) leave it by only two doors in v0.4.3: a larger import takes them in (they are
retired), or the idle give-back (off on AMD). A "chunk" (an import of up to 16 MB around a binding) of a zone the player left stays
in the table for good: nothing tells the back end the game's pools of that chunk ended (`RX_VK_POOL_FINALIZE` was off). So live
imports grow with every zone visited, whether or not the game still holds the memory. The log shows exactly this: steps up at
zone changes, flat while playing one place (lines 31951-37786: 100 live, 36 retired, unchanged for thousands of lines).
That is issue 121's finding (233 imports, 6.8 GB at 155,000 frames) and the reason of the accumulation sweep of 22 Nov
(`accum_sweep`, `RX_VK_ACCUM_RETIRE`, on by default since e65826e38; the v0.4.3 log has no "chunks with no live game pool" line, so
the player's build predates it).

**Retired imports** are the chunks taken in by a larger import, and (with the sweep) the chunks of zones left. On AMD and Intel on
Windows `retired_imports_give_back` keeps them for good (`keep`), so they grow too: 94 retired = every remake-larger plus every
swept chunk of a 4 h session.

Consequences:

1. With the sweep on (main) and the give-back off (AMD default), the sweep only moves bytes from "live" to "retired": the total
   pinned stays the same. On AMD the sweep alone does not bound anything. This is why the fix has to be the give-back.
2. `RX_VK_AMD_GIVE_BACK=1` (the reporter's try, 24 Nov) does more than give retired imports back: it turns on the idle give-back
   of LIVE imports (`imports_give_back`: past 70% of the system heap budget down to 60%, pools with no mirror idle for 600
   submissions are evicted: table entries zeroed, import freed, re-imported at the next binding). On v0.4.3 (no sweep) the live
   imports still grow; the evictions of pools the game still uses cost re-imports and leave the page table at 0 for a while:
   that is the likely source of the white flashes and of the faster deterioration. The new code does not touch that rule.

## What the new code does

`retired_imports_give_back` (every submission, `pools_mu` held):

- Where retired imports used to be kept (AMD and Intel on Windows): they are bounded. Nothing goes back while the retired imported
  bytes are at or under `RX_VK_RETIRED_BOUND_MB` (default 1024). Past it, oldest first, at most 2 a submission
  (`RX_VK_RETIRED_BOUND_STEP`), a retired import that is idle (no mirror, no inheritance pending, not lazy-held, the GPU past
  `retired_value` and `last_value`: the same test as the immediate give-back) and at least 600 submissions old
  (`RX_VK_RETIRED_BOUND_AGE`). The ones that overlap a live import (a larger live pool took them in: their pages stay pinned by the
  live one whatever happens, and freeing one next to a live import of the same pages is the doubtful case for a driver) go only past
  twice the bound (`RX_VK_RETIRED_BOUND_OVERLAP_MB`; a huge value: never). Pure rule in `retired_bound.h`, tested without a GPU.
- The free itself waits `RX_VK_RETIRED_BOUND_FLIGHT` (8) submissions after the GPU is past the import (`DeadImport::not_before`),
  on top of the timeline value, so a frame in flight, a descriptor or a table copy that the value does not name cannot meet a freed
  import.
- NVIDIA and Linux keep their immediate give-back unless `RX_VK_RETIRED_BOUND_MB` is set (then the bounded path replaces it: this is
  how the path is exercised on the NVIDIA main PC). `RX_VK_KEEP_RETIRED=1` keeps everything as before; `RX_VK_RETIRED_BOUND_MB=0`
  restores the old vendor rule.
- A refused import (VK_ERROR_INVALID_EXTERNAL_HANDLE, out of host or device memory) at a pool's import: `retired_emergency_free`
  gives idle retired imports back at once (and the free imports waiting for their frames), the hold on that range is dropped, the
  import is tried again once. "refused imports helped ..." in the imports line counts it. `RX_VK_IMPORT_EMERGENCY=0`: off.
  This is the small fallback; a mirror-instead fallback is not done (a mirror needs a pool entry with no import, which is
  `RX_VK_POOL_MIRROR`, off by default and not ready for all bindings).

Idle give-back of live imports (`imports_give_back`, `RX_VK_AMD_GIVE_BACK`) is unchanged and stays off on AMD.

## What is not known

The 6750 XT's device loss. Candidates the laptop arms separate: (1) an import freed while a submission still uses it (the
timeline value is not the whole story: shaders reach imports through the page table); the flight margin tests it. (2) freeing an
import whose pages another live import (the larger pool) also pins: the overlap rule tests it. (3) the idle live eviction of
`RX_VK_AMD_GIVE_BACK`, which this code does not use. If arm 3 of the list loses the device with the margin and no overlapped
frees, it is the driver freeing any import; then the bound has to be a count of pinned bytes the driver accepts and nothing more.
