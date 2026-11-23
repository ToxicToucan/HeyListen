# Copyright (C) 2026 Sacrappy

"""The texture host shaders never store past the edge of their image (gpu/vk/host_shaders/*_comp_spv.h).

A block of the ASTC decoder at the right or bottom edge of an image whose size is not a multiple of the block size reaches past
it, and the block-linear unswizzle's 32x32 workgroups round the dispatch up past the level. A store outside the image is
undefined (robustImageAccess2 is off): NVIDIA drops it, AMD lets it land in other memory (black blocks, and at last a GPU
fault on an RX 6750 XT). Both shaders now test the texel against the image's size first.

The checked-in headers hold the SPIR-V only (the .comp sources are Eglantine's, glslang 16.6.0, --target-env vulkan1.1).
This reads each header's words and checks the module's shape: the ASTC decoder has ONE OpImageWrite (its StoreTexel; the
three direct stores are gone) in a function that opens a selection before the store, and the unswizzle queries the
image's size (OpImageQuerySize) and branches on it before its one store. With spirv-val (--spirv-val, or on PATH) both
modules must also validate.

    python -I -B tests/test_host_shader_bounds.py [--spirv-val PATH]

Ported from Hookshot 6e11217e1 (#2442) by hckrman101, the bounds-checked stores half (Fritchii f52631cf6, from CheezerWheezer's
Lichii); the check is HeyListen's.
"""
import argparse
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "gpu/vk/host_shaders"

OP_FUNCTION, OP_FUNCTION_END = 54, 56
OP_IMAGE_WRITE, OP_IMAGE_QUERY_SIZE = 99, 104
OP_SELECTION_MERGE, OP_BRANCH_CONDITIONAL = 247, 250


def words(name):
    text = (DIR / (name + "_comp_spv.h")).read_text()
    return [int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]{8}", text[text.index("{"):])]


def instructions(w):
    """(opcode, index of its first word) of every instruction after the five header words"""
    i, out = 5, []
    while i < len(w):
        n = w[i] >> 16
        assert n > 0, "a zero-length instruction at word %d" % i
        out.append((w[i] & 0xFFFF, i))
        i += n
    assert i == len(w), "the last instruction runs past the module"
    return out


def check(name, want_queries):
    w = words(name)
    assert w[0] == 0x07230203, "%s: not SPIR-V" % name
    ins = instructions(w)
    writes = [i for op, i in ins if op == OP_IMAGE_WRITE]
    assert len(writes) == 1, "%s: %d OpImageWrite, want 1 (every store goes through the bounds test)" % (name, len(writes))
    # the function holding the store: from its OpFunction to the store
    start = max(i for op, i in ins if op == OP_FUNCTION and i < writes[0])
    between = [op for op, i in ins if start < i < writes[0]]
    assert OP_SELECTION_MERGE in between and OP_BRANCH_CONDITIONAL in between, \
        "%s: the store is not behind a condition in its function" % name
    queries = [i for op, i in ins if op == OP_IMAGE_QUERY_SIZE]
    assert len(queries) == want_queries, "%s: %d OpImageQuerySize, want %d" % (name, len(queries), want_queries)
    return w


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--spirv-val", default=None)
    a = p.parse_args()
    val = a.spirv_val or shutil.which("spirv-val")

    mods = {"astc_decoder": check("astc_decoder", 1), "block_linear_unswizzle_2d": check("block_linear_unswizzle_2d", 1)}
    # (the unswizzle's query comes before its store: the texel's coordinates are tested against the size)
    w = mods["block_linear_unswizzle_2d"]
    ins = instructions(w)
    q = [i for op, i in ins if op == OP_IMAGE_QUERY_SIZE][0]
    s = [i for op, i in ins if op == OP_IMAGE_WRITE][0]
    assert q < s, "the unswizzle stores before it asks the image's size"

    if val:
        for name, w in mods.items():
            with tempfile.NamedTemporaryFile(suffix=".spv", delete=False) as f:
                f.write(struct.pack("<%dI" % len(w), *w))
            r = subprocess.run([val, "--target-env", "vulkan1.1", f.name], capture_output=True, text=True)
            Path(f.name).unlink()
            assert r.returncode == 0, "%s fails spirv-val: %s" % (name, (r.stdout + r.stderr).strip())
    else:
        print("note: spirv-val not found, the modules' shape only")
    print("ok: the ASTC decoder and the block-linear unswizzle store only inside their image")
    return 0


if __name__ == "__main__":
    sys.exit(main())
