# Copyright (C) 2026 Sacrappy

"""The .nup extraction's progress lines (nx2/nup.py extract(progress=True)): `@@progress <0..1>` by bytes, rising, at most
one per 1%, ending at 1.000; none without progress. make_game.py forwards them to the launcher's bar (--prepare)."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import nup  # noqa: E402


class FakeNup:
    """walk() yields (rel, record) with the size at record[2]; copy_to() writes that many bytes"""

    def __init__(self, sizes):
        self.sizes = sizes

    def walk(self, part):
        for i, size in enumerate(self.sizes[part]):
            yield ("%s/f%03d" % (part, i), (0, 0, size, 0, size))

    def copy_to(self, r, g, rel):
        g.write(b"x" * r[2])


class ExtractProgressTests(unittest.TestCase):
    def run_extract(self, progress):
        fake = FakeNup({"exefs": [100, 300], "romfs": [1000 + 7 * i for i in range(300)]})
        t = {"exefs": "exefs", "romfs": "romfs"}
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(out):
            n, total = nup.extract(fake, t, d, quiet=True, progress=progress)
        return n, total, [l for l in out.getvalue().splitlines() if l.startswith("@@progress ")]

    def test_progress_lines_rise_to_one(self):
        n, total, lines = self.run_extract(True)
        self.assertEqual(n, 302)
        values = [float(l.split()[1]) for l in lines]
        self.assertTrue(values, "no progress line")
        self.assertEqual(values, sorted(values))
        self.assertAlmostEqual(values[-1], 1.0, places=3)
        self.assertLessEqual(len(values), 101)   # (one line per 1% at most)

    def test_no_progress_lines_by_default(self):
        _, _, lines = self.run_extract(False)
        self.assertEqual(lines, [])

    def test_make_game_forwards_the_lines(self):
        src = (ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")
        self.assertIn('"--extract", game, "--progress"]', src)
        self.assertIn('say("progress", line.split()[1])', src)


if __name__ == "__main__":
    unittest.main()
