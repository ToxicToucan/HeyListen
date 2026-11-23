# Copyright (C) 2026 Sacrappy

"""The ports from ProjectReLink (pixl27, GPL-3.0-or-later) and Longshot (NotRexed): the all-zero upload shortcut
(RX_VK_ZERO_UPLOAD), the reaper thread for image, view and buffer destroys with the dead textures' cap (RX_VK_REAP,
RX_VK_DESTROY_PER_FRAME), the VirtualQuery probe at a pool import (RX_VK_IMPORT_VQ) and the PCIe link probe (RX_VK_LINK_PROBE).
HOS_BG_CAP's behaviour is in test_vcore_escape.py. Reads the sources: the renderer needs a GPU to run.

python -I -B tests/test_relink_ports.py
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import block   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu" / "vk"
fails = []


def check(ok, what):
    if not ok:
        fails.append(what)
        print("FAIL:", what)


def read(name):
    return (VK / name).read_text(encoding="utf-8")


tex, vma, mem, internal = read("vk_texture.cpp"), read("vk_vma.cpp"), read("vk_memory.cpp"), read("vk_internal.h")

# --- the all-zero upload ---
zero = block(tex, "bool upload_all_zero(Texture& tex, uint64_t total, const Ranges* dirty, bool widened_format)")
check('getenv("RX_VK_ZERO_UPLOAD")' in zero, "the all-zero upload has its switch, RX_VK_ZERO_UPLOAD")
check("total < (32ull << 20)" in zero, "only storage over 32 MB is looked at")
check("tex.astc || tex.bc || tex.sparse || widened_format" in zero, "not ASTC, BC, sparse or widened formats")
check("covered < total" in zero, "only an upload of the whole storage")
check("memory_has_shadow(tex.gpu, total)" in zero, "not a storage the capture kept as it was at its submit")
check("vkCmdClearDepthStencilImage" in zero and "vkCmdClearColorImage" in zero, "the image is cleared on the GPU, depth or colour")
inner = block(tex, "bool upload_inner(Texture& tex, const Ranges* dirty) {")
check(inner.index("upload_unswizzle_gpu(tex, total, dirty)") < inner.index("upload_all_zero(tex, total, dirty, widen_from != 0)"),
      "the GPU unswizzle (colour images) goes first, the zero check before the CPU unswizzle")
check(inner.index("upload_all_zero(") < inner.index("std::vector<VkBufferImageCopy> copies;"), "and before any staging")

# --- the reaper ---
check('getenv("RX_VK_REAP")' in vma and "std::thread([] {" in vma and "on_reaper = true;" in vma, "the reaper thread, RX_VK_REAP")
check("void reap(std::function<void()> fn);" in internal and "bool reap_drain();" in internal, "the reaper's interface is in vk_internal.h")
img = block(vma, "void destroy_image(VkImage image, VmaAllocation allocation) {")
check("reap([image, allocation]" in img and "destroy_image_now(image, allocation)" in img, "destroy_image goes to the reaper")
now = block(vma, "void destroy_image_now(VkImage image, VmaAllocation allocation) {")
check("vmaDestroyImage(vma, image, allocation);" in now and "note_free(allocation);" in now, "and the destroy itself is as before")
buf = block(vma, "void destroy_buffer(VkBuffer buffer, VmaAllocation allocation)")
check("host_untag" in buf and buf.index("host_untag") < buf.index("reap("), "a buffer's range is untagged before the reaper has it")
check("reap([buffer, allocation] { vmaDestroyBuffer(vma, buffer, allocation); });" in buf, "destroy_buffer goes to the reaper")
check("reap_drain() && vmaCreateImage" in vma, "a refused image waits for the queue and is made again")
dead_list = block(tex, "void texture_frame(uint64_t completed)")
check('getenv("RX_VK_DESTROY_PER_FRAME")' in dead_list and "per_frame_cfg = getenv" in dead_list, "the cap on dead textures a frame, RX_VK_DESTROY_PER_FRAME")
loop = dead_list[dead_list.index("for (auto it = dead.begin(); it != dead.end() && destroyed < per_frame;) {"):]
check("reap([views = std::move(it->views)]" in loop, "a dead texture's views go to the reaper before its image")
check("reap([m = std::move(it->memory)]" in loop, "and its own memory")
check("destroyed++;" in loop and "pool_put(*it);" in loop[: loop.index("destroyed++;")], "only images destroyed count, not the pool's")
check("vu > va / 100 * 95" in dead_list and "per_frame = INT_MAX;" in dead_list, "the cap is lifted with VRAM within 5% of the driver's allowance")
check("dead.size() / 30" in dead_list, "and a backlog is worked off in about half a second")
pd = block(tex, "void pool_destroy(Pooled& p)")
check(pd.index("reap(") < pd.index("destroy_image("), "a pooled image's views go before it")

# --- the probes ---
check("link_probe();" in block(mem, "bool memory_init()"), "the PCIe link is measured at memory_init")
probe = block(mem, "void link_probe()")
check('getenv("RX_VK_LINK_PROBE")' in probe and "kBytes = 32ull << 20" in probe, "RX_VK_LINK_PROBE, 32 MB each way")
check("vkCmdWriteTimestamp2" in probe and "GB/s to VRAM" in probe, "timed by the device's timestamps and logged in GB/s")
check('getenv("RX_VK_IMPORT_VQ")' in mem and "VirtualQuery probe" in mem, "the VirtualQuery probe at a pool import, RX_VK_IMPORT_VQ")

if fails:
    sys.exit(1)
print("ok")
