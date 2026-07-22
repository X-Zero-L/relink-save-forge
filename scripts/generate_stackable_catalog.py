"""Generate the database-free Relink 2.0.2 ordinary stackable item catalog."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from build_materials_complete import (
    activation_state,
    classify_item,
    load_database,
)
from gbfr_hash import gbfr_hash
from save_editor_api import add_editor_argument


UNLOCK_TICKET_IDS = tuple(f"ITEM_23_{index:04d}" for index in range(8))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    add_editor_argument(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = args.database.resolve()
    output = args.output.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"item database does not exist: {database}")
    items, important_hashes, _, internal_hashes, internal_rows = load_database(database)
    exclusions = Counter()
    included = []
    ticket_hashes = {
        gbfr_hash(item_id) & 0xFFFFFFFF: item_id for item_id in UNLOCK_TICKET_IDS
    }
    for row in items:
        accepted, reason = classify_item(row, important_hashes, internal_hashes)
        if not accepted:
            exclusions[reason] += 1
            continue
        item_hash = int(row["_hash"]) & 0xFFFFFFFF
        included.append(
            {
                "key": str(row["Key"]),
                "hash": f"{item_hash:08X}",
                "name_key": str(row.get("ItemName") or ""),
                "category_id": int(row.get("ItemCategoryId") or 0),
                "activation_state": activation_state(row),
                "unlock_ticket": ticket_hashes.get(item_hash),
            }
        )
    included.sort(key=lambda row: row["key"])
    found_tickets = sorted(
        row["unlock_ticket"] for row in included if row["unlock_ticket"]
    )
    if found_tickets != list(UNLOCK_TICKET_IDS):
        raise RuntimeError("generated catalog does not contain all eight unlock tickets")
    if len({row["hash"] for row in included}) != len(included):
        raise RuntimeError("generated stackable catalog hashes are not unique")
    payload = {
        "schema_version": 1,
        "id": "ordinary-stackables-2.0.2",
        "game_data_version": "Relink 2.0.2",
        "description": (
            "Visible non-key materials and consumables safe for the 180x stack layout."
        ),
        "source": {
            "database_file": database.name,
            "database_sha256": sha256_file(database),
            "method": "item table filtered by visibility, category, key-item, and curated internal exclusions",
            "curated_internal_rows": len(internal_rows),
        },
        "count": len(included),
        "unlock_ticket_count": len(found_tickets),
        "excluded_reason_counts": dict(sorted(exclusions.items())),
        "items": included,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(
        json.dumps(
            {
                "output": str(output),
                "count": len(included),
                "unlock_ticket_count": len(found_tickets),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
