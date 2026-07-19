"""Generate reproducible Relink 2.0 catalogs from extracted SQLite tables."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from gbfr_hash import gbfr_hash_hex


KNOWN_CHARACTER_NAMES = {
    "PL0000": "Gran",
    "PL0100": "Djeeta",
    "PL0200": "Katalina",
    "PL0300": "Rackam",
    "PL0400": "Io",
    "PL0500": "Eugen",
    "PL0600": "Rosetta",
    "PL0700": "Ferry",
    "PL0800": "Lancelot",
    "PL0900": "Vane",
    "PL1000": "Percival",
    "PL1100": "Siegfried",
    "PL1200": "Charlotta",
    "PL1300": "Yodarha",
    "PL1400": "Narmaya",
    "PL1500": "Ghandagoza",
    "PL1600": "Zeta",
    "PL1700": "Vaseraga",
    "PL1800": "Cagliostro",
    "PL1900": "Id",
    "PL2100": "Sandalphon",
    "PL2200": "Seofon",
    "PL2300": "Tweyen",
    "PL2900": "Fediel",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def generate(game_db: Path, items_db: Path, output: Path) -> None:
    if not game_db.is_file() or not items_db.is_file():
        raise FileNotFoundError("Both --game-db and --items-db must point to extracted SQLite files.")
    output.mkdir(parents=True, exist_ok=True)

    game = sqlite3.connect(game_db)
    game.row_factory = sqlite3.Row
    items = sqlite3.connect(items_db)
    items.row_factory = sqlite3.Row

    character_rows = list(
        game.execute(
            """
            SELECT CharId, CharaName, UIOrder, MinorVersionFlag, Element, Gender, MaxLevelMaybe
            FROM chara
            WHERE CharId LIKE 'PL%' AND IsNPC = 0 AND MaxLevelMaybe = 100
            ORDER BY UIOrder, CharId
            """
        )
    )
    characters = []
    for row in character_rows:
        char_id = row["CharId"]
        characters.append(
            {
                "id": char_id,
                "hash": gbfr_hash_hex(char_id),
                "name": KNOWN_CHARACTER_NAMES.get(char_id),
                "localization_key": row["CharaName"],
                "ui_order": row["UIOrder"],
                "minor_version_flag": row["MinorVersionFlag"],
                "element": row["Element"],
                "gender": row["Gender"],
                "max_level": row["MaxLevelMaybe"],
            }
        )

    weapon_index: dict[str, list[sqlite3.Row]] = {}
    for row in game.execute(
        "SELECT Key, WeaponId, Name, CharaId, SortOrder, MaxUncap, MaxLevel, LastAwakeningLevel, MinFeatureVersion FROM weapon"
    ):
        weapon_index.setdefault(str(row["Key"]), []).append(row)
    weapons = []
    for character in characters:
        resolved = []
        for index in range(1, 10):
            weapon_id = f"WEP_{character['id']}_{index:02d}"
            hash_hex = gbfr_hash_hex(weapon_id)
            candidates = weapon_index.get(weapon_id, []) + weapon_index.get(hash_hex, [])
            base = next((row for row in candidates if row["LastAwakeningLevel"] == 0), None)
            if base is None and candidates:
                base = candidates[0]
            if base is not None:
                resolved.append((index, weapon_id, hash_hex, base))
        preferred = [entry for entry in resolved if entry[0] <= 6]
        selected = preferred if len(preferred) == 6 else resolved[:6]
        if len(selected) != 6:
            raise RuntimeError(f"Expected six base weapons for {character['id']}, found {len(selected)}.")
        for collection_slot, (index, weapon_id, hash_hex, base) in enumerate(selected, 1):
            weapons.append(
                {
                    "id": weapon_id,
                    "hash": hash_hex,
                    "character_id": character["id"],
                    "collection_slot": collection_slot,
                    "id_suffix": index,
                    "database_key": base["Key"],
                    "localization_key": base["Name"],
                    "database_match": True,
                    "max_uncap": base["MaxUncap"],
                    "max_level": base["MaxLevel"],
                    "min_feature_version": base["MinFeatureVersion"],
                }
            )

    sigils = []
    family_pattern = re.compile(r"^TXT_GEEN_(\d{3})_(\d{2})$")
    for row in game.execute(
        """
        SELECT Key, Name, Description, PlayerReq, SkillId1, SkillId2, Rarity,
               ItemTierId, CanGemMix, CanOnlyHoldOne, HideLevelNumber
        FROM gem
        WHERE Rarity = 5
        ORDER BY Name, Key
        """
    ):
        match = family_pattern.match(row["Name"] or "")
        if not match:
            continue
        family = int(match.group(1))
        if not (173 <= family <= 178 or 320 <= family <= 327):
            continue
        gbid = row["Name"].removeprefix("TXT_")
        sigils.append(
            {
                "gbid": gbid,
                "canonical_gbid_hash": gbfr_hash_hex(gbid),
                "database_key": row["Key"],
                "family": family,
                "variant": int(match.group(2)),
                "category": "character_exclusive" if row["PlayerReq"] else "2.0_generic",
                "player_requirement": row["PlayerReq"] or None,
                "localization_key": row["Name"],
                "description_key": row["Description"],
                "skill_id_1": row["SkillId1"] or None,
                "skill_id_2": row["SkillId2"] or None,
                "item_tier_id": row["ItemTierId"],
                "can_mix": bool(row["CanGemMix"]),
                "unique": bool(row["CanOnlyHoldOne"]),
                "hide_level": bool(row["HideLevelNumber"]),
            }
        )

    table_counts = {
        name: items.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for name in ("item", "item_consume", "item_important", "item_material_list")
    }
    source = {
        "game_db_sha256": file_sha256(game_db),
        "items_db_sha256": file_sha256(items_db),
        "game_table_counts": {
            name: game.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for name in ("chara", "chara_gem", "gem", "weapon", "weapon_status", "weapon_status_awake")
        },
        "items_table_counts": table_counts,
        "selection": {
            "characters": "PL*, IsNPC=0, MaxLevelMaybe=100",
            "weapons": "six database-backed base weapons per character; probe WEP_<character>_01..09",
            "sigils_2_0": "Rarity=5 and family 173..178 or 320..327",
        },
    }

    write_json(output / "characters.json", {"count": len(characters), "items": characters})
    write_json(output / "weapons.json", {"count": len(weapons), "items": weapons})
    write_json(output / "sigils-2.0.json", {"count": len(sigils), "items": sigils})
    write_json(output / "source-metadata.json", source)
    game.close()
    items.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-db", type=Path, required=True)
    parser.add_argument("--items-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("catalogs"))
    args = parser.parse_args()
    generate(args.game_db, args.items_db, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
