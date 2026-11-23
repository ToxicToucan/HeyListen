"""Compile and run the order thread's completion tracker (gpu/vk/completion_tracker.h, stream ORDER2; no game/GPU required)."""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_source():
    """The replay still hands its submits to the tracker, and recording_wait still asks it"""
    src = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    for needle in ('#include "completion_tracker.h"', 'getenv("RX_VK_COMPLETION_THREAD")', 'getenv("RX_VK_COMPLETION_CHECK")',
                   "if (r.submitted && comp_on()) r.chk_seq = seq, comp_submit(r);",
                   "if (r.submitted && comp_on()) comp_submit(r);", "if (j->r->submitted && comp_on()) comp_submit(*j->r);",
                   "comp_submit(owner, ms, (int)gn);", "if (r.comp_tracked) {"):
        if needle not in src:
            raise SystemExit("vk_replay.cpp no longer has: " + needle)
    if src.count("comp_submit(") < 6:
        raise SystemExit("vk_replay.cpp: fewer submit sites hand over to the completion thread than before")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="completion-tracker-") as tmp:
        exe = Path(tmp) / "completion-tracker"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pthread",
                        str(ROOT / "tests/test_completion_tracker.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
