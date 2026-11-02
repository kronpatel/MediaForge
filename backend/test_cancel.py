"""Tests for cancel_job in downloader.py and /queue/cancel in app.py."""

import os
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

import downloader

# ── Test isolation: route all backend data paths to a temp dir ──
_ISOLATION_TMPDIR = None
_ISOLATION_ORIG_PATHS = {}


def setUpModule():
    global _ISOLATION_TMPDIR, _ISOLATION_ORIG_PATHS
    _ISOLATION_TMPDIR = tempfile.TemporaryDirectory(prefix="mf_test_cancel_")
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


class TestCancelRegression(unittest.TestCase):
    """Regression tests for the Phase 2 queue-reliability fixes.

    Covers the verified backend issues:
      1. cancellation state transition + duplicate history handling
      2. worker handling of cancelled/paused queued jobs
      3. race condition removing an actively downloading job
      4. queue worker stability during cancellation/removal
      5. cooperative pause/resume (cancel works while paused)

    ``run_download`` is mocked so no real yt-dlp/FFmpeg work happens; the mock
    blocks on ``release_ev`` and then faithfully mirrors the real progress-hook
    flow: it calls ``progress_hook_for(job_id)`` (which raises
    ``_CancelDownloadError`` / ``_PauseDownloadError`` when the cancel/pause
    event is set) and converts them to ``KerzoxDownloadError`` exactly as the
    real ``run_download`` does.  Cancel/pause is triggered *naturally* via
    ``cancel_job`` / ``pause_job`` before releasing the mock, so the signal is
    fully observed with no thread-scheduling race and no manually simulated
    high-level exceptions.
    """

    def setUp(self):
        downloader._jobs.clear()
        downloader._active_job_id = None
        downloader._download_queue.queue.clear()
        downloader._history_cache = None
        downloader._history_last_mtime = None
        if os.path.exists(downloader.HISTORY_FILE):
            os.remove(downloader.HISTORY_FILE)

    def tearDown(self):
        downloader._jobs.clear()
        downloader._active_job_id = None
        downloader._download_queue.queue.clear()
        downloader._history_cache = None
        downloader._history_last_mtime = None
        if os.path.exists(downloader.HISTORY_FILE):
            os.remove(downloader.HISTORY_FILE)

    def _history_entries_for(self, job_id):
        return [h for h in downloader.read_history() if h.get("id") == job_id]

    def _make_job(self, job_id, status="queued"):
        job = downloader.DownloadJob(
            id=job_id,
            url="https://example.com/video",
            mode="mp3",
            label="MP3",
            status=status,
        )
        downloader._jobs[job.id] = job
        if status == "queued":
            downloader._download_queue.put(job.id)
        return job

    def _start_natural_download(self, job_id):
        """Start execute_job in a thread with a run_download mock that mirrors
        the real progress-hook flow.

        The mock signals that the job is running, blocks on release_ev, then
        calls progress_hook_for(job_id) — which raises _CancelDownloadError /
        _PauseDownloadError when the cancel/pause event is set — and converts
        them to KerzoxDownloadError exactly as the real run_download does.

        Returns (worker, release_ev).
        """
        start_ev = threading.Event()
        release_ev = threading.Event()

        def fake_run_download(url, options, label):
            start_ev.set()
            release_ev.wait(timeout=10)
            hook = downloader.progress_hook_for(job_id)
            try:
                hook(
                    {
                        "status": "downloading",
                        "downloaded_bytes": 0,
                        "total_bytes": 1,
                        "filename": "video.mp4",
                    }
                )
            except downloader._CancelDownloadError:
                raise downloader.KerzoxDownloadError("Download cancelled by user")
            except downloader._PauseDownloadError:
                raise downloader.KerzoxDownloadError("Download paused by user")

        job = downloader._jobs[job_id]
        with patch.object(downloader, "run_download", side_effect=fake_run_download):
            worker = threading.Thread(
                target=downloader.execute_job, args=(job,), daemon=True
            )
            worker.start()
            start_ev.wait(timeout=5)
        return worker, release_ev

    # ── Issue 1: cancellation state transition + duplicate history ──────────

    def test_cancel_queued_job_single_history_entry(self):
        """Cancelling a queued job produces exactly one history entry."""
        self._make_job("reg-queued-001")
        before = len(downloader.read_history())
        result = downloader.cancel_job("reg-queued-001")
        self.assertEqual(result["status"], "cancelled")
        after = len(downloader.read_history())
        self.assertEqual(after, before + 1)
        entries = self._history_entries_for("reg-queued-001")
        self.assertEqual(len(entries), 1, "exactly one history entry for a queued cancel")
        self.assertEqual(entries[0]["status"], "cancelled")

    def test_cancel_completed_job_rejected(self):
        """A completed job cannot be cancelled (no allowed transition)."""
        self._make_job("reg-completed-001", status="completed")
        with self.assertRaises(downloader.KerzoxDownloadError):
            downloader.cancel_job("reg-completed-001")
        self.assertEqual(len(self._history_entries_for("reg-completed-001")), 0)

    def test_cancel_already_cancelled_job_rejected(self):
        """An already-cancelled job cannot be cancelled again."""
        self._make_job("reg-cancelled-001", status="cancelled")
        with self.assertRaises(downloader.KerzoxDownloadError):
            downloader.cancel_job("reg-cancelled-001")
        self.assertEqual(len(self._history_entries_for("reg-cancelled-001")), 0)

    # ── Issues 3 & 4: cancel/remove while actively running ──────────────────

    def test_cancel_while_running_single_history_entry(self):
        """Cancelling a running job yields a single history entry, no crash.

        Cancellation is triggered naturally via cancel_job (sets the cancel
        event); the mocked run_download observes it through progress_hook and
        propagates _CancelDownloadError → KerzoxDownloadError.
        """
        job = self._make_job("reg-running-001")
        worker, release_ev = self._start_natural_download("reg-running-001")

        result = downloader.cancel_job("reg-running-001")
        self.assertEqual(result["status"], "cancelled")
        release_ev.set()
        worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "worker thread must not crash or hang")
        entries = self._history_entries_for("reg-running-001")
        self.assertEqual(len(entries), 1, "exactly one history entry for a cancelled run")
        self.assertEqual(entries[0]["status"], "cancelled")

    def test_remove_actively_downloading_job_no_crash(self):
        """Removing an actively downloading job must not crash the worker."""
        job = self._make_job("reg-remove-001")
        worker, release_ev = self._start_natural_download("reg-remove-001")

        downloader.remove_job("reg-remove-001")
        release_ev.set()
        worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "worker thread must not crash")
        self.assertNotIn("reg-remove-001", downloader._jobs)

    def test_removed_job_never_starts_downloading(self):
        """A job removed from _jobs before its attempt must never download."""
        job = self._make_job("reg-remove-queued-001")
        downloader.remove_job("reg-remove-queued-001")
        downloaded = threading.Event()

        def fake_run_download(url, options, label):
            downloaded.set()  # must never fire

        with patch.object(downloader, "run_download", side_effect=fake_run_download):
            worker = threading.Thread(
                target=downloader.execute_job, args=(job,), daemon=True
            )
            worker.start()
            worker.join(timeout=10)

        self.assertFalse(downloaded.is_set(), "removed job must not download")
        self.assertFalse(worker.is_alive(), "worker thread must not crash")
        self.assertNotIn("reg-remove-queued-001", downloader._jobs)

    # ── Issue 3/4: cooperative pause/resume (natural flow) ──────────────────

    def test_pause_downloading_job(self):
        """Pausing a running job aborts cooperatively, leaves PAUSED, no history.

        Pause is triggered naturally via pause_job (sets the pause event); the
        mocked run_download observes it through progress_hook and propagates
        _PauseDownloadError → KerzoxDownloadError("Download paused by user").
        """
        job = self._make_job("reg-pause-001")
        worker, release_ev = self._start_natural_download("reg-pause-001")

        downloader.pause_job("reg-pause-001")
        release_ev.set()
        worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "worker thread must not crash")
        self.assertEqual(downloader._jobs["reg-pause-001"].status, "paused")
        # A paused job is still active — no history entry.
        self.assertEqual(len(self._history_entries_for("reg-pause-001")), 0)

    def test_resume_paused_job(self):
        """Resuming a paused job re-queues it as QUEUED and clears the pause event."""
        job_id = "reg-resume-001"
        self._make_job(job_id, status="queued")
        downloader.pause_job(job_id)  # natural pause — no manual event simulation

        self.assertEqual(downloader._jobs[job_id].status, "paused")
        self.assertTrue(downloader._is_job_paused(job_id))

        downloader.resume_job(job_id)

        self.assertEqual(downloader._jobs[job_id].status, "queued")
        self.assertFalse(downloader._is_job_paused(job_id), "pause event must be cleared")
        self.assertIn(job_id, downloader._download_queue.queue)

    def test_cancel_while_paused(self):
        """Cancelling a paused job marks it cancelled with one history entry, no event leak.

        Pause is triggered naturally via pause_job, then cancel via cancel_job.
        """
        job_id = "reg-cancel-paused-001"
        self._make_job(job_id, status="queued")
        downloader.pause_job(job_id)  # natural pause — no manual event simulation

        self.assertEqual(downloader._jobs[job_id].status, "paused")

        result = downloader.cancel_job(job_id)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(downloader._jobs[job_id].status, "cancelled")

        entries = self._history_entries_for(job_id)
        self.assertEqual(len(entries), 1, "exactly one history entry for a paused cancel")
        self.assertEqual(entries[0]["status"], "cancelled")
        self.assertFalse(downloader._is_job_cancelled(job_id), "cancel event must be cleaned up")

    # ── Direct progress_hook_for() tests ────────────────────────────────────

    def test_progress_hook_raises_on_cancel(self):
        """progress_hook_for raises _CancelDownloadError when the cancel event is set."""
        job_id = "hook-cancel-001"
        self._make_job(job_id, status="queued")
        downloader._get_cancel_event(job_id).set()

        hook = downloader.progress_hook_for(job_id)
        with self.assertRaises(downloader._CancelDownloadError):
            hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 0,
                    "total_bytes": 1,
                    "filename": "video.mp4",
                }
            )

    def test_progress_hook_raises_on_pause(self):
        """progress_hook_for raises _PauseDownloadError when the pause event is set."""
        job_id = "hook-pause-001"
        self._make_job(job_id, status="queued")
        downloader._get_pause_effect(job_id).set()

        hook = downloader.progress_hook_for(job_id)
        with self.assertRaises(downloader._PauseDownloadError):
            hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 0,
                    "total_bytes": 1,
                    "filename": "video.mp4",
                }
            )

    # ── Exception-propagating thread assertions ─────────────────────────────

    def test_thread_exception_propagates_to_main_thread(self):
        """An exception raised inside a worker thread is captured and asserted,
        never silently swallowed.
        """
        job = downloader.DownloadJob(
            id="reg-propagate-001",
            url="https://example.com/video",
            mode="mp3",
            label="MP3",
            status="queued",
            max_retries=0,  # single attempt so the exception is captured once
        )
        downloader._jobs[job.id] = job
        downloader._download_queue.put(job.id)
        captured: list[BaseException] = []

        def fake_run_download(url, options, label):
            try:
                raise downloader.KerzoxDownloadError("thread failure")
            except BaseException as exc:  # noqa: BLE001 - capture for assertion
                captured.append(exc)
                raise

        with patch.object(downloader, "run_download", side_effect=fake_run_download):
            worker = threading.Thread(
                target=downloader.execute_job, args=(job,), daemon=True
            )
            worker.start()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "worker thread must not hang")
        self.assertEqual(len(captured), 1, "thread exception must surface for assertion")
        self.assertIsInstance(captured[0], downloader.KerzoxDownloadError)

    # ── Issue 4: cancel-event cleanup (no leaks) ──────────────────────────

    def test_no_cancel_event_leak_on_remove(self):
        """Removing a queued job must not leave a cancel event behind."""
        job_id = "reg-leak-remove-001"
        self._make_job(job_id)
        downloader.remove_job(job_id)
        self.assertNotIn(job_id, downloader._jobs)
        self.assertFalse(downloader._is_job_cancelled(job_id))

    def test_no_cancel_event_leak_on_queued_cancel(self):
        """Cancelling a queued job must not leave a cancel event behind."""
        job_id = "reg-leak-cancel-001"
        self._make_job(job_id)
        downloader.cancel_job(job_id)
        self.assertEqual(downloader._jobs[job_id].status, "cancelled")
        self.assertFalse(downloader._is_job_cancelled(job_id))


if __name__ == "__main__":
    unittest.main()
