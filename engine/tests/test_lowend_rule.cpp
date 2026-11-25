// Copyright (C) 2026 Sacrappy
// lowend_rule (gpu/vk/lowend_rule.h): the volumetric fog's programs and the first-start rule for low-end GPUs.
// tests/test_lowend_rule.py
#include "../gpu/vk/lowend_rule.h"

#include <cstdio>

static int failures = 0;
static void expect(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        failures++;
    }
}

int main() {
    using namespace lowend_rule;
    expect(fog_program(0x27ee9883e2a20323ull), "the 96x72x88 fog volume");
    expect(fog_program(0xee7764a6b1ef459cull), "the 48x36x48 fog volume");
    expect(fog_program(0x1e2fcee33a0c7209ull), "the pass after the first volume");
    expect(!fog_program(0x53509044401d78d2ull), "not the noise volumes' program (the foliage's crossfade needs it)");
    expect(!fog_program(0x27ee9883e2a20324ull), "whole keys only");

    expect(fog_on(-1, 1), "row on, no switch: on");
    expect(!fog_on(-1, 0), "row off, no switch: off");
    expect(fog_on(1, 0), "RX_VK_FOG=1 over the row");
    expect(!fog_on(0, 1), "RX_VK_FOG=0 over the row");

    expect(lowend_gpu(true, false), "integrated without mesh shaders (a Vega 8)");
    expect(!lowend_gpu(true, true), "integrated with mesh shaders (a 780M)");
    expect(!lowend_gpu(false, false), "discrete without mesh shaders (an RX 5700): not this rule");
    expect(!lowend_gpu(false, true), "discrete with mesh shaders");

    expect(first_start(-1, false, true) == APPLY_AND_SAVE, "low-end GPU, never applied: applied and saved");
    expect(first_start(-1, true, true) == NONE, "applied once already: the player's rows stay");
    expect(first_start(-1, false, false) == NONE, "another GPU: nothing");
    expect(first_start(0, false, true) == NONE, "HL_LOWEND=0: never");
    expect(first_start(1, false, false) == APPLY_THIS_RUN, "HL_LOWEND=1 on any GPU: this run only");
    expect(first_start(1, true, true) == APPLY_THIS_RUN, "HL_LOWEND=1 after it was applied: this run only, nothing saved");

    expect(mirror_not_wait(0x1002, true, false), "AMD integrated without mesh shaders (a Vega 8): the mirror, no wait");
    expect(!mirror_not_wait(0x8086, true, false), "Intel integrated without mesh shaders: not measured, as before");
    expect(!mirror_not_wait(0x1002, true, true), "AMD integrated with mesh shaders (780M): as before");
    expect(!mirror_not_wait(0x1002, false, false), "AMD discrete: as before");
    expect(kResolution == 6 && kHandheld == 1 && kFog == 0, "the rule's rows: 33%, handheld, fog off");
    {   // (the options file's own lines and line endings kept; only the rule's keys rewritten or added)
        const std::vector<std::pair<std::string, int>> keys = {{"resolution", 6}, {"handheld_profile", 1}, {"volumetric_fog", 0},
                                                               {"lowend_rule", 1}};
        const std::string crlf = "microphone=2\r\nresolution=4\r\nframe_pacing=0\r\nhandheld_profile=0\r\n";
        expect(ini_put_text(crlf, keys) == "microphone=2\r\nresolution=6\r\nframe_pacing=0\r\nhandheld_profile=1\r\n"
                                           "volumetric_fog=0\r\nlowend_rule=1\r\n", "a CRLF file stays CRLF, appended lines too");
        const std::string lf = "resolution=4\nx=1\n";
        expect(ini_put_text(lf, keys) == "resolution=6\nx=1\nhandheld_profile=1\nvolumetric_fog=0\nlowend_rule=1\n", "an LF file stays LF");
        expect(ini_put_text("", keys) == "resolution=6\nhandheld_profile=1\nvolumetric_fog=0\nlowend_rule=1\n", "no file: the keys alone");
        expect(ini_put_text("resolution_x=1\r\nlast=2", keys).rfind("resolution_x=1\r\nlast=2\r\nresolution=6\r\n", 0) == 0,
               "a key that only starts the same is not the key; a last line without its ending gets the file's");
    }
    if (failures) return 1;
    std::printf("lowend_rule: all checks passed\n");
    return 0;
}
