/* (longshot) The Insert menu's Shadow resolution: the sun's cascades at the game's own larger sizes. nx2/oot_shadow.py calls
   this after the game has chosen this frame's cascade size and rounded it to a multiple of 8, before it stores the size.

   The sizes are the game's: at the quality level it runs at its table holds 1600, the size it uses, and 2200, the largest of
   that level's entries; 3072 is its highest level's. Only the sun's shadow object is changed. The size is raised, never
   lowered, so a frame where the game asks for more keeps the game's. Nothing is written to the game's memory: Game gives the
   game's size again from the next frame.

   From Longshot 9f8958a5c (Lucas Lebbrecht, #773, GPL-3.0-or-later), ported to this fork 2026-11-18 (OOT perf session). Here the
   row is HL_OPT_SHADOW_RES (gpu/vk/vk_opt_ids.h, the same id 56) and has no Auto: 0 Game (1600), 1 High (2200), 2 Ultra (3072). */
#include "hos.h"
#include "../gpu/vk/vk_opt_ids.h"

int rx_option(int id, int otherwise);

void hos_oot_shadow(Ctx *c, uint64_t shadow) {
    const int choice = rx_option(HL_OPT_SHADOW_RES, 0);
    if (choice < 1 || choice > 2 || !shadow) return;
    if (recomp_load64(c, shadow) != 0x84baa0a8ULL) return;   /* the sun's shadow object */
    const uint32_t size = choice == 1 ? 2200 : 3072;
    if ((uint32_t)c->x[23] < size) c->x[23] = size;   /* w23: the rounded size */
}
