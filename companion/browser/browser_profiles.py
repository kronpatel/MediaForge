"""
browser_profiles.py – Read-only Chromium profile discovery.

Provides :class:`BrowserProfileManager`, a static utility for locating
and inspecting Chromium-based browser profile directories.  This layer
is consumed by the extension manager, installation wizard, diagnostics,
and repair tools.

Design constraints
------------------
* Read-only — no profile creation, modification, or deletion.
* No browser launching.
* No extension detection.
* Thread-safe — all methods are stateless static helpers.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .browser_defs import BrowserDefinition
from .browser_registry import BrowserRegistry


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProfileMetadata:
    """Metadata for a single discovered Chromium profile directory."""

    name: str
    """Profile directory name (``Default``, ``Profile 1``, …)."""

    path: str
    """Absolute path to the profile directory."""

    preferences_path: str
    """Absolute path to the ``Preferences`` file inside the profile."""

    preferences_exists: bool
    """``True`` if the Preferences file exists on disk."""

    is_default: bool
    """``True`` if this is the ``Default`` profile."""

    error: str = ""
    """Non-empty when an error occurred during scanning (e.g. permission denied)."""

    @property
    def is_valid(self) -> bool:
        """``True`` when the profile directory exists and has a Preferences file."""
        return self.preferences_exists and not self.error


@dataclass(frozen=True)
class BrowserScanResult:
    """Aggregated profile scan result for a single browser."""

    browser_name: str
    """Canonical browser name (``Chrome``, ``Brave``, ``Edge``)."""

    user_data_dir: str
    """Absolute path to the browser's User Data directory."""

    user_data_dir_exists: bool
    """``True`` when the User Data directory exists on disk."""

    profiles: List[ProfileMetadata] = field(default_factory=list)
    """Ordered list of discovered profiles."""

    error: str = ""
    """Non-empty when a top-level error occurred (e.g. directory unreadable)."""

    @property
    def profile_count(self) -> int:
        """Number of profiles discovered."""
        return len(self.profiles)

    @property
    def default_profile(self) -> Optional[ProfileMetadata]:
        """Return the ``Default`` profile, or ``None``."""
        for p in self.profiles:
            if p.is_default:
                return p
        return None

    @property
    def valid_profiles(self) -> List[ProfileMetadata]:
        """Return only profiles where ``is_valid`` is ``True``."""
        return [p for p in self.profiles if p.is_valid]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Matches Chromium profile directory names:
#   Default, Profile 1, Profile 2, …, Guest Profile, …
_PROFILE_DIR_RE = re.compile(
    r"^(Default|Profile \d+|Guest Profile|System Profile)$",
    re.IGNORECASE,
)


def _is_profile_dir(name: str) -> bool:
    """Return *True* if *name* looks like a Chromium profile directory."""
    return _PROFILE_DIR_RE.match(name) is not None


def _safe_isdir(path: str) -> bool:
    """os.path.isdir wrapper that swallows OSError."""
    try:
        return os.path.isdir(path)
    except (OSError, ValueError):
        return False


def _safe_isfile(path: str) -> bool:
    """os.path.isfile wrapper that swallows OSError."""
    try:
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


def _safe_scandir(path: str) -> List[str]:
    """Return directory entry names under *path*, or ``[]`` on error."""
    try:
        return [
            entry.name
            for entry in os.scandir(path)
            if entry.is_dir(follow_symlinks=False)
        ]
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------------------
# BrowserProfileManager
# ---------------------------------------------------------------------------

class BrowserProfileManager:
    """Static methods for Chromium profile discovery.

    All methods are idempotent, thread-safe, and never raise.
    """

    # -------------------------------------------------------------------
    # Core public API
    # -------------------------------------------------------------------

    @staticmethod
    def get_profiles(browser: str | BrowserDefinition) -> List[ProfileMetadata]:
        """Return all discovered profiles for *browser*.

        Parameters
        ----------
        browser:
            Either a browser name (``"Chrome"``) or a
            :class:`BrowserDefinition` instance.

        Returns
        -------
        list[ProfileMetadata]
            Ordered list; ``Default`` always appears first when present.
        """
        result = BrowserProfileManager.scan(browser)
        return result.profiles

    @staticmethod
    def get_default_profile(browser: str | BrowserDefinition) -> Optional[ProfileMetadata]:
        """Return the ``Default`` profile, or ``None``."""
        result = BrowserProfileManager.scan(browser)
        return result.default_profile

    @staticmethod
    def find_preferences(browser: str | BrowserDefinition) -> Dict[str, str]:
        """Return a mapping of ``{profile_name: preferences_path}`` for every
        profile whose Preferences file exists on disk.

        Parameters
        ----------
        browser:
            Browser name or definition.

        Returns
        -------
        dict[str, str]
            ``{profile_name: absolute_preferences_path}``
        """
        profiles = BrowserProfileManager.get_profiles(browser)
        return {
            p.name: p.preferences_path
            for p in profiles
            if p.preferences_exists
        }

    @staticmethod
    def scan(browser: str | BrowserDefinition) -> BrowserScanResult:
        """Perform a full profile scan for *browser*.

        This is the most comprehensive method; the other helpers delegate
        to it.

        Parameters
        ----------
        browser:
            Browser name or definition.

        Returns
        -------
        BrowserScanResult
        """
        defn = BrowserProfileManager._resolve_definition(browser)
        if defn is None:
            return BrowserScanResult(
                browser_name=str(browser) if not isinstance(browser, BrowserDefinition) else browser.name,
                user_data_dir="",
                user_data_dir_exists=False,
                error=f"Unknown browser: {browser}",
            )

        udd = defn.user_data_dir
        if not udd:
            return BrowserScanResult(
                browser_name=defn.name,
                user_data_dir="",
                user_data_dir_exists=False,
                error="No user data directory configured",
            )

        if not _safe_isdir(udd):
            return BrowserScanResult(
                browser_name=defn.name,
                user_data_dir=udd,
                user_data_dir_exists=False,
                error="User data directory not found",
            )

        profiles = BrowserProfileManager._discover_profiles(udd)

        return BrowserScanResult(
            browser_name=defn.name,
            user_data_dir=udd,
            user_data_dir_exists=True,
            profiles=profiles,
        )

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

    @staticmethod
    def _discover_profiles(user_data_dir: str) -> List[ProfileMetadata]:
        """Scan *user_data_dir* for Chromium profile directories."""
        entries = _safe_scandir(user_data_dir)
        if not entries:
            return []

        raw: List[ProfileMetadata] = []
        for name in entries:
            if not _is_profile_dir(name):
                continue
            meta = BrowserProfileManager._build_profile_metadata(user_data_dir, name)
            raw.append(meta)

        # Ensure Default always comes first
        raw.sort(key=lambda p: (not p.is_default, p.name))
        return raw

    @staticmethod
    def _build_profile_metadata(user_data_dir: str, profile_name: str) -> ProfileMetadata:
        """Build a single :class:`ProfileMetadata` for *profile_name*."""
        profile_path = os.path.join(user_data_dir, profile_name)
        prefs_path = os.path.join(profile_path, "Preferences")

        dir_ok = _safe_isdir(profile_path)
        prefs_ok = _safe_isfile(prefs_path) if dir_ok else False

        error = ""
        if not dir_ok:
            error = "Profile directory not accessible"

        return ProfileMetadata(
            name=profile_name,
            path=profile_path,
            preferences_path=prefs_path,
            preferences_exists=prefs_ok,
            is_default=(profile_name.lower() == "default"),
            error=error,
        )
