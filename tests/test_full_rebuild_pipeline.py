import argparse
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pipeline_args(root: Path, work_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        input=root / "source.dat",
        items_db=root / "items.db",
        game_db=root / "game.db",
        weapon_original=root / "weapon-original.dat",
        weapon_probe=root / "weapon-probe.dat",
        baseline=root / "baseline.dat",
        editor_root=root / "editor",
        work_dir=work_dir,
        output=None,
        report=None,
        character_catalog=ROOT / "catalogs" / "characters.json",
        weapon_catalog=ROOT / "catalogs" / "weapons.json",
        fate_catalog=ROOT / "catalogs" / "fate-episodes-2.0.json",
        sigil_preset=(
            ROOT / "presets" / "sigils" / "latest-endgame-gold-2.0.2.json"
        ),
        rebuild_catalog=ROOT / "catalogs" / "weapon-rebuild-2.0.json",
        weapon_blessing_preset=(
            ROOT / "presets" / "weapons" / "endgame-qol-blessing-2.0.2.json"
        ),
        summon_preset=(
            ROOT / "presets" / "summons" / "endgame-qol-passives-2.0.2.json"
        ),
        stack_quantity=900,
        expected_steam_id=None,
        expect_transcendence_instances=None,
        expect_transcendence_types=None,
        dry_run=True,
    )


def option_value(command: list[str], option: str) -> str:
    return command[command.index(option) + 1]


class FullRebuildPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pipeline = load_module(
            "test_run_full_rebuild",
            ROOT / "scripts" / "run_full_rebuild.py",
        )

    def test_loadout_stages_follow_transcendence_and_feed_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = pipeline_args(root, root / "work")
            paths = self.pipeline.prepare_paths(args)
            steps = self.pipeline.build_steps(args, paths)

        self.assertEqual(
            [step.name for step in steps],
            [
                "materials",
                "fate_episodes",
                "sigils_initialize",
                "sigils_latest_gold",
                "weapons",
                "transcendence",
                "weapon_blessings",
                "summon_traits",
                "verify",
            ],
        )
        by_name = {step.name: step for step in steps}
        self.assertEqual(
            Path(by_name["transcendence"].command[3]),
            paths["transcendence_save"],
        )
        self.assertEqual(
            Path(by_name["weapon_blessings"].command[2]),
            paths["transcendence_save"],
        )
        self.assertEqual(
            Path(by_name["weapon_blessings"].command[3]),
            paths["weapon_blessings_save"],
        )
        self.assertEqual(
            Path(by_name["summon_traits"].command[2]),
            paths["weapon_blessings_save"],
        )
        self.assertEqual(Path(by_name["summon_traits"].command[3]), paths["output"])
        verify = by_name["verify"].command
        self.assertEqual(Path(verify[2]), paths["output"])
        self.assertEqual(
            Path(option_value(verify, "--loadout-baseline")),
            paths["transcendence_save"],
        )
        self.assertEqual(
            Path(option_value(verify, "--weapon-blessing-preset")),
            paths["weapon_blessing_preset"],
        )
        self.assertEqual(
            Path(option_value(verify, "--summon-preset")),
            paths["summon_preset"],
        )
        self.assertEqual(paths["transcendence_save"].name, "05-transcendence.dat")
        self.assertEqual(paths["weapon_blessings_save"].name, "06-weapon-blessings.dat")
        self.assertEqual(paths["output"].name, "07-endgame.dat")
        self.assertEqual(paths["verification_report"].name, "08-verification.json")

    def test_preflight_accepts_repository_default_loadout_presets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = pipeline_args(root, root / "work")
            for path in (
                args.input,
                args.items_db,
                args.game_db,
                args.weapon_original,
                args.weapon_probe,
                args.baseline,
            ):
                path.write_bytes(b"fixture")
            editor_api = args.editor_root / "gbfr_editor" / "core" / "gbfr_save.py"
            editor_api.parent.mkdir(parents=True)
            editor_api.write_text("# fixture\n", encoding="utf-8")
            paths = self.pipeline.prepare_paths(args)
            inputs = self.pipeline.preflight(
                paths,
                self.pipeline.build_steps(args, paths),
            )

        self.assertIn("weapon blessing preset", inputs)
        self.assertIn("summon passive preset", inputs)


class CombinedTraitVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        stubs = {
            "build_materials_complete": types.SimpleNamespace(
                classify_item=lambda *args: (True, None),
                load_database=lambda *args: ([], set(), set(), set(), []),
            ),
            "equip_verified_summon_traits": types.SimpleNamespace(),
            "equip_verified_weapon_blessings": types.SimpleNamespace(),
            "gbfr_hash": types.SimpleNamespace(gbfr_hash=lambda value: 0),
            "save_editor_api": types.SimpleNamespace(
                GBFRSaveData=object,
                add_editor_argument=lambda parser: None,
            ),
        }
        with mock.patch.dict(sys.modules, stubs):
            cls.verifier = load_module(
                "test_verify_full_rebuild",
                ROOT / "scripts" / "verify_full_rebuild.py",
            )

    def test_accepts_exactly_31_distinct_nonempty_traits(self) -> None:
        self.assertIsNone(
            self.verifier.combined_trait_error(
                "PL0000",
                list(range(1, 25)),
                list(range(25, 28)),
                list(range(28, 32)),
            )
        )

    def test_rejects_cross_surface_duplicate_trait(self) -> None:
        error = self.verifier.combined_trait_error(
            "PL0000",
            list(range(1, 25)),
            list(range(25, 28)),
            [28, 29, 30, 27],
        )
        self.assertIn("duplicates", error or "")
        self.assertIn("0000001B", error or "")

    def test_rejects_wrong_surface_counts(self) -> None:
        error = self.verifier.combined_trait_error(
            "PL0000",
            list(range(1, 24)),
            list(range(24, 27)),
            list(range(27, 31)),
        )
        self.assertIn("expected (24, 3, 4)", error or "")

    def test_summon_preservation_distinguishes_relationship_bonus_and_flags(self) -> None:
        before = {
            "equipped": [1, 2, 3, 4],
            "instances": {
                "70000": {
                    "instance_id": 1,
                    "outer_hash": 10,
                    "bonus_hash": 20,
                    "bonus_level": 9,
                    "field_1460": [3],
                }
            },
        }
        after = {
            "equipped": list(before["equipped"]),
            "instances": {
                unit: {**values}
                for unit, values in before["instances"].items()
            },
        }
        after["instances"]["70000"]["bonus_level"] = 8
        after["instances"]["70000"]["field_1460"] = [7]

        checks = self.verifier.summon_preservation_checks(before, after)

        self.assertTrue(checks["relationships"])
        self.assertFalse(checks["bonus"])
        self.assertFalse(checks["field_1460"])


if __name__ == "__main__":
    unittest.main()
