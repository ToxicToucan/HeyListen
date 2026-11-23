// Copyright (C) 2026 Sacrappy

// tests/test_renderer_new_handler.py's fixture: the real new_refused of gpu/vk/vk_main.cpp (included from new_handler.inc),
// with a fake host, a fake commit and its own log. Each case in its own process.
#include <atomic>
#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <string>
#include <thread>

struct MkwVkHost {
    void (*log)(const char*);
    void (*out_of_memory)(const char*);
};
namespace mkwvk {
static void host_log(const char* s) { std::printf("  log: %s\n", s); }
static std::chrono::steady_clock::time_point started;
static bool stopped;
[[noreturn]] static void host_out_of_memory(const char* what) {
    const long ms = (long)std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - started).count();
    std::printf("  the host's out_of_memory(\"%s\") after %ld ms\n", what, ms);
    stopped = true;
    std::fflush(stdout);
    if (ms < 4900 || ms > 6500) { std::printf("FAIL: not after about 5 s\n"); std::exit(1); }
    std::exit(0);
}
static MkwVkHost fake = {host_log, host_out_of_memory};
static MkwVkHost nohost = {host_log, nullptr};
const MkwVkHost* host = &fake;
static long long fake_left = 100;
static long long commit_left_mb() { return fake_left; }
void logf(const char* fmt, ...) {
    char b[1024];
    std::va_list a;
    va_start(a, fmt);
    std::vsnprintf(b, sizeof b, fmt, a);
    va_end(a);
    if (host) host->log(b);
}
#include "new_handler.inc"
}   // namespace mkwvk

int main(int argc, char** argv) {
    using namespace mkwvk;
    const std::string c = argc > 1 ? argv[1] : "";
    started = std::chrono::steady_clock::now();
    if (c == "plenty") {   // (4 GB and more left: not the limit, the std::bad_alloc as before, at once)
        fake_left = 50000;
        try { new_refused(); } catch (const std::bad_alloc&) { std::printf("bad_alloc at once\n"); return 0; }
        std::printf("FAIL: no exception\n");
        return 1;
    }
    if (c == "unknown") {   // (the commit is not known: as plenty)
        fake_left = -1;
        try { new_refused(); } catch (const std::bad_alloc&) { std::printf("bad_alloc at once\n"); return 0; }
        std::printf("FAIL: no exception\n");
        return 1;
    }
    if (c == "limit") {   // (a real refusal: set_new_handler, and 1 PB asked of operator new, which no PC can give)
        std::set_new_handler(new_refused);
        std::printf("asking for 1 PB\n");
        std::fflush(stdout);
        void* volatile p = ::operator new((size_t)1 << 50);
        (void)p;
        std::printf("FAIL: allocated\n");
        return 1;
    }
    if (c == "recovers") {   // (refused twice, then the memory is there: no exception, the host not asked)
        int tries = 0;
        for (; tries < 2; tries++) new_refused();
        std::printf("returned %d times, the host not asked: %s\n", tries, stopped ? "FAIL" : "ok");
        return stopped ? 1 : 0;
    }
    if (c == "oldhost") {   // (a host with no out_of_memory: the exception after 5 s, as before)
        host = &nohost;
        try {
            for (;;) new_refused();
        } catch (const std::bad_alloc&) {
            const long ms = (long)std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - started).count();
            std::printf("bad_alloc after %ld ms\n", ms);
            return ms >= 4900 && ms < 6500 ? 0 : 1;
        }
    }
    if (c == "budget") {   // (a thread's 5 s are counted from its first refusal; one after a pause starts again)
        for (int i = 0; i < 60; i++) new_refused();   // (about 3 s)
        std::this_thread::sleep_for(std::chrono::milliseconds(400));
        const auto again = std::chrono::steady_clock::now();
        for (int i = 0; i < 60; i++) new_refused();   // (about 3 s more: under a new 5 s, but over the first's)
        const long ms = (long)std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - again).count();
        std::printf("3 s of tries after a pause: %ld ms, the host not asked: %s\n", ms, stopped ? "FAIL" : "ok");
        return stopped ? 1 : 0;
    }
    std::printf("unknown case\n");
    return 2;
}
