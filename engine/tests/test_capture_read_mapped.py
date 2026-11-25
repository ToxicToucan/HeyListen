"""A replay read of a virtual GPU address across a page boundary takes each page's capture from where that page maps.

python -I -B tests/test_capture_read_mapped.py [--cxx clang++]

gpu/vk/vk_memory.cpp's memory_read_as_submitted is cut out of the source and run against a fake host whose virtual GPU addresses
(1 TB and up) map consecutive pages to unrelated pages of a 4-page guest memory, with a capture of one guest page (page 1, all 42):
- a read inside one virtual page takes the capture if its page is that guest page, and not otherwise;
- a read across two virtual pages takes the capture only for the part whose own page maps to the captured guest page (before, the
  capture was looked up from the first page's guest address onward, as if the second page followed the first in guest memory: the
  wrong page's capture laid over live bytes, or the right page's missed);
- a direct (below 1 TB) read across pages is as before;
- RX_VK_READ_MAPPED=0 gives the old, contiguous answers back.
memory_page_stamp answers 0 for an address past the generation table or the guest's memory.

Ported from Longshot 7daafa7a (#1553, HumanBean), d87dd600 and a8947062's mapped reads ("translate discontiguous virtual ranges page
by page"); the harness is HeyListen's (its read keeps the late-fix, granule and cached-page rules, which are stubbed out here).
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from srccut import block

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

HARNESS = r"""
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <vector>
constexpr uint64_t kPageShift = 12, kPages = 4;
constexpr uint64_t virt = 1ull << 40;
static uint8_t ram[4 * 4096], capture[4096];
struct Host {
    uint8_t* guest_base() { return ram; }
    uint64_t guest_size() { return sizeof ram; }
    // (virtual page 0 -> guest page 0, 1 -> 2, 2 -> 1: neighbours that are not neighbours in guest memory)
    uint64_t gpu_guest(uint64_t a) {
        if (a < virt) return a < sizeof ram ? a : 0;
        const uint64_t off = a - virt, pg = off >> 12, in = off & 4095;
        static const uint64_t map[3] = {0, 2, 1};
        return pg < 3 ? (map[pg] << 12) + in : 0;
    }
    bool gpu_read(uint64_t a, void* dst, uint64_t n) {
        for (uint64_t i = 0; i < n; ++i) {
            const uint64_t p = gpu_guest(a + i);
            if (!p && a + i >= virt) return false;
            static_cast<uint8_t*>(dst)[i] = ram[p ? p : a + i];
        }
        return true;
    }
} api, *host = &api;
std::mutex pools_mu;
std::atomic<bool> captures_seen{true};
std::atomic<uint64_t> cap_view_gen{1};
static bool capture_on() { return true; }
static int cap_only_gpu() { return 0; }
static void cap_excl_note(uint64_t, uint64_t, const char*) {}
struct SubmittedPage { uint64_t page = ~0ull; uint32_t pgen = 0; uint64_t view = 0; };
static uint32_t page_gen(uint64_t p) { assert(p < kPages); return 1; }
static bool submitted_known(uint64_t, uint32_t, uint64_t) { return false; }
static SubmittedPage& submitted_page_for(uint64_t) { static SubmittedPage s; return s; }
struct CapEntry { uint32_t gen, slot; };
static CapEntry cap_entry(uint64_t page) { return page == 1 ? CapEntry{1, 0} : CapEntry{0, 0}; }
// (stream SUB1: the submission's view: a thread outside a segment reads the newest entry; the pending-submission hook is not here)
static thread_local bool tl_rec_set = false;
static thread_local uint32_t tl_cap_limit = 0;
static std::atomic<uint32_t> cap_latest{0};
static CapEntry cap_entry_view(uint64_t page, bool* old) { *old = false; return cap_entry(page); }
static bool memory_late_fix_pending() { return false; }
static void replay_sub_complete_if_pending() {}
struct CapBuf { void* map; std::vector<uint8_t>* gmask; };
static std::map<uint32_t, CapBuf> cap_bufs = {{1, {capture, nullptr}}};
struct PageX { uint8_t full; };
static PageX* pagex(uint64_t, bool) { return nullptr; }
static std::atomic<int64_t> st_gran_made_full{0}, st_late_reads{0};
static std::atomic<int64_t> st_read_late{0};   // (PIPE3: the reads that took guest memory for a page written after its copy)
static bool late_fix_slot_pending(uint32_t, uint32_t, uint64_t*) { return false; }
static void vk_timeline_wait_sent(uint64_t) {}
static void memory_late_fix_apply(uint64_t) {}
static uint64_t vk_timeline_completed() { return 0; }
static bool guarded_copy(void* d, const void* s, size_t n) { std::memcpy(d, s, n); return true; }
#define logf(...) ((void)0)
"""
CASES = r"""
int main(int argc, char** argv) {
    const bool old = argc > 1;
    std::memset(ram, 19, sizeof ram);
    std::memset(capture, 42, sizeof capture);
    uint8_t b[8];
    // inside virtual page 2 (guest page 1, captured): the capture
    std::memset(b, 0, 8);
    assert(memory_read_as_submitted(virt + 8200, b, 8) && b[0] == 42 && b[7] == 42);
    // inside virtual page 1 (guest page 2): live
    std::memset(b, 0, 8);
    assert(memory_read_as_submitted(virt + 4100, b, 8) && b[0] == 19 && b[7] == 19);
    // across virtual pages 0 and 1 (guest pages 0 and 2): no capture for either. The old code laid guest page 1's over the second half
    std::memset(b, 0, 8);
    assert(memory_read_as_submitted(virt + 4092, b, 8) && b[0] == 19 && b[3] == 19 && b[4] == (old ? 42 : 19));
    // across virtual pages 1 and 2 (guest pages 2 and 1): the second half is the captured page's
    std::memset(b, 0, 8);
    assert(memory_read_as_submitted(virt + 8188, b, 8) && b[0] == 19 && b[3] == 19 && b[4] == (old ? 19 : 42));
    // a direct read across guest pages 0 and 1: as before in both
    std::memset(b, 0, 8);
    assert(memory_read_as_submitted(4092, b, 8) && b[3] == 19 && b[4] == 42);
    // past the tables: no stamp
    assert(memory_page_stamp(1ull << 39) == 0 && memory_page_stamp(3 * 4096 + 5) == 1 && memory_page_stamp(4 * 4096 + 5) == 0);
    std::puts("ok");
}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cxx", default="c++")
    args = parser.parse_args()
    source = (ROOT / "gpu/vk/vk_memory.cpp").read_text()
    funcs = "\n".join(block(source, m) for m in ("bool memory_read_as_submitted(", "uint32_t memory_page_stamp("))
    with tempfile.TemporaryDirectory() as temp:
        fixture = Path(temp) / "read.cpp"
        fixture.write_text(HARNESS + funcs + CASES)
        exe = fixture.with_suffix(".exe")
        subprocess.run([args.cxx, "-std=c++17", "-O1", "-w", "-UNDEBUG", str(fixture), "-o", str(exe)], check=True)
        env = {k: v for k, v in os.environ.items() if k != "RX_VK_READ_MAPPED"}
        subprocess.run([str(exe)], check=True, env=env, timeout=30)
        subprocess.run([str(exe), "old"], check=True, env=dict(env, RX_VK_READ_MAPPED="0"), timeout=30)
    print("PASS: a read across virtual pages takes each page's capture from where the page maps; RX_VK_READ_MAPPED=0 as before")


if __name__ == "__main__":
    main()
