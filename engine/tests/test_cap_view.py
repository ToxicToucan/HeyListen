# Copyright (C) 2026 Sacrappy
"""RX_VK_CAP_DEPTH (gpu/vk/cap_view.h): the capture view of a submission, several captures deep (DECOUPLE stage 2).

A small program checks, with no GPU: the switch parse (unset 1, clamped to 1..4); the history of a page's replaced entries (newest
first, the oldest falls out at the depth); which entry a submission whose newest capture is `limit` reads (the current one when it is
not newer; else the newest replaced one that is not; none when the page had no entry then; none and "lost" when every kept entry is
newer); that depth 1 answers as SUB1's one kept entry did. And, by the text, that vk_memory.cpp keeps the history with the rule's
push at both places a capture replaces an entry, reads a worker's view through the rule's pick, and counts a lost view in the check.

python -I -B tests/test_cap_view.py --cxx <compiler>
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CPP = r'''
#include "cap_view.h"
#include <cstdio>
static int fails;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
int main() {
    using namespace cap_view;
    CHECK(parse_depth(nullptr) == 1 && parse_depth("") == 1 && parse_depth("0") == 1 && parse_depth("-2") == 1);
    CHECK(parse_depth("1") == 1 && parse_depth("3") == 3 && parse_depth("4") == kMaxDepth && parse_depth("99") == kMaxDepth);
    bool old = true, lost = true;
    {   // depth 1: SUB1's one entry (the entry the newest capture replaced)
        Entry hist[1]{};
        Entry cur{10, 5};
        CHECK(pick(cur, hist, 1, 0, &old, &lost).gen == 10 && !old && !lost);     // no limit: the newest
        CHECK(pick(cur, hist, 1, 10, &old, &lost).gen == 10 && !old && !lost);    // the limit's own capture
        CHECK(pick(cur, hist, 1, 12, &old, &lost).slot == 5 && !old && !lost);    // a worker ahead of the entry: the newest
        CHECK(pick(cur, hist, 1, 9, &old, &lost).gen == 0 && !old && !lost);      // the page had no entry before capture 10: guest memory
        push(hist, 1, cur);
        cur = Entry{11, 7};
        Entry e = pick(cur, hist, 1, 10, &old, &lost);
        CHECK(e.gen == 10 && e.slot == 5 && old && !lost);                        // behind by one capture: the replaced entry
        push(hist, 1, cur);
        cur = Entry{12, 9};
        e = pick(cur, hist, 1, 10, &old, &lost);
        CHECK(e.gen == 0 && !old && lost);                                        // behind by two with one kept: lost, and said so
        e = pick(cur, hist, 1, 11, &old, &lost);
        CHECK(e.gen == 11 && e.slot == 7 && old && !lost);
    }
    {   // depth 3: three submissions' segments recording at once
        Entry hist[3]{};
        Entry cur{};
        for (uint32_t g = 20; g <= 23; g++) {
            if (cur.gen) push(hist, 3, cur);
            cur = Entry{g, g * 2};
        }
        CHECK(hist[0].gen == 22 && hist[1].gen == 21 && hist[2].gen == 20);       // newest first
        for (uint32_t limit = 20; limit <= 23; limit++) {
            const Entry e = pick(cur, hist, 3, limit, &old, &lost);
            CHECK(e.gen == limit && e.slot == limit * 2 && old == (limit != 23) && !lost);
        }
        CHECK(pick(cur, hist, 3, 19, &old, &lost).gen == 0 && lost);              // older than everything kept
        push(hist, 3, cur);
        cur = Entry{24, 48};
        CHECK(hist[0].gen == 23 && hist[2].gen == 21);                            // 20 fell out
        CHECK(pick(cur, hist, 3, 20, &old, &lost).gen == 0 && lost);
        CHECK(pick(cur, hist, 3, 21, &old, &lost).gen == 21 && old && !lost);
        // a page taken by captures 30 and 33 only: a limit between them reads 30
        Entry h2[3]{};
        push(h2, 3, Entry{30, 1});
        const Entry c2{33, 2};
        CHECK(pick(c2, h2, 3, 32, &old, &lost).gen == 30 && old && !lost);
        CHECK(pick(c2, h2, 3, 29, &old, &lost).gen == 0 && !old && !lost);        // no entry before 30: not lost, there was none
    }
    if (!fails) std::printf("PASS the rules\n");
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
    mem = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    assert 'cap_view::parse_depth(getenv("RX_VK_CAP_DEPTH"))' in mem, "the depth is not read from RX_VK_CAP_DEPTH"
    assert "new cap_view::Entry[kCapChunkPages * cap_depth()]()" in mem, "the history is not cap_depth() entries a page"
    assert mem.count("cap_prev_push(page);") == 2, "a capture replaces an entry without keeping it (two places: cap_entry_set, the claimed copy's stamps)"
    push = mem[mem.index("inline void cap_prev_push("):mem.index("inline void cap_entry_set(")]
    assert "cap_view::push(" in push and "cap_depth()" in push, "the history is not kept by the rule"
    view = mem[mem.index("inline CapEntry cap_entry_view("):mem.index("// (each kept capture's copy on the GPU")]
    assert "if (!tl_cap_limit || e.gen <= tl_cap_limit) return e;" in view, "a thread at or ahead of the entry does not read the newest"
    assert "cap_view::pick(" in view and "tl_cap_limit, old, &lost)" in view, "a worker's view is not read by the rule"
    assert "st_sub_cap_lost.fetch_add(1" in view, "a view deeper than the history is not counted"
    rp = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    assert "views deeper than the capture history kept (RX_VK_CAP_DEPTH) %lld" in rp and "(long long)sc.cap_lost" in rp, "the check does not log the lost views"
    print("PASS the wiring")


if __name__ == "__main__":
    main()
