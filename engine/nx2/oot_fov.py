"""(fork) Adjust the gameplay lens before its projection and frustum are built (OoT 1.1.1): the Field of view row.

The camera update loads its vertical angle at +0x13c, then writes the angle and its half-angle's sine, cosine and tangent
into sead's perspective projection. Changing only that load lets the game build both rendering and CPU culling from the
chosen angle, without changing the camera's authored angle or accumulating a scale (host/oot_fov.c).

From Hookshot's nx2/oot_fov.py (ba472ea3b, GPL-3.0).
"""

MAIN = 0x80020000
SITE = MAIN + 0x4c52f8
# The angle load, half-angle multiply and projection angle store.
WORDS = (0xbd413e60, 0x1e210808, 0xbd00a280)
HOOK = ("{ extern void hos_oot_fov(Ctx *c, uint64_t camera); hos_oot_fov(c, c->x[19]); }"
        "   /* (nx2/oot_fov.py: gameplay field of view) */")


def install(lifter, img):
    """The site into lifter.after, after what is there; left out when its words differ (another game version)"""
    if any(img.word(SITE + 4 * i) != word for i, word in enumerate(WORDS)):
        print("field of view: unsupported camera code, left out")
        return 0
    lifter.after[SITE] = (lifter.after[SITE] + "\n" if SITE in lifter.after else "") + HOOK
    print("field of view: gameplay lens hooked")
    return 1
