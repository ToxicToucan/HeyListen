// Copyright (C) 2026 Sacrappy

/* host/hl_patched.c on a made-up hookshot_code_patches.txt and made-up native answers: an answer or gate for a function an exeFS mod
   patches is left to the lifted code, with one log line each; the others are not touched. */
#include "hl_patched.h"
#include <stdio.h>
#include <string.h>

static char lines[16][256];
static int nlines;
static void log_line(const char *l) { if (nlines < 16) snprintf(lines[nlines++], sizeof lines[0], "%s", l); }

static int failed;
#define CHECK(cond, what) do { if (!(cond)) { printf("FAIL: %s\n", what); failed = 1; } } while (0)

typedef struct { uint64_t guest; const char *what; int installed; } Answer;

/* the host's rule (hl_host.c answer_on): install unless a mod patches the function */
static void install(Answer *a) { a->installed = !hl_patched_left(a->guest, a->what, log_line); }

int main(void) {
    static const char list[] =
        "# code patches of this build\n"
        "range 0x80123456 0x80123458 main My Mod\n"
        "range 0x80200010 0x80200014 main Other\r\n"
        "function 0x80123400 0x80123480 My Mod\n"
        "function 0x80200000 0x80200040 Other\n"
        "function 0x80300000 0x80300010 Spaced Mod Name\n";
    Answer answers[] = {
        {0x80123400ULL, "a body's world box", 0},   /* the patched function's first address */
        {0x80123480ULL, "the next function", 0},    /* the end is not inside */
        {0x80200000ULL, "a box", 0},
        {0x80400000ULL, "something else", 0},
        {0x80300008ULL, "an address inside a function", 0},
    };
    CHECK(hl_patched_parse(list) == 5, "five entries read");
    CHECK(hl_patched_mod(0x80123456ULL) && !strcmp(hl_patched_mod(0x80123456ULL), "My Mod"), "a range names its mod");
    CHECK(hl_patched_mod(0x80123458ULL) && !strcmp(hl_patched_mod(0x80123458ULL), "My Mod"), "inside the function it is");
    CHECK(hl_patched_mod(0x80500000ULL) == NULL, "elsewhere nothing");
    for (unsigned i = 0; i < sizeof answers / sizeof answers[0]; i++) install(&answers[i]);
    CHECK(!answers[0].installed, "the answer for the patched function is not installed");
    CHECK(answers[1].installed, "the function after it is");
    CHECK(!answers[2].installed, "a second mod's function is left to the lifted code too");
    CHECK(answers[3].installed, "an unpatched function keeps its answer");
    CHECK(!answers[4].installed, "a mod name with spaces, an address inside the function");
    CHECK(nlines == 3, "one line for each function left out");
    CHECK(!strcmp(lines[0], "hl: a body's world box 0x80123400 left to the lifted code: an exeFS mod patches it (mods/My Mod)"), lines[0]);
    CHECK(strstr(lines[1], "(mods/Other)") != NULL, "the other mod is named");
    CHECK(strstr(lines[2], "(mods/Spaced Mod Name)") != NULL, "a name with spaces");
    CHECK(hl_patched_parse(NULL) == 0 && hl_patched_mod(0x80123400ULL) == NULL, "no list: nothing is patched");
    CHECK(hl_patched_parse("") == 0, "an empty list: nothing is patched");
    nlines = 0;
    CHECK(!hl_patched_left(0x80123400ULL, "x", log_line) && nlines == 0, "nothing logged when nothing is patched");
    if (!failed) printf("ok: exeFS mods: patched functions are left to the lifted code, one line each\n");
    return failed;
}
