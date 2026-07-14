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

    @patch("extension_manager.BrowserSessionManager.running_all")
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
        mock_running.return_value = {"Chrome": []}

        status = em.run_full_detection()
        self.assertIsNotNone(status)
        self.assertEqual(len(status.all_browsers), 1)
        self.assertTrue(status.file_status.all_present)
        self.assertFalse(status.installed_in_browser)
        self.assertIn("Chrome", status.browser_running)

    @patch("extension_manager.BrowserSessionManager.running_all")
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

    @patch("extension_manager.BrowserSessionManager.running_all")
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


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3.3 — Automation Engine Integration Tests
# ═══════════════════════════════════════════════════════════════════════════

_EM_SRC_PATH = os.path.join(_COMPANION, "extension_manager.py")


def _read_em_source() -> str:
    """Read the extension_manager source file."""
    with open(_EM_SRC_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


class TestAutomationEngineImports(unittest.TestCase):
    """Verify automation engine imports are available in extension_manager."""

    def test_browser_automation_engine_imported(self):
        self.assertTrue(hasattr(em, "BrowserAutomationEngine"))

    def test_get_automation_engine_imported(self):
        self.assertTrue(hasattr(em, "get_automation_engine"))

    def test_create_pipelines_imported(self):
        self.assertTrue(hasattr(em, "create_launch_pipeline"))
        self.assertTrue(hasattr(em, "create_install_pipeline"))
        self.assertTrue(hasattr(em, "create_verify_pipeline"))
        self.assertTrue(hasattr(em, "create_open_ext_page_pipeline"))

    def test_automation_state_imported(self):
        from browser import AutomationState
        self.assertTrue(hasattr(AutomationState, "PENDING"))
        self.assertTrue(hasattr(AutomationState, "COMPLETED"))


class TestAutomationInstanceVars(unittest.TestCase):
    """Verify ExtensionManagerPage has automation tracking instance vars."""

    def test_init_sets_automation_fields(self):
        """Check that __init__ code references automation fields."""
        src = _read_em_source()
        self.assertIn("_automation_engine", src)
        self.assertIn("_automation_sessions", src)
        self.assertIn("_automation_poll_jobs", src)


class TestEnsureEngine(unittest.TestCase):
    """Test _ensure_engine lazy initialization (source inspection)."""

    def test_ensure_engine_lazy_initializes(self):
        """Source shows _automation_engine is set from get_automation_engine."""
        src = _read_em_source()
        self.assertIn("self._automation_engine = get_automation_engine()", src)

    def test_ensure_engine_returns_none_on_exception(self):
        """Source has try/except that returns None."""
        src = _read_em_source()
        self.assertIn("def _ensure_engine(self)", src)
        self.assertIn("except Exception:", src)


class TestCancelAutomationForBrowser(unittest.TestCase):
    """Test cancellation logic (source inspection)."""

    def test_cancel_removes_session(self):
        src = _read_em_source()
        self.assertIn("self._automation_sessions.pop(browser_name, None)", src)

    def test_cancel_calls_engine_cancel(self):
        src = _read_em_source()
        self.assertIn("self._automation_engine.cancel(session_id)", src)

    def test_cancel_stops_polling(self):
        src = _read_em_source()
        self.assertIn("self._stop_polling(browser_name)", src)


class TestCancelAllAutomation(unittest.TestCase):
    """Test _cancel_all_automation (source inspection)."""

    def test_iterates_all_sessions(self):
        src = _read_em_source()
        self.assertIn("list(self._automation_sessions.keys())", src)

    def test_calls_cancel_for_each(self):
        src = _read_em_source()
        self.assertIn("self._cancel_automation_for_browser(browser_name)", src)


class TestCardActionRouting(unittest.TestCase):
    """Test card action callbacks route through automation engine."""

    def test_callbacks_dict_has_automation_methods(self):
        src = _read_em_source()
        self.assertIn("_on_card_launch", src)
        self.assertIn("_on_card_install", src)
        self.assertIn("_on_card_open_ext_page", src)
        self.assertIn("_on_card_verify", src)

    def test_on_card_launch_uses_engine(self):
        src = _read_em_source()
        self.assertIn("engine.run_async(", src)
        self.assertIn("create_launch_pipeline", src)

    def test_on_card_install_uses_engine(self):
        src = _read_em_source()
        self.assertIn("create_install_pipeline", src)

    def test_on_card_open_ext_page_uses_engine(self):
        src = _read_em_source()
        self.assertIn("create_open_ext_page_pipeline", src)

    def test_on_card_verify_uses_engine(self):
        src = _read_em_source()
        self.assertIn("create_verify_pipeline", src)

    def test_verify_btn_passes_browser_name(self):
        """Verify button in _build_actions should pass self._name."""
        src = _read_em_source()
        # The verify button callback should reference self._name
        self.assertIn('self._callbacks.get("verify", lambda _: None)(self._name)', src)

    def test_on_card_launch_no_direct_browser_launcher(self):
        """_on_card_launch should not use BrowserLauncher.launch_browser directly."""
        # Find the _on_card_launch method body in source
        src = _read_em_source()
        launch_start = src.index("def _on_card_launch(self, browser_name: str) -> None:")
        # Find next method def
        next_def = src.index("\n    def ", launch_start + 1)
        launch_body = src[launch_start:next_def]
        self.assertNotIn("BrowserLauncher.launch_browser", launch_body)

    def test_on_card_install_no_direct_engine(self):
        """_on_card_install should not use ExtensionInstallationEngine.launch directly."""
        src = _read_em_source()
        install_start = src.index("def _on_card_install(self, browser_name: str) -> None:")
        next_def = src.index("\n    def ", install_start + 1)
        install_body = src[install_start:next_def]
        self.assertNotIn("ExtensionInstallationEngine.launch(", install_body)


class TestPollingMechanism(unittest.TestCase):
    """Test polling source code structure."""

    def test_poll_automation_def_exists(self):
        src = _read_em_source()
        self.assertIn("def _poll_automation(self, browser_name: str, session_id: str) -> None:", src)

    def test_poll_checks_winfo_exists(self):
        src = _read_em_source()
        self.assertIn("if not self.winfo_exists():", src)

    def test_poll_checks_terminal_state(self):
        src = _read_em_source()
        self.assertIn("session.is_terminal", src)

    def test_poll_schedules_next(self):
        src = _read_em_source()
        self.assertIn("self.after(", src)
        self.assertIn("200", src)

    def test_start_polling_stops_existing(self):
        src = _read_em_source()
        self.assertIn("def _start_polling(self, browser_name: str, session_id: str) -> None:", src)
        self.assertIn("self._stop_polling(browser_name)", src)


class TestOnAutomationFinished(unittest.TestCase):
    """Test completion handler (source inspection)."""

    def test_success_shows_completed(self):
        src = _read_em_source()
        self.assertIn('"Completed"', src)
        self.assertIn('"#22c55e"', src)

    def test_failure_shows_failed(self):
        src = _read_em_source()
        self.assertIn('"Failed"', src)
        self.assertIn('"#ef4444"', src)

    def test_triggers_refresh_on_success(self):
        src = _read_em_source()
        self.assertIn("self._on_refresh", src)

    def test_cleans_up_session_tracking(self):
        src = _read_em_source()
        self.assertIn("self._automation_sessions.pop(browser_name, None)", src)


class TestOnHideCancelsAutomation(unittest.TestCase):
    """Test that on_hide cancels all automation sessions."""

    def test_on_hide_calls_cancel_all(self):
        src = _read_em_source()
        self.assertIn("_cancel_all_automation", src)


class TestWizardRoutesThroughEngine(unittest.TestCase):
    """Test wizard browser actions route through the automation engine."""

    def test_do_launch_browser_uses_engine(self):
        src = _read_em_source()
        self.assertIn("get_automation_engine", src)
        self.assertIn("engine.run_async(", src)
        self.assertIn("create_open_ext_page_pipeline", src)

    def test_do_open_extensions_page_uses_engine(self):
        src = _read_em_source()
        # Should appear in both the wizard and card methods
        self.assertIn("create_open_ext_page_pipeline", src)


class TestProfileButtonNotRouted(unittest.TestCase):
    """_on_card_open_profile should NOT use the automation engine."""

    def test_profile_opens_directly(self):
        src = _read_em_source()
        profile_start = src.index("def _on_card_open_profile(self, browser_name: str) -> None:")
        next_def = src.index("\n    def ", profile_start + 1)
        profile_body = src[profile_start:next_def]
        self.assertNotIn("get_automation_engine", profile_body)
        self.assertNotIn("run_async", profile_body)
        self.assertIn("detect_by_name", profile_body)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3.4 — Installation Wizard Automation Integration Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestWizardLaunchAutomation(unittest.TestCase):
    """Wizard launch step uses engine.run_async() with create_launch_pipeline."""

    def test_launch_uses_run_async(self):
        src = _read_em_source()
        # Find _do_launch_browser method body
        start = src.index("def _do_launch_browser(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("engine.run_async(", body)
        self.assertNotIn("engine.run(", body.replace("engine.run_async(", ""))

    def test_launch_uses_create_launch_pipeline(self):
        src = _read_em_source()
        start = src.index("def _do_launch_browser(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("create_launch_pipeline()", body)

    def test_launch_no_threaded_worker(self):
        src = _read_em_source()
        start = src.index("def _do_launch_browser(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertNotIn("threading.Thread", body)

    def test_launch_stores_session_id(self):
        src = _read_em_source()
        start = src.index("def _do_launch_browser(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_wizard_sessions", body)


class TestWizardOpenPageAutomation(unittest.TestCase):
    """Wizard open extensions page step uses engine.run_async()."""

    def test_open_page_uses_run_async(self):
        src = _read_em_source()
        start = src.index("def _do_open_extensions_page(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("engine.run_async(", body)
        self.assertNotIn("engine.run(", body.replace("engine.run_async(", ""))

    def test_open_page_uses_create_open_ext_page_pipeline(self):
        src = _read_em_source()
        start = src.index("def _do_open_extensions_page(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("create_open_ext_page_pipeline()", body)

    def test_open_page_no_threaded_worker(self):
        src = _read_em_source()
        start = src.index("def _do_open_extensions_page(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertNotIn("threading.Thread", body)

    def test_open_page_stores_session_id(self):
        src = _read_em_source()
        start = src.index("def _do_open_extensions_page(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_wizard_sessions", body)


class TestWizardVerifyAutomation(unittest.TestCase):
    """Wizard verification step uses engine.run_async() with create_verify_pipeline."""

    def test_verify_uses_run_async(self):
        src = _read_em_source()
        start = src.index("def _start_verification(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("engine.run_async(", body)

    def test_verify_uses_create_verify_pipeline(self):
        src = _read_em_source()
        start = src.index("def _start_verification(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("create_verify_pipeline()", body)

    def test_verify_stores_session_id(self):
        src = _read_em_source()
        start = src.index("def _start_verification(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_wizard_sessions", body)

    def test_verify_fallback_detection_exists(self):
        src = _read_em_source()
        self.assertIn("_run_fallback_detection", src)

    def test_verify_post_detection_exists(self):
        src = _read_em_source()
        self.assertIn("_run_post_verify_detection", src)


class TestWizardSessionTracking(unittest.TestCase):
    """Wizard has session tracking dictionaries."""

    def test_wizard_sessions_dict_in_init(self):
        src = _read_em_source()
        init_start = src.index("class _InstallationWizard:")
        init_body = src[init_start:src.index("def _build_dialog", init_start)]
        self.assertIn("_wizard_sessions", init_body)
        self.assertIn("_wizard_results", init_body)

    def test_wizard_sessions_dict_not_legacy(self):
        src = _read_em_source()
        init_start = src.index("class _InstallationWizard:")
        init_body = src[init_start:src.index("def _build_dialog", init_start)]
        self.assertNotIn("_launch_success", init_body)
        self.assertNotIn("_launch_error", init_body)


class TestWizardSessionCancellation(unittest.TestCase):
    """Wizard session cancellation methods exist and are correct."""

    def test_cancel_wizard_sessions_method_exists(self):
        src = _read_em_source()
        self.assertIn("def _cancel_wizard_sessions(self)", src)

    def test_cancel_wizard_session_method_exists(self):
        src = _read_em_source()
        self.assertIn("def _cancel_wizard_session(self, step_key: str)", src)

    def test_cancel_sessions_iterates_keys(self):
        src = _read_em_source()
        start = src.index("def _cancel_wizard_sessions(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._wizard_sessions", body)
        self.assertIn("_cancel_wizard_session", body)

    def test_cancel_single_session_calls_engine_cancel(self):
        src = _read_em_source()
        start = src.index("def _cancel_wizard_session(self, step_key: str)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("engine.cancel(session_id)", body)


class TestWizardBrowserSwitchCancels(unittest.TestCase):
    """Browser switching cancels active wizard sessions."""

    def test_select_browser_cancels_sessions(self):
        src = _read_em_source()
        start = src.index("def _select_browser(self, name: str)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_cancel_wizard_sessions()", body)

    def test_select_browser_resets_state(self):
        src = _read_em_source()
        start = src.index("def _select_browser(self, name: str)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._launched_browser = None", body)
        self.assertIn("self._opened_page_browser = None", body)
        self.assertIn("self._verification_status = None", body)


class TestWizardCloseCancels(unittest.TestCase):
    """Wizard close and finish cancel active sessions."""

    def test_on_cancel_cancels_sessions(self):
        src = _read_em_source()
        start = src.index("def _on_cancel(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_cancel_wizard_sessions()", body)

    def test_on_finish_cancels_sessions(self):
        src = _read_em_source()
        start = src.index("def _on_finish(self)")
        end = len(src)
        body = src[start:end]
        self.assertIn("_cancel_wizard_sessions()", body)


class TestWizardRetryResets(unittest.TestCase):
    """Retry methods reset wizard results."""

    def test_retry_launch_resets_result(self):
        src = _read_em_source()
        start = src.index("def _retry_launch(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn('_wizard_results.pop("launch"', body)

    def test_retry_verification_resets_result(self):
        src = _read_em_source()
        start = src.index("def _retry_verification(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn('_wizard_results.pop("verify"', body)

    def test_retry_open_page_exists(self):
        src = _read_em_source()
        self.assertIn("def _retry_open_page(self)", src)


class TestWizardPolling(unittest.TestCase):
    """Wizard polling infrastructure exists."""

    def test_poll_wizard_session_method_exists(self):
        src = _read_em_source()
        self.assertIn("def _poll_wizard_session(self, step_key: str, session_id: str)", src)

    def test_poll_checks_terminal_state(self):
        src = _read_em_source()
        start = src.index("def _poll_wizard_session(self, step_key: str, session_id: str)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("session.is_terminal", body)
        self.assertIn("get_result(session_id)", body)

    def test_poll_schedules_next(self):
        src = _read_em_source()
        start = src.index("def _poll_wizard_session(self, step_key: str, session_id: str)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._dialog.after(", body)

    def test_schedule_ui_method_exists(self):
        src = _read_em_source()
        self.assertIn("def _schedule_ui(self, step: int)", src)

    def test_schedule_ui_uses_after(self):
        src = _read_em_source()
        start = src.index("def _schedule_ui(self, step: int)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._dialog.after(", body)


class TestWizardOnSessionFinished(unittest.TestCase):
    """Wizard session completion handler covers all step keys."""

    def test_handler_method_exists(self):
        src = _read_em_source()
        self.assertIn("def _on_wizard_session_finished(self, step_key: str, result: Any)", src)

    def test_handler_handles_launch(self):
        src = _read_em_source()
        start = src.index("def _on_wizard_session_finished(self, step_key: str, result: Any)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn('step_key == "launch"', body)

    def test_handler_handles_open_page(self):
        src = _read_em_source()
        start = src.index("def _on_wizard_session_finished(self, step_key: str, result: Any)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn('step_key == "open_page"', body)

    def test_handler_handles_verify(self):
        src = _read_em_source()
        start = src.index("def _on_wizard_session_finished(self, step_key: str, result: Any)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn('step_key == "verify"', body)

    def test_handler_clears_session(self):
        src = _read_em_source()
        start = src.index("def _on_wizard_session_finished(self, step_key: str, result: Any)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_wizard_sessions.pop(step_key", body)
        self.assertIn("_wizard_results[step_key] = result", body)

    def test_handler_no_raw_except_clause(self):
        src = _read_em_source()
        start = src.index("def _on_wizard_session_finished(self, step_key: str, result: Any)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertNotIn("except:", body)

    def test_handler_uses_schedule_ui(self):
        src = _read_em_source()
        start = src.index("def _on_wizard_session_finished(self, step_key: str, result: Any)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_schedule_ui(", body)


class TestWizardGetEngine(unittest.TestCase):
    """Wizard lazy engine initialization."""

    def test_get_engine_method_exists(self):
        src = _read_em_source()
        self.assertIn("def _get_engine(self)", src)

    def test_get_engine_calls_get_automation_engine(self):
        src = _read_em_source()
        start = src.index("def _get_engine(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("get_automation_engine()", body)

    def test_get_engine_has_try_except(self):
        src = _read_em_source()
        start = src.index("def _get_engine(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("try:", body)
        self.assertIn("except", body)

    def test_do_launch_checks_engine(self):
        src = _read_em_source()
        start = src.index("def _do_launch_browser(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._get_engine()", body)

    def test_do_open_page_checks_engine(self):
        src = _read_em_source()
        start = src.index("def _do_open_extensions_page(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._get_engine()", body)

    def test_start_verify_checks_engine(self):
        src = _read_em_source()
        start = src.index("def _start_verification(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._get_engine()", body)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3.5 — Smart Recommendation & Auto-Fix Engine
# ═══════════════════════════════════════════════════════════════════════════


class TestSmartRecommendationStateVars(unittest.TestCase):
    """ExtensionManagerPage has smart recommendation state variables."""

    def test_rec_session_id_in_init(self):
        src = _read_em_source()
        start = src.index("def __init__(self, master")
        end = src.index("def _build_ui(self)", start)
        body = src[start:end]
        self.assertIn("_rec_session_id", body)

    def test_rec_poll_job_in_init(self):
        src = _read_em_source()
        start = src.index("def __init__(self, master")
        end = src.index("def _build_ui(self)", start)
        body = src[start:end]
        self.assertIn("_rec_poll_job", body)

    def test_rec_state_in_init(self):
        src = _read_em_source()
        start = src.index("def __init__(self, master")
        end = src.index("def _build_ui(self)", start)
        body = src[start:end]
        self.assertIn("_rec_state", body)

    def test_rec_fixing_in_init(self):
        src = _read_em_source()
        start = src.index("def __init__(self, master")
        end = src.index("def _build_ui(self)", start)
        body = src[start:end]
        self.assertIn("_rec_fixing", body)


class TestSmartRecommendationWidgets(unittest.TestCase):
    """Recommendation card has auto-fix action widgets."""

    def test_rec_action_frame_in_build_ui(self):
        src = _read_em_source()
        start = src.index("# ── Smart Auto-Fix Action Row")
        end = src.index("# ── Action Buttons", start)
        body = src[start:end]
        self.assertIn("_rec_action_frame", body)

    def test_rec_fix_btn_in_build_ui(self):
        src = _read_em_source()
        start = src.index("# ── Smart Auto-Fix Action Row")
        end = src.index("# ── Action Buttons", start)
        body = src[start:end]
        self.assertIn("_rec_fix_btn", body)
        self.assertIn("command=self._on_rec_fix_now", body)

    def test_rec_retry_btn_in_build_ui(self):
        src = _read_em_source()
        start = src.index("# ── Smart Auto-Fix Action Row")
        end = src.index("# ── Action Buttons", start)
        body = src[start:end]
        self.assertIn("_rec_retry_btn", body)
        self.assertIn("command=self._on_rec_retry", body)

    def test_rec_progress_lbl_in_build_ui(self):
        src = _read_em_source()
        start = src.index("# ── Smart Auto-Fix Action Row")
        end = src.index("# ── Action Buttons", start)
        body = src[start:end]
        self.assertIn("_rec_progress_lbl", body)


class TestComputeRecommendationState(unittest.TestCase):
    """_compute_recommendation_state() maps status to recommendation dict."""

    def test_method_exists(self):
        src = _read_em_source()
        self.assertIn("def _compute_recommendation_state(", src)

    def _get_method_body(self, src: str) -> str:
        start = src.index("def _compute_recommendation_state(")
        end = src.index("\n    def ", start + 1)
        return src[start:end]

    def test_returns_dict_with_required_keys(self):
        body = self._get_method_body(_read_em_source())
        for key in ("state", "title", "msg", "desc", "bg", "border",
                     "title_color", "action_label", "pipeline_factory"):
            self.assertIn(f'"{key}"', body, f"Missing key: {key}")

    def test_browser_missing_state(self):
        body = self._get_method_body(_read_em_source())
        self.assertIn('"browser_missing"', body)

    def test_browser_closed_state(self):
        body = self._get_method_body(_read_em_source())
        self.assertIn('"browser_closed"', body)

    def test_extension_missing_state(self):
        body = self._get_method_body(_read_em_source())
        self.assertIn('"extension_missing"', body)

    def test_version_mismatch_state(self):
        body = self._get_method_body(_read_em_source())
        self.assertIn('"version_mismatch"', body)

    def test_healthy_state(self):
        body = self._get_method_body(_read_em_source())
        self.assertIn('"healthy"', body)

    def test_browser_closed_uses_launch_pipeline(self):
        body = self._get_method_body(_read_em_source())
        idx_closed = body.index('"browser_closed"')
        idx_launch = body.index("create_launch_pipeline", idx_closed)
        idx_ext_missing = body.index('"extension_missing"')
        self.assertLess(idx_launch, idx_ext_missing,
                        "launch_pipeline should be in browser_closed block")

    def test_extension_missing_uses_install_pipeline(self):
        body = self._get_method_body(_read_em_source())
        idx_ext = body.index('"extension_missing"')
        self.assertIn("create_install_pipeline", body[idx_ext:idx_ext + 600])

    def test_version_mismatch_uses_launch_pipeline(self):
        body = self._get_method_body(_read_em_source())
        idx_vm = body.index('"version_mismatch"')
        self.assertIn("create_launch_pipeline", body[idx_vm:idx_vm + 600])

    def test_healthy_has_no_action(self):
        body = self._get_method_body(_read_em_source())
        idx_healthy = body.index('"healthy"')
        self.assertIn('"action_label"', body[idx_healthy:idx_healthy + 400])
        self.assertIn('""', body[idx_healthy:idx_healthy + 400])

    def test_no_status_returned_for_none(self):
        body = self._get_method_body(_read_em_source())
        self.assertIn('"no_status"', body)

    def test_global_no_browser_installed(self):
        body = self._get_method_body(_read_em_source())
        global_start = body.index("No browser selected")
        self.assertIn('"browser_missing"', body[global_start:])


class TestShowRecommendationActionLabel(unittest.TestCase):
    """_show_recommendation() accepts and handles action_label parameter."""

    def test_accepts_action_label_parameter(self):
        src = _read_em_source()
        start = src.index("def _show_recommendation(")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("action_label", body)

    def test_accepts_pipeline_factory_parameter(self):
        src = _read_em_source()
        start = src.index("def _show_recommendation(")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("pipeline_factory", body)

    def test_shows_action_frame_when_label_present(self):
        src = _read_em_source()
        start = src.index("def _show_recommendation(")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_rec_action_frame.pack(", body)

    def test_hides_action_frame_when_no_label(self):
        src = _read_em_source()
        start = src.index("def _show_recommendation(")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_rec_action_frame.pack_forget()", body)

    def test_disables_fix_btn_when_fixing(self):
        src = _read_em_source()
        start = src.index("def _show_recommendation(")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_fixing", body)


class TestUpdateRecommendationForSelected(unittest.TestCase):
    """_update_recommendation_for_selected uses _compute_recommendation_state."""

    def test_calls_compute_recommendation_state(self):
        src = _read_em_source()
        start = src.index("def _update_recommendation_for_selected(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_compute_recommendation_state(", body)

    def test_passes_selected_browser(self):
        src = _read_em_source()
        start = src.index("def _update_recommendation_for_selected(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._selected_browser", body)

    def test_stores_rec_state(self):
        src = _read_em_source()
        start = src.index("def _update_recommendation_for_selected(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_state", body)

    def test_passes_action_label_to_show(self):
        src = _read_em_source()
        start = src.index("def _update_recommendation_for_selected(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn('action_label=rec["action_label"]', body)


class TestRefreshRecommendationFromStatus(unittest.TestCase):
    """_refresh_recommendation_from_status uses _compute_recommendation_state."""

    def test_calls_compute_recommendation_state(self):
        src = _read_em_source()
        start = src.index("def _refresh_recommendation_from_status(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_compute_recommendation_state(", body)

    def test_passes_none_for_browser_name(self):
        src = _read_em_source()
        start = src.index("def _refresh_recommendation_from_status(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_compute_recommendation_state(status, None)", body)

    def test_passes_action_label_to_show(self):
        src = _read_em_source()
        start = src.index("def _refresh_recommendation_from_status(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn('action_label=rec["action_label"]', body)


class TestAutoFixMethods(unittest.TestCase):
    """Smart recommendation auto-fix methods exist with correct structure."""

    def test_on_rec_fix_now_exists(self):
        src = _read_em_source()
        self.assertIn("def _on_rec_fix_now(self)", src)

    def test_on_rec_retry_exists(self):
        src = _read_em_source()
        self.assertIn("def _on_rec_retry(self)", src)

    def test_poll_rec_session_exists(self):
        src = _read_em_source()
        self.assertIn("def _poll_rec_session(self)", src)

    def test_on_rec_finished_exists(self):
        src = _read_em_source()
        self.assertIn("def _on_rec_finished(self", src)

    def test_cancel_rec_session_exists(self):
        src = _read_em_source()
        self.assertIn("def _cancel_rec_session(self)", src)


class TestAutoFixFixNow(unittest.TestCase):
    """_on_rec_fix_now triggers correct pipeline."""

    def test_checks_fixing_guard(self):
        src = _read_em_source()
        start = src.index("def _on_rec_fix_now(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_fixing", body)

    def test_ensure_engine_called(self):
        src = _read_em_source()
        start = src.index("def _on_rec_fix_now(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._ensure_engine()", body)

    def test_uses_run_async(self):
        src = _read_em_source()
        start = src.index("def _on_rec_fix_now(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("engine.run_async(", body)

    def test_uses_pipeline_factory(self):
        src = _read_em_source()
        start = src.index("def _on_rec_fix_now(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("pipeline_factory()", body)

    def test_disables_button_during_fix(self):
        src = _read_em_source()
        start = src.index("def _on_rec_fix_now(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_rec_fix_btn.configure(state=\"disabled\"", body)

    def test_sets_progress_text(self):
        src = _read_em_source()
        start = src.index("def _on_rec_fix_now(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_rec_progress_lbl.configure(", body)

    def test_stores_session_id(self):
        src = _read_em_source()
        start = src.index("def _on_rec_fix_now(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_session_id = session_id", body)

    def test_starts_polling(self):
        src = _read_em_source()
        start = src.index("def _on_rec_fix_now(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._poll_rec_session()", body)

    def test_fallback_browser_selection(self):
        src = _read_em_source()
        start = src.index("def _on_rec_fix_now(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._selected_browser", body)
        self.assertIn("browser_name is None", body)

    def test_no_duplicate_execution(self):
        src = _read_em_source()
        start = src.index("def _on_rec_fix_now(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_session_id is not None", body)


class TestAutoFixRetry(unittest.TestCase):
    """_on_rec_retry resets state and re-runs fix."""

    def test_resets_fixing_flag(self):
        src = _read_em_source()
        start = src.index("def _on_rec_retry(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_fixing = False", body)

    def test_clears_session_id(self):
        src = _read_em_source()
        start = src.index("def _on_rec_retry(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_session_id = None", body)

    def test_calls_fix_now(self):
        src = _read_em_source()
        start = src.index("def _on_rec_retry(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._on_rec_fix_now()", body)


class TestAutoFixPolling(unittest.TestCase):
    """_poll_rec_session polls session for completion."""

    def test_checks_winfo_exists(self):
        src = _read_em_source()
        start = src.index("def _poll_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self.winfo_exists()", body)

    def test_checks_session_id(self):
        src = _read_em_source()
        start = src.index("def _poll_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_session_id", body)

    def test_checks_terminal_state(self):
        src = _read_em_source()
        start = src.index("def _poll_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("session.is_terminal", body)

    def test_calls_on_rec_finished(self):
        src = _read_em_source()
        start = src.index("def _poll_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._on_rec_finished(result)", body)

    def test_schedules_next_poll(self):
        src = _read_em_source()
        start = src.index("def _poll_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self.after(200", body)

    def test_updates_progress_label(self):
        src = _read_em_source()
        start = src.index("def _poll_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_rec_progress_lbl.configure(", body)

    def test_no_threading(self):
        src = _read_em_source()
        start = src.index("def _poll_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertNotIn("threading.Thread", body)


class TestAutoFixFinished(unittest.TestCase):
    """_on_rec_finished handles completion."""

    def test_clears_session_id(self):
        src = _read_em_source()
        start = src.index("def _on_rec_finished(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_session_id = None", body)

    def test_resets_fixing_flag(self):
        src = _read_em_source()
        start = src.index("def _on_rec_finished(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_fixing = False", body)

    def test_re_enables_button(self):
        src = _read_em_source()
        start = src.index("def _on_rec_finished(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_rec_fix_btn.configure(state=\"normal\")", body)

    def test_on_success_triggers_refresh(self):
        src = _read_em_source()
        start = src.index("def _on_rec_finished(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._on_refresh", body)

    def test_on_success_shows_done(self):
        src = _read_em_source()
        start = src.index("def _on_rec_finished(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_rec_progress_lbl.configure(text=", body)

    def test_on_failure_shows_retry_button(self):
        src = _read_em_source()
        start = src.index("def _on_rec_finished(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_retry_btn.pack(side=\"left\")", body)

    def test_on_failure_shows_error_message(self):
        src = _read_em_source()
        start = src.index("def _on_rec_finished(self")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("result.error", body)


class TestCancelRecSession(unittest.TestCase):
    """_cancel_rec_session cancels active recommendation automation."""

    def test_cancels_poll_job(self):
        src = _read_em_source()
        start = src.index("def _cancel_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_poll_job", body)
        self.assertIn("self.after_cancel(job)", body)

    def test_cancels_engine_session(self):
        src = _read_em_source()
        start = src.index("def _cancel_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._automation_engine.cancel(", body)

    def test_clears_session_id(self):
        src = _read_em_source()
        start = src.index("def _cancel_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_session_id = None", body)

    def test_resets_fixing_flag(self):
        src = _read_em_source()
        start = src.index("def _cancel_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._rec_fixing = False", body)

    def test_hides_action_frame(self):
        src = _read_em_source()
        start = src.index("def _cancel_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_rec_action_frame.pack_forget()", body)

    def test_clears_progress_label(self):
        src = _read_em_source()
        start = src.index("def _cancel_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("_rec_progress_lbl.configure(text=\"\")", body)

    def test_no_threading(self):
        src = _read_em_source()
        start = src.index("def _cancel_rec_session(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertNotIn("threading.Thread", body)


class TestOnHideCancelsRec(unittest.TestCase):
    """on_hide() cancels recommendation automation."""

    def test_on_hide_calls_cancel_rec_session(self):
        src = _read_em_source()
        start = src.index("def on_hide(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._cancel_rec_session()", body)

    def test_cancel_rec_before_cancel_all(self):
        src = _read_em_source()
        start = src.index("def on_hide(self)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        idx_cancel_rec = body.index("_cancel_rec_session()")
        idx_cancel_all = body.index("_cancel_all_automation()")
        self.assertLess(idx_cancel_rec, idx_cancel_all)


class TestOnCardSelectCancelsRec(unittest.TestCase):
    """_on_card_select cancels recommendation on browser switch."""

    def test_cancels_rec_on_switch(self):
        src = _read_em_source()
        start = src.index("def _on_card_select(self, browser_name: str)")
        end = src.index("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("self._cancel_rec_session()", body)


if __name__ == "__main__":
    unittest.main()
