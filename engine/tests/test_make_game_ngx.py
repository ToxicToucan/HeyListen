"""nx2/make_game.py and NVIDIA DLSS: the runtime placed in the game folder's ngx folder (never beside the exe), and the
fetch the Hookshot Builder's DLSS tick asks for, which needs NVIDIA's license accepted. No network, no game.

Run with: python -I tests/test_make_game_ngx.py
"""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("make_game_ngx_under_test", ROOT / "nx2" / "make_game.py")
make_game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_game)


class NgxRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hookshot-make-game-ngx-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sdk = self.root / "sdk"
        self.out = self.root / "game"
        (self.out / "bin").mkdir(parents=True)
        self.ngx = make_game.ngx_script()

    def sdk_files(self, platform, runtime=True):
        lib = Path(self.ngx.library(str(self.sdk), platform))
        lib.parent.mkdir(parents=True, exist_ok=True)
        lib.write_bytes(b"lib")
        if runtime:
            rt = Path(self.ngx.runtime(str(self.sdk), platform))
            rt.parent.mkdir(parents=True, exist_ok=True)
            rt.write_bytes(b"runtime")

    def place(self, platform, sdk=None):
        env = {"HOOKSHOT_NGX_SDK": str(self.sdk) if sdk is None else sdk}
        with mock.patch.object(make_game, "LINUX", platform == "linux"), mock.patch.dict(os.environ, env), \
                mock.patch("sys.stdout", io.StringIO()):
            return make_game.place_ngx_runtime(str(self.out))

    def test_windows_runtime_goes_to_the_ngx_folder(self):
        self.sdk_files("windows")
        dst = self.place("windows")
        self.assertEqual(Path(dst), self.out / "ngx" / "nvngx_dlss.dll")
        self.assertEqual(Path(dst).read_bytes(), b"runtime")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["bin", "ngx"])
        self.assertEqual(list((self.out / "bin").iterdir()), [])   # (nothing beside the exe)
        self.assertFalse([p for p in self.out.rglob("*") if p.name.lower() == "_nvngx.dll"])

    def built(self, platform, sdk=None):
        env = {"HOOKSHOT_NGX_SDK": str(self.sdk) if sdk is None else sdk}
        with mock.patch.object(make_game, "LINUX", platform == "linux"), mock.patch.dict(os.environ, env):
            return make_game.ngx_built()

    def test_built_with_this_platforms_library(self):
        # (the camera jitter is no longer tied to this: nx2/oot_jitter.py is in every standalone lift, for FSR 3.1 as well)
        self.assertFalse(self.built("linux", sdk=""))
        self.assertFalse(self.built("linux"))   # (no library yet)
        self.sdk_files("linux", runtime=False)
        self.assertTrue(self.built("linux"))
        self.assertFalse(self.built("windows"))   # (another platform's library only)

    def test_fsr_build(self):
        """--fsr: HOOKSHOT_FSR=1 for nx2/build.py; an --update of that folder keeps it (its mark), --no-fsr takes it out. (The
        camera jitter FSR 4 needs is in every build: nx2/lift.py)"""
        out = str(self.out)
        mark = self.out / make_game.FSR_MARK
        plain = SimpleNamespace(fsr=False, no_fsr=False, update=None)
        self.assertFalse(make_game.fsr_wanted(plain, out))
        self.assertTrue(make_game.fsr_wanted(SimpleNamespace(fsr=True, no_fsr=False, update=None), out))
        update = SimpleNamespace(fsr=False, no_fsr=False, update=out)
        self.assertFalse(make_game.fsr_wanted(update, out))
        mark.write_text("")
        self.assertTrue(make_game.fsr_wanted(update, out))
        self.assertFalse(make_game.fsr_wanted(SimpleNamespace(fsr=False, no_fsr=True, update=out), out))
        self.assertFalse(make_game.fsr_wanted(plain, out))   # (a new build without --fsr: the mark is for --update only)

    def test_linux_runtime(self):
        self.sdk_files("linux")
        self.assertEqual(Path(self.place("linux")), self.out / "ngx" / "libnvidia-ngx-dlss.so.310.9.16")

    def test_nothing_without_the_sdk_or_its_platform(self):
        self.assertIsNone(self.place("windows", sdk=""))
        self.sdk_files("linux")
        self.assertIsNone(self.place("windows"))   # (a Linux SDK folder: no Windows library, no DLSS in this build)
        self.sdk_files("windows", runtime=False)
        self.assertIsNone(self.place("windows"))
        self.assertFalse((self.out / "ngx").exists())


class FinishNgxTests(unittest.TestCase):
    """The game folder says what its renderer was built with, and a build without DLSS leaves none of NVIDIA's runtime behind
    (issue 130: a folder built without DLSS, then ticked, kept a runtime and a menu with no DLSS)"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hookshot-finish-ngx-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sdk = self.root / "sdk"
        self.out = self.root / "game"
        (self.out / "ngx").mkdir(parents=True)
        self.ngx = make_game.ngx_script()
        self.platform = make_game.ngx_platform()

    def sdk_files(self):
        for path in (self.ngx.library(str(self.sdk), self.platform), self.ngx.runtime(str(self.sdk), self.platform)):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"x")

    def finish(self, sdk=True):
        env = {"HOOKSHOT_NGX_SDK": str(self.sdk) if sdk else ""}
        with mock.patch.dict(os.environ, env), mock.patch("sys.stdout", io.StringIO()) as out:
            return make_game.finish_ngx(str(self.out)), out.getvalue()

    def test_a_build_with_the_sdk_places_the_runtime_and_the_mark(self):
        self.sdk_files()
        built, _ = self.finish()
        self.assertTrue(built)
        self.assertTrue((self.out / make_game.NGX_MARK).is_file())
        self.assertTrue((self.out / "ngx" / self.ngx.runtime_name(self.platform)).is_file())

    def test_a_build_without_it_takes_the_mark_and_the_runtime_away(self):
        (self.out / make_game.NGX_MARK).write_bytes(b"")
        for kind in self.ngx.RUNTIMES:
            (self.out / "ngx" / self.ngx.runtime_name(self.platform, kind)).write_bytes(b"x")
        (self.out / "ngx" / "ngx.log").write_text("NGX's own")
        built, said = self.finish(sdk=False)
        self.assertFalse(built)
        self.assertFalse((self.out / make_game.NGX_MARK).exists())
        self.assertEqual(sorted(p.name for p in (self.out / "ngx").iterdir()), ["ngx.log"])
        self.assertIn("not in this build", said)

    def test_a_sdk_folder_without_the_library_is_not_a_build_with_dlss(self):
        Path(self.ngx.runtime(str(self.sdk), self.platform)).parent.mkdir(parents=True)
        Path(self.ngx.runtime(str(self.sdk), self.platform)).write_bytes(b"x")   # (the runtime alone)
        built, _ = self.finish()
        self.assertFalse(built)
        self.assertFalse((self.out / make_game.NGX_MARK).exists())


class FetchGateTests(unittest.TestCase):
    def fetch(self, accept, sdk="sdk", makes_library=True):
        calls = []
        library = os.path.join(tempfile.gettempdir(), "hookshot-fetch-gate-%d.lib" % os.getpid())

        def fetched(*a, **k):
            calls.append(a)
            if makes_library:
                open(library, "wb").close()

        if os.path.exists(library):
            os.remove(library)
        self.addCleanup(lambda: os.path.exists(library) and os.remove(library))
        ngx = SimpleNamespace(NOTICE="notice", fetch=fetched, library=lambda *a: library)
        with mock.patch.object(make_game, "ngx_script", return_value=ngx), mock.patch.dict(os.environ, {"HOOKSHOT_NGX_SDK": sdk}), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            try:
                make_game.fetch_ngx_sdk(SimpleNamespace(accept_nvidia_license=accept))
            except SystemExit as e:
                return calls, e.code, out.getvalue()
        return calls, 0, out.getvalue()

    def test_needs_the_license_accepted(self):
        calls, code, out = self.fetch(False)
        self.assertEqual((calls, code), ([], 1))
        self.assertIn("@@error", out)
        self.assertIn("license", out)

    def test_needs_the_sdk_folder(self):
        calls, code, _ = self.fetch(True, sdk="")
        self.assertEqual((calls, code), ([], 1))

    def test_fetches_this_platforms_files_after_the_notice(self):
        calls, code, out = self.fetch(True)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("sdk", make_game.ngx_platform())])
        self.assertTrue(out.startswith("notice"))

    def test_a_fetch_that_leaves_no_library_stops_the_build(self):
        """The box was ticked, the SDK folder cannot build DLSS (no library): the build says so and stops. It used to go on and
        come out without DLSS, saying nothing the player could see (issue 130)"""
        calls, code, out = self.fetch(True, makes_library=False)
        self.assertEqual(code, 1)
        self.assertIn("@@error", out)
        self.assertIn("NVIDIA DLSS", out)
        self.assertIn("untick", out)

    def test_flags_are_off_by_default(self):
        with mock.patch("sys.argv", ["make_game.py", "--out", "x", "--nup", "y"]):
            a = make_game.parse_args()
        self.assertFalse(a.fetch_ngx_sdk)
        self.assertFalse(a.accept_nvidia_license)


class NoScriptTests(unittest.TestCase):
    """The AppImage carries no scripts/ (scripts/linux/appimage.sh): with HOOKSHOT_NGX_SDK set there, ngx_script() says so and
    stops, not a FileNotFoundError traceback from importlib"""

    def test_missing_script_is_said_plainly(self):
        with tempfile.TemporaryDirectory(prefix="hookshot-no-scripts-") as root, mock.patch.object(make_game, "ROOT", root), \
                mock.patch.dict(os.environ, {"HOOKSHOT_NGX_SDK": os.path.join(root, "sdk")}), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            with self.assertRaises(SystemExit) as stopped:
                make_game.place_ngx_runtime(os.path.join(root, "game"))
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("@@error", out.getvalue())
        self.assertIn("ngx_sdk.py", out.getvalue())
        self.assertIn("Unset HOOKSHOT_NGX_SDK", out.getvalue())


if __name__ == "__main__":
    unittest.main()
