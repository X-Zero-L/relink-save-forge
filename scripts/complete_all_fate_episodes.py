"""Complete all catalog-backed Fate episodes while preserving main-story state."""

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from gbfr_hash import gbfr_hash_hex
from save_editor_api import GBFRSaveData, add_editor_argument


ROOT = Path(__file__).resolve().parents[1]
MAIN_FIELDS = (2510, 2511, 2520, 2522)
EXPECTED_COUNTS = {
    "rows": 324,
    "fate_episodes": 319,
    "remi_rows": 5,
    "characters": 29,
    "episodes_per_character": 11,
    "nonzero_mission_references": 58,
    "unique_mission_quest_ids": 56,
    "shared_mission_quest_ids": 2,
}
EXPECTED_CONTRACT = {
    "fate_id_field": 3501,
    "fate_state_field": 3502,
    "completed_state": 30,
    "real_rows": 324,
    "fate_rows_to_complete": 319,
    "remi_rows_to_preserve": 5,
    "placeholder_rows_to_preserve": 496,
    "total_rows": 820,
    "placeholder_hash": "887AE0B0",
    "placeholder_state": 5,
    "mission_id_field": 2560,
    "mission_status_field": 2561,
    "mission_vector_length": 100,
    "mission_nonzero_entries": 56,
    "mission_empty_entries": 44,
    "mission_minimum_clear_count": 1,
}


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def refuse_live_output(output: Path) -> None:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    live_directory = resolved(local_app_data / "GBFR" / "Saved" / "SaveGames")
    target = resolved(output)
    if target == live_directory or live_directory in target.parents:
        raise RuntimeError(f"Refusing to write into the live save directory: {target}")


def records_digest(save: GBFRSaveData, field_ids) -> str:
    rows = []
    for field_id in field_ids:
        for record in save.find(id_type=field_id):
            rows.append(
                (
                    record.kind,
                    field_id,
                    record.unit_id,
                    list(save.get_values(record)),
                )
            )
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def record_snapshot(save: GBFRSaveData) -> dict[tuple[str, int, int], list]:
    return {
        (record.kind, record.id_type, record.unit_id): list(save.get_values(record))
        for record in save.records
    }


def record_delta(before: GBFRSaveData, after: GBFRSaveData) -> dict:
    left = record_snapshot(before)
    right = record_snapshot(after)
    changed = []
    for key in sorted(set(left) & set(right)):
        if left[key] != right[key]:
            changed.append(
                {
                    "kind": key[0],
                    "field_id": key[1],
                    "unit": key[2],
                    "before": left[key],
                    "after": right[key],
                }
            )
    return {
        "added": [list(key) for key in sorted(set(right) - set(left))],
        "removed": [list(key) for key in sorted(set(left) - set(right))],
        "changed": changed,
        "changed_field_counts": dict(
            sorted(Counter(row["field_id"] for row in changed).items())
        ),
        "changed_field_types": sorted({row["field_id"] for row in changed}),
    }


def load_catalog(path: Path) -> dict:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != 1:
        raise RuntimeError("Expected Fate catalog schema_version 1")
    if catalog.get("counts") != EXPECTED_COUNTS:
        raise RuntimeError(f"Unexpected Fate catalog counts: {catalog.get('counts')}")
    if catalog.get("save_contract") != EXPECTED_CONTRACT:
        raise RuntimeError(
            f"Unexpected Fate save contract: {catalog.get('save_contract')}"
        )
    items = catalog.get("items")
    if catalog.get("count") != EXPECTED_COUNTS["rows"] or not isinstance(items, list):
        raise RuntimeError("Fate catalog count/items are inconsistent")

    keys = [str(item.get("key") or "") for item in items]
    hashes = [str(item.get("hash") or "").upper() for item in items]
    if len(set(keys)) != len(items) or len(set(hashes)) != len(items):
        raise RuntimeError("Fate catalog keys or hashes are not unique")
    for item, key, hash_hex in zip(items, keys, hashes):
        if hash_hex != gbfr_hash_hex(key):
            raise RuntimeError(f"Fate catalog hash mismatch for {key}")
        if item.get("kind") not in {"fate", "remi"}:
            raise RuntimeError(f"Unexpected Fate catalog kind for {key}")

    fate_items = [item for item in items if item["kind"] == "fate"]
    remi_items = [item for item in items if item["kind"] == "remi"]
    if len(fate_items) != 319 or len(remi_items) != 5:
        raise RuntimeError("Fate catalog must contain 319 FATE and 5 REMI rows")
    characters = catalog.get("characters")
    if not isinstance(characters, list) or len(characters) != 29:
        raise RuntimeError("Fate catalog must contain 29 character summaries")
    for character in characters:
        episode_keys = character.get("episode_keys")
        if character.get("episode_count") != 11 or len(episode_keys or []) != 11:
            raise RuntimeError(
                f"{character.get('character_id')} does not contain 11 Fate episodes"
            )

    mission_rows = catalog.get("mission_quests")
    if not isinstance(mission_rows, list) or len(mission_rows) != 56:
        raise RuntimeError("Fate catalog must contain 56 unique mission rows")
    mission_values = set()
    for row in mission_rows:
        mission_id = str(row.get("mission_quest_id") or "")
        mission_value = int(mission_id, 16)
        if int(row.get("value", -1)) != mission_value or not mission_value:
            raise RuntimeError(f"Invalid MissionQuestId row: {row}")
        mission_values.add(mission_value)
    if len(mission_values) != 56:
        raise RuntimeError("Fate mission values are not unique")

    return {
        "catalog": catalog,
        "items": items,
        "fate_items": fate_items,
        "remi_items": remi_items,
        "hash_by_key": {key: int(hash_hex, 16) for key, hash_hex in zip(keys, hashes)},
        "fate_hashes": {int(item["hash"], 16) for item in fate_items},
        "remi_hashes": {int(item["hash"], 16) for item in remi_items},
        "missions": mission_values,
    }


def single_record(save: GBFRSaveData, field_id: int):
    records = save.find(id_type=field_id)
    if len(records) != 1:
        raise RuntimeError(f"Expected one field {field_id} record, found {len(records)}")
    return records[0]


def inspect_layout(save: GBFRSaveData, catalog: dict) -> dict:
    contract = EXPECTED_CONTRACT
    key_records_list = save.find(id_type=contract["fate_id_field"])
    state_records_list = save.find(id_type=contract["fate_state_field"])
    expected_total = contract["total_rows"]
    if len(key_records_list) != expected_total or len(state_records_list) != expected_total:
        raise RuntimeError(
            "Expected 820 Fate key/state records, found "
            f"{len(key_records_list)}/{len(state_records_list)}"
        )
    if any(record.kind != "uint" or record.value_count != 1 for record in key_records_list):
        raise RuntimeError("Unexpected 3501 record shape")
    if any(record.kind != "uint" or record.value_count != 1 for record in state_records_list):
        raise RuntimeError("Unexpected 3502 record shape")
    key_records = {record.unit_id: record for record in key_records_list}
    state_records = {record.unit_id: record for record in state_records_list}
    if len(key_records) != expected_total or len(state_records) != expected_total:
        raise RuntimeError("Fate key/state units are not unique")
    if set(key_records) != set(state_records):
        raise RuntimeError("Fate key/state unit sets differ")

    placeholder_hash = int(contract["placeholder_hash"], 16)
    by_hash = {}
    placeholder_units = []
    for unit in sorted(key_records):
        key_hash = int(save.get_first_value(key_records[unit], 0)) & 0xFFFFFFFF
        state = int(save.get_first_value(state_records[unit], 0))
        if key_hash == placeholder_hash:
            placeholder_units.append(unit)
            if state != contract["placeholder_state"]:
                raise RuntimeError(
                    f"Placeholder Fate unit {unit} has state {state}, "
                    f"expected {contract['placeholder_state']}"
                )
            continue
        if key_hash in by_hash:
            raise RuntimeError(f"Duplicate Fate key hash 0x{key_hash:08X}")
        by_hash[key_hash] = unit
    if len(placeholder_units) != contract["placeholder_rows_to_preserve"]:
        raise RuntimeError(
            f"Expected 496 placeholder Fate rows, found {len(placeholder_units)}"
        )
    catalog_hashes = set(catalog["hash_by_key"].values())
    if set(by_hash) != catalog_hashes:
        missing = sorted(catalog_hashes - set(by_hash))
        extra = sorted(set(by_hash) - catalog_hashes)
        raise RuntimeError(
            "Save/catalog Fate hash mismatch: "
            f"missing={[f'{value:08X}' for value in missing]}, "
            f"extra={[f'{value:08X}' for value in extra]}"
        )

    mission_key_record = single_record(save, contract["mission_id_field"])
    mission_state_record = single_record(save, contract["mission_status_field"])
    mission_keys = [
        int(value) & 0xFFFFFFFF for value in save.get_values(mission_key_record)
    ]
    mission_states = [int(value) for value in save.get_values(mission_state_record)]
    if len(mission_keys) != contract["mission_vector_length"] or len(
        mission_states
    ) != contract["mission_vector_length"]:
        raise RuntimeError("Mission key/state vectors must both contain 100 values")
    nonzero_missions = [value for value in mission_keys if value]
    if len(nonzero_missions) != contract["mission_nonzero_entries"]:
        raise RuntimeError(
            f"Expected 56 nonzero mission entries, found {len(nonzero_missions)}"
        )
    if len(set(nonzero_missions)) != len(nonzero_missions):
        raise RuntimeError("Mission keys in 2560 are not unique")
    if set(nonzero_missions) != catalog["missions"]:
        raise RuntimeError("Mission keys in 2560 do not equal catalog MissionQuestIds")
    empty_mission_indexes = [
        index for index, value in enumerate(mission_keys) if not value
    ]
    if len(empty_mission_indexes) != contract["mission_empty_entries"]:
        raise RuntimeError(
            f"Expected 44 empty mission entries, found {len(empty_mission_indexes)}"
        )
    if any(mission_states[index] != 0 for index in empty_mission_indexes):
        raise RuntimeError("An empty 2560 entry has a nonzero 2561 status")
    return {
        "key_records": key_records,
        "state_records": state_records,
        "by_hash": by_hash,
        "placeholder_units": placeholder_units,
        "mission_key_record": mission_key_record,
        "mission_state_record": mission_state_record,
        "mission_keys": mission_keys,
        "mission_states": mission_states,
        "empty_mission_indexes": empty_mission_indexes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("save", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "catalogs" / "fate-episodes-2.0.json",
    )
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--expect-steam-id", type=int)
    add_editor_argument(parser)
    args = parser.parse_args()

    source_path = resolved(args.save)
    output_path = resolved(args.output)
    catalog_path = resolved(args.catalog)
    audit_path = resolved(args.audit)
    if source_path == output_path:
        raise RuntimeError("Refusing to overwrite the input save")
    refuse_live_output(output_path)
    refuse_live_output(audit_path)
    if not source_path.is_file() or not catalog_path.is_file():
        raise RuntimeError("Input save or Fate catalog does not exist")
    if len({source_path, output_path, catalog_path, audit_path}) != 4:
        raise RuntimeError("Input, output, catalog, and audit paths must be distinct")
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("Refusing to overwrite an existing output or audit")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    source_sha = sha256_file(source_path)
    source_size = source_path.stat().st_size
    source = GBFRSaveData.open(source_path)
    if source.check_active_hash() is not True:
        raise RuntimeError("Input save active hash is invalid")
    source_header = source.container.header or {}
    source_steam_id = source_header.get("steam_id")
    if args.expect_steam_id is not None and source_steam_id != args.expect_steam_id:
        raise RuntimeError(
            f"Expected SteamID64 {args.expect_steam_id}, found {source_steam_id}"
        )

    catalog = load_catalog(catalog_path)
    layout = inspect_layout(source, catalog)
    main_digest = records_digest(source, MAIN_FIELDS)
    key_digest = records_digest(source, (EXPECTED_CONTRACT["fate_id_field"],))
    mission_key_digest = records_digest(
        source, (EXPECTED_CONTRACT["mission_id_field"],)
    )
    remi_before = {
        key_hash: int(
            source.get_first_value(
                layout["state_records"][layout["by_hash"][key_hash]], 0
            )
        )
        for key_hash in catalog["remi_hashes"]
    }
    placeholders_before = {
        unit: int(source.get_first_value(layout["state_records"][unit], 0))
        for unit in layout["placeholder_units"]
    }

    source_statuses = Counter()
    fate_changes = []
    for item in catalog["fate_items"]:
        key = item["key"]
        key_hash = catalog["hash_by_key"][key]
        unit = layout["by_hash"][key_hash]
        record = layout["state_records"][unit]
        old = int(source.get_first_value(record, 0))
        source_statuses[old] += 1
        if old != EXPECTED_CONTRACT["completed_state"]:
            source.set_first_value(record, EXPECTED_CONTRACT["completed_state"])
            fate_changes.append(
                {
                    "key": key,
                    "hash": f"{key_hash:08X}",
                    "unit": unit,
                    "before": old,
                    "after": EXPECTED_CONTRACT["completed_state"],
                }
            )

    mission_states = list(layout["mission_states"])
    mission_changes = []
    for index, mission in enumerate(layout["mission_keys"]):
        if not mission:
            continue
        old = mission_states[index]
        new = max(old, EXPECTED_CONTRACT["mission_minimum_clear_count"])
        mission_states[index] = new
        if old != new:
            mission_changes.append(
                {
                    "index": index,
                    "mission_quest_id": f"{mission:08X}",
                    "before": old,
                    "after": new,
                }
            )
    source.set_values(layout["mission_state_record"], mission_states)

    if records_digest(source, MAIN_FIELDS) != main_digest:
        raise RuntimeError("Protected main-story fields changed in memory")
    if records_digest(source, (EXPECTED_CONTRACT["fate_id_field"],)) != key_digest:
        raise RuntimeError("Fate ID field 3501 changed in memory")
    if records_digest(
        source, (EXPECTED_CONTRACT["mission_id_field"],)
    ) != mission_key_digest:
        raise RuntimeError("Mission ID field 2560 changed in memory")
    source.save_as(output_path, update_hash=True)

    if sha256_file(source_path) != source_sha:
        raise RuntimeError("Input save changed while the output was being built")
    output = GBFRSaveData.open(output_path)
    if output.check_active_hash() is not True:
        raise RuntimeError("Output active hash is invalid")
    if output_path.stat().st_size != source_size:
        raise RuntimeError("Output save size changed")
    if (output.container.header or {}).get("steam_id") != source_steam_id:
        raise RuntimeError("SteamID64 changed")
    if records_digest(output, MAIN_FIELDS) != main_digest:
        raise RuntimeError("Protected main-story fields changed after serialization")
    if records_digest(output, (EXPECTED_CONTRACT["fate_id_field"],)) != key_digest:
        raise RuntimeError("Fate ID field 3501 changed after serialization")
    if records_digest(
        output, (EXPECTED_CONTRACT["mission_id_field"],)
    ) != mission_key_digest:
        raise RuntimeError("Mission ID field 2560 changed after serialization")

    output_layout = inspect_layout(output, catalog)
    for item in catalog["fate_items"]:
        key_hash = catalog["hash_by_key"][item["key"]]
        unit = output_layout["by_hash"][key_hash]
        state = int(output.get_first_value(output_layout["state_records"][unit], 0))
        if state != EXPECTED_CONTRACT["completed_state"]:
            raise RuntimeError(f"{item['key']} did not persist as complete")
    remi_after = {
        key_hash: int(
            output.get_first_value(
                output_layout["state_records"][output_layout["by_hash"][key_hash]],
                0,
            )
        )
        for key_hash in catalog["remi_hashes"]
    }
    if remi_after != remi_before:
        raise RuntimeError("REMI states changed")
    placeholders_after = {
        unit: int(output.get_first_value(output_layout["state_records"][unit], 0))
        for unit in output_layout["placeholder_units"]
    }
    if placeholders_after != placeholders_before:
        raise RuntimeError("Placeholder Fate states changed")
    final_mission_states = [
        int(value) for value in output.get_values(output_layout["mission_state_record"])
    ]
    for index, mission in enumerate(output_layout["mission_keys"]):
        if mission and final_mission_states[index] < 1:
            raise RuntimeError(f"Mission {mission:08X} remains incomplete")
        if not mission and final_mission_states[index] != 0:
            raise RuntimeError(f"Empty mission entry {index} changed")

    original = GBFRSaveData.open(source_path)
    delta = record_delta(original, output)
    if len(original.records) != len(output.records):
        raise RuntimeError("Save record count changed")
    if delta["added"] or delta["removed"]:
        raise RuntimeError("Save records were added or removed")
    allowed_changed_fields = {
        EXPECTED_CONTRACT["fate_state_field"],
        EXPECTED_CONTRACT["mission_status_field"],
    }
    if not set(delta["changed_field_types"]).issubset(allowed_changed_fields):
        raise RuntimeError(f"Unexpected changed fields: {delta['changed_field_types']}")
    fate_units = {
        layout["by_hash"][key_hash] for key_hash in catalog["fate_hashes"]
    }
    for change in delta["changed"]:
        if (
            change["field_id"] == EXPECTED_CONTRACT["fate_state_field"]
            and change["unit"] not in fate_units
        ):
            raise RuntimeError(f"Non-FATE 3502 unit changed: {change['unit']}")
        if (
            change["field_id"] == EXPECTED_CONTRACT["mission_status_field"]
            and change["unit"] != 0
        ):
            raise RuntimeError(f"Unexpected 2561 unit changed: {change['unit']}")

    audit = {
        "catalog": {
            "sha256": sha256_file(catalog_path),
            "source_table_sha256": catalog["catalog"]["source"].get(
                "source_table_sha256"
            ),
            "counts": catalog["catalog"]["counts"],
        },
        "input": {
            "sha256": source_sha,
            "size": source_size,
            "steam_id": source_steam_id,
            "active_hash_ok": True,
            "fate_statuses": dict(sorted(source_statuses.items())),
        },
        "layout": {
            "fate_slots": len(layout["key_records"]),
            "real_rows": len(layout["by_hash"]),
            "placeholder_rows": len(layout["placeholder_units"]),
            "mission_entries": len(layout["mission_keys"]),
            "nonzero_missions": sum(bool(value) for value in layout["mission_keys"]),
            "empty_missions": len(layout["empty_mission_indexes"]),
        },
        "changes": {"fate": fate_changes, "missions": mission_changes},
        "record_delta": delta,
        "verification": {
            "fate_complete": EXPECTED_CONTRACT["fate_rows_to_complete"],
            "missions_complete": EXPECTED_CONTRACT["mission_nonzero_entries"],
            "active_hash_ok": True,
            "steam_id_unchanged": True,
            "main_story_unchanged": True,
            "fate_ids_unchanged": True,
            "mission_ids_unchanged": True,
            "remi_unchanged": True,
            "placeholder_rows_unchanged": True,
            "input_sha_unchanged": True,
        },
        "output": {
            "sha256": sha256_file(output_path),
            "size": output_path.stat().st_size,
        },
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "fate_complete": EXPECTED_CONTRACT["fate_rows_to_complete"],
                "fate_changed": len(fate_changes),
                "mission_changed": len(mission_changes),
                "changed_field_types": delta["changed_field_types"],
                "active_hash_ok": True,
                "main_story_unchanged": True,
                "output_sha256": audit["output"]["sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
