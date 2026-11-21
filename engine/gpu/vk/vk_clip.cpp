// (fork) Clips, see vk_clip.h. From the OoT recomp's clip recorder (runtime/src/gfx/clip_recorder.cpp, the menu branch).
#include "vk_clip.h"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

#include "vk_internal.h"   // (logf)

#ifdef _WIN32
namespace mkwvk::clip {
namespace {

using Clock = std::chrono::steady_clock;

struct Job {
    std::string path, log_path, ffmpeg, encoder;
    bool ring = false;   // (the Last 10 s mode: pieces in ring_dir, saved by save_last)
    std::string ring_dir, folder;
    int buffer_seconds = 10;
    uint32_t w = 0, h = 0;
    int fps = 60, bitrate_mbps = 30;
    std::vector<std::vector<uint8_t>> pool;
    std::vector<int> free_bufs;
    struct Msg { bool finish; int64_t index; int buf; };
    std::mutex mu;   // (free_bufs)
    std::mutex wmu;
    std::condition_variable wcv;
    std::deque<Msg> wq;   // (the copier's frames to the writer: the writer can block on the pipe, the copier never does)
    std::atomic<bool> failed{false};
    std::atomic<uint64_t> taken{0}, dropped{0};
    std::atomic<int64_t> end_index{0};
    std::string why;
};

std::shared_ptr<Job> current;   // (the clip being recorded; its threads hold their own reference)
std::mutex current_mu;
std::atomic<bool> busy_[kReadbacks];

std::string ini_dir() {
    char exe[MAX_PATH];
    const DWORD n = GetModuleFileNameA(nullptr, exe, MAX_PATH);
    std::string dir(exe, n < MAX_PATH ? n : 0);
    return dir.substr(0, dir.find_last_of("\\/") + 1);
}

bool exists(const std::string& p) { return !p.empty() && GetFileAttributesA(p.c_str()) != INVALID_FILE_ATTRIBUTES; }

void make_folder(const std::string& folder) {
    for (size_t i = 3; i <= folder.size(); i++)
        if (i == folder.size() || folder[i] == '\\' || folder[i] == '/') CreateDirectoryA(folder.substr(0, i).c_str(), nullptr);
}

void writer_main(std::shared_ptr<Job> job) {
    Job& j = *job;
    HANDLE proc = nullptr, pipe_wr = nullptr;
    bool ok = false;
    auto fail = [&](const std::string& why) {
        if (!j.failed.exchange(true)) j.why = why;
    };
    SECURITY_ATTRIBUTES sa = {sizeof sa, nullptr, TRUE};
    HANDLE rd = nullptr;
    HANDLE logh = CreateFileA(j.log_path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, &sa, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (CreatePipe(&rd, &pipe_wr, &sa, 1 << 20) && logh != INVALID_HANDLE_VALUE) {
        SetHandleInformation(pipe_wr, HANDLE_FLAG_INHERIT, 0);
        char args[2048];
        // (8 bit RGBA, converted to BT.709 limited range for the encoder; fragmented MP4: a clip cut short still plays)
        char out[1024];
        if (j.ring)   // (2-second pieces, each starting on a keyframe, in a ring of buffer_seconds / 2 + 3)
            snprintf(out, sizeof out,
                     "-force_key_frames expr:gte(t,n_forced*2) -forced-idr 1 -f segment -segment_time 2 -segment_wrap %d "
                     "-segment_format mpegts \"%s\\ring-%%02d.ts\"", j.buffer_seconds / 2 + 3, j.ring_dir.c_str());
        else
            snprintf(out, sizeof out, "-movflags +frag_keyframe+empty_moov+default_base_moof \"%s\"", j.path.c_str());
        snprintf(args, sizeof args,
                 "\"%s\" -hide_banner -loglevel warning -y -f rawvideo -pix_fmt rgba -video_size %ux%u -framerate %d -i pipe:0 "
                 "-threads 2 -filter_threads 2 -vf scale=out_color_matrix=bt709:out_range=tv,format=nv12 -c:v %s -preset p5 "
                 "-tune hq -rc vbr -b:v %dM -maxrate %dM -bufsize %dM -g %d -profile:v high -color_range tv -colorspace bt709 "
                 "-color_primaries bt709 -color_trc bt709 %s",
                 j.ffmpeg.c_str(), j.w, j.h, j.fps, j.encoder.c_str(), j.bitrate_mbps, j.bitrate_mbps * 3 / 2,
                 j.bitrate_mbps * 3, j.ring ? j.fps * 2 : j.fps, out);
        STARTUPINFOA si = {};
        si.cb = sizeof si;
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdInput = rd, si.hStdOutput = logh, si.hStdError = logh;
        PROCESS_INFORMATION pi = {};
        if (CreateProcessA(nullptr, args, nullptr, nullptr, TRUE, CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS, nullptr,
                           nullptr, &si, &pi)) {
            proc = pi.hProcess;
            CloseHandle(pi.hThread);
            ok = true;
            logf("clip: %s %ux%u at %d fps to %s (%s, %d Mbit/s, %s)", j.ring ? "buffering the last seconds" : "recording", j.w,
                 j.h, j.fps, j.ring ? j.ring_dir.c_str() : j.path.c_str(), j.encoder.c_str(), j.bitrate_mbps, j.ffmpeg.c_str());
        } else {
            logf("clip: could not start %s (error %lu)", j.ffmpeg.c_str(), GetLastError());
            fail("could not start ffmpeg");
        }
    } else {
        fail("could not open a pipe");
    }
    if (rd) CloseHandle(rd);
    if (logh != INVALID_HANDLE_VALUE) CloseHandle(logh);

    // (paced to a constant rate: a frame is written when the next one, or the end, comes, repeated for the indexes between)
    int64_t written = 0;
    int prev = -1;
    bool pipe_ok = ok;
    auto write_frame = [&](int buf, int64_t times) {
        for (int64_t n = 0; n < times && pipe_ok; ++n) {
            const uint8_t* p = j.pool[(size_t)buf].data();
            size_t left = j.pool[(size_t)buf].size();
            while (left && pipe_ok) {
                DWORD got = 0;
                if (!WriteFile(pipe_wr, p, (DWORD)std::min<size_t>(left, 1u << 22), &got, nullptr) || !got) {
                    pipe_ok = false;
                    logf("clip: the pipe to ffmpeg broke (error %lu): see %s", GetLastError(), j.log_path.c_str());
                    fail("the encoder quit (is NVENC available?)");
                    break;
                }
                p += got, left -= got;
            }
            if (pipe_ok) ++written;
        }
    };
    auto release = [&](int buf) {
        std::lock_guard<std::mutex> lk(j.mu);
        j.free_bufs.push_back(buf);
    };
    for (bool done = false; !done;) {
        Job::Msg m;
        {
            std::unique_lock<std::mutex> lk(j.wmu);
            j.wcv.wait(lk, [&] { return !j.wq.empty(); });
            m = j.wq.front();
            j.wq.pop_front();
        }
        if (m.finish) {
            if (prev >= 0) write_frame(prev, std::max<int64_t>(1, j.end_index.load() - written)), release(prev), prev = -1;
            done = true;
        } else if (!pipe_ok) {
            release(m.buf);
        } else {
            if (prev >= 0) write_frame(prev, std::max<int64_t>(1, m.index - written)), release(prev);
            else written = m.index;
            prev = m.buf;
        }
    }
    if (prev >= 0) release(prev);
    if (pipe_wr) CloseHandle(pipe_wr);
    DWORD code = 1;
    if (proc) {
        if (WaitForSingleObject(proc, 30000) != WAIT_OBJECT_0) TerminateProcess(proc, 1), fail("the encoder did not finish");
        GetExitCodeProcess(proc, &code);
        CloseHandle(proc);
    }
    if (j.failed.load()) {
        logf("clip: failed: %s (ffmpeg's log %s)", j.why.c_str(), j.log_path.c_str());
        return;
    }
    if (j.ring) {   // (the buffer stopped: its pieces go)
        WIN32_FIND_DATAA fd;
        HANDLE fh = FindFirstFileA((j.ring_dir + "\\ring-*.ts").c_str(), &fd);
        if (fh != INVALID_HANDLE_VALUE) {
            do DeleteFileA((j.ring_dir + "\\" + fd.cFileName).c_str());
            while (FindNextFileA(fh, &fd));
            FindClose(fh);
        }
        DeleteFileA(j.log_path.c_str());
        logf("clip: the last-seconds buffer stopped (%lld frames)", (long long)written);
        return;
    }
    if (code) {
        logf("clip: ffmpeg exited with %lu, see %s", code, j.log_path.c_str());
        return;
    }
    WIN32_FILE_ATTRIBUTE_DATA fa{};
    const double mb = GetFileAttributesExA(j.path.c_str(), GetFileExInfoStandard, &fa)
                          ? (double)(((uint64_t)fa.nFileSizeHigh << 32) | fa.nFileSizeLow) / 1048576.0 : 0;
    DeleteFileA(j.log_path.c_str());
    logf("clip: saved %s: %lld frames at %d fps (%llu taken from the game, %llu dropped), %.1f MB", j.path.c_str(),
         (long long)written, j.fps, (unsigned long long)j.taken.load(), (unsigned long long)j.dropped.load(), mb);
}

}  // namespace

Settings settings() {
    Settings s;
    const std::string dir = ini_dir();
    s.folder = dir + "clips";
    if (FILE* f = fopen((dir + "hookshot_options.ini").c_str(), "r")) {
        char line[600];
        while (fgets(line, sizeof line, f)) {
            std::string v(line);
            while (!v.empty() && (v.back() == '\n' || v.back() == '\r' || v.back() == ' ')) v.pop_back();
            const size_t eq = v.find('=');
            if (eq == std::string::npos) continue;
            const std::string k = v.substr(0, eq), val = v.substr(eq + 1);
            if (val.empty()) continue;
            if (k == "clip_folder") s.folder = val;
            else if (k == "clip_ffmpeg") s.ffmpeg = val;
            else if (k == "clip_encoder") s.encoder = val;
            else if (k == "clip_fps") s.fps = std::clamp(atoi(val.c_str()), 10, 120);
            else if (k == "clip_height") s.height = std::clamp(atoi(val.c_str()), 144, 4320);
            else if (k == "clip_max_minutes") s.max_minutes = std::clamp(atoi(val.c_str()), 1, 120);
            else if (k == "clip_bitrate_mbps") s.bitrate_mbps = std::clamp(atoi(val.c_str()), 2, 200);
            else if (k == "clip_buffer_seconds") s.buffer_seconds = std::clamp(atoi(val.c_str()), 4, 120);
        }
        fclose(f);
    }
    if (!exists(s.ffmpeg)) {
        const std::string cfg = s.ffmpeg;
        s.ffmpeg.clear();
        char buf[MAX_PATH * 2];
        if (!cfg.empty()) logf("clip: clip_ffmpeg=%s is not there", cfg.c_str());
        else if (const DWORD n = SearchPathA(nullptr, "ffmpeg.exe", nullptr, (DWORD)sizeof buf, buf, nullptr); n && n < sizeof buf)
            s.ffmpeg = buf;
        else if (exists("C:\\ffmpeg\\bin\\ffmpeg.exe")) s.ffmpeg = "C:\\ffmpeg\\bin\\ffmpeg.exe";
    }
    return s;
}

bool start(uint32_t w, uint32_t h, const Settings& s, bool ring) {
    if (s.ffmpeg.empty()) {
        logf("clip: no ffmpeg found (clip_ffmpeg= in hookshot_options.ini, the PATH, C:\\ffmpeg\\bin): clips need an ffmpeg "
             "with NVENC; nothing is recorded");
        return false;
    }
    make_folder(s.folder);
    auto job = std::make_shared<Job>();
    SYSTEMTIME t;
    GetLocalTime(&t);
    char name[64];
    snprintf(name, sizeof name, "\\clip-%04u%02u%02u-%02u%02u%02u.mp4", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
    job->path = s.folder + name;
    job->log_path = job->path + ".ffmpeg.log";
    job->folder = s.folder, job->ring = ring, job->buffer_seconds = s.buffer_seconds;
    if (ring) {   // (a buffer of its own, emptied first: pieces from an earlier run are not this one's)
        job->ring_dir = s.folder + "\\.clipbuffer";
        make_folder(job->ring_dir);
        WIN32_FIND_DATAA fd;
        HANDLE fh = FindFirstFileA((job->ring_dir + "\\ring-*.ts").c_str(), &fd);
        if (fh != INVALID_HANDLE_VALUE) {
            do DeleteFileA((job->ring_dir + "\\" + fd.cFileName).c_str());
            while (FindNextFileA(fh, &fd));
            FindClose(fh);
        }
        job->log_path = job->ring_dir + "\\ffmpeg.log";
    }
    job->ffmpeg = s.ffmpeg, job->encoder = s.encoder;
    job->w = w, job->h = h, job->fps = s.fps, job->bitrate_mbps = s.bitrate_mbps;
    const size_t bytes = (size_t)w * h * 4;
    const size_t pool_n = std::clamp<size_t>((256ull << 20) / bytes, 4, 32);   // (about 256 MB at most, only while recording)
    job->pool.assign(pool_n, std::vector<uint8_t>(bytes));
    for (size_t i = 0; i < pool_n; ++i) job->free_bufs.push_back((int)i);
    for (auto& b : busy_) b.store(false);
    {
        std::lock_guard<std::mutex> lk(current_mu);
        current = job;
    }
    std::thread(writer_main, job).detach();
    return true;
}

bool frame(int64_t index, int rb, const uint8_t* src) {
    std::shared_ptr<Job> job;
    {
        std::lock_guard<std::mutex> lk(current_mu);
        job = current;
    }
    if (!job) return busy_[rb].store(false), false;
    int buf = -1;
    {
        std::lock_guard<std::mutex> lk(job->mu);
        if (!job->free_bufs.empty()) buf = job->free_bufs.back(), job->free_bufs.pop_back();
    }
    if (buf < 0) {   // (the encoder is behind)
        job->dropped++;
        busy_[rb].store(false);
        return false;
    }
    job->taken++;
    std::thread([job, buf, index, rb, src] {   // (the copy off the replay thread: 8 MB at 1080p)
        std::vector<uint8_t>& px = job->pool[(size_t)buf];
        std::memcpy(px.data(), src, px.size());
        busy_[rb].store(false, std::memory_order_release);
        for (size_t k = 3; k < px.size(); k += 4) px[k] = 255;   // (the game's alpha is not the picture's: opaque)
        {
            std::lock_guard<std::mutex> lk(job->wmu);
            // (in index order: a copy that finishes before an earlier one waits its turn in the writer's pacing)
            auto it = job->wq.begin();
            while (it != job->wq.end() && !it->finish && it->index < index) ++it;
            job->wq.insert(it, {false, index, buf});
        }
        job->wcv.notify_one();
    }).detach();
    return true;
}

std::atomic<bool>* busy() { return busy_; }

bool save_last() {
    std::shared_ptr<Job> job;
    {
        std::lock_guard<std::mutex> lk(current_mu);
        job = current;
    }
    if (!job || !job->ring || job->failed.load()) return false;
    std::thread([job] {
        // (the pieces by time written, newest first; the one being written at the press is waited for, up to 3 s, until
        // the next one starts, so it is whole: a piece copied while written ends in a cut frame (clip10-mode1b))
        struct Piece { uint64_t t; std::string name; };
        auto list_pieces = [&job] {
            std::vector<Piece> v;
            WIN32_FIND_DATAA fd;
            HANDLE fh = FindFirstFileA((job->ring_dir + "\\ring-*.ts").c_str(), &fd);
            if (fh != INVALID_HANDLE_VALUE) {
                do v.push_back({((uint64_t)fd.ftLastWriteTime.dwHighDateTime << 32) | fd.ftLastWriteTime.dwLowDateTime, fd.cFileName});
                while (FindNextFileA(fh, &fd));
                FindClose(fh);
            }
            std::sort(v.begin(), v.end(), [](const Piece& a, const Piece& b) { return a.t > b.t; });
            return v;
        };
        std::vector<Piece> pieces = list_pieces();
        const std::string writing = pieces.empty() ? std::string() : pieces[0].name;
        bool whole = false;
        for (int i = 0; i < 30 && !writing.empty(); i++) {
            Sleep(100);
            pieces = list_pieces();
            if (!pieces.empty() && pieces[0].name != writing) { whole = true; break; }
        }
        if (whole) pieces.erase(pieces.begin());   // (the piece just begun: after the press)
        const size_t want = (size_t)job->buffer_seconds / 2;
        if (pieces.size() > want) pieces.resize(want);
        std::reverse(pieces.begin(), pieces.end());
        if (pieces.empty()) { logf("clip: nothing buffered yet"); return; }
        SYSTEMTIME t;
        GetLocalTime(&t);
        char stamp[64];
        snprintf(stamp, sizeof stamp, "%04u%02u%02u-%02u%02u%02u-%03u", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond,
                 t.wMilliseconds);
        const std::string snap = job->ring_dir + "\\save-" + stamp;   // (copies: the ring goes on writing over its pieces)
        make_folder(snap);
        std::string list;
        for (size_t i = 0; i < pieces.size(); i++) {
            char nm[32];
            snprintf(nm, sizeof nm, "p%02zu.ts", i);
            if (CopyFileA((job->ring_dir + "\\" + pieces[i].name).c_str(), (snap + "\\" + nm).c_str(), FALSE))
                list += std::string("file '") + nm + "'\n";
        }
        if (FILE* f = fopen((snap + "\\list.txt").c_str(), "w")) fputs(list.c_str(), f), fclose(f);
        const std::string out = job->folder + "\\clip-" + std::string(stamp).substr(0, 15) + ".mp4";
        char args[2048];
        snprintf(args, sizeof args, "\"%s\" -hide_banner -loglevel warning -y -f concat -safe 0 -i list.txt -c copy -movflags +faststart \"%s\"",
                 job->ffmpeg.c_str(), out.c_str());
        STARTUPINFOA si = {};
        si.cb = sizeof si;
        PROCESS_INFORMATION pi = {};
        DWORD code = 1;
        if (CreateProcessA(nullptr, args, nullptr, nullptr, FALSE, CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS, nullptr,
                           snap.c_str(), &si, &pi)) {
            CloseHandle(pi.hThread);
            if (WaitForSingleObject(pi.hProcess, 30000) != WAIT_OBJECT_0) TerminateProcess(pi.hProcess, 1);
            GetExitCodeProcess(pi.hProcess, &code);
            CloseHandle(pi.hProcess);
        }
        for (size_t i = 0; i < pieces.size(); i++) {
            char nm[32];
            snprintf(nm, sizeof nm, "\\p%02zu.ts", i);
            DeleteFileA((snap + nm).c_str());
        }
        DeleteFileA((snap + "\\list.txt").c_str());
        RemoveDirectoryA(snap.c_str());
        WIN32_FILE_ATTRIBUTE_DATA fa{};
        const double mb = GetFileAttributesExA(out.c_str(), GetFileExInfoStandard, &fa)
                              ? (double)(((uint64_t)fa.nFileSizeHigh << 32) | fa.nFileSizeLow) / 1048576.0 : 0;
        if (code || mb <= 0) logf("clip: the last seconds were not saved (ffmpeg exited with %lu)", code);
        else logf("clip: saved the last seconds: %s (%zu pieces of 2 s, %.1f MB)", out.c_str(), pieces.size(), mb);
    }).detach();
    return true;
}

void finish(int64_t end_index) {
    std::shared_ptr<Job> job;
    {
        std::lock_guard<std::mutex> lk(current_mu);
        job.swap(current);
    }
    if (!job) return;
    job->end_index.store(end_index);
    // (after the copies still running: they insert before the finish, which the writer takes last)
    std::thread([job] {
        for (int i = 0; i < 200; i++) {
            bool any = false;
            for (auto& b : busy_) any |= b.load();
            if (!any) break;
            Sleep(10);
        }
        {
            std::lock_guard<std::mutex> lk(job->wmu);
            job->wq.push_back({true, 0, -1});
        }
        job->wcv.notify_one();
    }).detach();
}

bool failed() {
    std::lock_guard<std::mutex> lk(current_mu);
    return current && current->failed.load();
}

}  // namespace mkwvk::clip
#else   // (Linux: clips need the Windows pipe and process calls above; none are recorded)
namespace mkwvk::clip {
static std::atomic<bool> busy_[kReadbacks];
Settings settings() { return {}; }
bool start(uint32_t, uint32_t, const Settings&, bool) { logf("clip: clips are recorded on Windows only"); return false; }
bool save_last() { return false; }
bool frame(int64_t, int rb, const uint8_t*) { busy_[rb].store(false); return false; }
std::atomic<bool>* busy() { return busy_; }
void finish(int64_t) {}
bool failed() { return false; }
}  // namespace mkwvk::clip
#endif
