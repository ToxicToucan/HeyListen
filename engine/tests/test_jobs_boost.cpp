// Copyright (C) 2026 Sacrappy

// jobs_set_boost (gpu/vk/vk_jobs.cpp, RX_VK_STORM_WORKERS=4, RX_VK_WORKERS=1): the extra workers take jobs only while the storm
// hold is on. Built against stand-ins for the renderer's headers (tests/test_jobs_wait_runs_queued.py).
#include "vk_jobs.h"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <thread>
#include <vector>

using namespace mkwvk;

namespace {
int failures = 0;
void expect(bool ok, const char* what) {
    if (!ok) std::printf("FAIL: %s\n", what), failures++;
}
std::mutex gate_mu;
std::condition_variable gate_cv;
bool gate_open = false, gate_held = false;
} // namespace

int main() {
    std::atomic<bool> hold_done{false};
    jobs_push([&] {
        std::unique_lock lk(gate_mu);
        gate_held = true;
        gate_cv.notify_all();
        gate_cv.wait(lk, [] { return gate_open; });
        hold_done.store(true, std::memory_order_release);
        jobs_notify();
    }, false, &hold_done);
    {
        std::unique_lock lk(gate_mu);
        gate_cv.wait_for(lk, std::chrono::seconds(5), [] { return gate_held; });
        expect(gate_held, "the only base worker took the holding job");
    }
    constexpr int N = 3;
    std::vector<std::atomic<bool>> done(N);
    for (int i = 0; i < N; i++)
        jobs_push([&, i] { done[i].store(true, std::memory_order_release); jobs_notify(); }, false, &done[i]);
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    expect(!done[0] && !done[1] && !done[2], "with the hold off nothing runs while the base worker is held");
    jobs_set_boost(true);
    const auto t0 = std::chrono::steady_clock::now();
    while ((!done[0] || !done[1] || !done[2]) && std::chrono::steady_clock::now() - t0 < std::chrono::seconds(5))
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    expect(done[0] && done[1] && done[2], "the extra workers took the queue while the hold was on");
    jobs_set_boost(false);
    std::atomic<bool> later{false};
    jobs_push([&] { later.store(true, std::memory_order_release); jobs_notify(); }, false, &later);
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    expect(!later, "released: the extra workers leave the queue to the base ones");
    {
        std::lock_guard lk(gate_mu);
        gate_open = true;
    }
    gate_cv.notify_all();
    jobs_wait(later);
    jobs_wait(hold_done);
    if (failures) return EXIT_FAILURE;
    std::printf("PASS: the extra storm workers translate only while the hold is on\n");
    std::fflush(stdout);
    std::_Exit(EXIT_SUCCESS);
}
