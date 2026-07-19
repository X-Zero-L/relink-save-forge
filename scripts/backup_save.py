"""Back up GBFR PC save files and write a SHA-256 manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SAVE_NAMES = ("SaveData1.dat", "SaveData1_BackUp.dat", "SaveData1_BackUp2.dat")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("save_dir", type=Path)
    parser.add_argument("backup_root", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = args.backup_root / f"gbfr-save-backup-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    files = []
    for name in SAVE_NAMES:
        source = args.save_dir / name
        if not source.is_file():
            continue
        destination = target / name
        shutil.copy2(source, destination)
        files.append({"name": name, "size": destination.stat().st_size, "sha256": sha256(destination)})
    if not files:
        target.rmdir()
        raise FileNotFoundError("No known GBFR save files were found.")
    manifest = {"created_utc": stamp, "files": files}
    (target / "manifest-sha256.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

