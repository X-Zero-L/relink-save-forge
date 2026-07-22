import atexit
import importlib
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

loadouts = importlib.import_module("ensure_sigil_loadouts")


class FakeRecord:
    def __init__(self, field_id: int, unit_id: int, values: list[int]) -> None:
        self.id_type = field_id
        self.unit_id = unit_id
        self.kind = "uint"
        self.values = list(values)


class FakeSave:
    def __init__(self, records: list[FakeRecord]) -> None:
        self.records = records

    def find(self, *, id_type: int, unit_id: int | None = None) -> list[FakeRecord]:
        return [
            record
            for record in self.records
            if record.id_type == id_type
            and (unit_id is None or record.unit_id == unit_id)
        ]

    @staticmethod
    def get_values(record: FakeRecord) -> list[int]:
        return list(record.values)

    @staticmethod
    def get_first_value(record: FakeRecord) -> int:
        return record.values[0]

    @staticmethod
    def set_values(record: FakeRecord, values: list[int]) -> None:
        record.values = list(values)

    @staticmethod
    def set_first_value(record: FakeRecord, value: int) -> None:
        record.values[0] = value


class EnsureSigilLoadoutsTests(unittest.TestCase):
    def test_external_references_and_unselected_owners_are_preserved(self) -> None:
        characters = [
            {
                "id": f"PL{index:04d}",
                "name": f"Character {index}",
                "unit": 50_000 + index,
                "hash": 0x1000 + index,
            }
            for index in range(loadouts.EXPECTED_CHARACTER_COUNT)
        ]
        instances = {}
        slot_to_unit = {}
        records = []
        next_unit = 40_000
        next_slot = 1
        for character in characters:
            character_slots = []
            for _ in range(loadouts.SIGILS_PER_CHARACTER):
                owner_record = FakeRecord(2706, next_unit, [character["hash"]])
                instances[next_unit] = {
                    "slot_id": next_slot,
                    "fields": {2706: owner_record},
                }
                slot_to_unit[next_slot] = next_unit
                records.append(owner_record)
                character_slots.append(next_slot)
                next_unit += 1
                next_slot += 1
            records.append(FakeRecord(1403, character["unit"], character_slots))

        shared_unit = slot_to_unit[1]
        shared_owner_before = loadouts.owner(
            FakeSave(records),
            instances[shared_unit],
        )

        missing_loadout = next(
            record
            for record in records
            if record.id_type == 1403 and record.unit_id == characters[-1]["unit"]
        )
        reserved_slot = missing_loadout.values[-1]
        reserved_unit = slot_to_unit[reserved_slot]
        instances[reserved_unit]["fields"][2706].values[0] = loadouts.EMPTY_HASH
        missing_loadout.values[-1] = 0

        extra_owned_unit = next_unit
        extra_owned_slot = next_slot
        extra_owner_record = FakeRecord(
            2706,
            extra_owned_unit,
            [characters[0]["hash"]],
        )
        instances[extra_owned_unit] = {
            "slot_id": extra_owned_slot,
            "fields": {2706: extra_owner_record},
        }
        slot_to_unit[extra_owned_slot] = extra_owned_unit
        records.append(extra_owner_record)
        next_unit += 1
        next_slot += 1

        tail_unit = next_unit
        tail_slot = next_slot
        tail_owner_record = FakeRecord(2706, tail_unit, [loadouts.EMPTY_HASH])
        instances[tail_unit] = {
            "slot_id": tail_slot,
            "fields": {2706: tail_owner_record},
        }
        slot_to_unit[tail_slot] = tail_unit
        records.append(tail_owner_record)
        next_unit += 1
        next_slot += 1
        first_loadout = next(
            record
            for record in records
            if record.id_type == 1403 and record.unit_id == characters[0]["unit"]
        )
        first_loadout.values.append(tail_slot)

        safe_unit = next_unit
        safe_slot = next_slot
        safe_owner_record = FakeRecord(2706, safe_unit, [loadouts.EMPTY_HASH])
        instances[safe_unit] = {
            "slot_id": safe_slot,
            "fields": {2706: safe_owner_record},
        }
        slot_to_unit[safe_slot] = safe_unit
        records.append(safe_owner_record)

        records.append(
            FakeRecord(
                1403,
                999_999,
                [1, reserved_slot, extra_owned_slot],
            )
        )

        save = FakeSave(records)
        plan = loadouts.plan_loadouts(save, characters, instances, slot_to_unit)

        self.assertIn(shared_unit, plan["selected_units"])
        self.assertIn(safe_unit, plan["selected_units"])
        self.assertNotIn(reserved_unit, plan["selected_units"])
        self.assertNotIn(extra_owned_unit, plan["selected_units"])
        self.assertNotIn(tail_unit, plan["selected_units"])
        self.assertEqual(
            plan["externally_referenced_units"],
            {shared_unit, reserved_unit, extra_owned_unit},
        )
        self.assertIn(extra_owned_unit, plan["extra_playable_owner_units"])
        self.assertEqual(plan["tail_referenced_units"], {tail_unit})

        loadouts.apply_plan(save, characters, instances, plan)
        loadouts.verify_loadouts(save, characters, instances, slot_to_unit)
        self.assertEqual(
            loadouts.owner(save, instances[shared_unit]),
            shared_owner_before,
        )
        self.assertEqual(
            loadouts.owner(save, instances[reserved_unit]),
            loadouts.EMPTY_HASH,
        )
        self.assertEqual(
            loadouts.owner(save, instances[extra_owned_unit]),
            characters[0]["hash"],
        )
        self.assertEqual(
            loadouts.owner(save, instances[safe_unit]),
            characters[-1]["hash"],
        )
        self.assertEqual(loadouts.owner(save, instances[tail_unit]), loadouts.EMPTY_HASH)
        self.assertEqual(first_loadout.values[-1], tail_slot)


if __name__ == "__main__":
    unittest.main()
