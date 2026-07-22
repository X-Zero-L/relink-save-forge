import json
import tempfile
import unittest
from pathlib import Path

from app.presets import PresetError, load_preset, load_presets, render_values


class PresetManifestTests(unittest.TestCase):
    def test_loads_transform_and_verify_steps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "sample.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "sample-pack",
                        "name": "Sample pack",
                        "description": "A deterministic test pack",
                        "steps": [
                            {
                                "id": "transform",
                                "command": [
                                    "{python}",
                                    "{root}/step.py",
                                    "{input}",
                                    "{output}",
                                    "{audit}",
                                ],
                            },
                            {
                                "id": "verify",
                                "kind": "verify",
                                "command": [
                                    "{python}",
                                    "{root}/verify.py",
                                    "{input}",
                                    "{audit}",
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            preset = load_preset(manifest)
            self.assertEqual(preset.id, "sample-pack")
            self.assertEqual([step.kind for step in preset.steps], ["transform", "verify"])
            self.assertTrue(preset.preserve_header)

    def test_rejects_transform_without_output_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "bad",
                        "name": "Bad",
                        "description": "Missing output",
                        "steps": [
                            {
                                "id": "broken",
                                "command": ["{python}", "x.py", "{input}", "{audit}"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PresetError, "output"):
                load_preset(path)

    def test_rejects_duplicate_pack_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = {
                "schema_version": 1,
                "id": "duplicate",
                "name": "Duplicate",
                "description": "Duplicate id",
                "steps": [
                    {
                        "id": "step",
                        "command": [
                            "{python}",
                            "x.py",
                            "{input}",
                            "{output}",
                            "{audit}",
                        ],
                    }
                ],
            }
            (root / "one.json").write_text(json.dumps(value), encoding="utf-8")
            (root / "two.json").write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(PresetError, "duplicate preset id"):
                load_presets(root)

    def test_renders_each_token_without_shell_expansion(self) -> None:
        rendered = render_values(
            ("{python}", "{root}/script.py", "--input={input}"),
            {
                "python": "python.exe",
                "root": "C:/Bundle Root",
                "input": "C:/Save Path/input.dat",
            },
        )
        self.assertEqual(
            rendered,
            [
                "python.exe",
                "C:/Bundle Root/script.py",
                "--input=C:/Save Path/input.dat",
            ],
        )


if __name__ == "__main__":
    unittest.main()
