# Copyright (C) 2026 Sacrappy

"""Windows Intel's compiler is entered by one thread, and a fault creating a shader module leaves that program out.

Longshot #252 (an Intel UHD on Windows read address 0x170 inside igvk64 during the first scene's compiles; the stack had no
"driver faulted compiling" line, so the call was not inside guarded_graphics or guarded_compute). vkCreateShaderModule
(translation, the movie and upscale helpers, a masked clear) and gpl_link's vkCreateGraphicsPipelines were not guarded, and
graphics pipelines were still built side by side. NVIDIA, AMD, and Intel on other systems are not held (the gate is a no-op
there: the same calls, nothing taken). The Debug tab's Intel shader compiles row (Intel graphics on Windows only), or
RX_VK_NO_INTEL_SERIAL over it, leaves them side by side. The renderer's own compute pipelines (ASTC, BC and unswizzle,
write-back diff, FSR 3.1 and its inputs and interpolation, frame generation, the cooperative-matrix check, the movie and typed
upscale helpers, the masked clear) are guarded and held too: one of them built beside a worker's is the same two threads in it.

python -I -B tests/test_intel_compiler_gate.py
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


def brace_block(text, open_at):
    """text[open_at] is '{'; return through its match, including the braces."""
    end = open_at + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[open_at:end]


def need(cond, why):
    if not cond:
        raise SystemExit("FAIL: " + why)


def main():
    internal = (ROOT / "gpu/vk/vk_internal.h").read_text(encoding="utf-8")
    gate = block(internal, "struct IntelDriverGate {")
    on = block(internal, "inline bool intel_gate_on() {")
    need("#ifdef _WIN32" in on, "the lock is not limited to Windows")
    need("!intel_compiles_side_by_side()" in on, "the Intel shader compiles row does not turn the lock off")
    need("dev.props.vendorID == 0x8086" in on, "a vendor other than Intel takes the lock")
    need("if (!intel_gate_on()) return;" in gate, "the lock is taken where it is off")
    # (an ask that compiles nothing never waits behind a worker's compile: it is answered cold)
    need("std::try_to_lock" in gate and "busy = !hold.owns_lock();" in gate, "an ask waits for the lock")
    need("std::unique_lock<std::mutex>(mu)" in gate, "the compiler is not entered by one thread")

    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8").replace("\r\n", "\n")
    side = block(draw, "bool intel_compiles_side_by_side() {")
    need('menu_env_on("RX_VK_NO_INTEL_SERIAL", "intel_shader_compiles")' in side,
         "the lock does not read the Intel shader compiles row and RX_VK_NO_INTEL_SERIAL")
    menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8").replace("\r\n", "\n")
    need('{TAB_DEBUG, "Intel shader compiles", &opts.intel_compiles, 0, 1, 1, tx_intel_compiles,' in menu,
         "no Intel shader compiles row")
    shown = block(menu, "bool row_shown(int i) {")
    need("rows[i].value == &opts.intel_compiles" in shown and "#ifdef _WIN32" in shown and
         "dev.props.vendorID != 0x8086" in shown, "the row is not limited to Intel graphics on Windows")
    # (the row's ini key is the one the gate reads: ini_key lower-cases the name and joins it with underscores)
    need('"Intel shader compiles"' in menu and "intel_shader_compiles" in draw, "the row's key and the gate's key differ")

    win = block(draw, "__declspec(noinline) VkResult guarded_shader_module(")
    need("__try" in win and "vkCreateShaderModule" in win and "return (VkResult)-77;" in win,
         "a fault in vkCreateShaderModule is not turned into a left-out program")
    linux_at = draw.index("#else", draw.index("__declspec(noinline) VkResult guarded_shader_module("))
    linux = block(draw, "VkResult guarded_shader_module(", linux_at)
    need("sigsetjmp(fault_jmp, 1)" in linux and "vkCreateShaderModule" in linux, "Linux does not guard vkCreateShaderModule")

    # (a draw's ask never waits behind a worker's compile: answered cold, counted and logged)
    build = block(draw, "VkPipeline build_graphics(const Program& p, const NvnState& s, const Targets& at, VkPrimitiveTopology topo, int part,")
    need("IntelDriverGate gate(cold != nullptr);" in build and "if (gate.busy) return intel_gate_busy(), *cold = true, VK_NULL_HANDLE;" in build,
         "build_graphics makes an ask wait for a compile")
    need(build.index("IntelDriverGate gate") < build.index("guarded_graphics_held("), "build_graphics compiles before it takes the lock")
    busy = block(draw, "void intel_gate_busy()")
    need("++st_intel_gate_busy" in busy and "logf(" in busy, "an ask answered cold for a busy gate is not counted")
    gated = block(draw, "VkResult gated_compute(")
    need(gated.index("IntelDriverGate") < gated.index("guarded_compute_held("), "a compute pipeline is made before the lock is taken")
    need("guarded_compute_held(pcache" not in draw.replace("return guarded_compute_held(cache, ci, pl);", ""),
         "a compute pipeline of the game's still calls the driver with no lock")
    link = block(draw, "VkPipeline gpl_link(")
    need(link.index("IntelDriverGate") < link.index("guarded_graphics"), "gpl_link's pipeline is not guarded")
    need("vkCreateGraphicsPipelines" not in link, "gpl_link still calls the driver with no guard")
    clear = block(draw, "VkPipeline clear_pipeline(")
    need("IntelDriverGate gate;" in clear and "vkCreateShaderModule" not in clear and "vkCreateGraphicsPipelines" not in clear,
         "the masked clear is not guarded")
    for fn in ("bool movie_upscale(", "static bool typed_upscale("):
        body = block(draw, fn, draw.rindex(fn))   # (typed_upscale is declared before its definition)
        need("IntelDriverGate gate;" in body and "vkCreateShaderModule" not in body and "vkCreateComputePipelines" not in body,
             fn + " is not guarded")
    # (no lock is taken inside another: a std::mutex is not recursive)
    for fn in ("VkResult gated_compute(", "bool movie_upscale(", "static bool typed_upscale("):
        need("gated_compute(" not in block(draw, fn, draw.rindex(fn)).replace("VkResult gated_compute(", ""), fn + " takes the lock twice")

    shader = (ROOT / "gpu/vk/vk_shader.cpp").read_text(encoding="utf-8")
    module = block(shader, "VkShaderModule make_module(")
    need("IntelDriverGate gate;" in module and "extern_guarded_shader_module" in module and "vkCreateShaderModule" not in module,
         "translation still creates a module unguarded")

    for name in ("vk_texture", "vk_memory", "vk_fsr3", "vk_fsr3_inputs", "vk_fsr3fi", "vk_framegen", "vk_coopmat"):
        src = (ROOT / "gpu/vk" / (name + ".cpp")).read_text(encoding="utf-8")
        need("vkCreateShaderModule(" not in src and "vkCreateComputePipelines(" not in src,
             name + ".cpp still calls the compiler with no guard")
        need("IntelDriverGate gate;" in src, name + ".cpp does not take the lock")
    print("Intel's compiler on Windows is entered by one thread, and a fault leaves that shader out: ok")


if __name__ == "__main__":
    main()
