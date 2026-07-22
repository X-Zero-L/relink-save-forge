import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


GAME_EXECUTABLE = "granblue_fantasy_relink.exe"
SAVE_FILENAME = "SaveData1.dat"


class OneClickError(RuntimeError):
    """A one-click safety check or operation failed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def files_equal(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            left_chunk = left.read(1024 * 1024)
            right_chunk = right.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class EventLogger:
    def __init__(self, run_dir: Path, *, echo: bool = True) -> None:
        self.run_dir = run_dir
        self.text_path = run_dir / "run.log"
        self.events_path = run_dir / "events.jsonl"
        self.echo = echo
        run_dir.mkdir(parents=True, exist_ok=True)

    def event(self, level: str, message: str, **details: object) -> None:
        record = {
            "created_utc": utc_now(),
            "level": level,
            "message": message,
            **details,
        }
        line = f"[{record['created_utc']}] {level.upper()}: {message}"
        with self.text_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")
            if details:
                stream.write(json.dumps(details, ensure_ascii=False, sort_keys=True) + "\n")
        with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        if self.echo:
            print(line, flush=True)

    def command_output(self, step_id: str, output: str) -> None:
        if not output:
            return
        with self.text_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"--- {step_id} output ---\n")
            stream.write(output)
            if not output.endswith("\n"):
                stream.write("\n")


def default_save_path() -> Path:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    return local_app_data / "GBFR" / "Saved" / "SaveGames" / SAVE_FILENAME


def resolve_save_path(value: Path | None) -> Path:
    candidate = default_save_path() if value is None else value.expanduser()
    if candidate.is_dir():
        candidate = candidate / SAVE_FILENAME
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise OneClickError(f"SaveData1.dat does not exist: {candidate}")
    return candidate


def is_game_running(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    if os.name != "nt":
        return False
    try:
        completed = run(
            [
                "tasklist",
                "/FI",
                f"IMAGENAME eq {GAME_EXECUTABLE}",
                "/FO",
                "CSV",
                "/NH",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise OneClickError(f"could not run tasklist: {exc}") from exc
    if completed.returncode not in (0, 1):
        raise OneClickError(
            f"tasklist failed with exit code {completed.returncode}: {completed.stderr.strip()}"
        )
    return GAME_EXECUTABLE.lower() in completed.stdout.lower()


def require_game_closed() -> None:
    if is_game_running():
        raise OneClickError(
            "Granblue Fantasy: Relink is running. Exit the game completely before editing."
        )


@dataclass(frozen=True)
class SaveSummary:
    path: str
    sha256: str
    size: int
    active_hash_ok: bool
    header: dict
    payload_size: int
    record_count: int

    def to_dict(self) -> dict:
        return asdict(self)


class SaveValidator:
    def __init__(self, editor_root: Path) -> None:
        self.editor_root = editor_root.expanduser().resolve()
        module_path = self.editor_root / "gbfr_editor" / "core" / "gbfr_save.py"
        if not module_path.is_file():
            raise OneClickError(
                f"GBFR-Save-Editor core was not found under {self.editor_root}"
            )
        module_suffix = hashlib.sha256(str(module_path).encode("utf-8")).hexdigest()[:16]
        module_name = f"_relink_gbfr_save_{module_suffix}"
        module = sys.modules.get(module_name)
        if module is None:
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec is None or spec.loader is None:
                raise OneClickError(f"could not load save editor module: {module_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)
                raise
        self._save_type = module.GBFRSaveData

    def inspect(self, path: Path) -> SaveSummary:
        path = path.expanduser().resolve()
        try:
            save = self._save_type.open(path)
            active_hash_ok = save.check_active_hash() is True
        except Exception as exc:
            raise OneClickError(f"could not open save {path}: {exc}") from exc
        if not active_hash_ok:
            raise OneClickError(f"save active hash is invalid: {path}")
        return SaveSummary(
            path=str(path),
            sha256=sha256_file(path),
            size=path.stat().st_size,
            active_hash_ok=True,
            header=dict(save.container.header or {}),
            payload_size=int(save.container.payload_size),
            record_count=len(save.records),
        )


def save_metadata(path: Path) -> dict:
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def directory_snapshot(directory: Path) -> dict[str, dict]:
    directory = directory.resolve()
    result: dict[str, dict] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise OneClickError(f"save directory contains an unsupported symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        result[relative] = save_metadata(path)
    return result
