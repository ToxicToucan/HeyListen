/* (HeyListen fork, after Longshot 24e73f1f0 (#1384 and #1485's fix), GPL-3.0) Havok cloth (Link's cap, King Zora's robe,
   people's clothes) keeps the game's clock away from 60 fps; host/hl_cloth.c calls these from two lift hooks.

   Below 60 fps: the simulate operator's per-frame setup (f_81b3aa48) takes the frame's time from its step context (+8) and
   splits it into substeps. That time is a 60th of a second whatever the frame, so the cloth ran at fps / 60 of the clock while
   Link moved at the game's: at 18 fps (a step of 3.3) his cap swung far out and took about a second to settle. While the
   game's frame step (the float f_83b6ea90 writes at 0x84d61fc8, 60 / its frame rate) is over 1, the time is multiplied by it
   (oot_cloth_seconds): the same number of solves, each that much longer.

   Above 60 fps: the solve job (f_81b47080, on the game's job workers, once a frame for each cloth near the camera) steps a
   60th each time, so at 240 fps the cloth moved four times too fast and cost four solves where the console makes one (Longshot
   issue 1485: near King Zora the solve was 45.7 % of the workers' time). On a frame whose step is under 1, the job solves only
   when the game clock (in 60ths, f_80706be0's object +0xd58: hos_step_probe) has crossed a whole 60th since the frame before
   (oot_cloth_gate); otherwise it returns at once with its own result, 0, before it saves anything, and the cloth keeps its last
   pose. Every job that sees one clock value makes the same choice. A clock that went back (a load) solves and starts again. The
   solve job calls only the cloth's own code, timer scopes, thread-local lookups and a scratch buffer of its own (read from the
   lift upstream): no job started or signalled, nothing the setup made freed. At a step of 1 or more, at a cap of 60 and for a
   step that is not a number, every frame solves, as the game has it. */
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>

static inline float oot_cloth_float(uint32_t bits) { float f; memcpy(&f, &bits, 4); return f; }
static inline uint32_t oot_cloth_bits(float f) { uint32_t b; memcpy(&b, &f, 4); return b; }

/* The cloth's time: seconds_bits (the step context's time, a float), step_bits (the game's frame step, a float), on (0: the
   game's). A step of 1 or less (60 fps and above), or not a number, 0, below 0 or over 8, keeps the game's */
static inline uint32_t oot_cloth_seconds(uint32_t seconds_bits, uint32_t step_bits, int on) {
    const float step = oot_cloth_float(step_bits);
    if (!on || !(step > 1.0f && step <= 8.0f)) return seconds_bits;   /* (the host's longest step is 4) */
    return oot_cloth_bits(oot_cloth_float(seconds_bits) * step);
}

typedef struct {
    double clock;   /* (the game clock of the last frame seen, in 60ths) */
    int solve;      /* (that frame's choice, for its other jobs) */
    int have;       /* (0: no frame seen since the gate last stood aside) */
} OotClothGate;
#define OOT_CLOTH_GATE_INIT {0.0, 1, 0}

/* 1: solve the cloth now, 0: hold it this frame. clock (the game clock, 60ths), step (the game's frame step), on, timed (a cap
   above 60: the frame's own time given) */
static inline int oot_cloth_gate(OotClothGate *g, double clock, float step, int on, int timed) {
    if (!on || !timed || !(step > 0.0f && step < 1.0f) || !(clock == clock)) {   /* (as the game has it: every frame) */
        g->have = 0;
        return 1;
    }
    if (g->have && clock == g->clock) return g->solve;   /* (another job of the same frame) */
    const int solve = !g->have || clock < g->clock || floor(clock) != floor(g->clock);
    g->clock = clock;
    g->solve = solve;
    g->have = 1;
    return solve;
}
