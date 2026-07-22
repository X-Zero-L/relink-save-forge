import atexit
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
if "GBFR_SAVE_EDITOR_ROOT" not in os.environ:
    _fake_editor = tempfile.TemporaryDirectory()
    atexit.register(_fake_editor.cleanup)
    _fake_core = Path(_fake_editor.name) / "gbfr_editor" / "core"
    _fake_core.mkdir(parents=True)
    (_fake_core / "gbfr_save.py").write_text(
        "\n".join(
            (
                "class GBFRSaveData:",
                "    pass",
                "",
                "class UnitRecord:",
                "    pass",
                "",
            )
        ),
        encoding="utf-8",
    )
    os.environ["GBFR_SAVE_EDITOR_ROOT"] = _fake_editor.name

characters = importlib.import_module("unlock_all_characters")
summons = importlib.import_module("ensure_top_summons")


class CharacterActivationTests(unittest.TestCase):
    def test_verified_activation_masks_cover_all_character_classes(self) -> None:
        catalog = json.loads(
            (ROOT / "catalogs" / "characters.json").read_text(encoding="utf-8")
        )
        masks = {
            row["id"]: characters.activation_mask(row["id"])
            for row in catalog["items"]
        }
        self.assertEqual(len(masks), 29)
        self.assertEqual(sum(mask == 0x01 for mask in masks.values()), 7)
        self.assertEqual(masks["PL1900"], 0x09)
        self.assertEqual(sum(mask == 0x11 for mask in masks.values()), 21)

    def test_activation_is_bitwise_and_preserves_existing_high_state(self) -> None:
        old = 0x4000
        self.assertEqual(old | characters.activation_mask("PL2400"), 0x4011)
        self.assertEqual(0x4009 | characters.activation_mask("PL1900"), 0x4009)
        self.assertEqual(0x4001 | characters.activation_mask("PL0000"), 0x4001)


class SummonCreationCatalogTests(unittest.TestCase):
    def test_checked_in_catalog_is_the_verified_four_tuple(self) -> None:
        path = ROOT / "catalogs" / "top-summons-2.0.2.json"
        loaded = summons.load_catalog(path, None)
        actual = [
            (
                row["name"],
                row["summon_hash_value"],
                row["trait_hash_value"],
                row["bonus_hash_value"],
                row["trait_level"],
                row["bonus_level"],
                row["state_1460"],
            )
            for row in loaded
        ]
        self.assertEqual(actual, list(summons.EXPECTED_SPECS))

    def test_only_the_canonical_empty_slot_is_allocatable(self) -> None:
        empty = {
            1456: [0],
            1457: [summons.EMPTY_HASH],
            1458: [summons.EMPTY_HASH, summons.EMPTY_HASH],
            1459: [-1, -1],
            1460: [0],
        }
        self.assertTrue(summons.is_clean_empty(empty))
        for field_id in empty:
            with self.subTest(field_id=field_id):
                changed = {key: list(values) for key, values in empty.items()}
                changed[field_id][0] += 1
                self.assertFalse(summons.is_clean_empty(changed))

    def test_catalog_rejects_a_rarity_derived_state_value(self) -> None:
        source = json.loads(
            (ROOT / "catalogs" / "top-summons-2.0.2.json").read_text(
                encoding="utf-8"
            )
        )
        source["summons"][0]["state_1460"] = 4
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "verified tuple"):
                summons.load_catalog(path, None)


if __name__ == "__main__":
    unittest.main()
