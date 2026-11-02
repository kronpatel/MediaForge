"""
test_backend_manager.py – Unit tests for BackendManager (Phase 3 Fixes #3 and #7)

Tests cover:
  - Fix #3: _discover_backend_pid() catches RuntimeError from psutil.net_connections()
  - Fix #7: _launch_backend() uses MediaForgeBackend.exe in frozen mode (no system-Python fallback)
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure companion/ is on the path so imports resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend_manager import BackendManager, BackendStatus  # noqa: E402


class DummyLogger:
    def info(self, msg, *args, **kwargs): pass
    def warning(self, msg, *args, **kwargs): pass
    def error(self, msg, *args, **kwargs): pass
    def log(self, msg, level="INFO", *args, **kwargs): pass


# ---------------------------------------------------------------------------
# Fix #3: _discover_backend_pid() must catch RuntimeError from net_connections()
# ---------------------------------------------------------------------------

class TestFix3DiscoverBackendPidRuntimeError(unittest.TestCase):
    """Fix #3: _discover_backend_pid() must not crash when net_connections() raises RuntimeError."""

    def setUp(self):
        self.logger = DummyLogger()
        self.mgr = BackendManager(self.logger)

    def test_discover_backend_pid_runtime_error_returns_none(self):
        """psutil.net_connections() raising RuntimeError must not crash; returns None."""
        with patch("psutil.net_connections", side_effect=RuntimeError("boom")), \
             patch("psutil.process_iter", return_value=[]):
            result = self.mgr._discover_backend_pid()
        self.assertIsNone(result)

    def test_discover_backend_pid_access_denied_returns_none(self):
        """psutil.AccessDenied from net_connections must also not crash."""
        import psutil
        with patch("psutil.net_connections", side_effect=psutil.AccessDenied("denied")), \
             patch("psutil.process_iter", return_value=[]):
            result = self.mgr._discover_backend_pid()
        self.assertIsNone(result)

    def test_discover_backend_pid_permission_error_returns_none(self):
        """PermissionError from net_connections must also not crash."""
        with patch("psutil.net_connections", side_effect=PermissionError("denied")), \
             patch("psutil.process_iter", return_value=[]):
            result = self.mgr._discover_backend_pid()
        self.assertIsNone(result)

    def test_discover_backend_pid_normal_path_returns_none_when_no_port_match(self):
        """When net_connections works but no port matches, returns None gracefully."""
        fake_conn = MagicMock()
        fake_conn.laddr.port = 9999
        fake_conn.pid = 1234
        with patch("psutil.net_connections", return_value=[fake_conn]), \
             patch("psutil.process_iter", return_value=[]):
            result = self.mgr._discover_backend_pid()
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Fix #7: _launch_backend() python_exe selection in frozen mode
# ---------------------------------------------------------------------------

class TestFix7LaunchBackendFrozenBackendExe(unittest.TestCase):
    """Fix #7: _launch_backend() must launch MediaForgeBackend.exe in frozen mode, with no system-Python fallback."""

    def setUp(self):
        self.logger = DummyLogger()
        self.mgr = BackendManager(self.logger)

    def _patch_launch(self, frozen, exe_path, backend_exists):
        """Helper to patch sys.frozen, sys.executable, os.path.exists, subprocess.Popen."""
        patcher_frozen = patch.object(sys, "frozen", frozen, create=True)
        patcher_exe = patch.object(sys, "executable", exe_path)
        patcher_exists = patch("os.path.exists", return_value=backend_exists)
        patcher_popen = patch("subprocess.Popen")
        patcher_frozen.start()
        patcher_exe.start()
        patcher_exists.start()
        mock_popen = patcher_popen.start()
        return mock_popen, (patcher_frozen, patcher_exe, patcher_exists, patcher_popen)

    def _unpatch_launch(self, patchers):
        for p in reversed(patchers):
            p.stop()

    def test_frozen_mode_uses_backend_exe_when_available(self):
        """Frozen: cmd is the MediaForgeBackend.exe next to the companion EXE."""
        mock_popen, patchers = self._patch_launch(
            frozen=True,
            exe_path=r"C:\MediaForge\MediaForge.exe",
            backend_exists=True,
        )
        try:
            self.mgr._launch_backend()
            call_args = mock_popen.call_args[0][0]
            self.assertEqual(call_args, [r"C:\MediaForge\MediaForgeBackend.exe"])
        finally:
            self._unpatch_launch(patchers)


    def test_frozen_mode_raises_when_backend_exe_missing(self):
        """Frozen: missing MediaForgeBackend.exe raises FileNotFoundError (no 'python' fallback)."""
        mock_popen, patchers = self._patch_launch(
            frozen=True,
            exe_path=r"C:\MediaForge\MediaForge.exe",
            backend_exists=False,
        )
        try:
            with self.assertRaises(FileNotFoundError):
                self.mgr._launch_backend()
            mock_popen.assert_not_called()
        finally:
            self._unpatch_launch(patchers)

    def test_source_mode_uses_sys_executable_and_app_py(self):
        """Source: cmd is [sys.executable, 'app.py'] unchanged."""
        mock_popen, patchers = self._patch_launch(
            frozen=False,
            exe_path=sys.executable,
            backend_exists=False,
        )
        try:
            self.mgr._launch_backend()
            call_args = mock_popen.call_args[0][0]
            self.assertEqual(call_args, [sys.executable, "app.py"])
        finally:
            self._unpatch_launch(patchers)


class TestPhase4BackendRecovery(unittest.TestCase):
    """Phase 4: a managed backend outage is recovered with bounded restarts."""

    def setUp(self):
        self.mgr = BackendManager(DummyLogger())

    def tearDown(self):
        self.mgr.shutdown()

    def test_health_failure_notifies_and_starts_one_recovery_worker(self):
        self.mgr._is_managed = True
        self.mgr._process = MagicMock()
        with patch("backend_manager.threading.Thread") as thread_cls:
            self.mgr._handle_health_failure()

        self.assertEqual(self.mgr.status, BackendStatus.CRASHED)
        thread_cls.assert_called_once()
        self.assertEqual(thread_cls.call_args.kwargs["name"], "BackendRecovery")
        self.assertTrue(self.mgr._recovery_in_progress)

    def test_recovery_retries_until_backend_runs(self):
        self.mgr._is_managed = True
        self.mgr._process = MagicMock()
        self.mgr._set_status(BackendStatus.CRASHED, "crashed")
        starts = []

        def fake_start(allow_adopt=True):
            starts.append(allow_adopt)
            with self.mgr._lock:
                self.mgr._is_managed = True
            self.mgr._set_status(BackendStatus.RUNNING, "recovered")

        with patch.object(self.mgr, "_terminate_backend"), \
             patch.object(self.mgr, "start", side_effect=fake_start), \
             patch("backend_manager.time.sleep"):
            self.mgr._recover_backend(self.mgr._lifecycle_generation)

        self.assertEqual(starts, [False])
        self.assertFalse(self.mgr._recovery_in_progress)


if __name__ == "__main__":
    unittest.main()
