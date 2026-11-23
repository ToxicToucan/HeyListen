/* The replay's recordings' completion, tracked off the order thread (vk_replay.cpp, stream ORDER2, RX_VK_COMPLETION_THREAD).
   The order thread submits a recording (or a group of them in one submit, one fence for the group) and hands it over with
   submit(); a thread of its own waits for the fences in the order they were submitted (one queue: they finish in that order)
   and resets them (retire); the order thread asks wait(slot) only when it needs the slot again, and finds it done without a
   driver call. No Vulkan in here: `retire(token)` is the caller's wait-and-reset of the fence `token` stands for, so that
   tests/test_completion_tracker.cpp can run it with fakes. */
#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <thread>

namespace mkwvk::completion {

/// A slot's two counters: the order thread numbers its submits (`sub`), the completion thread says which was last done
struct Slot {
    uint64_t sub = 0;                  // (the last submit's number; the submitting thread's, under whatever order it submits in)
    std::atomic<uint64_t> done{0};     // (the last submit number retired)
};

class Tracker {
public:
    static constexpr int kMax = 8;   // (a group's members at most: vk_replay.cpp FenceGroup::kMax)

    /// retire(token): wait for the fence `token` stands for and reset it. It runs on the completion thread, never on the submitter's
    explicit Tracker(std::function<void(uint64_t token)> retire) : retire_(std::move(retire)) {}
    Tracker(const Tracker&) = delete;
    Tracker& operator=(const Tracker&) = delete;
    ~Tracker() { stop(); }

    /// The submit just made (its fence is `token`, signalled when every member is done): the members are numbered and handed over.
    /// Call in submission order (under the queue's lock): the fences are waited for in this order
    void submit(uint64_t token, Slot* const* members, int n) {
        Entry e;
        e.token = token;
        e.n = n < kMax ? n : kMax;
        for (int i = 0; i < e.n; i++) {
            e.m[i] = members[i];
            e.gen[i] = ++members[i]->sub;
        }
        {
            std::lock_guard lk(mu_);
            if (!started_) {
                started_ = true;
                th_ = std::thread([this] { run(); });
            }
            q_.push_back(e);
            submitted_++;
        }
        cv_work_.notify_one();
    }

    /// Whether the slot's last submit is retired (no waiting)
    static bool done(const Slot& s) { return s.done.load(std::memory_order_acquire) >= s.sub; }

    /// Waits until the slot's last submit is retired. Returns true when it had to wait (the GPU was still on it)
    bool wait(Slot& s) {
        if (done(s)) return false;
        const uint64_t want = s.sub;
        std::unique_lock lk(mu_);
        waiters_++;
        cv_done_.wait(lk, [&] { return s.done.load(std::memory_order_acquire) >= want || stopped_; });
        waiters_--;
        return true;
    }

    uint64_t retired() const { return retired_.load(std::memory_order_relaxed); }
    uint64_t submitted() const { std::lock_guard lk(mu_); return submitted_; }

    /// Retires what is queued and joins the thread (tests; the replay never stops it)
    void stop() {
        {
            std::lock_guard lk(mu_);
            if (!started_ || stopped_) return;
            stopped_ = true;
        }
        cv_work_.notify_all();
        cv_done_.notify_all();
        if (th_.joinable()) th_.join();
    }

private:
    struct Entry {
        uint64_t token = 0;
        int n = 0;
        Slot* m[kMax] = {};
        uint64_t gen[kMax] = {};
    };

    void run() {
        for (;;) {
            Entry e;
            {
                std::unique_lock lk(mu_);
                cv_work_.wait(lk, [&] { return !q_.empty() || stopped_; });
                if (q_.empty()) return;   // (stopped, nothing left)
                e = q_.front();
                q_.pop_front();
            }
            retire_(e.token);   // (the fence is reset before any member is done: the owner's slot may be submitted again at once)
            std::lock_guard lk(mu_);   // (wait() tests done under this lock: no wake-up is lost)
            for (int i = 0; i < e.n; i++) e.m[i]->done.store(e.gen[i], std::memory_order_release);
            retired_.fetch_add(1, std::memory_order_relaxed);
            if (waiters_) cv_done_.notify_all();
        }
    }

    std::function<void(uint64_t)> retire_;
    mutable std::mutex mu_;
    std::condition_variable cv_work_, cv_done_;
    std::deque<Entry> q_;
    std::thread th_;
    bool started_ = false, stopped_ = false;
    int waiters_ = 0;
    uint64_t submitted_ = 0;
    std::atomic<uint64_t> retired_{0};
};

}   // namespace mkwvk::completion
