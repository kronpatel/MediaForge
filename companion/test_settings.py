"""
test_settings.py — Tests for SettingsPage save/exit validation flow.

Covers:
  - save_settings() returns bool (True=success, False=failure)
  - _validate_settings rejects invalid paths
  - Path normalization (~, relative paths)
  - Dirty flag reset after cancel
  - Highlight clearing
  - Message constants
  - Button state after save
  - First-invalid-field highlighting only
"""

from __future__ import annotations

import sys
import tempfile
import types as _types
import unittest
from unittest.mock import MagicMock, patch

# Stub base_page module so SettingsPage can inherit BasePage cleanly
_mock_base = _types.ModuleType("base_page")
class _FakeBasePage:
    def __init__(self, master, manager, logger):
        self.master = master
        self.manager = manager
        self.logger = logger
_mock_base.BasePage = _FakeBasePage

# Stub ctk module
ctk_mock = MagicMock()
ctk_mock.StringVar = MagicMock(side_effect=lambda: MagicMock())
ctk_mock.BooleanVar = MagicMock(side_effect=lambda: MagicMock())

# Save originals before patching sys.modules
_saved_modules: dict[str, object] = {}
for _mod_name in ("base_page", "customtkinter", "backend_manager", "notifications", "settings_panel"):
    _saved_modules[_mod_name] = sys.modules.get(_mod_name)

sys.modules["base_page"] = _mock_base
sys.modules["customtkinter"] = ctk_mock
sys.modules["backend_manager"] = MagicMock()
sys.modules["notifications"] = MagicMock()

try:
    # Force reload of settings_panel to use mock dependencies
    sys.modules.pop("settings_panel", None)
    from settings_panel import (
        MSG_INVALID_DOWNLOAD_FOLDER,
        MSG_INVALID_BACKEND_URL,
        MSG_INVALID_FFMPEG,
        MSG_INVALID_POLL,
        MSG_INVALID_UPDATE_POLL,
        MSG_INVALID_SCHEDULER_POLL,
        MSG_INVALID_SCHEDULER_RETRIES,
        SettingsPage,
    )
finally:
    for _mod_name, _orig in _saved_modules.items():
        if _orig is not None:
            sys.modules[_mod_name] = _orig
        else:
            sys.modules.pop(_mod_name, None)


class TestMessageConstants(unittest.TestCase):
    def test_download_folder_message_not_empty(self):
        self.assertTrue(len(MSG_INVALID_DOWNLOAD_FOLDER) > 10)

    def test_backend_url_message_not_empty(self):
        self.assertTrue(len(MSG_INVALID_BACKEND_URL) > 10)

    def test_ffmpeg_message_not_empty(self):
        self.assertTrue(len(MSG_INVALID_FFMPEG) > 10)

    def test_poll_message_not_empty(self):
        self.assertTrue(len(MSG_INVALID_POLL) > 10)

    def test_update_poll_message_not_empty(self):
        self.assertTrue(len(MSG_INVALID_UPDATE_POLL) > 10)

    def test_scheduler_poll_message_not_empty(self):
        self.assertTrue(len(MSG_INVALID_SCHEDULER_POLL) > 10)

    def test_scheduler_retries_message_not_empty(self):
        self.assertTrue(len(MSG_INVALID_SCHEDULER_RETRIES) > 10)


def _make_settings_page() -> SettingsPage:
    """Create a SettingsPage with _build_ui and _load_all_settings bypassed."""
    manager = MagicMock()
    manager.get_settings.return_value = {}
    manager.save_settings.return_value = {}
    manager.base_url = "http://127.0.0.1:5000"
    
    with (
        patch.object(SettingsPage, "_build_ui"),
        patch.object(SettingsPage, "_load_all_settings"),
    ):
        page = SettingsPage(MagicMock(), manager, MagicMock())
    
    # Wire up mock vars for each form field
    page._dir_var = MagicMock()
    page._ffmpeg_var = MagicMock()
    page._url_var = MagicMock()
    page._theme_var = MagicMock()
    page._auto_start_companion_var = MagicMock()
    page._auto_start_backend_var = MagicMock()
    page._notifications_var = MagicMock()
    page._poll_var = MagicMock()
    page._auto_check_var = MagicMock()
    page._check_startup_var = MagicMock()
    page._update_poll_var = MagicMock()
    page._scheduler_enabled_var = MagicMock()
    page._scheduler_poll_var = MagicMock()
    page._scheduler_auto_retry_var = MagicMock()
    page._scheduler_max_retries_var = MagicMock()
    page._scheduler_run_missed_startup_var = MagicMock()
    page._scheduler_notify_before_exec_var = MagicMock()

    # Wire up entry widgets for highlight tests
    page._dir_entry = MagicMock()
    page._ffmpeg_entry = MagicMock()
    page._url_entry = MagicMock()
    page._poll_entry = MagicMock()
    page._update_poll_entry = MagicMock()
    page._scheduler_poll_entry = MagicMock()
    page._scheduler_max_retries_entry = MagicMock()

    return page


def _set_widget_returns(page: SettingsPage, **overrides) -> None:
    """Convenience: set all widget .get() return values, with per-key overrides."""
    defaults = dict(
        download_folder="/default/downloads",
        ffmpeg_path="",
        backend_url="http://127.0.0.1:5000",
        theme="Dark",
        auto_start_companion=False,
        auto_start_backend=True,
        notification_toggle=True,
        backend_poll_interval=3,
        auto_check_updates=True,
        check_updates_startup=True,
        update_poll_interval=24,
        scheduler_enabled=True,
        scheduler_poll_interval=1,
        scheduler_auto_retry=True,
        scheduler_max_retries=3,
        scheduler_run_missed_startup=True,
        scheduler_notify_before_exec=True,
    )
    defaults.update(overrides)

    page._dir_entry.get.return_value = str(defaults["download_folder"])
    page._ffmpeg_var.get.return_value = defaults["ffmpeg_path"]
    page._url_var.get.return_value = defaults["backend_url"]
    page._theme_var.get.return_value = defaults["theme"]
    page._auto_start_companion_var.get.return_value = defaults["auto_start_companion"]
    page._auto_start_backend_var.get.return_value = defaults["auto_start_backend"]
    page._notifications_var.get.return_value = defaults["notification_toggle"]
    page._poll_var.get.return_value = str(defaults["backend_poll_interval"])
    page._auto_check_var.get.return_value = defaults["auto_check_updates"]
    page._check_startup_var.get.return_value = defaults["check_updates_startup"]
    page._update_poll_var.get.return_value = str(defaults["update_poll_interval"])
    page._scheduler_enabled_var.get.return_value = defaults["scheduler_enabled"]
    page._scheduler_poll_var.get.return_value = str(defaults["scheduler_poll_interval"])
    page._scheduler_auto_retry_var.get.return_value = defaults["scheduler_auto_retry"]
    page._scheduler_max_retries_var.get.return_value = str(defaults["scheduler_max_retries"])
    page._scheduler_run_missed_startup_var.get.return_value = defaults["scheduler_run_missed_startup"]
    page._scheduler_notify_before_exec_var.get.return_value = defaults["scheduler_notify_before_exec"]


def _base_vals() -> dict[str, any]:
    return dict(
        download_folder="/default/downloads",
        ffmpeg_path="",
        backend_url="http://127.0.0.1:5000",
        theme="Dark",
        auto_start_companion=False,
        auto_start_backend=True,
        notification_toggle=True,
        backend_poll_interval=3,
        auto_check_updates=True,
        check_updates_startup=True,
        update_poll_interval=24,
        scheduler_enabled=True,
        scheduler_poll_interval=1,
        scheduler_auto_retry=True,
        scheduler_max_retries=3,
        scheduler_run_missed_startup=True,
        scheduler_notify_before_exec=True,
    )


class TestValidateSettings(unittest.TestCase):
    def setUp(self):
        self.page = _make_settings_page()
        self.page._original_settings = {"backend_url": "http://127.0.0.1:5000"}
        self._isdir_patch = patch("settings_panel.os.path.isdir", return_value=True)
        self._isdir_patch.start()

    def tearDown(self):
        self._isdir_patch.stop()

    # Download folder
    def test_invalid_download_folder_returns_false(self):
        vals = _base_vals()
        vals["download_folder"] = "/nonexistent/path/xyz123"
        with patch("settings_panel.os.path.isdir", return_value=False):
            ok, msg, field = self.page._validate_settings(vals)
        self.assertFalse(ok)
        self.assertEqual(field, "download_folder")

    def test_empty_download_folder_returns_false(self):
        vals = _base_vals()
        vals["download_folder"] = ""
        ok, msg, field = self.page._validate_settings(vals)
        self.assertFalse(ok)
        self.assertEqual(field, "download_folder")

    def test_valid_download_folder_returns_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vals = _base_vals()
            vals["download_folder"] = tmpdir
            ok, msg, field = self.page._validate_settings(vals)
            self.assertTrue(ok)

    # Path normalization
    def test_tilde_path_expansion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("os.path.expanduser", return_value=tmpdir):
                with patch("os.path.isdir", return_value=True):
                    vals = _base_vals()
                    vals["download_folder"] = "~/some_folder"
                    ok, msg, field = self.page._validate_settings(vals)
                    self.assertTrue(ok)

    def test_relative_path_expansion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("os.path.abspath", return_value=tmpdir):
                with patch("os.path.isdir", return_value=True):
                    vals = _base_vals()
                    vals["download_folder"] = "relative/path"
                    ok, msg, field = self.page._validate_settings(vals)
                    self.assertTrue(ok)

    def test_mixed_slashes_normalized(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            normalized = tmpdir.replace("\\", "/")
            vals = _base_vals()
            vals["download_folder"] = normalized
            ok, msg, field = self.page._validate_settings(vals)
            self.assertTrue(ok)

    # FFmpeg
    def test_invalid_ffmpeg_returns_false(self):
        vals = _base_vals()
        vals["ffmpeg_path"] = "/nonexistent/ffmpeg.exe"
        with patch("os.path.exists", return_value=False):
            ok, msg, field = self.page._validate_settings(vals)
            self.assertFalse(ok)
            self.assertEqual(field, "ffmpeg_path")

    def test_empty_ffmpeg_is_valid(self):
        vals = _base_vals()
        vals["ffmpeg_path"] = ""
        ok, msg, field = self.page._validate_settings(vals)
        self.assertTrue(ok)

    # Backend URL
    def test_invalid_backend_url_returns_false(self):
        vals = _base_vals()
        vals["backend_url"] = "not-a-url"
        self.page._original_settings["backend_url"] = "http://old.url"
        ok, msg, field = self.page._validate_settings(vals)
        self.assertFalse(ok)
        self.assertEqual(field, "backend_url")

    def test_unchanged_backend_url_skips_validation(self):
        vals = _base_vals()
        url = "http://127.0.0.1:5000"
        vals["backend_url"] = url
        self.page._original_settings["backend_url"] = url
        ok, msg, field = self.page._validate_settings(vals)
        self.assertTrue(ok)

    # Poll intervals
    def test_invalid_poll_returns_false(self):
        vals = _base_vals()
        vals["backend_poll_interval"] = 999
        ok, msg, field = self.page._validate_settings(vals)
        self.assertFalse(ok)
        self.assertEqual(field, "backend_poll_interval")

    def test_valid_poll_returns_true(self):
        vals = _base_vals()
        vals["backend_poll_interval"] = 5
        ok, msg, field = self.page._validate_settings(vals)
        self.assertTrue(ok)

    def test_invalid_update_poll_returns_false(self):
        vals = _base_vals()
        vals["update_poll_interval"] = 999
        ok, msg, field = self.page._validate_settings(vals)
        self.assertFalse(ok)
        self.assertEqual(field, "update_poll_interval")

    def test_invalid_scheduler_poll_returns_false(self):
        vals = _base_vals()
        vals["scheduler_poll_interval"] = 999
        ok, msg, field = self.page._validate_settings(vals)
        self.assertFalse(ok)
        self.assertEqual(field, "scheduler_poll_interval")

    def test_invalid_scheduler_retries_returns_false(self):
        vals = _base_vals()
        vals["scheduler_max_retries"] = 999
        ok, msg, field = self.page._validate_settings(vals)
        self.assertFalse(ok)
        self.assertEqual(field, "scheduler_max_retries")

    # First invalid field
    def test_first_invalid_field_only(self):
        vals = _base_vals()
        vals["download_folder"] = ""
        vals["backend_poll_interval"] = 999
        ok, msg, field = self.page._validate_settings(vals)
        self.assertFalse(ok)
        self.assertEqual(field, "download_folder")

    def test_all_valid_returns_true_with_none_field(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vals = _base_vals()
            vals["download_folder"] = tmpdir
            ok, msg, field = self.page._validate_settings(vals)
            self.assertTrue(ok)
            self.assertIsNone(field)


class TestSaveClickReturnValue(unittest.TestCase):
    def setUp(self):
        self.page = _make_settings_page()

    def test_save_click_returns_true_on_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _set_widget_returns(self.page, download_folder=tmpdir)
            with patch("settings_panel.os.path.isdir", return_value=True):
                with patch.object(self.page, "_load_all_settings"):
                    with patch.object(self.page, "_show_validation_error"):
                        result = self.page._save_click()
                        self.assertTrue(result)

    def test_save_click_returns_false_on_validation_failure(self):
        _set_widget_returns(self.page, download_folder="/nonexistent")
        with patch("settings_panel.os.path.isdir", return_value=False):
            with patch.object(self.page, "_show_validation_error"):
                result = self.page._save_click()
                self.assertFalse(result)

    def test_save_settings_public_api_returns_same_as_private(self):
        with patch.object(self.page, "_save_click", return_value=True) as mock_save:
            result = self.page.save_settings()
            self.assertTrue(result)
        mock_save.assert_called_once()

    def test_highlight_set_on_first_invalid_field(self):
        self.page._clear_highlights = MagicMock()
        self.page._highlight_field = MagicMock()
        self.page._show_validation_error = MagicMock()
        _set_widget_returns(self.page, download_folder="/nonexistent")
        with patch("settings_panel.os.path.isdir", return_value=False):
            self.page._save_click()
        self.page._clear_highlights.assert_called_once()
        self.page._highlight_field.assert_called_once_with("download_folder")

    def test_highlight_cleared_on_successful_save(self):
        self.page._clear_highlights = MagicMock()
        self.page._highlight_field = MagicMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            _set_widget_returns(self.page, download_folder=tmpdir)
            with patch("settings_panel.os.path.isdir", return_value=True):
                with patch.object(self.page, "_load_all_settings"):
                    with patch.object(self.page, "_show_validation_error"):
                        self.page._save_click()
        self.page._clear_highlights.assert_called_once()
        self.page._highlight_field.assert_not_called()

    def test_clear_highlights_restores_default_border(self):
        entry = MagicMock()
        self.page._dir_entry = entry
        self.page._clear_highlights()
        entry.configure.assert_called_with(border_color="#2e3347")

    def test_highlight_field_sets_red_border_and_focus(self):
        entry = MagicMock()
        self.page._dir_entry = entry
        self.page._highlight_field("download_folder")
        entry.configure.assert_called_with(border_color="#e74c3c")
        entry.focus_set.assert_called_once()
        entry.select_range.assert_called_once_with(0, "end")

    def test_cancel_clears_highlights_and_calls_load_all(self):
        self.page._clear_highlights = MagicMock()
        self.page._load_all_settings = MagicMock()
        self.page._cancel_click()
        self.page._clear_highlights.assert_called_once()
        self.page._load_all_settings.assert_called_once()

    def test_save_btn_disabled_after_successful_save(self):
        """After save, _load_all_settings is called (which internally calls _update_save_btn_state)."""
        with patch.object(self.page, "_load_all_settings") as mock_load:
            with patch.object(self.page, "_show_validation_error"):
                with tempfile.TemporaryDirectory() as tmpdir:
                    _set_widget_returns(self.page, download_folder=tmpdir)
                    with patch("settings_panel.os.path.isdir", return_value=True):
                        self.page._save_click()
        mock_load.assert_called_once()


class TestDirtyFlag(unittest.TestCase):
    def setUp(self):
        self.page = _make_settings_page()

    def test_is_dirty_returns_false_when_values_match(self):
        self.page._original_settings = {"download_folder": "/same/path"}
        _set_widget_returns(self.page, download_folder="/same/path")
        self.assertFalse(self.page.is_dirty())

    def test_is_dirty_returns_true_when_changed(self):
        self.page._original_settings = {"download_folder": "/original"}
        _set_widget_returns(self.page, download_folder="/changed")
        self.assertTrue(self.page.is_dirty())

    def test_cancel_click_resets_dirty_flag(self):
        self.page._original_settings = {"download_folder": "/original"}
        _set_widget_returns(self.page, download_folder="/changed")
        self.assertTrue(self.page.is_dirty())
        self.page._load_all_settings = MagicMock()
        self.page._cancel_click()
        # After cancel, values are reset to originals (mocked below)
        _set_widget_returns(self.page, download_folder="/original")
        self.assertFalse(self.page.is_dirty())

    def test_save_resets_dirty_flag(self):
        """After successful save, originals are refreshed so dirty is False."""
        self.page._original_settings = {"download_folder": "/original"}
        _set_widget_returns(self.page, download_folder="/changed")
        self.assertTrue(self.page.is_dirty())
        # Simulate _load_all_settings refreshing originals
        self.page._original_settings["download_folder"] = "/changed"
        self.assertFalse(self.page.is_dirty())


# =========================================================================
# Exit Flow — Last Failed Field & Rehighlight
# =========================================================================


class TestLastFailedField(unittest.TestCase):
    def setUp(self):
        self.page = _make_settings_page()

    def test_last_failed_field_initialized_as_none(self):
        self.assertIsNone(self.page._last_failed_field)

    def test_last_failed_field_set_on_validation_failure(self):
        _set_widget_returns(self.page, download_folder="/nonexistent")
        with patch("settings_panel.os.path.isdir", return_value=False):
            with patch.object(self.page, "_show_validation_error"):
                self.page._save_click()
        self.assertEqual(self.page._last_failed_field, "download_folder")

    def test_last_failed_field_cleared_on_new_save_attempt(self):
        self.page._last_failed_field = "download_folder"
        _set_widget_returns(self.page, download_folder="/nonexistent")
        with patch("settings_panel.os.path.isdir", return_value=False):
            with patch.object(self.page, "_show_validation_error"):
                self.page._save_click()
        self.assertEqual(self.page._last_failed_field, "download_folder")

    def test_last_failed_field_set_on_poll_parse_error(self):
        self.page._get_widget_values = MagicMock(side_effect=ValueError)
        with patch.object(self.page, "_show_validation_error"):
            self.page._save_click()
        self.assertEqual(self.page._last_failed_field, "backend_poll_interval")

    def test_last_failed_field_cleared_on_successful_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _set_widget_returns(self.page, download_folder=tmpdir)
            with patch("settings_panel.os.path.isdir", return_value=True):
                with patch.object(self.page, "_load_all_settings"):
                    with patch.object(self.page, "_show_validation_error"):
                        self.page._save_click()
        self.assertIsNone(self.page._last_failed_field)

    def test_last_failed_field_cleared_on_new_save_attempt(self):
        self.page._last_failed_field = "download_folder"
        _set_widget_returns(self.page, download_folder="/nonexistent")
        with patch("settings_panel.os.path.isdir", return_value=False):
            with patch.object(self.page, "_show_validation_error"):
                self.page._save_click()
        self.assertEqual(self.page._last_failed_field, "download_folder")

    def test_last_failed_field_set_on_ffmpeg_invalid(self):
        _set_widget_returns(self.page, ffmpeg_path="/invalid/ffmpeg.exe")
        with patch("settings_panel.os.path.isdir", return_value=True):
            with patch("settings_panel.os.path.exists", return_value=False):
                with patch.object(self.page, "_show_validation_error"):
                    self.page._save_click()
        self.assertEqual(self.page._last_failed_field, "ffmpeg_path")

    def test_last_failed_field_set_on_invalid_url(self):
        _set_widget_returns(self.page, backend_url="not-a-valid-url")
        self.page._original_settings["backend_url"] = "http://127.0.0.1:5000"
        with patch("settings_panel.os.path.isdir", return_value=True):
            with patch.object(self.page, "_show_validation_error"):
                self.page._save_click()
        self.assertEqual(self.page._last_failed_field, "backend_url")

    def test_last_failed_field_set_on_invalid_poll(self):
        _set_widget_returns(self.page, backend_poll_interval=999)
        with patch("settings_panel.os.path.isdir", return_value=True):
            with patch.object(self.page, "_show_validation_error"):
                self.page._save_click()
        self.assertEqual(self.page._last_failed_field, "backend_poll_interval")


class TestRehighlightLastFailed(unittest.TestCase):
    def setUp(self):
        self.page = _make_settings_page()

    def test_rehighlight_calls_highlight_with_stored_field(self):
        self.page._highlight_field = MagicMock()
        self.page._last_failed_field = "download_folder"
        self.page._rehighlight_last_failed()
        self.page._highlight_field.assert_called_once_with("download_folder")

    def test_rehighlight_clears_last_failed_field(self):
        self.page._highlight_field = MagicMock()
        self.page._last_failed_field = "download_folder"
        self.page._rehighlight_last_failed()
        self.assertIsNone(self.page._last_failed_field)

    def test_rehighlight_noop_when_none(self):
        self.page._highlight_field = MagicMock()
        self.page._last_failed_field = None
        self.page._rehighlight_last_failed()
        self.page._highlight_field.assert_not_called()

    def test_rehighlight_clears_even_when_highlight_fails(self):
        self.page._highlight_field = MagicMock(side_effect=Exception("entry gone"))
        self.page._last_failed_field = "download_folder"
        with self.assertRaises(Exception):
            self.page._rehighlight_last_failed()
        self.assertEqual(self.page._last_failed_field, "download_folder")


if __name__ == "__main__":
    unittest.main()
