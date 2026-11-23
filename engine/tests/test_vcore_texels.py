"""nvnTextureReadTexels / WriteTexels hold no virtual core while the back end copies (host/rx_backend.c rx_nvn_read_texels,
rx_nvn_write_texels).

The Vulkan back end's texel copies take replay_mu, which the replay holds through a whole submission (hundreds of ms while
pipelines compile at boot or while a zone loads). With the core held, sead::MainThread sat on core 0 waiting for replay_mu
in a WriteTexels at boot (Linux, RX 9070 XT, the vcore escape's holder report: a 250-ms escape of the thread behind it).
The actual two wrappers are compiled with stand-ins that record whether the thread holds its core in the back end's copy:

- a read and a write by a thread with a core: neither with the core, the core back once after each;
- a write before the back end started (kept for later, no copy now): the core kept;
- a thread without a core takes none.

python -I -B tests/test_vcore_texels.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def block(source, marker):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { struct { int format; } d; } nvn_tex;
typedef struct { int xoffset, yoffset, zoffset, width, height, depth; } NVNcopyRegion;
typedef struct {
    int kind, x;
    nvn_tex t;
    unsigned level, layer0;
    NVNcopyRegion r;
    size_t row, img;
    uint8_t *data;
} early_call;
static int started = 1, kept;
static int keep_early(early_call *c) { kept++; free(c->data); return 1; }
static int nvn_fmt_bpb(int format, int *bw, int *bh) { (void)format; *bw = *bh = 1; return 4; }
static int held, takes, copies, copies_with_core, keep_flag;
static int texels_keep_core(void) { return keep_flag; }   /* (HOS_TEXELS_KEEP_CORE) */
int hos_vcore_away(void) { if (!held) return 0; held = 0; return 1; }
void hos_vcore_back(void) { if (!held) held = 1, takes++; }
typedef void (*read_fn)(const nvn_tex *, unsigned, unsigned, const NVNcopyRegion *, uint8_t *, size_t, size_t);
typedef void (*write_fn)(const nvn_tex *, unsigned, unsigned, const NVNcopyRegion *, const uint8_t *, size_t, size_t);
static void copy_read(const nvn_tex *t, unsigned l, unsigned a, const NVNcopyRegion *r, uint8_t *d, size_t row, size_t img) {
    (void)t, (void)l, (void)a, (void)r, (void)d, (void)row, (void)img;
    copies++, copies_with_core += held;
}
static void copy_write(const nvn_tex *t, unsigned l, unsigned a, const NVNcopyRegion *r, const uint8_t *s, size_t row, size_t img) {
    (void)t, (void)l, (void)a, (void)r, (void)s, (void)row, (void)img;
    copies++, copies_with_core += held;
}
static struct { read_fn nvn_read_texels; write_fn nvn_write_texels; } table = {copy_read, copy_write}, *vk = &table;
#define wgr_nvn_read_texels copy_read
#define wgr_nvn_write_texels copy_write

@WRAPPERS@

static int bad;
static void check(int ok, const char *what) { if (!ok) { printf("FAIL: %s\n", what); bad = 1; } }
int main(void) {
    static uint8_t buf[64];
    nvn_tex t = {{1}};
    NVNcopyRegion r = {0, 0, 0, 2, 2, 1};
    for (int core = 1; core >= 0; core--) {
        held = core, takes = copies = copies_with_core = 0;
        rx_nvn_read_texels(&t, 0, 0, &r, buf, 8, 16);
        check(copies == 1 && copies_with_core == 0, "the read copied without the core");
        check(takes == core && held == core, core ? "the core back once after the read" : "no core taken (none held)");
        held = core, takes = copies = copies_with_core = 0;
        rx_nvn_write_texels(&t, 0, 0, &r, buf, 8, 16);
        check(copies == 1 && copies_with_core == 0, "the write copied without the core");
        check(takes == core && held == core, core ? "the core back once after the write" : "no core taken (none held)");
    }
    keep_flag = 1, held = 1, takes = copies = copies_with_core = 0;   /* HOS_TEXELS_KEEP_CORE=1: the core kept, as before */
    rx_nvn_read_texels(&t, 0, 0, &r, buf, 8, 16);
    rx_nvn_write_texels(&t, 0, 0, &r, buf, 8, 16);
    check(copies == 2 && copies_with_core == 2 && takes == 0, "HOS_TEXELS_KEEP_CORE=1 keeps the core through both copies");
    keep_flag = 0;
    started = 0, held = 1, takes = copies = 0;
    rx_nvn_write_texels(&t, 0, 0, &r, buf, 8, 16);
    check(kept == 1 && copies == 0 && held == 1, "a write before the start: kept for later, the core kept");
    if (!bad) puts("ok");
    return bad;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="clang++")
    parser.add_argument("--source", type=Path, default=ROOT / "host/rx_backend.c")
    args = parser.parse_args()
    src = args.source.read_text(encoding="utf-8")
    wrappers = "\n\n".join((block(src, "void rx_nvn_read_texels(const nvn_tex *t,"),
                            block(src, "void rx_nvn_write_texels(const nvn_tex *t,")))
    fixture = HARNESS.replace("@WRAPPERS@", wrappers)
    compiler = shutil.which(args.cxx)
    if not compiler:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    with tempfile.TemporaryDirectory(prefix="vcore-texels-") as temporary:
        folder = Path(temporary)
        (folder / "fixture.cpp").write_text(fixture, encoding="utf-8")
        exe = folder / ("fixture.exe" if os.name == "nt" else "fixture")
        subprocess.run([compiler, "-std=c++17", "-O1", "-w", str(folder / "fixture.cpp"), "-o", str(exe)], check=True,
                       timeout=120)
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        print(result.stdout, end="")
        if result.returncode or "ok" not in result.stdout:
            raise SystemExit("a texel copy holds the virtual core while the back end waits for the replay")


if __name__ == "__main__":
    main()
