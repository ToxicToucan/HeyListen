# Copyright (C) 2026 Sacrappy
"""The per-stutter report (scripts/stutter_report.py) on small made-up files, and the game side it needs: HL_HITCH_LOG=rel:<ms>
(its test, compiled from the source: the median of the 30 before, 1.25x, the 20 a second cap), HL_STALL_WATCH=rel:<ms> (the
source's shape), RX_VK_ACCUM_RETIRE on by default.
    python -I tests/test_stutter_report.py [--cxx g++]"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import stutter_report as sr   # noqa: E402


def fail(msg):
    raise SystemExit("FAIL: " + msg)


def block(source, marker):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


HITCH = ("[hos Presentation Thread] vk: hitch {ms} ms: {cause}{load} | game {game} render 1.0 gpu_wait {gw} pace 0.0 gpu 1.0 | pipe {pipe} "
         "xlate 0.0 upload {up} memsync 0.1 readback {rb} drvcomp 0.0 present 0.1 | desc 0.0 (build 0.0 pool 0.0, tex 0.0 in upload) | "
         "os_wait {ow}: lock {lock} sleep 0.0 vcore 0.0 file 0.0, faults {faults}, ws -{ws} MB (lock)")


def hitch(ms, cause, game=1.0, gw=0.0, pipe=0.0, up=0.0, rb=0.0, ow=0.0, lock=0.0, faults=5, ws=0, load=""):
    return HITCH.format(ms=ms, cause=cause, load=load, game=game, gw=gw, pipe=pipe, up=up, rb=rb, ow=ow, lock=lock, faults=faults, ws=ws)


def make_rows():
    # (8 ms frames; a 12 ms frame (minor), a 28 ms (major), a 60 ms and a 120 ms (severe), a 9 ms: no stutter)
    ivs = [8.0] * 40 + [12.0] + [8.0] * 35 + [28.0] + [8.0] * 35 + [60.0] + [8.0] * 35 + [120.0] + [8.0] * 10 + [9.0] + [8.0] * 5
    t, rows = 1_000_000_000, []
    for i, ms in enumerate(ivs, 1):
        t += int(ms * 1e6)
        rows.append((i, t, int(ms * 1e6)))
    return rows


def write_trace(d, rows):
    with open(os.path.join(d, "present_trace.csv"), "w") as f:
        f.write("frame,return_ns,interval_ns\n")
        for r in rows:
            f.write("%d,%d,%d\n" % r)


def test_report():
    rows = make_rows()
    st = sr.find_stutters(rows, 50, 2, 1.25)
    got = [(round(s["ms"]), s["cls"]) for s in st]
    want = [(12, "minor"), (28, "major"), (60, "severe"), (120, "severe")]
    if got != want:
        fail(f"classes {got}, wanted {want}")
    with tempfile.TemporaryDirectory() as d:
        write_trace(d, rows)
        lines = ["[hos MainThread] boot", "[hos BaseProcExecutor0] map: Market_Child (child)",
                 hitch(12.0, "os_wait", game=11.0, ow=10.0, lock=9.0, faults=3),
                 "[hos BaseProcExecutor1] map: HyruleField_Child (child)",
                 hitch(28.1, "texture_upload", up=20.0),
                 "[hos host] stallwatch: gap 60.2 ms between presents: page faults +9000, working set +30 MB since the 40 ms mark; each thread's CPU since the mark and where it was then",
                 "[hos host] stallwatch:   ARes_Loading_0               12.5 Mcycles (cpu ticks user 0.0 kernel 15.6 ms); then at ntdll.dll+0x1659c4",
                 hitch(60.1, "os_wait", game=59.0, ow=58.0, lock=50.0, faults=9000, ws=3),
                 hitch(120.0, "readback", rb=100.0, gw=110.0)]
        Path(d, "game_log.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        csv_path = os.path.join(d, "out.csv")
        r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/stutter_report.py"), d, "--csv", csv_path, "--delta", "2", "--ratio", "1.25", "--floor", "0"],
                           capture_output=True, text=True)
        if r.returncode:
            fail("the report failed: " + r.stderr[-800:])
        out = r.stdout
        rows_md = [ln for ln in out.splitlines() if re.match(r"\| \d\d:", ln)]
        if len(rows_md) != 4:
            fail("wanted 4 stutters in the table, got:\n" + out)
        want = [("minor", "Market_Child", "lock wait on the frame hand-off"), ("major", "HyruleField_Child", "texture upload"),
                ("severe", "HyruleField_Child", "first-touch page faults / Windows trim"), ("severe", "HyruleField_Child", "readback wait")]
        for ln, (cls, mp, cause) in zip(rows_md, want):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if cells[3] != cls or mp not in cells[4] or cells[5] != cause:
                fail(f"row {cells[:6]}, wanted {cls} / {mp} / {cause}")
        if "ARes_Loading_0 12.5 Mcycles" not in out:
            fail("the stall block's thread is not in the evidence")
        for part in ("## By cause", "## By 5-minute window", "| 00-05 min | 1 | 1 | 2 | 4 |"):
            if part not in out:
                fail("the summary lacks: " + part)
        if len(Path(csv_path).read_text(encoding="utf-8").splitlines()) != 5:
            fail("the CSV has not one line a stutter")
    # (a run with no game log: every stutter unknown, with the way to get its cause)
    with tempfile.TemporaryDirectory() as d:
        write_trace(d, rows)
        r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/stutter_report.py"), d], capture_output=True, text=True)
        if r.returncode or "HL_HITCH_LOG=rel:2" not in r.stdout or not os.path.exists(os.path.join(d, "stutter_report.csv")):
            fail("a run without a log: " + r.stderr[-400:])


def test_default_rule_and_align():
    """STALL2: the default rule (8 ms, 2x, 16.7 ms) skips the 12 ms jitter and keeps the 28 ms frame; align() keeps the order across
    the lines the game's rate cap dropped"""
    rows = make_rows()
    got = [round(s["ms"]) for s in sr.find_stutters(rows, 50, 8, 2, 16.7)]
    if got != [28, 60, 120]:
        fail(f"the default rule flagged {got}, wanted [28, 60, 120]")
    # 300 stutters of 6 to 12 ms (ms = 6 + i % 7) with every third line dropped, and three long ones in between: all of them match
    st, items = [], []
    for i in range(300):
        ms = 6.0 + i % 7 + (0.1 if i % 11 == 0 else 0.0)
        st.append({"ms": ms})
        if i % 3:
            items.append({"ms": ms + 0.05})
    for at, ms in ((40, 150.0), (150, 400.0), (260, 75.0)):
        st.insert(at, {"ms": ms})
        items.insert(at * 2 // 3, {"ms": ms})
    by = sr.align(st, items, lambda x: x["ms"], 1.0, 0.03)
    for k, s in enumerate(st):
        if s["ms"] >= 50 and by.get(k, {}).get("ms") != s["ms"]:
            fail(f"the long stutter {s['ms']} did not match its line: {by.get(k)}")
    wrong = sum(1 for k, it in by.items() if abs(it["ms"] - st[k]["ms"]) > 1.0)
    if wrong or len(by) < 190:
        fail(f"align: {wrong} wrong pairs, {len(by)} matched of 203 expected")
    pos = [k for k in sorted(by)]
    where = {id(it): n for n, it in enumerate(items)}
    idx = [where[id(by[k])] for k in pos]
    if idx != sorted(idx):
        fail("align broke the order")


FIXTURE_MAIN = r'''
int main() {
    HitchRel h;
    int64_t dropped = 0, t = 5000000000LL;
    for (int i = 0; i < 40; i++) if (h.test(8.0f, 2.0, t += 8000000, &dropped)) { std::printf("steady frame logged\n"); return 1; }
    if (h.test(9.5f, 2.0, t += 8000000, &dropped)) { std::printf("1.5 ms over logged\n"); return 1; }
    if (!h.test(10.1f, 2.0, t += 8000000, &dropped)) { std::printf("2.1 ms over and 1.26x not logged\n"); return 1; }
    HitchRel g;   // (a median near 100 ms: +10 ms is under 1.25x)
    for (int i = 0; i < 40; i++) g.test(100.0f, 2.0, t += 100000000, &dropped);
    if (g.test(110.0f, 2.0, t += 100000000, &dropped)) { std::printf("1.1x logged\n"); return 1; }
    if (!g.test(130.0f, 2.0, t += 100000000, &dropped)) { std::printf("1.3x not logged\n"); return 1; }
    HitchRel k;
    for (int i = 0; i < 40; i++) k.test(8.0f, 2.0, 9000000000LL, &dropped);
    int logged = 0;
    for (int i = 0; i < 50; i++) {   // (50 stutters in one second, the median held by the frames between)
        if (k.test(30.0f, 2.0, 9000000000LL + i, &dropped)) logged++;
        for (int j = 0; j < 31; j++) k.test(8.0f, 2.0, 9000000000LL + i, &dropped);
    }
    if (logged != 20) { std::printf("%d lines in one second, wanted 20\n", logged); return 1; }
    if (!k.test(30.0f, 2.0, 10000000000LL, &dropped) || dropped != 30) { std::printf("dropped %lld, wanted 30\n", (long long)dropped); return 1; }
    std::printf("ok\n");
    return 0;
}
'''


def test_game_side(cxx):
    pr = (ROOT / "gpu/vk/perf_report.cpp").read_text(encoding="utf-8")
    cfg = block(pr, "struct HitchRel {") + ";"
    for need in ('"rel:"', "HitchRel hitch_rel", "hitch_by_rel", "the 20 a second cap"):
        if need not in pr:
            fail("perf_report.cpp lacks " + need)
    fixture = "#include <algorithm>\n#include <cstdint>\n#include <cstdio>\n" + cfg + FIXTURE_MAIN
    with tempfile.TemporaryDirectory() as tmp:
        cpp, exe = os.path.join(tmp, "h.cpp"), os.path.join(tmp, "h.exe")
        Path(cpp).write_text(fixture, encoding="utf-8")
        r = subprocess.run([cxx, "-std=c++17", "-O1", cpp, "-o", exe], capture_output=True, text=True)
        if r.returncode:
            fail("the HitchRel fixture does not compile:\n" + r.stderr[-2000:])
        r = subprocess.run([exe], capture_output=True, text=True)
        if r.returncode:
            fail("HitchRel: " + r.stdout.strip())
    jt = (ROOT / "host/hos_jobtrace.c").read_text(encoding="utf-8")
    for need in ('!strncmp(e, "rel", 3)', "sw_iv[30]", "HL_STALL_WATCH_MAX", "in_win >= 20", "th < med * 5 / 4", "th < 4000000LL"):
        if need not in jt:
            fail("hos_jobtrace.c lacks " + need)
    mem = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    if "const bool on = !e || atoi(e) != 0;" not in mem or "(default 1; =0 off)" not in mem:
        fail("RX_VK_ACCUM_RETIRE is not on by default")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cxx", default="g++")
    a = ap.parse_args()
    test_report()
    test_default_rule_and_align()
    test_game_side(a.cxx)
    print("PASS: the stutter report on made-up files, HL_HITCH_LOG=rel, HL_STALL_WATCH=rel, RX_VK_ACCUM_RETIRE on by default")


if __name__ == "__main__":
    main()
