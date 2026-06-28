import os
import json
import time
import unittest
import sys
from unittest.mock import MagicMock, patch, mock_open

from updater import UpdateManager, CACHE_FILE, FINAL_DOWNLOAD_FILE
from installer import InstallerManager

class DummyLogger:
    def info(self, msg, *args, **kwargs): pass
    def warning(self, msg, *args, **kwargs): pass
    def error(self, msg, *args, **kwargs): pass
    def log(self, msg, level="INFO", *args, **kwargs): pass

class DummyWindow:
    def __init__(self):
        self.prepare_called = False
        self.alerts = []
    def prepare_for_installation(self):
        self.prepare_called = True
    def after(self, ms, func):
        func()
    def show_alert(self, title, msg):
        self.alerts.append((title, msg))

class TestInstallerManager(unittest.TestCase):

    def setUp(self):
        self.logger = DummyLogger()
        self.updater = UpdateManager(self.logger)
        self.window = DummyWindow()
        self.installer = InstallerManager(self.logger, self.updater, self.window)

        # Setup safe os.path.exists mocking to prevent unittest/sys hangs
        self.real_exists = os.path.exists
        self.installer_file_exists = True

        def safe_exists(path):
            abs_path = os.path.abspath(path)
            abs_installer = os.path.abspath(FINAL_DOWNLOAD_FILE)
            if abs_path == abs_installer:
                return self.installer_file_exists
            return self.real_exists(path)

        self.exists_patcher = patch("os.path.exists", side_effect=safe_exists)
        self.exists_patcher.start()
        self._clear_files()

    def tearDown(self):
        self.exists_patcher.stop()
        self.updater.shutdown()
        self._clear_files()

    def _clear_files(self):
        # Temporarily disable the exists patcher during cleanup to allow real filesystem deletes
        self.installer_file_exists = False
        for f in (CACHE_FILE, FINAL_DOWNLOAD_FILE):
            try:
                # Use real path checks for cleanup
                if self.real_exists(f):
                    os.remove(f)
            except OSError:
                pass

    def test_validation_no_pending_install(self):
        self.updater._pending_install = False
        self.installer.install_update()
        self.assertFalse(self.installer._installing)

    def test_validation_missing_file(self):
        self.installer_file_exists = False
        self.updater._pending_install = True
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._installer_version = "v1.2.0"
        self.updater._latest_version = "v1.2.0"

        self.installer._run_install_loop()
        self.assertEqual(self.updater._installer_state, "Failed")
        self.assertFalse(self.updater._pending_install)

    @patch("os.path.getsize")
    def test_validation_size_mismatch(self, mock_getsize):
        self.installer_file_exists = True
        mock_getsize.return_value = 1000
        self.updater._pending_install = True
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._installer_version = "v1.2.0"
        self.updater._latest_version = "v1.2.0"
        self.updater._asset_size = 2000

        self.installer._run_install_loop()
        self.assertEqual(self.updater._installer_state, "Failed")
        self.assertIn("size mismatch", self.updater._last_install_error)

    @patch("os.path.getsize")
    def test_validation_version_mismatch(self, mock_getsize):
        self.installer_file_exists = True
        mock_getsize.return_value = 2000
        self.updater._pending_install = True
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._installer_version = "v1.1.0"
        self.updater._latest_version = "v1.2.0"
        self.updater._asset_size = 2000

        self.installer._run_install_loop()
        self.assertEqual(self.updater._installer_state, "Failed")
        self.assertIn("version mismatch", self.updater._last_install_error)

    @patch("os.path.getsize")
    @patch("builtins.open")
    def test_validation_hash_mismatch(self, mock_open_file, mock_getsize):
        self.installer_file_exists = True
        mock_getsize.return_value = 2000
        self.updater._pending_install = True
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._installer_version = "v1.2.0"
        self.updater._latest_version = "v1.2.0"
        self.updater._asset_size = 2000
        self.updater._installer_sha256 = "expected_hash_value"

        mock_open_file.return_value.__enter__.return_value.read.side_effect = [b"", b""]

        self.installer._run_install_loop()
        self.assertEqual(self.updater._installer_state, "Failed")
        self.assertIn("integrity verification failed", self.updater._last_install_error)

    @patch("os.path.getsize")
    @patch("builtins.open")
    @patch("subprocess.Popen")
    @patch("os.remove")
    @patch("os._exit")
    def test_successful_launch_exit_code_zero(self, mock_exit, mock_remove, mock_popen, mock_open_file, mock_getsize):
        self.installer_file_exists = True
        mock_getsize.return_value = 2000
        mock_open_file.return_value.__enter__.return_value.read.return_value = b""
        self.updater._pending_install = True
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._installer_version = "v1.2.0"
        self.updater._latest_version = "v1.2.0"
        self.updater._asset_size = 2000
        self.updater._installer_sha256 = ""
        self.updater._restart_after_install = True

        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0
        mock_proc.poll.return_value = None
        mock_popen.side_effect = [mock_proc, mock_proc]

        self.installer._run_install_loop()

        self.assertTrue(self.window.prepare_called)
        mock_remove.assert_called_once_with(FINAL_DOWNLOAD_FILE)
        self.assertEqual(self.updater._last_install_result, "success")
        self.assertFalse(self.updater._pending_install)
        mock_exit.assert_called_once_with(0)

    @patch("os.path.getsize")
    @patch("builtins.open")
    @patch("subprocess.Popen")
    def test_failed_launch_non_zero_exit_code(self, mock_popen, mock_open_file, mock_getsize):
        self.installer_file_exists = True
        mock_getsize.return_value = 2000
        mock_open_file.return_value.__enter__.return_value.read.return_value = b""
        self.updater._pending_install = True
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._installer_version = "v1.2.0"
        self.updater._latest_version = "v1.2.0"
        self.updater._asset_size = 2000
        self.updater._installer_sha256 = ""

        mock_proc = MagicMock()
        mock_proc.wait.return_value = 5
        mock_proc.returncode = 5
        mock_popen.return_value = mock_proc

        self.installer._run_install_loop()

        self.assertEqual(self.updater._installer_state, "Failed")
        self.assertEqual(self.updater._last_install_result, "failed")
        self.assertEqual(self.updater._last_exit_code, 5)
        self.assertTrue(self.updater._pending_install)

    @patch("os.path.getsize")
    @patch("builtins.open")
    @patch("subprocess.Popen")
    @patch("installer.InstallerManager._launch_elevated")
    def test_uac_elevation_uac_cancel(self, mock_launch_elevated, mock_popen, mock_open_file, mock_getsize):
        self.installer_file_exists = True
        mock_getsize.return_value = 2000
        mock_open_file.return_value.__enter__.return_value.read.return_value = b""
        self.updater._pending_install = True
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._installer_version = "v1.2.0"
        self.updater._latest_version = "v1.2.0"
        self.updater._asset_size = 2000
        self.updater._installer_sha256 = ""

        ose = OSError()
        ose.winerror = 740
        mock_popen.side_effect = ose
        mock_launch_elevated.side_effect = OSError("UAC elevation cancelled by user")

        self.installer._run_install_loop()

        self.assertEqual(self.updater._installer_state, "Failed")
        self.assertIn("Launch failed", self.updater._last_install_error)
        self.assertTrue(self.updater._pending_install)

    @patch("os.path.getsize")
    @patch("builtins.open")
    @patch("subprocess.Popen")
    @patch("time.sleep")
    def test_launch_timeout(self, mock_sleep, mock_popen, mock_open_file, mock_getsize):
        self.installer_file_exists = True
        mock_getsize.return_value = 2000
        mock_open_file.return_value.__enter__.return_value.read.return_value = b""
        self.updater._pending_install = True
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._installer_version = "v1.2.0"
        self.updater._latest_version = "v1.2.0"
        self.updater._asset_size = 2000
        self.updater._installer_sha256 = ""

        def hang_launcher(*args, **kwargs):
            time.sleep(100.0)
        mock_popen.side_effect = hang_launcher

        with patch("threading.Event.wait", return_value=False):
            self.installer._run_install_loop()

        self.assertEqual(self.updater._installer_state, "Failed")
        self.assertIn("timed out", self.updater._last_install_error)
        self.assertTrue(self.updater._pending_install)

    @patch("os.remove")
    @patch("time.sleep")
    def test_file_lock_retries(self, mock_sleep, mock_remove):
        self.installer_file_exists = True
        mock_remove.side_effect = [PermissionError("locked"), PermissionError("locked"), None]
        
        self.installer._delete_installer_file_safe(FINAL_DOWNLOAD_FILE)
        self.assertEqual(mock_remove.call_count, 3)

        mock_remove.reset_mock()
        mock_remove.side_effect = PermissionError("locked")
        self.installer._delete_installer_file_safe(FINAL_DOWNLOAD_FILE)
        self.assertEqual(mock_remove.call_count, 3)
        self.assertIn("Another application is using the installer", self.updater._last_install_error)

    def test_stale_cache_recovery_at_startup(self):
        self.installer_file_exists = False
        self.updater._latest_version = "v1.2.0"
        self.updater._asset_size = 2000
        self.updater._pending_install = True
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._installer_version = "v1.2.0"
        self.updater._save_cache()

        self.updater._load_cache()
        self.assertFalse(self.updater._pending_install)

    def test_recovery_crash_before_installer_launch(self):
        # Cache says installation was in progress, but version is mismatched on startup -> failed state
        self.installer_file_exists = True
        self.updater._installation_in_progress = True
        self.updater._latest_version = "v1.3.0"
        self.updater._installer_version = "v1.2.0"
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._asset_size = 2000
        self.updater._save_cache()

        self.updater._load_cache()
        self.assertFalse(self.updater._installation_in_progress)
        self.assertEqual(self.updater._installer_state, "Failed")

    @patch("os.path.getsize")
    def test_recovery_crash_during_waiting_for_exit_valid(self, mock_getsize):
        # Cache says installation was in progress, file is valid -> recovers to Pending Install / Idle
        self.installer_file_exists = True
        mock_getsize.return_value = 2000
        self.updater._installation_in_progress = True
        self.updater._latest_version = "v1.2.0"
        self.updater._installer_version = "v1.2.0"
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._asset_size = 2000
        self.updater._save_cache()

        self.updater._load_cache()
        self.assertFalse(self.updater._installation_in_progress)
        self.assertTrue(self.updater._pending_install)
        self.assertEqual(self.updater._installer_state, "Idle")

    def test_recovery_after_successful_update_exit(self):
        # Cache says installation in progress, file missing, but COMPANION_VERSION matches installer version -> Completed
        self.installer_file_exists = False
        from updater import COMPANION_VERSION
        self.updater._installation_in_progress = True
        self.updater._installer_version = COMPANION_VERSION
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._save_cache()

        self.updater._load_cache()
        self.assertFalse(self.updater._installation_in_progress)
        self.assertFalse(self.updater._pending_install)
        self.assertEqual(self.updater._installer_state, "Completed")
        self.assertEqual(self.updater._last_install_result, "success")

    def test_recovery_after_abnormal_termination_no_file(self):
        # Cache says installation in progress, file missing, version not updated -> Failed
        self.installer_file_exists = False
        self.updater._installation_in_progress = True
        self.updater._installer_version = "v9.9.9"
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._save_cache()

        self.updater._load_cache()
        self.assertFalse(self.updater._installation_in_progress)
        self.assertFalse(self.updater._pending_install)
        self.assertEqual(self.updater._installer_state, "Failed")

    def test_atomic_cache_replace(self):
        with patch("os.replace") as mock_replace, patch("os.fsync") as mock_fsync, patch("builtins.open", mock_open()):
            self.updater._save_cache()
            mock_replace.assert_called_once()
            tmp_arg = mock_replace.call_args[0][0]
            orig_arg = mock_replace.call_args[0][1]
            self.assertTrue(tmp_arg.endswith(".json.tmp"))
            self.assertEqual(orig_arg, CACHE_FILE)

    def test_corrupted_cache_recovery(self):
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            fh.write("invalid json contents")
        
        with patch("os.rename") as mock_rename:
            self.updater._load_cache()
            mock_rename.assert_called_once()
            self.assertEqual(self.updater._latest_version, "v—")
            self.assertFalse(self.updater._pending_install)

    @patch("os.path.getsize")
    def test_recovery_idempotence(self, mock_getsize):
        self.installer_file_exists = True
        mock_getsize.return_value = 2000
        self.updater._installation_in_progress = True
        self.updater._latest_version = "v1.2.0"
        self.updater._installer_version = "v1.2.0"
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._asset_size = 2000
        self.updater._save_cache()

        self.updater._load_cache()
        self.assertTrue(self.updater._recovery_completed)
        self.assertFalse(self.updater._installation_in_progress)

        self.updater._pending_install = False
        
        self.updater._load_cache()
        self.assertFalse(self.updater._pending_install)

    def test_exit_blocked_during_installation(self):
        import sys
        # Mock psutil module which is missing in unit test runner
        mock_psutil = MagicMock()
        orig_psutil = sys.modules.get('psutil')
        orig_ui = sys.modules.get('ui')
        
        sys.modules['psutil'] = mock_psutil
        
        try:
            from ui import CompanionWindow
            mock_window = MagicMock()
            mock_window.updater = self.updater
            mock_window.logger = self.logger
            mock_window.tray_active = True
            
            mock_window._confirm_install_exit.return_value = False
            
            self.updater._installer_state = "Launching"
            CompanionWindow._on_close_request(mock_window)
            mock_window.exit_completely.assert_not_called()
            mock_window.destroy.assert_not_called()
            
            mock_window._confirm_install_exit.return_value = True
            mock_window.exit_completely.reset_mock()
            mock_window.destroy.reset_mock()
            
            self.updater._installer_state = "Waiting For Exit"
            mock_window._confirm_install_exit.reset_mock()
            mock_window.exit_completely.reset_mock()
            CompanionWindow._on_close_request(mock_window)
            mock_window._confirm_install_exit.assert_not_called()
            mock_window.exit_completely.assert_not_called()
            
            self.updater._installer_state = "Restarting Companion"
            CompanionWindow._on_close_request(mock_window)
            mock_window._confirm_install_exit.assert_not_called()
            mock_window.exit_completely.assert_not_called()
        finally:
            if orig_psutil: sys.modules['psutil'] = orig_psutil
            else: sys.modules.pop('psutil', None)
            if orig_ui: sys.modules['ui'] = orig_ui
            else: sys.modules.pop('ui', None)

    @patch("subprocess.Popen")
    @patch("os.path.getsize")
    @patch("builtins.open")
    @patch("os.remove")
    @patch("os._exit")
    def test_installer_diagnostics_timeline(self, mock_exit, mock_remove, mock_open_file, mock_getsize, mock_popen):
        self.installer_file_exists = True
        mock_getsize.return_value = 2000
        mock_open_file.return_value.__enter__.return_value.read.return_value = b""
        self.updater._pending_install = True
        self.updater._installer_path = FINAL_DOWNLOAD_FILE
        self.updater._installer_version = "v1.2.0"
        self.updater._latest_version = "v1.2.0"
        self.updater._asset_size = 2000
        self.updater._installer_sha256 = ""
        self.updater._restart_after_install = True

        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0
        mock_proc.poll.return_value = None
        mock_popen.side_effect = [mock_proc, mock_proc]

        self.installer._run_install_loop()

        events = self.installer.get_recent_events()
        event_names = [e.split(" - ")[1] for e in events]
        self.assertIn("Validation Started", event_names)
        self.assertIn("Validation Passed", event_names)
        self.assertIn("Launch Started", event_names)
        self.assertIn("Installer Running", event_names)
        self.assertIn("Installer Completed", event_names)
        self.assertIn("Restart Requested", event_names)
        self.assertIn("Restart Successful", event_names)
        
        # Test timeline queue limits (100)
        for i in range(150):
            self.installer._add_event(f"Event {i}")
        self.assertEqual(len(self.installer.get_recent_events()), 100)
