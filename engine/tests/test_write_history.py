"""The GPU buffer-write history is bounded, ordered and published only when the GPU is done (gpu/vk/gpu_write_history.h; no game/GPU).

gpu_wrote_into asked a std::map of written ranges, which dropped its lowest-addressed range past 65,536 of them (a write a texture
should have been uploaded again for was forgotten), counted a write recorded before a draw into a texture as one that replaces
the drawn pixels, and noted a write as seen before the upload that took it had succeeded. GpuWriteHistory keeps at most 65,536
exact ranges pending and as many published, with a conservative floor beyond, orders a drawn image's draws and the writes by
(submission, segment, order), and publishes a submission's ranges when the GPU completes it.

Compiles tests/test_write_history.cpp (the PR's fixture: 196,608 disjoint writes, many outstanding timeline values, peak
allocations, neighbouring GPU-owned images, writes on either side of a draw in one submission) and checks that vk_texture.cpp uses
the history as the design needs, with RX_VK_WRITE_HISTORY=0 keeping the map:
- a write is recorded with its submission and segment; a drawn texture is noted at each draw (note_drawn_storage), watched at
  creation and forgotten when its texture goes;
- the writes of finished submissions are published at each present (texture_frame) and by the replay every 32 recordings;
- gpu_wrote_into asks for the newest write of the texture's bytes, as a drawn image's when the GPU drew it, and answers 0 for one
  it has been uploaded after; texture_touch notes it as seen only after the upload succeeded.

python -I -B tests/test_write_history.py [--cxx clang++]

Ported from Longshot 7daafa7a (#1553, HumanBean), 4ef972c6 and fdee2857; the source checks are HeyListen's.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from srccut import block

RUN_ALL_ARGS = ["--cxx", "{cxx}"]


def check_source():
    tex = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    wrote = block(tex, "void texture_gpu_wrote(uint64_t gpu, uint64_t size) {")
    if "gpu_hist.record(lo, hi, rec_value, tl_seg);" not in wrote or "std::lock_guard lk(gpu_wrote_mu);" not in wrote:
        raise SystemExit("texture_gpu_wrote no longer records a write with its submission and segment under the lock")
    if "gpu_wrote[lo] = {hi, rec_value};" not in wrote:
        raise SystemExit("texture_gpu_wrote lost the RX_VK_WRITE_HISTORY=0 map")
    into = block(tex, "uint64_t gpu_wrote_into(Texture& tex) {")
    for needle in ("gpu_hist.newest(lo, hi, tex.gpu_written ? tex.id : 0)", "newest <= tex.gpu_wrote_seen", "if (write_history_on())"):
        if needle not in into:
            raise SystemExit("gpu_wrote_into's walk no longer has: " + needle)
    if "tex.gpu_wrote_seen = newest;" not in into[into.index("if (gpu_wrote.empty())"):]:
        raise SystemExit("the map path no longer notes the write as seen (RX_VK_WRITE_HISTORY=0)")
    if "tex.gpu_wrote_seen = newest;" in into[:into.index("if (gpu_wrote.empty())")]:
        raise SystemExit("the history path notes a write as seen before the upload took it")
    frame = block(tex, "void texture_frame(uint64_t completed) {")
    complete = block(tex, "void texture_complete_writes(uint64_t completed) {")
    if "texture_complete_writes(completed);" not in frame or "gpu_hist.complete(completed);" not in complete:
        raise SystemExit("completed writes are not published at the frame boundary")
    replay = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    if "texture_complete_writes(done);" not in replay or "& 31) == 31" not in replay:
        raise SystemExit("the replay no longer publishes completed writes every 32 recordings")
    if "note_drawn_storage(tex);" not in block(tex, "void texture_written(Texture* tex, const char* why) {"):
        raise SystemExit("a draw into a texture is no longer noted in the write history")
    if "gpu_hist.forget(tex->id, tex->gpu, tex->gpu + tex->guest_size);" not in block(tex, "void forget_gpu(Texture* tex) {"):
        raise SystemExit("a destroyed texture is not forgotten by the write history")
    if "gpu_hist.watch(p->id, p->gpu, p->gpu + p->guest_size);" not in tex:
        raise SystemExit("a new texture is not watched by the write history")
    touch = block(tex, "Texture* texture_touch(Texture* tex, bool for_sampling) {")
    if "if (up_ok) {\n        if (gpu_dirty) tex->gpu_wrote_seen = gpu_dirty;" not in touch:
        raise SystemExit("a failed upload must leave the write available for a retry")
    drawn = block(tex, "static void note_drawn_storage(Texture* tex) {")
    if "write_history_on()" not in drawn or "gpu_hist.drew(tex->id, tex->gpu, end, rec_value, tl_seg);" not in drawn:
        raise SystemExit("note_drawn_storage lost its switch or its draw note")
    if "gpu_hist_records.fetch_add(1" not in wrote:
        raise SystemExit("a recorded write no longer counts, so a draw after it would be skipped as already noted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="write-history-") as tmp:
        exe = Path(tmp) / "write-history"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(ROOT / "tests/test_write_history.cpp"), "-o", str(exe)],
                       check=True)
        subprocess.run([str(exe)], check=True)
    print("PASS: the write history is bounded, ordered and published on completion")


if __name__ == "__main__":
    main()
