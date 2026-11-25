// Copyright (C) 2026 Sacrappy
// The submit sequencer of the replay's lanes (stream DECOUPLE, docs/research/decouple-cross-frame.md stage 2, section 4.3 item 1).
// Every unit of the stream takes a ticket, in stream order, when the spine begins it: a plain recording (begin_recording), or a big
// command buffer's group of segments (its segments go out in their own order inside it, EarlyGroup's next_sub). A unit is finished
// when it is in the queue: a recording sent, a group whose every segment is sent, or a unit dropped (nothing of it goes out). A
// recording made ready while a ticket before it is still open is left ready; whoever finishes the one before sends it (ready_head).
// Today every group is joined before the next submission's first recording is sent, so units finish in ticket order; with the
// lanes (RX_VK_LANES) the spine goes on beside a pending group and the sequencer keeps the queue's order. RX_VK_LANES_CHECK=1 takes
// the tickets with today's joins and counts a unit finished while an earlier one is open (out_of_order: must read 0).
// The pure rules, with no Vulkan and no locking in them (the caller holds one mutex): tests/test_submit_seq.py.
#pragma once
#include <cstdint>
#include <cstdlib>
#include <deque>

namespace submit_seq {

constexpr unsigned kMaxLanes = 4;

/// RX_VK_LANES=<0..kMaxLanes> (unset or 0: off): how many submissions' groups may be pending at once
inline unsigned parse_lanes(const char* e) {
    if (!e || !*e) return 0;
    const long v = atol(e);
    return v < 0 ? 0u : v > (long)kMaxLanes ? kMaxLanes : (unsigned)v;
}

class Sequencer {
public:
    enum : uint8_t { kOpen = 0, kReady = 1, kDone = 2 };

    /// A unit begun by the spine: its ticket, the next in stream order
    uint64_t take() {
        st_.push_back(kOpen);
        taken_++;
        const uint64_t pend = next_ - head_ + 1;
        if (pend > max_pending_) max_pending_ = pend;
        return next_++;
    }
    /// Whether every unit before t is finished (t may go to the queue now)
    bool is_head(uint64_t t) const { return t == head_; }
    /// Whether t is taken and not finished
    bool open(uint64_t t) const { return t >= head_ && t < next_ && st_[t - head_] != kDone; }
    /// A recording made ready that may not go yet (a unit before it is open): it waits for ready_head()
    void ready(uint64_t t) {
        if (!open(t)) {
            unknown_++;
            return;
        }
        st_[t - head_] = kReady;
    }
    /// The head's ticket when its recording was left ready (to send now, then finish), else 0
    uint64_t ready_head() const { return !st_.empty() && st_[0] == kReady ? head_ : 0; }
    /// The unit is in the queue (counted out of order when a unit before it is still open). drop: it never goes (not counted)
    void finish(uint64_t t) { end(t, true); }
    void drop(uint64_t t) { end(t, false); }

    uint64_t head() const { return head_; }
    uint64_t pending() const { return next_ - head_; }
    int64_t taken() const { return taken_; }
    int64_t finished() const { return finished_; }
    int64_t dropped() const { return dropped_; }
    int64_t out_of_order() const { return out_of_order_; }
    int64_t unknown() const { return unknown_; }
    uint64_t max_pending() const { return max_pending_; }

private:
    void end(uint64_t t, bool sent) {
        if (!open(t)) {   // (0, finished twice, or never taken)
            unknown_++;
            return;
        }
        if (sent && t != head_) out_of_order_++;
        st_[t - head_] = kDone;
        (sent ? finished_ : dropped_)++;
        while (!st_.empty() && st_[0] == kDone) st_.pop_front(), head_++;
    }
    std::deque<uint8_t> st_;   // (the tickets head_ .. next_ - 1)
    uint64_t next_ = 1, head_ = 1, max_pending_ = 0;
    int64_t taken_ = 0, finished_ = 0, dropped_ = 0, out_of_order_ = 0, unknown_ = 0;
};

/// The order thread's "done" marks with a submission's end pending (RX_VK_SUB_OVERLAP with RX_VK_ORDER_THREAD; section 4.3 item 5).
/// The order thread finishes its jobs in order and publishes how far it is (the last job's sequence number, the highest value): a
/// game thread that waits for a value goes on to the GPU's timeline once it is published. A submit job that returns with its
/// submission's end still pending (sent later by the thread that puts its group's last segment in the queue) is done only when that
/// end is sent: the marks stop just before it and wait, the jobs behind it finishing meanwhile, until end_sent() reaches its value.
/// The caller holds one mutex over all of it (ot_mu).
struct DoneHold {
    uint64_t pub_seq = 0, pub_value = 0;   // (published: every job up to pub_seq done, values up to pub_value)
    uint64_t max_seq = 0, max_value = 0;   // (finished, published or held)
    uint64_t held_value = 0;               // (0: nothing held; else the pending submission's value)
    uint64_t sent = 0;                     // (the highest pending submission's value whose end is sent)
    int64_t holds = 0, released_late = 0;
    /// A job finished in order. pend: its submission's value when it left the end pending, else 0. True: the marks moved
    bool finish(uint64_t seq, uint64_t value, uint64_t pend) {
        if (seq > max_seq) max_seq = seq;
        if (value > max_value) max_value = value;
        if (pend && pend > sent) {   // (its end not sent yet: done up to the job before it)
            if (!held_value) {
                holds++;
                held_value = pend;
                if (seq - 1 > pub_seq) pub_seq = seq - 1;
                return false;
            }
            if (pend > held_value) held_value = pend;   // (a later one pending behind the held one: the marks wait for both)
            return false;
        }
        if (held_value) return false;   // (behind a held one)
        return publish();
    }
    /// The end of the submission with this value is in the queue. True: the marks moved
    bool end_sent(uint64_t v) {
        if (v > sent) sent = v;
        if (!held_value || sent < held_value) return false;
        held_value = 0;
        released_late++;
        return publish();
    }

private:
    bool publish() {
        const bool moved = max_seq != pub_seq || max_value != pub_value;
        pub_seq = max_seq, pub_value = max_value;
        return moved;
    }
};

}   // namespace submit_seq
