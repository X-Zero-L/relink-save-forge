import hashlib
import json
import os
import uuid
import ctypes
from pathlib import Path
from typing import Callable

from app.runtime import EventLogger, OneClickError, utc_now


class SaveLockError(OneClickError):
    """The selected save is already owned by another one-click transaction."""


def _windows_pid_is_alive(pid: int, kernel32=None) -> bool:
    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102

    if kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(
        process_query_limited_information | synchronize,
        0,
        pid,
    )
    if not handle:
        return ctypes.get_last_error() == 5
    try:
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == wait_timeout:
            return True
        if result == wait_object_0:
            return False
        return True
    finally:
        kernel32.CloseHandle(handle)


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class SaveLock:
    def __init__(
        self,
        *,
        lock_root: Path,
        save_path: Path,
        session_id: str,
        logger: EventLogger | None = None,
        process_probe: Callable[[int], bool] = pid_is_alive,
    ) -> None:
        self.lock_root = lock_root.expanduser().resolve()
        self.save_path = save_path.expanduser().resolve()
        self.session_id = session_id
        self.logger = logger
        self.process_probe = process_probe
        normalized = os.path.normcase(str(self.save_path))
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest().upper()
        self.path = self.lock_root / f"{digest}.lock"
        self.acquired = False

    def _value(self) -> dict:
        return {
            "schema_version": 1,
            "pid": os.getpid(),
            "session_id": self.session_id,
            "save_path": str(self.save_path),
            "created_utc": utc_now(),
        }

    def _create_exclusive(self) -> bool:
        payload = (json.dumps(self._value(), ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        try:
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            self.path.unlink(missing_ok=True)
            raise
        self.acquired = True
        if self.logger:
            self.logger.event(
                "info",
                "Acquired exclusive save transaction lock",
                lock=str(self.path),
            )
        return True

    def _owner(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def acquire(self) -> None:
        if self.acquired:
            return
        self.lock_root.mkdir(parents=True, exist_ok=True)
        for _ in range(16):
            if self._create_exclusive():
                return
            owner = self._owner()
            owner_pid = owner.get("pid")
            if isinstance(owner_pid, int) and self.process_probe(owner_pid):
                raise SaveLockError(
                    "SaveData1.dat is already locked by an active Relink Save Forge "
                    f"process (PID {owner_pid}, session {owner.get('session_id')})."
                )

            stale = self.lock_root / f".{self.path.name}.{uuid.uuid4().hex}.stale"
            try:
                os.replace(self.path, stale)
            except FileNotFoundError:
                continue
            finally:
                stale.unlink(missing_ok=True)
        raise SaveLockError(f"could not acquire save transaction lock: {self.path}")

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            owner = self._owner()
            if (
                owner.get("pid") == os.getpid()
                and owner.get("session_id") == self.session_id
                and Path(str(owner.get("save_path", ""))).resolve() == self.save_path
            ):
                self.path.unlink(missing_ok=True)
                if self.logger:
                    self.logger.event(
                        "info",
                        "Released exclusive save transaction lock",
                        lock=str(self.path),
                    )
        finally:
            self.acquired = False
