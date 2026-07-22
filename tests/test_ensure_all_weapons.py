import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    stub = types.SimpleNamespace(
        GBFRSaveData=object,
        UnitRecord=object,
        add_editor_argument=lambda parser: None,
    )
    spec = importlib.util.spec_from_file_location(
        "test_ensure_all_weapons_module",
        ROOT / "scripts" / "ensure_all_weapons.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load ensure_all_weapons.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"save_editor_api": stub}):
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


class EnsureAllWeaponsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()
        cls.template = cls.module.load_template(
            ROOT / "catalogs" / "weapon-instance-template-2.0.2.json"
        )
        cls.targets, cls.identities = cls.module.load_targets(
            ROOT / "catalogs" / "weapons.json",
            ROOT / "catalogs" / "weapon-rebuild-2.0.json",
            ROOT / "catalogs" / "weapon-runtime-identities-2.0.2.json",
        )
        cls.by_id = {target.official_id: target for target in cls.targets}

    def snapshot(
        self,
        *,
        unit: int,
        slot: int,
        weapon_hash: int,
        overrides: dict[int, tuple[int, ...]] | None = None,
    ):
        values = dict(self.template.empty)
        values.update(
            {
                2802: (slot,),
                2803: (weapon_hash,),
                2804: (100,),
                2805: (1,),
                2815: (1,),
            }
        )
        values.update(overrides or {})
        return self.module.WeaponSnapshot(unit=unit, values=values)

    def layout(
        self,
        *,
        counter: int,
        occupied: list,
        empty_units: list[int],
    ):
        return self.module.WeaponLayout(
            counter=counter,
            groups={},
            occupied=occupied,
            empty_units=empty_units,
        )

    def test_catalogs_cover_174_targets_160_endgame_and_371_identities(self) -> None:
        self.assertEqual(len(self.targets), 174)
        self.assertEqual(sum(target.endgame for target in self.targets), 160)
        self.assertEqual(sum(not target.endgame for target in self.targets), 14)
        self.assertEqual(len(self.identities), 371)
        self.assertEqual(set(self.identities.values()), set(self.by_id))

    def test_missing_targets_use_lowest_empty_units_and_monotonic_slots(self) -> None:
        endgame = self.by_id["WEP_PL0000_01"]
        base_only = self.by_id["WEP_PL2200_02"]
        plan = self.module.plan_weapons(
            self.layout(counter=20, occupied=[], empty_units=[40002, 40007]),
            [endgame, base_only],
            self.identities,
            self.template,
        )

        first, second = plan.weapons
        self.assertEqual((first.unit, first.slot, first.action), (40002, 21, "create"))
        self.assertEqual(first.desired[2803], (endgame.target_hash,))
        self.assertEqual(first.desired[2817], (7,))
        self.assertEqual(first.desired[2818], endgame.skill_vector)
        self.assertEqual(first.desired[2815], (0x41,))
        self.assertEqual((second.unit, second.slot, second.action), (40007, 22, "create"))
        self.assertEqual(second.desired[2803], (base_only.base_hash,))
        self.assertEqual(second.desired[2817], (0,))
        self.assertEqual(second.desired[2818], self.template.empty[2818])
        self.assertEqual(second.desired[2815], (0x01,))
        self.assertEqual(plan.final_counter, 22)

    def test_historical_alias_is_upgraded_in_place_and_preserves_blessing(self) -> None:
        target = self.by_id["WEP_PL0000_06"]
        blessing_hash = 0x71173866
        source = self.snapshot(
            unit=40010,
            slot=33,
            weapon_hash=target.base_hash,
            overrides={2816: (blessing_hash,)},
        )
        plan = self.module.plan_weapons(
            self.layout(counter=40, occupied=[source], empty_units=[]),
            [target],
            self.identities,
            self.template,
        )

        row = plan.weapons[0]
        self.assertEqual((row.unit, row.slot, row.action), (40010, 33, "upgrade"))
        self.assertEqual(row.desired[2803], (target.target_hash,))
        self.assertEqual(row.desired[2807], (10,))
        self.assertEqual(row.desired[2815], (0x51,))
        self.assertEqual(row.desired[2816], (blessing_hash,))
        self.assertEqual(row.desired[2817], (7,))
        self.assertEqual(row.desired[2818], target.skill_vector)

    def test_unknown_copies_are_preserved(self) -> None:
        target = self.by_id["WEP_PL0000_01"]
        complete_values = self.module.desired_values(
            None,
            target,
            self.template,
            10,
        )
        current = self.module.WeaponSnapshot(unit=40000, values=complete_values)
        unknown = self.snapshot(
            unit=40002,
            slot=12,
            weapon_hash=0x7460CD22,
        )
        plan = self.module.plan_weapons(
            self.layout(
                counter=12,
                occupied=[unknown, current],
                empty_units=[],
            ),
            [target],
            self.identities,
            self.template,
        )

        self.assertEqual(plan.weapons[0].unit, 40000)
        self.assertEqual(plan.weapons[0].action, "unchanged")
        self.assertEqual([row.unit for row in plan.unknown], [40002])

    def test_duplicate_official_instances_are_rejected(self) -> None:
        target = self.by_id["WEP_PL0000_01"]
        current = self.snapshot(
            unit=40000,
            slot=10,
            weapon_hash=target.target_hash,
        )
        duplicate = self.snapshot(
            unit=40001,
            slot=11,
            weapon_hash=target.base_hash,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            r"duplicate official weapon instances: WEP_PL0000_01 at units \[40000, 40001\]",
        ):
            self.module.plan_weapons(
                self.layout(
                    counter=11,
                    occupied=[duplicate, current],
                    empty_units=[],
                ),
                [target],
                self.identities,
                self.template,
            )

    def test_full_snapshot_distinguishes_duplicate_record_coordinates(self) -> None:
        first = types.SimpleNamespace(
            kind="uint",
            index=7,
            id_type=2802,
            unit_id=40000,
            values=(10,),
        )
        second = types.SimpleNamespace(
            kind="uint",
            index=8,
            id_type=2802,
            unit_id=40000,
            values=(11,),
        )
        save = types.SimpleNamespace(
            records=[first, second],
            get_values=lambda record: record.values,
        )

        snapshot = self.module.full_snapshot(save)

        self.assertEqual(
            snapshot,
            {
                ("uint", 7, 2802, 40000): (10,),
                ("uint", 8, 2802, 40000): (11,),
            },
        )
        changed = self.module.changed_keys(
            snapshot,
            {**snapshot, ("uint", 8, 2802, 40000): (12,)},
        )
        self.assertEqual(changed, {("uint", 8, 2802, 40000)})

    def test_rejects_insufficient_capacity(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "canonical empty weapon units"):
            self.module.plan_weapons(
                self.layout(counter=5, occupied=[], empty_units=[40000]),
                self.targets[:2],
                self.identities,
                self.template,
            )

    def test_rejects_duplicate_instance_ids_and_counter_regression(self) -> None:
        first = self.snapshot(unit=40000, slot=8, weapon_hash=self.targets[0].base_hash)
        second = self.snapshot(unit=40001, slot=8, weapon_hash=self.targets[1].base_hash)
        with self.assertRaisesRegex(RuntimeError, "duplicate weapon instance ids"):
            self.module.plan_weapons(
                self.layout(counter=8, occupied=[first, second], empty_units=[]),
                self.targets[:2],
                self.identities,
                self.template,
            )
        with self.assertRaisesRegex(RuntimeError, "below occupied maximum"):
            self.module.plan_weapons(
                self.layout(counter=7, occupied=[first], empty_units=[]),
                [self.targets[0]],
                self.identities,
                self.template,
            )

    def test_rejects_noncanonical_partial_shell(self) -> None:
        values = dict(self.template.empty)
        values[2803] = (self.targets[0].base_hash,)
        with self.assertRaisesRegex(RuntimeError, "noncanonical partial shell"):
            self.module.classify_weapon_values(values, self.template, 40000)

    def test_base_only_weapon_rejects_unknown_transcendence_state(self) -> None:
        target = self.by_id["WEP_PL2200_02"]
        source = self.snapshot(
            unit=40000,
            slot=1,
            weapon_hash=target.base_hash,
            overrides={2817: (3,)},
        )
        with self.assertRaisesRegex(RuntimeError, "unsupported transcendence state"):
            self.module.plan_weapons(
                self.layout(counter=1, occupied=[source], empty_units=[]),
                [target],
                self.identities,
                self.template,
            )

    def test_known_flag_bits_are_normalized_while_unknown_bits_are_preserved(self) -> None:
        target = self.by_id["WEP_PL2200_02"]
        source = self.snapshot(
            unit=40000,
            slot=1,
            weapon_hash=target.base_hash,
            overrides={2815: (0xD1,)},
        )
        plan = self.module.plan_weapons(
            self.layout(counter=1, occupied=[source], empty_units=[]),
            [target],
            self.identities,
            self.template,
        )

        self.assertEqual(plan.weapons[0].desired[2815], (0x81,))

    def test_equipment_policy_preserves_or_selects_each_characters_strongest(self) -> None:
        plan = self.module.plan_weapons(
            self.layout(
                counter=0,
                occupied=[],
                empty_units=list(range(40000, 40000 + len(self.targets))),
            ),
            self.targets,
            self.identities,
            self.template,
        )
        equipment = {
            character_id: (10000 + index, object(), 999 + index)
            for index, character_id in enumerate(
                sorted({target.character_id for target in self.targets})
            )
        }

        self.assertEqual(
            self.module.plan_equipment(equipment, plan, "preserve"),
            [],
        )
        changes = self.module.plan_equipment(equipment, plan, "strongest")
        self.assertEqual(len(changes), 29)
        strongest = {
            target.character_id: target
            for target in self.targets
            if target.collection_slot == 6
        }
        by_character = {
            row.target.character_id: row
            for row in plan.weapons
            if row.target.collection_slot == 6
        }
        for change in changes:
            character_id = change["character_id"]
            self.assertEqual(
                change["after_slot"],
                by_character[character_id].slot,
            )
            self.assertIn(character_id, strongest)


if __name__ == "__main__":
    unittest.main()
