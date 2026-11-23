// Copyright (C) 2026 Sacrappy

// (HeyListen, v0.4.4) What a CPU read of GPU results waited for, said in the hitch rows, and when the light meter's strict wait is
// bounded. Plain functions: tests/test_readback_wait.py compiles this header and checks them.
//
// The labels: "<phase>:<size hex>[:meter][@<site hex>][ pool <hex>]" where the phase is what the read waited in longest:
//   rec    the replay reaching the recording the write-back is in (wait_recorded, or the catch-up before it)
//   gpu    the GPU's timeline reaching the write-back's value (wait_value)
//   strict the strict wait for the write-back's own value (RX_VK_READBACK_STRICT, RX_VK_STRICT_SLOW_MS)
//   cut    the bounded strict wait ran out and the read went on (RX_VK_STRICT_BOUND_MS)
//   tex    the catch-up for textures the GPU wrote there and their texels into memory
// "meter" marks the page of the light meter (the exposure sums); the site is the guest function that asked, by its address in
// the exe's map (RX_VK_RB_PROF's), the pool the guest address of the read's pool.
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace readback_wait {

enum Phase { kRecorded, kGpu, kStrict, kCut, kTextures, kPhases };
inline const char* phase_name(int p) {
    static const char* const t[] = {"rec", "gpu", "strict", "cut", "tex"};
    return p >= 0 && p < kPhases ? t[p] : "rec";
}

/// The phase a read waited in longest, from the time (ns) it spent in each; a strict wait that ran out is "cut". Ties go to the
/// first of them in the enum's order
inline int longest(const int64_t (&ns)[kPhases], bool cut) {
    int best = kRecorded;
    for (int i = 1; i < kPhases; i++)
        if (ns[i] > ns[best]) best = i;
    if (cut && best == kStrict) best = kCut;
    return best;
}

inline void label(char* out, size_t cap, int phase, uint64_t size, bool meter, unsigned site, uint64_t pool) {
    int n = snprintf(out, cap, "%s:%llx%s", phase_name(phase), (unsigned long long)size, meter ? ":meter" : "");
    if (n > 0 && (size_t)n < cap && site) n += snprintf(out + n, cap - (size_t)n, "@%x", site);
    if (n > 0 && (size_t)n < cap && pool) snprintf(out + n, cap - (size_t)n, " pool %llx", (unsigned long long)pool);
}

/// The most distinct labels a map keeps; the rest are counted as "other" (the server's rows stay few)
constexpr int kMaxLabels = 8;

/// Whether the strict wait on a read is bounded: the switch on, a bound, and the page one the exposure meter's own zeroing and
/// hold cover (RX_VK_METER_ZERO + RX_VK_METER_HOLD: the sums cleared on the GPU before each program, a zero read given the last
/// result again), so a read that gives up waiting sees the sums of the frame before, not a double or a zero. bound_all: the other
/// ranges of the strict list too (RX_VK_STRICT_BOUND=2: size 0xc0 as well)
inline bool strict_bounded(int mode, double bound_ms, bool zero_on, bool hold_on, uint64_t size, bool known_meter) {
    if (mode <= 0 || bound_ms <= 0 || !zero_on || !hold_on) return false;
    if (size == 0x1040) return known_meter;
    return mode >= 2 && size == 0xc0;
}

/// RX_VK_STRICT_BOUND's value: unset 1, 0 off, 2 the 0xc0 ranges too
inline int bound_mode(const char* e) { return e && *e ? atoi(e) : 1; }
/// RX_VK_STRICT_BOUND_MS's value: unset 4 ms; at most 1000
inline double bound_limit_ms(const char* e) {
    if (!e || !*e) return 4.0;
    const double v = atof(e);
    return v < 0 ? 0 : v > 1000 ? 1000 : v;
}

}   // namespace readback_wait
