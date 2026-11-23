// Copyright (C) 2026 Sacrappy

/* What host/hos_regionkeep.c takes from the engine, for tests/region_keep_test.c (HK_TEST): a small window in place of the guest's,
   the log on stdout, the clock, and the discard the test counts. */
#ifndef HK_TEST_STUBS_H
#define HK_TEST_STUBS_H
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <windows.h>

extern uint8_t *hk_base;
#define GP(a) ((void *)(hk_base + (uint64_t)(a)))
#define HOS_WRITTEN(p, n) ((void)(p), (void)(n))
static inline int hos_gp_ready(void) { return 0; }
static inline int rx_guest_imported(uint64_t lo, uint64_t hi) { (void)lo; (void)hi; return 0; }
static inline int64_t hos_now_ns(void) {
    LARGE_INTEGER f, c;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&c);
    return (int64_t)((double)c.QuadPart / (double)f.QuadPart * 1e9);
}
static inline void hos_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fputs("  log: ", stdout);
    vprintf(fmt, ap);
    fputc('\n', stdout);
    va_end(ap);
}
#endif
