"""Restore the 2.0 story dependency chain after 0010C001.

The old "complete everything except main story" preset raised side-quest,
quest-counter, and short-story completion fields directly.  That left the
chapter pointer at 0010D001 while swallowing the completion callbacks that
normally unlock the intervening 2.0 story chain.

This repair copies only the downstream dependency state from a pre-C001
reference save.  Main-story fields, chapter pointers, Fate Episode state,
characters, equipment, weapons, sigils, and inventory are protected.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

from save_editor_api import GBFRSaveData, add_editor_argument


SIDE_ID_FIELD = 2550
SIDE_STATE_FIELDS = (2551, 2554, 2555)
QUEST_ID_FIELD = 2570
QUEST_STATE_FIELDS = (2571, 2575, 2576)
QUEST_SUBMISSION_FIELD = 2577
SHORT_STORY_ID_FIELD = 2580
SHORT_STORY_STATE_FIELD = 2581

# Story-required quest-counter missions after 0010C001.  Sort-999 variants,
# post-ending 40A322-330 missions, and 40B challenge rematches are deliberately
# excluded: they do not gate the narrative and should remain completed.
QUEST_IDS = (
    0x00409302,
    0x00409303,
    0x00409305,
    0x00409307,
    0x00409308,
    0x00409309,
    0x00409311,
    0x00409312,
    0x00409313,
    0x00409314,
    0x00409315,
    0x00409317,
    0x00409319,
    0x00409320,
    0x0040A301,
    0x0040A302,
    0x0040A303,
    0x0040A304,
    0x0040A305,
    0x0040A306,
    0x0040A307,
    0x0040A308,
    0x0040A309,
    0x0040A310,
    0x0040A311,
    0x0040A312,
    0x0040A313,
    0x0040A314,
    0x0040A316,
    0x0040A320,
    0x0040A321,
)

SIDE_QUEST_IDS = (
    0x00220001,
    0x00220002,
    0x00220003,
    0x00220004,
    0x00220005,
    0x00230001,
    0x00230002,
    0x00230003,
    0x00230004,
)

# Transitive short-story/marker dependency closure from 0010C001 through the
# end of the 2.0 narrative.  00730236/00730237 are intentionally absent:
# those are Siegfried Fate Episode bridge/ending records, not main-story
# triggers.  00730237 is repaired separately to the completed state.
SHORT_STORY_IDS = (
    0x00730007,
    0x00730008,
    0x00730032,
    0x00730047,
    0x00730200,
    0x00730202,
    0x00730204,
    0x00730205,
    0x00730206,
    0x00730208,
    0x00730209,
    0x0073020F,
    0x00730210,
    0x00730211,
    0x00730212,
    0x00730213,
    0x00730214,
    0x00730215,
    0x00730216,
    0x00730220,
    0x00730221,
    0x00730222,
    0x00730223,
    0x00730224,
    0x00730225,
    0x00730226,
    0x00730227,
    0x00730228,
    0x00730229,
    0x00730230,
    0x00730231,
    0x00730232,
    0x00730233,
    0x00730313,
    0x00730333,
    0x00730334,
    0x00730335,
    0x00730336,
    0x00730337,
    0x00730338,
)

SIEGFRIED_FATE_QUEST_ID = 0x00730237
PROTECTED_MAIN_FIELDS = tuple(range(2500, 2530)) + tuple(range(2590, 2600))


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
        raise RuntimeError(f"Expected one field {field_id}, found {len(records)}")
    return records[0]


def values(save: GBFRSaveData, field_id: int) -> list:
    return list(save.get_values(single_record(save, field_id)))


def normalized_id(value) -> int:
    return int(value) & 0xFFFFFFFF


def quest_index_map(save: GBFRSaveData, field_id: int) -> dict[int, int]:
    result = {}
    for index, value in enumerate(values(save, field_id)):
        quest_id = normalized_id(value)
        if quest_id == 0:
            continue
        if quest_id in result:
            raise RuntimeError(f"Duplicate quest {quest_id:08X} in field {field_id}")
        result[quest_id] = index
    return result


def record_snapshot(save: GBFRSaveData) -> dict[tuple[str, int, int], list]:
    return {
        (record.kind, record.id_type, record.unit_id): list(save.get_values(record))
        for record in save.records
    }


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


def copy_scalar_indexes(
    source: GBFRSaveData,
    reference: GBFRSaveData,
    id_field: int,
    state_fields: tuple[int, ...],
    quest_ids: tuple[int, ...],
) -> list[dict]:
    source_indexes = quest_index_map(source, id_field)
    reference_indexes = quest_index_map(reference, id_field)
    rows = []
    for quest_id in quest_ids:
        if quest_id not in source_indexes or quest_id not in reference_indexes:
            raise RuntimeError(f"Quest {quest_id:08X} is missing from field {id_field}")
        source_index = source_indexes[quest_id]
        reference_index = reference_indexes[quest_id]
        row = {
            "quest_id": f"{quest_id:08X}",
            "source_index": source_index,
            "reference_index": reference_index,
            "fields": {},
        }
        for field_id in state_fields:
            source_record = single_record(source, field_id)
            source_values = list(source.get_values(source_record))
            reference_values = values(reference, field_id)
            before = source_values[source_index]
            target = reference_values[reference_index]
            source_values[source_index] = target
            source.set_values(source_record, source_values)
            row["fields"][str(field_id)] = {
                "before": before,
                "reference": target,
                "after": target,
            }
        rows.append(row)
    return rows


def copy_quest_submission_blocks(
    source: GBFRSaveData,
    reference: GBFRSaveData,
    quest_ids: tuple[int, ...],
    rows: list[dict],
) -> None:
    source_indexes = quest_index_map(source, QUEST_ID_FIELD)
    reference_indexes = quest_index_map(reference, QUEST_ID_FIELD)
    source_ids = values(source, QUEST_ID_FIELD)
    reference_ids = values(reference, QUEST_ID_FIELD)
    source_record = single_record(source, QUEST_SUBMISSION_FIELD)
    source_values = list(source.get_values(source_record))
    reference_values = values(reference, QUEST_SUBMISSION_FIELD)
    source_width, source_remainder = divmod(len(source_values), len(source_ids))
    reference_width, reference_remainder = divmod(
        len(reference_values), len(reference_ids)
    )
    if source_remainder or reference_remainder or source_width != reference_width:
        raise RuntimeError("Unexpected 2577 per-quest block dimensions")
    row_by_id = {int(row["quest_id"], 16): row for row in rows}
    for quest_id in quest_ids:
        source_index = source_indexes[quest_id]
        reference_index = reference_indexes[quest_id]
        source_start = source_index * source_width
        reference_start = reference_index * reference_width
        before = source_values[source_start : source_start + source_width]
        target = reference_values[
            reference_start : reference_start + reference_width
        ]
        source_values[source_start : source_start + source_width] = target
        row_by_id[quest_id]["fields"][str(QUEST_SUBMISSION_FIELD)] = {
            "width": source_width,
            "before": before,
            "reference": target,
            "after": target,
        }
    source.set_values(source_record, source_values)


def restore_siegfried_fate(source: GBFRSaveData) -> dict:
    indexes = quest_index_map(source, SHORT_STORY_ID_FIELD)
    if SIEGFRIED_FATE_QUEST_ID not in indexes:
        raise RuntimeError("00730237 is missing from the short-story table")
    index = indexes[SIEGFRIED_FATE_QUEST_ID]
    record = single_record(source, SHORT_STORY_STATE_FIELD)
    state = list(source.get_values(record))
    before = state[index]
    state[index] = 1
    source.set_values(record, state)
    return {
        "quest_id": f"{SIEGFRIED_FATE_QUEST_ID:08X}",
        "index": index,
        "before": before,
        "after": 1,
        "reason": "Siegfried Fate Episode bridge; preserve all-Fate completion",
    }


def changed_records(before: dict, after: dict) -> list[dict]:
    if set(before) != set(after):
        raise RuntimeError("Save record keys changed")
    result = []
    for key in sorted(before):
        left = before[key]
        right = after[key]
        if len(left) != len(right):
            raise RuntimeError(f"Record length changed for {key}")
        indexes = [index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]]
        if indexes:
            result.append(
                {
                    "kind": key[0],
                    "field_id": key[1],
                    "unit_id": key[2],
                    "changed_indexes": indexes,
                    "before": [left[index] for index in indexes],
                    "after": [right[index] for index in indexes],
                }
            )
    return result


def allowed_change_indexes(source: GBFRSaveData) -> dict[int, set[int]]:
    result = {field_id: set() for field_id in SIDE_STATE_FIELDS + QUEST_STATE_FIELDS}
    result[QUEST_SUBMISSION_FIELD] = set()
    result[SHORT_STORY_STATE_FIELD] = set()

    side_indexes = quest_index_map(source, SIDE_ID_FIELD)
    for quest_id in SIDE_QUEST_IDS:
        index = side_indexes[quest_id]
        for field_id in SIDE_STATE_FIELDS:
            result[field_id].add(index)

    quest_indexes = quest_index_map(source, QUEST_ID_FIELD)
    submission_values = values(source, QUEST_SUBMISSION_FIELD)
    width = len(submission_values) // len(values(source, QUEST_ID_FIELD))
    for quest_id in QUEST_IDS:
        index = quest_indexes[quest_id]
        for field_id in QUEST_STATE_FIELDS:
            result[field_id].add(index)
        result[QUEST_SUBMISSION_FIELD].update(range(index * width, (index + 1) * width))

    story_indexes = quest_index_map(source, SHORT_STORY_ID_FIELD)
    for quest_id in SHORT_STORY_IDS + (SIEGFRIED_FATE_QUEST_ID,):
        result[SHORT_STORY_STATE_FIELD].add(story_indexes[quest_id])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("save", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--expect-source-sha")
    parser.add_argument("--expect-steam-id", type=int)
    add_editor_argument(parser)
    args = parser.parse_args()

    source_path = resolved(args.save)
    reference_path = resolved(args.reference)
    output_path = resolved(args.output)
    audit_path = resolved(args.audit)
    if len({source_path, reference_path, output_path, audit_path}) != 4:
        raise RuntimeError("Input, reference, output, and audit paths must be distinct")
    refuse_live_output(output_path)
    refuse_live_output(audit_path)
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("Refusing to overwrite an existing output or audit")

    source_sha = sha256_file(source_path)
    if args.expect_source_sha and source_sha != args.expect_source_sha.upper():
        raise RuntimeError(
            f"Expected source SHA {args.expect_source_sha.upper()}, found {source_sha}"
        )
    source = GBFRSaveData.open(source_path)
    reference = GBFRSaveData.open(reference_path)
    if source.check_active_hash() is not True:
        raise RuntimeError("Input save active hash is invalid")
    if reference.check_active_hash() is not True:
        raise RuntimeError("Reference save active hash is invalid")
    steam_id = (source.container.header or {}).get("steam_id")
    if args.expect_steam_id is not None and steam_id != args.expect_steam_id:
        raise RuntimeError(
            f"Expected SteamID64 {args.expect_steam_id}, found {steam_id}"
        )

    protected_digest = record_digest(source, PROTECTED_MAIN_FIELDS)
    before_snapshot = record_snapshot(source)
    allowed = allowed_change_indexes(source)

    side_rows = copy_scalar_indexes(
        source,
        reference,
        SIDE_ID_FIELD,
        SIDE_STATE_FIELDS,
        SIDE_QUEST_IDS,
    )
    quest_rows = copy_scalar_indexes(
        source,
        reference,
        QUEST_ID_FIELD,
        QUEST_STATE_FIELDS,
        QUEST_IDS,
    )
    copy_quest_submission_blocks(source, reference, QUEST_IDS, quest_rows)
    story_rows = copy_scalar_indexes(
        source,
        reference,
        SHORT_STORY_ID_FIELD,
        (SHORT_STORY_STATE_FIELD,),
        SHORT_STORY_IDS,
    )
    siegfried_fate = restore_siegfried_fate(source)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    source.save_as(output_path, update_hash=True)
    output = GBFRSaveData.open(output_path)
    if output.check_active_hash() is not True:
        raise RuntimeError("Output save active hash is invalid")
    if (output.container.header or {}).get("steam_id") != steam_id:
        raise RuntimeError("SteamID64 changed during serialization")
    if record_digest(output, PROTECTED_MAIN_FIELDS) != protected_digest:
        raise RuntimeError("Protected main-story/chapter fields changed")

    after_snapshot = record_snapshot(output)
    changes = changed_records(before_snapshot, after_snapshot)
    for change in changes:
        field_id = change["field_id"]
        if field_id not in allowed:
            raise RuntimeError(f"Unexpected changed field {field_id}")
        unexpected = set(change["changed_indexes"]) - allowed[field_id]
        if unexpected:
            raise RuntimeError(
                f"Unexpected indexes in field {field_id}: {sorted(unexpected)}"
            )

    audit = {
        "operation": "restore_mainline_dependency_chain_after_0010C001",
        "source": {
            "path": str(source_path),
            "sha256": source_sha,
            "size": source_path.stat().st_size,
            "steam_id": steam_id,
            "active_hash_ok": True,
        },
        "reference": {
            "path": str(reference_path),
            "sha256": sha256_file(reference_path),
            "active_hash_ok": True,
        },
        "output": {
            "path": str(output_path),
            "sha256": sha256_file(output_path),
            "size": output_path.stat().st_size,
            "steam_id": (output.container.header or {}).get("steam_id"),
            "active_hash_ok": True,
        },
        "policy": {
            "checkpoint": "after 0010C001",
            "protected_main_fields": list(PROTECTED_MAIN_FIELDS),
            "fate_preserved": True,
            "siegfried_00730237_state": 1,
            "optional_post_story_quests_preserved": True,
        },
        "side_quests": side_rows,
        "quest_counter_missions": quest_rows,
        "main_story_short_stories": story_rows,
        "siegfried_fate_repair": siegfried_fate,
        "changed_records": changes,
        "protected_main_digest": protected_digest,
    }
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
