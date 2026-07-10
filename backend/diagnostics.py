"""Diagnostics, health monitoring, and self-check utilities for MediaForge.

Provides health check data, full diagnostics reports, diagnostics export,
startup self-checks, queue health validation, and performance metrics.
"""

import json
import logging
import os
import platform
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import downloader as _downloader_mod

from downloader import (
    BASE_DIR,
    SETTINGS_FILE,
    HISTORY_FILE,
    QUEUE_STATE_FILE,
    QUEUE_STATE_SCHEMA_VERSION,
    FFMPEG_PATH,
    _jobs,
    _jobs_lock,
    _download_queue,
    _START_TIME,
    read_settings,
    get_download_dir,
    cleanup_temp_files,
    DownloadState,
    now_iso,
)

from recovery import get_recovery_metrics, RecoveryMetrics

logger = logging.getLogger("kerzox.diagnostics")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())


# ── Performance Metrics ──────────────────────────────────────────────────

_perf_lock = threading.Lock()


@dataclass
class PerformanceMetrics:
    download_speeds: list[float] = field(default_factory=list)
    queue_waits: list[float] = field(default_factory=list)
    recovery_times: list[float] = field(default_factory=list)
    save_durations: list[float] = field(default_factory=list)

    def record_download_speed(self, speed_mbps: float) -> None:
        with _perf_lock:
            self.download_speeds.append(speed_mbps)

    def record_queue_wait(self, seconds: float) -> None:
        with _perf_lock:
            self.queue_waits.append(seconds)

    def record_recovery_time(self, seconds: float) -> None:
        with _perf_lock:
            self.recovery_times.append(seconds)

    def record_save_duration(self, seconds: float) -> None:
        with _perf_lock:
            self.save_durations.append(seconds)

    def average_download_speed(self) -> str:
        with _perf_lock:
            if not self.download_speeds:
                return "0 MB/s"
            avg = sum(self.download_speeds) / len(self.download_speeds)
            return f"{avg:.1f} MB/s"

    def average_queue_wait(self) -> str:
        with _perf_lock:
            if not self.queue_waits:
                return "0s"
            avg = sum(self.queue_waits) / len(self.queue_waits)
            return f"{avg:.1f}s"

    def average_recovery_time(self) -> str:
        with _perf_lock:
            if not self.recovery_times:
                return "0s"
            avg = sum(self.recovery_times) / len(self.recovery_times)
            return f"{avg:.1f}s"

    def average_save_duration(self) -> str:
        with _perf_lock:
            if not self.save_durations:
                return "0s"
            avg = sum(self.save_durations) / len(self.save_durations)
            return f"{avg*1000:.1f}ms"

    def snapshot(self) -> dict[str, Any]:
        with _perf_lock:
            speeds = list(self.download_speeds)
            waits = list(self.queue_waits)
            rec_times = list(self.recovery_times)
            saves = list(self.save_durations)
        avg_speed = (sum(speeds) / len(speeds)) if speeds else 0.0
        avg_wait = (sum(waits) / len(waits)) if waits else 0.0
        avg_rec = (sum(rec_times) / len(rec_times)) if rec_times else 0.0
        avg_save = (sum(saves) / len(saves)) if saves else 0.0
        return {
            "average_download_speed": f"{avg_speed:.1f} MB/s" if speeds else "0 MB/s",
            "average_queue_wait": f"{avg_wait:.1f}s" if waits else "0s",
            "average_recovery_time": f"{avg_rec:.1f}s" if rec_times else "0s",
            "average_save_duration": f"{avg_save*1000:.1f}ms" if saves else "0s",
            "download_speed_samples": len(speeds),
            "queue_wait_samples": len(waits),
            "recovery_time_samples": len(rec_times),
            "save_duration_samples": len(saves),
        }

    def raw_snapshot(self) -> dict[str, Any]:
        with _perf_lock:
            speeds = list(self.download_speeds)
            waits = list(self.queue_waits)
            rec_times = list(self.recovery_times)
            saves = list(self.save_durations)
        avg_speed = (sum(speeds) / len(speeds)) if speeds else 0.0
        avg_wait = (sum(waits) / len(waits)) if waits else 0.0
        avg_rec = (sum(rec_times) / len(rec_times)) if rec_times else 0.0
        avg_save = (sum(saves) / len(saves)) if saves else 0.0
        return {
            "average_download_speed": f"{avg_speed:.1f} MB/s" if speeds else "0 MB/s",
            "average_queue_wait": f"{avg_wait:.1f}s" if waits else "0s",
            "average_recovery_time": f"{avg_rec:.1f}s" if rec_times else "0s",
            "average_save_duration": f"{avg_save*1000:.1f}ms" if saves else "0s",
        }


_perf_metrics = PerformanceMetrics()


def get_performance_metrics() -> PerformanceMetrics:
    return _perf_metrics


# ── Health Check ─────────────────────────────────────────────────────────

def health_check() -> dict[str, Any]:
    with _jobs_lock:
        queue_size = len([j for j in _jobs.values() if j.status == DownloadState.QUEUED.value])
        active = _downloader_mod._active_job_id if _downloader_mod._active_job_id and _downloader_mod._active_job_id in _jobs else None

    queue_state_exists = os.path.exists(QUEUE_STATE_FILE)
    recovery = get_recovery_metrics()

    return {
        "status": "ok",
        "version": "1.2.0",
        "uptime_seconds": int(time.monotonic() - _START_TIME),
        "queue_size": queue_size,
        "active_download": active is not None,
        "active_job_id": active,
        "recovery": {
            "queues_restored": recovery.queues_restored,
            "downloads_recovered": recovery.downloads_recovered,
            "recovery_failures": recovery.recovery_failures,
            "queue_corruption_events": recovery.queue_corruption_events,
        },
        "persistence": {
            "queue_state_exists": queue_state_exists,
            "schema_version": QUEUE_STATE_SCHEMA_VERSION if queue_state_exists else None,
            "queue_state_file": os.path.basename(QUEUE_STATE_FILE),
        },
        "scheduler": {"status": "unknown"},
    }


# ── Diagnostics Report ──────────────────────────────────────────────────

_yt_dlp_version: Any = None


def _get_ytdlp_version() -> str:
    global _yt_dlp_version
    if _yt_dlp_version is None:
        try:
            import yt_dlp
            _yt_dlp_version = getattr(yt_dlp, "__version__", "unknown")
        except ImportError:
            _yt_dlp_version = "not installed"
    return _yt_dlp_version


def _check_ffmpeg() -> dict[str, Any]:
    ffmpeg_exe = shutil.which("ffmpeg")
    detected = False
    if not ffmpeg_exe:
        test_path = os.path.join(FFMPEG_PATH, "ffmpeg.exe")
        if os.path.exists(test_path):
            ffmpeg_exe = test_path
            detected = True
        test_path2 = os.path.join(FFMPEG_PATH, "ffmpeg")
        if os.path.exists(test_path2):
            ffmpeg_exe = test_path2
            detected = True
    else:
        detected = True
    return {"detected": detected, "path": ffmpeg_exe or "not found"}


def _memory_usage() -> dict[str, Any]:
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem = process.memory_info()
        return {
            "rss_mb": round(mem.rss / (1024 * 1024), 1),
            "vms_mb": round(mem.vms / (1024 * 1024), 1) if hasattr(mem, 'vms') else 0,
        }
    except ImportError:
        return {"note": "psutil not available"}
    except Exception as exc:
        return {"error": str(exc)}


def diagnostics_report() -> dict[str, Any]:
    with _jobs_lock:
        queue_size = len(_jobs)
        history_count = len([j for j in _jobs.values() if j.status == DownloadState.COMPLETED.value])
    recovery = get_recovery_metrics()
    perf = _perf_metrics.raw_snapshot()

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history_line_count = sum(1 for _ in f)
    except OSError:
        history_line_count = 0

    return {
        "generated_at": now_iso(),
        "application": {
            "version": "1.2.0",
            "name": "MediaForge Backend",
            "uptime_seconds": int(time.monotonic() - _START_TIME),
        },
        "system": {
            "python_version": sys.version,
            "platform": platform.platform(),
            "os": platform.system(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
        },
        "dependencies": {
            "yt_dlp_version": _get_ytdlp_version(),
            "ffmpeg": _check_ffmpeg(),
        },
        "resources": _memory_usage(),
        "queue": {
            "total_jobs": queue_size,
            "history_entries": history_line_count,
        },
        "recovery": recovery.snapshot(),
        "performance": perf,
        "settings_file": {
            "exists": os.path.exists(SETTINGS_FILE),
            "path": os.path.basename(SETTINGS_FILE),
        },
        "queue_state_file": {
            "exists": os.path.exists(QUEUE_STATE_FILE),
            "path": os.path.basename(QUEUE_STATE_FILE),
            "schema_version": QUEUE_STATE_SCHEMA_VERSION,
        },
        "download_directory": get_download_dir(),
    }


# ── Export Diagnostics ──────────────────────────────────────────────────

DIAGNOSTICS_EXPORT_FILE = os.path.join(BASE_DIR, "diagnostics.json")


def export_diagnostics() -> str:
    report = diagnostics_report()
    report["exported_at"] = now_iso()
    try:
        with open(DIAGNOSTICS_EXPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        logger.info("[Diagnostics] Exported diagnostics to %s", DIAGNOSTICS_EXPORT_FILE)
    except Exception as exc:
        logger.error("[Diagnostics] Failed to export diagnostics: %s", exc)
        raise
    return DIAGNOSTICS_EXPORT_FILE


# ── Startup Self Check ──────────────────────────────────────────────────

class SelfCheckResult:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.warnings: int = 0
        self.errors: int = 0

    def add_check(self, name: str, passed: bool, message: str = "") -> None:
        entry = {"name": name, "passed": passed, "message": message}
        self.checks.append(entry)
        if not passed:
            self.warnings += 1
        logger.info("[SelfCheck] %s: %s", "PASS" if passed else "WARN", message or name)

    def summary(self) -> dict[str, Any]:
        return {
            "total": len(self.checks),
            "passed": len(self.checks) - self.warnings,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def run_startup_self_check() -> SelfCheckResult:
    result = SelfCheckResult()

    ffmpeg_info = _check_ffmpeg()
    result.add_check(
        "ffmpeg",
        ffmpeg_info["detected"],
        f"FFmpeg {'found at ' + ffmpeg_info['path'] if ffmpeg_info['detected'] else 'not found'}",
    )

    download_dir = get_download_dir()
    dir_writable = os.access(download_dir, os.W_OK) if os.path.isdir(download_dir) else False
    result.add_check(
        "download_directory_writable",
        dir_writable,
        f"Download directory {'is writable' if dir_writable else 'not writable or missing'}: {download_dir}",
    )

    history_ok = True
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                json.loads(f.readline() or "{}")
        except (OSError, json.JSONDecodeError):
            history_ok = False
    result.add_check(
        "history_file",
        history_ok,
        f"History file {'ok' if history_ok else 'corrupt'}: {os.path.basename(HISTORY_FILE)}",
    )

    queue_file_ok = True
    if os.path.exists(QUEUE_STATE_FILE):
        try:
            with open(QUEUE_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            queue_file_ok = isinstance(data, dict) and "schema_version" in data
        except (OSError, json.JSONDecodeError):
            queue_file_ok = False
    result.add_check(
        "queue_state_file",
        queue_file_ok,
        f"Queue state file {'ok' if queue_file_ok else 'corrupt'}: {os.path.basename(QUEUE_STATE_FILE)}",
    )

    settings_ok = True
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                json.load(f)
        except (OSError, json.JSONDecodeError):
            settings_ok = False
    result.add_check(
        "settings_file",
        settings_ok,
        f"Settings file {'ok' if settings_ok else 'corrupt'}: {os.path.basename(SETTINGS_FILE)}",
    )

    temp_dir = os.path.join(BASE_DIR, "..", "temp")
    base_writable = os.access(BASE_DIR, os.W_OK)
    result.add_check(
        "backend_directory_writable",
        base_writable,
        f"Backend directory {'is writable' if base_writable else 'not writable'}: {BASE_DIR}",
    )

    return result


# ── Queue Health ─────────────────────────────────────────────────────────

class QueueHealthResult:
    def __init__(self) -> None:
        self.healthy: bool = True
        self.issues: list[dict[str, Any]] = []
        self.repaired: int = 0

    def add_issue(self, severity: str, message: str, repair_action: str = "") -> None:
        self.healthy = False
        issue = {"severity": severity, "message": message}
        if repair_action:
            issue["repair_action"] = repair_action
        self.issues.append(issue)


def check_queue_health() -> QueueHealthResult:
    result = QueueHealthResult()

    with _jobs_lock:
        seen_job_ids: set[str] = set()
        for jid, job in list(_jobs.items()):
            if job.id in seen_job_ids:
                result.add_issue("error", f"Duplicate job ID: {job.id}", "Removed duplicate")
                del _jobs[jid]
                result.repaired += 1
            else:
                seen_job_ids.add(job.id)

            try:
                state = DownloadState(job.status)
            except ValueError:
                result.add_issue(
                    "error",
                    f"Job {jid} has unknown state: {job.status}",
                    "Marked as FAILED",
                )
                job.status = DownloadState.FAILED.value
                job.message = "Invalid state detected"
                result.repaired += 1
                continue

            if state == DownloadState.COMPLETED and job.progress < 100.0:
                result.add_issue(
                    "warning",
                    f"Job {jid} completed with progress {job.progress}%",
                    "Progress corrected to 100%",
                )
                job.progress = 100.0
                result.repaired += 1

            if state in (DownloadState.QUEUED, DownloadState.STARTING, DownloadState.DOWNLOADING):
                if job.progress < 0 or job.progress > 100:
                    result.add_issue(
                        "warning",
                        f"Job {jid} has invalid progress: {job.progress}%",
                        "Progress clamped to 0-100",
                    )
                    job.progress = max(0.0, min(100.0, job.progress))
                    result.repaired += 1

        if _downloader_mod._active_job_id and _downloader_mod._active_job_id not in _jobs:
            result.add_issue(
                "warning",
                f"Orphan active job ID: {_downloader_mod._active_job_id} (no matching job)",
                "Cleared active job ID",
            )
            _downloader_mod._active_job_id = None
            result.repaired += 1

    return result


# ── Recovery Dashboard Data ─────────────────────────────────────────────

def recovery_dashboard_data() -> dict[str, Any]:
    metrics = get_recovery_metrics()
    return {
        "queues_restored": metrics.queues_restored,
        "downloads_recovered": metrics.downloads_recovered,
        "cleanup_operations": metrics.cleanup_operations,
        "queue_corruption_events": metrics.queue_corruption_events,
        "recovery_failures": metrics.recovery_failures,
        "total_errors": metrics.total_errors,
    }
