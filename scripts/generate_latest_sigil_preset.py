"""Generate the database-free Relink 2.0.2 endgame gold sigil preset."""

import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from gbfr_hash import gbfr_hash


EXPECTED_CHARACTER_COUNT = 29
OUTER_LEVEL = 15
TRAIT_LEVEL = 99
SIGIL_FLAGS = 3
FLIGHT_OVER_FIGHT_ID = "SKILL_159_00"
STUN_POWER_ID = "SKILL_004_00"
LINKED_TOGETHER_ID = "SKILL_009_00"

UNIVERSAL_CORE = (
    ("GEEN_320_24", "SKILL_321_00", "Celestial Nyx / Celestial Lumen"),
    ("GEEN_322_24", "SKILL_323_00", "Celestial Terra / Celestial Incendo"),
    ("GEEN_324_24", "SKILL_325_00", "Celestial Aqua / Fatebreaker"),
    ("GEEN_003_24", "SKILL_085_00", "Critical Hit Rate / Aegis"),
    ("GEEN_166_24", LINKED_TOGETHER_ID, "Greater Aegis / Linked Together"),
    ("GEEN_146_24", "SKILL_063_00", "War Elemental / Improved Dodge"),
    ("GEEN_160_04", "SKILL_020_00", "Alpha / Damage Cap"),
    ("GEEN_161_04", "SKILL_151_00", "Beta / Supplementary Damage"),
    ("GEEN_162_04", "SKILL_027_00", "Gamma / Tyranny"),
    ("GEEN_159_24", STUN_POWER_ID, "Flight over Fight / Stun Power"),
)
AWAKENING_SECONDARY = "SKILL_045_00"
WARPATH_SECONDARY = "SKILL_068_00"


def u32(value: int) -> int:
    return int(value) & 0xFFFFFFFF


def id_hash(value: str) -> int:
    return u32(gbfr_hash(value))


def reference_hash(value: str) -> int:
    if re.fullmatch(r"[0-9A-Fa-f]{8}", value or ""):
        return int(value, 16)
    if not value:
        raise RuntimeError("encountered an empty skill reference")
    return id_hash(value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_characters(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("items")
    if not isinstance(rows, list) or len(rows) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError("character catalog must contain exactly 29 rows")
    result = {str(row["id"]): row for row in rows}
    if len(result) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError("character catalog IDs are not unique")
    return result


def load_database(path: Path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        characters = connection.execute(
            """
            SELECT CharId, UIOrder
            FROM chara
            WHERE CharId LIKE 'PL%' AND MaxLevelMaybe = 100 AND IsNPC = 0
            ORDER BY UIOrder, CharId
            """
        ).fetchall()
        rows = connection.execute(
            """
            SELECT Key, Name, PlayerReq, SkillId1, SkillId2, Rarity,
                   CanOnlyHoldOne
            FROM gem
            """
        ).fetchall()
    finally:
        connection.close()
    if len(characters) != EXPECTED_CHARACTER_COUNT:
        raise RuntimeError("live database must contain exactly 29 playable characters")

    by_key = defaultdict(list)
    by_name = defaultdict(list)
    by_player = defaultdict(list)
    for row in rows:
        by_key[str(row["Key"] or "")].append(row)
        by_name[str(row["Name"] or "")].append(row)
        if row["PlayerReq"]:
            by_player[str(row["PlayerReq"])].append(row)
    return [str(row["CharId"]) for row in characters], by_key, by_name, by_player


def resolve_gem(gem_id: str, by_key, by_name):
    rows = by_key.get(gem_id, [])
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        raise RuntimeError(f"duplicate exact gem key {gem_id}")
    candidates = by_name.get(f"TXT_{gem_id}", [])
    expected_key = f"{id_hash(gem_id):08X}"
    canonical = [
        row
        for row in candidates
        if str(row["Key"] or "").upper() == expected_key
    ]
    if len(canonical) != 1:
        raise RuntimeError(
            f"expected one canonical row for {gem_id}, found {len(canonical)}"
        )
    return canonical[0]


def discover_special_ids(character_id: str, by_key, by_name, by_player):
    source_character = "PL0000" if character_id == "PL0100" else character_id
    rows = by_player.get(source_character, [])
    awakening_ids = sorted(
        str(row["Name"]).removeprefix("TXT_")
        for row in rows
        if row["Name"] and str(row["Name"]).endswith("_90")
    )
    warpath_ids = sorted(
        str(row["Name"]).removeprefix("TXT_")
        for row in rows
        if row["Name"] and str(row["Name"]).endswith("_93")
    )
    if len(awakening_ids) != 1 or len(warpath_ids) != 1:
        raise RuntimeError(f"could not resolve special sigils for {source_character}")
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
    return awakening_id, warpath_ids[0], source_character, avatar_fallback


def make_entry(slot: int, outer_id: str, secondary_id: str, label: str, by_key, by_name):
    row = resolve_gem(outer_id, by_key, by_name)
    primary_id = str(row["SkillId1"] or "")
    entry = {
        "slot": slot,
        "label": label,
        "outer_id": outer_id,
        "outer_hash": f"{id_hash(outer_id):08X}",
        "primary_id": primary_id,
        "primary_hash": f"{reference_hash(primary_id):08X}",
        "secondary_id": secondary_id,
        "secondary_hash": f"{reference_hash(secondary_id):08X}",
        "can_only_hold_one": bool(row["CanOnlyHoldOne"]),
    }
    return entry


def build_character(character_id: str, name: str, by_key, by_name, by_player) -> dict:
    entries = [
        make_entry(slot, outer_id, secondary_id, label, by_key, by_name)
        for slot, (outer_id, secondary_id, label) in enumerate(UNIVERSAL_CORE, 1)
    ]
    awakening_id, warpath_id, source, fallback = discover_special_ids(
        character_id, by_key, by_name, by_player
    )
    entries.extend(
        (
            make_entry(
                11,
                awakening_id,
                AWAKENING_SECONDARY,
                "Character Awakening / Guts",
                by_key,
                by_name,
            ),
            make_entry(
                12,
                warpath_id,
                WARPATH_SECONDARY,
                "Character Warpath / Autorevive",
                by_key,
                by_name,
            ),
        )
    )
    trait_hashes = [
        int(entry[key], 16)
        for entry in entries
        for key in ("primary_hash", "secondary_hash")
    ]
    if len(entries) != 12 or len(set(trait_hashes)) != 24:
        raise RuntimeError(f"{character_id} does not resolve to 12/24 unique traits")
    if trait_hashes.count(reference_hash(FLIGHT_OVER_FIGHT_ID)) != 1:
        raise RuntimeError(f"{character_id} must contain one Flight over Fight")
    for required_id in (STUN_POWER_ID, LINKED_TOGETHER_ID):
        if trait_hashes.count(reference_hash(required_id)) != 1:
            raise RuntimeError(f"{character_id} must contain one {required_id}")
    return {
        "id": character_id,
        "name": name,
        "source_character": source,
        "captain_avatar_one_only_fallback": fallback,
        "sigils": entries,
    }


def build_digest(characters: list[dict]) -> str:
    payload = []
    for character in sorted(characters, key=lambda row: row["id"]):
        payload.append(
            {
                "character_id": character["id"],
                "sigils": [
                    {
                        "outer_id": entry["outer_id"],
                        "outer_hash": int(entry["outer_hash"], 16),
                        "primary_hash": int(entry["primary_hash"], 16),
                        "secondary_hash": int(entry["secondary_hash"], 16),
                        "level": TRAIT_LEVEL,
                    }
                    for entry in character["sigils"]
                ],
            }
        )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest().upper()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--characters", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = args.database.resolve()
    character_path = args.characters.resolve()
    output = args.output.resolve()
    if not database.is_file() or not character_path.is_file():
        raise FileNotFoundError("database or character catalog is missing")
    catalog = load_characters(character_path)
    order, by_key, by_name, by_player = load_database(database)
    if set(order) != set(catalog):
        raise RuntimeError("database and character catalog playable sets differ")
    characters = [
        build_character(
            character_id,
            str(catalog[character_id].get("name") or ""),
            by_key,
            by_name,
            by_player,
        )
        for character_id in order
    ]
    payload = {
        "schema_version": 1,
        "id": "latest-endgame-gold-2.0.2",
        "name": "Latest Endgame Gold 99",
        "name_zh": "2.0.2 最新终盘老金（全角色）",
        "game_data_version": "Relink 2.0.2",
        "description": (
            "All 29 characters receive 12 equipped sigils and 24 unique "
            "level-99 traits, including Alpha, Beta, Gamma, Flight over Fight, "
            "Stun Power, Linked Together, Critical Hit Rate, Aegis, and Greater Aegis. "
            "Quick Cooldown, Cascade, Stout Heart, Potion Hoarder, Spartan Echo, "
            "and Berserker Echo are supplied by the companion weapon and summon "
            "presets."
        ),
        "outer_level": OUTER_LEVEL,
        "trait_level": TRAIT_LEVEL,
        "flags": SIGIL_FLAGS,
        "traits_per_character": 24,
        "flight_over_fight_per_character": 1,
        "stun_power_per_character": 1,
        "linked_together_per_character": 1,
        "build_sha256": build_digest(characters),
        "source": {
            "database_file": database.name,
            "database_sha256": sha256_file(database),
            "character_catalog": character_path.name,
            "method": "generated from the Relink 2.0.2 chara/gem tables",
        },
        "character_order": order,
        "characters": characters,
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
                "characters": len(characters),
                "build_sha256": payload["build_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
