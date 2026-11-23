"""Compile and run the order thread's gap attribution (gpu/vk/feed_stats.h, stream ORDER3; no game/GPU required)."""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_source():
    """The replay still takes the host times and logs the split, and the pool still looks for work before it sleeps (ORDER3)"""
    src = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    for needle in ('#include "feed_stats.h"', 'getenv("RX_VK_FEED_LOG")', "feed::attribute(", 'getenv("RX_VK_POOL_SPIN")',
                   "spin_before_sleep(", "par_pool.posted.fetch_add("):
        if needle not in src:
            raise SystemExit("vk_replay.cpp no longer has: " + needle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="feed-stats-") as tmp:
        exe = Path(tmp) / "feed-stats"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pthread",
                        str(ROOT / "tests/test_feed_stats.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
