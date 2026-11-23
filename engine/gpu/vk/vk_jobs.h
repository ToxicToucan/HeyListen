// Copyright (C) 2026 Sacrappy

// Background work of the back end (vk_jobs.cpp): shader translation and pipeline builds run on a few worker threads
// at below-normal priority while the replay goes on, after totk-pc-recomp's vk_pipeline.c background builds. A draw
// whose program or pipeline is not ready yet is left out of its frame, or waits for it (vk_draw.cpp decides).
#pragma once

#include <atomic>
#include <cstdint>
#include <functional>

namespace mkwvk {

/// Queues fn for a worker (urgent: ahead of the others, for work a draw waits on)
/// flag: what the job sets when it is done. A jobs_wait on it while the job is still queued runs the job there and then
void jobs_push(std::function<void()> fn, bool urgent = false, const std::atomic<bool>* flag = nullptr);
/// Queues fn for the one thread that runs such jobs one at a time, in order
void jobs_push_serial(std::function<void()> fn, bool first = false);
/// Waits until flag is set by a job (which calls jobs_notify after setting it). Returns whether the job ran during the
/// wait: stolen to this thread from the queue, or a worker seen finishing it just then (false: it was done already)
bool jobs_wait(const std::atomic<bool>& flag);
void jobs_notify();
int jobs_pending();
/// The present the draw path is recording now, for the jobs' quiet clock; noted per draw, cheap
void jobs_frame_note(uint64_t frame);
/// Presents since the last jobs_push (UINT64_MAX/2: nothing pushed yet). A queue that has not grown for a while of the
/// game's frames is a churn that has stopped pushing, whatever depth it still shows: the storm hold releases on it
uint64_t jobs_last_push_age_frames();
/// (RX_VK_STORM_WORKERS=<n>) The storm hold's engage and release: while on, n workers translate (extra ones take jobs only then)
void jobs_set_boost(bool on);
/// (RX_VK_JOB_DEMAND=1) A draw left out for want of this job's result asks for it again: the job moves up behind the jobs the
/// frame's earlier draws asked for, ahead of everything queued for no draw lately (the churn's superseded variants). No-op
/// when the switch is off or the job is not queued
void jobs_demand(const std::atomic<bool>& flag);
/// Jobs queued or running that a draw asked for (pushed, or demanded) within the last window presents; the rest of the
/// queue is what no frame has wanted since (the storm hold's release counts these: RX_VK_STORM_LIVE)
int jobs_pending_live(uint64_t window);

} // namespace mkwvk
