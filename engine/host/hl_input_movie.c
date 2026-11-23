/* (fork) Input recordings for identical A/B runs: the pad the game is given, frame by frame, written down and given back,
   and Link's position as the run's fingerprint. A reduced port of Longshot 971f77f52 by dyst (issue 1113; host/input_movie.c
   and input_movie.h there, git.magicbean.dev/MagicBeanDev/Longshot, GPL-3.0-or-later): its file format, its "one pad a game
   frame" rule and its marks. Not ported: the gyroscope, the native mouse camera, the game menus' mouse, the Debug tools'
   actions and rows, load anchors and HOS_INPUT_SYNC. So a recording for a replay is made with the pad or the keys (or a
   script), the Mouse camera not on Native, and no cheats used.

   HOS_INPUT_RECORD=<file>: records, from the first controller read after play begins (host/hos_main.c hos_oot_play_ns),
   each game frame's pad (host/nn_hid.c npad_states: the button word and sticks the game is given, after the script, the
   pads, the keys, the mouse as a stick and the filters; the free camera's, first person's and flight's inputs too) as runs
   of frames, and Link's position every quarter second of game time.
   HOS_INPUT_PLAY=<file>: from the same point, every controller read is the recording's alone (the player's devices and the
   script count no more), and Link's position is compared with the recording's marks: the log says where the game first
   went its own way (10 cm apart), every 10 s how far apart, and the largest at the end of the recording. Recording and
   playing both: the play wins (the file played is never written).

   The clock is the game's: at a Frame rate cap of 60 a frame is a 60th of a second, above it the frame's seconds the step
   hooks give the game (host/hl_step.c hos_frame_seconds_bits). A replay finds its pad by that game time, so a recording made
   at 60 replays frame for frame at 60, and at another cap gives each input at the same game time (Link's route then
   drifts a little, as the game's own steps differ: the marks say how much). HOS_INPUT_CLOCK=frame: by frame count instead.

   File: JSON lines, Longshot's ls-input-recording/1, with "t" (game seconds since the start) added to each line:
     {"format":"ls-input-recording/1","fps":60,...}
     {"f":F,"t":T,"cmd":"pad","frames":N,"bits":"0x...","lx":0,"ly":32767,"rx":0,"ry":0[,"fl":"0x...","fc":"0x...","fh":H]}
     {"f":F,"t":T,"cmd":"mark","pos":[x,y,z]}
   A run is written when the pad changes, or every 600 frames: a run cut short (the game closed by force) loses 10 s at most. */
#include "hos.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include "hl_input_movie.h"

extern volatile LONG64 hos_present_count;          /* (nn_vi.c: native game presents, one a game frame) */
extern volatile uint32_t hos_frame_seconds_bits;   /* (hl_step.c: the frame's seconds, 0 at a cap of 60) */
extern int64_t hos_oot_play_ns(void);              /* (hos_main.c: 0 until the player has run) */

#define RUN_MAX 600u
#define MARK_EVERY 0.25
#define APART_FIRST 0.10f

typedef struct { uint32_t f, frames; double t; MoviePad pad; } PadRun;
typedef struct { double t; float pos[3]; } Mark;

static volatile LONG mode = -1;   /* (-1 not looked yet, 0 none, 1 record, 2 play) */
static SRWLOCK lock = SRWLOCK_INIT;
static int started;
static LONG64 last_present;
static uint32_t frame;         /* (game frames since the start) */
static double game_t;          /* (game seconds at the current frame's start) */
static int by_frame;
static FILE *out;
static PadRun cur;             /* (recording: the run being made; cur.frames 0: none) */
static int first_read_done;    /* (recording: this frame's first read was taken) */
static double next_mark;
static PadRun *runs;           /* (playing: the file's) */
static size_t nruns, at_run;
static Mark *marks;
static size_t nmarks, at_mark;
static int ended, apart_said;
static float largest;
static double largest_t, next_report;
static char path_used[512];

static float bits_f(uint32_t b) { float f; memcpy(&f, &b, 4); return f; }

static const char *field(const char *s, const char *key) {   /* (the text after "key": in a line, or NULL) */
    char k[24];
    snprintf(k, sizeof k, "\"%s\":", key);
    const char *p = strstr(s, k);
    return p ? p + strlen(k) : NULL;
}
static double num(const char *s, const char *key, double otherwise) {
    const char *p = field(s, key);
    if (!p) return otherwise;
    if (*p == '"') p++;
    return strtod(p, NULL);
}
static uint64_t hex(const char *s, const char *key) {
    const char *p = field(s, key);
    if (!p) return 0;
    if (*p == '"') p++;
    return strtoull(p, NULL, 16);
}

static void load(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        hos_log("input: HOS_INPUT_PLAY: %s could not be opened: the player's input as ever", path);
        InterlockedExchange(&mode, 0);
        return;
    }
    size_t cap_r = 0, cap_m = 0;
    char line[1024];
    while (fgets(line, sizeof line, f)) {
        if (strstr(line, "\"cmd\":\"pad\"")) {
            if (nruns == cap_r) cap_r = cap_r ? cap_r * 2 : 1024, runs = realloc(runs, cap_r * sizeof *runs);
            PadRun *r = &runs[nruns++];
            memset(r, 0, sizeof *r);
            r->f = (uint32_t)num(line, "f", 0), r->t = num(line, "t", r->f / 60.0), r->frames = (uint32_t)num(line, "frames", 1);
            r->pad.bits = hex(line, "bits");
            r->pad.stick[0] = (int32_t)num(line, "lx", 0), r->pad.stick[1] = (int32_t)num(line, "ly", 0);
            r->pad.stick[2] = (int32_t)num(line, "rx", 0), r->pad.stick[3] = (int32_t)num(line, "ry", 0);
            r->pad.flight = hex(line, "fl"), r->pad.axes = hex(line, "fc"), r->pad.height = (int32_t)num(line, "fh", 0);
        } else if (strstr(line, "\"cmd\":\"mark\"")) {
            const char *p = field(line, "pos");
            float v[3];
            if (!p || sscanf(p, "[%f,%f,%f]", &v[0], &v[1], &v[2]) != 3) continue;
            if (nmarks == cap_m) cap_m = cap_m ? cap_m * 2 : 1024, marks = realloc(marks, cap_m * sizeof *marks);
            marks[nmarks].t = num(line, "t", 0);
            memcpy(marks[nmarks++].pos, v, sizeof v);
        }
    }
    fclose(f);
    const PadRun *last = nruns ? &runs[nruns - 1] : NULL;
    hos_log("input: HOS_INPUT_PLAY: %s: %zu pad runs, %zu marks, %u frames (%.1f s of game time); played %s", path, nruns,
            nmarks, last ? last->f + last->frames : 0, last ? last->t + last->frames / 60.0 : 0.0,
            by_frame ? "by frame (HOS_INPUT_CLOCK=frame)" : "by game time");
    if (!nruns) InterlockedExchange(&mode, 0);
}

static void look(void) {
    const char *play = getenv("HOS_INPUT_PLAY"), *rec = getenv("HOS_INPUT_RECORD"), *clock = getenv("HOS_INPUT_CLOCK");
    by_frame = clock && !strcmp(clock, "frame");
    if (play && *play) {
        snprintf(path_used, sizeof path_used, "%s", play);
        InterlockedExchange(&mode, 2);
        if (rec && *rec) hos_log("input: HOS_INPUT_RECORD and HOS_INPUT_PLAY both set: the replay only (nothing recorded)");
        load(play);
    } else if (rec && *rec) {
        snprintf(path_used, sizeof path_used, "%s", rec);
        out = fopen(rec, "wb");
        if (!out) {
            hos_log("input: HOS_INPUT_RECORD: %s could not be written: nothing recorded", rec);
            InterlockedExchange(&mode, 0);
            return;
        }
        fprintf(out, "{\"format\":\"ls-input-recording/1\",\"fps\":60,\"by\":\"HeyListen fork (pads and marks; after Longshot "
                     "971f77f52)\",\"clock\":\"t: game seconds since the start\"}\n");
        fflush(out);
        InterlockedExchange(&mode, 1);
        hos_log("input: HOS_INPUT_RECORD: recording to %s from when play begins", rec);
    } else {
        InterlockedExchange(&mode, 0);
    }
}

static void write_run(void) {   /* (under lock, recording) */
    if (!cur.frames || !out) return;
    const MoviePad *p = &cur.pad;
    fprintf(out, "{\"f\":%u,\"t\":%.6f,\"cmd\":\"pad\",\"frames\":%u,\"bits\":\"0x%llx\",\"lx\":%d,\"ly\":%d,\"rx\":%d,\"ry\":%d",
            cur.f, cur.t, cur.frames, (unsigned long long)p->bits, p->stick[0], p->stick[1], p->stick[2], p->stick[3]);
    if (p->flight || p->axes || p->height)
        fprintf(out, ",\"fl\":\"0x%llx\",\"fc\":\"0x%llx\",\"fh\":%d", (unsigned long long)p->flight,
                (unsigned long long)p->axes, p->height);
    fprintf(out, "}\n");
    fflush(out);
}

static int pad_same(const MoviePad *a, const MoviePad *b) {
    return a->bits == b->bits && !memcmp(a->stick, b->stick, sizeof a->stick) && a->flight == b->flight &&
           a->axes == b->axes && a->height == b->height;
}

/* (under lock) the game frame now: a new one at each native present; the start at the first read after play begins */
static int tick(void) {
    if (!started) {
        if (!hos_oot_play_ns()) return 0;
        started = 1, frame = 0, game_t = 0, last_present = InterlockedCompareExchange64(&hos_present_count, 0, 0);
        first_read_done = 0, next_mark = 0, next_report = 10;
        hos_log("input: %s from here (play began)", mode == 1 ? "recording" : "replaying");
        return 1;
    }
    const LONG64 present = InterlockedCompareExchange64(&hos_present_count, 0, 0);
    if (present != last_present) {
        const uint32_t bits = __atomic_load_n(&hos_frame_seconds_bits, __ATOMIC_RELAXED);
        const float dt = bits ? bits_f(bits) : 0.0f;
        const LONG64 frames = present - last_present;   /* (one, unless the game skipped reads for a frame) */
        game_t += frames * (dt > 0.0f && dt < 0.25f ? dt : 1.0 / 60.0);
        frame += (uint32_t)frames;
        last_present = present;
        first_read_done = 0;
    }
    return 1;
}

int hos_input_movie_active(void) {
    LONG m = mode;
    if (m < 0) {
        AcquireSRWLockExclusive(&lock);
        if (mode < 0) look();
        ReleaseSRWLockExclusive(&lock);
        m = mode;
    }
    return m > 0;
}

/* npad_states, a read of controller No.1: 1 with *given the pad to hand the game (a replay's, or a recorded frame's later
   read: one pad a frame), 0 when the live input goes in (then hos_input_movie_end with what it was) */
int hos_input_movie_begin(MoviePad *given) {
    if (!hos_input_movie_active()) return 0;
    int r = 0;
    AcquireSRWLockExclusive(&lock);
    if (tick()) {
        if (mode == 2) {
            const double now = by_frame ? frame : game_t;
            while (at_run + 1 < nruns && (by_frame ? runs[at_run + 1].f : runs[at_run + 1].t) <= now + 1e-6) at_run++;
            const PadRun *run = &runs[at_run];
            const double run_end = by_frame ? run->f + run->frames : run->t + run->frames / 60.0;
            if (at_run + 1 >= nruns && now >= run_end) {
                memset(given, 0, sizeof *given);   /* (past the end: a neutral pad) */
                if (!ended) {
                    ended = 1;
                    hos_log("input: the recording ended at %.2f s of game time (frame %u): Link was at most %.3f m from its "
                            "marks (at %.2f s)%s", game_t, frame, largest, largest_t, nmarks ? "" : " (no marks in the file)");
                }
            } else {
                *given = run->pad;
            }
            r = 1;
        } else if (first_read_done) {
            *given = cur.pad;   /* (a recorded frame's later read: the frame's first pad) */
            r = 1;
        }
    }
    ReleaseSRWLockExclusive(&lock);
    return r;
}

void hos_input_movie_end(const MoviePad *p) {
    if (mode != 1) return;
    AcquireSRWLockExclusive(&lock);
    if (started && !first_read_done) {
        first_read_done = 1;
        if (cur.frames && pad_same(&cur.pad, p) && cur.f + cur.frames == frame && cur.frames < RUN_MAX) {
            cur.frames++;
        } else {
            write_run();
            cur.f = frame, cur.t = game_t, cur.frames = 1, cur.pad = *p;
        }
    }
    ReleaseSRWLockExclusive(&lock);
}

/* hos_oot_lift_player, after Link's update: his position (actor +0x31c, x y z) */
void hos_input_movie_link(const float pos[3]) {
    if (mode <= 0 || !isfinite(pos[0]) || !isfinite(pos[1]) || !isfinite(pos[2])) return;
    AcquireSRWLockExclusive(&lock);
    if (started) {
        if (mode == 1 && out && game_t >= next_mark) {
            next_mark = game_t + MARK_EVERY;
            fprintf(out, "{\"f\":%u,\"t\":%.6f,\"cmd\":\"mark\",\"pos\":[%.9g,%.9g,%.9g]}\n", frame, game_t, pos[0], pos[1], pos[2]);
            fflush(out);
        } else if (mode == 2 && !ended) {
            while (at_mark < nmarks && marks[at_mark].t <= game_t + 1e-6) {
                const Mark *m = &marks[at_mark++];
                const float dx = pos[0] - m->pos[0], dy = pos[1] - m->pos[1], dz = pos[2] - m->pos[2];
                const float d = sqrtf(dx * dx + dy * dy + dz * dz);
                if (d > largest) largest = d, largest_t = m->t;
                if (d > APART_FIRST && !apart_said++)
                    hos_log("input: the replay first went its own way at %.2f s of game time (frame %u): Link %.3f m from the "
                            "recording's mark", m->t, frame, d);
                if (m->t >= next_report) {
                    next_report = m->t + 10;
                    hos_log("input: replay at %.0f s of game time: Link %.3f m from the recording (largest %.3f m)", m->t, d,
                            largest);
                }
            }
        }
    }
    ReleaseSRWLockExclusive(&lock);
}
