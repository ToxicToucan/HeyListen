"""A whole-file decode of at least one maximum zstd block gives the thread's virtual core up for the libzstd call.

host/hl_stream.c hl_decode_file's whole-file decode is cut out of the source and run against stand-ins: a decode whose
output or input is 128 KiB (ZSTD_BLOCKSIZE_MAX) or more is made with the core given up and the core taken again after it
(only when the thread held one: hos_vcore_away's answer), a smaller one keeps the core, and HL_STREAM_AWAY=0 keeps it
for every size, as before. Both zstd entry points (with and without the game's dictionary) are covered.

    python -I -B tests/test_stream_decode_away.py [--cc clang]

Ported from Longshot 7daafa7a (#1553, HumanBean), d87dd600 / a8947062 (hos_zstd.c's version of the same hand-off); the
fixture is HeyListen's (its decode is host/hl_stream.c, not hos_zstd.c).
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srccut import between, block

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define ZSTD_BLOCKSIZE_MAX (128 * 1024)
typedef struct ZSTD_DDict_s ZSTD_DDict;
typedef struct ZSTD_DCtx_s ZSTD_DCtx;
static int core = 1, held_at_call, calls, away_calls, back_calls, ok_state = 1;
static int hos_vcore_away(void) { int h = core; core = 0; away_calls++; return h; }
static void hos_vcore_back(void) { if (core) ok_state = 0; core = 1; back_calls++; }
static ZSTD_DCtx *thread_dctx(void) { return (ZSTD_DCtx *)1; }
static uint8_t arena[16];
#define GP(a) ((void *)arena)
#define HOS_WRITTEN(p, n) ((void)(p), (void)(n))
static size_t ZSTD_decompress_usingDDict(ZSTD_DCtx *z, void *d, size_t n, const void *s, size_t c, const ZSTD_DDict *dd) {
    (void)z, (void)d, (void)s, (void)dd; held_at_call = core; calls++; return n + c * 0;
}
static size_t ZSTD_decompressDCtx(ZSTD_DCtx *z, void *d, size_t n, const void *s, size_t c) {
    (void)z, (void)d, (void)s; held_at_call = core; calls++; return n + c * 0;
}
static int stream_away(void) { const char *e = getenv("HL_STREAM_AWAY"); return !(e && e[0] == '0' && !e[1]); }
static size_t run(ZSTD_DDict *dd, uint64_t dest, size_t size, const uint8_t *s, size_t csize) {
    size_t r = 0;
    /* DECODE */
    return r;
}
static int check(int hold, int dict, size_t size, size_t csize, int want_away) {
    core = hold, held_at_call = -1, calls = away_calls = back_calls = 0, ok_state = 1;
    run(dict ? (ZSTD_DDict *)8 : NULL, 0, size, (const uint8_t *)arena, csize);
    if (calls != 1 || !ok_state) return 1;
    if (want_away && hold) return held_at_call != 0 || away_calls != 1 || back_calls != 1 || core != 1;
    if (want_away) return away_calls != 1 || back_calls != 0 || core != 0;   /* (no core held: none taken after) */
    return held_at_call != hold || away_calls != 0 || back_calls != 0 || core != hold;
}
int main(void) {
    const int off = getenv("HL_STREAM_AWAY") != NULL;
    const size_t big = ZSTD_BLOCKSIZE_MAX, small = ZSTD_BLOCKSIZE_MAX - 1;
    for (int dict = 0; dict < 2; dict++) {
        if (check(1, dict, big, 100, !off) || check(1, dict, 100, big, !off) || check(1, dict, 8 << 20, 3 << 20, !off)) return 10 + dict;
        if (check(1, dict, small, small, 0)) return 20 + dict;
        if (!off && check(0, dict, big, big, 1)) return 30 + dict;
    }
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC", "clang"))
    args = parser.parse_args()
    source = (ROOT / "host/hl_stream.c").read_text()
    func = block(source, "static void hl_decode_file(")
    whole = between(func, "        if (h.dictID && !dd) r = (size_t)-ZSTD_error_dictionary_wrong;\n        else {", "        int32_t e = 0;")
    assert "hos_vcore_away" in whole and "hos_vcore_back" in whole, "the whole-file decode does not hand off the core"
    # (the block's own else-branch: the decode and its hand-off)
    decode = whole[whole.index("else {") + len("else {"):whole.rindex("}")]
    with tempfile.TemporaryDirectory() as tmp:
        c, exe = Path(tmp) / "fixture.c", Path(tmp) / "fixture.exe"
        c.write_text(FIXTURE.replace("/* DECODE */", decode.replace("s, csize, dd)", "s, csize, dd)")))
        subprocess.run([args.cc, "-std=gnu11", "-w", str(c), "-o", str(exe)], check=True)
        env = {k: v for k, v in os.environ.items() if k != "HL_STREAM_AWAY"}
        for name, e in (("on", env), ("HL_STREAM_AWAY=0", dict(env, HL_STREAM_AWAY="0"))):
            r = subprocess.run([str(exe)], env=e, timeout=20)
            if r.returncode:
                raise SystemExit("FAIL: whole-file decode core hand-off (%s): case %d" % (name, r.returncode))
    print("PASS: a decode of 128 KiB or more gives the virtual core up for libzstd and takes it back; smaller ones and HL_STREAM_AWAY=0 keep it")


if __name__ == "__main__":
    main()
