"""(stream REC1) The recording state a segment's worker reads: what is per recording or per thread, by the text of the sources.

docs/native-engine/recording-state.md has each piece's note. Here, the pieces already made per recording (Stage A):
- the page table's deferred changes (table_defer, table_pending) are per thread: one recording is one thread's, so a worker's
  texture_uploads_recorded flushes only what its own recording changed, never the replay thread's next recording's;
- the segments' init command buffers are an object of their group (SegTable), held by the group and by a worker while it records a
  segment of it: no global table (seg_init_mu, seg_inits), and nothing replaces it at a join;
- a segment's worker reads its own recording's value and epoch (memory_recording_thread), not the replay thread's current ones;
- (stream SUB1) the capture view, the pool's sync pair and the write-back and synced lists are per submission (MemView);
- the uniform updates' list positions are absolute (ub_base, ub_end, ub_at): a prune moves nothing a recording holds, and the deque is
  read through ub_at only.

python -I -B tests/test_recording_state.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu" / "vk"


def need(ok, why):
    if not ok:
        raise SystemExit("FAIL: " + why)


def main():
    mem = (VK / "vk_memory.cpp").read_text(encoding="utf-8")
    tex = (VK / "vk_texture.cpp").read_text(encoding="utf-8")
    hdr = (VK / "vk_texture.h").read_text(encoding="utf-8")
    rep = (VK / "vk_replay.cpp").read_text(encoding="utf-8")

    need("thread_local std::vector<TableRun> table_pending;" in mem and "thread_local bool table_defer = false;" in mem,
         "the page table's deferred changes are no longer per thread")

    need("seg_init_mu" not in tex and "seg_inits" not in tex, "a global segment init table is back")
    need("struct SegTable {" in tex and "SegTableRef texture_segments_begin(" in tex and "SegTableRef texture_segments_begin(" in hdr,
         "texture_segments_begin does not make a table of its own")
    need("texture_segments_begin(nullptr, 0)" not in rep, "a join clears a table that is no longer global")
    need("tl_seg_table = table.get();" in tex, "a worker does not hold its group's table")
    need(len(re.findall(r"texture_segment_worker\(", rep)) == 4, "a segment's worker is set or cleared in a place not seen here")

    need(re.search(r"\bub_updates\[", rep) is not None and len(re.findall(r"\bub_updates\[", rep)) == 1,
         "the uniform updates are read other than through ub_at")
    need("const UbUpdate& ub_at(size_t pos) { return ub_updates[pos - ub_base]; }" in rep, "ub_at does not subtract ub_base")
    need(rep.count("ub_updates.pop_front();\n        ub_base++;") == 1, "a pop does not move ub_base with it")
    need("if (ub_first_of_recording) ub_first_of_recording--;" not in rep, "a prune moves the recording's first position again")
    # the submission's value and epoch: a segment's worker has its own, the reads go through two accessors
    code = "\n".join(line.split("//")[0] for line in mem.splitlines())
    need(len(re.findall(r"\bmem_rec_value\b", code)) == 4, "mem_rec_value is read other than through rec_value_of() (its declaration, write and accessor)")
    need(len(re.findall(r"\bmirror_epoch\b", code)) == 5, "mirror_epoch is read other than through rec_epoch() (its declaration, bump, memory_epoch and accessor)")
    # (DECOUPLE) the guessed write-backs' snapshots are the submission's own: one map cleared at each submission's start lost a pending one's
    need(code.count("guess_snaps") == 5 and "lists().guess_snaps.try_emplace({p, lo});" in code
         and "sub.guess_snaps.lower_bound({p, 0})" in code and "sub.guess_snaps.clear();" in code,
         "the guessed ranges' snapshots are not kept per submission (MemSub::guess_snaps, taken through lists(), used and emptied by its own end)")
    need(rep.count("memory_recording_thread(j->r->value,") == 2 and rep.count("memory_recording_thread_end();") == 2,
         "a segment's worker does not set and clear its recording's value and epoch (both record paths)")
    need("G.view = memory_view();" in rep and "const MemView seg_view = memory_view();" in rep, "a group does not take the view of the submission it begins in")
    # (stream SUB1) the capture view, the pool's sync pair and the submission's lists
    need("cap_entry_view(page, &old_entry)" in mem,
         "memory_read_as_submitted does not read the capture through the submission's view")
    need(not re.search(r"^std::vector<WriteBack> writebacks;|^std::vector<Pool\*> mirror_wanted, mirror_synced;", mem, re.M),
         "the write-back and synced lists are global again")
    need("lists().writebacks.push_back" in mem and "lists().synced.push_back" in mem, "a binding adds to a list other than its submission's")
    need(code.count("synced_epoch = ") == 2, "a pool's sync pair is set other than through pool_sync_note (its declaration and the note)")
    print("PASS recording state: page table changes per thread, segment init table per group, uniform update positions absolute")


if __name__ == "__main__":
    main()
