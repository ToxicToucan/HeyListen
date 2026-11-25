// Copyright (C) 2026 Sacrappy

// AMD FSR 4 frame generation (vk_fsr4fg.h). The host side follows AMD's FidelityFX SDK 2.4 documents (Kits/FidelityFX/docs/techniques/
// frame-interpolation-api.md and frame-interpolation-ml.md) and headers (third_party/fidelityfx/framegeneration/include/
// ffx_framegeneration.h, version 4.0.2) as they are written:
//   capability  ffxQuery(NULL, ffxQueryDescGetVersions for FFX_API_CREATE_CONTEXT_DESC_TYPE_FRAMEGENERATION, the VkDeviceContext): AMD's
//               runtime lists the frame generation versions it has for this device; none: not offered
//   context     ffxCreateContext(ffxCreateContextDescFrameGeneration -> ffxCreateContextDescFrameGenerationVersion (FFX_FRAMEGENERATION_VERSION)
//               -> ffxCreateBackendVKDesc -> ffxOverrideVersion)
//   each frame  the mandatory order of frame-interpolation-ml.md "Call order": ffxConfigure(ffxConfigureDescFrameGeneration), ffxDispatch(
//               ffxDispatchDescFrameGenerationPrepareV2: depth, motion, camera), ffxDispatch(ffxDispatchDescFrameGeneration: the colour, one
//               output); one frame ID, rising by exactly 1 a frame (a gap is a reset, as the document says)
// No AMD swapchain: FFX_FRAMEGENERATION_FLAG_NO_SWAPCHAIN_CONTEXT_NOTIFY ("only run frame interpolation and not modify the swapchain"),
// no swapchain, no callbacks, no presentation by AMD; the renderer's display thread paces and presents as it does for every generator.
// NOT TRIED ON REAL HARDWARE (it needs a Radeon RX 9000; none of the developer's PCs has one). Every return code of AMD's is logged in
// words, and any failure leaves the set to the generators that made it before.
#include "vk_fsr4fg.h"

#include <atomic>
#include <cstdlib>

#include "vk_internal.h"
#include "vk_fsr4.h"

#if defined(HOOKSHOT_FSR) && defined(_WIN32)
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "vk_menu.h"
#include "vk_texture.h"   // (texture_frame_no)
#include "framegen_rows.h"
#include "api/include/ffx_api.h"
#include "api/include/ffx_api_types.h"
#include "api/include/vk/ffx_api_vk.h"
#include "framegeneration/include/ffx_framegeneration.h"
#endif
#include "vk_work.h"   // (after the others: the barriers here count as work for full_barrier)

namespace mkwvk {

namespace {
int env_switch(const char* name) {   // (-1: unset)
    const char* e = getenv(name);
    return e && *e ? atoi(e) : -1;
}
/// RX_VK_FSR4_FG: 0 never, 1 over the row (read once)
int env_fg() {
    static const int e = env_switch("RX_VK_FSR4_FG");
    return e;
}
}  // namespace

const char* fsr4fg_why_text(Fsr4FgWhy why) {
    switch (why) {
    case FSR4FG_OK: return "";
    case FSR4FG_NOT_BUILT: return "Not in this build (it is built with AMD's FSR headers).";
    case FSR4FG_ENV_OFF: return "Turned off for this run (RX_VK_FSR4_FG=0).";
    case FSR4FG_NOT_WINDOWS: return "AMD publishes FSR 4 frame generation for Windows only.";
    case FSR4FG_NO_LOADER: return "AMD's runtime is not there (amd_fidelityfx_loader_vk.dll and amd_fidelityfx_framegeneration_vk.dll, from AMD's FidelityFX SDK 2.4).";
    case FSR4FG_NO_PROVIDER: return "AMD's runtime has no frame generation for this GPU (it needs a Radeon RX 9000 on Windows 11).";
    case FSR4FG_FAILED: return "AMD's runtime refused three frames in a row; it is off for this run.";
    default: return "Not asked yet.";
    }
}

#if defined(HOOKSHOT_FSR) && defined(_WIN32)

namespace {

constexpr const char* kLoader = "amd_fidelityfx_loader_vk.dll";
constexpr int kFailuresOff = 3;   // (frames in a row AMD's runtime refuses before it is let go for the run)

/// AMD's return code in words (ffx_api.h ffxReturnCode_t)
const char* words(ffxReturnCode_t rc) {
    switch (rc) {
    case FFX_API_RETURN_OK: return "ok";
    case FFX_API_RETURN_ERROR: return "an error that AMD does not say more about";
    case FFX_API_RETURN_ERROR_UNKNOWN_DESCTYPE: return "a structure it does not know for this call (a programming error, or an older runtime)";
    case FFX_API_RETURN_ERROR_RUNTIME_ERROR: return "Vulkan or the effect itself returned an error";
    case FFX_API_RETURN_NO_PROVIDER: return "no provider for this structure (the framegeneration DLL is not beside the loader, or not for this GPU)";
    case FFX_API_RETURN_ERROR_MEMORY: return "a memory allocation failed";
    case FFX_API_RETURN_ERROR_PARAMETER: return "a parameter was invalid (a null pointer, an empty resource or a value out of range)";
    case FFX_API_RETURN_PROVIDER_NO_SUPPORT_NEW_DESCTYPE: return "the structure is newer than the runtime knows (update AMD's DLLs)";
    default: return "a code this version of the game does not know";
    }
}

/// A context and the descriptions it was made from: it keeps pointers into them until it is destroyed
struct Context {
    ffxCreateBackendVKDesc backend{};
    ffxOverrideVersion version{};
    ffxCreateContextDescFrameGenerationVersion api_version{};
    ffxCreateContextDescFrameGeneration desc{};
    ffxContext context = nullptr;
    uint32_t w = 0, h = 0, rw = 0, rh = 0;   // (the picture, and the largest render size the inputs have)
    uint64_t memory = 0;
    PfnFfxDestroyContext destroy = nullptr;
    ~Context() {
        if (context && destroy) destroy(&context, nullptr);
    }
};

struct State {
    std::mutex mu;
    // (what AMD's runtime said: -1 not asked, 0 not offered, 1 offered)
    std::atomic<int> cap{-1};
    std::atomic<int> why{FSR4FG_UNKNOWN};
    HMODULE lib = nullptr;
    PfnFfxCreateContext create = nullptr;
    PfnFfxDestroyContext destroy = nullptr;
    PfnFfxQuery query = nullptr;
    PfnFfxDispatch dispatch = nullptr;
    PfnFfxConfigure configure = nullptr;
    VkDeviceContext device_context{};
    uint64_t version_id = 0;
    std::string version_name;
    std::shared_ptr<Context> context;
    std::vector<std::pair<uint64_t, std::shared_ptr<Context>>> retired;   // (a context let go, destroyed a few frames on: its recordings may still be in flight)
    int failures = 0;
    bool failed = false;
    uint64_t asked = 0, made = 0, no_inputs = 0, refused = 0, resets = 0, last_sequence = 0;
};
State& st() {
    static State& s = *new State;   // (never destroyed: Hookshot leaves through ExitProcess with the device alive, and a destructor would call into AMD's DLL after its detach)
    return s;
}

/// The loader and the frame generation versions it has for this device: caller holds mu, once
void ask_locked(State& s) {
    const auto no = [&s](Fsr4FgWhy why) {
        s.why = why;
        s.cap = 0;
    };
    if (env_fg() == 0) return no(FSR4FG_ENV_OFF);
    std::string where;
    for (const std::string& dir : fsr_library_dirs()) {
        where += (where.empty() ? "" : ", ") + dir;
        const std::string path = dir + "\\" + kLoader;
        if (GetFileAttributesA(path.c_str()) == INVALID_FILE_ATTRIBUTES) continue;
        // (as vk_fsr4.cpp opens it: the DLLs the loader picks lie beside it, its folder first in the search; the same module when the upscaler has it open)
        s.lib = LoadLibraryExA(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        if (!s.lib) logf("vk: AMD FSR 4 FG: %s not loaded (Windows error %lu)", path.c_str(), (unsigned long)GetLastError());
        else break;
    }
    if (!s.lib) {
        logf("vk: AMD FSR 4 FG: not offered: no %s in %s (AMD's runtime: the readme says where it comes from)", kLoader, where.c_str());
        return no(FSR4FG_NO_LOADER);
    }
    s.create = (PfnFfxCreateContext)GetProcAddress(s.lib, "ffxCreateContext");
    s.destroy = (PfnFfxDestroyContext)GetProcAddress(s.lib, "ffxDestroyContext");
    s.query = (PfnFfxQuery)GetProcAddress(s.lib, "ffxQuery");
    s.dispatch = (PfnFfxDispatch)GetProcAddress(s.lib, "ffxDispatch");
    s.configure = (PfnFfxConfigure)GetProcAddress(s.lib, "ffxConfigure");
    if (!s.create || !s.destroy || !s.query || !s.dispatch || !s.configure) {
        logf("vk: AMD FSR 4 FG: not offered: %s lacks the FSR API's entry points", kLoader);
        return no(FSR4FG_NO_LOADER);
    }
    s.device_context = VkDeviceContext{dev.device, dev.physical, vkGetDeviceProcAddr};
    // (AMD's own capability query: the frame generation versions the runtime has for this device, as the upscaler row asks for FSR 4)
    ffxQueryDescGetVersions versions{};
    versions.header.type = FFX_API_QUERY_DESC_TYPE_GET_VERSIONS;
    versions.createDescType = FFX_API_CREATE_CONTEXT_DESC_TYPE_FRAMEGENERATION;
    versions.device = &s.device_context;
    uint64_t count = 0;
    versions.outputCount = &count;
    ffxReturnCode_t rc = s.query(nullptr, &versions.header);
    if (rc != FFX_API_RETURN_OK || !count) {
        logf("vk: AMD FSR 4 FG: not offered: AMD's runtime lists no frame generation for this GPU (its answer to the count query: %u, %s; %llu listed)",
             (unsigned)rc, words(rc), (unsigned long long)count);
        return no(FSR4FG_NO_PROVIDER);
    }
    std::vector<uint64_t> ids(count);
    std::vector<const char*> names(count);
    versions.versionIds = ids.data();
    versions.versionNames = names.data();
    rc = s.query(nullptr, &versions.header);
    if (rc != FFX_API_RETURN_OK) {
        logf("vk: AMD FSR 4 FG: not offered: the frame generation versions could not be listed (%u, %s)", (unsigned)rc, words(rc));
        return no(FSR4FG_NO_PROVIDER);
    }
    std::string listed;
    unsigned best[3] = {0, 0, 0};
    for (uint64_t i = 0; i < count && i < ids.size(); i++) {
        const char* name = names[i] ? names[i] : "";
        listed += (listed.empty() ? "" : ", ") + std::string(name);
        unsigned v[3] = {0, 0, 0};
        sscanf(name, "%u.%u.%u", &v[0], &v[1], &v[2]);
        // (FSR 4 frame generation is the 4.x one: a 3.x provider never stands in for it silently)
        if (v[0] == 4 && (!s.version_id || v[1] > best[1] || (v[1] == best[1] && v[2] > best[2]))) {
            std::memcpy(best, v, sizeof best);
            s.version_id = ids[i];
            s.version_name = name;
        }
    }
    if (!s.version_id) {
        logf("vk: AMD FSR 4 FG: not offered: no FSR 4 frame generation (4.x) for this GPU (AMD's runtime has: %s)", listed.c_str());
        return no(FSR4FG_NO_PROVIDER);
    }
    logf("vk: AMD FSR 4 FG: offered: AMD's runtime lists frame generation %s for this GPU; using %u.%u.%u (NOT TRIED ON REAL HARDWARE: "
         "send this log back)", listed.c_str(), best[0], best[1], best[2]);
    s.why = FSR4FG_OK;
    s.cap = 1;
}

void probe_locked(State& s) {
    if (s.cap.load() >= 0 || !dev.device) return;
    ask_locked(s);
}

/// The inverse of a 4x4, in doubles (as vk_dlss.cpp's: a float one of a projection with a far plane in the thousands loses what the camera needs)
bool inverse(const float* m, float* out) {
    double a[16], inv[16];
    for (int i = 0; i < 16; i++) a[i] = m[i];
    inv[0] = a[5] * a[10] * a[15] - a[5] * a[11] * a[14] - a[9] * a[6] * a[15] + a[9] * a[7] * a[14] + a[13] * a[6] * a[11] - a[13] * a[7] * a[10];
    inv[4] = -a[4] * a[10] * a[15] + a[4] * a[11] * a[14] + a[8] * a[6] * a[15] - a[8] * a[7] * a[14] - a[12] * a[6] * a[11] + a[12] * a[7] * a[10];
    inv[8] = a[4] * a[9] * a[15] - a[4] * a[11] * a[13] - a[8] * a[5] * a[15] + a[8] * a[7] * a[13] + a[12] * a[5] * a[11] - a[12] * a[7] * a[9];
    inv[12] = -a[4] * a[9] * a[14] + a[4] * a[10] * a[13] + a[8] * a[5] * a[14] - a[8] * a[6] * a[13] - a[12] * a[5] * a[10] + a[12] * a[6] * a[9];
    inv[1] = -a[1] * a[10] * a[15] + a[1] * a[11] * a[14] + a[9] * a[2] * a[15] - a[9] * a[3] * a[14] - a[13] * a[2] * a[11] + a[13] * a[3] * a[10];
    inv[5] = a[0] * a[10] * a[15] - a[0] * a[11] * a[14] - a[8] * a[2] * a[15] + a[8] * a[3] * a[14] + a[12] * a[2] * a[11] - a[12] * a[3] * a[10];
    inv[9] = -a[0] * a[9] * a[15] + a[0] * a[11] * a[13] + a[8] * a[1] * a[15] - a[8] * a[3] * a[13] - a[12] * a[1] * a[11] + a[12] * a[3] * a[9];
    inv[13] = a[0] * a[9] * a[14] - a[0] * a[10] * a[13] - a[8] * a[1] * a[14] + a[8] * a[2] * a[13] + a[12] * a[1] * a[10] - a[12] * a[2] * a[9];
    inv[2] = a[1] * a[6] * a[15] - a[1] * a[7] * a[14] - a[5] * a[2] * a[15] + a[5] * a[3] * a[14] + a[13] * a[2] * a[7] - a[13] * a[3] * a[6];
    inv[6] = -a[0] * a[6] * a[15] + a[0] * a[7] * a[14] + a[4] * a[2] * a[15] - a[4] * a[3] * a[14] - a[12] * a[2] * a[7] + a[12] * a[3] * a[6];
    inv[10] = a[0] * a[5] * a[15] - a[0] * a[7] * a[13] - a[4] * a[1] * a[15] + a[4] * a[3] * a[13] + a[12] * a[1] * a[7] - a[12] * a[3] * a[5];
    inv[14] = -a[0] * a[5] * a[14] + a[0] * a[6] * a[13] + a[4] * a[1] * a[14] - a[4] * a[2] * a[13] - a[12] * a[1] * a[6] + a[12] * a[2] * a[5];
    inv[3] = -a[1] * a[6] * a[11] + a[1] * a[7] * a[10] + a[5] * a[2] * a[11] - a[5] * a[3] * a[10] - a[9] * a[2] * a[7] + a[9] * a[3] * a[6];
    inv[7] = a[0] * a[6] * a[11] - a[0] * a[7] * a[10] - a[4] * a[2] * a[11] + a[4] * a[3] * a[10] + a[8] * a[2] * a[7] - a[8] * a[3] * a[6];
    inv[11] = -a[0] * a[5] * a[11] + a[0] * a[7] * a[9] + a[4] * a[1] * a[11] - a[4] * a[3] * a[9] - a[8] * a[1] * a[7] + a[8] * a[3] * a[5];
    inv[15] = a[0] * a[5] * a[10] - a[0] * a[6] * a[9] - a[4] * a[1] * a[10] + a[4] * a[2] * a[9] + a[8] * a[1] * a[6] - a[8] * a[2] * a[5];
    const double det = a[0] * inv[0] + a[1] * inv[4] + a[2] * inv[8] + a[3] * inv[12];
    if (det > -1e-30 && det < 1e-30) return false;
    for (int i = 0; i < 16; i++) out[i] = (float)(inv[i] / det);
    return true;
}
void multiply(const float* a, const float* b, float* out) {   // (row major: out = a * b)
    for (int r = 0; r < 4; r++)
        for (int c = 0; c < 4; c++) {
            double sum = 0;
            for (int k = 0; k < 4; k++) sum += (double)a[r * 4 + k] * b[k * 4 + c];
            out[r * 4 + c] = (float)sum;
        }
}

VkImageMemoryBarrier2 barrier(VkImage image, VkAccessFlags2 src, VkAccessFlags2 dst, VkImageLayout from, VkImageLayout to) {
    VkImageMemoryBarrier2 b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2};
    b.srcStageMask = b.dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
    b.srcAccessMask = src, b.dstAccessMask = dst, b.oldLayout = from, b.newLayout = to;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = image, b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    return b;
}
void barriers(VkCommandBuffer cmd, const VkImageMemoryBarrier2* b, uint32_t n) {
    VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
    di.imageMemoryBarrierCount = n, di.pImageMemoryBarriers = b;
    vkCmdPipelineBarrier2(cmd, &di);
}

/// An image as AMD's API takes it (a Vulkan image, its description from its format, size and usage, and the state it is in)
FfxApiResource resource(VkImage image, VkFormat format, uint32_t w, uint32_t h, VkImageUsageFlags usage, uint32_t state) {
    VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ci.imageType = VK_IMAGE_TYPE_2D, ci.format = format, ci.extent = {w, h, 1};
    ci.mipLevels = 1, ci.arrayLayers = 1, ci.samples = VK_SAMPLE_COUNT_1_BIT, ci.tiling = VK_IMAGE_TILING_OPTIMAL;
    ci.usage = usage, ci.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    return ffxApiGetResourceVK((void*)image, ffxApiGetImageResourceDescriptionVK(image, ci, 0), state);
}

/// A context for pictures of w x h with inputs up to rw x rh: null when AMD's runtime does not make one (logged with its code in words)
std::shared_ptr<Context> make_context(State& s, uint32_t w, uint32_t h, uint32_t rw, uint32_t rh) {
    auto c = std::make_shared<Context>();
    c->destroy = s.destroy;
    c->backend.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_BACKEND_VK;
    c->backend.deviceContext = s.device_context;
    c->version.header.type = FFX_API_DESC_TYPE_OVERRIDE_VERSION;
    c->version.versionId = s.version_id;
    c->backend.header.pNext = &c->version.header;
    c->api_version.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_FRAMEGENERATION_VERSION;
    c->api_version.version = FFX_FRAMEGENERATION_VERSION;   // (as ffx_framegeneration.h asks: the API the game was built against)
    c->api_version.header.pNext = &c->backend.header;
    c->desc.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_FRAMEGENERATION;
    c->desc.header.pNext = &c->api_version.header;
    // (the made frame is the game's RGBA8 picture, SDR; the scene's depth is reversed (1 at the near plane); its motion is in render
    // pixels without the jitter, at the render size)
    c->desc.flags = FFX_FRAMEGENERATION_ENABLE_DEPTH_INVERTED;
    c->desc.displaySize = {w, h};
    c->desc.maxRenderSize = {rw, rh};
    c->desc.backBufferFormat = FFX_API_SURFACE_FORMAT_R8G8B8A8_UNORM;
    const ffxReturnCode_t rc = s.create(&c->context, &c->desc.header, nullptr);
    if (rc != FFX_API_RETURN_OK || !c->context) {
        c->context = nullptr;
        logf("vk: AMD FSR 4 FG: ffxCreateContext said %u (%s) for %ux%u from %ux%u", (unsigned)rc, words(rc), w, h, rw, rh);
        return nullptr;
    }
    c->w = w, c->h = h, c->rw = rw, c->rh = rh;
    FfxApiEffectMemoryUsage usage{};
    ffxQueryDescFrameGenerationGetGPUMemoryUsage mem{};
    mem.header.type = FFX_API_QUERY_DESC_TYPE_FRAMEGENERATION_GPU_MEMORY_USAGE;
    mem.gpuMemoryUsageFrameGeneration = &usage;
    if (s.query(&c->context, &mem.header) == FFX_API_RETURN_OK) c->memory = usage.totalUsageInBytes;
    ffxQueryGetProviderVersion provider{};
    provider.header.type = FFX_API_QUERY_DESC_TYPE_GET_PROVIDER_VERSION;
    const bool known = s.query(&c->context, &provider.header) == FFX_API_RETURN_OK && provider.versionName;
    logf("vk: AMD FSR 4 FG: context made, FSR frame generation %s, %ux%u from inputs up to %ux%u (its own %llu MB)",
         known ? provider.versionName : "?", w, h, rw, rh, (unsigned long long)(c->memory >> 20));
    return c;
}

/// Contexts let go a few frames ago are destroyed now (their recordings are done)
void free_retired(State& s) {
    const uint64_t now = texture_frame_no();
    for (size_t i = 0; i < s.retired.size();) {
        if (now - s.retired[i].first > 8) {
            s.retired[i] = std::move(s.retired.back());
            s.retired.pop_back();
        } else {
            i++;
        }
    }
}
void retire(State& s) {
    if (s.context) s.retired.emplace_back(texture_frame_no(), std::move(s.context));
    s.context.reset();
}

}  // namespace

Fsr4FgWhy fsr4fg_why() {
    State& s = st();
    if (s.failed) return FSR4FG_FAILED;
    return (Fsr4FgWhy)s.why.load();
}

bool fsr4fg_offered() {
    State& s = st();
    return s.cap.load() == 1 && !s.failed;
}

void fsr4fg_probe() {
    State& s = st();
    if (s.cap.load() >= 0 || !dev.device) return;
    std::lock_guard lk(s.mu);
    probe_locked(s);
}

bool fsr4fg_wanted() {
    State& s = st();
    if (env_fg() == 0 || s.failed) return false;
    const bool chosen = env_fg() == 1 || opts.framegen_by == framegen_rows::BY_FSR4;
    if (!chosen) return false;
    if (s.cap.load() < 0) fsr4fg_probe();   // (a saved choice with the menu never opened: asked at the first frame that wants it)
    return s.cap.load() == 1;
}

void fsr4fg_idle() {
    State& s = st();
    if (!s.context && s.retired.empty()) return;
    std::lock_guard lk(s.mu);
    free_retired(s);
    if (s.context && !fsr4fg_wanted()) {
        logf("vk: AMD FSR 4 FG: off (Frame generation by): its context let go");
        retire(s);
    }
}

int fsr4fg_frames(VkCommandBuffer cmd, VkImage color, VkImageView color_view, uint32_t w, uint32_t h, VkImage out, VkImageView out_view,
                  const DlssFgInputs& in, uint64_t sequence, bool native_pq) {
    (void)color_view;
    (void)out_view;
    State& s = st();
    if (!fsr4fg_wanted()) return 0;
    std::lock_guard lk(s.mu);
    if (s.failed || s.cap.load() != 1) return 0;
    free_retired(s);
    if (++s.asked % 600 == 0) {
        logf("vk: AMD FSR 4 FG: of the last 600 sets %llu made by AMD's frame generation (%llu told to reset), %llu without its inputs or with the "
             "game's HDR picture, %llu refused (the renderer's own or FSR 3.1 made the rest)", (unsigned long long)s.made,
             (unsigned long long)s.resets, (unsigned long long)s.no_inputs, (unsigned long long)s.refused);
        s.made = s.resets = s.no_inputs = s.refused = 0;
    }
    // (the game's own HDR picture is RGB10A2 PQ: AMD's context here is the SDR one. The renderer's own makes those frames)
    if (native_pq || !in.depth_view || !in.motion_view || !in.depth || !in.motion || !in.render_w || !in.render_h || !w || !h || !color || !out)
        return s.no_inputs++, 0;
    // the camera without the jitter, from the matrices the game filled (as vk_dlss.cpp dlss_fg_frames): view = P^-1 * VP, its inverse the
    // camera's frame; OpenGL's camera looks down -z
    float ip[16], view[16], cam[16];
    if (!inverse(in.projection, ip)) return s.no_inputs++, 0;
    multiply(ip, in.view_projection, view);
    if (!inverse(view, cam)) return s.no_inputs++, 0;
    // (a context for this size, and inputs up to the render size it was made for: a larger render size makes it again)
    if (s.context && (s.context->w != w || s.context->h != h || s.context->rw < in.render_w || s.context->rh < in.render_h)) {
        logf("vk: AMD FSR 4 FG: the size changed (%ux%u from %ux%u): its context made again", w, h, in.render_w, in.render_h);
        retire(s);
    }
    if (!s.context) {
        s.context = make_context(s, w, h, std::max(in.render_w, (uint32_t)1), std::max(in.render_h, (uint32_t)1));
        if (!s.context) {
            if (++s.failures >= kFailuresOff) {
                s.failed = true;
                logf("vk: AMD FSR 4 FG: its context was not made %d times: off for this run, the renderer's own (or FSR 3.1) makes the frames", kFailuresOff);
            }
            return s.refused++, 0;
        }
        s.last_sequence = 0;
    }
    // ("frame ID must rise by exactly 1 or the history resets": ours is the present count, so a present another generator made is a gap, which is a reset)
    const bool reset = in.reset || !s.last_sequence || sequence != s.last_sequence + 1;
    s.resets += reset;

    constexpr VkImageLayout G = VK_IMAGE_LAYOUT_GENERAL, R = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    constexpr VkAccessFlags2 any = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT;
    const VkImageMemoryBarrier2 to_read[4] = {barrier(color, VK_ACCESS_2_MEMORY_WRITE_BIT, VK_ACCESS_2_SHADER_READ_BIT, G, R),
                                              barrier(in.depth, VK_ACCESS_2_MEMORY_WRITE_BIT, VK_ACCESS_2_SHADER_READ_BIT, G, R),
                                              barrier(in.motion, VK_ACCESS_2_MEMORY_WRITE_BIT, VK_ACCESS_2_SHADER_READ_BIT, G, R),
                                              barrier(out, any, any, G, G)};
    const VkImageMemoryBarrier2 to_general[4] = {barrier(color, VK_ACCESS_2_SHADER_READ_BIT, any, R, G),
                                                 barrier(in.depth, VK_ACCESS_2_SHADER_READ_BIT, any, R, G),
                                                 barrier(in.motion, VK_ACCESS_2_SHADER_READ_BIT, any, R, G),
                                                 barrier(out, any, any, G, G)};
    constexpr VkImageUsageFlags sampled = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    const FfxApiResource color_res = resource(color, VK_FORMAT_R8G8B8A8_UNORM, w, h, sampled, FFX_API_RESOURCE_STATE_COMPUTE_READ);
    const FfxApiResource out_res = resource(out, VK_FORMAT_R8G8B8A8_UNORM, w, h, sampled, FFX_API_RESOURCE_STATE_UNORDERED_ACCESS);
    const FfxApiResource depth_res = resource(in.depth, in.depth_format, in.render_w, in.render_h, sampled, FFX_API_RESOURCE_STATE_COMPUTE_READ);
    const FfxApiResource motion_res = resource(in.motion, in.motion_format, in.render_w, in.render_h, sampled, FFX_API_RESOURCE_STATE_COMPUTE_READ);

    // 1. ffxConfigure: frame generation on, no swapchain and no callbacks (the display thread presents), this frame's ID
    ffxConfigureDescFrameGeneration cfg{};
    cfg.header.type = FFX_API_CONFIGURE_DESC_TYPE_FRAMEGENERATION;
    cfg.swapChain = nullptr;
    cfg.presentCallback = nullptr, cfg.frameGenerationCallback = nullptr;
    cfg.frameGenerationEnabled = true;
    cfg.allowAsyncWorkloads = false;
    cfg.flags = FFX_FRAMEGENERATION_FLAG_NO_SWAPCHAIN_CONTEXT_NOTIFY;
    cfg.onlyPresentGenerated = false;
    cfg.generationRect = {0, 0, (int32_t)w, (int32_t)h};
    cfg.frameID = sequence;
    // 2. ffxDispatch(PrepareV2): the scene's depth, motion and camera
    ffxDispatchDescFrameGenerationPrepareV2 prep{};
    prep.header.type = FFX_API_DISPATCH_DESC_TYPE_FRAMEGENERATION_PREPARE_V2;
    prep.frameID = sequence;
    prep.flags = FFX_FRAMEGENERATION_FLAG_NO_SWAPCHAIN_CONTEXT_NOTIFY;
    prep.commandList = cmd;
    prep.renderSize = {in.render_w, in.render_h};
    prep.jitterOffset = {in.jitter[0], in.jitter[1]};   // (render pixels, as the projection was offset: as the upscalers take it)
    prep.motionVectorScale = {1.0f, 1.0f};              // (render pixels already, towards the frame before)
    prep.frameTimeDelta = in.frame_ms;
    prep.reset = reset;
    prep.cameraNear = in.near_z > 0 ? in.near_z : 0.1f;
    prep.cameraFar = std::isfinite(in.far_z) && in.far_z > prep.cameraNear && in.far_z < 1e30f ? in.far_z : 100000.0f;
    prep.cameraFovAngleVertical = in.fov_y > 0 ? in.fov_y : 1.0f;
    prep.viewSpaceToMetersFactor = 1.0f;
    prep.depth = depth_res;
    prep.motionVectors = motion_res;
    for (int i = 0; i < 3; i++) {   // (the view's inverse: position, right, up and forward, as DLSS frame generation's camera is made)
        prep.cameraPosition[i] = cam[i * 4 + 3];
        prep.cameraRight[i] = cam[i * 4 + 0];
        prep.cameraUp[i] = cam[i * 4 + 1];
        prep.cameraForward[i] = -cam[i * 4 + 2];
    }
    // 3. ffxDispatch: one made frame from the game's colour
    ffxDispatchDescFrameGeneration gen{};
    gen.header.type = FFX_API_DISPATCH_DESC_TYPE_FRAMEGENERATION;
    gen.commandList = cmd;
    gen.presentColor = color_res;
    gen.outputs[0] = out_res;
    gen.numGeneratedFrames = 1;
    gen.reset = reset;
    gen.backbufferTransferFunction = FFX_API_BACKBUFFER_TRANSFER_FUNCTION_SRGB;   // (the window picture's encoding)
    gen.minMaxLuminance[0] = 0.0f, gen.minMaxLuminance[1] = 0.0f;
    gen.generationRect = {0, 0, (int32_t)w, (int32_t)h};
    gen.frameID = sequence;

    ffxReturnCode_t rc = s.configure(&s.context->context, &cfg.header);
    const char* step = "ffxConfigure";
    if (rc == FFX_API_RETURN_OK) {
        barriers(cmd, to_read, 4);
        rc = s.dispatch(&s.context->context, &prep.header);
        step = "ffxDispatch (PrepareV2)";
        if (rc == FFX_API_RETURN_OK) {
            rc = s.dispatch(&s.context->context, &gen.header);
            step = "ffxDispatch (frame generation)";
        }
        note_work();   // (AMD's dispatches: full_barrier must not take the next barrier for a repeat of the one before them)
        barriers(cmd, to_general, 4);
    }
    if (rc != FFX_API_RETURN_OK) {
        static int told = 0;
        if (told++ < 12)
            logf("vk: AMD FSR 4 FG: %s said %u: %s (frame %llu, %ux%u from %ux%u)", step, (unsigned)rc, words(rc), (unsigned long long)sequence, w, h,
                 in.render_w, in.render_h);
        s.last_sequence = 0;
        s.refused++;
        if (++s.failures >= kFailuresOff) {
            s.failed = true;
            logf("vk: AMD FSR 4 FG: %d sets in a row refused (last: %s, %s): off for this run, the renderer's own (or FSR 3.1) makes the frames",
                 kFailuresOff, step, words(rc));
            retire(s);
        }
        return 0;   // (a half-made set: the other generators make all of it)
    }
    s.failures = 0;
    s.last_sequence = sequence;
    s.made++;
    static bool told = false;
    if (!told) {
        told = true;
        logf("vk: AMD FSR 4 FG: AMD's FSR 4 frame generation makes the frame between each two of the game's (%ux%u, its depth and motion %ux%u, "
             "frame ID %llu)", w, h, in.render_w, in.render_h, (unsigned long long)sequence);
    }
    return 1;
}

#else  // (no HOOKSHOT_FSR in this build, or not Windows: AMD's runtime is not there)

Fsr4FgWhy fsr4fg_why() {
#ifndef _WIN32
    return env_fg() == 0 ? FSR4FG_ENV_OFF : FSR4FG_NOT_WINDOWS;
#else
    return env_fg() == 0 ? FSR4FG_ENV_OFF : FSR4FG_NOT_BUILT;
#endif
}
bool fsr4fg_offered() { return false; }
bool fsr4fg_wanted() { return false; }
void fsr4fg_probe() {}
int fsr4fg_frames(VkCommandBuffer, VkImage, VkImageView, uint32_t, uint32_t, VkImage, VkImageView, const DlssFgInputs&, uint64_t, bool) { return 0; }
void fsr4fg_idle() {}

#endif

}  // namespace mkwvk
