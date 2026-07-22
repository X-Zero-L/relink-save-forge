"""Enable every verified playable character row without changing story progress."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from equip_legacy_gold_sigils import (
    changed_records,
    first_value,
    full_snapshot,
    load_characters,
    map_character_units,
    protected_story_digest,
    required_record,
    sha256_file,
)
from save_editor_api import GBFRSaveData, add_editor_argument


BASE_MASK_CHARACTERS = {
    "PL0000",
    "PL0100",
    "PL0200",
    "PL0300",
    "PL0400",
    "PL0500",
    "PL0600",
}


def activation_mask(character_id: str) -> int:
    if character_id in BASE_MASK_CHARACTERS:
        return 0x01
    if character_id == "PL1900":
        return 0x09
    return 0x11


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline source save copy")
    parser.add_argument("output", type=Path, help="Separate offline output save")
    parser.add_argument("--characters", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    add_editor_argument(parser)
    return parser.parse_args()


def activate_characters(save: GBFRSaveData, characters: list[dict]) -> list[dict]:
    rows = []
    for character in characters:
        unit = character["unit"]
        record = required_record(
            save, 1305, unit, kinds=("int", "uint"), value_count=1
        )
        before = first_value(save, record)
        mask = activation_mask(character["id"])
        after = before | mask
        if after != before:
            save.set_first_value(record, after)
        rows.append(
            {
                "character_id": character["id"],
                "name": character.get("name"),
                "unit": unit,
                "activation_mask": mask,
                "before_1305": before,
                "after_1305": after,
                "changed": before != after,
            }
        )
    return rows


def verify_active(save: GBFRSaveData, characters: list[dict]) -> None:
    for character in characters:
        unit = character["unit"]
        actual = first_value(
            save,
            required_record(
                save, 1305, unit, kinds=("int", "uint"), value_count=1
            ),
        )
        mask = activation_mask(character["id"])
        if actual & mask != mask:
            raise RuntimeError(
                f"{character['id']} field 1305 lacks activation mask 0x{mask:02X}"
            )


def require_whitelisted_changes(changes: list[dict], characters: list[dict]) -> None:
    units = {row["unit"] for row in characters}
    unexpected = [
        change
        for change in changes
        if not (
            change["unit_id"] in units
            and change["field_id"] == 1305
            and change["changed_indexes"] == [0]
        )
    ]
    if unexpected:
        raise RuntimeError(f"unexpected character activation changes: {unexpected[:3]}")


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

    before = full_snapshot(save)
    rows = activate_characters(save, characters)
    after = full_snapshot(save)
    changes = changed_records(before, after)
    require_whitelisted_changes(changes, characters)
    verify_active(save, characters)
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
    verify_active(output, output_characters)

    idempotent_before = full_snapshot(output)
    activate_characters(output, output_characters)
    idempotent_changes = changed_records(idempotent_before, full_snapshot(output))
    if idempotent_changes:
        raise RuntimeError(
            f"character activation transform is not idempotent: {idempotent_changes[:3]}"
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
            "playable_characters": len(rows),
            "activated_characters": sum(int(row["changed"]) for row in rows),
            "record_changes": len(changes),
            "idempotent_changes": 0,
        },
        "policy": {
            "preallocated_character_rows_only": True,
            "only_field_1305_modified": True,
            "existing_1305_bits_preserved_by_bitwise_or": True,
            "progression_and_loadout_fields_preserved": True,
            "protected_story_preserved": True,
        },
        "validation": {
            "all_29_catalog_characters_mapped": True,
            "all_activation_masks_present": True,
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
