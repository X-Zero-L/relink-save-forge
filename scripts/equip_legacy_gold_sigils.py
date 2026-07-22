"""Equip every playable character with the save's verified 99/99 sigil set.

The script treats twelve existing, unequipped sigils as a read-only template.
It copies their outer shell, two internal trait hashes, and levels onto each
character's twelve already-equipped instances. Instance IDs, ownership links,
character loadout references, inventory size, and story state are preserved.
"""

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from gbfr_hash import gbfr_hash
from save_editor_api import GBFRSaveData, add_editor_argument


EMPTY_HASH = 0x887AE0B0
EXPECTED_CHARACTER_COUNT = 29
SIGIL_FIELDS = (2702, 2703, 2704, 2706, 2707)
TRAIT_FIELDS = (1701, 1702)
MAIN_STORY_FIELDS = (2510, 2511, 2520, 2522)
TEMPLATE_UNITS = (
    32752,
    32753,
    32755,
    32764,
    32766,
    32768,
    32769,
    32770,
    32771,
    32775,
    32777,
    32778,
)
EXPECTED_TEMPLATE_SHA256 = (
    "20779E8D2165777F6F3E481542E0066EF50DF8837E623AC7A1C30C7152B7215E"
)
FLIGHT_OVER_FIGHT_HASH = int(gbfr_hash("SKILL_159_00")) & 0xFFFFFFFF


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline source save copy")
    parser.add_argument("output", type=Path, help="Separate offline output save")
    parser.add_argument("--characters", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument(
        "--expected-template-sha256",
        default=EXPECTED_TEMPLATE_SHA256,
        help="Exact digest of the twelve verified template specifications",
    )
    parser.add_argument(
        "--expected-record-changes",
        type=int,
        help="Optional exact mutation count for a known input save",
    )
    add_editor_argument(parser)
    return parser.parse_args()


def u32(value: int) -> int:
    return int(value) & 0xFFFFFFFF


def id_hash(value: str) -> int:
    return u32(gbfr_hash(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def first_value(save: GBFRSaveData, record) -> int:
    value = save.get_first_value(record)
    if value is None:
        raise RuntimeError(
            f"record {record.kind}:{record.index} has no readable first value"
        )
    return int(value)


def required_record(
    save: GBFRSaveData,
    field_id: int,
    unit_id: int,
    *,
    kinds: tuple[str, ...],
    value_count: int | None = None,
):
    records = save.find(id_type=field_id, unit_id=unit_id)
    if len(records) != 1:
        raise RuntimeError(
            f"expected one field {field_id}/unit {unit_id}, found {len(records)}"
        )
    record = records[0]
    if record.kind not in kinds:
        raise RuntimeError(
            f"unexpected kind for field {field_id}/unit {unit_id}: {record.kind}"
        )
    if value_count is not None and record.value_count != value_count:
        raise RuntimeError(
            f"unexpected width for field {field_id}/unit {unit_id}: "
            f"{record.value_count}"
        )
    return record


def trait_unit(sigil_unit: int, lane: int) -> int:
    if lane not in (0, 1):
        raise ValueError(f"invalid trait lane {lane}")
    return 120_000_000 + (sigil_unit - 30_000) * 100 + lane


def full_snapshot(save: GBFRSaveData) -> dict[tuple[str, int, int, int], list]:
    return {
        (record.kind, record.index, record.id_type, record.unit_id): list(
            save.get_values(record)
        )
        for record in save.records
    }


def changed_records(before: dict, after: dict) -> list[dict]:
    if set(before) != set(after):
        raise RuntimeError("save record identities changed")
    changes = []
    for key in sorted(before, key=lambda row: (row[2], row[3], row[0], row[1])):
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


def protected_story_digest(save: GBFRSaveData) -> str:
    rows = []
    for field_id in MAIN_STORY_FIELDS:
        for record in save.find(id_type=field_id):
            rows.append((field_id, int(record.unit_id), list(save.get_values(record))))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    encoded = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest().upper()


def load_characters(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("items")
    if not isinstance(rows, list) or len(rows) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError(
            f"character catalog must contain {EXPECTED_CHARACTER_COUNT} rows"
        )
    characters = []
    ids = set()
    hashes = set()
    for row in rows:
        character_id = str(row.get("id") or "")
        character_hash = int(str(row.get("hash") or ""), 16) & 0xFFFFFFFF
        if not character_id or character_id in ids or character_hash in hashes:
            raise RuntimeError("character catalog IDs and hashes must be unique")
        if id_hash(character_id) != character_hash:
            raise RuntimeError(f"catalog hash mismatch for {character_id}")
        ids.add(character_id)
        hashes.add(character_hash)
        characters.append(
            {
                "id": character_id,
                "name": row.get("name"),
                "hash": character_hash,
            }
        )
    return characters


def map_character_units(save: GBFRSaveData, characters: list[dict]) -> list[dict]:
    by_hash = {row["hash"]: row for row in characters}
    mapped = []
    for record in save.find(id_type=1301):
        if record.kind != "uint" or record.value_count != 1:
            continue
        character = by_hash.get(u32(first_value(save, record)))
        if character is None:
            continue
        loadout = required_record(
            save,
            1403,
            int(record.unit_id),
            kinds=("uint",),
        )
        if loadout.value_count < 12:
            raise RuntimeError(
                f"{character['id']} loadout has only {loadout.value_count} slots"
            )
        mapped.append({**character, "unit": int(record.unit_id)})
    if len(mapped) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError(
            f"mapped {len(mapped)} playable characters, expected {EXPECTED_CHARACTER_COUNT}"
        )
    if len({row["id"] for row in mapped}) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError("save contains duplicate playable character hashes")
    mapped.sort(key=lambda row: row["unit"])
    return mapped


def collect_instances(save: GBFRSaveData) -> tuple[dict, dict[int, int]]:
    sigil_groups = save.group_by_unit(SIGIL_FIELDS)
    trait_groups = save.group_by_unit(TRAIT_FIELDS)
    instances = {}
    slot_to_unit = {}
    for unit, fields in sigil_groups.items():
        if any(field not in fields for field in SIGIL_FIELDS):
            raise RuntimeError(f"incomplete sigil instance at unit {unit}")
        slot_id = first_value(save, fields[2702])
        if slot_id:
            if slot_id in slot_to_unit:
                raise RuntimeError(
                    f"duplicate sigil instance ID {slot_id} at units "
                    f"{slot_to_unit[slot_id]} and {unit}"
                )
            slot_to_unit[slot_id] = int(unit)
        lanes = []
        for lane in (0, 1):
            lane_unit = trait_unit(int(unit), lane)
            lane_fields = trait_groups.get(lane_unit, {})
            if any(field not in lane_fields for field in TRAIT_FIELDS):
                raise RuntimeError(
                    f"sigil unit {unit} is missing trait lane {lane}"
                )
            lanes.append(lane_fields)
        instances[int(unit)] = {
            "fields": fields,
            "slot_id": int(slot_id),
            "lanes": lanes,
        }
    return instances, slot_to_unit


def load_outer_catalog(database: Path) -> dict[int, dict]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "gem" not in tables:
            raise RuntimeError("database is missing the gem table")
        rows = connection.execute(
            "SELECT Key, Name, CanOnlyHoldOne FROM gem"
        ).fetchall()
    finally:
        connection.close()
    catalog = defaultdict(list)
    for row in rows:
        candidates = set()
        key = str(row["Key"] or "")
        name = str(row["Name"] or "")
        if key.startswith("GEEN_"):
            candidates.add(key)
        if name.startswith("TXT_GEEN_"):
            candidates.add(name.removeprefix("TXT_"))
        for gbid in candidates:
            catalog[id_hash(gbid)].append(
                {
                    "gbid": gbid,
                    "can_only_hold_one": bool(row["CanOnlyHoldOne"]),
                }
            )
    return dict(catalog)


def template_specifications(
    save: GBFRSaveData,
    instances: dict,
    outer_catalog: dict[int, list[dict]],
) -> tuple[list[dict], str]:
    if len(TEMPLATE_UNITS) != 12 or len(set(TEMPLATE_UNITS)) != 12:
        raise RuntimeError("template unit list must contain 12 unique units")
    specifications = []
    trait_hashes = []
    for unit in TEMPLATE_UNITS:
        instance = instances.get(unit)
        if instance is None:
            raise RuntimeError(f"template sigil unit {unit} is missing")
        fields = instance["fields"]
        owner = u32(first_value(save, fields[2706]))
        if owner not in (0, EMPTY_HASH):
            raise RuntimeError(f"template sigil unit {unit} is currently equipped")
        outer_hash = u32(first_value(save, fields[2703]))
        rows = outer_catalog.get(outer_hash, [])
        if not rows:
            raise RuntimeError(f"template outer hash {outer_hash:08X} is absent from gem DB")
        if any(row["can_only_hold_one"] for row in rows):
            raise RuntimeError(
                f"template outer {outer_hash:08X} is marked CanOnlyHoldOne"
            )
        outer_level = first_value(save, fields[2704])
        flags = first_value(save, fields[2707])
        if outer_level != 15 or flags != 3:
            raise RuntimeError(
                f"template unit {unit} has outer level/flags {outer_level}/{flags}"
            )
        lanes = []
        for lane_fields in instance["lanes"]:
            trait_hash = u32(first_value(save, lane_fields[1701]))
            level = first_value(save, lane_fields[1702])
            if trait_hash in (0, EMPTY_HASH) or level != 99:
                raise RuntimeError(
                    f"template unit {unit} does not contain two valid level-99 traits"
                )
            trait_hashes.append(trait_hash)
            lanes.append({"trait_hash": trait_hash, "level": level})
        specifications.append(
            {
                "template_unit": unit,
                "outer_hash": outer_hash,
                "outer_ids": sorted({row["gbid"] for row in rows}),
                "outer_level": outer_level,
                "flags": flags,
                "lanes": lanes,
            }
        )
    if len(set(trait_hashes)) != 24:
        raise RuntimeError("the legacy-gold template must contain 24 unique traits")
    if trait_hashes.count(FLIGHT_OVER_FIGHT_HASH) != 1:
        raise RuntimeError("the template must contain exactly one Flight over Fight trait")
    digest_rows = [
        {
            "outer_hash": row["outer_hash"],
            "outer_level": row["outer_level"],
            "flags": row["flags"],
            "lanes": row["lanes"],
        }
        for row in specifications
    ]
    encoded = json.dumps(
        digest_rows,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest().upper()
    return specifications, digest


def equipped_instances(
    save: GBFRSaveData,
    characters: list[dict],
    instances: dict,
    slot_to_unit: dict[int, int],
) -> tuple[dict[str, list[int]], dict]:
    selections = {}
    relationship_snapshot = {"loadouts": {}, "instances": {}}
    selected_units = set()
    for character in characters:
        character_id = character["id"]
        character_hash = character["hash"]
        loadout = required_record(
            save,
            1403,
            character["unit"],
            kinds=("uint",),
        )
        loadout_values = list(save.get_values(loadout))
        slot_ids = [int(value) for value in loadout_values[:12]]
        if any(slot_id <= 0 for slot_id in slot_ids) or len(set(slot_ids)) != 12:
            raise RuntimeError(f"{character_id} does not have 12 unique equipped slots")
        units = []
        for slot_id in slot_ids:
            unit = slot_to_unit.get(slot_id)
            if unit is None:
                raise RuntimeError(
                    f"{character_id} loadout references missing instance {slot_id}"
                )
            if unit in selected_units:
                raise RuntimeError(f"sigil unit {unit} is equipped more than once")
            instance = instances[unit]
            owner = u32(first_value(save, instance["fields"][2706]))
            if owner != character_hash:
                raise RuntimeError(
                    f"{character_id} instance {slot_id} has owner {owner:08X}"
                )
            selected_units.add(unit)
            units.append(unit)
            relationship_snapshot["instances"][str(unit)] = {
                "instance_id": first_value(save, instance["fields"][2702]),
                "owner": owner,
            }
        selections[character_id] = units
        relationship_snapshot["loadouts"][character_id] = loadout_values
    if len(selected_units) != EXPECTED_CHARACTER_COUNT * 12:
        raise RuntimeError("equipped sigil units are not globally unique")
    if selected_units & set(TEMPLATE_UNITS):
        raise RuntimeError("template sigils must remain outside equipped loadouts")
    return selections, relationship_snapshot


def apply_template(
    save: GBFRSaveData,
    characters: list[dict],
    instances: dict,
    selections: dict[str, list[int]],
    specifications: list[dict],
) -> list[dict]:
    rows = []
    for character in characters:
        character_rows = []
        for slot, (unit, specification) in enumerate(
            zip(selections[character["id"]], specifications),
            start=1,
        ):
            instance = instances[unit]
            fields = instance["fields"]
            save.set_first_value(fields[2703], specification["outer_hash"])
            save.set_first_value(fields[2704], specification["outer_level"])
            save.set_first_value(fields[2707], specification["flags"])
            for lane, lane_specification in enumerate(specification["lanes"]):
                lane_fields = instance["lanes"][lane]
                save.set_first_value(lane_fields[1701], lane_specification["trait_hash"])
                save.set_first_value(lane_fields[1702], lane_specification["level"])
            character_rows.append(
                {
                    "slot": slot,
                    "sigil_unit": unit,
                    "instance_id": first_value(save, fields[2702]),
                    "outer_hash": f"{specification['outer_hash']:08X}",
                    "outer_ids": specification["outer_ids"],
                    "traits": [
                        {
                            "hash": f"{lane['trait_hash']:08X}",
                            "level": lane["level"],
                        }
                        for lane in specification["lanes"]
                    ],
                }
            )
        rows.append(
            {
                "character_id": character["id"],
                "name": character["name"],
                "unit": character["unit"],
                "sigils": character_rows,
            }
        )
    return rows


def verify_relationships(
    save: GBFRSaveData,
    characters: list[dict],
    instances: dict,
    selections: dict[str, list[int]],
    relationship_snapshot: dict,
) -> None:
    for character in characters:
        character_id = character["id"]
        loadout = required_record(
            save,
            1403,
            character["unit"],
            kinds=("uint",),
        )
        if list(save.get_values(loadout)) != relationship_snapshot["loadouts"][character_id]:
            raise RuntimeError(f"{character_id} loadout relationship changed")
        for unit in selections[character_id]:
            expected = relationship_snapshot["instances"][str(unit)]
            instance = instances[unit]
            actual = {
                "instance_id": first_value(save, instance["fields"][2702]),
                "owner": u32(first_value(save, instance["fields"][2706])),
            }
            if actual != expected:
                raise RuntimeError(f"sigil relationship changed for unit {unit}")


def verify_builds(
    save: GBFRSaveData,
    characters: list[dict],
    instances: dict,
    selections: dict[str, list[int]],
    specifications: list[dict],
) -> None:
    for character in characters:
        trait_hashes = []
        for unit, specification in zip(
            selections[character["id"]], specifications
        ):
            instance = instances[unit]
            fields = instance["fields"]
            actual_outer = (
                u32(first_value(save, fields[2703])),
                first_value(save, fields[2704]),
                first_value(save, fields[2707]),
            )
            expected_outer = (
                specification["outer_hash"],
                specification["outer_level"],
                specification["flags"],
            )
            if actual_outer != expected_outer:
                raise RuntimeError(
                    f"outer sigil verification failed for {character['id']}/{unit}"
                )
            for lane, lane_specification in enumerate(specification["lanes"]):
                lane_fields = instance["lanes"][lane]
                actual_lane = (
                    u32(first_value(save, lane_fields[1701])),
                    first_value(save, lane_fields[1702]),
                )
                expected_lane = (
                    lane_specification["trait_hash"],
                    lane_specification["level"],
                )
                if actual_lane != expected_lane:
                    raise RuntimeError(
                        f"trait verification failed for {character['id']}/{unit}/{lane}"
                    )
                trait_hashes.append(actual_lane[0])
        if len(trait_hashes) != 24 or len(set(trait_hashes)) != 24:
            raise RuntimeError(f"{character['id']} does not have 24 unique traits")
        if trait_hashes.count(FLIGHT_OVER_FIGHT_HASH) != 1:
            raise RuntimeError(
                f"{character['id']} does not have exactly one Flight over Fight trait"
            )


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    character_path = args.characters.resolve()
    database_path = args.database.resolve()
    audit_path = args.audit.resolve()
    for path, label in (
        (input_path, "input save"),
        (character_path, "character catalog"),
        (database_path, "live database"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if len({input_path, output_path, audit_path}) != 3:
        raise RuntimeError("input, output, and audit paths must be distinct")
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("refusing to overwrite an output or audit")

    input_sha256 = sha256_file(input_path)
    save = GBFRSaveData.open(input_path)
    if save.check_active_hash() is not True:
        raise RuntimeError("input save active hash is invalid")
    input_header = dict(save.container.header)
    input_payload_size = save.container.payload_size
    input_record_count = len(save.records)
    story_digest = protected_story_digest(save)
    characters = map_character_units(save, load_characters(character_path))
    instances, slot_to_unit = collect_instances(save)
    specifications, template_sha256 = template_specifications(
        save,
        instances,
        load_outer_catalog(database_path),
    )
    expected_template = args.expected_template_sha256.strip().upper()
    if template_sha256 != expected_template:
        raise RuntimeError(
            f"template digest {template_sha256} != expected {expected_template}"
        )
    selections, relationship_snapshot = equipped_instances(
        save,
        characters,
        instances,
        slot_to_unit,
    )
    template_snapshot = {
        key: value
        for key, value in full_snapshot(save).items()
        if key[3] in set(TEMPLATE_UNITS)
        or key[3]
        in {
            trait_unit(unit, lane)
            for unit in TEMPLATE_UNITS
            for lane in (0, 1)
        }
    }
    before = full_snapshot(save)
    character_rows = apply_template(
        save,
        characters,
        instances,
        selections,
        specifications,
    )
    after = full_snapshot(save)
    changes = changed_records(before, after)

    selected_sigil_units = {
        unit for units in selections.values() for unit in units
    }
    selected_trait_units = {
        trait_unit(unit, lane)
        for unit in selected_sigil_units
        for lane in (0, 1)
    }
    unexpected = []
    for change in changes:
        valid = change["changed_indexes"] == [0]
        if change["unit_id"] in selected_sigil_units:
            valid = valid and change["field_id"] in (2703, 2704, 2707)
        elif change["unit_id"] in selected_trait_units:
            valid = valid and change["field_id"] in TRAIT_FIELDS
        else:
            valid = False
        if not valid:
            unexpected.append(change)
    if unexpected:
        raise RuntimeError(f"unexpected in-memory changes: {unexpected[:3]}")
    if (
        args.expected_record_changes is not None
        and len(changes) != args.expected_record_changes
    ):
        raise RuntimeError(
            f"record changes {len(changes)} != expected {args.expected_record_changes}"
        )
    if protected_story_digest(save) != story_digest:
        raise RuntimeError("protected main-story fields changed in memory")
    verify_relationships(
        save,
        characters,
        instances,
        selections,
        relationship_snapshot,
    )
    verify_builds(save, characters, instances, selections, specifications)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save.save_as(output_path, update_hash=True)
    if sha256_file(input_path) != input_sha256:
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
        raise RuntimeError("serialized records differ from verified in-memory data")
    if protected_story_digest(output) != story_digest:
        raise RuntimeError("protected main-story fields changed on disk")
    output_characters = map_character_units(output, load_characters(character_path))
    output_instances, output_slot_to_unit = collect_instances(output)
    output_selections, output_relationships = equipped_instances(
        output,
        output_characters,
        output_instances,
        output_slot_to_unit,
    )
    if output_selections != selections or output_relationships != relationship_snapshot:
        raise RuntimeError("serialized equipment relationships changed")
    verify_builds(
        output,
        output_characters,
        output_instances,
        output_selections,
        specifications,
    )
    output_snapshot = full_snapshot(output)
    serialized_template_snapshot = {
        key: value
        for key, value in output_snapshot.items()
        if key in template_snapshot
    }
    if serialized_template_snapshot != template_snapshot:
        raise RuntimeError("read-only template instances changed")

    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": {
            "path": str(input_path),
            "sha256": input_sha256,
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
        "template": {
            "units": list(TEMPLATE_UNITS),
            "sha256": template_sha256,
            "sigils": specifications,
            "traits": 24,
            "trait_level": 99,
            "flight_over_fight_hash": f"{FLIGHT_OVER_FIGHT_HASH:08X}",
            "flight_over_fight_count": 1,
        },
        "counts": {
            "characters": len(characters),
            "equipped_sigils": len(characters) * 12,
            "equipped_trait_lanes": len(characters) * 24,
            "record_changes": len(changes),
        },
        "policy": {
            "existing_instance_ids_preserved": True,
            "existing_owner_links_preserved": True,
            "existing_1403_loadouts_preserved": True,
            "template_instances_read_only": True,
            "all_trait_levels": 99,
            "one_flight_over_fight_per_character": True,
            "protected_main_story_fields": list(MAIN_STORY_FIELDS),
        },
        "characters": character_rows,
        "changes": changes,
        "validation": {
            "template_digest_exact": True,
            "template_has_24_unique_traits": True,
            "template_has_one_flight_over_fight": True,
            "template_outers_not_can_only_hold_one": True,
            "all_29_characters_have_12_unique_instances": True,
            "all_696_trait_lanes_are_level_99": True,
            "all_29_characters_have_one_flight_over_fight": True,
            "all_relationships_unchanged": True,
            "template_unchanged": True,
            "main_story_unchanged": True,
            "payload_size_unchanged": True,
            "record_count_unchanged": True,
            "steam_wrapper_unchanged": True,
            "active_hash_ok": True,
        },
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "audit": str(audit_path),
                "characters": len(characters),
                "equipped_sigils": len(characters) * 12,
                "trait_lanes_level_99": len(characters) * 24,
                "flight_over_fight_per_character": 1,
                "record_changes": len(changes),
                "template_sha256": template_sha256,
                "active_hash_ok": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
