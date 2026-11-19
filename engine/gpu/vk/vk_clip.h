// (fork) Clips: the Clip key (the Controls page's row, F9 unless changed) or the controller's Capture button held starts one;
// it records the presented frames for up to clip_seconds= (10) and saves an MP4, encoded by NVIDIA's NVENC through an ffmpeg
// found on this PC (clip_ffmpeg= in hookshot_options.ini, else ffmpeg.exe on the PATH, else C:\ffmpeg\bin; nothing is
// downloaded). A second press ends it early. The folder is clip_folder=, else "clips" beside the game.
//
// The GPU side is the present's (gpu/vk/vk_replay.cpp): each due frame (clip_fps=, 60) blitted to the clip's size
// (clip_height=, 1080 at most) and copied to a readback buffer of its own. This side copies a finished readback into a pool
// buffer on a thread of its own and a writer paces the frames to a constant rate into ffmpeg's pipe.
// From the OoT recomp's clip recorder (runtime/src/gfx/clip_recorder.cpp, the menu branch).
#pragma once
#include <atomic>
#include <cstdint>
#include <string>

namespace mkwvk::clip {

constexpr int kReadbacks = 6;   // (the GPU side's readback buffers)

struct Settings {
    std::string folder, ffmpeg, encoder = "h264_nvenc";
    int fps = 60, height = 1080, seconds = 10, bitrate_mbps = 30;
};
Settings settings();   // (hookshot_options.ini's clip_ lines, read now)

// A clip of w x h at fps starts: its job, pool and threads made, ffmpeg started on its worker. False: nothing started.
bool start(uint32_t w, uint32_t h, const Settings& s);
// The GPU side's frame number index (from the clip's start, at fps) is in readback buffer rb (src, w*h*4 bytes, done on the
// GPU): the copier takes it and clears busy[rb] once read. False: dropped (no pool buffer): busy[rb] is cleared at once.
bool frame(int64_t index, int rb, const uint8_t* src);
std::atomic<bool>* busy();   // (kReadbacks of them: a buffer stays taken until the copier has read it)
// The clip ends at end_index frames: the writer repeats the last frame up to it, closes the pipe and waits for ffmpeg.
void finish(int64_t end_index);
bool failed();

}  // namespace mkwvk::clip
