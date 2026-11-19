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

from backend_manager import BackendManager, BackendStatus, HTTP_TIMEOUT  # noqa: E402


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


class TestGetQueueStatusContract(unittest.TestCase):
    """Regression test for BUG-04: get_queue() includes all status buckets."""

    def setUp(self):
        self.mgr = BackendManager(DummyLogger())

    def test_get_queue_includes_paused_starting_retrying_and_unknown_keys(self):
        payload = {
            "success": True,
            "queue": {
                "active": {"id": "job-active", "status": "downloading", "url": "https://example.com/1"},
                "queued": [{"id": "job-queued", "status": "queued", "url": "https://example.com/2"}],
                "paused": [{"id": "job-paused", "status": "paused", "url": "https://example.com/3"}],
                "starting": [{"id": "job-starting", "status": "starting", "url": "https://example.com/4"}],
                "retrying": [{"id": "job-retrying", "status": "retrying", "url": "https://example.com/5"}],
                "custom_bucket": [{"id": "job-custom", "status": "custom", "url": "https://example.com/6"}],
                "queued_count": 1,
                "paused_count": 1,
                "history": [{"id": "job-completed", "status": "completed", "url": "https://example.com/7"}],
            },
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload

        with patch.object(self.mgr, "_send_request", return_value=mock_resp):
            jobs = self.mgr.get_queue()

        returned_ids = [j["id"] for j in jobs]
        self.assertIn("job-active", returned_ids)
        self.assertIn("job-queued", returned_ids)
        self.assertIn("job-paused", returned_ids)
        self.assertIn("job-starting", returned_ids)
        self.assertIn("job-retrying", returned_ids)
        self.assertIn("job-custom", returned_ids)
        self.assertNotIn("job-completed", returned_ids, "history items must not leak into active queue")
        self.assertEqual(len(jobs), 6)


class TestBug13ProcessDiscoveryFallback(unittest.TestCase):
    """BUG-13: Process discovery fallback finds MediaForgeBackend.exe and python app.py."""

    def setUp(self):
        self.mgr = BackendManager(DummyLogger())

    def _make_proc(self, pid: int, name: str, cmdline: list[str]):
        mock = MagicMock()
        mock.info = {"pid": pid, "name": name, "cmdline": cmdline}
        return mock

    def test_discover_backend_pid_finds_frozen_exe(self):
        """Fallback scan finds MediaForgeBackend.exe when net_connections is restricted."""
        import psutil
        fake_proc = self._make_proc(4455, "MediaForgeBackend.exe", ["MediaForgeBackend.exe"])
        with patch("psutil.net_connections", side_effect=psutil.AccessDenied("denied")), \
             patch("psutil.process_iter", return_value=[fake_proc]):
            pid = self.mgr._discover_backend_pid()
        self.assertEqual(pid, 4455)

    def test_discover_backend_pid_finds_python_app_py(self):
        """Fallback scan finds python backend/app.py in source mode."""
        import psutil
        fake_proc = self._make_proc(6677, "python.exe", ["python", "backend/app.py"])
        with patch("psutil.net_connections", side_effect=psutil.AccessDenied("denied")), \
             patch("psutil.process_iter", return_value=[fake_proc]):
            pid = self.mgr._discover_backend_pid()
        self.assertEqual(pid, 6677)

    def test_discover_backend_pid_ignores_unrelated_exe(self):
        """Fallback scan ignores unrelated processes like notepad.exe or idle.py."""
        import psutil
        p1 = self._make_proc(1001, "notepad.exe", ["notepad.exe"])
        p2 = self._make_proc(1002, "python.exe", ["python", "other_script.py"])
        with patch("psutil.net_connections", side_effect=psutil.AccessDenied("denied")), \
             patch("psutil.process_iter", return_value=[p1, p2]):
            pid = self.mgr._discover_backend_pid()
        self.assertIsNone(pid)

    def test_discover_backend_pid_falls_back_on_permission_error(self):
        """Fallback scan runs when net_connections raises PermissionError."""
        import psutil
        fake_proc = self._make_proc(7788, "MediaForgeBackend.exe", ["MediaForgeBackend.exe"])
        with patch("psutil.net_connections", side_effect=PermissionError("denied")), \
             patch("psutil.process_iter", return_value=[fake_proc]):
            pid = self.mgr._discover_backend_pid()
        self.assertEqual(pid, 7788)

    def test_discover_backend_pid_returns_none_when_process_iter_fails(self):
        """If process_iter raises, _discover_backend_pid returns None without crashing."""
        import psutil
        with patch("psutil.net_connections", side_effect=psutil.AccessDenied("denied")), \
                patch("psutil.process_iter", side_effect=RuntimeError("boom")):
            pid = self.mgr._discover_backend_pid()
        self.assertIsNone(pid)


# ---------------------------------------------------------------------------
# queue_download (Direct URL Paste / Drag-and-Drop)
# ---------------------------------------------------------------------------

class TestQueueDownload(unittest.TestCase):
    """queue_download() must POST to /download and translate results."""

    def setUp(self):
        self.mgr = BackendManager(DummyLogger())

    def _mock_response(self, status_code=202, payload=None):
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = payload or {
            "success": True,
            "job": {"id": "job-1", "url": "https://youtu.be/x", "mode": "mp3"},
        }
        return mock

    def test_queue_download_posts_correct_payload(self):
        mock = self._mock_response()
        with patch.object(self.mgr, "_send_request", return_value=mock) as send:
            job, error = self.mgr.queue_download("https://youtu.be/x", mode="mp3", retries=3)

        self.assertIsNone(error)
        self.assertEqual(job["id"], "job-1")
        self.assertEqual(send.call_args.args, ("POST", "/download"))
        self.assertEqual(send.call_args.kwargs["timeout"], HTTP_TIMEOUT)
        self.assertEqual(
            send.call_args.kwargs["json"],
            {"url": "https://youtu.be/x", "mode": "mp3", "retries": 3, "output_folder": None},
        )

    def test_queue_download_defaults_mode_and_retries(self):
        mock = self._mock_response()
        with patch.object(self.mgr, "_send_request", return_value=mock) as send:
            self.mgr.queue_download("https://youtu.be/x")
        payload = send.call_args.kwargs["json"]
        self.assertEqual(payload["mode"], "1080p")
        self.assertEqual(payload["retries"], 2)

    def test_queue_download_transport_error_returns_message(self):
        with patch.object(self.mgr, "_send_request", return_value=None):
            job, error = self.mgr.queue_download("https://youtu.be/x")
        self.assertIsNone(job)
        self.assertIsInstance(error, str)
        self.assertIn("backend", error.lower())

    def test_queue_download_invalid_json_returns_message(self):
        mock = MagicMock()
        mock.status_code = 200
        mock.json.side_effect = ValueError("not json")
        with patch.object(self.mgr, "_send_request", return_value=mock):
            job, error = self.mgr.queue_download("https://youtu.be/x")
        self.assertIsNone(job)
        self.assertEqual(error, "Invalid response from the backend.")

    def test_queue_download_backend_rejects_with_message(self):
        mock = self._mock_response(
            status_code=400,
            payload={
                "success": False,
                "message": "Please select a download quality or format.",
            },
        )
        with patch.object(self.mgr, "_send_request", return_value=mock):
            job, error = self.mgr.queue_download("https://youtu.be/x")
        self.assertIsNone(job)
        self.assertEqual(error, "Please select a download quality or format.")

    def test_queue_download_accepted_but_no_job(self):
        mock = self._mock_response(status_code=202, payload={"success": True})
        with patch.object(self.mgr, "_send_request", return_value=mock):
            job, error = self.mgr.queue_download("https://youtu.be/x")
        self.assertIsNone(job)
        self.assertEqual(error, "Backend accepted the download but returned no job.")


if __name__ == "__main__":
    unittest.main()
