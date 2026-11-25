/* Copyright (C) 2026 Sacrappy

   Sprint with L3 (the Controls tab's "Sprint with L3" row, off by default; host/nn_hid.c's npad_states calls hos_sprint_input).

   Original change by llun and kendowashere (Longshot #1929 and #2038, after thegrandao's #196); reduced here to a held
   left stick press, with no Toggle and no rebinding.

   Link sprints while his input flags hold the dash input (bit 4 of the word at *(Link +0x50)), the flag the game sets itself for
   the run that follows a roll while A stays held. Two places load those flags (nx2/oot_patches.py patch_sprint_link and
   patch_sprint_stance): Link's movement update, f_804caa80 at 0x804ce798, for the sprint's start and keep tests, and the
   stance and camera update, f_80c225d0 at 0x80c27f3c. Right after each load they call hos_sprint_link and hos_sprint_stance
   below, which OR the bit in while the left stick is pressed, so the game's own run happens: its speed, animation, camera and
   every rule that ends it (stopping, a roll, Z-targeting, a cutscene). A is never touched: the player's own A (a roll,
   talking, opening, picking up) is theirs whether Sprint runs or not.

   What the game gets instead of the press. The left stick's press is the game's LS button (what the keyboard or pad input
   bound to LS presses, L3 by default): with the row on it is Sprint's, and the game does not see it as LS, except for a press
   that begins outside gameplay (a conversation, a cutscene, a menu of the game: Link's update has not run, or an event is
   running), which goes to the game as LS for as long as it is held and starts no sprint (in a conversation the press hides
   the text box). A press that begins in gameplay is Sprint's for its whole length.

   Link's state: the id at [[[[Link +0x18] +0x270] +0x188] +0x1c0] +0x28 (+0x4c when that reads 0x11a, a state that wraps
   another). Sprint goes on only in the states of a walk, a jump, a fall, a slide, a roll, or while he stands (a few frames);
   any other state (an attack, an item, swimming, climbing, a cutscene) or ZL held ends it at once. */
#include "hos.h"
#include <stdint.h>
#include <string.h>

#define BIT_LS (1ull << 4)   /* (host/nn_hid.c's B_LS and B_ZL) */
#define BIT_ZL (1ull << 8)

#define DASH_INPUT 0x10u
#define LINK_CLASS 0x84b6dfc0ull      /* Link's component class (the object f_804caa80 gets in x19) */
#define ACTOR_MGR 0x84c9e0d0ull       /* the pointer to the actor manager pointer */
#define ACTOR_MGR_CLASS 0x84bffe68ull
#define CONTROLLED_CLASS 0x84b69698ull
#define EVENT_MGR 0x84c9e090ull       /* the pointer to the event manager pointer */
#define GAMEPLAY_POLLS 8              /* a press is Sprint's while Link's update ran within this many reads */

enum { MOVE, KEEP, STOP };
static const struct { int32_t id; uint8_t kind; } states[] = {
    {1, MOVE},     /* Walk (also running) */
    {13, MOVE},    /* Jump */
    {32, MOVE},    /* Land */
    {33, MOVE},    /* Fall */
    {68, MOVE},    /* Clamber */
    {253, KEEP},   /* StepOn */
    {35, KEEP},    /* Slide */
    {36, KEEP},    /* SlideLand */
    {99, KEEP},    /* Rolling */
    {40, KEEP},    /* Wait */
};

static volatile int enabled;   /* the row on */
static volatile int held;      /* the left stick pressed at the last read */
static volatile int latched;   /* the press now held began in gameplay: Sprint's */
static volatile int zl_held;   /* ZL (targeting) held at the last read */
static volatile int polls = GAMEPLAY_POLLS;   /* reads since Link's last update (none yet: not gameplay) */
static volatile int active;    /* the dash input goes in */

#define LOAD(p) __atomic_load_n(&(p), __ATOMIC_RELAXED)
#define STORE(p, v) __atomic_store_n(&(p), (v), __ATOMIC_RELAXED)

/* 1 when link is the Link the player controls. His class has one more instance the player does not control: the game's actor
   manager names the controlled one at +0x470, whose +0x18 must be Link's own +0x18. */
static int is_link(Ctx *c, uint64_t link) {
    if (!link || recomp_load64(c, link) != LINK_CLASS) return 0;
    const uint64_t mgr = recomp_load64(c, recomp_load64(c, ACTOR_MGR));
    if (!mgr || recomp_load64(c, mgr) != ACTOR_MGR_CLASS) return 0;
    const uint64_t ctl = recomp_load64(c, mgr + 0x470);
    if (!ctl || recomp_load64(c, ctl) != CONTROLLED_CLASS) return 0;
    const uint64_t actor = recomp_load64(c, ctl + 0x18);
    return actor && actor == recomp_load64(c, link + 0x18);
}

/* An event (a conversation, a cutscene) is running: the event manager has an active event at +0x68, the field the game's
   SeqQueryIsEventRunning reads. */
static int event_running(void) {
    const uint64_t p = G64(EVENT_MGR);
    const uint64_t m = p ? G64(p) : 0;
    return m && G64(m + 0x68);
}

static int32_t state_id;   /* (the last one state_kind read, for the log) */
static int state_kind(Ctx *c, uint64_t link) {
    uint64_t p = recomp_load64(c, link + 0x18);
    if (p) p = recomp_load64(c, p + 0x270);
    if (p) p = recomp_load64(c, p + 0x188);
    if (p) p = recomp_load64(c, p + 0x1c0);
    if (!p) return state_id = -1, STOP;
    int32_t id = (int32_t)recomp_load32(c, p + 0x28);
    if (id == 0x11a) id = (int32_t)recomp_load32(c, p + 0x4c);
    state_id = id;
    for (size_t i = 0; i < sizeof states / sizeof *states; i++)
        if (states[i].id == id) return states[i].kind;
    return STOP;
}

/* host/nn_hid.c npad_states, the game's thread: each read of the controllers. buttons: what the game would get; on: the row;
   blocked: no press is Sprint's or the game's now (the Insert menu open, the ocarina waiting for a note). Returns the buttons
   with the left stick's press taken out while the row is on, and put back for a press that began outside gameplay (Link's
   update has not run within the last few reads, or an event is running). */
uint64_t hos_sprint_input(uint64_t buttons, int on, int blocked) {
    static int was_down, passing;
    const int down = (buttons & BIT_LS) != 0;
    STORE(enabled, on);
    if (!on) {
        STORE(latched, 0), STORE(held, 0), STORE(active, 0);
        STORE(polls, GAMEPLAY_POLLS);
        was_down = down, passing = 0;
        return buttons;
    }
    buttons &= ~BIT_LS;
    const int n = LOAD(polls);
    if (n <= GAMEPLAY_POLLS) STORE(polls, n + 1);
    const int gameplay = n < GAMEPLAY_POLLS && !event_running();
    /* A press is decided at its start, for its whole length: Sprint's when it begins in gameplay, else the game's (LS). */
    if (down && !was_down) {
        const int mine = gameplay && !blocked;
        STORE(latched, mine);
        passing = !mine && !blocked;
    } else if (!down) {
        STORE(latched, 0);
        passing = 0;
    }
    if (blocked) STORE(latched, 0), STORE(active, 0), passing = 0;
    was_down = down;
    STORE(held, down);
    STORE(zl_held, (buttons & BIT_ZL) != 0);
    return passing ? buttons | BIT_LS : buttons;
}

/* (nx2/oot_patches.py) f_804caa80 at 0x804ce798, after its load of Link's input flags into x10 (Link in x19): the flags for
   the sprint's start and keep tests. */
void hos_sprint_link(Ctx *c) {
    if (!LOAD(enabled)) return;
    if (!is_link(c, c->x[19])) return;
    STORE(polls, 0);
    const int on = LOAD(held) && LOAD(latched) && !LOAD(zl_held) && state_kind(c, c->x[19]) != STOP;
    STORE(active, on);
    /* a line a few times a second while the row is on, for a test run: Link's update count, the dash, his state and where he is
       (the actor's position, floats at +796: the debug tools' Mark position reads the same) */
    static unsigned updates;
    if (++updates % 15 == 0) {
        const uint64_t actor = recomp_load64(c, c->x[19] + 0x18);
        float pos[3];
        for (int i = 0; i < 3; i++) {
            const uint32_t bits = (uint32_t)recomp_load32(c, actor + 796 + 4 * i);
            memcpy(&pos[i], &bits, 4);
        }
        hos_log("sprint: update %u dash %d held %d state %d position %.2f %.2f %.2f", updates, on, LOAD(held), (int)state_id,
                (double)pos[0], (double)pos[1], (double)pos[2]);
    }
    if (on) c->x[10] |= DASH_INPUT;
}

/* (nx2/oot_patches.py) f_80c225d0 at 0x80c27f3c, after its load of the same flags' low byte into x8 (Link in x19): the
   stance and camera update. */
void hos_sprint_stance(Ctx *c) {
    if (LOAD(enabled) && LOAD(active) && is_link(c, c->x[19])) c->x[8] |= DASH_INPUT;
}
