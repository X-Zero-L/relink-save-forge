import argparse
import hashlib
import json
import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gbfr_hash import gbfr_hash
from save_editor_api import GBFRSaveData, UnitRecord, add_editor_argument


EMPTY_HASH = 0x887AE0B0
MAIN_FIELDS = (2510, 2511, 2520, 2522)
WEAPON_FIELDS = (2802, 2803, 2804, 2805, 2806, 2807, 2813, 2814, 2815, 2816)
WEAPON_SLOT_MIN = 40_000
WEAPON_SLOT_MAX = 40_255
CATALOG_WEAPON_RE = re.compile(r"^WEP_(PL\d{4})_(\d{2})$")
# Every real baseline weapon with a non-zero 2807 value also carries bit 0x10.
# A real max-awakening record also proves 2813=0 and 2816=EMPTY are valid together.
AWAKENED_FLAG = 0x10
OWNED_FLAG = 0x01

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEAPON_CATALOG = REPOSITORY_ROOT / "catalogs" / "weapons.json"
DEFAULT_CHARACTER_CATALOG = REPOSITORY_ROOT / "catalogs" / "characters.json"


@dataclass(frozen=True)
class WeaponEntry:
    item_id: str
    hash_value: int
    character_id: str
    collection_slot: int
    id_suffix: int
    database_key: str
    max_awakening: int = 0


@dataclass(frozen=True)
class WeaponIdentity:
    unit_id: int
    slot_id: int
    weapon_hash: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild every verified Relink 2.0 weapon as a complete instance in a candidate save. "
            "The script refuses to write into the live GBFR save directory."
        )
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--original",
        type=Path,
        required=True,
        help="Known-good pre-forge save used to prove the baseline instance layout",
    )
    parser.add_argument(
        "--probe",
        type=Path,
        required=True,
        help="Known-good save made after forging and equipping exactly one weapon",
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--weapon-catalog", type=Path, default=DEFAULT_WEAPON_CATALOG)
    parser.add_argument("--character-catalog", type=Path, default=DEFAULT_CHARACTER_CATALOG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    add_editor_argument(parser)
    return parser.parse_args()


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def refuse_live_output(output: Path) -> None:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    live_directory = resolved(local_app_data / "GBFR" / "Saved" / "SaveGames")
    target = resolved(output)
    if target == live_directory or live_directory in target.parents:
        raise RuntimeError(f"Refusing to write a candidate into the live save directory: {target}")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def first_record(save: GBFRSaveData, id_type: int, unit_id: int) -> UnitRecord:
    rows = save.find(id_type=id_type, unit_id=unit_id)
    if len(rows) != 1:
        raise RuntimeError(f"Expected one field {id_type} for unit {unit_id}, found {len(rows)}")
    return rows[0]


def scalar(save: GBFRSaveData, fields: dict[int, UnitRecord], id_type: int) -> int:
    record = fields.get(id_type)
    if record is None:
        raise RuntimeError(f"Weapon unit is missing required field {id_type}")
    return int(save.get_first_value(record, 0))


def set_scalar(save: GBFRSaveData, fields: dict[int, UnitRecord], id_type: int, value: int) -> None:
    record = fields.get(id_type)
    if record is None:
        raise RuntimeError(f"Weapon unit is missing required field {id_type}")
    save.set_first_value(record, value)


def weapon_values(save: GBFRSaveData, fields: dict[int, UnitRecord]) -> dict[int, int]:
    return {field_id: scalar(save, fields, field_id) for field_id in WEAPON_FIELDS}


def is_occupied(values: dict[int, int]) -> bool:
    return (values[2803] & 0xFFFFFFFF) not in (0, EMPTY_HASH)


def empty_values() -> dict[int, int]:
    return {
        2802: 0,
        2803: EMPTY_HASH,
        2804: 0,
        2805: 0,
        2806: 0,
        2807: 0,
        2813: 0,
        2814: EMPTY_HASH,
        2815: 0,
        2816: EMPTY_HASH,
    }


def reset_weapon(save: GBFRSaveData, fields: dict[int, UnitRecord]) -> None:
    for field_id, value in empty_values().items():
        set_scalar(save, fields, field_id, value)


def main_digest(save: GBFRSaveData) -> str:
    payload: list[tuple[str, int, int, list[Any]]] = []
    for record in save.records:
        if record.id_type in MAIN_FIELDS:
            payload.append((record.kind, record.id_type, record.unit_id, list(save.get_values(record))))
    payload.sort(key=lambda row: (row[0], row[1], row[2]))
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def weapon_groups(save: GBFRSaveData) -> dict[int, dict[int, UnitRecord]]:
    groups = save.group_by_unit(WEAPON_FIELDS)
    expected_units = set(range(WEAPON_SLOT_MIN, WEAPON_SLOT_MAX + 1))
    if set(groups) != expected_units:
        missing = sorted(expected_units - set(groups))
        extra = sorted(set(groups) - expected_units)
        raise RuntimeError(f"Unexpected weapon unit layout; missing={missing[:8]} extra={extra[:8]}")
    for unit_id, fields in groups.items():
        if set(fields) != set(WEAPON_FIELDS):
            raise RuntimeError(
                f"Weapon unit {unit_id} has incomplete fields: {sorted(fields)}; expected {list(WEAPON_FIELDS)}"
            )
    return groups


def identities(save: GBFRSaveData) -> list[WeaponIdentity]:
    result: list[WeaponIdentity] = []
    for unit_id, fields in sorted(weapon_groups(save).items()):
        values = weapon_values(save, fields)
        if is_occupied(values):
            result.append(
                WeaponIdentity(
                    unit_id=unit_id,
                    slot_id=values[2802] & 0xFFFFFFFF,
                    weapon_hash=values[2803] & 0xFFFFFFFF,
                )
            )
    return result


def validate_probe(original: GBFRSaveData, probe: GBFRSaveData) -> tuple[WeaponIdentity, dict[int, int]]:
    original_ids = identities(original)
    probe_ids = identities(probe)
    if len(original_ids) != 101:
        raise RuntimeError(f"Original weapon baseline must contain 101 instances, found {len(original_ids)}")
    if len(probe_ids) != 102:
        raise RuntimeError(f"Forge probe must contain 102 instances, found {len(probe_ids)}")

    original_set = set(original_ids)
    probe_set = set(probe_ids)
    removed = original_set - probe_set
    added = probe_set - original_set
    if removed or len(added) != 1:
        raise RuntimeError(f"Forge probe identity delta is not exactly one addition: removed={removed}, added={added}")

    added_identity = next(iter(added))
    original_groups = weapon_groups(original)
    probe_groups = weapon_groups(probe)
    before = weapon_values(original, original_groups[added_identity.unit_id])
    after = weapon_values(probe, probe_groups[added_identity.unit_id])
    expected_empty = empty_values()
    if before != expected_empty:
        raise RuntimeError(f"Forge probe source unit {added_identity.unit_id} was not a canonical empty slot: {before}")
    if after[2802] != added_identity.slot_id or after[2803] != added_identity.weapon_hash:
        raise RuntimeError("Forge probe identity does not match its complete weapon record")
    if after[2804] <= 0 or after[2805] <= 0 or after[2815] == 0:
        raise RuntimeError(f"Forge probe did not produce a complete real weapon instance: {after}")
    for field_id in (2806, 2807, 2813):
        if after[field_id] != 0:
            raise RuntimeError(f"Unexpected forged default for field {field_id}: {after[field_id]}")
    for field_id in (2814, 2816):
        if after[field_id] != EMPTY_HASH:
            raise RuntimeError(f"Unexpected forged empty hash for field {field_id}: 0x{after[field_id]:08X}")

    original_max = scalar(original, {2801: first_record(original, 2801, 0)}, 2801)
    probe_max = scalar(probe, {2801: first_record(probe, 2801, 0)}, 2801)
    if added_identity.slot_id != original_max + 1 or probe_max != added_identity.slot_id:
        raise RuntimeError(
            f"Forge probe slot sequence is inconsistent: original max={original_max}, "
            f"added={added_identity.slot_id}, probe max={probe_max}"
        )
    return added_identity, after


def load_character_catalog(path: Path) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    document = read_json(path)
    items = document.get("items")
    if document.get("count") != 29 or not isinstance(items, list) or len(items) != 29:
        raise RuntimeError("Character catalog must contain exactly 29 playable characters")
    by_hash: dict[int, dict[str, Any]] = {}
    for item in items:
        character_id = str(item["id"]).upper()
        hash_value = int(str(item["hash"]), 16)
        if gbfr_hash(character_id) & 0xFFFFFFFF != hash_value:
            raise RuntimeError(f"Character hash mismatch for {character_id}")
        if hash_value in by_hash:
            raise RuntimeError(f"Duplicate character hash 0x{hash_value:08X}")
        by_hash[hash_value] = item
    return items, by_hash


def load_weapon_catalog(path: Path, character_ids: set[str]) -> list[WeaponEntry]:
    document = read_json(path)
    items = document.get("items")
    if document.get("count") != 174 or not isinstance(items, list) or len(items) != 174:
        raise RuntimeError("Weapon catalog must contain exactly 174 verified weapons")

    entries: list[WeaponEntry] = []
    seen_ids: set[str] = set()
    seen_hashes: set[int] = set()
    grouped: dict[str, list[WeaponEntry]] = defaultdict(list)
    for item in items:
        item_id = str(item["id"]).upper()
        match = CATALOG_WEAPON_RE.fullmatch(item_id)
        if match is None:
            raise RuntimeError(f"Rejected non-standard/internal weapon ID in catalog: {item_id}")
        character_id = str(item["character_id"]).upper()
        if match.group(1) != character_id or character_id not in character_ids:
            raise RuntimeError(f"Weapon/character mismatch for {item_id}: {character_id}")
        if item.get("database_match") is not True:
            raise RuntimeError(f"Weapon is not confirmed by the live database: {item_id}")
        hash_value = int(str(item["hash"]), 16)
        if gbfr_hash(item_id) & 0xFFFFFFFF != hash_value:
            raise RuntimeError(f"Weapon hash mismatch for {item_id}")
        if item_id in seen_ids or hash_value in seen_hashes:
            raise RuntimeError(f"Duplicate weapon catalog entry: {item_id} / 0x{hash_value:08X}")
        entry = WeaponEntry(
            item_id=item_id,
            hash_value=hash_value,
            character_id=character_id,
            collection_slot=int(item["collection_slot"]),
            id_suffix=int(item["id_suffix"]),
            database_key=str(item["database_key"]),
        )
        seen_ids.add(item_id)
        seen_hashes.add(hash_value)
        entries.append(entry)
        grouped[character_id].append(entry)

    if set(grouped) != character_ids:
        raise RuntimeError(f"Weapon catalog character set mismatch: {sorted(set(grouped) ^ character_ids)}")
    special_suffixes = {1, 2, 3, 4, 6, 7}
    standard_suffixes = {1, 2, 3, 4, 5, 6}
    for character_id, group in grouped.items():
        if len(group) != 6 or {entry.collection_slot for entry in group} != set(range(1, 7)):
            raise RuntimeError(f"{character_id} does not have six ordered collection weapons")
        expected = special_suffixes if character_id in {"PL2100", "PL2200", "PL2300"} else standard_suffixes
        actual = {entry.id_suffix for entry in group}
        if actual != expected:
            raise RuntimeError(f"{character_id} weapon suffix set is {sorted(actual)}, expected {sorted(expected)}")
    return entries


def attach_database_metadata(database: Path, entries: list[WeaponEntry], character_ids: set[str]) -> list[WeaponEntry]:
    connection = sqlite3.connect(database)
    try:
        database_characters = {str(row[0]).upper() for row in connection.execute("SELECT CharId FROM chara")}
        missing_characters = character_ids - database_characters
        if missing_characters:
            raise RuntimeError(f"Characters missing from live database: {sorted(missing_characters)}")
        rows = list(
            connection.execute(
                "SELECT Key, WeaponId, WeaponId2, LastAwakeningLevel, CharaId, MinFeatureVersion FROM weapon"
            )
        )
    finally:
        connection.close()

    result: list[WeaponEntry] = []
    for entry in entries:
        aliases = {entry.item_id, f"{entry.hash_value:08X}"}
        related = [
            row
            for row in rows
            if str(row[4]).upper() == entry.character_id
            and any(str(value or "").upper() in aliases for value in row[:3])
        ]
        if not related:
            raise RuntimeError(f"Weapon has no matching row in live database: {entry.item_id}")
        if any(int(row[5]) > 5 for row in related):
            raise RuntimeError(f"Weapon requires a future feature version: {entry.item_id}")
        max_awakening = max(int(row[3]) for row in related)
        if max_awakening not in (0, 10):
            raise RuntimeError(f"Unexpected maximum awakening {max_awakening} for {entry.item_id}")
        result.append(
            WeaponEntry(
                item_id=entry.item_id,
                hash_value=entry.hash_value,
                character_id=entry.character_id,
                collection_slot=entry.collection_slot,
                id_suffix=entry.id_suffix,
                database_key=entry.database_key,
                max_awakening=max_awakening,
            )
        )
    return result


def find_character_units(
    save: GBFRSaveData, character_by_hash: dict[int, dict[str, Any]]
) -> dict[str, int]:
    result: dict[str, int] = {}
    groups = save.group_by_unit((1301, 1402))
    for unit_id, fields in groups.items():
        hash_record = fields.get(1301)
        if hash_record is None:
            continue
        hash_value = int(save.get_first_value(hash_record, 0)) & 0xFFFFFFFF
        item = character_by_hash.get(hash_value)
        if item is None:
            continue
        character_id = str(item["id"]).upper()
        if character_id in result:
            raise RuntimeError(f"Duplicate save mapping for {character_id}")
        if 1402 not in fields:
            raise RuntimeError(f"Character {character_id} unit {unit_id} lacks field 1402")
        result[character_id] = unit_id
    if len(result) != 29:
        missing = sorted(str(item["id"]).upper() for item in character_by_hash.values() if str(item["id"]).upper() not in result)
        raise RuntimeError(f"Save does not contain all 29 playable character mappings: {missing}")
    return result


def max_template_values(original: GBFRSaveData) -> tuple[int, int, int]:
    occupied = []
    for fields in weapon_groups(original).values():
        values = weapon_values(original, fields)
        if is_occupied(values):
            occupied.append(values)
    max_xp = max(values[2804] for values in occupied)
    max_uncap = max(values[2805] for values in occupied)
    max_plus = max(values[2806] for values in occupied)
    if (max_xp, max_uncap, max_plus) != (162_540, 6, 99):
        raise RuntimeError(
            f"Real max templates changed unexpectedly: xp={max_xp}, uncap={max_uncap}, plus={max_plus}"
        )
    return max_xp, max_uncap, max_plus


def audit_state(
    save: GBFRSaveData,
    entries: list[WeaponEntry],
    character_units: dict[str, int],
) -> dict[str, Any]:
    groups = weapon_groups(save)
    occupied: list[dict[str, int]] = []
    by_hash: dict[int, list[dict[str, int]]] = defaultdict(list)
    by_slot: dict[int, list[dict[str, int]]] = defaultdict(list)
    shell_units: list[int] = []
    canonical_empty = empty_values()
    for unit_id, fields in sorted(groups.items()):
        values = weapon_values(save, fields)
        if is_occupied(values):
            row = {
                "unit_id": unit_id,
                "slot_id": values[2802] & 0xFFFFFFFF,
                "weapon_hash": values[2803] & 0xFFFFFFFF,
            }
            occupied.append(row)
            by_hash[row["weapon_hash"]].append(row)
            by_slot[row["slot_id"]].append(row)
        elif values != canonical_empty:
            shell_units.append(unit_id)

    catalog_hashes = {entry.hash_value for entry in entries}
    official_missing = sorted(catalog_hashes - set(by_hash))
    official_duplicates = {
        f"{hash_value:08X}": rows for hash_value, rows in by_hash.items() if hash_value in catalog_hashes and len(rows) != 1
    }
    slot_duplicates = {str(slot_id): rows for slot_id, rows in by_slot.items() if slot_id == 0 or len(rows) != 1}

    entry_by_character: dict[str, list[WeaponEntry]] = defaultdict(list)
    for entry in entries:
        entry_by_character[entry.character_id].append(entry)
    equipped: list[dict[str, Any]] = []
    for character_id, unit_id in sorted(character_units.items()):
        strongest = max(entry_by_character[character_id], key=lambda item: item.collection_slot)
        instance = by_hash.get(strongest.hash_value, [])
        equipped_slot = int(save.get_first_value(first_record(save, 1402, unit_id), 0)) & 0xFFFFFFFF
        target_slot = instance[0]["slot_id"] if len(instance) == 1 else None
        equipped.append(
            {
                "character_id": character_id,
                "unit_id": unit_id,
                "weapon_id": strongest.item_id,
                "weapon_hash": f"{strongest.hash_value:08X}",
                "slot_id": target_slot,
                "field_1402": equipped_slot,
                "matches": target_slot == equipped_slot,
            }
        )

    max_slot_id = int(save.get_first_value(first_record(save, 2801, 0), 0)) & 0xFFFFFFFF
    return {
        "occupied_count": len(occupied),
        "empty_count": len(groups) - len(occupied),
        "official_count": len(catalog_hashes & set(by_hash)),
        "out_of_catalog_count": len([row for row in occupied if row["weapon_hash"] not in catalog_hashes]),
        "out_of_catalog": [
            {**row, "weapon_hash": f"{row['weapon_hash']:08X}"}
            for row in occupied
            if row["weapon_hash"] not in catalog_hashes
        ],
        "missing_official_hashes": [f"{value:08X}" for value in official_missing],
        "official_duplicates": official_duplicates,
        "slot_duplicates": slot_duplicates,
        "shell_units": shell_units,
        "max_slot_id": max_slot_id,
        "actual_max_slot_id": max((row["slot_id"] for row in occupied), default=0),
        "equipped": equipped,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    input_path = resolved(args.input)
    original_path = resolved(args.original)
    probe_path = resolved(args.probe)
    database_path = resolved(args.database)
    weapon_catalog_path = resolved(args.weapon_catalog)
    character_catalog_path = resolved(args.character_catalog)
    output_path = resolved(args.output)
    audit_path = resolved(args.audit)

    refuse_live_output(output_path)
    refuse_live_output(audit_path)
    if output_path == input_path:
        raise RuntimeError("Output must be a distinct candidate file; in-place save editing is forbidden")
    if audit_path == output_path:
        raise RuntimeError("Audit JSON and candidate save cannot use the same path")
    for path in (
        input_path,
        original_path,
        probe_path,
        database_path,
        weapon_catalog_path,
        character_catalog_path,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    original = GBFRSaveData.open(original_path)
    probe = GBFRSaveData.open(probe_path)
    save = GBFRSaveData.open(input_path)
    for label, opened in (("original", original), ("probe", probe), ("input", save)):
        if opened.check_active_hash() is not True:
            raise RuntimeError(f"{label} save has an invalid active hash")

    main_before = main_digest(save)
    probe_added, forged_template = validate_probe(original, probe)
    max_xp, max_uncap, max_plus = max_template_values(original)
    character_items, character_by_hash = load_character_catalog(character_catalog_path)
    character_ids = {str(item["id"]).upper() for item in character_items}
    entries = load_weapon_catalog(weapon_catalog_path, character_ids)
    entries = attach_database_metadata(database_path, entries, character_ids)
    catalog_hashes = {entry.hash_value for entry in entries}
    entry_by_hash = {entry.hash_value: entry for entry in entries}
    character_units = find_character_units(save, character_by_hash)

    trusted_identities = set(identities(probe))
    input_identities = set(identities(save))
    missing_trusted = trusted_identities - input_identities
    if missing_trusted:
        raise RuntimeError(f"Input no longer contains all 102 real baseline/probe weapon instances: {missing_trusted}")

    groups = weapon_groups(save)
    cleared_shells: list[dict[str, Any]] = []
    canonical_empty = empty_values()
    for unit_id, fields in sorted(groups.items()):
        values = weapon_values(save, fields)
        if not is_occupied(values) and values != canonical_empty:
            cleared_shells.append({"unit_id": unit_id, "reason": "noncanonical_empty_record", "before": values})
            reset_weapon(save, fields)

    occupied_candidates: list[tuple[int, dict[int, UnitRecord], dict[int, int], bool]] = []
    for unit_id, fields in sorted(groups.items()):
        values = weapon_values(save, fields)
        if not is_occupied(values):
            continue
        identity = WeaponIdentity(unit_id, values[2802] & 0xFFFFFFFF, values[2803] & 0xFFFFFFFF)
        occupied_candidates.append((unit_id, fields, values, identity in trusted_identities))

    occupied_candidates.sort(key=lambda row: (not row[3], row[0]))
    used_slots: set[int] = set()
    used_official_hashes: set[int] = set()
    kept_units: set[int] = set()
    for unit_id, fields, values, trusted in occupied_candidates:
        slot_id = values[2802] & 0xFFFFFFFF
        weapon_hash = values[2803] & 0xFFFFFFFF
        reason: str | None = None
        if slot_id == 0:
            reason = "occupied_hash_with_zero_instance_id"
        elif slot_id in used_slots:
            reason = "duplicate_instance_id"
        elif weapon_hash in catalog_hashes and weapon_hash in used_official_hashes:
            reason = "duplicate_official_weapon"
        elif weapon_hash not in catalog_hashes and not trusted:
            reason = "untrusted_out_of_catalog_weapon"
        if reason is not None:
            if trusted:
                raise RuntimeError(f"Trusted real weapon {unit_id} violates {reason}")
            cleared_shells.append(
                {
                    "unit_id": unit_id,
                    "reason": reason,
                    "slot_id": slot_id,
                    "weapon_hash": f"{weapon_hash:08X}",
                }
            )
            reset_weapon(save, fields)
            continue
        kept_units.add(unit_id)
        used_slots.add(slot_id)
        if weapon_hash in catalog_hashes:
            used_official_hashes.add(weapon_hash)

    missing_entries = [entry for entry in entries if entry.hash_value not in used_official_hashes]
    reusable_units = [
        unit_id
        for unit_id, fields in sorted(groups.items())
        if unit_id not in kept_units and not is_occupied(weapon_values(save, fields))
    ]
    if len(reusable_units) < len(missing_entries):
        raise RuntimeError(f"Need {len(missing_entries)} reusable weapon records, found {len(reusable_units)}")

    old_max_slot = max(used_slots, default=0)
    next_slot_id = old_max_slot + 1
    added: list[dict[str, Any]] = []
    for unit_id, entry in zip(reusable_units, missing_entries):
        fields = groups[unit_id]
        for field_id, value in forged_template.items():
            set_scalar(save, fields, field_id, value)
        flags = forged_template[2815] | OWNED_FLAG
        if entry.max_awakening > 0:
            flags |= AWAKENED_FLAG
        values = {
            2802: next_slot_id,
            2803: entry.hash_value,
            2804: max_xp,
            2805: max_uncap,
            2806: max_plus,
            2807: entry.max_awakening,
            2813: forged_template[2813],
            2814: forged_template[2814],
            2815: flags,
            2816: forged_template[2816],
        }
        for field_id, value in values.items():
            set_scalar(save, fields, field_id, value)
        added.append(
            {
                "unit_id": unit_id,
                "slot_id": next_slot_id,
                "weapon_id": entry.item_id,
                "weapon_hash": f"{entry.hash_value:08X}",
                "max_awakening": entry.max_awakening,
            }
        )
        used_slots.add(next_slot_id)
        used_official_hashes.add(entry.hash_value)
        next_slot_id += 1

    if added:
        added_slot_ids = [row["slot_id"] for row in added]
        expected_slot_ids = list(range(old_max_slot + 1, old_max_slot + 1 + len(added)))
        if added_slot_ids != expected_slot_ids:
            raise RuntimeError("New weapon instance IDs are not unique and continuous")

    official_instances: dict[int, tuple[int, dict[int, UnitRecord]]] = {}
    for unit_id, fields in sorted(groups.items()):
        values = weapon_values(save, fields)
        weapon_hash = values[2803] & 0xFFFFFFFF
        entry = entry_by_hash.get(weapon_hash)
        if entry is None:
            continue
        if weapon_hash in official_instances:
            raise RuntimeError(f"Duplicate official weapon remains after cleanup: 0x{weapon_hash:08X}")
        set_scalar(save, fields, 2804, max_xp)
        set_scalar(save, fields, 2805, max_uncap)
        set_scalar(save, fields, 2806, max_plus)
        set_scalar(save, fields, 2807, entry.max_awakening)
        flags = scalar(save, fields, 2815) | OWNED_FLAG
        if entry.max_awakening > 0:
            flags |= AWAKENED_FLAG
        set_scalar(save, fields, 2815, flags)
        official_instances[weapon_hash] = (unit_id, fields)

    if set(official_instances) != catalog_hashes:
        missing = sorted(catalog_hashes - set(official_instances))
        raise RuntimeError(f"Official weapon rebuild is incomplete: {[f'{value:08X}' for value in missing]}")

    entries_by_character: dict[str, list[WeaponEntry]] = defaultdict(list)
    for entry in entries:
        entries_by_character[entry.character_id].append(entry)
    equip_changes: list[dict[str, Any]] = []
    for character_id, unit_id in sorted(character_units.items()):
        strongest = max(entries_by_character[character_id], key=lambda item: item.collection_slot)
        weapon_unit, weapon_fields = official_instances[strongest.hash_value]
        slot_id = scalar(save, weapon_fields, 2802) & 0xFFFFFFFF
        character_weapon = first_record(save, 1402, unit_id)
        before_slot = int(save.get_first_value(character_weapon, 0)) & 0xFFFFFFFF
        save.set_first_value(character_weapon, slot_id)
        equip_changes.append(
            {
                "character_id": character_id,
                "unit_id": unit_id,
                "before_slot_id": before_slot,
                "after_slot_id": slot_id,
                "weapon_unit_id": weapon_unit,
                "weapon_id": strongest.item_id,
                "weapon_hash": f"{strongest.hash_value:08X}",
            }
        )

    final_max_slot = max(used_slots, default=0)
    save.set_first_value(first_record(save, 2801, 0), final_max_slot)
    if main_digest(save) != main_before:
        raise RuntimeError("Main story digest changed before serialization")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save.save_as(output_path, update_hash=True)
    reopened = GBFRSaveData.open(output_path)
    if reopened.check_active_hash() is not True:
        raise RuntimeError("Candidate save failed active-hash validation")
    if main_digest(reopened) != main_before:
        raise RuntimeError("Main story digest changed after serialization")

    final_character_units = find_character_units(reopened, character_by_hash)
    after = audit_state(reopened, entries, final_character_units)
    if after["missing_official_hashes"]:
        raise RuntimeError(f"Official weapons still missing: {after['missing_official_hashes']}")
    if after["official_duplicates"] or after["slot_duplicates"] or after["shell_units"]:
        raise RuntimeError(
            "Candidate weapon audit failed: "
            f"official_duplicates={after['official_duplicates']}, "
            f"slot_duplicates={after['slot_duplicates']}, shells={after['shell_units']}"
        )
    if after["max_slot_id"] != after["actual_max_slot_id"]:
        raise RuntimeError(
            f"2801 does not equal the real maximum instance ID: {after['max_slot_id']} != {after['actual_max_slot_id']}"
        )
    if not all(row["matches"] for row in after["equipped"]):
        raise RuntimeError("At least one playable character 1402 does not resolve to its strongest official weapon")

    output_identities = set(identities(reopened))
    lost_trusted = trusted_identities - output_identities
    if lost_trusted:
        raise RuntimeError(f"Candidate lost trusted baseline/probe weapon identities: {lost_trusted}")

    original_out_of_catalog = sorted(
        (identity for identity in trusted_identities if identity.weapon_hash not in catalog_hashes),
        key=lambda identity: (identity.unit_id, identity.slot_id, identity.weapon_hash),
    )
    audit = {
        "status": "verified_candidate_only",
        "input": str(input_path),
        "output": str(output_path),
        "audit": str(audit_path),
        "original": str(original_path),
        "probe": str(probe_path),
        "database": str(database_path),
        "weapon_catalog": str(weapon_catalog_path),
        "character_catalog": str(character_catalog_path),
        "steam_id": reopened.container.header.get("steam_id"),
        "active_hash_valid": True,
        "main_story": {
            "protected_fields": list(MAIN_FIELDS),
            "digest_before": main_before,
            "digest_after": main_digest(reopened),
            "unchanged": True,
        },
        "source_proof": {
            "original_instance_count": 101,
            "probe_instance_count": 102,
            "forged_addition": {
                "unit_id": probe_added.unit_id,
                "slot_id": probe_added.slot_id,
                "weapon_hash": f"{probe_added.weapon_hash:08X}",
                "complete_fields": {str(key): value for key, value in forged_template.items()},
            },
            "max_template": {"xp": max_xp, "uncap": max_uncap, "plus": max_plus},
        },
        "catalog_proof": {
            "playable_characters": len(character_ids),
            "official_weapons": len(entries),
            "weapons_per_character": 6,
            "all_hashes_match_gbfr_hash": True,
            "all_weapons_match_live_database": True,
            "special_suffix_07_characters": ["PL2100", "PL2200", "PL2300"],
        },
        "changes": {
            "cleared_shell_count": len(cleared_shells),
            "cleared_shells": cleared_shells,
            "added_official_count": len(added),
            "added_instance_range": [added[0]["slot_id"], added[-1]["slot_id"]] if added else None,
            "added_instance_ids_continuous": True,
            "added": added,
            "maxed_official_count": len(official_instances),
            "equipped_character_count": len(equip_changes),
            "equipped": equip_changes,
            "field_2801_before": old_max_slot,
            "field_2801_after": final_max_slot,
        },
        "preservation": {
            "trusted_identity_count": len(trusted_identities),
            "trusted_identities_preserved": True,
            "preexisting_out_of_catalog_count": len(original_out_of_catalog),
            "preexisting_out_of_catalog": [
                {
                    "unit_id": identity.unit_id,
                    "slot_id": identity.slot_id,
                    "weapon_hash": f"{identity.weapon_hash:08X}",
                }
                for identity in original_out_of_catalog
            ],
            "new_out_of_catalog_count": 0,
        },
        "final_audit": after,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    args = parse_args()
    audit = build(args)
    summary = {
        "output": audit["output"],
        "audit": audit["audit"],
        "official_weapons": audit["final_audit"]["official_count"],
        "occupied_instances": audit["final_audit"]["occupied_count"],
        "added_official": audit["changes"]["added_official_count"],
        "equipped_characters": audit["changes"]["equipped_character_count"],
        "max_slot_id": audit["changes"]["field_2801_after"],
        "main_story_unchanged": audit["main_story"]["unchanged"],
        "active_hash_valid": audit["active_hash_valid"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
