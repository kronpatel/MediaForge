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
import webbrowser
from typing import TYPE_CHECKING, Any, Callable

import customtkinter as ctk

from base_page import BasePage
from notifications import (
    CATEGORY_INFO,
    SOURCE_UI,
    get_notification_manager,
)

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

_REQUIRED_EXTENSION_FILES = (
    "manifest.json",
    "icon.png",
    "background.js",
    "content.js",
    "settings.html",
)

# Common Chrome executable locations on Windows (order = priority)
_CHROME_SEARCH_PATHS = [
    os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
]

# Status colors
_CLR_GREEN = "#22c55e"
_CLR_ORANGE = "#f59e0b"
_CLR_RED = "#ef4444"
_CLR_GREY = "#8b92a8"


# ---------------------------------------------------------------------------
# Chrome Detection
# ---------------------------------------------------------------------------

class ChromeInfo:
    """Immutable snapshot of detected Chrome installation state."""

    __slots__ = ("installed", "path", "version")

    def __init__(self, installed: bool, path: str, version: str) -> None:
        self.installed = installed
        self.path = path
        self.version = version


def detect_chrome() -> ChromeInfo:
    """Detect Google Chrome installation on the system.

    Returns a ChromeInfo with installed=True if found, along with
    the executable path and version string when available.
    Degrades gracefully on any error.
    """
    if sys.platform != "win32":
        return ChromeInfo(False, "", "")

    for candidate in _CHROME_SEARCH_PATHS:
        try:
            if candidate and os.path.isfile(candidate):
                version = _read_chrome_version(candidate)
                return ChromeInfo(True, candidate, version)
        except Exception:
            continue

    return ChromeInfo(False, "", "")


def _read_chrome_version(exe_path: str) -> str:
    """Best-effort Chrome version extraction from the executable.

    Uses Windows file version info when available, falling back to
    directory inspection. Never raises.
    """
    try:
        import ctypes
        from ctypes import wintypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return ""

        data = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(exe_path, 0, size, data):
            return ""

        p_fixed = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(
            data, "\\", ctypes.byref(p_fixed), ctypes.byref(length)
        ):
            return ""

        # Read the file version DWORDs from fixed info block
        buf = ctypes.cast(p_fixed, ctypes.POINTER(ctypes.c_uint32))
        file_version_ms = buf[2]
        file_version_ls = buf[3]

        major = file_version_ms >> 16
        minor = file_version_ms & 0xFFFF
        build = file_version_ls >> 16
        patch = file_version_ls & 0xFFFF

        if major == 0 and minor == 0 and build == 0:
            return ""
        return f"{major}.{minor}.{build}.{patch}"
    except Exception:
        return ""


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

    Returns an ExtensionFileStatus. Never raises.
    """
    missing: list[str] = []
    manifest_data: dict[str, Any] = {}

    if not os.path.isdir(_EXTENSION_DIR):
        return ExtensionFileStatus(False, list(_REQUIRED_EXTENSION_FILES), manifest_data)

    for filename in _REQUIRED_EXTENSION_FILES:
        filepath = os.path.join(_EXTENSION_DIR, filename)
        if not os.path.isfile(filepath):
            missing.append(filename)

    # Attempt to read and parse manifest.json
    if os.path.isfile(_MANIFEST_PATH):
        try:
            with open(_MANIFEST_PATH, "r", encoding="utf-8") as fh:
                parsed = json.load(fh)
            if isinstance(parsed, dict):
                manifest_data = parsed
            else:
                missing.append("manifest.json")
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            if "manifest.json" not in missing:
                missing.append("manifest.json")

    return ExtensionFileStatus(len(missing) == 0, missing, manifest_data)


# ---------------------------------------------------------------------------
# Combined Detection Result
# ---------------------------------------------------------------------------

class ExtensionStatus:
    """Aggregated detection result for the entire extension ecosystem."""

    __slots__ = (
        "chrome", "file_status", "extension_version", "companion_version",
        "compatibility", "folder_exists",
    )

    # Compatibility states
    COMPATIBLE = "Compatible"
    MISMATCH = "Version Mismatch"
    MISSING = "Extension Missing"
    UNKNOWN = "Unknown"

    def __init__(self) -> None:
        self.chrome = ChromeInfo(False, "", "")
        self.file_status = ExtensionFileStatus(False, [], {})
        self.extension_version = ""
        self.companion_version = ""
        self.compatibility = self.UNKNOWN
        self.folder_exists = False


def run_full_detection() -> ExtensionStatus:
    """Execute all detection layers and return a unified status object.

    Every detection step is independently guarded against exceptions
    to ensure the UI never crashes regardless of system state.
    """
    status = ExtensionStatus()

    # 1. Chrome detection
    try:
        status.chrome = detect_chrome()
    except Exception:
        status.chrome = ChromeInfo(False, "", "")

    # 2. Extension file detection
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

    # 6. Compatibility
    status.compatibility = _compute_compatibility(status)

    return status


def _compute_compatibility(status: ExtensionStatus) -> str:
    """Derive the compatibility label from the detection results."""
    if not status.folder_exists:
        return ExtensionStatus.MISSING
    if not status.extension_version:
        return ExtensionStatus.MISSING
    if not status.file_status.all_present:
        return ExtensionStatus.MISSING
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
    return _CLR_GREY


def _chrome_status_text(info: ChromeInfo) -> tuple[str, str]:
    """Return (label, color) for Chrome detection."""
    if info.installed:
        label = info.version if info.version else "Installed"
        return label, _CLR_GREEN
    return "Not Installed", _CLR_RED


def _extension_status_text(file_status: ExtensionFileStatus) -> tuple[str, str]:
    """Return (label, color) for extension file integrity."""
    if file_status.all_present:
        return "Healthy", _CLR_GREEN
    if not file_status.missing_files:
        return "Unknown", _CLR_GREY
    return "Damaged", _CLR_RED


# ---------------------------------------------------------------------------
# Installation Assistant helpers
# ---------------------------------------------------------------------------

def _compute_overall_ready(status: ExtensionStatus) -> tuple[str, str]:
    """Return (label, color) for the Overall Ready row."""
    if not status.chrome.installed:
        return "No", _CLR_RED
    if not status.folder_exists:
        return "No", _CLR_RED
    if not status.file_status.all_present:
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
        self._previous_compatibility: str = ""
        self._detecting: bool = False
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
        ).pack(anchor="w", padx=20, pady=(0, 16))

        # ── Status Card ────────────────────────────────────────────────────
        self._status_card = ctk.CTkFrame(
            self,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        self._status_card.pack(fill="x", padx=20, pady=(0, 12))

        # Chrome Status
        self._chrome_row = self._make_status_row(
            self._status_card, "Chrome Status", "Detecting\u2026", "#f59e0b"
        )
        self._chrome_lbl = self._chrome_row[1]

        # Extension Status
        self._ext_status_row = self._make_status_row(
            self._status_card, "Extension Status", "Detecting\u2026", "#f59e0b"
        )
        self._ext_status_lbl = self._ext_status_row[1]

        # Compatibility
        self._compat_row = self._make_status_row(
            self._status_card, "Compatibility", "Detecting\u2026", "#f59e0b"
        )
        self._compat_lbl = self._compat_row[1]

        # Extension Version
        self._ext_ver_row = self._make_status_row(
            self._status_card, "Extension Version", "v\u2014", "#8b92a8"
        )
        self._ext_ver_lbl = self._ext_ver_row[1]

        # Companion Version
        self._comp_ver_row = self._make_status_row(
            self._status_card, "Companion Version", "v\u2014", "#8b92a8"
        )
        self._comp_ver_lbl = self._comp_ver_row[1]

        # ── Details card ───────────────────────────────────────────────────
        details_card = ctk.CTkFrame(
            self,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        details_card.pack(fill="x", padx=20, pady=(0, 12))

        # Chrome Path
        row_chrome_path = ctk.CTkFrame(details_card, fg_color="transparent")
        row_chrome_path.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(
            row_chrome_path,
            text="Chrome Path",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        ).pack(side="left")
        self._chrome_path_lbl = ctk.CTkLabel(
            row_chrome_path,
            text="\u2014",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#4f8ef7",
            wraplength=400,
            anchor="e",
            justify="right",
        )
        self._chrome_path_lbl.pack(side="right")

        sep1 = ctk.CTkFrame(details_card, height=1, fg_color="#2e3347")
        sep1.pack(fill="x", padx=16)

        # Extension Folder
        row_folder = ctk.CTkFrame(details_card, fg_color="transparent")
        row_folder.pack(fill="x", padx=16, pady=(4, 4))
        ctk.CTkLabel(
            row_folder,
            text="Extension Folder",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        ).pack(side="left")
        self._folder_lbl = ctk.CTkLabel(
            row_folder,
            text=_EXTENSION_DIR,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#4f8ef7",
            wraplength=400,
            anchor="e",
            justify="right",
        )
        self._folder_lbl.pack(side="right")

        sep2 = ctk.CTkFrame(details_card, height=1, fg_color="#2e3347")
        sep2.pack(fill="x", padx=16)

        # Missing Files
        row_missing = ctk.CTkFrame(details_card, fg_color="transparent")
        row_missing.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkLabel(
            row_missing,
            text="Missing Files",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        ).pack(side="left")
        self._missing_lbl = ctk.CTkLabel(
            row_missing,
            text="None",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#22c55e",
            anchor="e",
        )
        self._missing_lbl.pack(side="right")

        # ── Installation Assistant Card ───────────────────────────────────
        self._assistant_card = ctk.CTkFrame(
            self,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        self._assistant_card.pack(fill="x", padx=20, pady=(0, 12))

        ctk.CTkLabel(
            self._assistant_card,
            text="Installation Assistant",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#8b92a8",
        ).pack(anchor="w", padx=16, pady=(12, 0))

        # Chrome Installed
        self._asst_chrome_row = self._make_status_row(
            self._assistant_card, "Chrome Installed", "Detecting\u2026", "#f59e0b"
        )
        self._asst_chrome_lbl = self._asst_chrome_row[1]

        _sep_a1 = ctk.CTkFrame(self._assistant_card, height=1, fg_color="#2e3347")
        _sep_a1.pack(fill="x", padx=16)

        # Extension Folder Found
        self._asst_folder_row = self._make_status_row(
            self._assistant_card, "Extension Folder", "Detecting\u2026", "#f59e0b"
        )
        self._asst_folder_lbl = self._asst_folder_row[1]

        _sep_a2 = ctk.CTkFrame(self._assistant_card, height=1, fg_color="#2e3347")
        _sep_a2.pack(fill="x", padx=16)

        # Manifest Valid
        self._asst_manifest_row = self._make_status_row(
            self._assistant_card, "Manifest Valid", "Detecting\u2026", "#f59e0b"
        )
        self._asst_manifest_lbl = self._asst_manifest_row[1]

        _sep_a3 = ctk.CTkFrame(self._assistant_card, height=1, fg_color="#2e3347")
        _sep_a3.pack(fill="x", padx=16)

        # Compatibility
        self._asst_compat_row = self._make_status_row(
            self._assistant_card, "Compatibility", "Detecting\u2026", "#f59e0b"
        )
        self._asst_compat_lbl = self._asst_compat_row[1]

        _sep_a4 = ctk.CTkFrame(self._assistant_card, height=1, fg_color="#2e3347")
        _sep_a4.pack(fill="x", padx=16)

        # Overall Ready
        self._asst_ready_row = self._make_status_row(
            self._assistant_card, "Overall Ready", "Detecting\u2026", "#f59e0b"
        )
        self._asst_ready_lbl = self._asst_ready_row[1]

        # Spacer at bottom of assistant card
        ctk.CTkFrame(self._assistant_card, height=6, fg_color="transparent").pack()

        # ── Action Buttons (row 1) ────────────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 8))

        self._open_folder_btn = ctk.CTkButton(
            btn_frame,
            text="Open Extension Folder",
            width=170,
            height=34,
            corner_radius=8,
            fg_color="#4f8ef7",
            hover_color="#3a76e8",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_open_folder,
        )
        self._open_folder_btn.pack(side="left", padx=(0, 8))

        self._open_ext_btn = ctk.CTkButton(
            btn_frame,
            text="Open Chrome Extensions",
            width=190,
            height=34,
            corner_radius=8,
            fg_color="#f59e0b",
            hover_color="#d97706",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_open_extensions_page,
        )
        self._open_ext_btn.pack(side="left", padx=(0, 8))

        self._copy_path_btn = ctk.CTkButton(
            btn_frame,
            text="Copy Extension Folder",
            width=160,
            height=34,
            corner_radius=8,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_copy_path,
        )
        self._copy_path_btn.pack(side="left")

        # ── Action Buttons (row 2) ────────────────────────────────────────
        btn_frame2 = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame2.pack(fill="x", padx=20, pady=(0, 8))

        self._refresh_btn = ctk.CTkButton(
            btn_frame2,
            text="Refresh Status",
            width=130,
            height=34,
            corner_radius=8,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_refresh,
        )
        self._refresh_btn.pack(side="left", padx=(0, 8))

        self._verify_btn = ctk.CTkButton(
            btn_frame2,
            text="Verify Installation",
            width=150,
            height=34,
            corner_radius=8,
            fg_color="#8b5cf6",
            hover_color="#7c3aed",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_verify,
        )
        self._verify_btn.pack(side="left", padx=(0, 8))

        self._wizard_btn = ctk.CTkButton(
            btn_frame2,
            text="Installation Wizard",
            width=160,
            height=34,
            corner_radius=8,
            fg_color="#22c55e",
            hover_color="#16a34a",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_show_wizard,
        )
        self._wizard_btn.pack(side="left")

        # ── Status message ─────────────────────────────────────────────────
        self._msg_lbl = ctk.CTkLabel(
            self,
            text="",
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

    # ── Lifecycle ───────────────────────────────────────────────────────

    def on_show(self) -> None:
        self._on_refresh()

    def on_hide(self) -> None:
        pass

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
            except Exception:
                status = ExtensionStatus()

            def _done():
                self._detecting = False
                self._apply_status(status)

            try:
                self.after(0, _done)
            except Exception:
                self._detecting = False

        threading.Thread(target=_worker, daemon=True, name="ExtensionDetectWorker").start()

    def _on_verify(self) -> None:
        """Run verification and display results in a dedicated dialog."""
        if self._detecting:
            return
        # Prevent duplicate verification dialogs if one is already active in memory
        if (
            hasattr(self, "_verify_dialog")
            and self._verify_dialog
            and self._verify_dialog._dialog.winfo_exists()
        ):
            self._verify_dialog._dialog.lift()
            self._verify_dialog._dialog.focus_force()
            return

        self._detecting = True
        self._set_message("Verifying installation\u2026", "#f59e0b")
        self._verify_btn.configure(state="disabled")
        self._refresh_btn.configure(state="disabled")

        def _worker():
            try:
                status = run_full_detection()
            except Exception:
                status = ExtensionStatus()

            def _done():
                self._detecting = False
                self._verify_btn.configure(state="normal")
                self._refresh_btn.configure(state="normal")
                self._apply_status(status)
                
                # Double-check before creating a new dialog instance
                if (
                    hasattr(self, "_verify_dialog")
                    and self._verify_dialog
                    and self._verify_dialog._dialog.winfo_exists()
                ):
                    self._verify_dialog._dialog.lift()
                    self._verify_dialog._dialog.focus_force()
                    return
                self._verify_dialog = _VerificationDialog(self.winfo_toplevel(), status)

            try:
                self.after(0, _done)
            except Exception:
                self._detecting = False

        threading.Thread(target=_worker, daemon=True, name="ExtensionVerifyWorker").start()

    def _apply_status(self, status: ExtensionStatus) -> None:
        """Update all UI labels from a completed detection result."""
        old_compat = self._cached_status.compatibility if self._cached_status else ""
        self._cached_status = status
        self._refresh_btn.configure(state="normal")

        # Chrome status
        chrome_text, chrome_color = _chrome_status_text(status.chrome)
        self._chrome_lbl.configure(text=chrome_text, text_color=chrome_color)

        # Extension status
        ext_text, ext_color = _extension_status_text(status.file_status)
        self._ext_status_lbl.configure(text=ext_text, text_color=ext_color)

        # Compatibility
        compat_color = _compat_color(status.compatibility)
        self._compat_lbl.configure(text=status.compatibility, text_color=compat_color)

        # Extension version
        self._ext_ver_lbl.configure(
            text=f"v{status.extension_version}" if status.extension_version else "v\u2014",
            text_color="#e8eaf0" if status.extension_version else "#8b92a8",
        )

        # Companion version
        self._comp_ver_lbl.configure(
            text=f"v{status.companion_version}" if status.companion_version else "v\u2014",
            text_color="#e8eaf0" if status.companion_version else "#8b92a8",
        )

        # Chrome path
        self._chrome_path_lbl.configure(
            text=status.chrome.path if status.chrome.installed else "Not found",
            text_color="#4f8ef7" if status.chrome.installed else "#ef4444",
        )

        # Extension folder
        self._folder_lbl.configure(
            text=_EXTENSION_DIR,
            text_color="#4f8ef7",
        )

        # Missing files
        missing = status.file_status.missing_files
        if not status.folder_exists:
            self._missing_lbl.configure(text="Folder missing", text_color="#ef4444")
        elif not missing:
            self._missing_lbl.configure(text="None", text_color="#22c55e")
        else:
            self._missing_lbl.configure(
                text=", ".join(missing), text_color="#ef4444", wraplength=400
            )

        # ── Installation Assistant rows ───────────────────────────────────
        # Chrome Installed
        if status.chrome.installed:
            self._asst_chrome_lbl.configure(text="Yes", text_color=_CLR_GREEN)
        else:
            self._asst_chrome_lbl.configure(text="No", text_color=_CLR_RED)

        # Extension Folder Found
        if status.folder_exists:
            self._asst_folder_lbl.configure(text="Yes", text_color=_CLR_GREEN)
        else:
            self._asst_folder_lbl.configure(text="No", text_color=_CLR_RED)

        # Manifest Valid
        if status.file_status.all_present:
            self._asst_manifest_lbl.configure(text="Yes", text_color=_CLR_GREEN)
        elif status.folder_exists and not status.file_status.missing_files:
            self._asst_manifest_lbl.configure(text="Yes", text_color=_CLR_GREEN)
        else:
            self._asst_manifest_lbl.configure(text="No", text_color=_CLR_RED)

        # Compatibility (assistant card)
        self._asst_compat_lbl.configure(
            text=status.compatibility, text_color=compat_color
        )

        # Overall Ready
        ready_text, ready_color = _compute_overall_ready(status)
        self._asst_ready_lbl.configure(text=ready_text, text_color=ready_color)

        # ── Badge notification ────────────────────────────────────────────
        badge_color = _badge_color_for_compat(status.compatibility)
        _notify_badge(badge_color)

        # ── Compatibility change notification ─────────────────────────────
        if (
            old_compat
            and old_compat != ExtensionStatus.COMPATIBLE
            and status.compatibility == ExtensionStatus.COMPATIBLE
        ):
            self._fire_compatibility_notification()

        # Final message
        if not status.chrome.installed:
            self._set_message(
                "Chrome not detected. Install Chrome to use the extension.", "#f59e0b"
            )
        elif not status.file_status.all_present:
            self._set_message(
                "Extension files incomplete. Run the project setup to restore them.", "#ef4444"
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
        self._msg_lbl.configure(text=text, text_color=color)

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

    # ── Button Handlers ─────────────────────────────────────────────────

    def _on_open_folder(self) -> None:
        """Open the extension folder in the system file explorer."""
        if not os.path.isdir(_EXTENSION_DIR):
            self._set_message("Extension folder not found.", "#ef4444")
            return
        try:
            if sys.platform == "win32":
                os.startfile(_EXTENSION_DIR)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", _EXTENSION_DIR])
            else:
                subprocess.Popen(["xdg-open", _EXTENSION_DIR])
            self._set_message("Opened extension folder.")
        except Exception as exc:
            self._set_message(f"Failed to open folder: {exc}", "#ef4444")

    def _on_open_extensions_page(self) -> None:
        """Open chrome://extensions in the default browser."""
        try:
            webbrowser.open("chrome://extensions")
            self._set_message("Opened chrome://extensions in browser.")
        except Exception as exc:
            self._set_message(f"Failed to open browser: {exc}", "#ef4444")

    def _on_copy_path(self) -> None:
        """Copy the extension folder path to the clipboard."""
        try:
            self.clipboard_clear()
            self.clipboard_append(_EXTENSION_DIR)
            self.update()
            self._set_message("Extension folder path copied to clipboard.")
        except Exception as exc:
            self._set_message(f"Failed to copy path: {exc}", "#ef4444")

    def _on_show_wizard(self) -> None:
        """Show the Installation Wizard dialog."""
        if (
            hasattr(self, "_wizard_dialog")
            and self._wizard_dialog
            and self._wizard_dialog._dialog.winfo_exists()
        ):
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
# Badge color helper
# ---------------------------------------------------------------------------

def _badge_color_for_compat(compat: str) -> str:
    """Map a compatibility string to a sidebar badge color."""
    if compat == ExtensionStatus.COMPATIBLE:
        return _CLR_GREEN
    if compat == ExtensionStatus.MISMATCH:
        return _CLR_ORANGE
    if compat == ExtensionStatus.MISSING:
        return _CLR_RED
    return _CLR_GREY


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
        checks = [
            ("Chrome Installed", status.chrome.installed),
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
    """Step-by-step interactive installation wizard dialog."""

    def __init__(self, parent: ctk.CTk, extension_version: str) -> None:
        self._dialog = ctk.CTkToplevel(parent)
        self._dialog.title("Installation Wizard")
        self._dialog.transient(parent)
        self._dialog.grab_set()
        self._dialog.resizable(False, False)
        self._dialog.configure(fg_color="#0f1117")

        w, h = 500, 560
        pw, ph = parent.winfo_width(), parent.winfo_height()
        dx, dy = parent.winfo_x(), parent.winfo_y()
        x = dx + (pw - w) // 2
        y = dy + (ph - h) // 2
        self._dialog.geometry(f"{w}x{h}+{x}+{y}")

        self._current_step = 0
        self._steps = self._define_steps()
        self._step_labels: list[ctk.CTkLabel] = []
        self._step_descs: list[ctk.CTkLabel] = []
        self._step_checks: list[ctk.CTkLabel] = []
        self._step_frames: list[ctk.CTkFrame] = []

        self._build_ui(extension_version)
        self._update_step_ui()
        self._dialog.focus_set()

    def _define_steps(self) -> list[dict[str, str]]:
        return [
            {
                "title": "Step 1: Open Chrome",
                "desc": (
                    "Launch Google Chrome or any Chromium-based browser "
                    "(Edge, Brave, Opera, etc.)."
                ),
                "action_label": "Open Chrome",
            },
            {
                "title": "Step 2: Enable Developer Mode",
                "desc": (
                    'Navigate to chrome://extensions in the address bar.\n'
                    'Enable "Developer mode" using the toggle switch in the\n'
                    "top-right corner of the extensions page."
                ),
                "action_label": "Open chrome://extensions",
            },
            {
                "title": "Step 3: Load Unpacked",
                "desc": (
                    'Click the "Load unpacked" button in the top-left corner\n'
                    "of the extensions page."
                ),
                "action_label": "Open Extension Folder",
            },
            {
                "title": "Step 4: Select Extension Folder",
                "desc": (
                    f"In the file dialog, navigate to:\n\n"
                    f"  {_EXTENSION_DIR}\n\n"
                    "Select this folder and click OK."
                ),
                "action_label": "Copy Folder Path",
            },
            {
                "title": "Step 5: Verify Installation",
                "desc": (
                    "Verify that MediaForge appears in the extensions list\n"
                    "with no error messages. The extension icon should be\n"
                    "visible when you visit YouTube."
                ),
                "action_label": "Open Chrome Extensions",
            },
            {
                "title": "Done",
                "desc": (
                    "Installation complete! A floating MediaForge button\n"
                    "will appear below YouTube video titles.\n\n"
                    "If the button does not appear, reload the YouTube page\n"
                    "or restart Chrome."
                ),
                "action_label": "",
            },
        ]

    def _build_ui(self, ext_version: str) -> None:
        # Header
        ctk.CTkLabel(
            self._dialog,
            text="Installation Wizard",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="#e8eaf0",
        ).pack(anchor="w", padx=24, pady=(20, 2))

        ver_text = f"Extension v{ext_version}" if ext_version else "Extension version unknown"
        ctk.CTkLabel(
            self._dialog,
            text=ver_text,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        ).pack(anchor="w", padx=24, pady=(0, 10))

        # Progress indicator
        progress_frame = ctk.CTkFrame(self._dialog, fg_color="transparent")
        progress_frame.pack(fill="x", padx=24, pady=(0, 10))

        self._progress_bars: list[ctk.CTkFrame] = []
        total = len(self._steps)
        for i in range(total):
            bar = ctk.CTkFrame(
                progress_frame,
                height=4,
                corner_radius=2,
                fg_color="#2e3347",
            )
            bar.pack(side="left", expand=True, fill="x", padx=2)
            self._progress_bars.append(bar)

        # Steps container
        self._steps_container = ctk.CTkFrame(self._dialog, fg_color="transparent")
        self._steps_container.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        for idx, step in enumerate(self._steps):
            frame = ctk.CTkFrame(self._steps_container, fg_color="transparent")

            # Step number + title
            title_row = ctk.CTkFrame(frame, fg_color="transparent")
            title_row.pack(fill="x", pady=(0, 4))
            check_lbl = ctk.CTkLabel(
                title_row,
                text="\u25CB",
                font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
                text_color="#8b92a8",
                width=24,
            )
            check_lbl.pack(side="left")
            title_lbl = ctk.CTkLabel(
                title_row,
                text=step["title"],
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                text_color="#e8eaf0",
            )
            title_lbl.pack(side="left")

            # Description
            desc_lbl = ctk.CTkLabel(
                frame,
                text=step["desc"],
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color="#8b92a8",
                wraplength=420,
                justify="left",
                anchor="w",
            )
            desc_lbl.pack(fill="x", pady=(0, 6))

            self._step_frames.append(frame)
            self._step_checks.append(check_lbl)
            self._step_labels.append(title_lbl)
            self._step_descs.append(desc_lbl)

        # Action button
        self._action_btn = ctk.CTkButton(
            self._dialog,
            text="",
            width=200,
            height=34,
            corner_radius=8,
            fg_color="#4f8ef7",
            hover_color="#3a76e8",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_action,
        )
        self._action_btn.pack(pady=(0, 6))

        # Navigation row
        nav_frame = ctk.CTkFrame(self._dialog, fg_color="transparent")
        nav_frame.pack(fill="x", padx=24, pady=(0, 16))

        self._prev_btn = ctk.CTkButton(
            nav_frame,
            text="\u25C0 Back",
            width=100,
            height=30,
            corner_radius=8,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._on_prev,
        )
        self._prev_btn.pack(side="left")

        self._next_btn = ctk.CTkButton(
            nav_frame,
            text="Next \u25B6",
            width=100,
            height=30,
            corner_radius=8,
            fg_color="#4f8ef7",
            hover_color="#3a76e8",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_next,
        )
        self._next_btn.pack(side="right")

        self._close_btn = ctk.CTkButton(
            nav_frame,
            text="Close",
            width=80,
            height=30,
            corner_radius=8,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._dialog.destroy,
        )

    def _update_step_ui(self) -> None:
        """Show/hide frames and update progress indicators."""
        total = len(self._steps)
        for idx, frame in enumerate(self._step_frames):
            if idx == self._current_step:
                frame.pack(fill="x", pady=(0, 4))
            else:
                frame.pack_forget()

        # Update progress bars
        for idx, bar in enumerate(self._progress_bars):
            if idx < self._current_step:
                bar.configure(fg_color="#22c55e")
            elif idx == self._current_step:
                bar.configure(fg_color="#4f8ef7")
            else:
                bar.configure(fg_color="#2e3347")

        # Update step check icons
        for idx, check in enumerate(self._step_checks):
            if idx < self._current_step:
                check.configure(text="\u2714", text_color="#22c55e")
            elif idx == self._current_step:
                check.configure(text="\u25CF", text_color="#4f8ef7")
            else:
                check.configure(text="\u25CB", text_color="#8b92a8")

        # Navigation buttons
        self._prev_btn.configure(state="normal" if self._current_step > 0 else "disabled")

        is_last = self._current_step >= total - 1
        if is_last:
            self._next_btn.pack_forget()
            self._close_btn.pack(side="right")
        else:
            self._close_btn.pack_forget()
            self._next_btn.pack(side="right")

        # Action button
        step = self._steps[self._current_step]
        action_label = step.get("action_label", "")
        if action_label:
            self._action_btn.configure(text=action_label, state="normal")
            if not self._action_btn.winfo_manager():
                self._action_btn.pack(pady=(0, 6))
        else:
            if self._action_btn.winfo_manager():
                self._action_btn.pack_forget()

    def _on_next(self) -> None:
        if self._current_step < len(self._steps) - 1:
            self._current_step += 1
            self._update_step_ui()

    def _on_prev(self) -> None:
        if self._current_step > 0:
            self._current_step -= 1
            self._update_step_ui()

    def _on_action(self) -> None:
        """Execute the action associated with the current step."""
        step = self._steps[self._current_step]
        action = step.get("action_label", "")

        try:
            if "Chrome" in action and "extension" not in action.lower():
                # Open Chrome
                webbrowser.open("https://www.google.com/chrome/")
            elif "chrome://extensions" in action:
                webbrowser.open("chrome://extensions")
            elif "Extension Folder" in action:
                self._open_folder_action()
            elif "Copy Folder Path" in action:
                self._copy_path_action()
            elif "Chrome Extensions" in action:
                webbrowser.open("chrome://extensions")
        except Exception:
            pass

    def _open_folder_action(self) -> None:
        try:
            if os.path.isdir(_EXTENSION_DIR):
                if sys.platform == "win32":
                    os.startfile(_EXTENSION_DIR)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", _EXTENSION_DIR])
                else:
                    subprocess.Popen(["xdg-open", _EXTENSION_DIR])
        except Exception:
            pass

    def _copy_path_action(self) -> None:
        try:
            self._dialog.clipboard_clear()
            self._dialog.clipboard_append(_EXTENSION_DIR)
            self._dialog.update()
        except Exception:
            pass
