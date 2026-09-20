"""
automation_defs.py – Automation state machine and error code definitions.

Defines the lifecycle states for browser automation sessions and
typed error categories for diagnosing automation failures.

Design constraints
------------------
* Pure data — no I/O, no side effects.
* Enum values are immutable.
* Thread-safe — enum members are process-wide singletons.
"""

from __future__ import annotations

from enum import Enum


# ---------------------------------------------------------------------------
# Valid transition table (module-level to avoid enum annotation issues)
# ---------------------------------------------------------------------------

# Populated after AutomationState is defined (see bottom of file).
_VALID_TRANSITIONS: dict[AutomationState, set[AutomationState]] = {}


# ---------------------------------------------------------------------------
# Automation state machine
# ---------------------------------------------------------------------------

class AutomationState(Enum):
    """Lifecycle states for a browser automation session.

    Transitions are validated against the allowed transition table
    via :meth:`can_transition`.
    """

    PENDING    = "pending"
    """Session created but not yet started."""

    LAUNCHING  = "launching"
    """Browser process is being started."""

    RUNNING    = "running"
    """Browser is running and automation is in progress."""

    COMPLETED  = "completed"
    """Automation finished successfully (terminal)."""

    FAILED     = "failed"
    """Automation failed with an unrecoverable error (terminal)."""

    CANCELLED  = "cancelled"
    """Session was cancelled by the caller (terminal)."""

    @classmethod
    def can_transition(cls, source: AutomationState, target: AutomationState) -> bool:
        """Return ``True`` if *source* → *target* is a valid transition."""
        allowed = _VALID_TRANSITIONS.get(source, set())
        return target in allowed

    @classmethod
    def ordered_steps(cls) -> list[AutomationState]:
        """Return the normal execution order (non-terminal states)."""
        return [cls.PENDING, cls.LAUNCHING, cls.RUNNING]

    def is_terminal(self) -> bool:
        """Return ``True`` if this state has no outgoing transitions."""
        return len(_VALID_TRANSITIONS.get(self, set())) == 0

    def is_error(self) -> bool:
        """Return ``True`` if this state represents a failure."""
        return self is AutomationState.FAILED

    def is_active(self) -> bool:
        """Return ``True`` if this state represents an in-progress session."""
        return self in (
            AutomationState.PENDING,
            AutomationState.LAUNCHING,
            AutomationState.RUNNING,
        )

    def step_index(self) -> int:
        """Return the 0-based index in the normal execution order.

        Returns ``-1`` for terminal/error states outside the normal flow.
        """
        steps = self.ordered_steps()
        try:
            return steps.index(self)
        except ValueError:
            return -1


# ---------------------------------------------------------------------------
# Populate transition table after AutomationState is defined
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS[AutomationState.PENDING]   = {
    AutomationState.LAUNCHING, AutomationState.CANCELLED, AutomationState.FAILED,
}
_VALID_TRANSITIONS[AutomationState.LAUNCHING] = {
    AutomationState.RUNNING, AutomationState.COMPLETED, AutomationState.CANCELLED, AutomationState.FAILED,
}
_VALID_TRANSITIONS[AutomationState.RUNNING]   = {
    AutomationState.COMPLETED, AutomationState.CANCELLED, AutomationState.FAILED,
}
_VALID_TRANSITIONS[AutomationState.COMPLETED] = set()  # terminal
_VALID_TRANSITIONS[AutomationState.FAILED]    = set()  # terminal
_VALID_TRANSITIONS[AutomationState.CANCELLED] = set()  # terminal


# ---------------------------------------------------------------------------
# Automation error codes
# ---------------------------------------------------------------------------

class AutomationErrorCode(Enum):
    """Typed error categories for browser automation operations.

    Each code maps to a specific failure mode and can be used by
    recovery logic to determine the appropriate remediation strategy.
    """

    SUCCESS              = "success"
    """Operation completed without error."""

    LAUNCH_FAILED        = "launch_failed"
    """Browser process could not be started."""

    TIMEOUT              = "timeout"
    """An operation exceeded its time limit."""

    CANCELLED            = "cancelled"
    """Operation was cancelled by the caller."""

    CONNECTION_LOST      = "connection_lost"
    """Communication with the browser process was lost."""

    BROWSER_NOT_FOUND    = "browser_not_found"
    """The target browser executable was not found on the system."""

    BROWSER_CLOSED       = "browser_closed"
    """The browser process exited unexpectedly."""

    EXTENSION_MISSING    = "extension_missing"
    """The extension directory or required files are missing."""

    PERMISSION_DENIED    = "permission_denied"
    """Insufficient OS permissions to perform the operation."""

    UNKNOWN              = "unknown"
    """An unclassified error occurred."""

    @property
    def is_success(self) -> bool:
        """Return ``True`` for the ``SUCCESS`` code."""
        return self is AutomationErrorCode.SUCCESS

    @property
    def is_recoverable(self) -> bool:
        """Return ``True`` if this error code typically allows retry.

        This is a best-effort heuristic; actual recoverability depends
        on context and the specific step that failed.
        """
        _recoverable = {
            AutomationErrorCode.TIMEOUT,
            AutomationErrorCode.CONNECTION_LOST,
            AutomationErrorCode.BROWSER_CLOSED,
            AutomationErrorCode.LAUNCH_FAILED,
        }
        return self in _recoverable
