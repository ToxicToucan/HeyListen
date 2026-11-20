/* (HeyListen fork, after Longshot 24e73f1f0, GPL-3.0) Havok cloth keeps the game's clock: below 60 fps its time follows the
   frame step, above a 60 cap it is solved at most 60 times a second (host/oot_cloth.h has why and the rule).

   Upstream edits the lifted f_81b3aa48 and f_81b47080 in nx2/oot_patches.py; here the lifted work folder is shared, so the
   calls go in as lift hooks (nx2/hl_gates.py's "clothtime" and "clothsolve" kinds): a line after the setup's load of its
   step context's time sets the low float to hos_oot_cloth_seconds', and a line before the solve job's first store returns
   from it (the job's own result, 0) when hos_oot_cloth_solve says to hold.

   Both on by default (upstream has the above-60 gate behind HOS_FPS_CLOTH_60=1 while it is looked at on more PCs; it measured
   no frame rate change and the cloth looked right). HOS_FPS_CLOTH=0: below 60 the game's time. HOS_FPS_CLOTH_60=0: above a
   60 cap every frame solves, as the game has it. HOS_FPS_LOG: a line every 600 setups and every 600 solve jobs. */
#include "hl_host.h"
#include "oot_cloth.h"
#include <stdlib.h>
#include <windows.h>   /* (SRWLOCK, InterlockedIncrement64) */

HL_CLOTHTIME_HOOK(81b3aa48, "8dae48419ce63b75");    /* the simulate operator's per-frame setup: its time */
HL_CLOTHSOLVE_HOOK(81b47080, "3f18f25fa883490e");   /* the solve job: its head */

extern volatile uint32_t hos_frame_seconds_bits;   /* (hl_step.c: the frame's seconds, 0 at a cap of 60) */
extern volatile uint64_t hos_step_probe;           /* (hl_step.c: f_80706be0's object, its +0xd58 the game clock in 60ths) */
#define FRAME_STEP_AT 0x84d61fc8ULL                /* (f_83b6ea90's float: the game's frame step, 60 / its frame rate) */

/* (an env switch read once: 0 turns it off, anything else or unset leaves it on) */
static int switched(int *on, const char *name) {
    int v = __atomic_load_n(on, __ATOMIC_RELAXED);
    if (v < 0) v = !(getenv(name) && !atoi(getenv(name))), __atomic_store_n(on, v, __ATOMIC_RELAXED);
    return v;
}
static int fps_log(void) {
    static int on = -1;
    int v = __atomic_load_n(&on, __ATOMIC_RELAXED);
    if (v < 0) v = getenv("HOS_FPS_LOG") != NULL, __atomic_store_n(&on, v, __ATOMIC_RELAXED);
    return v;
}
static uint32_t frame_step_bits(void) { uint32_t b; memcpy(&b, GP(FRAME_STEP_AT), 4); return b; }

/* After f_81b3aa48 loads its step context's time (the low float of seconds2): that time times the frame step below 60 fps */
uint64_t hos_oot_cloth_seconds(uint64_t seconds2) {
    static int on = -1;
    const int v = switched(&on, "HOS_FPS_CLOTH");
    const uint32_t step = frame_step_bits(), was = (uint32_t)seconds2, seconds = oot_cloth_seconds(was, step, v);
    if (fps_log()) {
        static volatile LONG64 calls;
        const LONG64 n = InterlockedIncrement64(&calls);
        if (n % 600 == 1)
            hos_log("oot: cloth time %.2f ms (the game's %.2f ms: frame step %.3f%s; %lld setups)", oot_cloth_float(seconds) * 1000.0f,
                    oot_cloth_float(was) * 1000.0f, oot_cloth_float(step), v ? "" : ", HOS_FPS_CLOTH=0", (long long)n);
    }
    return (seconds2 & 0xffffffff00000000ULL) | seconds;
}

static OotClothGate cloth_gate = OOT_CLOTH_GATE_INIT;
static long long cloth_calls, cloth_solves;   /* (HOS_FPS_LOG's counts, under cloth_lock) */
static SRWLOCK cloth_lock = SRWLOCK_INIT;     /* (the solve jobs run on the game's job workers, several at once) */

/* f_81b47080's head: 0, the job returns at once and the cloth is held this frame */
int hos_oot_cloth_solve(void) {
    static int on = -1;
    const int v = switched(&on, "HOS_FPS_CLOTH_60");
    const int t = __atomic_load_n(&hos_frame_seconds_bits, __ATOMIC_RELAXED) != 0;
    if (!v || !t) return 1;   /* (off, or a cap of 60: every job solves, and no lock taken) */
    double clock = NAN;   /* (no clock: the gate stands aside) */
    const uint64_t probe = __atomic_load_n(&hos_step_probe, __ATOMIC_RELAXED);
    if (probe) memcpy(&clock, GP(probe + 0xd58), 8);
    const float step = oot_cloth_float(frame_step_bits());
    long long solved = -1, calls = 0;
    AcquireSRWLockExclusive(&cloth_lock);
    const int solve = oot_cloth_gate(&cloth_gate, clock, step, v, t);
    if (fps_log()) {
        cloth_solves += solve;
        if (!(++cloth_calls % 600)) solved = cloth_solves, calls = cloth_calls, cloth_solves = 0;
    }
    ReleaseSRWLockExclusive(&cloth_lock);
    if (solved >= 0)
        hos_log("oot: cloth solved %lld of the last 600 solve jobs (step %.3f; %lld jobs)", solved, step, calls);
    return solve;
}
