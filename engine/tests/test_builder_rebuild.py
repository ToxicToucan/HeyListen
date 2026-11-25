# Copyright (C) 2026 Sacrappy

"""Game-free source check of the Builder window's NVIDIA DLSS and AMD FSR 4 ticks on a game folder that is already built (issue 130).

A game built without DLSS, then the box ticked and the Builder run again at the same version: the window was in "Play" mode, which
ignores the ticks, and an Update only kept what the folder had. The window now compares its ticks with what the folder was built
with (the marks nx2/make_game.py leaves, GameFeatures.cs) and offers Rebuild; Update and Rebuild build with the ticks as they
are, FSR 4 unticked builds without it. The C# is not compiled here: this reads its text and checks that it agrees with make_game.py
and the renderer.

Run with: python -I -B tests/test_builder_rebuild.py
"""
import importlib.util
from pathlib import Path
import re
import unittest

ENGINE = Path(__file__).resolve().parents[1]
B = ENGINE / "builder" / "HookshotBuilder"


def text(name):
    return (B / name).read_text(encoding="utf-8")


def body(source, start):
    at = source.index(start)
    return source[at:source.index("\n    }\n", at)]


def make_game():
    spec = importlib.util.spec_from_file_location("make_game_rebuild_under_test", ENGINE / "nx2" / "make_game.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuilderRebuild(unittest.TestCase):
    def test_a_built_folder_with_other_ticks_is_not_play(self):
        cs = text("MainWindow.xaml.cs")
        self.assertIn("enum Mode { Build, UpdateGame, UpToDate, UpdateBuilder, Rebuild }", cs)
        refresh = body(cs, "void RefreshMode()")
        # (the same version: the ticks against the folder, Rebuild when they differ, Play only when they agree)
        self.assertIn("GameFeatures.Changes(OutPath.Text, DlssTick.IsChecked == true, FsrTick.IsChecked == true)", refresh)
        self.assertIn("rebuildChanges.Count > 0 ? Mode.Rebuild : Mode.UpToDate", refresh)
        self.assertIn('BuildButton.Content = "Rebuild"', refresh)

    def test_changing_a_tick_changes_the_button(self):
        cs = text("MainWindow.xaml.cs")
        for name in ("void DlssChanged(", "void FsrChanged("):
            self.assertIn("RefreshMode();", body(cs, name), name)

    def test_rebuild_builds_with_the_ticks_and_downloads_nothing(self):
        cs = text("MainWindow.xaml.cs")
        start = body(cs, "async void StartBuild(")
        rebuild = start[start.index("case Mode.Rebuild:"):]
        rebuild = rebuild[:rebuild.index("case Mode.UpToDate:")]
        self.assertIn("(PendingDlss, PendingFsr) = (DlssTick.IsChecked == true, FsrTick.IsChecked == true);", rebuild)
        self.assertIn("UpdateBuild(rebuildTools, OutPath.Text);", rebuild)
        self.assertNotIn("Updater.UpdateFolder", rebuild)
        update = start[start.index("case Mode.UpdateGame:"):start.index("case Mode.Rebuild:")]
        self.assertIn("PendingDlss", update)   # (an Update at a newer version takes the ticks too)

    def test_update_build_takes_the_ticks_over_what_the_folder_had(self):
        cs = text("MainWindow.xaml.cs")
        build = cs[cs.index("internal void UpdateBuild("):]
        build = build[:build.index("Run(tools, ShaderStep.Args(args, prepareNow)")]
        self.assertIn("bool? dlssChoice = PendingDlss, fsrChoice = PendingFsr;", build)
        self.assertIn("var dlss = dlssChoice ??", build)
        self.assertIn("if (fsrChoice == true) args.AddRange(FsrArgs);", build)
        self.assertIn('else if (fsrChoice == false) args.Add("--no-fsr");', build)
        # (the launcher's updates have no ticks: the folder as it was built, as before)
        self.assertIn("nvngx_dlss.dll", build)
        self.assertIn("LICENSE-AMD-FSR-SDK.md", build)
        self.assertIn("PendingDlss = PendingFsr = null;", build)

    def test_a_newer_builder_started_by_the_update_has_the_ticks(self):
        updater = text("Updater.cs")
        self.assertEqual(updater.count('ArgumentList.Add("--ticks")'), 2)
        self.assertIn('a[i] == "--ticks"', updater)
        cs = text("MainWindow.xaml.cs")
        self.assertIn("UseSavedTicks && BuilderSettings.Load(SettingsPath)", cs)

    def test_the_marks_are_make_games(self):
        features = text("GameFeatures.cs")
        mg = make_game()
        self.assertIn('DlssMark = "%s"' % mg.NGX_MARK, features)
        self.assertIn('FsrMark = "%s"' % mg.FSR_MARK, features)
        self.assertIn("File.Exists(Path.Combine(folder, DlssMark))", features)
        self.assertIn("File.Exists(Path.Combine(folder, FsrMark))", features)

    def test_a_folder_from_before_the_mark_is_read_from_its_renderer(self):
        """Issue 130's folder: NVIDIA's runtime beside a renderer built without DLSS. The renderer says: the text the builder looks
        for is the one gpu/vk/vk_dlss.cpp logs only when NGX is compiled in"""
        features = text("GameFeatures.cs")
        needle = re.search(r'GetBytes\("([^"]+)"\)', features).group(1)
        dlss = (ENGINE / "gpu" / "vk" / "vk_dlss.cpp").read_text(encoding="utf-8")
        ngx_part, stubs = dlss.split("#else  // (no NGX SDK in this build", 1)
        self.assertIn(needle, ngx_part)
        self.assertNotIn(needle, stubs)
        self.assertIn('Path.Combine(folder, "bin", "hookshot_vk.dll")', features)
        self.assertIn('Path.Combine(folder, "hookshot_vk.dll")', features)


if __name__ == "__main__":
    unittest.main()
