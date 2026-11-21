// (fork) Clips: the Clip key (the Controls page's row, F9 unless changed) or the controller's Capture button held starts one
// and the next press ends it (a toggle; clip_max_minutes=, 10, ends one left running); it saves an MP4, encoded by NVIDIA's NVENC through an ffmpeg
// found on this PC (clip_ffmpeg= in hookshot_options.ini, else ffmpeg.exe on the PATH, else C:\ffmpeg\bin; nothing is
// downloaded). The folder is clip_folder=, else "clips" beside the game. While one records, a REC marker shows in the top
// right corner (gpu/vk/vk_overlay.cpp; the clip is the game's picture, so the marker is not in it).
//
// The GPU side is the present's (gpu/vk/vk_replay.cpp): each due frame (clip_fps=, 60) blitted to the clip's size
// (clip_height=, 1080 at most) and copied to a readback buffer of its own. This side copies a finished readback into a pool
// buffer on a thread of its own and a writer paces the frames to a constant rate into ffmpeg's pipe.
// From the OoT recomp's clip recorder (runtime/src/gfx/clip_recorder.cpp, the menu branch).
//
// The Clip mode row's "Last 10 s" (clip_mode=1): the same frames go to an ffmpeg that runs the whole time, writing 2-second
// MPEG-TS pieces in a ring (clip_buffer_seconds=, 10, plus three pieces) in a ".clipbuffer" folder inside the clip folder, a
// keyframe at each piece's start; the Clip key then saves the newest pieces as an MP4 (copied, not encoded again), so a clip
// holds what just happened (10 to 12 s: whole pieces). No REC marker shows in this mode.
#pragma once
#include <atomic>
#include <cstdint>
#include <string>

namespace mkwvk::clip {

constexpr int kReadbacks = 6;   // (the GPU side's readback buffers)

struct Settings {
    std::string folder, ffmpeg, encoder = "h264_nvenc", preset = "p5";   // (clip_preset=p1..p7: NVENC's, p1 fastest)
    int fps = 60, height = 1080, max_minutes = 10, bitrate_mbps = 30, buffer_seconds = 10;
};
Settings settings();   // (hookshot_options.ini's clip_ lines, read now)

// A clip of w x h at fps starts: its job, pool and threads made, ffmpeg started on its worker. False: nothing started.
// ring: the Last 10 s mode's buffer (pieces in a ring) instead of one MP4.
bool start(uint32_t w, uint32_t h, const Settings& s, bool ring = false);
// The Last 10 s mode: the newest buffer_seconds (or a little more) saved as an MP4, on a thread of its own. False: no buffer.
bool save_last();
// The GPU side's frame number index (from the clip's start, at fps) is in readback buffer rb (src, w*h*4 bytes, done on the
// GPU): the copier takes it and clears busy[rb] once read. False: dropped (no pool buffer): busy[rb] is cleared at once.
bool frame(int64_t index, int rb, const uint8_t* src);
std::atomic<bool>* busy();   // (kReadbacks of them: a buffer stays taken until the copier has read it)
// The clip ends at end_index frames: the writer repeats the last frame up to it, closes the pipe and waits for ffmpeg.
void finish(int64_t end_index);
bool failed();

}  // namespace mkwvk::clip
