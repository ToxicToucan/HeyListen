// HL_RB_LAG (STEP1, docs/native-engine/readback-ring.md): one deterministic mechanism for the three GPU-result classes the game's CPU reads
// (R1 the exposure meter, R2 the 0x40 slots, R3 the 0x2000 pair). The ranges the CPU reads are learned from its reads (invalidate); a
// program's binding written to such a range (gpu_buffer_bind, in the mirror) is not written back to guest memory any more: the end of
// the submission copies the range's bytes into an entry of an engine-owned ring, tagged with the frame count, and the game's next read
// of it applies to guest memory the newest unapplied entry that is `lag` frames old, without a wait for the GPU (rb_ring.h has the rules).
// HL_RB_LAG_CHECK=1: the old path runs unchanged and what the ring would have applied is compared with what the game read.
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <map>
#include <mutex>
#include <vector>

#include "vk_internal.h"
#include "rb_ring.h"

namespace mkwvk {
extern int64_t presents_now;   // (vk_replay.cpp)
bool rbring_wait_value(uint64_t v, double ms);   // (vk_replay.cpp)
namespace {

// HL_RB_LAG=<L> (0 or unset: off): the ranges the CPU reads back (the exposure meter's block, the 0x40 slots, the 0x2000 blocks) are staged into an engine-owned ring when the GPU has written them, and a read is answered with the newest result L frames old (1..6; 2 is the default lag to use) with no wait for the GPU and no write-back of those ranges; the same values run to run. HL_RB_LAG_CHECK=1: the old path runs and the ring is compared with what it read (per-class counts in the log). HL_RB_LAG_WAIT_MS=<ms> (200): how long a read waits for an entry the GPU has not finished. HL_RB_LAG_LOG=1: one log row a frame with the hash of the values served.
const rb_ring::Config& config() {
    static const rb_ring::Config c = [] {
        rb_ring::Config k = rb_ring::parse(getenv("HL_RB_LAG"), getenv("HL_RB_LAG_CHECK"), getenv("HL_RB_LAG_WAIT_MS"));
        if (!k.lag && k.check) k.lag = 2;   // (a check alone compares the lag 2 ring)
        // (FIF1) with the CPU k-1 frames ahead of the replay (RX_VK_FRAMES_IN_FLIGHT=k) an entry is staged k-1 frames later: lag 2 + (k-1)
        // keeps every read free of a wait for the replay; HL_RB_LAG says otherwise
        const char* fk = getenv("RX_VK_FRAMES_IN_FLIGHT");
        if (k.lag && !(getenv("HL_RB_LAG") && *getenv("HL_RB_LAG")) && fk && atoi(fk) > 1)
            k.lag = std::min<unsigned>(rb_ring::kMaxLag, 2u + (unsigned)std::min(atoi(fk), 3) - 1u);
        return k;
    }();
    return c;
}
bool frame_log_on() {
    static const bool on = getenv("HL_RB_LAG_LOG") && atoi(getenv("HL_RB_LAG_LOG")) != 0;
    return on;
}

struct Addr {
    int cls = 0;
    bool stageable = false;   // a binding written in the mirror was staged: reads are answered by the ring
    bool tainted = false;     // a written binding that is not exactly this range overlaps it (its write-back would land in it): the old path
    uint64_t writes = 0;      // written bindings of exactly this range, and how many of them were staged (the rest went to guest memory directly)
    uint64_t staged = 0;
    uint64_t first_stage = 0;   // the writes counted when the first one was staged: those before it wrote guest memory and have landed long since
    uint64_t reads = 0;       // the CPU's reads of it so far
    rb_ring::Entry ents[rb_ring::kMaxEntries];
    unsigned n = 0;
    uint64_t applied = 0;   // the newest seq handed to the game
};
struct Req {
    uint64_t guest, size;
    VkBuffer buffer;
    VkDeviceSize offset;
};

std::mutex mu;
std::map<std::pair<uint64_t, uint64_t>, Addr> table;   // (guest, size)
std::vector<Req> pending;
uint64_t pending_bytes = 0;
VkBuffer ring_buffer = VK_NULL_HANDLE;
uint8_t* ring_map = nullptr;
bool ring_failed = false;
uint64_t arena_at = 0, arena_total = 0, next_seq = 0;

struct Stats {
    std::atomic<int64_t> reads{0}, applied{0}, none{0}, waited{0}, late{0};
    std::atomic<int64_t> chk_new{0}, chk_equal{0}, chk_differ{0}, chk_zero{0}, chk_nonzero{0}, chk_notdone{0}, chk_old{0};
    std::atomic<int64_t> age[4] = {};
    std::atomic<int64_t> got[5] = {};   // (serve mode) the age in frames of the entries handed out: 1, 2, 3, 4, more
} stats[4];
std::atomic<int64_t> st_staged{0}, st_staged_bytes{0}, st_direct{0};
std::atomic<uint64_t> learned_lo{~0ull}, learned_hi{0};   // (the span of the learned ranges: a written binding outside it is not looked up)
constexpr unsigned kQuietReads = 8;   // reads of a range no program writes before it is answered without a wait (the write-backs of before are long done)
// A range is served by the ring when its writes are exactly its staged ones, nothing else's write-back lands in it, and either a staged entry
// exists or no program has written it at all (a result slot nothing wrote: nothing to wait for)
bool served(const Addr& a) {
    if (a.tainted || a.writes - a.first_stage != a.staged) return false;
    return a.stageable || (a.writes == 0 && a.reads >= kQuietReads);
}

bool ring_init() {   // (mu held)
    if (ring_map) return true;
    if (ring_failed) return false;
    ring_failed = true;
    VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bi.size = rb_ring::kArena;
    bi.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    VkBuffer buffer{};
    if (vkCreateBuffer(dev.device, &bi, nullptr, &buffer) != VK_SUCCESS) return false;
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(dev.device, buffer, &req);
    const VkMemoryPropertyFlags want = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    uint32_t type = UINT32_MAX;
    for (int pass = 0; pass < 2 && type == UINT32_MAX; pass++)   // (system memory first: the host reads it every frame)
        for (uint32_t i = 0; i < dev.memory.memoryTypeCount && type == UINT32_MAX; i++) {
            const VkMemoryPropertyFlags f = dev.memory.memoryTypes[i].propertyFlags;
            if ((req.memoryTypeBits >> i & 1) && (f & want) == want && (pass || !(f & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT))) type = i;
        }
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = type;
    VkDeviceMemory memory{};
    void* words = nullptr;
    if (type == UINT32_MAX || vkAllocateMemory(dev.device, &ai, nullptr, &memory) != VK_SUCCESS ||
        vkBindBufferMemory(dev.device, buffer, memory, 0) != VK_SUCCESS ||
        vkMapMemory(dev.device, memory, 0, rb_ring::kArena, 0, &words) != VK_SUCCESS) {
        logf("vk: HL_RB_LAG: no host memory for the readback ring: the old path stays");
        vkDestroyBuffer(dev.device, buffer, nullptr);
        return false;
    }
    std::memset(words, 0, rb_ring::kArena);
    ring_buffer = buffer;
    ring_map = (uint8_t*)words;
    ring_failed = false;
    logf("vk: HL_RB_LAG=%u%s: the exposure meter, the 0x40 slots and the 0x2000 blocks the CPU reads come from a ring of %u KB, %u frames old, "
         "with no wait for the GPU (docs/native-engine/readback-ring.md)", config().lag, config().check ? " (check: the old path runs)" : "",
         (unsigned)(rb_ring::kArena >> 10), config().lag);
    return true;
}

// ---- the served-values hash, one row a frame (HL_RB_LAG_LOG=1) ----
std::mutex hmu;
struct FrameHash { uint64_t sum = 0, by[4] = {}; uint32_t reads = 0; };
std::map<int64_t, FrameHash> frame_hash;   // frame -> sum of the reads' hashes (and by class), reads
uint64_t run_hash = 0;
void hash_add(int64_t frame, int cls, uint64_t h) {
    if (!frame_log_on()) return;
    std::lock_guard lk(hmu);
    auto& e = frame_hash[frame];
    e.sum += h, e.by[cls] += h, e.reads++;
    for (auto it = frame_hash.begin(); it != frame_hash.end() && it->first < frame - 3;) {
        run_hash = (run_hash ^ it->second.sum) * 1099511628211ull + (uint64_t)it->first;
        logf("vk: rbring frame %lld hash %016llx reads %u run %016llx r1 %016llx r2 %016llx r3 %016llx", (long long)it->first,
             (unsigned long long)it->second.sum, it->second.reads, (unsigned long long)run_hash, (unsigned long long)it->second.by[1],
             (unsigned long long)it->second.by[2], (unsigned long long)it->second.by[3]);
        it = frame_hash.erase(it);
    }
}

const char* cname(int cls) { return cls == 1 ? "R1" : cls == 2 ? "R2" : "R3"; }

}   // namespace

bool rbring_on() { return config().lag != 0; }
bool rbring_check_on() { return config().lag != 0 && config().check; }

/// The CPU reads (guest, size): the range is one to stage from now on
void rbring_note_read(uint64_t guest, uint64_t size) {
    if (!config().lag || !guest) return;
    if (!rb_ring::class_of(size)) return;
    const int cls = rb_ring::class_of(size);
    if (!cls) return;
    std::lock_guard lk(mu);
    if (table.size() > 4096) table.clear(), learned_lo = ~0ull, learned_hi = 0;
    Addr& a = table[{guest, size}];
    a.cls = cls, a.reads++;
    {   // (HL_RB_LAG_LOG=2: the learned ranges that are not served, every 6000 reads)
        static const bool dbg = getenv("HL_RB_LAG_LOG") && atoi(getenv("HL_RB_LAG_LOG")) >= 2;
        static uint64_t n = 0;
        if (dbg && ++n % 6000 == 0) {
            int rows = 0;
            for (const auto& [k, v] : table)
                if (v.reads >= kQuietReads && !served(v) && rows++ < 12)
                    logf("vk: rbring not served: %#llx +%#llx R%d reads %llu stageable %d tainted %d writes %llu staged %llu", (unsigned long long)k.first,
                         (unsigned long long)k.second, v.cls, (unsigned long long)v.reads, v.stageable, v.tainted, (unsigned long long)v.writes,
                         (unsigned long long)v.staged);
        }
    }
    if (learned_lo > guest) learned_lo = guest;
    if (learned_hi < guest + size) learned_hi = guest + size;
}

/// Every binding for writing (a mirror's or guest memory's, gpu_buffer_bind): the learned ranges it overlaps are marked, an exact one counted
void rbring_note_written(uint64_t guest, uint64_t size) {
    if (!config().lag || guest >= learned_hi.load(std::memory_order_relaxed) || guest + size <= learned_lo.load(std::memory_order_relaxed)) return;
    std::lock_guard lk(mu);
    for (auto it = table.lower_bound({guest > 0x2000 ? guest - 0x2000 : 0, 0}); it != table.end() && it->first.first < guest + size; ++it) {
        const uint64_t lo = it->first.first, hi = lo + it->first.second;
        if (hi <= guest) continue;
        if (lo == guest && hi == guest + size) it->second.writes++;
        else it->second.tainted = true;
    }
}

/// A program's binding for writing in a mirror (pools_mu held): true when the range is staged, so no write-back is made for it
bool rbring_stage_bind(uint64_t guest, uint64_t size, VkBuffer buffer, VkDeviceSize offset) {
    if (!config().lag || !rb_ring::class_of(size) || !buffer) return false;
    std::lock_guard lk(mu);
    const auto it = table.find({guest, size});
    if (it == table.end()) return false;
    if (!ring_init() || pending_bytes + rb_ring::copy_bytes(it->second.cls, size) > rb_ring::kMaxPending) return false;
    if (!it->second.stageable) it->second.first_stage = it->second.writes ? it->second.writes - 1 : 0;   // (this binding was counted by rbring_note_written just before)
    it->second.staged++;
    it->second.stageable = true;
    for (Req& r : pending)
        if (r.guest == guest && r.size == size) {   // (written twice in a submission: the last binding's state is what the end copies)
            r.buffer = buffer, r.offset = offset;
            return !config().check;
        }
    pending.push_back({guest, size, buffer, offset});
    pending_bytes += rb_ring::copy_bytes(it->second.cls, size);
    return !config().check;
}

/// The end of a submission (pools_mu held): what the submission's programs wrote to the staged ranges, copied into the ring
void rbring_flush(VkCommandBuffer cmd, uint64_t value) {
    if (pending.empty()) return;   // (read without mu: written under pools_mu, as this call is)
    std::lock_guard lk(mu);
    if (pending.empty() || !ring_map) {
        pending.clear(), pending_bytes = 0;
        return;
    }
    const int64_t tag = rb_tag_for(value);
    auto barrier = [&](VkPipelineStageFlags2 s, VkAccessFlags2 sa, VkPipelineStageFlags2 d, VkAccessFlags2 da) {
        VkMemoryBarrier2 b{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
        b.srcStageMask = s, b.srcAccessMask = sa, b.dstStageMask = d, b.dstAccessMask = da;
        VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
        di.memoryBarrierCount = 1, di.pMemoryBarriers = &b;
        vkCmdPipelineBarrier2(cmd, &di);
    };
    barrier(VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, VK_ACCESS_2_MEMORY_WRITE_BIT, VK_PIPELINE_STAGE_2_COPY_BIT, VK_ACCESS_2_TRANSFER_READ_BIT);
    std::stable_sort(pending.begin(), pending.end(), [](const Req& a, const Req& b) { return a.buffer < b.buffer; });
    std::vector<VkBufferCopy> regions;
    for (size_t i = 0; i < pending.size();) {
        const VkBuffer buffer = pending[i].buffer;
        regions.clear();
        for (; i < pending.size() && pending[i].buffer == buffer; i++) {
            const Req& r = pending[i];
            Addr& a = table[{r.guest, r.size}];
            const uint64_t n = rb_ring::copy_bytes(a.cls, r.size);
            rb_ring::Entry e;
            e.seq = ++next_seq, e.tag = tag, e.value = value;
            e.cum = arena_total;
            e.at = rb_ring::allocate(arena_at, arena_total, n);
            rb_ring::push(a.ents, a.n, e);
            regions.push_back({r.offset, e.at, n});
            st_staged++, st_staged_bytes += (int64_t)n;
        }
        vkCmdCopyBuffer(cmd, buffer, ring_buffer, (uint32_t)regions.size(), regions.data());
    }
    barrier(VK_PIPELINE_STAGE_2_COPY_BIT, VK_ACCESS_2_TRANSFER_WRITE_BIT, VK_PIPELINE_STAGE_2_HOST_BIT, VK_ACCESS_2_HOST_READ_BIT);
    pending.clear(), pending_bytes = 0;
}

/// HL_RB_LAG_CHECK=1: a read of a range no program writes (answered with no wait in lag mode) before the check's entry: would the waits have changed it?
bool rbring_quiet(uint64_t guest, uint64_t size) {
    if (!config().lag || !config().check || !rb_ring::class_of(size)) return false;
    std::lock_guard lk(mu);
    const auto it = table.find({guest, size});
    return it != table.end() && !it->second.stageable && served(it->second);
}
void rbring_quiet_result(uint64_t size, bool changed) {
    static std::atomic<int64_t> reads{0}, changes{0};
    if (changed) changes++;
    if (++reads % 3000 == 0)
        logf("vk: rbring check quiet ranges (read 0x%llx bytes; no program writes them): 3000 reads, %lld of them changed by the old path's waits",
             (unsigned long long)size, (long long)changes.exchange(0));
}

/// True: this read is answered by rbring_apply and waits for nothing (lag mode, not the check, a range whose bindings are staged)
bool rbring_serves(uint64_t guest, uint64_t size) {
    if (!config().lag || config().check) return false;
    if (!rb_ring::class_of(size)) return false;
    std::lock_guard lk(mu);
    const auto it = table.find({guest, size});
    return it != table.end() && served(it->second);
}

/// The read of a served range: the newest unapplied entry `lag` frames old into guest memory
void rbring_apply(uint64_t gpu, uint64_t size) {
    const uint64_t guest = host->gpu_guest(gpu);
    const int64_t c = rb_frame_now();
    rb_wait_staged(c, config().lag);   // (frames in flight: the replay stages a frame's entries before it shows it)
    rb_ring::Entry e;
    int cls;
    uint64_t total;
    {
        std::lock_guard lk(mu);
        const auto it = table.find({guest, size});
        if (it == table.end()) return;
        Addr& a = it->second;
        cls = a.cls;
        total = arena_total;
        const int k = rb_ring::pick(a.ents, a.n, c, config().lag, a.applied);
        stats[cls].reads++;
        if (k < 0) {
            stats[cls].none++;
            hash_add(c, cls, rb_ring::hash_read(cls, guest, nullptr, 0));
            return;
        }
        e = a.ents[k];
    }
    const uint64_t n = rb_ring::copy_bytes(cls, size);
    if (!rb_ring::intact(e, total, n)) return;   // (the ring went round since: far too old to hand out)
    if (e.value > vk_timeline_completed()) {
        stats[cls].waited++;
        if (!rbring_wait_value(e.value, config().wait_ms)) {
            stats[cls].late++;
            return;
        }
    }
    uint8_t buf[0x2000];
    std::memcpy(buf, ring_map + e.at, n);
    host->gpu_write(gpu, buf, n);
    stats[cls].applied++;
    stats[cls].got[std::min<int64_t>(4, std::max<int64_t>(1, c - e.tag)) - 1]++;
    {
        std::lock_guard lk(mu);
        const auto it = table.find({guest, size});
        if (it != table.end()) it->second.applied = std::max(it->second.applied, e.seq);
    }
    hash_add(c, cls, rb_ring::hash_read(cls, guest, buf, n));
    if (stats[cls].reads % 3000 == 0)
        logf("vk: rbring %s: 3000 reads, %lld applied (%lld waited for the GPU, %lld not done in time), %lld nothing new; age in frames of the entries "
             "applied 1/2/3/4+: %lld/%lld/%lld/%lld (a fixed lag L hands out entries L frames old or older); staged so far %lld ranges, %.1f MB",
             cname(cls), (long long)stats[cls].applied.exchange(0), (long long)stats[cls].waited.exchange(0), (long long)stats[cls].late.exchange(0),
             (long long)stats[cls].none.exchange(0), (long long)stats[cls].got[0].exchange(0), (long long)(stats[cls].got[1].exchange(0) + 0),
             (long long)stats[cls].got[2].exchange(0), (long long)(stats[cls].got[3].exchange(0) + stats[cls].got[4].exchange(0)),
             (long long)st_staged.load(), st_staged_bytes.load() / 1048576.0);
}

/// HL_RB_LAG_CHECK=1, after the old path's waits: the ring's candidate against the value the game is about to read
void rbring_check(uint64_t gpu, uint64_t size) {
    if (!config().lag || !config().check) return;
    const uint64_t guest = host->gpu_guest(gpu);
    const int64_t c = rb_frame_now();
    rb_ring::Entry e;
    int cls = 0;
    uint64_t total = 0;
    bool have = false;
    int age = -1;
    {
        std::lock_guard lk(mu);
        const auto it = table.find({guest, size});
        if (it == table.end() || !it->second.stageable) return;
        Addr& a = it->second;
        if (a.tainted || a.writes - a.first_stage != a.staged) st_direct++;
        cls = a.cls, total = arena_total;
        if (const int nw = rb_ring::newest(a.ents, a.n); nw >= 0) age = (int)std::min<int64_t>(3, std::max<int64_t>(0, c - a.ents[nw].tag));
        const int k = rb_ring::pick(a.ents, a.n, c, config().lag, a.applied);
        if (k >= 0) e = a.ents[k], have = true, a.applied = e.seq;
    }
    Stats& st = stats[cls];
    st.reads++;
    if (age >= 0) st.age[age]++;
    const uint64_t n = rb_ring::copy_bytes(cls, size);
    uint8_t waited[0x2000];
    host->gpu_read(gpu, waited, n);
    hash_add(c, cls, rb_ring::hash_read(cls, guest, waited, n));   // (HL_RB_LAG_LOG=1: what the old path's waits gave the game, as the control of the ring's hashes)
    if (have) {
        st.chk_new++;
        if (e.value > vk_timeline_completed() && !rbring_wait_value(e.value, config().wait_ms)) {
            st.chk_notdone++;
        } else if (!rb_ring::intact(e, total, n)) {
            st.chk_old++;
        } else if (!std::memcmp(waited, ring_map + e.at, n)) {
            st.chk_equal++;
        } else {
            st.chk_differ++;
            static std::atomic<int> logged{0};
            if (logged++ < 24) {
                uint64_t w = 0;
                while (w + 4 <= n && !std::memcmp(waited + w, ring_map + e.at + w, 4)) w += 4;
                uint32_t a = 0, b = 0;
                std::memcpy(&a, waited + w, 4), std::memcpy(&b, ring_map + e.at + w, 4);
                logf("vk: rbring check %s differs at frame %lld %#llx: first word %llu waited %08x ring %08x (entry tag %lld)", cname(cls),
                     (long long)c, (unsigned long long)guest, (unsigned long long)(w / 4), a, b, (long long)e.tag);
            }
        }
    } else {
        bool zero = true;
        for (uint64_t i = 0; i < n && zero; i++) zero = waited[i] == 0;
        (zero ? st.chk_zero : st.chk_nonzero)++;
    }
    if (st.reads % 3000 == 0)
        logf("vk: rbring check %s (lag %u): 3000 reads; a new entry in %lld (equal to the waited value %lld, differ %lld, ring entry not done %lld, "
             "ring overwritten %lld); no new entry in %lld reads (the waited value is zero in %lld, not zero in %lld); newest entry's age "
             "in frames 0/1/2/3+: %lld/%lld/%lld/%lld",
             cname(cls), config().lag, (long long)st.chk_new.exchange(0), (long long)st.chk_equal.exchange(0), (long long)st.chk_differ.exchange(0),
             (long long)st.chk_notdone.exchange(0), (long long)st.chk_old.exchange(0), (long long)(st.chk_zero.load() + st.chk_nonzero.load()),
             (long long)st.chk_zero.exchange(0), (long long)st.chk_nonzero.exchange(0), (long long)st.age[0].exchange(0),
             (long long)st.age[1].exchange(0), (long long)st.age[2].exchange(0), (long long)st.age[3].exchange(0));
}

}   // namespace mkwvk
