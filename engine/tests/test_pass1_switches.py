# Copyright (C) 2026 Sacrappy

"""PASS1's switches, all off by default: RX_VK_VRS (vk_vrs.h's selection rule, compiled here, and the wiring in the sources),
RX_VK_LOAD_CLEAR and RX_VK_SHADOW_SHIFT (the wiring: what each one reads, where it acts, that off changes nothing).

python -I -B tests/test_pass1_switches.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srccut import block   # noqa: E402

RUN_ALL_ARGS = ["--cxx", "clang++"]
ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''#include <cstdio>
#include "vk_vrs.h"
using namespace mkwvk;
static int fails;
#define CHECK(name, cond) do { if (!(cond)) { std::printf("FAIL: %s (line %d)\n", name, __LINE__); fails++; } } while (0)
int main() {
    CHECK("no text selects nothing", !vrs::parse(nullptr).any() && !vrs::parse("").any());
    const vrs::Rule r = vrs::parse("1a7482c0,2560x1440,ABCDEF12");
    CHECK("three items", r.progs.size() == 2 && r.sizes.size() == 1 && !r.all);
    CHECK("a program's prefix", vrs::select(r, 0x1a7482c0deadbeefull, 2560, 1440, 1, true, false, 1));
    CHECK("upper case hex is read", vrs::select(r, 0xabcdef1200000000ull, 2560, 1440, 1, true, false, 1));
    CHECK("another program is not", !vrs::select(r, 0x1a7482c1deadbeefull, 2560, 1440, 1, true, false, 1));
    CHECK("a size: a colour pass without depth", vrs::select(r, 5, 2560, 1440, 1, false, false, 1));
    CHECK("a size with depth is not", !vrs::select(r, 5, 2560, 1440, 1, true, false, 1));
    CHECK("another size is not", !vrs::select(r, 5, 1280, 720, 1, false, false, 1));
    CHECK("a draw that writes depth stays 1x1", !vrs::select(r, 0x1a7482c0deadbeefull, 2560, 1440, 1, true, true, 1));
    CHECK("a multisampled target stays 1x1", !vrs::select(r, 0x1a7482c0deadbeefull, 2560, 1440, 1, true, false, 4));
    CHECK("a narrow target stays 1x1", !vrs::select(r, 0x1a7482c0deadbeefull, 32, 32, 1, false, false, 1));
    const vrs::Rule all = vrs::parse("all");
    CHECK("all: any ordinary draw", vrs::select(all, 1, 1920, 1080, 1, true, false, 1) && !vrs::select(all, 1, 1920, 1080, 1, true, true, 1));
    CHECK("RX_VK_VRS_MIN_W", vrs::select(vrs::parse("all", 16), 1, 32, 32, 1, false, false, 1));
    if (!fails) std::puts("PASS: vk_vrs.h picks the draws");
    return fails ? 1 : 0;
}
'''


def wiring():
    fails = []

    def need(cond, what):
        if not cond:
            fails.append(what)

    draw = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    tex = (ROOT / "gpu" / "vk" / "vk_texture.cpp").read_text(encoding="utf-8")
    main = (ROOT / "gpu" / "vk" / "vk_main.cpp").read_text(encoding="utf-8")
    dyn = block(draw, "void dynamic_state(VkCommandBuffer cmd, const NvnState& s, const Targets& at, VkPrimitiveTopology topo, bool mesh)")
    need("if (dev.frag_shading_rate) {" in dyn and "vkCmdSetFragmentShadingRateKHR" in dyn, "dynamic_state sets the rate only with the feature on")
    need(dyn.index("vkCmdSetFragmentShadingRateKHR") < dyn.index("dyn_pack("), "the rate is set before the dynamic state's fast path")
    need("VK_DYNAMIC_STATE_FRAGMENT_SHADING_RATE_KHR" in draw and "if (dev.frag_shading_rate) dyn.push_back(" in draw, "the pipelines get the dynamic state only with the feature")
    need("if (vrs::wanted()) optional(VK_KHR_FRAGMENT_SHADING_RATE_EXTENSION_NAME" in main, "the extension is enabled only with RX_VK_VRS set")
    need("pipelineFragmentShadingRate" in main and "attachmentFragmentShadingRate = VK_FALSE" in main, "only the pipeline rate is turned on")
    need("last_rate = -1, last_dyn_valid = false" in draw, "the rate is set again where the dynamic state is")
    need(block(draw, "bool load_clear_on()").count('static const bool v') == 1, "RX_VK_LOAD_CLEAR is read once")
    cc = block(draw, "void clear_color_target(")
    need("mask == 0xF && rect.offset.x == 0" in cc and "!pass_continues(at)" in cc, "a colour clear becomes a loadOp only when full, first in a new pass")
    need("loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR" in block(draw, "void begin_pass(VkCommandBuffer cmd, const Targets& at)"), "begin_pass takes the clear")
    need("aspect == all_aspects" in block(draw, "void clear_depth_stencil("), "a depth clear becomes a loadOp only when it covers every aspect")
    need('getenv("RX_VK_SHADOW_SHIFT")' in tex and "x.shrink = shift;" in tex and "d.width >= 8192" in tex, "the shadow map shrinks only by RX_VK_SHADOW_SHIFT, from 8192 texels")
    need("if (tex.shrink && tex.image)" in block(tex, "bool upload_inner(Texture& tex, const Ranges* dirty) {"), "a shrunk map gets no upload of the guest's storage")
    need("at.n == 0 && at.depth ? at.depth->shrink : 0" in draw, "the viewport and scissor shrink with the map")
    return fails


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="clang++")
    a = p.parse_args()
    fails = wiring()
    for f in fails:
        print("FAIL: " + f)
    if fails:
        raise SystemExit("test_pass1_switches: failed")
    print("PASS: the switches are wired and off by default")
    cxx = shutil.which(a.cxx)
    if not cxx:
        raise SystemExit("no C++ compiler %r (pass --cxx)" % a.cxx)
    with tempfile.TemporaryDirectory(prefix="pass1-", dir=os.environ.get("PASS1_TMP")) as temp:
        d = Path(temp)
        src, exe = d / "fixture.cpp", d / "fixture.exe"
        src.write_text(FIXTURE, encoding="utf-8")
        subprocess.run([cxx, "-std=c++17", "-O2", "-static", "-I", str(ROOT / "gpu" / "vk"), str(src), "-o", str(exe)], check=True)
        result = subprocess.run([str(exe)], capture_output=True, text=True)
        print(result.stdout, end="")
        if result.returncode:
            raise SystemExit("test_pass1_switches: failed")


if __name__ == "__main__":
    main()
