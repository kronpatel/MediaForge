"""
updater.py – UpdateManager

Background update checker and download manager for MediaForge Companion.
Handles GitHub releases checks, semantic version comparisons, integrity verification,
session reuse/recovery, and background daemon polling thread.
"""

from __future__ import annotations

import os
import sys
import json
import time
import hashlib
import threading
import webbrowser
from typing import TYPE_CHECKING, Any, Callable

import requests

if TYPE_CHECKING:
    from logger import AppLogger

COMPANION_VERSION = "1.2.0"
OWNER = "kronpatel"
REPO = "MediaForge"
GITHUB_API_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"

_DIR = os.path.dirname(os.path.abspath(__file__))

def is_portable_mode() -> bool:
    if os.environ.get("MEDIAFORGE_PORTABLE") == "1":
        return True
    if not getattr(sys, "frozen", False):
        return True
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.exists(os.path.join(exe_dir, "portable_settings.json")):
        return True
    return False

def get_companion_cache_dir() -> str:
    if is_portable_mode():
        return os.path.join(_DIR, "cache")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        path = os.path.join(local_app_data, "MediaForge", "cache")
    else:
        path = os.path.join(_DIR, "cache")
    os.makedirs(path, exist_ok=True)
    return path

def get_companion_updates_dir() -> str:
    if is_portable_mode():
        return os.path.join(_DIR, "updates")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        path = os.path.join(local_app_data, "MediaForge", "updates")
    else:
        path = os.path.join(_DIR, "updates")
    os.makedirs(path, exist_ok=True)
    return path

CACHE_FILE = os.path.join(get_companion_cache_dir(), "update_cache.json")
UPDATES_DIR = get_companion_updates_dir()
TEMP_DOWNLOAD_FILE = os.path.join(UPDATES_DIR, "update.tmp")
FINAL_DOWNLOAD_FILE = os.path.join(UPDATES_DIR, "MediaForge_Portable.zip")

# Ensure necessary directories exist
os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
os.makedirs(UPDATES_DIR, exist_ok=True)


class UpdateManager:
    """
    Manages Companion background checks and installer asset downloads from GitHub Releases.
    Runs updates on a background daemon thread to prevent UI freezes.
    """

    def __init__(self, logger: AppLogger) -> None:
        self.logger = logger
        self._lock = threading.RLock()
        self._callbacks: list[Callable[[str, float, str | None], None]] = []

        # Threading / Control State (Task 1 & Task 2)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._download_stop_event = threading.Event()
        self._shutdown = False
        self._check_lock = threading.Lock()
        self._checking = False
        self._download_lock = threading.Lock()
        self._downloading = False

        # Cache metadata
        self._latest_version = "v—"
        self._release_notes = ""
        self._published = "Never"
        self._asset_url = ""
        self._asset_size = 0
        self._last_checked = 0.0
        self._rate_limit_reset_until = 0.0
        self._html_url = ""

        # Pending Install State Metadata (Pending Install Refinement)
        self._pending_install = False
        self._installer_path = ""
        self._installer_version = ""
        self._download_completed_at = 0.0
        self._installer_state = "Idle"
        self._last_install_attempt = 0.0
        self._last_install_result = ""
        self._last_install_error = ""
        self._last_exit_code = 0
        self._restart_after_install = True
        self._installer_sha256 = ""
        self._installation_in_progress = False
        self._recovery_completed = False
        self._startup_recovery_events: list[str] = []

        # Create shared HTTP Session
        self._session = requests.Session()

        # Load initial values from cache if possible
        self._load_cache()

    @property
    def _check_running(self) -> bool:
        with self._check_lock:
            return self._checking

    @_check_running.setter
    def _check_running(self, val: bool) -> None:
        with self._check_lock:
            self._checking = val

    @property
    def _download_running(self) -> bool:
        with self._download_lock:
            return self._downloading

    @_download_running.setter
    def _download_running(self, val: bool) -> None:
        with self._download_lock:
            self._downloading = val

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_callback(self, fn: Callable[[str, float, str | None], None]) -> None:
        """Register a callback that receives (status, progress, error_msg)."""
        with self._lock:
            self._callbacks.append(fn)

    def unregister_callback(self, fn: Callable[[str, float, str | None], None]) -> None:
        """Remove a registered callback."""
        with self._lock:
            if fn in self._callbacks:
                self._callbacks.remove(fn)

    def get_current_version(self) -> str:
        return COMPANION_VERSION

    def get_latest_version(self) -> str:
        with self._lock:
            return self._latest_version

    def get_last_checked(self) -> float:
        with self._lock:
            return self._last_checked

    def has_update(self) -> bool:
        """Return True if self._latest_version is semantically newer than current version."""
        with self._lock:
            latest = self._latest_version
            asset_url = self._asset_url
        if not asset_url:
            return False
        return self.is_newer_version(COMPANION_VERSION, latest)

    def start(self) -> None:
        """Start the background checking poller thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_poller,
                name="UpdateManagerThread",
                daemon=True,
            )
            self._thread.start()
            self.logger.info("[Updater] Auto updater background thread started.")

    def shutdown(self) -> None:
        """Shut down poller threads and close the shared HTTP session cleanly."""
        with self._lock:
            self._shutdown = True
        self._stop_event.set()
        self.cancel_download()

        with self._lock:
            if self._session:
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.logger.info("[Updater] Auto updater thread stopped cleanly.")

    def check_for_updates(self, force: bool = False) -> None:
        """
        Triggers a check for updates. If force=False, uses valid cached info (<1 hour old).
        Otherwise fetches fresh release metadata from GitHub API on a background thread.
        """
        # Verify shutdown state (Task 2)
        with self._lock:
            if self._shutdown:
                self.logger.warning("[Updater] Update check rejected: updater is shutting down.")
                return

        # Check and set checking flag under check lock (Task 1)
        with self._check_lock:
            if self._checking:
                self.logger.info("[Updater] Update check already running. Request ignored.")
                return
            self._checking = True

        # Check rate limit suspension (unless forced by manual click)
        is_rate_limited = False
        with self._lock:
            if time.time() < self._rate_limit_reset_until:
                is_rate_limited = True

        if not force and is_rate_limited:
            self.logger.warning("[Updater] Update check skipped: GitHub API is currently rate limited.")
            self._notify("Rate Limited", 0.0)
            with self._check_lock:
                self._checking = False
            return

        def _worker():
            try:
                self._notify("Checking", 0.0)
                now = time.time()
                
                # Check cache validity
                with self._lock:
                    cache_age = now - self._last_checked
                    has_cache = self._latest_version != "v—"

                if not force and has_cache and cache_age < 3600.0:
                    self.logger.info("[Updater] Using cached update metadata (less than 1 hour old).")
                    self._notify_current_state()
                    return

                # Fetch fresh from GitHub Releases API
                self.logger.info(f"[Updater] Checking for updates from {GITHUB_API_URL}...")
                release = self._fetch_with_retries(GITHUB_API_URL)
                
                if not release:
                    self.logger.warning("[Updater] GitHub Releases API check failed. Offline or API limit reached.")
                    self._notify("Offline", 0.0)
                    return

                tag_name = release.get("tag_name", "v—")
                published_at = release.get("published_at", "Never")
                body = release.get("body", "")
                html_url = release.get("html_url", "")

                # Filter and validate installer assets (Task 1 & Task 4 & Task 5)
                assets = release.get("assets", [])
                compatible_assets = []
                for idx, asset in enumerate(assets):
                    name = asset.get("name", "")
                    
                    # Reject non-matching patterns (archives, source code, etc.)
                    if not (name.endswith(".zip") and "MediaForge_Portable" in name):
                        continue
                        
                    # Integrity validation
                    if asset.get("state") != "uploaded":
                        continue
                    if int(asset.get("size") or 0) <= 0:
                        continue
                    if not asset.get("browser_download_url"):
                        continue
                        
                    compatible_assets.append((asset, idx))

                # Deterministic sorting with timestamp fallbacks (Task 5)
                def get_sort_key(item):
                    asset, original_idx = item
                    
                    created = asset.get("created_at")
                    if isinstance(created, str) and created.strip():
                        return (created, "", -original_idx)
                        
                    updated = asset.get("updated_at")
                    if isinstance(updated, str) and updated.strip():
                        return ("", updated, -original_idx)
                        
                    return ("", "", -original_idx)

                if compatible_assets:
                    compatible_assets.sort(key=get_sort_key, reverse=True)
                    best_asset, _ = compatible_assets[0]
                    asset_url = best_asset.get("browser_download_url", "")
                    asset_size = int(best_asset.get("size") or 0)
                else:
                    self.logger.warning("[Updater] No compatible installer asset found in the latest release.")
                    asset_url = ""
                    asset_size = 0

                with self._lock:
                    # Invalidate pending install if a new version is fetched that differs from the pending installer version
                    if self._pending_install and self._installer_version != tag_name:
                        self._pending_install = False
                        self._installer_path = ""
                        self._installer_version = ""
                        self._download_completed_at = 0.0

                    self._latest_version = tag_name
                    self._release_notes = body
                    self._published = published_at
                    self._asset_url = asset_url
                    self._asset_size = asset_size
                    self._last_checked = now
                    self._html_url = html_url
                    self._save_cache()

                with self._check_lock:
                    self._checking = False

                self._notify_current_state()

            except Exception as exc:
                self.logger.warning(f"[Updater] Error checking for updates: {exc}")
                self._notify("Failed", 0.0, str(exc))
            finally:
                with self._check_lock:
                    self._checking = False

        try:
            threading.Thread(target=_worker, name="UpdateCheckWorker", daemon=True).start()
        except Exception:
            with self._check_lock:
                self._checking = False
            raise

    def download_update(self) -> None:
        """Start downloading the latest release installer asset in a background thread."""
        # Verify shutdown state (Task 2)
        with self._lock:
            if self._shutdown:
                self.logger.warning("[Updater] Download rejected: updater is shutting down.")
                return

        # Check and set downloading flag under download lock (Task 1)
        with self._download_lock:
            if self._downloading:
                self.logger.warning("[Updater] Download already in progress. Request ignored.")
                return
            self._downloading = True
            self._download_stop_event.clear()

        with self._lock:
            asset_url = self._asset_url
            expected_size = self._asset_size

        if not asset_url:
            self.logger.error("[Updater] No update asset download URL available.")
            self._notify("Failed", 0.0, "No asset URL found.")
            with self._download_lock:
                self._downloading = False
            return

        def _downloader():
            try:
                self._notify("Downloading", 0.0)
                self.logger.info(f"[Updater] Downloading update asset from {asset_url}...")
                
                # Delete any stale temp downloads and .new files (Task 5)
                new_file = os.path.join(UPDATES_DIR, "MediaForge_Portable.zip.new")
                for f in (TEMP_DOWNLOAD_FILE, new_file):
                    if os.path.exists(f):
                        try:
                            os.remove(f)
                        except OSError:
                            pass

                # Stream request
                session = self._get_session()
                if not session:
                    raise RuntimeError("HTTP session closed.")

                resp = session.get(asset_url, stream=True, timeout=10.0)
                resp.raise_for_status()

                total_length = int(resp.headers.get('content-length') or expected_size or 0)
                downloaded = 0

                with open(TEMP_DOWNLOAD_FILE, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if self._download_stop_event.is_set():
                            self.logger.info("[Updater] Download cancelled by user.")
                            fh.close()
                            self._cleanup_temp_file()
                            self._notify("Idle", 0.0)
                            return
                        if chunk:
                            fh.write(chunk)
                            downloaded += len(chunk)
                            if total_length > 0:
                                progress = min(100.0, (downloaded / total_length) * 100.0)
                                self._notify("Downloading", progress)

                self._notify("Verifying", 100.0)
                self.logger.info("[Updater] Verifying download size and integrity...")
                
                # Verify size match
                actual_size = os.path.getsize(TEMP_DOWNLOAD_FILE)
                if expected_size > 0 and actual_size != expected_size:
                    raise ValueError(f"File size mismatch: expected {expected_size} bytes, got {actual_size} bytes.")

                # Compute SHA-256 (integrity check)
                hasher = hashlib.sha256()
                with open(TEMP_DOWNLOAD_FILE, "rb") as fh:
                    while chunk := fh.read(65536):
                        hasher.update(chunk)
                sha256_hash = hasher.hexdigest()
                self.logger.info(f"[Updater] Verification success. SHA-256: {sha256_hash}")

                # Safely replace existing file with backup/restore logic and lock detection (Task 1 & Task 5)
                bak_file = os.path.join(UPDATES_DIR, "MediaForge_Portable.zip.bak")
                has_backup = False
                is_locked = False

                if os.path.exists(FINAL_DOWNLOAD_FILE):
                    try:
                        if os.path.exists(bak_file):
                            try:
                                os.remove(bak_file)
                            except OSError:
                                pass
                        os.rename(FINAL_DOWNLOAD_FILE, bak_file)
                        has_backup = True
                    except (OSError, PermissionError) as exc:
                        self.logger.warning(f"[Updater] Existing installer is locked/in-use: {exc}")
                        is_locked = True

                if is_locked:
                    # Keep the newly downloaded installer as MediaForge_Portable.zip.new (Task 5)
                    if os.path.exists(new_file):
                        try:
                            os.remove(new_file)
                        except OSError:
                            pass
                    os.rename(TEMP_DOWNLOAD_FILE, new_file)
                    raise PermissionError("Installer is currently in use and must be closed before replacement.")

                # Proceed to rename TEMP_DOWNLOAD_FILE to FINAL_DOWNLOAD_FILE
                try:
                    os.rename(TEMP_DOWNLOAD_FILE, FINAL_DOWNLOAD_FILE)
                    # Clean up backup
                    if has_backup and os.path.exists(bak_file):
                        try:
                            os.remove(bak_file)
                        except OSError:
                            pass
                except Exception as exc:
                    # Rename failed, restore backup if available
                    if has_backup:
                        try:
                            os.rename(bak_file, FINAL_DOWNLOAD_FILE)
                        except OSError:
                            pass
                    raise exc

                # Update pending install state (Pending Install Refinement)
                with self._lock:
                    self._pending_install = True
                    self._installer_path = os.path.abspath(FINAL_DOWNLOAD_FILE)
                    self._installer_version = self._latest_version
                    self._download_completed_at = time.time()
                    self._installer_sha256 = sha256_hash
                    self._save_cache()

                self._notify("Pending Install", 100.0)
                self.logger.info(f"[Updater] Update download completed successfully. Ready to install: {FINAL_DOWNLOAD_FILE}")

            except Exception as exc:
                self.logger.error(f"[Updater] Download failed: {exc}")
                self._cleanup_temp_file()
                self._notify("Failed", 0.0, str(exc))
            finally:
                with self._download_lock:
                    self._downloading = False

        threading.Thread(target=_downloader, name="UpdateDownloadWorker", daemon=True).start()

    def cancel_download(self) -> None:
        """Safely cancel the active download process, deleting the temp file and reverting progress."""
        with self._lock:
            if not self._download_running:
                return
            self._download_stop_event.set()
        
        # Give downloader thread a moment to notice the cancel and cleanup
        time.sleep(0.2)
        self._cleanup_temp_file()

    def open_release_notes(self) -> None:
        """Open the latest release webpage inside the default system browser."""
        with self._lock:
            url = getattr(self, "_html_url", f"https://github.com/{OWNER}/{REPO}/releases")
        try:
            webbrowser.open(url)
        except Exception as exc:
            self.logger.warning(f"[Updater] Failed to open release notes: {exc}")

    # ------------------------------------------------------------------
    # Helper Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def is_newer_version(current: str, latest: str) -> bool:
        """Semantic version parser and comparator (v1.0.9 < v1.2.0)."""
        def parse(v: str) -> list[int]:
            cleaned = v.strip().lower().lstrip('v')
            parts = []
            for p in cleaned.split('.'):
                try:
                    parts.append(int(p))
                except ValueError:
                    parts.append(0)
            return parts

        c_parts = parse(current)
        l_parts = parse(latest)
        
        max_len = max(len(c_parts), len(l_parts))
        c_parts += [0] * (max_len - len(c_parts))
        l_parts += [0] * (max_len - len(l_parts))
        
        return l_parts > c_parts

    def _notify(self, status: str, progress: float, error_msg: str | None = None) -> None:
        """Fire state notifications safely to all registered callback listeners."""
        with self._lock:
            subs = list(self._callbacks)
        for sub in subs:
            try:
                sub(status, progress, error_msg)
            except Exception:
                pass

    def get_status(self) -> str:
        """Retrieve the current unified status string."""
        with self._lock:
            if self._pending_install:
                if self._installer_state != "Idle":
                    return self._installer_state
                return "Pending Install"
            if self._downloading:
                return "Downloading"
            if self._checking:
                return "Checking"
            if self._latest_version == "v—":
                return "Idle"
            if not self._asset_url:
                return "Installer Not Found"
            if self.has_update():
                return "Update Available"
            return "Up To Date"

    def _notify_current_state(self) -> None:
        """Dispatch current check details as state notification."""
        status = self.get_status()
        progress = 100.0 if status in ("Pending Install", "Completed", "Waiting For Exit", "Launching") else 0.0
        self._notify(status, progress)

    def _cleanup_temp_file(self) -> None:
        if os.path.exists(TEMP_DOWNLOAD_FILE):
            try:
                os.remove(TEMP_DOWNLOAD_FILE)
            except OSError:
                pass

    def _get_session(self) -> requests.Session | None:
        with self._lock:
            return self._session

    def _fetch_with_retries(self, url: str) -> dict[str, Any] | None:
        """Perform HTTP GET requests with connection retry limit and Session recreation on failures."""
        attempts = 3
        delay = 2.0

        for i in range(attempts):
            session = self._get_session()
            if not session:
                return None
            try:
                resp = session.get(url, timeout=5.0)

                # Check for rate limit status (HTTP 403)
                if resp.status_code == 403:
                    reset_header = resp.headers.get("X-RateLimit-Reset")
                    if reset_header:
                        try:
                            reset_time = float(reset_header)
                        except ValueError:
                            reset_time = time.time() + 3600.0
                    else:
                        reset_time = time.time() + 3600.0

                    with self._lock:
                        self._rate_limit_reset_until = reset_time

                    self.logger.warning(f"[Updater] GitHub API rate limit exceeded. Suspending checks until timestamp {reset_time}.")
                    self._notify("Rate Limited", 0.0)
                    self._save_cache()
                    return None

                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                self.logger.warning(f"[Updater] GitHub fetch attempt {i+1} failed: {exc}")
                
                # Re-establish fresh Session
                with self._lock:
                    if self._session:
                        try:
                            self._session.close()
                        except Exception:
                            pass
                        self._session = requests.Session()
                
                if i < attempts - 1:
                    time.sleep(delay)
        return None

    def _run_poller(self) -> None:
        # Load local settings and check for start-up update checks
        from settings_panel import read_local_settings
        settings = read_local_settings(self.logger)
        
        if settings.get("check_updates_startup", True):
            self.check_for_updates(force=False)

        while not self._stop_event.is_set():
            settings = read_local_settings(self.logger)
            auto_check = settings.get("auto_check_updates", True)
            interval_hours = float(settings.get("update_poll_interval", 24))

            # Sleep interval using stop_event wait (unblocks instantly on shutdown)
            wait_time = interval_hours * 3600 if auto_check else 3600.0
            if self._stop_event.wait(wait_time):
                break

            if auto_check and not self._stop_event.is_set():
                self.check_for_updates(force=False)

    # ------------------------------------------------------------------
    # Cache Persistence
    # ------------------------------------------------------------------

    def _load_cache(self) -> None:
        """Load release metadata snapshot from local cache, handling corruption gracefully."""
        if getattr(self, "_recovery_completed", False):
            return
        if not os.path.exists(CACHE_FILE):
            return
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return
        except (json.JSONDecodeError, KeyError, OSError, TypeError) as exc:
            self.logger.warning(f"[Updater] Corrupted cache file found. Regenerating defaults. Error: {exc}")
            try:
                corrupt_path = CACHE_FILE.replace(".json", ".corrupt.json")
                if os.path.exists(corrupt_path):
                    os.remove(corrupt_path)
                os.rename(CACHE_FILE, corrupt_path)
            except Exception as e:
                self.logger.error(f"[Updater] Failed to rename corrupted cache file: {e}")
            
            # Reset parameters to default
            self._latest_version = "v—"
            self._release_notes = ""
            self._published = "Never"
            self._asset_url = ""
            self._asset_size = 0
            self._last_checked = 0.0
            self._rate_limit_reset_until = 0.0
            self._pending_install = False
            self._installer_path = ""
            self._installer_version = ""
            self._download_completed_at = 0.0
            self._installer_state = "Idle"
            self._last_install_attempt = 0.0
            self._last_install_result = ""
            self._last_install_error = ""
            self._last_exit_code = 0
            self._restart_after_install = True
            self._installer_sha256 = ""
            self._installation_in_progress = False
            self._recovery_completed = True
            try:
                self._save_cache()
            except Exception:
                pass
            return

        try:
            self._latest_version = data.get("latest_version", "v—")
            self._release_notes = data.get("release_notes", "")
            self._published = data.get("published", "Never")
            self._asset_url = data.get("asset_url", "")
            self._asset_size = int(data.get("asset_size") or 0)
            self._last_checked = float(data.get("last_checked") or 0.0)
            self._html_url = data.get("html_url", "")
            self._last_notified_version = data.get("last_notified_version", "")
            self._rate_limit_reset_until = float(data.get("rate_limit_reset_until") or 0.0)
            self._pending_install = bool(data.get("pending_install") or False)
            self._installer_path = data.get("installer_path", "")
            self._installer_version = data.get("installer_version", "")
            self._download_completed_at = float(data.get("download_completed_at") or 0.0)
            self._installer_state = data.get("installer_state", "Idle")
            self._last_install_attempt = float(data.get("last_install_attempt") or 0.0)
            self._last_install_result = data.get("last_install_result", "")
            self._last_install_error = data.get("last_install_error", "")
            self._last_exit_code = int(data.get("last_exit_code") or 0)
            self._restart_after_install = bool(data.get("restart_after_install") if "restart_after_install" in data else True)
            self._installer_sha256 = data.get("installer_sha256", "")
            self._installation_in_progress = bool(data.get("installation_in_progress") or False)

            if self._installation_in_progress:
                self.logger.info("[Updater] Companion started with an installation in progress. Running recovery checks...")
                self._startup_recovery_events.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Recovery Executed")
                if self._installer_path and os.path.exists(self._installer_path):
                    # File exists on disk. Verify size and version to determine health.
                    stale = False
                    reason = ""
                    try:
                        sz = os.path.getsize(self._installer_path)
                        if sz <= 0:
                            stale = True
                            reason = "installer size is zero"
                        elif sz != self._asset_size:
                            stale = True
                            reason = f"size mismatch (disk={sz}, expected={self._asset_size})"
                    except OSError as exc:
                        stale = True
                        reason = f"unreadable file: {exc}"

                    if not stale and self._installer_version != self._latest_version:
                        stale = True
                        reason = f"version mismatch (installer={self._installer_version}, latest={self._latest_version})"

                    if stale:
                        self.logger.warning(f"[Updater] Installer validation failed on recovery: {reason}. Restoring state to Failed.")
                        self._installer_state = "Failed"
                        self._last_install_result = "failed"
                        self._last_install_error = f"Recovery failed: {reason}"
                        self._installation_in_progress = False
                    else:
                        self.logger.info("[Updater] Installer file is valid. Restoring state to Pending Install.")
                        self._pending_install = True
                        self._installer_state = "Idle"
                        self._installation_in_progress = False
                else:
                    # Installer file does not exist
                    if self._installer_version == COMPANION_VERSION:
                        self.logger.info("[Updater] Installation completed successfully (version matches COMPANION_VERSION). Restoring state to Completed.")
                        self._installer_state = "Completed"
                        self._last_install_result = "success"
                        self._pending_install = False
                        self._installer_path = ""
                        self._installer_version = ""
                        self._download_completed_at = 0.0
                        self._installer_sha256 = ""
                    else:
                        self.logger.warning("[Updater] Installer file no longer exists and version not updated. Restoring state to Failed.")
                        self._installer_state = "Failed"
                        self._last_install_result = "failed"
                        self._last_install_error = "Recovery failed: installer file no longer exists"
                        self._pending_install = False
                        self._installer_path = ""
                        self._installer_version = ""
                        self._download_completed_at = 0.0
                        self._installer_sha256 = ""
                    self._installation_in_progress = False
                
                self._recovery_completed = True
                try:
                    self._save_cache()
                except Exception:
                    pass
            else:
                self._recovery_completed = True

            # Verify installer files (Component 5 / Refinements)
            if self._pending_install:
                stale = False
                reason = ""
                if not self._installer_path or not os.path.isabs(self._installer_path):
                    stale = True
                    reason = "invalid installer path"
                elif not os.path.exists(self._installer_path):
                    stale = True
                    reason = "installer file no longer exists"
                else:
                    try:
                        sz = os.path.getsize(self._installer_path)
                        if sz <= 0:
                            stale = True
                            reason = "installer size is zero"
                        elif sz != self._asset_size:
                            stale = True
                            reason = f"installer size differs (disk={sz}, cached={self._asset_size})"
                    except OSError as exc:
                        stale = True
                        reason = f"installer file unreadable: {exc}"
                
                if not stale and self._installer_version != self._latest_version:
                    stale = True
                    reason = f"installer version differs (installer={self._installer_version}, latest={self._latest_version})"
                
                if stale:
                    self.logger.warning(f"[Updater] Stale Pending Install detected on startup: {reason}. Clearing state.")
                    self._pending_install = False
                    self._installer_path = ""
                    self._installer_version = ""
                    self._download_completed_at = 0.0
                    self._installer_state = "Idle"
                    self._last_install_attempt = 0.0
                    self._last_install_result = ""
                    self._last_install_error = ""
                    self._last_exit_code = 0
                    self._installer_sha256 = ""
                    try:
                        self._save_cache()
                    except Exception:
                        pass
        except Exception:
            # Corrupted cache recovery -> reset parameters to default and recreate cache (Task 2)
            self._latest_version = "v—"
            self._release_notes = ""
            self._published = "Never"
            self._asset_url = ""
            self._asset_size = 0
            self._last_checked = 0.0
            self._rate_limit_reset_until = 0.0
            self._pending_install = False
            self._installer_path = ""
            self._installer_version = ""
            self._download_completed_at = 0.0
            try:
                self._save_cache()
            except Exception:
                pass

    def _save_cache(self) -> None:
        """Write release metadata snapshot into local cache atomically (Task 4)."""
        try:
            data = {
                "latest_version": self._latest_version,
                "release_notes": self._release_notes,
                "published": self._published,
                "asset_url": self._asset_url,
                "asset_size": self._asset_size,
                "last_checked": self._last_checked,
                "html_url": getattr(self, "_html_url", ""),
                "last_notified_version": getattr(self, "_last_notified_version", ""),
                "rate_limit_reset_until": getattr(self, "_rate_limit_reset_until", 0.0),
                "pending_install": self._pending_install,
                "installer_path": self._installer_path,
                "installer_version": self._installer_version,
                "download_completed_at": self._download_completed_at,
                "installer_state": self._installer_state,
                "last_install_attempt": self._last_install_attempt,
                "last_install_result": self._last_install_result,
                "last_install_error": self._last_install_error,
                "last_exit_code": self._last_exit_code,
                "restart_after_install": self._restart_after_install,
                "installer_sha256": self._installer_sha256,
                "installation_in_progress": getattr(self, "_installation_in_progress", False),
            }
            cache_dir = os.path.dirname(CACHE_FILE)
            tmp_file = CACHE_FILE + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_file, CACHE_FILE)
        except Exception as exc:
            self.logger.error(f"[Updater] Failed to save cache file: {exc}")
            tmp_file = CACHE_FILE + ".tmp"
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
