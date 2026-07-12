"""
test_exit_flow.py — Tests for CompanionWindow._on_close_request exit flow.

Covers:
  - Close → Yes → Invalid Folder → Exit aborted, switches to Settings
  - Close → Yes → Valid Settings → Exit proceeds normally
  - Close → Cancel → Exit cancelled
  - Close → No (Discard) → Exit without saving
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Module stubs (same pattern as test_settings.py)
# ---------------------------------------------------------------------------

# Stub base_page module
_mock_base_mod = types.ModuleType("base_page")


class _FakeBasePage:
    def __init__(self, master, manager, logger):
        self.master = master
        self.manager = manager
        self.logger = logger


_mock_base_mod.BasePage = _FakeBasePage

# Stub customtkinter module
ctk_mock = MagicMock()
ctk_mock.StringVar = MagicMock(side_effect=lambda: MagicMock())
ctk_mock.BooleanVar = MagicMock(side_effect=lambda: MagicMock())


class _FakeCTk:
    pass


ctk_mock.CTk = _FakeCTk

_saved_modules: dict[str, object] = {}
# Save modules that will be imported under mock context
for _mod_name in ("ui", "extension_manager", "extension_manager_page"):
    _saved_modules[_mod_name] = sys.modules.get(_mod_name)

for _mod_name, _mod_stub in (
    ("base_page", _mock_base_mod),
    ("customtkinter", ctk_mock),
    ("backend_manager", MagicMock()),
    ("notifications", MagicMock()),
    ("updater", MagicMock()),
    ("installer", MagicMock()),
    ("logger", MagicMock()),
    ("dashboard", MagicMock()),
    ("queue_panel", MagicMock()),
    ("history_panel", MagicMock()),
    ("stats_panel", MagicMock()),
    ("scheduler", MagicMock()),
    ("scheduler_panel", MagicMock()),
):
    _saved_modules[_mod_name] = sys.modules.get(_mod_name)
    sys.modules[_mod_name] = _mod_stub

try:
    from ui import CompanionWindow
finally:
    for _mod_name, _orig in _saved_modules.items():
        if _orig is not None:
            sys.modules[_mod_name] = _orig
        else:
            sys.modules.pop(_mod_name, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exit_window() -> tuple[MagicMock, MagicMock]:
    settings_page = MagicMock()
    window = MagicMock()
    window.updater = None
    window._pages = {"Settings": settings_page}
    window._current_page_name = "Queue"
    window.tray_active = False
    window.logger = MagicMock()
    window.show_page = MagicMock()
    window.withdraw = MagicMock()
    window._show_shutdown_dialog = MagicMock()
    window._confirm_install_exit = MagicMock()
    return window, settings_page


# ===========================================================================
# Exit Flow — Unsaved Changes → Yes → Validation Fails
# ===========================================================================

class TestExitFlowValidationFails(unittest.TestCase):
    """Close → Yes → Invalid Folder → Application remains open."""

    def setUp(self):
        self.window, self.settings_page = _make_exit_window()
        self.settings_page.is_dirty.return_value = True
        self.settings_page.save_settings.return_value = False

    @patch("tkinter.messagebox.askyesnocancel")
    def test_exit_cancelled_when_validation_fails(self, mock_ask):
        """Application remains open after validation failure on exit."""
        mock_ask.return_value = True
        CompanionWindow._on_close_request(self.window)
        self.settings_page.save_settings.assert_called_once()
        self.window.withdraw.assert_not_called()
        self.window._show_shutdown_dialog.assert_not_called()

    @patch("tkinter.messagebox.askyesnocancel")
    def test_switches_to_settings_when_on_different_page(self, mock_ask):
        """Automatically switches sidebar to Settings when current page is different."""
        mock_ask.return_value = True
        CompanionWindow._on_close_request(self.window)
        self.window.show_page.assert_called_once_with("Settings")

    @patch("tkinter.messagebox.askyesnocancel")
    def test_does_not_switch_when_already_on_settings(self, mock_ask):
        """No page switch needed when already on the Settings page."""
        mock_ask.return_value = True
        self.window._current_page_name = "Settings"
        CompanionWindow._on_close_request(self.window)
        self.window.show_page.assert_not_called()

    @patch("tkinter.messagebox.askyesnocancel")
    def test_logs_exit_cancelled(self, mock_ask):
        """Event is logged when exit is cancelled due to validation failure."""
        mock_ask.return_value = True
        CompanionWindow._on_close_request(self.window)
        self.window.logger.info.assert_called_with(
            "[Settings] Exit cancelled because validation failed."
        )

    @patch("tkinter.messagebox.askyesnocancel")
    def test_schedules_rehighlight(self, mock_ask):
        """Schedules _rehighlight_last_failed after switching to Settings."""
        mock_ask.return_value = True
        CompanionWindow._on_close_request(self.window)
        self.window.show_page.assert_called_once_with("Settings")
        self.window.after.assert_called_once()
        args, _ = self.window.after.call_args
        self.assertEqual(args[0], 50)
        self.assertTrue(callable(args[1]))

    @patch("tkinter.messagebox.askyesnocancel")
    def test_no_additional_unsaved_changes_dialog(self, mock_ask):
        """Only one unsaved changes dialog is shown (no second prompt)."""
        mock_ask.return_value = True
        CompanionWindow._on_close_request(self.window)
        mock_ask.assert_called_once()

    @patch("tkinter.messagebox.askyesnocancel")
    def test_already_on_settings_skips_switch_but_still_aborts(self, mock_ask):
        """When already on Settings, exit is still aborted without page switch."""
        mock_ask.return_value = True
        self.window._current_page_name = "Settings"
        CompanionWindow._on_close_request(self.window)
        self.window.show_page.assert_not_called()
        self.window.withdraw.assert_not_called()
        self.window._show_shutdown_dialog.assert_not_called()


# ===========================================================================
# Exit Flow — Unsaved Changes → Yes → Validation Succeeds
# ===========================================================================

class TestExitFlowValidationSucceeds(unittest.TestCase):
    """Close → Yes → Valid Settings → Application exits normally."""

    def setUp(self):
        self.window, self.settings_page = _make_exit_window()
        self.settings_page.is_dirty.return_value = True
        self.settings_page.save_settings.return_value = True

    @patch("tkinter.messagebox.askyesnocancel")
    def test_exit_continues_with_shutdown_dialog(self, mock_ask):
        """Exit proceeds to shutdown dialog when save succeeds."""
        mock_ask.return_value = True
        self.window.tray_active = False
        CompanionWindow._on_close_request(self.window)
        self.settings_page.save_settings.assert_called_once()
        self.window._show_shutdown_dialog.assert_called_once()
        self.window.withdraw.assert_not_called()

    @patch("tkinter.messagebox.askyesnocancel")
    def test_exit_hides_to_tray_when_tray_active(self, mock_ask):
        """Window hides to tray instead of showing shutdown dialog when tray is active."""
        mock_ask.return_value = True
        self.window.tray_active = True
        CompanionWindow._on_close_request(self.window)
        self.window.withdraw.assert_called_once()
        self.window._show_shutdown_dialog.assert_not_called()

    @patch("tkinter.messagebox.askyesnocancel")
    def test_no_exit_cancelled_log_when_save_succeeds(self, mock_ask):
        """No 'exit cancelled' log when save succeeds."""
        mock_ask.return_value = True
        CompanionWindow._on_close_request(self.window)
        self.window.logger.info.assert_not_called()

    @patch("tkinter.messagebox.askyesnocancel")
    def test_no_page_switch_when_save_succeeds(self, mock_ask):
        """No page switch when save succeeds and exit continues."""
        mock_ask.return_value = True
        CompanionWindow._on_close_request(self.window)
        self.window.show_page.assert_not_called()


# ===========================================================================
# Exit Flow — Unsaved Changes → Cancel
# ===========================================================================

class TestExitFlowCancel(unittest.TestCase):
    """Close → Cancel → Exit cancelled, no save attempted."""

    def setUp(self):
        self.window, self.settings_page = _make_exit_window()
        self.settings_page.is_dirty.return_value = True

    @patch("tkinter.messagebox.askyesnocancel")
    def test_exit_cancelled_when_user_clicks_cancel(self, mock_ask):
        mock_ask.return_value = None
        CompanionWindow._on_close_request(self.window)
        self.settings_page.save_settings.assert_not_called()
        self.window.withdraw.assert_not_called()
        self.window._show_shutdown_dialog.assert_not_called()

    @patch("tkinter.messagebox.askyesnocancel")
    def test_no_switch_to_settings_on_cancel(self, mock_ask):
        mock_ask.return_value = None
        CompanionWindow._on_close_request(self.window)
        self.window.show_page.assert_not_called()

    @patch("tkinter.messagebox.askyesnocancel")
    def test_logger_not_called_on_cancel(self, mock_ask):
        mock_ask.return_value = None
        CompanionWindow._on_close_request(self.window)
        self.window.logger.info.assert_not_called()


# ===========================================================================
# Exit Flow — Unsaved Changes → No (Discard)
# ===========================================================================

class TestExitFlowDiscard(unittest.TestCase):
    """Close → No → Exit proceeds without saving."""

    def setUp(self):
        self.window, self.settings_page = _make_exit_window()
        self.settings_page.is_dirty.return_value = True
        self.settings_page.save_settings.return_value = True

    @patch("tkinter.messagebox.askyesnocancel")
    def test_exit_proceeds_without_saving(self, mock_ask):
        mock_ask.return_value = False
        CompanionWindow._on_close_request(self.window)
        self.settings_page.save_settings.assert_not_called()

    @patch("tkinter.messagebox.askyesnocancel")
    def test_exit_shows_shutdown_dialog_after_discard(self, mock_ask):
        mock_ask.return_value = False
        self.window.tray_active = False
        CompanionWindow._on_close_request(self.window)
        self.window._show_shutdown_dialog.assert_called_once()

    @patch("tkinter.messagebox.askyesnocancel")
    def test_exit_hides_to_tray_after_discard_when_tray_active(self, mock_ask):
        mock_ask.return_value = False
        self.window.tray_active = True
        CompanionWindow._on_close_request(self.window)
        self.window.withdraw.assert_called_once()
        self.window._show_shutdown_dialog.assert_not_called()


# ===========================================================================
# Exit Flow — No Unsaved Changes
# ===========================================================================

class TestExitFlowNoUnsavedChanges(unittest.TestCase):
    """Close without unsaved changes → Exit proceeds directly."""

    def setUp(self):
        self.window, self.settings_page = _make_exit_window()
        self.settings_page.is_dirty.return_value = False

    @patch("tkinter.messagebox.askyesnocancel")
    def test_exit_proceeds_when_no_unsaved_changes(self, mock_ask):
        CompanionWindow._on_close_request(self.window)
        mock_ask.assert_not_called()
        self.window._show_shutdown_dialog.assert_called_once()


if __name__ == "__main__":
    unittest.main()
