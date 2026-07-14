"""
automation_session.py – Mutable lifecycle tracker for automation runs.

Provides :class:`AutomationSession`, a mutable dataclass that tracks
the full lifecycle of a single automation session from creation through
completion, failure, or cancellation.

Design constraints
------------------
* Thread-safe — all mutations are guarded by a reentrant lock.
* No I/O — pure in-memory state tracking.
* No browser interaction — this is the foundation only.
* Supports snapshot for safe cross-thread reads.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .automation_defs import AutomationState
from .automation_errors import AutomationError


# ---------------------------------------------------------------------------
# AutomationSession
# ---------------------------------------------------------------------------

@dataclass
class AutomationSession:
    """Mutable lifecycle tracker for a single automation run.

    A session progresses through :class:`AutomationState` values via
    :meth:`advance_to`.  All mutations are thread-safe.

    Parameters
    ----------
    session_id:
        Unique identifier.  Auto-generated if omitted.
    browser_name:
        Canonical browser name (e.g. ``Chrome``).
    profile_name:
        Browser profile name (populated during detection).
    extension_dir:
        Path to the extension directory.
    target_url:
        URL to open in the browser.
    max_retries:
        Maximum number of recovery retries allowed.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Unique identifier for this session."""

    browser_name: str = ""
    """Canonical browser name (e.g. ``Chrome``)."""

    profile_name: str = ""
    """Browser profile name, populated during detection."""

    extension_dir: str = ""
    """Path to the extension directory."""

    target_url: str = ""
    """URL to open in the browser."""

    max_retries: int = 3
    """Maximum number of recovery retries allowed."""

    # ── Internal state (not part of __init__ defaults) ──────────────────

    _current_state: AutomationState = field(
        default=AutomationState.PENDING, init=False, repr=False,
    )
    _previous_state: AutomationState = field(
        default=AutomationState.PENDING, init=False, repr=False,
    )
    _errors: list[AutomationError] = field(
        default_factory=list, init=False, repr=False,
    )
    _retry_count: int = field(default=0, init=False, repr=False)
    _steps_completed: list[AutomationState] = field(
        default_factory=list, init=False, repr=False,
    )
    _started_at: float = field(default=0.0, init=False, repr=False)
    _finished_at: float = field(default=0.0, init=False, repr=False)
    _cancellation_requested: bool = field(default=False, init=False, repr=False)
    _lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False,
    )

    # ── State accessors ─────────────────────────────────────────────────

    @property
    def current_state(self) -> AutomationState:
        """Return the current state (thread-safe read)."""
        with self._lock:
            return self._current_state

    @property
    def previous_state(self) -> AutomationState:
        """Return the previous state (thread-safe read)."""
        with self._lock:
            return self._previous_state

    @property
    def errors(self) -> list[AutomationError]:
        """Return a copy of the accumulated errors."""
        with self._lock:
            return list(self._errors)

    @property
    def retry_count(self) -> int:
        """Return the current retry count."""
        with self._lock:
            return self._retry_count

    @property
    def steps_completed(self) -> list[AutomationState]:
        """Return a copy of the completed steps list."""
        with self._lock:
            return list(self._steps_completed)

    @property
    def steps_completed_values(self) -> list[str]:
        """Return the string values of completed steps without intermediate copies.

        More efficient than ``[s.value for s in session.steps_completed]``
        because it avoids creating an intermediate list of enum members.
        """
        with self._lock:
            return [s.value for s in self._steps_completed]

    @property
    def started_at(self) -> float:
        """Return the session start timestamp (monotonic)."""
        with self._lock:
            return self._started_at

    @property
    def finished_at(self) -> float:
        """Return the session finish timestamp (monotonic)."""
        with self._lock:
            return self._finished_at

    @property
    def cancellation_requested(self) -> bool:
        """Return ``True`` if cancellation has been requested."""
        with self._lock:
            return self._cancellation_requested

    # ── Derived properties ──────────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        """Return ``True`` if the session is in a terminal state."""
        return self.current_state.is_terminal()

    @property
    def is_active(self) -> bool:
        """Return ``True`` if the session is in an active (non-terminal) state."""
        return self.current_state.is_active()

    @property
    def duration(self) -> float:
        """Return elapsed time in seconds, or total duration if finished."""
        with self._lock:
            if self._started_at == 0.0:
                return 0.0
            if self._finished_at > 0.0:
                return self._finished_at - self._started_at
            return time.monotonic() - self._started_at

    @property
    def can_retry(self) -> bool:
        """Return ``True`` if the session has retries remaining."""
        with self._lock:
            return self._retry_count < self.max_retries

    @property
    def last_error(self) -> AutomationError | None:
        """Return the most recent error, or ``None``."""
        with self._lock:
            return self._errors[-1] if self._errors else None

    # ── Mutations ───────────────────────────────────────────────────────

    def advance_to(self, state: AutomationState) -> None:
        """Transition to the next state.

        Parameters
        ----------
        state:
            The target state.

        Raises
        ------
        ValueError
            If the transition is not valid according to the state machine.

        Thread-safe.
        """
        with self._lock:
            if not AutomationState.can_transition(self._current_state, state):
                raise ValueError(
                    f"Invalid transition: {self._current_state.value} → {state.value}"
                )
            self._previous_state = self._current_state
            self._current_state = state

            if self._started_at == 0.0 and not state.is_terminal():
                self._started_at = time.monotonic()

            if state.is_terminal():
                if self._finished_at == 0.0:
                    self._finished_at = time.monotonic()

            # Record step completion for non-terminal forward progress
            if (
                state not in self._steps_completed
                and state.is_active()
            ):
                self._steps_completed.append(state)

    def record_error(self, error: AutomationError) -> None:
        """Append an error to the session's error history.

        Thread-safe.
        """
        with self._lock:
            self._errors.append(error)

    def increment_retry(self) -> int:
        """Increment and return the retry counter.

        Thread-safe.  Returns the new retry count.
        """
        with self._lock:
            self._retry_count += 1
            return self._retry_count

    def request_cancellation(self) -> None:
        """Request graceful cancellation.

        Thread-safe.  The ``cancellation_requested`` flag is checked
        at step boundaries by the engine worker.
        """
        with self._lock:
            self._cancellation_requested = True

    def reset(self) -> None:
        """Reset the session to its initial state.

        Thread-safe.  Preserves identity (session_id, browser_name)
        but clears all runtime state.
        """
        with self._lock:
            self._current_state = AutomationState.PENDING
            self._previous_state = AutomationState.PENDING
            self._errors.clear()
            self._retry_count = 0
            self._steps_completed.clear()
            self._started_at = 0.0
            self._finished_at = 0.0
            self._cancellation_requested = False

    # ── Snapshot ────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return an immutable snapshot of the session state.

        Useful for cross-thread reads without holding the lock.

        Thread-safe.
        """
        with self._lock:
            return {
                "session_id": self.session_id,
                "browser_name": self.browser_name,
                "profile_name": self.profile_name,
                "extension_dir": self.extension_dir,
                "target_url": self.target_url,
                "current_state": self._current_state.value,
                "previous_state": self._previous_state.value,
                "errors": [
                    {"code": e.code.value, "message": e.message}
                    for e in self._errors
                ],
                "retry_count": self._retry_count,
                "max_retries": self.max_retries,
                "steps_completed": [s.value for s in self._steps_completed],
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "duration": self.duration,
                "cancellation_requested": self._cancellation_requested,
                "is_terminal": self.is_terminal,
            }

    # ── String representations ──────────────────────────────────────────

    def __str__(self) -> str:
        state = self.current_state.value
        return (
            f"AutomationSession(id={self.session_id[:8]}…, "
            f"browser={self.browser_name}, state={state}, "
            f"retries={self.retry_count}/{self.max_retries})"
        )

    def __repr__(self) -> str:
        return (
            f"AutomationSession(session_id={self.session_id!r}, "
            f"browser_name={self.browser_name!r}, "
            f"current_state={self.current_state!r}, "
            f"retry_count={self._retry_count!r})"
        )
