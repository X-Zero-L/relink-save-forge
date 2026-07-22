"""Generate the Relink 2.0 per-weapon transcendence catalog."""

import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from gbfr_hash import gbfr_hash_hex


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_LEVELS = set(range(1, 8))
EXPECTED_DATABASE_ROWS = 162
EXPECTED_CURRENT_ROWS = 160
EXPECTED_ALTERNATES = 2
OLD_AWAKENING_MAX = 10
HEX_HASH_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
SOURCE_SKILL_FIELDS = {
    1: "WeaponSkillId1",
    2: "WeaponSkillId2",
    3: "WeaponSkillId5ForAwakening",
    4: "WeaponSkillId6ForAwakening",
    5: "WeaponSkillId7ForAwakening",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def normalized_hash(value: str) -> str:
    value = str(value or "")
    if HEX_HASH_RE.fullmatch(value):
        return value.upper()
    if not value:
        raise RuntimeError("Cannot hash an empty identifier")
    return gbfr_hash_hex(value)


def choose_curve_skill(row: dict, slot: int, curves: dict[str, list[dict]]) -> dict:
    curve_id = str(row[f"WeaponSkillLevelRebuildId{slot}"] or "")
    candidates = curves.get(curve_id, [])
    if not candidates:
        raise RuntimeError(f"{row['Key']} slot {slot} lacks curve {curve_id!r}")

    source_skill = str(row.get(SOURCE_SKILL_FIELDS[slot]) or "")
    selected = None
    selection = "first_database_row"
    if source_skill:
        matches = [
            candidate
            for candidate in candidates
            if normalized_hash(candidate["Unk12"]) == normalized_hash(source_skill)
        ]
        if len(matches) == 1:
            selected = matches[0]
            selection = "matching_weapon_skill_id"
        elif not matches and len(candidates) == 1:
            selected = candidates[0]
            selection = "single_database_row"
        else:
            raise RuntimeError(
                f"{row['Key']} slot {slot} curve {curve_id} has {len(matches)} "
                f"matches for {source_skill!r}"
            )
    else:
        selected = candidates[0]
        if len(candidates) == 1:
            selection = "single_database_row"
    return {
        "slot": slot,
        "curve_id": curve_id,
        "source_weapon_skill_id": source_skill or None,
        "skill_id": str(selected["Unk12"]),
        "hash": normalized_hash(selected["Unk12"]),
        "selection": selection,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--weapon-catalog",
        type=Path,
        default=ROOT / "catalogs" / "weapons.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "catalogs" / "weapon-rebuild-2.0.json",
    )
    parser.add_argument(
        "--aliases",
        type=Path,
        default=ROOT / "catalogs" / "weapon-runtime-aliases.json",
    )
    args = parser.parse_args()

    database = args.database.resolve()
    official_rows = json.loads(args.weapon_catalog.read_text(encoding="utf-8"))["items"]
    official_by_hash = {row["hash"].upper(): row for row in official_rows}

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    rebuild_levels: dict[str, set[int]] = defaultdict(set)
    for row in connection.execute("SELECT Key, Level FROM weapon_status_rebuild"):
        rebuild_levels[str(row["Key"])].add(int(row["Level"]))
    curves: dict[str, list[dict]] = defaultdict(list)
    for row in connection.execute(
        "SELECT rowid, * FROM weapon_skill_level_rebuild ORDER BY rowid"
    ):
        curves[str(row["Unk13"])].append(dict(row))

    eligible = []
    for raw_row in connection.execute("SELECT * FROM weapon"):
        row = dict(raw_row)
        curve_ids = [
            str(row[f"WeaponSkillLevelRebuildId{slot}"] or "")
            for slot in range(1, 6)
        ]
        if rebuild_levels.get(str(row.get("WeaponStatusRebuildId") or "")) != EXPECTED_LEVELS:
            continue
        if not all(curve_id and curve_id in curves for curve_id in curve_ids):
            continue
        eligible.append(row)
    connection.close()

    if len(eligible) != EXPECTED_DATABASE_ROWS:
        raise RuntimeError(
            f"Expected {EXPECTED_DATABASE_ROWS} complete database rows, found {len(eligible)}"
        )

    items = []
    for row in sorted(eligible, key=lambda item: (str(item["Key"]), str(item.get("WeaponId2") or ""))):
        base_reference = str(row.get("WeaponId2") or row.get("WeaponId") or row["Key"])
        official = official_by_hash.get(normalized_hash(base_reference))
        if official is None:
            raise RuntimeError(f"{row['Key']} has no official base weapon {base_reference}")
        skill_vector = [choose_curve_skill(row, slot, curves) for slot in range(1, 6)]
        items.append(
            {
                "database_key": str(row["Key"]),
                "runtime_hash": normalized_hash(row["Key"]),
                "official_id": official["id"],
                "base_hash": official["hash"],
                "character_id": official["character_id"],
                "collection_slot": official["collection_slot"],
                "alternate_runtime_only": str(row["Key"]).endswith("_A0"),
                "old_awakening": int(row.get("LastAwakeningLevel") or 0) == OLD_AWAKENING_MAX,
                "expected_2807": OLD_AWAKENING_MAX
                if int(row.get("LastAwakeningLevel") or 0) == OLD_AWAKENING_MAX
                else 0,
                "weapon_status_rebuild_id": str(row["WeaponStatusRebuildId"]),
                "transcendence_levels": sorted(EXPECTED_LEVELS),
                "skill_vector": skill_vector,
            }
        )

    alternates = [item for item in items if item["alternate_runtime_only"]]
    current = [item for item in items if not item["alternate_runtime_only"]]
    if len(current) != EXPECTED_CURRENT_ROWS or len(alternates) != EXPECTED_ALTERNATES:
        raise RuntimeError(
            f"Expected {EXPECTED_CURRENT_ROWS}+{EXPECTED_ALTERNATES} current/alternate rows, "
            f"found {len(current)}+{len(alternates)}"
        )

    output = {
        "schema_version": 3,
        "source": {
            "database_sha256": sha256_file(database),
            "method": (
                "Complete weapon_status_rebuild Level 1..7 rows with all five "
                "WeaponSkillLevelRebuildId curves resolved through "
                "weapon_skill_level_rebuild.Unk13 -> Unk12"
            ),
        },
        "counts": {
            "database_complete_rows": len(items),
            "current_specs": len(current),
            "alternate_runtime_specs": len(alternates),
        },
        "alternate_runtime_keys": [item["database_key"] for item in alternates],
        "transcendence_max_level": 7,
        "old_awakening_max_level": OLD_AWAKENING_MAX,
        "vector_derivation": {
            "curve_columns": "WeaponSkillLevelRebuildId1..5",
            "curve_join": "weapon_skill_level_rebuild.Unk13",
            "selected_skill": "weapon_skill_level_rebuild.Unk12",
            "matching_policy": (
                "match the corresponding WeaponSkillId when present; otherwise use "
                "the deterministic first/single database row"
            ),
            "never_uses": ["max_skill_id", "global_final_skill"],
        },
        "count": len(items),
        "items": items,
    }
    aliases = {
        "schema_version": 3,
        "source": "Relink 2.0 complete database-curve weapon rows",
        "count": len(items),
        "items": [
            {
                "database_key": item["database_key"],
                "runtime_hash": item["runtime_hash"],
                "official_id": item["official_id"],
                "base_hash": item["base_hash"],
                "alternate_runtime_only": item["alternate_runtime_only"],
            }
            for item in items
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.aliases.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.aliases.write_text(json.dumps(aliases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(items)} database-curve rows ({len(current)} current + {len(alternates)} alternate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
