"""Equip every playable character's current weapon with a verified QoL blessing.

The transform changes only the equipped weapon's Wrightstone shell field and
the three corresponding runtime trait lanes. It never edits character weapon
links, weapon progression, inventory Wrightstones, or story progress.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from build_all_sigils_strict import id_hash, reference_hash
from equip_legacy_gold_sigils import (
    EXPECTED_CHARACTER_COUNT,
    changed_records,
    first_value,
    full_snapshot,
    load_characters,
    map_character_units,
    protected_story_digest,
    required_record,
    sha256_file,
    u32,
)
from save_editor_api import GBFRSaveData, add_editor_argument


WEAPON_MIN_UNIT = 40_000
WEAPON_MAX_UNIT = 40_255
WEAPON_SLOT_FIELD = 2802
WEAPON_BLESSING_FIELD = 2816
TRAIT_FIELDS = (1701, 1702)
TRAIT_LEVEL = 99
EXPECTED_OUTER_ID = "ITEM_26_0131"
EXPECTED_TRAIT_IDS = (
    "SKILL_069_00",
    "SKILL_070_00",
    "SKILL_044_00",
)


def weapon_trait_unit(weapon_unit: int, lane: int) -> int:
    if not WEAPON_MIN_UNIT <= weapon_unit <= WEAPON_MAX_UNIT:
        raise ValueError(f"invalid weapon unit {weapon_unit}")
    if lane not in (0, 1, 2):
        raise ValueError(f"invalid weapon blessing lane {lane}")
    return 130_000_000 + (weapon_unit - WEAPON_MIN_UNIT) * 100 + lane


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline source save copy")
    parser.add_argument("output", type=Path, help="Separate offline output save")
    parser.add_argument("--characters", type=Path, required=True)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--expected-preset-sha256")
    add_editor_argument(parser)
    return parser.parse_args()


def load_preset(path: Path, expected_sha256: str | None) -> dict:
    if expected_sha256:
        actual = sha256_file(path)
        if actual != expected_sha256.strip().upper():
            raise RuntimeError(
                f"weapon blessing preset SHA-256 {actual} != {expected_sha256}"
            )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise RuntimeError("weapon blessing preset schema_version must be 1")
    outer = payload.get("outer")
    traits = payload.get("traits")
    if not isinstance(outer, dict) or not isinstance(traits, list):
        raise RuntimeError("weapon blessing preset is missing outer/traits")
    if outer.get("id") != EXPECTED_OUTER_ID:
        raise RuntimeError(f"weapon blessing outer must be {EXPECTED_OUTER_ID}")
    expected_outer_hash = f"{id_hash(EXPECTED_OUTER_ID):08X}"
    if str(outer.get("hash") or "").upper() != expected_outer_hash:
        raise RuntimeError("weapon blessing outer hash differs from its ID")
    if len(traits) != 3:
        raise RuntimeError("weapon blessing preset must contain three traits")
    resolved = []
    for lane, (row, expected_id) in enumerate(zip(traits, EXPECTED_TRAIT_IDS)):
        if row.get("lane") != lane or row.get("id") != expected_id:
            raise RuntimeError(f"weapon blessing lane {lane} must be {expected_id}")
        expected_hash = reference_hash(expected_id)
        if str(row.get("hash") or "").upper() != f"{expected_hash:08X}":
            raise RuntimeError(f"weapon blessing lane {lane} hash differs from its ID")
        if row.get("level") != TRAIT_LEVEL:
            raise RuntimeError(f"weapon blessing lane {lane} must be level 99")
        resolved.append(
            {
                "lane": lane,
                "id": expected_id,
                "hash": expected_hash,
                "level": TRAIT_LEVEL,
                "name": str(row.get("name") or expected_id),
            }
        )
    payload["resolved_outer_hash"] = int(expected_outer_hash, 16)
    payload["resolved_traits"] = resolved
    return payload


def collect_weapon_slots(save: GBFRSaveData) -> dict[int, int]:
    slots = {}
    units = set()
    for record in save.find(id_type=WEAPON_SLOT_FIELD):
        unit = int(record.unit_id)
        if not WEAPON_MIN_UNIT <= unit <= WEAPON_MAX_UNIT:
            continue
        if record.kind != "uint" or record.value_count != 1:
            raise RuntimeError(f"weapon {unit} has an invalid 2802 record")
        slot = int(first_value(save, record))
        units.add(unit)
        # Unused preallocated weapon records legitimately share slot 0. Only
        # positive inventory slots participate in character equipment links.
        if slot <= 0:
            continue
        if slot in slots:
            raise RuntimeError(
                f"weapon slot {slot} is duplicated by units {slots[slot]} and {unit}"
            )
        slots[slot] = unit
    if len(units) != WEAPON_MAX_UNIT - WEAPON_MIN_UNIT + 1:
        raise RuntimeError(f"expected 256 weapon records, found {len(units)}")
    return slots


def resolve_equipped_weapons(
    save: GBFRSaveData,
    characters: list[dict],
    slot_to_weapon: dict[int, int],
) -> list[dict]:
    rows = []
    selected = set()
    for character in characters:
        equipped = required_record(
            save,
            1402,
            character["unit"],
            kinds=("uint",),
            value_count=1,
        )
        slot = int(first_value(save, equipped))
        weapon_unit = slot_to_weapon.get(slot)
        if weapon_unit is None:
            raise RuntimeError(
                f"{character['id']} references missing weapon slot {slot}"
            )
        if weapon_unit in selected:
            raise RuntimeError(f"weapon unit {weapon_unit} is equipped more than once")
        selected.add(weapon_unit)
        rows.append({**character, "weapon_slot": slot, "weapon_unit": weapon_unit})
    if len(rows) != EXPECTED_CHARACTER_COUNT or len(selected) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError("did not resolve 29 unique equipped weapons")
    return rows


def apply_preset(
    save: GBFRSaveData,
    equipped: list[dict],
    preset: dict,
) -> list[dict]:
    result = []
    for row in equipped:
        weapon_unit = row["weapon_unit"]
        outer = required_record(
            save,
            WEAPON_BLESSING_FIELD,
            weapon_unit,
            kinds=("uint",),
            value_count=1,
        )
        before_outer = u32(first_value(save, outer))
        save.set_first_value(outer, preset["resolved_outer_hash"])
        lanes = []
        for trait in preset["resolved_traits"]:
            unit = weapon_trait_unit(weapon_unit, trait["lane"])
            hash_record = required_record(
                save,
                TRAIT_FIELDS[0],
                unit,
                kinds=("uint",),
                value_count=1,
            )
            level_record = required_record(
                save,
                TRAIT_FIELDS[1],
                unit,
                kinds=("int",),
                value_count=1,
            )
            before = {
                "hash": f"{u32(first_value(save, hash_record)):08X}",
                "level": int(first_value(save, level_record)),
            }
            save.set_first_value(hash_record, trait["hash"])
            save.set_first_value(level_record, trait["level"])
            lanes.append({**trait, "trait_unit": unit, "before": before})
        result.append(
            {
                "character_id": row["id"],
                "name": row["name"],
                "character_unit": row["unit"],
                "weapon_slot": row["weapon_slot"],
                "weapon_unit": weapon_unit,
                "outer_before": f"{before_outer:08X}",
                "outer_after": f"{preset['resolved_outer_hash']:08X}",
                "traits": lanes,
            }
        )
    return result


def verify_preset(save: GBFRSaveData, equipped: list[dict], preset: dict) -> None:
    for row in equipped:
        weapon_unit = row["weapon_unit"]
        outer = required_record(
            save,
            WEAPON_BLESSING_FIELD,
            weapon_unit,
            kinds=("uint",),
            value_count=1,
        )
        if u32(first_value(save, outer)) != preset["resolved_outer_hash"]:
            raise RuntimeError(f"weapon {weapon_unit} blessing outer verification failed")
        for trait in preset["resolved_traits"]:
            unit = weapon_trait_unit(weapon_unit, trait["lane"])
            actual = (
                u32(
                    first_value(
                        save,
                        required_record(
                            save,
                            1701,
                            unit,
                            kinds=("uint",),
                            value_count=1,
                        ),
                    )
                ),
                int(
                    first_value(
                        save,
                        required_record(
                            save,
                            1702,
                            unit,
                            kinds=("int",),
                            value_count=1,
                        ),
                    )
                ),
            )
            if actual != (trait["hash"], trait["level"]):
                raise RuntimeError(
                    f"weapon {weapon_unit} lane {trait['lane']} verification failed"
                )


def require_whitelisted_changes(changes: list[dict], equipped: list[dict]) -> None:
    weapon_units = {row["weapon_unit"] for row in equipped}
    trait_units = {
        weapon_trait_unit(row["weapon_unit"], lane)
        for row in equipped
        for lane in (0, 1, 2)
    }
    unexpected = []
    for change in changes:
        valid = change["changed_indexes"] == [0]
        if change["unit_id"] in weapon_units:
            valid = valid and change["field_id"] == WEAPON_BLESSING_FIELD
        elif change["unit_id"] in trait_units:
            valid = valid and change["field_id"] in TRAIT_FIELDS
        else:
            valid = False
        if not valid:
            unexpected.append(change)
    if unexpected:
        raise RuntimeError(f"unexpected weapon blessing changes: {unexpected[:3]}")


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    character_path = args.characters.resolve()
    preset_path = args.preset.resolve()
    audit_path = args.audit.resolve()
    for path, label in (
        (input_path, "input save"),
        (character_path, "character catalog"),
        (preset_path, "weapon blessing preset"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if len({input_path, output_path, audit_path}) != 3:
        raise RuntimeError("input, output, and audit paths must be distinct")
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("refusing to overwrite an output or audit")

    input_sha256 = sha256_file(input_path)
    preset = load_preset(preset_path, args.expected_preset_sha256)
    save = GBFRSaveData.open(input_path)
    if save.check_active_hash() is not True:
        raise RuntimeError("input save active hash is invalid")
    header = dict(save.container.header)
    payload_size = save.container.payload_size
    record_count = len(save.records)
    story_digest = protected_story_digest(save)
    characters = map_character_units(save, load_characters(character_path))
    slot_to_weapon = collect_weapon_slots(save)
    equipped = resolve_equipped_weapons(save, characters, slot_to_weapon)

    before = full_snapshot(save)
    rows = apply_preset(save, equipped, preset)
    after = full_snapshot(save)
    changes = changed_records(before, after)
    require_whitelisted_changes(changes, equipped)
    verify_preset(save, equipped, preset)
    if protected_story_digest(save) != story_digest:
        raise RuntimeError("protected main-story fields changed in memory")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save.save_as(output_path, update_hash=True)
    if sha256_file(input_path) != input_sha256:
        raise RuntimeError("offline input save changed during the run")

    output = GBFRSaveData.open(output_path)
    if output.check_active_hash() is not True:
        raise RuntimeError("output save active hash is invalid")
    if output.container.header != header:
        raise RuntimeError("Steam/account wrapper metadata changed")
    if output.container.payload_size != payload_size or len(output.records) != record_count:
        raise RuntimeError("save payload size or record count changed")
    if full_snapshot(output) != after:
        raise RuntimeError("serialized records differ from verified in-memory data")
    if protected_story_digest(output) != story_digest:
        raise RuntimeError("protected main-story fields changed on disk")
    output_characters = map_character_units(output, load_characters(character_path))
    output_equipped = resolve_equipped_weapons(
        output,
        output_characters,
        collect_weapon_slots(output),
    )
    if [row["weapon_unit"] for row in output_equipped] != [
        row["weapon_unit"] for row in equipped
    ]:
        raise RuntimeError("equipped weapon relationships changed")
    verify_preset(output, output_equipped, preset)

    idempotent_before = full_snapshot(output)
    apply_preset(output, output_equipped, preset)
    idempotent_changes = changed_records(idempotent_before, full_snapshot(output))
    if idempotent_changes:
        raise RuntimeError(f"weapon blessing transform is not idempotent: {idempotent_changes[:3]}")

    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": {"path": str(input_path), "sha256": input_sha256},
        "output": {
            "path": str(output_path),
            "sha256": sha256_file(output_path),
            "active_hash_ok": True,
            "size": output_path.stat().st_size,
            "record_count": len(output.records),
        },
        "preset": {
            "path": str(preset_path),
            "sha256": sha256_file(preset_path),
            "id": preset.get("id"),
            "outer_id": EXPECTED_OUTER_ID,
            "outer_hash": f"{preset['resolved_outer_hash']:08X}",
            "trait_level": TRAIT_LEVEL,
            "traits": preset["resolved_traits"],
        },
        "counts": {
            "characters": len(equipped),
            "equipped_weapons": len(equipped),
            "trait_lanes": len(equipped) * 3,
            "record_changes": len(changes),
            "idempotent_changes": 0,
        },
        "policy": {
            "weapon_link_field_1402_preserved": True,
            "weapon_progression_preserved": True,
            "inventory_wrightstones_preserved": True,
            "protected_story_preserved": True,
            "runtime_trait_units_use_physical_weapon_units": True,
        },
        "characters": rows,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
