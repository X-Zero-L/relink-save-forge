"""Validate generated catalogs, presets, hash vectors, and publish hygiene."""

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from gbfr_hash import gbfr_hash_hex


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_FATE_COUNTS = {
    "rows": 324,
    "fate_episodes": 319,
    "remi_rows": 5,
    "characters": 29,
    "episodes_per_character": 11,
    "nonzero_mission_references": 58,
    "unique_mission_quest_ids": 56,
    "shared_mission_quest_ids": 2,
}
EXPECTED_FATE_CONTRACT = {
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
EXPECTED_SPECIAL_CHARACTERS = {
    "PL2800": {
        "mission_episode_keys": ["FATE_PL2800_06", "FATE_PL2800_10"],
        "mission_quest_ids": ["00300028", "00301028"],
        "final_fate_key": "FATE_PL2800_09",
    },
    "PL2900": {
        "mission_episode_keys": ["FATE_PL2900_00", "FATE_PL2900_10"],
        "mission_quest_ids": ["00300029", "00301029"],
        "final_fate_key": "FATE_PL2900_00",
    },
}
FATE_ITEM_FIELDS = {
    "key",
    "hash",
    "kind",
    "character_id",
    "episode_index",
    "mission_quest_id",
    "required_quest_id",
    "required_character_id",
    "formation_slot_id",
    "item_reward_id",
    "party_unlock_status",
    "sort_order",
    "required_level",
    "bool_1",
    "unlock_by_default",
    "final_fate",
    "bool_4",
}
FATE_KEY_PATTERN = re.compile(r"^(FATE|REMI)_(PL\d{4})_(\d{2})$")
HEX_UINT_PATTERN = re.compile(r"^[0-9A-F]{8}$")
VERIFIED_LATEST_BUILD_SHA256 = (
    "E4EA6510730639CFF8870B98107009A87A9C4C7AC15F6D79BDD5C10A18D7B118"
)
VERIFIED_WEAPON_PRESET_SHA256 = (
    "BD522C3F97FECF31275AFA65C31B9FA6ED46104B706C91D816ED2AEDCBE48840"
)
VERIFIED_SUMMON_PRESET_SHA256 = (
    "41FDBB2CAF0263C0534EC5F13A66DBC4BDBE9DB74AAD84DF1AB7D425AF114C5F"
)
VERIFIED_STANDARD_SIGIL_PRESETS = {
    "standard-endgame-output-2.0.2": {
        "file": "presets/sigils/standard-endgame-output-2.0.2.json",
        "file_sha256": "F4CDF09C1DB1A4337194B6B855BC51D77577DA3C8F2FABED674EBB7FF4978790",
        "build_sha256": "6994C5F95EE5AD6AC188A0138E7BFD66AE80E06B41F912038D5342394813F7D4",
    },
    "standard-endgame-qol-2.0.2": {
        "file": "presets/sigils/standard-endgame-qol-2.0.2.json",
        "file_sha256": "21C44DD33A62D2EEF830FF702B6B78E87EB67A6D82ED290BED552CAFB3BEAAAA",
        "build_sha256": "FA2D287AA04C1D0E41744E868DC62706A4C485AFD753EF879AE0E31AC31F18D9",
    },
}
VERIFIED_STANDARD_WEAPON_PRESETS = {
    "endgame-qol-blessing-standard-2.0.2": {
        "file": "presets/weapons/endgame-qol-blessing-standard-2.0.2.json",
        "file_sha256": "3D7ED4731076222C0646B204A78017F80D7AD646E4628BAF701EB29A4EAD763B",
        "traits": ("SKILL_069_00", "SKILL_070_00", "SKILL_044_00"),
    },
    "endgame-survival-blessing-standard-2.0.2": {
        "file": "presets/weapons/endgame-survival-blessing-standard-2.0.2.json",
        "file_sha256": "29EC397191280452B5E78EB1880C803313AC775B5FA9965AF4115EABE4BC2DBF",
        "traits": ("SKILL_070_00", "SKILL_106_00", "SKILL_166_00"),
    },
}
VERIFIED_PAIR_CATALOG_SHA256 = (
    "C7D2A7F4CCCAD1D1E2B4C2D97CC862670176BA6A5677CC004C3B7535064F01F2"
)
VERIFIED_CAP_CATALOG_SHA256 = (
    "243A8B3A72B2991F80C7623BA8B5276402D834893EE6EFDFF13D854987076648"
)
VERIFIED_TOP_SUMMON_CATALOG_SHA256 = (
    "4DDD6F3310DD6263A1488DC358D56BECBCDA691D1345082FED4C3AA06770A909"
)


def require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def require_keys(value: object, expected: set[str], label: str) -> dict:
    require(isinstance(value, dict), f"{label} must be a JSON object")
    actual = set(value)
    require(
        actual == expected,
        f"{label} fields differ: missing={sorted(expected - actual)}, "
        f"extra={sorted(actual - expected)}",
    )
    return value


def load(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def validate_fate_catalog(catalog: object, character_ids: set[str]) -> None:
    catalog = require_keys(
        catalog,
        {
            "schema_version",
            "source",
            "counts",
            "save_contract",
            "characters",
            "mission_quests",
            "remi",
            "count",
            "items",
        },
        "Fate catalog",
    )
    require(catalog["schema_version"] == 1, "Fate catalog schema_version must be 1")
    require(
        catalog["counts"] == EXPECTED_FATE_COUNTS,
        f"Fate catalog counts differ: {catalog['counts']!r}",
    )
    require(
        catalog["save_contract"] == EXPECTED_FATE_CONTRACT,
        f"Fate catalog save_contract differs: {catalog['save_contract']!r}",
    )

    source = require_keys(
        catalog["source"],
        {
            "database_file",
            "database_sha256",
            "table",
            "table_columns",
            "method",
            "source_table_file",
            "source_table_sha256",
        },
        "Fate catalog source",
    )
    require(source["database_file"] == "fate.sqlite", "Unexpected Fate SQLite filename")
    require(source["table"] == "fate_episode", "Unexpected Fate source table")
    require(
        source["database_sha256"]
        == "350AFCE0FB0A0C784EA3F4EF05B19426F6BA4CBCAFDCC5C18B765AC878B70143",
        "Unexpected Fate SQLite SHA256",
    )
    require(
        source["source_table_file"] == "fate_episode.tbl",
        "Unexpected Fate source-table filename",
    )
    require(
        source["source_table_sha256"]
        == "9B6AAF0748A19B5C51FE298F5C062DDEB987EA158423BF116A7E2B4201AA5EF6",
        "Unexpected fate_episode.tbl SHA256",
    )
    table_columns = source["table_columns"]
    require(isinstance(table_columns, list), "Fate source table_columns must be a list")
    require(len(table_columns) == len(set(table_columns)), "Fate source columns are duplicated")
    require(
        {
            "Key",
            "CharaId",
            "ReqCharaId",
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
        <= set(table_columns),
        "Fate source table_columns omit required fields",
    )

    items = catalog["items"]
    require(isinstance(items, list), "Fate catalog items must be a list")
    require(
        catalog["count"] == EXPECTED_FATE_COUNTS["rows"] == len(items),
        "Fate catalog count/items must both equal 324",
    )

    key_counts = Counter()
    hash_counts = Counter()
    fate_items = []
    remi_items = []
    fate_by_character = defaultdict(list)
    mission_groups = defaultdict(set)
    for index, raw_item in enumerate(items):
        item = require_keys(raw_item, FATE_ITEM_FIELDS, f"Fate item {index}")
        key = item["key"]
        hash_hex = item["hash"]
        require(isinstance(key, str), f"Fate item {index} key must be a string")
        match = FATE_KEY_PATTERN.fullmatch(key)
        require(match is not None, f"Invalid Fate/REMI key: {key!r}")
        prefix, character_id, episode_text = match.groups()
        expected_kind = prefix.lower()
        require(item["kind"] == expected_kind, f"{key} kind must be {expected_kind}")
        require(item["character_id"] == character_id, f"{key} character_id differs")
        require(item["episode_index"] == int(episode_text), f"{key} episode_index differs")
        require(character_id in character_ids, f"{key} references an unknown character")
        require(
            isinstance(hash_hex, str) and HEX_UINT_PATTERN.fullmatch(hash_hex),
            f"{key} hash must be uppercase 8-digit hex",
        )
        require(hash_hex == gbfr_hash_hex(key), f"{key} GBFR hash differs")
        require(
            all(isinstance(item[field], bool) for field in ("bool_1", "unlock_by_default", "final_fate", "bool_4")),
            f"{key} boolean fields have invalid types",
        )
        require(
            all(isinstance(item[field], int) and not isinstance(item[field], bool) for field in ("party_unlock_status", "sort_order", "required_level")),
            f"{key} numeric fields have invalid types",
        )
        for field in ("mission_quest_id", "required_quest_id"):
            value = item[field]
            require(
                value is None
                or (isinstance(value, str) and HEX_UINT_PATTERN.fullmatch(value) and value != "00000000"),
                f"{key} {field} must be null or nonzero uppercase 8-digit hex",
            )

        key_counts[key] += 1
        hash_counts[hash_hex] += 1
        if expected_kind == "fate":
            fate_items.append(item)
            fate_by_character[character_id].append(item)
            if item["mission_quest_id"] is not None:
                mission_groups[item["mission_quest_id"]].add(key)
        else:
            remi_items.append(item)
            require(item["mission_quest_id"] is None, f"{key} must not reference a mission")

    require(
        all(count == 1 for count in key_counts.values()),
        "Fate catalog contains duplicate keys",
    )
    require(
        all(count == 1 for count in hash_counts.values()),
        "Fate catalog contains duplicate GBFR hashes",
    )
    require(len(fate_items) == 319, "Fate catalog must contain 319 FATE rows")
    require(len(remi_items) == 5, "Fate catalog must contain 5 REMI rows")
    require(set(fate_by_character) == character_ids, "Fate character coverage differs")

    character_summaries = catalog["characters"]
    require(isinstance(character_summaries, list), "Fate characters must be a list")
    require(len(character_summaries) == 29, "Fate catalog must contain 29 character summaries")
    summaries_by_id = {}
    for index, raw_summary in enumerate(character_summaries):
        summary = require_keys(
            raw_summary,
            {
                "character_id",
                "episode_count",
                "episode_keys",
                "mission_episode_keys",
                "mission_quest_ids",
                "final_fate_key",
            },
            f"Fate character summary {index}",
        )
        character_id = summary["character_id"]
        require(character_id not in summaries_by_id, f"Duplicate Fate summary for {character_id}")
        summaries_by_id[character_id] = summary
    require(set(summaries_by_id) == character_ids, "Fate summary character coverage differs")

    for character_id in sorted(character_ids):
        rows = sorted(fate_by_character[character_id], key=lambda item: item["episode_index"])
        summary = summaries_by_id[character_id]
        expected_keys = [f"FATE_{character_id}_{episode:02d}" for episode in range(11)]
        actual_keys = [item["key"] for item in rows]
        require(len(rows) == 11, f"{character_id} must have exactly 11 Fate episodes")
        require(actual_keys == expected_keys, f"{character_id} must contain Fate episodes 00..10")
        require(summary["episode_count"] == 11, f"{character_id} summary count must be 11")
        require(summary["episode_keys"] == expected_keys, f"{character_id} summary keys differ")
        mission_rows = [item for item in rows if item["mission_quest_id"] is not None]
        expected_mission_indexes = {6, 10} if character_id == "PL2800" else {0, 10} if character_id == "PL2900" else {4, 8}
        require(
            {item["episode_index"] for item in mission_rows} == expected_mission_indexes,
            f"{character_id} mission Fate indexes differ",
        )
        require(
            summary["mission_episode_keys"] == [item["key"] for item in mission_rows],
            f"{character_id} mission episode summary differs",
        )
        require(
            summary["mission_quest_ids"] == [item["mission_quest_id"] for item in mission_rows],
            f"{character_id} mission ID summary differs",
        )
        final_rows = [item for item in rows if item["final_fate"]]
        require(len(final_rows) == 1, f"{character_id} must have exactly one final Fate row")
        require(
            summary["final_fate_key"] == final_rows[0]["key"],
            f"{character_id} final Fate summary differs",
        )

    for character_id, expected in EXPECTED_SPECIAL_CHARACTERS.items():
        summary = summaries_by_id[character_id]
        for field, expected_value in expected.items():
            require(
                summary[field] == expected_value,
                f"{character_id} special {field} differs",
            )

    mission_references = sum(len(keys) for keys in mission_groups.values())
    require(mission_references == 58, "Fate catalog must contain 58 mission references")
    require(len(mission_groups) == 56, "Fate catalog must contain 56 unique missions")
    shared_missions = {
        mission_id: keys for mission_id, keys in mission_groups.items() if len(keys) > 1
    }
    require(shared_missions == EXPECTED_SHARED_MISSIONS, "Shared Fate missions differ")

    mission_rows = catalog["mission_quests"]
    require(isinstance(mission_rows, list), "Fate mission_quests must be a list")
    require(len(mission_rows) == 56, "Fate mission_quests must contain 56 rows")
    mission_rows_by_id = {}
    for index, raw_row in enumerate(mission_rows):
        row = require_keys(
            raw_row,
            {"mission_quest_id", "value", "episode_keys"},
            f"Fate mission row {index}",
        )
        mission_id = row["mission_quest_id"]
        require(
            isinstance(mission_id, str) and HEX_UINT_PATTERN.fullmatch(mission_id),
            f"Fate mission row {index} has an invalid ID",
        )
        require(mission_id != "00000000", "Fate mission IDs must be nonzero")
        require(mission_id not in mission_rows_by_id, f"Duplicate Fate mission {mission_id}")
        require(
            row["value"] == int(mission_id, 16),
            f"Fate mission {mission_id} numeric value differs",
        )
        require(
            row["episode_keys"] == sorted(mission_groups.get(mission_id, set())),
            f"Fate mission {mission_id} episode links differ",
        )
        mission_rows_by_id[mission_id] = row
    require(set(mission_rows_by_id) == set(mission_groups), "Fate mission coverage differs")

    remi_summary = catalog["remi"]
    require(isinstance(remi_summary, list), "Fate remi summary must be a list")
    require(len(remi_summary) == 5, "Fate remi summary must contain 5 rows")
    expected_remi_summary = [
        {
            "key": item["key"],
            "hash": item["hash"],
            "character_id": item["character_id"],
        }
        for item in remi_items
    ]
    for index, row in enumerate(remi_summary):
        require_keys(row, {"key", "hash", "character_id"}, f"REMI summary {index}")
    require(remi_summary == expected_remi_summary, "REMI summary does not match REMI items")
    require(
        {item["key"] for item in remi_items} == EXPECTED_REMI_KEYS,
        "Unexpected REMI key set",
    )


def reference_hash_hex(value: str) -> str:
    text = str(value or "")
    return text.upper() if HEX_UINT_PATTERN.fullmatch(text) else gbfr_hash_hex(text)


def validate_latest_sigil_preset(
    preset: object,
    character_ids: set[str],
) -> dict[str, set[str]]:
    require(isinstance(preset, dict), "Latest sigil preset must be an object")
    require(preset.get("schema_version") == 1, "Latest sigil preset schema must be 1")
    require(preset.get("id") == "latest-endgame-gold-2.0.2", "Latest sigil preset ID differs")
    require(preset.get("outer_level") == 15, "Latest sigil outer level must be 15")
    require(preset.get("trait_level") == 99, "Latest sigil trait level must be 99")
    require(preset.get("flags") == 3, "Latest sigil flags must be 3")
    require(preset.get("traits_per_character") == 24, "Latest sigil trait count differs")
    require(
        preset.get("flight_over_fight_per_character") == 1,
        "Latest sigil Flight over Fight count differs",
    )
    require(preset.get("stun_power_per_character") == 1, "Latest sigil Stun Power count differs")
    require(
        preset.get("linked_together_per_character") == 1,
        "Latest sigil Linked Together count differs",
    )
    order = preset.get("character_order")
    rows = preset.get("characters")
    require(isinstance(order, list) and len(order) == 29, "Latest sigil order must contain 29 IDs")
    require(isinstance(rows, list) and len(rows) == 29, "Latest sigil rows must contain 29 characters")
    require(set(order) == character_ids, "Latest sigil order differs from character catalog")
    by_id = {str(row.get("id") or ""): row for row in rows}
    require(set(by_id) == character_ids, "Latest sigil character rows differ")
    one_only = defaultdict(list)
    digest_rows = []
    character_traits = {}
    required_primary_ids = {
        "SKILL_160_00",
        "SKILL_161_00",
        "SKILL_162_00",
        "SKILL_159_00",
    }
    for character_id in order:
        row = by_id[character_id]
        sigil_rows = row.get("sigils")
        require(isinstance(sigil_rows, list) and len(sigil_rows) == 12, f"{character_id} must have 12 sigils")
        require(
            [sigil.get("slot") for sigil in sigil_rows] == list(range(1, 13)),
            f"{character_id} sigil slots must be 1..12",
        )
        traits = []
        digest_sigils = []
        primary_ids = set()
        for sigil in sigil_rows:
            outer_id = str(sigil.get("outer_id") or "")
            primary_id = str(sigil.get("primary_id") or "")
            secondary_id = str(sigil.get("secondary_id") or "")
            require(outer_id.startswith("GEEN_"), f"{character_id} has invalid outer ID")
            require(
                str(sigil.get("outer_hash") or "").upper() == gbfr_hash_hex(outer_id),
                f"{character_id}/{outer_id} outer hash differs",
            )
            primary_hash = reference_hash_hex(primary_id)
            secondary_hash = reference_hash_hex(secondary_id)
            require(
                str(sigil.get("primary_hash") or "").upper() == primary_hash,
                f"{character_id}/{outer_id} primary hash differs",
            )
            require(
                str(sigil.get("secondary_hash") or "").upper() == secondary_hash,
                f"{character_id}/{outer_id} secondary hash differs",
            )
            traits.extend((primary_hash, secondary_hash))
            primary_ids.add(primary_id)
            if sigil.get("can_only_hold_one"):
                one_only[outer_id].append(character_id)
            digest_sigils.append(
                {
                    "outer_id": outer_id,
                    "outer_hash": int(sigil["outer_hash"], 16),
                    "primary_hash": int(primary_hash, 16),
                    "secondary_hash": int(secondary_hash, 16),
                    "level": 99,
                }
            )
        require(len(set(traits)) == 24, f"{character_id} traits are not unique")
        require(
            traits.count(gbfr_hash_hex("SKILL_159_00")) == 1,
            f"{character_id} must contain one Flight over Fight",
        )
        require(
            traits.count(gbfr_hash_hex("SKILL_004_00")) == 1,
            f"{character_id} must contain one Stun Power",
        )
        require(
            traits.count(gbfr_hash_hex("SKILL_009_00")) == 1,
            f"{character_id} must contain one Linked Together",
        )
        require(
            required_primary_ids <= primary_ids,
            f"{character_id} lacks Alpha/Beta/Gamma/Flight over Fight",
        )
        fixed_traits = {
            "0DE887A0",
            "A7726190",
            "9232DC17",
            "36E3848D",
            "A898E283",
            "D029FE08",
            gbfr_hash_hex("SKILL_003_00"),
            gbfr_hash_hex("SKILL_085_00"),
            gbfr_hash_hex("SKILL_166_00"),
            gbfr_hash_hex("SKILL_009_00"),
            gbfr_hash_hex("SKILL_146_00"),
            gbfr_hash_hex("SKILL_063_00"),
            gbfr_hash_hex("SKILL_160_00"),
            gbfr_hash_hex("SKILL_020_00"),
            gbfr_hash_hex("SKILL_161_00"),
            gbfr_hash_hex("SKILL_151_00"),
            gbfr_hash_hex("SKILL_162_00"),
            gbfr_hash_hex("SKILL_027_00"),
            gbfr_hash_hex("SKILL_159_00"),
            gbfr_hash_hex("SKILL_004_00"),
            gbfr_hash_hex("SKILL_045_00"),
            gbfr_hash_hex("SKILL_068_00"),
        }
        require(
            fixed_traits <= set(traits),
            f"{character_id} lacks a required integrated sigil trait",
        )
        character_traits[character_id] = set(traits)
        digest_rows.append({"character_id": character_id, "sigils": digest_sigils})
    duplicate_one_only = {
        outer_id: owners for outer_id, owners in one_only.items() if len(owners) > 1
    }
    require(not duplicate_one_only, f"CanOnlyHoldOne preset shells repeat: {duplicate_one_only}")
    digest_rows.sort(key=lambda row: row["character_id"])
    encoded = json.dumps(digest_rows, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest().upper()
    require(preset.get("build_sha256") == digest, "Latest sigil build digest differs")
    require(
        digest == VERIFIED_LATEST_BUILD_SHA256,
        "Latest sigil build is not the verified 2.0.2 build",
    )
    return character_traits


def validate_endgame_surface_presets(
    weapon: object,
    summon: object,
    character_traits: dict[str, set[str]],
) -> None:
    require(isinstance(weapon, dict), "Weapon blessing preset must be an object")
    require(weapon.get("schema_version") == 1, "Weapon blessing schema must be 1")
    require(
        weapon.get("id") == "endgame-qol-blessing-2.0.2",
        "Weapon blessing preset ID differs",
    )
    outer = weapon.get("outer")
    require(isinstance(outer, dict), "Weapon blessing outer must be an object")
    require(outer.get("id") == "ITEM_26_0131", "Weapon blessing outer ID differs")
    require(
        str(outer.get("hash") or "").upper() == gbfr_hash_hex("ITEM_26_0131"),
        "Weapon blessing outer hash differs",
    )
    weapon_rows = weapon.get("traits")
    require(
        isinstance(weapon_rows, list) and len(weapon_rows) == 3,
        "Weapon blessing must contain three traits",
    )
    expected_weapon = [
        ("SKILL_069_00", "Quick Cooldown"),
        ("SKILL_070_00", "Cascade"),
        ("SKILL_044_00", "Stout Heart"),
    ]
    weapon_hashes = []
    for lane, (row, expected) in enumerate(zip(weapon_rows, expected_weapon)):
        skill_id, name = expected
        require(row.get("lane") == lane, f"Weapon blessing lane {lane} differs")
        require(row.get("id") == skill_id, f"Weapon blessing lane {lane} ID differs")
        require(row.get("name") == name, f"Weapon blessing lane {lane} name differs")
        skill_hash = gbfr_hash_hex(skill_id)
        require(
            str(row.get("hash") or "").upper() == skill_hash,
            f"Weapon blessing lane {lane} hash differs",
        )
        require(row.get("level") == 99, f"Weapon blessing lane {lane} must be 99")
        weapon_hashes.append(skill_hash)
    require(
        sha256_file(ROOT / "presets" / "weapons" / "endgame-qol-blessing-2.0.2.json")
        == VERIFIED_WEAPON_PRESET_SHA256,
        "Weapon blessing preset file SHA differs",
    )

    require(isinstance(summon, dict), "Summon passive preset must be an object")
    require(summon.get("schema_version") == 1, "Summon passive schema must be 1")
    require(
        summon.get("id") == "endgame-qol-passives-2.0.2",
        "Summon passive preset ID differs",
    )
    summon_rows = summon.get("summons")
    require(
        isinstance(summon_rows, list) and len(summon_rows) == 4,
        "Summon passive preset must contain four rows",
    )
    expected_summons = {
        "Rolan": ("0F986ED9", "SKILL_072_00", "26428274", "8E4A0E03"),
        "Lilith": ("DFAB70B7", "SKILL_073_00", "05205336", "8E4A0E03"),
        "Beelzebub": ("A7EFF558", "SKILL_234_00", "BD452CC9", "63E658AB"),
        "Lucilius": ("6E5968FC", "SKILL_233_00", "CC0B0EF1", "63E658AB"),
    }
    summon_hashes = []
    seen = set()
    for row in summon_rows:
        name = str(row.get("name") or "")
        require(name in expected_summons and name not in seen, f"Unexpected summon {name}")
        seen.add(name)
        summon_hash, skill_id, skill_lot, curve = expected_summons[name]
        require(row.get("summon_hash") == summon_hash, f"{name} shell hash differs")
        require(row.get("trait_id") == skill_id, f"{name} trait ID differs")
        require(
            str(row.get("trait_hash") or "").upper() == gbfr_hash_hex(skill_id),
            f"{name} trait hash differs",
        )
        require(row.get("skill_lot") == skill_lot, f"{name} skill lot differs")
        require(row.get("summon_curve") == curve, f"{name} summon curve differs")
        require(row.get("trait_level") == 15, f"{name} trait level must be 15")
        require(row.get("preserve_bonus") is True, f"{name} must preserve its bonus")
        summon_hashes.append(gbfr_hash_hex(skill_id))
    require(seen == set(expected_summons), "Summon passive coverage differs")
    require(
        sha256_file(ROOT / "presets" / "summons" / "endgame-qol-passives-2.0.2.json")
        == VERIFIED_SUMMON_PRESET_SHA256,
        "Summon passive preset file SHA differs",
    )
    require(len(set(weapon_hashes + summon_hashes)) == 7, "Surface traits repeat")
    for character_id, sigil_hashes in character_traits.items():
        combined = [*sigil_hashes, *weapon_hashes, *summon_hashes]
        require(
            len(combined) == 31 and len(set(combined)) == 31,
            f"{character_id} integrated build does not contain 31 unique effects",
        )


def validate_stackable_catalog(catalog: object) -> None:
    require(isinstance(catalog, dict), "Stackable catalog must be an object")
    require(catalog.get("schema_version") == 1, "Stackable catalog schema must be 1")
    rows = catalog.get("items")
    require(isinstance(rows, list), "Stackable catalog items must be a list")
    require(catalog.get("count") == 329 == len(rows), "Stackable catalog must contain 329 rows")
    hashes = set()
    tickets = []
    for row in rows:
        key = str(row.get("key") or "")
        hash_text = str(row.get("hash") or "").upper()
        require(HEX_UINT_PATTERN.fullmatch(hash_text), f"Invalid stackable hash for {key}")
        expected = hash_text if HEX_UINT_PATTERN.fullmatch(key) else gbfr_hash_hex(key)
        require(hash_text == expected, f"Stackable hash differs for {key}")
        require(hash_text not in hashes, f"Duplicate stackable hash {hash_text}")
        hashes.add(hash_text)
        if row.get("unlock_ticket") is not None:
            tickets.append(str(row["unlock_ticket"]))
    require(
        sorted(tickets) == [f"ITEM_23_{index:04d}" for index in range(8)],
        "Stackable catalog unlock-ticket coverage differs",
    )


def validate_standard_sigil_presets(
    presets: list[object],
    character_ids: set[str],
) -> None:
    require(
        sha256_file(ROOT / "catalogs" / "sigil-legal-pairs-2.0.2.json")
        == VERIFIED_PAIR_CATALOG_SHA256,
        "Standard sigil legal-pair catalog SHA differs",
    )
    require(
        sha256_file(ROOT / "catalogs" / "skill-level-caps-2.0.2.json")
        == VERIFIED_CAP_CATALOG_SHA256,
        "Standard sigil skill-cap catalog SHA differs",
    )
    seen = set()
    for preset in presets:
        require(isinstance(preset, dict), "Standard sigil preset must be an object")
        preset_id = str(preset.get("id") or "")
        expected = VERIFIED_STANDARD_SIGIL_PRESETS.get(preset_id)
        require(expected is not None and preset_id not in seen, f"Unexpected standard sigil preset {preset_id}")
        seen.add(preset_id)
        require(preset.get("schema_version") == 2, f"{preset_id} schema must be 2")
        require(
            preset.get("legality_mode") == "database_rows_only",
            f"{preset_id} legality mode differs",
        )
        require(preset.get("outer_level") == 15, f"{preset_id} outer level differs")
        require(preset.get("lane_level_max") == 15, f"{preset_id} lane max differs")
        require(preset.get("build_sha256") == expected["build_sha256"], f"{preset_id} build SHA differs")
        require(
            sha256_file(ROOT / expected["file"]) == expected["file_sha256"],
            f"{preset_id} file SHA differs",
        )
        require(
            preset.get("pair_catalog")
            == {
                "file": "catalogs/sigil-legal-pairs-2.0.2.json",
                "sha256": VERIFIED_PAIR_CATALOG_SHA256,
            },
            f"{preset_id} pair-catalog reference differs",
        )
        require(
            preset.get("skill_cap_catalog")
            == {
                "file": "catalogs/skill-level-caps-2.0.2.json",
                "sha256": VERIFIED_CAP_CATALOG_SHA256,
            },
            f"{preset_id} cap-catalog reference differs",
        )
        order = preset.get("character_order")
        rows = preset.get("characters")
        require(isinstance(order, list) and set(order) == character_ids, f"{preset_id} character order differs")
        require(isinstance(rows, list) and len(rows) == 29, f"{preset_id} must contain 29 character rows")
        row_by_id = {str(row.get("id") or ""): row for row in rows}
        require(set(row_by_id) == character_ids, f"{preset_id} character coverage differs")
        for character_id in order:
            row = row_by_id[character_id]
            sigils = row.get("sigils")
            require(isinstance(sigils, list) and len(sigils) == 12, f"{preset_id}/{character_id} must contain 12 sigils")
            require(
                [sigil.get("slot") for sigil in sigils] == list(range(1, 13)),
                f"{preset_id}/{character_id} slot order differs",
            )
            if character_id == "PL0100":
                require(
                    row.get("source_character") == "PL0000"
                    and row.get("captain_avatar_one_only_fallback") is True,
                    f"{preset_id} Djeeta fallback differs",
                )
            for sigil in sigils:
                primary = sigil.get("primary")
                secondary = sigil.get("secondary")
                require(isinstance(primary, dict) and primary.get("level") == 15, f"{preset_id} primary lane is not 15")
                require(
                    secondary is None
                    or (isinstance(secondary, dict) and secondary.get("level") == 15),
                    f"{preset_id} secondary lane is not null/15",
                )
                require(99 not in (primary.get("level"), None if secondary is None else secondary.get("level")), f"{preset_id} contains a level-99 lane")
    require(seen == set(VERIFIED_STANDARD_SIGIL_PRESETS), "Standard sigil preset coverage differs")


def validate_standard_weapon_presets(presets: list[object]) -> None:
    seen = set()
    for preset in presets:
        require(isinstance(preset, dict), "Standard weapon preset must be an object")
        preset_id = str(preset.get("id") or "")
        expected = VERIFIED_STANDARD_WEAPON_PRESETS.get(preset_id)
        require(expected is not None and preset_id not in seen, f"Unexpected standard weapon preset {preset_id}")
        seen.add(preset_id)
        require(preset.get("schema_version") == 1, f"{preset_id} schema differs")
        require(
            sha256_file(ROOT / expected["file"]) == expected["file_sha256"],
            f"{preset_id} file SHA differs",
        )
        traits = preset.get("traits")
        require(isinstance(traits, list) and len(traits) == 3, f"{preset_id} traits differ")
        actual_ids = tuple(str(row.get("id") or "") for row in traits)
        require(actual_ids == expected["traits"], f"{preset_id} trait profile differs")
        for lane, row in enumerate(traits):
            require(row.get("lane") == lane, f"{preset_id} lane order differs")
            require(row.get("level") == 15, f"{preset_id} lane {lane} must be 15")
            require(
                str(row.get("hash") or "").upper() == gbfr_hash_hex(actual_ids[lane]),
                f"{preset_id} lane {lane} hash differs",
            )
    require(seen == set(VERIFIED_STANDARD_WEAPON_PRESETS), "Standard weapon preset coverage differs")


def validate_creation_catalogs(
    top_summons: object,
    weapon_template: object,
    weapon_identities: object,
) -> None:
    require(
        sha256_file(ROOT / "catalogs" / "top-summons-2.0.2.json")
        == VERIFIED_TOP_SUMMON_CATALOG_SHA256,
        "Top-summon catalog SHA differs",
    )
    require(isinstance(top_summons, dict), "Top-summon catalog must be an object")
    require(top_summons.get("schema_version") == 1, "Top-summon catalog schema differs")
    rows = top_summons.get("summons")
    expected_summons = [
        ("Rolan", "0F986ED9", "B5FF9FD3", "9245DFA4"),
        ("Lilith", "DFAB70B7", "24883AF3", "A3E537B1"),
        ("Beelzebub", "A7EFF558", "3D8153A1", "CE70C58A"),
        ("Lucilius", "6E5968FC", "EE85CD1F", "5A1D2C89"),
    ]
    require(isinstance(rows, list) and len(rows) == 4, "Top-summon catalog must contain four rows")
    for index, (row, expected) in enumerate(zip(rows, expected_summons)):
        actual = (
            row.get("name"),
            row.get("summon_hash"),
            row.get("trait_hash"),
            row.get("bonus_hash"),
        )
        require(row.get("order") == index and actual == expected, f"Top-summon row {index} differs")
        require(
            row.get("trait_level") == 15
            and row.get("bonus_level") == 9
            and row.get("state_1460") == 6,
            f"Top-summon row {index} levels/state differ",
        )

    require(isinstance(weapon_template, dict), "Weapon instance template must be an object")
    require(
        weapon_template.get("id") == "weapon-instance-template-2.0.2"
        and weapon_template.get("schema_version") == 1,
        "Weapon instance template metadata differs",
    )
    require(
        weapon_template.get("max_progression")
        == {"experience": 162540, "uncap": 6, "plus": 99, "transcendence": 7},
        "Weapon instance max progression differs",
    )
    require(isinstance(weapon_identities, dict), "Weapon identity catalog must be an object")
    require(
        weapon_identities.get("id") == "weapon-runtime-identities-2.0.2"
        and weapon_identities.get("official_weapon_count") == 174
        and weapon_identities.get("identity_count") == 371
        and len(weapon_identities.get("identities", {})) == 371,
        "Weapon runtime identity catalog counts differ",
    )


def validate_pack_manifests() -> None:
    expected_ids = {
        "complete-armory",
        "create-top-four-summons",
        "gold-complete",
        "latest-endgame-gold",
        "resources-900",
        "fate-episodes-all",
        "mainline-safe-endgame",
        "standard-complete-output",
        "standard-complete-qol",
        "standard-endgame-output",
        "standard-endgame-qol",
        "unlock-all-characters",
    }
    paths = sorted((ROOT / "presets" / "packs").glob("*.json"))
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    require({row.get("id") for row in manifests} == expected_ids, "Preset pack IDs differ")
    allowed_placeholders = {
        "{python}",
        "{input}",
        "{output}",
        "{audit}",
        "{root}",
        "{editor_root}",
        "{run_dir}",
        "{save_dir}",
    }
    for manifest in manifests:
        require(manifest.get("schema_version") == 1, "Preset pack schema must be 1")
        require(isinstance(manifest.get("name"), str) and manifest["name"], "Preset pack name is empty")
        require(
            isinstance(manifest.get("description"), str) and manifest["description"],
            "Preset pack description is empty",
        )
        steps = manifest.get("steps")
        require(isinstance(steps, list) and steps, "Preset pack steps must be nonempty")
        for step in steps:
            command = step.get("command")
            require(isinstance(command, list) and command, "Preset step command must be nonempty")
            require(step.get("kind", "transform") in {"transform", "verify"}, "Preset step kind differs")
            rendered = " ".join(str(token) for token in command)
            for placeholder in re.findall(r"\{[^{}]+\}", rendered):
                require(placeholder in allowed_placeholders, f"Unknown preset placeholder {placeholder}")
            for token in command:
                if not isinstance(token, str) or not token.startswith("{root}/"):
                    continue
                referenced = ROOT / token.removeprefix("{root}/")
                require(referenced.is_file(), f"Preset command file is missing: {token}")
            if step.get("kind", "transform") == "transform":
                require("{input}" in command and "{output}" in command, "Transform step lacks input/output")
            if "--expected-build-sha256" in command:
                index = command.index("--expected-build-sha256")
                preset_index = command.index("--preset")
                preset_token = command[preset_index + 1]
                preset_path = ROOT / preset_token.removeprefix("{root}/")
                preset = json.loads(preset_path.read_text(encoding="utf-8"))
                require(
                    index + 1 < len(command)
                    and command[index + 1] == preset.get("build_sha256"),
                    "Preset expected build digest differs",
                )
            if "--expected-preset-sha256" in command:
                hash_index = command.index("--expected-preset-sha256")
                preset_index = command.index("--preset")
                require(
                    hash_index + 1 < len(command) and preset_index + 1 < len(command),
                    "Preset SHA command arguments are incomplete",
                )
                preset_token = command[preset_index + 1]
                require(
                    isinstance(preset_token, str) and preset_token.startswith("{root}/"),
                    "Preset path must be rooted at {root}",
                )
                preset_path = ROOT / preset_token.removeprefix("{root}/")
                require(
                    command[hash_index + 1] == sha256_file(preset_path),
                    f"Preset expected SHA differs for {preset_token}",
                )
            if "--expected-catalog-sha256" in command:
                hash_index = command.index("--expected-catalog-sha256")
                catalog_index = command.index("--catalog")
                catalog_token = command[catalog_index + 1]
                require(
                    isinstance(catalog_token, str) and catalog_token.startswith("{root}/"),
                    "Catalog path must be rooted at {root}",
                )
                catalog_path = ROOT / catalog_token.removeprefix("{root}/")
                require(
                    command[hash_index + 1] == sha256_file(catalog_path),
                    f"Catalog expected SHA differs for {catalog_token}",
                )

    by_id = {manifest["id"]: manifest for manifest in manifests}
    expected_complete_steps = {
        "standard-complete-output": [
            "unlock-all-characters",
            "fate-episodes-all",
            "ensure-sigil-loadouts",
            "complete-armory",
            "create-top-four-summons",
            "standard-output-sigils",
            "standard-output-weapon-blessing",
        ],
        "standard-complete-qol": [
            "unlock-all-characters",
            "fate-episodes-all",
            "ensure-sigil-loadouts",
            "complete-armory",
            "create-top-four-summons",
            "standard-qol-sigils",
            "standard-qol-weapon-blessing",
        ],
        "gold-complete": [
            "unlock-all-characters",
            "fate-episodes-all",
            "ensure-sigil-loadouts",
            "complete-armory",
            "create-top-four-summons",
            "latest-endgame-gold",
            "gold99-weapon-blessing",
        ],
    }
    for pack_id, expected_steps in expected_complete_steps.items():
        actual_steps = [step["id"] for step in by_id[pack_id]["steps"]]
        require(actual_steps == expected_steps, f"{pack_id} step order differs")
    existing_inventory_pack_ids = (
        "standard-endgame-output",
        "standard-endgame-qol",
        "latest-endgame-gold",
        "mainline-safe-endgame",
    )
    for pack_id in existing_inventory_pack_ids:
        scripts = " ".join(
            token
            for step in by_id[pack_id]["steps"]
            for token in step["command"]
            if isinstance(token, str)
        )
        require(
            "ensure_all_weapons.py" not in scripts
            and "ensure_top_summons.py" not in scripts
            and "unlock_all_characters.py" not in scripts
            and "ensure_sigil_loadouts.py" not in scripts,
            f"{pack_id} must remain existing-inventory only",
        )
    for pack_id in ("standard-endgame-output", "standard-endgame-qol"):
        scripts = " ".join(
            token
            for step in by_id[pack_id]["steps"]
            for token in step["command"]
            if isinstance(token, str)
        )
        require(
            "latest-endgame-gold" not in scripts
            and "endgame-qol-blessing-2.0.2.json" not in scripts,
            f"{pack_id} references a level-99 surface",
        )

    for schema_name in (
        "pack-schema.json",
        "sigil-preset-schema.json",
        "sigil-preset-schema-v2.json",
        "weapon-blessing-preset-schema.json",
        "summon-passive-preset-schema.json",
    ):
        schema_path = ROOT / "presets" / schema_name
        require(schema_path.is_file(), f"Missing preset schema {schema_name}")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        require(
            schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema",
            f"Preset schema draft differs for {schema_name}",
        )


def main() -> int:
    characters = load("catalogs/characters.json")
    weapons = load("catalogs/weapons.json")
    rebuild = load("catalogs/weapon-rebuild-2.0.json")
    runtime_aliases = load("catalogs/weapon-runtime-aliases.json")
    source_metadata = load("catalogs/source-metadata.json")
    sigils = load("catalogs/sigils-2.0.json")
    fate = load("catalogs/fate-episodes-2.0.json")
    latest_preset = load("presets/sigils/latest-endgame-gold-2.0.2.json")
    standard_sigils = [
        load("presets/sigils/standard-endgame-output-2.0.2.json"),
        load("presets/sigils/standard-endgame-qol-2.0.2.json"),
    ]
    weapon_blessing_preset = load(
        "presets/weapons/endgame-qol-blessing-2.0.2.json"
    )
    standard_weapon_presets = [
        load("presets/weapons/endgame-qol-blessing-standard-2.0.2.json"),
        load("presets/weapons/endgame-survival-blessing-standard-2.0.2.json"),
    ]
    summon_passive_preset = load(
        "presets/summons/endgame-qol-passives-2.0.2.json"
    )
    top_summons = load("catalogs/top-summons-2.0.2.json")
    weapon_template = load("catalogs/weapon-instance-template-2.0.2.json")
    weapon_identities = load("catalogs/weapon-runtime-identities-2.0.2.json")
    stackables = load("catalogs/stackable-items-2.0.2.json")
    require(
        characters["count"] == 29 == len(characters["items"]),
        "Character catalog must contain 29 rows",
    )
    require(
        weapons["count"] == 174 == len(weapons["items"]),
        "Weapon catalog must contain 174 rows",
    )
    require(
        all(item["database_match"] for item in weapons["items"]),
        "Every weapon row must be database-backed",
    )
    require(
        sigils["count"] == 84 == len(sigils["items"]),
        "Sigil catalog must contain 84 rows",
    )
    require(
        {item["family"] for item in sigils["items"]}
        == set(range(173, 179)) | set(range(320, 328)),
        "Sigil family coverage differs",
    )
    require(gbfr_hash_hex("PL2900") == "74DD4C79", "PL2900 hash vector differs")
    require(
        gbfr_hash_hex("WEP_PL2900_06") == "CDB13688",
        "WEP_PL2900_06 hash vector differs",
    )
    require(
        gbfr_hash_hex("0123456789ABCDEF") == "DE3D71B4",
        "16-byte hash vector differs",
    )
    require(
        gbfr_hash_hex("WEP_PL0000_03_03") == "77AB0809",
        "WEP_PL0000_03_03 hash vector differs",
    )
    character_by_id = {item["id"]: item for item in characters["items"]}
    require(len(character_by_id) == 29, "Character catalog IDs must be unique")
    validate_fate_catalog(fate, set(character_by_id))
    character_traits = validate_latest_sigil_preset(
        latest_preset,
        set(character_by_id),
    )
    validate_endgame_surface_presets(
        weapon_blessing_preset,
        summon_passive_preset,
        character_traits,
    )
    validate_standard_sigil_presets(standard_sigils, set(character_by_id))
    validate_standard_weapon_presets(standard_weapon_presets)
    validate_creation_catalogs(top_summons, weapon_template, weapon_identities)
    validate_stackable_catalog(stackables)
    validate_pack_manifests()
    runtime_metadata = source_metadata.get("runtime_presets_2_0_2", {})
    latest_metadata = runtime_metadata.get("latest_endgame_gold", {})
    weapon_metadata = runtime_metadata.get("equipped_weapon_qol_blessing", {})
    summon_metadata = runtime_metadata.get("equipped_summon_qol_passives", {})
    stackable_metadata = runtime_metadata.get("ordinary_stackables", {})
    require(
        latest_metadata.get("file_sha256")
        == sha256_file(ROOT / "presets" / "sigils" / "latest-endgame-gold-2.0.2.json"),
        "Latest sigil preset source-metadata SHA differs",
    )
    require(
        latest_metadata.get("build_sha256") == latest_preset.get("build_sha256"),
        "Latest sigil preset source-metadata build digest differs",
    )
    require(
        weapon_metadata.get("file_sha256")
        == sha256_file(
            ROOT / "presets" / "weapons" / "endgame-qol-blessing-2.0.2.json"
        ),
        "Weapon blessing preset source-metadata SHA differs",
    )
    require(
        weapon_metadata.get("equipped_weapons") == 29
        and weapon_metadata.get("trait_lanes") == 87
        and weapon_metadata.get("trait_level") == 99,
        "Weapon blessing preset source-metadata counts differ",
    )
    require(
        summon_metadata.get("file_sha256")
        == sha256_file(
            ROOT / "presets" / "summons" / "endgame-qol-passives-2.0.2.json"
        ),
        "Summon passive preset source-metadata SHA differs",
    )
    require(
        summon_metadata.get("equipped_summons") == 4
        and summon_metadata.get("trait_level") == 15
        and summon_metadata.get("preserved_bonus_lanes") == 4
        and summon_metadata.get("preserved_field_1460") is True,
        "Summon passive preset source-metadata counts differ",
    )
    require(
        stackable_metadata.get("file_sha256")
        == sha256_file(ROOT / "catalogs" / "stackable-items-2.0.2.json"),
        "Stackable catalog source-metadata SHA differs",
    )
    require(
        stackable_metadata.get("items") == stackables.get("count") == 329,
        "Stackable catalog source-metadata count differs",
    )
    require(character_by_id["PL2900"]["name"] == "Fediel", "PL2900 must be Fediel")
    require(character_by_id["PL2800"]["name"] is None, "PL2800 name must remain unknown")
    sigil_rows_by_gbid = {}
    for item in sigils["items"]:
        sigil_rows_by_gbid.setdefault(item["gbid"], []).append(item)
    for gbid in ("GEEN_178_90", "GEEN_178_93"):
        require(gbid in sigil_rows_by_gbid, f"Missing sigil catalog row {gbid}")
        require(
            any(item["player_requirement"] == "PL2900" for item in sigil_rows_by_gbid[gbid]),
            f"{gbid} lacks the PL2900 requirement",
        )
    weapon_ids = {item["id"] for item in weapons["items"]}
    for character_id in ("PL2100", "PL2200", "PL2300"):
        require(
            f"WEP_{character_id}_05" not in weapon_ids,
            f"Unexpected legacy weapon WEP_{character_id}_05",
        )
        require(
            f"WEP_{character_id}_07" in weapon_ids,
            f"Missing verified weapon WEP_{character_id}_07",
        )

    require(rebuild["schema_version"] == 3, "Weapon rebuild schema_version must be 3")
    require(
        rebuild["count"] == 162 == len(rebuild["items"]),
        "Weapon rebuild catalog must contain 162 rows",
    )
    require(
        rebuild["counts"]
        == {
            "database_complete_rows": 162,
            "current_specs": 160,
            "alternate_runtime_specs": 2,
        },
        "Weapon rebuild counts differ",
    )
    require(rebuild["old_awakening_max_level"] == 10, "Old awakening max must be 10")
    require(rebuild["transcendence_max_level"] == 7, "Transcendence max must be 7")
    require(
        rebuild["alternate_runtime_keys"] == ["WEP_PL2800_A0", "WEP_PL2900_A0"],
        "Alternate runtime weapon keys differ",
    )
    require(
        rebuild["vector_derivation"]
        == {
            "curve_columns": "WeaponSkillLevelRebuildId1..5",
            "curve_join": "weapon_skill_level_rebuild.Unk13",
            "selected_skill": "weapon_skill_level_rebuild.Unk12",
            "matching_policy": (
                "match the corresponding WeaponSkillId when present; otherwise use "
                "the deterministic first/single database row"
            ),
            "never_uses": ["max_skill_id", "global_final_skill"],
        },
        "Weapon rebuild vector derivation differs",
    )
    require(
        source_metadata["weapon_transcendence_2_0"]["database_complete_rows"] == 162,
        "Source metadata rebuild row count differs",
    )
    require(
        source_metadata["weapon_transcendence_2_0"]["live_save_full_vector_checks"] == 370,
        "Source metadata live vector check count differs",
    )
    require(
        runtime_aliases["count"] == 162 == len(runtime_aliases["items"]),
        "Weapon runtime alias catalog must contain 162 rows",
    )
    weapon_by_id = {item["id"]: item for item in weapons["items"]}
    require(len(weapon_by_id) == 174, "Weapon catalog IDs must be unique")
    aliases_by_key = {item["database_key"]: item for item in runtime_aliases["items"]}
    require(len(aliases_by_key) == 162, "Weapon runtime alias keys must be unique")
    require(
        set(aliases_by_key) == {item["database_key"] for item in rebuild["items"]},
        "Weapon rebuild/runtime alias key coverage differs",
    )
    current = 0
    alternates = 0
    for item in rebuild["items"]:
        require(item["official_id"] in weapon_by_id, "Weapon rebuild official ID is unknown")
        require(
            item["base_hash"] == weapon_by_id[item["official_id"]]["hash"],
            f"{item['database_key']} base hash differs",
        )
        runtime_id = item["database_key"]
        expected_runtime_hash = (
            runtime_id.upper()
            if re.fullmatch(r"[0-9A-Fa-f]{8}", runtime_id)
            else gbfr_hash_hex(runtime_id)
        )
        require(item["runtime_hash"] == expected_runtime_hash, f"{runtime_id} runtime hash differs")
        require(
            item["transcendence_levels"] == list(range(1, 8)),
            f"{runtime_id} transcendence levels differ",
        )
        require(
            item["expected_2807"] == (10 if item["old_awakening"] else 0),
            f"{runtime_id} expected 2807 differs",
        )
        require(len(item["skill_vector"]) == 5, f"{runtime_id} must have five skill slots")
        require(
            [skill["slot"] for skill in item["skill_vector"]] == [1, 2, 3, 4, 5],
            f"{runtime_id} skill slot order differs",
        )
        require(
            all(skill["curve_id"] and skill["skill_id"] for skill in item["skill_vector"]),
            f"{runtime_id} has an empty skill curve",
        )
        require(
            all(
                skill["hash"]
                == (
                    skill["skill_id"].upper()
                    if re.fullmatch(r"[0-9A-Fa-f]{8}", skill["skill_id"])
                    else gbfr_hash_hex(skill["skill_id"])
                )
                for skill in item["skill_vector"]
            ),
            f"{runtime_id} skill hash vector differs",
        )
        alternates += int(item["alternate_runtime_only"])
        current += int(not item["alternate_runtime_only"])
        alias = aliases_by_key[runtime_id]
        require(
            alias
            == {
                "database_key": runtime_id,
                "runtime_hash": item["runtime_hash"],
                "official_id": item["official_id"],
                "base_hash": item["base_hash"],
                "alternate_runtime_only": item["alternate_runtime_only"],
            },
            f"{runtime_id} runtime alias differs",
        )
    require(current == 160, "Weapon rebuild current-spec count must be 160")
    require(alternates == 2, "Weapon rebuild alternate-spec count must be 2")

    structure = (ROOT / "docs/SAVE_STRUCTURE.md").read_text(encoding="utf-8")
    for required in (
        "1403[0..11]",
        "2702",
        "2706",
        "120000000",
        "1701",
        "1702",
        "1402",
        "2802",
        "2803",
        "2807",
        "2815",
        "2817",
        "2818",
    ):
        require(required in structure, f"SAVE_STRUCTURE.md lacks {required}")

    required_scripts = {
        "build_materials_complete.py",
        "build_all_sigils_strict.py",
        "build_all_weapons_verified.py",
        "complete_all_fate_episodes.py",
        "complete_all_weapon_awakenings.py",
        "complete_character_progression.py",
        "equip_latest_endgame_gold_sigils.py",
        "equip_standard_sigil_preset.py",
        "equip_verified_summon_traits.py",
        "equip_verified_weapon_blessings.py",
        "ensure_all_weapons.py",
        "ensure_sigil_loadouts.py",
        "ensure_top_summons.py",
        "generate_fate_episode_catalog.py",
        "generate_latest_sigil_preset.py",
        "generate_stackable_catalog.py",
        "generate_weapon_rebuild_catalog.py",
        "run_full_rebuild.py",
        "save_editor_api.py",
        "set_stackable_quantity.py",
        "verify_full_rebuild.py",
        "unlock_all_characters.py",
    }
    available_scripts = {path.name for path in (ROOT / "scripts").glob("*.py")}
    require(
        required_scripts <= available_scripts,
        f"Missing required scripts: {sorted(required_scripts - available_scripts)}",
    )
    fate_document_path = ROOT / "docs" / "FATE_EPISODES_2_0.md"
    require(fate_document_path.is_file(), "Missing docs/FATE_EPISODES_2_0.md")
    fate_document = fate_document_path.read_text(encoding="utf-8")
    for required in ("3501", "3502", "2560", "2561", "PL2800", "PL2900", "REMI"):
        require(required in fate_document, f"FATE_EPISODES_2_0.md lacks {required}")
    for path in (ROOT / "scripts").glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    forbidden_extensions = {".dat", ".sav", ".db", ".sqlite", ".exe", ".dll", ".zip"}
    ignored_directories = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "runtime",
        "dist",
        "local",
        "backups",
    }
    steam_id_pattern = re.compile(r"\b7656119\d{10}\b")
    absolute_path_pattern = re.compile(r"[A-Za-z]:\\" + "|/" + "Users/|/" + "home/")
    for path in ROOT.rglob("*"):
        if any(part in ignored_directories for part in path.parts) or not path.is_file():
            continue
        require(path.suffix.lower() not in forbidden_extensions, f"forbidden file: {path}")
        if path.suffix.lower() in {
            ".md",
            ".json",
            ".yaml",
            ".yml",
            ".py",
            ".txt",
            ".ps1",
            ".cmd",
        }:
            text = path.read_text(encoding="utf-8")
            require(not steam_id_pattern.search(text), f"SteamID-like value in {path}")
            require(not absolute_path_pattern.search(text), f"absolute path in {path}")
    print(
        "ok: 29 characters, 174 weapons, 162 DB-curve rebuild/runtime rows, "
        "84 Relink 2.0 sigil rows, 319 Fate episodes + 5 REMI, 56 Fate missions, "
        "gold99 plus two database-row Lv15 builds, 329 stackable items, "
        "12 one-click packs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
