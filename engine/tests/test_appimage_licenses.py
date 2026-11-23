# Copyright (C) 2026 Sacrappy

"""The AppImage carries the licences of the third-party parts compiled into hookshot_vk.so: Dear ImGui (MIT), zstd (BSD), the
Libertinus fonts (OFL) and AMD's FSR API headers (MIT, the back end is built with AMD FSR 4), each with its PROVENANCE.md or README.md. Runs the real copy block of scripts/linux/appimage.sh into a temporary
folder; nothing is built.

    python -I -B tests/test_appimage_licenses.py
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WANT = {
    "licenses/imgui/LICENSE.txt": "third_party/imgui/LICENSE.txt",
    "licenses/imgui/PROVENANCE.md": "third_party/imgui/PROVENANCE.md",
    "licenses/zstd/LICENSE": "third_party/zstd/LICENSE",
    "licenses/zstd/PROVENANCE.md": "third_party/zstd/PROVENANCE.md",
    "licenses/libertinus/OFL.txt": "third_party/fonts/libertinus/OFL.txt",
    "licenses/libertinus/PROVENANCE.md": "third_party/fonts/PROVENANCE.md",
    "licenses/fidelityfx/LICENSE.md": "third_party/fidelityfx/LICENSE.md",
    "licenses/fidelityfx/README.md": "third_party/fidelityfx/README.md",
}


class AppImageLicences(unittest.TestCase):
    # (the AppImage is made on Linux; Windows runners have no usable bash for the script's block)
    @unittest.skipUnless(sys.platform.startswith("linux"), "the AppImage is made on Linux")
    def test_copied(self):
        script = (ROOT / "scripts/linux/appimage.sh").read_text(encoding="utf-8")
        start = script.index("# ---- the licences of the third-party parts compiled into hookshot_vk.so")
        end = script.index("# ---- end of the licences ----", start)
        with tempfile.TemporaryDirectory(prefix="hookshot-appimage-licences-") as folder:
            env = dict(os.environ, T=folder)
            result = subprocess.run(["bash", "-eu", "-o", "pipefail", "-c", script[start:end]], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for shipped, source in WANT.items():
                self.assertEqual((Path(folder) / shipped).read_bytes(), (ROOT / source).read_bytes(), shipped)

    def test_block_after_the_deps_licences(self):
        # (cp -a of the deps' licences into an existing folder would nest it: this block must come after)
        script = (ROOT / "scripts/linux/appimage.sh").read_text(encoding="utf-8")
        self.assertLess(script.index('cp -a "$DEPS/licenses" "$T/licenses"'),
                        script.index("# ---- the licences of the third-party parts compiled into hookshot_vk.so"))

    def test_vk_third_party_covered(self):
        # (every third_party folder the Vulkan back end compiles in, from nx2/build.py, has its licence shipped)
        build = (ROOT / "nx2/build.py").read_text(encoding="utf-8") + (ROOT / "nx2/ui_fonts.py").read_text(encoding="utf-8")
        for part, licence in (("imgui", "licenses/imgui/"), ("zstd", "licenses/zstd/"), ("fonts", "licenses/libertinus/"),
                              ("fidelityfx", "licenses/fidelityfx/")):
            if '"third_party", "%s"' % part in build:
                self.assertTrue(any(k.startswith(licence) for k in WANT), part)

    def test_back_end_built_with_fsr(self):
        # (the player's build has no back end to compile: the AppImage's carries AMD FSR 4, from the headers carried above)
        script = (ROOT / "scripts/linux/appimage.sh").read_text(encoding="utf-8")
        self.assertIn("HOOKSHOT_FSR=1", script[:script.index("VK=$VKW")])
        self.assertIn("with AMD FSR 4", script)
        self.assertTrue((ROOT / "third_party/fidelityfx/api/include/ffx_api.h").is_file())


if __name__ == "__main__":
    unittest.main()
