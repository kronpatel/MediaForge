"""
automation_result.py – Immutable outcome type for automation sessions.

Provides :class:`AutomationResult`, a frozen dataclass returned after
every completed, failed, or cancelled automation run.

Design constraints
------------------
* Frozen — result instances are immutable once created.
* No I/O — pure data carrier.
* Thread-safe — safe to share across threads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .automation_defs import AutomationErrorCode, AutomationState
from .automation_errors import AutomationError


# ---------------------------------------------------------------------------
# AutomationResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AutomationResult:
    """Immutable outcome of a completed automation session.

    Every run of :meth:`BrowserAutomationEngine.run` or
    :meth:`BrowserAutomationEngine.run_async` returns one of these
    once the session reaches a terminal state.
    """

    success: bool = False
    """``True`` if the automation completed successfully."""

    session_id: str = ""
    """Unique identifier for the session that produced this result."""

    execution_time: float = 0.0
    """Wall-clock duration of the automation run in seconds."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Arbitrary metadata collected during the run (e.g. PID, paths)."""

    error: AutomationError | None = None
    """Error details if the session failed or was cancelled."""

    # ── Derived properties ──────────────────────────────────────────────

    @property
    def failed(self) -> bool:
        """Return ``True`` if the automation failed."""
        return not self.success and self.error is not None

    @property
    def was_cancelled(self) -> bool:
        """Return ``True`` if the automation was cancelled."""
        if self.error is None:
            return False
        return self.error.code is AutomationErrorCode.CANCELLED

    @property
    def error_code(self) -> AutomationErrorCode:
        """Return the error code, or ``SUCCESS`` if there was no error."""
        if self.error is None:
            return AutomationErrorCode.SUCCESS
        return self.error.code

    @property
    def error_message(self) -> str:
        """Return a human-readable error message, or empty string."""
        if self.error is None:
            return ""
        return self.error.message

    @property
    def metadata_value(self) -> dict[str, Any]:
        """Return a copy of the metadata dict.

        Use this when callers need a mutable copy without violating
        the frozen constraint.
        """
        return dict(self.metadata)

    def __str__(self) -> str:
        status = "success" if self.success else "failed"
        if self.was_cancelled:
            status = "cancelled"
        parts = [f"AutomationResult({status}"]
        if self.session_id:
            parts.append(f"session={self.session_id}")
        if self.execution_time > 0:
            parts.append(f"time={self.execution_time:.2f}s")
        if self.error:
            parts.append(f"error={self.error.error_message}")
        return ", ".join(parts) + ")"

    def __repr__(self) -> str:
        return (
            f"AutomationResult(success={self.success!r}, "
            f"session_id={self.session_id!r}, "
            f"execution_time={self.execution_time!r}, "
            f"error={self.error!r})"
        )
