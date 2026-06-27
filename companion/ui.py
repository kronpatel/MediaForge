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

_STATUS_LABEL: dict[BackendStatus, str] = {
    BackendStatus.STOPPED:  "● Stopped",
    BackendStatus.STARTING: "● Starting…",
    BackendStatus.RUNNING:  "● Running",
    BackendStatus.CRASHED:  "● Crashed",
}

WINDOW_W = 420
WINDOW_H = 640


# ---------------------------------------------------------------------------
# Helper widget: RoundedCard frame
# ---------------------------------------------------------------------------

class _Card(ctk.CTkFrame):
    """A styled card frame used to group related controls."""

    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            fg_color=_CLR_CARD,
            corner_radius=12,
            border_width=1,
            border_color=_CLR_BORDER,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Log Viewer Toplevel
# ---------------------------------------------------------------------------

class LogViewerWindow(ctk.CTkToplevel):
    """Modal-like window that displays the full scrollable log."""

    def __init__(self, parent: "CompanionWindow", logger: AppLogger) -> None:
        super().__init__(parent)
        self._logger = logger
        self._parent = parent

        self.title("MediaForge Companion – Logs")
        self.geometry("640x480")
        self.resizable(True, True)
        self.configure(fg_color=_CLR_BG)

        # Register live-update callback
        self._logger.register_callback(self._on_new_entry)

        self._build_ui()
        self._populate()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self.lift)  # bring to front

    def _build_ui(self) -> None:
        title_lbl = ctk.CTkLabel(
            self,
            text="📜  Application Logs",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=_CLR_TEXT,
        )
        title_lbl.pack(pady=(16, 8), padx=16, anchor="w")

        self._textbox = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=_CLR_SURFACE,
            text_color=_CLR_TEXT,
            border_color=_CLR_BORDER,
            border_width=1,
            corner_radius=8,
            wrap="word",
            state="disabled",
        )
        self._textbox.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(0, 12))

        clear_btn = ctk.CTkButton(
            btn_frame,
            text="Clear Logs",
            width=110,
            height=32,
            fg_color=_CLR_CARD,
            hover_color=_CLR_BORDER,
            text_color=_CLR_SUBTEXT,
            corner_radius=8,
            command=self._clear,
        )
        clear_btn.pack(side="left")

        close_btn = ctk.CTkButton(
            btn_frame,
            text="Close",
            width=90,
            height=32,
            fg_color=_CLR_ACCENT,
            hover_color=_CLR_ACCENT_HOV,
            corner_radius=8,
            command=self._on_close,
        )
        close_btn.pack(side="right")

    def _populate(self) -> None:
        entries = self._logger.get_entries()
        self._textbox.configure(state="normal")
        self._textbox.delete("1.0", "end")
        for entry in entries:
            self._textbox.insert("end", str(entry) + "\n")
        self._textbox.configure(state="disabled")
        self._textbox.see("end")

    def _on_new_entry(self, entry: LogEntry) -> None:
        """Called from AppLogger (potentially non-UI thread)."""
        try:
            self.after(0, self._append_entry, entry)
        except Exception:
            pass

    def _append_entry(self, entry: LogEntry) -> None:
        self._textbox.configure(state="normal")
        self._textbox.insert("end", str(entry) + "\n")
        self._textbox.configure(state="disabled")
        self._textbox.see("end")

    def _clear(self) -> None:
        self._logger.clear()
        self._textbox.configure(state="normal")
        self._textbox.delete("1.0", "end")
        self._textbox.configure(state="disabled")

    def _on_close(self) -> None:
        self._logger.unregister_callback(self._on_new_entry)
        self.destroy()


# ---------------------------------------------------------------------------
# Main Companion Window
# ---------------------------------------------------------------------------

class CompanionWindow(ctk.CTk):
    """
    Main application window for MediaForge Companion.

    Fixed at WINDOW_W × WINDOW_H for Phase 1.
    """

    def __init__(self, manager: BackendManager, logger: AppLogger) -> None:
        super().__init__()

        self._manager = manager
        self._logger = logger
        self._log_viewer: LogViewerWindow | None = None
        self._current_status: BackendStatus = BackendStatus.STOPPED

        # Tray properties
        self.tray_active: bool = False
        self._tray_manager: TrayManager | None = None

        self._setup_window()
        self._build_ui()

        # Wire backend status updates into the UI
        self._manager.register_status_callback(self._on_status_change)

        # Wire log entries into the inline log panel
        self._logger.register_callback(self._on_log_entry)

        self.protocol("WM_DELETE_WINDOW", self._on_close_request)
        self.bind("<Unmap>", self._on_unmap)

        # Initial state sync
        self._apply_status(self._manager.status, "Ready")

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self) -> None:
        self.title("MediaForge Companion")
        self.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.resizable(False, False)
        self.configure(fg_color=_CLR_BG)

        # Try to set window icon
        try:
            icon_path = _resource_path("icon.ico")
            import os
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ── Header ───────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(20, 0))

        logo_lbl = ctk.CTkLabel(
            header,
            text="⚡  MediaForge Companion",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color=_CLR_TEXT,
        )
        logo_lbl.pack(side="left")

        self._version_lbl = ctk.CTkLabel(
            header,
            text="v—",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=_CLR_SUBTEXT,
        )
        self._version_lbl.pack(side="right", pady=4)

        # ── Separator ────────────────────────────────────────────────────
        sep = ctk.CTkFrame(self, height=1, fg_color=_CLR_BORDER)
        sep.pack(fill="x", padx=20, pady=(12, 0))

        # ── Status card ──────────────────────────────────────────────────
        status_card = _Card(self)
        status_card.pack(fill="x", padx=20, pady=(14, 0))

        status_inner = ctk.CTkFrame(status_card, fg_color="transparent")
        status_inner.pack(fill="x", padx=16, pady=14)

        # Left: status
        left_col = ctk.CTkFrame(status_inner, fg_color="transparent")
        left_col.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            left_col,
            text="Backend Status",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_CLR_SUBTEXT,
        ).pack(anchor="w")

        self._status_lbl = ctk.CTkLabel(
            left_col,
            text="● Stopped",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=_CLR_RED,
        )
        self._status_lbl.pack(anchor="w", pady=(2, 0))

        # Right: port
        right_col = ctk.CTkFrame(status_inner, fg_color="transparent")
        right_col.pack(side="right")

        ctk.CTkLabel(
            right_col,
            text="Port",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_CLR_SUBTEXT,
        ).pack(anchor="e")

        self._port_lbl = ctk.CTkLabel(
            right_col,
            text=str(self._manager.port),
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=_CLR_TEXT,
        )
        self._port_lbl.pack(anchor="e", pady=(2, 0))

        # ── Action buttons card ───────────────────────────────────────────
        btn_card = _Card(self)
        btn_card.pack(fill="x", padx=20, pady=(10, 0))

        ctk.CTkLabel(
            btn_card,
            text="Controls",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_CLR_SUBTEXT,
        ).pack(anchor="w", padx=16, pady=(12, 6))

        btn_grid = ctk.CTkFrame(btn_card, fg_color="transparent")
        btn_grid.pack(fill="x", padx=12, pady=(0, 12))

        # Row 1: Start / Stop / Restart
        row1 = ctk.CTkFrame(btn_grid, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 6))

        self._start_btn = self._make_btn(
            row1, "▶  Start", _CLR_GREEN, "#16a34a", self._action_start
        )
        self._start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self._stop_btn = self._make_btn(
            row1, "■  Stop", _CLR_RED, "#b91c1c", self._action_stop
        )
        self._stop_btn.pack(side="left", expand=True, fill="x", padx=(4, 4))

        self._restart_btn = self._make_btn(
            row1, "↻  Restart", _CLR_CARD, _CLR_BORDER, self._action_restart,
            text_color=_CLR_TEXT,
        )
        self._restart_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        # Row 2: Open Backend / View Logs / Exit
        row2 = ctk.CTkFrame(btn_grid, fg_color="transparent")
        row2.pack(fill="x")

        self._open_btn = self._make_btn(
            row2, "🌐  Open Backend", _CLR_ACCENT, _CLR_ACCENT_HOV, self._action_open
        )
        self._open_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        logs_btn = self._make_btn(
            row2, "📜  View Logs", _CLR_CARD, _CLR_BORDER, self._action_view_logs,
            text_color=_CLR_TEXT,
        )
        logs_btn.pack(side="left", expand=True, fill="x", padx=(4, 4))

        exit_btn = self._make_btn(
            row2, "❌  Exit", _CLR_CARD, _CLR_BORDER, self._on_close_request,
            text_color=_CLR_TEXT,
        )
        exit_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        # ── Inline Log Panel ─────────────────────────────────────────────
        log_card = _Card(self)
        log_card.pack(fill="both", expand=True, padx=20, pady=(10, 0))

        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.pack(fill="x", padx=14, pady=(10, 4))

        ctk.CTkLabel(
            log_header,
            text="Recent Activity",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_CLR_SUBTEXT,
        ).pack(side="left")

        self._log_box = ctk.CTkTextbox(
            log_card,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=_CLR_SURFACE,
            text_color=_CLR_TEXT,
            border_color=_CLR_BORDER,
            border_width=1,
            corner_radius=6,
            wrap="word",
            state="disabled",
            height=160,
        )
        self._log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # ── Status bar ───────────────────────────────────────────────────
        status_bar = ctk.CTkFrame(self, fg_color=_CLR_SURFACE, height=28, corner_radius=0)
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)

        self._statusbar_lbl = ctk.CTkLabel(
            status_bar,
            text="Ready",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_CLR_SUBTEXT,
            anchor="w",
        )
        self._statusbar_lbl.pack(side="left", padx=12, fill="y")

        host_lbl = ctk.CTkLabel(
            status_bar,
            text=f"{self._manager.host}:{self._manager.port}",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_CLR_SUBTEXT,
            anchor="e",
        )
        host_lbl.pack(side="right", padx=12, fill="y")

    # ------------------------------------------------------------------
    # Button factory
    # ------------------------------------------------------------------

    @staticmethod
    def _make_btn(
        parent,
        text: str,
        fg: str,
        hover: str,
        command,
        *,
        text_color: str = "#ffffff",
        height: int = 38,
    ) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=text,
            height=height,
            fg_color=fg,
            hover_color=hover,
            text_color=text_color,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            corner_radius=8,
            command=command,
        )

    # ------------------------------------------------------------------
    # Action handlers (all dispatch to threads)
    # ------------------------------------------------------------------

    def _action_start(self) -> None:
        self._set_buttons_busy()
        threading.Thread(target=self._manager.start, daemon=True).start()

    def _action_stop(self) -> None:
        self._set_buttons_busy()
        threading.Thread(target=self._manager.stop, daemon=True).start()

    def _action_restart(self) -> None:
        self._set_buttons_busy()
        threading.Thread(target=self._manager.restart, daemon=True).start()

    def _action_open(self) -> None:
        webbrowser.open(self._manager.base_url)

    def _action_view_logs(self) -> None:
        if self._log_viewer and self._log_viewer.winfo_exists():
            self._log_viewer.lift()
            return
        self._log_viewer = LogViewerWindow(self, self._logger)

    # ------------------------------------------------------------------
    # Status updates (called from BackendManager – may be non-UI thread)
    # ------------------------------------------------------------------

    def _on_status_change(self, status: BackendStatus, message: str) -> None:
        self.after(0, self._apply_status, status, message)

    def _apply_status(self, status: BackendStatus, message: str) -> None:
        """Must be called on the UI thread."""
        self._current_status = status
        color = _STATUS_COLOR[status]
        is_managed = self._manager.is_managed()

        if status == BackendStatus.RUNNING and not is_managed:
            label = "● Running (External)"
        else:
            label = _STATUS_LABEL[status]

        self._status_lbl.configure(text=label, text_color=color)

        # For externally adopted backends, override the message to be descriptive
        if status == BackendStatus.RUNNING and not is_managed:
            display_message = "External backend detected – monitoring only."
        else:
            display_message = message
        self._statusbar_lbl.configure(text=display_message)

        # Button states
        is_stopped_or_crashed = status in (BackendStatus.STOPPED, BackendStatus.CRASHED)
        is_running = status == BackendStatus.RUNNING

        self._start_btn.configure(state="normal" if is_stopped_or_crashed else "disabled")
        self._stop_btn.configure(state="normal" if (is_running and is_managed) else "disabled")
        self._restart_btn.configure(state="normal" if (is_running and is_managed) else "disabled")
        self._open_btn.configure(state="normal" if is_running else "disabled")

        # Fetch version from API when running
        if is_running:
            threading.Thread(target=self._refresh_version, daemon=True).start()
        else:
            self._version_lbl.configure(text="v—")

    # ------------------------------------------------------------------
    # Log updates
    # ------------------------------------------------------------------

    def _on_log_entry(self, entry: LogEntry) -> None:
        """Append a log entry to the inline log panel (thread-safe)."""
        try:
            self.after(0, self._append_log, entry)
        except Exception:
            pass

    def _append_log(self, entry: LogEntry) -> None:
        self._log_box.configure(state="normal")
        ts_short = entry.timestamp[11:]   # HH:MM:SS only for inline panel
        self._log_box.insert("end", f"[{ts_short}] {entry.message}\n")
        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    # ------------------------------------------------------------------
    # Version refresh
    # ------------------------------------------------------------------

    def _refresh_version(self) -> None:
        version = self._manager.fetch_version()
        display = f"v{version}" if version else "v—"
        try:
            self.after(0, self._version_lbl.configure, {"text": display})
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Button busy state
    # ------------------------------------------------------------------

    def _set_buttons_busy(self) -> None:
        for btn in (self._start_btn, self._stop_btn, self._restart_btn):
            btn.configure(state="disabled")

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def _on_close_request(self) -> None:
        """
        Called when the user presses the X button or the Exit button.

        If the backend is running and managed, show a confirmation dialog.
        Otherwise, hide to tray if active, or exit if inactive.
        """
        is_running = self._current_status in (BackendStatus.RUNNING, BackendStatus.STARTING)
        is_managed = self._manager.is_managed()

        if is_running and is_managed:
            self._show_shutdown_dialog()
        else:
            if self.tray_active:
                self.withdraw()
                if self._tray_manager:
                    self._tray_manager.notify_background()
            else:
                self._do_exit(stop_backend=False)

    def _show_shutdown_dialog(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Confirm Exit")
        dialog.geometry("360x200")
        dialog.resizable(False, False)
        dialog.configure(fg_color=_CLR_SURFACE)
        dialog.grab_set()
        dialog.focus_set()
        dialog.after(100, dialog.lift)

        ctk.CTkLabel(
            dialog,
            text="The backend is still running.",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=_CLR_TEXT,
        ).pack(pady=(24, 4))

        ctk.CTkLabel(
            dialog,
            text="What would you like to do?",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=_CLR_SUBTEXT,
        ).pack(pady=(0, 16))

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20)

        def stop_and_exit():
            dialog.destroy()
            self._do_exit(stop_backend=True)

        def exit_only():
            dialog.destroy()
            if self.tray_active:
                self.withdraw()
                if self._tray_manager:
                    self._tray_manager.notify_background()
            else:
                self._do_exit(stop_backend=False)

        def cancel():
            dialog.destroy()

        ctk.CTkButton(
            btn_frame,
            text="Stop & Exit",
            fg_color=_CLR_RED,
            hover_color="#b91c1c",
            height=36,
            corner_radius=8,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=stop_and_exit,
        ).pack(side="left", expand=True, fill="x", padx=(0, 4))

        ctk.CTkButton(
            btn_frame,
            text="Exit Only",
            fg_color=_CLR_CARD,
            hover_color=_CLR_BORDER,
            text_color=_CLR_TEXT,
            height=36,
            corner_radius=8,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=exit_only,
        ).pack(side="left", expand=True, fill="x", padx=(4, 4))

        ctk.CTkButton(
            btn_frame,
            text="Cancel",
            fg_color=_CLR_ACCENT,
            hover_color=_CLR_ACCENT_HOV,
            height=36,
            corner_radius=8,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=cancel,
        ).pack(side="left", expand=True, fill="x", padx=(4, 0))

    def _do_exit(self, *, stop_backend: bool) -> None:
        self._manager.shutdown()
        if stop_backend and self._manager.is_managed():
            # Run in thread so UI stays responsive during stop
            def _stop_then_destroy():
                self._manager.stop()
                self.after(0, self.destroy)
            threading.Thread(target=_stop_then_destroy, daemon=True).start()
        else:
            self.destroy()

    def _on_unmap(self, event) -> None:
        """Called on window state changes (e.g. minimizing)."""
        if event.widget == self:
            # Minimize Detection Clarification: only iconic state triggers tray hide
            if self.state() == "iconic" and self.tray_active:
                self.withdraw()
                if self._tray_manager:
                    self._tray_manager.notify_background()

    def set_tray_manager(self, tray_manager: TrayManager) -> None:
        """Register the TrayManager instance."""
        self._tray_manager = tray_manager

    def restore_window(self) -> None:
        """Restore and focus the main window (thread-safe)."""
        def _do_restore():
            self.deiconify()
            self.state("normal")
            self.focus_force()
            self.lift()
        self.after(0, _do_restore)

    def trigger_start(self) -> None:
        """Tray shortcut to trigger managed backend startup."""
        self._action_start()

    def trigger_stop(self) -> None:
        """Tray shortcut to trigger managed backend stop."""
        self._action_stop()

    def trigger_restart(self) -> None:
        """Tray shortcut to trigger managed backend restart."""
        self._action_restart()

    def exit_completely(self) -> None:
        """Exit the entire application completely, bypassing hide-to-tray."""
        if self._tray_manager:
            self._tray_manager.stop()
        self._do_exit(stop_backend=True)


# ---------------------------------------------------------------------------
# Resource helper
# ---------------------------------------------------------------------------

def _resource_path(filename: str) -> str:
    """Return the absolute path to a file in companion/resources/."""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "resources", filename)
