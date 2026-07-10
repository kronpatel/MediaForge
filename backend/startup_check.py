"""Startup validation checks for the MediaForge backend.

Runs non-blocking validation on startup and reports issues via the logger
without preventing the application from starting. Covers:
  - FFmpeg binary availability
  - Write permissions on data directories
  - Queue state file integrity
  - Corrupted log files (placeholder)
"""

from __future__ import annotations

import json
import os
import shutil
import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)

from downloader import BASE_DIR

_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_DIR)
_FFMPEG_DIR = os.path.join(_PROJECT_ROOT, "ffmpeg")


class StartupCheckResult(NamedTuple):
    name: str
    passed: bool
    message: str


def check_ffmpeg() -> StartupCheckResult:
    """Verify that ffmpeg and ffprobe executables exist in the ffmpeg/ directory."""
    ffmpeg = os.path.join(_FFMPEG_DIR, "ffmpeg.exe")
    ffprobe = os.path.join(_FFMPEG_DIR, "ffprobe.exe")

    missing = []
    if not os.path.isfile(ffmpeg):
        missing.append("ffmpeg.exe")
    if not os.path.isfile(ffprobe):
        missing.append("ffprobe.exe")

    if missing:
        return StartupCheckResult(
            name="FFmpeg",
            passed=False,
            message=f"Missing: {', '.join(missing)}. Download from https://ffmpeg.org and place in {_FFMPEG_DIR}",
        )
    try:
        ffmpeg_size = os.path.getsize(ffmpeg)
        ffprobe_size = os.path.getsize(ffprobe)
        return StartupCheckResult(
            name="FFmpeg",
            passed=True,
            message=f"ffmpeg.exe ({ffmpeg_size / 1024:.0f} KB), ffprobe.exe ({ffprobe_size / 1024:.0f} KB)",
        )
    except OSError as e:
        return StartupCheckResult(
            name="FFmpeg",
            passed=False,
            message=f"Error accessing FFmpeg binaries: {e}",
        )


def check_ffmpeg_env_path() -> StartupCheckResult:
    """Fallback: check if ffmpeg is reachable via PATH."""
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return StartupCheckResult(
            name="FFmpeg-PATH",
            passed=True,
            message=f"Found at {ffmpeg_path}",
        )
    return StartupCheckResult(
        name="FFmpeg-PATH",
        passed=False,
        message="Not found in PATH",
    )


def check_queue_file() -> StartupCheckResult:
    """Verify the queue state file exists and is valid JSON (if present)."""
    queue_path = os.path.join(BASE_DIR, "queue_state.json")
    if not os.path.exists(queue_path):
        return StartupCheckResult(
            name="QueueFile",
            passed=True,
            message="No queue_state.json yet (will be created on first save)",
        )
    if os.path.getsize(queue_path) == 0:
        return StartupCheckResult(
            name="QueueFile",
            passed=False,
            message="queue_state.json is empty",
        )
    try:
        with open(queue_path, "r") as f:
            data = json.load(f)
        version = data.get("schema_version", "unknown")
        return StartupCheckResult(
            name="QueueFile",
            passed=True,
            message=f"Valid JSON, schema_version={version}",
        )
    except (json.JSONDecodeError, OSError) as e:
        return StartupCheckResult(
            name="QueueFile",
            passed=False,
            message=f"Corrupted: {e}",
        )


def check_data_dirs() -> StartupCheckResult:
    """Verify we can write to the backend directory."""
    test_file = os.path.join(BASE_DIR, ".write_test")
    try:
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        return StartupCheckResult(
            name="DataDirs",
            passed=True,
            message="Write permission OK",
        )
    except OSError as e:
        return StartupCheckResult(
            name="DataDirs",
            passed=False,
            message=f"Cannot write to {BASE_DIR}: {e}",
        )


def run_startup_checks() -> list[StartupCheckResult]:
    """Run all startup checks and return results."""
    results = [
        check_ffmpeg(),
        check_ffmpeg_env_path(),
        check_queue_file(),
        check_data_dirs(),
    ]
    return results


def log_startup_checks(results: list[StartupCheckResult]) -> None:
    """Log startup check results through the standard logging system."""
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    for r in results:
        status = "OK" if r.passed else "WARN"
        msg = f"[StartupCheck] [{status}] {r.name}: {r.message}"
        if r.passed:
            logger.info(msg)
        else:
            logger.warning(msg)
    logger.info("[StartupCheck] Checks complete: %d/%d passed", passed, total)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    results = run_startup_checks()
    log_startup_checks(results)
