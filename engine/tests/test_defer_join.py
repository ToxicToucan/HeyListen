"""(stream JOIN1) RX_VK_DEFER_JOIN: a big command buffer's join may be left for later, and never outlives the replay lock.

By the text of gpu/vk/vk_replay.cpp:
- off unless the switch is set (0 when unset), clamped to 0..2, and only on the path with RX_VK_EARLY_GPU (the segments are submitted in
  order by the thread that finishes one, so the GPU is fed meanwhile) and without an occlusion stop;
- the pending group is on the heap (std::shared_ptr<EarlyGroup>, held by the pool's jobs), set only after the last segment was handed to the pool;
- the join (early_join) is made, at the latest, before run_submit's recording end (right after its command buffer loop), and before each
  of: the next big command buffer's pre-sync (the start of replay_handle_early), a serial replay (replay_handle's first branch), an
  early pre-sync on a worker, the multi-segment path's own tables, occ_go_on;
- a dry pass that runs with a join pending (replay_handle's) continues par_conds from where it ends (cond0), does not reset it, and holds
  ub_grow_mu for its appends (ub_growing) as an early dry pass does;
- RX_VK_DEFER_JOIN_CHECK compares each segment's recorded work with the dry pass's plan at the join.

python -I -B tests/test_defer_join.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def function(source, marker):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def need(cond, why):
    if not cond:
        raise SystemExit("FAIL: " + why)


def main():
    rp = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    m = function(rp, "int defer_join_mode() {")
    need('getenv("RX_VK_DEFER_JOIN")' in m and "std::clamp(atoi(e), 0, 2) : (sub_overlap_on() || lanes_k() ? 2 : 0);" in m, "RX_VK_DEFER_JOIN is not off when unset (on only under RX_VK_SUB_OVERLAP or RX_VK_LANES)")
    need("std::shared_ptr<EarlyGroup> defer_group;" in rp, "the pending group is not on the heap")
    e = function(rp, "const uint8_t* replay_handle_early(")
    need(e.index("defer_join_now(") < e.index("memory_sync_mirrors_before_segments();"), "the pre-sync is made beside pending segments")
    need("const bool defer = egpu && !occ_stop && defer_join_mode() != 0;" in e, "the deferral is not limited to EARLY_GPU without an occlusion stop")
    need(e.index("par_pool.jobs.push_back([group, last]") < e.index("defer_group = group;"), "the group is pending before its last segment is handed out")
    need("[group, j]" in e, "the pool's jobs do not hold the group")
    rs = function(rp, "bool run_submit(")
    need(rs.index("defer_join_now(") > rs.index("stop = replay_handle(") and rs.index("defer_join_now(") < rs.index("finish_recording(value);"),
         "a join can outlive run_submit's command buffer loop")
    rh = function(rp, "const uint8_t* replay_handle(VkCommandBuffer& cmd, const uint8_t* h, bool may_stop)")
    need(rh.index("defer_join_now(") < rh.index("suspend_ok = may_stop;"), "the serial replay is made beside pending segments")
    need("if (presync_kick_at) defer_join_now(kJoinPresyncKick);" in rh, "the early pre-sync on a worker runs beside pending segments")
    need("par_points.clear(), dry_work = 0;" in rh and "const size_t cond0 = defer_group || pend_before ? par_conds_n.load(std::memory_order_acquire) : 0;" in rh
         and "seg.push_back({h, nullptr, &start, ub0, cond0, serial0});" in rh, "a dry pass beside pending segments resets par_conds")
    need("ub_growing.store(true, std::memory_order_relaxed);" in rh[:rh.index("rmode = Mode::Dry;")], "its appends do not hold ub_grow_mu")
    need(rh.index("defer_join_now(", rh.index("if (seg.size() == 1)")) < rh.index("if (!par_pool.started) {"),
         "the multi-segment path takes the tables beside pending segments")
    need("defer_join_now(" in function(rp, "const uint8_t* occ_go_on(VkCommandBuffer& cmd, const uint8_t* at, bool may_stop, Recording* last) {"), "occ_go_on waits for a recording beside pending segments")
    j = function(rp, "void early_join(EarlyGroup& G)")
    need("recorded != (int64_t)(end - jobs[k]->g.serial)" in j, "the check does not compare the recorded work with the plan")
    need("if (G.init_lost)" in j and j.index("par_pool.done.wait(") < j.index("par_active = false;"), "the join order is not kept")
    print("PASS deferred join: off by default, on the heap, joined before the next pre-sync / serial replay / the recording's end, dry pass guarded")


if __name__ == "__main__":
    main()
