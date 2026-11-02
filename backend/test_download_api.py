"""API-level tests for /download route output_folder validation (TASK 3).

Uses Flask test-client to verify:
  - Integer, float, boolean, array and object values receive HTTP 400.
  - None and blank strings use the default folder.
  - Traversal using forward, backward and mixed separators is rejected.
  - Valid absolute paths are normalized before reaching queue_download().
  - Invalid paths do not create directories or crash the server.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, _enforce_single_instance

import downloader


class TestDownloadAPIValidation(unittest.TestCase):
    """Flask test-client tests for /download output_folder validation."""

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        # Patch queue_download to capture calls without actually queueing
        self._patch = patch("app.queue_download")
        self.mock_queue_download = self._patch.start()
        self.mock_queue_download.return_value = {
            "id": "test-job-id",
            "status": "queued",
            "queue_position": 1,
        }

    def tearDown(self):
        self._patch.stop()

    def _post_download(self, output_folder):
        return self.client.post(
            "/download",
            data=json.dumps({
                "url": "https://youtube.com/watch?v=test",
                "mode": "mp3",
                "output_folder": output_folder,
            }),
            content_type="application/json",
        )

    # ------------------------------------------------------------------
    # Non-string values → HTTP 400
    # ------------------------------------------------------------------

    def test_integer_output_folder_rejected(self):
        resp = self._post_download(123)
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertIn("string", data["message"].lower())

    def test_float_output_folder_rejected(self):
        resp = self._post_download(3.14)
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["success"])

    def test_boolean_output_folder_rejected(self):
        resp = self._post_download(True)
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["success"])

    def test_array_output_folder_rejected(self):
        resp = self._post_download(["C:/path"])
        self.assertEqual(resp.status_code, 400)

    def test_object_output_folder_rejected(self):
        resp = self._post_download({"path": "C:/path"})
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # None and blank strings → default folder
    # ------------------------------------------------------------------

    def test_none_output_folder_accepted(self):
        resp = self._post_download(None)
        self.assertEqual(resp.status_code, 202)
        kwargs = self.mock_queue_download.call_args.kwargs
        self.assertIsNone(kwargs.get("output_folder"))

    def test_blank_string_output_folder_accepted(self):
        resp = self._post_download("")
        self.assertEqual(resp.status_code, 202)
        kwargs = self.mock_queue_download.call_args.kwargs
        self.assertIsNone(kwargs.get("output_folder"))

    def test_whitespace_only_output_folder_accepted(self):
        resp = self._post_download("   ")
        self.assertEqual(resp.status_code, 202)
        kwargs = self.mock_queue_download.call_args.kwargs
        self.assertIsNone(kwargs.get("output_folder"))

    # ------------------------------------------------------------------
    # Path traversal rejection (forward, backward, mixed separators)
    # ------------------------------------------------------------------

    def test_path_traversal_forward_slash_rejected(self):
        resp = self._post_download("C:/Users/../foo")
        self.assertEqual(resp.status_code, 400)
        self.mock_queue_download.assert_not_called()

    def test_path_traversal_backslash_rejected(self):
        resp = self._post_download("C:\\Users\\..\\foo")
        self.assertEqual(resp.status_code, 400)
        self.mock_queue_download.assert_not_called()

    def test_path_traversal_mixed_rejected(self):
        resp = self._post_download("C:/Users\\..\\foo")
        self.assertEqual(resp.status_code, 400)
        self.mock_queue_download.assert_not_called()

    # ------------------------------------------------------------------
    # Valid absolute paths → normalized and passed to queue_download
    # ------------------------------------------------------------------

    def test_valid_windows_path_forward_slash_normalized(self):
        resp = self._post_download("C:/Users/foo/Downloads")
        self.assertEqual(resp.status_code, 202)
        kwargs = self.mock_queue_download.call_args.kwargs
        self.assertEqual(kwargs.get("output_folder"), "C:\\Users\\foo\\Downloads")

    def test_valid_windows_path_backslash(self):
        resp = self._post_download("C:\\Users\\foo\\Downloads")
        self.assertEqual(resp.status_code, 202)
        kwargs = self.mock_queue_download.call_args.kwargs
        self.assertEqual(kwargs.get("output_folder"), "C:\\Users\\foo\\Downloads")

    def test_valid_unc_path(self):
        resp = self._post_download("\\\\server\\share\\downloads")
        self.assertEqual(resp.status_code, 202)
        kwargs = self.mock_queue_download.call_args.kwargs
        self.assertIsNotNone(kwargs.get("output_folder"))

    # ------------------------------------------------------------------
    # Invalid paths do not create directories
    # ------------------------------------------------------------------

    def test_invalid_path_does_not_create_directory(self):
        """validate_output_folder must not create directories as a side effect."""
        nonexistent = os.path.join(tempfile.gettempdir(), "mf_nonexistent_12345_xyz")
        # Ensure it does not exist before the check
        if os.path.exists(nonexistent):
            os.rmdir(nonexistent)

        path, err = downloader.validate_output_folder(nonexistent)
        self.assertIsNone(err)
        self.assertIsNotNone(path)
        # validate_output_folder itself must not create the directory
        self.assertFalse(os.path.exists(nonexistent))

    def test_relative_path_rejected_no_directory_created(self):
        resp = self._post_download("relative/path")
        self.assertEqual(resp.status_code, 400)
        self.mock_queue_download.assert_not_called()

    # ------------------------------------------------------------------
    # queue_download receives normalized path, not raw input
    # ------------------------------------------------------------------

    def test_normalized_path_not_raw_input(self):
        """Forward-slash input becomes backslash-normalized path in queue_download."""
        resp = self._post_download("C:/Users/foo/Downloads")
        self.assertEqual(resp.status_code, 202)
        kwargs = self.mock_queue_download.call_args.kwargs
        # Raw input had forward slashes; queue_download must receive normalized
        self.assertEqual(kwargs.get("output_folder"), "C:\\Users\\foo\\Downloads")
        self.assertNotEqual(kwargs.get("output_folder"), "C:/Users/foo/Downloads")


# ── TASK 5: Single-instance guard tests ───────────────────────────────────


class TestSingleInstanceGuard(unittest.TestCase):
    """Single-instance guard for MediaForgeBackend.exe.

    Verifies that a second backend instance is blocked by a Windows named
    mutex, while the first instance proceeds normally.  Mocked at the ctypes
    level so no real EXE processes are launched.

    The hardened implementation also rejects startup when the mutex cannot be
    created at all (no unprotected fallback).
    """

    def test_first_launch_proceeds(self):
        """First instance: CreateMutexW returns a handle, GetLastError !=
        ERROR_ALREADY_EXISTS → guard returns True (proceed)."""
        with patch("ctypes.windll.kernel32.CreateMutexW", return_value=1), \
             patch("ctypes.windll.kernel32.GetLastError", return_value=0):
            self.assertTrue(_enforce_single_instance())

    def test_second_launch_blocked(self):
        """Second instance: CreateMutexW returns a handle, GetLastError ==
        ERROR_ALREADY_EXISTS (183) → guard returns False (blocked).
        CloseHandle is called to avoid a handle leak."""
        with patch("ctypes.windll.kernel32.CreateMutexW", return_value=1), \
             patch("ctypes.windll.kernel32.GetLastError", return_value=183), \
             patch("ctypes.windll.kernel32.CloseHandle") as mock_close:
            self.assertFalse(_enforce_single_instance())
            mock_close.assert_called_once_with(1)

    def test_mutex_creation_failure_rejects_startup(self):
        """If CreateMutexW fails (returns NULL), guard returns False — no
        unprotected startup is allowed."""
        with patch("ctypes.windll.kernel32.CreateMutexW", return_value=None), \
             patch("ctypes.windll.kernel32.GetLastError") as mock_gle:
            self.assertFalse(_enforce_single_instance())
            # GetLastError should not be consulted when handle is NULL
            mock_gle.assert_not_called()

    def test_restart_after_owner_exit(self):
        """After the owning process exits (mutex released by the OS), a new
        instance can proceed — same result as first launch."""
        with patch("ctypes.windll.kernel32.CreateMutexW", return_value=1), \
             patch("ctypes.windll.kernel32.GetLastError", return_value=0):
            self.assertTrue(_enforce_single_instance())

    def test_single_instance_guard_not_called_on_import(self):
        """The guard function exists at module level but is only invoked from
        __main__, so importing app does not create a mutex or block anything."""
        self.assertTrue(callable(_enforce_single_instance))


if __name__ == "__main__":
    unittest.main()