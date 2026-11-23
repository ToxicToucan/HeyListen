# Copyright (C) 2026 Sacrappy

"""The encoder decoded ASTC textures are kept with (gpu/vk/shaders/astc_bc_encode.comp, TICK-031): its own functions compiled
as C++, their blocks read back by a BC7 / BC1 decoder written from the format's specification. Issue 16: texels of alpha 0
and 255 come back exactly so (blending leaves the scene as it is only under alpha 0; BC7 mode 6's p-bit gave a transparent
white block alpha 1, and a sprite's whole quad lifted a dark scene by ~8 levels of 255: the light rectangles around the HUD,
the minimap and particles), in transparent blocks of any colour, at sprites' edges and in opaque ones; and the blocks are
still close to their texels (flat ones within one level, ramps and noise within bounds). astc_bc_encode_comp_spv.h names
the hash of the shader it was made from (its first line): with glslangValidator the shader compiles, and with the version
the header names, the rest of the header is its output byte for byte. --write makes the header.

python -I -B tests/test_astc_bc_encode.py [--cxx g++] [--glslang glslangValidator] [--write]
"""
import argparse
import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHADERS = ROOT / "gpu" / "vk" / "shaders"
COMP = SHADERS / "astc_bc_encode.comp"
HEADER = SHADERS / "astc_bc_encode_comp_spv.h"
GLSLANG_ARGS = ["-V", "--target-env", "vulkan1.1", "--vn", "ASTC_BC_ENCODE_COMP_SPV"]


def source_line(comp):
    """the header's first line: the shader's hash (line ends as LF, as the checkout has them) and how the header is made"""
    digest = hashlib.sha256(comp.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return ("// astc_bc_encode.comp sha256 %s (tests/test_astc_bc_encode.py --write: glslangValidator %s)\n" %
            (digest, " ".join(GLSLANG_ARGS))).encode()


def compile_header(glslang, comp, out):
    r = subprocess.run([glslang, *GLSLANG_ARGS, "-o", str(out), str(comp)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("astc_bc_encode.comp does not compile:\n" + r.stdout + r.stderr)
    return out.read_bytes()


def as_cpp(text):
    """the shader's functions (from its tables to main(), main left out) as C++ over the SHIM below"""
    body = text[text.index("const float W4[16]"):text.index("void main()")]
    body = re.sub(r"const float (\w+)\[(\d+)\] = float\[\d+\]\((.*?)\);", r"static const float \1[\2] = {\3};", body)
    body = re.sub(r"\b(?:inout|out)\s+(\w+)\s+(\w+)", r"\1& \2", body)
    body = re.sub(r"\.rgb\b", ".rgb()", body)
    body = re.sub(r"\b(round|floor|clamp|min|max|abs|length|dot|mix|outerProduct)\(", r"g_\1(", body)
    return body


# (just enough of GLSL's vectors and matrices for the encoder; round() is the GPU's, to the even neighbour on a half)
SHIM = r'''
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
typedef unsigned uint;
struct vec3 {
    float r, g, b;
    vec3() : r(0), g(0), b(0) {}
    explicit vec3(float v) : r(v), g(v), b(v) {}
    vec3(float x, float y, float z) : r(x), g(y), b(z) {}
};
struct uvec3 {
    uint r, g, b;
    explicit uvec3(uint v = 0) : r(v), g(v), b(v) {}
    uvec3(uint x, uint y, uint z) : r(x), g(y), b(z) {}
    explicit uvec3(vec3 v) : r((uint)v.r), g((uint)v.g), b((uint)v.b) {}
};
struct vec4 {
    union { struct { float x, y, z, w; }; struct { float r, g, b, a; }; float v[4]; };
    vec4() { x = y = z = w = 0; }
    explicit vec4(float s) { x = y = z = w = s; }
    vec4(float p, float q, float s, float t) { x = p, y = q, z = s, w = t; }
    vec4(vec3 c, float t) { x = c.r, y = c.g, z = c.b, w = t; }
    float& operator[](int i) { return v[i]; }
    float operator[](int i) const { return v[i]; }
    vec3 rgb() const { return {x, y, z}; }
    vec4& operator+=(vec4 o) { for (int i = 0; i < 4; i++) v[i] += o.v[i]; return *this; }
    vec4& operator/=(float s) { for (int i = 0; i < 4; i++) v[i] /= s; return *this; }
};
struct uvec4 {
    union { struct { uint x, y, z, w; }; struct { uint r, g, b, a; }; uint v[4]; };
    explicit uvec4(uint s = 0) { x = y = z = w = s; }
    explicit uvec4(vec4 f) { x = (uint)f.x, y = (uint)f.y, z = (uint)f.z, w = (uint)f.w; }
    uint& operator[](uint i) { return v[i]; }
};
struct uvec2 { uint x, y; uvec2(uint p = 0, uint q = 0) : x(p), y(q) {} };
struct mat4 {
    vec4 c[4];
    explicit mat4(float s = 0) { for (int i = 0; i < 4; i++) c[i] = vec4(0.0f), c[i][i] = s; }
    vec4& operator[](int i) { return c[i]; }
    mat4& operator+=(const mat4& o) { for (int i = 0; i < 4; i++) c[i] += o.c[i]; return *this; }
};
static vec4 operator+(vec4 a, vec4 b) { return {a.x + b.x, a.y + b.y, a.z + b.z, a.w + b.w}; }
static vec4 operator-(vec4 a, vec4 b) { return {a.x - b.x, a.y - b.y, a.z - b.z, a.w - b.w}; }
static vec4 operator*(vec4 a, float s) { return {a.x * s, a.y * s, a.z * s, a.w * s}; }
static vec4 operator*(float s, vec4 a) { return a * s; }
static vec4 operator/(vec4 a, float s) { return {a.x / s, a.y / s, a.z / s, a.w / s}; }
static vec4 operator+(vec4 a, float s) { return {a.x + s, a.y + s, a.z + s, a.w + s}; }
static vec4 operator-(vec4 a, float s) { return {a.x - s, a.y - s, a.z - s, a.w - s}; }
static vec4 operator*(const mat4& m, vec4 v) { return m.c[0] * v.x + m.c[1] * v.y + m.c[2] * v.z + m.c[3] * v.w; }
static vec3 operator+(vec3 a, vec3 b) { return {a.r + b.r, a.g + b.g, a.b + b.b}; }
static vec3 operator-(vec3 a, vec3 b) { return {a.r - b.r, a.g - b.g, a.b - b.b}; }
static vec3 operator*(vec3 a, vec3 b) { return {a.r * b.r, a.g * b.g, a.b * b.b}; }
static vec3 operator*(vec3 a, float s) { return {a.r * s, a.g * s, a.b * s}; }
static vec3 operator*(float s, vec3 a) { return a * s; }
static vec3 operator/(vec3 a, float s) { return {a.r / s, a.g / s, a.b / s}; }
static vec3 operator+(vec3 a, float s) { return {a.r + s, a.g + s, a.b + s}; }
static uvec4 operator*(uvec4 a, uint s) { uvec4 o; for (int i = 0; i < 4; i++) o.v[i] = a.v[i] * s; return o; }
static uvec4 operator+(uvec4 a, uint s) { uvec4 o; for (int i = 0; i < 4; i++) o.v[i] = a.v[i] + s; return o; }
static vec4 to_vec4(uvec4 u) { return {(float)u.x, (float)u.y, (float)u.z, (float)u.w}; }
static uvec3 operator<<(uvec3 a, int n) { return {a.r << n, a.g << n, a.b << n}; }
static uvec3 operator>>(uvec3 a, int n) { return {a.r >> n, a.g >> n, a.b >> n}; }
static uvec3 operator|(uvec3 a, uvec3 b) { return {a.r | b.r, a.g | b.g, a.b | b.b}; }
static vec3 to_vec3(uvec3 u) { return {(float)u.r, (float)u.g, (float)u.b}; }
static float g_round(float v) { return std::nearbyint(v); }
static vec3 g_round(vec3 v) { return {g_round(v.r), g_round(v.g), g_round(v.b)}; }
static vec4 g_round(vec4 v) { return {g_round(v.x), g_round(v.y), g_round(v.z), g_round(v.w)}; }
static float g_floor(float v) { return std::floor(v); }
static vec3 g_floor(vec3 v) { return {std::floor(v.r), std::floor(v.g), std::floor(v.b)}; }
static vec4 g_floor(vec4 v) { return {std::floor(v.x), std::floor(v.y), std::floor(v.z), std::floor(v.w)}; }
static float g_min(float a, float b) { return a < b ? a : b; }
static float g_max(float a, float b) { return a > b ? a : b; }
static float g_clamp(float v, float lo, float hi) { return g_min(g_max(v, lo), hi); }
static vec3 g_clamp(vec3 v, float lo, float hi) { return {g_clamp(v.r, lo, hi), g_clamp(v.g, lo, hi), g_clamp(v.b, lo, hi)}; }
static vec3 g_clamp(vec3 v, vec3 lo, vec3 hi) { return {g_clamp(v.r, lo.r, hi.r), g_clamp(v.g, lo.g, hi.g), g_clamp(v.b, lo.b, hi.b)}; }
static vec4 g_clamp(vec4 v, float lo, float hi) {
    return {g_clamp(v.x, lo, hi), g_clamp(v.y, lo, hi), g_clamp(v.z, lo, hi), g_clamp(v.w, lo, hi)};
}
static float g_abs(float v) { return std::fabs(v); }
static float g_dot(vec3 a, vec3 b) { return a.r * b.r + a.g * b.g + a.b * b.b; }
static float g_dot(vec4 a, vec4 b) { return a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w; }
static float g_length(vec4 a) { return std::sqrt(g_dot(a, a)); }
static vec4 g_mix(vec4 a, vec4 b, vec4 t) { vec4 o; for (int i = 0; i < 4; i++) o.v[i] = a.v[i] + (b.v[i] - a.v[i]) * t.v[i]; return o; }
static mat4 g_outerProduct(vec4 c, vec4 r) { mat4 m; for (int j = 0; j < 4; j++) m.c[j] = c * r.v[j]; return m; }
'''

# the vec4(uvec4) and vec3(uvec3) conversions GLSL writes as constructors
CONVERSIONS = [(r"vec4\((q\d \* 2u \+ p\d|q \* 2u \+ pb)\)", r"to_vec4(\1)"),
               (r"vec3\(\((c\d) << 1\) \| \((c\d) >> 6\)\)", r"to_vec3((\1 << 1) | (\2 >> 6))")]

FIXTURE = r'''
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)

// ---- BC7 and BC1 decoders, from the formats' specification (not from the encoder) ----
struct Bits {
    uint w[4];
    uint pos = 0;
    uint take(uint n) {
        uint v = 0;
        for (uint i = 0; i < n; i++, pos++) v |= ((w[pos >> 5] >> (pos & 31)) & 1u) << i;
        return v;
    }
};
static const int kW2[4] = {0, 21, 43, 64};
static const int kW4[16] = {0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64};
static int interp(int e0, int e1, int w) { return ((64 - w) * e0 + w * e1 + 32) >> 6; }
// texels as RGBA bytes; only modes 5 and 6 (the encoder writes no other): false for any other
static bool bc7_decode(const uint words[4], int out[16][4]) {
    Bits b{{words[0], words[1], words[2], words[3]}};
    int mode = 0;
    while (mode < 8 && !b.take(1)) mode++;
    if (mode == 6) {
        int e[2][4];
        for (int c = 0; c < 4; c++) for (int k = 0; k < 2; k++) e[k][c] = (int)b.take(7);
        const int p0 = (int)b.take(1), p1 = (int)b.take(1);
        for (int c = 0; c < 4; c++) e[0][c] = e[0][c] << 1 | p0, e[1][c] = e[1][c] << 1 | p1;
        for (int i = 0; i < 16; i++) {
            const int ix = (int)b.take(i == 0 ? 3 : 4);
            for (int c = 0; c < 4; c++) out[i][c] = interp(e[0][c], e[1][c], kW4[ix]);
        }
        return true;
    }
    if (mode == 5) {
        const int rot = (int)b.take(2);
        int e[2][4];
        for (int c = 0; c < 3; c++) for (int k = 0; k < 2; k++) { const int v = (int)b.take(7); e[k][c] = v << 1 | v >> 6; }
        for (int k = 0; k < 2; k++) e[k][3] = (int)b.take(8);
        int ci[16], ai[16];
        for (int i = 0; i < 16; i++) ci[i] = (int)b.take(i == 0 ? 1 : 2);
        for (int i = 0; i < 16; i++) ai[i] = (int)b.take(i == 0 ? 1 : 2);
        for (int i = 0; i < 16; i++) {
            for (int c = 0; c < 3; c++) out[i][c] = interp(e[0][c], e[1][c], kW2[ci[i]]);
            out[i][3] = interp(e[0][3], e[1][3], kW2[ai[i]]);
            if (rot) { const int t = out[i][3]; out[i][3] = out[i][rot - 1], out[i][rot - 1] = t; }
        }
        return true;
    }
    return false;
}
static void bc1_decode(uint w0, uint w1, int out[16][4]) {
    const uint c0 = w0 & 0xFFFFu, c1 = w0 >> 16;
    int pal[4][4];
    const auto unpack = [](uint v, int* o) {
        const int r = (int)(v >> 11 & 31), g = (int)(v >> 5 & 63), b = (int)(v & 31);
        o[0] = r << 3 | r >> 2, o[1] = g << 2 | g >> 4, o[2] = b << 3 | b >> 2, o[3] = 255;
    };
    unpack(c0, pal[0]), unpack(c1, pal[1]);
    for (int c = 0; c < 3; c++) {
        if (c0 > c1) pal[2][c] = (2 * pal[0][c] + pal[1][c]) / 3, pal[3][c] = (pal[0][c] + 2 * pal[1][c]) / 3;
        else pal[2][c] = (pal[0][c] + pal[1][c]) / 2, pal[3][c] = 0;
    }
    pal[2][3] = 255, pal[3][3] = c0 > c1 ? 255 : 0;
    for (int i = 0; i < 16; i++) for (int c = 0; c < 4; c++) out[i][c] = pal[w1 >> (2 * i) & 3][c];
}

// ---- the shader's main() for one block: alpha range, then BC7 (or BC1) ----
static int texels[16][4];
static void load() {
    for (int i = 0; i < 16; i++) px[i] = vec4((float)texels[i][0], (float)texels[i][1], (float)texels[i][2], (float)texels[i][3]);
}
static void encode7(int out[16][4]) {
    load();
    float alo = 255.0f, ahi = 0.0f;
    for (int i = 0; i < 16; i++) alo = g_min(alo, px[i].a), ahi = g_max(ahi, px[i].a);
    uvec4 b = bc7(alo, ahi);
    CHECK(bc7_decode(b.v, out));
}

static unsigned seed = 16;
static int rnd(int n) { seed = seed * 1664525u + 1013904223u; return (int)((seed >> 8) % (unsigned)n); }
static int clamp255(int v) { return v < 0 ? 0 : v > 255 ? 255 : v; }

// every texel of alpha 0 or 255 back exactly so; returns the block's largest error in any channel of the others
static int check_pinned(const char* what, int n) {
    int out[16][4];
    encode7(out);
    int worst = 0;
    for (int i = 0; i < 16; i++) {
        const int a = texels[i][3];
        if ((a == 0 || a == 255) && out[i][3] != a) {
            std::fprintf(stderr, "%s, block %d: texel %d of alpha %d (rgb %d %d %d) decodes to alpha %d\n", what, n, i, a,
                         texels[i][0], texels[i][1], texels[i][2], out[i][3]);
            std::exit(1);
        }
        for (int c = 0; c < 4; c++) {
            const int d = std::abs(out[i][c] - texels[i][c]);
            if (c == 3 || a != 0) worst = d > worst ? d : worst;   // (a transparent texel's colour shows nowhere)
        }
    }
    return worst;
}

int main() {
    // the issue's block: a transparent white border (the HUD's sprites and particles have them), and other colours
    const int colours[][3] = {{255, 255, 255}, {0, 0, 0}, {127, 63, 201}, {1, 1, 1}, {255, 128, 7}, {33, 99, 165}};
    int n = 0;
    for (const auto& c : colours) {
        for (int i = 0; i < 16; i++) texels[i][0] = c[0], texels[i][1] = c[1], texels[i][2] = c[2], texels[i][3] = 0;
        check_pinned("a flat transparent block", n++);
        for (int i = 0; i < 16; i++) texels[i][3] = 255;
        CHECK(check_pinned("a flat opaque block", n++) <= 1);
    }
    // transparent blocks of any colours, flat ones of any alpha (within one level)
    for (int k = 0; k < 20000; k++) {
        for (int i = 0; i < 16; i++) for (int c = 0; c < 3; c++) texels[i][c] = rnd(256);
        for (int i = 0; i < 16; i++) texels[i][3] = 0;
        check_pinned("a transparent block", n++);
        const int f[4] = {rnd(256), rnd(256), rnd(256), rnd(256)};
        for (int i = 0; i < 16; i++) for (int c = 0; c < 4; c++) texels[i][c] = f[c];
        CHECK(check_pinned("a flat block", n++) <= 1);
    }
    // sprites' edges: a soft edge across the block (alpha 0 on one side, 255 on the other, a ramp between), colour flat,
    // white, or varying; and opaque blocks of varying colour. The visible texels stay close
    long long sum = 0, count = 0;
    for (int k = 0; k < 40000; k++) {
        const float ang = (float)rnd(6283) / 1000.0f, off = (float)(rnd(600) - 300) / 100.0f, soft = 0.3f + (float)rnd(300) / 100.0f;
        const int kind = rnd(4);
        const int base[3] = {rnd(256), rnd(256), rnd(256)};
        for (int y = 0; y < 4; y++)
            for (int x = 0; x < 4; x++) {
                int* t = texels[y * 4 + x];
                const float d = ((float)x - 1.5f) * std::cos(ang) + ((float)y - 1.5f) * std::sin(ang) - off;
                t[3] = kind == 3 ? 255 : clamp255((int)std::lround(127.5f + 127.5f * d / soft));
                for (int c = 0; c < 3; c++)
                    t[c] = kind == 0 ? base[c] : kind == 1 ? 255 : clamp255(base[c] + (x + y) * 9 - 27 + rnd(9));
            }
        const int worst = check_pinned("a sprite's edge", n++);
        CHECK(worst <= 64);
        sum += worst, count++;
    }
    std::printf("PASS: alpha 0 and 255 decode exactly in %d blocks; flat blocks within one level; sprite edges' worst "
                "texel %.1f levels on average\n", n, (double)sum / (double)count);
    if ((double)sum / (double)count > 16.0) { std::fprintf(stderr, "FAIL: sprite edges too far from their texels\n"); return 1; }

    // BC1 (textures whose ASTC blocks have no alpha; a later upload that brought some): texels under half alpha transparent
    for (int k = 0; k < 20000; k++) {
        for (int i = 0; i < 16; i++) {
            for (int c = 0; c < 3; c++) texels[i][c] = rnd(256);
            texels[i][3] = rnd(4) ? 255 : 0;
        }
        load();
        const uvec2 b = bc1();
        int out[16][4];
        bc1_decode(b.x, b.y, out);
        for (int i = 0; i < 16; i++) CHECK(out[i][3] == texels[i][3]);
    }
    std::printf("PASS: BC1's transparent and opaque texels decode so\n");
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++")
    p.add_argument("--glslang", default="glslangValidator")
    p.add_argument("--write", action="store_true", help="make astc_bc_encode_comp_spv.h from astc_bc_encode.comp")
    a = p.parse_args()
    if a.write:
        glslang = shutil.which(a.glslang)
        if not glslang:
            raise SystemExit("--write needs %s" % a.glslang)
        with tempfile.TemporaryDirectory(prefix="astc-bc-") as tmp:
            HEADER.write_bytes(source_line(COMP) + compile_header(glslang, COMP, Path(tmp) / "out.h"))
        print("wrote %s" % HEADER)
        return
    header = HEADER.read_bytes()
    first, _, hbody = header.partition(b"\n")
    if first + b"\n" != source_line(COMP):
        raise SystemExit("astc_bc_encode_comp_spv.h was not made from this astc_bc_encode.comp (its first line names another "
                         "hash): python -I -B tests/test_astc_bc_encode.py --write")
    text = COMP.read_text(encoding="utf-8")
    if "const uvec4 b = bc7(alo, ahi);" not in text[text.index("void main()"):]:
        raise SystemExit("main() does not encode its BC7 blocks with bc7(), the function checked here")
    body = as_cpp(text)
    for pat, rep in CONVERSIONS:
        body, n = re.subn(pat, rep, body)
        if not n:
            raise SystemExit("the shader no longer has %s: update CONVERSIONS" % pat)
    with tempfile.TemporaryDirectory(prefix="astc-bc-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        exe = Path(tmp) / "fixture"
        cpp.write_text(SHIM + body + FIXTURE, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-parameter",
                        str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
        glslang = shutil.which(a.glslang)
        if not glslang:
            print("PASS: the header names this shader (no %s: its compile not checked)" % a.glslang)
            return
        made = compile_header(glslang, COMP, Path(tmp) / "astc_bc_encode_comp_spv.h")
        if made.splitlines()[0] != hbody.splitlines()[0]:
            print("PASS: the header names this shader, and it compiles (glslang %s, the header's is %s: bytes not compared)" %
                  (made.splitlines()[0].decode().strip(), hbody.splitlines()[0].decode().strip()))
            return
        if made != hbody:
            raise SystemExit("astc_bc_encode_comp_spv.h is not astc_bc_encode.comp compiled: "
                             "python -I -B tests/test_astc_bc_encode.py --write")
        print("PASS: astc_bc_encode_comp_spv.h is astc_bc_encode.comp compiled")


if __name__ == "__main__":
    main()
