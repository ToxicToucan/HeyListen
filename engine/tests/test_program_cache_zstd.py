# Copyright (C) 2026 Sacrappy

"""The program cache keeps its records packed with zstd, and still reads the old raw ones (gpu/vk/vk_shader.cpp).

hookshot_programs.bin was 6.5 GB of records, 58% of them zero bytes. A record is now stored as its kind word with kKindZstd set, then
one zstd frame of the whole record (level 1), unless that does not make it smaller. The old file stays readable as it is, record by
record, and disk_compact packs it once, through a temporary file that takes the file's place only when it is whole. This builds
record_pack, record_unpack, disk_read_record and disk_compact as they are in the source, with the real zstd of third_party/zstd,
and checks: a packed record comes back byte for byte, a raw one (an old file's, and one zstd cannot shrink) too, the first bytes of
a packed record come back without the rest, a record cut short or with a bad frame is refused, and a file of raw records packed by
disk_compact reads back the same, smaller, with a stale temporary file replaced and a damaged file left as it was.
python -I -B tests/test_program_cache_zstd.py [--cxx g++]
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHADER = ROOT / "gpu" / "vk" / "vk_shader.cpp"
ZSTD = ROOT / "third_party" / "zstd" / "lib"
SOURCES = ["common/debug.c", "common/entropy_common.c", "common/error_private.c", "common/fse_decompress.c", "common/xxhash.c",
           "common/zstd_common.c", "decompress/huf_decompress.c", "decompress/zstd_ddict.c", "decompress/zstd_decompress.c",
           "decompress/zstd_decompress_block.c", "compress/fse_compress.c", "compress/hist.c", "compress/huf_compress.c",
           "compress/zstd_compress.c", "compress/zstd_compress_literals.c", "compress/zstd_compress_sequences.c",
           "compress/zstd_compress_superblock.c", "compress/zstd_double_fast.c", "compress/zstd_fast.c", "compress/zstd_lazy.c",
           "compress/zstd_ldm.c", "compress/zstd_opt.c", "compress/zstd_preSplit.c"]


def block(text, marker, end="}"):
    start = text.index(marker)
    at = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[at] == "{") - (text[at] == "}")
        at += 1
    return text[start:at] + (";" if end == "};" else "")


def line(text, marker):
    start = text.index(marker)
    return text[start:text.index("\n", start)]


CPP = r"""
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>
#ifdef _WIN32
#include <io.h>
#include <windows.h>
#else
#include <unistd.h>
#define _fseeki64 fseeko
#define _ftelli64 ftello
#endif
#include "zstd.h"
static void logf(const char*, ...) {}
struct DiskRecord { int64_t at; size_t size; uint32_t kind; };
constexpr uint32_t kDiskMagic = 0x31505348;
PARTS
static int fails = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

// (a record like the game's: a kind word, then words, a good part of them zero, some SPIR-V-like noise)
static std::vector<uint8_t> make(uint32_t kind, size_t n, uint32_t seed, bool noise) {
    std::vector<uint8_t> v(n, 0);
    std::memcpy(v.data(), &kind, 4);
    uint32_t x = seed * 2654435761u + 1;
    for (size_t i = 4; i < n; i++) {
        x = x * 1664525u + 1013904223u;
        if (noise ? true : (x >> 28) < 2) v[i] = (uint8_t)(x >> 16);
    }
    return v;
}
static void put_record(FILE* f, uint64_t key, const std::vector<uint8_t>& st) {
    const uint64_t head[2] = {key, st.size()};
    fwrite(head, 8, 2, f);
    fwrite(st.data(), 1, st.size(), f);
}
static std::vector<uint8_t> slurp(const std::string& path) {
    std::vector<uint8_t> v;
    if (FILE* f = fopen(path.c_str(), "rb")) {
        uint8_t b[65536];
        for (size_t n; (n = fread(b, 1, sizeof b, f)) > 0;) v.insert(v.end(), b, b + n);
        fclose(f);
    }
    return v;
}
int main(int argc, char** argv) {
    const std::string dir = argv[1];
    {   // a packed record comes back whole, a raw one (zstd cannot shrink noise) is kept as it is
        auto rec = make(1, 150000, 1, false);
        std::vector<uint8_t> st, back;
        record_pack(rec.data(), rec.size(), st);
        uint32_t kind;
        std::memcpy(&kind, st.data(), 4);
        CHECK((kind & kKindZstd) && (kind & 0xff) == 1 && st.size() < rec.size() / 2);
        CHECK(record_unpack(st.data(), st.size(), back, 0) && back == rec);
        // (the first bytes only: the answers come first)
        CHECK(record_unpack(st.data(), st.size(), back, 4096) && back.size() == 4096 && !std::memcmp(back.data(), rec.data(), 4096));
        CHECK(record_unpack(st.data(), st.size(), back, 1 << 30) && back == rec);
        CHECK(record_unpack(st.data(), st.size(), back, 0) && back == rec);   // (the thread's context is still good after a cut-off read)
        auto noise = make(2, 5000, 2, true);
        record_pack(noise.data(), noise.size(), st);
        std::memcpy(&kind, st.data(), 4);
        CHECK(!(kind & kKindZstd) && st == noise);
        CHECK(record_unpack(st.data(), st.size(), back, 0) && back == noise);
        CHECK(record_unpack(st.data(), st.size(), back, 100) && back.size() == 100 && !std::memcmp(back.data(), noise.data(), 100));
        auto tiny = make(4, 4, 3, false);   // (a record of its kind word alone)
        record_pack(tiny.data(), tiny.size(), st);
        CHECK(st == tiny && record_unpack(st.data(), st.size(), back, 0) && back == tiny);
    }
    {   // damaged: cut short, a flipped byte (the frame's checksum), a frame of another record's kind, nothing
        auto rec = make(1, 150000, 4, false);
        std::vector<uint8_t> st, back;
        record_pack(rec.data(), rec.size(), st);
        std::vector<uint8_t> cut(st.begin(), st.begin() + st.size() / 2);
        CHECK(!record_unpack(cut.data(), cut.size(), back, 0));
        auto flip = st;
        flip[flip.size() / 2] ^= 0x10;
        CHECK(!record_unpack(flip.data(), flip.size(), back, 0));
        auto other = st;
        other[0] ^= 2;   // (the kind word says 3, the frame holds a 1)
        CHECK(!record_unpack(other.data(), other.size(), back, 0));
        CHECK(!record_unpack(st.data(), 3, back, 0));
        CHECK(!record_unpack(st.data(), 4, back, 0));
        CHECK(record_unpack(st.data(), st.size(), back, 0) && back == rec);   // (and the next read is fine)
    }
    const std::string path = dir + "/hookshot_programs.bin";
    // an old file: a head, then raw records (65 of 600 KB, past kCompactMin), some of them noise, one already packed
    std::vector<std::pair<uint64_t, std::vector<uint8_t>>> recs;
    std::vector<std::pair<uint64_t, DiskRecord>> order;
    {
        FILE* f = fopen(path.c_str(), "wb");
        const uint32_t h[2] = {kDiskMagic, 0};
        const uint64_t stamp = 0x1234567890abcdefull;
        fwrite(h, 4, 2, f);
        fwrite(&stamp, 8, 1, f);
        int64_t at = 16;
        for (int i = 0; i < 65; i++) {
            auto rec = make(i % 3 ? 0 : 1, 600000, 10 + i, i % 11 == 5);
            std::vector<uint8_t> st = rec;
            if (i == 7) record_pack(rec.data(), rec.size(), st);   // (a file a newer build wrote to)
            uint32_t kind;
            std::memcpy(&kind, st.data(), 4);
            put_record(f, (uint64_t)(1000 + i), st);
            order.push_back({(uint64_t)(1000 + i), {at + 16, st.size(), kind}});
            at += 16 + (int64_t)st.size();
            recs.push_back({(uint64_t)(1000 + i), rec});
        }
        fclose(f);
    }
    const std::vector<uint8_t> old_file = slurp(path);
    {   // each record reads back from the old file as it is
        FILE* in = fopen(path.c_str(), "rb");
        std::mutex mu;
        for (size_t i = 0; i < recs.size(); i++) {
            std::vector<uint8_t> got;
            CHECK(disk_read_record(in, &mu, recs[i].first, order[i].second, got) && got == recs[i].second);
        }
        std::vector<uint8_t> got;
        CHECK(!disk_read_record(in, &mu, 99999, order[0].second, got));   // (not the key the index said)
        DiskRecord wrong = order[1].second;
        wrong.size += 1;
        CHECK(!disk_read_record(in, &mu, recs[1].first, wrong, got));
        fclose(in);
    }
    int64_t good = (int64_t)old_file.size();
    {   // a damaged file is left as it was: a record whose head is not the one named
        auto bad_order = order;
        bad_order[30].second.size += 8;
        auto o = bad_order;
        int64_t g = good;
        CHECK(!disk_compact(path, dir + "/hookshot_programs.idx", 0x1234567890abcdefull, o, g));
        CHECK(slurp(path) == old_file && o[30].second.size == bad_order[30].second.size && g == good);
        CHECK(slurp(path + ".new").empty());
    }
    {   // packed: smaller, the same records, a stale temporary file out of the way
        if (FILE* f = fopen((path + ".new").c_str(), "wb")) fputs("stale", f), fclose(f);
        if (FILE* f = fopen((dir + "/hookshot_programs.idx").c_str(), "wb")) fputs("old index", f), fclose(f);
        const size_t before = old_file.size();
        CHECK(disk_compact(path, dir + "/hookshot_programs.idx", 0x1234567890abcdefull, order, good));
        const std::vector<uint8_t> now = slurp(path);
        CHECK(now.size() == (size_t)good && now.size() < before / 2);
        CHECK(slurp(path + ".new").empty());                       // (the temporary file is gone: it took the file's place)
        CHECK(slurp(dir + "/hookshot_programs.idx").empty());      // (the old index went first)
        uint32_t h[2];
        uint64_t stamp;
        std::memcpy(h, now.data(), 8), std::memcpy(&stamp, now.data() + 8, 8);
        CHECK(h[0] == kDiskMagic && stamp == 0x1234567890abcdefull);
        FILE* in = fopen(path.c_str(), "rb");
        std::mutex mu;
        size_t packed = 0;
        for (size_t i = 0; i < recs.size(); i++) {
            std::vector<uint8_t> got;
            CHECK(order[i].first == recs[i].first);
            CHECK(disk_read_record(in, &mu, recs[i].first, order[i].second, got) && got == recs[i].second);
            packed += (order[i].second.kind & kKindZstd) != 0;
        }
        CHECK(packed == 59);   // (the noise stays raw, the packed one stays packed)
        fclose(in);
        // (and nothing more to do: a second go is not worth it)
        auto o = order;
        int64_t g = good;
        CHECK(!disk_compact(path, dir + "/hookshot_programs.idx", 0x1234567890abcdefull, o, g));
    }
    if (!fails) std::puts("ok");
    return fails != 0;
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cxx", default=None)
    a = ap.parse_args()
    cxx = shutil.which(a.cxx) if a.cxx else (shutil.which("g++") or shutil.which("clang++"))
    if not cxx:
        print("SKIP: no C++ compiler")
        return
    name = Path(cxx).name
    cc = shutil.which(str(Path(cxx).with_name(name.replace("clang++", "clang").replace("g++", "gcc"))))
    if not cc:
        print("SKIP: no C compiler beside " + cxx)
        return
    src = SHADER.read_text(encoding="utf-8")
    parts = [
        line(src, "constexpr uint32_t kKindZstd"),
        line(src, "constexpr uint32_t kCompactMin"),
        block(src, "bool pack_on("),
        block(src, "struct ZstdThread {", "};"),
        block(src, "ZstdThread& zstd_thread("),
        line(src, "std::atomic<int64_t> st_pack_raw"),
        block(src, "void record_pack("),
        block(src, "bool record_unpack("),
        block(src, "bool disk_read_record("),
        block(src, "bool disk_compact("),
    ]
    with tempfile.TemporaryDirectory(prefix="program-zstd-") as tmp:
        tmp = Path(tmp)
        objs = []
        flags = ["-O2", "-DZSTD_DISABLE_ASM", "-DZSTD_LEGACY_SUPPORT=0", "-w", "-I" + str(ZSTD),
                 "-I" + str(ZSTD / "common")]
        procs = []
        for i, f in enumerate(SOURCES):
            obj = tmp / ("z%d.o" % i)
            objs.append(str(obj))
            procs.append(subprocess.Popen([cc, "-c", str(ZSTD / f), "-o", str(obj)] + flags, stderr=subprocess.PIPE, text=True))
        for p in procs:
            err = p.communicate()[1]
            if p.returncode:
                raise SystemExit("zstd did not build:\n" + err)
        cpp = tmp / "fixture.cpp"
        cpp.write_text(CPP.replace("PARTS", "\n".join(parts)), encoding="utf-8")
        exe = tmp / "fixture.exe"
        build = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wno-unused-function", "-I" + str(ZSTD), str(cpp), "-o", str(exe)] + objs
                               + (["-pthread"] if sys.platform != "win32" else []), capture_output=True, text=True)
        if build.returncode:
            raise SystemExit("the program cache's record code did not build:\n" + build.stderr)
        work = tmp / "cache"
        work.mkdir()
        got = subprocess.run([str(exe), str(work)], capture_output=True, text=True)
        if got.returncode or got.stdout.strip().splitlines()[-1:] != ["ok"]:
            raise SystemExit("the program cache's records:\n" + got.stdout + got.stderr)
    text = src
    for marker, want in (("void program_cache_save(", "record_pack(o.data(), o.size(), stored)"),
                         ("void program_cache_read(", "disk_read_record(in, mu, key, d, data)"),
                         ("void program_cache_each(", "disk_read_record(disk_in, &disk_mu, key, d, data)"),
                         ("SeedVariants seed_compute(", "disk_read_record(disk_in, &disk_mu, cs->key, d, data"),
                         ("bool program_variant_kept(", "disk_read_record(disk_in, &disk_mu, key, d, data"),
                         ("void disk_init(", "disk_compact(path, idx_path, stamp, order, good)")):
        if want not in block(text, marker):
            raise SystemExit("%s does not use %s" % (marker.rstrip("("), want))
    # (no reader takes a record's bytes from the file by hand any more: a packed one would be taken for a raw one)
    for marker in ("void program_cache_read(", "void program_cache_each(", "SeedVariants seed_compute(", "bool program_variant_kept("):
        if "fread(" in block(text, marker):
            raise SystemExit("%s reads the file itself, not through disk_read_record" % marker.rstrip("("))
    print("PASS: records packed and raw read back the same; an old file is packed once, or left as it was")


if __name__ == "__main__":
    main()
