"""
browser_defs.py – Browser definitions and capability descriptors.

Defines the static characteristics of each supported Chromium-based
browser, including executable search paths, user data locations,
and feature flags.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

class BrowserFeature:
    """Named capability flags for Chromium browsers."""

    SUPPORTS_PROFILES = "supports_profiles"
    SUPPORTS_EXTENSIONS = "supports_extensions"
    SUPPORTS_DEV_MODE = "supports_dev_mode"
    SUPPORTS_SIDELOADING = "supports_sideloading"
    SUPPORTS_PERSISTENT_PROFILES = "supports_persistent_profiles"


# ---------------------------------------------------------------------------
# Capabilities container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BrowserCapabilities:
    """Feature flags describing what a browser supports."""

    supports_profiles: bool = True
    supports_extensions: bool = True
    supports_dev_mode: bool = True
    supports_sideloading: bool = True
    supports_persistent_profiles: bool = True

    def has(self, feature: str) -> bool:
        """Return True if this browser has the given *feature* flag."""
        return getattr(self, feature, False)


# ---------------------------------------------------------------------------
# BrowserDefinition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BrowserDefinition:
    """Static, platform-specific definition of a Chromium-based browser.

    Each browser (Chrome, Brave, Edge, ...) is described by one instance
    containing executable search paths, user data directory, and
    supporting metadata.
    """

    name: str
    search_paths: List[str] = field(default_factory=list)
    user_data_dir: str = ""
    exe_names: List[str] = field(default_factory=list)
    registry_key: str = ""
    extensions_url: str = "chrome://extensions"
    version_args: tuple[str, ...] = ("--version",)
    capabilities: BrowserCapabilities = field(default_factory=BrowserCapabilities)

    def __post_init__(self) -> None:
        # Frozen dataclass — set via object.__setattr__
        if not self.exe_names:
            object.__setattr__(self, "exe_names", [f"{self.name.lower()}.exe"])


# ---------------------------------------------------------------------------
# Platform-aware path builders
# ---------------------------------------------------------------------------

def _env(key: str, fallback: str) -> str:
    return os.environ.get(key, fallback)


def chrome_definition() -> BrowserDefinition:
    """Return the Chrome browser definition for the current platform."""
    return BrowserDefinition(
        name="Chrome",
        search_paths=[
            os.path.join(_env("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(_env("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(_env("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ],
        user_data_dir=os.path.join(_env("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"),
        exe_names=["chrome.exe"],
        registry_key=r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        extensions_url="chrome://extensions",
        capabilities=BrowserCapabilities(),
    )


def brave_definition() -> BrowserDefinition:
    """Return the Brave browser definition for the current platform."""
    return BrowserDefinition(
        name="Brave",
        search_paths=[
            os.path.join(_env("LOCALAPPDATA", ""), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            os.path.join(_env("PROGRAMFILES", r"C:\Program Files"), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            os.path.join(_env("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ],
        user_data_dir=os.path.join(_env("LOCALAPPDATA", ""), "BraveSoftware", "Brave-Browser", "User Data"),
        exe_names=["brave.exe"],
        registry_key=r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\brave.exe",
        extensions_url="brave://extensions",
        capabilities=BrowserCapabilities(),
    )


def edge_definition() -> BrowserDefinition:
    """Return the Edge browser definition for the current platform."""
    return BrowserDefinition(
        name="Edge",
        search_paths=[
            os.path.join(_env("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(_env("PROGRAMFILES", r"C:\Program Files"), "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(_env("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Microsoft", "Edge", "Application", "msedge.exe"),
        ],
        user_data_dir=os.path.join(_env("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data"),
        exe_names=["msedge.exe"],
        registry_key=r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
        extensions_url="edge://extensions",
        capabilities=BrowserCapabilities(),
    )


# ---------------------------------------------------------------------------
# Canonical definitions list (ordered by priority)
# ---------------------------------------------------------------------------

def all_browser_definitions() -> list[BrowserDefinition]:
    """Return all built-in browser definitions in detection priority order."""
    return [chrome_definition(), brave_definition(), edge_definition()]
