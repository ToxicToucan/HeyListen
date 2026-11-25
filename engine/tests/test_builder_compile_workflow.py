# Copyright (C) 2026 Sacrappy

"""The Builder's compile workflow (.github/workflows/builder-compile.yml) keeps its shape: a Windows job that only builds the
Builder project in Release, on the same pinned actions as release.yml, with no secret, no upload, no publish, no write
permission, and only for changes to engine/builder or the workflow itself (and by hand). Game-free.

Run with: python -I -B tests/test_builder_compile_workflow.py
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
text = (WORKFLOWS / "builder-compile.yml").read_text(encoding="utf-8")
release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")


class BuilderCompileWorkflow(unittest.TestCase):
    def test_runs_only_for_the_builder_and_by_hand(self):
        on = text[text.index("\non:\n"):text.index("\npermissions:")]
        self.assertEqual(on.count("- 'engine/builder/**'"), 2)   # (push and pull_request)
        self.assertEqual(on.count("- '.github/workflows/builder-compile.yml'"), 2)
        self.assertIn("  push:\n    paths:", on)
        self.assertIn("  pull_request:\n    paths:", on)
        self.assertIn("  workflow_dispatch:", on)
        self.assertNotIn("tags:", on)
        self.assertNotIn("release:", on)

    def test_one_windows_job_called_builder_compile(self):
        jobs = text[text.index("\njobs:\n"):]
        self.assertEqual(re.findall(r"^  (\S+):$", jobs, re.M), ["builder-compile"])
        self.assertIn("runs-on: windows-latest", jobs)
        self.assertIn("    name: builder-compile\n", jobs)

    def test_same_pinned_actions_as_the_release(self):
        uses = re.findall(r"uses: (actions/[\w-]+@[0-9a-f]{40}) # (v[\d.]+)", text)
        self.assertEqual([u[0].split("@")[0] for u in uses], ["actions/checkout", "actions/setup-dotnet"])
        for action, version in uses:
            self.assertIn("uses: %s # %s" % (action, version), release)
        self.assertIn("dotnet-version: '8.0.x'", text)
        self.assertEqual(len(re.findall(r"uses:", text)), 2)   # (no other action)

    def test_builds_the_project_in_release_and_nothing_else(self):
        self.assertIn("dotnet build engine/builder/HookshotBuilder/HookshotBuilder.csproj --configuration Release", text)
        for word in ("publish", "upload", "secrets.", "gh release", "contents: write", "GH_TOKEN", "id-token"):
            self.assertNotIn(word, text.split("jobs:", 1)[1], word)
        self.assertIn("permissions: {}", text)
        self.assertIn("contents: read", text)
        self.assertIn("persist-credentials: false", text)

    def test_the_project_is_the_one_the_release_builds(self):
        make_release = (ROOT / "tools" / "release" / "make_release.py").read_text(encoding="utf-8")
        self.assertIn('"HookshotBuilder.csproj"', make_release)
        self.assertTrue((ROOT / "engine" / "builder" / "HookshotBuilder" / "HookshotBuilder.csproj").is_file())


if __name__ == "__main__":
    unittest.main()
