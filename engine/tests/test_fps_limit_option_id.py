# Copyright (C) 2026 Sacrappy

"""The Frame rate limit row's runtime option id is one named constant, off the ids upstream uses.

Hookshot's runtime option 10 is Text size and Longshot reserves 60 for its item wheel, so the row asks and is answered
under HL_OPT_FPS_LIMIT (gpu/vk/vk_opt_ids.h, 90). That header is not gpu/vk/vk_api.h on purpose: vk_api.h is part of the
shader stamp (nx2/build.py), and a change in it makes every player prepare shaders again.

python -I -B tests/test_fps_limit_option_id.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def main():
    ids = read("gpu", "vk", "vk_opt_ids.h")
    m = re.search(r"#define\s+HL_OPT_FPS_LIMIT\s+(\d+)", ids)
    assert m, "vk_opt_ids.h does not define HL_OPT_FPS_LIMIT"
    value = int(m.group(1))
    assert value == 90, f"HL_OPT_FPS_LIMIT is {value}: the id Longshot's trial used is 90"
    assert value not in (10, 60), "the id is one upstream uses"

    host = read("host", "nn_vi.c")
    assert "rx_option(HL_OPT_FPS_LIMIT," in host, "host/nn_vi.c does not ask under HL_OPT_FPS_LIMIT"
    assert "rx_option(10," not in host, "host/nn_vi.c still asks option 10"
    replay = read("gpu", "vk", "vk_replay.cpp")
    assert "id == HL_OPT_FPS_LIMIT ?" in replay, "the renderer does not answer HL_OPT_FPS_LIMIT"
    assert not re.search(r"\bid == 10\b", replay), "the renderer still answers option 10"

    # no other id of the renderer's table or of vk_api.h equals it
    api = read("gpu", "vk", "vk_api.h")
    assert not re.search(r"=\s*%d\b" % value, api), "vk_api.h already uses that id"
    assert "vk_opt_ids.h" not in api, "vk_api.h is part of the shader stamp: do not touch it"
    print("fps limit option id: ok (%d)" % value)


if __name__ == "__main__":
    main()
