# Copyright (C) 2026 Sacrappy

"""Compile and run the checked write-back of a guessed range (gpu/vk/wb_guess.h; no game/GPU) and check that vk_memory.cpp and
vk_draw.cpp use it: a bare storage entry's head goes through staging and is written, once its submission is done, only the words
the GPU changed and the CPU did not since the binding (CRASH3: the v0.4.3 module-job destroy-path crashes)."""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_source():
    mem = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    for needle in ('#include "wb_guess.h"',
                   'getenv("RX_VK_WB_GUESS_CHECKED")',
                   "tl_bind_guess && wb_guess_checked_on()",
                   "std::stable_partition(writebacks.begin(), writebacks.end(), [](const WriteBack& w) { return !w.guess; })",
                   "if (tl_bind_guess && wb_guess_checked_on()) guess_snap_take(p, off, std::min(off + size, p->hi - p->lo));",
                   "kStagedChecked | so",
                   "if (check & kStagedChecked) {",
                   "wb_guess::gpu_words(base + a, src, snap, known, e - a,"):
        if needle not in mem:
            raise SystemExit("vk_memory.cpp no longer has: " + needle)
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    if "(void)gpu_buffer_bind_guess(base, (head + 3) & ~3ull);" not in draw:
        raise SystemExit("vk_draw.cpp no longer binds a bare entry's head as a guess")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="wb-guess-") as tmp:
        exe = Path(tmp) / "wb-guess.exe"
        subprocess.run([args.cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests/test_wb_guess.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
