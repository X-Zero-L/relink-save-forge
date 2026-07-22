"""Load the external GBFR Save Editor API from a user-supplied checkout.

The save editor is intentionally not vendored. Mutation scripts accept
``--editor-root`` and also honor ``GBFR_SAVE_EDITOR_ROOT``. For convenience,
the default lookup checks a sibling ``GBFR-Save-Editor`` directory.
"""

import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _argument_value(name: str) -> str | None:
    prefix = f"{name}="
    for index, value in enumerate(sys.argv[1:], 1):
        if value.startswith(prefix):
            return value[len(prefix) :]
        if value == name and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return None


def resolve_editor_root() -> Path:
    configured = _argument_value("--editor-root") or os.environ.get(
        "GBFR_SAVE_EDITOR_ROOT"
    )
    candidate = (
        Path(configured).expanduser()
        if configured
        else REPOSITORY_ROOT.parent / "GBFR-Save-Editor"
    ).resolve()
    core = candidate / "gbfr_editor" / "core"
    if not (core / "gbfr_save.py").is_file():
        raise RuntimeError(
            "GBFR Save Editor API was not found. Pass --editor-root PATH, set "
            "GBFR_SAVE_EDITOR_ROOT, or place GBFR-Save-Editor beside this repository."
        )
    return candidate


EDITOR_ROOT = resolve_editor_root()
sys.path[:0] = [
    str(EDITOR_ROOT),
    str(EDITOR_ROOT / "gbfr_editor" / "core"),
    str(EDITOR_ROOT / "gbfr_editor" / "data"),
]

from gbfr_save import GBFRSaveData, UnitRecord  # noqa: E402, F401


def add_editor_argument(parser) -> None:
    parser.add_argument(
        "--editor-root",
        type=Path,
        default=EDITOR_ROOT,
        help=(
            "GBFR-Save-Editor checkout. Also accepted through "
            "GBFR_SAVE_EDITOR_ROOT."
        ),
    )
