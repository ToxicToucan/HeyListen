"""HL_EXPO_GPU (gpu/vk/expo_feed.h): the rules that choose which dispatch's sums the game's CPU is handed.

A small program checks, with no GPU: the switch parse (mode 0..2, lag 1..14, default 2); which sequence a read wants at each lag (none
before the first dispatches, the newest at lag 1, the one before at lag 2); the slot of a sequence; the newest older stamp as the fallback
(and none once the ring has overwritten it); the ledger (blocks by guest address, a full table, one counter a block). And, by the text,
that invalidate() skips the old wait only for a block the ring is fed from, and that the producer is recorded after the meter dispatch.

python -I -B tests/test_expo_feed.py --cxx <compiler>
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CPP = r'''
#include "expo_feed.h"
#include <cstdio>
static int fails;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
int main() {
    using namespace expo_feed;
    CHECK(parse(nullptr, nullptr).mode == 0 && parse(nullptr, nullptr).lag == 2);
    CHECK(parse("1", nullptr).mode == 1 && parse("2", "1").mode == 2 && parse("2", "1").lag == 1);
    CHECK(parse("7", "99").mode == 2 && parse("7", "99").lag == kRing - 2 && parse("-1", "0").mode == 0 && parse("1", "0").lag == 1);
    CHECK(wanted(0, 1) == 0 && wanted(0, 2) == 0 && wanted(1, 2) == 0 && wanted(2, 2) == 1 && wanted(1, 1) == 1 && wanted(40, 2) == 39);
    CHECK(slot_of(1) == 1 && slot_of(kRing) == 0 && slot_of(kRing + 3) == 3);
    uint32_t st[kRing] = {};
    CHECK(newest_older(st, 5) < 0);
    for (uint32_t s = 1; s <= 4; s++) st[slot_of(s)] = s;
    CHECK(newest_older(st, 5) == 4 && newest_older(st, 4) == 3 && newest_older(st, 1) < 0);
    st[slot_of(4)] = 4 + kRing;   // overwritten by a later dispatch: not older than the wanted one
    CHECK(newest_older(st, 5) == (int)slot_of(3));
    CHECK(newest_older(st, 3 + kRing + 1) == (int)slot_of(4) || newest_older(st, 3 + kRing + 1) < 0);
    Ledger l;
    CHECK(l.find(0x1000) < 0 && l.add(0x1000) == 0 && l.add(0x2000) == 1 && l.add(0x1000) == 0 && l.find(0x2000) == 1);
    CHECK(l.issue(0) == 1 && l.issue(0) == 2 && l.issue(1) == 1);
    for (unsigned i = 2; i < kBlocks; i++) CHECK(l.add(0x3000 + i) == (int)i);
    CHECK(l.add(0x9000) < 0);
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
    rp = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    inv = rp[rp.index("void invalidate(uint64_t gpu, uint64_t size) {"):]
    assert "expo_mode() == 1 && expo_serves(host->gpu_guest(gpu))" in inv, "the skip is not for a fed block in mode 1"
    assert "if (size && !expo_skip && !ring_serve) {" in inv, "the old wait is not skipped for a fed block only"
    assert inv.index("if (expo_skip) {\n            expo_answer(gpu);") > inv.index("if (size && !expo_skip && !ring_serve) {"), "the answer comes before the wait's place"
    assert "expo_check(gpu)" in inv, "the check is not called"
    dr = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    assert dr.index("vkCmdDispatch(cmd, x, y, z);") < dr.index("expo_record(cmd, meter_out.buffer"), "the ring is fed before the dispatch"
    print("PASS the wiring")


if __name__ == "__main__":
    main()
