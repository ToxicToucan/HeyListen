# Copyright (C) 2026 Sacrappy
"""A/B report of HL_REGION_KEEP runs: python -I abrep.py A=<run>,<run> B=<run>,<run>   (run = build/s5/<name> folders)
Per run: the zone changes read from the log (map: lines placed in time between matched hitch lines), fps from play+10 s, the stutters
(8 ms, 2x, 16.7 ms rule) from play on: all, page-fault class, 50 ms and over, and in the 30 s after each map line (-2 s .. +30 s):
count and excess ms (frame minus the median before it); the discards and keeps the game logged."""
import bisect
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stutter_report as sr


def one(run):
    run = run.rstrip('/\\')
    rows = sr.read_trace(run + '/present_trace.csv')
    log = open(run + '/game_log.txt', encoding='utf-8', errors='replace').read().split('\n')
    play = next((int(re.search(r'play: began at clock (\d+)', l).group(1)) for l in log if 'play: began' in l), rows[0][1])
    t0 = rows[0][1]
    hitches, blocks, maps = sr.read_log(run + '/game_log.txt')
    st_all = sr.find_stutters(rows, 50, 2, 1.25)
    by = sr.align(st_all, hitches, lambda h: h['ms'], 1.0, 0.03)
    pairs = [(h['no'], st_all[i]['ret']) for i, h in by.items()]
    for no, l in enumerate(log):   # (lines with a clock: the play line, HL_FAULT_WATCH's seconds)
        m = re.search(r'play: began at clock (\d+)', l) or re.search(r'hl fault: t=\d+ s ns=(\d+)', l)
        if m: pairs.append((no, int(m.group(1))))
    pairs.sort()
    nos = [p[0] for p in pairs]

    def t_of(no):
        k = bisect.bisect_left(nos, no)
        a = pairs[k - 1][1] if k > 0 else rows[0][1]
        b = pairs[k][1] if k < len(pairs) else rows[-1][1]
        return (a + b) // 2
    mt = [(t_of(no), name) for no, name in maps]
    _, out = sr.build(run, run + '-stalls', 50, 8, 2, 16.7)
    pl = [s for s in out if s['ret'] >= play]
    fr = [r for r in rows if r[1] >= play + 10_000_000_000]
    fps = (len(fr) - 1) / ((fr[-1][1] - fr[0][1]) / 1e9) if len(fr) > 2 else 0.0
    pfc = lambda s: s['cause'].startswith('first-touch')
    res = {'run': run, 'fps': fps, 'maps': [(round((t - play) / 1e9, 1), n) for t, n in mt],
           'all': len(pl), 'all_ms': sum(s['ms'] - s['med'] for s in pl), 'pf': sum(1 for s in pl if pfc(s)),
           'pf_ms': sum(s['ms'] - s['med'] for s in pl if pfc(s)), 'big': sum(1 for s in pl if s['ms'] >= 50)}
    w = [s for s in pl if any(t - 2e9 <= s['ret'] < t + 30e9 for t, _ in mt[1:])]   # (the first map line is the start's load)
    res.update(w_all=len(w), w_ms=sum(s['ms'] - s['med'] for s in w), w_pf=sum(1 for s in w if pfc(s)), w_pf_ms=sum(s['ms'] - s['med'] for s in w if pfc(s)))
    res['keep'] = [l[l.index('hl keep:'):][:210] for l in log if 'hl keep: this second' in l][-1:]
    res['discards'] = sum(int(m.group(1)) for l in log for m in [re.search(r'discards of freed guest pages this second: \d+ \((\d+) MB', l)] if m)
    return res


def main():
    groups = {}
    for a in sys.argv[1:]:
        k, v = a.split('=', 1)
        groups[k] = [one(r) for r in v.split(',')]
    for k, rs in groups.items():
        print('==', k)
        for r in rs:
            print(' ', r['run'].split('/')[-1], 'fps(play+10s) %.1f' % r['fps'], '| stutters from play: all %d (%.0f ms) pf %d (%.0f ms) >=50ms %d'
                  % (r['all'], r['all_ms'], r['pf'], r['pf_ms'], r['big']), '| 30 s after map lines (not the first): all %d (%.0f ms) pf %d (%.0f ms)'
                  % (r['w_all'], r['w_ms'], r['w_pf'], r['w_pf_ms']), '| discards logged %d MB' % r['discards'])
            print('     maps (s from play):', ', '.join('%s %s' % (t, n.split(' ')[0]) for t, n in r['maps']))
            if r['keep']: print('     ', r['keep'][0])
        n = len(rs)
        mean = lambda f: sum(f(r) for r in rs) / n
        print('  mean: fps %.1f, all %.1f (%.0f ms), pf %.1f (%.0f ms), 50ms+ %.1f, window all %.1f (%.0f ms), window pf %.1f (%.0f ms)' % (
            mean(lambda r: r['fps']), mean(lambda r: r['all']), mean(lambda r: r['all_ms']), mean(lambda r: r['pf']), mean(lambda r: r['pf_ms']),
            mean(lambda r: r['big']), mean(lambda r: r['w_all']), mean(lambda r: r['w_ms']), mean(lambda r: r['w_pf']), mean(lambda r: r['w_pf_ms'])))


if __name__ == '__main__':
    main()
