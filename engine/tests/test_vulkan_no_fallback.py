"""A PC with no usable Vulkan driver is told so; it no longer falls into the WebGPU renderer (host/rx_backend.c rx_start).

v0.4.2 and v0.4.3 reports: vkCreateInstance failed, the log said "the Vulkan back end did not start: WebGPU", and wgr_start
panicked inside wgpu_native.dll (an uncaught C++ exception, KERNELBASE 0xe06d7363) while the title was loading. The WebGPU
renderer was never made for this game. Now the default path ends in hos_fatal with the reason; RX_BACKEND=webgpu (any other
value than vulkan or eden) and HOS_VK_FALLBACK_WEBGPU=1 still reach it. The back end also logs the VkResult.

python -I -B tests/test_vulkan_no_fallback.py
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    rb = (ROOT / "host" / "rx_backend.c").read_text(encoding="utf-8")
    start = rb[rb.index("int rx_start(void *hwnd) {"):]
    start = start[:start.index("\n}\n")]
    vk_part, wgr_part = start.split("const int ok = wgr_start(hwnd);")
    if "hos_fatal(" not in vk_part or "HOS_VK_FALLBACK_WEBGPU" not in vk_part:
        raise SystemExit("FAIL: rx_start's Vulkan path does not end in hos_fatal (with HOS_VK_FALLBACK_WEBGPU as its off switch)")
    if not re.search(r"if \(!eden && !getenv\(\"HOS_VK_FALLBACK_WEBGPU\"\)\)\s*hos_fatal", vk_part):
        raise SystemExit("FAIL: the fatal is not tied to the default Vulkan path (not Eden's, not the off switch)")
    if vk_part.index("hos_fatal(") < vk_part.index("could not start on the window"):
        raise SystemExit("FAIL: the fatal comes before the log lines that say why the back end did not start")
    vm = (ROOT / "gpu" / "vk" / "vk_main.cpp").read_text(encoding="utf-8")
    if not re.search(r'logf\("vk: vkCreateInstance failed \(VkResult %d', vm):
        raise SystemExit("FAIL: vkCreateInstance's failure does not log its VkResult")
    print("PASS: no usable Vulkan driver ends the start with its reason, not in the WebGPU renderer's panic")


if __name__ == "__main__":
    main()
