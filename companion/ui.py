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
from updater import UpdateManager
from installer import InstallerManager

if TYPE_CHECKING:
    from tray import TrayManager


# ---------------------------------------------------------------------------
# Theme / palette
# ---------------------------------------------------------------------------

# Theme is applied dynamically from local settings at startup (see CompanionWindow.__init__)
ctk.set_appearance_mode("dark")  # fallback — overridden immediately after
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
from scheduler import SchedulerManager
from scheduler_panel import SchedulerPage


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

        # Subtitle
        ctk.CTkLabel(
            self,
            text="Diagnostics and internal log activity stream.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
        ).pack(anchor="w", padx=20, pady=(0, 20))

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

        # Default theme — saved preference is loaded in start_background_services
        ctk.set_appearance_mode("Dark")

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

        # Busy guard for lifecycle operations (prevents rapid double-clicks)
        self._lifecycle_busy: bool = False

        # Build Sidebar & Pages
        self._build_ui()

        # Pre-create managers (no blocking I/O here)
        self.scheduler: SchedulerManager | None = None
        self.updater: UpdateManager | None = None
        self.installer: InstallerManager | None = None
        self._dashboard_controller: DashboardController | None = None

        # Build page instances (widgets only, no backend calls)
        self._pages: dict[str, BasePage] = {
            "Dashboard": DashboardPage(self._content_container, self._manager, self.logger),
            "Queue": QueuePage(self._content_container, self._manager, self.logger),
            "History": HistoryPage(self._content_container, self._manager, self.logger),
            "Scheduler": SchedulerPage(self._content_container, self._manager, self.logger),
            "Statistics": StatsPage(self._content_container, self._manager, self.logger),
            "Settings": SettingsPage(self._content_container, self._manager, self.logger),
            "Logs": LogsPage(self._content_container, self._manager, self.logger),
        }

        # Register backend status callback
        self._manager.register_status_callback(self._on_backend_status_change)
        self._on_backend_status_change(self._manager.status)

        # Show initial landing page immediately
        self.show_page("Dashboard")

        self.logger.mark_timing("UI Created")

        # Register page show callbacks for timing marks
        for name, page in self._pages.items():
            original_on_show = page.on_show
            def _timed_show(orig=original_on_show, pname=name):
                if pname == "Dashboard":
                    self.logger.mark_timing("Dashboard Built")
                orig()
            page.on_show = _timed_show

        # Defer all heavy init until after the window is rendered
        self.after(150, self.start_background_services)

    def start_background_services(self) -> None:
        """Launch all background services as daemon threads. Runs on Tkinter thread
        but dispatches blocking work to workers — returns immediately."""
        import time as _time
        self._startup_t0 = _time.monotonic()
        self.logger.mark_timing("Background Init")
        self.logger.info("Starting background services…")

        # ── Backend deferred init (port check / ping) ──────────────────
        threading.Thread(target=self._manager.deferred_init, daemon=True,
                         name="BackendDeferredInit").start()

        # ── Unified polling controller ──────────────────────────────────
        local_settings = read_local_settings(self.logger)
        self._dashboard_controller = DashboardController(self._manager, self.logger)
        self._dashboard_controller.associate_window(self)
        self._dashboard_controller.set_poll_interval(
            float(local_settings.get("backend_poll_interval", 3))
        )
        for page in self._pages.values():
            self._dashboard_controller.register_page(page)

        # ── Apply saved theme from settings ──────────────────────────────
        self._apply_startup_theme(local_settings)

        # ── Scheduler (object created on main thread; heavy I/O deferred) ─
        self.scheduler = SchedulerManager(self.logger, self._manager, self)

        # Wire scheduler into poller BEFORE starting it
        self._dashboard_controller.register_page(self.scheduler)
        self._dashboard_controller.start()

        # Stage remaining startups with 50ms gaps to reduce CPU spikes
        self.after(50, self._stage_scheduler_startup)

    def _stage_scheduler_startup(self) -> None:
        def _worker():
            self.scheduler.deferred_startup()
            self.scheduler.start()
            self.logger.mark_timing("Scheduler Started")
            self.after(0, self._on_scheduler_ready)
        threading.Thread(target=_worker, daemon=True,
                         name="SchedulerStartupWorker").start()
        self.after(50, self._stage_updater_startup)

    def _stage_updater_startup(self) -> None:
        def _worker():
            self.updater = UpdateManager(logger=self.logger)
            self.updater.register_callback(self._on_updater_event)
            self.updater.start()
            self.logger.mark_timing("Updater Started")
            self.logger.info("Updater started.")
        threading.Thread(target=_worker, daemon=True,
                         name="UpdaterStartupWorker").start()
        self.after(50, self._stage_installer_startup)

    def _stage_installer_startup(self) -> None:
        def _worker():
            self.installer = InstallerManager(
                logger=self.logger, updater=self.updater, window=self
            )
            self.logger.mark_timing("Installer Ready")
        threading.Thread(target=_worker, daemon=True,
                         name="InstallerStartupWorker").start()
        self.after(500, self._log_startup_timings)

    def _apply_startup_theme(self, local_settings: dict) -> None:
        saved_theme = local_settings.get("theme", "Dark")
        if saved_theme not in ("Dark", "Light", "System"):
            saved_theme = "Dark"
        try:
            ctk.set_appearance_mode(saved_theme)
        except Exception as exc:
            self.logger.warning(f"Failed to apply theme '{saved_theme}': {exc}")

    def _on_scheduler_ready(self) -> None:
        """Called on Tkinter thread once the scheduler has finished startup."""
        self.logger.info("Scheduler startup complete.")
        # Notify the Scheduler page and Dashboard to wire up the scheduler reference
        if "Scheduler" in self._pages:
            sched_page = self._pages["Scheduler"]
            if hasattr(sched_page, "on_show") and sched_page.winfo_ismapped():
                sched_page.on_show()
        if "Dashboard" in self._pages:
            dash = self._pages["Dashboard"]
            if hasattr(dash, "_update_scheduler_countdown"):
                # Re-wire the scheduler reference and start countdown
                dash.scheduler = self.scheduler
                self.scheduler.register_listener(dash._on_scheduler_event)
                dash._update_scheduler_countdown()

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
        for name in ("Dashboard", "Queue", "History", "Scheduler", "Statistics", "Settings", "Logs"):
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
            btn.pack(fill="x", padx=10, pady=3)
            self._sidebar_buttons[name] = btn

        # Bottom backend control in sidebar
        self._sidebar_bottom = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        self._sidebar_bottom.pack(side="bottom", fill="x", padx=10, pady=16)

        # Separator line above status
        ctk.CTkFrame(self._sidebar_bottom, height=1, fg_color=_CLR_BORDER).pack(fill="x", pady=(0, 12))

        # Status indicator
        self._status_frame = ctk.CTkFrame(self._sidebar_bottom, fg_color="transparent")
        self._status_frame.pack(fill="x", pady=(0, 2))

        self._status_lbl = ctk.CTkLabel(
            self._status_frame,
            text="● Stopped",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=_CLR_RED,
            anchor="w",
        )
        self._status_lbl.pack(side="left")

        # Version label — centered beneath status
        self._version_lbl = ctk.CTkLabel(
            self._sidebar_bottom,
            text="v—",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=_CLR_SUBTEXT,
            anchor="center",
            justify="center",
        )
        self._version_lbl.pack(fill="x", pady=(0, 12))

        # Start / Stop / Restart buttons
        self._start_btn = self._make_btn(
            self._sidebar_bottom, "▶ Start", _CLR_GREEN, "#16a34a", self._action_start, height=32
        )
        self._start_btn.pack(fill="x", pady=3)

        self._stop_btn = self._make_btn(
            self._sidebar_bottom, "■ Stop", _CLR_RED, "#b91c1c", self._action_stop, height=32
        )
        self._stop_btn.pack(fill="x", pady=3)
        self._restart_btn = self._make_btn(
            self._sidebar_bottom, "↻ Restart", _CLR_CARD, _CLR_BORDER, self._action_restart, height=32, text_color=_CLR_TEXT
        )
        self._restart_btn.pack(fill="x", pady=3)

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

    def _log_startup_timings(self) -> None:
        self.logger.mark_timing("Startup Complete")
        self.logger.log_startup_timings()

    def _on_backend_status_change(self, status: BackendStatus, message: str = "") -> None:
        """Marshall status callbacks safely onto Tkinter thread."""
        if status == BackendStatus.RUNNING:
            self.logger.mark_timing("Backend Connected")
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
        pass

    # ------------------------------------------------------------------
    # Backend lifecycle button hooks
    # ------------------------------------------------------------------

    def _action_start(self) -> None:
        if self._lifecycle_busy:
            return
        self._manager.start()

    def _action_stop(self) -> None:
        if self._lifecycle_busy:
            return
        self._lifecycle_busy = True
        def _run():
            try:
                self._manager.stop()
            finally:
                self._lifecycle_busy = False
        threading.Thread(target=_run, daemon=True).start()

    def _action_restart(self) -> None:
        if self._lifecycle_busy:
            return
        self._lifecycle_busy = True
        def _run():
            try:
                self._manager.restart()
            finally:
                self._lifecycle_busy = False
        threading.Thread(target=_run, daemon=True).start()

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
        # Check active installer protection (Component 5 / Refinements)
        if hasattr(self, "updater") and self.updater:
            state = self.updater.get_status()
            if state in ("Waiting For Exit", "Restarting Companion"):
                self.logger.info(f"Ignoring close request: installer is active (state={state}).")
                return
            elif state == "Launching":
                if not self._confirm_install_exit():
                    self.logger.info("Close request cancelled: continuing installation.")
                    return

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

        # 2. Shutdown sequence: minimize to tray or show exit dialog
        if self.tray_active:
            self.withdraw()
            if self._tray_manager:
                self._tray_manager.notify_background()
        else:
            self._show_shutdown_dialog()

    def _confirm_install_exit(self) -> bool:
        """
        Shows a modern CustomTkinter confirmation dialog with CustomTkinter styled buttons.
        """
        dialog = ctk.CTkToplevel(self)
        dialog.title("Installation in Progress")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        
        # Center dialog relative to main window
        x = self.winfo_x() + (self.winfo_width() - 360) // 2
        y = self.winfo_y() + (self.winfo_height() - 160) // 2
        dialog.geometry(f"360x160+{x}+{y}")

        msg = "Installation is currently in progress. Exiting now may interrupt the update. Are you sure you want to exit?"
        lbl = ctk.CTkLabel(dialog, text=msg, wraplength=320, justify="left")
        lbl.pack(pady=(20, 10), padx=20)

        result = [False]

        def on_continue():
            result[0] = False
            dialog.destroy()

        def on_exit():
            result[0] = True
            dialog.destroy()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom", pady=15, padx=20)

        btn_exit = ctk.CTkButton(btn_frame, text="Exit Anyway", command=on_exit, width=110, fg_color="#C0392B", hover_color="#922B21")
        btn_exit.pack(side="left")

        btn_continue = ctk.CTkButton(btn_frame, text="Continue Installation", command=on_continue, width=160, fg_color="#2E4053", hover_color="#212F3D")
        btn_continue.pack(side="right")
        btn_continue.focus_set()

        # Make Enter trigger continue (default action)
        dialog.bind("<Return>", lambda e: on_continue())

        self.wait_window(dialog)
        return result[0]

    def _show_shutdown_dialog(self) -> None:
        from tkinter import messagebox
        if self._manager.status == BackendStatus.RUNNING and self._manager.is_managed():
            ans = messagebox.askyesnocancel(
                "Exit Companion",
                "The backend service is currently running.\n\n"
                "• Click [Yes] to STOP the backend and exit.\n"
                "• Click [No] to hide to tray / exit Companion (backend stays active).\n"
                "• Click [Cancel] to return."
            )
            if ans is True:
                self.exit_completely()
            elif ans is False:
                # Tray active → hide to tray; tray unavailable → destroy window only
                if self.tray_active:
                    self.withdraw()
                else:
                    self._do_exit(stop_backend=False)
            # ans is None (Cancel) → do nothing
        else:
            self.exit_completely()

    def _do_exit(self, *, stop_backend: bool) -> None:
        # Stop updater thread cleanly (Phase 4.1)
        if hasattr(self, "updater") and self.updater:
            self.updater.shutdown()
        # Threaded stop to prevent freezing on window destroy
        def _cleanup():
            if stop_backend:
                self._manager.stop()
            self.after(0, self.destroy)
        threading.Thread(target=_cleanup, daemon=True).start()

    def exit_completely(self) -> None:
        """Fully shut down companion and backend."""
        # Check active installer protection (Component 5 / Refinements)
        if hasattr(self, "updater") and self.updater:
            state = self.updater.get_status()
            if state in ("Waiting For Exit", "Restarting Companion"):
                self.logger.info(f"Ignoring exit request: installer is active (state={state}).")
                return
            elif state == "Launching":
                if not self._confirm_install_exit():
                    self.logger.info("Exit request cancelled: continuing installation.")
                    return

        self.logger.info("Initiating complete exit sequence...")

        # 1. Stop dashboard poll controller
        if hasattr(self, "_dashboard_controller") and self._dashboard_controller:
            self._dashboard_controller.shutdown()

        # 2. Stop background update checking thread
        if hasattr(self, "updater") and self.updater:
            self.updater.shutdown()

        # 3. Stop scheduler thread (Phase 4.3)
        if hasattr(self, "scheduler") and self.scheduler:
            self.scheduler.shutdown()

        # 4. Stop system tray icon
        if self.tray_active and self._tray_manager:
            self._tray_manager.stop()

        # 5. Stop backend manager
        self._manager.shutdown()
        self._manager.stop()

        # Destroy window
        try:
            self.destroy()
        except Exception:
            pass

    def _on_updater_event(self, status: str, progress: float, error_msg: str | None = None) -> None:
        """Handle updater events, displaying tray notification bubbles once per release version."""
        if self.tray_active and self._tray_manager:
            self._tray_manager.refresh_menu()

        if status == "Update Available" and self.tray_active and self._tray_manager:
            latest = self.updater.get_latest_version()
            
            # Retrieve cache to see if we already notified for this version
            from updater import CACHE_FILE
            import json
            last_notified = ""
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                        last_notified = data.get("last_notified_version", "")
                except Exception:
                    pass
            
            if latest != last_notified:
                # Update last notified version in cache
                if os.path.exists(CACHE_FILE):
                    try:
                        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
                            data = json.load(fh)
                        data["last_notified_version"] = latest
                        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
                            json.dump(data, fh, indent=2)
                    except Exception:
                        pass
                
                # Trigger tray notification bubble
                self._tray_manager.notify(
                    "MediaForge Update Available",
                    f"Version {latest} is ready."
                )
        elif self.tray_active and self._tray_manager:
            if status == "Pending Install":
                self._tray_manager.notify(
                    "MediaForge Companion",
                    "Update downloaded. Ready to install!"
                )
            elif status == "Completed":
                self._tray_manager.notify(
                    "MediaForge Companion",
                    "Installation completed successfully!"
                )
            elif status == "Failed":
                self._tray_manager.notify(
                    "MediaForge Companion",
                    f"Installation failed: {error_msg or 'Verification error'}"
                )
            elif status == "Cancelled":
                self._tray_manager.notify(
                    "MediaForge Companion",
                    "Installation cancelled."
                )

    def prepare_for_installation(self) -> None:
        """Gracefully stop background controllers, tray icon, and backend manager before installation."""
        self.logger.info("Preparing Companion for installation (releasing locks and services)...")
        
        # 1. Stop dashboard poll controller
        if hasattr(self, "_dashboard_controller") and self._dashboard_controller:
            try:
                self._dashboard_controller.shutdown()
            except Exception as exc:
                self.logger.warning(f"Failed to shutdown dashboard controller cleanly: {exc}")
                
        # 2. Stop background update checking thread
        if hasattr(self, "updater") and self.updater:
            try:
                self.updater.shutdown()
            except Exception as exc:
                self.logger.warning(f"Failed to shutdown updater cleanly: {exc}")

        # 3. Stop scheduler thread (Phase 4.3)
        if hasattr(self, "scheduler") and self.scheduler:
            try:
                self.scheduler.shutdown()
            except Exception as exc:
                self.logger.warning(f"Failed to shutdown scheduler cleanly: {exc}")
                
        # 4. Stop system tray loop and remove icon
        if self.tray_active and hasattr(self, "_tray_manager") and self._tray_manager:
            try:
                self._tray_manager.stop()
                self.tray_active = False
            except Exception as exc:
                self.logger.warning(f"Failed to shutdown tray cleanly: {exc}")
                
        # 5. Stop and shutdown managed backend
        if hasattr(self, "_manager") and self._manager:
            try:
                self._manager.shutdown()
                if self._manager.is_managed():
                    self._manager.stop()
            except Exception as exc:
                self.logger.warning(f"Failed to shutdown backend manager cleanly: {exc}")


# ---------------------------------------------------------------------------
# Resource helper
# ---------------------------------------------------------------------------

def _resource_path(filename: str) -> str:
    """Return the absolute path to a file in companion/resources/."""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "resources", filename)
