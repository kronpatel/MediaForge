"""
dashboard.py – DashboardController & DashboardPage

1. DashboardController: Single background polling manager that polls the backend
   and broadcasts state updates to subscribed pages.
2. DashboardPage: Landing panel displaying backend health summary, current active
   download status, progress bar, and recent logs.
3. UpdateDialog: Modal dialog for viewing and managing companion updates.
"""

from __future__ import annotations

import threading
import time
import webbrowser
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from base_page import BasePage
from backend_manager import BackendStatus
from notifications import CATEGORY_BACKEND_CRASHED, PRIORITY_HIGH, SOURCE_DASHBOARD, get_notification_manager

if TYPE_CHECKING:
    from backend_manager import BackendManager
    from logger import AppLogger


# ---------------------------------------------------------------------------
# Update Bell color constants
# ---------------------------------------------------------------------------

_BELL_COLOR_LATEST = "#22c55e"     # Green  – running latest version
_BELL_COLOR_UPDATE = "#f59e0b"     # Orange – update available
_BELL_COLOR_FAILED = "#ef4444"     # Red    – update download failed
_BELL_COLOR_CHECKING = "#8b92a8"   # Grey   – checking for updates
_BELL_COLOR_IDLE = "#4f8ef7"       # Blue   – idle / not yet checked


# ---------------------------------------------------------------------------
# Dashboard Polling Controller (Unified Poller)
# ---------------------------------------------------------------------------

class DashboardController:
    """
    Central background poller for Companion Phase 3.
    Ensures exactly one background thread communicates with the backend,
    distributing states thread-safely to registered UI sub-pages.
    """

    def __init__(self, manager: BackendManager, logger: AppLogger) -> None:
        self._manager = manager
        self._logger = logger
        self._poll_interval: float = 3.0
        self._subscribers: list[BasePage] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._poll_event = threading.Event()
        self._lock = threading.Lock()
        self._last_data: dict[str, Any] = {"offline": True}
        self._notification_sent: bool = False

    def register_page(self, page: BasePage) -> None:
        with self._lock:
            self._subscribers.append(page)

    def set_poll_interval(self, seconds: float) -> None:
        with self._lock:
            self._poll_interval = max(1.0, min(seconds, 60.0))

    def trigger_poll(self) -> None:
        """Force an immediate poll tick, waking up the sleep block if waiting."""
        self._poll_event.set()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._poll_event.clear()
            self._thread = threading.Thread(
                target=self._poll_loop,
                name="UnifiedPollerThread",
                daemon=True,
            )
            self._thread.start()
            self._logger.info("[Dashboard] Unified background polling thread started.")

    def shutdown(self) -> None:
        self._stop_event.set()
        self._poll_event.set()  # wake up poller if waiting
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._logger.info("[Dashboard] Unified background polling thread stopped.")

    def get_cached_data(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_data)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self._poll_event.clear()
            if self._stop_event.is_set():
                break
                
            status = self._manager.status
            
            if status == BackendStatus.RUNNING:
                try:
                    queue = self._manager.get_queue()
                    history = self._manager.get_history()
                    stats = self._manager.get_stats()
                    settings = self._manager.get_settings()
                    
                    data = {
                        "offline": False,
                        "status": status,
                        "queue": queue,
                        "history": history,
                        "stats": stats,
                        "settings": settings,
                    }
                    self._notification_sent = False
                    self._broadcast(data)
                except Exception as exc:
                    self._logger.debug_log(f"[Dashboard] Unified poll fetch failed: {exc}")
                    self._handle_offline()
            else:
                self._handle_offline()

            # Wait for next interval or immediate trigger
            if self._stop_event.is_set():
                break
            self._poll_event.wait(self._poll_interval)

    def _handle_offline(self) -> None:
        data = {
            "offline": True,
            "status": self._manager.status,
            "queue": [],
            "history": [],
            "stats": {},
            "settings": {},
        }
        self._broadcast(data)
        
        # Single notification if we just lost connection
        if not self._notification_sent and self._manager.status == BackendStatus.CRASHED:
            try:
                get_notification_manager().publish(
                    category=CATEGORY_BACKEND_CRASHED,
                    title="MediaForge Companion",
                    message="Backend offline. Companion has switched to offline mode.",
                    source=SOURCE_DASHBOARD,
                    priority=PRIORITY_HIGH,
                )
            except Exception:
                pass
            self._notification_sent = True

    def _broadcast(self, data: dict[str, Any]) -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            self._last_data = data
            subs = list(self._subscribers)
        
        try:
            if self._window_ref:
                # Route the entire broadcast onto the Tkinter main thread for absolute thread-safety
                self._window_ref.after(0, self._safe_broadcast_on_main, data, subs)
        except Exception:
            pass

    def _safe_broadcast_on_main(self, data: dict[str, Any], subs: list[Any]) -> None:
        """Updates pages. Runs entirely on the Tkinter main thread."""
        for sub in subs:
            try:
                if sub.winfo_exists():
                    sub.refresh(data)
            except Exception:
                pass

    def associate_window(self, window: Any) -> None:
        """Stores a reference to UI window to support tray notifications."""
        self._window_ref = window


# ---------------------------------------------------------------------------
# Dashboard Panel Frame
# ---------------------------------------------------------------------------

class DashboardPage(BasePage):
    """
    Main dashboard page displaying cards, active download progress bar,
    and trailing inline activity logs.
    """

    def __init__(self, master: ctk.CTk, manager: BackendManager, logger: AppLogger) -> None:
        super().__init__(master, manager, logger)
        self.updater = None
        self._updater_cb_registered = False
        self.scheduler = None
        self._cached_hash = None
        self._cached_version: str | None = None  # avoid per-poll HTTP round trip
        self._log_count: int = 0               # for incremental log appending
        self._cached_dl: dict[str, Any] = {}   # cached active download values
        self._build_ui()

    def _build_ui(self) -> None:
        # ── Page header ────────────────────────────────────────────────────
        hdr_frame = ctk.CTkFrame(self, fg_color="transparent")
        hdr_frame.pack(fill="x", padx=20, pady=(20, 4))

        ctk.CTkLabel(
            hdr_frame,
            text="Companion Dashboard",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#e8eaf0",
        ).pack(side="left", anchor="w")

        # ── Update Bell (top-right corner) ─────────────────────────────────
        self._bell_btn = ctk.CTkButton(
            hdr_frame,
            text="\U0001f514",
            width=36,
            height=36,
            corner_radius=18,
            fg_color=_BELL_COLOR_CHECKING,
            hover_color="#2e3347",
            font=ctk.CTkFont(family="Segoe UI", size=16),
            text_color="#ffffff",
            command=self._open_update_dialog,
        )
        self._bell_btn.pack(side="right", padx=(8, 0))

        ctk.CTkLabel(
            self,
            text="Overview of backend services, active downloads, and logs.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
        ).pack(anchor="w", padx=20, pady=(0, 14))

        # ── Grid of Info Cards ─────────────────────────────────────────────
        self._cards_parent_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._cards_parent_frame.pack(fill="x", padx=20, pady=(0, 10))

        self._status_card    = self._create_card(self._cards_parent_frame, "Backend Status",  "Loading\u2026", "#f59e0b")
        self._version_card   = self._create_card(self._cards_parent_frame, "Backend Version", "v\u2014",   "#8b92a8")
        self._uptime_card    = self._create_card(self._cards_parent_frame, "Backend Uptime",  "0s",     "#8b92a8")
        self._queue_card     = self._create_card(self._cards_parent_frame, "Queue Size",      "0",      "#8b92a8")

        # ── Companion Update card ──────────────────────────────────────────
        self._update_card_frame = ctk.CTkFrame(
            self._cards_parent_frame,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        _uc = ctk.CTkFrame(self._update_card_frame, fg_color="transparent")
        _uc.pack(padx=14, pady=14, fill="both", expand=True)
        ctk.CTkLabel(
            _uc,
            text="Companion Update",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#8b92a8",
        ).pack(anchor="w")
        self._update_status_lbl = ctk.CTkLabel(
            _uc,
            text="Checking\u2026",
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color="#4f8ef7",
            wraplength=160,
            justify="left",
        )
        self._update_status_lbl.pack(anchor="w", pady=(6, 0))
        self._update_versions_lbl = ctk.CTkLabel(
            _uc,
            text="v\u2014 \u2192 v\u2014",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._update_versions_lbl.pack(anchor="w", pady=(3, 0))

        # ── Upcoming Schedule card ─────────────────────────────────────────
        self._scheduler_card_frame = ctk.CTkFrame(
            self._cards_parent_frame,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        _sc = ctk.CTkFrame(self._scheduler_card_frame, fg_color="transparent")
        _sc.pack(padx=14, pady=14, fill="both", expand=True)
        ctk.CTkLabel(
            _sc,
            text="Upcoming Schedule",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#8b92a8",
        ).pack(anchor="w")
        self._sched_next_run_lbl = ctk.CTkLabel(
            _sc,
            text="Loading\u2026",
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color="#f59e0b",
            wraplength=160,
            justify="left",
        )
        self._sched_next_run_lbl.pack(anchor="w", pady=(6, 0))
        self._sched_details_lbl = ctk.CTkLabel(
            _sc,
            text="\u2014",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._sched_details_lbl.pack(anchor="w", pady=(3, 0))

        self._cards_list = [
            self._status_card["card"],
            self._version_card["card"],
            self._uptime_card["card"],
            self._queue_card["card"],
            self._update_card_frame,
            self._scheduler_card_frame,
        ]
        self._resize_timer: str | None = None
        self._last_width_bucket: int = 0
        self.bind("<Configure>", self._on_resize)

        # ── Active Download Panel ──────────────────────────────────────────
        self._active_card = ctk.CTkFrame(
            self,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        self._active_card.pack(fill="x", padx=20, pady=(0, 10))

        self._active_inner = ctk.CTkFrame(self._active_card, fg_color="transparent")
        self._active_inner.pack(fill="x", padx=16, pady=14)

        ctk.CTkLabel(
            self._active_inner,
            text="Active Download",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#8b92a8",
        ).pack(anchor="w")

        # Empty-state container (shown when idle)
        self._empty_dl_frame = ctk.CTkFrame(self._active_inner, fg_color="transparent")
        self._empty_dl_frame.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(
            self._empty_dl_frame,
            text="\u2b07",
            font=ctk.CTkFont(family="Segoe UI", size=28),
            text_color="#2e3347",
        ).pack()
        ctk.CTkLabel(
            self._empty_dl_frame,
            text="No active downloads",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#8b92a8",
        ).pack(pady=(4, 2))
        ctk.CTkLabel(
            self._empty_dl_frame,
            text="Downloads will appear here once started.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#5a6072",
        ).pack()

        # Active-state container (shown during download)
        self._active_dl_frame = ctk.CTkFrame(self._active_inner, fg_color="transparent")
        # Do NOT pack yet — shown only when download is active

        self._title_lbl = ctk.CTkLabel(
            self._active_dl_frame,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#e8eaf0",
            anchor="w",
        )
        self._title_lbl.pack(anchor="w", pady=(4, 2))

        self._mode_lbl = ctk.CTkLabel(
            self._active_dl_frame,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
            anchor="w",
        )
        self._mode_lbl.pack(anchor="w", pady=(0, 6))

        self._progress_bar = ctk.CTkProgressBar(
            self._active_dl_frame,
            fg_color="#20232f",
            progress_color="#4f8ef7",
            height=8,
        )
        self._progress_bar.pack(fill="x", pady=(0, 8))
        self._progress_bar.set(0.0)

        _meta = ctk.CTkFrame(self._active_dl_frame, fg_color="transparent")
        _meta.pack(fill="x")
        self._speed_lbl = ctk.CTkLabel(
            _meta, text="Speed: \u2014",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._speed_lbl.pack(side="left")
        self._eta_lbl = ctk.CTkLabel(
            _meta, text="ETA: \u2014",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._eta_lbl.pack(side="right")

        # Track empty/active state to avoid redundant frame swaps
        self._dl_state_active: bool = False

        # ── Logs & Activity ────────────────────────────────────────────────
        logs_card = ctk.CTkFrame(
            self,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        logs_card.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        # Logs header row with Clear View button
        _logs_hdr = ctk.CTkFrame(logs_card, fg_color="transparent")
        _logs_hdr.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(
            _logs_hdr,
            text="Recent Activity Logs",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#8b92a8",
        ).pack(side="left")
        ctk.CTkButton(
            _logs_hdr,
            text="Clear View",
            width=80,
            height=24,
            corner_radius=6,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#8b92a8",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            command=self._clear_log_view,
        ).pack(side="right")

        self._log_textbox = ctk.CTkTextbox(
            logs_card,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#0f1117",
            text_color="#c9d1d9",
            border_color="#2e3347",
            border_width=1,
            corner_radius=8,
            wrap="none",
            state="disabled",
        )
        self._log_textbox.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _create_card(self, parent: ctk.CTkFrame, title: str, val: str, color: str) -> dict[str, Any]:
        card = ctk.CTkFrame(
            parent,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=14, pady=14, fill="both", expand=True)
        ctk.CTkLabel(
            inner,
            text=title,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#8b92a8",
        ).pack(anchor="w")
        val_lbl = ctk.CTkLabel(
            inner,
            text=val,
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color=color,
        )
        val_lbl.pack(anchor="w", pady=(6, 0))
        return {"card": card, "lbl": val_lbl, "default_color": color}

    def _on_resize(self, event) -> None:
        if event.widget != self:
            return
        new_width = self.winfo_width()
        bucket = 3 if new_width > 900 else (2 if new_width > 600 else 1)
        if bucket == self._last_width_bucket:
            return
        self._last_width_bucket = bucket
        if self._resize_timer is not None:
            try:
                self.after_cancel(self._resize_timer)
            except Exception:
                pass
        self._resize_timer = self.after(100, self._grid_cards_responsive)

    def _grid_cards_responsive(self) -> None:
        self._resize_timer = None
        width = self.winfo_width()
        cols = 3 if width > 900 else (2 if width > 600 else 1)
        
        # Avoid redundant grid configurations
        if getattr(self, "_current_grid_cols", None) == cols:
            return
        self._current_grid_cols = cols
        
        # Reset all possible columns configuration (max 6)
        for i in range(6):
            self._cards_parent_frame.grid_columnconfigure(i, weight=0, uniform="")
            
        for i in range(cols):
            self._cards_parent_frame.grid_columnconfigure(i, weight=1, uniform="equal")
            
        for i, card in enumerate(self._cards_list):
            card.grid(row=i // cols, column=i % cols, padx=4, pady=4, sticky="nsew")

    # ------------------------------------------------------------------
    # Updater Callbacks & UI Mapping
    # ------------------------------------------------------------------

    def wire_updater(self, updater) -> None:
        """Register updater callback and refresh UI. Safe to call multiple times."""
        if self._updater_cb_registered and self.updater is updater:
            return
        if self._updater_cb_registered and self.updater is not None:
            try:
                self.updater.unregister_callback(self._on_update_status)
            except Exception:
                pass
            self._updater_cb_registered = False
        self.updater = updater
        if self.updater:
            self.updater.register_callback(self._on_update_status)
            self._updater_cb_registered = True
            self._refresh_updater_ui()

    def _refresh_updater_ui(self) -> None:
        """Refresh the update card and bell with current updater state."""
        if not self.updater:
            return
        status = self.updater.get_status()
        self._update_card_ui(status, 100.0 if self.updater._pending_install else 0.0)

    def on_show(self) -> None:
        main_window = self.master.master
        updater = getattr(main_window, "updater", None)
        if updater:
            self.wire_updater(updater)

        self.scheduler = getattr(main_window, "scheduler", None)
        if self.scheduler:
            self.scheduler.register_listener(self._on_scheduler_event)

        self._populate_logs()
        self._update_scheduler_countdown()

    def on_hide(self) -> None:
        if self._updater_cb_registered and self.updater:
            try:
                self.updater.unregister_callback(self._on_update_status)
            except Exception:
                pass
            self._updater_cb_registered = False
        if hasattr(self, "scheduler") and self.scheduler:
            self.scheduler.unregister_listener(self._on_scheduler_event)

    def _on_scheduler_event(self, name: str, payload: dict) -> None:
        try:
            self.after(0, self._force_update_countdown)
        except Exception:
            pass

    def _force_update_countdown(self) -> None:
        self._update_scheduler_countdown()

    def _on_update_status(self, status: str, progress: float, error_msg: str | None = None) -> None:
        try:
            self.after(0, self._update_card_ui, status, progress)
        except Exception:
            pass

    def _update_card_ui(self, status: str, progress: float) -> None:
        if not self.updater:
            main_window = self.master.master
            self.updater = getattr(main_window, "updater", None)
        if not self.updater:
            return

        current = self.updater.get_current_version()
        latest = self.updater.get_latest_version()
        has_up = self.updater.has_update()

        is_pending = False
        subtitle_text = ""
        if status == "Checking":
            status_text = "Checking..."
            color = "#4f8ef7"
        elif status == "Downloading":
            status_text = f"Downloading ({int(progress)}%)"
            color = "#f59e0b"
        elif status == "Verifying":
            status_text = "Verifying..."
            color = "#f59e0b"
        elif status == "Completed":
            status_text = "Installation Complete"
            color = "#22c55e"
            is_pending = True
            subtitle_text = "MediaForge update completed successfully!"
        elif status == "Pending Install":
            status_text = "Ready to Install"
            color = "#22c55e"
            is_pending = True
            subtitle_text = "Installer downloaded successfully"
        elif status == "Launching":
            status_text = "Installing..."
            color = "#f59e0b"
            is_pending = True
            subtitle_text = "Starting the installer process..."
        elif status == "Waiting For Exit":
            status_text = "Waiting For Installer"
            color = "#f59e0b"
            is_pending = True
            subtitle_text = "Installer running. Please complete instructions..."
        elif status == "Failed":
            if getattr(self.updater, "_installer_state", "Idle") == "Failed":
                status_text = "Installation Failed"
                subtitle_text = "Installation failed or verification error."
            else:
                status_text = "❌ Check Failed"
            color = "#ef4444"
            is_pending = True if subtitle_text else False
        elif status == "Cancelled":
            status_text = "Installation Cancelled"
            color = "#ef4444"
            is_pending = True
            subtitle_text = "Installation was cancelled by the user."
        elif status == "Restarting Companion":
            status_text = "Restarting Companion"
            color = "#22c55e"
            is_pending = True
            subtitle_text = "Restarting application..."
        elif status == "Offline":
            status_text = "⚠ Offline"
            color = "#f59e0b"
        elif status == "Rate Limited":
            status_text = "Rate Limited"
            color = "#f59e0b"
        elif status == "Installer Not Found":
            status_text = "⚠ Installer Not Found"
            color = "#ef4444"
        else:
            if has_up:
                status_text = "⬇ Update Available"
                color = "#f59e0b"
            else:
                status_text = "✓ Up To Date"
                color = "#22c55e"

        self._update_status_lbl.configure(text=status_text, text_color=color)
        if is_pending and subtitle_text:
            self._update_versions_lbl.configure(text=subtitle_text)
        elif is_pending:
            self._update_versions_lbl.configure(text="Installer downloaded successfully")
        else:
            self._update_versions_lbl.configure(text=f"v{current} → {latest}")

        # Update the bell icon color to match status
        self._update_bell_color(status)

    # ------------------------------------------------------------------
    # Update Bell
    # ------------------------------------------------------------------

    def _update_bell_color(self, status: str) -> None:
        """Set the update bell color based on the current updater status."""
        if not hasattr(self, "_bell_btn"):
            return
        color = _BELL_COLOR_CHECKING
        if status in ("Up To Date", "Completed"):
            color = _BELL_COLOR_LATEST
        elif status in ("Update Available", "Downloading", "Verifying", "Pending Install",
                         "Launching", "Waiting For Exit", "Restarting Companion"):
            color = _BELL_COLOR_UPDATE
        elif status in ("Failed", "Cancelled", "Installer Not Found"):
            color = _BELL_COLOR_FAILED
        elif status == "Checking":
            color = _BELL_COLOR_CHECKING
        elif status in ("Offline", "Rate Limited"):
            color = _BELL_COLOR_FAILED
        else:
            color = _BELL_COLOR_IDLE
        try:
            self._bell_btn.configure(fg_color=color)
        except Exception:
            pass

    def _open_update_dialog(self) -> None:
        """Open the Update Dialog modal."""
        main_window = self.master.master
        updater = getattr(main_window, "updater", None)
        if not updater:
            return
        # Prevent opening duplicate update dialogs
        if hasattr(self, "_update_dialog") and self._update_dialog and self._update_dialog._dialog.winfo_exists():
            self._update_dialog._dialog.lift()
            self._update_dialog._dialog.focus_force()
            return
        self._update_dialog = UpdateDialog(main_window, updater, self.logger)

    # ------------------------------------------------------------------
    # Lifecycle refresh
    # ------------------------------------------------------------------

    def refresh(self, data: dict[str, Any]) -> None:
        """Invoked on the Tkinter thread with polled backend state."""
        current_hash = hash(str(data))
        if current_hash == self._cached_hash:
            return
        self._cached_hash = current_hash

        offline = data.get("offline", True)

        # 1. Status card
        if offline:
            status_text = "Offline"
            status_color = "#ef4444"
        else:
            status_text = "Running" if self.manager.is_managed() else "Running (Ext)"
            status_color = "#22c55e"
        self._status_card["lbl"].configure(text=status_text, text_color=status_color)

        # 2. Version card — only re-fetch on first online tick (cached afterwards)
        if not offline and self._cached_version is None:
            self._cached_version = self.manager.fetch_version()
        if offline:
            self._cached_version = None
        self._version_card["lbl"].configure(
            text=f"v{self._cached_version}" if self._cached_version else "v—"
        )

        # 3. Uptime card
        stats = data.get("stats", {})
        uptime_sec = stats.get("backend_uptime", 0)
        self._uptime_card["lbl"].configure(text=self._format_uptime(uptime_sec) if uptime_sec else "0s")

        # 4. Queue Size card
        queue = data.get("queue", [])
        self._queue_card["lbl"].configure(text=str(len(queue)))

        # 5. Active download block (with value caching)
        active_job = next(
            (job for job in queue if job.get("status") == "downloading"),
            None
        )

        if active_job:
            if not self._dl_state_active:
                self._empty_dl_frame.pack_forget()
                self._active_dl_frame.pack(fill="x", pady=(8, 0))
                self._dl_state_active = True
                self._cached_dl.clear()
            mode = active_job.get("mode", "video")
            mode_icons = {"video": "\U0001f3ac Video", "audio": "\U0001f3b5 Audio"}
            new_title = active_job.get("label") or active_job.get("filename") or "Downloading\u2026"
            if self._cached_dl.get("title") != new_title:
                self._title_lbl.configure(text=new_title)
                self._cached_dl["title"] = new_title
            new_mode = mode_icons.get(mode, mode.capitalize())
            if self._cached_dl.get("mode") != new_mode:
                self._mode_lbl.configure(text=new_mode)
                self._cached_dl["mode"] = new_mode
            new_progress = float(active_job.get("progress") or 0.0) / 100.0
            if self._cached_dl.get("progress") != new_progress:
                self._progress_bar.set(new_progress)
                self._cached_dl["progress"] = new_progress
            raw_speed = active_job.get("speed") or ""
            speed_badge = f"Speed: \u2b07 {raw_speed}" if raw_speed and raw_speed != "\u2014" else f"Speed: \u2014"
            if self._cached_dl.get("speed") != speed_badge:
                self._speed_lbl.configure(text=speed_badge)
                self._cached_dl["speed"] = speed_badge
            new_eta = f"ETA: {active_job.get('eta') or '\u2014'}"
            if self._cached_dl.get("eta") != new_eta:
                self._eta_lbl.configure(text=new_eta)
                self._cached_dl["eta"] = new_eta

            # Queue overall progress info
            completed = sum(1 for j in queue if j.get("status") == "completed")
            total = len(queue)
            self._queue_card["lbl"].configure(text=f"{completed}/{total}")
        else:
            if self._dl_state_active:
                self._active_dl_frame.pack_forget()
                self._empty_dl_frame.pack(fill="x", pady=(8, 0))
                self._dl_state_active = False
                self._cached_dl.clear()
            if self._cached_dl.get("progress") != 0.0:
                self._progress_bar.set(0.0)
                self._cached_dl["progress"] = 0.0
            new_speed = "Speed: \u2014"
            if self._cached_dl.get("speed") != new_speed:
                self._speed_lbl.configure(text=new_speed)
                self._cached_dl["speed"] = new_speed
            new_eta = "ETA: \u2014"
            if self._cached_dl.get("eta") != new_eta:
                self._eta_lbl.configure(text=new_eta)
                self._cached_dl["eta"] = new_eta

        # 6. Incremental log append
        self._append_new_logs()


    def _clear_log_view(self) -> None:
        """Clear the visible log textbox without removing stored log entries."""
        self._log_textbox.configure(state="normal")
        self._log_textbox.delete("1.0", "end")
        self._log_textbox.insert("end", "Log view cleared. New entries will appear below.\n")
        self._log_textbox.configure(state="disabled")
        # Reset counter so next append reloads from current position
        self._log_count = len(self.logger.get_entries())

    def _populate_logs(self) -> None:
        """Full rewrite — used on page show or when count drops (e.g. after clear)."""
        entries = self.logger.get_entries()
        self._log_textbox.configure(state="normal")
        self._log_textbox.delete("1.0", "end")
        if not entries:
            self._log_textbox.insert("end", "System ready. No activity logs recorded yet.\n")
            self._log_count = 0
        else:
            for entry in entries[-50:]:
                self._log_textbox.insert("end", str(entry) + "\n")
            self._log_count = len(entries)
        self._log_textbox.configure(state="disabled")
        self._log_textbox.see("end")

    def _append_new_logs(self) -> None:
        """Append only newly-added log entries; preserve user scroll position."""
        entries = self.logger.get_entries()
        current_count = len(entries)
        if current_count == 0:
            self._populate_logs()
            return

        if self._log_count == 0 and current_count > 0:
            self._populate_logs()
            return

        if current_count <= self._log_count:
            # Count dropped (e.g. logs cleared) — do a full repopulate
            if current_count < self._log_count:
                self._populate_logs()
            return

        new_entries = entries[self._log_count:]
        # Auto-scroll only if user is already at (or near) the bottom
        try:
            at_bottom = self._log_textbox.yview()[1] >= 0.95
        except Exception:
            at_bottom = True

        self._log_textbox.configure(state="normal")
        for entry in new_entries:
            self._log_textbox.insert("end", str(entry) + "\n")
        self._log_count = current_count
        self._log_textbox.configure(state="disabled")

        if at_bottom:
            self._log_textbox.see("end")

    def _format_uptime(self, secs: int) -> str:
        if secs < 60:
            return f"{secs}s"
        elif secs < 3600:
            return f"{secs // 60}m"
        else:
            return f"{secs // 3600}h {(secs % 3600) // 60}m"

    def _update_scheduler_countdown(self) -> None:
        if not hasattr(self, "scheduler") or not self.scheduler:
            main_window = self.master.master
            self.scheduler = getattr(main_window, "scheduler", None)
            
        if self.scheduler:
            next_job = self.scheduler.get_next_job()
            all_jobs = self.scheduler.get_schedules()
            enabled_count = sum(1 for j in all_jobs if j.get("enabled"))
            
            self._sched_details_lbl.configure(text=f"{enabled_count} enabled schedule(s)")
            
            if next_job and next_job.get("next_run"):
                next_run_str = next_job["next_run"]
                try:
                    next_dt = datetime.strptime(next_run_str, "%Y-%m-%d %H:%M:%S")
                    now = datetime.now()
                    diff = next_dt - now
                    if diff.total_seconds() > 0:
                        hours, remainder = divmod(int(diff.total_seconds()), 3600)
                        minutes, seconds = divmod(remainder, 60)
                        
                        countdown_str = ""
                        if hours > 24:
                            days = hours // 24
                            countdown_str = f"{days}d remaining"
                        elif hours > 0:
                            countdown_str = f"{hours}h {minutes}m remaining"
                        elif minutes > 0:
                            countdown_str = f"{minutes}m {seconds}s remaining"
                        else:
                            countdown_str = f"{seconds}s remaining"
                            
                        time_str = next_dt.strftime("%H:%M")
                        date_prefix = "Today" if next_dt.date() == now.date() else next_dt.strftime("%m-%d")
                        
                        self._sched_next_run_lbl.configure(
                            text=f"{date_prefix} at {time_str}",
                            text_color="#f59e0b"
                        )
                        self._sched_details_lbl.configure(
                            text=f"{countdown_str} | {enabled_count} enabled"
                        )
                    else:
                        self._sched_next_run_lbl.configure(text="Due now...", text_color="#22c55e")
                except Exception:
                    self._sched_next_run_lbl.configure(text="No upcoming jobs", text_color="#8b92a8")
            else:
                self._sched_next_run_lbl.configure(text="No upcoming jobs", text_color="#8b92a8")
        
        # Schedule next tick in 1s if this widget is active and alive
        try:
            if self.winfo_exists():
                self.after(1000, self._update_scheduler_countdown)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Update Dialog
# ---------------------------------------------------------------------------

class UpdateDialog:
    """
    Modal dialog for viewing and managing companion updates.
    Displays current version, latest version, and action buttons
    wired directly to the existing UpdateManager backend.
    """

    def __init__(self, parent: ctk.CTk, updater: Any, logger: Any) -> None:
        self._parent = parent
        self._updater = updater
        self._logger = logger

        self._dialog = ctk.CTkToplevel(parent)
        self._dialog.title("Companion Update")
        self._dialog.transient(parent)
        self._dialog.grab_set()
        self._dialog.resizable(False, False)
        self._dialog.configure(fg_color="#0f1117")

        # Center on parent
        pw, ph = parent.winfo_width(), parent.winfo_height()
        dx, dy = parent.winfo_x(), parent.winfo_y()
        w, h = 420, 380
        x = dx + (pw - w) // 2
        y = dy + (ph - h) // 2
        self._dialog.geometry(f"{w}x{h}+{x}+{y}")

        self._callback_ref = None
        self._build_ui()
        self._refresh_info()
        self._register_updater_callback()

        self._dialog.protocol("WM_DELETE_WINDOW", self._on_close)
        self._dialog.focus_set()

    # ── UI Construction ─────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad = {"padx": 24, "pady": (0, 0)}

        # Title
        ctk.CTkLabel(
            self._dialog,
            text="Companion Update",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color="#e8eaf0",
        ).pack(anchor="w", **pad, pady=(20, 4))

        ctk.CTkLabel(
            self._dialog,
            text="Check, download, and install the latest companion version.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        ).pack(anchor="w", **pad, pady=(0, 14))

        # Info card
        info_card = ctk.CTkFrame(
            self._dialog,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=10,
        )
        info_card.pack(fill="x", **pad, pady=(0, 12))

        row_current = ctk.CTkFrame(info_card, fg_color="transparent")
        row_current.pack(fill="x", padx=16, pady=(12, 2))
        ctk.CTkLabel(
            row_current,
            text="Current Version",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        ).pack(side="left")
        self._current_ver_lbl = ctk.CTkLabel(
            row_current,
            text="v\u2014",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e8eaf0",
        )
        self._current_ver_lbl.pack(side="right")

        sep = ctk.CTkFrame(info_card, height=1, fg_color="#2e3347")
        sep.pack(fill="x", padx=16, pady=(4, 4))

        row_latest = ctk.CTkFrame(info_card, fg_color="transparent")
        row_latest.pack(fill="x", padx=16, pady=(2, 12))
        ctk.CTkLabel(
            row_latest,
            text="Latest Version",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        ).pack(side="left")
        self._latest_ver_lbl = ctk.CTkLabel(
            row_latest,
            text="v\u2014",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e8eaf0",
        )
        self._latest_ver_lbl.pack(side="right")

        # Status label
        self._status_lbl = ctk.CTkLabel(
            self._dialog,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._status_lbl.pack(anchor="w", **pad, pady=(0, 8))

        # Button row: Check + Release Notes
        btn_row1 = ctk.CTkFrame(self._dialog, fg_color="transparent")
        btn_row1.pack(fill="x", **pad, pady=(0, 6))

        self._check_btn = ctk.CTkButton(
            btn_row1,
            text="Check for Updates",
            width=160,
            height=32,
            corner_radius=8,
            fg_color="#4f8ef7",
            hover_color="#3a76e8",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_check_click,
        )
        self._check_btn.pack(side="left")

        self._notes_btn = ctk.CTkButton(
            btn_row1,
            text="Release Notes",
            width=130,
            height=32,
            corner_radius=8,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_notes_click,
        )
        self._notes_btn.pack(side="right")

        # Button row: Download + Install
        btn_row2 = ctk.CTkFrame(self._dialog, fg_color="transparent")
        btn_row2.pack(fill="x", **pad, pady=(0, 6))

        self._download_btn = ctk.CTkButton(
            btn_row2,
            text="Download Update",
            width=160,
            height=32,
            corner_radius=8,
            fg_color="#f59e0b",
            hover_color="#d97706",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_download_click,
        )
        self._download_btn.pack(side="left")

        self._install_btn = ctk.CTkButton(
            btn_row2,
            text="Install Update",
            width=130,
            height=32,
            corner_radius=8,
            fg_color="#22c55e",
            hover_color="#16a34a",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_install_click,
        )
        self._install_btn.pack(side="right")

        # Close button
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
            command=self._on_close,
        ).pack(pady=(10, 16))

    # ── Updater Callback ────────────────────────────────────────────────

    def _register_updater_callback(self) -> None:
        self._callback_ref = self._on_updater_event
        self._updater.register_callback(self._callback_ref)

    def _unregister_updater_callback(self) -> None:
        if self._callback_ref:
            self._updater.unregister_callback(self._callback_ref)
            self._callback_ref = None

    def _on_updater_event(self, status: str, progress: float, error_msg: str | None = None) -> None:
        try:
            self._dialog.after(0, self._handle_updater_event, status, progress, error_msg)
        except Exception:
            pass

    def _handle_updater_event(self, status: str, progress: float, error_msg: str | None) -> None:
        if not self._dialog.winfo_exists():
            return
        self._refresh_info()
        # Update button states based on status
        is_downloading = status == "Downloading"
        is_checking = status == "Checking"
        has_pending = self._updater.get_status() in ("Pending Install", "Completed")

        self._check_btn.configure(state="disabled" if (is_checking or is_downloading) else "normal")
        self._download_btn.configure(
            state="disabled" if (is_downloading or is_checking or not self._updater.has_update()) else "normal",
            text="Downloading\u2026" if is_downloading else "Download Update",
        )
        self._install_btn.configure(state="normal" if has_pending else "disabled")

        # Status message
        if is_downloading:
            self._status_lbl.configure(text=f"Downloading\u2026 {int(progress)}%", text_color="#f59e0b")
        elif status == "Verifying":
            self._status_lbl.configure(text="Verifying download\u2026", text_color="#f59e0b")
        elif status == "Pending Install":
            self._status_lbl.configure(text="Download complete. Ready to install.", text_color="#22c55e")
        elif status == "Completed":
            self._status_lbl.configure(text="Update installed successfully!", text_color="#22c55e")
        elif status == "Failed":
            self._status_lbl.configure(text=f"Failed: {error_msg or 'Unknown error'}", text_color="#ef4444")
        elif status == "Cancelled":
            self._status_lbl.configure(text="Download cancelled.", text_color="#ef4444")
        elif status == "Checking":
            self._status_lbl.configure(text="Checking for updates\u2026", text_color="#4f8ef7")
        elif status == "Offline":
            self._status_lbl.configure(text="Unable to reach GitHub.", text_color="#ef4444")
        elif status == "Rate Limited":
            self._status_lbl.configure(text="GitHub API rate limited.", text_color="#f59e0b")
        else:
            self._status_lbl.configure(text="", text_color="#8b92a8")

    # ── Refresh Info ────────────────────────────────────────────────────

    def _refresh_info(self) -> None:
        current = self._updater.get_current_version()
        latest = self._updater.get_latest_version()
        self._current_ver_lbl.configure(text=f"v{current}")
        self._latest_ver_lbl.configure(text=f"v{latest}")

        has_pending = self._updater.get_status() in ("Pending Install", "Completed")
        self._install_btn.configure(state="normal" if has_pending else "disabled")

        has_update = self._updater.has_update()
        is_active = self._updater.get_status() in ("Downloading", "Checking")
        self._download_btn.configure(
            state="disabled" if (is_active or not has_update) else "normal"
        )

    # ── Button Handlers ─────────────────────────────────────────────────

    def _on_check_click(self) -> None:
        self._check_btn.configure(state="disabled")
        self._status_lbl.configure(text="Checking for updates\u2026", text_color="#4f8ef7")
        self._updater.check_for_updates(force=True)

    def _on_notes_click(self) -> None:
        self._updater.open_release_notes()

    def _on_download_click(self) -> None:
        self._download_btn.configure(state="disabled", text="Downloading\u2026")
        self._updater.download_update()

    def _on_install_click(self) -> None:
        from installer import InstallerManager
        main_window = self._parent
        installer = getattr(main_window, "installer", None)
        if installer:
            self._install_btn.configure(state="disabled")
            threading.Thread(target=installer.install_update, daemon=True).start()

    def _on_close(self) -> None:
        self._unregister_updater_callback()
        try:
            self._dialog.grab_release()
        except Exception:
            pass
        self._dialog.destroy()
