#include "../gpu/vk/completion_tracker.h"

#include <chrono>
#include <cstdio>
#include <mutex>
#include <thread>
#include <vector>

#define CHECK(name, condition) do { if (!(condition)) { std::fprintf(stderr, "FAIL %s at line %d\n", name, __LINE__); return 1; } } while (0)

using namespace mkwvk::completion;
using namespace std::chrono_literals;

int main() {
    // (1) a single submit is retired by the thread, in the order submitted; wait() returns once it is
    {
        std::mutex mu;
        std::vector<uint64_t> order;
        Tracker t([&](uint64_t token) {
            std::this_thread::sleep_for(1ms);
            std::lock_guard lk(mu);
            order.push_back(token);
        });
        Slot a, b, c;
        Slot* ma[] = {&a};
        Slot* mb[] = {&b};
        Slot* mc[] = {&c};
        CHECK("a fresh slot is done", Tracker::done(a));
        t.submit(1, ma, 1);
        t.submit(2, mb, 1);
        t.submit(3, mc, 1);
        t.wait(c);
        CHECK("all three retired when the last is", Tracker::done(a) && Tracker::done(b) && Tracker::done(c));
        std::lock_guard lk(mu);
        CHECK("retired in submission order", order.size() == 3 && order[0] == 1 && order[1] == 2 && order[2] == 3);
    }
    // (2) a group: one fence, every member done together, and none before the fence is retired
    {
        std::mutex gate_mu;
        std::condition_variable gate_cv;
        bool open = false;
        Tracker t([&](uint64_t) {
            std::unique_lock lk(gate_mu);
            gate_cv.wait(lk, [&] { return open; });
        });
        Slot m[3];
        Slot* ms[] = {&m[0], &m[1], &m[2]};
        t.submit(7, ms, 3);
        std::this_thread::sleep_for(20ms);
        CHECK("not done while the fence is not", !Tracker::done(m[0]) && !Tracker::done(m[1]) && !Tracker::done(m[2]));
        std::thread waiter([&] { t.wait(m[1]); });
        std::this_thread::sleep_for(20ms);
        {
            std::lock_guard lk(gate_mu);
            open = true;
        }
        gate_cv.notify_all();
        waiter.join();
        CHECK("the group is done together", Tracker::done(m[0]) && Tracker::done(m[1]) && Tracker::done(m[2]));
        CHECK("one retire for the group", t.retired() == 1);
    }
    // (3) a slot submitted again is not done by the earlier submit's retire (stale generations)
    {
        std::mutex gate_mu;
        std::condition_variable gate_cv;
        int allowed = 0;
        Tracker t([&](uint64_t) {
            std::unique_lock lk(gate_mu);
            gate_cv.wait(lk, [&] { return allowed > 0; });
            allowed--;
        });
        Slot s;
        Slot* ms[] = {&s};
        t.submit(1, ms, 1);
        {
            std::lock_guard lk(gate_mu);
            allowed = 1;
        }
        gate_cv.notify_all();
        t.wait(s);
        CHECK("first submit done", Tracker::done(s));
        t.submit(2, ms, 1);   // (the same slot again, as after the order thread's recording_wait)
        CHECK("not done again at once", !Tracker::done(s));
        std::this_thread::sleep_for(10ms);
        CHECK("still not done: its fence is not", !Tracker::done(s));
        {
            std::lock_guard lk(gate_mu);
            allowed = 1;
        }
        gate_cv.notify_all();
        t.wait(s);
        CHECK("second submit done", Tracker::done(s));
    }
    // (4) a ring of 48 slots reused 5000 times, some of them in groups, the wait before each reuse: never a retire out of order
    {
        std::atomic<uint64_t> last{0};
        std::atomic<bool> bad{false};
        Tracker t([&](uint64_t token) {
            if (token != last.load() + 1) bad = true;
            last = token;
            if (token % 17 == 0) std::this_thread::sleep_for(200us);
        });
        constexpr int N = 48;
        Slot ring[N];
        uint64_t token = 0;
        for (int i = 0; i < 5000; i++) {
            const int k = i % N;
            const int n = (i % 5 == 0 && k + 3 <= N) ? 3 : 1;
            for (int j = 0; j < n; j++) t.wait(ring[k + j]);
            Slot* ms[3] = {&ring[k], &ring[k + (n > 1 ? 1 : 0)], &ring[k + (n > 1 ? 2 : 0)]};
            t.submit(++token, ms, n);
            if (n > 1) i += 2;
        }
        for (Slot& s : ring) t.wait(s);
        CHECK("the retires came in order", !bad.load() && last.load() == token);
        CHECK("every submit was retired once", t.retired() == token);
    }
    // (5) several threads waiting on different slots are all woken
    {
        Tracker t([](uint64_t) { std::this_thread::sleep_for(5ms); });
        Slot s[4];
        Slot* ms[] = {&s[0], &s[1], &s[2], &s[3]};
        t.submit(1, ms, 4);
        std::vector<std::thread> w;
        std::atomic<int> woken{0};
        for (int i = 0; i < 4; i++) w.emplace_back([&, i] { t.wait(s[i]); woken++; });
        for (auto& th : w) th.join();
        CHECK("four waiters woken", woken == 4);
    }
    // (6) stop() retires what is queued and joins
    {
        std::atomic<int> n{0};
        {
            Tracker t([&](uint64_t) { std::this_thread::sleep_for(1ms); n++; });
            Slot s[3];
            for (int i = 0; i < 3; i++) {
                Slot* ms[] = {&s[i]};
                t.submit(i + 1, ms, 1);
            }
            t.stop();
            CHECK("stop retired all three", n == 3 && Tracker::done(s[2]));
        }
    }
    std::puts("ok");
    return 0;
}
