// Copyright (C) 2026 Sacrappy
// TEXCACHE: the rules of the BC disk cache's packs (gpu/vk/vk_bc_disk.h), pure: the file and record headers, the scan that
// rebuilds the index and cuts a torn tail, the encoder version check, the two-pack eviction with hits copied forward, and the
// read-ahead's order. No file, no thread, no Vulkan here: tests/test_texcache_pack.py compiles and runs them with no game and no GPU.
//
// Two packs. Records are appended to the current one in the order the textures were made (a zone is a run of records, which is
// what the read-ahead follows). Once the current pack holds half the cap, the other (older) one is deleted and the appends go to a
// new one there. A record found in the older pack and used again is appended to the current one ("copied forward"), so what a
// session keeps using survives the older pack's deletion: only what was not used for a whole half of the cap goes.
// A pack whose header names another encoder version (a changed encoder shader or key layout) is not read: it is deleted and
// begun again. Every record carries a hash of its blocks, checked on every read.
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <string>
#include <unordered_map>
#include <vector>

namespace texcache {

constexpr uint32_t kFormat = 2;                 // (1: perf/bc7c's packs, "HLBC7C"; never read here)
constexpr char kMagic[8] = {'H', 'L', 'T', 'X', 'C', 'P', 0, 2};
constexpr uint32_t kRecMagic = 0x58435448;      // "HTCX"
constexpr uint32_t kMaxRecord = 1u << 30;       // (a size past this is a torn or foreign record)

struct FileHeader {
    char magic[8];
    uint32_t format, pad;
    uint64_t encoder;
    uint64_t seq;
};
struct RecHeader {
    uint32_t magic, size;
    uint64_t key, hash;
};
static_assert(sizeof(FileHeader) == 32 && sizeof(RecHeader) == 24, "the packs' layout");

inline uint64_t hash_blocks(const uint8_t* p, uint64_t n) {
    uint64_t h = 0x9E3779B97F4A7C15ull ^ n;
    uint64_t i = 0;
    for (; i + 8 <= n; i += 8) {
        uint64_t v;
        std::memcpy(&v, p + i, 8);
        h = (h ^ v) * 0x100000001b3ull;
        h ^= h >> 29;
    }
    for (; i < n; i++) h = (h ^ p[i]) * 0x100000001b3ull;
    h ^= h >> 32;
    return h * 0xff51afd7ed558ccdull;
}

inline FileHeader make_header(uint64_t encoder, uint64_t seq) {
    FileHeader h{};
    std::memcpy(h.magic, kMagic, 8);
    h.format = kFormat, h.encoder = encoder, h.seq = seq;
    return h;
}
/// A pack written by this format and this encoder version (anything else is deleted, not read)
inline bool header_ok(const FileHeader& h, uint64_t encoder) {
    return std::memcmp(h.magic, kMagic, 8) == 0 && h.format == kFormat && h.encoder == encoder;
}
inline RecHeader make_record(uint64_t key, const uint8_t* blocks, uint32_t size) {
    return RecHeader{kRecMagic, size, key, hash_blocks(blocks, size)};
}

struct Rec {
    uint64_t key;
    uint64_t at;     // the blocks' offset in the pack
    uint32_t size;
    uint64_t hash;
};
/// The records of a pack of `end` bytes whose header was checked: read(at, dst, n) reads n bytes at `at`. Returns the end of the
/// last whole record (what the file is to be cut to: a torn tail, a session killed mid-write, goes)
template <class Read>
uint64_t scan_records(Read&& read, uint64_t end, std::vector<Rec>& out) {
    uint64_t at = sizeof(FileHeader);
    for (;;) {
        RecHeader r{};
        if (at + sizeof r > end || !read(at, &r, sizeof r)) break;
        if (r.magic != kRecMagic || r.size == 0 || r.size > kMaxRecord || at + sizeof r + r.size > end) break;
        out.push_back(Rec{r.key, at + sizeof r, r.size, r.hash});
        at += sizeof r + r.size;
    }
    return at;
}

struct Loc {
    uint8_t gen;      // 0 / 1: the pack
    uint64_t at;      // the blocks' offset in it
    uint32_t size;
    uint64_t hash;
    uint32_t order;   // its place in `order` (the reading order: creation order, older pack first)
};

/// The index of both packs and the eviction rules (the caller does the file work the answers ask for)
struct Index {
    std::unordered_map<uint64_t, Loc> map;
    std::vector<uint64_t> order;   // keys by their records' order; a slot whose key moved on (copied forward) is stale
    uint64_t bytes[2] = {0, 0};    // each pack's size, header included (0: none)
    uint64_t seq[2] = {0, 0};
    int cur = 0;                   // the pack appended to
    uint64_t cap = 0;              // both packs

    /// After the scan: the packs found valid (valid[g]) with their records and sequence numbers; the newer one is appended to
    void load(const bool valid[2], const uint64_t seqs[2], const uint64_t ends[2], const std::vector<Rec> recs[2]) {
        map.clear(), order.clear();
        int first = 0;   // (the older pack's records first)
        if (valid[0] && valid[1]) first = seqs[0] < seqs[1] ? 0 : 1;
        else if (valid[1]) first = 1;
        cur = valid[0] && valid[1] ? 1 - first : valid[0] ? 0 : valid[1] ? 1 : 0;
        for (int g = 0; g < 2; g++) seq[g] = valid[g] ? seqs[g] : 0, bytes[g] = valid[g] ? ends[g] : 0;
        if (!valid[cur]) seq[cur] = valid[1 - cur] ? seq[1 - cur] + 1 : 1;
        for (int k = 0; k < 2; k++) {
            const int g = k == 0 ? first : 1 - first;
            if (!valid[g]) continue;
            for (const Rec& r : recs[g]) put(r.key, Loc{(uint8_t)g, r.at, r.size, r.hash, 0});
        }
    }
    const Loc* find(uint64_t key) const {
        auto it = map.find(key);
        return it == map.end() ? nullptr : &it->second;
    }
    /// A record of this key is worth appending: none yet, or only in the older pack (copied forward)
    bool wants(uint64_t key) const {
        const Loc* l = find(key);
        return !l || l->gen != cur;
    }
    /// The current pack is full: the older one is to be deleted and the appends go there (rotate())
    bool rotate_due(uint32_t size) const {
        return bytes[cur] > sizeof(FileHeader) && bytes[cur] + sizeof(RecHeader) + size > cap / 2;
    }
    /// The older pack's records dropped (its file deleted by the caller); the appends go to a new pack in its place
    void rotate() {
        const int o = 1 - cur;
        for (auto it = map.begin(); it != map.end();) {
            if (it->second.gen == o) it = map.erase(it);
            else ++it;
        }
        std::vector<uint64_t> keep;
        keep.reserve(map.size());
        for (size_t i = 0; i < order.size(); i++) {
            auto it = map.find(order[i]);
            if (it != map.end() && it->second.order == i) it->second.order = (uint32_t)keep.size(), keep.push_back(order[i]);
        }
        order.swap(keep);
        seq[o] = seq[cur] + 1;
        bytes[o] = 0;
        cur = o;
    }
    /// Where the next record goes (rotate first when rotate_due): its header's offset; the pack's header first when it is new
    uint64_t append_at() const { return bytes[cur] ? bytes[cur] : sizeof(FileHeader); }
    /// A record appended at append_at() to the current pack
    void appended(uint64_t key, uint32_t size, uint64_t hash) {
        const uint64_t at = append_at();
        put(key, Loc{(uint8_t)cur, at + sizeof(RecHeader), size, hash, 0});
        bytes[cur] = at + sizeof(RecHeader) + size;
    }
    /// The records to read ahead from `from` on, at most n of them, not stale (each key once, in its creation order)
    std::vector<uint64_t> ahead(uint32_t from, uint32_t n) const {
        std::vector<uint64_t> v;
        for (uint32_t i = from; i < order.size() && v.size() < n; i++) {
            const Loc* l = find(order[i]);
            if (l && l->order == i) v.push_back(order[i]);
        }
        return v;
    }
    uint64_t total() const { return bytes[0] + bytes[1]; }

   private:
    void put(uint64_t key, Loc l) {
        l.order = (uint32_t)order.size();
        order.push_back(key);
        map[key] = l;   // (a key copied forward: its newer record wins, its older order slot goes stale)
    }
};

/// The read-ahead's runs: a record used at `order` wants the n order slots after it read; a run already asked for that reaches
/// half of them far enough asks nothing, one that falls short is extended from its end (each slot asked once while a zone loads
/// in order), and a record elsewhere (another zone) starts a new run
struct AheadPlan {
    uint32_t lo = 0, hi = 0;   // the run asked for last: [lo, hi)
    bool next(uint32_t order, uint32_t n, uint32_t& from, uint32_t& count) {
        const uint32_t want_lo = order + 1, want_hi = order + 1 + n;
        if (hi > lo && want_lo >= lo && want_lo <= hi) {
            if (hi >= want_lo + n / 2) return false;
            from = hi, count = want_hi - hi, hi = want_hi;
            return true;
        }
        lo = want_lo, hi = want_hi, from = want_lo, count = n;
        return true;
    }
    void reset() { lo = hi = 0; }
};

/// The maps' lists: for each map (the host's "map:" name), the cache keys its textures were first wanted with there, in that order.
/// A map change reads the new map's list first (vk_bc_disk.h), in place of the creation order of the session that wrote the records
/// (which served 23-30% of the on-disk textures on the main PC's tour). Kept in a file of its own beside the packs.
struct MapLists {
    static constexpr uint32_t kMaxMaps = 256, kMaxKeys = 16384;   // (bounds: the file stays small, a list of a zone fits)
    struct List {
        std::string name;
        std::vector<uint64_t> keys;
        uint64_t used = 0;   // (when it was last entered: the least recently entered map goes past kMaxMaps)
    };
    std::vector<List> maps;
    uint64_t clock = 0;

    int find(const std::string& name) const {
        for (size_t i = 0; i < maps.size(); i++)
            if (maps[i].name == name) return (int)i;
        return -1;
    }
    /// The map entered: its list (made when new; the least recently entered one goes past the bound)
    int enter(const std::string& name) {
        int i = find(name);
        if (i < 0) {
            if (maps.size() >= kMaxMaps) {
                size_t old = 0;
                for (size_t k = 1; k < maps.size(); k++)
                    if (maps[k].used < maps[old].used) old = k;
                maps.erase(maps.begin() + (ptrdiff_t)old);
            }
            maps.push_back(List{name, {}, 0});
            i = (int)maps.size() - 1;
        }
        maps[(size_t)i].used = ++clock;
        return i;
    }
    /// key wanted on map i: appended when that list does not have it yet (true)
    bool note(int i, uint64_t key) {
        if (i < 0 || (size_t)i >= maps.size()) return false;
        std::vector<uint64_t>& k = maps[(size_t)i].keys;
        for (uint64_t x : k)
            if (x == key) return false;
        if (k.size() >= kMaxKeys) return false;
        k.push_back(key);
        return true;
    }
    /// keys appended to map `from` just before the map change was seen (the name is polled every 500 ms, after the zone's first
    /// textures): moved to the end of map `to`'s list, in their order
    void move(int from, int to, const std::vector<uint64_t>& keys) {
        if (from < 0 || to < 0 || from == to || (size_t)from >= maps.size() || (size_t)to >= maps.size()) return;
        std::vector<uint64_t>& f = maps[(size_t)from].keys;
        for (uint64_t key : keys) {
            for (size_t j = f.size(); j-- > 0;)
                if (f[j] == key) {
                    f.erase(f.begin() + (ptrdiff_t)j);
                    break;
                }
            note(to, key);
        }
    }
    /// The file: "HLTXMAPS", the encoder version, then per map: name length, name, key count, keys
    std::vector<uint8_t> save(uint64_t encoder) const {
        std::vector<uint8_t> b;
        auto put = [&](const void* p, size_t n) { b.insert(b.end(), (const uint8_t*)p, (const uint8_t*)p + n); };
        put("HLTXMAPS", 8);
        put(&encoder, 8);
        const uint32_t n = (uint32_t)maps.size();
        put(&n, 4);
        for (const List& l : maps) {
            const uint32_t len = (uint32_t)l.name.size(), keys = (uint32_t)l.keys.size();
            put(&len, 4), put(l.name.data(), len), put(&keys, 4), put(l.keys.data(), keys * 8ull);
        }
        return b;
    }
    /// false (and empty) when the file is not this format's or encoder's, or is cut short
    bool load(const uint8_t* p, size_t n, uint64_t encoder) {
        maps.clear(), clock = 0;
        size_t at = 0;
        auto get = [&](void* d, size_t k) {
            if (at + k > n) return false;
            std::memcpy(d, p + at, k), at += k;
            return true;
        };
        char magic[8];
        uint64_t enc = 0;
        uint32_t count = 0;
        if (!get(magic, 8) || std::memcmp(magic, "HLTXMAPS", 8) || !get(&enc, 8) || enc != encoder || !get(&count, 4) || count > kMaxMaps)
            return false;
        for (uint32_t i = 0; i < count; i++) {
            uint32_t len = 0, keys = 0;
            List l;
            if (!get(&len, 4) || len > 4096) return maps.clear(), false;
            l.name.resize(len);
            if (!get(l.name.data(), len) || !get(&keys, 4) || keys > kMaxKeys) return maps.clear(), false;
            l.keys.resize(keys);
            if (!get(l.keys.data(), keys * 8ull)) return maps.clear(), false;
            l.used = ++clock;
            maps.push_back(std::move(l));
        }
        return true;
    }
};

/// Who gets the disk cache when RX_VK_BC_DISK is not set (the owner's decision, 24 Nov, from the measures in
/// docs/research/texcache-disk.md): integrated GPUs that transcode ASTC on the GPU (a Vega 8 laptop: +30% and more while the camera
/// turns), and discrete cards with less than 10 GB of VRAM (an RTX 3050 8 GB: +2 to +6%, every warm run above every off one). Not
/// cards with 10 GB and more (an RTX 5070 12 GB: no gain, the transcode is about 1 ms of GPU a frame there), not the chips that
/// decode ASTC on the CPU (they make no BC blocks to keep). Returns the reason for the log line too
inline bool on_by_default(bool integrated, bool astc_on_gpu, uint64_t vram_bytes, const char** why) {
    const char* w;
    bool on;
    if (!astc_on_gpu) on = false, w = "ASTC is decoded on the CPU here: no BC blocks to keep";
    else if (integrated) on = true, w = "an integrated GPU that transcodes ASTC";
    else if (vram_bytes && vram_bytes < (10ull << 30)) on = true, w = "a discrete GPU with less than 10 GB of VRAM";
    else on = false, w = "a discrete GPU with 10 GB of VRAM or more: nothing to gain";
    if (why) *why = w;
    return on;
}
/// The read-ahead pool by the machine's RAM: 1024 MB with 24 GB or more, 512 with 14 GB (the 16 GB PCs: on the 15 GB laptop 512
/// served 87% of the on-disk textures and 1024 89%, the turning fps within the runs' noise, and the game's working set is already
/// 8.4-11.8 GB), 256 below
inline uint32_t pool_mb_for_ram(uint64_t total_bytes) {
    return total_bytes >= (24ull << 30) ? 1024 : total_bytes >= (14ull << 30) ? 512 : 256;
}
/// A record of `size` bytes may be written while the disk keeps at least `floor` free after it (never the last GB of a disk)
inline bool room_to_write(uint64_t available, uint64_t size, uint64_t floor = 1ull << 30) {
    return available >= floor && available - floor >= size;
}

/// The encoder version a pack is written for: the shaders' hash (the caller folds the SPIR-V) with the key layout's revision
/// and the format; a change of any of them starts empty packs
constexpr uint32_t kKeyRev = 1;   // (bumped by hand when the key's layout or the blocks' meaning changes without the shaders)
inline uint64_t encoder_version(uint64_t shaders_hash) {
    return shaders_hash ^ ((uint64_t)kKeyRev << 48) ^ ((uint64_t)kFormat << 56);
}

}  // namespace texcache
