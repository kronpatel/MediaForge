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

def _locate_backend_dir() -> str:
    """Locate the backend/ directory in source and frozen (EXE) modes."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        for candidate in (
            os.path.join(exe_dir, "backend"),              # portable: sibling of EXE
            os.path.join(os.path.dirname(exe_dir), "backend"),  # dev dist/: parent sibling
        ):
            if os.path.isdir(candidate):
                return candidate
        return os.path.join(exe_dir, "backend")  # fallback best guess
    # Source mode: companion/../backend/
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "backend",
    )


BACKEND_DIR = _locate_backend_dir()
SETTINGS_FILE = os.path.join(BACKEND_DIR, "settings.json")

_COMPANION_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_COMPANION_DIR)

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


class _ProcessHandle:
    """Lightweight Popen-like wrapper for an already-running process.

    Provides ``.pid`` and ``.poll()`` so it can be used in place of
    ``subprocess.Popen`` throughout BackendManager.
    """
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        try:
            p = psutil.Process(self.pid)
            return None if p.is_running() else (p.wait() or 0)
        except psutil.NoSuchProcess:
            return self.returncode or 0


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

        # Shared HTTP Session & connection states
        self._session: requests.Session | None = None
        self._has_transport_error: bool = False
        self._logged_after_shutdown: bool = False

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

        # Deferred startup — port check / ping moved to deferred_init() so
        # the constructor returns instantly and never blocks the UI thread.
        self._status = BackendStatus.STOPPED
        self._is_managed = False
        self._cached_version: str | None = None
        self._startup_lock = threading.Lock()
        self._startup_done = False

        # PID discovered during adoption (set by deferred_init)
        self._adopted_pid: int | None = None

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
                discovered_pid = self._discover_backend_pid()
                with self._lock:
                    if discovered_pid is not None:
                        self._is_managed = True
                        self._process = _ProcessHandle(discovered_pid)
                    else:
                        self._is_managed = False
                        self._process = None
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
            with self._lock:
                self._is_managed = False
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
        
        # Wait up to 4.0 seconds for the port to be released
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if not self._is_port_in_use():
                break
            time.sleep(0.2)

        time.sleep(RESTART_COOLDOWN)
        self.start()

    def is_managed(self) -> bool:
        """Return True if we own (started) the backend process."""
        with self._lock:
            return self._is_managed

    def close_session(self) -> None:
        """Close the shared HTTP session. Idempotent and thread-safe."""
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = None

    def shutdown(self) -> None:
        """
        Called when the Companion is closing.
        Signals the monitor thread to exit.
        """
        self._stop_event.set()
        self._monitor_active.set()  # unblock the wait so thread can exit
        self.close_session()

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

    def _send_request(self, method: str, path_or_url: str, **kwargs) -> requests.Response | None:
        """
        Send an HTTP request thread-safely.
        """
        if self._stop_event.is_set():
            if not getattr(self, "_logged_after_shutdown", False):
                self._logger.warning("Attempted to make HTTP request after backend manager shutdown.")
                self._logged_after_shutdown = True
            return None

        try:
            url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
            resp = requests.request(method, url, **kwargs)
            self._has_transport_error = False
            return resp
        except requests.RequestException as exc:
            if not getattr(self, "_has_transport_error", False):
                self._logger.warning(
                    f"Backend connection transport error ({exc})."
                )
                self._has_transport_error = True
            return None

    def _ping(self) -> bool:
        """
        Verify that the service on the configured URL is the MediaForge backend.

        Checks:
          * HTTP 200 response
          * Valid JSON body
          * name == "MediaForge Backend"
          * status == "running"

        Returns True on success, False silently on any failure.
        """
        resp = self._send_request("GET", self.base_url, timeout=HTTP_TIMEOUT)
        if resp is None or resp.status_code != 200:
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

    def deferred_init(self) -> None:
        """Check if backend is already running. Runs in a daemon thread — never blocks UI."""
        if self._is_port_in_use():
            if self._ping():
                # Discover PID of the running backend for lifecycle control
                discovered_pid = self._discover_backend_pid()

                with self._lock:
                    if discovered_pid is not None:
                        self._is_managed = True
                        self._adopted_pid = discovered_pid
                        self._process = _ProcessHandle(discovered_pid)
                    else:
                        self._is_managed = False
                        self._adopted_pid = None
                        self._process = None

                self._set_status(BackendStatus.RUNNING, "Backend already running.")
                self._monitor_active.set()
                self._logger.info(
                    f"Startup check: Detected backend already running on port {self._port} "
                    f"(PID {discovered_pid or 'unknown'}). Taking ownership for lifecycle control."
                )
                # Pre-cache version so UI never waits on HTTP during startup
                resp = self._send_request("GET", self.base_url, timeout=HTTP_TIMEOUT)
                if resp is not None and resp.status_code == 200:
                    try:
                        ver = resp.json().get("version")
                        with self._lock:
                            self._cached_version = ver
                    except Exception:
                        pass
            else:
                self._logger.warning(
                    f"Startup check: Port {self._port} is in use, but identity verification failed. "
                    f"The service at {self.base_url} is not a valid MediaForge backend. "
                    "Another application may be using this port."
                )

        with self._startup_lock:
            self._startup_done = True

    def _discover_backend_pid(self) -> int | None:
        """Try to discover the PID of the backend process listening on self._port."""
        # 1. First try net_connections
        try:
            for conn in psutil.net_connections():
                if conn.laddr.port == self._port and conn.pid:
                    try:
                        proc = psutil.Process(conn.pid)
                        if proc.is_running():
                            return conn.pid
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
        except (psutil.AccessDenied, PermissionError):
            self._logger.warning("Cannot enumerate network connections — insufficient privileges.")
        
        # 2. Fallback: scan running Python processes for backend/app.py
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    name = proc.info['name']
                    if name and 'python' in name.lower():
                        cmdline = proc.info['cmdline']
                        if cmdline:
                            cmdline_str = " ".join(cmdline).lower()
                            if "app.py" in cmdline_str:
                                return proc.info['pid']
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as exc:
            self._logger.warning(f"Process list scan fallback failed: {exc}")
            
        return None

    def fetch_version(self) -> str | None:
        """
        Fetch the backend version string from the root API endpoint.

        Returns the version string (e.g. '1.2.0') or None on failure.
        Uses cached value if available to avoid blocking during startup.
        """
        with self._lock:
            if self._cached_version is not None:
                return self._cached_version
        resp = self._send_request("GET", self.base_url, timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                ver = resp.json().get("version")
                with self._lock:
                    self._cached_version = ver
                return ver
            except Exception:
                pass
        return None

    def _normalize_job(self, raw_job: dict[str, Any]) -> dict[str, Any]:
        """Normalize raw job dictionary into a consistent internal model."""
        if not isinstance(raw_job, dict):
            raw_job = {}
        return {
            "id": str(raw_job.get("id") or ""),
            "url": str(raw_job.get("url") or ""),
            "filename": str(raw_job.get("filename") or ""),
            "label": str(raw_job.get("label") or raw_job.get("filename") or "Downloading…"),
            "status": str(raw_job.get("status") or "queued").lower(),
            "progress": float(raw_job.get("progress") or 0.0),
            "speed": str(raw_job.get("speed") or ""),
            "eta": str(raw_job.get("eta") or ""),
            "mode": str(raw_job.get("mode") or "video").lower(),
            "queued_at": str(raw_job.get("queued_at") or ""),
            "started_at": str(raw_job.get("started_at") or ""),
            "completed_at": str(raw_job.get("completed_at") or ""),
            "priority": str(raw_job.get("priority") or "normal").lower(),
            "downloaded_bytes": float(raw_job.get("downloaded_bytes") or 0.0),
            "total_bytes": float(raw_job.get("total_bytes") or 0.0),
            "size": str(raw_job.get("size") or ""),
            "quality": str(raw_job.get("quality") or ""),
            "format": str(raw_job.get("format") or ""),
        }

    def _normalize_stats(self, raw_stats: dict[str, Any]) -> dict[str, Any]:
        """Normalize raw stats dictionary into a consistent internal model."""
        if not isinstance(raw_stats, dict):
            raw_stats = {}
        return {
            "downloads_today": int(raw_stats.get("downloads_today") or 0),
            "total_downloads": int(raw_stats.get("total_downloads") or 0),
            "completed_count": int(raw_stats.get("completed_count") or 0),
            "failed_count": int(raw_stats.get("failed_count") or 0),
            "success_rate": float(raw_stats.get("success_rate") or 100.0),
            "failure_rate": float(raw_stats.get("failure_rate") or 0.0),
            "queue_length": int(raw_stats.get("queue_length") or 0),
            "active_jobs": int(raw_stats.get("active_jobs") or 0),
            "backend_uptime": int(raw_stats.get("backend_uptime") or 0),
            "average_speed": str(raw_stats.get("average_speed") or "0 KB/s"),
        }

    def _normalize_settings(self, raw_settings: dict[str, Any]) -> dict[str, Any]:
        """Normalize raw settings dictionary into a consistent internal model.

        NOTE: 'theme' is intentionally excluded — it is a Companion UI preference
        stored exclusively in local settings.json, not a backend responsibility.
        """
        if not isinstance(raw_settings, dict):
            raw_settings = {}
        return {
            "download_folder": str(raw_settings.get("download_folder") or ""),
            "ffmpeg_path": str(raw_settings.get("ffmpeg_path") or ""),
            "backend_url": str(raw_settings.get("backend_url") or ""),
        }

    def get_queue(self) -> list[dict[str, Any]]:
        """Fetch the list of queued and downloading jobs from the backend."""
        resp = self._send_request("GET", "/queue", timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
                if data.get("success"):
                    qdata = data.get("queue")
                    raw_jobs = []
                    if isinstance(qdata, dict):
                        # Flatten get_queue_status() dictionary into a single list of jobs
                        active = qdata.get("active")
                        if active:
                            raw_jobs.append(active)
                        raw_jobs.extend(qdata.get("queued", []))
                        raw_jobs.extend(qdata.get("failed", []))
                    elif isinstance(qdata, list):
                        raw_jobs = qdata
                    
                    return [self._normalize_job(j) for j in raw_jobs]
            except Exception:
                pass
        return []

    def retry_download(self, job_id: str) -> bool:
        """Re-queue a failed download by posting to the download endpoint."""
        resp = self._send_request("POST", "/queue/retry", json={"job_id": job_id}, timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                return resp.json().get("success", False)
            except Exception:
                pass
        return False

    def remove_download(self, job_id: str) -> bool:
        """Remove a download job from the queue."""
        resp = self._send_request("POST", "/queue/remove", json={"job_id": job_id}, timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                return resp.json().get("success", False)
            except Exception:
                pass
        return False

    def cancel_download(self, job_id: str) -> bool:
        """Cancel a running or queued download job."""
        resp = self._send_request("POST", "/queue/cancel", json={"job_id": job_id}, timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                return resp.json().get("success", False)
            except Exception:
                pass
        return False

    def pause_download(self, job_id: str) -> bool:
        """Pause a specific download job."""
        resp = self._send_request("POST", "/queue/pause", json={"job_id": job_id}, timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                return resp.json().get("success", False)
            except Exception:
                pass
        return False

    def resume_download(self, job_id: str) -> bool:
        """Resume a specific download job."""
        resp = self._send_request("POST", "/queue/resume", json={"job_id": job_id}, timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                return resp.json().get("success", False)
            except Exception:
                pass
        return False

    def change_priority(self, job_id: str, priority: str) -> bool:
        """Change the priority of a download job (high/normal/low)."""
        resp = self._send_request("POST", "/queue/priority",
                                  json={"job_id": job_id, "priority": priority}, timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                return resp.json().get("success", False)
            except Exception:
                pass
        return False

    def reorder_job(self, job_id: str, new_index: int) -> bool:
        """Move a job to a new position in the queue."""
        resp = self._send_request("POST", "/queue/reorder",
                                  json={"job_id": job_id, "new_index": new_index}, timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                return resp.json().get("success", False)
            except Exception:
                pass
        return False

    def get_history(self) -> list[dict[str, Any]]:
        """Fetch the history list from the backend."""
        resp = self._send_request("GET", "/history", timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
                if data.get("success"):
                    raw_history = data.get("history", [])
                    return [self._normalize_job(h) for h in raw_history]
            except Exception:
                pass
        return []

    def get_stats(self) -> dict[str, Any]:
        """Fetch backend statistics from the /stats endpoint."""
        resp = self._send_request("GET", "/stats", timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
                if data.get("success"):
                    return self._normalize_stats(data.get("stats", {}))
            except Exception:
                pass
        return self._normalize_stats({})

    def get_settings(self) -> dict[str, Any]:
        """Fetch settings from the backend."""
        resp = self._send_request("GET", "/settings", timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
                if data.get("success"):
                    return self._normalize_settings(data.get("settings", {}))
            except Exception:
                pass
        return self._normalize_settings({})

    def save_settings(self, changes: dict[str, Any]) -> dict[str, Any] | None:
        """Save settings updates to the backend."""
        resp = self._send_request("POST", "/settings", json=changes, timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
                if data.get("success"):
                    return data.get("settings")
            except Exception:
                pass
        return None

    def clear_history_api(self) -> bool:
        """Clear download history in the backend."""
        resp = self._send_request("POST", "/history/clear", timeout=HTTP_TIMEOUT)
        if resp is not None and resp.status_code == 200:
            try:
                return resp.json().get("success", False)
            except Exception:
                pass
        return False

    # ------------------------------------------------------------------
    # Process lifecycle helper methods
    # ------------------------------------------------------------------

    def _launch_backend(self) -> subprocess.Popen:
        """Launch the backend process (handles both source and frozen EXE modes)."""
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        if getattr(sys, "frozen", False):
            # Frozen EXE: sys.executable is MediaForge.exe, not python.exe.
            # Use system python from PATH to launch backend/app.py.
            python_exe = "python"
        else:
            python_exe = sys.executable

        env = os.environ.copy()
        # Propagate portable mode
        is_portable = False
        if os.environ.get("MEDIAFORGE_PORTABLE") == "1":
            is_portable = True
        elif getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            if os.path.exists(os.path.join(exe_dir, "portable_settings.json")):
                is_portable = True
        else:
            is_portable = True

        if is_portable:
            env["MEDIAFORGE_PORTABLE"] = "1"
        else:
            env["MEDIAFORGE_PORTABLE"] = "0"

        return subprocess.Popen(
            [python_exe, "app.py"],
            cwd=BACKEND_DIR,
            creationflags=creation_flags,
            env=env,
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
                self._set_status(BackendStatus.CRASHED, "Unable to communicate with backend.")

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
