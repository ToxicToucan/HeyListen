// Copyright (C) 2026 Sacrappy

// jobs_demand / jobs_pending_live (gpu/vk/vk_jobs.cpp, RX_VK_JOB_DEMAND=1, RX_VK_WORKERS=1): the jobs a draw asks for go up the
// queue in the draws' order, newest present first, and the live count leaves out what no draw has wanted lately.
// Built against stand-ins for the renderer's headers (tests/test_jobs_wait_runs_queued.py).
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
std::mutex order_mu;
std::vector<int> order;
} // namespace

int main() {
    std::atomic<bool> hold_done{false};
    jobs_frame_note(100);
    jobs_push([&] {
        std::unique_lock lk(gate_mu);
        gate_held = true;
        gate_cv.notify_all();
        gate_cv.wait(lk, [] { return gate_open; });
        hold_done.store(true, std::memory_order_release);
        jobs_notify();
    }, false, &hold_done);
    {   // (the only worker is in the holding job: what is pushed next stays queued)
        std::unique_lock lk(gate_mu);
        gate_cv.wait_for(lk, std::chrono::seconds(5), [] { return gate_held; });
        expect(gate_held, "the worker took the holding job");
    }
    constexpr int N = 8;
    std::vector<std::atomic<bool>> done(N);
    auto push = [&](int i) {
        jobs_push([&, i] {
            { std::lock_guard lk(order_mu); order.push_back(i); }
            done[i].store(true, std::memory_order_release);
            jobs_notify();
        }, false, &done[i]);
    };
    for (int i = 0; i < N; i++) push(i);
    expect(jobs_pending_live(3) == N + 1, "everything just pushed is live (the running holder too)");
    jobs_frame_note(110);   // (ten presents on: nothing asked for since)
    expect(jobs_pending_live(3) == 1, "the queue nothing asked for lately is not live, the running job is");
    jobs_demand(done[5]);   // (the draw order of this present: 5, then 2, then 7)
    jobs_demand(done[2]);
    jobs_demand(done[7]);
    jobs_demand(done[5]);   // (asked again in the same present: stays where it is)
    expect(jobs_pending_live(3) == 4, "the three asked for are live, with the running job");
    jobs_frame_note(111);
    jobs_demand(done[6]);   // (the next present's draw goes ahead of the older ones)
    expect(jobs_pending_live(3) == 5, "a job asked for in the next present is live too");
    {
        std::lock_guard lk(gate_mu);
        gate_open = true;
    }
    gate_cv.notify_all();
    for (int i = 0; i < N; i++) jobs_wait(done[i]);
    jobs_wait(hold_done);
    const std::vector<int> want{6, 5, 2, 7, 0, 1, 3, 4};
    std::lock_guard lk(order_mu);
    bool same = order.size() == want.size();
    for (size_t i = 0; same && i < want.size(); i++) same = order[i] == want[i];
    if (!same) {
        std::printf("order:");
        for (int v : order) std::printf(" %d", v);
        std::printf("\n");
    }
    expect(same, "the jobs ran newest present's draws first, each present in its draws' order, then the rest in push order");
    if (failures) return EXIT_FAILURE;
    std::printf("PASS: the jobs a draw asks for run ahead of the rest, in the draws' order\n");
    std::fflush(stdout);
    std::_Exit(EXIT_SUCCESS);
}
