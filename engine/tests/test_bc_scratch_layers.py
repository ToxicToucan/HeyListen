# Copyright (C) 2026 Sacrappy

"""The BC scratch has the layers a run uses (Longshot #384): 1 on a card under 8 GB of video memory (20 MB, not 160), 8 where
the batched transcodes (TICK-051) can use them, RX_VK_BC_LAYERS=<n> over both. Every place that sizes, views, transitions or
walks the scratch asks bc_layers(), not the largest the scratch could be (kBcLayers). Reads gpu/vk/vk_texture.cpp: no GPU/game.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    src = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    m = re.search(r"uint32_t bc_layers\(\) \{.*?\n\}\n", src, re.S)
    if not m:
        raise SystemExit("vk_texture.cpp: no bc_layers()")
    rule = m.group(0)
    for need in ('getenv("RX_VK_BC_LAYERS")', "device_vram_bytes()", "8ull << 30", "kBcLayers"):
        if need not in rule:
            raise SystemExit("bc_layers() lacks " + need)
    for name in ("bool bc_scratch_make() {", "bool bc_scratch2_make() {", "bool bc_scratch_layout("):
        body = src[src.index(name):]
        body = body[:body.index("\n}\n")]
        if "kBcLayers" in body.replace("j < bc_layers()", ""):
            raise SystemExit("%s sizes by kBcLayers, not bc_layers()" % name)
        if "bc_layers()" not in body:
            raise SystemExit("%s does not ask bc_layers()" % name)
    copy = src[src.index("l0 += bc_layers()") - 120:src.index("l0 += bc_layers()") + 120]
    if "std::min(bc_layers()" not in copy:
        raise SystemExit("texture_copy_into_bc: its run is not the scratch's layers")
    if "bc_layers() == kBcLayers && bc_layer_views[kBcLayers - 1]" not in src:
        raise SystemExit("the batched transcode runs with a scratch that has fewer layers than it uses")
    print("BC scratch layers: bc_layers() everywhere, the batch only with all %d: ok" % 8)


if __name__ == "__main__":
    main()
