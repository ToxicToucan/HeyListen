# Copyright (C) 2026 Sacrappy
"""The submit sequencer of the replay's lanes (gpu/vk/submit_seq.h, DECOUPLE stage 2, RX_VK_LANES / RX_VK_LANES_CHECK).

A small program checks, with no GPU: the switch parse (unset or 0 off, clamped to 0..4); tickets in stream order; a unit finished in
order moves the head and counts nothing; a unit finished while an earlier one is open is counted out of order and the head waits for
the earlier one; a recording left ready is handed to whoever finishes the one before (ready_head), and only when it is the head; a
dropped unit lets the head past without being counted; a ticket finished twice, or never taken, is counted as unknown; the lanes'
pattern (a group pending, the spine's next recordings begun and left ready, the group's end sending them in order) reads 0 out of
order. And, by the text, that vk_replay.cpp takes the tickets where the spine begins a unit, finishes them where the unit goes to the
queue, and logs the check's counters.

python -I -B tests/test_submit_seq.py --cxx <compiler>
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CPP = r'''
#include "submit_seq.h"
#include <cstdio>
static int fails;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
int main() {
    using namespace submit_seq;
    CHECK(parse_lanes(nullptr) == 0 && parse_lanes("") == 0 && parse_lanes("0") == 0 && parse_lanes("-3") == 0);
    CHECK(parse_lanes("1") == 1 && parse_lanes("2") == 2 && parse_lanes("4") == kMaxLanes && parse_lanes("9") == kMaxLanes);
    {   // in order: nothing counted
        Sequencer s;
        const uint64_t a = s.take(), b = s.take(), c = s.take();
        CHECK(a == 1 && b == 2 && c == 3 && s.pending() == 3 && s.is_head(a) && !s.is_head(b));
        s.finish(a); s.finish(b); s.finish(c);
        CHECK(s.out_of_order() == 0 && s.pending() == 0 && s.finished() == 3 && s.unknown() == 0 && s.max_pending() == 3);
    }
    {   // out of order: counted, and the head waits for the earlier unit
        Sequencer s;
        const uint64_t a = s.take(), b = s.take();
        s.finish(b);
        CHECK(s.out_of_order() == 1 && s.head() == a && s.open(a) && !s.open(b));
        s.finish(a);
        CHECK(s.out_of_order() == 1 && s.pending() == 0);
    }
    {   // a recording left ready goes when the unit before it is finished, by whoever finishes it
        Sequencer s;
        const uint64_t g = s.take();       // a group pending (its segments recording)
        const uint64_t r1 = s.take(), r2 = s.take(), r3 = s.take();
        s.ready(r1); s.ready(r2);           // the spine made two ready: not the head, left
        CHECK(s.ready_head() == 0);
        s.finish(g);                        // the group's last segment sent
        CHECK(s.ready_head() == r1);
        s.finish(s.ready_head());
        CHECK(s.ready_head() == r2);
        s.finish(s.ready_head());
        CHECK(s.ready_head() == 0 && s.is_head(r3));   // r3 is still being recorded: not ready
        s.finish(r3);
        CHECK(s.out_of_order() == 0 && s.pending() == 0 && s.finished() == 4);
    }
    {   // a dropped unit (a group that recorded nothing of its own) lets the head past, uncounted, wherever it is
        Sequencer s;
        const uint64_t a = s.take(), g = s.take(), b = s.take();
        s.drop(g);
        CHECK(s.out_of_order() == 0 && s.head() == a && s.dropped() == 1);
        s.finish(a);
        CHECK(s.is_head(b));
        s.finish(b);
        CHECK(s.out_of_order() == 0 && s.pending() == 0);
    }
    {   // unknown tickets: 0, twice, never taken
        Sequencer s;
        const uint64_t a = s.take();
        s.finish(0); s.finish(a); s.finish(a); s.finish(7); s.ready(a);
        CHECK(s.unknown() == 4 && s.finished() == 1 && s.out_of_order() == 0);
    }
    {   // a long stream: the window stays as deep as the units open
        Sequencer s;
        for (int i = 0; i < 100000; i++) {
            const uint64_t g = s.take(), r = s.take();
            s.ready(r);
            s.finish(g);
            s.finish(s.ready_head());
        }
        CHECK(s.pending() == 0 && s.out_of_order() == 0 && s.max_pending() == 2 && s.finished() == 200000);
    }
    {   // the order thread's done marks: a submission whose end is pending holds them just before it
        DoneHold h;
        CHECK(h.finish(1, 10, 0) && h.pub_seq == 1 && h.pub_value == 10);
        CHECK(!h.finish(2, 11, 11) && h.pub_seq == 1 && h.pub_value == 10 && h.holds == 1);   // job 2 left value 11's end pending
        CHECK(!h.finish(3, 0, 0) && h.pub_seq == 1);                                          // a registration behind it: held too
        CHECK(!h.finish(4, 12, 0) && h.pub_value == 10);                                      // a present behind it
        CHECK(!h.end_sent(9) && h.held_value == 11);                                          // an older end: nothing
        CHECK(h.end_sent(11) && h.pub_seq == 4 && h.pub_value == 12 && h.held_value == 0);    // its end sent: everything done so far
        CHECK(h.finish(5, 13, 0) && h.pub_seq == 5 && h.pub_value == 13);
    }
    {   // the end sent before the job is finished (a worker put the last segment in the queue first): no hold
        DoneHold h;
        h.end_sent(21);
        CHECK(h.finish(1, 21, 21) && h.pub_seq == 1 && h.pub_value == 21 && h.holds == 0);
    }
    {   // a second pending one behind a held one: the marks wait for both
        DoneHold h;
        h.finish(1, 30, 30);
        h.finish(2, 31, 31);
        CHECK(!h.end_sent(30) && h.pub_seq == 0);
        CHECK(h.end_sent(31) && h.pub_seq == 2 && h.pub_value == 31);
    }
    if (!fails) std::printf("PASS the rules\n");
    return fails;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        cpp, exe = Path(tmp) / "t.cpp", Path(tmp) / "t.exe"
        cpp.write_text(CPP)
        subprocess.run([args.cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "gpu/vk"), str(cpp), "-o", str(exe)],
                       check=True)
        subprocess.run([str(exe)], check=True)
    rp = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")

    def function(text, head):
        i = text.index(head)
        j = text.index("{", i)
        depth = 0
        for k in range(j, len(text)):
            depth += {"{": 1, "}": -1}.get(text[k], 0)
            if depth == 0:
                return text[i:k + 1]
        raise SystemExit("FAIL: unbalanced " + head)

    def need(c, msg):
        if not c:
            raise SystemExit("FAIL: " + msg)

    need('submit_seq::parse_lanes(getenv("RX_VK_LANES"))' in function(rp, "unsigned lanes_k() {"), "RX_VK_LANES is not read by the rule")
    need('getenv("RX_VK_LANES_CHECK")' in function(rp, "bool lanes_check_on() {"), "RX_VK_LANES_CHECK is not read")
    need("r.lane_ticket = lane_take();" in function(rp, "Recording& begin_recording(uint64_t value) {"), "a recording takes no ticket when begun")
    ss = function(rp, "void submit_send(SubmitPrep& P) {")
    need(ss.index("std::lock_guard lk(dev.present_mu);") < ss.index("lane_finish(r.lane_ticket), r.lane_ticket = 0;") < ss.index("if (batch_on() || late_hold)"),
         "a recording's ticket is not finished where it takes its place in the queue")
    sr = function(rp, "void early_submit_ready(EarlyGroup& G) {")
    need(sr.index("group_lane_close(G);") < sr.index("group_tail_send(G);"), "the group's ticket is not finished before the submission's end goes")
    need("G.lane_ticket && G.used && G.next_sub >= G.used" in function(rp, "void group_lane_close(EarlyGroup& G) {"),
         "the group's ticket is finished before every segment it uses is in the queue")
    e = function(rp, "const uint8_t* replay_handle_early(")
    eg = e[e.index("} else if (egpu) {"):]
    need(eg.index("submit_recording(r, false);") < eg.index("G.lane_ticket = lane_take();") < eg.index("rd = &begin_recording(rec_cur_value);"),
         "the group's ticket is not taken between r and rd")
    # (RX_VK_LANES: one group pending past the next one's start, joined at that one's dry pass's end, r held for it)
    need("if (lanes_k() && defer_group && !sub_pend && !lane_prev && defer_group->egpu) {" in e and "defer_join_now(kJoinEarlyStart);" in e,
         "the pending group is not kept past the next big command buffer's start under RX_VK_LANES only")
    lane_r = e[e.index("if (egpu && lane_prev) {"):e.index("} else if (egpu) {")]
    need("submit_recording(" not in lane_r and "dry_uploaded.store(true" in lane_r, "r is sent, or a segment let go, before the older group is in the queue")
    need(e.index("replay(none, state, h);") < e.index("if (lane_prev) lane_prev_join(true);") < e.index("if (used < 2 + P || !early) {"),
         "the older group is not joined at the dry pass's end, before anything of this one goes")
    held = e[e.index("if (egpu && !rd) {"):e.index("} else if (egpu) {   // (rd")]
    need(held.index("submit_recording(r, false);") < held.index("G.sub_after_rd = true;") < held.index("early_submit_ready(G);"), "r does not go ahead of the held segments")
    need("if (lane_prev) lane_prev_join(false);" in function(rp, "void defer_join_now(DeferJoinWhy why) {"), "a join leaves the older group pending")
    need("if (lane_next == nullptr || lane_next == &G) par_active = false;" in function(rp, "void early_join(EarlyGroup& G) {"),
         "the older group's join turns par_active off under the next group's segments")
    need(e.index("lane_finish(rd->lane_ticket, false), rd->lane_ticket = 0;") < e.index("submit_recording(*rd, false);"),
         "rd, sent inside the group, keeps a ticket of its own")
    need("group_conds_hold(G);" in e and "group_conds_release(G);" in e, "a group does not hold its render conditions' list")
    need("group_conds_release(G);" in function(rp, "void early_join(EarlyGroup& G) {"), "the join does not let the group's list go")
    rj = function(rp, "void early_record_job(EarlyGroup& G, EarlyJob* j) {")
    need("tl_conds = G.conds;" in rj and "tl_conds = conds_before;" in rj, "a segment's worker does not read its group's render conditions")
    need("const CondList& cl = conds_r();" in function(rp, "void gate_segment_begin(VkCommandBuffer cmd, const Segment& g) {"),
         "a segment's gates are not read from its group's list")
    need("conds_rotate();" in function(rp, "void par_conds_reset() {"), "a dry pass does not take a list of its own")
    need("vk: LANES_CHECK: submit tickets taken %lld" in rp and "lane_chk.seq.out_of_order()" in rp, "the check does not log its counters")
    so = function(rp, "bool sub_overlap_on() {")
    need("!order_thread_on()" not in so, "RX_VK_SUB_OVERLAP is still left off with the order thread")
    m = function(rp, "void ot_main() {")
    need("ot_hold.finish(j.seq, j.value, pend);" in m and "ot_publish_locked();" in m and "ot_value_done.store(" not in m,
         "the order thread's done marks are not held behind a pending submission's end")
    need("sub_pend_flag.load(std::memory_order_acquire) ? sub_pend_value_a.load(" in m, "a submit job's pending end is not read after it")
    ts = function(rp, "void group_tail_send(EarlyGroup& G) {")
    need(ts.index("submit_send(G.tail_prep);") < ts.index("ot_end_sent(G.sub_value);"), "the end's send does not release the done marks")
    need("ot_end_sent(sub_pend_value);" in function(rp, "void defer_join_now(DeferJoinWhy why) {"), "a join does not release the done marks")
    need(rp.count("sub_pend_value_a.store(value, std::memory_order_release);") == 2, "a pending submission's value is not published for the order thread")
    need("G.sub_value = value;" in function(rp, "void finish_recording_deferred(uint64_t value) {"), "the group does not know its submission's value")
    mem = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    need("const uint32_t end_gen = sub.gen_end && gen_cpu_last > sub.gen_end ? sub.gen_end : gen_now;" in mem
         and "for (Pool* p : sub.synced) p->synced_gen = end_gen;" in mem,
         "a submission ended after its handoff leaves its own GPU writes to be synced again (RX_VK_SUB_OVERLAP: 10 MB a submission)")
    need(mem.count("gen_cpu_last = g;") == 3 and "if (!g) g = ++gen_now, gen_cpu_last = g;" in mem,
         "a generation taken for the CPU's writes is not noted (collection, capture's take, lazy flush)")
    # (the order thread's stall watch: the wait sites keep a per-thread table, the watch reads it when the done mark stops)
    ws = rp[rp.index("struct WaitScope {"):rp.index("void wait_note(int site, int64_t ns) {")]
    need("w->since.store(now, std::memory_order_relaxed), w->site.store(s, std::memory_order_release);" in ws and "w->site.store(prev_site" in ws,
         "a wait site does not note where its thread is, or does not put back the outer one")
    need("std::thread(ot_watch).detach();" in rp and "vk: order thread watch: %s; done mark still %.1f s" in rp, "the order thread has no stall watch")
    for site in ("WS_OT_DONE", "WS_OT_SLOT", "WS_OT_IDLE", "WS_OT_JOB", "WS_JOIN", "WS_SIGNAL_LOCK", "WS_LATE_FIX"):
        need(rp.count("WaitScope wait_scope(" + site + ")") + rp.count("WaitScope job_scope(" + site + ")") >= 1, site + " is never noted")
    print("PASS the wiring")


if __name__ == "__main__":
    main()
