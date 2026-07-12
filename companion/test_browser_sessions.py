"""
test_browser_sessions.py — Tests for browser session detection (Phase 1.3).

Covers:
  - ProcessInfo: frozen dataclass, is_running property
  - BrowserSessionResult: running_count, is_running, pids
  - _iter_processes: psutil scanning
  - _match_process_to_browser: executable name matching
  - _build_exe_names_map: registry-to-exe-map conversion
  - _safe_process_info: graceful error handling
  - _scan_all: single-pass process grouping
  - BrowserSessionManager.running
  - BrowserSessionManager.running_all
  - BrowserSessionManager.find
  - BrowserSessionManager.count
  - BrowserSessionManager.has_running
  - Edge cases: unknown browsers, permission denied, zombie processes
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

import psutil

# Ensure companion/ is importable
_COMPANION = os.path.dirname(os.path.abspath(__file__))
if _COMPANION not in sys.path:
    sys.path.insert(0, _COMPANION)

from browser.browser_defs import BrowserDefinition
from browser.browser_sessions import (
    BrowserSessionManager,
    BrowserSessionResult,
    ProcessInfo,
    _build_exe_names_map,
    _iter_processes,
    _match_process_to_browser,
    _safe_process_info,
    _scan_all,
)
from browser.browser_registry import BrowserRegistry


# ═══════════════════════════════════════════════════════════════════════════
# ProcessInfo tests
# ═══════════════════════════════════════════════════════════════════════════

class TestProcessInfo(unittest.TestCase):
    def test_basic_construction(self):
        pi = ProcessInfo(
            pid=1234,
            name="chrome.exe",
            exe_path="C:\\Program Files\\Google\\Chrome\\chrome.exe",
            browser_name="Chrome",
            browser_exe="chrome.exe",
        )
        self.assertEqual(pi.pid, 1234)
        self.assertEqual(pi.name, "chrome.exe")
        self.assertEqual(pi.browser_name, "Chrome")

    def test_frozen(self):
        pi = ProcessInfo(pid=1, name="a", exe_path="", browser_name="A", browser_exe="a")
        with self.assertRaises(AttributeError):
            pi.pid = 999  # type: ignore[misc]

    @patch("browser.browser_sessions.psutil.Process")
    def test_is_running_true(self, MockProcess):
        mock_proc = MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.is_zombie.return_value = False
        MockProcess.return_value = mock_proc

        pi = ProcessInfo(pid=100, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")
        self.assertTrue(pi.is_running)
        mock_proc.is_running.assert_called_once()

    @patch("browser.browser_sessions.psutil.Process")
    def test_is_running_exited(self, MockProcess):
        mock_proc = MagicMock()
        mock_proc.is_running.return_value = False
        MockProcess.return_value = mock_proc

        pi = ProcessInfo(pid=100, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")
        self.assertFalse(pi.is_running)

    @patch("browser.browser_sessions.psutil.Process")
    def test_is_running_zombie(self, MockProcess):
        import psutil as _psutil
        mock_proc = MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.is_zombie.return_value = True
        MockProcess.return_value = mock_proc

        pi = ProcessInfo(pid=100, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")
        self.assertFalse(pi.is_running)

    @patch("browser.browser_sessions.psutil.Process")
    def test_is_running_no_such_process(self, MockProcess):
        import psutil as _psutil
        MockProcess.side_effect = _psutil.NoSuchProcess(999)

        pi = ProcessInfo(pid=999, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")
        self.assertFalse(pi.is_running)

    @patch("browser.browser_sessions.psutil.Process")
    def test_is_running_access_denied(self, MockProcess):
        import psutil as _psutil
        MockProcess.side_effect = _psutil.AccessDenied(999)

        pi = ProcessInfo(pid=999, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")
        self.assertFalse(pi.is_running)


# ═══════════════════════════════════════════════════════════════════════════
# BrowserSessionResult tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserSessionResult(unittest.TestCase):
    def _make_proc(self, pid: int) -> ProcessInfo:
        return ProcessInfo(pid=pid, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")

    def test_running_count_empty(self):
        r = BrowserSessionResult(browser_name="Chrome")
        self.assertEqual(r.running_count, 0)

    def test_running_count_with_processes(self):
        procs = [self._make_proc(1), self._make_proc(2)]
        r = BrowserSessionResult(browser_name="Chrome", processes=procs)
        self.assertEqual(r.running_count, 2)

    def test_is_running_true(self):
        procs = [self._make_proc(1)]
        r = BrowserSessionResult(browser_name="Chrome", processes=procs)
        self.assertTrue(r.is_running)

    def test_is_running_false(self):
        r = BrowserSessionResult(browser_name="Chrome", processes=[])
        self.assertFalse(r.is_running)

    def test_pids(self):
        procs = [self._make_proc(100), self._make_proc(200)]
        r = BrowserSessionResult(browser_name="Chrome", processes=procs)
        self.assertEqual(r.pids, [100, 200])

    def test_pids_empty(self):
        r = BrowserSessionResult(browser_name="Chrome")
        self.assertEqual(r.pids, [])

    def test_error_field(self):
        r = BrowserSessionResult(browser_name="Chrome", error="Permission denied")
        self.assertEqual(r.error, "Permission denied")


# ═══════════════════════════════════════════════════════════════════════════
# _iter_processes tests
# ═══════════════════════════════════════════════════════════════════════════

class TestIterProcesses(unittest.TestCase):
    @patch("browser.browser_sessions.psutil.process_iter")
    def test_returns_list(self, mock_iter):
        mock_iter.return_value = []
        result = _iter_processes()
        self.assertIsInstance(result, list)

    @patch("browser.browser_sessions.psutil.process_iter")
    def test_returns_empty_on_error(self, mock_iter):
        mock_iter.side_effect = psutil.Error("test error")
        result = _iter_processes()
        self.assertEqual(result, [])


# ═══════════════════════════════════════════════════════════════════════════
# _match_process_to_browser tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMatchProcessToBrowser(unittest.TestCase):
    def _make_proc(self, name: str) -> MagicMock:
        mock = MagicMock()
        mock.info = {"name": name}
        return mock

    def test_match_chrome(self):
        exe_map = {"Chrome": ["chrome.exe"], "Brave": ["brave.exe"]}
        proc = self._make_proc("chrome.exe")
        result = _match_process_to_browser(proc, exe_map)
        self.assertEqual(result, "Chrome")

    def test_match_brave(self):
        exe_map = {"Chrome": ["chrome.exe"], "Brave": ["brave.exe"]}
        proc = self._make_proc("brave.exe")
        result = _match_process_to_browser(proc, exe_map)
        self.assertEqual(result, "Brave")

    def test_match_case_insensitive(self):
        exe_map = {"Chrome": ["chrome.exe"]}
        proc = self._make_proc("CHROME.EXE")
        result = _match_process_to_browser(proc, exe_map)
        self.assertEqual(result, "Chrome")

    def test_no_match(self):
        exe_map = {"Chrome": ["chrome.exe"]}
        proc = self._make_proc("firefox.exe")
        result = _match_process_to_browser(proc, exe_map)
        self.assertIsNone(result)

    def test_empty_name(self):
        exe_map = {"Chrome": ["chrome.exe"]}
        proc = self._make_proc("")
        result = _match_process_to_browser(proc, exe_map)
        self.assertIsNone(result)

    def test_access_denied(self):
        import psutil as _psutil
        exe_map = {"Chrome": ["chrome.exe"]}
        proc = MagicMock()
        # Make info.get raise AccessDenied by making proc.info a custom object
        class _BadInfo:
            def get(self, key, default=None):
                raise _psutil.AccessDenied(1)
        proc.info = _BadInfo()
        result = _match_process_to_browser(proc, exe_map)
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════════
# _build_exe_names_map tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildExeNamesMap(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_default_browsers(self):
        result = _build_exe_names_map()
        self.assertIn("Chrome", result)
        self.assertIn("Brave", result)
        self.assertIn("Edge", result)
        self.assertIn("chrome.exe", result["Chrome"])

    def test_custom_registry(self):
        reg = BrowserRegistry.instance()
        custom = BrowserDefinition(name="Opera", exe_names=["opera.exe"])
        reg.register(custom)
        result = _build_exe_names_map(reg)
        self.assertIn("Opera", result)
        self.assertIn("opera.exe", result["Opera"])


# ═══════════════════════════════════════════════════════════════════════════
# _safe_process_info tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSafeProcessInfo(unittest.TestCase):
    def test_success(self):
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_proc.info = {"name": "chrome.exe"}
        mock_proc.exe.return_value = "C:\\chrome.exe"
        result = _safe_process_info(mock_proc, "Chrome", "chrome.exe")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.pid, 1234)
        self.assertEqual(result.browser_name, "Chrome")

    def test_no_such_process_on_pid(self):
        import psutil as _psutil
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        # Make info.get raise to simulate dead process
        class _BadInfo:
            def get(self, key, default=None):
                raise _psutil.NoSuchProcess(1234)
        mock_proc.info = _BadInfo()
        result = _safe_process_info(mock_proc, "Chrome", "chrome.exe")
        self.assertIsNone(result)

    def test_exe_access_denied(self):
        import psutil as _psutil
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_proc.info = {"name": "chrome.exe"}
        mock_proc.exe.side_effect = _psutil.AccessDenied(1234)
        result = _safe_process_info(mock_proc, "Chrome", "chrome.exe")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.exe_path, "")


# ═══════════════════════════════════════════════════════════════════════════
# _scan_all tests
# ═══════════════════════════════════════════════════════════════════════════

class TestScanAll(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_empty_process_list(self):
        with patch("browser.browser_sessions._iter_processes", return_value=[]):
            result = _scan_all()
            self.assertEqual(len(result), 0)

    def test_groups_by_browser(self):
        def _make_mock_proc(pid: int, name: str) -> MagicMock:
            mock = MagicMock()
            mock.pid = pid
            mock.info = {"name": name}
            mock.exe.return_value = f"C:\\{name}"
            return mock

        procs = [
            _make_mock_proc(1, "chrome.exe"),
            _make_mock_proc(2, "chrome.exe"),
            _make_mock_proc(3, "brave.exe"),
        ]

        with patch("browser.browser_sessions._iter_processes", return_value=procs):
            result = _scan_all()
            self.assertIn("Chrome", result)
            self.assertIn("Brave", result)
            self.assertEqual(len(result["Chrome"]), 2)
            self.assertEqual(len(result["Brave"]), 1)

    def test_skips_unmatched(self):
        def _make_mock_proc(pid: int, name: str) -> MagicMock:
            mock = MagicMock()
            mock.pid = pid
            mock.info = {"name": name}
            mock.exe.return_value = f"C:\\{name}"
            return mock

        procs = [
            _make_mock_proc(1, "chrome.exe"),
            _make_mock_proc(2, "firefox.exe"),
        ]

        with patch("browser.browser_sessions._iter_processes", return_value=procs):
            result = _scan_all()
            self.assertIn("Chrome", result)
            self.assertNotIn("Firefox", result)


# ═══════════════════════════════════════════════════════════════════════════
# BrowserSessionManager — running
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserSessionManagerRunning(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_running_returns_list(self):
        with patch("browser.browser_sessions._scan_all", return_value={
            "Chrome": [ProcessInfo(pid=1, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")],
        }):
            result = BrowserSessionManager.running("Chrome")
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].browser_name, "Chrome")

    def test_running_not_found(self):
        with patch("browser.browser_sessions._scan_all", return_value={}):
            result = BrowserSessionManager.running("Chrome")
            self.assertEqual(len(result), 0)

    def test_running_unknown_browser(self):
        with patch("browser.browser_sessions._scan_all", return_value={}):
            result = BrowserSessionManager.running("Firefox")
            self.assertEqual(len(result), 0)


# ═══════════════════════════════════════════════════════════════════════════
# BrowserSessionManager — running_all
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserSessionManagerRunningAll(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_running_all_returns_dict(self):
        with patch("browser.browser_sessions._scan_all", return_value={
            "Chrome": [ProcessInfo(pid=1, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")],
            "Edge": [ProcessInfo(pid=2, name="msedge.exe", exe_path="", browser_name="Edge", browser_exe="msedge.exe")],
        }):
            result = BrowserSessionManager.running_all()
            self.assertEqual(len(result), 2)
            self.assertIn("Chrome", result)
            self.assertIn("Edge", result)

    def test_running_all_empty(self):
        with patch("browser.browser_sessions._scan_all", return_value={}):
            result = BrowserSessionManager.running_all()
            self.assertEqual(len(result), 0)


# ═══════════════════════════════════════════════════════════════════════════
# BrowserSessionManager — find
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserSessionManagerFind(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_find_returns_session_result(self):
        proc = ProcessInfo(pid=100, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")
        with patch("browser.browser_sessions._scan_all", return_value={"Chrome": [proc]}):
            result = BrowserSessionManager.find("Chrome")
            self.assertEqual(result.browser_name, "Chrome")
            self.assertTrue(result.is_running)
            self.assertEqual(result.running_count, 1)

    def test_find_not_running(self):
        with patch("browser.browser_sessions._scan_all", return_value={}):
            result = BrowserSessionManager.find("Chrome")
            self.assertEqual(result.browser_name, "Chrome")
            self.assertFalse(result.is_running)

    def test_find_unknown_browser(self):
        result = BrowserSessionManager.find("Firefox")
        self.assertIn("Unknown browser", result.error)


# ═══════════════════════════════════════════════════════════════════════════
# BrowserSessionManager — count
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserSessionManagerCount(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_count_returns_int(self):
        procs = [
            ProcessInfo(pid=i, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")
            for i in range(5)
        ]
        with patch("browser.browser_sessions._scan_all", return_value={"Chrome": procs}):
            self.assertEqual(BrowserSessionManager.count("Chrome"), 5)

    def test_count_zero(self):
        with patch("browser.browser_sessions._scan_all", return_value={}):
            self.assertEqual(BrowserSessionManager.count("Chrome"), 0)

    def test_count_unknown_browser(self):
        self.assertEqual(BrowserSessionManager.count("Firefox"), 0)


# ═══════════════════════════════════════════════════════════════════════════
# BrowserSessionManager — has_running
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserSessionManagerHasRunning(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_has_running_true(self):
        proc = ProcessInfo(pid=1, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")
        with patch("browser.browser_sessions._scan_all", return_value={"Chrome": [proc]}):
            self.assertTrue(BrowserSessionManager.has_running("Chrome"))

    def test_has_running_false(self):
        with patch("browser.browser_sessions._scan_all", return_value={}):
            self.assertFalse(BrowserSessionManager.has_running("Chrome"))

    def test_has_running_unknown(self):
        self.assertFalse(BrowserSessionManager.has_running("Firefox"))


# ═══════════════════════════════════════════════════════════════════════════
# BrowserSessionManager — with BrowserDefinition input
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserSessionManagerWithDefinition(unittest.TestCase):
    """Tests that accept BrowserDefinition objects instead of name strings."""

    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_running_with_definition(self):
        defn = BrowserDefinition(name="Chrome", exe_names=["chrome.exe"])
        proc = ProcessInfo(pid=1, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")
        with patch("browser.browser_sessions._scan_all", return_value={"Chrome": [proc]}):
            result = BrowserSessionManager.running(defn)
            self.assertEqual(len(result), 1)

    def test_find_with_definition(self):
        defn = BrowserDefinition(name="Brave", exe_names=["brave.exe"])
        with patch("browser.browser_sessions._scan_all", return_value={}):
            result = BrowserSessionManager.find(defn)
            self.assertEqual(result.browser_name, "Brave")

    def test_count_with_definition(self):
        defn = BrowserDefinition(name="Edge", exe_names=["msedge.exe"])
        with patch("browser.browser_sessions._scan_all", return_value={}):
            self.assertEqual(BrowserSessionManager.count(defn), 0)

    def test_has_running_with_definition(self):
        defn = BrowserDefinition(name="Chrome", exe_names=["chrome.exe"])
        with patch("browser.browser_sessions._scan_all", return_value={}):
            self.assertFalse(BrowserSessionManager.has_running(defn))


# ═══════════════════════════════════════════════════════════════════════════
# BrowserSessionManager — resolve edge cases
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserSessionManagerResolve(unittest.TestCase):
    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_resolve_with_empty_string(self):
        result = BrowserSessionManager.find("")
        self.assertIn("Unknown browser", result.error)

    def test_resolve_with_whitespace(self):
        result = BrowserSessionManager.find("   ")
        self.assertIn("Unknown browser", result.error)

    def test_resolve_with_definition_passthrough(self):
        defn = BrowserDefinition(name="X", exe_names=["x.exe"])
        resolved = BrowserSessionManager._resolve_definition(defn)
        self.assertIs(resolved, defn)

    def test_resolve_registered_name(self):
        reg = BrowserRegistry.instance()
        custom = BrowserDefinition(name="Vivaldi", exe_names=["vivaldi.exe"])
        reg.register(custom)
        resolved = BrowserSessionManager._resolve_definition("Vivaldi")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.name, "Vivaldi")


# ═══════════════════════════════════════════════════════════════════════════
# Thread-safety smoke test
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowserSessionManagerThreadSafety(unittest.TestCase):
    """Verify that concurrent calls do not crash."""

    def setUp(self):
        BrowserRegistry.reset()

    def tearDown(self):
        BrowserRegistry.reset()

    def test_concurrent_find(self):
        procs = [
            ProcessInfo(pid=i, name="chrome.exe", exe_path="", browser_name="Chrome", browser_exe="chrome.exe")
            for i in range(3)
        ]
        with patch("browser.browser_sessions._scan_all", return_value={"Chrome": procs}):
            results: list[BrowserSessionResult] = []
            errors: list[Exception] = []

            def _worker():
                try:
                    r = BrowserSessionManager.find("Chrome")
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
                self.assertEqual(r.running_count, 3)


# ═══════════════════════════════════════════════════════════════════════════
# Package import tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPackageImports(unittest.TestCase):
    def test_import_from_browser_package(self):
        from browser import BrowserSessionManager, BrowserSessionResult, ProcessInfo
        self.assertIsNotNone(BrowserSessionManager)
        self.assertIsNotNone(BrowserSessionResult)
        self.assertIsNotNone(ProcessInfo)

    def test_import_from_browser_sessions_module(self):
        from browser.browser_sessions import (
            BrowserSessionManager,
            BrowserSessionResult,
            ProcessInfo,
        )
        self.assertIsNotNone(BrowserSessionManager)


if __name__ == "__main__":
    unittest.main()
