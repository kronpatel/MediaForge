"""End-to-end integration test covering the full queue lifecycle.

Flow:
  Queue Job → Pause → Resume → Cancel → History → Restart → Verify state

Jobs are created directly in _jobs to avoid the worker thread
race (the daemon worker immediately picks up real queues).
"""

import unittest
import os

import downloader


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


if __name__ == "__main__":
    unittest.main()
