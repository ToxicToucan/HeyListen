# Copyright (C) 2026 Sacrappy

"""AMD FSR 4 frame generation (gpu/vk/vk_fsr4fg.cpp), the Frame generation by row's fourth choice. It cannot run on any PC the project has
(it needs a Radeon RX 9000), so what is checked here is what can be: that it follows AMD's headers' call order and structures, that it is
offered only through AMD's own capability query, that every return code AMD's headers define is said in words, that a refusal leaves the
frame to the generators that were there before, and that on every other PC the row and the fallbacks are exactly what they were.

- third_party/fidelityfx/framegeneration: AMD's headers (FidelityFX SDK 2.4, frame generation 4.0.2), unmodified apart from the two include
  paths that point at the SDK's own folder layout.
- vk_fsr4fg.cpp: the capability query (the versions AMD lists for FFX_API_CREATE_CONTEXT_DESC_TYPE_FRAMEGENERATION, a 4.x one), the context's
  description chain, the per-frame order (configure, prepare V2, dispatch) with one frame ID, the no-swapchain flag, a plain-words text for
  each ffxReturnCode_t, three refusals in a row turning it off, and the stubs of a build or a platform without it.
- vk_framegen.cpp: AMD FSR 4 tried first at 2x where it is chosen and offered; the AMD FSR 3.1 interpolation and the renderer's own after it.

No game, GPU or AMD file required.

python -I -B tests/test_fsr4_framegen.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu" / "vk"
FFX = ROOT / "third_party" / "fidelityfx"


def read(path):
    return path.read_text(encoding="utf-8")


def main():
    # AMD's headers: frame generation 4.0.2, MIT, notice kept; only the include paths differ from the SDK's flat copy
    fg = read(FFX / "framegeneration" / "include" / "ffx_framegeneration.h")
    types = read(FFX / "framegeneration" / "include" / "ffx_framegeneration_api_types.h")
    assert "This file is part of the FidelityFX SDK." in fg and "Advanced Micro Devices, Inc." in fg, "AMD's notice is gone"
    for name, want in (("MAJOR", 4), ("MINOR", 0), ("PATCH", 2)):
        assert re.search(r"#define FFX_FRAMEGENERATION_VERSION_%s %d\b" % (name, want), fg), "frame generation %s is not %d" % (name, want)
    assert '#include "../../api/include/ffx_api.h"' in fg and '#include "../../api/include/ffx_api_types.h"' in types, "the include paths"
    for needle in ("ffxCreateContextDescFrameGeneration", "ffxCreateContextDescFrameGenerationVersion", "ffxConfigureDescFrameGeneration",
                   "ffxDispatchDescFrameGenerationPrepareV2", "ffxDispatchDescFrameGeneration", "FFX_FRAMEGENERATION_FLAG_NO_SWAPCHAIN_CONTEXT_NOTIFY",
                   "FFX_API_QUERY_DESC_TYPE_FRAMEGENERATION_GPU_MEMORY_USAGE"):
        assert needle in fg, "AMD's header lacks " + needle
    assert not list((FFX).rglob("*.dll")), "an AMD DLL is in the tree: none may be committed"

    src = read(VK / "vk_fsr4fg.cpp")
    # 1. the capability: AMD's own query, for the frame generation effect, a 4.x provider only, with no context
    ask = src[src.index("void ask_locked(State& s) {"):src.index("void probe_locked(State& s) {")]
    assert "versions.createDescType = FFX_API_CREATE_CONTEXT_DESC_TYPE_FRAMEGENERATION;" in ask, "the capability query is not for frame generation"
    assert "FFX_API_QUERY_DESC_TYPE_GET_VERSIONS" in ask and "s.query(nullptr, &versions.header)" in ask, "the query is not AMD's version list with no context"
    assert "v[0] == 4" in ask, "a 3.x provider could stand in for FSR 4 frame generation"
    assert ask.count("return no(FSR4FG_NO_PROVIDER)") >= 3 and "return no(FSR4FG_NO_LOADER)" in ask, "a reason for each way it is not offered"
    assert 'logf("vk: AMD FSR 4 FG: not offered' in ask and 'logf("vk: AMD FSR 4 FG: offered' in ask, "the log does not say why it is not offered here"
    assert "NOT TRIED ON REAL HARDWARE" in ask, "the offered line does not say it is untested"
    # nothing is asked before it is chosen (an NVIDIA PC loads nothing of AMD's unless the row or the switch asks)
    wanted = src[src.index("bool fsr4fg_wanted() {"):src.index("void fsr4fg_idle() {")]
    assert "opts.framegen_by == framegen_rows::BY_FSR4" in wanted and "env_fg() == 1" in wanted and "env_fg() == 0" in wanted
    probe_calls = [m.start() for m in re.finditer(r"fsr4fg_probe\(\)", wanted)]
    assert probe_calls and wanted.index("if (!chosen) return false;") < probe_calls[0], "the runtime is asked before FSR 4 is chosen"

    # 2. the context: frame generation description -> version -> backend -> override version, as the API guide chains them
    ctx = src[src.index("std::shared_ptr<Context> make_context("):src.index("/// Contexts let go a few frames ago")]
    assert "c->desc.header.pNext = &c->api_version.header;" in ctx and "c->api_version.version = FFX_FRAMEGENERATION_VERSION;" in ctx
    assert "c->api_version.header.pNext = &c->backend.header;" in ctx and "c->backend.header.pNext = &c->version.header;" in ctx, "the description chain"
    assert "FFX_FRAMEGENERATION_ENABLE_DEPTH_INVERTED" in ctx and "FFX_API_SURFACE_FORMAT_R8G8B8A8_UNORM" in ctx
    assert "ffxCreateContext said %u (%s)" in ctx, "a refused context is not said in words"

    # 3. the frame: configure, then prepare V2, then the frame generation dispatch; one frame ID; no swapchain, no callbacks
    frames = src[src.index("int fsr4fg_frames("):src.index("#else  // (no HOOKSHOT_FSR")]
    c, p, g = (frames.index(x) for x in ("s.configure(&s.context->context", "s.dispatch(&s.context->context, &prep.header)", "s.dispatch(&s.context->context, &gen.header)"))
    assert c < p < g, "AMD's mandatory call order: configure, PrepareV2, dispatch"
    assert frames.count("= sequence;") >= 3 and "cfg.frameID = sequence;" in frames and "prep.frameID = sequence;" in frames and "gen.frameID = sequence;" in frames, \
        "the frame ID is not one number for the three calls"
    assert "sequence != s.last_sequence + 1" in frames, "a gap in the frame IDs is not a reset"
    assert "cfg.swapChain = nullptr;" in frames and "cfg.flags = FFX_FRAMEGENERATION_FLAG_NO_SWAPCHAIN_CONTEXT_NOTIFY;" in frames
    assert "prep.flags = FFX_FRAMEGENERATION_FLAG_NO_SWAPCHAIN_CONTEXT_NOTIFY;" in frames, "PrepareV2 does not say there is no swapchain"
    assert "gen.numGeneratedFrames = 1;" in frames and "FFX_API_DISPATCH_DESC_TYPE_FRAMEGENERATION_PREPARE_V2" in frames
    for camera in ("cameraPosition", "cameraRight", "cameraUp", "cameraForward"):
        assert "prep.%s[i]" % camera in frames, "PrepareV2's camera: " + camera
    assert "native_pq ||" in frames, "the game's PQ picture is not left to the renderer's own"
    # every refusal: logged with the code in words, nothing claimed made, layouts put back, three in a row end it for the run
    assert "words(rc)" in frames and "return 0;   // (a half-made set" in frames and "s.failed = true;" in frames and "kFailuresOff" in frames
    assert frames.index("barriers(cmd, to_general, 4);") > g, "the images are not put back after AMD's dispatches"
    assert frames.rindex("return 1;") > frames.index("s.made++;"), "made is claimed only after all three calls said OK"
    # AMD's every return code has its words
    api = read(FFX / "api" / "include" / "ffx_api.h")
    codes = re.findall(r"(FFX_API_RETURN_\w+)\s*=\s*\d+", api)
    assert len(codes) >= 8, codes
    words = src[src.index("const char* words(ffxReturnCode_t rc) {"):src.index("/// A context and the descriptions it was made from")]
    for code in codes:
        assert "case %s:" % code in words, "no words for " + code

    # 4. the stubs: a build without AMD's headers, or off Windows, offers nothing and makes nothing
    stub = src[src.index("#else  // (no HOOKSHOT_FSR"):]
    assert "bool fsr4fg_offered() { return false; }" in stub and "bool fsr4fg_wanted() { return false; }" in stub and "{ return 0; }" in stub
    assert "defined(HOOKSHOT_FSR) && defined(_WIN32)" in src
    # the build gives it AMD's headers as it does vk_fsr4.cpp
    build = read(ROOT / "nx2" / "build.py")
    assert '("vk_fsr4.cpp", "vk_fsr4fg.cpp")' in build, "vk_fsr4fg.cpp is not built with AMD's headers"

    # 5. the renderer: AMD FSR 4 first at 2x where chosen and offered; a refusal falls through to AMD FSR 3.1's interpolation, then the renderer's own
    framegen = read(VK / "vk_framegen.cpp")
    record = framegen[framegen.index("bool framegen_record("):framegen.index("void framegen_queue(")]
    a = record.index("set->made = fsr4_frame(")
    b = record.index("if (set->made == 0 && blend && fsr_fi) set->made = fsr3fi_frame(")
    c2 = record.index("if (set->made == 0 && blend && m > 1) {")
    assert a < b < c2, "AMD FSR 4 is not tried first, with AMD FSR 3.1 and the renderer's own after it"
    assert "const bool fsr4 = m == 2 && !dlssg && framegen_by_now() == framegen_rows::BY_FSR4;" in record
    assert "if (!fsr4) fsr4fg_idle();" in record, "its context is not given back when the row leaves it"
    by = framegen[framegen.index("int framegen_by_now() {"):framegen.index("int framegen_now() {")]
    assert by.index("BY_DLSS") < by.index("BY_FSR4") < by.index("BY_FSR3 ? framegen_rows::BY_FSR3"), "framegen_by_now's order"
    # the menu says it is untested and why it is not offered
    menu = read(VK / "vk_menu.cpp")
    assert "NOT TESTED on real hardware" in menu and "fsr4fg_why_text(fsr4fg_why())" in menu, "the row's help does not say it is untested / why it is off"
    print("PASS: AMD FSR 4 frame generation: AMD's call order and structures, the capability query, every return code in words, the fallbacks")


if __name__ == "__main__":
    main()
