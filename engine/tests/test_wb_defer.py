# Copyright (C) 2026 Sacrappy

"""Compile and run the deferred write-back's rules (gpu/vk/wb_defer.h; no game/GPU) and check that vk_memory.cpp, vk_replay.cpp and
vk_internal.h wire them as designed (WBL1: ProjectReLink's RX_VK_WB_LAZY by pixl27, ported by hand)."""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_source():
    mem = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    for needle in ('#include "wb_defer.h"',
                   'getenv("RX_VK_WB_LAZY")',
                   'getenv("RX_VK_SLOW_LINK_GBPS")',
                   'getenv("RX_VK_WB_GUARD")',
                   'getenv("RX_VK_WB_LAZY_FRAMES")',
                   'getenv("RX_VK_WB_LAZY_QUIET")',
                   'getenv("RX_VK_WB_LAZY_MIN_KB")',
                   'getenv("RX_VK_WB_LAZY_OWN")',
                   'getenv("RX_VK_WB_LAZY_HOT")',
                   "wb_defer::hot_enough(p.wbl_streak[pg], hot_need)",
                   "wb_defer::on(env, link_d2h_gbps, line)",
                   "wb_defer::page_may_wait(known, mixed, read, flushing, age, quiet)",
                   "wb_defer::word_in_share(w, period, phase)",
                   # the write-back loop puts pages off only for a pool with its shadow, after TICK-015's filter
                   "wbl_put_off(*p, runs);",
                   "if (wb_lazy_on()) lazy_filter(*p, runs);",
                   # the sync flushes a page put off that the CPU wrote, ahead of its copies
                   "wbl_sync_flush(p, wbl_sync_pages);",
                   "wbl_note_read(guest, size);",
                   "wbl_faults_drain();",
                   "void memory_wb_texture(uint64_t gpu, uint64_t size)",
                   "int64_t memory_wbl_settle(uint64_t guest, uint64_t size)",
                   "memory_wbl_settle_try(wb_defer::region_base(g, region), region);",
                   'getenv("RX_VK_WB_LAZY_SETTLE")',
                   "int64_t memory_wbl_settle(uint64_t guest, uint64_t size)",
                   'getenv("RX_VK_WB_LAZY_SETTLE")',
                   'getenv("RX_VK_WB_LAZY_REGION_KB")',
                   'getenv("RX_VK_WB_LAZY_PROTECT")',
                   # a mirror with pages waiting is not given back; a pool's shadow going orphans them
                   "!p.wbl_pending_n",
                   "wbl_orphan_pool(p);",
                   "wbl_orphans_record(cmd);",
                   "dead_mirrors.back().value = kWblHold;",
                   "void memory_wb_flush_queue()",
                   "bool memory_wb_pending()"):
        if needle not in mem:
            raise SystemExit("vk_memory.cpp no longer has: " + needle)
    # the order in the write-back loop: TICK-015's filter, then the put off
    if mem.index("if (wb_lazy_on()) lazy_filter(*p, runs);") > mem.index("wbl_put_off(*p, runs);"):
        raise SystemExit("vk_memory.cpp: the put off must come after TICK-015's filter")
    tex = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    if "memory_wbl_settle(gpu, n);" not in tex:
        raise SystemExit("vk_texture.cpp guest_bytes no longer settles")
    tex = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    if "memory_wbl_settle(gpu, n);" not in tex:
        raise SystemExit("vk_texture.cpp guest_bytes no longer settles")
    rep = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    for needle in ("const bool wb_flush = memory_wb_pending();",
                   "if (wb_flush && !memory_wb_flush_own()) memory_wb_flush_queue();"):
        if needle not in rep:
            raise SystemExit("vk_replay.cpp no longer has: " + needle)
    # the flush joins the present's recording before its write-back
    if rep.index("memory_wb_flush_queue();") > rep.index("memory_end_submission(r.cmd);\n        full_barrier(r.cmd);\n        {   // (DECOUPLE3) its own value, never below a signal already sent"):
        raise SystemExit("vk_replay.cpp: the flush must be queued before the present's write-back")
    hdr = (ROOT / "gpu/vk/vk_internal.h").read_text(encoding="utf-8")
    for needle in ("bool memory_wb_pending();", "void memory_wb_flush_queue();", "bool memory_wb_flush_own();"):
        if needle not in hdr:
            raise SystemExit("vk_internal.h no longer has: " + needle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="wb-defer-") as tmp:
        exe = Path(tmp) / "wb-defer.exe"
        subprocess.run([args.cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests/test_wb_defer.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
