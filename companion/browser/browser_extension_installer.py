"""
browser_extension_installer.py – Extension installation engine.

Provides :class:`ExtensionInstallationEngine`, a static utility for
validating and launching Chromium-based browsers with the MediaForge
extension loaded via ``--load-extension``.

Design constraints
------------------
* Stateless — no instance or class state.
* Thread-safe — all methods are static helpers.
* Never raises — errors are communicated via frozen result dataclasses.
* Uses ``subprocess.Popen()`` with list arguments (never ``shell=True``).
* Reuses :class:`BrowserRegistry` definitions for executable resolution.
* Extension validation is read-only (no file writes or policy changes).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .browser_launcher import BrowserLauncher
from .browser_registry import BrowserRegistry


# ---------------------------------------------------------------------------
# Extension path defaults
# ---------------------------------------------------------------------------

_COMPANION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_COMPANION_DIR)
_DEFAULT_EXTENSION_DIR = os.path.join(_PROJECT_ROOT, "extension")

_REQUIRED_EXTENSION_FILES: tuple[str, ...] = (
    "manifest.json",
    "icon.png",
    "background.js",
    "content.js",
    "settings.html",
)


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

class ExtensionErrorCode(Enum):
    """Typed error categories for extension installation operations."""

    SUCCESS = "success"
    BROWSER_NOT_FOUND = "browser_not_found"
    EXTENSION_MISSING = "extension_missing"
    MANIFEST_MISSING = "manifest_missing"
    MANIFEST_INVALID = "manifest_invalid"
    REQUIRED_FILES_MISSING = "required_files_missing"
    PERMISSION_DENIED = "permission_denied"
    LAUNCH_FAILED = "launch_failed"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExtensionValidationResult:
    """Outcome of validating an extension directory."""

    valid: bool = False
    """``True`` if all checks pass."""

    extension_dir: str = ""
    """Absolute path to the extension directory validated."""

    manifest_exists: bool = False
    """``True`` if ``manifest.json`` was found."""

    manifest_data: Dict[str, Any] = field(default_factory=dict)
    """Parsed contents of ``manifest.json``, empty dict on failure."""

    missing_files: List[str] = field(default_factory=list)
    """Required files that were not found on disk."""

    error_code: ExtensionErrorCode = ExtensionErrorCode.UNKNOWN
    """High-level error category."""

    error_message: str = ""
    """Human-readable diagnostic message."""


@dataclass(frozen=True)
class ExtensionLaunchResult:
    """Outcome of an attempt to launch a browser with the extension loaded."""

    success: bool = False
    """``True`` if the browser process was started."""

    browser_name: str = ""
    """Canonical browser name (e.g. ``Chrome``)."""

    pid: Optional[int] = None
    """Process ID of the launched browser, if available."""

    exe_path: str = ""
    """Absolute path to the browser executable used."""

    extension_dir: str = ""
    """Absolute path to the extension directory."""

    error_code: ExtensionErrorCode = ExtensionErrorCode.UNKNOWN
    """High-level error category."""

    error_message: str = ""
    """Human-readable diagnostic message."""

    command: List[str] = field(default_factory=list)
    """The command-line arguments used to launch (for debugging)."""

    validation: Optional[ExtensionValidationResult] = None
    """Extension validation result, populated when validation was performed."""

    @property
    def failed(self) -> bool:
        return not self.success


# ---------------------------------------------------------------------------
# ExtensionInstallationEngine
# ---------------------------------------------------------------------------

class ExtensionInstallationEngine:
    """Static methods for validating and launching browsers with extensions.

    All methods are idempotent, thread-safe, and never raise.
    """

    # -------------------------------------------------------------------
    # Extension validation
    # -------------------------------------------------------------------

    @staticmethod
    def validate_extension(extension_dir: str = "") -> ExtensionValidationResult:
        """Validate an extension directory.

        Checks:
        1. Directory exists.
        2. ``manifest.json`` exists.
        3. ``manifest.json`` is valid JSON.
        4. All required files are present.

        Parameters
        ----------
        extension_dir:
            Path to the extension directory.  When empty, the default
            ``extension/`` folder at the project root is used.

        Returns
        -------
        ExtensionValidationResult
        """
        ext_dir = extension_dir.strip() if extension_dir else ""
        if not ext_dir:
            ext_dir = _DEFAULT_EXTENSION_DIR

        ext_dir = os.path.abspath(ext_dir)

        # 1. Directory exists
        if not os.path.isdir(ext_dir):
            return ExtensionValidationResult(
                valid=False,
                extension_dir=ext_dir,
                error_code=ExtensionErrorCode.EXTENSION_MISSING,
                error_message=f"Extension directory not found: {ext_dir}",
            )

        manifest_path = os.path.join(ext_dir, "manifest.json")

        # 2. manifest.json exists
        if not os.path.isfile(manifest_path):
            return ExtensionValidationResult(
                valid=False,
                extension_dir=ext_dir,
                manifest_exists=False,
                error_code=ExtensionErrorCode.MANIFEST_MISSING,
                error_message=f"manifest.json not found: {manifest_path}",
            )

        # 3. manifest.json is valid JSON
        manifest_data: Dict[str, Any] = {}
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest_data = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            return ExtensionValidationResult(
                valid=False,
                extension_dir=ext_dir,
                manifest_exists=True,
                error_code=ExtensionErrorCode.MANIFEST_INVALID,
                error_message=f"Failed to parse manifest.json: {exc}",
            )

        # 4. Check required files
        missing: List[str] = []
        for filename in _REQUIRED_EXTENSION_FILES:
            filepath = os.path.join(ext_dir, filename)
            if not os.path.isfile(filepath):
                missing.append(filename)

        if missing:
            return ExtensionValidationResult(
                valid=False,
                extension_dir=ext_dir,
                manifest_exists=True,
                manifest_data=manifest_data,
                missing_files=missing,
                error_code=ExtensionErrorCode.REQUIRED_FILES_MISSING,
                error_message=f"Missing required files: {', '.join(missing)}",
            )

        return ExtensionValidationResult(
            valid=True,
            extension_dir=ext_dir,
            manifest_exists=True,
            manifest_data=manifest_data,
            error_code=ExtensionErrorCode.SUCCESS,
            error_message="",
        )

    # -------------------------------------------------------------------
    # Launch capability check
    # -------------------------------------------------------------------

    @staticmethod
    def can_launch(browser_name: str) -> ExtensionLaunchResult:
        """Check whether a browser can be launched with the extension.

        Resolves the browser name to an executable via the registry and
        verifies it exists on disk.

        Parameters
        ----------
        browser_name:
            Canonical browser name (e.g. ``Chrome``, ``Brave``, ``Edge``).

        Returns
        -------
        ExtensionLaunchResult
        """
        name = browser_name.strip() if browser_name else ""
        if not name:
            return ExtensionLaunchResult(
                success=False,
                error_code=ExtensionErrorCode.BROWSER_NOT_FOUND,
                error_message="No browser name provided",
            )

        registry = BrowserRegistry.instance()
        bdef = registry.get(name)
        if bdef is None:
            return ExtensionLaunchResult(
                success=False,
                browser_name=name,
                error_code=ExtensionErrorCode.BROWSER_NOT_FOUND,
                error_message=f"Unknown browser: {name}",
            )

        info = BrowserLauncher.detect(bdef)
        if not info.installed or not info.path:
            return ExtensionLaunchResult(
                success=False,
                browser_name=name,
                error_code=ExtensionErrorCode.BROWSER_NOT_FOUND,
                error_message=f"{name} is not installed",
            )

        return ExtensionLaunchResult(
            success=True,
            browser_name=name,
            exe_path=info.path,
            error_code=ExtensionErrorCode.SUCCESS,
        )

    # -------------------------------------------------------------------
    # Command building
    # -------------------------------------------------------------------

    @staticmethod
    def build_launch_command(
        browser_name: str,
        extension_dir: str = "",
        url: str = "",
        extra_args: Optional[List[str]] = None,
    ) -> ExtensionLaunchResult:
        """Build the command-line arguments for launching a browser with
        the extension loaded.

        This does **not** launch the process — it only validates inputs
        and returns the constructed command.

        Parameters
        ----------
        browser_name:
            Canonical browser name.
        extension_dir:
            Path to the extension directory.  Defaults to ``extension/``.
        url:
            Optional URL to open after launch.
        extra_args:
            Additional command-line arguments to pass to the browser.

        Returns
        -------
        ExtensionLaunchResult
            With ``command`` populated on success.
        """
        name = browser_name.strip() if browser_name else ""

        # Resolve browser executable
        exe_result = ExtensionInstallationEngine.can_launch(name)
        if exe_result.failed:
            return exe_result

        # Validate extension
        val_result = ExtensionInstallationEngine.validate_extension(extension_dir)
        if not val_result.valid:
            return ExtensionLaunchResult(
                success=False,
                browser_name=name,
                exe_path=exe_result.exe_path,
                extension_dir=val_result.extension_dir,
                error_code=val_result.error_code,
                error_message=val_result.error_message,
                validation=val_result,
            )

        # Build command
        cmd: List[str] = [exe_result.exe_path]
        cmd.append(f"--load-extension={val_result.extension_dir}")

        if extra_args:
            cmd.extend(extra_args)

        if url:
            cmd.append(url)

        return ExtensionLaunchResult(
            success=True,
            browser_name=name,
            exe_path=exe_result.exe_path,
            extension_dir=val_result.extension_dir,
            error_code=ExtensionErrorCode.SUCCESS,
            command=cmd,
            validation=val_result,
        )

    # -------------------------------------------------------------------
    # Launch
    # -------------------------------------------------------------------

    @staticmethod
    def launch(
        browser_name: str,
        extension_dir: str = "",
        url: str = "",
        extra_args: Optional[List[str]] = None,
    ) -> ExtensionLaunchResult:
        """Validate and launch a browser with the extension loaded.

        This is the primary public method: it validates the extension,
        resolves the browser executable, and starts the process via
        ``subprocess.Popen``.

        Parameters
        ----------
        browser_name:
            Canonical browser name (e.g. ``Chrome``).
        extension_dir:
            Path to the extension directory.  Defaults to ``extension/``.
        url:
            Optional URL to open in the browser.
        extra_args:
            Additional command-line arguments.

        Returns
        -------
        ExtensionLaunchResult
        """
        cmd_result = ExtensionInstallationEngine.build_launch_command(
            browser_name=browser_name,
            extension_dir=extension_dir,
            url=url,
            extra_args=extra_args,
        )

        if cmd_result.failed:
            return cmd_result

        # Launch via subprocess
        try:
            proc = subprocess.Popen(cmd_result.command)  # noqa: S603
            return ExtensionLaunchResult(
                success=True,
                browser_name=cmd_result.browser_name,
                pid=proc.pid,
                exe_path=cmd_result.exe_path,
                extension_dir=cmd_result.extension_dir,
                error_code=ExtensionErrorCode.SUCCESS,
                command=cmd_result.command,
                validation=cmd_result.validation,
            )
        except PermissionError:
            return ExtensionLaunchResult(
                success=False,
                browser_name=cmd_result.browser_name,
                exe_path=cmd_result.exe_path,
                extension_dir=cmd_result.extension_dir,
                error_code=ExtensionErrorCode.PERMISSION_DENIED,
                error_message=f"Permission denied: {cmd_result.exe_path}",
                command=cmd_result.command,
                validation=cmd_result.validation,
            )
        except OSError as exc:
            return ExtensionLaunchResult(
                success=False,
                browser_name=cmd_result.browser_name,
                exe_path=cmd_result.exe_path,
                extension_dir=cmd_result.extension_dir,
                error_code=ExtensionErrorCode.LAUNCH_FAILED,
                error_message=str(exc),
                command=cmd_result.command,
                validation=cmd_result.validation,
            )
        except Exception as exc:  # noqa: BLE001
            return ExtensionLaunchResult(
                success=False,
                browser_name=cmd_result.browser_name,
                exe_path=cmd_result.exe_path,
                extension_dir=cmd_result.extension_dir,
                error_code=ExtensionErrorCode.UNKNOWN,
                error_message=str(exc),
                command=cmd_result.command,
                validation=cmd_result.validation,
            )

    # -------------------------------------------------------------------
    # Batch launch
    # -------------------------------------------------------------------

    @staticmethod
    def launch_all(
        extension_dir: str = "",
        url: str = "",
        extra_args: Optional[List[str]] = None,
    ) -> List[ExtensionLaunchResult]:
        """Launch the extension in all installed browsers.

        Returns a list of results, one per registered browser, in
        registration order (Chrome → Brave → Edge).

        Parameters
        ----------
        extension_dir:
            Path to the extension directory.
        url:
            Optional URL to open.
        extra_args:
            Additional command-line arguments.

        Returns
        -------
        list[ExtensionLaunchResult]
        """
        registry = BrowserRegistry.instance()
        results: List[ExtensionLaunchResult] = []

        for bdef in registry.all():
            result = ExtensionInstallationEngine.launch(
                browser_name=bdef.name,
                extension_dir=extension_dir,
                url=url,
                extra_args=extra_args,
            )
            results.append(result)

        return results


