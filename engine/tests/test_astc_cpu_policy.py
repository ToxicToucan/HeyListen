# Copyright (C) 2026 Sacrappy
"""Compile and run the rule for who decodes ASTC on the CPU by default (gpu/vk/astc_cpu_policy.h; no game/GPU).
Issue 127: AMD integrated GPUs (Radeon 890M) on Windows."""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_source():
    """The texture setup still asks the rule, with the device type, and logs when it applies"""
    src = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    for needle in ('#include "astc_cpu_policy.h"', "astc_cpu_policy::by_default(windows, vendor_id, device_id, integrated)",
                   "dev.props.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU);",
                   'logf("vk: ASTC decoded on the CPU'):
        if needle not in src:
            raise SystemExit("vk_texture.cpp no longer has: " + needle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="astc-cpu-policy-") as tmp:
        exe = Path(tmp) / "astc-cpu-policy"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests/test_astc_cpu_policy.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
