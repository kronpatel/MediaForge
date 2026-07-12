"""
test_browser_profiles.py — Tests for browser profile discovery (Phase 1.2).

Covers:
  - ProfileMetadata: frozen dataclass, is_valid property
  - BrowserScanResult: profile_count, default_profile, valid_profiles
  - _is_profile_dir: regex matching for Chromium profile directory names
  - BrowserProfileManager.get_profiles
  - BrowserProfileManager.get_default_profile
  - BrowserProfileManager.find_preferences
  - BrowserProfileManager.scan
  - Edge cases: missing directories, non-existent browsers, corrupt entries
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

# Ensure companion/ is importable
_COMPANION = os.path.dirname(os.path.abspath(__file__))
if _COMPANION not in sys.path:
    sys.path.insert(0, _COMPANION)

from browser.browser_defs import BrowserDefinition, chrome_definition, brave_definition, edge_definition
from browser.browser_profiles import (
    BrowserProfileManager,
    BrowserScanResult,
    ProfileMetadata,
    _is_profile_dir,
    _safe_isdir,
    _safe_isfile,
    _safe_scandir,
)
from browser.browser_registry import BrowserRegistry


# ═══════════════════════════════════════════════════════════════════════════
# _is_profile_dir tests
# ═══════════════════════════════════════════════════════════════════════════

class TestIsProfileDir(unittest.TestCase):
    def test_default(self):
        self.assertTrue(_is_profile_dir("Default"))

    def test_default_lowercase(self):
        self.assertTrue(_is_profile_dir("default"))

    def test_profile_1(self):
        self.assertTrue(_is_profile_dir("Profile 1"))

    def test_profile_2(self):
        self.assertTrue(_is_profile_dir("Profile 2"))

    def test_profile_99(self):
        self.assertTrue(_is_profile_dir("Profile 99"))

    def test_guest_profile(self):
        self.assertTrue(_is_profile_dir("Guest Profile"))

    def test_system_profile(self):
        self.assertTrue(_is_profile_dir("System Profile"))

    def test_invalid_name(self):
        self.assertFalse(_is_profile_dir("SomethingElse"))

    def test_empty_string(self):
        self.assertFalse(_is_profile_dir(""))

    def test_profile_no_space(self):
        self.assertFalse(_is_profile_dir("Profile1"))

    def test_profile_extra_text(self):
        self.assertFalse(_is_profile_dir("Profile 1 Extra"))


# ═══════════════════════════════════════════════════════════════════════════
# Safe filesystem helpers
# ═══════════════════════════════════════════════════════════════════════════

class TestSafeIsdir(unittest.TestCase):
    def test_existing_temp_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(_safe_isdir(td))

    def test_nonexistent(self):
        self.assertFalse(_safe_isdir("Z:\\nonexistent\\dir"))

    def test_empty_string(self):
        self.assertFalse(_safe_isdir(""))


class TestSafeIsfile(unittest.TestCase):
    def test_existing_file(self):
        with tempfile.NamedTemporaryFile() as tf:
            self.assertTrue(_safe_isfile(tf.name))

    def test_nonexistent(self):
        self.assertFalse(_safe_isfile("Z:\\nonexistent\\file.txt"))


class TestSafeScandir(unittest.TestCase):
    def test_existing_dir(self):
        with tempfile.TemporaryDirectory() as td:
            result = _safe_scandir(td)
            self.assertIsInstance(result, list)

    def test_nonexistent_dir(self):
        result = _safe_scandir("Z:\\nonexistent")
        self.assertEqual(result, [])


# ═══════════════════════════════════════════════════════════════════════════
# ProfileMetadata tests
# ═══════════════════════════════════════════════════════════════════════════

class TestProfileMetadata(unittest.TestCase):
    def test_default_profile(self):
        m = ProfileMetadata(
            name="Default",
            path="C:\\User Data\\Default",
            preferences_path="C:\\User Data\\Default\\Preferences",
            preferences_exists=True,
            is_default=True,
        )
        self.assertTrue(m.is_valid)
        self.assertTrue(m.is_default)

    def test_numbered_profile(self):
        m = ProfileMetadata(
            name="Profile 1",
            path="C:\\User Data\\Profile 1",
            preferences_path="C:\\User Data\\Profile 1\\Preferences",
            preferences_exists=True,
            is_default=False,
        )
        self.assertTrue(m.is_valid)
        self.assertFalse(m.is_default)

    def test_missing_preferences(self):
        m = ProfileMetadata(
            name="Default",
            path="C:\\User Data\\Default",
            preferences_path="C:\\User Data\\Default\\Preferences",
            preferences_exists=False,
            is_default=True,
        )
        self.assertFalse(m.is_valid)

    def test_with_error(self):
        m = ProfileMetadata(
            name="Default",
            path="C:\\User Data\\Default",
            preferences_path="C:\\User Data\\Default\\Preferences",
            preferences_exists=True,
            is_default=True,
            error="Permission denied",
        )
        self.assertFalse(m.is_valid)

    def test_frozen(self):
        m = ProfileMetadata(
            name="Default",
            path="C:\\path",
            preferences_path="C:\\path\\Preferences",
            preferences_exists=True,
            is_default=True,
        )
        with self.assertRaises(AttributeError):
            m.name = "Changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════
# BrowserScanResult tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserScanResult(unittest.TestCase):
    def _make_profile(self, name: str, valid: bool = True) -> ProfileMetadata:
        return ProfileMetadata(
            name=name,
            path=f"C:\\UD\\{name}",
            preferences_path=f"C:\\UD\\{name}\\Preferences",
            preferences_exists=valid,
            is_default=(name == "Default"),
        )

    def test_profile_count_empty(self):
        r = BrowserScanResult(browser_name="Chrome", user_data_dir="C:\\UD", user_data_dir_exists=True)
        self.assertEqual(r.profile_count, 0)

    def test_profile_count_with_profiles(self):
        profiles = [self._make_profile("Default"), self._make_profile("Profile 1")]
        r = BrowserScanResult(
            browser_name="Chrome",
            user_data_dir="C:\\UD",
            user_data_dir_exists=True,
            profiles=profiles,
        )
        self.assertEqual(r.profile_count, 2)

    def test_default_profile_found(self):
        profiles = [self._make_profile("Default"), self._make_profile("Profile 1")]
        r = BrowserScanResult(
            browser_name="Chrome",
            user_data_dir="C:\\UD",
            user_data_dir_exists=True,
            profiles=profiles,
        )
        dp = r.default_profile
        self.assertIsNotNone(dp)
        assert dp is not None
        self.assertEqual(dp.name, "Default")

    def test_default_profile_not_found(self):
        profiles = [self._make_profile("Profile 1")]
        r = BrowserScanResult(
            browser_name="Chrome",
            user_data_dir="C:\\UD",
            user_data_dir_exists=True,
            profiles=profiles,
        )
        self.assertIsNone(r.default_profile)

    def test_valid_profiles(self):
        profiles = [
            self._make_profile("Default", valid=True),
            self._make_profile("Profile 1", valid=False),
            self._make_profile("Profile 2", valid=True),
        ]
        r = BrowserScanResult(
            browser_name="Chrome",
            user_data_dir="C:\\UD",
            user_data_dir_exists=True,
            profiles=profiles,
        )
        valid = r.valid_profiles
        self.assertEqual(len(valid), 2)
        names = [p.name for p in valid]
        self.assertIn("Default", names)
        self.assertIn("Profile 2", names)
        self.assertNotIn("Profile 1", names)


# ═══════════════════════════════════════════════════════════════════════════
# BrowserProfileManager — scan with real temp directories
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserProfileManagerScan(unittest.TestCase):
    """Integration tests that create real temporary profile structures."""

    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def _make_user_data(self, profiles: list[str], *, write_prefs: bool = True) -> str:
        """Create a temp User Data dir with profile subdirectories."""
        td = tempfile.mkdtemp(prefix="browser_profiles_test_")
        for name in profiles:
            profile_dir = os.path.join(td, name)
            os.makedirs(profile_dir, exist_ok=True)
            if write_prefs:
                prefs_path = os.path.join(profile_dir, "Preferences")
                with open(prefs_path, "w", encoding="utf-8") as fh:
                    fh.write("{}")
        return td

    def _make_definition(self, user_data_dir: str, name: str = "TestBrowser") -> BrowserDefinition:
        return BrowserDefinition(
            name=name,
            user_data_dir=user_data_dir,
            search_paths=[],
        )

    def test_scan_with_default_and_profiles(self):
        udd = self._make_user_data(["Default", "Profile 1", "Profile 2"])
        defn = self._make_definition(udd)
        result = BrowserProfileManager.scan(defn)
        self.assertTrue(result.user_data_dir_exists)
        self.assertEqual(result.profile_count, 3)
        self.assertIsNotNone(result.default_profile)
        dp = result.default_profile
        assert dp is not None
        self.assertEqual(dp.name, "Default")
        # Default should be first
        self.assertEqual(result.profiles[0].name, "Default")

    def test_scan_profile_default_is_valid(self):
        udd = self._make_user_data(["Default"])
        defn = self._make_definition(udd)
        result = BrowserProfileManager.scan(defn)
        self.assertEqual(result.profile_count, 1)
        self.assertTrue(result.profiles[0].is_valid)
        self.assertTrue(result.profiles[0].is_default)

    def test_scan_profile_without_preferences(self):
        udd = self._make_user_data(["Default"], write_prefs=False)
        defn = self._make_definition(udd)
        result = BrowserProfileManager.scan(defn)
        self.assertEqual(result.profile_count, 1)
        self.assertFalse(result.profiles[0].preferences_exists)
        self.assertFalse(result.profiles[0].is_valid)

    def test_scan_nonexistent_user_data_dir(self):
        defn = self._make_definition("Z:\\nonexistent\\User Data")
        result = BrowserProfileManager.scan(defn)
        self.assertFalse(result.user_data_dir_exists)
        self.assertEqual(result.profile_count, 0)
        self.assertIn("not found", result.error.lower())

    def test_scan_empty_user_data_dir(self):
        udd = tempfile.mkdtemp(prefix="empty_ud_")
        defn = self._make_definition(udd)
        result = BrowserProfileManager.scan(defn)
        self.assertTrue(result.user_data_dir_exists)
        self.assertEqual(result.profile_count, 0)

    def test_scan_ignores_non_profile_dirs(self):
        udd = tempfile.mkdtemp(prefix="mixed_")
        # Real profile
        profile_dir = os.path.join(udd, "Default")
        os.makedirs(profile_dir)
        with open(os.path.join(profile_dir, "Preferences"), "w") as fh:
            fh.write("{}")
        # Non-profile entries
        os.makedirs(os.path.join(udd, "Cache"))
        os.makedirs(os.path.join(udd, "SomethingElse"))
        defn = self._make_definition(udd)
        result = BrowserProfileManager.scan(defn)
        self.assertEqual(result.profile_count, 1)
        self.assertEqual(result.profiles[0].name, "Default")

    def test_scan_unknown_browser_string(self):
        result = BrowserProfileManager.scan("Firefox")
        self.assertIn("Unknown browser", result.error)

    def test_scan_by_name(self):
        """Scan using browser name string (resolved via registry)."""
        udd = self._make_user_data(["Default"])
        # Register a custom browser with a known user data dir
        reg = BrowserRegistry.instance()
        defn = BrowserDefinition(
            name="TestBrowser",
            user_data_dir=udd,
            search_paths=[],
        )
        reg.register(defn)
        result = BrowserProfileManager.scan("TestBrowser")
        self.assertTrue(result.user_data_dir_exists)
        self.assertEqual(result.profile_count, 1)

    def test_scan_empty_user_data_dir_path(self):
        defn = BrowserDefinition(name="Empty", user_data_dir="", search_paths=[])
        result = BrowserProfileManager.scan(defn)
        self.assertIn("No user data directory", result.error)


# ═══════════════════════════════════════════════════════════════════════════
# BrowserProfileManager — get_profiles
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserProfileManagerGetProfiles(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def _make_user_data(self, profiles: list[str]) -> str:
        td = tempfile.mkdtemp(prefix="bp_getprofiles_")
        for name in profiles:
            profile_dir = os.path.join(td, name)
            os.makedirs(profile_dir, exist_ok=True)
            prefs_path = os.path.join(profile_dir, "Preferences")
            with open(prefs_path, "w", encoding="utf-8") as fh:
                fh.write("{}")
        return td

    def test_get_profiles_returns_list(self):
        udd = self._make_user_data(["Default", "Profile 1"])
        defn = BrowserDefinition(name="TB", user_data_dir=udd, search_paths=[])
        profiles = BrowserProfileManager.get_profiles(defn)
        self.assertEqual(len(profiles), 2)

    def test_get_profiles_nonexistent(self):
        defn = BrowserDefinition(name="TB", user_data_dir="Z:\\nope", search_paths=[])
        profiles = BrowserProfileManager.get_profiles(defn)
        self.assertEqual(len(profiles), 0)


# ═══════════════════════════════════════════════════════════════════════════
# BrowserProfileManager — get_default_profile
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserProfileManagerGetDefaultProfile(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def _make_user_data(self, profiles: list[str]) -> str:
        td = tempfile.mkdtemp(prefix="bp_default_")
        for name in profiles:
            profile_dir = os.path.join(td, name)
            os.makedirs(profile_dir, exist_ok=True)
            prefs_path = os.path.join(profile_dir, "Preferences")
            with open(prefs_path, "w", encoding="utf-8") as fh:
                fh.write("{}")
        return td

    def test_get_default_profile_found(self):
        udd = self._make_user_data(["Default", "Profile 1"])
        defn = BrowserDefinition(name="TB", user_data_dir=udd, search_paths=[])
        default = BrowserProfileManager.get_default_profile(defn)
        self.assertIsNotNone(default)
        assert default is not None
        self.assertEqual(default.name, "Default")

    def test_get_default_profile_not_found(self):
        udd = self._make_user_data(["Profile 1", "Profile 2"])
        defn = BrowserDefinition(name="TB", user_data_dir=udd, search_paths=[])
        default = BrowserProfileManager.get_default_profile(defn)
        self.assertIsNone(default)

    def test_get_default_profile_nonexistent_dir(self):
        defn = BrowserDefinition(name="TB", user_data_dir="Z:\\nope", search_paths=[])
        default = BrowserProfileManager.get_default_profile(defn)
        self.assertIsNone(default)


# ═══════════════════════════════════════════════════════════════════════════
# BrowserProfileManager — find_preferences
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserProfileManagerFindPreferences(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def _make_user_data(self, profiles: list[str], *, write_prefs: bool = True) -> str:
        td = tempfile.mkdtemp(prefix="bp_findprefs_")
        for name in profiles:
            profile_dir = os.path.join(td, name)
            os.makedirs(profile_dir, exist_ok=True)
            if write_prefs:
                prefs_path = os.path.join(profile_dir, "Preferences")
                with open(prefs_path, "w", encoding="utf-8") as fh:
                    fh.write("{}")
        return td

    def test_find_preferences_returns_mapping(self):
        udd = self._make_user_data(["Default", "Profile 1"])
        defn = BrowserDefinition(name="TB", user_data_dir=udd, search_paths=[])
        prefs = BrowserProfileManager.find_preferences(defn)
        self.assertEqual(len(prefs), 2)
        self.assertIn("Default", prefs)
        self.assertIn("Profile 1", prefs)
        self.assertTrue(prefs["Default"].endswith("Preferences"))

    def test_find_preferences_excludes_missing(self):
        udd = self._make_user_data(["Default", "Profile 1"], write_prefs=False)
        defn = BrowserDefinition(name="TB", user_data_dir=udd, search_paths=[])
        prefs = BrowserProfileManager.find_preferences(defn)
        self.assertEqual(len(prefs), 0)

    def test_find_preferences_nonexistent_dir(self):
        defn = BrowserDefinition(name="TB", user_data_dir="Z:\\nope", search_paths=[])
        prefs = BrowserProfileManager.find_preferences(defn)
        self.assertEqual(len(prefs), 0)


# ═══════════════════════════════════════════════════════════════════════════
# BrowserProfileManager — real browser definitions (mocked paths)
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserProfileManagerWithRealDefinitions(unittest.TestCase):
    """Tests using Chrome/Brave/Edge definitions with mocked filesystem."""

    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    @patch("browser.browser_profiles._safe_isdir", return_value=True)
    @patch("browser.browser_profiles._safe_scandir")
    def test_scan_chrome_definition(self, mock_scandir, mock_isdir):
        mock_scandir.return_value = ["Default", "Profile 1"]
        defn = chrome_definition()
        # Override user_data_dir to a known path for testing
        defn_copy = BrowserDefinition(
            name=defn.name,
            user_data_dir="C:\\test\\Chrome\\User Data",
            search_paths=defn.search_paths,
        )
        result = BrowserProfileManager.scan(defn_copy)
        self.assertEqual(result.browser_name, "Chrome")
        self.assertEqual(result.profile_count, 2)

    @patch("browser.browser_profiles._safe_isdir", return_value=True)
    @patch("browser.browser_profiles._safe_scandir")
    def test_scan_brave_definition(self, mock_scandir, mock_isdir):
        mock_scandir.return_value = ["Default"]
        defn = brave_definition()
        defn_copy = BrowserDefinition(
            name=defn.name,
            user_data_dir="C:\\test\\Brave\\User Data",
            search_paths=defn.search_paths,
        )
        result = BrowserProfileManager.scan(defn_copy)
        self.assertEqual(result.browser_name, "Brave")
        self.assertEqual(result.profile_count, 1)

    @patch("browser.browser_profiles._safe_isdir", return_value=True)
    @patch("browser.browser_profiles._safe_scandir")
    def test_scan_edge_definition(self, mock_scandir, mock_isdir):
        mock_scandir.return_value = ["Default", "Profile 1", "Profile 2"]
        defn = edge_definition()
        defn_copy = BrowserDefinition(
            name=defn.name,
            user_data_dir="C:\\test\\Edge\\User Data",
            search_paths=defn.search_paths,
        )
        result = BrowserProfileManager.scan(defn_copy)
        self.assertEqual(result.browser_name, "Edge")
        self.assertEqual(result.profile_count, 3)


# ═══════════════════════════════════════════════════════════════════════════
# Thread-safety smoke test
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserProfileManagerThreadSafety(unittest.TestCase):
    """Verify that concurrent calls to BrowserProfileManager do not crash."""

    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_concurrent_scan(self):
        td = tempfile.mkdtemp(prefix="bp_thread_")
        for name in ["Default", "Profile 1"]:
            profile_dir = os.path.join(td, name)
            os.makedirs(profile_dir)
            with open(os.path.join(profile_dir, "Preferences"), "w") as fh:
                fh.write("{}")
        defn = BrowserDefinition(name="TB", user_data_dir=td, search_paths=[])

        results: list[BrowserScanResult] = []
        errors: list[Exception] = []

        def _worker():
            try:
                r = BrowserProfileManager.scan(defn)
                results.append(r)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(len(errors), 0, f"Errors in threads: {errors}")
        self.assertEqual(len(results), 10)
        for r in results:
            self.assertEqual(r.profile_count, 2)


# ═══════════════════════════════════════════════════════════════════════════
# Package import tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPackageImports(unittest.TestCase):
    def test_import_from_browser_package(self):
        from browser import BrowserProfileManager, BrowserScanResult, ProfileMetadata
        self.assertIsNotNone(BrowserProfileManager)
        self.assertIsNotNone(BrowserScanResult)
        self.assertIsNotNone(ProfileMetadata)

    def test_import_from_browser_profiles_module(self):
        from browser.browser_profiles import (
            BrowserProfileManager,
            BrowserScanResult,
            ProfileMetadata,
        )
        self.assertIsNotNone(BrowserProfileManager)


if __name__ == "__main__":
    unittest.main()
