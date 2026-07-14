"""
extension_manager.py – ExtensionManagerPage

Management interface for the MediaForge browser extension with real
detection capabilities for Chrome, extension files, version compatibility,
installation status, and an interactive installation wizard.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Any, Callable

import customtkinter as ctk

from base_page import BasePage
from notifications import (
    CATEGORY_INFO,
    SOURCE_UI,
    get_notification_manager,
)

# Browser package integration — replaces legacy detection code
from browser import (
    BrowserAutomationEngine,
    BrowserInfo,
    BrowserLauncher,
    BrowserProfileManager,
    BrowserProfileResult,
    BrowserRegistrationResult,
    BrowserSessionManager,
    ExtensionInstallationEngine,
    get_automation_engine,
)
from browser.automation import (
    create_install_pipeline,
    create_launch_pipeline,
    create_open_ext_page_pipeline,
    create_verify_pipeline,
)
from browser.automation_errors import AutomationError
from browser.automation_result import AutomationResult

if TYPE_CHECKING:
    from backend_manager import BackendManager
    from logger import AppLogger


# ---------------------------------------------------------------------------
# Path helpers & constants
# ---------------------------------------------------------------------------

_COMPANION_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_COMPANION_DIR)
_EXTENSION_DIR = os.path.join(_PROJECT_ROOT, "extension")
_MANIFEST_PATH = os.path.join(_EXTENSION_DIR, "manifest.json")

from browser.browser_extension_installer import _REQUIRED_EXTENSION_FILES

# Status colors
_CLR_GREEN = "#22c55e"
_CLR_ORANGE = "#f59e0b"
_CLR_RED = "#ef4444"
_CLR_GREY = "#8b92a8"


# ---------------------------------------------------------------------------
# Extension detection helpers (delegates to browser package)
# ---------------------------------------------------------------------------

def _check_extension_in_preferences(prefs_path: str) -> tuple[bool, str]:
    """Check if the MediaForge unpacked extension is registered in a Preferences file.

    Scans ``extensions.settings`` for any entry whose ``path`` field matches
    the project's extension directory.  Returns ``(found, error_message)``.
    """
    try:
        with open(prefs_path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return False, f"Failed to parse: {exc}"

    if not isinstance(data, dict):
        return False, "Preferences not a dict"

    ext_settings = data.get("extensions", {})
    if not isinstance(ext_settings, dict):
        return False, "No extensions.settings"

    settings = ext_settings.get("settings", {})
    if not isinstance(settings, dict):
        return False, "No settings key"

    for _ext_id, entry in settings.items():
        if not isinstance(entry, dict):
            continue
        registered_path = entry.get("path", "")
        if not registered_path:
            continue
        if os.path.normpath(registered_path).lower() == os.path.normpath(_EXTENSION_DIR).lower():
            return True, ""

    return False, ""


def detect_browser_registration(ext_dir: str, detected_browsers: list[BrowserInfo] | None = None) -> tuple[list[BrowserRegistrationResult], bool]:
    """Check all detected browsers and their profiles for the extension.

    Uses BrowserProfileManager for profile discovery and the browser
    package's BrowserRegistrationResult for results.
    Returns ``(per_browser_results, installed_in_any_browser)``.
    """
    browsers = detected_browsers if detected_browsers is not None else BrowserLauncher.detect_all()
    results: list[BrowserRegistrationResult] = []
    installed_any = False

    for browser_info in browsers:
        if not browser_info.installed:
            results.append(BrowserRegistrationResult(browser_name=browser_info.name))
            continue

        scan_result = BrowserProfileManager.scan(browser_info.name)
        profile_results: list[BrowserProfileResult] = []
        found_in_browser = False

        for profile in scan_result.profiles:
            if not profile.preferences_exists:
                profile_results.append(BrowserProfileResult(
                    profile_name=profile.name,
                    preferences_exists=False,
                    extension_registered=False,
                    error=profile.error or "Preferences not found",
                ))
                continue

            found, error = _check_extension_in_preferences(profile.preferences_path)
            profile_results.append(BrowserProfileResult(
                profile_name=profile.name,
                preferences_exists=True,
                extension_registered=found,
                error=error,
            ))
            if found:
                found_in_browser = True

        results.append(BrowserRegistrationResult(
            browser_name=browser_info.name,
            profiles_scanned=scan_result.profile_count,
            extension_registered=found_in_browser,
            profile_results=profile_results,
        ))
        if found_in_browser:
            installed_any = True

    return results, installed_any


# ---------------------------------------------------------------------------
# Extension File Detection
# ---------------------------------------------------------------------------

class ExtensionFileStatus:
    """Snapshot of required extension file presence."""

    __slots__ = ("all_present", "missing_files", "manifest_data")

    def __init__(self, all_present: bool, missing_files: list[str], manifest_data: dict[str, Any]) -> None:
        self.all_present = all_present
        self.missing_files = missing_files
        self.manifest_data = manifest_data


def detect_extension_files() -> ExtensionFileStatus:
    """Verify that all required extension files exist and manifest is readable.

    Delegates to ExtensionInstallationEngine.validate_extension() from
    the browser package. Returns an ExtensionFileStatus. Never raises.
    """
    try:
        result = ExtensionInstallationEngine.validate_extension(_EXTENSION_DIR)
        return ExtensionFileStatus(
            all_present=result.valid,
            missing_files=list(result.missing_files),
            manifest_data=dict(result.manifest_data),
        )
    except Exception:
        return ExtensionFileStatus(False, list(_REQUIRED_EXTENSION_FILES), {})


# ---------------------------------------------------------------------------
# Combined Detection Result
# ---------------------------------------------------------------------------

class ExtensionStatus:
    """Aggregated detection result for the entire extension ecosystem."""

    __slots__ = (
        "file_status", "extension_version", "companion_version",
        "compatibility", "folder_exists",
        "all_browsers", "browser_registration", "installed_in_browser",
        "browser_running", "browser_processes",
    )

    # Compatibility states
    COMPATIBLE = "Compatible"
    MISMATCH = "Version Mismatch"
    MISSING = "Extension Missing"
    NOT_INSTALLED = "Not Installed"
    UNKNOWN = "Unknown"

    def __init__(self) -> None:
        self.file_status = ExtensionFileStatus(False, [], {})
        self.extension_version = ""
        self.companion_version = ""
        self.compatibility = self.UNKNOWN
        self.folder_exists = False
        self.all_browsers: list[BrowserInfo] = []
        self.browser_registration: list[BrowserRegistrationResult] = []
        self.installed_in_browser = False
        self.browser_running: dict[str, bool] = {}
        self.browser_processes: dict[str, list] = {}


def _detect_browser_running() -> dict[str, bool]:
    """Detect which supported browsers are currently running.

    Returns ``{browser_name: is_running}`` using BrowserSessionManager.
    """
    try:
        running = BrowserSessionManager.running_all()
        return {name: len(procs) > 0 for name, procs in running.items()}
    except Exception:
        return {}


def run_full_detection() -> ExtensionStatus:
    """Execute all detection layers and return a unified status object.

    Every detection step is independently guarded against exceptions
    to ensure the UI never crashes regardless of system state.

    Uses the browser package for:
    - Browser detection (BrowserLauncher)
    - Profile scanning (BrowserProfileManager)
    - Extension file validation (ExtensionInstallationEngine)
    - Running browser detection (BrowserSessionManager)
    """
    status = ExtensionStatus()

    # 1. Multi-browser detection (BrowserLauncher)
    try:
        status.all_browsers = BrowserLauncher.detect_all()
    except Exception:
        status.all_browsers = []

    # 2. Extension file detection (ExtensionInstallationEngine)
    try:
        status.file_status = detect_extension_files()
    except Exception:
        status.file_status = ExtensionFileStatus(False, [], {})

    # 3. Folder existence
    try:
        status.folder_exists = os.path.isdir(_EXTENSION_DIR)
    except Exception:
        status.folder_exists = False

    # 4. Extension version from manifest
    try:
        status.extension_version = status.file_status.manifest_data.get("version", "")
    except Exception:
        status.extension_version = ""

    # 5. Companion version
    try:
        from updater import COMPANION_VERSION
        status.companion_version = COMPANION_VERSION
    except Exception:
        status.companion_version = ""

    # 6. Browser registration detection (BrowserProfileManager + extension check)
    try:
         status.browser_registration, status.installed_in_browser = (
             detect_browser_registration(_EXTENSION_DIR, status.all_browsers)
         )
    except Exception:
        status.browser_registration = []
        status.installed_in_browser = False

    # 7. Browser running detection (BrowserSessionManager)
    try:
        running_data = BrowserSessionManager.running_all()
        status.browser_running = {name: len(procs) > 0 for name, procs in running_data.items()}
        status.browser_processes = running_data
    except Exception:
        status.browser_running = {}
        status.browser_processes = {}

    # 8. Compatibility
    try:
        status.compatibility = _compute_compatibility(status)
    except Exception:
        status.compatibility = ExtensionStatus.UNKNOWN

    return status


def _compute_compatibility(status: ExtensionStatus) -> str:
    """Derive the compatibility label from the detection results."""
    if not status.folder_exists:
        return ExtensionStatus.MISSING
    if not status.extension_version:
        return ExtensionStatus.MISSING
    if not status.file_status.all_present:
        return ExtensionStatus.MISSING
    if not status.installed_in_browser:
        return ExtensionStatus.NOT_INSTALLED
    if not status.companion_version:
        return ExtensionStatus.UNKNOWN
    if status.extension_version == status.companion_version:
        return ExtensionStatus.COMPATIBLE
    return ExtensionStatus.MISMATCH


# ---------------------------------------------------------------------------
# Compatibility helpers for UI color
# ---------------------------------------------------------------------------

def _compat_color(compat: str) -> str:
    if compat == ExtensionStatus.COMPATIBLE:
        return _CLR_GREEN
    if compat == ExtensionStatus.MISMATCH:
        return _CLR_ORANGE
    if compat == ExtensionStatus.MISSING:
        return _CLR_RED
    if compat == ExtensionStatus.NOT_INSTALLED:
        return _CLR_ORANGE
    return _CLR_GREY


def _format_compat_label(compat: str) -> str:
    """Return a human-readable compatibility label with icon."""
    if compat == ExtensionStatus.COMPATIBLE:
        return "\u2714 Compatible"
    if compat == ExtensionStatus.MISMATCH:
        return "\u26a0 Version Mismatch"
    if compat == ExtensionStatus.MISSING:
        return "\u2716 Extension Missing"
    if compat == ExtensionStatus.NOT_INSTALLED:
        return "\u26a0 Not Installed"
    return "\u26a0 Unknown"


def _extension_status_text(status: ExtensionStatus) -> tuple[str, str]:
    """Return (label, color) for extension installation status."""
    if not status.file_status.all_present:
        if not status.file_status.missing_files:
            return "Unknown", _CLR_GREY
        return "Damaged", _CLR_RED
    if not status.installed_in_browser:
        return "Files Present", _CLR_ORANGE
    return "Healthy", _CLR_GREEN


# ---------------------------------------------------------------------------
# Installation Assistant helpers
# ---------------------------------------------------------------------------

def _compute_overall_ready(status: ExtensionStatus) -> tuple[str, str]:
    """Return (label, color) for the Overall Ready row."""
    if not any(b.installed for b in status.all_browsers):
        return "No", _CLR_RED
    if not status.folder_exists:
        return "No", _CLR_RED
    if not status.file_status.all_present:
        return "No", _CLR_RED
    if not status.installed_in_browser:
        return "No", _CLR_RED
    if status.compatibility == ExtensionStatus.COMPATIBLE:
        return "Yes", _CLR_GREEN
    if status.compatibility == ExtensionStatus.MISMATCH:
        return "Needs Update", _CLR_ORANGE
    return "No", _CLR_RED


# ---------------------------------------------------------------------------
# Badge callback registry
# ---------------------------------------------------------------------------

_badge_callbacks: list[Callable[[str], None]] = []


def register_badge_callback(fn: Callable[[str], None]) -> None:
    """Register a callback that receives badge color strings."""
    if fn not in _badge_callbacks:
        _badge_callbacks.append(fn)


def unregister_badge_callback(fn: Callable[[str], None]) -> None:
    """Remove a registered badge callback."""
    try:
        _badge_callbacks.remove(fn)
    except ValueError:
        pass


def _notify_badge(color: str) -> None:
    """Broadcast badge color to all registered listeners."""
    for fn in list(_badge_callbacks):
        try:
            fn(color)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Browser Health
# ---------------------------------------------------------------------------

_BROWSER_ICONS: dict[str, str] = {
    "Chrome": "\U0001f310",
    "Brave": "\U0001f981",
    "Edge": "\U0001f537",
}

_HEALTH_LABELS: dict[str, tuple[str, str, str]] = {
    "Healthy": ("Healthy", "#22c55e", "#0f2a1a"),
    "Needs Attention": ("Needs Attention", "#f59e0b", "#2a1f0f"),
    "Extension Missing": ("Extension Missing", "#ef4444", "#2a0f0f"),
    "Browser Closed": ("Browser Closed", "#8b92a8", "#1a1d27"),
    "Version Mismatch": ("Version Mismatch", "#f59e0b", "#2a1f0f"),
    "Unavailable": ("Unavailable", "#8b92a8", "#1a1d27"),
}

_FILTER_OPTIONS: list[str] = [
    "Show All",
    "Installed",
    "Running",
    "Needs Action",
    "Healthy",
]


def _compute_browser_health(
    browser_info: BrowserInfo,
    reg_result: BrowserRegistrationResult | None,
    running: bool,
    status: ExtensionStatus | None,
) -> tuple[str, str, str]:
    """Return (label, text_color, bg_color) for a browser's health badge."""
    if not browser_info.installed:
        return _HEALTH_LABELS["Unavailable"]
    if not running:
        return _HEALTH_LABELS["Browser Closed"]
    ext_registered = reg_result.extension_registered if reg_result else False
    if not ext_registered:
        return _HEALTH_LABELS["Extension Missing"]
    if status and status.compatibility == ExtensionStatus.MISMATCH:
        return _HEALTH_LABELS["Version Mismatch"]
    if status and status.compatibility == ExtensionStatus.COMPATIBLE:
        return _HEALTH_LABELS["Healthy"]
    return _HEALTH_LABELS["Needs Attention"]


# ---------------------------------------------------------------------------
# Browser Card Widget
# ---------------------------------------------------------------------------

class _BrowserCard:
    """Modern card widget for a single browser with status, actions, and details."""

    _BTN_HEIGHT: int = 28

    def __init__(
        self,
        parent: ctk.CTkFrame,
        browser_name: str,
        callbacks: dict[str, Any],
    ) -> None:
        self._name = browser_name
        self._callbacks = callbacks
        self._expanded = False
        self._selected = False
        self._health_label = "Unavailable"
        self._installed = False
        self._running = False
        self._ext_registered = False
        self._action_backup: tuple[str, str, str] | None = None

        self._card = ctk.CTkFrame(
            parent,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )

        self._build_header()
        self._build_status_row()
        self._build_actions()
        self._build_details_toggle()
        self._build_details()

    # ── Construction ────────────────────────────────────────────────────

    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self._card, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(12, 2))

        icon = _BROWSER_ICONS.get(self._name, "\U0001f5a5\ufe0f")
        self._icon_lbl = ctk.CTkLabel(
            hdr, text=icon, font=ctk.CTkFont(size=28),
        )
        self._icon_lbl.pack(side="left", padx=(0, 10))

        left_col = ctk.CTkFrame(hdr, fg_color="transparent")
        left_col.pack(side="left", fill="x", expand=True)

        self._name_lbl = ctk.CTkLabel(
            left_col, text=self._name,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#e8eaf0", anchor="w",
        )
        self._name_lbl.pack(anchor="w")

        self._version_lbl = ctk.CTkLabel(
            left_col, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8", anchor="w",
        )
        self._version_lbl.pack(anchor="w")

        self._health_badge = ctk.CTkLabel(
            hdr, text="", width=120, height=24, corner_radius=6,
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
        )
        self._health_badge.pack(side="right", padx=(8, 0))

        for widget in (hdr, self._icon_lbl, left_col, self._name_lbl, self._version_lbl, self._health_badge):
            widget.bind("<Button-1>", lambda _e: self._on_select())

    def _build_status_row(self) -> None:
        ctk.CTkFrame(self._card, height=1, fg_color="#2e3347").pack(
            fill="x", padx=16, pady=(4, 4),
        )
        row = ctk.CTkFrame(self._card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 4))

        self._installed_lbl = ctk.CTkLabel(
            row, text="", font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._installed_lbl.pack(side="left", padx=(0, 20))

        self._running_lbl = ctk.CTkLabel(
            row, text="", font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._running_lbl.pack(side="left", padx=(0, 20))

        self._extension_lbl = ctk.CTkLabel(
            row, text="", font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._extension_lbl.pack(side="left")

    def _build_actions(self) -> None:
        ctk.CTkFrame(self._card, height=1, fg_color="#2e3347").pack(
            fill="x", padx=16, pady=(4, 4),
        )
        row = ctk.CTkFrame(self._card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 4))

        _btn_font = ctk.CTkFont(family="Segoe UI", size=10, weight="bold")
        _h = self._BTN_HEIGHT

        self._launch_btn = ctk.CTkButton(
            row, text="\u25b6 Launch", width=72, height=_h, corner_radius=6,
            fg_color="#22c55e", hover_color="#16a34a", text_color="#ffffff",
            font=_btn_font,
            command=lambda: self._callbacks.get("launch", lambda _: None)(self._name),
        )
        self._launch_btn.pack(side="left", padx=(0, 4))

        self._install_btn = ctk.CTkButton(
            row, text="\U0001f4e6 Install", width=80, height=_h, corner_radius=6,
            fg_color="#4f8ef7", hover_color="#3a76e8", text_color="#ffffff",
            font=_btn_font,
            command=lambda: self._callbacks.get("install", lambda _: None)(self._name),
        )
        self._install_btn.pack(side="left", padx=(0, 4))

        self._ext_page_btn = ctk.CTkButton(
            row, text="\U0001f517 Extensions", width=90, height=_h, corner_radius=6,
            fg_color="#20232f", hover_color="#2e3347", text_color="#e8eaf0",
            font=_btn_font,
            command=lambda: self._callbacks.get("extensions_page", lambda _: None)(self._name),
        )
        self._ext_page_btn.pack(side="left", padx=(0, 4))

        self._profile_btn = ctk.CTkButton(
            row, text="\U0001f4c1 Profile", width=72, height=_h, corner_radius=6,
            fg_color="#20232f", hover_color="#2e3347", text_color="#e8eaf0",
            font=_btn_font,
            command=lambda: self._callbacks.get("profile_folder", lambda _: None)(self._name),
        )
        self._profile_btn.pack(side="left", padx=(0, 4))

        self._verify_btn = ctk.CTkButton(
            row, text="\U0001f50d Verify", width=72, height=_h, corner_radius=6,
            fg_color="#8b5cf6", hover_color="#7c3aed", text_color="#ffffff",
            font=_btn_font,
            command=lambda: self._callbacks.get("verify", lambda _: None)(self._name),
        )
        self._verify_btn.pack(side="left")

    def _build_details_toggle(self) -> None:
        ctk.CTkFrame(self._card, height=1, fg_color="#2e3347").pack(
            fill="x", padx=16, pady=(4, 0),
        )
        self._details_toggle = ctk.CTkButton(
            self._card, text="\u25b6 Details", height=24, corner_radius=0,
            fg_color="transparent", hover_color="#242838",
            text_color="#8b92a8", anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            command=self._toggle_details,
        )
        self._details_toggle.pack(fill="x", padx=16, pady=(0, 4))

    def _build_details(self) -> None:
        self._details_frame = ctk.CTkFrame(
            self._card, fg_color="#0f1117", corner_radius=8,
        )

        fields = [
            ("Executable", "exe"),
            ("Profile Dir", "profile_dir"),
            ("Profiles", "profiles"),
            ("Processes", "processes"),
            ("Ext Folder", "ext_folder"),
            ("Dev Mode", "dev_mode"),
        ]
        self._detail_lbls: dict[str, ctk.CTkLabel] = {}
        for label, key in fields:
            row = ctk.CTkFrame(self._details_frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(
                row, text=f"{label}:",
                font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
                text_color="#8b92a8", width=80, anchor="w",
            ).pack(side="left")
            val = ctk.CTkLabel(
                row, text="\u2014",
                font=ctk.CTkFont(family="Consolas", size=10),
                text_color="#4f8ef7", anchor="w",
            )
            val.pack(side="left", fill="x", expand=True)
            self._detail_lbls[key] = val

    def _toggle_details(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self._details_frame.pack(fill="x", padx=12, pady=(0, 8))
            self._details_toggle.configure(text="\u25bc Details")
        else:
            self._details_frame.pack_forget()
            self._details_toggle.configure(text="\u25b6 Details")

    # ── Public API ──────────────────────────────────────────────────────

    def update_state(
        self,
        browser_info: BrowserInfo,
        reg_result: BrowserRegistrationResult | None,
        running: bool,
        status: ExtensionStatus | None,
        processes: list | None = None,
    ) -> None:
        """Refresh all card widgets from detection data."""
        health_label, health_fg, health_bg = _compute_browser_health(
            browser_info, reg_result, running, status,
        )

        if browser_info.installed:
            self._name_lbl.configure(text_color="#e8eaf0")
            ver = f"v{browser_info.version}" if browser_info.version else ""
            self._version_lbl.configure(text=ver)
        else:
            self._name_lbl.configure(text_color="#8b92a8")
            self._version_lbl.configure(text="Not installed")

        self._health_badge.configure(
            text=health_label, text_color=health_fg, fg_color=health_bg,
        )

        inst_ok = browser_info.installed
        self._installed_lbl.configure(
            text="\u2714 Installed" if inst_ok else "\u2718 Not Installed",
            text_color=_CLR_GREEN if inst_ok else _CLR_RED,
        )

        self._running_lbl.configure(
            text="\u2714 Running" if running else "\u2718 Not Running",
            text_color=_CLR_GREEN if running else _CLR_GREY,
        )

        ext_ok = reg_result.extension_registered if reg_result else False
        self._extension_lbl.configure(
            text="\u2714 Extension Installed" if ext_ok else "\u26a0 Extension Not Installed",
            text_color=_CLR_GREEN if ext_ok else _CLR_ORANGE,
        )

        state = "normal" if inst_ok else "disabled"
        for btn in (self._launch_btn, self._install_btn, self._ext_page_btn, self._profile_btn):
            btn.configure(state=state)

        self._health_label = health_label
        self._installed = inst_ok
        self._running = running
        self._ext_registered = ext_ok

        if self._action_backup is not None:
            self._action_backup = (health_label, health_fg, health_bg)

        self._update_details(browser_info, reg_result, running, processes=processes)

    def _update_details(
        self,
        browser_info: BrowserInfo,
        reg_result: BrowserRegistrationResult | None,
        running: bool,
        processes: list | None = None,
    ) -> None:
        self._detail_lbls["exe"].configure(
            text=browser_info.path or "\u2014",
            text_color="#4f8ef7" if browser_info.path else "#8b92a8",
        )
        self._detail_lbls["profile_dir"].configure(
            text=browser_info.user_data_dir or "\u2014",
            text_color="#4f8ef7" if browser_info.user_data_dir else "#8b92a8",
        )

        if reg_result and reg_result.profile_results:
            names = [p.profile_name for p in reg_result.profile_results]
            self._detail_lbls["profiles"].configure(
                text=", ".join(names) if names else "\u2014",
            )
        else:
            self._detail_lbls["profiles"].configure(text="\u2014")

        if running:
            try:
                procs = processes if processes is not None else BrowserSessionManager.running(browser_info.name)
                if procs:
                    pids = ", ".join(str(p.pid) for p in procs[:5])
                    extra = f" (+{len(procs) - 5})" if len(procs) > 5 else ""
                    self._detail_lbls["processes"].configure(
                        text=f"{len(procs)} running (PIDs: {pids}{extra})",
                    )
                else:
                    self._detail_lbls["processes"].configure(text="Not running")
            except Exception:
                self._detail_lbls["processes"].configure(text="\u2014")
        else:
            self._detail_lbls["processes"].configure(text="Not running")

        self._detail_lbls["ext_folder"].configure(text=_EXTENSION_DIR)
        self._detail_lbls["dev_mode"].configure(
            text="Supported" if browser_info.installed else "N/A",
        )

    def matches_filter(self, filter_name: str) -> bool:
        """Return True if this card should be visible under the given filter."""
        if filter_name == "Show All":
            return True
        if filter_name == "Installed":
            return self._installed
        if filter_name == "Running":
            return self._running
        if filter_name == "Healthy":
            return self._health_label == "Healthy"
        if filter_name == "Needs Action":
            return self._health_label in (
                "Extension Missing", "Version Mismatch", "Needs Attention",
            )
        return True

    def set_action_state(self, text: str, color: str, bg: str) -> None:
        """Show temporary action feedback on the health badge."""
        if self._action_backup is None:
            self._action_backup = (
                self._health_badge.cget("text"),
                self._health_badge.cget("text_color"),
                self._health_badge.cget("fg_color"),
            )
        self._health_badge.configure(text=text, text_color=color, fg_color=bg)

    def clear_action_state(self) -> None:
        """Restore normal health badge after action completes."""
        if self._action_backup is not None:
            text, color, bg = self._action_backup
            self._health_badge.configure(text=text, text_color=color, fg_color=bg)
            self._action_backup = None

    def set_selected(self, selected: bool) -> None:
        """Visually highlight this card as the selected browser."""
        self._selected = selected
        if selected:
            self._card.configure(border_color="#4f8ef7", border_width=2)
        else:
            self._card.configure(border_color="#2e3347", border_width=1)

    @property
    def browser_name(self) -> str:
        return self._name

    def _on_select(self) -> None:
        """Handle click on card header — notify the page of selection."""
        cb = self._callbacks.get("select")
        if cb:
            cb(self._name)

    def pack(self, **kwargs: Any) -> None:
        self._card.pack(**kwargs)

    def pack_forget(self) -> None:
        self._card.pack_forget()

    def destroy(self) -> None:
        self._card.destroy()

    @property
    def frame(self) -> ctk.CTkFrame:
        return self._card


# ---------------------------------------------------------------------------
# Extension Manager Page
# ---------------------------------------------------------------------------

class ExtensionManagerPage(BasePage):
    """
    Extension management page displaying real-time detection of Chrome,
    extension file integrity, version compatibility, installation assistant,
    and utility actions.
    """

    def __init__(self, master: ctk.CTkFrame, manager: BackendManager, logger: AppLogger) -> None:
        super().__init__(master, manager, logger)
        self._cached_status: ExtensionStatus | None = None
        self._detecting: bool = False
        self._verify_dialog: _VerificationDialog | None = None
        self._wizard_dialog: _InstallationWizard | None = None
        self._browser_cards: dict[str, _BrowserCard] = {}
        self._card_callbacks: dict[str, Any] | None = None
        self._active_filter: str = "Show All"
        self._selected_browser: str | None = None
        self._automation_engine: BrowserAutomationEngine | None = None
        self._automation_sessions: dict[str, str] = {}
        self._automation_poll_jobs: dict[str, str] = {}
        self._rec_session_id: str | None = None
        self._rec_poll_job: str | None = None
        self._rec_state: str = ""
        self._rec_fixing: bool = False
        self._delayed_jobs: set[str] = set()
        self._build_ui()

    def _build_ui(self) -> None:
        # ── Page header ────────────────────────────────────────────────────
        ctk.CTkLabel(
            self,
            text="\U0001f9e9 Extension Manager",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#e8eaf0",
        ).pack(anchor="w", padx=20, pady=(20, 4))

        ctk.CTkLabel(
            self,
            text="Manage and inspect the MediaForge browser extension.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
        ).pack(anchor="w", padx=20, pady=(0, 12))

        # ── Filter bar ─────────────────────────────────────────────────────
        filter_frame = ctk.CTkFrame(self, fg_color="transparent")
        filter_frame.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(
            filter_frame, text="Filter:",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#8b92a8",
        ).pack(side="left", padx=(0, 8))

        self._filter_btns: dict[str, ctk.CTkButton] = {}
        for opt in _FILTER_OPTIONS:
            is_active = opt == self._active_filter
            btn = ctk.CTkButton(
                filter_frame, text=opt, height=26, corner_radius=6,
                width=len(opt) * 8 + 16,
                fg_color="#4f8ef7" if is_active else "#20232f",
                hover_color="#3a76e8" if is_active else "#2e3347",
                text_color="#ffffff" if is_active else "#8b92a8",
                font=ctk.CTkFont(
                    family="Segoe UI", size=10,
                    weight="bold" if is_active else "normal",
                ),
                command=lambda o=opt: self._apply_filter(o),
            )
            btn.pack(side="left", padx=(0, 4))
            self._filter_btns[opt] = btn

        # ── Browser Cards Container ────────────────────────────────────────
        self._browser_cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._browser_cards_frame.pack(fill="x", padx=20, pady=(0, 12))

        # ── Status Card (simplified) ──────────────────────────────────────
        self._status_card = ctk.CTkFrame(
            self, fg_color="#1a1d27", border_color="#2e3347",
            border_width=1, corner_radius=12,
        )
        self._status_card.pack(fill="x", padx=20, pady=(0, 12))

        self._ext_status_row = self._make_status_row(
            self._status_card, "Extension Status", "Detecting\u2026", "#f59e0b",
        )
        self._ext_status_lbl = self._ext_status_row[1]

        _sep = ctk.CTkFrame(self._status_card, height=1, fg_color="#2e3347")
        _sep.pack(fill="x", padx=16)

        self._compat_row = self._make_status_row(
            self._status_card, "Compatibility", "Detecting\u2026", "#f59e0b",
        )
        self._compat_lbl = self._compat_row[1]

        _sep2 = ctk.CTkFrame(self._status_card, height=1, fg_color="#2e3347")
        _sep2.pack(fill="x", padx=16)

        self._ext_ver_row = self._make_status_row(
            self._status_card, "Extension Version", "v\u2014", "#8b92a8",
        )
        self._ext_ver_lbl = self._ext_ver_row[1]

        _sep3 = ctk.CTkFrame(self._status_card, height=1, fg_color="#2e3347")
        _sep3.pack(fill="x", padx=16)

        self._comp_ver_row = self._make_status_row(
            self._status_card, "Companion Version", "v\u2014", "#8b92a8",
        )
        self._comp_ver_lbl = self._comp_ver_row[1]

        # ── Recommendation Card ───────────────────────────────────────────
        self._recommendation_card = ctk.CTkFrame(
            self, fg_color="#241e12", border_color="#f59e0b",
            border_width=1, corner_radius=10,
        )

        self._recommend_title = ctk.CTkLabel(
            self._recommendation_card,
            text="\u26a0 Recommended Action",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#f59e0b", anchor="w",
        )
        self._recommend_title.pack(fill="x", padx=16, pady=(10, 2))

        self._recommend_msg = ctk.CTkLabel(
            self._recommendation_card, text="",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e8eaf0", anchor="w", wraplength=520, justify="left",
        )
        self._recommend_msg.pack(fill="x", padx=16, pady=2)

        self._recommend_desc = ctk.CTkLabel(
            self._recommendation_card, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8", anchor="w", wraplength=520, justify="left",
        )
        self._recommend_desc.pack(fill="x", padx=16, pady=(2, 10))

        # ── Smart Auto-Fix Action Row ──────────────────────────────────────
        self._rec_action_frame = ctk.CTkFrame(
            self._recommendation_card, fg_color="transparent",
        )

        self._rec_fix_btn = ctk.CTkButton(
            self._rec_action_frame, text="", width=140, height=28, corner_radius=8,
            fg_color="#4f8ef7", hover_color="#3a76e8", text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_rec_fix_now,
        )
        self._rec_fix_btn.pack(side="left", padx=(0, 8))

        self._rec_retry_btn = ctk.CTkButton(
            self._rec_action_frame, text="Retry", width=70, height=28, corner_radius=8,
            fg_color="#20232f", hover_color="#2e3347", text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._on_rec_retry,
        )
        self._rec_retry_btn.pack(side="left")

        self._rec_progress_lbl = ctk.CTkLabel(
            self._rec_action_frame, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#f59e0b", anchor="w",
        )
        self._rec_progress_lbl.pack(side="left", padx=(12, 0))

        # ── Action Buttons ────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 8))

        self._refresh_btn = ctk.CTkButton(
            btn_frame, text="Refresh Status", width=130, height=34, corner_radius=8,
            fg_color="#20232f", hover_color="#2e3347", text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_refresh,
        )
        self._refresh_btn.pack(side="left", padx=(0, 8))

        self._verify_btn = ctk.CTkButton(
            btn_frame, text="Verify Installation", width=150, height=34, corner_radius=8,
            fg_color="#8b5cf6", hover_color="#7c3aed", text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_verify,
        )
        self._verify_btn.pack(side="left", padx=(0, 8))

        self._wizard_btn = ctk.CTkButton(
            btn_frame, text="Installation Wizard", width=160, height=34, corner_radius=8,
            fg_color="#22c55e", hover_color="#16a34a", text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_show_wizard,
        )
        self._wizard_btn.pack(side="left")

        # ── Status message ────────────────────────────────────────────────
        self._msg_lbl = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._msg_lbl.pack(anchor="w", padx=20, pady=(0, 8))

    # ── Status Row Builder ──────────────────────────────────────────────

    @staticmethod
    def _make_status_row(
        parent: ctk.CTkFrame, label: str, value: str, color: str
    ) -> tuple[ctk.CTkFrame, ctk.CTkLabel]:
        """Create a label/value row inside a card and return the value label."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(6, 6))
        ctk.CTkLabel(
            row,
            text=label,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
        ).pack(side="left")
        val_lbl = ctk.CTkLabel(
            row,
            text=value,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=color,
        )
        val_lbl.pack(side="right")
        return row, val_lbl

    # ── Card Callbacks ──────────────────────────────────────────────────

    def _get_card_callbacks(self) -> dict[str, Any]:
        """Return the cached callback dict passed to every _BrowserCard."""
        if self._card_callbacks is None:
            self._card_callbacks = {
                "launch": self._on_card_launch,
                "install": self._on_card_install,
                "extensions_page": self._on_card_open_ext_page,
                "profile_folder": self._on_card_open_profile,
                "verify": self._on_card_verify,
                "select": self._on_card_select,
            }
        return self._card_callbacks

    def _clear_card_action(self, browser_name: str) -> None:
        """Safely clear action feedback on a card (handles stale references)."""
        if not self.winfo_exists():
            return
        card = self._browser_cards.get(browser_name)
        if card:
            card.clear_action_state()

    def _on_card_launch(self, browser_name: str) -> None:
        """Launch the browser via the automation engine."""
        engine = self._ensure_engine()
        if engine is None:
            return
        self._cancel_automation_for_browser(browser_name)

        card = self._browser_cards.get(browser_name)
        if card:
            card.set_action_state("Launching\u2026", "#4f8ef7", "#0f1a2e")
        self._set_message(f"Launching {browser_name}\u2026", "#f59e0b")

        session_id = engine.run_async(
            browser_name=browser_name,
            pipeline=create_launch_pipeline(),
            progress_callback=lambda s: None,
        )
        self._automation_sessions[browser_name] = session_id
        self._start_polling(browser_name, session_id)

    def _on_card_install(self, browser_name: str) -> None:
        """Install extension in browser via the automation engine."""
        engine = self._ensure_engine()
        if engine is None:
            return
        self._cancel_automation_for_browser(browser_name)

        card = self._browser_cards.get(browser_name)
        if card:
            card.set_action_state("Installing\u2026", "#f59e0b", "#2a1f0f")
        self._set_message(f"Installing extension in {browser_name}\u2026", "#f59e0b")

        session_id = engine.run_async(
            browser_name=browser_name,
            extension_dir=_EXTENSION_DIR,
            target_url="chrome://extensions",
            pipeline=create_install_pipeline(),
            progress_callback=lambda s: None,
        )
        self._automation_sessions[browser_name] = session_id
        self._start_polling(browser_name, session_id)

    def _on_card_open_ext_page(self, browser_name: str) -> None:
        """Open the extensions page via the automation engine."""
        engine = self._ensure_engine()
        if engine is None:
            return
        self._cancel_automation_for_browser(browser_name)

        card = self._browser_cards.get(browser_name)
        if card:
            card.set_action_state("Opening\u2026", "#4f8ef7", "#0f1a2e")

        urls = {
            "Chrome": "chrome://extensions",
            "Brave": "brave://extensions",
            "Edge": "edge://extensions",
        }
        url = urls.get(browser_name, "chrome://extensions")

        session_id = engine.run_async(
            browser_name=browser_name,
            target_url=url,
            pipeline=create_open_ext_page_pipeline(),
            progress_callback=lambda s: None,
        )
        self._automation_sessions[browser_name] = session_id
        self._start_polling(browser_name, session_id)

    def _on_card_open_profile(self, browser_name: str) -> None:
        """Open the browser profile folder with action feedback."""
        card = self._browser_cards.get(browser_name)
        if card:
            card.set_action_state("Opening\u2026", "#4f8ef7", "#0f1a2e")

        try:
            info = BrowserLauncher.detect_by_name(browser_name)
            if info and info.user_data_dir and os.path.isdir(info.user_data_dir):
                if sys.platform == "win32":
                    os.startfile(info.user_data_dir)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", info.user_data_dir])
                else:
                    subprocess.Popen(["xdg-open", info.user_data_dir])
                self._set_message(f"Opened {browser_name} profile folder.")
                if card:
                    card.set_action_state("Completed", "#22c55e", "#0f2a1a")
                    self._schedule_delayed(2500, lambda bn=browser_name: self._clear_card_action(bn))
            else:
                self._set_message(f"Profile folder not found for {browser_name}.", "#f59e0b")
                if card:
                    card.set_action_state("Not Found", "#f59e0b", "#2a1f0f")
                    self._schedule_delayed(2500, lambda bn=browser_name: self._clear_card_action(bn))
        except Exception as exc:
            self._set_message(f"Failed to open profile: {exc}", "#ef4444")
            if card:
                card.set_action_state("Failed", "#ef4444", "#2a0f0f")
                self._schedule_delayed(2500, lambda bn=browser_name: self._clear_card_action(bn))

    # ── Automation Engine Integration ────────────────────────────────

    def _ensure_engine(self) -> BrowserAutomationEngine | None:
        """Lazily initialize and return the automation engine."""
        if self._automation_engine is None:
            try:
                self._automation_engine = get_automation_engine()
            except Exception:
                return None
        return self._automation_engine

    def _on_card_verify(self, browser_name: str) -> None:
        """Verify extension installation via the automation engine."""
        engine = self._ensure_engine()
        if engine is None:
            return
        self._cancel_automation_for_browser(browser_name)

        card = self._browser_cards.get(browser_name)
        if card:
            card.set_action_state("Verifying\u2026", "#8b5cf6", "#1a0f2e")
        self._set_message(f"Verifying {browser_name} installation\u2026", "#f59e0b")

        session_id = engine.run_async(
            browser_name=browser_name,
            extension_dir=_EXTENSION_DIR,
            pipeline=create_verify_pipeline(),
            progress_callback=lambda s: None,
        )
        self._automation_sessions[browser_name] = session_id
        self._start_polling(browser_name, session_id)

    def _cancel_automation_for_browser(self, browser_name: str) -> None:
        """Cancel any active automation session for the given browser."""
        self._stop_polling(browser_name)
        session_id = self._automation_sessions.pop(browser_name, None)
        if session_id and self._automation_engine:
            try:
                self._automation_engine.cancel(session_id)
            except Exception:
                pass

    def _cancel_all_automation(self) -> None:
        """Cancel all active automation sessions."""
        for browser_name in list(self._automation_sessions.keys()):
            self._cancel_automation_for_browser(browser_name)

    def _start_polling(self, browser_name: str, session_id: str) -> None:
        """Start polling an automation session for completion."""
        self._stop_polling(browser_name)
        self._poll_automation(browser_name, session_id)

    def _stop_polling(self, browser_name: str) -> None:
        """Stop polling for a given browser."""
        job = self._automation_poll_jobs.pop(browser_name, None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass

    def _poll_automation(self, browser_name: str, session_id: str) -> None:
        """Poll an automation session for completion, updating UI."""
        if not self.winfo_exists():
            return

        engine = self._automation_engine
        if engine is None:
            return

        session = engine.get_session(session_id)
        if session is None or session.is_terminal:
            result = engine.get_result(session_id)
            if result is not None:
                self._on_automation_finished(browser_name, result)
            return

        self._automation_poll_jobs[browser_name] = self.after(
            200, lambda: self._poll_automation(browser_name, session_id),
        )

    def _on_automation_finished(self, browser_name: str, result: Any) -> None:
        """Handle automation session completion."""
        if not self.winfo_exists():
            return

        self._automation_sessions.pop(browser_name, None)
        self._stop_polling(browser_name)

        card = self._browser_cards.get(browser_name)
        if card:
            if result.success:
                card.set_action_state("Completed", "#22c55e", "#0f2a1a")
            else:
                card.set_action_state("Failed", "#ef4444", "#2a0f0f")
            self._schedule_delayed(2500, lambda bn=browser_name: self._clear_card_action(bn))

        if result.success:
            self._set_message(f"{browser_name} action completed.", "#22c55e")
            self._schedule_delayed(2000, self._on_refresh)
        else:
            err = result.error.message if result.error else "Unknown error"
            self._set_message(f"{browser_name} action failed: {err}", "#ef4444")

    def _on_card_select(self, browser_name: str) -> None:
        """Handle card header click — select browser and update recommendation."""
        if self._selected_browser == browser_name:
            self._selected_browser = None
        else:
            if self._selected_browser != browser_name:
                self._cancel_rec_session()
            self._selected_browser = browser_name

        for name, card in self._browser_cards.items():
            card.set_selected(name == self._selected_browser)

        self._update_recommendation_for_selected()

    def _compute_recommendation_state(
        self, status: ExtensionStatus | None, browser_name: str | None,
    ) -> dict[str, Any]:
        """Compute the recommendation state, action label, and pipeline.

        Returns a dict with keys: state, title, msg, desc, bg, border,
        title_color, action_label, pipeline_factory.
        """
        none_state: dict[str, Any] = {
            "state": "no_status", "title": "", "msg": "", "desc": "",
            "bg": "#1a1a2e", "border": "#f59e0b", "title_color": "#f59e0b",
            "action_label": "", "pipeline_factory": None,
        }
        if status is None:
            return none_state

        if browser_name is not None:
            reg_lookup = {r.browser_name: r for r in status.browser_registration}
            reg = reg_lookup.get(browser_name)
            browser_info = None
            for b in status.all_browsers:
                if b.name == browser_name:
                    browser_info = b
                    break

            if browser_info is None or not browser_info.installed:
                return {
                    "state": "browser_missing",
                    "title": "\u26a0 Not Installed",
                    "msg": f"{browser_name} is not installed on this system.",
                    "desc": "Install or update the browser, then click Refresh.",
                    "bg": "#1a1215", "border": _CLR_RED, "title_color": _CLR_RED,
                    "action_label": "", "pipeline_factory": None,
                }

            running = status.browser_running.get(browser_name, False)
            if not running:
                return {
                    "state": "browser_closed",
                    "title": "\u26a0 Browser Closed",
                    "msg": f"{browser_name} is installed but not running.",
                    "desc": "Launch the browser to activate the extension.",
                    "bg": "#241e12", "border": _CLR_ORANGE, "title_color": _CLR_ORANGE,
                    "action_label": "Launch Browser",
                    "pipeline_factory": create_launch_pipeline,
                }

            ext_ok = reg.extension_registered if reg else False
            if not ext_ok:
                return {
                    "state": "extension_missing",
                    "title": "\u26a0 Extension Missing",
                    "msg": f"{browser_name} is running but the extension is not installed.",
                    "desc": "Install the extension to enable MediaForge features.",
                    "bg": "#241e12", "border": _CLR_ORANGE, "title_color": _CLR_ORANGE,
                    "action_label": "Install Extension",
                    "pipeline_factory": create_install_pipeline,
                }

            if status.compatibility == ExtensionStatus.MISMATCH:
                return {
                    "state": "version_mismatch",
                    "title": "\u26a0 Version Mismatch",
                    "msg": f"{browser_name}: extension and companion versions differ.",
                    "desc": "Relaunch the browser to update the extension.",
                    "bg": "#241e12", "border": _CLR_ORANGE, "title_color": _CLR_ORANGE,
                    "action_label": "Relaunch Browser",
                    "pipeline_factory": create_launch_pipeline,
                }

            return {
                "state": "healthy",
                "title": "\u2714 Healthy",
                "msg": f"{browser_name}: extension is installed and compatible.",
                "desc": "Everything is ready. No action needed.",
                "bg": "#0f2a1a", "border": "#22c55e", "title_color": "#22c55e",
                "action_label": "", "pipeline_factory": None,
            }

        # No browser selected — global view
        any_browser_installed = any(b.installed for b in status.all_browsers)
        is_installed_any = status.installed_in_browser

        if not any_browser_installed:
            return {
                "state": "browser_missing",
                "title": "\u26a0 Recommended Action",
                "msg": "No supported browser was found.",
                "desc": "Install Chrome, Brave or Microsoft Edge to continue.",
                "bg": "#1a1215", "border": _CLR_RED, "title_color": _CLR_RED,
                "action_label": "", "pipeline_factory": None,
            }
        if status.compatibility == ExtensionStatus.MISMATCH:
            return {
                "state": "version_mismatch",
                "title": "\u26a0 Recommended Action",
                "msg": "Extension update required.",
                "desc": "Launch the browser again to update the extension.",
                "bg": "#241e12", "border": _CLR_ORANGE, "title_color": _CLR_ORANGE,
                "action_label": "Relaunch Browser",
                "pipeline_factory": create_launch_pipeline,
            }
        if not is_installed_any:
            return {
                "state": "extension_missing",
                "title": "\u26a0 Recommended Action",
                "msg": "MediaForge Extension is not installed in this browser.",
                "desc": "Use the Install button on the browser card to load the extension.",
                "bg": "#241e12", "border": _CLR_ORANGE, "title_color": _CLR_ORANGE,
                "action_label": "Install Extension",
                "pipeline_factory": create_install_pipeline,
            }
        return none_state

    def _update_recommendation_for_selected(self) -> None:
        """Update the Recommendation Card based on the selected browser."""
        if self._selected_browser is None or self._cached_status is None:
            self._refresh_recommendation_from_status(self._cached_status)
            return

        rec = self._compute_recommendation_state(
            self._cached_status, self._selected_browser,
        )

        if rec["state"] == "no_status":
            self._recommendation_card.pack_forget()
            return

        self._rec_state = rec["state"]
        self._show_recommendation(
            rec["title"], rec["msg"], rec["desc"],
            rec["bg"], rec["border"], rec["title_color"],
            action_label=rec["action_label"],
            pipeline_factory=rec["pipeline_factory"],
        )

    def _show_recommendation(
        self, title: str, msg: str, desc: str,
        bg: str, border: str, title_color: str,
        action_label: str = "",
        pipeline_factory: Any = None,
    ) -> None:
        """Configure and show the recommendation card with given content."""
        self._recommendation_card.configure(fg_color=bg, border_color=border)
        self._recommend_title.configure(text=title, text_color=title_color)
        self._recommend_msg.configure(text=msg)
        self._recommend_desc.configure(text=desc)

        if action_label and not self._rec_fixing:
            self._rec_fix_btn.configure(text=action_label, state="normal")
            self._rec_retry_btn.pack_forget()
            self._rec_progress_lbl.configure(text="")
            self._rec_action_frame.pack(fill="x", padx=16, pady=(0, 10))
        elif self._rec_fixing:
            self._rec_action_frame.pack(fill="x", padx=16, pady=(0, 10))
        else:
            self._rec_action_frame.pack_forget()

        self._recommendation_card.pack(
            fill="x", padx=20, pady=(0, 12), after=self._status_card,
        )

    def _refresh_recommendation_from_status(self, status: ExtensionStatus | None) -> None:
        """Reset recommendation card to the default status-based view."""
        if status is None:
            self._recommendation_card.pack_forget()
            return

        rec = self._compute_recommendation_state(status, None)

        if rec["state"] == "no_status":
            self._recommendation_card.pack_forget()
            return

        self._rec_state = rec["state"]
        self._show_recommendation(
            rec["title"], rec["msg"], rec["desc"],
            rec["bg"], rec["border"], rec["title_color"],
            action_label=rec["action_label"],
            pipeline_factory=rec["pipeline_factory"],
        )

    # ── Smart Recommendation Auto-Fix ──────────────────────────────────

    def _on_rec_fix_now(self) -> None:
        """Trigger the appropriate automation pipeline for the current rec state."""
        if self._rec_fixing or self._rec_session_id is not None:
            return

        engine = self._ensure_engine()
        if engine is None:
            return

        rec = self._compute_recommendation_state(
            self._cached_status, self._selected_browser,
        )
        pipeline_factory = rec.get("pipeline_factory")
        if pipeline_factory is None:
            return

        browser_name = self._selected_browser
        if browser_name is None:
            for b in (self._cached_status.all_browsers if self._cached_status else []):
                if b.installed:
                    browser_name = b.name
                    break
        if browser_name is None:
            return

        self._rec_fixing = True
        self._rec_fix_btn.configure(state="disabled", text="Fixing\u2026")
        self._rec_retry_btn.pack_forget()
        self._rec_progress_lbl.configure(text="\u23f3 Starting\u2026")

        session_id = engine.run_async(
            browser_name=browser_name,
            extension_dir=_EXTENSION_DIR,
            pipeline=pipeline_factory(),
            progress_callback=lambda s: None,
        )
        self._rec_session_id = session_id
        self._poll_rec_session()

    def _on_rec_retry(self) -> None:
        """Retry the last failed recommendation auto-fix."""
        self._rec_fixing = False
        self._rec_session_id = None
        self._on_rec_fix_now()

    def _poll_rec_session(self) -> None:
        """Poll the recommendation automation session for completion."""
        if not self.winfo_exists():
            return
        if self._rec_session_id is None:
            return

        engine = self._automation_engine
        if engine is None:
            return

        session = engine.get_session(self._rec_session_id)
        if session is None or session.is_terminal:
            result = engine.get_result(self._rec_session_id)
            self._on_rec_finished(result)
            return

        state_name = str(getattr(session, "current_state", "")).split(".")[-1]
        if state_name:
            self._rec_progress_lbl.configure(text=f"\u23f3 {state_name}\u2026")
        self._rec_poll_job = self.after(200, self._poll_rec_session)

    def _on_rec_finished(self, result: Any) -> None:
        """Handle recommendation automation completion."""
        self._rec_session_id = None
        self._rec_fixing = False
        if self.winfo_exists():
            self._rec_fix_btn.configure(state="normal")

        if result is not None and result.success:
            if self.winfo_exists():
                self._rec_progress_lbl.configure(text="\u2714 Done")
                self._rec_retry_btn.pack_forget()
            self._set_message("Auto-fix completed successfully.", "#22c55e")
            self._schedule_delayed(300, self._on_refresh)
        else:
            if self.winfo_exists():
                self._rec_progress_lbl.configure(text="\u2716 Failed")
                self._rec_retry_btn.pack(side="left")
            err = ""
            if result and result.error:
                err = result.error.message
            self._set_message(f"Auto-fix failed: {err}" if err else "Auto-fix failed.", "#ef4444")

    def _cancel_rec_session(self) -> None:
        """Cancel any active recommendation automation session."""
        job = self._rec_poll_job
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self._rec_poll_job = None

        if self._rec_session_id is not None and self._automation_engine is not None:
            try:
                self._automation_engine.cancel(self._rec_session_id)
            except Exception:
                pass
        self._rec_session_id = None
        self._rec_fixing = False
        if self.winfo_exists():
            self._rec_fix_btn.configure(state="normal")
            self._rec_progress_lbl.configure(text="")
            self._rec_retry_btn.pack_forget()
            self._rec_action_frame.pack_forget()

    # ── Browser Filters ─────────────────────────────────────────────────

    def _apply_filter(self, filter_name: str) -> None:
        """Apply the given filter to browser cards without re-detecting."""
        self._active_filter = filter_name
        self._update_filter_bar()
        self._reapply_filter_to_cards()

    def _update_filter_bar(self) -> None:
        """Refresh filter button active states to match _active_filter."""
        for opt, btn in self._filter_btns.items():
            is_active = opt == self._active_filter
            btn.configure(
                fg_color="#4f8ef7" if is_active else "#20232f",
                hover_color="#3a76e8" if is_active else "#2e3347",
                text_color="#ffffff" if is_active else "#8b92a8",
                font=ctk.CTkFont(
                    family="Segoe UI", size=10,
                    weight="bold" if is_active else "normal",
                ),
            )

    def _reapply_filter_to_cards(self) -> None:
        """Show/hide cards based on the active filter and handle empty state."""
        any_visible = False
        for name, card in self._browser_cards.items():
            visible = card.matches_filter(self._active_filter)
            if visible:
                card.frame.pack(fill="x", padx=0, pady=(0, 8))
                any_visible = True
            else:
                card.frame.pack_forget()

        if not any_visible and self._browser_cards:
            if not hasattr(self, "_empty_filter_lbl"):
                self._empty_filter_lbl = ctk.CTkLabel(
                    self._browser_cards_frame,
                    text="No browsers match the selected filter.",
                    font=ctk.CTkFont(family="Segoe UI", size=12),
                    text_color=_CLR_GREY,
                )
            self._empty_filter_lbl.pack(pady=8)
        elif hasattr(self, "_empty_filter_lbl"):
            self._empty_filter_lbl.pack_forget()

    # ── Browser Card Management ────────────────────────────────────────

    def _update_browser_cards(self, status: ExtensionStatus) -> None:
        """Create, update, or remove _BrowserCard widgets to match detected browsers."""
        _ORDER = {"Chrome": 1, "Brave": 2, "Edge": 3}
        detected = sorted(status.all_browsers, key=lambda x: _ORDER.get(x.name, 99))
        detected_names = {b.name for b in detected}

        # Clear selection if that browser disappeared
        if self._selected_browser and self._selected_browser not in detected_names:
            self._selected_browser = None

        # Remove cards for browsers no longer detected
        for name in list(self._browser_cards):
            if name not in detected_names:
                self._browser_cards[name].destroy()
                del self._browser_cards[name]

        # Also hide empty-filter message during rebuild
        if hasattr(self, "_empty_filter_lbl"):
            self._empty_filter_lbl.pack_forget()

        reg_lookup = {r.browser_name: r for r in status.browser_registration}
        callbacks = self._get_card_callbacks()

        for b in detected:
            if b.name not in self._browser_cards:
                card = _BrowserCard(self._browser_cards_frame, b.name, callbacks)
                self._browser_cards[b.name] = card

            running = status.browser_running.get(b.name, False)
            reg_result = reg_lookup.get(b.name)
            procs = status.browser_processes.get(b.name, [])
            self._browser_cards[b.name].update_state(b, reg_result, running, status, processes=procs)
            self._browser_cards[b.name].set_selected(b.name == self._selected_browser)

        # Apply filter (handles packing and empty-filter message)
        self._reapply_filter_to_cards()

        # Show empty message if no browsers detected at all
        if not detected:
            if not hasattr(self, "_empty_browsers_lbl"):
                self._empty_browsers_lbl = ctk.CTkLabel(
                    self._browser_cards_frame,
                    text="No supported Chromium browsers detected.",
                    font=ctk.CTkFont(family="Segoe UI", size=12),
                    text_color=_CLR_ORANGE,
                )
            self._empty_browsers_lbl.pack(pady=8)
        elif hasattr(self, "_empty_browsers_lbl"):
            self._empty_browsers_lbl.pack_forget()

    # ── Lifecycle ───────────────────────────────────────────────────────

    def on_show(self) -> None:
        self._on_refresh()

    def on_hide(self) -> None:
        self._detecting = False
        self._cancel_all_delayed()
        self._cancel_rec_session()
        self._cancel_all_automation()

        if self._verify_dialog is not None:
            try:
                if self._verify_dialog._dialog.winfo_exists():
                    self._verify_dialog._dialog.destroy()
            except Exception:
                pass
            self._verify_dialog = None

        if self._wizard_dialog is not None:
            try:
                self._wizard_dialog._cancel_wizard_sessions()
            except Exception:
                pass
            try:
                if self._wizard_dialog._dialog.winfo_exists():
                    self._wizard_dialog._dialog.destroy()
            except Exception:
                pass
            self._wizard_dialog = None

    def refresh(self, data: dict[str, Any]) -> None:
        pass

    # ── Refresh Logic ───────────────────────────────────────────────────

    def _on_refresh(self) -> None:
        """Run full detection in a background thread, then update UI."""
        if self._detecting:
            return
        self._detecting = True
        self._set_message("Detecting\u2026", "#f59e0b")
        self._refresh_btn.configure(state="disabled")

        def _worker():
            try:
                status = run_full_detection()
            except Exception as exc:
                if self.winfo_exists():
                    self.logger.exception(f"[ExtensionManager] Exception in background detection: {exc}")
                status = ExtensionStatus()

            def _done():
                self._detecting = False
                if not self.winfo_exists():
                    return
                self._apply_status(status)

            try:
                if self.winfo_exists():
                    self.after(0, _done)
                else:
                    self._detecting = False
            except Exception:
                self._detecting = False

        threading.Thread(target=_worker, daemon=True, name="ExtensionDetectWorker").start()

    def _disable_buttons(self) -> None:
        """Disable all action buttons during an operation."""
        if not self.winfo_exists():
            return
        self._refresh_btn.configure(state="disabled")
        self._verify_btn.configure(state="disabled")
        self._wizard_btn.configure(state="disabled")

    def _enable_buttons(self) -> None:
        """Re-enable action buttons after operation."""
        if not self.winfo_exists():
            return
        self._refresh_btn.configure(state="normal")
        self._verify_btn.configure(state="normal")
        self._wizard_btn.configure(state="normal")

    def _on_verify(self) -> None:
        """Run verification and display results in a dedicated dialog."""
        if self._detecting:
            return
        if self._verify_dialog is not None and self._verify_dialog._dialog.winfo_exists():
            self._verify_dialog._dialog.lift()
            self._verify_dialog._dialog.focus_force()
            return

        self._detecting = True
        self._set_message("Verifying installation\u2026", "#f59e0b")
        self._disable_buttons()

        def _worker():
            try:
                status = run_full_detection()
            except Exception as exc:
                if self.winfo_exists():
                    self.logger.exception(f"[ExtensionManager] Exception in background verification: {exc}")
                status = ExtensionStatus()

            def _done():
                self._detecting = False
                if not self.winfo_exists():
                    return
                self._enable_buttons()
                self._apply_status(status)

                if self._verify_dialog is not None and self._verify_dialog._dialog.winfo_exists():
                    self._verify_dialog._dialog.lift()
                    self._verify_dialog._dialog.focus_force()
                    return
                self._verify_dialog = _VerificationDialog(self.winfo_toplevel(), status)

            try:
                if self.winfo_exists():
                    self.after(0, _done)
                else:
                    self._detecting = False
            except Exception:
                self._detecting = False

        threading.Thread(target=_worker, daemon=True, name="ExtensionVerifyWorker").start()

    def _apply_status(self, status: ExtensionStatus) -> None:
        """Update all UI labels and browser cards from a detection result."""
        if not self.winfo_exists():
            return
        old_compat = self._cached_status.compatibility if self._cached_status else ""
        self._cached_status = status
        self._refresh_btn.configure(state="normal")

        any_browser_installed = any(b.installed for b in status.all_browsers)

        # ── Browser Cards ────────────────────────────────────────────────
        self._update_browser_cards(status)

        # ── Extension status ──────────────────────────────────────────────
        ext_text, ext_color = _extension_status_text(status)
        if ext_text == "Healthy":
            ext_display = "\u2714 Healthy"
        elif ext_text in ("Files Present", "Not Installed"):
            ext_display = "\u26a0 Not Installed"
        elif ext_text == "Damaged":
            ext_display = "\u2716 Damaged"
        else:
            ext_display = f"\u26a0 {ext_text}"
        self._ext_status_lbl.configure(text=ext_display, text_color=ext_color)

        # ── Compatibility ─────────────────────────────────────────────────
        compat_color = _compat_color(status.compatibility)

        self._compat_lbl.configure(
            text=_format_compat_label(status.compatibility), text_color=compat_color,
        )

        # ── Extension version ─────────────────────────────────────────────
        self._ext_ver_lbl.configure(
            text=f"v{status.extension_version}" if status.extension_version else "v\u2014",
            text_color="#e8eaf0" if status.extension_version else "#8b92a8",
        )

        # ── Companion version ─────────────────────────────────────────────
        self._comp_ver_lbl.configure(
            text=f"v{status.companion_version}" if status.companion_version else "v\u2014",
            text_color="#e8eaf0" if status.companion_version else "#8b92a8",
        )

        # ── Dynamic Recommendation Card Toggle ────────────────────────────
        if self._selected_browser:
            self._update_recommendation_for_selected()
        else:
            self._refresh_recommendation_from_status(status)

        # ── Badge notification ────────────────────────────────────────────
        badge_color = _compat_color(status.compatibility)
        _notify_badge(badge_color)

        # ── Compatibility change notification ─────────────────────────────
        if (
            old_compat
            and old_compat != ExtensionStatus.COMPATIBLE
            and status.compatibility == ExtensionStatus.COMPATIBLE
        ):
            self._fire_compatibility_notification()

        # Final message
        if not any_browser_installed and not status.folder_exists:
            self._set_message(
                "No Chromium browser detected. Install Chrome, Brave, or Edge.",
                "#f59e0b",
            )
        elif not status.file_status.all_present:
            self._set_message(
                "Extension files incomplete. Run the project setup to restore them.",
                "#ef4444",
            )
        elif status.compatibility == ExtensionStatus.NOT_INSTALLED:
            self._set_message(
                "Extension files are present but not installed in any browser.",
                "#f59e0b",
            )
        elif status.compatibility == ExtensionStatus.COMPATIBLE:
            self._set_message("Extension is healthy and compatible.", "#22c55e")
        elif status.compatibility == ExtensionStatus.MISMATCH:
            self._set_message(
                f"Version mismatch: extension v{status.extension_version}, "
                f"companion v{status.companion_version}.",
                "#f59e0b",
            )
        else:
            self._set_message("")

    def _set_message(self, text: str, color: str = "#8b92a8") -> None:
        if not self.winfo_exists():
            return
        self._msg_lbl.configure(text=text, text_color=color)

    # ── Delayed Callback Lifecycle ────────────────────────────────────────

    def _schedule_delayed(self, delay_ms: int, callback: Callable[[], None]) -> str | None:
        """Schedule a delayed ``after()`` callback, tracking its job ID.

        Returns the job ID so callers can reference it, or ``None``
        if the widget was already destroyed.
        """
        if not self.winfo_exists():
            return None
        job_id = self.after(delay_ms, callback)
        self._delayed_jobs.add(job_id)
        return job_id

    def _cancel_delayed(self, job_id: str) -> None:
        """Cancel a single tracked delayed callback (safe if already fired)."""
        self._delayed_jobs.discard(job_id)
        try:
            self.after_cancel(job_id)
        except Exception:
            pass

    def _cancel_all_delayed(self) -> None:
        """Cancel every tracked delayed callback."""
        for job_id in list(self._delayed_jobs):
            try:
                self.after_cancel(job_id)
            except Exception:
                pass
        self._delayed_jobs.clear()

    def _fire_compatibility_notification(self) -> None:
        """Publish a desktop notification when the extension becomes compatible."""
        try:
            notif = get_notification_manager()
            notif.publish(
                category=CATEGORY_INFO,
                title="MediaForge Extension Ready",
                message="The browser extension is now compatible and ready to use.",
                source=SOURCE_UI,
            )
        except Exception:
            pass

    def _on_show_wizard(self) -> None:
        """Show the Installation Wizard dialog."""
        if self._wizard_dialog is not None and self._wizard_dialog._dialog.winfo_exists():
            self._wizard_dialog._dialog.lift()
            self._wizard_dialog._dialog.focus_force()
            return
        ext_ver = ""
        if self._cached_status:
            ext_ver = self._cached_status.extension_version
        self._wizard_dialog = _InstallationWizard(
            self.winfo_toplevel(), ext_ver
        )


# ---------------------------------------------------------------------------
# Verification Dialog
# ---------------------------------------------------------------------------

class _VerificationDialog:
    """Modal dialog showing detailed installation verification results."""

    def __init__(self, parent: ctk.CTk, status: ExtensionStatus) -> None:
        self._dialog = ctk.CTkToplevel(parent)
        self._dialog.title("Installation Verification")
        self._dialog.transient(parent)
        self._dialog.grab_set()
        self._dialog.resizable(False, False)
        self._dialog.configure(fg_color="#0f1117")

        w, h = 420, 340
        pw, ph = parent.winfo_width(), parent.winfo_height()
        dx, dy = parent.winfo_x(), parent.winfo_y()
        x = dx + (pw - w) // 2
        y = dy + (ph - h) // 2
        self._dialog.geometry(f"{w}x{h}+{x}+{y}")

        self._build_ui(status)
        self._dialog.focus_set()

    def _build_ui(self, status: ExtensionStatus) -> None:
        # Determine overall result
        overall_ready, overall_color = _compute_overall_ready(status)

        ctk.CTkLabel(
            self._dialog,
            text="Installation Verification",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="#e8eaf0",
        ).pack(anchor="w", padx=24, pady=(20, 4))

        # Overall status banner
        banner_color = "#16a34a" if overall_ready == "Yes" else "#922b21"
        banner = ctk.CTkFrame(
            self._dialog,
            fg_color=banner_color,
            corner_radius=8,
        )
        banner.pack(fill="x", padx=24, pady=(0, 12))
        banner_inner = ctk.CTkFrame(banner, fg_color="transparent")
        banner_inner.pack(fill="x", padx=16, pady=10)
        icon_text = "\u2714" if overall_ready == "Yes" else "\u2718"
        ctk.CTkLabel(
            banner_inner,
            text=icon_text,
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#ffffff",
        ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            banner_inner,
            text=f"Overall: {overall_ready}",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#ffffff",
        ).pack(side="left")

        # Check items
        any_running = any(
            status.browser_running.get(b.name, False) for b in status.all_browsers
        )
        checks = [
            ("Browser Installed", any(b.installed for b in status.all_browsers)),
            ("Browser Running", any_running),
            ("Extension Folder", status.folder_exists),
            ("All Files Present", status.file_status.all_present),
            ("Manifest Valid", bool(status.file_status.manifest_data)),
            ("Versions Match", status.compatibility == ExtensionStatus.COMPATIBLE),
        ]

        checks_frame = ctk.CTkFrame(self._dialog, fg_color="transparent")
        checks_frame.pack(fill="x", padx=24, pady=(0, 12))

        for label, passed in checks:
            row = ctk.CTkFrame(checks_frame, fg_color="transparent")
            row.pack(fill="x", pady=(0, 6))
            icon = "\u2714" if passed else "\u2718"
            color = _CLR_GREEN if passed else _CLR_RED
            ctk.CTkLabel(
                row,
                text=f"  {icon}  {label}",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color=color,
            ).pack(side="left")

        ctk.CTkButton(
            self._dialog,
            text="Close",
            width=100,
            height=30,
            corner_radius=8,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._dialog.destroy,
        ).pack(pady=(8, 16))


# ---------------------------------------------------------------------------
# Installation Wizard
# ---------------------------------------------------------------------------

class _InstallationWizard:
    """Eight-step guided installation wizard for browser extension setup.

    Steps:
        0. Welcome — introduction and prerequisites.
        1. Select Browser — choose Chrome, Brave, or Edge.
        2. Launch Browser — auto-launch the selected browser.
        3. Open Extensions Page — navigate to the extensions management page.
        4. Enable Developer Mode — instructions to toggle Developer mode.
        5. Load Extension Folder — instructions to load the unpacked folder.
        6. Verification — auto-verify installation with troubleshooting.
        7. Finish — confirmation and final status.

    The wizard is modal and runs in the calling thread.  Background work
    (browser launch, page open, verification) uses daemon threads with
    results marshalled back to the main thread via ``dialog.after``.
    """

    _TOTAL_STEPS: int = 8

    _STEP_WELCOME: int = 0
    _STEP_SELECT_BROWSER: int = 1
    _STEP_LAUNCH: int = 2
    _STEP_EXTENSIONS_PAGE: int = 3
    _STEP_DEV_MODE: int = 4
    _STEP_LOAD_EXTENSION: int = 5
    _STEP_VERIFICATION: int = 6
    _STEP_FINISH: int = 7

    _BROWSER_CONFIG: dict[str, dict[str, str]] = {
        "Chrome": {
            "icon": "\U0001f310",
            "extensions_url": "chrome://extensions",
        },
        "Brave": {
            "icon": "\U0001f981",
            "extensions_url": "brave://extensions",
        },
        "Edge": {
            "icon": "\U0001f537",
            "extensions_url": "edge://extensions",
        },
    }

    _TROUBLESHOOTING_ITEMS: list[tuple[str, str]] = [
        (
            "Developer Mode Disabled",
            "Go to the extensions page and enable the\n"
            '"Developer mode" toggle in the top-right corner.',
        ),
        (
            "Wrong Folder Selected",
            "Make sure to select the 'extension' folder:\n"
            f"  {_EXTENSION_DIR}",
        ),
        (
            "Browser Was Restarted",
            "If the browser restarted, navigate back to\n"
            "the extensions page and re-enable Developer mode.",
        ),
        (
            "Extension Was Removed",
            "The extension may have been removed.  Re-install\n"
            'it by clicking "Load unpacked" again.',
        ),
        (
            "Profile Mismatch",
            "Ensure you are using the same browser profile\n"
            "where the extension was loaded.",
        ),
    ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, parent: ctk.CTk, extension_version: str) -> None:
        self._parent = parent
        self._extension_version = extension_version
        self._selected_browser: str = "Chrome"
        self._current_step: int = self._STEP_WELCOME

        # Per-session state flags (persist across back/forward)
        self._launched_browser: str | None = None
        self._opened_page_browser: str | None = None
        self._verification_done: bool = False

        # Transient async state — UI flow flags
        self._launching: bool = False
        self._opening_page: bool = False
        self._verifying: bool = False
        self._verification_status: ExtensionStatus | None = None

        # Wizard automation session tracking
        self._wizard_sessions: dict[str, str] = {}
        self._wizard_results: dict[str, Any] = {}
        self._wizard_poll_jobs: dict[str, str] = {}

        self._build_dialog()
        self._show_step(self._STEP_WELCOME)
        self._dialog.focus_set()

    # ------------------------------------------------------------------
    # Dialog shell
    # ------------------------------------------------------------------

    def _build_dialog(self) -> None:
        self._dialog = ctk.CTkToplevel(self._parent)
        self._dialog.title("Installation Wizard")
        self._dialog.transient(self._parent)
        self._dialog.grab_set()
        self._dialog.resizable(False, False)
        self._dialog.configure(fg_color="#0f1117")

        w, h = 560, 640
        pw, ph = self._parent.winfo_width(), self._parent.winfo_height()
        dx, dy = self._parent.winfo_x(), self._parent.winfo_y()
        self._dialog.geometry(f"{w}x{h}+{dx + (pw - w) // 2}+{dy + (ph - h) // 2}")

        # ── Header ──────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self._dialog, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(16, 0))

        self._title_lbl = ctk.CTkLabel(
            hdr,
            text="Installation Wizard",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color="#e8eaf0",
        )
        self._title_lbl.pack(side="left")

        self._step_counter_lbl = ctk.CTkLabel(
            hdr,
            text="Step 1 of 8",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._step_counter_lbl.pack(side="right")

        # ── Progress bars ───────────────────────────────────────────────
        pf = ctk.CTkFrame(self._dialog, fg_color="transparent")
        pf.pack(fill="x", padx=24, pady=(8, 12))
        self._progress_bars: list[ctk.CTkFrame] = []
        for _ in range(self._TOTAL_STEPS):
            bar = ctk.CTkFrame(pf, height=4, corner_radius=2, fg_color="#2e3347")
            bar.pack(side="left", expand=True, fill="x", padx=2)
            self._progress_bars.append(bar)

        # ── Separator ───────────────────────────────────────────────────
        ctk.CTkFrame(self._dialog, height=1, fg_color="#2e3347").pack(fill="x", padx=24)

        # ── Content area (rebuilt per step) ─────────────────────────────
        self._content = ctk.CTkFrame(self._dialog, fg_color="transparent")
        self._content.pack(fill="both", expand=True, padx=24, pady=(12, 0))

        # ── Navigation bar ──────────────────────────────────────────────
        nav = ctk.CTkFrame(self._dialog, fg_color="transparent")
        nav.pack(fill="x", padx=24, pady=(12, 16))

        self._cancel_btn = ctk.CTkButton(
            nav, text="Cancel", width=80, height=34, corner_radius=8,
            fg_color="#20232f", hover_color="#ef4444", text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._on_cancel,
        )
        self._cancel_btn.pack(side="left")

        self._prev_btn = ctk.CTkButton(
            nav, text="\u25c0 Previous", width=100, height=34, corner_radius=8,
            fg_color="#20232f", hover_color="#2e3347", text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._on_prev,
        )
        self._prev_btn.pack(side="left", padx=(8, 0))

        self._next_btn = ctk.CTkButton(
            nav, text="Next \u25b6", width=100, height=34, corner_radius=8,
            fg_color="#4f8ef7", hover_color="#3a76e8", text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_next,
        )
        self._next_btn.pack(side="right")

        self._finish_btn = ctk.CTkButton(
            nav, text="Finish \u2714", width=100, height=34, corner_radius=8,
            fg_color="#22c55e", hover_color="#16a34a", text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_finish,
        )

    # ------------------------------------------------------------------
    # Automation helpers
    # ------------------------------------------------------------------

    def _get_engine(self) -> BrowserAutomationEngine | None:
        """Lazily return the automation engine."""
        try:
            return get_automation_engine()
        except Exception:
            return None

    def _schedule_ui(self, step: int) -> None:
        """Schedule a UI rebuild on the main thread."""
        try:
            if self._dialog.winfo_exists():
                self._dialog.after(0, lambda s=step: self._show_step(s))
        except Exception:
            pass

    def _poll_wizard_session(self, step_key: str, session_id: str) -> None:
        """Poll an automation session for completion."""
        if not self._dialog.winfo_exists():
            return
        engine = self._get_engine()
        if engine is None:
            return
        session = engine.get_session(session_id)
        if session is None or session.is_terminal:
            result = engine.get_result(session_id)
            self._on_wizard_session_finished(step_key, result)
            return
        job = self._dialog.after(
            200, lambda: self._poll_wizard_session(step_key, session_id),
        )
        self._wizard_poll_jobs[step_key] = job

    def _on_wizard_session_finished(self, step_key: str, result: Any) -> None:
        """Handle completion of a wizard automation session."""
        if not self._dialog.winfo_exists():
            return

        self._wizard_sessions.pop(step_key, None)
        self._wizard_results[step_key] = result

        poll_job = self._wizard_poll_jobs.pop(step_key, None)
        if poll_job is not None:
            try:
                self._dialog.after_cancel(poll_job)
            except Exception:
                pass

        if step_key == "launch":
            self._launching = False
            if result and result.success:
                self._launched_browser = self._selected_browser
            self._schedule_ui(self._STEP_LAUNCH)

        elif step_key == "open_page":
            self._opening_page = False
            if result and result.success:
                self._opened_page_browser = self._selected_browser
            else:
                self._opened_page_browser = None
            self._schedule_ui(self._STEP_EXTENSIONS_PAGE)

        elif step_key == "verify":
            self._run_post_verify_detection()

    def _run_post_verify_detection(self) -> None:
        """Run full detection after verify automation completes for detailed UI."""

        def _worker() -> None:
            try:
                self._verification_status = run_full_detection()
            except Exception:
                self._verification_status = ExtensionStatus()
            finally:
                self._verifying = False
                self._verification_done = True
                self._schedule_ui(self._STEP_VERIFICATION)

        threading.Thread(target=_worker, daemon=True, name="WizardPostVerify").start()

    def _cancel_wizard_session(self, step_key: str) -> None:
        """Cancel a specific wizard automation session."""
        poll_job = self._wizard_poll_jobs.pop(step_key, None)
        if poll_job is not None:
            try:
                self._dialog.after_cancel(poll_job)
            except Exception:
                pass

        session_id = self._wizard_sessions.pop(step_key, None)
        if session_id:
            engine = self._get_engine()
            if engine is not None:
                try:
                    engine.cancel(session_id)
                except Exception:
                    pass

    def _cancel_wizard_sessions(self) -> None:
        """Cancel all active wizard automation sessions."""
        for key in list(self._wizard_sessions.keys()):
            self._cancel_wizard_session(key)

    # ------------------------------------------------------------------
    # Step dispatcher
    # ------------------------------------------------------------------

    def _show_step(self, step: int) -> None:
        self._current_step = step

        for child in self._content.winfo_children():
            child.destroy()

        builders: dict[int, Callable[[], None]] = {
            self._STEP_WELCOME: self._build_welcome,
            self._STEP_SELECT_BROWSER: self._build_select_browser,
            self._STEP_LAUNCH: self._build_launch,
            self._STEP_EXTENSIONS_PAGE: self._build_extensions_page,
            self._STEP_DEV_MODE: self._build_dev_mode,
            self._STEP_LOAD_EXTENSION: self._build_load_extension,
            self._STEP_VERIFICATION: self._build_verification,
            self._STEP_FINISH: self._build_finish,
        }
        builders[step]()

        self._step_counter_lbl.configure(text=f"Step {step + 1} of {self._TOTAL_STEPS}")

        for i, bar in enumerate(self._progress_bars):
            if i < step:
                bar.configure(fg_color="#22c55e")
            elif i == step:
                bar.configure(fg_color="#4f8ef7")
            else:
                bar.configure(fg_color="#2e3347")

        self._prev_btn.configure(state="normal" if step > self._STEP_WELCOME else "disabled")

        self._next_btn.pack_forget()
        self._finish_btn.pack_forget()

        if step == self._STEP_FINISH:
            self._finish_btn.pack(side="right")
        elif step in (self._STEP_LAUNCH, self._STEP_EXTENSIONS_PAGE, self._STEP_VERIFICATION):
            pass  # inline Next buttons in content area handle these steps
        else:
            self._next_btn.pack(side="right")

    # ------------------------------------------------------------------
    # Step 0 — Welcome
    # ------------------------------------------------------------------

    def _build_welcome(self) -> None:
        f = self._content

        ctk.CTkLabel(f, text="\U0001f9e9", font=ctk.CTkFont(size=52)).pack(pady=(20, 4))
        ctk.CTkLabel(
            f, text="Welcome to the Installation Wizard",
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            text_color="#e8eaf0",
        ).pack(pady=(0, 4))

        if self._extension_version:
            ctk.CTkLabel(
                f, text=f"Extension v{self._extension_version}",
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color="#8b92a8",
            ).pack(pady=(0, 14))

        ctk.CTkLabel(
            f,
            text="This wizard will guide you through installing\nthe MediaForge browser extension step by step.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
            justify="center",
        ).pack(pady=(0, 14))

        box = ctk.CTkFrame(f, fg_color="#1a1d27", corner_radius=10)
        box.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkLabel(
            box,
            text=(
                "You will need:\n\n"
                "  \u2022  A Chromium-based browser (Chrome, Brave, or Edge)\n"
                "  \u2022  About 2 minutes of your time"
            ),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=14)

    # ------------------------------------------------------------------
    # Step 1 — Select Browser
    # ------------------------------------------------------------------

    def _build_select_browser(self) -> None:
        f = self._content
        ctk.CTkLabel(
            f, text="Select Your Browser",
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            text_color="#e8eaf0",
        ).pack(pady=(16, 4))
        ctk.CTkLabel(
            f,
            text="Choose the browser where you want to install the extension.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
        ).pack(pady=(0, 14))

        for name, cfg in self._BROWSER_CONFIG.items():
            self._make_browser_card(f, name, cfg)

    def _make_browser_card(self, parent: ctk.CTkFrame, name: str, cfg: dict) -> None:
        selected = name == self._selected_browser
        border = "#4f8ef7" if selected else "#2e3347"
        bg = "#1e2233" if selected else "#1a1d27"

        card = ctk.CTkFrame(
            parent, fg_color=bg, border_color=border, border_width=2,
            corner_radius=10,
        )
        card.pack(fill="x", padx=12, pady=4)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=12)

        icon_lbl = ctk.CTkLabel(inner, text=cfg["icon"], font=ctk.CTkFont(size=26))
        icon_lbl.pack(side="left", padx=(0, 12))

        name_lbl = ctk.CTkLabel(
            inner, text=name,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#e8eaf0",
        )
        name_lbl.pack(side="left")

        if selected:
            ctk.CTkLabel(
                inner, text="\u2714 Selected",
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                text_color="#4f8ef7",
            ).pack(side="right")

        for widget in (card, inner, icon_lbl, name_lbl):
            widget.bind("<Button-1>", lambda _e, n=name: self._select_browser(n))

    def _select_browser(self, name: str) -> None:
        if self._selected_browser == name:
            return
        self._cancel_wizard_sessions()
        self._selected_browser = name
        self._launched_browser = None
        self._opened_page_browser = None
        self._verification_done = False
        self._verification_status = None
        self._launching = False
        self._opening_page = False
        self._verifying = False
        self._show_step(self._STEP_SELECT_BROWSER)

    # ------------------------------------------------------------------
    # Step 2 — Launch Browser
    # ------------------------------------------------------------------

    def _build_launch(self) -> None:
        f = self._content
        cfg = self._BROWSER_CONFIG[self._selected_browser]

        ctk.CTkLabel(f, text=cfg["icon"], font=ctk.CTkFont(size=48)).pack(pady=(16, 4))
        ctk.CTkLabel(
            f, text=f"Launching {self._selected_browser}",
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            text_color="#e8eaf0",
        ).pack(pady=(0, 10))

        launch_result = self._wizard_results.get("launch")
        if self._launched_browser == self._selected_browser and launch_result and launch_result.success:
            ctk.CTkLabel(
                f, text=f"\u2714  {self._selected_browser} launched successfully",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color="#22c55e",
            ).pack(pady=(0, 6))
            self._show_next_in_content()
        elif self._launching:
            ctk.CTkLabel(
                f, text=f"Launching {self._selected_browser}\u2026",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color="#f59e0b",
            ).pack(pady=(0, 6))
            ctk.CTkLabel(f, text="\u23f3", font=ctk.CTkFont(size=36)).pack()
        elif launch_result is not None and not launch_result.success:
            ctk.CTkLabel(
                f, text=f"\u2718  Failed to launch {self._selected_browser}",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color="#ef4444",
            ).pack(pady=(0, 6))
            error_msg = launch_result.error_message if launch_result.error else ""
            if error_msg:
                ctk.CTkLabel(
                    f, text=error_msg,
                    font=ctk.CTkFont(family="Segoe UI", size=11),
                    text_color="#8b92a8",
                    wraplength=420,
                ).pack(pady=(0, 10))
            ctk.CTkButton(
                f, text="Retry", width=120, height=30, corner_radius=8,
                fg_color="#4f8ef7", hover_color="#3a76e8", text_color="#ffffff",
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                command=self._retry_launch,
            ).pack(pady=(0, 6))
        else:
            self._do_launch_browser()

    def _do_launch_browser(self) -> None:
        if self._launching:
            return
        self._launching = True

        browser_name = self._selected_browser

        engine = self._get_engine()
        if engine is None:
            self._launching = False
            self._wizard_results["launch"] = AutomationResult(
                success=False,
                error=AutomationError(message="Automation engine not available"),
            )
            self._schedule_ui(self._STEP_LAUNCH)
            return

        session_id = engine.run_async(
            browser_name=browser_name,
            pipeline=create_launch_pipeline(),
        )
        self._wizard_sessions["launch"] = session_id
        self._poll_wizard_session("launch", session_id)
        ctk.CTkLabel(
            self._content, text=f"Launching {self._selected_browser}\u2026",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color="#f59e0b",
        ).pack(pady=(0, 6))
        ctk.CTkLabel(self._content, text="\u23f3", font=ctk.CTkFont(size=36)).pack()

    def _retry_launch(self) -> None:
        self._wizard_results.pop("launch", None)
        self._launched_browser = None
        self._show_step(self._STEP_LAUNCH)

    # ------------------------------------------------------------------
    # Step 3 — Open Extensions Page
    # ------------------------------------------------------------------

    def _build_extensions_page(self) -> None:
        f = self._content
        cfg = self._BROWSER_CONFIG[self._selected_browser]

        ctk.CTkLabel(f, text="\U0001f517", font=ctk.CTkFont(size=48)).pack(pady=(16, 4))
        ctk.CTkLabel(
            f, text="Open Extensions Page",
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            text_color="#e8eaf0",
        ).pack(pady=(0, 10))

        ctk.CTkLabel(
            f,
            text=f"The {self._selected_browser} extensions management page\nwill be opened for you automatically.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
            justify="center",
        ).pack(pady=(0, 14))

        url_box = ctk.CTkFrame(f, fg_color="#1a1d27", corner_radius=8)
        url_box.pack(fill="x", padx=20, pady=(0, 14))
        ctk.CTkLabel(
            url_box, text=cfg["extensions_url"],
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color="#4f8ef7",
        ).pack(padx=16, pady=10)

        page_result = self._wizard_results.get("open_page")
        if self._opened_page_browser == self._selected_browser:
            ctk.CTkLabel(
                f, text="\u2714  Page opened",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color="#22c55e",
            ).pack(pady=(0, 4))
            self._show_next_in_content()
        elif page_result is not None and not page_result.success:
            ctk.CTkLabel(
                f, text="\u2718  Failed to open extensions page",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color="#ef4444",
            ).pack(pady=(0, 6))
            error_msg = page_result.error_message if page_result.error else ""
            if error_msg:
                ctk.CTkLabel(
                    f, text=error_msg,
                    font=ctk.CTkFont(family="Segoe UI", size=11),
                    text_color="#8b92a8",
                    wraplength=420,
                ).pack(pady=(0, 10))
            ctk.CTkButton(
                f, text="Retry", width=120, height=30, corner_radius=8,
                fg_color="#4f8ef7", hover_color="#3a76e8", text_color="#ffffff",
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                command=self._retry_open_page,
            ).pack(pady=(0, 6))
        elif self._opening_page:
            ctk.CTkLabel(
                f, text="Opening page\u2026",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color="#f59e0b",
            ).pack(pady=(0, 4))
        else:
            self._do_open_extensions_page()

    def _do_open_extensions_page(self) -> None:
        if self._opening_page:
            return
        self._opening_page = True
        browser_name = self._selected_browser
        url = self._BROWSER_CONFIG[browser_name]["extensions_url"]

        engine = self._get_engine()
        if engine is None:
            self._opening_page = False
            self._wizard_results["open_page"] = AutomationResult(
                success=False,
                error=AutomationError(message="Automation engine not available"),
            )
            self._schedule_ui(self._STEP_EXTENSIONS_PAGE)
            return

        session_id = engine.run_async(
            browser_name=browser_name,
            target_url=url,
            pipeline=create_open_ext_page_pipeline(),
        )
        self._wizard_sessions["open_page"] = session_id
        self._poll_wizard_session("open_page", session_id)
        ctk.CTkLabel(
            self._content, text="Opening page\u2026",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#f59e0b",
        ).pack(pady=(0, 4))

    def _retry_open_page(self) -> None:
        self._wizard_results.pop("open_page", None)
        self._opened_page_browser = None
        self._show_step(self._STEP_EXTENSIONS_PAGE)

    # ------------------------------------------------------------------
    # Step 4 — Enable Developer Mode
    # ------------------------------------------------------------------

    def _build_dev_mode(self) -> None:
        f = self._content

        ctk.CTkLabel(f, text="\U0001f527", font=ctk.CTkFont(size=48)).pack(pady=(16, 4))
        ctk.CTkLabel(
            f, text="Enable Developer Mode",
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            text_color="#e8eaf0",
        ).pack(pady=(0, 10))

        ctk.CTkLabel(
            f,
            text=(
                f"1.  Look at the top-right corner of the\n"
                f"    {self._selected_browser} extensions page.\n\n"
                f'2.  Find the "Developer mode" toggle switch.\n\n'
                f"3.  Click the toggle to enable it."
            ),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=24, pady=(0, 14))

        note = ctk.CTkFrame(f, fg_color="#241e12", corner_radius=8)
        note.pack(fill="x", padx=20)
        ctk.CTkLabel(
            note,
            text="\u26a0  Developer mode is required to load unpacked extensions.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#f59e0b",
        ).pack(padx=14, pady=10, anchor="w")

    # ------------------------------------------------------------------
    # Step 5 — Load Extension Folder
    # ------------------------------------------------------------------

    def _build_load_extension(self) -> None:
        f = self._content

        ctk.CTkLabel(f, text="\U0001f4c2", font=ctk.CTkFont(size=48)).pack(pady=(16, 4))
        ctk.CTkLabel(
            f, text="Load Extension Folder",
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            text_color="#e8eaf0",
        ).pack(pady=(0, 10))

        ctk.CTkLabel(
            f,
            text=(
                f'1.  Click the "Load unpacked" button in the\n'
                f"    top-left of the extensions page.\n\n"
                f"2.  In the file dialog, navigate to:"
            ),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=24, pady=(0, 4))

        path_box = ctk.CTkFrame(f, fg_color="#1a1d27", corner_radius=8)
        path_box.pack(fill="x", padx=20, pady=(0, 4))
        ctk.CTkLabel(
            path_box, text=_EXTENSION_DIR,
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color="#4f8ef7",
            wraplength=460,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=14, pady=8)

        ctk.CTkLabel(
            f, text='3.  Select this folder and click "Open".',
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=24, pady=(0, 12))

        ctk.CTkButton(
            f, text="Copy Folder Path to Clipboard", width=200, height=30,
            corner_radius=8, fg_color="#20232f", hover_color="#2e3347",
            text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._copy_path,
        ).pack(pady=(0, 6))

    def _copy_path(self) -> None:
        try:
            self._dialog.clipboard_clear()
            self._dialog.clipboard_append(_EXTENSION_DIR)
            self._dialog.update()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Step 6 — Verification
    # ------------------------------------------------------------------

    def _build_verification(self) -> None:
        f = self._content

        ctk.CTkLabel(f, text="\U0001f50d", font=ctk.CTkFont(size=48)).pack(pady=(16, 4))
        ctk.CTkLabel(
            f, text="Verify Installation",
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            text_color="#e8eaf0",
        ).pack(pady=(0, 10))

        if self._verifying:
            ctk.CTkLabel(
                f, text="Verifying installation\u2026",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color="#f59e0b",
            ).pack(pady=(0, 6))
            ctk.CTkLabel(f, text="\u23f3", font=ctk.CTkFont(size=36)).pack()
            return

        if self._verification_status is None:
            self._start_verification()
            ctk.CTkLabel(
                f, text="Starting verification\u2026",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color="#f59e0b",
            ).pack(pady=(0, 6))
            ctk.CTkLabel(f, text="\u23f3", font=ctk.CTkFont(size=36)).pack()
            return

        status = self._verification_status
        self._verification_done = True
        overall_ready, _ = _compute_overall_ready(status)

        banner_bg = "#16a34a" if overall_ready == "Yes" else "#922b21"
        banner = ctk.CTkFrame(f, fg_color=banner_bg, corner_radius=8)
        banner.pack(fill="x", padx=8, pady=(0, 10))
        b_inner = ctk.CTkFrame(banner, fg_color="transparent")
        b_inner.pack(fill="x", padx=16, pady=10)
        icon_text = "\u2714" if overall_ready == "Yes" else "\u2718"
        ctk.CTkLabel(
            b_inner, text=icon_text,
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#ffffff",
        ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            b_inner, text=f"Overall: {overall_ready}",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#ffffff",
        ).pack(side="left")

        any_running = any(
            status.browser_running.get(b.name, False) for b in status.all_browsers
        )
        checks = [
            ("Browser Installed", any(b.installed for b in status.all_browsers)),
            ("Browser Running", any_running),
            ("Extension Folder", status.folder_exists),
            ("All Files Present", status.file_status.all_present),
            ("Manifest Valid", bool(status.file_status.manifest_data)),
            ("Versions Match", status.compatibility == ExtensionStatus.COMPATIBLE),
        ]

        cf = ctk.CTkFrame(f, fg_color="transparent")
        cf.pack(fill="x", padx=8, pady=(0, 10))
        for label, passed in checks:
            row = ctk.CTkFrame(cf, fg_color="transparent")
            row.pack(fill="x", pady=2)
            icon = "\u2714" if passed else "\u2718"
            color = _CLR_GREEN if passed else _CLR_RED
            ctk.CTkLabel(
                row, text=f"  {icon}  {label}",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color=color,
            ).pack(side="left")

        if overall_ready != "Yes":
            self._show_troubleshooting(f)

        btn_row = ctk.CTkFrame(f, fg_color="transparent")
        btn_row.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(
            btn_row, text="Re-verify", width=100, height=30, corner_radius=8,
            fg_color="#20232f", hover_color="#2e3347", text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._retry_verification,
        ).pack(side="left")
        self._show_next_in_content(btn_row)

    def _start_verification(self) -> None:
        if self._verifying:
            return
        self._verifying = True

        engine = self._get_engine()
        if engine is None:
            self._run_fallback_detection()
            return

        session_id = engine.run_async(
            browser_name=self._selected_browser,
            extension_dir=_EXTENSION_DIR,
            pipeline=create_verify_pipeline(),
        )
        self._wizard_sessions["verify"] = session_id
        self._poll_wizard_session("verify", session_id)

    def _run_fallback_detection(self) -> None:
        """Run full detection directly when automation engine is unavailable."""

        def _worker() -> None:
            try:
                self._verification_status = run_full_detection()
            except Exception:
                self._verification_status = ExtensionStatus()
            finally:
                self._verifying = False
                self._verification_done = True
                self._schedule_ui(self._STEP_VERIFICATION)

        threading.Thread(target=_worker, daemon=True, name="WizardFallbackDetect").start()

    def _retry_verification(self) -> None:
        self._verification_done = False
        self._verification_status = None
        self._wizard_results.pop("verify", None)
        self._show_step(self._STEP_VERIFICATION)

    def _show_troubleshooting(self, parent: ctk.CTkFrame) -> None:
        ctk.CTkLabel(
            parent, text="\u26a0  Troubleshooting",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#f59e0b",
        ).pack(anchor="w", padx=8, pady=(6, 4))

        for title, desc in self._TROUBLESHOOTING_ITEMS:
            tip = ctk.CTkFrame(parent, fg_color="#1e2233", corner_radius=6)
            tip.pack(fill="x", padx=8, pady=2)
            inner = ctk.CTkFrame(tip, fg_color="transparent")
            inner.pack(fill="x", padx=10, pady=6)
            ctk.CTkLabel(
                inner, text=f"\u2022 {title}",
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                text_color="#e8eaf0",
            ).pack(anchor="w")
            ctk.CTkLabel(
                inner, text=desc,
                font=ctk.CTkFont(family="Segoe UI", size=10),
                text_color="#8b92a8",
                wraplength=440,
                justify="left",
                anchor="w",
            ).pack(anchor="w")

    # ------------------------------------------------------------------
    # Step 7 — Finish
    # ------------------------------------------------------------------

    def _build_finish(self) -> None:
        f = self._content

        ctk.CTkLabel(f, text="\u2705", font=ctk.CTkFont(size=52)).pack(pady=(20, 4))
        ctk.CTkLabel(
            f, text="Installation Complete",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color="#22c55e",
        ).pack(pady=(0, 6))

        ctk.CTkLabel(
            f,
            text="The MediaForge extension is ready to use.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
        ).pack(pady=(0, 14))

        info_box = ctk.CTkFrame(f, fg_color="#1a1d27", corner_radius=10)
        info_box.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(
            info_box,
            text=(
                "A floating MediaForge button will appear below\n"
                "YouTube video titles when the extension is active.\n\n"
                "If the button does not appear, reload the YouTube page\n"
                "or restart the browser."
            ),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=12)

        self._finish_status_lbl = ctk.CTkLabel(
            f, text="Running final check\u2026",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#f59e0b",
        )
        self._finish_status_lbl.pack(pady=(0, 6))

        self._run_final_check()

    def _run_final_check(self) -> None:
        def _worker() -> None:
            try:
                status = run_full_detection()
            except Exception:
                status = None
            try:
                if self._dialog.winfo_exists():
                    self._dialog.after(0, lambda: self._apply_final_status(status))
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True, name="WizardFinalCheck").start()

    def _apply_final_status(self, status: ExtensionStatus | None) -> None:
        if not self._dialog.winfo_exists() or status is None:
            return
        overall_ready, _ = _compute_overall_ready(status)
        if overall_ready == "Yes":
            self._finish_status_lbl.configure(
                text="\u2714  Extension is ready to use!",
                text_color="#22c55e",
            )
        else:
            self._finish_status_lbl.configure(
                text="\u26a0  Extension may need attention \u2014 run Verify on the main page.",
                text_color="#f59e0b",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _show_next_in_content(self, after_widget: ctk.CTkFrame | None = None) -> None:
        """Place a 'Next' button inside the content area for inline flow."""
        target = after_widget or self._content
        ctk.CTkButton(
            target, text="Next \u25b6", width=100, height=30, corner_radius=8,
            fg_color="#4f8ef7", hover_color="#3a76e8", text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_next,
        ).pack(pady=(6, 0))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_next(self) -> None:
        if self._current_step < self._TOTAL_STEPS - 1:
            self._show_step(self._current_step + 1)

    def _on_prev(self) -> None:
        if self._current_step > self._STEP_WELCOME:
            self._show_step(self._current_step - 1)

    def _on_cancel(self) -> None:
        self._cancel_wizard_sessions()
        self._verifying = False
        self._launching = False
        self._opening_page = False
        self._dialog.destroy()

    def _on_finish(self) -> None:
        self._cancel_wizard_sessions()
        self._verifying = False
        self._launching = False
        self._opening_page = False
        self._dialog.destroy()
