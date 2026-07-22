"""Create or complete every verified Relink 2.0.2 weapon on an offline save.

The supported public path is catalog and template driven. It does not require a
user-specific forge probe, preserves unknown weapon copies, and refuses
malformed or duplicate official weapon layouts instead of guessing repairs.
"""

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from save_editor_api import GBFRSaveData, UnitRecord, add_editor_argument


ROOT = Path(__file__).resolve().parents[1]
MAIN_FIELDS = (2510, 2511, 2520, 2522)
EXPECTED_OFFICIAL_WEAPONS = 174
EXPECTED_ENDGAME_WEAPONS = 160
EXPECTED_BASE_ONLY_WEAPONS = 14

DEFAULT_CHARACTERS = ROOT / "catalogs" / "characters.json"
DEFAULT_WEAPONS = ROOT / "catalogs" / "weapons.json"
DEFAULT_REBUILD = ROOT / "catalogs" / "weapon-rebuild-2.0.json"
DEFAULT_IDENTITIES = ROOT / "catalogs" / "weapon-runtime-identities-2.0.2.json"
DEFAULT_TEMPLATE = ROOT / "catalogs" / "weapon-instance-template-2.0.2.json"


@dataclass(frozen=True)
class InstanceTemplate:
    main_version: int
    sub_version: int
    counter_field: int
    counter_unit: int
    unit_min: int
    unit_max: int
    contracts: dict[int, tuple[str, int]]
    empty: dict[int, tuple[int, ...]]
    empty_hash: int
    experience: int
    uncap: int
    plus: int
    transcendence: int
    owned_flag: int
    awakening_flag: int
    transcendence_flag: int


@dataclass(frozen=True)
class WeaponTarget:
    official_id: str
    character_id: str
    collection_slot: int
    base_hash: int
    target_hash: int
    expected_2807: int
    skill_vector: tuple[int, ...]
    endgame: bool


@dataclass(frozen=True)
class WeaponSnapshot:
    unit: int
    values: dict[int, tuple[int, ...]]

    @property
    def slot(self) -> int:
        return self.values[2802][0]

    @property
    def weapon_hash(self) -> int:
        return self.values[2803][0] & 0xFFFFFFFF


@dataclass(frozen=True)
class PlannedWeapon:
    target: WeaponTarget
    unit: int
    slot: int
    action: str
    before_hash: int | None
    desired: dict[int, tuple[int, ...]]


@dataclass(frozen=True)
class BuildPlan:
    weapons: tuple[PlannedWeapon, ...]
    final_counter: int
    unknown: tuple[WeaponSnapshot, ...]


@dataclass
class WeaponLayout:
    counter: int
    groups: dict[int, dict[int, UnitRecord]]
    occupied: list[WeaponSnapshot]
    empty_units: list[int]


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def file_metadata(path: Path, save: GBFRSaveData) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        "record_count": len(save.records),
        "active_hash_ok": save.check_active_hash() is True,
    }


def parse_hash(value: object, label: str) -> int:
    try:
        return int(str(value), 16) & 0xFFFFFFFF
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} is not an eight-digit hash") from exc


def refuse_live_output(path: Path, label: str) -> None:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    live = resolved(local_app_data / "GBFR" / "Saved" / "SaveGames")
    target = resolved(path)
    if target == live or live in target.parents:
        raise RuntimeError(f"Refusing to write {label} into the live save directory: {target}")


def load_template(path: Path) -> InstanceTemplate:
    payload = read_json(path)
    if (
        payload.get("schema_version") != 1
        or payload.get("id") != "weapon-instance-template-2.0.2"
        or payload.get("game_data_version") != "Relink 2.0.2"
    ):
        raise RuntimeError("weapon instance template schema or id is invalid")
    layout = payload.get("layout", {})
    raw_contracts = layout.get("fields", {})
    raw_empty = payload.get("canonical_empty", {})
    expected_fields = {2802, 2803, 2804, 2805, 2806, 2807, 2813, 2814, 2815, 2816, 2817, 2818}
    if {int(key) for key in raw_contracts} != expected_fields:
        raise RuntimeError("weapon template field contracts are incomplete")
    if {int(key) for key in raw_empty} != expected_fields:
        raise RuntimeError("weapon template canonical empty values are incomplete")
    contracts = {
        int(field_id): (str(contract["kind"]), int(contract["value_count"]))
        for field_id, contract in raw_contracts.items()
    }
    empty = {
        int(field_id): tuple(int(value) for value in values)
        for field_id, values in raw_empty.items()
    }
    for field_id, (_kind, value_count) in contracts.items():
        if len(empty[field_id]) != value_count:
            raise RuntimeError(f"weapon template field {field_id} has the wrong empty width")
    progression = payload.get("max_progression", {})
    flags = payload.get("flags", {})
    header = payload.get("save_header", {})
    template = InstanceTemplate(
        main_version=int(header["main_version"]),
        sub_version=int(header["sub_version"]),
        counter_field=int(layout["counter_field"]),
        counter_unit=int(layout["counter_unit"]),
        unit_min=int(layout["weapon_unit_min"]),
        unit_max=int(layout["weapon_unit_max"]),
        contracts=contracts,
        empty=empty,
        empty_hash=parse_hash(payload.get("empty_hash"), "template empty_hash"),
        experience=int(progression["experience"]),
        uncap=int(progression["uncap"]),
        plus=int(progression["plus"]),
        transcendence=int(progression["transcendence"]),
        owned_flag=int(flags["owned"]),
        awakening_flag=int(flags["old_awakening"]),
        transcendence_flag=int(flags["transcendence_unlocked"]),
    )
    if template.unit_max - template.unit_min + 1 != 256:
        raise RuntimeError("weapon template must describe exactly 256 physical units")
    if template.empty[2803] != (template.empty_hash,):
        raise RuntimeError("weapon template empty hash does not match field 2803")
    if template.empty[2818] != (template.empty_hash,) * 5:
        raise RuntimeError("weapon template empty transcendence vector is invalid")
    if (template.experience, template.uncap, template.plus, template.transcendence) != (
        162_540,
        6,
        99,
        7,
    ):
        raise RuntimeError("weapon template max progression contract changed")
    return template


def load_targets(
    weapon_path: Path,
    rebuild_path: Path,
    identity_path: Path,
) -> tuple[list[WeaponTarget], dict[int, str]]:
    weapons = read_json(weapon_path)
    items = weapons.get("items")
    if (
        weapons.get("count") != EXPECTED_OFFICIAL_WEAPONS
        or not isinstance(items, list)
        or len(items) != EXPECTED_OFFICIAL_WEAPONS
    ):
        raise RuntimeError("weapon catalog must contain exactly 174 official weapons")
    rebuild = read_json(rebuild_path)
    current_specs = [
        row for row in rebuild.get("items", []) if not row.get("alternate_runtime_only")
    ]
    if rebuild.get("schema_version") != 3 or len(current_specs) != EXPECTED_ENDGAME_WEAPONS:
        raise RuntimeError("weapon rebuild catalog must contain 160 current specs")
    specs = {}
    for row in current_specs:
        official_id = str(row["official_id"])
        if official_id in specs:
            raise RuntimeError(f"duplicate current rebuild spec for {official_id}")
        vector = tuple(
            parse_hash(item["hash"], f"{official_id} skill vector")
            for item in row["skill_vector"]
        )
        if len(vector) != 5:
            raise RuntimeError(f"{official_id} rebuild vector does not contain five skills")
        specs[official_id] = {**row, "vector": vector}

    identities = read_json(identity_path)
    raw_identities = identities.get("identities")
    identity_source = identities.get("source", {})
    if (
        identities.get("schema_version") != 1
        or identities.get("id") != "weapon-runtime-identities-2.0.2"
        or identities.get("game_data_version") != "Relink 2.0.2"
        or identities.get("official_weapon_count") != EXPECTED_OFFICIAL_WEAPONS
        or identities.get("identity_count") != 371
        or identity_source.get("weapon_rows") != 410
        or identity_source.get("database_sha256")
        != "7A721A9B1822C2C7660C71F653B2FA1B3BE2DAFC4049B6FD4178BA78E5E96789"
        or not isinstance(raw_identities, dict)
        or len(raw_identities) != 371
    ):
        raise RuntimeError("weapon runtime identity catalog schema or counts are invalid")
    alias_to_official = {
        parse_hash(hash_value, "runtime identity"): str(official_id)
        for hash_value, official_id in raw_identities.items()
    }

    targets = []
    official_ids = set()
    base_hashes = set()
    targets_by_character: dict[str, list[WeaponTarget]] = defaultdict(list)
    for item in items:
        official_id = str(item["id"])
        character_id = str(item["character_id"])
        base_hash = parse_hash(item["hash"], f"{official_id} base hash")
        if official_id in official_ids or base_hash in base_hashes:
            raise RuntimeError(f"duplicate official weapon {official_id}")
        if item.get("database_match") is not True:
            raise RuntimeError(f"weapon {official_id} is not database verified")
        official_ids.add(official_id)
        base_hashes.add(base_hash)
        spec = specs.get(official_id)
        if spec is None:
            target_hash = base_hash
            expected_2807 = 0
            vector: tuple[int, ...] = ()
            endgame = False
        else:
            if parse_hash(spec["base_hash"], f"{official_id} rebuild base") != base_hash:
                raise RuntimeError(f"{official_id} rebuild base hash differs from weapons.json")
            target_hash = parse_hash(spec["runtime_hash"], f"{official_id} runtime hash")
            expected_2807 = int(spec["expected_2807"])
            if expected_2807 not in (0, 10):
                raise RuntimeError(f"{official_id} has unsupported expected_2807")
            vector = spec["vector"]
            endgame = True
        if alias_to_official.get(base_hash) != official_id:
            raise RuntimeError(f"identity catalog does not map {official_id} base hash")
        if alias_to_official.get(target_hash) != official_id:
            raise RuntimeError(f"identity catalog does not map {official_id} target hash")
        target = WeaponTarget(
            official_id=official_id,
            character_id=character_id,
            collection_slot=int(item["collection_slot"]),
            base_hash=base_hash,
            target_hash=target_hash,
            expected_2807=expected_2807,
            skill_vector=vector,
            endgame=endgame,
        )
        targets.append(target)
        targets_by_character[character_id].append(target)
    if set(alias_to_official.values()) != official_ids:
        raise RuntimeError("identity catalog official weapon coverage differs")
    if Counter(target.endgame for target in targets) != Counter(
        {True: EXPECTED_ENDGAME_WEAPONS, False: EXPECTED_BASE_ONLY_WEAPONS}
    ):
        raise RuntimeError("weapon target endgame/base-only counts are invalid")
    if len(targets_by_character) != 29 or any(
        len(rows) != 6
        or {row.collection_slot for row in rows} != set(range(1, 7))
        for rows in targets_by_character.values()
    ):
        raise RuntimeError("weapon targets must contain six ordered weapons for 29 characters")
    return targets, alias_to_official


def normalized_record_values(
    save: GBFRSaveData,
    record: UnitRecord,
) -> tuple[int, ...]:
    values = []
    for value in save.get_values(record):
        parsed = int(value)
        if record.kind == "uint":
            parsed &= 0xFFFFFFFF
        values.append(parsed)
    return tuple(values)


def required_record(
    save: GBFRSaveData,
    field_id: int,
    unit: int,
    *,
    kind: str,
    value_count: int,
) -> UnitRecord:
    rows = save.find(id_type=field_id, unit_id=unit)
    if len(rows) != 1:
        raise RuntimeError(f"expected one field {field_id} at unit {unit}, found {len(rows)}")
    record = rows[0]
    if record.kind != kind or record.value_count != value_count:
        raise RuntimeError(
            f"field {field_id} unit {unit} has {record.kind}[{record.value_count}], "
            f"expected {kind}[{value_count}]"
        )
    return record


def classify_weapon_values(
    values: dict[int, tuple[int, ...]],
    template: InstanceTemplate,
    unit: int,
) -> str:
    if values == template.empty:
        return "empty"
    slot = values[2802][0]
    weapon_hash = values[2803][0] & 0xFFFFFFFF
    if slot <= 0 or weapon_hash in (0, template.empty_hash):
        raise RuntimeError(f"weapon unit {unit} is a noncanonical partial shell")
    if values[2814] != template.empty[2814]:
        raise RuntimeError(f"weapon unit {unit} has unsupported field 2814 state")
    return "occupied"


def read_layout(save: GBFRSaveData, template: InstanceTemplate) -> WeaponLayout:
    expected_units = set(range(template.unit_min, template.unit_max + 1))
    raw_groups = save.group_by_unit(tuple(template.contracts))
    if set(raw_groups) != expected_units:
        missing = sorted(expected_units - set(raw_groups))
        extra = sorted(set(raw_groups) - expected_units)
        raise RuntimeError(
            f"weapon physical unit layout differs; missing={missing[:8]} extra={extra[:8]}"
        )
    groups = raw_groups
    occupied = []
    empty_units = []
    used_slots = {}
    for unit in sorted(expected_units):
        fields = groups[unit]
        if set(fields) != set(template.contracts):
            raise RuntimeError(f"weapon unit {unit} has incomplete field coverage")
        values = {}
        for field_id, (kind, value_count) in template.contracts.items():
            record = fields[field_id]
            if record.kind != kind or record.value_count != value_count:
                raise RuntimeError(
                    f"weapon unit {unit} field {field_id} has invalid kind or width"
                )
            values[field_id] = normalized_record_values(save, record)
        state = classify_weapon_values(values, template, unit)
        if state == "empty":
            empty_units.append(unit)
            continue
        snapshot = WeaponSnapshot(unit=unit, values=values)
        previous = used_slots.get(snapshot.slot)
        if previous is not None:
            raise RuntimeError(
                f"duplicate weapon instance id {snapshot.slot} at units {previous} and {unit}"
            )
        used_slots[snapshot.slot] = unit
        occupied.append(snapshot)
    counter_record = required_record(
        save,
        template.counter_field,
        template.counter_unit,
        kind="uint",
        value_count=1,
    )
    counter = normalized_record_values(save, counter_record)[0]
    maximum_slot = max(used_slots, default=0)
    if counter < maximum_slot:
        raise RuntimeError(
            f"weapon instance counter {counter} is below occupied maximum {maximum_slot}"
        )
    return WeaponLayout(
        counter=counter,
        groups=groups,
        occupied=occupied,
        empty_units=empty_units,
    )


def desired_values(
    source: WeaponSnapshot | None,
    target: WeaponTarget,
    template: InstanceTemplate,
    slot: int,
) -> dict[int, tuple[int, ...]]:
    values = dict(template.empty if source is None else source.values)
    if not target.endgame and source is not None:
        if (
            source.values[2817] != template.empty[2817]
            or source.values[2818] != template.empty[2818]
        ):
            raise RuntimeError(
                f"base-only weapon {target.official_id} has an unsupported transcendence state"
            )
    structural_flags = (
        template.owned_flag
        | template.awakening_flag
        | template.transcendence_flag
    )
    flags = (values[2815][0] & ~structural_flags) | template.owned_flag
    if target.expected_2807:
        flags |= template.awakening_flag
    if target.endgame:
        flags |= template.transcendence_flag
    values.update(
        {
            2802: (slot,),
            2803: (target.target_hash,),
            2804: (template.experience,),
            2805: (template.uncap,),
            2806: (template.plus,),
            2807: (target.expected_2807,),
            2815: (flags,),
            2817: (template.transcendence if target.endgame else 0,),
            2818: target.skill_vector if target.endgame else template.empty[2818],
        }
    )
    return values


def plan_weapons(
    layout: WeaponLayout,
    targets: list[WeaponTarget],
    alias_to_official: dict[int, str],
    template: InstanceTemplate,
) -> BuildPlan:
    slots = [snapshot.slot for snapshot in layout.occupied]
    duplicate_slots = sorted(slot for slot, count in Counter(slots).items() if count > 1)
    if duplicate_slots:
        raise RuntimeError(f"duplicate weapon instance ids: {duplicate_slots}")
    maximum_slot = max(slots, default=0)
    if layout.counter < maximum_slot:
        raise RuntimeError(
            f"weapon instance counter {layout.counter} is below occupied maximum {maximum_slot}"
        )
    target_by_id = {target.official_id: target for target in targets}
    candidates: dict[str, list[WeaponSnapshot]] = defaultdict(list)
    unknown = []
    for snapshot in layout.occupied:
        official_id = alias_to_official.get(snapshot.weapon_hash)
        if official_id is None:
            unknown.append(snapshot)
        else:
            candidates[official_id].append(snapshot)

    duplicate_officials = {
        official_id: sorted(snapshot.unit for snapshot in options)
        for official_id, options in candidates.items()
        if len(options) > 1
    }
    if duplicate_officials:
        details = ", ".join(
            f"{official_id} at units {units}"
            for official_id, units in sorted(duplicate_officials.items())
        )
        raise RuntimeError(f"duplicate official weapon instances: {details}")

    missing_count = sum(not candidates.get(target.official_id) for target in targets)
    if missing_count > len(layout.empty_units):
        raise RuntimeError(
            f"need {missing_count} canonical empty weapon units, found {len(layout.empty_units)}"
        )
    if layout.counter + missing_count > 0xFFFFFFFF:
        raise RuntimeError("weapon instance counter would overflow uint32")

    empty_units = iter(layout.empty_units)
    next_slot = layout.counter + 1
    planned = []
    for target in targets:
        options = candidates.get(target.official_id, [])
        if options:
            selected = options[0]
            desired = desired_values(selected, target, template, selected.slot)
            action = "unchanged" if desired == selected.values else "upgrade"
            planned.append(
                PlannedWeapon(
                    target=target,
                    unit=selected.unit,
                    slot=selected.slot,
                    action=action,
                    before_hash=selected.weapon_hash,
                    desired=desired,
                )
            )
            continue
        unit = next(empty_units)
        desired = desired_values(None, target, template, next_slot)
        planned.append(
            PlannedWeapon(
                target=target,
                unit=unit,
                slot=next_slot,
                action="create",
                before_hash=None,
                desired=desired,
            )
        )
        next_slot += 1
    if set(target_by_id) != {row.target.official_id for row in planned}:
        raise RuntimeError("weapon plan does not cover every official target")
    return BuildPlan(
        weapons=tuple(planned),
        final_counter=next_slot - 1,
        unknown=tuple(sorted(unknown, key=lambda row: row.unit)),
    )


def load_character_hashes(path: Path) -> dict[int, str]:
    payload = read_json(path)
    items = payload.get("items")
    if payload.get("count") != 29 or not isinstance(items, list) or len(items) != 29:
        raise RuntimeError("character catalog must contain exactly 29 entries")
    result = {}
    for item in items:
        hash_value = parse_hash(item["hash"], f"{item['id']} character hash")
        if hash_value in result:
            raise RuntimeError(f"duplicate character hash for {item['id']}")
        result[hash_value] = str(item["id"])
    return result


def character_equipment(
    save: GBFRSaveData,
    character_hashes: dict[int, str],
) -> dict[str, tuple[int, UnitRecord, int]]:
    result = {}
    for unit, fields in save.group_by_unit((1301, 1402)).items():
        identity = fields.get(1301)
        if identity is None:
            continue
        character_id = character_hashes.get(
            int(save.get_first_value(identity, 0)) & 0xFFFFFFFF
        )
        if character_id is None:
            continue
        if character_id in result or 1402 not in fields:
            raise RuntimeError(f"character equipment mapping is invalid for {character_id}")
        record = fields[1402]
        if record.kind != "uint" or record.value_count != 1:
            raise RuntimeError(f"character {character_id} field 1402 is invalid")
        result[character_id] = (
            unit,
            record,
            int(save.get_first_value(record, 0)) & 0xFFFFFFFF,
        )
    return result


def plan_equipment(
    equipment: dict[str, tuple[int, UnitRecord, int]],
    plan: BuildPlan,
    policy: str,
) -> list[dict[str, int | str]]:
    if policy == "preserve":
        return []
    by_character: dict[str, list[PlannedWeapon]] = defaultdict(list)
    for row in plan.weapons:
        by_character[row.target.character_id].append(row)
    if set(equipment) != set(by_character) or len(equipment) != 29:
        missing = sorted(set(by_character) - set(equipment))
        raise RuntimeError(
            f"strongest equip policy requires all 29 character mappings; missing={missing}"
        )
    changes = []
    target_slots = set()
    for character_id in sorted(by_character):
        strongest = max(
            by_character[character_id],
            key=lambda row: row.target.collection_slot,
        )
        unit, _record, current_slot = equipment[character_id]
        if strongest.slot in target_slots:
            raise RuntimeError(f"strongest weapon slot {strongest.slot} is not unique")
        target_slots.add(strongest.slot)
        if current_slot != strongest.slot:
            changes.append(
                {
                    "character_id": character_id,
                    "character_unit": unit,
                    "before_slot": current_slot,
                    "after_slot": strongest.slot,
                    "weapon_unit": strongest.unit,
                }
            )
    return changes


def story_digest(save: GBFRSaveData) -> str:
    rows = []
    for field_id in MAIN_FIELDS:
        for record in save.find(id_type=field_id):
            rows.append((record.kind, field_id, record.unit_id, list(save.get_values(record))))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    encoded = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def full_snapshot(
    save: GBFRSaveData,
) -> dict[tuple[str, int, int, int], tuple[Any, ...]]:
    return {
        (record.kind, record.index, record.id_type, record.unit_id): tuple(
            save.get_values(record)
        )
        for record in save.records
    }


def changed_keys(
    before: dict[tuple[str, int, int, int], tuple[Any, ...]],
    after: dict[tuple[str, int, int, int], tuple[Any, ...]],
) -> set[tuple[str, int, int, int]]:
    if set(before) != set(after):
        raise RuntimeError("save record keys changed")
    return {key for key in before if before[key] != after[key]}


def set_record_values(save: GBFRSaveData, record: UnitRecord, values: tuple[int, ...]) -> None:
    save.set_values(record, list(values))


def apply_plan(
    save: GBFRSaveData,
    layout: WeaponLayout,
    plan: BuildPlan,
    equipment: dict[str, tuple[int, UnitRecord, int]],
    equipment_changes: list[dict[str, int | str]],
    template: InstanceTemplate,
) -> set[tuple[str, int, int, int]]:
    allowed = set()
    for row in plan.weapons:
        if row.action == "unchanged":
            continue
        fields = layout.groups[row.unit]
        for field_id, values in row.desired.items():
            record = fields[field_id]
            current = normalized_record_values(save, record)
            if current == values:
                continue
            set_record_values(save, record, values)
            allowed.add((record.kind, record.index, record.id_type, record.unit_id))
    if plan.final_counter != layout.counter:
        counter = required_record(
            save,
            template.counter_field,
            template.counter_unit,
            kind="uint",
            value_count=1,
        )
        save.set_first_value(counter, plan.final_counter)
        allowed.add((counter.kind, counter.index, counter.id_type, counter.unit_id))
    for change in equipment_changes:
        record = equipment[str(change["character_id"])][1]
        save.set_first_value(record, int(change["after_slot"]))
        allowed.add((record.kind, record.index, record.id_type, record.unit_id))
    return allowed


def verify_preserved_units(
    output_layout: WeaponLayout,
    preserved: dict[int, dict[int, tuple[int, ...]]],
) -> None:
    by_unit = {snapshot.unit: snapshot for snapshot in output_layout.occupied}
    for unit, values in preserved.items():
        snapshot = by_unit.get(unit)
        if snapshot is None or snapshot.values != values:
            raise RuntimeError(f"preserved weapon unit {unit} changed")


def write_audit(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline source save copy")
    parser.add_argument("output", type=Path, help="Separate offline candidate save")
    parser.add_argument("--characters", type=Path, default=DEFAULT_CHARACTERS)
    parser.add_argument("--weapons", type=Path, default=DEFAULT_WEAPONS)
    parser.add_argument("--rebuild-catalog", type=Path, default=DEFAULT_REBUILD)
    parser.add_argument("--identity-catalog", type=Path, default=DEFAULT_IDENTITIES)
    parser.add_argument("--instance-template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument(
        "--equip-policy",
        choices=("preserve", "strongest"),
        default="preserve",
    )
    parser.add_argument("--audit", type=Path, required=True)
    add_editor_argument(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = resolved(args.input)
    output_path = resolved(args.output)
    audit_path = resolved(args.audit)
    character_path = resolved(args.characters)
    weapon_path = resolved(args.weapons)
    rebuild_path = resolved(args.rebuild_catalog)
    identity_path = resolved(args.identity_catalog)
    template_path = resolved(args.instance_template)
    for path, label in (
        (input_path, "input save"),
        (character_path, "character catalog"),
        (weapon_path, "weapon catalog"),
        (rebuild_path, "weapon rebuild catalog"),
        (identity_path, "weapon runtime identity catalog"),
        (template_path, "weapon instance template"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if len({input_path, output_path, audit_path}) != 3:
        raise RuntimeError("input, output, and audit paths must be distinct")
    if output_path.exists() or audit_path.exists():
        raise RuntimeError("refusing to overwrite an output or audit")
    refuse_live_output(output_path, "candidate")
    refuse_live_output(audit_path, "audit")

    template = load_template(template_path)
    targets, alias_to_official = load_targets(
        weapon_path,
        rebuild_path,
        identity_path,
    )
    character_hashes = load_character_hashes(character_path)
    source_sha = sha256_file(input_path)
    save = GBFRSaveData.open(input_path)
    if save.check_active_hash() is not True:
        raise RuntimeError("input save active hash is invalid")
    header = dict(save.container.header)
    if (
        header.get("main_version") != template.main_version
        or header.get("sub_version") != template.sub_version
    ):
        raise RuntimeError(
            "save version is not covered by the Relink 2.0.2 weapon instance template"
        )
    payload_size = save.container.payload_size
    record_count = len(save.records)
    before_story = story_digest(save)
    before_snapshot = full_snapshot(save)
    layout = read_layout(save, template)
    plan = plan_weapons(layout, targets, alias_to_official, template)
    equipment = character_equipment(save, character_hashes)
    equipment_changes = plan_equipment(equipment, plan, args.equip_policy)

    selected_units = {row.unit for row in plan.weapons}
    preserved = {
        snapshot.unit: snapshot.values
        for snapshot in layout.occupied
        if snapshot.unit not in selected_units
    }
    allowed = apply_plan(
        save,
        layout,
        plan,
        equipment,
        equipment_changes,
        template,
    )
    after_snapshot = full_snapshot(save)
    actual_changes = changed_keys(before_snapshot, after_snapshot)
    if actual_changes != allowed:
        unexpected = sorted(actual_changes ^ allowed)
        raise RuntimeError(f"weapon transform change whitelist mismatch: {unexpected[:8]}")
    if story_digest(save) != before_story:
        raise RuntimeError("protected main-story fields changed in memory")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save.save_as(output_path, update_hash=True)
    if sha256_file(input_path) != source_sha:
        raise RuntimeError("offline input save changed during the run")

    output = GBFRSaveData.open(output_path)
    if output.check_active_hash() is not True:
        raise RuntimeError("output save active hash is invalid")
    if dict(output.container.header) != header:
        raise RuntimeError("Steam/account wrapper metadata changed")
    if output.container.payload_size != payload_size or len(output.records) != record_count:
        raise RuntimeError("save payload size or record count changed")
    if full_snapshot(output) != after_snapshot:
        raise RuntimeError("serialized records differ from verified in-memory data")
    if story_digest(output) != before_story:
        raise RuntimeError("protected main-story fields changed on disk")

    output_layout = read_layout(output, template)
    verify_preserved_units(output_layout, preserved)
    post_plan = plan_weapons(output_layout, targets, alias_to_official, template)
    remaining_actions = [
        row for row in post_plan.weapons if row.action != "unchanged"
    ]
    if remaining_actions or post_plan.final_counter != output_layout.counter:
        raise RuntimeError("weapon transform is not idempotent after serialization")
    output_equipment = character_equipment(output, character_hashes)
    remaining_equipment = plan_equipment(
        output_equipment,
        post_plan,
        args.equip_policy,
    )
    if remaining_equipment:
        raise RuntimeError("strongest weapon equipment did not persist")

    action_counts = Counter(row.action for row in plan.weapons)
    audit = {
        "schema_version": 1,
        "success": True,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": file_metadata(input_path, GBFRSaveData.open(input_path)),
        "output": file_metadata(output_path, output),
        "catalog": {
            "weapons": str(weapon_path),
            "weapons_sha256": sha256_file(weapon_path),
            "rebuild": str(rebuild_path),
            "rebuild_sha256": sha256_file(rebuild_path),
            "identities": str(identity_path),
            "identities_sha256": sha256_file(identity_path),
            "template": str(template_path),
            "template_sha256": sha256_file(template_path),
            "counts": {
                "official_weapons": EXPECTED_OFFICIAL_WEAPONS,
                "endgame_runtime_weapons": EXPECTED_ENDGAME_WEAPONS,
                "base_only_weapons": EXPECTED_BASE_ONLY_WEAPONS,
                "runtime_identities": len(alias_to_official),
            },
        },
        "counts": {
            "official_weapons": len(plan.weapons),
            "endgame_runtime_weapons": sum(row.target.endgame for row in plan.weapons),
            "base_only_weapons": sum(not row.target.endgame for row in plan.weapons),
            "created": action_counts["create"],
            "upgraded": action_counts["upgrade"],
            "already_complete": action_counts["unchanged"],
            "preserved_unknown_instances": len(plan.unknown),
            "equipped_characters": len(output_equipment),
            "equipment_changes": len(equipment_changes),
        },
        "validation": {
            "active_hash_ok": True,
            "catalog_complete": True,
            "all_official_targets_complete": True,
            "runtime_vectors_complete": True,
            "instance_ids_unique": True,
            "counter_monotonic": True,
            "serialized_snapshot_verified": True,
            "idempotent_plan": True,
        },
        "policy": {
            "main_story_preserved": True,
            "wrapper_header_preserved": True,
            "payload_size_preserved": True,
            "record_count_preserved": True,
            "unknown_instances_preserved": True,
            "duplicate_official_instances_rejected": True,
            "canonical_empty_units_only": True,
            "no_user_forge_probe_required": True,
        },
        "mode": {"equip_policy": args.equip_policy},
        "layout": {
            "physical_units": template.unit_max - template.unit_min + 1,
            "occupied_before": len(layout.occupied),
            "occupied_after": len(output_layout.occupied),
            "empty_after": len(output_layout.empty_units),
            "counter_before": layout.counter,
            "counter_after": output_layout.counter,
        },
        "changes": {
            "created": [
                {
                    "official_id": row.target.official_id,
                    "unit": row.unit,
                    "slot": row.slot,
                    "target_hash": f"{row.target.target_hash:08X}",
                }
                for row in plan.weapons
                if row.action == "create"
            ],
            "upgraded": [
                {
                    "official_id": row.target.official_id,
                    "unit": row.unit,
                    "slot": row.slot,
                    "before_hash": f"{row.before_hash:08X}",
                    "target_hash": f"{row.target.target_hash:08X}",
                }
                for row in plan.weapons
                if row.action == "upgrade"
            ],
            "equipment": equipment_changes,
        },
    }
    write_audit(audit_path, audit)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "audit": str(audit_path),
                "official_weapons": len(plan.weapons),
                "created": action_counts["create"],
                "upgraded": action_counts["upgrade"],
                "equip_policy": args.equip_policy,
                "equipment_changes": len(equipment_changes),
                "active_hash_ok": True,
                "main_story_preserved": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
