// Copyright (C) 2026 Sacrappy
// Low-end GPUs: the volumetric fog's programs (the Volumetric fog row, vk_draw.cpp dispatch), the first-start rule that gives an
// integrated GPU without mesh shaders Render resolution 33%, the handheld profile and the fog off (vk_menu.cpp
// menu_first_run_lowend), and the VRAM mirror in place of the present's wait on AMD's (vk_memory.cpp mirror_on, vk_replay.cpp).
// tests/test_lowend_rule.py; the measures: docs/research/lowend-laptop.md
#pragma once
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace lowend_rule {

// The volumetric fog: 27ee9883e2a20323 (a 96x72x88 froxel volume with shadow lookups), ee7764a6b1ef459c (48x36x48) and
// 1e2fcee33a0c7209 (the 96x72 pass after the first). The first and the third ping-pong two sets of images by frame parity, so a
// lower rate is wrong (GPU11: the haze goes, or a white-out); left out from the start they write nothing and the scene reads no
// haze: no black, flashing or undefined parts (GPU11's pictures in Hyrule Field and the Lost Woods). On a Ryzen 7 5700U's Vega 8
// they cost about 9 ms of a 60 ms frame; on an RTX 3050 0.6-1.1 ms
inline bool fog_program(uint64_t key) {
    return key == 0x27ee9883e2a20323ull || key == 0xee7764a6b1ef459cull || key == 0x1e2fcee33a0c7209ull;
}
/// Whether the fog runs: RX_VK_FOG (env: -1 unset, else 0 off / 1 on) over the row (row: opts.fog, 1 on)
inline bool fog_on(int env, int row) { return env >= 0 ? env != 0 : row != 0; }

/// The GPUs the low-end preset is for: an integrated GPU that has no mesh shaders (task and mesh programs run as compute: the
// issue 640 fallback). AMD's Vega APUs (Ryzen 2000 to 5000 U/H/G) and Intel's Xe-LP graphics; not the RDNA 2/3 APUs (680M,
// 780M, 890M, the Steam Deck under RADV), which have mesh shaders
inline bool lowend_gpu(bool integrated, bool mesh_shader) { return integrated && !mesh_shader; }

/// The rows the rule sets, and only those (the options file's other lines are left as they are: a menu save would also write
/// every row's value, and the Minimum preset its pacing, window filter and sharpness): Render resolution 33% (value 6), the
/// handheld profile (read at the start: from the next one), Volumetric fog off
constexpr int kResolution = 6, kHandheld = 1, kFog = 0;
/// An AMD integrated GPU without mesh shaders (the Vega APUs): the VRAM mirror in place of the present's wait for its own frame
/// (RX_VK_MIRROR / RX_VK_WAIT_OWN over it). On a Ryzen 7 5700U both keep the HUD and the grass right (0 faulty HUD in 18 shots;
/// lists 2-3 pictures) and the mirror's way leaves the GPU fewer gaps (13-16 -> 8.5-11 ms a frame, +1 fps turning at 33%).
/// Not Intel's: not measured there
inline bool mirror_not_wait(uint32_t vendor_id, bool integrated, bool mesh_shader) {
    return vendor_id == 0x1002 && lowend_gpu(integrated, mesh_shader);
}

/// The options file's text with these keys' lines set to these values, every other line kept as it is, in its place and with its
/// own line ending; keys not there yet added at the end. The file's line ending (CRLF if any line has one, else LF) is used for the
/// lines written: an options file edited on Windows kept CRLF (the laptop's) and the rule's first version turned all of it to LF
inline std::string ini_put_text(const std::string& in, const std::vector<std::pair<std::string, int>>& keys) {
    const std::string eol = in.find("\r\n") != std::string::npos ? "\r\n" : "\n";
    std::string out;
    std::vector<bool> done(keys.size());
    for (size_t at = 0; at < in.size();) {
        const size_t end = in.find('\n', at);
        const size_t next = end == std::string::npos ? in.size() : end + 1;
        std::string body = in.substr(at, (end == std::string::npos ? in.size() : end) - at);
        if (!body.empty() && body.back() == '\r') body.pop_back();
        const size_t eq = body.find('=');
        bool replaced = false;
        for (size_t i = 0; eq != std::string::npos && i < keys.size(); i++)
            if (!done[i] && eq == keys[i].first.size() && body.compare(0, eq, keys[i].first) == 0) {
                out += keys[i].first + "=" + std::to_string(keys[i].second) + eol;
                done[i] = replaced = true;
            }
        if (!replaced) {
            out.append(in, at, next - at);
            if (end == std::string::npos) out += eol;
        }
        at = next;
    }
    for (size_t i = 0; i < keys.size(); i++)
        if (!done[i]) out += keys[i].first + "=" + std::to_string(keys[i].second) + eol;
    return out;
}

enum Apply { NONE, APPLY_AND_SAVE, APPLY_THIS_RUN };
/// The first-start rule: env is HL_LOWEND (-1 unset; 0 never; 1 as if the GPU were one, for this run only: nothing saved),
/// marked whether the options file already has the rule's line (lowend_rule=: once applied or declined, never again: the
/// player's choices stay theirs), gpu lowend_gpu's answer
inline Apply first_start(int env, bool marked, bool gpu) {
    if (env == 0) return NONE;
    if (env > 0) return APPLY_THIS_RUN;
    return gpu && !marked ? APPLY_AND_SAVE : NONE;
}

}  // namespace lowend_rule
