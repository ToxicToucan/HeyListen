# Copyright (C) 2026 Sacrappy

"""A movie's end is in every log (host/nn_video.c, #104): the decoder's Flush / Stop and the game being told a picture is
the last one are logged without HOS_VIDEO_LOG (the first 48 of each), so a log of a movie that never ends for the game
(the end credits' freeze) shows which of the two lines it lacks. A source-shape check: the real decode path needs the game.

python -I -B tests/test_movie_end_log.py
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def block(source, marker):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def main():
    source = (ROOT / "host/nn_video.c").read_text(encoding="utf-8")
    failures = []
    flush = block(source, "static void dec_flush(")
    if not re.search(r"video_log\(\) \|\| InterlockedIncrement\(&n\) <= 48\)\s*\n\s*hos_log\(\"video: %s after", flush):
        failures.append("dec_flush does not log its first 48 calls without HOS_VIDEO_LOG")
    props = block(source, "static void dec_properties(")
    if not re.search(r"slot_last\[i\] && \(video_log\(\) \? InterlockedIncrement\(&n\) <= 8 : InterlockedIncrement\(&n\) <= 48\)", props):
        failures.append("dec_properties does not log the first 48 'last picture' answers without HOS_VIDEO_LOG")
    if "the last picture, pts" not in props:
        failures.append("dec_properties lost its 'the last picture' line")
    for line in failures:
        print("FAIL:", line)
    if failures:
        return 1
    print("ok: a movie's Flush/Stop and its 'last picture' answers are in every log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
