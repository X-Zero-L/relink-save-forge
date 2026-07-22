"""Complete every database-backed Relink 2.0 weapon transcendence instance."""

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from save_editor_api import GBFRSaveData, add_editor_argument


ROOT = Path(__file__).resolve().parents[1]
MAIN_FIELDS = (2510, 2511, 2520, 2522)
WEAPON_FIELDS = (2802, 2803, 2807, 2813, 2815, 2816, 2817, 2818)
PRESERVED_WEAPON_FIELDS = (2803, 2807, 2813, 2816)
TRANSCENDENCE_FIELDS = (2815, 2817, 2818)
TRANSCENDENCE_UNLOCK_FLAG = 0x40
TRANSCENDENCE_MAX_LEVEL = 7
OLD_AWAKENING_MAX_LEVEL = 10


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def refuse_live_output(output: Path) -> None:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    live_directory = resolved(local_app_data / "GBFR" / "Saved" / "SaveGames")
    target = resolved(output)
    if target == live_directory or live_directory in target.parents:
        raise RuntimeError(f"Refusing to write into the live save directory: {target}")


def protected_digest(save: GBFRSaveData) -> str:
    rows = []
    for field_id in MAIN_FIELDS:
        for record in save.find(id_type=field_id):
            rows.append((record.kind, field_id, record.unit_id, list(save.get_values(record))))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def record_snapshot(save: GBFRSaveData) -> dict[tuple[str, int, int], list]:
    return {
        (record.kind, record.id_type, record.unit_id): list(save.get_values(record))
        for record in save.records
    }


def record_delta(before: GBFRSaveData, after: GBFRSaveData) -> dict:
    left = record_snapshot(before)
    right = record_snapshot(after)
    changed = []
    for key in sorted(set(left) & set(right)):
        if left[key] != right[key]:
            changed.append(
                {
                    "kind": key[0],
                    "field_id": key[1],
                    "unit": key[2],
                    "before": left[key],
                    "after": right[key],
                }
            )
    return {
        "added": [list(key) for key in sorted(set(right) - set(left))],
        "removed": [list(key) for key in sorted(set(left) - set(right))],
        "changed": changed,
        "changed_field_counts": dict(sorted(Counter(row["field_id"] for row in changed).items())),
        "changed_field_types": sorted({row["field_id"] for row in changed}),
    }


def load_specs(path: Path) -> tuple[dict[int, dict], dict]:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != 3:
        raise RuntimeError("Expected schema_version 3 database-curve catalog")
    counts = catalog.get("counts", {})
    if counts != {
        "database_complete_rows": 162,
        "current_specs": 160,
        "alternate_runtime_specs": 2,
    }:
        raise RuntimeError(f"Unexpected catalog counts: {counts}")
    if set(catalog.get("vector_derivation", {}).get("never_uses", [])) != {
        "max_skill_id",
        "global_final_skill",
    }:
        raise RuntimeError("Catalog does not forbid the disproved global-skill model")

    specs = {}
    current_hashes = set()
    alternate_keys = []
    for row in catalog["items"]:
        runtime_hash = int(row["runtime_hash"], 16)
        if runtime_hash in specs:
            raise RuntimeError(f"Duplicate runtime hash {row['runtime_hash']}")
        vector = [int(item["hash"], 16) for item in row["skill_vector"]]
        if len(vector) != 5 or any(not item["curve_id"] for item in row["skill_vector"]):
            raise RuntimeError(f"Invalid curve vector for {row['database_key']}")
        spec = {**row, "runtime_hash_int": runtime_hash, "vector": vector}
        specs[runtime_hash] = spec
        if row["alternate_runtime_only"]:
            alternate_keys.append(row["database_key"])
        else:
            current_hashes.add(runtime_hash)
    return specs, {"current_hashes": current_hashes, "alternate_keys": alternate_keys}


def value(save: GBFRSaveData, group: dict, field_id: int) -> int:
    return int(save.get_first_value(group[field_id], 0))


def values(save: GBFRSaveData, group: dict, field_id: int) -> list[int]:
    return [int(item) & 0xFFFFFFFF for item in save.get_values(group[field_id])]


def collect_targets(save: GBFRSaveData, specs: dict[int, dict]) -> list[dict]:
    targets = []
    for unit, group in save.group_by_unit(WEAPON_FIELDS).items():
        if 2802 not in group or not value(save, group, 2802):
            continue
        if any(field_id not in group for field_id in WEAPON_FIELDS):
            continue
        spec = specs.get(value(save, group, 2803) & 0xFFFFFFFF)
        if spec is None or spec["alternate_runtime_only"]:
            continue
        targets.append({"unit": unit, "group": group, "spec": spec})
    return sorted(targets, key=lambda item: item["unit"])


def summarize(save: GBFRSaveData, target: dict) -> dict:
    group = target["group"]
    spec = target["spec"]
    return {
        "unit": target["unit"],
        "slot_id": value(save, group, 2802),
        "database_key": spec["database_key"],
        "official_id": spec["official_id"],
        "old_awakening": spec["old_awakening"],
        "2807": value(save, group, 2807),
        "2815": value(save, group, 2815),
        "2817": value(save, group, 2817),
        "2818": [f"{item:08X}" for item in values(save, group, 2818)],
        "expected_2818": [f"{item:08X}" for item in spec["vector"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("save", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--rebuild-catalog",
        type=Path,
        default=ROOT / "catalogs" / "weapon-rebuild-2.0.json",
    )
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--expect-instances", type=int)
    parser.add_argument("--expect-types", type=int)
    parser.add_argument("--expect-source-full", type=int)
    parser.add_argument("--expect-source-remaining", type=int)
    add_editor_argument(parser)
    args = parser.parse_args()

    source_path = resolved(args.save)
    output_path = resolved(args.output)
    if source_path == output_path:
        raise RuntimeError("Refusing to overwrite the input save")
    refuse_live_output(output_path)
    source = GBFRSaveData.open(source_path)
    if source.check_active_hash() is not True:
        raise RuntimeError("Input save active hash is invalid")
    before_main = protected_digest(source)
    specs, metadata = load_specs(resolved(args.rebuild_catalog))
    targets = collect_targets(source, specs)
    present_types = {target["spec"]["runtime_hash_int"] for target in targets}
    missing = metadata["current_hashes"] - present_types

    for expected, actual, label in (
        (args.expect_instances, len(targets), "instances"),
        (args.expect_types, len(present_types), "types"),
    ):
        if expected is not None and actual != expected:
            raise RuntimeError(f"Expected {expected} {label}, found {actual}")

    source_full = []
    source_remaining = []
    old_instances = 0
    ordinary_instances = 0
    for target in targets:
        group = target["group"]
        spec = target["spec"]
        expected_2807 = OLD_AWAKENING_MAX_LEVEL if spec["old_awakening"] else 0
        if value(source, group, 2807) != expected_2807:
            raise RuntimeError(
                f"Weapon unit {target['unit']} has 2807={value(source, group, 2807)}, "
                f"expected {expected_2807}"
            )
        old_instances += int(spec["old_awakening"])
        ordinary_instances += int(not spec["old_awakening"])
        stage = value(source, group, 2817)
        summary = summarize(source, target)
        if stage == TRANSCENDENCE_MAX_LEVEL:
            if not value(source, group, 2815) & TRANSCENDENCE_UNLOCK_FLAG:
                raise RuntimeError(f"Full weapon unit {target['unit']} lacks flag 0x40")
            if values(source, group, 2818) != spec["vector"]:
                raise RuntimeError(f"Full weapon unit {target['unit']} disproves its DB curve vector")
            source_full.append(summary)
        elif stage == 0:
            source_remaining.append(summary)
        else:
            raise RuntimeError(f"Weapon unit {target['unit']} has unsupported source stage {stage}")

    for expected, actual, label in (
        (args.expect_source_full, len(source_full), "full source instances"),
        (args.expect_source_remaining, len(source_remaining), "remaining source instances"),
    ):
        if expected is not None and actual != expected:
            raise RuntimeError(f"Expected {expected} {label}, found {actual}")

    changes = []
    for target in targets:
        group = target["group"]
        before = summarize(source, target)
        preserved = {field_id: list(source.get_values(group[field_id])) for field_id in PRESERVED_WEAPON_FIELDS}
        source.set_first_value(group[2815], value(source, group, 2815) | TRANSCENDENCE_UNLOCK_FLAG)
        source.set_first_value(group[2817], TRANSCENDENCE_MAX_LEVEL)
        source.set_values(group[2818], target["spec"]["vector"])
        for field_id, expected in preserved.items():
            if list(source.get_values(group[field_id])) != expected:
                raise RuntimeError(f"Weapon unit {target['unit']} changed preserved field {field_id}")
        after = summarize(source, target)
        if before != after:
            changes.append({"unit": target["unit"], "before": before, "after": after})

    source.save_as(output_path, update_hash=True)
    output = GBFRSaveData.open(output_path)
    if output.check_active_hash() is not True:
        raise RuntimeError("Output active hash is invalid")
    if protected_digest(output) != before_main:
        raise RuntimeError("Protected main-story fields changed")
    original = GBFRSaveData.open(source_path)
    delta = record_delta(original, output)
    if delta["added"] or delta["removed"]:
        raise RuntimeError("Save records were added or removed")
    if not set(delta["changed_field_types"]).issubset(TRANSCENDENCE_FIELDS):
        raise RuntimeError(f"Unexpected changed fields: {delta['changed_field_types']}")
    if len(changes) != len(source_remaining):
        raise RuntimeError("Changed-instance count does not equal incomplete source count")

    completed = collect_targets(output, specs)
    for target in completed:
        group = target["group"]
        if value(output, group, 2817) != TRANSCENDENCE_MAX_LEVEL:
            raise RuntimeError(f"Weapon unit {target['unit']} did not persist level 7")
        if not value(output, group, 2815) & TRANSCENDENCE_UNLOCK_FLAG:
            raise RuntimeError(f"Weapon unit {target['unit']} did not persist flag 0x40")
        if values(output, group, 2818) != target["spec"]["vector"]:
            raise RuntimeError(f"Weapon unit {target['unit']} did not persist its curve vector")

    audit = {
        "catalog": str(resolved(args.rebuild_catalog)),
        "database_eligibility": {
            "database_specs": 162,
            "current_specs": 160,
            "alternate_runtime_specs": metadata["alternate_keys"],
            "present_current_spec_types": len(present_types),
            "missing_current_runtime_hashes": [f"{item:08X}" for item in sorted(missing)],
        },
        "curve_vector_derivation": {
            "source_columns": ["WeaponSkillLevelRebuildId1..5", "Unk12", "Unk13"],
            "never_uses": ["max_skill_id", "global_final_skill"],
            "full_source_instances_proven": len(source_full),
            "full_source_vector_checks": len(source_full) * 5,
            "proof_mismatches": [],
        },
        "instances": {
            "eligible": len(completed),
            "old_awakening": old_instances,
            "ordinary": ordinary_instances,
            "source_full": len(source_full),
            "source_remaining": len(source_remaining),
            "changed": len(changes),
            "completed": len(completed),
        },
        "field_policy": {
            "preserved": list(PRESERVED_WEAPON_FIELDS),
            "ordinary_2807": 0,
            "legacy_old_awakening_2807": 10,
            "2815_operation": "OR 0x40",
            "2817": 7,
            "2818": "per-weapon five-slot DB curve vector",
        },
        "record_delta": delta,
        "active_hash_ok": True,
        "main_story_unchanged": True,
        "source_full_instances": source_full,
        "source_remaining_instances": source_remaining,
        "changes": changes,
    }
    audit_path = resolved(args.audit)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "eligible_instances": len(completed),
                "present_types": len(present_types),
                "source_full": len(source_full),
                "source_remaining": len(source_remaining),
                "changed_instances": len(changes),
                "changed_field_types": delta["changed_field_types"],
                "active_hash_ok": True,
                "main_story_unchanged": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
