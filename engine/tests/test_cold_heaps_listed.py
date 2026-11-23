# Copyright (C) 2026 Sacrappy

"""The cold-page heap pass over the OS layer's own books (host/hl_mem.c heaps_listed, heaps_pass_listed; HL_MEM_COLD_LISTED).

The pass that found the heaps' committed pages with VirtualQuery held the address space lock through queries of 100+ ms
over large lazily committed ranges, and a game thread committing memory meanwhile kept its virtual core 250 ms (the
v0.4.0 benchmark's steady-play spikes). The listed pass takes the heap and the address regions above it from hos_mem.c and
looks at their pages with QueryWorkingSetEx alone. This checks, with the two functions cut out of the source and the OS
calls stubbed: no VirtualQuery in it; the heap and the regions merged into whole 64 KB units, ascending; pools and stacks
(the excluded ranges) never looked at; each pass looks at no more than its budget; and passes go on where the last one
stopped, so that a sweep looks at every listed page once and then starts again at the heap's base.

python -I -B tests/test_cold_heaps_listed.py [--cxx clang++]
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def define(text, name):
    m = re.search(r'^#define %s .*$' % name, text, re.M)
    if not m:
        raise SystemExit('FAIL: no #define ' + name)
    return m.group(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cxx', default='g++')
    parser.add_argument('--source', type=Path, default=ROOT / 'host/hl_mem.c')
    args = parser.parse_args()
    text = args.source.read_text(encoding='utf-8')
    listed = block(text, 'static int heaps_listed(')
    pass_ = block(text, 'static void heaps_pass_listed(')
    if 'VirtualQuery' in listed + pass_:
        raise SystemExit('FAIL: the listed heap pass calls VirtualQuery')
    if not re.search(r'#define HL_MEM_COLD_LISTED_DEFAULT 1\b', text):
        raise SystemExit('FAIL: the listed heap pass is not the default')
    if 'else if (heaps_listed_on) heaps_pass_listed();' not in text:
        raise SystemExit('FAIL: the cold thread does not run the listed pass when it is on')
    fixture = '\n'.join([
        '#include <cstdint>', '#include <cstdio>', '#include <cstdlib>', '#include <vector>', '#include <utility>',
        '#define CHECK(ok) do { if (!(ok)) { std::fprintf(stderr, "failed line %d\\n", __LINE__); std::exit(1); } } while (0)',
        '#define HOS_HEAP_BASE 0x1000000000ULL', '#define HOS_GUEST_SPACE (1ULL << 39)', '#define HL_MEM_COLD_HEAPS 1',
        define(text, 'COLD_UNIT_MASK'), define(text, 'COLD_MAX_LISTED'), define(text, 'COLD_LIST_SCAN_PAGES'),
        define(text, 'COLD_MAX_EXCLUDED'),
        r'''
static uint64_t hos_heap_size;
static uint64_t listed[COLD_MAX_LISTED][2];
static std::vector<std::pair<uint64_t, uint64_t>> regions;   // (ascending, as hos_regions_above gives them)
static int hos_regions_above(uint64_t from, uint64_t (*out)[2], int max) {
    int n = 0;
    for (auto& r : regions) if (r.first >= from && n < max) out[n][0] = r.first, out[n][1] = r.second, n++;
    return n;
}
static uint64_t excluded[COLD_MAX_EXCLUDED][2];
static std::vector<std::pair<uint64_t, uint64_t>> excl;
static int heaps_excluded(void) {
    int n = 0;
    for (auto& r : excl) excluded[n][0] = r.first, excluded[n][1] = r.second, n++;
    return n;
}
static int passes;
static void hl_mem_cold_pass(int) { passes++; }
static int64_t heap_sweeps;
static std::vector<std::pair<uint64_t, uint64_t>> looked;   // (the ranges a pass gave cold_range)
static int64_t cold_range(int, uint64_t a, uint64_t b) {
    looked.push_back({a, b});
    return (int64_t)((b - a) >> 12);
}
''', listed, pass_, r'''
static const uint64_t U = 0x10000, MB = 1 << 20;
int main() {
    hos_heap_size = 100 * MB + 0x1000;   // (not a whole unit: looked at to the unit's end)
    const uint64_t r0 = HOS_HEAP_BASE + 200 * MB;
    regions = {{HOS_HEAP_BASE + hos_heap_size + U, HOS_HEAP_BASE + 150 * MB},   // (in the unit after the heap's: merged)
               {r0, r0 + 600 * MB}, {r0 + 600 * MB, r0 + 1000 * MB}};        // (adjacent ones merge too)
    excl = {{HOS_HEAP_BASE + 10 * MB, HOS_HEAP_BASE + 12 * MB}, {r0 + 50 * MB, r0 + 51 * MB}};
    const int n = heaps_listed();
    CHECK(n == 2);
    CHECK(listed[0][0] == HOS_HEAP_BASE && listed[0][1] == HOS_HEAP_BASE + 150 * MB);
    CHECK(listed[1][0] == r0 && listed[1][1] == r0 + 1000 * MB);
    // one sweep: every listed page once, none excluded, at most the budget a pass
    const uint64_t budget = (uint64_t)COLD_LIST_SCAN_PAGES << 12;
    uint64_t total = 0;
    int sweep_passes = 0;
    while (!heap_sweeps) {
        looked.clear();
        heaps_pass_listed();
        sweep_passes++;
        CHECK(sweep_passes < 100);
        uint64_t in_pass = 0;
        for (auto& r : looked) {
            CHECK(r.first < r.second && !(r.first & COLD_UNIT_MASK) && !(r.second & COLD_UNIT_MASK));
            for (auto& x : excl) CHECK(r.second <= x.first || r.first >= x.second);
            const bool inside = (r.first >= listed[0][0] && r.second <= listed[0][1]) ||
                                (r.first >= listed[1][0] && r.second <= listed[1][1]);
            CHECK(inside);
            in_pass += r.second - r.first;
        }
        CHECK(in_pass <= budget);
        total += in_pass;
    }
    const uint64_t want = (150 * MB - 2 * MB) + (1000 * MB - 1 * MB);
    CHECK(total == want);
    // the next pass starts again at the heap's base
    looked.clear();
    heaps_pass_listed();
    CHECK(!looked.empty() && looked[0].first == HOS_HEAP_BASE);
    CHECK(passes == sweep_passes + 1);   // (each pass told the policy it began)
    std::printf("PASS: the listed heap pass looks at the heap and the regions once a sweep (%d passes), never at pools or "
                "stacks, within its budget, with no VirtualQuery\n", sweep_passes);
}
'''])
    with tempfile.TemporaryDirectory(prefix='cold-listed-') as tmp:
        cpp = Path(tmp) / 'fixture.cpp'
        exe = Path(tmp) / 'fixture.exe'
        cpp.write_text(fixture, encoding='utf-8')
        subprocess.run([args.cxx, '-std=c++20', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', str(cpp), '-o', str(exe)],
                       check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    main()
