# Copyright (C) 2026 Sacrappy
"""RX_VK_FRAMES_IN_FLIGHT on the readback ring (FIF1), by the text of the source:
- the ring counts frames by the game's own presents and tags an entry at its submission's submit when frames are in flight (rb_clock, rb_tag_for);
- a read waits (bounded) for the replay to have staged its frame (rb_wait_staged) before it picks an entry;
- the light meter's strict wait is NOT turned on by frames in flight (stream R3's 18c7f79ad is not ported);
- the CPU's event signals go in the ordered capture sequence with the submits' captures (CapItem, memory_capture_signal).

python -I -B tests/test_frames_in_flight.py
"""
from pathlib import Path

VK = Path(__file__).resolve().parents[1] / "gpu" / "vk"
rp = (VK / "vk_replay.cpp").read_text(encoding="utf-8")
rb = (VK / "vk_rbring.cpp").read_text(encoding="utf-8")
mem = (VK / "vk_memory.cpp").read_text(encoding="utf-8")


def need(c, msg):
    if not c:
        raise SystemExit("FAIL " + msg)


need("rb_clock.fetch_add(1" in rp and "rb_note_value(value);" in rp, "the game's frame clock and the submit's tag")
need("int64_t rb_tag_for(uint64_t value)" in rp and "const int64_t tag = rb_tag_for(value);" in rb, "entries tagged by the submit's clock")
need("const int64_t c = rb_frame_now();" in rb and "rb_wait_staged(c, config().lag);" in rb, "reads use the clock and wait for the staged frame")
need("2u + (unsigned)std::min(atoi(fk), 3) - 1u" in rb, "lag 2 + (k - 1) with frames in flight unless HL_RB_LAG is set")
need("readback waits for the write-back for the light meter's 2 sizes" not in rp, "no forced strict meter wait")
need("std::deque<CapItem> captures;" in mem and "bool memory_capture_signal(" in mem, "ordered capture sequence")
need("drain(0, j.upto);" in rp, "the present forces only its own frame's stopped work")
print("PASS frames in flight: the ring's clock, the staged wait, no strict meter wait, the ordered captures")
