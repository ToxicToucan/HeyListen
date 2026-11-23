"""Check capture pool enumeration, expiry and range invalidation. Run python -I -B tests/test_capture_pool_refresh.py [--cxx clang++].

gpu/vk/vk_memory.cpp's refresh_pool_ranges_locked (with its switch and the merge helper) is cut out of the source and run against
a fake host whose pools and mapping generation the test changes:
- default, no expiry, no RX_VK_CAP_ONLY_GPU: unchanged pools are not enumerated again after 64 captures, a new mapping generation
  enumerates them but moves the ranges' version only when the list changed, an added pool and a removed one both do;
- with RX_VK_CAPTURE_EAGER expiry: a pool expires after its n captures and the version moves;
- with RX_VK_CAP_ONLY_GPU (the default in the game): the periodic rebuild stays (it reconciles the live pools), but an unchanged
  list does not move the version;
- RX_VK_CAP_RANGES_REUSE=0: the rebuild every 64 captures and every generation, and its version bump, as before.

Ported from Longshot 7daafa7a (#1553, HumanBean), e37f44d8 / 6156d57b; the harness and the ONLY_GPU and switch cases are HeyListen's.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from srccut import block


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cxx", default="c++")
    args = parser.parse_args()
    source = (ROOT / "gpu/vk/vk_memory.cpp").read_text()
    harness = r"""
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>
using Range = std::pair<uint64_t, uint64_t>;
static uint64_t captures_made, pool_cap_built, cap_ranges_ver = 1, expiry;
static uint32_t pool_cap_gen, generation = 1;
static unsigned scans;
static int only_gpu;
static std::vector<Range> pool_cap_ranges, all_pool_ranges;
struct PoolStartHash { size_t operator()(uint64_t k) const { return (size_t)k; } };
static std::unordered_map<uint64_t, uint64_t, PoolStartHash> pool_first_seen;
static std::vector<Range> pools = {{0x2000, 0x1000}};
static uint64_t capture_pool_max() { return 1u << 20; }
static uint64_t eager_captures() { return expiry; }
static int cap_only_gpu() { return only_gpu; }
static void* GetModuleHandleA(const char*) { return nullptr; }
static void* GetProcAddress(void*, const char*) { return nullptr; }
struct Host {
    uint32_t maps_generation() { return generation; }
    int gpu_pools(uint64_t (*out)[2], int max) {
        ++scans;
        assert(pools.size() <= (unsigned)max);
        for (size_t i = 0; i < pools.size(); ++i) { out[i][0] = pools[i].first; out[i][1] = pools[i].second; }
        return (int)pools.size();
    }
} instance, *host = &instance;
"""
    cases = r"""
int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "";
    expiry = mode == "expiry" ? 64 : 0;
    only_gpu = mode == "gpu" || mode == "legacy_gpu";
    const bool reuse = !(mode == "legacy" || mode == "legacy_gpu");
    refresh_pool_ranges_locked();
    assert(pool_cap_ranges.size() == 1);
    auto version = cap_ranges_ver;
    captures_made = 64;
    refresh_pool_ranges_locked();
    if (!reuse) {
        assert(scans == 2 && cap_ranges_ver != version);
        version = cap_ranges_ver;
        ++generation; refresh_pool_ranges_locked();
        assert(scans == 3 && cap_ranges_ver != version);
        return 0;
    }
    if (expiry) {
        assert(scans == 2 && pool_cap_ranges.empty() && cap_ranges_ver != version);
        pools.clear(); ++generation; refresh_pool_ranges_locked();
        pools.emplace_back(0x2000,0x1000); ++generation; refresh_pool_ranges_locked();
        assert(pool_cap_ranges.size() == 1);
    } else if (only_gpu) {
        // (the periodic rebuild is kept, the version moves only with a change)
        assert(scans == 2 && cap_ranges_ver == version);
        ++generation; refresh_pool_ranges_locked();
        assert(scans == 3 && cap_ranges_ver == version);
        pools.emplace_back(0x3000,0x1000); ++generation; refresh_pool_ranges_locked();
        assert(cap_ranges_ver != version && pool_cap_ranges.front() == Range(0x2000,0x4000));
    } else {
        if (scans != 1) { std::fprintf(stderr, "re-enumerated unchanged pools without expiry\n"); return 1; }
        assert(cap_ranges_ver == version);
        ++generation; refresh_pool_ranges_locked();
        assert(scans == 2 && cap_ranges_ver == version);
        pools.emplace_back(0x3000,0x1000); ++generation; refresh_pool_ranges_locked();
        assert(pool_cap_ranges.size() == 1 && pool_cap_ranges.front() == Range(0x2000,0x4000));
        assert(cap_ranges_ver != version);
        pools.clear(); ++generation; refresh_pool_ranges_locked();
        assert(pool_cap_ranges.empty());
    }
}
"""
    helpers = "\n".join(block(source, m) for m in ("bool cap_ranges_reuse()", "std::vector<std::pair<uint64_t, uint64_t>> ranges_merged("))
    with tempfile.TemporaryDirectory() as temp:
        fixture = Path(temp) / "refresh.cpp"
        fixture.write_text(harness + helpers + block(source, "void refresh_pool_ranges_locked()") + cases)
        exe = fixture.with_suffix(".exe")
        subprocess.run([args.cxx, "-std=c++17", "-O2", "-UNDEBUG", str(fixture), "-o", str(exe)], check=True)
        env = {k: v for k, v in os.environ.items() if k != "RX_VK_CAP_RANGES_REUSE"}
        for mode in ("", "expiry", "gpu"):
            subprocess.run([str(exe), mode], check=True, env=env)
        for mode in ("legacy", "legacy_gpu"):
            subprocess.run([str(exe), mode], check=True, env=dict(env, RX_VK_CAP_RANGES_REUSE="0"))
    print("PASS: capture pool ranges are rebuilt only when their membership or expiry can change")


if __name__ == "__main__":
    main()
