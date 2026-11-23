# Copyright (C) 2026 Sacrappy

"""The storm hold's state machine and its two holds (gpu/vk/storm_hold.h; no game/GPU).

Compiles the header with tests/storm_hold_test.cpp and drives it: the latch engages at RX_VK_STORM_HOLD jobs and not
below, holds RX_VK_STORM_MIN_FRAMES presents, releases only once the queue has gone RX_VK_STORM_QUIET_FRAMES presents
without a push (or, with that 0, below half the engage depth), and re-engages on a second storm.

The source is checked for the wiring the state machine cannot show on its own: the present path (vk_replay.cpp's
do_present) ticks it once a present with the queue's quiet clock, present_image() returns before touching the swapchain
while the presents are held, the draw path (vk_draw.cpp) asks the hold whether draws are what is held, and the switch
RX_VK_STORM_HOLD_PRESENT (on by default) chooses between the two holds.
python -I -B tests/test_storm_hold_present.py [--cxx g++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def function(src, head):
    """The body of head's function, from its name to the next column-0 closing brace"""
    at = src.index(head)
    end = src.index("\n}\n", at)
    return src[at:end + 3]


def check_source():
    hold = (ROOT / "gpu/vk/storm_hold.h").read_text(encoding="utf-8")
    for needle in ("struct StormHold {", "StormHoldEvent tick(", "quiet_frames", "min_frames", "hold_present"):
        if needle not in hold:
            raise SystemExit("storm_hold.h lost the state machine's: " + needle)
    for switch in ("RX_VK_STORM_HOLD", "RX_VK_STORM_MIN_FRAMES", "RX_VK_STORM_QUIET_FRAMES", "RX_VK_STORM_HOLD_PRESENT"):
        if switch not in hold:
            raise SystemExit("storm_hold.h no longer names the switch " + switch)

    replay = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    present = function(replay, "void do_present(")
    order = ["jobs_frame_note(storm_frame)", "storm_hold_tick(jobs_pending(), (int64_t)jobs_last_push_age_frames()",
             "storm_hold_presents_held()"]
    at = -1
    for needle in order:
        nxt = present.find(needle)
        if nxt < 0:
            raise SystemExit("do_present no longer has: " + needle)
        if nxt < at:
            raise SystemExit("do_present's storm hold tick must come before it is read: " + needle)
        at = nxt
    if "if (present_held) shot = false;" not in present:
        raise SystemExit("a held present is still written as a shot, though it never reached the screen")

    image = function(replay, "void present_image(")
    held = image.find("if (storm_hold_presents_held()) return;")
    if held < 0:
        raise SystemExit("present_image no longer withholds the present while the storm hold is on")
    if "vkAcquireNextImageKHR" not in image:
        raise SystemExit("present_image no longer acquires (this check needs updating)")
    if image.index("vkAcquireNextImageKHR") < held:
        raise SystemExit("present_image acquires the swapchain before it asks the storm hold")
    if image.index("vkQueuePresentKHR", image.index("vkAcquireNextImageKHR")) < held:
        raise SystemExit("present_image presents before it asks the storm hold")

    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    if "storm_hold_draws_held()" not in draw:
        raise SystemExit("the draw path no longer asks the storm hold whether the draws are held")
    if "RX_VK_STORM_HOLD_PRESENT" not in draw:
        raise SystemExit("the draw path no longer says which hold RX_VK_STORM_HOLD_PRESENT chooses")
    if 'getenv("RX_VK_STORM_MIN_FRAMES")' in draw:
        raise SystemExit("the draw path reads the hold's floor again: the state machine lives in storm_hold.h")
    if 'getenv("RX_VK_STOLEN_SKIP")' not in draw or 'getenv("RX_VK_DEFER_SKIP")' not in draw:
        raise SystemExit("RX_VK_STOLEN_SKIP / RX_VK_DEFER_SKIP are no longer read beside the hold")

    # (frame interpolation shows a frame on its own thread, a set behind: a frame held when it was recorded is not shown
    # even if the hold lifts before the display thread reaches it, so the first presented frame past a release is clean)
    if "framegen_queue(value, present_held)" not in present:
        raise SystemExit("do_present no longer tells the display thread whether the frame's present is held")
    fg = (ROOT / "gpu/vk/vk_framegen.cpp").read_text(encoding="utf-8")
    if "if (!j.held) present_image(" not in fg:
        raise SystemExit("a frame the storm hold withheld can still be shown by the display thread after the release")

    rules = function(replay, "const StormHoldRules& storm_hold_rules()")
    if 'getenv("RX_VK_STORM_HOLD_PRESENT")' not in rules:
        raise SystemExit("the present hold's switch is not read where the rules are")
    if "hold_present = !getenv(\"RX_VK_STORM_HOLD_PRESENT\")" not in rules:
        raise SystemExit("RX_VK_STORM_HOLD_PRESENT is not on by default")
    tick = function(replay, "StormHoldEvent storm_hold_tick(")
    if "RX_VK_STORM_LIVE" not in rules or "jobs_pending_live(" not in present:
        raise SystemExit("the hold's release no longer counts the jobs a draw asked for lately (RX_VK_STORM_LIVE)")
    if "storm_hold_state.tick(r, pending, push_age, frame, skips, live)" not in tick:
        raise SystemExit("the present path no longer ticks the hold's own state machine")

    docs = (ROOT / "docs/development.md").read_text(encoding="utf-8")
    if "RX_VK_STORM_HOLD_PRESENT" not in docs:
        raise SystemExit("docs/development.md does not name the present hold's switch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="storm-hold-") as tmp:
        tmp = Path(tmp)
        exe = tmp / "storm-hold"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-I", str(ROOT / "gpu/vk"),
                        str(ROOT / "tests/storm_hold_test.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == "__main__":
    main()
