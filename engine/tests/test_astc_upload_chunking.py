"""Large GPU ASTC uploads go to the GPU in bounded queue submissions (gpu/vk/vk_texture.cpp; no game or GPU needed).
Ported from Longshot d12ddf69b (Jer9, #1833); upstream's test cuts upload_astc_gpu whole into a Vulkan spy harness, which this
fork's (small scratch, batched transcodes) would not fit: the chunk rule is run here, the places that use it are read as text.

Kokiri Forest's two 1365 x 1365 x 90 11-mip ASTC terrain arrays decode 990 slices and re-encode 990 BC3 slices each (1,980 compute
dispatches, ~223M texels). Recorded into one upload command buffer, the submission ran past Windows' 2 s WDDM timeout on integrated
AMD GPUs (VK_ERROR_DEVICE_LOST, #1040, #1730). astc_chunk_step() counts the decoded texels since the last flush (up_astc_texels,
reset by texture_begin_recording) with a 64K-texel floor a step, and flushes the upload command buffer (upload_chunk_flush ->
replay_flush_upload) before a step that would take it past RX_VK_ASTC_CHUNK_MPX million texels (16; 0: never).

Run on the rule itself (astc_chunk_texels and astc_chunk_step cut out of the source, a stand-in flush counting submissions) over the
two arrays' dispatch sequences, level by level and layer by layer:
  - every chunk is at most the bound (a single step over it is a chunk of its own); the number of submissions is about the texels
    over the bound; none is empty;
  - RX_VK_ASTC_CHUNK_MPX=0 never flushes, a negative or non-number value is the 16M default, =4 splits at 4M texels;
  - a flush that does nothing (replay_flush_upload false: a buffer that is not a recording's own) leaves one chunk and keeps counting.
Source: the three loops of upload_astc_gpu (the BC levels, the plain levels, astc_bc_batched's groups) step before they bind, a level
too big for the bound is not one dispatch of all its layers, the flush resets this thread's barrier state, replay_flush_upload ends,
submits and waits for the buffer and begins it in place, and the switch's default row exists.

python -I -B tests/test_astc_upload_chunking.py [--cxx g++]
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))   # (python -I leaves the check's own folder off the path)
from srccut import block   # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r"""
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
struct VkCommandBuffer_T { int id; };
using VkCommandBuffer = VkCommandBuffer_T*;
thread_local uint64_t up_astc_texels = 0;
static bool flush_works = true;
static std::vector<uint64_t> chunks;   // (the texels of each submission, the last one the open chunk)
bool upload_chunk_flush(VkCommandBuffer) {
    if (!flush_works) return false;
    chunks.push_back(0);
    return true;
}
@RULE@
static void step(VkCommandBuffer cmd, uint64_t chunk, uint64_t texels) {
    if (chunks.empty()) chunks.push_back(0);
    const uint64_t floor_step = std::max<uint64_t>(texels, 65536ull);
    astc_chunk_step(cmd, chunk, texels);
    chunks.back() += floor_step;
}
int main(int argc, char** argv) {
    (void)argc;
    if (argv[1][0] == 'n') flush_works = false;
    VkCommandBuffer cmd = nullptr;
    const uint64_t chunk = astc_chunk_texels();
    std::printf("bound %llu\n", (unsigned long long)chunk);
    // the 1365 x 1365 x 90, 11-level array: one layer a step at each level (a level over the bound is not one dispatch of all)
    uint64_t total = 0, most_step = 0;
    for (int array = 0; array < 2; array++)
        for (uint32_t level = 0; level < 11; level++) {
            const uint32_t w = std::max(1u, 1365u >> level), h = std::max(1u, 1365u >> level);
            for (int layer = 0; layer < 90; layer++) {
                step(cmd, chunk, (uint64_t)w * h);
                total += std::max<uint64_t>((uint64_t)w * h, 65536ull);
                most_step = std::max<uint64_t>(most_step, (uint64_t)w * h);
            }
        }
    uint64_t biggest = 0;
    size_t empty = 0;
    for (uint64_t c : chunks) biggest = std::max(biggest, c), empty += c == 0;
    std::printf("total %llu chunks %zu biggest %llu empty %zu most_step %llu\n", (unsigned long long)total, chunks.size(),
                (unsigned long long)biggest, empty, (unsigned long long)most_step);
    return 0;
}
"""


def need(cond, why):
    if not cond:
        raise SystemExit("FAIL: " + why)


def run(exe, mode, env_value):
    env = {k: v for k, v in os.environ.items() if k != "RX_VK_ASTC_CHUNK_MPX"}
    if env_value is not None:
        env["RX_VK_ASTC_CHUNK_MPX"] = env_value
    r = subprocess.run([str(exe), mode], capture_output=True, text=True, timeout=60, env=env)
    if r.returncode:
        raise SystemExit("FAIL: the harness failed: " + r.stdout + r.stderr)
    out = {}
    for line in r.stdout.splitlines():
        k = line.split()
        if k[0] == "bound":
            out["bound"] = int(k[1])
        else:
            out.update({k[i]: int(k[i + 1]) for i in range(0, len(k), 2)})
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default=os.environ.get("CXX", "g++"))
    cxx = p.parse_args().cxx
    tex = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    rep = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")

    upload = block(tex, "bool upload_astc_gpu(Texture& tex, uint64_t total, const Ranges* dirty) {")
    batched = block(tex, "void astc_bc_batched(VkCommandBuffer cmd, Texture& tex, const std::vector<AstcPiece>& pieces, uint64_t staged, const Staging& s,\n                     BcCacheEntry* store) {")
    need("astc_chunk_step(cmd, chunk_texels, (uint64_t)g.w * g.h * n);\n                vkCmdBindPipeline" in upload,
         "the BC levels' runs do not step before they bind")
    need("astc_chunk_step(cmd, chunk_texels, (uint64_t)g.w * g.h * (all_lv ? tex.layers : 1));" in upload,
         "the plain levels do not step before they dispatch")
    plain = upload[upload.index("(all_lv ? tex.layers : 1));") :]
    need(plain.index("vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, astc_pipe)") < plain.index("vkCmdPushDescriptorSetKHR"),
         "the plain levels do not bind the decoder's pipeline again after a step (a flush leaves none bound: a null pipeline in the driver)")
    need("all_lv = all && (!chunk_texels || (uint64_t)g.w * g.h * tex.layers <= chunk_texels)" in upload,
         "a level over the bound is one dispatch of every layer")
    need("astc_chunk_step(cmd, astc_chunk_texels(), group_texels);" in batched, "the batched groups do not step")
    flush = block(tex, "bool upload_chunk_flush(VkCommandBuffer cmd) {")
    need("if (par_active.load(std::memory_order_relaxed) || tl_seg_index >= 0) return false;" in flush,
         "upload_chunk_flush flushes under the parallel replay (a new image's first layout change is in a later init command buffer)")
    for part in ("replay_flush_upload(cmd)", "last_barrier_cmd = VK_NULL_HANDLE;", "trust_cmd = VK_NULL_HANDLE;",
                 "last_pass_end_cmd = VK_NULL_HANDLE;", "full_barrier(cmd);"):
        need(part in flush, "upload_chunk_flush: " + part)
    need("up_astc_texels = 0;\n    memory_table_begin();" in tex, "texture_begin_recording does not reset the chunk's count")
    pol = (ROOT / "gpu/vk/astc_cpu_policy.h").read_text(encoding="utf-8")
    need("return integrated || device_id == 0x164E || device_id == 0x13C0;" in pol and "astc_cpu_policy::by_default(" in tex,
         "the AMD integrated CPU-decode rule (Ryzen 7000/9000 and the larger iGPUs) is gone while the parallel replay cannot chunk")

    fl = block(rep, "bool replay_flush_upload(VkCommandBuffer cur) {")
    order = ["vkEndCommandBuffer(cur)", "vkQueueSubmit2(dev.queue, 1, &si, r->fence)", "vkWaitForFences(dev.device, 1, &r->fence",
             "vkResetFences(dev.device, 1, &r->fence)", "vkResetCommandPool(dev.device, r->upload_pool, 0)", "vkBeginCommandBuffer(r->upload, &bi)"]
    at = [fl.find(o) for o in order]
    need(all(a >= 0 for a in at) and at == sorted(at), "replay_flush_upload: end, submit, wait, reset the fence and the pool, begin again, in that order")
    need("!slot.submitted" in fl and "if (!r) return false;" in fl, "replay_flush_upload may flush a buffer that is not a recording's own")

    rule = block(tex, "uint64_t astc_chunk_texels() {") + "\n" + block(tex, "void astc_chunk_step(VkCommandBuffer cmd, uint64_t chunk_texels, uint64_t step_texels) {")
    with tempfile.TemporaryDirectory(prefix="astc-chunk-") as temp:
        src, exe = Path(temp) / "chunk.cpp", Path(temp) / "chunk.exe"
        src.write_text(FIXTURE.replace("@RULE@", rule), encoding="utf-8")
        r = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", str(src), "-o", str(exe)], capture_output=True, text=True)
        if r.returncode:
            print(r.stdout + r.stderr)
            raise SystemExit("FAIL: the rule did not compile")
        d = run(exe, "y", None)
        need(d["bound"] == 16000000, "the default bound is not 16M texels")
        need(d["biggest"] <= 16000000 + d["most_step"] and d["empty"] == 0, "a chunk is past the bound or empty: %r" % d)
        need(d["chunks"] >= d["total"] // 16000000 and d["chunks"] <= d["total"] // 8000000 + 2,
             "the number of submissions does not follow the texels: %r" % d)
        need(d["chunks"] >= 14, "the two arrays (~223M texels) went in fewer than 14 submissions: %r" % d)
        d0 = run(exe, "y", "0")
        need(d0["bound"] == 0 and d0["chunks"] == 1, "=0 still splits: %r" % d0)
        for bad in ("-3", "abc", "", "5x"):
            need(run(exe, "y", bad)["bound"] == 16000000, "RX_VK_ASTC_CHUNK_MPX=%r is not the default" % bad)
        d4 = run(exe, "y", "4")
        need(d4["bound"] == 4000000 and d4["biggest"] <= 4000000 + d4["most_step"] and d4["chunks"] > d["chunks"], "=4 does not split at 4M texels: %r" % d4)
        dn = run(exe, "n", None)
        need(dn["chunks"] == 1, "a flush that does nothing still made chunks: %r" % dn)
    print("PASS: GPU ASTC uploads are bounded to 16M decoded texels a submission (two Kokiri arrays: %d submissions); the loops step, the flush resets the barrier state" % d["chunks"])


if __name__ == "__main__":
    main()
