"""Create, configure, unlock, and equip the verified Relink 2.0.2 top four."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from equip_legacy_gold_sigils import (
    EMPTY_HASH,
    changed_records,
    first_value,
    full_snapshot,
    protected_story_digest,
    sha256_file,
    u32,
)
from save_editor_api import GBFRSaveData, add_editor_argument


EXPECTED_SPECS = (
    ("Rolan", 0x0F986ED9, 0xB5FF9FD3, 0x9245DFA4, 15, 9, 6),
    ("Lilith", 0xDFAB70B7, 0x24883AF3, 0xA3E537B1, 15, 9, 6),
    ("Beelzebub", 0xA7EFF558, 0x3D8153A1, 0xCE70C58A, 15, 9, 6),
    ("Lucilius", 0x6E5968FC, 0xEE85CD1F, 0x5A1D2C89, 15, 9, 6),
)
INSTANCE_FIELDS = (1456, 1457, 1458, 1459, 1460)
INSTANCE_COUNT = 1000
CATALOG_COUNT = 300


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline source save copy")
    parser.add_argument("output", type=Path, help="Separate offline output save")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--expected-catalog-sha256")
    add_editor_argument(parser)
    return parser.parse_args()


def load_catalog(path: Path, expected_sha256: str | None) -> list[dict]:
    if expected_sha256:
        actual = sha256_file(path)
        if actual != expected_sha256.strip().upper():
            raise RuntimeError(f"summon catalog SHA-256 {actual} != {expected_sha256}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("summons")
    if payload.get("schema_version") != 1 or not isinstance(rows, list):
        raise RuntimeError("summon creation catalog schema is invalid")
    if len(rows) != len(EXPECTED_SPECS):
        raise RuntimeError("summon creation catalog must contain four rows")
    resolved = []
    for index, (row, expected) in enumerate(zip(rows, EXPECTED_SPECS)):
        if not isinstance(row, dict) or row.get("order") != index:
            raise RuntimeError(f"summon catalog row {index} has an invalid order")
        actual = (
            str(row.get("name") or ""),
            int(str(row.get("summon_hash") or ""), 16),
            int(str(row.get("trait_hash") or ""), 16),
            int(str(row.get("bonus_hash") or ""), 16),
            int(row.get("trait_level", -1)),
            int(row.get("bonus_level", -1)),
            int(row.get("state_1460", -1)),
        )
        if actual != expected:
            raise RuntimeError(
                f"summon catalog row {index} differs from the verified tuple: {actual}"
            )
        resolved.append(
            {
                **row,
                "summon_hash_value": actual[1],
                "trait_hash_value": actual[2],
                "bonus_hash_value": actual[3],
            }
        )
    return resolved


def one_record(
    save: GBFRSaveData,
    kind: str,
    field_id: int,
    unit_id: int,
    value_count: int,
):
    records = save.records_by_id.get((kind, field_id, unit_id), [])
    if len(records) != 1:
        raise RuntimeError(
            f"expected one {kind} field {field_id}/unit {unit_id}, found {len(records)}"
        )
    record = records[0]
    if record.value_count != value_count:
        raise RuntimeError(
            f"unexpected width for {kind} field {field_id}/unit {unit_id}: "
            f"{record.value_count}"
        )
    return record


def collect_instances(save: GBFRSaveData) -> dict[int, dict[int, object]]:
    result = {}
    for unit in range(INSTANCE_COUNT):
        result[unit] = {
            1456: one_record(save, "uint", 1456, unit, 1),
            1457: one_record(save, "uint", 1457, unit, 1),
            1458: one_record(save, "uint", 1458, unit, 2),
            1459: one_record(save, "int", 1459, unit, 2),
            1460: one_record(save, "uint", 1460, unit, 1),
        }
    return result


def instance_values(save: GBFRSaveData, fields: dict[int, object]) -> dict[int, list]:
    return {field_id: list(save.get_values(fields[field_id])) for field_id in INSTANCE_FIELDS}


def is_clean_empty(values: dict[int, list]) -> bool:
    return values == {
        1456: [0],
        1457: [EMPTY_HASH],
        1458: [EMPTY_HASH, EMPTY_HASH],
        1459: [-1, -1],
        1460: [0],
    }


def set_values_if_changed(save: GBFRSaveData, record, values: list[int]) -> bool:
    if list(save.get_values(record)) == list(values):
        return False
    save.set_values(record, list(values))
    return True


def exact_instance(save: GBFRSaveData, fields: dict[int, object], spec: dict) -> bool:
    values = instance_values(save, fields)
    return (
        values[1457] == [spec["summon_hash_value"]]
        and values[1458]
        == [spec["trait_hash_value"], spec["bonus_hash_value"]]
        and values[1459] == [spec["trait_level"], spec["bonus_level"]]
        and values[1460] == [spec["state_1460"]]
    )


def apply_catalog(save: GBFRSaveData, specs: list[dict]) -> dict:
    equip = one_record(save, "uint", 1451, 0, 4)
    counter = one_record(save, "uint", 1454, 0, 1)
    initialized = one_record(save, "uint", 1455, 0, 1)
    initialized_before = first_value(save, initialized)
    if initialized_before not in (0, 1):
        raise RuntimeError(
            f"summon manager field 1455 must be 0 or 1, found {initialized_before}"
        )

    catalog_hashes = []
    catalog_flags = {}
    for unit in range(CATALOG_COUNT):
        catalog_hashes.append(
            u32(
                first_value(
                    save,
                    one_record(save, "uint", 1452, unit, 1),
                )
            )
        )
        catalog_flags[unit] = one_record(save, "uint", 1453, unit, 1)

    instances = collect_instances(save)
    ids = {
        unit: int(first_value(save, fields[1456]))
        for unit, fields in instances.items()
    }
    nonzero_ids = [instance_id for instance_id in ids.values() if instance_id > 0]
    if len(nonzero_ids) != len(set(nonzero_ids)):
        raise RuntimeError("save contains duplicate nonzero summon instance IDs")
    if initialized_before == 0:
        if (
            list(save.get_values(equip)) != [0, 0, 0, 0]
            or first_value(save, counter) != 0
            or any(
                not is_clean_empty(instance_values(save, fields))
                for fields in instances.values()
            )
        ):
            raise RuntimeError(
                "summon manager field 1455 is 0 but its inventory is not pristine"
            )
        set_values_if_changed(save, initialized, [1])
    next_id = max([int(first_value(save, counter)), *nonzero_ids, 0])

    created = []
    reused = []
    configured = []
    selected_ids = []
    selected_units = set()
    catalog_units = set()
    for spec in specs:
        candidates = [
            unit
            for unit, fields in instances.items()
            if ids[unit] > 0
            and u32(first_value(save, fields[1457])) == spec["summon_hash_value"]
        ]
        if candidates:
            unit = min(
                candidates,
                key=lambda candidate: (
                    not exact_instance(save, instances[candidate], spec),
                    ids[candidate],
                    candidate,
                ),
            )
            before = instance_values(save, instances[unit])
            action = "reused"
            reused.append(unit)
        else:
            unit = next(
                (
                    candidate
                    for candidate, fields in instances.items()
                    if ids[candidate] == 0
                    and is_clean_empty(instance_values(save, fields))
                ),
                None,
            )
            if unit is None:
                raise RuntimeError("no structurally clean summon inventory slot is available")
            if next_id >= 0xFFFFFFFF:
                raise RuntimeError("summon instance ID counter is exhausted")
            before = instance_values(save, instances[unit])
            next_id += 1
            ids[unit] = next_id
            set_values_if_changed(save, instances[unit][1456], [next_id])
            action = "created"
            created.append(unit)

        fields = instances[unit]
        set_values_if_changed(save, fields[1457], [spec["summon_hash_value"]])
        set_values_if_changed(
            save,
            fields[1458],
            [spec["trait_hash_value"], spec["bonus_hash_value"]],
        )
        set_values_if_changed(
            save, fields[1459], [spec["trait_level"], spec["bonus_level"]]
        )
        set_values_if_changed(save, fields[1460], [spec["state_1460"]])

        matches = [
            index
            for index, value in enumerate(catalog_hashes)
            if value == spec["summon_hash_value"]
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"{spec['name']} must appear exactly once in field 1452; found {matches}"
            )
        catalog_unit = matches[0]
        set_values_if_changed(save, catalog_flags[catalog_unit], [1])
        catalog_units.add(catalog_unit)
        selected_units.add(unit)
        selected_ids.append(ids[unit])
        configured.append(
            {
                "name": spec["name"],
                "action": action,
                "unit": unit,
                "instance_id": ids[unit],
                "catalog_unit": catalog_unit,
                "before": before,
                "after": instance_values(save, fields),
            }
        )

    set_values_if_changed(save, counter, [next_id])
    set_values_if_changed(save, equip, selected_ids)
    return {
        "created_units": created,
        "reused_units": reused,
        "selected_units": selected_units,
        "catalog_units": catalog_units,
        "selected_ids": selected_ids,
        "configured": configured,
        "counter_after": next_id,
        "initialized_manager": initialized_before == 0,
    }


def require_whitelisted_changes(changes: list[dict], result: dict) -> None:
    unexpected = []
    for change in changes:
        field_id = change["field_id"]
        unit = change["unit_id"]
        indexes = change["changed_indexes"]
        valid = False
        if field_id == 1451 and unit == 0:
            valid = all(index in range(4) for index in indexes)
        elif field_id == 1453 and unit in result["catalog_units"]:
            valid = indexes == [0]
        elif field_id == 1454 and unit == 0:
            valid = indexes == [0]
        elif field_id == 1455 and unit == 0:
            valid = indexes == [0]
        elif field_id in INSTANCE_FIELDS and unit in result["selected_units"]:
            valid = all(index in range(2 if field_id in (1458, 1459) else 1) for index in indexes)
        if not valid:
            unexpected.append(change)
    if unexpected:
        raise RuntimeError(f"unexpected summon creation changes: {unexpected[:3]}")


def verify_catalog(save: GBFRSaveData, specs: list[dict]) -> list[dict]:
    if first_value(save, one_record(save, "uint", 1455, 0, 1)) != 1:
        raise RuntimeError("summon manager field 1455 changed")
    instances = collect_instances(save)
    by_id = {}
    for unit, fields in instances.items():
        instance_id = int(first_value(save, fields[1456]))
        if instance_id <= 0:
            continue
        if instance_id in by_id:
            raise RuntimeError(f"duplicate summon instance ID {instance_id}")
        by_id[instance_id] = unit
    equipped = list(
        save.get_values(one_record(save, "uint", 1451, 0, 4))
    )
    if len(set(equipped)) != 4 or any(int(value) <= 0 for value in equipped):
        raise RuntimeError(f"invalid equipped summon IDs: {equipped}")
    rows = []
    for spec, instance_id in zip(specs, equipped):
        unit = by_id.get(int(instance_id))
        if unit is None or not exact_instance(save, instances[unit], spec):
            raise RuntimeError(f"{spec['name']} equipped summon verification failed")
        rows.append(
            {
                "name": spec["name"],
                "instance_id": int(instance_id),
                "unit": unit,
            }
        )
    counter = int(first_value(save, one_record(save, "uint", 1454, 0, 1)))
    if by_id and counter < max(by_id):
        raise RuntimeError("summon instance counter is below an existing instance ID")
    for spec in specs:
        catalog_matches = []
        for unit in range(CATALOG_COUNT):
            outer = u32(
                first_value(
                    save,
                    one_record(save, "uint", 1452, unit, 1),
                )
            )
            if outer == spec["summon_hash_value"]:
                catalog_matches.append(unit)
        if len(catalog_matches) != 1:
            raise RuntimeError(f"{spec['name']} catalog identity verification failed")
        flag = first_value(
            save,
            one_record(save, "uint", 1453, catalog_matches[0], 1),
        )
        if flag != 1:
            raise RuntimeError(f"{spec['name']} catalog unlock flag is not 1")
    return rows


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    catalog_path = args.catalog.resolve()
    audit_path = args.audit.resolve()
    for path, label in ((input_path, "input save"), (catalog_path, "summon catalog")):
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if len({input_path, output_path, audit_path}) != 3:
        raise RuntimeError("input, output, and audit paths must be distinct")
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("refusing to overwrite an output or audit")

    input_sha256 = sha256_file(input_path)
    specs = load_catalog(catalog_path, args.expected_catalog_sha256)
    save = GBFRSaveData.open(input_path)
    if save.check_active_hash() is not True:
        raise RuntimeError("input save active hash is invalid")
    header = dict(save.container.header)
    payload_size = save.container.payload_size
    record_count = len(save.records)
    story_digest = protected_story_digest(save)

    before = full_snapshot(save)
    result = apply_catalog(save, specs)
    after = full_snapshot(save)
    changes = changed_records(before, after)
    require_whitelisted_changes(changes, result)
    verify_catalog(save, specs)
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
    verified = verify_catalog(output, specs)

    idempotent_before = full_snapshot(output)
    second_result = apply_catalog(output, specs)
    idempotent_changes = changed_records(idempotent_before, full_snapshot(output))
    if idempotent_changes:
        raise RuntimeError(
            f"summon creation transform is not idempotent: {idempotent_changes[:3]}"
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
        "catalog": {
            "path": str(catalog_path),
            "sha256": sha256_file(catalog_path),
            "counts": {"summons": len(specs)},
        },
        "counts": {
            "target_summons": len(specs),
            "created_instances": len(result["created_units"]),
            "reused_instances": len(result["reused_units"]),
            "configured_instances": len(verified),
            "catalog_unlock_flags": len(result["catalog_units"]),
            "manager_initialized": int(result["initialized_manager"]),
            "record_changes": len(changes),
            "idempotent_changes": 0,
        },
        "policy": {
            "missing_instances_created_only_in_canonical_empty_slots": True,
            "uninitialized_manager_enabled_only_when_pristine": True,
            "existing_unknown_instances_preserved": True,
            "field_1460_uses_ingame_normalized_value_6": True,
            "protected_story_preserved": True,
        },
        "validation": {
            "active_hash_ok": True,
            "steam_wrapper_unchanged": True,
            "payload_size_unchanged": True,
            "record_count_unchanged": True,
            "equipped_top_four_exact": True,
            "catalog_flags_unlocked": True,
            "instance_ids_unique": True,
            "second_run_idempotent": True,
        },
        "summons": result["configured"],
        "verified_equipped": verified,
        "counter_after": result["counter_after"],
        "second_run": {
            "created_instances": len(second_result["created_units"]),
            "record_changes": 0,
        },
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
