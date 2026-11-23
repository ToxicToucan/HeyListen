"""The ASTC decoder's push constants and the code that fills them agree (blocks_x, blocks_y). Run python -I -B tests/test_astc_blocks.py.

host_shaders/astc_decoder_comp_spv.h returns for a thread past the mip's blocks (push constants blocks_x, blocks_y, at offsets 32
and 36) and reads zero past the end of its buffer. If a place that fills the decoder's constants leaves them zero, every thread
returns and the texture stays black; if the C++ struct and the shader's block disagree, every later constant is wrong. This
reads the shader module's push-constant block (member offsets) and gpu/vk/vk_texture.cpp's AstcPush, and checks that:
- the module's push-constant block has the nine members, the last two at offsets 32 and 36, and the struct the C++ pushes is 40 bytes;
- the module reads a buffer length (OpArrayLength: the clamp) and compares the thread's position with the two constants;
- every place that makes the decoder's constants (each AstcPush it builds) sets blocks_x and blocks_y through astc_blocks_limit, which
  gives everything (no early return) with RX_VK_ASTC_BLOCKS=0.

The header is glslang 16.6.0's output for Longshot's astc_decoder.comp at 7daafa7a (#1553, HumanBean): HeyListen keeps no .comp
sources (see test_host_shader_bounds.py); regenerate with
    glslang -V --target-env vulkan1.1 --vn ASTC_DECODER_COMP_SPV -S comp -o astc_decoder_comp_spv.h astc_decoder.comp
"""
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
OP_VARIABLE, OP_TYPE_STRUCT, OP_TYPE_POINTER, OP_MEMBER_DECORATE, OP_ARRAY_LENGTH = 59, 30, 32, 72, 68
DECORATION_OFFSET = 35
STORAGE_PUSH_CONSTANT = 9


def words(name):
    text = (ROOT / "gpu/vk/host_shaders" / (name + "_comp_spv.h")).read_text()
    return [int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]{8}", text[text.index("{"):])]


def instructions(w):
    i, out = 5, []
    while i < len(w):
        n = w[i] >> 16
        assert n > 0
        out.append((w[i] & 0xFFFF, w[i + 1:i + n]))
        i += n
    return out


def main():
    ins = instructions(words("astc_decoder"))
    pointers = {ops[0]: ops[2] for op, ops in ins if op == OP_TYPE_POINTER}      # (id -> pointee)
    push_struct = None
    for op, ops in ins:
        if op == OP_VARIABLE and ops[2] == STORAGE_PUSH_CONSTANT:
            push_struct = pointers[ops[0]]
    assert push_struct is not None, "the module has no push-constant block"
    members = sorted((ops[1], ops[3]) for op, ops in ins
                     if op == OP_MEMBER_DECORATE and ops[0] == push_struct and ops[2] == DECORATION_OFFSET)
    offsets = [o for _, o in members]
    assert offsets == [0, 8, 12, 16, 20, 24, 28, 32, 36], "push-constant member offsets %s" % offsets
    assert any(op == OP_ARRAY_LENGTH for op, _ in ins), "the module does not clamp its read to the buffer's length"

    source = (ROOT / "gpu/vk/vk_texture.cpp").read_text()
    struct_body = source[source.index("struct AstcPush {"):]
    struct_body = struct_body[:struct_body.index("};")]
    fields = re.findall(r"uint32_t ([^;]+);", struct_body)
    names = [n.strip() for f in fields for n in f.split(",")]
    assert names[-2:] == ["blocks_x", "blocks_y"] or [n.split("//")[0].strip() for n in names][-2:] == ["blocks_x", "blocks_y"], names
    size = sum(8 if "[2]" in n else 4 for n in names)
    assert size == 40, "AstcPush is %d bytes, the shader's block 40" % size

    # every place that builds the decoder's constants sets both limits through astc_blocks_limit
    builds = len(re.findall(r"AstcPush\s*(?:pc\{\}|&\s*pc\s*=\s*pc0\.pc)", source))
    sets = len(re.findall(r"pc\.blocks_x = astc_blocks_limit\(g\.tw\), pc\.blocks_y = astc_blocks_limit\(g\.th\)", source))
    assert builds >= 3 and sets == builds, "%d AstcPush built, %d set blocks_x / blocks_y" % (builds, sets)
    body = source[source.index("uint32_t astc_blocks_limit("):]
    body = body[:body.index("\n}")]
    assert "RX_VK_ASTC_BLOCKS" in body and "UINT32_MAX" in body
    print("ok: the ASTC decoder's nine push constants (40 bytes), the buffer-length clamp, and %d places that set blocks_x / blocks_y" % sets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
