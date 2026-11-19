"""Samples-passed reports from occlusion queries (RX_VK_OCC_QUERY=1, off by default in this fork; gpu/vk/occ_answer.h,
vk_replay.cpp "occlusion queries", vk_draw.cpp).

From Triforce v0.5.0 (6f338c8, tests/test_occ_answer.py), GPL-3.0, ported to this HeyListen fork 2026-11-18. The source
checks follow HeyListen's names (occq_*, OccqSlot: HeyListen's own RX_VK_OCC_REAL already has occ_*), its places (wait_core
for Triforce's wait_counted; TICK-033's occ_serial / occ_wserial for Triforce's stream_cb / stream_work; two parallel
replay submit paths, replay_handle and replay_handle_early) and its default (off unless RX_VK_OCC_QUERY=1, never with
RX_VK_OCC_REAL). Triforce's lab knob (RX_VK_LAB_AB) and docs/development.md checks are left out: HeyListen has neither.

The game resets NVN's samples-passed counter, draws an object's proxy and reports the count; a count of 0 is an object it does
not draw. The replay writes every report as it passes it with 4096 (everything visible), and still does with the switch on.
With RX_VK_OCC_QUERY=1 the draws from a reset to its report are counted by Vulkan occlusion queries, and once the GPU is done
with the recording that holds them the count is written into the report's record over the 4096, only while that report is
still the newest at its address, only when the count is whole, and only over the value the replay left in the word (a
compare-and-write). What must hold, without the game or a GPU:
- occ_answer.h (compiled and run, tests/occ_answer_test.cpp);
- the source (read): off unless RX_VK_OCC_QUERY=1 (occq_init), every hook behind occq_live (or occq_pending, 0 with it off);
  with it off the report's record is written exactly as before; the slot lifecycle (before_begin, slot_begin, seal after
  dev.present_mu is let go, harvest without waiting); the replay's ops; the draw hooks; every Vulkan call on the pool inside an
  occq_ function.

    python -I -B tests/test_occ_answer.py [--cxx clang++]
"""
import argparse
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def need(where, text, needles):
    for needle in needles:
        if needle not in text:
            raise SystemExit(where + " no longer has: " + needle)


def in_order(where, text, needles):
    at = [text.index(n) if n in text else -1 for n in needles]
    if -1 in at or at != sorted(at):
        raise SystemExit(where + ": not in this order: " + " / ".join(needles))


def check_source(src):
    replay = (src / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8").replace("\r\n", "\n")
    init = block(replay, "bool occq_init() {")
    in_order("vk_replay.cpp occq_init (off unless RX_VK_OCC_QUERY=1; never with RX_VK_OCC_REAL)", init,
             ['getenv("RX_VK_OCC_QUERY")', "occq_env_real = e && atoi(e) != 0;", "if (!occq_env_real) return false;",
              "if (occ_mode()) {", "qi.queryType = VK_QUERY_TYPE_OCCLUSION;",
              "dev.features.occlusionQueryPrecise ? VK_QUERY_CONTROL_PRECISE_BIT : 0",
              "occq_host_reset = dev.vk12.hostQueryReset != VK_FALSE;",
              "if (occq_host_reset) vkResetQueryPool(dev.device, occq_pool, 0, qi.queryCount);", 'getenv("RX_VK_OCC_QUERY_SKIP")',
              'occq_cond_on = !getenv("RX_VK_OCC_COND") || atoi(getenv("RX_VK_OCC_COND")) != 0;',
              'occq_log_cond = getenv("RX_VK_OCC_LOG_COND") && atoi(getenv("RX_VK_OCC_LOG_COND")) != 0;'])
    begin = block(replay, "Recording& begin_recording(uint64_t value) {")
    in_order("vk_replay.cpp begin_recording", begin,
             ["static const bool occq_on = (occq_live = occq_init());", "recording_wait(r);", "if (occq_live) occq_before_begin(r);",
              "vkResetCommandPool(dev.device, r.pool, 0);", "vkBeginCommandBuffer(r.cmd, &bi);", "if (occq_live) occq_slot_begin(r);"])
    seg = block(replay, "Recording& begin_segment_recording(uint64_t value) {")
    in_order("vk_replay.cpp begin_segment_recording", seg,
             ["recording_wait(r);", "if (occq_live) occq_before_begin(r);", "vkBeginCommandBuffer(r.cmd, &bi);",
              "if (occq_live) occq_slot_begin(r);"])
    in_order("vk_replay.cpp begin_segment_slot / segment_begin_here (TICK-061 D7's split)",
             block(replay, "Recording& begin_segment_slot(uint64_t value) {") + block(replay, "void segment_begin_here(Recording& r) {"),
             ["recording_wait(r);", "if (occq_live) occq_before_begin(r);", "vkBeginCommandBuffer(r.cmd, &bi);",
              "if (occq_live) occq_slot_begin(r);"])
    if "VK_QUERY_RESULT_WAIT_BIT" in replay:
        raise SystemExit("vk_replay.cpp: an occlusion query read waits (VK_QUERY_RESULT_WAIT_BIT)")
    read = block(replay, "void occq_read_slot(int i, occ::Stats::At at) {")
    in_order("vk_replay.cpp occq_read_slot (no WAIT bit; whole counts written through occ::complete; reset on the host once read)",
             read, ["VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WITH_AVAILABILITY_BIT", "occ::complete(sl.reps, v.data(), sl.used,",
                    "occq_writers,", "occq_write(addr, expect, count)", "sl.reps.clear();",
                    "if (occq_host_reset && sl.used) vkResetQueryPool(dev.device, occq_pool, (uint32_t)i * kOccqPerSlot, sl.used), sl.used = 0;",
                    "sl.sealed = false;"])
    in_order("vk_replay.cpp occq_write (a compare-and-write of the count word over the replay's value, then as the replay "
             "writes a report: the page marked written, kept out of write-backs)",
             block(replay, "bool occq_write(uint64_t addr, uint64_t expect, uint64_t count) {"),
             ["host->gpu_ptr(addr, &avail)", "__atomic_compare_exchange_n(reinterpret_cast<uint64_t*>(p), &expect, count,",
              "host->mark_written(p, 8);", "now == expect && host->gpu_write(addr, &count, 8);", "if (!ok) return false;",
              "memory_cpu_owned(host->gpu_guest(addr), 16);", "return true;"])
    if replay.count("occq_write(") != 2:   # (its definition and occq_read_slot's call: no other count write)
        raise SystemExit("vk_replay.cpp: a count is written somewhere other than occq_read_slot")
    harvest = block(replay, "void occq_harvest_done(occ::Stats::At at) {")
    in_order("vk_replay.cpp occq_harvest_done (one timeline read under occq_mu; fences only for a slot with no number, never "
             "on a game's thread)", harvest,
             ["if (!occq_pending.load(std::memory_order_acquire)) return;",
              "if (!rec_timeline && at == occ::Stats::kAtWait) return;", "std::lock_guard lk(occq_mu);",
              "if (rec_timeline) vkGetSemaphoreCounterValue(dev.device, rec_timeline, &done);",
              "if (!sl.sealed || sl.reps.empty()) continue;", "is_done = sl.seq <= done;",
              "if (at == occ::Stats::kAtWait) continue;", "vkWaitForFences(dev.device, 1, &o.fence, VK_TRUE, 0) == VK_SUCCESS",
              "if (is_done) occq_read_slot(i, at);"])
    in_order("vk_replay.cpp occq_before_begin (read if sealed, else dropped; reset either way; then the done ones)",
             block(replay, "void occq_before_begin(Recording& r) {"),
             ["std::lock_guard lk(occq_mu);", "if (sl.sealed) {", "occq_read_slot(r.slot, occ::Stats::kAtReuse);",
              "occ::dropped(sl.reps, occq_writers, occq_st);", "vkResetQueryPool(", "sl.armed = false, sl.sealed = false, sl.seq = 0;",
              "occq_harvest_done(occ::Stats::kAtBegin);"])
    in_order("vk_replay.cpp occq_seal (sealed once, by its timeline number; counted pending if it has reports)",
             block(replay, "void occq_seal(Recording& r, uint64_t seq) {"),
             ["std::lock_guard lk(occq_mu);", "if (!sl.armed || sl.sealed) return;", "sl.sealed = true, sl.seq = seq;",
              "occq_pending.fetch_add(1, std::memory_order_release);"])
    need("vk_replay.cpp OccqSeal (behind occq_live)", block(replay, "struct OccqSeal {"),
         ["if (occq_live && rec.submitted) occq_seal(rec, at);"])
    in_order("vk_replay.cpp occq_slot_begin (reset in the command buffer only without hostQueryReset; a new serial)",
             block(replay, "void occq_slot_begin(Recording& r) {"),
             ["if (!occq_host_reset) vkCmdResetQueryPool(r.cmd, occq_pool, (uint32_t)r.slot * kOccqPerSlot, kOccqPerSlot), sl.used = 0;",
              "sl.serial = occq_serial_next.fetch_add(1, std::memory_order_relaxed) + 1;", "sl.armed = true;"])
    submit = block(replay, "void submit_recording(Recording& r, bool signal_value = true, uint64_t signal_at = 0) {")
    in_order("vk_replay.cpp submit_recording (sealed after dev.present_mu is let go: the seal is declared before the lock)",
             submit, ["seq = ++rec_seq;", "OccqSeal occq_sealed{r, seq};", "std::lock_guard lk(dev.present_mu);   // (one queue"])
    waitc = block(replay, "int wait_core(uint64_t f, uint64_t timeout_ns) {")
    in_order("vk_replay.cpp wait_core (a game's wait that ended: the done counts written first)", waitc,
             ["if (completed() >= f) {", "if (occq_pending.load(std::memory_order_relaxed)) occq_harvest_done(occ::Stats::kAtWait);",
              "return 1;", "const int r = wait_inner(f, timeout_ns);",
              "if (r && occq_pending.load(std::memory_order_relaxed)) occq_harvest_done(occ::Stats::kAtWait);"])
    if "c.cmd != cmd" in replay:
        raise SystemExit("vk_replay.cpp: the counting is matched by command buffer handle again")
    in_order("vk_replay.cpp occq_ctr (a segment's: the thread's; the replay's: its queue's state's, under replay_mu)",
             block(replay, "occ::Counter& occq_ctr() {"), ["if (rmode == Mode::Worker) return occq_seg;", "c = &occq_mains[cur_state];"])
    need("vk_replay.cpp occq_seg", replay, ["thread_local occ::Counter occq_seg;",
                                            "std::unordered_map<const NvnState*, occ::Counter> occq_mains;"])
    need("vk_replay.cpp occq_pos (the dry pass's place is the segment thread's: TICK-033's serials)", replay,
         ["uint64_t occq_pos() { return rmode == Mode::Worker ? occ_wserial : occ_serial; }"])
    need("vk_replay.cpp occq_close (only in the recording it was begun in)", block(replay, "void occq_close(occ::Counter& c, VkCommandBuffer cmd) {"),
         ["occq_serial_of(cmd, &slot) == c.serial && slot == c.slot", "vkCmdEndQuery(cmd, occq_pool,", "c.lost = true"])
    in_order("vk_replay.cpp occq_report_measure (matched by serial; only a whole, writable count goes with the recording)",
             block(replay, "void occq_report_measure(VkCommandBuffer cmd, uint64_t addr, uint64_t pos, bool writable) {"),
             ["occq_close(c, cmd);", "const uint64_t serial = occq_serial_of(cmd, &slot);",
              "c.report(occq_keep.load(std::memory_order_relaxed), serial,", "if (o == occ::Counter::kMeasured && writable) {",
              "rep.pos = pos", "occq_slots[c.slot].reps.push_back(rep);"])
    in_order("vk_replay.cpp occq_placed (a clear flag, then the skip list: whether the count is to be written)",
             block(replay, "void occq_placed(uint64_t addr, uint64_t pos, bool dry, bool flag, uint64_t value) {"),
             ["bool write = !flag;", "occ::listed(occq_skip, addr)",
              "occq_writers.placed(addr, pos, occq_presents.load(std::memory_order_relaxed), write, value);"])
    in_order("vk_replay.cpp occq_cond (NX2navi's cond_enabled over the answer from memory; RX_VK_OCC_COND; the skipping state)",
             block(replay, "void occq_cond(const NvnState& s, uint64_t addr, int mode, uint64_t& x, uint64_t& y, bool* enabled) {"),
             ["occq_cond_off = nullptr;", "std::lock_guard lk(occq_mu);",
              "occq_writers.word(addr, now), sy = occq_writers.word(addr + 16, now);",
              "if (sx == occ::Writers::kNone && sy == occ::Writers::kNone) return;",
              "if (active && (sx == occ::Writers::kWrittenSrc || sy == occ::Writers::kWrittenSrc)) {",
              "host->gpu_read(addr, &x, 8);", "*enabled = mode == 0 ? x == y : x != y;",
              "occ::cond_decide(*enabled, sx, sy, active, occq_cond_on);", "occq_st.cond(c.kind);",
              "*enabled = c.enabled;", "if (c.kind == occ::Cond::kWrittenOff) occq_cond_off = &s;"])
    in_order("vk_replay.cpp occq_rewritten", block(replay, "void occq_rewritten(uint64_t addr, uint64_t size, bool report) {"),
             ["occq_writers.erase(addr)", "occq_writers.cleared(addr, addr + size)"])
    in_order("vk_replay.cpp occq_present_tick (the done ones read; Writers pruned every 60 presents, the line every 600)",
             block(replay, "void occq_present_tick() {"),
             ["occq_presents.store(presents_now, std::memory_order_relaxed);", "occq_harvest_done(occ::Stats::kAtPresent);",
              "if (++n % 60) return;", "occq_writers.prune(presents_now, occ::kKeepAge);", "if (n % 600) return;", "occq_st.line("])
    replay_fn = block(replay, "void replay(VkCommandBuffer& cmd, NvnState& s, const uint8_t* p, const uint8_t* end = nullptr) {")
    report = replay_fn[replay_fn.index("case NOP_REPORT_COUNTER: {"):replay_fn.index("case NOP_RESET_COUNTER: {")]
    in_order("vk_replay.cpp NOP_REPORT_COUNTER (the worker measures; the replay writes the fixed count in stream order, placed "
             "under occq_mu with its write; the dry pass measures nothing; off: written as before)", report,
             ["if (worker) {", "if (occq_live) {",
              "occq_report_measure(cmd, c.addr, occq_pos(), (c.type & NVN_COUNTER_WRITTEN) == 0);", "break;",
              "uint64_t count = samples ? (occ_fixed >= 0 ? (uint64_t)occ_fixed : s.samples_passed) : 0;",
              "const uint64_t rec[2] = {count, ns * 12 / 625};", "const auto write_record = [&] {",
              "if (flag_written) host->gpu_write(c.addr + 8, rec + 1, 8);", "else host->gpu_write(c.addr, rec, sizeof rec);",
              "memory_cpu_owned(host->gpu_guest(c.addr), sizeof rec);",
              "if (samples && occq_live) {", "const uint64_t pos = occq_pos();", "std::lock_guard lk(occq_mu);",
              "occq_placed(c.addr, pos, dry, flag_written, count);", "write_record();",
              "if (!dry) occq_report_measure(cmd, c.addr, pos, !flag_written);",
              "} else if (occq_live) {\n                std::lock_guard lk(occq_mu);\n                occq_rewritten(c.addr, 16, true);\n                write_record();",
              "} else {\n                write_record();\n            }\n            break;"])
    reset = replay_fn[replay_fn.index("case NOP_RESET_COUNTER: {"):]
    in_order("vk_replay.cpp NOP_RESET_COUNTER", reset[:500], ["s.samples_passed = 0;", "if (occq_live && !dry) occq_reset(cmd);"])
    cond = replay_fn[replay_fn.index("case NOP_RENDER_COND: {"):]
    in_order("vk_replay.cpp NOP_RENDER_COND (not the worker's: the replay's and the dry pass's decide, the segments take it)",
             cond[:3000],
             ["if (worker) {", "par_conds[cond_at] != 0", "break;", "s.render_enabled = r.mode == 0 ? x == y : x != y;",
              "if (occq_live) {", "if (r.snapped) occq_cond_off = nullptr;",
              "else occq_cond(s, r.addr, (int)r.mode, x, y, &s.render_enabled);", "if (always) s.render_enabled = true;",
              "par_conds.push_back(s.render_enabled ? 1 : 0);"])
    enable = replay_fn[replay_fn.index("case NOP_RENDER_ENABLE:"):replay_fn.index("case NOP_RENDER_COND: {")]
    need("vk_replay.cpp NOP_RENDER_ENABLE (a condition's skipping ends)", enable,
         ["if (occq_live && !worker && occq_cond_off == &s) occq_cond_off = nullptr;"])
    clear_buf = replay_fn[replay_fn.index("case NOP_CLEAR_BUF: {"):]
    in_order("vk_replay.cpp NOP_CLEAR_BUF (a small clear in stream order: the records it covers leave Writers with its write)",
             clear_buf[:1800], ["if (in_stream && !worker) {", "if (occq_live) {", "std::lock_guard lk(occq_mu);",
                                "occq_rewritten(c0.dst, std::min<uint64_t>(c0.size, sizeof words), false);",
                                "host->gpu_write(c0.dst, words, std::min<uint64_t>(c0.size, sizeof words));", "} else {",
                                "host->gpu_write(c0.dst, words, std::min<uint64_t>(c0.size, sizeof words));   // (hookshot"])
    for op in ("case NOP_CLEAR_COLOR:", "case NOP_CLEAR_DS:"):
        at = replay_fn[replay_fn.index(op):]
        in_order("vk_replay.cpp " + op + " (a skipped clear counted, not a worker's)", at[:at.index("break;")],
                 ["if (occq_live && !worker) occq_skip_note(s, true);", "if (s.render_enabled && !dry) {"])
    in_order("vk_replay.cpp replay() (a skipped draw or dispatch counted, not a worker's)", replay_fn,
             ["h.op == NOP_DISPATCH || h.op == NOP_DISPATCH_INDIRECT) {", "if (occq_live && !worker) occq_skip_note(s, false);",
              "if (worker) occ_wserial++;"])
    handle = block(replay, "const uint8_t* replay_handle(VkCommandBuffer& cmd, const uint8_t* h, bool may_stop) {")
    in_order("vk_replay.cpp replay_handle (the replay's counting given up before a dry pass, the early path's too)", handle,
             ["if (occq_live) occq_serial_give_up(cmd);", "replay_handle_early(", "rmode = Mode::Dry;"])
    in_order("vk_replay.cpp replay_handle (a segment's recording signals the recordings' timeline and is sealed after "
             "dev.present_mu is let go; off: the submit as before)", handle,
             ["if (occq_live && rec_timeline) {", "occq_seq = ++rec_seq;",
              "occq_sig.semaphore = rec_timeline, occq_sig.value = occq_seq,",
              "si.signalSemaphoreInfoCount = 1, si.pSignalSemaphoreInfos = &occq_sig;",
              "OccqSeal occq_sealed{*j->r, occq_seq};", "std::lock_guard lk(dev.present_mu);", "queue_flush_locked();",
              "vkQueueSubmit2(dev.queue, 1, &si, j->r->fence);", "j->r->submitted = res == VK_SUCCESS;"])
    early = block(replay, "const uint8_t* replay_handle_early(")
    in_order("vk_replay.cpp replay_handle_early (its segments sealed too; a timeline number only on the replay thread's submits)",
             early, ["const int egpu = early ? early_gpu_mode() : 0;", "const auto submit_job = [&init_lost, egpu](Job* j) {",
                     "if (occq_live && rec_timeline && !egpu) {", "occq_seq = ++rec_seq;", "OccqSeal occq_sealed{*j->r, occq_seq};",
                     "std::lock_guard lk(dev.present_mu);", "vkQueueSubmit2(dev.queue, 1, &si, j->r->fence);"])
    in_order("vk_replay.cpp record_segment (a segment counts on its own)", block(replay, "void record_segment(VkCommandBuffer& cmd, const Segment& g) {"),
             ["rmode = Mode::Worker;", "occ_wserial = g.serial;", "if (occq_live) occq_segment_edge(cmd);",
              "replay(cmd, s, g.p, g.end);", "end_pass(cmd);", "if (occq_live) occq_segment_edge(cmd);   // (Triforce: what",
              "rmode = Mode::Serial;"])
    for fn in ("void occq_segment_edge(VkCommandBuffer cmd) {", "void occq_serial_give_up(VkCommandBuffer cmd) {"):
        need("vk_replay.cpp " + fn, block(replay, fn), ["occ::Counter& c = occq_ctr();", "occq_close(c, cmd);", "c.segment();"])
    in_order("vk_replay.cpp occq_draw_begin", block(replay, "void occq_draw_begin(VkCommandBuffer cmd) {"),
             ["if (!c.may_begin(keep)) return;", "if (!c.begin(keep, occq_serial_of(cmd, &slot))) return;",
              "if (sl.used >= kOccqPerSlot) return c.full();", "vkCmdBeginQuery(cmd, occq_pool,", "occq_open_here = true;"])
    need("vk_replay.cpp occq_pass_end (only this thread's query)", block(replay, "void occq_pass_end(VkCommandBuffer cmd) {"),
         ["if (occq_open_here) occq_close(occq_ctr(), cmd);"])
    need("vk_replay.cpp the present", replay, ["if (occq_live) occq_present_tick();"])
    # every Vulkan call on the occlusion pool is in an occq_ function (behind occq_live or occq_pending): none in the off path
    for call in ("vkCmdBeginQuery(", "vkCmdEndQuery(", "vkGetQueryPoolResults(", "vkCmdResetQueryPool(", "vkResetQueryPool(",
                 "vkCreateQueryPool("):
        for m in re.finditer(re.escape(call), replay):
            if "occq_pool" not in replay[m.start():replay.index(";", m.start())]:
                continue   # (another pool's)
            fn = max(replay.rfind("\nvoid occq_", 0, m.start()), replay.rfind("\nbool occq_init() {", 0, m.start()))
            other = max(replay.rfind("\nvoid ", 0, m.start()), replay.rfind("\nbool ", 0, m.start()),
                        replay.rfind("\nuint64_t ", 0, m.start()), replay.rfind("\nint ", 0, m.start()))
            if fn < other:
                raise SystemExit("vk_replay.cpp: " + call + " outside the occq_ functions")
    if re.search(r"\bocc_live\b", replay):
        raise SystemExit("vk_replay.cpp: Triforce's occ_live name is back: it is occq_live here")

    draw = (src / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8").replace("\r\n", "\n")
    in_order("vk_draw.cpp end_pass (the query ends in its pass)", block(draw, "void end_pass(VkCommandBuffer cmd) {"),
             ["if (!ps.open) return;", "if (occq_live) occq_pass_end(cmd);", "vkCmdEndRendering(cmd);"])
    d = block(draw, "void draw(VkCommandBuffer cmd, NvnState& s, const DrawArgs& d) {")
    in_order("vk_draw.cpp draw", d, ["if (occq_live) occq_draw_enter();", "begin_pass(cmd, at);",
                                     "if (occq_live && ps.open) occq_draw_begin(cmd);"])
    # every draw it records says so to the count; the one st_draws++ that records nothing (RX_VK_OCC_PROXY_SKIP) does not
    issued = d.count("if (occq_live) occq_draw_issued();")
    if issued != 5 or d.count("st_draws++, ps.draws++;") != 6:
        raise SystemExit("vk_draw.cpp draw: every draw it records (st_draws++) says so to the count (occq_draw_issued)")
    need("vk_draw.cpp occ_proxy_skip_on (not with the queries: they count those draws)", block(draw, "bool occ_proxy_skip_on() {"),
         ["&& !occq_live;"])
    head = (src / "gpu/vk/vk_draw.h").read_text(encoding="utf-8")
    need("vk_draw.h", head, ["extern bool occq_live;", "void occq_draw_enter();", "void occq_draw_begin(VkCommandBuffer cmd);",
                             "void occq_draw_issued();", "void occq_pass_end(VkCommandBuffer cmd);"])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=ROOT)
    p.add_argument("--cxx", default="clang++")
    a = p.parse_args()
    check_source(a.source)
    with tempfile.TemporaryDirectory(prefix="occ-answer-") as temp:
        exe = Path(temp) / "occ_answer_test.exe"
        flags = ["-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS", "-I" + str(a.source / "gpu/vk")]
        subprocess.run([a.cxx, *flags, str(ROOT / "tests/occ_answer_test.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    print("occ_answer: ok")


if __name__ == "__main__":
    main()
