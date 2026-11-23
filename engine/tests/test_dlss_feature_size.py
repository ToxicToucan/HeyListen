# Copyright (C) 2026 Sacrappy

"""DLSS's feature is created at each mode's recommended input size (NGX_DLSS_GET_OPTIMAL_SETTINGS' w, h), not at the largest
size of the mode's dynamic range: the input images and the feature's range (feature_rw/rh, min_rw/rh) still cover the whole
range, so a render size inside it needs no new feature. DLAA's creation size is the output's.

Longshot's own check compiles prepare() against an SDK stand-in; this tree's prepare differs (its State, its signature), so
this reads the source. No GPU/SDK/game. API sizing reference: NVIDIA-RTX/Streamline source/plugins/sl.dlss/dlssEntry.cpp.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = (ROOT / "gpu/vk/vk_dlss.cpp").read_text(encoding="utf-8")
    begin = source.index("int prepare(VkCommandBuffer cmd")
    end = source.index("\nbool start()", begin)
    body = source[begin:end]
    if not re.search(r"unsigned create_w = ow, create_h = oh;", body):
        raise SystemExit("vk_dlss.cpp prepare: DLAA's creation size is not the output's")
    if "create_w = w, create_h = h" not in body:
        raise SystemExit("vk_dlss.cpp prepare: the chosen mode's recommended size is not kept for the feature's creation")
    if "cp.Feature.InWidth = create_w, cp.Feature.InHeight = create_h" not in body:
        raise SystemExit("vk_dlss.cpp prepare: the feature is not created at the recommended size")
    if "cp.Feature.InWidth = max_w" in body:
        raise SystemExit("vk_dlss.cpp prepare: the feature is created at the largest size of the range")
    if "s.feature_rw = max_w, s.feature_rh = max_h" not in body or "s.min_rw = min_w, s.min_rh = min_h" not in body:
        raise SystemExit("vk_dlss.cpp prepare: the feature no longer keeps its dynamic range for the input images")
    print("DLSS feature creation size: recommended size, range kept: ok")


if __name__ == "__main__":
    main()
