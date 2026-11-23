// Copyright (C) 2026 Sacrappy

// jobs_wait (gpu/vk/vk_jobs.cpp): a waited-for job still in the queue runs on the waiting thread at once, instead of after
// every job queued before it, and the queue tells how long it has gone unpushed (the storm hold's quiet clock).
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

// (every worker held in a job until released, so that a job pushed after them stays queued)
std::mutex gate_mu;
std::condition_variable gate_cv;
bool gate_open = false;
std::atomic<int> held{0};
void hold() {
    held++;
    std::unique_lock lk(gate_mu);
    gate_cv.wait(lk, [] { return gate_open; });
}
void release() {
    {
        std::lock_guard lk(gate_mu);
        gate_open = true;
    }
    gate_cv.notify_all();
}

} // namespace

int main() {
    const unsigned workers = 32;   // (more than start() makes: every worker is held, and some holds stay queued)
    std::vector<std::atomic<bool>> holds_done(workers);
    for (unsigned i = 0; i < workers; i++)
        jobs_push([&holds_done, i] { hold(); holds_done[i].store(true); jobs_notify(); }, false, &holds_done[i]);
    const auto t0 = std::chrono::steady_clock::now();
    while (held.load() < 2 && std::chrono::steady_clock::now() - t0 < std::chrono::seconds(5)) std::this_thread::yield();
    expect(held.load() >= 2, "the workers took the holding jobs");

    std::atomic<bool> ready{false};
    std::thread::id ran_on{};
    jobs_push([&] { ran_on = std::this_thread::get_id(); ready.store(true, std::memory_order_release); }, false, &ready);
    const bool ran_during = jobs_wait(ready);   // (every worker is held: before, this waited until release(), which nobody calls)
    expect(ready.load(), "the waited-for job ran");
    expect(ran_on == std::this_thread::get_id(), "it ran on the waiting thread");
    expect(ran_during, "the wait reports the job as run during it");
    expect(!jobs_wait(ready), "a wait on a job already done reports that nothing ran during it");

    std::atomic<bool> unflagged{false};
    jobs_push([&] { unflagged.store(true, std::memory_order_release); jobs_notify(); });   // (no flag: left in the queue)
    release();
    jobs_wait(unflagged);   // (taken by a worker once released)
    expect(unflagged.load(), "a job pushed without its flag still runs on a worker");
    for (unsigned i = 0; i < workers; i++) jobs_wait(holds_done[i]);

    // (hookshot) the storm hold's quiet clock, in presents: it releases on a queue nothing has been pushed into for a
    // while of them (vk_draw.cpp), so the age must grow while nothing is pushed and reset on a push
    jobs_frame_note(1000);
    std::atomic<bool> noted{false};
    jobs_push([&] { noted.store(true, std::memory_order_release); jobs_notify(); }, false, &noted);
    jobs_wait(noted);
    jobs_frame_note(1000 + 120);
    expect(jobs_last_push_age_frames() >= 100, "an unpushed queue reports its quiet age growing");
    std::atomic<bool> fresh{false};
    jobs_push([&] { fresh.store(true, std::memory_order_release); jobs_notify(); }, false, &fresh);
    expect(jobs_last_push_age_frames() < 100, "a push resets the quiet clock");
    jobs_wait(fresh);

    if (failures) return EXIT_FAILURE;
    std::printf("PASS: a waited-for job still queued runs on the waiting thread at once\n");
    std::fflush(stdout);
    std::_Exit(EXIT_SUCCESS);   // (the workers are detached and wait forever)
}
