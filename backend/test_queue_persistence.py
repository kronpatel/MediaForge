"""Tests for queue persistence (save/restore cycle) and recovery manager."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import downloader
from downloader import (
    DownloadJob, QUEUE_STATE_FILE, QUEUE_STATE_SCHEMA_VERSION,
    save_queue_state, _restore_queue_state, _handle_corrupted_queue_state,
    _job_to_dict, _dict_to_job,
)
import recovery


class TestJobSerialization(unittest.TestCase):
    def test_job_to_dict_roundtrip(self):
        job = DownloadJob(
            id="test-123",
            url="https://example.com/video",
            mode="mp3",
            label="MP3",
            status="queued",
            progress=50.0,
            max_retries=3,
            queued_at="2026-01-01T00:00:00",
        )
        data = _job_to_dict(job)
        restored = _dict_to_job(data)
        self.assertEqual(restored.id, job.id)
        self.assertEqual(restored.url, job.url)
        self.assertEqual(restored.mode, job.mode)
        self.assertEqual(restored.status, "queued")
        self.assertEqual(restored.progress, 50.0)
        self.assertEqual(restored.max_retries, 3)

    def test_dict_to_job_ignores_extra_fields(self):
        data = {"id": "x", "url": "u", "mode": "m", "label": "L", "nonexistent": True}
        job = _dict_to_job(data)
        self.assertEqual(job.id, "x")
        self.assertFalse(hasattr(job, "nonexistent"))


class TestSaveRestoreCycle(unittest.TestCase):
    def setUp(self):
        downloader._jobs.clear()
        downloader._active_job_id = None
        downloader._download_queue.queue.clear()
        self._remove_state_file()

    def _remove_state_file(self):
        for path in (QUEUE_STATE_FILE, QUEUE_STATE_FILE + ".tmp",
                     QUEUE_STATE_FILE + ".corrupt"):
            if os.path.exists(path):
                os.remove(path)

    def _add_job(self, job_id, status="queued"):
        job = DownloadJob(
            id=job_id, url="https://example.com/v",
            mode="mp3", label="MP3", status=status,
        )
        downloader._jobs[job.id] = job
        if status == "queued":
            downloader._download_queue.put(job.id)
        return job

    def test_save_and_restore_empty_queue(self):
        save_queue_state()
        self.assertTrue(os.path.exists(QUEUE_STATE_FILE))

        downloader._jobs.clear()
        count = _restore_queue_state()
        self.assertEqual(count, 0)

    def test_save_and_restore_single_job(self):
        self._add_job("job-001")
        save_queue_state()

        downloader._jobs.clear()
        downloader._active_job_id = None
        count = _restore_queue_state()
        self.assertEqual(count, 1)
        self.assertIn("job-001", downloader._jobs)
        self.assertEqual(downloader._jobs["job-001"].status, "queued")

    def test_save_and_restore_multiple_jobs(self):
        self._add_job("job-a")
        self._add_job("job-b")
        self._add_job("job-c")
        save_queue_state()

        downloader._jobs.clear()
        count = _restore_queue_state()
        self.assertEqual(count, 3)
        for jid in ("job-a", "job-b", "job-c"):
            self.assertIn(jid, downloader._jobs)

    def test_restore_preserves_queue_order(self):
        self._add_job("job-1")
        self._add_job("job-2")
        self._add_job("job-3")
        save_queue_state()

        downloader._jobs.clear()
        downloader._download_queue.queue.clear()
        _restore_queue_state()

        restored_order = list(downloader._download_queue.queue)
        self.assertEqual(restored_order, ["job-1", "job-2", "job-3"])

    def test_restore_preserves_active_job(self):
        j = self._add_job("active-001", status="downloading")
        downloader._active_job_id = "active-001"
        save_queue_state()

        downloader._jobs.clear()
        downloader._active_job_id = None
        _restore_queue_state()
        self.assertEqual(downloader._active_job_id, "active-001")

    def test_restore_with_metadata(self):
        j = self._add_job("meta-001", status="paused")
        j.progress = 42.5
        j.attempts = 2
        j.max_retries = 5
        j.message = "Priority: high"
        save_queue_state()

        downloader._jobs.clear()
        _restore_queue_state()
        restored = downloader._jobs["meta-001"]
        self.assertEqual(restored.progress, 42.5)
        self.assertEqual(restored.attempts, 2)
        self.assertEqual(restored.max_retries, 5)
        self.assertEqual(restored.message, "Priority: high")

    def test_schema_version_check(self):
        self._add_job("schema-test")
        save_queue_state()

        with open(QUEUE_STATE_FILE, "r") as f:
            data = json.load(f)
        data["schema_version"] = 999
        with open(QUEUE_STATE_FILE, "w") as f:
            json.dump(data, f)

        downloader._jobs.clear()
        count = _restore_queue_state()
        self.assertEqual(count, 0, "Should not restore unknown schema")

    def test_corrupted_state_file_recovery(self):
        with open(QUEUE_STATE_FILE, "w") as f:
            f.write("not valid json")
        count = _restore_queue_state()
        self.assertEqual(count, 0)
        self.assertTrue(os.path.exists(QUEUE_STATE_FILE + ".corrupt"))

    def test_atomic_write_on_save(self):
        self._add_job("atomic-test")
        save_queue_state()
        self.assertTrue(os.path.exists(QUEUE_STATE_FILE))
        self.assertFalse(os.path.exists(QUEUE_STATE_FILE + ".tmp"))

    def tearDown(self):
        downloader._jobs.clear()
        downloader._active_job_id = None
        downloader._download_queue.queue.clear()
        self._remove_state_file()


class TestRecoveryManager(unittest.TestCase):
    def setUp(self):
        downloader._jobs.clear()
        downloader._active_job_id = None
        downloader._download_queue.queue.clear()
        for path in (QUEUE_STATE_FILE, QUEUE_STATE_FILE + ".tmp",
                     QUEUE_STATE_FILE + ".corrupt"):
            if os.path.exists(path):
                os.remove(path)

    def _add_job(self, job_id, status="queued"):
        job = DownloadJob(
            id=job_id, url="https://example.com/v",
            mode="mp3", label="MP3", status=status,
        )
        downloader._jobs[job.id] = job
        if status == "queued":
            downloader._download_queue.put(job.id)
        return job

    def test_recover_downloading_job_to_queued(self):
        self._add_job("recover-001", status="downloading")
        downloader._active_job_id = "recover-001"
        downloader.save_queue_state()

        downloader._jobs.clear()
        downloader._active_job_id = None
        result = recovery.recover_queue()
        self.assertGreaterEqual(result.jobs_restored, 1)
        self.assertGreaterEqual(result.jobs_recovered, 1)
        self.assertIn("recover-001", downloader._jobs)
        self.assertEqual(downloader._jobs["recover-001"].status, "queued")

    def test_recover_paused_job_to_queued(self):
        self._add_job("recover-paused", status="paused")
        downloader.save_queue_state()

        downloader._jobs.clear()
        result = recovery.recover_queue()
        self.assertGreaterEqual(result.jobs_restored, 1)
        self.assertGreaterEqual(result.jobs_recovered, 1)
        self.assertEqual(downloader._jobs["recover-paused"].status, "queued")

    def test_completed_job_not_recovered(self):
        self._add_job("done-job", status="completed")
        downloader.save_queue_state()

        downloader._jobs.clear()
        result = recovery.recover_queue()
        self.assertGreaterEqual(result.jobs_restored, 1)
        completed = [j for j in downloader._jobs.values() if j.status == "completed"]
        self.assertEqual(len(completed), 1)

    def test_corrupted_state_returns_zero(self):
        with open(QUEUE_STATE_FILE, "w") as f:
            f.write("{corrupt json")
        result = recovery.recover_queue()
        self.assertEqual(result.jobs_restored, 0)

    def test_recovery_result_has_all_fields(self):
        result = recovery.RecoveryResult()
        self.assertTrue(hasattr(result, "jobs_restored"))
        self.assertTrue(hasattr(result, "jobs_recovered"))
        self.assertTrue(hasattr(result, "files_cleaned"))
        self.assertTrue(hasattr(result, "history_consistent"))
        self.assertTrue(hasattr(result, "errors"))

    def test_cleanup_temp_files(self):
        download_dir = downloader.get_download_dir()

        # Files matching the yt-dlp output pattern SHOULD be cleaned
        mf_part = os.path.join(download_dir, "_test [abc123].mp4.part")
        mf_tmp = os.path.join(download_dir, "_test [abc123].mp4.tmp")

        # Non-MediaForge temp files (e.g. Firefox .part, IDM .tmp)
        # MUST survive cleanup
        foreign_part = os.path.join(download_dir, "_test_firefox_download.part")
        foreign_tmp = os.path.join(download_dir, "_test_ldm_download.tmp")

        try:
            for path in (mf_part, mf_tmp, foreign_part, foreign_tmp):
                with open(path, "w") as f:
                    f.write("x")

            cleaned = downloader.cleanup_temp_files()
            self.assertGreaterEqual(cleaned, 2)
            self.assertFalse(os.path.exists(mf_part))
            self.assertFalse(os.path.exists(mf_tmp))
            # Foreign files must not be touched
            self.assertTrue(os.path.exists(foreign_part))
            self.assertTrue(os.path.exists(foreign_tmp))
        finally:
            for p in (mf_part, mf_tmp, foreign_part, foreign_tmp):
                if os.path.exists(p):
                    os.remove(p)

    def test_validate_history_consistency(self):
        if os.path.exists(downloader.HISTORY_FILE):
            os.remove(downloader.HISTORY_FILE)

        with open(downloader.HISTORY_FILE, "w") as f:
            f.write(json.dumps({"id": "dup1", "status": "completed"}) + "\n")
            f.write(json.dumps({"id": "dup1", "status": "completed"}) + "\n")
            f.write(json.dumps({"id": "unique", "status": "failed"}) + "\n")
            f.write("not json\n")

        valid, dupes = recovery.validate_history_consistency()
        self.assertTrue(valid)
        self.assertEqual(dupes, 2)

        with open(downloader.HISTORY_FILE, "r") as f:
            lines = [l.strip() for l in f if l.strip()]
        self.assertEqual(len(lines), 2)

    def test_has_corrupted_queue_state(self):
        self.assertFalse(recovery.has_corrupted_queue_state())
        with open(QUEUE_STATE_FILE, "w") as f:
            f.write("{{{")
        self.assertTrue(recovery.has_corrupted_queue_state())

    def tearDown(self):
        downloader._jobs.clear()
        downloader._active_job_id = None
        downloader._download_queue.queue.clear()
        for path in (QUEUE_STATE_FILE, QUEUE_STATE_FILE + ".tmp",
                     QUEUE_STATE_FILE + ".corrupt"):
            if os.path.exists(path):
                os.remove(path)
        if os.path.exists(downloader.HISTORY_FILE):
            os.remove(downloader.HISTORY_FILE)


if __name__ == "__main__":
    unittest.main()
