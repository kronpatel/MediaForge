"""
tray.py – TrayManager

Manages the Windows System Tray icon, menu, notifications, tooltips, and status
synchronization for the MediaForge Companion.
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from typing import TYPE_CHECKING

from PIL import Image
import pystray

from backend_manager import BackendStatus

if TYPE_CHECKING:
    from backend_manager import BackendManager
    from logger import AppLogger
    from ui import CompanionWindow


class TrayManager:
    """
    Manages the system tray lifecycle, menus, and notification bubbles.

    Runs pystray in a daemon background thread. All interactions that affect
    the Tkinter UI or trigger backend actions are marshalled onto the Tkinter
    main thread using window.after(0, ...).
    """

    def __init__(self, manager: BackendManager, window: CompanionWindow, logger: AppLogger) -> None:
        self._manager = manager
        self._window = window
        self._logger = logger

        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None
        self._running_lock = threading.Lock()
        self._is_running: bool = False

        # Notification state
        self._has_notified_background: bool = False
        self._last_notification: tuple[str, str, float] | None = None  # (title, message, timestamp)
        self._previous_status: BackendStatus | None = None
        self._is_restarting: bool = False

        # Path to tray icon
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self._icon_path = os.path.join(base_dir, "resources", "tray.ico")

        # Sync tray status automatically when the backend manager status changes
        self._manager.register_status_callback(self._on_backend_status_change)

    def start(self) -> bool:
        """
        Initialize and run the system tray icon in a background thread.
        This call is idempotent and protected against double initialization.
        """
        with self._running_lock:
            if self._is_running:
                return True

            if not os.path.exists(self._icon_path):
                self._logger.warning(f"Tray icon file not found at {self._icon_path}. Cannot start tray.")
                return False

            try:
                # Load the icon image
                img = Image.open(self._icon_path)
                
                # Instantiate the pystray Icon with a default empty menu
                self._icon = pystray.Icon(
                    name="MediaForgeCompanion",
                    icon=img,
                    title="MediaForge Companion",
                    menu=self._build_menu(BackendStatus.STOPPED, False)
                )
                
                # Set up double-click to restore
                self._icon.activated = self._on_activated
                
                self._is_running = True
            except Exception as exc:
                self._logger.error("Failed to initialize system tray icon.", exc=exc)
                self._icon = None
                self._is_running = False
                return False

            # Spawn pystray runner thread
            self._thread = threading.Thread(
                target=self._run_icon_loop,
                name="TrayIconThread",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        """Clean up the system tray icon and wait for the thread to exit."""
        with self._running_lock:
            if not self._is_running or not self._icon:
                return
            
            self._logger.info("Stopping system tray icon…")
            try:
                self._icon.stop()
            except Exception as exc:
                self._logger.error("Error stopping system tray icon.", exc=exc)
            
            self._is_running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._logger.info("System tray thread joined successfully.")

    def notify(self, title: str, message: str) -> None:
        """
        Display a system tray bubble notification with 5-second duplicate debouncing.
        """
        if not self._icon:
            return

        current_time = time.monotonic()
        if (
            self._last_notification
            and self._last_notification[0] == title
            and self._last_notification[1] == message
            and current_time - self._last_notification[2] < 5.0
        ):
            # Suppress duplicate notification
            return

        self._last_notification = (title, message, current_time)

        try:
            self._icon.notify(message, title)
        except Exception as exc:
            self._logger.warning(f"Failed to display tray notification: {exc}")

    def notify_background(self) -> None:
        """Display background hiding guidance exactly once per session."""
        if self._has_notified_background:
            return
        
        self.notify(
            "MediaForge Companion",
            "Running in the background.\nDouble-click the tray icon to restore."
        )
        self._has_notified_background = True

    def set_restarting(self) -> None:
        """Flag that a restart is occurring to suppress intermediate stop notifications."""
        self._is_restarting = True

    # ------------------------------------------------------------------
    # Tray Callbacks & Marshalling
    # ------------------------------------------------------------------

    def _marshal(self, fn, *args, **kwargs) -> None:
        """Safe wrapper to marshal callbacks onto the Tkinter thread."""
        try:
            self._window.after(0, lambda: fn(*args, **kwargs))
        except Exception as exc:
            self._logger.error("Failed to marshal tray action to Tkinter main thread.", exc=exc)

    def _on_activated(self, icon: pystray.Icon) -> None:
        """Triggered on tray icon double click."""
        self._marshal(self._window.restore_window)

    def _on_show_companion(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._marshal(self._window.restore_window)

    def _on_open_backend(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        def _check_and_open():
            # Browser Launch Validation: verify backend is running and reachable
            if self._manager.status == BackendStatus.RUNNING and self._manager._ping():
                webbrowser.open(self._manager.base_url)
            else:
                self.notify(
                    "MediaForge Companion",
                    "Backend is not running. Please start the backend first."
                )
        self._marshal(_check_and_open)

    def _on_start_backend(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._marshal(self._window.trigger_start)

    def _on_stop_backend(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._marshal(self._window.trigger_stop)

    def _on_restart_backend(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.set_restarting()
        self._marshal(self._window.trigger_restart)

    def _on_exit_companion(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._marshal(self._window.exit_completely)

    # ------------------------------------------------------------------
    # Status Sync & Menu Rebuilding
    # ------------------------------------------------------------------

    def _on_backend_status_change(self, status: BackendStatus, message: str) -> None:
        """Status callback registered with BackendManager."""
        self._marshal(self._sync_status, status)

    def _sync_status(self, status: BackendStatus) -> None:
        """Synchronize tray icon properties with backend status (runs on Tkinter thread)."""
        if not self._icon:
            return

        is_managed = self._manager.is_managed()
        status_str = self._get_status_string(status, is_managed)

        # 1. Update Tooltip
        self._icon.title = f"MediaForge Companion\nBackend: {status_str}"

        # 2. Update Menu
        self._icon.menu = self._build_menu(status, is_managed)

        # 3. Trigger Notification based on state transition
        if self._previous_status is not None and self._previous_status != status:
            if status == BackendStatus.RUNNING:
                if self._is_restarting:
                    self.notify("MediaForge Companion", "Backend Restarted")
                    self._is_restarting = False
                elif self._previous_status == BackendStatus.STARTING:
                    self.notify("MediaForge Companion", "Backend Started")
            elif status == BackendStatus.STOPPED:
                if not self._is_restarting and self._previous_status in (BackendStatus.RUNNING, BackendStatus.STARTING):
                    self.notify("MediaForge Companion", "Backend Stopped")
            elif status == BackendStatus.CRASHED:
                self.notify("MediaForge Companion", "Backend Stopped Unexpectedly (Crashed)")

        self._previous_status = status

    def _build_menu(self, status: BackendStatus, is_managed: bool) -> pystray.Menu:
        """Rebuild the status-dependent tray menu."""
        is_running = status == BackendStatus.RUNNING
        is_stopped_or_crashed = status in (BackendStatus.STOPPED, BackendStatus.CRASHED)
        is_managed_running = is_running and is_managed

        status_str = self._get_status_string(status, is_managed)

        return pystray.Menu(
            pystray.MenuItem("MediaForge Companion", self._on_show_companion, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show Companion", self._on_show_companion),
            pystray.MenuItem("Open Backend", self._on_open_backend, enabled=is_running),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start Backend", self._on_start_backend, enabled=is_stopped_or_crashed),
            pystray.MenuItem("Stop Backend", self._on_stop_backend, enabled=is_managed_running),
            pystray.MenuItem("Restart Backend", self._on_restart_backend, enabled=is_managed_running),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Backend Status: {status_str}", action=None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit Companion", self._on_exit_companion)
        )

    def _get_status_string(self, status: BackendStatus, is_managed: bool) -> str:
        if status == BackendStatus.RUNNING:
            return "Running" if is_managed else "Running (External)"
        elif status == BackendStatus.STARTING:
            return "Starting"
        elif status == BackendStatus.CRASHED:
            return "Crashed"
        else:
            return "Stopped"

    # ------------------------------------------------------------------
    # Runner Thread Loop
    # ------------------------------------------------------------------

    def _run_icon_loop(self) -> None:
        """Blocking method run in daemon thread."""
        try:
            if self._icon:
                self._icon.run()
        except Exception as exc:
            self._logger.error("Exception in system tray run loop.", exc=exc)
            with self._running_lock:
                self._is_running = False
