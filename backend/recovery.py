"""Recovery Manager for MediaForge backend.

Handles queue restoration, download recovery, temp file cleanup,
history consistency validation, and schema migration on backend startup.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from downloader import (
    BASE_DIR,
    QUEUE_STATE_FILE,
    QUEUE_STATE_SCHEMA_VERSION,
    HISTORY_FILE,
    _jobs,
    _jobs_lock,
    _active_job_id,
    _download_queue,
    _restore_queue_state,
    _handle_corrupted_queue_state,
    DownloadState,
    cleanup_temp_files,
    get_download_dir,
    validate_transition,
    now_iso,
    logger as downloader_logger,
)

logger = logging.getLogger("kerzox.recovery")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())


# ── Recovery Metrics ──────────────────────────────────────────────────────

@dataclass
class RecoveryMetrics:
    """Lightweight statistics tracking across recovery activities."""
    queues_restored: int = 0
    downloads_recovered: int = 0
    recovery_failures: int = 0
    queue_corruption_events: int = 0
    cleanup_operations: int = 0
    total_errors: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


_metrics = RecoveryMetrics()
_metrics_lock = threading.Lock()


def get_recovery_metrics() -> RecoveryMetrics:
    """Return a snapshot of current recovery metrics."""
    with _metrics_lock:
        return RecoveryMetrics(**_metrics.__dict__)


def _inc_metric(name: str) -> None:
    with _metrics_lock:
        setattr(_metrics, name, getattr(_metrics, name) + 1)


# ── Schema Migration Registry ─────────────────────────────────────────────

_SCHEMA_MIGRATIONS: dict[int, Any] = {}


def register_schema_migration(from_version: int, fn: Any) -> None:
    """Register a migration function for upgrading queue state schema."""
    _SCHEMA_MIGRATIONS[from_version] = fn


def _migrate_queue_state(state: dict[str, Any], from_version: int) -> dict[str, Any] | None:
    """Attempt to migrate queue state from an older schema version.

    Returns the migrated state, or *None* if migration is unavailable.
    """
    current = from_version
    while current < QUEUE_STATE_SCHEMA_VERSION:
        fn = _SCHEMA_MIGRATIONS.get(current)
        if fn is None:
            logger.warning(
                "[Recovery] No migration path from schema v%s to v%s — starting empty",
                from_version, QUEUE_STATE_SCHEMA_VERSION,
            )
            return None
        try:
            state = fn(state)
            current += 1
            logger.info("[Recovery] Migrated queue state from schema v%s", current - 1)
        except Exception as e:
            logger.error("[Recovery] Schema migration from v%s failed: %s", current, e)
            return None
    state["schema_version"] = QUEUE_STATE_SCHEMA_VERSION
    return state


class RecoveryResult:
    """Container for recovery results."""
    def __init__(self) -> None:
        self.jobs_restored: int = 0
        self.jobs_recovered: int = 0
        self.files_cleaned: int = 0
        self.history_consistent: bool = True
        self.history_duplicates_removed: int = 0
        self.errors: list[str] = []


def recover_queue() -> RecoveryResult:
    """Restore the queue from persistent state and recover interrupted jobs.

    Called once on backend startup.
    """
    _t0 = time.monotonic()
    result = RecoveryResult()
    logger.info("[Recovery] Starting queue recovery...")

    # Phase 1: Restore persisted queue state
    try:
        restored = _restore_queue_state()
        result.jobs_restored = restored
        if restored:
            _inc_metric("queues_restored")
        logger.info("[Recovery] Queue restored (%d jobs)", restored)
    except Exception as e:
        msg = f"[Recovery] Queue restore failed: {e}"
        logger.error(msg)
        result.errors.append(msg)
        _inc_metric("recovery_failures")

    # Phase 2: Recover interrupted jobs (running/downloading at crash time)
    try:
        recovered = _recover_interrupted_jobs()
        result.jobs_recovered = recovered
        if recovered:
            _inc_metric("downloads_recovered")
        logger.info("[Recovery] Downloads recovered: %d", recovered)
    except Exception as e:
        msg = f"[Recovery] Download recovery failed: {e}"
        logger.error(msg)
        result.errors.append(msg)
        _inc_metric("recovery_failures")

    # Phase 3: Clean up orphaned temp files
    try:
        cleaned = cleanup_temp_files()
        result.files_cleaned = cleaned
        if cleaned:
            _inc_metric("cleanup_operations")
        logger.info("[Recovery] Cleanup complete (%d files)", cleaned)
    except Exception as e:
        msg = f"[Recovery] Cleanup failed: {e}"
        logger.error(msg)
        result.errors.append(msg)

    # Phase 4: Validate history consistency
    try:
        valid, dupes = validate_history_consistency()
        result.history_consistent = valid
        result.history_duplicates_removed = dupes
        logger.info("[Recovery] History validated (duplicates removed: %d)", dupes)
    except Exception as e:
        msg = f"[Recovery] History validation failed: {e}"
        logger.error(msg)
        result.errors.append(msg)

    # Aggregate error count
    if result.errors:
        with _metrics_lock:
            _metrics.total_errors += len(result.errors)

    logger.info("[Recovery] Recovery complete — restored=%d recovered=%d cleaned=%d errors=%d",
                result.jobs_restored, result.jobs_recovered,
                result.files_cleaned, len(result.errors))

    try:
        from diagnostics import _perf_metrics as _pm
        _pm.record_recovery_time(time.monotonic() - _t0)
    except Exception:
        pass

    return result


def _recover_interrupted_jobs() -> int:
    """Recover jobs that were in a running/downloading/paused state at crash.

    Transitions:
      STARTING / DOWNLOADING → QUEUED (restart download)
      PAUSED → QUEUED (re-queue for resume)
      RECOVERING → QUEUED (re-queue after incomplete recovery)

    Jobs that cannot be recovered (e.g. invalid state) are marked FAILED.
    """
    recovered = 0
    with _jobs_lock:
        for job in list(_jobs.values()):
            try:
                state = DownloadState(job.status)
            except ValueError:
                job.status = DownloadState.FAILED.value
                job.message = "Unrecognised state after crash — marked failed"
                continue

            if state in (DownloadState.STARTING, DownloadState.DOWNLOADING):
                validate_transition(state, DownloadState.QUEUED)
                job.status = DownloadState.QUEUED.value
                job.message = "Restarted after crash recovery"
                job.progress = 0.0
                job.speed = ""
                job.eta = ""
                _download_queue.put(job.id)
                recovered += 1
                logger.info("[Recovery] Recovered job %s (was %s) → QUEUED", job.id, state.value)

            elif state == DownloadState.PAUSED:
                validate_transition(state, DownloadState.QUEUED)
                job.status = DownloadState.QUEUED.value
                job.message = "Re-queued after restart (was paused)"
                _download_queue.put(job.id)
                recovered += 1
                logger.info("[Recovery] Recovered paused job %s → QUEUED", job.id)

            elif state == DownloadState.RECOVERING:
                validate_transition(state, DownloadState.QUEUED)
                job.status = DownloadState.QUEUED.value
                job.message = "Re-queued after incomplete recovery"
                _download_queue.put(job.id)
                recovered += 1
                logger.info("[Recovery] Recovered job %s (was RECOVERING) → QUEUED", job.id)

    return recovered


def validate_history_consistency() -> tuple[bool, int]:
    """Check the history file for structural consistency and deduplicate.

    Returns (is_consistent, duplicates_removed).
    """
    if not os.path.exists(HISTORY_FILE):
        return True, 0

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        logger.warning("[Recovery] Cannot read history file for validation")
        return False, 0

    seen_ids: set[str] = set()
    duplicates = 0
    valid_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
            job_id = entry.get("id", "")
            if job_id in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(job_id)
            valid_lines.append(line)
        except (json.JSONDecodeError, TypeError):
            duplicates += 1
            continue

    if duplicates > 0:
        logger.info("[Recovery] Removed %d duplicate entries from history", duplicates)
        try:
            tmp = HISTORY_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(valid_lines)
            os.replace(tmp, HISTORY_FILE)
        except OSError as e:
            logger.error("Failed to write deduplicated history: %s", e)
            return False, duplicates

    return True, duplicates


def has_corrupted_queue_state() -> bool:
    """Check if the queue state file appears corrupted (quick check)."""
    if not os.path.exists(QUEUE_STATE_FILE):
        return False
    try:
        with open(QUEUE_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return True
        if "schema_version" not in data:
            return True
        return False
    except (OSError, json.JSONDecodeError):
        return True


def handle_corruption() -> None:
    """Handle corrupted queue state by renaming it."""
    _handle_corrupted_queue_state()
