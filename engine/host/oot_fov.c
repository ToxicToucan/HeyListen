/* (fork) The Gameplay page's Field of view, as a percentage of the game's current vertical angle (HL_OPT_FOV,
   gpu/vk/vk_opt_ids.h). nx2/oot_fov.py calls this after the angle load, before the game builds its projection (including
   the cached half-angle tangent used by CPU culling).

   Only camera zero's gameplay view is adjusted. The source camera is never written: zooms still vary with the game, each
   update starts from its own angle, and 100% restores it on the next update. The change eases out over 0.35 s
   (smoothstep) as an event or cutscene starts and back in as it ends, so the picture's width does not jump in one frame;
   time the game spends without camera updates (a pause, a load) does not count as easing.

   From Hookshot's host/oot_fov.c and nx2/oot_fov.py (ba472ea3b, GPL-3.0), with the easing of the OoT recomp's menu branch
   (153db9d). */
#include "hos.h"
#include "../gpu/vk/vk_opt_ids.h"
#include <math.h>
#include <string.h>

int rx_option(int id, int otherwise);
extern int64_t hos_now_ns(void);

/* how much of the change is in effect: towards 1 in play, towards 0 in events, smoothstepped */
static float fov_share(int in_play) {
    static float share = 1.0f;
    static int64_t last;
    const int64_t now = hos_now_ns();
    double dt = last ? (double)(now - last) * 1e-9 : 0;
    last = now;
    if (dt < 0 || dt > 0.1) dt = 0.1 < dt ? 0.1 : 0;   /* (a long gap, a pause or a load, counts as one step at most) */
    const float step = (float)(dt / 0.35);
    share = in_play ? fminf(1.0f, share + step) : fmaxf(0.0f, share - step);
    return share * share * (3.0f - 2.0f * share);
}

void hos_oot_fov(Ctx *c, uint64_t camera) {
    const int percent = rx_option(HL_OPT_FOV, 100);
    if (percent == 100 || percent < 50 || percent > 150 || !camera) return;

    const uint64_t views = recomp_load64(c, camera + 0x18);
    const uint64_t view = views ? recomp_load64(c, views + 8) : 0;
    if (!view || recomp_load64(c, view) != 0x84c18a88ULL) return;   /* sead::LookAtCamera */

    /* CameraMgr's first camera supplies the gameplay view (hos_main.c's free view uses the same ownership check).
       Portraits, reflections and UI stay alone. */
    const uint64_t singleton = recomp_load64(c, 0x84ca6bc0ULL);
    const uint64_t manager = singleton ? recomp_load64(c, singleton) : 0;
    const uint64_t cameras = manager && recomp_load32(c, manager + 32) ? recomp_load64(c, manager + 40) : 0;
    const uint64_t main_camera = cameras ? recomp_load64(c, cameras) : 0;
    if (!main_camera || recomp_load64(c, main_camera + 120) != view) return;

    const uint64_t event_singleton = recomp_load64(c, 0x84c9e090ULL);
    const uint64_t events = event_singleton ? recomp_load64(c, event_singleton) : 0;
    const float share = fov_share(!(events && recomp_load64(c, events + 104)));
    if (share <= 0.0f) return;

    float angle;
    memcpy(&angle, c->vreg[0], sizeof angle);
    if (!isfinite(angle) || angle <= 0.0f || angle >= 3.141592654f) return;
    const float scale = 1.0f + ((float)percent / 100.0f - 1.0f) * share;
    /* Keep the vertical lens within 5..140 degrees even during an extreme zoom. */
    angle = fminf(2.443460953f, fmaxf(0.087266463f, angle * scale));
    uint32_t bits;
    memcpy(&bits, &angle, sizeof bits);
    a64_vset64(c->vreg[0], bits);
}
