"""Unit tests for scripts/verify_build.py (BUG-19)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import verify_build


class TestVerifyBuildArtifacts(unittest.TestCase):
    """Tests for multi-artifact verification in verify_build.py."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = self.tmpdir.name
        self.dist_dir = os.path.join(self.base_dir, "dist")
        os.makedirs(self.dist_dir, exist_ok=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _create_exe(self, filename: str, size: int = 1024):
        path = os.path.join(self.dist_dir, filename)
        with open(path, "wb") as f:
            f.write(b"X" * size)
        return path

    def test_all_three_artifacts_present_passes(self):
        """Test 1: All 3 required artifacts present and non-empty returns 0 errors."""
        self._create_exe("MediaForge.exe")
        self._create_exe("MediaForgeBackend.exe")
        self._create_exe("MediaForgeService.exe")

        errors = verify_build.verify_artifacts(base_dir=self.base_dir)
        self.assertEqual(len(errors), 0, f"Expected 0 errors, got: {errors}")

    def test_backend_exe_missing_fails(self):
        """Test 2: MediaForgeBackend.exe missing reports error containing MISSING."""
        self._create_exe("MediaForge.exe")
        self._create_exe("MediaForgeService.exe")

        errors = verify_build.verify_artifacts(base_dir=self.base_dir)
        self.assertGreaterEqual(len(errors), 1)
        self.assertTrue(
            any("dist/MediaForgeBackend.exe" in err and "MISSING" in err for err in errors),
            f"Expected MISSING error for MediaForgeBackend.exe, got: {errors}",
        )

    def test_service_exe_missing_fails(self):
        """Test 3: MediaForgeService.exe missing reports error containing MISSING."""
        self._create_exe("MediaForge.exe")
        self._create_exe("MediaForgeBackend.exe")

        errors = verify_build.verify_artifacts(base_dir=self.base_dir)
        self.assertGreaterEqual(len(errors), 1)
        self.assertTrue(
            any("dist/MediaForgeService.exe" in err and "MISSING" in err for err in errors),
            f"Expected MISSING error for MediaForgeService.exe, got: {errors}",
        )


if __name__ == "__main__":
    unittest.main()
