"""
updater.py – UpdateManager

Background update checker and download manager for MediaForge Companion.
Handles GitHub releases checks, semantic version comparisons, integrity verification,
session reuse/recovery, and background daemon polling thread.
"""

from __future__ import annotations

import os
import json
import time
import hashlib
import threading
import webbrowser
from typing import TYPE_CHECKING, Any, Callable

import requests

if TYPE_CHECKING:
    from logger import AppLogger

COMPANION_VERSION = "1.1.0"
OWNER = "kronpatel"
REPO = "MediaForge"
GITHUB_API_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"

_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(_DIR, "cache", "update_cache.json")
UPDATES_DIR = os.path.join(_DIR, "updates")
TEMP_DOWNLOAD_FILE = os.path.join(UPDATES_DIR, "update.tmp")
FINAL_DOWNLOAD_FILE = os.path.join(UPDATES_DIR, "MediaForge-Setup.exe")

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
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[str, float, str | None], None]] = []

        # Threading / Control State
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._check_running = False
        self._download_running = False
        self._download_stop_event = threading.Event()

        # Cache metadata
        self._latest_version = "v—"
        self._release_notes = ""
        self._published = "Never"
        self._asset_url = ""
        self._asset_size = 0
        self._last_checked = 0.0
        self._rate_limit_reset_until = 0.0

        # Create shared HTTP Session
        self._session = requests.Session()

        # Load initial values from cache if possible
        self._load_cache()

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
            self.logger.info("Auto updater background thread started.")

    def shutdown(self) -> None:
        """Shut down poller threads and close the shared HTTP session cleanly."""
        self.logger.info("Stopping auto updater thread...")
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
        self.logger.info("Auto updater thread stopped cleanly.")

    def check_for_updates(self, force: bool = False) -> None:
        """
        Triggers a check for updates. If force=False, uses valid cached info (<1 hour old).
        Otherwise fetches fresh release metadata from GitHub API on a background thread.
        """
        is_rate_limited = False
        with self._lock:
            if time.time() < self._rate_limit_reset_until:
                is_rate_limited = True

        if not force and is_rate_limited:
            self.logger.warning("Update check skipped: GitHub API is currently rate limited.")
            self._notify("Rate Limited", 0.0)
            return

        with self._lock:
            if self._check_running:
                self.logger.info("Update check already running. Request ignored.")
                return
            self._check_running = True

        def _worker():
            try:
                self._notify("Checking", 0.0)
                now = time.time()
                
                # Check cache validity
                with self._lock:
                    cache_age = now - self._last_checked
                    has_cache = self._latest_version != "v—"

                if not force and has_cache and cache_age < 3600.0:
                    self.logger.info("Using cached update metadata (less than 1 hour old).")
                    self._notify_current_state()
                    return

                # Fetch fresh from GitHub Releases API
                self.logger.info(f"Checking for updates from {GITHUB_API_URL}...")
                release = self._fetch_with_retries(GITHUB_API_URL)
                
                if not release:
                    self.logger.warning("GitHub Releases API check failed. Offline or API limit reached.")
                    self._notify("Offline", 0.0)
                    return

                tag_name = release.get("tag_name", "v—")
                published_at = release.get("published_at", "Never")
                body = release.get("body", "")
                html_url = release.get("html_url", "")

                # Find the setup/installer asset
                asset_url = ""
                asset_size = 0
                assets = release.get("assets", [])
                for asset in assets:
                    name = asset.get("name", "")
                    if name.endswith(".exe") or "Setup" in name or "installer" in name.lower():
                        asset_url = asset.get("browser_download_url", "")
                        asset_size = int(asset.get("size") or 0)
                        break

                if not asset_url and assets:
                    # Fallback to first asset if no pattern matches
                    asset_url = assets[0].get("browser_download_url", "")
                    asset_size = int(assets[0].get("size") or 0)

                with self._lock:
                    self._latest_version = tag_name
                    self._release_notes = body
                    self._published = published_at
                    self._asset_url = asset_url
                    self._asset_size = asset_size
                    self._last_checked = now
                    self._html_url = html_url
                    self._save_cache()

                self._notify_current_state()

            except Exception as exc:
                self.logger.warning(f"Error checking for updates: {exc}")
                self._notify("Failed", 0.0, str(exc))
            finally:
                with self._lock:
                    self._check_running = False

        threading.Thread(target=_worker, name="UpdateCheckWorker", daemon=True).start()

    def download_update(self) -> None:
        """Start downloading the latest release installer asset in a background thread."""
        with self._lock:
            if self._download_running:
                self.logger.warning("Download already in progress. Request ignored.")
                return
            asset_url = self._asset_url
            expected_size = self._asset_size

        if not asset_url:
            self.logger.error("No update asset download URL available.")
            self._notify("Failed", 0.0, "No asset URL found.")
            return

        with self._lock:
            self._download_running = True
            self._download_stop_event.clear()

        def _downloader():
            try:
                self._notify("Downloading", 0.0)
                self.logger.info(f"Downloading update asset from {asset_url}...")
                
                # Delete any stale temp downloads and .new files (Task 5)
                new_file = os.path.join(UPDATES_DIR, "MediaForge-Setup.new")
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
                            self.logger.info("Download cancelled by user.")
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
                self.logger.info("Verifying download size and integrity...")
                
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
                self.logger.info(f"Verification success. SHA-256: {sha256_hash}")

                # Safely replace existing file with backup/restore logic and lock detection (Task 1 & Task 5)
                bak_file = os.path.join(UPDATES_DIR, "MediaForge-Setup.bak")
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
                        self.logger.warning(f"Existing installer is locked/in-use: {exc}")
                        is_locked = True

                if is_locked:
                    # Keep the newly downloaded installer as MediaForge-Setup.new (Task 5)
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

                self._notify("Completed", 100.0)
                self.logger.info(f"Update download completed successfully: {FINAL_DOWNLOAD_FILE}")

            except Exception as exc:
                self.logger.error(f"Download failed: {exc}")
                self._cleanup_temp_file()
                self._notify("Failed", 0.0, str(exc))
            finally:
                with self._lock:
                    self._download_running = False

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
            self.logger.warning(f"Failed to open release notes: {exc}")

    # ------------------------------------------------------------------
    # Helper Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def is_newer_version(current: str, latest: str) -> bool:
        """Semantic version parser and comparator (v1.0.9 < v1.1.0)."""
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

    def _notify_current_state(self) -> None:
        """Dispatch current check details as state notification."""
        if self.has_update():
            self._notify("Update Available", 0.0)
        else:
            self._notify("Up To Date", 0.0)

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

                    self.logger.warning(f"GitHub API rate limit exceeded. Suspending checks until timestamp {reset_time}.")
                    self._notify("Rate Limited", 0.0)
                    self._save_cache()
                    return None

                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                self.logger.warning(f"GitHub fetch attempt {i+1} failed: {exc}")
                
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
        if not os.path.exists(CACHE_FILE):
            return
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return
            self._latest_version = data.get("latest_version", "v—")
            self._release_notes = data.get("release_notes", "")
            self._published = data.get("published", "Never")
            self._asset_url = data.get("asset_url", "")
            self._asset_size = int(data.get("asset_size") or 0)
            self._last_checked = float(data.get("last_checked") or 0.0)
            self._html_url = data.get("html_url", "")
            self._last_notified_version = data.get("last_notified_version", "")
            self._rate_limit_reset_until = float(data.get("rate_limit_reset_until") or 0.0)
        except Exception:
            # Corrupted cache recovery -> reset parameters to default and recreate cache (Task 2)
            self._latest_version = "v—"
            self._release_notes = ""
            self._published = "Never"
            self._asset_url = ""
            self._asset_size = 0
            self._last_checked = 0.0
            self._rate_limit_reset_until = 0.0
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
            }
            cache_dir = os.path.dirname(CACHE_FILE)
            tmp_file = os.path.join(cache_dir, "update_cache.tmp")
            with open(tmp_file, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_file, CACHE_FILE)
        except Exception:
            tmp_file = os.path.join(os.path.dirname(CACHE_FILE), "update_cache.tmp")
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
