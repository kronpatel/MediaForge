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


if __name__ == "__main__":
    unittest.main()
