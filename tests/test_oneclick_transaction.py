import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.presets import load_preset
from app.locking import SaveLockError
from app.runtime import OneClickError, SaveValidator, sha256_file
from app.transaction import (
    PresetTransaction,
    TransactionError,
    latest_backup,
    recover_incomplete_transactions,
    restore_backup_directory,
)


SOURCE_BYTES = b"VALID|state=0"
FINAL_BYTES = b"VALID|state=1"


FAKE_EDITOR = r'''
from pathlib import Path

class _Container:
    def __init__(self, path, data):
        self.path = Path(path)
        steam_id = 999999999 if b"sid=other" in data else 123456789
        self.header = {"steam_id": steam_id}
        self.payload_size = 777

class GBFRSaveData:
    def __init__(self, path, data):
        self.container = _Container(path, data)
        self.records = [object(), object(), object()]
        self._data = data

    @classmethod
    def open(cls, path):
        data = Path(path).read_bytes()
        if not data.startswith(b"VALID|"):
            raise ValueError("invalid fake save")
        return cls(path, data)

    def check_active_hash(self):
        return b"bad-hash" not in self._data
'''


STEP_SCRIPT = r'''
import argparse
import hashlib
import json
from pathlib import Path

from bundle_helper import VERIFIED_RECORDS

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
    "counts": {"verified_records": VERIFIED_RECORDS},
    "validation": {"semantic_result_verified": True},
}), encoding="utf-8")
'''


NOOP_FAILED_AUDIT_SCRIPT = r'''
import argparse
import hashlib
import json
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("input", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--audit", type=Path, required=True)
args = parser.parse_args()
shutil.copy2(args.input, args.output)
args.audit.write_text(json.dumps({
    "schema_version": 1,
    "input": {"sha256": hashlib.sha256(args.input.read_bytes()).hexdigest().upper()},
    "output": {
        "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest().upper(),
        "size": args.output.stat().st_size,
        "record_count": 3,
        "active_hash_ok": True,
    },
    "counts": {"record_changes": 0},
    "validation": {"semantic_result_verified": False},
}), encoding="utf-8")
'''


class OneClickTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        (self.bundle / "bundle_helper.py").write_text(
            "VERIFIED_RECORDS = 3\n",
            encoding="utf-8",
        )
        (self.bundle / "scripts").mkdir()
        (self.bundle / "scripts" / "step.py").write_text(
            STEP_SCRIPT, encoding="utf-8"
        )
        self.editor = self.root / "editor"
        editor_core = self.editor / "gbfr_editor" / "core"
        editor_core.mkdir(parents=True)
        (editor_core / "gbfr_save.py").write_text(FAKE_EDITOR, encoding="utf-8")

        self.save_dir = self.root / "live" / "SaveGames"
        self.save_dir.mkdir(parents=True)
        self.save_path = self.save_dir / "SaveData1.dat"
        self.save_path.write_bytes(SOURCE_BYTES)
        (self.save_dir / "SystemData.dat").write_bytes(b"SYSTEM")
        nested = self.save_dir / "nested"
        nested.mkdir()
        (nested / "cloud.txt").write_text("cloud", encoding="utf-8")

        self.state_root = self.root / "state"
        manifest_path = self.bundle / "preset.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "test-pack",
                    "name": "Test pack",
                    "description": "Idempotent fake transform",
                    "steps": [
                        {
                            "id": "transform",
                            "command": [
                                "{python}",
                                "{root}/scripts/step.py",
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
        self.preset = load_preset(manifest_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_transaction(self, *, apply: bool) -> PresetTransaction:
        return PresetTransaction(
            bundle_root=self.bundle,
            state_root=self.state_root,
            preset=self.preset,
            save_path=self.save_path,
            editor_root=self.editor,
            apply=apply,
            game_guard=lambda: None,
            echo=False,
        )

    def test_full_backup_two_pass_and_atomic_deployment(self) -> None:
        transaction = self.make_transaction(apply=True)
        result = transaction.execute()

        self.assertEqual(result.status, "completed")
        self.assertTrue(result.deployed)
        self.assertEqual(self.save_path.read_bytes(), FINAL_BYTES)
        self.assertEqual(result.candidate.read_bytes(), FINAL_BYTES)
        self.assertEqual(
            (result.backup_dir / "SaveGames" / "SaveData1.dat").read_bytes(),
            SOURCE_BYTES,
        )
        self.assertEqual(
            (result.backup_dir / "SaveGames" / "SystemData.dat").read_bytes(),
            b"SYSTEM",
        )
        self.assertTrue(
            (result.backup_dir / "SaveGames" / "nested" / "cloud.txt").is_file()
        )
        first = result.run_dir / "pass-1" / "01-transform.dat"
        second = result.run_dir / "pass-2" / "01-transform.dat"
        self.assertEqual(first.read_bytes(), second.read_bytes())
        session = json.loads((result.run_dir / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(session["status"], "completed")
        self.assertTrue(session["idempotency"]["byte_identical"])

    def test_exit_zero_noop_is_rejected_by_semantic_audit(self) -> None:
        script = self.bundle / "scripts" / "noop_failed_audit.py"
        script.write_text(NOOP_FAILED_AUDIT_SCRIPT, encoding="utf-8")
        manifest = self.bundle / "noop-preset.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "noop-pack",
                    "name": "No-op pack",
                    "description": "Must fail semantic audit validation",
                    "steps": [
                        {
                            "id": "noop",
                            "command": [
                                "{python}",
                                "{root}/scripts/noop_failed_audit.py",
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
        transaction = PresetTransaction(
            bundle_root=self.bundle,
            state_root=self.state_root,
            preset=load_preset(manifest),
            save_path=self.save_path,
            editor_root=self.editor,
            apply=False,
            game_guard=lambda: None,
            echo=False,
        )

        with self.assertRaisesRegex(TransactionError, "semantic verification"):
            transaction.execute()

        self.assertEqual(self.save_path.read_bytes(), SOURCE_BYTES)

    def test_post_deployment_failure_restores_original_primary(self) -> None:
        transaction = self.make_transaction(apply=True)
        base_validator = transaction.validator

        class RejectDeployedCandidate:
            def inspect(inner_self, path: Path):
                if path.resolve() == self.save_path.resolve() and path.read_bytes() == FINAL_BYTES:
                    raise OneClickError("forced post-deployment validation failure")
                return base_validator.inspect(path)

        transaction.validator = RejectDeployedCandidate()
        with self.assertRaisesRegex(OneClickError, "forced post-deployment"):
            transaction.execute()

        self.assertEqual(self.save_path.read_bytes(), SOURCE_BYTES)
        session = json.loads(
            (transaction.run_dir / "session.json").read_text(encoding="utf-8")
        )
        self.assertEqual(session["status"], "rolled_back")

    def test_recovers_interrupted_deployment_on_next_start(self) -> None:
        transaction = self.make_transaction(apply=False)
        result = transaction.execute()
        backup_primary = result.backup_dir / "SaveGames" / "SaveData1.dat"
        self.save_path.write_bytes(FINAL_BYTES)

        interrupted = self.state_root / "runs" / "interrupted"
        interrupted.mkdir(parents=True)
        (interrupted / "session.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "deploying",
                    "applied": True,
                    "save_path": str(self.save_path),
                    "backup_primary": str(backup_primary),
                    "source": {"sha256": sha256_file(backup_primary)},
                    "candidate_sha256": sha256_file(self.save_path),
                }
            ),
            encoding="utf-8",
        )

        recovered = recover_incomplete_transactions(
            self.state_root,
            self.editor,
            save_path=self.save_path,
            game_guard=lambda: None,
        )
        self.assertIn(interrupted, recovered)
        self.assertEqual(self.save_path.read_bytes(), SOURCE_BYTES)
        state = json.loads((interrupted / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "recovered_after_interruption")
        self.assertEqual(state["recovery_action"], "restored_source")
        SaveValidator(self.editor).inspect(self.save_path)

    def test_interrupted_recovery_leaves_existing_source_untouched(self) -> None:
        transaction = self.make_transaction(apply=False)
        result = transaction.execute()
        backup_primary = result.backup_dir / "SaveGames" / "SaveData1.dat"
        session_dir = self.state_root / "runs" / "source-present"
        session_dir.mkdir(parents=True)
        session_path = session_dir / "session.json"
        session_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "deploying",
                    "applied": True,
                    "save_path": str(self.save_path),
                    "backup_primary": str(backup_primary),
                    "source": {"sha256": sha256_file(self.save_path)},
                    "candidate_sha256": sha256_file(result.candidate),
                }
            ),
            encoding="utf-8",
        )

        recovered = recover_incomplete_transactions(
            self.state_root,
            self.editor,
            save_path=self.save_path,
            game_guard=lambda: None,
        )
        self.assertIn(session_dir, recovered)
        self.assertEqual(self.save_path.read_bytes(), SOURCE_BYTES)
        state = json.loads(session_path.read_text(encoding="utf-8"))
        self.assertEqual(state["recovery_action"], "source_already_present")

    def test_interrupted_recovery_refuses_unknown_live_sha(self) -> None:
        transaction = self.make_transaction(apply=False)
        result = transaction.execute()
        backup_primary = result.backup_dir / "SaveGames" / "SaveData1.dat"
        unknown = b"VALID|state=unknown"
        self.save_path.write_bytes(unknown)
        session_dir = self.state_root / "runs" / "unknown-live"
        session_dir.mkdir(parents=True)
        session_path = session_dir / "session.json"
        session_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "deploying",
                    "applied": True,
                    "save_path": str(self.save_path),
                    "backup_primary": str(backup_primary),
                    "source": {"sha256": sha256_file(backup_primary)},
                    "candidate_sha256": sha256_file(result.candidate),
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(TransactionError, "neither source nor candidate"):
            recover_incomplete_transactions(
                self.state_root,
                self.editor,
                save_path=self.save_path,
                game_guard=lambda: None,
            )
        self.assertEqual(self.save_path.read_bytes(), unknown)
        state = json.loads(session_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "recovery_blocked_unknown_live_sha")

    def test_interrupted_recovery_filters_other_save_directories(self) -> None:
        result = self.make_transaction(apply=False).execute()
        other_save_dir = self.root / "other-live" / "SaveGames"
        other_save_dir.mkdir(parents=True)
        other_save = other_save_dir / "SaveData1.dat"
        other_save.write_bytes(FINAL_BYTES)
        other_backup = self.root / "other-backup.dat"
        other_backup.write_bytes(SOURCE_BYTES)
        other_session = self.state_root / "runs" / "other-save"
        other_session.mkdir(parents=True)
        other_session_path = other_session / "session.json"
        other_session_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "deploying",
                    "applied": True,
                    "save_path": str(other_save),
                    "backup_primary": str(other_backup),
                    "source": {"sha256": sha256_file(other_backup)},
                    "candidate_sha256": sha256_file(other_save),
                }
            ),
            encoding="utf-8",
        )

        recovered = recover_incomplete_transactions(
            self.state_root,
            self.editor,
            save_path=self.save_path,
            game_guard=lambda: None,
        )

        self.assertNotIn(other_session, recovered)
        self.assertEqual(other_save.read_bytes(), FINAL_BYTES)
        state = json.loads(other_session_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "deploying")
        self.assertTrue(result.candidate.is_file())

    def test_transaction_recovers_selected_save_after_acquiring_lock(self) -> None:
        result = self.make_transaction(apply=False).execute()
        backup_primary = result.backup_dir / "SaveGames" / "SaveData1.dat"
        self.save_path.write_bytes(FINAL_BYTES)
        interrupted = self.state_root / "runs" / "auto-recover"
        interrupted.mkdir(parents=True)
        session_path = interrupted / "session.json"
        session_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "deploying",
                    "applied": True,
                    "save_path": str(self.save_path),
                    "backup_primary": str(backup_primary),
                    "source": {"sha256": sha256_file(backup_primary)},
                    "candidate_sha256": sha256_file(self.save_path),
                }
            ),
            encoding="utf-8",
        )

        transaction = self.make_transaction(apply=False)
        completed = transaction.execute()

        self.assertIn(interrupted, transaction.recovered_runs)
        self.assertEqual(completed.status, "verified_offline")
        self.assertEqual(self.save_path.read_bytes(), SOURCE_BYTES)
        state = json.loads(session_path.read_text(encoding="utf-8"))
        self.assertEqual(state["recovery_action"], "restored_source")

    def test_manual_restore_rejects_different_save_directory(self) -> None:
        result = self.make_transaction(apply=False).execute()
        manifest_path = result.backup_dir / "manifest-sha256.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source_directory"] = str(self.root / "another" / "SaveGames")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(TransactionError, "different SaveGames"):
            restore_backup_directory(
                result.backup_dir,
                self.save_dir,
                validator=SaveValidator(self.editor),
            )

    def test_manual_restore_rejects_header_mismatch(self) -> None:
        result = self.make_transaction(apply=False).execute()
        self.save_path.write_bytes(b"VALID|state=0|sid=other")
        with self.assertRaisesRegex(TransactionError, "header does not match"):
            restore_backup_directory(
                result.backup_dir,
                self.save_dir,
                validator=SaveValidator(self.editor),
            )

    def test_manual_restore_accepts_missing_live_primary(self) -> None:
        result = self.make_transaction(apply=False).execute()
        self.save_path.unlink()

        restored = restore_backup_directory(
            result.backup_dir,
            self.save_dir,
            validator=SaveValidator(self.editor),
        )

        self.assertEqual(restored, 3)
        self.assertEqual(self.save_path.read_bytes(), SOURCE_BYTES)

    def test_manual_restore_accepts_invalid_live_primary_hash(self) -> None:
        result = self.make_transaction(apply=False).execute()
        self.save_path.write_bytes(b"VALID|state=corrupt|bad-hash")

        restored = restore_backup_directory(
            result.backup_dir,
            self.save_dir,
            validator=SaveValidator(self.editor),
        )

        self.assertEqual(restored, 3)
        self.assertEqual(self.save_path.read_bytes(), SOURCE_BYTES)

    def test_restore_prevalidates_all_backup_files_before_commit(self) -> None:
        result = self.make_transaction(apply=False).execute()
        self.save_path.write_bytes(FINAL_BYTES)
        live_system = self.save_dir / "SystemData.dat"
        live_system.write_bytes(b"NEW-SYSTEM")
        (result.backup_dir / "SaveGames" / "SystemData.dat").write_bytes(b"CORRUPT")

        with self.assertRaisesRegex(TransactionError, "backup file failed verification"):
            restore_backup_directory(
                result.backup_dir,
                self.save_dir,
                validator=SaveValidator(self.editor),
            )

        self.assertEqual(self.save_path.read_bytes(), FINAL_BYTES)
        self.assertEqual(live_system.read_bytes(), b"NEW-SYSTEM")

    def test_restore_rolls_back_files_when_commit_fails(self) -> None:
        result = self.make_transaction(apply=False).execute()
        self.save_path.write_bytes(FINAL_BYTES)
        live_system = self.save_dir / "SystemData.dat"
        live_system.write_bytes(b"NEW-SYSTEM")
        real_replace = __import__("os").replace
        failed = False

        def fail_second_commit(source, target):
            nonlocal failed
            source_path = Path(source)
            target_path = Path(target)
            if (
                not failed
                and target_path == live_system
                and ".relink-restore-" in str(source_path)
                and "staged" in source_path.parts
            ):
                failed = True
                raise OSError("forced restore commit failure")
            return real_replace(source, target)

        with patch("app.transaction.os.replace", side_effect=fail_second_commit):
            with self.assertRaisesRegex(OSError, "forced restore commit failure"):
                restore_backup_directory(
                    result.backup_dir,
                    self.save_dir,
                    validator=SaveValidator(self.editor),
                )

        self.assertTrue(failed)
        self.assertEqual(self.save_path.read_bytes(), FINAL_BYTES)
        self.assertEqual(live_system.read_bytes(), b"NEW-SYSTEM")

    def test_automatic_rollback_refuses_tampered_backup_primary(self) -> None:
        transaction = self.make_transaction(apply=True)
        base_validator = transaction.validator

        class TamperBackupThenReject:
            def inspect(inner_self, path: Path):
                if path.resolve() == self.save_path.resolve() and path.read_bytes() == FINAL_BYTES:
                    session = json.loads(
                        transaction.session_path.read_text(encoding="utf-8")
                    )
                    Path(session["backup_primary"]).write_bytes(b"VALID|tampered")
                    raise OneClickError("forced deployed failure after backup tamper")
                return base_validator.inspect(path)

        transaction.validator = TamperBackupThenReject()
        with self.assertRaisesRegex(TransactionError, "rollback also failed"):
            transaction.execute()
        self.assertEqual(self.save_path.read_bytes(), FINAL_BYTES)
        state = json.loads(transaction.session_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "rollback_failed")

    def test_rejects_state_root_inside_live_save_directory_before_mkdir(self) -> None:
        unsafe_state = self.save_dir / "unsafe-state"
        with self.assertRaisesRegex(TransactionError, "state root"):
            PresetTransaction(
                bundle_root=self.bundle,
                state_root=unsafe_state,
                preset=self.preset,
                save_path=self.save_path,
                editor_root=self.editor,
                apply=False,
                game_guard=lambda: None,
                echo=False,
            )
        self.assertFalse(unsafe_state.exists())

    def test_latest_backup_skips_newer_invalid_backup(self) -> None:
        result = self.make_transaction(apply=False).execute()
        invalid = self.state_root / "backups" / "gbfr-save-backup-99999999T999999Z-invalid"
        shutil.copytree(result.backup_dir, invalid)
        (invalid / "SaveGames" / "SaveData1.dat").write_bytes(b"VALID|corrupt-copy")

        selected = latest_backup(
            self.state_root / "backups",
            save_dir=self.save_dir,
            validator=SaveValidator(self.editor),
        )
        self.assertEqual(selected, result.backup_dir)

    def test_active_process_lock_rejects_second_transaction(self) -> None:
        first = self.make_transaction(apply=False)
        second = self.make_transaction(apply=False)
        first.lock.acquire()
        try:
            with self.assertRaisesRegex(SaveLockError, "already locked"):
                second.execute()
        finally:
            first.lock.release()
        self.assertFalse(first.lock.path.exists())

    def test_dead_process_lock_is_taken_over_safely(self) -> None:
        transaction = self.make_transaction(apply=False)
        transaction.lock.path.parent.mkdir(parents=True, exist_ok=True)
        transaction.lock.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "pid": 987654321,
                    "session_id": "dead-session",
                    "save_path": str(self.save_path),
                }
            ),
            encoding="utf-8",
        )
        transaction.lock.process_probe = lambda _pid: False

        result = transaction.execute()
        self.assertEqual(result.status, "verified_offline")
        self.assertFalse(transaction.lock.path.exists())


if __name__ == "__main__":
    unittest.main()
