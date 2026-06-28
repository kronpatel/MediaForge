from __future__ import annotations
import os
import sys
import time
import hashlib
import subprocess
import threading
import ctypes
from ctypes import wintypes
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logger import AppLogger
    from updater import UpdateManager
    from ui import CompanionWindow

# Windows structures for ShellExecuteExW
if sys.platform == "win32":
    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpOperation", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]


class InstallerManager:
    """
    Manages verification, safe launching, monitoring, and restart verification of the installer.
    """

    def __init__(self, logger: AppLogger, updater: UpdateManager, window: CompanionWindow) -> None:
        self.logger = logger
        self.updater = updater
        self.window = window
        self._lock = threading.Lock()
        self._installing = False

        # In-memory diagnostics event timeline (Component 6)
        self._timeline_lock = threading.Lock()
        self._events: list[str] = []
        for ev in getattr(updater, "_startup_recovery_events", []):
            self._add_event(ev)

    def _add_event(self, name: str) -> None:
        with self._timeline_lock:
            if " - " in name:
                self._events.append(name)
            else:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                self._events.append(f"{timestamp} - {name}")
            self._events = self._events[-100:]

    def get_recent_events(self) -> list[str]:
        """Expose timeline events list copy (Component 6)."""
        with self._timeline_lock:
            return list(self._events)

    def install_update(self) -> None:
        """
        Launches the update sequence on a background monitor thread.
        """
        with self._lock:
            if self._installing:
                self.logger.warning("Installer is already running.")
                return
            if not self.updater._pending_install:
                self.logger.error("No update is pending install.")
                return
            self._installing = True

        threading.Thread(target=self._run_install_loop, name="InstallerMonitorThread", daemon=True).start()

    def _set_state(self, state: str) -> None:
        with self.updater._lock:
            self.updater._installer_state = state
            self.updater._save_cache()
        self.updater._notify_current_state()

    def _set_state_failed(self, error_msg: str) -> None:
        with self.updater._lock:
            self.updater._installer_state = "Failed"
            self.updater._last_install_result = "failed"
            self.updater._last_install_error = error_msg
            self.updater._save_cache()
        self.updater._notify_current_state()

    def _validate_installer(self) -> None:
        installer_path = self.updater._installer_path
        if not installer_path:
            raise ValueError("Installer path is empty in cache.")
        if not os.path.exists(installer_path):
            raise FileNotFoundError(f"Installer file not found at: {installer_path}")

        sz = os.path.getsize(installer_path)
        if sz <= 0:
            raise ValueError("Installer file size is zero.")
        if sz != self.updater._asset_size:
            raise ValueError(f"Installer size mismatch: expected {self.updater._asset_size} bytes, got {sz} bytes.")

        if self.updater._installer_version != self.updater._latest_version:
            raise ValueError(f"Installer version mismatch: cached {self.updater._installer_version} != latest {self.updater._latest_version}.")

        # Compute SHA-256 (integrity check)
        self.logger.info("Computing SHA-256 hash of installer for integrity verification...")
        hasher = hashlib.sha256()
        with open(installer_path, "rb") as fh:
            while chunk := fh.read(65536):
                hasher.update(chunk)
        current_hash = hasher.hexdigest()

        expected_hash = self.updater._installer_sha256
        if expected_hash:
            if current_hash != expected_hash:
                raise ValueError("Installer integrity verification failed: SHA-256 hash mismatch.")
            self.logger.info("Integrity check succeeded: SHA-256 matches.")
        else:
            self.logger.info("Skipped SHA-256 hash check (no cached hash available).")

    def _launch_elevated(self, installer_path: str) -> int:
        if sys.platform != "win32":
            raise NotImplementedError("UAC elevation is only supported on Windows.")

        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        info = SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
        info.fMask = SEE_MASK_NOCLOSEPROCESS
        info.hwnd = None
        info.lpOperation = "runas"
        info.lpFile = installer_path
        info.lpParameters = None
        info.lpDirectory = None
        info.nShow = 1  # SW_SHOWNORMAL

        ret = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info))
        if not ret:
            err = ctypes.GetLastError()
            raise OSError(f"ShellExecuteExW failed with error code {err}")
        return info.hProcess

    def _delete_installer_file_safe(self, path: str) -> None:
        if not os.path.exists(path):
            return
        retries = 3
        delay = 0.5
        for i in range(retries):
            try:
                os.remove(path)
                self.logger.info(f"Successfully deleted installer file: {path}")
                return
            except OSError as exc:
                self.logger.warning(f"Failed to delete installer (attempt {i+1}/{retries}): {exc}")
                if i < retries - 1:
                    time.sleep(delay)

        self.logger.error(f"Installer remains locked after {retries} deletion attempts.")
        with self.updater._lock:
            self.updater._last_install_error = "Another application is using the installer"
            self.updater._save_cache()

    def _restart_companion_verify(self) -> None:
        self.logger.info("Restarting MediaForge Companion...")
        executable = sys.executable
        args = []
        if executable.lower().endswith(("python.exe", "pythonw.exe")):
            main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
            args = [executable, main_py]
        else:
            args = [executable]

        try:
            p = subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            time.sleep(1.0)
            if p.poll() is None:
                self._add_event("Restart Successful")
                self.logger.info("New Companion process started successfully. Exiting current process.")
                os._exit(0)
            else:
                ret = p.poll()
                raise RuntimeError(f"New Companion process exited immediately with code {ret}")
        except Exception as exc:
            self.logger.error(f"Companion restart validation failed: {exc}")
            def _alert():
                from tkinter import messagebox
                messagebox.showwarning(
                    "MediaForge Companion Restart",
                    "The update was completed successfully, but MediaForge Companion failed to restart automatically.\n\n"
                    "Please launch MediaForge Companion manually."
                )
            self.window.after(0, _alert)

    def _run_install_loop(self) -> None:
        try:
            self._add_event("Validation Started")
            self._set_state("Launching")
            try:
                self._validate_installer()
                self._add_event("Validation Passed")
            except Exception as e:
                self.logger.error(f"Installer validation failed: {e}")
                with self.updater._lock:
                    self.updater._pending_install = False
                    self.updater._installer_path = ""
                    self.updater._installer_version = ""
                    self.updater._download_completed_at = 0.0
                    self.updater._installer_sha256 = ""
                    self.updater._save_cache()
                self._set_state_failed(f"Validation failed: {e}")
                self.updater._notify_current_state()
                return

            self.logger.info("Performing graceful shutdown of companion services before launch...")
            try:
                shutdown_event = threading.Event()
                def _do_shutdown():
                    try:
                        self.window.prepare_for_installation()
                    finally:
                        shutdown_event.set()
                self.window.after(0, _do_shutdown)
                if not shutdown_event.wait(timeout=10.0):
                    raise RuntimeError("Graceful shutdown of Companion timed out.")
            except Exception as e:
                self.logger.error(f"Graceful shutdown failed: {e}")
                self._set_state_failed(f"Shutdown failed: {e}")
                self.updater._notify_current_state()
                return

            self.logger.info("Launching installer...")
            self._add_event("Launch Started")
            with self.updater._lock:
                self.updater._installation_in_progress = True
                self.updater._save_cache()
            installer_path = self.updater._installer_path
            launch_result = {"hProcess": None, "proc": None, "error": None}
            launch_done = threading.Event()

            def _launcher():
                try:
                    if sys.platform == "win32":
                        try:
                            proc = subprocess.Popen([installer_path])
                            launch_result["proc"] = proc
                        except OSError as exc:
                            if exc.winerror == 740 or isinstance(exc, PermissionError):
                                self.logger.info("Installer requires UAC elevation. Launching with runas...")
                                self._add_event("UAC Requested")
                                hProcess = self._launch_elevated(installer_path)
                                launch_result["hProcess"] = hProcess
                            else:
                                raise
                    else:
                        proc = subprocess.Popen([installer_path])
                        launch_result["proc"] = proc
                except Exception as exc:
                    launch_result["error"] = exc
                finally:
                    launch_done.set()

            # Launch timeout thread guard (Component 5 / Refinements)
            threading.Thread(target=_launcher, daemon=True).start()
            if not launch_done.wait(timeout=30.0):
                self.logger.error("Installer launch operation timed out (30 seconds).")
                with self.updater._lock:
                    self.updater._installation_in_progress = False
                self._set_state_failed("Installer launch timed out")
                self.updater._notify_current_state()
                return

            if launch_result["error"]:
                err = launch_result["error"]
                self.logger.error(f"Failed to launch installer process: {err}")
                with self.updater._lock:
                    self.updater._installation_in_progress = False
                self._set_state_failed(f"Launch failed: {err}")
                self.updater._notify_current_state()
                return

            self._set_state("Waiting For Exit")
            self._add_event("Installer Running")
            hProcess = launch_result["hProcess"]
            proc = launch_result["proc"]
            exit_code = -1

            if hProcess:
                self.logger.info("Waiting for elevated installer process to exit...")
                ctypes.windll.kernel32.WaitForSingleObject(hProcess, 0xFFFFFFFF)
                val = wintypes.DWORD()
                ctypes.windll.kernel32.GetExitCodeProcess(hProcess, ctypes.byref(val))
                exit_code = val.value
                ctypes.windll.kernel32.CloseHandle(hProcess)
            elif proc:
                self.logger.info(f"Waiting for standard installer PID={proc.pid} to exit...")
                proc.wait()
                exit_code = proc.returncode

            self.logger.info(f"Installer exited with code {exit_code}")

            if exit_code == 0:
                self._add_event("Installer Completed")
                self._set_state("Completed")
                with self.updater._lock:
                    self.updater._pending_install = False
                    self.updater._installer_path = ""
                    self.updater._installer_version = ""
                    self.updater._download_completed_at = 0.0
                    self.updater._installer_sha256 = ""
                    self.updater._last_install_result = "success"
                    self.updater._last_install_error = ""
                    self.updater._last_exit_code = 0
                    self.updater._installation_in_progress = False
                    self.updater._save_cache()

                self._delete_installer_file_safe(installer_path)

                if self.updater._restart_after_install:
                    self._add_event("Restart Requested")
                    self._set_state("Restarting Companion")
                    self._restart_companion_verify()
                else:
                    self.updater._notify_current_state()
            else:
                with self.updater._lock:
                    self.updater._last_install_result = "failed"
                    self.updater._last_exit_code = exit_code
                    if exit_code in (1602, 1223, 3):
                        self.updater._last_install_error = "Installation cancelled by user"
                        self._set_state("Cancelled")
                    else:
                        self.updater._last_install_error = f"Installer exited with error code {exit_code}"
                        self._set_state("Failed")
                    self.updater._installation_in_progress = False
                    self.updater._save_cache()
                self.updater._notify_current_state()

        except Exception as exc:
            self.logger.error(f"Unexpected error in installer loop: {exc}")
            with self.updater._lock:
                self.updater._installation_in_progress = False
            self._set_state_failed(f"Unexpected error: {exc}")
            self.updater._notify_current_state()
        finally:
            with self._lock:
                self._installing = False
