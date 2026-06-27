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

from backend_manager import BackendStatus
from base_page import BasePage

if sys.platform == "win32":
    import winreg

if TYPE_CHECKING:
    from backend_manager import BackendManager
    from logger import AppLogger


# Path to local companion settings
_COMPANION_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_SETTINGS_FILE = os.path.join(_COMPANION_DIR, "settings.json")


DEFAULT_BACKEND_URL = "http://127.0.0.1:5000"


def default_local_settings() -> dict[str, Any]:
    return {
        "auto_start_companion": False,
        "auto_start_backend": True,
        "notification_toggle": True,
        "backend_poll_interval": 3,
        "theme": "Dark",
        # Phase 4.1 Auto-updates options
        "auto_check_updates": True,
        "update_poll_interval": 24,
        "check_updates_startup": True,
    }


def read_local_settings(logger: AppLogger | None = None) -> dict[str, Any]:
    cfg = default_local_settings()
    if os.path.exists(LOCAL_SETTINGS_FILE):
        try:
            with open(LOCAL_SETTINGS_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    cfg.update(data)
        except Exception:
            pass

    # Task 4 — Theme Validation & Fallback on loading
    theme = cfg.get("theme")
    if theme not in ("Dark", "Light", "System"):
        cfg["theme"] = "Dark"
        if logger:
            logger.log(f"Invalid theme preference '{theme}' detected. Falling back to 'Dark'.", "WARNING")

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

        title_container = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_container.pack(side="left", anchor="w")

        ctk.CTkLabel(
            title_container,
            text="Settings Center",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#e8eaf0",
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_container,
            text="Configure local app preferences and backend behaviors.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
        ).pack(anchor="w", pady=(4, 0))

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
        
        theme_row = ctk.CTkFrame(self._form, fg_color="transparent")
        theme_row.pack(fill="x", padx=10, pady=4)
        
        ctk.CTkLabel(theme_row, text="Theme Palette", width=150, anchor="w", text_color="#e8eaf0").pack(side="left")
        
        self._theme_var = ctk.StringVar()
        self._theme_menu = ctk.CTkOptionMenu(
            theme_row,
            variable=self._theme_var,
            values=["Dark", "Light", "System"],
            height=28,
            fg_color="#20232f",
            button_color="#2e3347",
            button_hover_color="#2e3347",
            dropdown_fg_color="#1a1d27",
            dropdown_hover_color="#2e3347",
            corner_radius=6
        )
        self._theme_menu.pack(side="left")
        
        self._apply_theme_btn = ctk.CTkButton(
            theme_row,
            text="Apply Theme",
            width=100,
            height=28,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=6,
            command=self._apply_theme
        )
        self._apply_theme_btn.pack(side="left", padx=(10, 0))

        # ── Category: Update Settings ──────────────────────────────────────
        self._create_section_label(self._form, "Update Settings")

        self._auto_check_var = ctk.BooleanVar()
        self._create_form_row_checkbox(self._form, "Auto Check Updates", "Automatically check for updates in the background", self._auto_check_var)

        self._check_startup_var = ctk.BooleanVar()
        self._create_form_row_checkbox(self._form, "Check on Startup", "Check for updates when the Companion launches", self._check_startup_var)

        self._update_poll_var = ctk.StringVar()
        self._create_form_row_entry(self._form, "Poll Interval (hours)", self._update_poll_var, "Default is 24 hours")

        # Info row (Current Version, Latest Version, Last Checked)
        info_row = ctk.CTkFrame(self._form, fg_color="transparent")
        info_row.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(info_row, text="Update Status", width=150, anchor="w", text_color="#e8eaf0").pack(side="left")

        self._update_info_lbl = ctk.CTkLabel(
            info_row,
            text="Current: v1.1.0 | Latest: v—\nLast checked: Never",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
            justify="left",
            anchor="w"
        )
        self._update_info_lbl.pack(side="left", fill="x", expand=True)

        # Action Row (Check Now, Download Update, Release Notes)
        actions_row = ctk.CTkFrame(self._form, fg_color="transparent")
        actions_row.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(actions_row, text="", width=150).pack(side="left")

        self._check_now_btn = ctk.CTkButton(
            actions_row,
            text="Check Now",
            width=100,
            height=28,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=6,
            command=self._check_now_click
        )
        self._check_now_btn.pack(side="left", padx=4)

        self._download_update_btn = ctk.CTkButton(
            actions_row,
            text="Download Update",
            width=125,
            height=28,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=6,
            command=self._download_update_click
        )
        self._download_update_btn.pack(side="left", padx=4)

        self._release_notes_btn = ctk.CTkButton(
            actions_row,
            text="Release Notes",
            width=100,
            height=28,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=6,
            command=self._release_notes_click
        )
        self._release_notes_btn.pack(side="left", padx=4)

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

        # Trace general options variables for dynamic Save button enable/disable state
        for var in (self._dir_var, self._ffmpeg_var, self._url_var, self._poll_var,
                    self._auto_start_companion_var, self._auto_start_backend_var,
                    self._notifications_var,
                    self._auto_check_var, self._check_startup_var, self._update_poll_var):
            var.trace_add("write", lambda *_: self._update_save_btn_state())

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
        """Load backend and local settings and populate all form fields."""
        backend_settings = self.manager.get_settings()
        local_settings = read_local_settings(self.logger)

        # Backend URL: prefer backend response, then manager's current base_url,
        # then the universal default. Never leave it blank.
        backend_url = (
            backend_settings.get("backend_url")
            or self.manager.base_url
            or DEFAULT_BACKEND_URL
        )

        self._original_settings = {
            "download_folder": backend_settings.get("download_folder", ""),
            "ffmpeg_path": backend_settings.get("ffmpeg_path", ""),
            "backend_url": backend_url,
            # Theme is a local Companion preference — stored in settings.json only
            "theme": local_settings.get("theme", "Dark"),
            "auto_start_companion": local_settings.get("auto_start_companion", False),
            "auto_start_backend": local_settings.get("auto_start_backend", True),
            "notification_toggle": local_settings.get("notification_toggle", True),
            "backend_poll_interval": local_settings.get("backend_poll_interval", 3),
            "auto_check_updates": local_settings.get("auto_check_updates", True),
            "update_poll_interval": local_settings.get("update_poll_interval", 24),
            "check_updates_startup": local_settings.get("check_updates_startup", True),
        }

        # Apply to form fields
        self._dir_var.set(self._original_settings["download_folder"])
        self._ffmpeg_var.set(self._original_settings["ffmpeg_path"])
        self._url_var.set(self._original_settings["backend_url"])
        self._poll_var.set(str(self._original_settings["backend_poll_interval"]))
        self._theme_var.set(self._original_settings["theme"])

        self._auto_start_companion_var.set(self._original_settings["auto_start_companion"])
        self._auto_start_backend_var.set(self._original_settings["auto_start_backend"])
        self._notifications_var.set(self._original_settings["notification_toggle"])
        self._auto_check_var.set(self._original_settings["auto_check_updates"])
        self._check_startup_var.set(self._original_settings["check_updates_startup"])
        self._update_poll_var.set(str(self._original_settings["update_poll_interval"]))

        # Update Save changes button state
        self._update_save_btn_state()

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
            "auto_check_updates": self._auto_check_var.get(),
            "check_updates_startup": self._check_startup_var.get(),
            "update_poll_interval": int(self._update_poll_var.get().strip() or "24"),
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
        self._update_save_btn_state()
        offline = data.get("offline", True)
        self._defaults_btn.configure(state="disabled" if offline else "normal")

    # ------------------------------------------------------------------
    # Local Settings Validation
    # ------------------------------------------------------------------

    def _validate_settings(self, vals: dict[str, Any]) -> tuple[bool, str]:
        """Validate settings values. Only validates Backend URL if it was changed."""
        # Theme validation has been removed from this general workflow (Task 5)

        # 1. Download folder must be a valid existing directory
        folder = vals["download_folder"]
        if not folder or not os.path.isdir(folder):
            return False, "Download Folder path must be a valid existing directory."

        # 2. FFmpeg path must exist if provided (empty = use system PATH)
        ffmpeg = vals["ffmpeg_path"]
        if ffmpeg and not os.path.exists(ffmpeg):
            return False, "FFmpeg Path must point to an existing file/directory, or be left empty for system defaults."

        # 3. Backend URL — only validate if it was changed by the user
        url = vals["backend_url"]
        original_url = self._original_settings.get("backend_url", "")
        if url != original_url:
            try:
                parsed = urllib.parse.urlparse(url)
                if not parsed.scheme or not parsed.netloc:
                    raise ValueError
            except Exception:
                return False, "Backend URL must be a valid HTTP/HTTPS address (e.g. http://127.0.0.1:5000)."

        # 4. Polling rate must be 1–60 seconds
        try:
            poll = int(vals["backend_poll_interval"])
            if poll < 1 or poll > 60:
                raise ValueError
        except ValueError:
            return False, "Backend Poll Interval must be an integer between 1 and 60 seconds."

        # 5. Update polling rate must be 1–168 hours
        try:
            upoll = int(vals.get("update_poll_interval", 24))
            if upoll < 1 or upoll > 168:
                raise ValueError
        except ValueError:
            return False, "Update Poll Interval must be an integer between 1 and 168 hours."

        return True, ""

    # ------------------------------------------------------------------
    # Button Commands
    # ------------------------------------------------------------------

    def _save_click(self) -> None:
        try:
            vals = self._get_widget_values()
        except ValueError:
            self._show_validation_error("Backend Poll Interval must be a number.")
            return

        ok, err = self._validate_settings(vals)
        if not ok:
            self._show_validation_error(err)
            return

        # 1. Save Backend Settings (theme excluded — it is a local preference)
        backend_changes = {
            "download_folder": vals["download_folder"],
            "ffmpeg_path": vals["ffmpeg_path"],
            "backend_url": vals["backend_url"],
        }
        self.manager.save_settings(backend_changes)

        # 2. Save Local Companion Settings (theme preserved/merged)
        local_settings = read_local_settings(self.logger)
        local_settings.update({
            "auto_start_companion": vals["auto_start_companion"],
            "auto_start_backend": vals["auto_start_backend"],
            "notification_toggle": vals["notification_toggle"],
            "backend_poll_interval": vals["backend_poll_interval"],
            "auto_check_updates": vals["auto_check_updates"],
            "check_updates_startup": vals["check_updates_startup"],
            "update_poll_interval": vals["update_poll_interval"],
        })
        write_local_settings(local_settings)

        # 3. Apply updated poll interval to the live controller
        try:
            main_window = self.master.master
            if hasattr(main_window, "_dashboard_controller") and main_window._dashboard_controller:
                main_window._dashboard_controller.set_poll_interval(float(vals["backend_poll_interval"]))
        except Exception:
            pass

        self.logger.info("Settings saved successfully.")
        self._load_all_settings()  # reload to refresh originals and update button state

    def _cancel_click(self) -> None:
        self._load_all_settings()
        self.logger.info("Changes discarded.")

    def _restore_defaults_click(self) -> None:
        self._dir_var.set(os.path.expanduser("~/Downloads"))
        self._ffmpeg_var.set("")
        self._url_var.set(DEFAULT_BACKEND_URL)
        self._poll_var.set("3")
        self._theme_var.set("Dark")
        self._auto_start_companion_var.set(False)
        self._auto_start_backend_var.set(True)
        self._notifications_var.set(True)
        self._auto_check_var.set(True)
        self._check_startup_var.set(True)
        self._update_poll_var.set("24")
        self._update_save_btn_state()
        self.logger.info("Restored settings controls to default values (click Save to apply).")

    def _update_save_btn_state(self) -> None:
        """Dynamically enable or disable the Save Changes button based on general settings dirty state."""
        try:
            current = self._get_widget_values()
            general_dirty = False
            for key, val in self._original_settings.items():
                if key == "theme":
                    continue
                if current.get(key) != val:
                    general_dirty = True
                    break
        except ValueError:
            general_dirty = True

        offline = self.manager.status != BackendStatus.RUNNING
        if offline:
            self._save_btn.configure(state="disabled")
        else:
            self._save_btn.configure(state="normal" if general_dirty else "disabled")

    def _check_now_click(self) -> None:
        if self.updater:
            self.updater.check_for_updates(force=True)

    def _download_update_click(self) -> None:
        if not self.updater:
            return
        btn_text = self._download_update_btn.cget("text")
        if "Cancel" in btn_text:
            self.updater.cancel_download()
        else:
            self.updater.download_update()

    def _release_notes_click(self) -> None:
        if self.updater:
            self.updater.open_release_notes()

    def _update_updater_status_ui(self, status: str, progress: float, error_msg: str | None = None) -> None:
        if not self.updater:
            main_window = self.master.master
            self.updater = getattr(main_window, "updater", None)

        if not self.updater:
            return

        current = self.updater.get_current_version()
        latest = self.updater.get_latest_version()
        last_checked_ts = self.updater.get_last_checked()

        if last_checked_ts > 0:
            from datetime import datetime
            last_checked_str = datetime.fromtimestamp(last_checked_ts).strftime("%Y-%m-%d %H:%M:%S")
        else:
            last_checked_str = "Never"

        # Update info text
        if status == "Rate Limited":
            info_text = f"Current: v{current} | Latest: Rate Limited\nLast checked: {last_checked_str}"
        elif status == "Installer Not Found":
            info_text = f"Current: v{current} | Latest: Installer Not Found\nLast checked: {last_checked_str}"
        else:
            info_text = f"Current: v{current} | Latest: {latest}\nLast checked: {last_checked_str}"
        self._update_info_lbl.configure(text=info_text)

        # Dynamic action buttons state
        if status == "Checking":
            self._check_now_btn.configure(state="disabled", text="Checking...")
            self._download_update_btn.configure(state="disabled", text="Download Update")
            self._release_notes_btn.configure(state="disabled")
        elif status == "Downloading":
            self._check_now_btn.configure(state="disabled", text="Check Now")
            self._download_update_btn.configure(state="normal", text=f"Cancel ({int(progress)}%)")
            self._release_notes_btn.configure(state="normal")
        elif status == "Verifying":
            self._check_now_btn.configure(state="disabled", text="Check Now")
            self._download_update_btn.configure(state="disabled", text="Verifying...")
            self._release_notes_btn.configure(state="normal")
        elif status == "Completed":
            self._check_now_btn.configure(state="normal", text="Check Now")
            self._download_update_btn.configure(state="disabled", text="Completed")
            self._release_notes_btn.configure(state="normal")
        elif status == "Failed":
            self._check_now_btn.configure(state="normal", text="Check Now")
            has_up = self.updater.has_update()
            self._download_update_btn.configure(
                state="normal" if has_up else "disabled",
                text="Retry Download" if has_up else "Download Update"
            )
            self._release_notes_btn.configure(state="normal" if latest != "v—" else "disabled")
        elif status == "Rate Limited":
            self._check_now_btn.configure(state="normal", text="Check Now")
            self._download_update_btn.configure(state="disabled", text="Download Update")
            self._release_notes_btn.configure(state="disabled")
        elif status == "Installer Not Found":
            self._check_now_btn.configure(state="normal", text="Check Now")
            self._download_update_btn.configure(state="disabled", text="Download Update")
            self._release_notes_btn.configure(state="normal" if latest != "v—" else "disabled")
        else:  # Idle, Up To Date, Update Available, Offline
            self._check_now_btn.configure(state="normal", text="Check Now")
            has_up = self.updater.has_update()
            self._download_update_btn.configure(
                state="normal" if has_up else "disabled",
                text="Download Update"
            )
            self._release_notes_btn.configure(state="normal" if latest != "v—" else "disabled")

    def on_show(self) -> None:
        main_window = self.master.master
        self.updater = getattr(main_window, "updater", None)
        if self.updater:
            self.updater.register_callback(self._on_update_status)
            has_up = self.updater.has_update()
            latest = self.updater.get_latest_version()
            if has_up:
                self._update_updater_status_ui("Update Available", 0.0)
            elif latest != "v—":
                self._update_updater_status_ui("Up To Date", 0.0)
            else:
                self._update_updater_status_ui("Idle", 0.0)

    def on_hide(self) -> None:
        if self.updater:
            self.updater.unregister_callback(self._on_update_status)

    def _on_update_status(self, status: str, progress: float, error_msg: str | None = None) -> None:
        try:
            self.after(0, self._update_updater_status_ui, status, progress, error_msg)
        except Exception:
            pass

    def _apply_theme(self) -> None:
        """Apply and persist selected Theme Palette independently from the general Settings save workflow."""
        selected_theme = self._theme_var.get()
        previous_theme = self._original_settings.get("theme", "Dark")

        try:
            if selected_theme not in ("Dark", "Light", "System"):
                raise ValueError(f"Invalid appearance mode value '{selected_theme}'")

            # 1. Apply theme immediately
            ctk.set_appearance_mode(selected_theme)

            # 2. Save theme directly into local settings
            local_settings = read_local_settings(self.logger)
            local_settings["theme"] = selected_theme
            write_local_settings(local_settings)

            # 3. Synchronize in-memory settings to clear Theme dirty state (Task 4)
            self._original_settings["theme"] = selected_theme

            # 4. Success log
            self.logger.info(f"Theme palette applied and saved: {selected_theme}")
            
            # Recalculate Save Changes button state immediately
            self._update_save_btn_state()

        except Exception as exc:
            # Task 5 — Failure Recovery
            self.logger.log(f"Failed to apply theme '{selected_theme}': {exc}", "ERROR", exc=exc)

            # Restore previous theme immediately
            try:
                ctk.set_appearance_mode(previous_theme)
            except Exception:
                pass

            # Restore previous dropdown selection
            self._theme_var.set(previous_theme)

            # Display friendly error dialog
            from tkinter import messagebox
            messagebox.showerror(
                "Theme Error",
                "Unable to apply the selected theme.\n"
                "Your previous theme has been restored."
            )

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
