# SPDX-License-Identifier: GPL-3.0-or-later
"""The GPUs whose task + mesh draws take the compute fallback by default (gpu/vk/mesh_mode.h, gpu/vk/vk_main.cpp).

HeyListen issue 57 (an RX 6700 XT, Navi 22, AMD's Windows driver: the Market dog drawn as a flat pale silhouette on the native
path) and Hookshot issue 996: on AMD's Windows driver the native task + mesh path timed out on Navi 22 and on Navi 21 (an
RX 6800 XT lost the device within minutes at Hyrule Castle's entrance and played 25 minutes there through the fallback). An RX 6600
(Navi 23) lost the device on the session's first task + mesh draws (reported in Mako by sharkiix). Navi 24 is held out: no report,
and 4 GB cards where the fallback's buffers cost the most. This compiles mesh_rule::task_mesh_fallback_default and
task_mesh_fallback_wanted and checks the device IDs, only for AMD and only on Windows, and that Native turns the default off and
RX_VK_TASK_MESH_FALLBACK decides over it; and reads vk_main.cpp for the wiring.

python -I -B tests/test_task_mesh_fallback_devices.py
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
# (device IDs and models: the PCI ID database, pci-ids.ucw.cz, and libdrm's amdgpu.ids)
DEVICES = {
    0x73A1: "Navi 21 (Radeon Pro V620)",
    0x73A2: "Navi 21 (Radeon Pro W6900X)",
    0x73A3: "Navi 21 (Radeon Pro W6800)",
    0x73AB: "Navi 21 (Radeon Pro W6800X, W6800X Duo)",
    0x73AE: "Navi 21 (Radeon Pro V620 MxGPU)",
    0x73BF: "Navi 21 (RX 6800, 6800 XT, 6900 XT)",
    0x73AF: "Navi 21 (RX 6900 XT, XTXH)",
    0x73A5: "Navi 21 (RX 6950 XT)",
    0x73DF: "Navi 22 (RX 6700, 6700 XT, 6750 XT)",
    0x73E0: "Navi 23 (RX 6600M)",
    0x73E1: "Navi 23 (Radeon Pro W6600M)",
    0x73E3: "Navi 23 (Radeon Pro W6600)",
    0x73EF: "Navi 23 (RX 6650 XT)",
    0x73FF: "Navi 23 (RX 6600, 6600 XT, 6600 LE)",
}
# (not in the default: RDNA 1; the included chips' IDs that are no named card (USB 0x73A4, 0x73C4, 0x73E4; unnamed 0x73C3, Navi 22's
# SR-IOV 0x73CE; 0x73DA in neither list); all of Navi 24 (0x743F RX 6400 and 6500 XT, 0x7421 to 0x7424 RX 6300 and Pro W6300 to
# W6500M), held until a Windows run; RDNA 3)
OTHERS = [0x731F, 0x7340, 0x73A4, 0x73C3, 0x73C4, 0x73CE, 0x73DA, 0x73E4, 0x743F, 0x7421, 0x7422, 0x7423, 0x7424, 0x73F0,
          0x744C, 0x7480]

RULE = r"""#include "mesh_mode.h"
#include <cstdio>
#include <cstdlib>
int main(int argc, char** argv) {
    // argv[1] d: groups of vendor device (hex); w: groups of windows(0/1) vendor device choice env ("-": unset)
    const char m = argv[1][0];
    if (m == 'd') {
        for (int i = 2; i + 1 < argc; i += 2)
            std::printf("%d\n", mesh_rule::task_mesh_fallback_default((unsigned)std::strtoul(argv[i], nullptr, 16),
                                                                       (unsigned)std::strtoul(argv[i + 1], nullptr, 16)) ? 1 : 0);
    } else {
        for (int i = 2; i + 4 < argc; i += 5)
            std::printf("%d\n", mesh_rule::task_mesh_fallback_wanted(argv[i][0] == '1', (unsigned)std::strtoul(argv[i + 1], nullptr, 16),
                                                                     (unsigned)std::strtoul(argv[i + 2], nullptr, 16), std::atoi(argv[i + 3]),
                                                                     argv[i + 4][0] == '-' ? nullptr : argv[i + 4]) ? 1 : 0);
    }
    return 0;
}
"""


def check_source():
    main_cpp = (ROOT / "gpu/vk/vk_main.cpp").read_text(encoding="utf-8")
    at = main_cpp.index("bool task_mesh_fallback = mesh_rule::task_mesh_fallback_wanted(")
    block = main_cpp[at:main_cpp.index("dev.mesh_only_fallback = ", at)]
    for need in ("!linux_os", "dev.props.vendorID", "dev.props.deviceID", "mesh_choice.mode", 'getenv("RX_VK_TASK_MESH_FALLBACK")',
                 "dev.task_mesh_fallback = task_mesh_fallback && dev.mesh_shader && f12.bufferDeviceAddress"):
        assert need in block, "vk_main.cpp: %s is missing from the task + mesh fallback's wiring" % need
    shader = (ROOT / "gpu/vk/vk_shader.cpp").read_text(encoding="utf-8")
    assert "dev.uses_mesh_fallback(task != nullptr)" in shader, "mesh_program_locked does not ask per program"
    assert "(dev.task_mesh_fallback ? 4u : 0u)" in shader, "the program cache's stamp does not hold the task + mesh mode"
    assert "(dev.mesh_only_fallback ? 8u : 0u)" in shader, "the program cache's stamp does not hold the mesh-only mode"
    internal = (ROOT / "gpu/vk/vk_internal.h").read_text(encoding="utf-8")
    assert "bool uses_mesh_fallback(bool has_task) const" in internal
    # (longshot 3228cb5f, #1650: Windows Navi 21 to 23 also run mesh-only draws as compute, by the same default rule and switch)
    assert "task_mesh_fallback && (has_task || mesh_only_fallback)" in internal, "a mesh-only program does not follow the RDNA 2 rule"
    assert ("dev.mesh_only_fallback = dev.task_mesh_fallback && !linux_os && "
            "mesh_rule::task_mesh_fallback_default(dev.props.vendorID, dev.props.deviceID);") in main_cpp, "mesh_only_fallback is not the Windows RDNA 2 default"
    assert "(uint64_t)dev.mesh_only_fallback << 8" in (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8"), "the pipeline key does not hold it"


def run(exe, args):
    return subprocess.run([str(exe)] + args, check=True, timeout=60, capture_output=True, text=True).stdout.split()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="task-mesh-devices-") as tmp:
        src = Path(tmp) / "task_mesh_devices.cpp"
        src.write_text(RULE, encoding="utf-8")
        exe = Path(tmp) / "task-mesh-devices"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "gpu" / "vk"), str(src),
                        "-o", str(exe)], check=True)
        cases = [(0x1002, d) for d in DEVICES] + [(0x1002, d) for d in OTHERS] + [(0x10DE, d) for d in DEVICES] + \
            [(0x8086, d) for d in DEVICES]
        argv = ["d"]
        for vendor, device in cases:
            argv += ["%x" % vendor, "%x" % device]
        got = dict(zip(cases, (x == "1" for x in run(exe, argv))))
        for device, name in DEVICES.items():
            assert got[(0x1002, device)], "%s (0x%04x) is not in the default" % (name, device)
            assert not got[(0x10DE, device)] and not got[(0x8086, device)], "0x%04x is in the default for a maker other than AMD" % device
        extra = [hex(d) for d in OTHERS if got[(0x1002, d)]]
        assert not extra, "unexpected devices in the default: %s" % extra
        # (the precedence: only on Windows; Native (mode 1) turns the default off; RX_VK_TASK_MESH_FALLBACK decides over both)
        wanted = []

        def case(windows, vendor, device, mode, env, expect):
            wanted.append(((windows, vendor, device, mode, env), expect))

        for device in (0x73DF, 0x73BF, 0x73FF):   # a 6700 XT, a 6800 XT, a 6600
            case(1, 0x1002, device, 0, "-", True)      # Windows AMD RDNA 2, Automatic: on
            case(1, 0x1002, device, 1, "-", False)     # Native: off
            case(1, 0x1002, device, 0, "0", False)     # =0: native
            case(1, 0x1002, device, 1, "1", True)      # =1 decides over Native
            case(0, 0x1002, device, 0, "-", False)     # Linux: not by default (RADV is the whole compute path there)
            case(0, 0x1002, device, 1, "1", True)      # ... but =1 on Linux AMD with Native: the middle path
            case(1, 0x10DE, device, 0, "-", False)     # NVIDIA with the same ID: off
        case(1, 0x1002, 0x743F, 0, "-", False)         # Navi 24: held out
        case(1, 0x1002, 0x743F, 0, "1", True)          # ... and opt-in
        case(1, 0x10DE, 0x2684, 0, "1", True)          # any GPU with =1
        argv = ["w"]
        for (windows, vendor, device, mode, env), _ in wanted:
            argv += [str(windows), "%x" % vendor, "%x" % device, str(mode), env]
        for ((windows, vendor, device, mode, env), expect), out in zip(wanted, run(exe, argv)):
            assert (out == "1") == expect, "windows=%s vendor=%#x device=%#x mode=%s env=%s: wanted %s" % (
                windows, vendor, device, mode, env, expect)
    print("PASS: task + mesh fallback by default on Windows for AMD's Navi 21 to 23 (%d device IDs), Native and the switch over it"
          % len(DEVICES))


if __name__ == "__main__":
    main()
