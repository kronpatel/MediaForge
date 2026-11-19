"""
test_updater.py – Unit tests for UpdateManager (Phase 3 Fix #8)

Tests cover:
  - Fix #8: cancel_download() waits for the download thread to exit before cleanup
"""

import os
import json
import sys
import threading
import time
import unittest
import tempfile
import hashlib
from unittest.mock import MagicMock, patch

# Ensure companion/ is on the path so imports resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from updater import UpdateManager  # noqa: E402


class DummyLogger:
    def info(self, msg, *args, **kwargs): pass
    def warning(self, msg, *args, **kwargs): pass
    def error(self, msg, *args, **kwargs): pass
    def log(self, msg, level="INFO", *args, **kwargs): pass


class TestFix8CancelDownloadSynchronization(unittest.TestCase):
    """Fix #8: cancel_download() must wait for the download thread to finish
    before calling _cleanup_temp_file(), avoiding a race with the thread's
    own temp-file cleanup."""

    def setUp(self):
        self.logger = DummyLogger()
        self.updater = UpdateManager(self.logger)

    def tearDown(self):
        self.updater._download_stop_event.set()
        self.updater._download_running = False

    def test_cancel_download_when_not_running_returns_immediately(self):
        """No download in progress → cancel_download returns without doing anything."""
        self.updater._download_running = False
        with patch.object(self.updater, "_cleanup_temp_file") as mock_cleanup:
            self.updater.cancel_download()
        mock_cleanup.assert_not_called()

    def test_cancel_download_waits_for_thread_to_exit(self):
        """cancel_download polls _download_running until the thread exits,
        then calls _cleanup_temp_file() exactly once."""
        self.updater._download_running = True
        self.updater._download_stop_event.clear()

        # Simulate a download thread that eventually clears _download_running.
        released = threading.Event()

        def _fake_download_thread():
            time.sleep(0.1)
            released.set()
            with self.updater._download_lock:
                self.updater._downloading = False

        t = threading.Thread(target=_fake_download_thread, daemon=True)
        t.start()

        with patch.object(self.updater, "_cleanup_temp_file") as mock_cleanup:
            self.updater.cancel_download()

        t.join(timeout=5.0)
        # _cleanup_temp_file should be called (as final cleanup, no-op if thread handled it)
        mock_cleanup.assert_called_once()
        released.set()

    def test_cancel_download_sets_stop_event(self):
        """cancel_download sets _download_stop_event so the download thread observes it."""
        self.updater._download_running = True
        self.updater._download_stop_event.clear()

        with patch.object(self.updater, "_cleanup_temp_file"):
            self.updater.cancel_download()

        self.assertTrue(self.updater._download_stop_event.is_set())

    def test_cancel_download_times_out_after_5_seconds(self):
        """cancel_download must not wait forever — it gives up after 5 seconds."""
        self.updater._download_running = True
        self.updater._download_stop_event.clear()

        # Never clear _download_running — simulates a stuck download thread.
        with patch.object(self.updater, "_cleanup_temp_file") as mock_cleanup:
            start = time.monotonic()
            self.updater.cancel_download()
            elapsed = time.monotonic() - start

        self.assertTrue(mock_cleanup.called)
        # Should not take more than ~5.5 seconds (5s deadline + small overhead)
        self.assertLess(elapsed, 6.0, f"cancel_download took too long: {elapsed:.2f}s")


class TestPhase4InstallerRecovery(unittest.TestCase):
    """Phase 4: recover installer completion after Companion restart."""

    def setUp(self):
        self.updater = UpdateManager(DummyLogger())

    def tearDown(self):
        self.updater._download_stop_event.set()
        self.updater._download_running = False

    def test_success_status_clears_stale_installation_state(self):
        status_path = os.path.join(os.path.dirname(__file__), "cache", "phase4_status.json")
        os.makedirs(os.path.dirname(status_path), exist_ok=True)
        with open(status_path, "w", encoding="utf-8") as fh:
            json.dump({"exit_code": 0, "completed_at": "2026-11-03T00:00:00"}, fh)

        self.updater._installation_in_progress = True
        self.updater._pending_install = True
        self.updater._installer_path = status_path
        with patch.object(self.updater, "_installer_status_candidates", return_value=[status_path]), \
             patch.object(self.updater, "_save_cache"):
            self.updater._recover_installation_state()

        self.assertFalse(self.updater._installation_in_progress)
        self.assertFalse(self.updater._pending_install)
        self.assertEqual(self.updater._installer_state, "Completed")
        self.assertEqual(self.updater._last_exit_code, 0)
        os.remove(status_path)

    def test_missing_status_marks_installation_interrupted(self):
        self.updater._installation_in_progress = True
        self.updater._pending_install = True
        with patch.object(self.updater, "_installer_status_candidates", return_value=[]), \
             patch.object(self.updater, "_save_cache"):
            self.updater._recover_installation_state()

        self.assertFalse(self.updater._installation_in_progress)
        self.assertTrue(self.updater._pending_install)
        self.assertEqual(self.updater._installer_state, "Interrupted")


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.headers = {"content-length": str(len(payload))}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=0):
        yield self.payload


class _FakeBackendManager:
    def __init__(self, path: str, idle: bool = True):
        self.path = path
        self.idle = idle
        self.stop_calls = 0
        self.start_calls = 0
        self.status = type("Status", (), {"name": "RUNNING"})()

    def fetch_version(self):
        return "1.0.0"

    def get_backend_executable_path(self):
        return self.path

    def is_idle_for_update(self):
        return self.idle

    def stop(self):
        self.stop_calls += 1

    def start(self, allow_adopt=False):
        self.start_calls += 1


class TestAutomaticBackendUpdate(unittest.TestCase):
    """Backend-only updates replace one binary and never touch browser data."""

    def setUp(self):
        self.updater = UpdateManager(DummyLogger())
        self.tempdir = tempfile.TemporaryDirectory()
        self.backend_path = os.path.join(self.tempdir.name, "MediaForgeBackend.exe")
        with open(self.backend_path, "wb") as fh:
            fh.write(b"old backend")
        self.manager = _FakeBackendManager(self.backend_path)
        self.updater.attach_backend_manager(self.manager)
        self.payload = b"new backend binary"
        self.updater._backend_asset_url = "https://example.invalid/backend.exe"
        self.updater._backend_asset_size = len(self.payload)
        self.updater._backend_asset_digest = hashlib.sha256(self.payload).hexdigest()
        self.updater._backend_version = "v2.1.0"

    def tearDown(self):
        self.updater.shutdown()
        self.tempdir.cleanup()

    def test_backend_update_replaces_binary_and_restarts_only_backend(self):
        new_file = os.path.join(self.tempdir.name, "MediaForgeBackend.exe.download")
        with patch("updater.sys.frozen", True, create=True), \
             patch("updater.BACKEND_DOWNLOAD_FILE", new_file), \
             patch.object(self.updater, "_get_session", return_value=MagicMock(
                 get=MagicMock(return_value=_FakeResponse(self.payload)))):
            self.updater._download_and_install_backend()

        with open(self.backend_path, "rb") as fh:
            self.assertEqual(fh.read(), self.payload)
        self.assertEqual(self.manager.stop_calls, 1)
        self.assertEqual(self.manager.start_calls, 1)
        self.assertFalse(os.path.exists(self.backend_path + ".backup"))

    def test_backend_update_is_deferred_when_queue_is_busy(self):
        self.manager.idle = False
        with patch("updater.sys.frozen", True, create=True), \
             patch.object(self.updater, "_get_session") as get_session:
            self.updater._download_and_install_backend()

        get_session.assert_not_called()
        self.assertEqual(self.manager.stop_calls, 0)
        with open(self.backend_path, "rb") as fh:
            self.assertEqual(fh.read(), b"old backend")

    def test_safe_replace_retries_on_permission_error_and_succeeds(self):
        """BUG-12: _safe_replace retries up to 5 times on transient Windows PermissionError."""
        from updater import _safe_replace, REPLACE_RETRY_DELAY
        replace_calls = [0]
        sleep_delays = []

        def mock_replace(src, dst):
            replace_calls[0] += 1
            if replace_calls[0] < 3:
                raise PermissionError("[WinError 5] Access is denied")
            return None

        with patch("updater.os.replace", side_effect=mock_replace), \
             patch("updater.time.sleep", side_effect=lambda d: sleep_delays.append(d)):
            _safe_replace("source.exe", "dest.exe")

        self.assertEqual(replace_calls[0], 3, "Must retry and succeed on 3rd attempt")
        self.assertEqual(sleep_delays, [REPLACE_RETRY_DELAY, REPLACE_RETRY_DELAY])

    def test_safe_replace_raises_if_all_attempts_fail(self):
        """BUG-12: _safe_replace raises PermissionError if all retry attempts fail."""
        from updater import _safe_replace, REPLACE_RETRY_ATTEMPTS
        replace_calls = [0]

        def mock_replace(src, dst):
            replace_calls[0] += 1
            raise PermissionError("[WinError 32] In use")

        with patch("updater.os.replace", side_effect=mock_replace), \
             patch("updater.time.sleep"):
            with self.assertRaises(PermissionError):
                _safe_replace("source.exe", "dest.exe")

        self.assertEqual(replace_calls[0], REPLACE_RETRY_ATTEMPTS)

    def test_backend_update_retries_on_transient_file_lock(self):
        """BUG-12: Hot-swap update succeeds when os.replace encounters initial Windows file locking."""
        new_file = os.path.join(self.tempdir.name, "MediaForgeBackend.exe.download")
        replace_calls = [0]

        real_replace = os.replace

        def transient_replace(src, dst):
            replace_calls[0] += 1
            if src == self.backend_path and replace_calls[0] <= 2:
                raise PermissionError("[WinError 5] Access is denied (PE lock)")
            return real_replace(src, dst)

        with patch("updater.sys.frozen", True, create=True), \
             patch("updater.BACKEND_DOWNLOAD_FILE", new_file), \
             patch("updater.os.replace", side_effect=transient_replace), \
             patch("updater.time.sleep"), \
             patch.object(self.updater, "_get_session", return_value=MagicMock(
                 get=MagicMock(return_value=_FakeResponse(self.payload)))):
            self.updater._download_and_install_backend()

        with open(self.backend_path, "rb") as fh:
            self.assertEqual(fh.read(), self.payload)
        self.assertGreaterEqual(replace_calls[0], 2, "Must retry after initial lock failure")
        self.assertEqual(self.manager.start_calls, 1)


if __name__ == "__main__":
    unittest.main()
