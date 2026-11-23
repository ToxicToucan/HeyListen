// Copyright (C) 2026 Sacrappy

// The worker threads (vk_jobs.h). As many as a third of the hardware threads (2 to 4): the game itself runs six
// virtual cores, and the replay thread is one of the busiest.
#include <algorithm>
#include <atomic>
#include <chrono>
#include <exception>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>
#include <vector>

#include "vk_jobs.h"
#include "vk_internal.h"
#include "vk_draw.h"   // (ub_thread_off)

namespace mkwvk {
namespace {

std::mutex mu;
std::condition_variable work_cv, done_cv;
struct Entry {
    std::function<void()> fn;
    const std::atomic<bool>* flag;   // (what the job sets when done, for jobs_wait to find it while queued)
    uint64_t demand = 0;             // (the present a draw last asked for it in, or it was pushed in: jobs_pending_live)
    bool urgent = false;             // (pushed to the front: a compute pipeline a dispatch cannot do without)
};
std::deque<Entry> queue;
std::atomic<int> pending{0};
// (hookshot) the storm hold's quiet clock, in presents: the last present the draw path noted (jobs_frame_note) and the
// last present a job was pushed in. The churn of a cold area's arrival is the pushes themselves — its fade rewriting
// the handles every game frame, each frame's picks pushing new translations — and the game's frames are the clock its
// bursts keep: a queue nothing has grown into for a while of them is a churn that has stopped, whatever depth it shows
// (vk_draw.cpp's hold releases on it)
std::atomic<uint64_t> frame_noted{0};
std::atomic<uint64_t> last_push_frame{0};
bool started = false;
// (hookshot) RX_VK_JOB_DEMAND (on; 0: first in, first out): where the jobs a draw asked for this present go in the queue (past the urgent ones), so that the
// frame's draws are served in their order and ahead of what no draw has wanted since
uint64_t demand_frame = 0;
size_t demand_pos = 0;
bool demand_on() {
    static const bool on = !getenv("RX_VK_JOB_DEMAND") || atoi(getenv("RX_VK_JOB_DEMAND")) != 0;
    return on;
}

// (a job that throws: the game ended at once ("approach Malon, crash": an exception escaped a worker, KERNELBASE's RaiseException
// with no handler, 2026-11-08). It is logged and let go; what it was making stays not ready and is asked for again)
void run_guarded(std::function<void()>& fn) {
    try {
        fn();
    } catch (const std::exception& e) {
        static std::atomic<int> logged;
        if (logged++ < 20) logf("vk: a background job failed: %s", e.what());
    } catch (...) {
        static std::atomic<int> logged;
        if (logged++ < 20) logf("vk: a background job failed (not a std::exception)");
    }
}

// (hookshot) RX_VK_STORM_WORKERS=<n>: while the storm hold is on, n workers translate (the base ones and extra ones that take
// jobs only while it is). The game's own threads wait on the arrival's translations then, and the cold area's queue is worth
// every core; outside a storm the base count is what shares the CPU with the game. Unset: every hardware thread but one (12
// at most) on 8 threads or more; 0: no extra workers
std::atomic<bool> boost{false};
unsigned base_workers = 0;
bool extra_started = false;
unsigned storm_workers_wanted() {
    static const unsigned n = [] {
        if (const char* e = getenv("RX_VK_STORM_WORKERS")) return (unsigned)std::clamp(atoi(e), 0, 32);
        // (unset: every hardware thread but one, 12 at most, on a machine of 8 threads or more; fewer: the base workers only)
        const unsigned hw = std::thread::hardware_concurrency();
        return hw >= 8 ? std::min(hw - 1, 12u) : 0u;
    }();
    return n;
}

void worker(bool extra) {
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
    SetThreadDescription(GetCurrentThread(), extra ? L"mkw_vk storm worker" : L"mkw_vk worker");
    ub_thread_off();
    for (;;) {
        std::function<void()> fn;
        {
            std::unique_lock lk(mu);
            work_cv.wait(lk, [extra] { return !queue.empty() && (!extra || boost.load(std::memory_order_relaxed)); });
            fn = std::move(queue.front().fn);
            queue.pop_front();
            if (demand_pos) demand_pos--;   // (the promoted block's front went)
        }
        run_guarded(fn);
        pending--;
        jobs_notify();
    }
}

void start() {
    // (hookshot: with 16 hardware threads or more, 6 workers; RX_VK_WORKERS=<n>: n, 1 to 16)
    const unsigned hw = std::thread::hardware_concurrency();
    unsigned n = hw >= 16 ? 6u : std::clamp(hw / 3, 2u, 4u);
    if (const char* e = getenv("RX_VK_WORKERS")) n = (unsigned)std::clamp(atoi(e), 1, 16);
    for (unsigned i = 0; i < n; i++) std::thread(worker, false).detach();
    base_workers = n;
    started = true;
    logf("vk: %u background workers (shader translation, pipelines)", n);
}

} // namespace

void jobs_push(std::function<void()> fn, bool urgent, const std::atomic<bool>* flag) {
    std::lock_guard lk(mu);
    if (!started) start();
    pending++;
    last_push_frame.store(frame_noted.load(std::memory_order_relaxed), std::memory_order_relaxed);
    const uint64_t now = frame_noted.load(std::memory_order_relaxed);
    if (urgent) queue.push_front({std::move(fn), flag, now, true});
    else queue.push_back({std::move(fn), flag, now, false});
    work_cv.notify_one();
}
// (issue 496) Intel A750: four of the game's big compute pipelines built at once by Intel's compiler (up to 58 s each) froze
// the whole PC. One thread builds them one at a time there, below normal priority, beside the shared workers.
// (longshot #404) Between two of its builds no compute build is under way: the moment Intel's pipeline cache fetch waits for
// (pipeline_cache_between_builds). first: the front lane, in order among its own
void jobs_push_serial(std::function<void()> fn, bool first) {
    static std::mutex smu;
    static std::condition_variable scv;
    static std::deque<std::function<void()>> sq, sq_first;
    static bool sstarted = false;
    std::lock_guard lk(smu);
    if (!sstarted) {
        sstarted = true;
        std::thread([] {
            SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
            SetThreadDescription(GetCurrentThread(), L"mkw_vk serial builds");
            ub_thread_off();
            for (;;) {
                std::function<void()> fn;
                {
                    std::unique_lock lk(smu);
                    scv.wait(lk, [] { return !sq.empty() || !sq_first.empty(); });
                    auto& q = sq_first.empty() ? sq : sq_first;
                    fn = std::move(q.front());
                    q.pop_front();
                }
                run_guarded(fn);
                jobs_notify();
                std::function<void()> between = pipeline_cache_between_builds;
                run_guarded(between);
            }
        }).detach();
        logf("vk: compute pipelines are built one at a time (the game's dispatches before the mesh fallback's)");
    }
    (first ? sq_first : sq).push_back(std::move(fn));
    scv.notify_one();
}
void jobs_notify() {
    std::lock_guard lk(mu);
    done_cv.notify_all();
}
bool jobs_wait(const std::atomic<bool>& flag) {
    std::unique_lock lk(mu);
    const bool fresh = !flag.load(std::memory_order_acquire);   // (done already: nothing ran during this wait)
    // (still queued: run here, now. A draw that must wait for its pipeline waited for every job queued before it, and a new
    // area queues a hundred pipelines: with ten workers at 0.3-2 s a build, frames stopped for 6-12 s, the draw's own build
    // still in the queue. On by default again (v0.4.4) behind the storm hold: the frames drawn during a new area's translation
    // storm sampled state not yet settled (cyan/white areas, green bands on slower GPUs, why v0.4.3 had it off), and the hold now
    // withholds those presents until the queue drains. RX_VK_JOB_STEAL=0: the arrival waits as in v0.4.2)
    static const bool steal = !getenv("RX_VK_JOB_STEAL") || atoi(getenv("RX_VK_JOB_STEAL")) != 0;
    if (steal && !flag.load(std::memory_order_acquire))
        for (auto it = queue.begin(); it != queue.end(); ++it)
            if (it->flag == &flag) {
                std::function<void()> fn = std::move(it->fn);
                queue.erase(it);
                if (demand_pos) demand_pos--;
                lk.unlock();
                run_guarded(fn);
                pending--;
                jobs_notify();
                lk.lock();
                break;
            }
    done_cv.wait(lk, [&] { return flag.load(std::memory_order_acquire); });
    return fresh;   // (the caller that watched its own translation end in its wait can leave its draw out of that frame)
}
int jobs_pending() {
    return pending.load();
}
void jobs_set_boost(bool on) {
    const unsigned want = storm_workers_wanted();
    if (!want) return;
    std::lock_guard lk(mu);
    if (!started) start();
    if (on && !extra_started) {
        extra_started = true;
        for (unsigned i = base_workers; i < want; i++) std::thread(worker, true).detach();
        logf("vk: %u storm workers beside the %u background ones (RX_VK_STORM_WORKERS)", want > base_workers ? want - base_workers : 0u,
             base_workers);
    }
    boost.store(on, std::memory_order_relaxed);
    if (on) work_cv.notify_all();
}
void jobs_demand(const std::atomic<bool>& flag) {
    if (!demand_on()) return;
    std::lock_guard lk(mu);
    const uint64_t now = frame_noted.load(std::memory_order_relaxed);
    if (now != demand_frame) demand_frame = now, demand_pos = 0;   // (the newest present's draws first)
    size_t urgent = 0;
    while (urgent < queue.size() && queue[urgent].urgent) urgent++;
    for (size_t i = urgent; i < queue.size(); i++) {
        if (queue[i].flag != &flag) continue;
        if (queue[i].demand == now && queue[i].demand != 0 && i < urgent + demand_pos) return;   // (already moved up this present)
        Entry e = std::move(queue[i]);
        queue.erase(queue.begin() + (std::ptrdiff_t)i);
        e.demand = now;
        queue.insert(queue.begin() + (std::ptrdiff_t)(urgent + std::min(demand_pos, queue.size() - urgent)), std::move(e));
        demand_pos++;
        return;
    }
}
int jobs_pending_live(uint64_t window) {
    std::lock_guard lk(mu);
    const uint64_t now = frame_noted.load(std::memory_order_relaxed);
    const uint64_t from = now > window ? now - window : 0;
    int live = std::max(0, pending.load() - (int)queue.size());   // (running: a worker has it)
    for (const Entry& e : queue)
        if (e.demand >= from) live++;
    return live;
}
void jobs_frame_note(uint64_t frame) {
    frame_noted.store(frame, std::memory_order_relaxed);
}
uint64_t jobs_last_push_age_frames() {
    const uint64_t last = last_push_frame.load(std::memory_order_relaxed);
    return last ? frame_noted.load(std::memory_order_relaxed) - last : (UINT64_MAX >> 1);   // (never pushed: quiet since forever)
}

} // namespace mkwvk
