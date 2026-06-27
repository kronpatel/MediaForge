"""
ui.py – CompanionWindow

CustomTkinter main window for the MediaForge Companion.

Design principles
-----------------
* Fixed 400 × 620 px window (Phase 1 – resizing in later phases).
* All blocking operations run in daemon threads.
* Cross-thread UI updates are dispatched via ``self.after(0, fn)``.
* Three status states:
    STOPPED  → red  indicator
    STARTING → yellow indicator
    RUNNING  → green indicator
"""

from __future__ import annotations

import os
import threading
import webbrowser
from typing import TYPE_CHECKING

import customtkinter as ctk

from backend_manager import BackendManager, BackendStatus
from logger import AppLogger, LogEntry

if TYPE_CHECKING:
    from tray import TrayManager


# ---------------------------------------------------------------------------
# Theme / palette
# ---------------------------------------------------------------------------

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Custom accent palette
_CLR_BG         = "#0f1117"
_CLR_SURFACE    = "#1a1d27"
_CLR_CARD       = "#20232f"
_CLR_BORDER     = "#2e3347"
_CLR_TEXT       = "#e8eaf0"
_CLR_SUBTEXT    = "#8b92a8"
_CLR_ACCENT     = "#4f8ef7"
_CLR_ACCENT_HOV = "#3a76e8"

_CLR_GREEN      = "#22c55e"
_CLR_YELLOW     = "#f59e0b"
_CLR_RED        = "#ef4444"

_STATUS_COLOR: dict[BackendStatus, str] = {
    BackendStatus.STOPPED:  _CLR_RED,
    BackendStatus.STARTING: _CLR_YELLOW,
    BackendStatus.RUNNING:  _CLR_GREEN,
    BackendStatus.CRASHED:  _CLR_RED,
}

_STATUS_TEXTS: dict[BackendStatus, str] = {
    BackendStatus.STOPPED:  "● Stopped",
    BackendStatus.STARTING: "● Starting…",
    BackendStatus.RUNNING:  "● Running",
    BackendStatus.CRASHED:  "● Crashed",
}

WINDOW_W = 900
WINDOW_H = 650

# ---------------------------------------------------------------------------
# Base & Custom Pages
# ---------------------------------------------------------------------------

from base_page import BasePage
from dashboard import DashboardController, DashboardPage
from queue_panel import QueuePage
from history_panel import HistoryPage
from stats_panel import StatsPage
from settings_panel import SettingsPage, read_local_settings


class LogsPage(BasePage):
    """
    Embedded logs page in the dashboard.
    Supports in-app log display, search, export to file, and clearing.
    """

    def __init__(self, master: ctk.CTkFrame, manager: BackendManager, logger: AppLogger) -> None:
        super().__init__(master, manager, logger)
        self._build_ui()

    def _build_ui(self) -> None:
        # Title
        ctk.CTkLabel(
            self,
            text="Companion Logs",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#e8eaf0",
        ).pack(anchor="w", padx=20, pady=(20, 10))

        # Toolbar
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=20, pady=6)

        self._export_btn = ctk.CTkButton(
            toolbar,
            text="Export Logs",
            width=110,
            height=30,
            fg_color="#4f8ef7",
            hover_color="#3a76e8",
            text_color="#ffffff",
            corner_radius=8,
            command=self._export_logs_click,
        )
        self._export_btn.pack(side="left")

        self._clear_btn = ctk.CTkButton(
            toolbar,
            text="Clear Logs",
            width=110,
            height=30,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=8,
            command=self._clear_logs_click,
        )
        self._clear_btn.pack(side="left", padx=10)

        # Log TextBox
        self._textbox = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#1a1d27",
            text_color="#e8eaf0",
            border_color="#2e3347",
            border_width=1,
            corner_radius=8,
            wrap="word",
            state="disabled",
        )
        self._textbox.pack(fill="both", expand=True, padx=20, pady=(10, 20))

    def on_show(self) -> None:
        self._populate_logs()

    def refresh(self, data: dict[str, Any]) -> None:
        # Avoid forcing textbox refreshes while in another tab
        pass

    def _populate_logs(self) -> None:
        entries = self.logger.get_entries()
        self._textbox.configure(state="normal")
        self._textbox.delete("1.0", "end")
        for entry in entries:
            self._textbox.insert("end", str(entry) + "\n")
        self._textbox.configure(state="disabled")
        self._textbox.see("end")

    def _clear_logs_click(self) -> None:
        self.logger.clear()
        self._populate_logs()

    def _export_logs_click(self) -> None:
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            title="Export Logs As",
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")]
        )
        if path:
            try:
                self.logger.export_logs(path)
                self.logger.info(f"Logs successfully exported to: {path}")
            except Exception as exc:
                self.logger.error(f"Failed to export logs: {exc}")


# ---------------------------------------------------------------------------
# Main Window class
# ---------------------------------------------------------------------------

class CompanionWindow(ctk.CTk):
    """
    Multi-page Tkinter main window using CustomTkinter styling.
    Features a persistent sidebar for backend lifecycle operations and navigation.
    """

    def __init__(self, manager: BackendManager, logger: AppLogger) -> None:
        super().__init__()
        self._manager = manager
        self.logger = logger
        self.tray_active: bool = False
        self._tray_manager: Any = None
        self._current_page_name: str = ""

        # Window settings
        self.title("MediaForge Companion")
        self.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.resizable(False, False)
        self.configure(fg_color=_CLR_BG)

        # Set taskbar icon
        self._set_window_icon()

        # Window closing events
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)
        self.bind("<Unmap>", self._on_unmap)

        # Build Sidebar & Pages
        self._build_ui()

        # Build pages
        self._pages: dict[str, BasePage] = {
            "Dashboard": DashboardPage(self._content_container, self._manager, self.logger),
            "Queue": QueuePage(self._content_container, self._manager, self.logger),
            "History": HistoryPage(self._content_container, self._manager, self.logger),
            "Statistics": StatsPage(self._content_container, self._manager, self.logger),
            "Settings": SettingsPage(self._content_container, self._manager, self.logger),
            "Logs": LogsPage(self._content_container, self._manager, self.logger),
        }

        # Setup Unified Controller
        self._dashboard_controller = DashboardController(self._manager, self.logger)
        self._dashboard_controller.associate_window(self)
        
        # Load local settings for poll interval
        local_settings = read_local_settings()
        self._dashboard_controller.set_poll_interval(float(local_settings.get("backend_poll_interval", 3)))

        # Register pages and start controller thread
        for page in self._pages.values():
            self._dashboard_controller.register_page(page)
            
        self._dashboard_controller.start()

        # Show initial landing page
        self.show_page("Dashboard")

        # Sync backend managers callback to update UI
        self._manager.register_status_callback(self._on_backend_status_change)
        
        # Trigger initial status application
        self._on_backend_status_change(self._manager.status)

    def _set_window_icon(self) -> None:
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(base_dir, "resources", "icon.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass

    def _build_ui(self) -> None:
        # Sidebar Frame (left)
        self._sidebar = ctk.CTkFrame(
            self,
            width=180,
            fg_color=_CLR_SURFACE,
            border_color=_CLR_BORDER,
            border_width=1,
            corner_radius=0,
        )
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        # Title/Logo in sidebar
        logo_lbl = ctk.CTkLabel(
            self._sidebar,
            text="⚡ MediaForge",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color=_CLR_TEXT,
        )
        logo_lbl.pack(pady=(20, 16), padx=15, anchor="w")

        # Sidebar navigation buttons
        self._sidebar_buttons = {}
        for name in ("Dashboard", "Queue", "History", "Statistics", "Settings", "Logs"):
            btn = ctk.CTkButton(
                self._sidebar,
                text=name,
                height=36,
                fg_color="transparent",
                hover_color=_CLR_BORDER,
                text_color=_CLR_TEXT,
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                anchor="w",
                command=lambda n=name: self.show_page(n),
                corner_radius=6,
            )
            btn.pack(fill="x", padx=10, pady=2)
            self._sidebar_buttons[name] = btn

        # Bottom backend control in sidebar
        self._sidebar_bottom = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        self._sidebar_bottom.pack(side="bottom", fill="x", padx=10, pady=16)

        # Separator line above status
        ctk.CTkFrame(self._sidebar_bottom, height=1, fg_color=_CLR_BORDER).pack(fill="x", pady=(0, 12))

        # Status Circle & Text
        self._status_frame = ctk.CTkFrame(self._sidebar_bottom, fg_color="transparent")
        self._status_frame.pack(fill="x", pady=(0, 4))

        self._status_lbl = ctk.CTkLabel(
            self._status_frame,
            text="● Stopped",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=_CLR_RED,
            anchor="w",
        )
        self._status_lbl.pack(side="left")

        self._version_lbl = ctk.CTkLabel(
            self._sidebar_bottom,
            text="v—",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=_CLR_SUBTEXT,
            anchor="w",
        )
        self._version_lbl.pack(fill="x", pady=(0, 10))

        # Start / Stop / Restart buttons
        self._start_btn = self._make_btn(
            self._sidebar_bottom, "▶ Start", _CLR_GREEN, "#16a34a", self._action_start, height=30
        )
        self._start_btn.pack(fill="x", pady=2)

        self._stop_btn = self._make_btn(
            self._sidebar_bottom, "■ Stop", _CLR_RED, "#b91c1c", self._action_stop, height=30
        )
        self._stop_btn.pack(fill="x", pady=2)
        self._restart_btn = self._make_btn(
            self._sidebar_bottom, "↻ Restart", _CLR_CARD, _CLR_BORDER, self._action_restart, height=30, text_color=_CLR_TEXT
        )
        self._restart_btn.pack(fill="x", pady=2)

        # ── Main Content Area Frame (right) ──────────────────────────────
        self._content_container = ctk.CTkFrame(self, fg_color="transparent")
        self._content_container.pack(side="right", fill="both", expand=True)
        self._content_container.grid_rowconfigure(0, weight=1)
        self._content_container.grid_columnconfigure(0, weight=1)

    def _make_btn(self, parent, text, fg, hover, cmd, **kwargs) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=text,
            fg_color=fg,
            hover_color=hover,
            command=cmd,
            corner_radius=8,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Page Switching Navigation
    # ------------------------------------------------------------------

    def show_page(self, name: str) -> None:
        """Switch content view frame to the cached page."""
        # Unsaved changes protection
        if self._current_page_name == "Settings" and name != "Settings":
            settings_page = self._pages["Settings"]
            if settings_page.is_dirty():
                from tkinter import messagebox
                ans = messagebox.askyesnocancel(
                    "Unsaved Changes",
                    "You have unsaved changes in Settings.\nDo you want to save them before leaving?"
                )
                if ans is True:  # Save
                    settings_page._save_click()
                    if settings_page.is_dirty():
                        return  # Validation failed, stop transition
                elif ans is False:  # Discard
                    settings_page._cancel_click()
                else:  # Cancel page switch
                    return

        # Hide current
        if self._current_page_name:
            old_page = self._pages[self._current_page_name]
            old_page.grid_forget()
            old_page.on_hide()

        # Show new page
        self._current_page_name = name
        new_page = self._pages[name]
        new_page.grid(row=0, column=0, sticky="nsew")

        # Style sidebar buttons
        for btn_name, btn in self._sidebar_buttons.items():
            if btn_name == name:
                btn.configure(fg_color=_CLR_BORDER)
            else:
                btn.configure(fg_color="transparent")

        new_page.on_show()

        # Provide immediate refresh with cached data
        if hasattr(self, "_dashboard_controller") and self._dashboard_controller:
            new_page.refresh(self._dashboard_controller.get_cached_data())

    # ------------------------------------------------------------------
    # Unified status callback
    # ------------------------------------------------------------------

    def _on_backend_status_change(self, status: BackendStatus, message: str = "") -> None:
        """Marshall status callbacks safely onto Tkinter thread."""
        self.after(0, self._apply_status, status)

    def _apply_status(self, status: BackendStatus) -> None:
        lbl_text = _STATUS_TEXTS.get(status, f"● {status.name.capitalize()}")
        self._status_lbl.configure(text=lbl_text)

        if status == BackendStatus.RUNNING:
            self._status_lbl.configure(text_color=_CLR_GREEN)
            
            # Adopted/External handling
            if self._manager.is_managed():
                self._start_btn.configure(state="disabled")
                self._stop_btn.configure(state="normal")
                self._restart_btn.configure(state="normal")
            else:
                self._start_btn.configure(state="disabled")
                self._stop_btn.configure(state="disabled")
                self._restart_btn.configure(state="disabled")
                
            # Fetch backend version
            ver = self._manager.fetch_version()
            self._version_lbl.configure(text=f"Version: v{ver}" if ver else "Version: v—")

            # Trigger immediate poller tick for instant online recovery sync
            if hasattr(self, "_dashboard_controller") and self._dashboard_controller:
                self._dashboard_controller.trigger_poll()

        elif status in (BackendStatus.STOPPED, BackendStatus.CRASHED):
            self._status_lbl.configure(text_color=_CLR_RED)
            self._start_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")
            self._restart_btn.configure(state="disabled")
            self._version_lbl.configure(text="v—")
        elif status == BackendStatus.STARTING:
            self._status_lbl.configure(text_color=_CLR_YELLOW)
            self._start_btn.configure(state="disabled")
            self._stop_btn.configure(state="disabled")
            self._restart_btn.configure(state="disabled")

        # Let the tray update itself (tray_manager listens to manager state)
        if self.tray_active and self._tray_manager:
            self._tray_manager.refresh_menu()

    # ------------------------------------------------------------------
    # Backend lifecycle button hooks
    # ------------------------------------------------------------------

    def _action_start(self) -> None:
        self._manager.start()

    def _action_stop(self) -> None:
        # Run stop in daemon thread to avoid blocking main Tk thread
        threading.Thread(target=self._manager.stop, daemon=True).start()

    def _action_restart(self) -> None:
        threading.Thread(target=self._manager.restart, daemon=True).start()

    def set_tray_manager(self, tray_manager: Any) -> None:
        self._tray_manager = tray_manager
        self.tray_active = True

    def restore_window(self) -> None:
        """Restore window to normal size and lift to topmost."""
        self.after(0, self._restore_safe)

    def _restore_safe(self) -> None:
        self.deiconify()
        self.state("normal")
        self.focus_force()
        self.reveal_window()

    def reveal_window(self) -> None:
        self.lift()
        self.focus_force()

    def trigger_start(self) -> None:
        self._action_start()

    def trigger_stop(self) -> None:
        self._action_stop()

    def trigger_restart(self) -> None:
        self._action_restart()

    # ------------------------------------------------------------------
    # Close / Minimise events
    # ------------------------------------------------------------------

    def _on_unmap(self, event) -> None:
        # Intercept iconic minimisation
        if event.widget == self and self.state() == "iconic" and self.tray_active:
            self.withdraw()
            if self._tray_manager:
                self._tray_manager.notify_background()

    def _on_close_request(self) -> None:
        # 1. Unsaved changes check
        if "Settings" in self._pages:
            settings_page = self._pages["Settings"]
            if settings_page.is_dirty():
                from tkinter import messagebox
                ans = messagebox.askyesnocancel(
                    "Unsaved Changes",
                    "You have unsaved changes in Settings.\nDo you want to save them before exiting?"
                )
                if ans is True:
                    settings_page._save_click()
                    if settings_page.is_dirty():
                        return  # Validation failed, stop exit
                elif ans is False:
                    pass  # proceed without saving
                else:
                    return  # cancel exit

        # 2. Shutdown sequence checks
        if not self.tray_active:
            self._show_shutdown_dialog()
            return

        if self._manager.status == BackendStatus.RUNNING and self._manager.is_managed():
            self._show_shutdown_dialog()
        else:
            self.withdraw()

    def _show_shutdown_dialog(self) -> None:
        from tkinter import messagebox
        if self._manager.status == BackendStatus.RUNNING and self._manager.is_managed():
            ans = messagebox.askyesnocancel(
                "Exit Companion",
                "The backend service is currently running.\n\n"
                "• Click [Yes] to STOP the backend and exit.\n"
                "• Click [No] to hide to tray (Exit Only) keeping backend active.\n"
                "• Click [Cancel] to return."
            )
            if ans is True:
                self.exit_completely()
            elif ans is False:
                if self.tray_active:
                    self.withdraw()
                else:
                    self._do_exit(stop_backend=False)
        else:
            self.exit_completely()

    def _do_exit(self, *, stop_backend: bool) -> None:
        # Threaded stop to prevent freezing on window destroy
        def _cleanup():
            if stop_backend:
                self._manager.stop()
            self.after(0, self.destroy)
        threading.Thread(target=_cleanup, daemon=True).start()

    def exit_completely(self) -> None:
        """Fully shut down companion and backend."""
        self.logger.info("Initiating complete exit sequence...")
        
        # Stop polling thread first
        if hasattr(self, "_dashboard_controller") and self._dashboard_controller:
            self._dashboard_controller.shutdown()

        # Stop pystray icon
        if self.tray_active and self._tray_manager:
            self._tray_manager.stop()
            self._tray_manager.join(timeout=2.0)

        # Stop backend manager
        self._manager.shutdown()
        self._manager.stop()

        # Destroy window
        try:
            self.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Resource helper
# ---------------------------------------------------------------------------

def _resource_path(filename: str) -> str:
    """Return the absolute path to a file in companion/resources/."""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "resources", filename)
