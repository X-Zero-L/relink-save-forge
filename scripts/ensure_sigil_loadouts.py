"""Ensure all 29 playable characters have twelve resolvable sigil links."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from equip_legacy_gold_sigils import (
    EMPTY_HASH,
    EXPECTED_CHARACTER_COUNT,
    changed_records,
    collect_instances,
    first_value,
    full_snapshot,
    load_characters,
    map_character_units,
    protected_story_digest,
    required_record,
    sha256_file,
    u32,
)
from save_editor_api import GBFRSaveData, add_editor_argument


SIGILS_PER_CHARACTER = 12


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline source save copy")
    parser.add_argument("output", type=Path, help="Separate offline output save")
    parser.add_argument("--characters", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    add_editor_argument(parser)
    return parser.parse_args()


def owner(save: GBFRSaveData, instance: dict) -> int:
    return u32(first_value(save, instance["fields"][2706]))


def non_target_references(
    save: GBFRSaveData,
    character_units: set[int],
    slot_to_unit: dict[int, int],
) -> tuple[set[int], int]:
    referenced_units = set()
    record_count = 0
    for record in save.find(id_type=1403):
        if int(record.unit_id) in character_units:
            continue
        record_count += 1
        for slot_id in save.get_values(record):
            unit = slot_to_unit.get(int(slot_id))
            if unit is not None:
                referenced_units.add(unit)
    return referenced_units, record_count


def plan_loadouts(
    save: GBFRSaveData,
    characters: list[dict],
    instances: dict,
    slot_to_unit: dict[int, int],
) -> dict:
    playable_hashes = {row["hash"] for row in characters}
    character_units = {row["unit"] for row in characters}
    externally_referenced_units, non_target_loadout_records = non_target_references(
        save,
        character_units,
        slot_to_unit,
    )
    active_units = {
        unit
        for unit, instance in instances.items()
        if instance["slot_id"] > 0
    }
    selected: dict[str, list[int]] = {}
    selected_units = set()
    loadouts = {}
    tail_referenced_units = set()

    for character in characters:
        loadout = required_record(
            save,
            1403,
            character["unit"],
            kinds=("uint",),
        )
        values = list(save.get_values(loadout))
        if len(values) < SIGILS_PER_CHARACTER:
            raise RuntimeError(
                f"{character['id']} loadout has only {len(values)} slots"
            )
        loadouts[character["id"]] = (loadout, values)
        for slot_id in values[SIGILS_PER_CHARACTER:]:
            unit = slot_to_unit.get(int(slot_id))
            if unit is not None:
                tail_referenced_units.add(unit)

    for character in characters:
        character_hash = character["hash"]
        _, values = loadouts[character["id"]]
        preferred = []
        for slot_id in values[:SIGILS_PER_CHARACTER]:
            unit = slot_to_unit.get(int(slot_id))
            if (
                unit is None
                or unit not in active_units
                or unit in tail_referenced_units
                or unit in selected_units
                or unit in preferred
            ):
                continue
            current_owner = owner(save, instances[unit])
            if current_owner == character_hash or (
                current_owner in (0, EMPTY_HASH)
                and unit not in externally_referenced_units
            ):
                preferred.append(unit)
        owned = sorted(
            unit
            for unit in active_units
            if unit not in selected_units
            and unit not in preferred
            and unit not in externally_referenced_units
            and unit not in tail_referenced_units
            and owner(save, instances[unit]) == character_hash
        )
        chosen = (preferred + owned)[:SIGILS_PER_CHARACTER]
        selected[character["id"]] = chosen
        selected_units.update(chosen)

    available = sorted(
        unit
        for unit in active_units
        if unit not in selected_units
        and unit not in externally_referenced_units
        and unit not in tail_referenced_units
        and owner(save, instances[unit]) in (0, EMPTY_HASH)
    )
    available_index = 0
    for character in characters:
        chosen = selected[character["id"]]
        needed = SIGILS_PER_CHARACTER - len(chosen)
        if available_index + needed > len(available):
            raise RuntimeError(
                f"not enough unowned active sigil instances for {character['id']}: "
                f"need {needed}, remaining {len(available) - available_index}"
            )
        additions = available[available_index : available_index + needed]
        available_index += needed
        chosen.extend(additions)
        selected_units.update(additions)

    expected = EXPECTED_CHARACTER_COUNT * SIGILS_PER_CHARACTER
    if len(selected_units) != expected:
        raise RuntimeError(f"selected {len(selected_units)} sigils, expected {expected}")
    extra_playable_owner_units = {
        unit
        for unit in active_units
        if unit not in selected_units and owner(save, instances[unit]) in playable_hashes
    }
    return {
        "selected": selected,
        "selected_units": selected_units,
        "externally_referenced_units": externally_referenced_units,
        "tail_referenced_units": tail_referenced_units,
        "non_target_loadout_records": non_target_loadout_records,
        "extra_playable_owner_units": extra_playable_owner_units,
    }


def apply_plan(
    save: GBFRSaveData,
    characters: list[dict],
    instances: dict,
    plan: dict,
) -> list[dict]:
    rows = []
    for character in characters:
        units = plan["selected"][character["id"]]
        slot_ids = [instances[unit]["slot_id"] for unit in units]
        if len(set(slot_ids)) != SIGILS_PER_CHARACTER or any(
            slot_id <= 0 for slot_id in slot_ids
        ):
            raise RuntimeError(f"{character['id']} selected invalid sigil IDs")
        reassigned = 0
        for unit in units:
            record = instances[unit]["fields"][2706]
            if u32(first_value(save, record)) != character["hash"]:
                save.set_first_value(record, character["hash"])
                reassigned += 1
        loadout = required_record(
            save,
            1403,
            character["unit"],
            kinds=("uint",),
        )
        before = list(save.get_values(loadout))
        after = list(before)
        after[:SIGILS_PER_CHARACTER] = slot_ids
        if before != after:
            save.set_values(loadout, after)
        rows.append(
            {
                "character_id": character["id"],
                "name": character.get("name"),
                "unit": character["unit"],
                "loadout_before": before,
                "loadout_after": after,
                "selected_units": units,
                "selected_instance_ids": slot_ids,
                "owner_links_reassigned": reassigned,
            }
        )
    return rows


def verify_loadouts(
    save: GBFRSaveData,
    characters: list[dict],
    instances: dict,
    slot_to_unit: dict[int, int],
) -> None:
    globally_selected = set()
    all_tail_units = set()
    loadouts = {}
    for character in characters:
        loadout = required_record(
            save,
            1403,
            character["unit"],
            kinds=("uint",),
        )
        values = list(save.get_values(loadout))
        loadouts[character["id"]] = (loadout, values)
        all_tail_units.update(
            slot_to_unit[int(slot_id)]
            for slot_id in values[SIGILS_PER_CHARACTER:]
            if int(slot_id) in slot_to_unit
        )
    for character in characters:
        loadout, loadout_values = loadouts[character["id"]]
        slot_ids = [
            int(value)
            for value in loadout_values[:SIGILS_PER_CHARACTER]
        ]
        if len(set(slot_ids)) != SIGILS_PER_CHARACTER or any(
            slot_id <= 0 for slot_id in slot_ids
        ):
            raise RuntimeError(f"{character['id']} loadout is not twelve unique IDs")
        for slot_id in slot_ids:
            unit = slot_to_unit.get(slot_id)
            if unit is None:
                raise RuntimeError(
                    f"{character['id']} loadout references missing sigil {slot_id}"
                )
            if unit in globally_selected:
                raise RuntimeError(f"sigil unit {unit} is linked more than once")
            if unit in all_tail_units:
                raise RuntimeError(
                    f"{character['id']} sigil {slot_id} is also referenced by a target tail slot"
                )
            if owner(save, instances[unit]) != character["hash"]:
                raise RuntimeError(
                    f"{character['id']} sigil {slot_id} has the wrong owner"
                )
            globally_selected.add(unit)


def require_whitelisted_changes(
    changes: list[dict],
    characters: list[dict],
    plan: dict,
) -> None:
    character_units = {row["unit"] for row in characters}
    owner_units = plan["selected_units"]
    unexpected = []
    for change in changes:
        valid = change["changed_indexes"] == [0]
        if change["field_id"] == 2706:
            valid = valid and change["unit_id"] in owner_units
        elif change["field_id"] == 1403:
            valid = (
                change["unit_id"] in character_units
                and all(index < SIGILS_PER_CHARACTER for index in change["changed_indexes"])
            )
        else:
            valid = False
        if not valid:
            unexpected.append(change)
    if unexpected:
        raise RuntimeError(f"unexpected sigil relationship changes: {unexpected[:3]}")


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    character_path = args.characters.resolve()
    audit_path = args.audit.resolve()
    for path, label in ((input_path, "input save"), (character_path, "character catalog")):
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if len({input_path, output_path, audit_path}) != 3:
        raise RuntimeError("input, output, and audit paths must be distinct")
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("refusing to overwrite an output or audit")

    input_sha256 = sha256_file(input_path)
    save = GBFRSaveData.open(input_path)
    if save.check_active_hash() is not True:
        raise RuntimeError("input save active hash is invalid")
    header = dict(save.container.header)
    payload_size = save.container.payload_size
    record_count = len(save.records)
    story_digest = protected_story_digest(save)
    characters = map_character_units(save, load_characters(character_path))
    instances, slot_to_unit = collect_instances(save)

    before = full_snapshot(save)
    plan = plan_loadouts(save, characters, instances, slot_to_unit)
    rows = apply_plan(save, characters, instances, plan)
    after = full_snapshot(save)
    changes = changed_records(before, after)
    require_whitelisted_changes(changes, characters, plan)
    verify_loadouts(save, characters, instances, slot_to_unit)
    if protected_story_digest(save) != story_digest:
        raise RuntimeError("protected main-story fields changed in memory")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save.save_as(output_path, update_hash=True)
    if sha256_file(input_path) != input_sha256:
        raise RuntimeError("offline input save changed during the run")

    output = GBFRSaveData.open(output_path)
    if output.check_active_hash() is not True:
        raise RuntimeError("output save active hash is invalid")
    if output.container.header != header:
        raise RuntimeError("Steam/account wrapper metadata changed")
    if output.container.payload_size != payload_size or len(output.records) != record_count:
        raise RuntimeError("save payload size or record count changed")
    if full_snapshot(output) != after:
        raise RuntimeError("serialized records differ from verified in-memory data")
    if protected_story_digest(output) != story_digest:
        raise RuntimeError("protected main-story fields changed on disk")
    output_characters = map_character_units(output, load_characters(character_path))
    output_instances, output_slot_to_unit = collect_instances(output)
    verify_loadouts(output, output_characters, output_instances, output_slot_to_unit)

    idempotent_before = full_snapshot(output)
    second_plan = plan_loadouts(
        output,
        output_characters,
        output_instances,
        output_slot_to_unit,
    )
    apply_plan(output, output_characters, output_instances, second_plan)
    idempotent_changes = changed_records(idempotent_before, full_snapshot(output))
    if idempotent_changes:
        raise RuntimeError(
            f"sigil relationship transform is not idempotent: {idempotent_changes[:3]}"
        )

    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": {
            "path": str(input_path),
            "sha256": input_sha256,
            "size": input_path.stat().st_size,
            "record_count": record_count,
        },
        "output": {
            "path": str(output_path),
            "sha256": sha256_file(output_path),
            "active_hash_ok": True,
            "size": output_path.stat().st_size,
            "record_count": len(output.records),
        },
        "counts": {
            "characters": len(rows),
            "selected_sigils": EXPECTED_CHARACTER_COUNT * SIGILS_PER_CHARACTER,
            "owner_links_reassigned": sum(
                row["owner_links_reassigned"] for row in rows
            ),
            "non_target_loadout_records_preserved": plan[
                "non_target_loadout_records"
            ],
            "externally_referenced_instances_reserved": len(
                plan["externally_referenced_units"]
            ),
            "target_tail_instances_reserved": len(plan["tail_referenced_units"]),
            "extra_playable_owner_links_preserved": len(
                plan["extra_playable_owner_units"]
            ),
            "loadouts_changed": sum(
                row["loadout_before"] != row["loadout_after"] for row in rows
            ),
            "record_changes": len(changes),
            "idempotent_changes": 0,
        },
        "policy": {
            "existing_nonzero_sigil_instances_only": True,
            "sigil_instance_ids_preserved": True,
            "sigil_shells_levels_flags_and_traits_preserved": True,
            "non_target_loadout_references_preserved": True,
            "target_loadout_tail_references_preserved": True,
            "unselected_owner_links_preserved": True,
            "unknown_nonplayable_owner_links_preserved": True,
            "protected_story_preserved": True,
        },
        "validation": {
            "all_29_loadouts_have_12_unique_instances": True,
            "all_selected_owner_links_match": True,
            "no_duplicate_global_selection": True,
            "non_target_1403_records_unchanged": True,
            "target_1403_tail_indexes_unchanged": True,
            "active_hash_ok": True,
            "steam_wrapper_unchanged": True,
            "payload_size_unchanged": True,
            "record_count_unchanged": True,
            "second_run_idempotent": True,
        },
        "characters": rows,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
