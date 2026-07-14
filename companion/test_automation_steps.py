"""
test_automation_steps.py — Tests for Phase 3.2 step handlers and pipeline.

Covers:
  - step_handlers: detect_browser, validate_extension, launch_browser,
    detect_profiles, detect_running, verify_installation
  - automation_steps: AutomationSteps pipeline, StepDescriptor, execute,
    cancellation, error propagation
  - integration: full pipeline via BrowserAutomationEngine.run()
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure companion/ is importable
_COMPANION = os.path.dirname(os.path.abspath(__file__))
if _COMPANION not in sys.path:
    sys.path.insert(0, _COMPANION)

from browser.automation_defs import AutomationState, AutomationErrorCode
from browser.automation_errors import AutomationError, make_error, cancelled_error
from browser.automation_session import AutomationSession
from browser.automation_steps import AutomationSteps, StepDescriptor
from browser.step_handlers import (
    detect_browser,
    detect_profiles,
    detect_running,
    launch_browser,
    validate_extension,
    verify_installation,
)
from browser.browser_info import BrowserInfo, LaunchResult, LaunchErrorCode
from browser.browser_profiles import BrowserScanResult, ProfileMetadata
from browser.browser_sessions import BrowserSessionResult, ProcessInfo
from browser.browser_extension_installer import (
    ExtensionValidationResult,
    ExtensionErrorCode,
)


# ═══════════════════════════════════════════════════════════════════════════
# StepDescriptor tests
# ═══════════════════════════════════════════════════════════════════════════

class TestStepDescriptor(unittest.TestCase):
    def test_frozen_dataclass(self):
        desc = StepDescriptor(
            name="test",
            handler=lambda s: None,
            state=AutomationState.PENDING,
            description="A test step",
        )
        self.assertEqual(desc.name, "test")
        self.assertEqual(desc.state, AutomationState.PENDING)
        self.assertEqual(desc.description, "A test step")

    def test_immutability(self):
        desc = StepDescriptor(
            name="x",
            handler=lambda s: None,
            state=AutomationState.RUNNING,
        )
        with self.assertRaises(AttributeError):
            desc.name = "y"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════
# detect_browser step handler tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDetectBrowser(unittest.TestCase):
    def test_empty_browser_name_returns_error(self):
        session = AutomationSession(browser_name="")
        error = detect_browser(session)
        self.assertIsNotNone(error)
        self.assertEqual(error.code, AutomationErrorCode.BROWSER_NOT_FOUND)

    def test_browser_not_found_returns_error(self):
        session = AutomationSession(browser_name="NonexistentBrowser")
        with patch("browser.step_handlers.BrowserLauncher.detect_by_name") as mock:
            mock.return_value = None
            error = detect_browser(session)
            self.assertIsNotNone(error)
            self.assertEqual(error.code, AutomationErrorCode.BROWSER_NOT_FOUND)

    def test_browser_found_stores_info(self):
        session = AutomationSession(browser_name="Chrome")
        info = BrowserInfo(name="Chrome", installed=True, path="/fake/chrome.exe")
        with patch("browser.step_handlers.BrowserLauncher.detect_by_name") as mock:
            mock.return_value = info
            error = detect_browser(session)
            self.assertIsNone(error)
            self.assertEqual(session._detected_browser_info.name, "Chrome")

    def test_browser_not_installed_returns_error(self):
        session = AutomationSession(browser_name="Chrome")
        info = BrowserInfo(name="Chrome", installed=False)
        with patch("browser.step_handlers.BrowserLauncher.detect_by_name") as mock:
            mock.return_value = info
            error = detect_browser(session)
            self.assertIsNotNone(error)
            self.assertEqual(error.code, AutomationErrorCode.BROWSER_NOT_FOUND)

    def test_exception_returns_error(self):
        session = AutomationSession(browser_name="Chrome")
        with patch("browser.step_handlers.BrowserLauncher.detect_by_name") as mock:
            mock.side_effect = OSError("disk error")
            error = detect_browser(session)
            self.assertIsNotNone(error)
            self.assertIn("disk error", error.message)


# ═══════════════════════════════════════════════════════════════════════════
# validate_extension step handler tests
# ═══════════════════════════════════════════════════════════════════════════

class TestValidateExtension(unittest.TestCase):
    def test_empty_extension_dir_returns_error(self):
        session = AutomationSession(extension_dir="")
        error = validate_extension(session)
        self.assertIsNotNone(error)
        self.assertEqual(error.code, AutomationErrorCode.EXTENSION_MISSING)

    def test_valid_extension_returns_none(self):
        session = AutomationSession(extension_dir="/fake/ext")
        result = ExtensionValidationResult(valid=True)
        with patch("browser.step_handlers.ExtensionInstallationEngine.validate_extension") as mock:
            mock.return_value = result
            error = validate_extension(session)
            self.assertIsNone(error)
            self.assertTrue(session._extension_validation.valid)

    def test_invalid_extension_returns_error(self):
        session = AutomationSession(extension_dir="/fake/ext")
        result = ExtensionValidationResult(
            valid=False,
            missing_files=["icon.png"],
            error_code=ExtensionErrorCode.REQUIRED_FILES_MISSING,
        )
        with patch("browser.step_handlers.ExtensionInstallationEngine.validate_extension") as mock:
            mock.return_value = result
            error = validate_extension(session)
            self.assertIsNotNone(error)
            self.assertIn("icon.png", error.message)

    def test_exception_returns_error(self):
        session = AutomationSession(extension_dir="/fake/ext")
        with patch("browser.step_handlers.ExtensionInstallationEngine.validate_extension") as mock:
            mock.side_effect = RuntimeError("fs crash")
            error = validate_extension(session)
            self.assertIsNotNone(error)
            self.assertIn("fs crash", error.message)


# ═══════════════════════════════════════════════════════════════════════════
# launch_browser step handler tests
# ═══════════════════════════════════════════════════════════════════════════

class TestLaunchBrowser(unittest.TestCase):
    def test_no_detected_browser_returns_error(self):
        session = AutomationSession(browser_name="Chrome")
        error = launch_browser(session)
        self.assertIsNotNone(error)
        self.assertEqual(error.code, AutomationErrorCode.BROWSER_NOT_FOUND)

    def test_browser_not_installed_returns_error(self):
        session = AutomationSession(browser_name="Chrome")
        session._detected_browser_info = BrowserInfo(name="Chrome", installed=False)
        error = launch_browser(session)
        self.assertIsNotNone(error)
        self.assertEqual(error.code, AutomationErrorCode.BROWSER_NOT_FOUND)

    def test_successful_launch_returns_none(self):
        session = AutomationSession(browser_name="Chrome", extension_dir="/ext")
        session._detected_browser_info = BrowserInfo(
            name="Chrome", installed=True, path="/chrome.exe",
        )
        launch_result = LaunchResult(success=True, pid=1234)
        with patch("browser.step_handlers.BrowserLauncher.launch") as mock:
            mock.return_value = launch_result
            error = launch_browser(session)
            self.assertIsNone(error)
            self.assertEqual(session._launch_result.pid, 1234)

    def test_failed_launch_returns_error(self):
        session = AutomationSession(browser_name="Chrome", extension_dir="/ext")
        session._detected_browser_info = BrowserInfo(
            name="Chrome", installed=True, path="/chrome.exe",
        )
        launch_result = LaunchResult(
            success=False,
            error_code=LaunchErrorCode.PERMISSION_DENIED,
            error_message="access denied",
        )
        with patch("browser.step_handlers.BrowserLauncher.launch") as mock:
            mock.return_value = launch_result
            error = launch_browser(session)
            self.assertIsNotNone(error)
            self.assertEqual(error.code, AutomationErrorCode.LAUNCH_FAILED)

    def test_extension_args_passed(self):
        session = AutomationSession(browser_name="Chrome", extension_dir="/my/ext")
        session._detected_browser_info = BrowserInfo(
            name="Chrome", installed=True, path="/chrome.exe",
        )
        with patch("browser.step_handlers.BrowserLauncher.launch") as mock:
            mock.return_value = LaunchResult(success=True, pid=1)
            launch_browser(session)
            call_args = mock.call_args
            self.assertIn("--load-extension", call_args.kwargs.get("args", call_args[1].get("args", [])))

    def test_exception_returns_error(self):
        session = AutomationSession(browser_name="Chrome", extension_dir="/ext")
        session._detected_browser_info = BrowserInfo(
            name="Chrome", installed=True, path="/chrome.exe",
        )
        with patch("browser.step_handlers.BrowserLauncher.launch") as mock:
            mock.side_effect = OSError("spawn failed")
            error = launch_browser(session)
            self.assertIsNotNone(error)
            self.assertEqual(error.code, AutomationErrorCode.LAUNCH_FAILED)


# ═══════════════════════════════════════════════════════════════════════════
# detect_profiles step handler tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDetectProfiles(unittest.TestCase):
    def test_successful_scan_stores_result(self):
        session = AutomationSession(browser_name="Chrome")
        scan = BrowserScanResult(
            browser_name="Chrome",
            user_data_dir="/data",
            user_data_dir_exists=True,
            profiles=[ProfileMetadata(name="Default", path="/data/Default", preferences_path="/data/Default/Preferences", preferences_exists=True, is_default=True)],
        )
        with patch("browser.step_handlers.BrowserProfileManager.scan") as mock:
            mock.return_value = scan
            error = detect_profiles(session)
            self.assertIsNone(error)
            self.assertEqual(session._profile_scan_result.profile_count, 1)

    def test_exception_stores_none_non_fatal(self):
        session = AutomationSession(browser_name="Chrome")
        with patch("browser.step_handlers.BrowserProfileManager.scan") as mock:
            mock.side_effect = OSError("perm")
            error = detect_profiles(session)
            self.assertIsNone(error)
            self.assertIsNone(session._profile_scan_result)

    def test_always_returns_none(self):
        session = AutomationSession(browser_name="Chrome")
        scan = BrowserScanResult(
            browser_name="Chrome",
            user_data_dir="/data",
            user_data_dir_exists=False,
        )
        with patch("browser.step_handlers.BrowserProfileManager.scan") as mock:
            mock.return_value = scan
            error = detect_profiles(session)
            self.assertIsNone(error)


# ═══════════════════════════════════════════════════════════════════════════
# detect_running step handler tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDetectRunning(unittest.TestCase):
    def test_successful_scan_stores_result(self):
        session = AutomationSession(browser_name="Chrome")
        session_result = BrowserSessionResult(
            browser_name="Chrome",
            processes=[ProcessInfo(pid=100, name="chrome.exe", exe_path="/chrome.exe", browser_name="Chrome", browser_exe="chrome.exe")],
        )
        with patch("browser.step_handlers.BrowserSessionManager.find") as mock:
            mock.return_value = session_result
            error = detect_running(session)
            self.assertIsNone(error)
            self.assertTrue(session._session_scan_result.is_running)

    def test_exception_stores_none_non_fatal(self):
        session = AutomationSession(browser_name="Chrome")
        with patch("browser.step_handlers.BrowserSessionManager.find") as mock:
            mock.side_effect = OSError("psutil fail")
            error = detect_running(session)
            self.assertIsNone(error)
            self.assertIsNone(session._session_scan_result)

    def test_no_running_processes_stores_result(self):
        session = AutomationSession(browser_name="Chrome")
        session_result = BrowserSessionResult(browser_name="Chrome", processes=[])
        with patch("browser.step_handlers.BrowserSessionManager.find") as mock:
            mock.return_value = session_result
            error = detect_running(session)
            self.assertIsNone(error)
            self.assertFalse(session._session_scan_result.is_running)


# ═══════════════════════════════════════════════════════════════════════════
# verify_installation step handler tests
# ═══════════════════════════════════════════════════════════════════════════

class TestVerifyInstallation(unittest.TestCase):
    def test_empty_extension_dir_returns_error(self):
        session = AutomationSession(extension_dir="")
        error = verify_installation(session)
        self.assertIsNotNone(error)
        self.assertEqual(error.code, AutomationErrorCode.EXTENSION_MISSING)

    def test_installed_in_browser_returns_none(self):
        session = AutomationSession(extension_dir="/ext", browser_name="Chrome")
        with patch("browser.step_handlers.extension_manager") as mock_em:
            mock_em.detect_browser_registration.return_value = (
                [MagicMock(extension_registered=True)],
                True,
            )
            error = verify_installation(session)
            self.assertIsNone(error)
            self.assertTrue(session._installed_in_any_browser)

    def test_not_installed_returns_error(self):
        session = AutomationSession(extension_dir="/ext", browser_name="Chrome")
        with patch("browser.step_handlers.extension_manager") as mock_em:
            mock_em.detect_browser_registration.return_value = (
                [MagicMock(extension_registered=False)],
                False,
            )
            error = verify_installation(session)
            self.assertIsNotNone(error)
            self.assertEqual(error.code, AutomationErrorCode.EXTENSION_MISSING)

    def test_exception_returns_error(self):
        session = AutomationSession(extension_dir="/ext", browser_name="Chrome")
        with patch("browser.step_handlers.extension_manager") as mock_em:
            mock_em.detect_browser_registration.side_effect = RuntimeError("io error")
            error = verify_installation(session)
            self.assertIsNotNone(error)
            self.assertIn("io error", error.message)

    def test_detected_browser_passed_through(self):
        session = AutomationSession(extension_dir="/ext", browser_name="Chrome")
        info = BrowserInfo(name="Chrome", installed=True, path="/chrome.exe")
        session._detected_browser_info = info
        with patch("browser.step_handlers.extension_manager") as mock_em:
            mock_em.detect_browser_registration.return_value = ([], False)
            verify_installation(session)
            call_kwargs = mock_em.detect_browser_registration.call_args.kwargs
            self.assertEqual(len(call_kwargs["detected_browsers"]), 1)
            self.assertEqual(call_kwargs["detected_browsers"][0].name, "Chrome")


# ═══════════════════════════════════════════════════════════════════════════
# AutomationSteps pipeline tests
# ═══════════════════════════════════════════════════════════════════════════

class TestAutomationStepsInit(unittest.TestCase):
    def test_default_pipeline_step_count(self):
        steps = AutomationSteps()
        self.assertEqual(steps.step_count, 6)

    def test_default_pipeline_step_names(self):
        steps = AutomationSteps()
        expected = [
            "detect_browser",
            "validate_extension",
            "launch_browser",
            "detect_profiles",
            "detect_running",
            "verify_installation",
        ]
        self.assertEqual(steps.step_names, expected)

    def test_custom_pipeline(self):
        custom = [
            StepDescriptor(name="a", handler=lambda s: None, state=AutomationState.PENDING),
            StepDescriptor(name="b", handler=lambda s: None, state=AutomationState.RUNNING),
        ]
        steps = AutomationSteps(steps=custom)
        self.assertEqual(steps.step_count, 2)
        self.assertEqual(steps.step_names, ["a", "b"])

    def test_get_step_found(self):
        steps = AutomationSteps()
        step = steps.get_step("detect_browser")
        self.assertIsNotNone(step)
        self.assertEqual(step.name, "detect_browser")

    def test_get_step_not_found(self):
        steps = AutomationSteps()
        step = steps.get_step("nonexistent")
        self.assertIsNone(step)

    def test_str_repr(self):
        steps = AutomationSteps()
        self.assertIn("AutomationSteps", str(steps))
        self.assertIn("count=6", repr(steps))


class TestAutomationStepsAddRemove(unittest.TestCase):
    def test_add_step_to_end(self):
        steps = AutomationSteps(steps=[])
        steps.add_step("x", lambda s: None, AutomationState.PENDING)
        self.assertEqual(steps.step_count, 1)
        self.assertEqual(steps.step_names, ["x"])

    def test_add_step_at_index(self):
        steps = AutomationSteps(steps=[])
        steps.add_step("a", lambda s: None, AutomationState.PENDING)
        steps.add_step("b", lambda s: None, AutomationState.RUNNING)
        steps.add_step("mid", lambda s: None, AutomationState.LAUNCHING, index=1)
        self.assertEqual(steps.step_names, ["a", "mid", "b"])

    def test_remove_step(self):
        steps = AutomationSteps()
        self.assertTrue(steps.remove_step("detect_browser"))
        self.assertEqual(steps.step_count, 5)
        self.assertNotIn("detect_browser", steps.step_names)

    def test_remove_nonexistent_returns_false(self):
        steps = AutomationSteps()
        self.assertFalse(steps.remove_step("nonexistent"))

    def test_duplicate_names_allowed(self):
        steps = AutomationSteps(steps=[])
        steps.add_step("dup", lambda s: None, AutomationState.PENDING)
        steps.add_step("dup", lambda s: None, AutomationState.RUNNING)
        self.assertEqual(steps.step_count, 2)
        self.assertTrue(steps.remove_step("dup"))
        self.assertEqual(steps.step_count, 1)


# ═══════════════════════════════════════════════════════════════════════════
# AutomationSteps.execute tests
# ═══════════════════════════════════════════════════════════════════════════

class TestAutomationStepsExecute(unittest.TestCase):
    def test_all_steps_pass_returns_none(self):
        steps = AutomationSteps(steps=[
            StepDescriptor(name="s1", handler=lambda s: None, state=AutomationState.PENDING),
            StepDescriptor(name="s2", handler=lambda s: None, state=AutomationState.LAUNCHING),
            StepDescriptor(name="s3", handler=lambda s: None, state=AutomationState.RUNNING),
        ])
        session = AutomationSession(browser_name="Test")
        error = steps.execute(session)
        self.assertIsNone(error)

    def test_first_step_failure_stops_pipeline(self):
        call_log = []

        def failing_handler(s):
            call_log.append("fail")
            return make_error(AutomationErrorCode.BROWSER_NOT_FOUND, "not found")

        def should_not_run(s):
            call_log.append("should_not_run")
            return None

        steps = AutomationSteps(steps=[
            StepDescriptor(name="fail_step", handler=failing_handler, state=AutomationState.PENDING),
            StepDescriptor(name="next_step", handler=should_not_run, state=AutomationState.RUNNING),
        ])
        session = AutomationSession(browser_name="Test")
        error = steps.execute(session)
        self.assertIsNotNone(error)
        self.assertEqual(error.code, AutomationErrorCode.BROWSER_NOT_FOUND)
        self.assertEqual(call_log, ["fail"])

    def test_cancellation_before_step_returns_cancelled(self):
        steps = AutomationSteps(steps=[
            StepDescriptor(name="s1", handler=lambda s: None, state=AutomationState.PENDING),
        ])
        session = AutomationSession(browser_name="Test")
        session.request_cancellation()
        error = steps.execute(session)
        self.assertIsNotNone(error)
        self.assertEqual(error.code, AutomationErrorCode.CANCELLED)

    def test_handler_exception_returns_unknown_error(self):
        def exploding_handler(s):
            raise RuntimeError("kaboom")

        steps = AutomationSteps(steps=[
            StepDescriptor(name="boom", handler=exploding_handler, state=AutomationState.PENDING),
        ])
        session = AutomationSession(browser_name="Test")
        error = steps.execute(session)
        self.assertIsNotNone(error)
        self.assertEqual(error.code, AutomationErrorCode.UNKNOWN)
        self.assertIn("kaboom", error.message)

    def test_progress_callback_called_on_success(self):
        callbacks = []
        steps = AutomationSteps(steps=[
            StepDescriptor(name="s1", handler=lambda s: None, state=AutomationState.PENDING),
        ])
        session = AutomationSession(browser_name="Test")
        steps.execute(session, progress_callback=lambda s: callbacks.append(s.session_id))
        self.assertEqual(len(callbacks), 1)

    def test_progress_callback_error_does_not_break_pipeline(self):
        def bad_callback(s):
            raise RuntimeError("cb error")

        steps = AutomationSteps(steps=[
            StepDescriptor(name="s1", handler=lambda s: None, state=AutomationState.PENDING),
        ])
        session = AutomationSession(browser_name="Test")
        error = steps.execute(session, progress_callback=bad_callback)
        self.assertIsNone(error)

    def test_state_transitions_recorded(self):
        steps = AutomationSteps(steps=[
            StepDescriptor(name="s1", handler=lambda s: None, state=AutomationState.PENDING),
            StepDescriptor(name="s2", handler=lambda s: None, state=AutomationState.LAUNCHING),
            StepDescriptor(name="s3", handler=lambda s: None, state=AutomationState.RUNNING),
        ])
        session = AutomationSession(browser_name="Test")
        steps.execute(session)
        completed = session.steps_completed
        # PENDING is skipped (already the initial state), so only LAUNCHING and RUNNING are recorded
        self.assertNotIn(AutomationState.PENDING, completed)
        self.assertIn(AutomationState.LAUNCHING, completed)
        self.assertIn(AutomationState.RUNNING, completed)

    def test_invalid_state_transition_returns_error(self):
        steps = AutomationSteps(steps=[
            StepDescriptor(name="bad", handler=lambda s: None, state=AutomationState.COMPLETED),
        ])
        session = AutomationSession(browser_name="Test")
        error = steps.execute(session)
        self.assertIsNotNone(error)
        self.assertIn("State transition failed", error.message)

    def test_empty_pipeline_returns_none(self):
        steps = AutomationSteps(steps=[])
        session = AutomationSession(browser_name="Test")
        error = steps.execute(session)
        self.assertIsNone(error)


# ═══════════════════════════════════════════════════════════════════════════
# Integration: full engine run with mocked handlers
# ═══════════════════════════════════════════════════════════════════════════

class TestEngineIntegration(unittest.TestCase):
    def setUp(self):
        from browser.automation import reset_automation_engine
        reset_automation_engine()

    def tearDown(self):
        from browser.automation import reset_automation_engine
        reset_automation_engine()

    def test_engine_run_with_all_steps_passing(self):
        from browser.automation import get_automation_engine

        engine = get_automation_engine()

        # Replace all step handlers with no-ops
        noop_steps = AutomationSteps(steps=[
            StepDescriptor(name="s1", handler=lambda s: None, state=AutomationState.PENDING),
            StepDescriptor(name="s2", handler=lambda s: None, state=AutomationState.LAUNCHING),
            StepDescriptor(name="s3", handler=lambda s: None, state=AutomationState.RUNNING),
        ])
        engine._automation_steps = noop_steps

        result = engine.run(browser_name="Chrome")
        self.assertTrue(result.success)
        self.assertEqual(result.error_code, AutomationErrorCode.SUCCESS)

    def test_engine_run_with_failing_step(self):
        from browser.automation import get_automation_engine

        engine = get_automation_engine()

        fail_steps = AutomationSteps(steps=[
            StepDescriptor(
                name="fail",
                handler=lambda s: make_error(AutomationErrorCode.BROWSER_NOT_FOUND, "gone"),
                state=AutomationState.PENDING,
            ),
        ])
        engine._automation_steps = fail_steps

        result = engine.run(browser_name="Chrome")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, AutomationErrorCode.BROWSER_NOT_FOUND)

    def test_engine_run_async_with_steps(self):
        import time
        from browser.automation import get_automation_engine

        engine = get_automation_engine()

        noop_steps = AutomationSteps(steps=[
            StepDescriptor(name="s1", handler=lambda s: None, state=AutomationState.PENDING),
            StepDescriptor(name="s2", handler=lambda s: None, state=AutomationState.LAUNCHING),
            StepDescriptor(name="s3", handler=lambda s: None, state=AutomationState.RUNNING),
        ])
        engine._automation_steps = noop_steps

        session_id = engine.run_async(browser_name="Chrome")
        self.assertTrue(len(session_id) > 0)

        # Wait for completion
        time.sleep(0.5)
        result = engine.get_result(session_id)
        self.assertIsNotNone(result)
        self.assertTrue(result.success)

    def test_engine_cancel_during_pipeline(self):
        from browser.automation import get_automation_engine

        engine = get_automation_engine()
        event = __import__("threading").Event()

        def slow_handler(s):
            event.wait(timeout=2.0)
            return None

        slow_steps = AutomationSteps(steps=[
            StepDescriptor(name="slow", handler=slow_handler, state=AutomationState.PENDING),
        ])
        engine._automation_steps = slow_steps

        session_id = engine.run_async(browser_name="Chrome")
        time.sleep(0.1)  # Let it start
        engine.cancel(session_id)
        event.set()

        time.sleep(0.5)
        result = engine.get_result(session_id)
        self.assertIsNotNone(result)
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
