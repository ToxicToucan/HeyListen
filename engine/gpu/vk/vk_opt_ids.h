#pragma once
/* (HeyListen) Runtime option ids that are ours alone, in a header of their own: gpu/vk/vk_api.h is part of the shader stamp
   (nx2/build.py), and a change there makes every player prepare shaders again. The ids upstream uses (Hookshot's and
   Longshot's, in vk_api.h and the renderer's option table) are left alone: 10 is upstream's Text size, 60 Longshot's item
   wheel. The options file is keyed by row name, so the id is not stored anywhere. */

/* The Frame rate limit row (host/nn_vi.c asks, gpu/vk/vk_replay.cpp answers in frames a second: 60, 90, 120, 144, 165, 240) */
#define HL_OPT_FPS_LIMIT 90

/* The Skip title screen row (host/oot_autoload.c asks, gpu/vk/vk_replay.cpp answers 0 or 1). 44 is the id Longshot's change
   of the same row uses in vk_api.h (free in this tree: the ids there run 32 to 35, 40 to 43), so the two agree. */
#define HL_OPT_SKIP_TITLE 44

/* (longshot, ported 2026-11-18) The Shadow resolution row (host/oot_shadow.c asks, gpu/vk/vk_replay.cpp answers 0 Game's 1600,
   1 2200, 2 3072). 56 is the id Longshot 9f8958a5c gives the same row in its vk_api.h (free in this tree). */
#define HL_OPT_SHADOW_RES 56
/* The Debug tab's First person row (host/hos_main.c and host/nn_hid.c ask, gpu/vk/vk_replay.cpp answers 0 or 1) */
#define HL_OPT_FIRST_PERSON 91
