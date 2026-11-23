# Copyright (C) 2026 Sacrappy

"""--prepare (RecompLauncher's setup): the shaders are prepared as the setup's last step, and the game's @@stage / @@progress lines
become the launcher's `progress <0..1> <text>` lines inside the shaders span, after the build's own stages.

python -I -B tests/test_prepare_progress_lines.py
"""
import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("make_game_prepare_lines", ROOT / "nx2" / "make_game.py")
mg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mg)


class PrepareProgressLinesTests(unittest.TestCase):
    def setUp(self):
        self.old = (mg.PROGRESS_LINES, mg.say, dict(mg._bar))
        mg.PROGRESS_LINES, mg.say = True, mg.progress_say
        mg._bar.update({"stage": "finish", "shown": 0.80, "text": "Putting the game folder together"})

    def tearDown(self):
        mg.PROGRESS_LINES, mg.say = self.old[0], self.old[1]
        mg._bar.clear()
        mg._bar.update(self.old[2])

    def test_the_games_preparation_moves_the_bar_to_the_end(self):
        code = ("print('@@stage shaders Preparing shaders'); print('@@progress 0.500'); print('@@progress 1.000');"
                "print('@@done shaders')")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mg.prepare_shaders([sys.executable, "-I", "-c", code])
        self.assertEqual(rc, 0)
        lines = [l for l in out.getvalue().splitlines() if l.startswith("progress ")]
        lo, hi = mg.PROGRESS_SPANS["shaders"]
        self.assertIn("progress %.3f Preparing shaders" % lo, lines)
        self.assertIn("progress %.3f Preparing shaders" % (lo + (hi - lo) * 0.5), lines)
        self.assertIn("progress %.3f Preparing shaders" % hi, lines)
        self.assertFalse([l for l in out.getvalue().splitlines() if l.startswith("@@")], "no raw @@ line reaches the launcher")

    def test_spans_follow_each_other(self):
        order = ["read", "lift", "compile", "finish", "shaders"]
        spans = [mg.PROGRESS_SPANS[k] for k in order]
        self.assertEqual(spans[0][0], 0.0)
        self.assertEqual(spans[-1][1], 1.0)
        for (a_lo, a_hi), (b_lo, b_hi) in zip(spans, spans[1:]):
            self.assertLess(a_lo, a_hi)
            self.assertEqual(a_hi, b_lo)


if __name__ == "__main__":
    unittest.main()
