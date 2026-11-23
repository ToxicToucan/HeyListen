# Copyright (C) 2026 Sacrappy

"""On Intel's one-at-a-time compute builds a dispatch's pipeline is made first without the optimizer, then with it.

Ported from Longshot's check of the same name (b2911d33, #404), cut to what this tree has: no Debug-tab row, so
RX_VK_COMPUTE_QUICK_FIRST alone turns it off.

A UHD Graphics (i3-1115G4, 2 cores, 4 threads, 16 GB shared) took 0.3 s to 6.5 min with the optimizer for each of 37 of the
game's dispatches, 22 minutes on the one serial thread, and every pass over the scene (its light, its exposure) was left
out until its turn. The first build is made without the optimizer; the optimized one is queued behind the builds asked for
first and bound once made, the first kept for the session.

python -I -B tests/test_intel_quick_compute.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker, start_at=0):
    start = text.index(marker, start_at)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def need(cond, why):
    if not cond:
        raise SystemExit("FAIL: " + why)


def main():
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    shader = (ROOT / "gpu/vk/vk_shader.h").read_text(encoding="utf-8")
    jobs = (ROOT / "gpu/vk/vk_jobs.cpp").read_text(encoding="utf-8")
    dispatch = block(draw, "void dispatch(VkCommandBuffer cmd, NvnState& s, uint32_t x, uint32_t y, uint32_t z, uint64_t indirect) {")

    # (the dispatch's first build, on the serial thread: no optimizer, the optimized one behind, the module kept for it)
    need("static const bool serial = serial_builds_on();" in dispatch, "the dispatch's serial switch is not serial_builds_on")
    build = dispatch[dispatch.index("auto build = [job] {"):dispatch.index("if (serial) jobs_push_serial(std::move(build)")]
    need("const bool quick = serial && intel_quick_compute() && !(ci.flags & VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT);" in build,
         "the quick build is not limited to the serial builds and the switch")
    need("if (quick) ci.flags |= VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT;" in build, "the first build keeps the optimizer")
    made = build[build.index("std::atomic_ref<VkPipeline>(q.compute_pipeline).store(made, std::memory_order_release);"):]
    need("if (quick) jobs_push_serial([job] { compute_optimize(const_cast<Program&>(*job)); });" in made and
         "else cs_module_release(q);" in made, "the optimized build is not queued once the quick one is made, or its module is let go")
    need("jobs_push_serial(std::move(build), true)" in dispatch, "a dispatch's build is not in the serial thread's front lane")
    need("jobs_push_serial([job] { compute_optimize(const_cast<Program&>(*job)); }, true)" not in build,
         "the optimized build is queued in the front lane, ahead of the passes still to be built")

    # (bound once made, the quick one kept: a recording may hold it)
    need("const VkPipeline optimized = p.compute_optimized.load(std::memory_order_acquire);" in dispatch and
         "const VkPipeline bound = optimized ? optimized : pipe;" in dispatch and
         "vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, bound);" in dispatch,
         "the dispatch does not bind the optimized pipeline once made")
    opt = block(draw, "void compute_optimize(Program& q) {")
    need("DISABLE_OPTIMIZATION" not in opt, "the second build is not the optimized one")
    need("q.compute_optimized.store(made, std::memory_order_release);" in opt, "the optimized pipeline is not published")
    need("vkDestroyPipeline" not in opt and "compute_pipeline =" not in opt,
         "the pipeline without the optimizer is destroyed or replaced under a recording")
    need("cs_module_release(q)" in opt, "the module kept for the optimized build is never let go")
    need("std::atomic<VkPipeline> compute_optimized{VK_NULL_HANDLE};" in shader, "Program has no slot for the optimized pipeline")
    need("std::atomic<bool> optimize_owed{false};" in shader, "Program has no flag for the owed optimized build")

    # (before play, the same: the prepare's warm pass and a start's precompile ask for the pipeline a dispatch's first build asks
    # for. A program made so owes its optimized build to its first dispatch, not before: most are never dispatched)
    quick = block(draw, "bool precompile_compute_quick(VkComputePipelineCreateInfo& ci) {")
    need("if (!serial_builds_on() || !intel_quick_compute() || (ci.flags & VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT)) return false;"
         in quick and "ci.flags |= VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT;" in quick,
         "the precompile's quick build is not limited to the serial builds and the switch")
    need(draw.count("precompile_compute_quick(ci)") == 2, "the warm pass and the precompile do not both ask for the quick pipeline")
    run_at = draw.index("const bool quick = precompile_compute_quick(ci);")
    run = draw[run_at:run_at + 700]
    need("if (quick) p.optimize_owed.store(true, std::memory_order_release);" in run and "jobs_push" not in run,
         "the precompile's quick pipeline owes no optimized build, or queues it for programs never dispatched")
    first = dispatch[dispatch.index("if (p.optimize_owed.load(std::memory_order_relaxed)"):]
    need(first.index("p.optimize_owed.exchange(false, std::memory_order_acq_rel)") < first.index(
         "jobs_push_serial([q] { compute_optimize(*q); });") < first.index("const VkPipeline optimized ="),
         "the first dispatch of a precompiled quick pipeline does not queue its optimized build once, before binding")

    # (the serial thread: the front lane, a save owed to it between two builds)
    serial = block(jobs, "void jobs_push_serial(")
    need("sq_first" in serial and "pipeline_cache_between_builds" in serial and
         serial.index("run_guarded(fn);") < serial.index("run_guarded(between);"),
         "the serial thread has no front lane, or no save between two builds")
    between = block(draw, "void pipeline_cache_between_builds() {")
    need("pcache_save_owed.exchange(false)" in between and "pipeline_cache_save(false);" in between, "the owed save is not run between builds")
    print("PASS: Intel's serial compute builds draw without the optimizer first, and an owed save runs between two builds")


if __name__ == "__main__":
    main()
