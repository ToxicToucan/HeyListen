# Copyright (C) 2026 Sacrappy
"""The cause of every stutter of a run, from the files the game leaves (standard library only).

    python -I engine/scripts/stutter_report.py <run dir> [--stalls <dir>] [--abs 50] [--delta 8] [--ratio 2] [--floor 16.7] [--csv <file>]

A run dir holds present_trace.csv (frame, return_ns, interval_ns) and game_log.txt. The game wants, in its log:
    HL_HITCH_LOG=rel:8     a "vk: hitch" line (the cause split) for each frame 8 ms and 1.25x over the median of the 30 before it
    HL_STALL_WATCH=rel:8   a "stallwatch: gap" block (per-thread CPU, page faults) and a stall_NN.jt dump for each such gap
    (the plain numbers, HL_HITCH_LOG=50 and HL_STALL_WATCH=40, still work: the report then has causes for the long ones only.
    The game keeps at most 20 hitch lines and 20 gap blocks a second and 80 dumps in all, the dumps going to the first gaps over
    HL_STALL_WATCH_AFTER seconds: a long run wants HL_STALL_WATCH_MAX raised.)

Stutter rule (STALL2, 22 Nov: at 2 ms and 1.25x the owner's 23 minutes gave 25,501 stutters, one frame in seven, mostly the normal
jitter of a 130 fps game whose 90th percentile is 1.6x its median): a frame is a stutter when its interval is over the median of the
30 frames before it by --delta ms (8) and by --ratio (2x) and over --floor ms (16.7, a missed 60 Hz refresh), or over --abs ms (50).
On that run this keeps 1,724 (75 a minute) and 89 percent of the time they cost. Classes: minor (under 3x the median), major
(3 to 6x), severe (over 6x, or over 100 ms).

How the lines are matched. The game's log has no time stamps, but its lines come in the order of the frames and a hitch line
carries the frame's length. The game's 20 a second cap drops lines (a quarter of them at the old rule), so the lines are a
sub-sequence of the stutters: the long stutters are matched first to the lines of the same length (within 1 ms or 3 percent) by a
longest common subsequence, then shorter levels fill the gaps between the pairs found (align(); the greedy match this replaced lost
the order after the first dropped line, and 99 of the 223 stutters of 50 ms or more went "unknown" with their line in the log). The
stall dumps share the trace's clock (their window ends at the present that ended the gap), so a dump is matched by time; a gap block
without a dump is matched like a hitch line, by order and length. The map at a stutter is the last "map:" line before its hitch line
(or gap block) in the log; "~" marks a map taken from the stutter before it, "?" a stutter before the first zone (the start-up)."""
import argparse
import csv
import os
import re
import sys
from collections import defaultdict

HITCH_RE = re.compile(
    r"vk: hitch (?P<ms>[\d.]+) ms: (?P<kind>[a-z_ ]+?)(?P<load> \(load\))? \| game (?P<game>[\d.]+) render (?P<render>[\d.]+) "
    r"gpu_wait (?P<gpu_wait>[\d.]+) pace (?P<pace>[\d.]+) gpu (?P<gpu>[\d.]+) \| pipe (?P<pipe>[\d.]+) xlate (?P<xlate>[\d.]+) "
    r"upload (?P<upload>[\d.]+) memsync (?P<memsync>[\d.]+) readback (?P<readback>[\d.]+) drvcomp (?P<drvcomp>[\d.]+) "
    r"present (?P<present>[\d.]+) \| desc (?P<desc>[\d.]+) .*?\| os_wait (?P<os_wait>[\d.]+): lock (?P<lock>[\d.]+) "
    r"sleep (?P<sleep>[\d.]+) vcore (?P<vcore>[\d.]+) file (?P<file>[\d.]+), faults (?P<faults>-?\d+), ws -(?P<ws>[\d.]+) MB")
GAP_RE = re.compile(r"stallwatch: gap (?P<ms>[\d.]+) ms between presents: page faults \+(?P<faults>\d+), working set (?P<ws>[+-]?\d+) MB")
THREAD_RE = re.compile(r"stallwatch:\s+(?P<name>\S+)\s+(?:(?P<mc>[\d.]+) Mcycles \(cpu ticks user (?P<u1>[\d.]+) kernel (?P<k1>[\d.]+) ms\)|"
                       r"cpu (?P<cpu>[\d.]+) ms \(user (?P<u2>[\d.]+) kernel (?P<k2>[\d.]+)\)); then at (?P<where>.*)$")
DUMP_RE = re.compile(r"stallwatch: (?P<n>\d+) records to (?P<path>.*stall_\d+\.jt)")
MAP_RE = re.compile(r"\] map: (?P<name>.+)$")
SKIP_RE = re.compile(r"(\d+) lines dropped|(\d+) gaps over the 20 a second cap")

# the job trace's record types (host/hos.h JT_*), as scratch-stall1's stallan.py reads them
EV_B, EV_E, EV_S, SPIN, CV_B, CV_E, CV_S, MX_B, MX_E, MX_G, SL_B, SL_E, VC_B, VC_E, Q_B, Q_E, Q_S, K_B, K_E, K_W, MARK = range(1, 22)
BEGIN = {EV_B: (EV_E, "event", EV_S), CV_B: (CV_E, "cv", CV_S), MX_B: (MX_E, "mutex", MX_G), SL_B: (SL_E, "sleep", None),
         VC_B: (VC_E, "vcore", None), Q_B: (Q_E, "queue", Q_S), K_B: (K_E, "kernel", K_W)}
END_TO_BEGIN = {v[0]: k for k, v in BEGIN.items()}

CAUSES = ["first-touch page faults / Windows trim", "shader or pipeline build", "texture upload", "readback wait", "GPU bound",
          "lock wait on the frame hand-off", "zone load", "guest sleep (the game's own SleepThread)", "other OS wait (kernel, queue)",
          "render thread busy, not split", "unknown"]


def median(v):
    v = sorted(v)
    n = len(v)
    return 0.0 if not n else (v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2]))


def read_trace(path):
    rows = []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            try:
                rows.append((int(r["frame"]), int(r["return_ns"]), int(r["interval_ns"])))
            except (KeyError, ValueError):
                continue
    return rows


def find_stutters(rows, abs_ms, delta, ratio, floor=0.0):
    """[(frame, t_s since the first frame, ms, median ms, class)], by the rule above"""
    out, ring = [], []
    t0 = rows[0][1] if rows else 0
    for frame, ret, iv in rows:
        ms = iv / 1e6
        if ms <= 0:
            continue
        med = median(ring) if len(ring) >= 5 else 0.0
        hit = ms >= abs_ms or (med > 0 and ms >= med + delta and ms >= med * ratio and ms >= floor)
        if hit:
            x = ms / med if med else 0.0
            cls = "severe" if (ms > 100 or (med and x > 6)) else "major" if (med and x >= 3) or (not med) else "minor"
            out.append({"frame": frame, "ret": ret, "t": (ret - t0) / 1e9, "ms": ms, "med": med, "x": x, "cls": cls})
        ring.append(ms)
        if len(ring) > 30:
            ring.pop(0)
    return out


def read_log(path):
    """hitch lines, stall blocks and map lines of game_log.txt, each with its line number (the log's own order)"""
    hitches, blocks, maps = [], [], []
    cur = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for no, line in enumerate(f):
            m = HITCH_RE.search(line)
            if m:
                d = {k: (float(v) if v and re.fullmatch(r"-?[\d.]+", v) else v) for k, v in m.groupdict().items()}
                d["load"] = bool(m.group("load"))
                d["no"] = no
                hitches.append(d)
                continue
            m = GAP_RE.search(line)
            if m:
                cur = {"no": no, "ms": float(m.group("ms")), "faults": int(m.group("faults")), "ws": int(m.group("ws")), "threads": [],
                       "dump": None}
                blocks.append(cur)
                continue
            m = THREAD_RE.search(line)
            if m and cur is not None:
                mc = float(m.group("mc")) if m.group("mc") else None
                cpu = float(m.group("u1") or m.group("u2") or 0) + float(m.group("k1") or m.group("k2") or 0)
                cur["threads"].append((m.group("name"), mc, cpu, m.group("where").strip()))
                continue
            m = DUMP_RE.search(line)
            if m and cur is not None and cur["dump"] is None:
                cur["dump"] = m.group("path")
                continue
            m = MAP_RE.search(line)
            if m:
                maps.append((no, m.group("name").strip()))
    return hitches, blocks, maps


def match_by_order(stutters, items, key_ms, tol_abs, tol_rel, look=4):
    """(the old greedy matching, kept for a segment too long for the alignment) each stutter, in order, takes the nearest item
    (same length) among the next few; returns {stutter index: item}"""
    got, p = {}, 0
    for i, s in enumerate(stutters):
        best, bd = None, None
        for j in range(p, min(len(items), p + look)):
            d = abs(key_ms(items[j]) - s["ms"])
            if d <= max(tol_abs, tol_rel * s["ms"]) and (bd is None or d < bd):
                best, bd = j, d
        if best is not None:
            got[i] = items[best]
            p = best + 1
    return got


ALIGN_LEVELS = (150.0, 60.0, 30.0, 16.0, 10.0, 6.0, 0.0)
ALIGN_CELLS = 2_500_000


def align(stutters, items, key_ms, tol_abs, tol_rel):
    """Match the stutters to the log's items (hitch lines or gap blocks) by length, keeping the order: returns {stutter index: item}.

    Both lists are in time order, but the game's rate cap drops lines, so the items are a sub-sequence of the stutters (plus a few
    the trace's own rule does not flag). A greedy match drifts after the first dropped line (STALL2, 22 Nov: 99 of the 223 stutters
    of 50 ms or more went "unknown" with the line in the log). So the long ones, rare and distinct, are matched first, by a longest
    common subsequence on the lengths; then each level (150, 60, 30, 16, 10, 6, 0 ms) fills the gaps between the pairs found so far."""
    sk = [s["ms"] for s in stutters]
    ik = [key_ms(x) for x in items]
    got = {}   # stutter index -> item index

    def tol(v):
        return max(tol_abs, tol_rel * v)

    def lcs(S, I):
        n, m = len(S), len(I)
        if not n or not m:
            return []
        if n * m > ALIGN_CELLS:
            sub = match_by_order([stutters[i] for i in S], [items[j] for j in I], key_ms, tol_abs, tol_rel)
            by_id = {id(items[j]): j for j in I}
            return [(S[k], by_id[id(it)]) for k, it in sub.items()]
        dp = [[0.0] * (m + 1) for _ in range(n + 1)]
        for a in range(n - 1, -1, -1):
            row, nxt, va = dp[a], dp[a + 1], sk[S[a]]
            for b in range(m - 1, -1, -1):
                best = nxt[b] if nxt[b] > row[b + 1] else row[b + 1]
                d = abs(ik[I[b]] - va)
                t = tol(va)
                if d <= t:
                    w = nxt[b + 1] + 1.0 - 0.5 * d / t
                    if w > best:
                        best = w
                row[b] = best
        out, a, b = [], 0, 0
        while a < n and b < m:
            d = abs(ik[I[b]] - sk[S[a]])
            if d <= tol(sk[S[a]]) and abs(dp[a][b] - (dp[a + 1][b + 1] + 1.0 - 0.5 * d / tol(sk[S[a]]))) < 1e-9:
                out.append((S[a], I[b]))
                a += 1
                b += 1
            elif dp[a + 1][b] >= dp[a][b + 1]:
                a += 1
            else:
                b += 1
        return out

    for thr in ALIGN_LEVELS:
        anchors = [(-1, -1)] + sorted(got.items()) + [(len(stutters), len(items))]
        for (s0, i0), (s1, i1) in zip(anchors, anchors[1:]):
            S = [i for i in range(s0 + 1, s1) if sk[i] >= thr]
            I = [j for j in range(i0 + 1, i1) if ik[j] >= thr - tol(thr)]
            for si, ii in lcs(S, I):
                got[si] = ii
    return {si: items[ii] for si, ii in got.items()}


def read_jt(path):
    """(window start, window end, thread names, records) of a stall_NN.jt"""
    names, recs, win = {}, [], None
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with f:
        for line in f:
            if not line.strip():
                continue
            if line[0] == "#":
                p = line.split()
                win = (int(p[3]), int(p[4]))
            elif line[0] == "T":
                _, i, _ptr, name = line.rstrip("\n").split(" ", 3)
                names[int(i)] = name
            else:
                try:
                    t, ty, th, a, b = line.split()
                    recs.append((int(t), int(ty), int(th), int(a, 16), int(b, 16)))
                except ValueError:
                    pass
    if not win:
        return None
    recs.sort(key=lambda r: r[0])
    return win[0], win[1], names, recs


def jt_summary(jt, min_ms=2.0):
    """what the presenting thread waited on in the gap: [(kind, ms, signalled by)], the longest first (stallan.py's reading)"""
    _, _, names, recs = jt
    open_, waits, signals = {}, defaultdict(list), defaultdict(list)
    for t, ty, th, a, b in recs:
        if ty in BEGIN:
            open_[(th, ty)] = (t, a)
        elif ty in END_TO_BEGIN:
            o = open_.pop((th, END_TO_BEGIN[ty]), None)
            if o:
                waits[th].append((o[0], t, BEGIN[END_TO_BEGIN[ty]][1], o[1]))
        elif ty in (EV_S, CV_S, MX_G, Q_S):
            signals[a].append((t, th))
    out = []
    for th, name in names.items():
        if "Presentation" not in name:
            continue
        for b, e, kind, key in waits[th]:
            if (e - b) / 1e6 < min_ms:
                continue
            sg = [s for s in signals.get(key, []) if b - 1000 <= s[0] <= e + 1000]
            who = names.get(sg[-1][1], "?") if sg else ("-" if kind in ("sleep", "vcore") else "no signal in the dump")
            out.append((kind, (e - b) / 1e6, who))
    out.sort(key=lambda w: -w[1])
    return out[:3]


def label(s, h, blk):
    """one cause label and the evidence for it: the hitch line's split first, the stall block's numbers where there is no line"""
    ms = s["ms"]
    ev = []
    if h:
        if h["load"]:
            return "zone load", "hitch line flagged (load)"
        build = h["pipe"] + h["xlate"] + h["drvcomp"]
        if h["faults"] >= 1000 and h["faults"] / ms >= 20 or h["ws"] >= 50:
            return CAUSES[0], f"{int(h['faults'])} page faults, working set -{h['ws']:.0f} MB, os_wait {h['os_wait']:.1f} ms"
        if build >= max(1.0, 0.3 * ms):
            return CAUSES[1], f"pipe {h['pipe']:.1f} xlate {h['xlate']:.1f} drvcomp {h['drvcomp']:.1f} ms"
        if max(h["upload"], h["desc"]) >= max(1.0, 0.3 * ms):
            return CAUSES[2], f"upload {h['upload']:.1f} desc {h['desc']:.1f} ms"
        if h["readback"] >= max(1.0, 0.3 * ms):
            return CAUSES[3], f"readback {h['readback']:.1f} ms"
        if h["gpu_wait"] >= 0.4 * ms or h["gpu"] >= 0.7 * ms:
            return CAUSES[4], f"gpu_wait {h['gpu_wait']:.1f} gpu {h['gpu']:.1f} ms"
        if h["lock"] >= 0.5 * ms:
            return CAUSES[5], f"os_wait lock {h['lock']:.1f} of {ms:.1f} ms, {int(h['faults'])} faults"
        if h["sleep"] >= 0.5 * ms:
            return CAUSES[7], f"os_wait sleep {h['sleep']:.1f} of {ms:.1f} ms"
        if h["os_wait"] >= 0.5 * ms:
            return CAUSES[8], f"os_wait {h['os_wait']:.1f} of {ms:.1f} ms (lock {h['lock']:.1f} sleep {h['sleep']:.1f} vcore {h['vcore']:.1f} file {h['file']:.1f})"
        if h["render"] >= 0.5 * ms:
            return CAUSES[9], f"render {h['render']:.1f} of {ms:.1f} ms, none of it pipe/xlate/upload/memsync/readback/gpu_wait"
        ev.append(f"game {h['game']:.1f} render {h['render']:.1f} gpu_wait {h['gpu_wait']:.1f} os_wait {h['os_wait']:.1f} "
                  f"(lock {h['lock']:.1f} sleep {h['sleep']:.1f} vcore {h['vcore']:.1f} file {h['file']:.1f}) faults {int(h['faults'])}")
    if blk:
        if blk["faults"] >= 1000 and blk["faults"] / ms >= 20 or blk["ws"] >= 50:
            return CAUSES[0], f"stall watch: {blk['faults']} page faults, working set {blk['ws']:+d} MB"
        ev.append(f"stall watch: {blk['faults']} faults, ws {blk['ws']:+d} MB")
    if not h and not blk:
        ev.append("no hitch line and no stall block matched (HL_HITCH_LOG=rel:2 / HL_STALL_WATCH=rel:2 not on, or a rate cap dropped it)")
    return "unknown", "; ".join(ev)


def stall_text(blk, jt):
    parts = []
    if blk:
        top = sorted(blk["threads"], key=lambda t: -(t[1] if t[1] is not None else t[2]))[:3]
        if top:
            parts.append("busy: " + ", ".join(f"{n} {(f'{mc:.1f} Mcycles' if mc is not None else f'{cpu:.1f} ms')} at {w}" for n, mc, cpu, w in top))
        parts.append(f"faults +{blk['faults']}, ws {blk['ws']:+d} MB")
    if jt:
        ws = jt_summary(jt)
        if ws:
            parts.append("present thread waited: " + ", ".join(f"{k} {ms:.1f} ms (signalled by {w})" for k, ms, w in ws))
    return "; ".join(parts)


def hitch_text(h):
    if not h:
        return ""
    keys = ["game", "render", "gpu_wait", "pace", "gpu", "pipe", "xlate", "upload", "memsync", "readback", "drvcomp", "present", "desc", "os_wait"]
    top = sorted(((k, h[k]) for k in keys if h[k] >= 0.5), key=lambda kv: -kv[1])[:5]
    s = "hitch: " + " ".join(f"{k} {v:.1f}" for k, v in top)
    if h["os_wait"] >= 0.5:
        s += f" | os_wait: lock {h['lock']:.1f} sleep {h['sleep']:.1f} vcore {h['vcore']:.1f} file {h['file']:.1f}"
    return s + f" | faults {int(h['faults'])}, ws -{h['ws']:.0f} MB"


def build(run, stalls, abs_ms, delta, ratio, floor=0.0):
    rows = read_trace(os.path.join(run, "present_trace.csv"))
    st = find_stutters(rows, abs_ms, delta, ratio, floor)
    log = os.path.join(run, "game_log.txt")
    hitches, blocks, maps = read_log(log) if os.path.exists(log) else ([], [], [])
    by_h = align(st, hitches, lambda h: h["ms"], 1.0, 0.03)
    # (the dumps by time: a dump's window ends at the present that ended its gap)
    jts, by_b = {}, {}
    for b in blocks:
        if b["dump"]:
            cand = [os.path.join(stalls, os.path.basename(b["dump"])) if stalls else None, b["dump"], os.path.join(run, os.path.basename(b["dump"]))]
            for c in cand:
                if c and os.path.exists(c):
                    jt = read_jt(c)
                    if jt:
                        jts[id(b)] = jt
                    break
    ends = sorted((jts[id(b)][1], b) for b in blocks if id(b) in jts)
    for i, s in enumerate(st):
        near = [(abs(e - s["ret"]), b) for e, b in ends if abs(e - s["ret"]) <= 5_000_000]
        if near:
            by_b[i] = min(near, key=lambda x: x[0])[1]
    rest = [b for b in blocks if id(b) not in jts]
    left = [i for i in range(len(st)) if i not in by_b]
    for k, b in align([st[i] for i in left], rest, lambda b: b["ms"], 5.0, 0.15).items():
        by_b[left[k]] = b
    out, last_map = [], "?"
    for i, s in enumerate(st):
        h, b = by_h.get(i), by_b.get(i)
        no = h["no"] if h else (b["no"] if b else None)
        if no is not None:
            before = [n for ln, n in maps if ln < no]
            mp = before[-1] if before else "?"
            last_map = mp
        else:
            mp = "~" + last_map if last_map != "?" else "?"
        cause, why = label(s, h, b)
        out.append({**s, "map": mp, "cause": cause, "why": why, "hitch": hitch_text(h), "stall": stall_text(b, jts.get(id(b)) if b else None),
                    "matched": ("hitch" if h else "") + ("+" if h and b else "") + ("stall" if b else "")})
    return rows, out


def mmss(t):
    return f"{int(t) // 60:02d}:{t - int(t) // 60 * 60:04.1f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--stalls", help="the folder of the stall_NN.jt dumps (HL_STALL_WATCH_DIR); default: where the log says")
    ap.add_argument("--abs", "--ms", dest="abs", type=float, default=50.0, help="a frame over this many ms is a stutter (default 50)")
    ap.add_argument("--delta", type=float, default=8.0, help="ms over the median of the 30 frames before (default 8)")
    ap.add_argument("--ratio", type=float, default=2.0, help="times the median (default 2)")
    ap.add_argument("--floor", type=float, default=16.7, help="no frame under this many ms is a stutter, 0: none (default 16.7)")
    ap.add_argument("--csv", help="the CSV file (default: <run dir>/stutter_report.csv)")
    a = ap.parse_args(argv)
    rows, out = build(a.run, a.stalls, a.abs, a.delta, a.ratio, a.floor)
    if not rows:
        print("no frames in", os.path.join(a.run, "present_trace.csv"))
        return 1
    csv_path = a.csv or os.path.join(a.run, "stutter_report.csv")
    cols = ["frame", "time", "ms", "median_ms", "x_median", "class", "map", "cause", "evidence", "hitch", "stall", "matched"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for s in out:
            w.writerow([s["frame"], mmss(s["t"]), f"{s['ms']:.1f}", f"{s['med']:.1f}", f"{s['x']:.2f}", s["cls"], s["map"], s["cause"], s["why"],
                        s["hitch"], s["stall"], s["matched"]])
    span = (rows[-1][1] - rows[0][1]) / 1e9
    print(f"# Stutters of {a.run}\n")
    print(f"{len(rows)} frames over {mmss(span)}, median frame {median([r[2] / 1e6 for r in rows if r[2]]):.2f} ms; "
          f"stutter = {a.delta:g} ms and {a.ratio:g}x over the median of the 30 before and over {a.floor:g} ms, or over {a.abs:g} ms; {len(out)} stutters.\n")
    print("| time | ms | x med | class | map | cause | evidence |\n|---|---|---|---|---|---|---|")
    for s in out:
        extra = " ".join(x for x in (s["hitch"], s["stall"]) if x)
        print(f"| {mmss(s['t'])} | {s['ms']:.1f} | {s['x']:.1f} | {s['cls']} | {s['map']} | {s['cause']} | {s['why']}{'; ' + extra if extra else ''} |")
    print("\n## By cause\n\n| cause | minor | major | severe | count | total ms |\n|---|---|---|---|---|---|")
    agg = defaultdict(lambda: [0, 0, 0, 0.0])
    for s in out:
        c = agg[s["cause"]]
        c[("minor", "major", "severe").index(s["cls"])] += 1
        c[3] += s["ms"]
    for cause in CAUSES:
        if cause in agg:
            c = agg[cause]
            print(f"| {cause} | {c[0]} | {c[1]} | {c[2]} | {c[0] + c[1] + c[2]} | {c[3]:.0f} |")
    print("\n## By 5-minute window\n\n| window | minor | major | severe | stutters | total ms |\n|---|---|---|---|---|---|")
    win = defaultdict(lambda: [0, 0, 0, 0.0])
    for s in out:
        c = win[int(s["t"] // 300)]
        c[("minor", "major", "severe").index(s["cls"])] += 1
        c[3] += s["ms"]
    for k in range(int(span // 300) + 1):
        c = win.get(k, [0, 0, 0, 0.0])
        print(f"| {k * 5:02d}-{k * 5 + 5:02d} min | {c[0]} | {c[1]} | {c[2]} | {c[0] + c[1] + c[2]} | {c[3]:.0f} |")
    print(f"\nCSV: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
