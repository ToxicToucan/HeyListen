"""HL_RB_LAG (gpu/vk/rb_ring.h): the rules that choose which staged result the game's CPU is handed.

A small program checks, with no GPU: the switch parse; the class of a read by its size; the
bytes staged a class; which entry a read at frame c is answered with at each lag (the newest with tag + lag <= c that was not applied
yet, none otherwise, an applied one never twice); the table's replacement of its oldest entry; the ring's allocation (wrap, total, an entry
overwritten once the ring has gone round); the per-read hash; (EXPO1) a range's standing: a binding is staged only for a range the ring
can serve (never a tainted one), and a range whose writes went to guest memory is served again only once the GPU is past the submission
that took it over. And, by the text, that invalidate() skips the old waits only for a served range, that the write-back is left out for a
staged binding only, and that the staged ranges are copied at the end of the submission.

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
    {   // (EXPO1) a range's standing: which written bindings are staged, when its reads are served
        Standing q;   // a range no program writes: served after kQuietReads reads
        q.reads = kQuietReads - 1;
        CHECK(!served(q, 0));
        q.reads = kQuietReads;
        CHECK(served(q, 0));
        Standing a;   // the meter's block: read, then written in a mirror every frame
        a.reads = 3, a.writes = 1;
        CHECK(stage(a) && a.stageable && a.staged == 1 && a.first_stage == 0 && a.settle == 0 && served(a, 0));
        a.writes++;
        CHECK(stage(a) && a.staged == 2 && served(a, 0));
        a.writes++;   // a binding in guest memory directly (its pool had no mirror): counted, not staged, written back
        CHECK(!served(a, ~0ull - 1));   // the old path answers: it reads guest memory, where that write-back lands
        a.writes++;   // the next one in a mirror: staged, but answered only once the GPU is past its submission
        CHECK(stage(a) && a.staged == 3 && a.writes - a.first_stage == a.staged && a.settle == kUnsettled && !served(a, ~0ull - 1));
        settle_at(a, 500);
        CHECK(a.settle == 500 && !served(a, 499) && served(a, 500));
        settle_at(a, 900);   // (a later submission does not move a settled value)
        CHECK(a.settle == 500);
        a.writes++;
        CHECK(stage(a) && a.settle == 500 && served(a, 500));   // in good standing: no new settle
        Standing b;   // two writes to guest memory before the first staged one
        b.writes = 3;
        CHECK(stage(b) && b.first_stage == 2 && b.staged == 1 && b.settle == kUnsettled && !served(b, ~0ull - 1));
        settle_at(b, 7);
        CHECK(served(b, 7));
        Standing t;   // a written binding over it that is not exactly it: never staged, so its own write-backs go on (the old path reads them)
        t.writes = 1, t.tainted = true;
        CHECK(!stage(t) && t.staged == 0 && !t.stageable && !served(t, ~0ull));
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
    rp = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    inv = rp[rp.index("void invalidate(uint64_t gpu, uint64_t size) {"):]
    assert "rbring_serves(host->gpu_guest(gpu), size)" in inv, "the serve decision is missing"
    assert "if (size && !expo_skip && !ring_serve) {" in inv, "the old wait is not skipped for a served range only"
    assert inv.index("if (ring_serve) rbring_apply(gpu, size);") > inv.index("if (size && !expo_skip && !ring_serve) {"), "the answer comes before the wait's place"
    assert "rbring_check(gpu, size)" in inv, "the check is not called"
    mem = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    assert "!rbring_stage_bind(guest, size, p->mirror, off, lists().rb))\n            lists().writebacks.push_back" in mem, "a staged binding still queues its write-back"
    assert "rbring_flush(cmd, rec_value_of(), sub.rb);" in mem and mem.index("rbring_flush(cmd, rec_value_of(), sub.rb);") < mem.index("if (no_writeback) writebacks.clear();"), "the staged ranges are not copied before the write-backs"
    # (EXPO1, docs/research/expo1-stuck-meter.md) a range the ring does not serve keeps its write-backs; one taken over again waits for its settle
    ring = (ROOT / "gpu/vk/vk_rbring.cpp").read_text(encoding="utf-8")
    sb = ring[ring.index("bool rbring_stage_bind("):ring.index("void rbring_flush(")]
    assert "} else if (!rb_ring::stage(it->second)) {" in sb and "st_kept_wb++;\n        return false;" in sb, \
        "a binding of a range the ring does not serve is staged (its write-back left out)"
    assert sb.index("rb_ring::stage(it->second)") < sb.index("pend.reqs.push_back"), "the standing is decided after the binding is queued"
    fl = ring[ring.index("void rbring_flush("):ring.index("bool rbring_quiet(")]
    assert "rb_ring::settle_at(a, value);" in fl, "a range taken over again never gets its settle value"
    sv = ring[ring.index("bool rbring_serves("):ring.index("void rbring_apply(")]
    assert "settle == rb_ring::kUnsettled || settle > vk_timeline_completed()" in sv, "a range taken over again is served before the GPU is past it"
    print("PASS the wiring")


if __name__ == "__main__":
    main()
