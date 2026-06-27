"""
dashboard.py – DashboardController & DashboardPage

1. DashboardController: Single background polling manager that polls the backend
   and broadcasts state updates to subscribed pages.
2. DashboardPage: Landing panel displaying backend health summary, current active
   download status, progress bar, and recent logs.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from base_page import BasePage
from backend_manager import BackendStatus

if TYPE_CHECKING:
    from backend_manager import BackendManager
    from logger import AppLogger


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
            self._logger.info("Unified background polling thread started.")

    def shutdown(self) -> None:
        self._stop_event.set()
        self._poll_event.set()  # wake up poller if waiting
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._logger.info("Unified background polling thread stopped.")

    def get_cached_data(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_data)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self._poll_event.clear()
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
                    self._logger.debug_log(f"Unified poll fetch failed: {exc}")
                    self._handle_offline()
            else:
                self._handle_offline()

            # Wait for next interval or immediate trigger
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
        
        # Single tray warning notification if we just lost connection
        if not self._notification_sent and self._manager.status == BackendStatus.CRASHED:
            # We will trigger tray notification safely through window
            try:
                # We can marshal the tray notification
                def _trigger_tray_notify():
                    if getattr(self._window_ref, "tray_active", False) and self._window_ref._tray_manager:
                        self._window_ref._tray_manager.notify(
                            "MediaForge Companion",
                            "Backend offline. Companion has switched to offline mode."
                        )
                self._window_ref.after(0, _trigger_tray_notify)
            except Exception:
                pass
            self._notification_sent = True

    def _broadcast(self, data: dict[str, Any]) -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            self._last_data = data
            subs = list(self._subscribers)
        
        for sub in subs:
            try:
                if sub.winfo_exists():
                    sub.after(0, sub.refresh, data)
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
        self._cached_hash = None
        self._build_ui()

    def _build_ui(self) -> None:
        # Title
        ctk.CTkLabel(
            self,
            text="Dashboard",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#e8eaf0",
        ).pack(anchor="w", padx=20, pady=(20, 10))

        # ── Grid of Info Cards ───────────────────────────────────────────
        cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        cards_frame.pack(fill="x", padx=20, pady=10)

        # Columns configuration
        cards_frame.columnconfigure((0, 1, 2, 3), weight=1, uniform="equal")

        self._status_card = self._create_card(cards_frame, 0, "Backend Status", "Offline", "#ef4444")
        self._version_card = self._create_card(cards_frame, 1, "Version", "v—", "#8b92a8")
        self._uptime_card = self._create_card(cards_frame, 2, "Backend Uptime", "0s", "#8b92a8")
        self._queue_card = self._create_card(cards_frame, 3, "Queue Size", "0", "#8b92a8")

        # ── Active Download Panel ─────────────────────────────────────────
        self._active_card = ctk.CTkFrame(
            self,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        self._active_card.pack(fill="x", padx=20, pady=10)

        self._active_inner = ctk.CTkFrame(self._active_card, fg_color="transparent")
        self._active_inner.pack(fill="x", padx=16, pady=14)

        ctk.CTkLabel(
            self._active_inner,
            text="Active Download",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#8b92a8",
        ).pack(anchor="w")

        self._title_lbl = ctk.CTkLabel(
            self._active_inner,
            text="No active downloads.",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#e8eaf0",
        )
        self._title_lbl.pack(anchor="w", pady=(4, 8))

        self._progress_bar = ctk.CTkProgressBar(
            self._active_inner,
            fg_color="#20232f",
            progress_color="#4f8ef7",
            height=8,
        )
        self._progress_bar.pack(fill="x", pady=(0, 8))
        self._progress_bar.set(0.0)

        # Meta panel: Speed & ETA
        self._meta_frame = ctk.CTkFrame(self._active_inner, fg_color="transparent")
        self._meta_frame.pack(fill="x")

        self._speed_lbl = ctk.CTkLabel(
            self._meta_frame,
            text="Speed: —",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._speed_lbl.pack(side="left")

        self._eta_lbl = ctk.CTkLabel(
            self._meta_frame,
            text="ETA: —",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._eta_lbl.pack(side="right")

        # ── Logs & Activity ──────────────────────────────────────────────
        logs_card = ctk.CTkFrame(
            self,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        logs_card.pack(fill="both", expand=True, padx=20, pady=(10, 20))

        ctk.CTkLabel(
            logs_card,
            text="Recent Activity Logs",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#8b92a8",
        ).pack(anchor="w", padx=16, pady=(12, 6))

        self._log_textbox = ctk.CTkTextbox(
            logs_card,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#0f1117",
            text_color="#e8eaf0",
            border_color="#2e3347",
            border_width=1,
            corner_radius=8,
            wrap="word",
            state="disabled",
        )
        self._log_textbox.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _create_card(self, parent: ctk.CTkFrame, col: int, title: str, val: str, color: str) -> dict[str, Any]:
        card = ctk.CTkFrame(
            parent,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        card.grid(row=0, column=col, padx=4, sticky="nsew")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=12, pady=12, fill="both")

        ctk.CTkLabel(
            inner,
            text=title,
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color="#8b92a8",
        ).pack(anchor="w")

        val_lbl = ctk.CTkLabel(
            inner,
            text=val,
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color=color,
        )
        val_lbl.pack(anchor="w", pady=(4, 0))

        return {"lbl": val_lbl, "default_color": color}

    # ------------------------------------------------------------------
    # Lifecycle refresh
    # ------------------------------------------------------------------

    def refresh(self, data: dict[str, Any]) -> None:
        """Invoked on the Tkinter thread with polled backend state."""
        # Performance check: serialize state to check if change occurred
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

        # 2. Version card
        version = self.manager.fetch_version() if not offline else None
        self._version_card["lbl"].configure(text=f"v{version}" if version else "v—")

        # 3. Uptime card
        stats = data.get("stats", {})
        uptime_sec = stats.get("backend_uptime", 0)
        self._uptime_card["lbl"].configure(text=self._format_uptime(uptime_sec) if uptime_sec else "0s")

        # 4. Queue Size card
        queue = data.get("queue", [])
        self._queue_card["lbl"].configure(text=str(len(queue)))

        # 5. Active download block
        # Active download is defined as any job in queue with status "downloading"
        active_job = None
        for job in queue:
            if job.get("status") == "downloading":
                active_job = job
                break

        if active_job:
            self._title_lbl.configure(text=active_job.get("label", active_job.get("filename", "Downloading…")))
            progress = active_job.get("progress", 0.0)
            self._progress_bar.set(float(progress) / 100.0)
            self._speed_lbl.configure(text=f"Speed: {active_job.get('speed', '—')}")
            self._eta_lbl.configure(text=f"ETA: {active_job.get('eta', '—')}")
        else:
            self._title_lbl.configure(text="No active downloads.")
            self._progress_bar.set(0.0)
            self._speed_lbl.configure(text="Speed: —")
            self._eta_lbl.configure(text="ETA: —")

    def on_show(self) -> None:
        """Trigger log populate when page is focused."""
        self._populate_logs()

    def _populate_logs(self) -> None:
        entries = self.logger.get_entries()
        self._log_textbox.configure(state="normal")
        self._log_textbox.delete("1.0", "end")
        for entry in entries[-20:]:  # show recent 20 logs
            self._log_textbox.insert("end", str(entry) + "\n")
        self._log_textbox.configure(state="disabled")
        self._log_textbox.see("end")

    def _format_uptime(self, secs: int) -> str:
        if secs < 60:
            return f"{secs}s"
        elif secs < 3600:
            return f"{secs // 60}m"
        else:
            return f"{secs // 3600}h {(secs % 3600) // 60}m"
