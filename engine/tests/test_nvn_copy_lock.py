"""The recordings' copy lock is held for less, with the same copies kept and let go (host/nvn_native.c; no game/GPU).

From Hookshot d1ce8a4cd: check_handle reads the live-handle set without copy_lock first (a miss is asked again under
the lock), free_dead_copies returns at once until the replay has passed the oldest copy let go (dead_first), and
sweep_overwritten_copies goes over SWEEP_SLICE of the buckets every SWEEP_EVERY recordings instead of all of them at
once every 25,000. The copies' tables (rec_node to the end of sweep_overwritten_copies) and check_handle are cut out of
the source and compiled with stubs for the host (the SRW lock as a pthread rwlock, the replay's counters, guest memory):

- 3000 copies marked in their control memory, a third of the marks overwritten by the game: after one round of slices
  (every bucket once) exactly that third is let go, the rest still live, and both reads of the set agree;
- nothing let go is freed before the replay passes it, and all of it once it does (dead_first then back to none);
- check_handle accepts every live copy.

python -I -B tests/test_nvn_copy_lock.py [--cc gcc]
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

STUBS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <pthread.h>
typedef int64_t LONG64;
typedef pthread_rwlock_t SRWLOCK;
#define SRWLOCK_INIT PTHREAD_RWLOCK_INITIALIZER
static void AcquireSRWLockExclusive(SRWLOCK *l) { pthread_rwlock_wrlock(l); }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { pthread_rwlock_unlock(l); }
static void AcquireSRWLockShared(SRWLOCK *l) { pthread_rwlock_rdlock(l); }
static void ReleaseSRWLockShared(SRWLOCK *l) { pthread_rwlock_unlock(l); }
static LONG64 InterlockedAdd64(volatile LONG64 *p, LONG64 v) { return __atomic_add_fetch(p, v, __ATOMIC_SEQ_CST); }
static void die(const char *fmt, ...) { va_list a; va_start(a, fmt); vfprintf(stderr, fmt, a); va_end(a); fputc('\n', stderr); exit(3); }
static void hos_log(const char *fmt, ...) { (void)fmt; }
static void *hos_xmalloc(size_t n, const char *w) { (void)w; void *p = malloc(n); if (!p) exit(3); return p; }
static void *hos_xcalloc(size_t c, size_t n, const char *w) { (void)w; void *p = calloc(c, n); if (!p) exit(3); return p; }
static void *hos_xrealloc(void *o, size_t n, const char *w) { (void)w; void *p = realloc(o, n); if (!p) exit(3); return p; }
static int env_on(const char *n) { return getenv(n) != NULL; }
static uint64_t replayed, pushed;
static uint64_t rx_nvn_replayed(void) { return replayed; }
static uint64_t rx_nvn_pushed(void) { return pushed; }
static uint64_t held_count(void) { return 0; }
static int freed;
static void pool_put(void *b, unsigned cls) { (void)cls; free(b); freed++; }
/* (guest memory: one 64-byte block of control memory a 64 KiB block, the copies' bucket unit, so that they spread over
   the buckets and every slice has some) */
static uint8_t guest[4096 * 64];
static void *hos_guest_range(uint64_t a, uint64_t n) {
    return (a & 0xffff) + n <= 64 && (a >> 16) < 4096 ? guest + (a >> 16) * 64 + (a & 0xffff) : NULL;
}
'''

MAIN = r'''
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); return 1; } while (0)
int main(void) {
    rec_buckets = (rec_node **)calloc(REC_BUCKETS, sizeof *rec_buckets);
    static uint8_t *copies[3000];
    for (int i = 0; i < 3000; i++) {
        copy_prefix *p = (copy_prefix *)calloc(1, sizeof *p + 64);
        p->magic = COPY_MAGIC, p->bytes = 64;
        copies[i] = (uint8_t *)(p + 1);
        const uint64_t ctl = (uint64_t)i << 16;
        rec_node *n = (rec_node *)calloc(1, sizeof *n);
        n->ctl = ctl, n->copy = copies[i], n->marked = 1;
        const unsigned b = rec_bucket(ctl);
        n->next = rec_buckets[b], rec_buckets[b] = n;
        const uint64_t mark[2] = {COPY_MARK, (uint64_t)(uintptr_t)copies[i]};
        memcpy(hos_guest_range(ctl, sizeof mark), mark, sizeof mark);
        AcquireSRWLockExclusive(&copy_lock);
        live_handle_add((uint64_t)(uintptr_t)copies[i]);
        ReleaseSRWLockExclusive(&copy_lock);
        ncopies++;
    }
    for (int i = 0; i < 3000; i += 3) memset(hos_guest_range((uint64_t)i << 16, 16), 0x55, 16);   /* (the game reused it) */
    pushed = 10;
    const unsigned sweeps = (REC_BUCKETS + SWEEP_SLICE - 1) / SWEEP_SLICE;
    if ((unsigned long long)sweeps * SWEEP_EVERY > 26000) FAIL("a bucket is swept less often than every ~25,000 recordings");
    AcquireSRWLockExclusive(&copy_lock);
    sweep_overwritten_copies();   /* (one slice: some of the overwritten copies, not all) */
    ReleaseSRWLockExclusive(&copy_lock);
    if (!ndead || ndead >= 1000) FAIL("one slice let go %zu of the 1000 (a slice is not a part of the buckets)", ndead);
    for (unsigned k = 1; k < sweeps; k++) {   /* (the rest of the round: every bucket seen once) */
        AcquireSRWLockExclusive(&copy_lock);
        sweep_overwritten_copies();
        ReleaseSRWLockExclusive(&copy_lock);
    }
    if (ncopies != 2000 || ndead != 1000) FAIL("one round of slices: %zu live, %zu let go (want 2000, 1000)", ncopies, ndead);
    for (int i = 0; i < 3000; i++) {
        const uint64_t h = (uint64_t)(uintptr_t)copies[i];
        const int live = i % 3 != 0;
        if (live_handle_seen(h) != live || live_handle_has(h) != live) FAIL("copy %d: the set's two reads disagree", i);
        if (live) check_handle(h);
    }
    AcquireSRWLockExclusive(&copy_lock);
    replayed = 5, free_dead_copies();
    if (freed || dead_first != 10) FAIL("freed before the replay passed them: %d (first %llu)", freed, (unsigned long long)dead_first);
    replayed = 10, free_dead_copies();
    ReleaseSRWLockExclusive(&copy_lock);
    if (freed != 1000 || ndead || dead_first != UINT64_MAX) FAIL("once passed: %d freed, %zu left", freed, ndead);
    puts("ok");
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    a = p.parse_args()
    cc = shutil.which(a.cc)
    if not cc:
        print("SKIP: no C compiler " + a.cc)
        return
    src = (ROOT / "host" / "nvn_native.c").read_text(encoding="utf-8")
    start = src.index("typedef struct rec_node {")
    tables = src[start:src.index("NV(nvnCommandBufferAddControlMemory)", start)]
    ch = src.index("static void check_handle(uint64_t h) {")
    check = src[ch:src.index("\n}\n", ch) + 3]
    if "live_handle_seen(h)" not in check:
        raise SystemExit("check_handle takes copy_lock before it looks: Hookshot d1ce8a4cd's lock-free first read is gone")
    if "recording % SWEEP_EVERY == 0 ? sweep_overwritten_copies() : 0;" not in src:
        raise SystemExit("EndRecording no longer sweeps a slice every SWEEP_EVERY recordings")
    with tempfile.TemporaryDirectory(prefix="nvn-copy-lock-") as tmp:
        c, exe = Path(tmp) / "t.c", Path(tmp) / "t"
        c.write_text(STUBS + tables + check + MAIN, encoding="utf-8")
        subprocess.run([cc, "-std=gnu11", "-D_GNU_SOURCE", "-O1", "-Wall", "-Wno-unused-function", "-Wno-unused-variable",
                        "-pthread", str(c), "-o", str(exe)], check=True)
        r = subprocess.run([str(exe)], text=True, capture_output=True, timeout=60)
        if r.returncode or r.stdout.strip() != "ok":
            raise SystemExit(r.stderr or r.stdout or "exit %d" % r.returncode)
    print("PASS: the copy tables keep and let go the same copies, with the lock-free read, the dead list's first and the swept slices")


if __name__ == "__main__":
    main()
