"""Restore the swallowed 2.0 DLC continuation trigger without rewinding story state."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from save_editor_api import GBFRSaveData, add_editor_argument


TARGET_QUEST_ID = 0x00730237
TARGET_EXPECTED_INDEX = 65
TARGET_STORY_ID = 0x0010D001
TARGET_STORY_EXPECTED_INDEX = 54
EXPECTED_CURRENT_CHAPTER = 243
EXPECTED_UNLOCKED_CHAPTERS = (240, 241, 242)
QUEST_ID_FIELD = 2580
QUEST_STATE_FIELD = 2581
QUEST_STATE_BLOCK_64_FIELD = 2582
QUEST_STATE_BLOCK_32_FIELD = 2583
MAIN_STORY_FIELDS = (2510, 2511, 2520, 2522)


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def refuse_live_output(path: Path) -> None:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    live_directory = resolved(local_app_data / "GBFR" / "Saved" / "SaveGames")
    target = resolved(path)
    if target == live_directory or live_directory in target.parents:
        raise RuntimeError(f"Refusing to write into the live save directory: {target}")


def single_record(save: GBFRSaveData, field_id: int):
    records = save.find(id_type=field_id)
    if len(records) != 1:
        raise RuntimeError(
            f"Expected one field {field_id} record, found {len(records)}"
        )
    return records[0]


def record_digest(save: GBFRSaveData, field_ids: tuple[int, ...]) -> str:
    rows = []
    for field_id in field_ids:
        for record in save.find(id_type=field_id):
            rows.append(
                (
                    record.kind,
                    record.id_type,
                    record.unit_id,
                    list(save.get_values(record)),
                )
            )
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def record_snapshot(save: GBFRSaveData) -> dict[tuple[str, int, int], list]:
    return {
        (record.kind, record.id_type, record.unit_id): list(save.get_values(record))
        for record in save.records
    }


def changed_indexes(before: list, after: list) -> list[int]:
    if len(before) != len(after):
        raise RuntimeError("A save vector changed length")
    return [index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]]


def inspect_contract(save: GBFRSaveData) -> dict:
    quest_ids = [
        int(value) & 0xFFFFFFFF
        for value in save.get_values(single_record(save, QUEST_ID_FIELD))
    ]
    quest_states = [
        int(value)
        for value in save.get_values(single_record(save, QUEST_STATE_FIELD))
    ]
    if len(quest_ids) != 150 or len(quest_states) != 150:
        raise RuntimeError("Expected 150-entry 2580/2581 quest vectors")
    target_indexes = [
        index for index, quest_id in enumerate(quest_ids) if quest_id == TARGET_QUEST_ID
    ]
    if target_indexes != [TARGET_EXPECTED_INDEX]:
        raise RuntimeError(
            f"Expected quest {TARGET_QUEST_ID:08X} at index "
            f"{TARGET_EXPECTED_INDEX}, found {target_indexes}"
        )

    block_64 = [
        int(value)
        for value in save.get_values(
            single_record(save, QUEST_STATE_BLOCK_64_FIELD)
        )
    ]
    block_32 = [
        int(value)
        for value in save.get_values(
            single_record(save, QUEST_STATE_BLOCK_32_FIELD)
        )
    ]
    if len(block_64) != 150 * 64 or len(block_32) != 150 * 32:
        raise RuntimeError("Unexpected 2582/2583 quest block dimensions")
    target_block_64 = block_64[
        TARGET_EXPECTED_INDEX * 64 : (TARGET_EXPECTED_INDEX + 1) * 64
    ]
    target_block_32 = block_32[
        TARGET_EXPECTED_INDEX * 32 : (TARGET_EXPECTED_INDEX + 1) * 32
    ]
    if any(target_block_64) or any(target_block_32):
        raise RuntimeError("00730237 has unexpected nonzero subordinate quest state")

    story_ids = [
        int(value) & 0xFFFFFFFF
        for value in save.get_values(single_record(save, 2510))
    ]
    story_indexes = [
        index for index, story_id in enumerate(story_ids) if story_id == TARGET_STORY_ID
    ]
    if story_indexes != [TARGET_STORY_EXPECTED_INDEX]:
        raise RuntimeError(
            f"Expected story {TARGET_STORY_ID:08X} at index "
            f"{TARGET_STORY_EXPECTED_INDEX}, found {story_indexes}"
        )
    story_state_2511 = list(save.get_values(single_record(save, 2511)))
    story_state_2522 = list(save.get_values(single_record(save, 2522)))
    if (
        int(story_state_2511[TARGET_STORY_EXPECTED_INDEX]) != 1
        or int(story_state_2522[TARGET_STORY_EXPECTED_INDEX]) != 1
    ):
        raise RuntimeError("0010D001 is not in the expected game-registered state")

    current_chapter = [
        int(value) for value in save.get_values(single_record(save, 2506))
    ]
    if not current_chapter or current_chapter[0] != EXPECTED_CURRENT_CHAPTER:
        raise RuntimeError(
            f"Expected current chapter {EXPECTED_CURRENT_CHAPTER}, found {current_chapter}"
        )
    unlocked_chapters = [
        int(value) for value in save.get_values(single_record(save, 2590))
    ]
    for chapter in EXPECTED_UNLOCKED_CHAPTERS:
        if chapter not in unlocked_chapters:
            raise RuntimeError(f"Expected chapter-select key {chapter} to be registered")
    if EXPECTED_CURRENT_CHAPTER in unlocked_chapters:
        raise RuntimeError(
            "Chapter-select key 243 is already registered; this repair is not applicable"
        )

    return {
        "target_index": TARGET_EXPECTED_INDEX,
        "target_state": quest_states[TARGET_EXPECTED_INDEX],
        "target_block_64_nonzero": sum(bool(value) for value in target_block_64),
        "target_block_32_nonzero": sum(bool(value) for value in target_block_32),
        "story_index": TARGET_STORY_EXPECTED_INDEX,
        "story_state_2511": int(story_state_2511[TARGET_STORY_EXPECTED_INDEX]),
        "story_state_2522": int(story_state_2522[TARGET_STORY_EXPECTED_INDEX]),
        "current_chapter": current_chapter,
        "unlocked_chapter_tail": unlocked_chapters[-8:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("save", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--expect-source-sha")
    parser.add_argument("--expect-steam-id", type=int)
    add_editor_argument(parser)
    args = parser.parse_args()

    source_path = resolved(args.save)
    output_path = resolved(args.output)
    audit_path = resolved(args.audit)
    if len({source_path, output_path, audit_path}) != 3:
        raise RuntimeError("Input, output, and audit paths must be distinct")
    refuse_live_output(output_path)
    refuse_live_output(audit_path)
    if not source_path.is_file():
        raise RuntimeError(f"Input save does not exist: {source_path}")
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("Refusing to overwrite an existing output or audit")

    source_sha = sha256_file(source_path)
    if args.expect_source_sha and source_sha != args.expect_source_sha.upper():
        raise RuntimeError(
            f"Expected source SHA {args.expect_source_sha.upper()}, found {source_sha}"
        )
    source = GBFRSaveData.open(source_path)
    if source.check_active_hash() is not True:
        raise RuntimeError("Input save active hash is invalid")
    header = source.container.header or {}
    steam_id = header.get("steam_id")
    if args.expect_steam_id is not None and steam_id != args.expect_steam_id:
        raise RuntimeError(
            f"Expected SteamID64 {args.expect_steam_id}, found {steam_id}"
        )

    before_contract = inspect_contract(source)
    if before_contract["target_state"] not in (0, 1):
        raise RuntimeError(
            f"Unexpected 00730237 state {before_contract['target_state']}"
        )
    main_digest = record_digest(source, MAIN_STORY_FIELDS)
    before_snapshot = record_snapshot(source)
    quest_state_record = single_record(source, QUEST_STATE_FIELD)
    quest_states = list(source.get_values(quest_state_record))
    quest_states[TARGET_EXPECTED_INDEX] = 0
    source.set_values(quest_state_record, quest_states)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    source.save_as(output_path, update_hash=True)
    output = GBFRSaveData.open(output_path)
    if output.check_active_hash() is not True:
        raise RuntimeError("Output save active hash is invalid")
    if (output.container.header or {}).get("steam_id") != steam_id:
        raise RuntimeError("SteamID64 changed during serialization")
    if record_digest(output, MAIN_STORY_FIELDS) != main_digest:
        raise RuntimeError("Main-story fields changed during trigger repair")

    after_contract = inspect_contract(output)
    if after_contract["target_state"] != 0:
        raise RuntimeError("00730237 completion state was not reset")
    after_snapshot = record_snapshot(output)
    if set(before_snapshot) != set(after_snapshot):
        raise RuntimeError("Save record keys changed")
    changed_records = []
    for key in sorted(before_snapshot):
        indexes = changed_indexes(before_snapshot[key], after_snapshot[key])
        if indexes:
            changed_records.append(
                {
                    "kind": key[0],
                    "field_id": key[1],
                    "unit_id": key[2],
                    "changed_indexes": indexes,
                    "before": [before_snapshot[key][index] for index in indexes],
                    "after": [after_snapshot[key][index] for index in indexes],
                }
            )
    expected_changes = []
    if before_contract["target_state"] != 0:
        expected_changes = [
            {
                "kind": "uint",
                "field_id": QUEST_STATE_FIELD,
                "unit_id": 0,
                "changed_indexes": [TARGET_EXPECTED_INDEX],
                "before": [before_contract["target_state"]],
                "after": [0],
            }
        ]
    if changed_records != expected_changes:
        raise RuntimeError(f"Unexpected record delta: {changed_records}")

    audit = {
        "operation": "restore_dlc_2_0_continuation_trigger",
        "source": {
            "path": str(source_path),
            "sha256": source_sha,
            "size": source_path.stat().st_size,
            "steam_id": steam_id,
            "active_hash_ok": True,
        },
        "output": {
            "path": str(output_path),
            "sha256": sha256_file(output_path),
            "size": output_path.stat().st_size,
            "steam_id": (output.container.header or {}).get("steam_id"),
            "active_hash_ok": True,
        },
        "database_contract": {
            "chapter_select_key": EXPECTED_CURRENT_CHAPTER,
            "quest_id": f"{TARGET_STORY_ID:08X}",
            "unlock_quest_id": f"{TARGET_QUEST_ID:08X}",
        },
        "before": before_contract,
        "after": after_contract,
        "changed_records": changed_records,
        "protected_main_story_digest": main_digest,
    }
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
