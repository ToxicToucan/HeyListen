"""Exercise host event handoff with real competing threads. Run python -I -B tests/test_event_handoff.py.

host/nn_os_sync.c's event_peek, event_clear, event_signal and event_wait (and the switch they read) are cut out of the
source and run against real threads: an auto-clear event wakes one waiter per signal, a manual one all of them, a signal of
an event that is already set wakes nobody, and when two sleepers are pinned to different cores (the host's one pick may be
the busy one) the idle core's sleeper takes the signal. HOS_EVENT_FAST=0 keeps the locked path: the same events behave the
same, its signals just always take the lock.

Ported from Longshot 7daafa7a (#1553, HumanBean), the test of c2f43513 and fdee2857; the harness's stubs are HeyListen's
(HOS_JT in place of HOS_CHAIN) and the legacy-path run is added here.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srccut import block

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cxx", default="c++")
    args = parser.parse_args()
    source = (ROOT / "host/nn_os_sync.c").read_text()
    functions = "\n".join(block(source, name) for name in
                          ("static int event_fast(", "static int event_peek(", "static void event_clear(",
                           "static void event_signal(", "static int event_wait("))
    harness = r"""
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>
#include <cstdint>
#include <climits>
struct Obj {
    uint64_t key{}; int signaled{}, autoclear{}, mwait{}; int64_t sig_ns{};
    std::mutex lk; std::condition_variable_any cv;
};
std::atomic<int> wakes{}, parked{}, passed{};
constexpr unsigned INFINITE = UINT_MAX;
#define HOS_JT(...) ((void)0)
uint64_t hos_jt_lr() { return 0; }
enum { HOS_SLACK_QUEUE, HOS_SLACK_EVENT };
void hos_slack_wait(int, uint64_t, int64_t) {}   // (PIPE2 stage 0, hos_slack.c)
void AcquireSRWLockExclusive(std::mutex* m) { m->lock(); }
void ReleaseSRWLockExclusive(std::mutex* m) { m->unlock(); }
// Model a legal host wake-one choice: core 0's worker wakes first, but cannot
// resume guest execution until its busy core is available. Core 1 is idle.
thread_local int core = -1;
bool model_cores = false, core0_available = false;
unsigned runnable = 0;
void WakeConditionVariable(std::condition_variable_any* cv) {
    ++wakes;
    if (model_cores) { runnable |= 1; cv->notify_all(); }
    else cv->notify_one();
}
void WakeAllConditionVariable(std::condition_variable_any* cv) { ++wakes;
    if (model_cores) runnable |= 3;
    cv->notify_all();
}
void poke_multiwait(Obj*) {}
int wake_lat_on() { return 0; }
void wake_lat_note(int, int64_t) {}
void spin_until_set(Obj*, volatile int*) {}
int64_t hos_now_ns() { return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
void hos_cv_sleep(std::condition_variable_any* cv, std::mutex* m, unsigned ms) {
    ++parked;
    if (model_cores) {
        cv->wait(*m, [] { return runnable & (1u << core); });
        runnable &= ~(1u << core);
        if (core == 0) cv->wait(*m, [] { return core0_available; });
    } else if (ms == INFINITE) cv->wait(*m);
    else cv->wait_for(*m,std::chrono::milliseconds(ms));
    --parked;
}
int wait_until(Obj* e,int64_t deadline) {
    auto remaining=deadline-hos_now_ns();
    if (remaining<=0) return 0;
    hos_cv_sleep(&e->cv,&e->lk,(unsigned)((remaining+999999)/1000000));
    return 1;
}
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL %s\n",#x); std::exit(1); } } while (0)
"""
    cases = r"""
int main(int argc, char **argv) {
    const bool legacy = argc > 1 && !std::strcmp(argv[1], "legacy");
    CHECK(event_fast() == !legacy);
    Obj e; e.autoclear=1;
    event_signal(&e); event_signal(&e);
    CHECK(event_peek(&e)); CHECK(!event_wait(&e,0,1));
    CHECK(!event_wait(&e,1000000,0));
    if (!legacy) CHECK(wakes==0);
    std::vector<std::thread> workers;
    for (int i=0;i<8;++i) workers.emplace_back([&] { if (event_wait(&e,0,0)) ++passed; });
    while (parked!=8) std::this_thread::yield();
    for (int i=1;i<=8;++i) {
        event_signal(&e);
        while (passed<i) std::this_thread::yield();
        CHECK(passed==i);
    }
    for(auto& w:workers) w.join();
    workers.clear(); e.autoclear=0; passed=0;
    for(int i=0;i<8;++i) workers.emplace_back([&] { if (event_wait(&e,0,0)) ++passed; });
    while(parked!=8) std::this_thread::yield();
    event_signal(&e);
    for(auto& w:workers) w.join();
    CHECK(passed==8); CHECK(event_peek(&e)); CHECK(event_wait(&e,0,1));
    event_clear(&e); CHECK(!event_peek(&e));
    if (legacy) return 0;
    workers.clear(); e.autoclear=1; model_cores=true;
    std::atomic<int> consumer{-1};
    for (int i=0;i<2;++i) workers.emplace_back([&,i] {
        core=i;
        if (event_wait(&e,0,0)) consumer=i;
    });
    while(parked!=2) std::this_thread::yield();
    event_signal(&e);
    {
        std::lock_guard lock(e.lk);
        CHECK((runnable & 2) || parked < 2); // core 1 was notified or has resumed
    }
    while(consumer==-1) std::this_thread::yield();
    CHECK(consumer==1 && !event_peek(&e));
    {
        std::lock_guard lock(e.lk);
        core0_available=true;
        e.cv.notify_all();
    }
    event_signal(&e);
    for(auto& w:workers) w.join();
    CHECK(consumer==0 && !event_peek(&e));
}
"""
    with tempfile.TemporaryDirectory() as tmp:
        cpp = Path(tmp) / "handoff.cpp"
        exe = cpp.with_suffix(".exe")
        cpp.write_text(harness + functions + cases)
        subprocess.run([args.cxx, "-std=c++17", "-O2", "-pthread", str(cpp), "-o", str(exe)], check=True)
        env = {k: v for k, v in os.environ.items() if k != "HOS_EVENT_FAST"}
        subprocess.run([str(exe)], check=True, timeout=15, env=env)
        subprocess.run([str(exe), "legacy"], check=True, timeout=15, env=dict(env, HOS_EVENT_FAST="0"))


if __name__ == "__main__":
    main()
