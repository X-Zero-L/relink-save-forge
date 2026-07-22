import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from gbfr_hash import gbfr_hash
from save_editor_api import EDITOR_ROOT, GBFRSaveData, add_editor_argument


EMPTY_HASH = 0x887AE0B0
MATERIAL_FIELDS = (1801, 1802, 1803, 1804, 1805, 1806, 1807)
STACK_FIELDS = (1801, 1802, 1803, 1804, 1807)
MAIN_STORY_FIELDS = (2510, 2511, 2520, 2522)
WALLET_TARGETS = {
    1104: ("Rupies", 99_999_999),
    1106: ("Commendations", 999),
    1112: ("Mastery Points", 9_999_999),
}

# These item.tbl categories are real stack-like inventory families.  Category
# 10 is only a shadow presentation of direct wallet fields, category 11 is
# Fate/story-special inventory, and category 14 is item_important/key-item data.
ALLOWED_ITEM_CATEGORY_IDS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 13, 15}

# Game-owned rows show a stable family split in this save and in the known-good
# all-items sample: consumables/wrightstones/special coins/tickets use state 4;
# normal materials, EXP/MSP items, cards, munitions, and glitterstones use 12.
STATE_4_ITEM_CATEGORY_IDS = {5, 6, 9, 13}

HEX_HASH_RE = re.compile(r"^[0-9A-Fa-f]{8}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a hash-valid 2.0 material/consumable inventory save without touching 210x item instances or story progress."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Source save; always work on an offline copy",
    )
    parser.add_argument(
        "output",
        type=Path,
        help="Offline output save",
    )
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="Live 2.0 item SQLite database",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        required=True,
        help="JSON audit report",
    )
    parser.add_argument(
        "--quantity",
        type=int,
        default=900,
        help="Target ordinary stack quantity (1-999, default: 900)",
    )
    add_editor_argument(parser)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_item_hash(raw_key: str) -> int:
    key = str(raw_key or "").strip()
    if not key:
        raise ValueError("item table contains a blank Key")
    # GBFRDataTools emits unresolved GBIDs as their already-computed 8-digit
    # hash. Hashing that text a second time creates a fake item ID.
    if HEX_HASH_RE.fullmatch(key):
        return int(key, 16) & 0xFFFFFFFF
    return gbfr_hash(key) & 0xFFFFFFFF


def first_value(save: GBFRSaveData, record, default=0) -> int:
    if record is None:
        return int(default)
    value = save.get_first_value(record, default)
    return int(default if value is None else value)


def set_if_changed(save: GBFRSaveData, record, value: int) -> bool:
    if record is None:
        raise RuntimeError("required save record is missing")
    if first_value(save, record) == int(value):
        return False
    save.set_first_value(record, int(value))
    return True


def record_snapshot(save: GBFRSaveData, field_ids) -> list[dict]:
    wanted = set(field_ids)
    rows = []
    for record in save.records:
        if record.id_type not in wanted:
            continue
        rows.append(
            {
                "kind": record.kind,
                "index": record.index,
                "id_type": record.id_type,
                "unit_id": record.unit_id,
                "values": save.get_values(record),
            }
        )
    rows.sort(key=lambda row: (row["id_type"], row["unit_id"], row["kind"], row["index"]))
    return rows


def snapshot_digest(rows: list[dict]) -> str:
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_curated_internal_hashes() -> tuple[set[int], list[dict]]:
    resources = EDITOR_ROOT / "gbfr_editor" / "resources"
    internal_hashes: set[int] = set()
    rows = []
    for filename in (
        "item_ids_seed.csv",
        "item_ids_sheet_merged.csv",
        "item_ids_manual_supplement.csv",
    ):
        path = resources / filename
        if not path.is_file():
            continue
        import csv

        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                name = str(row.get("name") or row.get("item_name") or "").strip()
                if not re.search(r"\b(internal|unused|reserved|dummy)\b", name, flags=re.IGNORECASE):
                    continue
                raw_hash = str(row.get("hash") or row.get("hash_hex") or "").strip()
                if not HEX_HASH_RE.fullmatch(raw_hash):
                    continue
                item_hash = int(raw_hash, 16) & 0xFFFFFFFF
                internal_hashes.add(item_hash)
                rows.append(
                    {
                        "source": filename,
                        "key": str(row.get("id") or row.get("item_id") or ""),
                        "name": name,
                        "hash": f"{item_hash:08X}",
                    }
                )
    return internal_hashes, rows


def load_database(path: Path) -> tuple[list[dict], set[int], list[str], set[int], list[dict]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        required_tables = {"item", "item_consume", "item_important"}
        present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(required_tables - present)
        if missing:
            raise RuntimeError(f"database is missing required tables: {missing}")

        items = [dict(row) for row in connection.execute("SELECT * FROM item")]
        important_hashes = {
            normalized_item_hash(row[0])
            for row in connection.execute("SELECT Key FROM item_important")
        }
        consume_keys = [str(row[0]) for row in connection.execute("SELECT Key FROM item_consume")]
    finally:
        connection.close()

    seen: dict[int, str] = {}
    for row in items:
        item_hash = normalized_item_hash(row["Key"])
        previous = seen.get(item_hash)
        if previous is not None:
            raise RuntimeError(
                f"item.tbl hash collision/duplicate: {previous!r} and {row['Key']!r} -> 0x{item_hash:08X}"
            )
        seen[item_hash] = str(row["Key"])
        row["_hash"] = item_hash
    internal_hashes, internal_rows = load_curated_internal_hashes()
    return items, important_hashes, consume_keys, internal_hashes, internal_rows


def classify_item(
    row: dict,
    important_hashes: set[int],
    curated_internal_hashes: set[int],
) -> tuple[bool, str]:
    if int(row.get("IsVisible") or 0) != 1:
        return False, "not_visible"
    item_hash = int(row["_hash"])
    if item_hash in important_hashes:
        return False, "item_important_story_or_key_item"
    if item_hash in curated_internal_hashes:
        return False, "curated_internal_or_reserved_item"
    if not str(row.get("ItemName") or "").strip():
        return False, "internal_or_reserved_blank_name"

    category_id = int(row.get("ItemCategoryId") or 0)
    if category_id == 10:
        return False, "wallet_shadow_use_1104_1106_1112"
    if category_id == 11:
        return False, "fate_or_story_special_not_ordinary_inventory"
    if category_id == 14:
        return False, "key_item_category"
    if category_id not in ALLOWED_ITEM_CATEGORY_IDS:
        return False, f"unsupported_nonordinary_item_category_{category_id}"
    return True, "visible_nonimportant_material_or_consumable"


def activation_state(row: dict) -> int:
    category_id = int(row.get("ItemCategoryId") or 0)
    return 4 if category_id in STATE_4_ITEM_CATEGORY_IDS else 12


def required_single_record(save: GBFRSaveData, id_type: int, unit_id: int = 0):
    records = save.find(id_type=id_type, unit_id=unit_id)
    if len(records) != 1:
        raise RuntimeError(f"expected one save record for {id_type}/unit {unit_id}, found {len(records)}")
    return records[0]


def validate_stack_layout(save: GBFRSaveData) -> tuple[dict[int, dict], dict[int, list[int]], list[int]]:
    grouped = save.group_by_unit(MATERIAL_FIELDS)
    stack_units: dict[int, dict] = {}
    by_hash: dict[int, list[int]] = defaultdict(list)
    empty_units: list[int] = []

    for unit_id, fields in sorted(grouped.items()):
        if not all(field_id in fields for field_id in STACK_FIELDS):
            continue
        stack_units[unit_id] = fields
        item_hash = first_value(save, fields[1801]) & 0xFFFFFFFF
        by_hash[item_hash].append(unit_id)
        if item_hash != EMPTY_HASH:
            continue
        template_values = {
            1802: first_value(save, fields[1802]),
            1803: first_value(save, fields[1803]),
            1804: first_value(save, fields[1804]),
            1807: first_value(save, fields[1807]),
        }
        if any(template_values.values()):
            raise RuntimeError(
                f"empty 180x unit {unit_id} is not a pristine real slot template: {template_values}"
            )
        empty_units.append(unit_id)

    if not stack_units:
        raise RuntimeError("save contains no complete 1801/1802/1803/1804/1807 material rows")
    for item_hash, units in by_hash.items():
        if item_hash != EMPTY_HASH and len(units) != 1:
            raise RuntimeError(f"duplicate non-empty 1801 hash 0x{item_hash:08X} in units {units}")
    return stack_units, by_hash, empty_units


def build_save(args: argparse.Namespace) -> dict:
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    database_path = Path(args.database).resolve()
    audit_path = Path(args.audit).resolve()
    quantity_target = int(args.quantity)
    if not 1 <= quantity_target <= 999:
        raise RuntimeError("quantity must be between 1 and 999")

    for path, label in [(input_path, "input save"), (database_path, "database")]:
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if output_path == input_path:
        raise RuntimeError("refusing to overwrite the input save; choose a separate offline output path")

    input_sha_before = sha256_file(input_path)
    save = GBFRSaveData.open(input_path)
    if save.check_active_hash() is not True:
        raise RuntimeError("input save active hash is not valid")

    items, important_hashes, consume_keys, curated_internal_hashes, curated_internal_rows = load_database(database_path)
    stack_units, units_by_hash, empty_units = validate_stack_layout(save)

    story_before = record_snapshot(save, MAIN_STORY_FIELDS)
    story_digest_before = snapshot_digest(story_before)
    item_210x_before = record_snapshot(save, range(2100, 2200))
    item_210x_digest_before = snapshot_digest(item_210x_before)
    material_1806_before = record_snapshot(save, (1806,))
    material_1806_digest_before = snapshot_digest(material_1806_before)
    header_before = dict(save.container.header)
    payload_size_before = save.container.payload_size
    record_count_before = len(save.records)

    classifications = Counter()
    excluded_rows = []
    candidates = []
    for row in items:
        included, reason = classify_item(row, important_hashes, curated_internal_hashes)
        classifications[reason] += 1
        if included:
            candidates.append(row)
        else:
            excluded_rows.append(
                {
                    "key": row["Key"],
                    "hash": f"{int(row['_hash']):08X}",
                    "item_name_key": row.get("ItemName") or "",
                    "item_category_id": int(row.get("ItemCategoryId") or 0),
                    "min_feature_version": int(row.get("MinFeatureVersion") or 0),
                    "reason": reason,
                }
            )

    candidates.sort(
        key=lambda row: (
            int(row.get("MinFeatureVersion") or 0),
            int(row.get("SortOrder") or 0),
            int(row["_hash"]),
        )
    )
    candidate_hashes = {int(row["_hash"]) for row in candidates}
    if len(candidate_hashes) != len(candidates):
        raise RuntimeError("candidate item hashes are not unique")

    consume_hashes = {normalized_item_hash(key) for key in consume_keys}
    missing_real_consumables = sorted(
        item_hash for item_hash in consume_hashes if item_hash not in candidate_hashes
    )
    # item_consume includes one blank-name reserved row in the live table. It is
    # intentionally excluded; every named consumable must be selected.
    named_item_hashes = {
        int(row["_hash"])
        for row in items
        if str(row.get("ItemName") or "").strip()
    }
    unexpected_missing_consumables = [
        item_hash for item_hash in missing_real_consumables if item_hash in named_item_hashes
    ]
    if unexpected_missing_consumables:
        rendered = [f"0x{item_hash:08X}" for item_hash in unexpected_missing_consumables]
        raise RuntimeError(f"named item_consume rows were excluded unexpectedly: {rendered}")

    missing_hashes = [int(row["_hash"]) for row in candidates if int(row["_hash"]) not in units_by_hash]
    if len(missing_hashes) > len(empty_units):
        raise RuntimeError(
            f"live database needs {len(missing_hashes)} new material rows but only {len(empty_units)} pristine 180x templates exist"
        )

    counter_record = required_single_record(save, 1805, 0)
    counter_before = first_value(save, counter_record)
    existing_instance_ids = {
        first_value(save, fields[1804])
        for fields in stack_units.values()
        if first_value(save, fields[1804]) > 0
    }
    max_instance_before = max(existing_instance_ids, default=0)
    if counter_before < max_instance_before:
        raise RuntimeError(
            f"1805 global counter {counter_before} is below active 1804 max {max_instance_before}"
        )
    next_instance_id = counter_before

    empty_iter = iter(sorted(empty_units))
    activated = []
    maxed_existing = []
    reused_empty_templates = []
    touched_units = set()
    scalar_changes = Counter()

    for row in candidates:
        item_hash = int(row["_hash"])
        units = units_by_hash.get(item_hash, [])
        reused_empty = False
        if units:
            unit_id = units[0]
        else:
            unit_id = next(empty_iter)
            reused_empty = True
            units_by_hash[item_hash] = [unit_id]
        fields = stack_units[unit_id]
        touched_units.add(unit_id)

        old = {
            "hash": first_value(save, fields[1801]) & 0xFFFFFFFF,
            "quantity": first_value(save, fields[1802]),
            "state": first_value(save, fields[1803]),
            "instance_id": first_value(save, fields[1804]),
            "extra": first_value(save, fields[1807]),
        }

        needs_full_activation = reused_empty or old["state"] == 0 or old["instance_id"] == 0
        if reused_empty:
            if old != {
                "hash": EMPTY_HASH,
                "quantity": 0,
                "state": 0,
                "instance_id": 0,
                "extra": 0,
            }:
                raise RuntimeError(f"chosen empty template unit {unit_id} changed before use: {old}")
            if set_if_changed(save, fields[1801], item_hash):
                scalar_changes["1801_hash"] += 1

        if needs_full_activation:
            next_instance_id += 1
            if next_instance_id in existing_instance_ids:
                raise RuntimeError(f"generated duplicate 1804 instance id {next_instance_id}")
            existing_instance_ids.add(next_instance_id)
            state = activation_state(row)
            if set_if_changed(save, fields[1803], state):
                scalar_changes["1803_state"] += 1
            if set_if_changed(save, fields[1804], next_instance_id):
                scalar_changes["1804_instance_id"] += 1
            if set_if_changed(save, fields[1807], 0):
                scalar_changes["1807_extra"] += 1
            activated.append(
                {
                    "unit": unit_id,
                    "key": row["Key"],
                    "hash": f"{item_hash:08X}",
                    "min_feature_version": int(row.get("MinFeatureVersion") or 0),
                    "item_category_id": int(row.get("ItemCategoryId") or 0),
                    "state": state,
                    "instance_id": next_instance_id,
                    "source": "pristine_empty_180x_template" if reused_empty else "exact_inactive_catalog_row",
                }
            )
            if reused_empty:
                reused_empty_templates.append(unit_id)

        if set_if_changed(save, fields[1802], quantity_target):
            scalar_changes["1802_quantity"] += 1
        maxed_existing.append(
            {
                "unit": unit_id,
                "key": row["Key"],
                "hash": f"{item_hash:08X}",
                "old_quantity": old["quantity"],
                "new_quantity": quantity_target,
            }
        )

    if next_instance_id != counter_before:
        if set_if_changed(save, counter_record, next_instance_id):
            scalar_changes["1805_global_counter"] += 1

    wallet_changes = []
    for field_id, (label, target) in WALLET_TARGETS.items():
        record = required_single_record(save, field_id, 0)
        old_value = first_value(save, record)
        changed = set_if_changed(save, record, target)
        wallet_changes.append(
            {
                "field_id": field_id,
                "label": label,
                "old": old_value,
                "new": target,
                "changed": changed,
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save.save_as(output_path, update_hash=True)

    # The source must remain byte-identical because this task is offline only.
    input_sha_after = sha256_file(input_path)
    if input_sha_after != input_sha_before:
        raise RuntimeError("input save changed unexpectedly")

    out = GBFRSaveData.open(output_path)
    if out.check_active_hash() is not True:
        raise RuntimeError("output active hash validation failed")
    if out.container.payload_size != payload_size_before or len(out.records) != record_count_before:
        raise RuntimeError("output FlatBuffer size or record count changed")
    if dict(out.container.header) != header_before:
        raise RuntimeError("save wrapper header/Steam account metadata changed")

    story_after = record_snapshot(out, MAIN_STORY_FIELDS)
    story_digest_after = snapshot_digest(story_after)
    if story_digest_after != story_digest_before:
        raise RuntimeError("protected main-story fields changed")

    item_210x_after = record_snapshot(out, range(2100, 2200))
    item_210x_digest_after = snapshot_digest(item_210x_after)
    if item_210x_digest_after != item_210x_digest_before:
        raise RuntimeError("210x item-instance records changed")

    material_1806_after = record_snapshot(out, (1806,))
    material_1806_digest_after = snapshot_digest(material_1806_after)
    if material_1806_digest_after != material_1806_digest_before:
        raise RuntimeError("1806 companion/global state changed")

    out_stack_units, out_units_by_hash, out_empty_units = validate_stack_layout(out)
    final_ids = []
    for row in candidates:
        item_hash = int(row["_hash"])
        units = out_units_by_hash.get(item_hash, [])
        if len(units) != 1:
            raise RuntimeError(f"candidate 0x{item_hash:08X} does not have exactly one output row")
        fields = out_stack_units[units[0]]
        quantity = first_value(out, fields[1802])
        state = first_value(out, fields[1803])
        instance_id = first_value(out, fields[1804])
        if quantity != quantity_target or state == 0 or instance_id <= 0:
            raise RuntimeError(
                f"candidate {row['Key']} is not a complete x{quantity_target} stack: "
                f"qty={quantity}, state={state}, instance={instance_id}"
            )
        final_ids.append(instance_id)
    duplicate_final_ids = [value for value, count in Counter(final_ids).items() if count > 1]
    if duplicate_final_ids:
        raise RuntimeError(f"selected inventory rows contain duplicate 1804 IDs: {duplicate_final_ids[:10]}")

    counter_after = first_value(out, required_single_record(out, 1805, 0))
    max_instance_after = max(
        (first_value(out, fields[1804]) for fields in out_stack_units.values()),
        default=0,
    )
    if counter_after < max_instance_after:
        raise RuntimeError(
            f"output 1805 global counter {counter_after} is below max 1804 instance {max_instance_after}"
        )

    wallet_after = {}
    for field_id, (label, target) in WALLET_TARGETS.items():
        value = first_value(out, required_single_record(out, field_id, 0))
        if value != target:
            raise RuntimeError(f"{label} field {field_id} did not persist target {target}: {value}")
        wallet_after[str(field_id)] = value

    output_sha = sha256_file(output_path)
    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": {
            "path": str(input_path),
            "sha256": input_sha_before,
            "active_hash_ok": True,
            "unchanged_after_run": input_sha_before == input_sha_after,
        },
        "output": {
            "path": str(output_path),
            "sha256": output_sha,
            "active_hash_ok": out.check_active_hash(),
            "size": output_path.stat().st_size,
            "steam_id": out.container.header.get("steam_id"),
        },
        "database": {
            "path": str(database_path),
            "sha256": sha256_file(database_path),
            "item_rows": len(items),
            "item_important_hashes": len(important_hashes),
            "item_consume_rows": len(consume_keys),
            "curated_internal_hashes": len(curated_internal_hashes),
            "curated_internal_rows": curated_internal_rows,
        },
        "policy": {
            "target_quantity": quantity_target,
            "selected_item_category_ids": sorted(ALLOWED_ITEM_CATEGORY_IDS),
            "activation_state_4_item_category_ids": sorted(STATE_4_ITEM_CATEGORY_IDS),
            "excluded": [
                "IsVisible != 1",
                "item_important/key/story hashes",
                "blank-name internal/reserved rows",
                "curated internal/reserved/dummy rows",
                "wallet shadow item rows (direct fields used instead)",
                "Fate/story-special category 11",
                "unsupported non-ordinary categories",
            ],
        },
        "counts": {
            "selected_stacks": len(candidates),
            "selected_feature_version_5": sum(
                int(row.get("MinFeatureVersion") or 0) == 5 for row in candidates
            ),
            "activated_full_records": len(activated),
            "reused_pristine_empty_templates": len(reused_empty_templates),
            "quantity_rows_changed": int(scalar_changes["1802_quantity"]),
            "empty_templates_before": len(empty_units),
            "empty_templates_after": len(out_empty_units),
            "classification": dict(sorted(classifications.items())),
        },
        "instance_allocator": {
            "counter_1805_before": counter_before,
            "counter_1805_after": counter_after,
            "max_1804_before": max_instance_before,
            "max_1804_after": max_instance_after,
            "new_ids": len(activated),
            "all_selected_1804_unique": True,
        },
        "wallet": {
            "changes": wallet_changes,
            "verified_after": wallet_after,
        },
        "changes": {
            "scalar_counts": dict(sorted(scalar_changes.items())),
            "activated": activated,
            "maxed_stacks": maxed_existing,
            "reused_empty_template_units": reused_empty_templates,
        },
        "excluded_rows": excluded_rows,
        "validation": {
            "active_hash_ok": out.check_active_hash() is True,
            "input_unchanged": input_sha_before == input_sha_after,
            "main_story_digest_before": story_digest_before,
            "main_story_digest_after": story_digest_after,
            "main_story_unchanged": story_digest_before == story_digest_after,
            "item_210x_digest_before": item_210x_digest_before,
            "item_210x_digest_after": item_210x_digest_after,
            "item_210x_unchanged": item_210x_digest_before == item_210x_digest_after,
            "material_1806_digest_before": material_1806_digest_before,
            "material_1806_digest_after": material_1806_digest_after,
            "material_1806_unchanged": material_1806_digest_before == material_1806_digest_after,
            "payload_size_unchanged": out.container.payload_size == payload_size_before,
            "record_count_unchanged": len(out.records) == record_count_before,
            "wrapper_header_unchanged": dict(out.container.header) == header_before,
            "all_selected_stacks_equal_target": True,
            "all_selected_instance_ids_unique": True,
        },
    }

    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    args = parse_args()
    report = build_save(args)
    summary = {
        "output": report["output"]["path"],
        "audit": str(Path(args.audit).resolve()),
        "active_hash_ok": report["validation"]["active_hash_ok"],
        "selected_stacks": report["counts"]["selected_stacks"],
        "feature_version_5_stacks": report["counts"]["selected_feature_version_5"],
        "activated_full_records": report["counts"]["activated_full_records"],
        "reused_empty_templates": report["counts"]["reused_pristine_empty_templates"],
        "main_story_unchanged": report["validation"]["main_story_unchanged"],
        "item_210x_unchanged": report["validation"]["item_210x_unchanged"],
        "input_unchanged": report["validation"]["input_unchanged"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
