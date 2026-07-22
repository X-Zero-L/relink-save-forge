import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from gbfr_hash import gbfr_hash
from save_editor_api import GBFRSaveData, add_editor_argument


EMPTY_HASH = 0x887AE0B0
MAX_LEVEL = 100
MAX_LEVEL_EXP = 8_400_000
SIGIL_LEVEL = 15
SIGIL_FLAGS = 3
MAIN_STORY_FIELDS = (2510, 2511, 2520, 2522)
CHARACTER_FIELDS = (1301, 1303, 1308, 1403)
SIGIL_FIELDS = (2702, 2703, 2704, 2706, 2707)
TRAIT_FIELDS = (1701, 1702)

# This is the exact universal 2.0 core that was confirmed in-game on PL2900.
# Every tuple is (outer sigil ID, inner secondary trait ID). The primary inner
# trait is read from the live gem table instead of being guessed from the shell.
VERIFIED_CORE = (
    ("GEEN_320_24", "SKILL_321_00"),
    ("GEEN_322_24", "SKILL_323_00"),
    ("GEEN_324_24", "SKILL_325_00"),
    ("GEEN_004_24", "SKILL_020_00"),
    ("GEEN_004_24", "SKILL_151_00"),
    ("GEEN_233_24", "SKILL_069_00"),
    ("GEEN_234_24", "SKILL_070_00"),
    ("GEEN_146_24", "SKILL_063_00"),
    ("GEEN_162_04", "SKILL_020_00"),
    ("GEEN_162_04", "SKILL_020_00"),
)
AWAKENING_SECONDARY = "SKILL_073_00"
WARPATH_SECONDARY = "SKILL_068_00"


def u32(value: int) -> int:
    return int(value) & 0xFFFFFFFF


def id_hash(value: str) -> int:
    return u32(gbfr_hash(value))


def reference_hash(value: str) -> int:
    if re.fullmatch(r"[0-9A-Fa-f]{8}", value or ""):
        return int(value, 16)
    if not value:
        raise RuntimeError("Encountered an empty live-table skill reference")
    return id_hash(value)


def trait_unit(sigil_unit: int, lane: int) -> int:
    if lane not in (0, 1):
        raise ValueError(f"Invalid trait lane: {lane}")
    return 120_000_000 + (sigil_unit - 30_000) * 100 + lane


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def protected_digest(save: GBFRSaveData) -> str:
    snapshot = []
    for field_id in MAIN_STORY_FIELDS:
        for record in save.find(id_type=field_id):
            snapshot.append(
                (field_id, int(record.unit_id), list(save.get_values(record)))
            )
    snapshot.sort(key=lambda row: (row[0], row[1], row[2]))
    encoded = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def require_one(save: GBFRSaveData, field_id: int, unit_id: int):
    records = save.find(id_type=field_id, unit_id=unit_id)
    if len(records) != 1:
        raise RuntimeError(
            f"Expected one field {field_id} record for unit {unit_id}, "
            f"found {len(records)}"
        )
    return records[0]


def load_live_catalog(database: Path):
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    characters = connection.execute(
        """
        SELECT CharId, UIOrder
        FROM chara
        WHERE CharId LIKE 'PL%' AND MaxLevelMaybe = 100 AND IsNPC = 0
        ORDER BY UIOrder, CharId
        """
    ).fetchall()
    if len(characters) != 29:
        raise RuntimeError(
            f"The live character table must contain 29 playable level-100 rows, "
            f"found {len(characters)}"
        )

    gem_rows = connection.execute(
        """
        SELECT Key, Name, PlayerReq, SkillId1, SkillId2, Rarity,
               CanOnlyHoldOne
        FROM gem
        """
    ).fetchall()
    connection.close()

    by_key = defaultdict(list)
    by_name = defaultdict(list)
    by_player = defaultdict(list)
    for row in gem_rows:
        by_key[row["Key"]].append(row)
        by_name[row["Name"]].append(row)
        if row["PlayerReq"]:
            by_player[row["PlayerReq"]].append(row)

    return [row["CharId"] for row in characters], by_key, by_name, by_player


def resolve_gem(gem_id: str, by_key, by_name):
    # Older table rows expose the canonical ID in Key. New 2.0 rows may expose
    # a raw unrelated table key, while Name still contains TXT_<canonical ID>.
    # Prefer an exact canonical Key, then use the localization identity.
    rows = by_key.get(gem_id, [])
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        raise RuntimeError(f"Duplicate exact live gem keys for {gem_id}")

    rows = by_name.get(f"TXT_{gem_id}", [])
    expected_hash_key = f"{id_hash(gem_id):08X}"
    canonical_rows = [
        row
        for row in rows
        if (row["Key"] or "").upper() == expected_hash_key
    ]
    if len(canonical_rows) == 1:
        return canonical_rows[0]
    raise RuntimeError(
        f"Expected one canonical live gem row for {gem_id} "
        f"(key {expected_hash_key}), found {len(canonical_rows)} among "
        f"{len(rows)} localization matches"
    )


def discover_special_ids(character_id: str, by_key, by_name, by_player):
    # Gran and Djeeta are two save units but one database character bucket.
    # The database intentionally has only one CanOnlyHoldOne Awakening shell.
    # The second avatar therefore uses the real _91 first-trait V+ shell with
    # the same inner primary trait, avoiding an invalid duplicate one-only item.
    source_character = "PL0000" if character_id == "PL0100" else character_id
    player_rows = by_player.get(source_character, [])
    awakening_ids = sorted(
        row["Name"].removeprefix("TXT_")
        for row in player_rows
        if row["Name"] and row["Name"].endswith("_90")
    )
    warpath_ids = sorted(
        row["Name"].removeprefix("TXT_")
        for row in player_rows
        if row["Name"] and row["Name"].endswith("_93")
    )
    if len(awakening_ids) != 1 or len(warpath_ids) != 1:
        raise RuntimeError(
            f"Expected one Awakening and one Warpath for {source_character}, "
            f"found {awakening_ids} and {warpath_ids}"
        )

    awakening_id = awakening_ids[0]
    avatar_fallback = character_id == "PL0100"
    if avatar_fallback:
        awakening_id = f"{awakening_id[:-2]}91"

    awakening = resolve_gem(awakening_id, by_key, by_name)
    warpath = resolve_gem(warpath_ids[0], by_key, by_name)
    if awakening["PlayerReq"] != source_character:
        raise RuntimeError(f"{awakening_id} has the wrong PlayerReq")
    if warpath["PlayerReq"] != source_character:
        raise RuntimeError(f"{warpath_ids[0]} has the wrong PlayerReq")

    return (
        awakening_id,
        warpath_ids[0],
        source_character,
        avatar_fallback,
    )


def build_spec(character_id: str, by_key, by_name, by_player):
    entries = []
    for outer_id, secondary_id in VERIFIED_CORE:
        row = resolve_gem(outer_id, by_key, by_name)
        entries.append(
            {
                "outer_id": outer_id,
                "outer_hash": id_hash(outer_id),
                "primary_ref": row["SkillId1"],
                "primary_hash": reference_hash(row["SkillId1"]),
                "secondary_ref": secondary_id,
                "secondary_hash": reference_hash(secondary_id),
                "can_only_hold_one": bool(row["CanOnlyHoldOne"]),
            }
        )

    awakening_id, warpath_id, source, avatar_fallback = discover_special_ids(
        character_id, by_key, by_name, by_player
    )
    for outer_id, secondary_id in (
        (awakening_id, AWAKENING_SECONDARY),
        (warpath_id, WARPATH_SECONDARY),
    ):
        row = resolve_gem(outer_id, by_key, by_name)
        entries.append(
            {
                "outer_id": outer_id,
                "outer_hash": id_hash(outer_id),
                "primary_ref": row["SkillId1"],
                "primary_hash": reference_hash(row["SkillId1"]),
                "secondary_ref": secondary_id,
                "secondary_hash": reference_hash(secondary_id),
                "can_only_hold_one": bool(row["CanOnlyHoldOne"]),
            }
        )

    if len(entries) != 12:
        raise RuntimeError(f"{character_id} did not resolve to a 12-sigil build")
    return entries, source, avatar_fallback


def character_units(save: GBFRSaveData, character_ids: list[str]):
    units_by_hash = defaultdict(list)
    for record in save.find(id_type=1301):
        value = u32(save.get_first_value(record, 0))
        units_by_hash[value].append(int(record.unit_id))

    result = {}
    used_units = set()
    for character_id in character_ids:
        character_hash = id_hash(character_id)
        units = units_by_hash.get(character_hash, [])
        if len(units) != 1:
            raise RuntimeError(
                f"Expected one real save unit for {character_id} "
                f"(0x{character_hash:08X}), found {units}"
            )
        unit_id = units[0]
        if unit_id in used_units:
            raise RuntimeError(f"Character unit {unit_id} was mapped twice")
        used_units.add(unit_id)
        for field_id in CHARACTER_FIELDS:
            require_one(save, field_id, unit_id)
        result[character_id] = unit_id
    return result


def collect_instances(save: GBFRSaveData):
    groups = save.group_by_unit(SIGIL_FIELDS)
    traits = save.group_by_unit(TRAIT_FIELDS)
    instances = {}
    nonzero_slots = {}

    for unit_id, fields in groups.items():
        if any(field_id not in fields for field_id in SIGIL_FIELDS):
            raise RuntimeError(f"Incomplete sigil instance at unit {unit_id}")
        slot_id = int(save.get_first_value(fields[2702], 0))
        if slot_id:
            if slot_id in nonzero_slots:
                raise RuntimeError(
                    f"Duplicate nonzero 2702 instance ID {slot_id} at units "
                    f"{nonzero_slots[slot_id]} and {unit_id}"
                )
            nonzero_slots[slot_id] = unit_id

        lane_records = []
        for lane in (0, 1):
            lane_unit = trait_unit(unit_id, lane)
            lane_fields = traits.get(lane_unit, {})
            if any(field_id not in lane_fields for field_id in TRAIT_FIELDS):
                raise RuntimeError(
                    f"Sigil unit {unit_id} is missing complete trait lane {lane}"
                )
            lane_records.append(lane_fields)

        instances[unit_id] = {
            "fields": fields,
            "slot_id": slot_id,
            "owner": u32(save.get_first_value(fields[2706], EMPTY_HASH)),
            "lanes": lane_records,
        }

    return instances, nonzero_slots


def select_instances(
    save: GBFRSaveData,
    character_ids: list[str],
    units: dict[str, int],
    instances,
):
    playable_hashes = {id_hash(character_id) for character_id in character_ids}
    usable_units = {
        unit_id
        for unit_id, instance in instances.items()
        if instance["slot_id"]
        and instance["owner"] in ({0, EMPTY_HASH} | playable_hashes)
    }
    selections = {}
    selected_units = set()

    # First reserve each character's existing complete instances. PL2900 keeps
    # the exact in-game-verified loadout order; reruns are idempotent for all.
    for character_id in character_ids:
        character_hash = id_hash(character_id)
        owned = {
            unit_id
            for unit_id in usable_units
            if instances[unit_id]["owner"] == character_hash
        }
        preferred = []
        loadout = require_one(save, 1403, units[character_id])
        loadout_slots = list(save.get_values(loadout))[:12]
        slot_to_unit = {
            instances[unit_id]["slot_id"]: unit_id for unit_id in owned
        }
        for slot_id in loadout_slots:
            unit_id = slot_to_unit.get(int(slot_id))
            if unit_id is not None and unit_id not in preferred:
                preferred.append(unit_id)
        preferred.extend(sorted(owned - set(preferred)))
        selections[character_id] = preferred[:12]
        selected_units.update(selections[character_id])

    available = sorted(
        usable_units - selected_units,
        key=lambda unit_id: (
            instances[unit_id]["owner"] not in (0, EMPTY_HASH),
            unit_id,
        ),
    )
    available_index = 0
    for character_id in character_ids:
        needed = 12 - len(selections[character_id])
        if available_index + needed > len(available):
            raise RuntimeError(
                f"Not enough reusable complete sigil instances for {character_id}"
            )
        additions = available[available_index : available_index + needed]
        available_index += needed
        selections[character_id].extend(additions)
        selected_units.update(additions)

    if len(selected_units) != len(character_ids) * 12:
        raise RuntimeError("Selected sigil units are not globally unique")
    return selections, playable_hashes


def patch_save(
    save: GBFRSaveData,
    character_ids: list[str],
    units: dict[str, int],
    instances,
    selections,
    builds,
    playable_hashes: set[int],
):
    # Remove every stale playable-character owner link before rebuilding the
    # exact 29 x 12 bidirectional relationship.
    for instance in instances.values():
        if instance["owner"] in playable_hashes:
            save.set_first_value(instance["fields"][2706], EMPTY_HASH)

    character_audit = []
    for character_id in character_ids:
        character_hash = id_hash(character_id)
        character_unit = units[character_id]
        save.set_first_value(
            require_one(save, 1308, character_unit), MAX_LEVEL
        )
        save.set_first_value(
            require_one(save, 1303, character_unit), MAX_LEVEL_EXP
        )

        slot_ids = []
        sigil_audit = []
        for slot_number, (unit_id, spec) in enumerate(
            zip(selections[character_id], builds[character_id]), start=1
        ):
            instance = instances[unit_id]
            fields = instance["fields"]
            slot_id = instance["slot_id"]
            save.set_first_value(fields[2702], slot_id)
            save.set_first_value(fields[2703], spec["outer_hash"])
            save.set_first_value(fields[2704], SIGIL_LEVEL)
            save.set_first_value(fields[2706], character_hash)
            save.set_first_value(fields[2707], SIGIL_FLAGS)
            for lane, trait_hash in enumerate(
                (spec["primary_hash"], spec["secondary_hash"])
            ):
                lane_fields = instance["lanes"][lane]
                save.set_first_value(lane_fields[1701], trait_hash)
                save.set_first_value(lane_fields[1702], SIGIL_LEVEL)

            slot_ids.append(slot_id)
            sigil_audit.append(
                {
                    "slot": slot_number,
                    "unit": unit_id,
                    "instance_id": slot_id,
                    "outer_id": spec["outer_id"],
                    "outer_hash": f"0x{spec['outer_hash']:08X}",
                    "primary": spec["primary_ref"],
                    "secondary": spec["secondary_ref"],
                }
            )

        loadout = require_one(save, 1403, character_unit)
        loadout_values = list(save.get_values(loadout))
        if len(loadout_values) < 12:
            raise RuntimeError(
                f"Character {character_id} loadout has only "
                f"{len(loadout_values)} slots"
            )
        loadout_values[:12] = slot_ids
        save.set_values(loadout, loadout_values)
        character_audit.append(
            {
                "character_id": character_id,
                "character_hash": f"0x{character_hash:08X}",
                "unit": character_unit,
                "level": MAX_LEVEL,
                "experience": MAX_LEVEL_EXP,
                "sigils": sigil_audit,
            }
        )

    return character_audit


def verify_output(
    output: Path,
    character_ids: list[str],
    units: dict[str, int],
    selections,
    builds,
    expected_story_digest: str,
):
    save = GBFRSaveData.open(output)
    if save.check_active_hash() is not True:
        raise RuntimeError("Output active hash validation failed")
    if protected_digest(save) != expected_story_digest:
        raise RuntimeError("Protected main-story fields changed")

    instances, nonzero_slots = collect_instances(save)
    selected_slot_ids = []
    one_only_selected = defaultdict(list)
    playable_hashes = {id_hash(character_id) for character_id in character_ids}

    for character_id in character_ids:
        character_hash = id_hash(character_id)
        character_unit = units[character_id]
        level = int(
            save.get_first_value(require_one(save, 1308, character_unit), 0)
        )
        experience = int(
            save.get_first_value(require_one(save, 1303, character_unit), 0)
        )
        if level != MAX_LEVEL or experience != MAX_LEVEL_EXP:
            raise RuntimeError(f"Character growth verification failed for {character_id}")

        expected_slots = [
            instances[unit_id]["slot_id"] for unit_id in selections[character_id]
        ]
        loadout_slots = list(
            save.get_values(require_one(save, 1403, character_unit))
        )[:12]
        if loadout_slots != expected_slots:
            raise RuntimeError(f"1403 loadout verification failed for {character_id}")

        owner_count = 0
        for instance in instances.values():
            if u32(save.get_first_value(instance["fields"][2706], EMPTY_HASH)) == character_hash:
                owner_count += 1
        if owner_count != 12:
            raise RuntimeError(
                f"Expected 12 owner links for {character_id}, found {owner_count}"
            )

        for unit_id, spec in zip(selections[character_id], builds[character_id]):
            instance = instances[unit_id]
            fields = instance["fields"]
            actual = (
                int(save.get_first_value(fields[2702], 0)),
                u32(save.get_first_value(fields[2703], 0)),
                int(save.get_first_value(fields[2704], 0)),
                u32(save.get_first_value(fields[2706], EMPTY_HASH)),
                int(save.get_first_value(fields[2707], 0)),
            )
            expected = (
                instance["slot_id"],
                spec["outer_hash"],
                SIGIL_LEVEL,
                character_hash,
                SIGIL_FLAGS,
            )
            if actual != expected:
                raise RuntimeError(
                    f"Sigil record verification failed for {character_id}, unit {unit_id}"
                )
            for lane, trait_hash in enumerate(
                (spec["primary_hash"], spec["secondary_hash"])
            ):
                lane_fields = instance["lanes"][lane]
                actual_trait = (
                    u32(save.get_first_value(lane_fields[1701], 0)),
                    int(save.get_first_value(lane_fields[1702], 0)),
                )
                if actual_trait != (trait_hash, SIGIL_LEVEL):
                    raise RuntimeError(
                        f"Trait lane {lane} verification failed for "
                        f"{character_id}, unit {unit_id}"
                    )
            selected_slot_ids.append(instance["slot_id"])
            if spec["can_only_hold_one"]:
                one_only_selected[spec["outer_id"]].append(character_id)

    if len(selected_slot_ids) != len(set(selected_slot_ids)):
        raise RuntimeError("Selected 2702 instance IDs are not unique")
    duplicate_one_only = {
        outer_id: owners
        for outer_id, owners in one_only_selected.items()
        if len(owners) > 1
    }
    if duplicate_one_only:
        raise RuntimeError(
            f"Selected duplicate CanOnlyHoldOne sigils: {duplicate_one_only}"
        )

    stray_owner_counts = defaultdict(int)
    for instance in instances.values():
        owner = u32(save.get_first_value(instance["fields"][2706], EMPTY_HASH))
        if owner in playable_hashes:
            stray_owner_counts[owner] += 1
    if any(count != 12 for count in stray_owner_counts.values()):
        raise RuntimeError("Playable owner-link count audit failed")

    return {
        "active_hash_valid": True,
        "main_story_digest": expected_story_digest,
        "global_nonzero_instance_ids": len(nonzero_slots),
        "global_nonzero_instance_ids_unique": True,
        "selected_instance_ids": len(selected_slot_ids),
        "selected_instance_ids_unique": True,
        "selected_can_only_hold_one_duplicates": {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build strict Relink 2.0 full internal sigil loadouts"
    )
    parser.add_argument("save", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--audit-json", type=Path)
    add_editor_argument(parser)
    args = parser.parse_args()

    source = args.save.resolve()
    output = args.output.resolve()
    database = args.db.resolve()
    if source == output:
        raise RuntimeError("Refusing to overwrite the input save in place")
    if not source.is_file():
        raise RuntimeError(f"Input save does not exist: {source}")
    if not database.is_file():
        raise RuntimeError(f"Live database does not exist: {database}")
    output.parent.mkdir(parents=True, exist_ok=True)

    character_ids, by_key, by_name, by_player = load_live_catalog(database)
    save = GBFRSaveData.open(source)
    if save.check_active_hash() is not True:
        raise RuntimeError("Input save active hash validation failed")
    story_digest = protected_digest(save)
    units = character_units(save, character_ids)
    instances, nonzero_slots = collect_instances(save)
    selections, playable_hashes = select_instances(
        save, character_ids, units, instances
    )

    builds = {}
    build_metadata = {}
    for character_id in character_ids:
        build, special_source, avatar_fallback = build_spec(
            character_id, by_key, by_name, by_player
        )
        builds[character_id] = build
        build_metadata[character_id] = {
            "special_source": special_source,
            "captain_avatar_one_only_fallback": avatar_fallback,
        }

    character_audit = patch_save(
        save,
        character_ids,
        units,
        instances,
        selections,
        builds,
        playable_hashes,
    )
    save.save_as(output, update_hash=True)
    verification = verify_output(
        output,
        character_ids,
        units,
        selections,
        builds,
        story_digest,
    )

    for row in character_audit:
        row.update(build_metadata[row["character_id"]])
    audit = {
        "source": str(source),
        "source_sha256": file_sha256(source),
        "output": str(output),
        "output_sha256": file_sha256(output),
        "live_database": str(database),
        "characters": len(character_ids),
        "equipped_sigils": len(character_ids) * 12,
        "baseline_complete_sigil_instances": len(instances),
        "baseline_nonzero_instance_ids": len(nonzero_slots),
        "protected_main_story_fields": list(MAIN_STORY_FIELDS),
        "verification": verification,
        "character_builds": character_audit,
    }
    encoded = json.dumps(audit, indent=2, ensure_ascii=False)
    if args.audit_json:
        audit_path = args.audit_json.resolve()
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
