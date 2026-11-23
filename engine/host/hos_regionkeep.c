// Copyright (C) 2026 Sacrappy

/* (STALL2, 22 Nov 2026) HL_REGION_KEEP=<seconds> (off by default; 0 off): a freed address region is not discarded at once.
   The owner's session and the step-0 runs showed the churn: when the game frees a zone's address region (nn::os::
   FreeAddressRegion), hos_mem.c discards its pages (DiscardVirtualMemory: out of the working set, back to demand-zero), and the
   next zone's loaders (ARes_Loading_*) first-touch the same address space again: in the Market 3.47 GB discarded in 63 s of play,
   372 K pages (1.45 GB) faulted straight back, 84 of the 130 stutters. Windows spends about 10 us a fault when the PC is short of RAM.
   Here a freed region above the heap goes on a keep list with the time it was freed; its resident pages stay resident.
   - Re-allocated (hos_region_alloc over the same address space, even a part of it): the new region must read as zero (the game
     maps pages in it with AllocateMemoryPages, whose commit leaves committed pages as they are), so the overlap is zeroed here, on
     the caller's thread (the game's resource memory thread), by memset over the resident pages (about 0.1 ms a MB, against 2.5 ms
     a MB of faults) and a discard of the committed pages that are not resident (they may hold stale data in the page file).
     That memset is the cost; no fault is taken by a loader, and nothing is read by the game that it did not write.
   - Not re-allocated for <seconds> (try 20): discarded as before, by a thread of its own (1 s granularity).
   - The list is capped at HL_REGION_KEEP_MB resident megabytes (default 2048): over it the oldest are discarded first. While the
     system's available RAM is under 1024 MB (HL_REGION_KEEP_LOWMEM_MB) the oldest are discarded too, one after another until it
     is not.
   - A region with pages the Vulkan back end imported, one that touches the guest-pages substrate (HOS_GUEST_PAGES) and one with
     under 1 MB resident are discarded at once, as before.
   One line a second when anything moved: "hl keep: ...": kept, reused and discarded MB (aged, over the cap, under pressure) and
   what is on the list. Windows only; elsewhere the functions do nothing. */
#ifndef HK_TEST
#include "hos.h"
#include "hos_guestpages.h"
#else
#include "hk_test_stubs.h"   /* (tests/test_region_keep.py: the same file built against a small window) */
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32) && !defined(__EMSCRIPTEN__)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

typedef BOOL(WINAPI *KeepQueryWsFn)(HANDLE, PVOID, DWORD);
typedef DWORD(WINAPI *KeepDiscardFn)(PVOID, SIZE_T);
typedef struct { PVOID va; ULONG_PTR attr; } KeepWs;
typedef struct { uint64_t lo, hi, bytes; int64_t t; int busy; } Kept;
enum { KEEP_MAX = 256 };
static Kept kept[KEEP_MAX];
static int nkept;
static uint64_t kept_bytes;
static SRWLOCK klock = SRWLOCK_INIT;
static int keep_secs = -1;
static uint64_t keep_cap, keep_low_mb;
static KeepQueryWsFn query_ws;
static KeepDiscardFn discard_fn;
static volatile LONG64 c_kept, c_reused, c_age, c_cap, c_press, c_zeroed_ns;
static volatile LONG started;

int hos_keep_enabled(void) {
    if (keep_secs < 0) {
        const char *e = getenv("HL_REGION_KEEP");
        const char *c = getenv("HL_REGION_KEEP_MB"), *l = getenv("HL_REGION_KEEP_LOWMEM_MB");
        keep_cap = (uint64_t)(c && c[0] ? atoll(c) : 2048) << 20;
        keep_low_mb = (uint64_t)(l && l[0] ? atoll(l) : 1024);
        /* (20 s by default since 2026-11-22: a warp tour Market <-> Hyrule Field, 4 zone changes, A B B A: the RTX 3050 PC with
           10-15 GB free gained 1.5 fps, -20% frames over 25 ms, -10% over 50 ms, longest stall 0.8 s against 1.1-1.3 s; the
           RTX 5070 PC under memory pressure -19% stutters after a zone change; 5-6 GB of discards a tour gone) */
        keep_secs = e && e[0] ? (atoi(e) > 0 ? atoi(e) : 0) : 20;
        HMODULE k32 = GetModuleHandleA("kernel32.dll");
        query_ws = (KeepQueryWsFn)(void *)GetProcAddress(k32, "K32QueryWorkingSetEx");
        discard_fn = (KeepDiscardFn)(void *)GetProcAddress(k32, "DiscardVirtualMemory");
        if (keep_secs && !query_ws) {
            hos_log("hl keep: HL_REGION_KEEP: no QueryWorkingSetEx here: off");
            keep_secs = 0;
        } else if (keep_secs)
            hos_log("hl keep: on: a freed address region is kept for %d s (re-allocated: zeroed here, no discard, no fault-back); at most %llu MB resident, "
                    "discarded oldest first under %llu MB of free RAM (HL_REGION_KEEP=0: off)",
                    keep_secs, (unsigned long long)(keep_cap >> 20), (unsigned long long)keep_low_mb);
    }
    return keep_secs > 0;
}

/* the pages of [lo, hi) of the window that are committed and writable, as runs: {a, b} of host addresses; resident is how many of
   them are in the working set. Used by the keep and the zeroing */
static uint64_t keep_resident(uint8_t *lo, uint8_t *hi) {
    uint64_t resident = 0;
    uint8_t *at = lo;
    MEMORY_BASIC_INFORMATION mbi;
    static _Thread_local KeepWs ws[1024];
    while (at < hi && VirtualQuery(at, &mbi, sizeof mbi)) {
        uint8_t *end = (uint8_t *)mbi.BaseAddress + mbi.RegionSize;
        if (end > hi) end = hi;
        if (mbi.State == MEM_COMMIT && (mbi.Protect & (PAGE_READWRITE | PAGE_EXECUTE_READWRITE)) && !(mbi.Protect & PAGE_GUARD)) {
            for (uint8_t *p = at; p < end;) {
                int n = 0;
                for (; n < 1024 && p + (size_t)n * 4096 < end; n++) ws[n].va = p + (size_t)n * 4096;
                if (!query_ws(GetCurrentProcess(), ws, (DWORD)(n * sizeof ws[0]))) return resident;
                for (int i = 0; i < n; i++) resident += (ws[i].attr & 1) ? 4096 : 0;
                p += (size_t)n * 4096;
            }
        }
        at = end;
    }
    return resident;
}

void hos_keep_discard(uint64_t addr, uint64_t size);   /* (hos_mem.c: discard_pages; the test's own) */

static void keep_discard_run(uint8_t *a, uint8_t *b) {
    if (!discard_fn || discard_fn(a, (SIZE_T)(b - a)) != ERROR_SUCCESS) VirtualAlloc(a, (SIZE_T)(b - a), MEM_RESET, PAGE_READWRITE);
}

/* make [lo, hi) read as zero: the committed pages that are resident are written with zeros (no fault), the ones that are not are
   discarded (their backing may hold the old bytes) */
static void keep_zero(uint8_t *lo, uint8_t *hi) {
    uint8_t *at = lo;
    MEMORY_BASIC_INFORMATION mbi;
    static _Thread_local KeepWs ws[1024];
    while (at < hi && VirtualQuery(at, &mbi, sizeof mbi)) {
        uint8_t *end = (uint8_t *)mbi.BaseAddress + mbi.RegionSize;
        if (end > hi) end = hi;
        if (mbi.State == MEM_COMMIT && (mbi.Protect & (PAGE_READWRITE | PAGE_EXECUTE_READWRITE)) && !(mbi.Protect & PAGE_GUARD)) {
            for (uint8_t *p = at; p < end;) {
                int n = 0;
                for (; n < 1024 && p + (size_t)n * 4096 < end; n++) ws[n].va = p + (size_t)n * 4096;
                if (!query_ws(GetCurrentProcess(), ws, (DWORD)(n * sizeof ws[0]))) {
                    keep_discard_run(p, p + (size_t)n * 4096);   /* (unknown: the safe way to zero) */
                } else {
                    int i = 0;
                    while (i < n) {
                        const int res = (ws[i].attr & 1) != 0;
                        int j = i;
                        while (j < n && ((ws[j].attr & 1) != 0) == res) j++;
                        uint8_t *a = p + (size_t)i * 4096, *b = p + (size_t)j * 4096;
                        if (res) memset(a, 0, (size_t)(b - a));
                        else keep_discard_run(a, b);
                        i = j;
                    }
                }
                p += (size_t)n * 4096;
            }
        }
        at = end;
    }
}

/* take an entry off the list (klock held by the caller) */
static void keep_remove_locked(int i) {
    kept_bytes -= kept[i].bytes < kept_bytes ? kept[i].bytes : kept_bytes;
    kept[i] = kept[--nkept];
}

/* discard the entry that is oldest (busy ones left to their owner); 0 when there is none. why: 0 aged, 1 cap, 2 pressure */
static int keep_discard_oldest(int why, int64_t only_older_than) {
    AcquireSRWLockExclusive(&klock);
    int at = -1;
    for (int i = 0; i < nkept; i++)
        if (!kept[i].busy && (at < 0 || kept[i].t < kept[at].t)) at = i;
    if (at < 0 || (only_older_than && kept[at].t > only_older_than)) {
        ReleaseSRWLockExclusive(&klock);
        return 0;
    }
    kept[at].busy = 1;   /* (hos_keep_reuse waits for it) */
    const uint64_t lo = kept[at].lo, hi = kept[at].hi, bytes = kept[at].bytes;
    ReleaseSRWLockExclusive(&klock);
    hos_keep_discard(lo, hi - lo);
    AcquireSRWLockExclusive(&klock);
    for (int i = 0; i < nkept; i++)
        if (kept[i].lo == lo && kept[i].hi == hi && kept[i].busy) { keep_remove_locked(i); break; }
    ReleaseSRWLockExclusive(&klock);
    InterlockedAdd64(why == 0 ? &c_age : why == 1 ? &c_cap : &c_press, (LONG64)bytes);
    return 1;
}

static DWORD WINAPI keep_main(void *unused) {
    (void)unused;
    uint64_t last[6] = {0};
    for (;;) {
        Sleep(1000);
        const int64_t now = hos_now_ns();
        while (keep_discard_oldest(0, now - (int64_t)keep_secs * 1000000000LL)) {}
        for (;;) {
            MEMORYSTATUSEX ms;
            memset(&ms, 0, sizeof ms);
            ms.dwLength = sizeof ms;
            if (!GlobalMemoryStatusEx(&ms) || ms.ullAvailPhys >> 20 >= keep_low_mb || !keep_discard_oldest(2, 0)) break;
        }
        const uint64_t cur[6] = {(uint64_t)c_kept, (uint64_t)c_reused, (uint64_t)c_age, (uint64_t)c_cap, (uint64_t)c_press, (uint64_t)c_zeroed_ns};
        if (memcmp(cur, last, sizeof cur)) {
            AcquireSRWLockShared(&klock);
            const uint64_t on_list = kept_bytes;
            const int n = nkept;
            ReleaseSRWLockShared(&klock);
            hos_log("hl keep: this second: kept %llu MB, reused %llu MB (zeroed in %.1f ms), discarded %llu MB (aged %llu, over the cap %llu, "
                    "under pressure %llu); on the list: %llu MB resident in %d regions",
                    (unsigned long long)((cur[0] - last[0]) >> 20), (unsigned long long)((cur[1] - last[1]) >> 20), (double)(cur[5] - last[5]) / 1e6,
                    (unsigned long long)((cur[2] - last[2] + cur[3] - last[3] + cur[4] - last[4]) >> 20), (unsigned long long)((cur[2] - last[2]) >> 20),
                    (unsigned long long)((cur[3] - last[3]) >> 20), (unsigned long long)((cur[4] - last[4]) >> 20), (unsigned long long)(on_list >> 20), n);
            memcpy(last, cur, sizeof last);
        }
    }
    return 0;
}

int hos_keep_try(uint64_t addr, uint64_t size) {
    if (!hos_keep_enabled() || !size) return 0;
    if (hos_gp_ready()) return 0;   /* (the substrate splits the reservation: its own discard) */
    extern int rx_guest_imported(uint64_t lo, uint64_t hi);
    if (rx_guest_imported(addr, addr + size)) return 0;
    uint8_t *lo = (uint8_t *)GP(addr);
    const uint64_t resident = keep_resident(lo, lo + size);
    if (resident < (1ULL << 20)) return 0;
    if (InterlockedCompareExchange(&started, 1, 0) == 0) {
        HANDLE t = CreateThread(NULL, 0, keep_main, NULL, 0, NULL);
        if (t) { SetThreadPriority(t, THREAD_PRIORITY_BELOW_NORMAL); CloseHandle(t); }
    }
    AcquireSRWLockExclusive(&klock);
    if (nkept == KEEP_MAX) {
        ReleaseSRWLockExclusive(&klock);
        return 0;
    }
    kept[nkept++] = (Kept){addr, addr + size, resident, hos_now_ns(), 0};
    kept_bytes += resident;
    ReleaseSRWLockExclusive(&klock);
    InterlockedAdd64(&c_kept, (LONG64)resident);
    while (kept_bytes > keep_cap && keep_discard_oldest(1, 0)) {}
    return 1;
}

void hos_keep_reuse(uint64_t lo, uint64_t hi) {
    if (keep_secs <= 0 || !nkept) return;
    for (;;) {
        uint64_t zlo = 0, zhi = 0, share = 0, dlo = 0, dhi = 0;
        int wait = 0;
        AcquireSRWLockExclusive(&klock);
        for (int i = 0; i < nkept; i++) {
            if (kept[i].hi <= lo || kept[i].lo >= hi) continue;
            if (kept[i].busy) { wait = 1; break; }   /* (being discarded by the thread: the address space is not ours till it is done) */
            const uint64_t a = kept[i].lo > lo ? kept[i].lo : lo, b = kept[i].hi < hi ? kept[i].hi : hi;
            const uint64_t whole = kept[i].hi - kept[i].lo;
            share = (uint64_t)((double)kept[i].bytes * (double)(b - a) / (double)whole);
            zlo = a, zhi = b;
            /* the part of the entry that is left stays kept (a part under and a part over the new region: the second is a new entry) */
            const uint64_t olo = kept[i].lo, ohi = kept[i].hi, obytes = kept[i].bytes;
            const int64_t ot = kept[i].t;
            if (olo >= a && ohi <= b) keep_remove_locked(i);
            else {
                kept_bytes -= share < kept_bytes ? share : kept_bytes;
                if (olo < a && ohi > b) {
                    kept[i].hi = a;
                    kept[i].bytes = (uint64_t)((double)obytes * (double)(a - olo) / (double)whole);
                    const uint64_t rbytes = (uint64_t)((double)obytes * (double)(ohi - b) / (double)whole);
                    if (nkept < KEEP_MAX) kept[nkept++] = (Kept){b, ohi, rbytes, ot, 0};
                    else kept_bytes -= rbytes < kept_bytes ? rbytes : kept_bytes, dlo = b, dhi = ohi;   /* (no room: discarded below) */
                } else if (olo < a) kept[i].hi = a, kept[i].bytes = obytes - share;
                else kept[i].lo = b, kept[i].bytes = obytes - share;
            }
            break;
        }
        ReleaseSRWLockExclusive(&klock);
        if (wait) { SwitchToThread(); continue; }
        if (dhi) hos_keep_discard(dlo, dhi - dlo);
        if (!zhi) return;
        const int64_t t0 = hos_now_ns();
        keep_zero((uint8_t *)GP(zlo), (uint8_t *)GP(zhi));
        HOS_WRITTEN(GP(zlo), zhi - zlo);
        InterlockedAdd64(&c_zeroed_ns, hos_now_ns() - t0);
        InterlockedAdd64(&c_reused, (LONG64)share);
    }
}
#else
int hos_keep_enabled(void) { return 0; }
int hos_keep_try(uint64_t addr, uint64_t size) { (void)addr; (void)size; return 0; }
void hos_keep_reuse(uint64_t lo, uint64_t hi) { (void)lo; (void)hi; }
#endif
