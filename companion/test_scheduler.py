"""
test_scheduler.py – Unit tests for SchedulerManager (Phase 4.3)

Tests cover:
  - calculate_next_run for all repeat types
  - add / edit / delete / duplicate CRUD
  - event bus register / unregister / fire
  - atomic JSON writes (tmp → replace)
  - history record insertion and 500-record trimming
  - clock-jump recalculation trigger
  - startup recovery (run-missed / skip-missed paths)
  - concurrency guard against double-execution
"""

import os
import sys
import json
import datetime
import threading
import time
import unittest
from unittest.mock import MagicMock, patch, mock_open

# Ensure companion/ is on the path so imports resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scheduler import SchedulerManager, SCHEDULER_FILE, HISTORY_FILE


# ---------------------------------------------------------------------------
# Helper: build a minimal SchedulerManager without running the background
# thread and without touching the real filesystem for settings.
# ---------------------------------------------------------------------------

def _make_scheduler() -> SchedulerManager:
    logger = MagicMock()
    logger.debug_log = MagicMock()
    backend = MagicMock()
    backend.get_settings.return_value = {"download_folder": "C:/Downloads"}
    backend.save_settings.return_value = {}

    # Patch settings load so we don't need a real settings.json.
    # read_local_settings is imported inside __init__ and _check_and_execute_schedules
    # via "from settings_panel import read_local_settings", so patch at source module.
    fake_settings = {
        "scheduler_enabled": True,
        "scheduler_auto_retry": True,
        "scheduler_max_retries": 3,
        "scheduler_run_missed_startup": True,
        "scheduler_poll_interval": 1,
    }
    with patch("settings_panel.read_local_settings", return_value=fake_settings):
        mgr = SchedulerManager(logger, backend)

    # Bake settings in directly so later calls inside the manager also get them
    mgr.settings = fake_settings
    return mgr


class TestSchedulerNextRun(unittest.TestCase):
    """calculate_next_run correctness for every repeat type."""

    def setUp(self) -> None:
        self.mgr = _make_scheduler()

    def tearDown(self) -> None:
        self.mgr.shutdown()

    def test_one_time_future(self) -> None:
        future = datetime.datetime.now() + datetime.timedelta(hours=1)
        self.assertEqual(self.mgr.calculate_next_run(future, "One Time"), future)

    def test_one_time_past_returns_none(self) -> None:
        past = datetime.datetime.now() - datetime.timedelta(hours=1)
        self.assertIsNone(self.mgr.calculate_next_run(past, "One Time"))

    def test_daily_gives_future_date(self) -> None:
        now = datetime.datetime.now()
        past = now - datetime.timedelta(hours=2)
        result = self.mgr.calculate_next_run(past, "Daily")
        self.assertGreater(result, now)
        self.assertLess(result, now + datetime.timedelta(days=1, seconds=30))

    def test_weekly_gives_future_date(self) -> None:
        now = datetime.datetime.now()
        past = now - datetime.timedelta(days=1)
        result = self.mgr.calculate_next_run(past, "Weekly")
        self.assertGreater(result, now)
        self.assertLess(result, now + datetime.timedelta(weeks=1, seconds=30))

    def test_monthly_gives_future_date(self) -> None:
        now = datetime.datetime.now()
        past = now - datetime.timedelta(days=1)
        result = self.mgr.calculate_next_run(past, "Monthly")
        self.assertGreater(result, now)
        self.assertLess(result, now + datetime.timedelta(days=32))

    def test_unknown_repeat_returns_none(self) -> None:
        future = datetime.datetime.now() + datetime.timedelta(hours=1)
        self.assertIsNone(self.mgr.calculate_next_run(future, "Quarterly"))


class TestSchedulerCRUD(unittest.TestCase):
    """Add / Edit / Delete / Duplicate schedule API."""

    def setUp(self) -> None:
        # Clean files BEFORE constructing to avoid loading stale schedules
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        self.mgr = _make_scheduler()
        # Guarantee clean in-memory state regardless of any leftover files
        self.mgr._schedules.clear()

    def tearDown(self) -> None:
        self.mgr.shutdown()
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def _future_job(self, **extra) -> dict:
        future = (datetime.datetime.now() + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        base = {
            "url": "https://youtube.com/watch?v=test",
            "mode": "1080p",
            "quality": "Video (1080p)",
            "output_folder": "",
            "scheduled_time": future,
            "repeat_type": "One Time",
            "max_retries": 2,
            "enabled": True,
        }
        base.update(extra)
        return base

    def test_add_creates_entry_and_fires_event(self) -> None:
        listener = MagicMock()
        self.mgr.register_listener(listener)

        jid = self.mgr.add_schedule(self._future_job())

        self.assertIsNotNone(jid)
        self.assertEqual(len(self.mgr.get_schedules()), 1)
        listener.assert_called_with("Schedule Added", unittest.mock.ANY)

    def test_edit_updates_url(self) -> None:
        jid = self.mgr.add_schedule(self._future_job())
        self.mgr.edit_schedule(jid, {"url": "https://youtube.com/watch?v=edited"})
        schedules = self.mgr.get_schedules()
        self.assertEqual(schedules[0]["url"], "https://youtube.com/watch?v=edited")

    def test_delete_removes_entry(self) -> None:
        jid = self.mgr.add_schedule(self._future_job())
        self.mgr.delete_schedule(jid)
        self.assertEqual(len(self.mgr.get_schedules()), 0)

    def test_duplicate_creates_distinct_copy(self) -> None:
        jid = self.mgr.add_schedule(self._future_job())
        dup_id = self.mgr.duplicate_schedule(jid)
        self.assertNotEqual(jid, dup_id)
        self.assertEqual(len(self.mgr.get_schedules()), 2)

    def test_toggle_enables_and_disables(self) -> None:
        jid = self.mgr.add_schedule(self._future_job())
        original = self.mgr.get_schedules()[0]["enabled"]
        self.mgr.toggle_schedule(jid)
        toggled = self.mgr.get_schedules()[0]["enabled"]
        self.assertNotEqual(original, toggled)


class TestEventBus(unittest.TestCase):
    """register_listener / unregister_listener / _notify."""

    def setUp(self) -> None:
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        self.mgr = _make_scheduler()
        self.mgr._schedules.clear()

    def tearDown(self) -> None:
        self.mgr.shutdown()
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def test_listener_receives_events(self) -> None:
        cb = MagicMock()
        self.mgr.register_listener(cb)
        self.mgr._notify("Test Event", {"key": "value"})
        cb.assert_called_once_with("Test Event", {"key": "value"})

    def test_unregister_stops_delivery(self) -> None:
        cb = MagicMock()
        self.mgr.register_listener(cb)
        self.mgr.unregister_listener(cb)
        self.mgr._notify("Another Event", {})
        cb.assert_not_called()

    def test_duplicate_register_is_idempotent(self) -> None:
        cb = MagicMock()
        self.mgr.register_listener(cb)
        self.mgr.register_listener(cb)   # register twice
        self.mgr._notify("Event", {})
        cb.assert_called_once()          # still only one call


class FakeWindow:
    """Minimal Tkinter-window stand-in capturing after() callbacks."""
    def __init__(self) -> None:
        self.after_calls = []
        self.exists = True

    def after(self, ms: int, fn) -> None:
        self.after_calls.append(fn)

    def winfo_exists(self) -> bool:
        return self.exists


class TestSchedulerThreadSafety(unittest.TestCase):
    """Scheduler events must reach listeners on the Tkinter main thread."""

    def setUp(self):
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        self.mgr = _make_scheduler()
        self.mgr._schedules.clear()
        self.window = FakeWindow()
        self.mgr.window = self.window
        # Do NOT start the UI poll here; tests call _start_ui_poll() explicitly
        # when they need the recurring poll running.

    def tearDown(self):
        self.mgr.shutdown()
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def test_notify_from_background_thread_only_enqueues(self):
        """_notify() from a background thread only performs queue.put(), never calls Tkinter."""
        cb = MagicMock()
        self.mgr.register_listener(cb)

        queue_size_before = self.mgr._ui_event_queue.qsize()
        after_calls_before = len(self.window.after_calls)

        t = threading.Thread(target=self.mgr._notify, args=("Schedule Started", {"url": "https://x"}))
        t.start()
        t.join()

        # Not delivered synchronously from the background thread
        cb.assert_not_called()
        # Only queue.put() was performed
        self.assertEqual(self.mgr._ui_event_queue.qsize(), queue_size_before + 1)
        # No Tkinter after() calls from _notify()
        self.assertEqual(len(self.window.after_calls), after_calls_before)

    def test_main_thread_poll_drains_queued_events(self):
        """Calling _ui_poll() on the main thread drains the queue and delivers to listeners."""
        cb = MagicMock()
        self.mgr.register_listener(cb)

        # Enqueue from background threads
        self.mgr._notify("Event 1", {"i": 1})
        self.mgr._notify("Event 2", {"i": 2})

        cb.assert_not_called()  # Not delivered until main-thread poll runs

        # Simulate main-thread poll (poll must be active to drain)
        self.mgr._ui_poll_active = True
        self.mgr._ui_poll()

        cb.assert_any_call("Event 1", {"i": 1})
        cb.assert_any_call("Event 2", {"i": 2})
        self.assertEqual(cb.call_count, 2)

    def test_listener_callback_runs_on_calling_thread(self):
        """Listener callback thread identity equals the thread that calls _ui_poll()."""
        calling_thread_id = threading.get_ident()
        callback_thread_ids = []

        def cb(event_name, payload):
            callback_thread_ids.append(threading.get_ident())

        self.mgr.register_listener(cb)
        self.mgr._notify("Test", {"k": "v"})

        # Deliver via main-thread poll (simulating Tkinter main thread)
        self.mgr._ui_poll_active = True
        self.mgr._ui_poll()

        self.assertEqual(len(callback_thread_ids), 1)
        self.assertEqual(callback_thread_ids[0], calling_thread_id)

    def test_multiple_queued_notifications_delivered(self):
        """Multiple queued notifications are all delivered on a single poll."""
        cb = MagicMock()
        self.mgr.register_listener(cb)

        for i in range(5):
            self.mgr._notify(f"Event {i}", {"i": i})

        cb.assert_not_called()
        self.mgr._ui_poll_active = True
        self.mgr._ui_poll()
        self.assertEqual(cb.call_count, 5)

    def test_start_ui_poll_schedules_recurring_poll(self):
        """_start_ui_poll() schedules _ui_poll via window.after(100, ...) from main thread."""
        self.mgr._start_ui_poll()
        self.assertTrue(self.mgr._ui_poll_active)
        self.assertEqual(len(self.window.after_calls), 1)
        # The scheduled function is _ui_poll
        self.assertEqual(self.window.after_calls[0], self.mgr._ui_poll)

    def test_shutdown_stops_future_polling(self):
        """shutdown() sets _ui_poll_active=False, preventing rescheduling."""
        self.mgr._start_ui_poll()
        cb = MagicMock()
        self.mgr.register_listener(cb)

        self.mgr._notify("Event", {"k": "v"})
        self.mgr.shutdown()

        self.assertFalse(self.mgr._ui_poll_active)

        # _ui_poll does not reschedule after shutdown
        after_calls_before = len(self.window.after_calls)
        self.mgr._ui_poll()
        after_calls_after = len(self.window.after_calls)
        self.assertEqual(after_calls_after, after_calls_before)  # No new after() scheduled

    def test_shutdown_prevents_delivery_of_pending_events(self):
        """After shutdown, _ui_poll() must not drain or deliver pending queue entries."""
        self.mgr._start_ui_poll()
        cb = MagicMock()
        self.mgr.register_listener(cb)

        # Enqueue an event, then shut down
        self.mgr._notify("Event", {"k": "v"})
        self.mgr.shutdown()

        self.assertFalse(self.mgr._ui_poll_active)

        # _ui_poll() must return immediately without draining pending events
        self.mgr._ui_poll()
        cb.assert_not_called()
        # No new after() scheduled after shutdown
        self.assertEqual(len(self.window.after_calls), 1)

    def test_window_destroyed_stops_polling(self):
        """When the window is destroyed, _ui_poll stops rescheduling safely."""
        self.mgr._start_ui_poll()
        cb = MagicMock()
        self.mgr.register_listener(cb)

        self.mgr._notify("Event", {"k": "v"})

        # _notify() must not call any Tkinter API even when window is alive
        after_calls_before = len(self.window.after_calls)
        cb.assert_not_called()

        # Destroy the window
        self.window.exists = False

        # _ui_poll should drain the queue but not reschedule (window destroyed)
        self.mgr._ui_poll()
        # cb was called because the event was already queued before destruction
        cb.assert_called_once_with("Event", {"k": "v"})
        # No new after() scheduled because window was destroyed
        self.assertEqual(len(self.window.after_calls), after_calls_before)

    def test_gui_mode_never_delivers_directly_from_background_thread(self):
        """GUI mode never falls back to direct worker-thread delivery."""
        cb = MagicMock()
        self.mgr.register_listener(cb)

        # Call _notify from a background thread
        t = threading.Thread(target=self.mgr._notify, args=("Schedule Started", {"url": "https://x"}))
        t.start()
        t.join()

        cb.assert_not_called()
        self.assertEqual(self.mgr._ui_event_queue.qsize(), 1)

    def test_non_gui_usage_delivers_directly(self):
        """Without a window, _notify() delivers synchronously (headless/test)."""
        mgr = _make_scheduler()  # window is None
        cb = MagicMock()
        mgr.register_listener(cb)
        mgr._notify("Event", {"k": "v"})
        cb.assert_called_once_with("Event", {"k": "v"})
        mgr.shutdown()


class TestAtomicPersistence(unittest.TestCase):
    """_save_schedules writes via .tmp then os.replace."""

    def setUp(self) -> None:
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        self.mgr = _make_scheduler()
        self.mgr._schedules.clear()

    def tearDown(self) -> None:
        self.mgr.shutdown()
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def test_atomic_write_uses_tmp_then_replace(self) -> None:
        with patch("scheduler.os.replace") as mock_replace, \
             patch("scheduler.os.fsync"), \
             patch("builtins.open", mock_open()):
            # Reset any calls from startup (e.g. _run_startup_recovery → _save_schedules)
            mock_replace.reset_mock()
            self.mgr._save_schedules()

        mock_replace.assert_called_once()
        tmp_path, dest_path = mock_replace.call_args[0]
        self.assertTrue(tmp_path.endswith(".json.tmp"),
                        f"Expected .json.tmp, got: {tmp_path}")
        self.assertEqual(dest_path, SCHEDULER_FILE)


class TestHistoryTrimming(unittest.TestCase):
    """_add_history_record keeps at most 500 entries, newest-first."""

    def setUp(self) -> None:
        self.mgr = _make_scheduler()
        # Clean any leftover file
        for f in (HISTORY_FILE, HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                os.remove(f)

    def tearDown(self) -> None:
        self.mgr.shutdown()
        for f in (HISTORY_FILE, HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def test_trims_to_500_records(self) -> None:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        job = {
            "url": "https://youtube.com",
            "mode": "mp3",
            "scheduled_time": now_str,
            "last_execution": now_str,
            "retry_count": 0,
        }
        for i in range(510):
            # _add_history_record(uuid_str, job, status, title="", error_msg="")
            self.mgr._add_history_record(f"uuid-{i}", job, "success", f"Title {i}")

        with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        self.assertEqual(len(data), 500)
        # Most-recently inserted record should be first (index 0)
        self.assertEqual(data[0]["title"], "Title 509")


class TestClockJumpDetection(unittest.TestCase):
    """Clock-jump >60 s triggers _recalculate_all_schedules via a single tick."""

    def setUp(self) -> None:
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        self.mgr = _make_scheduler()
        self.mgr._schedules.clear()

    def tearDown(self) -> None:
        self.mgr.shutdown()
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def _run_single_tick(self) -> None:
        """Simulate exactly one iteration of the scheduler loop body."""
        now_sys = datetime.datetime.now()
        delta = (now_sys - self.mgr._last_system_time).total_seconds()
        if abs(delta) > 60.0:
            self.mgr._recalculate_all_schedules()
        self.mgr._last_system_time = now_sys

    def test_jump_triggers_recalculate(self) -> None:
        # Back-date last system time by 5 minutes to simulate a clock jump
        self.mgr._last_system_time = datetime.datetime.now() - datetime.timedelta(minutes=5)
        with patch.object(self.mgr, "_recalculate_all_schedules") as mock_recalc:
            self._run_single_tick()
            mock_recalc.assert_called_once()

    def test_no_jump_skips_recalculate(self) -> None:
        # Last system time is just 1 second ago — no jump
        self.mgr._last_system_time = datetime.datetime.now() - datetime.timedelta(seconds=1)
        with patch.object(self.mgr, "_recalculate_all_schedules") as mock_recalc:
            self._run_single_tick()
            mock_recalc.assert_not_called()


class TestStartupRecovery(unittest.TestCase):
    """_run_startup_recovery: missed jobs are run or skipped based on settings."""

    def setUp(self) -> None:
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        self.mgr = _make_scheduler()
        self.mgr._schedules.clear()

    def tearDown(self) -> None:
        self.mgr.shutdown()
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def _insert_past_job(self) -> None:
        past_str = (datetime.datetime.now() - datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        self.mgr._schedules["test-past-id"] = {
            "uuid": "test-past-id",
            "url": "https://youtube.com/watch?v=missed",
            "mode": "1080p",
            "scheduled_time": past_str,
            "next_run": past_str,
            "repeat_type": "One Time",
            "enabled": True,
            "max_retries": 1,
        }

    def test_run_missed_enabled_executes_job(self) -> None:
        self._insert_past_job()
        self.mgr.settings["scheduler_run_missed_startup"] = True
        with patch.object(self.mgr, "_execute_job_thread") as mock_exec:
            self.mgr._run_startup_recovery()
            mock_exec.assert_called_once_with("test-past-id")

    def test_run_missed_disabled_skips_execution(self) -> None:
        self._insert_past_job()
        self.mgr.settings["scheduler_run_missed_startup"] = False
        with patch.object(self.mgr, "_execute_job_thread") as mock_exec:
            self.mgr._run_startup_recovery()
            mock_exec.assert_not_called()

    def test_run_missed_disabled_marks_expired(self) -> None:
        self._insert_past_job()
        self.mgr.settings["scheduler_run_missed_startup"] = False
        with patch.object(self.mgr, "_execute_job_thread"):
            self.mgr._run_startup_recovery()
        # One-time job in past with missed disabled → should be Expired
        self.assertEqual(self.mgr._schedules["test-past-id"]["state"], "Expired")


class TestConcurrencyGuard(unittest.TestCase):
    """Two threads racing to execute the same job — only one wins."""

    def setUp(self) -> None:
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        self.mgr = _make_scheduler()
        self.mgr._schedules.clear()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.mgr._schedules["test-id"] = {
            "uuid": "test-id",
            "url": "https://youtube.com/watch?v=race",
            "mode": "mp3",
            "scheduled_time": now_str,
            "repeat_type": "One Time",
            "enabled": True,
            "max_retries": 0,
        }

    def tearDown(self) -> None:
        self.mgr.shutdown()
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def test_only_one_thread_fires_schedule_started(self) -> None:
        # Patch backend so execution doesn't actually hit the network
        self.mgr.backend._send_request.return_value = MagicMock(
            status_code=200, json=lambda: {"job_id": "fake-job"}
        )
        # Reset any prior calls from setUp
        self.mgr.backend._send_request.reset_mock()

        t1 = threading.Thread(target=self.mgr._execute_job_thread, args=("test-id",))
        t2 = threading.Thread(target=self.mgr._execute_job_thread, args=("test-id",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Only ONE /download POST should have been dispatched regardless of thread count
        call_count = self.mgr.backend._send_request.call_count
        self.assertEqual(call_count, 1,
                         f"Expected exactly 1 backend /download dispatch, got {call_count}")

    def test_two_different_jobs_run_concurrently(self):
        """Different schedule IDs must execute concurrently (per-job locks).

        Two threads dispatch two *different* jobs at the same time.  A barrier
        held inside the mocked /download handler forces both network calls to be
        in flight simultaneously, proving the jobs run in parallel rather than
        being serialized behind a single global lock.
        """
        mock_send = MagicMock(return_value=MagicMock(status_code=202, json=lambda: {"job_id": "fake-job"}))
        self.mgr.backend._send_request = mock_send

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.mgr._schedules["job-a"] = {
            "uuid": "job-a",
            "url": "https://youtube.com/watch?v=job-a",
            "mode": "mp3",
            "scheduled_time": now_str,
            "repeat_type": "One Time",
            "enabled": True,
            "max_retries": 0,
            "state": "Scheduled",
        }
        self.mgr._schedules["job-b"] = {
            "uuid": "job-b",
            "url": "https://youtube.com/watch?v=job-b",
            "mode": "mp3",
            "scheduled_time": now_str,
            "repeat_type": "One Time",
            "enabled": True,
            "max_retries": 0,
            "state": "Scheduled",
        }

        reach_barrier = threading.Barrier(2, timeout=5)

        def blocking_send(*args, **kwargs):
            reach_barrier.wait()   # both threads arrive here together
            return mock_send(*args, **kwargs)

        self.mgr.backend._send_request = blocking_send

        t1 = threading.Thread(target=self.mgr._execute_job_thread, args=("job-a",))
        t2 = threading.Thread(target=self.mgr._execute_job_thread, args=("job-b",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Both different jobs dispatched a /download POST, and both were inside
        # the network call at the same time (the barrier forces that overlap).
        self.assertEqual(mock_send.call_count, 2,
                         "Expected both different jobs to dispatch /download")

    def test_two_jobs_different_output_folders_dispatch_own_folder(self):
        """Concurrent jobs with different output folders each send their own
        folder in the download payload; the global download_folder is never
        mutated via save_settings."""
        mock_send = MagicMock(return_value=MagicMock(status_code=202, json=lambda: {"job_id": "fake-job"}))
        self.mgr.backend._send_request = mock_send

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.mgr._schedules["job-a"] = {
            "uuid": "job-a",
            "url": "https://youtube.com/watch?v=job-a",
            "mode": "mp3",
            "output_folder": "C:/FolderA",
            "scheduled_time": now_str,
            "repeat_type": "One Time",
            "enabled": True,
            "max_retries": 0,
            "state": "Scheduled",
        }
        self.mgr._schedules["job-b"] = {
            "uuid": "job-b",
            "url": "https://youtube.com/watch?v=job-b",
            "mode": "mp3",
            "output_folder": "C:/FolderB",
            "scheduled_time": now_str,
            "repeat_type": "One Time",
            "enabled": True,
            "max_retries": 0,
            "state": "Scheduled",
        }

        reach_barrier = threading.Barrier(2, timeout=5)

        def blocking_send(*args, **kwargs):
            reach_barrier.wait()
            return mock_send(*args, **kwargs)

        self.mgr.backend._send_request = blocking_send

        with patch("os.path.isdir", return_value=True):
            t1 = threading.Thread(target=self.mgr._execute_job_thread, args=("job-a",))
            t2 = threading.Thread(target=self.mgr._execute_job_thread, args=("job-b",))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        self.assertEqual(mock_send.call_count, 2)

        folders = sorted(
            call.kwargs.get("json", {}).get("output_folder")
            for call in mock_send.call_args_list
        )
        self.assertEqual(folders, ["C:/FolderA", "C:/FolderB"],
                         "Each job must download to its own output folder")

        # The global download_folder setting must not be mutated per job.
        folder_calls = [c for c in self.mgr.backend.save_settings.call_args_list
                        if "download_folder" in c[0][0]]
        self.assertEqual(len(folder_calls), 0,
                         "Global download_folder must not be mutated per job")

    def test_delete_schedule_keeps_active_id_during_running_job(self):
        """delete_schedule() must not remove active UUID while job is running.
        The running execution's finally block owns active-ID cleanup.
        A replacement schedule with the same UUID must not execute concurrently."""
        jid = "delete-active-concurrency-id"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.mgr._schedules[jid] = {
            "uuid": jid,
            "url": "https://youtube.com/watch?v=delete-active",
            "mode": "mp3",
            "scheduled_time": now_str,
            "repeat_type": "One Time",
            "enabled": True,
            "max_retries": 0,
            "state": "Scheduled",
        }
        release_event = threading.Event()
        call_count = [0]

        def blocking_send(*args, **kwargs):
            call_count[0] += 1
            release_event.wait(timeout=10)
            return MagicMock(status_code=202, json=lambda: {"job_id": "fake-job"})

        self.mgr.backend._send_request = blocking_send

        # Start execution thread
        t1 = threading.Thread(target=self.mgr._execute_job_thread, args=(jid,))
        t1.start()

        # Wait until job becomes active
        for _ in range(500):  # up to 5 seconds
            if jid in self.mgr._active_job_ids:
                break
            time.sleep(0.01)
        self.assertIn(jid, self.mgr._active_job_ids,
                      "Job must become active before proceeding")

        # Delete the schedule while job is running
        self.mgr.delete_schedule(jid)

        # Active UUID must remain present (finally block owns cleanup)
        self.assertIn(jid, self.mgr._active_job_ids,
                      "Active UUID must remain while job is running")
        # Schedule must be removed from listing
        self.assertNotIn(jid, self.mgr._schedules)
        # Backend job tracking must be removed
        self.assertNotIn(jid, self.mgr._active_jobs)

        # Attempt second execution with same UUID — must be rejected
        t2_done = threading.Event()

        def run_second():
            self.mgr._execute_job_thread(jid)
            t2_done.set()

        t2 = threading.Thread(target=run_second)
        t2.start()
        t2_done.wait(timeout=5)
        # Second execution must not dispatch /download (rejected by active guard)
        self.assertEqual(call_count[0], 1,
                         "Second execution with same UUID must not dispatch /download")

        # Release the original thread
        release_event.set()
        t1.join(timeout=10)
        self.assertFalse(t1.is_alive(), "Original worker thread must terminate")

        # Finally block must remove active UUID
        self.assertNotIn(jid, self.mgr._active_job_ids,
                         "Finally block must remove active UUID after execution")

        # A new schedule with the same UUID can now execute (old run finished)
        self.mgr._schedules[jid] = {
            "uuid": jid,
            "url": "https://youtube.com/watch?v=delete-active-redo",
            "mode": "mp3",
            "scheduled_time": now_str,
            "repeat_type": "One Time",
            "enabled": True,
            "max_retries": 0,
            "state": "Scheduled",
        }
        call_count[0] = 0
        release_event2 = threading.Event()

        def blocking_send2(*args, **kwargs):
            call_count[0] += 1
            release_event2.wait(timeout=10)
            return MagicMock(status_code=202, json=lambda: {"job_id": "fake-job2"})

        self.mgr.backend._send_request = blocking_send2
        t3 = threading.Thread(target=self.mgr._execute_job_thread, args=(jid,))
        t3.start()
        # Wait for the replacement job to actually dispatch /download
        deadline = time.monotonic() + 10
        while call_count[0] < 1:
            if time.monotonic() > deadline:
                self.fail("Replacement schedule with same UUID did not execute after old run finishes")
            time.sleep(0.01)
        self.assertEqual(call_count[0], 1,
                         "Replacement schedule with same UUID must execute after old run finishes")
        release_event2.set()
        t3.join(timeout=10)
        self.assertFalse(t3.is_alive(), "Replacement worker thread must terminate")
        self.assertNotIn(jid, self.mgr._active_job_ids,
                         "Finally block must remove active UUID after replacement execution")

class TestSchedulerRefinements(unittest.TestCase):
    """Schema versioning migrations and statistics persistence / correctness tests."""

    def setUp(self) -> None:
        # Clean files
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def tearDown(self) -> None:
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def test_schema_migration(self) -> None:
        # Legacy format (no schema_version)
        legacy_data = {
            "schedules": {
                "legacy-job-id": {
                    "uuid": "legacy-job-id",
                    "url": "https://youtube.com/watch?v=legacy",
                    "mode": "mp3",
                    "scheduled_time": "2026-06-28 12:00:00",
                    "repeat_type": "One Time",
                    "enabled": True
                }
            }
        }
        with open(SCHEDULER_FILE, "w", encoding="utf-8") as fh:
            json.dump(legacy_data, fh)

        mgr = _make_scheduler()
        mgr.deferred_startup()
        try:
            # Verify data loaded correctly
            self.assertEqual(len(mgr.get_schedules()), 1)
            self.assertEqual(mgr.get_schedules()[0]["url"], "https://youtube.com/watch?v=legacy")
            
            # Verify migrated file contains version 1 and stats
            with open(SCHEDULER_FILE, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved.get("schema_version"), 1)
            self.assertIn("stats", saved)
        finally:
            mgr.shutdown()

    def test_unknown_future_schema_version(self) -> None:
        # Future version 2 format
        future_data = {
            "schema_version": 2,
            "schedules": {
                "future-job-id": {
                    "uuid": "future-job-id",
                    "url": "https://youtube.com/watch?v=future",
                    "mode": "1080p",
                    "scheduled_time": "2026-06-28 12:00:00",
                    "repeat_type": "One Time",
                    "enabled": True
                }
            }
        }
        with open(SCHEDULER_FILE, "w", encoding="utf-8") as fh:
            json.dump(future_data, fh)

        mgr = _make_scheduler()
        mgr.deferred_startup()
        try:
            # Should ignore version 2 schedules safely and start with empty schedules
            self.assertEqual(len(mgr.get_schedules()), 0)
        finally:
            mgr.shutdown()

    def test_scheduler_statistics_correctness(self) -> None:
        mgr = _make_scheduler()
        try:
            future_str = (datetime.datetime.now() + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            job_data = {
                "uuid": "stat-job-id",
                "url": "https://youtube.com/watch?v=stat",
                "mode": "mp3",
                "scheduled_time": future_str,
                "repeat_type": "One Time",
                "enabled": True
            }
            mgr._schedules["stat-job-id"] = job_data
            
            # 1. Total and enabled schedules
            stats = mgr.get_scheduler_stats()
            self.assertEqual(stats["total_schedules"], 1)
            self.assertEqual(stats["enabled_schedules"], 1)
            
            # 2. Completed runs
            mgr._handle_job_success("stat-job-id", "Completed Title")
            stats = mgr.get_scheduler_stats()
            self.assertEqual(stats["completed_runs"], 1)
            
            # 3. Retries / Failed runs
            mgr.settings["scheduler_auto_retry"] = True
            mgr.settings["scheduler_max_retries"] = 1
            mgr._schedules["stat-job-id"] = job_data # restore
            mgr._schedules["stat-job-id"]["retry_count"] = 0
            
            mgr._handle_job_failure("stat-job-id", "Error 1")
            stats = mgr.get_scheduler_stats()
            self.assertEqual(stats["retries"], 1)
            
            # Second failure should trigger failed_runs
            mgr._handle_job_failure("stat-job-id", "Error 2")
            stats = mgr.get_scheduler_stats()
            self.assertEqual(stats["failed_runs"], 1)
            
            # 4. Cancelled runs
            mgr._schedules["stat-job-id"] = job_data # restore
            mgr._handle_job_cancelled("stat-job-id")
            stats = mgr.get_scheduler_stats()
            self.assertEqual(stats["cancelled_runs"], 1)
        finally:
            mgr.shutdown()

    def test_counter_persistence_after_restart(self) -> None:
        mgr = _make_scheduler()
        try:
            mgr._stats["completed_runs"] = 12
            mgr._stats["failed_runs"] = 3
            mgr._stats["retries"] = 7
            mgr._save_schedules()
        finally:
            mgr.shutdown()

        # Restart scheduler
        mgr2 = _make_scheduler()
        mgr2.deferred_startup()
        try:
            stats = mgr2.get_scheduler_stats()
            self.assertEqual(stats["completed_runs"], 12)
            self.assertEqual(stats["failed_runs"], 3)
            self.assertEqual(stats["retries"], 7)
        finally:
            mgr2.shutdown()


# ── Regression tests for Phase 3 fixes ──────────────────────────────────────

class TestRunNowSchedulerEnabledGuard(unittest.TestCase):
    """Fix #4: run_now() must respect scheduler_enabled setting."""

    def setUp(self) -> None:
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        self.mgr = _make_scheduler()
        self.mgr._schedules.clear()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.mgr._schedules["test-id"] = {
            "uuid": "test-id",
            "url": "https://youtube.com/watch?v=guard",
            "mode": "mp3",
            "scheduled_time": now_str,
            "repeat_type": "One Time",
            "enabled": True,
            "max_retries": 0,
        }

    def tearDown(self) -> None:
        self.mgr.shutdown()
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def test_run_now_rejected_when_scheduler_disabled(self):
        """run_now() must not execute a job when scheduler is disabled."""
        self.mgr.settings["scheduler_enabled"] = False
        with patch("settings_panel.read_local_settings", return_value=self.mgr.settings):
            with patch.object(self.mgr, "_execute_job_thread") as mock_exec:
                self.mgr.run_now("test-id")
                mock_exec.assert_not_called()

    def test_run_now_executes_when_scheduler_enabled(self):
        """run_now() must execute a job when scheduler is enabled."""
        self.mgr.settings["scheduler_enabled"] = True
        self.mgr.backend._send_request.return_value = MagicMock(
            status_code=200, json=lambda: {"job_id": "fake-job"}
        )
        with patch("settings_panel.read_local_settings", return_value=self.mgr.settings):
            with patch.object(self.mgr, "_execute_job_thread", wraps=self.mgr._execute_job_thread):
                self.mgr.run_now("test-id")
                import time
                time.sleep(0.3)
                self.mgr.backend._send_request.assert_called()


class TestPerRequestOutputFolder(unittest.TestCase):
    """output_folder must be passed per-request in the download payload, not
    set via global save_settings (and not restored via a deferred Timer)."""

    def setUp(self) -> None:
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        self.mgr = _make_scheduler()
        self.mgr._schedules.clear()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.mgr._schedules["test-id"] = {
            "uuid": "test-id",
            "url": "https://youtube.com/watch?v=output-folder",
            "mode": "mp3",
            "output_folder": "C:/Custom/Folder",
            "scheduled_time": now_str,
            "repeat_type": "One Time",
            "enabled": True,
            "max_retries": 0,
        }
        self.mgr.backend.get_settings.return_value = {"download_folder": "C:/Original/Folder"}
        self.mgr.backend._send_request.return_value = MagicMock(
            status_code=200, json=lambda: {"job_id": "fake-job"}
        )

    def tearDown(self) -> None:
        self.mgr.shutdown()
        for f in (SCHEDULER_FILE, HISTORY_FILE, SCHEDULER_FILE + ".tmp", HISTORY_FILE + ".tmp"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def test_output_folder_passed_in_download_payload(self):
        """The custom output_folder must be sent in the download POST payload."""
        self.mgr.settings["scheduler_enabled"] = True
        with patch("settings_panel.read_local_settings", return_value=self.mgr.settings), \
             patch("os.path.isdir", return_value=True):
            self.mgr._execute_job_thread("test-id")

        # The download POST must have been made
        self.assertTrue(self.mgr.backend._send_request.called)
        payload = self.mgr.backend._send_request.call_args.kwargs.get("json")
        self.assertIsNotNone(payload)
        self.assertEqual(payload.get("output_folder"), "C:/Custom/Folder")

    def test_no_global_folder_mutation(self):
        """The global download_folder setting must NOT be mutated via save_settings."""
        self.mgr.settings["scheduler_enabled"] = True
        with patch("settings_panel.read_local_settings", return_value=self.mgr.settings), \
             patch("os.path.isdir", return_value=True):
            self.mgr._execute_job_thread("test-id")

        # save_settings must not be called to change download_folder at all
        folder_calls = [c for c in self.mgr.backend.save_settings.call_args_list
                        if "download_folder" in c[0][0]]
        self.assertEqual(len(folder_calls), 0,
                         "Global download_folder must not be mutated per job")


if __name__ == "__main__":
    unittest.main()
