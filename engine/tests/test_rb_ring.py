"""HL_RB_LAG (gpu/vk/rb_ring.h): the rules that choose which staged result the game's CPU is handed.

A small program checks, with no GPU: the switch parse; the class of a read by its size; the
bytes staged a class; which entry a read at frame c is answered with at each lag (the newest with tag + lag <= c that was not applied
yet, none otherwise, an applied one never twice); the table's replacement of its oldest entry; the ring's allocation (wrap, total, an entry
overwritten once the ring has gone round); the per-read hash. And, by the text, that invalidate() skips the old waits only for a served
range, that the write-back is left out for a staged binding, and that the staged ranges are copied at the end of the submission.

python -I -B tests/test_rb_ring.py --cxx <compiler>
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CPP = r'''
#include "rb_ring.h"
#include <cstdio>
static int fails;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
int main() {
    using namespace rb_ring;
    CHECK(parse(nullptr, nullptr, nullptr).lag == 2 && !parse(nullptr, nullptr, nullptr).check && parse(nullptr, nullptr, nullptr).wait_ms == 200.0);
    CHECK(parse("0", nullptr, nullptr).lag == 0);   // (0 turns the ring off: the old waiting path)
    CHECK(parse("2", "1", "5").lag == 2 && parse("2", "1", "5").check && parse("2", "1", "5").wait_ms == 5.0);
    CHECK(parse("99", "0", nullptr).lag == kMaxLag && !parse("99", "0", nullptr).check && parse("-3", nullptr, nullptr).lag == 0);
    CHECK(class_of(0x40) == 2 && class_of(0x2000) == 3 && class_of(0x1040) == 1 && class_of(0xc0) == 1 && class_of(0x80) == 0 && class_of(0) == 0);
    CHECK(copy_bytes(1, 0x1040) == 16 && copy_bytes(1, 0xc0) == 0xc0 && copy_bytes(2, 0x40) == 0x40 && copy_bytes(3, 0x2000) == 0x2000);
    Entry e[kMaxEntries];
    unsigned n = 0;
    CHECK(pick(e, n, 10, 2, 0) < 0 && newest(e, n) < 0);
    push(e, n, Entry{1, 5, 100, 0, 0});
    push(e, n, Entry{2, 6, 101, 16, 16});
    push(e, n, Entry{3, 7, 102, 32, 32});
    CHECK(pick(e, n, 7, 1, 0) == 1);          // frame 7, lag 1: tags up to 6; the newest of those
    CHECK(pick(e, n, 7, 2, 0) == 0);          // lag 2: tags up to 5
    CHECK(pick(e, n, 6, 2, 0) < 0);           // nothing 2 frames old yet
    CHECK(pick(e, n, 8, 1, 0) == 2);
    CHECK(pick(e, n, 8, 1, 3) < 0 && pick(e, n, 8, 1, 2) == 2 && pick(e, n, 7, 2, 1) < 0);   // applied ones are not handed out again
    CHECK(newest(e, n) == 2);
    for (uint64_t s = 4; s <= 12; s++) push(e, n, Entry{s, (int64_t)s + 4, 0, 0, 0});
    CHECK(n == kMaxEntries);
    uint64_t lo = ~0ull;
    for (unsigned i = 0; i < n; i++) lo = e[i].seq < lo ? e[i].seq : lo;
    CHECK(lo == 5 && newest(e, n) >= 0 && e[newest(e, n)].seq == 12);
    uint64_t at = 0, total = 0;
    CHECK(allocate(at, total, 16) == 0 && allocate(at, total, 20) == 16 && at == 48 && total == 48);
    const uint64_t first_cum = total;
    allocate(at, total, kArena - 48 - 16);   // leaves 16 bytes at the end
    CHECK(at == kArena - 16);
    CHECK(allocate(at, total, 64) == 0 && at == 64 && total == first_cum + (kArena - 48 - 16) + 16 + 64);
    Entry old{1, 0, 0, 0, 0};
    CHECK(intact(old, 100, 64) && !intact(old, kArena + 1, 64) && !intact(Entry{}, 0, 16) && intact(old, kArena - 64, 64));
    const uint8_t d[4] = {1, 2, 3, 4}, d2[4] = {1, 2, 3, 5};
    CHECK(hash_read(2, 0x1000, d, 4) == hash_read(2, 0x1000, d, 4) && hash_read(2, 0x1000, d, 4) != hash_read(2, 0x1000, d2, 4));
    CHECK(hash_read(2, 0x1000, d, 4) != hash_read(3, 0x1000, d, 4) && hash_read(2, 0x1000, d, 4) != hash_read(2, 0x1040, d, 4));
    CHECK(hash_read(2, 0x1000, nullptr, 0) != hash_read(2, 0x1000, d, 4));
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
    assert "rbring_serves(host->gpu_guest(gpu), size)" in inv, "the serve decision is missing"
    assert "if (size && !expo_skip && !ring_serve) {" in inv, "the old wait is not skipped for a served range only"
    assert inv.index("if (ring_serve) rbring_apply(gpu, size);") > inv.index("if (size && !expo_skip && !ring_serve) {"), "the answer comes before the wait's place"
    assert "rbring_check(gpu, size)" in inv, "the check is not called"
    mem = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    assert "!rbring_stage_bind(guest, size, p->mirror, off))\n            writebacks.push_back" in mem, "a staged binding still queues its write-back"
    assert "rbring_flush(cmd, mem_rec_value);" in mem and mem.index("rbring_flush(cmd, mem_rec_value);") < mem.index("if (no_writeback) writebacks.clear();"), \
        "the staged ranges are not copied before the write-backs"
    print("PASS the wiring")


if __name__ == "__main__":
    main()
