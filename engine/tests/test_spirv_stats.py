# Copyright (C) 2026 Sacrappy

"""scripts/spirv_stats.py counts a module's global-memory helper, its constant bit-field chains and reads the GPU pass timing.

A module is made by hand (no game data): a main function that calls a helper (an u64 address, a storage buffer access, an
ArrayLength and a branch: the shape of the translator's global-memory search) and holds one BitFieldUExtract and one IEqual of
constants. The counts are checked, then a program cache with the module's record is scanned, and a log block with the
per-program lines of RX_VK_PASS_TIMING=2 is read.

python -I -B tests/test_spirv_stats.py
"""
import importlib.util
import os
import struct
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("spirv_stats", ROOT / "scripts" / "spirv_stats.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def op(code, *args):
    return [(len(args) + 1) << 16 | code, *args]


def module():
    """(SPIR-V bytes, the helper's instruction count). Ids: 2 u32, 3 u64, 4 bool, 13 the storage buffer, 20 main, 30 the helper"""
    w = [0x07230203, 0x00010600, 0, 40, 0]
    w += op(17, 1)                                     # OpCapability Shader
    w += op(19, 1) + op(21, 2, 32, 0) + op(21, 3, 64, 0) + op(20, 4)
    w += op(33, 5, 2, 3) + op(33, 6, 1)                # u32(u64), void()
    w += op(43, 2, 7, 0) + op(43, 2, 8, 1) + op(43, 2, 9, 15) + op(43, 3, 15, 5, 0)
    w += op(29, 10, 2) + op(30, 11, 10) + op(32, 12, 12, 11) + op(59, 12, 13, 12) + op(32, 14, 12, 2)
    w += op(54, 1, 20, 0, 6) + op(248, 21)             # main
    w += op(203, 2, 22, 9, 7, 8)                       # BitFieldUExtract of constants
    w += op(170, 4, 23, 22, 7)                         # IEqual of that and a constant
    w += op(57, 2, 24, 30, 15)                         # the call
    w += op(253) + op(56)
    helper = []
    helper += op(54, 2, 30, 1, 5) + op(55, 3, 31) + op(248, 32)
    helper += op(65, 14, 33, 13, 7, 7) + op(61, 2, 34, 33)
    helper += op(68, 2, 35, 13, 0)                      # ArrayLength
    helper += op(170, 4, 36, 34, 7)
    helper += op(247, 38, 0) + op(250, 36, 37, 38)
    helper += op(248, 37) + op(254, 34) + op(248, 38) + op(254, 7) + op(56)
    n_helper = 0
    i = 0
    while i < len(helper):
        i += helper[i] >> 16
        n_helper += 1
    w += helper
    return struct.pack("<%dI" % len(w), *w), n_helper


LOG = """[hos Presentation Thread] vk: GPU pass timing, 600 presents, per frame (each timestamp waits ...):
[hos Presentation Thread] vk:   22.31 ms a frame in all (recordings' command buffers + upload command buffers)
[hos Presentation Thread] vk:   the 25 spans that took the most (ms a frame, spans a frame, us a span, items a frame, MB a frame):
[hos Presentation Thread] vk:   m   0.423 ms     1.0 x    422.8 us       1.0 items    0.00 MB  dispatch  prog 9dc98871eb7fc101 mma grid 64x36x1
[hos Presentation Thread] vk:   the 20 programs whose runs of draws took the most (RX_VK_PASS_TIMING=2: a timestamp at each change of program):
[hos Presentation Thread] vk:     2.406 ms     1.0 runs   2405.9 us a run       1.0 draws  2405.94 us a draw  prog 43ec86f35fd937b9
[hos Presentation Thread] vk:   the late waits' GPU tail (busy with no gap up to the value a capture waited for): 16.60 ms a wait
[hos Presentation Thread] vk:     tail m   0.419 ms     1.0 x  dispatch  prog 9dc98871eb7fc101 mma grid 64x36x1
"""


def main():
    spirv, n_helper = module()
    r = ss.analyse(spirv)
    assert r["funcs"] == 2, r["funcs"]
    assert r["gm_helpers"] == 1 and r["gm_call_sites"] == 1, (r["gm_helpers"], r["gm_call_sites"])
    assert r["gm_body_instr"] == n_helper and r["gm_effective_instr"] == n_helper, r["gm_body_instr"]
    assert r["gm_max_candidates"] == 1 and r["gm_load_call_sites"] == 1, r
    assert r["bfue"] == 1 and r["bfue_const_base"] == 1 and r["ieq"] == 2, (r["bfue"], r["bfue_const_base"], r["ieq"])
    assert r["const_foldable"] == 2, r["const_foldable"]   # (the extract, and the compare of its result)
    assert r["ssbo_vars"] == 1 and r["direct_ssbo_accesses"] == 0, (r["ssbo_vars"], r["direct_ssbo_accesses"])
    assert r["cas"] == 0 and r["loops"] == 0 and r["f16_ops"] == 0
    assert ss.op_histogram(spirv)[203] == 1

    with tempfile.TemporaryDirectory() as tmp:
        data = struct.pack("<I", 0) + bytes(12)
        data += struct.pack("<iI", 7, 0x20) + struct.pack("<Q", len(spirv) // 4) + spirv + bytes(48)
        cache = os.path.join(tmp, "hookshot_programs.bin")
        with open(cache, "wb") as f:
            f.write(struct.pack("<IIQ", ss.CACHE_MAGIC, 0, 1))
            f.write(struct.pack("<QQ", 0x43EC86F35FD937B9, len(data)) + data)
        out = os.path.join(tmp, "out")
        assert ss.main(["scan", cache, "--out", out, "--jobs", "1"]) in (0, None)
        rows = ss.read_stages(out)
        assert len(rows) == 1 and rows[0]["key"] == "43ec86f35fd937b9" and rows[0]["stage"] == "compute", rows
        log = os.path.join(tmp, "game_log.txt")
        Path(log).write_text(LOG, encoding="utf-8")
        draws, disp, total = ss.read_log(log)
        assert draws == {"43ec86f35fd937b9": 2.406} and disp == {"9dc98871eb7fc101": 0.423} and total == 22.31, (draws, disp, total)
    print("spirv_stats: helper, constant chains, cache scan and pass timing ok")


if __name__ == "__main__":
    main()
