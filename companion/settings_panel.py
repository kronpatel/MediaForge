"""
settings_panel.py – SettingsPage

Professional settings editor for MediaForge Companion.
Handles backend settings (download folder, ffmpeg path, backend URL, theme)
and local settings (auto-start companion, auto-start backend, notification toggle, poll rate).
Integrates with the Windows Registry for companion startup.
Provides local validations and unsaved changes interception.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from base_page import BasePage

if sys.platform == "win32":
    import winreg

if TYPE_CHECKING:
    from backend_manager import BackendManager
    from logger import AppLogger


# Path to local companion settings
_COMPANION_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_SETTINGS_FILE = os.path.join(_COMPANION_DIR, "settings.json")


def default_local_settings() -> dict[str, Any]:
    return {
        "auto_start_companion": False,
        "auto_start_backend": True,
        "notification_toggle": True,
        "backend_poll_interval": 3,
    }


def read_local_settings() -> dict[str, Any]:
    cfg = default_local_settings()
    if os.path.exists(LOCAL_SETTINGS_FILE):
        try:
            with open(LOCAL_SETTINGS_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    cfg.update(data)
        except Exception:
            pass
    # Sync auto_start_companion with Windows Registry state
    cfg["auto_start_companion"] = is_registry_autostart_enabled()
    return cfg


def write_local_settings(settings: dict[str, Any]) -> None:
    try:
        with open(LOCAL_SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        # Apply Windows Registry auto start
        set_registry_autostart(settings.get("auto_start_companion", False))
    except Exception:
        pass


def is_registry_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, "MediaForgeCompanion")
        winreg.CloseKey(key)
        return bool(value)
    except Exception:
        return False


def set_registry_autostart(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enabled:
            # Use sys.executable to run main.py
            main_script = os.path.join(_COMPANION_DIR, "main.py")
            cmd = f'"{sys.executable}" "{os.path.abspath(main_script)}"'
            winreg.SetValueEx(key, "MediaForgeCompanion", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "MediaForgeCompanion")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception:
        pass


class SettingsPage(BasePage):
    """
    Settings panel with categories, file dialog pickers, local validation,
    modification tracking, and navigation intercept checks.
    """

    def __init__(self, master: ctk.CTk, manager: BackendManager, logger: AppLogger, on_navigate_fn: Callable[[], None] | None = None) -> None:
        super().__init__(master, manager, logger)
        self._on_navigate = on_navigate_fn
        self._original_settings: dict[str, Any] = {}
        
        self._build_ui()
        self._load_all_settings()

    def _build_ui(self) -> None:
        # Header & Save Buttons
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(20, 10))

        ctk.CTkLabel(
            header_frame,
            text="Settings Center",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#e8eaf0",
        ).pack(side="left")

        # Action buttons in header
        self._save_btn = ctk.CTkButton(
            header_frame,
            text="Save Changes",
            width=110,
            height=30,
            fg_color="#22c55e",
            hover_color="#16a34a",
            text_color="#ffffff",
            corner_radius=8,
            command=self._save_click,
        )
        self._save_btn.pack(side="right", padx=(6, 0))

        self._cancel_btn = ctk.CTkButton(
            header_frame,
            text="Cancel",
            width=80,
            height=30,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=8,
            command=self._cancel_click,
        )
        self._cancel_btn.pack(side="right")

        # Scrollable Settings Form
        self._form = ctk.CTkScrollableFrame(self, fg_color="transparent", label_text="")
        self._form.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        # ── Category: Downloads ──────────────────────────────────────────
        self._create_section_label(self._form, "Download Management")
        
        self._dir_var = ctk.StringVar()
        self._create_form_row_picker(
            self._form, "Download Folder", self._dir_var, "Browse…", self._browse_dir
        )

        self._ffmpeg_var = ctk.StringVar()
        self._create_form_row_picker(
            self._form, "FFmpeg Path", self._ffmpeg_var, "Browse…", self._browse_file
        )

        # ── Category: Backend Connection ─────────────────────────────────
        self._create_section_label(self._form, "Backend Settings")
        
        self._url_var = ctk.StringVar()
        self._create_form_row_entry(self._form, "Backend URL", self._url_var, "e.g. http://127.0.0.1:5000")

        # ── Category: Local General Options ─────────────────────────────
        self._create_section_label(self._form, "Companion Preferences")
        
        self._poll_var = ctk.StringVar()
        self._create_form_row_entry(self._form, "Poll Interval (sec)", self._poll_var, "Default is 3 seconds")

        self._auto_start_companion_var = ctk.BooleanVar()
        self._create_form_row_checkbox(self._form, "Auto Start Companion", "Start Companion automatically on Windows launch", self._auto_start_companion_var)

        self._auto_start_backend_var = ctk.BooleanVar()
        self._create_form_row_checkbox(self._form, "Auto Start Backend", "Start the backend process automatically on Companion startup", self._auto_start_backend_var)

        self._notifications_var = ctk.BooleanVar()
        self._create_form_row_checkbox(self._form, "Show Notifications", "Enable system tray warning and success bubbles", self._notifications_var)

        # ── Category: Appearance ──────────────────────────────────────────
        self._create_section_label(self._form, "Appearance")
        self._theme_var = ctk.StringVar()
        self._create_form_row_menu(self._form, "Theme Palette", self._theme_var, ["Dark", "Midnight", "Contrast"])

        # ── Reset Default Button ──────────────────────────────────────────
        ctk.CTkFrame(self._form, height=1, fg_color="#2e3347").pack(fill="x", pady=20)
        
        self._defaults_btn = ctk.CTkButton(
            self._form,
            text="Restore Defaults",
            width=140,
            height=30,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=8,
            command=self._restore_defaults_click,
        )
        self._defaults_btn.pack(anchor="w", padx=10, pady=(0, 20))

    def _create_section_label(self, parent: ctk.CTkScrollableFrame, title: str) -> None:
        ctk.CTkLabel(
            parent,
            text=title,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#4f8ef7",
        ).pack(anchor="w", padx=10, pady=(16, 8))

    def _create_form_row_entry(self, parent: ctk.CTkScrollableFrame, label: str, var: ctk.StringVar, placeholder: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=4)
        
        ctk.CTkLabel(row, text=label, width=150, anchor="w", text_color="#e8eaf0").pack(side="left")
        ctk.CTkEntry(row, textvariable=var, placeholder_text=placeholder, height=28, fg_color="#1a1d27", border_color="#2e3347", corner_radius=6).pack(side="left", fill="x", expand=True)

    def _create_form_row_picker(self, parent: ctk.CTkScrollableFrame, label: str, var: ctk.StringVar, btn_text: str, command: Callable[[], None]) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=4)
        
        ctk.CTkLabel(row, text=label, width=150, anchor="w", text_color="#e8eaf0").pack(side="left")
        ctk.CTkEntry(row, textvariable=var, height=28, fg_color="#1a1d27", border_color="#2e3347", corner_radius=6).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text=btn_text, width=80, height=28, fg_color="#20232f", hover_color="#2e3347", text_color="#e8eaf0", command=command, corner_radius=6).pack(side="left", padx=(6, 0))

    def _create_form_row_checkbox(self, parent: ctk.CTkScrollableFrame, label: str, desc: str, var: ctk.BooleanVar) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=4)
        
        ctk.CTkLabel(row, text=label, width=150, anchor="w", text_color="#e8eaf0").pack(side="left")
        ctk.CTkCheckBox(row, variable=var, text=desc, font=ctk.CTkFont(family="Segoe UI", size=11), text_color="#8b92a8", border_width=2, corner_radius=4, width=20, height=20).pack(side="left", fill="x", expand=True)

    def _create_form_row_menu(self, parent: ctk.CTkScrollableFrame, label: str, var: ctk.StringVar, values: list[str]) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=4)
        
        ctk.CTkLabel(row, text=label, width=150, anchor="w", text_color="#e8eaf0").pack(side="left")
        ctk.CTkOptionMenu(row, variable=var, values=values, height=28, fg_color="#20232f", button_color="#2e3347", button_hover_color="#2e3347", dropdown_fg_color="#1a1d27", dropdown_hover_color="#2e3347", corner_radius=6).pack(side="left")

    # ------------------------------------------------------------------
    # Settings Fetching & Loading
    # ------------------------------------------------------------------

    def _load_all_settings(self) -> None:
        # Load backend settings (from memory/polled state or fresh GET)
        backend_settings = self.manager.get_settings()
        local_settings = read_local_settings()

        self._original_settings = {
            "download_folder": backend_settings.get("download_folder", ""),
            "ffmpeg_path": backend_settings.get("ffmpeg_path", ""),
            "backend_url": backend_settings.get("backend_url", self.manager.base_url),
            "theme": backend_settings.get("theme", "dark").capitalize(),
            "auto_start_companion": local_settings.get("auto_start_companion", False),
            "auto_start_backend": local_settings.get("auto_start_backend", True),
            "notification_toggle": local_settings.get("notification_toggle", True),
            "backend_poll_interval": local_settings.get("backend_poll_interval", 3),
        }

        # Apply to fields
        self._dir_var.set(self._original_settings["download_folder"])
        self._ffmpeg_var.set(self._original_settings["ffmpeg_path"])
        self._url_var.set(self._original_settings["backend_url"])
        self._poll_var.set(str(self._original_settings["backend_poll_interval"]))
        self._theme_var.set(self._original_settings["theme"])
        
        self._auto_start_companion_var.set(self._original_settings["auto_start_companion"])
        self._auto_start_backend_var.set(self._original_settings["auto_start_backend"])
        self._notifications_var.set(self._original_settings["notification_toggle"])

    def _get_widget_values(self) -> dict[str, Any]:
        return {
            "download_folder": self._dir_var.get().strip(),
            "ffmpeg_path": self._ffmpeg_var.get().strip(),
            "backend_url": self._url_var.get().strip(),
            "theme": self._theme_var.get(),
            "auto_start_companion": self._auto_start_companion_var.get(),
            "auto_start_backend": self._auto_start_backend_var.get(),
            "notification_toggle": self._notifications_var.get(),
            "backend_poll_interval": int(self._poll_var.get().strip() or "3"),
        }

    def is_dirty(self) -> bool:
        """Compares widget values against loaded original settings."""
        try:
            current = self._get_widget_values()
            for key, val in self._original_settings.items():
                if current.get(key) != val:
                    return True
        except ValueError:
            return True # if parse error in poll rate entry, treat as dirty/invalid
        return False

    def refresh(self, data: dict[str, Any]) -> None:
        """Settings Page does not poll-update while focused to avoid wiping unsaved edits."""
        # If offline, make inputs read-only
        offline = data.get("offline", True)
        state = "disabled" if offline else "normal"
        self._save_btn.configure(state=state)
        self._defaults_btn.configure(state=state)

    # ------------------------------------------------------------------
    # Local Settings Validation
    # ------------------------------------------------------------------

    def _validate_settings(self, vals: dict[str, Any]) -> tuple[bool, str]:
        # 1. Download folder exists
        folder = vals["download_folder"]
        if not folder or not os.path.isdir(folder):
            return False, "Download Folder path must be a valid existing directory."

        # 2. FFmpeg path exists (if provided)
        ffmpeg = vals["ffmpeg_path"]
        if ffmpeg and not os.path.exists(ffmpeg):
            return False, "FFmpeg Path must point to an existing file/directory, or be left empty for system defaults."

        # 3. Valid Backend URL
        url = vals["backend_url"]
        try:
            parsed = urllib.parse.urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError
        except Exception:
            return False, "Backend URL must be a valid HTTP/HTTPS address (e.g. http://127.0.0.1:5000)."

        # 4. Valid Polling Rate
        try:
            poll = int(vals["backend_poll_interval"])
            if poll < 1 or poll > 60:
                raise ValueError
        except ValueError:
            return False, "Backend Poll Interval must be an integer between 1 and 60 seconds."

        return True, ""

    # ------------------------------------------------------------------
    # Button Commands
    # ------------------------------------------------------------------

    def _save_click(self) -> None:
        try:
            vals = self._get_widget_values()
        except ValueError as exc:
            self._show_validation_error("Backend Poll Interval must be a number.")
            return

        ok, err = self._validate_settings(vals)
        if not ok:
            self._show_validation_error(err)
            return

        # 1. Save Backend Settings
        backend_changes = {
            "download_folder": vals["download_folder"],
            "theme": vals["theme"].lower(),
            "ffmpeg_path": vals["ffmpeg_path"],
            "backend_url": vals["backend_url"],
        }
        self.manager.save_settings(backend_changes)

        # 2. Save Local Companion Settings
        local_changes = {
            "auto_start_companion": vals["auto_start_companion"],
            "auto_start_backend": vals["auto_start_backend"],
            "notification_toggle": vals["notification_toggle"],
            "backend_poll_interval": vals["backend_poll_interval"],
        }
        write_local_settings(local_changes)

        # Apply polling update
        try:
            # Get window reference
            main_window = self.master.master
            if hasattr(main_window, "_dashboard_controller") and main_window._dashboard_controller:
                main_window._dashboard_controller.set_poll_interval(float(vals["backend_poll_interval"]))
        except Exception:
            pass

        self.logger.info("Settings saved successfully.")
        self._load_all_settings() # reload references

    def _cancel_click(self) -> None:
        self._load_all_settings()
        self.logger.info("Changes discarded.")

    def _restore_defaults_click(self) -> None:
        self._dir_var.set(os.path.expanduser("~/Downloads"))
        self._ffmpeg_var.set("")
        self._url_var.set("http://127.0.0.1:5000")
        self._poll_var.set("3")
        self._theme_var.set("Dark")
        self._auto_start_companion_var.set(False)
        self._auto_start_backend_var.set(True)
        self._notifications_var.set(True)
        self.logger.info("Restored settings controls to default values (click Save to apply).")

    # ------------------------------------------------------------------
    # Dialogs & Browsers
    # ------------------------------------------------------------------

    def _browse_dir(self) -> None:
        from tkinter import filedialog
        path = filedialog.askdirectory(
            title="Select Download Folder",
            initialdir=self._dir_var.get() or os.path.expanduser("~/Downloads")
        )
        if path:
            self._dir_var.set(path)

    def _browse_file(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select FFmpeg Executable",
            initialdir=os.path.dirname(self._ffmpeg_var.get() or "") or "/"
        )
        if path:
            self._ffmpeg_var.set(path)

    def _show_validation_error(self, message: str) -> None:
        from tkinter import messagebox
        messagebox.showerror("Settings Error", message)
