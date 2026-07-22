"""Strictly complete the Granblue Fantasy: Relink 2.0 specialization board.

The 2.0 board reuses the save's 400-entry ``1601``/``1602`` node array for
each character.  ``1601`` stores a node id and ``1602`` stores its unlock
bitset.  This module resolves database nodes through their saved ``1601`` id,
preserves every existing bit, and only ORs the verified unlock bit into empty
specialization groups.
"""

import hashlib
import sqlite3
from collections import defaultdict
from pathlib import Path

from save_editor_api import GBFRSaveData


EXPECTED_LAYOUT_ROWS = 2_895
EXPECTED_AUTO_ACQUIRE_ROWS = 1_450
EXPECTED_AUTO_ROWS_PER_CHARACTER = 50
EXPECTED_NODE_ARRAY_ROWS = 400
EXPECTED_FINAL_SELECTABLE_NODES = 1_450

GROUP_SPECS = (
    {
        "id": "68DE92AC",
        "name": "Chaos I",
        "cap": 10,
        "start": 0,
        "end": 10,
        "root_index": 0,
    },
    {
        "id": "A96D9EBC",
        "name": "Chaos II",
        "cap": 10,
        "start": 10,
        "end": 20,
        "root_index": 1,
    },
    {
        "id": "4A5DDC7B",
        "name": "Chaos III",
        "cap": 10,
        "start": 20,
        "end": 30,
        "root_index": 2,
    },
    {
        "id": "3B99904D",
        "name": "Chaos EX",
        "cap": 20,
        "start": 30,
        "end": 50,
        "root_index": None,
    },
)


def _u32_hex(value: object, *, label: str) -> int:
    text = str(value or "").strip()
    try:
        parsed = int(text, 16)
    except ValueError as exc:
        raise RuntimeError(f"invalid {label} hex value {text!r}") from exc
    if not 0 <= parsed <= 0xFFFFFFFF:
        raise RuntimeError(f"{label} is outside uint32 range: {text!r}")
    return parsed


def _first_value(save: GBFRSaveData, record) -> int:
    value = save.get_first_value(record)
    if value is None:
        raise RuntimeError(
            f"record {record.kind}:{record.index} has no readable first value"
        )
    return int(value)


def _required_tables(connection: sqlite3.Connection) -> None:
    required = {
        "skillboard_auto_acquire",
        "skillboard_layout",
        "skillboard_unlock",
    }
    present = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required - present)
    if missing:
        raise RuntimeError(f"mastery database is missing tables: {missing}")


def _database_plan(path: Path, character_ids: set[str]) -> dict:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        _required_tables(connection)
        layout_count = int(
            connection.execute("SELECT COUNT(*) FROM skillboard_layout").fetchone()[0]
        )
        auto_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM skillboard_auto_acquire"
            ).fetchone()[0]
        )
        if layout_count != EXPECTED_LAYOUT_ROWS:
            raise RuntimeError(
                f"expected {EXPECTED_LAYOUT_ROWS} skillboard layouts, found {layout_count}"
            )
        if auto_count != EXPECTED_AUTO_ACQUIRE_ROWS:
            raise RuntimeError(
                "expected "
                f"{EXPECTED_AUTO_ACQUIRE_ROWS} auto-acquire rows, found {auto_count}"
            )

        unlock_rows = connection.execute(
            "SELECT Rank1NodeCountAdd, Rank2NodeCountAdd, "
            "Rank3NodeCountAdd, RankEXNodeCountAdd "
            "FROM skillboard_unlock ORDER BY rowid"
        ).fetchall()
        if len(unlock_rows) != EXPECTED_AUTO_ROWS_PER_CHARACTER:
            raise RuntimeError(
                "skillboard_unlock must contain exactly "
                f"{EXPECTED_AUTO_ROWS_PER_CHARACTER} rows"
            )
        cap_columns = range(4)
        database_caps = tuple(
            sum(int(row[column]) for row in unlock_rows) for column in cap_columns
        )
        expected_caps = tuple(int(spec["cap"]) for spec in GROUP_SPECS)
        if database_caps != expected_caps:
            raise RuntimeError(
                f"unexpected specialization caps {database_caps}; expected {expected_caps}"
            )

        plan = {}
        for character_id in sorted(character_ids):
            layout_rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT rowid, Key, SkillboardEffectOrUiId, "
                    "SkillboardCategoryId, SkillboardGroupId, CharacterId, "
                    "Unk24, Unk25, Unk30 "
                    "FROM skillboard_layout WHERE CharacterId = ? ORDER BY rowid",
                    (character_id,),
                )
            ]
            if not layout_rows:
                raise RuntimeError(f"no skillboard layouts found for {character_id}")
            by_key = {}
            for row in layout_rows:
                key = str(row["Key"]).upper()
                if key in by_key:
                    raise RuntimeError(
                        f"duplicate skillboard layout key {key} for {character_id}"
                    )
                row["Key"] = key
                row["SkillboardGroupId"] = str(row["SkillboardGroupId"]).upper()
                row["node_hash"] = _u32_hex(
                    row["SkillboardEffectOrUiId"],
                    label=f"{character_id} node",
                )
                row["bit"] = int(row["Unk24"])
                if not 0 <= row["bit"] <= 31:
                    raise RuntimeError(
                        f"{character_id}/{key} has invalid unlock bit {row['bit']}"
                    )
                by_key[key] = row

            auto_rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT rowid, SkillboardLayoutId1, SkillboardLayoutId2, "
                    "SkillboardLayoutId3, Unk4 FROM skillboard_auto_acquire "
                    "WHERE Unk4 = ? ORDER BY rowid",
                    (character_id,),
                )
            ]
            if len(auto_rows) != EXPECTED_AUTO_ROWS_PER_CHARACTER:
                raise RuntimeError(
                    f"{character_id} has {len(auto_rows)} auto-acquire rows; "
                    f"expected {EXPECTED_AUTO_ROWS_PER_CHARACTER}"
                )

            groups = []
            chosen_node_hashes = set()
            for spec in GROUP_SPECS:
                group_id = str(spec["id"])
                selectable = [
                    row
                    for row in layout_rows
                    if row["SkillboardGroupId"] == group_id
                    and int(row["Unk25"]) != 100
                ]
                if not selectable:
                    raise RuntimeError(
                        f"{character_id}/{group_id} has no selectable layouts"
                    )

                chosen_keys = [
                    str(auto_rows[index]["SkillboardLayoutId1"]).upper()
                    for index in range(int(spec["start"]), int(spec["end"]))
                ]
                if len(chosen_keys) != int(spec["cap"]) or len(set(chosen_keys)) != int(
                    spec["cap"]
                ):
                    raise RuntimeError(
                        f"{character_id}/{group_id} column-1 nodes are not a unique full set"
                    )
                chosen = []
                for key in chosen_keys:
                    row = by_key.get(key)
                    if row is None:
                        raise RuntimeError(
                            f"{character_id} auto-acquire references missing layout {key}"
                        )
                    if row["SkillboardGroupId"] != group_id or int(row["Unk25"]) == 100:
                        raise RuntimeError(
                            f"{character_id}/{key} is not a selectable {group_id} node"
                        )
                    if row["node_hash"] in chosen_node_hashes:
                        raise RuntimeError(
                            f"{character_id} column-1 plan repeats node "
                            f"{row['node_hash']:08X}"
                        )
                    chosen_node_hashes.add(row["node_hash"])
                    chosen.append(row)

                root_index = spec["root_index"]
                if root_index is None:
                    roots = [
                        row
                        for row in layout_rows
                        if row["SkillboardGroupId"] == group_id
                        and int(row["Unk25"]) == 100
                    ]
                    if roots:
                        raise RuntimeError(
                            f"{character_id}/{group_id} unexpectedly has EX root nodes"
                        )
                    root = None
                else:
                    roots = [
                        row
                        for row in layout_rows
                        if row["SkillboardGroupId"] == group_id
                        and int(row["Unk25"]) == 100
                        and str(row["SkillboardCategoryId"]) == "SB_DEF"
                        and int(row["Unk30"]) == int(root_index)
                    ]
                    if len(roots) != 1:
                        raise RuntimeError(
                            f"{character_id}/{group_id} expected one DEF root, found {len(roots)}"
                        )
                    root = roots[0]

                groups.append(
                    {
                        **spec,
                        "selectable": selectable,
                        "chosen": chosen,
                        "root": root,
                    }
                )

            if len(chosen_node_hashes) != EXPECTED_AUTO_ROWS_PER_CHARACTER:
                raise RuntimeError(
                    f"{character_id} column-1 plan does not contain 50 unique nodes"
                )
            plan[character_id] = {
                "layouts": layout_rows,
                "groups": groups,
            }
    finally:
        connection.close()

    if set(plan) != character_ids:
        missing = sorted(character_ids - set(plan))
        extra = sorted(set(plan) - character_ids)
        raise RuntimeError(
            f"specialization database character mismatch: missing={missing}, extra={extra}"
        )
    return {
        "layout_rows": layout_count,
        "auto_acquire_rows": auto_count,
        "unlock_rows": len(unlock_rows),
        "caps": list(database_caps),
        "characters": plan,
    }


def _save_node_maps(save: GBFRSaveData, character_unit: int) -> tuple[dict, dict]:
    start = character_unit * 1000
    end = start + EXPECTED_NODE_ARRAY_ROWS
    node_rows = [
        record
        for record in save.find(id_type=1601)
        if start <= int(record.unit_id) < end
    ]
    state_rows = [
        record
        for record in save.find(id_type=1602)
        if start <= int(record.unit_id) < end
    ]
    if len(node_rows) != EXPECTED_NODE_ARRAY_ROWS or len(state_rows) != EXPECTED_NODE_ARRAY_ROWS:
        raise RuntimeError(
            f"character unit {character_unit} must have 400 paired 1601/1602 rows"
        )
    nodes_by_hash = defaultdict(list)
    states_by_unit = {}
    for record in node_rows:
        if record.kind != "uint" or record.value_count != 1:
            raise RuntimeError(
                f"unexpected 1601 shape at unit {record.unit_id}: "
                f"{record.kind}/{record.value_count}"
            )
        nodes_by_hash[_first_value(save, record) & 0xFFFFFFFF].append(
            int(record.unit_id)
        )
    for record in state_rows:
        if record.kind != "int" or record.value_count != 1:
            raise RuntimeError(
                f"unexpected 1602 shape at unit {record.unit_id}: "
                f"{record.kind}/{record.value_count}"
            )
        unit = int(record.unit_id)
        if unit in states_by_unit:
            raise RuntimeError(f"duplicate 1602 row at unit {unit}")
        states_by_unit[unit] = record
    if {record.unit_id for record in node_rows} != set(states_by_unit):
        raise RuntimeError(f"1601/1602 unit pairing differs for character {character_unit}")
    return dict(nodes_by_hash), states_by_unit


def _resolve_layouts(
    save: GBFRSaveData,
    character_id: str,
    character_unit: int,
    layouts: list[dict],
) -> tuple[dict[int, dict], dict[int, object]]:
    nodes_by_hash, states_by_unit = _save_node_maps(save, character_unit)
    resolved = {}
    for row in layouts:
        node_hash = int(row["node_hash"])
        units = nodes_by_hash.get(node_hash, [])
        if len(units) != 1:
            raise RuntimeError(
                f"{character_id} node {node_hash:08X} maps to {len(units)} saved 1601 rows"
            )
        unit = units[0]
        if unit in resolved:
            raise RuntimeError(
                f"{character_id} layouts share saved node unit {unit}"
            )
        state_record = states_by_unit.get(unit)
        if state_record is None:
            raise RuntimeError(f"{character_id} node unit {unit} has no paired 1602 row")
        resolved[unit] = row
    return resolved, states_by_unit


def _target_hash(target_masks: dict[int, int]) -> str:
    payload = "\n".join(
        f"{unit}:{mask}" for unit, mask in sorted(target_masks.items())
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest().upper()


def complete_specialization_board(
    save: GBFRSaveData,
    database_path: Path,
    characters: list[dict],
    *,
    expected_new_units: int | None = None,
    expected_target_sha256: str | None = None,
    expected_final_active_layouts: int | None = None,
) -> tuple[dict, dict[int, int]]:
    """Fill empty specialization groups and return an audit plus changed masks."""

    character_ids = {str(character["id"]) for character in characters}
    database = _database_plan(database_path, character_ids)
    target_masks = {}
    state_records = {}
    old_layout_bits = set()
    character_rows = []

    for character in characters:
        character_id = str(character["id"])
        character_unit = int(character["unit"])
        character_plan = database["characters"][character_id]
        resolved, character_states = _resolve_layouts(
            save,
            character_id,
            character_unit,
            character_plan["layouts"],
        )
        state_records.update(character_states)

        for unit, row in resolved.items():
            value = _first_value(save, character_states[unit])
            bit = int(row["bit"])
            if (value >> bit) & 1:
                old_layout_bits.add((unit, bit))

        group_rows = []
        for group in character_plan["groups"]:
            active_before = 0
            for row in group["selectable"]:
                node_hash = int(row["node_hash"])
                unit = next(
                    unit
                    for unit, resolved_row in resolved.items()
                    if int(resolved_row["node_hash"]) == node_hash
                )
                value = _first_value(save, character_states[unit])
                active_before += (value >> int(row["bit"])) & 1
            cap = int(group["cap"])
            if active_before not in (0, cap):
                raise RuntimeError(
                    f"{character_id}/{group['name']} is partial or over-cap: "
                    f"{active_before}/{cap}"
                )

            planned_rows = []
            if active_before == 0:
                planned_rows.extend(group["chosen"])
                if group["root"] is not None:
                    planned_rows.append(group["root"])
            changed_units = []
            for row in planned_rows:
                node_hash = int(row["node_hash"])
                unit = next(
                    unit
                    for unit, resolved_row in resolved.items()
                    if int(resolved_row["node_hash"]) == node_hash
                )
                bit = int(row["bit"])
                mask = 1 << bit
                value = _first_value(save, character_states[unit])
                if value & mask:
                    continue
                previous = target_masks.get(unit, 0)
                if previous & mask:
                    raise RuntimeError(
                        f"duplicate specialization target unit/bit {unit}/{bit}"
                    )
                target_masks[unit] = previous | mask
                changed_units.append(unit)

            group_rows.append(
                {
                    "group_id": group["id"],
                    "name": group["name"],
                    "cap": cap,
                    "active_before": active_before,
                    "preserved_existing_full_group": active_before == cap,
                    "planned_selectable_nodes": 0 if active_before == cap else cap,
                    "planned_root": active_before == 0 and group["root"] is not None,
                    "new_units": sorted(changed_units),
                }
            )
        character_rows.append(
            {
                "character_id": character_id,
                "name": character.get("name"),
                "unit": character_unit,
                "groups": group_rows,
            }
        )

    target_sha256 = _target_hash(target_masks)
    if expected_new_units is not None and len(target_masks) != expected_new_units:
        raise RuntimeError(
            f"specialization target count {len(target_masks)} != expected {expected_new_units}"
        )
    if expected_target_sha256 is not None:
        expected_hash = expected_target_sha256.strip().upper()
        if target_sha256 != expected_hash:
            raise RuntimeError(
                f"specialization target hash {target_sha256} != expected {expected_hash}"
            )

    before_values = {
        unit: _first_value(save, state_records[unit]) for unit in target_masks
    }
    for unit, mask in sorted(target_masks.items()):
        old_value = before_values[unit]
        new_value = old_value | mask
        if new_value == old_value:
            raise RuntimeError(f"specialization target {unit} was already active")
        save.set_first_value(state_records[unit], new_value)

    final_selectable = 0
    final_active_layouts = 0
    final_layout_bits = set()
    for character, character_report in zip(characters, character_rows):
        character_id = str(character["id"])
        character_unit = int(character["unit"])
        character_plan = database["characters"][character_id]
        resolved, character_states = _resolve_layouts(
            save,
            character_id,
            character_unit,
            character_plan["layouts"],
        )
        units_by_hash = {
            int(row["node_hash"]): unit for unit, row in resolved.items()
        }
        for unit, row in resolved.items():
            value = _first_value(save, character_states[unit])
            bit = int(row["bit"])
            if (value >> bit) & 1:
                final_active_layouts += 1
                final_layout_bits.add((unit, bit))
        for group, group_report in zip(
            character_plan["groups"], character_report["groups"]
        ):
            active_after = 0
            for row in group["selectable"]:
                unit = units_by_hash[int(row["node_hash"])]
                value = _first_value(save, character_states[unit])
                active_after += (value >> int(row["bit"])) & 1
            if active_after != int(group["cap"]):
                raise RuntimeError(
                    f"{character_id}/{group['name']} persisted as "
                    f"{active_after}/{group['cap']}"
                )
            group_report["active_after"] = active_after
            final_selectable += active_after

    if final_selectable != EXPECTED_FINAL_SELECTABLE_NODES:
        raise RuntimeError(
            f"final selectable specialization count {final_selectable} != "
            f"{EXPECTED_FINAL_SELECTABLE_NODES}"
        )
    if not old_layout_bits <= final_layout_bits:
        raise RuntimeError("existing specialization layout bits were cleared")
    if (
        expected_final_active_layouts is not None
        and final_active_layouts != expected_final_active_layouts
    ):
        raise RuntimeError(
            f"final active layout count {final_active_layouts} != expected "
            f"{expected_final_active_layouts}"
        )
    for unit, mask in target_masks.items():
        old_value = before_values[unit]
        final_value = _first_value(save, state_records[unit])
        if final_value != old_value | mask:
            raise RuntimeError(
                f"specialization unit {unit} changed outside its OR mask"
            )

    database_report = {
        key: value for key, value in database.items() if key != "characters"
    }
    report = {
        "database": database_report,
        "policy": {
            "existing_full_groups_preserved": True,
            "empty_groups_use_official_auto_acquire_column": 1,
            "chaos_1_to_3_def_roots_enabled": True,
            "state_operation": "old | (1 << Unk24)",
            "no_existing_bits_cleared": True,
        },
        "counts": {
            "characters": len(character_rows),
            "new_1602_units": len(target_masks),
            "final_selectable_nodes": final_selectable,
            "final_active_layouts": final_active_layouts,
        },
        "target_sha256": target_sha256,
        "target_masks": [
            {"unit": unit, "mask": mask}
            for unit, mask in sorted(target_masks.items())
        ],
        "characters": character_rows,
        "validation": {
            "database_layout_count_exact": True,
            "database_auto_acquire_count_exact": True,
            "unlock_caps_10_10_10_20": True,
            "all_saved_node_ids_unique_per_character": True,
            "all_groups_started_empty_or_full": True,
            "all_29_groups_final_10_10_10_20": True,
            "all_writes_are_bitwise_or": True,
            "existing_layout_bits_preserved": True,
        },
    }
    return report, target_masks
