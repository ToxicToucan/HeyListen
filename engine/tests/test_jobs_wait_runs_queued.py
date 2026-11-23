# Copyright (C) 2026 Sacrappy

"""A waited-for job still in the queue runs on the waiting thread at once (gpu/vk/vk_jobs.cpp jobs_wait; no game/GPU).

vk_jobs.cpp is compiled beside stand-ins for vk_internal.h and vk_draw.h (logging, the thread calls, ub_thread_off) and
run with every worker held in a job: the draw's pipeline job, queued behind them, must run on the thread that waits.
The source is checked for the same contract's neighbours: jobs pushed with their flags, and the storm hold's release
waiting for the queue to stop being pushed (jobs_last_push_age_frames), not for its depth alone.
python -I -B tests/test_jobs_wait_runs_queued.py
"""
import argparse
import shutil
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STUB_INTERNAL = """#pragma once
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <thread>
using HANDLE = void*;
constexpr int THREAD_PRIORITY_BELOW_NORMAL = -1;
inline HANDLE GetCurrentThread() { return nullptr; }
inline int SetThreadPriority(HANDLE, int) { return 1; }
inline long SetThreadDescription(HANDLE, const wchar_t*) { return 0; }
namespace mkwvk {
inline void logf(const char*, ...) {}
}
"""

STUB_DRAW = """#pragma once
namespace mkwvk {
inline void ub_thread_off() {}
inline void pipeline_cache_between_builds() {}
}
"""


def check_source():
    """The pipeline and translation jobs a draw waits for are pushed with their flags, and jobs_wait still looks for them in the queue"""
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    if "}, false, &e->ready);" not in draw:
        raise SystemExit("graphics_pipeline's build job is pushed without its ready flag")
    shader = (ROOT / "gpu/vk/vk_shader.cpp").read_text(encoding="utf-8")
    if shader.count("&raw->ready);") < 2:
        raise SystemExit("a graphics or mesh translation job is pushed without its ready flag")
    jobs = (ROOT / "gpu/vk/vk_jobs.cpp").read_text(encoding="utf-8")
    wait = jobs[jobs.index("bool jobs_wait("):]
    wait = wait[:wait.index("\n}\n")]
    if "it->flag == &flag" not in wait or 'getenv("RX_VK_JOB_STEAL")' not in wait:
        raise SystemExit("jobs_wait no longer runs a queued job it waits for")
    if "return fresh" not in wait:
        raise SystemExit("jobs_wait no longer tells whether the job ran during the wait")
    if "void jobs_demand(" not in jobs or "int jobs_pending_live(" not in jobs or 'RX_VK_JOB_DEMAND' not in jobs:
        raise SystemExit("the jobs queue lost the demand ordering (RX_VK_JOB_DEMAND) or the live count")
    if draw.count("jobs_demand(cp->ready)") != 1 or draw.count("jobs_demand(pe->ready)") != 1:
        raise SystemExit("a draw left out for want of its program or pipeline no longer asks for the job (jobs_demand)")
    if "void jobs_set_boost(" not in jobs or 'RX_VK_STORM_WORKERS' not in jobs:
        raise SystemExit("the jobs queue lost the storm workers (RX_VK_STORM_WORKERS)")
    if "jobs_set_boost(true)" not in (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8"):
        raise SystemExit("the storm hold's engage no longer turns the extra workers on")
    if "last_push_frame.store" not in jobs or "uint64_t jobs_last_push_age_frames" not in jobs:
        raise SystemExit("the jobs queue no longer tells how long it has gone unpushed")
    hold = (ROOT / "gpu/vk/storm_hold.h").read_text(encoding="utf-8")
    if "quiet_frames" not in hold or "min_frames" not in hold or "StormHoldEvent tick(" not in hold:
        raise SystemExit("the storm hold no longer waits for the churn to stop pushing before it releases, or lost its floor")
    if 'RX_VK_STORM_QUIET_FRAMES' not in hold or 'RX_VK_STORM_MIN_FRAMES' not in hold:
        raise SystemExit("the storm hold's switches are not named beside its rule")
    replay = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    if "storm_hold_tick(jobs_pending(), (int64_t)jobs_last_push_age_frames()," not in replay:
        raise SystemExit("the present path no longer ticks the storm hold with the queue's quiet clock")
    if "storm_hold_draws_held()" not in draw:
        raise SystemExit("the draw path no longer asks the storm hold whether the draws are held")
    if "texture_sample_deferred_take" not in draw or 'RX_VK_DEFER_SKIP' not in draw:
        raise SystemExit("a draw whose sampled texture is held back is no longer left out of its frame")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="jobs-wait-") as tmp:
        tmp = Path(tmp)
        for name in ("vk_jobs.cpp", "vk_jobs.h"):
            shutil.copy(ROOT / "gpu/vk" / name, tmp / name)
        (tmp / "vk_internal.h").write_text(STUB_INTERNAL, encoding="utf-8")
        (tmp / "vk_draw.h").write_text(STUB_DRAW, encoding="utf-8")
        exe = tmp / "jobs-wait"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-pthread", "-I", str(tmp),
                        str(tmp / "vk_jobs.cpp"), str(ROOT / "tests/test_jobs_wait_runs_queued.cpp"), "-o", str(exe)],
                       check=True)
        subprocess.run([str(exe)], check=True, timeout=60, env={**os.environ, "RX_VK_JOB_STEAL": "1"})
        boost = tmp / "jobs-boost"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-pthread", "-I", str(tmp),
                        str(tmp / "vk_jobs.cpp"), str(ROOT / "tests/test_jobs_boost.cpp"), "-o", str(boost)], check=True)
        subprocess.run([str(boost)], check=True, timeout=60,
                       env={**os.environ, "RX_VK_WORKERS": "1", "RX_VK_STORM_WORKERS": "4", "RX_VK_JOB_STEAL": "0"})
        # (RX_VK_JOB_DEMAND: one worker, so the queue's order is the run order)
        demand = tmp / "jobs-demand"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-pthread", "-I", str(tmp),
                        str(tmp / "vk_jobs.cpp"), str(ROOT / "tests/test_jobs_demand.cpp"), "-o", str(demand)], check=True)
        subprocess.run([str(demand)], check=True, timeout=60,
                       env={**os.environ, "RX_VK_JOB_DEMAND": "1", "RX_VK_WORKERS": "1", "RX_VK_JOB_STEAL": "0"})


if __name__ == "__main__":
    main()
