"""Max 2.0 character specialization and legacy four-line overmastery safely.

The script only edits an offline save copy.  It derives the 29 playable
character units from their 1301 GBID hashes, validates the 2.0 specialization
cap against the extracted database, preserves every existing overmastery stat
type, fills only genuinely empty stat slots, and emits a machine-readable
audit report.
"""

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from save_editor_api import GBFRSaveData, add_editor_argument
from specialization_board import complete_specialization_board


EMPTY_HASH = 0x887AE0B0
EXPECTED_CHARACTER_COUNT = 29
EXPECTED_MASTER_TOTAL = 3_309_499
MASTER_POINTS_TARGET = 9_999_999
# Field 1607 is a one-hot level bitmap. Bit 9 is the verified legal maximum;
# writing every lower bit (0x03FF) creates an invalid composite state.
OVERMASTERY_MAX_VALUE = 0x0200
SPECIALIZATION_STATE_WIDTH = 241

EXPECTED_CHARACTER_UNITS = {
    "PL0000": 10000,
    "PL0100": 10001,
    "PL0200": 10002,
    "PL0300": 10003,
    "PL0400": 10004,
    "PL0500": 10005,
    "PL0600": 10007,
    "PL1500": 10008,
    "PL0700": 10009,
    "PL0800": 10010,
    "PL0900": 10011,
    "PL1000": 10012,
    "PL1100": 10014,
    "PL1200": 10015,
    "PL2300": 10016,
    "PL1300": 10017,
    "PL1400": 10018,
    "PL2400": 10019,
    "PL1600": 10020,
    "PL1900": 10021,
    "PL1700": 10022,
    "PL1800": 10024,
    "PL2100": 10027,
    "PL2200": 10028,
    "PL2900": 10036,
    "PL2600": 10037,
    "PL2500": 10038,
    "PL2700": 10039,
    "PL2800": 10040,
}

EMPTY_SLOT_FALLBACKS = (
    (0xC4925BD7, "Attack Power Up"),
    (0x43B7581D, "Normal Damage Cap Up"),
    (0x9C555433, "Skill Damage Cap Up"),
    (0x45C65767, "Critical Rate"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline source save copy")
    parser.add_argument("output", type=Path, help="Separate offline output save")
    parser.add_argument(
        "--characters",
        type=Path,
        required=True,
        help="2.0 characters.json catalog",
    )
    parser.add_argument(
        "--mastery-database",
        type=Path,
        required=True,
        help="SQLite database containing chara_master_exp",
    )
    parser.add_argument(
        "--expected-specialization-new-units",
        type=int,
        help="Optional exact first-run 1602 mutation count",
    )
    parser.add_argument(
        "--expected-specialization-target-sha256",
        help="Optional SHA-256 of sorted unit:mask specialization targets",
    )
    parser.add_argument(
        "--expected-specialization-active-layouts",
        type=int,
        help="Optional exact final count of active 2.0 layout bits",
    )
    parser.add_argument("--audit", type=Path, required=True, help="JSON audit report")
    add_editor_argument(parser)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def full_snapshot(save: GBFRSaveData) -> dict[tuple[str, int, int, int], list]:
    return {
        (record.kind, record.index, record.id_type, record.unit_id): list(
            save.get_values(record)
        )
        for record in save.records
    }


def changed_records(
    before: dict[tuple[str, int, int, int], list],
    after: dict[tuple[str, int, int, int], list],
) -> list[dict]:
    if set(before) != set(after):
        raise RuntimeError("save record identities changed")
    changes = []
    for key in sorted(before, key=lambda value: (value[2], value[3], value[0], value[1])):
        old_values = before[key]
        new_values = after[key]
        if old_values == new_values:
            continue
        if len(old_values) != len(new_values):
            raise RuntimeError(f"save record width changed for {key}")
        changes.append(
            {
                "kind": key[0],
                "record_index": key[1],
                "field_id": key[2],
                "unit_id": key[3],
                "changed_indexes": [
                    index
                    for index, (old, new) in enumerate(zip(old_values, new_values))
                    if old != new
                ],
                "before": old_values,
                "after": new_values,
            }
        )
    return changes


def required_record(
    save: GBFRSaveData,
    field_id: int,
    unit_id: int,
    *,
    kinds: tuple[str, ...],
    value_count: int,
):
    records = save.find(id_type=field_id, unit_id=unit_id)
    if len(records) != 1:
        raise RuntimeError(
            f"expected one record for field {field_id}/unit {unit_id}, found {len(records)}"
        )
    record = records[0]
    if record.kind not in kinds or record.value_count != value_count:
        raise RuntimeError(
            f"unexpected shape for field {field_id}/unit {unit_id}: "
            f"kind={record.kind}, count={record.value_count}"
        )
    return record


def first_value(save: GBFRSaveData, record) -> int:
    value = save.get_first_value(record)
    if value is None:
        raise RuntimeError(
            f"record {record.kind}:{record.index} has no readable first value"
        )
    return int(value)


def set_if_changed(save: GBFRSaveData, record, value: int) -> bool:
    if first_value(save, record) == int(value):
        return False
    save.set_first_value(record, int(value))
    return True


def load_characters(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError(
            f"character catalog must contain {EXPECTED_CHARACTER_COUNT} items"
        )
    characters = []
    seen_ids = set()
    seen_hashes = set()
    for row in items:
        character_id = str(row.get("id") or "")
        hash_text = str(row.get("hash") or "")
        if character_id not in EXPECTED_CHARACTER_UNITS:
            raise RuntimeError(f"unexpected playable character ID {character_id!r}")
        try:
            character_hash = int(hash_text, 16) & 0xFFFFFFFF
        except ValueError as exc:
            raise RuntimeError(
                f"invalid character hash for {character_id}: {hash_text!r}"
            ) from exc
        if character_id in seen_ids or character_hash in seen_hashes:
            raise RuntimeError("character catalog IDs and hashes must be unique")
        seen_ids.add(character_id)
        seen_hashes.add(character_hash)
        characters.append(
            {
                "id": character_id,
                "name": row.get("name"),
                "hash": character_hash,
                "expected_unit": EXPECTED_CHARACTER_UNITS[character_id],
            }
        )
    if seen_ids != set(EXPECTED_CHARACTER_UNITS):
        missing = sorted(set(EXPECTED_CHARACTER_UNITS) - seen_ids)
        raise RuntimeError(f"character catalog is missing expected characters: {missing}")
    return characters


def load_mastery_cap(path: Path) -> dict:
    connection = sqlite3.connect(path)
    try:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chara_master_exp'"
        ).fetchone()
        if table is None:
            raise RuntimeError("mastery database is missing chara_master_exp")
        values = [
            int(row[0])
            for row in connection.execute(
                "SELECT TotalMSP FROM chara_master_exp ORDER BY rowid"
            )
        ]
    finally:
        connection.close()
    if not values:
        raise RuntimeError("chara_master_exp contains no rows")
    cap = max(values)
    if cap != EXPECTED_MASTER_TOTAL or values[-1] != EXPECTED_MASTER_TOTAL:
        raise RuntimeError(
            "mastery database does not match the verified 2.0 cap: "
            f"max={cap}, final={values[-1]}"
        )
    return {
        "row_count": len(values),
        "master_level_50_total": values[-6],
        "master_break_totals": values[-5:],
        "final_total": cap,
    }


def map_character_units(save: GBFRSaveData, characters: list[dict]) -> list[dict]:
    by_hash = {row["hash"]: row for row in characters}
    mapped = []
    for record in save.find(id_type=1301):
        if record.kind != "uint" or record.value_count != 1:
            continue
        character_hash = first_value(save, record) & 0xFFFFFFFF
        character = by_hash.get(character_hash)
        if character is None:
            continue
        mapped.append({**character, "unit": record.unit_id})
    if len(mapped) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError(
            f"save mapped {len(mapped)} playable characters, expected {EXPECTED_CHARACTER_COUNT}"
        )
    mapped_ids = [row["id"] for row in mapped]
    if len(set(mapped_ids)) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError("save contains duplicate playable character hashes")
    mismatches = [
        (row["id"], row["unit"], row["expected_unit"])
        for row in mapped
        if row["unit"] != row["expected_unit"]
    ]
    if mismatches:
        raise RuntimeError(f"playable character unit mapping changed: {mismatches}")
    mapped.sort(key=lambda row: row["unit"])
    return mapped


def validate_character_progression_shape(
    save: GBFRSaveData, characters: list[dict]
) -> None:
    for character in characters:
        unit = character["unit"]
        required_record(save, 1323, unit, kinds=("int",), value_count=1)
        required_record(save, 1324, unit, kinds=("int",), value_count=1)
        required_record(save, 1325, unit, kinds=("int",), value_count=1)
        required_record(
            save,
            1326,
            unit,
            kinds=("byte",),
            value_count=SPECIALIZATION_STATE_WIDTH,
        )
        for slot in range(4):
            slot_unit = unit * 1000 + slot
            required_record(save, 1606, slot_unit, kinds=("uint",), value_count=1)
            required_record(
                save,
                1607,
                slot_unit,
                kinds=("int", "uint"),
                value_count=1,
            )


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    characters_path = args.characters.resolve()
    mastery_database_path = args.mastery_database.resolve()
    audit_path = args.audit.resolve()

    for path, label in (
        (input_path, "input save"),
        (characters_path, "character catalog"),
        (mastery_database_path, "mastery database"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if len({input_path, output_path, audit_path}) != 3:
        raise RuntimeError("input, output, and audit paths must be distinct")
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("refusing to overwrite an existing output or audit")

    input_sha = sha256_file(input_path)
    save = GBFRSaveData.open(input_path)
    if save.check_active_hash() is not True:
        raise RuntimeError("input save active hash is invalid")

    characters = map_character_units(save, load_characters(characters_path))
    mastery_database = load_mastery_cap(mastery_database_path)
    validate_character_progression_shape(save, characters)

    before = full_snapshot(save)
    input_header = dict(save.container.header)
    input_payload_size = save.container.payload_size
    input_record_count = len(save.records)

    wallet = required_record(save, 1112, 0, kinds=("int",), value_count=1)
    wallet_before = first_value(save, wallet)
    wallet_changed = set_if_changed(save, wallet, MASTER_POINTS_TARGET)

    character_rows = []
    allowed_1323_units = set()
    allowed_1606_units = set()
    allowed_1607_units = set()
    for character in characters:
        unit = character["unit"]
        specialization = required_record(
            save, 1323, unit, kinds=("int",), value_count=1
        )
        specialization_before = first_value(save, specialization)
        specialization_changed = set_if_changed(
            save, specialization, EXPECTED_MASTER_TOTAL
        )
        allowed_1323_units.add(unit)

        slot_rows = []
        for slot, (fallback_hash, fallback_name) in enumerate(
            EMPTY_SLOT_FALLBACKS
        ):
            slot_unit = unit * 1000 + slot
            stat_record = required_record(
                save, 1606, slot_unit, kinds=("uint",), value_count=1
            )
            value_record = required_record(
                save,
                1607,
                slot_unit,
                kinds=("int", "uint"),
                value_count=1,
            )
            stat_before = first_value(save, stat_record) & 0xFFFFFFFF
            value_before = first_value(save, value_record)
            filled_empty = False
            if stat_before == EMPTY_HASH:
                save.set_first_value(stat_record, fallback_hash)
                filled_empty = True
                allowed_1606_units.add(slot_unit)
            value_changed = set_if_changed(
                save, value_record, OVERMASTERY_MAX_VALUE
            )
            allowed_1607_units.add(slot_unit)
            slot_rows.append(
                {
                    "slot": slot,
                    "unit": slot_unit,
                    "stat_before": f"{stat_before:08X}",
                    "stat_after": f"{first_value(save, stat_record) & 0xFFFFFFFF:08X}",
                    "filled_empty": filled_empty,
                    "fallback_name": fallback_name if filled_empty else None,
                    "value_before": value_before,
                    "value_after": first_value(save, value_record),
                    "value_changed": value_changed,
                }
            )

        character_rows.append(
            {
                "character_id": character["id"],
                "name": character["name"],
                "unit": unit,
                "character_hash": f"{character['hash']:08X}",
                "specialization_before": specialization_before,
                "specialization_after": first_value(save, specialization),
                "specialization_changed": specialization_changed,
                "legacy_overmastery": slot_rows,
            }
        )

    specialization_board, specialization_masks = complete_specialization_board(
        save,
        mastery_database_path,
        characters,
        expected_new_units=args.expected_specialization_new_units,
        expected_target_sha256=args.expected_specialization_target_sha256,
        expected_final_active_layouts=args.expected_specialization_active_layouts,
    )

    after = full_snapshot(save)
    changes = changed_records(before, after)
    unexpected = []
    for change in changes:
        field_id = change["field_id"]
        unit_id = change["unit_id"]
        valid = change["changed_indexes"] == [0]
        if field_id == 1112:
            valid = valid and unit_id == 0
        elif field_id == 1323:
            valid = valid and unit_id in allowed_1323_units
        elif field_id == 1606:
            valid = valid and unit_id in allowed_1606_units
        elif field_id == 1607:
            valid = valid and unit_id in allowed_1607_units
        elif field_id == 1602:
            mask = specialization_masks.get(unit_id)
            valid = (
                valid
                and mask is not None
                and change["after"][0] == change["before"][0] | mask
            )
        else:
            valid = False
        if not valid:
            unexpected.append(change)
    if unexpected:
        raise RuntimeError(f"unexpected in-memory changes: {unexpected[:3]}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save.save_as(output_path, update_hash=True)
    if sha256_file(input_path) != input_sha:
        raise RuntimeError("offline input save changed during the run")

    output = GBFRSaveData.open(output_path)
    if output.check_active_hash() is not True:
        raise RuntimeError("output save active hash is invalid")
    if output.container.header != input_header:
        raise RuntimeError("Steam/account wrapper metadata changed")
    if output.container.payload_size != input_payload_size:
        raise RuntimeError("save payload size changed")
    if len(output.records) != input_record_count:
        raise RuntimeError("save record count changed")
    if full_snapshot(output) != after:
        raise RuntimeError("serialized records differ from verified in-memory candidate")

    output_characters = map_character_units(output, load_characters(characters_path))
    validate_character_progression_shape(output, output_characters)
    output_specialization_board, output_specialization_masks = (
        complete_specialization_board(
            output,
            mastery_database_path,
            output_characters,
            expected_new_units=0,
            expected_final_active_layouts=args.expected_specialization_active_layouts,
        )
    )
    if output_specialization_masks:
        raise RuntimeError("serialized specialization board was not idempotent")
    final_wallet = first_value(
        output,
        required_record(output, 1112, 0, kinds=("int",), value_count=1),
    )
    if final_wallet != MASTER_POINTS_TARGET:
        raise RuntimeError(f"Mastery Points persisted as {final_wallet}")
    for character in output_characters:
        unit = character["unit"]
        final_total = first_value(
            output,
            required_record(output, 1323, unit, kinds=("int",), value_count=1),
        )
        if final_total != EXPECTED_MASTER_TOTAL:
            raise RuntimeError(
                f"{character['id']} specialization persisted as {final_total}"
            )
        for slot in range(4):
            slot_unit = unit * 1000 + slot
            stat_value = first_value(
                output,
                required_record(
                    output, 1606, slot_unit, kinds=("uint",), value_count=1
                ),
            ) & 0xFFFFFFFF
            amount_value = first_value(
                output,
                required_record(
                    output,
                    1607,
                    slot_unit,
                    kinds=("int", "uint"),
                    value_count=1,
                ),
            )
            if stat_value == EMPTY_HASH:
                raise RuntimeError(
                    f"{character['id']} overmastery slot {slot} remained empty"
                )
            if amount_value != OVERMASTERY_MAX_VALUE:
                raise RuntimeError(
                    f"{character['id']} overmastery slot {slot} persisted as {amount_value}"
                )

    filled_slots = sum(
        int(slot["filled_empty"])
        for character in character_rows
        for slot in character["legacy_overmastery"]
    )
    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": {
            "path": str(input_path),
            "sha256": input_sha,
            "active_hash_ok": True,
        },
        "output": {
            "path": str(output_path),
            "sha256": sha256_file(output_path),
            "active_hash_ok": True,
            "steam_id": output.container.header.get("steam_id"),
            "size": output_path.stat().st_size,
            "record_count": len(output.records),
        },
        "mastery_database": mastery_database,
        "policy": {
            "mastery_points_target": MASTER_POINTS_TARGET,
            "specialization_total_target": EXPECTED_MASTER_TOTAL,
            "specialization_interpretation": "Master Lv 50 plus five Master Breaks",
            "preserved_fields": [1324, 1325, 1326, 1601, 1605],
            "legacy_overmastery_value_target": OVERMASTERY_MAX_VALUE,
            "existing_legacy_stat_types_preserved": True,
            "only_empty_legacy_stat_types_filled": True,
        },
        "counts": {
            "playable_characters": len(character_rows),
            "specialization_records_changed": sum(
                int(row["specialization_changed"]) for row in character_rows
            ),
            "specialization_board_1602_records_changed": len(
                specialization_masks
            ),
            "legacy_overmastery_slots": len(character_rows) * 4,
            "legacy_empty_stat_types_filled": filled_slots,
            "legacy_existing_stat_types_preserved": len(character_rows) * 4
            - filled_slots,
            "record_changes": len(changes),
        },
        "wallet": {
            "field_id": 1112,
            "before": wallet_before,
            "after": final_wallet,
            "changed": wallet_changed,
        },
        "characters": character_rows,
        "specialization_board": specialization_board,
        "serialized_specialization_board": {
            "counts": output_specialization_board["counts"],
            "target_sha256": output_specialization_board["target_sha256"],
            "idempotent": True,
        },
        "changes": changes,
        "validation": {
            "character_hash_mapping_exact": True,
            "database_cap_exact": True,
            "only_1112_1323_1602_1606_1607_changed": True,
            "all_29_specializations_maxed": True,
            "all_29_specialization_boards_10_10_10_20": True,
            "all_116_legacy_overmastery_values_maxed": True,
            "no_empty_legacy_overmastery_slots": True,
            "payload_size_unchanged": True,
            "record_count_unchanged": True,
            "steam_wrapper_unchanged": True,
            "active_hash_ok": True,
        },
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "audit": str(audit_path),
                "playable_characters": len(character_rows),
                "specialization_total": EXPECTED_MASTER_TOTAL,
                "specialization_board_new_units": len(specialization_masks),
                "specialization_board_target_sha256": specialization_board[
                    "target_sha256"
                ],
                "legacy_slots_maxed": len(character_rows) * 4,
                "empty_stat_types_filled": filled_slots,
                "record_changes": len(changes),
                "active_hash_ok": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
