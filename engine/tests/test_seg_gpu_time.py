"""Both parallel-replay segment paths are in the GPU-time counts, from the text of gpu/vk/vk_replay.cpp.

RX_VK_SEG_EARLY's path (replay_handle_early, on by default through hos_main.c) never wrote a segment's timestamps: a frame's
early segments were missing from the log's "GPU time", its "gaps between recordings" (a 3-5 ms "idle" gap before each present
that was those segments running) and RX_VK_TL's GPU side. Checked here:
- replay_handle_early: gpu_time_begin on each segment's slot right after its upload command buffer is begun, before the dry pass
  hands any segment out; gpu_time_end in record_job after the segment's last upload (texture_uploads_recorded) and before its
  command buffers end. The same as replay_handle's segments (TICK-057), whose begin is in segment_begin_here with
  RX_VK_SEG_SETUP_PAR;
- not behind a switch: neither call under a getenv, and gpu_time_begin / gpu_time_end do nothing without the query pool;
- each used segment's slot has its recording number (ts_recseq) set after the recording before it is submitted, in both paths;
- each segment's own submit is counted with submit_recording's (gpu_time_segment_submitted, before its vkQueueSubmit2): the blits'
  places among the collected recordings (ts_last_end_seen) stay in step with gpu_time_collect, which numbers segments too.

python -I -B tests/test_seg_gpu_time.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def function(source, marker):
    if source.count(marker) != 1:
        raise SystemExit("FAIL: marker not found once: " + marker)
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def main():
    fails = []

    def need(cond, why):
        if not cond:
            fails.append(why)

    rp = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    hm = (ROOT / "host/hos_main.c").read_text(encoding="utf-8")
    need('"RX_VK_SEG_EARLY"' in function(hm, "static void heylisten_env_defaults(void)"), "hos_main.c: RX_VK_SEG_EARLY no longer on by default "
         "(this check's premise)")

    gb = function(rp, "void gpu_time_begin(Recording& r) {")
    ge = function(rp, "void gpu_time_end(Recording& r, bool uploads) {")
    need("if (!ts_pool) return;" in gb and "if (!ts_pool) return;" in ge, "gpu_time_begin / gpu_time_end write without the query pool")
    gs = function(rp, "void gpu_time_segment_submitted(const Recording& r) {")
    need("if (!ts_pool || !ts_pending[r.slot]) return;" in gs and "ts_seq_submitted++;" in gs and "std::lock_guard lk(ts_mu);" in gs,
         "gpu_time_segment_submitted: counts only a timed segment, under ts_mu")
    sr = function(rp, "void submit_recording(Recording& r, bool signal_value = true, uint64_t signal_at = 0, SubmitPrep* defer = nullptr) {") + function(rp, "void submit_send(SubmitPrep& P) {")
    need("ts_seq_submitted++;" in sr, "submit_recording no longer counts its submissions")

    e = function(rp, "const uint8_t* replay_handle_early(")
    need("getenv" not in e, "replay_handle_early reads a switch: the segments' GPU time must not depend on one")
    loop = e[e.index("for (int k = 0; k < nplan + (int)P; k++) {"):e.index("std::vector<VkCommandBuffer> inits;")]
    need("gpu_time_begin(*j->r);" in loop and loop.index("vkBeginCommandBuffer(j->r->upload, &bi);") < loop.index("gpu_time_begin(*j->r);"),
         "early path: a segment's timestamps are not begun after its upload command buffer")
    need(e.index("gpu_time_begin(*j->r);") < e.index("hook.on_point = [&](const ParPoint& q) {") < e.index("jt_mark(13, 0);"),
         "early path: timestamps begun after a segment can be handed out")
    rj = function(rp, "void early_record_job(EarlyGroup& G, EarlyJob* j)")
    need("gpu_time_end(*j->r, j->uploads);" in rj, "early path: a segment's end timestamps are not written")
    if "gpu_time_end(*j->r, j->uploads);" in rj:
        at = rj.index("gpu_time_end(*j->r, j->uploads);")
        need(rj.index("j->uploads = texture_uploads_recorded();") < at < rj.index("vkEndCommandBuffer(j->r->upload);")
             and at < rj.index("vkEndCommandBuffer(j->r->cmd);"), "early path: the end timestamps are not after the uploads and before the ends")
    sub = e.index("submit_recording(r, false);")
    need("ts_recseq[jobs[k]->r->slot] = rec_seq;" in e and sub < e.index("ts_recseq[jobs[k]->r->slot] = rec_seq;") < e.index("early_record_job(G, last);"),
         "early path: the segments' recording number is not set after rec_cur's submit, before they are recorded")
    es = function(rp, "void early_submit_job(EarlyGroup& G, EarlyJob* j)")
    sq = es.index("gpu_time_segment_submitted(*j->r);") if "gpu_time_segment_submitted(*j->r);" in es else -1
    need(sq > 0 and es.index("queue_flush_locked();") < sq < es.index("vkQueueSubmit2(dev.queue, 1, &si, j->r->fence)"),
         "early path: a segment's submit is not counted under the present lock before it goes")

    rh = function(rp, "const uint8_t* replay_handle(VkCommandBuffer& cmd, const uint8_t* h, bool may_stop)")
    jobs = rh[rh.index("const bool setup_here = seg_setup_par_on() && ts_pool;"):rh.index("jt_mark(21, 0);", rh.index("const bool setup_here"))]
    need("gpu_time_begin(*j->r);" in jobs, "replay_handle: the segments' timestamps (TICK-057) are gone")
    need("ts_recseq[j->r->slot] = rec_seq;" in jobs and jobs.index("ts_recseq[j->r->slot] = rec_seq;") > jobs.index("        }\n"),
         "replay_handle: the recording number is set for one setup path only")
    need(rh.index("submit_recording(r, false);\n    jt_mark(20, 0);") < rh.index("ts_recseq[j->r->slot] = rec_seq;"),
         "replay_handle: the recording number is taken before rec_cur's submit")
    need("gpu_time_begin(r);" in function(rp, "void segment_begin_here(Recording& r) {"), "RX_VK_SEG_SETUP_PAR: a segment's timestamps not begun")
    seg_count = "gpu_time_segment_submitted(*j->r);" if "gpu_time_segment_submitted(*j->r);" in rh else "gpu_time_segment_submitted(*member[q]->r);"   # (ORDER1: merged submits count each member)
    sq = rh.index(seg_count) if seg_count in rh else -1
    need(sq > 0 and rh.index("queue_flush_locked();", rh.index("record_job")) < sq < rh.index("vkQueueSubmit2(dev.queue, 1, &si, " + ("owner.fence)" if "vkQueueSubmit2(dev.queue, 1, &si, owner.fence)" in rh else "j->r->fence)")),
         "replay_handle: a segment's submit is not counted under the present lock before it goes")

    if fails:
        for f in fails:
            print("FAIL", f)
        return 1
    print("PASS: both segment paths write their GPU timestamps (no switch), take their recording number and count their submits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
