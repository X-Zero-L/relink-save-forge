import atexit
import copy
import hashlib
import importlib
import json
import os
import sqlite3
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

standard = importlib.import_module("equip_standard_sigil_preset")


PAIR_CATALOG = ROOT / "catalogs" / "sigil-legal-pairs-2.0.2.json"
CAP_CATALOG = ROOT / "catalogs" / "skill-level-caps-2.0.2.json"
CHARACTERS = ROOT / "catalogs" / "characters.json"
OUTPUT_PRESET = ROOT / "presets" / "sigils" / "standard-endgame-output-2.0.2.json"
QOL_PRESET = ROOT / "presets" / "sigils" / "standard-endgame-qol-2.0.2.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


class StandardSigilPresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = {}
        for name, path in (("output", OUTPUT_PRESET), ("qol", QOL_PRESET)):
            cls.loaded[name] = standard.load_standard_preset(
                path,
                PAIR_CATALOG,
                CAP_CATALOG,
                CHARACTERS,
            )
        cls.pairs = json.loads(PAIR_CATALOG.read_text(encoding="utf-8"))
        cls.caps = json.loads(CAP_CATALOG.read_text(encoding="utf-8"))

    def test_profiles_are_29_by_12_database_row_builds(self) -> None:
        expected = {
            "output": (
                "6994C5F95EE5AD6AC188A0138E7BFD66AE80E06B41F912038D5342394813F7D4",
                666,
                30,
            ),
            "qol": (
                "FA2D287AA04C1D0E41744E868DC62706A4C485AFD753EF879AE0E31AC31F18D9",
                637,
                59,
            ),
        }
        for name, (digest, nonempty, empty) in expected.items():
            with self.subTest(profile=name):
                order, builds, _, payload, _ = self.loaded[name]
                self.assertEqual(len(order), 29)
                self.assertEqual(len(builds), 29)
                self.assertEqual(payload["build_sha256"], digest)
                self.assertEqual(standard.build_digest(builds), digest)
                self.assertTrue(all(len(entries) == 12 for entries in builds.values()))
                lanes = [
                    lane
                    for entries in builds.values()
                    for entry in entries
                    for lane in (entry["primary"], entry["secondary"])
                ]
                self.assertEqual(sum(lane is not None for lane in lanes), nonempty)
                self.assertEqual(sum(lane is None for lane in lanes), empty)
                self.assertTrue(
                    all(lane["level"] == 15 for lane in lanes if lane is not None)
                )
                self.assertFalse(
                    any(lane["level"] == 99 for lane in lanes if lane is not None)
                )

    def test_output_and_qol_role_contracts_are_distinct(self) -> None:
        output_roles = self.loaded["output"][3]["slot_roles"]
        qol_roles = self.loaded["qol"][3]["slot_roles"]
        self.assertEqual(
            output_roles,
            [
                "alpha",
                "beta",
                "gamma",
                "celestial-lumen",
                "celestial-terra",
                "celestial-incendo",
                "celestial-aqua",
                "fatebreaker",
                "celestial-ventus",
                "divergence",
                "awakening",
                "warpath",
            ],
        )
        self.assertEqual(
            qol_roles,
            [
                "alpha",
                "beta",
                "gamma",
                "fatebreaker",
                "celestial-nyx",
                "celestial-terra",
                "flight-over-fight",
                "improved-dodge",
                "stout-heart",
                "potion-autorevive",
                "awakening",
                "warpath",
            ],
        )

    def test_damage_cap_totals_60_without_exceeding_any_curve(self) -> None:
        damage_cap_hash = standard.reference_hash("SKILL_020_00")
        for name, (_, _, metadata, _, _) in self.loaded.items():
            with self.subTest(profile=name):
                for character_id, row in metadata.items():
                    levels = {
                        int(item["hash"], 16): item
                        for item in row["aggregate_levels"]
                    }
                    self.assertEqual(levels[damage_cap_hash]["total_level"], 60)
                    self.assertEqual(levels[damage_cap_hash]["max_total_level"], 65)
                    self.assertTrue(
                        all(
                            item["total_level"] <= item["max_total_level"]
                            for item in row["aggregate_levels"]
                        ),
                        character_id,
                    )

    def test_djeeta_uses_real_single_lane_fallback(self) -> None:
        for name, (_, builds, metadata, _, _) in self.loaded.items():
            with self.subTest(profile=name):
                gran = next(entry for entry in builds["PL0000"] if entry["role"] == "awakening")
                djeeta = next(entry for entry in builds["PL0100"] if entry["role"] == "awakening")
                self.assertEqual(gran["database_key"], "GEEN_114_90")
                self.assertTrue(gran["can_only_hold_one"])
                self.assertIsNotNone(gran["secondary"])
                self.assertEqual(djeeta["database_key"], "GEEN_114_91")
                self.assertFalse(djeeta["can_only_hold_one"])
                self.assertIsNone(djeeta["secondary"])
                self.assertEqual(metadata["PL0100"]["source_character"], "PL0000")
                self.assertTrue(
                    metadata["PL0100"]["captain_avatar_one_only_fallback"]
                )

    def test_every_preset_lane_matches_the_checked_in_database_row(self) -> None:
        pairs = {row["database_key"]: row for row in self.pairs["items"]}
        for name, (_, builds, _, _, _) in self.loaded.items():
            with self.subTest(profile=name):
                for entries in builds.values():
                    for entry in entries:
                        pair = pairs[entry["database_key"]]
                        self.assertEqual(entry["outer_hash"], int(pair["outer_hash"], 16))
                        self.assertEqual(entry["primary"]["id"], pair["primary"]["id"])
                        if pair["secondary"] is None:
                            self.assertIsNone(entry["secondary"])
                        else:
                            self.assertEqual(
                                entry["secondary"]["id"], pair["secondary"]["id"]
                            )

    def test_rejects_level_99_and_fabricated_secondary_lanes(self) -> None:
        source = json.loads(OUTPUT_PRESET.read_text(encoding="utf-8"))
        mutations = []
        level_99 = copy.deepcopy(source)
        level_99["characters"][0]["sigils"][0]["primary"]["level"] = 99
        mutations.append((level_99, "must be level 15"))
        fake_dual = copy.deepcopy(source)
        warpath = fake_dual["characters"][0]["sigils"][11]
        warpath["secondary"] = copy.deepcopy(warpath["primary"])
        mutations.append((fake_dual, "disagree on an empty secondary lane"))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, (payload, message) in enumerate(mutations):
                with self.subTest(index=index):
                    path = root / f"invalid-{index}.json"
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(RuntimeError, message):
                        standard.load_standard_preset(
                            path,
                            PAIR_CATALOG,
                            CAP_CATALOG,
                            CHARACTERS,
                        )

    def test_rejects_aggregate_above_skill_status_cap(self) -> None:
        preset = json.loads(OUTPUT_PRESET.read_text(encoding="utf-8"))
        caps = copy.deepcopy(self.caps)
        for row in caps["items"]:
            if row["skill_id"] == "SKILL_020_00":
                row["max_total_level"] = 59
                row["curve_row_count"] = 59
        for character in preset["characters"]:
            for sigil in character["sigils"]:
                for lane in (sigil["primary"], sigil["secondary"]):
                    if lane is not None and lane["id"] == "SKILL_020_00":
                        lane["max_total_level"] = 59

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cap_path = root / CAP_CATALOG.name
            cap_path.write_text(json.dumps(caps), encoding="utf-8")
            preset["skill_cap_catalog"]["sha256"] = sha256_file(cap_path)
            preset_path = root / OUTPUT_PRESET.name
            preset_path.write_text(json.dumps(preset), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "total 60 exceeds cap 59"):
                standard.load_standard_preset(
                    preset_path,
                    PAIR_CATALOG,
                    cap_path,
                    CHARACTERS,
                )

    def test_checked_in_catalogs_match_local_2_0_2_databases_when_available(self) -> None:
        live_path = os.environ.get("GBFR_TEST_LIVE_DB")
        skill_path = os.environ.get("GBFR_TEST_SKILL_DB")
        if not live_path or not skill_path:
            self.skipTest("local extracted 2.0.2 databases were not supplied")
        live_database = Path(live_path)
        skill_database = Path(skill_path)
        self.assertEqual(self.pairs["source"]["sha256"], sha256_file(live_database))
        self.assertEqual(self.caps["source"]["sha256"], sha256_file(skill_database))

        live = sqlite3.connect(live_database)
        live.row_factory = sqlite3.Row
        try:
            for expected in self.pairs["items"]:
                rows = live.execute(
                    "SELECT * FROM gem WHERE Key=?", (expected["database_key"],)
                ).fetchall()
                self.assertEqual(len(rows), 1, expected["database_key"])
                row = rows[0]
                self.assertEqual(str(row["Name"]).removeprefix("TXT_"), expected["outer_id"])
                self.assertEqual(row["SkillId1"], expected["primary"]["id"])
                self.assertEqual(
                    str(row["SkillId2"] or "") or None,
                    None if expected["secondary"] is None else expected["secondary"]["id"],
                )
                self.assertEqual(bool(row["CanOnlyHoldOne"]), expected["can_only_hold_one"])
        finally:
            live.close()

        skills = sqlite3.connect(skill_database)
        try:
            for expected in self.caps["items"]:
                levels = [
                    int(row[0])
                    for row in skills.execute(
                        "SELECT Level FROM skill_status WHERE upper(Key)=upper(?) ORDER BY Level",
                        (expected["skill_id"],),
                    )
                ]
                self.assertEqual(
                    levels,
                    list(range(1, expected["max_total_level"] + 1)),
                    expected["skill_id"],
                )
        finally:
            skills.close()


if __name__ == "__main__":
    unittest.main()
