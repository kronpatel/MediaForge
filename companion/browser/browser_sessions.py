"""
browser_sessions.py – Read-only Chromium process detection.

Provides :class:`BrowserSessionManager`, a static utility for detecting
running Chromium-based browser processes via ``psutil``.  This layer is
consumed by the extension manager, smart installation wizard, browser
launch, diagnostics, and live connection status.

Design constraints
------------------
* Read-only — no process launching, killing, or modification.
* No UI.
* Thread-safe — all methods are stateless static helpers.
* Uses ``psutil`` for cross-platform process enumeration.
* Gracefully handles ``AccessDenied``, ``ZombieProcess``, ``NoSuchProcess``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import psutil

from .browser_defs import BrowserDefinition
from .browser_registry import BrowserRegistry


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProcessInfo:
    """Metadata for a single detected browser process."""

    pid: int
    """Operating-system process identifier."""

    name: str
    """Process name as reported by the OS (e.g. ``chrome.exe``)."""

    exe_path: str
    """Absolute path to the process executable, or ``""`` if unavailable."""

    browser_name: str
    """Canonical browser name (``Chrome``, ``Brave``, ``Edge``)."""

    browser_exe: str
    """Executable filename used to match this process (e.g. ``chrome.exe``)."""

    @property
    def is_running(self) -> bool:
        """Best-effort liveness check.

        Returns ``True`` if the process is still alive, ``False`` if it has
        exited or is inaccessible.  Never raises.
        """
        try:
            proc = psutil.Process(self.pid)
            if not proc.is_running():
                return False
            # is_zombie() is Unix-only; use getattr for cross-platform safety
            zombie_fn = getattr(proc, "is_zombie", None)
            if zombie_fn is not None and zombie_fn():
                return False
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            return False


@dataclass(frozen=True)
class BrowserSessionResult:
    """Aggregated session scan result for a single browser."""

    browser_name: str
    """Canonical browser name."""

    processes: List[ProcessInfo] = field(default_factory=list)
    """List of detected running processes for this browser."""

    error: str = ""
    """Non-empty when an error occurred during scanning."""

    @property
    def running_count(self) -> int:
        """Number of running processes detected."""
        return len(self.processes)

    @property
    def is_running(self) -> bool:
        """``True`` if at least one process is detected."""
        return len(self.processes) > 0

    @property
    def pids(self) -> List[int]:
        """Return a list of all detected PIDs."""
        return [p.pid for p in self.processes]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iter_processes() -> List[psutil.Process]:
    """Return all system processes, swallowing iteration errors."""
    try:
        return list(psutil.process_iter(["pid", "name", "exe"]))
    except (psutil.Error, OSError):
        return []


def _match_process_to_browser(
    proc: psutil.Process,
    exe_names_lower: Dict[str, List[str]],
) -> Optional[str]:
    """Return the canonical browser name if *proc* matches, else ``None``.

    Parameters
    ----------
    proc:
        A ``psutil.Process`` instance (may raise on attribute access).
    exe_names_lower:
        Mapping of ``{browser_name: [exe_name_lower, …]}``.
    """
    try:
        proc_name = proc.info.get("name", "") or ""
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None

    if not proc_name:
        return None

    proc_name_lower = proc_name.lower()

    for browser_name, names in exe_names_lower.items():
        if proc_name_lower in names:
            return browser_name

    return None


def _build_exe_names_map(
    registry: BrowserRegistry | None = None,
) -> Dict[str, List[str]]:
    """Build ``{browser_name: [exe_name_lower, …]}`` from the registry."""
    if registry is None:
        registry = BrowserRegistry.instance()
    result: Dict[str, List[str]] = {}
    for bdef in registry.all():
        result[bdef.name] = [e.lower() for e in bdef.exe_names]
    return result


def _safe_process_info(
    proc: psutil.Process,
    browser_name: str,
    browser_exe: str,
) -> Optional[ProcessInfo]:
    """Build a :class:`ProcessInfo` from a ``psutil.Process``, never raising."""
    try:
        pid = proc.pid
        name = proc.info.get("name", "") or ""
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None

    try:
        exe_path = proc.exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        exe_path = ""

    return ProcessInfo(
        pid=pid,
        name=name,
        exe_path=exe_path,
        browser_name=browser_name,
        browser_exe=browser_exe,
    )


def _scan_all(
    registry: BrowserRegistry | None = None,
) -> Dict[str, List[ProcessInfo]]:
    """Single pass over all processes, grouping by browser name.

    Returns ``{browser_name: [ProcessInfo, …]}`` for browsers that have
    at least one running process.
    """
    exe_map = _build_exe_names_map(registry)
    processes = _iter_processes()

    result: Dict[str, List[ProcessInfo]] = {}

    for proc in processes:
        browser_name = _match_process_to_browser(proc, exe_map)
        if browser_name is None:
            continue

        exe_names = exe_map.get(browser_name, [])
        browser_exe = exe_names[0] if exe_names else ""

        info = _safe_process_info(proc, browser_name, browser_exe)
        if info is not None:
            result.setdefault(browser_name, []).append(info)

    return result


# ---------------------------------------------------------------------------
# BrowserSessionManager
# ---------------------------------------------------------------------------

class BrowserSessionManager:
    """Static methods for Chromium process detection.

    All methods are idempotent, thread-safe, and never raise.
    """

    # -------------------------------------------------------------------
    # Core public API
    # -------------------------------------------------------------------

    @staticmethod
    def running(browser: str | BrowserDefinition) -> List[ProcessInfo]:
        """Return all running processes for *browser*.

        Parameters
        ----------
        browser:
            Either a browser name (``"Chrome"``) or a
            :class:`BrowserDefinition` instance.

        Returns
        -------
        list[ProcessInfo]
            Empty list if no processes are found or on error.
        """
        result = BrowserSessionManager.find(browser)
        return result.processes

    @staticmethod
    def running_all() -> Dict[str, List[ProcessInfo]]:
        """Return a mapping of ``{browser_name: [ProcessInfo, …]}`` for all
        detected browsers.

        Returns
        -------
        dict[str, list[ProcessInfo]]
            Only browsers with at least one running process are included.
        """
        return _scan_all()

    @staticmethod
    def find(browser: str | BrowserDefinition) -> BrowserSessionResult:
        """Perform a session scan for a single browser.

        This is the most comprehensive per-browser method; other helpers
        delegate to it.

        Parameters
        ----------
        browser:
            Browser name or definition.

        Returns
        -------
        BrowserSessionResult
        """
        defn = BrowserSessionManager._resolve_definition(browser)
        if defn is None:
            return BrowserSessionResult(
                browser_name=str(browser) if not isinstance(browser, BrowserDefinition) else browser.name,
                error=f"Unknown browser: {browser}",
            )

        all_sessions = _scan_all()
        processes = all_sessions.get(defn.name, [])

        return BrowserSessionResult(
            browser_name=defn.name,
            processes=processes,
        )

    @staticmethod
    def count(browser: str | BrowserDefinition) -> int:
        """Return the number of running processes for *browser*.

        Returns ``0`` on error.
        """
        result = BrowserSessionManager.find(browser)
        return result.running_count

    @staticmethod
    def has_running(browser: str | BrowserDefinition) -> bool:
        """Return ``True`` if at least one process for *browser* is running."""
        result = BrowserSessionManager.find(browser)
        return result.is_running

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _resolve_definition(browser: str | BrowserDefinition) -> Optional[BrowserDefinition]:
        """Resolve a browser name or definition to a :class:`BrowserDefinition`."""
        if isinstance(browser, BrowserDefinition):
            return browser
        name = browser.strip() if isinstance(browser, str) else ""
        if not name:
            return None
        registry = BrowserRegistry.instance()
        return registry.get(name)
