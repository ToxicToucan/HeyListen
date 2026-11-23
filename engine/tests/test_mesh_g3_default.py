# Copyright (C) 2026 Sacrappy

"""RX_VK_MESH_G3 (the storage buffers' ranges worked out once in a task or mesh program's first block) is on by default.

It is off only for RX_VK_MESH_G3=0 (or any value atoi reads as 0). It is not tied to a GPU vendor: the change is in the
SPIR-V, not in a driver's path, so there is no device flag to add to disk_stamp(). The name stays under RX_VK_MESH, which
disk_stamp() hashes with the environment, so RX_VK_MESH_G3=0 has a program cache of its own.
python -I -B tests/test_mesh_g3_default.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "gpu/vk/eden/shader_recompiler/backend/spirv/spirv_emit_context.cpp"
SHADER = ROOT / "gpu/vk/vk_shader.cpp"


def need(cond, why):
    if not cond:
        raise SystemExit("FAIL: " + why)


def main():
    text = CONTEXT.read_text(encoding="utf-8")
    at = text.index("static const bool g3_hoist{")
    decl = text[at:text.index("};", at) + 2]
    need('getenv("RX_VK_MESH_G3") == nullptr' in decl, "unset does not turn it on")
    need('atoi(getenv("RX_VK_MESH_G3")) != 0' in decl, "a value of 0 does not turn it off")
    need("!= nullptr &&" not in decl, "the old off-by-default test is still there")
    need("vendorID" not in decl and "mesh_g3" not in decl, "a vendor gate appeared without a stamp change")
    need("stage == Stage::Task || stage == Stage::Mesh" in text[at:at + 1600], "it is not limited to task and mesh stages")
    stamp = SHADER.read_text(encoding="utf-8")
    need('"RX_VK_MESH"' in stamp, "RX_VK_MESH* names are no longer hashed into the program cache stamp")
    print("ok")


if __name__ == "__main__":
    main()
