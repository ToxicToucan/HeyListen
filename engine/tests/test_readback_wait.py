# Copyright (C) 2026 Sacrappy

"""v0.4.4: what a CPU read of GPU results waited for, said in the hitch rows (gpu/vk/readback_wait.h), and the bounded strict wait
on the light meter's page (RX_VK_STRICT_BOUND, RX_VK_STRICT_BOUND_MS), with no game and no GPU.

The header is compiled with a small program: the labels, the phase a read waited in longest ("cut" when the bounded strict wait ran
out), and the rule that bounds a strict wait: only with the meter's own zeroing and hold on (RX_VK_METER_ZERO + RX_VK_METER_HOLD),
for the 0x1040 page the meter's programs bound (the others, and the 0xc0 ranges unless RX_VK_STRICT_BOUND=2, wait as before).
And, by the text, that invalidate() times the phases, bounds only through strict_bounded_for, keeps the unbounded wait_value for
every other range, labels a wait of 1 ms or more, and that the summary asks for the worst read once a frame.

python -I -B tests/test_readback_wait.py --cxx <compiler>
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]

CPP = r'''
#include "readback_wait.h"
#include <cstdio>
#include <cstring>
static int fails;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
int main() {
    using namespace readback_wait;
    char b[64];
    label(b, sizeof b, kStrict, 0x1040, true, 0, 0x1000);
    CHECK(!strcmp(b, "strict:1040:meter pool 1000"));
    label(b, sizeof b, kRecorded, 0x2000, false, 0x80364080, 0);
    CHECK(!strcmp(b, "rec:2000@80364080"));
    label(b, sizeof b, kCut, 0xc0, false, 0, 0);
    CHECK(!strcmp(b, "cut:c0"));
    char tiny[8];
    label(tiny, sizeof tiny, kGpu, 0x1b580, false, 0x1234, 0x99);   // (cut by the buffer: no overrun, always terminated)
    CHECK(strlen(tiny) < sizeof tiny);
    int64_t ns[kPhases] = {5, 9, 3, 0, 0};
    CHECK(longest(ns, false) == kGpu);
    int64_t st[kPhases] = {5, 1, 9, 0, 0};
    CHECK(longest(st, false) == kStrict && longest(st, true) == kCut);
    int64_t gp[kPhases] = {5, 9, 1, 0, 0};
    CHECK(longest(gp, true) == kGpu);   // (a cut that did not wait longest in the strict wait is still the GPU's)
    int64_t none[kPhases] = {};
    CHECK(longest(none, false) == kRecorded);
    CHECK(bound_mode(nullptr) == 1 && bound_mode("") == 1 && bound_mode("0") == 0 && bound_mode("2") == 2);
    CHECK(bound_limit_ms(nullptr) == 4.0 && bound_limit_ms("2.5") == 2.5 && bound_limit_ms("-3") == 0 && bound_limit_ms("5000") == 1000);
    // the rule: the meter page bounded with the zero and hold on; nothing else
    CHECK(strict_bounded(1, 4, true, true, 0x1040, true));
    CHECK(!strict_bounded(1, 4, true, true, 0x1040, false));      // (a 0x1040 range that is not the meter's page)
    CHECK(!strict_bounded(1, 4, true, false, 0x1040, true));      // (no hold: a zero read is not answered)
    CHECK(!strict_bounded(1, 4, false, true, 0x1040, true));      // (no zeroing: a late write-back doubles the sums)
    CHECK(!strict_bounded(0, 4, true, true, 0x1040, true));       // (RX_VK_STRICT_BOUND=0)
    CHECK(!strict_bounded(1, 0, true, true, 0x1040, true));       // (RX_VK_STRICT_BOUND_MS=0)
    CHECK(!strict_bounded(1, 4, true, true, 0xc0, false) && !strict_bounded(1, 4, true, true, 0x1b580, true));
    CHECK(strict_bounded(2, 4, true, true, 0xc0, false) && !strict_bounded(2, 4, true, true, 0x2000, true));
    if (!fails) std::printf("PASS the labels and the rule\n");
    return fails;
}
'''


def function(source, marker):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


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
    inv = function(rp, "void invalidate(uint64_t gpu, uint64_t size) {")
    sb = function(rp, "bool strict_bounded_for(uint64_t size, uint64_t gpu) {")
    assert 'getenv("RX_VK_STRICT_BOUND")' in sb and 'getenv("RX_VK_STRICT_BOUND_MS")' in sb, "the switches are not read"
    assert "meter_zero_on()" in sb and "meter_hold_on()" in sb and "memory_is_meter(host->gpu_guest(gpu))" in sb, \
        "the bound does not need the zeroing, the hold and the meter's page"
    i_strict = inv.index("if (strict_bounded_for(size, gpu))")
    i_unb = inv.index("wait_value(v);", i_strict)
    assert inv.index("wait_value_bounded(v)", i_strict) < i_unb and "} else {" in inv[i_strict:i_unb], \
        "the other ranges no longer take the unbounded wait"
    assert "(readback_strict_wanted(size) || readback_strict_slow(size)) && v > completed()" in inv, "the strict wait's condition changed"
    assert "readback_wait::longest(phase_ns, cut)" in inv and "rb_worst_note(d, lab)" in inv and "d >= 1000000" in inv, \
        "a wait of 1 ms or more is not labelled"
    bounded = function(rp, "bool wait_value_bounded(uint64_t v) {")
    assert "vkWaitSemaphores(dev.device, &wi, (uint64_t)(ms * 1e6))" in bounded and "UINT64_MAX" not in bounded, "the wait is not bounded"
    assert "int64_t readback_worst_take(char* label, size_t cap)" in rp and "rb_worst_ns = 0;" in rp
    rep = (ROOT / "gpu/vk/perf_report.cpp").read_text(encoding="utf-8")
    assert "readback_worst_take(label, sizeof label) > 0 && w.readback > 0" in rep and "w.readback_which = label" in rep, \
        "the summary does not take the worst read each frame"
    ph = (ROOT / "gpu/vk/perf_report.h").read_text(encoding="utf-8")
    assert "int64_t readback_worst_take(char* label, size_t cap);" in ph
    print("PASS the wiring")


if __name__ == "__main__":
    main()
