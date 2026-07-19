"""Safely replace the SteamID64 stored in GBFR PC save headers."""

from __future__ import annotations

import argparse
import os
import shutil
import struct
from datetime import datetime, timezone
from pathlib import Path

SAVE_NAMES = ("SaveData1.dat", "SaveData1_BackUp.dat", "SaveData1_BackUp2.dat")


def inspect(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 12:
        raise ValueError(f"{path} is too small to contain a GBFR PC save header.")
    return struct.unpack_from("<Q", data, 4)[0], len(data)


def replace(path: Path, old_id: int, new_id: int, backup_dir: Path) -> None:
    data = bytearray(path.read_bytes())
    actual = struct.unpack_from("<Q", data, 4)[0]
    if actual != old_id:
        raise ValueError(f"{path.name}: expected SteamID64 {old_id}, found {actual}.")
    shutil.copy2(path, backup_dir / path.name)
    struct.pack_into("<Q", data, 4, new_id)
    temporary = path.with_name(f".{path.name}.resign.tmp")
    temporary.write_bytes(data)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    verified, _ = inspect(path)
    if verified != new_id:
        raise RuntimeError(f"{path.name}: SteamID64 verification failed after replacement.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("save_dir", type=Path)
    parser.add_argument("--expected-old-steam-id", type=int, required=True)
    parser.add_argument("--new-steam-id", type=int, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Write changes; without this flag only inspect.")
    args = parser.parse_args()

    paths = [args.save_dir / name for name in SAVE_NAMES if (args.save_dir / name).is_file()]
    if not paths:
        raise FileNotFoundError("No known GBFR save files were found.")
    for path in paths:
        steam_id, size = inspect(path)
        if steam_id != args.expected_old_steam_id:
            raise ValueError(f"{path.name}: expected SteamID64 {args.expected_old_steam_id}, found {steam_id}.")
        print(f"{path.name}: SteamID64={steam_id} size={size}")
    if not args.apply:
        print("Dry run only. Re-run with --apply after verifying the backup destination and IDs.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = args.backup_root / f"gbfr-save-before-resign-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in paths:
        replace(path, args.expected_old_steam_id, args.new_steam_id, backup_dir)
    print(f"Updated {len(paths)} file(s). Backup: {backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

