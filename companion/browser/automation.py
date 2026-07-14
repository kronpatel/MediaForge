"""
automation.py – BrowserAutomationEngine singleton.

Core orchestrator for browser automation sessions.  Manages session
lifecycle, active/completed session tracking, cancellation, and
cleanup.

Supports lightweight pipeline factories for card-level actions
(launch, install, verify) alongside the full default pipeline.

Design constraints
------------------
* Singleton — one engine instance per process.
* Thread-safe — all session registry mutations are guarded by a lock.
* Never raises — errors are communicated via :class:`AutomationResult`.
* Non-blocking — ``run_async`` returns a session ID immediately.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from .automation_defs import AutomationErrorCode, AutomationState
from .automation_errors import AutomationError, cancelled_error, make_error
from .automation_result import AutomationResult
from .automation_session import AutomationSession
from .automation_steps import AutomationSteps, StepDescriptor


# ---------------------------------------------------------------------------
# Callback type
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[AutomationSession], None]
"""Signature for progress notification callbacks.

The callback receives the :class:`AutomationSession` snapshot and must
be thread-safe (it may be invoked from a worker thread).
"""


# ---------------------------------------------------------------------------
# Pipeline factory functions
# ---------------------------------------------------------------------------

def create_launch_pipeline() -> AutomationSteps:
    """Pipeline for launching a browser without extension loading.

    Steps: detect_browser → launch_browser.
    """
    from .step_handlers import detect_browser, launch_browser  # noqa: PLC0415

    return AutomationSteps(steps=[
        StepDescriptor(
            name="detect_browser",
            handler=detect_browser,
            state=AutomationState.PENDING,
            description="Detect the target browser on the system",
        ),
        StepDescriptor(
            name="launch_browser",
            handler=launch_browser,
            state=AutomationState.LAUNCHING,
            description="Launch the browser without extension",
        ),
    ])


def create_install_pipeline() -> AutomationSteps:
    """Pipeline for validating extension and launching browser with it.

    Steps: detect_browser → validate_extension → launch_browser.
    """
    from .step_handlers import (  # noqa: PLC0415
        detect_browser,
        launch_browser,
        validate_extension,
    )

    return AutomationSteps(steps=[
        StepDescriptor(
            name="detect_browser",
            handler=detect_browser,
            state=AutomationState.PENDING,
            description="Detect the target browser on the system",
        ),
        StepDescriptor(
            name="validate_extension",
            handler=validate_extension,
            state=AutomationState.PENDING,
            description="Validate extension directory and manifest",
        ),
        StepDescriptor(
            name="launch_browser",
            handler=launch_browser,
            state=AutomationState.LAUNCHING,
            description="Launch browser with extension loaded",
        ),
    ])


def create_verify_pipeline() -> AutomationSteps:
    """Pipeline for verifying extension installation.

    Steps: detect_browser → validate_extension → verify_installation.
    """
    from .step_handlers import (  # noqa: PLC0415
        detect_browser,
        validate_extension,
        verify_installation,
    )

    return AutomationSteps(steps=[
        StepDescriptor(
            name="detect_browser",
            handler=detect_browser,
            state=AutomationState.PENDING,
            description="Detect the target browser on the system",
        ),
        StepDescriptor(
            name="validate_extension",
            handler=validate_extension,
            state=AutomationState.PENDING,
            description="Validate extension directory and manifest",
        ),
        StepDescriptor(
            name="verify_installation",
            handler=verify_installation,
            state=AutomationState.RUNNING,
            description="Verify extension is registered in browser profiles",
        ),
    ])


def create_open_ext_page_pipeline() -> AutomationSteps:
    """Pipeline for launching browser and navigating to extensions page.

    Steps: detect_browser → launch_browser.
    """
    from .step_handlers import detect_browser, launch_browser  # noqa: PLC0415

    return AutomationSteps(steps=[
        StepDescriptor(
            name="detect_browser",
            handler=detect_browser,
            state=AutomationState.PENDING,
            description="Detect the target browser on the system",
        ),
        StepDescriptor(
            name="launch_browser",
            handler=launch_browser,
            state=AutomationState.LAUNCHING,
            description="Launch browser and open extensions page",
        ),
    ])


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------

_instance: BrowserAutomationEngine | None = None
_instance_lock = threading.Lock()


def get_automation_engine(
    logger: logging.Logger | Any | None = None,
) -> BrowserAutomationEngine:
    """Return the global :class:`BrowserAutomationEngine` singleton.

    On first call, creates the engine with the given logger.  Subsequent
    calls return the existing instance (ignoring the *logger* parameter).

    Parameters
    ----------
    logger:
        Logger instance for the engine.  Only used on first call.
    """
    global _instance
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is not None:
            return _instance
        _instance = BrowserAutomationEngine(logger=logger)
        return _instance


def reset_automation_engine() -> None:
    """Reset the global singleton (for testing only).

    Calls :meth:`BrowserAutomationEngine.shutdown` on the existing
    instance before discarding it.
    """
    global _instance
    with _instance_lock:
        if _instance is not None:
            _instance.shutdown()
            _instance = None


# ---------------------------------------------------------------------------
# BrowserAutomationEngine
# ---------------------------------------------------------------------------

class BrowserAutomationEngine:
    """Orchestrates browser automation session lifecycles.

    Manages session creation, state transitions, cancellation, and
    cleanup.  Supports both the full default pipeline and lightweight
    custom pipelines for individual card actions.

    Thread-safe.  Supports concurrent sessions.

    Parameters
    ----------
    logger:
        Application logger instance.
    max_retries:
        Default maximum recovery attempts per session.
    """

    def __init__(
        self,
        logger: logging.Logger | Any | None = None,
        max_retries: int = 3,
    ) -> None:
        self._logger = logger or logging.getLogger("automation")
        self._max_retries = max_retries
        self._lock = threading.RLock()
        self._shutdown_event = threading.Event()

        # Session registries
        self._active_sessions: dict[str, AutomationSession] = {}
        self._completed_sessions: dict[str, AutomationResult] = {}
        self._worker_threads: dict[str, threading.Thread] = {}

        # Step pipeline
        self._automation_steps = AutomationSteps()

        # Maximum completed sessions to retain before cleanup
        self._max_completed: int = 100

    # ── Public API ──────────────────────────────────────────────────────

    def run(
        self,
        browser_name: str,
        extension_dir: str = "",
        target_url: str = "",
        progress_callback: ProgressCallback | None = None,
        pipeline: AutomationSteps | None = None,
    ) -> AutomationResult:
        """Execute an automation workflow synchronously.

        Blocks until the session reaches a terminal state.  For
        non-blocking execution, use :meth:`run_async`.

        Parameters
        ----------
        browser_name:
            Canonical browser name (e.g. ``Chrome``).
        extension_dir:
            Path to the extension directory.
        target_url:
            URL to open in the browser.
        progress_callback:
            Optional callback invoked on state changes.
        pipeline:
            Optional custom pipeline.  If ``None``, the engine's
            default full pipeline is used.

        Returns
        -------
        AutomationResult
            The outcome of the automation run.
        """
        if self._shutdown_event.is_set():
            return self._make_shutdown_result()

        session = self._create_session(
            browser_name=browser_name,
            extension_dir=extension_dir,
            target_url=target_url,
        )

        result = self._execute_workflow(session, progress_callback, pipeline)
        self.cleanup_completed()
        return result

    def run_async(
        self,
        browser_name: str,
        extension_dir: str = "",
        target_url: str = "",
        progress_callback: ProgressCallback | None = None,
        pipeline: AutomationSteps | None = None,
    ) -> str:
        """Execute an automation workflow on a background thread.

        Returns the session ID immediately.  Use :meth:`get_session`
        to poll status, or provide a *progress_callback* for live
        updates.

        Parameters
        ----------
        browser_name:
            Canonical browser name.
        extension_dir:
            Path to the extension directory.
        target_url:
            URL to open in the browser.
        progress_callback:
            Optional callback invoked on state changes.
        pipeline:
            Optional custom pipeline.  If ``None``, the engine's
            default full pipeline is used.

        Returns
        -------
        str
            The session ID.
        """
        if self._shutdown_event.is_set():
            session = self._create_session(
                browser_name=browser_name,
                extension_dir=extension_dir,
                target_url=target_url,
            )
            result = self._make_shutdown_result(session.session_id)
            with self._lock:
                self._completed_sessions[session.session_id] = result
                self._active_sessions.pop(session.session_id, None)
            self.cleanup_completed()
            return session.session_id

        session = self._create_session(
            browser_name=browser_name,
            extension_dir=extension_dir,
            target_url=target_url,
        )

        thread = threading.Thread(
            target=self._worker_thread,
            args=(session.session_id, progress_callback, pipeline),
            daemon=True,
            name=f"AutoWorker-{session.session_id[:8]}",
        )

        with self._lock:
            self._worker_threads[session.session_id] = thread

        thread.start()
        return session.session_id

    def cancel(self, session_id: str) -> bool:
        """Request cancellation of a running session.

        Parameters
        ----------
        session_id:
            The session to cancel.

        Returns
        -------
        bool
            ``True`` if cancellation was requested, ``False`` if the
            session was not found or already completed.
        """
        with self._lock:
            session = self._active_sessions.get(session_id)
            if session is None:
                return False
            if session.is_terminal:
                return False
            session.request_cancellation()
            return True

    def cancel_all_for_browser(self, browser_name: str) -> int:
        """Cancel all active sessions for the given browser name.

        Parameters
        ----------
        browser_name:
            Canonical browser name.

        Returns
        -------
        int
            Number of sessions cancelled.
        """
        cancelled = 0
        with self._lock:
            to_cancel = [
                sid for sid, s in self._active_sessions.items()
                if s.browser_name == browser_name and not s.is_terminal
            ]
        for sid in to_cancel:
            if self.cancel(sid):
                cancelled += 1
        return cancelled

    def get_session(self, session_id: str) -> AutomationSession | None:
        """Return the session by ID, or ``None`` if not found.

        Returns a reference to the live session object (thread-safe
        reads via its own lock).
        """
        with self._lock:
            return self._active_sessions.get(session_id)

    def get_result(self, session_id: str) -> AutomationResult | None:
        """Return the result of a completed session, or ``None``."""
        with self._lock:
            return self._completed_sessions.get(session_id)

    def get_active_sessions(self) -> list[AutomationSession]:
        """Return all sessions in a non-terminal state."""
        with self._lock:
            return [
                s for s in self._active_sessions.values()
                if not s.is_terminal
            ]

    def get_active_session_ids(self) -> list[str]:
        """Return all active (non-terminal) session IDs."""
        with self._lock:
            return [
                sid for sid, s in self._active_sessions.items()
                if not s.is_terminal
            ]

    def get_completed_sessions(self) -> list[AutomationResult]:
        """Return all completed session results (most recent first)."""
        with self._lock:
            results = list(self._completed_sessions.values())
        return sorted(results, key=lambda r: r.execution_time, reverse=True)

    @property
    def active_count(self) -> int:
        """Return the number of active (non-terminal) sessions."""
        with self._lock:
            return sum(
                1 for s in self._active_sessions.values()
                if not s.is_terminal
            )

    @property
    def completed_count(self) -> int:
        """Return the number of completed sessions in the registry."""
        with self._lock:
            return len(self._completed_sessions)

    def shutdown(self) -> None:
        """Cancel all active sessions and release resources.

        After shutdown, :meth:`run` and :meth:`run_async` return
        immediately with a shutdown error result.
        """
        self._shutdown_event.set()

        with self._lock:
            active_ids = list(self._active_sessions.keys())

        for sid in active_ids:
            self.cancel(sid)

        # Wait briefly for worker threads to exit
        with self._lock:
            threads = list(self._worker_threads.values())

        for t in threads:
            t.join(timeout=2.0)

        self._logger.info("[AutomationEngine] Shutdown complete.")

    def cleanup_completed(self, max_results: int | None = None) -> int:
        """Remove old completed sessions, keeping at most *max_results*.

        Returns the number of entries removed.
        """
        limit = max_results if max_results is not None else self._max_completed
        with self._lock:
            if len(self._completed_sessions) <= limit:
                return 0
            # Remove oldest entries (by insertion order, roughly FIFO)
            excess = len(self._completed_sessions) - limit
            to_remove = list(self._completed_sessions.keys())[:excess]
            for sid in to_remove:
                del self._completed_sessions[sid]
            return len(to_remove)

    # ── Internal: session lifecycle ─────────────────────────────────────

    def _create_session(
        self,
        browser_name: str,
        extension_dir: str = "",
        target_url: str = "",
    ) -> AutomationSession:
        """Create and register a new session."""
        session = AutomationSession(
            browser_name=browser_name,
            extension_dir=extension_dir,
            target_url=target_url,
            max_retries=self._max_retries,
        )
        with self._lock:
            self._active_sessions[session.session_id] = session
        self._logger.info(
            "[AutomationEngine] Session created: %s (browser=%s)",
            session.session_id[:8], browser_name,
        )
        return session

    def _execute_workflow(
        self,
        session: AutomationSession,
        progress_callback: ProgressCallback | None = None,
        pipeline: AutomationSteps | None = None,
    ) -> AutomationResult:
        """Execute the step workflow for a session (synchronous path).

        Delegates to :class:`AutomationSteps` for sequential step
        execution.  Supports cancellation between steps and error
        propagation.
        """
        steps = pipeline if pipeline is not None else self._automation_steps

        try:
            # Execute the step pipeline
            pipeline_error = steps.execute(session, progress_callback)

            if pipeline_error is not None:
                session.record_error(pipeline_error)
                session.advance_to(AutomationState.FAILED)
                self._notify_progress(session, progress_callback)
                return self._finalize_session(
                    session, success=False, error=pipeline_error,
                )

            # All steps passed — mark as completed
            session.advance_to(AutomationState.COMPLETED)
            self._notify_progress(session, progress_callback)

        except (ValueError, RuntimeError) as exc:
            error = make_error(
                AutomationErrorCode.UNKNOWN,
                str(exc),
                details={"exception_type": type(exc).__name__},
            )
            session.record_error(error)
            try:
                session.advance_to(AutomationState.FAILED)
            except (ValueError, RuntimeError):
                pass
            return self._finalize_session(
                session, success=False, error=error,
            )

        except Exception as exc:  # noqa: BLE001
            error = make_error(
                AutomationErrorCode.UNKNOWN,
                str(exc),
                details={"exception_type": type(exc).__name__},
            )
            session.record_error(error)
            try:
                session.advance_to(AutomationState.FAILED)
            except (ValueError, RuntimeError):
                pass
            return self._finalize_session(
                session, success=False, error=error,
            )

        return self._finalize_session(session, success=True)

    def _worker_thread(
        self,
        session_id: str,
        progress_callback: ProgressCallback | None = None,
        pipeline: AutomationSteps | None = None,
    ) -> None:
        """Background worker for ``run_async`` sessions."""
        with self._lock:
            session = self._active_sessions.get(session_id)

        if session is None:
            self._logger.warning(
                "[AutomationEngine] Worker started for unknown session: %s",
                session_id[:8],
            )
            with self._lock:
                self._worker_threads.pop(session_id, None)
            return

        try:
            result = self._execute_workflow(session, progress_callback, pipeline)
        except Exception as exc:  # noqa: BLE001
            self._logger.error(
                "[AutomationEngine] Unexpected error in worker for %s: %s",
                session_id[:8], exc,
            )
            error = make_error(
                AutomationErrorCode.UNKNOWN,
                f"Worker thread exception: {exc}",
                details={"exception_type": type(exc).__name__},
            )
            session.record_error(error)
            try:
                session.advance_to(AutomationState.FAILED)
            except (ValueError, RuntimeError):
                pass
            result = self._finalize_session(
                session, success=False, error=error,
            )

        with self._lock:
            self._worker_threads.pop(session_id, None)
            self.cleanup_completed()

        self._logger.info(
            "[AutomationEngine] Session %s finished: %s",
            session_id[:8], "success" if result.success else "failed",
        )

    def _finalize_session(
        self,
        session: AutomationSession,
        success: bool,
        error: AutomationError | None = None,
    ) -> AutomationResult:
        """Move a session from active to completed registry.

        Builds and stores the :class:`AutomationResult`, removes the
        session from the active registry, and returns the result so
        callers don't need to build a separate instance.
        """
        result = self._build_result(session, success=success, error=error)
        with self._lock:
            self._completed_sessions[session.session_id] = result
            self._active_sessions.pop(session.session_id, None)
        return result

    def _build_result(
        self,
        session: AutomationSession,
        success: bool,
        error: AutomationError | None = None,
    ) -> AutomationResult:
        """Build an AutomationResult from the current session state."""
        duration = session.duration
        return AutomationResult(
            success=success,
            session_id=session.session_id,
            execution_time=duration,
            metadata={
                "browser_name": session.browser_name,
                "profile_name": session.profile_name,
                "steps_completed": session.steps_completed_values,
                "retry_count": session.retry_count,
            },
            error=error,
        )

    def _make_shutdown_result(
        self,
        session_id: str = "",
    ) -> AutomationResult:
        """Create a result indicating the engine was shut down."""
        error = cancelled_error(session_id)
        return AutomationResult(
            success=False,
            session_id=session_id,
            error=error,
        )

    def _notify_progress(
        self,
        session: AutomationSession,
        callback: ProgressCallback | None,
    ) -> None:
        """Invoke the progress callback if provided."""
        if callback is None:
            return
        try:
            callback(session)
        except Exception as exc:  # noqa: BLE001
            self._logger.debug(
                "[AutomationEngine] Progress callback error for %s: %s",
                session.session_id[:8], exc,
            )

    # ── String representations ──────────────────────────────────────────

    def __str__(self) -> str:
        active = self.active_count
        completed = self.completed_count
        return (
            f"BrowserAutomationEngine("
            f"active={active}, completed={completed})"
        )

    def __repr__(self) -> str:
        return (
            f"BrowserAutomationEngine("
            f"max_retries={self._max_retries!r}, "
            f"active={self.active_count!r}, "
            f"completed={self.completed_count!r})"
        )
