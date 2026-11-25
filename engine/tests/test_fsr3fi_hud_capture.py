# Copyright (C) 2026 Sacrappy

"""Frame generation's HUD-less capture (RX_VK_FSR_FG_HUDLESS), step 1 of the HUD-less chain.

The game draws its HUD into the same picture as the scene, after its gameplay upscale, and frame generation was handed the
picture as presented, HUD and all. Now the picture is copied right after the upscale wrote it (vk_draw.cpp `upscale_picture`
finds it, `framegen_hud_before` in vk_framegen.cpp copies it), and the present takes the copy as its own (`hud_source`) when
it is fresh, still the picture it was copied from, and the presented picture's size. By Longshot 597c5a4e0 (#1464, HumanBean)
and ed96031ac (#1691, futondestroyer: the native-resolution case); ported by behaviour.

Two fixtures, each compiling the real functions cut out of the sources, no GPU and no game:
  - the selector: the known gameplay pass at 100% render resolution (output as big as the input) is the picture; an inferred
    dispatch of the same size, an output narrower or less tall than the input, a non-scene format or FSR 3.1 not asked for is
    not;
  - the capture: copied after the upscale, taken at the present; a present with no copy since (a menu, a movie, loading), a
    picture destroyed or moved since, another size, another format or a level, Native HDR's PQ present and the switch off
    take nothing and copy nothing.

    python -I -B tests/test_fsr3fi_hud_capture.py --cxx g++ --headers <Vulkan-Headers/include>
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))   # (python -I leaves the check's own folder off the path)
from srccut import block, between  # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}", "--headers", "{vulkan_headers}"]

ROOT = Path(__file__).resolve().parents[1]

SELECTOR_PREFIX = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>

using VkFormat = unsigned;
constexpr VkFormat VK_FORMAT_UNDEFINED = 0, SCENE = 1, MOVIE = 2;
constexpr unsigned VK_IMAGE_VIEW_TYPE_2D = 1;
enum class ViewUse { Sample, Storage };
struct Texture { uint32_t width, height; VkFormat format; };
struct Descriptor { unsigned handle; VkFormat format; };
struct StageInfo {
    bool uses_cooperative_matrix;
    std::vector<Descriptor> texture_descriptors, image_descriptors;
};
struct Stage { StageInfo info; };
struct Program { uint64_t key; std::vector<Stage> stages; };
struct NvnState {};
bool hud_wanted = true;
bool framegen_hud_wanted() { return hud_wanted; }

Texture in_tex{1920, 1080, SCENE}, out_tex{1920, 1080, SCENE};
uint32_t output_level = 0;

uint32_t handle_of(const NvnState&, const Stage&, const Descriptor& descriptor, int) { return descriptor.handle; }
VkFormat image_format(unsigned) { return SCENE; }
bool temporal_colour_pair(VkFormat in, VkFormat out) { return in == SCENE && out == SCENE; }

void texture_view_of(uint32_t handle, unsigned, ViewUse use, VkFormat, Texture** texture, uint32_t* level = nullptr) {
    if (use == ViewUse::Sample && handle == 1) *texture = &in_tex;
    if (use == ViewUse::Storage && handle == 2) {
        *texture = &out_tex;
        *level = output_level;
    }
}
'''

SELECTOR_CASES = r'''
void expect(const char* what, const Program& program, Texture* wanted) {
    uint32_t level = 0;
    if (upscale_picture(NvnState{}, program, &level) != wanted || (wanted && level != output_level)) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        std::exit(1);
    }
}

int main() {
    const Program full{0x9dc98871eb7fc101ull, {{StageInfo{true, {{1, SCENE}}, {{2, SCENE}}}}}};
    const Program inferred{0x123ull, {{StageInfo{true, {{1, SCENE}}, {{2, SCENE}}}}}};
    const Program plain{0x9dc98871eb7fc101ull, {}};
    expect("The known 100% gameplay dispatch did not supply a pre-HUD picture", full, &out_tex);
    expect("An inferred equal-size dispatch supplied a pre-HUD picture", inferred, nullptr);
    expect("A program with no stage supplied a pre-HUD picture", plain, nullptr);
    out_tex.height = 1079;
    expect("Equal widths with different heights were treated as native resolution", full, nullptr);
    out_tex = {1919, 1080, SCENE};
    expect("A smaller output supplied a pre-HUD picture", full, nullptr);
    out_tex = {3840, 2160, SCENE};
    output_level = 1;
    expect("The selected output mip's equal dimensions were ignored", full, &out_tex);
    output_level = 0;
    in_tex = {1440, 810, SCENE};
    out_tex = {1920, 1080, SCENE};
    expect("A scaled gameplay dispatch lost its pre-HUD picture", inferred, &out_tex);
    expect("A scaled known gameplay dispatch lost its pre-HUD picture", full, &out_tex);
    out_tex.format = MOVIE;
    expect("A non-scene image supplied a pre-HUD picture", full, nullptr);
    out_tex.format = SCENE;
    hud_wanted = false;
    expect("HUD capture ran with the capture not wanted", full, nullptr);
    std::puts("selector: ok");
}
'''

CAPTURE_PREFIX = r'''
#include <algorithm>
#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <mutex>
#include <string>
#include <utility>
#include <vector>
#define VK_NO_PROTOTYPES
#define VK_USE_64_BIT_PTR_DEFINES 0
#include <vulkan/vulkan.h>
using VmaAllocation = void*;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); std::exit(1); } } while (0)
'''

CAPTURE_STUBS = r'''
struct Texture {   // (vk_texture.h's, the fields framegen_hud_before reads)
    VkImage image = VK_NULL_HANDLE;
    VkFormat format = VK_FORMAT_UNDEFINED;
    VkImageType type = VK_IMAGE_TYPE_2D;
    uint32_t width = 1, height = 1, levels = 1, layers = 1;
    VkSampleCountFlagBits samples = VK_SAMPLE_COUNT_1_BIT;
    uint64_t gpu = 0;
};
void logf(const char*, ...) {}
uint64_t next_handle = 0x1000;
template <class T> T handle() { return (T)(uintptr_t)(next_handle += 0x10); }
int made_images = 0;
constexpr uint32_t FG_HUD_COMP_SPV[] = {0}, FG_HUD_LAYER_COMP_SPV[] = {0}, FG_HUD_COMPOSITE_COMP_SPV[] = {0};
void drop_pipe(Pipe& p) { p = {}; }
bool fail_layer_pipes = false;   // (the layer's pipelines cannot be made)
struct { Img frames[2]; VkSampler nearest = VK_NULL_HANDLE; } fg;
int made_pipes = 0;
bool make_pipe(Pipe& p, const uint32_t*, size_t, std::initializer_list<VkDescriptorType> types, uint32_t push) {
    CHECK((types.size() == 4 && push == 16) || (types.size() == 6 && push == 20) || (types.size() == 3 && push == 8));
    if (types.size() != 4 && fail_layer_pipes) return false;
    p.pipe = handle<VkPipeline>(), p.layout = handle<VkPipelineLayout>(), p.set = handle<VkDescriptorSetLayout>();
    return ++made_pipes, true;
}
// (the HUD-less pass: its pipeline, its bindings, its push constants)
struct Pass { VkPipeline pipe; std::vector<VkImageView> views; int32_t pc[5]; uint32_t w, h; };
std::vector<Pass> passes;
void pass(VkCommandBuffer, const Pipe& p, std::initializer_list<std::pair<VkImageView, VkSampler>> bindings, uint32_t w,
          uint32_t h, const void* push = nullptr, uint32_t push_bytes = 0) {
    Pass r{p.pipe, {}, {}, w, h};
    for (const auto& b : bindings) r.views.push_back(b.first);
    CHECK(push && push_bytes <= sizeof r.pc && push_bytes >= 8);
    std::memcpy(r.pc, push, push_bytes);
    passes.push_back(r);
}
bool make_img(Img& i, uint32_t, uint32_t, VkFormat, VkImageUsageFlags) {
    i.image = handle<VkImage>(), i.view = handle<VkImageView>();
    return ++made_images, true;
}
void drop_img(Img& i) { i = {}; }
void to_general(VkCommandBuffer, VkImage) {}
void full_barrier(VkCommandBuffer) {}
struct Copy { VkImage src, dst; uint32_t w, h; };
std::vector<Copy> copies;
void vkCmdCopyImage(VkCommandBuffer, VkImage src, VkImageLayout, VkImage dst, VkImageLayout, uint32_t n, const VkImageCopy* c) {
    CHECK(n == 1);
    copies.push_back({src, dst, c->extent.width, c->extent.height});
}
uint64_t frame_no = 100, retired = 1;
uint64_t texture_frame_no() { return frame_no; }
uint64_t vk_retirement_value() { return 1; }
uint64_t vk_timeline_completed() { return retired; }
// (vk_texture.cpp's: the textures not destroyed, by their GPU address; a destroyed one is never read)
std::vector<std::pair<uint64_t, const Texture*>> alive;
bool texture_alive(uint64_t gpu, const Texture* t, VkImage image) {
    for (const auto& [g, a] : alive)
        if (g == gpu && a == t) return t->image == image;
    return false;
}
bool fg_on = true;
bool framegen_on() { return fg_on; }
bool framegen_hud_wanted();
namespace fsr3fi {
bool wanted = true;
uint32_t frames_wanted() { return wanted ? 1u : 0u; }
}
'''

CAPTURE_SUFFIX = r'''
int main(int argc, char** argv) {
    const bool switch_on = argc < 2 || std::strcmp(argv[1], "off") != 0;
    const bool built_in = argc > 1 && std::strcmp(argv[1], "builtin") == 0;   // (RX_VK_FG_HUDLESS alone: FSR 3.1 not asked for)
    if (switch_on && !built_in) putenv((char*)"RX_VK_FSR_FG_HUDLESS=1");
    if (built_in) putenv((char*)"RX_VK_FG_HUDLESS=1"), fsr3fi::wanted = false;
    const VkCommandBuffer cmd = (VkCommandBuffer)(uintptr_t)1;
    Texture picture;
    picture.image = handle<VkImage>(), picture.format = VK_FORMAT_B10G11R11_UFLOAT_PACK32;
    picture.width = 2560, picture.height = 1440, picture.gpu = 0x80000000;
    alive.push_back({picture.gpu, &picture});

    if (!switch_on) {   // (the switch off, the default: nothing copied, nothing taken, whatever the frame is)
        framegen_hud_before(cmd, &picture, 0);
        CHECK(copies.empty() && made_images == 0 && passes.empty() && made_pipes == 0);
        CHECK(!hud_source(cmd, 0, 2560, 1440, 0, 0, 2560, 1440, false) && !hud.ready && hud.captured == 0);
        std::puts("capture off: ok");
        return 0;
    }
    fg.frames[0] = {handle<VkImage>(), nullptr, handle<VkImageView>()};
    fg.frames[1] = {handle<VkImage>(), nullptr, handle<VkImageView>()};
    const auto present = [&](uint32_t w = 2560, uint32_t h = 1440, bool pq = false) {
        return hud_source(cmd, 0, w, h, 0, 0, w, h, pq);
    };

    if (built_in) {   // (RX_VK_FG_HUDLESS alone, FSR 3.1 not asked for: the built-in interpolation's pair)
        framegen_hud_before(cmd, &picture, 0);
        CHECK(copies.size() == 1 && present() && hud.ready && hud.layered && hud.captured == 1);
        CHECK(!hud.pair);   // (no frame before: this frame's picture alone)
        const int first = hud.hl;
        framegen_hud_before(cmd, &picture, 0);
        CHECK(present() && hud.pair && hud.hl == (first ^ 1) && hud.hudless[0].image != hud.hudless[1].image &&
              hud.hudless[hud.hl ^ 1].image);
        CHECK(!present() && !hud.pair);   // (a present with no picture: the frame after it has no pair)
        framegen_hud_before(cmd, &picture, 0);
        CHECK(present() && !hud.pair);
        framegen_hud_before(cmd, &picture, 0);
        CHECK(present() && hud.pair);
        // (another crop: no pair; the layer failing: no pair, the HUD-less colour alone)
        framegen_hud_before(cmd, &picture, 0);
        CHECK(hud_source(cmd, 0, 2560, 1440, 320, 0, 1920, 1440, false) && !hud.pair);
        framegen_hud_before(cmd, &picture, 0);
        CHECK(hud_source(cmd, 0, 2560, 1440, 320, 0, 1920, 1440, false) && hud.pair);
        fail_layer_pipes = true, hud.fit_pipe = {}, hud.over_pipe = {};
        framegen_hud_before(cmd, &picture, 0);
        CHECK(present() && hud.ready && !hud.layered && !hud.pair);
        fail_layer_pipes = false;
        // (frame generation off: nothing copied, nothing taken)
        const size_t b = copies.size();
        fg_on = false;
        framegen_hud_before(cmd, &picture, 0);
        fg_on = true;
        CHECK(copies.size() == b && !present());
        std::puts("capture builtin: ok");
        return 0;
    }

    // (a frame of play: the picture copied after its upscale, taken at the present)
    framegen_hud_before(cmd, &picture, 0);
    CHECK(copies.size() == 1 && copies[0].src == picture.image && copies[0].w == 2560 && copies[0].h == 1440);
    CHECK(copies[0].dst == hud.before[0].image);
    CHECK(present() && hud.ready && hud.captured == 1);
    // (the picture as presented copied too, and the HUD-less pass run over the frame, the picture before the HUD, and that copy)
    CHECK(copies.size() == 2 && copies[1].src == picture.image && copies[1].dst == hud.after[0].image);
    CHECK(passes.size() == 1 && passes[0].views.size() == 4 && passes[0].views[0] == fg.frames[0].view &&
          passes[0].views[1] == hud.before[0].view && passes[0].views[2] == hud.after[0].view &&
          passes[0].views[3] == hud.hudless[0].view && passes[0].w == 2560 && passes[0].h == 1440);
    CHECK(passes[0].pc[0] == 0 && passes[0].pc[1] == 0 && passes[0].pc[2] == 2560 && passes[0].pc[3] == 1440);
    // (and not the next present's too: the next one with no upscale since (a menu, a movie, loading) goes on as it is)
    CHECK(!present() && !hud.ready && hud.captured == 1);

    // (the next frame's copy goes into the other of the two, the frame before's stays)
    framegen_hud_before(cmd, &picture, 0);
    CHECK(copies.size() == 3 && copies[2].dst == hud.before[1].image && hud.before[0].image != hud.before[1].image);
    CHECK(present() && hud.captured == 2 && hud.after[0].image != hud.after[1].image);
    // (a crop of the picture: the pass's corner in it)
    framegen_hud_before(cmd, &picture, 0);
    CHECK(hud_source(cmd, 0, 2560, 1440, 320, 0, 1920, 1440, false) && passes.back().pc[0] == 320 &&
          passes.back().pc[2] == 1920 && passes.back().w == 1920);
    CHECK(hud.hudless[0].image && hud.captured == 3);

    // (the picture destroyed since the copy, or moved to another image: nothing to take)
    framegen_hud_before(cmd, &picture, 0);
    alive.clear();
    CHECK(!present());
    alive.push_back({picture.gpu, &picture});
    framegen_hud_before(cmd, &picture, 0);
    const VkImage moved_from = picture.image;
    picture.image = handle<VkImage>();
    CHECK(!present());
    picture.image = moved_from;
    CHECK(!present());   // (and that copy is not taken up a present later)

    // (not the presented picture's size: nothing; Native HDR's PQ present: nothing, its frames are another encoding)
    framegen_hud_before(cmd, &picture, 0);
    CHECK(!present(3840, 2160));
    framegen_hud_before(cmd, &picture, 0);
    CHECK(!present(2560, 1440, true));

    // (not the scene's B10G11R11: a movie's RGBA8 upscale, PsychoV35's RGBA16F; nor another level, nor no picture)
    const size_t n = copies.size();
    Texture other = picture;
    other.format = VK_FORMAT_R8G8B8A8_UNORM;
    framegen_hud_before(cmd, &other, 0);
    other.format = VK_FORMAT_R16G16B16A16_SFLOAT;
    framegen_hud_before(cmd, &other, 0);
    framegen_hud_before(cmd, &picture, 1);
    framegen_hud_before(cmd, nullptr, 0);
    Texture ms = picture;
    ms.samples = VK_SAMPLE_COUNT_4_BIT;
    framegen_hud_before(cmd, &ms, 0);
    CHECK(copies.size() == n && !present());

    // (a picture of another size: the copies are made again at that size, the old ones retired and let go later)
    Texture big = picture;
    big.image = handle<VkImage>(), big.width = 3840, big.height = 2160, big.gpu = 0x90000000;
    alive.push_back({big.gpu, &big});
    framegen_hud_before(cmd, &big, 0);
    CHECK(copies.back().w == 3840 && copies.back().h == 2160 && hud.dead.size() >= 4);
    CHECK(present(3840, 2160));
    frame_no += 10;
    CHECK(!present());   // (frees the retired images: the GPU is past them)
    CHECK(hud.dead.empty());

    // (FSR 3.1 not asked for, or frame generation off: nothing copied, nothing taken)
    const size_t m = copies.size();
    fsr3fi::wanted = false;
    framegen_hud_before(cmd, &big, 0);
    CHECK(!present(3840, 2160));
    fsr3fi::wanted = true;
    fg_on = false;
    framegen_hud_before(cmd, &big, 0);
    fg_on = true;
    CHECK(copies.size() == m && !present(3840, 2160));
    std::puts("capture: ok");
    return 0;
}
'''


def run(cmd):
    subprocess.run(cmd, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++")
    p.add_argument("--headers", type=Path, required=True)
    p.add_argument("--source", type=Path, default=ROOT)
    a = p.parse_args()
    draw = (a.source / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    fg = (a.source / "gpu/vk/vk_framegen.cpp").read_text(encoding="utf-8")
    flags = ["-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers", "-Wno-unused-function",
             "-Wno-unused-parameter", "-pthread"]
    with tempfile.TemporaryDirectory(prefix="fsr3fi-hud-capture-") as temp:
        sel_cpp, sel_exe = Path(temp) / "selector.cpp", Path(temp) / "selector.exe"
        functions = [block(draw, m) for m in ("bool gameplay_network(", "static Texture* upscale_picture(")]
        sel_cpp.write_text("\n".join([SELECTOR_PREFIX, *functions, SELECTOR_CASES]), encoding="utf-8")
        run([a.cxx, *flags, str(sel_cpp), "-o", str(sel_exe)])
        run([str(sel_exe)])

        cap_cpp, cap_exe = Path(temp) / "capture.cpp", Path(temp) / "capture.exe"
        img = between(fg, "struct Img {", "struct OutSet {") + between(fg, "struct Pipe {", "struct State {")
        hud = between(fg, "struct HudState {", "} // namespace\n\nbool framegen_hud_wanted(")
        cap_cpp.write_text(CAPTURE_PREFIX + img + CAPTURE_STUBS + hud + block(fg, "bool framegen_hud_wanted(") +
                           block(fg, "void framegen_hud_before(") + CAPTURE_SUFFIX, encoding="utf-8")
        run([a.cxx, *flags, "-I" + str(a.headers), str(cap_cpp), "-o", str(cap_exe)])
        run([str(cap_exe)])
        run([str(cap_exe), "off"])
        run([str(cap_exe), "builtin"])
    # (the wiring the fixtures cannot see: the dispatch's call sites, and the sources of the picture)
    for need in ("framegen_hud_before(cmd, stub_picture, 0)", "framegen_hud_before(cmd, picture, picture_level)",
                 "if (fsr3_stand_in(cmd, s, p)) return framegen_hud_before(", "if (temporal_stand_in(cmd, s, p)) return framegen_hud_before(",
                 "if (off_stand_in(cmd, s, p)) return framegen_hud_before(", "if (taau_stand_in(cmd, s, p)) return framegen_hud_before("):
        assert need in draw, "vk_draw.cpp: %r is missing (the gameplay upscale's picture is copied after whoever drew it)" % need
    assert "framegen_hud_wanted() && hud_source(" in fg, "framegen_record: the HUD-less picture is taken at the present when asked for"
    print("fsr3fi hud capture: ok")


if __name__ == "__main__":
    main()
