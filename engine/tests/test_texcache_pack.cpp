// Copyright (C) 2026 Sacrappy
// texcache_pack.h (gpu/vk): the BC disk cache's pack format, scan, stale encoder versions, two-pack eviction with copies forward,
// and the read-ahead's runs. tests/test_texcache_pack.py
#include "../gpu/vk/texcache_pack.h"

#include <cstdio>
#include <string>
#include <vector>

static int failures = 0;
static void expect(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        failures++;
    }
}

// A pack in memory: header + records, as vk_bc_disk.h writes them
struct Pack {
    std::vector<uint8_t> b;
    explicit Pack(uint64_t encoder, uint64_t seq) {
        const texcache::FileHeader h = texcache::make_header(encoder, seq);
        put(&h, sizeof h);
    }
    void put(const void* p, size_t n) { b.insert(b.end(), (const uint8_t*)p, (const uint8_t*)p + n); }
    void rec(uint64_t key, uint32_t size, uint8_t fill) {
        std::vector<uint8_t> blocks(size, fill);
        const texcache::RecHeader r = texcache::make_record(key, blocks.data(), size);
        put(&r, sizeof r);
        put(blocks.data(), size);
    }
    uint64_t scan(std::vector<texcache::Rec>& out) const {
        return texcache::scan_records(
            [&](uint64_t at, void* dst, uint64_t n) {
                if (at + n > b.size()) return false;
                std::memcpy(dst, b.data() + at, n);
                return true;
            },
            b.size(), out);
    }
};

int main() {
    using namespace texcache;
    const uint64_t enc = encoder_version(0x1234);

    // the header: this format and this encoder only
    {
        FileHeader h = make_header(enc, 7);
        expect(header_ok(h, enc), "a pack of this encoder is read");
        expect(!header_ok(h, encoder_version(0x1235)), "a changed shader: the pack is not read (stale encoder)");
        expect(encoder_version(0x1234) != (0x1234ull ^ 0), "the key revision and format are folded into the version");
        h.format = 1;
        expect(!header_ok(h, enc), "perf/bc7c's format 1 is not read");
        FileHeader g = make_header(enc, 7);
        g.magic[0] = 'X';
        expect(!header_ok(g, enc), "a foreign file is not read");
    }
    // the scan: whole records, a torn tail cut, a hash per record
    {
        Pack p(enc, 1);
        p.rec(11, 64, 1);
        p.rec(12, 128, 2);
        const uint64_t whole = p.b.size();
        p.rec(13, 256, 3);
        p.b.resize(p.b.size() - 100);   // (killed mid-write)
        std::vector<Rec> recs;
        const uint64_t keep = p.scan(recs);
        expect(recs.size() == 2 && recs[0].key == 11 && recs[1].key == 12, "the torn record is left out");
        expect(keep == whole, "the file is cut after the last whole record");
        expect(recs[1].at == sizeof(FileHeader) + sizeof(RecHeader) + 64 + sizeof(RecHeader), "a record's blocks offset");
        std::vector<uint8_t> blk(128, 2);
        expect(hash_blocks(blk.data(), 128) == recs[1].hash, "the record's hash is its blocks'");
        blk[5] ^= 1;
        expect(hash_blocks(blk.data(), 128) != recs[1].hash, "a flipped byte fails the hash");
        Pack q(enc, 1);
        RecHeader bad{0xdeadbeef, 16, 1, 0};
        q.put(&bad, sizeof bad);
        std::vector<Rec> none;
        expect(q.scan(none) == sizeof(FileHeader) && none.empty(), "a bad record magic ends the scan");
        Pack z(enc, 1);
        RecHeader huge{kRecMagic, kMaxRecord + 1, 1, 0};
        z.put(&huge, sizeof huge);
        std::vector<Rec> none2;
        expect(z.scan(none2) == sizeof(FileHeader) && none2.empty(), "an impossible size ends the scan");
    }
    // the index: load both packs, the newer appended to, a key in both resolves to the newer
    {
        Pack a(enc, 4), b(enc, 5);
        a.rec(1, 100, 1), a.rec(2, 100, 2), a.rec(3, 100, 3);
        b.rec(2, 100, 2), b.rec(4, 100, 4);
        std::vector<Rec> recs[2];
        const uint64_t ends[2] = {a.scan(recs[0]), b.scan(recs[1])};
        const bool valid[2] = {true, true};
        const uint64_t seqs[2] = {4, 5};
        Index ix;
        ix.cap = 1 << 20;
        ix.load(valid, seqs, ends, recs);
        expect(ix.cur == 1, "the newer pack (higher sequence) is appended to");
        expect(ix.find(2) && ix.find(2)->gen == 1, "a key copied forward resolves to the newer record");
        expect(ix.find(1) && ix.find(1)->gen == 0, "an older pack's record is found");
        expect(ix.wants(1) && !ix.wants(4) && !ix.wants(2) && ix.wants(9), "wants: absent or only in the older pack");
        const std::vector<uint64_t> run = ix.ahead(0, 10);
        expect(run.size() == 4 && run[0] == 1 && run[1] == 3 && run[2] == 2 && run[3] == 4,
               "read ahead in creation order, older pack first, a stale slot skipped");
        // one pack only, the other stale: appends go to a new pack with the next sequence
        const bool one[2] = {true, false};
        std::vector<Rec> r1[2] = {recs[0], {}};
        const uint64_t e1[2] = {ends[0], 0};
        Index j;
        j.cap = 1 << 20;
        j.load(one, seqs, e1, r1);
        expect(j.cur == 0 && j.append_at() == ends[0], "the only valid pack is appended to, at its end");
        const bool nothing[2] = {false, false};
        Index k;
        k.cap = 1 << 20;
        k.load(nothing, seqs, e1, r1);
        expect(k.map.empty() && k.append_at() == sizeof(FileHeader) && k.seq[k.cur] == 1, "no pack: a new one, header first");
    }
    // eviction: two packs, the older one goes when the current one holds half the cap; what was used is copied forward
    {
        Index ix;
        ix.cap = 2 * (sizeof(FileHeader) + 4 * (sizeof(RecHeader) + 1000));   // (4 records a pack)
        const bool none[2] = {false, false};
        const uint64_t z[2] = {0, 0};
        std::vector<Rec> nr[2];
        ix.load(none, z, z, nr);
        auto add = [&](uint64_t key) {
            if (!ix.wants(key)) return false;
            if (ix.rotate_due(1000)) ix.rotate();
            ix.appended(key, 1000, key * 7);
            return true;
        };
        for (uint64_t key = 1; key <= 4; key++) add(key);
        expect(ix.cur == 0 && ix.map.size() == 4, "four records fill half the cap");
        expect(ix.rotate_due(1000), "a fifth one is due a rotation");
        add(5);
        expect(ix.cur == 1 && ix.find(5)->gen == 1 && ix.find(1)->gen == 0, "rotated: appends go to the other pack");
        // key 2 used again in this session: copied forward (wants it, appended to the current pack)
        expect(ix.wants(2), "a record in the older pack is wanted again (copy forward)");
        add(2);
        expect(ix.find(2)->gen == 1, "copied forward");
        add(6), add(7);
        add(8);   // (the current pack full again: the older one, with 1, 3, 4, goes)
        expect(!ix.find(1) && !ix.find(3) && !ix.find(4), "the older pack's records not used again went");
        expect(ix.find(2) && ix.find(5) && ix.find(6) && ix.find(7) && ix.find(8), "the copied forward one survived the rotation");
        expect(ix.total() <= ix.cap, "both packs within the cap");
        const std::vector<uint64_t> run = ix.ahead(0, 10);
        expect(run.size() == 5 && run[0] == 5 && run[1] == 2 && run[4] == 8, "the order renumbered after the rotation");
        for (const auto& kv : ix.map) expect(ix.order[kv.second.order] == kv.first, "every key's order slot names it");
        // a working set within half the cap never thrashes: used every session, nothing re-written
        int first = 0, later = 0;
        for (uint64_t key : {2ull, 5ull, 8ull}) first += add(key) ? 1 : 0;
        for (int session = 1; session < 5; session++)
            for (uint64_t key : {2ull, 5ull, 8ull}) later += add(key) ? 1 : 0;
        expect(first == 2, "a working set's records in the older pack are copied forward once");
        expect(later == 0, "then never written again (no thrash within half the cap)");
    }
    // the read-ahead's runs
    {
        AheadPlan ap;
        uint32_t from = 0, count = 0;
        expect(ap.next(10, 8, from, count) && from == 11 && count == 8, "a first hit asks for the 8 records after it");
        expect(!ap.next(11, 8, from, count), "the next record of the run asks nothing (the run reaches far enough)");
        expect(!ap.next(14, 8, from, count), "nor in the middle of it");
        expect(ap.next(16, 8, from, count) && from == 19 && count == 6, "near its end: the run is extended from its end");
        expect(ap.next(500, 8, from, count) && from == 501 && count == 8, "a record elsewhere (another zone) starts a new run");
        ap.reset();
        expect(ap.next(0, 4, from, count) && from == 1 && count == 4, "after a reset (rotation) a new run");
    }
    // the maps' lists: first-use order per map, the late name's keys moved, the file round trip, a stale encoder, bounds
    {
        MapLists ml;
        const int market = ml.enter("Market_Child (child)");
        expect(ml.note(market, 1) && ml.note(market, 2) && !ml.note(market, 1), "a key listed once per map, in first-use order");
        ml.note(market, 3), ml.note(market, 4);
        const int field = ml.enter("HyruleField_Child (child)");
        ml.move(market, field, {3, 4});   // (wanted in the 3 s before the field's name came)
        expect(ml.maps[(size_t)market].keys == std::vector<uint64_t>({1, 2}), "the late keys left the old map");
        expect(ml.maps[(size_t)field].keys == std::vector<uint64_t>({3, 4}), "and lead the new map's list, in order");
        expect(ml.note(field, 1), "a key shared by two maps is on both lists");
        expect(ml.enter("Market_Child (child)") == market, "a map entered again keeps its list");
        const std::vector<uint8_t> b = ml.save(enc);
        MapLists back;
        expect(back.load(b.data(), b.size(), enc) && back.maps.size() == 2 && back.maps[1].keys == ml.maps[1].keys,
               "the file round trip");
        expect(!back.load(b.data(), b.size(), encoder_version(0x99)) && back.maps.empty(), "another encoder's file is not read");
        expect(!back.load(b.data(), b.size() - 3, enc) && back.maps.empty(), "a cut file is not read");
        MapLists many;
        for (uint32_t i = 0; i < MapLists::kMaxMaps + 5; i++) many.enter("m" + std::to_string(i));
        many.enter("m10");   // (entered again: the most recent)
        many.enter("new");
        expect(many.maps.size() == MapLists::kMaxMaps && many.find("m10") >= 0 && many.find("new") >= 0,
               "past the bound the least recently entered map goes");
        int kept = 0;
        for (uint32_t i = 0; i < MapLists::kMaxKeys + 10; i++) kept += many.note(0, i + 1) ? 1 : 0;
        expect(kept == (int)MapLists::kMaxKeys, "a list holds at most kMaxKeys");
    }
    // who gets it by default, the pool by RAM, the disk's last GB
    {
        const char* why = nullptr;
        const uint64_t GB = 1ull << 30;
        expect(on_by_default(true, true, 512ull << 20, &why) && why, "an integrated GPU that transcodes ASTC (the Vega 8 laptop): on");
        expect(!on_by_default(true, false, 512ull << 20, &why), "an integrated GPU that decodes ASTC on the CPU (890M): off, no BC blocks");
        expect(on_by_default(false, true, 8 * GB, &why), "a discrete 8 GB card (RTX 3050): on");
        expect(on_by_default(false, true, 10 * GB - 1, &why), "just under 10 GB: on");
        expect(!on_by_default(false, true, 10 * GB, &why), "10 GB: off");
        expect(!on_by_default(false, true, 12 * GB, &why), "a discrete 12 GB card (RTX 5070): off");
        expect(!on_by_default(false, true, 0, &why), "VRAM not known: off");
        expect(pool_mb_for_ram(32 * GB) == 1024 && pool_mb_for_ram(24 * GB) == 1024, "24 GB of RAM and more: 1024 MB");
        expect(pool_mb_for_ram(16 * GB) == 512 && pool_mb_for_ram(15 * GB) == 512, "the 16 GB PCs and the 15 GB laptop: 512 MB");
        expect(pool_mb_for_ram(8 * GB) == 256, "8 GB: 256 MB");
        expect(room_to_write(5 * GB, 100 << 20), "5 GB free: a record is written");
        expect(!room_to_write(GB + (50 << 20), 100 << 20), "it would eat into the last GB: not written");
        expect(!room_to_write(GB / 2, 1), "under 1 GB free: nothing written");
        expect(room_to_write(GB + 200, 200) && !room_to_write(GB + 199, 200), "the floor is exact");
    }
    if (failures) return 1;
    std::printf("texcache_pack: ok\n");
    return 0;
}
