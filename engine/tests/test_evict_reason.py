# Copyright (C) 2026 Sacrappy

"""Compile and run the eviction pass's rules (gpu/vk/evict_reason.h; no game/GPU), and check the pass still asks them."""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_source():
    """The eviction pass still leaves out the write-back of a texture another image was drawn over since"""
    src = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    for needle in ('#include "evict_reason.h"',
                   "if (e.flush) e.flush = !evict_reason::write_back_stale(e.tex->last_written, other[i++]);"):
        if needle not in src:
            raise SystemExit("vk_texture.cpp no longer has: " + needle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="evict-reason-") as tmp:
        exe = Path(tmp) / "evict-reason"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests/test_evict_reason.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
