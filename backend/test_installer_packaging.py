"""Tests for packaging and installer validation workflows.

Covers:
  - VERSION file existence and format
  - verify_versions.py script execution
  - check_resources.py script execution
  - verify_build.py script execution (with EXE present / absent)
  - package_portable.py script execution
  - Build artifact structure verification
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")


class TestPackagingValidation(unittest.TestCase):
    """Tests for packaging scripts and build verification."""

    def test_version_file_exists(self):
        path = os.path.join(PROJECT_ROOT, "VERSION")
        self.assertTrue(os.path.exists(path), "VERSION file missing")
        with open(path) as f:
            content = f.read().strip()
        self.assertRegex(content, r"^\d+\.\d+\.\d+$")

    def test_version_file_format(self):
        path = os.path.join(PROJECT_ROOT, "VERSION")
        with open(path) as f:
            content = f.read().strip()
        parts = content.split(".")
        self.assertEqual(len(parts), 3)
        for p in parts:
            self.assertTrue(p.isdigit())

    def test_verify_versions_script_runs(self):
        script = os.path.join(SCRIPTS_DIR, "verify_versions.py")
        self.assertTrue(os.path.exists(script))
        # This should either pass or fail gracefully
        result = subprocess.run(
            [sys.executable, script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertIn("VERSION", result.stdout)

    def test_verify_versions_script_output(self):
        script = os.path.join(SCRIPTS_DIR, "verify_versions.py")
        result = subprocess.run(
            [sys.executable, script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertIn("All versions match", result.stdout)

    def test_check_resources_script_runs(self):
        script = os.path.join(SCRIPTS_DIR, "check_resources.py")
        self.assertTrue(os.path.exists(script))
        result = subprocess.run(
            [sys.executable, script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertIn("All resources OK", result.stdout)

    def test_check_resources_missing_file(self):
        import scripts.check_resources as cr
        original = cr.REQUIRED_RESOURCES[:]
        cr.REQUIRED_RESOURCES.append(
            ("nonexistent_file_12345.txt", "Should not exist")
        )
        try:
            with self.assertRaises(SystemExit):
                cr.main()
        finally:
            cr.REQUIRED_RESOURCES = original

    def test_verify_build_script_runs_without_exe(self):
        script = os.path.join(SCRIPTS_DIR, "verify_build.py")
        self.assertTrue(os.path.exists(script))
        exe_path = os.path.join(PROJECT_ROOT, "dist", "MediaForge.exe")
        temp_exe_path = os.path.join(PROJECT_ROOT, "dist", "MediaForge.exe.bak")
        exe_existed = os.path.exists(exe_path)
        if exe_existed:
            os.rename(exe_path, temp_exe_path)
        try:
            # When EXE doesn't exist, script should exit with error
            result = subprocess.run(
                [sys.executable, script],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
        finally:
            if exe_existed and os.path.exists(temp_exe_path):
                if os.path.exists(exe_path):
                    os.remove(exe_path)
                os.rename(temp_exe_path, exe_path)

    def test_package_portable_script_runs_without_exe(self):
        script = os.path.join(SCRIPTS_DIR, "package_portable.py")
        self.assertTrue(os.path.exists(script))
        result = subprocess.run(
            [sys.executable, script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        # Should run without crashing even if EXE missing
        self.assertIn("Creating", result.stdout)
        # Clean up any created files
        release_dir = os.path.join(PROJECT_ROOT, "release")
        if os.path.exists(release_dir):
            import shutil
            shutil.rmtree(release_dir, ignore_errors=True)

    def test_smoke_test_script_exists(self):
        script = os.path.join(SCRIPTS_DIR, "smoke_test.py")
        self.assertTrue(os.path.exists(script))

    def test_build_bat_exists(self):
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "build.bat")))

    def test_clean_bat_exists(self):
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "clean.bat")))

    def test_release_bat_exists(self):
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "release.bat")))

    def test_pyinstaller_spec_exists(self):
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "MediaForge.spec")))

    def test_logger_file_logging(self):
        """Test that AppLogger.enable_file_logging works without crashing."""
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "companion"))
        try:
            from logger import AppLogger
            logger = AppLogger(debug=False)
            self.assertFalse(logger.file_logging_enabled)
            logger.enable_file_logging()
            self.assertTrue(logger.file_logging_enabled)
            log_path = logger.get_log_file_path()
            self.assertTrue(log_path.endswith("companion.log"))
            logger.info("[Test] Production logging test entry")
        finally:
            sys.path.pop(0)

    def test_resources_directory_structure(self):
        res_dir = os.path.join(PROJECT_ROOT, "companion", "resources")
        self.assertTrue(os.path.isdir(res_dir))
        files = os.listdir(res_dir)
        self.assertIn("icon.ico", files)
        self.assertIn("icon.png", files)
        self.assertIn("tray.ico", files)

    def test_build_scripts_no_python_syntax_errors(self):
        scripts = ["build.bat", "clean.bat", "release.bat"]
        for script in scripts:
            path = os.path.join(PROJECT_ROOT, script)
            self.assertTrue(os.path.exists(path), f"{script} missing")
            with open(path) as f:
                content = f.read()
            self.assertGreater(len(content), 10, f"{script} is too short")

    def test_scripts_directory_python_syntax(self):
        for fname in os.listdir(SCRIPTS_DIR):
            if fname.endswith(".py"):
                path = os.path.join(SCRIPTS_DIR, fname)
                result = subprocess.run(
                    [sys.executable, "-m", "py_compile", path],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, f"{fname} has syntax error")


if __name__ == "__main__":
    unittest.main()
