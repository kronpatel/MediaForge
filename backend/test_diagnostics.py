"""Tests for diagnostics, health, recovery, and startup self-check."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

from diagnostics import (
    health_check,
    diagnostics_report,
    export_diagnostics,
    DIAGNOSTICS_EXPORT_FILE,
    run_startup_self_check,
    SelfCheckResult,
    check_queue_health,
    QueueHealthResult,
    recovery_dashboard_data,
    PerformanceMetrics,
    get_performance_metrics,
)

from recovery import RecoveryMetrics, get_recovery_metrics
import downloader as _dl

from downloader import (
    DownloadJob,
    DownloadState,
    _jobs,
    _jobs_lock,
    _download_queue,
    BASE_DIR,
    now_iso,
)


class TestHealthCheckAPI(unittest.TestCase):
    def setUp(self):
        with _jobs_lock:
            _jobs.clear()
            _download_queue.queue.clear()

    def test_health_check_returns_expected_keys(self):
        result = health_check()
        self.assertEqual(result["status"], "ok")
        self.assertIn("version", result)
        self.assertIn("uptime_seconds", result)
        self.assertIn("queue_size", result)
        self.assertIn("active_download", result)
        self.assertIn("recovery", result)
        self.assertIn("persistence", result)
        self.assertIn("scheduler", result)

    def test_health_check_with_queued_job(self):
        with _jobs_lock:
            job = DownloadJob(
                id="test-1", url="https://example.com/video",
                mode="mp3", label="MP3",
                status=DownloadState.QUEUED.value,
            )
            _jobs["test-1"] = job
        result = health_check()
        self.assertGreaterEqual(result["queue_size"], 1)

    def test_health_check_with_active_job(self):
        with _jobs_lock:
            job = DownloadJob(
                id="active-1", url="https://example.com/video",
                mode="mp3", label="MP3",
                status=DownloadState.DOWNLOADING.value,
            )
            _jobs["active-1"] = job
            _dl._active_job_id = "active-1"
        result = health_check()
        self.assertTrue(result["active_download"])
        self.assertEqual(result["active_job_id"], "active-1")


class TestDiagnosticsReport(unittest.TestCase):
    def setUp(self):
        with _jobs_lock:
            _jobs.clear()
            _download_queue.queue.clear()

    def test_diagnostics_returns_all_sections(self):
        report = diagnostics_report()
        self.assertIn("application", report)
        self.assertIn("system", report)
        self.assertIn("dependencies", report)
        self.assertIn("resources", report)
        self.assertIn("queue", report)
        self.assertIn("recovery", report)
        self.assertIn("performance", report)
        self.assertIn("generated_at", report)

    def test_diagnostics_application_info(self):
        report = diagnostics_report()
        self.assertEqual(report["application"]["name"], "MediaForge Backend")
        self.assertEqual(report["application"]["version"], "1.2.3")
        self.assertGreaterEqual(report["application"]["uptime_seconds"], 0)

    def test_diagnostics_system_info(self):
        report = diagnostics_report()
        self.assertIn("python_version", report["system"])
        self.assertIn("platform", report["system"])

    def test_diagnostics_dependencies(self):
        report = diagnostics_report()
        self.assertIn("yt_dlp_version", report["dependencies"])
        self.assertIn("ffmpeg", report["dependencies"])

    def test_diagnostics_queue_info(self):
        report = diagnostics_report()
        self.assertIn("total_jobs", report["queue"])
        self.assertIn("history_entries", report["queue"])

    def test_diagnostics_includes_recovery(self):
        report = diagnostics_report()
        self.assertIn("queues_restored", report["recovery"])

    def test_diagnostics_includes_performance(self):
        report = diagnostics_report()
        self.assertIn("average_download_speed", report["performance"])
        self.assertIn("average_queue_wait", report["performance"])


class TestExportDiagnostics(unittest.TestCase):
    def setUp(self):
        self._orig_file = DIAGNOSTICS_EXPORT_FILE
        self._tmp = tempfile.mktemp(suffix=".json")
        import diagnostics
        diagnostics.DIAGNOSTICS_EXPORT_FILE = self._tmp

    def tearDown(self):
        import diagnostics
        diagnostics.DIAGNOSTICS_EXPORT_FILE = self._orig_file
        if os.path.exists(self._tmp):
            os.remove(self._tmp)

    def test_export_creates_file(self):
        path = export_diagnostics()
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("application", data)
        self.assertIn("system", data)
        self.assertIn("exported_at", data)

    def test_export_contains_no_download_history(self):
        path = export_diagnostics()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("downloads", data)
        self.assertNotIn("history", data)

    def test_export_is_valid_json(self):
        path = export_diagnostics()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)


class TestStartupSelfCheck(unittest.TestCase):
    def test_self_check_returns_result(self):
        result = run_startup_self_check()
        self.assertIsInstance(result, SelfCheckResult)
        self.assertGreater(len(result.checks), 0)

    def test_self_check_all_checks_have_names(self):
        result = run_startup_self_check()
        for check in result.checks:
            self.assertIn("name", check)
            self.assertIn("passed", check)

    def test_self_check_summary(self):
        result = run_startup_self_check()
        summary = result.summary()
        self.assertEqual(summary["total"], len(result.checks))
        self.assertGreaterEqual(summary["passed"], 0)
        self.assertGreaterEqual(summary["warnings"], 0)

    def test_self_check_ffmpeg(self):
        result = run_startup_self_check()
        ffmpeg = [c for c in result.checks if c["name"] == "ffmpeg"]
        self.assertEqual(len(ffmpeg), 1)


class TestQueueHealth(unittest.TestCase):
    def setUp(self):
        with _jobs_lock:
            _jobs.clear()
            _download_queue.queue.clear()

    def test_healthy_empty_queue(self):
        result = check_queue_health()
        self.assertTrue(result.healthy)
        self.assertEqual(len(result.issues), 0)

    def test_healthy_single_job(self):
        with _jobs_lock:
            job = DownloadJob(
                id="healthy-1", url="https://example.com",
                mode="mp3", label="MP3",
                status=DownloadState.QUEUED.value,
                progress=0.0,
            )
            _jobs["healthy-1"] = job
        result = check_queue_health()
        self.assertTrue(result.healthy)

    def test_unknown_state_detected(self):
        with _jobs_lock:
            job = DownloadJob(
                id="bad-state", url="https://example.com",
                mode="mp3", label="MP3",
                status="invalid_state",
            )
            _jobs["bad-state"] = job
        result = check_queue_health()
        self.assertFalse(result.healthy)
        self.assertGreaterEqual(len(result.issues), 1)

    def test_completed_with_partial_progress(self):
        with _jobs_lock:
            job = DownloadJob(
                id="partial", url="https://example.com",
                mode="mp3", label="MP3",
                status=DownloadState.COMPLETED.value,
                progress=50.0,
            )
            _jobs["partial"] = job
        result = check_queue_health()
        self.assertFalse(result.healthy)
        self.assertGreaterEqual(result.repaired, 1)

    def test_invalid_progress_clamped(self):
        with _jobs_lock:
            job = DownloadJob(
                id="neg-progress", url="https://example.com",
                mode="mp3", label="MP3",
                status=DownloadState.QUEUED.value,
                progress=-5.0,
            )
            _jobs["neg-progress"] = job
        result = check_queue_health()
        self.assertFalse(result.healthy)
        self.assertEqual(job.progress, 0.0)

    def test_orphan_active_job_detected(self):
        with _jobs_lock:
            _dl._active_job_id = "nonexistent-job"
        result = check_queue_health()
        self.assertFalse(result.healthy)
        self.assertIsNone(_dl._active_job_id)

    def test_duplicate_id_removed(self):
        with _jobs_lock:
            job1 = DownloadJob(
                id="dup-id", url="https://a.com",
                mode="mp3", label="MP3",
            )
            job2 = DownloadJob(
                id="dup-id", url="https://b.com",
                mode="mp3", label="MP3",
            )
            _jobs["dup-id-a"] = job1
            _jobs["dup-id-b"] = job2
        result = check_queue_health()
        self.assertFalse(result.healthy)
        self.assertGreaterEqual(result.repaired, 1)


class TestRecoveryDashboardData(unittest.TestCase):
    def test_recovery_data_returns_all_fields(self):
        data = recovery_dashboard_data()
        self.assertIn("queues_restored", data)
        self.assertIn("downloads_recovered", data)
        self.assertIn("cleanup_operations", data)
        self.assertIn("queue_corruption_events", data)
        self.assertIn("recovery_failures", data)
        self.assertIn("total_errors", data)

    def test_recovery_data_types(self):
        data = recovery_dashboard_data()
        for key in ("queues_restored", "downloads_recovered",
                     "cleanup_operations", "queue_corruption_events",
                     "recovery_failures", "total_errors"):
            self.assertIsInstance(data[key], int)


class TestPerformanceMetrics(unittest.TestCase):
    def setUp(self):
        self.metrics = PerformanceMetrics()

    def test_empty_metrics(self):
        self.assertEqual(self.metrics.average_download_speed(), "0 MB/s")
        self.assertEqual(self.metrics.average_queue_wait(), "0s")
        self.assertEqual(self.metrics.average_recovery_time(), "0s")
        self.assertEqual(self.metrics.average_save_duration(), "0s")

    def test_record_download_speed(self):
        self.metrics.record_download_speed(5.0)
        self.metrics.record_download_speed(15.0)
        self.assertIn("10.0", self.metrics.average_download_speed())

    def test_record_queue_wait(self):
        self.metrics.record_queue_wait(2.5)
        self.metrics.record_queue_wait(7.5)
        self.assertIn("5.0", self.metrics.average_queue_wait())

    def test_record_recovery_time(self):
        self.metrics.record_recovery_time(1.0)
        self.assertEqual(self.metrics.average_recovery_time(), "1.0s")

    def test_record_save_duration(self):
        self.metrics.record_save_duration(0.05)
        self.metrics.record_save_duration(0.15)
        snap = self.metrics.snapshot()
        self.assertEqual(snap["save_duration_samples"], 2)

    def test_snapshot_counts(self):
        self.metrics.record_download_speed(3.0)
        self.metrics.record_queue_wait(1.0)
        snap = self.metrics.snapshot()
        self.assertEqual(snap["download_speed_samples"], 1)
        self.assertEqual(snap["queue_wait_samples"], 1)
        self.assertEqual(snap["recovery_time_samples"], 0)
        self.assertEqual(snap["save_duration_samples"], 0)

    def test_thread_safety(self):
        errors = []

        def record_speeds():
            for _ in range(100):
                try:
                    self.metrics.record_download_speed(1.0)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=record_speeds) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(self.metrics.snapshot()["download_speed_samples"], 400)


class TestLogPrefixes(unittest.TestCase):
    def test_downloader_logs_have_prefixes(self):
        import downloader
        with open(downloader.__file__, "r", encoding="utf-8") as f:
            content = f.read()

        for pattern in ["[Downloader]", "[Queue]", "[History]", "[Settings]", "[Recovery]"]:
            self.assertIn(pattern, content)

    def test_recovery_logs_have_prefix(self):
        import recovery
        with open(recovery.__file__, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[Recovery]", content)

    def test_diagnostics_logs_have_prefix(self):
        import diagnostics
        with open(diagnostics.__file__, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[Diagnostics]", content)
        self.assertIn("[SelfCheck]", content)


class TestStaticAnalysis(unittest.TestCase):
    def test_self_check_result_class(self):
        result = SelfCheckResult()
        self.assertEqual(result.warnings, 0)
        self.assertEqual(result.errors, 0)
        self.assertEqual(len(result.checks), 0)

        result.add_check("test-check", True, "Everything OK")
        self.assertEqual(len(result.checks), 1)
        self.assertEqual(result.warnings, 0)

        result.add_check("test-fail", False, "Something wrong")
        self.assertEqual(result.warnings, 1)

        summary = result.summary()
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["warnings"], 1)

    def test_queue_health_result_class(self):
        result = QueueHealthResult()
        self.assertTrue(result.healthy)
        self.assertEqual(len(result.issues), 0)

        result.add_issue("warning", "Test issue")
        self.assertFalse(result.healthy)
        self.assertEqual(len(result.issues), 1)

        result.add_issue("error", "Critical", "Auto-fix applied")
        self.assertEqual(len(result.issues), 2)
        self.assertEqual(result.issues[1]["repair_action"], "Auto-fix applied")

    def test_recovery_metrics_snapshot(self):
        metrics = RecoveryMetrics(
            queues_restored=3,
            downloads_recovered=2,
            cleanup_operations=5,
            queue_corruption_events=1,
            recovery_failures=0,
            total_errors=0,
        )
        snap = metrics.snapshot()
        self.assertEqual(snap["queues_restored"], 3)
        self.assertEqual(snap["cleanup_operations"], 5)

    def test_get_performance_metrics_returns_instance(self):
        pm = get_performance_metrics()
        self.assertIsInstance(pm, PerformanceMetrics)


if __name__ == "__main__":
    unittest.main()
