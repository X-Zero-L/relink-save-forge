"""Set every ordinary stackable inventory item to one safe quantity.

This deliberately excludes story/key items, Fate-special inventory, wallet
shadow rows, internal placeholders, weapons, sigils, summons, and all 210x
item instances.  It operates on an offline save copy and writes a separate
output plus a machine-readable audit report.
"""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from build_materials_complete import (
    MAIN_STORY_FIELDS,
    classify_item,
    first_value,
    load_database,
    normalized_item_hash,
    record_snapshot,
    set_if_changed,
    snapshot_digest,
    validate_stack_layout,
)
from gbfr_hash import gbfr_hash
from save_editor_api import GBFRSaveData, add_editor_argument


UNLOCK_TICKET_IDS = tuple(f"ITEM_23_{index:04d}" for index in range(8))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline source save copy")
    parser.add_argument("output", type=Path, help="Separate offline output save")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--database", type=Path, help="Live item SQLite database")
    source.add_argument(
        "--catalog",
        type=Path,
        help="Bundled database-free ordinary stackable item catalog",
    )
    parser.add_argument("--quantity", type=int, default=900, help="Target stack quantity (1-999)")
    parser.add_argument("--audit", type=Path, required=True, help="JSON audit report")
    add_editor_argument(parser)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def full_snapshot(save: GBFRSaveData) -> dict[tuple[str, int, int], list]:
    return {
        (record.kind, record.id_type, record.unit_id): list(save.get_values(record))
        for record in save.records
    }


def changed_records(
    before: dict[tuple[str, int, int], list],
    after: dict[tuple[str, int, int], list],
) -> list[dict]:
    if set(before) != set(after):
        raise RuntimeError("save record identities changed")
    changes = []
    for key in sorted(before, key=lambda value: (value[1], value[2], value[0])):
        old_values = before[key]
        new_values = after[key]
        if old_values == new_values:
            continue
        if len(old_values) != len(new_values):
            raise RuntimeError(f"save record width changed for {key}")
        changes.append(
            {
                "kind": key[0],
                "field_id": key[1],
                "unit_id": key[2],
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


def load_stackable_catalog(path: Path) -> tuple[list[dict], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise RuntimeError("stackable catalog schema_version must be 1")
    rows = payload.get("items")
    if not isinstance(rows, list) or payload.get("count") != len(rows):
        raise RuntimeError("stackable catalog count/items are inconsistent")
    candidates = []
    hashes = set()
    ticket_ids = []
    for index, row in enumerate(rows):
        key = str(row.get("key") or "")
        hash_text = str(row.get("hash") or "").upper()
        try:
            item_hash = int(hash_text, 16) & 0xFFFFFFFF
        except ValueError as exc:
            raise RuntimeError(f"invalid stackable catalog hash at row {index}") from exc
        if not key or len(hash_text) != 8 or item_hash in hashes:
            raise RuntimeError(f"invalid or duplicate stackable catalog row {index}")
        if normalized_item_hash(key) != item_hash:
            raise RuntimeError(f"stackable catalog hash differs for {key}")
        hashes.add(item_hash)
        ticket = row.get("unlock_ticket")
        if ticket is not None:
            ticket_ids.append(str(ticket))
        candidates.append({"Key": key, "_hash": item_hash})
    if sorted(ticket_ids) != list(UNLOCK_TICKET_IDS):
        raise RuntimeError("stackable catalog must contain all eight unlock tickets")
    return candidates, payload


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    database_path = args.database.resolve() if args.database else None
    catalog_path = args.catalog.resolve() if args.catalog else None
    audit_path = args.audit.resolve()
    quantity = int(args.quantity)

    if not 1 <= quantity <= 999:
        raise RuntimeError("quantity must be between 1 and 999")
    source_path = database_path if database_path is not None else catalog_path
    if not input_path.is_file() or source_path is None or not source_path.is_file():
        raise FileNotFoundError("input save or item source does not exist")
    if len({input_path, output_path, audit_path}) != 3:
        raise RuntimeError("input, output, and audit paths must be distinct")
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("refusing to overwrite an existing output or audit")

    input_sha = sha256_file(input_path)
    save = GBFRSaveData.open(input_path)
    if save.check_active_hash() is not True:
        raise RuntimeError("input save active hash is invalid")

    stack_units, units_by_hash, _ = validate_stack_layout(save)
    if database_path is not None:
        items, important_hashes, _, internal_hashes, _ = load_database(database_path)
        candidates = []
        for row in items:
            included, _ = classify_item(row, important_hashes, internal_hashes)
            if included:
                candidates.append(row)
        item_source = {
            "kind": "database",
            "path": str(database_path),
            "sha256": sha256_file(database_path),
        }
    else:
        candidates, catalog_payload = load_stackable_catalog(catalog_path)
        item_source = {
            "kind": "catalog",
            "id": str(catalog_payload.get("id") or ""),
            "path": str(catalog_path),
            "sha256": sha256_file(catalog_path),
        }

    missing = [row["Key"] for row in candidates if int(row["_hash"]) not in units_by_hash]
    if missing:
        raise RuntimeError(f"stackable catalog items are missing from the save: {missing[:10]}")

    before = full_snapshot(save)
    story_before = snapshot_digest(record_snapshot(save, MAIN_STORY_FIELDS))
    item_210x_before = snapshot_digest(record_snapshot(save, range(2100, 2200)))
    target_units = set()
    quantity_before = Counter()
    changed_rows = []

    for row in candidates:
        item_hash = int(row["_hash"])
        unit_id = units_by_hash[item_hash][0]
        fields = stack_units[unit_id]
        if first_value(save, fields[1803]) == 0 or first_value(save, fields[1804]) <= 0:
            raise RuntimeError(f"stackable item {row['Key']} is not active")
        old_quantity = first_value(save, fields[1802])
        quantity_before[old_quantity] += 1
        target_units.add(unit_id)
        if set_if_changed(save, fields[1802], quantity):
            changed_rows.append(
                {
                    "key": row["Key"],
                    "hash": f"{item_hash:08X}",
                    "unit": unit_id,
                    "before": old_quantity,
                    "after": quantity,
                }
            )

    mutation_changes = changed_records(before, full_snapshot(save))
    unexpected = [
        row
        for row in mutation_changes
        if row["field_id"] != 1802
        or row["unit_id"] not in target_units
        or row["changed_indexes"] != [0]
    ]
    if unexpected:
        raise RuntimeError(f"unexpected in-memory changes: {unexpected[:3]}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save.save_as(output_path, update_hash=True)
    if sha256_file(input_path) != input_sha:
        raise RuntimeError("offline input save changed during the run")

    output = GBFRSaveData.open(output_path)
    if output.check_active_hash() is not True:
        raise RuntimeError("output save active hash is invalid")
    if output.container.header != save.container.header:
        raise RuntimeError("Steam/account wrapper metadata changed")
    if output.container.payload_size != save.container.payload_size:
        raise RuntimeError("save payload size changed")
    if len(output.records) != len(save.records):
        raise RuntimeError("save record count changed")
    if full_snapshot(output) != full_snapshot(save):
        raise RuntimeError("serialized records differ from the verified in-memory candidate")
    if snapshot_digest(record_snapshot(output, MAIN_STORY_FIELDS)) != story_before:
        raise RuntimeError("main-story fields changed")
    if snapshot_digest(record_snapshot(output, range(2100, 2200))) != item_210x_before:
        raise RuntimeError("210x item-instance records changed")

    out_stack_units, out_units_by_hash, _ = validate_stack_layout(output)
    final_quantities = Counter()
    ticket_rows = []
    ticket_hashes = {gbfr_hash(item_id) & 0xFFFFFFFF: item_id for item_id in UNLOCK_TICKET_IDS}
    for row in candidates:
        item_hash = int(row["_hash"])
        unit_id = out_units_by_hash[item_hash][0]
        value = first_value(output, out_stack_units[unit_id][1802])
        final_quantities[value] += 1
        if value != quantity:
            raise RuntimeError(f"{row['Key']} persisted quantity {value}, expected {quantity}")
        if item_hash in ticket_hashes:
            ticket_rows.append(
                {
                    "key": ticket_hashes[item_hash],
                    "hash": f"{item_hash:08X}",
                    "unit": unit_id,
                    "quantity": value,
                }
            )

    ticket_rows.sort(key=lambda row: row["key"])
    if [row["key"] for row in ticket_rows] != list(UNLOCK_TICKET_IDS):
        raise RuntimeError("not all eight unlock-ticket stacks were verified")

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
        },
        "policy": {
            "target_quantity": quantity,
            "ordinary_stackable_items_only": True,
            "story_key_fate_wallet_internal_items_excluded": True,
            "item_source": item_source,
        },
        "counts": {
            "selected_stacks": len(candidates),
            "changed_stacks": len(changed_rows),
            "unchanged_stacks": len(candidates) - len(changed_rows),
            "quantity_histogram_before": dict(sorted(quantity_before.items())),
            "quantity_histogram_after": dict(sorted(final_quantities.items())),
        },
        "unlock_tickets": ticket_rows,
        "changes": changed_rows,
        "validation": {
            "only_1802_target_units_changed": True,
            "all_selected_stacks_equal_target": True,
            "all_eight_unlock_tickets_equal_target": True,
            "main_story_unchanged": True,
            "item_210x_unchanged": True,
            "payload_size_unchanged": True,
            "record_count_unchanged": True,
            "active_hash_ok": True,
        },
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path),
                "audit": str(audit_path),
                "target_quantity": quantity,
                "selected_stacks": len(candidates),
                "changed_stacks": len(changed_rows),
                "unlock_tickets_verified": len(ticket_rows),
                "active_hash_ok": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
