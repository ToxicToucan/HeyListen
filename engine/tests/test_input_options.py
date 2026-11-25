# Copyright (C) 2026 Sacrappy

"""Two input options, both off for whoever does not ask: Sprint with L3, and Touchpad opens menu (issue 120).

Sprint with L3 (host/oot_sprint.c, nx2/oot_patches.py; after Longshot's Sprint, llun's #1929 and kendowashere's #2038).
host/oot_sprint.c is compiled as it is over a guest memory of its own (a made-up Link, actor manager and event manager) with
the two edits of nx2/oot_patches.py made in text shaped as the lift writes it:
- the row off (the default): the buttons come back as they went in, LS included; the flags Link loads are not touched;
- the row on, a press of LS that begins in gameplay (Link's update ran within the last reads, no event): the game does not
  get LS; Link's flags get the dash input (bit 4) at his movement update and at his stance update, and not before or after
  the press; A's bit and every other button are left alone;
- ZL held (targeting), a state that is not a walk, a jump, a slide or a roll, a Link that is not the controlled one: no dash;
- a press that begins outside gameplay (no update of Link for a while, or an event running, a conversation): the game gets LS
  for as long as it is held, no dash, even when gameplay comes back during the press; the next press starts afresh;
- the Insert menu open ("blocked"): nothing is passed, nothing dashes;
- the edits: made once each, a second run changes nothing, another version's text is left alone, the supported build only.

Touchpad opens menu: the one line of gpu/vk/vk_menu.cpp's menu_tick that opens the menu from the pads is cut out and compiled
over every combination of the row and of Home, the touchpad click and Select + Start: the click opens it only with the row on
(the default), Home and Select + Start open it with the row off too (a pad cannot lock itself out of the menu).

The rows themselves (names, defaults, ini keys, option id, the renderer's answer, the host's call, the docs) are read from the
sources.

python -I -B tests/test_input_options.py [--cc gcc]
"""
import argparse
import inspect
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "nx2"))
import oot_patches  # noqa: E402

A64_H = r'''#pragma once
#include <stdint.h>
#include <string.h>
#define A64_NOESC
typedef struct { uint64_t x[32]; uint8_t n, z, c, v; } Ctx;
#define GUEST 0x84b00000ull
extern unsigned char mem[0x200000];
#define GP(a) (mem + ((a) - GUEST))
static inline uint64_t recomp_load32(Ctx *c, uint64_t a) { uint32_t v; (void)c; memcpy(&v, GP(a), 4); return v; }
static inline uint64_t recomp_load64(Ctx *c, uint64_t a) { uint64_t v; (void)c; memcpy(&v, GP(a), 8); return v; }
static inline uint64_t recomp_load8(Ctx *c, uint64_t a) { (void)c; return *GP(a); }
#define A64_POLL(c) ((void)0)
'''
HOS_H = r'''#pragma once
#include "a64.h"
#define G64(a) (*(uint64_t *)GP(a))
#define G32(a) (*(uint32_t *)GP(a))
void hos_log(const char *fmt, ...);
'''

# Link's two places, as the lift writes them (the movement update's loads at 0x804ce798, the stance update's at 0x80c27f3c)
LIFTED = r'''#include "a64.h"
void f_804caa80(Ctx *__restrict A64_NOESC c) {
    c->x[10]=recomp_load64(c,c->x[19] + 80);
L_804ce790:;
    c->x[10]=recomp_load64(c,c->x[19] + 80);
    c->x[9]=recomp_load32(c,c->x[19] + 44);
    c->x[10]=recomp_load64(c,c->x[10] + 0);
if (((c->x[9] >> 20) & 1)) { goto L_804cfa90; }
if (((c->x[10] >> 4) & 1)) { goto L_804ced50; }
L_804ce7a4:; A64_POLL(c);
    return;
L_804cfa90:;
L_804ced50:;
    return;
}
void f_80c225d0(Ctx *__restrict A64_NOESC c) {
    c->x[8]=recomp_load64(c,c->x[19] + 80);
    c->x[8]=recomp_load8(c,c->x[8] + 0);
if (((c->x[8] >> 4) & 1)) { goto L_80c27f54; }
    return;
L_80c27f54:;
    return;
}
'''

FIXTURE = r'''#include "hos.h"
#include <stdio.h>
#include <stdlib.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
unsigned char mem[0x200000];
static int logs;
void hos_log(const char *fmt, ...) { (void)fmt; logs++; }
#define LS (1ull << 4)
#define ZL (1ull << 8)
#define A (1ull << 0)
#define LINK 0x84b10000ull
#define ACTOR 0x84b11000ull
#define STATE_A 0x84b12000ull
#define STATE_B 0x84b12100ull
#define STATE_C 0x84b12200ull
#define STATE_D 0x84b12300ull
#define FLAGS 0x84b13000ull
#define MGR 0x84b14000ull
#define CTL 0x84b15000ull
#define EVTP 0x84b16000ull
#define EVT 0x84b17000ull
static void st64(uint64_t a, uint64_t v) { memcpy(GP(a), &v, 8); }
static void st32(uint64_t a, uint32_t v) { memcpy(GP(a), &v, 4); }
uint64_t hos_sprint_input(uint64_t buttons, int on, int blocked);
void f_804caa80(Ctx *c);
void f_80c225d0(Ctx *c);
void hos_sprint_link(Ctx *c);
void hos_sprint_stance(Ctx *c);

static void make_world(void) {
    memset(mem, 0, sizeof mem);
    st64(LINK, 0x84b6dfc0ull), st64(LINK + 0x18, ACTOR), st64(LINK + 80, FLAGS);
    st64(ACTOR + 0x270, STATE_A), st64(STATE_A + 0x188, STATE_B), st64(STATE_B + 0x1c0, STATE_C);
    st32(STATE_C + 0x28, 1);   /* Walk */
    st64(0x84c9e0d0ull, MGR - 8), st64(MGR - 8, MGR), st64(MGR, 0x84bffe68ull), st64(MGR + 0x470, CTL);
    st64(CTL, 0x84b69698ull), st64(CTL + 0x18, ACTOR);
    st64(0x84c9e090ull, EVTP), st64(EVTP, EVT), st64(EVT + 0x68, 0);   /* no event */
    st64(FLAGS, 0x40);   /* (a flag that is not the dash input's, so it can be seen kept) */
}
static void set_state(int id) { st32(STATE_C + 0x28, (uint32_t)id); }
/* Link's two updates, as a frame runs them: the flags the game loads, then what the lift's code leaves in x10 and x8 */
static uint64_t update_flags(void) {
    Ctx c;
    memset(&c, 0, sizeof c);
    c.x[19] = LINK, c.x[10] = 0x40;
    f_804caa80(&c);
    return c.x[10];
}
static uint64_t stance_flags(void) {
    Ctx c;
    memset(&c, 0, sizeof c);
    c.x[19] = LINK, c.x[8] = 0x40;
    f_80c225d0(&c);
    return c.x[8];
}
#define DASH 0x10ull
/* a frame: the controllers' read, then Link's update and stance. Returns what the game's controllers gave; *flags, *stance the flags after */
static uint64_t frame(uint64_t buttons, int on, int blocked, uint64_t *flags, uint64_t *stance) {
    const uint64_t got = hos_sprint_input(buttons, on, blocked);
    *flags = update_flags();
    *stance = stance_flags();
    return got;
}
int main(void) {
    uint64_t f, s, got;
    make_world();
    /* the row off (the default): nothing changes, whatever is pressed */
    got = frame(LS | A | ZL, 0, 0, &f, &s);
    CHECK(got == (LS | A | ZL) && !(f & DASH) && !(s & DASH));
    got = frame(0, 0, 0, &f, &s);
    CHECK(got == 0 && f == 0x40 && s == 0x40);
    puts("PASS: the row off: the buttons come back as they went in, the flags are the game's");

    /* the row on, a press that begins in gameplay */
    for (int k = 0; k < 3; k++) frame(0, 1, 0, &f, &s);
    got = frame(LS | A, 1, 0, &f, &s);
    CHECK(!(got & LS) && (got & A));   /* the game does not get LS; A is left alone */
    CHECK((f & DASH) && (f & 0x40) && (s & DASH) && (s & 0x40));
    for (int k = 0; k < 20; k++) {
        got = frame(LS, 1, 0, &f, &s);
        CHECK(!(got & LS) && (f & DASH) && (s & DASH));
    }
    got = frame(0, 1, 0, &f, &s);
    CHECK(got == 0 && f == 0x40 && s == 0x40);
    puts("PASS: a press of LS from gameplay is Sprint's: no LS for the game, the dash input in Link's flags while it is held, A untouched");

    /* ZL held (targeting), and a state that is not a run's: no dash, the press still not the game's */
    for (int k = 0; k < 3; k++) frame(0, 1, 0, &f, &s);
    frame(LS | ZL, 1, 0, &f, &s);
    got = frame(LS | ZL, 1, 0, &f, &s);
    CHECK(!(got & LS) && !(f & DASH) && !(s & DASH));
    got = frame(LS, 1, 0, &f, &s);
    CHECK((f & DASH) && (s & DASH));   /* (the press began in gameplay: ZL let go in the middle of it, the dash is back) */
    frame(0, 1, 0, &f, &s);
    set_state(5);   /* an attack */
    frame(0, 1, 0, &f, &s);
    got = frame(LS, 1, 0, &f, &s);
    CHECK(!(got & LS) && !(f & DASH) && !(s & DASH));
    set_state(99);   /* a roll keeps it */
    frame(0, 1, 0, &f, &s);
    got = frame(LS, 1, 0, &f, &s);
    CHECK((f & DASH) && (s & DASH));
    set_state(1);
    frame(0, 1, 0, &f, &s);
    puts("PASS: ZL held and a state that is not a run's: no dash; a roll keeps it");

    /* another Link than the controlled one */
    st64(CTL + 0x18, ACTOR + 8);
    frame(LS, 1, 0, &f, &s);
    got = frame(LS, 1, 0, &f, &s);
    CHECK(!(f & DASH) && !(s & DASH));
    st64(CTL + 0x18, ACTOR);
    frame(0, 1, 0, &f, &s);
    puts("PASS: a Link the player does not control gets nothing");

    /* an event (a conversation): the press goes to the game as LS, for its whole length, and starts no sprint */
    st64(EVT + 0x68, 1);
    got = frame(LS, 1, 0, &f, &s);
    CHECK((got & LS) && !(f & DASH) && !(s & DASH));
    st64(EVT + 0x68, 0);
    for (int k = 0; k < 3; k++) {
        got = frame(LS, 1, 0, &f, &s);
        CHECK((got & LS) && !(f & DASH) && !(s & DASH));
    }
    got = frame(0, 1, 0, &f, &s);
    CHECK(got == 0);
    got = frame(LS, 1, 0, &f, &s);   /* the next press, in gameplay again */
    CHECK(!(got & LS) && (f & DASH));
    frame(0, 1, 0, &f, &s);
    puts("PASS: a press that begins in a conversation is the game's LS for its whole length (it hides the text box), no dash; the next one is Sprint's");

    /* no update of Link for a while (a menu of the game, a loading screen): not gameplay */
    for (int k = 0; k < 12; k++) hos_sprint_input(0, 1, 0);
    got = hos_sprint_input(LS, 1, 0);
    CHECK(got & LS);
    for (int k = 0; k < 3; k++) {
        got = hos_sprint_input(LS, 1, 0);
        CHECK(got & LS);
    }
    f = update_flags();
    CHECK(!(f & DASH));
    hos_sprint_input(0, 1, 0);
    update_flags();
    puts("PASS: a press that begins while Link is not updated is the game's LS");

    /* the Insert menu open: nothing passes, nothing dashes */
    for (int k = 0; k < 3; k++) frame(0, 1, 0, &f, &s);
    got = frame(LS, 1, 1, &f, &s);
    CHECK(!(got & LS) && !(f & DASH) && !(s & DASH));
    got = frame(LS, 1, 1, &f, &s);
    CHECK(!(got & LS) && !(f & DASH));
    frame(0, 1, 0, &f, &s);
    puts("PASS: with the Insert menu open the press is nobody's");

    /* the row turned off in the middle of a sprint: the dash goes at the next update */
    for (int k = 0; k < 3; k++) frame(0, 1, 0, &f, &s);
    frame(LS, 1, 0, &f, &s);
    got = frame(LS, 0, 0, &f, &s);
    CHECK((got & LS) && !(f & DASH) && !(s & DASH));
    puts("PASS: the row turned off ends the sprint and gives LS back");
    CHECK(logs >= 3);   /* (the test run's line, every 15th update of Link while the row is on) */
    return 0;
}
'''

# The line of menu_tick that opens the menu from the pads, over a lambda for pad() and the row's value
TOUCH_FIXTURE = r'''#include <stdio.h>
#include <stdlib.h>
struct { int touchpad_menu; } opts;
enum { HOS_PAD_GUIDE = 5, HOS_PAD_TOUCHPAD = 17 };
static int toggled(int guide, int touch, int chord) {
    const bool chord_now = chord != 0;
    auto pad = [&](int p) { return p == HOS_PAD_GUIDE ? guide != 0 : p == HOS_PAD_TOUCHPAD ? touch != 0 : false; };
    bool press[1] = {false};
    LINE
    return press[0] ? 1 : 0;
}
int main() {
    int bad = 0;
    for (int row = 0; row < 2; row++)
        for (int guide = 0; guide < 2; guide++)
            for (int touch = 0; touch < 2; touch++)
                for (int chord = 0; chord < 2; chord++) {
                    opts.touchpad_menu = row;
                    const int want = guide || chord || (row && touch);
                    if (toggled(guide, touch, chord) != want) {
                        printf("FAIL: row %d guide %d touch %d chord %d: got %d\n", row, guide, touch, chord, toggled(guide, touch, chord));
                        bad = 1;
                    }
                }
    if (!bad) puts("PASS: the touchpad click opens the menu only with the row on; Home and Select + Start with the row on or off");
    return bad;
}
'''


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def need(ok, what):
    if not ok:
        raise SystemExit("FAIL: " + what)


def compile_run(cc, tmp, sources, extra, mode_args=()):
    exe = tmp / ("fixture.exe" if os.name == "nt" else "fixture")
    r = run([cc, "-O1", "-Wall", "-Wextra", "-Werror", "-I", str(tmp)] + extra + sources + ["-o", str(exe)])
    if r.returncode:
        raise SystemExit("FAIL: compile\n" + r.stdout + r.stderr)
    r = run([str(exe)] + list(mode_args))
    print("\n".join(line for line in r.stdout.splitlines() if line.startswith("PASS")))
    if r.returncode:
        raise SystemExit("FAIL:\n%s%s" % (r.stdout, r.stderr))


def check_patch():
    out_link, n1 = oot_patches.patch_sprint_link(LIFTED)
    out, n2 = oot_patches.patch_sprint_stance(out_link)
    need(n1 == 1 and n2 == 1, "the two edits were not made once each")
    need(out.count("hos_sprint_link(c);") == 1 and out.count("hos_sprint_stance(c);") == 1, "the calls are not there once each")
    # (the call comes right after the load of the flags' word, before the tests of its bits)
    need(re.search(r"c->x\[10\]=recomp_load64\(c,c->x\[10\] \+ 0\);\n    hos_sprint_link\(c\);.*\nif \(\(\(c->x\[10\] >> 4\)", out, re.S),
         "the movement update's call is not right after the load of Link's flags")
    need(re.search(r"c->x\[8\]=recomp_load8\(c,c->x\[8\] \+ 0\);\n    hos_sprint_stance\(c\);", out), "the stance update's call is misplaced")
    need(out.count("extern void hos_sprint_link(Ctx *c);") == 1, "the declarations are not made once")
    again, m1 = oot_patches.patch_sprint_link(out)
    again, m2 = oot_patches.patch_sprint_stance(again)
    need(again == out and m1 == 1 and m2 == 1, "a second run changed the text")
    other = LIFTED.replace("L_804ce790:;", "L_804ce794:;").replace("goto L_80c27f54", "goto L_80c27f58")
    need(oot_patches.patch_sprint_link(other) == (other, 0) and oot_patches.patch_sprint_stance(other) == (other, 0),
         "another version's text was edited")
    src = inspect.getsource(oot_patches.apply)
    need("if protect_hearts:\n            for what, patch in ((\"sprint link\", patch_sprint_link)" in src,
         "the edits are made on builds other than the supported one")
    need('done["sprint link"] = 0' in src and 'done["sprint stance"] = 0' in src, "the edits are not counted (a missed one would not warn)")
    print("PASS: nx2/oot_patches.py: the two sites edited once each, a second run changes nothing, other text is left alone, "
          "the supported build only")
    return out


def check_sources():
    menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    header = (ROOT / "gpu/vk/vk_menu.h").read_text(encoding="utf-8")
    api = (ROOT / "gpu/vk/vk_api.h").read_text(encoding="utf-8")
    ids = (ROOT / "gpu/vk/vk_opt_ids.h").read_text(encoding="utf-8")
    replay = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    hid = (ROOT / "host/nn_hid.c").read_text(encoding="utf-8")
    readme = (ROOT / "readme.md").read_text(encoding="utf-8")
    need("int sprint = 0;" in header, "vk_menu.h: Sprint with L3 is not Off by default")
    need("int touchpad_menu = 1;" in header, "vk_menu.h: Touchpad opens menu is not On by default")
    sp = re.search(r'\{TAB_CONTROLS, "Sprint with L3", &opts\.sprint, 0, 1, 1, tx_onoff,', menu)
    tp = re.search(r'\{TAB_CONTROLS, "Touchpad opens menu", &opts\.touchpad_menu, 0, 1, 1, tx_onoff,', menu)
    need(sp and tp, "vk_menu.cpp: the two Controls rows are missing")
    # (the options file is keyed by the row's name, as ini_key makes it: a key of its own would need a line there)
    for row, key in (("Sprint with L3", "sprint_with_l3"), ("Touchpad opens menu", "touchpad_opens_menu")):
        made = row.replace(" ", "_").lower()
        need(made == key and len(made) < 32, "the ini key of " + row)
        need(key in readme, "readme.md does not name the ini key " + key)
    need("opts.sprint" not in re.search(r"const char\* ini_key\(const Row& r\) \{.*?\n\}", menu, re.S).group(0), "ini_key has a line of its own for sprint")
    need(re.search(r"#define\s+HL_OPT_SPRINT\s+97\b", ids), "vk_opt_ids.h: HL_OPT_SPRINT is not option id 97")
    need("SPRINT" not in api and "touchpad_menu" not in api, "vk_api.h (the shader stamp) names the new options: ids belong in vk_opt_ids.h")
    ids_in_use = re.findall(r"#define\s+HL_OPT_\w+\s+(\d+)", ids)
    need(len(ids_in_use) == len(set(ids_in_use)), "two options share an id in vk_opt_ids.h")
    need("if (id == HL_OPT_SPRINT) return opts.sprint != 0;" in replay, "vk_replay.cpp does not answer HL_OPT_SPRINT")
    need("buttons = hos_sprint_input(buttons, rx_option(HL_OPT_SPRINT, 0), menu_open);" in hid, "nn_hid.c does not call Sprint")
    need('"LS", B_LS' in hid, "the input script has no LS (a test run's L3)")
    for what in ("Sprint with L3", "Touchpad opens menu"):
        need("**%s:**" % what in readme, "readme.md does not describe " + what)
    credit = (REPO / "README.md").read_text(encoding="utf-8")
    need("**Sprint with L3**" in credit and "llun" in credit and "kendowashere" in credit and "thegrandao" in credit,
         "README.md does not credit the Longshot authors of Sprint")
    need("Original change by llun" in (ROOT / "host/oot_sprint.c").read_text(encoding="utf-8"), "oot_sprint.c does not credit its authors")
    # (the touchpad's click never reaches the game and the menu still opens another way: tests/test_menu_touchpad.py; here the way out)
    need("HOS_PAD_TOUCHPAD" not in hid, "nn_hid.c names the touchpad")
    tick = menu[menu.index("void menu_tick() {"):]
    need("pad(HOS_PAD_GUIDE) || (opts.touchpad_menu && pad(HOS_PAD_TOUCHPAD)) || chord_now" in tick,
         "the line of menu_tick that opens the menu from the pads changed")
    print("PASS: the rows (Controls tab, Off and On by default), their ini keys, id 97, the renderer's answer, the host's call and the docs")
    return tick


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    lifted = check_patch()
    tick = check_sources()
    line = re.search(r"^\s*(if \(pad\(HOS_PAD_GUIDE\)[^\n]*?press\[0\] = true;)", tick, re.M).group(1)
    cc, cxx = shutil.which(a.cc) or shutil.which("gcc"), shutil.which(a.cxx) or shutil.which("g++")
    if not cc or not cxx:
        print("SKIP: no C or C++ compiler")
        return
    with tempfile.TemporaryDirectory(prefix="input-options-") as tmp:
        tmp = Path(tmp)
        (tmp / "a64.h").write_text(A64_H, encoding="utf-8")
        (tmp / "hos.h").write_text(HOS_H, encoding="utf-8")
        (tmp / "fixture.c").write_text(FIXTURE, encoding="utf-8")
        (tmp / "code.c").write_text(lifted, encoding="utf-8")
        (tmp / "oot_sprint.c").write_text((ROOT / "host/oot_sprint.c").read_text(encoding="utf-8"), encoding="utf-8")
        compile_run(cc, tmp, [str(tmp / "fixture.c"), str(tmp / "code.c"), str(tmp / "oot_sprint.c")], ["-std=gnu11", "-Wno-unused-label"])
        (tmp / "touch.cpp").write_text(TOUCH_FIXTURE.replace("LINE", line), encoding="utf-8")
        compile_run(cxx, tmp, [str(tmp / "touch.cpp")], ["-std=c++17"])


if __name__ == "__main__":
    main()
