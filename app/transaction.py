import json
import os
import shutil
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.locking import SaveLock
from app.presets import PresetPack, PresetStep, render_values
from app.runtime import (
    EventLogger,
    OneClickError,
    SaveSummary,
    SaveValidator,
    atomic_write_json,
    compact_utc_stamp,
    directory_snapshot,
    files_equal,
    require_game_closed,
    sha256_file,
    utc_now,
)


RECOVERABLE_STATES = {"deploying", "deployed_unverified", "rollback_failed"}


class TransactionError(OneClickError):
    """The offline build, deployment, or rollback failed."""


@dataclass(frozen=True)
class BackupResult:
    directory: Path
    save_copy: Path
    manifest: Path
    files: dict[str, dict]


@dataclass(frozen=True)
class TransactionResult:
    status: str
    run_dir: Path
    backup_dir: Path
    candidate: Path
    source_sha256: str
    candidate_sha256: str
    deployed: bool


@dataclass(frozen=True)
class RestoreEntry:
    relative: str
    source: Path
    target: Path
    sha256: str
    size: int


def _assert_outside(child: Path, parent: Path, label: str) -> None:
    child = child.resolve()
    parent = parent.resolve()
    if child == parent or parent in child.parents:
        raise TransactionError(f"{label} must be outside the live save directory: {child}")


def create_full_backup(
    save_dir: Path,
    backup_root: Path,
    *,
    session_id: str,
    logger: EventLogger,
) -> BackupResult:
    save_dir = save_dir.resolve()
    backup_root = backup_root.resolve()
    _assert_outside(backup_root, save_dir, "backup root")
    target = backup_root / f"gbfr-save-backup-{compact_utc_stamp()}-{session_id[-8:]}"
    backup_save_dir = target / "SaveGames"
    if target.exists():
        raise TransactionError(f"backup directory already exists: {target}")

    logger.event("info", "Scanning live SaveGames directory before backup", path=str(save_dir))
    before = directory_snapshot(save_dir)
    if not before:
        raise TransactionError(f"live save directory is empty: {save_dir}")

    target.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copytree(save_dir, backup_save_dir, copy_function=shutil.copy2)
        after = directory_snapshot(save_dir)
        copied = directory_snapshot(backup_save_dir)
        stable = before == after
        copy_exact = before == copied
        manifest_value = {
            "schema_version": 1,
            "created_utc": utc_now(),
            "session_id": session_id,
            "source_directory": str(save_dir),
            "backup_directory": str(backup_save_dir),
            "stable_source": stable,
            "copy_exact": copy_exact,
            "files": before,
            "source_after_copy": after,
            "backup_files": copied,
        }
        manifest = target / "manifest-sha256.json"
        atomic_write_json(manifest, manifest_value)
        if not stable:
            raise TransactionError(
                "SaveGames changed while it was being backed up; the game or Steam Cloud may still be writing"
            )
        if not copy_exact:
            raise TransactionError("full SaveGames backup did not match the source snapshot")
    except Exception:
        logger.event("error", "Full SaveGames backup failed", backup=str(target))
        raise

    logger.event(
        "info",
        "Full SaveGames backup completed",
        backup=str(target),
        file_count=len(before),
    )
    return BackupResult(
        directory=target,
        save_copy=backup_save_dir,
        manifest=manifest,
        files=before,
    )


def _copy_with_fsync(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as source_stream, target.open("xb") as target_stream:
        shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
        target_stream.flush()
        os.fsync(target_stream.fileno())


def atomic_copy_replace(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        _copy_with_fsync(source, temporary)
        if not files_equal(source, temporary):
            raise TransactionError(f"staged deployment copy differs from source: {temporary}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransactionError(f"could not read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TransactionError(f"JSON root must be an object: {path}")
    return value


def _restore_entries(
    backup_save_dir: Path,
    save_dir: Path,
    files: dict,
) -> list[RestoreEntry]:
    entries: list[RestoreEntry] = []
    for relative, metadata in sorted(files.items()):
        if not isinstance(relative, str) or not relative or not isinstance(metadata, dict):
            raise TransactionError(f"invalid backup manifest entry: {relative!r}")
        expected_sha256 = metadata.get("sha256")
        expected_size = metadata.get("size")
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise TransactionError(f"backup manifest SHA-256 is invalid: {relative}")
        if not isinstance(expected_size, int) or expected_size < 0:
            raise TransactionError(f"backup manifest size is invalid: {relative}")

        source = (backup_save_dir / Path(relative)).resolve()
        if backup_save_dir not in source.parents:
            raise TransactionError(f"backup manifest path escapes SaveGames: {relative}")
        if (
            not source.is_file()
            or source.stat().st_size != expected_size
            or sha256_file(source) != expected_sha256
        ):
            raise TransactionError(f"backup file failed verification: {source}")

        target = (save_dir / Path(relative)).resolve()
        if save_dir not in target.parents:
            raise TransactionError(f"restore path escapes SaveGames: {relative}")
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise TransactionError(f"restore target is not a regular file: {target}")
        entries.append(
            RestoreEntry(
                relative=relative,
                source=source,
                target=target,
                sha256=expected_sha256,
                size=expected_size,
            )
        )
    return entries


def _read_wrapped_steam_id(path: Path) -> int | None:
    try:
        with path.open("rb") as stream:
            header = stream.read(12)
    except OSError:
        return None
    if len(header) < 12:
        return None
    return int(struct.unpack_from("<Q", header, 4)[0])


def _rollback_restore(
    committed: list[RestoreEntry],
    originals: dict[str, Path | None],
) -> list[str]:
    errors: list[str] = []
    for entry in reversed(committed):
        original = originals[entry.relative]
        try:
            if original is None:
                entry.target.unlink(missing_ok=True)
            else:
                atomic_copy_replace(original, entry.target)
                if not files_equal(original, entry.target):
                    raise TransactionError(
                        f"rollback copy differs from the staged original: {entry.target}"
                    )
        except Exception as exc:
            errors.append(f"{entry.relative}: {exc}")
    return errors


def restore_backup_directory(
    backup_dir: Path,
    save_dir: Path,
    *,
    validator: SaveValidator | None = None,
    logger: EventLogger | None = None,
) -> int:
    backup_dir = backup_dir.resolve()
    save_dir = save_dir.resolve()
    manifest_path = backup_dir / "manifest-sha256.json"
    manifest = _load_json(manifest_path)
    if manifest.get("stable_source") is not True or manifest.get("copy_exact") is not True:
        raise TransactionError(f"backup was not marked stable and exact: {backup_dir}")
    source_directory = manifest.get("source_directory")
    if not isinstance(source_directory, str):
        raise TransactionError(f"backup manifest has no source_directory: {manifest_path}")
    if Path(source_directory).expanduser().resolve() != save_dir:
        raise TransactionError(
            f"backup belongs to a different SaveGames directory: {source_directory}"
        )
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise TransactionError(f"backup manifest has no files: {manifest_path}")
    backup_save_dir = backup_dir / "SaveGames"
    entries = _restore_entries(backup_save_dir, save_dir, files)

    primary_relative = "SaveData1.dat"
    primary_metadata = files.get(primary_relative)
    if not isinstance(primary_metadata, dict):
        raise TransactionError(f"backup manifest has no {primary_relative} entry")
    backup_primary = backup_save_dir / primary_relative
    live_primary = save_dir / primary_relative
    if not backup_primary.is_file():
        raise TransactionError("backup SaveData1.dat is missing")
    if sha256_file(backup_primary) != primary_metadata.get("sha256"):
        raise TransactionError(f"backup primary failed SHA-256 verification: {backup_primary}")
    backup_summary = None
    if validator is not None:
        backup_summary = validator.inspect(backup_primary)
        if live_primary.is_file():
            try:
                live_summary = validator.inspect(live_primary)
            except OneClickError:
                live_steam_id = _read_wrapped_steam_id(live_primary)
                backup_steam_id = backup_summary.header.get("steam_id")
                if (
                    "main_version" in backup_summary.header
                    and isinstance(live_steam_id, int)
                    and isinstance(backup_steam_id, int)
                    and live_steam_id != backup_steam_id
                ):
                    raise TransactionError(
                        "backup SaveData1.dat Steam/account header does not match the current save"
                    )
            else:
                if live_summary.header != backup_summary.header:
                    raise TransactionError(
                        "backup SaveData1.dat Steam/account header does not match the current save"
                    )

    staging_root = save_dir / f".relink-restore-{uuid.uuid4().hex}"
    staged: dict[str, Path] = {}
    originals: dict[str, Path | None] = {}
    committed: list[RestoreEntry] = []
    cleanup_staging = True
    staging_root.mkdir(parents=True, exist_ok=False)
    try:
        for entry in entries:
            staged_path = staging_root / "staged" / Path(entry.relative)
            _copy_with_fsync(entry.source, staged_path)
            if (
                staged_path.stat().st_size != entry.size
                or sha256_file(staged_path) != entry.sha256
            ):
                raise TransactionError(f"staged restore file failed verification: {entry.relative}")
            staged[entry.relative] = staged_path

            if entry.target.is_file():
                original_path = staging_root / "original" / Path(entry.relative)
                _copy_with_fsync(entry.target, original_path)
                if not files_equal(entry.target, original_path):
                    raise TransactionError(
                        f"could not stage the current restore target: {entry.target}"
                    )
                originals[entry.relative] = original_path
            else:
                originals[entry.relative] = None

        try:
            for entry in entries:
                entry.target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged[entry.relative], entry.target)
                committed.append(entry)
            for entry in entries:
                if (
                    not entry.target.is_file()
                    or entry.target.stat().st_size != entry.size
                    or sha256_file(entry.target) != entry.sha256
                ):
                    raise TransactionError(
                        f"restored file failed verification: {entry.target}"
                    )
            if validator is not None:
                restored_summary = validator.inspect(live_primary)
                if backup_summary is None or restored_summary.header != backup_summary.header:
                    raise TransactionError("restored SaveData1.dat header verification failed")
        except Exception as exc:
            rollback_errors = _rollback_restore(committed, originals)
            if rollback_errors:
                cleanup_staging = False
                raise TransactionError(
                    f"restore failed ({exc}); rollback also failed: "
                    + "; ".join(rollback_errors)
                    + f"; staged originals were preserved at {staging_root}"
                ) from exc
            raise
    finally:
        if cleanup_staging:
            shutil.rmtree(staging_root, ignore_errors=True)

    restored = len(entries)
    if logger:
        logger.event(
            "warning",
            "Restored all manifest files from the SaveGames backup",
            backup=str(backup_dir),
            restored_files=restored,
        )
    return restored


def latest_backup(
    backup_root: Path,
    *,
    save_dir: Path | None = None,
    validator: SaveValidator | None = None,
) -> Path:
    candidates = sorted(
        (
            path
            for path in backup_root.glob("gbfr-save-backup-*")
            if (path / "manifest-sha256.json").is_file()
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    expected_save_dir = save_dir.expanduser().resolve() if save_dir else None
    rejected: list[str] = []
    for candidate in candidates:
        try:
            manifest = _load_json(candidate / "manifest-sha256.json")
            if manifest.get("stable_source") is not True:
                raise TransactionError("stable_source is not true")
            if manifest.get("copy_exact") is not True:
                raise TransactionError("copy_exact is not true")
            if expected_save_dir is not None:
                source_directory = manifest.get("source_directory")
                if not isinstance(source_directory, str):
                    raise TransactionError("source_directory is missing")
                if Path(source_directory).expanduser().resolve() != expected_save_dir:
                    raise TransactionError("source_directory does not match")
            files = manifest.get("files")
            primary_metadata = files.get("SaveData1.dat") if isinstance(files, dict) else None
            if not isinstance(primary_metadata, dict):
                raise TransactionError("SaveData1.dat metadata is missing")
            primary = candidate / "SaveGames" / "SaveData1.dat"
            if not primary.is_file():
                raise TransactionError("SaveData1.dat backup is missing")
            if sha256_file(primary) != primary_metadata.get("sha256"):
                raise TransactionError("SaveData1.dat backup SHA-256 is invalid")
            if validator is not None:
                validator.inspect(primary)
            return candidate
        except (OSError, OneClickError, TransactionError) as exc:
            rejected.append(f"{candidate.name}: {exc}")
    if rejected:
        raise TransactionError(
            "no valid backups were found; rejected: " + "; ".join(rejected)
        )
    raise TransactionError(f"no backups were found in {backup_root}")


def _summary_invariants(
    actual: SaveSummary,
    baseline: SaveSummary,
    preset: PresetPack,
    *,
    label: str,
) -> None:
    if preset.preserve_header and actual.header != baseline.header:
        raise TransactionError(f"{label}: Steam/account header changed")
    if preset.preserve_payload_size and actual.payload_size != baseline.payload_size:
        raise TransactionError(f"{label}: payload size changed")
    if preset.preserve_record_count and actual.record_count != baseline.record_count:
        raise TransactionError(f"{label}: save record count changed")


def _boolean_evidence(value: object) -> list[bool]:
    if isinstance(value, bool):
        return [value]
    if isinstance(value, dict):
        result: list[bool] = []
        for nested in value.values():
            result.extend(_boolean_evidence(nested))
        return result
    if isinstance(value, list):
        result = []
        for nested in value:
            result.extend(_boolean_evidence(nested))
        return result
    return []


def _has_positive_numeric_evidence(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, dict):
        return any(_has_positive_numeric_evidence(nested) for nested in value.values())
    if isinstance(value, list):
        return any(_has_positive_numeric_evidence(nested) for nested in value)
    return False


def validate_step_audit(
    audit_path: Path,
    *,
    step: PresetStep,
    input_path: Path,
    output_path: Path | None,
    output_summary: SaveSummary | None,
) -> dict:
    audit = _load_json(audit_path)
    schema_version = audit.get("schema_version")
    if schema_version is not None and schema_version != 1:
        raise TransactionError(f"step {step.id} audit schema_version must be 1")
    if audit.get("success") is False:
        raise TransactionError(f"step {step.id} audit reports failure")

    input_metadata = audit.get("input")
    if not isinstance(input_metadata, dict):
        raise TransactionError(f"step {step.id} audit has no input metadata")
    input_sha256 = input_metadata.get("sha256")
    if not isinstance(input_sha256, str) or input_sha256 != sha256_file(input_path):
        raise TransactionError(f"step {step.id} audit input SHA-256 differs")

    if output_path is not None:
        output_metadata = audit.get("output")
        if not isinstance(output_metadata, dict):
            raise TransactionError(f"step {step.id} audit has no output metadata")
        output_sha256 = output_metadata.get("sha256")
        if not isinstance(output_sha256, str) or output_sha256 != sha256_file(output_path):
            raise TransactionError(f"step {step.id} audit output SHA-256 differs")
        if "size" in output_metadata and output_metadata.get("size") != output_path.stat().st_size:
            raise TransactionError(f"step {step.id} audit output size differs")
        if (
            output_summary is not None
            and "record_count" in output_metadata
            and output_metadata.get("record_count") != output_summary.record_count
        ):
            raise TransactionError(f"step {step.id} audit record count differs")
        if output_metadata.get("active_hash_ok") is False:
            raise TransactionError(f"step {step.id} audit reports an invalid output hash")

    evidence_sections = [
        audit.get("validation"),
        audit.get("verification"),
        audit.get("policy"),
    ]
    boolean_evidence = [
        value
        for section in evidence_sections
        if isinstance(section, dict)
        for value in _boolean_evidence(section)
    ]
    if not boolean_evidence or not all(boolean_evidence):
        raise TransactionError(
            f"step {step.id} audit lacks successful semantic verification"
        )

    count_evidence: list[object] = [audit.get("counts"), audit.get("layout")]
    catalog = audit.get("catalog")
    if isinstance(catalog, dict):
        count_evidence.append(catalog.get("counts"))
    if not any(_has_positive_numeric_evidence(value) for value in count_evidence):
        raise TransactionError(f"step {step.id} audit lacks positive coverage counts")
    return audit


def prepare_step_command(command: list[str], bundle_root: Path) -> list[str]:
    if len(command) < 2:
        return command
    executable = Path(command[0]).expanduser().resolve()
    current_python = Path(sys.executable).expanduser().resolve()
    script = Path(command[1]).expanduser()
    if (
        os.path.normcase(str(executable)) != os.path.normcase(str(current_python))
        or script.suffix.lower() != ".py"
    ):
        return command
    script = script.resolve()
    bootstrap = (
        "import runpy,sys;"
        "script=sys.argv[1];"
        "sys.path[:0]=[sys.argv[2],sys.argv[3]];"
        "sys.argv=[script,*sys.argv[4:]];"
        "runpy.run_path(script,run_name='__main__')"
    )
    return [
        str(executable),
        "-I",
        "-c",
        bootstrap,
        str(script),
        str(bundle_root.resolve()),
        str(script.parent),
        *command[2:],
    ]


class PresetTransaction:
    def __init__(
        self,
        *,
        bundle_root: Path,
        state_root: Path,
        preset: PresetPack,
        save_path: Path,
        editor_root: Path,
        apply: bool,
        game_guard: Callable[[], None] = require_game_closed,
        echo: bool = True,
    ) -> None:
        self.bundle_root = bundle_root.resolve()
        self.state_root = state_root.expanduser().resolve()
        self.preset = preset
        self.save_path = save_path.resolve()
        self.save_dir = self.save_path.parent
        self.editor_root = editor_root.expanduser().resolve()
        self.apply = apply
        self.game_guard = game_guard
        self.session_id = (
            f"{compact_utc_stamp()}-{preset.id}-{uuid.uuid4().hex[:8]}"
        )
        self.run_dir = self.state_root / "runs" / self.session_id
        self.backup_root = self.state_root / "backups"
        _assert_outside(self.state_root, self.save_dir, "state root")
        _assert_outside(self.run_dir, self.save_dir, "run directory")
        _assert_outside(self.backup_root, self.save_dir, "backup root")
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.logger = EventLogger(self.run_dir, echo=echo)
        self.lock = SaveLock(
            lock_root=self.state_root / "locks",
            save_path=self.save_path,
            session_id=self.session_id,
            logger=self.logger,
        )
        self.session_path = self.run_dir / "session.json"
        self.validator = SaveValidator(self.editor_root)
        self.recovered_runs: list[Path] = []
        self.session: dict = {
            "schema_version": 1,
            "session_id": self.session_id,
            "preset_id": preset.id,
            "preset_path": str(preset.source_path),
            "status": "created",
            "applied": apply,
            "created_utc": utc_now(),
            "updated_utc": utc_now(),
            "run_dir": str(self.run_dir),
            "save_path": str(self.save_path),
            "save_dir": str(self.save_dir),
            "editor_root": str(self.editor_root),
        }
        self._write_session()

    def _write_session(self, **updates: object) -> None:
        self.session.update(updates)
        self.session["updated_utc"] = utc_now()
        atomic_write_json(self.session_path, self.session)

    def _render_context(
        self,
        *,
        current_input: Path,
        output: Path,
        audit: Path,
    ) -> dict[str, str]:
        return {
            "python": sys.executable,
            "input": str(current_input),
            "output": str(output),
            "audit": str(audit),
            "root": str(self.bundle_root),
            "editor_root": str(self.editor_root),
            "run_dir": str(self.run_dir),
            "save_dir": str(self.save_dir),
        }

    def _run_command(
        self,
        step: PresetStep,
        command: list[str],
        environment: dict[str, str],
        *,
        pass_number: int,
    ) -> None:
        self.logger.event(
            "info",
            f"Running pass {pass_number} step {step.id}",
            step_name=step.name,
            command=command,
        )
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=self.bundle_root,
                env=environment,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=step.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            output = "".join(part for part in (exc.stdout, exc.stderr) if isinstance(part, str))
            self.logger.command_output(step.id, output)
            raise TransactionError(
                f"step {step.id} timed out after {step.timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            raise TransactionError(f"could not start step {step.id}: {exc}") from exc

        output = completed.stdout + completed.stderr
        self.logger.command_output(step.id, output)
        self.logger.event(
            "info" if completed.returncode == 0 else "error",
            f"Step {step.id} exited with code {completed.returncode}",
            duration_seconds=round(time.monotonic() - started, 3),
        )
        if completed.returncode != 0:
            raise TransactionError(
                f"step {step.id} failed with exit code {completed.returncode}"
            )

    def _run_pass(
        self,
        source: Path,
        *,
        pass_number: int,
        baseline: SaveSummary,
    ) -> Path:
        pass_dir = self.run_dir / f"pass-{pass_number}"
        pass_dir.mkdir(parents=True, exist_ok=False)
        current_input = source
        for index, step in enumerate(self.preset.steps, 1):
            output = pass_dir / f"{index:02d}-{step.id}.dat"
            audit = pass_dir / f"{index:02d}-{step.id}-audit.json"
            context = self._render_context(
                current_input=current_input,
                output=output,
                audit=audit,
            )
            command = prepare_step_command(
                render_values(step.command, context),
                self.bundle_root,
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "GBFR_SAVE_EDITOR_ROOT": str(self.editor_root),
                    "PYTHONUTF8": "1",
                    "PYTHONIOENCODING": "utf-8",
                }
            )
            environment.update(
                zip(step.env.keys(), render_values(list(step.env.values()), context))
            )
            self._run_command(
                step,
                command,
                environment,
                pass_number=pass_number,
            )
            if step.audit_required and not audit.is_file():
                raise TransactionError(f"step {step.id} did not create its audit: {audit}")
            if step.kind == "verify":
                if step.audit_required:
                    validate_step_audit(
                        audit,
                        step=step,
                        input_path=current_input,
                        output_path=None,
                        output_summary=None,
                    )
                continue
            if not output.is_file():
                raise TransactionError(f"step {step.id} did not create its output: {output}")
            summary = self.validator.inspect(output)
            if step.audit_required:
                validate_step_audit(
                    audit,
                    step=step,
                    input_path=current_input,
                    output_path=output,
                    output_summary=summary,
                )
            _summary_invariants(
                summary,
                baseline,
                self.preset,
                label=f"pass {pass_number} step {step.id}",
            )
            current_input = output
        return current_input

    def _rollback_primary(self, backup: BackupResult, source_sha256: str) -> None:
        relative = self.save_path.relative_to(self.save_dir)
        backup_primary = backup.save_copy / relative
        if not backup_primary.is_file():
            raise TransactionError(f"backup is missing the original primary save: {backup_primary}")
        backup_sha = sha256_file(backup_primary)
        if backup_sha != source_sha256:
            raise TransactionError(
                f"backup primary SHA-256 mismatch before rollback: {backup_sha} != {source_sha256}"
            )
        atomic_copy_replace(backup_primary, self.save_path)
        restored_sha = sha256_file(self.save_path)
        if restored_sha != source_sha256:
            raise TransactionError(
                f"automatic rollback SHA-256 mismatch: {restored_sha} != {source_sha256}"
            )
        self.validator.inspect(self.save_path)
        self.logger.event(
            "warning",
            "Automatic rollback restored the original SaveData1.dat",
            sha256=restored_sha,
            backup=str(backup.directory),
        )

    def execute(self) -> TransactionResult:
        self.lock.acquire()
        try:
            self.recovered_runs = recover_incomplete_transactions(
                self.state_root,
                self.editor_root,
                save_path=self.save_path,
                lock=self.lock,
                game_guard=self.game_guard,
            )
            for recovered in self.recovered_runs:
                self.logger.event(
                    "warning",
                    "Recovered an interrupted deployment before starting the preset",
                    recovered_run=str(recovered),
                )
            return self._execute_locked()
        finally:
            self.lock.release()

    def _execute_locked(self) -> TransactionResult:
        backup: BackupResult | None = None
        source_sha256 = ""
        candidate = self.run_dir / "candidate.dat"
        deployment_started = False
        try:
            self.game_guard()
            source_summary = self.validator.inspect(self.save_path)
            source_sha256 = source_summary.sha256
            self._write_session(
                status="preflight",
                source=source_summary.to_dict(),
            )
            self.logger.event(
                "info",
                "Validated live source save",
                save=str(self.save_path),
                sha256=source_sha256,
            )

            backup = create_full_backup(
                self.save_dir,
                self.backup_root,
                session_id=self.session_id,
                logger=self.logger,
            )
            backup_primary = backup.save_copy / self.save_path.relative_to(self.save_dir)
            self._write_session(
                status="backed_up",
                backup_dir=str(backup.directory),
                backup_manifest=str(backup.manifest),
                backup_primary=str(backup_primary),
            )
            if sha256_file(self.save_path) != source_sha256:
                raise TransactionError("live SaveData1.dat changed immediately after backup")

            source_copy = self.run_dir / "source.dat"
            shutil.copy2(self.save_path, source_copy)
            if sha256_file(source_copy) != source_sha256:
                raise TransactionError("offline source copy does not match the live save")
            self._write_session(status="building", offline_source=str(source_copy))

            first_final = self._run_pass(
                source_copy,
                pass_number=1,
                baseline=source_summary,
            )
            first_summary = self.validator.inspect(first_final)
            _summary_invariants(
                first_summary,
                source_summary,
                self.preset,
                label="first final candidate",
            )
            second_final = self._run_pass(
                first_final,
                pass_number=2,
                baseline=source_summary,
            )
            second_summary = self.validator.inspect(second_final)
            _summary_invariants(
                second_summary,
                source_summary,
                self.preset,
                label="idempotency candidate",
            )
            if not files_equal(first_final, second_final):
                raise TransactionError(
                    "preset is not idempotent: the second complete run changed the save bytes"
                )
            shutil.copy2(first_final, candidate)
            candidate_summary = self.validator.inspect(candidate)
            if candidate_summary.sha256 != first_summary.sha256:
                raise TransactionError("final candidate copy changed unexpectedly")
            if sha256_file(source_copy) != source_sha256:
                raise TransactionError("offline source was modified by a preset step")
            self._write_session(
                status="verified_offline",
                candidate=candidate_summary.to_dict(),
                idempotency={
                    "second_pass_sha256": second_summary.sha256,
                    "byte_identical": True,
                },
            )
            self.logger.event(
                "info",
                "Offline candidate and second-pass idempotency verification succeeded",
                candidate=str(candidate),
                sha256=candidate_summary.sha256,
            )

            if not self.apply:
                return TransactionResult(
                    status="verified_offline",
                    run_dir=self.run_dir,
                    backup_dir=backup.directory,
                    candidate=candidate,
                    source_sha256=source_sha256,
                    candidate_sha256=candidate_summary.sha256,
                    deployed=False,
                )

            self.game_guard()
            if sha256_file(self.save_path) != source_sha256:
                raise TransactionError(
                    "live SaveData1.dat changed after the offline build; deployment was cancelled"
                )
            deployment_started = True
            self._write_session(
                status="deploying",
                candidate_sha256=candidate_summary.sha256,
            )
            atomic_copy_replace(candidate, self.save_path)
            self._write_session(status="deployed_unverified")

            deployed_summary = self.validator.inspect(self.save_path)
            if deployed_summary.sha256 != candidate_summary.sha256:
                raise TransactionError(
                    "post-deployment SaveData1.dat does not match the verified candidate"
                )
            _summary_invariants(
                deployed_summary,
                source_summary,
                self.preset,
                label="deployed save",
            )
            self._write_session(
                status="completed",
                completed_utc=utc_now(),
                deployed=deployed_summary.to_dict(),
            )
            self.logger.event(
                "info",
                "Atomic deployment and post-deployment reopen verification succeeded",
                save=str(self.save_path),
                sha256=deployed_summary.sha256,
            )
            return TransactionResult(
                status="completed",
                run_dir=self.run_dir,
                backup_dir=backup.directory,
                candidate=candidate,
                source_sha256=source_sha256,
                candidate_sha256=candidate_summary.sha256,
                deployed=True,
            )
        except Exception as exc:
            self.logger.event("error", "One-click transaction failed", error=str(exc))
            if deployment_started and backup is not None and source_sha256:
                try:
                    self._rollback_primary(backup, source_sha256)
                    self._write_session(
                        status="rolled_back",
                        error=str(exc),
                        rolled_back_utc=utc_now(),
                    )
                except Exception as rollback_exc:
                    self._write_session(
                        status="rollback_failed",
                        error=str(exc),
                        rollback_error=str(rollback_exc),
                    )
                    raise TransactionError(
                        f"transaction failed ({exc}); automatic rollback also failed "
                        f"({rollback_exc}); use backup {backup.directory}"
                    ) from rollback_exc
            else:
                self._write_session(status="failed", error=str(exc))
            if isinstance(exc, OneClickError):
                raise
            raise TransactionError(str(exc)) from exc


def recover_incomplete_transactions(
    state_root: Path,
    editor_root: Path,
    *,
    save_path: Path,
    lock: SaveLock | None = None,
    game_guard: Callable[[], None] = require_game_closed,
) -> list[Path]:
    state_root = state_root.expanduser().resolve()
    target_save = save_path.expanduser().resolve()
    owned_lock = lock is None
    if lock is None:
        lock = SaveLock(
            lock_root=state_root / "locks",
            save_path=target_save,
            session_id=f"recovery-{uuid.uuid4().hex}",
        )
        lock.acquire()
    elif not lock.acquired or lock.save_path != target_save:
        raise TransactionError("interrupted recovery requires the selected save lock")

    try:
        runs_root = state_root / "runs"
        if not runs_root.is_dir():
            return []
        validator = SaveValidator(editor_root)
        recovered: list[Path] = []
        for session_path in sorted(runs_root.glob("*/session.json")):
            session = _load_json(session_path)
            if (
                session.get("status") not in RECOVERABLE_STATES
                or session.get("applied") is not True
            ):
                continue
            raw_save_path = session.get("save_path")
            if not isinstance(raw_save_path, str) or not raw_save_path:
                continue
            session_save = Path(raw_save_path).expanduser().resolve()
            if session_save != target_save:
                continue

            raw_backup_primary = session.get("backup_primary")
            source = session.get("source")
            source_sha = source.get("sha256") if isinstance(source, dict) else None
            candidate_sha = session.get("candidate_sha256")
            if (
                not isinstance(raw_backup_primary, str)
                or not raw_backup_primary
                or not isinstance(source_sha, str)
                or not isinstance(candidate_sha, str)
            ):
                continue
            backup_primary = Path(raw_backup_primary).expanduser().resolve()
            if not backup_primary.is_file():
                continue
            backup_sha = sha256_file(backup_primary)
            if backup_sha != source_sha:
                raise TransactionError(
                    f"interrupted transaction backup SHA mismatch: {backup_sha} != {source_sha}"
                )

            game_guard()
            if target_save.exists() and not target_save.is_file():
                raise TransactionError(
                    f"interrupted transaction save target is not a file: {target_save}"
                )
            if not target_save.exists():
                atomic_copy_replace(backup_primary, target_save)
                session["recovery_action"] = "restored_missing_source"
            else:
                current_sha = sha256_file(target_save)
                if current_sha == candidate_sha:
                    atomic_copy_replace(backup_primary, target_save)
                    session["recovery_action"] = "restored_source"
                elif current_sha == source_sha:
                    session["recovery_action"] = "source_already_present"
                else:
                    session["status"] = "recovery_blocked_unknown_live_sha"
                    session["recovery_action"] = "none"
                    session["recovery_current_sha256"] = current_sha
                    session["updated_utc"] = utc_now()
                    atomic_write_json(session_path, session)
                    raise TransactionError(
                        "refusing interrupted-transaction recovery because live SaveData1.dat "
                        f"matches neither source nor candidate SHA-256: {target_save}"
                    )

            if sha256_file(target_save) != source_sha:
                raise TransactionError(
                    f"recovery failed to restore {target_save} from {backup_primary}"
                )
            validator.inspect(target_save)
            session["status"] = "recovered_after_interruption"
            session["recovered_utc"] = utc_now()
            session["updated_utc"] = utc_now()
            atomic_write_json(session_path, session)
            recovered.append(session_path.parent)
        return recovered
    finally:
        if owned_lock:
            lock.release()
