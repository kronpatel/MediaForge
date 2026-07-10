"""Tests for startup validation module (startup_check.py).

Covers:
  - FFmpeg check (file present / missing)
  - Queue file check (missing, empty, valid, corrupt)
  - Data directory write permissions
  - Full run_startup_checks pipeline
  - Graceful handling of all scenarios (never crashes)
"""

import json
import os
import sys
import tempfile
import unittest

# Ensure the backend directory is on sys.path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.startup_check import (
    check_ffmpeg,
    check_ffmpeg_env_path,
    check_queue_file,
    check_data_dirs,
    run_startup_checks,
    log_startup_checks,
    StartupCheckResult,
)


class TestStartupCheck(unittest.TestCase):

    def setUp(self):
        # Save original working directory
        self._orig_dir = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="mf_startup_")
        # Point ffmpeg dir to a temp location to avoid interfering with real files
        self._patch_ffmpeg_dir(self._tmpdir)
        # Also patch the backend dir for queue checks
        self._backend_dir = os.path.join(self._tmpdir, "backend")
        os.makedirs(self._backend_dir, exist_ok=True)
        self._patch_backend_dir(self._backend_dir)

    def tearDown(self):
        os.chdir(self._orig_dir)
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _patch_ffmpeg_dir(self, tmpdir):
        import backend.startup_check as sc
        sc._FFMPEG_DIR = os.path.join(tmpdir, "ffmpeg")

    def _patch_backend_dir(self, path):
        import backend.startup_check as sc
        sc._DIR = path
        sc.BASE_DIR = path
        import backend.downloader as dl
        dl.BASE_DIR = path

    def _create_ffmpeg_binaries(self, subdir="ffmpeg"):
        ffmpeg_dir = os.path.join(self._tmpdir, subdir)
        os.makedirs(ffmpeg_dir, exist_ok=True)
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            with open(os.path.join(ffmpeg_dir, name), "w") as f:
                f.write("fake binary")
        return ffmpeg_dir

    # ------------------------------------------------------------------
    # check_ffmpeg
    # ------------------------------------------------------------------

    def test_ffmpeg_missing(self):
        result = check_ffmpeg()
        self.assertFalse(result.passed)
        self.assertIn("ffmpeg.exe", result.message)

    def test_ffmpeg_present(self):
        self._create_ffmpeg_binaries()
        result = check_ffmpeg()
        self.assertTrue(result.passed)

    def test_ffmpeg_partial(self):
        ffmpeg_dir = os.path.join(self._tmpdir, "ffmpeg")
        os.makedirs(ffmpeg_dir, exist_ok=True)
        with open(os.path.join(ffmpeg_dir, "ffmpeg.exe"), "w") as f:
            f.write("fake")
        result = check_ffmpeg()
        self.assertFalse(result.passed)
        self.assertIn("ffprobe.exe", result.message)

    # ------------------------------------------------------------------
    # check_ffmpeg_env_path
    # ------------------------------------------------------------------

    def test_ffmpeg_env_path(self):
        # Should at least not crash
        result = check_ffmpeg_env_path()
        self.assertIsInstance(result, StartupCheckResult)
        self.assertIn(result.name, ("FFmpeg-PATH",))

    # ------------------------------------------------------------------
    # check_queue_file
    # ------------------------------------------------------------------

    def test_queue_file_missing(self):
        result = check_queue_file()
        self.assertTrue(result.passed)
        self.assertIn("No queue_state.json", result.message)

    def test_queue_file_empty(self):
        queue_path = os.path.join(self._backend_dir, "queue_state.json")
        with open(queue_path, "w") as f:
            pass  # empty file
        result = check_queue_file()
        self.assertFalse(result.passed)
        self.assertIn("empty", result.message)

    def test_queue_file_valid(self):
        queue_path = os.path.join(self._backend_dir, "queue_state.json")
        with open(queue_path, "w") as f:
            json.dump({"schema_version": 1, "queue": []}, f)
        result = check_queue_file()
        self.assertTrue(result.passed)
        self.assertIn("schema_version=1", result.message)

    def test_queue_file_corrupt(self):
        queue_path = os.path.join(self._backend_dir, "queue_state.json")
        with open(queue_path, "w") as f:
            f.write("{invalid json")
        result = check_queue_file()
        self.assertFalse(result.passed)
        self.assertIn("Corrupted", result.message)

    # ------------------------------------------------------------------
    # check_data_dirs
    # ------------------------------------------------------------------

    def test_data_dirs_writable(self):
        result = check_data_dirs()
        self.assertTrue(result.passed)
        self.assertIn("Write permission OK", result.message)

    def test_data_dirs_unwritable(self):
        import backend.startup_check as sc
        original_dir = sc.BASE_DIR
        sc.BASE_DIR = os.path.join(self._tmpdir, "nonexistent_deep_dir")
        result = check_data_dirs()
        self.assertFalse(result.passed)
        self.assertIn("Cannot write", result.message)
        sc.BASE_DIR = original_dir

    # ------------------------------------------------------------------
    # run_startup_checks
    # ------------------------------------------------------------------

    def test_run_startup_checks_returns_list(self):
        results = run_startup_checks()
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertIsInstance(r, StartupCheckResult)

    def test_run_startup_checks_all_named(self):
        results = run_startup_checks()
        names = {r.name for r in results}
        self.assertIn("FFmpeg", names)
        self.assertIn("FFmpeg-PATH", names)
        self.assertIn("QueueFile", names)
        self.assertIn("DataDirs", names)

    # ------------------------------------------------------------------
    # log_startup_checks
    # ------------------------------------------------------------------

    def test_log_startup_checks_does_not_crash(self):
        results = run_startup_checks()
        # Should not raise
        log_startup_checks(results)

    def test_log_startup_checks_empty(self):
        log_startup_checks([])


if __name__ == "__main__":
    unittest.main()
