# Copyright (C) 2026 Sacrappy
"""Compile and run the low-end rules (gpu/vk/lowend_rule.h; no game/GPU): the volumetric fog's programs, the Volumetric fog row,
and the first-start rule that gives an integrated GPU without mesh shaders the Minimum preset with the fog off."""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def need(path, needles):
    src = (ROOT / path).read_text(encoding="utf-8")
    for needle in needles:
        if needle not in src:
            raise SystemExit(path + " no longer has: " + needle)


def check_source():
    """The dispatch leaves the fog's programs out when the fog is off; the menu has the row, the rule and its options-file line;
    the device setup asks the rule once the mesh shaders are known"""
    need("gpu/vk/vk_draw.cpp", ['#include "lowend_rule.h"', "if (lowend_rule::fog_program(cp->key) && !fog_runs()) {"])
    need("gpu/vk/vk_menu.cpp", ['{TAB_GRAPHICS, "Volumetric fog", &opts.fog, 0, 1, 1, tx_onoff,',
                                'lowend_rule::fog_on(env_int("RX_VK_FOG"), opts.fog)',
                                'lowend_rule::first_start(env_int("HL_LOWEND"), lowend_marked, gpu)',
                                'if (!strcmp(line, "lowend_rule")) lowend_marked = true;',
                                'key == "lowend_rule";', 'if (lowend_marked) put("lowend_rule=1\\n");',
                                "opts.resolution = ur::snap(upscaler(), lowend_rule::kResolution);",
                                "opts.handheld = lowend_rule::kHandheld;\n    opts.fog = lowend_rule::kFog;",
                                "static void ini_put_only(", 'fopen(ini_path().c_str(), "rb")', "lowend_rule::ini_put_text(in, keys);", "{\"volumetric_fog\", opts.fog}, {\"lowend_rule\", 1}});",
                                "lowend_mirror = lowend_rule::mirror_not_wait(vendor_id, integrated, mesh_shader);"])
    src = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    body = src[src.index("void menu_first_run_lowend("):]
    body = body[:body.index("\n}\n")]
    if "apply_preset" in body or "save();" in body:
        raise SystemExit("the low-end rule must write its own rows only (no preset, no whole save)")
    need("gpu/vk/vk_memory.cpp", ["discrete || lowend_mirror_not_wait();"])
    need("gpu/vk/vk_replay.cpp", ["!lowend_mirror_not_wait());   // (LAPTOP1: the mirror in its place there)"])
    src = (ROOT / "gpu/vk/vk_main.cpp").read_text(encoding="utf-8")
    at = src.find("menu_first_run_lowend(dev.props.vendorID, dev.props.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU, dev.mesh_shader);")
    mesh = src.find("dev.mesh_shader = dev.mesh_shader && mesh.meshShader && mesh.taskShader;")
    if at < 0 or mesh < 0 or at < mesh:
        raise SystemExit("vk_main.cpp: the low-end rule must be asked after the mesh shaders are known")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="lowend-rule-") as tmp:
        exe = Path(tmp) / "lowend-rule"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests/test_lowend_rule.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
