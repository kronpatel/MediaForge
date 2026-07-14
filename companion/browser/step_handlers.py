"""
step_handlers.py – Step handler implementations for browser automation.

Each handler wraps an existing browser API call and returns an
:class:`AutomationError` on failure or ``None`` on success.  Handlers
store their results directly on the :class:`AutomationSession` instance
for downstream consumers.

Design constraints
------------------
* Stateless — no instance or class state beyond the method signature.
* Thread-safe — all browser APIs used are static/idempotent.
* Never raises — errors are communicated via :class:`AutomationError`.
* No UI, no CDP, no browser detection logic changes.
"""

from __future__ import annotations

import logging
from typing import Any

from .automation_defs import AutomationState
from .automation_errors import (
    AutomationError,
    browser_not_found,
    extension_missing,
    launch_failed,
    make_error,
    AutomationErrorCode,
)
from .automation_session import AutomationSession
from .browser_launcher import BrowserLauncher
from .browser_profiles import BrowserProfileManager
from .browser_sessions import BrowserSessionManager
from .browser_registry import BrowserRegistry
from .browser_extension_installer import ExtensionInstallationEngine
import extension_manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step handler protocol (implicit — callable[[AutomationSession], AutomationError | None])
# ---------------------------------------------------------------------------

# Each step handler is a callable:
#   (session: AutomationSession) -> AutomationError | None
#
# Returns None on success, AutomationError on failure.
# Results are stored on the session object for downstream steps.


# ---------------------------------------------------------------------------
# DetectBrowserStep
# ---------------------------------------------------------------------------

def detect_browser(session: AutomationSession) -> AutomationError | None:
    """Detect the target browser and store :class:`BrowserInfo` on the session.

    Uses :meth:`BrowserLauncher.detect_by_name` to find the browser.
    Stores the result in ``session._detected_browser_info`` (private,
    set by the handler).

    Returns ``None`` on success, or an error if the browser is not found.
    """
    browser_name = session.browser_name
    if not browser_name:
        return browser_not_found("(no browser name provided)")

    try:
        info = BrowserLauncher.detect_by_name(browser_name)
    except Exception as exc:  # noqa: BLE001
        return make_error(
            AutomationErrorCode.BROWSER_NOT_FOUND,
            f"Exception detecting {browser_name}: {exc}",
            details={"browser_name": browser_name, "exception_type": type(exc).__name__},
        )

    if info is None or not info.installed:
        return browser_not_found(browser_name)

    # Store on session for downstream steps
    session._detected_browser_info = info  # type: ignore[attr-defined]
    return None


# ---------------------------------------------------------------------------
# ValidateExtensionStep
# ---------------------------------------------------------------------------

def validate_extension(session: AutomationSession) -> AutomationError | None:
    """Validate that the extension directory contains required files.

    Uses :class:`ExtensionInstallationEngine` to check manifest and
    required files.  Stores validation result on
    ``session._extension_validation``.

    Returns ``None`` if valid, or an error describing what is missing.
    """
    ext_dir = session.extension_dir
    if not ext_dir:
        return extension_missing("No extension directory provided")

    try:
        result = ExtensionInstallationEngine.validate_extension(ext_dir)
    except Exception as exc:  # noqa: BLE001
        return make_error(
            AutomationErrorCode.EXTENSION_MISSING,
            f"Exception validating extension: {exc}",
            details={"extension_dir": ext_dir, "exception_type": type(exc).__name__},
        )

    session._extension_validation = result  # type: ignore[attr-defined]

    if result.valid:
        return None

    missing = ", ".join(result.missing_files) if result.missing_files else "unknown"
    return extension_missing(
        f"Extension validation failed: missing={missing}"
    )


# ---------------------------------------------------------------------------
# LaunchBrowserStep
# ---------------------------------------------------------------------------

def launch_browser(session: AutomationSession) -> AutomationError | None:
    """Launch the detected browser with the extension loaded.

    Uses :meth:`BrowserLauncher.launch` with ``--load-extension`` arg.
    Stores the :class:`LaunchResult` on ``session._launch_result``.

    Returns ``None`` on success, or an error if launch failed.
    """
    browser_info = getattr(session, "_detected_browser_info", None)
    if browser_info is None or not browser_info.installed:
        return browser_not_found(session.browser_name)

    ext_dir = session.extension_dir
    args = ["--load-extension", ext_dir] if ext_dir else []

    try:
        result = BrowserLauncher.launch(
            exe_path=browser_info.path,
            args=args,
            url=session.target_url,
        )
    except Exception as exc:  # noqa: BLE001
        return launch_failed(session.browser_name, str(exc))

    session._launch_result = result  # type: ignore[attr-defined]

    if result.success:
        return None

    return launch_failed(
        session.browser_name,
        result.error_message or "unknown launch error",
    )


# ---------------------------------------------------------------------------
# DetectProfilesStep
# ---------------------------------------------------------------------------

def detect_profiles(session: AutomationSession) -> AutomationError | None:
    """Discover browser user profiles for the detected browser.

    Uses :meth:`BrowserProfileManager.scan`.  Stores the
    :class:`BrowserScanResult` on ``session._profile_scan_result``.

    Always returns ``None`` — profile detection is informational and
    non-fatal.  If scanning fails, the error is stored but does not
    block the pipeline.
    """
    browser_name = session.browser_name
    try:
        scan_result = BrowserProfileManager.scan(browser_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[StepHandlers] Profile scan failed for %s: %s",
            browser_name, exc,
        )
        session._profile_scan_result = None  # type: ignore[attr-defined]
        return None  # Non-fatal

    session._profile_scan_result = scan_result  # type: ignore[attr-defined]
    return None


# ---------------------------------------------------------------------------
# DetectRunningStep
# ---------------------------------------------------------------------------

def detect_running(session: AutomationSession) -> AutomationError | None:
    """Check if instances of the target browser are already running.

    Uses :meth:`BrowserSessionManager.find`.  Stores the
    :class:`BrowserSessionResult` on ``session._session_scan_result``.

    Always returns ``None`` — running detection is informational and
    non-fatal.
    """
    browser_name = session.browser_name
    try:
        session_result = BrowserSessionManager.find(browser_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[StepHandlers] Session scan failed for %s: %s",
            browser_name, exc,
        )
        session._session_scan_result = None  # type: ignore[attr-defined]
        return None  # Non-fatal

    session._session_scan_result = session_result  # type: ignore[attr-defined]
    return None


# ---------------------------------------------------------------------------
# VerifyInstallationStep
# ---------------------------------------------------------------------------

def verify_installation(session: AutomationSession) -> AutomationError | None:
    """Verify the extension is registered in the browser's profiles.

    Uses the existing ``detect_browser_registration`` utility from
    ``extension_manager``.  Stores results on
    ``session._installation_results`` and
    ``session._installed_in_any_browser``.

    Returns ``None`` on success, or an error if the extension is not
    registered in any browser profile.
    """
    ext_dir = session.extension_dir
    if not ext_dir:
        return extension_missing("No extension directory for installation verification")

    browser_info = getattr(session, "_detected_browser_info", None)
    detected_browsers = [browser_info] if browser_info and browser_info.installed else None

    try:
        results, installed_any = extension_manager.detect_browser_registration(
            ext_dir=ext_dir,
            detected_browsers=detected_browsers,
        )
    except Exception as exc:  # noqa: BLE001
        return make_error(
            AutomationErrorCode.EXTENSION_MISSING,
            f"Exception verifying installation: {exc}",
            details={"extension_dir": ext_dir, "exception_type": type(exc).__name__},
        )

    session._installation_results = results  # type: ignore[attr-defined]
    session._installed_in_any_browser = installed_any  # type: ignore[attr-defined]

    if installed_any:
        return None

    return extension_missing(
        f"Extension not registered in any profile for {session.browser_name}"
    )
