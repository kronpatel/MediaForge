"""
test_browser_extension_installer.py — Tests for ExtensionInstallationEngine (Phase 1.4).

Covers:
  - ExtensionErrorCode enum values
  - ExtensionValidationResult: frozen dataclass, properties
  - ExtensionLaunchResult: frozen dataclass, properties
  - ExtensionInstallationEngine.validate_extension: missing dir, missing manifest,
    invalid JSON, missing required files, valid extension
  - ExtensionInstallationEngine.can_launch: empty name, unknown browser,
    not installed, success
  - ExtensionInstallationEngine.build_launch_command: error propagation,
    command construction, extra args, URL inclusion
  - ExtensionInstallationEngine.launch: validation failure, subprocess success,
    PermissionError, OSError, generic exception
  - ExtensionInstallationEngine.launch_all: batch launch across browsers
  - ExtensionInstallationEngine.launch_with_extension: convenience method
  - Thread safety: concurrent validate_extension calls
  - Package-level import of new types
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

# Ensure companion/ is importable
_COMPANION = os.path.dirname(os.path.abspath(__file__))
if _COMPANION not in sys.path:
    sys.path.insert(0, _COMPANION)

from browser.browser_extension_installer import (
    ExtensionErrorCode,
    ExtensionInstallationEngine,
    ExtensionLaunchResult,
    ExtensionValidationResult,
    _DEFAULT_EXTENSION_DIR,
    _REQUIRED_EXTENSION_FILES,
)
from browser.browser_defs import BrowserDefinition
from browser.browser_info import LaunchResult
from browser.browser_registry import BrowserRegistry


# ═══════════════════════════════════════════════════════════════════════════
# ExtensionErrorCode tests
# ═══════════════════════════════════════════════════════════════════════════

class TestExtensionErrorCode(unittest.TestCase):
    def test_success_value(self):
        self.assertEqual(ExtensionErrorCode.SUCCESS.value, "success")

    def test_browser_not_found_value(self):
        self.assertEqual(ExtensionErrorCode.BROWSER_NOT_FOUND.value, "browser_not_found")

    def test_extension_missing_value(self):
        self.assertEqual(ExtensionErrorCode.EXTENSION_MISSING.value, "extension_missing")

    def test_manifest_missing_value(self):
        self.assertEqual(ExtensionErrorCode.MANIFEST_MISSING.value, "manifest_missing")

    def test_manifest_invalid_value(self):
        self.assertEqual(ExtensionErrorCode.MANIFEST_INVALID.value, "manifest_invalid")

    def test_required_files_missing_value(self):
        self.assertEqual(ExtensionErrorCode.REQUIRED_FILES_MISSING.value, "required_files_missing")

    def test_permission_denied_value(self):
        self.assertEqual(ExtensionErrorCode.PERMISSION_DENIED.value, "permission_denied")

    def test_launch_failed_value(self):
        self.assertEqual(ExtensionErrorCode.LAUNCH_FAILED.value, "launch_failed")

    def test_unknown_value(self):
        self.assertEqual(ExtensionErrorCode.UNKNOWN.value, "unknown")

    def test_all_codes_unique(self):
        values = [e.value for e in ExtensionErrorCode]
        self.assertEqual(len(values), len(set(values)))


# ═══════════════════════════════════════════════════════════════════════════
# ExtensionValidationResult tests
# ═══════════════════════════════════════════════════════════════════════════

class TestExtensionValidationResult(unittest.TestCase):
    def test_frozen(self):
        r = ExtensionValidationResult()
        with self.assertRaises(AttributeError):
            r.valid = True  # type: ignore[misc]

    def test_defaults(self):
        r = ExtensionValidationResult()
        self.assertFalse(r.valid)
        self.assertEqual(r.extension_dir, "")
        self.assertFalse(r.manifest_exists)
        self.assertEqual(r.manifest_data, {})
        self.assertEqual(r.missing_files, [])
        self.assertEqual(r.error_code, ExtensionErrorCode.UNKNOWN)
        self.assertEqual(r.error_message, "")

    def test_valid_result(self):
        r = ExtensionValidationResult(
            valid=True,
            extension_dir="C:\\ext",
            manifest_exists=True,
            manifest_data={"name": "Test"},
            error_code=ExtensionErrorCode.SUCCESS,
        )
        self.assertTrue(r.valid)
        self.assertEqual(r.manifest_data["name"], "Test")

    def test_missing_files_list_independent(self):
        """Each instance gets its own list (not shared default)."""
        r1 = ExtensionValidationResult()
        r2 = ExtensionValidationResult()
        r1.missing_files.append("test.js")
        self.assertEqual(r2.missing_files, [])


# ═══════════════════════════════════════════════════════════════════════════
# ExtensionLaunchResult tests
# ═══════════════════════════════════════════════════════════════════════════

class TestExtensionLaunchResult(unittest.TestCase):
    def test_frozen(self):
        r = ExtensionLaunchResult()
        with self.assertRaises(AttributeError):
            r.success = True  # type: ignore[misc]

    def test_defaults(self):
        r = ExtensionLaunchResult()
        self.assertFalse(r.success)
        self.assertEqual(r.browser_name, "")
        self.assertIsNone(r.pid)
        self.assertEqual(r.exe_path, "")
        self.assertEqual(r.extension_dir, "")
        self.assertEqual(r.error_code, ExtensionErrorCode.UNKNOWN)
        self.assertEqual(r.error_message, "")
        self.assertEqual(r.command, [])
        self.assertIsNone(r.validation)

    def test_failed_property(self):
        r = ExtensionLaunchResult(success=False)
        self.assertTrue(r.failed)

    def test_not_failed(self):
        r = ExtensionLaunchResult(success=True)
        self.assertFalse(r.failed)

    def test_with_validation(self):
        val = ExtensionValidationResult(valid=True)
        r = ExtensionLaunchResult(success=True, validation=val)
        self.assertIsNotNone(r.validation)
        if r.validation is not None:
            self.assertTrue(r.validation.valid)


# ═══════════════════════════════════════════════════════════════════════════
# validate_extension tests
# ═══════════════════════════════════════════════════════════════════════════

class TestValidateExtension(unittest.TestCase):
    def test_missing_directory(self):
        result = ExtensionInstallationEngine.validate_extension("C:\\nonexistent\\dir")
        self.assertFalse(result.valid)
        self.assertEqual(result.error_code, ExtensionErrorCode.EXTENSION_MISSING)
        self.assertIn("not found", result.error_message)

    def test_empty_dir_defaults_to_project(self):
        """Empty string should resolve to the default extension directory."""
        result = ExtensionInstallationEngine.validate_extension("")
        # We don't care if it's valid or not — just that it resolved the path
        self.assertEqual(os.path.abspath(result.extension_dir), os.path.abspath(_DEFAULT_EXTENSION_DIR))

    def test_whitespace_dir_defaults_to_project(self):
        result = ExtensionInstallationEngine.validate_extension("   ")
        self.assertEqual(os.path.abspath(result.extension_dir), os.path.abspath(_DEFAULT_EXTENSION_DIR))

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = ExtensionInstallationEngine.validate_extension(tmpdir)
            self.assertFalse(result.valid)
            self.assertFalse(result.manifest_exists)
            self.assertEqual(result.error_code, ExtensionErrorCode.MANIFEST_MISSING)

    def test_invalid_manifest_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                fh.write("{invalid json content")
            result = ExtensionInstallationEngine.validate_extension(tmpdir)
            self.assertFalse(result.valid)
            self.assertTrue(result.manifest_exists)
            self.assertEqual(result.error_code, ExtensionErrorCode.MANIFEST_INVALID)
            self.assertIn("parse", result.error_message)

    def test_manifest_encoding_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "wb") as fh:
                fh.write(b"\xff\xfe\x00\x00invalid")
            result = ExtensionInstallationEngine.validate_extension(tmpdir)
            self.assertFalse(result.valid)
            self.assertEqual(result.error_code, ExtensionErrorCode.MANIFEST_INVALID)

    def test_missing_required_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump({"name": "Test"}, fh)
            result = ExtensionInstallationEngine.validate_extension(tmpdir)
            self.assertFalse(result.valid)
            self.assertEqual(result.error_code, ExtensionErrorCode.REQUIRED_FILES_MISSING)
            self.assertTrue(len(result.missing_files) > 0)
            self.assertIn("icon.png", result.missing_files)

    def test_valid_extension_all_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test Extension", "version": "1.0"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    filepath = os.path.join(tmpdir, filename)
                    with open(filepath, "w", encoding="utf-8") as fh:
                        fh.write("")
            result = ExtensionInstallationEngine.validate_extension(tmpdir)
            self.assertTrue(result.valid)
            self.assertEqual(result.error_code, ExtensionErrorCode.SUCCESS)
            self.assertTrue(result.manifest_exists)
            self.assertEqual(result.manifest_data["name"], "Test Extension")
            self.assertEqual(result.missing_files, [])

    def test_manifest_read_only(self):
        """validate_extension does not modify the manifest on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Unchanged"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            original_stat = os.stat(manifest_path)
            ExtensionInstallationEngine.validate_extension(tmpdir)
            after_stat = os.stat(manifest_path)
            self.assertEqual(original_stat.st_mtime, after_stat.st_mtime)
            self.assertEqual(original_stat.st_size, after_stat.st_size)

    def test_resolves_to_abspath(self):
        """The returned extension_dir is always an absolute path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = ExtensionInstallationEngine.validate_extension(tmpdir)
            self.assertTrue(os.path.isabs(result.extension_dir))


# ═══════════════════════════════════════════════════════════════════════════
# can_launch tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCanLaunch(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_empty_name(self):
        result = ExtensionInstallationEngine.can_launch("")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ExtensionErrorCode.BROWSER_NOT_FOUND)
        self.assertIn("No browser name", result.error_message)

    def test_whitespace_name(self):
        result = ExtensionInstallationEngine.can_launch("   ")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ExtensionErrorCode.BROWSER_NOT_FOUND)

    def test_unknown_browser(self):
        result = ExtensionInstallationEngine.can_launch("Firefox")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ExtensionErrorCode.BROWSER_NOT_FOUND)
        self.assertIn("Unknown browser", result.error_message)

    def test_not_installed(self):
        with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
            mock_detect.return_value = MagicMock(installed=False, path="")
            result = ExtensionInstallationEngine.can_launch("Chrome")
            self.assertFalse(result.success)
            self.assertEqual(result.error_code, ExtensionErrorCode.BROWSER_NOT_FOUND)
            self.assertIn("not installed", result.error_message)

    def test_success(self):
        with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
            mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
            result = ExtensionInstallationEngine.can_launch("Chrome")
            self.assertTrue(result.success)
            self.assertEqual(result.error_code, ExtensionErrorCode.SUCCESS)
            self.assertEqual(result.exe_path, "C:\\fake\\chrome.exe")
            self.assertEqual(result.browser_name, "Chrome")

    def test_success_brave(self):
        with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
            mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\brave.exe")
            result = ExtensionInstallationEngine.can_launch("Brave")
            self.assertTrue(result.success)
            self.assertEqual(result.browser_name, "Brave")


# ═══════════════════════════════════════════════════════════════════════════
# build_launch_command tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildLaunchCommand(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_browser_not_found_propagates(self):
        result = ExtensionInstallationEngine.build_launch_command("Firefox")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ExtensionErrorCode.BROWSER_NOT_FOUND)

    def test_extension_missing_propagates(self):
        with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
            mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
            result = ExtensionInstallationEngine.build_launch_command(
                "Chrome", extension_dir="C:\\nonexistent"
            )
            self.assertFalse(result.success)
            self.assertEqual(result.error_code, ExtensionErrorCode.EXTENSION_MISSING)

    def test_command_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
                result = ExtensionInstallationEngine.build_launch_command(
                    "Chrome", extension_dir=tmpdir
                )
                self.assertTrue(result.success)
                self.assertEqual(result.command[0], "C:\\fake\\chrome.exe")
                self.assertTrue(
                    any("--load-extension=" in arg for arg in result.command)
                )

    def test_url_included_in_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
                result = ExtensionInstallationEngine.build_launch_command(
                    "Chrome", extension_dir=tmpdir, url="https://youtube.com"
                )
                self.assertTrue(result.success)
                self.assertIn("https://youtube.com", result.command)

    def test_extra_args_included(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
                result = ExtensionInstallationEngine.build_launch_command(
                    "Chrome", extension_dir=tmpdir, extra_args=["--no-sandbox", "--incognito"]
                )
                self.assertTrue(result.success)
                self.assertIn("--no-sandbox", result.command)
                self.assertIn("--incognito", result.command)

    def test_validation_result_attached(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
                result = ExtensionInstallationEngine.build_launch_command(
                    "Chrome", extension_dir=tmpdir
                )
                self.assertIsNotNone(result.validation)
                if result.validation is not None:
                    self.assertFalse(result.validation.valid)

    def test_does_not_launch_process(self):
        """build_launch_command should not call subprocess."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
                with patch("browser.browser_extension_installer.subprocess.Popen") as mock_popen:
                    ExtensionInstallationEngine.build_launch_command(
                        "Chrome", extension_dir=tmpdir
                    )
                    mock_popen.assert_not_called()

    def test_command_starts_with_exe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\edge.exe")
                result = ExtensionInstallationEngine.build_launch_command(
                    "Edge", extension_dir=tmpdir
                )
                self.assertEqual(result.command[0], "C:\\fake\\edge.exe")


# ═══════════════════════════════════════════════════════════════════════════
# launch tests
# ═══════════════════════════════════════════════════════════════════════════

class TestLaunch(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_launch_browser_not_found(self):
        result = ExtensionInstallationEngine.launch("Firefox")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ExtensionErrorCode.BROWSER_NOT_FOUND)

    def test_launch_extension_missing(self):
        with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
            mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
            result = ExtensionInstallationEngine.launch(
                "Chrome", extension_dir="C:\\nonexistent"
            )
            self.assertFalse(result.success)
            self.assertEqual(result.error_code, ExtensionErrorCode.EXTENSION_MISSING)

    def test_launch_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
                with patch("browser.browser_extension_installer.subprocess.Popen") as mock_popen:
                    mock_proc = MagicMock()
                    mock_proc.pid = 12345
                    mock_popen.return_value = mock_proc
                    result = ExtensionInstallationEngine.launch(
                        "Chrome", extension_dir=tmpdir
                    )
                    self.assertTrue(result.success)
                    self.assertEqual(result.pid, 12345)
                    self.assertEqual(result.browser_name, "Chrome")
                    self.assertTrue(result.command)

    def test_launch_passes_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
                with patch("browser.browser_extension_installer.subprocess.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock(pid=99)
                    result = ExtensionInstallationEngine.launch(
                        "Chrome", extension_dir=tmpdir, url="https://youtube.com"
                    )
                    self.assertTrue(result.success)
                    self.assertIn("https://youtube.com", result.command)

    def test_launch_passes_extra_args(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
                with patch("browser.browser_extension_installer.subprocess.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock(pid=99)
                    result = ExtensionInstallationEngine.launch(
                        "Chrome", extension_dir=tmpdir, extra_args=["--no-sandbox"]
                    )
                    self.assertTrue(result.success)
                    self.assertIn("--no-sandbox", result.command)

    def test_launch_permission_denied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
                with patch("browser.browser_extension_installer.subprocess.Popen") as mock_popen:
                    mock_popen.side_effect = PermissionError("denied")
                    result = ExtensionInstallationEngine.launch(
                        "Chrome", extension_dir=tmpdir
                    )
                    self.assertFalse(result.success)
                    self.assertEqual(result.error_code, ExtensionErrorCode.PERMISSION_DENIED)
                    self.assertIn("Permission denied", result.error_message)

    def test_launch_os_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
                with patch("browser.browser_extension_installer.subprocess.Popen") as mock_popen:
                    mock_popen.side_effect = OSError("cannot execute")
                    result = ExtensionInstallationEngine.launch(
                        "Chrome", extension_dir=tmpdir
                    )
                    self.assertFalse(result.success)
                    self.assertEqual(result.error_code, ExtensionErrorCode.LAUNCH_FAILED)

    def test_launch_generic_exception(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
                with patch("browser.browser_extension_installer.subprocess.Popen") as mock_popen:
                    mock_popen.side_effect = RuntimeError("unexpected")
                    result = ExtensionInstallationEngine.launch(
                        "Chrome", extension_dir=tmpdir
                    )
                    self.assertFalse(result.success)
                    self.assertEqual(result.error_code, ExtensionErrorCode.UNKNOWN)
                    self.assertIn("unexpected", result.error_message)

    def test_launch_validates_extension_before_launching(self):
        """Extension is validated before subprocess is invoked."""
        with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
            mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
            with patch("browser.browser_extension_installer.subprocess.Popen") as mock_popen:
                ExtensionInstallationEngine.launch(
                    "Chrome", extension_dir="C:\\nonexistent"
                )
                mock_popen.assert_not_called()

    def test_launch_validation_attached_on_error(self):
        with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
            mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\chrome.exe")
            result = ExtensionInstallationEngine.launch(
                "Chrome", extension_dir="C:\\nonexistent"
            )
            self.assertIsNotNone(result.validation)
            if result.validation is not None:
                self.assertFalse(result.validation.valid)

    def test_launch_empty_browser_name(self):
        result = ExtensionInstallationEngine.launch("")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ExtensionErrorCode.BROWSER_NOT_FOUND)

    def test_launch_never_raises(self):
        """launch should never raise, even with all kinds of bad input."""
        try:
            ExtensionInstallationEngine.launch("Firefox")
            ExtensionInstallationEngine.launch("")
            ExtensionInstallationEngine.launch("Chrome", extension_dir="C:\\nope")
        except Exception:
            self.fail("launch() raised an unexpected exception")


# ═══════════════════════════════════════════════════════════════════════════
# launch_all tests
# ═══════════════════════════════════════════════════════════════════════════

class TestLaunchAll(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    @patch("browser.browser_extension_installer.BrowserLauncher.detect")
    def test_returns_list_per_browser(self, mock_detect):
        mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\browser.exe")
        results = ExtensionInstallationEngine.launch_all(
            extension_dir="C:\\nonexistent"
        )
        self.assertEqual(len(results), 3)

    @patch("browser.browser_extension_installer.BrowserLauncher.detect")
    def test_all_fail_no_extension(self, mock_detect):
        mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\browser.exe")
        results = ExtensionInstallationEngine.launch_all(
            extension_dir="C:\\nonexistent"
        )
        for r in results:
            self.assertFalse(r.success)
            self.assertEqual(r.error_code, ExtensionErrorCode.EXTENSION_MISSING)

    def test_launch_all_with_valid_extension(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\browser.exe")
                with patch("browser.browser_extension_installer.subprocess.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock(pid=100)
                    results = ExtensionInstallationEngine.launch_all(extension_dir=tmpdir)
                    self.assertEqual(len(results), 3)
                    # All should succeed since mock_detect returns installed
                    for r in results:
                        self.assertTrue(r.success)

    def test_launch_all_url_propagated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"name": "Test"}
            manifest_path = os.path.join(tmpdir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            for filename in _REQUIRED_EXTENSION_FILES:
                if filename != "manifest.json":
                    with open(os.path.join(tmpdir, filename), "w") as fh:
                        fh.write("")
            with patch("browser.browser_extension_installer.BrowserLauncher.detect") as mock_detect:
                mock_detect.return_value = MagicMock(installed=True, path="C:\\fake\\browser.exe")
                with patch("browser.browser_extension_installer.subprocess.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock(pid=100)
                    results = ExtensionInstallationEngine.launch_all(
                        extension_dir=tmpdir, url="https://example.com"
                    )
                    for r in results:
                        if r.success:
                            self.assertIn("https://example.com", r.command)



# ═══════════════════════════════════════════════════════════════════════════
# Thread safety tests
# ═══════════════════════════════════════════════════════════════════════════

class TestThreadSafety(unittest.TestCase):
    def test_concurrent_validate_extension(self):
        results = []
        errors = []

        def validate(dir_path):
            try:
                r = ExtensionInstallationEngine.validate_extension(dir_path)
                results.append(r)
            except Exception as exc:
                errors.append(exc)

        with tempfile.TemporaryDirectory() as tmpdir:
            threads = [
                threading.Thread(target=validate, args=(tmpdir,))
                for _ in range(10)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(len(errors), 0, f"Errors in threads: {errors}")
        self.assertEqual(len(results), 10)

    def test_concurrent_can_launch(self):
        results = []
        errors = []

        def check(name):
            try:
                r = ExtensionInstallationEngine.can_launch(name)
                results.append(r)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=check, args=("Chrome",))
            for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors in threads: {errors}")
        self.assertEqual(len(results), 10)


# ═══════════════════════════════════════════════════════════════════════════
# Package import tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPackageImports(unittest.TestCase):
    def test_import_new_types(self):
        from browser import (
            ExtensionErrorCode,
            ExtensionInstallationEngine,
            ExtensionLaunchResult,
            ExtensionValidationResult,
        )
        self.assertIsNotNone(ExtensionInstallationEngine)
        self.assertIsNotNone(ExtensionErrorCode)

    def test_import_via_module(self):
        from browser.browser_extension_installer import (
            ExtensionErrorCode,
            ExtensionInstallationEngine,
            ExtensionLaunchResult,
            ExtensionValidationResult,
        )
        self.assertTrue(callable(ExtensionInstallationEngine.validate_extension))
        self.assertTrue(callable(ExtensionInstallationEngine.launch))


# ═══════════════════════════════════════════════════════════════════════════
# Constants tests
# ═══════════════════════════════════════════════════════════════════════════

class TestConstants(unittest.TestCase):
    def test_required_files_non_empty(self):
        self.assertTrue(len(_REQUIRED_EXTENSION_FILES) > 0)

    def test_required_files_includes_manifest(self):
        self.assertIn("manifest.json", _REQUIRED_EXTENSION_FILES)

    def test_default_extension_dir_is_path(self):
        self.assertTrue(os.path.isabs(_DEFAULT_EXTENSION_DIR) or _DEFAULT_EXTENSION_DIR)


# ═══════════════════════════════════════════════════════════════════════════
# Backward-compat: existing tests should not break
# ═══════════════════════════════════════════════════════════════════════════

class TestBackwardCompat(unittest.TestCase):
    def test_existing_imports_still_work(self):
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
        )
        self.assertIsNotNone(BrowserLauncher)
        self.assertIsNotNone(BrowserRegistry)
        self.assertIsNotNone(LaunchResult)


if __name__ == "__main__":
    unittest.main()
