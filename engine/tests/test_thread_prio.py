# Copyright (C) 2026 Sacrappy

"""RX_VK_THREAD_PRIO: the background compile threads kept off the replay's core (gpu/vk/thread_prio.h) and the call sites (no game/GPU).

thread_prio.h is built on its own and run at modes 0 and 1. The source is checked: the in-play compile workers carry the pin, the jobs workers
do not, no time-critical level, the start line, the affinity call.
python -I -B tests/test_thread_prio.py [--cxx g++]
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def check_source():
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    jobs = (ROOT / "gpu/vk/vk_jobs.cpp").read_text(encoding="utf-8")
    main = (ROOT / "gpu/vk/vk_main.cpp").read_text(encoding="utf-8")
    header = (ROOT / "gpu/vk/thread_prio.h").read_text(encoding="utf-8")
    for name in ("vk_draw.cpp", "vk_jobs.cpp", "vk_replay.cpp", "vk_main.cpp", "vk_memory.cpp"):
        if "THREAD_PRIORITY_TIME_CRITICAL" in (ROOT / "gpu/vk" / name).read_text(encoding="utf-8"):
            raise SystemExit("a renderer thread is time-critical: " + name)
    if "MKWVK_PIN_BACKGROUND" in jobs or "thread_prio" in jobs:
        raise SystemExit("the jobs workers run work a frame waits for: they are not pinned off the replay's core (matrix98: -1% warm)")
    if draw.count("if (background) MKWVK_PIN_BACKGROUND();") != 4 or draw.count("if (!wait) MKWVK_PIN_BACKGROUND();") != 1:
        raise SystemExit("the in-play compile workers (state list x2, prepare, prediction, precompile) lost their pin")
    if 'logf("vk: thread priorities: %s"' not in main or "thread_prio_init();" not in main:
        raise SystemExit("the start line for the thread priorities is gone")
    if "SetThreadAffinityMask(GetCurrentThread(), (DWORD_PTR)mask)" not in main or "host->reserved_cpus" not in main:
        raise SystemExit("the compile threads are no longer kept off the replay's core")
    if "RX_VK_THREAD_PRIO" not in header:
        raise SystemExit("the switch is not named in its header")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="thread-prio-") as tmp:
        tmp = Path(tmp)
        exe = tmp / "thread-prio"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-I", str(ROOT / "gpu/vk"),
                        str(ROOT / "tests/test_thread_prio.cpp"), "-o", str(exe)], check=True)
        for mode in ("0", "1"):
            subprocess.run([str(exe), mode], check=True, timeout=60, env={**os.environ, "RX_VK_THREAD_PRIO": mode})


if __name__ == "__main__":
    main()
