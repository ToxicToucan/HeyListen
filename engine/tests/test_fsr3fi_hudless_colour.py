# Copyright (C) 2026 Sacrappy

"""AMD FSR 3.1 frame generation interpolates the picture from before the HUD (RX_VK_FSR_FG_HUDLESS), step 2 of the HUD-less chain.

With the picture captured at the gameplay upscale (test_fsr3fi_hud_capture.py), FSR 3.1 is given AMD's HUD-less mode: `color` is the
picture before the HUD, `present` the frame as presented, `hudless_attached_factor` is 1, and the inpainting pass's present
backbuffer (binding 2) is the presented frame, which it lays over the made one wherever the two differ. That mode only works if the
HUD-less colour is the presented frame's to the bit wherever nothing was drawn over the picture, and the same picture encoded as the
frame is under the HUD: the picture is linear light (B10G11R11), the frame sRGB-encoded (RGBA8); taken as it was, the two
differed everywhere and every made frame came out as the game's own frame (Longshot d664e3428, #1538, HumanBean; the mode itself
is 310e7c9df, #1487; ported by behaviour). shaders/fg_hud.comp does that, per pixel.

This check
  - compiles `hudless_colour` (and `srgb`) cut out of the shader as C++: no HUD gives the presented pixel bit for bit, the HUD gives
    the sRGB-encoded copy clamped as the RGBA8 frame is, never the linear copy;
  - makes fg_hud_comp_spv.h from the shader (--write), and with glslang compares the header with its output byte for byte;
  - reads the wiring: the shader's bindings and push constants against vk_framegen.cpp's pipeline, FSR 3.1's inputs (`present`, the
    factor, the history reset when the kind of source changes, binding 2 of the inpainting pass).

    python -I -B tests/test_fsr3fi_hudless_colour.py [--cxx g++] [--glslang glslangValidator] [--write]
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # (python -I leaves the check's own folder off the path)
from srccut import block  # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
SHADER = ROOT / "gpu" / "vk" / "shaders" / "fg_hud.comp"
HEADER = ROOT / "gpu" / "vk" / "shaders" / "fg_hud_comp_spv.h"
VAR, ENV = "FG_HUD_COMP_SPV", "vulkan1.3"

PREFIX = r'''
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
// (just enough GLSL for the shader's functions, compiled as they are written)
struct vec3 {
    float r, g, b;
    vec3(float x, float y, float z) : r(x), g(y), b(z) {}
    bool operator==(const vec3& o) const { return r == o.r && g == o.g && b == o.b; }
    bool operator!=(const vec3& o) const { return !(*this == o); }
};
static float glsl_clamp(float v, float lo, float hi) { return std::min(std::max(v, lo), hi); }
static float glsl_pow(float a, float b) { return std::pow(a, b); }
#define clamp glsl_clamp
#define pow glsl_pow
'''

CASES = r'''
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); std::exit(1); } } while (0)
#undef pow
static float ref(float v) { v = std::min(std::max(v, 0.0f), 1.0f); return v <= 0.0031308f ? 12.92f * v : 1.055f * std::pow(v, 1.0f / 2.4f) - 0.055f; }
int main() {
    // (nothing drawn over the picture here: the presented pixel, bit for bit, whatever the picture's encoding makes of it)
    const vec3 shown(0.2f, 0.5019608f, 0.7f);
    const vec3 same(0.0001f, 0.2158605f, 0.4f);
    vec3 r = hudless_colour(same, same, shown);
    CHECK(std::memcmp(&r, &shown, sizeof r) == 0);
    // (the HUD: the copy before it, encoded as the frame is, not the linear copy)
    const vec3 scene(0.5f, 0.002f, 0.0f), drawn(0.9f, 0.9f, 0.9f);
    r = hudless_colour(scene, drawn, shown);
    CHECK(std::fabs(r.r - ref(0.5f)) < 1e-6f && std::fabs(r.g - ref(0.002f)) < 1e-6f && r.b == 0.0f);
    CHECK(r.r > 0.7f && r.r != scene.r);   // (0.5 linear is 0.735 encoded: the linear copy was far darker)
    CHECK(std::fabs(r.g - 12.92f * 0.002f) < 1e-6f);   // (the linear segment)
    // (clamped as the RGBA8 frame is)
    r = hudless_colour(vec3(4.0f, -1.0f, 1.0f), drawn, shown);
    CHECK(r.r == 1.0f && r.g == 0.0f && std::fabs(r.b - 1.0f) < 1e-6f);
    // (a faint HUD pixel whose copy and picture differ by one step still takes the encoded copy, not the presented pixel)
    r = hudless_colour(vec3(0.25f, 0.25f, 0.25f), vec3(0.2501f, 0.25f, 0.25f), shown);
    CHECK(std::fabs(r.r - ref(0.25f)) < 1e-6f);
    std::puts("hudless_colour: ok");
    return 0;
}
'''


def source_line(comp):
    digest = hashlib.sha256(comp.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return ("// %s sha256 %s (tests/test_fsr3fi_hudless_colour.py --write: glslangValidator -V --target-env %s --vn %s)\n"
            % (comp.name, digest, ENV, VAR)).encode()


def compile_header(glslang, comp, out):
    r = subprocess.run([glslang, "-V", "--target-env", ENV, "--vn", VAR, "-o", str(out), str(comp)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("%s does not compile:\n%s%s" % (comp.name, r.stdout, r.stderr))
    return out.read_bytes()


def check_wiring():
    shader = SHADER.read_text(encoding="utf-8").replace("\r\n", "\n")
    for need in ("layout(binding = 0) uniform sampler2D frame;", "layout(binding = 1) uniform sampler2D before;",
                 "layout(binding = 2) uniform sampler2D after;", "layout(binding = 3, rgba8) uniform writeonly image2D hudless;",
                 "layout(push_constant) uniform P { ivec2 offset; ivec2 size; } pc;",
                 "imageStore(hudless, g, vec4(hudless_colour(scene, drawn, shown), 1.0));"):
        assert need in shader, "fg_hud.comp: %r is missing" % need
    fg = (ROOT / "gpu/vk/vk_framegen.cpp").read_text(encoding="utf-8")
    assert '#include "shaders/fg_hud_comp_spv.h"' in fg
    assert "make_pipe(hud.mask, FG_HUD_COMP_SPV, sizeof FG_HUD_COMP_SPV, {S, S, S, I}, 16)" in fg, "framegen: the pass's bindings and push"
    record = block(fg, "bool hud_source(")
    assert "const int32_t pc[4] = {x0, y0, (int32_t)w, (int32_t)h};" in record
    assert ("{{fg.frames[cur].view, fg.nearest}, {hud.before[k].view, fg.nearest}, {hud.after[k].view, fg.nearest},\n"
            "          {hud.hudless[k].view, VK_NULL_HANDLE}}") in record, "hud_source: frame, before, after, then the HUD-less image"
    assert "VK_FORMAT_R8G8B8A8_UNORM" in record and "VK_IMAGE_USAGE_TRANSFER_SRC_BIT" in record   # (FSR keeps it as its previous source)
    assert "!hud_image(cmd, hud.after[k], sw, sh, hud.format" in record and "vkCmdCopyImage(cmd, hud.picture" in record, \
        "hud_source copies the picture as presented"
    frame = block(fg, "int fsr3fi_frame(")
    assert "in.color = hud.hudless[hud.hl].view, in.color_image = hud.hudless[hud.hl].image;" in frame, "fsr3fi_frame: the HUD-less colour is FSR's colour"
    assert "in.present = layered ? hud.hudless[hud.hl].view : fg.frames[cur].view;" in frame,         "fsr3fi_frame: the presented frame is FSR's present (the HUD-less colour with the HUD layer, which lays the HUD itself)"
    assert "hud.ready && hudless_wanted()" in frame, "fsr3fi_frame: only with the picture, only when asked for"
    fi = (ROOT / "gpu/vk/vk_fsr3fi.cpp").read_text(encoding="utf-8")
    gen = block(fi, "bool generate(VkCommandBuffer cmd, const Inputs& in)")
    assert "const bool hudless = in.present != VK_NULL_HANDLE;" in gen
    assert "g.hudless != hudless" in gen and "g.hudless = hudless;" in gen, "generate: history reset when the kind of source changes"
    assert "k.hudless_attached_factor = hudless ? 1 : 0;" in gen
    assert "const Resource present = hudless ? Resource{in.present} : color;" in gen
    assert "tex(g.of_scd_output), tex(g.fi_inpainting_pyramid), present, color," in gen, "the inpainting pass: binding 2 is the present"
    assert "VkImageView present = VK_NULL_HANDLE;" in (ROOT / "gpu/vk/vk_fsr3fi.h").read_text(encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default=shutil.which("g++") or shutil.which("clang++") or "g++")
    p.add_argument("--glslang", default=shutil.which("glslangValidator"))
    p.add_argument("--write", action="store_true", help="make fg_hud_comp_spv.h from the shader")
    a = p.parse_args()
    check_wiring()
    shader = SHADER.read_text(encoding="utf-8")
    funcs = "\n".join(block(shader, m) for m in ("float srgb1(float v)", "vec3 srgb(vec3 v)", "vec3 hudless_colour("))
    with tempfile.TemporaryDirectory(prefix="fsr3fi-hudless-") as tmp:
        cpp, exe = Path(tmp) / "case.cpp", Path(tmp) / "case.exe"
        cpp.write_text(PREFIX + funcs + CASES, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
        if a.write:
            if not a.glslang:
                raise SystemExit("--write needs glslangValidator")
            HEADER.write_bytes(source_line(SHADER) + compile_header(a.glslang, SHADER, Path(tmp) / HEADER.name))
        header = HEADER.read_bytes()
        first, _, rest = header.partition(b"\n")
        assert first + b"\n" == source_line(SHADER), "fg_hud_comp_spv.h was not made from fg_hud.comp as it is (--write)"
        if a.glslang:
            assert compile_header(a.glslang, SHADER, Path(tmp) / HEADER.name) == rest, \
                "fg_hud_comp_spv.h is not glslang's output for fg_hud.comp"
    print("fsr3fi hudless colour: ok, header %s" % ("compiled and compared" if a.glslang else "hashed (no glslang)"))


if __name__ == "__main__":
    main()
