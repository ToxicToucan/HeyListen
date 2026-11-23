# Copyright (C) 2026 Sacrappy

"""Game-free source check of the Builder window's AMD FSR 4 tick: ticking it shows AMD's license (and the tick stays only when it
is accepted, as NVIDIA DLSS's does), the build gets the same arguments as recomplauncher.json's fsr4 option, make_game.py takes
them, and the fetch folder reaches the build (HOOKSHOT_FFX_SDK). AMD's files are never in the repository.

Run with: python -I -B tests/test_builder_fsr_fetch.py
"""
import json
from pathlib import Path
import re
import sys
import unittest

ENGINE = Path(__file__).resolve().parents[1]
REPO = ENGINE.parent
B = ENGINE / "builder" / "HookshotBuilder"


def text(name):
    return (B / name).read_text(encoding="utf-8")


class BuilderFsrFetch(unittest.TestCase):
    def test_tick_passes_the_fetch_arguments(self):
        cs = text("MainWindow.xaml.cs")
        m = re.search(r"FsrArgs\s*=\s*\{([^}]*)\}", cs)
        self.assertTrue(m, "FsrArgs not found")
        args = re.findall(r'"([^"]+)"', m.group(1))
        self.assertEqual(args, ["--fsr", "--fetch-fsr-runtime", "--accept-amd-license"])
        self.assertIn("if (FsrTick.IsChecked == true) args.AddRange(FsrArgs);", cs)

    def test_same_as_the_launchers_option(self):
        manifest = json.loads((REPO / "recomplauncher.json").read_text(encoding="utf-8"))
        opts = [o for o in self.options(manifest) if o.get("id") == "fsr4"]
        self.assertEqual(len(opts), 1)
        cs = text("MainWindow.xaml.cs")
        for a in opts[0]["args"]:
            self.assertIn('"%s"' % a, cs)

    @staticmethod
    def options(node):
        found = []
        if isinstance(node, dict):
            if "args" in node and "id" in node:
                found.append(node)
            for v in node.values():
                found += BuilderFsrFetch.options(v)
        elif isinstance(node, list):
            for v in node:
                found += BuilderFsrFetch.options(v)
        return found

    def test_license_is_shown_and_must_be_accepted(self):
        cs, xaml = text("MainWindow.xaml.cs"), text("MainWindow.xaml")
        self.assertIn('Click="FsrChanged"', xaml)
        self.assertNotIn("see the readme", xaml.split('x:Name="FsrTick"', 1)[1].split("/>", 1)[0])
        body = cs.split("void FsrChanged(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("MessageBoxButton.YesNo", body)
        self.assertIn("FsrTick.IsChecked = false", body)
        sys.path.insert(0, str(ENGINE / "scripts"))
        import ffx_sdk
        self.assertIn(ffx_sdk.LICENSE_URL, cs)

    def test_fetch_folder_reaches_the_build(self):
        self.assertIn("HOOKSHOT_FFX_SDK", text("Runner.cs"))
        self.assertIn("FfxSdk", text("Storage.cs"))
        self.assertIn("Storage.FfxSdk", text("MainWindow.xaml.cs"))
        self.assertIn("Storage.FfxSdk", text("HeadlessCli.cs"))

    def test_make_game_takes_the_arguments(self):
        mg = (ENGINE / "nx2" / "make_game.py").read_text(encoding="utf-8")
        for flag in ("--fsr", "--fetch-fsr-runtime", "--accept-amd-license"):
            self.assertIn('"%s"' % flag, mg)


if __name__ == "__main__":
    unittest.main()
