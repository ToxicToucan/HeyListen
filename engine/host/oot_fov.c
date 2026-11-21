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
#include <stdlib.h>
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

/* (fork, 2026-11-21) Vert+ on a picture narrower than 16:9 (host/hos_main.c: HOS_ASPECT, the ini's aspect= or the Ultrawide
   row; the owner's phone stream is 2670x1878, about 1.42:1). nx2/oot_wide.py gives every 16:9 camera the picture's ratio at its
   own vertical angle, so a narrower picture loses the sides of the game's view. Here each 16:9 camera's vertical angle is
   widened as it is loaded, tan(v'/2) = tan(v/2) * (16/9) / ratio: the horizontal view stays the game's and the extra height
   shows more above and below. Only the register is changed (the camera's own angle stays), so it never compounds. All 16:9
   cameras (gameplay, events, menus' 3D), as the ratio hooks; HOS_WIDE_VERT=0 keeps the narrower view (Hor-).
   The idea is our old engine's (6366644: its field-of-view hook at the same site, main+0x4c52fc); written here for this tree. */
static void vert_plus(Ctx *c, uint64_t camera) {
    extern volatile float hos_wide_ratio;
    static int on = -1;
    const float r = hos_wide_ratio;
    if (!(r > 0.5f && r < 16.0f / 9.0f - 0.01f) || !camera) return;
    if (on < 0) { const char *e = getenv("HOS_WIDE_VERT"); on = !(e && atoi(e) == 0); }
    if (!on) return;
    float aspect, angle;
    const uint32_t a = recomp_load32(c, camera + 0x140);
    memcpy(&aspect, &a, sizeof aspect);
    if (!(fabsf(aspect - 16.0f / 9.0f) <= 0.01f)) return;
    memcpy(&angle, c->vreg[0], sizeof angle);
    if (!isfinite(angle) || angle <= 0.0f || angle >= 3.0f) return;
    const float wide = 2.0f * atanf(tanf(angle * 0.5f) * (16.0f / 9.0f) / r);
    static int logged;
    if (!logged) {
        logged = 1;
        hos_log("wide: picture ratio %.4f: a 16:9 camera's vertical view %.1f -> %.1f degrees (Vert+; HOS_WIDE_VERT=0: off)",
                (double)r, (double)angle * 57.29578, (double)wide * 57.29578);
    }
    uint32_t bits;
    memcpy(&bits, &wide, sizeof bits);
    a64_vset64(c->vreg[0], bits);
}

void hos_oot_fov(Ctx *c, uint64_t camera) {
    vert_plus(c, camera);
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
