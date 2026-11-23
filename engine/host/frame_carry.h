#pragma once
#include <stdint.h>
/* (Longshot 2ae9bffcf, SadTransGirl, #1828; HOS_FRAME_CARRY) Whether the whole-frame counters keep the fractions of the step: while the
   host publishes the frame's seconds (a cap above 60), and at a cap of 60 while the rate the game was given makes a step that is not
   a whole frame (46 fps steps 1.30: cut, the counters took 46 whole frames a second and the time of day ran at 77 %; nn_vi.c
   realtime_step sets hos_frame_carry_step, which is 0 for a rate of 60, 30, 20 or 15, Per frame, HOS_FRAME_CARRY=0 and a cap above
   60 without the frames' time). */
extern volatile uint32_t hos_frame_seconds_bits;
extern volatile int hos_frame_carry_step;
#define HOS_FRAME_CARRYING() (__atomic_load_n(&hos_frame_seconds_bits, __ATOMIC_RELAXED) != 0 || \
                              __atomic_load_n(&hos_frame_carry_step, __ATOMIC_RELAXED) != 0)
