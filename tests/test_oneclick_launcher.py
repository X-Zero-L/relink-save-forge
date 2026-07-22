import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import launcher
from app.runtime import OneClickError


class OneClickLauncherTests(unittest.TestCase):
    def write_verified_bundled_editor(self, bundle: Path) -> Path:
        editor = bundle / "runtime" / "third_party" / "GBFR-Save-Editor"
        core = editor / "gbfr_editor" / "core"
        core.mkdir(parents=True)
        (core / "gbfr_save.py").write_text(
            "class GBFRSaveData: pass\n",
            encoding="utf-8",
        )
        marker = editor / ".relink-save-forge-source.json"
        marker.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "component": "gbfr-save-editor",
                    "repository": launcher.PINNED_EDITOR_REPOSITORY,
                    "commit": launcher.PINNED_EDITOR_COMMIT,
                    "url": launcher.PINNED_EDITOR_URL,
                    "archive_sha256": launcher.PINNED_EDITOR_SHA256,
                    "core_sha256": launcher._sha256_file(core / "gbfr_save.py"),
                    "tree_sha256": launcher._directory_tree_sha256(
                        editor,
                        {marker.name},
                    ),
                }
            ),
            encoding="utf-8",
        )
        (bundle / "runtime" / "runtime-lock.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "editor": {
                        "repository": launcher.PINNED_EDITOR_REPOSITORY,
                        "commit": launcher.PINNED_EDITOR_COMMIT,
                        "url": launcher.PINNED_EDITOR_URL,
                        "sha256": launcher.PINNED_EDITOR_SHA256,
                        "installed": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        return editor

    def test_restore_path_allows_a_missing_primary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            save_dir = Path(directory) / "SaveGames"
            resolved = launcher.resolve_restore_save_path(save_dir)
            self.assertEqual(resolved, (save_dir / "SaveData1.dat").resolve())

    def test_restore_latest_holds_the_selected_save_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            save_path = root / "live" / "SaveGames" / "SaveData1.dat"
            editor_root = root / "editor"
            backup = state_root / "backups" / "backup"
            events = []
            lock = MagicMock()
            lock.acquire.side_effect = lambda: events.append("lock-acquired")
            lock.release.side_effect = lambda: events.append("lock-released")

            def latest(*_args, **_kwargs):
                events.append("backup-selected")
                return backup

            def restore(*_args, **_kwargs):
                events.append("backup-restored")
                return 3

            with (
                patch.object(launcher, "SaveLock", return_value=lock),
                patch.object(
                    launcher,
                    "require_game_closed",
                    side_effect=lambda: events.append("game-checked"),
                ),
                patch.object(launcher, "SaveValidator", return_value=object()),
                patch.object(launcher, "latest_backup", side_effect=latest),
                patch.object(launcher, "restore_backup_directory", side_effect=restore),
            ):
                result = launcher.restore_latest(state_root, save_path, editor_root)

            self.assertEqual(result, 0)
            self.assertEqual(
                events,
                [
                    "lock-acquired",
                    "game-checked",
                    "backup-selected",
                    "backup-restored",
                    "lock-released",
                ],
            )

    def test_first_editor_use_bootstraps_only_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            bundle.mkdir()
            expected = bundle / "runtime" / "third_party" / "GBFR-Save-Editor"
            calls = []

            def fake_bootstrap() -> None:
                calls.append(True)
                core = expected / "gbfr_editor" / "core"
                core.mkdir(parents=True)
                (core / "gbfr_save.py").write_text("class GBFRSaveData: pass\n", encoding="utf-8")

            with (
                patch.object(launcher, "BUNDLE_ROOT", bundle),
                patch.object(launcher, "_bootstrap_editor", fake_bootstrap),
                patch.dict("os.environ", {}, clear=True),
            ):
                resolved = launcher.resolve_editor_root(None)

            self.assertEqual(resolved, expected.resolve())
            self.assertEqual(len(calls), 1)

    def test_explicit_invalid_editor_root_does_not_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid-editor"
            with patch.object(
                launcher,
                "_bootstrap_editor",
                side_effect=AssertionError("bootstrap must not run"),
            ):
                with self.assertRaisesRegex(OneClickError, "--editor-root"):
                    launcher.resolve_editor_root(invalid)

    def test_release_bundle_ignores_environment_and_sibling_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "SHA256SUMS.json").write_text("{}", encoding="utf-8")
            configured = root / "configured"
            configured_core = configured / "gbfr_editor" / "core"
            configured_core.mkdir(parents=True)
            (configured_core / "gbfr_save.py").write_text("pass\n", encoding="utf-8")
            sibling = root / "GBFR-Save-Editor" / "gbfr_editor" / "core"
            sibling.mkdir(parents=True)
            (sibling / "gbfr_save.py").write_text("pass\n", encoding="utf-8")

            def fake_bootstrap() -> None:
                self.write_verified_bundled_editor(bundle)

            with (
                patch.object(launcher, "BUNDLE_ROOT", bundle),
                patch.object(launcher, "_bootstrap_editor", side_effect=fake_bootstrap),
                patch.dict(
                    "os.environ",
                    {"GBFR_SAVE_EDITOR_ROOT": str(configured)},
                    clear=True,
                ),
            ):
                resolved = launcher.resolve_editor_root(None)

            self.assertEqual(
                resolved,
                (bundle / "runtime" / "third_party" / "GBFR-Save-Editor").resolve(),
            )

    def test_release_bundle_rejects_unmarked_bundled_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            bundle.mkdir()
            (bundle / "SHA256SUMS.json").write_text("{}", encoding="utf-8")
            core = (
                bundle
                / "runtime"
                / "third_party"
                / "GBFR-Save-Editor"
                / "gbfr_editor"
                / "core"
            )
            core.mkdir(parents=True)
            (core / "gbfr_save.py").write_text("pass\n", encoding="utf-8")

            with (
                patch.object(launcher, "BUNDLE_ROOT", bundle),
                patch.object(
                    launcher,
                    "_bootstrap_editor",
                    side_effect=OneClickError("identity repair failed"),
                ),
            ):
                with self.assertRaisesRegex(OneClickError, "identity repair failed"):
                    launcher.resolve_editor_root(None)

    def test_release_bundle_identity_ignores_python_bytecode_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            bundle.mkdir()
            (bundle / "SHA256SUMS.json").write_text("{}", encoding="utf-8")
            expected = self.write_verified_bundled_editor(bundle)
            cache = expected / "gbfr_editor" / "core" / "__pycache__"
            cache.mkdir()
            (cache / "gbfr_save.cpython-311.pyc").write_bytes(b"derived cache")

            with (
                patch.object(launcher, "BUNDLE_ROOT", bundle),
                patch.object(
                    launcher,
                    "_bootstrap_editor",
                    side_effect=AssertionError("derived cache must not trigger repair"),
                ),
            ):
                resolved = launcher.resolve_editor_root(None)

            self.assertEqual(resolved, expected.resolve())

    def test_explicit_editor_root_remains_available_in_release_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "SHA256SUMS.json").write_text("{}", encoding="utf-8")
            explicit = root / "custom-editor"
            core = explicit / "gbfr_editor" / "core"
            core.mkdir(parents=True)
            (core / "gbfr_save.py").write_text("pass\n", encoding="utf-8")

            with (
                patch.object(launcher, "BUNDLE_ROOT", bundle),
                patch.object(
                    launcher,
                    "_bootstrap_editor",
                    side_effect=AssertionError("explicit override must not bootstrap"),
                ),
            ):
                resolved = launcher.resolve_editor_root(explicit)

            self.assertEqual(resolved, explicit.resolve())

    def test_list_presets_never_resolves_or_bootstraps_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packs = root / "packs"
            packs.mkdir()
            (packs / "sample.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "sample",
                        "name": "Sample",
                        "description": "List-only test",
                        "steps": [
                            {
                                "id": "step",
                                "command": [
                                    "{python}",
                                    "step.py",
                                    "{input}",
                                    "{output}",
                                    "{audit}",
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(
                launcher,
                "resolve_editor_root",
                side_effect=AssertionError("editor resolution must not run"),
            ):
                result = launcher.main(
                    ["--list-presets", "--presets-dir", str(packs)]
                )
            self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
