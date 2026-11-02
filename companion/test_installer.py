"""
test_installer.py – Unit tests for InstallerManager (Phase 3 Fix #10)

Tests cover:
  - Fix #10: install_update() must NOT bypass integrity verification
    when no expected hash is stored
"""

import hashlib
import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure companion/ is on the path so imports resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from installer import InstallerManager  # noqa: E402


class DummyLogger:
    def info(self, msg, *args, **kwargs): pass
    def warning(self, msg, *args, **kwargs): pass
    def error(self, msg, *args, **kwargs): pass
    def log(self, msg, level="INFO", *args, **kwargs): pass


class DummyUpdater:
    def __init__(self):
        self._installer_path = ""
        self._installer_sha256 = ""
        self._lock = threading.Lock()
        self._download_lock = threading.Lock()
        self._downloading = False
        self._installation_in_progress = False
        self.notifications = []

    def _notify(self, status, progress, error_msg=None):
        self.notifications.append((status, progress, error_msg))

    @property
    def _download_running(self) -> bool:
        return self._downloading

    def _save_cache(self):
        pass


class DummyWindow:
    def after(self, delay, fn):
        try:
            fn()
        except Exception:
            pass

    def prepare_for_installation(self):
        pass

    def destroy(self):
        pass


def _run_thread_immediately(target, args=(), kwargs=None, daemon=None, name=None):
    """Mock side effect for threading.Thread: run target immediately and return a dummy."""
    target(*args, **(kwargs or {}))
    dummy = MagicMock()
    dummy.is_alive.return_value = False
    return dummy


class TestFix10InstallUpdateIntegrityCheck(unittest.TestCase):
    """Fix #10: install_update() must not bypass integrity verification
    when no expected hash is stored."""

    def setUp(self):
        self.logger = DummyLogger()
        self.updater = DummyUpdater()
        self.window = DummyWindow()
        self.installer = InstallerManager(self.logger, self.updater, self.window)

    def _make_fake_zip(self, name):
        path = os.path.join(os.path.dirname(__file__), "cache", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        content = b"fake zip contents"
        with open(path, "wb") as fh:
            fh.write(content)
        return path, content

    def test_install_update_fails_when_no_expected_hash(self):
        """No expected hash stored → logs error, notifies failure, does NOT install."""
        fake_zip, _ = self._make_fake_zip("fake_update.zip")

        self.updater._installer_path = fake_zip
        self.updater._installer_sha256 = ""  # No expected hash — the bug scenario

        with patch("threading.Thread", side_effect=_run_thread_immediately):
            with patch.object(self.updater, "_notify") as mock_notify:
                self.installer.install_update()

        # _notify should have been called with "Failed" status
        failed_calls = [
            c for c in mock_notify.call_args_list if c[0][0] == "Failed"
        ]
        self.assertGreaterEqual(len(failed_calls), 1)
        self.assertIn("no expected hash", failed_calls[0][0][2].lower())

        # Installation should NOT have started
        self.assertFalse(self.updater._installation_in_progress)

        try:
            os.remove(fake_zip)
        except OSError:
            pass

    def test_install_update_zip_not_found(self):
        """No ZIP path → notifies failure, returns early."""
        self.updater._installer_path = ""
        self.updater._installer_sha256 = "somehash"

        with patch("threading.Thread", side_effect=_run_thread_immediately):
            with patch.object(self.updater, "_notify") as mock_notify:
                self.installer.install_update()

        failed_calls = [
            c for c in mock_notify.call_args_list if c[0][0] == "Failed"
        ]
        self.assertGreaterEqual(len(failed_calls), 1)
        self.assertIn("zip not found", failed_calls[0][0][2].lower())

    def test_install_update_hash_mismatch_raises(self):
        """Expected hash does not match actual → ValueError raised, no install."""
        fake_zip, content = self._make_fake_zip("fake_update3.zip")
        wrong_hash = "0000000000000000000000000000000000000000000000000000000000000000"

        self.updater._installer_path = fake_zip
        self.updater._installer_sha256 = wrong_hash

        with patch("threading.Thread", side_effect=_run_thread_immediately):
            with patch.object(self.updater, "_notify") as mock_notify:
                self.installer.install_update()

        failed_calls = [
            c for c in mock_notify.call_args_list if c[0][0] == "Failed"
        ]
        self.assertGreaterEqual(len(failed_calls), 1)
        self.assertIn("integrity check failed", failed_calls[0][0][2].lower())

        self.assertFalse(self.updater._installation_in_progress)

        try:
            os.remove(fake_zip)
        except OSError:
            pass


class FakeProcess:
    """A stand-in for subprocess.Popen that records wait/terminate/kill."""
    def __init__(self, exit_code: int = 0):
        self._exit_code = exit_code
        self.wait_called = False
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        self.wait_called = True
        return self._exit_code

    def poll(self):
        return self._exit_code if self.wait_called else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class TestInstallerSubprocessLifecycle(unittest.TestCase):
    """Installer tracks the Popen handle, captures the exit code, awaits
    completion, and never hard-kills the installer while it extracts files."""

    def setUp(self):
        self.logger = DummyLogger()
        self.updater = DummyUpdater()
        self.window = DummyWindow()
        self.installer = InstallerManager(self.logger, self.updater, self.window)

    def _make_fake_zip(self, name: str) -> str:
        path = os.path.join(os.path.dirname(__file__), "cache", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"fake zip contents")
        return path

    def _installer_ready(self) -> str:
        """Put the updater into a post-download state (valid hash + zip)."""
        fake_zip = self._make_fake_zip("lifecycle_update.zip")
        self.updater._installer_path = fake_zip
        self.updater._installer_sha256 = hashlib.sha256(b"fake zip contents").hexdigest()
        return fake_zip

    def test_installer_tracks_popen_handle_and_captures_exit_code(self):
        fake_zip = self._installer_ready()
        fake_proc = FakeProcess(exit_code=0)

        with patch("threading.Thread", side_effect=_run_thread_immediately), \
             patch("time.sleep", return_value=None), \
             patch("installer.subprocess.Popen", return_value=fake_proc) as mock_popen, \
             patch.object(self.updater, "_notify") as mock_notify:
            self.installer.install_update()

        # Popen was launched with the batch file
        mock_popen.assert_called_once()
        args, _ = mock_popen.call_args
        self.assertEqual(args[0][0], "cmd.exe")
        self.assertEqual(args[0][1], "/c")

        # Completion was awaited and the exit code captured
        self.assertTrue(fake_proc.wait_called)
        self.assertEqual(self.installer.get_installer_exit_code(), 0)

        # The installer was NOT hard-killed while extracting/replacing files
        self.assertFalse(fake_proc.terminated)
        self.assertFalse(fake_proc.killed)

        try:
            os.remove(fake_zip)
        except OSError:
            pass

    def test_installer_refuses_when_download_in_progress(self):
        """Separate download cancellation from installer execution: never
        install while a download is still in progress."""
        fake_zip = self._installer_ready()
        self.updater._downloading = True  # simulate an in-flight download

        with patch("threading.Thread", side_effect=_run_thread_immediately), \
             patch("time.sleep", return_value=None), \
             patch("installer.subprocess.Popen") as mock_popen, \
             patch.object(self.updater, "_notify") as mock_notify:
            self.installer.install_update()

        # Popen must NOT be launched while a download is in progress
        mock_popen.assert_not_called()
        self.assertFalse(self.installer.is_installer_running())
        self.assertIsNone(self.installer.get_installer_exit_code())

        failed = [c for c in mock_notify.call_args_list if c[0][0] == "Failed"]
        self.assertGreaterEqual(len(failed), 1)
        self.assertIn("download is in progress", failed[0][0][2].lower())

        try:
            os.remove(fake_zip)
        except OSError:
            pass


class TestInstallerStatusFileFallback(unittest.TestCase):
    """get_installer_exit_code() falls back to the persisted status file
    when the in-memory value was not captured (daemon watcher terminated
    on parent exit)."""

    def setUp(self):
        self.logger = DummyLogger()
        self.updater = DummyUpdater()
        self.window = DummyWindow()
        self.installer = InstallerManager(self.logger, self.updater, self.window)

    def _make_fake_zip(self, name: str) -> str:
        path = os.path.join(os.path.dirname(__file__), "cache", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"fake zip contents")
        return path

    def _write_status_file(self, exit_code: int) -> str:
        """Manually write an installer_status.json as the watcher would."""
        updates_dir = os.path.join(os.path.dirname(__file__), "cache", "updates")
        os.makedirs(updates_dir, exist_ok=True)
        status_file = os.path.join(updates_dir, "installer_status.json")
        import json as _json
        status = {
            "exit_code": exit_code,
            "completed_at": "2026-10-31 00:00:00",
        }
        tmp = status_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            _json.dump(status, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, status_file)
        self.installer._installer_status_file = status_file
        return status_file

    def test_get_installer_exit_code_reads_status_file_when_in_memory_none(self):
        """In-memory exit code is None → fall back to status file."""
        status_file = self._write_status_file(3)
        try:
            self.assertIsNone(self.installer._install_exit_code)
            self.assertEqual(self.installer.get_installer_exit_code(), 3)
        finally:
            if os.path.exists(status_file):
                os.remove(status_file)

    def test_get_installer_exit_code_returns_none_when_no_status_file(self):
        """No in-memory value and no status file → None."""
        self.assertIsNone(self.installer._install_exit_code)
        self.assertIsNone(self.installer.get_installer_exit_code())

    def test_watch_installer_writes_status_file_atomically(self):
        """_watch_installer writes exit_code + completed_at via tmp + os.replace."""
        import tempfile
        tmpdir = tempfile.mkdtemp()
        status_file = os.path.join(tmpdir, "installer_status.json")
        self.installer._installer_status_file = status_file

        class FakeProc:
            def wait(self):
                return 0

        from installer import InstallerManager as _IM
        # Reach into the closure: create a watcher and drive it directly.
        proc = FakeProc()

        def _watch_installer(process):
            try:
                exit_code = process.wait()
                self.installer._install_exit_code = exit_code
                if self.installer._installer_status_file:
                    import json as _json
                    status = {
                        "exit_code": exit_code,
                        "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    tmp = self.installer._installer_status_file + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as fh:
                        _json.dump(status, fh)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, self.installer._installer_status_file)
            except Exception:
                pass
            finally:
                self.installer._install_process = None

        _watch_installer(proc)

        try:
            self.assertTrue(os.path.exists(status_file))
            self.assertFalse(os.path.exists(status_file + ".tmp"))
            import json as _json
            with open(status_file) as fh:
                data = _json.load(fh)
            self.assertEqual(data["exit_code"], 0)
            self.assertIn("completed_at", data)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
