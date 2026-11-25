# Copyright (C) 2026 Sacrappy
"""Compile and run the BC disk cache's pack rules (gpu/vk/texcache_pack.h: format, scan, stale encoder, eviction with copies
forward, read-ahead runs; no game/GPU), and check that the renderer keeps the rules that make it safe: the render thread never
reads the disk, the writer's backlog is bounded, the encoder hash is worked out once, and nothing packages the cache files."""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def block(src, head):
    i = src.index(head)
    j = src.index("\n}\n", i)
    return src[i:j]


def check_source():
    tex = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    disk = (ROOT / "gpu/vk/vk_bc_disk.h").read_text(encoding="utf-8")
    need = [
        (tex, '#include "vk_bc_disk.h"'),
        (tex, "bcdisk::start(bcd_encoder(), game_dir_path())"),
        (tex, "static const uint64_t v = [] {"),             # (bcd_encoder: the SPIR-V folded once)
        (tex, "bcdisk::wants(disk_key, (uint32_t)entry_bytes)"),
        (tex, "if (to_disk && !entry.buf) bcdisk::unreserve(disk_key, (uint32_t)entry_bytes);"),
        (tex, "if (it->last_value > completed || (it->pin && it->pin->load() > 0)) { ++it; continue; }"),
        (disk, "s.write_pending.load(std::memory_order_relaxed) + size > (int64_t)s.write_cap"),
        (disk, "THREAD_MODE_BACKGROUND_BEGIN"),
        (disk, "texcache::header_ok(h, s.encoder)"),
        (disk, "texcache::room_to_write(sp.available"),       # (never the disk's last GB)
        (disk, "s.write_off_why = \"its folder could not be written\";"),   # (a read-only folder: writes off, reads on)
        (tex, "texcache::on_by_default(integrated, astc_ready() && bc_ready(), vram, &why)"),
        (tex, "env >= 0 ? env == 1 :"),                        # (RX_VK_BC_DISK forces it either way)
    ]
    for src, needle in need:
        if needle not in src:
            raise SystemExit("missing: " + needle)
    # the render thread's side reads no file: the upload path takes from the pool or asks for a read ahead
    up = block(tex, "bool upload_astc_gpu(Texture& tex, uint64_t total, const Ranges* dirty) {")
    for bad in ("read_blocks", "ifstream", "bcd_load"):
        if bad in up:
            raise SystemExit("upload_astc_gpu reads the disk (" + bad + ")")
    for fn in ("inline bool probe(", "inline void took(", "inline bool wants(", "inline void set_map(", "inline void use("):
        body = block(disk, fn)
        if "read_blocks" in body or "fstream" in body or "s.wr." in body:
            raise SystemExit(fn + " touches a file on the render thread")
    # the files are runtime-made from the player's game: no packaging script names them
    # (the release takes the build's publish folder, never the game folder the packs live in; nor may it name them)
    rel = [ROOT / "scripts/release_parts.py"] + sorted((ROOT.parent / "tools/release").glob("*.py"))
    for p in rel:
        if p.is_file() and "hookshot_texcache_" in p.read_text(encoding="utf-8", errors="ignore"):
            raise SystemExit(f"{p} names the BC disk cache's files: they must never be packaged")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="texcache-pack-") as tmp:
        exe = Path(tmp) / "texcache-pack"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests/test_texcache_pack.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
