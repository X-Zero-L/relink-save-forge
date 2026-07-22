"""Equip all characters with the Relink 2.0.2 integrated 99/99 gold build.

The twelve-sigil portion keeps the six Celestial traits, War Elemental, Alpha,
Beta, Gamma, Critical Hit Rate, Aegis, Greater Aegis, the character's Awakening
and Warpath traits, and exactly one Flight over Fight (Chinese: 摇曳步). Quick
Cooldown, Cascade, Stout Heart, Potion Hoarder, Spartan Echo, and Berserker Echo
are intentionally placed on the equipped weapon/summon surfaces by the
companion verified transforms. Every one of the 24 internal sigil traits
remains unique and level 99.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from build_all_sigils_strict import (
    discover_special_ids,
    id_hash,
    load_live_catalog,
    reference_hash,
    resolve_gem,
)
from equip_legacy_gold_sigils import (
    EXPECTED_CHARACTER_COUNT,
    MAIN_STORY_FIELDS,
    TRAIT_FIELDS,
    changed_records,
    collect_instances,
    equipped_instances,
    first_value,
    full_snapshot,
    load_characters,
    map_character_units,
    protected_story_digest,
    sha256_file,
    trait_unit,
    u32,
    verify_relationships,
)
from save_editor_api import GBFRSaveData, add_editor_argument


TRAIT_LEVEL = 99
OUTER_LEVEL = 15
SIGIL_FLAGS = 3

# One-copy-at-99 sigil portion of the integrated build. The weapon blessing
# carries Quick Cooldown, Cascade, and Stout Heart. The global summon set
# carries Uplift, Potion Hoarder, Spartan Echo, and Berserker Echo. Alpha,
# Beta, Gamma, Aegis, and Greater Aegis remain here at level 99. The manual
# in-game sample proves
# that Flight over Fight (摇曳步) is SKILL_159_00; SKILL_150_00 is the separate
# Untouchable trait (Chinese: 躲避距离).
UNIVERSAL_CORE = (
    ("GEEN_320_24", "SKILL_321_00", "Celestial Nyx / Celestial Lumen"),
    ("GEEN_322_24", "SKILL_323_00", "Celestial Terra / Celestial Incendo"),
    ("GEEN_324_24", "SKILL_325_00", "Celestial Aqua / Fatebreaker"),
    ("GEEN_003_24", "SKILL_085_00", "Critical Hit Rate / Aegis"),
    ("GEEN_166_24", "SKILL_106_00", "Greater Aegis / Nimble Onslaught"),
    ("GEEN_146_24", "SKILL_063_00", "War Elemental / Improved Dodge"),
    ("GEEN_160_04", "SKILL_020_00", "Alpha / Damage Cap"),
    ("GEEN_161_04", "SKILL_151_00", "Beta / Supplementary Damage"),
    ("GEEN_162_04", "SKILL_027_00", "Gamma / Tyranny"),
    ("GEEN_159_24", "SKILL_006_00", "Flight over Fight / Stamina"),
)
FLIGHT_OVER_FIGHT_HASH = reference_hash("SKILL_159_00")
AWAKENING_SECONDARY = "SKILL_045_00"
WARPATH_SECONDARY = "SKILL_068_00"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline source save copy")
    parser.add_argument("output", type=Path, help="Separate offline output save")
    parser.add_argument("--characters", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--database", type=Path, help="Relink 2.0.2 live SQLite database")
    source.add_argument(
        "--preset",
        type=Path,
        help="Bundled database-free sigil preset generated from the live database",
    )
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--expected-build-sha256")
    parser.add_argument("--expected-record-changes", type=int)
    add_editor_argument(parser)
    return parser.parse_args()


def make_entry(outer_id: str, secondary_id: str, label: str, by_key, by_name) -> dict:
    row = resolve_gem(outer_id, by_key, by_name)
    return {
        "outer_id": outer_id,
        "outer_hash": id_hash(outer_id),
        "outer_level": OUTER_LEVEL,
        "flags": SIGIL_FLAGS,
        "primary_ref": row["SkillId1"],
        "primary_hash": reference_hash(row["SkillId1"]),
        "secondary_ref": secondary_id,
        "secondary_hash": reference_hash(secondary_id),
        "can_only_hold_one": bool(row["CanOnlyHoldOne"]),
        "label": label,
    }


def build_character_specs(
    character_ids: list[str],
    by_key,
    by_name,
    by_player,
) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    builds = {}
    metadata = {}
    one_only = defaultdict(list)
    for character_id in character_ids:
        entries = [
            make_entry(outer_id, secondary_id, label, by_key, by_name)
            for outer_id, secondary_id, label in UNIVERSAL_CORE
        ]
        awakening_id, warpath_id, source, avatar_fallback = discover_special_ids(
            character_id,
            by_key,
            by_name,
            by_player,
        )
        entries.append(
            make_entry(
                awakening_id,
                AWAKENING_SECONDARY,
                "Character Awakening / Guts",
                by_key,
                by_name,
            )
        )
        entries.append(
            make_entry(
                warpath_id,
                WARPATH_SECONDARY,
                "Character Warpath / Autorevive",
                by_key,
                by_name,
            )
        )
        if len(entries) != 12:
            raise RuntimeError(f"{character_id} did not resolve to 12 sigils")
        trait_hashes = [
            trait_hash
            for entry in entries
            for trait_hash in (entry["primary_hash"], entry["secondary_hash"])
        ]
        if len(set(trait_hashes)) != 24:
            duplicates = sorted(
                {value for value in trait_hashes if trait_hashes.count(value) > 1}
            )
            raise RuntimeError(
                f"{character_id} latest build repeats trait hashes: "
                f"{[f'{value:08X}' for value in duplicates]}"
            )
        if trait_hashes.count(FLIGHT_OVER_FIGHT_HASH) != 1:
            raise RuntimeError(
                f"{character_id} latest build must contain one Flight over Fight"
            )
        for entry in entries:
            if entry["can_only_hold_one"]:
                one_only[entry["outer_id"]].append(character_id)
        builds[character_id] = entries
        metadata[character_id] = {
            "special_source": source,
            "captain_avatar_one_only_fallback": avatar_fallback,
            "awakening_id": awakening_id,
            "warpath_id": warpath_id,
        }
    duplicate_one_only = {
        outer_id: owners
        for outer_id, owners in one_only.items()
        if len(owners) > 1
    }
    if duplicate_one_only:
        raise RuntimeError(
            f"latest build duplicates CanOnlyHoldOne shells: {duplicate_one_only}"
        )
    return builds, metadata


def build_digest(builds: dict[str, list[dict]]) -> str:
    payload = []
    for character_id in sorted(builds):
        payload.append(
            {
                "character_id": character_id,
                "sigils": [
                    {
                        "outer_id": entry["outer_id"],
                        "outer_hash": entry["outer_hash"],
                        "primary_hash": entry["primary_hash"],
                        "secondary_hash": entry["secondary_hash"],
                        "level": TRAIT_LEVEL,
                    }
                    for entry in builds[character_id]
                ],
            }
        )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest().upper()


def load_preset_specs(
    path: Path,
    catalog_characters: list[dict],
) -> tuple[list[str], dict[str, list[dict]], dict[str, dict], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise RuntimeError("sigil preset schema_version must be 1")
    if (
        payload.get("outer_level") != OUTER_LEVEL
        or payload.get("trait_level") != TRAIT_LEVEL
        or payload.get("flags") != SIGIL_FLAGS
    ):
        raise RuntimeError("sigil preset levels or flags differ from the gold preset contract")
    order = payload.get("character_order")
    rows = payload.get("characters")
    if not isinstance(order, list) or not isinstance(rows, list):
        raise RuntimeError("sigil preset character_order/characters must be arrays")
    if len(order) != EXPECTED_CHARACTER_COUNT or len(rows) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError("sigil preset must contain exactly 29 characters")
    catalog_by_id = {row["id"]: row for row in catalog_characters}
    if set(order) != set(catalog_by_id):
        raise RuntimeError("sigil preset and character catalog playable sets differ")
    row_by_id = {str(row.get("id") or ""): row for row in rows}
    if set(row_by_id) != set(order):
        raise RuntimeError("sigil preset character rows differ from character_order")

    builds = {}
    metadata = {}
    one_only = defaultdict(list)
    for character_id in order:
        row = row_by_id[character_id]
        sigils = row.get("sigils")
        if not isinstance(sigils, list) or len(sigils) != 12:
            raise RuntimeError(f"{character_id} preset must contain 12 sigils")
        entries = []
        for expected_slot, raw in enumerate(sigils, 1):
            if raw.get("slot") != expected_slot:
                raise RuntimeError(f"{character_id} preset slot order is invalid")
            outer_id = str(raw.get("outer_id") or "")
            primary_id = str(raw.get("primary_id") or "")
            secondary_id = str(raw.get("secondary_id") or "")
            outer_hash = id_hash(outer_id)
            primary_hash = reference_hash(primary_id)
            secondary_hash = reference_hash(secondary_id)
            expected_hashes = {
                "outer_hash": f"{outer_hash:08X}",
                "primary_hash": f"{primary_hash:08X}",
                "secondary_hash": f"{secondary_hash:08X}",
            }
            for key, expected in expected_hashes.items():
                if str(raw.get(key) or "").upper() != expected:
                    raise RuntimeError(
                        f"{character_id}/{expected_slot} {key} differs from its ID"
                    )
            entry = {
                "outer_id": outer_id,
                "outer_hash": outer_hash,
                "outer_level": OUTER_LEVEL,
                "flags": SIGIL_FLAGS,
                "primary_ref": primary_id,
                "primary_hash": primary_hash,
                "secondary_ref": secondary_id,
                "secondary_hash": secondary_hash,
                "can_only_hold_one": bool(raw.get("can_only_hold_one")),
                "label": str(raw.get("label") or ""),
            }
            if entry["can_only_hold_one"]:
                one_only[outer_id].append(character_id)
            entries.append(entry)
        trait_hashes = [
            trait_hash
            for entry in entries
            for trait_hash in (entry["primary_hash"], entry["secondary_hash"])
        ]
        if len(set(trait_hashes)) != 24:
            raise RuntimeError(f"{character_id} preset traits are not unique")
        if trait_hashes.count(FLIGHT_OVER_FIGHT_HASH) != 1:
            raise RuntimeError(
                f"{character_id} preset must contain one Flight over Fight"
            )
        builds[character_id] = entries
        metadata[character_id] = {
            "special_source": str(row.get("source_character") or character_id),
            "captain_avatar_one_only_fallback": bool(
                row.get("captain_avatar_one_only_fallback")
            ),
            "awakening_id": entries[10]["outer_id"],
            "warpath_id": entries[11]["outer_id"],
        }
    duplicate_one_only = {
        outer_id: owners for outer_id, owners in one_only.items() if len(owners) > 1
    }
    if duplicate_one_only:
        raise RuntimeError(
            f"sigil preset duplicates CanOnlyHoldOne shells: {duplicate_one_only}"
        )
    digest = build_digest(builds)
    if str(payload.get("build_sha256") or "").upper() != digest:
        raise RuntimeError("sigil preset build_sha256 is invalid")
    return [str(value) for value in order], builds, metadata, payload


def apply_builds(
    save: GBFRSaveData,
    characters: list[dict],
    instances: dict,
    selections: dict[str, list[int]],
    builds: dict[str, list[dict]],
    metadata: dict[str, dict],
) -> list[dict]:
    character_rows = []
    for character in characters:
        character_id = character["id"]
        rows = []
        for slot, (unit, entry) in enumerate(
            zip(selections[character_id], builds[character_id]),
            start=1,
        ):
            instance = instances[unit]
            fields = instance["fields"]
            save.set_first_value(fields[2703], entry["outer_hash"])
            save.set_first_value(fields[2704], entry["outer_level"])
            save.set_first_value(fields[2707], entry["flags"])
            for lane, trait_hash in enumerate(
                (entry["primary_hash"], entry["secondary_hash"])
            ):
                lane_fields = instance["lanes"][lane]
                save.set_first_value(lane_fields[1701], trait_hash)
                save.set_first_value(lane_fields[1702], TRAIT_LEVEL)
            rows.append(
                {
                    "slot": slot,
                    "sigil_unit": unit,
                    "instance_id": first_value(save, fields[2702]),
                    "outer_id": entry["outer_id"],
                    "outer_hash": f"{entry['outer_hash']:08X}",
                    "label": entry["label"],
                    "primary": {
                        "id": entry["primary_ref"],
                        "hash": f"{entry['primary_hash']:08X}",
                        "level": TRAIT_LEVEL,
                    },
                    "secondary": {
                        "id": entry["secondary_ref"],
                        "hash": f"{entry['secondary_hash']:08X}",
                        "level": TRAIT_LEVEL,
                    },
                }
            )
        character_rows.append(
            {
                "character_id": character_id,
                "name": character["name"],
                "unit": character["unit"],
                **metadata[character_id],
                "sigils": rows,
            }
        )
    return character_rows


def verify_builds(
    save: GBFRSaveData,
    characters: list[dict],
    instances: dict,
    selections: dict[str, list[int]],
    builds: dict[str, list[dict]],
) -> None:
    for character in characters:
        character_id = character["id"]
        trait_hashes = []
        for unit, entry in zip(selections[character_id], builds[character_id]):
            instance = instances[unit]
            fields = instance["fields"]
            actual_outer = (
                u32(first_value(save, fields[2703])),
                first_value(save, fields[2704]),
                first_value(save, fields[2707]),
            )
            expected_outer = (entry["outer_hash"], OUTER_LEVEL, SIGIL_FLAGS)
            if actual_outer != expected_outer:
                raise RuntimeError(
                    f"outer verification failed for {character_id}/{unit}"
                )
            for lane, trait_hash in enumerate(
                (entry["primary_hash"], entry["secondary_hash"])
            ):
                lane_fields = instance["lanes"][lane]
                actual_lane = (
                    u32(first_value(save, lane_fields[1701])),
                    first_value(save, lane_fields[1702]),
                )
                if actual_lane != (trait_hash, TRAIT_LEVEL):
                    raise RuntimeError(
                        f"trait verification failed for {character_id}/{unit}/{lane}"
                    )
                trait_hashes.append(actual_lane[0])
        if len(trait_hashes) != 24 or len(set(trait_hashes)) != 24:
            raise RuntimeError(f"{character_id} does not have 24 unique traits")
        if trait_hashes.count(FLIGHT_OVER_FIGHT_HASH) != 1:
            raise RuntimeError(
                f"{character_id} does not have exactly one Flight over Fight"
            )


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    character_path = args.characters.resolve()
    database_path = args.database.resolve() if args.database else None
    preset_path = args.preset.resolve() if args.preset else None
    audit_path = args.audit.resolve()
    required_paths = [
        (input_path, "input save"),
        (character_path, "character catalog"),
    ]
    required_paths.append(
        (database_path, "live database")
        if database_path is not None
        else (preset_path, "sigil preset")
    )
    for path, label in required_paths:
        if path is None:
            raise RuntimeError(f"{label} path was not provided")
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

    catalog_characters = load_characters(character_path)
    catalog_by_id = {row["id"]: row for row in catalog_characters}
    if database_path is not None:
        character_ids, by_key, by_name, by_player = load_live_catalog(database_path)
        if set(character_ids) != set(catalog_by_id):
            raise RuntimeError("database and character catalog playable sets differ")
        builds, metadata = build_character_specs(
            character_ids,
            by_key,
            by_name,
            by_player,
        )
        build_source = {
            "kind": "database",
            "game_data": "Relink 2.0.2 live database",
            "path": str(database_path),
            "sha256": sha256_file(database_path),
        }
    else:
        character_ids, builds, metadata, preset_payload = load_preset_specs(
            preset_path,
            catalog_characters,
        )
        build_source = {
            "kind": "preset",
            "game_data": str(preset_payload.get("game_data_version") or "Relink 2.0.2"),
            "id": str(preset_payload.get("id") or ""),
            "path": str(preset_path),
            "sha256": sha256_file(preset_path),
        }
    ordered_catalog = [catalog_by_id[character_id] for character_id in character_ids]
    characters = map_character_units(save, ordered_catalog)
    mapped_by_id = {row["id"]: row for row in characters}
    characters = [mapped_by_id[character_id] for character_id in character_ids]

    build_sha256 = build_digest(builds)
    if args.expected_build_sha256:
        expected_build = args.expected_build_sha256.strip().upper()
        if build_sha256 != expected_build:
            raise RuntimeError(
                f"build digest {build_sha256} != expected {expected_build}"
            )

    instances, slot_to_unit = collect_instances(save)
    selections, relationship_snapshot = equipped_instances(
        save,
        characters,
        instances,
        slot_to_unit,
    )
    before = full_snapshot(save)
    character_rows = apply_builds(
        save,
        characters,
        instances,
        selections,
        builds,
        metadata,
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
    verify_builds(save, characters, instances, selections, builds)

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

    output_instances, output_slot_to_unit = collect_instances(output)
    output_selections, output_relationships = equipped_instances(
        output,
        characters,
        output_instances,
        output_slot_to_unit,
    )
    if output_selections != selections or output_relationships != relationship_snapshot:
        raise RuntimeError("serialized equipment relationships changed")
    verify_builds(
        output,
        characters,
        output_instances,
        output_selections,
        builds,
    )

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
        "build": {
            "game_data": build_source["game_data"],
            "source": build_source,
            "sha256": build_sha256,
            "trait_level": TRAIT_LEVEL,
            "universal_core": [
                {
                    "outer_id": outer_id,
                    "secondary_id": secondary_id,
                    "label": label,
                }
                for outer_id, secondary_id, label in UNIVERSAL_CORE
            ],
            "character_specific_slots": [
                "Awakening / Guts",
                "Warpath / Autorevive",
            ],
            "traits_per_character": 24,
            "flight_over_fight_per_character": 1,
        },
        "counts": {
            "characters": EXPECTED_CHARACTER_COUNT,
            "equipped_sigils": EXPECTED_CHARACTER_COUNT * 12,
            "equipped_trait_lanes": EXPECTED_CHARACTER_COUNT * 24,
            "record_changes": len(changes),
        },
        "policy": {
            "celestial_2_0_core_included": True,
            "echo_2_0_core_included": True,
            "alpha_beta_gamma_included": True,
            "character_awakening_included": True,
            "character_warpath_included": True,
            "duplicate_level_15_cap_traits_removed": True,
            "all_24_traits_unique_per_character": True,
            "all_trait_levels": TRAIT_LEVEL,
            "one_flight_over_fight_per_character": True,
            "existing_instance_ids_preserved": True,
            "existing_owner_links_preserved": True,
            "existing_1403_loadouts_preserved": True,
            "protected_main_story_fields": list(MAIN_STORY_FIELDS),
        },
        "characters": character_rows,
        "changes": changes,
        "validation": {
            "database_character_count_exact": True,
            "build_digest_exact_if_requested": True,
            "all_29_characters_have_12_unique_instances": True,
            "all_696_trait_lanes_are_level_99": True,
            "all_29_characters_have_24_unique_traits": True,
            "all_29_characters_have_one_flight_over_fight": True,
            "can_only_hold_one_shells_unique": True,
            "all_relationships_unchanged": True,
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
                "characters": EXPECTED_CHARACTER_COUNT,
                "equipped_sigils": EXPECTED_CHARACTER_COUNT * 12,
                "trait_lanes_level_99": EXPECTED_CHARACTER_COUNT * 24,
                "unique_traits_per_character": 24,
                "flight_over_fight_per_character": 1,
                "record_changes": len(changes),
                "build_sha256": build_sha256,
                "active_hash_ok": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
