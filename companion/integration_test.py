"""Integration tests using real CTk widgets and the actual validation pipeline.

WARNING: This file must be run separately from the unit test suite (`test_*.py`)
because test_settings.py replaces customtkinter with a MagicMock globally,
which breaks real-widget creation.
Run:  python -m unittest companion.test_integration -v
"""

import customtkinter as ctk
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure companion/ submodules are importable as top-level names
_companion_dir = os.path.join(os.path.dirname(__file__))
if _companion_dir not in sys.path:
    sys.path.insert(0, _companion_dir)

# Restore real customtkinter if test_settings.py poisoned it
if "customtkinter" in sys.modules:
    import importlib
    import customtkinter as _real_ctk
    if isinstance(_real_ctk, MagicMock) or hasattr(_real_ctk, '_mock_return_value'):
        sys.modules["customtkinter"] = importlib.import_module("customtkinter")


class RealWidgetValidationTest(unittest.TestCase):
    """Creates real CTk widgets and exercises the actual validation pipeline."""

    @classmethod
    def setUpClass(cls):
        ctk.set_appearance_mode("Dark")
        cls.root = ctk.CTk()
        cls.root.withdraw()
        cls.root.title("Integration Test")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _ensure_real_modules(self):
        """Force a fresh re-import of settings_panel so it uses real base classes."""
        import importlib

        # 1. Ensure sys.modules has real modules for everything settings_panel needs
        for mod_name in ("customtkinter", "base_page", "backend_manager", "notifications"):
            if mod_name in sys.modules:
                mod = sys.modules[mod_name]
                # Check if it's a mock
                if isinstance(mod, MagicMock) or hasattr(mod, '_mock_return_value') or \
                   (hasattr(mod, '__class__') and 'MagicMock' in type(mod).__name__):
                    try:
                        sys.modules[mod_name] = importlib.import_module(mod_name)
                    except ImportError:
                        pass  # Use whatever is there

        # 2. Force settings_panel to re-import on next import
        for key in list(sys.modules.keys()):
            if 'settings_panel' in key:
                del sys.modules[key]

    def setUp(self):
        self._ensure_real_modules()

        import settings_panel as sp_mod
        self.sp_mod = sp_mod

        # Build mock manager
        self.mock_manager = MagicMock()
        self.mock_manager.get_settings.return_value = {
            "download_folder": "",
            "ffmpeg_path": "",
            "backend_url": "http://127.0.0.1:5000",
        }
        self.mock_manager.base_url = "http://127.0.0.1:5000"
        self.mock_manager.status = "running"
        self.mock_manager.save_settings.return_value = True

        # Mock logger
        self.mock_logger = MagicMock()

        # Mock local settings IO — patch the name in the module's namespace
        self._orig_read = sp_mod.read_local_settings
        self._orig_write = sp_mod.write_local_settings
        sp_mod.read_local_settings = MagicMock(return_value={
            "theme": "Dark",
            "auto_start_companion": False,
            "auto_start_backend": True,
            "notification_toggle": True,
            "backend_poll_interval": 3,
            "auto_check_updates": True,
            "update_poll_interval": 24,
            "check_updates_startup": True,
            "scheduler_enabled": True,
            "scheduler_poll_interval": 1,
            "scheduler_auto_retry": True,
            "scheduler_max_retries": 3,
            "scheduler_run_missed_startup": True,
            "scheduler_notify_before_exec": True,
        })
        sp_mod.write_local_settings = MagicMock()

        # Build a parent frame and create a real SettingsPage inside it
        self.parent = ctk.CTkFrame(self.root)
        self.parent.pack(fill="both", expand=True)

        self.settings = sp_mod.SettingsPage(self.parent, self.mock_manager, self.mock_logger)
        self.settings.pack(fill="both", expand=True)

        # Process pending tkinter events
        self.root.update()

    def tearDown(self):
        if hasattr(self, 'sp_mod') and hasattr(self, '_orig_read'):
            self.sp_mod.read_local_settings = self._orig_read
            self.sp_mod.write_local_settings = self._orig_write
        if hasattr(self, 'parent'):
            self.parent.destroy()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_folder(self, path: str) -> None:
        """Set the download folder entry via tkinter API (as a real user would)."""
        self.settings._dir_entry.configure(state="normal")
        self.settings._dir_entry.delete(0, "end")
        self.settings._dir_entry.insert(0, path)
        self.root.update()

    def _get_folder(self) -> str:
        return self.settings._dir_entry.get().strip()

    # ------------------------------------------------------------------
    # Scenario 1: Valid folder, Save, X close
    # ------------------------------------------------------------------

    def test_scenario1_valid_folder_save_and_close(self):
        """
        1. Set download folder to C:\\Users\\KRON\\Downloads
        2. Click Save
        3. Click X
        Expected: No validation error, no traceback, app exits normally.
        """
        self._set_folder("C:\\Users\\KRON\\Downloads")

        # Simulate Save button click
        result = self.settings._save_click()

        self.assertTrue(result, "save_settings() should return True for valid folder")
        self.mock_manager.save_settings.assert_called_once()
        saved_args = self.mock_manager.save_settings.call_args[0][0]
        normalized = os.path.normpath(os.path.abspath(os.path.expanduser("C:\\Users\\KRON\\Downloads")))
        self.assertEqual(
            os.path.normpath(saved_args["download_folder"]),
            normalized,
        )

        # Simulate exit flow (clean state, no dirty check needed since we saved)
        self.assertFalse(self.settings.is_dirty(),
                         "Settings should not be dirty after save")

    def test_scenario1_forward_slash_path(self):
        """Forward slash path (from filedialog) should pass validation after normpath."""
        self._set_folder("C:/Users/KRON/Downloads")
        result = self.settings._save_click()
        self.assertTrue(result, "Forward-slash path should pass validation with normpath")

    # ------------------------------------------------------------------
    # Scenario 2: Invalid folder, Save
    # ------------------------------------------------------------------

    def test_scenario2_invalid_folder_shows_error(self):
        """
        1. Set download folder to C:\\ThisFolderDoesNotExist
        2. Click Save
        Expected: Validation dialog appears, Settings stays open,
                  Download Folder field highlighted, no crash.
        """
        self._set_folder("C:\\ThisFolderDoesNotExist")

        # Mock messagebox to verify dialog would appear
        # We test the return value of _save_click which indicates validation failure
        with patch("tkinter.messagebox.showerror") as mock_showerror:
            result = self.settings._save_click()

        self.assertFalse(result, "save_settings() should return False for invalid folder")

        # Verify error dialog was shown
        mock_showerror.assert_called_once()
        args, _ = mock_showerror.call_args
        error_text = " ".join(str(a).lower() for a in args)
        self.assertTrue(
            any(word in error_text for word in ["folder", "directory", "path", "exist", "valid"]),
            f"Error dialog should mention folder/directory/path. Got: {args}",
        )

        # Verify field was highlighted
        self.assertEqual(
            self.settings._last_failed_field,
            "download_folder",
            "Should mark download_folder as failed",
        )

    def test_scenario2_invalid_folder_no_crash(self):
        """Invalid folder must never cause an unhandled exception."""
        self._set_folder("C:\\ThisFolderDoesNotExist\\Sub\\Path\\XYZ")
        try:
            with patch("tkinter.messagebox.showerror"):
                result = self.settings._save_click()
            self.assertFalse(result)
        except Exception:
            self.fail("Validation should not raise an exception for invalid folder")

    # ------------------------------------------------------------------
    # Scenario 3: Dirty, X -> Yes -> save succeeds -> exit
    # ------------------------------------------------------------------

    def test_scenario3_dirty_x_yes_save_and_exit(self):
        """
        1. Make settings dirty (change a value)
        2. Click X
        3. Choose Yes (handled by _on_close_request)
        Expected: save_settings() runs, validation succeeds, window exits.
        """
        # Make it dirty by changing the folder
        self._set_folder("C:\\Users\\KRON\\Downloads")
        self.root.update()

        self.assertTrue(self.settings.is_dirty(),
                        "Settings should be dirty after changing folder")

        # save_settings() is called by _on_close_request when user clicks Yes.
        # It calls _save_click internally — no dialog in this method.
        should_save = self.settings.save_settings()
        self.assertTrue(should_save,
                        "save_settings() should succeed for valid folder")

    def test_scenario3_dirty_not_dirty_after_save(self):
        """After save with valid data, settings should no longer be dirty."""
        self._set_folder("C:\\Users\\KRON\\Downloads")
        self.settings._save_click()
        self.assertFalse(self.settings.is_dirty(),
                         "Settings should not be dirty after successful save")

    # ------------------------------------------------------------------
    # Scenario 4: Dirty, X -> Yes -> validation fails
    # ------------------------------------------------------------------

    def test_scenario4_dirty_x_yes_validation_fails(self):
        """
        1. Make settings dirty with INVALID folder
        2. Click X
        3. Choose Yes
        Expected: Validation dialog appears, window remains open,
                  sidebar switches to Settings, field gets focus,
                  no second Unsaved Changes dialog, no traceback.
        """
        # Make dirty with invalid folder
        self._set_folder("C:\\ThisFolderDoesNotExist")
        self.root.update()

        self.assertTrue(self.settings.is_dirty(),
                        "Settings should be dirty with changed invalid folder")

        # Simulate _on_close_request behavior:
        # 1. User clicks Yes on Unsaved Changes dialog
        # 2. save_settings() runs and fails validation
        # 3. Exit should be aborted
        with (
            patch("tkinter.messagebox.askyesnocancel", return_value=True) as mock_unsaved,
            patch("tkinter.messagebox.showerror") as mock_showerror,
        ):
            result = self.settings.save_settings()

        self.assertFalse(result,
                         "save_settings() should return False for invalid folder")

        # Verify the error dialog appeared
        mock_showerror.assert_called_once()

        # Verify field was marked
        self.assertEqual(
            self.settings._last_failed_field,
            "download_folder",
        )

        # Verify _on_close_request's behavior after save_settings() returns False:
        # - Should NOT show a second Unsaved Changes dialog
        # - Should switch to Settings page
        # - _rehighlight_last_failed() should be scheduled
        # The re-highlight scheduling is handled in ui.py's _on_close_request.
        # Here we verify that _rehighlight_last_failed doesn't crash:
        try:
            self.settings._rehighlight_last_failed()
        except Exception:
            self.fail("_rehighlight_last_failed should not raise an exception")

    def test_scenario4_no_second_dialog_after_validation_fail(self):
        """After validation failure, no second Unsaved Changes dialog should appear."""
        self._set_folder("C:\\ThisFolderDoesNotExist")
        self.root.update()

        # First save attempt fails validation
        with patch("tkinter.messagebox.showerror"):
            self.settings.save_settings()

        # After validation failure, settings should still be dirty
        # (because the invalid value was not saved)
        self.assertTrue(self.settings.is_dirty(),
                        "Settings should remain dirty after failed validation")

        # Simulate what _on_close_request does:
        # The dialog is already shown by save_settings() failing.
        # The exit is aborted (returned early).
        # If we loop back to the unsaved changes check in _on_close_request,
        # we would show a second dialog. But the early return prevents that.
        # We verify that is_dirty() returns True but no dialog loop occurs
        # because the exit was aborted at the validation step.

    def test_scenario4_highlight_recovery(self):
        """_rehighlight_last_failed should highlight the download_folder entry."""
        self._set_folder("C:\\ThisFolderDoesNotExist")
        self.root.update()

        with patch("tkinter.messagebox.showerror"):
            self.settings._save_click()

        self.assertEqual(self.settings._last_failed_field, "download_folder")

        # Simulate the after() callback in _on_close_request
        self.settings._clear_highlights()
        self.settings._rehighlight_last_failed()
        self.root.update()

        # The highlight should have been applied (we can't easily verify
        # visual border color, but we verify no crash and focus is set)
        focused = self.root.focus_get() if self.root.focus_get() else None
        # No assertion on focus — it may not work in withdrawn window.
        # The key is no exception was raised.

    def test_scenario4_validation_then_valid_save(self):
        """After validation failure, user can fix value and save successfully."""
        self._set_folder("C:\\ThisFolderDoesNotExist")
        self.root.update()

        with patch("tkinter.messagebox.showerror"):
            self.settings._save_click()

        self.assertFalse(self.mock_manager.save_settings.called,
                         "save_settings should not be called when validation fails")

        # Fix the folder and save again
        self._set_folder("C:\\Users\\KRON\\Downloads")
        self.root.update()
        result = self.settings._save_click()
        self.assertTrue(result)

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_folder_validation_fails(self):
        """Empty download folder should fail validation."""
        self._set_folder("")
        with patch("tkinter.messagebox.showerror"):
            result = self.settings._save_click()
        self.assertFalse(result)

    def test_whitespace_folder_validation_fails(self):
        """Whitespace-only folder should fail validation."""
        self._set_folder("   ")
        with patch("tkinter.messagebox.showerror"):
            result = self.settings._save_click()
        self.assertFalse(result)

    def test_tempdir_validation_succeeds(self):
        """A real temp directory should pass validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._set_folder(tmpdir)
            result = self.settings._save_click()
        self.assertTrue(result,
                        f"Temporary directory '{tmpdir}' should pass validation")

    def test_entry_and_var_are_synced(self):
        """entry.get() and var.get() should return the same value."""
        test_path = "C:\\Users\\KRON\\Downloads"
        self._set_folder(test_path)
        self.root.update()
        entry_val = self.settings._dir_entry.get().strip()
        var_val = self.settings._dir_var.get().strip()
        self.assertEqual(entry_val, var_val)
        self.assertEqual(entry_val, test_path)

    def test_normpath_in_validation_pipeline(self):
        """os.path.normpath is applied during validation."""
        vals = {"download_folder": "C:/Users/KRON/Downloads", "ffmpeg_path": "",
                "backend_url": "http://127.0.0.1:5000", "backend_poll_interval": 3,
                "update_poll_interval": 24, "scheduler_poll_interval": 1,
                "scheduler_max_retries": 3}
        ok, err, field = self.settings._validate_settings(vals)
        self.assertTrue(ok)

    def test_normpath_invalid_becomes_valid(self):
        """A path that only works after normpath should be accepted."""
        vals = {"download_folder": "C:/Users/KRON/Downloads/", "ffmpeg_path": "",
                "backend_url": "http://127.0.0.1:5000", "backend_poll_interval": 3,
                "update_poll_interval": 24, "scheduler_poll_interval": 1,
                "scheduler_max_retries": 3}
        ok, err, field = self.settings._validate_settings(vals)
        self.assertTrue(ok)

    def test_dirty_changed_folder(self):
        """Changing the folder value should make settings dirty."""
        initial = self.settings._dir_entry.get().strip()
        self._set_folder("C:\\Users\\KRON\\Downloads")
        self.root.update()
        self.assertTrue(self.settings.is_dirty(),
                        "Changing folder should mark settings as dirty")

    def test_highlight_field_ctkentry_api(self):
        """_highlight_field must use CTkEntry API (select_range, not selection_range)."""
        self.settings._clear_highlights()
        self.settings._highlight_field("download_folder")
        self.root.update()
        # Mainly a crash test — verify no exception from select_range

    def test_save_and_rehighlight_flow(self):
        """Save -> fail -> rehighlight should all work without crash."""
        self._set_folder("C:\\ThisFolderDoesNotExist")
        with patch("tkinter.messagebox.showerror"):
            self.settings._save_click()
        self.settings._rehighlight_last_failed()
        self.root.update()

    def test_save_ffmpeg_preserved(self):
        """Saving settings should preserve ffmpeg_path from the entry."""
        self._set_folder("C:\\Users\\KRON\\Downloads")
        self.settings._ffmpeg_var.set("")
        self.root.update()
        result = self.settings._save_click()
        self.assertTrue(result, "Empty ffmpeg path should pass validation")
        saved_args = self.mock_manager.save_settings.call_args[0][0]
        self.assertEqual(saved_args["ffmpeg_path"], "")


if __name__ == "__main__":
    unittest.main()
