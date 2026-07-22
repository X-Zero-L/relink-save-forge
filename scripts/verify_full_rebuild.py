"""Audit a full Relink 2.0 save rebuild without changing the save."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import equip_verified_summon_traits as verified_summons
import equip_verified_weapon_blessings as verified_blessings
from build_materials_complete import classify_item, load_database
from gbfr_hash import gbfr_hash
from save_editor_api import GBFRSaveData, add_editor_argument


ROOT = Path(__file__).resolve().parents[1]
EMPTY = 0x887AE0B0
MAIN_FIELDS = (2510, 2511, 2520, 2522)
UNLOCK_TICKET_IDS = tuple(f"ITEM_23_{index:04d}" for index in range(8))
CHARACTER_TRAIT_COUNT = 24
WEAPON_TRAIT_COUNT = 3
SUMMON_TRAIT_COUNT = 4
COMBINED_TRAIT_COUNT = CHARACTER_TRAIT_COUNT + WEAPON_TRAIT_COUNT + SUMMON_TRAIT_COUNT


def protected_digest(save: GBFRSaveData) -> str:
    rows = []
    for field_id in MAIN_FIELDS:
        for record in save.find(id_type=field_id):
            rows.append((field_id, record.unit_id, list(save.get_values(record))))
    rows.sort(key=lambda row: (row[0], row[1]))
    encoded = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def trait_unit(sigil_unit: int, slot: int) -> int:
    return 120_000_000 + (sigil_unit - 30_000) * 100 + slot


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def combined_trait_error(
    character_id: str,
    character_traits: list[int],
    weapon_traits: list[int],
    summon_traits: list[int],
) -> str | None:
    counts = tuple(map(len, (character_traits, weapon_traits, summon_traits)))
    expected = (CHARACTER_TRAIT_COUNT, WEAPON_TRAIT_COUNT, SUMMON_TRAIT_COUNT)
    if counts != expected:
        return (
            f"{character_id} combined trait counts are {counts}, expected {expected}"
        )
    combined = [value & 0xFFFFFFFF for value in character_traits + weapon_traits + summon_traits]
    invalid = sorted({value for value in combined if value in (0, EMPTY)})
    if invalid:
        return f"{character_id} combined traits contain empty hashes {invalid}"
    duplicates = sorted(
        value for value, count in Counter(combined).items() if count > 1
    )
    if duplicates or len(set(combined)) != COMBINED_TRAIT_COUNT:
        rendered = [f"{value:08X}" for value in duplicates]
        return f"{character_id} combined 31-trait build has duplicates {rendered}"
    return None


def weapon_relationship_snapshot(save: GBFRSaveData, character_path: Path) -> list[tuple]:
    characters = verified_blessings.map_character_units(
        save,
        verified_blessings.load_characters(character_path),
    )
    equipped = verified_blessings.resolve_equipped_weapons(
        save,
        characters,
        verified_blessings.collect_weapon_slots(save),
    )
    return [
        (
            row["id"],
            int(row["unit"]),
            int(row["weapon_slot"]),
            int(row["weapon_unit"]),
        )
        for row in equipped
    ]


def summon_preservation_checks(before: dict, after: dict) -> dict[str, bool]:
    before_instances = before.get("instances", {})
    after_instances = after.get("instances", {})
    shared_units = set(before_instances) == set(after_instances)
    relationship_fields = ("instance_id", "outer_hash")
    bonus_fields = ("bonus_hash", "bonus_level")
    return {
        "relationships": (
            before.get("equipped") == after.get("equipped")
            and shared_units
            and all(
                all(
                    before_instances[unit][field] == after_instances[unit][field]
                    for field in relationship_fields
                )
                for unit in before_instances
            )
        ),
        "bonus": (
            shared_units
            and all(
                all(
                    before_instances[unit][field] == after_instances[unit][field]
                    for field in bonus_fields
                )
                for unit in before_instances
            )
        ),
        "field_1460": (
            shared_units
            and all(
                before_instances[unit]["field_1460"]
                == after_instances[unit]["field_1460"]
                for unit in before_instances
            )
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("save", type=Path)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--items-db", type=Path, required=True)
    parser.add_argument(
        "--characters",
        type=Path,
        default=ROOT / "catalogs" / "characters.json",
    )
    parser.add_argument(
        "--weapons",
        type=Path,
        default=ROOT / "catalogs" / "weapons.json",
    )
    parser.add_argument(
        "--rebuild-catalog",
        type=Path,
        default=ROOT / "catalogs" / "weapon-rebuild-2.0.json",
    )
    parser.add_argument(
        "--fate-catalog",
        type=Path,
        default=ROOT / "catalogs" / "fate-episodes-2.0.json",
    )
    parser.add_argument(
        "--sigil-preset",
        type=Path,
        default=ROOT / "presets" / "sigils" / "latest-endgame-gold-2.0.2.json",
    )
    parser.add_argument(
        "--weapon-blessing-preset",
        type=Path,
        default=ROOT / "presets" / "weapons" / "endgame-qol-blessing-2.0.2.json",
    )
    parser.add_argument(
        "--summon-preset",
        type=Path,
        default=ROOT / "presets" / "summons" / "endgame-qol-passives-2.0.2.json",
    )
    parser.add_argument(
        "--loadout-baseline",
        type=Path,
        help=(
            "Pre-blessing/pre-summon save required for a successful verification of "
            "equipped weapon and summon relationships, summon bonus lanes, and field 1460"
        ),
    )
    parser.add_argument(
        "--stack-quantity",
        type=int,
        default=900,
        help="Expected ordinary stack quantity",
    )
    parser.add_argument(
        "--expected-steam-id",
        type=int,
        help="Optional SteamID64 header assertion",
    )
    parser.add_argument("--report", type=Path)
    add_editor_argument(parser)
    args = parser.parse_args()

    save = GBFRSaveData.open(args.save.resolve())
    baseline = GBFRSaveData.open(args.baseline.resolve())
    loadout_baseline = (
        GBFRSaveData.open(args.loadout_baseline.resolve())
        if args.loadout_baseline
        else None
    )
    errors: list[str] = []
    if save.check_active_hash() is not True:
        errors.append("active hash is invalid")
    header = save.container.header or {}
    if (
        args.expected_steam_id is not None
        and header.get("steam_id") != args.expected_steam_id
    ):
        errors.append("SteamID64 mismatch")
    main_story_preserved = protected_digest(save) == protected_digest(baseline)
    if not main_story_preserved:
        errors.append("protected main-story digest changed")
    if loadout_baseline is None:
        errors.append(
            "--loadout-baseline is required to prove loadout relationships and "
            "summon preserved fields"
        )
    else:
        if loadout_baseline.check_active_hash() is not True:
            errors.append("loadout relationship baseline active hash is invalid")
        if loadout_baseline.container.header != save.container.header:
            errors.append("loadout relationship baseline wrapper metadata differs")
        if protected_digest(loadout_baseline) != protected_digest(save):
            errors.append("protected main-story fields differ from loadout baseline")

    chars = read_json(args.characters.resolve())["items"]
    weapons = read_json(args.weapons.resolve())["items"]
    rebuild = read_json(args.rebuild_catalog.resolve())
    fate = read_json(args.fate_catalog.resolve())
    sigil_preset = read_json(args.sigil_preset.resolve())
    weapon_blessing_preset = None
    summon_preset = None
    try:
        weapon_blessing_preset = verified_blessings.load_preset(
            args.weapon_blessing_preset.resolve(),
            None,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"weapon blessing preset is invalid: {exc}")
    try:
        summon_preset = verified_summons.load_preset(
            args.summon_preset.resolve(),
            None,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"summon passive preset is invalid: {exc}")
    expected_sigils = {
        str(row.get("id") or ""): row.get("sigils")
        for row in sigil_preset.get("characters", [])
    }
    if (
        sigil_preset.get("schema_version") != 1
        or sigil_preset.get("trait_level") != 99
        or sigil_preset.get("outer_level") != 15
        or sigil_preset.get("flags") != 3
        or len(expected_sigils) != 29
    ):
        errors.append("latest endgame sigil preset schema/counts are invalid")
    if not 1 <= args.stack_quantity <= 999:
        errors.append("stack quantity target is outside 1-999")
    if rebuild.get("schema_version") != 3:
        errors.append("rebuild catalog is not schema_version 3")
    if rebuild.get("counts") != {
        "database_complete_rows": 162,
        "current_specs": 160,
        "alternate_runtime_specs": 2,
    }:
        errors.append("rebuild catalog DB/current/alternate counts are invalid")
    if int(rebuild["old_awakening_max_level"]) != 10:
        errors.append("rebuild catalog old awakening max is not 10")
    if int(rebuild["transcendence_max_level"]) != 7:
        errors.append("rebuild catalog transcension max is not 7")
    if set(rebuild.get("vector_derivation", {}).get("never_uses", [])) != {
        "max_skill_id",
        "global_final_skill",
    }:
        errors.append("rebuild catalog does not reject the disproved global-skill model")
    expected_fate_counts = {
        "rows": 324,
        "fate_episodes": 319,
        "remi_rows": 5,
        "characters": 29,
        "episodes_per_character": 11,
        "nonzero_mission_references": 58,
        "unique_mission_quest_ids": 56,
        "shared_mission_quest_ids": 2,
    }
    if fate.get("schema_version") != 1 or fate.get("counts") != expected_fate_counts:
        errors.append("Fate catalog schema/counts are invalid")
    fate_contract = fate.get("save_contract", {})
    expected_fate_contract = {
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
    if fate_contract != expected_fate_contract:
        errors.append("Fate catalog save contract is invalid")

    fate_items = fate.get("items", [])
    fate_rows = [row for row in fate_items if row.get("kind") == "fate"]
    remi_rows = [row for row in fate_items if row.get("kind") == "remi"]
    fate_hashes = {int(row["hash"], 16) for row in fate_rows}
    remi_hashes = {int(row["hash"], 16) for row in remi_rows}
    catalog_fate_hashes = fate_hashes | remi_hashes
    if (
        fate.get("count") != 324
        or len(fate_items) != 324
        or len(fate_rows) != 319
        or len(remi_rows) != 5
        or len(catalog_fate_hashes) != 324
    ):
        errors.append("Fate catalog item/hash coverage is invalid")

    fate_key_records = save.find(id_type=3501)
    fate_state_records = save.find(id_type=3502)
    baseline_fate_keys = baseline.find(id_type=3501)
    baseline_fate_states = baseline.find(id_type=3502)
    fate_complete = 0
    fate_real_rows = 0
    fate_placeholder_rows = 0
    remi_preserved = 0
    if (
        len(fate_key_records) != 820
        or len(fate_state_records) != 820
        or len(baseline_fate_keys) != 820
        or len(baseline_fate_states) != 820
    ):
        errors.append("expected 820 Fate key/state rows in save and baseline")
    else:
        save_keys_by_unit = {record.unit_id: record for record in fate_key_records}
        save_states_by_unit = {record.unit_id: record for record in fate_state_records}
        baseline_keys_by_unit = {record.unit_id: record for record in baseline_fate_keys}
        baseline_states_by_unit = {
            record.unit_id: record for record in baseline_fate_states
        }
        all_units = set(save_keys_by_unit)
        if (
            len(all_units) != 820
            or all_units != set(save_states_by_unit)
            or all_units != set(baseline_keys_by_unit)
            or all_units != set(baseline_states_by_unit)
        ):
            errors.append("Fate key/state unit sets are inconsistent")
        else:
            runtime_hashes = set()
            placeholder_hash = int(fate_contract["placeholder_hash"], 16)
            for unit_id in sorted(all_units):
                key_hash = int(
                    save.get_first_value(save_keys_by_unit[unit_id], 0)
                ) & 0xFFFFFFFF
                baseline_key_hash = int(
                    baseline.get_first_value(baseline_keys_by_unit[unit_id], 0)
                ) & 0xFFFFFFFF
                state = int(save.get_first_value(save_states_by_unit[unit_id], 0))
                baseline_state = int(
                    baseline.get_first_value(baseline_states_by_unit[unit_id], 0)
                )
                if key_hash != baseline_key_hash:
                    errors.append(f"Fate key field changed at unit {unit_id}")
                    break
                if key_hash == placeholder_hash:
                    fate_placeholder_rows += 1
                    if state != baseline_state or state != fate_contract["placeholder_state"]:
                        errors.append(f"placeholder Fate state changed at unit {unit_id}")
                    continue
                runtime_hashes.add(key_hash)
                fate_real_rows += 1
                if key_hash in fate_hashes:
                    if state == fate_contract["completed_state"]:
                        fate_complete += 1
                    else:
                        errors.append(
                            f"Fate unit {unit_id} remains at state {state}, expected 30"
                        )
                elif key_hash in remi_hashes:
                    remi_preserved += 1
                    if state != baseline_state:
                        errors.append(f"REMI state changed at unit {unit_id}")
                else:
                    errors.append(f"unknown Fate hash 0x{key_hash:08X} at unit {unit_id}")
            if runtime_hashes != catalog_fate_hashes:
                errors.append("save Fate hashes do not exactly match the 2.0 catalog")
            if fate_placeholder_rows != 496:
                errors.append(
                    f"expected 496 Fate placeholders, found {fate_placeholder_rows}"
                )

    mission_key_records = save.find(id_type=2560)
    mission_state_records = save.find(id_type=2561)
    baseline_mission_keys = baseline.find(id_type=2560)
    fate_missions_complete = 0
    fate_mission_entries = 0
    fate_empty_mission_entries = 0
    if (
        len(mission_key_records) != 1
        or len(mission_state_records) != 1
        or len(baseline_mission_keys) != 1
    ):
        errors.append("expected one 2560/2561 mission vector")
    else:
        mission_keys = [
            int(value) & 0xFFFFFFFF for value in save.get_values(mission_key_records[0])
        ]
        mission_states = [int(value) for value in save.get_values(mission_state_records[0])]
        baseline_mission_values = [
            int(value) & 0xFFFFFFFF
            for value in baseline.get_values(baseline_mission_keys[0])
        ]
        catalog_missions = {int(row["value"]) for row in fate.get("mission_quests", [])}
        if mission_keys != baseline_mission_values:
            errors.append("Fate mission ID vector 2560 changed")
        if len(mission_keys) != 100 or len(mission_states) != 100:
            errors.append("Fate mission vectors must contain 100 entries")
        else:
            nonzero_missions = {value for value in mission_keys if value}
            if nonzero_missions != catalog_missions or len(nonzero_missions) != 56:
                errors.append("Fate mission IDs do not exactly match the catalog")
            for mission, state in zip(mission_keys, mission_states):
                if mission:
                    fate_mission_entries += 1
                    if state >= fate_contract["mission_minimum_clear_count"]:
                        fate_missions_complete += 1
                    else:
                        errors.append(f"Fate mission {mission:08X} is incomplete")
                else:
                    fate_empty_mission_entries += 1
                    if state != 0:
                        errors.append("empty Fate mission entry has nonzero status")

    char_by_hash = {int(row["hash"], 16): row for row in chars}
    weapon_by_hash = {int(row["hash"], 16): row for row in weapons}
    rebuild_by_runtime = {
        int(row["runtime_hash"], 16): row
        for row in rebuild["items"]
        if not row["alternate_runtime_only"]
    }
    stage_hash_to_base = {}
    for row in rebuild["items"]:
        stage_hash_to_base[int(row["runtime_hash"], 16)] = int(row["base_hash"], 16)

    strongest_by_char = {}
    for row in weapons:
        current = strongest_by_char.get(row["character_id"])
        if current is None or row["collection_slot"] > current["collection_slot"]:
            strongest_by_char[row["character_id"]] = row

    char_groups = save.group_by_unit([1301, 1303, 1308, 1402, 1403])
    playable = {}
    for unit_id, fields in char_groups.items():
        if 1301 not in fields:
            continue
        char_hash = int(save.get_first_value(fields[1301], 0)) & 0xFFFFFFFF
        if char_hash in char_by_hash:
            playable[char_hash] = (unit_id, fields)
    if len(playable) != 29:
        errors.append(f"expected 29 playable character mappings, found {len(playable)}")

    weapon_fields = [
        2802,
        2803,
        2804,
        2805,
        2806,
        2807,
        2815,
        2816,
        2817,
        2818,
    ]
    weapon_groups = save.group_by_unit(weapon_fields)
    weapon_by_slot = {}
    official_seen = Counter()
    conceptual_by_unit = {}
    for unit_id, fields in weapon_groups.items():
        if 2803 not in fields or 2802 not in fields:
            continue
        item_hash = int(save.get_first_value(fields[2803], 0)) & 0xFFFFFFFF
        slot_id = int(save.get_first_value(fields[2802], 0))
        if item_hash not in (0, EMPTY) and slot_id == 0:
            errors.append(f"weapon unit {unit_id} is nonempty with slot id 0")
        if slot_id:
            if slot_id in weapon_by_slot:
                errors.append(f"duplicate weapon slot id {slot_id}")
            weapon_by_slot[slot_id] = (unit_id, fields, item_hash)
        conceptual_hash = (
            item_hash
            if item_hash in weapon_by_hash
            else stage_hash_to_base.get(item_hash)
        )
        if conceptual_hash is not None:
            conceptual_by_unit[unit_id] = conceptual_hash
            official_seen[conceptual_hash] += 1
    missing_weapons = sorted(set(weapon_by_hash) - set(official_seen))
    if missing_weapons:
        errors.append(f"missing {len(missing_weapons)} official weapon hashes")

    sigil_groups = save.group_by_unit([2702, 2703, 2704, 2706, 2707])
    sigil_by_slot = {}
    for unit_id, fields in sigil_groups.items():
        if 2702 in fields:
            slot_id = int(save.get_first_value(fields[2702], 0))
            if slot_id:
                sigil_by_slot[slot_id] = (unit_id, fields)
    traits = save.group_by_unit([1701, 1702])

    equipped_weapon_rows: list[dict] = []
    weapon_traits_by_character: dict[str, list[int]] = {}
    weapon_relationships_preserved: bool | None = None
    if weapon_blessing_preset is not None:
        try:
            mapped_characters = verified_blessings.map_character_units(
                save,
                verified_blessings.load_characters(args.characters.resolve()),
            )
            equipped_weapon_rows = verified_blessings.resolve_equipped_weapons(
                save,
                mapped_characters,
                verified_blessings.collect_weapon_slots(save),
            )
            verified_blessings.verify_preset(
                save,
                equipped_weapon_rows,
                weapon_blessing_preset,
            )
            for row in equipped_weapon_rows:
                actual_traits = []
                for trait in weapon_blessing_preset["resolved_traits"]:
                    trait_fields = traits.get(
                        verified_blessings.weapon_trait_unit(
                            row["weapon_unit"],
                            trait["lane"],
                        ),
                        {},
                    )
                    actual_traits.append(
                        int(save.get_first_value(trait_fields.get(1701), 0))
                        & 0xFFFFFFFF
                    )
                weapon_traits_by_character[row["id"]] = actual_traits
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(f"equipped weapon blessing verification failed: {exc}")

    summon_trait_hashes: list[int] = []
    equipped_summon_count = 0
    summon_preservation = {
        "relationships": None,
        "bonus": None,
        "field_1460": None,
    }
    if summon_preset is not None:
        try:
            equipped_summons = verified_summons.match_preset(
                verified_summons.equipped_instances(save),
                summon_preset,
            )
            equipped_summon_count = len(equipped_summons)
            verified_summons.verify_preset(save, equipped_summons)
            summon_trait_hashes = [
                int(
                    save.get_first_value(row["trait_bonus_record"], 0)
                )
                & 0xFFFFFFFF
                for row in equipped_summons
            ]
            if loadout_baseline is not None:
                baseline_summons = verified_summons.match_preset(
                    verified_summons.equipped_instances(loadout_baseline),
                    summon_preset,
                )
                before = verified_summons.relationship_snapshot(
                    loadout_baseline,
                    baseline_summons,
                )
                after = verified_summons.relationship_snapshot(save, equipped_summons)
                summon_preservation = summon_preservation_checks(before, after)
                if not summon_preservation["relationships"]:
                    errors.append("equipped summon relationships changed")
                if not summon_preservation["bonus"]:
                    errors.append("equipped summon bonus hashes or levels changed")
                if not summon_preservation["field_1460"]:
                    errors.append("equipped summon field 1460 changed")
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(f"equipped summon passive verification failed: {exc}")

    if loadout_baseline is not None:
        try:
            weapon_relationships_preserved = weapon_relationship_snapshot(
                save,
                args.characters.resolve(),
            ) == weapon_relationship_snapshot(
                loadout_baseline,
                args.characters.resolve(),
            )
            if not weapon_relationships_preserved:
                errors.append("equipped character-to-weapon relationships changed")
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            weapon_relationships_preserved = False
            errors.append(f"weapon relationship preservation check failed: {exc}")

    character_traits_by_id: dict[str, list[int]] = {}
    for char_hash, (unit_id, fields) in playable.items():
        char = char_by_hash[char_hash]
        level = int(save.get_first_value(fields.get(1308), 0))
        exp = int(save.get_first_value(fields.get(1303), 0))
        if level != 100 or exp < 8_400_000:
            errors.append(f"{char['id']} unit {unit_id} level/exp is {level}/{exp}")
        weapon_slot = int(save.get_first_value(fields.get(1402), 0))
        weapon_row = weapon_by_slot.get(weapon_slot)
        if weapon_row is None:
            errors.append(f"{char['id']} points to missing weapon slot {weapon_slot}")
        else:
            weapon_unit = weapon_row[0]
            expected = int(strongest_by_char[char["id"]]["hash"], 16)
            if conceptual_by_unit.get(weapon_unit) != expected:
                errors.append(
                    f"{char['id']} equipped conceptual weapon mismatch at unit {weapon_unit}"
                )
        loadout = list(save.get_values(fields[1403])) if 1403 in fields else []
        expected_build = expected_sigils.get(char["id"])
        if not isinstance(expected_build, list) or len(expected_build) != 12:
            errors.append(f"{char['id']} is missing from the latest sigil preset")
            continue
        if (
            len(loadout) < 12
            or any(not value for value in loadout[:12])
            or len(set(loadout[:12])) != 12
        ):
            errors.append(f"{char['id']} does not have 12 populated sigil slots")
            continue
        actual_character_traits = []
        for slot_index, slot_id in enumerate(loadout[:12]):
            expected_sigil = expected_build[slot_index]
            sigil_row = sigil_by_slot.get(int(slot_id))
            if sigil_row is None:
                errors.append(f"{char['id']} points to missing sigil slot {slot_id}")
                continue
            sigil_unit, sigil_fields = sigil_row
            owner = int(save.get_first_value(sigil_fields.get(2706), 0)) & 0xFFFFFFFF
            outer_hash = int(save.get_first_value(sigil_fields.get(2703), 0)) & 0xFFFFFFFF
            sigil_level = int(save.get_first_value(sigil_fields.get(2704), 0))
            flags = int(save.get_first_value(sigil_fields.get(2707), 0))
            expected_outer = int(str(expected_sigil.get("outer_hash") or "0"), 16)
            if (
                owner != char_hash
                or outer_hash != expected_outer
                or sigil_level != 15
                or flags != 3
            ):
                errors.append(
                    f"{char['id']} sigil unit {sigil_unit} outer/owner/level/flags mismatch"
                )
            expected_traits = (
                int(str(expected_sigil.get("primary_hash") or "0"), 16),
                int(str(expected_sigil.get("secondary_hash") or "0"), 16),
            )
            for trait_slot, expected_trait in enumerate(expected_traits):
                lane = traits.get(trait_unit(sigil_unit, trait_slot), {})
                trait_hash = int(save.get_first_value(lane.get(1701), 0)) & 0xFFFFFFFF
                trait_level = int(save.get_first_value(lane.get(1702), 0))
                actual_character_traits.append(trait_hash)
                if trait_hash != expected_trait or trait_level != 99:
                    errors.append(
                        f"{char['id']} sigil unit {sigil_unit} trait {trait_slot} invalid"
                    )
        character_traits_by_id[char["id"]] = actual_character_traits

    combined_trait_characters = 0
    for character in chars:
        character_id = character["id"]
        error = combined_trait_error(
            character_id,
            character_traits_by_id.get(character_id, []),
            weapon_traits_by_character.get(character_id, []),
            summon_trait_hashes,
        )
        if error:
            errors.append(error)
        else:
            combined_trait_characters += 1

    item_rows, important_hashes, _consume_keys, internal_hashes, _internal_rows = (
        load_database(args.items_db.resolve())
    )
    visible = [
        row
        for row in item_rows
        if classify_item(row, important_hashes, internal_hashes)[0]
    ]
    item_groups = save.group_by_unit([1801, 1802])
    counts = {}
    for fields in item_groups.values():
        if 1801 in fields and 1802 in fields:
            item_hash = int(save.get_first_value(fields[1801], 0)) & 0xFFFFFFFF
            counts[item_hash] = int(save.get_first_value(fields[1802], 0))
    missing_items = [
        (row["Key"], counts.get(int(row["_hash"]), -1))
        for row in visible
        if int(row["_hash"]) not in counts
    ]
    below_items = [
        (row["Key"], counts[int(row["_hash"])])
        for row in visible
        if int(row["_hash"]) in counts
        and counts[int(row["_hash"])] < args.stack_quantity
    ]
    above_items = [
        (row["Key"], counts[int(row["_hash"])])
        for row in visible
        if int(row["_hash"]) in counts
        and counts[int(row["_hash"])] > args.stack_quantity
    ]
    wrong_items = missing_items + below_items + above_items
    if wrong_items:
        errors.append(
            f"{len(wrong_items)} visible ordinary stacks do not equal {args.stack_quantity}"
        )
    ticket_rows = {
        item_id: counts.get(gbfr_hash(item_id) & 0xFFFFFFFF)
        for item_id in UNLOCK_TICKET_IDS
    }
    wrong_tickets = {
        item_id: value
        for item_id, value in ticket_rows.items()
        if value != args.stack_quantity
    }
    if wrong_tickets:
        errors.append(f"unlock tickets do not equal {args.stack_quantity}: {wrong_tickets}")

    transcendence_units = 0
    transcendence_runtime_types = set()
    transcendence_official_types = set()
    for unit_id, fields in weapon_groups.items():
        if 2803 not in fields:
            continue
        current_hash = int(save.get_first_value(fields[2803], 0)) & 0xFFFFFFFF
        spec = rebuild_by_runtime.get(current_hash)
        if spec is None:
            continue
        transcendence_units += 1
        transcendence_runtime_types.add(current_hash)
        transcendence_official_types.add(spec["official_id"])
        expected_skills = [int(skill["hash"], 16) for skill in spec["skill_vector"]]
        if len(expected_skills) != 5 or any(not skill["curve_id"] for skill in spec["skill_vector"]):
            errors.append(f"weapon unit {unit_id} catalog lacks a five-curve vector")
            continue
        if any(field_id not in fields for field_id in (2807, 2815, 2817, 2818)):
            errors.append(f"awakenable weapon unit {unit_id} lacks completion fields")
            continue
        expected_2807 = 10 if spec["old_awakening"] else 0
        if int(save.get_first_value(fields[2807], 0)) != expected_2807:
            errors.append(f"weapon unit {unit_id} has invalid legacy awakening level")
        if int(save.get_first_value(fields[2817], 0)) != 7:
            errors.append(f"weapon unit {unit_id} transcendence is incomplete")
        if not int(save.get_first_value(fields[2815], 0)) & 0x40:
            errors.append(f"weapon unit {unit_id} lacks transcendence flag 0x40")
        if list(save.get_values(fields[2818])) != expected_skills:
            errors.append(f"weapon unit {unit_id} per-weapon curve vector mismatch")
    if transcendence_units != 171:
        errors.append(f"expected 171 transcendence instances, found {transcendence_units}")
    if len(transcendence_runtime_types) != 159:
        errors.append(
            f"expected 159 present current transcendence types, found {len(transcendence_runtime_types)}"
        )
    missing_specs = set(rebuild_by_runtime) - transcendence_runtime_types
    missing_official = {rebuild_by_runtime[item]["official_id"] for item in missing_specs}
    if missing_official != {"WEP_PL0000_01"}:
        errors.append(f"unexpected missing transcendence specs: {sorted(missing_official)}")

    baseline_instances = baseline.group_by_unit([2101, 2102, 2103, 2104, 2105])
    current_instances = save.group_by_unit([2101, 2102, 2103, 2104, 2105])
    for unit_id, source in baseline_instances.items():
        target = current_instances.get(unit_id, {})
        if any(
            field_id not in target
            or list(baseline.get_values(source_record))
            != list(save.get_values(target[field_id]))
            for field_id, source_record in source.items()
        ):
            errors.append(f"wrightstone/item-instance baseline changed at unit {unit_id}")
            break

    report = {
        "active_hash_ok": save.check_active_hash() is True,
        "steam_id": header.get("steam_id"),
        "playable_characters": len(playable),
        "official_weapon_types": len(official_seen),
        "weapon_instances": len(weapon_by_slot),
        "visible_items_checked": len(visible),
        "visible_stacks_at_target": len(visible) - len(wrong_items),
        "visible_stacks_missing": len(missing_items),
        "visible_stacks_below_target": len(below_items),
        "visible_stacks_above_target": len(above_items),
        "stack_quantity_target": args.stack_quantity,
        "unlock_tickets": ticket_rows,
        "sigil_preset_build_sha256": sigil_preset.get("build_sha256"),
        "weapon_blessing_preset_id": (
            weapon_blessing_preset.get("id")
            if weapon_blessing_preset is not None
            else None
        ),
        "weapon_blessing_equipped_weapons": len(equipped_weapon_rows),
        "weapon_blessing_trait_lanes_at_99": sum(
            len(rows) for rows in weapon_traits_by_character.values()
        ),
        "summon_preset_id": summon_preset.get("id") if summon_preset else None,
        "equipped_summons": equipped_summon_count,
        "summon_passive_traits_at_15": len(summon_trait_hashes),
        "combined_unique_trait_count": COMBINED_TRAIT_COUNT,
        "combined_unique_trait_characters": combined_trait_characters,
        "main_story_preserved": main_story_preserved,
        "loadout_baseline_supplied": loadout_baseline is not None,
        "weapon_relationships_preserved": weapon_relationships_preserved,
        "summon_relationships_preserved": summon_preservation["relationships"],
        "summon_bonus_preserved": summon_preservation["bonus"],
        "summon_field_1460_preserved": summon_preservation["field_1460"],
        "transcendence_weapon_instances": transcendence_units,
        "transcendence_runtime_types": len(transcendence_runtime_types),
        "transcendence_official_types": len(transcendence_official_types),
        "old_awakening_max_level": 10,
        "transcendence_max_level": 7,
        "vector_model": "per-weapon WeaponSkillLevelRebuildId1..5 curves",
        "fate_real_rows": fate_real_rows,
        "fate_complete": fate_complete,
        "fate_remi_preserved": remi_preserved,
        "fate_placeholder_rows": fate_placeholder_rows,
        "fate_mission_entries": fate_mission_entries,
        "fate_missions_complete": fate_missions_complete,
        "fate_empty_mission_entries": fate_empty_mission_entries,
        "errors": errors,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        report_path = args.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
