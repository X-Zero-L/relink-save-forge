import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows bundle contract")
class WindowsBundlePackagingTests(unittest.TestCase):
    def make_launcher_fixture(self, root: Path, bootstrap_body: str) -> Path:
        shutil.copy2(ROOT / "RelinkSaveForge.cmd", root / "RelinkSaveForge.cmd")
        packaging = root / "packaging"
        packaging.mkdir()
        (packaging / "bootstrap-runtime.ps1").write_text(
            bootstrap_body,
            encoding="utf-8",
        )
        app = root / "app"
        app.mkdir()
        (app / "launcher.py").write_text(
            "from pathlib import Path\n"
            "Path(__file__).with_name('system-python-used.txt').write_text('used')\n",
            encoding="utf-8",
        )
        return root / "RelinkSaveForge.cmd"

    def run_launcher(self, launcher: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["cmd.exe", "/d", "/c", str(launcher), "--list-presets"],
            cwd=launcher.parent,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_launcher_preserves_bootstrap_failure_and_never_uses_system_python(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = self.make_launcher_fixture(root, "exit 37\n")

            completed = self.run_launcher(launcher)

            self.assertEqual(completed.returncode, 37)
            self.assertFalse((root / "app" / "system-python-used.txt").exists())

    def test_launcher_fails_when_bootstrap_does_not_create_portable_python(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = self.make_launcher_fixture(root, "exit 0\n")

            completed = self.run_launcher(launcher)

            self.assertEqual(completed.returncode, 1)
            self.assertFalse((root / "app" / "system-python-used.txt").exists())

    def test_bootstrap_skip_mode_remains_network_free_for_development(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "packaging" / "bootstrap-runtime.ps1"),
                    "-BundleRoot",
                    str(root),
                    "-SkipPython",
                    "-SkipEditor",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            lock = json.loads(
                (root / "runtime" / "runtime-lock.json").read_text(encoding="utf-8-sig")
            )
            self.assertFalse(lock["python"]["installed"])
            self.assertFalse(lock["editor"]["installed"])

    def test_bundle_uses_dedicated_readme_and_only_runtime_bootstrap(self) -> None:
        build_script = (ROOT / "packaging" / "build-windows-bundle.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("packaging/BUNDLE_README.md", build_script)
        self.assertIn("-DestinationRelativePath 'README.md'", build_script)
        self.assertNotIn("$PackagingSource", build_script)
        self.assertNotIn("'README.md',", build_script)
        self.assertIn("--list-presets", build_script)
        launcher_script = (ROOT / "RelinkSaveForge.cmd").read_text(encoding="utf-8")
        self.assertIn('set "BUNDLE_ROOT_ARG=%~dp0."', launcher_script)
        self.assertIn('-BundleRoot "%BUNDLE_ROOT_ARG%"', launcher_script)


if __name__ == "__main__":
    unittest.main()
