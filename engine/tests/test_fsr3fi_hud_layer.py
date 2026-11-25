# Copyright (C) 2026 Sacrappy

"""The HUD laid over FSR 3.1's made frames by how see-through it is (RX_VK_FSR_FG_HUD_LAYER), step 3 of the HUD-less chain.

Laid over a made frame whole, as AMD's HUD-less mode does, the presented pixel brings the real frame's scene with it wherever the
HUD lets the scene through (the hearts' glow, the item box's shadow, the faint icons, the minimap): a halo of the real frame's
background around every HUD element. The game blends its HUD as D = B + T * S (S the picture before the HUD, D after), so from one
frame to the next D1 - D0 = T * (S1 - S0): T is fitted per pixel from the two pictures the renderer already copies, B follows, and
the HUD goes over each made frame's own scene as B + T * scene (Longshot ad9fa48e0, #1755, Jordan; ported by behaviour).

Fixtures, each compiling the real code cut out of the sources, no GPU and no game:
  - `hud_fit` (shaders/fg_hud_layer.comp) as C++ over a HUD pixel through frames at B10G11R11's precision: T and B are found, the
    made frame's HUD is B + T * its own scene, a pixel with no HUD is (0, 0, 0, 1), a pixel not yet known or no longer explained
    (the HUD changed), a scene or HUD brighter than 1, a first frame and a garbage fit are unknown (T < 0), the presented pixel there;
  - `hud_over` (shaders/fg_hud_composite.comp): unknown takes the presented pixel, known the sRGB-encoded B + T * the made scene;
  - vk_framegen.cpp's `hud_source` with the layer on: the fit pass after the HUD-less pass with this frame's and the frame before's
    pictures (this frame's bound in its place when there is none), the frame before dropped by a present without the picture or
    a change of size, the fit's images made once, the pipelines failing leaving the HUD laid as presented, the switch off doing
    none of it;
  - the headers are glslang's output for the shaders (--write makes them).

    python -I -B tests/test_fsr3fi_hud_layer.py --cxx g++ --headers <Vulkan-Headers/include> [--glslang glslangValidator] [--write]
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # (python -I leaves the check's own folder off the path)
from srccut import block, between  # noqa: E402
from test_fsr3fi_hud_capture import CAPTURE_PREFIX, CAPTURE_STUBS  # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}", "--headers", "{vulkan_headers}"]

ROOT = Path(__file__).resolve().parents[1]
SHADERS = ROOT / "gpu" / "vk" / "shaders"
ENV = "vulkan1.3"
SOURCES = (("fg_hud_layer.comp", "fg_hud_layer_comp_spv.h", "FG_HUD_LAYER_COMP_SPV"),
           ("fg_hud_composite.comp", "fg_hud_composite_comp_spv.h", "FG_HUD_COMPOSITE_COMP_SPV"))

GLSL = r'''
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
// (just enough GLSL for the shaders' functions, compiled as they are written)
struct vec3 {
    float x, y, z;
    vec3() : x(0), y(0), z(0) {}
    explicit vec3(float v) : x(v), y(v), z(v) {}
    vec3(float a, float b, float c) : x(a), y(b), z(c) {}
    float r() const { return x; }
    bool operator==(const vec3& o) const { return x == o.x && y == o.y && z == o.z; }
};
struct vec4 {
    float x, y, z, w;
    vec4() : x(0), y(0), z(0), w(0) {}
    explicit vec4(float v) : x(v), y(v), z(v), w(v) {}
    vec4(float a, float b, float c, float d) : x(a), y(b), z(c), w(d) {}
    vec4(vec3 v, float d) : x(v.x), y(v.y), z(v.z), w(d) {}
    bool operator==(const vec4& o) const { return x == o.x && y == o.y && z == o.z && w == o.w; }
};
struct bvec3 { bool x, y, z; };
static vec3 operator+(vec3 a, vec3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
static vec3 operator-(vec3 a, vec3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
static vec3 operator+(vec3 a, float b) { return {a.x + b, a.y + b, a.z + b}; }
static vec3 operator*(float a, vec3 b) { return {a * b.x, a * b.y, a * b.z}; }
static float dot(vec3 a, vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
static vec3 abs(vec3 a) { return {std::fabs(a.x), std::fabs(a.y), std::fabs(a.z)}; }
static vec3 max(vec3 a, vec3 b) { return {std::max(a.x, b.x), std::max(a.y, b.y), std::max(a.z, b.z)}; }
static bvec3 lessThanEqual(vec3 a, vec3 b) { return {a.x <= b.x, a.y <= b.y, a.z <= b.z}; }
static bvec3 greaterThan(vec3 a, vec3 b) { return {a.x > b.x, a.y > b.y, a.z > b.z}; }
static bool all(bvec3 b) { return b.x && b.y && b.z; }
static bool any(bvec3 b) { return b.x || b.y || b.z; }
static float glsl_clamp(float v, float lo, float hi) { return std::min(std::max(v, lo), hi); }
static float glsl_min(float a, float b) { return std::min(a, b); }
static float glsl_pow(float a, float b) { return std::pow(a, b); }
static float glsl_abs(float a) { return std::fabs(a); }
#define clamp glsl_clamp
#define pow glsl_pow
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); std::exit(1); } } while (0)
'''

CASES = r'''
#undef pow
#undef clamp
// (B10G11R11's precision: 6 bits of mantissa in red and green, 5 in blue, values rounded to them)
static float q(float v, int bits) {
    if (v <= 0.0f) return 0.0f;
    int e;
    const float m = std::frexp(v, &e);
    const float scale = (float)(1 << (bits + 1));
    return std::ldexp(std::round(m * scale) / scale, e);
}
static vec3 packed(vec3 v) { return {q(v.x, 6), q(v.y, 6), q(v.z, 5)}; }
static float enc(float v) { v = std::min(std::max(v, 0.0f), 1.0f); return v <= 0.0031308f ? 12.92f * v : 1.055f * std::pow(v, 1.0f / 2.4f) - 0.055f; }
static vec3 scene_at(int n) { return {0.20f + 0.09f * n, 0.35f + 0.06f * n, 0.10f + 0.12f * n}; }

int main() {
    const vec3 B(0.30f, 0.10f, 0.05f);
    const float T = 0.4f;
    const auto D = [&](vec3 s) { return packed(B + T * s); };
    vec4 f(0.0f), l;
    vec3 s0 = packed(scene_at(0)), d0 = D(s0), s1, d1;
    // (frame 0: nothing before it; frame 1: one frame of fit; frame 2: two, known)
    for (int n = 1; n <= 6; n++) {
        s1 = packed(scene_at(n)), d1 = D(s1);
        f = hud_fit(s1, d1, s0, d0, n > 1, f, l);
        if (n < 3) CHECK(l.w < 0.0f);   // (not yet known: the presented pixel)
        s0 = s1, d0 = d1;
    }
    CHECK(l.w >= 0.0f && std::fabs(l.w - T) < 0.03f);
    CHECK(std::fabs(l.x - B.x) < 0.03f && std::fabs(l.y - B.y) < 0.03f && std::fabs(l.z - B.z) < 0.03f);
    // (the made frame's own scene, between the two game frames: B + T * its scene, sRGB-encoded, within the picture's rounding of
    // what the game would have drawn)
    const vec3 sm = scene_at(7);
    const vec3 made(enc(sm.x), enc(sm.y), enc(sm.z));
    const vec3 shown(0.1f, 0.1f, 0.1f);
    const vec3 over = hud_over(made, shown, l);
    const vec3 want = B + T * sm;
    CHECK(std::fabs(over.x - enc(want.x)) < 0.02f && std::fabs(over.y - enc(want.y)) < 0.02f && std::fabs(over.z - enc(want.z)) < 0.02f);
    CHECK(std::fabs(over.x - shown.x) > 0.1f);   // (not the presented pixel)
    // (unknown: the presented pixel, whole)
    CHECK(hud_over(made, shown, vec4(0.0f, 0.0f, 0.0f, -1.0f)) == shown);

    // (no HUD in the pixel: nothing to lay, the fit kept)
    const vec4 kept(1.0f, 2.0f, 5.0f, 0.0f);
    vec4 nf = hud_fit(s1, s1, s0, d0, true, kept, l);
    CHECK(l == vec4(0.0f, 0.0f, 0.0f, 1.0f) && nf == kept);

    // (the HUD changed (a count ticked, a prompt came up): the fit starts again, the presented pixel meanwhile)
    const vec3 s2 = packed(scene_at(8));
    nf = hud_fit(s2, packed(vec3(0.8f, 0.7f, 0.6f)), s1, d1, true, f, l);
    CHECK(nf == vec4(0.0f) && l.w < 0.0f);

    // (no frame before, or no HUD in it: unknown, the fit untouched)
    nf = hud_fit(s2, D(s2), s1, d1, false, f, l);
    CHECK(l.w < 0.0f && nf == f);
    nf = hud_fit(s2, D(s2), s1, s1, true, f, l);
    CHECK(l.w < 0.0f && nf == f);

    // (a scene or a HUD brighter than 1: the frame is clamped, so the presented pixel)
    const vec3 bright(1.4f, 0.5f, 0.5f);
    nf = hud_fit(bright, D(bright), s1, d1, true, f, l);
    CHECK(l.w < 0.0f);

    // (a fit that is not one, NaN or out of range included: started again, not used)
    const float nan = std::nanf("");
    for (vec4 bad : {vec4(nan, 1.0f, 3.0f, 0.0f), vec4(1.0f, nan, 3.0f, 0.0f), vec4(1.0f, 1.0f, nan, 0.0f), vec4(0.0f, -1.0f, 3.0f, 0.0f),
                     vec4(2e4f, 1.0f, 3.0f, 0.0f), vec4(0.0f, 1.0f, 300.0f, 0.0f)}) {
        nf = hud_fit(s2, D(s2), s1, d1, true, bad, l);
        CHECK(nf.z <= 1.0f && l.w < 0.0f);
    }

    // (a pixel the scene does not move behind: T is not knowable, never invented)
    vec4 g(0.0f);
    const vec3 still = packed(vec3(0.5f, 0.5f, 0.5f));
    for (int n = 0; n < 8; n++) g = hud_fit(still, D(still), still, D(still), true, g, l);
    CHECK(l.w < 0.0f);
    std::puts("hud layer: ok");
    return 0;
}
'''

LAYER_SUFFIX = r'''
int main(int argc, char** argv) {
    const bool on = argc < 2 || std::strcmp(argv[1], "off") != 0;
    putenv((char*)"RX_VK_FSR_FG_HUDLESS=1");
    if (on) putenv((char*)"RX_VK_FSR_FG_HUD_LAYER=1");
    const VkCommandBuffer cmd = (VkCommandBuffer)(uintptr_t)1;
    fg.frames[0] = {handle<VkImage>(), nullptr, handle<VkImageView>()};
    fg.frames[1] = {handle<VkImage>(), nullptr, handle<VkImageView>()};
    Texture picture;
    picture.image = handle<VkImage>(), picture.format = VK_FORMAT_B10G11R11_UFLOAT_PACK32;
    picture.width = 2560, picture.height = 1440, picture.gpu = 0x80000000;
    alive.push_back({picture.gpu, &picture});
    const auto present = [&](bool with_picture = true, uint32_t w = 2560, uint32_t h = 1440) {
        if (with_picture) framegen_hud_before(cmd, &picture, 0);
        passes.clear();
        const bool ok = hud_source(cmd, 0, w, h, 0, 0, w, h, false);
        return ok;
    };

    if (!on) {   // (the switch off, the default: the HUD-less colour alone, no fit, no layer)
        CHECK(present() && !hud.layered && passes.size() == 1 && made_pipes == 1 && hud.layers == 0);
        CHECK(!hud.fit.image && !hud.layer.image);
        std::puts("layer off: ok");
        return 0;
    }
    // (the first present: no frame before, this frame's pictures bound in its place, prev 0)
    CHECK(present() && hud.layered && hud.layers == 1);
    CHECK(passes.size() == 2 && passes[0].views.size() == 4 && passes[1].views.size() == 6);
    const Pass& fit = passes[1];
    CHECK(fit.views[0] == hud.before[1].view || fit.views[0] == hud.before[0].view);
    CHECK(fit.views[0] == fit.views[2] && fit.views[1] == fit.views[3] && fit.pc[4] == 0);
    CHECK(fit.views[4] == hud.fit.view && fit.views[5] == hud.layer.view && fit.w == 2560 && fit.h == 1440 && fit.pc[2] == 2560);
    CHECK(made_pipes == 3);   // (the HUD-less pass, the fit, the lay-over: once)
    // (the next: the frame before's pictures, the other pair, prev 1; its fit and layer the same images)
    VkImageView fit_image = hud.fit.view;
    const VkImageView before_k = passes[1].views[0];
    CHECK(present() && hud.layered && hud.layers == 2);
    CHECK(passes[1].views[0] != before_k && passes[1].views[2] == before_k && passes[1].pc[4] == 1);
    CHECK(passes[1].views[1] != passes[1].views[3] && hud.fit.view == fit_image && made_pipes == 3);
    // (a present with no picture (a menu, a movie): no layer, and the frame after it has no frame before)
    CHECK(!present(false) && !hud.layered && !hud.ready && passes.empty());
    CHECK(present() && passes[1].pc[4] == 0 && passes[1].views[0] == passes[1].views[2]);
    CHECK(present() && passes[1].pc[4] == 1);
    // (a crop of another size: the fit and the layer are made again for it, from no frame before)
    framegen_hud_before(cmd, &picture, 0);
    passes.clear();
    CHECK(hud_source(cmd, 0, 2560, 1440, 320, 0, 1920, 1440, false) && passes[1].pc[0] == 320 && passes[1].pc[2] == 1920 &&
          passes[1].pc[4] == 0 && passes[1].w == 1920 && hud.fit.view != fit_image);
    // (the picture's own size changed: the pictures made again, no frame before)
    Texture big = picture;
    big.image = handle<VkImage>(), big.width = 3840, big.height = 2160, big.gpu = 0x90000000;
    alive.push_back({big.gpu, &big});
    framegen_hud_before(cmd, &big, 0);
    passes.clear();
    CHECK(hud_source(cmd, 0, 3840, 2160, 0, 0, 3840, 2160, false) && passes[1].pc[4] == 0 && passes[1].w == 3840);
    // (Native HDR's PQ present: no picture, no layer)
    framegen_hud_before(cmd, &big, 0);
    passes.clear();
    CHECK(!hud_source(cmd, 0, 3840, 2160, 0, 0, 3840, 2160, true) && !hud.layered && passes.empty());
    CHECK(!hud.ready);

    // (the layer's pipelines cannot be made: the HUD-less colour alone, the HUD laid over the made frame as presented, and no retry)
    hud.fit_pipe = {}, hud.over_pipe = {}, hud.layer_failed = false;
    fail_layer_pipes = true;
    const auto present_big = [&] {
        framegen_hud_before(cmd, &big, 0);
        passes.clear();
        return hud_source(cmd, 0, 3840, 2160, 0, 0, 3840, 2160, false);
    };
    CHECK(present_big() && hud.ready && !hud.layered && hud.layer_failed && passes.size() == 1);
    fail_layer_pipes = false;
    const int pipes = made_pipes;
    CHECK(present_big() && !hud.layered && made_pipes == pipes);
    // (frame generation off for a while: everything let go, the state as at the start, the next frame made again)
    CHECK(hud.fit.image && hud.layer.image && hud.before[0].image && hud.hudless[0].image);
    hud_release();
    CHECK(!hud.fit.image && !hud.layer.image && !hud.before[0].image && !hud.before[1].image && !hud.after[0].image &&
          !hud.hudless[0].image && !hud.hudless[1].image && hud.dead.empty() && !hud.fit_pipe.pipe && !hud.fresh && !hud.prev &&
          !hud.layer_failed && !hud.init && !hud.picture && hud.pw == 0);
    framegen_hud_before(cmd, &big, 0);
    passes.clear();
    CHECK(hud_source(cmd, 0, 3840, 2160, 0, 0, 3840, 2160, false) && hud.layered && passes[1].pc[4] == 0);
    std::puts("layer: ok");
    return 0;
}
'''


def source_line(comp, var):
    digest = hashlib.sha256(comp.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return ("// %s sha256 %s (tests/test_fsr3fi_hud_layer.py --write: glslangValidator -V --target-env %s --vn %s)\n"
            % (comp.name, digest, ENV, var)).encode()


def compile_header(glslang, comp, out, var):
    r = subprocess.run([glslang, "-V", "--target-env", ENV, "--vn", var, "-o", str(out), str(comp)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("%s does not compile:\n%s%s" % (comp.name, r.stdout, r.stderr))
    return out.read_bytes()


def check_wiring():
    layer = (SHADERS / "fg_hud_layer.comp").read_text(encoding="utf-8").replace("\r\n", "\n")
    for need in ("layout(binding = 0) uniform sampler2D before;", "layout(binding = 1) uniform sampler2D after;",
                 "layout(binding = 2) uniform sampler2D prev_before;", "layout(binding = 3) uniform sampler2D prev_after;",
                 "layout(binding = 4, rgba16f) uniform image2D fit;", "layout(binding = 5, rgba16f) uniform writeonly image2D layer;",
                 "layout(push_constant) uniform P { ivec2 offset; ivec2 size; int prev; } pc;"):
        assert need in layer, "fg_hud_layer.comp: %r is missing" % need
    over = (SHADERS / "fg_hud_composite.comp").read_text(encoding="utf-8").replace("\r\n", "\n")
    for need in ("layout(binding = 0, rgba8) uniform image2D made;", "layout(binding = 1) uniform sampler2D shown;",
                 "layout(binding = 2) uniform sampler2D layer;", "layout(push_constant) uniform P { ivec2 size; } pc;"):
        assert need in over, "fg_hud_composite.comp: %r is missing" % need
    fg = (ROOT / "gpu/vk/vk_framegen.cpp").read_text(encoding="utf-8")
    for need in ('#include "shaders/fg_hud_layer_comp_spv.h"', '#include "shaders/fg_hud_composite_comp_spv.h"',
                 "make_pipe(hud.fit_pipe, FG_HUD_LAYER_COMP_SPV, sizeof FG_HUD_LAYER_COMP_SPV, {S, S, S, S, I, I}, 20)",
                 "make_pipe(hud.over_pipe, FG_HUD_COMPOSITE_COMP_SPV, sizeof FG_HUD_COMPOSITE_COMP_SPV, {I, S, S}, 8)",
                 'getenv("RX_VK_FSR_FG_HUD_LAYER")', "return e && *e && atoi(e) != 0;"):
        assert need in fg, "vk_framegen.cpp: %r is missing" % need
    frame = block(fg, "int fsr3fi_frame(")
    assert "in.present = layered ? hud.hudless[hud.hl].view : fg.frames[cur].view;" in frame, "FSR 3.1's present is the HUD-less colour: it lays nothing"
    copy = frame.index("vkCmdCopyImage(cmd, fsr3fi::made_image()")
    assert frame.index("if (layered) {") > copy and "hud_composite(cmd, set.frame[0].view, cur, uw, uh);" in frame[copy:],         "the HUD goes over the made frame after its copy"
    compose = block(fg, "void hud_composite(")
    assert "{{made, VK_NULL_HANDLE}, {fg.frames[cur].view, fg.nearest}, {hud.layer.view, fg.nearest}}" in compose


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default=shutil.which("g++") or shutil.which("clang++") or "g++")
    p.add_argument("--headers", type=Path, required=True)
    p.add_argument("--glslang", default=shutil.which("glslangValidator"))
    p.add_argument("--write", action="store_true", help="make the headers from the shaders")
    a = p.parse_args()
    check_wiring()
    layer = (SHADERS / "fg_hud_layer.comp").read_text(encoding="utf-8")
    over = (SHADERS / "fg_hud_composite.comp").read_text(encoding="utf-8")
    consts = between(layer, "const float kKeep", "float see_through(")
    funcs = "\n".join(block(layer, m) for m in ("float see_through(", "bool known(", "bool explains(", "vec4 hud_fit("))
    funcs = funcs.replace("out vec4 layer", "vec4& layer").replace("min(f.z", "glsl_min(f.z").replace("abs(f.x)", "glsl_abs(f.x)")
    # (the composite's helpers: its own srgb1 and friends, named apart from the GLSL stand-ins above)
    helpers = "\n".join(block(over, m) for m in ("float srgb1(", "vec3 srgb(", "float linear1(", "vec3 linear(", "vec3 hud_over("))
    helpers = helpers.replace("vec3(srgb1(v.r), srgb1(v.g), srgb1(v.b))", "vec3(srgb1(v.x), srgb1(v.y), srgb1(v.z))")
    helpers = helpers.replace("vec3(linear1(v.r), linear1(v.g), linear1(v.b))", "vec3(linear1(v.x), linear1(v.y), linear1(v.z))")
    helpers = helpers.replace("l.rgb + l.a * linear(made)", "vec3(l.x, l.y, l.z) + l.w * linear(made)").replace("l.a < 0.0", "l.w < 0.0")
    flags = ["-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter",
             "-Wno-missing-field-initializers", "-pthread"]
    with tempfile.TemporaryDirectory(prefix="fsr3fi-hud-layer-") as tmp:
        tmp = Path(tmp)
        cpp, exe = tmp / "fit.cpp", tmp / "fit.exe"
        cpp.write_text(GLSL + consts + funcs + helpers + CASES, encoding="utf-8")
        subprocess.run([a.cxx, *flags, str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)

        fg = (ROOT / "gpu/vk/vk_framegen.cpp").read_text(encoding="utf-8")
        img = between(fg, "struct Img {", "struct OutSet {") + between(fg, "struct Pipe {", "struct State {")
        hud = between(fg, "struct HudState {", "} // namespace\n\nbool framegen_hud_wanted(")
        fix = tmp / "framegen.cpp"
        fix.write_text(CAPTURE_PREFIX + img + CAPTURE_STUBS + hud + block(fg, "bool framegen_hud_wanted(") +
                       block(fg, "void framegen_hud_before(") + LAYER_SUFFIX, encoding="utf-8")
        fexe = tmp / "framegen.exe"
        subprocess.run([a.cxx, *flags, "-I" + str(a.headers), str(fix), "-o", str(fexe)], check=True)
        subprocess.run([str(fexe)], check=True)
        subprocess.run([str(fexe), "off"], check=True)

        for comp_name, header_name, var in SOURCES:
            comp, header = SHADERS / comp_name, SHADERS / header_name
            if a.write:
                if not a.glslang:
                    raise SystemExit("--write needs glslangValidator")
                header.write_bytes(source_line(comp, var) + compile_header(a.glslang, comp, tmp / header_name, var))
            first, _, rest = header.read_bytes().partition(b"\n")
            assert first + b"\n" == source_line(comp, var), "%s was not made from %s as it is (--write)" % (header_name, comp_name)
            if a.glslang:
                assert compile_header(a.glslang, comp, tmp / header_name, var) == rest, \
                    "%s is not glslang's output for %s" % (header_name, comp_name)
    print("fsr3fi hud layer: ok, headers %s" % ("compiled and compared" if a.glslang else "hashed (no glslang)"))


if __name__ == "__main__":
    main()
