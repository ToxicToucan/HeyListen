# Copyright (C) 2026 Sacrappy

"""A start whose noted pipelines were compiled, not found, writes the pipeline cache when the precompile ends (gpu/vk/precompile_save.h).

In play the pipeline cache is written only once it has grown 64 MB. A Steam Deck start compiled its 32,438 noted pipelines for 20
minutes under keys the cache did not have and grew it by 4.3 MB (RADV keeps a pipeline as its key and its shaders' hashes; the
code was already there): nothing was written, and the next start had only the driver's own folder. This checks precompile_save.h
(tests/precompile_save_test.cpp) and the wiring in vk_draw.cpp: precompile_finished writes the cache when it is due.
(Ported from Longshot f9a2195c, #903; adapted: this tree's precompile has no warm pass or "built" marker, and its forced save
records no outcome.)
python -I -B tests/test_precompile_save.py [--cxx c++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import block   # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]


def check_sources():
    draw = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    if '#include "precompile_save.h"' not in draw:
        return ["vk_draw.cpp does not include precompile_save.h"]
    problems = []
    fin = block(draw, "void precompile_finished(Pre* pre) {")
    if "precompile_save::due(" not in fin or 'getenv("RX_VK_PRECOMPILE_SAVE_S")' not in fin:
        problems.append("precompile_finished does not decide the save with precompile_save::due and RX_VK_PRECOMPILE_SAVE_S")
    elif fin.index("precompile_save::due(") > fin.index("pipeline_cache_save(true)"):
        problems.append("precompile_finished saves before it asks whether the save is due")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "c++"))
    args = parser.parse_args()
    cxx = shutil.which(args.cxx)
    if not cxx:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    problems = check_sources()
    with tempfile.TemporaryDirectory(prefix="precompile-save-") as folder:
        exe = Path(folder) / "t"
        r = subprocess.run([cxx, "-std=c++17", "-O0", "-Wall", "-Wextra", "-Werror", str(ROOT / "tests" / "precompile_save_test.cpp"),
                            "-o", str(exe)], capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise SystemExit("FAIL: precompile_save_test.cpp does not compile\n" + r.stdout + r.stderr)
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        print(r.stdout, end="")
        if r.returncode:
            problems.append("precompile_save_test failed")
    if problems:
        raise SystemExit("FAIL:\n  " + "\n  ".join(problems))
    print("PASS: a long precompile writes the pipeline cache when it ends")


if __name__ == "__main__":
    main()
