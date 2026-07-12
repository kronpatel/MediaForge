"""
browser_launcher.py – Browser detection and launch orchestration.

BrowserLauncher is the primary public interface for detecting installed
Chromium-based browsers and launching them.  All methods are static —
no instance state is held.

This module replaces the detection logic previously inlined in
``extension_manager.py`` while keeping backward-compatible function
names available at the package level.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

from .browser_defs import BrowserDefinition
from .browser_info import BrowserInfo, LaunchErrorCode, LaunchResult
from .browser_registry import BrowserRegistry
from ._version_reader import read_browser_version
from ._path_utils import is_executable


class BrowserLauncher:
    """Static methods for browser detection and launch.

    All methods are idempotent and never raise.
    """

    # -----------------------------------------------------------------------
    # Detection
    # -----------------------------------------------------------------------

    @staticmethod
    def detect(defn: BrowserDefinition) -> BrowserInfo:
        """Detect a single browser from its definition.

        Returns a ``BrowserInfo`` with ``installed=True`` if the executable
        is found; ``installed=False`` otherwise.
        """
        if sys.platform != "win32":
            return BrowserInfo(
                name=defn.name,
                installed=False,
                user_data_dir=defn.user_data_dir,
                exe_names=defn.exe_names,
                registry_key=defn.registry_key,
                extensions_url=defn.extensions_url,
            )

        for candidate in defn.search_paths:
            try:
                if candidate and is_executable(candidate):
                    version = read_browser_version(candidate)
                    return BrowserInfo(
                        name=defn.name,
                        installed=True,
                        path=candidate,
                        version=version,
                        user_data_dir=defn.user_data_dir,
                        exe_names=defn.exe_names,
                        registry_key=defn.registry_key,
                        extensions_url=defn.extensions_url,
                    )
            except Exception:
                continue

        return BrowserInfo(
            name=defn.name,
            installed=False,
            user_data_dir=defn.user_data_dir,
            exe_names=defn.exe_names,
            registry_key=defn.registry_key,
            extensions_url=defn.extensions_url,
        )

    @staticmethod
    def detect_all(registry: BrowserRegistry | None = None) -> List[BrowserInfo]:
        """Detect all registered browsers.

        If *registry* is ``None`` the global singleton is used.
        """
        if registry is None:
            registry = BrowserRegistry.instance()
        results: List[BrowserInfo] = []
        for bdef in registry.all():
            try:
                results.append(BrowserLauncher.detect(bdef))
            except Exception:
                results.append(BrowserInfo(name=bdef.name, installed=False))
        return results

    @staticmethod
    def detect_first(registry: BrowserRegistry | None = None) -> Optional[BrowserInfo]:
        """Return the first detected installed browser, or ``None``."""
        for info in BrowserLauncher.detect_all(registry):
            if info.installed:
                return info
        return None

    @staticmethod
    def detect_by_name(name: str, registry: BrowserRegistry | None = None) -> Optional[BrowserInfo]:
        """Detect a single browser by name (case-insensitive)."""
        if registry is None:
            registry = BrowserRegistry.instance()
        bdef = registry.get(name)
        if bdef is None:
            return None
        return BrowserLauncher.detect(bdef)

    # -----------------------------------------------------------------------
    # Launch
    # -----------------------------------------------------------------------

    @staticmethod
    def launch(
        exe_path: str,
        args: list[str] | None = None,
        url: str = "",
    ) -> LaunchResult:
        """Launch a browser executable.

        Parameters
        ----------
        exe_path:
            Absolute path to the browser executable.
        args:
            Extra command-line arguments.
        url:
            Optional URL to open in the browser.

        Returns
        -------
        LaunchResult
            With ``success=True`` and a PID on success, or an error code
            on failure.
        """
        if not exe_path or not exe_path.strip():
            return LaunchResult(
                success=False,
                error_code=LaunchErrorCode.NOT_FOUND,
                error_message="No executable path provided",
            )

        if not is_executable(exe_path):
            return LaunchResult(
                success=False,
                error_code=LaunchErrorCode.NOT_FOUND,
                error_message=f"Executable not found: {exe_path}",
                exe_path=exe_path,
            )

        cmd: list[str] = [exe_path]
        if args:
            cmd.extend(args)
        if url:
            cmd.append(url)

        try:
            proc = subprocess.Popen(cmd)  # noqa: S603
            return LaunchResult(
                success=True,
                pid=proc.pid,
                browser_name=os.path.basename(exe_path),
                exe_path=exe_path,
            )
        except PermissionError:
            return LaunchResult(
                success=False,
                error_code=LaunchErrorCode.PERMISSION_DENIED,
                error_message=f"Permission denied: {exe_path}",
                exe_path=exe_path,
            )
        except OSError as exc:
            return LaunchResult(
                success=False,
                error_code=LaunchErrorCode.UNKNOWN,
                error_message=str(exc),
                exe_path=exe_path,
            )

    @staticmethod
    def launch_browser(
        browser_name: str,
        url: str = "",
        registry: BrowserRegistry | None = None,
    ) -> LaunchResult:
        """Convenience: detect + launch by browser name."""
        info = BrowserLauncher.detect_by_name(browser_name, registry)
        if info is None or not info.installed:
            return LaunchResult(
                success=False,
                error_code=LaunchErrorCode.NOT_FOUND,
                error_message=f"{browser_name} not installed",
                browser_name=browser_name,
            )
        return BrowserLauncher.launch(info.path, url=url)


# ---------------------------------------------------------------------------
# Backward-compatible free functions
# ---------------------------------------------------------------------------

def detect_chrome() -> BrowserInfo:
    """Detect Chrome using the global registry (backward-compat)."""
    return BrowserLauncher.detect_by_name("Chrome") or BrowserInfo(name="Chrome")


def detect_all_browsers(registry: BrowserRegistry | None = None) -> List[BrowserInfo]:
    """Detect all browsers (backward-compat)."""
    return BrowserLauncher.detect_all(registry)


def detect_first_browser(registry: BrowserRegistry | None = None) -> Optional[BrowserInfo]:
    """Detect the first installed browser (backward-compat)."""
    return BrowserLauncher.detect_first(registry)
