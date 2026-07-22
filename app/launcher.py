import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path


sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.presets import PresetError, PresetPack, load_presets  # noqa: E402
from app.locking import SaveLock  # noqa: E402
from app.runtime import (  # noqa: E402
    SAVE_FILENAME,
    OneClickError,
    SaveValidator,
    default_save_path,
    require_game_closed,
    resolve_save_path,
)
from app.transaction import (  # noqa: E402
    PresetTransaction,
    latest_backup,
    restore_backup_directory,
)


BUNDLE_ROOT = Path(__file__).resolve().parents[1]
PINNED_EDITOR_REPOSITORY = "https://github.com/xcier/GBFR-Save-Editor"
PINNED_EDITOR_COMMIT = "8fdb4497fcf0cf67a4b122062a00f8ff07cc3942"
PINNED_EDITOR_URL = (
    "https://codeload.github.com/xcier/GBFR-Save-Editor/zip/"
    + PINNED_EDITOR_COMMIT
)
PINNED_EDITOR_SHA256 = (
    "9DA34D0714796FD45D2E51C00DD55BA1AB6F92C6289B115BBF706845660A9E5A"
)


def default_state_root() -> Path:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    return local_app_data / "RelinkSaveForge"


def resolve_restore_save_path(value: Path | None) -> Path:
    candidate = default_save_path() if value is None else value.expanduser()
    if candidate.is_dir():
        candidate = candidate / SAVE_FILENAME
    elif candidate.name.lower() != SAVE_FILENAME.lower():
        if candidate.suffix:
            raise OneClickError(
                f"restore target must be {SAVE_FILENAME} or its SaveGames directory: {candidate}"
            )
        candidate = candidate / SAVE_FILENAME
    return candidate.resolve()


def _editor_core(candidate: Path) -> Path:
    return candidate / "gbfr_editor" / "core" / "gbfr_save.py"


def _is_release_bundle() -> bool:
    return (BUNDLE_ROOT / "SHA256SUMS.json").is_file()


def _read_json_object(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _directory_tree_sha256(root: Path, excluded: set[str]) -> str:
    row_digests = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        relative_parts = path.relative_to(root).parts
        if (
            relative in excluded
            or any(part.casefold() == "__pycache__" for part in relative_parts)
            or path.suffix.casefold() in {".pyc", ".pyo"}
        ):
            continue
        row = f"{relative}\0{path.stat().st_size}\0{_sha256_file(path)}"
        row_digests.append(hashlib.sha256(row.encode("utf-8")).hexdigest().upper())
    digest = hashlib.sha256()
    canonical = "".join(f"{row_digest}\n" for row_digest in sorted(row_digests))
    digest.update(canonical.encode("utf-8"))
    return digest.hexdigest().upper()


def _verified_bundled_editor(candidate: Path) -> bool:
    core = _editor_core(candidate)
    if not core.is_file():
        return False
    marker = _read_json_object(candidate / ".relink-save-forge-source.json")
    lock = _read_json_object(BUNDLE_ROOT / "runtime" / "runtime-lock.json")
    if marker is None or lock is None:
        return False
    editor = lock.get("editor")
    if not isinstance(editor, dict):
        return False
    expected_marker_fields = {
        "schema_version": 1,
        "component": "gbfr-save-editor",
        "repository": PINNED_EDITOR_REPOSITORY,
        "commit": PINNED_EDITOR_COMMIT,
        "url": PINNED_EDITOR_URL,
        "archive_sha256": PINNED_EDITOR_SHA256,
    }
    if not all(
        marker.get(key) == value for key, value in expected_marker_fields.items()
    ):
        return False
    try:
        core_sha256 = _sha256_file(core)
        tree_sha256 = _directory_tree_sha256(
            candidate,
            {".relink-save-forge-source.json"},
        )
    except OSError:
        return False
    return all(
        (
            lock.get("schema_version") == 1,
            editor.get("repository") == PINNED_EDITOR_REPOSITORY,
            editor.get("commit") == PINNED_EDITOR_COMMIT,
            editor.get("url") == PINNED_EDITOR_URL,
            editor.get("sha256") == PINNED_EDITOR_SHA256,
            editor.get("installed") is True,
            marker.get("core_sha256") == core_sha256,
            marker.get("tree_sha256") == tree_sha256,
        )
    )


def _bootstrap_editor() -> None:
    script = BUNDLE_ROOT / "packaging" / "bootstrap-runtime.ps1"
    if os.name != "nt" or not script.is_file():
        raise OneClickError(
            "GBFR-Save-Editor core is unavailable and automatic Windows bootstrap is unavailable."
        )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-BundleRoot",
            str(BUNDLE_ROOT),
            "-SkipPython",
        ],
        cwd=BUNDLE_ROOT,
        check=False,
    )
    if completed.returncode != 0:
        raise OneClickError(
            f"GBFR-Save-Editor bootstrap failed with exit code {completed.returncode}"
        )


def resolve_editor_root(value: Path | None) -> Path:
    if value is not None:
        resolved = value.expanduser().resolve()
        if _editor_core(resolved).is_file():
            return resolved
        raise OneClickError(f"--editor-root does not contain the editor core: {resolved}")

    bundled = BUNDLE_ROOT / "runtime" / "third_party" / "GBFR-Save-Editor"
    if _is_release_bundle():
        if _verified_bundled_editor(bundled):
            return bundled.resolve()
        _bootstrap_editor()
        if _verified_bundled_editor(bundled):
            return bundled.resolve()
        raise OneClickError(
            "GBFR-Save-Editor bootstrap completed without a verified pinned editor"
        )

    candidates: list[Path] = []
    configured = os.environ.get("GBFR_SAVE_EDITOR_ROOT")
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            bundled,
            BUNDLE_ROOT.parent / "GBFR-Save-Editor",
        ]
    )
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if _editor_core(resolved).is_file():
            return resolved

    _bootstrap_editor()
    bootstrapped = bundled
    if _editor_core(bootstrapped).is_file():
        return bootstrapped.resolve()
    raise OneClickError("GBFR-Save-Editor bootstrap completed without the required core")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manifest-driven, backed-up, idempotent GBFR Relink save preset runner."
    )
    parser.add_argument("--preset", help="Preset pack id; omit for the interactive menu")
    parser.add_argument(
        "--save",
        type=Path,
        help="SaveData1.dat or its containing SaveGames directory",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Atomically deploy the verified candidate; otherwise build offline only",
    )
    parser.add_argument("--editor-root", type=Path)
    parser.add_argument(
        "--presets-dir",
        type=Path,
        default=BUNDLE_ROOT / "presets" / "packs",
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=default_state_root(),
        help="Runs, full backups, and logs directory",
    )
    parser.add_argument("--list-presets", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--restore-latest", action="store_true")
    return parser.parse_args(argv)


def print_presets(presets: dict[str, PresetPack]) -> None:
    for preset in presets.values():
        print(f"{preset.id}\t{preset.name}\t{preset.description}")


def interactive_choice(presets: dict[str, PresetPack]) -> tuple[str, bool] | None:
    ordered = list(presets.values())
    print("\nRelink Save Forge")
    print("检测、完整备份、离线修改、二次幂等验证后才会部署。\n")
    for index, preset in enumerate(ordered, 1):
        print(f"  {index}. {preset.name}")
        print(f"     {preset.description}")
    print("  V. 只验证当前存档")
    print("  R. 恢复最近一次完整备份")
    print("  Q. 退出")
    choice = input("\n请选择: ").strip().lower()
    if choice == "q":
        return None
    if choice == "v":
        return "__validate__", False
    if choice == "r":
        return "__restore__", True
    try:
        preset = ordered[int(choice) - 1]
    except (ValueError, IndexError):
        raise OneClickError(f"无效选择: {choice!r}")
    deploy = input("部署到当前活动存档？输入 y 部署，其余只生成离线候选 [y/N]: ")
    return preset.id, deploy.strip().lower() == "y"


def validate_current(save_path: Path, editor_root: Path) -> int:
    require_game_closed()
    summary = SaveValidator(editor_root).inspect(save_path)
    print(f"有效存档: {summary.path}")
    print(f"SHA-256: {summary.sha256}")
    print(f"SteamID64: {summary.header.get('steam_id')}")
    print(f"记录数: {summary.record_count}")
    return 0


def restore_latest(state_root: Path, save_path: Path, editor_root: Path) -> int:
    state_root = state_root.expanduser().resolve()
    save_path = save_path.expanduser().resolve()
    lock = SaveLock(
        lock_root=state_root / "locks",
        save_path=save_path,
        session_id=f"manual-restore-{uuid.uuid4().hex}",
    )
    lock.acquire()
    try:
        require_game_closed()
        validator = SaveValidator(editor_root)
        backup = latest_backup(
            state_root / "backups",
            save_dir=save_path.parent,
            validator=validator,
        )
        restored = restore_backup_directory(
            backup,
            save_path.parent,
            validator=validator,
        )
    finally:
        lock.release()
    print(f"已从 {backup} 恢复 {restored} 个文件。")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        presets = load_presets(args.presets_dir)
        if args.list_presets:
            print_presets(presets)
            return 0

        interactive = args.preset is None and not args.validate_only and not args.restore_latest
        preset_id = args.preset
        apply = args.apply
        if interactive:
            selected = interactive_choice(presets)
            if selected is None:
                return 0
            preset_id, apply = selected

        restore_requested = args.restore_latest or preset_id == "__restore__"
        save_path = (
            resolve_restore_save_path(args.save)
            if restore_requested
            else resolve_save_path(args.save)
        )
        editor_root = resolve_editor_root(args.editor_root)
        if args.validate_only or preset_id == "__validate__":
            return validate_current(save_path, editor_root)
        if args.restore_latest or preset_id == "__restore__":
            return restore_latest(
                args.state_root.expanduser().resolve(),
                save_path,
                editor_root,
            )
        if not preset_id:
            raise OneClickError("--preset is required for non-interactive execution")
        preset = presets.get(preset_id)
        if preset is None:
            raise PresetError(
                f"unknown preset {preset_id!r}; available: {', '.join(presets)}"
            )

        transaction = PresetTransaction(
            bundle_root=BUNDLE_ROOT,
            state_root=args.state_root,
            preset=preset,
            save_path=save_path,
            editor_root=editor_root,
            apply=apply,
        )
        result = transaction.execute()
        for path in transaction.recovered_runs:
            print(f"已恢复上次中断的部署: {path}")
        print(f"状态: {result.status}")
        print(f"候选: {result.candidate}")
        print(f"候选 SHA-256: {result.candidate_sha256}")
        print(f"完整备份: {result.backup_dir}")
        print(f"日志: {result.run_dir / 'run.log'}")
        return 0
    except (OneClickError, PresetError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
