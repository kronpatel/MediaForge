"""
backend_manager.py – BackendManager

Manages the lifecycle of the MediaForge Flask backend process:
  * Start / stop / restart
  * Duplicate-instance detection via port check & identity verification
  * Background health monitoring (paused when backend is stopped)
  * Dynamic host/port config read from backend/settings.json
  * Uses sys.executable – never hardcodes 'python' / 'python3' / 'py'
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from enum import Enum, auto
from typing import Callable
from urllib.parse import urlparse

import psutil  # noqa: F401 – retained for potential future process inspection
import requests

from logger import AppLogger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Path to the backend directory (relative to this file's parent)
_COMPANION_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_COMPANION_DIR)
BACKEND_DIR = os.path.join(_ROOT_DIR, "backend")
SETTINGS_FILE = os.path.join(BACKEND_DIR, "settings.json")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000

# Centralized timing configuration (no magic numbers)
STARTUP_TIMEOUT = 8.0          # seconds to wait for backend HTTP 200 on start
HEALTH_CHECK_INTERVAL = 3.0    # seconds between health-check polls
TERMINATE_TIMEOUT = 5.0        # seconds before SIGKILL
HTTP_TIMEOUT = 2.0             # seconds per health-check request

STARTUP_POLL_INTERVAL = 0.4    # polling rate during startup checks
MONITOR_SUSPEND_INTERVAL = 0.5 # rate of checking active state when suspended
RESTART_COOLDOWN = 0.5         # pause between stop and start
PORT_PROBE_TIMEOUT = 0.5       # connect timeout for port check


# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------

class BackendStatus(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    CRASHED = auto()


StatusCallback = Callable[[BackendStatus, str], None]


# ---------------------------------------------------------------------------
# BackendManager
# ---------------------------------------------------------------------------

class BackendManager:
    """
    Manages the MediaForge Flask backend process.

    All public methods are thread-safe.  The internal health-monitor thread
    runs as a daemon and is suspended (via an Event) while the backend is
    stopped – preventing unnecessary HTTP requests.

    Parameters
    ----------
    logger:
        Shared AppLogger instance for event recording.
    """

    def __init__(self, logger: AppLogger) -> None:
        self._logger = logger
        self._process: subprocess.Popen | None = None
        self._status: BackendStatus = BackendStatus.STOPPED
        self._is_managed: bool = False
        self._lock = threading.Lock()

        # Config (populated in _load_config)
        self._host: str = DEFAULT_HOST
        self._port: int = DEFAULT_PORT

        # Health monitor
        self._monitor_active = threading.Event()   # set = monitor runs
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()       # set = thread should exit

        # Status callbacks registered by the UI
        self._status_callbacks: list[StatusCallback] = []

        self._load_config()

        # Startup Detection: check if backend is already running & authentic
        if self._is_port_in_use():
            if self._ping():
                self._status = BackendStatus.RUNNING
                self._is_managed = False
                self._monitor_active.set()
                self._logger.info(
                    f"Startup check: Detected backend already running on port {self._port} "
                    "(externally managed). Adopting for monitoring."
                )
            else:
                self._status = BackendStatus.STOPPED
                self._is_managed = False
                self._logger.warning(
                    f"Startup check: Port {self._port} is in use, but identity verification failed. "
                    f"The service at {self.base_url} is not a valid MediaForge backend. "
                    "Another application may be using this port."
                )
        else:
            self._status = BackendStatus.STOPPED
            self._is_managed = False

        self._start_monitor_thread()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    @property
    def status(self) -> BackendStatus:
        with self._lock:
            return self._status

    def register_status_callback(self, fn: StatusCallback) -> None:
        """Register a callable invoked on every status change."""
        with self._lock:
            self._status_callbacks.append(fn)

    def start(self) -> None:
        """
        Start the backend process.

        If the port is already in use, marks the backend as Running without
        launching a new process if identity verification succeeds.
        Transitions: STOPPED → STARTING → RUNNING (or STOPPED on failure).
        """
        with self._lock:
            current = self._status

        if current in (BackendStatus.RUNNING, BackendStatus.STARTING):
            self._logger.info("Backend is already running or starting.")
            return

        # Reload config in case settings changed since launch
        self._load_config()

        if self._is_port_in_use():
            if self._ping():
                self._logger.info(
                    f"Backend already running on port {self._port} "
                    "(started externally). Adopting existing instance."
                )
                with self._lock:
                    self._is_managed = False
                self._set_status(BackendStatus.RUNNING, "Backend already running.")
                self._monitor_active.set()
                return
            else:
                msg = f"Port already in use by another service on port {self._port}."
                self._logger.error(msg)
                self._set_status(BackendStatus.STOPPED, msg)
                return

        self._set_status(BackendStatus.STARTING, "Starting backend…")
        self._logger.info("Backend starting…")

        start_time = time.monotonic()
        try:
            proc = self._launch_backend()
        except FileNotFoundError:
            msg = "Backend executable not found. Ensure backend/app.py exists."
            self._logger.error(msg)
            self._set_status(BackendStatus.STOPPED, msg)
            return
        except PermissionError as exc:
            msg = "Permission denied when launching backend."
            self._logger.error(msg, exc=exc)
            self._set_status(BackendStatus.STOPPED, msg)
            return
        except Exception as exc:  # noqa: BLE001
            msg = "Backend failed to start."
            self._logger.error(msg, exc=exc)
            self._set_status(BackendStatus.STOPPED, msg)
            return

        with self._lock:
            self._process = proc
            self._is_managed = True

        # Wait for the backend to become responsive
        if self._wait_until_ready(proc):
            duration = time.monotonic() - start_time
            version = self.fetch_version() or "unknown"
            self._logger.info(
                f"MediaForge Backend Started\n"
                f"PID: {proc.pid}\n"
                f"Version: {version}\n"
                f"Host: {self._host}\n"
                f"Port: {self._port}\n"
                f"Startup Time: {duration:.1f} seconds"
            )
            self._set_status(BackendStatus.RUNNING, "Backend started.")
            self._monitor_active.set()
        else:
            # Process may still be running but never became healthy
            if proc.poll() is None:
                self._terminate_backend(proc)
            with self._lock:
                self._process = None
                self._is_managed = False
            msg = "Backend startup timed out."
            self._logger.error(msg)
            self._set_status(BackendStatus.STOPPED, msg)

    def stop(self) -> None:
        """
        Stop the backend process (only if it was launched by the Companion).

        Transitions: RUNNING/STARTING → STOPPED.
        """
        with self._lock:
            proc = self._process
            current = self._status
            is_managed = self._is_managed

        if current == BackendStatus.STOPPED:
            self._logger.info("Backend is already stopped.")
            return

        if not is_managed:
            self._logger.warning("Attempted to stop an externally managed backend. Ignored.")
            return

        # Suspend the health monitor while we're intentionally stopping
        self._monitor_active.clear()

        if proc is None:
            self._logger.warning(
                "Backend process handle is missing; marking as stopped."
            )
            self._set_status(BackendStatus.STOPPED, "Backend marked stopped.")
            return

        self._logger.info("Stopping backend…")
        self._set_status(BackendStatus.STOPPED, "Backend stopping…")

        pid = proc.pid
        self._terminate_backend(proc)
        exit_code = proc.poll()

        with self._lock:
            self._process = None
            self._is_managed = False

        self._logger.info(
            f"MediaForge Backend Stopped\n"
            f"PID: {pid}\n"
            f"Exit Code: {exit_code if exit_code is not None else 'unknown'}"
        )
        self._set_status(BackendStatus.STOPPED, "Backend stopped.")

    def restart(self) -> None:
        """Restart the backend process (only if it was launched by the Companion)."""
        if not self.is_managed():
            self._logger.warning("Attempted to restart an externally managed backend. Ignored.")
            return
        self._logger.info("Restarting backend…")
        self.stop()
        time.sleep(RESTART_COOLDOWN)
        self.start()

    def is_managed(self) -> bool:
        """Return True if we own (started) the backend process."""
        with self._lock:
            return self._is_managed

    def shutdown(self) -> None:
        """
        Called when the Companion is closing.
        Signals the monitor thread to exit.
        """
        self._stop_event.set()
        self._monitor_active.set()  # unblock the wait so thread can exit

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        """
        Read host/port from backend/settings.json → backend_url.
        Falls back to DEFAULT_HOST / DEFAULT_PORT if the file is absent,
        malformed, or does not contain a parseable backend_url.
        """
        host, port = DEFAULT_HOST, DEFAULT_PORT

        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                raw_url: str = data.get("backend_url", "")
                if raw_url:
                    parsed = urlparse(raw_url)
                    if parsed.hostname:
                        host = parsed.hostname
                    if parsed.port:
                        port = parsed.port
            except Exception:  # noqa: BLE001
                pass  # silently fall back to defaults

        with self._lock:
            self._host = host
            self._port = port

    # ------------------------------------------------------------------
    # Port / ping helpers
    # ------------------------------------------------------------------

    def _is_port_in_use(self) -> bool:
        """Return True if something is already listening on self._port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(PORT_PROBE_TIMEOUT)
            result = sock.connect_ex((self._host, self._port))
            return result == 0

    def _ping(self) -> bool:
        """
        Verify that the service on the configured URL is the MediaForge backend.

        Checks:
          * HTTP 200 response
          * Valid JSON body
          * name == "MediaForge Backend"
          * status == "running"

        Returns True on success, False silently on any failure so callers
        (startup detection, health monitor) decide whether to log a warning.
        """
        try:
            resp = requests.get(self.base_url, timeout=HTTP_TIMEOUT)
            if resp.status_code != 200:
                return False
            try:
                data = resp.json()
            except ValueError:
                return False
            if not isinstance(data, dict):
                return False
            return (
                data.get("name") == "MediaForge Backend"
                and data.get("status") == "running"
                and data.get("version") is not None
            )
        except requests.RequestException:
            return False

    def fetch_version(self) -> str | None:
        """
        Fetch the backend version string from the root API endpoint.

        Returns the version string (e.g. '1.1.0') or None on failure.
        """
        try:
            resp = requests.get(self.base_url, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp.json().get("version")
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Process lifecycle helper methods
    # ------------------------------------------------------------------

    def _launch_backend(self) -> subprocess.Popen:
        """Launch the backend process using sys.executable."""
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        return subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=BACKEND_DIR,
            creationflags=creation_flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _wait_until_ready(self, proc: subprocess.Popen) -> bool:
        """Wait for the backend to become responsive (up to STARTUP_TIMEOUT)."""
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False
            if self._ping():
                return True
            time.sleep(STARTUP_POLL_INTERVAL)
        return False

    def _terminate_backend(self, proc: subprocess.Popen) -> None:
        """Terminate the backend process tree cleanly or force kill if necessary."""
        try:
            # On Windows, terminating the parent process leaves child processes
            # (such as the Flask reloader process) running as orphans.
            # We use psutil to find and terminate the entire process tree.
            parent_pid = proc.pid
            try:
                parent = psutil.Process(parent_pid)
                children = parent.children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
                parent = None

            # First, terminate all children and then the parent
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            if parent:
                try:
                    parent.terminate()
                except psutil.NoSuchProcess:
                    pass

            # Wait for all processes to exit
            procs_to_wait = children + ([parent] if parent else [])
            gone, alive = psutil.wait_procs(procs_to_wait, timeout=TERMINATE_TIMEOUT)

            # If any are still alive, force kill them
            if alive:
                self._logger.warning("Some backend processes did not exit gracefully – forcing.")
                for p in alive:
                    try:
                        p.kill()
                    except psutil.NoSuchProcess:
                        pass
                psutil.wait_procs(alive, timeout=2.0)
        except Exception as exc:
            self._logger.error("Error while terminating backend process tree.", exc=exc)

    def _check_health(self) -> bool:
        """Check if the backend process and its API are healthy and authentic."""
        with self._lock:
            proc = self._process
            is_managed = self._is_managed

        process_alive = True
        if is_managed and proc is not None:
            process_alive = proc.poll() is None
        else:
            process_alive = self._is_port_in_use()

        if not process_alive:
            return False

        if not self._ping():
            self._logger.warning(
                f"Backend identity check failed on {self.base_url}. "
                "The service did not return a valid MediaForge response."
            )
            return False

        return True

    # ------------------------------------------------------------------
    # Health monitor (background thread)
    # ------------------------------------------------------------------

    def _start_monitor_thread(self) -> None:
        self._monitor_thread = threading.Thread(
            target=self._health_monitor,
            name="BackendHealthMonitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _health_monitor(self) -> None:
        """
        Runs forever in a daemon thread.

        Waits on ``_monitor_active`` (an Event) so it is fully suspended
        while the backend is stopped – no busy-waiting, no unnecessary HTTP.
        Resumes automatically once the Event is set during ``start()``.
        """
        while not self._stop_event.is_set():
            # Block here when the backend is stopped
            self._monitor_active.wait()

            if self._stop_event.is_set():
                break

            with self._lock:
                current_status = self._status

            if current_status != BackendStatus.RUNNING:
                # Not yet running (e.g. still STARTING) – wait briefly
                time.sleep(MONITOR_SUSPEND_INTERVAL)
                continue

            if not self._check_health():
                self._logger.warning("Unable to communicate with backend. Identity validation failed or port closed.")
                self._monitor_active.clear()
                with self._lock:
                    self._process = None
                    self._is_managed = False
                self._set_status(BackendStatus.STOPPED, "Unable to communicate with backend.")

            time.sleep(HEALTH_CHECK_INTERVAL)

    # ------------------------------------------------------------------
    # Status management
    # ------------------------------------------------------------------

    def _set_status(self, status: BackendStatus, message: str) -> None:
        """Update internal status and fire all registered callbacks."""
        callbacks: list[StatusCallback] = []
        with self._lock:
            self._status = status
            callbacks = list(self._status_callbacks)

        for cb in callbacks:
            try:
                cb(status, message)
            except Exception:
                pass
