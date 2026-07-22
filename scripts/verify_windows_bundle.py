import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path, PurePosixPath


FORBIDDEN_DATA_SUFFIXES = (".dat", ".db", ".sqlite", ".sqlite3")
FORBIDDEN_CACHE_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "downloads",
}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".cmd",
    ".csv",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".pth",
    ".py",
    ".toml",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
STEAM_ID64_PATTERN = re.compile(r"(?<!\d)7656119\d{10}(?!\d)")
LOCAL_PATH_PATTERNS = (
    re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]"),
    re.compile(r"/(?:home|Users)/[^/\s]+/"),
)
MAX_MEMBER_SIZE = 128 * 1024 * 1024
MAX_ARCHIVE_SIZE = 512 * 1024 * 1024


class BundleVerificationError(RuntimeError):
    """A release archive failed a production-readiness check."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def normalized_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise BundleVerificationError(f"unsafe ZIP member path: {info.filename!r}")
    return path


def inspect_archive(archive: Path) -> tuple[str, list[zipfile.ZipInfo]]:
    try:
        bundle = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BundleVerificationError(f"could not open release ZIP {archive}: {exc}") from exc

    with bundle:
        infos = bundle.infolist()
        if not infos:
            raise BundleVerificationError("release ZIP is empty")

        roots: set[str] = set()
        seen: set[str] = set()
        total_size = 0
        for info in infos:
            path = normalized_member(info)
            if not path.parts:
                continue
            roots.add(path.parts[0])
            normalized = path.as_posix().casefold()
            if normalized in seen:
                raise BundleVerificationError(f"duplicate ZIP member path: {path}")
            seen.add(normalized)
            if info.flag_bits & 0x1:
                raise BundleVerificationError(f"encrypted ZIP member is not allowed: {path}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise BundleVerificationError(f"symbolic link is not allowed in bundle: {path}")
            if info.file_size > MAX_MEMBER_SIZE:
                raise BundleVerificationError(f"ZIP member is unexpectedly large: {path}")
            total_size += info.file_size
            if total_size > MAX_ARCHIVE_SIZE:
                raise BundleVerificationError("release ZIP expands beyond the size limit")

            lowered_parts = tuple(part.casefold() for part in path.parts)
            lowered_name = path.name.casefold()
            if not info.is_dir() and lowered_name.endswith(FORBIDDEN_DATA_SUFFIXES):
                raise BundleVerificationError(f"save/database file leaked into bundle: {path}")
            if any(part in FORBIDDEN_CACHE_PARTS for part in lowered_parts):
                raise BundleVerificationError(f"cache or download directory leaked into bundle: {path}")
            if "gbfr-save-editor" in lowered_parts or "gbfr_editor" in lowered_parts:
                raise BundleVerificationError(f"upstream editor source leaked into bundle: {path}")

        if len(roots) != 1:
            raise BundleVerificationError(
                f"release ZIP must contain one top-level directory; found {sorted(roots)}"
            )

        for info in infos:
            if info.is_dir():
                continue
            path = normalized_member(info)
            if path.suffix.casefold() not in TEXT_SUFFIXES:
                continue
            raw = bundle.read(info)
            if b"\0" in raw:
                continue
            try:
                content = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                continue
            if STEAM_ID64_PATTERN.search(content):
                raise BundleVerificationError(f"SteamID64 leaked into text file: {path}")
            if any(pattern.search(content) for pattern in LOCAL_PATH_PATTERNS):
                raise BundleVerificationError(f"absolute local path leaked into text file: {path}")

        return next(iter(roots)), infos


def verify_checksum_file(archive: Path, checksums: Path | None) -> None:
    if checksums is None:
        return
    try:
        lines = checksums.read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise BundleVerificationError(f"could not read {checksums}: {exc}") from exc
    entries: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        pieces = line.strip().split(maxsplit=1)
        if len(pieces) != 2:
            raise BundleVerificationError(f"invalid checksum line: {line!r}")
        digest, name = pieces
        entries[name.lstrip("*")] = digest.casefold()
    expected = entries.get(archive.name)
    actual = sha256_file(archive)
    if expected is None:
        raise BundleVerificationError(f"{checksums} has no entry for {archive.name}")
    if expected != actual:
        raise BundleVerificationError(
            f"release checksum mismatch for {archive.name}: {actual} != {expected}"
        )


def verify_internal_manifest(bundle_root: Path) -> None:
    manifest_path = bundle_root / "SHA256SUMS.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleVerificationError(f"invalid internal bundle manifest: {exc}") from exc
    files = manifest.get("files")
    if manifest.get("schema_version") != 1 or not isinstance(files, list):
        raise BundleVerificationError("internal bundle manifest has an unsupported schema")

    declared: dict[str, dict] = {}
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise BundleVerificationError("internal bundle manifest contains an invalid file entry")
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise BundleVerificationError(f"internal manifest path is unsafe: {relative}")
        key = relative.as_posix().casefold()
        if key in declared:
            raise BundleVerificationError(f"internal manifest path is duplicated: {relative}")
        declared[key] = entry

    actual: dict[str, Path] = {}
    for path in bundle_root.rglob("*"):
        if not path.is_file() or path == manifest_path:
            continue
        relative = path.relative_to(bundle_root).as_posix()
        actual[relative.casefold()] = path
    if declared.keys() != actual.keys():
        missing = sorted(declared.keys() - actual.keys())
        undeclared = sorted(actual.keys() - declared.keys())
        raise BundleVerificationError(
            f"internal manifest file set differs; missing={missing}, undeclared={undeclared}"
        )
    for key, path in actual.items():
        entry = declared[key]
        if entry.get("size") != path.stat().st_size:
            raise BundleVerificationError(f"internal manifest size differs for {path.name}")
        if str(entry.get("sha256", "")).casefold() != sha256_file(path):
            raise BundleVerificationError(f"internal manifest SHA-256 differs for {path.name}")


def run_checked(command: list[str], *, cwd: Path, timeout: int = 120) -> str:
    environment = os.environ.copy()
    environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise BundleVerificationError(
            f"command failed with exit code {completed.returncode}: {' '.join(command)}\n{output}"
        )
    return output


def verify_preset_listing(bundle_root: Path, python_executable: Path) -> None:
    pack_dir = bundle_root / "presets" / "packs"
    expected_ids = {
        json.loads(path.read_text(encoding="utf-8"))["id"]
        for path in pack_dir.glob("*.json")
    }
    if not expected_ids:
        raise BundleVerificationError("bundle contains no preset packs")
    output = run_checked(
        [str(python_executable), str(bundle_root / "app" / "launcher.py"), "--list-presets"],
        cwd=bundle_root,
    )
    listed_ids = {line.split("\t", 1)[0] for line in output.splitlines() if "\t" in line}
    if listed_ids != expected_ids:
        raise BundleVerificationError(
            f"portable launcher preset list differs; expected={sorted(expected_ids)}, "
            f"listed={sorted(listed_ids)}"
        )


FAKE_EDITOR = r'''
from pathlib import Path


class _Container:
    def __init__(self):
        self.header = {"steam_id": 123456789}
        self.payload_size = 777


class GBFRSaveData:
    def __init__(self, data):
        self.container = _Container()
        self.records = [object(), object(), object()]
        self._data = data

    @classmethod
    def open(cls, path):
        data = Path(path).read_bytes()
        if not data.startswith(b"VALID|"):
            raise ValueError("invalid fake save")
        return cls(data)

    def check_active_hash(self):
        return True
'''


FAKE_TRANSFORM = r'''
import argparse
import hashlib
import json
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("input", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--audit", type=Path, required=True)
args = parser.parse_args()
data = args.input.read_bytes().replace(b"state=0", b"state=1")
args.output.write_bytes(data)
args.audit.write_text(json.dumps({
    "schema_version": 1,
    "input": {"sha256": hashlib.sha256(args.input.read_bytes()).hexdigest().upper()},
    "output": {
        "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest().upper(),
        "size": args.output.stat().st_size,
        "record_count": 3,
        "active_hash_ok": True,
    },
    "counts": {"verified_records": 3},
    "validation": {"semantic_result_verified": True},
}), encoding="utf-8")
'''


TRANSACTION_HARNESS = r'''
import argparse
import sys
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("bundle_root", type=Path)
parser.add_argument("preset", type=Path)
parser.add_argument("save", type=Path)
parser.add_argument("editor_root", type=Path)
parser.add_argument("state_root", type=Path)
args = parser.parse_args()
sys.path.insert(0, str(args.bundle_root.resolve()))

from app.presets import load_preset
from app.transaction import PresetTransaction


result = PresetTransaction(
    bundle_root=args.bundle_root,
    state_root=args.state_root,
    preset=load_preset(args.preset),
    save_path=args.save,
    editor_root=args.editor_root,
    apply=True,
    game_guard=lambda: None,
).execute()
if result.status != "completed" or not result.deployed:
    raise RuntimeError(f"unexpected transaction result: {result}")
'''


def verify_two_pass_transaction(
    bundle_root: Path,
    python_executable: Path,
    fixture_root: Path,
) -> None:
    editor_core = fixture_root / "editor" / "gbfr_editor" / "core"
    editor_core.mkdir(parents=True)
    (editor_core / "gbfr_save.py").write_text(
        textwrap.dedent(FAKE_EDITOR).lstrip(), encoding="utf-8"
    )

    transform = fixture_root / "transform.py"
    transform.write_text(
        textwrap.dedent(FAKE_TRANSFORM).lstrip(), encoding="utf-8"
    )
    harness = fixture_root / "transaction_harness.py"
    harness.write_text(
        textwrap.dedent(TRANSACTION_HARNESS).lstrip(), encoding="utf-8"
    )
    packs = fixture_root / "packs"
    packs.mkdir()
    (packs / "ci-smoke.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "ci-smoke",
                "name": "CI two-pass smoke",
                "description": "Fake editor and save transaction smoke test",
                "invariants": {
                    "preserve_header": True,
                    "preserve_payload_size": True,
                    "preserve_record_count": True,
                },
                "steps": [
                    {
                        "id": "transform",
                        "command": [
                            "{python}",
                            str(transform),
                            "{input}",
                            "{output}",
                            "--audit",
                            "{audit}",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    save_dir = fixture_root / "live" / "SaveGames"
    save_dir.mkdir(parents=True)
    save_path = save_dir / "SaveData1.dat"
    save_path.write_bytes(b"VALID|state=0")
    (save_dir / "SystemData.dat").write_bytes(b"SYSTEM")
    nested = save_dir / "nested"
    nested.mkdir()
    (nested / "cloud.txt").write_text("cloud", encoding="utf-8")
    state_root = fixture_root / "state"

    run_checked(
        [
            str(python_executable),
            str(harness),
            str(bundle_root),
            str(packs / "ci-smoke.json"),
            str(save_path),
            str(fixture_root / "editor"),
            str(state_root),
        ],
        cwd=bundle_root,
    )

    if save_path.read_bytes() != b"VALID|state=1":
        raise BundleVerificationError("portable launcher did not deploy the fake candidate")
    run_dirs = [path for path in (state_root / "runs").iterdir() if path.is_dir()]
    backup_dirs = [path for path in (state_root / "backups").iterdir() if path.is_dir()]
    if len(run_dirs) != 1 or len(backup_dirs) != 1:
        raise BundleVerificationError("portable transaction produced an unexpected run layout")
    run_dir = run_dirs[0]
    session = json.loads((run_dir / "session.json").read_text(encoding="utf-8"))
    if session.get("status") != "completed":
        raise BundleVerificationError("portable transaction did not reach completed state")
    if session.get("idempotency", {}).get("byte_identical") is not True:
        raise BundleVerificationError("portable transaction did not prove byte idempotency")
    first = run_dir / "pass-1" / "01-transform.dat"
    second = run_dir / "pass-2" / "01-transform.dat"
    if first.read_bytes() != second.read_bytes():
        raise BundleVerificationError("portable transaction passes produced different bytes")
    backup_save = backup_dirs[0] / "SaveGames"
    if (backup_save / "SaveData1.dat").read_bytes() != b"VALID|state=0":
        raise BundleVerificationError("portable transaction did not preserve the original save backup")
    if (backup_save / "SystemData.dat").read_bytes() != b"SYSTEM":
        raise BundleVerificationError("portable transaction did not back up sibling save files")
    if not (backup_save / "nested" / "cloud.txt").is_file():
        raise BundleVerificationError("portable transaction did not back up nested save files")


def verify_bundle(archive: Path, checksums: Path | None) -> None:
    archive = archive.resolve()
    if not archive.is_file():
        raise BundleVerificationError(f"release ZIP does not exist: {archive}")
    verify_checksum_file(archive, checksums.resolve() if checksums else None)
    root_name, _ = inspect_archive(archive)

    with tempfile.TemporaryDirectory(prefix="relink-bundle-smoke-") as temporary:
        temporary_root = Path(temporary)
        extract_root = temporary_root / "extract"
        extract_root.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extract_root)
        bundle_root = extract_root / root_name
        if not bundle_root.is_dir():
            raise BundleVerificationError(f"bundle root was not extracted: {bundle_root}")
        required = [
            bundle_root / "RelinkSaveForge.cmd",
            bundle_root / "app" / "launcher.py",
            bundle_root / "runtime" / "python" / "python.exe",
            bundle_root / "SHA256SUMS.json",
        ]
        missing = [str(path.relative_to(bundle_root)) for path in required if not path.is_file()]
        if missing:
            raise BundleVerificationError(f"bundle is missing required runtime files: {missing}")

        verify_internal_manifest(bundle_root)
        python_executable = bundle_root / "runtime" / "python" / "python.exe"
        verify_preset_listing(bundle_root, python_executable)
        verify_two_pass_transaction(
            bundle_root,
            python_executable,
            temporary_root / "fixture",
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify a Windows release ZIP with its bundled portable Python runtime."
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--checksums", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        verify_bundle(args.archive, args.checksums)
    except (BundleVerificationError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"bundle verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"bundle verification passed: {args.archive.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
