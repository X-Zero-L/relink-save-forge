import unittest
from unittest.mock import patch

from app import locking


class FakeKernel32:
    def __init__(self, *, handle=100, wait_result=0x102) -> None:
        self.handle = handle
        self.wait_result = wait_result
        self.closed = []

    def OpenProcess(self, _access, _inherit, _pid):
        return self.handle

    def WaitForSingleObject(self, _handle, _milliseconds):
        return self.wait_result

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


class SaveLockProcessTests(unittest.TestCase):
    def test_windows_probe_uses_open_process_without_os_kill(self) -> None:
        with (
            patch.object(locking.os, "name", "nt"),
            patch.object(locking, "_windows_pid_is_alive", return_value=True) as probe,
            patch.object(
                locking.os,
                "kill",
                side_effect=AssertionError("os.kill must not run on Windows"),
            ),
        ):
            self.assertTrue(locking.pid_is_alive(424242))
        probe.assert_called_once_with(424242)

    def test_windows_wait_timeout_means_process_is_alive(self) -> None:
        kernel = FakeKernel32(wait_result=0x102)
        self.assertTrue(locking._windows_pid_is_alive(123, kernel))
        self.assertEqual(kernel.closed, [100])

    def test_windows_signaled_handle_means_process_has_exited(self) -> None:
        kernel = FakeKernel32(wait_result=0)
        self.assertFalse(locking._windows_pid_is_alive(123, kernel))
        self.assertEqual(kernel.closed, [100])

    def test_windows_access_denied_is_treated_as_alive(self) -> None:
        kernel = FakeKernel32(handle=0)
        with patch.object(locking.ctypes, "get_last_error", return_value=5):
            self.assertTrue(locking._windows_pid_is_alive(123, kernel))


if __name__ == "__main__":
    unittest.main()
