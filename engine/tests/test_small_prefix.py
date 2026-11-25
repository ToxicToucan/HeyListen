# Copyright (C) 2026 Sacrappy

"""(stream SMALL1) RX_VK_SMALL_PREFIX: the small command buffers before a big one are dry-passed in place and recorded by the first job
of the big one's early group; every other path records them whole, in order, as before.

By the text of gpu/vk/vk_replay.cpp:
- the switch is off unless RX_VK_SMALL_PREFIX is set non-zero;
- a command buffer is only left pending when it is one segment, in a burst, its dry pass had no occlusion stop (a wait it stopped at ends its
  segment), and it has RX_VK_SMALL_PREFIX_MIN work or one is pending already, with the state at its start copied to the heap;
- every place that records or hands on a recording first records what is pending, in order (the serial path, the single-segment path, the
  multi-segment path, run_submit's end outside a burst, finish_recording), and the early path is the only one that takes it (as the first job, whose segments are
  recorded in order by early_record_job);
- the early path's indices are shifted by the prefix job (P): the plan, the open segment, the first of the big command buffer's jobs, the
  render conditions continue (no reset) and the join's profile starts after the prefix;
- a dry pass that follows pending ones continues par_conds (cond0 = par_conds_n) instead of resetting it.
A model of the in-order submit of the jobs (the prefix first) and of the conditions' indices checks the order and that no index repeats.

python -I -B tests/test_small_prefix.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def function(source, marker):
    start = source.index(marker)
    end = source.index("{" + chr(10), start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def need(text, what, items):
    for it in items:
        if it not in text:
            raise SystemExit(what + " is not as this check knows it: " + it)


def model():
    """submits in order: jobs [prefix, big segments...]; conditions indices: each dry pass continues the list"""
    import random
    rnd = random.Random(7)
    for _ in range(2000):
        smalls = [rnd.randint(0, 6) for _ in range(rnd.randint(0, 20))]   # conditions each small command buffer's dry pass appends
        big = rnd.randint(0, 40)
        conds, starts = 0, []
        for n in smalls:
            starts.append(conds)
            conds += n
        big_start = conds if smalls else 0   # (replay_handle_early: no reset when there is a prefix)
        conds += big
        # every small's range ends where the next begins, the big's begins after the last small
        for i, st in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else big_start
            assert end - st == smalls[i]
        assert conds == sum(smalls) + big
        segments = rnd.randint(2, 6)
        jobs = (["pre"] if smalls else []) + ["s%d" % k for k in range(segments)]
        order = list(jobs)
        done = list(range(len(jobs)))
        rnd.shuffle(done)   # the jobs finish in any order; the submits go out in the jobs' order
        sent, nxt, finished = [], 0, set()
        for d in done:
            finished.add(d)
            while nxt in finished:
                sent.append(order[nxt])
                nxt += 1
        assert sent == order, (sent, order)


def main():
    src = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    need(src, "the switch", ['getenv("RX_VK_SMALL_PREFIX")', "const bool on = e && atoi(e) != 0;"])
    handle = function(src, "const uint8_t* replay_handle(VkCommandBuffer& cmd, const uint8_t* h, bool may_stop) {")
    need(handle, "replay_handle", [
        "if (rec_cur) small_pend_flush();",
        "const bool pend_before = !small_pend.empty();",
        "defer_group || pend_before ? par_conds_n.load(std::memory_order_acquire) : 0;",
        "} else if (pend_before) {",
        "if (!small_pend.empty() && nplan + 1 <= kMaxBatch) pre = std::move(small_pend), small_pend.clear();",
        "std::move(pre));",
        "if (seg.size() == 1 && small_prefix_on() && burst_open && !occ_stop && !defer_group && small_pend.size() < 24 &&",
        "(!small_pend.empty() || total >= small_prefix_min())) {",
        "e.g = {h, stop, e.start.get(), ub0, cond0, serial0};",
        "return stop;",
        "e.start = std::make_unique<NvnState>(start);",
        "small_pend.push_back(std::move(e));",
    ])
    if handle.index("if (seg.size() == 1 && small_prefix_on() && burst_open") > handle.index("if (seg.size() == 1) {   // (one segment"):
        raise SystemExit("the pending branch must come before the single-segment recording")
    # the single-segment and multi-segment paths record what is pending first
    single = handle[handle.index("if (seg.size() == 1) {   // (one segment"):]
    if single.index("small_pend_flush();") > single.index("record_segment(cmd, seg[0]);"):
        raise SystemExit("the single-segment path must flush the pending ones before it records")
    multi = handle[handle.index("small_pend_flush();   // (stream SMALL1: the small command buffers before it"):]
    if multi.index("submit_recording(r, false);") < 0:
        raise SystemExit("multi")
    if handle.index("small_pend_flush();   // (stream SMALL1: the small command buffers before it") > handle.index("submit_recording(r, false);"):
        raise SystemExit("the multi-segment path must flush before it submits what came before")
    early = function(src, "const uint8_t* replay_handle_early(VkCommandBuffer& cmd, const uint8_t* h, bool may_stop, int nplan, const int64_t* plan,")
    need(early, "replay_handle_early", [
        "std::vector<PendingSmall> pre) {",
        "const size_t P = pre.empty() ? 0 : 1;",
        "if (!P) par_conds_reset();",
        "const size_t cond0 = par_conds_n.load(std::memory_order_acquire);",
        "par_seq += (uint64_t)nplan + P;",
        "memory_rb_segments_begin(base, (uint64_t)nplan + P);",
        "G.first = P;",
        "for (int k = 0; k < nplan + (int)P; k++) {",
        "size_t open = P;",
        "jobs[0]->pre = std::move(pre);",
        "jobs[P]->g = {h, nullptr, &jobs[P]->s, ub0, cond0, serial0};",
        "(int)open + 1 < nplan + (int)P && work >= plan[open - P]",
        "if (used < 2 + P || !early) {",
        "texture_segment_end(*G.seg_table, (int)P);",
        "while (G.next_sub < P) early_submit_job(G, jobs[G.next_sub++].get());",
        "Segment whole = jobs[P]->g;",
    ])
    job = function(src, "void early_record_job(EarlyGroup& G, EarlyJob* j) {")
    need(job, "early_record_job", ["for (PendingSmall& e : j->pre) {", "record_segment(c, e.g);", "small_pre_mismatch.fetch_add"])
    join = function(src, "void early_join(EarlyGroup& G) {")
    need(join, "early_join", ["for (size_t k = G.first; k < used && k < (size_t)kMaxBatch && G.total > 0; k++, m++) {"])
    run = function(src, "bool run_submit(const uint64_t* handles, int n, uint64_t value, uint64_t queue, bool memory_work, int cb = 0,")
    need(run, "run_submit", ["if (!burst_open) small_pend_flush();"])
    fin = function(src, "void finish_recording(uint64_t value) {")
    if fin.index("small_pend_flush();") > fin.index("Recording& r = *rec_cur;"):
        raise SystemExit("finish_recording must record what is pending before it takes the recording")
    # RX_VK_MID_SEG: every place that counts a command buffer's segments widens a lone one by with_mid, from the same count
    if src.count("mkwvk::segment_plan::with_mid(") != 3 or 'getenv("RX_VK_MID_SEG")' not in src:
        raise SystemExit("RX_VK_MID_SEG must be applied at the early plan, the lean plan and the segment count")
    need(handle, "replay_handle", ["nseg = mkwvk::segment_plan::with_mid(nseg, total, seg_cap(), mid_seg_min());",
                                   "nplan = mkwvk::segment_plan::with_mid(nplan, last_total, seg_cap(), mid_seg_min());"])
    model()
    print("test_small_prefix: ok")


main()
