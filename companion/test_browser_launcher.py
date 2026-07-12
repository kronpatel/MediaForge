"""
test_browser_launcher.py — Tests for the browser package (Phase 1.1).

Covers:
  - _path_utils: paths_match, is_executable, validate_executable_paths
  - _version_reader: read_browser_version (non-Windows returns "")
  - browser_defs: definition factories, BrowserCapabilities.has()
  - browser_info: BrowserInfo.display_label, LaunchResult.failed, LaunchErrorCode enum
  - browser_registry: singleton, register/unregister, lookup, find_by_exe, installed_browsers
  - browser_launcher: detect, detect_all, detect_first, detect_by_name, launch, launch_browser
  - backward-compatible free functions: detect_chrome, detect_all_browsers, detect_first_browser
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

from browser._path_utils import is_executable, paths_match, validate_executable_paths
from browser._version_reader import read_browser_version
from browser.browser_defs import (
    BrowserCapabilities,
    BrowserDefinition,
    BrowserFeature,
    all_browser_definitions,
    brave_definition,
    chrome_definition,
    edge_definition,
)
from browser.browser_info import (
    BrowserInfo,
    BrowserProfileResult,
    BrowserRegistrationResult,
    EnterprisePolicyResult,
    ExtensionStatus,
    LaunchErrorCode,
    LaunchResult,
)
from browser.browser_launcher import (
    BrowserLauncher,
    detect_all_browsers,
    detect_chrome,
    detect_first_browser,
)
from browser.browser_registry import BrowserRegistry


# ═══════════════════════════════════════════════════════════════════════════
# _path_utils tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPathsMatch(unittest.TestCase):
    def test_identical_paths(self):
        self.assertTrue(paths_match("C:\\a\\b", "C:\\a\\b"))

    def test_case_insensitive(self):
        self.assertTrue(paths_match("C:\\A\\B", "C:\\a\\b"))

    def test_normalized(self):
        self.assertTrue(paths_match("C:\\a\\..\\a\\b", "C:\\a\\b"))

    def test_different_paths(self):
        self.assertFalse(paths_match("C:\\a\\b", "C:\\a\\c"))

    def test_empty_strings(self):
        self.assertTrue(paths_match("", ""))


class TestIsExecutable(unittest.TestCase):
    def test_empty_string(self):
        self.assertFalse(is_executable(""))

    def test_blank_string(self):
        self.assertFalse(is_executable("   "))

    def test_none_like(self):
        self.assertFalse(is_executable(""))

    @patch("browser._path_utils.sys")
    def test_non_windows_returns_false(self, mock_sys):
        mock_sys.platform = "linux"
        self.assertFalse(is_executable("/usr/bin/something"))

    def test_nonexistent_file(self):
        self.assertFalse(is_executable("Z:\\nonexistent\\fake.exe"))


class TestValidateExecutablePaths(unittest.TestCase):
    def test_filters_nonexistent(self):
        result = validate_executable_paths([
            "Z:\\nonexistent\\a.exe",
            "Z:\\nonexistent\\b.exe",
        ])
        self.assertEqual(result, [])

    def test_empty_input(self):
        self.assertEqual(validate_executable_paths([]), [])


# ═══════════════════════════════════════════════════════════════════════════
# _version_reader tests
# ═══════════════════════════════════════════════════════════════════════════

class TestVersionReader(unittest.TestCase):
    @patch("browser._version_reader.sys")
    def test_non_windows_returns_empty(self, mock_sys):
        mock_sys.platform = "linux"
        self.assertEqual(read_browser_version("/usr/bin/chrome"), "")

    def test_nonexistent_file_returns_empty(self):
        self.assertEqual(read_browser_version("Z:\\no\\such\\file.exe"), "")


# ═══════════════════════════════════════════════════════════════════════════
# browser_defs tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserDefs(unittest.TestCase):
    def test_chrome_definition(self):
        d = chrome_definition()
        self.assertEqual(d.name, "Chrome")
        self.assertIsInstance(d, BrowserDefinition)
        self.assertTrue(len(d.search_paths) > 0)
        self.assertIn("chrome.exe", d.exe_names)

    def test_brave_definition(self):
        d = brave_definition()
        self.assertEqual(d.name, "Brave")
        self.assertIn("brave.exe", d.exe_names)

    def test_edge_definition(self):
        d = edge_definition()
        self.assertEqual(d.name, "Edge")
        self.assertIn("msedge.exe", d.exe_names)

    def test_all_browser_definitions_count(self):
        defs = all_browser_definitions()
        self.assertEqual(len(defs), 3)
        names = [d.name for d in defs]
        self.assertIn("Chrome", names)
        self.assertIn("Brave", names)
        self.assertIn("Edge", names)

    def test_frozen_dataclass(self):
        d = chrome_definition()
        with self.assertRaises(AttributeError):
            d.name = "Firefox"  # type: ignore[misc]


class TestBrowserCapabilities(unittest.TestCase):
    def test_defaults_all_true(self):
        caps = BrowserCapabilities()
        self.assertTrue(caps.supports_profiles)
        self.assertTrue(caps.supports_extensions)
        self.assertTrue(caps.supports_dev_mode)
        self.assertTrue(caps.supports_sideloading)
        self.assertTrue(caps.supports_persistent_profiles)

    def test_has_feature(self):
        caps = BrowserCapabilities()
        self.assertTrue(caps.has(BrowserFeature.SUPPORTS_PROFILES))
        self.assertFalse(caps.has("nonexistent_feature"))

    def test_partial_caps(self):
        caps = BrowserCapabilities(supports_profiles=False)
        self.assertFalse(caps.supports_profiles)
        self.assertTrue(caps.supports_extensions)


# ═══════════════════════════════════════════════════════════════════════════
# browser_info tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserInfo(unittest.TestCase):
    def test_not_installed_label(self):
        info = BrowserInfo(name="Chrome", installed=False)
        self.assertIn("not found", info.display_label)

    def test_installed_with_version(self):
        info = BrowserInfo(name="Chrome", installed=True, version="126.0.6478.127")
        self.assertEqual(info.display_label, "Chrome 126.0.6478.127")

    def test_installed_with_channel_and_version(self):
        info = BrowserInfo(name="Chrome", installed=True, channel="Stable", version="126.0")
        self.assertEqual(info.display_label, "Chrome Stable 126.0")

    def test_detected_property(self):
        info = BrowserInfo(installed=True)
        self.assertTrue(info.detected)
        info2 = BrowserInfo(installed=False)
        self.assertFalse(info2.detected)


class TestLaunchResult(unittest.TestCase):
    def test_success_result(self):
        r = LaunchResult(success=True, pid=1234)
        self.assertFalse(r.failed)
        self.assertEqual(r.pid, 1234)

    def test_failure_result(self):
        r = LaunchResult(success=False, error_code=LaunchErrorCode.NOT_FOUND)
        self.assertTrue(r.failed)

    def test_error_codes(self):
        self.assertEqual(LaunchErrorCode.NOT_FOUND.value, "not_found")
        self.assertEqual(LaunchErrorCode.PERMISSION_DENIED.value, "permission_denied")
        self.assertEqual(LaunchErrorCode.UNKNOWN.value, "unknown")


class TestExtensionStatus(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(ExtensionStatus.COMPATIBLE, "Compatible")
        self.assertEqual(ExtensionStatus.MISMATCH, "Version Mismatch")
        self.assertEqual(ExtensionStatus.MISSING, "Extension Missing")
        self.assertEqual(ExtensionStatus.NOT_INSTALLED, "Not Installed")
        self.assertEqual(ExtensionStatus.UNKNOWN, "Unknown")


# ═══════════════════════════════════════════════════════════════════════════
# browser_registry tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserRegistry(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_singleton(self):
        r1 = BrowserRegistry.instance()
        r2 = BrowserRegistry.instance()
        self.assertIs(r1, r2)

    def test_reset_creates_new_instance(self):
        r1 = BrowserRegistry.instance()
        BrowserRegistry.reset()
        r2 = BrowserRegistry.instance()
        self.assertIsNot(r1, r2)

    def test_default_has_three_browsers(self):
        reg = BrowserRegistry.instance()
        self.assertEqual(len(reg.all()), 3)
        self.assertIn("Chrome", reg.names())
        self.assertIn("Brave", reg.names())
        self.assertIn("Edge", reg.names())

    def test_register_custom_browser(self):
        reg = BrowserRegistry.instance()
        custom = BrowserDefinition(name="Opera", exe_names=["opera.exe"])
        reg.register(custom)
        self.assertTrue(reg.has("Opera"))
        self.assertIn("Opera", reg.names())

    def test_unregister(self):
        reg = BrowserRegistry.instance()
        self.assertTrue(reg.has("Chrome"))
        self.assertTrue(reg.unregister("Chrome"))
        self.assertFalse(reg.has("Chrome"))

    def test_unregister_nonexistent(self):
        reg = BrowserRegistry.instance()
        self.assertFalse(reg.unregister("Firefox"))

    def test_get(self):
        reg = BrowserRegistry.instance()
        d = reg.get("Chrome")
        self.assertIsNotNone(d)
        self.assertEqual(d.name, "Chrome")

    def test_get_nonexistent(self):
        reg = BrowserRegistry.instance()
        self.assertIsNone(reg.get("Firefox"))

    def test_find_by_exe(self):
        reg = BrowserRegistry.instance()
        d = reg.find_by_exe("brave.exe")
        self.assertIsNotNone(d)
        self.assertEqual(d.name, "Brave")

    def test_find_by_exe_case_insensitive(self):
        reg = BrowserRegistry.instance()
        d = reg.find_by_exe("CHROME.EXE")
        self.assertIsNotNone(d)
        self.assertEqual(d.name, "Chrome")

    def test_find_by_exe_nonexistent(self):
        reg = BrowserRegistry.instance()
        self.assertIsNone(reg.find_by_exe("opera.exe"))

    @patch("browser.browser_registry._is_file")
    def test_installed_browsers(self, mock_is_file):
        mock_is_file.return_value = True
        reg = BrowserRegistry.instance()
        installed = reg.installed_browsers()
        self.assertEqual(len(installed), 3)

    @patch("browser.browser_registry._is_file")
    def test_installed_browsers_none_found(self, mock_is_file):
        mock_is_file.return_value = False
        reg = BrowserRegistry.instance()
        installed = reg.installed_browsers()
        self.assertEqual(len(installed), 0)


# ═══════════════════════════════════════════════════════════════════════════
# browser_launcher tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserLauncherDetect(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    @patch("browser.browser_launcher.is_executable")
    @patch("browser.browser_launcher.read_browser_version")
    def test_detect_found(self, mock_ver, mock_exe):
        mock_exe.return_value = True
        mock_ver.return_value = "126.0.6478.127"
        defn = chrome_definition()
        info = BrowserLauncher.detect(defn)
        self.assertTrue(info.installed)
        self.assertEqual(info.name, "Chrome")
        self.assertEqual(info.version, "126.0.6478.127")
        self.assertTrue(len(info.path) > 0)

    @patch("browser.browser_launcher.is_executable")
    def test_detect_not_found(self, mock_exe):
        mock_exe.return_value = False
        defn = chrome_definition()
        info = BrowserLauncher.detect(defn)
        self.assertFalse(info.installed)
        self.assertEqual(info.name, "Chrome")
        self.assertEqual(info.path, "")

    @patch("browser.browser_launcher.sys")
    def test_detect_non_windows(self, mock_sys):
        mock_sys.platform = "linux"
        info = BrowserLauncher.detect(chrome_definition())
        self.assertFalse(info.installed)

    @patch("browser.browser_launcher.is_executable")
    def test_detect_exception_handled(self, mock_exe):
        mock_exe.side_effect = OSError("disk error")
        info = BrowserLauncher.detect(chrome_definition())
        self.assertFalse(info.installed)


class TestBrowserLauncherDetectAll(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    @patch("browser.browser_launcher.is_executable")
    def test_detect_all(self, mock_exe):
        mock_exe.return_value = True
        results = BrowserLauncher.detect_all()
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.installed for r in results))

    @patch("browser.browser_launcher.is_executable")
    def test_detect_all_none_installed(self, mock_exe):
        mock_exe.return_value = False
        results = BrowserLauncher.detect_all()
        self.assertEqual(len(results), 3)
        self.assertFalse(any(r.installed for r in results))


class TestBrowserLauncherDetectFirst(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    @patch("browser.browser_launcher.is_executable")
    def test_detect_first_found(self, mock_exe):
        mock_exe.return_value = True
        info = BrowserLauncher.detect_first()
        self.assertIsNotNone(info)
        self.assertTrue(info.installed)

    @patch("browser.browser_launcher.is_executable")
    def test_detect_first_none(self, mock_exe):
        mock_exe.return_value = False
        info = BrowserLauncher.detect_first()
        self.assertIsNone(info)


class TestBrowserLauncherDetectByName(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    @patch("browser.browser_launcher.is_executable")
    def test_detect_by_name(self, mock_exe):
        mock_exe.return_value = True
        info = BrowserLauncher.detect_by_name("Brave")
        self.assertIsNotNone(info)
        self.assertEqual(info.name, "Brave")
        self.assertTrue(info.installed)

    def test_detect_by_name_nonexistent(self):
        info = BrowserLauncher.detect_by_name("Firefox")
        self.assertIsNone(info)


class TestBrowserLauncherLaunch(unittest.TestCase):
    @patch("browser.browser_launcher.is_executable")
    @patch("browser.browser_launcher.subprocess.Popen")
    def test_launch_success(self, mock_popen, mock_exe):
        mock_exe.return_value = True
        mock_proc = MagicMock()
        mock_proc.pid = 9999
        mock_popen.return_value = mock_proc
        result = BrowserLauncher.launch("C:\\fake\\chrome.exe", url="https://example.com")
        self.assertTrue(result.success)
        self.assertEqual(result.pid, 9999)
        mock_popen.assert_called_once()

    def test_launch_empty_path(self):
        result = BrowserLauncher.launch("")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, LaunchErrorCode.NOT_FOUND)

    @patch("browser.browser_launcher.is_executable")
    def test_launch_not_found(self, mock_exe):
        mock_exe.return_value = False
        result = BrowserLauncher.launch("C:\\fake\\missing.exe")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, LaunchErrorCode.NOT_FOUND)

    @patch("browser.browser_launcher.is_executable")
    @patch("browser.browser_launcher.subprocess.Popen")
    def test_launch_permission_denied(self, mock_popen, mock_exe):
        mock_exe.return_value = True
        mock_popen.side_effect = PermissionError("denied")
        result = BrowserLauncher.launch("C:\\fake\\chrome.exe")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, LaunchErrorCode.PERMISSION_DENIED)

    @patch("browser.browser_launcher.is_executable")
    @patch("browser.browser_launcher.subprocess.Popen")
    def test_launch_os_error(self, mock_popen, mock_exe):
        mock_exe.return_value = True
        mock_popen.side_effect = OSError("cannot execute")
        result = BrowserLauncher.launch("C:\\fake\\chrome.exe")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, LaunchErrorCode.UNKNOWN)


class TestBrowserLauncherLaunchBrowser(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_launch_browser_not_installed(self):
        with patch("browser.browser_launcher.is_executable", return_value=False):
            result = BrowserLauncher.launch_browser("Chrome")
            self.assertFalse(result.success)
            self.assertEqual(result.error_code, LaunchErrorCode.NOT_FOUND)

    @patch("browser.browser_launcher.is_executable")
    @patch("browser.browser_launcher.subprocess.Popen")
    def test_launch_browser_success(self, mock_popen, mock_exe):
        mock_exe.return_value = True
        mock_proc = MagicMock()
        mock_proc.pid = 5555
        mock_popen.return_value = mock_proc
        result = BrowserLauncher.launch_browser("Chrome", url="https://youtube.com")
        self.assertTrue(result.success)
        self.assertEqual(result.pid, 5555)


# ═══════════════════════════════════════════════════════════════════════════
# Backward-compatible free functions
# ═══════════════════════════════════════════════════════════════════════════

class TestBackwardCompatFunctions(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    @patch("browser.browser_launcher.is_executable")
    def test_detect_chrome(self, mock_exe):
        mock_exe.return_value = True
        info = detect_chrome()
        self.assertEqual(info.name, "Chrome")

    @patch("browser.browser_launcher.is_executable")
    def test_detect_all_browsers(self, mock_exe):
        mock_exe.return_value = True
        results = detect_all_browsers()
        self.assertEqual(len(results), 3)

    @patch("browser.browser_launcher.is_executable")
    def test_detect_first_browser(self, mock_exe):
        mock_exe.return_value = True
        info = detect_first_browser()
        self.assertIsNotNone(info)
        self.assertTrue(info.installed)

    @patch("browser.browser_launcher.is_executable")
    def test_detect_first_browser_none(self, mock_exe):
        mock_exe.return_value = False
        info = detect_first_browser()
        self.assertIsNone(info)


# ═══════════════════════════════════════════════════════════════════════════
# Package import tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPackageImports(unittest.TestCase):
    def test_import_all_public_names(self):
        from browser import (
            BrowserCapabilities,
            BrowserDefinition,
            BrowserFeature,
            BrowserInfo,
            BrowserLauncher,
            BrowserProfileResult,
            BrowserRegistrationResult,
            BrowserRegistry,
            EnterprisePolicyResult,
            ExtensionStatus,
            LaunchErrorCode,
            LaunchResult,
            all_browser_definitions,
            brave_definition,
            chrome_definition,
            detect_all_browsers,
            detect_chrome,
            detect_first_browser,
            edge_definition,
        )
        self.assertIsNotNone(BrowserLauncher)
        self.assertIsNotNone(BrowserRegistry)


if __name__ == "__main__":
    unittest.main()
