/* (HeyListen fork, after Hookshot f02132ece, "the frozen Zoras stand still above a 60 fps cap") The clock of an animation that
   loops over one frame or less (host/loop_step.h has why and the rule).

   Upstream edits the lifted f_802cfca0 and f_80944aa0 in nx2/oot_patches.py: it changes the step the two clock advances
   multiply (vreg 8) and the time the skeleton's drawn time store writes (vreg 0). Here the lifted work folder is shared, so the
   calls go in as lift hooks (nx2/hl_gates.py: HL_LOOP_HOOK, HL_POSE_HOOK), lines put in around the game's own lines, which stay
   as they are: before the advance's multiply the step is set to the one hos_loop_step_in gives (the game's, kept for after),
   after it the game's step is put back; before the store the time is set to the one hos_loop_pose_in gives, after it the
   game's time is put back. So only the multiply and the store see the new value, as upstream's. At a cap of 60
   (hos_frame_seconds_bits 0) the values are the game's. */
#include "hl_host.h"
#include "loop_step.h"
#include <windows.h>   /* (SRWLOCK) */

HL_LOOP_HOOK(802cfca0, "072fb052f190924c", "x20_");   /* the animation entries' clock: its two advances (the entry in x20_) */
HL_POSE_HOOK(80944aa0, "509feb73a7f7e5bd");            /* the skeletons' drawn time: its one store */

extern volatile uint32_t hos_frame_seconds_bits;   /* (hl_step.c: the frame's seconds, 0 at a cap of 60) */

static LoopStep loop_step_state;
static SRWLOCK loop_step_lock = SRWLOCK_INIT;   /* (the game's update threads) */
static _Thread_local uint32_t kept_step, kept_time;   /* (the game's values, put back after the multiply and the store) */

static int loop_timed(void) { return __atomic_load_n(&hos_frame_seconds_bits, __ATOMIC_RELAXED) != 0; }

/* Before an advance's multiply: the step for the entry at `at` (whole frames for a loop of one frame or less, the fraction
   carried), the game's kept for hos_loop_step_out */
uint32_t hos_loop_step_in(uint64_t at, uint32_t step_bits, uint32_t start_bits, uint32_t end_bits) {
    kept_step = step_bits;
    const int timed = loop_timed();
    if (!timed || !loop_is_pose(start_bits, end_bits)) return step_bits;   /* (most entries: no lock) */
    AcquireSRWLockExclusive(&loop_step_lock);
    const uint32_t whole = loop_step(&loop_step_state, at, step_bits, start_bits, end_bits, timed);
    ReleaseSRWLockExclusive(&loop_step_lock);
    return whole;
}
uint32_t hos_loop_step_out(void) { return kept_step; }

/* Before the drawn time's store: the loop's start for a range of one frame or less, the game's time kept */
uint32_t hos_loop_pose_in(uint32_t time_bits, uint32_t start_bits, uint32_t end_bits) {
    kept_time = time_bits;
    return loop_pose(time_bits, start_bits, end_bits, loop_timed());
}
uint32_t hos_loop_pose_out(void) { return kept_time; }
