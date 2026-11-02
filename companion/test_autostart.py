"""
test_autostart.py – Tests for set_registry_autostart (Phase 3 Area 5).

Covers:
  - Frozen mode: registry Run command is MediaForge.exe alone (no main.py arg).
  - Source mode: registry Run command is "python main.py".
  - Disabled: DeleteValue removes the Run key entry.
  - Non-Windows: no-op.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

# Stub heavy GUI/backend deps so settings_panel can be imported standalone.
_saved = {}
for _mod in ("base_page", "customtkinter", "backend_manager", "notifications", "winreg"):
    _saved[_mod] = sys.modules.get(_mod)
    sys.modules[_mod] = MagicMock()

try:
    sys.modules.pop("settings_panel", None)
    import settings_panel  # noqa: E402
finally:
    for _mod, _orig in _saved.items():
        if _orig is not None:
            sys.modules[_mod] = _orig
        else:
            sys.modules.pop(_mod, None)


@unittest.skipUnless(sys.platform == "win32", "Windows registry autostart")
class TestSetRegistryAutostart(unittest.TestCase):
    def setUp(self):
        self.winreg = settings_panel.winreg
        self.winreg.reset_mock()

    def test_frozen_mode_uses_exe_directly_no_main_py(self):
        """Frozen: command must be the EXE itself, never '... main.py'."""
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", r"C:\MediaForge\MediaForge.exe"), \
             patch.object(settings_panel, "_COMPANION_DIR", r"C:\MediaForge\companion"):
            settings_panel.set_registry_autostart(True)

        self.winreg.OpenKey.assert_called_once()
        self.winreg.SetValueEx.assert_called_once()
        # SetValueEx(key, value_name, reserved, type, value) -> value is index 4
        call = self.winreg.SetValueEx.call_args
        self.assertEqual(call[0][1], "MediaForgeCompanion")
        cmd = call[0][4]
        self.assertEqual(cmd, r'"C:\MediaForge\MediaForge.exe"')
        self.assertNotIn("main.py", cmd)

    def test_source_mode_uses_python_main_py(self):
        """Source: command must be the interpreter plus the companion entry point."""
        with patch.object(sys, "frozen", False, create=True), \
             patch.object(sys, "executable", sys.executable), \
             patch.object(settings_panel, "_COMPANION_DIR", r"C:\MediaForge\companion"):
            settings_panel.set_registry_autostart(True)

        self.winreg.SetValueEx.assert_called_once()
        call = self.winreg.SetValueEx.call_args
        self.assertEqual(call[0][1], "MediaForgeCompanion")
        cmd = call[0][4]
        self.assertIn(sys.executable, cmd)
        self.assertIn("main.py", cmd)

    def test_disabled_calls_delete_value(self):
        """Disabled: remove the Run key value rather than writing one."""
        with patch.object(sys, "frozen", False, create=True):
            settings_panel.set_registry_autostart(False)

        self.winreg.DeleteValue.assert_called_once()
        call = self.winreg.DeleteValue.call_args
        self.assertEqual(call[0][1], "MediaForgeCompanion")
        self.winreg.SetValueEx.assert_not_called()


if __name__ == "__main__":
    unittest.main()
