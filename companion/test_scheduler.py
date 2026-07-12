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


if __name__ == "__main__":
    unittest.main()
