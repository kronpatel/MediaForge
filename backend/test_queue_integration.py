"""End-to-end integration test covering the full queue lifecycle.

Flow:
  Queue Job → Pause → Resume → Cancel → History → Restart → Verify state

Jobs are created directly in _jobs to avoid the worker thread
race (the daemon worker immediately picks up real queues).
"""

import os
import tempfile
import unittest
from unittest.mock import patch

import downloader

# ── Test isolation: route all backend data paths to a temp dir ──
_ISOLATION_TMPDIR = None
_ISOLATION_ORIG_PATHS = {}


def setUpModule():
    global _ISOLATION_TMPDIR, _ISOLATION_ORIG_PATHS
    _ISOLATION_TMPDIR = tempfile.TemporaryDirectory(prefix="mf_test_qi_")
    _tmp = _ISOLATION_TMPDIR.name
    _ISOLATION_ORIG_PATHS = {
        "BASE_DIR": downloader.BASE_DIR,
        "QUEUE_STATE_FILE": downloader.QUEUE_STATE_FILE,
        "HISTORY_FILE": downloader.HISTORY_FILE,
        "SETTINGS_FILE": downloader.SETTINGS_FILE,
        "DOWNLOAD_DIR": downloader.DOWNLOAD_DIR,
    }
    downloader.BASE_DIR = _tmp
    downloader.QUEUE_STATE_FILE = os.path.join(_tmp, "queue_state.json")
    downloader.HISTORY_FILE = os.path.join(_tmp, "download_history.jsonl")
    downloader.SETTINGS_FILE = os.path.join(_tmp, "settings.json")
    downloader.DOWNLOAD_DIR = os.path.join(_tmp, "Downloads")
    os.makedirs(downloader.DOWNLOAD_DIR, exist_ok=True)


def tearDownModule():
    global _ISOLATION_TMPDIR
    for _k, _v in _ISOLATION_ORIG_PATHS.items():
        setattr(downloader, _k, _v)
    if _ISOLATION_TMPDIR is not None:
        _ISOLATION_TMPDIR.cleanup()
        _ISOLATION_TMPDIR = None


class TestQueueLifecycleIntegration(unittest.TestCase):
    def setUp(self):
        downloader._jobs.clear()
        downloader._active_job_id = None
        downloader._history_cache = None
        downloader._history_last_mtime = None

    def _make_job(self, job_id="lifecycle-id", status="queued") -> downloader.DownloadJob:
        job = downloader.DownloadJob(
            id=job_id,
            url="https://example.com/video",
            mode="mp3",
            label="MP3",
            status=status,
        )
        if status == "queued":
            downloader._download_queue.put(job_id)
        downloader._jobs[job.id] = job
        return job

    def _clean_history_file(self):
        if os.path.exists(downloader.HISTORY_FILE):
            os.remove(downloader.HISTORY_FILE)
        downloader._history_cache = None
        downloader._history_last_mtime = None

    def test_full_lifecycle(self):
        self._clean_history_file()

        JOB_ID = "e2e-lifecycle-001"

        # ── Step 1: Queue a job ──────────────────────────────────────────
        job = self._make_job(JOB_ID)
        status = downloader.get_download_status(JOB_ID)
        self.assertEqual(status["status"], "queued")
        self.assertEqual(status["mode"], "mp3")

        # ── Step 2: Pause ────────────────────────────────────────────────
        downloader.pause_job(JOB_ID)
        status = downloader.get_download_status(JOB_ID)
        self.assertEqual(status["status"], "paused")
        self.assertEqual(status["message"], "Paused")

        # ── Step 3: Resume ───────────────────────────────────────────────
        downloader.resume_job(JOB_ID)
        status = downloader.get_download_status(JOB_ID)
        self.assertEqual(status["status"], "queued")
        self.assertIn("Re-queued", status["message"])

        # ── Step 4: Cancel ───────────────────────────────────────────────
        result = downloader.cancel_job(JOB_ID)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["message"], "Cancelled by user")
        self.assertNotEqual(result["completed_at"], "")

        # ── Step 5: History contains cancelled job ───────────────────────
        history = downloader.read_history()
        ids_in_history = [h["id"] for h in history]
        self.assertIn(JOB_ID, ids_in_history)

        cancelled = [h for h in history if h["id"] == JOB_ID]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["status"], "cancelled")

        # ── Step 6: Restart (simulate Companion restart) ─────────────────
        downloader._jobs.clear()
        downloader._active_job_id = None

        # In-memory queue state is lost after restart (intentional design)
        queue_status = downloader.get_queue_status()
        self.assertIsNone(queue_status["active"])
        self.assertEqual(queue_status["queued_count"], 0)

        # ── Step 7: History survives restart ─────────────────────────────
        history_after = downloader.read_history()
        ids_after = [h["id"] for h in history_after]
        self.assertIn(JOB_ID, ids_after)
        cancelled_after = [h for h in history_after if h["id"] == JOB_ID]
        self.assertEqual(len(cancelled_after), 1)
        self.assertEqual(cancelled_after[0]["status"], "cancelled")

    def test_priority_metadata_preserved(self):
        """Verify priority metadata stores and restores correctly."""
        self._clean_history_file()

        job = self._make_job("priority-test-001")
        downloader.set_job_priority("priority-test-001", "high")

        status = downloader.get_download_status("priority-test-001")
        self.assertIn("Priority: high", status["message"])

        # Change to low
        downloader.set_job_priority("priority-test-001", "low")
        status = downloader.get_download_status("priority-test-001")
        self.assertIn("Priority: low", status["message"])

    def test_queue_ordering_restored(self):
        """Verify queue ordering after reorder operations (in-memory only)."""
        j1 = self._make_job("order-1")
        j2 = self._make_job("order-2")
        j3 = self._make_job("order-3")
        j4 = self._make_job("order-4")

        # Move last to first
        downloader.set_job_position("order-4", 0)

        ordered_ids = list(downloader._jobs.keys())
        self.assertEqual(ordered_ids[0], "order-4")
        self.assertEqual(ordered_ids[-1], "order-3")

    def test_cancelled_job_restores_correctly(self):
        """Cancelled jobs are persisted in history."""
        self._clean_history_file()

        self._make_job("cancel-restore-001", status="queued")
        downloader.cancel_job("cancel-restore-001")

        history = downloader.read_history()
        cancelled = [h for h in history if h["id"] == "cancel-restore-001"]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["status"], "cancelled")
        self.assertNotEqual(cancelled[0]["completed_at"], "")

    def test_runtime_only_queue_cleared_on_restart(self):
        """In-memory queue is intentionally runtime-only — verify it clears."""
        self._make_job("runtime-001")
        self._make_job("runtime-002")

        self.assertIn("runtime-001", downloader._jobs)
        self.assertIn("runtime-002", downloader._jobs)

        # Simulate restart
        downloader._jobs.clear()
        downloader._active_job_id = None

        self.assertEqual(len(downloader._jobs), 0)
        self.assertIsNone(downloader._active_job_id)

    def tearDown(self):
        downloader._jobs.clear()
        downloader._active_job_id = None
        self._clean_history_file()


class TestValidateOutputFolder(unittest.TestCase):
    """Tests for validate_output_folder() — Correction 1."""

    def test_none_returns_default(self):
        """None output_folder → (None, None), caller uses global default."""
        path, err = downloader.validate_output_folder(None)
        self.assertIsNone(path)
        self.assertIsNone(err)

    def test_empty_string_returns_default(self):
        """Empty string → (None, None), caller uses global default."""
        path, err = downloader.validate_output_folder("")
        self.assertIsNone(path)
        self.assertIsNone(err)

    def test_whitespace_only_returns_default(self):
        """Whitespace-only string → (None, None), caller uses global default."""
        path, err = downloader.validate_output_folder("   ")
        self.assertIsNone(path)
        self.assertIsNone(err)

    def test_non_string_rejected(self):
        """Non-string JSON values (int, bool, list) rejected safely, no crash."""
        for bad in [123, True, False, ["C:/path"], {"path": "C:/path"}, 3.14]:
            path, err = downloader.validate_output_folder(bad)
            self.assertIsNone(path, f"Expected None path for {bad!r}, got {path!r}")
            self.assertIsNotNone(err, f"Expected error message for {bad!r}")
            self.assertIn("must be a string", err)

    def test_null_byte_rejected(self):
        """Path containing null byte is rejected."""
        path, err = downloader.validate_output_folder("C:\\Users\\foo\\bar\x00baz")
        self.assertIsNone(path)
        self.assertIsNotNone(err)
        self.assertIn("null bytes", err)

    def test_path_traversal_forward_slash_rejected(self):
        """Path traversal with forward slashes is rejected on Windows."""
        path, err = downloader.validate_output_folder("C:/Users/../foo")
        self.assertIsNone(path)
        self.assertIsNotNone(err)
        self.assertIn("traversal", err)

    def test_path_traversal_backslash_rejected(self):
        """Path traversal with backslashes is rejected on Windows."""
        path, err = downloader.validate_output_folder("C:\\Users\\..\\foo")
        self.assertIsNone(path)
        self.assertIsNotNone(err)
        self.assertIn("traversal", err)

    def test_path_traversal_mixed_separators_rejected(self):
        """Path traversal with mixed separators is rejected."""
        path, err = downloader.validate_output_folder("C:/Users\\..\\foo")
        self.assertIsNone(path)
        self.assertIsNotNone(err)
        self.assertIn("traversal", err)

    def test_relative_path_rejected(self):
        """Relative path is rejected (must be absolute)."""
        path, err = downloader.validate_output_folder("relative/path")
        self.assertIsNone(path)
        self.assertIsNotNone(err)
        self.assertIn("absolute", err)

    def test_valid_windows_path_forward_slash(self):
        """Valid Windows path with forward slashes is normalized and accepted."""
        path, err = downloader.validate_output_folder("C:/Users/foo/Downloads")
        self.assertIsNone(err, f"Unexpected error: {err}")
        self.assertEqual(path, "C:\\Users\\foo\\Downloads")

    def test_valid_windows_path_backslash(self):
        """Valid Windows path with backslashes is accepted as-is."""
        path, err = downloader.validate_output_folder("C:\\Users\\foo\\Downloads")
        self.assertIsNone(err, f"Unexpected error: {err}")
        self.assertEqual(path, "C:\\Users\\foo\\Downloads")

    def test_valid_unc_path(self):
        """Valid UNC path is accepted."""
        path, err = downloader.validate_output_folder("\\\\server\\share\\downloads")
        self.assertIsNone(err, f"Unexpected error: {err}")
        self.assertIsNotNone(path)


class TestOutputFolderNoneRegression(unittest.TestCase):
    """TASK 4: Regression tests for output_folder=None handling in build_output_template,
    base_options, and execute_job.  Ensures older persisted jobs without an
    output_folder remain compatible and do not crash with TypeError."""

    def test_build_output_template_none_download_dir(self):
        """build_output_template(download_dir=None) must not crash with TypeError."""
        tmpl = downloader.build_output_template(download_dir=None)
        self.assertIsInstance(tmpl, str)
        self.assertIn("%(title).180B", tmpl)
        # Should use the global download dir, not crash on None
        expected_dir = downloader.get_download_dir()
        self.assertIn(expected_dir, tmpl)

    def test_build_output_template_explicit_dir(self):
        """build_output_template with explicit custom dir uses that dir."""
        tmpl = downloader.build_output_template(download_dir="C:/Custom/Folder")
        self.assertIsInstance(tmpl, str)
        self.assertIn("%(title).180B", tmpl)
        # os.path.join preserves the input separator style; check the input path
        self.assertIn("C:/Custom/Folder", tmpl)

    def test_build_output_template_playlist_none(self):
        """build_output_template(playlist=True, download_dir=None) must not crash."""
        tmpl = downloader.build_output_template(playlist=True, download_dir=None)
        self.assertIsInstance(tmpl, str)
        self.assertIn("%(playlist_title).180B", tmpl)
        expected_dir = downloader.get_download_dir()
        self.assertIn(expected_dir, tmpl)

    def test_base_options_none_download_dir(self):
        """base_options(download_dir=None) must not crash and uses default dir."""
        opts = downloader.base_options(download_dir=None)
        self.assertIsInstance(opts, dict)
        self.assertIn("outtmpl", opts)
        expected_dir = downloader.get_download_dir()
        self.assertIn(expected_dir, opts["outtmpl"])

    def test_base_options_explicit_download_dir(self):
        """base_options with explicit dir uses that dir in outtmpl."""
        opts = downloader.base_options(download_dir="C:/Custom/Folder")
        self.assertIn("outtmpl", opts)
        # os.path.join preserves the input separator style; check the input path
        self.assertIn("C:/Custom/Folder", opts["outtmpl"])

    def test_base_options_does_not_mutate_global_settings(self):
        """base_options must not call save_settings to change download_folder."""
        with patch.object(downloader, "write_settings") as mock_write:
            downloader.base_options(download_dir="C:/Custom/Folder")
            # write_settings should not be called with download_folder changes
            folder_calls = [
                c for c in mock_write.call_args_list
                if "download_folder" in str(c)
            ]
            self.assertEqual(len(folder_calls), 0)

    def test_execute_job_none_output_folder_no_typeerror(self):
        """execute_job with output_folder=None must not raise TypeError."""
        job = downloader.DownloadJob(
            id="regress-none-001",
            url="https://example.com/video",
            mode="mp3",
            label="MP3",
            status="queued",
            max_retries=0,
        )
        downloader._jobs[job.id] = job

        captured = []

        def fake_run_download(url, options, label):
            captured.append(options)
            return None

        with patch.object(downloader, "run_download", side_effect=fake_run_download):
            # execute_job should not crash with TypeError on None output_folder
            downloader.execute_job(job)

        self.assertEqual(len(captured), 1)
        options = captured[0]
        self.assertIn("outtmpl", options)
        # outtmpl should use the default download dir, not None
        expected_dir = downloader.get_download_dir()
        self.assertIn(expected_dir, options["outtmpl"])
    def test_execute_job_explicit_output_folder(self):
        """execute_job with explicit output_folder uses that folder in outtmpl."""
        job = downloader.DownloadJob(
            id="regress-explicit-001",
            url="https://example.com/video",
            mode="mp3",
            label="MP3",
            status="queued",
            max_retries=0,
            output_folder="C:/Explicit/Folder",
        )
        downloader._jobs[job.id] = job

        captured = []

        def fake_run_download(url, options, label):
            captured.append(options)
            return None

        with patch.object(downloader, "run_download", side_effect=fake_run_download):
            downloader.execute_job(job)

        self.assertEqual(len(captured), 1)
        options = captured[0]
        self.assertIn("outtmpl", options)
        # os.path.join preserves the input separator style; check the input path
        self.assertIn("C:/Explicit/Folder", options["outtmpl"])

    def test_legacy_persisted_job_recovery_without_output_folder(self):
        """TASK 4: A persisted legacy queue-state JSON file without output_folder
        must restore via _restore_queue_state() and execute without TypeError."""
        import json as _json

        tmpdir = tempfile.TemporaryDirectory(prefix="mf_legacy_recovery_")
        try:
            legacy_state_file = os.path.join(tmpdir.name, "queue_state.json")

            # Write an actual legacy queue-state JSON file without output_folder
            legacy_job_dict = {
                "id": "legacy-no-output-folder",
                "url": "https://example.com/video",
                "mode": "mp3",
                "label": "MP3",
                "status": "queued",
                "message": "Waiting in queue",
                "filename": "",
                "error": "",
                "attempts": 0,
                "max_retries": 0,
                "queued_at": "",
                "started_at": "",
                "completed_at": "",
                # NOTE: no "output_folder" key — legacy format
            }
            state = {
                "schema_version": downloader.QUEUE_STATE_SCHEMA_VERSION,
                "active_job_id": None,
                "queue_order": [],
                "jobs": [legacy_job_dict],
                "saved_at": "",
            }
            with open(legacy_state_file, "w", encoding="utf-8") as f:
                _json.dump(state, f)

            # Save original QUEUE_STATE_FILE and redirect to temp file
            orig_queue_state_file = downloader.QUEUE_STATE_FILE
            downloader.QUEUE_STATE_FILE = legacy_state_file

            try:
                # Call the production recovery loader
                restored = downloader._restore_queue_state()
                self.assertEqual(restored, 1,
                                 "Exactly one job should be restored from legacy state")

                # Verify the job is restored with output_folder=None
                job = downloader._jobs.get("legacy-no-output-folder")
                self.assertIsNotNone(job, "Legacy job should be restored")
                self.assertIsNone(job.output_folder,
                                  "Legacy job without output_folder key should default to None")

                # Execute the recovered job with patched run_download
                captured = []

                def fake_run_download(url, options, label):
                    captured.append(options)
                    return None

                with patch.object(downloader, "run_download", side_effect=fake_run_download):
                    downloader.execute_job(job)

                self.assertEqual(len(captured), 1,
                                 "execute_job should call run_download exactly once")
                options = captured[0]
                self.assertIn("outtmpl", options)
                # outtmpl should use the default download directory
                expected_dir = downloader.get_download_dir()
                self.assertIn(expected_dir, options["outtmpl"],
                              "Recovered legacy job should use default download dir")
            finally:
                # Restore original QUEUE_STATE_FILE
                downloader.QUEUE_STATE_FILE = orig_queue_state_file
                # Clean up in-memory jobs
                downloader._jobs.clear()
        finally:
            tmpdir.cleanup()


class TestYouTubeDownloadCompatibility(unittest.TestCase):
    """Regression coverage for YouTube formats that require fallbacks."""

    def test_base_options_does_not_force_low_quality_youtube_client(self):
        """yt-dlp must negotiate clients instead of being pinned to Android."""
        options = downloader.base_options()
        self.assertNotIn("extractor_args", options)

    def test_mp3_format_falls_back_to_progressive_video(self):
        options = downloader.mp3_options()
        self.assertEqual(options["format"], "bestaudio/best")

    def test_video_formats_fall_back_when_separate_streams_missing(self):
        options = downloader.video_options(max_height=1080)
        self.assertEqual(
            options["format"],
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        )


if __name__ == "__main__":
    unittest.main()
