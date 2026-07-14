"""
automation_steps.py – Pipeline orchestration for browser automation.

Provides :class:`AutomationSteps`, an ordered pipeline of step handlers
that the :class:`BrowserAutomationEngine` executes during a workflow.

Design constraints
------------------
* Stateless — no instance or class state beyond the step list.
* Thread-safe — all methods are safe to call from any thread.
* Never raises — errors are communicated via :class:`AutomationError`.
* Sequential execution — steps run in order; first failure stops the pipeline.
* Cancellation-aware — checks ``session.cancellation_requested`` between steps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from .automation_defs import AutomationState
from .automation_errors import AutomationError, cancelled_error, make_error, AutomationErrorCode
from .automation_session import AutomationSession


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step descriptor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StepDescriptor:
    """Immutable descriptor for a single automation step.

    Attributes
    ----------
    name:
        Human-readable step name (e.g. ``"detect_browser"``).
    handler:
        Callable that executes the step.  Takes an
        :class:`AutomationSession` and returns ``None`` on success
        or an :class:`AutomationError` on failure.
    state:
        The :class:`AutomationState` the session transitions to before
        this step executes.
    description:
        Longer description for logging and diagnostics.
    """

    name: str
    handler: Callable[[AutomationSession], AutomationError | None]
    state: AutomationState
    description: str = ""


# ---------------------------------------------------------------------------
# Default pipeline
# ---------------------------------------------------------------------------

def _build_default_steps() -> list[StepDescriptor]:
    """Build the default ordered step list.

    Import handlers lazily to avoid circular imports at module level.
    """
    from .step_handlers import (  # noqa: PLC0415
        detect_browser,
        validate_extension,
        launch_browser,
        detect_profiles,
        detect_running,
        verify_installation,
    )

    return [
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
            description="Launch the browser with the extension loaded",
        ),
        StepDescriptor(
            name="detect_profiles",
            handler=detect_profiles,
            state=AutomationState.RUNNING,
            description="Discover browser user profiles",
        ),
        StepDescriptor(
            name="detect_running",
            handler=detect_running,
            state=AutomationState.RUNNING,
            description="Check for existing browser instances",
        ),
        StepDescriptor(
            name="verify_installation",
            handler=verify_installation,
            state=AutomationState.RUNNING,
            description="Verify extension is registered in browser profiles",
        ),
    ]


# ---------------------------------------------------------------------------
# AutomationSteps
# ---------------------------------------------------------------------------

class AutomationSteps:
    """Ordered pipeline of step handlers for browser automation.

    Manages the ordered list of :class:`StepDescriptor` entries and
    provides execution logic that the engine calls during a workflow.

    Thread-safe.  Supports concurrent session execution (each session
    gets its own call stack).

    Parameters
    ----------
    steps:
        Optional custom step list.  If ``None``, the default pipeline
        is used.
    """

    def __init__(
        self,
        steps: list[StepDescriptor] | None = None,
    ) -> None:
        self._steps: list[StepDescriptor] = steps if steps is not None else _build_default_steps()

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def step_names(self) -> list[str]:
        """Return the ordered list of step names."""
        return [s.name for s in self._steps]

    @property
    def step_count(self) -> int:
        """Return the number of registered steps."""
        return len(self._steps)

    def get_step(self, name: str) -> StepDescriptor | None:
        """Return the step descriptor by name, or ``None``."""
        for step in self._steps:
            if step.name == name:
                return step
        return None

    def add_step(
        self,
        name: str,
        handler: Callable[[AutomationSession], AutomationError | None],
        state: AutomationState,
        description: str = "",
        index: int | None = None,
    ) -> None:
        """Add a step to the pipeline.

        Parameters
        ----------
        name:
            Unique step name.
        handler:
            Callable that executes the step.
        state:
            The state the session transitions to before this step.
        description:
            Optional human-readable description.
        index:
            Position to insert at (0-based).  If ``None``, appends
            to the end.
        """
        descriptor = StepDescriptor(
            name=name,
            handler=handler,
            state=state,
            description=description,
        )
        if index is not None:
            self._steps.insert(index, descriptor)
        else:
            self._steps.append(descriptor)

    def remove_step(self, name: str) -> bool:
        """Remove a step by name.  Returns ``True`` if found and removed."""
        for i, step in enumerate(self._steps):
            if step.name == name:
                self._steps.pop(i)
                return True
        return False

    def execute(
        self,
        session: AutomationSession,
        progress_callback: Callable[[AutomationSession], None] | None = None,
    ) -> AutomationError | None:
        """Execute all steps sequentially for the given session.

        For each step:
        1. Check if cancellation was requested → return CANCELLED error.
        2. Advance the session to the step's target state.
        3. Invoke the step handler.
        4. If the handler returns an error, return it immediately.
        5. Notify the progress callback.

        Returns ``None`` if all steps complete successfully, or the
        first :class:`AutomationError` encountered.
        """
        for step in self._steps:
            # 1. Cancellation check
            if session.cancellation_requested:
                return cancelled_error(session.session_id)

            # 2. State transition (skip if already in target state)
            if session.current_state is not step.state:
                try:
                    session.advance_to(step.state)
                except (ValueError, RuntimeError) as exc:
                    return make_error(
                        AutomationErrorCode.UNKNOWN,
                        f"State transition failed at step '{step.name}': {exc}",
                        details={
                            "step": step.name,
                            "current_state": session.current_state.value,
                            "target_state": step.state.value,
                        },
                    )

            # 3. Execute step handler
            try:
                error = step.handler(session)
            except Exception as exc:  # noqa: BLE001
                error = make_error(
                    AutomationErrorCode.UNKNOWN,
                    f"Step '{step.name}' raised an exception: {exc}",
                    details={
                        "step": step.name,
                        "exception_type": type(exc).__name__,
                    },
                )

            # 4. Check result
            if error is not None:
                logger.warning(
                    "[AutomationSteps] Step '%s' failed: %s",
                    step.name, error.error_message,
                )
                return error

            # 5. Progress notification
            if progress_callback is not None:
                try:
                    progress_callback(session)
                except Exception:  # noqa: BLE001
                    # Never let callback errors break the pipeline
                    pass

            logger.debug(
                "[AutomationSteps] Step '%s' completed successfully",
                step.name,
            )

        return None

    # ── String representations ──────────────────────────────────────────

    def __str__(self) -> str:
        names = ", ".join(self.step_names)
        return f"AutomationSteps([{names}])"

    def __repr__(self) -> str:
        return f"AutomationSteps(count={self.step_count})"
