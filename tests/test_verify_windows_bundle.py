import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.verify_windows_bundle import BundleVerificationError, inspect_archive


class WindowsBundleArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_archive(self, name: str, members: dict[str, bytes | str]) -> Path:
        archive = self.root / name
        with zipfile.ZipFile(archive, "w") as bundle:
            for path, content in members.items():
                bundle.writestr(path, content)
        return archive

    def test_accepts_minimal_single_root_archive(self) -> None:
        archive = self.write_archive(
            "valid.zip",
            {
                "RelinkSaveForge/README.md": "Release bundle\n",
                "RelinkSaveForge/app/launcher.py": "print('ok')\n",
            },
        )

        root, infos = inspect_archive(archive)

        self.assertEqual(root, "RelinkSaveForge")
        self.assertEqual(
            [info.filename for info in infos],
            [
                "RelinkSaveForge/README.md",
                "RelinkSaveForge/app/launcher.py",
            ],
        )

    def test_rejects_path_traversal(self) -> None:
        archive = self.write_archive(
            "traversal.zip",
            {"RelinkSaveForge/../escape.txt": "escape"},
        )

        with self.assertRaisesRegex(BundleVerificationError, "unsafe ZIP member path"):
            inspect_archive(archive)

    def test_rejects_save_and_database_files(self) -> None:
        for index, leaked_path in enumerate(
            (
                "RelinkSaveForge/SaveData1.dat",
                "RelinkSaveForge/cache.db",
                "RelinkSaveForge/catalog.sqlite",
                "RelinkSaveForge/catalog.sqlite3",
            )
        ):
            with self.subTest(leaked_path=leaked_path):
                archive = self.write_archive(
                    f"data-leak-{index}.zip",
                    {leaked_path: b"secret"},
                )
                with self.assertRaisesRegex(
                    BundleVerificationError, "save/database file leaked"
                ):
                    inspect_archive(archive)

    def test_rejects_steam_id_and_absolute_local_path(self) -> None:
        steam_id = "7656119" + "8012345678"
        local_path = "C:" + r"\Users\developer\relink-save-forge"
        leaks = (
            ("steam-id.zip", f"SteamID64={steam_id}", "SteamID64 leaked"),
            (
                "local-path.zip",
                f"workspace={local_path}",
                "absolute local path leaked",
            ),
        )
        for name, content, expected in leaks:
            with self.subTest(name=name):
                archive = self.write_archive(
                    name,
                    {"RelinkSaveForge/README.md": content},
                )
                with self.assertRaisesRegex(BundleVerificationError, expected):
                    inspect_archive(archive)

    def test_rejects_editor_source_and_cache_directories(self) -> None:
        leaked_paths = (
            "RelinkSaveForge/runtime/third_party/GBFR-Save-Editor/gbfr_editor/core/gbfr_save.py",
            "RelinkSaveForge/runtime/downloads/editor.zip",
            "RelinkSaveForge/app/__pycache__/launcher.pyc",
        )
        for index, leaked_path in enumerate(leaked_paths):
            with self.subTest(leaked_path=leaked_path):
                archive = self.write_archive(
                    f"runtime-leak-{index}.zip",
                    {leaked_path: b"leak"},
                )
                with self.assertRaisesRegex(
                    BundleVerificationError,
                    "editor source leaked|cache or download directory leaked",
                ):
                    inspect_archive(archive)


if __name__ == "__main__":
    unittest.main()
