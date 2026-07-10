"""Tests for cancel_job in downloader.py and /queue/cancel in app.py."""

import unittest
from unittest.mock import patch, MagicMock

import downloader


class TestCancelJob(unittest.TestCase):
    def setUp(self):
        downloader._jobs.clear()
        downloader._active_job_id = None
        self.job = downloader.DownloadJob(
            id="test-id",
            url="https://example.com",
            mode="mp3",
            label="MP3",
            status="downloading",
        )
        downloader._jobs[self.job.id] = self.job

    def test_cancel_downloading_job(self):
        result = downloader.cancel_job("test-id")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["message"], "Cancelled by user")

    def test_cancel_queued_job(self):
        self.job.status = "queued"
        result = downloader.cancel_job("test-id")
        self.assertEqual(result["status"], "cancelled")

    def test_cancel_paused_job(self):
        self.job.status = "paused"
        result = downloader.cancel_job("test-id")
        self.assertEqual(result["status"], "cancelled")

    def test_cancel_retrying_job(self):
        self.job.status = "retrying"
        result = downloader.cancel_job("test-id")
        self.assertEqual(result["status"], "cancelled")

    def test_cancel_completed_job_raises(self):
        self.job.status = "completed"
        with self.assertRaises(downloader.KerzoxDownloadError):
            downloader.cancel_job("test-id")

    def test_cancel_failed_job_raises(self):
        self.job.status = "failed"
        with self.assertRaises(downloader.KerzoxDownloadError):
            downloader.cancel_job("test-id")

    def test_cancel_nonexistent_job_raises(self):
        with self.assertRaises(downloader.KerzoxDownloadError):
            downloader.cancel_job("nonexistent")

    def test_cancel_sets_completed_at(self):
        import datetime
        result = downloader.cancel_job("test-id")
        self.assertNotEqual(result["completed_at"], "")

    def test_cancel_appends_to_history(self):
        history_before = downloader.read_history()
        downloader.cancel_job("test-id")
        history_after = downloader.read_history()
        self.assertGreater(len(history_after), len(history_before))

    def test_cancel_running_job(self):
        self.job.status = "running"
        result = downloader.cancel_job("test-id")
        self.assertEqual(result["status"], "cancelled")


class TestCancelJobThreadSafety(unittest.TestCase):
    def setUp(self):
        downloader._jobs.clear()
        downloader._active_job_id = None
        self.job = downloader.DownloadJob(
            id="thread-test-id",
            url="https://example.com",
            mode="mp3",
            label="MP3",
        )
        downloader._jobs[self.job.id] = self.job

    def test_concurrent_cancel_safe(self):
        import concurrent.futures
        def cancel():
            try:
                downloader.cancel_job("thread-test-id")
                return True
            except downloader.KerzoxDownloadError:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(cancel) for _ in range(4)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(sum(results), 1, "Only one cancel should succeed")
        self.assertEqual(downloader._jobs["thread-test-id"].status, "cancelled")


if __name__ == "__main__":
    unittest.main()
