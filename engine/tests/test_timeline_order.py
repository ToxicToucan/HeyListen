# Copyright (C) 2026 Sacrappy
"""The replay's timeline signals never go back (gpu/vk/timeline_order.h, DECOUPLE3, docs/research/decouple-cross-frame.md 4.9).

With RX_VK_ORDER_THREAD=1 the order thread holds a present for the CPU's signal and runs the jobs behind it meanwhile (PIPE4). Their
values are above the present's; their recordings signalled past it, and the present's own signal then went back on the timeline. A
late fix deferred meanwhile (RX_VK_LATE_DEFER: its value at or below the highest signal sent) waited for a value the timeline had gone
back below, every recording held behind it: the PC 3050's load hang (matrix82, matrix97).

A small program checks, with no GPU: signal_at below the oldest stopped submission and below a held present; the order thread's
sequence (a present held, the jobs behind it recorded, the present, the next recording) signals strictly rising values with the
rule, and went back without the held present's bound (the bug, as it was). And, by the text, that vk_replay.cpp uses the rule where
a recording ends, notes the held present on the order thread from the moment it takes the present job until it is recorded, never
signals a present below the last signal, logs a signal that would go back, bounds a late fix's wait (a last resort, logged), and
that a capture's take does not wait for a late value behind a held present.

python -I -B tests/test_timeline_order.py --cxx <compiler>
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CPP = r'''
#include "timeline_order.h"
#include <cstdio>
#include <vector>
static int fails;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
using namespace timeline_order;
constexpr uint64_t kNone = UINT64_MAX;
// The order thread's sequence: submissions 1..9 recorded, a present with value 10 taken and held, jobs 11..14 recorded behind it,
// then the present (its own value), then submission 15. Returns the signals sent, in queue order.
static std::vector<uint64_t> run(bool held_bound) {
    std::vector<uint64_t> sent;
    uint64_t done_max = 0, signaled = 0, held = 0;
    auto end_recording = [&](uint64_t value) {
        done_max = std::max(done_max, value);
        const uint64_t at = signal_at(done_max, kNone, held_bound ? held : 0);
        if (signal_moves(at, signaled)) signaled = at, sent.push_back(at);
    };
    for (uint64_t v = 1; v <= 9; v++) end_recording(v);
    held = 10;
    for (uint64_t v = 11; v <= 14; v++) end_recording(v);
    // the present, as it was: its own value whatever was sent
    sent.push_back(10), signaled = std::max(signaled, (uint64_t)10), done_max = std::max(done_max, (uint64_t)10);
    held = 0;
    end_recording(15);
    return sent;
}
static bool rising(const std::vector<uint64_t>& s) {
    for (size_t i = 1; i < s.size(); i++)
        if (s[i] <= s[i - 1]) return false;
    return true;
}
int main() {
    CHECK(signal_at(7, kNone, 0) == 7);
    CHECK(signal_at(7, 5, 0) == 4);            // below the oldest stopped submission
    CHECK(signal_at(7, kNone, 6) == 5);        // below a held present
    CHECK(signal_at(7, 3, 6) == 2 && signal_at(7, 6, 3) == 2);
    CHECK(signal_at(2, kNone, 9) == 2);        // nothing past done_max
    CHECK(signal_moves(5, 4) && !signal_moves(4, 4) && !signal_moves(3, 4));
    const std::vector<uint64_t> fixed = run(true), before = run(false);
    CHECK(rising(fixed));
    CHECK(fixed.back() == 15);
    CHECK(!rising(before));                    // the bug: 14 sent behind the held present, then its 10
    // a late fix registered behind the held present: its value (13) at or below the highest sent only after the present
    {
        uint64_t sent_max = 0;
        for (uint64_t s : fixed) { if (s >= 10) break; sent_max = std::max(sent_max, s); }
        CHECK(sent_max < 13);                  // while held, 13 reads "not sent yet": no fix is deferred for it
    }
    if (!fails) std::printf("PASS the rule\n");
    return fails;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        cpp, exe = Path(tmp) / "t.cpp", Path(tmp) / "t.exe"
        cpp.write_text(CPP)
        subprocess.run([args.cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "gpu/vk"), str(cpp), "-o", str(exe)],
                       check=True)
        subprocess.run([str(exe)], check=True)
    rp = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    mem = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")

    def function(text, head):
        i = text.index(head)
        j = text.index("{", i)
        depth = 0
        for k in range(j, len(text)):
            depth += {"{": 1, "}": -1}.get(text[k], 0)
            if depth == 0:
                return text[i:k + 1]
        raise SystemExit("FAIL: unbalanced " + head)

    def need(c, msg):
        if not c:
            raise SystemExit("FAIL: " + msg)

    rule = "timeline_order::signal_at(done_max, oldest_unfinished(), ot_held_present.load(std::memory_order_acquire))"
    for head in ("void finish_recording(uint64_t value) {", "void finish_recording_deferred(uint64_t value) {"):
        f = function(rp, head)
        need(rule in f and "timeline_order::signal_moves(at, signaled)" in f, head + " does not signal below a held present")
        need("oldest - 1" not in f, head + " still has its own rule")
    m = function(rp, "void ot_main() {")
    p = m[m.index("if (got && j.kind == JOB_PRESENT) {"):]
    need(p.index("ot_held_present.store(j.value") < p.index("ot_present_run(j, ms == 0)"), "the present's value is not noted before it is run")
    need(m.count("ot_held_present.store(0") == 2, "the held present's value is not cleared once it is recorded (at once, or held)")
    h = m[m.index("if (ot_present_run(held, force)) {"):]
    need(h.index("ot_held_present.store(0") < h.index("finish(held);"), "the held present's value is not cleared when it is recorded")
    dp = function(rp, "void do_present(const nvn_tex* t, const int crop[4]) {")
    need("const bool rises = timeline_order::signal_moves(value, signaled);" in dp and "submit_recording(r, rises);" in dp,
         "a present may signal below the timeline's last signal")
    need("submit_recording(r);" not in dp, "a present signals its value whatever was sent")
    ns = function(rp, "void note_signal_sent(uint64_t v) {")
    need("the timeline went back" in ns, "a signal that goes back is not said")
    lf = function(rp, "bool late_fix_wait_unlocked() {")
    need("std::chrono::seconds(2)" in lf and "memory_late_fix_apply(lv);" in lf and "not reached in 2 s" in lf,
         "a late fix's wait has no last-resort bound")
    need("uint64_t vk_timeline_held_present() { return ot_held_present.load(" in rp, "the held present is not told to the captures")
    tk = function(mem, "bool take_capture(std::unique_ptr<Capture> c, uint64_t value, uint64_t completed) {")
    need("const bool behind_held = held && c->late_value >= held && c->late_value > vk_timeline_signal_sent();" in tk
         and "if (behind_held) done = false;" in tk and "!behind_held;" in tk,
         "a take waits for a late value behind a held present (no wait can reach it before the present)")
    print("PASS the wiring")


if __name__ == "__main__":
    main()
