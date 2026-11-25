# Copyright (C) 2026 Sacrappy
"""The no-mesh fallback's indexed form (RX_VK_FALLBACK_INDEXED; no game/GPU): the constants' layout, the translator's choice of
the form, the host's fills, buffer usage and indexed draw, the program cache's flag and stamp."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu/vk"


def text(rel):
    return (VK / rel).read_text(encoding="utf-8")


def need(rel, needles):
    src = text(rel)
    for n in needles:
        if n not in src:
            raise SystemExit(f"{rel} no longer has: {n}")
    return src


def constants():
    src = text("eden/shader_recompiler/backend/spirv/emit_spirv.h")
    v = {}
    for name, expr in re.findall(r"constexpr u32 (MESH_FALLBACK_\w+) = ([^;]+);", src):
        e = expr.replace("u", "")
        for k, val in sorted(v.items(), key=lambda kv: -len(kv[0])):
            e = e.replace(k, str(val))
        try:
            v[name] = eval(e, {"__builtins__": {}})
        except NameError:   # (the push offsets: built on other constants, not needed here)
            pass
    return v


def main():
    c = constants()
    assert c["MESH_FALLBACK_HEADER_WORDS"] == 8 and c["MESH_FALLBACK_DISPATCH_WORD"] == 5
    base = c["MESH_FALLBACK_INDEX_BASE"]
    assert base == c["MESH_FALLBACK_HEADER_WORDS"] + c["MESH_FALLBACK_RECORD_WORDS"] - c["MESH_FALLBACK_INDEX_WORDS"], "index region at the end"
    assert c["MESH_FALLBACK_INDEXED_DRAW_WORD"] == base, "the indexed command opens the region"
    assert c["MESH_FALLBACK_INDEX_ALLOC_WORD"] == base + 5, "its allocator right after the 5-word command"
    assert c["MESH_FALLBACK_INDEX_FIRST_WORD"] == base + 8, "the indices after the command and allocator"
    assert (c["MESH_FALLBACK_INDEX_FIRST_WORD"] * 4) % 4 == 0, "index buffer offset a multiple of 4"
    assert c["MESH_FALLBACK_INDEX_WORDS"] < c["MESH_FALLBACK_RECORD_WORDS"] // 2, "vertex records keep most of the area"

    mesh = need("eden/shader_recompiler/backend/spirv/emit_spirv_mesh.cpp", [
        "void MeshFallbackEpilogueIndexed(EmitContext& ctx) {",
        "if (ctx.runtime_info.mesh_fallback_indexed) MeshFallbackEpilogueIndexed(ctx);",
        "const u32 vertex_fit{(MESH_FALLBACK_RECORD_WORDS - MESH_FALLBACK_INDEX_WORDS) / words};",
        "atomic_add(MESH_FALLBACK_INDEXED_DRAW_WORD, indices);",
        "ctx.OpIAdd(ctx.U32[1], vbase, vertex));"])
    body = mesh[mesh.index("void MeshFallbackEpilogueIndexed"):]
    body = body[:body.index("\nvoid StorePosition")]
    # (the per-primitive lines are never written by the indexed form: the host only picks it without them)
    assert "primitive_lines" not in body, "the indexed form must not write per-primitive lines"
    need("eden/shader_recompiler/runtime_info.h", ["bool mesh_fallback_indexed{};"])
    need("vk_shader.cpp", [
        "fallback && fallback_indexed_on() && mesh_t->program.mesh.primitive_lines.empty() &&",
        "mesh_t->program.mesh.Corners() == 3;",
        "mrt.mesh_fallback_indexed = fb_indexed;", "p.fallback_indexed = fb_indexed;",
        "(p.fallback_indexed ? 16 : 0)", "p.fallback_indexed = (kind & 16) != 0;",
        'const char* e = getenv("RX_VK_FALLBACK_INDEXED");',
        "if (dev.mesh_fallback && fallback_indexed_on()) {"])
    need("vk_draw.cpp", [
        "VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT | VK_BUFFER_USAGE_INDEX_BUFFER_BIT",
        "if (p.fallback_indexed) {", "fallback_draw_indexed = p.fallback_indexed;",
        "bind_index(cmd, fallback_draw_records, (VkDeviceSize)Shader::Backend::SPIRV::MESH_FALLBACK_INDEX_FIRST_WORD * 4,",
        "vkCmdDrawIndexedIndirect(cmd, fallback_draw_records,"])
    print("fallback indexed: all checks passed")


if __name__ == "__main__":
    main()
