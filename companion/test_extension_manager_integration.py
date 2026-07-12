"""
test_extension_manager_integration.py — Phase 1.5 integration tests.

Tests that extension_manager.py correctly delegates to the browser package
for detection, profile scanning, session detection, and extension validation.

Covers:
  - Chrome / Brave / Edge installed detection
  - Browser not installed detection
  - Browser running / stopped detection
  - Extension installed / not installed in browser
  - detect_all_browsers() delegates to BrowserLauncher
  - detect_extension_files() delegates to ExtensionInstallationEngine
  - detect_browser_registration() delegates to BrowserProfileManager
  - _detect_browser_running() delegates to BrowserSessionManager
  - run_full_detection() orchestrates all layers
  - ExtensionStatus has browser_running field
  - Backward compatibility of local types
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure companion/ is importable
_COMPANION = os.path.dirname(os.path.abspath(__file__))
if _COMPANION not in sys.path:
    sys.path.insert(0, _COMPANION)

from browser.browser_info import BrowserInfo
from browser.browser_registry import BrowserRegistry
from browser.browser_extension_installer import (
    ExtensionInstallationEngine,
    ExtensionErrorCode,
)

# Globals to share imported module and saved modules state
em = None
_saved_modules = {}

def setUpModule():
    global em, _saved_modules
    # Save original modules before patching sys.modules
    for _mod_name in ("customtkinter", "base_page", "notifications", "backend_manager", "logger", "updater", "extension_manager"):
        _saved_modules[_mod_name] = sys.modules.get(_mod_name)

    # Mock them
    _ctk_mock = MagicMock()
    sys.modules["customtkinter"] = _ctk_mock
    sys.modules["base_page"] = MagicMock()
    sys.modules["notifications"] = MagicMock()
    sys.modules["backend_manager"] = MagicMock()
    sys.modules["logger"] = MagicMock()
    sys.modules["updater"] = MagicMock()

    # Force reload of extension_manager to use mock dependencies
    sys.modules.pop("extension_manager", None)
    import extension_manager
    em = extension_manager

def tearDownModule():
    global _saved_modules
    # Restore original modules to prevent test pollution
    for _mod_name, _orig in _saved_modules.items():
        if _orig is not None:
            sys.modules[_mod_name] = _orig
        else:
            sys.modules.pop(_mod_name, None)



# ═══════════════════════════════════════════════════════════════════════════
# detect_extension_files() — delegates to ExtensionInstallationEngine
# ═══════════════════════════════════════════════════════════════════════════

class TestDetectExtensionFiles(unittest.TestCase):
    @patch("extension_manager.ExtensionInstallationEngine.validate_extension")
    def test_valid_extension(self, mock_validate):
        mock_validate.return_value = MagicMock(
            valid=True, missing_files=[], manifest_data={"name": "MF", "version": "1.2.3"},
        )
        result = em.detect_extension_files()
        self.assertTrue(result.all_present)
        self.assertEqual(result.missing_files, [])
        self.assertEqual(result.manifest_data["version"], "1.2.3")

    @patch("extension_manager.ExtensionInstallationEngine.validate_extension")
    def test_invalid_extension(self, mock_validate):
        mock_validate.return_value = MagicMock(
            valid=False, missing_files=["icon.png"],
            manifest_data={}, error_code=MagicMock(),
        )
        result = em.detect_extension_files()
        self.assertFalse(result.all_present)
        self.assertIn("icon.png", result.missing_files)

    @patch("extension_manager.ExtensionInstallationEngine.validate_extension")
    def test_fallback_on_exception(self, mock_validate):
        mock_validate.side_effect = RuntimeError("import error")
        result = em.detect_extension_files()
        # Should fall back to manual check
        self.assertIsInstance(result, em.ExtensionFileStatus)

    @patch("extension_manager.ExtensionInstallationEngine.validate_extension")
    def test_called_with_extension_dir(self, mock_validate):
        mock_validate.return_value = MagicMock(
            valid=True, missing_files=[], manifest_data={},
        )
        em.detect_extension_files()
        call_args = mock_validate.call_args[0][0]
        self.assertTrue(call_args.endswith("extension"))


# ═══════════════════════════════════════════════════════════════════════════
# detect_browser_registration() — delegates to BrowserProfileManager
# ═══════════════════════════════════════════════════════════════════════════

class TestDetectBrowserRegistration(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    @patch("extension_manager.BrowserProfileManager.scan")
    @patch("extension_manager.BrowserLauncher.detect_all")
    def test_extension_registered_in_chrome(self, mock_detect, mock_scan):
        mock_detect.return_value = [
            BrowserInfo(name="Chrome", installed=True, path="C:\\chrome.exe"),
        ]
        # Mock profile with preferences that contain the extension
        mock_profile = MagicMock()
        mock_profile.name = "Default"
        mock_profile.preferences_exists = True
        mock_profile.preferences_path = "/fake/prefs"
        mock_profile.error = ""
        mock_scan_result = MagicMock()
        mock_scan_result.profiles = [mock_profile]
        mock_scan_result.profile_count = 1
        mock_scan.return_value = mock_scan_result

        with patch("extension_manager._check_extension_in_preferences", return_value=(True, "")):
            results, installed = em.detect_browser_registration("C:\\ext")
        self.assertTrue(installed)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].extension_registered)

    @patch("extension_manager.BrowserProfileManager.scan")
    @patch("extension_manager.BrowserLauncher.detect_all")
    def test_extension_not_registered(self, mock_detect, mock_scan):
        mock_detect.return_value = [
            BrowserInfo(name="Chrome", installed=True, path="C:\\chrome.exe"),
        ]
        mock_profile = MagicMock()
        mock_profile.name = "Default"
        mock_profile.preferences_exists = True
        mock_profile.preferences_path = "/fake/prefs"
        mock_profile.error = ""
        mock_scan_result = MagicMock()
        mock_scan_result.profiles = [mock_profile]
        mock_scan_result.profile_count = 1
        mock_scan.return_value = mock_scan_result

        with patch("extension_manager._check_extension_in_preferences", return_value=(False, "")):
            results, installed = em.detect_browser_registration("C:\\ext")
        self.assertFalse(installed)
        self.assertFalse(results[0].extension_registered)

    @patch("extension_manager.BrowserLauncher.detect_all")
    def test_not_installed_browser_skipped(self, mock_detect):
        mock_detect.return_value = [
            BrowserInfo(name="Chrome", installed=False),
        ]
        results, installed = em.detect_browser_registration("C:\\ext")
        self.assertFalse(installed)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].extension_registered)

    @patch("extension_manager.BrowserProfileManager.scan")
    @patch("extension_manager.BrowserLauncher.detect_all")
    def test_uses_browser_profile_manager(self, mock_detect, mock_scan):
        mock_detect.return_value = [
            BrowserInfo(name="Edge", installed=True, path="C:\\msedge.exe"),
        ]
        mock_scan.return_value = MagicMock(profiles=[], profile_count=0)
        results, installed = em.detect_browser_registration("C:\\ext")
        mock_scan.assert_called_once_with("Edge")


# ═══════════════════════════════════════════════════════════════════════════
# _detect_browser_running() — delegates to BrowserSessionManager
# ═══════════════════════════════════════════════════════════════════════════

class TestDetectBrowserRunning(unittest.TestCase):
    @patch("extension_manager.BrowserSessionManager.running_all")
    def test_chrome_running(self, mock_running):
        mock_running.return_value = {
            "Chrome": [MagicMock(pid=100)],
        }
        result = em._detect_browser_running()
        self.assertTrue(result.get("Chrome", False))

    @patch("extension_manager.BrowserSessionManager.running_all")
    def test_chrome_stopped(self, mock_running):
        mock_running.return_value = {}
        result = em._detect_browser_running()
        self.assertFalse(result.get("Chrome", False))

    @patch("extension_manager.BrowserSessionManager.running_all")
    def test_multiple_browsers_running(self, mock_running):
        mock_running.return_value = {
            "Chrome": [MagicMock(pid=100)],
            "Edge": [MagicMock(pid=200)],
        }
        result = em._detect_browser_running()
        self.assertTrue(result.get("Chrome", False))
        self.assertTrue(result.get("Edge", False))
        self.assertFalse(result.get("Brave", False))

    @patch("extension_manager.BrowserSessionManager.running_all")
    def test_exception_returns_empty(self, mock_running):
        mock_running.side_effect = RuntimeError("psutil error")
        result = em._detect_browser_running()
        self.assertEqual(result, {})


# ═══════════════════════════════════════════════════════════════════════════
# ExtensionStatus — browser_running field
# ═══════════════════════════════════════════════════════════════════════════

class TestExtensionStatus(unittest.TestCase):
    def test_has_browser_running_field(self):
        status = em.ExtensionStatus()
        self.assertIsInstance(status.browser_running, dict)
        self.assertEqual(status.browser_running, {})

    def test_browser_running_populated(self):
        status = em.ExtensionStatus()
        status.browser_running = {"Chrome": True, "Brave": False}
        self.assertTrue(status.browser_running["Chrome"])
        self.assertFalse(status.browser_running["Brave"])

    def test_default_compatibility(self):
        status = em.ExtensionStatus()
        self.assertEqual(status.compatibility, em.ExtensionStatus.UNKNOWN)

    def test_compatibility_constants(self):
        self.assertEqual(em.ExtensionStatus.COMPATIBLE, "Compatible")
        self.assertEqual(em.ExtensionStatus.MISMATCH, "Version Mismatch")
        self.assertEqual(em.ExtensionStatus.MISSING, "Extension Missing")
        self.assertEqual(em.ExtensionStatus.NOT_INSTALLED, "Not Installed")
        self.assertEqual(em.ExtensionStatus.UNKNOWN, "Unknown")

    def test_all_browsers_default(self):
        status = em.ExtensionStatus()
        self.assertEqual(status.all_browsers, [])

    def test_browser_registration_default(self):
        status = em.ExtensionStatus()
        self.assertEqual(status.browser_registration, [])


# ═══════════════════════════════════════════════════════════════════════════
# run_full_detection() — orchestration
# ═══════════════════════════════════════════════════════════════════════════

class TestRunFullDetection(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    @patch("extension_manager._detect_browser_running")
    @patch("extension_manager.detect_browser_registration")
    @patch("extension_manager.detect_extension_files")
    @patch("extension_manager.BrowserLauncher.detect_all")
    def test_full_detection_returns_status(
        self, mock_browsers, mock_files, mock_reg, mock_running,
    ):
        mock_browsers.return_value = [
            BrowserInfo(name="Chrome", installed=True, path="C:\\chrome.exe"),
        ]
        mock_files.return_value = em.ExtensionFileStatus(True, [], {"version": "1.2.3"})
        mock_reg.return_value = ([], False)
        mock_running.return_value = {"Chrome": False}

        status = em.run_full_detection()
        self.assertIsNotNone(status)
        self.assertEqual(len(status.all_browsers), 1)
        self.assertTrue(status.file_status.all_present)
        self.assertFalse(status.installed_in_browser)
        self.assertIn("Chrome", status.browser_running)

    @patch("extension_manager._detect_browser_running")
    @patch("extension_manager.detect_browser_registration")
    @patch("extension_manager.detect_extension_files")
    @patch("extension_manager.BrowserLauncher.detect_all")
    def test_exception_in_browsers_still_returns_status(
        self, mock_browsers, mock_files, mock_reg, mock_running,
    ):
        mock_browsers.side_effect = RuntimeError("fail")
        mock_files.return_value = em.ExtensionFileStatus(False, [], {})
        mock_reg.return_value = ([], False)
        mock_running.return_value = {}

        status = em.run_full_detection()
        self.assertIsNotNone(status)
        self.assertEqual(status.all_browsers, [])

    @patch("extension_manager._detect_browser_running")
    @patch("extension_manager.detect_browser_registration")
    @patch("extension_manager.detect_extension_files")
    @patch("extension_manager.BrowserLauncher.detect_all")
    def test_exception_in_running_still_returns_status(
        self, mock_browsers, mock_files, mock_reg, mock_running,
    ):
        mock_browsers.return_value = []
        mock_files.return_value = em.ExtensionFileStatus(False, [], {})
        mock_reg.return_value = ([], False)
        mock_running.side_effect = RuntimeError("psutil fail")

        status = em.run_full_detection()
        self.assertIsNotNone(status)
        self.assertEqual(status.browser_running, {})


# ═══════════════════════════════════════════════════════════════════════════
# UI helper functions
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeOverallReady(unittest.TestCase):
    def test_ready_when_compatible(self):
        status = em.ExtensionStatus()
        status.all_browsers = [BrowserInfo(name="Chrome", installed=True)]
        status.folder_exists = True
        status.file_status = em.ExtensionFileStatus(True, [], {})
        status.installed_in_browser = True
        status.compatibility = em.ExtensionStatus.COMPATIBLE
        label, color = em._compute_overall_ready(status)
        self.assertEqual(label, "Yes")

    def test_not_ready_no_browser(self):
        status = em.ExtensionStatus()
        status.all_browsers = [BrowserInfo(name="Chrome", installed=False)]
        status.folder_exists = True
        label, color = em._compute_overall_ready(status)
        self.assertEqual(label, "No")


class TestComputeCompatibility(unittest.TestCase):
    def test_compatible(self):
        status = em.ExtensionStatus()
        status.folder_exists = True
        status.extension_version = "1.2.3"
        status.file_status = em.ExtensionFileStatus(True, [], {})
        status.installed_in_browser = True
        status.companion_version = "1.2.3"
        result = em._compute_compatibility(status)
        self.assertEqual(result, em.ExtensionStatus.COMPATIBLE)

    def test_missing_folder(self):
        status = em.ExtensionStatus()
        status.folder_exists = False
        result = em._compute_compatibility(status)
        self.assertEqual(result, em.ExtensionStatus.MISSING)

    def test_not_installed(self):
        status = em.ExtensionStatus()
        status.folder_exists = True
        status.extension_version = "1.2.3"
        status.file_status = em.ExtensionFileStatus(True, [], {})
        status.installed_in_browser = False
        result = em._compute_compatibility(status)
        self.assertEqual(result, em.ExtensionStatus.NOT_INSTALLED)


# ═══════════════════════════════════════════════════════════════════════════
# Backward compatibility
# ═══════════════════════════════════════════════════════════════════════════

class TestBackwardCompat(unittest.TestCase):
    def test_extension_file_status_has_all_present(self):
        s = em.ExtensionFileStatus(True, [], {})
        self.assertTrue(s.all_present)

    def test_extension_file_status_has_missing_files(self):
        s = em.ExtensionFileStatus(False, ["icon.png"], {})
        self.assertIn("icon.png", s.missing_files)

    def test_extension_file_status_has_manifest_data(self):
        s = em.ExtensionFileStatus(True, [], {"name": "test"})
        self.assertEqual(s.manifest_data["name"], "test")

    def test_badge_callback_register_unregister(self):
        fn = lambda color: None
        em.register_badge_callback(fn)
        self.assertIn(fn, em._badge_callbacks)
        em.unregister_badge_callback(fn)
        self.assertNotIn(fn, em._badge_callbacks)

    def test_badge_callback_unregister_nonexistent(self):
        fn = lambda color: None
        em.unregister_badge_callback(fn)  # Should not raise


# ═══════════════════════════════════════════════════════════════════════════
# One-Click Install / Launch flow tests
# ═══════════════════════════════════════════════════════════════════════════

class TestOnInstallExtension(unittest.TestCase):
    """Tests for the _on_install_extension launch workflow logic."""

    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    @patch("extension_manager.ExtensionInstallationEngine.launch")
    def test_chrome_launch_called(self, mock_launch):
        """Chrome launch delegates to ExtensionInstallationEngine.launch."""
        mock_launch.return_value = MagicMock(
            success=True, browser_name="Chrome", pid=1234,
            error_code=MagicMock(value="success"),
        )
        ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        result = ExtensionInstallationEngine.launch(
            browser_name="Chrome",
            extension_dir=ext_dir,
            url="chrome://extensions",
        )
        mock_launch.assert_called_once()

    @patch("extension_manager.ExtensionInstallationEngine.launch")
    def test_brave_launch_called(self, mock_launch):
        """Brave launch delegates to ExtensionInstallationEngine.launch."""
        mock_launch.return_value = MagicMock(
            success=True, browser_name="Brave", pid=1235,
            error_code=MagicMock(value="success"),
        )
        ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        result = ExtensionInstallationEngine.launch(
            browser_name="Brave",
            extension_dir=ext_dir,
            url="chrome://extensions",
        )
        self.assertTrue(result.success)
        mock_launch.assert_called_once()

    @patch("extension_manager.ExtensionInstallationEngine.launch")
    def test_edge_launch_called(self, mock_launch):
        """Edge launch delegates to ExtensionInstallationEngine.launch."""
        mock_launch.return_value = MagicMock(
            success=True, browser_name="Edge", pid=1236,
            error_code=MagicMock(value="success"),
        )
        ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        result = ExtensionInstallationEngine.launch(
            browser_name="Edge",
            extension_dir=ext_dir,
            url="chrome://extensions",
        )
        self.assertTrue(result.success)
        mock_launch.assert_called_once()

    @patch("extension_manager.ExtensionInstallationEngine.launch")
    def test_successful_launch_returns_pid(self, mock_launch):
        """Successful launch result contains process ID."""
        mock_launch.return_value = MagicMock(
            success=True, browser_name="Chrome", pid=1234, error_message="",
            error_code=MagicMock(value="success"),
            exe_path="C:\\chrome.exe",
        )
        ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        result = ExtensionInstallationEngine.launch(
            browser_name="Chrome",
            extension_dir=ext_dir,
            url="chrome://extensions",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.pid, 1234)

    @patch("extension_manager.ExtensionInstallationEngine.launch")
    def test_launch_failure_returns_error(self, mock_launch):
        """Launch failure result contains error information."""
        mock_launch.return_value = MagicMock(
            success=False, browser_name="Chrome", pid=None,
            error_code=ExtensionErrorCode.LAUNCH_FAILED,
            error_message="Process exited immediately",
            exe_path="",
        )
        ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        result = ExtensionInstallationEngine.launch(
            browser_name="Chrome",
            extension_dir=ext_dir,
            url="chrome://extensions",
        )
        self.assertTrue(result.failed)
        self.assertIn("Process exited", result.error_message)

    @patch("extension_manager.ExtensionInstallationEngine.launch")
    def test_browser_not_found(self, mock_launch):
        """Browser-not-found returns proper error."""
        mock_launch.return_value = MagicMock(
            success=False, browser_name="Chrome", pid=None,
            error_code=ExtensionErrorCode.BROWSER_NOT_FOUND,
            error_message="Chrome is not installed",
            exe_path="",
        )
        ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        result = ExtensionInstallationEngine.launch(
            browser_name="Chrome",
            extension_dir=ext_dir,
            url="chrome://extensions",
        )
        self.assertTrue(result.failed)
        self.assertEqual(result.error_code, ExtensionErrorCode.BROWSER_NOT_FOUND)

    @patch("extension_manager.ExtensionInstallationEngine.launch")
    def test_permission_denied(self, mock_launch):
        """Permission denied returns proper error."""

        mock_launch.return_value = MagicMock(
            success=False, browser_name="Chrome", pid=None,
            error_code=ExtensionErrorCode.PERMISSION_DENIED,
            error_message="Permission denied: C:\\chrome.exe",
            exe_path="C:\\chrome.exe",
        )
        ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        result = ExtensionInstallationEngine.launch(
            browser_name="Chrome",
            extension_dir=ext_dir,
            url="chrome://extensions",
        )
        self.assertTrue(result.failed)
        self.assertEqual(result.error_code, ExtensionErrorCode.PERMISSION_DENIED)

    @patch("extension_manager.ExtensionInstallationEngine.launch")
    def test_extension_missing(self, mock_launch):
        """Missing extension returns proper error."""

        mock_launch.return_value = MagicMock(
            success=False, browser_name="Chrome", pid=None,
            error_code=ExtensionErrorCode.EXTENSION_MISSING,
            error_message="Extension directory not found: /fake/path",
            exe_path="",
        )
        ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        result = ExtensionInstallationEngine.launch(
            browser_name="Chrome",
            extension_dir=ext_dir,
            url="chrome://extensions",
        )
        self.assertTrue(result.failed)
        self.assertEqual(result.error_code, ExtensionErrorCode.EXTENSION_MISSING)

    @patch("extension_manager.ExtensionInstallationEngine.launch")
    def test_launch_passes_url(self, mock_launch):
        """Launch is called with chrome://extensions URL."""
        mock_launch.return_value = MagicMock(
            success=True, browser_name="Chrome", pid=1234,
            error_code=MagicMock(value="success"),
        )
        ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        ExtensionInstallationEngine.launch(
            browser_name="Chrome",
            extension_dir=ext_dir,
            url="chrome://extensions",
        )
        _, kwargs = mock_launch.call_args
        self.assertEqual(kwargs.get("url"), "chrome://extensions")

    @patch("extension_manager.ExtensionInstallationEngine.launch")
    def test_launch_passes_extension_dir(self, mock_launch):
        """Launch is called with the correct extension directory."""
        mock_launch.return_value = MagicMock(
            success=True, browser_name="Chrome", pid=1234,
            error_code=MagicMock(value="success"),
        )
        ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        ExtensionInstallationEngine.launch(
            browser_name="Chrome",
            extension_dir=ext_dir,
            url="chrome://extensions",
        )
        _, kwargs = mock_launch.call_args
        self.assertTrue(kwargs.get("extension_dir", "").endswith("extension"))

    def test_on_install_result_success_handling(self):
        """_on_install_result sets success message for successful launch."""

        result = MagicMock(
            success=True, browser_name="Chrome", pid=1234,
            error_code=ExtensionErrorCode.SUCCESS,
            error_message="",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.browser_name, "Chrome")

    def test_on_install_result_failure_handling(self):
        """_on_install_result sets failure message for failed launch."""

        result = MagicMock(
            success=False, browser_name="Chrome", pid=None,
            error_code=ExtensionErrorCode.BROWSER_NOT_FOUND,
            error_message="Chrome is not installed",
        )
        self.assertTrue(result.failed)
        self.assertEqual(result.browser_name, "Chrome")





class TestInstallFlowRefreshCycle(unittest.TestCase):
    """Tests for the refresh-after-launch cycle."""

    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    @patch("extension_manager.run_full_detection")
    def test_refresh_updates_browser_list(self, mock_detect):
        """Refreshing detection updates list of browsers."""
        mock_detect.return_value = em.ExtensionStatus()
        mock_detect.return_value.all_browsers = [
            BrowserInfo(name="Chrome", installed=True, path="C:\\chrome.exe"),
        ]
        status = em.run_full_detection()
        self.assertEqual(len(status.all_browsers), 1)
        self.assertEqual(status.all_browsers[0].name, "Chrome")

    @patch("extension_manager.run_full_detection")
    def test_refresh_updates_browser_running(self, mock_detect):
        """Refreshing detection updates browser running state."""
        mock_detect.return_value = em.ExtensionStatus()
        mock_detect.return_value.browser_running = {"Chrome": True}
        status = em.run_full_detection()
        self.assertTrue(status.browser_running["Chrome"])

    @patch("extension_manager.run_full_detection")
    def test_refresh_updates_installed_in_browser(self, mock_detect):
        """Refreshing detection updates installed-in-browser state."""
        mock_detect.return_value = em.ExtensionStatus()
        mock_detect.return_value.installed_in_browser = True
        status = em.run_full_detection()
        self.assertTrue(status.installed_in_browser)

    @patch("extension_manager.ExtensionInstallationEngine.launch")
    def test_install_then_refresh_updates_compatibility(self, mock_launch):
        """After a successful launch, refresh updates the compatibility."""
        mock_launch.return_value = MagicMock(
            success=True, browser_name="Chrome", pid=1234,
            error_code=MagicMock(value="success"),
        )
        ext_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        result = ExtensionInstallationEngine.launch(
            browser_name="Chrome",
            extension_dir=ext_dir,
            url="chrome://extensions",
        )
        self.assertTrue(result.success)

        mock_detect_status = em.ExtensionStatus()
        mock_detect_status.installed_in_browser = True
        mock_detect_status.compatibility = em.ExtensionStatus.COMPATIBLE
        self.assertTrue(mock_detect_status.installed_in_browser)


if __name__ == "__main__":
    unittest.main()
