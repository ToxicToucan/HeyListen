// Copyright (C) 2026 Sacrappy

/* (heylisten) What a guest thread waited on, by kind, for the performance summary's os_wait hitches (gpu/vk/perf_report.h's
   os_wait_kind): the time the calling thread spent blocked in each kind of wait, nanoseconds, since it started. The presenting
   thread hands the difference since its last present to the renderer (nvn_native.c's push, gpu/vk/perf_parts.h's ids). A thread's
   own counters: nothing shared, one clock read at each end of a wait that was going to block anyway.

     lock   hos_thread_wait and hos_cv_sleep: the guest's mutexes, condition variables, events, semaphores, message queues
     sleep  a guest or host sleep (the pacing sleep too: the renderer takes the pace part out)
     vcore  waiting for a virtual core after a block (vc_acquire)
     file   a file open, read, write or flush (nn_fs.c) */
#pragma once

#include <stdint.h>

enum { HOS_WN_LOCK, HOS_WN_SLEEP, HOS_WN_VCORE, HOS_WN_FILE, HOS_WN_KINDS };
extern _Thread_local int64_t hos_wn_ns[HOS_WN_KINDS];
static inline void hos_wn_add(int kind, int64_t ns) {
    if (ns > 0) hos_wn_ns[kind] += ns;
}
