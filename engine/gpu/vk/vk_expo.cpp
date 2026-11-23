// HL_EXPO_GPU (EXPO1, docs/native-engine/exposure.md): the exposure meter's sums reach the game's CPU from a ring the engine owns, not
// from the GPU's write-back of the block. The producer copies each meter dispatch's four sum words into a slot of a host-visible
// buffer, right after the dispatch, and stamps the slot with the dispatch's sequence number once the copy is done. The consumer, the
// game's invalidate of the block, writes into guest memory the sums of a dispatch `lag` ones back from the newest recorded
// (expo_feed.h), and does not wait for any write-back: with lag 2 that dispatch is a frame older than the one the GPU is working on.
// HL_EXPO_GPU=2 runs the old path and compares what it read with the ring's newest slot, bit for bit.
#include <chrono>
#include <cstdio>
#include <mutex>
#include <thread>

#include "vk_internal.h"
#include "expo_feed.h"

namespace mkwvk {
extern int64_t presents_now;   // (vk_replay.cpp)
namespace {

// HL_EXPO_GPU=1: the exposure meter's sums come to the game's CPU from the engine's ring, `HL_EXPO_LAG` dispatches back (default 2), with no wait for the GPU's write-back; =2: the old path runs and is compared with the ring (a check); 0 (the default): as before. HL_EXPO_LAG=<1..14>, HL_EXPO_WAIT_MS=<ms> (4: how long a read waits for a slot not done), HL_EXPO_LOG=<rows>: the check's rows.
const expo_feed::Config& config() {
    static const expo_feed::Config c = expo_feed::parse(getenv("HL_EXPO_GPU"), getenv("HL_EXPO_LAG"));
    return c;
}

std::mutex mu;
expo_feed::Ledger ledger;
VkBuffer ring_buffer = VK_NULL_HANDLE;
uint8_t* ring_map = nullptr;
bool ring_failed = false;
constexpr VkDeviceSize kBytes = (VkDeviceSize)expo_feed::kBlocks * expo_feed::kRing * expo_feed::kSlotBytes;

bool ring_init() {   // (mu held)
    if (ring_map) return true;
    if (ring_failed) return false;
    ring_failed = true;
    VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bi.size = kBytes;
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
        vkBindBufferMemory(dev.device, buffer, memory, 0) != VK_SUCCESS || vkMapMemory(dev.device, memory, 0, kBytes, 0, &words) != VK_SUCCESS) {
        logf("vk: HL_EXPO_GPU: no host memory for the sums ring: the old path stays");
        vkDestroyBuffer(dev.device, buffer, nullptr);
        return false;
    }
    std::memset(words, 0, kBytes);
    ring_buffer = buffer;
    ring_map = (uint8_t*)words;
    ring_failed = false;
    logf("vk: HL_EXPO_GPU=%d: the exposure meter's sums come from a ring of %u slots a block, lag %u (docs/native-engine/exposure.md)",
         config().mode, expo_feed::kRing, config().lag);
    return true;
}

const volatile uint8_t* slot_at(int block, unsigned slot) {
    return ring_map + ((size_t)block * expo_feed::kRing + slot) * expo_feed::kSlotBytes;
}
uint32_t stamp_of(int block, unsigned slot) { return *(const volatile uint32_t*)(slot_at(block, slot) + expo_feed::kStampOffset); }

double wait_ms() {
    static const double ms = getenv("HL_EXPO_WAIT_MS") ? atof(getenv("HL_EXPO_WAIT_MS")) : 4.0;
    return ms;
}

/// The sums a read wants from block `block`, the lag given: waits at most wait_ms for the slot, else the newest older one, else zeros.
/// Returns what it found: 0 the wanted slot, 1 an older one (it was not done in time), 2 none (zeros)
int fetch(int block, uint32_t newest, unsigned lag, uint32_t out[4], bool wait) {
    std::memset(out, 0, 16);
    const uint32_t want = expo_feed::wanted(newest, lag);
    if (!want) return 2;
    const unsigned slot = expo_feed::slot_of(want);
    const auto t0 = std::chrono::steady_clock::now();
    while (stamp_of(block, slot) != want) {
        if (!wait || std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count() >= wait_ms()) {
            uint32_t stamps[expo_feed::kRing];
            for (unsigned i = 0; i < expo_feed::kRing; i++) stamps[i] = stamp_of(block, i);
            const int older = expo_feed::newest_older(stamps, want);
            if (older < 0) return 2;
            std::memcpy(out, (const void*)slot_at(block, (unsigned)older), 16);
            return 1;
        }
        std::this_thread::yield();
    }
    std::memcpy(out, (const void*)slot_at(block, slot), 16);
    return 0;
}

}   // namespace

int expo_mode() { return config().mode; }

/// The producer: right after a meter program's dispatch, on its recording
void expo_record(VkCommandBuffer cmd, VkBuffer src, VkDeviceSize offset, uint64_t guest) {
    if (!config().mode || !src || !guest) return;
    int block;
    uint32_t seq;
    {
        std::lock_guard lk(mu);
        if (!ring_init()) return;
        block = ledger.add(guest);
        if (block < 0) return;
        seq = ledger.issue(block);
    }
    const VkDeviceSize at = ((VkDeviceSize)block * expo_feed::kRing + expo_feed::slot_of(seq)) * expo_feed::kSlotBytes;
    auto barrier = [&](VkPipelineStageFlags2 s, VkAccessFlags2 sa, VkPipelineStageFlags2 d, VkAccessFlags2 da) {
        VkMemoryBarrier2 b{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
        b.srcStageMask = s, b.srcAccessMask = sa, b.dstStageMask = d, b.dstAccessMask = da;
        VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
        di.memoryBarrierCount = 1, di.pMemoryBarriers = &b;
        vkCmdPipelineBarrier2(cmd, &di);
    };
    barrier(VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT, VK_ACCESS_2_SHADER_WRITE_BIT, VK_PIPELINE_STAGE_2_COPY_BIT, VK_ACCESS_2_TRANSFER_READ_BIT);
    const VkBufferCopy c{offset, at, 16};
    vkCmdCopyBuffer(cmd, src, ring_buffer, 1, &c);
    barrier(VK_PIPELINE_STAGE_2_COPY_BIT, VK_ACCESS_2_TRANSFER_WRITE_BIT, VK_PIPELINE_STAGE_2_CLEAR_BIT, VK_ACCESS_2_TRANSFER_WRITE_BIT);
    vkCmdFillBuffer(cmd, ring_buffer, at + expo_feed::kStampOffset, 4, seq);
    barrier(VK_PIPELINE_STAGE_2_CLEAR_BIT, VK_ACCESS_2_TRANSFER_WRITE_BIT, VK_PIPELINE_STAGE_2_HOST_BIT, VK_ACCESS_2_HOST_READ_BIT);
}

/// True: this read is answered by expo_answer (HL_EXPO_GPU=1 and a block the ring has been fed from), and waits for nothing
bool expo_serves(uint64_t guest) {
    if (config().mode != 1) return false;
    std::lock_guard lk(mu);
    return ledger.find(guest) >= 0;
}

/// HL_EXPO_GPU=1: the sums of the dispatch `lag` back from the newest recorded, into the game's memory
void expo_answer(uint64_t gpu) {
    static std::atomic<int64_t> reads{0}, older{0}, none{0};
    int block;
    uint32_t newest;
    {
        std::lock_guard lk(mu);
        block = ledger.find(host->gpu_guest(gpu));
        if (block < 0) return;
        newest = ledger.recorded[block];
    }
    uint32_t w[4];
    const int r = fetch(block, newest, config().lag, w, true);
    host->gpu_write(gpu, w, 16);
    if (r == 1) older++;
    if (r == 2) none++;
    if (++reads % 3000 == 0)
        logf("vk: HL_EXPO_GPU: 3000 reads of the meter's sums served from the ring: %lld took an older slot (the wanted one was not done within %.1f ms), %lld found nothing (zeros)",
             (long long)older.exchange(0), wait_ms(), (long long)none.exchange(0));
}

/// HL_EXPO_GPU=2, after the old path read the block: its first 16 bytes against the ring's newest slot (lag 1), and the ring one back (lag 2)
void expo_check(uint64_t gpu) {
    static std::atomic<int64_t> reads{0}, same{0}, differ{0}, unfinished{0};
    static const int rows = getenv("HL_EXPO_LOG") ? atoi(getenv("HL_EXPO_LOG")) : 0;
    static std::atomic<int> logged{0};
    int block;
    uint32_t newest;
    {
        std::lock_guard lk(mu);
        block = ledger.find(host->gpu_guest(gpu));
        if (block < 0) return;
        newest = ledger.recorded[block];
    }
    uint32_t old_w[4], l1[4], l2[4];
    host->gpu_read(gpu, old_w, 16);
    const int r1 = fetch(block, newest, 1, l1, true);
    fetch(block, newest, 2, l2, false);
    reads++;
    if (r1 != 0) unfinished++;
    const bool eq = !std::memcmp(old_w, l1, 16);
    (eq ? same : differ)++;
    if ((!eq && differ <= 12) || (rows && logged++ < rows))
        logf("vk: expo check %lld block %#llx seq %u: old %08x %08x %08x %08x | lag1 %08x %08x %08x %08x%s | lag2 %08x %08x %08x %08x%s",
             (long long)presents_now, (unsigned long long)host->gpu_guest(gpu), newest, old_w[0], old_w[1], old_w[2], old_w[3], l1[0], l1[1], l1[2],
             l1[3], eq ? "" : " MISMATCH", l2[0], l2[1], l2[2], l2[3], r1 ? " (ring slot not done)" : "");
    if (reads % 1000 == 0)
        logf("vk: HL_EXPO_GPU=2 check: %lld reads, %lld equal, %lld mismatches, %lld with the newest slot not done", (long long)reads.load(),
             (long long)same.load(), (long long)differ.load(), (long long)unfinished.load());
}

}   // namespace mkwvk
