// Copyright (C) 2026 Sacrappy

// tests/test_perf_report.py: gpu/vk/perf_report.h, the performance summary: its interval, its buckets, what a hitch is and what it waited on,
// the pacing misses, the JSON the relay takes, and what a frame costs to add
#include "../gpu/vk/perf_report.h"

#include <chrono>
#include <cstdio>
#include <cstring>

#define CHECK(name, condition) do { if (!(condition)) { std::fprintf(stderr, "FAIL %s at line %d\n", name, __LINE__); return 1; } } while (0)

namespace pr = mkwvk::perf_report;
namespace fs = mkwvk::fps_stats;

static fs::Frame frame(float ms, float game, float render, float gpu_wait, float pace) {
    fs::Frame f;
    f.ms = ms;
    f.part[fs::kGame] = game, f.part[fs::kRender] = render, f.part[fs::kGpuWait] = gpu_wait, f.part[fs::kPace] = pace;
    return f;
}

int main() {
    // the buckets: the relay's lists, an upper bound each, +Inf last
    CHECK("counts", pr::kFrameBucketCount == 13 && pr::kGpuBucketCount == 11);
    CHECK("a cut never splits a character", pr::json_text("ab\xc3\xa9", 3) == "\"ab\"" && pr::json_text("ab\xc3\xa9", 4) == "\"ab\xc3\xa9\"");
    CHECK("a 60 fps frame", pr::bucket_of(16.6, pr::kFrameBuckets) == 4);
    CHECK("on the bound", pr::bucket_of(16.7, pr::kFrameBuckets) == 4);
    CHECK("past every bound", pr::bucket_of(5000, pr::kFrameBuckets) == pr::kFrameBucketCount - 1);

    // the interval between summaries: RX_VK_PERF_INTERVAL, 60 to 840 s (the relay's 900 s, a minute left for the last frame)
    CHECK("unset: 5 minutes", pr::summary_period(nullptr) == 300 && pr::summary_period("") == 300);
    CHECK("10 minutes", pr::summary_period("600") == 600);
    CHECK("at least a minute", pr::summary_period("10") == 60 && pr::summary_period("-5") == 60);
    CHECK("at most 14 minutes", pr::summary_period("900") == 840 && pr::summary_period("99999999999") == 840);
    CHECK("not a number: the default", pr::summary_period("soon") == 300 && pr::summary_period("600s") == 300);

    // a hitch: over twice the target and at least 25 ms
    CHECK("60 fps, 40 ms", pr::is_hitch(40, 16.7));
    CHECK("60 fps, 30 ms", !pr::is_hitch(30, 16.7));
    CHECK("240 fps, 12 ms is no stutter", !pr::is_hitch(12, 4.17));
    CHECK("30 fps, 70 ms", pr::is_hitch(70, 33.3));
    {   // (a 60 cap the GPU holds at 20 fps: its 50 ms frames are slow, not hitches; a 120 ms one among them is)
        pr::MapTotals m;
        for (int i = 0; i < 50; i++) pr::add_frame(m, frame(50, 10, 35, 3, 2), pr::Waits{}, 16.7, 50, 50);
        int64_t hitches = 0;
        for (int64_t h : m.hitch) hitches += h;
        CHECK("held at 20 fps under a 60 cap: no hitch", hitches == 0);
        pr::add_frame(m, frame(120, 10, 100, 3, 2), pr::Waits{}, 16.7, 50, 50);
        for (int64_t h : m.hitch) hitches += h;
        CHECK("a 120 ms frame among them: one", hitches == 1);
    }

    // what it waited on: the biggest of the renderer's waits and the parts, "other" under a quarter of the frame
    {
        pr::Waits w;
        w.pipeline = 30;
        CHECK("pipelines", pr::cause_of(frame(50, 10, 35, 2, 3), w) == pr::kPipelineWait);
        CHECK("the GPU", pr::cause_of(frame(50, 5, 5, 38, 2), pr::Waits{}) == pr::kGpuBound);
        CHECK("the game", pr::cause_of(frame(50, 40, 5, 3, 2), pr::Waits{}) == pr::kGameCpu);
        w = {};
        w.upload = 20;
        CHECK("uploads", pr::cause_of(frame(60, 10, 25, 10, 5), w) == pr::kTextureUpload);
        CHECK("nothing stands out", pr::cause_of(frame(100, 20, 20, 20, 20), pr::Waits{}) == pr::kOther);
        w = {};
        w.readback = 30;   // (v0.4.3: the read-backs' waits are counted now, the game's CPU reads of GPU results)
        CHECK("read-backs", pr::cause_of(frame(70, 30, 5, 3, 2), w) == pr::kReadback);
        w = {};
        w.driver_compile = 45;   // (the driver's vkCreate*Pipelines, the GPL libraries and their links)
        CHECK("driver compiles", pr::cause_of(frame(60, 5, 10, 3, 2), w) == pr::kDriverCompile);
        w = {};
        w.descriptors = 28;   // (descriptor building and binding on the drawing threads)
        CHECK("descriptors", pr::cause_of(frame(60, 10, 20, 2, 2), w) == pr::kDescriptors);
        w = {};
        w.present = 35;   // (the swapchain: waiting for its image and the present call)
        CHECK("the swapchain", pr::cause_of(frame(60, 10, 12, 3, 2), w) == pr::kPresentBlock);
        w.os_wait = 40;   // (the presenting thread off the CPU, a gap with no work: the game's part beyond its CPU time)
        CHECK("the OS delayed it", pr::cause_of(frame(60, 45, 5, 3, 2), w) == pr::kOsWait);
        CHECK("a frame of the game's own CPU work", pr::cause_of(frame(60, 45, 5, 3, 2), pr::Waits{}) == pr::kGameCpu);
        CHECK("names", !std::strcmp(pr::cause_name(pr::kGpuBound), "gpu_bound") && !std::strcmp(pr::cause_name(pr::kPacing), "pace") &&
                           !std::strcmp(pr::cause_name(pr::kDriverCompile), "driver_compile") &&
                           !std::strcmp(pr::cause_name(pr::kPresentBlock), "present") &&
                           !std::strcmp(pr::cause_name(pr::kOsWait), "os_wait") &&
                           !std::strcmp(pr::event_name(pr::kDeviceLost), "device_lost"));
    }

    // pacing: a quarter of the target from the frame before; never the first frame
    CHECK("even", !pr::is_pacing_miss(16.9, 16.5, 16.7));
    CHECK("uneven", pr::is_pacing_miss(22, 16.5, 16.7));
    CHECK("first", !pr::is_pacing_miss(22, 0, 16.7));

    // a map's totals, and the summary as the relay takes it
    {
        std::map<std::string, pr::MapTotals> maps;
        auto& m = maps["Kokiri Forest (child)"];
        double last = 0;
        for (int i = 0; i < 100; i++) pr::add_frame(m, frame(16.6f, 10, 4, 2, 0.6f), pr::Waits{}, 16.7, last), last = 16.6;
        pr::Waits w;
        w.pipeline = 40;
        pr::add_frame(m, frame(60, 10, 45, 3, 2), w, 16.7, last);
        CHECK("frames", m.frame[4] == 100 && m.frame[9] == 1);
        CHECK("seconds", m.seconds > 1.71 && m.seconds < 1.73);
        CHECK("the hitch", m.hitch[pr::kPipelineWait] == 1 && m.pacing_misses == 0);
        CHECK("the parts' sums over the frames",
              m.part[fs::kGame] == 1010 && m.part[fs::kRender] == 445 && m.part[fs::kGpuWait] == 203 &&
              std::abs(m.part[fs::kPace] - 62) < 0.001);
        CHECK("the hitch's detail", m.hitch_detail[pr::kPipelineWait].count == 1 &&
                                     m.hitch_detail[pr::kPipelineWait].ms_sum == 60 && m.hitch_detail[pr::kPipelineWait].ms_max == 60 &&
                                     m.hitch_detail[pr::kPipelineWait].load_count == 0);
        CHECK("an empty map is left out", !maps["Hyrule Field (adult)"].any());
        pr::Machine mc;
        mc.version = "2.4.1 (source 1234abcd)", mc.os = "Arch Linux", mc.kernel = "6.12.3-arch1-1", mc.gpu = "AMD \"Radeon\" RX 7800 XT";
        mc.driver = "radv Mesa 25.2.4", mc.cpu = "AMD Ryzen 7 7800X3D", mc.upscaler = "FSR 3.1", mc.preset = "High";
        mc.vram_gb = 16, mc.ram_gb = 32, mc.fps_cap = 60;
        mc.framegen = pr::framegen_name(pr::kFramegenFsr3), mc.framegen_x = 2, mc.dlss_model = pr::dlss_model_name(11, true), mc.render_scale = 67;
        pr::Memory mem{5.25, 15.5, 7.75};
        const std::string j = pr::summary_json(mc, true, 312.5, "Kokiri Forest (child)", &mem, maps);
        CHECK("version 1", j.rfind("{\"v\":1,", 0) == 0);
        CHECK("quotes escaped", j.find("\"AMD \\\"Radeon\\\" RX 7800 XT\"") != std::string::npos);
        CHECK("the map", j.find("\"Kokiri Forest (child)\":{\"new\":true,") != std::string::npos);
        CHECK("not the empty one", j.find("Hyrule Field") == std::string::npos);
        CHECK("buckets", j.find("\"frame_ms\":[0,0,0,0,100,0,0,0,0,1,0,0,0]") != std::string::npos);
        CHECK("hitches", j.find("\"hitches\":{\"pipeline_wait\":1,") != std::string::npos);
        // (the parts are the average ms a frame over the summary's frames on the map: 1010/101, 445/101, 203/101, 62/101)
        CHECK("parts as averages", j.find("\"parts_ms\":{\"game\":10,\"render\":4.406,\"gpu_wait\":2.01,\"pace\":0.614}") != std::string::npos);
        CHECK("hitch detail", j.find("\"hitch_detail\":{\"pipeline_wait\":{\"count\":1,\"ms_sum\":60,\"ms_max\":60,"
                                      "\"load_count\":0,\"load_ms_sum\":0,\"load_ms_max\":0}}") != std::string::npos);
        CHECK("memory", j.find("\"memory\":{\"vram_used_gb\":5.25,\"vram_allowed_gb\":15.5,\"ram_used_gb\":7.75}") != std::string::npos);
        CHECK("numbers", j.find("\"session_s\":312.5,") != std::string::npos && j.find("\"fps_cap\":60,") != std::string::npos);
        CHECK("frame generation, DLSS model and render scale, after the cap", j.find("\"fps_cap\":60,\"framegen\":\"fsr3\",\"framegen_x\":2,\"framegen_auto\":false,\"dlss_model\":\"K\",\"render_scale\":67,\"map\":") != std::string::npos);
        CHECK("closed", j.back() == '}' && std::count(j.begin(), j.end(), '{') == std::count(j.begin(), j.end(), '}'));
    }

    // frame generation, the DLSS model and the render scale: the names the server takes, and what a PC without them says
    {
        CHECK("framegen names", !std::strcmp(pr::framegen_name(pr::kFramegenOff), "off") && !std::strcmp(pr::framegen_name(pr::kFramegenBuiltin), "builtin") &&
                                    !std::strcmp(pr::framegen_name(pr::kFramegenFsr3), "fsr3") && !std::strcmp(pr::framegen_name(pr::kFramegenDlssg), "dlssg") &&
                                    !std::strcmp(pr::framegen_name(pr::kFramegens), "other") &&
                                    !std::strcmp(pr::framegen_name(-1), "other"));
        CHECK("the presets by their letter", pr::dlss_model_name(11, true) == "K" && pr::dlss_model_name(13, true) == "M" &&
                                                 pr::dlss_model_name(12, true) == "L" && pr::dlss_model_name(5, true) == "E");
        CHECK("NVIDIA's own pick", pr::dlss_model_name(0, true) == "default");
        CHECK("no DLSS network: none, whatever the row says", pr::dlss_model_name(11, false) == "none" && pr::dlss_model_name(0, false) == "none");
        CHECK("a preset past the letters", pr::dlss_model_name(27, true) == "other" && pr::dlss_model_name(-1, true) == "other");
        std::map<std::string, pr::MapTotals> maps;
        pr::add_frame(maps["Title and intro"], frame(16.6f, 0, 0, 0, 0), pr::Waits{}, 16.7, 0);
        pr::Machine off;   // (a machine nothing is read for: frame generation off, no DLSS, Auto resolution)
        const std::string j = pr::summary_json(off, false, 1, "Title and intro", nullptr, maps);
        CHECK("the defaults", j.find("\"framegen\":\"off\",\"framegen_x\":1,\"framegen_auto\":false,\"dlss_model\":\"none\",\"render_scale\":0,") != std::string::npos);
        pr::Machine autom;
        autom.framegen = pr::framegen_name(pr::kFramegenBuiltin), autom.framegen_x = 3, autom.framegen_auto = true, autom.render_scale = 100;
        const std::string a = pr::summary_json(autom, false, 1, "Title and intro", nullptr, maps);
        CHECK("Auto: its limit, marked", a.find("\"framegen\":\"builtin\",\"framegen_x\":3,\"framegen_auto\":true,") != std::string::npos &&
                                             a.find("\"render_scale\":100,") != std::string::npos);
        autom.framegen = std::string(100, 'f'), autom.dlss_model = std::string(100, 'd');
        const std::string k = pr::summary_json(autom, false, 1, "Title and intro", nullptr, maps);
        CHECK("framegen and model cut at 40", k.find("\"framegen\":\"" + std::string(40, 'f') + "\",") != std::string::npos &&
                                                  k.find("\"dlss_model\":\"" + std::string(40, 'd') + "\",") != std::string::npos);
    }

    // a hitch while the game loads a map: the cause's row keeps it apart, whatever the cause was
    {
        std::map<std::string, pr::MapTotals> maps;
        pr::Waits w;
        w.os_wait = 70;   // (a load's page faults: the presenting thread off the CPU)
        pr::MapTotals& m = maps["Hyrule Field (child)"];
        pr::add_frame(m, frame(90, 80, 5, 3, 2), w, 16.7, 16.6, 16.6, true);
        pr::add_frame(m, frame(30, 25, 2, 2, 1), pr::Waits{}, 16.7, 16.6, 16.6, false);
        CHECK("the load's hitch", m.hitch[pr::kOsWait] == 1 && m.hitch_detail[pr::kOsWait].count == 1 &&
                                   m.hitch_detail[pr::kOsWait].load_count == 1 &&
                                   m.hitch_detail[pr::kOsWait].load_ms_sum == 90 && m.hitch_detail[pr::kOsWait].load_ms_max == 90);
        CHECK("the steady frame beside it", m.hitch_detail[pr::kOsWait].ms_sum == 90 && m.frame[7] == 1 && m.pacing_misses == 1);
        CHECK("a cause without hitches has no detail row",
              m.hitch_detail[pr::kPipelineWait].count == 0 && m.hitch_detail[pr::kGameCpu].load_count == 0);
        m.fresh = false;   // (a map seen before: the server's first_visit is this)
        pr::Machine mc;
        mc.version = "v0.4.3";
        const std::string j = pr::summary_json(mc, false, 60, "Hyrule Field (child)", nullptr, maps);
        CHECK("the load's row", j.find("\"hitch_detail\":{\"os_wait\":{\"count\":1,\"ms_sum\":90,\"ms_max\":90,"
                                       "\"load_count\":1,\"load_ms_sum\":90,\"load_ms_max\":90}}") != std::string::npos);
        CHECK("first visit from the map's new", j.find("\"new\":false,") != std::string::npos);
    }

    // the draws the replay left out are counted now (a program or pipeline not ready: skipped, not waited for)
    {
        pr::MapTotals m;
        fs::Frame f = frame(16.6f, 1, 2, 3, 4);
        f.left_out = 7;
        pr::add_frame(m, f, pr::Waits{}, 16.7, 0);
        CHECK("draws left out", m.left_out == 7);
    }

    // the kernel out of the Bug report's system line
    CHECK("linux", pr::kernel_of("Arch Linux (Linux 6.12.3-arch1-1), KDE (wayland)") == "6.12.3-arch1-1");
    CHECK("windows", pr::kernel_of("Windows 11 (build 26100)") == "10.0.26100");
    CHECK("unknown", pr::kernel_of("Linux").empty());

    // the release flag: a release's version built from the release's own sources (the stamp's source=)
    CHECK("release", pr::release_flag("v0.4.0 (source 1a2b3c4d)", "version=v0.4.0\nsource=1a2b3c4d\n"));
    CHECK("rc", pr::release_flag("v0.4.0-rc.2 (source 1a2b3c4d)", "source=1a2b3c4d\r\n"));
    CHECK("nightly", pr::release_flag("nightly-123-0123abcd (source ff)", "source=ff\n"));
    CHECK("another build of the folder", !pr::release_flag("v0.4.0 (source 1a2b3c4d)", "source=deadbeef\n"));
    CHECK("no stamp", !pr::release_flag("v0.4.0 (source 1a2b3c4d)", ""));
    CHECK("not a release's name", !pr::release_flag("movie-sync (source 1a2b3c4d)", "source=1a2b3c4d\n"));
    CHECK("no source", !pr::release_flag("v0.4.0", "source=1a2b3c4d\n"));

    // the payload keeps Longshot's field tables, which the server validates: every key in them, the new causes and the
    // hitch detail with them, the unmeasured ones 0
    {
        std::map<std::string, pr::MapTotals> maps;
        auto& m = maps["Title and intro"];
        pr::add_frame(m, frame(16.6f, 0, 0, 0, 0), pr::Waits{}, 16.7, 0);
        pr::Machine mc;
        const std::string j = pr::summary_json(mc, false, 1, "Title and intro", nullptr, maps);
        for (const char* key : {"\"v\":1", "\"version\"", "\"release\"", "\"first\"", "\"session_s\"", "\"os\"", "\"kernel\"", "\"renderer\"",
                                "\"gpu\"", "\"driver\"", "\"vram_gb\"", "\"cpu\"", "\"ram_gb\"", "\"upscaler\"", "\"preset\"", "\"fps_cap\"",
                                "\"framegen\"", "\"framegen_x\"", "\"framegen_auto\"", "\"dlss_model\"", "\"render_scale\"",
                                "\"map\"", "\"maps\"", "\"new\"", "\"seconds\"", "\"frame_ms\"", "\"frame_ms_sum\"", "\"gpu_ms\"", "\"gpu_ms_sum\"",
                                "\"parts_ms\"", "\"hitches\"", "\"hitch_detail\"", "\"pacing_misses\"", "\"draws_left_out\"", "\"events\"",
                                "\"movie\"", "\"pipeline_wait\"", "\"shader_translate\"", "\"texture_upload\"", "\"memsync\"",
                                "\"gpu_bound\"", "\"readback\"", "\"game_cpu\"", "\"pace\"", "\"driver_compile\"", "\"descriptors\"",
                                "\"present\"", "\"os_wait\"", "\"other\"", "\"device_lost\"", "\"dlss_recreated\"",
                                "\"movie_upscaler_dropped\"", "\"program_failed\"", "\"swapchain_recreated\"", "\"vram_over_budget\"",
                                "\"shown\"", "\"dropped\"", "\"late\"", "\"decode_ms\"", "\"fallbacks\""})
            CHECK(key, j.find(key) != std::string::npos);
        // (a map with no hitches writes an empty hitch_detail: the server reads its rows as they come)
        CHECK("no hitches, no detail", j.find("\"hitch_detail\":{},") != std::string::npos);
        // (the server's limits: a field's text is cut where its table says)
        mc.gpu = std::string(300, 'g'), mc.cpu = std::string(300, 'c'), mc.os = std::string(100, 'o'), mc.version = std::string(100, 'v');
        const std::string k = pr::summary_json(mc, false, 1, std::string(200, 'm'), nullptr, maps);
        CHECK("gpu cut at 120", k.find("\"gpu\":\"" + std::string(120, 'g') + "\"") != std::string::npos);
        CHECK("cpu cut at 120", k.find("\"cpu\":\"" + std::string(120, 'c') + "\"") != std::string::npos);
        CHECK("os cut at 60", k.find("\"os\":\"" + std::string(60, 'o') + "\"") != std::string::npos);
        CHECK("version cut at 40", k.find("\"version\":\"" + std::string(40, 'v') + "\"") != std::string::npos);
        CHECK("map cut at 120", k.find("\"map\":\"" + std::string(120, 'm') + "\"") != std::string::npos);
        CHECK("sixteen maps most", pr::kMaxMaps == 16);
    }

    // v0.4.4: the readback and descriptor hitches say what they waited on (hitch_detail's "which"), at most 8 labels a cause
    {
        pr::MapTotals m;
        pr::Waits w;
        w.readback = 45, w.readback_which = "strict:1040:meter pool 1000";
        pr::add_frame(m, frame(60, 5, 5, 40, 2), w, 16.7, 16.6);
        pr::add_frame(m, frame(70, 5, 5, 40, 2), w, 16.7, 16.6);
        w.readback_which = "rec:2000@80364080";
        pr::add_frame(m, frame(65, 5, 5, 40, 2), w, 16.7, 16.6);
        CHECK("readback hitches counted", m.hitch[pr::kReadback] == 3 && m.hitch_detail[pr::kReadback].which.size() == 2);
        CHECK("by label", m.hitch_detail[pr::kReadback].which.at("strict:1040:meter pool 1000") == 2 &&
                          m.hitch_detail[pr::kReadback].which.at("rec:2000@80364080") == 1);
        pr::Waits d;
        d.descriptors = 30, d.descriptors_which = pr::descriptor_kind(5, 25);
        pr::add_frame(m, frame(60, 5, 5, 1, 2), d, 16.7, 16.6);
        CHECK("descriptor hitch by kind", m.hitch[pr::kDescriptors] == 1 && m.hitch_detail[pr::kDescriptors].which.at("pool") == 1);
        CHECK("kinds", pr::descriptor_kind(25, 5) == "build" && pr::descriptor_kind(5, 25) == "pool");
        pr::Waits o;   // (a hitch of another cause carries no label even when one was given)
        o.pipeline = 30, o.readback_which = "gpu:40";
        pr::add_frame(m, frame(60, 5, 35, 1, 2), o, 16.7, 16.6);
        CHECK("another cause: no label", m.hitch_detail[pr::kPipelineWait].which.empty());
        std::map<std::string, pr::MapTotals> maps;
        maps["Hyrule Field (adult)"] = m;
        const std::string j = pr::summary_json(pr::Machine{}, false, 1, "Hyrule Field (adult)", nullptr, maps);
        CHECK("the readback row's which", j.find("\"readback\":{\"count\":3,") != std::string::npos &&
                                          j.find("\"which\":{\"rec:2000@80364080\":1,\"strict:1040:meter pool 1000\":2}}") != std::string::npos);
        CHECK("the descriptors row's which", j.find("\"which\":{\"pool\":1}}") != std::string::npos);
        CHECK("none for pipeline_wait", j.find("\"pipeline_wait\":{\"count\":1,\"ms_sum\":60,\"ms_max\":60,\"load_count\":0,\"load_ms_sum\":0,\"load_ms_max\":0}") != std::string::npos);
        CHECK("closed", std::count(j.begin(), j.end(), '{') == std::count(j.begin(), j.end(), '}'));
        pr::HitchDetail many;   // (nine labels: eight kept, the ninth under "other")
        for (int i = 0; i < 9; i++) pr::note_which(many, "l" + std::to_string(i));
        pr::note_which(many, "l0");
        pr::note_which(many, "");
        CHECK("eight labels and other", many.which.size() == 9 && many.which.at("other") == 1 && many.which.at("l0") == 2);
        pr::note_which(many, "l9");
        CHECK("other keeps counting", many.which.size() == 9 && many.which.at("other") == 2);
    }

    // v0.4.4: the os_wait hitches say which wait they were (hitch_detail's "which" and "which_ms"), from the host's waits by kind
    {
        pr::Waits w;
        w.os_wait = 100, w.ow_known = true;
        CHECK("no waits pushed: no label", [&] { pr::Waits n; n.os_wait = 100; return pr::os_wait_kind(n).empty(); }());
        w.ow_lock = 80, w.ow_sleep = 5;
        CHECK("lock", pr::os_wait_kind(w) == "lock");
        w.ow_lock = 10, w.ow_sleep = 70;
        CHECK("sleep", pr::os_wait_kind(w) == "sleep");
        w.ow_sleep = 0, w.ow_vcore = 55;
        CHECK("vcore", pr::os_wait_kind(w) == "vcore");
        w.ow_vcore = 0, w.ow_file = 60;
        CHECK("file", pr::os_wait_kind(w) == "file");
        w.ow_file = 20, w.ow_lock = 20;   // (none makes up half: the rest is the OS's)
        CHECK("nothing says why: off the CPU", pr::os_wait_kind(w) == "off_cpu");
        w.faults = pr::kFaultsMin;
        CHECK("page faults", pr::os_wait_kind(w) == "page_fault");
        w.ws_drop_mb = pr::kTrimMb;
        CHECK("a working-set trim first", pr::os_wait_kind(w) == "ws_trim");
        w.ow_file = 70;   // (a wait that explains it beats the faults)
        CHECK("an explained wait first", pr::os_wait_kind(w) == "file");

        pr::MapTotals m;
        pr::Waits a;
        a.os_wait = 90, a.ow_known = true, a.ow_lock = 80;
        pr::add_frame(m, frame(100, 100, 0, 0, 0), a, 16.7, 16.6);
        pr::add_frame(m, frame(120, 120, 0, 0, 0), a, 16.7, 16.6);
        pr::Waits b;
        b.os_wait = 90, b.ow_known = true;
        pr::add_frame(m, frame(80, 80, 0, 0, 0), b, 16.7, 16.6);
        const auto& d = m.hitch_detail[pr::kOsWait];
        CHECK("os_wait hitches by wait", m.hitch[pr::kOsWait] == 3 && d.which.at("lock") == 2 && d.which.at("off_cpu") == 1);
        CHECK("and their ms", d.which_ms.at("lock") == 220 && d.which_ms.at("off_cpu") == 80);
        std::map<std::string, pr::MapTotals> maps;
        maps["Market"] = m;
        const std::string j = pr::summary_json(pr::Machine{}, false, 1, "Market", nullptr, maps);
        CHECK("json", j.find("\"which\":{\"lock\":2,\"off_cpu\":1},\"which_ms\":{\"lock\":220,\"off_cpu\":80}}") != std::string::npos);
        CHECK("closed", std::count(j.begin(), j.end(), '{') == std::count(j.begin(), j.end(), '}'));
    }

    // v0.4.4: the memory record's new fields, at the end of "memory"; the three before them as they were
    {
        pr::Memory mem;
        mem.vram_used_gb = 5.25, mem.vram_allowed_gb = 15.5, mem.ram_used_gb = 7.75;
        std::map<std::string, pr::MapTotals> none;
        const std::string old = pr::summary_json(pr::Machine{}, false, 1, "Market", &mem, none);
        CHECK("as before without extra", old.find("\"memory\":{\"vram_used_gb\":5.25,\"vram_allowed_gb\":15.5,\"ram_used_gb\":7.75},") != std::string::npos);
        mem.extra = true, mem.ram_class = pr::ram_class_of(15.7), mem.free_min_gb = 1.25, mem.commit_pct_max = 83.5, mem.ws_trims = 2,
        mem.page_faults = 123456;
        std::string j = pr::summary_json(pr::Machine{}, false, 1, "Market", &mem, none);
        CHECK("extra", j.find("\"ram_used_gb\":7.75,\"ram_class\":\"16\",\"ram_free_min_gb\":1.25,\"commit_pct_max\":83.5,"
                              "\"ws_trims\":2,\"page_faults\":123456},\"maps\"") != std::string::npos);
        mem.cold_line = true, mem.cold_held_ds = 125, mem.cold_passes = 40;
        j = pr::summary_json(pr::Machine{}, false, 1, "Market", &mem, none);
        CHECK("cold hold", j.find("\"page_faults\":123456,\"cold_held_s\":12.5,\"cold_passes\":40}") != std::string::npos);
        mem.free_min_gb = -1, mem.commit_pct_max = -1;   // (not read: left out, not 0)
        j = pr::summary_json(pr::Machine{}, false, 1, "Market", &mem, none);
        CHECK("unread left out", j.find("ram_free_min_gb") == std::string::npos && j.find("commit_pct_max") == std::string::npos);
        CHECK("classes", !std::strcmp(pr::ram_class_of(0), "unknown") && !std::strcmp(pr::ram_class_of(4), "<=4") &&
                         !std::strcmp(pr::ram_class_of(7.8), "8") && !std::strcmp(pr::ram_class_of(15.4), "16") &&
                         !std::strcmp(pr::ram_class_of(31.9), "32") && !std::strcmp(pr::ram_class_of(63.8), "64+"));
        CHECK("closed", std::count(j.begin(), j.end(), '{') == std::count(j.begin(), j.end(), '}'));
    }

    // v0.4.4: the load hitches. The loading flag is rarely seen, so a map change seen later marks the hitches and stalls just before it
    {
        pr::MapTotals m;
        std::vector<pr::Recent> recent;
        pr::Waits w;
        w.os_wait = 150;
        pr::add_frame(m, frame(160, 160, 0, 0, 0), w, 16.7, 16.6, 0, false, &recent, 100000);
        pr::add_frame(m, frame(90, 90, 0, 0, 0), w, 16.7, 16.6, 0, true, &recent, 101000);   // (the flag was seen on this one)
        pr::add_stall(m, 3500, false, &recent, 102000);
        pr::add_stall(m, 1500, false, &recent, 102100);   // (under 2 s: a frame's business, not a stall)
        pr::add_stall(m, 90000, false, &recent, 102200);   // (over a minute: the window was away, not a stall)
        CHECK("stall counted", m.stall_count == 1 && m.stall_ms_sum == 3500 && m.stall_ms_max == 3500 && m.stall_load_count == 0);
        CHECK("flagged frame counted at once", m.hitch_detail[pr::kOsWait].load_count == 1 && m.hitch_detail[pr::kOsWait].load_ms_sum == 90);
        const int n = pr::reclassify_as_load(m, recent, 103500);   // (the change seen 1.5 s after the stall)
        CHECK("two marked", n == 2);
        CHECK("the hitch's load columns", m.hitch_detail[pr::kOsWait].load_count == 2 && m.hitch_detail[pr::kOsWait].load_ms_sum == 250 &&
                                          m.hitch_detail[pr::kOsWait].load_ms_max == 160);
        CHECK("the stall's", m.stall_load_count == 1 && recent.empty());
        CHECK("nothing twice", pr::reclassify_as_load(m, recent, 103600) == 0);
        // a hitch from long before the change is not the load's
        pr::add_frame(m, frame(100, 100, 0, 0, 0), w, 16.7, 16.6, 0, false, &recent, 200000);
        CHECK("a window of five seconds", pr::reclassify_as_load(m, recent, 206000) == 0 && m.hitch_detail[pr::kOsWait].load_count == 2);
        // the list is bounded
        std::vector<pr::Recent> many;
        for (int i = 0; i < 500; i++) pr::add_frame(m, frame(100, 100, 0, 0, 0), w, 16.7, 16.6, 0, false, &many, 300000 + i * 5);
        CHECK("bounded", many.size() <= pr::kRecentMax);
        std::map<std::string, pr::MapTotals> maps;
        pr::MapTotals only;
        pr::add_stall(only, 4200, true);
        CHECK("a map with only a stall is sent", only.any());
        maps["Kokiri Forest (child)"] = only;
        const std::string j = pr::summary_json(pr::Machine{}, false, 1, "Kokiri Forest (child)", nullptr, maps);
        CHECK("stalls in the json", j.find("\"movie\":{\"shown\":0,\"dropped\":0,\"late\":0,\"decode_ms\":0,\"fallbacks\":0},"
                                          "\"stalls\":{\"count\":1,\"ms_sum\":4200,\"ms_max\":4200,\"load_count\":1}}}") != std::string::npos);
    }

    // what a frame costs (a player's): well under a microsecond, against a 16.7 ms frame
    {
        pr::MapTotals m;
        const auto f = frame(16.6f, 10, 4, 2, 0.6f);
        const auto t0 = std::chrono::steady_clock::now();
        const int n = 1000000;
        for (int i = 0; i < n; i++) pr::add_frame(m, f, pr::Waits{}, 16.7, 16.5);
        const double ns = std::chrono::duration<double, std::nano>(std::chrono::steady_clock::now() - t0).count() / n;
        std::printf("add_frame: %.1f ns a frame\n", ns);
        CHECK("cheap", ns < 1000 && m.frame[4] == n);
    }
    return 0;
}
