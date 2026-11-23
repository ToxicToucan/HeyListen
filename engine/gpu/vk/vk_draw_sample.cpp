// (stream DRAW1, RX_VK_DRAW_SAMPLE=<hz>, with RX_VK_PROF_FINE) Where inside draw() the replay's recording threads are: a sampler thread
// stops each thread that is inside draw() at <hz> (default 2000), reads its program counter and lets it run; the counts by address are
// logged with the fine profile ("vk: draw sample ...", offsets from the library's base; llvm-nm sorts them into functions offline).
// Diagnosis only: off, nothing runs. The sampler stops a thread for ~10 us at a time, so the runs with it on are not timed.
#include "vk_draw_sample.h"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#include "vk_internal.h"

#ifdef _WIN32
#include <windows.h>
#include <psapi.h>
#endif

namespace mkwvk::draw_sample {
namespace {

constexpr int kMaxThreads = 32;
constexpr size_t kMaxSamples = 1u << 22;
struct Slot {
#ifdef _WIN32
    HANDLE h = nullptr;
#endif
    std::atomic<int> in_draw{0};
    std::atomic<int> used{0};
};
Slot slots[kMaxThreads];
std::atomic<int> nslots{0};
std::vector<uintptr_t> samples;   // (preallocated: the sampler never allocates)
std::vector<uintptr_t> callers;   // (the same places: for a sample outside the library, the first return address into it on the stack, 0 none)
uintptr_t module_end = 0;
std::atomic<size_t> nsamples{0};
std::atomic<bool> started{false};
uintptr_t module_base = 0;
std::mutex mu;

int hz_value() {
    static const int hz = [] {
        const char* e = getenv("RX_VK_DRAW_SAMPLE");
        if (!e || !*e) return 0;
        const int v = atoi(e);
        return v <= 1 ? 2000 : std::min(v, 20000);
    }();
    return hz;
}

#ifdef _WIN32
void sampler_main() {
    HANDLE timer = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL);
    const LONGLONG period_100ns = std::max<LONGLONG>(1, 10000000LL / hz_value());
    for (;;) {
        if (timer) {
            LARGE_INTEGER due;
            due.QuadPart = -period_100ns;
            SetWaitableTimer(timer, &due, 0, nullptr, nullptr, FALSE);
            WaitForSingleObject(timer, INFINITE);
        } else {
            Sleep(1);
        }
        const int n = nslots.load(std::memory_order_acquire);
        for (int i = 0; i < n; i++) {
            Slot& s = slots[i];
            if (!s.in_draw.load(std::memory_order_acquire)) continue;
            CONTEXT ctx;
            memset(&ctx, 0, sizeof ctx);
            ctx.ContextFlags = CONTEXT_CONTROL;
            if (SuspendThread(s.h) == (DWORD)-1) continue;
            const BOOL got = GetThreadContext(s.h, &ctx);
            const bool still = s.in_draw.load(std::memory_order_acquire) != 0;
            uintptr_t caller = 0;
            if (got && still && ((uintptr_t)ctx.Rip < module_base || (uintptr_t)ctx.Rip >= module_end)) {
                // (outside the library, in a driver call or a wait: the nearest return address into it on the thread's stack, found
                // by a plain scan of the first 2 KB: a stale word may be taken, the counts are by place and are read as such)
                const uintptr_t* sp = (const uintptr_t*)ctx.Rsp;
                for (int i = 0; i < 256; i++) {
                    const uintptr_t w = sp[i];
                    if (w >= module_base && w < module_end) {
                        caller = w;
                        break;
                    }
                }
            }
            ResumeThread(s.h);
            if (!got || !still) continue;
            const size_t at = nsamples.fetch_add(1, std::memory_order_relaxed);
            if (at < samples.size()) samples[at] = (uintptr_t)ctx.Rip, callers[at] = caller;
        }
    }
}
#endif

}   // namespace

bool on() {
    return hz_value() > 0;
}

int register_thread() {
    if (!on()) return -1;
#ifdef _WIN32
    std::lock_guard lk(mu);
    const int i = nslots.load();
    if (i >= kMaxThreads) return -1;
    if (!DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &slots[i].h, 0, FALSE, DUPLICATE_SAME_ACCESS))
        return -1;
    if (!started.exchange(true)) {
        samples.assign(kMaxSamples, 0);
        callers.assign(kMaxSamples, 0);
        HMODULE hm = nullptr;
        if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (LPCWSTR)&register_thread, &hm)) {
            module_base = (uintptr_t)hm;
            MODULEINFO mi{};
            module_end = module_base + (GetModuleInformation(GetCurrentProcess(), hm, &mi, sizeof mi) ? mi.SizeOfImage : 0x1000000);
        }
        HANDLE t = CreateThread(nullptr, 0, [](LPVOID) -> DWORD { sampler_main(); return 0; }, nullptr, 0, nullptr);
        if (t) CloseHandle(t);
        logf("vk: draw sample: %d Hz, library base %#llx", hz_value(), (unsigned long long)module_base);
    }
    nslots.store(i + 1, std::memory_order_release);
    return i;
#else
    return -1;
#endif
}

void enter(int slot) {
    if (slot >= 0) slots[slot].in_draw.store(1, std::memory_order_release);
}
void leave(int slot) {
    if (slot >= 0) slots[slot].in_draw.store(0, std::memory_order_release);
}

void report() {
    if (!on() || !started.load()) return;
    const size_t n = std::min(nsamples.load(), samples.size());
    std::unordered_map<uintptr_t, int64_t> hist;
    for (size_t i = 0; i < n; i++) hist[samples[i] & ~(uintptr_t)3]++;   // (4-byte buckets: an instruction's own address, near enough)
    std::vector<std::pair<uintptr_t, int64_t>> v(hist.begin(), hist.end());
    std::sort(v.begin(), v.end(), [](const auto& a, const auto& b) { return a.second > b.second; });
    logf("vk: draw sample: %zu samples, %zu addresses, base %#llx", n, v.size(), (unsigned long long)module_base);
#ifdef _WIN32
    {   // (the samples outside the library by module, and by the library's code that called into it: "module>caller offset:count")
        std::unordered_map<std::string, int64_t> by_mod;
        std::unordered_map<std::uintptr_t, std::unordered_map<std::string, int64_t>> by_caller;
        std::unordered_map<std::uintptr_t, std::string> mod_of;
        for (size_t i = 0; i < n; i++) {
            const uintptr_t pc = samples[i];
            if (pc >= module_base && pc < module_end) continue;
            const uintptr_t key = pc >> 16;
            auto it = mod_of.find(key);
            if (it == mod_of.end()) {
                std::string name = "?";
                HMODULE hm = nullptr;
                wchar_t path[MAX_PATH]{};
                if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (LPCWSTR)pc, &hm) &&
                    GetModuleFileNameW(hm, path, MAX_PATH)) {
                    std::wstring w(path);
                    const size_t slash = w.find_last_of(L"\/");
                    if (slash != std::wstring::npos) w = w.substr(slash + 1);
                    name.assign(w.begin(), w.end());
                }
                it = mod_of.emplace(key, name).first;
            }
            by_mod[it->second]++;
            by_caller[callers[i] & ~(uintptr_t)3][it->second]++;
        }
        char line2[1200];
        int k2 = snprintf(line2, sizeof line2, "vk: draw sample modules:");
        for (const auto& [name, c] : by_mod) k2 += snprintf(line2 + k2, sizeof line2 - k2, " %s=%lld", name.c_str(), (long long)c);
        logf("%s", line2);
        k2 = 0;
        for (const auto& [caller, m] : by_caller)
            for (const auto& [name, c] : m) {
                if (c < 3) continue;
                k2 += snprintf(line2 + k2, sizeof line2 - k2, " %s>%llx:%lld", name.c_str(), (unsigned long long)(caller ? caller - module_base : 0), (long long)c);
                if (k2 > 1000) logf("vk: draw sample outside:%s", line2), k2 = 0;
            }
        if (k2) logf("vk: draw sample outside:%s", line2);
    }
#endif
    char line[1200];
    int k = 0;
    for (size_t i = 0; i < v.size() && i < 4000; i++) {
        k += snprintf(line + k, sizeof line - k, " %llx:%lld", (unsigned long long)(v[i].first - module_base), (long long)v[i].second);
        if (k > 1000 || i + 1 == v.size() || i + 1 == 4000) {
            logf("vk: draw sample at:%s", line);
            k = 0;
        }
    }
}

}   // namespace mkwvk::draw_sample
