"""Run the complete offline Relink 2.0 save rebuild pipeline."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CATALOGS = ROOT / "catalogs"


class PipelineError(RuntimeError):
    """A preflight or pipeline step failed safely."""

    def __init__(self, message: str, returncode: int = 1) -> None:
        super().__init__(message)
        self.returncode = returncode or 1


@dataclass(frozen=True)
class PipelineStep:
    name: str
    command: list[str]
    artifacts: tuple[Path, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def file_metadata(path: Path) -> dict:
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def live_save_directory() -> Path:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    return resolved(local_app_data / "GBFR" / "Saved" / "SaveGames")


def refuse_live_write(path: Path, label: str) -> None:
    live = live_save_directory()
    target = resolved(path)
    if target == live or live in target.parents:
        raise PipelineError(f"Refusing to write {label} into the live save directory: {target}")


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise PipelineError(f"Required {label} does not exist: {path}")


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a fully verified Relink 2.0 offline candidate through the "
            "materials, Fate Episode, sigil, weapon, transcendence, weapon blessing, "
            "summon passive, and final verification stages. The pipeline never writes "
            "into the live save directory."
        )
    )
    parser.add_argument("--input", type=Path, required=True, help="Offline source save")
    parser.add_argument("--items-db", type=Path, required=True)
    parser.add_argument("--game-db", type=Path, required=True)
    parser.add_argument(
        "--weapon-original",
        type=Path,
        required=True,
        help="Known-good save immediately before the one-weapon forge probe",
    )
    parser.add_argument(
        "--weapon-probe",
        type=Path,
        required=True,
        help="Known-good save after forging and equipping exactly one weapon",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="Baseline used by the final main-story and item-instance verifier",
    )
    parser.add_argument(
        "--editor-root",
        type=Path,
        required=True,
        help="GBFR-Save-Editor checkout",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        required=True,
        help="Offline directory for intermediate saves, audits, and reports",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Final candidate path; defaults to WORK_DIR/07-endgame.dat",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help=(
            "Pipeline report path; defaults to WORK_DIR/pipeline-report.json "
            "or WORK_DIR/pipeline-dry-run-report.json"
        ),
    )
    parser.add_argument(
        "--character-catalog",
        type=Path,
        default=CATALOGS / "characters.json",
    )
    parser.add_argument(
        "--weapon-catalog",
        type=Path,
        default=CATALOGS / "weapons.json",
    )
    parser.add_argument(
        "--fate-catalog",
        type=Path,
        default=CATALOGS / "fate-episodes-2.0.json",
    )
    parser.add_argument(
        "--sigil-preset",
        type=Path,
        default=ROOT / "presets" / "sigils" / "latest-endgame-gold-2.0.2.json",
    )
    parser.add_argument(
        "--rebuild-catalog",
        type=Path,
        default=CATALOGS / "weapon-rebuild-2.0.json",
    )
    parser.add_argument(
        "--weapon-blessing-preset",
        type=Path,
        default=ROOT / "presets" / "weapons" / "endgame-qol-blessing-2.0.2.json",
    )
    parser.add_argument(
        "--summon-preset",
        type=Path,
        default=ROOT / "presets" / "summons" / "endgame-qol-passives-2.0.2.json",
    )
    parser.add_argument(
        "--stack-quantity",
        type=int,
        default=900,
        help="Ordinary stack quantity written by the materials stage",
    )
    parser.add_argument("--expected-steam-id", type=int)
    parser.add_argument("--expect-transcendence-instances", type=int)
    parser.add_argument("--expect-transcendence-types", type=int)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and write the command plan without running mutation scripts",
    )
    return parser.parse_args()


def build_steps(args: argparse.Namespace, paths: dict[str, Path]) -> list[PipelineStep]:
    editor = ["--editor-root", str(paths["editor_root"])]
    fate_command = [
        sys.executable,
        str(SCRIPTS / "complete_all_fate_episodes.py"),
        str(paths["materials_save"]),
        str(paths["fate_save"]),
        "--catalog",
        str(paths["fate_catalog"]),
        "--audit",
        str(paths["fate_audit"]),
        *editor,
    ]
    if args.expected_steam_id is not None:
        fate_command.extend(["--expect-steam-id", str(args.expected_steam_id)])

    steps = [
        PipelineStep(
            "materials",
            [
                sys.executable,
                str(SCRIPTS / "build_materials_complete.py"),
                str(paths["input"]),
                str(paths["materials_save"]),
                "--database",
                str(paths["items_db"]),
                "--quantity",
                str(args.stack_quantity),
                "--audit",
                str(paths["materials_audit"]),
                *editor,
            ],
            (paths["materials_save"], paths["materials_audit"]),
        ),
        PipelineStep(
            "fate_episodes",
            fate_command,
            (paths["fate_save"], paths["fate_audit"]),
        ),
        PipelineStep(
            "sigils_initialize",
            [
                sys.executable,
                str(SCRIPTS / "build_all_sigils_strict.py"),
                str(paths["fate_save"]),
                str(paths["sigils_base_save"]),
                "--db",
                str(paths["game_db"]),
                "--audit-json",
                str(paths["sigils_base_audit"]),
                *editor,
            ],
            (paths["sigils_base_save"], paths["sigils_base_audit"]),
        ),
        PipelineStep(
            "sigils_latest_gold",
            [
                sys.executable,
                str(SCRIPTS / "equip_latest_endgame_gold_sigils.py"),
                str(paths["sigils_base_save"]),
                str(paths["sigils_save"]),
                "--characters",
                str(paths["character_catalog"]),
                "--preset",
                str(paths["sigil_preset"]),
                "--audit",
                str(paths["sigils_audit"]),
                *editor,
            ],
            (paths["sigils_save"], paths["sigils_audit"]),
        ),
        PipelineStep(
            "weapons",
            [
                sys.executable,
                str(SCRIPTS / "build_all_weapons_verified.py"),
                "--input",
                str(paths["sigils_save"]),
                "--original",
                str(paths["weapon_original"]),
                "--probe",
                str(paths["weapon_probe"]),
                "--database",
                str(paths["game_db"]),
                "--weapon-catalog",
                str(paths["weapon_catalog"]),
                "--character-catalog",
                str(paths["character_catalog"]),
                "--output",
                str(paths["weapons_save"]),
                "--audit",
                str(paths["weapons_audit"]),
                *editor,
            ],
            (paths["weapons_save"], paths["weapons_audit"]),
        ),
    ]

    transcendence_command = [
        sys.executable,
        str(SCRIPTS / "complete_all_weapon_awakenings.py"),
        str(paths["weapons_save"]),
        str(paths["transcendence_save"]),
        "--rebuild-catalog",
        str(paths["rebuild_catalog"]),
        "--audit",
        str(paths["transcendence_audit"]),
        *editor,
    ]
    if args.expect_transcendence_instances is not None:
        transcendence_command.extend(
            ["--expect-instances", str(args.expect_transcendence_instances)]
        )
    if args.expect_transcendence_types is not None:
        transcendence_command.extend(
            ["--expect-types", str(args.expect_transcendence_types)]
        )
    steps.append(
        PipelineStep(
            "transcendence",
            transcendence_command,
            (paths["transcendence_save"], paths["transcendence_audit"]),
        )
    )

    steps.append(
        PipelineStep(
            "weapon_blessings",
            [
                sys.executable,
                str(SCRIPTS / "equip_verified_weapon_blessings.py"),
                str(paths["transcendence_save"]),
                str(paths["weapon_blessings_save"]),
                "--characters",
                str(paths["character_catalog"]),
                "--preset",
                str(paths["weapon_blessing_preset"]),
                "--audit",
                str(paths["weapon_blessings_audit"]),
                *editor,
            ],
            (paths["weapon_blessings_save"], paths["weapon_blessings_audit"]),
        )
    )

    steps.append(
        PipelineStep(
            "summon_traits",
            [
                sys.executable,
                str(SCRIPTS / "equip_verified_summon_traits.py"),
                str(paths["weapon_blessings_save"]),
                str(paths["output"]),
                "--preset",
                str(paths["summon_preset"]),
                "--audit",
                str(paths["summon_audit"]),
                *editor,
            ],
            (paths["output"], paths["summon_audit"]),
        )
    )

    verification_command = [
        sys.executable,
        str(SCRIPTS / "verify_full_rebuild.py"),
        str(paths["output"]),
        "--baseline",
        str(paths["baseline"]),
        "--items-db",
        str(paths["items_db"]),
        "--characters",
        str(paths["character_catalog"]),
        "--weapons",
        str(paths["weapon_catalog"]),
        "--rebuild-catalog",
        str(paths["rebuild_catalog"]),
        "--fate-catalog",
        str(paths["fate_catalog"]),
        "--sigil-preset",
        str(paths["sigil_preset"]),
        "--weapon-blessing-preset",
        str(paths["weapon_blessing_preset"]),
        "--summon-preset",
        str(paths["summon_preset"]),
        "--loadout-baseline",
        str(paths["transcendence_save"]),
        "--stack-quantity",
        str(args.stack_quantity),
        "--report",
        str(paths["verification_report"]),
        *editor,
    ]
    if args.expected_steam_id is not None:
        verification_command.extend(
            ["--expected-steam-id", str(args.expected_steam_id)]
        )
    steps.append(
        PipelineStep(
            "verify",
            verification_command,
            (paths["verification_report"],),
        )
    )
    return steps


def prepare_paths(args: argparse.Namespace) -> dict[str, Path]:
    work_dir = resolved(args.work_dir)
    output = resolved(args.output) if args.output else work_dir / "07-endgame.dat"
    default_report = (
        "pipeline-dry-run-report.json" if args.dry_run else "pipeline-report.json"
    )
    report = resolved(args.report) if args.report else work_dir / default_report
    return {
        "input": resolved(args.input),
        "items_db": resolved(args.items_db),
        "game_db": resolved(args.game_db),
        "weapon_original": resolved(args.weapon_original),
        "weapon_probe": resolved(args.weapon_probe),
        "baseline": resolved(args.baseline),
        "editor_root": resolved(args.editor_root),
        "work_dir": work_dir,
        "output": output,
        "report": report,
        "character_catalog": resolved(args.character_catalog),
        "weapon_catalog": resolved(args.weapon_catalog),
        "fate_catalog": resolved(args.fate_catalog),
        "sigil_preset": resolved(args.sigil_preset),
        "rebuild_catalog": resolved(args.rebuild_catalog),
        "weapon_blessing_preset": resolved(args.weapon_blessing_preset),
        "summon_preset": resolved(args.summon_preset),
        "materials_save": work_dir / "01-materials.dat",
        "materials_audit": work_dir / "01-materials-audit.json",
        "fate_save": work_dir / "02-fates.dat",
        "fate_audit": work_dir / "02-fates-audit.json",
        "sigils_base_save": work_dir / "03-sigils-initialized.dat",
        "sigils_base_audit": work_dir / "03-sigils-initialized-audit.json",
        "sigils_save": work_dir / "03-sigils.dat",
        "sigils_audit": work_dir / "03-sigils-audit.json",
        "weapons_save": work_dir / "04-weapons.dat",
        "weapons_audit": work_dir / "04-weapons-audit.json",
        "transcendence_save": work_dir / "05-transcendence.dat",
        "transcendence_audit": work_dir / "05-transcendence-audit.json",
        "weapon_blessings_save": work_dir / "06-weapon-blessings.dat",
        "weapon_blessings_audit": work_dir / "06-weapon-blessings-audit.json",
        "summon_audit": work_dir / "07-summon-traits-audit.json",
        "verification_report": work_dir / "08-verification.json",
    }


def preflight(paths: dict[str, Path], steps: list[PipelineStep]) -> dict:
    required_files = {
        "source save": paths["input"],
        "items database": paths["items_db"],
        "game database": paths["game_db"],
        "weapon pre-forge original": paths["weapon_original"],
        "weapon forge probe": paths["weapon_probe"],
        "verification baseline": paths["baseline"],
        "character catalog": paths["character_catalog"],
        "weapon catalog": paths["weapon_catalog"],
        "Fate Episode catalog": paths["fate_catalog"],
        "latest endgame sigil preset": paths["sigil_preset"],
        "weapon rebuild catalog": paths["rebuild_catalog"],
        "weapon blessing preset": paths["weapon_blessing_preset"],
        "summon passive preset": paths["summon_preset"],
    }
    for label, path in required_files.items():
        require_file(path, label)

    editor_api = paths["editor_root"] / "gbfr_editor" / "core" / "gbfr_save.py"
    require_file(editor_api, "GBFR-Save-Editor API")

    script_paths = {Path(step.command[1]) for step in steps}
    for script in script_paths:
        require_file(script, "pipeline script")

    write_paths = {artifact for step in steps for artifact in step.artifacts}
    write_paths.add(paths["report"])
    for path in write_paths:
        refuse_live_write(path, "pipeline artifact")
    refuse_live_write(paths["work_dir"], "work directory")

    input_paths = set(required_files.values()) | {editor_api} | script_paths
    overlap = sorted(write_paths & input_paths, key=str)
    if overlap:
        raise PipelineError(
            "Pipeline outputs overlap required inputs: "
            + ", ".join(str(path) for path in overlap)
        )
    if len(write_paths) != sum(len(step.artifacts) for step in steps) + 1:
        raise PipelineError("Pipeline artifact paths are not unique")

    existing = sorted((path for path in write_paths if path.exists()), key=str)
    if existing:
        raise PipelineError(
            "Refusing to overwrite existing pipeline artifacts: "
            + ", ".join(str(path) for path in existing)
        )
    if paths["work_dir"].exists() and not paths["work_dir"].is_dir():
        raise PipelineError(f"Work directory path is not a directory: {paths['work_dir']}")

    return {
        label: file_metadata(path)
        for label, path in sorted(required_files.items())
    }


def run_step(step: PipelineStep, index: int, total: int) -> dict:
    print(f"[{index}/{total}] {step.name}", flush=True)
    started_at = utc_now()
    started = time.monotonic()
    try:
        completed = subprocess.run(
            step.command,
            cwd=ROOT,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise PipelineError(f"Could not start {step.name}: {exc}") from exc
    result = {
        "name": step.name,
        "status": "completed" if completed.returncode == 0 else "failed",
        "command": step.command,
        "started_at": started_at,
        "finished_at": utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "artifacts": [],
    }
    if completed.returncode != 0:
        error = PipelineError(
            f"Step {step.name} failed with exit code {completed.returncode}",
            completed.returncode,
        )
        error.step_result = result
        raise error

    missing = [path for path in step.artifacts if not path.is_file()]
    if missing:
        result["status"] = "failed"
        error = PipelineError(
            f"Step {step.name} did not create expected artifacts: "
            + ", ".join(str(path) for path in missing)
        )
        error.step_result = result
        raise error
    result["artifacts"] = [file_metadata(path) for path in step.artifacts]
    return result


def main() -> int:
    args = parse_args()
    if not 1 <= args.stack_quantity <= 999:
        raise SystemExit("--stack-quantity must be between 1 and 999")
    paths = prepare_paths(args)
    steps = build_steps(args, paths)
    report_owned = False
    report = {
        "schema_version": 1,
        "pipeline": "GBFR Relink 2.0 complete offline rebuild",
        "status": "preflight",
        "dry_run": args.dry_run,
        "started_at": utc_now(),
        "finished_at": None,
        "python": {
            "executable": sys.executable,
            "version": sys.version,
        },
        "repository_root": str(ROOT),
        "work_dir": str(paths["work_dir"]),
        "final_output": str(paths["output"]),
        "report": str(paths["report"]),
        "inputs": {},
        "steps": [],
        "error": None,
    }

    try:
        report["inputs"] = preflight(paths, steps)
        paths["work_dir"].mkdir(parents=True, exist_ok=True)
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        if not args.dry_run:
            paths["output"].parent.mkdir(parents=True, exist_ok=True)

        if args.dry_run:
            report["status"] = "dry-run"
            report["steps"] = [
                {
                    "name": step.name,
                    "status": "planned",
                    "command": step.command,
                    "artifacts": [str(path) for path in step.artifacts],
                }
                for step in steps
            ]
            report["finished_at"] = utc_now()
            write_report(paths["report"], report)
            report_owned = True
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        report["status"] = "running"
        write_report(paths["report"], report)
        report_owned = True
        for index, step in enumerate(steps, 1):
            try:
                result = run_step(step, index, len(steps))
            except PipelineError as exc:
                result = getattr(exc, "step_result", None)
                if result is not None:
                    report["steps"].append(result)
                raise
            report["steps"].append(result)
            write_report(paths["report"], report)

        report["status"] = "completed"
        report["finished_at"] = utc_now()
        report["final_artifact"] = file_metadata(paths["output"])
        write_report(paths["report"], report)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "output": report["final_artifact"],
                    "report": str(paths["report"]),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, PipelineError) as exc:
        returncode = exc.returncode if isinstance(exc, PipelineError) else 1
        report["status"] = "failed"
        report["finished_at"] = utc_now()
        report["error"] = str(exc)
        try:
            if report_owned:
                write_report(paths["report"], report)
            elif not paths["report"].exists():
                refuse_live_write(paths["report"], "pipeline report")
                write_report(paths["report"], report)
        except (OSError, PipelineError):
            pass
        print(f"error: {exc}", file=sys.stderr)
        return returncode


if __name__ == "__main__":
    raise SystemExit(main())
