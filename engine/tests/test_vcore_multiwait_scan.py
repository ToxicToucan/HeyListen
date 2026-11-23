"""WaitAny's scan makes no virtual-core checkpoint while it holds mw_lock (host/nn_os_sync.c wait_any, holder_ready,
obj_quiet).

Every nn::os object lookup (obj_any) is a checkpoint: a thread asked to give its core to a better waiter does it there
(vc_yield: the core given up, then a wait for one). WaitAny's scan looked its holders' objects up with mw_lock held, so a
thread could wait for a core while holding mw_lock, and every holder of a core that then signalled an event, sent or
received on a message queue or entered WaitAny waited for mw_lock with its core: nothing ran on those cores until the
250-ms escape (Linux, RX 9070 XT: every escape at a 120 fps cap's boot, the holders all in poke_multiwait or in
hos_cv_sleep taking mw_lock again). The actual obj, obj_any, obj_quiet, holder_ready and wait_any are compiled with
stand-ins that count the checkpoints made while mw_lock is held:

- a WaitAny whose event holder is ready, one that sleeps once until a semaphore holder is ready, and a TryWaitAny with
  nothing ready, over event, timer, semaphore and queue holders: no checkpoint under mw_lock, and still one at the
  call (WaitAny is an nn::os call, where a better waiter may take the core);
- hos_event_signal and hos_system_event_signal keep their checkpoint: guest threads call them through the HLE, and a
  build without it ran Kokiri 3-4% slower (uncapped, 6 paired rounds).

python -I -B tests/test_vcore_multiwait_scan.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def block(source, marker):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def through(source, marker, last):
    """from marker to the end of the line holding last (after marker)"""
    start = source.index(marker)
    end = source.index("\n", source.index(last, start))
    return source[start:end]


def line(source, marker):
    start = source.index(marker)
    return source[start:source.index("\n", start)]


HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef long LONG;
typedef unsigned long DWORD;
typedef struct { int locked; } SRWLOCK;
typedef struct { int x; } CONDITION_VARIABLE;
typedef struct HosThread HosThread;
#define INFINITE 0xFFFFFFFFul
#define GP(a) ((void *)(uintptr_t)(a))
#define HOS_JT(t, a, b) ((void)0)
static uint64_t hos_jt_lr(void) { return 0; }
static int64_t clock_ns = 1000000000;
static int64_t hos_now_ns(void) { return clock_ns; }
static LONG InterlockedIncrement(volatile LONG *p) { return ++*p; }
static LONG InterlockedDecrement(volatile LONG *p) { return --*p; }
static void AcquireSRWLockExclusive(SRWLOCK *l) { l->locked = 1; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { l->locked = 0; }

@TYPES@

static SRWLOCK mw_lock;
static CONDITION_VARIABLE mw_cv;
static volatile LONG mw_waiting;
/* the checkpoint (host/hos_thread.c hos_vcore_checkpoint: a better waiter's request makes it vc_yield, a wait for a
   core): counted, and counted again when made with mw_lock held */
static int checkpoints, under_mw_lock;
void hos_vcore_checkpoint(void) {
    checkpoints++;
    if (mw_lock.locked) under_mw_lock++;
}
/* the shared table (obj_lookup): one object per address, made on first sight */
static Obj objs[64];
static int nobjs;
static Obj *obj_lookup(uint64_t key, int kind) {
    for (int i = 0; i < nobjs; i++)
        if (objs[i].key == key) { objs[i].kind = kind; return &objs[i]; }
    Obj *o = &objs[nobjs++];
    o->key = key, o->kind = kind;
    return o;
}
/* an event's peek (consumes an auto-clear signal: WaitAny's scan, nn_os_sync.c event_peek) */
static int event_peek(Obj *e) {
    const int got = e->signaled;
    if (got && e->autoclear) e->signaled = 0;
    return got;
}
static int event_wait(Obj *e, int64_t timeout_ns, int try_only) {
    (void)timeout_ns, (void)try_only;
    return event_peek(e);
}
static int signals;
static void event_signal(Obj *e) { e->signaled = 1, signals++; }
/* a sleep on mw_cv: mw_lock let go meanwhile; the semaphore 0x5000 is released while it sleeps */
static int sleeps;
static int hos_cv_sleep(void *cv, void *lk, unsigned ms) {
    (void)cv, (void)ms;
    sleeps++;
    ((SRWLOCK *)lk)->locked = 0;
    obj_lookup(0x5000, K_SEMA)->sem = 1;
    clock_ns += 1000000;
    ((SRWLOCK *)lk)->locked = 1;
    return 1;
}

@FUNCS@

static int bad;
static void check(int ok, const char *what) { if (!ok) { printf("FAIL: %s\n", what); bad = 1; } }
/* a multi-wait 0x100 with holders 0x200.. over: an event 0x1000, a timer 0x2000 (due later), a semaphore 0x5000, a queue
   0x6000 (empty) */
static void setup(int event_set) {
    memset(objs, 0, sizeof objs), nobjs = 0;
    Obj *mw = obj_lookup(0x100, K_MULTIWAIT);
    static Obj *links[8];
    const struct { uint64_t key; int what; uint64_t target; } hs[] = {
        {0x200, H_EVENT, 0x1000}, {0x210, H_TIMER, 0x2000}, {0x220, H_SEMA, 0x5000}, {0x230, H_QUEUE_NOT_EMPTY, 0x6000}};
    for (int i = 0; i < 4; i++) {
        Obj *h = obj_lookup(hs[i].key, K_HOLDER);
        h->sem_max = hs[i].what, h->event = hs[i].target;
        links[i] = h;
    }
    mw->links = links, mw->nlinks = 4;
    Obj *e = obj_lookup(0x1000, K_EVENT);
    e->signaled = event_set, e->autoclear = 1;
    obj_lookup(0x2000, K_TIMER)->due_ns = clock_ns + 3000000000LL;
    obj_lookup(0x6000, K_QUEUE)->qcap = 4;
    checkpoints = under_mw_lock = sleeps = 0;
}
int main(void) {
    setup(1);
    uint64_t r = wait_any(0x100, -1);
    check(r == 0x200 && !obj_lookup(0x1000, K_EVENT)->signaled, "WaitAny: the event's holder, its signal consumed");
    check(under_mw_lock == 0, "WaitAny: no checkpoint while mw_lock is held");
    check(checkpoints >= 1, "WaitAny: still a checkpoint at the call");

    setup(0);
    r = wait_any(0x100, -1);
    check(r == 0x220 && sleeps == 1, "WaitAny: one sleep, then the semaphore's holder");
    check(under_mw_lock == 0, "WaitAny after a sleep: no checkpoint while mw_lock is held");

    setup(0);
    r = wait_any(0x100, 0);
    check(r == 0 && sleeps == 0 && under_mw_lock == 0, "TryWaitAny with nothing ready: none, no checkpoint under mw_lock");

    setup(0);
    hos_event_signal(0x1000);
    hos_system_event_signal(0x1000);
    check(signals == 2 && obj_lookup(0x1000, K_EVENT)->signaled, "the host's signals signal");
    check(checkpoints == 2, "the host's signals keep their checkpoint (guest threads call them: Kokiri -3% without)");
    if (!bad) puts("ok");
    return bad;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="clang++")
    parser.add_argument("--source", type=Path, default=ROOT / "host/nn_os_sync.c")
    args = parser.parse_args()
    src = args.source.read_text(encoding="utf-8")
    types = "\n".join((
        line(src, "enum { K_MUTEX = 1,"),
        through(src, "typedef struct Obj {", "} Obj;"),
        line(src, "#define MX_IX_OFFSET"),
        line(src, "#define MX_IX_CAP"),
        line(src, "static Obj *volatile mx_objs[MX_IX_CAP];"),
        line(src, "static volatile long mx_next"),
        line(src, "enum { H_EVENT,"),
        "static Obj *obj(uint64_t key, int kind);",
        "static Obj *obj_any(uint64_t key, int kind);",
        "static Obj *obj_quiet(uint64_t key, int kind);" if "static Obj *obj_quiet(" in src else "",
    ))
    funcs = [block(src, "static Obj *obj(uint64_t key, int kind) {"),
             block(src, "static Obj *obj_any(uint64_t key, int kind) {")]
    if "static Obj *obj_quiet(uint64_t key, int kind) {" in src:
        funcs.append(block(src, "static Obj *obj_quiet(uint64_t key, int kind) {"))
    funcs += [block(src, "static int holder_ready(Obj *h) {"),
              block(src, "static uint64_t wait_any(uint64_t multiwait, int64_t timeout_ns) {"),
              line(src, "void hos_event_signal(uint64_t guest_event) {"),
              line(src, "void hos_system_event_signal(uint64_t key) {")]
    fixture = HARNESS.replace("@TYPES@", types).replace("@FUNCS@", "\n\n".join(funcs))
    compiler = shutil.which(args.cxx)
    if not compiler:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    with tempfile.TemporaryDirectory(prefix="vcore-multiwait-") as temporary:
        folder = Path(temporary)
        (folder / "fixture.cpp").write_text(fixture, encoding="utf-8")
        exe = folder / ("fixture.exe" if os.name == "nt" else "fixture")
        subprocess.run([compiler, "-std=c++17", "-O1", "-w", str(folder / "fixture.cpp"), "-o", str(exe)], check=True,
                       timeout=120)
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        print(result.stdout, end="")
        if result.returncode or "ok" not in result.stdout:
            raise SystemExit("WaitAny's scan makes a virtual-core checkpoint with mw_lock held (or a host signal lost its own)")


if __name__ == "__main__":
    main()
