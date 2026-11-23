// Copyright (C) 2026 Sacrappy

// (HeyListen) What the prediction of pipelines (predict_run, vk_draw.cpp) holds itself to. The pipelines it makes are built for the
// driver's own cache and let go, so none of them is needed before the game: they are speculative, and a start that is cold (no program
// cache, every shader prepared) runs them all in one go. On the PC 3050 (RTX 3050, 32 GB) two programs of the game's, 502k and 1014k
// words of SPIR-V, each kept the driver's compiler busy for over three minutes and growing past 11 GB of the process's commit (29 GB
// in the user's run, which then refused C++ allocations at the commit limit, "0 MB of commit left", and had the game start only
// after 616 s); every other program, up to 133k words, took 25 s at most and a few hundred MB. So:
//  * a program over kDefaultMaxKWords thousand words is left to the draw that needs it (RX_VK_PREDICT_MAX_KWORDS, 0: no limit);
//  * the prediction stops, the rest left to play, when the system has less than kDefaultReserveMB of commit left
//    (RX_VK_PREDICT_RESERVE_MB, 0: no limit).
// (v0.4.3: the default is 100: on a GTX 1660 SUPER (Turing) one program of 144k words stayed in the driver for 2 hours, the same
// takes ~25 s on Ampere; the programs measured cheap and safe are 40k words or fewer, and a prediction is speculative anyway.)
// (v0.4.3.1: the prediction in play, behind a lazy update's game (prepare_background): the whole prediction, ~41000 pipelines with no time
// budget, on up to four workers, took the game from 95 to 30 fps with frames of 1.2 s on a 32 GB PC that held 28 GB of private
// memory already (the owner's run, 2026-11-20). In play it is made small and polite, RX_VK_PREDICT_PLAY_GENTLE=0 for what it was:
//  * at most kPlayWorkers workers (RX_VK_PREDICT_PLAY_WORKERS), at the idle priority;
//  * a worker waits for the next program while the system has less than kDefaultPlayFreeMB of memory available
//    (RX_VK_PREDICT_PLAY_FREE_MB, 0: it does not look): the game's own pages are not pushed out for a speculative build.)
// No Vulkan: tests/predict_limits_test.cpp.
#pragma once
#include <cstdint>

namespace mkwvk::predictlimits {

constexpr unsigned long long kDefaultMaxKWords = 100;   // (was 300: a 144k-word program sat 2 hours in a GTX 1660 SUPER's driver, v0.4.2; the cheap ones measured are <= 40k)
constexpr unsigned long long kDefaultReserveMB = 3072;
constexpr unsigned kPlayWorkers = 2;                        // (the prediction in play: v0.4.3's was a quarter of the CPUs, two to four)
constexpr unsigned long long kDefaultPlayFreeMB = 6144;     // (the physical memory the prediction in play leaves to the PC)

/// A program of `words` 32-bit words of SPIR-V (all its stages) is too big to be predicted when max_kwords is not 0 and it is over it
inline bool too_big(uint64_t words, unsigned long long max_kwords) { return max_kwords && words / 1000 > max_kwords; }

/// The system has less commit left than the prediction wants to leave (reserve_mb 0: it does not look)
inline bool low_commit(unsigned long long left_mb, unsigned long long reserve_mb) { return reserve_mb && left_mb < reserve_mb; }

/// How many workers the prediction in play has: `asked` (RX_VK_PREDICT_PLAY_WORKERS, a text, null when unset) or kPlayWorkers, at
/// least one and not more than the CPUs
inline unsigned play_workers(unsigned cpus, const char* asked) {
    unsigned n = kPlayWorkers;
    if (asked && *asked >= '0' && *asked <= '9') {
        n = 0;
        for (const char* c = asked; *c >= '0' && *c <= '9' && n < 1000; c++) n = n * 10 + (unsigned)(*c - '0');
    }
    if (n > cpus) n = cpus;
    return n ? n : 1;
}

/// The system has less memory available than the prediction in play wants to leave (free_mb 0: it does not look)
inline bool low_memory(unsigned long long avail_mb, unsigned long long free_mb) { return free_mb && avail_mb < free_mb; }

}  // namespace mkwvk::predictlimits
