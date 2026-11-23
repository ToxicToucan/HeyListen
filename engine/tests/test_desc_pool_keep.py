# Copyright (C) 2026 Sacrappy

"""v0.4.4, the descriptors cause: the recordings' extra descriptor pools are kept and reset (RX_VK_DESC_POOL_KEEP, vk_draw.cpp) instead
of made in the middle of the draws and destroyed at the next recording, and the performance summary's descriptors cause counts the
build's own time and the pools' (vk_internal.h DescPart), not the texture work PROF_DESC also held. By the source text and a
model of the pool ladder:

- the keep path resets the first pool and every extra one in use, keeps them, destroys idle ones only after kExtraIdle recordings
  without a need, and the old path (RX_VK_DESC_POOL_KEEP=0) still destroys them all at each recording's start;
- the allocation takes the last pool in use, goes up to an idle kept one before it makes a pool;
- a set that fails twice is not bound (as before);
- ProfScopeHot's PROF_DESC splits its own time (sampled) from the anonymous exact scopes inside it (PROF_EXACT: texture work), and a
  phase's own scope does not leave its inner anonymous time behind (it is counted in the phase);
- the summary adds the texture work to the upload cause and the build and pool time to the descriptors cause.

python -I -B tests/test_desc_pool_keep.py
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


def model(recordings, keep, idle_limit=120):
    """The ladder as vk_draw.cpp walks it: per recording the number of pools it needs (1 = the first pool only); returns
    (pools made in all, pools destroyed in all, pools alive at the end)"""
    extra, used, idle, made, destroyed = 0, 0, 0, 0, 0
    for need in recordings:
        # begin: reset
        if keep:
            if used:
                idle = 0
            elif extra and (idle := idle + 1) >= idle_limit:
                destroyed += extra
                extra, idle = 0, 0
            used = 0
        else:
            destroyed += extra
            extra = used = 0
        # draws: each pool past the first is taken when the one before ran out
        for _ in range(need - 1):
            if keep and used < extra:
                used += 1
            else:
                extra += 1
                made += 1
                if keep:
                    used = extra
    return made, destroyed, extra


def main():
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    begin = function(draw, "void draw_begin_recording(int slot, VkCommandBuffer cmd, uint64_t value) {")
    assert 'getenv("RX_VK_DESC_POOL_KEEP")' in function(draw, "bool desc_pool_keep() {")
    assert "for (size_t i = 0; i < r.extra_used; i++) vkResetDescriptorPool(dev.device, r.extra[i], 0);" in begin, "the used extras are not reset"
    assert "++r.extra_idle >= kExtraIdle" in begin and "r.extra_used = 0;" in begin, "idle extras are never given back, or the count is kept"
    i_keep = begin.index("if (desc_pool_keep()) {")
    i_else = begin.index("} else {", i_keep)
    assert "vkDestroyDescriptorPool" in begin[i_else:], "RX_VK_DESC_POOL_KEEP=0 no longer destroys the extras at each recording"
    assert "vkDestroyDescriptorPool(dev.device, p, nullptr);\n                r.extra.clear(), r.extra_idle = 0;" in begin
    bind = function(draw, "bool bind_descriptors(VkCommandBuffer cmd, VkPipelineBindPoint bp, const Program& p) {")
    assert "cur->extra[cur->extra_used - 1]" in bind and "cur->extra_used < cur->extra.size()" in bind, "an idle kept pool is not taken first"
    assert bind.count("prof_desc[PD_POOL] +=") >= 2, "the pool's time is not counted"
    assert "kExtraIdle = 120" in draw
    # the model: a scene that needs a second pool every recording makes one pool, not one a recording
    made, destroyed, alive = model([2] * 500, True)
    assert (made, destroyed, alive) == (1, 0, 1), (made, destroyed, alive)
    made, destroyed, alive = model([2] * 500, False)
    assert made == 500 and destroyed == 499, (made, destroyed)
    # a scene that needed three pools once keeps two extra ones, and gives them back after 120 recordings that did not
    made, destroyed, alive = model([3] + [1] * 120, True)
    assert (made, destroyed, alive) == (2, 0, 2), (made, destroyed, alive)
    made, destroyed, alive = model([3] + [1] * 121, True)
    assert (made, destroyed, alive) == (2, 2, 0), (made, destroyed, alive)
    made, destroyed, alive = model([3, 1, 3] + [1] * 5, True)   # (a need in between restarts the idle count)
    assert (made, destroyed, alive) == (2, 0, 2), (made, destroyed, alive)
    # the descriptors cause
    inter = (ROOT / "gpu/vk/vk_internal.h").read_text(encoding="utf-8")
    hot = function(inter, "~ProfScopeHot() {")
    assert "prof_desc[PD_SELF] += own;" in hot and "prof_desc[PD_TEX] += a;" in hot and "phase == PROF_DESC" in hot
    assert "prof_ns[phase] += own + x;" in hot, "PROF_DESC's own total changed (the replay's logs read it)"
    scope = function(inter, "~ProfScope() {")
    assert "prof_t.anon = phase < 0 ? a0 + dt : a0;" in scope, "an anonymous exact scope is not what is counted"
    rep = (ROOT / "gpu/vk/perf_report.cpp").read_text(encoding="utf-8")
    assert "w.descriptors = dd[PD_SELF] + dd[PD_POOL];" in rep and "w.upload += dd[PD_TEX];" in rep
    assert "w.descriptors_which = pr::descriptor_kind(dd[PD_SELF], dd[PD_POOL]);" in rep
    assert "SplitCount prof_desc[PD_COUNT];" in (ROOT / "gpu/vk/vk_main.cpp").read_text(encoding="utf-8")
    print("PASS the pools and the descriptors cause")


if __name__ == "__main__":
    main()
