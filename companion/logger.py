"""
logger.py – AppLogger

Thread-safe, in-memory log store for MediaForge Companion.
Caps entries at MAX_LOG_ENTRIES (500) and notifies registered UI callbacks
whenever a new line is appended.
"""

from __future__ import annotations

import threading
import traceback
from datetime import datetime
from typing import Callable, Literal, NamedTuple


LogLevel = Literal["INFO", "WARNING", "ERROR", "DEBUG"]

MAX_LOG_ENTRIES: int = 500


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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
            if len(self._entries) > MAX_LOG_ENTRIES:
                self._entries = self._entries[-MAX_LOG_ENTRIES:]
            callbacks = list(self._callbacks)

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
