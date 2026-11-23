# Copyright (C) 2026 Sacrappy

"""HL_REGION_KEEP (host/hos_regionkeep.c): a freed address region kept resident, zeroed when a new region takes its address space, discarded
when it ages or over the cap (tests/region_keep_test.c, the same source built with HK_TEST against a small window). Also the shape of the
wiring in hos_mem.c (kept at hos_region_free, zeroed in hos_region_alloc, off by default). Windows only for the run: skipped elsewhere."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    args = parser.parse_args()
    mem = (ROOT / "host/hos_mem.c").read_text(encoding="utf-8")
    for part in ("hos_keep_try(freed, freed_size)", "hos_keep_reuse(found, found + size)", "void hos_keep_discard("):
        if part not in mem:
            raise SystemExit("FAIL: hos_mem.c lacks: " + part)
    keep = (ROOT / "host/hos_regionkeep.c").read_text(encoding="utf-8")
    if 'getenv("HL_REGION_KEEP")' not in keep or "keep_secs = e && e[0] ? (atoi(e) > 0 ? atoi(e) : 0) : 20;" not in keep:
        raise SystemExit("FAIL: HL_REGION_KEEP is not 20 s by default (0 off)")
    if os.name != "nt":
        print("skipped: Windows only")
        return 0
    with tempfile.TemporaryDirectory(prefix="hookshot-regionkeep-") as temp:
        exe = Path(temp) / "region_keep_test.exe"
        subprocess.run([args.cc, "-std=gnu11", "-O1", "-Wall", "-Wextra", "-DHK_TEST", "-I" + str(ROOT / "tests"), "-I" + str(ROOT / "host"),
                        str(ROOT / "tests/region_keep_test.c"), str(ROOT / "host/hos_regionkeep.c"), "-o", str(exe)], check=True)
        r = subprocess.run([str(exe)], timeout=120, capture_output=True, text=True)
        sys.stdout.write(r.stdout[-1500:])
        return r.returncode


if __name__ == "__main__":
    sys.exit(main())
