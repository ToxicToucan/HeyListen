// Copyright (C) 2026 Sacrappy

/* (heylisten) The performance summary (perf_report.cpp gathers it, the launcher sends it: nx2/perf_report.py): what a
   player who ticked "Send performance statistics" in the launcher sends about their frames, split by the map they were
   on. Every few minutes and at the end the game writes one summary, the counts since the one before, into the game folder's
   perf_pending folder; the launcher sends them to the perf relay (the HeyListen telemetry server's telemetry, /telemetry/perf: telemetry/perf there, whose
   format this is) at its next start, and deletes them.

   Plain functions and a summary's data: tests/test_perf_report.cpp checks them. The bucket lists are the relay's
   (FRAME_BUCKETS_MS, GPU_BUCKETS_MS): change both together. */
#pragma once

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <string>
#include <vector>

#include "fps_stats.h"

namespace mkwvk {
/// (perf_report.cpp) whether the build has an address to send to (nx2/addresses.ini's telemetry=, or HL_TELEMETRY_URL): the two
/// telemetry rows of the menu say "not available in this build" without one
bool telemetry_available();
/// (perf_report.cpp) a frame was shown: into the summary when Send performance statistics is on and the build has an address
void perf_frame_shown();
/// (perf_report.cpp) a renderer's event (perf_report::Event), counted into the next summary
void perf_event(int event);
/// (perf_report.cpp) the last summary at once, with that event (-1 none: the window closed; a lost device: the process ends next)
void perf_report_final(int event);
/// (vk_replay.cpp) the GPU's own time so far, ns
int64_t gpu_time_ns();
/// (vk_replay.cpp) the read-backs' waits for the GPU so far, ns: the game's CPU reads of GPU results (invalidate), any thread
int64_t readback_wait_ns();
/// (vk_replay.cpp, v0.4.4) the longest single read's wait since the last call, ns (0: none of 1 ms or more), and its label (readback_wait.h)
int64_t readback_worst_take(char* label, size_t cap);
/// (perf_report.cpp) a draw's program key, once per key per map: the zone usage recorder (zone_report.h), on only while
/// the performance statistics row is on, the build has an address and HL_TELEMETRY_ZONES is not 0
void zone_note_program(uint64_t key);
/// (perf_report.cpp) the same for a graphics pipeline's key, where the draw looks it up
void zone_note_state(uint64_t key);
}   // namespace mkwvk

namespace mkwvk::perf_report {

/// Frame times around the usual refresh rates (240, 144, 120, 90, 60, 50, 40, 30, 20 fps) and the stutters past them, ms
constexpr double kFrameBuckets[] = {4.17, 6.94, 8.33, 11.1, 16.7, 20, 25, 33.3, 50, 100, 250, 1000};
constexpr int kFrameBucketCount = sizeof kFrameBuckets / sizeof kFrameBuckets[0] + 1;   // (+Inf last)
/// The GPU's own time a frame, ms
constexpr double kGpuBuckets[] = {2, 4, 6, 8.33, 11.1, 16.7, 25, 33.3, 50, 100};
constexpr int kGpuBucketCount = sizeof kGpuBuckets / sizeof kGpuBuckets[0] + 1;

template <size_t N>
inline int bucket_of(double ms, const double (&bounds)[N]) {
    for (size_t i = 0; i < N; i++)
        if (ms <= bounds[i]) return (int)i;
    return (int)N;
}

/// What a slow frame waited on most (the relay's CAUSES, in its order; v0.4.3 adds the four after "pace", before "other",
/// and the server takes new keys as they come: it stores the summary's JSON as it is)
enum Cause { kPipelineWait, kShaderTranslate, kTextureUpload, kMemsync, kGpuBound, kReadback, kGameCpu, kPacing,
             kDriverCompile, kDescriptors, kPresentBlock, kOsWait, kOther, kCauses };
inline const char* cause_name(int c) {
    static const char* const t[] = {"pipeline_wait", "shader_translate", "texture_upload", "memsync", "gpu_bound", "readback",
                                    "game_cpu", "pace", "driver_compile", "descriptors", "present", "os_wait", "other"};
    return c >= 0 && c < kCauses ? t[c] : "other";
}

/// The "map" before the game is in one: the start-up screen preparing the shaders, then the intro, title and file select
/// (the crash report names them the same: host/hos_crash_dialog.h)
constexpr const char* kPreparingShaders = "Preparing shaders";
constexpr const char* kTitleAndIntro = "Title and intro";

/// The renderer's events (the relay's EVENTS)
enum Event { kProgramFailed, kDeviceLost, kSwapchainRecreated, kMovieUpscalerDropped, kDlssRecreated, kVramOverBudget, kEvents };
inline const char* event_name(int e) {
    static const char* const t[] = {"program_failed", "device_lost", "swapchain_recreated", "movie_upscaler_dropped",
                                    "dlss_recreated", "vram_over_budget"};
    return e >= 0 && e < kEvents ? t[e] : "";
}

/// Who makes the frames the Frame generation row asks for (vk_framegen.h framegen_engine's values): not running, the renderer's
/// own interpolation, AMD FSR 3.1's frame interpolation (at 2x, where it runs), NVIDIA DLSS FG (the Frame generation by row's
/// choice, where its runtime runs and a temporal Upscaler's inputs are there: the renderer's own makes them elsewhere)
enum FrameGen { kFramegenOff, kFramegenBuiltin, kFramegenFsr3, kFramegenDlssg, kFramegens };
inline const char* framegen_name(int e) {
    static const char* const t[] = {"off", "builtin", "fsr3", "dlssg"};
    return e >= 0 && e < kFramegens ? t[e] : "other";
}

/// A DLSS preset (NVSDK_NGX_DLSS_Hint_Render_Preset_*: 1 A ... 26 Z, 0 NVIDIA's own pick) by its letter; "none" for no DLSS network
/// (the game's own or FSR upscales: the model row does nothing then)
inline std::string dlss_model_name(int preset, bool dlss_runs) {
    if (!dlss_runs) return "none";
    if (preset == 0) return "default";
    return preset >= 1 && preset <= 26 ? std::string(1, (char)('A' + preset - 1)) : "other";
}

/// A frame's times beyond the FPS counter's parts, ms: from the renderer's counters, since the frame before. The parts
/// themselves (fps_stats::Frame) are measured on the presenting thread (perf_parts.h); these are what the parts were made of
struct Waits {
    double pipeline = 0;    // (building pipelines and picking programs on the drawing threads)
    double translate = 0;   // (draws waiting for a shader translation, or translating one in place)
    double upload = 0;      // (texture uploads and their CPU unswizzle)
    double memsync = 0;     // (the guest memory's sync to the GPU)
    double readback = 0;    // (read-backs' waits for the GPU: the game's CPU reads of GPU results, vk_replay.cpp invalidate)
    double driver_compile = 0;  // (the driver's pipeline compiles: vkCreate*Pipelines, the GPL libraries and their links)
    double descriptors = 0;    // (descriptor building and binding on the drawing threads)
    double present = 0;        // (the swapchain: waiting for its image and the present call, vk_replay.cpp present_image)
    double os_wait = 0;        // (the presenting thread off the CPU while nothing of the renderer ran: a gap with no work)
    // (v0.4.4: what the frame's longest wait of those two causes was on: readback_wait.h label of the read that waited most,
    // the descriptor work that built longest (draw or dispatch, pool or push); empty: not known. Put in the hitch's detail row)
    std::string readback_which, descriptors_which;
    // (v0.4.4: what the presenting thread was blocked on this frame, ms, by kind (host/hos_waitnote.h; the pacing sleep taken out of
    // the sleeps), the process's page faults and the working set's fall in MB since the frame before; ow_known: the host pushed the
    // waits (not HL_TEL_WAITS=0). The os_wait hitches' sub-cause: os_wait_kind)
    bool ow_known = false;
    double ow_lock = 0, ow_sleep = 0, ow_vcore = 0, ow_file = 0;
    int64_t faults = 0;
    double ws_drop_mb = 0;
    // (the descriptors' parts, ms, for HL_HITCH_LOG: the build, the texture work inside it, the pools)
    double desc_self = 0, desc_tex = 0, desc_pool = 0;
};

/// A frame's page faults (soft and hard together: the process's counter) from which the os_wait hitch's rest is called a page fault, and
/// the working set's fall (MB) from which it is called a trim
constexpr int64_t kFaultsMin = 2000;
constexpr double kTrimMb = 64;
/// What an os_wait hitch waited on (v0.4.4): the longest of the lock/condition, sleep, virtual-core and file waits when they make up at
/// least half of it, else "ws_trim" (the working set fell), "page_fault" (the process faulted a lot), else "off_cpu" (the thread was
/// not running and none of the above says why: preempted, or a wait the host does not time). "" when the host sent no waits
inline std::string os_wait_kind(const Waits& w) {
    if (!w.ow_known) return "";
    const struct { const char* name; double ms; } k[] = {{"lock", w.ow_lock}, {"sleep", w.ow_sleep}, {"vcore", w.ow_vcore}, {"file", w.ow_file}};
    int best = 0;
    for (int i = 1; i < 4; i++)
        if (k[i].ms > k[best].ms) best = i;
    if (k[best].ms > 0 && k[best].ms >= 0.5 * w.os_wait) return k[best].name;
    if (w.ws_drop_mb >= kTrimMb) return "ws_trim";
    if (w.faults >= kFaultsMin) return "page_fault";
    return "off_cpu";
}

/// Seconds of play between summaries: RX_VK_PERF_INTERVAL's whole seconds, 60 to 840, else 300. A summary ends on the frame
/// past it, and the relay takes a map's seconds up to 900: 840 leaves a minute for that frame
constexpr int kPeriodDefault = 300, kPeriodMin = 60, kPeriodMax = 840;
/// The maps a summary holds at most: the relay refuses one with more (400), and the sender then deletes it. A map past them
/// ends the summary early (perf_report.cpp where_now)
constexpr int kMaxMaps = 16;
inline int summary_period(const char* env) {
    char* end = nullptr;
    const long s = env ? strtol(env, &end, 10) : 0;
    if (!env || end == env || *end) return kPeriodDefault;   // (unset, empty or not a number: the default)
    return (int)std::clamp<long>(s, kPeriodMin, kPeriodMax);
}

/// A hitch: a frame over twice its target, and at least 25 ms (a 240 fps frame twice as long is not a stutter anyone sees)
inline bool is_hitch(double ms, double target_ms) { return ms > std::max(2.0 * target_ms, 25.0); }

/// What the hitch waited on most: the renderer's own waits first, then the FPS counter's parts (the GPU's, the pacing's, the
/// game's); "other" when nothing measured is a quarter of the frame. The game's part splits in two: its CPU at work
/// (game_cpu) and the wall time the presenting thread was neither running nor in the renderer nor sleeping for pacing
/// (os_wait: the OS delayed it, or something the renderer cannot see). The driver's compiles, the descriptor work and the
/// swapchain's block are their own causes even inside the render part
inline Cause cause_of(const fps_stats::Frame& f, const Waits& w) {
    const double c[kCauses] = {w.pipeline, w.translate, w.upload, w.memsync, std::max<double>(f.part[fps_stats::kGpuWait], f.gpu_ms), w.readback,
                               std::max(0.0, f.part[fps_stats::kGame] - w.os_wait), f.part[fps_stats::kPace], w.driver_compile,
                               w.descriptors, w.present, w.os_wait, 0};
    int best = kOther;
    double most = f.ms * 0.25;
    for (int i = 0; i < kOther; i++)
        if (c[i] > most) best = i, most = c[i];
    return (Cause)best;
}

/// What the descriptor work of a frame was mostly (v0.4.4, the hitch rows' label): the sets and pools of the descriptor pools
/// (made, reset, allocated: a scene with more sets than a recording's pool holds), or the build of the descriptors themselves
inline std::string descriptor_kind(double self_ms, double pool_ms) { return pool_ms > self_ms ? "pool" : "build"; }

/// A frame off its slot: more than a quarter of the target away from the frame before (uneven pacing, even at a good
/// average). Hitches are counted apart
inline bool is_pacing_miss(double ms, double last_ms, double target_ms) {
    return last_ms > 0 && std::abs(ms - last_ms) > 0.25 * target_ms;
}

/// One hitch cause's detail since the summary before: the hitch frames' count, their times' sum and their longest, and the
/// same three of those that happened while the game was loading a map (an area change: host/hos_main.c's push). The map's
/// "new" says whether that was the session's first visit
struct HitchDetail {
    int64_t count = 0;
    double ms_sum = 0, ms_max = 0;
    int64_t load_count = 0;
    double load_ms_sum = 0, load_ms_max = 0;
    /// (v0.4.4, readback and descriptors hitches) what the frame waited on, by label (readback_wait.h): the hitches of each, at
    /// most kWhichMax labels, the rest under "other"
    std::map<std::string, int64_t> which;
    /// (v0.4.4, os_wait hitches) the same labels' frame times in ms: how long each wait kind held the hitches
    std::map<std::string, double> which_ms;
};
constexpr size_t kWhichMax = 8;
inline void note_which(HitchDetail& d, const std::string& label) {
    if (label.empty()) return;
    auto it = d.which.find(label);
    if (it == d.which.end() && d.which.size() >= kWhichMax) it = d.which.try_emplace("other", 0).first;
    else if (it == d.which.end()) it = d.which.try_emplace(label, 0).first;
    it->second++;
}

/// A hitch (or stall) just counted, kept a few seconds: the map change that comes after it is seen late (the game thread that names the
/// map is the one the load blocks), so the load's own frames are marked as its hitches once the change is seen (reclassify_as_load)
struct Recent {
    int cause;        // (a Cause; -1: a stall)
    double ms;
    int64_t at_ms;    // (the frame's time on any steady clock)
    bool loaded;      // (already counted in the load columns)
};
constexpr int64_t kLoadWindowMs = 5000;   // (how far back from the map change's sight a hitch is the load's)
constexpr size_t kRecentMax = 64;

/// The totals of one map since the summary before
struct MapTotals {
    bool fresh = true;   // (the first summary of this session with this map: the relay counts it as a session on it)
    double seconds = 0;
    int64_t frame[kFrameBucketCount] = {};
    double frame_sum = 0;
    int64_t gpu[kGpuBucketCount] = {};
    double gpu_sum = 0;
    double part[fps_stats::kParts] = {};
    int64_t hitch[kCauses] = {};
    HitchDetail hitch_detail[kCauses] = {};
    int64_t pacing_misses = 0, left_out = 0;
    /// (v0.4.4) the gaps between two frames of over 2 s (up to a minute), which the frame counts leave out: the longest freezes, a load's
    /// among them. stall_load: those within a map change's window
    int64_t stall_count = 0, stall_load_count = 0;
    double stall_ms_sum = 0, stall_ms_max = 0;
    int64_t event[kEvents] = {};
    int64_t movie_shown = 0, movie_dropped = 0, movie_late = 0, movie_fallbacks = 0;
    double movie_decode_ms = 0;
    bool any() const {
        return seconds > 0 || stall_count > 0 || movie_shown || movie_dropped || movie_late || movie_fallbacks || movie_decode_ms > 0 ||
               std::any_of(event, event + kEvents, [](int64_t v) { return v > 0; });
    }
};

/// usual_ms: the recent average frame. A hitch is measured against the longer of it and the target: a game the GPU holds
/// at 20 fps under a 60 cap has slow frames, not stutters (its fps says so), and each of its frames would be one otherwise.
/// loading: the game is loading a map (host/hos_main.c's flag), the hitch rows' load side
inline void note_which_ms(HitchDetail& d, const std::string& label, double ms) {
    if (label.empty()) return;
    auto it = d.which_ms.find(label);
    if (it == d.which_ms.end() && d.which_ms.size() >= kWhichMax) it = d.which_ms.try_emplace("other", 0.0).first;
    else if (it == d.which_ms.end()) it = d.which_ms.try_emplace(label, 0.0).first;
    it->second += ms;
}
inline void remember(std::vector<Recent>* recent, const Recent& r) {
    if (!recent) return;
    while (!recent->empty() && (recent->size() >= kRecentMax || r.at_ms - recent->front().at_ms > kLoadWindowMs))
        recent->erase(recent->begin());
    recent->push_back(r);
}
/// The map change was just seen (m: the map it left): the hitches and stalls of the window before it that the loading flag did not mark
/// are the load's. Returns how many were marked; the list is emptied (they are counted)
inline int reclassify_as_load(MapTotals& m, std::vector<Recent>& recent, int64_t now_ms) {
    int n = 0;
    for (Recent& r : recent) {
        if (r.loaded || now_ms - r.at_ms > kLoadWindowMs) continue;
        if (r.cause < 0) m.stall_load_count++;
        else {
            HitchDetail& d = m.hitch_detail[r.cause];
            d.load_count++, d.load_ms_sum += r.ms, d.load_ms_max = std::max<double>(d.load_ms_max, r.ms);
        }
        r.loaded = true, n++;
    }
    recent.clear();
    return n;
}
/// A gap of over 2 s between two frames (up to kStallMaxMs): counted apart, as the frame buckets leave it out
constexpr double kStallMinMs = 2000, kStallMaxMs = 60000;
inline void add_stall(MapTotals& m, double ms, bool loading, std::vector<Recent>* recent = nullptr, int64_t now_ms = 0) {
    if (ms <= kStallMinMs || ms > kStallMaxMs) return;
    m.stall_count++, m.stall_ms_sum += ms, m.stall_ms_max = std::max(m.stall_ms_max, ms);
    if (loading) m.stall_load_count++;
    remember(recent, {-1, ms, now_ms, loading});
}

inline void add_frame(MapTotals& m, const fps_stats::Frame& f, const Waits& w, double target_ms, double last_ms,
                      double usual_ms = 0, bool loading = false, std::vector<Recent>* recent = nullptr, int64_t now_ms = 0) {
    m.seconds += f.ms / 1000.0;
    m.frame[bucket_of(f.ms, kFrameBuckets)]++;
    m.frame_sum += f.ms;
    if (f.gpu_ms > 0) m.gpu[bucket_of(f.gpu_ms, kGpuBuckets)]++, m.gpu_sum += f.gpu_ms;
    for (int p = 0; p < fps_stats::kParts; p++) m.part[p] += f.part[p];
    if (is_hitch(f.ms, std::max(target_ms, usual_ms))) {
        const Cause c = cause_of(f, w);
        m.hitch[c]++;
        HitchDetail& d = m.hitch_detail[c];
        d.count++, d.ms_sum += f.ms, d.ms_max = std::max<double>(d.ms_max, f.ms);
        if (c == kReadback) note_which(d, w.readback_which);
        else if (c == kDescriptors) note_which(d, w.descriptors_which);
        else if (c == kOsWait) {   // (v0.4.4: which wait: os_wait_kind, with the frame's ms under it)
            const std::string kind = os_wait_kind(w);
            note_which(d, kind), note_which_ms(d, kind, f.ms);
        }
        if (loading) d.load_count++, d.load_ms_sum += f.ms, d.load_ms_max = std::max<double>(d.load_ms_max, f.ms);
        remember(recent, {(int)c, f.ms, now_ms, loading});
    } else if (is_pacing_miss(f.ms, last_ms, target_ms)) m.pacing_misses++;
    m.left_out += f.left_out;
}

/// What the summary says of the machine and the settings (the relay derives the classes itself)
struct Machine {
    std::string version, os, kernel, renderer = "Vulkan", gpu, driver, cpu, upscaler, preset;
    bool release = false;
    double vram_gb = 0, ram_gb = 0;
    int fps_cap = 0;   // (0: uncapped)
    std::string framegen = "off", dlss_model = "none";   // (framegen_name; dlss_model_name)
    int framegen_x = 1;      // (the frames shown for each of the game's: 1 with frame generation off; Auto: the most it makes)
    bool framegen_auto = false;   // (the Auto row: framegen_x is then its limit, not a fixed multiplier)
    int render_scale = 0;    // (the 3D's share of the output in percent, 0: Auto, the game's dynamic resolution)
};

struct Memory {
    double vram_used_gb = 0, vram_allowed_gb = 0, ram_used_gb = 0;
    // (v0.4.4, extra: the memory record's new fields, on when perf_report.cpp could read the system's memory) the PC's RAM class, the
    // least free RAM the summary's period saw (GB; < 0 not read), the highest commit charge in percent of the limit (< 0 not read),
    // the working-set trims (a fall of kTrimMb or more between two frames) and the process's page faults in the period, the cold pages'
    // hold (HL_MEM_COLD_LOWMEM set: cold_line; tenths of a second held in all, passes left out in all)
    bool extra = false;
    std::string ram_class;
    double free_min_gb = -1, commit_pct_max = -1;
    int64_t ws_trims = 0, page_faults = 0;
    bool cold_line = false;
    int64_t cold_held_ds = 0, cold_passes = 0;
};
/// The PC's RAM as a class, not the figure: "<=4", "8" (up to 8), "16", "32", "64+" by the RAM the OS reports (a 16 GB PC reports 15.x)
inline const char* ram_class_of(double ram_gb) {
    if (ram_gb <= 0) return "unknown";
    if (ram_gb <= 4.5) return "<=4";
    if (ram_gb <= 8.5) return "8";
    if (ram_gb <= 16.5) return "16";
    if (ram_gb <= 32.5) return "32";
    return "64+";
}

/// A string as JSON: quotes, backslashes and control characters escaped, cut at `limit` bytes (the relay's limits), never
/// inside a UTF-8 character (half of one would make the whole file unreadable)
inline std::string json_text(const std::string& s, size_t limit = 120) {
    size_t n = std::min(s.size(), limit);
    while (n > 0 && n < s.size() && ((unsigned char)s[n] & 0xC0) == 0x80) n--;
    std::string out = "\"";
    for (size_t i = 0; i < n; i++) {
        const unsigned char ch = (unsigned char)s[i];
        if (ch == '"' || ch == '\\') out += '\\', out += (char)ch;
        else if (ch < 0x20) out += ' ';
        else out += (char)ch;
    }
    return out + "\"";
}

inline std::string json_num(double v) {
    char b[32];
    snprintf(b, sizeof b, "%.3f", std::max(v, 0.0));
    std::string s = b;
    s.erase(s.find_last_not_of('0') + 1);
    if (s.back() == '.') s.pop_back();
    return s;
}

template <class T, size_t N>
std::string json_list(const T (&v)[N]) {
    std::string out = "[";
    for (size_t i = 0; i < N; i++) out += (i ? "," : "") + json_num((double)v[i]);
    return out + "]";
}

/// The summary, as the relay takes it (version 1)
inline std::string summary_json(const Machine& m, bool first, double session_s, const std::string& map, const Memory* mem,
                                const std::map<std::string, MapTotals>& maps) {
    std::string o = "{\"v\":1,\"version\":" + json_text(m.version, 40) + ",\"release\":" + (m.release ? "true" : "false") +
                    ",\"first\":" + (first ? "true" : "false") + ",\"session_s\":" + json_num(session_s) +
                    ",\"os\":" + json_text(m.os, 60) + ",\"kernel\":" + json_text(m.kernel, 60) +
                    ",\"renderer\":" + json_text(m.renderer, 40) + ",\"gpu\":" + json_text(m.gpu) +
                    ",\"driver\":" + json_text(m.driver, 60) + ",\"vram_gb\":" + json_num(m.vram_gb) +
                    ",\"cpu\":" + json_text(m.cpu) + ",\"ram_gb\":" + json_num(m.ram_gb) +
                    ",\"upscaler\":" + json_text(m.upscaler, 40) + ",\"preset\":" + json_text(m.preset, 40) +
                    ",\"fps_cap\":" + std::to_string(m.fps_cap) + ",\"framegen\":" + json_text(m.framegen, 40) +
                    ",\"framegen_x\":" + std::to_string(m.framegen_x) + ",\"framegen_auto\":" + (m.framegen_auto ? "true" : "false") +
                    ",\"dlss_model\":" + json_text(m.dlss_model, 40) + ",\"render_scale\":" + std::to_string(m.render_scale) +
                    ",\"map\":" + json_text(map);
    if (mem)
        o += ",\"memory\":{\"vram_used_gb\":" + json_num(mem->vram_used_gb) + ",\"vram_allowed_gb\":" +
             json_num(mem->vram_allowed_gb) + ",\"ram_used_gb\":" + json_num(mem->ram_used_gb);
    if (mem && mem->extra) {   // (v0.4.4: new fields at the end of the object; the three before are as they were)
        o += ",\"ram_class\":" + json_text(mem->ram_class, 12);
        if (mem->free_min_gb >= 0) o += ",\"ram_free_min_gb\":" + json_num(mem->free_min_gb);
        if (mem->commit_pct_max >= 0) o += ",\"commit_pct_max\":" + json_num(mem->commit_pct_max);
        o += ",\"ws_trims\":" + std::to_string(mem->ws_trims) + ",\"page_faults\":" + std::to_string(mem->page_faults);
        if (mem->cold_line)
            o += ",\"cold_held_s\":" + json_num(mem->cold_held_ds / 10.0) + ",\"cold_passes\":" + std::to_string(mem->cold_passes);
    }
    if (mem) o += "}";
    o += ",\"maps\":{";
    bool comma = false;
    for (const auto& [name, t] : maps) {
        if (!t.any()) continue;
        double frames = 0;   // (the frames this summary counted on the map: the parts' averages are over them)
        for (int b = 0; b < kFrameBucketCount; b++) frames += (double)t.frame[b];
        const auto avg = [&](double sum) { return frames > 0 ? sum / frames : 0; };
        o += std::string(comma ? "," : "") + json_text(name) + ":{\"new\":" + (t.fresh ? "true" : "false") +
             ",\"seconds\":" + json_num(t.seconds) + ",\"frame_ms\":" + json_list(t.frame) + ",\"frame_ms_sum\":" +
             json_num(t.frame_sum) + ",\"gpu_ms\":" + json_list(t.gpu) + ",\"gpu_ms_sum\":" + json_num(t.gpu_sum) +
             ",\"parts_ms\":{\"game\":" + json_num(avg(t.part[fps_stats::kGame])) + ",\"render\":" +
             json_num(avg(t.part[fps_stats::kRender])) + ",\"gpu_wait\":" + json_num(avg(t.part[fps_stats::kGpuWait])) +
             ",\"pace\":" + json_num(avg(t.part[fps_stats::kPace])) + "},\"hitches\":{";
        for (int c = 0; c < kCauses; c++) o += std::string(c ? "," : "") + "\"" + cause_name(c) + "\":" + std::to_string(t.hitch[c]);
        o += "},\"hitch_detail\":{";
        bool comma2 = false;
        for (int c = 0; c < kCauses; c++) {
            const HitchDetail& d = t.hitch_detail[c];
            if (!d.count) continue;
            o += std::string(comma2 ? "," : "") + "\"" + cause_name(c) + "\":{\"count\":" + std::to_string(d.count) +
                 ",\"ms_sum\":" + json_num(d.ms_sum) + ",\"ms_max\":" + json_num(d.ms_max) +
                 ",\"load_count\":" + std::to_string(d.load_count) + ",\"load_ms_sum\":" + json_num(d.load_ms_sum) +
                 ",\"load_ms_max\":" + json_num(d.load_ms_max);
            if (!d.which.empty()) {   // (v0.4.4: the labels of what the readback and descriptor hitches waited on)
                o += ",\"which\":{";
                bool comma3 = false;
                for (const auto& [label, n] : d.which) {
                    o += std::string(comma3 ? "," : "") + json_text(label, 60) + ":" + std::to_string(n);
                    comma3 = true;
                }
                o += "}";
            }
            if (!d.which_ms.empty()) {   // (v0.4.4, os_wait: the same labels' hitch ms)
                o += ",\"which_ms\":{";
                bool comma3 = false;
                for (const auto& [label, ms] : d.which_ms) {
                    o += std::string(comma3 ? "," : "") + json_text(label, 60) + ":" + json_num(ms);
                    comma3 = true;
                }
                o += "}";
            }
            o += "}";
            comma2 = true;
        }
        o += "},\"pacing_misses\":" + std::to_string(t.pacing_misses) + ",\"draws_left_out\":" + std::to_string(t.left_out) +
             ",\"events\":{";
        for (int e = 0; e < kEvents; e++) o += std::string(e ? "," : "") + "\"" + event_name(e) + "\":" + std::to_string(t.event[e]);
        o += "},\"movie\":{\"shown\":" + std::to_string(t.movie_shown) + ",\"dropped\":" + std::to_string(t.movie_dropped) +
             ",\"late\":" + std::to_string(t.movie_late) + ",\"decode_ms\":" + json_num(t.movie_decode_ms) +
             ",\"fallbacks\":" + std::to_string(t.movie_fallbacks) + "}";
        if (t.stall_count)   // (v0.4.4: the gaps of over 2 s the frame buckets leave out)
            o += ",\"stalls\":{\"count\":" + std::to_string(t.stall_count) + ",\"ms_sum\":" + json_num(t.stall_ms_sum) +
                 ",\"ms_max\":" + json_num(t.stall_ms_max) + ",\"load_count\":" + std::to_string(t.stall_load_count) + "}";
        o += "}";
        comma = true;
    }
    return o + "}}";
}

/// A release's version: v1.2.3, v1.2.3-rc.4, nightly-<count>-<8 hex> (as the crash report's release flag: host/hos_crash_dialog.h)
inline bool release_version(const std::string& v) {
    size_t i = 0;
    auto digits = [&] {
        const size_t from = i;
        while (i < v.size() && v[i] >= '0' && v[i] <= '9') i++;
        return i > from;
    };
    if (!v.compare(0, 8, "nightly-")) {
        i = 8;
        if (!digits() || i >= v.size() || v[i++] != '-' || v.size() - i != 8) return false;
        for (; i < v.size(); i++)
            if (!((v[i] >= '0' && v[i] <= '9') || (v[i] >= 'a' && v[i] <= 'f'))) return false;
        return true;
    }
    if (i < v.size() && v[i] == 'v') i++;
    for (int part = 0; part < 3; part++) {
        if (!digits()) return false;
        if (part < 2 && (i >= v.size() || v[i++] != '.')) return false;
    }
    if (!v.compare(i, 4, "-rc.")) {
        i += 4;
        if (!digits()) return false;
    }
    return i == v.size();
}
/// The release flag from the game's version line ("v0.4.0 (source 1a2b3c4d)") and hookshot_version.txt's text: a release's version
/// built from the release's own sources (the line's source is the stamp's source=)
inline bool release_flag(const std::string& version_line, const std::string& stamp) {
    const size_t at = version_line.find(" (source ");
    if (at == std::string::npos) return false;
    const std::string bare = version_line.substr(0, at);
    const size_t close = version_line.find(')', at);
    const std::string source = version_line.substr(at + 9, close == std::string::npos ? std::string::npos : close - at - 9);
    std::string expected;
    for (size_t p = 0; p < stamp.size();) {
        const size_t nl = stamp.find('\n', p), end = nl == std::string::npos ? stamp.size() : nl;
        if (!stamp.compare(p, 7, "source=")) {
            expected = stamp.substr(p + 7, end - p - 7);
            while (!expected.empty() && (expected.back() == '\r' || expected.back() == ' ')) expected.pop_back();
        }
        p = end + 1;
    }
    return release_version(bare) && !source.empty() && source == expected;
}

/// "6.12.3-arch1-1" out of uname's release, "10.0.26100" out of Windows' version: the relay cuts them to major.minor
inline std::string kernel_of(const std::string& os_text) {
    const size_t at = os_text.find("(Linux ");
    if (at != std::string::npos) return os_text.substr(at + 7, os_text.find(')', at) - at - 7);
    const size_t b = os_text.find("(build ");
    if (b != std::string::npos) return "10.0." + os_text.substr(b + 7, os_text.find(')', b) - b - 7);
    return "";
}

}   // namespace mkwvk::perf_report
