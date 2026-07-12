"""
browser_info.py – Runtime detection result types.

Data classes returned by BrowserLauncher and BrowserRegistry methods.
These are *mutable* results populated during detection — not static
definitions (which live in ``browser_defs``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Launch result types
# ---------------------------------------------------------------------------

class LaunchErrorCode(Enum):
    """Possible error categories when a browser launch fails."""

    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    ALREADY_RUNNING = "already_running"
    TIMED_OUT = "timed_out"
    USER_CANCELLED = "user_cancelled"
    ENTERPRISE_BLOCKED = "enterprise_blocked"
    UNKNOWN = "unknown"


@dataclass
class LaunchResult:
    """Outcome of an attempt to launch a browser executable."""

    success: bool = False
    pid: Optional[int] = None
    error_code: Optional[LaunchErrorCode] = None
    error_message: str = ""
    browser_name: str = ""
    exe_path: str = ""

    @property
    def failed(self) -> bool:
        return not self.success


# ---------------------------------------------------------------------------
# Enterprise policy
# ---------------------------------------------------------------------------

@dataclass
class EnterprisePolicyResult:
    """Result of checking Windows Registry enterprise policies."""

    has_policy: bool = False
    install_allowed: bool = True
    extension_install_allowed: bool = True
    policy_keys: List[str] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# Browser installation info
# ---------------------------------------------------------------------------

@dataclass
class BrowserInfo:
    """Runtime information about a detected browser installation."""

    name: str = ""
    installed: bool = False
    path: str = ""
    version: str = ""
    user_data_dir: str = ""
    exe_names: List[str] = field(default_factory=list)
    registry_key: str = ""
    extensions_url: str = "chrome://extensions"
    is_default: bool = False
    channel: str = ""

    @property
    def detected(self) -> bool:
        return self.installed

    @property
    def display_label(self) -> str:
        """Human-readable label like ``Chrome 126.0.6478.127``."""
        if not self.installed:
            return f"{self.name} (not found)"
        parts = [self.name]
        if self.channel:
            parts.append(self.channel)
        if self.version:
            parts.append(self.version)
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Profile scanning results
# ---------------------------------------------------------------------------

@dataclass
class BrowserProfileResult:
    """Result of checking a single browser profile for the extension."""

    profile_name: str = ""
    preferences_exists: bool = False
    extension_registered: bool = False
    error: str = ""


@dataclass
class BrowserRegistrationResult:
    """Combined result for a single browser across all its profiles."""

    browser_name: str = ""
    profiles_scanned: int = 0
    extension_registered: bool = False
    profile_results: List[BrowserProfileResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Aggregated detection snapshot
# ---------------------------------------------------------------------------

@dataclass
class ExtensionStatus:
    """Aggregated detection result for the entire extension ecosystem."""

    # Chrome legacy compat (kept for backward compatibility)
    chrome_installed: bool = False
    chrome_path: str = ""
    chrome_version: str = ""

    # Extension file state
    all_files_present: bool = False
    missing_files: List[str] = field(default_factory=list)
    manifest_data: Dict[str, Any] = field(default_factory=dict)

    # Versions
    extension_version: str = ""
    companion_version: str = ""

    # Compatibility label
    compatibility: str = ""

    # Folder
    folder_exists: bool = False

    # Multi-browser detection
    all_browsers: List[BrowserInfo] = field(default_factory=list)
    browser_registration: List[BrowserRegistrationResult] = field(default_factory=list)
    installed_in_browser: bool = False

    # Compatibility constants
    COMPATIBLE = "Compatible"
    MISMATCH = "Version Mismatch"
    MISSING = "Extension Missing"
    NOT_INSTALLED = "Not Installed"
    UNKNOWN = "Unknown"
