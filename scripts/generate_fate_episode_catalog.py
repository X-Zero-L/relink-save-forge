"""Generate a strict Relink 2.0 Fate Episode catalog from extracted SQLite data."""

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from gbfr_hash import gbfr_hash_hex


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ROWS = 324
EXPECTED_FATE_ROWS = 319
EXPECTED_REMI_ROWS = 5
EXPECTED_CHARACTER_COUNT = 29
EXPECTED_EPISODES_PER_CHARACTER = 11
EXPECTED_MISSION_REFERENCES = 58
EXPECTED_UNIQUE_MISSIONS = 56
EXPECTED_REMI_KEYS = {
    "REMI_PL0200_00",
    "REMI_PL0300_00",
    "REMI_PL0400_00",
    "REMI_PL0500_00",
    "REMI_PL0600_00",
}
EXPECTED_SHARED_MISSIONS = {
    "00300000": {"FATE_PL0000_04", "FATE_PL0100_04"},
    "00301000": {"FATE_PL0000_08", "FATE_PL0100_08"},
}
EXPECTED_SPECIAL_MISSION_EPISODES = {
    "PL2800": {6, 10},
    "PL2900": {0, 10},
}
FATE_KEY_RE = re.compile(r"^(FATE|REMI)_(PL\d{4})_(\d{2})$")
HEX_UINT_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
REQUIRED_COLUMNS = {
    "Key",
    "CharaId",
    "ReqCharaId",
    "FormationSlotId",
    "ItemReward",
    "ReqQuestId",
    "MissionQuestId",
    "PartyUnlockStatus",
    "SortOrder",
    "ReqLevel",
    "Bool1",
    "UnlockByDefaultMaybe",
    "FinalFateMaybe",
    "Bool4",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def normalize_hex_uint(value: object, *, column: str, key: str) -> str:
    if isinstance(value, int):
        if not 0 <= value <= 0xFFFFFFFF:
            raise RuntimeError(f"{key} {column} is outside uint32: {value}")
        return f"{value:08X}"
    text = str(value or "").strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    if not HEX_UINT_RE.fullmatch(text):
        raise RuntimeError(f"{key} {column} is not an 8-digit hex uint: {value!r}")
    return text.upper()


def bool_column(row: sqlite3.Row, column: str, key: str) -> bool:
    value = int(row[column])
    if value not in (0, 1):
        raise RuntimeError(f"{key} {column} must be 0 or 1, found {value}")
    return bool(value)


def load_character_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError(f"{path} does not contain an items list")
    character_ids = {str(item["id"]) for item in items}
    if int(payload.get("count", -1)) != len(items) or len(character_ids) != len(items):
        raise RuntimeError(f"{path} has inconsistent or duplicate character rows")
    if len(character_ids) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_CHARACTER_COUNT} catalog characters, found {len(character_ids)}"
        )
    return character_ids


def read_rows(database: Path) -> tuple[list[dict], list[str]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'fate_episode'"
        ).fetchone()
        if table is None:
            raise RuntimeError(f"{database} has no fate_episode table")
        columns = [
            str(row["name"])
            for row in connection.execute('PRAGMA table_info("fate_episode")')
        ]
        missing = sorted(REQUIRED_COLUMNS - set(columns))
        if missing:
            raise RuntimeError(f"fate_episode lacks required columns: {', '.join(missing)}")
        rows = list(connection.execute("SELECT * FROM fate_episode"))
    finally:
        connection.close()

    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError(f"Expected {EXPECTED_ROWS} fate_episode rows, found {len(rows)}")

    items = []
    for row in rows:
        key = str(row["Key"] or "")
        match = FATE_KEY_RE.fullmatch(key)
        if match is None:
            raise RuntimeError(f"Unexpected fate_episode Key: {key!r}")
        kind, character_id, episode_text = match.groups()
        if str(row["CharaId"] or "") != character_id:
            raise RuntimeError(
                f"{key} CharaId mismatch: expected {character_id}, found {row['CharaId']!r}"
            )
        mission_quest_id = normalize_hex_uint(
            row["MissionQuestId"], column="MissionQuestId", key=key
        )
        required_quest_id = normalize_hex_uint(
            row["ReqQuestId"], column="ReqQuestId", key=key
        )
        items.append(
            {
                "key": key,
                "hash": gbfr_hash_hex(key),
                "kind": kind.lower(),
                "character_id": character_id,
                "episode_index": int(episode_text),
                "mission_quest_id": None if mission_quest_id == "00000000" else mission_quest_id,
                "required_quest_id": None if required_quest_id == "00000000" else required_quest_id,
                "required_character_id": str(row["ReqCharaId"] or "") or None,
                "formation_slot_id": str(row["FormationSlotId"] or "") or None,
                "item_reward_id": str(row["ItemReward"] or "") or None,
                "party_unlock_status": int(row["PartyUnlockStatus"]),
                "sort_order": int(row["SortOrder"]),
                "required_level": int(row["ReqLevel"]),
                "bool_1": bool_column(row, "Bool1", key),
                "unlock_by_default": bool_column(row, "UnlockByDefaultMaybe", key),
                "final_fate": bool_column(row, "FinalFateMaybe", key),
                "bool_4": bool_column(row, "Bool4", key),
            }
        )
    items.sort(
        key=lambda item: (
            0 if item["kind"] == "fate" else 1,
            item["character_id"],
            item["episode_index"],
            item["key"],
        )
    )
    return items, columns


def validate_items(items: list[dict], character_catalog_ids: set[str]) -> None:
    key_counts = Counter(item["key"] for item in items)
    hash_counts = Counter(item["hash"] for item in items)
    duplicate_keys = sorted(key for key, count in key_counts.items() if count != 1)
    duplicate_hashes = sorted(value for value, count in hash_counts.items() if count != 1)
    if duplicate_keys or duplicate_hashes:
        raise RuntimeError(
            f"Duplicate keys/hashes: keys={duplicate_keys}, hashes={duplicate_hashes}"
        )

    fate_items = [item for item in items if item["kind"] == "fate"]
    remi_items = [item for item in items if item["kind"] == "remi"]
    if len(fate_items) != EXPECTED_FATE_ROWS or len(remi_items) != EXPECTED_REMI_ROWS:
        raise RuntimeError(
            f"Expected {EXPECTED_FATE_ROWS} FATE + {EXPECTED_REMI_ROWS} REMI rows, "
            f"found {len(fate_items)} + {len(remi_items)}"
        )
    remi_keys = {item["key"] for item in remi_items}
    if remi_keys != EXPECTED_REMI_KEYS:
        raise RuntimeError(f"Unexpected REMI key set: {sorted(remi_keys)}")
    if any(item["mission_quest_id"] is not None for item in remi_items):
        raise RuntimeError("REMI rows must not reference MissionQuestId values")

    by_character: dict[str, list[dict]] = defaultdict(list)
    for item in fate_items:
        by_character[item["character_id"]].append(item)
    if set(by_character) != character_catalog_ids:
        raise RuntimeError(
            "Fate character IDs do not exactly match catalogs/characters.json: "
            f"missing={sorted(character_catalog_ids - set(by_character))}, "
            f"extra={sorted(set(by_character) - character_catalog_ids)}"
        )
    expected_indexes = set(range(EXPECTED_EPISODES_PER_CHARACTER))
    for character_id, rows in sorted(by_character.items()):
        indexes = {item["episode_index"] for item in rows}
        if len(rows) != EXPECTED_EPISODES_PER_CHARACTER or indexes != expected_indexes:
            raise RuntimeError(
                f"{character_id} must contain exactly episodes 00..10; "
                f"count={len(rows)}, indexes={sorted(indexes)}"
            )
        final_rows = [item for item in rows if item["final_fate"]]
        if len(final_rows) != 1:
            raise RuntimeError(f"{character_id} must have exactly one FinalFateMaybe row")
        mission_indexes = {
            item["episode_index"] for item in rows if item["mission_quest_id"] is not None
        }
        expected_mission_indexes = EXPECTED_SPECIAL_MISSION_EPISODES.get(
            character_id, {4, 8}
        )
        if mission_indexes != expected_mission_indexes:
            raise RuntimeError(
                f"{character_id} mission episodes must be {sorted(expected_mission_indexes)}, "
                f"found {sorted(mission_indexes)}"
            )

    mission_rows = [item for item in fate_items if item["mission_quest_id"] is not None]
    mission_groups: dict[str, set[str]] = defaultdict(set)
    for item in mission_rows:
        mission_groups[item["mission_quest_id"]].add(item["key"])
    if len(mission_rows) != EXPECTED_MISSION_REFERENCES:
        raise RuntimeError(
            f"Expected {EXPECTED_MISSION_REFERENCES} nonzero MissionQuestId references, "
            f"found {len(mission_rows)}"
        )
    if len(mission_groups) != EXPECTED_UNIQUE_MISSIONS:
        raise RuntimeError(
            f"Expected {EXPECTED_UNIQUE_MISSIONS} unique MissionQuestId values, "
            f"found {len(mission_groups)}"
        )
    shared = {mission_id: keys for mission_id, keys in mission_groups.items() if len(keys) > 1}
    if shared != EXPECTED_SHARED_MISSIONS:
        raise RuntimeError(
            "Unexpected shared MissionQuestId mappings: "
            f"{ {key: sorted(value) for key, value in sorted(shared.items())} }"
        )


def build_output(
    items: list[dict],
    columns: list[str],
    database: Path,
    source_table: Path | None,
) -> dict:
    fate_items = [item for item in items if item["kind"] == "fate"]
    remi_items = [item for item in items if item["kind"] == "remi"]
    by_character: dict[str, list[dict]] = defaultdict(list)
    mission_groups: dict[str, list[str]] = defaultdict(list)
    for item in fate_items:
        by_character[item["character_id"]].append(item)
        if item["mission_quest_id"] is not None:
            mission_groups[item["mission_quest_id"]].append(item["key"])

    characters = []
    for character_id, rows in sorted(by_character.items()):
        ordered = sorted(rows, key=lambda item: item["episode_index"])
        characters.append(
            {
                "character_id": character_id,
                "episode_count": len(ordered),
                "episode_keys": [item["key"] for item in ordered],
                "mission_episode_keys": [
                    item["key"] for item in ordered if item["mission_quest_id"] is not None
                ],
                "mission_quest_ids": [
                    item["mission_quest_id"]
                    for item in ordered
                    if item["mission_quest_id"] is not None
                ],
                "final_fate_key": next(item["key"] for item in ordered if item["final_fate"]),
            }
        )

    mission_quests = [
        {
            "mission_quest_id": mission_id,
            "value": int(mission_id, 16),
            "episode_keys": sorted(keys),
        }
        for mission_id, keys in sorted(mission_groups.items())
    ]
    source = {
        "database_file": database.name,
        "database_sha256": sha256_file(database),
        "table": "fate_episode",
        "table_columns": columns,
        "method": (
            "All Relink 2.0 fate_episode rows; textual Key values are converted with "
            "the GBFR custom XXHash32 implementation in scripts/gbfr_hash.py"
        ),
    }
    if source_table is not None:
        source["source_table_file"] = source_table.name
        source["source_table_sha256"] = sha256_file(source_table)

    return {
        "schema_version": 1,
        "source": source,
        "counts": {
            "rows": len(items),
            "fate_episodes": len(fate_items),
            "remi_rows": len(remi_items),
            "characters": len(characters),
            "episodes_per_character": EXPECTED_EPISODES_PER_CHARACTER,
            "nonzero_mission_references": sum(
                item["mission_quest_id"] is not None for item in fate_items
            ),
            "unique_mission_quest_ids": len(mission_quests),
            "shared_mission_quest_ids": sum(
                len(entry["episode_keys"]) > 1 for entry in mission_quests
            ),
        },
        "save_contract": {
            "fate_id_field": 3501,
            "fate_state_field": 3502,
            "completed_state": 30,
            "real_rows": EXPECTED_ROWS,
            "fate_rows_to_complete": EXPECTED_FATE_ROWS,
            "remi_rows_to_preserve": EXPECTED_REMI_ROWS,
            "placeholder_rows_to_preserve": 496,
            "total_rows": 820,
            "placeholder_hash": "887AE0B0",
            "placeholder_state": 5,
            "mission_id_field": 2560,
            "mission_status_field": 2561,
            "mission_vector_length": 100,
            "mission_nonzero_entries": EXPECTED_UNIQUE_MISSIONS,
            "mission_empty_entries": 44,
            "mission_minimum_clear_count": 1,
        },
        "characters": characters,
        "mission_quests": mission_quests,
        "remi": [
            {"key": item["key"], "hash": item["hash"], "character_id": item["character_id"]}
            for item in remi_items
        ],
        "count": len(items),
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--characters",
        type=Path,
        default=ROOT / "catalogs" / "characters.json",
    )
    parser.add_argument("--source-table", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "catalogs" / "fate-episodes-2.0.json",
    )
    args = parser.parse_args()

    database = args.database.resolve()
    characters = args.characters.resolve()
    source_table = args.source_table.resolve() if args.source_table else None
    if not database.is_file():
        raise FileNotFoundError(f"SQLite database not found: {database}")
    if not characters.is_file():
        raise FileNotFoundError(f"Character catalog not found: {characters}")
    if source_table is not None and not source_table.is_file():
        raise FileNotFoundError(f"Source table not found: {source_table}")

    character_catalog_ids = load_character_ids(characters)
    items, columns = read_rows(database)
    validate_items(items, character_catalog_ids)
    output = build_output(items, columns, database, source_table)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {len(items)} rows: {EXPECTED_FATE_ROWS} FATE + "
        f"{EXPECTED_REMI_ROWS} REMI, {EXPECTED_UNIQUE_MISSIONS} unique missions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
