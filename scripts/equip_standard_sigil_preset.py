"""Equip a database-row-backed Relink 2.0.2 level-15 sigil preset."""

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from build_all_sigils_strict import id_hash, reference_hash
from equip_legacy_gold_sigils import (
    EMPTY_HASH,
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


GAME_DATA_VERSION = "Relink 2.0.2"
OUTER_LEVEL = 15
LANE_LEVEL_MAX = 15
SIGIL_FLAGS = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline source save copy")
    parser.add_argument("output", type=Path, help="Separate offline output save")
    parser.add_argument("--characters", type=Path, required=True)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--pair-catalog", type=Path, required=True)
    parser.add_argument("--cap-catalog", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--expected-build-sha256")
    parser.add_argument("--expected-preset-sha256")
    parser.add_argument("--expected-record-changes", type=int)
    add_editor_argument(parser)
    return parser.parse_args()


def load_json_object(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object")
    return payload


def runtime_hash(database_key: str) -> int:
    if re.fullmatch(r"[0-9A-Fa-f]{8}", database_key):
        return int(database_key, 16)
    return id_hash(database_key)


def load_pair_catalog(path: Path) -> tuple[dict, dict[str, dict]]:
    payload = load_json_object(path, "sigil legal-pair catalog")
    rows = payload.get("items")
    if (
        payload.get("schema_version") != 1
        or payload.get("game_data_version") != GAME_DATA_VERSION
        or not isinstance(rows, list)
        or payload.get("count") != len(rows)
    ):
        raise RuntimeError("sigil legal-pair catalog metadata is invalid")

    by_key: dict[str, dict] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise RuntimeError("sigil legal-pair catalog row must be an object")
        database_key = str(raw.get("database_key") or "")
        outer_id = str(raw.get("outer_id") or "")
        if not database_key or database_key in by_key or not outer_id:
            raise RuntimeError(f"invalid or duplicate legal-pair key {database_key!r}")
        expected_outer = f"{runtime_hash(database_key):08X}"
        expected_canonical = f"{id_hash(outer_id):08X}"
        if str(raw.get("outer_hash") or "").upper() != expected_outer:
            raise RuntimeError(f"{database_key} runtime outer hash differs")
        if str(raw.get("canonical_outer_hash") or "").upper() != expected_canonical:
            raise RuntimeError(f"{database_key} canonical outer hash differs")
        if raw.get("rarity") != 5 or raw.get("pairing_source") != "gem_database_row":
            raise RuntimeError(f"{database_key} is not a verified rarity-5 database row")

        primary = raw.get("primary")
        secondary = raw.get("secondary")
        if not isinstance(primary, dict):
            raise RuntimeError(f"{database_key} primary lane is missing")
        primary_id = str(primary.get("id") or "")
        if str(primary.get("hash") or "").upper() != f"{reference_hash(primary_id):08X}":
            raise RuntimeError(f"{database_key} primary hash differs from its ID")
        if secondary is not None:
            if not isinstance(secondary, dict):
                raise RuntimeError(f"{database_key} secondary lane is invalid")
            secondary_id = str(secondary.get("id") or "")
            if str(secondary.get("hash") or "").upper() != (
                f"{reference_hash(secondary_id):08X}"
            ):
                raise RuntimeError(f"{database_key} secondary hash differs from its ID")
        by_key[database_key] = raw
    return payload, by_key


def load_cap_catalog(path: Path) -> tuple[dict, dict[int, dict]]:
    payload = load_json_object(path, "skill-level cap catalog")
    rows = payload.get("items")
    if (
        payload.get("schema_version") != 1
        or payload.get("game_data_version") != GAME_DATA_VERSION
        or payload.get("lane_max_level") != LANE_LEVEL_MAX
        or not isinstance(rows, list)
        or payload.get("count") != len(rows)
    ):
        raise RuntimeError("skill-level cap catalog metadata is invalid")

    by_hash: dict[int, dict] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise RuntimeError("skill-level cap row must be an object")
        skill_id = str(raw.get("skill_id") or "")
        skill_hash = reference_hash(skill_id)
        if str(raw.get("skill_hash") or "").upper() != f"{skill_hash:08X}":
            raise RuntimeError(f"skill cap hash differs for {skill_id}")
        if skill_hash in by_hash:
            raise RuntimeError(f"duplicate skill cap hash {skill_hash:08X}")
        minimum = raw.get("curve_min_level")
        maximum = raw.get("max_total_level")
        count = raw.get("curve_row_count")
        if (
            minimum != 1
            or not isinstance(maximum, int)
            or maximum < LANE_LEVEL_MAX
            or maximum > 99
            or count != maximum
        ):
            raise RuntimeError(f"skill cap curve is invalid for {skill_id}")
        by_hash[skill_hash] = raw
    return payload, by_hash


def build_digest(builds: dict[str, list[dict]]) -> str:
    payload = []
    for character_id in sorted(builds):
        payload.append(
            {
                "character_id": character_id,
                "sigils": [
                    {
                        "slot": entry["slot"],
                        "role": entry["role"],
                        "database_key": entry["database_key"],
                        "outer_hash": entry["outer_hash"],
                        "primary": {
                            "hash": entry["primary"]["hash"],
                            "level": entry["primary"]["level"],
                        },
                        "secondary": (
                            None
                            if entry["secondary"] is None
                            else {
                                "hash": entry["secondary"]["hash"],
                                "level": entry["secondary"]["level"],
                            }
                        ),
                    }
                    for entry in builds[character_id]
                ],
            }
        )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest().upper()


def validate_aggregate_levels(
    entries: list[dict],
    caps_by_hash: dict[int, dict],
    character_id: str,
) -> list[dict]:
    totals: dict[int, int] = defaultdict(int)
    ids: dict[int, str] = {}
    for entry in entries:
        for lane in (entry["primary"], entry["secondary"]):
            if lane is None:
                continue
            level = lane["level"]
            if not isinstance(level, int) or not 1 <= level <= LANE_LEVEL_MAX:
                raise RuntimeError(
                    f"{character_id}/{entry['slot']} lane level must be 1..15"
                )
            if level == 99:
                raise RuntimeError(f"{character_id} standard preset contains level 99")
            cap = caps_by_hash.get(lane["hash"])
            if cap is None:
                raise RuntimeError(
                    f"{character_id} has no cap row for {lane['id']}/{lane['hash']:08X}"
                )
            if lane["max_total_level"] != cap["max_total_level"]:
                raise RuntimeError(
                    f"{character_id} preset cap differs for {lane['id']}"
                )
            totals[lane["hash"]] += level
            ids[lane["hash"]] = lane["id"]

    report = []
    for skill_hash, total in sorted(totals.items()):
        maximum = caps_by_hash[skill_hash]["max_total_level"]
        if total > maximum:
            raise RuntimeError(
                f"{character_id} {ids[skill_hash]} total {total} exceeds cap {maximum}"
            )
        report.append(
            {
                "id": ids[skill_hash],
                "hash": f"{skill_hash:08X}",
                "total_level": total,
                "max_total_level": maximum,
            }
        )
    return report


def _resolve_lane(raw: dict | None, pair_lane: dict | None, cap_by_hash: dict) -> dict | None:
    if raw is None or pair_lane is None:
        if raw is not None or pair_lane is not None:
            raise RuntimeError("preset and database row disagree on an empty secondary lane")
        return None
    if not isinstance(raw, dict):
        raise RuntimeError("preset lane must be an object")
    skill_id = str(raw.get("id") or "")
    skill_hash = reference_hash(skill_id)
    if skill_id != pair_lane.get("id"):
        raise RuntimeError(f"preset lane {skill_id} differs from database row")
    if str(raw.get("hash") or "").upper() != f"{skill_hash:08X}":
        raise RuntimeError(f"preset lane hash differs for {skill_id}")
    level = raw.get("level")
    if level != LANE_LEVEL_MAX:
        raise RuntimeError(f"standard preset lane {skill_id} must be level 15")
    cap = cap_by_hash.get(skill_hash)
    if cap is None or raw.get("max_total_level") != cap["max_total_level"]:
        raise RuntimeError(f"standard preset cap differs for {skill_id}")
    return {
        "id": skill_id,
        "hash": skill_hash,
        "level": level,
        "max_total_level": cap["max_total_level"],
    }


def load_standard_preset(
    preset_path: Path,
    pair_catalog_path: Path,
    cap_catalog_path: Path,
    character_catalog_path: Path,
) -> tuple[list[str], dict[str, list[dict]], dict[str, dict], dict, dict]:
    pair_payload, pairs = load_pair_catalog(pair_catalog_path)
    cap_payload, caps_by_hash = load_cap_catalog(cap_catalog_path)
    payload = load_json_object(preset_path, "standard sigil preset")
    if (
        payload.get("schema_version") != 2
        or payload.get("game_data_version") != GAME_DATA_VERSION
        or payload.get("legality_mode") != "database_rows_only"
        or payload.get("outer_level") != OUTER_LEVEL
        or payload.get("lane_level_max") != LANE_LEVEL_MAX
        or payload.get("flags") != SIGIL_FLAGS
        or str(payload.get("empty_trait_hash") or "").upper() != f"{EMPTY_HASH:08X}"
    ):
        raise RuntimeError("standard sigil preset metadata is invalid")

    catalog_references = (
        ("pair_catalog", pair_catalog_path),
        ("skill_cap_catalog", cap_catalog_path),
    )
    for key, path in catalog_references:
        reference = payload.get(key)
        if not isinstance(reference, dict):
            raise RuntimeError(f"standard preset {key} reference is missing")
        if Path(str(reference.get("file") or "")).name != path.name:
            raise RuntimeError(f"standard preset {key} filename differs")
        if str(reference.get("sha256") or "").upper() != sha256_file(path):
            raise RuntimeError(f"standard preset {key} SHA-256 differs")

    characters = load_characters(character_catalog_path)
    catalog_by_id = {row["id"]: row for row in characters}
    order = payload.get("character_order")
    rows = payload.get("characters")
    slot_roles = payload.get("slot_roles")
    if (
        not isinstance(order, list)
        or not isinstance(rows, list)
        or not isinstance(slot_roles, list)
        or len(order) != EXPECTED_CHARACTER_COUNT
        or len(rows) != EXPECTED_CHARACTER_COUNT
        or len(slot_roles) != 12
        or len(set(slot_roles)) != 12
        or set(order) != set(catalog_by_id)
    ):
        raise RuntimeError("standard preset character or slot-role layout is invalid")
    row_by_id = {str(row.get("id") or ""): row for row in rows if isinstance(row, dict)}
    if set(row_by_id) != set(order):
        raise RuntimeError("standard preset character rows differ from character_order")

    builds: dict[str, list[dict]] = {}
    metadata: dict[str, dict] = {}
    one_only: dict[str, list[str]] = defaultdict(list)
    aggregate_report: dict[str, list[dict]] = {}
    for character_id in order:
        row = row_by_id[character_id]
        source_character = str(row.get("source_character") or "")
        fallback = bool(row.get("captain_avatar_one_only_fallback"))
        expected_source = "PL0000" if character_id == "PL0100" else character_id
        if source_character != expected_source or fallback != (character_id == "PL0100"):
            raise RuntimeError(f"{character_id} captain/source metadata is invalid")
        sigils = row.get("sigils")
        if not isinstance(sigils, list) or len(sigils) != 12:
            raise RuntimeError(f"{character_id} standard preset must contain 12 sigils")

        entries = []
        for slot, raw in enumerate(sigils, 1):
            if not isinstance(raw, dict):
                raise RuntimeError(f"{character_id}/{slot} sigil row is invalid")
            role = str(raw.get("role") or "")
            if raw.get("slot") != slot or role != slot_roles[slot - 1]:
                raise RuntimeError(f"{character_id}/{slot} slot role is invalid")
            database_key = str(raw.get("database_key") or "")
            pair = pairs.get(database_key)
            if pair is None:
                raise RuntimeError(f"{character_id}/{slot} uses unknown pair {database_key}")
            player_requirement = str(pair.get("player_requirement") or "")
            if player_requirement and player_requirement != source_character:
                raise RuntimeError(
                    f"{character_id}/{slot} pair belongs to {player_requirement}"
                )
            expected_fields = {
                "outer_id": pair["outer_id"],
                "outer_hash": str(pair["outer_hash"]).upper(),
                "canonical_outer_hash": str(pair["canonical_outer_hash"]).upper(),
                "can_only_hold_one": bool(pair["can_only_hold_one"]),
            }
            for key, expected in expected_fields.items():
                actual = raw.get(key)
                if isinstance(expected, str):
                    actual = str(actual or "").upper() if key.endswith("hash") else str(actual or "")
                if actual != expected:
                    raise RuntimeError(
                        f"{character_id}/{slot} {key} differs from database row"
                    )
            primary = _resolve_lane(raw.get("primary"), pair["primary"], caps_by_hash)
            secondary = _resolve_lane(raw.get("secondary"), pair.get("secondary"), caps_by_hash)
            entry = {
                "slot": slot,
                "role": role,
                "label": str(raw.get("label") or ""),
                "database_key": database_key,
                "outer_id": pair["outer_id"],
                "outer_hash": int(pair["outer_hash"], 16),
                "canonical_outer_hash": int(pair["canonical_outer_hash"], 16),
                "outer_level": OUTER_LEVEL,
                "flags": SIGIL_FLAGS,
                "can_only_hold_one": bool(pair["can_only_hold_one"]),
                "primary": primary,
                "secondary": secondary,
            }
            if entry["can_only_hold_one"]:
                one_only[database_key].append(character_id)
            entries.append(entry)

        awakening = next(entry for entry in entries if entry["role"] == "awakening")
        warpath = next(entry for entry in entries if entry["role"] == "warpath")
        if character_id == "PL0100":
            if (
                awakening["database_key"] != "GEEN_114_91"
                or awakening["secondary"] is not None
                or awakening["can_only_hold_one"]
            ):
                raise RuntimeError("Djeeta must use the real single-lane _91 fallback")
        elif not awakening["database_key"].endswith("_90") or not awakening["can_only_hold_one"]:
            raise RuntimeError(f"{character_id} Awakening row is invalid")
        if not warpath["database_key"].endswith("_93") or warpath["secondary"] is not None:
            raise RuntimeError(f"{character_id} Warpath must keep its empty database lane")

        builds[character_id] = entries
        aggregate_report[character_id] = validate_aggregate_levels(
            entries,
            caps_by_hash,
            character_id,
        )
        metadata[character_id] = {
            "source_character": source_character,
            "captain_avatar_one_only_fallback": fallback,
            "aggregate_levels": aggregate_report[character_id],
        }

    duplicate_one_only = {
        key: owners for key, owners in one_only.items() if len(owners) > 1
    }
    if duplicate_one_only:
        raise RuntimeError(
            f"standard preset repeats CanOnlyHoldOne rows: {duplicate_one_only}"
        )
    digest = build_digest(builds)
    if str(payload.get("build_sha256") or "").upper() != digest:
        raise RuntimeError("standard preset build_sha256 is invalid")
    source = {
        "pair_catalog": pair_payload,
        "cap_catalog": cap_payload,
        "aggregate_levels": aggregate_report,
    }
    return [str(value) for value in order], builds, metadata, payload, source


def apply_builds(
    save: GBFRSaveData,
    characters: list[dict],
    instances: dict,
    selections: dict[str, list[int]],
    builds: dict[str, list[dict]],
    metadata: dict[str, dict],
) -> list[dict]:
    rows = []
    for character in characters:
        character_id = character["id"]
        sigil_rows = []
        for unit, entry in zip(selections[character_id], builds[character_id]):
            instance = instances[unit]
            fields = instance["fields"]
            save.set_first_value(fields[2703], entry["outer_hash"])
            save.set_first_value(fields[2704], OUTER_LEVEL)
            save.set_first_value(fields[2707], SIGIL_FLAGS)
            lane_rows = []
            for lane_index, lane in enumerate((entry["primary"], entry["secondary"])):
                lane_fields = instance["lanes"][lane_index]
                trait_hash = EMPTY_HASH if lane is None else lane["hash"]
                level = 0 if lane is None else lane["level"]
                save.set_first_value(lane_fields[1701], trait_hash)
                save.set_first_value(lane_fields[1702], level)
                lane_rows.append(
                    None
                    if lane is None
                    else {
                        "id": lane["id"],
                        "hash": f"{lane['hash']:08X}",
                        "level": level,
                        "max_total_level": lane["max_total_level"],
                    }
                )
            sigil_rows.append(
                {
                    "slot": entry["slot"],
                    "role": entry["role"],
                    "label": entry["label"],
                    "sigil_unit": unit,
                    "instance_id": first_value(save, fields[2702]),
                    "database_key": entry["database_key"],
                    "outer_id": entry["outer_id"],
                    "outer_hash": f"{entry['outer_hash']:08X}",
                    "primary": lane_rows[0],
                    "secondary": lane_rows[1],
                }
            )
        rows.append(
            {
                "character_id": character_id,
                "name": character["name"],
                "unit": character["unit"],
                **metadata[character_id],
                "sigils": sigil_rows,
            }
        )
    return rows


def verify_builds(
    save: GBFRSaveData,
    characters: list[dict],
    instances: dict,
    selections: dict[str, list[int]],
    builds: dict[str, list[dict]],
    caps_by_hash: dict[int, dict],
) -> None:
    for character in characters:
        character_id = character["id"]
        for unit, entry in zip(selections[character_id], builds[character_id]):
            instance = instances[unit]
            fields = instance["fields"]
            actual_outer = (
                u32(first_value(save, fields[2703])),
                first_value(save, fields[2704]),
                first_value(save, fields[2707]),
            )
            if actual_outer != (entry["outer_hash"], OUTER_LEVEL, SIGIL_FLAGS):
                raise RuntimeError(
                    f"outer verification failed for {character_id}/{unit}"
                )
            for lane_index, lane in enumerate((entry["primary"], entry["secondary"])):
                lane_fields = instance["lanes"][lane_index]
                actual = (
                    u32(first_value(save, lane_fields[1701])),
                    first_value(save, lane_fields[1702]),
                )
                expected = (EMPTY_HASH, 0) if lane is None else (lane["hash"], lane["level"])
                if actual != expected:
                    raise RuntimeError(
                        f"trait verification failed for {character_id}/{unit}/{lane_index}"
                    )
        validate_aggregate_levels(builds[character_id], caps_by_hash, character_id)


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    character_path = args.characters.resolve()
    preset_path = args.preset.resolve()
    pair_catalog_path = args.pair_catalog.resolve()
    cap_catalog_path = args.cap_catalog.resolve()
    audit_path = args.audit.resolve()
    required = (
        (input_path, "input save"),
        (character_path, "character catalog"),
        (preset_path, "standard sigil preset"),
        (pair_catalog_path, "sigil legal-pair catalog"),
        (cap_catalog_path, "skill-level cap catalog"),
    )
    for path, label in required:
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if len({input_path, output_path, audit_path}) != 3:
        raise RuntimeError("input, output, and audit paths must be distinct")
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("refusing to overwrite an existing output or audit")
    if args.expected_preset_sha256:
        expected = args.expected_preset_sha256.strip().upper()
        if sha256_file(preset_path) != expected:
            raise RuntimeError("standard preset SHA-256 differs from expected")

    order, builds, metadata, preset, source = load_standard_preset(
        preset_path,
        pair_catalog_path,
        cap_catalog_path,
        character_path,
    )
    build_sha256 = build_digest(builds)
    if args.expected_build_sha256:
        expected = args.expected_build_sha256.strip().upper()
        if build_sha256 != expected:
            raise RuntimeError(f"build digest {build_sha256} != expected {expected}")

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
    mapped = map_character_units(save, [catalog_by_id[value] for value in order])
    mapped_by_id = {row["id"]: row for row in mapped}
    characters = [mapped_by_id[value] for value in order]
    instances, slot_to_unit = collect_instances(save)
    selections, relationships = equipped_instances(
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
    selected_sigil_units = {unit for values in selections.values() for unit in values}
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
    if args.expected_record_changes is not None and len(changes) != args.expected_record_changes:
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
        relationships,
    )
    caps_by_hash = load_cap_catalog(cap_catalog_path)[1]
    verify_builds(save, characters, instances, selections, builds, caps_by_hash)

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
    if output_selections != selections or output_relationships != relationships:
        raise RuntimeError("serialized equipment relationships changed")
    verify_builds(
        output,
        characters,
        output_instances,
        output_selections,
        builds,
        caps_by_hash,
    )

    idempotent_before = full_snapshot(output)
    apply_builds(
        output,
        characters,
        output_instances,
        output_selections,
        builds,
        metadata,
    )
    idempotent_changes = changed_records(idempotent_before, full_snapshot(output))
    if idempotent_changes:
        raise RuntimeError(
            f"standard sigil transform is not idempotent: {idempotent_changes[:3]}"
        )

    nonempty_lanes = sum(
        lane is not None
        for entries in builds.values()
        for entry in entries
        for lane in (entry["primary"], entry["secondary"])
    )
    empty_lanes = EXPECTED_CHARACTER_COUNT * 24 - nonempty_lanes
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
            "size": output_path.stat().st_size,
            "record_count": len(output.records),
        },
        "preset": {
            "id": preset["id"],
            "path": str(preset_path),
            "sha256": sha256_file(preset_path),
            "build_sha256": build_sha256,
            "legality_mode": preset["legality_mode"],
        },
        "catalogs": {
            "pairs": {
                "path": str(pair_catalog_path),
                "sha256": sha256_file(pair_catalog_path),
                "source_sha256": source["pair_catalog"]["source"]["sha256"],
            },
            "caps": {
                "path": str(cap_catalog_path),
                "sha256": sha256_file(cap_catalog_path),
                "source_sha256": source["cap_catalog"]["source"]["sha256"],
            },
        },
        "counts": {
            "characters": EXPECTED_CHARACTER_COUNT,
            "equipped_sigils": EXPECTED_CHARACTER_COUNT * 12,
            "nonempty_trait_lanes": nonempty_lanes,
            "empty_trait_lanes": empty_lanes,
            "record_changes": len(changes),
            "idempotent_changes": 0,
        },
        "policy": {
            "database_rows_only": True,
            "all_nonempty_lanes_level_15": True,
            "no_level_99_lanes": True,
            "all_aggregate_skill_levels_within_curve_caps": True,
            "empty_secondary_lanes_written_as_empty_hash_level_zero": True,
            "djeeta_uses_real_91_fallback": True,
            "can_only_hold_one_rows_not_repeated": True,
            "existing_instance_ids_preserved": True,
            "existing_owner_links_preserved": True,
            "existing_1403_loadouts_preserved": True,
            "protected_main_story_fields": list(MAIN_STORY_FIELDS),
        },
        "characters": character_rows,
        "changes": changes,
        "validation": {
            "semantic_result_verified": True,
            "active_hash_ok": True,
            "steam_wrapper_unchanged": True,
            "payload_size_unchanged": True,
            "record_count_unchanged": True,
            "main_story_unchanged": True,
            "all_relationships_unchanged": True,
            "serialized_records_exact": True,
            "second_application_is_noop": True,
        },
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "audit": str(audit_path),
                "preset": preset["id"],
                "build_sha256": build_sha256,
                "characters": EXPECTED_CHARACTER_COUNT,
                "nonempty_trait_lanes": nonempty_lanes,
                "empty_trait_lanes": empty_lanes,
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
