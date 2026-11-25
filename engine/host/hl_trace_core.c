// Copyright (C) 2026 Sacrappy

/* (HeyListen native engine) The crash recorder's core (hl_trace.h): the threads' rings, the live and freed blocks' tables,
   the report and the file. Plain C11 with no OS layer, so that tests/hl_trace_test.c runs it with no game. */
#include "hl_trace.h"
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#if defined(__x86_64__) || defined(_M_X64)
#include <x86intrin.h>
#endif

volatile uint32_t hl_trace_mask;

typedef struct {
    uint64_t stamp;
    uint32_t kind, c;
    uint64_t a, b;
} Event;
_Static_assert(sizeof(Event) == 32, "an event is 32 bytes");

typedef struct {
    _Atomic uint64_t head;   /* events written so far (the slot of event i is i & mask) */
    uint32_t tid;
    char name[32];
    Event *ev;
} Ring;

enum { MAX_RINGS = 512, STRIPES = 256 };
static HlTraceConfig cfg;
static Ring rings[MAX_RINGS];
static _Atomic uint32_t nrings;
static _Thread_local Ring *my_ring;
static uint32_t ring_mask;
static double tick_ms = 1e6;

/* the live blocks: STRIPES open-addressed tables (linear probing, backward-shift removal), each under a spin lock */
typedef struct {
    atomic_flag lock;
    uint32_t used;
    HlTraceRecord *slot;   /* addr 0: empty */
} Stripe;
static Stripe stripes[STRIPES];
static uint32_t stripe_slots;
/* the last frees: a ring */
static HlTraceFree *freed;
static uint32_t freed_mask;
static _Atomic uint64_t freed_head;
static _Atomic uint64_t n_allocs, n_frees, n_unknown, n_full;
/* the back end's writes into guest memory, mirrors and imports: a ring */
typedef struct {
    uint64_t guest, size, pool, value, done, stamp;
    uint32_t what, tid;
} GpuRecord;
enum { GPU_RECORDS = 1 << 18 };
static GpuRecord *gpu;
static _Atomic uint64_t gpu_head, gpu_done;
/* the write-back watch's targets: by guest page (open addressing), up to WB_PER ranges written back in each */
typedef struct {
    uint64_t lo, hi, first, last, value;   /* the range, its first and last write-back (ticks), the last one's submission */
    uint64_t count;                        /* write-backs of it */
    HlTraceRecord blk;                     /* a heap block live over it, allocated while it was written back (addr 0: none) */
    uint32_t what, told[3];                /* told: lines written of each kind (into live, allocated over, freed under) */
} WbTarget;
enum { WB_PAGES = 1 << 15, WB_PER = 4, WB_PROBE = 32, WB_MAX = 16 << 10 };
typedef struct {
    uint64_t page;   /* page number + 1 (0: empty) */
    WbTarget t[WB_PER];
} WbPage;
static WbPage *wbp;
static atomic_flag wb_lock = ATOMIC_FLAG_INIT;
static _Atomic uint64_t wb_n[5];   /* into live, allocated over, freed under, targets, targets hit */

uint64_t hl_trace_now(void) {
#if defined(__x86_64__) || defined(_M_X64)
    return __rdtsc();
#else
    struct timespec t;
    timespec_get(&t, TIME_UTC);
    return (uint64_t)t.tv_sec * 1000000000u + (uint64_t)t.tv_nsec;
#endif
}

double hl_trace_ticks_per_ms(void) { return tick_ms; }

static uint64_t wall_ns(void) {
    struct timespec t;
    timespec_get(&t, TIME_UTC);
    return (uint64_t)t.tv_sec * 1000000000u + (uint64_t)t.tv_nsec;
}

int hl_trace_start(uint32_t mask, const HlTraceConfig *config) {
    if (hl_trace_mask) return 1;
    cfg = *config;
    const uint32_t events = cfg.ring_events ? cfg.ring_events : 65536;
    ring_mask = events - 1;
    stripe_slots = (cfg.live_slots ? cfg.live_slots : 2u << 20) / STRIPES;
    const uint32_t nfreed = cfg.freed_slots ? cfg.freed_slots : 1u << 20;
    freed_mask = nfreed - 1;
    freed = calloc(nfreed, sizeof *freed);
    gpu = calloc(GPU_RECORDS, sizeof *gpu);
    if (!freed || !gpu) return 0;
    if (cfg.wb_watch && !(wbp = calloc(WB_PAGES, sizeof *wbp))) cfg.wb_watch = 0;
    for (int s = 0; s < STRIPES; s++) {
        atomic_flag_clear(&stripes[s].lock);
        stripes[s].slot = calloc(stripe_slots, sizeof(HlTraceRecord));
        if (!stripes[s].slot) return 0;
    }
    /* (the clock: ticks against the wall clock over about 20 ms) */
    const uint64_t w0 = wall_ns(), t0 = hl_trace_now();
    while (wall_ns() - w0 < 20000000u) {}
    const uint64_t w1 = wall_ns(), t1 = hl_trace_now();
    tick_ms = (double)(t1 - t0) / ((double)(w1 - w0) / 1e6);
    if (tick_ms <= 0) tick_ms = 1e6;
    hl_trace_mask = mask;
    return 1;
}

/* The calling thread's ring, made and listed at its first event. */
static Ring *ring(void) {
    Ring *r = my_ring;
    if (r) return r;
    const uint32_t i = atomic_fetch_add(&nrings, 1);
    if (i >= MAX_RINGS) return NULL;
    r = &rings[i];
    r->ev = calloc((size_t)ring_mask + 1, sizeof(Event));
    r->tid = i;
    const char *name = cfg.thread_name ? cfg.thread_name() : NULL;
    snprintf(r->name, sizeof r->name, "%s", name ? name : "?");
    my_ring = r;
    return r;
}

void hl_trace_event(uint32_t kind, uint64_t a, uint64_t b, uint32_t c) {
    Ring *r = ring();
    if (!r || !r->ev) return;
    const uint64_t h = atomic_load_explicit(&r->head, memory_order_relaxed);
    Event *e = &r->ev[h & ring_mask];
    e->stamp = hl_trace_now();
    e->kind = kind;
    e->c = c;
    e->a = a;
    e->b = b;
    atomic_store_explicit(&r->head, h + 1, memory_order_release);   /* (a reader takes events below head) */
}

/* ---- the live blocks ---- */

static uint32_t hash(uint64_t a) { return (uint32_t)((a >> 4) * 0x9E3779B97F4A7C15ull >> 32); }

/* the write-back watch (hl_trace.h hl_trace_wb_counts; below) */
static void wb_block(uint64_t block, uint64_t size, int freed, const HlTraceRecord *r);
static void wb_write(uint32_t what, uint64_t guest, uint64_t size, uint64_t value);

static void lock(Stripe *s) {
    while (atomic_flag_test_and_set_explicit(&s->lock, memory_order_acquire)) {}
}
static void unlock(Stripe *s) { atomic_flag_clear_explicit(&s->lock, memory_order_release); }

static void record(HlTraceRecord *r, uint64_t block, uint64_t size, uint64_t heap, const uint32_t *chain) {
    r->addr = block;
    r->size = size;
    r->heap = heap;
    r->stamp = hl_trace_now();
    Ring *g = ring();
    r->tid = g ? g->tid : 0xffffffffu;
    for (int k = 0; k < HL_TRACE_CHAIN; k++) r->chain[k] = chain ? chain[k] : 0;
}

void hl_trace_alloc(uint64_t block, uint64_t size, uint64_t heap, const uint32_t *chain) {
    if (!block || !hl_trace_mask) return;
    atomic_fetch_add_explicit(&n_allocs, 1, memory_order_relaxed);
    hl_trace_event(HL_EV_ALLOC, block, size, chain ? chain[0] : 0);
    const uint32_t h = hash(block);
    Stripe *s = &stripes[h & (STRIPES - 1)];
    const uint32_t m = stripe_slots - 1;
    lock(s);
    uint32_t i = (h >> 8) & m;
    while (s->slot[i].addr && s->slot[i].addr != block) i = (i + 1) & m;
    if (!s->slot[i].addr) {
        if (s->used + 1 >= stripe_slots - stripe_slots / 8) {   /* (full enough: not listed) */
            unlock(s);
            atomic_fetch_add_explicit(&n_full, 1, memory_order_relaxed);
            return;
        }
        s->used++;
    }
    record(&s->slot[i], block, size, heap, chain);
    const HlTraceRecord rec = s->slot[i];
    unlock(s);
    if (cfg.wb_watch) wb_block(block, size, 0, &rec);
}

void hl_trace_free(uint64_t block, uint64_t heap, const uint32_t *chain) {
    if (!block || !hl_trace_mask) return;
    atomic_fetch_add_explicit(&n_frees, 1, memory_order_relaxed);
    HlTraceFree f;
    memset(&f, 0, sizeof f);
    const uint32_t h = hash(block);
    Stripe *s = &stripes[h & (STRIPES - 1)];
    const uint32_t m = stripe_slots - 1;
    lock(s);
    uint32_t i = (h >> 8) & m;
    while (s->slot[i].addr && s->slot[i].addr != block) i = (i + 1) & m;
    if (s->slot[i].addr) {
        f.alloc = s->slot[i];
        f.has_alloc = 1;
        /* (backward-shift removal: the entries after it move up while their home is not between) */
        uint32_t j = i;
        for (;;) {
            s->slot[i].addr = 0;
            for (;;) {
                j = (j + 1) & m;
                if (!s->slot[j].addr) goto removed;
                const uint32_t home = (hash(s->slot[j].addr) >> 8) & m;
                if (i <= j ? (i < home && home <= j) : (i < home || home <= j)) continue;
                break;
            }
            s->slot[i] = s->slot[j];
            i = j;
        }
    removed:
        s->used--;
    }
    unlock(s);
    if (!f.has_alloc) atomic_fetch_add_explicit(&n_unknown, 1, memory_order_relaxed);
    record(&f.freed, block, f.has_alloc ? f.alloc.size : 0, heap, chain);
    hl_trace_event(HL_EV_FREE, block, f.freed.size, chain ? chain[0] : 0);
    const uint64_t at = atomic_fetch_add_explicit(&freed_head, 1, memory_order_relaxed);
    freed[at & freed_mask] = f;
    if (cfg.wb_watch) wb_block(block, f.freed.size, 1, &f.freed);
}

void hl_trace_gpu_record(uint32_t what, uint64_t guest, uint64_t size, uint64_t pool, uint64_t value, uint64_t done) {
    if (!hl_trace_mask || !gpu) return;
    hl_trace_event(HL_EV_GPU, guest, size, what);
    Ring *r = ring();
    const uint64_t at = atomic_fetch_add_explicit(&gpu_head, 1, memory_order_relaxed);
    gpu[at & (GPU_RECORDS - 1)] = (GpuRecord){guest, size, pool, value, done, hl_trace_now(), what, r ? r->tid : 0xffffffffu};
    uint64_t d = atomic_load_explicit(&gpu_done, memory_order_relaxed);
    while (done > d && !atomic_compare_exchange_weak(&gpu_done, &d, done)) {}
    if (cfg.wb_watch && (what == 1 || what == 2)) wb_write(what, guest, size, value);
}

int hl_trace_lookup(uint64_t addr, HlTraceRecord *live, HlTraceFree *fr) {
    int got = 0;
    HlTraceRecord lv;
    memset(&lv, 0, sizeof lv);
    if (!hl_trace_mask) return 0;
    /* (the smallest live block holding it: a heap's arena is itself a block of its parent heap) */
    for (int k = 0; k < STRIPES; k++) {
        const Stripe *s = &stripes[k];
        for (uint32_t i = 0; i < stripe_slots; i++) {
            const HlTraceRecord *r = &s->slot[i];
            if (r->addr && addr >= r->addr && addr < r->addr + (r->size ? r->size : 1) && (!(got & 1) || r->size < lv.size)) {
                lv = *r;
                got |= 1;
            }
        }
    }
    const uint64_t head = atomic_load(&freed_head);
    const uint64_t n = head < (uint64_t)freed_mask + 1 ? head : (uint64_t)freed_mask + 1;
    for (uint64_t k = 1; k <= n; k++) {   /* (the latest free first) */
        const HlTraceFree *f = &freed[(head - k) & freed_mask];
        const uint64_t size = f->freed.size ? f->freed.size : 1;
        if (f->freed.addr && addr >= f->freed.addr && addr < f->freed.addr + size) {
            /* (a live block over it allocated before this free is not "over it") */
            if (!(got & 1) || lv.stamp > f->freed.stamp) {
                if (fr) *fr = *f;
                got |= 2;
            }
            break;
        }
    }
    if ((got & 1) && live) *live = lv;
    return got;
}

void hl_trace_live_each(void (*fn)(const HlTraceRecord *, void *), void *user) {
    if (!hl_trace_mask) return;
    for (int k = 0; k < STRIPES; k++) {
        Stripe *s = &stripes[k];
        lock(s);
        for (uint32_t i = 0; i < stripe_slots; i++)
            if (s->slot[i].addr) fn(&s->slot[i], user);
        unlock(s);
    }
}

/* ---- (CRASH3) the write-back watch: GPU words copied back over memory the game's heap gave to the CPU ---- */

static void wb_lock_take(void) {
    while (atomic_flag_test_and_set_explicit(&wb_lock, memory_order_acquire)) {}
}
static void wb_lock_drop(void) { atomic_flag_clear_explicit(&wb_lock, memory_order_release); }

/* (wb_lock held) page's entry; make: a new one (in an empty slot, else over the probed entry written back the longest ago) */
static WbPage *wb_page(uint64_t page, int make) {
    const uint32_t h = hash(page << 12) & (WB_PAGES - 1);
    WbPage *oldest = NULL;
    uint64_t oldest_last = ~0ull;
    for (int k = 0; k < WB_PROBE; k++) {
        WbPage *p = &wbp[(h + (uint32_t)k) & (WB_PAGES - 1)];
        if (p->page == page + 1) return p;
        if (!p->page) {
            if (!make) return NULL;
            memset(p, 0, sizeof *p);
            p->page = page + 1;
            return p;
        }
        uint64_t last = 0;
        for (int j = 0; j < WB_PER; j++) last = p->t[j].last > last ? p->t[j].last : last;
        if (last < oldest_last) oldest_last = last, oldest = p;
    }
    if (!make || !oldest) return NULL;
    memset(oldest, 0, sizeof *oldest);
    oldest->page = page + 1;
    return oldest;
}

typedef struct {
    char text[4][512];
    int n;
} WbLines;
static void wb_line(WbLines *l, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void wb_line(WbLines *l, const char *fmt, ...) {
    if (l->n >= 4) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(l->text[l->n++], sizeof l->text[0], fmt, ap);
    va_end(ap);
}
static void wb_flush(const WbLines *l) {
    for (int k = 0; k < l->n; k++)
        if (cfg.out) cfg.out(l->text[k]);
}
static int wb_tell(uint32_t *told) {   /* the first 3 of a kind for a target, then every 1000th */
    const uint32_t n = (*told)++;
    return n < 3 || n % 1000 == 999;
}
static const char *gpu_what(uint32_t w);
static double ms_ago(uint64_t stamp, uint64_t now);
static const char *ring_name(uint32_t tid);
static void chain_text(char *out, size_t cap, const uint32_t *chain);

static void wb_write(uint32_t what, uint64_t guest, uint64_t size, uint64_t value) {
    if (!size || size > WB_MAX || !wbp) return;
    const uint64_t now = hl_trace_now(), hi = guest + size;
    WbLines lines;
    lines.n = 0;
    int hit = 0;
    wb_lock_take();
    for (uint64_t page = guest >> 12; page <= (hi - 1) >> 12; page++) {
        WbPage *p = wb_page(page, 1);
        if (!p) continue;
        WbTarget *t = NULL, *free_t = NULL, *old_t = NULL;
        for (int j = 0; j < WB_PER && !t; j++) {
            WbTarget *c = &p->t[j];
            if (c->lo == guest && c->hi == hi) t = c;
            else if (!c->hi && !free_t) free_t = c;
            else if (!old_t || c->last < old_t->last) old_t = c;
        }
        if (!t) {
            t = free_t ? free_t : old_t;
            memset(t, 0, sizeof *t);
            t->lo = guest, t->hi = hi, t->first = now;
            if (page == guest >> 12) atomic_fetch_add_explicit(&wb_n[3], 1, memory_order_relaxed);
        }
        t->last = now, t->value = value, t->what = what;
        t->count++;
        if (t->blk.addr && !hit) {
            hit = 1;
            atomic_fetch_add_explicit(&wb_n[0], 1, memory_order_relaxed);
            if (!t->told[0] && !t->told[1]) atomic_fetch_add_explicit(&wb_n[4], 1, memory_order_relaxed);
            if (wb_tell(&t->told[0])) {
                char ch[96];
                chain_text(ch, sizeof ch, t->blk.chain);
                wb_line(&lines, "TRACE WB: %s INTO A LIVE HEAP BLOCK: [%#llx, %#llx) submission %llu (%llu write-backs of it since "
                        "%.3f ms ago; %u told); block %#llx size %#llx heap %#llx allocated %.3f ms ago on %s by%s",
                        gpu_what(what), (unsigned long long)guest, (unsigned long long)hi, (unsigned long long)value,
                        (unsigned long long)t->count, ms_ago(t->first, now), t->told[0], (unsigned long long)t->blk.addr,
                        (unsigned long long)t->blk.size, (unsigned long long)t->blk.heap, ms_ago(t->blk.stamp, now),
                        ring_name(t->blk.tid), ch);
            }
        }
    }
    wb_lock_drop();
    wb_flush(&lines);
}

static void wb_block(uint64_t block, uint64_t size, int freed, const HlTraceRecord *r) {
    if (!wbp || size > (64u << 10)) return;   /* (a heap's arena is a block of its parent: not the CPU's words) */
    const uint64_t lo = block >= 16 ? block - 16 : 0, hi = block + (size ? size : 16);   /* (with its header) */
    const uint64_t now = hl_trace_now(), window = (uint64_t)(5000.0 * tick_ms);
    WbLines lines;
    lines.n = 0;
    wb_lock_take();
    for (uint64_t page = lo >> 12; page <= (hi - 1) >> 12; page++) {
        WbPage *p = wb_page(page, 0);
        if (!p) continue;
        for (int j = 0; j < WB_PER; j++) {
            WbTarget *t = &p->t[j];
            if (!t->hi || !(t->lo < hi && lo < t->hi)) continue;
            if (freed && t->blk.addr == block) t->blk.addr = 0;
            if (now - t->last > window) continue;   /* (not written back lately) */
            const int kind = freed ? 2 : 1;
            if (!freed) t->blk = *r;
            if (page != ((t->lo > lo ? t->lo : lo) >> 12)) continue;   /* (one count for a block over a target's pages) */
            atomic_fetch_add_explicit(&wb_n[kind], 1, memory_order_relaxed);
            if (!t->told[0] && !t->told[1] && !t->told[2]) atomic_fetch_add_explicit(&wb_n[4], 1, memory_order_relaxed);
            if (wb_tell(&t->told[kind])) {
                char ch[96];
                chain_text(ch, sizeof ch, r->chain);
                wb_line(&lines, "TRACE WB: %s A WRITE-BACK TARGET: block %#llx size %#llx heap %#llx (with its header [%#llx, "
                        "%#llx)) on %s by%s; target [%#llx, %#llx) (%s) written back %llu times since %.3f ms ago, the last "
                        "%.3f ms ago in submission %llu",
                        freed ? "FREED UNDER" : "ALLOCATED OVER", (unsigned long long)block, (unsigned long long)size,
                        (unsigned long long)r->heap, (unsigned long long)lo, (unsigned long long)hi, ring_name(r->tid), ch,
                        (unsigned long long)t->lo, (unsigned long long)t->hi, gpu_what(t->what), (unsigned long long)t->count,
                        ms_ago(t->first, now), ms_ago(t->last, now), (unsigned long long)t->value);
            }
        }
    }
    wb_lock_drop();
    wb_flush(&lines);
}

void hl_trace_wb_counts(HlTraceWbCounts *out) {
    out->into_live = atomic_load(&wb_n[0]);
    out->allocs_over = atomic_load(&wb_n[1]);
    out->frees_under = atomic_load(&wb_n[2]);
    out->targets = atomic_load(&wb_n[3]);
    out->targets_hit = atomic_load(&wb_n[4]);
}

/* ---- the report ---- */

static void outf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void outf(const char *fmt, ...) {
    char line[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    if (cfg.out) cfg.out(line);
}

static const char *ring_name(uint32_t tid) { return tid < MAX_RINGS && tid < atomic_load(&nrings) ? rings[tid].name : "?"; }

static double ms_ago(uint64_t stamp, uint64_t now) { return (double)(int64_t)(now - stamp) / tick_ms; }

static void chain_text(char *out, size_t cap, const uint32_t *chain) {
    int n = 0;
    out[0] = 0;
    for (int k = 0; k < HL_TRACE_CHAIN && chain[k] && n < (int)cap - 12; k++) n += snprintf(out + n, cap - n, " %#x", chain[k]);
}

static void print_record(const char *what, const HlTraceRecord *r, uint64_t now) {
    char ch[96];
    chain_text(ch, sizeof ch, r->chain);
    outf("TRACE:     %s %.3f ms ago on %s, heap %#llx, size %#llx, by%s", what, ms_ago(r->stamp, now), ring_name(r->tid),
         (unsigned long long)r->heap, (unsigned long long)r->size, ch);
}

static const char *gpu_what(uint32_t w) {
    static const char *const names[] = {"?", "write-back", "lazy release's write-back", "mirror made", "mirror given back",
                                        "import made", "import given back", "readback page"};
    return w < sizeof names / sizeof names[0] ? names[w] : "?";
}

/* The back end's latest records over [lo, hi): its writes into guest memory first (the newest 6), and the mirrors and
   imports of the pools there (the newest 3). */
static void print_gpu(uint64_t lo, uint64_t hi, uint64_t now) {
    if (!gpu) return;
    const uint64_t head = atomic_load(&gpu_head), done = atomic_load(&gpu_done);
    const uint64_t n = head < GPU_RECORDS ? head : GPU_RECORDS;
    int writes = 0, lives = 0;
    for (uint64_t k = 1; k <= n && (writes < 6 || lives < 3); k++) {
        const GpuRecord *g = &gpu[(head - k) & (GPU_RECORDS - 1)];
        if (!(g->guest < hi && lo < g->guest + (g->size ? g->size : 1))) continue;
        const int write = g->what == 1 || g->what == 2;
        if (write ? writes++ >= 6 : lives++ >= 3) continue;
        outf("TRACE:     %s by the back end %.3f ms ago on %s: [%#llx, %#llx) of pool %#llx, submission %llu (the GPU had "
             "done %llu then; %llu at the last record)", gpu_what(g->what), ms_ago(g->stamp, now), ring_name(g->tid),
             (unsigned long long)g->guest, (unsigned long long)(g->guest + g->size), (unsigned long long)g->pool,
             (unsigned long long)g->value, (unsigned long long)g->done, (unsigned long long)done);
    }
}

static void print_block(const char *what, uint64_t addr, uint64_t now) {
    HlTraceRecord live;
    HlTraceFree fr;
    const int got = hl_trace_lookup(addr, &live, &fr);
    if (!got) {   /* (no block: the back end's records of the address itself) */
        print_gpu(addr, addr + 1, now);
        return;
    }
    if (got & 2) {
        outf("TRACE: %s %#llx is in a FREED block %#llx (+%#llx)%s", what, (unsigned long long)addr,
             (unsigned long long)fr.freed.addr, (unsigned long long)(addr - fr.freed.addr),
             (got & 1) ? ", since allocated again (below)" : "");
        print_record("freed", &fr.freed, now);
        if (fr.has_alloc) print_record("allocated", &fr.alloc, now);
    }
    if (got & 1) {
        outf("TRACE: %s %#llx is in a live block %#llx (+%#llx)", what, (unsigned long long)addr, (unsigned long long)live.addr,
             (unsigned long long)(addr - live.addr));
        print_record("allocated", &live, now);
    }
    const uint64_t lo = (got & 1) ? live.addr : fr.freed.addr;
    const uint64_t size = (got & 1) ? live.size : fr.freed.size;
    print_gpu(lo, lo + (size ? size : 1), now);
}

static const char *kind_name(uint32_t kind) {
    switch (kind) {
    case HL_EV_ALLOC: return "alloc";
    case HL_EV_FREE: return "free";
    case HL_EV_HEAP: return "heap";
    case HL_EV_UNMAP: return "unmap";
    case HL_EV_NVN: return "nvn";
    case HL_EV_GPU: return "gpu";
    case HL_EV_COLD: return "cold";
    case HL_EV_DROP: return "drop";
    case HL_EV_ZERO: return "zero";
    case HL_EV_STAGE: return "stage";
    case HL_EV_GROUP: return "group";
    case HL_EV_JOB: return "job";
    case HL_EV_FILE: return "file";
    default: return "?";
    }
}

typedef struct {
    Event e;
    uint32_t tid;
} Tagged;

static int by_stamp(const void *x, const void *y) {
    const uint64_t a = ((const Tagged *)x)->e.stamp, b = ((const Tagged *)y)->e.stamp;
    return a < b ? -1 : a > b;
}

void hl_trace_report(uint64_t fault, const uint64_t *regs, const char *const *names, int n, int own, int others) {
    if (!hl_trace_mask) return;
    const uint64_t now = hl_trace_now();
    uint64_t a, f, u, full, t;
    hl_trace_counts(&a, &f, &u, &full, &t);
    outf("TRACE: the recorder: %llu allocations, %llu frees (%llu of blocks it did not see allocated), %llu not listed "
         "(table full), %llu threads", (unsigned long long)a, (unsigned long long)f, (unsigned long long)u,
         (unsigned long long)full, (unsigned long long)t);
    if (fault) print_block("the faulting address", fault, now);
    for (int k = 0; k < n; k++)
        if (regs[k] >= 0x10000) print_block(names[k], regs[k], now);
    const uint32_t nr = atomic_load(&nrings) < MAX_RINGS ? atomic_load(&nrings) : MAX_RINGS;
    size_t cap = 0;
    for (uint32_t r = 0; r < nr; r++) cap += (size_t)(&rings[r] == my_ring ? own : others);
    Tagged *all = calloc(cap ? cap : 1, sizeof *all);
    if (!all) return;
    size_t m = 0;
    for (uint32_t r = 0; r < nr; r++) {
        Ring *g = &rings[r];
        if (!g->ev) continue;
        const uint64_t head = atomic_load_explicit(&g->head, memory_order_acquire);
        const uint64_t want = (uint64_t)(g == my_ring ? own : others);
        const uint64_t take = head < want ? head : want;
        for (uint64_t k = head - take; k < head; k++) all[m++] = (Tagged){g->ev[k & ring_mask], r};
    }
    qsort(all, m, sizeof *all, by_stamp);
    outf("TRACE: the last events (%d of this thread, %d of each other), oldest first:", own, others);
    for (size_t k = 0; k < m; k++) {
        const Event *e = &all[k].e;
        outf("TRACE: %10.3f ms %-24s %-6s %#llx %#llx %#x", -ms_ago(e->stamp, now), ring_name(all[k].tid), kind_name(e->kind),
             (unsigned long long)e->a, (unsigned long long)e->b, e->c);
    }
    free(all);
}

void hl_trace_counts(uint64_t *allocs, uint64_t *frees, uint64_t *unknown_frees, uint64_t *live_full, uint64_t *threads) {
    *allocs = atomic_load(&n_allocs);
    *frees = atomic_load(&n_frees);
    *unknown_frees = atomic_load(&n_unknown);
    *live_full = atomic_load(&n_full);
    *threads = atomic_load(&nrings);
}

/* ---- the file (tools/bench/hltrace.py) ----
   "HLTRACE1", ticks a millisecond (double), the clock now (u64), ring events (u32), rings (u32); each ring: name (32),
   id (u32), head (u64), its events (ring events x 32 bytes); frees kept (u32), freed head (u64), the freed ring
   (HlTraceFree each); live slots (u32), the live table (HlTraceRecord each, address 0 empty); the back end's records kept`n   (u32), their head (u64), their ring (GpuRecord each). */
int hl_trace_write(const char *path) {
    if (!hl_trace_mask) return -1;
    FILE *fp = fopen(path, "wb");
    if (!fp) return -1;
    uint64_t now = hl_trace_now();
    const uint32_t events = ring_mask + 1, nr = atomic_load(&nrings) < MAX_RINGS ? atomic_load(&nrings) : MAX_RINGS;
    fwrite("HLTRACE1", 1, 8, fp);
    fwrite(&tick_ms, sizeof tick_ms, 1, fp);
    fwrite(&now, sizeof now, 1, fp);
    fwrite(&events, 4, 1, fp);
    fwrite(&nr, 4, 1, fp);
    for (uint32_t r = 0; r < nr; r++) {
        const uint64_t head = atomic_load(&rings[r].head);
        fwrite(rings[r].name, 1, 32, fp);
        fwrite(&rings[r].tid, 4, 1, fp);
        fwrite(&head, 8, 1, fp);
        if (rings[r].ev) fwrite(rings[r].ev, sizeof(Event), events, fp);
        else for (uint32_t k = 0; k < events; k++) fwrite(&(Event){0}, sizeof(Event), 1, fp);
    }
    const uint32_t nf = freed_mask + 1;
    const uint64_t fh = atomic_load(&freed_head);
    fwrite(&nf, 4, 1, fp);
    fwrite(&fh, 8, 1, fp);
    fwrite(freed, sizeof *freed, nf, fp);
    const uint32_t nl = stripe_slots * STRIPES;
    fwrite(&nl, 4, 1, fp);
    for (int s = 0; s < STRIPES; s++) fwrite(stripes[s].slot, sizeof(HlTraceRecord), stripe_slots, fp);
    const uint32_t ng = GPU_RECORDS;
    const uint64_t gh = atomic_load(&gpu_head);
    fwrite(&ng, 4, 1, fp);
    fwrite(&gh, 8, 1, fp);
    fwrite(gpu, sizeof *gpu, ng, fp);
    /* (the clock as the file ends: the threads went on while it was written, and every event is older than this) */
    now = hl_trace_now();
    fseek(fp, 16, SEEK_SET);
    fwrite(&now, sizeof now, 1, fp);
    return fclose(fp) ? -1 : 0;
}
