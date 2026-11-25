// Copyright (C) 2026 Sacrappy

/* (PIPE2, stage 0) The frame's two stages' slack, per frame, without a job trace (docs/research/pipe2-order-thread.md).

   OoT runs its frame as two stages joined once a frame each way (docs/research/pipe1-frame-overlap.md, section 1): the main
   thread's loop (the game) hands the frame over to the Presentation Thread (PT), which replays it, presents and signals the
   game's present event. Each stage's wait for the other is a guest wait the host sees:
     - the PT's wait for the hand-over: a message queue receive on the presenting thread (the game's receive, lr 0x834a3084: the
       same function serves other threads' queues, so only the presenting thread's count);
     - the main thread's present wait: an event wait (lr 0x81207a6c), signalled by the PT after its present.
   The PT waiting means the game's loop is the longer stage (game-bound); the main thread waiting means the replay's chain is
   (replay-bound: an overlap of the replay with the game can only win there). Logged every 600 presents with the share of
   frames whose present wait was over 0.5 ms. HL_SLACK=0: off. HL_SLACK_LRS=<pt queue lr>,<main event lr> (hex): other callers. */
#include "hos.h"
#include "hos_slack.h"
#include <stdlib.h>
#include <stdio.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

static uint64_t lr_pt = 0x834a3084, lr_main = 0x81207a6c;
static int on = -1;
static void *pt_thread;   /* (the presenting thread: nvn_native.c's present) */
static volatile LONG64 sum_ns[2], sum_n[2];

static int slack_on(void) {
    if (on < 0) {
        const char *e = getenv("HL_SLACK");
        const char *l = getenv("HL_SLACK_LRS");
        if (l) {
            unsigned long long a = 0, b = 0;
            if (sscanf(l, "%llx,%llx", &a, &b) == 2) lr_pt = a, lr_main = b;
        }
        on = !(e && e[0] == '0' && !e[1]);
    }
    return on;
}

void hos_slack_wait(int kind, uint64_t lr, int64_t ns) {
    if (ns <= 0 || !slack_on()) return;
    int k;
    if (kind == HOS_SLACK_QUEUE && lr == lr_pt && pt_thread && (void *)hos_self() == pt_thread) k = 0;
    else if (kind == HOS_SLACK_EVENT && lr == lr_main) k = 1;
    else return;
    InterlockedAdd64(&sum_ns[k], ns);
    InterlockedIncrement64(&sum_n[k]);
}

void hos_slack_present(void *thread) {
    if (!slack_on()) return;
    pt_thread = thread;
    static int64_t last[2], frames, over, t0;
    static double acc[2];
    const int64_t m = InterlockedCompareExchange64(&sum_ns[1], 0, 0), p = InterlockedCompareExchange64(&sum_ns[0], 0, 0);
    const int64_t dm = m - last[1], dp = p - last[0];
    last[1] = m, last[0] = p;
    if (!t0) { t0 = hos_now_ns(); return; }   /* (the first present: a base) */
    frames++, over += dm > 500000;
    acc[0] += dp / 1e6, acc[1] += dm / 1e6;
    if (frames < 600) return;
    const int64_t now = hos_now_ns();
    const double ms_frame = (now - t0) / 1e6 / frames;
    hos_log("slack (%lld frames, %.2f ms a frame): the PT waited %.2f ms a frame for the main thread's hand-over, the main thread %.2f ms "
            "a frame for the present (%.0f%% of frames over 0.5 ms): %s", (long long)frames, ms_frame, acc[0] / frames, acc[1] / frames,
            100.0 * over / frames, acc[1] / frames > 0.5 ? "replay-bound" : acc[0] / frames > 0.5 ? "game-bound" : "balanced");
    frames = over = 0, acc[0] = acc[1] = 0, t0 = now;
}
