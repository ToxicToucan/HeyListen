# Copyright (C) 2026 Sacrappy

"""A texture's or a program's release gives the thread's virtual core up only while the back end would wait (host/rx_backend.c
rx_nvn_texture_released, rx_nvn_program_released, v0.4.4; issue 67: OptimizeChangeBanc held a core for 2-2.5 s inside the renderer).

The two actual wrappers are compiled with stand-ins; the back end's hint (nvn_release_may_wait) and HOS_RELEASE_KEEP_CORE decide:
- the hint says "would wait": the core is given up around the call and taken once after it;
- the hint says "free": the core is kept and no core is taken (thousands of releases a load must not pay a give-up);
- no hint in the table (NULL): the core is given up, as an invalidate does;
- HOS_RELEASE_KEEP_CORE=1: kept whatever the hint says; a thread without a core takes none; a release before the back end started
  is kept for later (no call, the core kept).
And by the text, that the back end's hint looks at both locks a release takes without taking them for good.

python -I -B tests/test_vcore_release.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
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
typedef struct { int x; } nvn_tex;
typedef struct { int x; } nvn_prog;
typedef struct { int kind, x; nvn_tex t; } early_call;
static int started = 1, kept;
static int keep_early(early_call *c) { (void)c; kept++; return 1; }
static int held, takes, calls, calls_with_core, hint_value = 1, have_hint = 1, hint_asked;
int hos_vcore_away(void) { if (!held) return 0; held = 0; return 1; }
void hos_vcore_back(void) { if (!held) held = 1, takes++; }
static int hint(void) { hint_asked++; return hint_value; }
static void rel_tex(const nvn_tex *t) { (void)t; calls++, calls_with_core += held; }
static void rel_prog(const nvn_prog *p) { (void)p; calls++, calls_with_core += held; }
static struct { void (*nvn_texture_released)(const nvn_tex *); void (*nvn_program_released)(const nvn_prog *); int (*nvn_release_may_wait)(void); }
    table = {rel_tex, rel_prog, hint}, *vk = &table;
#define wgr_nvn_texture_released rel_tex
#define wgr_nvn_program_released rel_prog
static int keep_env;
static int env_on(const char *name) { (void)name; return keep_env; }

@WRAPPERS@

static int bad;
static void check(int ok, const char *what) { if (!ok) { printf("FAIL: %s\n", what); bad = 1; } }
static void reset(int core) { held = core, takes = calls = calls_with_core = hint_asked = 0; }
int main(void) {
    nvn_tex t = {1};
    nvn_prog p = {1};
    // (the hint is looked at once a process for the switch's value: the first call decides it, so the order matters)
    reset(1), hint_value = 1;
    rx_nvn_texture_released(&t);
    check(calls == 1 && calls_with_core == 0 && takes == 1 && held == 1, "would wait: the texture release without the core, taken back once");
    reset(1);
    rx_nvn_program_released(&p);
    check(calls == 1 && calls_with_core == 0 && takes == 1 && held == 1, "would wait: the program release without the core, taken back once");
    reset(1), hint_value = 0;
    rx_nvn_texture_released(&t);
    rx_nvn_program_released(&p);
    check(calls == 2 && calls_with_core == 2 && takes == 0 && held == 1, "free: the core kept, none taken");
    reset(0), hint_value = 1;
    rx_nvn_texture_released(&t);
    check(calls == 1 && takes == 0 && held == 0, "no core held: none taken");
    reset(1), table.nvn_release_may_wait = 0;
    rx_nvn_texture_released(&t);
    check(calls == 1 && calls_with_core == 0 && takes == 1, "no hint in the table: the core given up");
    table.nvn_release_may_wait = hint;
    started = 0, reset(1);
    rx_nvn_texture_released(&t);
    check(kept == 1 && calls == 0 && held == 1 && takes == 0, "before the back end started: kept for later, the core kept");
    started = 1;
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
    wrappers = "\n\n".join((block(src, "static int release_gives_core(void) {"),
                            block(src, "void rx_nvn_texture_released(const nvn_tex *t) {"),
                            block(src, "void rx_nvn_program_released(const nvn_prog *p) {")))
    assert 'env_on("HOS_RELEASE_KEEP_CORE")' in wrappers, "the off switch is not read"
    fixture = HARNESS.replace("@WRAPPERS@", wrappers)
    compiler = shutil.which(args.cxx)
    if not compiler:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    with tempfile.TemporaryDirectory(prefix="vcore-release-") as temporary:
        folder = Path(temporary)
        (folder / "fixture.cpp").write_text(fixture, encoding="utf-8")
        exe = folder / ("fixture.exe" if os.name == "nt" else "fixture")
        subprocess.run([compiler, "-std=c++17", "-O1", "-w", str(folder / "fixture.cpp"), "-o", str(exe)], check=True, timeout=120)
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        print(result.stdout, end="")
        if result.returncode or "ok" not in result.stdout:
            raise SystemExit("a release holds the virtual core while the back end waits, or gives it up for nothing")
        # (HOS_RELEASE_KEEP_CORE=1 is read once: its own run)
        (folder / "keep.cpp").write_text(fixture.replace("static int keep_env;", "static int keep_env = 1;"), encoding="utf-8")
        exe2 = folder / ("keep.exe" if os.name == "nt" else "keep")
        subprocess.run([compiler, "-std=c++17", "-O1", "-w", str(folder / "keep.cpp"), "-o", str(exe2)], check=True, timeout=120)
        keep = subprocess.run([str(exe2)], capture_output=True, text=True, timeout=30)
        # (every case that expects a give-up fails with the switch on: the core is kept, never given up)
        assert keep.returncode != 0 and "FAIL: would wait" in keep.stdout, "HOS_RELEASE_KEEP_CORE=1 does not keep the core"
    # (the memory pool calls: a pool made, its flags, its finalize: the core given up around the back end's call, once each)
    for name in ("void rx_nvn_pool_flags(uint64_t gpu, uint64_t size, uint32_t flags) {",
                 "void rx_nvn_pool_finalized(uint64_t gpu, uint64_t size) {", "void rx_nvn_pool_created(uint64_t gpu, uint64_t size) {"):
        body = block(src, name)
        assert "pool_call_gives_core() ? hos_vcore_away() : 0" in body and body.count("hos_vcore_back()") == 1, name + " does not give the core up"
        assert body.index("hos_vcore_away()") < body.index("hos_vcore_back()")
    assert 'env_on("HOS_RELEASE_KEEP_CORE")' in block(src, "static int pool_call_gives_core(void) {")
    rp = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    hint = block(rp, "static int release_may_wait() {")
    assert "sync_order_mu.mu.try_lock()" in hint and "replay_mu.try_lock()" in hint and hint.count("unlock()") == 2, \
        "the hint does not look at both locks and let them go"
    assert "if (async_on()) return 0;" in hint and 'getenv("RX_VK_RELEASE_HINT")' in hint
    assert "t.nvn_release_may_wait = release_may_wait;" in rp
    api = (ROOT / "gpu/vk/vk_api.h").read_text(encoding="utf-8")
    assert "int (*nvn_release_may_wait)(void);" in api and api.index("nvn_release_may_wait") > api.index("option_set"), \
        "the hint is not appended at the table's end"
    print("PASS the hint")


if __name__ == "__main__":
    main()
