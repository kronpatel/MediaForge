"""
logger.py – AppLogger

Thread-safe, in-memory log store for MediaForge Companion, with optional
production-level file logging via Python's RotatingFileHandler.

In-memory mode
--------------
Caps entries at MAX_LOG_ENTRIES (500) and notifies registered UI callbacks
whenever a new line is appended.

File-logging mode (production)
-------------------------------
When enable_file_logging() is called, writes to companion/logs/ with:
  - RotatingFileHandler, 5 MB max size, 3 backup files
  - UTF-8 encoding, same format as in-memory entries
  - Thread-safe via the existing _lock
  - Logs directory auto-created on first write
"""

from __future__ import annotations

import logging as _logging
from logging.handlers import RotatingFileHandler as _RotatingFileHandler
import os as _os
import time as _time
import threading
import traceback
from datetime import datetime
from typing import Callable, Literal, NamedTuple


LogLevel = Literal["INFO", "WARNING", "ERROR", "DEBUG"]

MAX_LOG_ENTRIES: int = 500

def is_portable_mode() -> bool:
    import sys
    if _os.environ.get("MEDIAFORGE_PORTABLE") == "1":
        return True
    if not getattr(sys, "frozen", False):
        return True
    exe_dir = _os.path.dirname(_os.path.abspath(sys.executable))
    if _os.path.exists(_os.path.join(exe_dir, "portable_settings.json")):
        return True
    return False

def get_companion_logs_dir() -> str:
    src_logs_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs")
    if is_portable_mode():
        return src_logs_dir
    local_app_data = _os.environ.get("LOCALAPPDATA")
    if local_app_data:
        path = _os.path.join(local_app_data, "MediaForge", "logs")
        _os.makedirs(path, exist_ok=True)
        return path
    return src_logs_dir

_LOG_DIR = get_companion_logs_dir()
_LOG_FILE = _os.path.join(_LOG_DIR, "companion.log")
_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3


class LogEntry(NamedTuple):
    """A single immutable log record."""

    timestamp: str      # ISO-8601 local time string
    level: LogLevel
    message: str

    def __str__(self) -> str:
        return f"[{self.timestamp}] [{self.level:<7}] {self.message}"


class AppLogger:
    """
    Central logging component for the Companion.

    Features
    --------
    * Thread-safe append with automatic cap at MAX_LOG_ENTRIES.
    * Registered callbacks are invoked on every new entry (from the calling thread).
    * Debug mode controls whether tracebacks are included in ERROR entries.
    * `get_entries()` returns a snapshot copy for safe UI consumption.
    """

    def __init__(self, *, debug: bool = False) -> None:
        self._entries: list[LogEntry] = []
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[LogEntry], None]] = []
        self.debug: bool = debug
        self.max_entries: int = MAX_LOG_ENTRIES
        self._timings: dict[str, float] = {}
        self._t0 = _time.monotonic()
        self._file_logger: _logging.Logger | None = None
        self._file_enabled: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_max_entries(self, count: int) -> None:
        """Update the maximum log entries cap and truncate existing entries if necessary."""
        with self._lock:
            self.max_entries = max(1, count)
            if len(self._entries) > self.max_entries:
                self._entries = self._entries[-self.max_entries:]

    def export_logs(self, filepath: str) -> None:
        """Write all current log entries to the specified file path."""
        entries = self.get_entries()
        with open(filepath, "w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(str(entry) + "\n")

    def register_callback(self, fn: Callable[[LogEntry], None]) -> None:
        """Register a callable that is invoked whenever a new entry is logged."""
        with self._lock:
            self._callbacks.append(fn)

    def unregister_callback(self, fn: Callable[[LogEntry], None]) -> None:
        """Remove a previously registered callback."""
        with self._lock:
            self._callbacks = [cb for cb in self._callbacks if cb is not fn]

    def log(
        self,
        message: str,
        level: LogLevel = "INFO",
        *,
        exc: BaseException | None = None,
    ) -> None:
        """
        Append a new log entry.

        Parameters
        ----------
        message:
            Human-readable description of the event.
        level:
            One of INFO, WARNING, ERROR, DEBUG.
        exc:
            Optional exception.  If ``debug`` is True the full traceback is
            appended to the message; otherwise it is suppressed.
        """
        if exc is not None and self.debug:
            tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
            message = f"{message}\n{''.join(tb).strip()}"

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = LogEntry(timestamp=timestamp, level=level, message=message)

        callbacks: list[Callable[[LogEntry], None]] = []
        with self._lock:
            self._entries.append(entry)
            # Discard oldest entries beyond the cap
            if len(self._entries) > self.max_entries:
                self._entries = self._entries[-self.max_entries:]
            callbacks = list(self._callbacks)
            self._write_to_file(entry)

        # Fire callbacks outside the lock to avoid deadlocks
        for cb in callbacks:
            try:
                cb(entry)
            except Exception:
                pass  # Never let a UI callback crash the logger

    # Convenience shorthands
    def info(self, message: str, **kwargs) -> None:
        self.log(message, "INFO", **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        self.log(message, "WARNING", **kwargs)

    def error(self, message: str, **kwargs) -> None:
        self.log(message, "ERROR", **kwargs)

    def debug_log(self, message: str, **kwargs) -> None:
        if self.debug:
            self.log(message, "DEBUG", **kwargs)

    def get_entries(self) -> list[LogEntry]:
        """Return a snapshot copy of all current log entries."""
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        """Discard all stored entries."""
        with self._lock:
            self._entries.clear()

    # ------------------------------------------------------------------
    # Production file logging
    # ------------------------------------------------------------------

    def enable_file_logging(self) -> None:
        """Enable production-grade file logging with rotation.

        Creates companion/logs/ directory if missing and configures a
        RotatingFileHandler (5 MB max, 3 backups, UTF-8).
        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._file_enabled:
            return
        _os.makedirs(_LOG_DIR, exist_ok=True)
        handler = _RotatingFileHandler(
            _LOG_FILE,
            maxBytes=_MAX_FILE_SIZE,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(_logging.Formatter(
            "[%(asctime)s] [%(levelname)-7s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        handler.setLevel(_logging.DEBUG)
        logger = _logging.getLogger("MediaForge")
        logger.setLevel(_logging.DEBUG)
        logger.addHandler(handler)
        logger.propagate = False
        self._file_logger = logger
        self._file_enabled = True
        self.info(f"[Logger] File logging enabled: {_LOG_FILE}")

    @property
    def file_logging_enabled(self) -> bool:
        return self._file_enabled

    def get_log_file_path(self) -> str:
        return _LOG_FILE

    def _write_to_file(self, entry: LogEntry) -> None:
        if not self._file_enabled or self._file_logger is None:
            return
        level_map = {
            "DEBUG": _logging.DEBUG,
            "INFO": _logging.INFO,
            "WARNING": _logging.WARNING,
            "ERROR": _logging.ERROR,
        }
        self._file_logger.log(level_map.get(entry.level, _logging.INFO),
                              "%s", entry.message)

    # ------------------------------------------------------------------
    # Startup timing helpers
    # ------------------------------------------------------------------

    def mark_timing(self, label: str) -> None:
        """Record a named timing checkpoint relative to logger creation."""
        with self._lock:
            self._timings[label] = _time.monotonic()

    def log_startup_timings(self) -> None:
        """Log all recorded timing checkpoints as a formatted table with total."""
        with self._lock:
            timings = dict(self._timings)
        if not timings:
            return
        t0 = self._t0
        lines = [""]
        for label, t in timings.items():
            elapsed = (t - t0) * 1000
            label_padded = (label + " ").ljust(27, ".")
            lines.append(f"{label_padded} {elapsed:>6.0f} ms")
        last = timings.get("Startup Complete") or max(timings.values(), default=t0)
        total = (last - t0) * 1000
        lines.append("")
        lines.append(f"{'Startup Complete':<28} {total:>6.0f} ms")
        self.info("Startup Performance\n" + "\n".join(lines))
