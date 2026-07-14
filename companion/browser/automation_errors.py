"""
automation_errors.py – Structured error types for browser automation.

Provides :class:`AutomationError`, a frozen dataclass that carries
typed error information from any automation step without raising
exceptions.

Design constraints
------------------
* Frozen — error instances are immutable once created.
* No I/O — pure data carrier.
* Thread-safe — safe to share across threads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .automation_defs import AutomationErrorCode


# ---------------------------------------------------------------------------
# AutomationError
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AutomationError:
    """Structured error information from an automation step.

    Every error carries a typed :class:`AutomationErrorCode`, a
    human-readable message, and optional diagnostic details.  The
    ``recoverable`` flag hints whether recovery logic should attempt
    a retry.

    Parameters
    ----------
    code:
        The error category.
    message:
        Human-readable description of the failure.
    details:
        Optional structured diagnostic data (e.g. exception info,
        command that failed, paths involved).
    recoverable:
        ``True`` if the error is potentially recoverable via retry
        or alternative strategy.
    """

    code: AutomationErrorCode = AutomationErrorCode.UNKNOWN
    """High-level error category."""

    message: str = ""
    """Human-readable diagnostic message."""

    details: dict[str, Any] = field(default_factory=dict)
    """Optional structured diagnostic data."""

    recoverable: bool = False
    """``True`` if recovery logic should attempt a retry."""

    @property
    def is_success(self) -> bool:
        """Return ``True`` if this error represents a successful outcome."""
        return self.code is AutomationErrorCode.SUCCESS

    @property
    def is_recoverable(self) -> bool:
        """Return ``True`` if this error is potentially recoverable.

        Combines the instance-level ``recoverable`` flag with the
        error code's built-in heuristic.
        """
        return self.recoverable or self.code.is_recoverable

    @property
    def error_message(self) -> str:
        """Return a formatted error string for logging."""
        parts: list[str] = []
        if self.code is not AutomationErrorCode.UNKNOWN:
            parts.append(f"[{self.code.value}]")
        if self.message:
            parts.append(self.message)
        return " ".join(parts) if parts else "Unknown error"

    def __str__(self) -> str:
        return self.error_message

    def __repr__(self) -> str:
        return (
            f"AutomationError(code={self.code!r}, message={self.message!r}, "
            f"recoverable={self.recoverable!r})"
        )


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------

def make_error(
    code: AutomationErrorCode,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    recoverable: bool | None = None,
) -> AutomationError:
    """Create an :class:`AutomationError` with sensible defaults.

    If *recoverable* is ``None``, it is derived from the error code's
    ``is_recoverable`` heuristic.
    """
    if recoverable is None:
        recoverable = code.is_recoverable
    return AutomationError(
        code=code,
        message=message,
        details=details or {},
        recoverable=recoverable,
    )


def browser_not_found(browser_name: str) -> AutomationError:
    """Create a BROWSER_NOT_FOUND error for the given browser."""
    return make_error(
        AutomationErrorCode.BROWSER_NOT_FOUND,
        f"Browser not found: {browser_name}",
        details={"browser_name": browser_name},
        recoverable=False,
    )


def launch_failed(browser_name: str, reason: str = "") -> AutomationError:
    """Create a LAUNCH_FAILED error for the given browser."""
    msg = f"Failed to launch {browser_name}"
    if reason:
        msg += f": {reason}"
    return make_error(
        AutomationErrorCode.LAUNCH_FAILED,
        msg,
        details={"browser_name": browser_name, "reason": reason},
        recoverable=True,
    )


def timeout_error(step: str, duration: float = 0.0) -> AutomationError:
    """Create a TIMEOUT error for the given step."""
    msg = f"Timeout during {step}"
    if duration > 0:
        msg += f" after {duration:.1f}s"
    return make_error(
        AutomationErrorCode.TIMEOUT,
        msg,
        details={"step": step, "duration": duration},
        recoverable=True,
    )


def connection_lost(browser_name: str, pid: int | None = None) -> AutomationError:
    """Create a CONNECTION_LOST error."""
    msg = f"Connection lost to {browser_name}"
    if pid is not None:
        msg += f" (PID {pid})"
    return make_error(
        AutomationErrorCode.CONNECTION_LOST,
        msg,
        details={"browser_name": browser_name, "pid": pid},
        recoverable=True,
    )


def cancelled_error(session_id: str = "") -> AutomationError:
    """Create a CANCELLED error."""
    return make_error(
        AutomationErrorCode.CANCELLED,
        "Operation was cancelled",
        details={"session_id": session_id} if session_id else {},
        recoverable=False,
    )


def permission_denied(path: str = "") -> AutomationError:
    """Create a PERMISSION_DENIED error."""
    msg = "Permission denied"
    if path:
        msg += f": {path}"
    return make_error(
        AutomationErrorCode.PERMISSION_DENIED,
        msg,
        details={"path": path} if path else {},
        recoverable=False,
    )


def extension_missing(detail: str = "") -> AutomationError:
    """Create an EXTENSION_MISSING error."""
    msg = "Extension files missing or invalid"
    if detail:
        msg += f": {detail}"
    return make_error(
        AutomationErrorCode.EXTENSION_MISSING,
        msg,
        details={"detail": detail} if detail else {},
        recoverable=False,
    )


def unknown_error(message: str = "") -> AutomationError:
    """Create an UNKNOWN error."""
    return make_error(
        AutomationErrorCode.UNKNOWN,
        message or "Unknown error occurred",
        recoverable=False,
    )
