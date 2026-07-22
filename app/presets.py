import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter


SUPPORTED_SCHEMA_VERSION = 1
VALID_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SUPPORTED_PLACEHOLDERS = {
    "python",
    "input",
    "output",
    "audit",
    "root",
    "editor_root",
    "run_dir",
    "save_dir",
}


class PresetError(RuntimeError):
    """A preset pack is malformed or cannot be selected safely."""


@dataclass(frozen=True)
class PresetStep:
    id: str
    name: str
    command: tuple[str, ...]
    kind: str = "transform"
    timeout_seconds: int = 1800
    audit_required: bool = True
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PresetPack:
    id: str
    name: str
    description: str
    steps: tuple[PresetStep, ...]
    source_path: Path
    preserve_header: bool = True
    preserve_payload_size: bool = True
    preserve_record_count: bool = True


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PresetError(f"{label} must be a non-empty string")
    return value.strip()


def _require_id(value: object, label: str) -> str:
    identifier = _require_string(value, label)
    if not VALID_ID.fullmatch(identifier):
        raise PresetError(
            f"{label} must match {VALID_ID.pattern!r}; found {identifier!r}"
        )
    return identifier


def _placeholders(values: tuple[str, ...] | list[str]) -> set[str]:
    found: set[str] = set()
    formatter = Formatter()
    for value in values:
        try:
            for _, field_name, _, _ in formatter.parse(value):
                if field_name:
                    found.add(field_name)
        except ValueError as exc:
            raise PresetError(f"invalid command format string {value!r}: {exc}") from exc
    return found


def _load_step(raw: object, *, preset_id: str, index: int) -> PresetStep:
    if not isinstance(raw, dict):
        raise PresetError(f"{preset_id}.steps[{index}] must be an object")
    step_id = _require_id(raw.get("id"), f"{preset_id}.steps[{index}].id")
    name = _require_string(raw.get("name", step_id), f"{preset_id}.{step_id}.name")
    kind = str(raw.get("kind", "transform")).strip().lower()
    if kind not in {"transform", "verify"}:
        raise PresetError(f"{preset_id}.{step_id}.kind must be transform or verify")

    command_raw = raw.get("command")
    if not isinstance(command_raw, list) or not command_raw:
        raise PresetError(f"{preset_id}.{step_id}.command must be a non-empty array")
    if not all(isinstance(value, str) and value for value in command_raw):
        raise PresetError(f"{preset_id}.{step_id}.command entries must be strings")
    command = tuple(command_raw)
    placeholders = _placeholders(command)
    unknown = sorted(placeholders - SUPPORTED_PLACEHOLDERS)
    if unknown:
        raise PresetError(
            f"{preset_id}.{step_id}.command uses unsupported placeholders: {unknown}"
        )
    if "input" not in placeholders:
        raise PresetError(f"{preset_id}.{step_id}.command must reference {{input}}")
    if kind == "transform" and "output" not in placeholders:
        raise PresetError(
            f"{preset_id}.{step_id}.transform command must reference {{output}}"
        )

    audit_required = raw.get("audit_required", True)
    if not isinstance(audit_required, bool):
        raise PresetError(f"{preset_id}.{step_id}.audit_required must be boolean")
    if audit_required and "audit" not in placeholders:
        raise PresetError(
            f"{preset_id}.{step_id} requires an audit but command omits {{audit}}"
        )

    timeout_seconds = raw.get("timeout_seconds", 1800)
    if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 86_400:
        raise PresetError(
            f"{preset_id}.{step_id}.timeout_seconds must be between 1 and 86400"
        )

    env_raw = raw.get("env", {})
    if not isinstance(env_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in env_raw.items()
    ):
        raise PresetError(f"{preset_id}.{step_id}.env must be a string map")
    env_placeholders = _placeholders(list(env_raw.values()))
    unknown_env = sorted(env_placeholders - SUPPORTED_PLACEHOLDERS)
    if unknown_env:
        raise PresetError(
            f"{preset_id}.{step_id}.env uses unsupported placeholders: {unknown_env}"
        )

    return PresetStep(
        id=step_id,
        name=name,
        command=command,
        kind=kind,
        timeout_seconds=timeout_seconds,
        audit_required=audit_required,
        env=dict(env_raw),
    )


def load_preset(path: Path) -> PresetPack:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PresetError(f"could not read preset {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PresetError(f"preset root must be an object: {path}")
    if raw.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise PresetError(
            f"{path.name}: schema_version must be {SUPPORTED_SCHEMA_VERSION}"
        )

    preset_id = _require_id(raw.get("id"), f"{path.name}.id")
    name = _require_string(raw.get("name"), f"{preset_id}.name")
    description = _require_string(
        raw.get("description"), f"{preset_id}.description"
    )
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise PresetError(f"{preset_id}.steps must be a non-empty array")
    steps = tuple(
        _load_step(step, preset_id=preset_id, index=index)
        for index, step in enumerate(steps_raw)
    )
    step_ids = [step.id for step in steps]
    duplicates = sorted({step_id for step_id in step_ids if step_ids.count(step_id) > 1})
    if duplicates:
        raise PresetError(f"{preset_id} repeats step ids: {duplicates}")

    invariants = raw.get("invariants", {})
    if not isinstance(invariants, dict):
        raise PresetError(f"{preset_id}.invariants must be an object")
    values = {
        "preserve_header": invariants.get("preserve_header", True),
        "preserve_payload_size": invariants.get("preserve_payload_size", True),
        "preserve_record_count": invariants.get("preserve_record_count", True),
    }
    if not all(isinstance(value, bool) for value in values.values()):
        raise PresetError(f"{preset_id}.invariants values must be boolean")

    return PresetPack(
        id=preset_id,
        name=name,
        description=description,
        steps=steps,
        source_path=path.resolve(),
        **values,
    )


def load_presets(directory: Path) -> dict[str, PresetPack]:
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise PresetError(f"preset directory does not exist: {directory}")
    result: dict[str, PresetPack] = {}
    for path in sorted(directory.glob("*.json")):
        preset = load_preset(path)
        if preset.id in result:
            raise PresetError(
                f"duplicate preset id {preset.id!r}: {result[preset.id].source_path}, {path}"
            )
        result[preset.id] = preset
    if not result:
        raise PresetError(f"no preset packs were found in {directory}")
    return result


def render_values(values: tuple[str, ...] | list[str], context: dict[str, str]) -> list[str]:
    try:
        return [value.format_map(context) for value in values]
    except KeyError as exc:
        raise PresetError(f"missing command placeholder value: {exc.args[0]}") from exc

