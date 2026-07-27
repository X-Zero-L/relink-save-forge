"""Replace only the passive trait lane on the four equipped top-tier summons."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from build_all_sigils_strict import reference_hash
from equip_legacy_gold_sigils import (
    changed_records,
    first_value,
    full_snapshot,
    protected_story_digest,
    required_record,
    sha256_file,
    u32,
)
from save_editor_api import GBFRSaveData, add_editor_argument


EXPECTED_SUMMON_HASHES = {
    "Rolan": 0x0F986ED9,
    "Lilith": 0xDFAB70B7,
    "Beelzebub": 0xA7EFF558,
    "Lucilius": 0x6E5968FC,
}
STANDARD_TRAITS = {
    "Rolan": "SKILL_072_00",
    "Lilith": "SKILL_073_00",
    "Beelzebub": "SKILL_234_00",
    "Lucilius": "SKILL_233_00",
}
GOLD_TRAITS = {**STANDARD_TRAITS, "Rolan": "SKILL_063_00"}
TRAIT_LEVEL = 15


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline source save copy")
    parser.add_argument("output", type=Path, help="Separate offline output save")
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--expected-preset-sha256")
    add_editor_argument(parser)
    return parser.parse_args()


def load_preset(path: Path, expected_sha256: str | None) -> dict:
    if expected_sha256:
        actual = sha256_file(path)
        if actual != expected_sha256.strip().upper():
            raise RuntimeError(
                f"summon trait preset SHA-256 {actual} != {expected_sha256}"
            )
    payload = json.loads(path.read_text(encoding="utf-8"))
    preset_id = str(payload.get("id") or "")
    expected_traits = (
        GOLD_TRAITS
        if preset_id == "gold-endgame-qol-passives-2.0.2"
        else STANDARD_TRAITS
    )
    rows = payload.get("summons")
    if payload.get("schema_version") != 1 or not isinstance(rows, list):
        raise RuntimeError("summon trait preset schema is invalid")
    if len(rows) != 4:
        raise RuntimeError("summon trait preset must contain four rows")
    resolved = {}
    for row in rows:
        name = str(row.get("name") or "")
        if name not in EXPECTED_SUMMON_HASHES or name in resolved:
            raise RuntimeError(f"unexpected or duplicate summon row {name!r}")
        summon_hash = int(str(row.get("summon_hash") or ""), 16)
        if summon_hash != EXPECTED_SUMMON_HASHES[name]:
            raise RuntimeError(f"{name} summon hash differs from the verified shell")
        trait_id = str(row.get("trait_id") or "")
        if trait_id != expected_traits[name]:
            raise RuntimeError(f"{name} trait must be {expected_traits[name]}")
        trait_hash = reference_hash(trait_id)
        if str(row.get("trait_hash") or "").upper() != f"{trait_hash:08X}":
            raise RuntimeError(f"{name} trait hash differs from its ID")
        if row.get("trait_level") != TRAIT_LEVEL:
            raise RuntimeError(f"{name} trait level must be 15")
        if row.get("preserve_bonus") is not True:
            raise RuntimeError(f"{name} must preserve the summon bonus lane")
        resolved[name] = {
            **row,
            "summon_hash_value": summon_hash,
            "trait_hash_value": trait_hash,
        }
    if set(resolved) != set(EXPECTED_SUMMON_HASHES):
        raise RuntimeError("summon trait preset does not cover the verified top four")
    payload["resolved"] = resolved
    return payload


def equipped_instances(save: GBFRSaveData) -> list[dict]:
    equip = required_record(save, 1451, 0, kinds=("uint",), value_count=4)
    equipped_ids = [int(value) for value in save.get_values(equip)]
    if any(value <= 0 for value in equipped_ids) or len(set(equipped_ids)) != 4:
        raise RuntimeError(f"invalid equipped summon instance IDs: {equipped_ids}")
    instance_to_unit = {}
    for record in save.find(id_type=1456):
        if record.kind != "uint" or record.value_count != 1:
            continue
        instance_id = int(first_value(save, record))
        if not instance_id:
            continue
        if instance_id in instance_to_unit:
            raise RuntimeError(f"duplicate summon instance ID {instance_id}")
        instance_to_unit[instance_id] = int(record.unit_id)
    rows = []
    for instance_id in equipped_ids:
        unit = instance_to_unit.get(instance_id)
        if unit is None:
            raise RuntimeError(f"equipped summon instance {instance_id} does not resolve")
        outer = required_record(save, 1457, unit, kinds=("uint",), value_count=1)
        trait_bonus = required_record(save, 1458, unit, kinds=("uint",), value_count=2)
        levels = required_record(save, 1459, unit, kinds=("int",), value_count=2)
        flags = required_record(save, 1460, unit, kinds=("uint",), value_count=1)
        rows.append(
            {
                "instance_id": instance_id,
                "unit": unit,
                "outer_hash": u32(first_value(save, outer)),
                "trait_bonus_record": trait_bonus,
                "levels_record": levels,
                "bonus_hash": u32(list(save.get_values(trait_bonus))[1]),
                "bonus_level": int(list(save.get_values(levels))[1]),
                "flags": list(save.get_values(flags)),
            }
        )
    return rows


def match_preset(equipped: list[dict], preset: dict) -> list[dict]:
    by_hash = {
        row["summon_hash_value"]: {"name": name, **row}
        for name, row in preset["resolved"].items()
    }
    matched = []
    seen = set()
    for row in equipped:
        spec = by_hash.get(row["outer_hash"])
        if spec is None:
            raise RuntimeError(
                f"equipped summon {row['instance_id']} has unexpected shell "
                f"{row['outer_hash']:08X}"
            )
        if spec["name"] in seen:
            raise RuntimeError(f"equipped summons repeat {spec['name']}")
        seen.add(spec["name"])
        matched.append({**row, "spec": spec})
    if seen != set(EXPECTED_SUMMON_HASHES):
        raise RuntimeError("equipped summons are not the verified top four")
    return matched


def apply_preset(save: GBFRSaveData, matched: list[dict]) -> list[dict]:
    rows = []
    for row in matched:
        trait_bonus = list(save.get_values(row["trait_bonus_record"]))
        levels = list(save.get_values(row["levels_record"]))
        before_trait = u32(trait_bonus[0])
        before_level = int(levels[0])
        trait_bonus[0] = row["spec"]["trait_hash_value"]
        levels[0] = TRAIT_LEVEL
        save.set_values(row["trait_bonus_record"], trait_bonus)
        save.set_values(row["levels_record"], levels)
        rows.append(
            {
                "name": row["spec"]["name"],
                "summon_hash": f"{row['outer_hash']:08X}",
                "instance_id": row["instance_id"],
                "unit": row["unit"],
                "trait_before": f"{before_trait:08X}",
                "trait_level_before": before_level,
                "trait_id": row["spec"]["trait_id"],
                "trait_hash": f"{row['spec']['trait_hash_value']:08X}",
                "trait_level": TRAIT_LEVEL,
                "bonus_hash_preserved": f"{row['bonus_hash']:08X}",
                "bonus_level_preserved": row["bonus_level"],
                "field_1460_preserved": row["flags"],
            }
        )
    return rows


def verify_preset(save: GBFRSaveData, matched: list[dict]) -> None:
    for row in matched:
        trait_bonus = list(save.get_values(row["trait_bonus_record"]))
        levels = list(save.get_values(row["levels_record"]))
        if (
            u32(trait_bonus[0]) != row["spec"]["trait_hash_value"]
            or int(levels[0]) != TRAIT_LEVEL
        ):
            raise RuntimeError(f"{row['spec']['name']} passive trait verification failed")
        if u32(trait_bonus[1]) != row["bonus_hash"]:
            raise RuntimeError(f"{row['spec']['name']} bonus hash changed")
        if int(levels[1]) != row["bonus_level"]:
            raise RuntimeError(f"{row['spec']['name']} bonus level changed")
        flags = required_record(
            save,
            1460,
            row["unit"],
            kinds=("uint",),
            value_count=1,
        )
        if list(save.get_values(flags)) != row["flags"]:
            raise RuntimeError(f"{row['spec']['name']} field 1460 changed")


def require_whitelisted_changes(changes: list[dict], matched: list[dict]) -> None:
    units = {row["unit"] for row in matched}
    unexpected = [
        change
        for change in changes
        if not (
            change["unit_id"] in units
            and change["field_id"] in (1458, 1459)
            and change["changed_indexes"] == [0]
        )
    ]
    if unexpected:
        raise RuntimeError(f"unexpected summon trait changes: {unexpected[:3]}")


def relationship_snapshot(save: GBFRSaveData, matched: list[dict]) -> dict:
    return {
        "equipped": list(
            save.get_values(
                required_record(save, 1451, 0, kinds=("uint",), value_count=4)
            )
        ),
        "instances": {
            str(row["unit"]): {
                "instance_id": row["instance_id"],
                "outer_hash": row["outer_hash"],
                "bonus_hash": row["bonus_hash"],
                "bonus_level": row["bonus_level"],
                "field_1460": row["flags"],
            }
            for row in matched
        },
    }


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    preset_path = args.preset.resolve()
    audit_path = args.audit.resolve()
    for path, label in (
        (input_path, "input save"),
        (preset_path, "summon trait preset"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if len({input_path, output_path, audit_path}) != 3:
        raise RuntimeError("input, output, and audit paths must be distinct")
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("refusing to overwrite an output or audit")

    input_sha256 = sha256_file(input_path)
    preset = load_preset(preset_path, args.expected_preset_sha256)
    save = GBFRSaveData.open(input_path)
    if save.check_active_hash() is not True:
        raise RuntimeError("input save active hash is invalid")
    header = dict(save.container.header)
    payload_size = save.container.payload_size
    record_count = len(save.records)
    story_digest = protected_story_digest(save)
    matched = match_preset(equipped_instances(save), preset)
    relationships = relationship_snapshot(save, matched)

    before = full_snapshot(save)
    rows = apply_preset(save, matched)
    after = full_snapshot(save)
    changes = changed_records(before, after)
    require_whitelisted_changes(changes, matched)
    verify_preset(save, matched)
    if relationship_snapshot(save, matched) != relationships:
        raise RuntimeError("summon relationships or preserved fields changed")
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
    output_matched = match_preset(equipped_instances(output), preset)
    if relationship_snapshot(output, output_matched) != relationships:
        raise RuntimeError("serialized summon relationships or preserved fields changed")
    verify_preset(output, output_matched)

    idempotent_before = full_snapshot(output)
    apply_preset(output, output_matched)
    idempotent_changes = changed_records(idempotent_before, full_snapshot(output))
    if idempotent_changes:
        raise RuntimeError(f"summon trait transform is not idempotent: {idempotent_changes[:3]}")

    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": {"path": str(input_path), "sha256": input_sha256},
        "output": {
            "path": str(output_path),
            "sha256": sha256_file(output_path),
            "active_hash_ok": True,
            "size": output_path.stat().st_size,
            "record_count": len(output.records),
        },
        "preset": {
            "path": str(preset_path),
            "sha256": sha256_file(preset_path),
            "id": preset.get("id"),
        },
        "counts": {
            "equipped_summons": 4,
            "passive_traits": 4,
            "record_changes": len(changes),
            "idempotent_changes": 0,
        },
        "policy": {
            "only_1458_1459_index_zero_modified": True,
            "summon_shells_preserved": True,
            "summon_bonus_lanes_preserved": True,
            "field_1460_preserved": True,
            "protected_story_preserved": True,
        },
        "summons": rows,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
