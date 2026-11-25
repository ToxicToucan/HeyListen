# Copyright (C) 2026 Sacrappy

"""A texture's first look of a submission without ParLock (RX_VK_TOUCH_FAST, gpu/vk/touch_fast.h, stream PIPE1): the decision against the
locked texture_touch's rules (tests/touch_fast_test.cpp), and the wiring in vk_texture.cpp and vk_draw.cpp (on by default, =0 off, serial replay
untouched, the order of the reads, the check mode's locked answer, both ParLock touch sites asking it first).
python -I -B tests/test_touch_fast.py [--cxx clang++]"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def need(body, needles, where):
    for needle in needles:
        if needle not in body:
            raise SystemExit(f"{where} no longer has: {needle}")


def check_wiring():
    tex = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    mode = tex[tex.index("int touch_fast_mode() {"):tex.index("std::atomic<int64_t> st_tf_skip")]
    need(mode, ('env_on("RX_VK_TOUCH_FAST_CHECK")', 'getenv("RX_VK_TOUCH_FAST")', "check ? 2 : e && atoi(e) == 0 ? 0 : 1"), "touch_fast_mode")
    dec = tex[tex.index("touch_fast::Verdict touch_fast_decide("):tex.index("void touch_fast_log()")]
    # (upload_gen read first, with acquire; upload_value / upload_seg after the memory look)
    a = dec.index("__atomic_load_n(&tex->upload_gen, __ATOMIC_ACQUIRE)")
    b = dec.index("texture_dirty(*tex, since, dirty)")
    c = dec.index("__atomic_load_n(&tex->upload_value, __ATOMIC_ACQUIRE)")
    if not a < b < c:
        raise SystemExit("touch_fast_decide no longer reads upload_gen, then the memory, then the segment order")
    need(dec, ("in.virtual_storage = tex->gpu >= (1ull << 40)", "in.deferred_before = tex->defer_frame != 0", "gpu_wrote_pending(*tex)"),
         "touch_fast_decide")
    body = tex[tex.index("bool texture_touch_unlocked(Texture* tex, bool for_sampling) {"):tex.index("void texture_gpu_wrote(")]
    need(body, ("!par_active.load(std::memory_order_relaxed)) return false;",   # (serial replay: the old path bit for bit)
                "if (mode == 2) {", "ParLock lk;", "texture_dirty(*tex, since, dirty)",   # (the check asks the memory under the lock)
                "tex->checked_epoch = epoch - 1;", "texture_touch(tex, for_sampling);",    # (a disagreement: the locked path decides)
                "if (tex->checked_epoch != epoch) tex->checked_epoch = epoch;"), "texture_touch_unlocked")
    # (gpu_wrote_pending asks without the side effect gpu_wrote_into has)
    pend = tex[tex.index("bool gpu_wrote_pending(const Texture& tex) {"):tex.index("touch_fast::Verdict touch_fast_decide(")]
    if "gpu_wrote_seen =" in pend:
        raise SystemExit("gpu_wrote_pending changes gpu_wrote_seen")
    need(pend, ("std::lock_guard lk(gpu_wrote_mu);",), "gpu_wrote_pending")
    # (texture_touch stores upload_value / upload_seg before upload_gen, with a release fence between)
    touch = tex[tex.index("Texture* texture_touch(Texture* tex, bool for_sampling) {"):tex.index("VkImageView texture_view(Texture* tex,")]
    a = touch.index("tex->upload_value = rec_value, tex->upload_seg = tl_seg;\n        // (its storage joins")
    b = touch.index("std::atomic_thread_fence(std::memory_order_release);")
    c = touch.index("tex->upload_gen = gen;\n        tex->gpu_written = false;")
    if not a < b < c:
        raise SystemExit("texture_touch no longer stores the upload's segment, a release fence, then upload_gen")
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    need(draw, ("!texture_touch_unlocked(it->second.tex, it->second.sampling)) {   // (stream PIPE1, RX_VK_TOUCH_FAST)",
                "if (!texture_touch_quick(tex, true) && !texture_touch_unlocked(tex, true)) {   // (stream PIPE1, RX_VK_TOUCH_FAST)"),
         "vk_draw.cpp's touch sites")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="clang++")
    a = p.parse_args()
    check_wiring()
    with tempfile.TemporaryDirectory(prefix="touch-fast-") as temp:
        exe = Path(temp) / "touch_fast_test.exe"
        subprocess.run([a.cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", str(ROOT / "tests/touch_fast_test.cpp"), "-o", str(exe)],
                       check=True)
        out = subprocess.run([str(exe)], check=True, timeout=120, capture_output=True, text=True).stdout
        assert " 0 failures" in out, out
    print("PASS:", out.strip().splitlines()[-1])


if __name__ == "__main__":
    main()
