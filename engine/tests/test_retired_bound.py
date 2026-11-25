# Copyright (C) 2026 Sacrappy

"""Compile and run the rule for which retired imports go back (gpu/vk/retired_bound.h; no game/GPU), and check that
vk_memory.cpp still follows it (issues 131, 121)."""
import argparse
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_source():
    src = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    for needle in ('#include "retired_bound.h"', "retired_bound::plan(bcands, lr, retired_bytes, rec_epoch(), bnd.cfg)",
                   'getenv("RX_VK_RETIRED_BOUND_MB")', "d.not_before = rec_epoch() + bnd.flight;",
                   "rec_epoch() >= dead_imports.front().not_before", "old->retired_seq = rec_epoch();",
                   "uint64_t retired_emergency_free(uint64_t need) {", 'getenv("RX_VK_IMPORT_EMERGENCY")',
                   "import_forget_failure(lo, hi);", 'getenv("RX_VK_RETIRE_STRESS")', "    retire_stress();", 'getenv("RX_VK_IMPORT_REFUSE_MB")',
                   "st_pinned.fetch_add((int64_t)bytes);", "pinned_sub(dead_imports.front().bytes);"):
        if needle not in src:
            raise SystemExit("vk_memory.cpp no longer has: " + needle)
    # (a retired import is only handed to the plan when the GPU is past it and it has no mirror)
    body = src[src.index("void retired_imports_give_back() {"):]
    body = re.sub(r"\s+", " ", body[:body.index("retired_compact();")])
    if ("c.idle = p.mstate == Pool::MIRROR_NONE && !p.inherit_pending && !p.lazy_held && p.retired_value <= mem_completed && "
            "p.last_value <= mem_completed;") not in body:
        raise SystemExit("the plan's idle rule is not the immediate give-back's")
    # (the emergency free frees only what the GPU is past, with no mirror)
    em = src[src.index("uint64_t retired_emergency_free(uint64_t need) {"):]
    em = re.sub(r"\s+", " ", em[:em.index("void retired_imports_give_back()")])
    if "p.retired_value > mem_completed || p.last_value > mem_completed" not in em or "p.mstate != Pool::MIRROR_NONE" not in em:
        raise SystemExit("the emergency free no longer waits for the GPU and the mirror")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="retired-bound-") as tmp:
        exe = Path(tmp) / "retired-bound"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests/test_retired_bound.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
