# Copyright (C) 2026 Sacrappy

"""make_game.py --progress-lines / --prepare / --game: the build without a window, for RecompLauncher's setup step
(recomplauncher.json "prepare"). Lines `progress <0 to 1> <text>`, the builder window's @@ lines left out, exit 0 when it worked and a
clear last line with a nonzero exit when not. No game and no build: the stages are said as a build says them.

Run with: python -I -B tests/test_progress_lines.py
"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ENGINE = Path(__file__).resolve().parents[1]
SOURCE = ENGINE / "nx2" / "make_game.py"


def load():
    spec = importlib.util.spec_from_file_location("make_game_progress_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def said(make_game, events):
    out = io.StringIO()
    make_game._bar.update(stage="read", shown=-1.0, text="")
    with contextlib.redirect_stdout(out):
        for e in events:
            make_game.progress_say(*e)
    return out.getvalue().splitlines()


class ProgressLinesTests(unittest.TestCase):
    def setUp(self):
        self.mg = load()

    def test_a_build_reads_as_progress_lines(self):
        lines = said(self.mg, [("stage", "read", "Reading your game files"), ("progress", "0.5"), ("stage", "lift", "Translating the game to PC code"),
                               ("stage", "compile", "Compiling"), ("progress", "0.000"), ("progress", "0.500"), ("progress", "1.000"),
                               ("stage", "finish", "Putting the game folder together"), ("times", "100", "60", "-1"), ("done", "C:/game")])
        for line in lines:
            self.assertRegex(line, r"^progress (0\.\d{3}|1\.000) \S.*$")
            self.assertFalse(line.startswith("@@"))
        values = [float(l.split()[1]) for l in lines]
        self.assertEqual(values, sorted(values), "the bar never goes back")
        self.assertEqual(lines[0], "progress 0.000 Reading your game files")
        self.assertEqual(lines[-1], "progress 1.000 Your game is ready")
        self.assertIn("progress 0.250 Compiling", lines)
        self.assertTrue(any(l.endswith("Compiling") and 0.5 < float(l.split()[1]) < 0.7 for l in lines))   # (half of the compile)

    def test_a_progress_that_changes_nothing_is_not_said_again(self):
        lines = said(self.mg, [("stage", "compile", "Compiling")] + [("progress", "0.0001")] * 50)
        self.assertEqual(lines, ["progress 0.250 Compiling"])

    def test_check_and_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            lines = said(self.mg, [("check", "ok", "This is the version HeyListen supports (build e0f3ccfa)."),
                                   ("error", "The build failed: no compiler.")])
        self.assertEqual(lines, ["This is the version HeyListen supports (build e0f3ccfa).", "error: The build failed: no compiler."])
        self.assertEqual(err.getvalue().strip(), "error: The build failed: no compiler.")

    def test_without_the_flag_the_window_lines_are_unchanged(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mg.say("stage", "lift", "Translating the game to PC code")
            self.mg.say("progress", "0.250")
        self.assertEqual(out.getvalue().splitlines(), ["@@stage lift Translating the game to PC code", "@@progress 0.250"])

    def test_a_steps_window_lines_are_not_printed_in_this_mode(self):
        script = "import sys; print('@@file code_001.c 12 new'); print('3 files, 1 to compile'); print('@@progress 0.5')"
        for mode, expected in ((True, ["3 files, 1 to compile"]), (False, ["@@file code_001.c 12 new", "3 files, 1 to compile", "@@progress 0.5"])):
            self.mg.PROGRESS_LINES = mode
            seen = []
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.mg.run([sys.executable, "-c", script], on_line=seen.append, cwd=str(ENGINE))
            self.mg.PROGRESS_LINES = False
            self.assertEqual(out.getvalue().splitlines(), expected)
            self.assertEqual(len(seen), 3, "the build still reads every line")

    def test_game_is_a_nup_or_a_folder(self):
        def args(**kw):
            a = type("A", (), dict(game=None, nup=None, romfs=None, exefs=None))()
            a.__dict__.update(kw)
            return a
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            nup = tmp / "game.nup"
            nup.write_bytes(b"x")
            a = args(game=str(nup))
            self.mg.resolve_game(a)
            self.assertEqual(a.nup, str(nup))
            (tmp / "dump" / "romfs").mkdir(parents=True)
            (tmp / "dump" / "exefs").mkdir()
            a = args(game=str(tmp / "dump"))
            self.mg.resolve_game(a)
            self.assertEqual((a.nup, a.romfs, a.exefs), (None, str(tmp / "dump" / "romfs"), str(tmp / "dump" / "exefs")))
            (tmp / "one").mkdir()
            (tmp / "one" / "a.nup").write_bytes(b"x")
            a = args(game=str(tmp / "one"))
            self.mg.resolve_game(a)
            self.assertEqual(a.nup, str(tmp / "one" / "a.nup"))
            for bad in (tmp / "missing.nup", tmp / "empty"):
                (tmp / "empty").mkdir(exist_ok=True)
                with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                    self.mg.resolve_game(args(game=str(bad)))
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                self.mg.resolve_game(args(game=str(nup), nup="other.nup"))

    def test_the_language_is_one_of_the_games(self):
        codes = {c for c, _ in self.mg.LANGUAGES}
        self.assertIn(self.mg.system_language(), codes)

    def test_prepare_leaves_the_window_things_off(self):
        old = sys.argv
        try:
            sys.argv = ["make_game.py", "--prepare", "--game", "g.nup", "--out", "o"]
            a = self.mg.parse_args()
            keep = {k: os.environ.get(k) for k in ("EMSDK", "RX_CPM", "WGPU_NATIVE", "HOOKSHOT_NGX_SDK")}
            os.environ["HOOKSHOT_NGX_SDK"] = "C:/a-sdk-the-pc-had"
            try:
                self.mg.prepare_setup(a)
                self.assertTrue(self.mg.PROGRESS_LINES)
                self.assertIs(self.mg.say, self.mg.progress_say, "fail() and every step now say progress lines")
                self.assertFalse(a.no_prepare_shaders, "--prepare prepares the shaders as its last step, with its progress")
                self.assertNotIn("HOOKSHOT_NGX_SDK", os.environ, "a build is with DLSS only when asked")
                self.assertIn(a.language, {c for c, _ in self.mg.LANGUAGES})
            finally:
                self.mg.PROGRESS_LINES = False
                for k, v in keep.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        finally:
            sys.argv = old

    def test_a_failed_prepare_says_why_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-I", "-B", str(SOURCE), "--prepare", "--game", os.path.join(tmp, "none.nup"), "--out", os.path.join(tmp, "o"),
                                "--work", os.path.join(tmp, "w")], capture_output=True, text=True, cwd=str(ENGINE))
        self.assertEqual(r.returncode, 1)
        last = r.stdout.strip().splitlines()[-1]
        self.assertTrue(last.startswith("error: Your game files were not found at "), last)
        self.assertNotIn("@@", r.stdout)

    def test_a_game_that_is_not_the_supported_build_is_refused_with_a_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, "dump")
            os.makedirs(empty)
            r = subprocess.run([sys.executable, "-I", "-B", str(SOURCE), "--prepare", "--game", empty, "--out", os.path.join(tmp, "o"),
                                "--work", os.path.join(tmp, "w")], capture_output=True, text=True, cwd=str(ENGINE))
        self.assertEqual(r.returncode, 1)
        self.assertRegex(r.stdout.strip().splitlines()[-1], r"^error: .*is not a \.nup file, and has no romfs and exefs folders")


if __name__ == "__main__":
    unittest.main()
